# AGENTS.md — Medical Desert Planner

This is the **root agent entry point**. Keep it short. It is a routing layer, not
a binding document — detailed context lives in `docs/harness/`.

## What this repo is

A **Medical Desert Planner** (DAIS 2026, Track 2, for the Virtue Foundation): a
planner picks a **capability** (maternity, ICU, NICU, emergency, oncology,
trauma) and a **geography**, sees **regional coverage**, drills into the
**trust-scored facility records** behind any aggregate, and **saves a planning
scenario**. The product question is *where are the highest-risk gaps in care, and
how confident are we they are real?* — so the core discipline is separating a
**real care desert** from a **data-poor region**: facility free-text is treated
as claims to verify (graded high/medium/unverified, with cited evidence), never
as ground truth.

Stack: **Python + Streamlit** (`app.py`, `pages/`), a deterministic spine in
`mission_core/`, a build-time pipeline in `data/` (Unity Catalog Marketplace →
cache CSVs), and **Databricks** (Lakebase / Unity Catalog) as the deployed
backend with local CSV/SQLite fallback. The dashboard works with **no LLM**; an
optional agent panel adds free-form planning.

## Start here, in order

1. Read `docs/harness/context-map.md` — the index of every harness file and when to update it.
2. Read `docs/harness/constraints.md` — the durable rules (C0xx) you must obey.
3. Read `docs/harness/project-context.md` — the domain: components, data flow, build order.
4. Run `make agent-clock-in` — it prints the next feature and the closeout reminder.
5. Read `docs/harness/progress.md` — what the last session left behind.

## The loop

`clock-in → pick the next feature → build → verify → record evidence → teach to mastery → clock-out`.

Teaching is part of the loop for **every** feature — see the standing rule below
and `docs/harness/teaching-protocol.md`.

- A change is **not done** until external evidence confirms it. The author is not
  the only judge. See `docs/harness/verification.md`.
- Only `scripts/harness.py verify-feature <ID>` may move a feature to `passing`.
- Run `make harness-check` before you call anything finished. It must exit 0.
- After non-trivial edits, run the `autoreview` skill as a closeout code review.
- **Teach as you go.** For any non-trivial session, follow
  `docs/harness/teaching-protocol.md`: teach the human incrementally, confirm
  mastery at each step, and keep a running understanding checklist.

## Closeout

- Update `docs/harness/progress.md` (Current State / In Progress / Next Steps).
- Record any failure in `docs/harness/diagnostics.md` with its layer and fix.
- Run `make clock-out`.
