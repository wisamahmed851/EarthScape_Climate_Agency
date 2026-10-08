"""Current-data collection, alert evaluation and the background poller.

poll_cycle: provider -> raw envelope appended to local staging -> normalised reading upserted into MongoDB
(`latest_readings`, unique per source/city/station/observed_at) -> alert rules evaluated. Staged days are sealed
into HDFS RAW once the UTC day is over (`seal_raw`). Polling is near-real-time REST polling, not event streaming.
"""
import hashlib
import json
import logging
import operator
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError, PyMongoError

from . import services
from .settings import ROOT

sys.path.insert(0, str(ROOT / "src"))
from ingestion import live_sources as src  # noqa: E402
from ingestion import nasa_power as hdfs_tools  # noqa: E402  (HDFS helpers only)

log = logging.getLogger("earthscape.live")
STAGING = ROOT / "data" / "staging"
# A reading older than this is stale: shown as stale and never used to raise or clear an alert.
STALE_AFTER = {src.WEATHER: timedelta(minutes=60), src.AIRQUALITY: timedelta(hours=3), src.OPENAQ: timedelta(hours=24)}
RAW_SUBDIR = {src.WEATHER: ("openmeteo_weather_model", "polls.jsonl"), src.AIRQUALITY: ("openmeteo_airquality_model", "polls.jsonl"),
              src.OPENAQ: ("openaq_observed/current", "latest.jsonl")}
SOURCE_LABELS = {src.WEATHER: "Open-Meteo current weather (modelled)", src.AIRQUALITY: "Open-Meteo air quality (modelled, CAMS)",
                 src.OPENAQ: "OpenAQ PM2.5 (observed)"}
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def now():
    return datetime.now(timezone.utc)


def aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- storage ----------
def store_readings(db, readings):
    """Upsert; an identical (source, city, station, observed_at) is a duplicate and is not stored twice. Returns new count."""
    new = 0
    for r in readings:
        key = {k: r[k] for k in ("source", "city_id", "station_key", "observed_at")}
        res = db.latest_readings.update_one(key, {"$setOnInsert": {k: v for k, v in r.items() if k not in key}}, upsert=True)
        new += res.upserted_id is not None
    return new


def stage_raw(source, envelope):
    stamp = envelope["retrieved_at"][:10]
    folder, name = RAW_SUBDIR[source]
    path = STAGING / folder / f"date={stamp}" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(envelope, separators=(",", ":")) + "\n")


def record_status(db, source, stored, errors):
    t = now()
    update = {"$set": {"last_attempt": t, "last_stored": stored, "last_errors": errors[:5]}}
    if not errors or stored:
        update["$set"]["last_success"] = t
    if errors:
        update["$inc"] = {"consecutive_failures": 1}
    else:
        update["$set"]["consecutive_failures"] = 0
    db.ingest_status.update_one({"_id": source}, update, upsert=True)


# ---------- polling ----------
def poll_openmeteo(db, source, cities=None):
    stored, errors = 0, []
    for city in cities or src.CITIES:
        try:
            env = src.fetch_openmeteo(source, city)
            stage_raw(source, env)
            stored += store_readings(db, [src.normalise_openmeteo(source, city, env)])
        except (src.FetchError, KeyError, ValueError) as e:
            errors.append(f"{city}: {type(e).__name__}: {e}"[:200])
    record_status(db, source, stored, errors)
    return stored, errors


def poll_openaq(db, cities=None):
    stored, errors = 0, []
    for city in cities or src.CITIES:
        try:
            envelopes, readings = src.fetch_openaq(city)
            for env in envelopes:
                stage_raw(src.OPENAQ, env)
            stored += store_readings(db, readings)
        except (src.FetchError, KeyError, ValueError) as e:
            errors.append(f"{city}: {type(e).__name__}: {e}"[:200])
    record_status(db, src.OPENAQ, stored, errors)
    return stored, errors


def poll_cycle(db, include_openaq=True):
    result = {}
    for source in (src.WEATHER, src.AIRQUALITY):
        result[source] = poll_openmeteo(db, source)
    if include_openaq:
        result[src.OPENAQ] = poll_openaq(db)
    result["alerts"] = evaluate_alerts(db)
    return result


# ---------- HDFS RAW sealing ----------
SEAL_GRACE = timedelta(minutes=15)   # a poll that straddles midnight may still be writing into the day that just ended
SEAL_META = {src.WEATHER: ("Open-Meteo", "modelled", "https://api.open-meteo.com/v1/forecast"),
             src.AIRQUALITY: ("Open-Meteo Air Quality", "modelled", "https://air-quality-api.open-meteo.com/v1/air-quality"),
             src.OPENAQ: ("OpenAQ", "observed", "https://api.openaq.org/v3")}


def seal_raw(db=None):
    """Upload staged daily files of finished UTC days to HDFS RAW (immutable, verified, one manifest record per object).

    Safe to repeat or resume after an interruption: an identical object already in HDFS is verified and reused, an
    identical manifest record is not appended twice, a different object is never overwritten, and the staged file is
    removed only after the object and its manifest record are both in place. Returns one result per staged day.
    """
    results = []
    cutoff = (now() - SEAL_GRACE).strftime("%Y-%m-%d")
    for source, (folder, name) in RAW_SUBDIR.items():
        for path in sorted((STAGING / folder).glob("date=*/" + name)):
            day = path.parent.name[5:]
            if day >= cutoff:
                continue
            key = f"{folder}/{day}"
            try:
                body = path.read_bytes()
                sha = hashlib.sha256(body).hexdigest()
                stamps = [json.loads(line)["retrieved_at"] for line in body.splitlines()]
                if not stamps or any(t[:10] != day for t in stamps):
                    raise hdfs_tools.IngestError("envelopes do not all belong to this UTC day")
                final = f"{hdfs_tools.HDFS_ROOT}/raw/{folder}/date={day}/{name}"
                manifest = f"{hdfs_tools.HDFS_ROOT}/_manifest/{folder.split('/')[0]}.jsonl"
                if hdfs_tools.hdfs_exists(final):
                    if hdfs_tools.hdfs_sha256(final) != sha:
                        raise hdfs_tools.IngestError("different object already exists; not overwritten")
                else:
                    tmp = f"{hdfs_tools.HDFS_ROOT}/_tmp/{folder.replace('/', '_')}_{day}.{sha[:12]}"
                    hdfs_tools.hdfs_ok("-mkdir", "-p", f"{hdfs_tools.HDFS_ROOT}/_tmp", final.rsplit("/", 1)[0])
                    with open(path, "rb") as f:
                        hdfs_tools.hdfs_ok("-put", "-f", "-", tmp, stdin=f)
                    if hdfs_tools.hdfs_sha256(tmp) != sha:
                        raise hdfs_tools.IngestError("HDFS copy does not match the staged file")
                    hdfs_tools.hdfs_ok("-mv", tmp, final)
                if hdfs_tools.hdfs_exists(manifest):
                    recorded = hdfs_tools.hdfs_ok("-cat", manifest).decode("utf-8").splitlines()
                else:
                    hdfs_tools.hdfs_ok("-mkdir", "-p", manifest.rsplit("/", 1)[0])
                    hdfs_tools.hdfs_ok("-touchz", manifest)
                    recorded = []
                if any((r := json.loads(line)).get("object_key") == key and r.get("sha256") == sha and r.get("status") == "ok"
                       for line in recorded if line.strip()):
                    status = "already sealed"
                else:
                    provider, origin, api = SEAL_META[source]
                    rec = {"object_key": key, "source": folder, "provider": provider, "value_origin": origin, "api": api,
                           "format": "jsonl envelopes (provider JSON responses, unmodified)", "partition": "UTC day of retrieved_at",
                           "utc_day": day, "first_retrieved_at": min(stamps), "last_retrieved_at": max(stamps),
                           "hdfs_path": final, "size_bytes": len(body), "sha256": sha, "record_count": len(stamps), "status": "ok",
                           "sealed_at": now().isoformat(timespec="seconds")}
                    r = hdfs_tools.hdfs("-appendToFile", "-", manifest, input=(json.dumps(rec) + "\n").encode())
                    if r.returncode != 0:
                        raise hdfs_tools.IngestError("manifest append failed")
                    status = "sealed"
                path.unlink()
                results.append({"object": key, "status": status})
            except Exception as e:   # HDFS down, bad staged file, conflicting object: keep the staged file for the next attempt
                results.append({"object": key, "status": "kept locally", "reason": f"{type(e).__name__}: {e}"[:200]})
    return results


# ---------- alert evaluation ----------
def evaluate_alerts(db):
    """Evaluate enabled live-variable rules against each city's latest non-stale readings.

    One alert per (rule, city, station) episode: while the condition keeps holding no new alert is created; when it
    stops holding the episode is marked cleared. Stale readings neither raise nor clear alerts.
    """
    summary = {"rules_evaluated": 0, "created": 0, "cleared": 0, "stale_skipped": 0, "no_data": 0}
    t = now()
    for rule in db.alert_rules.find({"enabled": True}):
        live = services.LIVE_VARIABLES.get(rule["variable"])
        if not live:
            continue
        source, field, unit = live
        summary["rules_evaluated"] += 1
        for city in (src.CITIES if rule["city_id"] == "all" else [rule["city_id"]]):
            latest = list(db.latest_readings.aggregate([
                {"$match": {"source": source, "city_id": city, f"values.{field}": {"$exists": True}}},
                {"$sort": {"observed_at": -1}}, {"$group": {"_id": "$station_key", "doc": {"$first": "$$ROOT"}}}]))
            if not latest:
                summary["no_data"] += 1
            for item in latest:
                doc = item["doc"]
                if t - aware(doc["observed_at"]) > STALE_AFTER[source]:
                    summary["stale_skipped"] += 1
                    continue
                value = doc["values"][field]
                episode = {"rule_id": rule["_id"], "city_id": city, "station_key": doc["station_key"], "status": {"$in": ["open", "acknowledged"]}}
                current = db.alerts.find_one(episode)
                if OPS[rule["operator"]](value, rule["threshold"]):
                    if current:
                        db.alerts.update_one({"_id": current["_id"]}, {"$set": {"last_value": value, "last_seen_at": t}})
                        continue
                    try:
                        db.alerts.insert_one({
                            "rule_id": rule["_id"], "rule_name": rule["name"], "city_id": city, "station_key": doc["station_key"],
                            "variable": rule["variable"], "operator": rule["operator"], "threshold": rule["threshold"],
                            "severity": rule["severity"], "value": value, "last_value": value, "unit": unit, "source": source,
                            "value_origin": doc["value_origin"], "observed_at": doc["observed_at"], "evaluated_at": t,
                            "last_seen_at": t, "status": "open", "station": (doc.get("provider") or {}).get("location_name")})
                        summary["created"] += 1
                    except DuplicateKeyError:
                        pass
                elif current:
                    db.alerts.update_one({"_id": current["_id"]}, {"$set": {"status": "cleared", "cleared_at": t, "last_value": value}})
                    summary["cleared"] += 1
    db.ingest_status.update_one({"_id": "alert_evaluation"}, {"$set": {"last_attempt": t, "last_success": t, "summary": summary}}, upsert=True)
    return summary


# ---------- freshness for the dashboard ----------
def freshness(db):
    rows = []
    for source, label in SOURCE_LABELS.items():
        status = db.ingest_status.find_one({"_id": source}) or {}
        newest = db.latest_readings.find_one({"source": source}, sort=[("observed_at", -1)])
        age = None
        if newest:
            age = (now() - aware(newest["observed_at"])).total_seconds()
        rows.append({"source": source, "label": label, "origin": "observed" if source == src.OPENAQ else "modelled",
                     "last_observed": newest["observed_at"] if newest else None, "age_minutes": round(age / 60) if age is not None else None,
                     "stale": age is None or age > STALE_AFTER[source].total_seconds(),
                     "last_attempt": status.get("last_attempt"), "last_success": status.get("last_success"),
                     "last_errors": status.get("last_errors", []), "consecutive_failures": status.get("consecutive_failures", 0),
                     "stored_last_cycle": status.get("last_stored"), "readings_total": db.latest_readings.count_documents({"source": source})})
    return rows


def latest_by_city(db):
    """Latest weather and air-quality reading per city plus the active OpenAQ stations."""
    out = {}
    for city in src.CITIES:
        entry = {"weather": None, "air_quality": None, "openaq": []}
        for key, source in (("weather", src.WEATHER), ("air_quality", src.AIRQUALITY)):
            d = db.latest_readings.find_one({"source": source, "city_id": city}, sort=[("observed_at", -1)])
            if d:
                d["stale"] = (now() - aware(d["observed_at"])) > STALE_AFTER[source]
            entry[key] = d
        stations = db.latest_readings.aggregate([{"$match": {"source": src.OPENAQ, "city_id": city}}, {"$sort": {"observed_at": -1}},
                                                 {"$group": {"_id": "$station_key", "doc": {"$first": "$$ROOT"}}}])
        for s in stations:
            d = s["doc"]
            d["stale"] = (now() - aware(d["observed_at"])) > STALE_AFTER[src.OPENAQ]
            entry["openaq"].append(d)
        entry["openaq"].sort(key=lambda d: d["provider"]["distance_km"])
        out[city] = entry
    return out


# ---------- background poller ----------
class Poller(threading.Thread):
    """Runs poll_cycle every `minutes` (OpenAQ every `openaq_minutes`) until stopped. Failures are logged, never fatal."""

    def __init__(self, db, minutes=15, openaq_minutes=60):
        super().__init__(daemon=True, name="earthscape-poller")
        self.db, self.minutes, self.openaq_minutes = db, minutes, openaq_minutes
        self.stop_event = threading.Event()
        self.last_openaq = None

    def run(self):
        while not self.stop_event.is_set():
            try:
                due = self.last_openaq is None or now() - self.last_openaq >= timedelta(minutes=self.openaq_minutes)
                started = time.monotonic()
                result = poll_cycle(self.db, include_openaq=due)
                seconds = round(time.monotonic() - started, 1)
                self.db.ingest_status.update_one({"_id": "poll_cycle"}, {"$set": {"last_cycle_seconds": seconds, "at": now()}}, upsert=True)
                if due:
                    self.last_openaq = now()
                log.info("poll cycle done in %s s: %s", seconds, {k: (v if k == "alerts" else v[0]) for k, v in result.items()})
                seal_raw(self.db)
            except (PyMongoError, OSError) as e:
                log.error("poll cycle failed: %s", type(e).__name__)
            except Exception as e:   # keep the thread alive
                log.error("poll cycle error: %s", type(e).__name__)
            self.stop_event.wait(self.minutes * 60)

    def stop(self):
        self.stop_event.set()
