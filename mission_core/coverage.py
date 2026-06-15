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


def data_confidence(total_facilities: int, total_signal: int, verified_supply: int) -> str:
    """Documentation / data-density confidence for a district's coverage signal — reported SEPARATELY
    from the coverage gap, and described as EVIDENCE STRENGTH, not care quality. It tells a real care
    desert (people present, no verified care) apart from a data-poor region (little facility data of
    any kind), so a thin-documentation (often rural/public) district is not read as 'low coverage'
    when it is really 'low information' (R1/R2)."""
    if total_facilities == 0:
        return "data-poor"          # nothing resolved here — could be a real desert OR missing data
    if verified_supply > 0:
        return "well-evidenced"     # at least one text-verified provider
    if total_signal > 0:
        return "claims-only"        # facilities assert it but none corroborated — verify
    return "documented-gap"          # facilities present, none even claim this service


def trust_weighted_supply(high: int, medium: int, unverified: int,
                          count_unverified: bool = False) -> float:
    """Trust-weighted facility supply for a district×capability. high·1.0 + medium·0.6, plus
    unverified·0.3 ONLY when count_unverified is on. This is the 'trust-weighted evidence' the
    Track-2 coverage aggregate is built on — verified evidence outweighs an uncorroborated claim."""
    s = high * TRUST_WEIGHTS["high"] + medium * TRUST_WEIGHTS["medium"]
    if count_unverified:
        s += unverified * TRUST_WEIGHTS["unverified"]
    return round(s, 3)


# State-level map fill thresholds (adjustable, like COST_ASSUMPTIONS). Applied to a state's MEAN
# district desert score when the state has some verified coverage: lower mean = better coverage.
DESERT_SHADE_THRESHOLDS = {"strong": 0.34, "moderate": 0.5}   # <strong -> strong; <moderate -> moderate; else weaker


def state_fill_category(*, lit: bool, n_confirmed: int, n_claim_only: int,
                        mean_desert_score: float | None) -> str:
    """The map fill for a whole state (chosen, documented rollup rule — see plan):
      no_data         — no facilities at all (never score 0).
      no_claim_desert — has facilities but NO district claims the capability anywhere.
      claim_only      — claimed somewhere but NOTHING text-verified statewide.
      strong/moderate/weaker — by MEAN district desert score where verified coverage exists.
    (We reserve red for genuinely no-claim states rather than 'any desert district', which would
    flood the map red given our data density and kill the gradient.)"""
    if not lit:
        return "no_data"
    if n_confirmed == 0:
        return "claim_only" if n_claim_only > 0 else "no_claim_desert"
    md = mean_desert_score if mean_desert_score is not None else 1.0
    if md < DESERT_SHADE_THRESHOLDS["strong"]:
        return "strong"
    if md < DESERT_SHADE_THRESHOLDS["moderate"]:
        return "moderate"
    return "weaker"


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
