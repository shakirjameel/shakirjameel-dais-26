"""
stress_test.py — Adversarial stress test of the deterministic spine.

Two prongs:
  (1) property/edge-case probes on the pure functions (suppressed values, truncated JSON,
      boundary numbers, negation, division-by-zero, out-of-range), checking invariants.
  (2) integration fuzz: run coverage_by_geography / state_rollup over ALL capabilities × ALL
      states and assert invariants hold (no exceptions, scores in range, ranks contiguous,
      classification counts consistent).

Run:  ./.venv/bin/python tests/stress_test.py
Prints FAIL (broken invariant), WARN (known/acceptable weakness), and a PASS count.
"""
import os, sys, math, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mission_core.burden import (parse_nfhs_value, _normalize, burden_score, capability_demand,
                                  INTERVENTION_INDICATORS)
from mission_core.claims import classify_claim, _items, CAPABILITIES
from mission_core.coverage import (supply_adequacy, coverage_gap, trust_weighted_supply,
                                   data_confidence, gap_classification)
from mission_core.cost import mission_cost, days_to_meet_demand
from mission_core.impact import need_addressed_per_cost
from mission_core.reach import haversine_km
from mission_core.coverage_view import coverage_by_geography, coverage_summary, state_rollup
from mission_core.data_access import list_states

FAILS, WARNS, PASS = [], [], 0


def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILS.append(f"{name}  {detail}")


def warn(name, cond, detail=""):
    if not cond:
        WARNS.append(f"{name}  {detail}")


def section(t):
    print(f"\n── {t}")


# ===================================================================== 1. burden / parsing
section("burden.py — parse_nfhs_value / _normalize / burden_score")
for raw, exp_val, exp_flag in [
        (None, None, "suppressed"), ("", None, "suppressed"), ("*", None, "suppressed"),
        ("NA", None, "suppressed"), ("na", None, "suppressed"), ("abc", None, "suppressed"),
        ("1,234", None, "suppressed"), ("(29.5)", 29.5, "low_confidence"), ("45.2 ", 45.2, None),
        (" 0 ", 0.0, None), ("-5", -5.0, None)]:
    v, f = parse_nfhs_value(raw)
    check(f"parse_nfhs_value({raw!r})", v == exp_val and f == exp_flag, f"got {(v, f)}")

# _normalize clamps to [0,1] and direction is a true inverse
check("_normalize clamps high", _normalize(150, "high_is_worse") == 1.0)
check("_normalize clamps low", _normalize(-50, "high_is_worse") == 0.0)
check("_normalize None passthrough", _normalize(None, "high_is_worse") is None)
for x in (0, 25, 50, 75, 100):
    hi, lo = _normalize(x, "high_is_worse"), _normalize(x, "low_is_worse")
    check(f"_normalize inverse@{x}", abs((hi + lo) - 1.0) < 1e-9, f"{hi}+{lo}")
    check(f"_normalize range@{x}", 0.0 <= hi <= 1.0)

# burden_score: unknown intervention raises; all-suppressed -> None; range; monotonic
try:
    burden_score({}, "not_a_real_intervention"); check("burden unknown raises", False)
except KeyError:
    check("burden unknown raises", True)

cols = [c for c, _ in INTERVENTION_INDICATORS["maternal_health"]]
empty = {c: "*" for c in cols}
bs = burden_score(empty, "maternal_health")
check("burden all-suppressed -> None", bs["score"] is None and bs["confidence"] == "none", str(bs))

# monotonic: worsen the high_is_worse anaemia indicator -> burden up
base = {c: "50" for c in cols}
lo_b = burden_score({**base, "all_w15_49_who_are_anaemic_pct": "10"}, "maternal_health")["score"]
hi_b = burden_score({**base, "all_w15_49_who_are_anaemic_pct": "90"}, "maternal_health")["score"]
check("burden monotonic in anaemia", hi_b > lo_b, f"{lo_b} -> {hi_b}")
check("burden in [0,1]", all(0 <= b <= 1 for b in (lo_b, hi_b)))

# capability_demand: emergency/trauma have no proxy
for cap in ("emergency", "trauma"):
    d = capability_demand(base, cap)
    check(f"demand_available False for {cap}", d["demand_available"] is False and d["score"] is None)

# ===================================================================== 2. claims / trust grading
section("claims.py — _items / classify_claim")
check("_items valid json", _items('["a","b"]') == ["a", "b"])
check("_items truncated json", _items('["a","b","c') == ["a", "b", "c"])
check("_items empty", _items("") == [])
check("_items non-list", _items('"solo"') == ["solo"])

none = classify_claim({"capability": "", "description": "", "procedure": "", "equipment": ""}, "maternity")
check("claim empty -> none", none["confidence"] == "none")
flag = classify_claim({"maternal_supply": 1, "capability": "[\"eye care\"]"}, "maternity")
check("claim flag-only -> unverified", flag["confidence"] == "unverified", str(flag["confidence"]))
med = classify_claim({"capability": "[\"maternity ward\"]", "procedure": ""}, "maternity")
check("claim text-only -> medium", med["confidence"] == "medium")
check("claim text-only corrob_unavailable", med["corroboration_available"] is False)
high = classify_claim({"capability": "[\"obstetrics\"]", "procedure": "[\"c-section performed\"]"}, "maternity")
check("claim+corrob -> high", high["confidence"] == "high")
# grade is always in the valid set, for all capabilities, on garbage input
import json as _json
for cap in CAPABILITIES:
    g = classify_claim({"capability": "[\"random text 123\"]", "procedure": "[\"xyz\"]",
                        "maternal_supply": 0}, cap)
    check(f"grade in set ({cap})", g["confidence"] in ("high", "medium", "unverified", "none"))
# KNOWN WEAKNESS: negation is not understood (documented edge case)
neg = classify_claim({"capability": "[\"no maternity services available\"]", "procedure": ""}, "maternity")
warn("claim negation handled", neg["confidence"] == "none",
     f"'no maternity services' graded {neg['confidence']} (known: substring match, no negation guard)")

# ===================================================================== 3. coverage / gap
section("coverage.py — adequacy / gap / trust_weighted / classification / data_confidence")
check("adequacy 0 -> 0", supply_adequacy(0) == 0.0)
check("adequacy negative -> 0", supply_adequacy(-5) == 0.0)
check("adequacy asymptote <1", supply_adequacy(1e6) < 1.0 and supply_adequacy(1e6) > 0.99)
prev = -1
for s in (0, 1, 2, 3, 10, 100):
    a = supply_adequacy(s)
    check(f"adequacy range@{s}", 0 <= a <= 1)
    check(f"adequacy monotonic@{s}", a >= prev); prev = a

check("gap None burden -> None", coverage_gap(None, 0)["gap"] is None)
g0 = coverage_gap(0.8, 0)["gap"]
g5 = coverage_gap(0.8, 5)["gap"]
check("gap <= burden", g0 <= 0.8 + 1e-9 and g5 <= 0.8 + 1e-9)
check("gap shrinks with supply", g5 < g0, f"{g0} -> {g5}")
check("gap >= 0", g0 >= 0 and g5 >= 0)

check("tws ordering high>med>unv", trust_weighted_supply(1, 0, 0) > trust_weighted_supply(0, 1, 0) > 0)
check("tws excludes unverified by default", trust_weighted_supply(0, 0, 5) == 0.0)
check("tws includes unverified on toggle", trust_weighted_supply(0, 0, 5, True) > 0)
check("tws non-negative", trust_weighted_supply(0, 0, 0) == 0.0)

check("gapclass confirmed (high)", gap_classification(2, 0, 0) == "confirmed_coverage")
check("gapclass confirmed (medium)", gap_classification(0, 1, 0) == "confirmed_coverage")
check("gapclass unverified-only", gap_classification(0, 0, 3) == "unverified_claims")
check("gapclass desert", gap_classification(0, 0, 0) == "no_claim_desert")

check("dataconf data-poor", data_confidence(0, 0, 0) == "data-poor")
check("dataconf well-evidenced", data_confidence(5, 5, 3) == "well-evidenced")
check("dataconf claims-only", data_confidence(5, 2, 0) == "claims-only")
check("dataconf documented-gap", data_confidence(5, 0, 0) == "documented-gap")

# ===================================================================== 4. cost / impact / reach
section("cost.py / impact.py / reach.py")
mc = mission_cost(100, 3, team_size=6, days=7)
check("cost total == sum(parts)", abs(mc["total_usd"] - sum(mc["breakdown"].values())) < 0.01)
far = mission_cost(400, 8, team_size=6, days=7)["total_usd"]
near = mission_cost(50, 1, team_size=6, days=7)["total_usd"]
check("cost monotonic in distance", far > near)
check("cost monotonic in team", mission_cost(100, 3, team_size=12, days=7)["total_usd"]
      > mission_cost(100, 3, team_size=3, days=7)["total_usd"])
check("cost monotonic in days", mission_cost(100, 3, team_size=6, days=14)["total_usd"]
      > mission_cost(100, 3, team_size=6, days=3)["total_usd"])
check("cost zero distance -> stay only", mission_cost(0, 0, team_size=6, days=7)["breakdown"]["transport_usd"] == 0)

check("npc None gap -> None", need_addressed_per_cost(None, 100) is None)
check("npc zero cost -> None", need_addressed_per_cost(0.5, 0) is None)
check("npc negative cost -> None", need_addressed_per_cost(0.5, -10) is None)
check("npc positive", need_addressed_per_cost(0.5, 1000) > 0)

check("haversine same point 0", haversine_km(25, 85, 25, 85) == 0)
check("haversine symmetric", abs(haversine_km(25, 85, 28, 77) - haversine_km(28, 77, 25, 85)) < 1e-6)
pat_del = haversine_km(25.59, 85.14, 28.61, 77.21)   # Patna -> Delhi ~ 850-900 km
check("haversine Patna-Delhi sane", 800 < pat_del < 950, f"{pat_del:.0f} km")

check("days_to_meet 0 need -> 0 days", days_to_meet_demand(0, 6)["days"] == 0)
check("days_to_meet more team -> fewer days",
      days_to_meet_demand(0.9, 12)["days"] <= days_to_meet_demand(0.9, 2)["days"])

# ===================================================================== 5. INTEGRATION FUZZ
section("integration — coverage_by_geography × all capabilities × all states")
VALID_CLS = {"confirmed_coverage", "unverified_claims", "no_claim_desert"}
states = [None] + list_states()
combos = 0
for cap in CAPABILITIES:
    for stt in states:
        combos += 1
        try:
            rows = coverage_by_geography(cap, stt)
        except Exception as e:
            FAILS.append(f"coverage_by_geography({cap},{stt}) raised: {e!r}")
            continue
        if not rows:
            continue
        # invariants
        ranks = [r["rank"] for r in rows]
        check(f"ranks contiguous {cap}/{stt}", ranks == list(range(1, len(rows) + 1)),
              f"{ranks[:5]}…")
        for r in rows:
            if not (0.0 <= r["desert_score"] <= 1.0):
                FAILS.append(f"desert_score out of [0,1] {cap}/{r['district']}: {r['desert_score']}"); break
            if r["gap_classification"] not in VALID_CLS:
                FAILS.append(f"bad classification {cap}/{r['district']}: {r['gap_classification']}"); break
            if r["verified_supply"] != r["high"] + r["medium"]:
                FAILS.append(f"verified_supply != high+medium {cap}/{r['district']}"); break
            if r["trust_weighted_supply"] < 0:
                FAILS.append(f"negative tws {cap}/{r['district']}"); break
        else:
            PASS += 1
            # summary consistency: the three classes partition every district
            s = coverage_summary(rows)
            check(f"summary partitions {cap}/{stt}",
                  s["confirmed_coverage"] + s["unverified_claims"] + s["no_claim_desert"] == s["districts"])
print(f"   fuzzed {combos} capability×state combinations")

section("integration — state_rollup × all capabilities (36 states each)")
for cap in CAPABILITIES:
    try:
        roll = state_rollup(cap)
        check(f"state_rollup 36 states ({cap})", len(roll) == 36, f"got {len(roll)}")
        check(f"state_rollup fill valid ({cap})",
              all(r["fill_category"] for r in roll))
    except Exception as e:
        FAILS.append(f"state_rollup({cap}) raised: {e!r}\n{traceback.format_exc()}")

# ===================================================================== report
print("\n" + "=" * 60)
print(f"PASS: {PASS}   FAIL: {len(FAILS)}   WARN: {len(WARNS)}")
if WARNS:
    print("\nWARN (known/acceptable weaknesses):")
    for w in WARNS:
        print(f"  ⚠ {w}")
if FAILS:
    print("\nFAIL (broken invariants):")
    for f in FAILS:
        print(f"  ✗ {f}")
    sys.exit(1)
print("\nAll invariants held. ✓")
