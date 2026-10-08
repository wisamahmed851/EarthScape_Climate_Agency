"""Local health check: application, MongoDB, HDFS, live-source polling freshness and the newest backup.

Each run appends one JSON line (UTC timestamp, per-check result, failed checks) to logs/health.jsonl and returns it.
It checks and records only: it never restarts, repairs or notifies anything. Run by `manage.py health-check`,
normally from Windows Task Scheduler (docs/security-and-reliability.md).
"""
import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from . import data, live
from .settings import ROOT

LOG = ROOT / "logs" / "health.jsonl"
LOG_KEEP = 2000                       # lines; older ones are dropped so the file stays small
BACKUP_MAX_AGE = timedelta(hours=36)  # a daily backup that is more than a day and a half old counts as missing
BACKUP_DIR = re.compile(r"^earthscape_\d{8}T\d{6}Z$")


def check_app(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=10) as r:
            body = json.loads(r.read())
        return body.get("app") == "ok", f"poller {body.get('poller')}", body
    except (OSError, ValueError) as e:
        return False, f"not reachable ({type(e).__name__})", None


def check_mongo(settings):
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        client.admin.command("ping")
        return True, "ping ok", client[settings.mongo_db]
    except PyMongoError as e:
        client.close()
        return False, f"unavailable ({type(e).__name__})", None


def check_hdfs():
    try:
        data.hdfs("-test", "-e", f"{data.HDFS_ROOT}/processed/power_daily", timeout=30)
        return True, "processed layer readable"
    except data.DataError as e:
        return False, str(e)


def check_polling(db):
    rows = live.freshness(db)
    stale = [r["source"] for r in rows if r["stale"]]
    detail = ", ".join(f"{r['source']} {'STALE' if r['stale'] else 'fresh'} ({r['age_minutes']} min)" for r in rows)
    return not stale, detail


def newest_backup(root):
    found = [p for p in Path(root).glob("earthscape_*") if BACKUP_DIR.match(p.name) and (p / "BACKUP.json").exists()]
    return max(found, key=lambda p: p.name) if found else None


def check_backup(root, now):
    latest = newest_backup(root)
    if not latest:
        return False, "no backup found"
    meta = json.loads((latest / "BACKUP.json").read_text(encoding="utf-8"))
    created = datetime.strptime(meta["created_at"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    for name, info in meta["files"].items():
        if "sha256" in info and hashlib.sha256((latest / name).read_bytes()).hexdigest() != info["sha256"]:
            return False, f"{latest.name}: {name} fails its checksum"
    age = now - created
    return age <= BACKUP_MAX_AGE, f"{latest.name}, {round(age.total_seconds() / 3600, 1)} h old, checksums match"


def check_disk(path=ROOT, min_free_gb=5):
    free = shutil.disk_usage(path).free / 1e9
    return free >= min_free_gb, f"{free:.1f} GB free on the project drive (alert below {min_free_gb} GB)"


def check_resources(max_cpu=95, max_memory=95):
    """System CPU (1 s sample) and memory use, plus the memory of the app and database processes. Recorded; flagged only when extreme."""
    cpu, mem = psutil.cpu_percent(interval=1), psutil.virtual_memory()
    procs = {}
    for p in psutil.process_iter(["name", "cmdline", "memory_info"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            key = "app" if "uvicorn" in cmd and "app.main" in cmd else "mongod" if p.info["name"] == "mongod.exe" else None
            if key:
                procs[key] = procs.get(key, 0) + p.info["memory_info"].rss
        except (psutil.Error, OSError):
            pass
    detail = f"CPU {cpu:.0f}%, memory {mem.percent:.0f}% of {mem.total / 2**30:.1f} GiB" + "".join(f", {k} {v / 2**20:.0f} MiB" for k, v in sorted(procs.items()))
    return cpu < max_cpu and mem.percent < max_memory, detail, {"cpu_percent": cpu, "memory_percent": mem.percent, "process_rss_mib": {k: round(v / 2**20) for k, v in procs.items()}}


def run(settings, url="http://127.0.0.1:8000", backup_root=ROOT / "backups", log_path=LOG, now=None):
    now = now or datetime.now(timezone.utc)
    checks = {}
    ok, detail, _ = check_app(url)
    checks["application"] = {"ok": ok, "detail": detail}
    ok, detail, db = check_mongo(settings)
    checks["mongodb"] = {"ok": ok, "detail": detail}
    ok, detail = check_hdfs()
    checks["hdfs"] = {"ok": ok, "detail": detail}
    try:
        ok, detail = check_polling(db) if db is not None else (False, "skipped: MongoDB unavailable")
    except PyMongoError as e:
        ok, detail = False, f"MongoDB error ({type(e).__name__})"
    checks["live_polling"] = {"ok": ok, "detail": detail}
    if db is not None and hasattr(db, "client"):
        db.client.close()
    try:
        ok, detail = check_backup(backup_root, now)
    except (OSError, ValueError, KeyError) as e:
        ok, detail = False, f"backup unreadable ({type(e).__name__})"
    checks["backup"] = {"ok": ok, "detail": detail}
    ok, detail = check_disk()
    checks["disk"] = {"ok": ok, "detail": detail}
    ok, detail, metrics = check_resources()
    checks["resources"] = {"ok": ok, "detail": detail, **metrics}
    record = {"checked_at": now.isoformat(timespec="seconds"), "ok": all(c["ok"] for c in checks.values()), "checks": checks,
              "failures": [k for k, c in checks.items() if not c["ok"]]}
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    log_path.write_text("\n".join((lines + [json.dumps(record)])[-LOG_KEEP:]) + "\n", encoding="utf-8")
    return record
