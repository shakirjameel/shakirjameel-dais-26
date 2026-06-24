# Quality

Per-area quality grades (A–D). Lower a grade when verification fails or debt
accrues; raise a grade only with evidence. Prioritize cleanup on the
lowest-scoring area that has active work.

| Module or area | Quality | Verification passing | Agent understandable | Test stability | Architecture compliance | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Harness gate (`scripts/harness.py`) | A | yes | yes | stable | yes | Stdlib only; covered by `test_harness.py` |
| Context docs (`docs/harness/`) | A | n/a | yes | n/a | yes | Routed from `AGENTS.md` |
| Data pipeline (`data/`) | A | yes | yes | stable | yes | MDP001 — builds the cache CSVs |
| Coverage view (`mission_core/`) | A | yes | yes | stable | yes | MDP002 — trust-graded coverage |
| Planner UI (`app.py`, `pages/`) | B | partial | yes | n/a | yes | MDP004–MDP006 in progress |

## Update rules

- Lower a grade when verification fails or known debt grows.
- Raise a grade only with verification evidence.
- Prioritize cleanup on the lowest-scoring area with active work.
