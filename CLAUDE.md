# EarthScape Climate Agency
> Last synced: 2026-10-08

Single Aptech final-semester big-data/data-science project (climate monitoring for Pakistan). Not a multi-project workspace; no multi-session routing; session handoff files DECISIONS.md and CONTEXT.md in use since 2026-10-07. Status (final phase 2026-10-08): all approved phases implemented, plus the current-vs-historical comparison and a health check + daily backup via Task Scheduler. The real SRS was found at C:\Users\AKL\Downloads\Big Data-EarthScape_Climate_Agency.docx and is traced in docs/srs-completion-plan.md. Still open: MODIS ingestion (needs Earthdata credentials), TLS activation and encryption at rest (need approval), uptime, Apache/Tableau/Impala/RStudio (SRS software list, undecided), demo video. NASA POWER RAW/INTERIM/daily/monthly/yearly done (MapReduce on YARN, verified); web app, live polling (Open-Meteo, OpenAQ), alerts, ML analytics, tests and docs done. See README.md and docs/srs-completion-plan.md (22 complete, 8 partial, 10 blocked/not implemented of 40).

## 1. Authority
The Aptech spec and the user's explicit instructions are authoritative. Never invent requirements, datasets, models, APIs, dashboards or business rules. If an unspecified decision affects architecture, data, technology, ML, security, UI, deployment or the academic spec: STOP and ask. Open list: [docs/open-decisions.md](docs/open-decisions.md).
Docs (read only when needed): [docs/environment.md](docs/environment.md) setup/start commands | [docs/datasets.md](docs/datasets.md) providers, coordinates, provenance | [docs/data-architecture.md](docs/data-architecture.md) HDFS layout + ingestion design (confirmed by the user 2026-10-06).

## 2. Project facts
- Scope (decided): Pakistan; Karachi, Lahore, Islamabad, Peshawar, Quetta. Use the coordinates in docs/datasets.md; never change cities.
- Approved datasets (change nothing without asking): NASA POWER hourly 2001-01-01..2025-12-31 (T2M, RH2M, PRECTOTCORR, WS2M, PS, ALLSKY_SFC_SW_DWN; gridded reanalysis, NOT station data) | Open-Meteo current weather (model-based, REST polling, NOT streaming) | OpenAQ = OBSERVED air quality (PM2.5 primary, mostly low-cost sensors) | Open-Meteo Air Quality = MODELLED pollutants (never present modelled as observed) | MODIS MOD11A2 v061 LST 2015-01-01..2025-12-31, tiles h24v06/h24v05/h23v05, HDF4-EOS. Raw stays provider-native.
- Spec: role-based auth (Administrator, Analyst are examples; role set not confirmed); HDFS storage; Hadoop MapReduce is mandatory (never silently replace with Spark); real-time processing integrated with batch; ML for anomaly detection, trend prediction, correlation (updatable); interactive dashboards; threshold alerts; feedback/support; monitoring, encryption at rest/in transit, 99% uptime, scheduled backups, horizontal scaling, docs + demo video.
- Decided 2026-10-08 (docs/open-decisions.md): polling thread (no Kafka/Spark), ML methods, MongoDB as app DB, in-app alerts and feedback, roles Administrator/Analyst, FastAPI dashboard, pyhdf. NOT chosen (implement nothing until decided): roles of Impala, Tableau, Apache Server, R/RStudio; TLS/encryption at rest activation; email/SMS.

## 3. Directories
`data/raw` source data and provider samples (read-only, git-ignored) | `data/interim` validated/cleaned | `data/processed` analysis-ready | `src/{ingestion,processing/{batch,mapreduce,realtime},ml,app}` reusable code | `notebooks` exploration only | `config` non-secret config (`config/hadoop` = EarthScape Hadoop conf) | `tests` | `docs` | `artifacts` generated models/metrics/figures (git-ignored). HDFS mirrors raw/interim/processed under `/earthscape`. Follow the existing layout; create new dirs only when needed.

## 4. Workflow and phases
collection → validation → cleaning → preprocessing → EDA → features → modeling → evaluation → visualization/reporting. Do only the phase the task needs; never rebuild the whole pipeline because one phase changed. Keep raw, interim/processed, exploration, reusable code, artifacts and reports separate.

## 5. Hadoop/HDFS/MapReduce
Hadoop 3.4.3 in WSL Ubuntu-24.04 (`/opt/hadoop`, Java 17, `hdfs://localhost:9000`), shared with the UrbanTransit project. Use HDFS path `/earthscape` only. Never reformat the NameNode, touch `/urbantransit*`, or edit `/opt/hadoop/etc/hadoop`. EarthScape uses its own conf: `source config/earthscape-env.sh` (sets `HADOOP_CONF_DIR`). HDFS/YARN are not auto-started and daemons die when WSL has no open session: follow the start steps in docs/environment.md. MapReduce verified through YARN. MapReduce style is Hadoop Streaming (Python): `src/processing/mapreduce/run_power_job.py daily|monthly`.

## 6. Data integrity
- Never modify raw data; transformations are scripted and reproducible. Raw files are immutable; fixes happen downstream.
- Tag values observed / transformed / imputed / derived / predicted / synthetic (and modelled vs observed environmental data). Never fabricate readings, labels or missing observations; no fake datasets, models or metrics.
- No silent row/column loss; check types, nulls, duplicates, ranges, join cardinality (no accidental many-to-many). Missing data stays visible (e.g. POWER -999 becomes null plus flag in interim, not an invented value).
- Extreme values: classify (invalid, sensor error, legitimate event) before removing; never drop because unusual.
- Treat datasets as possibly sensitive; don't upload project data to third-party services without authorization.

## 7. Leakage, reproducibility, evaluation
- Split first; fit preprocessing (scaling, imputation, encoding, selection, resampling, dimensionality reduction, target-derived and temporal features) on train only. Time series: chronological splits unless an approved method says otherwise.
- Explicit documented seeds; never tune seeds for better numbers. Dependencies pinned.
- Metrics fit the problem (classification: precision/recall/F1/PR-AUC/confusion matrix as justified; regression: MAE/RMSE/R2; unsupervised: method-appropriate); never train-only; compare models under identical conditions; no cherry-picking.
- Never report a metric, result or passing test that did not actually run and produce it.

## 8. EDA, notebooks, visualization
- EDA separates OBSERVATION / INTERPRETATION / HYPOTHESIS and covers distributions, missingness, outliers, correlations, imbalance, temporal and geographic patterns, leakage, impossible values. No conclusions the data doesn't support.
- Notebooks: linear order, no hidden state, no giant cells, rerunnable from clean; reusable logic goes in `src/`. Jupyter kernel `EarthScape` points to `.venv`.
- Charts: honest axes, titles, axis labels with units, legends, no hidden categories, no misleading aggregation, predictions visually distinct from observations, correlation never framed as causation.

## 9. Security
Never commit, print or log passwords, keys, tokens, DB credentials, connection strings or `.env` values (logs, notebooks, docs, screenshots, chat). Secrets live only in environment variables or the git-ignored `.env` (e.g. `OPENAQ_API_KEY`); `.env.example` lists names only, with empty values. Don't create real credentials without approval. For APIs/databases: parameterized queries, validate external input, least privilege, no sensitive logging, follow the auth design. No full security audit for ordinary tasks.

## 10. Scope, dependencies, performance
- Don't touch other projects or global Java/Python/Hadoop/Mongo config; don't overwrite files unread; no installs of major software without approval. Don't delete data, models, notebooks or artifacts without need and authorization; prefer reversible changes. Report unrelated problems separately; no broad cleanup.
- Python deps live in project `.venv`, pinned in `requirements.txt` (ipykernel plus, since 2026-10-08, fastapi, uvicorn, jinja2, python-multipart, pymongo, argon2-cffi, httpx). Before adding a package: check existing deps, prefer established libraries, check Python compatibility, ask before heavy ones, record it in `requirements.txt`. No installs of Spark, Kafka, Flink, R, Impala, Tableau, Apache, Conda, HDF4/geo libraries until their decision is made.
- Measure before optimizing; log processing times; avoid needless full-data copies and row-by-row loops on large data.

## 11. Simple code, comments, Git
- Simplest correct implementation: no unnecessary abstractions, patterns, classes, wrappers, helper layers, interfaces, factories, utilities, indirection, config or dependencies; nothing for hypothetical needs. Don't refactor clear working code just to tidy it. Between equally correct options pick the easiest to understand and test; simple means understandable, not clever or artificially short.
- Comments minimal: at most one concise comment above a main or non-obvious block (e.g. `# Validate climate records before processing`); explain WHY (constraints, data assumptions, workarounds), never narrate lines. Real methodology may get proper docstrings/docs.
- Git: don't commit unless the user asks or the task says so; leave changes uncommitted and report them. No tiny per-edit commits; group related work. Short plain human messages (e.g. `add climate data validation`), no prefixes or emojis.

## 12. Skill routing (opt-in; none by default; one primary skill per phase; stop when the phase ends)
| Task | Skill |
|---|---|
| trivial/localized change, docs edit | none or `surgical-patch` |
| unknown bug / unexpected result / unclear cause | `investigate-first` |
| new bounded implementation (ingestion step, validator, job) | `lean-build` |
| structural refactor of existing code | `safe-refactor` |
| schema/data migration | `migration` |
| dashboard/UI | `impeccable` (+ `dataviz` for charts) |
| security-sensitive work | `security-review` |
| CI/CD | `ci-cd-builder` |
| focused final verification | `verify-and-stop` |
| anything else | the one most relevant skill, only if justified |
Defaults, not a pipeline: never chain investigate → plan → implement → audit → review for every task. Subagents are not the default; use one only for clearly separable or parallel work, never several doing the same analysis.

## 13. Verification
Smallest meaningful check first, expand only with risk. Reusable logic: test transformations, validators, edge cases. Pipelines: columns, types, row counts, nulls, uniqueness, ranges/categories. ML: pipeline runs, train/test separation intact, real metrics, valid artifacts. No heavy verification for tiny docs changes. Tests: `.venv\Scripts\python.exe -m unittest discover -s tests` (stdlib unittest).

## 14. Context efficiency
Read only needed files; don't preload the project or re-read still-valid context; correctness before token savings.

## 15. Commands
- Hadoop/HDFS (WSL): `wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hdfs dfs -ls /earthscape`; version: `... /opt/hadoop/bin/hadoop version`. Start/verify services: docs/environment.md.
- Python: `.venv\Scripts\python.exe`; Jupyter: `jupyter lab` (kernel `EarthScape`).
- POWER ingestion: `.venv\Scripts\python.exe src\ingestion\nasa_power.py --city karachi --start 2001-01-01 --end 2001-12-31` (details in docs/data-architecture.md; needs HDFS running).
