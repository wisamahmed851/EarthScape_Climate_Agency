# EarthScape user guide

Open **http://127.0.0.1:8000** and sign in. Setup and startup commands are in the [README](../README.md). The **Climate / Live / System** menus group the pages; a red *Open alerts* badge appears when alerts need attention.

## Roles
- **Analyst:** all dashboards, alert rules, acknowledging alerts, feedback submission.
- **Administrator:** everything an Analyst can do, plus User Management, Feedback Review, data refresh, "Poll all sources now" and "Retrain analytics".

## Pages
| Page (menu) | URL | What it shows |
|---|---|---|
| Overview (Climate) | `/overview?city=karachi` | KPIs for the chosen city (mean temperature, highest/lowest hourly value, annual precipitation, humidity, wind, pressure), data coverage, provenance. Source label: NASA POWER gridded reanalysis, not station data |
| Historical Climate | `/history` | Temperature, precipitation, humidity and wind charts; choose city, date range, daily (max 1,830 days) or monthly. Gaps mean missing values |
| City Comparison | `/compare` | Five cities side by side by year range (2001-2025): table and four charts |
| Trends & Extremes | `/trends` | Theil-Sen trend table and charts; anomaly detection results; hot/frost/heavy-rain day counts per year from the Hadoop yearly job |
| Correlation | `/correlation` | Pearson and Spearman matrices of deseasonalised daily variables |
| Forecast | `/forecast` | 12-month model-derived forecast with held-out test evaluation. Predictions are not observations |
| Current Data (Live) | `/current` | Freshness and staleness of each live source, latest weather and air quality per city (**MODELLED**), OpenAQ monitors (**OBSERVED** PM2.5), recent history charts |
| Current vs historical (on Current Data) | `/current` | Open-Meteo temperature and humidity of the last 24 h against the NASA POWER climatology for the same UTC hour (2001-2025, +-7 calendar days, mean and +-1 SD) with a plain-language assessment. Wind, pressure, precipitation and PM2.5 are deliberately not compared |
| Alert Rules | `/alerts` | Create, edit, enable/disable threshold rules (live variables are evaluated; historical ones are stored only) |
| Alert History | `/alerts/history` | Alerts raised from current readings with value, unit, source and modelled/observed tag; acknowledge them |
| Data Sources (System) | `/sources` | Provider classifications, pipeline status, freshness, provenance (YARN ids, checksums); Administrator: refresh from HDFS |
| Feedback | `/feedback` | Submit feedback, support requests or bug reports |
| Feedback Review (Admin) | `/admin/feedback` | Review, filter and mark feedback reviewed |
| User Management (Admin) | `/admin/users` | Create users, change roles, disable/enable, reset passwords |
| Health | `/health` | JSON status of app, MongoDB, HDFS, data cache, poller and live sources |

## Common tasks
- **Create an alert for high PM2.5:** Alert Rules, name it, City = Lahore, Variable = "PM2.5 - OpenAQ, OBSERVED", When `>`, Threshold `100`, Save. After the next poll, matches appear in Alert History.
- **Interpret an alert:** the *Data type* badge tells you whether the value was modelled or observed; OpenAQ alerts are per monitoring location (many are low-cost sensors).
- **Why is something marked STALE?** The newest reading is older than the allowed age (weather 60 min, air quality 3 h, OpenAQ 24 h). Stale values are shown but never trigger alerts.
- **Refresh historical data (Admin):** Data Sources, "Refresh from HDFS" (HDFS must be running). **Retrain analytics:** Trends & Extremes, "Retrain analytics".
- **Update the models after new data:** refresh data, then retrain; each run is stored as a new version.

## Reading the data honestly
POWER is gridded reanalysis/satellite-derived data for a grid cell, not a station reading; Open-Meteo is a model; OpenAQ is the only observed series and its coverage is sparse; forecasts are model output with approximate bands.

## FAQ
- **I cannot log in.** Five wrong passwords lock the account for 15 minutes. An Administrator can reset a password under User Management. If no Administrator exists, run `manage.py create-admin --username <name>` (it prompts for the password).
- **Why does the page say "no readings"?** Live data accumulates only while the server runs. The first poll runs at start-up; Administrators can use "Poll all sources now".
- **The comparison says "stale" or "no reference".** Stale = the newest reading is older than 60 minutes. No reference = fewer than 200 historical samples exist for that day and hour, or `manage.py build-reference` has not been run.
- **Does "within the typical range" mean the weather is normal?** It only says the model value lies within one standard deviation of the 2001-2025 reanalysis mean for that hour. The two sources are different models on different grids; a difference of a degree or two can be a model or grid difference. It is not anomaly detection and not a forecast.
- **Why is PM2.5 shown twice?** Modelled (Open-Meteo/CAMS) and observed (OpenAQ sensors) are different things and are never merged.
- **Why are there no satellite (MODIS) data?** NASA Earthdata credentials were not available; nothing is invented.
- **Is my data safe?** Passwords are hashed with Argon2id; sessions are server-side; the app listens on 127.0.0.1 only. TLS and disk encryption are not active (see security-and-reliability.md).

## Tutorial: a 5-minute tour
1. Sign in and open **Overview** (Karachi). 2. **Historical Climate**: choose Lahore, monthly view. 3. **Trends & Extremes**: read the trend table. 4. **Current Data**: look at the freshness table, then the *Current weather versus historical reference* card. 5. **Alert Rules**: create "Lahore PM2.5 > 100", then check **Alert History** after the next poll and acknowledge it. 6. **Feedback**: send a message; an Administrator reads it under Feedback Review.
