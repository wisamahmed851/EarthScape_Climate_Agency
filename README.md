# EarthScape Climate Agency

Aptech final-semester big-data / data-science project: climate and air-quality monitoring for Karachi, Lahore, Islamabad, Peshawar and Quetta. HDFS + Hadoop MapReduce for 25 years of NASA POWER data, near-real-time Open-Meteo and OpenAQ polling, threshold alerts, trend/anomaly/correlation/forecast analytics, and a role-protected FastAPI dashboard.

**Status and honesty:** see [docs/srs-completion-plan.md](docs/srs-completion-plan.md), traced to the real SRS (22 of 40 line items complete, 8 partial, 10 blocked or not implemented) and [docs/limitations.md](docs/limitations.md). MODIS is not ingested (needs NASA Earthdata credentials); TLS is prepared but not active; disk encryption could not be confirmed and is not enabled; Apache, Tableau, Impala and RStudio are not installed; no demo video is recorded.

## Documentation
[architecture](docs/architecture.md) (components, data flow, HDFS, MongoDB schema, RBAC, alert workflow) | [data-architecture](docs/data-architecture.md) (ingestion and MapReduce detail) | [ml-methodology](docs/ml-methodology.md) | [security-and-reliability](docs/security-and-reliability.md) | [user-guide](docs/user-guide.md) | [application](docs/application.md) | [testing-report](docs/testing-report.md) | [limitations](docs/limitations.md) | [report-material](docs/report-material.md) | [demo-script](docs/demo-script.md) | [datasets](docs/datasets.md) | [environment](docs/environment.md)

## Prerequisites (Windows 11)
- Python 3.13 and the project venv: `python -m venv .venv` then `.venv\Scripts\python.exe -m pip install -r requirements.txt`
- MongoDB service running on `localhost:27017` (`Get-Service MongoDB`)
- WSL2 Ubuntu-24.04 with Hadoop 3.4.3 in `/opt/hadoop` (see [docs/environment.md](docs/environment.md)) - only needed for HDFS/MapReduce work and the data refresh
- A `.env` file (git-ignored) with `OPENAQ_API_KEY=...`; optional `EARTHDATA_TOKEN` or `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` for MODIS. Names only are in `.env.example`.
- Internet access (provider APIs, and the CDN for Bootstrap/Chart.js)

## Start everything (PowerShell, project root)
```powershell
# 1. Hadoop (skip if you only run the dashboard from the existing cache)
wsl -d Ubuntu-24.04 -e sleep infinity            # leave this window open
wsl -d Ubuntu-24.04 -e /opt/hadoop/sbin/start-dfs.sh
# YARN (only for new MapReduce runs): steps in docs/environment.md

# 2. One-time application setup
.venv\Scripts\python.exe src\app\manage.py init-db
.venv\Scripts\python.exe src\app\manage.py create-admin --username admin     # prompts for the password (12+ chars)
.venv\Scripts\python.exe src\app\manage.py refresh-data                      # verified HDFS -> local cache (needs HDFS)
.venv\Scripts\python.exe src\app\manage.py train-ml                          # analytics run stored in MongoDB

# 3. Run the server (also starts the background poller)
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --app-dir src --host 127.0.0.1 --port 8000
```
Open **http://127.0.0.1:8000**. Set `POLL_ENABLED=0` to run without background polling.

## Other commands
```powershell
.venv\Scripts\python.exe src\app\manage.py poll-once            # one polling cycle now (weather, air quality, OpenAQ, alerts)
.venv\Scripts\python.exe src\app\manage.py seal-raw             # staged finished days -> HDFS RAW
.venv\Scripts\python.exe src\app\manage.py build-reference    # POWER hourly climatology for the current-vs-historical comparison (needs HDFS, about 4 min)
.venv\Scripts\python.exe src\app\manage.py health-check        # app, MongoDB, HDFS, polling, backup, disk -> logs\health.jsonl
.venv\Scripts\python.exe src\app\manage.py backup [--hdfs] [--keep 7]   # application data (+ HDFS manifests/outputs) -> backups\
.venv\Scripts\python.exe src\app\manage.py restore --path backups\earthscape_<stamp> [--replace] [--target-db other_db]
powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1   # scheduled health check (15 min) and daily backup
.venv\Scripts\python.exe src\processing\mapreduce\run_power_job.py daily|monthly|yearly   # needs HDFS + YARN
.venv\Scripts\python.exe src\ingestion\modis.py plan            # keyless MODIS catalogue/size check
.venv\Scripts\python.exe src\ingestion\modis.py check           # MODIS prerequisites (credentials present? pyhdf, HDFS, disk)
.venv\Scripts\python.exe src\ingestion\modis.py pilot --tile h24v05 --year 2015 --count 2   # only after adding Earthdata credentials to .env; never the full 11.2 GB without approval
.venv\Scripts\python.exe -m unittest discover -s tests          # all tests (needs MongoDB)
```

## Project layout
`src/app` web app | `src/ingestion` NASA POWER, live sources, MODIS | `src/processing/{batch,mapreduce}` | `src/ml` analytics | `notebooks` EDA | `config` cities, Hadoop and Apache example config | `tests` | `docs` | `data` (git-ignored: raw samples, staging, cache) | `artifacts` (git-ignored: ML artifacts, MapReduce logs).

## Stopping and restarting the server
Stop with Ctrl+C in the console (or `Stop-Process` on the python process listening on port 8000); shutdown is graceful (poller stopped, port released). Restart with the uvicorn command above, or detached: `Start-Process .venv\Scripts\python.exe -ArgumentList '-m','uvicorn','app.main:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8000' -WindowStyle Hidden`.

## Deliverables
`submission/EarthScape_Final_Report.docx|.doc`, `submission/ReadMe.docx|.doc` (built by `scripts\build_report.py --tests N`), [docs/demo-checklist.md](docs/demo-checklist.md) for the video. No demo video exists yet.

## Preparing the submission ZIP
`.venv\Scripts\python.exe scripts\package_submission.py` writes `dist\earthscape_submission.zip` without `.env`, `.venv`, `data`, `artifacts`, `logs`, `backups`, caches or other secrets/large files.

## Reproducing the project from the ZIP (large data is intentionally not included)
The ZIP holds source, tests, the EDA notebook, configuration templates and documentation. It excludes `.env`, `.venv`, `data/` (including the 1.1 million-row POWER download and the cache), `artifacts/`, `logs/`, `backups/`, caches and the Hadoop installation. To rebuild on another Windows 11 machine with WSL2:
1. Install Python 3.13, MongoDB, WSL2 Ubuntu-24.04 with Hadoop 3.4.3 ([docs/environment.md](docs/environment.md)); create the venv and install `requirements.txt`; copy `.env.example` to `.env` and fill `OPENAQ_API_KEY`.
2. Start HDFS and YARN; ingest POWER RAW (`src\ingestion\nasa_power_batch.py`, 125 city-year files from the public NASA POWER API, no key), then `src\processing\batch\power_interim.py`, then the three jobs `src\processing\mapreduce\run_power_job.py daily|monthly|yearly` ([docs/data-architecture.md](docs/data-architecture.md) has the exact order and verification steps).
3. `manage.py init-db`, `create-admin`, `refresh-data`, `build-reference`, `train-ml`, then start the server. Tests: `.venv\Scripts\python.exe -m unittest discover -s tests` (needs MongoDB only; HDFS-dependent behaviour is tested with fakes).

## Assumptions (the SRS asks for a ReadMe listing them)
- Cities and datasets are the approved set in [docs/datasets.md](docs/datasets.md); POWER is gridded reanalysis, Open-Meteo is modelled, only OpenAQ is observed.
- "Real-time streaming" is implemented as near-real-time REST polling (15 min; OpenAQ hourly); notifications are in-app only.
- Roles are Administrator and Analyst. The application is local (127.0.0.1) and not deployed publicly.
- Apache, Tableau, Impala and RStudio from the SRS software list are not installed; see the traceability matrix.
