"""
chain.py — The deterministic cost-per-impact chain (Rung 1 spine).

Produces the GROUNDED FACTS the agent reasons over. The agent ranks/explains; it does NOT
compute these numbers. For an intervention + constraints, for each district:

    burden (NFHS-5)  ->  coverage gap (burden x low reachable supply)
                     ->  mission cost (transport + stay + reach-time, from reachability)
                     ->  need-addressed-per-cost  (the ranking metric)

`reach_fn(district_row) -> (distance_km, drive_hours) | None` is INJECTED so this module is
testable without a network (tests pass a synthetic reach_fn; step (b) passes an ORS-backed one).
"""

from __future__ import annotations

from typing import Callable

from .burden import burden_score
from .coverage import coverage_gap, trust_weighted_supply
from .cost import mission_cost, CostAssumptions, DEFAULTS
from .impact import need_addressed_per_cost
from .data_access import load_districts

# Which supply column counts as "relevant" reachable supply per intervention.
INTERVENTION_SUPPLY_COLUMN = {
    "maternal_health": "maternal_supply_facilities",
    "anaemia": "facilities",
    "child_nutrition": "facilities",
}


def _data_confidence(burden_conf: str, total_facilities: int, supply: float,
                     verified_supply: int = None, unverified: int = 0) -> str:
    """Confidence in the coverage signal, keyed off DATA PRESENCE (total facilities), so it stays
    consistent with the confirmed/candidate tiering. Surfaces Data Risk R2 only when truly no data.

    When per-facility CLAIM verification is available (verified_supply not None), the label reflects
    whether supply is text-CORROBORATED, just the noisy flag's word, or absent — the summit's
    'claims to verify, not ground truth' discipline."""
    if total_facilities == 0:
        return "low (no facility data — candidate desert or data gap, R2)"
    if verified_supply is not None:                      # maternal: claim-aware labelling
        if verified_supply > 0:
            base = f"high ({verified_supply} facilities' maternal capability corroborated by their own text)"
            return base if burden_conf not in ("none", "partial") else base + f"; burden {burden_conf}"
        if unverified > 0:
            return ("low–medium (supply is an UNVERIFIED claim — the ob/gyn flag is set but no facility's "
                    "own capability/procedure text corroborates it; verify before acting)")
        return "medium (facilities present but none claim this service — measured gap)"
    if supply == 0:
        return "medium (facilities present but none offer this service — measured gap)"
    if burden_conf in ("none", "partial"):
        return f"medium (burden {burden_conf})"
    return "high"


def rank_districts(intervention: str,
                   reach_fn: Callable[[dict], tuple | None],
                   team_size: int = None, days: int = None,
                   top_n: int = 10,
                   assumptions: CostAssumptions = DEFAULTS,
                   districts: list[dict] = None,
                   count_unverified: bool = False) -> dict:
    """
    Rank districts by need-addressed-per-cost for an intervention, split into TWO TIERS so the
    agent never silently ignores data gaps — it surfaces them as work to be done:

      confirmed_gaps : total facilities > 0 -> we HAVE facility data, so a remaining gap is a
                       MEASURED under-supply. These are actionable recommendations.
      candidate_gaps : total facilities == 0 -> NO facility data resolved (Data Risk R2). Could be
                       a true desert OR a data gap. Surfaced as "investigate / data needs work",
                       not crowned as a confident #1.
      excluded       : burden uncomputable, or unreachable. Recorded with a reason, never dropped.

    Each tier is ranked independently (top_n applies per tier). Returns a dict with these keys.
    """
    supply_col = INTERVENTION_SUPPLY_COLUMN.get(intervention, "facilities")
    rows = districts if districts is not None else load_districts()

    confirmed, candidate, excluded = [], [], []
    for d in rows:
        b = burden_score(d, intervention)
        total_facilities = int(d.get("facilities") or 0)
        # For maternal, SUPPLY is TRUST-WEIGHTED (high·1 + medium·0.6 [+ unverified·0.3 if opted in])
        # rather than the raw flag count — so the coverage gap reflects verified evidence, not claims.
        if intervention == "maternal_health":
            claim_breakdown = {
                "high": int(d.get("maternal_claim_high") or 0),
                "medium": int(d.get("maternal_claim_medium") or 0),
                "unverified": int(d.get("maternal_claim_unverified") or 0)}
            verified_supply = claim_breakdown["high"] + claim_breakdown["medium"]
            supply = trust_weighted_supply(claim_breakdown["high"], claim_breakdown["medium"],
                                           claim_breakdown["unverified"], count_unverified)
        else:
            claim_breakdown = None
            verified_supply = None
            supply = int(d.get(supply_col) or 0)
        reach = reach_fn(d)
        row = {"district": d["nfhs_district"].strip(), "state": d["state_ut"].strip(),
               "burden": b, "supply": supply, "total_facilities": total_facilities,
               "verified_supply": verified_supply, "claim_breakdown": claim_breakdown}
        if b["score"] is None or reach is None:
            row["metric"] = None
            row["excluded_reason"] = "burden unavailable" if b["score"] is None else "unreachable"
            excluded.append(row)
            continue
        distance_km, drive_hours = reach
        gap = coverage_gap(b["score"], supply)
        cost = mission_cost(distance_km, drive_hours, team_size, days, assumptions)
        row.update({
            "metric": need_addressed_per_cost(gap["gap"], cost["total_usd"]),
            "gap": gap, "cost": cost,
            "reach": {"distance_km": distance_km, "drive_hours": drive_hours},
            "data_confidence": _data_confidence(b["confidence"], total_facilities, supply,
                                                verified_supply,
                                                (claim_breakdown or {}).get("unverified", 0)),
        })
        if total_facilities == 0:
            row["tier"] = "candidate_gap"
            candidate.append(row)
        else:
            row["tier"] = "confirmed_gap"
            confirmed.append(row)

    def _ranked(lst):
        lst.sort(key=lambda r: (r["metric"] is not None, r["metric"] or 0), reverse=True)
        for i, r in enumerate(lst, 1):
            r["tier_rank"] = i
        return lst[:top_n] if top_n else lst

    return {
        "intervention": intervention,
        "confirmed_gaps": _ranked(confirmed),
        "candidate_gaps": _ranked(candidate),
        "excluded": excluded,
    }
