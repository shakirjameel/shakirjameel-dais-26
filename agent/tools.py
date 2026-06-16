"""
tools.py — the tools the agent can call. Thin wrappers over mission_core that return GROUNDED
facts (with provenance), so the agent narrates/ranks but never computes or invents a number.

Three tools:
  list_interventions   — what interventions exist + which burden indicators / supply each uses
  rank_districts       — the two-tier cost-per-impact ranking (confirmed vs candidate gaps)
  get_district_detail  — full cited breakdown for one district (indicators, cost breakdown, reach)

Each tool returns a JSON-serializable dict. Reachability is the cached ORS table from the staging
city; the candidate set is the staging region (Bihar + Jharkhand) — see data/reachability_precompute.py.
"""

from __future__ import annotations

from functools import lru_cache

from mission_core.data_access import (load_districts, make_reach_fn, load_facility_claims,
                                       list_states, STAGING, CANDIDATE_STATES)
from mission_core.burden import INTERVENTION_INDICATORS
from mission_core.chain import rank_districts as _rank, INTERVENTION_SUPPLY_COLUMN
from mission_core.sensitivity import sweep as _sweep, COEFFICIENTS
from mission_core.claims import CAPABILITIES, CAPABILITY_LABELS
from mission_core.coverage_view import coverage_by_geography as _coverage, coverage_summary, optimize as _optimize
from mission_core.geo_names import list_origins, DEFAULT_ORIGIN
from .brief import build_brief


@lru_cache(maxsize=1)
def _candidates() -> list[dict]:
    return [d for d in load_districts() if d["state_ut"].strip().lower() in CANDIDATE_STATES]


@lru_cache(maxsize=1)
def _reach_fn():
    return make_reach_fn()


def _summarize(row: dict) -> dict:
    """Compact, citable view of one ranked district for the LLM (no giant nested blobs)."""
    return {
        "rank": row.get("tier_rank"),
        "district": row["district"], "state": row["state"],
        "need_per_dollar": row["metric"],
        "burden_score": row["burden"]["score"],
        "burden_confidence": row["burden"]["confidence"],
        "gap": row["gap"]["gap"],
        "reachable_supply": row["supply"],
        # maternal supply split into text-verified vs flag-only (claims-to-verify discipline)
        "verified_maternal_supply": row.get("verified_supply"),
        "maternal_claim_breakdown": row.get("claim_breakdown"),
        "drive_hours": round(row["reach"]["drive_hours"], 1),
        "distance_km": row["reach"]["distance_km"],
        "cost_total_usd": row["cost"]["total_usd"],
        "tier": row["tier"],
        "data_confidence": row["data_confidence"],
    }


def _cite(c: dict) -> dict:
    """One facility's CITED claim — name, source link, contact, placement + the underlying text."""
    return {
        "facility_name": c.get("name") or None,
        "city": c.get("city") or None,
        "source_url": c.get("source_url") or None,
        "claim_confidence": c.get("claim_confidence"),
        "operator": c.get("operator"),
        "capability": c.get("capability"),
        "accepts_volunteers": str(c.get("accepts_volunteers") or "0") in ("1", "1.0"),
        "phone": c.get("phone") or None,
        "website": c.get("website") or None,
        "capacity_beds": c.get("capacity_beds") or None,
        "claimed_capability_text": c.get("capability_evidence") or None,
        "corroborating_procedure_text": c.get("procedure_evidence") or None,
        "matched_claim_terms": c.get("claim_terms"),
        "matched_corroborating_terms": c.get("corroborating_terms"),
    }


# ---------------------------------------------------------------- tool implementations
def list_interventions() -> dict:
    out = []
    for name, indicators in INTERVENTION_INDICATORS.items():
        out.append({
            "intervention": name,
            "burden_indicators": [c for c, _ in indicators],
            "supply_counted_as": INTERVENTION_SUPPLY_COLUMN.get(name, "facilities"),
        })
    return {"staging_city": STAGING["name"], "candidate_region": sorted(CANDIDATE_STATES),
            "interventions": out}


def rank_districts_tool(intervention: str, team_size: int = 6, days: int = 7, top_n: int = 5,
                        count_unverified: bool = False) -> dict:
    if intervention not in INTERVENTION_INDICATORS:
        return {"error": f"unknown intervention '{intervention}'",
                "valid": list(INTERVENTION_INDICATORS)}
    res = _rank(intervention, _reach_fn(), team_size=team_size, days=days,
                top_n=top_n, districts=_candidates(), count_unverified=count_unverified)
    return {
        "intervention": intervention, "staging_city": STAGING["name"],
        "team_size": team_size, "days": days,
        "confirmed_gaps": [_summarize(r) for r in res["confirmed_gaps"]],
        "candidate_gaps": [_summarize(r) for r in res["candidate_gaps"]],
        "n_excluded": len(res["excluded"]),
        "note": ("confirmed_gaps have measured facility data; candidate_gaps have NO facility data "
                 "(possible desert OR data gap) — surface them as 'investigate', never as a confident pick."),
    }


def get_district_detail(intervention: str, district: str, team_size: int = 6, days: int = 7) -> dict:
    if intervention not in INTERVENTION_INDICATORS:
        return {"error": f"unknown intervention '{intervention}'", "valid": list(INTERVENTION_INDICATORS)}
    res = _rank(intervention, _reach_fn(), team_size=team_size, days=days,
                top_n=None, districts=_candidates())
    key = district.strip().lower()
    for tier in ("confirmed_gaps", "candidate_gaps"):
        for r in res[tier]:
            if r["district"].strip().lower() == key:
                return {
                    "district": r["district"], "state": r["state"], "tier": r["tier"],
                    "tier_rank": r["tier_rank"], "data_confidence": r["data_confidence"],
                    "burden": r["burden"],          # score + indicators_used + missing + low_confidence
                    "gap": r["gap"],
                    "supply": {"reachable_relevant": r["supply"],
                               "verified_maternal": r.get("verified_supply"),
                               "claim_breakdown": r.get("claim_breakdown")},
                    "reach": r["reach"],
                    "cost": r["cost"],               # full breakdown + assumptions_used (provenance)
                    "need_per_dollar": r["metric"],
                    # a representative CITED facility claim (the underlying text), if any
                    "evidence": ([_cite(c) for c in load_facility_claims(r["district"], "maternity")[:1]]
                                 or [None])[0],
                }
    for r in res["excluded"]:
        if r["district"].strip().lower() == key:
            return {"district": r["district"], "state": r["state"],
                    "excluded": True, "reason": r["excluded_reason"], "burden": r["burden"]}
    return {"error": f"district '{district}' not found in candidate region",
            "hint": "call rank_districts first to see valid district names"}


def coverage_by_geography(capability: str = "maternity", state: str = None,
                          count_unverified: bool = False, top_n: int = 12) -> dict:
    """Track-2's primary aggregate: TRUST-WEIGHTED coverage by district for a capability across a
    state — ranked by desert score, distinguishing REAL care gaps from data-poor regions
    (confirmed_coverage / unverified_claims / no_claim_desert). Use this to answer 'where are the
    highest-risk gaps for <capability> in <state>?'. All numbers are computed; cite, don't invent."""
    cap = {"maternal_health": "maternity"}.get(capability, capability)
    if cap not in CAPABILITIES:
        return {"error": f"unknown capability '{capability}'", "valid": CAPABILITIES}
    rows = _coverage(cap, state, count_unverified, top_n=top_n)
    if not rows:
        return {"capability": cap, "state": state, "districts": [],
                "note": "no coverage rows — check the state name or run the data pipeline."}
    return {
        "capability": cap, "capability_label": CAPABILITY_LABELS.get(cap, cap), "state": state,
        "count_unverified": count_unverified,
        "summary": coverage_summary(_coverage(cap, state, count_unverified)),
        "districts": [{
            "rank": r["rank"], "district": r["district"], "state": r["state"],
            "gap_classification": r["gap_classification"],
            "verified_supply": r["verified_supply"], "unverified": r["unverified"],
            "trust_ratio": r["trust_ratio"], "burden": r["burden"],
            "desert_score": r["desert_score"],
        } for r in rows],
        "note": ("supply is TRUST-WEIGHTED (corroborated claims count fully, claimed-only partly, "
                 "flag-only nothing unless count_unverified). 'no_claim_desert' = real gap; "
                 "'unverified_claims' = claims to verify, not confirmed coverage."),
    }


def optimize_deployment(capability: str = "maternity", state: str = None, origin: str = DEFAULT_ORIGIN,
                        team_size: int = 6, days: int = 7, patients_per_volunteer_day: float = 20.0,
                        count_unverified: bool = False, auto_days: bool = False, top_n: int = 8) -> dict:
    """Deployment optimizer: for a team of `team_size` specialised in `capability`, based at `origin`
    (a home city), rank districts in `state` (or all-India) by need-addressed-per-dollar = measured
    DEMAND × unmet trust-weighted gap ÷ mission cost(travel-from-origin + per-diem). Returns per
    district: demand (or honest 'supply-scarcity only'), verified supply, distance+cost FROM the
    origin, days-to-meet-demand (fewer volunteers ⇒ more days), and # facilities that accept
    volunteers. Use for 'I have N <capability> volunteers in <city>, where should we go?'."""
    cap = {"maternal_health": "maternity"}.get(capability, capability)
    if cap not in CAPABILITIES:
        return {"error": f"unknown capability '{capability}'", "valid": CAPABILITIES}
    if origin not in list_origins():
        return {"error": f"unknown origin '{origin}'", "valid": list_origins()}
    res = _optimize(cap, state=state, origin=origin, team_size=team_size, days=days,
                    patients_per_volunteer_day=patients_per_volunteer_day,
                    count_unverified=count_unverified, auto_days=auto_days, top_n=top_n)
    return {
        "capability": cap, "state": state or "all India", "origin": origin,
        "team_size": team_size, "demand_available": res["demand_available"],
        "demand_note": res["demand_note"],
        "districts": [{
            "rank": d["opt_rank"], "district": d["district"], "state": d["state"],
            "demand": d["burden"] if d["demand_available"] else None,
            "verified_supply": d["verified_supply"], "gap_classification": d["gap_classification"],
            "distance_km": d["distance_km"], "travel_source": d["travel_source"],
            "cost_total_usd": d["cost_total_usd"], "need_per_dollar": d["need_per_dollar"],
            "days_to_meet_demand": d["days_to_meet_demand"], "accepts_volunteers": d["accepts_volunteers"],
        } for d in res["districts"]],
        "note": ("need is RELATIVE (no population in source data) with named adjustable assumptions; "
                 "demand is NFHS-measured where available, else 'supply-scarcity only' (honest)."),
    }


def get_district_facilities(district: str, capability: str = "maternity", limit: int = 8,
                            intervention: str = None) -> dict:
    """The underlying facility records behind a district's supply for a CAPABILITY: for each, the
    facility NAME, a SOURCE link, its CLAIMED capability text, the corroborating procedure text (if
    any), and a claim-confidence (high = claimed + corroborated; medium = claimed only; unverified =
    a flag/specialty asserts it but the facility's own text doesn't). Use to CITE evidence for a
    ranking and show which 'supply' is verified vs an unverified claim."""
    cap = {"maternal_health": "maternity"}.get(intervention or capability, capability)
    if cap not in CAPABILITIES:
        return {"capability": cap, "district": district, "facilities": [],
                "note": f"unknown capability '{cap}'. valid: {CAPABILITIES}"}
    claims = load_facility_claims(district, cap)
    if not claims:
        return {"capability": cap, "district": district, "facilities": [],
                "note": ("no facility even claims this capability here — a candidate care desert "
                         "(or the facility-text table isn't loaded).")}
    counts = {k: sum(1 for c in claims if c["claim_confidence"] == k)
              for k in ("high", "medium", "unverified")}
    return {
        "capability": cap, "district": district, "counts": counts,
        "verified_supply": counts["high"] + counts["medium"],
        "facilities": [_cite(c) for c in claims[:limit]],
        "note": ("capability/procedure are FDR-extracted CLAIMS to verify, not ground truth. "
                 "Cite the facility name + source + text; never present an unverified claim as fact."),
    }


def sensitivity_analysis(intervention: str, coefficient: str = "surgeon_day_value_usd",
                         team_size: int = 6, days: int = 7) -> dict:
    """Is the #1 confirmed pick robust to a cost assumption, or does it flip? Sweeps the
    coefficient across a plausible range and reports the robust range + any flip point."""
    if intervention not in INTERVENTION_INDICATORS:
        return {"error": f"unknown intervention '{intervention}'", "valid": list(INTERVENTION_INDICATORS)}
    if coefficient not in COEFFICIENTS:
        return {"error": f"unknown coefficient '{coefficient}'", "valid": list(COEFFICIENTS)}
    return _sweep(intervention, _reach_fn(), coefficient,
                  team_size=team_size, days=days, districts=_candidates())


def generate_brief(intervention: str, district: str, team_size: int = 6, days: int = 7) -> dict:
    """Produce a cited one-page mission brief for a district (every figure traces to a source
    indicator or a named assumption). Returns {"brief": markdown}."""
    detail = get_district_detail(intervention, district, team_size, days)
    if detail.get("error"):
        return detail
    ranked = rank_districts_tool(intervention, team_size=team_size, days=days, top_n=4)
    brief = build_brief(detail, ranked.get("candidate_gaps", []), intervention,
                        STAGING["name"], team_size, days)
    return {"brief": brief}


# ---------------------------------------------------------------- OpenAI tool schemas + dispatch
_INTERVENTION_ENUM = list(INTERVENTION_INDICATORS)
_COEFFICIENT_ENUM = list(COEFFICIENTS)
_CAPABILITY_ENUM = list(CAPABILITIES)

TOOLS = [
    {"type": "function", "function": {
        "name": "list_interventions",
        "description": "List available interventions, the NFHS-5 burden indicators each uses, the "
                       "staging city, and the candidate region. Call this first if unsure which "
                       "intervention name to use.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "coverage_by_geography",
        "description": "PRIMARY tool. Trust-weighted facility COVERAGE by district for a capability "
                       "(maternity/icu/nicu/emergency/oncology/trauma) across a state — ranked by "
                       "desert score, classifying each district as confirmed_coverage / "
                       "unverified_claims / no_claim_desert. Use for 'where are the highest-risk "
                       "gaps for <capability> in <state>?' and to tell real gaps from data-poor regions.",
        "parameters": {"type": "object", "properties": {
            "capability": {"type": "string", "enum": _CAPABILITY_ENUM,
                           "description": "Which capability (default maternity)."},
            "state": {"type": "string", "description": "State/UT name, e.g. 'Bihar'. Omit for all-India."},
            "count_unverified": {"type": "boolean",
                                 "description": "Count flag-only/unverified claims as (discounted) supply? Default false."},
            "top_n": {"type": "integer", "description": "How many districts to return (default 12)."}},
            "required": ["capability"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "rank_districts",
        "description": "Rank candidate districts by need-addressed-per-dollar for an intervention, "
                       "split into two tiers: confirmed_gaps (measured facility data) and "
                       "candidate_gaps (no facility data — investigate, not a confident pick). "
                       "All numbers are computed deterministically; do not invent any.",
        "parameters": {"type": "object", "properties": {
            "intervention": {"type": "string", "enum": _INTERVENTION_ENUM,
                             "description": "Which intervention to plan for."},
            "team_size": {"type": "integer", "description": "Number of volunteers (default 6)."},
            "days": {"type": "integer", "description": "Mission duration in days (default 7)."},
            "top_n": {"type": "integer", "description": "How many per tier to return (default 5)."}},
            "required": ["intervention"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "get_district_detail",
        "description": "Full cited breakdown for ONE district: burden indicators (with which are "
                       "missing/low-confidence), coverage gap, reachability, and the itemized cost "
                       "with the named assumptions used. Use to justify 'where a number came from'.",
        "parameters": {"type": "object", "properties": {
            "intervention": {"type": "string", "enum": _INTERVENTION_ENUM},
            "district": {"type": "string", "description": "District name as shown in rank_districts."},
            "team_size": {"type": "integer"}, "days": {"type": "integer"}},
            "required": ["intervention", "district"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "optimize_deployment",
        "description": "Deployment optimizer for a volunteer team: given a capability (the team's "
                       "specialisation), team size, and home-base origin city, rank districts in a "
                       "state (or all-India) by need-addressed-per-dollar — measured demand × unmet "
                       "gap ÷ cost(travel-from-origin + per-diem). Returns distance/cost FROM the "
                       "origin, days-to-meet-demand, and facilities that accept volunteers. Use for "
                       "'I have N <capability> volunteers in <city>, where should we deploy?'.",
        "parameters": {"type": "object", "properties": {
            "capability": {"type": "string", "enum": _CAPABILITY_ENUM},
            "state": {"type": "string", "description": "State/UT to rank within; omit for all-India."},
            "origin": {"type": "string", "description": "Home-base city the team travels from (e.g. 'Delhi', 'Patna (Bihar)')."},
            "team_size": {"type": "integer"}, "days": {"type": "integer"},
            "patients_per_volunteer_day": {"type": "number"},
            "auto_days": {"type": "boolean", "description": "Set mission length to days-needed-to-meet-demand."}},
            "required": ["capability"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "get_district_facilities",
        "description": "The underlying facility RECORDS behind a district's supply for a capability: "
                       "each facility's NAME, a SOURCE link, its CLAIMED capability text + the "
                       "corroborating procedure text + a claim-confidence (high/medium/unverified). "
                       "Call this to CITE the facility evidence for a ranking, or when asked 'can "
                       "these facilities actually do it?' — capability is a CLAIM to verify.",
        "parameters": {"type": "object", "properties": {
            "district": {"type": "string", "description": "District name as shown in coverage_by_geography."},
            "capability": {"type": "string", "enum": _CAPABILITY_ENUM,
                           "description": "Which capability (default maternity)."},
            "limit": {"type": "integer", "description": "Max facilities to cite (default 8)."}},
            "required": ["district"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "sensitivity_analysis",
        "description": "Test whether the #1 confirmed pick is robust to a cost assumption or flips. "
                       "Use when the planner challenges a number or asks how sensitive the ranking is "
                       "to the cost coefficients. Reports the robust range and any flip point.",
        "parameters": {"type": "object", "properties": {
            "intervention": {"type": "string", "enum": _INTERVENTION_ENUM},
            "coefficient": {"type": "string", "enum": _COEFFICIENT_ENUM,
                            "description": "Which cost assumption to sweep (default surgeon_day_value_usd)."},
            "team_size": {"type": "integer"}, "days": {"type": "integer"}},
            "required": ["intervention"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "generate_brief",
        "description": "Produce a cited one-page mission brief for a chosen district — burden "
                       "evidence, coverage gap, reach, itemized cost with assumptions, candidate "
                       "gaps to investigate, and flagged uncertainties. Use when the planner wants "
                       "a deliverable for the top recommendation.",
        "parameters": {"type": "object", "properties": {
            "intervention": {"type": "string", "enum": _INTERVENTION_ENUM},
            "district": {"type": "string"},
            "team_size": {"type": "integer"}, "days": {"type": "integer"}},
            "required": ["intervention", "district"], "additionalProperties": False}}},
]

_DISPATCH = {
    "list_interventions": list_interventions,
    "coverage_by_geography": coverage_by_geography,
    "optimize_deployment": optimize_deployment,
    "rank_districts": rank_districts_tool,
    "get_district_detail": get_district_detail,
    "get_district_facilities": get_district_facilities,
    "sensitivity_analysis": sensitivity_analysis,
    "generate_brief": generate_brief,
}


def dispatch(name: str, arguments: dict) -> dict:
    """Execute a tool call by name. Unknown tool / bad args -> structured error (never raises)."""
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool '{name}'", "valid_tools": list(_DISPATCH)}
    try:
        return fn(**(arguments or {}))
    except TypeError as e:
        return {"error": f"bad arguments for '{name}': {e}"}
