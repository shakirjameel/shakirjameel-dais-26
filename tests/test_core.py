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
from mission_core.coverage import (coverage_gap, supply_adequacy, trust_weighted_supply,
                                    gap_classification)
from mission_core.coverage_view import coverage_by_geography, coverage_summary, state_rollup, optimize
from mission_core.coverage import state_fill_category
from mission_core.cost import days_to_meet_demand
from mission_core.reach import distance_from_origin, haversine_km
from mission_core.burden import capability_demand
from mission_core.geo_names import to_topo_state, list_topo_states
from mission_core.data_access import load_districts
from mission_core.impact import need_addressed_per_cost, people_reached
from mission_core.chain import rank_districts
from mission_core.claims import classify_claim, summarize_claims


# ---------- claim verification (treat free-text as claims to verify) ----------
def _fac(cap="", proc="", equip="", desc="", flag=0):
    return {"unique_id": "x", "capability": cap, "procedure": proc,
            "equipment": equip, "description": desc, "maternal_supply": flag}

def test_claim_high_when_capability_claims_and_procedure_corroborates():
    f = _fac(cap='["Maternity care including prenatal check-ups"]',
             proc='["Cesarean deliveries (C-sections) performed"]', flag=1)
    r = classify_claim(f, "maternal_health")
    assert r["confidence"] == "high"
    assert r["claimed"] and r["corroborated"]
    assert r["capability_evidence"] and r["procedure_evidence"]  # citable text, not invented

def test_claim_medium_when_claimed_but_uncorroborated():
    f = _fac(cap='["Obstetrics and gynaecology department"]',
             proc='["General outpatient consultation"]', flag=1)
    r = classify_claim(f, "maternal_health")
    assert r["confidence"] == "medium"
    assert r["claimed"] and not r["corroborated"]

def test_claim_unverified_when_flag_only_and_text_contradicts():
    # the noisy flag asserts maternal, but the facility's own text is an eye hospital
    f = _fac(cap='["Eye care hospital","Cataract surgery"]',
             proc='["Phacoemulsification"]', flag=1)
    r = classify_claim(f, "maternal_health")
    assert r["confidence"] == "unverified"
    assert not r["claimed"]

def test_claim_none_when_no_flag_and_no_text():
    r = classify_claim(_fac(cap='["Dental clinic"]', flag=0), "maternal_health")
    assert r["confidence"] == "none"

def test_generic_delivery_does_not_corroborate():
    # "drug delivery" / "radiation delivery" must NOT count as childbirth corroboration
    f = _fac(cap='["Gynaecology services"]',
             proc='["VMAT planning and delivery","Targeted drug delivery"]', flag=1)
    r = classify_claim(f, "maternal_health")
    assert not r["corroborated"] and r["confidence"] == "medium"

def test_capability_nicu_high_with_equipment_corroboration():
    f = _fac(cap='["NICU with neonatal care"]', equip='["Radiant warmer","Incubator"]')
    r = classify_claim(f, "nicu")
    assert r["confidence"] == "high" and r["capability"] == "nicu"

def test_capability_icu_claimed_without_ventilator_is_medium():
    f = _fac(cap='["Has an ICU and high dependency unit"]', proc='["General consultation"]')
    r = classify_claim(f, "icu")
    assert r["confidence"] == "medium" and not r["corroborated"]

def test_capability_specialties_count_as_claim_source():
    # the controlled `specialties` field is a claim source for any capability
    f = {"capability": "", "description": "", "procedure": '["Chemotherapy"]', "equipment": "",
         "specialties": '["medicalOncology"]', "maternal_supply": 0}
    r = classify_claim(f, "oncology")
    assert r["confidence"] == "high" and r["claimed"]

def test_capability_isolation_eye_hospital_not_maternity():
    # an eye hospital must be 'none' for maternity (no flag, no maternal claim)
    f = _fac(cap='["Eye care hospital"]', proc='["Phacoemulsification"]', flag=0)
    assert classify_claim(f, "maternity")["confidence"] == "none"

def test_maternal_health_alias_maps_to_maternity():
    f = _fac(cap='["maternity"]', proc='["cesarean deliveries"]', flag=1)
    assert classify_claim(f, "maternal_health")["capability"] == "maternity"

def test_summarize_counts_and_picks_verified_supply():
    facs = [
        _fac(cap='["maternity"]', proc='["cesarean deliveries"]', flag=1),       # high
        _fac(cap='["obstetrics dept"]', proc='["opd"]', flag=1),                  # medium
        _fac(cap='["eye hospital"]', flag=1),                                     # unverified
        _fac(cap='["dental"]', flag=0),                                           # none
    ]
    s = summarize_claims(facs, "maternal_health")
    assert (s["high"], s["medium"], s["unverified"], s["none"]) == (1, 1, 1, 1)
    assert s["verified_supply"] == 2                       # flag-only is NOT counted as supply
    assert s["best_evidence"]["confidence"] == "high"     # exemplar is the corroborated one


# ---------- map: state reconciliation + rollup (no grey holes, no false zeros) ----------
def test_state_reconciliation_aliases():
    assert to_topo_state("Maharastra") == "Maharashtra"        # misspelling
    assert to_topo_state("NCT of Delhi") == "Delhi"            # curated alias
    assert to_topo_state("Jammu & Kashmir") == "Jammu and Kashmir"   # & -> and
    assert to_topo_state("Andaman & Nicobar Islands") == "Andaman and Nicobar Islands"

def test_all_lit_states_reconcile_no_grey_holes():
    fac = {}
    for d in load_districts():
        fac[d["state_ut"].strip()] = fac.get(d["state_ut"].strip(), 0) + int(d.get("facilities") or 0)
    unmapped = [s for s, n in fac.items() if n > 0 and to_topo_state(s) is None]
    assert unmapped == [], f"lit states with no topology match (grey holes): {unmapped}"

def test_state_fill_category_rules():
    assert state_fill_category(lit=False, n_confirmed=0, n_claim_only=0, mean_desert_score=None) == "no_data"
    assert state_fill_category(lit=True, n_confirmed=0, n_claim_only=0, mean_desert_score=None) == "no_claim_desert"
    assert state_fill_category(lit=True, n_confirmed=0, n_claim_only=3, mean_desert_score=None) == "claim_only"
    assert state_fill_category(lit=True, n_confirmed=5, n_claim_only=0, mean_desert_score=0.1) == "strong"
    assert state_fill_category(lit=True, n_confirmed=5, n_claim_only=0, mean_desert_score=0.42) == "moderate"
    assert state_fill_category(lit=True, n_confirmed=5, n_claim_only=0, mean_desert_score=0.8) == "weaker"

def test_state_rollup_covers_all_topo_states_and_marks_no_data():
    roll = state_rollup("maternity")
    assert len(roll) == len(list_topo_states())          # one row per map state
    by = {r["st_nm"]: r for r in roll}
    # a state with no facilities renders no_data, never a misleading score-0 'good coverage'
    assert by["Ladakh"]["fill_category"] == "no_data" and not by["Ladakh"]["lit"]
    assert by["Sikkim"]["fill_category"] == "no_data"
    # a data-rich state is lit and not no_data
    assert by["Kerala"]["lit"] and by["Kerala"]["fill_category"] != "no_data"


# ---------- optimizer: capacity-to-serve, origin cost, demand-supply matching ----------
def test_capability_demand_honesty():
    d = load_districts()[0]
    assert capability_demand(d, "emergency")["demand_available"] is False   # no NFHS proxy
    assert capability_demand(d, "trauma")["demand_available"] is False
    assert "maternity" == capability_demand(d, "maternal_health")["capability"]  # alias

def test_days_to_meet_demand_scales_with_team():
    few = days_to_meet_demand(0.5, team_size=2)["days"]
    many = days_to_meet_demand(0.5, team_size=10)["days"]
    assert few > many > 0                                   # fewer volunteers ⇒ more days

def test_origin_distance_varies_by_base():
    # haversine sanity: Delhi→Patna is hundreds of km; Patna→Patna ~0
    assert haversine_km(28.6, 77.2, 25.6, 85.1) > 700
    assert haversine_km(25.6, 85.1, 25.6, 85.1) < 1

def test_optimizer_origin_changes_cost_and_is_state_scoped():
    from_delhi = optimize("maternity", state="Bihar", origin="Delhi", top_n=5)["districts"]
    from_patna = optimize("maternity", state="Bihar", origin="Patna (Bihar)", top_n=5)["districts"]
    assert from_delhi and from_patna
    assert all(d["state"] == "Bihar" for d in from_delhi)   # state-scoped
    # same district costs more from Delhi than from Patna (farther)
    pa = {d["district"]: d["cost_total_usd"] for d in from_patna}
    assert any(d["cost_total_usd"] > pa.get(d["district"], 0) for d in from_delhi if d["district"] in pa)


# ---------- trust-weighting + coverage-by-geography ----------
def test_trust_weighted_supply_weights_and_toggle():
    assert trust_weighted_supply(2, 0, 5) == 2.0                       # high·1
    assert trust_weighted_supply(1, 5, 0) == 1 + 5 * 0.6              # +medium·0.6
    assert trust_weighted_supply(0, 0, 4, count_unverified=False) == 0.0   # unverified ignored by default
    assert trust_weighted_supply(0, 0, 4, count_unverified=True) == round(4 * 0.3, 3)

def test_gap_classification_distinguishes_real_gap_from_data_poor():
    assert gap_classification(1, 0, 3) == "confirmed_coverage"        # some verified
    assert gap_classification(0, 0, 4) == "unverified_claims"         # claimed only, none corroborated
    assert gap_classification(0, 0, 0) == "no_claim_desert"           # nobody even claims it

def test_coverage_by_geography_ranks_and_classifies():
    rows = coverage_by_geography("maternity", state="Bihar")
    assert rows, "expected Bihar maternity coverage rows"
    assert rows[0]["rank"] == 1 and rows[0]["desert_score"] >= rows[-1]["desert_score"]  # sorted desc
    assert {r["gap_classification"] for r in rows} <= {
        "confirmed_coverage", "unverified_claims", "no_claim_desert"}
    assert rows[0]["has_burden"]                                       # maternity carries NFHS burden
    s = coverage_summary(rows)
    assert s["districts"] == len(rows)

def test_coverage_demand_honesty_gradient():
    # emergency/trauma have NO NFHS demand proxy → demand_available False (ranked by supply scarcity);
    # oncology/icu/maternity DO have measured demand.
    assert coverage_by_geography("emergency", state="Bihar")[0]["demand_available"] is False
    assert coverage_by_geography("oncology", state="Bihar")[0]["demand_available"] is True

def test_coverage_toggle_changes_trust_weighted_supply():
    off = {r["district"]: r["trust_weighted_supply"] for r in coverage_by_geography("maternity", "Bihar", count_unverified=False)}
    on = {r["district"]: r["trust_weighted_supply"] for r in coverage_by_geography("maternity", "Bihar", count_unverified=True)}
    assert any(on[d] > off[d] for d in off)                           # counting unverified raises some supply


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
