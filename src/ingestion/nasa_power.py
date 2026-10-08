"""NASA POWER hourly point ingestion"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://power.larc.nasa.gov/api/temporal/hourly/point"
SOURCE = "power_hourly_gridded"
HDFS_ROOT = "/earthscape"
MANIFEST = f"{HDFS_ROOT}/_manifest/{SOURCE}.jsonl"
APPROVED_VARIABLES = ["T2M", "RH2M", "PRECTOTCORR", "WS2M", "PS", "ALLSKY_SFC_SW_DWN"]
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5


class IngestError(Exception):
    pass


def build_url(lat, lon, start, end, variables):
    query = urllib.parse.urlencode({
        "parameters": ",".join(variables), "community": "AG",
        "longitude": lon, "latitude": lat,
        "start": f"{start:%Y%m%d}", "end": f"{end:%Y%m%d}",
        "format": "CSV", "time-standard": "UTC",
    })
    return f"{ENDPOINT}?{query}"


def raw_path(city, start, end):
    if start.year != end.year:
        raise IngestError("one file per city-year: start and end must be in the same year")
    full_year = start == date(start.year, 1, 1) and end == date(start.year, 12, 31)
    tag = str(start.year) if full_year else f"{start:%Y%m%d}_{end:%Y%m%d}"
    return f"{HDFS_ROOT}/raw/{SOURCE}/city={city}/year={start.year}/power_hourly_{city}_{tag}.csv"


def download(url):
    """Return (body, provider version, attempts); bounded retries on timeouts, 429 and 5xx"""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read(), r.headers.get("x-app-version"), attempt
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS:
                raise IngestError(f"HTTP {e.code}: {e.read()[:300]!r}")
            wait = int(e.headers.get("Retry-After") or 2 ** attempt)
            problem = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            wait, problem = 2 ** attempt, repr(e)
        if attempt == MAX_ATTEMPTS:
            raise IngestError(f"download failed after {MAX_ATTEMPTS} attempts: {problem}")
        time.sleep(wait)


def validate(text, lat, lon, start, end, variables):
    """Check the response is a complete NASA POWER hourly CSV for the request. Returns (rows, -999 cells)"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "-BEGIN HEADER-":
        raise IngestError("not a POWER CSV (no header block); first bytes: " + text[:120])
    end_idx = next((i for i, l in enumerate(lines) if l.strip() == "-END HEADER-"), None)
    if end_idx is None:
        raise IngestError("header block not closed")
    header = "\n".join(lines[:end_idx])

    m = re.search(r"Latitude\s+(-?[\d.]+)\s+Longitude\s+(-?[\d.]+)", header)
    if not m or abs(float(m[1]) - lat) > 1e-4 or abs(float(m[2]) - lon) > 1e-4:
        raise IngestError(f"header location {m and m.groups()} does not match requested {lat}, {lon}")
    m = re.search(r"(\d\d)/(\d\d)/(\d{4}) through (\d\d)/(\d\d)/(\d{4}) in UTC", header)
    got = m and (date(int(m[3]), int(m[1]), int(m[2])), date(int(m[6]), int(m[4]), int(m[5])))
    if got != (start, end):
        raise IngestError(f"header period {got} does not match requested {start}..{end} in UTC")
    for v in variables:
        if not re.search(rf"^{v}\s", header, re.M):
            raise IngestError(f"parameter {v} missing from header")

    expected_cols = ["YEAR", "MO", "DY", "HR"] + variables
    if lines[end_idx + 1].split(",") != expected_cols:
        raise IngestError(f"unexpected columns: {lines[end_idx + 1]}")

    rows = lines[end_idx + 2:]
    expected = ((end - start).days + 1) * 24
    if len(rows) != expected:
        raise IngestError(f"{len(rows)} data rows, expected {expected}")
    when, missing = datetime(start.year, start.month, start.day), 0
    for row in rows:
        f = row.split(",")
        if len(f) != len(expected_cols):
            raise IngestError(f"malformed row: {row!r}")
        try:
            stamp = tuple(int(x) for x in f[:4])
            values = [float(x) for x in f[4:]]
        except ValueError:
            raise IngestError(f"non-numeric field in row: {row!r}")
        if stamp != (when.year, when.month, when.day, when.hour):
            raise IngestError(f"gap or duplicate near {when}: {row!r}")
        missing += values.count(-999.0)
        when += timedelta(hours=1)
    return len(rows), missing


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hdfs(*args, stdin=None, input=None):
    cmd = ["wsl", "-d", "Ubuntu-24.04", "-e", "/opt/hadoop/bin/hdfs", "dfs", *args]
    return subprocess.run(cmd, stdin=stdin, input=input, capture_output=True, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def hdfs_ok(*args, stdin=None):
    r = hdfs(*args, stdin=stdin)
    if r.returncode != 0:
        raise IngestError(f"hdfs dfs {args[0]} failed: {r.stderr.decode(errors='replace')[:300]}")
    return r.stdout


def hdfs_exists(path):
    return hdfs("-test", "-e", path).returncode == 0


def hdfs_size(path):
    return int(hdfs_ok("-stat", "%b", path))


def hdfs_sha256(path):
    h = hashlib.sha256()
    proc = subprocess.Popen(["wsl", "-d", "Ubuntu-24.04", "-e", "/opt/hadoop/bin/hdfs", "dfs", "-cat", path],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
        h.update(chunk)
    if proc.wait() != 0:
        raise IngestError(f"hdfs cat failed: {proc.stderr.read().decode(errors='replace')[:300]}")
    return h.hexdigest()


def last_record(manifest_text, key):
    """Last manifest line for object_key wins"""
    found = None
    for line in manifest_text.splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec["object_key"] == key:
                found = rec
    return found


def read_manifest():
    return hdfs_ok("-cat", MANIFEST).decode("utf-8") if hdfs_exists(MANIFEST) else ""


def append_manifest(record):
    if not hdfs_exists(MANIFEST):
        hdfs_ok("-mkdir", "-p", MANIFEST.rsplit("/", 1)[0])
        hdfs_ok("-touchz", MANIFEST)
    r = hdfs("-appendToFile", "-", MANIFEST, input=(json.dumps(record) + "\n").encode("utf-8"))
    if r.returncode != 0:
        raise IngestError(f"manifest append failed: {r.stderr.decode(errors='replace')[:300]}")


def ingest(city, lat, lon, start, end, variables, staging_dir):
    key = f"{SOURCE}/{city}/{start:%Y%m%d}_{end:%Y%m%d}"
    final = raw_path(city, start, end)
    url = build_url(lat, lon, start, end, variables)
    record = {
        "object_key": key, "source": SOURCE, "dataset": "NASA POWER hourly point (AG community)",
        "provider_version": None, "request": url,
        "scope": {"city": city, "latitude": lat, "longitude": lon, "variables": variables},
        "time_start": f"{start:%Y-%m-%d}T00:00:00Z", "time_end": f"{end:%Y-%m-%d}T23:00:00Z",
        "retrieved_at": None, "format": "csv", "hdfs_path": final,
        "size_bytes": None, "sha256": None, "record_count": None,
        "status": "started", "attempt": 0, "error": None,
    }

    prior = last_record(read_manifest(), key)
    if prior and prior["status"] == "ok" and hdfs_exists(final) and hdfs_size(final) == prior["size_bytes"]:
        return {"result": "skipped", "reason": "already ingested", "hdfs_path": final, "sha256": prior["sha256"]}

    append_manifest(record)
    staged = Path(staging_dir) / f"{Path(final).name}.part"
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        body, version, record["attempt"] = download(url)
        record["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record["provider_version"] = version
        staged.write_bytes(body)
        rows, missing = validate(body.decode("utf-8"), lat, lon, start, end, variables)
        record["record_count"], record["fill_value_cells"] = rows, missing
        record["size_bytes"], record["sha256"] = staged.stat().st_size, sha256_of(staged)

        if hdfs_exists(final):
            if hdfs_sha256(final) != record["sha256"]:
                raise IngestError(f"{final} exists with different content; refusing to overwrite")
            outcome = "adopted existing identical object"
        else:
            tmp = f"{HDFS_ROOT}/_tmp/{Path(final).name}.{record['sha256'][:12]}"
            hdfs_ok("-mkdir", "-p", f"{HDFS_ROOT}/_tmp", final.rsplit("/", 1)[0])
            with open(staged, "rb") as f:
                hdfs_ok("-put", "-f", "-", tmp, stdin=f)
            if hdfs_size(tmp) != record["size_bytes"] or hdfs_sha256(tmp) != record["sha256"]:
                raise IngestError("HDFS copy does not match the staged file")
            hdfs_ok("-mv", tmp, final)
            outcome = "ingested"
        record["status"] = "ok"
        append_manifest(record)
        staged.unlink()
        return {"result": outcome, "hdfs_path": final, "rows": rows, "size_bytes": record["size_bytes"],
                "sha256": record["sha256"], "fill_value_cells": missing, "attempts": record["attempt"]}
    except Exception as e:
        record["status"], record["error"] = "failed", str(e)[:500]
        append_manifest(record)
        if staged.exists():
            failed_dir = Path(staging_dir) / "_failed"
            failed_dir.mkdir(exist_ok=True)
            staged.replace(failed_dir / staged.name)
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--city", required=True, help="city id from config/cities.json")
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument("--variables", default=",".join(APPROVED_VARIABLES))
    p.add_argument("--staging-dir", default=str(ROOT / "data" / "staging" / SOURCE))
    a = p.parse_args(argv)
    cities = json.loads((ROOT / "config" / "cities.json").read_text(encoding="utf-8"))
    if a.city not in cities:
        sys.exit(f"unknown city {a.city!r}; choose from {sorted(cities)}")
    c = cities[a.city]
    try:
        result = ingest(a.city, c["latitude"], c["longitude"], a.start, a.end, a.variables.split(","), a.staging_dir)
    except IngestError as e:
        sys.exit(f"INGEST FAILED: {e}")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
