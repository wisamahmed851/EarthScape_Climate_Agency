"""Local management commands (run from the project root with the project venv).

  python src/app/manage.py init-db                        create MongoDB indexes
  python src/app/manage.py create-admin --username admin  password prompted twice, or EARTHSCAPE_ADMIN_PASSWORD
  python src/app/manage.py refresh-data                   HDFS processed outputs -> verified local cache
  python src/app/manage.py build-reference                POWER hourly climatology (INTERIM, verified) for the current-vs-historical comparison
  python src/app/manage.py train-ml                       train/evaluate climate analytics on the cache, store the run
  python src/app/manage.py poll-once [--no-openaq]        one polling cycle: weather, air quality, OpenAQ, alert evaluation
  python src/app/manage.py seal-raw                       upload finished UTC days of staged raw polls to HDFS RAW
  python src/app/manage.py backup [--hdfs] [--keep N]     export application data (and optionally HDFS manifests/outputs); keeps the newest N (default 7)
  python src/app/manage.py health-check [--url URL]       app, MongoDB, HDFS, polling freshness, newest backup -> logs/health.jsonl; exit 1 on failure
  python src/app/manage.py restore --path DIR [--replace] [--target-db NAME] restore a backup (refuses to touch non-empty collections otherwise)
"""
import argparse
import getpass
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bson import json_util

from app import analytics, data, db as dbmod, live, monitor, reference, services
from app.settings import ROOT, Settings

BACKUP_COLLECTIONS = ["users", "feedback", "alert_rules", "alerts", "ml_runs", "ingest_status", "latest_readings"]


def backup(database, root, with_hdfs):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(root) / f"earthscape_{stamp}"
    out.mkdir(parents=True)
    files = {}
    for name in BACKUP_COLLECTIONS:
        docs = list(database[name].find())
        path = out / f"{name}.json"
        path.write_text(json_util.dumps(docs, indent=0), encoding="utf-8")
        files[path.name] = {"documents": len(docs)}
    if with_hdfs:
        for sub in ("_manifest", "processed"):
            for line in data.hdfs("-ls", "-R", f"{data.HDFS_ROOT}/{sub}", timeout=300).decode().splitlines():
                if line.startswith("-"):
                    remote = line.split(None, 7)[7]
                    local = out / "hdfs" / remote.removeprefix(data.HDFS_ROOT + "/")
                    local.parent.mkdir(parents=True, exist_ok=True)
                    local.write_bytes(data.hdfs("-cat", remote, timeout=300))
    for p in sorted(out.rglob("*")):
        if p.is_file():
            files.setdefault(str(p.relative_to(out)), {})["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    (out / "BACKUP.json").write_text(json.dumps({"created_at": stamp, "database": database.name, "files": files}, indent=1), encoding="utf-8")
    return out


def prune_backups(root, keep):
    """Delete the oldest backup folders (named earthscape_<UTC stamp> and holding a BACKUP.json) beyond the newest `keep`"""
    folders = sorted(p for p in Path(root).glob("earthscape_*") if monitor.BACKUP_DIR.match(p.name) and (p / "BACKUP.json").exists())
    removed = folders[:-keep] if keep >= 1 else []
    for p in removed:
        shutil.rmtree(p)
    return [p.name for p in removed]


def restore(database, path, replace):
    path = Path(path)
    meta = json.loads((path / "BACKUP.json").read_text(encoding="utf-8"))
    for name, info in meta["files"].items():
        if "sha256" in info and hashlib.sha256((path / name).read_bytes()).hexdigest() != info["sha256"]:
            sys.exit(f"Backup file {name} does not match its recorded checksum; nothing restored.")
    for name in BACKUP_COLLECTIONS:
        if database[name].count_documents({}) and not replace:
            sys.exit(f"Collection {name} is not empty; use --replace to overwrite it. Nothing restored.")
    for name in BACKUP_COLLECTIONS:
        docs = json_util.loads((path / f"{name}.json").read_text(encoding="utf-8"))
        database[name].delete_many({})
        if docs:
            database[name].insert_many(docs)
        print(f"{name}: {len(docs)} documents")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("refresh-data")
    sub.add_parser("build-reference")
    sub.add_parser("train-ml")
    sub.add_parser("seal-raw")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--username", required=True)
    poll = sub.add_parser("poll-once")
    poll.add_argument("--no-openaq", action="store_true")
    b = sub.add_parser("backup")
    b.add_argument("--hdfs", action="store_true")
    b.add_argument("--dir", default=str(ROOT / "backups"))
    b.add_argument("--keep", type=int, default=7)
    h = sub.add_parser("health-check")
    h.add_argument("--url", default="http://127.0.0.1:8000")
    r = sub.add_parser("restore")
    r.add_argument("--path", required=True)
    r.add_argument("--replace", action="store_true")
    r.add_argument("--target-db", default="")
    args = p.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "refresh-data":
        meta = data.refresh(settings.cache_dir)
        print(f"cache refreshed at {meta['refreshed_at']}: daily {meta['daily']['record_count']}, monthly {meta['monthly']['record_count']}"
              + (f", yearly {meta['yearly']['record_count']}" if "yearly" in meta else "") + " rows")
        return
    if args.cmd == "health-check":
        rec = monitor.run(settings, args.url)
        print(json.dumps(rec, indent=1))
        sys.exit(0 if rec["ok"] else 1)
    if args.cmd == "build-reference":
        ref = reference.build(settings.cache_dir)
        print("hourly reference written: " + ", ".join(f"{c} {i['years'][0]}-{i['years'][1]}" for c, i in ref["inputs"].items()))
        return
    database = dbmod.connect(settings)
    dbmod.ensure_indexes(database)
    if args.cmd == "init-db":
        print("indexes ready")
    elif args.cmd == "train-ml":
        print(json.dumps(analytics.train(database, settings.cache_dir), indent=1))
    elif args.cmd == "poll-once":
        res = live.poll_cycle(database, include_openaq=not args.no_openaq)
        print(json.dumps({k: (v if k == "alerts" else {"stored": v[0], "errors": v[1]}) for k, v in res.items()}, indent=1))
    elif args.cmd == "seal-raw":
        print(json.dumps(live.seal_raw(database), indent=1))
    elif args.cmd == "backup":
        out = backup(database, args.dir, args.hdfs)
        print(f"backup written to {out}; pruned {prune_backups(args.dir, args.keep) if args.keep >= 1 else []}")
    elif args.cmd == "restore":
        target = database.client[args.target_db] if args.target_db else database
        dbmod.ensure_indexes(target)
        restore(target, args.path, args.replace)
    elif args.cmd == "create-admin":
        password = os.environ.get("EARTHSCAPE_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("Administrator password: ")
            if password != getpass.getpass("Repeat password: "):
                sys.exit("Passwords differ.")
        try:
            services.create_user(database, args.username, password, "Administrator")
        except services.ValidationError as e:
            sys.exit(f"Not created: {e}")
        print(f"Administrator '{args.username.strip().lower()}' created.")


if __name__ == "__main__":
    main()
