"""Read-only access to the verified POWER PROCESSED daily/monthly outputs.

refresh() copies the two HDFS part files into a bounded local cache (2 files) only after checking them against their
provenance manifest records (sha256, size, rows, header, daily->monthly lineage). Requests are served from memory
loaded from that cache; HDFS is never touched per request. Nothing in HDFS is written.
"""
import bisect
import csv
import hashlib
import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

HDFS_ROOT = "/earthscape"
SOURCES = {"daily": "power_daily", "monthly": "power_monthly"}
DAILY_HEADER = ("city_id,date,hours_observed,temperature_2m_mean_c,temperature_2m_min_c,temperature_2m_max_c,"
                "relative_humidity_2m_mean_pct,precipitation_total_mm,wind_speed_2m_mean_m_s,surface_pressure_mean_kpa,"
                "solar_irradiance_mean_mj_hr,temperature_2m_missing_hours,relative_humidity_2m_missing_hours,"
                "precipitation_missing_hours,wind_speed_2m_missing_hours,surface_pressure_missing_hours,"
                "solar_irradiance_missing_hours")
MONTHLY_HEADER = ("city_id,month,days_observed,hours_observed,temperature_2m_mean_c,temperature_2m_min_c,"
                  "temperature_2m_max_c,relative_humidity_2m_mean_pct,precipitation_total_mm,wind_speed_2m_mean_m_s,"
                  "surface_pressure_mean_kpa,solar_irradiance_mean_mj_hr,temperature_2m_missing_hours,"
                  "relative_humidity_2m_missing_hours,precipitation_missing_hours,wind_speed_2m_missing_hours,"
                  "surface_pressure_missing_hours,solar_irradiance_missing_hours")
YEARLY_HEADER = ("city_id,year,days_observed,hours_observed,temperature_2m_mean_c,temperature_2m_min_c,temperature_2m_max_c,"
                 "precipitation_total_mm,max_daily_precipitation_mm,days_with_precip_total,wet_days_ge_1mm,"
                 "heavy_precip_days_ge_10mm,very_heavy_precip_days_ge_20mm,hot_days_max_ge_35c,frost_days_min_lt_0c")
HEADERS = {"daily": DAILY_HEADER, "monthly": MONTHLY_HEADER, "yearly": YEARLY_HEADER}
OPTIONAL = {"yearly": "power_yearly"}   # extreme-event job; the app works without it until it has run
VALUE_COLS = {
    "daily": ["hours_observed", "temperature_2m_mean_c", "temperature_2m_min_c", "temperature_2m_max_c",
              "relative_humidity_2m_mean_pct", "precipitation_total_mm", "wind_speed_2m_mean_m_s",
              "surface_pressure_mean_kpa", "solar_irradiance_mean_mj_hr"],
    "monthly": ["days_observed", "hours_observed", "temperature_2m_mean_c", "temperature_2m_min_c",
                "temperature_2m_max_c", "relative_humidity_2m_mean_pct", "precipitation_total_mm",
                "wind_speed_2m_mean_m_s", "surface_pressure_mean_kpa", "solar_irradiance_mean_mj_hr"],
    "yearly": ["days_observed", "hours_observed", "temperature_2m_mean_c", "temperature_2m_min_c", "temperature_2m_max_c",
               "precipitation_total_mm", "max_daily_precipitation_mm", "days_with_precip_total", "wet_days_ge_1mm",
               "heavy_precip_days_ge_10mm", "very_heavy_precip_days_ge_20mm", "hot_days_max_ge_35c", "frost_days_min_lt_0c"],
}
CITY_NAMES = {"karachi": "Karachi", "lahore": "Lahore", "islamabad": "Islamabad", "peshawar": "Peshawar", "quetta": "Quetta"}
MANIFESTS = ["power_hourly_gridded", "power_hourly_interim", "power_daily", "power_monthly", "power_yearly"]


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # background callers (server, scheduled tasks) must not flash a console per wsl call


class DataError(Exception):
    pass


def hdfs(*args, timeout=120):
    cmd = ["wsl", "-d", "Ubuntu-24.04", "-e", "/opt/hadoop/bin/hdfs", "dfs", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DataError(f"HDFS not reachable ({type(e).__name__})")
    if r.returncode != 0:
        raise DataError("HDFS command failed")
    return r.stdout


def last_ok(manifest_text, key):
    found = None
    for line in manifest_text.splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec["object_key"] == key and rec["status"] == "ok":
                found = rec
    return found


def manifest_summary(text):
    """Latest record per object_key: how many are ok and the latest timestamp."""
    last = {}
    for line in text.splitlines():
        if line.strip():
            rec = json.loads(line)
            last[rec["object_key"]] = rec
    ok = [r for r in last.values() if r["status"] == "ok"]
    stamps = [r.get(k) for r in ok for k in ("retrieved_at", "transformed_at", "submitted_at") if r.get(k)]
    return {"objects": len(last), "ok": len(ok), "failed": sum(r["status"] == "failed" for r in last.values()),
            "latest": max(stamps) if stamps else None}


def refresh(cache_dir):
    """Validate the PROCESSED outputs against their manifests and replace the local cache. Raises DataError."""
    cache_dir = Path(cache_dir)
    texts, records, manifests = {}, {}, {}
    for kind, name in {**SOURCES, **OPTIONAL}.items():
        try:
            mtext = hdfs("-cat", f"{HDFS_ROOT}/_manifest/{name}.jsonl").decode("utf-8")
        except DataError:
            if kind in OPTIONAL:
                continue
            raise
        rec = last_ok(mtext, f"{name}/all")
        if not rec:
            if kind in OPTIONAL:
                continue
            raise DataError(f"no ok provenance record for {name}")
        out = rec["output"]
        body = hdfs("-cat", out["hdfs_path"])
        if len(body) != out["size_bytes"] or hashlib.sha256(body).hexdigest() != out["sha256"]:
            raise DataError(f"{name} does not match its manifest size/sha256")
        text = body.decode("utf-8")
        lines = text.split("\n")
        if lines.pop() != "" or lines[0] != HEADERS[kind] or len(lines) - 1 != out["record_count"]:
            raise DataError(f"{name} header or row count does not match the manifest")
        texts[kind], records[kind] = text, rec
    if records["monthly"]["inputs"][0]["sha256"] != records["daily"]["output"]["sha256"]:
        raise DataError("monthly output was not built from the current daily output")
    for name in MANIFESTS:
        try:
            manifests[name] = manifest_summary(hdfs("-cat", f"{HDFS_ROOT}/_manifest/{name}.jsonl").decode("utf-8"))
        except DataError:
            if name != "power_yearly":
                raise
    meta = {"refreshed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "manifests": manifests}
    for kind, rec in records.items():
        meta[kind] = {**rec["output"], "job_id": rec["job_id"], "application_id": rec["application_id"],
                      "yarn_final_state": rec["yarn_final_state"], "submitted_at": rec["submitted_at"],
                      "version": rec["version"]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    for kind, text in texts.items():
        tmp = cache_dir / f"{kind}.csv.tmp"
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, cache_dir / f"{kind}.csv")
    (cache_dir / "meta.json.tmp").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    os.replace(cache_dir / "meta.json.tmp", cache_dir / "meta.json")
    return meta


def num(x):
    return float(x) if x != "" else None


class Store:
    """In-memory columns per city, loaded from the verified cache."""

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.lock = threading.Lock()
        self.meta = None
        self.tables = {}
        self.error = None
        self.load()

    def load(self):
        with self.lock:
            try:
                meta = json.loads((self.cache_dir / "meta.json").read_text(encoding="utf-8"))
                tables = {}
                for kind in [k for k in HEADERS if k in SOURCES or k in meta]:
                    raw = (self.cache_dir / f"{kind}.csv").read_bytes()
                    if hashlib.sha256(raw).hexdigest() != meta[kind]["sha256"]:
                        raise DataError(f"cached {kind} file does not match its recorded checksum")
                    tables[kind] = self._parse(kind, raw.decode("utf-8"))
                self.meta, self.tables, self.error = meta, tables, None
            except FileNotFoundError:
                self.meta, self.tables, self.error = None, {}, "No cached data yet. An Administrator must run the data refresh."
            except (DataError, ValueError, KeyError) as e:
                self.meta, self.tables, self.error = None, {}, f"Cached data is unusable: {e}"

    @staticmethod
    def _parse(kind, text):
        key = {"daily": "date", "monthly": "month", "yearly": "year"}[kind]
        by_city = {}
        for row in csv.DictReader(text.splitlines()):
            c = by_city.setdefault(row["city_id"], {"keys": [], **{v: [] for v in VALUE_COLS[kind]}})
            c["keys"].append(row[key])
            for v in VALUE_COLS[kind]:
                c[v].append(num(row[v]))
        return by_city

    @property
    def ready(self):
        return self.meta is not None

    def refresh(self, cache_dir=None):
        meta = refresh(self.cache_dir)
        self.load()
        return meta

    def cities(self):
        return [c for c in CITY_NAMES if c in self.tables.get("daily", {})]

    def window(self, kind, city, start, end):
        """Columns of one city between two keys (inclusive; ISO strings compare chronologically)."""
        t = self.tables[kind][city]
        lo, hi = bisect.bisect_left(t["keys"], start), bisect.bisect_right(t["keys"], end)
        return {k: v[lo:hi] for k, v in t.items()}

    def date_range(self):
        t = self.tables["daily"]
        return min(c["keys"][0] for c in t.values()), max(c["keys"][-1] for c in t.values())

    def overview(self, city):
        d = self.tables["daily"][city]
        valid = lambda col: [(v, k) for v, k in zip(d[col], d["keys"]) if v is not None]
        avg = lambda col: round(sum(v for v, _ in valid(col)) / len(valid(col)), 2) if valid(col) else None
        hi, lo = max(valid("temperature_2m_max_c")), min(valid("temperature_2m_min_c"))
        years = len({k[:4] for k in d["keys"]})
        rain = [v for v in d["precipitation_total_mm"] if v is not None]
        days = len(d["keys"])
        return {
            "city": city, "name": CITY_NAMES[city], "first_date": d["keys"][0], "last_date": d["keys"][-1], "days": days,
            "days_with_24_hours": sum(h == 24 for h in d["hours_observed"]),
            "days_without_precipitation_total": days - len(rain),
            "mean_temperature_c": avg("temperature_2m_mean_c"), "highest_daily_max_c": {"value": hi[0], "date": hi[1]},
            "lowest_daily_min_c": {"value": lo[0], "date": lo[1]},
            "mean_annual_precipitation_mm": round(sum(rain) / years, 1) if rain else None,
            "mean_humidity_pct": avg("relative_humidity_2m_mean_pct"), "mean_wind_m_s": avg("wind_speed_2m_mean_m_s"),
            "mean_pressure_kpa": avg("surface_pressure_mean_kpa"),
        }

    def compare(self, start_year, end_year):
        """Yearly series per city from the monthly output (hour-weighted means; precipitation only for complete years)."""
        out = {}
        for city, m in self.tables["monthly"].items():
            years = {}
            for i, key in enumerate(m["keys"]):
                y = int(key[:4])
                if start_year <= y <= end_year:
                    years.setdefault(y, []).append(i)
            series = {"temperature_c": [], "humidity_pct": [], "wind_m_s": [], "precipitation_mm": []}
            for y in sorted(years):
                idx = years[y]

                def wmean(col):
                    pairs = [(m[col][i], m["hours_observed"][i]) for i in idx if m[col][i] is not None]
                    return round(sum(v * h for v, h in pairs) / sum(h for _, h in pairs), 3) if pairs else None

                totals = [m["precipitation_total_mm"][i] for i in idx]
                series["temperature_c"].append(wmean("temperature_2m_mean_c"))
                series["humidity_pct"].append(wmean("relative_humidity_2m_mean_pct"))
                series["wind_m_s"].append(wmean("wind_speed_2m_mean_m_s"))
                series["precipitation_mm"].append(round(sum(totals), 1) if len(idx) == 12 and None not in totals else None)
            out[city] = {"years": sorted(years), **series}
        return out

    def extremes(self):
        """Yearly extreme-event rows (from the MapReduce yearly job) or None when that job's output is not cached."""
        y = self.tables.get("yearly")
        if not y:
            return None
        return {city: {"years": t["keys"], **{k: v for k, v in t.items() if k != "keys"}} for city, t in y.items()}
