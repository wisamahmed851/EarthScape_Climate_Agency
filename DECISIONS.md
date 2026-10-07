# DECISIONS.md — Project Memory

> Every session's changes, bugs, fixes, and decisions are logged here automatically
> by the `session-handoff` Claude Code skill.
>
> 📖 **New to this project?** Read from the bottom up for full history, or just read
> the first (newest) entry to get current status.
>
> 🤖 **Starting a Claude Code session?** Run `/session-start` — Claude reads this
> file and briefs you in under 60 seconds.
>
> Install the skill: https://github.com/s9b/session-handoff

---
<!-- ENTRIES_START — Claude inserts new entries right below this line -->
---

## [2026-10-07] — NASA POWER RAW + INTERIM complete; MapReduce design proposed

**Team member:** Wisam Ahmed
**Status:** In Progress (waiting for user approval of the MapReduce design)

### Task
Finish NASA POWER historical ingestion (RAW), design and build RAW to INTERIM for all 5 cities, then design the first MapReduce job (hourly to daily).

### Changes made
| File / Module | What changed | Why |
|--------------|-------------|-----|
| src/ingestion/nasa_power.py, nasa_power_batch.py | NASA POWER ingestion (one city-year) and resumable batch with full HDFS verification | RAW ingestion of 125 city-years |
| src/processing/batch/power_interim.py | RAW to INTERIM transformer (stdlib only), 9-column CSV, interim manifest | Confirmed INTERIM contract |
| tests/test_nasa_power*.py, tests/test_power_interim.py | 22 unit tests, all pass | Verify ingestion and transformation logic |
| docs/data-architecture.md | Implementation status, confirmed POWER INTERIM contract, PROPOSED MapReduce design; fixed stale statements | Keep design and status current |
| docs/open-decisions.md | Confirmed contract moved to Decided; items 14 and 16-19 for MapReduce | Track pending decisions |
| docs/progress-log.md | New summary of all work so far | User request |
| CLAUDE.md | Status line, fixed broken `nasa_power.py` path, handoff note | Project truth file |
| HDFS /earthscape | raw/power_hourly_gridded (125 files, 48,048,922 B), interim/power_hourly (5 files, 67,291,971 B), manifests power_hourly_gridded.jsonl and power_hourly_interim.jsonl | Data layers |

### Problems & fixes
| Problem | Root cause | Fix applied |
|---------|-----------|-------------|
| Batch interrupted at 52/125 | Session ended mid-run | Read-only inspection, then resumed (73 more, 0 failures) |
| HDFS down at resume | HDFS is not auto-started in WSL | Started with documented start-dfs.sh, no config touched |
| Corrupted paths in docs (`\n` became a newline) | Shell heredoc escaping | Repaired with Edit |
| Solar unit ambiguity | POWER reports `MJ/hr`, area basis not stated | User decided: field `solar_irradiance_mj_hr`, unit kept, no conversion |

### Decisions made
- RAW POWER stays immutable; `-999` stays in RAW (0 occurrences found).
- INTERIM: CSV, one file per city under /earthscape/interim/power_hourly/city=<city>/, 9 columns, UTC start-of-hour timestamp, no unit conversion, `-999` becomes empty plus `missing_vars`, transform version `power_interim_v1`, no `value_origin` per row (lineage in manifest).
- PRECTOTCORR uses RAW header unit mm/hour; catalogue says mm/day, recorded in manifest.
- PROPOSED, not approved: Hadoop Streaming with Python, 17-field daily schema, 45,650 expected daily rows, output /earthscape/processed/power_daily/, one reducer.

### Current state
RAW (125 files, 1,095,720 rows) and INTERIM (5 files, 1,095,720 rows, 0 gaps, 0 duplicates, 0 missing) are complete, verified and idempotent. No MapReduce code exists. Everything is uncommitted apart from this handoff commit. HDFS and WSL must be started by hand.

### Next steps
- [ ] User approves or amends the MapReduce proposal (items 14, 16-19 in docs/open-decisions.md)
- [ ] Implement the first MapReduce job (hourly to daily) and its validation script
- [ ] Later: daily to monthly job, other data sources

---
