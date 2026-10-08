"""Climate analytics on the verified POWER PROCESSED daily/monthly data (reanalysis-derived, not station observations).

Four analyses, all chronological and reproducible (seed 42):
  trend       Theil-Sen slope + Mann-Kendall test on annual values (2001-2025)
  anomalies   daily temperature vs a day-of-year climatology fitted on 2001-2015 only; robust z-score (> 3.5) and an
              Isolation Forest fitted on the same training years; both applied to 2016-2025
  correlation Pearson/Spearman between deseasonalised daily anomalies (descriptive; association, not causation)
  forecast    monthly means, train 2001-2020 / test 2021-2025; seasonal-climatology and last-year baselines vs a
              harmonic + linear-trend Ridge; the forward model is picked by rolling-origin validation (2011-2020) INSIDE the training years
Run: python src/app/manage.py train-ml
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import Ridge

VERSION = "climate_ml_v1"
SEED = 42
TRAIN_END_YEAR, ANOM_TRAIN_END = 2020, 2015
Z_LIMIT = 3.5
VARS = {  # label -> (daily column, monthly column, unit)
    "temperature": ("temperature_2m_mean_c", "temperature_2m_mean_c", "C"),
    "humidity": ("relative_humidity_2m_mean_pct", "relative_humidity_2m_mean_pct", "%"),
    "precipitation": ("precipitation_total_mm", "precipitation_total_mm", "mm/month"),
    "wind": ("wind_speed_2m_mean_m_s", "wind_speed_2m_mean_m_s", "m/s"),
}
CORR_COLS = {"temperature": "temperature_2m_mean_c", "humidity": "relative_humidity_2m_mean_pct", "precipitation": "precipitation_total_mm",
             "wind": "wind_speed_2m_mean_m_s", "pressure": "surface_pressure_mean_kpa", "solar": "solar_irradiance_mean_mj_hr"}


def load(cache_dir):
    d = pd.read_csv(Path(cache_dir) / "daily.csv", parse_dates=["date"])
    m = pd.read_csv(Path(cache_dir) / "monthly.csv")
    m["period"] = pd.PeriodIndex(m["month"], freq="M")
    return d, m


def clean(x):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else float(x)


# ---------- trend ----------
def trends(m):
    out = {}
    for city, g in m.groupby("city_id"):
        g = g.assign(year=g["period"].dt.year)
        res = {}
        for label, (_, col, unit) in VARS.items():
            if label == "precipitation":
                yearly = g.groupby("year").apply(lambda x: x[col].sum() if len(x) == 12 and x[col].notna().all() else np.nan, include_groups=False)
                unit = "mm/year"
            else:
                yearly = g.groupby("year").apply(lambda x: np.average(x[col].dropna(), weights=x.loc[x[col].notna(), "hours_observed"]), include_groups=False)
            yearly = yearly.dropna()
            slope, intercept, lo, hi = stats.theilslopes(yearly.values, yearly.index.values.astype(float))
            tau, p = stats.kendalltau(yearly.index.values, yearly.values)
            res[label] = {"unit": unit, "years": [int(y) for y in yearly.index], "values": [round(float(v), 3) for v in yearly.values],
                          "slope_per_decade": round(slope * 10, 3), "ci95_per_decade": [round(lo * 10, 3), round(hi * 10, 3)],
                          "kendall_tau": round(float(tau), 3), "p_value": round(float(p), 4), "n_years": int(len(yearly)),
                          "significant_at_5pct": bool(p < 0.05)}
        out[city] = res
    return out


# ---------- anomalies ----------
def climatology(train):
    """Smoothed (15-day circular) day-of-year median of the training series."""
    doy = np.minimum(train.index.dayofyear, 365)
    med = train.groupby(doy).median().reindex(range(1, 366)).interpolate().values
    ext = np.r_[med[-7:], med, med[:7]]
    return np.convolve(ext, np.ones(15) / 15, mode="valid")


def deseason(series, clim):
    return series - clim[np.minimum(series.index.dayofyear, 365) - 1]


def anomalies(d):
    out = {}
    for city, g in d.groupby("city_id"):
        g = g.set_index("date").sort_index()
        train_mask = g.index.year <= ANOM_TRAIN_END
        feats = {}
        for k, col in (("temperature", "temperature_2m_mean_c"), ("humidity", "relative_humidity_2m_mean_pct"),
                       ("wind", "wind_speed_2m_mean_m_s"), ("pressure", "surface_pressure_mean_kpa")):
            s = g[col].dropna()
            clim = climatology(s[s.index.year <= ANOM_TRAIN_END])
            feats[k] = deseason(s, clim)
        t = feats["temperature"]
        tr = t[t.index.year <= ANOM_TRAIN_END]
        mad = tr.groupby(tr.index.month).apply(lambda x: 1.4826 * np.median(np.abs(x - np.median(x))))
        z = t / t.index.month.map(mad).values
        flag = z.abs() > Z_LIMIT
        feat_df = pd.DataFrame(feats).dropna()
        scale = feat_df[feat_df.index.year <= ANOM_TRAIN_END].std()
        scaled = feat_df / scale
        iso = IsolationForest(n_estimators=200, contamination=0.01, random_state=SEED)
        iso.fit(scaled[scaled.index.year <= ANOM_TRAIN_END])
        iso_flag = pd.Series(iso.predict(scaled) == -1, index=scaled.index)
        test = t.index.year > ANOM_TRAIN_END
        by_year = flag.groupby(flag.index.year).sum()
        top = z[flag].reindex(z[flag].abs().sort_values(ascending=False).index).head(15)
        out[city] = {
            "train_days": int((~test).sum()), "test_days": int(test.sum()),
            "zscore_flag_rate_train": round(float(flag[~test].mean()), 5), "zscore_flag_rate_test": round(float(flag[test].mean()), 5),
            "zscore_flags_by_year": {int(y): int(n) for y, n in by_year.items()},
            "isolation_forest_flag_rate_train": round(float(iso_flag[iso_flag.index.year <= ANOM_TRAIN_END].mean()), 5),
            "isolation_forest_flag_rate_test": round(float(iso_flag[iso_flag.index.year > ANOM_TRAIN_END].mean()), 5),
            "both_methods_test_days": int((flag.reindex(iso_flag.index).fillna(False) & iso_flag)[iso_flag.index.year > ANOM_TRAIN_END].sum()),
            "top_zscore_days": [{"date": ts.strftime("%Y-%m-%d"), "temperature_c": round(float(g.loc[ts, "temperature_2m_mean_c"]), 2),
                                 "anomaly_c": round(float(t[ts]), 2), "robust_z": round(float(v), 2)} for ts, v in top.items()],
        }
    return out


# ---------- correlation ----------
def correlation(d):
    out = {}
    for city, g in d.groupby("city_id"):
        g = g.set_index("date").sort_index()
        an = pd.DataFrame({k: deseason(g[c].dropna(), climatology(g[c].dropna())) for k, c in CORR_COLS.items() if k != "precipitation"})
        an["precipitation"] = g["precipitation_total_mm"]   # skewed and non-seasonal in mean: used as is (Spearman is rank-based)
        an = an.dropna()
        names = list(an.columns)
        out[city] = {"variables": names, "n_days": int(len(an)),
                     "pearson": an.corr("pearson").round(3).values.tolist(), "spearman": an.corr("spearman").round(3).values.tolist()}
    return out


# ---------- forecast ----------
def design(periods, t0):
    t = np.array([(p.year - t0.year) * 12 + (p.month - t0.month) for p in periods], dtype=float)
    month = np.array([p.month for p in periods])
    cols = [t / 120.0]
    for k in (1, 2, 3):
        cols += [np.sin(2 * np.pi * k * month / 12), np.cos(2 * np.pi * k * month / 12)]
    return np.column_stack(cols)


def metrics(y, p):
    err = y - p
    ss = float(((y - y.mean()) ** 2).sum())
    return {"mae": round(float(np.abs(err).mean()), 4), "rmse": round(float(math.sqrt((err ** 2).mean())), 4),
            "r2": round(1 - float((err ** 2).sum()) / ss, 4) if ss else None}


def climatology_model(train):
    means = train.groupby(train.index.month).mean()
    return lambda periods: np.array([means[p.month] for p in periods])


def last_year_model(train):
    last = train.iloc[-12:]
    by_month = {p.month: v for p, v in last.items()}
    return lambda periods: np.array([by_month[p.month] for p in periods])


def ridge_model(train):
    t0 = train.index[0]
    reg = Ridge(alpha=1.0, random_state=SEED).fit(design(train.index, t0), train.values)
    return (lambda periods: reg.predict(design(periods, t0))), reg


MODELS = {"seasonal_climatology": climatology_model, "last_year_repeat": last_year_model,
          "harmonic_trend_ridge": lambda s: ridge_model(s)[0]}


def rolling_origin_mae(train, first_year=2011):
    """Mean MAE of one-year-ahead forecasts for each year first_year..last training year, fitted only on earlier months.
    Uses training years only; far less noisy than a single validation block."""
    scores = {n: [] for n in MODELS}
    for year in range(first_year, int(train.index[-1].year) + 1):
        fit, val = train[train.index.year < year], train[train.index.year == year]
        for n, f in MODELS.items():
            scores[n].append(metrics(val.values, f(fit)(val.index))["mae"])
    return {n: round(float(np.mean(v)), 4) for n, v in scores.items()}


def forecasts(m, artifact_dir=None):
    out, saved = {}, {}
    for city, g in m.groupby("city_id"):
        g = g.set_index("period").sort_index()
        res = {}
        for label, (_, col, unit) in VARS.items():
            s = g[col].astype(float)
            if s.isna().any():
                res[label] = {"skipped": "missing monthly values; no forecast made"}
                continue
            train, test = s[s.index.year <= TRAIN_END_YEAR], s[s.index.year > TRAIN_END_YEAR]
            val_mae = rolling_origin_mae(train)
            test_scores = {n: metrics(test.values, f(train)(test.index)) for n, f in MODELS.items()}
            chosen = min(val_mae, key=val_mae.get)        # chosen on validation inside the training years only
            final_fn = MODELS[chosen](s)
            future = pd.period_range(s.index[-1] + 1, periods=12, freq="M")
            pred = final_fn(future)
            half = 1.96 * test_scores[chosen]["rmse"]
            res[label] = {
                "unit": unit, "split": {"train": f"{train.index[0]}..{train.index[-1]}", "test": f"{test.index[0]}..{test.index[-1]}",
                                        "validation_inside_train": "rolling origin 2011-2020: each year forecast from the data before it"},
                "validation_mae": val_mae, "test_metrics": test_scores, "selected_model": chosen,
                "selected_on": "lowest mean rolling-origin MAE inside the training years (test set not used for selection)",
                "history": {"months": [str(p) for p in s.index[-60:]], "values": [round(float(v), 3) for v in s.values[-60:]]},
                "test_predictions": {"months": [str(p) for p in test.index], "values": [round(float(v), 3) for v in MODELS[chosen](train)(test.index)]},
                "forecast": {"months": [str(p) for p in future], "values": [round(float(v), 3) for v in pred],
                             "lower": [round(float(v - half), 3) for v in pred], "upper": [round(float(v + half), 3) for v in pred],
                             "interval_note": "approximate: prediction +/- 1.96 x test RMSE of the selected model; not a calibrated interval"},
            }
            if chosen == "harmonic_trend_ridge" and artifact_dir:
                saved[f"{city}.{label}"] = ridge_model(s)[1]
        out[city] = res
    if artifact_dir and saved:
        joblib.dump(saved, Path(artifact_dir) / "ridge_models.joblib")
    return out


def run(cache_dir, artifact_root):
    """Train/evaluate everything on the cached verified data. Returns the results document and writes artifacts."""
    d, m = load(cache_dir)
    created = datetime.now(timezone.utc)
    art = Path(artifact_root) / f"{VERSION}_{created:%Y%m%dT%H%M%SZ}"
    art.mkdir(parents=True, exist_ok=True)
    res = {"model_version": VERSION, "created_at": created.isoformat(timespec="seconds"), "seed": SEED,
           "data_labels": {"history": "derived aggregates of NASA POWER gridded reanalysis / satellite radiation (not station observations)",
                           "predictions": "model-derived forecasts (not observations)"},
           "trend": trends(m), "anomalies": anomalies(d), "correlation": correlation(d), "forecast": forecasts(m, art),
           "artifact_dir": str(art)}
    (art / "results.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res
