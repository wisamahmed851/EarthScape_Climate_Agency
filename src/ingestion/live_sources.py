"""Fetch and normalise the current-data providers. No storage here (see src/app/live.py)"""
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CITIES = json.loads((ROOT / "config" / "cities.json").read_text(encoding="utf-8"))
WEATHER = "openmeteo_weather_model"
AIRQUALITY = "openmeteo_airquality_model"
OPENAQ = "openaq_observed"
WEATHER_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "wind_speed_10m", "surface_pressure"]
AQ_VARS = ["pm2_5", "pm10", "carbon_monoxide", "nitrogen_dioxide", "sulphur_dioxide", "ozone", "us_aqi"]
URLS = {WEATHER: "https://api.open-meteo.com/v1/forecast", AIRQUALITY: "https://air-quality-api.open-meteo.com/v1/air-quality"}
OPENAQ_URL = "https://api.openaq.org/v3"
OPENAQ_RADIUS_M = 25000
OPENAQ_ACTIVE_DAYS = 7
OPENAQ_MAX_LOCATIONS = 20
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def get_json(url, headers=None, attempts=4, pause=1.0):
    """GET with bounded retries on timeouts, connection errors, 429 and 5xx. Returns parsed JSON"""
    last = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EarthScape-academic/1.0", **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in RETRY_STATUS:
                break
            wait = int(e.headers.get("Retry-After", 0) or 0) if e.code == 429 else 0
            time.sleep(min(max(wait, pause * 2 ** n), 30))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last = type(e).__name__
            time.sleep(pause * 2 ** n)
    raise FetchError(last or "request failed")


def iso(ts):
    """Provider timestamps are UTC ('timezone=GMT' requested, OpenAQ gives Z); returns aware datetime"""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)


def fetch_openmeteo(source, city):
    c = CITIES[city]
    variables = WEATHER_VARS if source == WEATHER else AQ_VARS
    url = URLS[source] + "?" + urllib.parse.urlencode({"latitude": c["latitude"], "longitude": c["longitude"],
                                                       "current": ",".join(variables), "timezone": "GMT"})
    return {"retrieved_at": now().isoformat(timespec="seconds"), "request": {"url": url, "city": city}, "response": get_json(url)}


def normalise_openmeteo(source, city, envelope):
    r = envelope["response"]
    cur, units = r["current"], r["current_units"]
    variables = WEATHER_VARS if source == WEATHER else AQ_VARS
    values = {v: cur[v] for v in variables if cur.get(v) is not None}
    if not values or "time" not in cur:
        raise FetchError("provider response has no current values")
    return {"source": source, "value_origin": "modelled", "city_id": city, "station_key": "",
            "observed_at": iso(cur["time"]),
            "retrieved_at": iso(envelope["retrieved_at"]), "values": values, "units": {v: units[v] for v in values},
            "provider": {"name": "Open-Meteo", "interval_s": cur.get("interval"), "grid_latitude": r.get("latitude"),
                         "grid_longitude": r.get("longitude"), "elevation_m": r.get("elevation"),
                         "model_note": "weather-model output" if source == WEATHER else "CAMS atmospheric-composition model output"}}


def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def openaq_headers():
    key = os.environ.get("OPENAQ_API_KEY", "").strip()
    if not key:
        raise FetchError("OPENAQ_API_KEY is not set")
    return {"X-API-Key": key}


def fetch_openaq(city, throttle=1.1):
    """Return (envelopes, readings): raw provider responses and normalised PM2.5 readings for active locations"""
    c = CITIES[city]
    headers = openaq_headers()
    query = urllib.parse.urlencode({"coordinates": f"{c['latitude']},{c['longitude']}", "radius": OPENAQ_RADIUS_M,
                                    "limit": 1000, "parameters_id": 2})
    url = f"{OPENAQ_URL}/locations?{query}"
    catalogue = {"retrieved_at": now().isoformat(timespec="seconds"), "request": {"url": url, "city": city}, "response": get_json(url, headers)}
    cutoff = now() - timedelta(days=OPENAQ_ACTIVE_DAYS)
    active = []
    for loc in catalogue["response"]["results"]:
        last = (loc.get("datetimeLast") or {}).get("utc")
        if last and iso(last) >= cutoff:
            active.append(loc)
    active.sort(key=lambda l: km(c["latitude"], c["longitude"], l["coordinates"]["latitude"], l["coordinates"]["longitude"]))
    envelopes, readings = [catalogue], []
    for loc in active[:OPENAQ_MAX_LOCATIONS]:
        time.sleep(throttle)
        url = f"{OPENAQ_URL}/locations/{loc['id']}/latest"
        env = {"retrieved_at": now().isoformat(timespec="seconds"), "request": {"url": url, "city": city, "location_id": loc["id"]},
               "response": get_json(url, headers)}
        envelopes.append(env)
        readings += normalise_openaq(city, loc, env)
    return envelopes, readings


def normalise_openaq(city, loc, envelope):
    """PM2.5 readings only, from sensors the location catalogue says measure pm25. Nothing is inferred or filled"""
    c = CITIES[city]
    pm25 = {s["id"]: s for s in loc["sensors"] if s["parameter"]["name"] == "pm25"}
    dist = km(c["latitude"], c["longitude"], loc["coordinates"]["latitude"], loc["coordinates"]["longitude"])
    out = []
    for r in envelope["response"].get("results", []):
        s = pm25.get(r.get("sensorsId"))
        if not s or r.get("value") is None:
            continue
        out.append({"source": OPENAQ, "value_origin": "observed", "city_id": city, "station_key": f"{loc['id']}:{s['id']}",
                    "observed_at": iso(r["datetime"]["utc"]), "retrieved_at": iso(envelope["retrieved_at"]),
                    "values": {"pm25": r["value"]}, "units": {"pm25": s["parameter"]["units"]},
                    "provider": {"name": "OpenAQ", "location_id": loc["id"], "location_name": loc.get("name"), "sensor_id": s["id"],
                                 "data_provider": (loc.get("provider") or {}).get("name"), "owner": (loc.get("owner") or {}).get("name"),
                                 "is_reference_monitor": bool(loc.get("isMonitor")), "is_mobile": bool(loc.get("isMobile")),
                                 "sensor_class": "reference monitor" if loc.get("isMonitor") else "low-cost / community sensor",
                                 "distance_km": round(dist, 1), "latitude": loc["coordinates"]["latitude"],
                                 "longitude": loc["coordinates"]["longitude"], "locations_last_seen_utc": (loc.get("datetimeLast") or {}).get("utc")}})
    return out
