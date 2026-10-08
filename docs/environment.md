# Environment inventory (inspected 2026-10-05)

Windows 11 Pro 10.0.22000. Hadoop lives in WSL, not on Windows.

| Technology | Version | Status | Notes |
|---|---|---|---|
| WSL2 | Ubuntu-24.04 (default, D:\WSL\Ubuntu-24.04), also "Ubuntu" (empty) | Usable | |
| Java (Windows) | JRE 1.8.0_401, no javac, JAVA_HOME unset | Unusable for Hadoop 3.4 | Do not touch |
| Java (WSL) | OpenJDK 17.0.20 | Usable | Set as JAVA_HOME in hadoop-env.sh |
| Hadoop / HDFS (WSL) | 3.4.3 at /opt/hadoop | HDFS verified 2026-10-06: NameNode, DataNode, SecondaryNameNode running; `hdfs dfs -ls` responds | Single node, `hdfs://localhost:9000`, replication 1, `dfs.permissions.enabled=false`, data in `~/hadoop_data`. Streaming jar present (MapReduce via Hadoop Streaming possible). |
| YARN | 3.4.3 | Works with EarthScape config | ResourceManager + NodeManager running (`jps`); `yarn node -list` shows 1 RUNNING node. Config: `config/hadoop/` |
| MapReduce | 3.4.3 | Works through YARN | wordcount `job_1791283447001_0001` SUCCEEDED (2026-10-06), input/output under `/earthscape/_verify_yarn_tmp` (deleted), output alpha 3 / beta 2 / gamma 1. Not job_local |
| EarthScape HDFS root | - | Exists | `/earthscape/{raw,interim,processed}`, empty |
| EarthScape .venv | Python 3.13.7 | Works | Only ipykernel + its dependencies, pinned in requirements.txt |
| Jupyter kernel | - | Executed OK | `EarthScape` (name `earthscape`); a test execution printed `...\.venv\Scripts\python.exe` |
| Python (Windows) | 3.13.7, pip 26.0.1 | Usable | Global packages: pandas, numpy, pymongo, pyarrow, scikit-learn |
| Python (WSL) | 3.12.3 | Usable | venv for another project in ~/venvs |
| Jupyter (Windows) | JupyterLab 4.6.3, notebook 7.6.2 | Usable | |
| Conda/Anaconda | - | Not installed | Spec says "Anaconda Notebook 3"; Jupyter present |
| R / RStudio | - | Not installed | Purpose unclear |
| MongoDB Server | 8.2.6 | Installed, service running, ping ok (server 8.2.6) | Shared instance; use separate DB name |
| MongoDB Compass | 1.49.15 | Installed | |
| mongosh | - | Not installed | |
| MySQL | 8.4.3 (Laragon) | Present | Not in spec |
| Spark | - | Not installed | Not required |
| Impala | - | Not installed | Purpose unclear |
| Apache HTTP Server | - | Not installed | Purpose unclear |
| Tableau | - | Not detected | Purpose unclear |
| Git | 2.45.1 | Usable | |
| Node.js / npm | 24.19.0 / 11.6.2 | Usable | |
| VS Code | 1.140.0 | Usable | |
| Docker | - | Not installed | |

## Reuse without coupling
- Hadoop binaries and the single-node HDFS can be shared; keep EarthScape isolated under HDFS path `/earthscape` and a dedicated project venv.
- The WSL NameNode/DataNode dirs (~/hadoop_data) already belong to an earlier project (venv `urbantransit` exists). Do not reformat the NameNode. Starting daemons is fine; changing config affects both projects.
- MongoDB service is shared; EarthScape must use its own database name and its own user.
- Do not use global pip installs; create `.venv` for EarthScape.

## EarthScape Hadoop config (project-local, shared config untouched)
- `config/hadoop/`: copies of core-site, hdfs-site, hadoop-env (shared `HADOOP_PID_DIR` line removed), log4j, workers, capacity-scheduler, plus EarthScape `mapred-site.xml` (framework=yarn) and `yarn-site.xml` (4 GB / 4 vcores, 1 node). Same HDFS: `hdfs://localhost:9000`.
- `config/earthscape-env.sh` sets `HADOOP_CONF_DIR` and EarthScape-only PID/log dirs (`~/hadoop_data/earthscape-{pids,logs}`; YARN dirs in `~/hadoop_data/earthscape-yarn`).
- Safe mode after HDFS start cleared by itself in about 15 s; never forced.

## Starting EarthScape services (WSL Ubuntu-24.04)
1. HDFS (shared): `/opt/hadoop/sbin/start-dfs.sh`, wait until `hdfs dfsadmin -safemode get` says OFF.
2. `source config/earthscape-env.sh`, then `setsid nohup yarn --daemon start resourcemanager` and `setsid nohup yarn --daemon start nodemanager` (redirect output to /dev/null).
3. Check `jps` and `yarn node -list`.
- WSL stops its VM when no WSL process is open and the daemons die with it; keep a WSL session open (e.g. `wsl -d Ubuntu-24.04 -e sleep infinity`).
- Daemons started from a session that ends get SIGHUP (the first ResourceManager died this way); hence `setsid nohup`.
- Paths contain spaces; the env script quotes them. Jobs leave staging data under HDFS `/tmp/hadoop-yarn`.
