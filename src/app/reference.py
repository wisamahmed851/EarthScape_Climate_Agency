"""Historical reference for current weather: NASA POWER hourly climatology versus Open-Meteo current readings"""
import functools
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from . import data

FILE = "hourly_reference.json"
WINDOW_DAYS = 7
MIN_SAMPLES = 200
VARIABLES = {
    "temperature": ("temperature_2m_c", "temperature_2m", "C"),
    "humidity": ("relative_humidity_2m_pct", "relative_humidity_2m", "%"),
}
PROVIDER_UNITS = {"temperature_2m": {"°C", "C"}, "relative_humidity_2m": {"%"}}
SLOTS = 366 * 24
MONTH_DAY = [(m, d) for m, days in enumerate([31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], 1) for d in range(1, days + 1)]
MD_INDEX = {md: i for i, md in enumerate(MONTH_DAY)}


def slot(ts):
    return MD_INDEX[(ts.month, ts.day)] * 24 + ts.hour


def build(cache_dir):
    """Read the five INTERIM files from HDFS, check them against the interim manifest, write the reference file"""
    manifest = data.hdfs("-cat", f"{data.HDFS_ROOT}/_manifest/power_hourly_interim.jsonl").decode("utf-8")
    out = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "window_days": WINDOW_DAYS, "cities": {}, "inputs": {}}
    for city in data.CITY_NAMES:
        rec = data.last_ok(manifest, f"power_hourly_interim/{city}")
        if not rec:
            raise data.DataError(f"no ok interim record for {city}")
        body = data.hdfs("-cat", rec["hdfs_path"], timeout=300)
        if len(body) != rec["size_bytes"] or hashlib.sha256(body).hexdigest() != rec["sha256"]:
            raise data.DataError(f"INTERIM {city} does not match its manifest size/sha256")
        out["cities"][city], years = accumulate(body.decode("utf-8").splitlines())
        out["inputs"][city] = {"hdfs_path": rec["hdfs_path"], "sha256": rec["sha256"], "years": years}
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cache_dir / (FILE + ".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, cache_dir / FILE)
    return out


def accumulate(lines):
    """Per-variable count/sum/sum-of-squares arrays by (calendar day, UTC hour). Empty cells are skipped, never filled"""
    header = lines[0].split(",")
    cols = {k: header.index(c) for k, (c, _, _) in VARIABLES.items()}
    acc = {k: {"n": [0] * SLOTS, "sum": [0.0] * SLOTS, "sumsq": [0.0] * SLOTS} for k in VARIABLES}
    years = set()
    for line in lines[1:]:
        f = line.split(",")
        ts = datetime.strptime(f[0], "%Y-%m-%dT%H:%M:%SZ")
        years.add(ts.year)
        i = slot(ts)
        for k, c in cols.items():
            if f[c] != "":
                v = float(f[c])
                a = acc[k]
                a["n"][i] += 1
                a["sum"][i] += v
                a["sumsq"][i] += v * v
    for a in acc.values():
        a["sum"] = [round(x, 4) for x in a["sum"]]
        a["sumsq"] = [round(x, 4) for x in a["sumsq"]]
    return acc, [min(years), max(years)]


@functools.lru_cache(maxsize=2)
def _load(path, mtime):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load(cache_dir):
    path = Path(cache_dir) / FILE
    try:
        return _load(str(path), path.stat().st_mtime_ns)
    except FileNotFoundError:
        return None


def reference(ref, city, variable, ts):
    """Mean, SD and sample count for the calendar window around ts (UTC) at ts.hour, or None when too few samples"""
    a = ref["cities"][city][variable]
    centre = MD_INDEX[(ts.month, ts.day)]
    n = s = ss = 0
    for off in range(-WINDOW_DAYS, WINDOW_DAYS + 1):
        i = ((centre + off) % 366) * 24 + ts.hour
        n, s, ss = n + a["n"][i], s + a["sum"][i], ss + a["sumsq"][i]
    if n < MIN_SAMPLES:
        return None
    mean = s / n
    return {"mean": round(mean, 2), "sd": round(math.sqrt(max(ss / n - mean * mean, 0.0) * n / (n - 1)), 2), "n": n}


def describe(z):
    if z is None:
        return "no reference"
    a = abs(z)
    side = "above" if z > 0 else "below"
    return "within the typical range" if a <= 1 else f"{side} the typical range" if a <= 2 else f"well {side} the typical range"


def compare_city(ref, city, readings, now, stale_after_minutes=60):
    """readings: Open-Meteo weather docs (newest data last is not required). Returns per-variable series and a latest summary"""
    out = {"city": city, "reference_years": ref["inputs"][city]["years"], "window_days": ref["window_days"], "variables": {}}
    newest = max((r["observed_at"] for r in readings), default=None)
    age = round((now - newest).total_seconds() / 60) if newest else None
    out["latest_age_minutes"], out["stale"] = age, age is None or age > stale_after_minutes
    for key, (_, field, unit) in VARIABLES.items():
        rows = []
        for r in sorted(readings, key=lambda r: r["observed_at"]):
            if r["values"].get(field) is None:
                continue
            if r.get("units", {}).get(field) not in PROVIDER_UNITS[field]:
                continue
            ts = r["observed_at"]
            ref_v = reference(ref, city, key, ts)
            z = (r["values"][field] - ref_v["mean"]) / ref_v["sd"] if ref_v and ref_v["sd"] > 0 else None
            rows.append({"time": ts.strftime("%Y-%m-%dT%H:%MZ"), "value": r["values"][field], "ref_mean": ref_v and ref_v["mean"],
                         "ref_sd": ref_v and ref_v["sd"], "samples": ref_v and ref_v["n"],
                         "difference": round(r["values"][field] - ref_v["mean"], 2) if ref_v else None,
                         "z": round(z, 2) if z is not None else None})
        latest = rows[-1] if rows else None
        out["variables"][key] = {"unit": unit, "series": rows,
                                 "latest": latest and {**latest, "assessment": "stale: not current" if out["stale"] else describe(latest["z"])}}
    return out
