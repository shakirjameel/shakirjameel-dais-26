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

from mission_core.data_access import load_districts, make_reach_fn, STAGING, CANDIDATE_STATES
from mission_core.burden import INTERVENTION_INDICATORS
from mission_core.chain import rank_districts as _rank, INTERVENTION_SUPPLY_COLUMN
from mission_core.sensitivity import sweep as _sweep, COEFFICIENTS
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
        "drive_hours": round(row["reach"]["drive_hours"], 1),
        "distance_km": row["reach"]["distance_km"],
        "cost_total_usd": row["cost"]["total_usd"],
        "tier": row["tier"],
        "data_confidence": row["data_confidence"],
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


def rank_districts_tool(intervention: str, team_size: int = 6, days: int = 7, top_n: int = 5) -> dict:
    if intervention not in INTERVENTION_INDICATORS:
        return {"error": f"unknown intervention '{intervention}'",
                "valid": list(INTERVENTION_INDICATORS)}
    res = _rank(intervention, _reach_fn(), team_size=team_size, days=days,
                top_n=top_n, districts=_candidates())
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
                    "supply": {"reachable_relevant": r["supply"]},
                    "reach": r["reach"],
                    "cost": r["cost"],               # full breakdown + assumptions_used (provenance)
                    "need_per_dollar": r["metric"],
                }
    for r in res["excluded"]:
        if r["district"].strip().lower() == key:
            return {"district": r["district"], "state": r["state"],
                    "excluded": True, "reason": r["excluded_reason"], "burden": r["burden"]}
    return {"error": f"district '{district}' not found in candidate region",
            "hint": "call rank_districts first to see valid district names"}


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

TOOLS = [
    {"type": "function", "function": {
        "name": "list_interventions",
        "description": "List available interventions, the NFHS-5 burden indicators each uses, the "
                       "staging city, and the candidate region. Call this first if unsure which "
                       "intervention name to use.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
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
    "rank_districts": rank_districts_tool,
    "get_district_detail": get_district_detail,
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
