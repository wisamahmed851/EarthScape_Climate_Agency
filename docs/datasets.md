# EarthScape datasets (research + validation, no pipeline yet)

Verified 2026-10-06 against official provider docs/APIs. Raw sample responses are in `data/raw/_provider_samples/` (git-ignored, unmodified provider output).

## Geographic scope
Pakistan. Coordinates are GeoNames city-centre points from the Open-Meteo geocoding API (one set for the whole project). Elevation is the GeoNames value; each provider also reports its own grid elevation.

| City | Province/territory | Lat | Lon | GeoNames elev. (m) | GeoNames id |
|---|---|---|---|---|---|
| Karachi | Sindh | 24.8608 | 67.0104 | 8 | 1174872 |
| Lahore | Punjab | 31.558 | 74.35071 | 216 | 1172451 |
| Islamabad | Islamabad Capital Territory | 33.72148 | 73.04329 | 579 | 1176615 |
| Peshawar | Khyber Pakhtunkhwa | 34.008 | 71.57849 | 340 | 1168197 |
| Quetta | Balochistan | 30.18414 | 67.00141 | 1683 | 1167528 |

## Provider matrix
| Role | Provider / product | Nature of data | Auth | Verified for 5 cities |
|---|---|---|---|---|
| Historical climate | NASA POWER (daily + hourly point API) | Gridded: MERRA-2 reanalysis + CERES SYN1deg satellite radiation. NOT station measurements | None | Yes |
| Current weather | Open-Meteo forecast API `current=` | Weather-model output (not station observations) | None (free, non-commercial) | Yes |
| Air quality | OpenAQ v3 | Aggregated third-party monitors (reference + low-cost) | API key (free) | Yes: PM2.5 all cities; no gases; short history |
| Satellite | NASA Earthdata (LP DAAC MODIS V061) | Actual satellite observation, L3 gridded products | Earthdata Login (free) for download; CMR search is keyless | Metadata only, no download |

## NASA POWER
- Docs: https://power.larc.nasa.gov/docs/ ; endpoint `https://power.larc.nasa.gov/api/temporal/{daily|hourly}/point` (API v2.10.0 seen).
- Coverage: global grid; all five cities returned data. Grid cell MERRA-2 0.5 x 0.625 deg; returned elevation is the cell average (Karachi 53 m, Lahore 209 m, Islamabad 544 m, Peshawar 515 m, Quetta 1804 m), which differs from GeoNames.
- Daily: 1981-01-01 onward (radiation from 1984-01-01; earlier radiation is dropped with a message or -999). Hourly: from 2001-01-01 (earlier start gives HTTP 422). Latency: last real value was 2026-10-03 when queried on 2026-10-06; later days return -999.
- Formats: JSON, CSV, NetCDF, ASCII, ICASA (AG community only). Time standard LST (default) or UTC. Missing value: -999 (header `fill_value`). Point request limit: 20 parameters. Rate limits are not stated in the docs read (HTTP 429 exists); no API key.
- Attribution: acknowledge the NASA LaRC POWER project and cite service, version and access date. No explicit licence restriction found.

| Parameter | Meaning | Daily unit | Hourly unit | Source |
|---|---|---|---|---|
| T2M | Temp at 2 m | C | C | MERRA-2 |
| T2M_MAX / T2M_MIN | Max/min hourly temp in the day | C | daily only | POWER (computed from hourly) |
| RH2M | Relative humidity at 2 m | % | % | MERRA-2 |
| PRECTOTCORR | Bias-corrected precipitation | mm/day | mm/hour | MERRA-2 corrected |
| WS2M | Wind speed at 2 m | m/s | m/s | MERRA-2 |
| PS | Surface pressure | kPa | kPa | MERRA-2 |
| ALLSKY_SFC_SW_DWN | Solar irradiance, horizontal surface | MJ/m^2/day | MJ/hr | CERES SYN1deg |

Sample validation: daily 2024-01-01..05, all 8 parameters returned for all 5 cities; hourly CSV tested for Karachi on 2024-01-01 (24 rows). First-day T2M: Karachi 21.44, Lahore 12.02, Islamabad 12.11, Peshawar 11.95, Quetta 5.95 C.

Raw JSON schema: GeoJSON `Feature`; `geometry.coordinates=[lon,lat,elev]`; `properties.parameter.{PARAM}.{YYYYMMDD}=value`; `header` (title, api version, sources, fill_value, time_standard, start, end); `parameters.{PARAM}={units,longname}`; `messages[]`. CSV: text header block, then `YEAR,MO,DY[,HR],params...`.

## Open-Meteo
- Docs: https://open-meteo.com/en/docs ; endpoint `https://api.open-meteo.com/v1/forecast?current=...`.
- Free tier: non-commercial (educational/public research qualifies), no key, under 10,000 calls/day, 5,000/hour, 600/minute. Data CC-BY 4.0, attribute Open-Meteo.
- `current` values are 15-minute model data (interval 900 s observed). With `timezone=UTC` timestamps are UTC. Docs state current conditions are model-based, not station observations; the model used for Pakistan is not shown in the response.
- The returned grid point differs from the request (Karachi 24.8506, 66.9925, 8 m; Quetta 30.1933, 66.9474, 1684 m).
- Variables tested (all returned for all 5 cities, no missing fields): temperature_2m C, relative_humidity_2m %, apparent_temperature C, precipitation mm, rain mm, weather_code wmo, surface_pressure hPa, pressure_msl hPa, wind_speed_10m km/h, wind_direction_10m deg, cloud_cover %.
- Differences from POWER: wind is 10 m in km/h (POWER 2 m in m/s); pressure hPa (POWER kPa); precipitation is a current-interval amount, not an hourly/daily total; temperature/humidity come from a different model. Do not treat the two as one series without documented conversion.
- Report wording: "near-real-time model-based current weather, polled periodically from a REST API". This is NOT continuous event streaming; the streaming design is still undecided.
- Raw JSON schema: `latitude, longitude, elevation, utc_offset_seconds, timezone, current_units{}, current{time, interval, vars...}`.

## OpenAQ (verified 2026-10-06, API v3)
- Docs: https://docs.openaq.org/ ; endpoint `https://api.openaq.org/v3/`, key sent in the `X-API-Key` header (stored in git-ignored `.env`, variable `OPENAQ_API_KEY`; authentication succeeded). Free limits: 60 requests/min, 2,000/hour.
- Nature of data: aggregated public measurements from contributing networks, not an OpenAQ-operated network. Pakistan providers (country id 109, 447 locations): Hawanama (223, PAQI community network, PM2.5 only), AirGradient (217, low-cost sensors: PM1, PM2.5, RH, temperature, particle count), AirNow (3) and StateAir (2) (US-embassy reference monitors, PM2.5), Clarity (2, PM1/PM2.5/PM10). Only 5 of 447 locations are reference monitors (`isMonitor`). Licences seen: CC BY 4.0 (attribute the owner, e.g. The Urban Unit, PAQI).
- Parameters in all of Pakistan: pm25, pm10, pm1, relativehumidity, temperature (C and F), um003 (particles/cm3). **No NO2, SO2, CO or O3 exists anywhere in Pakistan on OpenAQ.** PM10 exists at only 6 locations (Karachi AirGradient x4, Clarity Karachi, Clarity Lahore). Units: ug/m3 for PM, % for RH, c/f for temperature.
- Location identification: location `id`, `coordinates`, `provider`, `owner`, `sensors[]` (each with `parameter`), `datetimeFirst`/`datetimeLast`. City is not a reliable key, so coverage was computed by great-circle (haversine) distance from the project coordinates over all 447 Pakistan locations; radius 25 km, nearest 10 km counted separately. No distant station was assigned to a city.
- "Active" = `datetimeLast` on or after 2026-09-29 (within about 7 days). Spot checks of the sensor `hours`/`latest` endpoints confirmed recent PM2.5 for all 5 cities (Lahore, Islamabad, Peshawar, Quetta via `hours`; Karachi via `locations/6135449/latest`, 2026-10-06T11:00Z, 12.0 ug/m3). The `hours` and `measurements` endpoints returned HTTP 500 for the Karachi sensor 15904590 (provider-side error, not absence of data).
- Raw samples: `data/raw/_provider_samples/openaq_*.json` (country, 447-location pages, recent hours).

| City | Locations within 25 km (active) | Closest useful | PM2.5 | PM10 | NO2/SO2/CO/O3 | Earliest | Latest | Quality |
|---|---|---|---|---|---|---|---|---|
| Karachi | 55 (23) | 8156 Karachi AirNow 2.2 km (stale); active: 6135449 TDF MagnifiScience 1.8 km | yes | yes, few sites (Clarity, AirGradient) | none | 2019-05-22 (AirNow) | 2026-10-06 | PARTIAL |
| Lahore | 83 (56) | 8664 US Diplomatic Post 1.4 km (stale); active: 6135329 LAS Lahore 1.3 km | yes | Clarity 1894641 only, last 2026-08-31 | none | 2019-05-22 (StateAir) | 2026-10-06 | PARTIAL |
| Islamabad | 45 (27) | 233470 Islamabad AirNow 6.7 km (stale); active: 4515644 F-7 1.2 km | yes | none found | none | 2019-05-22 (StateAir) | 2026-10-06 | PARTIAL |
| Peshawar | 23 (17) | 8088 Peshawar AirNow 3.8 km (stale); active: 4609354 Ashrafia Colony 2.1 km | yes | none found | none | 2019-06-16 (AirNow) | 2026-10-06 | PARTIAL |
| Quetta | 20 (12) | 4757303 Industries and Commerce 1.0 km | yes | none found | none | 2025-06-16 | 2026-10-06 | POOR |

Classification criteria (conservative): GOOD = at least 3 active PM2.5 sites, PM10 and gases, and 5+ years of continuous history. PARTIAL = active PM2.5 now plus either missing pollutants or short history. POOR = active PM2.5 but under 2 years of history and no reference station. NONE = nothing within 25 km. No city meets GOOD.

Historical depth, measured from `sensors/{id}/days` for the reference-style PM2.5 sensors:

| Location | Provider | First | Last | Days with data |
|---|---|---|---|---|
| 8156 Karachi | AirNow | 2019-05-22 | 2025-03-03 | 2,044 |
| 8664 US Diplomatic Post Lahore | StateAir Lahore | 2019-05-22 | 2025-03-03 | 2,038 |
| 233470 Islamabad | AirNow | 2021-08-25 | 2026-02-15 | 1,255 |
| 8088 Peshawar | AirNow | 2019-06-15 | 2025-03-24 | 2,042 |

These reference stations stopped reporting in 2025 (Islamabad in 2026), so there is a hole between their end and the low-cost networks. Low-cost AirGradient/Hawanama sensors start mid-2025 (AirGradient 2025-05/06, Hawanama 2025-11) and are mostly still running, so continuous multi-city history is about 12 to 16 months. Low-cost sensors are not reference-grade and are not directly comparable to the embassy monitors.

**Assessment: PARTIALLY SUFFICIENT.** Good for current PM2.5 monitoring and dashboard display in all five cities (at least 12 active sites each). Insufficient for NO2/SO2/CO/O3, PM10 (only Karachi/Lahore), a long common history, and a consistent record across cities. Cannot support a multi-year anomaly baseline on its own.

### Free alternatives (report only; none adopted, nothing implemented)
| | Open-Meteo Air Quality API | CAMS Global Reanalysis EAC4 (Copernicus ADS) |
|---|---|---|
| Nature | CAMS global model forecasts (not measurements) | Reanalysis assimilating observations (not measurements) |
| Pollutants | PM10, PM2.5, CO, NO2, SO2, O3 (tested: all 6 returned for all 5 cities, ug/m3) | PM1, PM2.5, PM10, NO2, SO2, CO, O3 |
| Resolution | 0.4 deg (~45 km), 3-hourly globally, from 2022-08 (earlier dates returned null) | 0.75 deg, 3-hourly |
| History | from 2022-08 to now (+ forecast) | 2003 to 2025, 4-6 month delay |
| Access / auth | REST, no key | Atmosphere Data Store account, free |
| Format | JSON | GRIB (NetCDF option) |
| Licence | CAMS ENSEMBLE + Open-Meteo attribution, non-commercial | CC-BY |
| Fit | Matches the Open-Meteo weather integration; fills the gases gap; model estimate, ~45 km | Longest history; real scientific format; large grid, delayed |
Official Pakistani sources (Punjab/Sindh EPA) were not investigated in this task; no open API for them is known yet.

Recommendation (needs approval): keep OpenAQ for observed PM2.5/PM10 (current + recent history, with the limits above), and add Open-Meteo Air Quality as the source for the gases and a gap-free model series, labelled model-based. Use CAMS EAC4 only if a multi-year air-quality baseline is required.

## NASA Earthdata (LP DAAC MODIS Terra, version 061)
Search/metadata via CMR (keyless): https://cmr.earthdata.nasa.gov/search/ . Granule download needs a free Earthdata Login account (not created; nothing downloaded). All collections start 2000-02 and are ongoing (latest MOD11A2 granule in CMR: 2026-09-22).

| | Land Surface Temperature | NDVI |
|---|---|---|
| Daily | MOD11A1, 1 km, ~4 MB/tile | none |
| 8/16-day | **MOD11A2, 1 km, 8-day composite, ~5.5 MB/tile** | MOD13Q1, 250 m, 16-day, ~183 MB/tile |
| Monthly | MOD11C3, 0.05 deg, ~68 MB global | MOD13A3 1 km ~17 MB/tile; MOD13C2 0.05 deg ~90-100 MB global |
| Satellite/instrument | Terra / MODIS | Terra / MODIS |
| Format | HDF (HDF4-EOS) | HDF (HDF4-EOS) |
| 1 km tiles for the cities | h24v06 Karachi; h24v05 Lahore, Islamabad; h23v05 Peshawar, Quetta | same |

Sizes and tiles come from CMR granule listings. HDF4 needs a reader library (decision pending). Cloud-obscured pixels are fill values in both product families (provider documentation, not tested here).

## Formats (only what was observed)
- NASA POWER: JSON and CSV tested; NetCDF, ASCII, ICASA stated in docs (not tested).
- Open-Meteo: JSON (tested).
- OpenAQ: JSON via API (tested); the S3 archive path shows `csv.gz` (not inspected).
- MODIS LP DAAC: HDF (.hdf in granule links). GeoTIFF/NetCDF not seen for these collections.
- So the scientific-format case is HDF4-EOS from MODIS; NetCDF exists only as a POWER output option.

## Provenance (verified 2026-10-06)
| Source | Official URL | Access | Temporal | Resolution |
|---|---|---|---|---|
| NASA POWER daily | https://power.larc.nasa.gov/ | REST point, no key | 1981 to ~3 days ago | daily; MERRA-2 0.5 x 0.625 deg; radiation CERES 1 deg |
| NASA POWER hourly | same | same | 2001 to ~3 days ago | hourly |
| Open-Meteo | https://open-meteo.com/ | REST, no key | now | 15 min, model grid |
| OpenAQ | https://openaq.org/ | REST v3, key | 2019-05 to now (reference stations ended 2025; low-cost from 2025-06) | per station |
| MODIS MOD11A2 | https://www.earthdata.nasa.gov/ (LP DAAC) | CMR search + Earthdata Login download | 2000-02 to present | 8-day, 1 km tiles |

## Coverage gaps and risks
- OpenAQ has no NO2/SO2/CO/O3 in Pakistan, PM10 only in Karachi/Lahore, and only about 12-16 months of continuous multi-city history (low-cost sensors, not reference-grade).
- POWER is not station data: city values are grid-cell estimates; grid elevation differs from the city (Quetta 1804 m vs 1683 m).
- POWER radiation starts 1984 (daily); hourly starts 2001; data lags about 3 days; missing is -999.
- Units differ across providers (kPa vs hPa, 2 m m/s vs 10 m km/h, mm/day vs mm).
- Resolutions differ: point time series vs 8-day 1 km raster tiles; correlation needs a documented aggregation.
- Satellite download needs a free account and an HDF4 reader; cloud gaps expected.
- POWER rate limits not found in the docs read; throttle requests.

## Historical size comparison (5 cities)
Row counts are arithmetic; sizes are estimates (~60 B per hourly CSV row, ~100 B per daily row with 8 parameters).

| Option | Rows | Est. size |
|---|---|---|
| Daily 10 y (2016-2025) | 18,260 | ~2 MB |
| Daily 25 y (2001-2025) | 45,655 | ~5 MB |
| Daily 45 y (1981-2025) | 82,170 | ~8 MB |
| Hourly 10 y | 438,360 | ~26 MB |
| Hourly 25 y (2001-2025) | 1,096,000 | ~66 MB |

Every option is below one 128 MB HDFS block, so this data is small for Hadoop whichever is chosen; the report should say so. Hourly gives MapReduce real aggregation work (hourly to daily/monthly, anomalies against the diurnal cycle), and a daily series can be derived from it (tagged derived). Daily alone is simpler but gives little to process.

## APPROVED configuration (final, 2026-10-06)
Changing any of this (cities, providers, periods, resolutions, variables) requires asking the user first.

```
EarthScape - Pakistan (Karachi, Lahore, Islamabad, Peshawar, Quetta)
|
+-- Historical Climate   -> NASA POWER   [gridded MERRA-2 reanalysis + CERES satellite radiation]
|                           hourly, 2001-01-01..2025-12-31, UTC time standard
|                           T2M, RH2M, PRECTOTCORR, WS2M, PS, ALLSKY_SFC_SW_DWN
|                           (no daily-only T2M_MAX/MIN in the hourly raw; daily stats are DERIVED later)
+-- Current Weather      -> Open-Meteo   [model-based, near-real-time, obtained through REST API polling]
|                           variables verified above; polling is not event streaming
+-- Environmental
|   +-- OBSERVED         -> OpenAQ v3    [aggregated monitors: mostly low-cost/community; PM2.5 primary]
|   +-- MODELLED         -> Open-Meteo Air Quality [CAMS model: PM10, PM2.5, CO, NO2, SO2, O3, ug/m3]
|                           never merged with OpenAQ in a way that makes modelled values look observed
+-- Satellite            -> NASA Earthdata MODIS MOD11A2 v061 Land Surface Temperature
                            2015-01-01..2025-12-31, tiles h24v06 h24v05 h23v05, native HDF4-EOS
```
Raw formats: POWER CSV and/or JSON, Open-Meteo JSON (weather and air quality), OpenAQ JSON, MODIS HDF4-EOS. Provider-native data is preserved, not converted to CSV at ingestion.

Known weaknesses that stay documented: POWER is gridded, not station data; Open-Meteo values are model output; OpenAQ Pakistan has PM2.5 in all five cities but mostly low-cost sensors, stale reference stations, about 12-16 months of common continuous history and no gas pollutants (hence the modelled source); Quetta OpenAQ coverage is POOR; MODIS has cloud gaps.

## OPTIONAL / FUTURE (not approved for implementation)
- CAMS EAC4 reanalysis (Copernicus ADS) for a multi-year air-quality baseline.
- Official Pakistani EPA sources (not investigated).
- Open-Meteo Air Quality history backfill beyond live polling (period not decided).
- HDF4 reader library (decided at implementation).
