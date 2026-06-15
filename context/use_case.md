# Medical Mission Deployment Copilot — Use Case & Decision Record

> **Project context for Claude Code.** Complete narrative: what we're building, why it wins, every decision and its reasoning, the data provenance ledger, the incremental build ladder, and open questions. Read alongside `architecture.md` (how it's built) and `eval_set.md` (how it's tested). This doc carries the *why*.
>
> **NOTE ON HISTORY:** This project pivoted from a wildfire-evacuation concept once the hackathon's provided dataset was revealed (India healthcare, not wildfire). The reusable spine — "the agent decides over grounded facts, never computes or invents," the uncertainty-honesty discipline, the eval methodology, and "don't optimize the naive metric, optimize the real one" — carried over intact. Wildfire-specific logic was retired.

---

## 1. The opportunity

**Event:** Databricks "Apps & Agents for Good" Hackathon 2026 (partner: OpenAI), at Data + AI Summit. Build **agentic data apps for social impact** on **Databricks Free Edition**, using the provided hackathon dataset, **Lakebase**, **Agent Bricks / model serving**, and **Databricks Apps**. Live 3-minute demo; Git repo + Devpost submission.

**Timing:** Sunday evening now -> showcase Tuesday afternoon. Solo participant, using Claude / Claude Code as the working team. This mandates **scope discipline**: a narrow thing that works flawlessly beats an ambitious thing that breaks live.

**Provided dataset:** Virtue Foundation dataset (DAIS 2026) — India healthcare. Three layers:
- **Facilities** (FDR pipeline core): GenAI-extracted, one unified row per facility. Supply side. *Exact columns confirmed during build — gates specialty choice.*
- **NFHS-5 district health indicators**: 706 districts x 109 indicators (2019–21). Demand/burden side. The rich layer.
- **India Post PIN directory**: 165,627 rows; geographic glue. Trap: row grain is post office, not PIN — naive join fans out; dedupe/aggregate first.

---

## 2. Who Virtue Foundation is (defines the user)

VF is a real medical NGO. Mission: *deliver quality healthcare to those in need, and build "an efficient marketplace for global healthcare delivery through machine learning and AI."* They run **volunteer surgical/medical missions** (Ghana, Mongolia, 25+ countries) and already have a data product, **VF Match** (built with Carto, Databricks, DataRobot), mapping "medical deserts across specialties" in 72 countries.

**The user is therefore unambiguous:** a medical-NGO planner deciding **where to send a mission, which specialty to bring, and which facility to partner with**, to do the most good with scarce volunteer surgeons. We build a focused, India-specific, *agentic* version of VF's own job. Our edge over VF Match is the agentic reasoning layer + cost-per-impact + honest uncertainty, not generic desert-mapping.

---

## 3. What we're building (one paragraph)

An **agentic deployment-optimization copilot for medical NGOs.** Input: an intervention/specialty plus constraints (team size, days, budget). Output: a ranked set of Indian districts where that team does the most good **per dollar** — each backed by burden evidence (NFHS-5), the coverage gap (facilities), real road reachability and a transparent cost estimate (OpenRouteService + a documented cost model), a directional sense of whether the region is improving (NFHS-6 state-level trend), and clinical grounding for why this intervention fits this burden profile — delivered as a **cited mission brief that flags every uncertain join and suppressed value.**

---

## 4. Why this wins

### 4a. The shared-data problem
Every team gets the same three tables. The baseline everyone builds is "burden - coverage = medical-desert map." That's table stakes. **The edge is what you layer on top and connect to**, not the data itself.

### 4b. The three differentiators (in priority order)
1. **Cost-per-impact ranking (the centerpiece).** Not raw need, not raw proximity — estimated people-reached / total mission cost. This is the decision an NGO actually makes, and it requires *chaining* burden -> gap -> reachability -> cost -> impact ratio. The chain is the moat; each link is individually defensible. Direct heir to the wildfire "don't optimize the naive metric" insight.
2. **The cited mission brief (artifact).** A real deliverable an NGO would use, reusing everything the ranking computed. High value-per-effort.
3. **External-data delight.** The provided data is static and 2019–21; it can't see *now* or real-world logistics. We connect:
   - **OpenRouteService** (free, OSM-based, Matrix endpoint) -> real road travel time/distance -> the reachability + cost layer. Validated.
   - **NFHS-6 (2023–24)** -> directional trajectory ("is this region improving?"). **Scoped to state level** — district-level NFHS-6 CSVs were not cleanly available as of mid-2026; NFHS-6 release tables are "national and state/UT levels" only. Use NFHS-5 for district burden, NFHS-6 for state trend, and *label the resolution difference as a visible uncertainty.*
   - (Optional, only if trivial) clinical-guideline grounding for the burden->intervention rationale.

### 4c. Maps to the judging rubric
- **Product judgment (clear user, thoughtful tradeoffs):** NGO planner; cost-model assumptions explicit and adjustable.
- **Evidence & uncertainty (grounded, honest):** every number cites a source row or named assumption; suppressed values (`*`), low-confidence estimates (parenthesized), uncertain joins, and the NFHS-5-vs-6 resolution gap are all surfaced, not hidden. *This dataset's own quality caveats become features.*
- **Technical execution (works live, uses Databricks well):** Lakebase as real operational state; pre-computed/cached external calls; spatial join done right (point-in-polygon, not string-matching district names).
- **Ambition (beyond the minimum):** the cost-per-impact chain, the trajectory, the generated brief.

---

## 5. The cost-per-impact model (the spine — get this right first)

Reasoning chain, each link deterministic Python the agent reasons *over*:

1. **Burden** (NFHS-5, district): for the chosen intervention, the relevant indicators (e.g., cataract -> vision proxies; maternal -> ANC/institutional delivery; anaemia; diabetes/hypertension).
2. **Gap** (facilities + burden): high burden AND low/distant relevant-facility coverage = desert. Computed.
3. **Reachability** (ORS Matrix, pre-computed + cached): road travel time + distance from staging city/facility to district. Labeled "estimated."
4. **Cost model** (transparent, named assumptions): transport (distance x per-km rate) + stay (days x per-diem x team size) + reach time-cost (lost operating days). Every coefficient defensible and adjustable.
5. **Impact-per-dollar:** estimated people-reached (burden magnitude x district population) / total cost -> ranking metric.
6. **Cited recommendation:** agent explains the ranking, cites the indicators, flags uncertainty.

**Defensibility rule:** when asked "where does this number come from?", show the breakdown. Transparent assumptions are a rubric point; a black-box number is a hallucination. Ground defaults in real norms where possible (per-diem, fuel rates, VF's published mission team sizes/durations), labeled as assumptions.

---

## 6. Anti-hallucination architecture (carried over, non-negotiable)

**The agent decides; it never computes or invents.**
- Burden values, gaps, distances, costs, impact ratios — all from deterministic Python or DB rows, handed to the agent as grounded context.
- The agent only **classifies/ranks among provided options**, **explains**, and **fills the brief template**.
- It cannot invent a facility (selects from the table), a district indicator (reads NFHS-5), or a cost (computed). A missing value -> "[unavailable / suppressed]", never a guess.
- One-line for judges: *"Every number traces to a source row or a named assumption. The agent reasons and explains; it doesn't fabricate."*

---

## 7. Data provenance ledger (the honesty story — a demo asset)

| Layer | Source | Status |
|---|---|---|
| Healthcare facilities | VF / FDR pipeline (provided) | **Real** — GenAI-extracted; expect sparseness, flag it |
| District health burden | NFHS-5 2019–21 (provided) | **Real** — district resolution, the burden core |
| Postal geography | India Post PIN directory (provided) | **Real** — join layer; watch post-office row grain |
| Road reachability | OpenRouteService (OSM) | **Real, estimated** — rural India road data approximate; labeled |
| State health trajectory | NFHS-6 2023–24 | **Real, state-level only** — district CSVs not cleanly available; resolution gap disclosed |
| Cost coefficients | Named assumptions (per-diem, fuel, team) | **Assumptions, transparent + adjustable** — not claimed as ground truth |
| Estimated people-reached | burden x population heuristic | **Derived estimate** — method shown, hedged |

Volunteering this table is what disarms "is your data real?". Most layers real; the derived/assumed ones are labeled, which is itself the evidence-and-uncertainty rubric point.

---

## 8. Free Edition reality (confirmed)

Per the hackathon setup guide, **Lakebase IS available** for the event on Free Edition (the generic public limitations page is out of date for this configuration). Workflow the guide recommends: **build/test locally, deploy to Free Edition regularly, deploy early once.** Apps cap: <=3, auto-stop 24h after deploy/update -> deploy late, restart before judging. Credits: post account ID in `#get-databricks-credits` Discord if limits hit. Start from the **hackathon app template** (a prompt you paste into the coding agent) which scaffolds a Lakebase-backed app with the dataset synced from Unity Catalog Marketplace — adapt it rather than build the scaffold from scratch.

---

## 9. The incremental build ladder (the scope discipline)

**Rule: never start a rung until the previous is demoable AND stable. After every rung you have a complete story.**

- **Rung 0 — Foundation (Sun night):** Free Edition + template scaffolds Lakebase app. Confirm facilities schema (decides specialty). Build grounding + cost functions in pure Python with unit tests. No LLM. *Demoable: data joined, one district's cost computed.*
- **Rung 1 — Core agent flow (Mon AM):** one intervention -> candidate districts -> full chain -> ranked top-N by cost-per-impact, reasoning shown. **Minimum winning demo. Everything after is additive.**
- **Rung 2 — Cited mission brief (Mon PM):** generate the one-page brief for the top district (burden evidence, facility, reach + cost breakdown, clinical rationale, caveats). Second vertical; reuses Rung 1.
- **Rung 3 — One visual + trend (Mon eve / Tue AM, CUTTABLE):** desert map + reach lines; state-level NFHS-6 trajectory note. The "something in other verticals." Only if 1–2 bulletproof.
- **Rung 4 — Polish + rehearse (Tue AM):** lock the 3-min script, rehearse the one flow until unbreakable, prep provenance/uncertainty Q&A.

---

## 10. Decision log

1. **Pivot to provided healthcare dataset** — building on VF data is expected; ignoring it works against the technical-execution and for-Good framing. Reusable patterns transferred; wildfire retired.
2. **User = medical-NGO mission planner** — unambiguous, matches VF's actual job, data supports the planner side far better than a patient-navigator (no real-time/facility-specialty detail for routing individuals).
3. **Cost-per-impact as centerpiece** — the decision NGOs actually make; requires a defensible reasoning chain a 4-person team won't casually replicate.
4. **External connections: ORS (validated) + NFHS-6 state trend (scoped honestly)** — chosen for impact-per-effort; both pre-computed/cached, never live in demo.
5. **Cost model = transparent named assumptions** — adjustability + citation is a rubric strength; a black box is a liability.
6. **Agent decides, never computes** — carried-over anti-hallucination spine; every output gradeable.
7. **Spatial join over string-matching** — dataset explicitly warns name-matching is unreliable; point-in-polygon on coordinates is robust.
8. **Incremental ladder, deep-before-wide** — solo + one night + Tue PM; one bulletproof flow beats ten broken features.
9. **Uncertainty as a feature** — suppressed values, low-confidence estimates, approximate routing, resolution gaps all surfaced; the dataset's caveats become the evidence-and-uncertainty story.
10. **Facility capability = a claim to verify, not ground truth** *(added 2026-06-15, from the summit "Core Requirements" + "Where the dataset comes from" slides).* The earlier R8 read (capability "mostly null") conflated the sparse *structured* fields (capacity 25%, year 48%) with the *free-text* capability (99.7%) / procedure (92.5%), which are well-covered. We re-ingest that free-text (`data/02_facility_text_ingest.py`), grade each facility's ob/gyn claim by corroboration against its own procedure/equipment text (`mission_core/claims.py`: high/medium/unverified), and **cite the underlying text** behind every ranking — satisfying the rubric's "cite the underlying facility text" + "treat noisy fields as claims to verify." This serves Track 2 (the supply side of the desert ranking) and opens a Track 3 (Referral Copilot) seam later. The FDR `maternal_supply` flag is itself treated as an unverified claim.
11. **Persist the planner's work** *(added 2026-06-15, from the "Persist user actions" MUST).* The app now saves shortlists, per-district notes, review decisions (approve/reject/needs-investigation, wired to the candidate-gap tier), and named scenarios (inputs + ranking snapshot) — dual-backend like the read path (SQLite local / Lakebase `mission_app` schema the App SP owns). One write path, self-provisioning tables, de-risked by an `app.yaml` `[WRITEPROBE]`.

12. **Coverage-led Track-2, multi-capability + geography** *(added 2026-06-15, from the full official Track-2 spec).* The primary view is now **trust-weighted coverage by geography**: pick a capability (maternity, ICU, NICU, emergency, oncology, trauma) and a state → districts ranked by desert score and classified **confirmed_coverage / unverified_claims / no_claim_desert** (the literal "real gap vs data-poor"). Supply is **trust-weighted** (`mission_core/coverage.py`: corroborated 1.0, claimed-only 0.6, flag-only 0 — or 0.3 with the honesty toggle); claims classified per-capability in `mission_core/claims.py`; aggregates in `district_capability` (district×capability) + long `facility_claims` (with **name, city, source_url**). Every citation now carries the facility **name + source link**. The cost-per-impact ranking is demoted to a labelled **"Deployment optimizer (maternal · Patna)"** deep-dive. New module `mission_core/coverage_view.py`; new agent tool `coverage_by_geography`.

**One-liner for judges:** *"Pick a capability and a state; we show trust-weighted coverage that tells real care deserts from data-poor regions, grade every facility's claim against its own text, cite it by name with a source link, communicate what's unverified, and persist the planner's shortlist, notes, reviews and scenarios."*

---

## 11. Open questions / action items

- [ ] **Inspect facilities schema first** (Rung 0): specialty/service fields? coordinate completeness? -> decides the demo intervention.
- [ ] **Set cost-model defaults** with real-norm grounding (per-diem, fuel/km, typical mission team size + days from VF's published missions).
- [ ] **Confirm ORS key + cache strategy** (matrix calls pre-computed for demo districts).
- [ ] **Check (briefly) whether district-level NFHS-6 CSVs have appeared**; if not, state-level trend only — do not bet the demo on district trajectory.
- [ ] **Pin the "estimated people-reached" heuristic** and write its caveat.
- [ ] **Solo-team eligibility** — confirm on Discord (mechanics don't block it, but confirm).
- [ ] **Define impact-per-cost ranking policy** + tie-breaks (the eval will test this).

---

## 12. Companion files
- `architecture.md` — components, schema, build order, the cost-model module, dual local/Lakebase path.
- `eval_set.md` — known-answer scenarios for the ranking + brief (anti-hallucination + management evidence + rehearsal).
