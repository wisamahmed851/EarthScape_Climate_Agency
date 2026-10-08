import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "ingestion"))
import nasa_power as np_  # noqa: E402

VARS = ["T2M", "RH2M"]


def make_csv(start=date(2001, 1, 1), end=date(2001, 1, 2), lat=24.8608, lon=67.0104, drop_row=None, cols=None):
    """Synthetic test fixture in the POWER CSV layout (not data)."""
    from datetime import datetime, timedelta
    head = [
        "-BEGIN HEADER-", "NASA/POWER Source Native Resolution Hourly Data ",
        f"Dates (month/day/year): {start:%m/%d/%Y} through {end:%m/%d/%Y} in UTC",
        f"Location: Latitude  {lat}   Longitude {lon} ", "Parameter(s): ",
        "T2M                   MERRA-2 Temperature at 2 Meters (C) ",
        "RH2M                  MERRA-2 Relative Humidity at 2 Meters (%) ", "-END HEADER-",
        cols or "YEAR,MO,DY,HR,T2M,RH2M",
    ]
    rows, t = [], datetime(start.year, start.month, start.day)
    while t < datetime(end.year, end.month, end.day) + timedelta(days=1):
        rows.append(f"{t.year},{t.month},{t.day},{t.hour},10.5,-999.0")
        t += timedelta(hours=1)
    if drop_row is not None:
        del rows[drop_row]
    return "\n".join(head + rows) + "\n"


class ValidateTests(unittest.TestCase):
    def check(self, text, **kw):
        a = dict(lat=24.8608, lon=67.0104, start=date(2001, 1, 1), end=date(2001, 1, 2), variables=VARS)
        a.update(kw)
        return np_.validate(text, **a)

    def test_good_response_counts_rows_and_keeps_fill_values(self):
        self.assertEqual(self.check(make_csv()), (48, 48))

    def test_error_payload_rejected(self):
        with self.assertRaises(np_.IngestError):
            self.check('{"header":"The POWER Hourly API failed","messages":["bad"]}')

    def test_wrong_location_rejected(self):
        with self.assertRaises(np_.IngestError):
            self.check(make_csv(lat=31.0))

    def test_missing_hour_rejected(self):
        with self.assertRaises(np_.IngestError):
            self.check(make_csv(drop_row=5))

    def test_wrong_columns_rejected(self):
        with self.assertRaises(np_.IngestError):
            self.check(make_csv(cols="YEAR,MO,DY,HR,RH2M,T2M"))

    def test_period_mismatch_rejected(self):
        with self.assertRaises(np_.IngestError):
            self.check(make_csv(), end=date(2001, 1, 3))


class PathAndManifestTests(unittest.TestCase):
    def test_full_year_path(self):
        self.assertEqual(
            np_.raw_path("karachi", date(2001, 1, 1), date(2001, 12, 31)),
            "/earthscape/raw/power_hourly_gridded/city=karachi/year=2001/power_hourly_karachi_2001.csv")

    def test_partial_range_path_is_deterministic(self):
        p = np_.raw_path("lahore", date(2024, 1, 1), date(2024, 1, 5))
        self.assertEqual(p, np_.raw_path("lahore", date(2024, 1, 1), date(2024, 1, 5)))
        self.assertTrue(p.endswith("power_hourly_lahore_20240101_20240105.csv"))

    def test_range_across_years_rejected(self):
        with self.assertRaises(np_.IngestError):
            np_.raw_path("lahore", date(2001, 12, 1), date(2002, 1, 1))

    def test_last_record_wins(self):
        m = '{"object_key":"a","status":"started"}\n{"object_key":"b","status":"ok"}\n{"object_key":"a","status":"ok"}\n'
        self.assertEqual(np_.last_record(m, "a")["status"], "ok")
        self.assertIsNone(np_.last_record(m, "c"))

    def test_sha256(self):
        p = Path(__file__).with_name("_sha_tmp.txt")
        p.write_bytes(b"abc")
        try:
            self.assertEqual(np_.sha256_of(p), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        finally:
            p.unlink()


if __name__ == "__main__":
    unittest.main()
