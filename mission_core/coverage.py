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
