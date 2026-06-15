"""End-to-end Rung 0 demo: burden -> cost -> impact -> ranking, with breakdown.
Uses synthetic districts so it runs before the real tables exist."""
from core.burden import burden_score, people_reached, impact_per_cost
from core.cost import mission_cost
import json

# synthetic candidate districts (stand-ins for NFHS-5 rows + resolved geo)
districts = [
    {"name":"District A (remote, high burden)","population":900000,
     "women_anaemic_pct":"82","children_anaemic_pct":"75",
     "distance_km":380,"drive_hours":7.5},
    {"name":"District B (near city, moderate)","population":1200000,
     "women_anaemic_pct":"58","children_anaemic_pct":"54",
     "distance_km":60,"drive_hours":1.2},
    {"name":"District C (near, high, some suppressed)","population":700000,
     "women_anaemic_pct":"79","children_anaemic_pct":"*",
     "distance_km":95,"drive_hours":2.0},
]

intervention = "anaemia"
rows = []
for d in districts:
    b = burden_score(d, intervention)
    pr = people_reached(d, b)
    cost = mission_cost(d["distance_km"], d["drive_hours"], team_size=6, days=7)
    ipc = impact_per_cost(pr, cost["total_usd"])
    rows.append((d["name"], b, pr, cost, ipc))

# rank by impact-per-cost (None sinks to bottom)
rows.sort(key=lambda r: (r[4] is not None, r[4]), reverse=True)

print(f"\nINTERVENTION: {intervention}  |  ranking by impact-per-cost\n" + "="*70)
for i,(name,b,pr,cost,ipc) in enumerate(rows,1):
    print(f"\n#{i}  {name}")
    print(f"    burden score : {b['score']}  (confidence: {b['confidence']}"
          + (f", missing: {b['missing_indicators']}" if b['missing_indicators'] else "") + ")")
    print(f"    people reached~: {pr['value']}  [{pr['confidence']}]  ({pr['note']})")
    print(f"    mission cost : ${cost['total_usd']:,}")
    print(f"        transport ${cost['breakdown']['transport_usd']:,} | "
          f"stay ${cost['breakdown']['stay_usd']:,} | "
          f"reach-time ${cost['breakdown']['reach_time_cost_usd']:,}")
    print(f"    IMPACT/COST  : {ipc}")
