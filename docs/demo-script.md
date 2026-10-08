# Demo video script and recording checklist (video NOT yet recorded)

The step-by-step manual checklist (what to click and what to expect) is in [demo-checklist.md](demo-checklist.md).


Target length 8-10 minutes. Record the browser at 1366x768 plus the terminal for steps 1 and 9.

## Before recording
- [ ] MongoDB service running; HDFS and YARN started ([application.md](application.md)); `logs/` and `backups/` empty or irrelevant.
- [ ] `manage.py refresh-data`, `train-ml` and one `poll-once` done; Administrator account created (do **not** show the password on screen).
- [ ] Server running at http://127.0.0.1:8000; log in once to check; close other windows, hide bookmarks, enlarge browser zoom to 110%.
- [ ] Create one Analyst account (`analyst1`) in advance to show roles.

## Script
1. **Intro (30 s)** - project goal: climate monitoring for Karachi, Lahore, Islamabad, Peshawar, Quetta. Show the architecture diagram from [architecture.md](architecture.md).
2. **Hadoop (1 min)** - terminal: `hdfs dfs -ls -R /earthscape/processed`, `yarn application -list -appStates FINISHED` (three MapReduce jobs); mention 45,655 / 1,500 / 125 rows and the independent verification.
3. **Login and roles (1 min)** - log in as Administrator; show a wrong password message; open User Management; log in as the Analyst and show that admin pages return "not permitted".
4. **Historical dashboards (1.5 min)** - Overview (note the NASA POWER label), Historical Climate (change city/dates, monthly view), City Comparison.
5. **Analytics (2 min)** - Trends & Extremes (trend table, anomalies, extreme-event charts from the MapReduce yearly job), Correlation, Forecast (point out "Predictions are not observations" and the held-out test table).
6. **Live data (1.5 min)** - Current Data: freshness table, modelled vs observed labels, OpenAQ monitors, STALE badges; Data Sources page (provenance, YARN ids, checksums).
6b. **Current vs historical (45 s)** - on Current Data scroll to *Current weather versus historical reference*: explain the two different sources, the band (mean +-1 SD) and why only temperature and humidity are compared.
7. **Alerts (1 min)** - create a rule that is certainly true (e.g. Lahore OpenAQ PM2.5 &gt; 10), "Poll all sources now" as Administrator, show Alert History and the red badge, acknowledge it. State that email/SMS are not configured.
8. **Feedback (30 s)** - submit as Analyst, review as Administrator.
9. **Reliability and security (1 min)** - `/health` JSON; `Get-ScheduledTask 'EarthScape*'`, `Get-Content logs\health.jsonl -Tail 2`; `manage.py backup`; mention CSRF/Argon2id/loopback binding, and that TLS and encryption at rest are documented, not active.
10. **Limits and wrap-up (30 s)** - MODIS blocked on Earthdata credentials; no 99% uptime claim; what was verified (tests, browser check).

## After recording
- [ ] Save as `EarthScape_demo.mp4` outside the repository; check audio and that no credential or `.env` content appears.
- [ ] Add the file to the final ZIP only if it is small enough for the submission portal.
