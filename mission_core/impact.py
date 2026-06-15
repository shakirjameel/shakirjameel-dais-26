"""
impact.py — The ranking metric: need addressed per dollar.

HONESTY NOTE on "people reached": an ABSOLUTE people-reached estimate needs a district
POPULATION denominator, which the provided dataset does NOT contain (see data/DATA_RISKS.md,
"population denominators"). So we do NOT fabricate it. Two functions:

  - need_addressed_per_cost(): the ranking metric we CAN compute now — burden-weighted coverage
    gap per dollar (a relative measure, 0..1 numerator). This is what rank_districts uses.
  - people_reached(): population-gated. Returns a hedged estimate IF a population is supplied,
    else None + a caveat. Slots in unchanged once a Census/WorldPop denominator is added.
"""


def need_addressed_per_cost(gap: float | None, cost_total: float) -> float | None:
    """Ranking metric: coverage gap (need a mission would address) per USD. None if uncomputable."""
    if gap is None or not cost_total or cost_total <= 0:
        return None
    return round(gap / cost_total, 8)


def people_reached(gap: float | None, population: float | None,
                   served_fraction: float = 0.15) -> dict:
    """
    Optional ABSOLUTE estimate (needs population). HEURISTIC — always hedged.
    method: population * gap * served_fraction. Returns None + caveat if population absent.
    """
    if gap is None or population is None:
        return {"value": None, "confidence": "none",
                "method": "population * gap * served_fraction",
                "note": "population denominator unavailable — not estimated (see DATA_RISKS)"}
    return {
        "value": int(population * gap * served_fraction),
        "method": f"population({population}) * gap({gap}) * served_fraction({served_fraction})",
        "served_fraction_assumption": served_fraction,
        "confidence": "partial",
        "note": "heuristic estimate — not a measured count",
    }
