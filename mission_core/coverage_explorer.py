"""
coverage_explorer.py — Regional coverage aggregates (Track 2: Medical Desert Planner).

Reachability/cost only exist for the staging-city candidate set, so this layer deliberately
uses ONLY burden (NFHS-5) + supply (resolved facilities) — making it work for ALL districts
nationwide. It answers "where are the gaps, and how confident are we they're real?" without the
mission-cost layer.

The per-region dict returned here is the HAND-OFF CONTRACT the facility drill-down consumes:
given (intervention, state, district) it expands `n_facilities` into the trust-scored records.
"""

from __future__ import annotations

from .burden import burden_score
from .coverage import coverage_gap
from .chain import INTERVENTION_SUPPLY_COLUMN
from .data_access import load_districts


def coverage_status(total_facilities: int, relevant_supply: int, burden_conf: str) -> tuple[str, str]:
    """(status, human label). Mirrors chain.py tiering but without reachability.
       confirmed = we have facility data here; candidate = none resolved (real desert OR data gap)."""
    if total_facilities == 0:
        return "candidate", "no facility data — real desert OR data-poor (verify)"
    if relevant_supply == 0:
        return "confirmed", "facilities present but none offer this service — measured gap"
    if burden_conf in ("none", "partial"):
        return "confirmed", f"measured, but burden {burden_conf}"
    return "confirmed", "measured"


def district_coverage(d: dict, intervention: str) -> dict:
    """Coverage aggregate for one district. burden + supply -> gap + confidence. No reachability."""
    supply_col = INTERVENTION_SUPPLY_COLUMN.get(intervention, "facilities")
    b = burden_score(d, intervention)
    total_facilities = int(d.get("facilities") or 0)
    relevant_supply = int(d.get(supply_col) or 0)
    cg = coverage_gap(b["score"], relevant_supply)
    status, label = coverage_status(total_facilities, relevant_supply, b["confidence"])
    return {
        "district": d["nfhs_district"].strip(),
        "state": d["state_ut"].strip(),
        "intervention": intervention,
        "burden": b["score"],
        "burden_confidence": b["confidence"],
        "indicators_used": b["indicators_used"],
        "indicators_total": b["indicators_total"],
        "missing_indicators": b.get("missing_indicators", []),
        "n_facilities": total_facilities,
        "relevant_supply": relevant_supply,
        "supply_adequacy": cg["supply_adequacy"],
        "gap": cg["gap"],
        "coverage_status": status,        # "confirmed" | "candidate"
        "confidence_label": label,
    }


def list_states() -> list[str]:
    """All states/UTs present in the district base, sorted."""
    return sorted({d["state_ut"].strip() for d in load_districts() if d.get("state_ut")})


def regional_coverage(intervention: str, state: str | None = None) -> dict:
    """All districts (optionally filtered to one state) ranked worst-gap-first, plus a rollup.
       Returns {rows, summary}. `rows` are the hand-off aggregates; `summary` is region-level."""
    rows = []
    for d in load_districts():
        if state and d["state_ut"].strip() != state:
            continue
        rows.append(district_coverage(d, intervention))

    scored = [r for r in rows if r["gap"] is not None]
    confirmed = [r for r in scored if r["coverage_status"] == "confirmed"]
    candidate = [r for r in scored if r["coverage_status"] == "candidate"]
    # rank worst gap first within each tier
    confirmed.sort(key=lambda r: r["gap"], reverse=True)
    candidate.sort(key=lambda r: r["gap"], reverse=True)

    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
    summary = {
        "scope": state or "All India",
        "districts": len(rows),
        "with_burden": len(scored),
        "confirmed_gaps": len(confirmed),
        "candidate_gaps": len(candidate),
        "total_facilities": sum(r["n_facilities"] for r in rows),
        "mean_burden": mean([r["burden"] for r in scored]),
        "mean_gap": mean([r["gap"] for r in scored]),
        "data_coverage_pct": round(len(confirmed) / len(scored), 3) if scored else None,
    }
    return {"confirmed": confirmed, "candidate": candidate, "summary": summary}
