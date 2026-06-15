"""
sensitivity.py — is the ranking robust, or an artifact of a guessed cost coefficient?

The cost-per-impact ranking depends on assumptions (surgeon-day value, per-diem, transport/km).
The sharpest attack is "change one coefficient and your whole story flips." This module answers
it: sweep a coefficient across a plausible range, re-run the ranking at each value, and report
whether the #1 pick holds — turning honesty into a capability, not an apology.

Deterministic, no LLM. reach_fn + districts are injected (testable; the tool wires the real ones).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .cost import DEFAULTS
from .chain import rank_districts

# coefficient -> (default, sweep values, human label). Ranges span plausible real-world norms.
COEFFICIENTS = {
    "surgeon_day_value_usd": (800.0, [200, 400, 600, 800, 1000, 1200, 1600, 2000], "surgeon-day value ($)"),
    "per_diem_usd":          (60.0, [30, 45, 60, 90, 120, 150], "per-diem ($/person/day)"),
    "transport_per_km_usd":  (0.35, [0.15, 0.25, 0.35, 0.50, 0.75, 1.00], "transport ($/km)"),
}


def _top_at(intervention, reach_fn, assumptions, team_size, days, districts, tier):
    res = rank_districts(intervention, reach_fn, team_size=team_size, days=days,
                         top_n=3, assumptions=assumptions, districts=districts)
    rows = res[tier]
    return ([r["district"] for r in rows], rows[0]["metric"] if rows else None)


def sweep(intervention: str, reach_fn: Callable, coefficient: str,
          values: list | None = None, team_size: int = 6, days: int = 7,
          districts: list[dict] | None = None, tier: str = "confirmed_gaps") -> dict:
    """
    Re-rank across coefficient values; report whether the #1 pick is robust and where it flips.
    Returns {coefficient, default, baseline_top, points[], robust_values[], flips[]}.
    """
    if coefficient not in COEFFICIENTS:
        return {"error": f"unknown coefficient '{coefficient}'", "valid": list(COEFFICIENTS)}
    default, default_values, label = COEFFICIENTS[coefficient]
    values = values or default_values

    points = []
    for v in values:
        order, metric = _top_at(intervention, reach_fn, replace(DEFAULTS, **{coefficient: v}),
                                team_size, days, districts, tier)
        points.append({"value": v, "top_district": order[0] if order else None,
                       "top_metric": metric, "order_top3": order})

    baseline_order, _ = _top_at(intervention, reach_fn, replace(DEFAULTS, **{coefficient: default}),
                                team_size, days, districts, tier)
    baseline_top = baseline_order[0] if baseline_order else None

    robust_values = [p["value"] for p in points if p["top_district"] == baseline_top]
    flips = [{"value": p["value"], "new_top": p["top_district"]}
             for p in points if p["top_district"] != baseline_top]

    return {
        "coefficient": coefficient, "label": label, "default": default,
        "baseline_top": baseline_top,
        "robust_range": (min(robust_values), max(robust_values)) if robust_values else None,
        "flips": flips,
        "verdict": ("robust across the full swept range" if not flips else
                    f"#1 holds for {label} in [{min(robust_values)}, {max(robust_values)}]; "
                    f"flips at {flips[0]['value']} -> {flips[0]['new_top']}"),
        "points": points,
    }
