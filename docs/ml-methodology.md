# Machine-learning methodology and evaluation

Code: [src/ml/climate_ml.py](../src/ml/climate_ml.py); run with `.venv\Scripts\python.exe src\app\manage.py train-ml` (or the Administrator button on *Trends & Extremes*). Version `climate_ml_v1`, seed 42, trained 2026-10-08 11:41 UTC; artifacts in `artifacts/ml/` (results JSON and fitted Ridge models, git-ignored); results are stored in MongoDB `ml_runs`. Tests: `tests/test_live_ml.py` (`ClimateMlTest`, synthetic series with a known signal, never shown in the app).

**What the data are.** NASA POWER daily/monthly values derived by Hadoop MapReduce from MERRA-2 reanalysis and CERES satellite radiation: gridded **model-based** values, not weather-station observations, 2001-2025, five city grid cells. Every statement below is about that product. Forecasts are model-derived and never labelled or stored as observations.

## 1. Trend analysis
Annual values per city and variable (hour-weighted means; precipitation only for complete years). **Theil-Sen slope** (median of pairwise slopes, robust to outliers) with a 95% interval, and the **Mann-Kendall** test (Kendall tau of value vs year) for monotonic trend. n = 25 years, so intervals are wide.

| City | Temperature (C/decade) [95% interval] | p | Precipitation (mm/decade) [95% interval] | p | Humidity (%/decade) | Wind (m/s/decade) |
|---|---|---|---|---|---|---|
| Karachi | 0.127 [-0.053, 0.261] | 0.1557 | 107.326 [8.55, 230.24] | 0.0223 | 0.473 (p=0.3914) | -0.025 (p=0.6273) |
| Lahore | -0.672 [-1.019, -0.264] | 0.0007 | 207.462 [124.155, 324.533] | 0.0 | 7.175 (p=0.0001) | 0.028 (p=0.1183) |
| Islamabad | -0.613 [-1.12, -0.269] | 0.0024 | 401.167 [238.975, 598.324] | 0.0001 | 9.006 (p=0.0) | -0.044 (p=0.0288) |
| Peshawar | -0.722 [-1.165, -0.428] | 0.0004 | 353.764 [234.327, 465.643] | 0.0 | 9.16 (p=0.0) | -0.047 (p=0.0034) |
| Quetta | -0.028 [-0.398, 0.346] | 0.9447 | 124.536 [40.613, 225.856] | 0.0039 | 3.636 (p=0.0325) | 0.013 (p=0.6943) |

*Reading:* a slope whose interval spans zero and p &gt; 0.05 is not distinguishable from no trend over 25 years. Reanalysis trends can include artefacts from changing assimilated observations; this is a description of the dataset, not a physical attribution.

## 2. Anomaly detection (unsupervised)
Daily mean temperature. Climatology = day-of-year median over **2001-2015 only**, smoothed (15-day circular window). Anomaly = value minus climatology. **Robust z** = anomaly / (1.4826 x MAD of the training anomalies of that calendar month); flagged when |z| &gt; 3.5 (Iglewicz-Hoaglin rule). A second detector, **Isolation Forest** (200 trees, contamination 0.01, seed 42), is fitted on 2001-2015 anomalies of temperature, humidity, wind and pressure and applied to 2016-2025. Nothing in 2016-2025 influenced the climatology, scales or forest (no leakage).

| City | Robust-z flag rate train / test | Isolation Forest rate train / test | Test days flagged by both |
|---|---|---|---|
| Karachi | 0.68% / 1.45% | 1.00% / 0.90% | 9 |
| Lahore | 0.13% / 0.74% | 1.00% / 2.22% | 14 |
| Islamabad | 0.07% / 0.77% | 1.00% / 2.19% | 13 |
| Peshawar | 0.15% / 0.55% | 1.00% / 1.18% | 8 |
| Quetta | 0.27% / 0.30% | 1.00% / 0.88% | 3 |

*Evaluation:* there are no ground-truth labels, so precision/recall cannot be computed. The checks used are: flag rate on the training years (expected small), flag rate on unseen years, agreement of two independent methods, and a unit test that an injected +15 C spike is found. Flags mean "unusual relative to 2001-2015 climate", not "confirmed event"; because temperatures rise, positive flags can become more frequent in 2016-2025.

## 3. Correlation
Pearson and Spearman correlations between daily **deseasonalised** anomalies (value minus its day-of-year climatology) for temperature, humidity, wind, pressure and solar radiation, plus precipitation as is. Descriptive over all days; association only, not causation. Air-quality variables are excluded: observed PM2.5 exists for only a short, sparse period.

## 4. Forecasting
Monthly means (precipitation: monthly total) per city and variable. **Chronological split: train 2001-01..2020-12, test 2021-01..2025-12 (60 months).** Models, all fitted on training months only:
- *seasonal_climatology* (baseline): mean of each calendar month in training.
- *last_year_repeat* (naive baseline): the last training year repeated.
- *harmonic_trend_ridge* (improved): Ridge regression (alpha = 1) on a linear time trend and three annual sine/cosine harmonics.

**Model selection** uses **rolling-origin validation inside the training years** (each year 2011-2020 is forecast from the months before it; mean MAE). The test years are never used to choose. The chosen model is then refitted on all 2001-2025 months to forecast 2026-01..2026-12. The displayed band is the forecast +/- 1.96 x test RMSE of the selected model: an approximation, **not a calibrated prediction interval**.

| City | Variable (unit) | Selected (rolling-origin) | Test MAE: selected | Test MAE: seasonal climatology | Test MAE: last year | Test MAE: harmonic ridge | Test RMSE selected | Test R2 selected |
|---|---|---|---|---|---|---|---|---|
| Karachi | temperature (C) | seasonal_climatology | 0.6799 | 0.6799 | 1.0558 | 0.6606 | 0.8099 | 0.9507 |
| Karachi | humidity (%) | seasonal_climatology | 4.3657 | 4.3657 | 5.1815 | 4.3252 | 5.5138 | 0.7975 |
| Karachi | precipitation (mm/month) | seasonal_climatology | 27.8426 | 27.8426 | 65.2885 | 32.6053 | 62.928 | 0.2118 |
| Karachi | wind (m/s) | seasonal_climatology | 0.3087 | 0.3087 | 0.4815 | 0.3246 | 0.419 | 0.849 |
| Lahore | temperature (C) | harmonic_trend_ridge | 1.3433 | 1.47 | 1.899 | 1.3433 | 1.6517 | 0.9558 |
| Lahore | humidity (%) | harmonic_trend_ridge | 9.005 | 12.4123 | 13.1678 | 9.005 | 10.8272 | 0.5526 |
| Lahore | precipitation (mm/month) | seasonal_climatology | 45.568 | 45.568 | 61.8182 | 47.3482 | 79.2111 | 0.4832 |
| Lahore | wind (m/s) | seasonal_climatology | 0.1471 | 0.1471 | 0.1694 | 0.1564 | 0.1858 | 0.6239 |
| Islamabad | temperature (C) | harmonic_trend_ridge | 1.1925 | 1.3065 | 1.8518 | 1.1925 | 1.5252 | 0.9561 |
| Islamabad | humidity (%) | harmonic_trend_ridge | 6.5547 | 12.4995 | 11.7729 | 6.5547 | 7.9764 | 0.5998 |
| Islamabad | precipitation (mm/month) | seasonal_climatology | 75.5339 | 75.5339 | 254.3003 | 86.2751 | 126.9437 | 0.3291 |
| Islamabad | wind (m/s) | seasonal_climatology | 0.1015 | 0.1015 | 0.1247 | 0.105 | 0.1285 | 0.7116 |
| Peshawar | temperature (C) | harmonic_trend_ridge | 1.3106 | 1.4197 | 1.9969 | 1.3106 | 1.7045 | 0.9508 |
| Peshawar | humidity (%) | harmonic_trend_ridge | 7.1217 | 11.9701 | 12.2541 | 7.1217 | 8.7729 | 0.368 |
| Peshawar | precipitation (mm/month) | harmonic_trend_ridge | 60.4475 | 56.0727 | 150.2157 | 60.4475 | 74.9044 | 0.2652 |
| Peshawar | wind (m/s) | seasonal_climatology | 0.1454 | 0.1454 | 0.1723 | 0.1432 | 0.1907 | 0.3895 |
| Quetta | temperature (C) | seasonal_climatology | 1.2504 | 1.2504 | 2.0149 | 1.3292 | 1.576 | 0.9615 |
| Quetta | humidity (%) | harmonic_trend_ridge | 9.4449 | 9.8447 | 13.1315 | 9.4449 | 11.926 | 0.2916 |
| Quetta | precipitation (mm/month) | last_year_repeat | 46.155 | 34.718 | 46.155 | 36.9194 | 77.6262 | 0.0592 |
| Quetta | wind (m/s) | seasonal_climatology | 0.246 | 0.246 | 0.3023 | 0.2585 | 0.3011 | 0.491 |

The selected model had a test MAE no worse than the seasonal-climatology baseline in 18 of 20 city-variable forecasts. Where it did not, that is reported as is (the selection rule was fixed beforehand and not tuned on the test years; for example Quetta precipitation selects the noisy naive model). High R2 for temperature mostly reflects the seasonal cycle, so compare MAE against the baselines. Precipitation skill is limited.

## Limitations
25 years only; reanalysis, not observations; one grid cell per city; no uncertainty calibration; no external validation against station data; anomaly detection has no labels; models are linear/seasonal and cannot anticipate regime changes.
