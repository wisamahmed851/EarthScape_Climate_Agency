"""MongoDB connection and indexes. Holds users, sessions, feedback, alert rules/alerts, current readings, ingest status and ML results (not the historical dataset)."""
from pymongo import ASCENDING, DESCENDING, MongoClient


def connect(settings):
    return MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000)[settings.mongo_db]


def ensure_indexes(db):
    db.users.create_index("username", unique=True)
    db.sessions.create_index("expires_at", expireAfterSeconds=0)
    db.sessions.create_index("user_id")
    db.feedback.create_index([("created_at", DESCENDING)])
    db.feedback.create_index("status")
    db.alert_rules.create_index("name", unique=True)
    db.alert_rules.create_index([("enabled", ASCENDING), ("variable", ASCENDING)])
    # Current readings: unique per source/city/station/observed time (duplicate polls are not stored twice); 30-day TTL.
    db.latest_readings.create_index([("city_id", ASCENDING), ("source", ASCENDING), ("observed_at", DESCENDING)])
    db.latest_readings.create_index([("source", ASCENDING), ("city_id", ASCENDING), ("station_key", ASCENDING), ("observed_at", ASCENDING)],
                                    unique=True, name="uniq_reading")
    db.latest_readings.create_index("retrieved_at", expireAfterSeconds=30 * 24 * 3600, name="ttl_30d")
    db.alerts.create_index([("rule_id", ASCENDING), ("city_id", ASCENDING), ("station_key", ASCENDING), ("observed_at", ASCENDING)],
                           unique=True, name="uniq_alert")
    db.alerts.create_index([("status", ASCENDING), ("evaluated_at", DESCENDING)])
    db.ml_runs.create_index([("created_at", DESCENDING)])
