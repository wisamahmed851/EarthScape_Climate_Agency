# Testing report (final verification, 2026-10-08)

## Automated suite
`.venv\Scripts\python.exe -m unittest discover -s tests` -> **Ran 149 tests, OK** (about 110-150 s; needs the local MongoDB; app tests use throw-away databases that are dropped afterwards; no test touches HDFS data, the real `earthscape` database or the real provider APIs).

| Area | Tests | Module / class |
|---|---|---|
| NASA POWER ingestion, batch driver, RAW to INTERIM | 22 | `test_nasa_power*.py`, `test_power_interim.py` |
| MapReduce daily/monthly mapper/reducer, driver log parsing, rounding | 16 | `test_power_mapreduce.py` |
| MapReduce yearly (indices, leap year, weighting, bad rows) | 3 | `test_live_ml.YearlyReducerTest` |
| Authentication, sessions, CSRF, lockout, headers | 11 | `test_app.AuthTest` |
| Password and username policy, CSRF compare | 4 | `test_app.PasswordTest` |
| RBAC and user management | 7 | `test_app.RbacUserTest` |
| MongoDB indexes, uniqueness, DB-down behaviour | 4 | `test_app.MongoTest` |
| Feedback (submit, review, validation, XSS escaping) | 3 | `test_app.FeedbackTest` |
| Alert rule configuration | 3 | `test_app.AlertTest` |
| Historical data loading, cache tamper detection, manifest validation on refresh, HDFS down | 6 | `test_app.DataTest` |
| Dashboard pages and JSON APIs, invalid input, 503 without data, safe 500 | 7 | `test_app.DashboardTest` |
| Health endpoint | 2 | `test_app.HealthTest` |
| Provider parsing (modelled vs observed, metadata, null values, key handling, retries) | 6 | `test_live_ml.SourceParsingTest` |
| Live storage, duplicates, poll failure, alert lifecycle, stale data, scope, observed vs modelled, history UI, acknowledgement, badge, poll-now authorization, background poller start/stop | 12 | `test_live_ml.LiveStorageAndAlertsTest` |
| Raw sealing: HDFS down keeps the file | 1 | `test_live_ml.SealRawTest` |
| **Live RAW daily rollover on an in-memory HDFS**: finished day sealed with provenance, count and sha256; open day stays staged; one partition per source; repeat creates no duplicate object or manifest line; interruption before move and after move recovered; different object never overwritten; envelope outside its UTC day or corrupt file refused; 15-minute grace after midnight | 8 | `test_final_phase.RawRolloverTest` |
| **Historical reference and comparison**: window mean/SD, year-end wrap, Feb 29, missing cells and too few samples, unit and staleness handling, assessment wording | 5 | `test_final_phase.ReferenceTest` |
| Comparison API: authentication, validation, only temperature and humidity returned, labels on the page | 2 | `test_final_phase.ComparisonApiTest` |
| Health monitor: backup age/checksum/absence, failures with timestamps, log trimming, CPU/memory metrics, MongoDB down | 4 | `test_final_phase.MonitorTest` |
| Backup retention and restore into a separate database | 2 | `test_final_phase.BackupRetentionAndRestoreTest` |
| MODIS HDF4 reader on a synthetic HDF4 file: scale factor, fill value, QC flags, geolocation check, empty window gives None | 2 | `test_final_phase.ModisReaderTest` |
| Unknown routes render the HTML error page (defect found by the browser test) | 1 | `test_final_phase.ErrorPageTest` |
| ML (structure, chronological split, baselines train-only, reproducibility, trend recovery, injected anomaly, correlation validity, missing months) | 9 | `test_live_ml.ClimateMlTest` |
| Analytics pages, empty state, invalid input, retrain authorization | 4 | `test_live_ml.AnalyticsPagesTest` |
| MODIS (tile math for the five cities, credentials, CMR parsing, HDF validation) | 4 | `test_live_ml.ModisTest` |
| Backup/restore round trip, overwrite protection, tamper detection | 1 | `test_live_ml.BackupRestoreTest` |

The ML tests use a **synthetic** series with a known signal purely to test code paths; real-data metrics are in [ml-methodology.md](ml-methodology.md).

## Hadoop / HDFS checks (real cluster)
- Three Streaming jobs ran on YARN and SUCCEEDED: daily (`application_1791451740685_0004`, 45,655 rows), monthly (`_0005`, 1,500), yearly (`_0006`, 125). Each output was recomputed independently from INTERIM (exact fractions; monthly/yearly means within the documented 0.0001 bound: measured 0.0000694 and 0.0000598). Repeat runs returned "skipped". RAW (125 objects) and INTERIM (5) listings and sha256 values equal their manifests before and after.
- Raw sealing of staged poll files verified on real HDFS in a scratch namespace: upload, size+sha256 read-back, manifest line, idempotent repeat, refusal to overwrite different content, then the scratch namespace was removed.

## Final real-browser verification (Chrome via Playwright, throw-away database `earthscape_browser_test`, real stored provider readings)
52 page views (13 pages x Administrator/Analyst x 1280 px/390 px): every Chart.js canvas created and painted, no failed requests, no horizontal overflow, no console errors except the browser's own log line for the **intended 403** when the Analyst opens `/admin/feedback` and `/admin/users`. Flows passed: unauthenticated redirect to login, wrong password gives the generic error, Analyst has no admin menu, an alert raised from real readings appears and is acknowledged, feedback submitted by the Analyst appears escaped in the Administrator review, invalid date/city/granularity give clear 400 messages, Analyst cannot poll, static assets 200, logout. **Defect found and fixed:** unknown URLs returned raw JSON `{"detail":"Not Found"}`; they now show the HTML error page (regression test added). The comparison card and API returned real series. Screenshot: `docs/screenshots/current_with_historical_comparison.png`. The test used throw-away users with random passwords (not the real accounts); the Playwright script is outside the repository.

## Earlier real-browser verification (Chrome via Playwright, throw-away database, real provider data)
15 dashboard pages: all expected Chart.js charts created **and painted** (history 4, compare 4, trends 7, forecast 1, current 2), **no console errors, no failed requests, no horizontal overflow**; city switching, navigation dropdown, feedback form and admin review worked; 7 pages checked at a 390 px mobile width without overflow. Screenshots: `docs/screenshots/`. The Playwright script lived outside the repository (scratch directory) and is not part of the submission.

## Live smoke tests against real providers
Throw-away database: Open-Meteo weather and air quality stored 5 + 5 readings; OpenAQ stored 89 PM2.5 readings from active monitors (all five cities); repeat polls stored 0 duplicates; alert rules fired per city/monitor, did not duplicate, and cleared when the condition ended; stale readings were skipped. The final server's poller then wrote to the real database.

## Server check before this phase (superseded by the final check in the report)
`http://127.0.0.1:8000`: process alive; `/health` reports app ok, MongoDB ok, HDFS ok, data cache ok, poller running; login page, `theme.css` and `charts.js` return 200; an unauthenticated `/api/series` returns 401. Authenticated endpoints were **not** called against the real server because no Administrator account exists yet (it must be created by the user); they were exercised by the suite and the browser test on identical code.

## Not run / not possible
- MODIS download, HDF4 reading of a real file, and sealing of a real full UTC day (needs credentials / elapsed time).
- TLS and encryption at rest (not active; Apache not installed; BitLocker state unreadable without Administrator rights); uptime measurement.
- The first real sealed daily RAW object (first UTC day ends 05:00 PKT on 2026-10-09).
- Email/SMS delivery (not configured).
- Tests against Impala, Tableau, Apache, R (not installed).

## Final-phase operational checks (real system)
- **Real backup and restore:** `backup` wrote 7 collections plus HDFS manifests/processed with sha256 in `BACKUP.json`; `restore --target-db earthscape_restore_check` produced identical counts (users 1, rules 1, ml_runs 2, ingest_status 4, latest_readings 252) and identical user documents; the scratch database was dropped. The OpenAQ key does not appear in the backup.
- **Reference check:** the Karachi 12:00 UTC temperature reference around 8 October (mean 30.2 C, SD 1.61, n = 375) was recomputed independently by streaming the INTERIM CSV from HDFS and matched the stored reference exactly.
- **Task Scheduler:** `EarthScape health check` (every 15 min) and `EarthScape backup` (daily 02:00) registered without elevation and confirmed `Ready`; scheduler-started runs ended with result 0 and produced a health record and a new backup.
- **MODIS:** `modis.py check` reports credentials not configured, pyhdf importable, HDFS reachable, 152 GB free. No granule was downloaded.

## Final SRS-compliance phase (2026-10-08, later)
- Health check now records CPU, memory and the memory of the app and `mongod` (psutil, already pinned); poll-cycle duration is stored in `ingest_status` and logged. Full suite re-run: **149 tests OK**.
- Server lifecycle verified on a throw-away database: startup answered `/health` 200; Ctrl+Break gave a graceful "Application shutdown complete" and the port closed.
- Dashboards re-checked in Chrome (52 page views, 2 roles, 1280 and 390 px): same result as above; all flows passed.
- Scheduled tasks re-checked: both `Ready`, last results 0.
- `mongosh`: `winget install MongoDB.Shell --scope user` fails ("No applicable installer"); it needs an elevated install, so it was not installed.
- Report and ReadMe generated by `scripts/build_report.py` as genuine .docx; legacy .doc copies made with the WPS Office on this machine and re-opened (ReadMe: 75 paragraphs, 1 table; report: 561 paragraphs, 7 tables, 8 images, 15 pages). Not opened in Microsoft Word.
