#!/usr/bin/env python3
"""Hadoop Streaming reducer: all hourly values of one city-day -> one 17-field daily row (power_daily_v1).

Exact Decimal sums (shuffle order is not fixed); means rounded half-up to 4 decimals; empty hourly values are
excluded and counted, never zero. Precipitation total is empty unless all 24 hours are valid. Single reducer only
(it writes the header).
"""
import sys
from decimal import Decimal, ROUND_HALF_UP

HEADER = ("city_id,date,hours_observed,temperature_2m_mean_c,temperature_2m_min_c,temperature_2m_max_c,"
          "relative_humidity_2m_mean_pct,precipitation_total_mm,wind_speed_2m_mean_m_s,surface_pressure_mean_kpa,"
          "solar_irradiance_mean_mj_hr,temperature_2m_missing_hours,relative_humidity_2m_missing_hours,"
          "precipitation_missing_hours,wind_speed_2m_missing_hours,surface_pressure_missing_hours,"
          "solar_irradiance_missing_hours")
Q = Decimal("0.0001")
T, RH, P, W, PS, S = range(6)


def mean(v):
    return format((sum(v) / len(v)).quantize(Q, ROUND_HALF_UP), "f") if v else ""


def reduce_day(key, rows):
    """rows: list of six-item lists of hourly value text (empty = missing). Returns the daily CSV row."""
    city, day = key.split("|")
    n = len(rows)
    v = [[Decimal(x) for x in col if x != ""] for col in zip(*rows)]
    miss = [n - len(c) for c in v]
    extreme = lambda f, c: format(f(c), "f") if c else ""
    precip = format(sum(v[P]), "f") if n == 24 and len(v[P]) == 24 else ""
    out = [city, day, n, mean(v[T]), extreme(min, v[T]), extreme(max, v[T]), mean(v[RH]), precip,
           mean(v[W]), mean(v[PS]), mean(v[S]), miss[T], miss[RH], miss[P], miss[W], miss[PS], miss[S]]
    return ",".join(str(x) for x in out), n != 24 or any(miss)


def main():
    print(HEADER)
    key, rows = None, []

    def flush():
        line, incomplete = reduce_day(key, rows)
        print(line)
        if incomplete:
            print("reporter:counter:EarthScape,incomplete_days,1", file=sys.stderr)

    for line in sys.stdin:
        k, _, val = line.rstrip("\n").partition("\t")
        if k != key:
            if key is not None:
                flush()
            key, rows = k, []
        rows.append(val.split(","))
    if key is not None:
        flush()


if __name__ == "__main__":
    main()
