# CONTEXT.md — Current Status

> Auto-updated by `session-handoff`. Always reflects the current state of the project.
> Last updated: 2026-10-07

## What are we building?
EarthScape Climate Agency: an Aptech final-semester big-data climate monitoring project for five Pakistani cities (Karachi, Lahore, Islamabad, Peshawar, Quetta) on HDFS with mandatory Hadoop MapReduce.

## Just completed
- NASA POWER hourly RAW ingestion: 125 city-year files, 1,095,720 rows, 48,048,922 bytes, 0 `-999`, verified and idempotent.
- NASA POWER RAW to INTERIM: 5 city CSVs, 9 columns, 67,291,971 bytes, 0 gaps, 0 duplicates, values equal to RAW, idempotent. Code in `src/processing/batch/power_interim.py`.
- 22 unit tests pass (`.venv\Scripts\python.exe -m unittest discover -s tests`).
- MapReduce design (Hadoop Streaming with Python, hourly to daily, 45,650 daily rows) written up as PROPOSED in `docs/data-architecture.md`.
- Summary of everything so far: `docs/progress-log.md`.

## Up next
- Approve or amend the MapReduce proposal (`docs/open-decisions.md` items 14 and 16-19: Streaming vs Java, solar daily aggregation, precipitation rule, reducer count/rounding, monthly as a separate job).
- Then implement the first job (hourly to daily) with an independent validation script.

## ⚠️ Gotchas — read before touching code
- HDFS/YARN are not auto-started and die when WSL has no open session: follow `docs/environment.md`.
- Use only `/earthscape` in HDFS; never touch `/urbantransit*` or `/opt/hadoop/etc/hadoop`; never format the NameNode.
- RAW and INTERIM are immutable inputs; do not re-run RAW ingestion.
- Solar `solar_irradiance_mj_hr` keeps the provider unit `MJ/hr`; do not relabel or convert without a user decision.
- In Git Bash, `wsl ... /opt/...` paths get mangled; use PowerShell for WSL commands.
- Streaming mappers would run under WSL `python3` 3.12 (stdlib only), not the Windows `.venv`.
- Almost everything is uncommitted apart from the handoff commit; see `git status`.

---

## Starting a new Claude Code session?

Paste this into Claude Code at the start:

```
Read CLAUDE.md, then the latest entry in DECISIONS.md, then CONTEXT.md.
Give me a briefing on the current project state before doing anything.
```

Or just run: `/session-start`
