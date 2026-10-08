# Security and reliability status

Local academic deployment: the app binds to **127.0.0.1:8000** only. Nothing is exposed publicly. Statuses below say what is *active and tested* and what is *documented only*.

## Active and tested
| Control | Implementation | Verification |
|---|---|---|
| Authentication | Argon2id hashes, generic login errors, similar timing for unknown users, 5-failure lockout (15 min) | `AuthTest`, `PasswordTest` |
| Sessions | server-side, random token in HttpOnly SameSite=Lax cookie, only SHA-256 stored, TTL expiry, new id at login, ended on logout/disable/role change/password reset | `AuthTest`, `RbacUserTest` |
| CSRF | per-session token required on every state-changing POST (including login and logout) | forged-token tests on login, logout, feedback, alerts, users, polling, training |
| Authorization | `need_user` / `need_admin` on every page and API; Analysts get 403 on admin routes; last-admin protection | `RbacUserTest`, `LiveStorageAndAlertsTest` |
| Input validation | usernames, passwords (12-128), feedback length/category, alert rule fields (finite numbers, enumerated variables/operators/cities), ObjectId format, date and range limits on APIs, city/variable whitelists | `AlertTest`, `FeedbackTest`, `DashboardTest`, `AnalyticsPagesTest` |
| MongoDB safety | no string-built queries; user input is only used as values or after whitelist/ObjectId validation; unique indexes enforce username/rule/reading/alert uniqueness | `MongoTest` |
| XSS | Jinja2 autoescape; feedback containing HTML is shown escaped; CSP `default-src 'self'` with only jsDelivr scripts/styles; no inline scripts | `FeedbackTest`, browser check shows no console errors |
| Headers | `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Cache-Control: no-store`, CSP | `AuthTest.test_security_headers_and_no_openapi` |
| Safe errors | no stack traces or exception text to users; 503 when MongoDB is down; API docs disabled | `DashboardTest`, `MongoTest` |
| Secrets | `OPENAQ_API_KEY` and any Earthdata credentials come only from the environment/git-ignored `.env`; the OpenAQ key is sent only in a request header, never logged or stored; `.env.example` lists names only; admin password is prompted, never hardcoded | code review; `grep` shows no key in tracked files |
| Logging | rotating `logs/earthscape.log` (git-ignored) with error types, paths and counts only; no passwords, tokens, request bodies or headers | by design |
| Data integrity | manifests with sha256 for RAW/INTERIM/PROCESSED; refresh validates the cache against them; RAW never overwritten; independent recomputation of every MapReduce output | `DataTest`, MapReduce verification |
| Health | `GET /health`: `app`, `mongodb`, `hdfs`, `data_cache`, `poller`, `live_sources` (fresh/stale) reported separately; HTTP 200 while the app runs | `HealthTest`, smoke test |
| Backup / restore | `manage.py backup [--hdfs] [--keep N]` exports MongoDB collections (not sessions) and optionally HDFS manifests + PROCESSED as JSON with sha256 in `BACKUP.json`; `restore` verifies checksums first and refuses to overwrite non-empty collections unless `--replace` | `BackupRestoreTest` (round trip, protection, tamper detection) |

Backups contain password hashes: the `backups/` directory is git-ignored; keep copies on protected storage.

## Monitoring and scheduled backups (ACTIVE since 2026-10-08, confirmed with Windows Task Scheduler)
| Task (current user, runs while logged on) | Schedule | Command | Verified |
|---|---|---|---|
| `EarthScape health check` | every 15 min | `.venv\Scripts\pythonw.exe src\app\manage.py health-check` | task state Ready; a scheduler-started run finished with result 0 and appended to `logs/health.jsonl` |
| `EarthScape backup` | daily 02:00 (runs at next start if the laptop was off) | `... manage.py backup --keep 7` | scheduler-started run finished with result 0 and wrote a checksummed backup; the next automatic run is 02:00 |

`health-check` records a UTC timestamp and, per check, ok/failed with detail: **application** (`/health`), **mongodb** (ping), **hdfs** (processed layer readable), **live_polling** (freshness of the three live sources), **backup** (newest backup exists, is under 36 h old and its checksums match), **disk** (>= 5 GB free) and **resources** (CPU and memory of the system, memory of the app and `mongod`; flagged only above 95%). Failures are listed in `failures`; the process exit code is 1. It only records: it does not restart, repair or notify anything (no email/SMS). View history: `Get-Content logs\health.jsonl -Tail 5`.

Retention: `backup --keep N` (default 7) deletes only older folders named `earthscape_<UTC stamp>` that contain a `BACKUP.json`. Backups hold users (Argon2 password **hashes**, no plaintext), feedback, alert rules/alerts, ML runs, ingest status and latest readings; sessions and `.env` values are never included (the OpenAQ key was searched for in a real backup: not present). `backups/` is git-ignored and excluded from the ZIP; keep copies on protected storage. The scheduled job does not copy HDFS (`backup --hdfs` is a manual extra; HDFS data is reproducible from the providers and protected by manifests).

**Restore verified (real data, 2026-10-08):** a real backup was restored into the separate database `earthscape_restore_check` with `restore --path <folder> --target-db earthscape_restore_check`: all 7 collections had the same document counts as production and the users were identical; the scratch database was then dropped.

Manage the tasks: `Get-ScheduledTask 'EarthScape*'`, `Start-ScheduledTask 'EarthScape backup'`, `Unregister-ScheduledTask 'EarthScape backup' -Confirm:$false`. To register them again (no elevation needed): see `scripts
egister_tasks.ps1`.

## TLS (encryption in transit): PREPARED, NOT ACTIVE
Browser to app traffic is plain HTTP on loopback; the app binds 127.0.0.1:8000 only. [config/apache/earthscape.conf](../config/apache/earthscape.conf) was reviewed and corrected (TLS 1.2/1.3 only, AEAD ciphers, session tickets off, proxy to 127.0.0.1:8000, HTTP to HTTPS redirect, HSTS, **listens on 127.0.0.1 only** so nothing becomes public by accident). It has **not** been run because Apache HTTP Server is not installed (decision pending).
Certificate requirements: a certificate and private key; local demo = self-signed with `subjectAltName=DNS:earthscape.local,IP:127.0.0.1` (the `openssl req -x509 ...` command at the top of the conf file was run and produced a correct certificate); real deployment = a CA-issued certificate for the real host name. Keep the key outside the project. Then start the app with `COOKIE_SECURE=1`. Provider calls (Open-Meteo, OpenAQ, NASA) already use HTTPS. HDFS and MongoDB traffic stays on loopback, unencrypted.

## Encryption at rest: NOT ACTIVE (status checked read-only)
- Windows 11 Pro on an HP EliteBook 745 G5 with a TPM present. `Get-BitLockerVolume` and `manage-bde -status` both returned **Access denied** without Administrator rights, so the real BitLocker state of C: and D: **could not be confirmed**. The Explorer property `System.Volume.BitLockerProtection` returned 2 for both drives, which is commonly the "not protected" value, but this is indicative only.
- To confirm: open an elevated PowerShell and run `manage-bde -status`. WSL's HDFS data lives on D: (`D:\WSL`), the MongoDB files on the system drive.
- **Steps that need your approval** (nothing was changed): (1) in an elevated prompt run `manage-bde -on D: -RecoveryPassword` (and C: if wanted), save the printed recovery key outside the laptop; encryption proceeds in the background. (2) HDFS encryption zones (`hadoop key create`, `hdfs crypto -createZone`) would need a running Hadoop KMS and rewriting the data; not recommended here. MongoDB community edition has no encrypted storage engine; disk encryption covers it.

## Reliability limits (stated honestly)
- **99% uptime is not demonstrated.** One laptop, WSL daemons stop when the WSL session ends (environment.md), no supervisor or failover. Measuring uptime would need a service wrapper and a monitored window.
- **Monitoring** is the health endpoint, the scheduled health check history (`logs/health.jsonl`), the log file and on-page staleness/failure indicators; there is no notification about outages (no email/SMS) and no CPU/memory metrics.
- **Failure handling:** provider failures are retried (4 attempts, backoff, `Retry-After`), recorded in `ingest_status`, shown on the Current Data page, and never stop other cities or sources; HDFS down keeps raw files staged locally for the next attempt; MongoDB down gives 503 pages; a bad data cache is rejected with a clear message.
- **Horizontal scaling:** the batch layer uses HDFS/YARN (scale-out capable by design); this installation is one node and scaling was not demonstrated. The web app is a single process.
