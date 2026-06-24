# Diagnostics

Turn failure into targeted harness improvements. Classify each failure by layer,
then record it so the same failure does not recur silently.

## Layer taxonomy

| Layer | Failure signal | Typical fix |
| --- | --- | --- |
| Context | Agent acts on stale/missing instruction | Update the smallest owning file; tighten `context-map.md` |
| Tooling | A check was optional and got skipped | Make it executable in `harness.py` / `Makefile` |
| Environment | Setup not reproducible across machines | Fix `startup-readiness.md` start commands |
| State | Work lost across sessions | Update `progress.md` / `decisions.md` discipline |
| Feedback | A regression shipped undetected | Add a feature verification or eval |
| Cleanup | Stale temp artifacts left behind | Record + remove; add to clock-out checklist |

## Failure records

| Date | Task | Symptom | Layer | Corrective action | Evidence |
| --- | --- | --- | --- | --- | --- |
| 2026-06-04 | Harness setup | (none yet) | — | — | `make harness-check` exits 0 |
| 2026-06-05 | code-review-graph install | Default `python3` was 3.9 and Homebrew Python rejected direct user install under PEP 668 | Environment | Installed code-review-graph in an isolated Python 3.13 venv under `~/.local/share/code-review-graph-venv` and symlinked `~/.local/bin/code-review-graph` | `code-review-graph --version` reports 2.3.5; `code-review-graph status --repo .` reports 40 nodes / 236 edges |
