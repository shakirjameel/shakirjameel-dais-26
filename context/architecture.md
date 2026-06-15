# Medical Mission Deployment Copilot — Architecture

> **Project context for Claude Code.** System architecture for an agentic medical-mission deployment copilot built on the Databricks hackathon (Virtue Foundation India healthcare dataset). Centerpiece: **cost-per-impact ranking** of Indian districts for a medical NGO. Build order is incremental (the "ladder") so there is a demoable, stable system after every rung. Read with `use_case.md` (the why) and `eval_set.md` (the tests).

---

## 0. Environment (read first)

- **Platform:** Databricks **Free Edition** (the hackathon's required, level-playing-field environment). Lakebase **is** available for the event (the generic public limitations page is stale for this config).
- **Recommended workflow (from the setup guide):** build and test **locally**, deploy to Free Edition **regularly**, deploy **early at least once**. Most dev is local; Free Edition hosts the Databricks-specific pieces (Lakebase, model serving, the deployed App).
- **App caps:** <=3 apps/account; each auto-stops 24h after deploy/update. **Deploy late, restart right before judging.**
- **Start from the hackathon template:** it's a prompt for the coding agent that scaffolds a Databricks App on Lakebase Postgres with the Marketplace dataset synced from Unity Catalog. **Adapt it; don't rebuild the scaffold.**

### Dual path that keeps us unblocked
| Component | Target (Free Edition / demo) | Local fallback (dev tonight) |
|---|---|---|
| Operational DB | **Lakebase** (Postgres) | **SQLite / local Postgres** behind a data-access module |
| Agent | Model serving (Agent Bricks or served custom agent) | Custom Python agent run inline / external model API key |
| Front-end | **Databricks App** | Same app code run locally |
| External calls (ORS) | Pre-computed + cached to a table | Pull once locally, cache to CSV/DB |

**Design rule:** all DB access behind ONE data-access module; all agent calls behind ONE agent-client module; all external calls (ORS) behind ONE connector module with a cache. Swapping local <-> Lakebase then touches one file. This is what makes "build locally tonight, deploy to Lakebase later" a config change, not a rewrite.

---

## 1. System overview

For a chosen intervention/specialty + constraints (team size, days, budget), the copilot:
1. Scores each Indian district's **burden** for that intervention (NFHS-5).
2. Computes the **coverage gap** (relevant facilities, by reachability not just count).
3. Computes **reachability** (road travel time/distance via ORS) from a staging point.
4. Computes a transparent **mission cost** (transport + stay + reach time-cost).
5. Ranks districts by **impact-per-dollar** (estimated people-reached / cost).
6. Generates a **cited mission brief** for the top district, flagging all uncertainty.

### Core principle (the anti-hallucination spine)
**The agent decides; it never computes or invents.** All numbers come from deterministic Python or DB rows and are handed to the LLM as grounded context. The LLM ranks among provided options, explains, and fills the brief template. Missing value -> "[unavailable/suppressed]", never a guess.

---

## 2. Data flow

```
  PROVIDED (Unity Catalog Marketplace -> synced to Lakebase)
  +-----------------+  +------------------------+  +----------------------+
  | facilities      |  | nfhs5_district_health  |  | india_post_pincode   |
  | (FDR pipeline)  |  | (706 x 109 indicators) |  | (165k, post-office)  |
  +--------+--------+  +-----------+------------+  +----------+-----------+
           |                       |                          |
           +-----------+-----------+--------------+-----------+
                       v                          v
              GEO RESOLUTION                EXTERNAL (cached)
              point-in-polygon              +------------------+
              facility coords ->            | OpenRouteService |
              district; PIN dedupe          | matrix: time/dist|
                       |                     +--------+---------+
                       v                              |
              +-------------------------------------------------+
              | GROUNDING + COST LAYER  (pure Python, no LLM)    |
              | burden_score / coverage_gap / reachability /    |
              | mission_cost / people_reached / impact_per_cost |
              +-----------------------+-------------------------+
                                      v
              +-------------------------------------------------+
              | AGENT  (decides over grounded facts)            |
              | 1 rank_districts  2 explain  3 generate_brief   |
              +-----------------------+-------------------------+
                                      v
              +-------------------------------------------------+
              | DATABRICKS APP                                  |
              | input panel | ranked list + reasoning |         |
              | cost breakdown | (map) | mission brief          |
              +-------------------------------------------------+
```

---

## 3. The grounding + cost layer (BUILD FIRST — Rung 0)

Pure Python, no LLM, fully unit-tested. Everything downstream depends on it. The eval set tests the agent on top of these; if these are wrong, all outputs are wrong.

```python
# geo.py ---------------------------------------------------------------
def assign_district(fac_lat, fac_lon, district_polygons) -> str | None:
    """Point-in-polygon. Returns district id or None if uncoded.
    Spatial join (NOT string-matching names — dataset warns names are unreliable)."""

def dedupe_pincode(pin_rows) -> rows:
    """India Post grain is post office, not PIN. Aggregate to the join grain
    BEFORE joining or rows fan out. Always check cardinality."""

# burden.py ------------------------------------------------------------
INTERVENTION_INDICATORS = {
    # intervention -> list of NFHS-5 columns that proxy its burden
    # FILLED IN once facilities schema + chosen specialty are known.
    # e.g. "maternal": ["anc_4plus_visits_pct", "institutional_delivery_pct", ...]
}
def burden_score(district_row, intervention) -> float:
    """Composite 0..1 from the intervention's indicators.
    Suppressed '*' -> None (NOT zero). Parenthesized estimates -> flag low_conf.
    Returns score + which indicators were missing (for honesty in output)."""

# coverage.py ----------------------------------------------------------
def coverage_gap(district, facilities, reachability, intervention) -> dict:
    """Relevant facilities weighted by reachability, not raw count.
    High burden + low reachable relevant supply = large gap."""

# cost.py  (THE CENTERPIECE) ------------------------------------------
COST_ASSUMPTIONS = {  # every value named, defensible, ADJUSTABLE
    "transport_per_km_usd": 0.0,      # set with real norm
    "per_diem_usd": 0.0,              # lodging+food per person/day
    "team_size_default": 0,
    "mission_days_default": 0,
    "surgeon_day_value_usd": 0.0,     # time-cost of a lost operating day
}
def mission_cost(distance_km, drive_hours, team_size, days, a=COST_ASSUMPTIONS) -> dict:
    """Returns {transport, stay, reach_time_cost, total} with the breakdown
    so the UI can show 'where the number came from'. No hidden math."""

# impact.py ------------------------------------------------------------
def people_reached(district_row, intervention, burden) -> dict:
    """Estimate from burden magnitude x district population (heuristic).
    Returns value + method string + confidence flag. NEVER unhedged."""

def impact_per_cost(people, cost_total) -> float:
    """The ranking metric. people_reached / total_cost."""
```

**Most error-prone parts:** (1) the spatial join + PIN dedupe (cardinality bugs fan out rows); (2) suppressed/low-confidence value handling (`*` must be NULL not 0); (3) the cost assumptions being defensible. Unit-test these before any agent work.

---

## 4. External connector (ORS) — Rung 0/1, cached

```python
# ors_client.py -- behind one module, results CACHED to a table/CSV
def reachability_matrix(origins, destinations, profile="driving-car") -> matrix:
    """ORS Matrix endpoint (free key). Many-to-many time+distance.
    Pre-compute for demo districts; NEVER call live during the demo.
    Label outputs 'estimated road travel time' (rural OSM data is approximate)."""
```
Rate-limited free tier -> pull once, cache. ORS Matrix supports up to 3500 origin x destination per request; demo scope is tiny.

---

## 5. Operational DB schema (Postgres-compatible; same on SQLite/Lakebase)

```sql
-- provided, synced (shapes confirmed at build from the Marketplace dataset)
-- facilities(...), nfhs5_district(...), india_post_pincode(...)

-- derived / app tables we create:
CREATE TABLE district_geo (        -- resolved district centroids/polygons + population
  district_id TEXT PRIMARY KEY, state TEXT, name TEXT,
  centroid_lat DOUBLE PRECISION, centroid_lon DOUBLE PRECISION, population BIGINT
);
CREATE TABLE reachability_cache (  -- ORS results, pre-computed
  origin_id TEXT, dest_facility_id TEXT,
  drive_minutes DOUBLE PRECISION, distance_km DOUBLE PRECISION, est BOOLEAN DEFAULT TRUE
);
CREATE TABLE nfhs6_state_trend (   -- state-level trajectory (NFHS5 vs NFHS6)
  state TEXT, indicator TEXT, nfhs5_value DOUBLE PRECISION,
  nfhs6_value DOUBLE PRECISION, direction TEXT  -- improving/worsening/flat
);
CREATE TABLE eval_scenarios (      -- fixtures double as rehearsal script
  scenario_id TEXT, inputs_json TEXT, expected_json TEXT, failure_caught TEXT
);
```

---

## 6. The agent (Rung 1)

### Outputs (each gradeable)
1. `ranked_districts`: ordered list with {district, impact_per_cost, burden, gap, cost_breakdown, confidence_flags}
2. `explanation`: why the top district ranks where it does, citing specific indicators
3. `mission_brief`: templated, slot-filled, every claim traceable, uncertainty flagged

### Hard rules (enforced)
- Ranking uses ONLY computed `impact_per_cost`; the LLM may not reorder on a hunch.
- Any district with suppressed key indicators is shown WITH a confidence flag, never silently dropped or filled.
- The brief contains only provided slot values; missing -> "[unavailable/suppressed]".
- Cost numbers always accompanied by their breakdown.

### Target vs fallback
- **Target:** served agent on Free Edition model serving (Agent Bricks if available, else a served custom agent).
- **Fallback (tonight):** custom Python agent, external model API key, run inline. Hidden behind the agent-client module.

### Keep the LLM surface tiny
The model receives "district X: burden 0.82 (indicators a,b,c; d suppressed), reachable relevant facilities 1 within 4h, cost $14.2k (breakdown...), people_reached ~3,400 (heuristic), impact/cost 0.24" and RANKS + EXPLAINS. It never computes the numbers.

---

## 7. The Databricks App (Rung 1-3)

- **Input panel:** intervention/specialty, team size, days, budget.
- **Ranked districts:** top-N by impact-per-cost with the 3-line reasoning each.
- **Cost breakdown:** expandable "where this number came from" (the defensibility beat).
- **Map (Rung 3, cuttable):** districts shaded by gap; reach lines to facilities.
- **Trajectory (Rung 3, cuttable):** state-level NFHS-6 improving/worsening tag.
- **Mission brief (Rung 2):** generated one-pager; downloadable.
- **Provenance panel:** the real/estimated/assumed ledger (show on "is the data real?").

---

## 8. Observability & eval (the management story)

- Known-answer `eval_scenarios` run through the agent: exact-match on ranking order for fixed inputs; rubric on brief (required slots present, no invented facts, uncertainty flagged).
- Designed-to-fail kept unfixed: a district with suppressed key indicators (must flag, not fabricate); a facility with no coordinates (must exclude with note, not guess a location).
- Decision trace: inputs -> computed metrics -> ranking -> explanation, all logged. "Why this district?" is auditable.
- Graceful degradation: stale/missing data labeled, never reasoned on as if complete.

---

## 9. Build order (the ladder — matches use_case.md S9)

0. **Foundation (Sun night):** workspace + template; confirm facilities schema; grounding + cost + impact functions in pure Python with unit tests; ORS pull+cache for a few demo districts. *Local SQLite ok.*
1. **Core agent flow (Mon AM):** one intervention end-to-end -> ranked top-N by impact-per-cost + reasoning. **Minimum winning demo.**
2. **Mission brief (Mon PM):** generate the cited one-pager for the top district.
3. **Visual + trend (Mon eve/Tue AM, CUTTABLE):** map + state trajectory.
4. **Polish + rehearse (Tue AM):** lock the 3-min flow, rehearse, prep Q&A.

Never start a rung until the previous is demoable and stable.

---

## 10. Open decisions to pin (don't let float)
- **Facilities schema** -> which intervention/specialty (inspect FIRST).
- **Cost assumptions** -> real-norm grounded defaults, labeled adjustable.
- **people_reached heuristic** -> method + caveat string.
- **Ranking tie-breaks** -> documented policy (eval tests it).
- **NFHS-6 district CSV availability** -> if absent, state-level trend only.
