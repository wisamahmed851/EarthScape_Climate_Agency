"""Run a POWER MapReduce job (Hadoop Streaming on YARN), verify it independently, publish it, write provenance.

  daily   : INTERIM hourly CSVs  -> /earthscape/processed/power_daily/    (45,655 rows)
  monthly : processed daily CSV  -> /earthscape/processed/power_monthly/  (1,500 rows)
  yearly  : processed daily CSV  -> /earthscape/processed/power_yearly/   (125 rows: annual means and extreme-event counts)

Inputs are only read. Output goes to /earthscape/_tmp/<job>_<time>, is verified, then renamed into place; an
existing output is never overwritten (an ok manifest record for the same inputs and scripts means skip).
Run: python src/processing/mapreduce/run_power_job.py daily   (needs HDFS + YARN, see docs/environment.md)
"""
import argparse
import calendar
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src" / "ingestion"))
sys.path.insert(0, str(HERE))
import nasa_power as raw  # noqa: E402
import power_daily_reducer as daily_r  # noqa: E402
import power_monthly_reducer as monthly_r  # noqa: E402
import power_yearly_reducer as yearly_r  # noqa: E402

STREAMING_JAR = "/opt/hadoop/share/hadoop/tools/lib/hadoop-streaming-3.4.3.jar"
H = raw.HDFS_ROOT
CITIES = list(json.loads((ROOT / "config" / "cities.json").read_text(encoding="utf-8")))
DAILY_PATH = f"{H}/processed/power_daily/part-00000"
JOBS = {
    "daily": {"version": "power_daily_v1", "out": f"{H}/processed/power_daily", "rows": 45655, "header": daily_r.HEADER,
              "scripts": ["power_daily_mapper.py", "power_daily_reducer.py"], "manifest": f"{H}/_manifest/power_daily.jsonl"},
    "monthly": {"version": "power_monthly_v1", "out": f"{H}/processed/power_monthly", "rows": 1500,
                "header": monthly_r.HEADER, "scripts": ["power_monthly_mapper.py", "power_monthly_reducer.py"],
                "manifest": f"{H}/_manifest/power_monthly.jsonl"},
    "yearly": {"version": "power_yearly_v1", "out": f"{H}/processed/power_yearly", "rows": 125, "header": yearly_r.HEADER,
               "scripts": ["power_yearly_mapper.py", "power_yearly_reducer.py"], "manifest": f"{H}/_manifest/power_yearly.jsonl"},
}
DAYS = (date(2025, 12, 31) - date(2001, 1, 1)).days + 1


def wsl_path(p):
    p = Path(p).resolve()
    return f"/mnt/{p.drive[0].lower()}{p.as_posix()[2:]}"


def bash(script):
    r = subprocess.run(["wsl", "-d", "Ubuntu-24.04", "-e", "bash", "-s"], input=script.encode(), capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")


def hdfs_text(path):
    return raw.hdfs_ok("-cat", path).decode("utf-8")


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def append(manifest, record):
    if not raw.hdfs_exists(manifest):
        raw.hdfs_ok("-mkdir", "-p", manifest.rsplit("/", 1)[0])
        raw.hdfs_ok("-touchz", manifest)
    r = raw.hdfs("-appendToFile", "-", manifest, input=(json.dumps(record) + "\n").encode("utf-8"))
    if r.returncode != 0:
        raise raw.IngestError(f"manifest append failed: {r.stderr.decode(errors='replace')[:300]}")


def read_interim():
    """Verify the five INTERIM files against the INTERIM manifest. Returns (inputs, {city: data lines})."""
    manifest = hdfs_text(f"{H}/_manifest/power_hourly_interim.jsonl")
    inputs, texts = [], {}
    for city in CITIES:
        rec = raw.last_record(manifest, f"power_hourly_interim/{city}")
        path = f"{H}/interim/power_hourly/city={city}/power_hourly_{city}.csv"
        text = hdfs_text(path)
        if not rec or rec["status"] != "ok" or rec["hdfs_path"] != path or sha_text(text) != rec["sha256"] \
                or len(text.encode("utf-8")) != rec["size_bytes"]:
            raise raw.IngestError(f"INTERIM {city} is not ok or does not match its manifest")
        inputs.append({"hdfs_path": path, "sha256": rec["sha256"], "size_bytes": rec["size_bytes"],
                       "record_count": rec["record_count"]})
        texts[city] = text.split("\n")[1:-1]
    return inputs, texts


def read_daily_input():
    rec = raw.last_record(hdfs_text(JOBS["daily"]["manifest"]), "power_daily/all")
    if not rec or rec["status"] != "ok" or sha_text(hdfs_text(DAILY_PATH)) != rec["output"]["sha256"]:
        raise raw.IngestError("processed daily output is not ok or does not match its manifest")
    o = rec["output"]
    return [{"hdfs_path": DAILY_PATH, "sha256": o["sha256"], "size_bytes": o["size_bytes"], "record_count": o["record_count"]}]


def parse_log(log):
    """Job id, YARN application id and all counters from the Hadoop client log."""
    job = re.search(r"Running job: (job_\d+_\d+)", log)
    if not job:
        raise raw.IngestError("no job id in the Hadoop log")
    counters, group, on = {}, None, False
    for line in log.splitlines():
        if re.search(r"Counters: \d+", line):
            on = True
        elif on and line.startswith("\t\t"):
            name, _, val = line.strip().rpartition("=")
            counters[group][name] = int(val)
        elif on and line.startswith("\t"):
            group = line.strip()
            counters[group] = {}
        elif on:
            on = False
    return job[1], "application_" + job[1][4:], counters


def run_yarn(job, name, input_arg, out_dir):
    # The repo path contains spaces, which Hadoop's -files URI parsing rejects, so ship copies from a clean dir.
    mapper, reducer = JOBS[job]["scripts"]
    return bash(f"""set -e
source '{wsl_path(ROOT)}/config/earthscape-env.sh'
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
cp '{wsl_path(HERE)}/{mapper}' '{wsl_path(HERE)}/{reducer}' "$stage"
cd "$stage"
hadoop jar {STREAMING_JAR} -D mapreduce.job.name='{name}' -D mapreduce.job.reduces=1 \\
  -D stream.reduce.output.field.separator=, -D stream.num.reduce.output.key.fields=1 \\
  -D mapreduce.output.textoutputformat.separator=, \\
  -files {mapper},{reducer} -mapper 'python3 {mapper}' -reducer 'python3 {reducer}' \\
  -input '{input_arg}' -output '{out_dir}' 2>&1
""")


def yarn_state(app):
    _, out = bash(f"source '{wsl_path(ROOT)}/config/earthscape-env.sh'\nyarn application -status {app} 2>&1")
    return re.search(r"Final-State : (\w+)", out)[1], re.search(r"Application-Name : (.*)", out)[1].strip()


def r4(x):
    """Exact half-up (away from zero) rounding of a Fraction to 4 decimals, as text; independent of the reducer's Decimal code."""
    q = (abs(x) * 10000 + Fraction(1, 2)).__floor__()
    return ("-" if x < 0 and q else "") + f"{q // 10000}.{q % 10000:04d}"


def group_hourly(texts, key_len):
    """{(city, timestamp[:key_len]): [rows]} from INTERIM lines."""
    groups = {}
    for city, lines in texts.items():
        for line in lines:
            f = line.split(",")
            groups.setdefault((city, f[0][:key_len]), []).append(f)
    return groups


def hourly_columns(rows):
    cols = [[Fraction(r[2 + i]) for r in rows if r[2 + i] != ""] for i in range(6)]
    return cols, [len(rows) - len(c) for c in cols]


def verify_daily(output, texts):
    """Compare every daily row with a recomputation from INTERIM. Returns a summary or raises."""
    lines = output.split("\n")
    if lines.pop() != "" or lines[0] != daily_r.HEADER:
        raise raw.IngestError("daily output header/terminator wrong")
    rows = [l.split(",") for l in lines[1:]]
    exp = group_hourly(texts, 10)
    want = sorted((c, (date(2001, 1, 1) + timedelta(d)).isoformat()) for c in CITIES for d in range(DAYS))
    if [(r[0], r[1]) for r in rows] != want or sorted(exp) != want:
        raise raw.IngestError(f"daily keys differ: {len(rows)} rows, {len(want)} expected, missing/extra/unsorted")
    incomplete = 0
    for r in rows:
        hrs = exp[(r[0], r[1])]
        cols, miss = hourly_columns(hrs)
        mean = lambda c: r4(sum(c) / len(c)) if c else ""
        total = sum(cols[2]) if len(hrs) == 24 and not miss[2] else None
        same = (len(r) == 17 and int(r[2]) == len(hrs) and r[3] == mean(cols[0]) and Fraction(r[4]) == min(cols[0])
                and Fraction(r[5]) == max(cols[0]) and r[6] == mean(cols[1]) and r[8] == mean(cols[3])
                and r[9] == mean(cols[4]) and r[10] == mean(cols[5]) and [int(x) for x in r[11:]] == miss
                and ((r[7] == "") if total is None else Fraction(r[7]) == total))
        if not same:
            raise raw.IngestError(f"daily mismatch at {r[0]} {r[1]}: {r}")
        incomplete += len(hrs) != 24 or any(miss)
    return {"rows": len(rows), "days_not_24_hours_or_with_missing": incomplete}


def verify_monthly(output, texts):
    """Recompute each month from the HOURLY INTERIM rows; means may differ by at most 0.0001 (rounded daily means)."""
    lines = output.split("\n")
    if lines.pop() != "" or lines[0] != monthly_r.HEADER:
        raise raw.IngestError("monthly output header/terminator wrong")
    rows = [l.split(",") for l in lines[1:]]
    want = sorted((c, f"{y}-{m:02d}") for c in CITIES for y in range(2001, 2026) for m in range(1, 13))
    if [(r[0], r[1]) for r in rows] != want:
        raise raw.IngestError(f"monthly keys differ: {len(rows)} rows, {len(want)} expected")
    hourly = group_hourly(texts, 7)
    worst = Fraction(0)
    for r in rows:
        hrs = hourly[(r[0], r[1])]
        cols, miss = hourly_columns(hrs)
        dim = calendar.monthrange(int(r[1][:4]), int(r[1][5:]))[1]
        total = sum(cols[2]) if int(r[2]) == dim and not miss[2] else None
        ok = (len(r) == 18 and int(r[2]) == len({h[0][:10] for h in hrs}) and int(r[3]) == len(hrs)
              and Fraction(r[5]) == min(cols[0]) and Fraction(r[6]) == max(cols[0]) and [int(x) for x in r[12:]] == miss
              and ((r[8] == "") if total is None else Fraction(r[8]) == total))
        for col, c in ((4, cols[0]), (7, cols[1]), (9, cols[3]), (10, cols[4]), (11, cols[5])):
            diff = abs(Fraction(r[col]) - sum(c) / len(c))
            worst = max(worst, diff)
            ok = ok and diff <= Fraction(1, 10000)
        if not ok:
            raise raw.IngestError(f"monthly mismatch at {r[0]} {r[1]}: {r}")
    return {"rows": len(rows), "max_abs_mean_difference_vs_hourly": float(worst)}


def verify_yearly(output, texts):
    """Recompute every city-year from the HOURLY INTERIM rows (daily extremes and precipitation totals built from hours)."""
    lines = output.split("\n")
    if lines.pop() != "" or lines[0] != yearly_r.HEADER:
        raise raw.IngestError("yearly output header/terminator wrong")
    rows = [l.split(",") for l in lines[1:]]
    want = sorted((c, str(y)) for c in CITIES for y in range(2001, 2026))
    if [(r[0], r[1]) for r in rows] != want:
        raise raw.IngestError(f"yearly keys differ: {len(rows)} rows, {len(want)} expected")
    years = {}
    for (city, day), hrs in group_hourly(texts, 10).items():
        cols, miss = hourly_columns(hrs)
        years.setdefault((city, day[:4]), []).append((len(hrs), cols[0], miss[0], sum(cols[2]) if len(hrs) == 24 and not miss[2] else None))
    worst = Fraction(0)
    for r in rows:
        days = years[(r[0], r[1])]
        totals = [d[3] for d in days if d[3] is not None]
        tmax = [max(d[1]) for d in days]
        tmin = [min(d[1]) for d in days]
        all_t = [t for d in days for t in d[1]]
        full = len(days) == (366 if calendar.isleap(int(r[1])) else 365) and len(totals) == len(days)
        mean_diff = abs(Fraction(r[4]) - sum(all_t) / len(all_t))
        worst = max(worst, mean_diff)
        ok = (len(r) == 15 and int(r[2]) == len(days) and int(r[3]) == sum(d[0] for d in days) and mean_diff <= Fraction(1, 10000)
              and Fraction(r[5]) == min(all_t) and Fraction(r[6]) == max(all_t)
              and ((r[7] == "") if not full else Fraction(r[7]) == sum(totals))
              and Fraction(r[8]) == max(totals) and int(r[9]) == len(totals)
              and [int(x) for x in r[10:]] == [sum(t >= 1 for t in totals), sum(t >= 10 for t in totals), sum(t >= 20 for t in totals),
                                                 sum(m >= 35 for m in tmax), sum(m < 0 for m in tmin)])
        if not ok:
            raise raw.IngestError(f"yearly mismatch at {r[0]} {r[1]}: {r}")
    return {"rows": len(rows), "max_abs_mean_difference_vs_hourly": float(worst)}


VERIFY = {"daily": verify_daily, "monthly": verify_monthly, "yearly": verify_yearly}


def run(job):
    cfg = JOBS[job]
    scripts = {s: hashlib.sha256((HERE / s).read_bytes()).hexdigest() for s in cfg["scripts"]}
    key = f"power_{job}/all"
    interim_inputs, texts = read_interim()
    inputs = interim_inputs if job == "daily" else read_daily_input()
    prior = raw.last_record(hdfs_text(cfg["manifest"]) if raw.hdfs_exists(cfg["manifest"]) else "", key)
    if raw.hdfs_exists(cfg["out"]):
        if prior and prior["status"] == "ok" and prior["inputs"] == inputs and prior["scripts"] == scripts \
                and sha_text(hdfs_text(f"{cfg['out']}/part-00000")) == prior["output"]["sha256"]:
            return {"result": "skipped", "reason": "already computed from identical inputs and scripts", **prior["output"]}
        raise raw.IngestError(f"{cfg['out']} exists but is not a valid current output; refusing to overwrite")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp = f"{H}/_tmp/power_{job}_{stamp}"
    name = f"earthscape_power_{job}_{stamp}"
    input_arg = f"{H}/interim/power_hourly/city=*/*.csv" if job == "daily" else DAILY_PATH
    record = {"object_key": key, "job": job, "version": cfg["version"], "style": "Hadoop Streaming (Python 3) on YARN",
              "scripts": scripts, "inputs": inputs, "submitted_at": stamp, "status": "started", "error": None,
              "job_id": None, "application_id": None, "yarn_final_state": None, "counters": None,
              "output": None, "validation": None}
    append(cfg["manifest"], record)
    try:
        rc, log = run_yarn(job, name, input_arg, tmp)
        log_file = ROOT / "artifacts" / "mapreduce" / f"{name}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(log, encoding="utf-8")
        record["job_id"], record["application_id"], record["counters"] = parse_log(log)
        record["log_file"] = str(log_file.relative_to(ROOT))
        record["yarn_final_state"], app_name = yarn_state(record["application_id"])
        if rc != 0 or record["yarn_final_state"] != "SUCCEEDED" or app_name != name:
            raise raw.IngestError(f"job did not succeed on YARN: rc={rc} state={record['yarn_final_state']} (log {log_file})")
        if not raw.hdfs_exists(f"{tmp}/_SUCCESS") or raw.hdfs_ok("-ls", tmp).decode().count("part-") != 1:
            raise raw.IngestError("expected _SUCCESS and exactly one part file")
        output = hdfs_text(f"{tmp}/part-00000")
        record["validation"] = VERIFY[job](output, texts)
        if record["validation"]["rows"] != cfg["rows"]:
            raise raw.IngestError(f"{record['validation']['rows']} rows, expected {cfg['rows']}")
        if read_interim()[0] != interim_inputs or (job != "daily" and read_daily_input() != inputs):
            raise raw.IngestError("an input changed during the job")
        raw.hdfs_ok("-mv", tmp, cfg["out"])
        record["output"] = {"hdfs_path": f"{cfg['out']}/part-00000", "size_bytes": len(output.encode("utf-8")),
                            "sha256": sha_text(output), "record_count": record["validation"]["rows"]}
        record["status"] = "ok"
        append(cfg["manifest"], record)
        return {"result": "computed", "job_id": record["job_id"], "application_id": record["application_id"],
                **record["output"], "validation": record["validation"]}
    except Exception as e:
        record["status"], record["error"] = "failed", str(e)[:500]
        append(cfg["manifest"], record)
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("job", choices=JOBS)
    try:
        print(json.dumps(run(p.parse_args(argv).job), indent=2))
    except raw.IngestError as e:
        sys.exit(f"MAPREDUCE JOB FAILED: {e}")


if __name__ == "__main__":
    main()
