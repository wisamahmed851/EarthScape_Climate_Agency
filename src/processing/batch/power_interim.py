"""NASA POWER RAW -> INTERIM for one city (all years 2001..2025).

RAW HDFS -> verify manifest + sha256 -> transform -> validate -> local staging -> HDFS _tmp -> verify -> mv -> manifest.
Contract: docs/data-architecture.md ("POWER RAW to INTERIM contract"). RAW is only read.

Run: python src/processing/batch/power_interim.py --city karachi
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src" / "ingestion"))
import nasa_power as raw  # noqa: E402

SOURCE = "power_hourly_interim"
VERSION = "power_interim_v1"
MANIFEST = f"{raw.HDFS_ROOT}/_manifest/{SOURCE}.jsonl"
YEARS = range(2001, 2026)
FILL = -999.0
RAW_COLUMNS = ["YEAR", "MO", "DY", "HR"] + raw.APPROVED_VARIABLES
VALUE_FIELDS = ["temperature_2m_c", "relative_humidity_2m_pct", "precipitation_mm_h",
                "wind_speed_2m_m_s", "surface_pressure_kpa", "solar_irradiance_mj_hr"]
FIELDS = ["timestamp_utc", "city_id"] + VALUE_FIELDS + ["missing_vars"]
HEADER = ",".join(FIELDS)
MERRA = "reanalysis / model-based gridded (MERRA-2)"
CLASS = {"T2M": MERRA, "RH2M": MERRA, "WS2M": MERRA, "PS": MERRA,
         "PRECTOTCORR": "reanalysis, bias-corrected precipitation / model-based gridded (MERRA-2)",
         "ALLSKY_SFC_SW_DWN": "satellite-derived radiation / gridded (CERES SYN1deg)"}
NOTES = {
    "PRECTOTCORR": "POWER parameter catalogue lists the hourly unit as mm/day; the RAW file headers say mm/hour and are used",
    "ALLSKY_SFC_SW_DWN": "GHI (irradiance on a horizontal plane); POWER reports the hourly unit as MJ/hr, kept as reported, not relabelled or converted",
}


def parse_raw(text):
    """Return (header info, data rows as string lists) of one POWER RAW CSV."""
    lines = text.splitlines()
    end = next(i for i, l in enumerate(lines) if l.strip() == "-END HEADER-")
    header = "\n".join(lines[:end])
    if lines[end + 1].split(",") != RAW_COLUMNS:
        raise raw.IngestError(f"unexpected columns: {lines[end + 1]}")
    loc = re.search(r"Latitude\s+(-?[\d.]+)\s+Longitude\s+(-?[\d.]+)", header)
    elev = re.search(r"Elevation from MERRA-2: (.*?) = ([\d.]+) meters", header)
    params = {}
    for v in raw.APPROVED_VARIABLES:
        m = re.search(rf"^{v}\s+(.*?)\s*\(([^()]*)\)\s*$", header, re.M)
        params[v] = {"header_name": m[1], "unit": m[2]}
    info = {"latitude": float(loc[1]), "longitude": float(loc[2]),
            "grid": elev[1], "elevation_m": float(elev[2]), "params": params}
    return info, [l.split(",") for l in lines[end + 2:]]


def convert_row(f, city):
    """One RAW row (strings) -> INTERIM row (strings). Only -999 changes: it becomes empty and is named."""
    y, mo, d, h = (int(x) for x in f[:4])
    values, missing = [], []
    for name, v in zip(VALUE_FIELDS, f[4:]):
        if float(v) == FILL:
            values.append("")
            missing.append(name)
        else:
            values.append(v)
    return [f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:00:00Z", city] + values + [";".join(missing)]


def validate_output(text, raw_rows, city):
    """Check the finished CSV against the RAW rows. Returns (rows, empty climate cells, first, last)."""
    lines = text.split("\n")
    if lines.pop() != "" or lines[0] != HEADER:
        raise raw.IngestError("output must be LF-terminated with the approved header")
    rows = [l.split(",") for l in lines[1:]]
    if len(rows) != len(raw_rows):
        raise raw.IngestError(f"row loss: {len(raw_rows)} input rows, {len(rows)} output rows")
    when, empty = datetime(YEARS[0], 1, 1), 0
    for out, src in zip(rows, raw_rows):
        if len(out) != len(FIELDS) or out[1] != city:
            raise raw.IngestError(f"bad row: {out}")
        if out[0] != f"{when:%Y-%m-%dT%H:00:00Z}":
            raise raw.IngestError(f"gap, duplicate or disorder near {when}: {out[0]}")
        gone = [n for n, o, s in zip(VALUE_FIELDS, out[2:8], src[4:]) if (o == "") != (float(s) == FILL)
                or (o != "" and o != s)]
        if gone or out[8] != ";".join(n for n, o in zip(VALUE_FIELDS, out[2:8]) if o == ""):
            raise raw.IngestError(f"value changed or missing flag wrong at {out[0]}: {gone}")
        empty += out[2:8].count("")
        when += timedelta(hours=1)
    return len(rows), empty, rows[0][0], rows[-1][0]


def is_current(prior, inputs):
    """True when an ok interim record was made by this version from exactly these RAW inputs."""
    return bool(prior) and prior["status"] == "ok" and prior["transform_version"] == VERSION \
        and prior["inputs"] == inputs


def read_inputs(city, lat, lon, manifest_text):
    """Verify every RAW object (manifest ok, path, size, sha256, header, rows). Returns (inputs, raw_rows, header info)."""
    inputs, rows, first = [], [], None
    for year in YEARS:
        start, end = date(year, 1, 1), date(year, 12, 31)
        rec = raw.last_record(manifest_text, f"{raw.SOURCE}/{city}/{start:%Y%m%d}_{end:%Y%m%d}")
        path = raw.raw_path(city, start, end)
        if not rec or rec["status"] != "ok" or rec["hdfs_path"] != path:
            raise raw.IngestError(f"RAW {city} {year} is not ok in the RAW manifest")
        body = raw.hdfs_ok("-cat", path)
        if len(body) != rec["size_bytes"] or hashlib.sha256(body).hexdigest() != rec["sha256"]:
            raise raw.IngestError(f"RAW {path} does not match its manifest size/sha256")
        info, year_rows = parse_raw(body.decode("utf-8"))
        if abs(info["latitude"] - lat) > 1e-4 or abs(info["longitude"] - lon) > 1e-4:
            raise raw.IngestError(f"{path}: header location does not match config")
        expected = ((end - start).days + 1) * 24
        if len(year_rows) != expected or len(year_rows) != rec["record_count"]:
            raise raw.IngestError(f"{path}: {len(year_rows)} rows, expected {expected}")
        if first is None:
            first = info
        elif {k: info[k] for k in ("params", "grid", "elevation_m")} != {k: first[k] for k in ("params", "grid", "elevation_m")}:
            raise raw.IngestError(f"{path}: header metadata differs between years")
        inputs.append({"hdfs_path": path, "sha256": rec["sha256"], "record_count": len(year_rows)})
        rows += year_rows
    return inputs, rows, first


def append_manifest(record):
    if not raw.hdfs_exists(MANIFEST):
        raw.hdfs_ok("-mkdir", "-p", MANIFEST.rsplit("/", 1)[0])
        raw.hdfs_ok("-touchz", MANIFEST)
    r = raw.hdfs("-appendToFile", "-", MANIFEST, input=(json.dumps(record) + "\n").encode("utf-8"))
    if r.returncode != 0:
        raise raw.IngestError(f"manifest append failed: {r.stderr.decode(errors='replace')[:300]}")


def transform(city, staging_dir):
    cfg = json.loads((ROOT / "config" / "cities.json").read_text(encoding="utf-8"))[city]
    final = f"{raw.HDFS_ROOT}/interim/power_hourly/city={city}/power_hourly_{city}.csv"
    inputs, raw_rows, info = read_inputs(city, cfg["latitude"], cfg["longitude"], raw.read_manifest())

    prior = raw.last_record(raw.hdfs_ok("-cat", MANIFEST).decode("utf-8") if raw.hdfs_exists(MANIFEST) else "",
                            f"{SOURCE}/{city}")
    if is_current(prior, inputs) and raw.hdfs_exists(final) and raw.hdfs_size(final) == prior["size_bytes"]:
        return {"result": "skipped", "reason": "already transformed", "hdfs_path": final, "sha256": prior["sha256"]}

    record = {
        "object_key": f"{SOURCE}/{city}", "provider": "NASA POWER", "dataset": "NASA POWER hourly point (AG community)",
        "temporal_resolution": "hourly", "time_standard": "UTC, timestamp = start of the hour",
        "classification": "gridded reanalysis plus satellite radiation; not station observations",
        "variables": {f: {"raw_parameter": v, "provider_unit": info["params"][v]["unit"],
                          "provider_header_name": info["params"][v]["header_name"],
                          "lineage": CLASS[v], **({"note": NOTES[v]} if v in NOTES else {})}
                      for f, v in zip(VALUE_FIELDS, raw.APPROVED_VARIABLES)},
        "city": city, "latitude": cfg["latitude"], "longitude": cfg["longitude"],
        "grid": {"basis": info["grid"], "elevation_m": info["elevation_m"]},
        "inputs": inputs, "transform_version": VERSION, "transformed_at": None,
        "status": "started", "error": None, "hdfs_path": final, "size_bytes": None, "sha256": None,
        "record_count": None, "fill_value_cells": None,
    }
    record["transformed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    append_manifest(record)
    staged = Path(staging_dir) / f"{Path(final).name}.part"
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = "\n".join([HEADER] + [",".join(convert_row(f, city)) for f in raw_rows]) + "\n"
        rows, empty, first, last = validate_output(text, raw_rows, city)
        staged.write_bytes(text.encode("utf-8"))
        record["record_count"], record["fill_value_cells"] = rows, empty
        record["size_bytes"], record["sha256"] = staged.stat().st_size, raw.sha256_of(staged)

        if raw.hdfs_exists(final):
            # Never overwrite: only adopt an identical object left by an interrupted run.
            if raw.hdfs_sha256(final) != record["sha256"]:
                raise raw.IngestError(f"{final} exists with different content; refusing to overwrite")
            outcome = "adopted existing identical object"
        else:
            tmp = f"{raw.HDFS_ROOT}/_tmp/{Path(final).name}.{record['sha256'][:12]}"
            raw.hdfs_ok("-mkdir", "-p", f"{raw.HDFS_ROOT}/_tmp", final.rsplit("/", 1)[0])
            with open(staged, "rb") as f:
                raw.hdfs_ok("-put", "-f", "-", tmp, stdin=f)
            body = raw.hdfs_ok("-cat", tmp)
            back = validate_output(body.decode("utf-8"), raw_rows, city)
            if len(body) != record["size_bytes"] or hashlib.sha256(body).hexdigest() != record["sha256"] \
                    or back != (rows, empty, first, last):
                raise raw.IngestError("HDFS copy does not match the staged file")
            raw.hdfs_ok("-mv", tmp, final)
            outcome = "transformed"
        record["status"] = "ok"
        append_manifest(record)
        staged.unlink()
        return {"result": outcome, "hdfs_path": final, "rows": rows, "size_bytes": record["size_bytes"],
                "sha256": record["sha256"], "fill_value_cells": empty, "first": first, "last": last}
    except Exception as e:
        record["status"], record["error"] = "failed", str(e)[:500]
        append_manifest(record)
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--city", required=True, help="city id from config/cities.json")
    p.add_argument("--staging-dir", default=str(ROOT / "data" / "staging" / SOURCE))
    a = p.parse_args(argv)
    try:
        print(json.dumps(transform(a.city, a.staging_dir), indent=2))
    except raw.IngestError as e:
        sys.exit(f"INTERIM FAILED: {e}")


if __name__ == "__main__":
    main()
