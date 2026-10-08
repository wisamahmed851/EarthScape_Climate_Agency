# EarthScape web application

FastAPI + Jinja2 + Bootstrap + Chart.js (CDN) on MongoDB, with HDFS PROCESSED outputs (via a verified local cache), live polling and ML analytics. Code: [src/app/](../src/app/) (`main.py` routes, `services.py` MongoDB operations and validation, `live.py` polling/alerts/sealing, `data.py` cache, `analytics.py` ML storage, `security.py`, `db.py`, `settings.py`, `manage.py` CLI). Setup and run commands: [README](../README.md). Architecture, schema, RBAC and alert workflow: [architecture.md](architecture.md). User-facing pages: [user-guide.md](user-guide.md).

## Configuration (environment variables or the git-ignored `.env`; names only)
`MONGODB_URI` (default `mongodb://localhost:27017`), `MONGODB_DB` (`earthscape`), `SESSION_HOURS` (8), `COOKIE_SECURE=1` (behind TLS), `POLL_ENABLED` (default 1), `POLL_MINUTES` (15), `OPENAQ_MINUTES` (60), `OPENAQ_API_KEY`, optional `EARTHDATA_*` for MODIS. No secret is in code, logs or output.

## Prerequisites
MongoDB service on `localhost:27017`; `.venv` with `requirements.txt`; HDFS (and YARN for new MapReduce runs) only for `refresh-data`, `seal-raw`, `backup --hdfs` and the `/health` HDFS check. The server starts and serves from the cache without HDFS.

## Behaviour notes
- **Sessions/security:** see [security-and-reliability.md](security-and-reliability.md).
- **Historical data:** read-only, served from memory loaded from 3 cached files (about 4.4 MB) that `refresh-data` validated against the provenance manifests; daily charts are limited to 1,830 days.
- **Polling:** a daemon thread started with the app polls Open-Meteo weather and air quality every `POLL_MINUTES` and OpenAQ every `OPENAQ_MINUTES`, evaluates alerts, and seals finished UTC days of raw responses into HDFS RAW when HDFS is up. Failures are logged and shown on *Current Data*; they never stop the app. This is near-real-time polling, not event streaming.
- **Analytics:** `train-ml` (or the Administrator button) stores a versioned run in `ml_runs`; the Trends, Forecast and Correlation pages show the latest run and say clearly when none exists.
- **Not implemented here:** TLS, encryption at rest, email/SMS, MODIS data (no credentials).

## Tests
`.venv\Scripts\python.exe -m unittest discover -s tests` (needs MongoDB; the app tests use throw-away databases that are dropped afterwards). Results: [testing-report.md](testing-report.md).
