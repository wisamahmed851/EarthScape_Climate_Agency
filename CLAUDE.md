# EarthScape Climate Agency

> Last synced: 2026-10-05

Single Aptech final-semester big-data/data-science project. Session handoff files (DECISIONS.md, CONTEXT.md) are used at the user's request via /session-handoff; no other multi-session routing.

## 1. Authoritative requirements
Aptech spec + user's explicit instructions are authoritative. Never invent requirements or features. If an unspecified decision affects architecture, data, technology, ML, security, UI or deployment: STOP and ask. Open list: [docs/open-decisions.md](docs/open-decisions.md). Environment: [docs/environment.md](docs/environment.md).

## 2. Known architecture (only what the spec says)
- Auth with roles (Administrator, Analyst are examples; role set not confirmed); role-based data access.
- Ingestion: satellite imagery, weather stations, environmental sensors; historical + real-time.
- Storage: HDFS, partitioned for retrieval. Scheme decided only after real datasets are known.
- Processing: Hadoop MapReduce (mandatory; never silently replace with Spark) for patterns, anomalies, correlations; must handle missing data.
- Real-time streaming, integrated with batch. Technology NOT chosen.
- ML: anomaly detection, trend prediction, correlation analysis; models updatable with new data. Algorithms NOT chosen.
- Visualization: interactive, customizable dashboards for patterns, anomalies, predictions.
- Threshold-based alerts (transport NOT chosen); feedback/support (mechanism NOT chosen).
- NFR: monitoring (performance, resources, processing times), encryption at rest/in transit, 99% uptime, scheduled backups, horizontal scaling/load balancing, docs + demo video.
- Spec-listed tools with unresolved roles: MongoDB, Impala, Tableau, Apache Server, R/RStudio. Do not assign responsibilities.

## 3. Directories
`data/raw` read-only source | `data/interim` validated/cleaned | `data/processed` analysis-ready | `src/ingestion` | `src/processing/{batch,mapreduce,realtime}` | `src/ml` | `src/app` dashboards/auth UI | `notebooks` exploration only | `config` non-secret config | `tests` | `docs` | `artifacts` generated models/metrics/figures. Data and artifacts are git-ignored. Create further dirs only when needed.

## 4. Workflow
raw → validation → cleaning/transform → processed → analysis/modeling → results/visualization. Work only the phase the task needs; don't rerun or redesign the whole pipeline.

## 5. Hadoop/HDFS/MapReduce
Hadoop 3.4.3 runs in WSL Ubuntu-24.04 (/opt/hadoop, Java 17, `hdfs://localhost:9000`, shared with another project). Use HDFS path `/earthscape` only. Never reformat the NameNode or edit shared Hadoop config without asking. No MapReduce business jobs before data/schema decisions.

## 6. Boundaries
Real-time, ML, visualization, notifications, support: implement nothing until their open decision is answered.

## 7. Data integrity
- Never modify raw data; transformations are scripted and reproducible.
- Tag every value as observed / transformed / imputed / derived / predicted / synthetic. Never fabricate readings, labels, or missing observations; no fake datasets, models, or metrics.
- No silent row/column loss; check types, nulls, duplicates, ranges, join cardinality (no accidental many-to-many).
- Extreme values: classify (invalid, sensor error, legitimate event) before removing; don't drop because unusual.

## 8. Leakage, reproducibility, evaluation
- Split first; fit preprocessing on train only; apply to val/test. Covers scaling, imputation, encoding, feature selection, resampling, dimensionality reduction, target-derived and temporal features.
- Time series: chronological splits unless an approved method says otherwise.
- Explicit seeds, documented; never tune seeds to improve results.
- Metrics fit the problem; not train-only; compare models under identical conditions; no cherry-picking.
- Never report a metric/result/test that did not actually run and produce it.

## 9. EDA and notebooks
EDA separates OBSERVATION / INTERPRETATION / HYPOTHESIS. Cover distributions, missingness, outliers, correlations, imbalance, temporal and geographic patterns, leakage, impossible values. Notebooks: linear order, no hidden state, no giant cells, rerunnable clean; reusable logic goes into `src/`.

## 10. Visualization
Honest axes, titled/labelled with units and legends, no hidden categories, no misleading aggregation, predictions visually distinct from observations, correlation never framed as causation.

## 11. Security
Never commit or log passwords, keys, tokens, DB credentials, connection strings, `.env`. Use env vars (`.env.example` lists names only). No secrets in notebooks, docs or screenshots. Don't create real credentials without approval.

## 12. Scope, dependencies, performance
- Don't touch other projects, global Java/Python/Hadoop/Mongo config. Don't overwrite files unread. No installs of major software without approval.
- Python deps in project `.venv`, pinned in `requirements.txt` once the first one is added. Ask before adding heavy dependencies.
- Measure before optimizing; log processing times.

## 13. Verification
Match checks to the change. Pipelines: columns, types, row counts, nulls, uniqueness, ranges. ML: pipeline runs, train/test separation, real metrics, artifacts. Say "tests pass" only if run and passed.

## 14. Skill routing (opt-in, one primary skill per phase, none by default)
trivial/localized → none or surgical-patch | unknown bug → investigate-first | new bounded feature → lean-build | structural refactor → safe-refactor | schema/data migration → migration | dashboard/UI → impeccable (+dataviz for charts) | security-sensitive → security-review | CI/CD → ci-cd-builder | final check → verify-and-stop | else the minimum specialized skill. Never chain investigate→plan→implement→audit→review by default. Subagents only for clearly separable work.

## 15. Context efficiency
Read only needed files; don't re-read valid context; don't preload the project.

## 16. Commands
- HDFS/Hadoop (WSL): `wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hdfs dfs -ls /earthscape`
- Hadoop version: `wsl -d Ubuntu-24.04 -e /opt/hadoop/bin/hadoop version`
- Jupyter: `jupyter lab`
- Tests: none yet.
