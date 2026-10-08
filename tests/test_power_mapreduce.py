import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "src" / "processing" / "mapreduce"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src" / "ingestion"))
import power_daily_mapper as dm
import power_daily_reducer as dr
import power_monthly_mapper as mm
import power_monthly_reducer as mr
import run_power_job as job


def hourly(day, hour, t="20.0", rh="50.0", p="0.1", w="2.0", ps="100.0", s="1.0"):
    return f"{day}T{hour:02d}:00:00Z,karachi,{t},{rh},{p},{w},{ps},{s},"


def shuffle(mapper, lines):
    """Imitate map + shuffle/sort: mapper output grouped by key, sorted by key"""
    groups = {}
    for line in lines:
        out = mapper(line)
        if out:
            groups.setdefault(out[0], []).append(out[1].split(","))
    return sorted(groups.items())


class DailyTest(unittest.TestCase):
    def day(self, override):
        lines = ["timestamp_utc,city_id,temperature_2m_c,relative_humidity_2m_pct,precipitation_mm_h,"
                 "wind_speed_2m_m_s,surface_pressure_kpa,solar_irradiance_mj_hr,missing_vars"]
        lines += [hourly("2001-01-01", h, **override.get(h, {})) for h in range(24)]
        return [dr.reduce_day(k, v) for k, v in shuffle(dm.map_line, lines)]

    def test_header_skipped_and_key(self):
        self.assertIsNone(dm.map_line("timestamp_utc,city_id,a,b,c,d,e,f,missing_vars"))
        self.assertEqual(dm.map_line(hourly("2001-01-01", 5)), ("karachi|2001-01-01", "20.0,50.0,0.1,2.0,100.0,1.0"))

    def test_malformed_row_fails(self):
        with self.assertRaises(ValueError):
            dm.map_line("2001-01-01T00:00:00Z,karachi,1")

    def test_full_day(self):
        [(row, incomplete)] = self.day({0: {"t": "10.0"}, 1: {"t": "30.5", "p": "0.25"}})
        f = row.split(",")
        self.assertEqual((f[0], f[1], f[2]), ("karachi", "2001-01-01", "24"))
        self.assertEqual(f[3:6], ["20.0208", "10.0", "30.5"])
        self.assertEqual(f[7], "2.55")
        self.assertEqual(f[11:], ["0"] * 6)
        self.assertEqual(len(f), 17)
        self.assertFalse(incomplete)

    def test_missing_precipitation_gives_empty_total_but_valid_means(self):
        [(row, incomplete)] = self.day({3: {"p": ""}, 4: {"t": ""}})
        f = row.split(",")
        self.assertEqual(f[7], "")
        self.assertEqual(f[13], "1")
        self.assertEqual(f[11], "1")
        self.assertEqual(f[3], "20.0000")
        self.assertTrue(incomplete)

    def test_partial_day_has_no_precipitation_total(self):
        lines = [hourly("2001-01-02", h) for h in range(23)]
        [(row, incomplete)] = [dr.reduce_day(k, v) for k, v in shuffle(dm.map_line, lines)]
        self.assertEqual(row.split(",")[2:8], ["23", "20.0000", "20.0", "20.0", "50.0000", ""])
        self.assertTrue(incomplete)

    def test_all_missing_variable_is_empty_not_zero(self):
        [(row, _)] = self.day({h: {"s": ""} for h in range(24)})
        f = row.split(",")
        self.assertEqual((f[10], f[16]), ("", "24"))

    def test_rounding_half_up_and_negative(self):
        self.assertEqual(dr.mean([dr.Decimal("0.00005")]), "0.0001")
        self.assertEqual(dr.mean([dr.Decimal("-0.00005")]), "-0.0001")

    def test_independent_rounding_matches_reducer(self):
        from fractions import Fraction
        for text in ("0.00005", "-0.00005", "1.23455", "-3.5", "0"):
            self.assertEqual(job.r4(Fraction(text)), dr.mean([dr.Decimal(text)]))


class MonthlyTest(unittest.TestCase):
    @staticmethod
    def daily(day, hours=24, tmean="20.0000", missing=0, precip="2.4"):
        valid = hours - missing
        return (f"karachi,{day},{hours},{tmean},15.0,25.0,50.0000,{precip},2.0000,100.0000,1.0000,"
                f"{missing},0,{0 if precip else 24},0,0,0")

    def month(self, lines):
        return [mr.reduce_month(k, v) for k, v in shuffle(mm.map_line, lines)]

    def test_header_skipped_and_key(self):
        self.assertIsNone(mm.map_line("city_id,date,..."))
        self.assertEqual(mm.map_line(self.daily("2001-02-03"))[0], "karachi|2001-02")

    def test_full_month(self):
        rows = [self.daily(f"2001-02-{d:02d}") for d in range(1, 29)]
        [row] = self.month(rows)
        f = row.split(",")
        self.assertEqual(f[:4], ["karachi", "2001-02", "28", "672"])
        self.assertEqual(f[4:9], ["20.0000", "15.0", "25.0", "50.0000", "67.2"])
        self.assertEqual(f[12:], ["0"] * 6)
        self.assertEqual(len(f), 18)

    def test_means_are_weighted_by_valid_hours(self):
        rows = [self.daily("2001-02-01", tmean="10.0000"), self.daily("2001-02-02", hours=12, tmean="30.0000")]
        f = self.month(rows)[0].split(",")
        self.assertEqual(f[4], "16.6667")

    def test_missing_hours_reduce_weight(self):
        rows = [self.daily("2001-02-01", tmean="10.0000", missing=12), self.daily("2001-02-02", tmean="30.0000")]
        f = self.month(rows)[0].split(",")
        self.assertEqual((f[4], f[12]), ("23.3333", "12"))

    def test_incomplete_month_or_missing_day_precipitation_is_empty(self):
        rows = [self.daily(f"2001-02-{d:02d}") for d in range(1, 28)]
        self.assertEqual(self.month(rows)[0].split(",")[8], "")
        rows = [self.daily(f"2001-02-{d:02d}", precip="" if d == 5 else "2.4") for d in range(1, 29)]
        self.assertEqual(self.month(rows)[0].split(",")[8], "")

    def test_inconsistent_daily_mean_is_rejected(self):
        with self.assertRaises(ValueError):
            self.month([self.daily("2001-02-01", tmean="")])


class DriverTest(unittest.TestCase):
    def test_parse_log(self):
        log = ("INFO mapreduce.Job: Running job: job_1791_0007\n"
               "INFO mapreduce.Job: Counters: 2\n\tMap-Reduce Framework\n\t\tMap input records=1095725\n"
               "\tEarthScape\n\t\tincomplete_days=3\nINFO streaming.StreamJob: Output directory: x\n")
        jid, app, counters = job.parse_log(log)
        self.assertEqual((jid, app), ("job_1791_0007", "application_1791_0007"))
        self.assertEqual(counters["Map-Reduce Framework"]["Map input records"], 1095725)
        self.assertEqual(counters["EarthScape"]["incomplete_days"], 3)

    def test_verify_daily_detects_changed_value(self):
        texts = {c: [] for c in job.CITIES}
        with self.assertRaises(job.raw.IngestError):
            job.verify_daily(dr.HEADER + "\n", texts)


if __name__ == "__main__":
    unittest.main()
