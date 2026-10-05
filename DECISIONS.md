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

## [2026-10-05] — Project init and local Hadoop environment verification

**Team member:** Wisam Ahmed
**Status:** In Progress

### Task
Initialize EarthScape (structure, CLAUDE.md), then verify the local dev environment: HDFS, YARN, MapReduce, Python venv, Jupyter kernel, MongoDB.

### Changes made
| File / Module | What changed | Why |
|--------------|-------------|-----|
| CLAUDE.md, README.md, .gitignore, .env.example | Created | Project rules, secret/data safety |
| docs/environment.md, docs/open-decisions.md | Created | Inventory and unresolved decisions |
| data/, src/, notebooks/, config/, tests/, artifacts/ | Empty dirs with .gitkeep | Minimal structure |
| HDFS /earthscape/{raw,interim,processed} | Created | Isolated namespace on shared HDFS |
| .venv | Created (Python 3.13.7), ipykernel install NOT completed | Project-local env |
| DECISIONS.md, CONTEXT.md | Created via /session-handoff (user override of earlier "no handoff files" rule) | Session record |

### Problems & fixes
| Problem | Root cause | Fix applied |
|---------|-----------|-------------|
| YARN unconfigured | mapred-site.xml and yarn-site.xml have no properties | Not fixed; awaiting approval (shared config) |
| pip install ipykernel failed in .venv | pypi.org read timeouts | Retry with --default-timeout 60 was interrupted by user; not yet done |

### Decisions made
- Verified MapReduce via Hadoop's local runner (job_local...), wordcount on temp HDFS path /tmp/earthscape_mr_verify, which was cleaned up. This is NOT a YARN run.
- HDFS started with start-dfs.sh; NameNode, DataNode, SecondaryNameNode confirmed; safemode exited; /earthscape created; other projects' dirs untouched.
- MongoDB service running, ping ok (via global pymongo). No EarthScape DB created.
- User chose to run session-handoff, overriding the "no handoff files" rule.

### Current state
HDFS running and responsive with /earthscape/{raw,interim,processed}. MapReduce works in local mode only; YARN not configured. .venv exists but has no packages, no requirements.txt, no EarthScape Jupyter kernel yet. Docs (environment.md) not yet updated with verified facts; CLAUDE.md lacks the simple-code, comment and git rules requested.

### Next steps
- [ ] Install ipykernel in .venv (retry with longer timeout), freeze requirements.txt, register EarthScape kernel
- [ ] Decide on YARN: propose project-local HADOOP_CONF_DIR vs editing shared mapred-site/yarn-site (needs approval)
- [ ] Update docs/environment.md with verified facts
- [ ] Add simple-code, comment-policy and git-commit rules to CLAUDE.md
- [ ] Answer unresolved decisions in docs/open-decisions.md

---
