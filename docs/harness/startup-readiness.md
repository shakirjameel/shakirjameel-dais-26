# Startup Readiness

Make setup reproducible. Harness dependencies use only the Python standard
library, so the gate runs before any application dependencies are installed.

## Start commands

- Clock in: `make agent-clock-in`
- Run harness checks: `make harness-check`
- Run all known checks (also CI): `make check`
- Triage brief: `make triage` (or `/loop`, `/goal` interactively — see `loops.md`)
- Cost totals: `make cost-report`
- Generate dashboard: `make dashboard`
- Show code graph status: `code-review-graph status --repo .`
- Rebuild code graph: `code-review-graph build --repo .`
- Clock out: `make clock-out`
- Commit pending work: `make auto-commit` (see `git-workflow.md`, C016)
- New feature branch: `make feature NAME="..."`; release: `make cut-release`; PR: `make open-pr`

App stack (Python + Streamlit + Databricks):

- Create the venv (Python 3.11): `python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt`
- Authenticate Databricks: `databricks auth login --host <workspace-url>`
- Build the data cache (one-time, needs Databricks): `DATABRICKS_CONFIG_PROFILE=<profile> DBSQL_WAREHOUSE_ID=<id> ./.venv/bin/python data/02_facility_text_ingest.py && ./.venv/bin/python -m data.geo_resolve`
- Run the app: `./.venv/bin/streamlit run app.py` (http://localhost:8501)
- Run the spine tests: `./.venv/bin/python -m pytest tests/ -q`
- Eval metric self-check: `python3 eval/recall_at_k.py --selftest`

Data lives in Unity Catalog Marketplace (catalog
`databricks_virtue_foundation_dataset_dais_2026`); the app falls back to local
cache CSVs + SQLite when Databricks env vars are absent.

## Initialization acceptance checklist

- [x] A harness verification command exists (`make harness-check`)
- [x] Required files all present (`scripts/harness.py` `REQUIRED_FILES`)
- [x] Feature queue parses with valid states
- [x] `context-harness` skill has valid frontmatter and no `[TODO]`
- [x] Claude-compatible redirect added in `CLAUDE.md`
- [x] Code-review graph configured for Codex and Claude Code
- [x] CI runs `make check` on push/PR (`.github/workflows/ci.yml`)
- [x] Secrets documented in `.env.example`; `.env`/dashboard gitignored
- [x] Loop layer present (automations, maker/checker subagents, worktrees — `loops.md`)
- [x] App stack (`app.py`/`mission_core/`, Python + Streamlit) and eval harness (`eval/`, Python) present
- [ ] Git checkpoint commit for this loop-layer change exists (blocked: session lacks git write perms)
