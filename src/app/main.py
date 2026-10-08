"""EarthScape web application (FastAPI + Jinja2). Run: uvicorn app.main:create_app --factory --app-dir src"""
import functools
import logging
import logging.handlers
import statistics
import threading
from contextlib import asynccontextmanager
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import analytics, data, db as dbmod, live, reference, security, services
from .services import ValidationError
from .settings import Settings

HERE = Path(__file__).resolve().parent
COOKIE = "earthscape_session"
log = logging.getLogger("earthscape")
MAX_DAILY_DAYS = 1830
POWER_LABEL = ("NASA POWER: gridded reanalysis (MERRA-2) plus satellite-derived radiation (CERES), derived aggregates. "
               "Not physical weather-station observations.")
SOURCES = [
    {"id": "power", "name": "NASA POWER hourly (2001-2025)", "klass": "Gridded reanalysis + satellite-derived radiation", "status": "Implemented",
     "detail": "RAW, INTERIM, daily/monthly/yearly PROCESSED layers in HDFS (MapReduce)"},
    {"id": "openmeteo_weather", "name": "Open-Meteo current weather", "klass": "Model-based, near-real-time (REST polling)", "status": "Implemented",
     "detail": "polled; latest readings in MongoDB; raw envelopes sealed daily to HDFS RAW"},
    {"id": "openaq", "name": "OpenAQ air quality (PM2.5)", "klass": "Observed (mostly low-cost sensors; sparse coverage)", "status": "Implemented",
     "detail": "polled hourly; PM2.5 only; active monitors within 25 km of each city"},
    {"id": "openmeteo_aq", "name": "Open-Meteo Air Quality", "klass": "Modelled pollutants (CAMS); never shown as observed", "status": "Implemented",
     "detail": "polled; PM2.5, PM10, CO, NO2, SO2, O3, US AQI"},
    {"id": "modis", "name": "MODIS MOD11A2 v061 land surface temperature", "klass": "Satellite-derived", "status": "Not ingested",
     "detail": "downloader and HDF4 reader are built; no data because NASA Earthdata credentials are not configured"},
]
NAV = [  # (group label, [(key, label, href, role or None)])
    ("Climate", [("overview", "Overview", "/overview", None), ("history", "Historical Climate", "/history", None),
                 ("compare", "City Comparison", "/compare", None), ("trends", "Trends & Extremes", "/trends", None),
                 ("correlation", "Correlation", "/correlation", None), ("forecast", "Forecast", "/forecast", None)]),
    ("Live", [("current", "Current Data", "/current", None), ("alerts", "Alert Rules", "/alerts", None),
              ("alert_history", "Alert History", "/alerts/history", None)]),
    ("System", [("sources", "Data Sources", "/sources", None), ("feedback", "Feedback", "/feedback", None),
                ("admin_feedback", "Feedback Review", "/admin/feedback", "Administrator"),
                ("users", "User Management", "/admin/users", "Administrator")]),
]


def create_app(settings=None, db=None, store=None):
    settings = settings or Settings.from_env()
    if settings.log_file:   # rotating file log; messages carry error types and paths only, never credentials or request bodies
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(settings.log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger("earthscape").addHandler(handler)
        logging.getLogger("earthscape").setLevel(logging.INFO)
    @asynccontextmanager
    async def lifespan(_):
        try:
            dbmod.ensure_indexes(app.state.db)
        except PyMongoError as e:
            log.error("MongoDB unavailable at startup (%s); pages that need it will return 503", type(e).__name__)
        if settings.poll_enabled:
            app.state.poller = live.Poller(app.state.db, settings.poll_minutes, settings.openaq_minutes)
            app.state.poller.start()
        yield
        if app.state.poller:
            app.state.poller.stop()

    app = FastAPI(title="EarthScape", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db if db is not None else dbmod.connect(settings)
    app.state.store = store if store is not None else data.Store(settings.cache_dir)
    app.state.health_cache = (0.0, None)
    app.state.poller = None
    app.state.poll_lock = threading.Lock()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.update({
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "Cache-Control": "no-store" if not request.url.path.startswith("/static") else "public, max-age=300",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
                                       "style-src 'self' https://cdn.jsdelivr.net; font-src 'self' https://cdn.jsdelivr.net; img-src 'self' data:; frame-ancestors 'none'"})
        return resp

    # ---------- errors ----------
    def wants_json(request):
        return request.url.path.startswith("/api/") or request.url.path == "/health"

    def error_response(request, status, message):
        if wants_json(request):
            return JSONResponse({"error": message}, status_code=status)
        ctx = load_ctx(request) if status != 503 else SimpleNamespace(user=None, session=None)
        return page(request, ctx, "error.html", {"status": status, "message": message}, status=status)

    @app.exception_handler(StarletteHTTPException)   # also catches the router's own 404/405
    async def http_error(request, exc):
        if exc.status_code == 401 and not wants_json(request):
            return RedirectResponse("/login", status_code=303)
        msg = {401: "Authentication required.", 403: "You do not have permission to do this.", 404: "Page not found."}
        return error_response(request, exc.status_code, msg.get(exc.status_code, str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def bad_input(request, exc):
        return error_response(request, 400, "Invalid request parameters.")

    @app.exception_handler(PyMongoError)
    async def mongo_down(request, exc):
        log.error("MongoDB error: %s", type(exc).__name__)
        return error_response(request, 503, "The database is temporarily unavailable.")

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        log.error("Unhandled error on %s: %s", request.url.path, type(exc).__name__)
        return error_response(request, 500, "Something went wrong.")

    # ---------- session / auth helpers ----------
    def load_ctx(request):
        token = request.cookies.get(COOKIE)
        try:
            session = services.get_session(app.state.db, token)
            user = None
            if session and session["user_id"]:
                user = app.state.db.users.find_one({"_id": session["user_id"], "active": True}, {"password_hash": 0})
        except PyMongoError:
            return SimpleNamespace(session=None, user=None, token=None)
        return SimpleNamespace(session=session, user=user, token=token)

    def need_user(request: Request):
        ctx = load_ctx(request)
        if not ctx.user:
            raise HTTPException(401)
        return ctx

    def need_admin(ctx=Depends(need_user)):
        if ctx.user["role"] != "Administrator":
            raise HTTPException(403)
        return ctx

    def check_csrf(ctx, token):
        if not ctx.session or not security.same(ctx.session["csrf"], token):
            raise HTTPException(403, "Invalid or missing CSRF token.")

    def flash(ctx, kind, text):
        if ctx.session:
            app.state.db.sessions.update_one({"_id": ctx.session["_id"]}, {"$set": {"flash": {"kind": kind, "text": text}}})

    def go(path):
        return RedirectResponse(path, status_code=303)

    def page(request, ctx, name, extra=None, status=200):
        message = None
        if ctx.session and ctx.session.get("flash"):
            message = ctx.session["flash"]
            try:
                app.state.db.sessions.update_one({"_id": ctx.session["_id"]}, {"$set": {"flash": None}})
            except PyMongoError:
                pass
        nav = [(g, [i for i in items if i[3] is None or (ctx.user and ctx.user["role"] == i[3])]) for g, items in NAV]
        open_alerts = 0
        if ctx.user:
            try:
                open_alerts = services.open_alert_count(app.state.db)
            except PyMongoError:
                pass
        context = {"user": ctx.user, "csrf": ctx.session["csrf"] if ctx.session else "", "flash": message, "nav": nav,
                   "active": name.split(".")[0], "power_label": POWER_LABEL, "store": app.state.store,
                   "open_alerts": open_alerts, **(extra or {})}
        return templates.TemplateResponse(request, name, context, status_code=status)

    def parse_date(value, label):
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{label} must be a date (YYYY-MM-DD).")

    def need_data():
        if not app.state.store.ready:
            raise HTTPException(503, app.state.store.error or "Historical data is not available.")
        return app.state.store

    def pick_city(store, city):
        city = city or store.cities()[0]
        if city not in store.cities():
            raise ValidationError("Unknown city.")
        return city

    # ---------- auth ----------
    @app.get("/")
    def root(request: Request):
        return go("/overview")

    @app.get("/login")
    def login_form(request: Request):
        ctx = load_ctx(request)
        if ctx.user:
            return go("/overview")
        resp_token = None
        if not ctx.session:
            resp_token = services.create_session(app.state.db, None, 0, minutes=30)
            ctx = SimpleNamespace(user=None, session=services.get_session(app.state.db, resp_token), token=resp_token)
        resp = page(request, ctx, "login.html", {"error": None})
        if resp_token:
            resp.set_cookie(COOKIE, resp_token, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=1800)
        return resp

    @app.post("/login")
    def login(request: Request, username: str = Form(""), password: str = Form(""), csrf_token: str = Form("")):
        ctx = load_ctx(request)
        check_csrf(ctx, csrf_token)
        user = services.authenticate(app.state.db, username[:64], password[:256])
        if not user:
            return page(request, ctx, "login.html", {"error": "Invalid username or password."}, status=401)
        services.delete_session(app.state.db, ctx.token)   # new session id at login (no fixation)
        token = services.create_session(app.state.db, user["_id"], settings.session_hours)
        resp = go("/overview")
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.session_hours * 3600)
        return resp

    @app.post("/logout")
    def logout(request: Request, csrf_token: str = Form(""), ctx=Depends(need_user)):
        check_csrf(ctx, csrf_token)
        services.delete_session(app.state.db, ctx.token)
        resp = go("/login")
        resp.delete_cookie(COOKIE)
        return resp

    # ---------- dashboard pages ----------
    @app.get("/overview")
    def overview(request: Request, city: str = "", ctx=Depends(need_user)):
        store = app.state.store
        if not store.ready:
            return page(request, ctx, "overview.html", {"o": None})
        try:
            city = pick_city(store, city)
        except ValidationError as e:
            raise HTTPException(400, str(e))
        return page(request, ctx, "overview.html", {"o": store.overview(city), "city": city, "cities": store.cities(),
                                                    "names": data.CITY_NAMES})

    @app.get("/history")
    def history(request: Request, city: str = "", start: str = "", end: str = "", granularity: str = "daily",
                ctx=Depends(need_user)):
        store = app.state.store
        if not store.ready:
            return page(request, ctx, "history.html", {"ready": False})
        error = None
        first, last = store.date_range()
        try:
            city = pick_city(store, city)
            if granularity not in ("daily", "monthly"):
                raise ValidationError("Unknown granularity.")
            e = parse_date(end or last, "End date")
            s = parse_date(start or (e - timedelta(days=364)).isoformat(), "Start date")
            if s > e:
                raise ValidationError("Start date must not be after the end date.")
            if granularity == "daily" and (e - s).days >= MAX_DAILY_DAYS:
                raise ValidationError(f"Daily view is limited to {MAX_DAILY_DAYS} days; choose a shorter range or monthly.")
        except ValidationError as ex:
            error, city, s, e = str(ex), store.cities()[0], date.fromisoformat(last) - timedelta(days=364), date.fromisoformat(last)
            granularity = "daily"
        return page(request, ctx, "history.html", {"ready": True, "error": error, "city": city, "cities": store.cities(),
                                                   "names": data.CITY_NAMES, "start": s.isoformat(), "end": e.isoformat(),
                                                   "granularity": granularity, "first": first, "last": last})

    @app.get("/compare")
    def compare(request: Request, start_year: int = 2001, end_year: int = 2025, ctx=Depends(need_user)):
        store = app.state.store
        if not store.ready:
            return page(request, ctx, "compare.html", {"ready": False})
        if not 2001 <= start_year <= end_year <= 2025:
            raise HTTPException(400, "Years must satisfy 2001 <= start <= end <= 2025.")
        series = store.compare(start_year, end_year)
        mean = lambda xs: round(sum(v for v in xs if v is not None) / len([v for v in xs if v is not None]), 2) \
            if any(v is not None for v in xs) else None
        rows = [{"name": data.CITY_NAMES[c], "temp": mean(s["temperature_c"]), "hum": mean(s["humidity_pct"]),
                 "wind": mean(s["wind_m_s"]), "rain": mean(s["precipitation_mm"])} for c, s in series.items()]
        return page(request, ctx, "compare.html", {"ready": True, "start_year": start_year, "end_year": end_year, "rows": rows})

    @app.get("/sources")
    def sources(request: Request, ctx=Depends(need_user)):
        store = app.state.store
        return page(request, ctx, "sources.html", {"sources": SOURCES, "meta": store.meta, "data_error": store.error})

    # ---------- JSON API (read-only, authenticated) ----------
    def api_guard(fn):
        @functools.wraps(fn)
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except ValidationError as e:
                raise HTTPException(400, str(e))
        return wrapper

    @app.get("/api/series")
    @api_guard
    def api_series(city: str = "", start: str = "", end: str = "", granularity: str = "daily", ctx=Depends(need_user)):
        store = need_data()
        city = pick_city(store, city)
        if granularity not in ("daily", "monthly"):
            raise ValidationError("Unknown granularity.")
        first, last = store.date_range()
        e = parse_date(end or last, "End date")
        s = parse_date(start or (e - timedelta(days=364)).isoformat(), "Start date")
        if s > e:
            raise ValidationError("Start date must not be after the end date.")
        if granularity == "daily":
            if (e - s).days >= MAX_DAILY_DAYS:
                raise ValidationError(f"Daily view is limited to {MAX_DAILY_DAYS} days.")
            w = store.window("daily", city, s.isoformat(), e.isoformat())
        else:
            w = store.window("monthly", city, s.isoformat()[:7], e.isoformat()[:7])
        return {"city": city, "granularity": granularity, "labels": w["keys"],
                "temperature_mean_c": w["temperature_2m_mean_c"], "temperature_min_c": w["temperature_2m_min_c"],
                "temperature_max_c": w["temperature_2m_max_c"], "humidity_pct": w["relative_humidity_2m_mean_pct"],
                "precipitation_mm": w["precipitation_total_mm"], "wind_m_s": w["wind_speed_2m_mean_m_s"],
                "precipitation_unit": "mm/day" if granularity == "daily" else "mm/month",
                "source": POWER_LABEL, "derived_from": store.meta[granularity]["sha256"]}

    @app.get("/api/overview")
    @api_guard
    def api_overview(city: str = "", ctx=Depends(need_user)):
        store = need_data()
        return {**store.overview(pick_city(store, city)), "source": POWER_LABEL}

    @app.get("/api/compare")
    @api_guard
    def api_compare(start_year: int = 2001, end_year: int = 2025, ctx=Depends(need_user)):
        store = need_data()
        if not 2001 <= start_year <= end_year <= 2025:
            raise ValidationError("Years must satisfy 2001 <= start <= end <= 2025.")
        return {"cities": {data.CITY_NAMES[c]: s for c, s in store.compare(start_year, end_year).items()}, "source": POWER_LABEL}

    @app.post("/admin/data/refresh")
    def refresh_data(request: Request, csrf_token: str = Form(""), ctx=Depends(need_admin)):
        check_csrf(ctx, csrf_token)
        try:
            app.state.store.refresh()
            flash(ctx, "success", "Historical data cache refreshed from HDFS and verified against its manifests.")
        except data.DataError as e:
            flash(ctx, "danger", f"Refresh failed; the previous cache is unchanged. ({e})")
        return go("/sources")

    # ---------- alerts (configuration only) ----------
    @app.get("/alerts")
    def alerts(request: Request, edit: str = "", ctx=Depends(need_user)):
        rules = services.list_rules(app.state.db)
        editing = next((r for r in rules if str(r["_id"]) == edit), None)
        return page(request, ctx, "alerts.html", {"rules": rules, "editing": editing, "variables": services.ALERT_VARIABLES,
                                                  "operators": services.OPERATORS, "severities": services.SEVERITIES, "live_vars": services.LIVE_VARIABLES,
                                                  "cities": app.state.store.cities() or list(data.CITY_NAMES), "names": data.CITY_NAMES})

    def rule_form(name, city_id, variable, operator, threshold, severity):
        return {"name": name, "city_id": city_id, "variable": variable, "operator": operator, "threshold": threshold, "severity": severity}

    @app.post("/alerts")
    def alert_create(name: str = Form(""), city_id: str = Form(""), variable: str = Form(""), operator: str = Form(""),
                     threshold: str = Form(""), severity: str = Form(""), csrf_token: str = Form(""), ctx=Depends(need_user)):
        check_csrf(ctx, csrf_token)
        try:
            services.create_rule(app.state.db, ctx.user, rule_form(name, city_id, variable, operator, threshold, severity),
                                 list(data.CITY_NAMES))
            flash(ctx, "success", "Rule saved. Live-variable rules are evaluated after every poll; rules on historical variables are stored only.")
        except ValidationError as e:
            flash(ctx, "danger", str(e))
        return go("/alerts")

    @app.post("/alerts/{rule_id}/edit")
    def alert_edit(rule_id: str, name: str = Form(""), city_id: str = Form(""), variable: str = Form(""),
                   operator: str = Form(""), threshold: str = Form(""), severity: str = Form(""), csrf_token: str = Form(""),
                   ctx=Depends(need_user)):
        check_csrf(ctx, csrf_token)
        try:
            services.update_rule(app.state.db, rule_id, rule_form(name, city_id, variable, operator, threshold, severity),
                                 list(data.CITY_NAMES))
            flash(ctx, "success", "Rule updated.")
        except ValidationError as e:
            flash(ctx, "danger", str(e))
        return go("/alerts")

    @app.post("/alerts/{rule_id}/toggle")
    def alert_toggle(rule_id: str, enabled: str = Form(""), csrf_token: str = Form(""), ctx=Depends(need_user)):
        check_csrf(ctx, csrf_token)
        try:
            services.set_rule_enabled(app.state.db, rule_id, enabled == "1")
            flash(ctx, "success", "Rule enabled." if enabled == "1" else "Rule disabled.")
        except ValidationError as e:
            flash(ctx, "danger", str(e))
        return go("/alerts")

    # ---------- feedback ----------
    @app.get("/feedback")
    def feedback_form(request: Request, ctx=Depends(need_user)):
        return page(request, ctx, "feedback.html", {"categories": services.FEEDBACK_CATEGORIES})

    @app.post("/feedback")
    def feedback_submit(category: str = Form(""), subject: str = Form(""), message: str = Form(""),
                        csrf_token: str = Form(""), ctx=Depends(need_user)):
        check_csrf(ctx, csrf_token)
        try:
            services.add_feedback(app.state.db, ctx.user, category, subject, message)
            flash(ctx, "success", "Thank you. Your message was submitted to the Administrators.")
        except ValidationError as e:
            flash(ctx, "danger", str(e))
        return go("/feedback")

    @app.get("/admin/feedback")
    def feedback_admin(request: Request, status: str = "", ctx=Depends(need_admin)):
        return page(request, ctx, "feedback_admin.html", {"items": services.list_feedback(app.state.db, status),
                                                          "status": status, "statuses": services.FEEDBACK_STATUSES})

    @app.post("/admin/feedback/{feedback_id}/status")
    def feedback_status(feedback_id: str, status: str = Form(""), csrf_token: str = Form(""), ctx=Depends(need_admin)):
        check_csrf(ctx, csrf_token)
        try:
            services.set_feedback_status(app.state.db, feedback_id, status)
        except ValidationError as e:
            flash(ctx, "danger", str(e))
        return go("/admin/feedback")

    # ---------- user management (Administrator only) ----------
    @app.get("/admin/users")
    def users(request: Request, ctx=Depends(need_admin)):
        return page(request, ctx, "users.html", {"users": services.list_users(app.state.db), "roles": services.ROLES})

    def user_action(ctx, csrf_token, fn, ok):
        check_csrf(ctx, csrf_token)
        try:
            fn()
            flash(ctx, "success", ok)
        except ValidationError as e:
            flash(ctx, "danger", str(e))
        return go("/admin/users")

    @app.post("/admin/users")
    def user_create(username: str = Form(""), password: str = Form(""), role: str = Form(""), csrf_token: str = Form(""),
                    ctx=Depends(need_admin)):
        return user_action(ctx, csrf_token, lambda: services.create_user(app.state.db, username, password, role), "User created.")

    @app.post("/admin/users/{user_id}/role")
    def user_role(user_id: str, role: str = Form(""), csrf_token: str = Form(""), ctx=Depends(need_admin)):
        return user_action(ctx, csrf_token, lambda: services.update_user(app.state.db, ctx.user["_id"], user_id, role=role), "Role updated.")

    @app.post("/admin/users/{user_id}/active")
    def user_active(user_id: str, active: str = Form(""), csrf_token: str = Form(""), ctx=Depends(need_admin)):
        return user_action(ctx, csrf_token, lambda: services.update_user(app.state.db, ctx.user["_id"], user_id, active=active == "1"),
                           "User updated.")

    @app.post("/admin/users/{user_id}/password")
    def user_password(user_id: str, password: str = Form(""), csrf_token: str = Form(""), ctx=Depends(need_admin)):
        return user_action(ctx, csrf_token, lambda: services.reset_password(app.state.db, user_id, password),
                           "Password reset; the user's sessions were ended.")

    # ---------- current data ----------
    def jsonable(doc):
        if isinstance(doc, dict):
            return {k: jsonable(v) for k, v in doc.items() if k != "_id"}
        if isinstance(doc, list):
            return [jsonable(v) for v in doc]
        if isinstance(doc, datetime):
            return doc.isoformat(timespec="seconds") + ("Z" if doc.tzinfo is None else "")
        return doc

    @app.get("/current")
    def current_page(request: Request, ctx=Depends(need_user)):
        return page(request, ctx, "current.html", {"latest": live.latest_by_city(app.state.db), "names": data.CITY_NAMES,
                                                   "fresh": live.freshness(app.state.db), "cities": list(data.CITY_NAMES),
                                                   "polling": app.state.poller is not None})

    @app.get("/api/current")
    @api_guard
    def api_current(ctx=Depends(need_user)):
        return {"cities": jsonable(live.latest_by_city(app.state.db)), "freshness": jsonable(live.freshness(app.state.db)),
                "note": "Open-Meteo values are modelled; OpenAQ values are observed by third-party monitors."}

    @app.get("/api/current/history")
    @api_guard
    def api_current_history(city: str = "", hours: int = 72, ctx=Depends(need_user)):
        if city not in data.CITY_NAMES:
            raise ValidationError("Unknown city.")
        if not 1 <= hours <= 720:
            raise ValidationError("hours must be 1-720.")
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = lambda source: list(app.state.db.latest_readings.find(
            {"source": source, "city_id": city, "observed_at": {"$gte": since}}).sort("observed_at", 1))
        weather, aq, oaq = rows(live.src.WEATHER), rows(live.src.AIRQUALITY), rows(live.src.OPENAQ)
        by_time = {}
        for r in oaq:
            by_time.setdefault(r["observed_at"], []).append(r["values"]["pm25"])
        stamp = lambda r: jsonable(r["observed_at"])
        return {"city": city, "hours": hours,
                "weather": {"labels": [stamp(r) for r in weather], "temperature_2m": [r["values"].get("temperature_2m") for r in weather]},
                "air_quality_modelled": {"labels": [stamp(r) for r in aq], "pm2_5": [r["values"].get("pm2_5") for r in aq]},
                "openaq_observed": {"labels": [jsonable(t) for t in by_time], "median_pm25": [statistics.median(v) for v in by_time.values()],
                                    "stations": [len(v) for v in by_time.values()]}}

    @app.get("/api/current/comparison")
    @api_guard
    def api_current_comparison(city: str = "", hours: int = 24, ctx=Depends(need_user)):
        if city not in data.CITY_NAMES:
            raise ValidationError("Unknown city.")
        if not 1 <= hours <= 72:
            raise ValidationError("hours must be 1-72.")
        ref = reference.load(settings.cache_dir)
        if not ref:
            raise HTTPException(503, "The historical reference has not been built yet. An Administrator must run build-reference.")
        since = live.now() - timedelta(hours=hours)
        docs = list(app.state.db.latest_readings.find({"source": live.src.WEATHER, "city_id": city, "observed_at": {"$gte": since}}))
        for d in docs:
            d["observed_at"] = live.aware(d["observed_at"])
        res = reference.compare_city(ref, city, docs, live.now(), int(live.STALE_AFTER[live.src.WEATHER].total_seconds() // 60))
        provider = docs[-1]["provider"] if docs else {}
        res["current_source"] = {"name": "Open-Meteo current weather (modelled)", "grid_latitude": provider.get("grid_latitude"),
                                 "grid_longitude": provider.get("grid_longitude"), "elevation_m": provider.get("elevation_m")}
        res["reference_source"] = "NASA POWER hourly (MERRA-2 reanalysis), UTC, built " + ref["built_at"]
        return res

    @app.post("/admin/poll")
    def poll_now(csrf_token: str = Form(""), ctx=Depends(need_admin)):
        check_csrf(ctx, csrf_token)
        if app.state.poll_lock.acquire(blocking=False):
            def work():
                try:
                    live.poll_cycle(app.state.db)
                except Exception as e:   # background work must not take the app down
                    log.error("manual poll failed: %s", type(e).__name__)
                finally:
                    app.state.poll_lock.release()
            threading.Thread(target=work, daemon=True).start()
            flash(ctx, "success", "Polling started in the background (about two minutes). Reload this page to see new readings.")
        else:
            flash(ctx, "warning", "A poll is already running.")
        return go("/current")

    # ---------- alert history ----------
    @app.get("/alerts/history")
    def alert_history(request: Request, status: str = "", ctx=Depends(need_user)):
        return page(request, ctx, "alert_history.html", {"alerts": services.list_alerts(app.state.db, status), "status": status,
                                                         "statuses": services.ALERT_STATUSES, "names": data.CITY_NAMES,
                                                         "labels": services.ALERT_VARIABLES,
                                                         "evaluation": app.state.db.ingest_status.find_one({"_id": "alert_evaluation"})})

    @app.post("/alerts/history/{alert_id}/ack")
    def alert_ack(alert_id: str, csrf_token: str = Form(""), ctx=Depends(need_user)):
        check_csrf(ctx, csrf_token)
        try:
            services.acknowledge_alert(app.state.db, ctx.user, alert_id)
            flash(ctx, "success", "Alert acknowledged.")
        except ValidationError as e:
            flash(ctx, "danger", str(e))
        return go("/alerts/history")

    # ---------- analytics (trained on the verified POWER data) ----------
    def ml_context(city):
        run = analytics.latest(app.state.db)
        cities = list(data.CITY_NAMES)
        if city and city not in cities:
            raise HTTPException(400, "Unknown city.")
        return run, (city or cities[0]), cities

    @app.get("/trends")
    def trends(request: Request, city: str = "", ctx=Depends(need_user)):
        run, city, cities = ml_context(city)
        payload = None
        if run:
            t = run["trend"][city]
            for v in t.values():   # Theil-Sen line for the chart
                med = statistics.median(y - v["slope_per_decade"] / 10 * x for x, y in zip(v["years"], v["values"]))
                v["fit"] = [round(med + v["slope_per_decade"] / 10 * x, 3) for x in v["years"]]
            payload = {"trend": t, "anomalies": run["anomalies"][city], "extremes": (app.state.store.extremes() or {}).get(city)}
        return page(request, ctx, "trends.html", {"run": run, "city": city, "cities": cities, "names": data.CITY_NAMES, "payload": payload})

    @app.get("/forecast")
    def forecast(request: Request, city: str = "", variable: str = "temperature", ctx=Depends(need_user)):
        run, city, cities = ml_context(city)
        if variable not in ("temperature", "humidity", "precipitation", "wind"):
            raise HTTPException(400, "Unknown variable.")
        return page(request, ctx, "forecast.html", {"run": run, "city": city, "cities": cities, "names": data.CITY_NAMES, "variable": variable,
                                                    "fc": run["forecast"][city][variable] if run else None})

    @app.get("/correlation")
    def correlation(request: Request, city: str = "", ctx=Depends(need_user)):
        run, city, cities = ml_context(city)
        return page(request, ctx, "correlation.html", {"run": run, "city": city, "cities": cities, "names": data.CITY_NAMES,
                                                       "corr": run["correlation"][city] if run else None})

    @app.get("/api/ml/summary")
    @api_guard
    def api_ml(ctx=Depends(need_user)):
        run = analytics.latest(app.state.db)
        if not run:
            raise HTTPException(503, "No analytics run yet. An Administrator must run train-ml.")
        return jsonable({k: run[k] for k in ("model_version", "created_at", "seed", "data_labels", "trend", "anomalies", "correlation", "forecast")})

    @app.post("/admin/ml/train")
    def ml_train(csrf_token: str = Form(""), ctx=Depends(need_admin)):
        check_csrf(ctx, csrf_token)
        if not app.state.store.ready:
            flash(ctx, "danger", "Refresh the historical data cache first.")
        else:
            try:
                res = analytics.train(app.state.db, settings.cache_dir)
                flash(ctx, "success", f"Analytics retrained ({res['model_version']}).")
            except Exception as e:
                log.error("training failed: %s", type(e).__name__)
                flash(ctx, "danger", "Training failed; see the server log.")
        return go("/trends")

    # ---------- health ----------
    @app.get("/health")
    def health():
        """App health is always 'ok' while this answers; dependencies are reported separately."""
        out = {"app": "ok"}
        try:
            app.state.db.command("ping")
            out["mongodb"] = "ok"
        except PyMongoError:
            out["mongodb"] = "unavailable"
        checked, value = app.state.health_cache
        if time.time() - checked > 30:
            try:
                data.hdfs("-test", "-e", f"{data.HDFS_ROOT}/processed/power_daily", timeout=15)
                value = "ok"
            except data.DataError:
                value = "unavailable"
            app.state.health_cache = (time.time(), value)
        out["hdfs"] = value
        store = app.state.store
        out["data_cache"] = {"status": "ok" if store.ready else "missing", "refreshed_at": store.meta["refreshed_at"] if store.ready else None}
        out["poller"] = "running" if app.state.poller and app.state.poller.is_alive() else "not running"
        if out["mongodb"] == "ok":
            try:
                out["live_sources"] = {r["source"]: "stale" if r["stale"] else "fresh" for r in live.freshness(app.state.db)}
            except PyMongoError:
                pass
        return out

    return app
