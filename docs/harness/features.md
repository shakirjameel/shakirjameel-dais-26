# Features — the work queue

Keep each item small enough to complete in one session and give it an executable
verification command. Valid states: `not_started`, `active`, `blocked`,
`failing`, `passing`. Only `scripts/harness.py verify-feature <ID>` may move an
item to `passing`. IDs look like `H001` (harness) or `MDP001` (app).

The table below is owned by the harness — edit cells freely, but leave the
markers in place; `scripts/harness.py` parses and rewrites between them.

<!-- harness:features:start -->
| id | behavior | verification | state | evidence |
| --- | --- | --- | --- | --- |
| H001 | Harness gate validates the workspace and tests pass | `python3 scripts/harness.py check --skip-tests` | passing | Harness check passed. |
| H002 | Repo hygiene enforced: secrets untracked, generated artifacts ignored, CI present | `python3 scripts/check_repo_hygiene.py` | passing | Repo hygiene check passed. |
| H003 | Loop tooling: triage + cost ledger + maker/checker subagents | `python3 scripts/triage.py --selftest && python3 scripts/cost_ledger.py --selftest && test -f .claude/agents/verifier.md` | passing | cost_ledger selftest passed |
| MDP001 | Data pipeline builds the cache CSVs the app reads from Unity Catalog | `test -f data/cache/district_capability.csv && test -f data/cache/nfhs5_districts.csv` | passing | district_capability.csv (4170 rows) built |
| MDP002 | Trust-graded coverage view returns ranked districts for a capability+state | `./.venv/bin/python -c "from mission_core.coverage_view import coverage_by_geography as c; assert c('maternity','Bihar')"` | passing | Bihar maternity: 38 districts |
| MDP003 | Deterministic spine tests pass (burden, coverage, cost, chain) | `./.venv/bin/python -m pytest tests/ -q` | active | Replace when verified |
| MDP004 | Geography selector + regional coverage rollup (state/district/PIN) | `./.venv/bin/python -c "from mission_core.coverage_view import coverage_summary, coverage_by_geography as c; assert coverage_summary(c('maternity','Bihar'))['districts']>0"` | active | Replace when built |
| MDP005 | Facility drill-down: trust-scored records + cited evidence behind an aggregate | `./.venv/bin/python -c "from mission_core.data_access import load_facility_claims as f; assert f(capability='maternity')"` | not_started | Replace when built |
| MDP006 | Save / load a planning scenario (Lakebase, SQLite fallback) | `./.venv/bin/python -c "from mission_core.data_access import _store_execute"` | not_started | Replace when built |
| MDP007 | Data-quality: dedupe facilities on cluster_id; surface public/private split | `./.venv/bin/python -c "import csv; r=list(csv.DictReader(open('data/cache/district_base.csv'))); assert any('public' in k for k in r[0])"` | not_started | Replace when built |
<!-- harness:features:end -->
