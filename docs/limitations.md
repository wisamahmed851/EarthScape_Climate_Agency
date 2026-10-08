# Known limitations

**Data**
- NASA POWER is gridded reanalysis plus satellite-derived radiation (one grid cell per city), not station observations. 25 years (2001-2025) only; the product has no gaps, so missing-data handling is covered by unit tests rather than real cases.
- POWER's hourly solar unit is reported as `MJ/hr`; the area basis is not stated by the provider and the value is kept as reported (daily mean only, no energy total).
- Open-Meteo weather and air quality are model output. OpenAQ is observed but sparse: PM2.5 only (no NO2/SO2/CO/O3 exist for Pakistan on OpenAQ), mostly low-cost sensors, differing sensors over time, history only since the polling started.
- **MODIS has not been ingested**: no NASA Earthdata credentials were available. The pipeline (CMR search, authenticated download, HDF4 magic/size validation, HDFS upload, pyhdf reader, sinusoidal tile math) is built and unit-tested but never ran against a real file; the measured plan is 1,515 granules / 11.2 GB.
- The first real sealed RAW objects of the live sources appear only after the first UTC day ends (polling began on 2026-10-08, so the earliest seal is after 05:00 PKT on 2026-10-09). The rollover is tested with an in-memory HDFS and was earlier verified on real HDFS in a scratch namespace; no real daily object exists yet.
- The current-vs-historical comparison covers temperature and humidity only. The reference is a 2001-2025 reanalysis mean (it does not reflect recent warming), the two sources differ in model, grid and elevation, and a 15-minute reading is matched to the UTC hour.

**Processing and ML**
- MapReduce runs on one node with one reducer; the data (67 MB INTERIM) is below one HDFS block, so scale is not demonstrated.
- Forecasts are linear/seasonal models on 25 years of reanalysis; skill beyond the seasonal baseline is modest (see ml-methodology.md); the band is approximate. Anomaly detection is unsupervised with no ground-truth labels. Correlation is association only.
- Real-time processing is near-real-time REST polling (15 min / hourly), not event streaming; alerts are evaluated after each poll, not continuously.

**Application and operations**
- Alerts are in-app only; email and SMS are not configured. Alert rules on historical POWER variables are stored but never evaluated.
- TLS is prepared (Apache config reviewed, Apache not installed) but **not active**; disk encryption is not enabled and its current state could not be read without Administrator rights. The app is bound to 127.0.0.1.
- A scheduled health check (15 min) and a daily backup run through Windows Task Scheduler while the user is logged on; they only record and back up, they do not notify or restart anything. 99% uptime is **not** demonstrated. HDFS/YARN must be started by hand after every reboot or WSL stop. Load balancing is not implemented.
- Chart.js and Bootstrap load from the jsDelivr CDN, so the dashboards need internet access.
- Impala, Tableau, Apache HTTP Server and RStudio are listed by the SRS under Hardware/Software Requirements (no function stated); they are not installed or integrated. The SRS also mentions weather *station* records; none were approved or ingested.
- No demo video has been recorded.
