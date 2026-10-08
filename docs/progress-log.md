# EarthScape progress log

Summary of the work done so far. Details live in [data-architecture.md](data-architecture.md), [datasets.md](datasets.md), [environment.md](environment.md) and [open-decisions.md](open-decisions.md). Nothing here is committed to Git yet.

## 1. Project setup (earlier sessions)
- Project structure, Python `.venv`, `CLAUDE.md` project rules, Jupyter kernel `EarthScape`.
- Local Hadoop 3.4.3 in WSL Ubuntu-24.04 (shared with UrbanTransit). EarthScape uses only `/earthscape` and its own config in `config/hadoop`; HDFS is started by hand (see environment.md). MapReduce was verified through YARN.
- Dataset research and approved configuration: Pakistan; Karachi, Lahore, Islamabad, Peshawar, Quetta; NASA POWER hourly, Open-Meteo, OpenAQ, Open-Meteo Air Quality, MODIS MOD11A2 (datasets.md).
- Storage design confirmed 2026-10-06: HDFS layers RAW / INTERIM / PROCESSED, partitioning, manifest, idempotency, retry rules (data-architecture.md).

## 2. NASA POWER RAW ingestion (complete)
- Code: `src/ingestion/nasa_power.py` (one city-year) and `src/ingestion/nasa_power_batch.py` (sequential, resumable batch with full verification).
- Lifecycle: provider, local staging, validation, sha256, HDFS `_tmp`, read-back check, atomic move, manifest `ok`.
- Pilot (Karachi 2001) passed, then the full batch ran. It was interrupted at 52 of 125 objects. A read-only inspection found it safe to resume, and the resume finished the other 73.
- Final result: 125 city-year objects (5 cities x 2001..2025), 1,095,720 rows, 48,048,922 bytes, 0 `-999` cells, 0 failed. The RAW manifest has 250 lines (125 `started`, 125 `ok`).
- A second full run ingested 0, skipped 125, failed 0, and made no provider requests.
- Only Quetta 2012 needed retries (4 attempts).

## 3. RAW to INTERIM design (confirmed)
Documented in data-architecture.md, "POWER RAW to INTERIM contract".
- **Format:** CSV, one file per city: `/earthscape/interim/power_hourly/city=<city>/power_hourly_<city>.csv`.
- **Row (9 columns):** `timestamp_utc`, `city_id`, `temperature_2m_c`, `relative_humidity_2m_pct`, `precipitation_mm_h`, `wind_speed_2m_m_s`, `surface_pressure_kpa`, `solar_irradiance_mj_hr`, `missing_vars`.
- **Timestamp:** `YYYY-MM-DDTHH:00:00Z`, UTC, start of the hour, no shift.
- **Units:** all provider units are kept, with no numeric conversion.
  - Solar stays `MJ/hr` as POWER reports it; it is not relabelled `MJ/m²/hour`. The physical quantity is GHI.
  - PRECTOTCORR uses the RAW header unit, mm/hour. The POWER catalogue says mm/day, and the discrepancy is recorded in the manifest.
- **Missing values:** `-999` becomes an empty field and the field name goes into `missing_vars`. Nothing is imputed.
- **Not in rows:** latitude, longitude, elevation, year, month, day, hour and `value_origin`.
- **Lineage:** kept per variable in the manifest.
  - Five variables are MERRA-2 reanalysis, model-based and gridded.
  - Solar is CERES SYN1deg, satellite-derived and gridded.
  - None are station observations.
- **Manifest:** `/earthscape/_manifest/power_hourly_interim.jsonl`, transform version `power_interim_v1`.

## 4. RAW to INTERIM implementation (complete)
- Code: `src/processing/batch/power_interim.py`; tests in `tests/test_power_interim.py`. It uses the standard library only.
- It verifies each RAW input (manifest `ok`, size, sha256, header, rows), transforms, validates, stages locally, uploads via `_tmp`, reads back, moves into place and appends the manifest.
- It never overwrites a different existing output, and a rerun on unchanged inputs is skipped.
- Karachi pilot ran first, then Lahore, Islamabad, Peshawar and Quetta with no code change.

| City | Rows | Bytes | SHA-256 |
|---|---|---|---|
| karachi | 219,144 | 13,541,214 | `8de350a844db03c64b356b411a6334690d441843355a896fef93a42ef82a00ba` |
| lahore | 219,144 | 13,182,480 | `c9998c14a6c8ea7fc96b397cf8729180629cbd011a4734ec828474adfb7e4e25` |
| islamabad | 219,144 | 13,839,674 | `8515abb941e05f1929f4f954a402ad70c3fd17362d7d94e81c7293e2f3bee869` |
| peshawar | 219,144 | 13,620,653 | `bcfbd152e93f3ad34ae462fd6256f1b25d1f1b20d2259299ac6671fdd02be4e7` |
| quetta | 219,144 | 13,107,950 | `8bca171d6f31f4b4ba5434568d780d648d572d02af070d2caa619bc3e79c6b5b` |
| total | 1,095,720 | 67,291,971 | |

Verification, done by a separate script that re-read every INTERIM file and all 125 RAW objects:
- 5 files and no unexpected ones; header plus 219,144 rows each; 9 columns.
- First timestamp `2001-01-01T00:00:00Z`, last `2025-12-31T23:00:00Z`; 0 gaps, 0 duplicate keys.
- 0 value mismatches against RAW; 0 `-999` in RAW and 0 empty cells in INTERIM.
- The manifest has 10 lines (5 `started`, 5 `ok`). Each record traces to 25 RAW inputs, and its sha256, size and rows equal the HDFS file.
- A second run over all five cities transformed 0, skipped 5, failed 0, and the manifest stayed at 10 lines.
- RAW is unchanged. `/earthscape/_tmp` and local staging are empty.

## 5. Tests
`.venv\Scripts\python.exe -m unittest discover -s tests` runs 22 tests (16 ingestion, 6 interim), all passing.

## 6. Housekeeping done along the way
- Fixed the broken `src\ingestion\nasa_power.py` path in `CLAUDE.md`.
- Updated the `CLAUDE.md` status line, `docs/data-architecture.md` and `docs/open-decisions.md` with the actual results.
- `/urbantransit*` and the shared Hadoop config were never touched.

## 7. Not started
MapReduce (the Java vs Hadoop Streaming choice is open), PROCESSED data and aggregates, EDA, ML and anomaly detection, Open-Meteo / OpenAQ / MODIS ingestion, streaming, MongoDB, dashboards, Tableau, alerts and deployment.

## 8. Next step
Choose the MapReduce implementation style (Java or Hadoop Streaming), then design the first job, hourly to daily aggregation over the INTERIM files.
