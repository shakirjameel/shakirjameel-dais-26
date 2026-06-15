"""
burden.py — Burden scoring + impact estimation from NFHS-5 indicators.

Honesty rules baked in (these ARE the evidence-and-uncertainty rubric points):
  * Suppressed values ('*') -> None, treated as MISSING, never zero.
  * Parenthesized estimates like '(29.5)' -> low-confidence flag, value usable but marked.
  * Every score reports which indicators were missing, so the agent can disclose it.

INTERVENTION_INDICATORS is filled in once the facilities schema + chosen
specialty are known. Each entry maps an intervention to the NFHS-5 columns that
proxy its burden, with a direction: 'high_is_worse' or 'low_is_worse'.
"""

from dataclasses import dataclass


# direction semantics:
#   high_is_worse: a higher indicator value = more burden (e.g. anaemia %)
#   low_is_worse:  a lower value = more burden (e.g. % institutional delivery)
INTERVENTION_INDICATORS = {
    # PLACEHOLDER mapping — replace column names with real NFHS-5 columns at build.
    "maternal_health": [
        ("institutional_delivery_pct", "low_is_worse"),
        ("anc_4plus_visits_pct", "low_is_worse"),
    ],
    "anaemia": [
        ("women_anaemic_pct", "high_is_worse"),
        ("children_anaemic_pct", "high_is_worse"),
    ],
    "child_nutrition": [
        ("children_stunted_pct", "high_is_worse"),
        ("children_underweight_pct", "high_is_worse"),
    ],
}


def parse_nfhs_value(raw):
    """
    Normalize a raw NFHS cell into (value, flag).
      flag in {None, 'suppressed', 'low_confidence'}.
    '*' -> (None, 'suppressed');  '(29.5)' -> (29.5, 'low_confidence');
    '45.2' -> (45.2, None);  '' / None -> (None, 'suppressed').
    """
    if raw is None:
        return None, "suppressed"
    s = str(raw).strip()
    if s == "" or s == "*":
        return None, "suppressed"
    low_conf = False
    if s.startswith("(") and s.endswith(")"):
        low_conf = True
        s = s[1:-1].strip()
    try:
        return float(s), ("low_confidence" if low_conf else None)
    except ValueError:
        return None, "suppressed"


def _normalize(value, direction):
    """Map a percentage indicator to a 0..1 burden contribution."""
    if value is None:
        return None
    v = max(0.0, min(100.0, value)) / 100.0
    return v if direction == "high_is_worse" else (1.0 - v)


def burden_score(district_row: dict, intervention: str) -> dict:
    """
    Composite burden 0..1 for an intervention in a district.
    Averages available indicator contributions; reports missing + low-confidence.
    Returns value=None only if NO indicator is usable (district then flagged, not dropped).
    """
    spec = INTERVENTION_INDICATORS.get(intervention)
    if not spec:
        raise KeyError(f"Unknown intervention '{intervention}'. "
                       f"Known: {list(INTERVENTION_INDICATORS)}")

    contributions, missing, low_conf = [], [], []
    for col, direction in spec:
        value, flag = parse_nfhs_value(district_row.get(col))
        if flag == "suppressed":
            missing.append(col)
            continue
        if flag == "low_confidence":
            low_conf.append(col)
        c = _normalize(value, direction)
        if c is not None:
            contributions.append(c)

    score = round(sum(contributions) / len(contributions), 4) if contributions else None
    return {
        "intervention": intervention,
        "score": score,                       # 0..1 or None
        "indicators_used": len(contributions),
        "indicators_total": len(spec),
        "missing_indicators": missing,         # disclose these
        "low_confidence_indicators": low_conf, # disclose these
        "confidence": _confidence(len(contributions), len(spec), low_conf),
    }


def _confidence(used, total, low_conf):
    if used == 0:
        return "none"
    if used < total or low_conf:
        return "partial"
    return "full"


def people_reached(district_row: dict, burden: dict,
                   served_fraction: float = 0.15) -> dict:
    """
    Estimate people a mission could reach. HEURISTIC — always hedged.
    method: population * burden_score * served_fraction
            (served_fraction = the share of the burdened population a single
             time-boxed mission can plausibly serve; an assumption, labeled).
    Returns value + method string + confidence; never an unhedged number.
    """
    pop = district_row.get("population")
    score = burden.get("score")
    if pop is None or score is None:
        return {"value": None, "method": "population * burden * served_fraction",
                "confidence": "none",
                "note": "population or burden score unavailable — not estimated"}
    val = pop * score * served_fraction
    return {
        "value": int(val),
        "method": f"population({pop}) * burden({score}) * served_fraction({served_fraction})",
        "served_fraction_assumption": served_fraction,
        "confidence": burden.get("confidence", "partial"),
        "note": "heuristic estimate — not a measured count",
    }


def impact_per_cost(people: dict, cost_total: float) -> float | None:
    """Ranking metric: estimated people reached per USD. None if not computable."""
    pv = people.get("value")
    if pv is None or not cost_total or cost_total <= 0:
        return None
    return round(pv / cost_total, 6)
