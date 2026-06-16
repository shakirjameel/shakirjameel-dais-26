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
from .burden import capability_demand
from .coverage import (trust_weighted_supply, supply_adequacy, gap_classification,
                       state_fill_category, SUPPLY_HALF_SATURATION)
from .geo_names import list_topo_states, from_topo_state, DEFAULT_ORIGIN

_ALIAS = {"maternal_health": "maternity"}


def _demand_by_district(capability: str) -> dict:
    """Per-district capability DEMAND (NFHS-5) — honest gradient; demand_available=False where no
    NFHS proxy exists (emergency/trauma)."""
    return {normalize_name(d["nfhs_district"]): capability_demand(d, capability) for d in load_districts()}


def _facilities_by_district() -> dict:
    """Total resolved facilities per district — a tiebreaker so that among equal-desert districts,
    those with MORE health infrastructure but none verifiably doing the capability surface first
    (a sharper, more actionable gap than a district with little infrastructure of any kind)."""
    return {normalize_name(d["nfhs_district"]): int(d.get("facilities") or 0) for d in load_districts()}


def coverage_by_geography(capability: str, state: str = None, count_unverified: bool = False,
                          half_sat: float = SUPPLY_HALF_SATURATION, top_n: int = None) -> list[dict]:
    """Ranked district coverage rows for a capability (and optionally one state). Each row carries
    trust-weighted supply, a gap classification (confirmed / unverified-claims / no-claim desert),
    measured DEMAND (NFHS-5, where it exists; honest 'supply-scarcity only' where it doesn't), and a
    desert score = demand·(1−adequacy) [demand available] or (1−adequacy) [scarcity] — highest = the
    biggest, most-confident gap. Also exposes accepts-volunteers + beds for the optimizer."""
    capability = _ALIAS.get(capability, capability)
    rows = load_district_capability(capability, state)
    demand_by_key = _demand_by_district(capability)
    facilities_by_key = _facilities_by_district()

    out = []
    for r in rows:
        tws = trust_weighted_supply(r["high"], r["medium"], r["unverified"], count_unverified)
        adeq = supply_adequacy(tws, half_sat)
        cls = gap_classification(r["high"], r["medium"], r["unverified"])
        total_signal = r["total_signal"]
        dm = demand_by_key.get(r["district_key"]) or {}
        dscore = dm.get("score")
        demand_available = bool(dm.get("demand_available"))
        desert = round((dscore if demand_available else 1.0) * (1.0 - adeq), 4)
        out.append({
            "district_key": r["district_key"],
            "district": r["nfhs_district"].strip(), "state": r["state_ut"].strip(),
            "capability": capability,
            "high": r["high"], "medium": r["medium"], "unverified": r["unverified"],
            "verified_supply": r["verified_supply"],
            "accepts_volunteers": r.get("accepts_volunteers", 0), "verified_beds": r.get("verified_beds", 0),
            "trust_weighted_supply": tws,
            "supply_adequacy": round(adeq, 4),
            "trust_ratio": round(r["verified_supply"] / total_signal, 3) if total_signal else None,
            "gap_classification": cls,
            # demand (kept under burden* keys for back-compat) — honest gradient
            "burden": dscore, "burden_confidence": dm.get("confidence"), "has_burden": demand_available,
            "demand_available": demand_available, "demand_note": dm.get("note"),
            "total_facilities": facilities_by_key.get(r["district_key"], 0),
            "desert_score": desert,
        })
    # rank by desert score; tiebreak by health infrastructure present (sharper, more actionable gap)
    out.sort(key=lambda x: (x["desert_score"], x["total_facilities"]), reverse=True)
    for i, x in enumerate(out, 1):
        x["rank"] = i
    return out[:top_n] if top_n else out


def state_rollup(capability: str, count_unverified: bool = False) -> list[dict]:
    """One row per MAP topology state (all 36), for the country choropleth + stat rail. Reuses the
    per-district coverage and rolls it up; states with no facilities at all render 'no_data' (never
    score 0). `fill_category` is the map colour (see coverage.state_fill_category)."""
    rows = coverage_by_geography(capability, None, count_unverified)
    our_states = sorted({r["state"] for r in rows})
    by_state: dict[str, list] = {}
    for r in rows:
        by_state.setdefault(r["state"], []).append(r)

    out = []
    for st_nm in list_topo_states():
        our = from_topo_state(st_nm, our_states)
        drs = by_state.get(our, []) if our else []
        total_fac = sum(r["total_facilities"] for r in drs)
        n_conf = sum(1 for r in drs if r["gap_classification"] == "confirmed_coverage")
        n_claim = sum(1 for r in drs if r["gap_classification"] == "unverified_claims")
        n_des = sum(1 for r in drs if r["gap_classification"] == "no_claim_desert")
        data_bearing = [r["desert_score"] for r in drs if r["total_facilities"] > 0]
        mean_desert = round(sum(data_bearing) / len(data_bearing), 4) if data_bearing else None
        lit = total_fac > 0
        out.append({
            "st_nm": st_nm, "our_state": our, "lit": lit,
            "n_districts": len(drs), "total_facilities": total_fac,
            "verified_facilities": sum(r["verified_supply"] for r in drs),
            "n_confirmed": n_conf, "n_claim_only": n_claim, "n_desert": n_des,
            "mean_desert_score": mean_desert,
            "fill_category": state_fill_category(lit=lit, n_confirmed=n_conf,
                                                 n_claim_only=n_claim, mean_desert_score=mean_desert),
        })
    return out


def optimize(capability: str, state: str = None, origin: str = DEFAULT_ORIGIN,
             team_size: int = 6, days: int = 7, patients_per_volunteer_day: float = None,
             addressable_need_units: float = None, count_unverified: bool = False,
             auto_days: bool = False, top_n: int = None) -> dict:
    """Deployment optimizer: for a team of `team_size` specialised in `capability`, based at `origin`,
    rank districts IN the selected geography by need-addressed-per-dollar = (demand × unmet-gap) ÷
    mission cost(team, days, distance-from-origin). Capacity-to-serve: `days_to_meet_demand` shows how
    long that team needs (fewer volunteers ⇒ more days); `auto_days` uses it as the mission length.
    Honest where the capability has no demand indicator (ranked by supply scarcity)."""
    from .reach import distance_from_origin
    from .cost import mission_cost, days_to_meet_demand, DEFAULTS
    from .impact import need_addressed_per_cost

    rows = coverage_by_geography(capability, state, count_unverified)
    out = []
    for r in rows:
        need = r["desert_score"]                          # demand × unmet-gap (or scarcity)
        dist = distance_from_origin(origin, r["district_key"])
        dm = days_to_meet_demand(need, team_size, patients_per_volunteer_day, addressable_need_units)
        days_used = dm["days"] if (auto_days and dm["days"] > 0) else days
        cost = mission_cost(dist["distance_km"] or 0.0, dist["drive_hours"] or 0.0,
                            team_size, days_used, DEFAULTS)
        out.append({**r,
            "origin": origin, "distance_km": dist["distance_km"], "drive_hours": dist["drive_hours"],
            "travel_source": dist["source"], "days_used": days_used,
            "days_to_meet_demand": dm["days"], "patients_needed": dm["patients_needed"],
            "cost_total_usd": cost["total_usd"], "cost_breakdown": cost["breakdown"],
            "need_per_dollar": need_addressed_per_cost(need, cost["total_usd"])})
    out = [x for x in out if x["need_per_dollar"] is not None]
    out.sort(key=lambda x: x["need_per_dollar"], reverse=True)
    for i, x in enumerate(out, 1):
        x["opt_rank"] = i
    return {
        "capability": capability, "state": state, "origin": origin,
        "team_size": team_size, "days": days, "auto_days": auto_days,
        "demand_available": bool(rows and rows[0].get("demand_available")),
        "demand_note": (rows[0].get("demand_note") if rows else None),
        "districts": out[:top_n] if top_n else out,
    }


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
