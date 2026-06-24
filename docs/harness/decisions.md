# Decisions

Dated log of non-obvious design decisions. Record the decision, the reason, and
the alternative rejected — so a future session does not re-litigate it.

| Date | Decision | Why | Rejected alternative |
| --- | --- | --- | --- |
| 2026-06-04 | Markdown "brain" in `docs/harness/` gated by a Python CLI | Durable, diffable context that survives sessions; the gate makes checks executable instead of optional | Keeping context only in chat / a fat AGENTS.md |
| 2026-06-04 | Harness tooling is Python stdlib only | Zero install friction; the gate must run anywhere before app deps exist | Pulling in pytest/click for the harness itself |
| 2026-06-04 | `AGENTS.md` is a short routing layer; `CLAUDE.md` redirects to it | One source of truth across Claude / Codex / Cursor | Maintaining parallel instruction files |
| 2026-06-04 | Feature `passing` only via `verify-feature` | Completion needs external evidence, not author self-judgement | Manually editing state cells |
| 2026-06-15 | Track 2 framing: separate a **real care desert** from a **data-poor region** | The judging question is confidence, not just ranking; web-sourced facility data is noisy and rural-sparse | Ranking districts on raw facility counts (overstates coverage where data is rich) |
| 2026-06-15 | Treat facility free-text as **claims to verify**, graded high/medium/unverified | "0 facilities" ≠ confirmed desert; transparent keyword corroboration lets the UI cite evidence and a planner overrule it | Trusting the structured `maternal_supply` flag as ground truth |
| 2026-06-15 | Trust-weighted supply (`high·1.0 + medium·0.6`, unverified off by default) | Verified evidence should outweigh an uncorroborated claim | Counting every claimed facility equally |
| 2026-06-15 | Coverage view uses burden only for **maternity** | Maternity is the only capability with an NFHS-5 demand indicator; others rank on supply scarcity alone (labelled) | Fabricating demand signals for ICU/NICU/etc. |
| 2026-06-15 | Build-time pipeline → cache CSVs the app reads (dual-backend: Lakebase or local CSV/SQLite) | Live demo never makes network calls; same code path works locally and deployed | Querying Unity Catalog / ORS live from the app |
| 2026-06-15 | No absolute "people reached" estimate | The dataset has no district population denominator; refuse to fabricate it | Heuristic population × gap as if it were measured |
| 2026-06-15 | Stack is Python + Streamlit + Databricks (Lakebase / Unity Catalog) | Matches the dataset's home and the deterministic-spine design; one language for pipeline + app | A TypeScript/Next app surface (no benefit for a data-grounded planner) |
| 2026-06-15 | Imported the context-harness from `~/Desktop/prairie` and retargeted it to this project | Reuse the proven Markdown-brain + gate + loop scaffolding instead of rebuilding it | Authoring a fresh harness, or working without one |
