# Final manual demo checklist (video NOT recorded; this is the plan for the recording)

Use with [demo-script.md](demo-script.md) (timings and narration). Server: http://127.0.0.1:8000 (start command in the README). Use the real Administrator account you created; never show a password on screen. Create an Analyst beforehand under User Management.

Before starting: MongoDB service up; WSL window open (`wsl -d Ubuntu-24.04 -e sleep infinity`); HDFS running (`hdfs dfs -ls /earthscape`); `/health` shows every component ok.

| # | Step | Do | Expect to see |
|---|---|---|---|
| 1 | Login and permissions | Wrong password, then Administrator login; open User Management; log out; log in as Analyst; open `/admin/users` | generic error message; admin pages work for Administrator; Analyst gets "You do not have permission" and has no admin menu items |
| 2 | Historical climate | Overview (Karachi), Historical Climate (Lahore, monthly), City Comparison | charts painted; NASA POWER label "gridded reanalysis, not station data"; gaps shown as gaps |
| 3 | Current weather and observed PM2.5 | Current Data | freshness table; weather and modelled PM2.5 marked MODELLED; OpenAQ monitors marked OBSERVED with sensor class; STALE badges where old |
| 4 | Historical comparison | Scroll to "Current weather versus historical reference" | latest value vs reference mean +-SD, assessment text, the two charts, and the banner saying the two sources are different models; only temperature and humidity |
| 5 | MapReduce outputs | Terminal: `wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hdfs dfs -ls -R /earthscape/processed`; Trends and Extremes (hot/frost/heavy-rain charts) | `power_daily`, `power_monthly`, `power_yearly` part files; rows 45,655 / 1,500 / 125 (docs/data-architecture.md); YARN ids on the Data Sources page |
| 6 | ML | Trends and Extremes, Correlation, Forecast (choose a variable) | trend table with intervals; anomaly counts; "Predictions are not observations"; held-out test table |
| 7 | Alerts | Alert Rules: create "Lahore PM2.5 > 10" (OBSERVED PM2.5); as Administrator press "Poll all sources now" on Current Data; open Alert History; acknowledge | alert with value, unit, source, observed tag; red badge; status acknowledged; note that email/SMS are not configured |
| 8 | Feedback | As Analyst submit feedback; as Administrator open Feedback Review and mark reviewed | message appears, HTML shown escaped, status changes |
| 9 | HDFS data and manifests | `hdfs dfs -ls -R /earthscape/_manifest`; `hdfs dfs -cat /earthscape/_manifest/power_daily.jsonl \| tail -1`; Data Sources page | sha256, record counts, job and application ids; RAW 125 files, INTERIM 5; after 05:00 PKT on 2026-10-09 also `raw/openmeteo_weather_model/date=2026-10-08` once `seal-raw` has run |
| 10 | Monitoring and backups | `Get-ScheduledTask 'EarthScape*'`; `Get-Content logs\health.jsonl -Tail 1`; `manage.py health-check`; `manage.py backup --keep 7`; `/health` | both tasks Ready; record with timestamp, per-check ok, CPU/memory/disk; new backup folder with BACKUP.json checksums |

Close: state the limits honestly (MODIS not ingested, TLS and encryption at rest not active, no 99% uptime claim, SRS software list partly not installed).

After recording: save the video outside the repository (for example `EarthScape_demo.mp4`); check that no `.env` content, password or API key was shown; add it to the submission only if the portal allows the size. **Until you do this, no video exists.**
