import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "ml"))
sys.path.insert(0, str(ROOT / "src" / "processing" / "mapreduce"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app import AppTestCase, PW, MONGO

import climate_ml
import power_yearly_mapper as ym
import power_yearly_reducer as yr
from app import analytics, live, manage, services
from ingestion import live_sources as src, modis

NOW = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)

WEATHER_JSON = {"latitude": 24.85, "longitude": 66.99, "elevation": 8.0, "utc_offset_seconds": 0,
                "current_units": {"time": "iso8601", "interval": "seconds", "temperature_2m": "°C", "relative_humidity_2m": "%",
                                  "precipitation": "mm", "wind_speed_10m": "km/h", "surface_pressure": "hPa"},
                "current": {"time": "2026-10-08T10:45", "interval": 900, "temperature_2m": 29.7, "relative_humidity_2m": 65,
                            "precipitation": 0.0, "wind_speed_10m": 14.3, "surface_pressure": 1012.8}}
AQ_JSON = {"latitude": 24.9, "longitude": 67.0, "current_units": {"time": "iso8601", "pm2_5": "μg/m³", "pm10": "μg/m³", "ozone": "μg/m³"},
           "current": {"time": "2026-10-08T10:00", "interval": 3600, "pm2_5": 18.6, "pm10": 48.3, "ozone": 85.0, "carbon_monoxide": None}}
LOCATION = {"id": 8664, "name": "Test Monitor", "isMobile": False, "isMonitor": True, "owner": {"name": "Org"}, "provider": {"name": "ProvA"},
            "coordinates": {"latitude": 31.56, "longitude": 74.34}, "datetimeLast": {"utc": "2026-10-08T09:00:00Z"},
            "sensors": [{"id": 25135, "parameter": {"name": "pm25", "units": "µg/m³"}}, {"id": 25136, "parameter": {"name": "temperature", "units": "c"}}]}
LATEST_JSON = {"results": [{"datetime": {"utc": "2026-10-08T09:00:00Z"}, "value": 68.1, "sensorsId": 25135, "locationsId": 8664},
                           {"datetime": {"utc": "2026-10-08T09:00:00Z"}, "value": 31.0, "sensorsId": 25136, "locationsId": 8664}]}


def reading(source, city, field, value, age_min=5, station="", origin="modelled"):
    return {"source": source, "value_origin": origin, "city_id": city, "station_key": station,
            "observed_at": NOW - timedelta(minutes=age_min), "retrieved_at": NOW, "values": {field: value}, "units": {field: "u"},
            "provider": {"location_name": "Test Monitor", "distance_km": 1.0, "sensor_class": "reference monitor", "data_provider": "ProvA"}}


class SourceParsingTest(unittest.TestCase):
    def test_openmeteo_weather_is_modelled_and_utc(self):
        r = src.normalise_openmeteo(src.WEATHER, "karachi", {"retrieved_at": "2026-10-08T10:46:00+00:00", "request": {}, "response": WEATHER_JSON})
        self.assertEqual((r["value_origin"], r["observed_at"].isoformat(), r["station_key"]), ("modelled", "2026-10-08T10:45:00+00:00", ""))
        self.assertEqual(r["values"]["temperature_2m"], 29.7)
        self.assertEqual(r["units"]["surface_pressure"], "hPa")
        self.assertEqual(r["provider"]["interval_s"], 900)

    def test_openmeteo_aq_drops_null_values_and_stays_modelled(self):
        r = src.normalise_openmeteo(src.AIRQUALITY, "lahore", {"retrieved_at": "2026-10-08T10:46:00+00:00", "request": {}, "response": AQ_JSON})
        self.assertNotIn("carbon_monoxide", r["values"])
        self.assertEqual(r["value_origin"], "modelled")
        self.assertIn("CAMS", r["provider"]["model_note"])

    def test_empty_provider_response_is_an_error(self):
        bad = {"retrieved_at": "2026-10-08T10:46:00+00:00", "request": {}, "response": {"current": {"time": "2026-10-08T10:00"}, "current_units": {}}}
        with self.assertRaises(src.FetchError):
            src.normalise_openmeteo(src.WEATHER, "karachi", bad)

    def test_openaq_keeps_only_pm25_with_metadata_and_never_invents(self):
        out = src.normalise_openaq("lahore", LOCATION, {"retrieved_at": "2026-10-08T10:00:00+00:00", "response": LATEST_JSON})
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual((r["value_origin"], r["values"], r["station_key"]), ("observed", {"pm25": 68.1}, "8664:25135"))
        self.assertEqual((r["provider"]["sensor_class"], r["provider"]["location_name"]), ("reference monitor", "Test Monitor"))
        self.assertLess(r["provider"]["distance_km"], 5)
        self.assertEqual(src.normalise_openaq("lahore", LOCATION, {"retrieved_at": "2026-10-08T10:00:00+00:00", "response": {"results": []}}), [])

    def test_openaq_requires_key_and_never_leaks_it(self):
        with mock.patch.dict("os.environ", {"OPENAQ_API_KEY": ""}):
            with self.assertRaises(src.FetchError) as cm:
                src.fetch_openaq("lahore")
        self.assertNotIn("OPENAQ_API_KEY=", str(cm.exception).replace("OPENAQ_API_KEY is not set", ""))

    def test_get_json_retries_then_fails(self):
        with mock.patch.object(src.urllib.request, "urlopen", side_effect=src.urllib.error.URLError("down")), \
                mock.patch.object(src.time, "sleep"):
            with self.assertRaises(src.FetchError):
                src.get_json("http://x.invalid", attempts=3)


class LiveStorageAndAlertsTest(AppTestCase):
    def test_duplicate_readings_not_stored_twice(self):
        r = reading(src.WEATHER, "karachi", "temperature_2m", 30.0)
        self.assertEqual(live.store_readings(self.db, [r]), 1)
        self.assertEqual(live.store_readings(self.db, [dict(r)]), 0)
        self.assertEqual(self.db.latest_readings.count_documents({}), 1)

    def test_poll_failure_is_recorded_and_other_cities_continue(self):
        def fake(source, city):
            if city == "lahore":
                raise src.FetchError("HTTP 503")
            return {"retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "request": {"city": city}, "response": WEATHER_JSON}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(live, "STAGING", Path(tmp)), mock.patch.object(src, "fetch_openmeteo", fake):
            stored, errors = live.poll_openmeteo(self.db, src.WEATHER)
            staged = [len(f.read_text().splitlines()) for f in Path(tmp).rglob("polls.jsonl")]
        self.assertEqual(stored, 4)
        self.assertTrue(any("lahore" in e for e in errors))
        status = self.db.ingest_status.find_one({"_id": src.WEATHER})
        self.assertEqual(status["consecutive_failures"], 1)
        self.assertEqual(staged, [4])

    def rule(self, **kw):
        base = {"name": "r", "city_id": "karachi", "variable": "openmeteo_weather_model:temperature_2m", "operator": ">",
                "threshold": 35.0, "severity": "warning", "enabled": True, "created_by": "t"}
        self.db.alert_rules.insert_one({**base, **kw})

    def test_threshold_alert_lifecycle_without_duplicates(self):
        self.rule()
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 30.0, age_min=30)])
        self.assertEqual(live.evaluate_alerts(self.db)["created"], 0)
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 41.0, age_min=10)])
        self.assertEqual(live.evaluate_alerts(self.db)["created"], 1)
        self.assertEqual(live.evaluate_alerts(self.db)["created"], 0)
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 42.0, age_min=5)])
        live.evaluate_alerts(self.db)
        self.assertEqual(self.db.alerts.count_documents({}), 1)
        a = self.db.alerts.find_one()
        self.assertEqual((a["value"], a["last_value"], a["value_origin"], a["status"], a["unit"]), (41.0, 42.0, "modelled", "open", "C"))
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 20.0, age_min=1)])
        self.assertEqual(live.evaluate_alerts(self.db)["cleared"], 1)
        self.assertEqual(self.db.alerts.find_one()["status"], "cleared")
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 44.0, age_min=0)])
        live.evaluate_alerts(self.db)
        self.assertEqual(self.db.alerts.count_documents({}), 2)

    def test_stale_readings_never_raise_or_clear_alerts(self):
        self.rule()
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 50.0, age_min=500)])
        s = live.evaluate_alerts(self.db)
        self.assertEqual((s["created"], s["stale_skipped"]), (0, 1))

    def test_rule_scope_disabled_and_historical_variables(self):
        self.rule(city_id="all", name="all-cities")
        self.rule(name="off", enabled=False)
        self.rule(name="hist", variable="temperature_2m_max_c", threshold=-100.0)
        for city in ("karachi", "lahore"):
            live.store_readings(self.db, [reading(src.WEATHER, city, "temperature_2m", 40.0)])
        s = live.evaluate_alerts(self.db)
        self.assertEqual(s["created"], 2)
        self.assertEqual({a["city_id"] for a in self.db.alerts.find()}, {"karachi", "lahore"})

    def test_observed_pm25_alert_is_per_station_and_labelled_observed(self):
        self.rule(city_id="lahore", variable="openaq_observed:pm25", threshold=50.0, name="pm")
        live.store_readings(self.db, [reading(src.OPENAQ, "lahore", "pm25", 80.0, station="1:1", origin="observed"),
                                      reading(src.OPENAQ, "lahore", "pm25", 10.0, station="2:2", origin="observed"),
                                      reading(src.OPENAQ, "lahore", "pm25", 90.0, station="3:3", origin="observed")])
        self.assertEqual(live.evaluate_alerts(self.db)["created"], 2)
        self.assertEqual({a["value_origin"] for a in self.db.alerts.find()}, {"observed"})

    def test_no_data_means_no_alert(self):
        self.rule()
        s = live.evaluate_alerts(self.db)
        self.assertEqual((s["created"], s["no_data"]), (0, 1))

    def test_alert_ui_acknowledge_history_and_badge(self):
        self.rule()
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 41.0)])
        live.evaluate_alerts(self.db)
        b = self.analyst()
        self.assertIn('badge bg-danger">1<', b.get("/overview").text)
        page = b.get("/alerts/history").text
        self.assertIn("MODELLED", page)
        self.assertIn("email and SMS delivery are not configured", page)
        alert = self.db.alerts.find_one()
        self.assertEqual(b.post(f"/alerts/history/{alert['_id']}/ack", data={"csrf_token": "forged"}).status_code, 403)
        self.post(b, f"/alerts/history/{alert['_id']}/ack", "/alerts/history")
        a = self.db.alerts.find_one()
        self.assertEqual((a["status"], a["acknowledged_by"]), ("acknowledged", "analyst"))
        self.assertNotIn("badge bg-danger", b.get("/overview").text)
        self.post(b, f"/alerts/history/{alert['_id']}/ack", "/alerts/history")
        self.assertIn("not found or not open", b.get("/alerts/history").text)
        self.assertEqual(self.http.post(f"/alerts/history/{alert['_id']}/ack", data={}).status_code, 303)

    def test_live_alert_rule_can_be_created_through_the_form(self):
        b = self.analyst()
        self.post(b, "/alerts", "/alerts", name="PM obs", city_id="all", variable="openaq_observed:pm25", operator=">", threshold="100", severity="critical")
        self.assertEqual(self.db.alert_rules.find_one()["variable"], "openaq_observed:pm25")
        self.assertIn("Live", b.get("/alerts").text)

    def test_current_data_pages_and_api(self):
        live.store_readings(self.db, [reading(src.WEATHER, "karachi", "temperature_2m", 30.5),
                                      reading(src.AIRQUALITY, "karachi", "pm2_5", 22.0),
                                      reading(src.OPENAQ, "karachi", "pm25", 61.0, station="9:9", origin="observed", age_min=7000)])
        a = self.analyst()
        html = a.get("/current").text
        self.assertIn("Near-real-time <strong>REST polling</strong>", html)
        self.assertIn("30.5", html)
        self.assertIn("STALE", html)
        j = a.get("/api/current").json()
        self.assertIn("modelled", j["note"])
        self.assertEqual(j["cities"]["karachi"]["weather"]["values"]["temperature_2m"], 30.5)
        self.assertNotIn('"_id"', json.dumps(j))
        h = a.get("/api/current/history?city=karachi&hours=24").json()
        self.assertEqual(h["weather"]["temperature_2m"], [30.5])
        self.assertEqual(a.get("/api/current/history?city=nowhere").status_code, 400)
        self.assertEqual(a.get("/api/current/history?city=karachi&hours=0").status_code, 400)
        self.assertEqual(self.http.get("/api/current").status_code, 401)

    def test_poll_now_requires_admin_and_csrf(self):
        b = self.analyst()
        self.assertEqual(b.post("/admin/poll", data={"csrf_token": self.csrf(b, "/overview")}).status_code, 403)
        a = self.admin()
        self.assertEqual(a.post("/admin/poll", data={"csrf_token": "forged"}).status_code, 403)
        with mock.patch.object(live, "poll_cycle", return_value={}) as pc:
            self.assertEqual(self.post(a, "/admin/poll", "/current").status_code, 303)
            for _ in range(50):
                if pc.called:
                    break
                import time; time.sleep(0.05)
        self.assertTrue(pc.called)

    def test_background_poller_starts_and_stops_with_the_app(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.settings import Settings
        started = []
        with mock.patch.object(live, "poll_cycle", side_effect=lambda *a, **k: started.append(1) or {}), \
                mock.patch.object(live, "seal_raw", return_value=[]):
            app = create_app(Settings(mongo_db=self.dbname, cache_dir=self.cache, poll_enabled=True, poll_minutes=60), db=self.db)
            with TestClient(app):
                for _ in range(60):
                    if started:
                        break
                    import time; time.sleep(0.05)
                self.assertTrue(app.state.poller.is_alive())
            app.state.poller.join(timeout=3)
        self.assertTrue(started)
        self.assertFalse(app.state.poller.is_alive())


class SealRawTest(unittest.TestCase):
    def test_hdfs_down_keeps_the_file(self):
        tools = mock.Mock(HDFS_ROOT="/earthscape", IngestError=Exception)
        tools.hdfs_exists.side_effect = OSError("wsl down")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "openaq_observed" / "current" / "date=2020-01-01" / "latest.jsonl"
            f.parent.mkdir(parents=True)
            f.write_text("{}\n")
            with mock.patch.object(live, "STAGING", Path(tmp)), mock.patch.object(live, "hdfs_tools", tools):
                res = live.seal_raw()
            self.assertEqual(res[0]["status"], "kept locally")
            self.assertTrue(f.exists())


def synthetic_cache(path):
    """Synthetic multi-year series, used ONLY to test the ML code paths (never shown to users)"""
    rng = np.random.default_rng(0)
    days = pd.date_range("2001-01-01", "2025-12-31", freq="D")
    rows = []
    for ci, city in enumerate(["karachi", "lahore", "islamabad", "peshawar", "quetta"]):
        doy = days.dayofyear.values
        temp = 20 + 10 * np.sin(2 * np.pi * (doy - 100) / 365.25) + 0.03 * (days.year.values - 2001) + rng.normal(0, 1.5, len(days)) + ci
        df = pd.DataFrame({"city_id": city, "date": days, "hours_observed": 24, "temperature_2m_mean_c": temp,
                           "temperature_2m_min_c": temp - 4, "temperature_2m_max_c": temp + 4,
                           "relative_humidity_2m_mean_pct": 50 - 0.5 * (temp - 20) + rng.normal(0, 5, len(days)),
                           "precipitation_total_mm": np.maximum(0, rng.normal(0.5, 3, len(days))),
                           "wind_speed_2m_mean_m_s": 2 + rng.normal(0, .5, len(days)), "surface_pressure_mean_kpa": 100 + rng.normal(0, 1, len(days)),
                           "solar_irradiance_mean_mj_hr": 1 + 0.5 * np.sin(2 * np.pi * (doy - 80) / 365.25) + rng.normal(0, .1, len(days))})
        df["temperature_2m_missing_hours"] = 0
        rows.append(df)
    daily = pd.concat(rows)
    daily["month"] = daily["date"].dt.strftime("%Y-%m")
    monthly = daily.groupby(["city_id", "month"]).agg(
        hours_observed=("hours_observed", "sum"), temperature_2m_mean_c=("temperature_2m_mean_c", "mean"),
        relative_humidity_2m_mean_pct=("relative_humidity_2m_mean_pct", "mean"), precipitation_total_mm=("precipitation_total_mm", "sum"),
        wind_speed_2m_mean_m_s=("wind_speed_2m_mean_m_s", "mean")).reset_index()
    Path(path).mkdir(parents=True, exist_ok=True)
    daily.drop(columns="month").to_csv(Path(path) / "daily.csv", index=False)
    monthly.to_csv(Path(path) / "monthly.csv", index=False)


class ClimateMlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        synthetic_cache(cls.tmp.name)
        cls.out = Path(cls.tmp.name) / "artifacts"
        cls.res = climate_ml.run(cls.tmp.name, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_result_structure_versions_and_labels(self):
        r = self.res
        self.assertEqual((r["model_version"], r["seed"]), ("climate_ml_v1", 42))
        self.assertIn("not station observations", r["data_labels"]["history"])
        self.assertIn("not observations", r["data_labels"]["predictions"])
        self.assertTrue((Path(r["artifact_dir"]) / "results.json").exists())
        self.assertEqual(set(r["forecast"]), {"karachi", "lahore", "islamabad", "peshawar", "quetta"})

    def test_forecast_split_is_chronological_and_forecast_is_in_the_future(self):
        f = self.res["forecast"]["lahore"]["temperature"]
        self.assertEqual(f["split"]["train"], "2001-01..2020-12")
        self.assertEqual(f["split"]["test"], "2021-01..2025-12")
        self.assertEqual(f["forecast"]["months"][0], "2026-01")
        self.assertEqual(len(f["forecast"]["values"]), 12)
        self.assertEqual(len(f["test_predictions"]["values"]), 60)
        self.assertIn("test set not used for selection", f["selected_on"])
        self.assertEqual(set(f["test_metrics"]), {"seasonal_climatology", "last_year_repeat", "harmonic_trend_ridge"})
        self.assertTrue(all(f["forecast"]["lower"][i] < f["forecast"]["values"][i] < f["forecast"]["upper"][i] for i in range(12)))

    def test_model_beats_naive_baseline_on_known_signal(self):
        f = self.res["forecast"]["karachi"]["temperature"]["test_metrics"]
        self.assertLess(f["harmonic_trend_ridge"]["mae"], f["last_year_repeat"]["mae"])
        self.assertGreater(f["harmonic_trend_ridge"]["r2"], 0.9)

    def test_baseline_uses_training_data_only(self):
        _, m = climate_ml.load(self.tmp.name)
        s = m[m.city_id == "karachi"].set_index("period")["temperature_2m_mean_c"]
        train = s[s.index.year <= 2020]
        expected = train.groupby(train.index.month).mean()
        got = climate_ml.climatology_model(train)(pd.period_range("2021-01", periods=12, freq="M"))
        self.assertTrue(np.allclose(got, expected.values))

    def test_training_is_reproducible(self):
        again = climate_ml.run(self.tmp.name, self.out)
        for part in ("trend", "anomalies", "correlation", "forecast"):
            self.assertEqual(json.dumps(self.res[part], sort_keys=True), json.dumps(again[part], sort_keys=True), part)

    def test_trend_recovers_the_synthetic_warming(self):
        t = self.res["trend"]["karachi"]["temperature"]
        self.assertAlmostEqual(t["slope_per_decade"], 0.3, delta=0.15)
        self.assertTrue(t["significant_at_5pct"])
        self.assertEqual(t["n_years"], 25)

    def test_anomaly_detection_flags_an_injected_heat_spike_only_after_training(self):
        d, _ = climate_ml.load(self.tmp.name)
        d = d[d.city_id == "karachi"].copy()
        spike = d["date"] == pd.Timestamp("2022-06-15")
        d.loc[spike, "temperature_2m_mean_c"] += 15
        a = climate_ml.anomalies(d)["karachi"]
        self.assertIn("2022-06-15", [x["date"] for x in a["top_zscore_days"]])
        self.assertLess(a["zscore_flag_rate_train"], 0.02)
        self.assertEqual(a["train_days"] + a["test_days"], 9131)

    def test_correlation_matrix_is_valid(self):
        c = self.res["correlation"]["karachi"]
        p = np.array(c["pearson"])
        self.assertTrue(np.allclose(p, p.T) and np.allclose(np.diag(p), 1))
        self.assertLess(p[c["variables"].index("temperature"), c["variables"].index("humidity")], -0.05)

    def test_missing_months_are_skipped_not_filled(self):
        _, m = climate_ml.load(self.tmp.name)
        m.loc[(m.city_id == "karachi") & (m.month == "2010-05"), "wind_speed_2m_mean_m_s"] = np.nan
        f = climate_ml.forecasts(m)["karachi"]["wind"]
        self.assertIn("skipped", f)


@unittest.skipUnless(MONGO, "local MongoDB is not running")
class AnalyticsPagesTest(AppTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ml_cache = Path(cls.tmp.name) / "mlcache"
        synthetic_cache(cls.ml_cache)
        cls.res = json.loads(json.dumps(climate_ml.run(cls.ml_cache, Path(cls.tmp.name) / "art")))

    def seed(self):
        self.db.ml_runs.insert_one({**self.res, "created_at": datetime.now(timezone.utc)})

    def test_pages_without_a_run_explain_instead_of_showing_fake_numbers(self):
        a = self.analyst()
        for path in ("/trends", "/forecast", "/correlation"):
            r = a.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn("No analytics run exists yet", r.text)
        self.assertEqual(a.get("/api/ml/summary").status_code, 503)

    def test_pages_render_stored_results(self):
        self.seed()
        a = self.analyst()
        t = a.get("/trends?city=quetta").text
        self.assertIn("Theil-Sen", t)
        self.assertIn("Isolation Forest", t)
        self.assertIn('id="payload"', t)
        f = a.get("/forecast?city=lahore&variable=precipitation").text
        self.assertIn("Predictions are not observations", f)
        self.assertIn("harmonic_trend_ridge", f)
        self.assertIn("corr-table", a.get("/correlation?city=karachi").text)
        j = a.get("/api/ml/summary").json()
        self.assertEqual(j["model_version"], "climate_ml_v1")
        self.assertIn("karachi", j["forecast"])

    def test_invalid_city_or_variable_rejected(self):
        self.seed()
        a = self.analyst()
        self.assertEqual(a.get("/forecast?city=atlantis").status_code, 400)
        self.assertEqual(a.get("/forecast?variable=gold").status_code, 400)
        self.assertEqual(a.get("/trends?city=<script>").status_code, 400)
        self.assertEqual(self.http.get("/trends").status_code, 303)

    def test_retrain_requires_admin_and_stores_a_versioned_run(self):
        b = self.analyst()
        self.assertEqual(b.post("/admin/ml/train", data={"csrf_token": self.csrf(b, "/overview")}).status_code, 403)
        real_run, art_dir = climate_ml.run, Path(self.tmp.name) / "retrain_art"
        with mock.patch.object(analytics.climate_ml, "run", side_effect=lambda cache, art: real_run(self.ml_cache, art_dir)):
            a = self.admin()
            self.post(a, "/admin/ml/train", "/trends")
        run = analytics.latest(self.db)
        self.assertEqual(run["model_version"], "climate_ml_v1")
        self.assertIn("created_at", run)


class YearlyReducerTest(unittest.TestCase):
    @staticmethod
    def day(date, tmin="10.0", tmax="20.0", precip="0.0", hours=24, miss=0, tmean="15.0000"):
        return f"karachi,{date},{hours},{tmean},{tmin},{tmax},50.0000,{precip},2.0000,100.0000,0.5000,{miss},0,{0 if precip else 24},0,0,0"

    def run_year(self, lines, year="2001"):
        groups = {}
        for l in lines:
            out = ym.map_line(l)
            if out:
                groups.setdefault(out[0], []).append(out[1].split(","))
        return [yr.reduce_year(k, v).split(",") for k, v in sorted(groups.items())]

    def test_indices_and_incomplete_year(self):
        lines = [self.day("2001-01-01", tmax="36.0", precip="25.0"), self.day("2001-01-02", tmin="-1.0", precip="10.0"),
                 self.day("2001-01-03", precip="1.0"), self.day("2001-01-04", precip="0.9"), self.day("2001-01-05", precip="")]
        [row] = self.run_year(lines)
        self.assertEqual(row[:4], ["karachi", "2001", "5", "120"])
        self.assertEqual(row[7], "")
        self.assertEqual(row[8:], ["25.0", "4", "3", "2", "1", "1", "1"])

    def test_full_leap_year_total_and_weighting(self):
        import datetime as dt
        days = [dt.date(2004, 1, 1) + dt.timedelta(i) for i in range(366)]
        lines = [self.day(str(d), precip="1.0", tmean="10.0000" if i % 2 else "20.0000") for i, d in enumerate(days)]
        [row] = self.run_year(lines)
        self.assertEqual((row[2], row[7], row[4]), ("366", "366.0", "15.0000"))

    def test_inconsistent_daily_row_rejected(self):
        with self.assertRaises(ValueError):
            self.run_year([self.day("2001-01-01", tmean="")])


class ModisTest(unittest.TestCase):
    def test_all_five_cities_land_in_the_three_approved_tiles(self):
        cities = json.loads((ROOT / "config" / "cities.json").read_text())
        tiles = {c: "h%02dv%02d" % modis.tile_pixel(v["latitude"], v["longitude"])[:2] for c, v in cities.items()}
        self.assertEqual(tiles, {"karachi": "h24v06", "lahore": "h24v05", "islamabad": "h24v05", "peshawar": "h23v05", "quetta": "h23v05"})
        for c, v in cities.items():
            _, _, row, col = modis.tile_pixel(v["latitude"], v["longitude"])
            self.assertTrue(0 <= row < 1200 and 0 <= col < 1200)

    def test_no_credentials_means_no_download(self):
        with mock.patch.dict("os.environ", {"EARTHDATA_TOKEN": "", "EARTHDATA_USERNAME": "", "EARTHDATA_PASSWORD": ""}):
            with self.assertRaises(modis.ModisError):
                modis.credentials()
        with mock.patch.dict("os.environ", {"EARTHDATA_TOKEN": "abc"}):
            self.assertEqual(modis.credentials(), "Bearer abc")

    def test_search_parses_cmr_entries(self):
        body = json.dumps({"feed": {"entry": [{"time_start": "2015-01-01T00:00:00.000Z", "granule_size": "5.5", "links": [
            {"rel": "http://esipfed.org/ns/fedsearch/1.1/data#", "href": "https://x.example/MOD11A2.A2015001.h24v05.061.2021.hdf"},
            {"rel": "http://esipfed.org/ns/fedsearch/1.1/metadata#", "href": "https://x.example/meta.xml"}]}]}}).encode()
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body
        with mock.patch.object(modis.urllib.request, "urlopen", return_value=resp):
            g = modis.search("h24v05", "2015-01-01", "2015-12-31")
        self.assertEqual(g, [{"name": "MOD11A2.A2015001.h24v05.061.2021.hdf", "url": "https://x.example/MOD11A2.A2015001.h24v05.061.2021.hdf",
                              "size_mb": 5.5, "time_start": "2015-01-01T00:00:00.000Z"}])

    def test_login_page_is_not_accepted_as_hdf(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".part") as f:
            f.write(b"<html>Earthdata Login</html>")
        with self.assertRaises(modis.ModisError):
            modis.validate_hdf(f.name)
        Path(f.name).write_bytes(modis.HDF4_MAGIC + b"\0" * 5_500_000)
        self.assertEqual(modis.validate_hdf(f.name, 5.5), 5_500_004)
        with self.assertRaises(modis.ModisError):
            modis.validate_hdf(f.name, 20.0)
        Path(f.name).unlink()


@unittest.skipUnless(MONGO, "local MongoDB is not running")
class BackupRestoreTest(AppTestCase):
    def test_round_trip_and_safety(self):
        self.db.feedback.insert_one({"username": "x", "subject": "s", "message": "m" * 12, "status": "new", "created_at": NOW})
        with tempfile.TemporaryDirectory() as tmp:
            out = manage.backup(self.db, tmp, with_hdfs=False)
            meta = json.loads((out / "BACKUP.json").read_text())
            self.assertEqual(meta["files"]["users.json"]["documents"], 2)
            self.assertNotIn("sessions.json", meta["files"])
            users_before = self.db.users.count_documents({})
            with self.assertRaises(SystemExit):
                manage.restore(self.db, out, replace=False)
            self.db.users.delete_many({})
            self.db.feedback.delete_many({})
            manage.restore(self.db, out, replace=True)
            self.assertEqual(self.db.users.count_documents({}), users_before)
            self.assertEqual(self.db.feedback.find_one()["subject"], "s")
            self.assertEqual(self.login("admin")[1].status_code, 303)
            (out / "feedback.json").write_text("[]")
            with self.assertRaises(SystemExit):
                manage.restore(self.db, out, replace=True)


if __name__ == "__main__":
    unittest.main()
