# CONTEXT.md — Current Status

> Last updated: 2026-10-05

## What are we building?
EarthScape Climate Agency: Aptech big-data project (HDFS, Hadoop MapReduce, streaming, ML, dashboards, alerts) over climate data.

## Just completed
- Project structure, CLAUDE.md, .gitignore, .env.example, docs
- HDFS started and verified; /earthscape/{raw,interim,processed} created
- MapReduce wordcount verified in local runner mode (not YARN); test files cleaned up
- MongoDB service reachable

## Up next
- Finish .venv (ipykernel, requirements.txt) and register EarthScape kernel
- YARN decision (needs approval; shared config is empty)
- Update docs/environment.md; add simple-code, comment and git rules to CLAUDE.md
- Answer the open architecture decisions in docs/open-decisions.md

## ⚠️ Gotchas — read before touching code
- Hadoop is shared with another project (/urbantransit*). Never format the NameNode or edit shared config without approval.
- Hadoop runs only in WSL Ubuntu-24.04 (/opt/hadoop); HDFS daemons are currently running.
- pypi.org timeouts: use pip --default-timeout 60 --retries 8.
- No application code, data, schemas or models exist yet.

---

Start a new session with: `/session-start`
