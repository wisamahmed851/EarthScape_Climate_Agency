# Final report material (draft text for the academic report)

## 1. Abstract
EarthScape Climate Agency is a locally deployed climate-monitoring system for five Pakistani cities. It stores 25 years of NASA POWER hourly reanalysis in HDFS, aggregates it with Hadoop MapReduce on YARN, polls Open-Meteo (modelled weather and air quality) and OpenAQ (observed PM2.5), evaluates user-defined threshold alerts, trains trend, anomaly, correlation and forecasting models, and presents everything in a role-protected web dashboard. Data classes (reanalysis, modelled, observed, predicted) are labelled throughout and never mixed.

## 2. Problem and objectives
Climate and air-quality information for Pakistani cities is scattered across providers with different semantics. Objectives: integrate several approved sources, process the large historical set with Hadoop MapReduce, add near-real-time monitoring and alerts, apply defensible machine learning, and deliver authenticated interactive dashboards with feedback, monitoring, backup and documented security.

## 3. Methodology
- **Ingestion:** validated, idempotent, provenance-tracked ingestion (manifests with sha256), immutable RAW, scripted RAW to INTERIM (`-999` to null plus flag, no unit conversion). 125 city-year objects, 1,095,720 hourly rows, zero fill values.
- **Batch processing:** three Hadoop Streaming jobs with exact decimal arithmetic, each verified by an independent recomputation (daily 45,655, monthly 1,500, yearly 125 rows).
- **Near-real-time:** REST polling every 15 min (OpenAQ hourly), duplicate-free storage in MongoDB, daily sealed raw files in HDFS, stale-data rules.
- **ML:** Theil-Sen/Mann-Kendall trends; robust z-score and Isolation Forest anomalies fitted on 2001-2015; deseasonalised correlations; monthly forecasts with a chronological train/test split, two baselines and a harmonic-trend Ridge model, selected by rolling-origin validation ([ml-methodology.md](ml-methodology.md)).
- **Current vs historical:** an hour-of-day POWER climatology (count/sum/sum of squares per calendar day and UTC hour, +-7-day window, 2001-2025) is compared with Open-Meteo readings of the same UTC hour for temperature and humidity only, with units, staleness and sample-size guards.
- **Operations:** scheduled health check and backup with retention; restore verified into an isolated database.
- **Application:** FastAPI/Jinja2 with Argon2id, server-side sessions, CSRF, RBAC; MongoDB for application data.

## 4. Results (all produced by real runs)
- Pipeline volumes and checks: data-architecture.md and [architecture.md](architecture.md).
- Analytics: tables in ml-methodology.md (trend slopes with intervals, anomaly rates on training vs unseen years, forecast errors against baselines).
- Live data: first polls stored 5 weather, 5 air-quality and 89 OpenAQ readings from active monitors; alert lifecycle tested end to end on real readings.
- Testing: [testing-report.md](testing-report.md) (automated suite, real-browser check, live smoke tests).
- Screenshots: `docs/screenshots/`.

## 5. Discussion points
- POWER is a reanalysis product; agreement with station observations was not assessed.
- The forecasting gain over a seasonal-climatology baseline is small for most variables; the honest message is that seasonality explains most monthly variance.
- OpenAQ coverage is dense in Lahore and Karachi and sparse elsewhere; most sensors are low-cost, so absolute PM2.5 values carry sensor uncertainty. Modelled (CAMS) and observed PM2.5 differ and are shown separately.
- MapReduce is justified here by the requirement and by the aggregation pattern, not by data volume.

## 6. Limitations and future work
See [limitations.md](limitations.md). Future work: MODIS land-surface-temperature ingestion once credentials exist, TLS and disk encryption, scheduled backups and uptime measurement, station-data validation, email/SMS delivery, Impala/Tableau/R integration if the SRS requires them.

## 7. Requirement coverage
[srs-completion-plan.md](srs-completion-plan.md) (traced to the real SRS: 22 complete, 8 partial, 10 blocked or not implemented of 40 line items).

## 8. References
NASA POWER (power.larc.nasa.gov) MERRA-2/CERES; Open-Meteo weather and air-quality APIs (CAMS); OpenAQ v3; NASA LP DAAC MOD11A2 v061; Apache Hadoop 3.4.3; ETCCDI climate extremes indices; Iglewicz and Hoaglin (1993) robust outlier rule; Sen (1968) slope estimator; Mann (1945)/Kendall (1975) trend test.
