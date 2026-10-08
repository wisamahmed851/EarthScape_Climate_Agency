"""NASA POWER historical batch: every approved city x year through nasa_power.ingest, then an HDFS check.

Sequential and resumable: nasa_power.ingest skips city-years the manifest already records as ok.

Run: python src/ingestion/nasa_power_batch.py [--start-year 2001] [--end-year 2025] [--delay 1.0]
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone

import nasa_power as np_

MAX_CONSECUTIVE_FAILURES = 5  # more than this in a row suggests a systemic problem (HDFS down, no network)


def work_units(cities, first_year, last_year):
    return [(c, y) for c in cities for y in range(first_year, last_year + 1)]


def expected_rows(year):
    return (date(year, 12, 31) - date(year, 1, 1)).days * 24 + 24


def run_batch(units, ingest_one, delay=1.0, sleep=time.sleep):
    """ingest_one(city, year) -> result dict. Failures are collected; the batch continues."""
    results, consecutive = [], 0
    for city, year in units:
        try:
            r = ingest_one(city, year)
            consecutive = 0
            if r["result"] != "skipped":
                sleep(delay)
        except Exception as e:
            r = {"result": "failed", "error": str(e)[:300]}
            consecutive += 1
        results.append({"city": city, "year": year, **r})
        print(f"{city} {year}: {r['result']}" + (f" - {r['error']}" if r["result"] == "failed" else ""), flush=True)
        if consecutive > MAX_CONSECUTIVE_FAILURES:
            print("aborting: too many consecutive failures", flush=True)
            break
    attempted = {(r["city"], r["year"]) for r in results}
    return results, [u for u in units if u not in attempted]


def summarize(units, results, not_attempted, started, ended):
    counts = Counter(r["result"] for r in results)
    return {
        "expected_objects": len(units),
        "ingested": counts["ingested"], "adopted": counts["adopted existing identical object"],
        "skipped": counts["skipped"], "failed": counts["failed"], "not_attempted": len(not_attempted),
        "failures": [{"city": r["city"], "year": r["year"], "reason": r["error"]} for r in results if r["result"] == "failed"],
        "max_attempts_seen": max([r.get("attempts") or 1 for r in results] or [1]),
        "started": started.isoformat(timespec="seconds"), "ended": ended.isoformat(timespec="seconds"),
        "elapsed_seconds": round((ended - started).total_seconds(), 1),
    }


def exit_code(summary):
    return 1 if summary["failed"] or summary["not_attempted"] or summary["verification"]["problems"] else 0


def hdfs_bytes(path):
    r = np_.hdfs("-cat", path)
    if r.returncode != 0:
        raise np_.IngestError(f"cannot read {path}: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout


def verify(units, cities, variables):
    """Check HDFS directly: every object present, manifest ok and consistent, sha256/size/rows re-derived."""
    manifest = np_.read_manifest()
    listed = {l.split()[-1] for l in np_.hdfs_ok("-ls", "-R", f"{np_.HDFS_ROOT}/raw/{np_.SOURCE}").decode().splitlines()
              if l.startswith("-")}
    expected_paths = {np_.raw_path(c, date(y, 1, 1), date(y, 12, 31)) for c, y in units}
    problems = [f"missing in HDFS: {p}" for p in sorted(expected_paths - listed)]
    problems += [f"unexpected file: {p}" for p in sorted(listed - expected_paths)]
    tmp = np_.hdfs("-ls", f"{np_.HDFS_ROOT}/_tmp")
    if tmp.returncode == 0 and b"/_tmp/" in tmp.stdout:
        problems.append("leftover files in /earthscape/_tmp")

    per_city = {c: {"objects": 0, "rows": 0, "bytes": 0, "fill_value_cells": 0} for c in cities}
    sentinel = Counter()
    for city, year in units:
        path = np_.raw_path(city, date(year, 1, 1), date(year, 12, 31))
        if path not in listed:
            continue
        rec = np_.last_record(manifest, f"{np_.SOURCE}/{city}/{year}0101_{year}1231")
        data = hdfs_bytes(path)
        sha = hashlib.sha256(data).hexdigest()
        if not rec or rec["status"] != "ok":
            problems.append(f"{city} {year}: manifest has no ok record")
        elif (rec["sha256"], rec["size_bytes"], rec["hdfs_path"]) != (sha, len(data), path):
            problems.append(f"{city} {year}: manifest sha256/size/path differs from HDFS object")
        coords = CITIES[city]
        try:
            rows, missing = np_.validate(data.decode("utf-8"), coords["latitude"], coords["longitude"],
                                         date(year, 1, 1), date(year, 12, 31), variables)
        except np_.IngestError as e:
            problems.append(f"{city} {year}: validation failed on HDFS object: {e}")
            continue
        if rows != expected_rows(year):
            problems.append(f"{city} {year}: {rows} rows, expected {expected_rows(year)}")
        body = data.decode("utf-8").splitlines()
        body = body[body.index("-END HEADER-") + 2:]
        for line in body:
            for var, value in zip(variables, line.split(",")[4:]):
                if float(value) == -999.0:
                    sentinel[(city, year, var)] += 1
        c = per_city[city]
        c["objects"] += 1; c["rows"] += rows; c["bytes"] += len(data); c["fill_value_cells"] += missing
    failed_dir = np_.ROOT / "data" / "staging" / np_.SOURCE / "_failed"
    if failed_dir.exists() and any(failed_dir.iterdir()):
        problems.append(f"files in {failed_dir}")
    return {
        "objects_in_hdfs": sum(c["objects"] for c in per_city.values()),
        "rows_expected": sum(expected_rows(y) for _, y in units),
        "rows_actual": sum(c["rows"] for c in per_city.values()),
        "bytes": sum(c["bytes"] for c in per_city.values()),
        "fill_value_cells": sum(c["fill_value_cells"] for c in per_city.values()),
        "per_city": per_city,
        "fill_value_inventory": [{"city": c, "year": y, "variable": v, "cells": n} for (c, y, v), n in sorted(sentinel.items())],
        "problems": problems,
    }


CITIES = json.loads((np_.ROOT / "config" / "cities.json").read_text(encoding="utf-8"))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start-year", type=int, default=2001)
    p.add_argument("--end-year", type=int, default=2025)
    p.add_argument("--delay", type=float, default=1.0, help="seconds to wait after each new download")
    a = p.parse_args(argv)

    units = work_units(list(CITIES), a.start_year, a.end_year)
    staging = str(np_.ROOT / "data" / "staging" / np_.SOURCE)

    def ingest_one(city, year):
        c = CITIES[city]
        return np_.ingest(city, c["latitude"], c["longitude"], date(year, 1, 1), date(year, 12, 31),
                          np_.APPROVED_VARIABLES, staging)

    started = datetime.now(timezone.utc)
    results, not_attempted = run_batch(units, ingest_one, a.delay)
    summary = summarize(units, results, not_attempted, started, datetime.now(timezone.utc))
    summary["verification"] = verify(units, list(CITIES), np_.APPROVED_VARIABLES)

    out_dir = np_.ROOT / "artifacts" / "ingestion"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{np_.SOURCE}_{started:%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    v = summary["verification"]
    print(f"\nexpected {summary['expected_objects']} | ingested {summary['ingested']} | adopted {summary['adopted']} | "
          f"skipped {summary['skipped']} | failed {summary['failed']} | not attempted {summary['not_attempted']}")
    print(f"HDFS objects {v['objects_in_hdfs']} | rows {v['rows_actual']}/{v['rows_expected']} | bytes {v['bytes']} | "
          f"-999 cells {v['fill_value_cells']} | elapsed {summary['elapsed_seconds']}s | problems {len(v['problems'])}")
    for f in summary["failures"]:
        print("FAILED:", f)
    for pr in v["problems"]:
        print("PROBLEM:", pr)
    print("summary:", out)
    sys.exit(exit_code(summary))


if __name__ == "__main__":
    main()
