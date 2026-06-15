# Data Risk Register — Virtue Foundation (DAIS 2026) dataset

> Honest assessment of where the provided data is strong, where it is weak, and what would be
> needed to scale the Medical Mission Deployment Copilot beyond the hackathon. This file is a
> **demo asset**: volunteering it is what disarms "is your data real?" — and the gaps below are
> themselves the *evidence-and-uncertainty* rubric points. Findings from `01_data_gate_analysis.py`
> (run 2026-06, Free Edition). Severity = impact on our use case, not on the data in general.

---

## 1. Where the data is STRONG (lean on these in the demo)

| Strength | Evidence | Why it matters |
|---|---|---|
| Facility geolocation is usable | **98.8%** of 10,088 facilities have valid India coordinates | Makes the **spatial join** (point-in-polygon → district) viable — the backbone of the coverage layer |
| NFHS-5 burden is comprehensive | 706 districts × 109 indicators, 36 states/UTs; suppression rare | A genuinely rich, real, district-resolution **burden** layer — hard for competitors to out-source |
| Maternal/child supply is well represented | ob/gyn 4,660 · pediatrics 5,080 · gen-surgery 3,201 | Supports a credible **maternal-health** demo intervention with both burden AND supply signal |
| PIN geography is near-complete | 165,627 rows, 19,586 PINs; **92.8%** have coordinates | Usable as a secondary geographic glue / settlement-density proxy |
| Indicators are decision-relevant | institutional birth, ANC-4, anaemia, NCD screening, vaccination | Directly map to interventions an NGO would actually deploy |

---

## 2. Where the data is WEAK / RISKY (handle honestly, never hide)

### 🔴 HIGH — these can mislead the core decision

**R1. Supply skews ~88% private.**
8,842 `private` vs **469 `public`** (688 null, 2 government). NGO surgical/medical missions
typically fill **public / charitable** gaps in underserved areas. A supply layer dominated by
private (often urban, fee-paying) facilities **overstates accessible coverage** for the poor
populations a mission targets.
*Mitigation:* weight coverage by `operatorTypeId`; treat "private-only" districts as *low public
coverage*; surface the public/private split per district as a visible caveat.

**R2. Geographic / urban-digital bias.**
Facilities concentrate in populous, digitally-visible states (Maharashtra 1,575, Gujarat 981,
UP 919, Tamil Nadu 780…). The FDR pipeline extracts from web sources (Justdial, Facebook, clinic
sites), so facilities in **poor, rural districts — exactly the medical deserts we want to find —
are underrepresented**. **Absence of facility data ≠ confirmed desert.**
*Mitigation (critical):* never present "0 facilities" as certainty. Tag each district's coverage
with a **data-density confidence**; cross-check facility absence against PIN post-office density
(settlement proxy). This is the single most important caveat for the brief.

**R3. GenAI extraction field-bleed.**
Some rows have leaked values in the wrong column — e.g. `operatorTypeId` containing URLs,
a coordinate (`81.657…`), `"true"`, or `"kie"`. The medallion + GenAI extraction occasionally
misaligns fields.
*Mitigation:* whitelist categorical values (`private|public|government|null`); schema-validate
and bbox-filter coordinates (done); quarantine rows that fail validation rather than trusting them.

### 🟠 MEDIUM

**R4. Near-duplicate facilities.** 10,088 rows vs **9,959 distinct `cluster_id`** (~129 dupes).
*Mitigation:* dedupe on `cluster_id` before counting supply.

**R5. NFHS-5 mixed typing.** ~half the 109 indicators are `string`-typed because they carry `*`
(suppressed), `(x)` (low-confidence), or trailing spaces. Naive numeric use breaks or mis-scores.
*Mitigation:* the `parse_nfhs_value` discipline in `context/burden.py` (`*`→None, `(x)`→low-conf).

**R6. District-name reconciliation across THREE vocabularies.** Polygon `shapeName`, NFHS
`district_name`, and PIN `district` all differ (naive name-join covers only **85%** of NFHS
districts; some names repeat across states). Spatial join fixes facility→district, but a one-time
**~700-row name reconciliation** (polygon ↔ NFHS) is still required.
*Mitigation:* exact + normalized match, then manual fix of the unmatched tail; report coverage.

**R7. Specialty vocabulary is inconsistent free-text arrays.** e.g. `generalSurgery` vs
`generalsurgery`, `ent` vs `otolaryngology`, duplicates within a row.
*Mitigation:* normalize to a controlled specialty vocabulary before counting supply.

### 🟡 LOW (note, don't block)

**R8. Capability/capacity mostly null.** `numberDoctors`, `capacity`, `equipment` are sparse →
we can assert facility *presence* but not reliably its *capability*. Limits "which facility to
partner with" to presence + declared specialties.

**R9. Temporal staleness.** NFHS-5 is **2019–21**; facility `recency_of_page_update` is largely
null so web-data vintage is unknown.
*Mitigation:* NFHS-6 **state-level** trajectory (`external/nfhs6_trend.py`); label all vintages.

**R10. Country leakage.** 88 facilities are not `address_countryCode='IN'`; 6 have out-of-India
coordinates. Small, already excluded by the bbox filter.

---

## 3. What's needed to SCALE this use case (beyond the hackathon)

Ordered by leverage for turning the demo into something VF could operate:

1. **Authoritative public-facility registry** — National Health Facility Registry / HMIS /
   Ayushman Bharat HWC lists. Directly fixes **R1 + R2**: corrects the private skew and fills
   rural coverage the web-scraped layer misses. *Highest-leverage single addition.*
2. **Population & demographic denominators per district** — Census 2011 + projections (or
   WorldPop). Turns the `people_reached` heuristic into a defensible estimate, not a guess.
3. **District-level NFHS-6** (when released) — true trajectory at decision resolution, removing
   the NFHS-5(district) ↔ NFHS-6(state) resolution gap (R9).
4. **Validated facility capability data** — beds, operating theatres, specialist counts, service
   volumes — to rank *which* facility to partner with, not just where (fixes R8).
5. **Richer reachability** — seasonal/monsoon road accessibility, terrain, public-transport
   options; beyond ORS average driving times.
6. **Cost coefficients from VF's real mission ledgers** — replace the labeled assumptions in
   `context/cost.py` with sourced per-diem, transport, and surgeon-day values.
7. **Productionized data-quality pipeline** — schema validation, categorical whitelisting, and
   entity resolution on the GenAI extraction (fixes R3 + R4 at scale).
8. **Multi-country generalization** — VF operates in 25+ countries. The reusable asset is the
   *pipeline pattern* (extract → resolve geography → join burden → cost-per-impact), not the
   India-specific tables. Scaling means parameterizing geography + burden sources per country.

---

## 4. One-line summary for judges

> *"Most layers are real and strong — district burden and facility geolocation especially. The
> known weaknesses (a private/urban supply bias, occasional extraction noise, and a 2019–21
> vintage) are measured, surfaced in the app, and never reasoned over as if complete. Absence of
> facility data is shown as uncertainty, not asserted as a medical desert."*
