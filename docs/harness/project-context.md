# Medical Desert Planner — Domain Context

What we are building and the order to build it. This is the domain file
`AGENTS.md` routes to. Keep it current as architecture decisions land (and log
the decisions in `decisions.md`).

## Premise (the product question)

**Track 2 — Medical Desert Planner (DAIS 2026, for the Virtue Foundation).**
The question we answer: *where are the highest-risk gaps in care, and how
confident are we that those gaps are real?* A planner picks a **capability**
(maternity, ICU, NICU, emergency, oncology, trauma) and a **geography** (state /
district), sees **regional coverage**, drills into the **trust-scored facility
records** behind any aggregate, and **saves a planning scenario**.

The hard part is not the ranking — it is **distinguishing a real care desert
from a data-poor region**. Web-sourced facility data is noisy and rural-sparse,
so "0 facilities" is never assumed to mean "no care." Every claim is graded by
how well the facility's own text backs it up, and the UI always shows the
evidence and the uncertainty.

## Stack (Python-first, Databricks-native)

- **Python 3.11 + Streamlit (`app.py`, `pages/`)** — the planner UI. The
  deterministic dashboard works with **no LLM**; an optional agent panel adds
  free-form planning. Run locally with `streamlit run app.py`.
- **`mission_core/`** — the deterministic spine: `burden` (NFHS-5 scoring),
  `claims` (trust-grade facility free-text), `coverage` / `coverage_view`
  (trust-weighted coverage + gap classification), `cost`, `impact`, `chain`
  (cost-per-impact ranking), `data_access` (dual-backend reads + persistence).
- **`data/`** — the build-time pipeline: pull Unity Catalog Marketplace tables,
  point-in-polygon facilities → districts, trust-grade claims, aggregate to the
  cache CSVs the app reads. Stdlib + `shapely`/`requests`.
- **`agent/`** — provider-agnostic LLM client + tool-orchestrating copilot
  (degrades gracefully when the model is unreachable).
- **Databricks** — Unity Catalog Marketplace (source data), serverless SQL
  warehouse (ingestion), and **Lakebase** (managed Postgres) as the deployed
  read + persistence backend. Local dev falls back to cache CSVs + SQLite.
- **Harness tooling (`scripts/`)** stays Python stdlib-only (C006).

The loop layer (automations, maker/checker subagents, worktrees) is documented in
`loops.md`.

## Data sources

| Layer | Source | Reality |
| --- | --- | --- |
| Burden (demand) | NFHS-5 district health indicators (Marketplace) | Real — 706 districts × 109 indicators; suppressed values handled |
| Supply | Virtue Foundation facilities (FDR pipeline, Marketplace) | Real — ~10k web-extracted facilities; noisy, ~88% private, rural-sparse |
| Geography | geoBoundaries ADM2 district polygons | Open data; point-in-polygon join |
| Reachability | OpenRouteService (or straight-line fallback) | Pre-computed; staging-city → district only |

## Components

1. **Ingestion** (`data/ingest_marketplace.py`, `data/02_facility_text_ingest.py`)
   — pull NFHS-5 + facilities (incl. free-text) from Unity Catalog into cache CSVs.
2. **Geo-resolution** (`data/geo_resolve.py`) — point-in-polygon each facility
   into a district; reconcile polygon ↔ NFHS names; aggregate supply.
3. **Claim grading** (`mission_core/claims.py`) — treat facility free-text as
   *claims to verify*: high (claimed + corroborated), medium (claimed only),
   unverified (flag only — likely noise). Every grade cites the matched text.
4. **Coverage view** (`mission_core/coverage_view.py`) — trust-weighted supply +
   gap classification (confirmed_coverage / unverified_claims / no_claim_desert)
   + burden-aware desert score. The Track-2 primary aggregate.
5. **Mission planner** (`mission_core/chain.py`) — adds reachability + a
   transparent cost model → need-addressed-per-dollar ranking (staging set only).
6. **Persistence** (`mission_core/data_access.py`) — saved scenarios / notes,
   dual-backend (Lakebase Postgres or local SQLite).

## Data flow

```
Unity Catalog Marketplace ──ingest──> cache CSVs (facilities_text, nfhs5_districts)
                                          │
                  geoBoundaries polygons ─┤
                                          ▼
                          geo_resolve (point-in-polygon + claim grading)
                                          │
            district_capability.csv  ◄────┴────►  facility_claims.csv (evidence)
                     │                                     │
                     ▼                                     ▼
          coverage_view (trust-weighted gap)        drill-down (cited records)
                     │
                     ▼
            Streamlit planner UI  ──save──>  scenarios (Lakebase / SQLite)
```

## Build order (each becomes a feature in features.md with a verification command)

1. Harness gate green (done — H001).
2. Data pipeline: Marketplace → cache CSVs (done — MDP001).
3. Trust-graded coverage view + gap classification (done — MDP002).
4. Geography selector + regional coverage rollup (state/district/PIN).
5. Facility drill-down: trust-scored records + cited evidence behind an aggregate.
6. Save / load a planning scenario (Lakebase, with local SQLite fallback).
7. Data-quality passes: dedupe on `cluster_id`, surface public/private split.

## Evaluation metrics (build these alongside, not after)

| Metric | What it measures |
| --- | --- |
| Coverage classification accuracy | Confirmed vs unverified vs desert, spot-checked against cited text |
| Trust-grade precision | Share of `high` claims whose corroborating text truly supports the service |
| Geographic resolution rate | % of facilities resolved to a district by point-in-polygon |
| Burden completeness | Indicators available / suppressed per district |
| Latency | Time from selection to ranked coverage |
| Data-density confidence | Facility-data presence per district (real-desert vs data-poor signal) |
