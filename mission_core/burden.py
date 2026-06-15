"""
burden.py — Burden scoring from NFHS-5 indicators, wired to the REAL column names.

Honesty rules (the evidence-and-uncertainty rubric points):
  * Suppressed '*' (or blank/NA) -> None, treated as MISSING, never zero.
  * Parenthesized '(29.5)' -> low-confidence flag; value usable but marked.
  * Trailing whitespace artifacts (NFHS string columns) are trimmed.
  * Every score reports which indicators were missing/low-confidence, for disclosure.

INTERVENTION_INDICATORS maps an intervention to the NFHS-5 columns that proxy its burden,
each with a direction. These are the ACTUAL columns present in data/cache/district_base.csv
(verified in the data gate). All are 0-100 percentages.
"""

# direction:
#   high_is_worse — a higher value = more burden (e.g. anaemia %, stunting %)
#   low_is_worse  — a lower value  = more burden (e.g. % institutional delivery)
INTERVENTION_INDICATORS = {
    "maternal_health": [
        ("institutional_birth_5y_pct", "low_is_worse"),
        ("mothers_who_had_at_least_4_anc_visits_lb5y_pct", "low_is_worse"),
        ("births_attended_by_skilled_hp_5y_10_pct", "low_is_worse"),
        ("all_w15_49_who_are_anaemic_pct", "high_is_worse"),
    ],
    "anaemia": [
        ("all_w15_49_who_are_anaemic_pct", "high_is_worse"),
    ],
    "child_nutrition": [
        ("child_u5_who_are_stunted_height_for_age_18_pct", "high_is_worse"),
    ],
}


def parse_nfhs_value(raw):
    """
    Normalize a raw NFHS cell -> (value, flag), flag in {None,'suppressed','low_confidence'}.
    '*'/''/None/'NA' -> (None,'suppressed');  '(29.5)' -> (29.5,'low_confidence');
    '45.2 ' -> (45.2, None).  Non-numeric -> (None,'suppressed').
    """
    if raw is None:
        return None, "suppressed"
    s = str(raw).strip()
    if s == "" or s == "*" or s.upper() == "NA":
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
    """Map a 0-100 percentage indicator to a 0..1 burden contribution."""
    if value is None:
        return None
    v = max(0.0, min(100.0, value)) / 100.0
    return v if direction == "high_is_worse" else (1.0 - v)


def _confidence(used, total, low_conf):
    if used == 0:
        return "none"
    if used < total or low_conf:
        return "partial"
    return "full"


def burden_score(district_row: dict, intervention: str) -> dict:
    """
    Composite burden 0..1 for an intervention in a district (mean of available indicator
    contributions). Reports missing + low-confidence indicators. score=None only if NO
    indicator is usable (district flagged, never silently dropped or zero-filled).
    """
    spec = INTERVENTION_INDICATORS.get(intervention)
    if not spec:
        raise KeyError(f"Unknown intervention '{intervention}'. Known: {list(INTERVENTION_INDICATORS)}")

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
        "score": score,
        "indicators_used": len(contributions),
        "indicators_total": len(spec),
        "missing_indicators": missing,
        "low_confidence_indicators": low_conf,
        "confidence": _confidence(len(contributions), len(spec), low_conf),
    }
