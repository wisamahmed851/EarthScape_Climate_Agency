"""MongoDB operations for users, sessions, feedback and alert rules, with input validation."""
import math
import re
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from . import security

ROLES = ("Administrator", "Analyst")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")
MAX_FAILED, LOCK_MINUTES = 5, 15
FEEDBACK_CATEGORIES = ("feedback", "support", "bug")
FEEDBACK_STATUSES = ("new", "reviewed")
# Daily variables a rule can target; names match the PROCESSED daily schema (evaluation is not implemented yet).
ALERT_VARIABLES = {
    "temperature_2m_max_c": "Daily maximum temperature (C)",
    "temperature_2m_min_c": "Daily minimum temperature (C)",
    "temperature_2m_mean_c": "Daily mean temperature (C)",
    "relative_humidity_2m_mean_pct": "Daily mean relative humidity (%)",
    "precipitation_total_mm": "Daily precipitation total (mm/day)",
    "wind_speed_2m_mean_m_s": "Daily mean wind speed at 2 m (m/s)",
    "surface_pressure_mean_kpa": "Daily mean surface pressure (kPa)",
}
# Live variables, evaluated against incoming readings: key -> (source, field, unit). Wording always states modelled/observed.
LIVE_VARIABLES = {
    "openmeteo_weather_model:temperature_2m": ("openmeteo_weather_model", "temperature_2m", "C"),
    "openmeteo_weather_model:relative_humidity_2m": ("openmeteo_weather_model", "relative_humidity_2m", "%"),
    "openmeteo_weather_model:precipitation": ("openmeteo_weather_model", "precipitation", "mm"),
    "openmeteo_weather_model:wind_speed_10m": ("openmeteo_weather_model", "wind_speed_10m", "km/h"),
    "openmeteo_weather_model:surface_pressure": ("openmeteo_weather_model", "surface_pressure", "hPa"),
    "openmeteo_airquality_model:pm2_5": ("openmeteo_airquality_model", "pm2_5", "ug/m3"),
    "openmeteo_airquality_model:pm10": ("openmeteo_airquality_model", "pm10", "ug/m3"),
    "openmeteo_airquality_model:ozone": ("openmeteo_airquality_model", "ozone", "ug/m3"),
    "openmeteo_airquality_model:nitrogen_dioxide": ("openmeteo_airquality_model", "nitrogen_dioxide", "ug/m3"),
    "openmeteo_airquality_model:us_aqi": ("openmeteo_airquality_model", "us_aqi", "US AQI"),
    "openaq_observed:pm25": ("openaq_observed", "pm25", "ug/m3"),
}
LIVE_LABELS = {
    "openmeteo_weather_model:temperature_2m": "Current temperature at 2 m (C) - Open-Meteo, modelled",
    "openmeteo_weather_model:relative_humidity_2m": "Current relative humidity (%) - Open-Meteo, modelled",
    "openmeteo_weather_model:precipitation": "Current precipitation (mm) - Open-Meteo, modelled",
    "openmeteo_weather_model:wind_speed_10m": "Current wind speed at 10 m (km/h) - Open-Meteo, modelled",
    "openmeteo_weather_model:surface_pressure": "Current surface pressure (hPa) - Open-Meteo, modelled",
    "openmeteo_airquality_model:pm2_5": "PM2.5 (ug/m3) - Open-Meteo air quality, MODELLED",
    "openmeteo_airquality_model:pm10": "PM10 (ug/m3) - Open-Meteo air quality, MODELLED",
    "openmeteo_airquality_model:ozone": "Ozone (ug/m3) - Open-Meteo air quality, MODELLED",
    "openmeteo_airquality_model:nitrogen_dioxide": "NO2 (ug/m3) - Open-Meteo air quality, MODELLED",
    "openmeteo_airquality_model:us_aqi": "US AQI - Open-Meteo air quality, MODELLED",
    "openaq_observed:pm25": "PM2.5 (ug/m3) - OpenAQ, OBSERVED (per monitoring location)",
}
ALERT_VARIABLES.update(LIVE_LABELS)
OPERATORS = (">", ">=", "<", "<=")
ALERT_STATUSES = ("open", "acknowledged", "cleared")
SEVERITIES = ("info", "warning", "critical")


class ValidationError(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def oid(value):
    if not isinstance(value, str) or not ObjectId.is_valid(value):
        raise ValidationError("Invalid identifier.")
    return ObjectId(value)


def clean_text(value, label, min_len, max_len):
    value = (value or "").strip()
    if not min_len <= len(value) <= max_len:
        raise ValidationError(f"{label} must be {min_len}-{max_len} characters.")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValidationError(f"{label} contains invalid characters.")
    return value


# ---------- users ----------
def validate_username(username):
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise ValidationError("Username must be 3-32 characters: lowercase letters, digits, '.', '_' or '-'.")
    return username


def validate_password(password, username=""):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValidationError("Password must be 12-128 characters.")
    if password.lower() == username.lower():
        raise ValidationError("Password must not equal the username.")


def create_user(db, username, password, role):
    username = validate_username(username)
    validate_password(password, username)
    if role not in ROLES:
        raise ValidationError("Unknown role.")
    from pymongo.errors import DuplicateKeyError
    try:
        res = db.users.insert_one({"username": username, "password_hash": security.hash_password(password),
                                   "role": role, "active": True, "failed_logins": 0, "locked_until": None,
                                   "created_at": now()})
    except DuplicateKeyError:
        raise ValidationError("Username already exists.")
    return res.inserted_id


def authenticate(db, username, password):
    """Return the user document, or None. Failures are indistinguishable to the caller."""
    username = (username or "").strip().lower()
    user = db.users.find_one({"username": username}) if USERNAME_RE.match(username) else None
    if not user:
        security.burn_time(password or "")
        return None
    locked = user.get("locked_until")
    if locked and locked.replace(tzinfo=timezone.utc) > now():
        security.burn_time(password or "")
        return None
    if not user["active"] or not security.verify_password(user["password_hash"], password or ""):
        if user["active"]:
            fails = user.get("failed_logins", 0) + 1
            update = {"failed_logins": 0, "locked_until": now() + timedelta(minutes=LOCK_MINUTES)} \
                if fails >= MAX_FAILED else {"failed_logins": fails}
            db.users.update_one({"_id": user["_id"]}, {"$set": update})
        return None
    db.users.update_one({"_id": user["_id"]}, {"$set": {"failed_logins": 0, "locked_until": None, "last_login": now()}})
    return user


def list_users(db):
    return list(db.users.find({}, {"password_hash": 0}).sort("username", 1))


def active_admins(db):
    return db.users.count_documents({"role": "Administrator", "active": True})


def update_user(db, actor_id, user_id, role=None, active=None):
    user = db.users.find_one({"_id": oid(user_id)})
    if not user:
        raise ValidationError("User not found.")
    changes = {}
    if role is not None:
        if role not in ROLES:
            raise ValidationError("Unknown role.")
        changes["role"] = role
    if active is not None:
        changes["active"] = bool(active)
    removes_admin = user["role"] == "Administrator" and user["active"] and \
        (changes.get("role", user["role"]) != "Administrator" or not changes.get("active", user["active"]))
    if removes_admin and (user["_id"] == actor_id or active_admins(db) <= 1):
        raise ValidationError("You cannot remove your own or the last active Administrator access.")
    db.users.update_one({"_id": user["_id"]}, {"$set": changes})
    if changes.get("active") is False or "role" in changes:
        db.sessions.delete_many({"user_id": user["_id"]})


def reset_password(db, user_id, password):
    user = db.users.find_one({"_id": oid(user_id)})
    if not user:
        raise ValidationError("User not found.")
    validate_password(password, user["username"])
    db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": security.hash_password(password),
                                                         "failed_logins": 0, "locked_until": None}})
    db.sessions.delete_many({"user_id": user["_id"]})


# ---------- sessions (server-side; the cookie holds only a random token, the DB stores its hash) ----------
def create_session(db, user_id, hours, minutes=None):
    token = security.new_token()
    ttl = timedelta(minutes=minutes) if minutes else timedelta(hours=hours)
    db.sessions.insert_one({"_id": security.token_hash(token), "user_id": user_id, "csrf": security.new_token(),
                            "created_at": now(), "expires_at": now() + ttl, "flash": None})
    return token


def get_session(db, token):
    if not token:
        return None
    s = db.sessions.find_one({"_id": security.token_hash(token)})
    if not s or s["expires_at"].replace(tzinfo=timezone.utc) <= now():
        return None
    return s


def delete_session(db, token):
    if token:
        db.sessions.delete_one({"_id": security.token_hash(token)})


# ---------- feedback ----------
def add_feedback(db, user, category, subject, message):
    if category not in FEEDBACK_CATEGORIES:
        raise ValidationError("Unknown category.")
    doc = {"user_id": user["_id"], "username": user["username"], "category": category,
           "subject": clean_text(subject, "Subject", 3, 120), "message": clean_text(message, "Message", 10, 2000),
           "status": "new", "created_at": now()}
    return db.feedback.insert_one(doc).inserted_id


def list_feedback(db, status=None, limit=200):
    query = {"status": status} if status in FEEDBACK_STATUSES else {}
    return list(db.feedback.find(query).sort("created_at", -1).limit(limit))


def set_feedback_status(db, feedback_id, status):
    if status not in FEEDBACK_STATUSES:
        raise ValidationError("Unknown status.")
    if db.feedback.update_one({"_id": oid(feedback_id)}, {"$set": {"status": status, "reviewed_at": now()}}).matched_count == 0:
        raise ValidationError("Feedback not found.")


# ---------- alert rules (configuration only; nothing evaluates them yet) ----------
def validate_rule(form, cities):
    try:
        threshold = float(str(form.get("threshold", "")).strip())
    except ValueError:
        raise ValidationError("Threshold must be a number.")
    if not math.isfinite(threshold) or abs(threshold) > 1e6:
        raise ValidationError("Threshold is out of range.")
    city = form.get("city_id", "")
    if city != "all" and city not in cities:
        raise ValidationError("Unknown city.")
    if form.get("variable") not in ALERT_VARIABLES:
        raise ValidationError("Unknown variable.")
    if form.get("operator") not in OPERATORS:
        raise ValidationError("Unknown operator.")
    if form.get("severity") not in SEVERITIES:
        raise ValidationError("Unknown severity.")
    return {"name": clean_text(form.get("name"), "Name", 3, 80), "city_id": city, "variable": form["variable"],
            "operator": form["operator"], "threshold": threshold, "severity": form["severity"]}


def create_rule(db, user, form, cities):
    from pymongo.errors import DuplicateKeyError
    rule = validate_rule(form, cities)
    rule.update({"enabled": True, "created_by": user["username"], "created_at": now(), "updated_at": now()})
    try:
        return db.alert_rules.insert_one(rule).inserted_id
    except DuplicateKeyError:
        raise ValidationError("A rule with this name already exists.")


def update_rule(db, rule_id, form, cities):
    from pymongo.errors import DuplicateKeyError
    changes = validate_rule(form, cities)
    changes["updated_at"] = now()
    try:
        if db.alert_rules.update_one({"_id": oid(rule_id)}, {"$set": changes}).matched_count == 0:
            raise ValidationError("Rule not found.")
    except DuplicateKeyError:
        raise ValidationError("A rule with this name already exists.")


def set_rule_enabled(db, rule_id, enabled):
    if db.alert_rules.update_one({"_id": oid(rule_id)}, {"$set": {"enabled": bool(enabled), "updated_at": now()}}).matched_count == 0:
        raise ValidationError("Rule not found.")


def list_rules(db):
    return list(db.alert_rules.find().sort("name", 1))


# ---------- alert history ----------
def list_alerts(db, status=None, limit=200):
    query = {"status": status} if status in ALERT_STATUSES else {}
    return list(db.alerts.find(query).sort("evaluated_at", -1).limit(limit))


def open_alert_count(db):
    return db.alerts.count_documents({"status": "open"})


def acknowledge_alert(db, user, alert_id):
    res = db.alerts.update_one({"_id": oid(alert_id), "status": "open"},
                               {"$set": {"status": "acknowledged", "acknowledged_by": user["username"], "acknowledged_at": now()}})
    if res.matched_count == 0:
        raise ValidationError("Alert not found or not open.")
