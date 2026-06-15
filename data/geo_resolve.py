"""
geo_resolve.py — Resolve facility coordinates -> district by point-in-polygon, then
reconcile to NFHS-5 districts and aggregate the SUPPLY side.

WHY (the gate finding this fixes)
    A naive district NAME-join links only ~85% of NFHS-5 districts and is ambiguous for names
    repeated across states. So we resolve geography by COORDINATES, not names:
        facility (lat,lon) --point-in-polygon--> ADM2 polygon (shapeName)
        polygon shapeName  --name reconciliation--> NFHS-5 district_name
        aggregate           --> supply counts per NFHS district (the coverage layer input)

PIPELINE (all local, validated before it ever runs on a cluster)
    1. load facilities extract        (data/cache/facilities_geo.csv  — pulled from Databricks)
    2. load NFHS-5 district roster     (data/cache/nfhs5_districts.csv — pulled from Databricks)
    3. fetch India ADM2 polygons       (external/district_polygons.py, cached)
    4. point-in-polygon each facility  -> polygon district name
    5. reconcile polygon names <-> NFHS names (exact + normalized), report the unmatched tail
    6. aggregate supply per NFHS district -> data/cache/district_base.csv
    7. print resolution stats (the honesty numbers for the demo)

RUN
    ./.venv/bin/python -m data.geo_resolve
"""

from __future__ import annotations

import csv
import difflib
import re
from pathlib import Path

from data.external.district_polygons import fetch_india_districts, build_index, assign_district

CACHE = Path(__file__).resolve().parent / "cache"
FAC_CSV = CACHE / "facilities_geo.csv"
NFHS_CSV = CACHE / "nfhs5_districts.csv"
OUT_CSV = CACHE / "district_base.csv"
UNMATCHED_CSV = CACHE / "unmatched_districts.csv"


# Curated district-name aliases: normalized POLYGON name -> normalized NFHS-5 name.
# CURATED, NOT auto-fuzzy: fuzzy matching produced dangerously wrong joins for post-2019
# districts (e.g. Ranipet->Panipat, Agar->Sagar), so each entry below was VERIFIED against the
# NFHS-5 roster. Spelling variants and known renamings only. Genuinely new districts that have
# no NFHS-5 (2019-21) baseline are intentionally NOT aliased — see NEW_DISTRICTS_NO_BASELINE.
DISTRICT_ALIASES = {
    "hydrabad": "hyderabad",
    "rangareddy": "ranga reddy",
    "medchal": "medchalmalkajgiri",
    "north twenty four parganas": "north twenty four pargana",
    "south twenty four parganas": "south twenty four pargana",
    "warangal u": "warangal urban",
    "warangal r": "warangal rural",
    "sri potti sriramulu nellore": "sri potti sriramulu nello",
    "kadapaysr": "y s r",
    "samli": "shamli",
    "jagtial": "jagitial",
    "agar": "agar malwa",
    "batod": "botad",
    "gariaband": "gariyaband",
    "karbi anglong west": "karbi anglong",   # 2016 split -> parent burden (approximate)
    "yadadri bhongiri": "yadadri bhuvanagiri",
    "bhadradri": "bhadradri kothagudem",
}

# Polygons that are post-2019 districts (or ambiguous splits) with NO NFHS-5 baseline row.
# Their facilities are real, but we cannot attribute burden to them — surfaced, not guessed.
NEW_DISTRICTS_NO_BASELINE = {
    "alipurduar", "kalimpong", "chengalputtu", "ranipet", "tenkasi", "tirupathur", "barddhaman",
}


# --------------------------------------------------------------------------- normalize
def normalize_name(s: str) -> str:
    """Lowercase, strip common suffixes/punctuation so district names compare across sources."""
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[._]", " ", s)
    s = re.sub(r"\b(district|distt|dist|division|circle)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)        # drop &, (), etc.
    s = re.sub(r"\s+", " ", s).strip()
    return s


# --------------------------------------------------------------------------- load
def load_facilities() -> list[dict]:
    with FAC_CSV.open() as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["latitude"] = float(r["latitude"])
        r["longitude"] = float(r["longitude"])
        r["maternal_supply"] = int(r["maternal_supply"])
    return rows


def load_nfhs_districts() -> list[dict]:
    with NFHS_CSV.open() as f:
        return list(csv.DictReader(f))


def _base_row(nd: dict, facilities: int, maternal: int, public: int, private: int) -> dict:
    """District base row: identity + supply columns, then ALL NFHS indicator columns passed
    through verbatim (so the burden layer can use any indicator without re-plumbing)."""
    row = {"nfhs_district": nd["district_name"], "state_ut": nd["state_ut"],
           "facilities": facilities, "maternal_supply_facilities": maternal,
           "public": public, "private": private}
    for k, v in nd.items():
        if k not in ("district_name", "state_ut"):
            row[k] = v
    return row


# --------------------------------------------------------------------------- resolve
def resolve(force_fetch: bool = False) -> dict:
    facilities = load_facilities()
    nfhs = load_nfhs_districts()
    print(f"loaded {len(facilities)} facilities, {len(nfhs)} NFHS-5 districts")

    index = build_index(fetch_india_districts(force=force_fetch))
    engine = index.get("engine")
    print(f"polygon index ready (engine={engine})")

    # 4. point-in-polygon each facility
    resolved, unresolved = 0, 0
    supply_by_poly: dict[str, dict] = {}
    for fac in facilities:
        hit = assign_district(fac["latitude"], fac["longitude"], index)
        if not hit or not hit.get("district"):
            unresolved += 1
            continue
        resolved += 1
        key = normalize_name(hit["district"])
        agg = supply_by_poly.setdefault(key, {"raw_name": hit["district"], "facilities": 0,
                                              "maternal": 0, "public": 0, "private": 0})
        agg["facilities"] += 1
        agg["maternal"] += fac["maternal_supply"]
        if fac["operator"] == "public":
            agg["public"] += 1
        elif fac["operator"] == "private":
            agg["private"] += 1

    print(f"point-in-polygon: {resolved} resolved, {unresolved} unresolved "
          f"({resolved/len(facilities):.1%} coverage)")

    # 5. reconcile polygon names <-> NFHS names (on normalized key)
    nfhs_by_key = {}
    for d in nfhs:
        nfhs_by_key.setdefault(normalize_name(d["district_name"]), d)

    matched, aliased, unmatched_polys = 0, 0, []
    rows_out = []
    nfhs_matched_keys = set()
    nfhs_norm_keys = list(nfhs_by_key)
    for key, agg in supply_by_poly.items():
        nd = nfhs_by_key.get(key)
        if nd is None and key in DISTRICT_ALIASES:           # curated, verified alias
            nd = nfhs_by_key.get(DISTRICT_ALIASES[key])
            if nd is not None:
                aliased += 1
        if nd:
            matched += 1
            nfhs_matched_keys.add(normalize_name(nd["district_name"]))
            rows_out.append(_base_row(nd, agg["facilities"], agg["maternal"],
                                      agg["public"], agg["private"]))
        else:
            # Assist (don't auto-apply) the human: best fuzzy candidate + a reason.
            sugg = difflib.get_close_matches(key, nfhs_norm_keys, n=1, cutoff=0.0)
            suggestion = nfhs_by_key[sugg[0]]["district_name"].strip() if sugg else ""
            reason = ("new district / no NFHS-5 baseline"
                      if key in NEW_DISTRICTS_NO_BASELINE else "needs review")
            unmatched_polys.append((agg["raw_name"], agg["facilities"], suggestion, reason))

    # NFHS districts with NO facilities resolved to them = possible deserts OR data gaps (R2).
    nfhs_with_no_supply = [d for d in nfhs if normalize_name(d["district_name"]) not in nfhs_matched_keys]
    for d in nfhs_with_no_supply:
        rows_out.append(_base_row(d, 0, 0, 0, 0))

    # 6. write outputs
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader(); w.writerows(rows_out)
    with UNMATCHED_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["polygon_district", "facilities", "fuzzy_suggestion", "reason"])
        w.writerows(sorted(unmatched_polys, key=lambda x: -x[1]))

    stats = {
        "facilities_total": len(facilities),
        "facilities_resolved_pct": round(resolved / len(facilities), 4),
        "polygon_districts_with_facilities": len(supply_by_poly),
        "matched_to_nfhs": matched,
        "  of_which_via_curated_alias": aliased,
        "unmatched_polygon_districts": len(unmatched_polys),
        "nfhs_districts_total": len(nfhs),
        "nfhs_districts_with_zero_supply": len(nfhs_with_no_supply),
    }
    return {"stats": stats, "rows": rows_out}


if __name__ == "__main__":
    import json
    out = resolve()
    print("\n=== RESOLUTION STATS ===")
    print(json.dumps(out["stats"], indent=2))
    print(f"\nwrote district base -> {OUT_CSV}")
    print(f"wrote unmatched polygon names (for manual reconciliation) -> {UNMATCHED_CSV}")
    print("\nNOTE (Data Risk R2): NFHS districts with zero resolved supply are CANDIDATE deserts, "
          "NOT confirmed — facility data is web-sourced and rural-sparse. Treat as low-confidence.")
