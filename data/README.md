# `data/` — data gate + external connectors

This folder completes **Rung 0's data gate**: it proves the provided dataset can support the
cost-per-impact chain, and provides the connectors for the external data we depend on. See
`context/use_case.md` and `context/architecture.md` for the full plan.

## Contents

| File | What it does |
|---|---|
| `01_data_gate_analysis.py` | **Databricks notebook** (source format). Walks schema, volume, and quality of all three provided tables, tests cross-table linkage, and prints an explicit **GO/BLOCKED gate verdict** + intervention recommendation. Read-only. |
| `DATA_RISKS.md` | Risk register — where the data is strong/weak (severity-rated) and what's needed to scale beyond the hackathon. A demo asset. |
| `geo_resolve.py` | **Point-in-polygon pipeline.** Resolves every facility coordinate → district, reconciles polygon names ↔ NFHS-5 (curated alias map, *not* auto-fuzzy), aggregates supply per district → `cache/district_base.csv`. |
| `external/ors_client.py` | Road reachability via OpenRouteService Matrix, cached, with a **straight-line × 1.3 fallback** so routing never hard-fails the demo. |
| `external/district_polygons.py` | Downloads India **ADM2 district boundaries** (geoBoundaries) and assigns coordinates → district by **point-in-polygon** (the fix for the unreliable name-join). |
| `external/nfhs6_trend.py` | Loads **NFHS-6 state-level** indicators and computes improving/worsening trajectory (district-level NFHS-6 not reliably available — resolution gap disclosed). |
| `requirements.txt` | Local dev deps for the connectors + geo pipeline (all optional; pure-Python fallbacks exist). |
| `cache/` | Cached pulls + derived tables (git-ignored): `facilities_geo.csv`, `nfhs5_districts.csv`, `india_adm2.geojson`, `district_base.csv`, `unmatched_districts.csv`. Pre-compute here so the demo runs offline. |

## Geo resolution result (run 2026-06)

`geo_resolve.py` resolved **99.98%** of 9,953 facilities to a district by point-in-polygon, and
reconciled **534/541** polygon districts to NFHS-5 (17 via a curated, verified alias map —
e.g. `Hydrabad`→Hyderabad, `Medchal`→Medchal-Malkajgiri). The remaining 7 are **post-2019 new
districts with no NFHS-5 baseline** (Ranipet, Tenkasi, Tirupathur, Alipurduar, Kalimpong,
Chengalpattu, + the ambiguous Barddhaman split) — correctly left unmatched, not force-joined.
Output: `cache/district_base.csv` (per-district burden + facility supply + public/private split),
the input to the cost-per-impact chain. **161 NFHS districts have zero resolved supply** —
candidate deserts *or* data gaps (Risk R2): treat as low-confidence, never asserted.

> **Known follow-on (Risk R6):** the name index collapses ~11 same-name districts across states
> (e.g. Aurangabad MH/BR) to the first occurrence — fix by keying on (state, district) or
> resolving polygon→state too.

## Data gate verdict (run 2026-06, Free Edition)

**GO.** Catalog: `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset`.

- **facilities** — 10,088 rows; **98.8% have valid India coordinates** (118 missing, 6 out-of-India junk → excluded with a note). Specialty supply: ob/gyn 4,660 · pediatrics 5,080 · gen-surgery 3,201 · ophthalmology 2,869.
- **nfhs_5_district_health_indicators** — 706 districts × 109 indicators, 36 states/UTs. Suppression (`*`) is rare; low-confidence `(x)` values handled by `context/burden.py`.
- **india_post_pincode_directory** — 165,627 rows, 19,586 PINs, 750 districts. ~8.5 rows/PIN (post-office fan-out trap); 7.2% `NA` coords.
- **Linkage** — naive district name-join covers only 597/698 NFHS districts (85%) → **point-in-polygon required** (`district_polygons.py`).

**Recommended demo intervention: maternal health** — strongest alignment of facility supply (4,660 ob/gyn) and NFHS-5 burden (institutional birth, ANC-4, skilled attendance, maternal anaemia). Avoid ophthalmology/cataract as the headline: facility supply exists but **NFHS-5 has no vision burden indicator**.

## Running

**The notebook** — import `data/01_data_gate_analysis.py` into the workspace (or sync via the
Databricks CLI) and run on the serverless warehouse/compute. It only `SELECT`s from the shared
catalog.

**The connectors** (locally, Python 3.10+):

```bash
export ORS_API_KEY=...          # optional; free key at https://openrouteservice.org/sign-up/
pip install requests shapely    # optional — both have pure-Python fallbacks

python -m data.external.ors_client          # smoke test (works without a key, via fallback)
python -m data.external.district_polygons   # downloads + caches India district polygons
python -m data.external.nfhs6_trend         # reports NFHS-6 availability
```

Each connector exposes `validate_setup()` so the gate can report what's wired and what's missing.
