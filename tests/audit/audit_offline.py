"""
audit_offline.py — READ-ONLY stress-test of the analytical logic (Tracks A–D, F).

Runs against mission_core in local-CSV mode. Mutates nothing. Prints a structured findings
report (PASS / WARN / FAIL + concrete numbers) that feeds VERIFICATION.md. Re-runnable.

Run: ./.venv/bin/python tests/audit/audit_offline.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# force local CSV backend
os.environ.pop("PGHOST", None)
os.environ.pop("LAKEBASE_ENDPOINT", None)

from mission_core import data_access as da
from mission_core.burden import CAPABILITY_INDICATORS, capability_demand, _normalize, parse_nfhs_value
from mission_core.coverage import (trust_weighted_supply, supply_adequacy, gap_classification,
                                   state_fill_category, SUPPLY_HALF_SATURATION, TRUST_WEIGHTS,
                                   DESERT_SHADE_THRESHOLDS)
from mission_core.cost import mission_cost, days_to_meet_demand, DEFAULTS
from mission_core.reach import distance_from_origin, ROAD_FACTOR, AVG_SPEED_KMH
from mission_core.coverage_view import coverage_by_geography, optimize, state_rollup
from mission_core.claims import CAPABILITIES, CAPABILITY_TERMS
from mission_core.geo_names import list_topo_states, to_topo_state, from_topo_state, list_origins

R = []  # (track, id, title, verdict, detail)
def rec(track, cid, title, verdict, detail=""):
    R.append((track, cid, title, verdict, detail))
    print(f"  [{verdict:4}] {track}.{cid}  {title}")
    if detail:
        for line in str(detail).splitlines():
            print(f"         {line}")

def hdr(t):
    print(f"\n{'='*78}\n{t}\n{'='*78}")

# district_base header (offline proxy for NFHS columns; live-confirmed in Track E)
with da.DISTRICT_BASE_CSV.open() as f:
    DB_COLS = set(next(csv.reader(f)))

# ============================================================ TRACK A — capability logic
hdr("TRACK A — capability → demand logic")

for cap, spec in CAPABILITY_INDICATORS.items():
    if spec is None:
        # emergency / trauma honesty
        d = capability_demand({}, cap)
        ok = (d["demand_available"] is False and d["score"] is None)
        rec("A", f"honesty.{cap}", f"{cap}: no NFHS proxy → demand_available=False",
            "PASS" if ok else "FAIL", f"note={d['note']!r}")
        continue
    missing = [c for c, _ in spec if c not in DB_COLS]
    rec("A", f"cols.{cap}", f"{cap}: all {len(spec)} indicator columns exist in district_base",
        "PASS" if not missing else "FAIL",
        ("missing=" + str(missing)) if missing else "cols=" + ", ".join(c for c, _ in spec))
    # direction listing for human review
    rec("A", f"dir.{cap}", f"{cap}: directions (manual clinical check)", "INFO",
        "\n".join(f"{c}  ->  {d}" for c, d in spec))

# normalization correctness
n_hi = _normalize(80.0, "high_is_worse")   # expect 0.8
n_lo = _normalize(80.0, "low_is_worse")    # expect 0.2
rec("A", "normalize", "_normalize maps 0–100→0–1 and inverts low_is_worse",
    "PASS" if (abs(n_hi-0.8) < 1e-9 and abs(n_lo-0.2) < 1e-9) else "FAIL",
    f"high_is_worse(80)={n_hi}  low_is_worse(80)={n_lo}")

# suppression handling
cases = {"*": (None, "suppressed"), "": (None, "suppressed"), "NA": (None, "suppressed"),
         "(29.5)": (29.5, "low_confidence"), "45.2 ": (45.2, None), "abc": (None, "suppressed")}
bad = {k: parse_nfhs_value(k) for k, exp in cases.items() if parse_nfhs_value(k) != exp}
rec("A", "suppress", "parse_nfhs_value handles * / (x) / NA / numeric",
    "PASS" if not bad else "FAIL", ("mismatches=" + str(bad)) if bad else "all 6 cases correct")

# demand actually computes for a real district (maternity)
mat = coverage_by_geography("maternity", state="Bihar")
with_demand = [r for r in mat if r["demand_available"]]
rec("A", "demand.live", "maternity demand computes from NFHS for Bihar districts",
    "PASS" if with_demand else "FAIL",
    f"{len(with_demand)}/{len(mat)} Bihar districts have a demand score; "
    f"example {with_demand[0]['district']}={with_demand[0]['burden']}" if with_demand else "")

# ============================================================ TRACK B — cost / distance
hdr("TRACK B — cost / distance / capacity")

# B1 monotonicity per fixed origin
def monotonicity(origin, state="Bihar", cap="maternity"):
    res = optimize(cap, state=state, origin=origin)
    rows = [r for r in res["districts"] if r["distance_km"] is not None]
    rows.sort(key=lambda r: r["distance_km"])
    inversions = []
    for i in range(len(rows)):
        for j in range(i+1, len(rows)):
            if rows[j]["cost_total_usd"] < rows[i]["cost_total_usd"] - 1e-6:
                # farther j cheaper than closer i
                inversions.append((rows[i], rows[j]))
    return rows, inversions

for origin in ("Delhi", "Patna (Bihar)"):
    rows, inv = monotonicity(origin)
    if not inv:
        rec("B", f"mono.{origin}", f"cost monotonic in distance from {origin}", "PASS",
            f"{len(rows)} routable districts, 0 inversions")
    else:
        a, b = inv[0]
        rec("B", f"mono.{origin}", f"cost NON-monotonic in distance from {origin}", "FAIL",
            f"{len(inv)} inversion pairs. e.g. CLOSER {a['district']} "
            f"({a['distance_km']}km, ${a['cost_total_usd']:,.0f}, {a['travel_source']}) costs MORE "
            f"than FARTHER {b['district']} ({b['distance_km']}km, ${b['cost_total_usd']:,.0f}, "
            f"{b['travel_source']})")

# B2 method-mixing within a single Patna run
res_p = optimize("maternity", state="Bihar", origin="Patna (Bihar)")
sources = sorted({r["travel_source"] for r in res_p["districts"]})
rec("B", "mixing", "single Patna run mixes travel-distance methods", "FAIL" if len(sources) > 1 else "PASS",
    f"travel_source values in one ranking: {sources}")

# B3 cost-term dominance
for (dist, hrs, lbl) in [(100, 2.5, "100km/2.5h"), (100, 4.0, "100km/4h(bad road)"),
                         (250, 5.5, "250km/5.5h")]:
    c = mission_cost(dist, hrs, team_size=6, days=7)
    b = c["breakdown"]
    rec("B", f"dominance.{lbl}", f"cost breakdown @ {lbl}, team6/7d", "INFO",
        f"total=${c['total_usd']:,.0f}  transport=${b['transport_usd']:,.0f} "
        f"stay=${b['stay_usd']:,.0f} reach_time=${b['reach_time_cost_usd']:,.0f} "
        f"(reach_time share={b['reach_time_cost_usd']/c['total_usd']:.0%})")

# B4 auto_days flattening
off = optimize("maternity", state="Bihar", origin="Delhi", auto_days=False, top_n=5)
on  = optimize("maternity", state="Bihar", origin="Delhi", auto_days=True,  top_n=5)
top_off = [d["district"] for d in off["districts"]]
top_on  = [d["district"] for d in on["districts"]]
spread_off = (off["districts"][0]["impact_score"] - off["districts"][-1]["impact_score"])
spread_on  = (on["districts"][0]["impact_score"]  - on["districts"][-1]["impact_score"])
rec("B", "autodays", "auto_days changes ranking / compresses impact spread",
    "WARN" if top_off != top_on or spread_on < spread_off else "PASS",
    f"top5 off={top_off}\ntop5 on ={top_on}\nimpact spread top1-top5: off={spread_off} on={spread_on}")

# B5 constants register (informational dump)
rec("B", "constants", "cost/geo constants in force", "INFO",
    f"transport_per_km={DEFAULTS.transport_per_km_usd} per_diem={DEFAULTS.per_diem_usd} "
    f"surgeon_day={DEFAULTS.surgeon_day_value_usd} team_default={DEFAULTS.team_size_default} "
    f"days_default={DEFAULTS.mission_days_default}\n"
    f"patients/vol/day={DEFAULTS.patients_per_volunteer_day} addressable_need={DEFAULTS.addressable_need_units}\n"
    f"ROAD_FACTOR={ROAD_FACTOR} AVG_SPEED_KMH={AVG_SPEED_KMH} HALF_SAT={SUPPLY_HALF_SATURATION}\n"
    f"TRUST_WEIGHTS={TRUST_WEIGHTS} SHADE_THRESHOLDS={DESERT_SHADE_THRESHOLDS}")

# ============================================================ TRACK C — coverage / desert
hdr("TRACK C — coverage / desert / map")

# C1 re-derive a district by hand
sample = next(r for r in coverage_by_geography("maternity", state="Bihar") if r["verified_supply"] > 0)
tws = trust_weighted_supply(sample["high"], sample["medium"], sample["unverified"], False)
adeq = supply_adequacy(tws)
dm = capability_demand(next(d for d in da.load_districts()
                            if da.normalize_name(d["nfhs_district"]) == sample["district_key"]), "maternity")
expected_desert = round((dm["score"] if dm["demand_available"] else 1.0) * (1 - adeq), 4)
ok = (abs(tws - sample["trust_weighted_supply"]) < 1e-6
      and abs(round(adeq, 4) - sample["supply_adequacy"]) < 1e-4
      and abs(expected_desert - sample["desert_score"]) < 1e-3)
rec("C", "rederive", f"hand re-derivation matches code ({sample['district']})",
    "PASS" if ok else "FAIL",
    f"high={sample['high']} medium={sample['medium']} unv={sample['unverified']} -> "
    f"tws={tws} (code {sample['trust_weighted_supply']}); adeq={round(adeq,4)} "
    f"(code {sample['supply_adequacy']}); demand={dm['score']}; "
    f"desert={expected_desert} (code {sample['desert_score']})")

# C2 gap_classification consistency
bad_cls = []
for r in coverage_by_geography("maternity"):
    exp = gap_classification(r["high"], r["medium"], r["unverified"])
    if exp != r["gap_classification"]:
        bad_cls.append((r["district"], exp, r["gap_classification"]))
rec("C", "classify", "gap_classification consistent across all districts",
    "PASS" if not bad_cls else "FAIL", f"{len(bad_cls)} mismatches" if bad_cls else "all consistent")

# C3 optimizer exclusion rule
allidx = optimize("maternity")   # all-India
ranked = allidx["districts"]
bad_rank = [r["district"] for r in ranked if r["total_facilities"] == 0 or r["distance_km"] is None]
rec("C", "exclude", "optimizer excludes no-data/no-route from ranking",
    "PASS" if not bad_rank else "FAIL",
    f"excluded_data_gaps={allidx['excluded_data_gaps']}; ranked={len(ranked)}; "
    f"violations={len(bad_rank)}; top impact={ranked[0]['impact_score'] if ranked else None}")

# C4 no_data never scores as covered
roll = state_rollup("maternity")
nd = [s for s in roll if s["fill_category"] == "no_data"]
nd_bad = [s["st_nm"] for s in nd if s["total_facilities"] != 0]
rec("C", "nodata", "states tagged no_data have zero facilities (never score 0-coverage)",
    "PASS" if not nd_bad else "FAIL", f"{len(nd)} no_data states; violations={nd_bad}")

# ============================================================ TRACK D — filters
hdr("TRACK D — filters")

rec("D", "capabilities", "Capability filter == 6 classifier capabilities", "INFO",
    f"{CAPABILITIES}")

# D state canonicalization round-trip
states = da.list_states()
unmapped = [s for s in states if to_topo_state(s) is None]
rec("D", "state.fwd", "every data state_ut maps to a GeoJSON st_nm",
    "PASS" if not unmapped else "FAIL", f"{len(states)} states; unmapped={unmapped}")
# reverse
rev_bad = []
for s in states:
    topo = to_topo_state(s)
    if topo and from_topo_state(topo, states) != s:
        rev_bad.append((s, topo, from_topo_state(topo, states)))
rec("D", "state.rev", "st_nm round-trips back to the same data state",
    "PASS" if not rev_bad else "WARN", f"round-trip mismatches={rev_bad}" if rev_bad else "all round-trip")

# every topo state hovered resolves (the hover-name bug regression guard)
roll_states = {s["st_nm"]: s["our_state"] for s in roll}
topo_unbound = [n for n in list_topo_states() if n not in roll_states]
rec("D", "map.hover", "every GeoJSON st_nm is present in state_rollup (hover-name guard)",
    "PASS" if not topo_unbound else "FAIL", f"unbound topo names={topo_unbound}" if topo_unbound else
    f"all {len(roll_states)} topo states bound")

# count_unverified toggle formula
s_off = trust_weighted_supply(2, 3, 5, False)   # 2 + 1.8 = 3.8
s_on  = trust_weighted_supply(2, 3, 5, True)    # + 1.5 = 5.3
rec("D", "toggle", "count_unverified adds unverified*0.3 only when on",
    "PASS" if (abs(s_off-3.8) < 1e-9 and abs(s_on-5.3) < 1e-9) else "FAIL",
    f"off={s_off} on={s_on}")

# origins sourced
rec("D", "origins", "optimizer origins list", "INFO", f"{len(list_origins())} origins; default first={list_origins()[0]}")

# ============================================================ TRACK F — integrity
hdr("TRACK F — cross-cutting integrity")

# F agent vs UI parity — agent tool must call coverage_view.optimize
import inspect
from agent import tools as T
src = inspect.getsource(T)
uses_optimize = "optimize" in src and "coverage_view" in src
rec("F", "parity", "agent tools reuse mission_core.coverage_view (no divergent metric)",
    "PASS" if uses_optimize else "WARN",
    "agent/tools.py references coverage_view.optimize" if uses_optimize else "check agent path")

# F context not imported by live app
import subprocess
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
grep = subprocess.run(["grep", "-rln", "import context", "--include=*.py",
                       os.path.join(root, "app.py"), os.path.join(root, "mission_core"),
                       os.path.join(root, "agent")], capture_output=True, text=True)
rec("F", "context", "live app does not import the stale context/ prototype",
    "PASS" if not grep.stdout.strip() else "FAIL",
    grep.stdout.strip() or "no imports of context/ in app.py / mission_core / agent")

# ============================================================ summary
hdr("SUMMARY")
from collections import Counter
c = Counter(v for *_, v, _ in [(t, i, ti, v, d) for (t, i, ti, v, d) in R])
print("  " + "  ".join(f"{k}={c[k]}" for k in ("PASS", "WARN", "FAIL", "INFO")))
fails = [(t, i, ti) for (t, i, ti, v, d) in R if v == "FAIL"]
warns = [(t, i, ti) for (t, i, ti, v, d) in R if v == "WARN"]
if fails:
    print("\n  FAILS:")
    for t, i, ti in fails:
        print(f"    {t}.{i}  {ti}")
if warns:
    print("\n  WARNS:")
    for t, i, ti in warns:
        print(f"    {t}.{i}  {ti}")
