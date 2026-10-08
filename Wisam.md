# Wisam's guide: what is done, what you must do by hand, and why

Written 2026-10-08 for the EarthScape Climate Agency eProject (Aptech, ACCP Prime 2.0, Semester VI, ADSE II, supervisor Sir Muhammad Umer).
All commands are **PowerShell, run from `D:\aptech semester end project`** unless it says otherwise. Nothing in this file contains a password.
This file is for you only. It is deliberately **not** put into the submission ZIP.

---

## 1. The 60-second picture

| Area | State |
|---|---|
| Web application (login, roles, 14 pages, charts, alerts, feedback) | Done and tested (149 automated tests, real-browser check, both roles, phone width) |
| Hadoop: HDFS storage + 3 MapReduce jobs on YARN | Done and verified (daily 45,655 rows, monthly 1,500, yearly 125) |
| Live data (Open-Meteo weather + air quality, OpenAQ PM2.5) every 15 min | Done, running now |
| Machine learning (trends, anomalies, correlation, forecasts) | Done, retrainable |
| Current vs historical comparison (new) | Done |
| Health check every 15 min + daily backup (Windows Task Scheduler) | Done, confirmed |
| New dashboard theme with your logo and colours | Done (this session) |
| Dashboard screenshots inserted in your document | Done (copy made, original untouched) |
| Final report + ReadMe (.docx and .doc) | Done |
| **Satellite data (MODIS)** | **NOT done: needs your NASA Earthdata login** |
| **Weather-station records** (the SRS names them) | **NOT done: no station dataset was ever chosen** |
| **TLS (https) and disk encryption** | **NOT active: needs your decision/admin rights** |
| **Apache, Tableau, Impala, RStudio** (SRS software list) | **NOT installed: needs your decision** |
| **99% uptime, load balancing** | **NOT demonstrated / not built** |
| **Audit log** (your own document, Security section 6) | **NOT built** (see 3.2) |
| **Demo video** | **NOT recorded: only you can do it** |
| Git commit | Nothing was committed. 60+ files are uncommitted. Your choice (see 9) |

**Where things are**
- Your document with dashboard pictures added: `submission\EarthScape Climate Agency - with dashboard.docx`
- Technical final report: `submission\EarthScape_Final_Report.docx` and `.doc`
- ReadMe the SRS asks for: `submission\ReadMe.docx` and `ReadMe.doc`
- Demo plan: `docs\demo-checklist.md` and `docs\demo-script.md`
- Requirement-by-requirement status (40 rows): `docs\srs-completion-plan.md`
- Submission ZIP: `dist\earthscape_submission.zip` (rebuild it after any change, see 8)
- The app: http://127.0.0.1:8000 (account `admin`)

---

## 2. Do these first (time-critical or security)

### 2.1 Change the admin password (today)
**Why:** you pasted the password into a chat. Treat it as exposed, and do not reuse it anywhere.
1. Open http://127.0.0.1:8000, sign in as `admin`.
2. System > User Management > the `admin` row > reset password (12 or more characters, new, unique).
3. Also create an **Analyst** account there (for the demo and to show role restrictions), e.g. `analyst1`.
4. Never type these passwords in the video or in screenshots.

### 2.2 Seal the first real live day into HDFS (after 05:15 PKT on 2026-10-09)
**Why:** the SRS wants HDFS storage and real-time data integrated with batch. The live readings are staged locally and are uploaded to HDFS only after a UTC day finishes. UTC midnight is 05:00 in Pakistan; the code waits 15 more minutes. Until this happens there is **no real daily live object in HDFS** (only tested with fakes).
1. Keep the laptop on, WSL window open, HDFS running, and the server running. The server does the sealing by itself after 05:15.
2. Or do it by hand any time after 05:15:
   ```powershell
   .venv\Scripts\python.exe src\app\manage.py seal-raw
   ```
   It prints `sealed` for each source.
3. Verify (these are the screenshots/proof for the video and report):
   ```powershell
   wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hdfs dfs -ls -R /earthscape/raw/openmeteo_weather_model
   wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hdfs dfs -ls -R /earthscape/raw/openaq_observed
   wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hdfs dfs -cat /earthscape/_manifest/openmeteo_weather_model.jsonl
   ```
   Expect a folder `date=2026-10-08`, a `polls.jsonl`, and a manifest line with `"status": "ok"`, a `sha256` and `record_count`.
4. If it says `kept locally`, nothing is lost: the staged file stays in `data\staging`. Check that HDFS is running (section 5) and run the command again.
5. When it works, tell me (or edit `docs\limitations.md` and `docs\testing-report.md`: change "no real daily object exists yet" to the real result).

### 2.3 Keep the system alive until you finish
Everything live depends on: laptop on and logged in, a WSL session open, HDFS running, the server process running. If any stops, polling stops (not fatal, just gaps). Start-up cheat sheet is in section 5.

---

## 3. What your own document asks for, and where we stand

I read `EarthScape Climate Agency.docx` fully. It is written in future tense ("will") like a proposal. The project now exists, so some wording must change. Below is each place where the document and the real system differ.

### 3.1 Your document vs what was built
| In your document | What actually exists | What you should do |
|---|---|---|
| ML slide: Linear Regression, Random Forest Regression, Isolation Forest | Theil-Sen + Mann-Kendall trends; Ridge (regularised linear) forecasts vs seasonal baselines; Isolation Forest + robust z-score for anomalies. **No Random Forest.** | Either edit the slide to match (my as-built table is already in the new section), or ask me to add a Random Forest and compare it fairly. Do not leave the slide claiming something that does not exist. |
| Database design slide (users, datasets, data_sources, processing_jobs, model_versions, prediction_results, alert_events, alert_rules) | Collections: users, sessions, alert_rules, alerts, latest_readings, ingest_status, ml_runs, feedback. Dataset/job provenance is in HDFS manifest files, not MongoDB | The slide itself says "align with the final implementation". My new section "As-Built Implementation Notes" has the real table. Replace or annotate the old picture. |
| "Streaming capabilities will be integrated" | Near-real-time REST polling every 15 min (decided earlier; no Kafka/Spark) | Say "near-real-time polling" in the text. Ask the supervisor if that counts as streaming (section 4). |
| "Notify users in real-time" | In-app alerts only; **no email/SMS** | Say so. Email would need an SMTP account (your credentials). |
| "Satellite imagery, weather station records" | Satellite: code ready, **no data** (no credentials). Station records: **none** | Section 6 (MODIS). For stations ask the supervisor (section 4). |
| Security 3: encrypted connections + encryption at rest | **Not active** | Section 7. |
| Security 6: **audit logging** (logins, role changes, dataset imports, config updates, with timestamps and user) | **Not built.** Only an application log file with errors and counts | Honest options: (a) tell me "build the audit log" (about an hour: an `audit` collection, hooks on login/failed login/role change/user create/rule change/refresh/retrain, and an Admin page), or (b) list it under limitations. Your own document promises it, so I recommend (a). |
| Security 4: "uploaded files validated" | There is **no upload page**; data come in through ingestion scripts which validate everything | Write "data enter through validated ingestion scripts; there is no browser upload". |
| Testing plan (13 rows) says actual outcomes will be recorded | Not yet recorded in the document | Use the table in 3.2 and add a test ID column. |
| Hardware: 16 GB RAM | This laptop has 15 GB, memory is often ~92% used | Close other programs during the demo (Chrome, WPS, Cursor, etc.). |
| Software list: Anaconda, RStudio, VS Code, Compass+Shell, Hadoop/HDFS/Apache, Tableau, Impala | See section 4 | Decide with the supervisor. |

### 3.2 Your Testing Plan filled with real results (copy this into the document)
These are real results from this project. "Evidence" names where it is. Add a test ID (T01...) per row as your document asks.

| Test area | Actual result | Status | Evidence |
|---|---|---|---|
| Authentication | Valid login accepted; wrong password gives the same generic error; 5 failures lock 15 min | Pass | tests AuthTest; browser test |
| Authorization | Analyst gets 403 on `/admin/users`, `/admin/feedback`, cannot poll or retrain | Pass | RbacUserTest; browser test |
| Data ingestion | Valid POWER/Open-Meteo/OpenAQ data accepted; bad or truncated responses and non-HDF files rejected with a clear error. **No upload page** | Pass for scripts; upload N/A | test_nasa_power, ModisTest, SourceParsingTest |
| Data validation | Invalid downloads are refused and recorded as failed in the manifest. Individual bad records are **not** separated into a review file | Partial | test_nasa_power |
| Data cleaning | POWER -999 becomes empty + flag, 0 duplicates, units kept as provider units; live duplicates not stored twice; unit changes not compared | Pass | test_power_interim, LiveStorage tests, ReferenceTest |
| HDFS storage | Stored and read back with matching sha256: 125 raw, 5 interim, 3 processed objects | Pass | HDFS listing + manifests |
| Distributed processing | 3 MapReduce jobs equal an independent recomputation | Pass | data-architecture.md, test_power_mapreduce |
| Streaming | New observations processed without duplication (polling) | Pass (polling, not event streaming) | LiveStorageAndAlertsTest |
| Machine learning | Chronological split, preprocessing fitted on training years only, real metrics | Pass | ClimateMlTest, ml-methodology.md |
| Dashboards | City/date/resolution filters change the charts; invalid dates give a clear message | Pass | browser test, DashboardTest |
| Alerts | Rule raised once per episode, updated while true, cleared when false, stale data ignored. The exact "equals threshold" boundary was not tested as its own case | Pass / boundary untested | AlertTest, LiveStorageAndAlertsTest. **Manual check:** create a rule `>= 27.1` vs a current value of exactly 27.1 |
| Backup and recovery | Real backup restored into a separate database: identical counts and users | Pass | docs/testing-report.md |
| Performance | Only monitoring (CPU, memory, disk, poll time). **No test with increasing data volumes was done** | Not done | health.jsonl |

---

## 4. Decisions you must take (ask Sir Muhammad Umer)

The SRS lists software under "Hardware/Software Requirements" without saying what each is for. I did **not** treat them as optional and did not silently drop them. Send him this (edit as you like):

> Sir, for the EarthScape project the SRS lists Apache server, Tableau, Impala server and RStudio. Please confirm which are required for marking. Also: (1) we use near-real-time REST polling instead of event streaming; (2) alerts are in-app, not email/SMS; (3) no weather-station dataset was approved, we use gridded reanalysis (NASA POWER), model data (Open-Meteo) and observed sensors (OpenAQ); (4) satellite data (MODIS) needs NASA login which we are arranging. Is this acceptable?

Based on the answer:

| Item | If required | Effort | My recommendation |
|---|---|---|---|
| **Apache** | Install Apache (Windows build), use my prepared `config\apache\earthscape.conf` as the https reverse proxy. Also gives you the TLS requirement. Steps in 7.1 | 1 to 2 hours | **Do it** (it closes TLS and one software item at once) |
| **Tableau** | Install Tableau Public (free), connect to the three CSV files in `data\processed\app_cache\`, build one dashboard, screenshot it for the report | 1 hour | Do it only if required; low risk |
| **RStudio** | Install R + RStudio Desktop (free), run a short script on the same CSV, screenshot (snippet in 7.4) | 1 hour | Do it only if required |
| **Impala** | Impala runs on Linux with the Hadoop/Hive metastore; heavy, slow, risky on this laptop and in WSL, and it shares HDFS with another project | many hours, may fail | **Do not install on your own.** Ask the supervisor if documenting it as "not installed, design only" is acceptable. If it is mandatory, tell me and I will plan it carefully first. |
| **Anaconda** | Anaconda is a Python distribution; we use JupyterLab + a venv (kernel `EarthScape`), which is the same thing in practice | 30 min | Say "Jupyter used" and show the notebook `notebooks\01_power_eda.ipynb` running |
| **mongosh** (Mongo "Shell") | Run `winget install MongoDB.Shell` in an **Administrator** PowerShell (the non-admin install failed) | 5 min | Do it, then show `mongosh` listing collections in the video |
| **PyCharm/VS Code** | VS Code is used | 0 | nothing |

---

## 5. Start-up and shutdown cheat sheet (do this every time you reboot or before the demo)

**Why:** HDFS/YARN are not services; they stop when WSL stops. MongoDB is a Windows service. The web server is a normal process.

1. MongoDB: `Get-Service MongoDB` (must say Running; if not, `Start-Service MongoDB` in an Administrator PowerShell).
2. Keep WSL alive (leave this window open): 
   ```powershell
   wsl -d Ubuntu-24.04 -e sleep infinity
   ```
3. In a second PowerShell, start HDFS:
   ```powershell
   wsl -d Ubuntu-24.04 -e /opt/hadoop/sbin/start-dfs.sh
   wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hdfs dfsadmin -safemode get
   ```
   Wait until it says `Safe mode is OFF` (about 15 s). Never force it.
4. YARN is needed **only** to run MapReduce again (not for the demo). Steps: `docs\environment.md`.
5. Start the server (detached, survives closing the terminal):
   ```powershell
   Start-Process "D:\aptech semester end project\.venv\Scripts\python.exe" -ArgumentList '-m','uvicorn','app.main:create_app','--factory','--app-dir','src','--host','127.0.0.1','--port','8000' -WorkingDirectory "D:\aptech semester end project" -WindowStyle Hidden
   ```
6. Check: open http://127.0.0.1:8000/health, every item must say `ok`/`running`.
7. Stop the server: `Get-NetTCPConnection -LocalPort 8000 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }`
8. Scheduled tasks (health every 15 min, backup 02:00) check: `Get-ScheduledTask 'EarthScape*'`. They only run while you are logged in; the backup runs at next start if the laptop was off at 02:00.

**If the black WSL windows keep flashing:** I fixed the cause (the health check and server opened a console per `wsl` call). If it still happens, run `Stop-ScheduledTask 'EarthScape health check'; Disable-ScheduledTask 'EarthScape health check'` and tell me.

**Common problems**
- Page says database unavailable: MongoDB service is stopped (step 1).
- `/health` says `hdfs: unavailable`: steps 2 and 3. Dashboards still work from the local cache.
- Port 8000 busy: step 7 and start again.
- "Safe mode is ON" stays on: wait a minute; do not leave safe mode manually.
- Never run `hdfs namenode -format`, never touch `/urbantransit*` in HDFS (another project shares this Hadoop).

---

## 6. Satellite data (MODIS): the one real data gap

**Why:** the SRS names satellite imagery. The downloader, validator and HDF4 reader exist and are tested on a synthetic file, but no real file has ever been downloaded because NASA requires a login. Nothing was invented.

1. Create a free account at https://urs.earthdata.nasa.gov (Register). Use an email you control.
2. Sign in > **Applications > Authorized Apps > Approve More Applications** > approve **LP DAAC Data Pool** (and "LP DAAC OpenSearch" if listed). Without this the download returns a login page.
3. Profile > **Generate Token**. Copy the token (it expires, usually in 60 days).
4. Open `D:\aptech semester end project\.env` in Notepad and add one line (no quotes, no spaces): `EARTHDATA_TOKEN=<paste token>`. Never paste it in chat, screenshots or the video. (`.env` is never committed or zipped.)
5. Check prerequisites (prints only true/false, not the token):
   ```powershell
   .venv\Scripts\python.exe src\ingestion\modis.py check
   ```
   Needs `earthdata_credentials_configured: true`, pyhdf true, HDFS true.
6. Download **only the two-granule pilot** (about 10 MB):
   ```powershell
   .venv\Scripts\python.exe src\ingestion\modis.py pilot --tile h24v05 --year 2015 --count 2
   ```
   It prints, per granule, the real land-surface temperature for the cities in that tile, the valid and cloud pixel counts and the scale factor.
7. Sanity check the numbers: daytime LST for Lahore/Islamabad (Kelvin) should be plausible (e.g. 270 to 330 K). Then tell me: I will integrate a real sample into the app. The full download (1,515 granules, **11.2 GB**, about 140 GB free on D:) only after you say yes.
8. If the pilot says "download failed: HTTP 401/403": the app approval in step 2 is missing, or the token expired.

---

## 7. Security items that need you (admin rights or a decision)

### 7.1 TLS / https with Apache (closes "encryption in transit" and one software item)
**Why:** the SRS and your document require encrypted connections. Today the browser talks plain http to `127.0.0.1` (safe on one machine but not "encrypted").
1. Download Apache 2.4 for Windows (Apache Lounge, `httpd-2.4.x-win64-VS17.zip`) and the Visual C++ redistributable it asks for. Extract to `C:\Apache24`.
2. Make a certificate (Git's openssl works; run in PowerShell). The folder must be **outside** the project:
   ```powershell
   New-Item -ItemType Directory C:\certs -Force
   & "C:\Program Files\Git\usr\bin\openssl.exe" req -x509 -newkey rsa:3072 -nodes -keyout C:\certs\earthscape.key -out C:\certs\earthscape.crt -days 365 -subj "/CN=earthscape.local" -addext "subjectAltName=DNS:earthscape.local,IP:127.0.0.1"
   ```
   (I tested this command; it makes a certificate for earthscape.local and 127.0.0.1.)
3. As Administrator add this line to `C:\Windows\System32\drivers\etc\hosts`: `127.0.0.1 earthscape.local`
4. Add to the end of `C:\Apache24\conf\httpd.conf`: `Include "D:/aptech semester end project/config/apache/earthscape.conf"` (if Apache complains a module is already loaded, delete the matching `LoadModule` line from my file).
5. `C:\Apache24\bin\httpd.exe -t` must say `Syntax OK`; then run `C:\Apache24\bin\httpd.exe` (leave open).
6. Restart the web app with cookies for https: set `$env:COOKIE_SECURE='1'` in the same PowerShell, then the start command from section 5.
7. Open https://earthscape.local. The browser warns about the self-signed certificate: import `C:\certs\earthscape.crt` into "Trusted Root Certification Authorities" (current user) to remove the warning. Take a screenshot of the padlock for the report.
8. Only after this works may we write "TLS active". Tell me and I update the docs. The key file must never go in the ZIP or git.

### 7.2 Disk encryption (BitLocker)
**Why:** "encryption during storage". I could not read the real state without admin rights, and I changed nothing.
1. Administrator PowerShell: `manage-bde -status` (look at "Conversion Status" and "Protection Status" for C: and D:).
2. If not encrypted and you agree: `manage-bde -on D: -RecoveryPassword` (and C: if your Windows asks for it). **Save the printed 48-digit recovery key outside the laptop** (phone photo, Microsoft account, paper). If you lose it and Windows asks for it, the data is gone.
3. Encryption runs in the background; the laptop stays usable. Screenshot `manage-bde -status` afterwards.
4. HDFS-level encryption zones are not recommended here (needs a KMS and rewriting all data).

### 7.3 mongosh
Administrator PowerShell: `winget install MongoDB.Shell`. Then `mongosh` and `use earthscape`, `show collections`. (Do not print the users collection in the video: it contains password hashes.)

### 7.4 Tableau / RStudio (only if the supervisor says they are required)
- Tableau Public: Connect > Text file > `D:\aptech semester end project\data\processed\app_cache\monthly.csv` (daily.csv and yearly.csv also). Build a line chart of `temperature_2m_mean_c` by `month` per `city_id`, screenshot, add to the report as "external BI view of the MapReduce output".
- RStudio: install R then RStudio Desktop; in the console:
  ```r
  m <- read.csv("D:/aptech semester end project/data/processed/app_cache/monthly.csv")
  m$month <- as.Date(paste0(m$month, "-01"))
  plot(m$month[m$city_id=="karachi"], m$temperature_2m_mean_c[m$city_id=="karachi"], type="l", xlab="Month", ylab="Mean temperature at 2 m (C)", main="Karachi (NASA POWER, MapReduce monthly)")
  ```
  Screenshot it. Label both honestly as extra views of the same data.

---

## 8. Documents: what to do with the Word files and the ZIP

You now have **two** documents. Decide which is "the report":

1. **`submission\EarthScape Climate Agency - with dashboard.docx`**: your team's document (cover, certificate, objectives, requirements, workflow, ML slide, testing plan, security, DB design, conclusion) with two new sections inserted before "Testing & Troubleshooting": **Dashboard Implementation** (14 screenshots of the new theme) and **As-Built Implementation Notes** (real collections and ML methods). Your original in Downloads is unchanged.
2. **`submission\EarthScape_Final_Report.docx` / `.doc`**: the long technical report generated from our project docs (HDFS layout, MapReduce, ML tables, testing, limitations, requirement matrix).

Recommended: submit **both**, document 1 as the main project document and document 2 as the technical report/appendix. Check with the supervisor if he wants only one.

**Steps for document 1 (your document)**
1. Open it in Word (or WPS). It will ask to update fields: click **Yes** (this refreshes the Table of Contents so "Dashboard Implementation" and "As-Built Implementation Notes" appear). If it does not ask: right-click the Table of Contents > Update Field > Update entire table.
2. Scroll through the new section: check pictures are not cut off and captions sit under pictures. The document grew to about 59 pages because screenshots are large.
3. Replace or annotate the old **Machine Learning Models** and **Database Design** pictures (3.1). Delete "Random Forest" from the ML slide unless we build it.
4. Change future tense ("will") to present/past where the feature really exists. Keep "will" only for future work.
5. Fill the Testing Plan "Actual result" column using 3.2, add test IDs, and replace "Actual outcomes will be recorded after the tests are executed".
6. Add to Limitations/Scope: MODIS pending, stations not used, TLS/encryption status, polling not streaming, in-app alerts, no audit trail (unless built).
7. Update the Hardware section only if the supervisor asks (this machine: HP EliteBook 745 G5, 15 GB RAM).
8. Check names, student IDs, batch, dates on the cover/certificate pages yourself. I did not change them.
9. Save as `.docx` and also **PDF** (File > Save As > PDF) as a backup copy.

**Steps for document 2 (technical report) and ReadMe**
1. Open `EarthScape_Final_Report.docx` and `ReadMe.docx`: type your name, roll number and centre where the report says "to be completed by the student".
2. If the SRS/supervisor wants `.doc` (Word 97-2003): use the `.doc` copies I made with WPS, or open the `.docx` in **Microsoft Word** > Save As > Word 97-2003. Open the `.doc` in real Word once to check the layout (I could only check in WPS).
3. If you edit them in Word, you do not need Python. If you want me to regenerate after changes to our docs: `.venv\Scripts\python.exe scripts\build_report.py --tests 149` then re-convert.

**Rebuild the ZIP (always last)**
```powershell
.venv\Scripts\python.exe scripts\package_submission.py
```
Then open `dist\earthscape_submission.zip` and check: contains `src`, `tests`, `notebooks`, `config`, `docs`, `submission`, `README.md`, `requirements.txt`, `.env.example`; does **not** contain `.env`, `.venv`, `data`, `logs`, `backups`, `*.key`, `*.pem`, the SRS file, or this `Wisam.md`. Put the **video** next to the ZIP (do not put a huge video inside it unless the portal allows).

---

## 9. Git (your decision)
I never committed. `git status` shows about 60 uncommitted files, last commit `1aec270`. If your supervisor wants a repository: review `git status`, make sure `.env` is not listed (it is ignored), then:
```powershell
git add -A
git commit -m "complete EarthScape application, docs and tests"
```
Do not push anywhere public: the repo history must never contain `.env`, keys, or backups. Ask me if you want me to do it.

---

## 10. The demo video (only you can record it)

**Why:** the SRS requires "video displaying complete working of the application". It does not exist yet.
1. Prepare: section 5 start-up; sign in once; close other apps; browser zoom 110%; hide bookmarks; use a **fresh Analyst account** for the roles part. Password boxes show dots; still do not type credentials on screen if you can avoid it (cut that part or blur).
2. Record with Windows **Win+G** (Xbox Game Bar) or OBS Studio (free). 1920x1080, microphone on. Aim for 8 to 10 minutes.
3. Follow `docs\demo-checklist.md` (10 steps: login/roles, historical, current + observed PM2.5, comparison, MapReduce outputs, ML, alerts + acknowledge, feedback, HDFS manifests, monitoring + backup). Narration hints are in `docs\demo-script.md`.
4. Say honestly at the end: MODIS status, polling instead of streaming, in-app alerts, TLS/encryption status, no uptime claim.
5. After: watch it once, make sure no password, `.env`, token or API key appears; save as `EarthScape_demo.mp4` outside the project.

---

## 11. Final submission checklist (tick in order)
- [ ] Admin password changed; Analyst account exists (2.1)
- [ ] First live day sealed into HDFS and verified (2.2)
- [ ] Supervisor asked about the software list, streaming, alerts, stations (4)
- [ ] MODIS pilot done **or** documented as blocked (6)
- [ ] Apache/TLS done **or** documented as not active (7.1); BitLocker status checked (7.2)
- [ ] mongosh installed and shown, or documented (7.3)
- [ ] Tableau/RStudio/Impala decision recorded (4, 7.4)
- [ ] Audit log built **or** listed as a limitation (3.1)
- [ ] Your document: TOC updated, ML/DB slides corrected, testing table filled, tense fixed (8)
- [ ] Technical report + ReadMe: your details added, `.doc` checked in Word (8)
- [ ] Demo video recorded and reviewed (10)
- [ ] `docs\srs-completion-plan.md` statuses updated if anything above changed (tell me and I update; do not mark anything "complete" that you have not run)
- [ ] ZIP rebuilt, opened and checked for secrets (8)
- [ ] A second copy of everything on a USB drive or cloud drive you own
- [ ] Day before: full start-up rehearsal (5) and the demo checklist once

## 12. Questions the examiner may ask (and honest answers)
- *Is this real-time/streaming?* Near-real-time REST polling every 15 min, by design; no Kafka/Spark.
- *Is the climate data observed?* NASA POWER is gridded reanalysis (MERRA-2/CERES), not station data. Open-Meteo is a model. Only OpenAQ PM2.5 is observed (mostly low-cost sensors, sparse).
- *Why Hadoop for 67 MB?* The SRS requires it; the jobs demonstrate the architecture on one node; scaling was not demonstrated.
- *How do you know MapReduce is right?* Each output was recomputed independently from the INTERIM data with exact arithmetic and matched.
- *How good are the forecasts?* Better than the seasonal baseline in 18 of 20 cases but gains are modest; precipitation skill is low; see `docs\ml-methodology.md`.
- *Is it secure?* Argon2id, server-side sessions, CSRF, role checks, input validation; TLS and disk encryption are prepared/not active unless you finished section 7.
- *Where is satellite data?* Needs NASA credentials: say whether the pilot was done.
- *Is 99% uptime proven?* No. One laptop, WSL daemons stop with the session; health history exists in `logs\health.jsonl`.
