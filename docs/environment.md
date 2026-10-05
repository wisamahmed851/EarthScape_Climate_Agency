# Environment inventory (inspected 2026-10-05)

Windows 11 Pro 10.0.22000. Hadoop lives in WSL, not on Windows.

| Technology | Version | Status | Notes |
|---|---|---|---|
| WSL2 | Ubuntu-24.04 (default, D:\WSL\Ubuntu-24.04), also "Ubuntu" (empty) | Usable | |
| Java (Windows) | JRE 1.8.0_401, no javac, JAVA_HOME unset | Unusable for Hadoop 3.4 | Do not touch |
| Java (WSL) | OpenJDK 17.0.20 | Usable | Set as JAVA_HOME in hadoop-env.sh |
| Hadoop / HDFS (WSL) | 3.4.3 at /opt/hadoop | Installed, configured, daemons not running | Single node, `hdfs://localhost:9000`, replication 1, `dfs.permissions.enabled=false`, data in `~/hadoop_data`. Streaming jar present (MapReduce via Hadoop Streaming possible). |
| YARN / MapReduce | 3.4.3 | Not verified | yarn-site/mapred-site and running state not checked |
| Python (Windows) | 3.13.7, pip 26.0.1 | Usable | Global packages: pandas, numpy, pymongo, pyarrow, scikit-learn |
| Python (WSL) | 3.12.3 | Usable | venv for another project in ~/venvs |
| Jupyter (Windows) | JupyterLab 4.6.3, notebook 7.6.2 | Usable | |
| Conda/Anaconda | - | Not installed | Spec says "Anaconda Notebook 3"; Jupyter present |
| R / RStudio | - | Not installed | Purpose unclear |
| MongoDB Server | 8.2.6 | Installed, service running | Shared instance; use separate DB name |
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
