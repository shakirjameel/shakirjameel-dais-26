# Knowledge Graph: Ontology & Roadmap

## Overview

The Mission Copilot's deterministic chain (burden → coverage gap → cost → impact-per-dollar → rank) is a forward-only pipeline. It answers one question well: "which district should we send this team to?" But it cannot answer lateral, temporal, or provenance questions without new Python code for each.

A knowledge graph wraps around the existing chain and unlocks:

- **Multi-hop reasoning** — the agent can ask questions the developer didn't anticipate ("which districts share high burden AND low public supply AND are within 3 hours of each other?")
- **Ad-hoc agent queries** — a single graph tool replaces many bespoke lookups
- **Provenance traversal** — every recommendation traces back through transformations to source rows
- **Temporal versioning** — NFHS-5 indicators coexist with NFHS-6; trend analysis becomes a query
- **Multi-mission planning** — sequencing teams across districts becomes a graph optimization problem

The chain still runs. The graph is additive — it makes the existing results queryable and extensible without rewriting mission_core.

---

## Ontology — Node Types

| Node | Key Attributes | Source |
|------|---------------|--------|
| **District** | name, state, normalized_key, lat, lon, population | NFHS-5 roster, Census/WorldPop (future) |
| **Facility** | name, type, operator_type (public/private), specialties[], lat, lon, cluster_id | Provided facilities dataset |
| **Intervention** | name, burden_indicators[], supply_column, cost_profile | mission_core config (INTERVENTION_INDICATORS, INTERVENTION_SUPPLY_COLUMN) |
| **Indicator** | name, value, confidence (high/low/suppressed), direction (high_is_worse/low_is_worse), raw_string | NFHS-5 district rows, parsed via parse_nfhs_value() |
| **StagingCity** | name, lat, lon | data_access.py STAGING constant |
| **CostAssumption** | coefficient_name, value, unit, source_label | cost.py CostAssumptions dataclass |
| **Mission** | intervention, team_size, days, date, staging_city, outcome | Future: Virtue Foundation operational records |
| **DataSource** | name, vintage, coverage_scope, known_risks[], resolution_method | DATA_RISKS.md, geo_resolve.py metadata |

---

## Ontology — Edge Types

| Edge | From → To | Properties |
|------|-----------|-----------|
| **HAS_BURDEN** | District → Indicator | score (0..1), confidence_level, indicators_used_count, missing_count, low_confidence_flags[] |
| **SUPPLIES** | Facility → District | resolved_by (point-in-polygon), specialty_match (intervention-specific), operator_type |
| **REACHABLE_FROM** | District → StagingCity | distance_km, drive_hours, source (ors/fallback/straight-line), computed_date |
| **ADDRESSES** | Intervention → Indicator | direction, normalization_method, weight (default 1.0) |
| **COSTS** | Mission → District | total_usd, transport_usd, stay_usd, reach_time_cost_usd, assumptions_used{} |
| **RANKED_AT** | District → Intervention | need_per_dollar, tier (confirmed/candidate), gap_score, supply_adequacy, data_confidence |
| **DERIVED_FROM** | Any node → DataSource | transform_step, transform_module, timestamp, row_id |
| **ADJACENT_TO** | District → District | shared_border (bool), same_state (bool), road_distance_km (optional) |
| **PREVIOUSLY_SERVED** | Mission → District | outcome_summary, year, team_feedback |

---

## What This Enables

### 1. Multi-Hop Reasoning

The agent can answer questions that span multiple entity types and relationships without custom code:

```
Find districts where:
  - burden score > 0.7 for maternal_health (HAS_BURDEN)
  - AND drive_hours < 4 from Patna (REACHABLE_FROM)
  - AND no public-sector facility supplies them (SUPPLIES, operator_type != 'public')
  - AND they are adjacent to a district we've previously served (ADJACENT_TO + PREVIOUSLY_SERVED)
Order by: burden score descending
```

Today this requires writing a new Python function. With the graph, it's a single traversal pattern the agent can compose at runtime.

### 2. Explainability as Graph Traversal

Every recommendation is a subgraph, not a number:

```
Recommendation: "Deploy to Gaya"
  → RANKED_AT (need_per_dollar: 0.00034, tier: confirmed)
    → HAS_BURDEN (score: 0.78, indicators: [institutional_birth: 62%, anaemia: 71%])
      → DERIVED_FROM (NFHS-5, row 234, parsed via parse_nfhs_value)
    → REACHABLE_FROM (142 km, 3.1 hours, source: ORS)
      → DERIVED_FROM (reachability_precompute.py, ORS API, 2026-06-10)
    → COSTS (total: $4,230, breakdown: transport $99, stay $2,520, reach-time $1,611)
      → CostAssumption (surgeon_day_value: $800, source: "VF estimate")
```

The mission brief becomes a rendered subgraph. Every claim has a traceable path.

### 3. Sensitivity as Edge-Weight Perturbation

Instead of sweeping coefficients in a Python loop (sensitivity.py), perturb edge weights on COSTS relationships and observe which RANKED_AT edges flip position. The graph makes sensitivity analysis a structural operation — "which edges, if changed by ±30%, cause a different district to rank #1?"

### 4. Temporal Versioning

Edges carry `valid_from` / `valid_to` properties:

- NFHS-5 indicators: `{valid_from: "2019", valid_to: "2021"}`
- NFHS-6 state-level (when available): `{valid_from: "2023", valid_to: "2025"}`
- Reachability: `{computed_date: "2026-06-10"}` (roads change)

Trend analysis becomes: traverse all HAS_BURDEN edges for a district, ordered by valid_from. No schema migration needed — just new edges alongside old ones.

### 5. Data Lineage as First-Class Citizen

Every DERIVED_FROM edge carries the transform step:

```
District "Gaya" → DERIVED_FROM → DataSource "NFHS-5"
  properties: {transform_step: "parse_nfhs_value", module: "mission_core/burden.py", row: 234}

Facility "XYZ Clinic" → DERIVED_FROM → DataSource "Provided Facilities"
  properties: {transform_step: "point_in_polygon", module: "data/geo_resolve.py", resolved_to: "Gaya"}
```

"Why does this district show 0 maternal facilities?" → traverse DERIVED_FROM → see it's a data gap (Risk R2 — no facilities resolved to this polygon), not a confirmed desert.

### 6. Multi-Mission Optimization

With PREVIOUSLY_SERVED edges and ADJACENT_TO topology:

```
Given 3 teams over 2 months:
  - Find districts with high RANKED_AT.need_per_dollar
  - Exclude districts with PREVIOUSLY_SERVED in 2026
  - Prefer clusters of ADJACENT_TO high-burden districts (one staging serves multiple)
  - Minimize total REACHABLE_FROM.drive_hours across all deployments
  - Account for diminishing returns (re-visiting the same district)
```

This is a graph optimization problem (covering set + routing) that the linear chain cannot express.

---

## Migration Path

### Phase 1: Model the Existing Chain as a Graph

**No new data required.** Convert what already exists:

- `district_base.csv` rows → District nodes + Indicator nodes + HAS_BURDEN edges + SUPPLIES edges (from aggregated facility counts)
- `reachability_patna.csv` → REACHABLE_FROM edges
- `INTERVENTION_INDICATORS` config → Intervention nodes + ADDRESSES edges
- `CostAssumptions` defaults → CostAssumption nodes
- `STAGING` constant → StagingCity node

The chain (`mission_core/chain.py`) still runs as-is. Its outputs are *also* written as RANKED_AT edges. The graph is a parallel view, not a replacement.

**Touches:** `data_access.py` (graph write path), new `mission_core/graph.py` (schema + load)

### Phase 2: Add the Agent Graph Tool

A 6th tool for the agent: `query_knowledge_graph(pattern)`.

Start with pre-canned query patterns (safe, bounded):
- "districts with high burden and low supply for intervention X"
- "facilities near district Y by specialty"
- "data lineage for recommendation Z"

Later: open to structured query language (Cypher, Gremlin, or SPARQL subset) with guardrails.

**Touches:** `agent/tools.py` (new tool), `agent/prompts.py` (tool description in system prompt)

### Phase 3: Add Temporal + Lineage Edges

- NFHS-6 state-level trend data → new HAS_BURDEN edges with `valid_from: 2023`
- Data source provenance → DERIVED_FROM edges on all nodes
- Population data (Census/WorldPop) → population attribute on District nodes
- District adjacency (from ADM2 polygon topology) → ADJACENT_TO edges

**Touches:** `data/geo_resolve.py` (adjacency extraction), new ingest scripts for NFHS-6/Census

### Phase 4: Graph-Native Reasoning

Replace the linear `rank_districts()` chain with graph traversal + scoring. The existing chain becomes one traversal pattern ("rank by need-per-dollar from staging city X"), but the graph supports arbitrary patterns:

- Cluster-based deployment (serve adjacent districts in one trip)
- Equity-weighted ranking (prioritize districts never previously served)
- Trend-aware ranking (burden worsening faster = higher urgency)
- Multi-intervention bundling (same team, multiple interventions, one district)

The chain doesn't disappear — it's the default traversal. The graph makes other traversals possible without new code.

**Touches:** `mission_core/chain.py` (refactor to graph query), `agent/orchestrator.py` (richer tool results)

---

## What's Needed to Build This

| Need | Current State | To Add |
|------|--------------|--------|
| Graph schema definition | This document | Formalize as code (node/edge type classes or JSON-LD) |
| ETL: existing data → graph | `geo_resolve.py` outputs flat CSV | Graph writer that creates nodes + edges from district_base.csv + reachability.csv |
| Graph query layer | None | `mission_core/graph.py` — query interface (backend-agnostic, like data_access.py) |
| Agent graph tool | 5 tools in `agent/tools.py` | 6th tool: `query_knowledge_graph(pattern)` with pre-canned patterns |
| Population nodes | Not in provided dataset | Census/WorldPop ingest → District.population attribute |
| Adjacency edges | ADM2 polygons exist in `data/cache/` | Compute shared borders from polygon topology → ADJACENT_TO edges |
| Temporal edges | NFHS-5 only, no valid_from/to | Add temporal properties to HAS_BURDEN edges; ingest NFHS-6 when available |
| Outcome edges | No mission history | Integrate VF operational records → Mission nodes + PREVIOUSLY_SERVED edges |

---

## Relationship to Existing Code

The knowledge graph does not replace any existing module. It wraps around them:

| Existing Module | Graph Role |
|----------------|-----------|
| `mission_core/burden.py` | Produces HAS_BURDEN edges (score, confidence, indicators) |
| `mission_core/coverage.py` | Produces RANKED_AT.gap_score and .supply_adequacy |
| `mission_core/cost.py` | Produces COSTS edges (total, breakdown, assumptions) |
| `mission_core/impact.py` | Produces RANKED_AT.need_per_dollar |
| `mission_core/chain.py` | Orchestrates the above → writes RANKED_AT edges for all districts |
| `mission_core/data_access.py` | Reads graph (or falls back to CSV/Lakebase as today) |
| `data/geo_resolve.py` | Produces SUPPLIES edges + DERIVED_FROM provenance |
| `data/reachability_precompute.py` | Produces REACHABLE_FROM edges |
| `agent/tools.py` | Reads graph via query patterns (Phase 2+) |

The chain still computes. The graph stores and connects what it computes.

---

## Live Catalog Queries — Populating Graph Nodes & Edges

Connection verified against:
- **Catalog:** `databricks_virtue_foundation_dataset_dais_2026`
- **Schema:** `virtue_foundation_dataset`
- **Warehouse:** Serverless Starter Warehouse (`248996ee378e4a9d`)

### District Nodes (from NFHS-5)

```sql
-- 706 District nodes with identity + key burden indicators
SELECT
    TRIM(district_name) AS name,
    state_ut AS state,
    institutional_birth_5y_pct,
    all_w15_49_who_are_anaemic_pct,
    births_attended_by_skilled_hp_5y_10_pct,
    mothers_who_had_at_least_4_anc_visits_lb5y_pct,
    child_u5_who_are_stunted_height_for_age_18_pct
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators
```

### Facility Nodes (with coordinate + specialty filtering)

```sql
-- Valid India facilities (exclude coord junk + out-of-bounds)
SELECT
    unique_id,
    name,
    CASE
        WHEN operatorTypeId IN ('private', 'public', 'government') THEN operatorTypeId
        ELSE 'unknown'
    END AS operator_type,
    specialties,
    latitude,
    longitude,
    cluster_id,
    address_city,
    address_stateOrRegion AS state
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
WHERE latitude IS NOT NULL
  AND longitude IS NOT NULL
  AND latitude BETWEEN 6.0 AND 38.0
  AND longitude BETWEEN 68.0 AND 98.0
```

### SUPPLIES Edges (Facility → District via specialty match)

```sql
-- Maternal health supply: facilities with ob/gyn specialty per district
-- (requires point-in-polygon resolution from geo_resolve.py, then join)
SELECT
    f.unique_id AS facility_id,
    f.name AS facility_name,
    TRIM(n.district_name) AS district_name,
    n.state_ut AS state,
    'maternal_health' AS intervention,
    f.operatorTypeId AS operator_type
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities f
JOIN databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators n
  ON LOWER(TRIM(f.address_city)) = LOWER(TRIM(n.district_name))
WHERE f.specialties LIKE '%gynecologyAndObstetrics%'
  AND f.latitude BETWEEN 6.0 AND 38.0
-- NOTE: This is a name-match approximation. Production uses point-in-polygon
-- from geo_resolve.py for accurate spatial assignment (99.98% resolution).
```

### HAS_BURDEN Edges (District → Indicator for maternal_health)

```sql
-- Burden indicators for maternal_health intervention
-- Each row produces multiple HAS_BURDEN edges (one per indicator)
SELECT
    TRIM(district_name) AS district,
    state_ut AS state,
    'institutional_birth_5y_pct' AS indicator_name,
    institutional_birth_5y_pct AS value,
    CASE
        WHEN institutional_birth_5y_pct IS NULL THEN 'suppressed'
        ELSE 'high'
    END AS confidence,
    'high_is_worse' AS direction
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators
WHERE state_ut IN ('Bihar', 'Jharkhand')

UNION ALL

SELECT
    TRIM(district_name),
    state_ut,
    'all_w15_49_who_are_anaemic_pct',
    all_w15_49_who_are_anaemic_pct,
    CASE
        WHEN all_w15_49_who_are_anaemic_pct IS NULL THEN 'suppressed'
        ELSE 'high'
    END,
    'high_is_worse'
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators
WHERE state_ut IN ('Bihar', 'Jharkhand')

UNION ALL

SELECT
    TRIM(district_name),
    state_ut,
    'mothers_who_had_at_least_4_anc_visits_lb5y_pct',
    CAST(mothers_who_had_at_least_4_anc_visits_lb5y_pct AS DOUBLE),
    CASE
        WHEN mothers_who_had_at_least_4_anc_visits_lb5y_pct IS NULL THEN 'suppressed'
        WHEN mothers_who_had_at_least_4_anc_visits_lb5y_pct LIKE '(%' THEN 'low'
        WHEN mothers_who_had_at_least_4_anc_visits_lb5y_pct = '*' THEN 'suppressed'
        ELSE 'high'
    END,
    'low_is_worse'
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators
WHERE state_ut IN ('Bihar', 'Jharkhand')
```

### Candidate Graph Queries (what the agent can ask)

```sql
-- Multi-hop: High burden + low public supply + reachable
-- (Once graph is materialized, this becomes a traversal pattern)
WITH burden AS (
    SELECT
        TRIM(district_name) AS district,
        state_ut,
        all_w15_49_who_are_anaemic_pct AS anaemia_burden
    FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators
    WHERE state_ut IN ('Bihar', 'Jharkhand')
      AND all_w15_49_who_are_anaemic_pct > 60
),
supply AS (
    SELECT
        LOWER(TRIM(address_city)) AS district_key,
        COUNT(*) AS total_facilities,
        SUM(CASE WHEN operatorTypeId = 'public' THEN 1 ELSE 0 END) AS public_facilities
    FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
    WHERE latitude BETWEEN 6.0 AND 38.0
      AND address_stateOrRegion IN ('Bihar', 'Jharkhand')
    GROUP BY LOWER(TRIM(address_city))
)
SELECT
    b.district,
    b.state_ut,
    b.anaemia_burden,
    COALESCE(s.total_facilities, 0) AS total_facilities,
    COALESCE(s.public_facilities, 0) AS public_facilities
FROM burden b
LEFT JOIN supply s ON LOWER(b.district) = s.district_key
WHERE COALESCE(s.public_facilities, 0) = 0
ORDER BY b.anaemia_burden DESC
```

### Data Quality Awareness (R3: field-bleed in operatorTypeId)

```sql
-- Clean operator classification (handles known field-bleed)
SELECT
    operatorTypeId,
    COUNT(*) AS cnt,
    CASE
        WHEN operatorTypeId IN ('private', 'public', 'government') THEN 'valid'
        WHEN operatorTypeId IS NULL OR operatorTypeId = 'null' THEN 'null'
        ELSE 'field_bleed_junk'
    END AS quality_flag
FROM databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.facilities
GROUP BY operatorTypeId
ORDER BY cnt DESC
```

### Connection Template (Python)

```python
from databricks import sql

conn = sql.connect(
    server_hostname='dbc-2f9d7b87-5aa9.cloud.databricks.com',
    http_path='/sql/1.0/warehouses/248996ee378e4a9d',
    access_token='<YOUR_PAT>'  # never commit
)
cursor = conn.cursor()
cursor.execute("SELECT ...")
rows = cursor.fetchall()
cursor.close()
conn.close()
```
