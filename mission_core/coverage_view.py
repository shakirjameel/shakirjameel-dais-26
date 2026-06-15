"""
coverage_view.py — Track-2's primary aggregate: trust-weighted facility coverage across geography.

For a chosen CAPABILITY + geography, rank each district by how big and how REAL its care gap is —
distinguishing a true care desert from a data-poor region (the literal Track-2 question). The
supply side is the trust-weighted claim aggregate (`district_capability`); for maternity we also
weight by NFHS-5 burden (the only capability with a demand indicator), so the gap is need-aware.

The agent reasons OVER these grounded rows; it does not compute them.
"""

from __future__ import annotations

from .data_access import load_district_capability, load_districts, normalize_name
from .burden import burden_score
from .coverage import (trust_weighted_supply, supply_adequacy, gap_classification,
                       data_confidence, SUPPLY_HALF_SATURATION)

# Only maternity has an NFHS-5 burden indicator; other capabilities rank on supply scarcity alone
# (clearly labelled "no burden indicator for this capability").
CAPABILITY_BURDEN = {"maternity": "maternal_health"}
_ALIAS = {"maternal_health": "maternity"}


def _burden_by_district(intervention: str) -> dict:
    out = {}
    for d in load_districts():
        out[normalize_name(d["nfhs_district"])] = burden_score(d, intervention)
    return out


def _facilities_by_district() -> dict:
    """Total resolved facilities per district — a tiebreaker so that among equal-desert districts,
    those with MORE health infrastructure but none verifiably doing the capability surface first
    (a sharper, more actionable gap than a district with little infrastructure of any kind)."""
    return {normalize_name(d["nfhs_district"]): int(d.get("facilities") or 0) for d in load_districts()}


def coverage_by_geography(capability: str, state: str = None, count_unverified: bool = False,
                          half_sat: float = SUPPLY_HALF_SATURATION, top_n: int = None) -> list[dict]:
    """Ranked district coverage rows for a capability (and optionally one state). Each row carries
    the trust-weighted supply, a gap classification (confirmed / unverified-claims / no-claim desert),
    burden (maternity only) and a desert score = burden·(1−adequacy) [maternity] or (1−adequacy)
    [supply scarcity] — highest = biggest, most-confident gap."""
    capability = _ALIAS.get(capability, capability)
    rows = load_district_capability(capability, state)
    burden_intervention = CAPABILITY_BURDEN.get(capability)
    burden_by_key = _burden_by_district(burden_intervention) if burden_intervention else {}
    facilities_by_key = _facilities_by_district()

    out = []
    for r in rows:
        tws = trust_weighted_supply(r["high"], r["medium"], r["unverified"], count_unverified)
        adeq = supply_adequacy(tws, half_sat)
        cls = gap_classification(r["high"], r["medium"], r["unverified"])
        total_signal = r["total_signal"]
        b = burden_by_key.get(r["district_key"])
        bscore = b["score"] if b else None
        desert = round((bscore if bscore is not None else 1.0) * (1.0 - adeq), 4)
        out.append({
            "district": r["nfhs_district"].strip(), "state": r["state_ut"].strip(),
            "capability": capability,
            "high": r["high"], "medium": r["medium"], "unverified": r["unverified"],
            "verified_supply": r["verified_supply"],
            "trust_weighted_supply": tws,
            "evidence_strength": tws,   # alias: this is EVIDENCE strength, not care quality
            "data_confidence": data_confidence(
                facilities_by_key.get(r["district_key"], 0), total_signal, r["verified_supply"]),
            "supply_adequacy": round(adeq, 4),
            "trust_ratio": round(r["verified_supply"] / total_signal, 3) if total_signal else None,
            "gap_classification": cls,
            "burden": bscore, "burden_confidence": (b["confidence"] if b else None),
            "has_burden": bscore is not None,
            "total_facilities": facilities_by_key.get(r["district_key"], 0),
            "desert_score": desert,
        })
    # rank by desert score; tiebreak by health infrastructure present (sharper, more actionable gap)
    out.sort(key=lambda x: (x["desert_score"], x["total_facilities"]), reverse=True)
    for i, x in enumerate(out, 1):
        x["rank"] = i
    return out[:top_n] if top_n else out


def coverage_summary(rows: list[dict]) -> dict:
    """Roll-up for a geography: how many districts are confirmed vs unverified-claims vs deserts."""
    cls = {"confirmed_coverage": 0, "unverified_claims": 0, "no_claim_desert": 0}
    for r in rows:
        cls[r["gap_classification"]] = cls.get(r["gap_classification"], 0) + 1
    return {
        "districts": len(rows),
        "confirmed_coverage": cls["confirmed_coverage"],
        "unverified_claims": cls["unverified_claims"],
        "no_claim_desert": cls["no_claim_desert"],
        "verified_facilities": sum(r["verified_supply"] for r in rows),
    }
