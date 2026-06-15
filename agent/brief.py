"""
brief.py — deterministic, cited one-page mission brief.

Slot-fills the brief from grounded tool values (district detail + candidate gaps) so EVERY claim
traces to a source indicator or a named assumption. The agent presents the brief; it doesn't
write the numbers. Missing values render as "[unavailable/suppressed]", never guessed.
"""

from __future__ import annotations


def _na(v):
    return "[unavailable/suppressed]" if v in (None, "") else v


def build_brief(detail: dict, candidate_gaps: list[dict], intervention: str,
                staging_city: str, team_size: int, days: int) -> str:
    """detail = get_district_detail output for the chosen district; candidate_gaps = the
    candidate-tier rows from rank_districts. Returns a markdown brief."""
    if detail.get("error") or detail.get("excluded"):
        return f"Cannot brief {detail.get('district', '?')}: {detail.get('reason') or detail.get('error')}"

    b, gap, cost, reach = detail["burden"], detail["gap"], detail["cost"], detail["reach"]
    bd, used = cost["breakdown"], cost["assumptions_used"]

    missing = b.get("missing_indicators") or []
    lowconf = b.get("low_confidence_indicators") or []
    caveats = []
    if missing:
        caveats.append(f"suppressed indicators: {', '.join(missing)}")
    if lowconf:
        caveats.append(f"low-confidence indicators: {', '.join(lowconf)}")
    caveats.append("road reach is an estimate (ORS where routable, else straight-line ×1.3)")
    caveats.append("cost coefficients are named assumptions, adjustable (see sensitivity)")

    cand = "\n".join(
        f"- {c['district']}, {c['state']} — burden {c['burden_score']}, "
        f"{c['drive_hours']}h away — NO facility data; verify on the ground"
        for c in candidate_gaps[:4]
    ) or "- (none in range)"

    return f"""\
MISSION BRIEF — {intervention} | staging: {staging_city} | team {team_size} × {days} days

TOP RECOMMENDATION (confirmed gap): {detail['district']}, {detail['state']}   [{detail['data_confidence']}]

BURDEN (NFHS-5)
  composite score {_na(b['score'])} ({b['confidence']}), {b['indicators_used']}/{b['indicators_total']} indicators used
COVERAGE GAP
  {_na(gap['gap'])}  (reachable relevant supply: {detail['supply']['reachable_relevant']}; supply adequacy {_na(gap.get('supply_adequacy'))})
REACHABILITY (from {staging_city})
  {_na(reach['distance_km'])} km / {round(reach['drive_hours'],1)} h (estimated road travel)
MISSION COST  ${cost['total_usd']:,.0f}
  transport ${bd['transport_usd']:,.0f} + stay ${bd['stay_usd']:,.0f} + reach-time ${bd['reach_time_cost_usd']:,.0f}
  assumptions: ${used['transport_per_km_usd']}/km · ${used['per_diem_usd']}/diem · ${used['surgeon_day_value_usd']}/surgeon-day
NEED-PER-DOLLAR  {_na(detail['need_per_dollar'])}

CANDIDATE GAPS TO INVESTIGATE (no facility data — could be desert OR data gap):
{cand}

FLAGGED UNCERTAINTIES: {'; '.join(caveats)}
Every figure above traces to a source indicator or a named, adjustable assumption."""
