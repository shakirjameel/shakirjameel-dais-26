"""
Tests for the deterministic spine (mission_core). Hermetic — synthetic rows + injected
reachability, no network, no Databricks. Run:
    ./.venv/bin/python -m pytest tests/ -v      (or)   ./.venv/bin/python tests/test_core.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mission_core.cost import mission_cost, CostAssumptions
from mission_core.burden import parse_nfhs_value, burden_score
from mission_core.coverage import coverage_gap, supply_adequacy
from mission_core.impact import need_addressed_per_cost, people_reached
from mission_core.chain import rank_districts


# ---------- cost ----------
def test_cost_total_is_sum_of_parts():
    r = mission_cost(distance_km=100, drive_hours=3, team_size=6, days=7)
    assert abs(r["total_usd"] - sum(r["breakdown"].values())) < 0.01
    assert "assumptions_used" in r  # defensibility: shows where the number came from

def test_cost_directional_and_adjustable():
    near = mission_cost(50, 1, team_size=4, days=5)
    far = mission_cost(400, 8, team_size=4, days=5)
    assert far["breakdown"]["transport_usd"] > near["breakdown"]["transport_usd"]
    assert far["breakdown"]["reach_time_cost_usd"] > near["breakdown"]["reach_time_cost_usd"]
    assert far["breakdown"]["stay_usd"] == near["breakdown"]["stay_usd"]
    cheap = mission_cost(100, 3, assumptions=CostAssumptions(per_diem_usd=30))
    pricey = mission_cost(100, 3, assumptions=CostAssumptions(per_diem_usd=120))
    assert pricey["breakdown"]["stay_usd"] > cheap["breakdown"]["stay_usd"]


# ---------- NFHS honesty parsing ----------
def test_suppressed_is_none_not_zero():
    for raw in ("*", "", None, "NA"):
        assert parse_nfhs_value(raw) == (None, "suppressed")

def test_low_confidence_and_trailing_space():
    assert parse_nfhs_value("(29.5)") == (29.5, "low_confidence")
    assert parse_nfhs_value("71.7 ") == (71.7, None)   # NFHS trailing-space artifact


# ---------- burden (real columns) ----------
def _d(**kw):
    base = {"institutional_birth_5y_pct": "80", "mothers_who_had_at_least_4_anc_visits_lb5y_pct": "70",
            "births_attended_by_skilled_hp_5y_10_pct": "85", "all_w15_49_who_are_anaemic_pct": "50",
            "child_u5_who_are_stunted_height_for_age_18_pct": "30"}
    base.update(kw); return base

def test_maternal_low_institutional_birth_raises_burden():
    bad = burden_score(_d(institutional_birth_5y_pct="20", mothers_who_had_at_least_4_anc_visits_lb5y_pct="15",
                          births_attended_by_skilled_hp_5y_10_pct="25"), "maternal_health")
    good = burden_score(_d(institutional_birth_5y_pct="98", mothers_who_had_at_least_4_anc_visits_lb5y_pct="95",
                           births_attended_by_skilled_hp_5y_10_pct="99"), "maternal_health")
    assert bad["score"] > good["score"]

def test_suppressed_indicator_flagged_not_dropped():
    b = burden_score(_d(all_w15_49_who_are_anaemic_pct="*"), "maternal_health")
    assert "all_w15_49_who_are_anaemic_pct" in b["missing_indicators"]
    assert b["confidence"] == "partial" and b["score"] is not None

def test_all_suppressed_gives_none_score():
    b = burden_score({"all_w15_49_who_are_anaemic_pct": "*"}, "anaemia")
    assert b["score"] is None and b["confidence"] == "none"

def test_low_confidence_propagates():
    b = burden_score(_d(all_w15_49_who_are_anaemic_pct="(55)"), "maternal_health")
    assert "all_w15_49_who_are_anaemic_pct" in b["low_confidence_indicators"]
    assert b["confidence"] == "partial"


# ---------- coverage ----------
def test_supply_adequacy_saturates():
    assert supply_adequacy(0) == 0.0
    assert 0 < supply_adequacy(3) < supply_adequacy(30) < 1.0

def test_gap_high_when_burden_high_and_supply_low():
    high = coverage_gap(0.9, 0)["gap"]
    low = coverage_gap(0.9, 30)["gap"]
    assert high > low
    assert coverage_gap(None, 5)["gap"] is None


# ---------- impact ----------
def test_metric_rewards_cheaper_missions():
    assert need_addressed_per_cost(0.6, 10_000) > need_addressed_per_cost(0.6, 30_000)
    assert need_addressed_per_cost(None, 10_000) is None
    assert need_addressed_per_cost(0.6, 0) is None

def test_people_reached_population_gated():
    assert people_reached(0.6, None)["value"] is None        # no population -> hedged None
    assert people_reached(0.6, 1_000_000)["value"] is not None


# ---------- chain: two-tier ranking + the signature beat ----------
def _synthetic_districts():
    # anaemia % drives burden (high_is_worse); 'facilities' is the supply column for anaemia.
    return [
        {"nfhs_district": "C-near-high", "state_ut": "X", "facilities": 1,
         "all_w15_49_who_are_anaemic_pct": "80"},   # confirmed: high burden, low supply, near
        {"nfhs_district": "B-near-moderate", "state_ut": "X", "facilities": 5,
         "all_w15_49_who_are_anaemic_pct": "55"},   # confirmed: moderate burden, supplied, near
        {"nfhs_district": "F-far-high", "state_ut": "X", "facilities": 2,
         "all_w15_49_who_are_anaemic_pct": "88"},   # confirmed: HIGHEST burden but very far
        {"nfhs_district": "A-nodata-high", "state_ut": "X", "facilities": 0,
         "all_w15_49_who_are_anaemic_pct": "85"},   # candidate: high burden, NO facility data
        {"nfhs_district": "D-no-data", "state_ut": "X", "facilities": 2,
         "all_w15_49_who_are_anaemic_pct": "*"},     # excluded: burden uncomputable
    ]

_REACH = {"C-near-high": (95, 2.0), "B-near-moderate": (60, 1.2),
          "F-far-high": (600, 12.0), "A-nodata-high": (300, 5.0)}

def _reach_fn(row):
    return _REACH.get(row["nfhs_district"].strip())  # None for D -> excluded

def test_chain_two_tiers_and_signature_beat():
    res = rank_districts("anaemia", _reach_fn, districts=_synthetic_districts(), top_n=None)
    conf = [r["district"] for r in res["confirmed_gaps"]]
    cand = [r["district"] for r in res["candidate_gaps"]]
    exc = {r["district"]: r for r in res["excluded"]}

    # TIERING: zero-facility-data district is a CANDIDATE gap, not crowned among confirmed
    assert "A-nodata-high" not in conf and cand == ["A-nodata-high"]
    assert res["candidate_gaps"][0]["tier"] == "candidate_gap"
    assert "R2" in res["candidate_gaps"][0]["data_confidence"]

    # SIGNATURE BEAT (within confirmed): C (near, high) wins; F (HIGHEST burden but far) sinks last
    assert conf[0] == "C-near-high"
    assert conf[-1] == "F-far-high"
    fbur = res["confirmed_gaps"][-1]["burden"]["score"]
    assert all(fbur >= r["burden"]["score"] for r in res["confirmed_gaps"])  # F had top burden

    # EXCLUDED with a reason, never silently dropped
    assert exc["D-no-data"]["metric"] is None
    assert exc["D-no-data"]["excluded_reason"] in ("burden unavailable", "unreachable")


if __name__ == "__main__":
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
