import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "ingestion"))
import nasa_power_batch as b  # noqa: E402

CITIES = ["karachi", "lahore", "islamabad", "peshawar", "quetta"]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class BatchTests(unittest.TestCase):
    def test_work_units_are_125_with_year_boundaries(self):
        units = b.work_units(CITIES, 2001, 2025)
        self.assertEqual(len(units), 125)
        self.assertEqual(len(set(units)), 125)
        self.assertEqual({y for _, y in units}, set(range(2001, 2026)))
        self.assertIn(("quetta", 2025), units)
        self.assertNotIn(("karachi", 2000), units)

    def test_expected_rows_handle_leap_years(self):
        self.assertEqual(b.expected_rows(2001), 8760)
        self.assertEqual(b.expected_rows(2004), 8784)
        self.assertEqual(b.expected_rows(1900), 8760)
        self.assertEqual(sum(b.expected_rows(y) for y in range(2001, 2026)) * 5, 1_095_720)

    def test_resume_skips_done_and_collects_failures_and_continues(self):
        done = {("karachi", 2001)}

        def fake(city, year):
            if (city, year) in done:
                return {"result": "skipped"}
            if (city, year) == ("karachi", 2003):
                raise RuntimeError("HTTP 422")
            return {"result": "ingested", "attempts": 2 if year == 2004 else 1}

        units = b.work_units(["karachi"], 2001, 2005)
        slept = []
        results, left = b.run_batch(units, fake, delay=0.5, sleep=slept.append)
        s = b.summarize(units, results, left, NOW, NOW)
        self.assertEqual((s["ingested"], s["skipped"], s["failed"], s["not_attempted"]), (3, 1, 1, 0))
        self.assertEqual(s["failures"], [{"city": "karachi", "year": 2003, "reason": "HTTP 422"}])
        self.assertEqual(s["max_attempts_seen"], 2)
        self.assertEqual(len(slept), 3)  # delay only after real downloads, not skips or failures

    def test_aborts_after_consecutive_failures(self):
        def always_fail(city, year):
            raise RuntimeError("hdfs down")

        units = b.work_units(["karachi"], 2001, 2025)
        results, left = b.run_batch(units, always_fail, sleep=lambda s: None)
        self.assertEqual(len(results), b.MAX_CONSECUTIVE_FAILURES + 1)
        self.assertEqual(len(results) + len(left), 25)

    def test_exit_code_nonzero_when_unresolved(self):
        ok = {"failed": 0, "not_attempted": 0, "verification": {"problems": []}}
        self.assertEqual(b.exit_code(ok), 0)
        self.assertEqual(b.exit_code({**ok, "failed": 1}), 1)
        self.assertEqual(b.exit_code({**ok, "not_attempted": 2}), 1)
        self.assertEqual(b.exit_code({**ok, "verification": {"problems": ["missing"]}}), 1)


if __name__ == "__main__":
    unittest.main()
