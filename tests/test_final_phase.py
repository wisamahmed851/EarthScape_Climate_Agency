"""Final-phase tests: live RAW daily rollover (fake HDFS), historical reference/comparison, health monitor, backup retention/restore."""
import hashlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app import AppTestCase  # noqa: E402

from app import live, manage, monitor, reference  # noqa: E402
from ingestion import live_sources as src, modis  # noqa: E402
import numpy as np  # noqa: E402


class FakeHdfs:
    """In-memory stand-in for the nasa_power HDFS helpers; `fail` = (command, occurrence) raises/returns an error once."""
    HDFS_ROOT = "/earthscape"
    IngestError = type("IngestError", (Exception,), {})

    def __init__(self, fail=None):
        self.files, self.fail, self.counts = {}, fail, {}

    def _maybe_fail(self, cmd):
        self.counts[cmd] = self.counts.get(cmd, 0) + 1
        return self.fail == (cmd, self.counts[cmd])

    def hdfs_exists(self, p):
        return p in self.files

    def hdfs_sha256(self, p):
        return hashlib.sha256(self.files[p]).hexdigest()

    def hdfs_ok(self, *a, stdin=None):
        cmd = a[0]
        if self._maybe_fail(cmd):
            raise self.IngestError(f"{cmd} failed")
        if cmd == "-put":
            self.files[a[-1]] = stdin.read()
        elif cmd == "-mv":
            self.files[a[-1]] = self.files.pop(a[-2])
        elif cmd == "-touchz":
            self.files[a[-1]] = b""
        elif cmd == "-cat":
            return self.files[a[-1]]
        return b""

    def hdfs(self, *a, input=None, stdin=None):
        if self._maybe_fail(a[0]):
            return types.SimpleNamespace(returncode=1, stdout=b"")
        self.files[a[2]] += input
        return types.SimpleNamespace(returncode=0, stdout=b"")

    def manifest(self, name):
        return [json.loads(l) for l in self.files.get(f"/earthscape/_manifest/{name}.jsonl", b"").decode().splitlines()]


def envelope(day, minute=0):
    return json.dumps({"retrieved_at": f"{day}T10:{minute:02d}:00+00:00", "request": {"city": "karachi"}, "response": {"x": minute}}) + "\n"


def stage(tmp, folder, name, day, n=3, text=None):
    f = Path(tmp) / folder / f"date={day}" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text if text is not None else "".join(envelope(day, i) for i in range(n)), encoding="utf-8", newline="\n")
    return f


class RawRolloverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = FakeHdfs()
        self.yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def seal(self, fake=None):
        with mock.patch.object(live, "STAGING", Path(self.tmp.name)), mock.patch.object(live, "hdfs_tools", fake or self.fake):
            return live.seal_raw()

    def test_finished_day_is_sealed_with_provenance_counts_and_hash(self):
        f = stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.yesterday, n=4)
        body = f.read_bytes()
        stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.today)          # the open day must stay staged
        res = self.seal()
        self.assertEqual(res, [{"object": f"openmeteo_weather_model/{self.yesterday}", "status": "sealed"}])
        final = f"/earthscape/raw/openmeteo_weather_model/date={self.yesterday}/polls.jsonl"
        self.assertEqual(self.fake.files[final], body)                                       # provider envelopes unmodified
        (rec,) = self.fake.manifest("openmeteo_weather_model")
        self.assertEqual((rec["sha256"], rec["size_bytes"], rec["record_count"]), (hashlib.sha256(body).hexdigest(), len(body), 4))
        self.assertEqual((rec["provider"], rec["value_origin"], rec["utc_day"], rec["status"]), ("Open-Meteo", "modelled", self.yesterday, "ok"))
        self.assertLessEqual(rec["first_retrieved_at"], rec["last_retrieved_at"])
        self.assertFalse(f.exists())
        self.assertTrue((Path(self.tmp.name) / "openmeteo_weather_model" / f"date={self.today}" / "polls.jsonl").exists())
        self.assertEqual([k for k in self.fake.files if "/_tmp/" in k], [])                  # no temporary object left behind

    def test_each_source_has_its_own_partition_and_origin(self):
        stage(self.tmp.name, "openaq_observed/current", "latest.jsonl", self.yesterday)
        stage(self.tmp.name, "openmeteo_airquality_model", "polls.jsonl", self.yesterday)
        self.assertEqual(sorted(r["status"] for r in self.seal()), ["sealed", "sealed"])
        self.assertEqual(self.fake.manifest("openaq_observed")[0]["value_origin"], "observed")
        self.assertIn(f"/earthscape/raw/openaq_observed/current/date={self.yesterday}/latest.jsonl", self.fake.files)
        self.assertEqual(self.fake.manifest("openmeteo_airquality_model")[0]["value_origin"], "modelled")

    def test_second_run_creates_no_duplicate_object_or_manifest_line(self):
        f = stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.yesterday)
        body = f.read_bytes()
        self.seal()
        f.write_bytes(body)                                                                  # staged file reappears (interrupted after the manifest, before the delete)
        self.assertEqual(self.seal()[0]["status"], "already sealed")
        self.assertEqual(len(self.fake.manifest("openmeteo_weather_model")), 1)
        self.assertFalse(f.exists())
        self.assertEqual(self.seal(), [])                                                    # nothing staged: nothing to do

    def test_interruption_before_move_is_recovered_without_loss(self):
        f = stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.yesterday)
        body = f.read_bytes()
        res = self.seal(FakeHdfs(fail=("-mv", 1)))
        self.assertEqual(res[0]["status"], "kept locally")
        self.assertTrue(f.exists())                                                          # staged data is never deleted on failure
        res = self.seal()                                                                    # next cycle completes it
        self.assertEqual(res[0]["status"], "sealed")
        self.assertEqual(self.fake.files[f"/earthscape/raw/openmeteo_weather_model/date={self.yesterday}/polls.jsonl"], body)

    def test_interruption_after_move_before_manifest_is_recovered(self):
        f = stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.yesterday)
        broken = FakeHdfs(fail=("-appendToFile", 1))
        self.assertEqual(self.seal(broken)[0]["status"], "kept locally")
        self.assertTrue(f.exists())
        self.assertEqual(broken.manifest("openmeteo_weather_model"), [])
        self.assertEqual(self.seal(broken)[0]["status"], "sealed")                           # object reused, manifest now written once
        self.assertEqual(len(broken.manifest("openmeteo_weather_model")), 1)

    def test_a_different_existing_object_is_never_overwritten(self):
        f = stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.yesterday)
        final = f"/earthscape/raw/openmeteo_weather_model/date={self.yesterday}/polls.jsonl"
        self.fake.files[final] = b"something else\n"
        res = self.seal()
        self.assertEqual(res[0]["status"], "kept locally")
        self.assertEqual(self.fake.files[final], b"something else\n")
        self.assertTrue(f.exists())

    def test_envelope_outside_its_utc_day_or_corrupt_file_is_not_sealed(self):
        other = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        f = stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.yesterday, text=envelope(other))
        g = stage(self.tmp.name, "openaq_observed/current", "latest.jsonl", self.yesterday, text=envelope(self.yesterday) + '{"retrieved_at": "20')
        self.assertEqual({r["status"] for r in self.seal()}, {"kept locally"})
        self.assertTrue(f.exists() and g.exists())
        self.assertEqual(self.fake.files, {})

    def test_day_that_ended_minutes_ago_waits_for_the_grace_period(self):
        stage(self.tmp.name, "openmeteo_weather_model", "polls.jsonl", self.yesterday)
        just_after_midnight = datetime.now(timezone.utc).replace(hour=0, minute=5, second=0, microsecond=0)
        with mock.patch.object(live, "now", return_value=just_after_midnight):
            self.assertEqual(self.seal(), [])
        just_after_midnight = just_after_midnight.replace(minute=20)
        with mock.patch.object(live, "now", return_value=just_after_midnight):
            self.assertEqual(self.seal()[0]["status"], "sealed")


# ---------- historical reference ----------
def hourly_csv(years, fill=None):
    """Synthetic INTERIM-shaped rows (tests only): temperature = 10 + hour, humidity = 50 + year offset."""
    head = "timestamp_utc,city_id,temperature_2m_c,relative_humidity_2m_pct,precipitation_mm_h,wind_speed_2m_m_s,surface_pressure_kpa,solar_irradiance_mj_hr,missing_vars"
    rows, t = [head], datetime(years[0], 1, 1)
    while t.year <= years[-1]:
        temp = "" if fill == "gap" and t.month == 6 else f"{10 + t.hour:.2f}"
        rows.append(f"{t:%Y-%m-%dT%H:%M:%SZ},karachi,{temp},{50 + (t.year - years[0]):.2f},0.0,2.0,100.0,0.0,")
        t += timedelta(hours=1)
    return rows


def make_ref(years=(2001, 2020), fill=None):
    acc, yrs = reference.accumulate(hourly_csv(years, fill))
    return {"built_at": "t", "window_days": reference.WINDOW_DAYS, "cities": {"karachi": acc}, "inputs": {"karachi": {"years": yrs}}}


class ReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = make_ref()

    def test_window_mean_and_sd_are_computed_from_the_right_hours(self):
        r = reference.reference(self.ref, "karachi", "temperature", datetime(2026, 10, 8, 12, 15))
        self.assertEqual(r["mean"], 22.0)                # 10 + hour 12 in every year/day
        self.assertEqual(r["sd"], 0.0)
        self.assertEqual(r["n"], 15 * 20)                # 15 calendar days x 20 years (no leap day in this window)
        h = reference.reference(self.ref, "karachi", "humidity", datetime(2026, 10, 8, 12))
        self.assertEqual(h["mean"], 59.5)                # years 2001..2020 -> 50..69
        self.assertAlmostEqual(h["sd"], 5.78, places=2)   # sqrt((20**2-1)/12) * sqrt(300/299)

    def test_window_wraps_around_the_year_end_and_includes_feb_29(self):
        jan1 = reference.reference(self.ref, "karachi", "temperature", datetime(2026, 1, 1, 0))
        self.assertEqual(jan1["n"], 15 * 20)             # late-December days count for early January
        feb29 = reference.reference(self.ref, "karachi", "temperature", datetime(2026, 3, 1, 6))
        self.assertEqual(feb29["n"], 14 * 20 + 5)        # Feb 29 exists only in the 5 leap years 2004..2020

    def test_missing_cells_are_skipped_and_too_few_samples_give_no_reference(self):
        gap = make_ref(fill="gap")
        self.assertIsNone(reference.reference(gap, "karachi", "temperature", datetime(2026, 6, 15, 12)))
        self.assertIsNotNone(reference.reference(gap, "karachi", "humidity", datetime(2026, 6, 15, 12)))   # humidity had no gap
        short = make_ref(years=(2001, 2005))   # 75 samples < MIN_SAMPLES
        self.assertIsNone(reference.reference(short, "karachi", "temperature", datetime(2026, 10, 8, 12)))

    def test_compare_flags_difference_units_staleness_and_missing_fields(self):
        now = datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc)

        def rd(minute, temp, unit="°C"):
            return {"observed_at": datetime(2026, 10, 8, 12, minute, tzinfo=timezone.utc), "values": {"temperature_2m": temp},
                    "units": {"temperature_2m": unit}}
        out = reference.compare_city(self.ref, "karachi", [rd(15, 25.0, "C"), rd(30, 99.0, "F")], now)
        self.assertFalse(out["stale"])
        t = out["variables"]["temperature"]
        self.assertEqual(len(t["series"]), 1)             # the reading with another unit is not compared
        self.assertEqual(t["latest"]["difference"], 3.0)
        self.assertEqual(out["variables"]["humidity"]["series"], [])   # no humidity value stored -> nothing invented
        self.assertIsNone(out["variables"]["humidity"]["latest"])
        stale = reference.compare_city(self.ref, "karachi", [rd(15, 25.0, "C")], now + timedelta(hours=3))
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["variables"]["temperature"]["latest"]["assessment"], "stale: not current")
        empty = reference.compare_city(self.ref, "karachi", [], now)
        self.assertTrue(empty["stale"] and empty["latest_age_minutes"] is None)

    def test_describe(self):
        self.assertEqual(reference.describe(0.5), "within the typical range")
        self.assertEqual(reference.describe(-1.5), "below the typical range")
        self.assertEqual(reference.describe(2.5), "well above the typical range")
        self.assertEqual(reference.describe(None), "no reference")


class ComparisonApiTest(AppTestCase):
    def setUp(self):
        super().setUp()
        (self.cache / reference.FILE).unlink(missing_ok=True)       # the cache directory is shared by the test class
        self.addCleanup((self.cache / reference.FILE).unlink, missing_ok=True)

    def reading(self, temp, minutes_ago):
        t = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).replace(second=0, microsecond=0, tzinfo=None)
        return {"source": src.WEATHER, "value_origin": "modelled", "city_id": "karachi", "station_key": "", "observed_at": t, "retrieved_at": t,
                "values": {"temperature_2m": temp, "relative_humidity_2m": 60, "wind_speed_10m": 14.0}, "units": {"temperature_2m": "°C", "relative_humidity_2m": "%", "wind_speed_10m": "km/h"},
                "provider": {"name": "Open-Meteo", "grid_latitude": 24.85, "grid_longitude": 66.99, "elevation_m": 8.0}}

    def test_requires_login_validates_input_and_reports_missing_reference(self):
        self.assertEqual(self.http.get("/api/current/comparison?city=karachi").status_code, 401)
        a = self.analyst()
        self.assertEqual(a.get("/api/current/comparison?city=nowhere").status_code, 400)
        self.assertEqual(a.get("/api/current/comparison?city=karachi&hours=0").status_code, 400)
        r = a.get("/api/current/comparison?city=karachi")
        self.assertEqual(r.status_code, 503)
        self.assertIn("build-reference", r.json()["error"])

    def test_comparison_uses_only_comparable_variables_with_labels(self):
        (self.cache / reference.FILE).write_text(json.dumps(make_ref()), encoding="utf-8")
        live.store_readings(self.db, [self.reading(25.0, 5)])
        j = self.analyst().get("/api/current/comparison?city=karachi").json()
        self.assertEqual(set(j["variables"]), {"temperature", "humidity"})                   # no wind, pressure, precipitation, PM2.5
        self.assertIn("NASA POWER", j["reference_source"])
        self.assertIn("modelled", j["current_source"]["name"])
        self.assertEqual(j["variables"]["temperature"]["unit"], "C")
        self.assertEqual(len(j["variables"]["temperature"]["series"]), 1)
        self.assertFalse(j["stale"])
        html = self.analyst().get("/current").text
        self.assertIn("Current weather versus historical reference", html)
        self.assertIn("not two measurements of one thing", html)


# ---------- monitor ----------
class MonitorTest(unittest.TestCase):
    def make_backup(self, root, stamp, tamper=False):
        d = Path(root) / f"earthscape_{stamp}"
        d.mkdir(parents=True)
        (d / "users.json").write_text("[]")
        sha = hashlib.sha256(b"[]").hexdigest() if not tamper else "0" * 64
        (d / "BACKUP.json").write_text(json.dumps({"created_at": stamp, "database": "x", "files": {"users.json": {"documents": 0, "sha256": sha}}}))
        return d

    def test_backup_check_age_checksum_and_absence(self):
        now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(monitor.check_backup(tmp, now), (False, "no backup found"))
            self.make_backup(tmp, "20261008T020000Z")
            self.assertTrue(monitor.check_backup(tmp, now)[0])
            self.assertFalse(monitor.check_backup(tmp, now + timedelta(days=2))[0])        # too old
        with tempfile.TemporaryDirectory() as tmp:
            self.make_backup(tmp, "20261008T020000Z", tamper=True)
            ok, detail = monitor.check_backup(tmp, now)
            self.assertFalse(ok)
            self.assertIn("checksum", detail)

    def test_run_records_failures_with_timestamps_and_trims_the_log(self):
        settings = types.SimpleNamespace(mongo_uri="mongodb://localhost:27017", mongo_db="earthscape_monitor_test")
        now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(monitor, "check_app", return_value=(False, "not reachable (URLError)", None)), \
                mock.patch.object(monitor, "check_hdfs", return_value=(False, "HDFS not reachable")), \
                mock.patch.object(monitor, "check_mongo", return_value=(True, "ping ok", mock.Mock())), \
                mock.patch.object(monitor, "check_polling", return_value=(True, "fresh")):
            log = Path(tmp) / "logs" / "health.jsonl"
            rec = monitor.run(settings, backup_root=Path(tmp) / "none", log_path=log, now=now)
            self.assertFalse(rec["ok"])
            self.assertEqual(rec["failures"], ["application", "hdfs", "backup"])   # disk is fine here
            self.assertEqual(rec["checked_at"], "2026-10-08T12:00:00+00:00")
            with mock.patch.object(monitor, "LOG_KEEP", 3):
                for _ in range(5):
                    monitor.run(settings, backup_root=Path(tmp) / "none", log_path=log, now=now)
            self.assertEqual(len(log.read_text().splitlines()), 3)
            self.assertEqual(json.loads(log.read_text().splitlines()[-1])["failures"], ["application", "hdfs", "backup"])

    def test_resource_metrics_are_recorded_and_extremes_flagged(self):
        ok, detail, m = monitor.check_resources()
        self.assertTrue(0 <= m["cpu_percent"] <= 100 and 0 < m["memory_percent"] <= 100)
        self.assertIn("memory", detail)
        self.assertFalse(monitor.check_resources(max_cpu=-1)[0])                              # a threshold below any reading must fail the check

    def test_mongo_down_skips_polling_check_without_crashing(self):
        settings = types.SimpleNamespace(mongo_uri="mongodb://127.0.0.1:1", mongo_db="x")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(monitor, "check_app", return_value=(True, "ok", {})), \
                mock.patch.object(monitor, "check_hdfs", return_value=(True, "ok")):
            rec = monitor.run(settings, backup_root=tmp, log_path=Path(tmp) / "h.jsonl")
        self.assertIn("mongodb", rec["failures"])
        self.assertIn("live_polling", rec["failures"])


class ErrorPageTest(AppTestCase):
    def test_unknown_route_gives_the_html_error_page_not_raw_json(self):
        for client in (self.http, self.analyst()):
            r = client.get("/no-such-page")
            self.assertEqual(r.status_code, 404)
            self.assertIn("text/html", r.headers["content-type"])
            self.assertIn("Page not found", r.text)
        self.assertEqual(self.http.get("/api/no-such").json(), {"error": "Page not found."})


class BackupRetentionAndRestoreTest(AppTestCase):
    def test_prune_keeps_newest_and_ignores_foreign_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            for s in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
                MonitorTest.make_backup(None, tmp, s)
            (Path(tmp) / "earthscape_notes").mkdir()
            (Path(tmp) / "earthscape_20260101T000000Z_manual").mkdir()
            self.assertEqual(manage.prune_backups(tmp, 2), ["earthscape_20260101T000000Z"])
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),
                             ["earthscape_20260101T000000Z_manual", "earthscape_20260102T000000Z", "earthscape_20260103T000000Z", "earthscape_notes"])
            self.assertEqual(manage.prune_backups(tmp, 0), [])                              # keep < 1 never deletes

    def test_restore_into_a_separate_database_leaves_the_source_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = manage.backup(self.db, tmp, False)
            target = self.client_db[self.dbname + "_copy"]
            self.addCleanup(self.client_db.drop_database, self.dbname + "_copy")
            manage.restore(target, out, False)
            self.assertEqual(target.users.count_documents({}), 2)
            self.assertEqual(self.db.users.count_documents({}), 2)
            self.assertEqual(sorted(u["username"] for u in target.users.find()), ["admin", "analyst"])


# ---------- MODIS reader on a synthetic HDF4-EOS file (test fixture only: NOT satellite data) ----------
def write_fixture(path, h, v, row, col, corner_shift=0.0):
    from pyhdf.SD import SD, SDC
    sd = SD(str(path), SDC.WRITE | SDC.CREATE)
    x, y = modis.X0 + h * modis.TILE_M + corner_shift, modis.Y0 - v * modis.TILE_M
    setattr(sd, "StructMetadata.0", chr(10).join(["GROUP=GridStructure", f" UpperLeftPointMtrs=({x:.6f},{y:.6f})", "END_GROUP=GridStructure", "END"]))
    lst, qc = np.zeros((1200, 1200), np.uint16), np.zeros((1200, 1200), np.uint8)
    lst[row - 1:row + 2, col - 1:col + 2] = [[15000, 15100, 0], [15200, 15300, 15400], [15500, 15600, 15700]]   # 0 = fill value
    qc[row - 1:row + 2, col - 1:col + 2] = [[0, 1, 3], [0, 0, 2], [0, 1, 0]]                                      # bits 0-1: flag
    for name, arr, typ in (("LST_Day_1km", lst, SDC.UINT16), ("LST_Night_1km", lst // 2, SDC.UINT16), ("QC_Day", qc, SDC.UINT8), ("QC_Night", qc, SDC.UINT8)):
        sds = sd.create(name, typ, (1200, 1200))
        if name.startswith("LST"):
            sds.setfillvalue(0)
            sds.scale_factor = 0.02
        sds[:] = arr
        sds.endaccess()
    sd.end()


class ModisReaderTest(unittest.TestCase):
    def test_scale_fill_quality_flags_and_geolocation(self):
        lat, lon = 24.8608, 67.0104                                        # Karachi -> tile h24v06
        h, v, row, col = modis.tile_pixel(lat, lon)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.hdf"
            write_fixture(path, h, v, row, col)
            r = modis.read_lst(path, lat, lon)
            self.assertEqual((h, v), (24, 6))
            valid = [15000, 15100, 15200, 15300, 15500, 15600, 15700]   # fill 0, QC flag 3 and QC flag 2 pixels are excluded
            self.assertEqual(r["lst_day_valid_pixels"], 7)
            self.assertAlmostEqual(r["lst_day_k"], round(sum(valid) / 7 * 0.02, 2), places=2)
            self.assertEqual(r["lst_day_good_quality_pixels"], 5)            # flag 0 and not fill
            self.assertEqual(r["lst_day_cloud_pixels"], 1)
            self.assertEqual(r["lst_day_scale_factor"], 0.02)
            self.assertGreater(r["lst_day_k"], 273.15)                       # plausible Kelvin, not scaled integers
            wrong = Path(tmp) / "wrong_tile.hdf"
            write_fixture(wrong, h, v, row, col, corner_shift=5000.0)
            with self.assertRaises(modis.ModisError):
                modis.read_lst(wrong, lat, lon)

    def test_window_without_valid_pixels_is_none_not_filled(self):
        lat, lon = 24.8608, 67.0104
        h, v, row, col = modis.tile_pixel(lat, lon)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.hdf"
            write_fixture(path, h, v, row, col)
            r = modis.read_lst(path, lat, lon, half=0)                       # centre pixel only
            self.assertEqual(r["lst_day_valid_pixels"], 1)
            r2 = modis.read_lst(path, lat + 0.5, lon, half=0)                # a pixel outside the fixture's populated window
            self.assertIsNone(r2["lst_day_k"])
            self.assertEqual(r2["lst_day_valid_pixels"], 0)


if __name__ == "__main__":
    unittest.main()
