"""
demo_mission.py — End-to-end cost-per-impact chain on REAL data (Rung 1 preview).

Ties together everything built so far:
  district_base.csv (burden + supply, point-in-polygon resolved)
  + cached ORS road reachability from the staging city
  -> mission_core.chain ranks districts by need-addressed-per-cost, with full provenance.

This is the deterministic output the AGENT will reason over (it ranks/explains; it does not
compute these numbers). Run:
    set -a; . ./.env; set +a
    ./.venv/bin/python demo_mission.py
"""

import sys
sys.path.insert(0, ".")

from mission_core.data_access import load_districts
from mission_core.chain import rank_districts
from data.reachability_precompute import make_reach_fn, STAGING, CANDIDATE_STATES

INTERVENTION = "maternal_health"
TEAM_SIZE, DAYS = 6, 7

reach_fn = make_reach_fn(STAGING["name"])
rows = [d for d in load_districts() if d["state_ut"].strip().lower() in CANDIDATE_STATES]
res = rank_districts(INTERVENTION, reach_fn, team_size=TEAM_SIZE, days=DAYS, top_n=None, districts=rows)
conf, cand = res["confirmed_gaps"], res["candidate_gaps"]

print(f"\nINTERVENTION: {INTERVENTION} | staging: {STAGING['name']} | team {TEAM_SIZE} x {DAYS} days")
print(f"confirmed gaps: {len(conf)}  |  candidate gaps (data needs work): {len(cand)}"
      f"  |  excluded: {len(res['excluded'])}")


def _show(r):
    b, g, c = r["burden"], r["gap"], r["cost"]
    print(f"\n#{r['tier_rank']}  {r['district']}, {r['state']}   [{r['data_confidence']}]")
    print(f"    burden {b['score']} ({b['confidence']}"
          + (f", missing {b['missing_indicators']}" if b['missing_indicators'] else "") + ")"
          + f" | reachable maternal supply {r['supply']} | total facilities {r['total_facilities']}"
          f" | gap {g['gap']}")
    print(f"    reach {r['reach']['distance_km']} km / {r['reach']['drive_hours']:.1f} h"
          f" | cost ${c['total_usd']:,.0f}  "
          f"(transport ${c['breakdown']['transport_usd']:,.0f} + stay ${c['breakdown']['stay_usd']:,.0f}"
          f" + reach-time ${c['breakdown']['reach_time_cost_usd']:,.0f})")
    print(f"    NEED-PER-$  {r['metric']:.3e}")


print("\n" + "=" * 84 + "\nTIER 1 — CONFIRMED GAPS (we have facility data; act on these)\n" + "=" * 84)
for r in conf[:6]:
    _show(r)

print("\n" + "=" * 84 + "\nTIER 2 — CANDIDATE GAPS (NO facility data — investigate / data needs work, R2)\n" + "=" * 84)
for r in cand[:5]:
    _show(r)

print("\n" + "=" * 84)
print("Signature beat (within CONFIRMED tier) — highest-burden districts vs where they rank:")
for r in sorted(conf, key=lambda r: -r["burden"]["score"])[:5]:
    print(f"    burden {r['burden']['score']:.2f}  ->  confirmed-rank #{r['tier_rank']:>2}  "
          f"({r['reach']['drive_hours']:.1f}h away, supply {r['supply']})  {r['district']}")
