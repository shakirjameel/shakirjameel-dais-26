"""
coverage.py — Coverage gap: high burden AND low reachable relevant supply = a desert.

The gap is what a mission would ADDRESS. It combines the burden (need) with how much relevant,
reachable supply already exists. Supply adequacy saturates (each additional facility helps less),
governed by a named, adjustable threshold.
"""

# Number of relevant reachable facilities at which a district is ~half "adequately served".
# A mission-planning assumption (adjustable), NOT a clinical standard.
SUPPLY_HALF_SATURATION = 3.0


def supply_adequacy(reachable_relevant_supply: float, half_sat: float = SUPPLY_HALF_SATURATION) -> float:
    """0..1 saturating curve: 0 facilities -> 0.0 adequacy; many -> approaches 1.0."""
    s = max(0.0, reachable_relevant_supply)
    return s / (s + half_sat)


def coverage_gap(burden: float | None, reachable_relevant_supply: float,
                 half_sat: float = SUPPLY_HALF_SATURATION) -> dict:
    """
    gap = burden * (1 - supply_adequacy). High when burden is high AND reachable supply is low.
    Returns {gap, supply_adequacy, reachable_supply, half_saturation} or gap=None if burden None.
    """
    if burden is None:
        return {"gap": None, "supply_adequacy": None,
                "reachable_supply": reachable_relevant_supply, "half_saturation": half_sat,
                "note": "burden unavailable — gap not computed"}
    adeq = supply_adequacy(reachable_relevant_supply, half_sat)
    return {
        "gap": round(burden * (1.0 - adeq), 4),
        "supply_adequacy": round(adeq, 4),
        "reachable_supply": reachable_relevant_supply,
        "half_saturation": half_sat,
    }


# --------------------------------------------------------------------------- trust-weighting
# Weights for "trust-weighted supply": a corroborated claim counts fully, a claimed-only facility
# partially, and a flag-only ('unverified') facility nothing — UNLESS the planner opts to count
# unverified claims (the honesty toggle), in which case it contributes a discounted amount. These
# are mission-planning assumptions (adjustable), not clinical standards.
TRUST_WEIGHTS = {"high": 1.0, "medium": 0.6, "unverified": 0.3}


def trust_weighted_supply(high: int, medium: int, unverified: int,
                          count_unverified: bool = False) -> float:
    """Trust-weighted facility supply for a district×capability. high·1.0 + medium·0.6, plus
    unverified·0.3 ONLY when count_unverified is on. This is the 'trust-weighted evidence' the
    Track-2 coverage aggregate is built on — verified evidence outweighs an uncorroborated claim."""
    s = high * TRUST_WEIGHTS["high"] + medium * TRUST_WEIGHTS["medium"]
    if count_unverified:
        s += unverified * TRUST_WEIGHTS["unverified"]
    return round(s, 3)


def gap_classification(high: int, medium: int, unverified: int) -> str:
    """Distinguish a REAL care gap from a DATA-poor region (the literal Track-2 question):
      confirmed_coverage — at least one facility's claim is text-verified (high or medium).
      unverified_claims  — facilities assert the capability (flag/specialty) but NONE is corroborated
                           by their own text → a claim to verify, not a confirmed service.
      no_claim_desert    — no facility here even claims this capability → a candidate care desert."""
    if high + medium > 0:
        return "confirmed_coverage"
    if unverified > 0:
        return "unverified_claims"
    return "no_claim_desert"
