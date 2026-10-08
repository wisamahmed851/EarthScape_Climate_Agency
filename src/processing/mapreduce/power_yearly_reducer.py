#!/usr/bin/env python3
"""Hadoop Streaming reducer: all daily rows of one city-year -> one 15-field extreme-event/annual row (power_yearly_v1).

Counts use standard ETCCDI-style index thresholds on the daily values: wet day >= 1 mm, heavy >= 10 mm, very heavy >= 20 mm,
hot day = daily maximum of HOURLY values >= 35 C, frost day = daily minimum of hourly values < 0 C. Days with an empty
precipitation total are not counted in any precipitation index and are reported in `days_with_precip_total`. Annual
precipitation is empty unless every calendar day of the year has a valid total. Mean temperature is weighted by valid
hours. Single reducer only (it writes the header).
"""
import calendar
import sys
from decimal import Decimal, ROUND_HALF_UP

HEADER = ("city_id,year,days_observed,hours_observed,temperature_2m_mean_c,temperature_2m_min_c,temperature_2m_max_c,"
          "precipitation_total_mm,max_daily_precipitation_mm,days_with_precip_total,wet_days_ge_1mm,"
          "heavy_precip_days_ge_10mm,very_heavy_precip_days_ge_20mm,hot_days_max_ge_35c,frost_days_min_lt_0c")
Q = Decimal("0.0001")
# Daily value fields after the mapper drops city_id and date.
HOURS, T_MEAN, T_MIN, T_MAX, PRECIP, T_MISS = 0, 1, 2, 3, 5, 9


def fmt(d):
    return format(d, "f")


def reduce_year(key, days):
    """days: list of 15-item lists of daily field text. Returns the yearly CSV row."""
    city, year = key.split("|")
    expected_days = 366 if calendar.isleap(int(year)) else 365
    num = den = Decimal(0)
    for d in days:
        valid = int(d[HOURS]) - int(d[T_MISS])
        if (d[T_MEAN] == "") != (valid == 0):
            raise ValueError(f"daily mean/valid-hours mismatch: {d}")
        if valid:
            num += Decimal(d[T_MEAN]) * valid
            den += valid
    mins = [Decimal(d[T_MIN]) for d in days if d[T_MIN] != ""]
    maxs = [Decimal(d[T_MAX]) for d in days if d[T_MAX] != ""]
    totals = [Decimal(d[PRECIP]) for d in days if d[PRECIP] != ""]
    annual = fmt(sum(totals)) if len(days) == expected_days and len(totals) == expected_days else ""
    out = [city, year, len(days), sum(int(d[HOURS]) for d in days),
           fmt((num / den).quantize(Q, ROUND_HALF_UP)) if den else "", fmt(min(mins)) if mins else "", fmt(max(maxs)) if maxs else "",
           annual, fmt(max(totals)) if totals else "", len(totals),
           sum(t >= 1 for t in totals), sum(t >= 10 for t in totals), sum(t >= 20 for t in totals),
           sum(m >= 35 for m in maxs), sum(m < 0 for m in mins)]
    return ",".join(str(x) for x in out)


def main():
    print(HEADER)
    key, days = None, []
    for line in sys.stdin:
        k, _, val = line.rstrip("\n").partition("\t")
        if k != key:
            if key is not None:
                print(reduce_year(key, days))
            key, days = k, []
        days.append(val.split(","))
    if key is not None:
        print(reduce_year(key, days))


if __name__ == "__main__":
    main()
