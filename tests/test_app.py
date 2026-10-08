import hashlib
import json
import re
import sys
import tempfile
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fastapi.testclient import TestClient  # noqa: E402
from pymongo import MongoClient  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from app import data, db as dbmod, security, services  # noqa: E402
from app.main import create_app  # noqa: E402
from app.settings import Settings  # noqa: E402

PW = "correct-horse-battery"
try:
    _c = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=1500)
    _c.admin.command("ping")
    MONGO = True
    _c.close()
except PyMongoError:
    MONGO = False


def fixture_files():
    """Small synthetic PROCESSED-style files for tests only (never shown by the real app)."""
    daily = [data.DAILY_HEADER]
    for city in ("karachi", "lahore"):
        for i in range(40):
            d = date(2001, 1, 1) + timedelta(days=i)
            daily.append(f"{city},{d},24,{10 + i % 5}.5000,5.0,20.0,50.0000,{'' if i == 3 else '1.2'},2.0000,100.0000,0.5000,0,0,{24 if i == 3 else 0},0,0,0")
    monthly = [data.MONTHLY_HEADER]
    for city in ("karachi", "lahore"):
        for y in (2001, 2002):
            for m in range(1, 13):
                monthly.append(f"{city},{y}-{m:02d},28,672,{15 + m}.0000,5.0,30.0,55.0000,3.5,2.5000,100.0000,0.6000,0,0,0,0,0,0")
    return "\n".join(daily) + "\n", "\n".join(monthly) + "\n"


def write_cache(cache_dir, daily=None, monthly=None):
    d, m = fixture_files()
    daily, monthly = daily or d, monthly or m
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "daily.csv").write_text(daily, encoding="utf-8", newline="")
    (cache_dir / "monthly.csv").write_text(monthly, encoding="utf-8", newline="")
    meta = {"refreshed_at": "2026-01-01T00:00:00+00:00", "manifests": {k: {"objects": 1, "ok": 1, "failed": 0, "latest": "x"} for k in data.MANIFESTS}}
    for kind, text in (("daily", daily), ("monthly", monthly)):
        meta[kind] = {"sha256": hashlib.sha256(text.encode()).hexdigest(), "size_bytes": len(text.encode()), "record_count": text.count("\n") - 1,
                      "application_id": "application_1_1", "yarn_final_state": "SUCCEEDED", "job_id": "job_1_1",
                      "submitted_at": "t", "version": "v", "hdfs_path": "/x"}
    (cache_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


class PasswordTest(unittest.TestCase):
    def test_hash_and_verify(self):
        h = security.hash_password(PW)
        self.assertNotIn(PW, h)
        self.assertTrue(h.startswith("$argon2id$"))
        self.assertTrue(security.verify_password(h, PW))
        self.assertFalse(security.verify_password(h, PW + "x"))
        self.assertFalse(security.verify_password("not-a-hash", PW))
        self.assertNotEqual(h, security.hash_password(PW))   # salted

    def test_policy(self):
        for bad in ("short", "x" * 129, None, "adminadminadmin"):
            with self.assertRaises(services.ValidationError):
                services.validate_password(bad, "adminadminadmin")
        services.validate_password(PW, "admin")

    def test_username_policy(self):
        for bad in ("ab", "Has Space", "x" * 33, "-bad", "a;b"):
            with self.assertRaises(services.ValidationError):
                services.validate_username(bad)
        self.assertEqual(services.validate_username(" Alice_1 "), "alice_1")

    def test_csrf_compare(self):
        self.assertTrue(security.same("abc", "abc"))
        self.assertFalse(security.same("abc", ""))
        self.assertFalse(security.same("abc", "abd"))


@unittest.skipUnless(MONGO, "local MongoDB is not running")
class AppTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_db = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
        cls.dbname = "earthscape_test_" + uuid.uuid4().hex[:8]
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cache = Path(cls.tmp.name) / "cache"
        write_cache(cls.cache)

    @classmethod
    def tearDownClass(cls):
        cls.client_db.drop_database(cls.dbname)
        cls.client_db.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.client_db.drop_database(self.dbname)
        self.db = self.client_db[self.dbname]
        self.settings = Settings(mongo_db=self.dbname, cache_dir=self.cache)
        self.app = create_app(self.settings, db=self.db, store=data.Store(self.cache))
        self.http = TestClient(self.app, follow_redirects=False, raise_server_exceptions=False)
        self.http.__enter__()   # runs startup (indexes)
        self.addCleanup(self.http.__exit__, None, None, None)
        services.create_user(self.db, "admin", PW, "Administrator")
        services.create_user(self.db, "analyst", PW, "Analyst")

    # helpers
    def csrf(self, client, path):
        html = client.get(path).text
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def login(self, username="admin", password=PW):
        client = TestClient(self.app, follow_redirects=False, raise_server_exceptions=False)
        resp = client.post("/login", data={"username": username, "password": password, "csrf_token": self.csrf(client, "/login")})
        return client, resp

    def admin(self):
        return self.login("admin")[0]

    def analyst(self):
        return self.login("analyst")[0]

    def post(self, client, path, page, **fields):
        return client.post(path, data={"csrf_token": self.csrf(client, page), **fields})


class AuthTest(AppTestCase):
    def test_unauthenticated_access(self):
        r = self.http.get("/overview")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/login"))
        r = self.http.get("/api/series")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "Authentication required."})
        self.assertEqual(self.http.post("/logout", data={}).status_code, 303)

    def test_login_success_sets_hardened_cookie_and_server_side_session(self):
        client, resp = self.login()
        self.assertEqual((resp.status_code, resp.headers["location"]), (303, "/overview"))
        cookie = resp.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        token = client.cookies.get("earthscape_session")
        session = self.db.sessions.find_one({"_id": security.token_hash(token)})
        self.assertTrue(session and session["user_id"])
        self.assertIsNone(self.db.sessions.find_one({"_id": token}))   # token itself is not stored
        self.assertEqual(client.get("/overview").status_code, 200)

    def test_login_failures_are_generic(self):
        _, bad_pw = self.login("admin", "wrong-password-123")
        _, no_user = self.login("nobody", "wrong-password-123")
        self.assertEqual((bad_pw.status_code, no_user.status_code), (401, 401))
        self.assertIn("Invalid username or password.", bad_pw.text)
        self.assertEqual(re.sub(r'value="[^"]+"', "", bad_pw.text), re.sub(r'value="[^"]+"', "", no_user.text))

    def test_login_requires_csrf(self):
        client = TestClient(self.app, follow_redirects=False)
        client.get("/login")
        r = client.post("/login", data={"username": "admin", "password": PW, "csrf_token": "forged"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(TestClient(self.app).post("/login", data={"username": "admin", "password": PW}).status_code, 403)

    def test_session_id_changes_at_login(self):
        client = TestClient(self.app, follow_redirects=False)
        token = self.csrf(client, "/login")
        before = client.cookies.get("earthscape_session")
        client.post("/login", data={"username": "admin", "password": PW, "csrf_token": token})
        self.assertNotEqual(before, client.cookies.get("earthscape_session"))
        self.assertIsNone(self.db.sessions.find_one({"_id": security.token_hash(before)}))

    def test_logout_ends_session(self):
        client, _ = self.login()
        old = client.cookies.get("earthscape_session")
        self.assertEqual(self.post(client, "/logout", "/overview").status_code, 303)
        client.cookies.set("earthscape_session", old)
        self.assertEqual(client.get("/overview").status_code, 303)
        self.assertEqual(self.db.sessions.count_documents({"_id": security.token_hash(old)}), 0)

    def test_logout_requires_csrf(self):
        client, _ = self.login()
        self.assertEqual(client.post("/logout", data={"csrf_token": "x"}).status_code, 403)
        self.assertEqual(client.get("/overview").status_code, 200)

    def test_expired_session_rejected(self):
        client, _ = self.login()
        self.db.sessions.update_many({}, {"$set": {"expires_at": services.now() - timedelta(minutes=1)}})
        self.assertEqual(client.get("/overview").status_code, 303)

    def test_lockout_after_repeated_failures(self):
        for _ in range(services.MAX_FAILED):
            self.login("analyst", "wrong-password-123")
        _, resp = self.login("analyst", PW)
        self.assertEqual(resp.status_code, 401)
        self.db.users.update_one({"username": "analyst"}, {"$set": {"locked_until": services.now() - timedelta(seconds=1)}})
        self.assertEqual(self.login("analyst", PW)[1].status_code, 303)

    def test_disabled_user_cannot_use_existing_session_or_login(self):
        client, _ = self.login("analyst")
        self.db.users.update_one({"username": "analyst"}, {"$set": {"active": False}})
        self.assertEqual(client.get("/overview").status_code, 303)
        self.assertEqual(self.login("analyst", PW)[1].status_code, 401)

    def test_security_headers_and_no_openapi(self):
        r = self.http.get("/login")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(self.http.get("/openapi.json").status_code, 404)


class RbacUserTest(AppTestCase):
    def test_analyst_blocked_from_admin_routes(self):
        a = self.analyst()
        for path in ("/admin/users", "/admin/feedback"):
            self.assertEqual(a.get(path).status_code, 403, path)
        csrf = self.csrf(a, "/overview")
        for path, body in (("/admin/users", {"username": "evil", "password": PW, "role": "Administrator"}),
                           ("/admin/data/refresh", {}), ("/admin/feedback/" + "a" * 24 + "/status", {"status": "reviewed"})):
            self.assertEqual(a.post(path, data={"csrf_token": csrf, **body}).status_code, 403, path)
        self.assertIsNone(self.db.users.find_one({"username": "evil"}))

    def test_admin_pages_ok_and_nav_hides_admin_for_analyst(self):
        self.assertEqual(self.admin().get("/admin/users").status_code, 200)
        self.assertNotIn("User Management", self.analyst().get("/overview").text)
        self.assertIn("User Management", self.admin().get("/overview").text)

    def test_create_user(self):
        a = self.admin()
        r = self.post(a, "/admin/users", "/admin/users", username="Newbie", password=PW, role="Analyst")
        self.assertEqual(r.status_code, 303)
        u = self.db.users.find_one({"username": "newbie"})
        self.assertEqual(u["role"], "Analyst")
        self.assertNotEqual(u["password_hash"], PW)
        self.assertEqual(self.login("newbie")[1].status_code, 303)

    def test_create_user_validation(self):
        a = self.admin()
        for fields in ({"username": "admin", "password": PW, "role": "Analyst"},         # duplicate
                       {"username": "ok_user", "password": "short", "role": "Analyst"},    # weak
                       {"username": "ok_user", "password": PW, "role": "Root"},           # unknown role
                       {"username": "bad name", "password": PW, "role": "Analyst"}):
            self.post(a, "/admin/users", "/admin/users", **fields)
            self.assertIn("alert-danger", a.get("/admin/users").text)
        self.assertEqual(self.db.users.count_documents({}), 2)

    def test_role_change_and_last_admin_protection(self):
        a = self.admin()
        analyst = self.db.users.find_one({"username": "analyst"})
        admin = self.db.users.find_one({"username": "admin"})
        self.post(a, f"/admin/users/{analyst['_id']}/role", "/admin/users", role="Administrator")
        self.assertEqual(self.db.users.find_one({"_id": analyst["_id"]})["role"], "Administrator")
        self.post(a, f"/admin/users/{analyst['_id']}/role", "/admin/users", role="Analyst")
        self.post(a, f"/admin/users/{admin['_id']}/role", "/admin/users", role="Analyst")      # last admin
        self.post(a, f"/admin/users/{admin['_id']}/active", "/admin/users", active="0")
        stored = self.db.users.find_one({"_id": admin["_id"]})
        self.assertEqual((stored["role"], stored["active"]), ("Administrator", True))

    def test_disable_and_reset_password_end_sessions(self):
        a, b = self.admin(), self.analyst()
        analyst = self.db.users.find_one({"username": "analyst"})
        self.post(a, f"/admin/users/{analyst['_id']}/password", "/admin/users", password="a-brand-new-password")
        self.assertEqual(b.get("/overview").status_code, 303)
        self.assertEqual(self.login("analyst", "a-brand-new-password")[1].status_code, 303)
        self.assertEqual(self.login("analyst", PW)[1].status_code, 401)
        self.post(a, f"/admin/users/{analyst['_id']}/active", "/admin/users", active="0")
        self.assertEqual(self.login("analyst", "a-brand-new-password")[1].status_code, 401)

    def test_user_actions_need_csrf_and_valid_ids(self):
        a = self.admin()
        self.assertEqual(a.post("/admin/users", data={"username": "x1x", "password": PW, "role": "Analyst"}).status_code, 403)
        self.post(a, "/admin/users/not-an-id/role", "/admin/users", role="Analyst")
        self.assertIn("Invalid identifier", a.get("/admin/users").text)


class MongoTest(AppTestCase):
    def test_indexes(self):
        names = {c: {k for k, v in self.db[c].index_information().items()} for c in
                 ("users", "sessions", "feedback", "alert_rules", "latest_readings")}
        self.assertIn("username_1", names["users"])
        self.assertTrue(self.db.users.index_information()["username_1"]["unique"])
        self.assertEqual(self.db.sessions.index_information()["expires_at_1"]["expireAfterSeconds"], 0)
        self.assertTrue(self.db.alert_rules.index_information()["name_1"]["unique"])
        self.assertIn("city_id_1_source_1_observed_at_-1", names["latest_readings"])

    def test_unique_username_enforced_by_database(self):
        with self.assertRaises(services.ValidationError):
            services.create_user(self.db, "ADMIN", PW, "Analyst")

    def test_no_secrets_in_session_or_user_listing(self):
        self.assertTrue(all("password_hash" not in u for u in services.list_users(self.db)))

    def test_database_down_gives_503_not_crash(self):
        client, _ = self.login()
        with mock.patch.object(services, "list_rules", side_effect=PyMongoError("boom")):
            r = client.get("/alerts")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("boom", r.text)


class FeedbackTest(AppTestCase):
    def test_submit_and_admin_review(self):
        b = self.analyst()
        r = self.post(b, "/feedback", "/feedback", category="bug", subject="Chart problem", message="The chart axis looks wrong today.")
        self.assertEqual(r.status_code, 303)
        item = self.db.feedback.find_one({})
        self.assertEqual((item["username"], item["category"], item["status"]), ("analyst", "bug", "new"))
        a = self.admin()
        self.assertIn("Chart problem", a.get("/admin/feedback").text)
        self.post(a, f"/admin/feedback/{item['_id']}/status", "/admin/feedback", status="reviewed")
        self.assertEqual(self.db.feedback.find_one({})["status"], "reviewed")
        self.assertNotIn("Chart problem", a.get("/admin/feedback?status=new").text)
        self.assertEqual(b.get("/admin/feedback").status_code, 403)

    def test_invalid_feedback_rejected(self):
        b = self.analyst()
        for fields in ({"category": "spam", "subject": "Valid subject", "message": "A long enough message."},
                       {"category": "bug", "subject": "x", "message": "A long enough message."},
                       {"category": "bug", "subject": "Valid subject", "message": "short"},
                       {"category": "bug", "subject": "Valid subject", "message": "x" * 2001}):
            self.post(b, "/feedback", "/feedback", **fields)
        self.assertEqual(self.db.feedback.count_documents({}), 0)

    def test_needs_login_and_csrf_and_escapes_html(self):
        self.assertEqual(self.http.post("/feedback", data={}).status_code, 303)
        b = self.analyst()
        self.assertEqual(b.post("/feedback", data={"category": "bug", "subject": "Valid subject", "message": "A long enough message."}).status_code, 403)
        self.post(b, "/feedback", "/feedback", category="feedback", subject="<script>alert(1)</script>", message="<img src=x onerror=alert(1)> text")
        page = self.admin().get("/admin/feedback").text
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


class AlertTest(AppTestCase):
    RULE = {"name": "Karachi heat", "city_id": "karachi", "variable": "temperature_2m_max_c", "operator": ">",
            "threshold": "44.5", "severity": "critical"}

    def test_create_list_edit_disable(self):
        b = self.analyst()
        self.assertEqual(self.post(b, "/alerts", "/alerts", **self.RULE).status_code, 303)
        rule = self.db.alert_rules.find_one({})
        self.assertEqual((rule["threshold"], rule["enabled"], rule["created_by"]), (44.5, True, "analyst"))
        self.assertIn("Karachi heat", self.admin().get("/alerts").text)
        self.assertIn("never evaluated", b.get("/alerts").text)
        self.post(b, f"/alerts/{rule['_id']}/edit", "/alerts", **{**self.RULE, "threshold": "46", "operator": ">="})
        rule = self.db.alert_rules.find_one({})
        self.assertEqual((rule["threshold"], rule["operator"]), (46.0, ">="))
        self.post(b, f"/alerts/{rule['_id']}/toggle", "/alerts", enabled="0")
        self.assertFalse(self.db.alert_rules.find_one({})["enabled"])
        self.post(b, f"/alerts/{rule['_id']}/toggle", "/alerts", enabled="1")
        self.assertTrue(self.db.alert_rules.find_one({})["enabled"])
        self.assertIn('href="/alerts?edit=', b.get("/alerts").text)
        self.assertIn("Edit rule", b.get(f"/alerts?edit={rule['_id']}").text)

    def test_invalid_rules_rejected(self):
        b = self.analyst()
        bad = [{"threshold": "abc"}, {"threshold": "nan"}, {"threshold": "inf"}, {"threshold": "1e12"}, {"variable": "solar"},
               {"operator": "=="}, {"severity": "panic"}, {"city_id": "atlantis"}, {"name": "ab"}]
        for change in bad:
            self.post(b, "/alerts", "/alerts", **{**self.RULE, **change})
        self.assertEqual(self.db.alert_rules.count_documents({}), 0)

    def test_duplicate_name_and_csrf_and_auth(self):
        b = self.analyst()
        self.post(b, "/alerts", "/alerts", **self.RULE)
        self.post(b, "/alerts", "/alerts", **self.RULE)
        self.assertEqual(self.db.alert_rules.count_documents({}), 1)
        self.assertEqual(b.post("/alerts", data=self.RULE).status_code, 403)
        self.assertEqual(self.http.post("/alerts", data=self.RULE).status_code, 303)
        self.assertEqual(self.db.alert_rules.count_documents({}), 1)


class DataTest(AppTestCase):
    def test_store_loads_and_windows(self):
        store = data.Store(self.cache)
        self.assertTrue(store.ready)
        self.assertEqual(store.cities(), ["karachi", "lahore"])
        w = store.window("daily", "karachi", "2001-01-05", "2001-01-08")
        self.assertEqual(w["keys"], ["2001-01-05", "2001-01-06", "2001-01-07", "2001-01-08"])
        self.assertIsNone(store.window("daily", "karachi", "2001-01-04", "2001-01-04")["precipitation_total_mm"][0])   # missing stays None

    def test_missing_or_tampered_cache_is_reported_not_fatal(self):
        empty = data.Store(Path(self.tmp.name) / "nothing")
        self.assertFalse(empty.ready)
        self.assertIn("No cached data", empty.error)
        bad = Path(self.tmp.name) / "bad"
        write_cache(bad)
        (bad / "daily.csv").write_text("tampered", encoding="utf-8")
        store = data.Store(bad)
        self.assertFalse(store.ready)
        self.assertIn("checksum", store.error)

    def test_overview_stats(self):
        o = data.Store(self.cache).overview("karachi")
        self.assertEqual((o["days"], o["first_date"], o["last_date"]), (40, "2001-01-01", "2001-02-09"))
        self.assertEqual(o["days_without_precipitation_total"], 1)
        self.assertEqual(o["highest_daily_max_c"]["value"], 20.0)

    def test_compare_requires_complete_years_for_precipitation(self):
        c = data.Store(self.cache).compare(2001, 2002)["karachi"]
        self.assertEqual(c["years"], [2001, 2002])
        self.assertEqual(c["precipitation_mm"], [42.0, 42.0])

    def test_refresh_validates_against_manifest(self):
        daily, monthly = fixture_files()
        mk = lambda name, body, extra=None: {"object_key": f"{name}/all", "status": "ok", "job_id": "job_1_2", "application_id": "application_1_2",
                                             "yarn_final_state": "SUCCEEDED", "submitted_at": "t", "version": "v",
                                             "inputs": [{"sha256": hashlib.sha256(daily.encode()).hexdigest()}],
                                             "output": {"hdfs_path": f"/p/{name}", "size_bytes": len(body.encode()),
                                                        "sha256": hashlib.sha256(body.encode()).hexdigest(), "record_count": body.count("\n") - 1}}
        files = {"/earthscape/_manifest/power_daily.jsonl": json.dumps(mk("power_daily", daily)) + "\n",
                 "/earthscape/_manifest/power_monthly.jsonl": json.dumps(mk("power_monthly", monthly)) + "\n",
                 "/p/power_daily": daily, "/p/power_monthly": monthly}
        for m in ("power_hourly_gridded", "power_hourly_interim"):
            files[f"/earthscape/_manifest/{m}.jsonl"] = json.dumps({"object_key": "a", "status": "ok", "retrieved_at": "r"}) + "\n"

        def fake(files):
            def call(op, path, timeout=0):
                if path not in files:
                    raise data.DataError('missing')
                return files[path].encode()
            return call

        target = Path(self.tmp.name) / "refreshed"
        with mock.patch.object(data, "hdfs", fake(files)):
            meta = data.refresh(target)
        self.assertEqual(meta["daily"]["record_count"], 80)
        self.assertTrue(data.Store(target).ready)
        broken = {**files, "/p/power_daily": daily.replace("1.2", "9.9", 1)}
        with mock.patch.object(data, "hdfs", fake(broken)), self.assertRaises(data.DataError):
            data.refresh(Path(self.tmp.name) / "never")
        self.assertFalse((Path(self.tmp.name) / "never").exists())

    def test_hdfs_unavailable_raises_dataerror(self):
        with mock.patch.object(data.subprocess, "run", side_effect=OSError("no wsl")), self.assertRaises(data.DataError):
            data.hdfs("-ls", "/")


class DashboardTest(AppTestCase):
    def test_pages_render_for_both_roles(self):
        for client in (self.admin(), self.analyst()):
            for path in ("/overview", "/history", "/compare", "/sources", "/alerts", "/feedback"):
                r = client.get(path)
                self.assertEqual(r.status_code, 200, path)
        self.assertIn("not physical weather-station observations", self.admin().get("/overview").text.replace("Not physical", "not physical"))
        self.assertIn("Gridded reanalysis", self.analyst().get("/sources").text)
        self.assertIn("Not implemented", self.analyst().get("/sources").text)
        self.assertNotIn("Refresh from HDFS", self.analyst().get("/sources").text)

    def test_api_series(self):
        a = self.analyst()
        d = a.get("/api/series?city=karachi&start=2001-01-02&end=2001-01-05").json()
        self.assertEqual(d["labels"], ["2001-01-02", "2001-01-03", "2001-01-04", "2001-01-05"])
        self.assertIsNone(d["precipitation_mm"][2])
        self.assertIn("Not physical weather-station observations", d["source"])
        m = a.get("/api/series?city=lahore&start=2001-03-01&end=2001-05-31&granularity=monthly").json()
        self.assertEqual(m["labels"], ["2001-03", "2001-04", "2001-05"])
        self.assertEqual(m["precipitation_unit"], "mm/month")

    def test_api_invalid_input(self):
        a = self.analyst()
        for q in ("city=atlantis", "start=yesterday", "end=2001-13-40", "granularity=hourly", "start=2001-02-01&end=2001-01-01",
                  "start=2001-01-01&end=2007-01-01&granularity=daily"):
            self.assertEqual(a.get("/api/series?" + q).status_code, 400, q)
        self.assertEqual(a.get("/api/compare?start_year=2010&end_year=2005").status_code, 400)
        self.assertEqual(a.get("/api/compare?start_year=abc").status_code, 400)
        self.assertEqual(a.get("/api/overview?city=%27%3B%20drop").status_code, 400)
        self.assertEqual(a.get("/compare?start_year=1900").status_code, 400)

    def test_api_overview_and_compare(self):
        a = self.analyst()
        o = a.get("/api/overview?city=karachi").json()
        self.assertEqual(o["days"], 40)
        c = a.get("/api/compare?start_year=2001&end_year=2002").json()
        self.assertEqual(set(c["cities"]), {"Karachi", "Lahore"})

    def test_api_without_data_returns_503_and_pages_explain(self):
        app = create_app(self.settings, db=self.db, store=data.Store(Path(self.tmp.name) / "empty"))
        client = TestClient(app, follow_redirects=False)
        page = client.get("/login").text
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        client.post("/login", data={"username": "analyst", "password": PW, "csrf_token": token})
        self.assertEqual(client.get("/api/series").status_code, 503)
        self.assertIn("No cached data yet", client.get("/overview").text)
        self.assertEqual(client.get("/history").status_code, 200)

    def test_refresh_failure_keeps_cache_and_reports(self):
        a = self.admin()
        with mock.patch.object(data, "hdfs", side_effect=data.DataError("HDFS not reachable")):
            r = self.post(a, "/admin/data/refresh", "/sources")
        self.assertEqual(r.status_code, 303)
        self.assertIn("previous cache is unchanged", a.get("/sources").text)
        self.assertTrue(self.app.state.store.ready)

    def test_unexpected_error_hides_details(self):
        a = self.admin()
        with mock.patch.object(services, "list_users", side_effect=RuntimeError("secret internals")):
            r = a.get("/admin/users")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("secret internals", r.text)
        self.assertNotIn("Traceback", r.text)
        self.assertEqual(self.http.get("/no/such/page").status_code, 404)


class HealthTest(AppTestCase):
    def test_health_separates_app_from_dependencies(self):
        with mock.patch.object(data, "hdfs", side_effect=data.DataError("down")):
            h = self.http.get("/health")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["app"], "ok")
        self.assertEqual(h.json()["mongodb"], "ok")
        self.assertEqual(h.json()["hdfs"], "unavailable")
        self.assertEqual(h.json()["data_cache"]["status"], "ok")

    def test_health_when_mongodb_is_down(self):
        with mock.patch.object(self.db, "command", side_effect=PyMongoError("x")), mock.patch.object(data, "hdfs", return_value=b""):
            h = self.http.get("/health")
        self.assertEqual((h.status_code, h.json()["app"], h.json()["mongodb"]), (200, "ok", "unavailable"))


if __name__ == "__main__":
    unittest.main()
