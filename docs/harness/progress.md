# Progress

Session-survival log. Update at every clock-out so the next session (or the next
agent) can resume without re-deriving state.

## Current State

Harness scaffold is green (`make check` passes all gates). The **Medical Desert
Planner app runs end-to-end on real data**: the Databricks dev environment is
authenticated (profile `dbc-8ee3f787-8d83`), the Virtue Foundation Marketplace
catalog is added, and the data pipeline has been run to build the cache CSVs.
The Streamlit app serves the deterministic dashboard locally
(`streamlit run app.py`, http://localhost:8501) and the trust-graded coverage
view shows real coverage by state. H001/H002/H003, MDP001, MDP002 are `passing`.
Next actionable feature: MDP003 (spine tests) then MDP004 (geography selector).

2026-06-15: bootstrapped the project. Authenticated the Databricks CLI; added
the `databricks_virtue_foundation_dataset_dais_2026` Marketplace catalog
(tables: `facilities`, `nfhs_5_district_health_indicators`,
`india_post_pincode_directory`). Built the cache via
`data/ingest_marketplace.py` (NFHS + facilities → CSVs),
`data/02_facility_text_ingest.py` (facility free-text → `facilities_text.csv`,
warehouse `eb95df8c60c40891`), and `data/geo_resolve.py` (point-in-polygon
99.98% resolved; trust-graded claims → `district_capability.csv` 4170 rows +
`facility_claims.csv`). Fixed a dark-mode/light-background CSS readability bug
(`.streamlit/config.toml` + explicit text colors). Merged `main` into
`feature/jeff-branch` (resolved one `app.py` conflict, kept the readable CSS)
and pushed. Imported the context-harness from `~/Desktop/prairie` and retargeted
it from the GTM domain to this project.

## Completed

- [x] Markdown context brain under `docs/harness/` (retargeted to this project)
- [x] Python stdlib gate `scripts/harness.py` (`check`, `verify-feature`, `clock-in/out`, `dashboard`)
- [x] Unit tests `scripts/test_harness.py`
- [x] `Makefile` command aliases (+ `triage`, `cost-report`, `hygiene`, extended `check`)
- [x] `AGENTS.md` entry point + `CLAUDE.md` redirect
- [x] `context-harness` and `autoreview` skills under `.claude/skills/`
- [x] Loop layer: `docs/harness/loops.md`, `.claude/agents/{explorer,implementer,verifier}.md`
- [x] Heartbeat: `.github/workflows/ci.yml` + `nightly-triage.yml`, `scripts/triage.py`
- [x] Hygiene: `scripts/check_repo_hygiene.py`, hardened `.gitignore`
- [x] Cost control: `scripts/cost_ledger.py` (C012); eval harness `eval/recall_at_k.py` (C009)
- [x] MDP001 — data pipeline: Marketplace → cache CSVs
- [x] MDP002 — trust-graded coverage view (`coverage_view.py`)

## In Progress

- [ ] MDP003 — deterministic spine tests pass under pytest
- [ ] MDP004 — geography selector + regional coverage rollup

## Known Issues

- Reachability/cost layer only covers the staging-city candidate set (62 districts
  near Patna); the coverage view is nationwide but the mission-cost ranking is not.
- Two overlapping coverage implementations exist: `coverage_view.py` (main's
  trust-graded version, what the app uses) and `coverage_explorer.py` (a simpler
  burden-vs-supply version). Reconcile to one.
- Facilities are not yet deduped on `cluster_id` (~129 dupes inflate counts).
- Trust grade correlates with web-data richness → can amplify urban bias (R2).

## Next Steps

1. Verify MDP003: `./.venv/bin/python -m pytest tests/ -q` (install pytest if needed).
2. Build MDP004 (geography selector) and MDP005 (facility drill-down).
3. Run features through the loop: explorer → implementer (worktree) → verifier.
4. Decide whether to deploy to Databricks Apps + Lakebase (MDP006 persistence).
