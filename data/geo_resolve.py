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
from mission_core.claims import classify_claim, CAPABILITIES

CACHE = Path(__file__).resolve().parent / "cache"
# Prefer the free-text extract (facilities_text.csv, from data/02_facility_text_ingest.py) so we can
# classify + CITE per-facility claims; fall back to the 5-column geo extract if text isn't pulled yet.
FAC_TEXT_CSV = CACHE / "facilities_text.csv"
FAC_GEO_CSV = CACHE / "facilities_geo.csv"
NFHS_CSV = CACHE / "nfhs5_districts.csv"
OUT_CSV = CACHE / "district_base.csv"
UNMATCHED_CSV = CACHE / "unmatched_districts.csv"
FACILITY_CLAIMS_CSV = CACHE / "facility_claims.csv"        # long: one row per resolved facility×capability
DISTRICT_CAPABILITY_CSV = CACHE / "district_capability.csv"  # long: one row per district×capability
_TEXT_COLS = ("specialties", "description", "capability", "procedure", "equipment")
_PROV_COLS = ("name", "city", "pincode", "source_urls")     # provenance (citations) — may be absent in legacy CSV


def _first_url(source_urls: str) -> str:
    """First http(s) URL from the source_urls field (a JSON-array string) — the citation link."""
    if not source_urls:
        return ""
    m = re.search(r"https?://[^\s\"',\]]+", source_urls)
    return m.group(0) if m else ""


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
def load_facilities() -> tuple[list[dict], bool]:
    """Returns (rows, has_text). Uses facilities_text.csv when present (carries the free-text claim
    columns); else the legacy 5-column facilities_geo.csv (no text -> claims can't be corroborated)."""
    has_text = FAC_TEXT_CSV.exists()
    src = FAC_TEXT_CSV if has_text else FAC_GEO_CSV
    with src.open() as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["latitude"] = float(r["latitude"])
        r["longitude"] = float(r["longitude"])
        r["maternal_supply"] = int(r["maternal_supply"])
        for c in _TEXT_COLS + _PROV_COLS:
            r.setdefault(c, "")
    note = "with free-text claims" if has_text else \
        "NO text — run data/02_facility_text_ingest.py to enable claim verification"
    print(f"facilities source: {src.name} ({note})")
    return rows, has_text


def load_nfhs_districts() -> list[dict]:
    with NFHS_CSV.open() as f:
        return list(csv.DictReader(f))


def _base_row(nd: dict, agg: dict) -> dict:
    """District base row: identity + supply columns + maternal-CLAIM aggregates, then ALL NFHS
    indicator columns passed through verbatim (so the burden layer can use any without re-plumbing).

    maternal_supply_facilities = facilities the (noisy) flag marks ob/gyn. The claim columns split
    that into text-corroborated (high/medium) vs flag-only (unverified) — the honesty signal."""
    m = (agg.get("caps") or {}).get("maternity", {})
    hi, md, un = m.get("high", 0), m.get("medium", 0), m.get("unverified", 0)
    row = {"nfhs_district": nd["district_name"], "state_ut": nd["state_ut"],
           "facilities": agg.get("facilities", 0),
           "maternal_supply_facilities": agg.get("maternal", 0),
           "public": agg.get("public", 0), "private": agg.get("private", 0),
           "maternal_claim_high": hi, "maternal_claim_medium": md,
           "maternal_claim_unverified": un, "maternal_verified_supply": hi + md}
    for k, v in nd.items():
        if k not in ("district_name", "state_ut"):
            row[k] = v
    return row


# --------------------------------------------------------------------------- resolve
def resolve(force_fetch: bool = False) -> dict:
    facilities, has_text = load_facilities()
    nfhs = load_nfhs_districts()
    print(f"loaded {len(facilities)} facilities, {len(nfhs)} NFHS-5 districts")

    index = build_index(fetch_india_districts(force=force_fetch))
    engine = index.get("engine")
    print(f"polygon index ready (engine={engine})")

    # 4. point-in-polygon each facility; classify its claim for EVERY capability; aggregate per
    #    polygon district × capability.
    def _new_caps():
        return {c: {"high": 0, "medium": 0, "unverified": 0} for c in CAPABILITIES}

    resolved, unresolved = 0, 0
    supply_by_poly: dict[str, dict] = {}
    facilities_resolved: list[dict] = []   # long: per facility×capability rows (stamped w/ NFHS later)
    for fac in facilities:
        hit = assign_district(fac["latitude"], fac["longitude"], index)
        if not hit or not hit.get("district"):
            unresolved += 1
            continue
        resolved += 1
        key = normalize_name(hit["district"])
        agg = supply_by_poly.setdefault(key, {"raw_name": hit["district"], "facilities": 0,
                                              "maternal": 0, "public": 0, "private": 0,
                                              "caps": _new_caps()})
        agg["facilities"] += 1
        agg["maternal"] += fac["maternal_supply"]
        if fac["operator"] == "public":
            agg["public"] += 1
        elif fac["operator"] == "private":
            agg["private"] += 1

        prov = {"name": fac.get("name", "") or "", "city": fac.get("city", "") or "",
                "pincode": fac.get("pincode", "") or "", "source_url": _first_url(fac.get("source_urls", "")),
                "operator": fac.get("operator", "") or "", "unique_id": fac.get("unique_id", "")}
        for cap in CAPABILITIES:
            claim = classify_claim(fac, cap)
            conf = claim["confidence"]
            if conf in ("high", "medium", "unverified"):
                agg["caps"][cap][conf] += 1
                facilities_resolved.append({
                    **prov, "poly_key": key, "capability": cap, "claim_confidence": conf,
                    "claim_terms": "; ".join(claim["claim_terms"]),
                    "corroborating_terms": "; ".join(claim["corroborating_terms"]),
                    "capability_evidence": claim["capability_evidence"] or "",
                    "procedure_evidence": claim["procedure_evidence"] or "",
                })

    print(f"point-in-polygon: {resolved} resolved, {unresolved} unresolved "
          f"({resolved/len(facilities):.1%} coverage)")
    if has_text:
        for cap in CAPABILITIES:
            n = sum(1 for f in facilities_resolved if f["capability"] == cap)
            hi = sum(1 for f in facilities_resolved if f["capability"] == cap and f["claim_confidence"] == "high")
            md = sum(1 for f in facilities_resolved if f["capability"] == cap and f["claim_confidence"] == "medium")
            print(f"  {cap:10} graded {n:5} (high {hi}, medium {md}) — verified = high+medium")

    # 5. reconcile polygon names <-> NFHS names (on normalized key)
    nfhs_by_key = {}
    for d in nfhs:
        nfhs_by_key.setdefault(normalize_name(d["district_name"]), d)

    matched, aliased, unmatched_polys = 0, 0, []
    rows_out = []
    nfhs_matched_keys = set()
    poly_key_to_nd: dict[str, dict] = {}          # polygon key -> reconciled NFHS row (for stamping facilities)
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
            poly_key_to_nd[key] = nd
            rows_out.append(_base_row(nd, agg))
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
        rows_out.append(_base_row(d, {}))

    # Stamp each resolved facility×capability with its reconciled NFHS district, then write the long
    # per-facility CLAIM table the app/agent CITE (only facilities that reconciled to an NFHS district).
    claim_rows = []
    for fr in facilities_resolved:
        nd = poly_key_to_nd.get(fr["poly_key"])
        if nd is None:
            continue
        claim_rows.append({
            "unique_id": fr["unique_id"], "name": fr["name"], "city": fr["city"], "pincode": fr["pincode"],
            "source_url": fr["source_url"], "operator": fr["operator"],
            "district_key": normalize_name(nd["district_name"]),
            "nfhs_district": nd["district_name"].strip(), "state_ut": nd["state_ut"].strip(),
            "capability": fr["capability"], "claim_confidence": fr["claim_confidence"],
            "claim_terms": fr["claim_terms"], "corroborating_terms": fr["corroborating_terms"],
            "capability_evidence": fr["capability_evidence"], "procedure_evidence": fr["procedure_evidence"],
        })

    # district × capability aggregate (long). Every NFHS district appears for every capability —
    # including all-zero rows, so the coverage view can show the deserts, not just where supply exists.
    dc: dict[tuple, dict] = {}
    for key, agg in supply_by_poly.items():
        nd = poly_key_to_nd.get(key)
        if nd is None:
            continue
        dkey = normalize_name(nd["district_name"])
        for cap in CAPABILITIES:
            cc = agg["caps"][cap]
            row = dc.setdefault((dkey, cap), {"district_key": dkey,
                "nfhs_district": nd["district_name"].strip(), "state_ut": nd["state_ut"].strip(),
                "capability": cap, "high": 0, "medium": 0, "unverified": 0})
            row["high"] += cc["high"]; row["medium"] += cc["medium"]; row["unverified"] += cc["unverified"]
    for d in nfhs_with_no_supply:                       # zero-supply districts = candidate deserts
        dkey = normalize_name(d["district_name"])
        for cap in CAPABILITIES:
            dc.setdefault((dkey, cap), {"district_key": dkey, "nfhs_district": d["district_name"].strip(),
                "state_ut": d["state_ut"].strip(), "capability": cap, "high": 0, "medium": 0, "unverified": 0})
    dc_rows = []
    for row in dc.values():
        row["verified_supply"] = row["high"] + row["medium"]
        row["total_signal"] = row["high"] + row["medium"] + row["unverified"]
        dc_rows.append(row)

    # 6. write outputs
    with OUT_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        w.writeheader(); w.writerows(rows_out)
    with UNMATCHED_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["polygon_district", "facilities", "fuzzy_suggestion", "reason"])
        w.writerows(sorted(unmatched_polys, key=lambda x: -x[1]))
    if claim_rows:
        with FACILITY_CLAIMS_CSV.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(claim_rows[0].keys()))
            w.writeheader(); w.writerows(claim_rows)
    if dc_rows:
        with DISTRICT_CAPABILITY_CSV.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(dc_rows[0].keys()))
            w.writeheader(); w.writerows(dc_rows)

    stats = {
        "facilities_total": len(facilities),
        "facilities_resolved_pct": round(resolved / len(facilities), 4),
        "polygon_districts_with_facilities": len(supply_by_poly),
        "matched_to_nfhs": matched,
        "  of_which_via_curated_alias": aliased,
        "unmatched_polygon_districts": len(unmatched_polys),
        "nfhs_districts_total": len(nfhs),
        "nfhs_districts_with_zero_supply": len(nfhs_with_no_supply),
        "facility_capability_claims": len(claim_rows),
        "district_capability_rows": len(dc_rows),
    }
    return {"stats": stats, "rows": rows_out, "claim_rows": claim_rows, "dc_rows": dc_rows}


if __name__ == "__main__":
    import json
    out = resolve()
    print("\n=== RESOLUTION STATS ===")
    print(json.dumps(out["stats"], indent=2))
    print(f"\nwrote district base -> {OUT_CSV}")
    print(f"wrote unmatched polygon names (for manual reconciliation) -> {UNMATCHED_CSV}")
    print("\nNOTE (Data Risk R2): NFHS districts with zero resolved supply are CANDIDATE deserts, "
          "NOT confirmed — facility data is web-sourced and rural-sparse. Treat as low-confidence.")
