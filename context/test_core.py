"""
Run: python -m pytest tests/ -v   (or: python tests/test_core.py)
Covers the Rung 0 spine: cost breakdown transparency, suppressed/low-conf
handling, burden scoring, and the impact-per-cost ranking metric.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cost import mission_cost, CostAssumptions
from core.burden import (parse_nfhs_value, burden_score, people_reached,
                         impact_per_cost, INTERVENTION_INDICATORS)


# ---------- cost ----------
def test_cost_returns_full_breakdown_not_bare_total():
    r = mission_cost(distance_km=100, drive_hours=3, team_size=6, days=7)
    assert "total_usd" in r and "breakdown" in r and "assumptions_used" in r
    b = r["breakdown"]
    # total equals sum of parts — no hidden math
    assert abs(r["total_usd"] - sum(b.values())) < 0.01

def test_cost_components_directionally_correct():
    near = mission_cost(distance_km=50, drive_hours=1, team_size=4, days=5)
    far = mission_cost(distance_km=400, drive_hours=8, team_size=4, days=5)
    # farther -> higher transport AND higher reach time-cost, same stay
    assert far["breakdown"]["transport_usd"] > near["breakdown"]["transport_usd"]
    assert far["breakdown"]["reach_time_cost_usd"] > near["breakdown"]["reach_time_cost_usd"]
    assert far["breakdown"]["stay_usd"] == near["breakdown"]["stay_usd"]

def test_cost_assumptions_are_adjustable():
    cheap = CostAssumptions(per_diem_usd=30)
    pricey = CostAssumptions(per_diem_usd=120)
    rc = mission_cost(100, 3, assumptions=cheap)
    rp = mission_cost(100, 3, assumptions=pricey)
    assert rp["breakdown"]["stay_usd"] > rc["breakdown"]["stay_usd"]


# ---------- NFHS value parsing (the honesty rules) ----------
def test_suppressed_is_none_not_zero():
    assert parse_nfhs_value("*") == (None, "suppressed")
    assert parse_nfhs_value("") == (None, "suppressed")
    assert parse_nfhs_value(None) == (None, "suppressed")

def test_parenthesized_is_low_confidence():
    v, flag = parse_nfhs_value("(29.5)")
    assert v == 29.5 and flag == "low_confidence"

def test_plain_value_clean():
    assert parse_nfhs_value("45.2") == (45.2, None)


# ---------- burden ----------
def _row(**kw):
    base = {"population": 1_000_000}
    base.update(kw); return base

def test_burden_high_is_worse():
    # high anaemia % -> high burden
    row = _row(women_anaemic_pct="80", children_anaemic_pct="70")
    b = burden_score(row, "anaemia")
    assert b["score"] > 0.7 and b["confidence"] == "full"

def test_burden_low_is_worse_inverts():
    # LOW institutional delivery -> HIGH maternal burden
    bad = burden_score(_row(institutional_delivery_pct="20", anc_4plus_visits_pct="15"),
                       "maternal_health")
    good = burden_score(_row(institutional_delivery_pct="95", anc_4plus_visits_pct="90"),
                        "maternal_health")
    assert bad["score"] > good["score"]

def test_suppressed_indicator_flagged_not_dropped_silently():
    row = _row(women_anaemic_pct="60", children_anaemic_pct="*")
    b = burden_score(row, "anaemia")
    assert "children_anaemic_pct" in b["missing_indicators"]
    assert b["confidence"] == "partial"
    assert b["score"] is not None   # still scored on what's available

def test_all_suppressed_gives_none_score():
    row = _row(women_anaemic_pct="*", children_anaemic_pct="*")
    b = burden_score(row, "anaemia")
    assert b["score"] is None and b["confidence"] == "none"

def test_low_confidence_propagates():
    row = _row(women_anaemic_pct="(55)", children_anaemic_pct="60")
    b = burden_score(row, "anaemia")
    assert "women_anaemic_pct" in b["low_confidence_indicators"]
    assert b["confidence"] == "partial"


# ---------- impact + ranking ----------
def test_people_reached_hedged_and_methoded():
    row = _row(women_anaemic_pct="80", children_anaemic_pct="70")
    b = burden_score(row, "anaemia")
    pr = people_reached(row, b)
    assert pr["value"] is not None and "method" in pr and "heuristic" in pr["note"]

def test_people_reached_none_when_pop_missing():
    row = {"women_anaemic_pct": "80", "children_anaemic_pct": "70"}  # no population
    b = burden_score(row, "anaemia")
    pr = people_reached(row, b)
    assert pr["value"] is None and pr["confidence"] == "none"

def test_impact_per_cost_ranks_correctly():
    # same people reached, cheaper mission -> higher impact/cost
    people = {"value": 3000}
    hi = impact_per_cost(people, 10_000)
    lo = impact_per_cost(people, 30_000)
    assert hi > lo

def test_impact_none_when_uncomputable():
    assert impact_per_cost({"value": None}, 10_000) is None
    assert impact_per_cost({"value": 3000}, 0) is None


if __name__ == "__main__":
    # lightweight runner if pytest isn't installed
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}  {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}  {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
