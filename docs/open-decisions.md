# Open decisions (do not implement until answered)

## Decided (details: docs/datasets.md, docs/data-architecture.md)
- Geographic scope: Pakistan; Karachi, Lahore, Islamabad, Peshawar, Quetta
- Historical climate: NASA POWER, hourly, 2001-01-01..2025-12-31, T2M RH2M PRECTOTCORR WS2M PS ALLSKY_SFC_SW_DWN
- Current weather: Open-Meteo, near-real-time model-based data via REST polling (not event streaming)
- Environmental: OpenAQ (observed, PM2.5 primary) + Open-Meteo Air Quality (modelled), kept distinct
- Satellite: NASA Earthdata MODIS MOD11A2 v061 LST, 2015-01-01..2025-12-31, tiles h24v06 h24v05 h23v05, HDF4-EOS
- Raw formats: provider-native (CSV/JSON/HDF4-EOS)
- HDFS layers and layout, ingestion/manifest/idempotency design: docs/data-architecture.md (confirmed 2026-10-06; POWER raw format CSV)
- App foundation (approved 2026-10-08, implemented): FastAPI + Jinja2 + Bootstrap + Chart.js; MongoDB for users, sessions, feedback, alert rules, latest readings; roles Administrator and Analyst; server-side sessions + CSRF; in-app feedback; no email/SMS, no public deployment. docs/application.md
- MapReduce (approved 2026-10-08): Hadoop Streaming with Python on YARN; daily solar = mean in provider unit `MJ/hr` (no daily total); daily precipitation total empty unless all 24 hourly values are valid; one reducer; exact decimal sums, means rounded to 4 decimals; 17-field daily schema; output `/earthscape/processed/power_daily/`, manifest `power_daily.jsonl`; daily to monthly as a separate second job (1,500 rows, `power_monthly_v1`). Implemented and verified: docs/data-architecture.md
- POWER RAW -> INTERIM contract (CSV, 9-column row, one file per city, UTC start-of-hour timestamp, `-999` to null + `missing_vars`, interim manifest, `power_interim_v1`), solar field `solar_irradiance_mj_hr` with provider unit `MJ/hr`: fully confirmed 2026-10-07; all five cities transformed and verified; docs/data-architecture.md
- Implementation approvals (2026-10-08, implemented): streaming = near-real-time REST polling thread (no Kafka/Spark); ML = Theil-Sen/Mann-Kendall trends, robust-z + Isolation Forest anomalies, deseasonalised correlation, seasonal baselines vs harmonic-trend Ridge forecasts; MongoDB = application database; notification = in-app alerts (no email/SMS); support = in-app feedback form; roles = Administrator and Analyst; dashboard/page structure and FastAPI deployment on localhost; HDF4 reader = pyhdf; polling every 15 min (OpenAQ 60 min); INTERIM-equivalent for live sources = normalised MongoDB readings plus sealed daily raw files. Daily/monthly/yearly MapReduce and the app foundation are described in docs/data-architecture.md and docs/architecture.md.

## Open
1. Impala: listed by the SRS (software list, no function stated); install and role undecided
2. Tableau: same; cache CSVs are Tableau-readable
3. Apache Server: same; TLS config prepared in config/apache/earthscape.conf, install undecided
4. RStudio: same
5. TLS activation and encryption at rest (BitLocker needs an elevated step; see docs/security-and-reliability.md)
9. Weather station records named by the SRS: no station dataset approved
6. Open-Meteo Air Quality backfill period (if any)
7. MODIS: NASA Earthdata credentials needed before any download (plan measured: 1,515 granules, 11.2 GB)
8. Email/SMS notification (needs external credentials; not configured)
