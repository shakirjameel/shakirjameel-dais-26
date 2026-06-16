# Medical Desert Planner — Stress-Test & Verification Report

**Scope:** audit-only (no app/logic code changed). Every analytical assumption, every filter, and
every served number was stress-tested; the served data was reconciled against the **live Databricks
source** end-to-end.
**Date:** 2026-06-16 · **Repro:** `./.venv/bin/python tests/audit/audit_offline.py` (logic, offline)
and `./.venv/bin/python tests/audit/live_recon.py` (live source reconciliation).

---

## 1. Executive summary

> **Update 2026-06-16:** the two ranking defects are **FIXED**. **F1** (Patna cost non-monotonic):
> travel time is now modelled uniformly as distance ÷ 45 km/h for every origin (measured ORS distance
> retained; provenance flagged on the UI) → **0 cost inversions**. **F2** (`auto_days` reordering):
> ranking is now on a fixed mission length, with auto-days as a readout only → ranking identical on/off.
> Both guarded by regression tests; suite **53/53** green. Details in §3.

> **Update 2026-06-16 (data-gap visibility):** the optimizer no longer hides the 160 zero-facility
> districts behind a bare "excluded" count. They're now surfaced as a **"🔍 highest-need data gaps"**
> list — NFHS-surveyed (so populated + measured need) districts with no facility records, ranked by
> need (Mon/Tuensang/Zunheboto, Nagaland at the top), plus a "measured need, no facility data" KPI
> (state + national) and a sharpened `no_data` legend. Honest framing: *care desert OR scrape gap —
> investigate*, never a deployment target. India Post PIN was evaluated as a settlement denominator and
> **rejected as redundant** (NFHS need already proves population). Additive only — F1/F2 invariants
> re-asserted; suite **56/56**.

**Overall: demo-ready. Both ranking defects (F1, F2) are resolved; remaining items are honest caveats to script.**

- **Data integrity is excellent.** The claims→coverage aggregation reconciles **4,164 / 4,164 rows
  exactly** against the per-facility claims; NFHS-5 values match the live source to the decimal;
  winsorization, the India bbox filter, and the `verified_supply = high+medium` invariant all hold.
  Nothing on screen is fabricated — every number traces to source.
- **The capability logic is sound and honestly graded.** All NFHS demand-proxy columns exist in the
  live schema, directions are clinically correct, and the two capabilities with no proxy
  (emergency, trauma) correctly fall back to scarcity-only with an explicit flag.
- **One real defect:** the **cost model is non-monotonic in distance for the Patna origin** — a
  closer district can cost *more* than a farther one, and a nationwide Patna ranking silently mixes
  two different distance methods. This is the single thing a judge could poke. Fix before the demo.

### Findings table

| ID | Title | Severity | Verdict | One-line fix |
|----|-------|:--------:|:-------:|--------------|
| **F1** | Patna cost non-monotonic in distance + mixes ORS/haversine in one ranking | **HIGH** | **✅ FIXED** | Done: `drive_hours = distance_km/45` uniformly for all origins; ORS distance kept + provenance flagged on UI. |
| F2 | `auto_days` reorders/compresses the optimizer ranking | LOW–MED | **✅ FIXED** | Done: ranking is on a fixed mission length; auto-days is now a duration/cost readout only. |
| F5 | Only **22 facilities nationwide** report `acceptsVolunteers=true` | MED | WARN | Keep the 🤝 badge but never headline it; frame as "where known". |
| F6 | 11 duplicate `unique_id` rows carried through (no dedup) | LOW | WARN | Dedup on `unique_id`/`cluster_id` before counting supply (~0.1% effect). |
| F7 | 14 districts with facilities have no centroid → invisible to the optimizer for non-Patna origins | LOW | WARN | Backfill those centroids, or note them in `excluded_data_gaps`. |
| F3 | `context/` holds a stale duplicate prototype of the logic | LOW | WARN | Delete or move to `archive/`; it is not imported by the app. |
| F4 | No population denominator (need is relative) | LOW | INFO | Already disclosed; keep "days to meet demand" labeled relative. |
| F8 | Minor doc drift (9,964 not 9,965 facilities; 706 not 707 NFHS rows) | LOW | INFO | Update prose counts. |

**Logic audit tally (after F1+F2 fixes):** 23 PASS · 0 WARN · 0 FAIL · 10 INFO. **Live recon:** all
integrity checks PASS. **Test suite:** 53/53 (40 core + 13 agent).

---

## 2. Track A — Capability → demand logic  ✅ PASS

For each capability the NFHS-5 demand proxy was checked for column existence (live), direction
correctness, and honest fallback.

| Capability | Proxy indicators (NFHS-5) | Direction | Verdict |
|---|---|---|---|
| maternity | institutional birth, ANC-4 visits, skilled birth attendant, women anaemia | first 3 `low_is_worse`, anaemia `high_is_worse` | ✅ clinically correct |
| oncology | cervical / breast / oral cancer screening coverage | all `low_is_worse` (low screening = high unmet need) | ✅ defensible |
| icu | hypertension (W+M), high blood-sugar (W+M) | all `high_is_worse` (NCD prevalence = critical-care demand) | ✅ defensible |
| nicu | institutional birth, child underweight, child wasted | mixed | ⚠️ labeled **weak proxy** in code (`DEMAND_NOTE`) — honest |
| emergency | — none — | — | ✅ `demand_available=False`, ranked by scarcity |
| trauma | — none — | — | ✅ `demand_available=False`, ranked by scarcity |

- **Column existence (live):** all 13 distinct proxy columns are present in the live
  `nfhs_5_district_health_indicators` schema (Track E confirmed).
- **Normalization:** `_normalize(80,"high_is_worse")=0.8`, `(80,"low_is_worse")=0.2` — correct 0–100→0–1
  scaling with inversion. **No double-counting** (mean of available contributions).
- **Suppression honesty:** `*`/`NA`/blank → missing (never 0); `(29.5)` → low-confidence flag; all 6
  parse cases correct.
- **Live compute:** 38/38 Bihar districts produce a maternity demand score (e.g. Kishanganj = 0.571).

**Defensibility note for judges:** every demand number is an NFHS-5 indicator (a published GoI survey),
not an invention. The only modeling choices are (a) which indicators proxy which capability and
(b) equal weighting — both are stated in code and adjustable.

---

## 3. Track B — Cost / distance / capacity logic

> ### ✅ F1 RESOLVED (2026-06-16)
> **Fix shipped:** `mission_core/reach.py` now models travel time as `distance_km / AVG_SPEED_KMH`
> for **every** origin, including Patna's ORS rows. The measured ORS road *distance* is retained
> (more accurate than straight-line); only the independent ORS drive-*time* — the thing that let a
> nearer district cost more — is dropped. Cost is therefore a monotone function of distance and the
> cost basis is identical across home bases. The UI now flags each district's distance provenance
> ("measured road distance" vs "straight-line estimate") plus a one-line note that travel time =
> distance ÷ 45 km/h. **Verified:** Patna **0 inversions** (was 30); origin still baselines cost
> (Delhi costs more than Patna for 97% of shared Bihar districts; e.g. Samastipur $5,192 vs $34,945);
> regression test `test_mission_cost_is_monotonic_in_distance_for_every_origin` added; **52/52 tests**.
> The original analysis is retained below for the record.

### F1 (HIGH — original finding) — Cost is not monotonic in distance for the Patna origin
Cost decomposes (team=6, 7 days) to:
`cost = 0.70·distance_km + 60·team·days + 200·drive_hours·team` → `0.70·distance_km + 2,520 + 1,200·drive_hours`.

- **Every origin except Patna** computes `drive_hours = distance_km / 45` (haversine × road-factor),
  so cost is strictly increasing in distance. **Delhi → Bihar: 29 routable districts, 0 inversions ✅.**
- **Patna** reads `distance_km` and `drive_hours` *independently* from the ORS road matrix, and the
  `reach_time_cost` term (≈$1,200/hr) dominates (54–71% of total). So drive-time, not distance, drives
  cost. **Patna → Bihar: 30 inversion pairs.** Concrete:
  - *Saran 71.4 km → $4,010* costs **more** than *Bhojpur 72.1 km → $3,902* (farther, cheaper).
  - Widest margin: **Kishanganj (378 km, 4.58 h, $8,280) is 190 km farther than Munger (188 km,
    4.71 h, $8,304) yet $23 cheaper** — because its ORS drive-time is fractionally lower.
- **Method-mixing (nationwide Patna):** a single all-India Patna ranking contains **41 "ORS road"
  rows and 493 "straight-line est." rows** — two non-comparable distance methods in one sorted list.
  The Delhi↔Patna demo toggle therefore compares costs computed by different methods.

**Why it matters:** the explicit product promise is "baseline cost on where the team is based" and the
intuitive contract "closer = cheaper". For Patna (the default origin) that contract breaks, and a
judge toggling origins sees non-comparable dollars.

**Recommended fix (pick one):**
1. **Uniform method (recommended for demo):** compute `drive_hours = distance_km / AVG_SPEED_KMH` for
   *all* origins including Patna. Keeps cost monotonic and origin-comparable; optionally still display
   the ORS road distance/time as an annotation. ~3 lines in `mission_core/reach.py`.
2. **Keep ORS, fix the cost term:** make `reach_time_cost` a function of `distance_km` (not raw
   `drive_hours`) so cost is monotonic while ORS distance is retained.
   In either case, **never mix methods in one ranking** — if ORS covers only part of the geography,
   either use it for all rows or none.

### ✅ F2 RESOLVED (2026-06-16) — `auto_days` no longer reorders the ranking
**Original finding:** with auto-days, `days ∝ need` and stay-cost dominates, pushing `need/cost`
toward constant; measured top-5 reordered at #4/#5 (Sitamarhi↔Gopalganj) and impact spread compressed
(13 → 11). The highest-need districts lost ranking advantage exactly when auto-days was on — because
the variable mission length fed back into the ranking's cost denominator.
**Fix shipped:** `coverage_view.optimize()` now ranks every district at a **fixed mission length** (the
`days` value) — a `rank_cost` independent of auto-days — so impact-per-dollar is a fair comparison.
`auto_days` is now a **duration/cost readout**: it sets the *displayed* `cost_total_usd` / `days_used`
(the actual mission you'd run) but never the rank order or impact scores. A UI caption explains this
when the toggle is on. **Verified:** top-5 and all impact scores are now identical with auto-days on
vs off; regression test `test_auto_days_is_a_readout_and_never_reorders_ranking` added.

### Cost-term dominance (INFO)
| Mission | total | transport | stay | reach-time | reach-time share |
|---|---|---|---|---|---|
| 100 km / 2.5 h | $5,590 | $70 | $2,520 | $3,000 | 54% |
| 100 km / 4 h (bad road) | $7,390 | $70 | $2,520 | $4,800 | 65% |
| 250 km / 5.5 h | $9,295 | $175 | $2,520 | $6,600 | 71% |

`reach_time_cost` is the dominant term — intended (lost operating days are the real cost driver), but
it is why F1 bites. Worth a one-line tooltip so a judge isn't surprised the "value of travel time"
outweighs fuel.

---

## 4. Track C — Coverage / desert / map logic  ✅ PASS

- **Hand re-derivation matches code** (Katihar, maternity): high=0/med=1/unv=0 → `trust_weighted_supply
  = 0.6` ✓, `supply_adequacy = 0.6/(0.6+3) = 0.1667` ✓, demand = 0.531, `desert = 0.531·(1−0.1667) =
  0.4425` ✓ — identical to served.
- **`gap_classification` consistent across all districts** (0 mismatches).
- **Optimizer exclusion rule works:** all-India run excludes **160 no-data/no-route districts**, ranks
  534, and **0 ranked rows** have zero facilities or null distance; top impact = 100.
- **`no_data` never scores as covered:** all 3 no_data states have exactly 0 facilities.

---

## 5. Track D — Filters  ✅ PASS

| Filter | Source of options | Check | Verdict |
|---|---|---|---|
| Country | hardcoded `["India"]`, locked | dataset is India-only | ✅ |
| Capability | `claims.CAPABILITIES` (6) | matches classifier vocab | ✅ |
| State / UT | `list_states()` (data-driven, only "lit" states) | 35 data states, **all map to a GeoJSON `st_nm`** (0 unmapped), and **all round-trip back** | ✅ |
| Map hover | GeoJSON `st_nm` | all 36 topology states bound in `state_rollup` (regression-guards the old wrong-name bug) | ✅ |
| Count-unverified toggle | — | off `=high·1+med·0.6`, on `+unv·0.3` (verified: 3.8 / 5.3) | ✅ |
| Optimizer origin | `geo_names.ORIGINS` (34) | default = Patna | ✅ (but see F1) |
| Team / days / throughput / auto-days | sliders | reach the documented `optimize()` / `mission_cost()` args | ✅ |

District-table column filters (district text, coverage multiselect, verified/unverified/facilities/
trust-ratio/desert-score sliders) derive their ranges from the in-state row maxima and apply via
`_passes(r)` — validated by code-reading; ranges are data-driven, not hardcoded.

---

## 6. Track E — Live source reconciliation  ✅ PASS (with caveats F5–F7)

Re-queried the live source (`databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset`)
and compared to the served CSVs.

**E1 — facilities**
- Live raw rows **10,088**; distinct `unique_id` **10,077** → **11 duplicate IDs** (F6).
- Live in-India (coords + bbox) **9,964** == served `facilities_text.csv` **9,964** → **MATCH**.
- Winsorization verified: raw max capacity **200,000**; 1 row >5,000 live; served rows >5,000 = **0**.
- **`acceptsVolunteers=true` = 22 rows in the entire source** (F5) — the volunteer signal is extremely
  sparse; the 🤝 badge is truthful but must not be headlined.

**E2 — NFHS-5**
- Live **706 rows × 109 cols** == served (note prose elsewhere says 707/9,965 — F8 doc drift).
- All 13 burden-proxy columns present live. Spot-check of `institutional_birth_5y_pct` across 5
  districts (Nicobars, Chirang, Hamirpur, South West Garo Hills, Bijnor) matches served to the decimal.

**E3 — aggregation (the part most prone to bugs)**
- `facility_claims.csv` → `district_capability.csv`: **4,164 / 4,164 rows reconcile exactly** (0
  mismatches). `verified_supply = high + medium` holds for **4,164 / 4,164** rows. This is the strongest
  single result — the coverage aggregate is provably faithful to the graded claims.

**E4 — centroids / join**
- All **680/680** centroids inside the India bbox.
- **14** district×capability districts have facilities but no centroid (F7) → unroutable for
  centroid-based (non-Patna) origins, so excluded from the optimizer for those origins. Consistent
  with the exclusion rule but a small blind spot; backfilling those 14 centroids would close it.

---

## 7. Track F — Cross-cutting integrity

- **Single source of truth ✅:** `app.py`, `mission_core/`, and `agent/` contain **no `import context`**
  — the `context/` folder (`burden.py`, `cost.py`, `demo_chain.py`, `test_core.py`) is a **stale
  prototype** (F3) that duplicates and has drifted from the live logic. Recommend delete/archive so no
  one verifies against the wrong file.
- **Agent vs UI parity ✅:** `agent/tools.py` calls `coverage_view.optimize` — no divergent second
  implementation of the metric.
- **Test gaps:** the 51 tests don't currently cover cost-monotonicity or the auto-days behavior;
  recommend adding the two assertions from `tests/audit/audit_offline.py` after F1 is fixed.

---

## 8. Constants & assumptions register

| Constant | Value | Where | Sourced / assumed |
|---|---|---|---|
| transport_per_km_usd | 0.35 | cost.py | assumption (vehicle hire + fuel) |
| per_diem_usd | 60 | cost.py | assumption (lodging + food / person / day) |
| surgeon_day_value_usd | 800 | cost.py | assumption (opportunity cost of a lost operating day) — dominant term |
| team_size / mission_days defaults | 6 / 7 | cost.py | assumption (VF-plausible; user-adjustable) |
| patients_per_volunteer_day | 20 | cost.py | assumption (capacity-to-serve; UI-adjustable) |
| addressable_need_units | 4,000 | cost.py | assumption — **flat across districts (no population denominator, F4)** |
| ROAD_FACTOR | 1.3 | reach.py | assumption (straight-line → road, rural India) |
| AVG_SPEED_KMH | 45 | reach.py | assumption (drive-hours estimate) |
| SUPPLY_HALF_SATURATION | 3.0 | coverage.py | assumption (½-adequacy at 3 facilities) |
| TRUST_WEIGHTS | high 1.0 / med 0.6 / unv 0.3 | coverage.py | assumption (evidence hierarchy) |
| DESERT_SHADE_THRESHOLDS | strong<0.34, moderate<0.5 | coverage.py | assumption (map shading) |

All are **named, labeled assumptions** exposed in code and (mostly) the UI — the right posture for
judges. None is presented as a sourced clinical standard.

---

## 9. Prioritized fix list (for the user to action)

1. ~~**F1 — make cost monotonic & origin-comparable (HIGH).**~~ **✅ DONE** — uniform travel-time model
   in `reach.py`, provenance flagged on the UI, regression test added (52/52 green).
2. **F5 — reframe the volunteer badge (MED, copy only).** With 22 true values nationwide, present 🤝 as
   "accepts volunteers (where known)" and don't lead with it.
3. ~~**F2 — decide auto-days ranking semantics (LOW–MED).**~~ **✅ DONE** — ranking is on a fixed
   mission length; auto-days is a readout only; UI caption + regression test added.
4. **F6 — dedup facilities on `unique_id` before counting (LOW).** ~0.1% supply overcount today.
5. **F7 — backfill 14 missing centroids (LOW).** Closes the optimizer blind spot for non-Patna origins.
6. **F3 — delete/archive `context/` (LOW, hygiene).**
7. **F8 — correct prose counts (LOW):** 9,964 facilities, 706 NFHS districts.

**What needs no change:** the data pipeline (aggregation reconciles exactly), the NFHS values, the
capability mappings, the trust-weighting, the gap classification, the desert score, the no-data
honesty, the state-name canonicalization, and the optimizer's no-data exclusion rule — all verified
correct against the live source.

---

*Audit scripts (`tests/audit/audit_offline.py`, `tests/audit/live_recon.py`) are read-only and
re-runnable; every FAIL/WARN above cites a number they reproduce.*

---

## 11. AI footprint (AI-for-Social-Good)

- **AI summary column (Databricks `ai_query`).** The ingest Job generates a per-district recommendation
  with `ai_query('databricks-gpt-oss-20b', …, failOnError => false)` that reasons **over the deterministic
  numbers** (care-gap score, coverage status, verified/total facilities) — it interprets, never computes,
  the metrics, so the trust model holds. 4,164/4,164 rows populated (no quota issue); deterministic CASE
  fallback guarantees no blanks. Surfaced in the coverage table's "🤖 AI summary" column via Lakebase
  `mission.district_ai_summary` (SP SELECT verified).
- **One merged copilot.** Genie is now a tool (`ask_genie`) of the single "Ask the copilot" agent — it
  reasons over the deterministic tools and runs native text-to-SQL on demand; numbers stay tool/Genie-sourced.
- **Deliberately NOT AI (and why):** claims grading stays transparent keyword corroboration for citability;
  district-name reconciliation stays curated to avoid wrong joins. Documented in the in-app
  "How does it all work?" page (Logical enablers).

## 10. Free-Edition risks (platform limits, not logic defects)

- **Lakebase load cannot run on a serverless Job (memory ceiling).** The reproducible ingestion Job
  (`mission_ingest` → `data/job_ingest.py`) runs the full Spark read + point-in-polygon + claims +
  coverage transforms and writes the five curated **UC Delta** tables (`workspace.mission_uc.*`)
  on-platform — verified `TERMINATED SUCCESS`, row counts exact (district_capability 4164,
  facility_claims 12774, district_base 695, centroids 680, district_coverage 4164). But the
  **Lakebase bulk-COPY** (`data/load_lakebase.py`) **OOMs on Free-Edition serverless** even as an
  isolated task with tiny data and row-streaming — "Execution ran out of memory" is a Free-Edition
  serverless resource ceiling, not a logic bug. **Mitigation:** Lakebase `mission.*` is loaded via the
  local `data.load_lakebase` path (unchanged, proven); the Job owns the UC-Delta half. The COPY was made
  streaming (one row resident) regardless — a real improvement for the local path.
- **Genie companion — built, created, and verified working.** A Genie space
  (`01f169b79bea145194ed851438c7f553`) sits over the 4 curated `workspace.mission_uc.*` tables + the 3
  raw VF tables on the PRO/serverless warehouse `3027e674d4e2102b`. Live-tested: "highest maternity
  desert score" → SQL over `district_coverage` → Nagaland (matches the app); "facilities in Bihar" →
  raw table → 258. App SP has `CAN_RUN` (via the `genie_space` app resource). Genie API is rate-limited
  (~5 q/min) — the app surfaces a friendly message on 429.
- **Lakebase itself is listed as unavailable on Free Edition in current docs**, yet runs in our
  workspace. Treated as empirical truth; the new UC-Delta layer is a natural fallback substrate for the
  curated data if Lakebase is ever disabled.
