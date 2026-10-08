import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "processing" / "batch"))
import power_interim as p  # noqa: E402

SAMPLE = """-BEGIN HEADER-
NASA/POWER Source Native Resolution Hourly Data
Dates (month/day/year): 01/01/2001 through 12/31/2001 in UTC
Location: Latitude  24.8608   Longitude 67.0104
Elevation from MERRA-2: Average for 0.5 x 0.625 degree lat/lon region = 53.05 meters
Parameter(s):
T2M                   MERRA-2 Temperature at 2 Meters (C)
RH2M                  MERRA-2 Relative Humidity at 2 Meters (%)
PRECTOTCORR           MERRA-2 Precipitation Corrected (mm/hour)
WS2M                  MERRA-2 Wind Speed at 2 Meters (m/s)
PS                    MERRA-2 Surface Pressure (kPa)
ALLSKY_SFC_SW_DWN     CERES SYN1deg All Sky Surface Shortwave Downward Irradiance (MJ/hr)
-END HEADER-
YEAR,MO,DY,HR,T2M,RH2M,PRECTOTCORR,WS2M,PS,ALLSKY_SFC_SW_DWN
2001,1,1,0,16.65,83.51,0.0,2.5,101.0,0.0
"""


def all_raw_rows():
    when, rows = datetime(2001, 1, 1), []
    while when.year < 2026:
        rows.append([str(when.year), str(when.month), str(when.day), str(when.hour), "1.5", "50.0", "0.0", "2.0", "101.0", "0.0"])
        when += timedelta(hours=1)
    return rows


class InterimTests(unittest.TestCase):
    def test_normal_row_keeps_values_verbatim_and_builds_utc_timestamp(self):
        out = p.convert_row(["2004", "2", "29", "7", "16.65", "83.51", "0.0", "2.5", "101.0", "0.08"], "karachi")
        self.assertEqual(out, ["2004-02-29T07:00:00Z", "karachi", "16.65", "83.51", "0.0", "2.5", "101.0", "0.08", ""])

    def test_fill_value_becomes_empty_and_is_named(self):
        out = p.convert_row(["2001", "1", "1", "0", "-999", "50.0", "0.0", "2.0", "101.0", "-999.0"], "karachi")
        self.assertEqual(out[2], "")
        self.assertEqual(out[7], "")
        self.assertEqual(out[8], "temperature_2m_c;solar_irradiance_mj_hr")
        self.assertNotIn("-999", ",".join(out))

    def test_schema_is_the_nine_approved_columns(self):
        self.assertEqual(p.HEADER, "timestamp_utc,city_id,temperature_2m_c,relative_humidity_2m_pct,"
                         "precipitation_mm_h,wind_speed_2m_m_s,surface_pressure_kpa,solar_irradiance_mj_hr,missing_vars")
        self.assertEqual(len(p.convert_row(["2001", "1", "1", "0"] + ["1"] * 6, "karachi")), 9)

    def test_parse_raw_reads_units_and_location(self):
        info, rows = p.parse_raw(SAMPLE)
        self.assertEqual(info["params"]["PRECTOTCORR"]["unit"], "mm/hour")
        self.assertEqual(info["params"]["ALLSKY_SFC_SW_DWN"]["unit"], "MJ/hr")
        self.assertEqual((info["latitude"], info["elevation_m"]), (24.8608, 53.05))
        self.assertEqual(len(rows), 1)

    def test_full_span_passes_with_leap_days_and_detects_gap_and_value_change(self):
        raw_rows = all_raw_rows()
        self.assertEqual(len(raw_rows), 219144)
        lines = [",".join(p.convert_row(f, "karachi")) for f in raw_rows]
        text = "\n".join([p.HEADER] + lines) + "\n"
        self.assertEqual(p.validate_output(text, raw_rows, "karachi"),
                         (219144, 0, "2001-01-01T00:00:00Z", "2025-12-31T23:00:00Z"))
        gap = "\n".join([p.HEADER] + lines[:100] + lines[101:]) + "\n"
        with self.assertRaises(p.raw.IngestError):
            p.validate_output(gap, raw_rows[:100] + raw_rows[101:], "karachi")
        changed = "\n".join([p.HEADER, lines[0].replace(",1.5,", ",1.6,", 1)] + lines[1:]) + "\n"
        with self.assertRaises(p.raw.IngestError):
            p.validate_output(changed, raw_rows, "karachi")

    def test_is_current_requires_same_version_and_identical_inputs(self):
        inputs = [{"hdfs_path": "a", "sha256": "x", "record_count": 1}]
        prior = {"status": "ok", "transform_version": p.VERSION, "inputs": inputs}
        self.assertTrue(p.is_current(prior, inputs))
        self.assertFalse(p.is_current({**prior, "transform_version": "v0"}, inputs))
        self.assertFalse(p.is_current(prior, [{**inputs[0], "sha256": "y"}]))
        self.assertFalse(p.is_current({**prior, "status": "failed"}, inputs))
        self.assertFalse(p.is_current(None, inputs))


if __name__ == "__main__":
    unittest.main()
