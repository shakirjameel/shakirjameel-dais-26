"""
live_recon.py — READ-ONLY reconciliation of the served data against the LIVE Databricks source
(Track E). Re-queries the source tables and compares to the cached CSVs the app serves. Mutates
nothing (SELECT only). Run: ./.venv/bin/python tests/audit/live_recon.py
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import truststore
truststore.inject_into_ssl()

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState

WAREHOUSE_ID = os.environ.get("DBSQL_WAREHOUSE_ID", "3027e674d4e2102b")
CAT = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset"
INDIA = "latitude BETWEEN 6.0 AND 37.5 AND longitude BETWEEN 68.0 AND 97.5"
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "cache")

w = WorkspaceClient()

def q(sql):
    r = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=sql,
        disposition=Disposition.INLINE, format=Format.JSON_ARRAY, wait_timeout="50s")
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{r.status.state}: {r.status.error}")
    cols = [c.name for c in r.manifest.schema.columns]
    rows = list(r.result.data_array or [])
    return cols, rows

def csv_rows(name):
    with open(os.path.join(CACHE, name)) as f:
        return list(csv.DictReader(f))

def line(tag, msg):
    print(f"  [{tag}] {msg}")

print("="*78); print("TRACK E — LIVE SOURCE RECONCILIATION"); print("="*78)

# ---- E1 facilities counts + bbox + dupes ------------------------------------
_, r = q(f"""SELECT
    count(*) total,
    count(DISTINCT unique_id) uniq,
    sum(CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL AND {INDIA} THEN 1 ELSE 0 END) in_india,
    sum(CASE WHEN try_cast(capacity AS double) > 5000 THEN 1 ELSE 0 END) cap_over_5000,
    max(try_cast(capacity AS double)) cap_max_raw,
    sum(CASE WHEN try_cast(numberDoctors AS double) > 500 THEN 1 ELSE 0 END) docs_over_500,
    sum(CASE WHEN lower(cast(acceptsVolunteers AS string)) IN ('true','1','yes') THEN 1 ELSE 0 END) accepts_vol
  FROM {CAT}.facilities""")
total, uniq, in_india, cap_over, cap_max, docs_over, accepts = r[0]
ftext = csv_rows("facilities_text.csv")
print("\n-- E1 facilities (live) vs facilities_text.csv (served) --")
line("LIVE", f"raw rows={total}  distinct unique_id={uniq}  in-India(bbox & coords)={in_india}")
line("LIVE", f"winsor targets: capacity>5000 -> {cap_over} (raw max={cap_max}); doctors>500 -> {docs_over}")
line("LIVE", f"acceptsVolunteers true (raw) = {accepts}")
line("SERVED", f"facilities_text.csv rows = {len(ftext)}")
line("CHECK", f"served rows == live in-India? {len(ftext)} vs {in_india} -> "
              f"{'MATCH' if int(in_india)==len(ftext) else 'DELTA '+str(int(in_india)-len(ftext))}")
served_cap_over = sum(1 for x in ftext if x.get('capacity_beds') and float(x['capacity_beds']) > 5000)
line("CHECK", f"served capacity_beds>5000 (should be 0 after winsor) = {served_cap_over}")
served_dupes = len(ftext) - len({x['unique_id'] for x in ftext})
line("CHECK", f"served duplicate unique_id rows = {served_dupes}")

# ---- E2 NFHS row count + column presence + spot-checks -----------------------
ncols, _ = q(f"SELECT * FROM {CAT}.nfhs_5_district_health_indicators LIMIT 0")
_, rc = q(f"SELECT count(*) FROM {CAT}.nfhs_5_district_health_indicators")
nfhs_served = csv_rows("nfhs5_districts.csv")
print("\n-- E2 NFHS-5 (live) vs nfhs5_districts.csv (served) --")
line("LIVE", f"rows={rc[0][0]}  columns={len(ncols)}")
line("SERVED", f"rows={len(nfhs_served)}  columns={len(nfhs_served[0]) if nfhs_served else 0}")

from mission_core.burden import CAPABILITY_INDICATORS
used = sorted({c for spec in CAPABILITY_INDICATORS.values() if spec for c, _ in spec})
missing_live = [c for c in used if c not in ncols]
line("CHECK", f"all {len(used)} burden columns present in LIVE nfhs schema? "
              f"{'YES' if not missing_live else 'MISSING '+str(missing_live)}")

# spot-check specific district x indicator values live vs served (include a suppressed/low-conf if any)
sample_districts = [nfhs_served[i]["district_name"] for i in (0, 50, 200, 400, 600) if i < len(nfhs_served)]
col = "institutional_birth_5y_pct"
deltas = []
for dn in sample_districts:
    safe = dn.replace("'", "''")
    _, rr = q(f"SELECT `{col}` FROM {CAT}.nfhs_5_district_health_indicators WHERE district_name = '{safe}' LIMIT 1")
    live_v = (rr[0][0] if rr and rr[0] else None)
    served_v = next((x[col] for x in nfhs_served if x["district_name"] == dn), None)
    match = (str(live_v).strip() == str(served_v).strip())
    deltas.append((dn, live_v, served_v, match))
print("\n-- E2 spot-check: institutional_birth_5y_pct live vs served --")
for dn, lv, sv, m in deltas:
    line("OK " if m else "DELTA", f"{dn}: live={lv!r} served={sv!r}")

# ---- E3 district_capability aggregation reconciled to facility_claims (deterministic) ----
print("\n-- E3 aggregation: facility_claims.csv -> district_capability.csv --")
fc = csv_rows("facility_claims.csv")
dc = csv_rows("district_capability.csv")
from collections import defaultdict
agg = defaultdict(lambda: {"high": 0, "medium": 0, "unverified": 0})
for r_ in fc:
    k = (r_["district_key"], r_["capability"])
    conf = r_.get("claim_confidence")
    if conf in agg[k]:
        agg[k][conf] += 1
mismatch = 0
checked = 0
for r_ in dc:
    k = (r_["district_key"], r_["capability"])
    a = agg.get(k, {"high": 0, "medium": 0, "unverified": 0})
    checked += 1
    if (a["high"] != int(r_["high"] or 0) or a["medium"] != int(r_["medium"] or 0)
            or a["unverified"] != int(r_["unverified"] or 0)):
        mismatch += 1
        if mismatch <= 3:
            line("DELTA", f"{k}: claims-agg={a} vs served high/med/unv="
                          f"{r_['high']}/{r_['medium']}/{r_['unverified']}")
line("CHECK", f"district_capability rows reconciled to facility_claims counts: "
              f"{checked-mismatch}/{checked} match ({mismatch} mismatches)")
# verified_supply == high+medium invariant
vs_bad = sum(1 for r_ in dc if int(r_['verified_supply'] or 0) != int(r_['high'] or 0)+int(r_['medium'] or 0))
line("CHECK", f"verified_supply == high+medium for all rows: {len(dc)-vs_bad}/{len(dc)}")

# ---- E4 centroids sanity + join coverage ------------------------------------
print("\n-- E4 centroids + join coverage --")
cen = csv_rows("district_centroids.csv")
oob = [c for c in cen if not (6.0 <= float(c["lat"]) <= 37.5 and 68.0 <= float(c["lon"]) <= 97.5)]
line("CHECK", f"centroids in India bbox: {len(cen)-len(oob)}/{len(cen)} (oob={len(oob)})")
dc_keys = {r_["district_key"] for r_ in dc}
cen_keys = {c["district_key"] for c in cen}
line("INFO", f"district_capability districts={len(dc_keys)}  centroids={len(cen_keys)}  "
             f"capability rows w/o centroid={len(dc_keys - cen_keys)}")

print("\n" + "="*78); print("LIVE RECON COMPLETE"); print("="*78)
