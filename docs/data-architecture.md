# EarthScape data architecture (layout confirmed by the user 2026-10-06; NASA POWER RAW, INTERIM, daily and monthly MapReduce implemented)

NASA POWER RAW ingestion and POWER RAW to INTERIM are implemented and verified (see Implementation status). Everything else (other sources, their INTERIM schemas, PROCESSED, MapReduce) is design only. Provider facts are in [datasets.md](datasets.md); open items are in [open-decisions.md](open-decisions.md).

## Purpose
Define how the five approved sources land in HDFS, stay traceable, and flow from RAW to INTERIM to PROCESSED, without small-file explosions or silent duplication. Single-machine academic scale.

## Data sources
| Source dir name (encodes the class) | Provider / product | Class | Raw format | Time / space |
|---|---|---|---|---|
| `power_hourly_gridded` | NASA POWER hourly | Reanalysis + satellite radiation, gridded | CSV (header block keeps units, fill value, elevation) | 2001..2025, city point |
| `openmeteo_weather_model` | Open-Meteo `current` | Model-based, near-real-time | JSON | polled, city point |
| `openaq_observed` | OpenAQ v3 | Observed (mostly low-cost) | JSON | location/sensor, hourly |
| `openmeteo_airquality_model` | Open-Meteo Air Quality | Model-based | JSON | polled, city point |
| `modis_mod11a2_satellite` | MODIS MOD11A2 v061 | Satellite-derived LST | HDF4-EOS | 8-day, 1 km tiles |

Choice of POWER CSV over JSON is a recommendation: it is smaller, line-oriented and keeps the header; JSON would also work.

## Data classification
Every value carries one origin tag downstream: observed, reanalysis, modelled, satellite, derived, imputed, predicted. The source directory name fixes the class for RAW; INTERIM records the class in its manifest, per variable where lineage differs (POWER: see the RAW to INTERIM contract below). Modelled data is never merged into observed series.

## HDFS layers
- RAW: provider-native, immutable.
- INTERIM: parsed, validated, standardised (timestamps, ids, units, missing flags). Not final output.
- PROCESSED: analysis-ready aggregates and derived results.
- `/earthscape/_manifest`: ingestion metadata (not data).

## Proposed directory structure
Created only when each source is implemented. Today `raw/power_hourly_gridded`, `interim/power_hourly`, `_manifest/power_hourly_gridded.jsonl` and `_manifest/power_hourly_interim.jsonl` exist; `processed/power_daily`, `processed/power_monthly`, `_manifest/power_daily.jsonl` and `_manifest/power_monthly.jsonl` were added 2026-10-08 and the other sources are not created.
```
/earthscape
|-- _manifest/<source>.jsonl            append-only ingestion log
|-- raw/
|   |-- power_hourly_gridded/city=karachi/year=2001/power_hourly_karachi_2001.csv   (5 cities x 25 years = 125 files)
|   |-- openmeteo_weather_model/date=2026-10-06/polls.jsonl                          (1 sealed file per UTC day)
|   |-- openmeteo_airquality_model/date=2026-10-06/polls.jsonl
|   |-- openaq_observed/
|   |   |-- locations/snapshot=2026-10-06/pakistan_locations.jsonl                   (location catalogue, dated)
|   |   |-- hours/city=lahore/year=2026/month=09/pm25_hours.jsonl                    (sealed after month end)
|   |   `-- current/date=2026-10-06/latest.jsonl                                     (daily sealed polls)
|   `-- modis_mod11a2_satellite/tile=h24v05/year=2015/MOD11A2.A2015001.h24v05.061.<ts>.hdf   (native names)
|-- interim/power_hourly/city=<city>/power_hourly_<city>.csv   (POWER, confirmed and built: 5 files)
|-- interim/<other dataset>/...         layout decided with each source's interim schema
`-- processed/<product>/...             layout decided per product
```
`city=` in OpenAQ raw is EarthScape's request grouping (sensors within 25 km of the city coordinate), not a provider field; the location-to-city mapping and distances are stored in INTERIM.

## Partitioning strategy
| Source | Partitions | Why |
|---|---|---|
| POWER | city / year | Time series per point; yearly requests are the natural retry unit; 125 files of ~0.5 MB. Only two levels. |
| Open-Meteo weather and AQ | date | Polls accumulate over time; one sealed file per UTC day gives a natural close point. No city level: each line carries its city. |
| OpenAQ hours | city / year / month | Fetch is per sensor, but one file per sensor would be thousands of tiny files; grouping sensors by city-month gives about 80 files. |
| OpenAQ locations | snapshot date | Active/stale status changes, so the catalogue is dated. |
| MODIS | tile / year | Provider unit is tile + acquisition date; ~46 files per tile-year; native file names kept. |
No source is partitioned by hour, pollutant or variable.

## File/batch strategy (small files)
- POWER: one provider response per city-year (not per day/hour).
- Open-Meteo and OpenAQ current: polls are buffered locally, appended as JSON lines, and uploaded once per UTC day as one immutable file. The open day stays in local staging.
- OpenAQ history: one file per city-month, built from the API responses for that group, uploaded after month end.
- MODIS: files are already ~5.5 MB; kept as is (about 1,500 files).
- RAW total is roughly 2,100 files, which is fine for the NameNode. MapReduce over many small RAW files is inefficient, so INTERIM compacts them into a few large files (target near the 128 MB block size or one file per dataset).
- Alternatives rejected: per-response files (about 175k a year for polling), HAR/sequence packing of RAW (changes the native files).

## Raw immutability
- RAW files are written once, never edited, never overwritten, never deleted by pipelines.
- No imputation, unit conversion, timestamp shifting or outlier removal in RAW; -999 stays -999.
- Payload is stored verbatim. For polled sources each JSON line is an envelope `{retrieved_at, request, response}` where `response` is the unmodified provider body (the envelope is EarthScape's, the payload is the provider's).
- Provider revisions (POWER reprocesses data) are stored as a new file with a `.r<retrieval date>` suffix and noted in the manifest; the older file stays.
- Corrections happen only in INTERIM or PROCESSED.

## Local staging
Not `data/raw` (that stays the read-only source mirror); `data/staging/<source>/` (implemented for POWER RAW and INTERIM; `data/staging/*` is git-ignored).
```
provider -> data/staging/<source>/<name>.part -> validate -> sha256 -> hdfs put to /earthscape/_tmp/<name>
         -> verify size (+ sha256 read-back) -> hdfs mv (atomic rename) to raw path -> manifest ok -> delete local file
```
Failed or quarantined files stay in `data/staging/_failed/` for inspection; successful ones are deleted only after the manifest records `ok`.

## Validation (before accepting RAW)
HTTP 2xx; non-empty body; not an error payload (e.g. POWER `messages`/HTTP 422 bodies, OpenAQ error JSON); JSON parses / CSV has the expected header; provider metadata matches the request (coordinates, parameter list, `fill_value`); expected time range present; HDF4 magic bytes and size equal to the advertised size; sha256 computed. No statistical checks, cleaning or range filtering at this stage. Observed data that "looks wrong" is kept.

## Manifest / provenance
Recommendation: one append-only JSON-lines file per source at `/earthscape/_manifest/<source>.jsonl` (HDFS append on a single node, last line per `object_key` wins). No database, so MongoDB's undecided role is not touched.

| Field | Meaning |
|---|---|
| object_key | deterministic id, e.g. `power_hourly_gridded/karachi/2001` |
| source, dataset, provider_version | e.g. `NASA POWER hourly`, API version seen in the response |
| request | URL and parameters, with no key or auth header |
| scope | city, OpenAQ location ids, or MODIS tile |
| time_start, time_end | covered range (UTC) |
| retrieved_at | UTC timestamp |
| format, hdfs_path, size_bytes, sha256 | the stored object |
| record_count | where meaningful (rows, JSON lines, sensors) |
| status | started, ok, failed, quarantined |
| attempt, error | retry count and short error (never headers) |

## Idempotency
- Path and `object_key` are deterministic from source + scope + time range, so a repeat run targets the same name.
- Already downloaded: manifest has an `ok` line and the HDFS file exists with the recorded size. Skip.
- New: no manifest entry. Fetch.
- Failed/incomplete: last line is `started` or `failed`, or the file is missing. Redo; leftovers in `/earthscape/_tmp` and local `.part` files from this tool are discarded.
- Never overwrite: if the target exists, the run stops unless an explicit refresh is requested, which writes the `.r<date>` variant.
- Open (current) days and months are not uploaded until sealed, so partial files never reach RAW.

## Failure / retry handling
- Bounded retries (5, implemented for POWER) with exponential backoff; honour HTTP 429/`Retry-After` and per-provider limits (OpenAQ 60/min, 2,000/hour; Open-Meteo 600/min).
- Timeout per request; a timeout, 5xx or 429 retries; other 4xx (e.g. POWER 422) fails immediately with the provider message in the manifest.
- Interrupted or corrupt download: stays `.part`, size/signature check fails, retry (HTTP Range resume for MODIS if the server supports it, otherwise restart that single file).
- Machine or WSL restart: manifest `started` without `ok` marks work to redo; HDFS must be started by hand first (see environment.md).
- Items that exhaust retries are marked `failed` and the run continues; a final summary lists them.

## Interim layer
Responsibility: parse provider-native RAW and make it comparable without final analysis.
- Timestamps in UTC (POWER requested in UTC).
- Standard `city_id`; OpenAQ location-to-city mapping and distances.
- Missing values as null with a flag (POWER -999, MODIS fill values, absent sensor hours).
- Unit and height harmonisation recorded explicitly (kPa vs hPa, 2 m m/s vs 10 m km/h, temperature c/f); no value is converted without the original being kept.
- Quality flags, including OpenAQ sensor type (low-cost vs reference) and active/stale status.
- MODIS: extract LST (day/night) and QC for pixels around each city into tabular rows; parsing needs the HDF4 reader (open decision).
- Compaction of small RAW files into large files.
POWER interim schema and format (CSV) are confirmed below. Not decided yet: schemas for the other sources, and the MODIS pixel window size.

## Processed layer
Derived and analysis-ready outputs, all tagged derived: hourly to daily climate aggregates (mean T2M, derived min/max from hourly T2M, precipitation total, mean RH/wind/pressure), monthly aggregates, city/year summaries, extreme and missing-value statistics, anomaly outputs, satellite city summaries, correlation-ready joins (weather x LST, observed PM2.5 x weather). ML datasets are out of scope for now.

## MapReduce boundary
```
RAW -> parse/validate (plain script) -> INTERIM -> MapReduce -> PROCESSED
```
Natural MapReduce jobs (not implemented):
1. Hourly to daily aggregation per city (keyed city + date), including derived min/max and precipitation totals.
2. Daily to monthly aggregation.
3. City/year summaries and extreme-value counts.
4. Missing-value statistics per city, variable, year.
5. OpenAQ PM2.5 hourly to daily statistics per location/city.
6. Cross-source correlation prep (join keyed by city + date).
Not MapReduce: downloading, file validation, HDF4 parsing, API polling, dashboards, and small lookups. Honest note: POWER hourly (actual: RAW 48 MB, INTERIM 67 MB, about 13 MB per city file) is below one HDFS block, so MapReduce here is a requirement and a demonstration, not a necessity; do not claim big-data scale.

## Current data boundary
Open-Meteo weather/AQ and OpenAQ `latest` polls land in local staging as JSON lines, then become one sealed daily RAW file. They never write into the historical directories. The latest-values access path for dashboards is undecided (depends on the open MongoDB/dashboard decisions). Sealed days are compacted to INTERIM and join PROCESSED through the same city/date keys, always carrying their source and `value_origin`, so current model output is never blended silently with POWER history. The polling is a REST schedule; the streaming technology is a separate open decision.

## Satellite handling
MODIS `.hdf` files go to RAW byte-for-byte with their native names (`MOD11A2.A<yyyyddd>.<tile>.061.<ts>.hdf`), downloaded with an Earthdata Login account (credentials via environment variables, none created yet) after CMR search (keyless). The manifest records tile, acquisition date, size and sha256. No conversion in RAW. Later INTERIM reads LST and QC layers for city pixel windows into rows; reader library chosen at implementation.

## Data volume (estimates)
| Source | Estimate | Basis |
|---|---|---|
| POWER hourly | 1,095,720 rows, ~66 MB, 125 files | 5 cities x 25 years x 8,766 h, ~60 B/row (previous estimate). Actual: 1,095,720 rows, RAW 48,048,922 B, INTERIM 67,291,971 B |
| MODIS | ~1,518 files, ~8.3 GB | 11 years x 46 x 3 tiles x ~5.5 MB (CMR sizes 5.4-6.0 MB) |
| OpenAQ hours | roughly 0.3-0.5 GB upper bound | up to 226 sensors within 25 km, ~16 months hourly, ~150 B/row JSON; fewer if limited to PM2.5 or active sites |
| Open-Meteo weather | ~30-120 MB per year | 5 cities, hourly to 15-minute polling, ~700 B per record |
| Open-Meteo AQ | ~20-90 MB per year | same polling, ~500 B per record |
| Total RAW | about 9-10 GB first year | INTERIM and PROCESSED add perhaps 1-3 GB |
All figures are estimates, not measurements. HDFS replication is 1.

## Capacity (read-only check, 2026-10-06)
- HDFS: configured 1,006.85 GB, remaining 944.32 GB, used 2.46 GB (UrbanTransit ~2.0 GB), block size 128 MB.
- Windows D: drive (hosts the WSL disk) has 151 GB free of 293 GB, which is the real limit. Planned ~10-15 GB fits many times over.
- Local staging needs only the largest in-flight file (a few MB) plus the open day's poll buffer.
Verdict: the machine is sufficient.

## Security
Keys come from environment variables or the git-ignored `.env` (`OPENAQ_API_KEY`; Earthdata credentials later). Manifests, logs and exceptions never store request headers or keys; stored request URLs omit credentials. Checked 2026-10-06 without printing: `.env` is git-ignored, the key appears in no tracked file, no project file other than `.env`, and no Git history.

## Decisions still open
HDF4 reader library; MapReduce style (Java or Hadoop Streaming; recommendation proposed, awaiting approval); interim schemas of the sources other than POWER (POWER interim format and schema are confirmed); polling interval; Open-Meteo AQ backfill; streaming technology; MongoDB/Impala/Tableau/Apache/R roles; ML; notifications; support; roles; dashboards; deployment.

## Implementation status
### NASA POWER ingestion (pilot done 2026-10-06)
Code: `src/ingestion/nasa_power.py` (standard library + HDFS CLI through `wsl`, no new dependencies). City coordinates come from `config/cities.json` (the project-standard set in datasets.md). HDFS and WSL must be running (see environment.md).

Run (one city, one calendar year per file; start and end must be in the same year, end date inclusive):
```
.venv\Scripts\python.exe src\ingestion\nasa_power.py --city karachi --start 2001-01-01 --end 2001-12-31
```
Optional: `--variables` (default is the approved six), `--staging-dir`. Tests: `.venv\Scripts\python.exe -m unittest discover -s tests`.

Behaviour as built:
- Request: hourly point API, `community=AG`, CSV, `time-standard=UTC`, approved variables, coordinates from config. Provider version is read from the `x-app-version` response header.
- File name: `power_hourly_<city>_<year>.csv` for a full year, otherwise `power_hourly_<city>_<YYYYMMDD>_<YYYYMMDD>.csv`, under `raw/power_hourly_gridded/city=<city>/year=<year>/`.
- Staging `data/staging/power_hourly_gridded/<name>.part`; deleted after success; any failure after download moves it to `data/staging/_failed/`.
- Validation: HTTP 200 and non-empty; POWER CSV header (not an error payload); header coordinates, UTC period and every parameter match the request; exact column list; exactly 24 rows per requested day; each row's timestamp is the expected next hour (no gaps or duplicates); numeric fields. `-999` is accepted and counted (`fill_value_cells`), never altered.
- Upload: `hdfs put` to `/earthscape/_tmp/`, size and sha256 read-back check, then `hdfs mv` into raw. An existing raw file is never overwritten; an identical one left by an interrupted run is adopted, a different one fails.
- Manifest: `/earthscape/_manifest/power_hourly_gridded.jsonl`; a `started` line, then `ok` or `failed` (last line per `object_key` wins), with the fields listed above plus `fill_value_cells`.
- Idempotency: `ok` record + file present + size equal means skip, with no download.
- Retries: 5 attempts, backoff `2**n` s or `Retry-After`, only for timeouts, connection errors, 429 and 5xx; other HTTP errors fail at once. Failures exit non-zero and are written to the manifest.

Pilot result (Karachi 2001-01-01..2001-12-31): 8,760 rows, 388,633 bytes, sha256 `763e5209...6504`, 0 `-999` cells, 1 attempt; HDFS path `/earthscape/raw/power_hourly_gridded/city=karachi/year=2001/power_hourly_karachi_2001.csv`. HDFS read-back sha256 equals the staged sha256 and equals a fresh independent download. Second run: skipped, no new file, manifest not appended.

### NASA POWER full historical batch (completed 2026-10-07)
Driver: `src/ingestion/nasa_power_batch.py` (`.venv\Scripts\python.exe src\ingestion\nasa_power_batch.py`; sequential, resumable, ends with a full HDFS verification and writes `artifacts/ingestion/power_hourly_gridded_<UTC>.json`).

Final result: 5 cities x 2001..2025 = 125 city-year objects, 25 per city, 0 missing, 0 duplicates, 0 failed.
- Rows: 1,095,720 actual = 1,095,720 expected (219,144 per city; 2004, 2008, 2012, 2016, 2020, 2024 are leap years with 8,784 rows). Bytes: 48,048,922 (Karachi 9,736,423; Lahore 9,596,833; Islamabad 9,596,620; Peshawar 9,596,718; Quetta 9,522,328).
- `-999` fill cells: 0 in all 125 objects. RAW unchanged.
- Manifest: 250 lines (125 `started`, 125 `ok`); last record per object is `ok`; no orphaned `started`, no `failed`. Sizes, paths and sha256 equal the HDFS objects; `/earthscape/_tmp` empty; local staging and `_failed` empty.
- Run history: the first run was interrupted at 52 objects (Karachi, Lahore, Islamabad 2001-2002); the resume ingested the other 73 in 5,731 s with 0 failures (only Quetta 2012 needed retries, 4 attempts). Verification: 0 problems.
- Idempotency: a further full run ingested 0, skipped 125, failed 0, manifest unchanged, no provider requests for skipped units (it still re-reads all HDFS objects for verification, 2,011 s).

## POWER RAW to INTERIM contract (FULLY CONFIRMED by the user 2026-10-07; implemented and verified for all five cities, see Implementation status)
Transformation version `power_interim_v1`. RAW stays immutable; every change happens while writing INTERIM. No open items remain for this contract.

### RAW structure observed (Karachi 2004 object; same form in all objects)
- Header block `-BEGIN HEADER-` .. `-END HEADER-`: title, `Dates (month/day/year): MM/DD/YYYY through MM/DD/YYYY in UTC`, `Location: Latitude <lat> Longitude <lon>`, `Elevation from MERRA-2: Average for 0.5 x 0.625 degree lat/lon region = <m> meters`, fill value line (`-999`), one line per parameter with source product and unit.
- Column line `YEAR,MO,DY,HR,T2M,RH2M,PRECTOTCORR,WS2M,PS,ALLSKY_SFC_SW_DWN`, then one row per UTC hour (8,760 or 8,784 rows). Plain decimals, no quoting.
- City, coordinates, elevation and provider version are not in the data rows; they are in the header, the path and the RAW manifest.

### Canonical INTERIM row (one per city-hour; 9 columns, one header row)
| Field | Type | Unit | Source | Nullable |
|---|---|---|---|---|
| timestamp_utc | ISO-8601 text | UTC | YEAR,MO,DY,HR | no |
| city_id | text | - | path / config/cities.json | no |
| temperature_2m_c | decimal | C | T2M | yes |
| relative_humidity_2m_pct | decimal | % | RH2M | yes |
| precipitation_mm_h | decimal | mm/hour | PRECTOTCORR | yes |
| wind_speed_2m_m_s | decimal | m/s | WS2M | yes |
| surface_pressure_kpa | decimal | kPa | PS | yes |
| solar_irradiance_mj_hr | decimal | MJ/hr (provider unit, unchanged) | ALLSKY_SFC_SW_DWN | yes |
| missing_vars | text | - | computed | yes (empty = none) |

No latitude, longitude, elevation, year, month, day, hour, source id or `value_origin` per row. Values keep the provider's decimal text, so nothing is reformatted or numerically converted.

### Timestamp (RESOLVED)
`YEAR,MO,DY,HR` becomes `YYYY-MM-DDTHH:00:00Z` (zero padded, always UTC). The user confirmed from NASA POWER documentation that the hourly timestamp is the START of the hour, so there is no shift; the label means the interval `[T, T+1h)`. No local Pakistan time. RAW columns are untouched.

### Unit contract
No numeric conversion. Mappings: T2M C to `temperature_2m_c` C; RH2M % to `relative_humidity_2m_pct` %; PRECTOTCORR mm/hour to `precipitation_mm_h`; WS2M m/s (2 m height) to `wind_speed_2m_m_s`; PS kPa to `surface_pressure_kpa`; ALLSKY_SFC_SW_DWN MJ/hr to `solar_irradiance_mj_hr`. Note: the POWER parameter catalogue lists PRECTOTCORR hourly units as "mm/day", while every RAW file header says "mm/hour". The unit in the ingested RAW headers is used (`precipitation_mm_h`, mm/hour, no conversion) and the discrepancy is stored in the interim manifest.

### Solar unit finding (checked 2026-10-07 against the POWER parameter catalogue `api/system/manager/parameters` and a one-day API response)
- Provider unit string for the hourly parameter: `MJ/hr` in both the RAW header and the catalogue (`units`).
- Catalogue definition: "total solar irradiance incident (direct plus diffuse) on a horizontal plane at the surface ... Global Horizontal Irradiance (GHI)". Irradiance is a per-area quantity, so the area basis is implied by the definition, but no POWER text states "m^2" or "MJ/m^2 over the hour".
- Plausibility (not authoritative): Karachi January noon values near 2.4 are consistent with about 0.65 kW/m^2 x 3600 s = 2.3 MJ/m^2 per hour.
- Decision (user, 2026-10-07): field `solar_irradiance_mj_hr`, unit metadata `MJ/hr` exactly as POWER reports it. It is not relabelled `MJ/m^2/hour` and not converted. The physical quantity is GHI (solar irradiance on a horizontal plane); the POWER metadata available to the project reports the hourly unit as `MJ/hr`.

### Missing values
`-999` in any of the six fields becomes an empty field (null) and that field's INTERIM name is appended to `missing_vars` (`;`-separated, empty when none). No imputation, interpolation, forward fill or substitute value. Currently 0 such cells; the rule is defined for later provider revisions.

### Variable-level lineage (kept in the INTERIM manifest, not in rows)
Lineage comes from the RAW header parameter lines (primary) and the catalogue `source` tag. Normalising renames and re-formats values but does not derive them, so no value is tagged "derived".
| INTERIM field | RAW header lineage | Catalogue source tag | Class |
|---|---|---|---|
| temperature_2m_c | MERRA-2 Temperature at 2 Meters | SOURCE | reanalysis (model-based, gridded) |
| relative_humidity_2m_pct | MERRA-2 Relative Humidity at 2 Meters | POWER | reanalysis (model-based, gridded); catalogue tags POWER as the supplier |
| precipitation_mm_h | MERRA-2 Precipitation Corrected | SOURCE | reanalysis, bias-corrected (model-based, gridded) |
| wind_speed_2m_m_s | MERRA-2 Wind Speed at 2 Meters | POWER | reanalysis (model-based, gridded); catalogue tags POWER as the supplier |
| surface_pressure_kpa | MERRA-2 Surface Pressure | SOURCE | reanalysis (model-based, gridded) |
| solar_irradiance_mj_hr | CERES SYN1deg All Sky Surface Shortwave Downward Irradiance (API header `sources: SYN1DEG`) | SOURCE | satellite-derived radiation product, gridded |
None are station observations. Dataset class: gridded reanalysis plus satellite radiation (see Data sources table).

### Format
CSV (UTF-8, LF, comma, no quoting needed, one header row that mappers skip; null = empty field). Reasons: no new dependency, readable by Java TextInputFormat and Hadoop Streaming, inspectable with `hdfs dfs -cat`, 67 MB in total (actual). Parquet would need pyarrow and Java Parquet libraries and does not pay off below one HDFS block. The choice leaves Java versus Streaming open.

### HDFS layout and grouping
```
/earthscape/interim/power_hourly/city=<city>/power_hourly_<city>.csv
```
Five files (karachi, lahore, islamabad, peshawar, quetta), each 25 years, 219,144 rows, 13.1 to 13.8 MB (actual), sorted by `timestamp_utc`. City is a directory for pruning and also a column so MapReduce need not parse paths. No year level: 125 files would repeat the small-file problem. Written to `/earthscape/_tmp`, then `hdfs mv`; never overwritten. The whole dataset is below one 128 MB block, so MapReduce here demonstrates the architecture, not scale.

### Validation invariants (the transformation must enforce)
Every RAW input is `ok` in the RAW manifest and its sha256 matches HDFS; exact column list; consecutive hourly timestamps over 2001..2025 with no gaps or duplicates; unique `(city_id, timestamp_utc)`; all fields numeric; header coordinates equal config/cities.json; input rows = output rows (per city-year equal to the RAW `record_count`, 219,144 per city, 1,095,720 total); 9 fields per row; empty-field count = `-999` input count; no value changed except `-999`.

### INTERIM manifest (`/earthscape/_manifest/power_hourly_interim.jsonl`, created at implementation)
Append-only, last line per `object_key` wins, same status rules as RAW. One record per output object:
- Dataset: provider (NASA POWER), dataset, temporal resolution (hourly), dataset class, variable-level lineage (table above), provider unit strings.
- Location: city, latitude, longitude, grid cell and elevation from the RAW header.
- Inputs: list of RAW `{hdfs_path, sha256, record_count}` (25 per city).
- Run: `transform_version` (`power_interim_v1`), `transformed_at`, `status`, `error`.
- Output: `hdfs_path`, `size_bytes`, `sha256`, `record_count`, `fill_value_cells`.
This is not repeated in rows. The data file contains no timestamp, so rerunning on the same RAW gives the same sha256. Skip rule as RAW: `ok` + file present + size equal + input sha256 values unchanged.

### MapReduce input boundary
MapReduce receives the five city CSVs as hourly standardised rows keyed by `city_id` and the date part of `timestamp_utc`, and later produces PROCESSED daily and monthly aggregates, extreme-value statistics and missing-data statistics. Anomaly algorithms and the Java versus Streaming style stay undecided.

### NASA POWER RAW to INTERIM, Karachi pilot (done 2026-10-07)
Code: `src/processing/batch/power_interim.py` (standard library; reuses the HDFS and checksum helpers of `nasa_power.py`). Run: `.venv\Scripts\python.exe src\processing\batch\power_interim.py --city karachi`. Tests: `tests/test_power_interim.py`. Only Karachi is transformed; Lahore, Islamabad, Peshawar and Quetta are not.
- Input check: 25 RAW objects (2001..2025) `ok` in the RAW manifest, size and sha256 equal, header, columns, units and rows valid; 219,144 input rows.
- Output: `/earthscape/interim/power_hourly/city=karachi/power_hourly_karachi.csv`, 219,144 data rows + 1 header, 9 columns, 13,541,214 bytes, sha256 `8de350a844db03c64b356b411a6334690d441843355a896fef93a42ef82a00ba`. First `2001-01-01T00:00:00Z`, last `2025-12-31T23:00:00Z`, no gaps or duplicates.
- Missing: 0 `-999` in RAW, 0 empty climate cells, `missing_vars` empty in every row (the `-999` rule is covered by unit tests only).
- Validation result: rows in = rows out, every non-missing value text equal to its RAW value, HDFS read-back sha256, size, rows, first and last timestamps equal to the staged file.
- Manifest `/earthscape/_manifest/power_hourly_interim.jsonl`: `started` then `ok` for `power_hourly_interim/karachi` with the 25 RAW inputs (path, sha256, record count), `power_interim_v1`, variable lineage and provider units, the PRECTOTCORR catalogue note, grid and elevation (53.05 m).
- Idempotency: a second run returned `skipped / already transformed` with no new file and no new manifest line (2 lines in total). The run took 146 s the first time; RAW stayed at 125 objects, 48,048,922 bytes and 250 manifest lines.

### NASA POWER RAW to INTERIM, all five cities (COMPLETE 2026-10-07)
The Karachi pilot was followed by Lahore, Islamabad, Peshawar and Quetta with the same unchanged `power_interim.py` (no code change). Checked independently of the transformer by re-reading every INTERIM file and all 125 RAW objects from HDFS.
| City | Rows | Bytes | SHA-256 | `-999` / empty cells |
|---|---|---|---|---|
| karachi | 219,144 | 13,541,214 | `8de350a844db03c64b356b411a6334690d441843355a896fef93a42ef82a00ba` | 0 / 0 |
| lahore | 219,144 | 13,182,480 | `c9998c14a6c8ea7fc96b397cf8729180629cbd011a4734ec828474adfb7e4e25` | 0 / 0 |
| islamabad | 219,144 | 13,839,674 | `8515abb941e05f1929f4f954a402ad70c3fd17362d7d94e81c7293e2f3bee869` | 0 / 0 |
| peshawar | 219,144 | 13,620,653 | `bcfbd152e93f3ad34ae462fd6256f1b25d1f1b20d2259299ac6671fdd02be4e7` | 0 / 0 |
| quetta | 219,144 | 13,107,950 | `8bca171d6f31f4b4ba5434568d780d648d572d02af070d2caa619bc3e79c6b5b` | 0 / 0 |
| total | 1,095,720 | 67,291,971 | | 0 / 0 |
- Exactly 5 files under `/earthscape/interim/power_hourly/`, each header + 219,144 data rows, 9 columns. All first timestamps `2001-01-01T00:00:00Z`, last `2025-12-31T23:00:00Z`; 0 gaps, 0 duplicate `(city_id, timestamp_utc)`, correct `city_id`; every non-missing value text equals its RAW value (0 mismatches).
- Manifest `power_hourly_interim.jsonl`: 10 lines (5 `started`, 5 `ok`), no failed or orphaned record, one `ok` per city; sha256, size, path and row count equal the HDFS files; each record lists 25 RAW inputs whose path, sha256 and record count equal the RAW manifest.
- Idempotency: a second run over all five cities transformed 0, skipped 5, failed 0; manifest stayed at 10 lines and no INTERIM file changed.
- RAW unchanged (125 objects, 1,095,720 rows, 48,048,922 bytes, 250 manifest lines); `/earthscape/_tmp` and local staging empty.

## MapReduce: POWER hourly to daily to monthly (APPROVED 2026-10-08; IMPLEMENTED and verified, see "MapReduce results" below)
Approved by the user 2026-10-08: every proposal below (items 14 and 16-19 of the former open list). Where the text says proposed, read approved.

### Style: Hadoop Streaming with Python (recommended) vs native Java
Environment facts checked 2026-10-07: Hadoop 3.4.3 with `hadoop-streaming-3.4.3.jar` present; YARN verified by the 2026-10-06 wordcount job; WSL has OpenJDK 17 (`javac` 17.0.20), `python3` 3.12.3 and no Maven on the PATH; the Windows Java is a 1.8 JRE without `javac` and unusable for Hadoop. Both styles are genuine MapReduce submitted to YARN.
| Aspect | Java MapReduce | Hadoop Streaming (Python) |
|---|---|---|
| Real MapReduce on YARN, HDFS in/out | yes | yes (same framework; mapper and reducer run as child processes reading stdin) |
| Build / dependencies | `javac` against the Hadoop classpath and a jar to package, no Maven; unit testing would need JUnit (not installed) | none: two stdlib Python scripts shipped with `-files`, run by WSL `python3` (not the Windows `.venv`) |
| Code size | three classes plus Writable types and boilerplate | two short scripts |
| Local test without a cluster | needs a harness | `cat file \| mapper \| sort \| reducer` imitates map, shuffle, reduce; plain `unittest` on the functions |
| Fit with the Python pipeline | separate language | same language and conventions as ingestion and interim code |
| Demo clarity | typed code, harder to read in a report | the key emitted by the mapper and the grouped values are visible as plain text |
| Strengths of the other side | typed values, combiners, custom partitioners, speed | not needed at 1.1 M rows |
Recommendation: Hadoop Streaming with Python. It satisfies the Hadoop MapReduce requirement (job id, YARN application, map/shuffle/reduce counters), needs no installs and no build step, and keeps the code simple and testable. Java is not rejected on technical grounds: if the assignment or evaluator specifically expects Java MapReduce, that needs the user's decision.

### Input and job
Input: the five INTERIM CSVs (header + 219,144 rows each, 1,095,720 data rows, 9 fields) via the path pattern `/earthscape/interim/power_hourly/city=*/*.csv`, read only. Each file is below one block, so there are five map tasks.
- Mapper: skip the line starting `timestamp_utc`; split on commas; key = `city_id` + `|` + the first 10 characters of `timestamp_utc` (the UTC date); value = the six climate fields as text (empty stays empty). The key is valid because `timestamp_utc` is `YYYY-MM-DDTHH:00:00Z` in UTC and `(city_id, timestamp_utc)` is unique.
- Shuffle/sort: brings all hourly values of one city-day to one reducer, keys sorted by city then date.
- Reducer: for each key computes the daily row below. Sums use exact decimal arithmetic (inputs are decimal text) because shuffle does not fix the order of values and float sums depend on order. Means are rounded to 4 decimals (proposed); min, max and totals are exact.
- One reducer (proposed): the output is one sorted file; 45,655 small rows do not justify more. No combiner.

### Daily schema (PROCESSED, 17 fields, one header row, comma-separated)
| Field | Type | Unit | Aggregation / source | Nullable | Class |
|---|---|---|---|---|---|
| city_id | text | - | key, copied | no | key |
| date | YYYY-MM-DD | UTC day | key, from `timestamp_utc` | no | key |
| hours_observed | int | hours | count of INTERIM rows in the day (24 expected) | no | quality |
| temperature_2m_mean_c | decimal | C | mean of valid hourly `temperature_2m_c` | yes | aggregated |
| temperature_2m_min_c | decimal | C | min of valid hourly values (lowest hourly value, not an instantaneous extreme) | yes | aggregated |
| temperature_2m_max_c | decimal | C | max of valid hourly values (same caveat) | yes | aggregated |
| relative_humidity_2m_mean_pct | decimal | % | mean of valid hourly values | yes | aggregated |
| precipitation_total_mm | decimal | mm/day | sum of the 24 hourly mm/hour values; empty unless all hours are valid | yes | aggregated |
| wind_speed_2m_mean_m_s | decimal | m/s | mean of valid hourly values | yes | aggregated |
| surface_pressure_mean_kpa | decimal | kPa | mean of valid hourly values | yes | aggregated |
| solar_irradiance_mean_mj_hr | decimal | MJ/hr (provider unit) | mean of valid hourly values | yes | aggregated |
| temperature_2m_missing_hours | int | hours | `hours_observed` minus valid hourly values | no | quality |
| relative_humidity_2m_missing_hours | int | hours | same | no | quality |
| precipitation_missing_hours | int | hours | same | no | quality |
| wind_speed_2m_missing_hours | int | hours | same | no | quality |
| surface_pressure_missing_hours | int | hours | same | no | quality |
| solar_irradiance_missing_hours | int | hours | same | no | quality |

All aggregated values are tagged derived (computed from reanalysis and satellite-radiation hourly values, never observations). Per-variable missing counts are integers so a later monthly job can sum them. No `value_origin` per row; lineage goes in the processed manifest.
Solar caveat (decision pending): POWER reports the hourly unit as `MJ/hr` and the area basis is not stated. The mean keeps the provider unit and needs no assumption. A daily solar total (a sum, comparable to the POWER daily MJ/m^2/day) would depend on reading each hourly value as energy per square metre in that hour; that is not decided, so no sum is proposed.

### Expected daily rows
Days per city 2001-01-01..2025-12-31: 25 x 365 + 6 leap days (2004, 2008, 2012, 2016, 2020, 2024) = 9,125 + 6 = 9,131 (= 219,144 / 24). Five cities: 9,131 x 5 = 45,655 daily rows (+1 header). (Earlier drafts said 45,650, an arithmetic slip corrected 2026-10-08.) Reducer input records 1,095,720; reducer input groups 45,655.

### Missing values
Empty INTERIM climate values are never zero. They are excluded from mean, min and max; each variable's `*_missing_hours` records how many; a mean or extreme is empty when no valid hour exists. Precipitation: the daily total is empty unless all hours of the day are present, because a partial sum would silently understate rain; `precipitation_missing_hours` shows why. Currently 0 missing cells, so every `*_missing_hours` is expected to be 0 and every day to have 24 hours; the rules exist for future data.

### HDFS layout
Job output goes to `/earthscape/_tmp/power_daily_<job id>` (MapReduce refuses an existing output directory), is verified, then moved to `/earthscape/processed/power_daily/` holding `_SUCCESS` and `part-00000`. No further partitions: 45,655 rows, about 7 MB (an estimate). Provenance goes to `/earthscape/_manifest/power_daily.jsonl` in the same append-only style: the five INTERIM inputs (path, sha256, rows), YARN application id, counters, output path, sha256, size, rows and a version name.

### Execution flow
```
INTERIM  city=*/power_hourly_<city>.csv  (1,095,720 rows)
  -> Mapper        skip header; emit  karachi|2001-01-01 <tab> t,rh,precip,wind,pressure,solar
  -> Shuffle/Sort  all hours of one city-day reach one reducer; keys sorted by city, then date
  -> Reducer       mean/min/max/total, valid and missing counts -> one daily line
  -> PROCESSED     /earthscape/processed/power_daily/part-00000  (45,655 rows)
  -> Validation    independent Python recomputation from INTERIM
```

### Validation contract (future)
- Inputs: 5 files and 1,095,720 data rows (map input records 1,095,725 including 5 headers); INTERIM and RAW checksums and sizes identical before and after.
- Job: SUCCEEDED in YARN (not `job_local`) and `_SUCCESS` present.
- Output: 45,655 daily rows; unique `(city_id, date)`; no missing day from 2001-01-01 to 2025-12-31 for any city; `hours_observed` = 24 on every day (any other value is listed, not hidden).
- Missing counts equal the INTERIM empty-cell counts (0 now).
- An independent stdlib script recomputes every daily value from INTERIM and compares exactly.
- Rerunning gives byte-identical output (exact decimal sums, sorted keys, one reducer).

### Daily to monthly
A separate second MapReduce job reading the daily output (key `city_id|YYYY-MM`, 1,500 expected monthly rows = 300 months x 5), not folded into the first job: it shows a two-stage pipeline and each stage stays small and testable. Weighting of monthly means by valid hours belongs to that job's design.

### Scale statement
POWER INTERIM is about 67 MB and each file is below one 128 MB block, so Hadoop is not technically necessary here. MapReduce is used because it is an explicit assignment requirement and because hourly to daily and monthly aggregation is a legitimate MapReduce workload. The design demonstrates the architecture and does not claim big-data scale.

## MapReduce results (2026-10-08)
Code: `src/processing/mapreduce/` (`power_daily_mapper.py`, `power_daily_reducer.py`, `power_monthly_mapper.py`, `power_monthly_reducer.py`, driver and independent verifier `run_power_job.py`). Run: `.venv\Scripts\python.exe src\processing\mapreduce\run_power_job.py daily` then `... monthly` (needs HDFS and YARN). Tests: `tests/test_power_mapreduce.py` (16, stdlib unittest). The driver verifies INTERIM against its manifest, submits the Streaming job to YARN, checks YARN `Final-State` and application name, verifies the output against a recomputation from INTERIM written independently of the reducers (exact `Fraction` arithmetic), then renames it into place and appends the provenance record. An existing output is never overwritten; the same inputs and script hashes give `skipped`. Hadoop's `-files` cannot take a path with spaces, so the driver ships copies of the two scripts from a temporary WSL directory. Hadoop appends a tab to reducer lines without one, so the jobs set the output separator to a comma (`stream.reduce.output.field.separator`, `stream.num.reduce.output.key.fields=1`, `mapreduce.output.textoutputformat.separator`).

**Correction:** the expected daily count is 45,655, not 45,650 (9,131 days x 5 cities; earlier drafts had an arithmetic slip). The independent key check confirmed 45,655.

| Job | YARN application | Input -> output | Rows | Bytes | SHA-256 |
|---|---|---|---|---|---|
| daily | `application_1791451740685_0004` (`job_1791451740685_0004`) SUCCEEDED, 5 maps, 1 reduce | 5 INTERIM files -> `/earthscape/processed/power_daily/part-00000` | 45,655 | 4,027,812 | `965adf4db7673fd2228c9040152a2c1759debc4f605e47904cd6941696b44932` |
| monthly | `application_1791451740685_0005` (`job_1791451740685_0005`) SUCCEEDED, 2 maps, 1 reduce | daily part-00000 -> `/earthscape/processed/power_monthly/part-00000` | 1,500 | 136,141 | `853f598c758d8f0c4ce9934d558a994768147d97109514b1dfe633ac1c639c6b` |

- Daily counters: map input 1,095,725 (1,095,720 + 5 headers), map output 1,095,720, reduce input groups 45,655, reduce output 45,656 (with header). Every day has 24 hours and 0 missing values, so the custom `incomplete_days` counter never fired (the missing-value rules are covered by unit tests only).
- Monthly counters: map input 45,656, reduce input groups 1,500, reduce output 1,501. Monthly means were checked against the HOURLY INTERIM values: largest difference 0.0000694 (bound 0.0001).
- Manifests `power_daily.jsonl` (8 lines) and `power_monthly.jsonl` (2 lines): each `ok` record holds job id, application id, counters, script sha256, input path/sha256/size/rows, output and validation. The daily manifest also keeps 3 `failed` attempts from the first runs (Hadoop `-files` URI with spaces; trailing-tab separator; the wrong 45,650 expectation); the third produced a correct output that was discarded and recomputed after the constant was fixed. Client logs are in `artifacts/mapreduce/`.
- Idempotency: a repeat of both jobs returned `skipped`; no new output or manifest lines.
- RAW and INTERIM: HDFS listings (path, size, modification time) identical before and after (125 and 5 files); manifests stayed at 250 and 10 lines; after both jobs every one of the 130 files was re-read from HDFS and its sha256 equals the manifest value (0 mismatches).

### Monthly schema (`power_monthly_v1`, 18 fields, key `city_id|YYYY-MM`)
`city_id, month, days_observed, hours_observed, temperature_2m_mean_c, temperature_2m_min_c, temperature_2m_max_c, relative_humidity_2m_mean_pct, precipitation_total_mm, wind_speed_2m_mean_m_s, surface_pressure_mean_kpa, solar_irradiance_mean_mj_hr`, then `*_missing_hours` for temperature, relative humidity, precipitation, wind, pressure, solar.
- Means: daily mean weighted by that day's valid hours (hours observed minus missing), so a partly missing day counts less. Daily means are already rounded to 4 decimals, so a monthly mean differs from the hourly mean by at most 0.0001 (measured 0.0000694). Rounded half up to 4 decimals. Solar stays the mean in `MJ/hr`.
- `temperature_2m_min_c` / `max_c`: lowest / highest hourly value of the month (not instantaneous extremes).
- `precipitation_total_mm`: exact sum of daily totals; empty unless every calendar day of the month is present with a valid daily total (`precipitation_missing_hours` shows hours missing). `days_observed` below the days in the month is visible, never filled.
- Missing hours are summed; a mean with no valid hour is empty. All values derived from reanalysis/satellite-radiation hourly data, never observations.

## Yearly extreme-event job and live sources (2026-10-08)
- **Yearly MapReduce job** (`power_yearly_v1`, `application_1791451740685_0006`, 125 rows = 5 cities x 25 years, `/earthscape/processed/power_yearly/part-00000`, 9,330 bytes, sha256 `f8ad7e93871eb345f1f649b464d6b821e01c0677283ed60546578f2b6892e550`): per city-year annual mean temperature, lowest/highest hourly temperature, annual precipitation (empty unless all days valid), maximum daily precipitation and ETCCDI-style counts (wet >= 1 mm, heavy >= 10 mm, very heavy >= 20 mm, hot day: highest hourly value >= 35 C, frost day: lowest hourly value < 0 C). Verified independently against the hourly INTERIM data (largest mean difference 0.0000598; bound 0.0001); manifest `power_yearly.jsonl`.
- **Live sources:** Open-Meteo weather and air quality (modelled) and OpenAQ PM2.5 (observed) are polled by `src/app/live.py` using `src/ingestion/live_sources.py`. Raw responses are staged under `data/staging/<source>/date=YYYY-MM-DD/` and sealed into `/earthscape/raw/<source>/date=YYYY-MM-DD/` after the UTC day ends (size + sha256 read-back, atomic move, manifest line, never overwritten); normalised readings go to MongoDB `latest_readings`. See architecture.md.
- **MODIS:** `src/ingestion/modis.py` (CMR search, authenticated download, HDF4 validation, pyhdf reader). Measured plan for h24v06/h24v05/h23v05, 2015-2025: 1,515 granules, about 11.2 GB. No data downloaded (no Earthdata credentials).
