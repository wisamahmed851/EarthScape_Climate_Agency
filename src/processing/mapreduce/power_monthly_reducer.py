#!/usr/bin/env python3
"""Hadoop Streaming reducer: all daily rows of one city-month -> one 18-field monthly row (power_monthly_v1).

Monthly means weight each daily mean by that day's valid hours (hours_observed - missing hours), so days with
less coverage count less. Daily means are already rounded to 4 decimals, so a monthly mean can differ from the
mean of the hourly values by at most 0.0001. Min/max are the lowest/highest hourly value of the month. Precipitation
total is empty unless every calendar day of the month is present with a valid daily total. Single reducer only.
"""
import calendar
import sys
from decimal import Decimal, ROUND_HALF_UP

HEADER = ("city_id,month,days_observed,hours_observed,temperature_2m_mean_c,temperature_2m_min_c,"
          "temperature_2m_max_c,relative_humidity_2m_mean_pct,precipitation_total_mm,wind_speed_2m_mean_m_s,"
          "surface_pressure_mean_kpa,solar_irradiance_mean_mj_hr,temperature_2m_missing_hours,"
          "relative_humidity_2m_missing_hours,precipitation_missing_hours,wind_speed_2m_missing_hours,"
          "surface_pressure_missing_hours,solar_irradiance_missing_hours")
Q = Decimal("0.0001")
# Daily value fields after the mapper drops city_id and date (index 0 = hours_observed).
HOURS, T_MEAN, T_MIN, T_MAX, RH, PRECIP, WIND, PRESS, SOLAR = range(9)
MISS = {T_MEAN: 9, RH: 10, PRECIP: 11, WIND: 12, PRESS: 13, SOLAR: 14}


def weighted_mean(days, col):
    """Mean of daily means weighted by valid hours; empty when no valid hour exists."""
    num = den = Decimal(0)
    for d in days:
        valid = int(d[HOURS]) - int(d[MISS[col]])
        if (d[col] == "") != (valid == 0):
            raise ValueError(f"daily mean/valid-hours mismatch in column {col}: {d}")
        if valid:
            num += Decimal(d[col]) * valid
            den += valid
    return format((num / den).quantize(Q, ROUND_HALF_UP), "f") if den else ""


def reduce_month(key, days):
    """days: list of 15-item lists of daily field text. Returns the monthly CSV row."""
    city, month = key.split("|")
    year, mon = int(month[:4]), int(month[5:])
    expected_days = calendar.monthrange(year, mon)[1]
    mins = [Decimal(d[T_MIN]) for d in days if d[T_MIN] != ""]
    maxs = [Decimal(d[T_MAX]) for d in days if d[T_MAX] != ""]
    totals = [d[PRECIP] for d in days]
    precip = format(sum(Decimal(t) for t in totals), "f") if len(days) == expected_days and "" not in totals else ""
    out = [city, month, len(days), sum(int(d[HOURS]) for d in days),
           weighted_mean(days, T_MEAN), format(min(mins), "f") if mins else "", format(max(maxs), "f") if maxs else "",
           weighted_mean(days, RH), precip, weighted_mean(days, WIND), weighted_mean(days, PRESS),
           weighted_mean(days, SOLAR)]
    out += [sum(int(d[MISS[c]]) for d in days) for c in (T_MEAN, RH, PRECIP, WIND, PRESS, SOLAR)]
    return ",".join(str(x) for x in out)


def main():
    print(HEADER)
    key, days = None, []
    for line in sys.stdin:
        k, _, val = line.rstrip("\n").partition("\t")
        if k != key:
            if key is not None:
                print(reduce_month(key, days))
            key, days = k, []
        days.append(val.split(","))
    if key is not None:
        print(reduce_month(key, days))


if __name__ == "__main__":
    main()
