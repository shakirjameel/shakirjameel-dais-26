"""
Generate a self-contained HTML knowledge graph visualization for the
Medical Mission Deployment Copilot. Queries Databricks live, builds
nodes/edges, injects into a D3.js force-directed graph template.

Usage:
    python generate_kg_viz.py

Output:
    output/knowledge_graph.html
"""

import json
import math
from pathlib import Path
from databricks import sql

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
DB_HOST = "dbc-2f9d7b87-5aa9.cloud.databricks.com"
DB_HTTP_PATH = "/sql/1.0/warehouses/248996ee378e4a9d"
DB_TOKEN = "dapi30966aeb7adc407b4cf4826b042eb53b"
CATALOG = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset"

CANDIDATE_STATES = ("Bihar", "Jharkhand")
STAGING = {"name": "Patna", "lat": 25.5941, "lon": 85.1376}

INTERVENTION_INDICATORS = {
    "maternal_health": [
        ("institutional_birth_5y_pct", "low_is_worse"),
        ("mothers_who_had_at_least_4_anc_visits_lb5y_pct", "low_is_worse"),
        ("births_attended_by_skilled_hp_5y_10_pct", "low_is_worse"),
        ("all_w15_49_who_are_anaemic_pct", "high_is_worse"),
    ],
    "anaemia": [
        ("all_w15_49_who_are_anaemic_pct", "high_is_worse"),
    ],
    "child_nutrition": [
        ("child_u5_who_are_stunted_height_for_age_18_pct", "high_is_worse"),
    ],
}

INDICATOR_LABELS = {
    "institutional_birth_5y_pct": "Institutional Births (5yr %)",
    "mothers_who_had_at_least_4_anc_visits_lb5y_pct": "4+ ANC Visits (%)",
    "births_attended_by_skilled_hp_5y_10_pct": "Skilled Birth Attendance (%)",
    "all_w15_49_who_are_anaemic_pct": "Women 15-49 Anaemic (%)",
    "child_u5_who_are_stunted_height_for_age_18_pct": "Child U5 Stunted (%)",
}

COST_ASSUMPTIONS = {
    "transport_per_km_usd": {"value": 0.35, "label": "Transport", "unit": "$/km"},
    "per_diem_usd": {"value": 60.0, "label": "Per Diem", "unit": "$/person/day"},
    "team_size_default": {"value": 6, "label": "Team Size", "unit": "persons"},
    "mission_days_default": {"value": 7, "label": "Mission Days", "unit": "days"},
    "surgeon_day_value_usd": {"value": 800.0, "label": "Surgeon Day Value", "unit": "$/day"},
}

CATEGORY_COLORS = {
    "Geography": "#58a6ff",
    "Health Burden": "#f85149",
    "Supply": "#2ea043",
    "Cost Model": "#bc8cff",
    "Data Provenance": "#8b949e",
}

# ---------------------------------------------------------------------------
# DATA EXTRACTION
# ---------------------------------------------------------------------------

def query_databricks():
    """Pull district + facility data from live Databricks warehouse."""
    conn = sql.connect(
        server_hostname=DB_HOST,
        http_path=DB_HTTP_PATH,
        access_token=DB_TOKEN,
    )
    cursor = conn.cursor()

    # Districts
    cursor.execute(f"""
        SELECT
            TRIM(district_name) AS district_name,
            state_ut,
            institutional_birth_5y_pct,
            mothers_who_had_at_least_4_anc_visits_lb5y_pct,
            births_attended_by_skilled_hp_5y_10_pct,
            all_w15_49_who_are_anaemic_pct,
            child_u5_who_are_stunted_height_for_age_18_pct
        FROM {CATALOG}.nfhs_5_district_health_indicators
        WHERE state_ut IN ('Bihar', 'Jharkhand')
        ORDER BY state_ut, district_name
    """)
    districts = []
    for row in cursor.fetchall():
        districts.append({
            "district_name": row[0].strip() if row[0] else "",
            "state": row[1],
            "institutional_birth_5y_pct": _parse_val(row[2]),
            "mothers_who_had_at_least_4_anc_visits_lb5y_pct": _parse_val(row[3]),
            "births_attended_by_skilled_hp_5y_10_pct": _parse_val(row[4]),
            "all_w15_49_who_are_anaemic_pct": _parse_val(row[5]),
            "child_u5_who_are_stunted_height_for_age_18_pct": _parse_val(row[6]),
        })

    # Facility supply aggregates per city
    cursor.execute(f"""
        SELECT
            LOWER(TRIM(address_city)) AS city,
            address_stateOrRegion AS state,
            COUNT(*) AS total_facilities,
            SUM(CASE WHEN operatorTypeId = 'public' THEN 1 ELSE 0 END) AS public_fac,
            SUM(CASE WHEN operatorTypeId = 'private' THEN 1 ELSE 0 END) AS private_fac,
            SUM(CASE WHEN specialties LIKE '%gynecologyAndObstetrics%' THEN 1 ELSE 0 END) AS maternal_fac
        FROM {CATALOG}.facilities
        WHERE address_stateOrRegion IN ('Bihar', 'Jharkhand')
          AND latitude BETWEEN 6.0 AND 38.0
          AND longitude BETWEEN 68.0 AND 98.0
        GROUP BY LOWER(TRIM(address_city)), address_stateOrRegion
        ORDER BY total_facilities DESC
    """)
    supply = {}
    for row in cursor.fetchall():
        supply[row[0]] = {
            "city": row[0],
            "state": row[1],
            "total": row[2],
            "public": row[3],
            "private": row[4],
            "maternal": row[5],
        }

    cursor.close()
    conn.close()
    return districts, supply


def _parse_val(raw):
    """Parse NFHS value (may be string with spaces, None, or float)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s in ("", "*", "NA", "na"):
        return None
    s = s.strip("()")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def compute_burden(district, intervention):
    """Compute burden score 0..1 for a district/intervention."""
    indicators = INTERVENTION_INDICATORS[intervention]
    scores = []
    for col, direction in indicators:
        val = district.get(col)
        if val is None:
            continue
        if direction == "high_is_worse":
            scores.append(val / 100.0)
        else:
            scores.append(1.0 - val / 100.0)
    if not scores:
        return None
    return sum(scores) / len(scores)


def estimate_reachability(district_name):
    """Rough distance estimate from Patna based on known Bihar/Jharkhand geography."""
    known = {
        "patna": (0, 0.0), "nalanda": (75, 1.5), "gaya": (115, 2.5),
        "aurangabad": (140, 3.0), "nawada": (110, 2.3), "jehanabad": (55, 1.2),
        "arwal": (65, 1.4), "bhojpur": (60, 1.3), "buxar": (130, 2.8),
        "rohtas": (150, 3.2), "kaimur (bhabua)": (190, 4.0),
        "begusarai": (130, 2.5), "munger": (180, 3.5), "bhagalpur": (230, 4.5),
        "banka": (210, 4.0), "jamui": (170, 3.3), "lakhisarai": (150, 2.9),
        "sheikhpura": (65, 1.4), "vaishali": (35, 0.7), "muzaffarpur": (80, 1.7),
        "sitamarhi": (145, 3.0), "sheohar": (155, 3.2), "darbhanga": (140, 2.8),
        "madhubani": (175, 3.5), "samastipur": (100, 2.0), "saharsa": (220, 4.2),
        "supaul": (240, 4.6), "madhepura": (250, 4.8), "purnia": (300, 5.5),
        "katihar": (280, 5.2), "araria": (310, 5.8), "kishanganj": (340, 6.2),
        "saran": (95, 2.0), "siwan": (130, 2.7), "gopalganj": (150, 3.0),
        "pashchim champaran": (200, 4.0), "purba champaran": (170, 3.5),
        "khagaria": (160, 3.0), "east singhbhum": (380, 7.0),
        "west singhbhum": (420, 7.5), "ranchi": (330, 6.0),
        "dhanbad": (290, 5.3), "bokaro": (310, 5.6), "giridih": (250, 4.8),
        "deoghar": (280, 5.2), "dumka": (320, 5.8), "godda": (300, 5.5),
        "sahebganj": (340, 6.2), "pakur": (350, 6.4), "jamtara": (260, 5.0),
        "hazaribagh": (270, 5.0), "ramgarh": (300, 5.5), "chatra": (240, 4.5),
        "koderma": (210, 4.0), "palamu": (280, 5.2), "latehar": (310, 5.7),
        "garhwa": (330, 6.0), "lohardaga": (350, 6.3), "gumla": (380, 6.8),
        "simdega": (400, 7.2), "khunti": (340, 6.2), "saraikela-kharsawan": (360, 6.5),
    }
    key = district_name.lower().strip()
    if key in known:
        return known[key]
    return (200, 4.0)  # default moderate distance


# ---------------------------------------------------------------------------
# BUILD GRAPH DATA
# ---------------------------------------------------------------------------

def build_graph(districts, supply):
    """Build nodes and edges for the knowledge graph."""
    nodes = []
    edges = []
    node_id_map = {}

    # --- INTERVENTION NODES (Level 1) ---
    for intv in INTERVENTION_INDICATORS:
        nid = f"MMD-INT-{intv}"
        nodes.append({
            "id": nid,
            "title": intv.replace("_", " ").title(),
            "category": "Health Burden",
            "color": CATEGORY_COLORS["Health Burden"],
            "refs": [],
            "refCount": 0,
            "isCore": True,
            "level": 1,
            "isPending": False,
        })
        node_id_map[intv] = nid

    # --- INDICATOR NODES (Level 3) ---
    unique_indicators = {}
    for intv, indicators in INTERVENTION_INDICATORS.items():
        for col, direction in indicators:
            if col not in unique_indicators:
                nid = f"MMD-IND-{col}"
                unique_indicators[col] = nid
                nodes.append({
                    "id": nid,
                    "title": INDICATOR_LABELS.get(col, col),
                    "category": "Health Burden",
                    "color": CATEGORY_COLORS["Health Burden"],
                    "refs": [],
                    "refCount": 0,
                    "isCore": False,
                    "level": 3,
                    "isPending": False,
                })

    # ADDRESSES edges: Intervention → Indicator
    for intv, indicators in INTERVENTION_INDICATORS.items():
        for col, direction in indicators:
            edges.append({
                "source": node_id_map[intv],
                "target": unique_indicators[col],
                "type": "ADDRESSES",
            })

    # --- STAGING CITY NODE (Level 2) ---
    stg_id = "MMD-STG-patna"
    nodes.append({
        "id": stg_id,
        "title": f"{STAGING['name']} (Staging City)",
        "category": "Geography",
        "color": CATEGORY_COLORS["Geography"],
        "refs": [],
        "refCount": 0,
        "isCore": True,
        "level": 2,
        "isPending": False,
    })

    # --- COST ASSUMPTION NODES (Level 3) ---
    for field, info in COST_ASSUMPTIONS.items():
        nid = f"MMD-CST-{field}"
        nodes.append({
            "id": nid,
            "title": f"{info['label']}: {info['value']} {info['unit']}",
            "category": "Cost Model",
            "color": CATEGORY_COLORS["Cost Model"],
            "refs": [],
            "refCount": 0,
            "isCore": False,
            "level": 3,
            "isPending": False,
        })

    # --- DATA SOURCE NODES (Level 4) ---
    sources = [
        ("nfhs5", "NFHS-5 District Health Indicators (2019-21)"),
        ("facilities", "Virtue Foundation Facilities Dataset (2024)"),
        ("india_post", "India Post PIN Directory"),
    ]
    for src_key, src_title in sources:
        nid = f"MMD-SRC-{src_key}"
        nodes.append({
            "id": nid,
            "title": src_title,
            "category": "Data Provenance",
            "color": CATEGORY_COLORS["Data Provenance"],
            "refs": [],
            "refCount": 0,
            "isCore": False,
            "level": 4,
            "isPending": False,
        })

    # --- DISTRICT NODES (Level 2) ---
    # Compute rankings to mark top districts as core
    rankings = {}
    for intv in INTERVENTION_INDICATORS:
        scored = []
        for d in districts:
            burden = compute_burden(d, intv)
            if burden is None:
                continue
            dist_km, hours = estimate_reachability(d["district_name"])
            cost = dist_km * 2 * 0.35 + 60 * 6 * 7 + (hours * 2 / 8) * 800 * 6
            if cost > 0:
                metric = burden / cost
            else:
                metric = 0
            scored.append((d["district_name"], burden, metric, dist_km, hours, cost))
        scored.sort(key=lambda x: -x[2])
        rankings[intv] = scored[:10]

    top_districts = set()
    for intv, ranked in rankings.items():
        for name, *_ in ranked:
            top_districts.add(name.lower())

    district_ids = {}
    for idx, d in enumerate(districts):
        nid = f"MMD-DST-{idx:03d}"
        district_ids[d["district_name"].lower()] = nid
        is_core = d["district_name"].lower() in top_districts
        nodes.append({
            "id": nid,
            "title": f"{d['district_name']}, {d['state']}",
            "category": "Geography",
            "color": CATEGORY_COLORS["Geography"],
            "refs": [],
            "refCount": 0,
            "isCore": is_core,
            "level": 2,
            "isPending": False,
        })

        # REACHABLE_FROM edge: District → StagingCity
        edges.append({
            "source": nid,
            "target": stg_id,
            "type": "REACHABLE_FROM",
        })

        # DERIVED_FROM edge: District → NFHS-5
        edges.append({
            "source": nid,
            "target": "MMD-SRC-nfhs5",
            "type": "DERIVED_FROM",
        })

    # HAS_BURDEN edges (top-20 districts per intervention to avoid clutter)
    for intv in INTERVENTION_INDICATORS:
        ranked_names = [r[0].lower() for r in rankings[intv]]
        for name in ranked_names:
            if name not in district_ids:
                continue
            d_id = district_ids[name]
            for col, _ in INTERVENTION_INDICATORS[intv]:
                edges.append({
                    "source": d_id,
                    "target": unique_indicators[col],
                    "type": "HAS_BURDEN",
                })

    # RANKED_AT edges (top-10 per intervention)
    for intv, ranked in rankings.items():
        for name, *_ in ranked:
            key = name.lower()
            if key in district_ids:
                edges.append({
                    "source": district_ids[key],
                    "target": node_id_map[intv],
                    "type": "RANKED_AT",
                })

    # --- SUPPLY CLUSTER NODES (Level 3) ---
    for d in districts:
        key = d["district_name"].lower()
        sup = supply.get(key, {"total": 0, "public": 0, "private": 0, "maternal": 0})
        nid = f"MMD-FAC-{key.replace(' ', '_')[:20]}"
        nodes.append({
            "id": nid,
            "title": f"Supply: {d['district_name']} ({sup['total']} fac, {sup['public']} public)",
            "category": "Supply",
            "color": CATEGORY_COLORS["Supply"],
            "refs": [],
            "refCount": 0,
            "isCore": False,
            "level": 3,
            "isPending": sup["total"] == 0,
        })

        # SUPPLIES edge: SupplyCluster → District
        if key in district_ids:
            edges.append({
                "source": nid,
                "target": district_ids[key],
                "type": "SUPPLIES",
            })

        # DERIVED_FROM: SupplyCluster → Facilities dataset
        edges.append({
            "source": nid,
            "target": "MMD-SRC-facilities",
            "type": "DERIVED_FROM",
        })

    # Compute refs for nodes
    ref_map = {}
    for e in edges:
        ref_map.setdefault(e["source"], []).append(e["target"])
    for n in nodes:
        n["refs"] = ref_map.get(n["id"], [])
        n["refCount"] = len(n["refs"])

    return nodes, edges, rankings


# ---------------------------------------------------------------------------
# Q&A, GLOSSARY, ACRONYMS
# ---------------------------------------------------------------------------

def build_qa(districts, rankings):
    """Generate Q&A pairs for the chat widget."""
    qa = []
    idx = 0

    # Top rankings per intervention
    for intv, ranked in rankings.items():
        idx += 1
        top5 = ", ".join([f"{r[0]} (score: {r[1]:.2f})" for r in ranked[:5]])
        qa.append({
            "id": f"qa_{idx:04d}",
            "source": "computation",
            "sourceId": intv,
            "category": "ranking",
            "question": f"Which districts rank highest for {intv.replace('_', ' ')}?",
            "answer": f"<p>Top 5 by need-per-dollar: {top5}</p>",
            "compliance": "Y",
        })

    # Per-district Q&A for top districts
    for intv, ranked in rankings.items():
        for name, burden, metric, dist_km, hours, cost in ranked[:5]:
            idx += 1
            qa.append({
                "id": f"qa_{idx:04d}",
                "source": "computation",
                "sourceId": name,
                "category": "district_detail",
                "question": f"What is the burden score of {name} for {intv.replace('_', ' ')}?",
                "answer": f"<p>{name} has a burden score of <b>{burden:.3f}</b> (0-1 scale, higher = greater need) for {intv.replace('_', ' ')}.</p>",
                "compliance": "Y",
            })
            idx += 1
            qa.append({
                "id": f"qa_{idx:04d}",
                "source": "computation",
                "sourceId": name,
                "category": "reachability",
                "question": f"How far is {name} from Patna?",
                "answer": f"<p>{name} is approximately <b>{dist_km:.0f} km</b> from Patna ({hours:.1f} hours drive).</p>",
                "compliance": "Y",
            })
            idx += 1
            qa.append({
                "id": f"qa_{idx:04d}",
                "source": "computation",
                "sourceId": name,
                "category": "cost",
                "question": f"What would a mission to {name} cost?",
                "answer": f"<p>Estimated total mission cost: <b>${cost:,.0f}</b> (6-person team, 7 days).</p>",
                "compliance": "Y",
            })

    # General Q&A
    general = [
        ("What interventions are available?", "<p>Three interventions: <b>Maternal Health</b> (institutional births, ANC visits, skilled attendance, anaemia), <b>Anaemia</b> (women 15-49 anaemic %), and <b>Child Nutrition</b> (under-5 stunting).</p>"),
        ("How is burden score computed?", "<p>Burden is the mean of normalized NFHS-5 indicators for the intervention. Values are scaled 0-1 where 1 = highest need. Direction-aware: 'high_is_worse' indicators use value/100; 'low_is_worse' use 1-value/100.</p>"),
        ("What is need-per-dollar?", "<p>The ranking metric: coverage_gap / total_mission_cost. Higher = more impact per dollar spent. Districts with high burden but low cost rank highest.</p>"),
        ("What is a confirmed gap vs candidate gap?", "<p><b>Confirmed gap:</b> District has facility data (total_facilities > 0), so the coverage gap is measured. <b>Candidate gap:</b> No facility data resolved — could be a true desert or a data gap (Risk R2). Flagged for investigation.</p>"),
        ("Where is the staging city?", f"<p>Patna, Bihar (lat: {STAGING['lat']}, lon: {STAGING['lon']}). All reachability computed from this origin.</p>"),
        ("What cost assumptions are used?", "<p>Transport: $0.35/km, Per diem: $60/person/day, Team: 6, Days: 7, Surgeon day value: $800 (opportunity cost of lost operating time in transit).</p>"),
    ]
    for q, a in general:
        idx += 1
        qa.append({
            "id": f"qa_{idx:04d}",
            "source": "glossary",
            "sourceId": "system",
            "category": "general",
            "question": q,
            "answer": a,
            "compliance": "Y",
        })

    return qa


ACRONYMS = [
    {"acronym": "NFHS", "definition": "National Family Health Survey (India's DHS equivalent)"},
    {"acronym": "ORS", "definition": "OpenRouteService (road routing API)"},
    {"acronym": "NGO", "definition": "Non-Governmental Organization"},
    {"acronym": "ANC", "definition": "Antenatal Care"},
    {"acronym": "VF", "definition": "Virtue Foundation (partner NGO)"},
    {"acronym": "DAIS", "definition": "Data + AI Summit (Databricks conference)"},
    {"acronym": "ADM2", "definition": "Administrative Level 2 (District boundary)"},
    {"acronym": "PIN", "definition": "Postal Index Number (India Post)"},
    {"acronym": "OSM", "definition": "OpenStreetMap"},
    {"acronym": "IFA", "definition": "Iron and Folic Acid (supplement for anaemia)"},
    {"acronym": "PNC", "definition": "Postnatal Care"},
    {"acronym": "BMI", "definition": "Body Mass Index"},
]

GLOSSARY = [
    {"term": "Burden Score", "definition": "Normalized 0-1 composite of NFHS-5 health indicators for a district. Higher = greater health need."},
    {"term": "Coverage Gap", "definition": "Burden multiplied by (1 - supply_adequacy). Measures unmet need after accounting for existing facilities."},
    {"term": "Supply Adequacy", "definition": "Saturating curve: facilities / (facilities + half_saturation). Approaches 1.0 as facility count increases."},
    {"term": "Need-Per-Dollar", "definition": "The ranking metric: coverage_gap / total_mission_cost. Higher = more impact per dollar."},
    {"term": "Mission Cost", "definition": "Total: transport (distance x $/km x round-trip) + stay (per_diem x team x days) + reach-time-cost (lost operating days in transit)."},
    {"term": "Reach Time Cost", "definition": "Opportunity cost of surgeon time spent traveling. Converts drive hours to lost operating days at $800/surgeon/day."},
    {"term": "Staging City", "definition": "The origin point for all missions. Currently Patna, Bihar."},
    {"term": "Confirmed Gap", "definition": "District with facility data present (total > 0). Coverage gap is measured, not estimated."},
    {"term": "Candidate Gap", "definition": "District with no facility data resolved. Could be a true healthcare desert OR a data gap. Flagged for investigation."},
    {"term": "Half-Saturation", "definition": "The number of reachable facilities at which supply_adequacy = 0.5. Default: 3.0."},
    {"term": "Sensitivity Sweep", "definition": "Test whether the #1 ranked district changes when a cost assumption varies across its plausible range."},
]


# ---------------------------------------------------------------------------
# HTML TEMPLATE
# ---------------------------------------------------------------------------

def generate_html(nodes, edges, qa_data, rankings):
    """Generate the self-contained HTML knowledge graph visualization."""

    # Compute stats
    category_dist = {}
    for n in nodes:
        category_dist[n["category"]] = category_dist.get(n["category"], 0) + 1
    cat_dist_list = sorted(category_dist.items(), key=lambda x: -x[1])

    # Top referenced nodes
    incoming = {}
    for e in edges:
        incoming[e["target"]] = incoming.get(e["target"], 0) + 1
    top_referenced = sorted(incoming.items(), key=lambda x: -x[1])[:10]
    # Resolve titles
    title_map = {n["id"]: n["title"] for n in nodes}
    top10_display = [(title_map.get(nid, nid), count) for nid, count in top_referenced]

    data_block = f"""
var entityData = {json.dumps(nodes, indent=None)};
var graphNodes = {json.dumps(nodes, indent=None)};
var graphEdges = {json.dumps(edges, indent=None)};
var qaData = {json.dumps(qa_data, indent=None)};
var acronymData = {json.dumps(ACRONYMS, indent=None)};
var glossaryData = {json.dumps(GLOSSARY, indent=None)};
var CATEGORY_COLORS = {json.dumps(CATEGORY_COLORS, indent=None)};
var categoryGroups = {json.dumps({cat: [n["id"] for n in nodes if n["category"] == cat] for cat in CATEGORY_COLORS}, indent=None)};
var totalQA = {len(qa_data)};
var catDist = {json.dumps(cat_dist_list)};
var top10 = {json.dumps(top10_display)};
"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Medical Mission Deployment Copilot — Knowledge Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
:root {{
  --bg: #0d1117;
  --bg2: #161b22;
  --bg3: #21262d;
  --bg4: #30363d;
  --border: #30363d;
  --text: #e6edf3;
  --text2: #8b949e;
  --text3: #c9d1d9;
  --blue: #58a6ff;
  --green: #2ea043;
  --orange: #d29922;
  --red: #f85149;
  --purple: #bc8cff;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); font-size: 14px; overflow: hidden; height: 100vh; }}
.navbar {{ position:fixed; top:0; left:0; right:0; height:48px; background:var(--bg2); border-bottom:1px solid var(--border); display:flex; align-items:center; padding:0 16px; z-index:1000; }}
.navbar .logo {{ font-weight:700; font-size:16px; color:var(--blue); margin-right:24px; }}
.navbar .tabs {{ display:flex; gap:4px; }}
.navbar .tabs button {{ background:none; border:none; color:var(--text2); padding:8px 14px; cursor:pointer; border-radius:6px; font-size:13px; }}
.navbar .tabs button.active {{ background:var(--bg3); color:var(--text); }}
.navbar .tabs button:hover {{ color:var(--text); }}
.navbar .search-box {{ margin-left:auto; }}
.navbar .search-box input {{ background:var(--bg3); border:1px solid var(--border); color:var(--text); padding:6px 12px; border-radius:6px; width:220px; font-size:13px; display:none; }}
.navbar .search-box input.visible {{ display:block; }}
.main {{ position:fixed; top:48px; left:0; right:0; bottom:0; display:flex; }}
.tab-content {{ display:none; width:100%; height:100%; overflow:auto; }}
.tab-content.active {{ display:flex; }}

/* Graph Tab */
.graph-container {{ display:flex; width:100%; height:100%; }}
.left-sidebar {{ width:220px; background:var(--bg2); border-right:1px solid var(--border); padding:16px; overflow-y:auto; flex-shrink:0; }}
.left-sidebar h3 {{ font-size:12px; text-transform:uppercase; color:var(--text2); margin-bottom:12px; letter-spacing:0.5px; }}
.lifecycle-item {{ padding:8px 10px; margin-bottom:4px; border-radius:6px; cursor:pointer; font-size:12px; color:var(--text3); }}
.lifecycle-item:hover {{ background:var(--bg3); color:var(--text); }}
.lifecycle-item .step-num {{ display:inline-block; width:18px; height:18px; border-radius:50%; background:var(--bg4); text-align:center; line-height:18px; font-size:10px; margin-right:6px; color:var(--text2); }}
.legend {{ margin-top:20px; }}
.legend-item {{ display:flex; align-items:center; gap:8px; padding:4px 0; font-size:12px; color:var(--text2); cursor:pointer; }}
.legend-item:hover {{ color:var(--text); }}
.legend-dot {{ width:10px; height:10px; border-radius:50%; }}
.graph-svg-container {{ flex:1; position:relative; }}
.graph-svg-container svg {{ width:100%; height:100%; }}
.filters {{ position:absolute; top:12px; left:12px; display:flex; gap:8px; z-index:10; }}
.filters select, .filters button {{ background:var(--bg2); border:1px solid var(--border); color:var(--text); padding:6px 10px; border-radius:6px; font-size:12px; cursor:pointer; }}
.right-panel {{ width:300px; background:var(--bg2); border-left:1px solid var(--border); padding:16px; overflow-y:auto; display:none; flex-shrink:0; }}
.right-panel.visible {{ display:block; }}
.right-panel h3 {{ font-size:14px; margin-bottom:8px; }}
.right-panel .detail-id {{ font-size:11px; color:var(--text2); margin-bottom:4px; }}
.right-panel .detail-cat {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; margin-bottom:12px; }}
.right-panel .refs-list {{ list-style:none; }}
.right-panel .refs-list li {{ padding:4px 0; font-size:12px; color:var(--blue); cursor:pointer; }}
.right-panel .refs-list li:hover {{ text-decoration:underline; }}

/* Dashboard Tab */
.dashboard {{ padding:24px; width:100%; flex-direction:column; }}
.stats-row {{ display:flex; gap:16px; margin-bottom:24px; flex-wrap:wrap; }}
.stat-tile {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:16px 20px; min-width:140px; }}
.stat-tile .stat-val {{ font-size:24px; font-weight:700; color:var(--blue); }}
.stat-tile .stat-label {{ font-size:11px; color:var(--text2); margin-top:4px; }}
.card-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:12px; }}
.card {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:14px; }}
.card .card-title {{ font-size:13px; font-weight:600; margin-bottom:4px; }}
.card .card-id {{ font-size:11px; color:var(--text2); }}
.card .card-badge {{ display:inline-block; padding:2px 6px; border-radius:4px; font-size:10px; margin-top:6px; }}

/* Hierarchy Tab */
.hierarchy {{ padding:24px; width:100%; flex-direction:column; }}
.hier-section {{ margin-bottom:16px; }}
.hier-header {{ padding:10px 14px; background:var(--bg2); border:1px solid var(--border); border-radius:6px; cursor:pointer; display:flex; justify-content:space-between; align-items:center; }}
.hier-header:hover {{ background:var(--bg3); }}
.hier-children {{ padding:8px 0 8px 24px; display:none; }}
.hier-children.expanded {{ display:block; }}
.hier-item {{ padding:6px 10px; font-size:12px; color:var(--text3); border-left:2px solid var(--border); margin-bottom:2px; }}
.hier-item .badge {{ display:inline-block; padding:1px 6px; border-radius:4px; font-size:10px; margin-left:8px; background:var(--bg4); color:var(--text2); }}

/* Statistics Tab */
.statistics {{ padding:24px; width:100%; flex-direction:column; }}
.chart-section {{ margin-bottom:32px; }}
.chart-section h3 {{ font-size:14px; margin-bottom:12px; color:var(--text); }}
.bar-chart {{ display:flex; flex-direction:column; gap:6px; }}
.bar-row {{ display:flex; align-items:center; gap:10px; }}
.bar-label {{ width:200px; font-size:12px; color:var(--text3); text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.bar-fill {{ height:20px; border-radius:4px; transition:width 0.3s; }}
.bar-value {{ font-size:11px; color:var(--text2); min-width:30px; }}

/* Chat Widget */
.chat-widget {{ position:fixed; bottom:20px; right:20px; z-index:1001; }}
.chat-toggle {{ width:48px; height:48px; border-radius:50%; background:var(--blue); border:none; color:white; font-size:20px; cursor:pointer; box-shadow:0 4px 12px rgba(0,0,0,0.4); }}
.chat-panel {{ display:none; position:absolute; bottom:60px; right:0; width:360px; max-height:480px; background:var(--bg2); border:1px solid var(--border); border-radius:12px; overflow:hidden; box-shadow:0 8px 24px rgba(0,0,0,0.5); }}
.chat-panel.open {{ display:flex; flex-direction:column; }}
.chat-header {{ padding:12px 16px; background:var(--bg3); border-bottom:1px solid var(--border); font-weight:600; font-size:13px; display:flex; justify-content:space-between; }}
.chat-input {{ padding:12px; border-bottom:1px solid var(--border); }}
.chat-input input {{ width:100%; background:var(--bg); border:1px solid var(--border); color:var(--text); padding:8px 12px; border-radius:6px; font-size:13px; }}
.chat-results {{ flex:1; overflow-y:auto; padding:12px; max-height:340px; }}
.chat-result-item {{ padding:10px; margin-bottom:8px; background:var(--bg); border-radius:6px; }}
.chat-result-item .q {{ font-size:12px; font-weight:600; color:var(--blue); margin-bottom:4px; }}
.chat-result-item .a {{ font-size:12px; color:var(--text3); }}

/* Data Confidence Tab */
.confidence {{ padding:24px; width:100%; flex-direction:column; }}
.conf-group {{ margin-bottom:20px; }}
.conf-group h4 {{ font-size:13px; margin-bottom:8px; padding:6px 10px; border-radius:4px; }}
.conf-group.high h4 {{ background:rgba(46,160,67,0.15); color:var(--green); }}
.conf-group.medium h4 {{ background:rgba(210,153,34,0.15); color:var(--orange); }}
.conf-group.low h4 {{ background:rgba(248,81,73,0.15); color:var(--red); }}
.conf-bar {{ display:flex; height:8px; border-radius:4px; overflow:hidden; margin-bottom:4px; }}
.conf-bar .seg {{ height:100%; }}
.conf-item {{ padding:4px 10px; font-size:12px; color:var(--text3); }}
</style>
</head>
<body>

<div class="navbar">
  <div class="logo">Mission Copilot KG</div>
  <div class="tabs">
    <button class="active" onclick="switchTab('graph')">Graph</button>
    <button onclick="switchTab('dashboard')">Dashboard</button>
    <button onclick="switchTab('hierarchy')">Hierarchy</button>
    <button onclick="switchTab('confidence')">Data Confidence</button>
    <button onclick="switchTab('statistics')">Statistics</button>
  </div>
  <div class="search-box"><input id="globalSearch" placeholder="Search (Ctrl+K)..." /></div>
</div>

<div class="main">
  <!-- GRAPH TAB -->
  <div id="tab-graph" class="tab-content active graph-container">
    <div class="left-sidebar">
      <h3>Reasoning Chain</h3>
      <div class="lifecycle-item" onclick="highlightCategory('Health Burden')"><span class="step-num">1</span>Burden Assessment</div>
      <div class="lifecycle-item" onclick="highlightCategory('Supply')"><span class="step-num">2</span>Supply Mapping</div>
      <div class="lifecycle-item" onclick="highlightByEdge('SUPPLIES')"><span class="step-num">3</span>Coverage Gap</div>
      <div class="lifecycle-item" onclick="highlightByEdge('REACHABLE_FROM')"><span class="step-num">4</span>Reachability</div>
      <div class="lifecycle-item" onclick="highlightCategory('Cost Model')"><span class="step-num">5</span>Cost Computation</div>
      <div class="lifecycle-item" onclick="highlightByEdge('RANKED_AT')"><span class="step-num">6</span>Impact Ranking</div>
      <div class="lifecycle-item" onclick="highlightCore()"><span class="step-num">7</span>Mission Brief</div>
      <div class="legend">
        <h3>Categories</h3>
        <div class="legend-item" onclick="filterCategory('all')"><div class="legend-dot" style="background:#fff"></div>All</div>
        <div class="legend-item" onclick="filterCategory('Geography')"><div class="legend-dot" style="background:#58a6ff"></div>Geography (districts)</div>
        <div class="legend-item" onclick="filterCategory('Health Burden')"><div class="legend-dot" style="background:#f85149"></div>Health Burden</div>
        <div class="legend-item" onclick="filterCategory('Supply')"><div class="legend-dot" style="background:#2ea043"></div>Supply</div>
        <div class="legend-item" onclick="filterCategory('Cost Model')"><div class="legend-dot" style="background:#bc8cff"></div>Cost Model</div>
        <div class="legend-item" onclick="filterCategory('Data Provenance')"><div class="legend-dot" style="background:#8b949e"></div>Data Provenance</div>
      </div>
    </div>
    <div class="graph-svg-container">
      <div class="filters">
        <select id="catFilter" onchange="filterCategory(this.value)">
          <option value="all">All Categories</option>
          <option value="Geography">Geography</option>
          <option value="Health Burden">Health Burden</option>
          <option value="Supply">Supply</option>
          <option value="Cost Model">Cost Model</option>
          <option value="Data Provenance">Data Provenance</option>
        </select>
        <select id="edgeFilter" onchange="filterEdgeType(this.value)">
          <option value="all">All Relationships</option>
          <option value="HAS_BURDEN">HAS_BURDEN</option>
          <option value="SUPPLIES">SUPPLIES</option>
          <option value="REACHABLE_FROM">REACHABLE_FROM</option>
          <option value="ADDRESSES">ADDRESSES</option>
          <option value="RANKED_AT">RANKED_AT</option>
          <option value="DERIVED_FROM">DERIVED_FROM</option>
        </select>
        <button onclick="togglePending()">Toggle Pending</button>
      </div>
      <svg id="graphSvg"></svg>
    </div>
    <div class="right-panel" id="detailPanel">
      <h3 id="detailTitle"></h3>
      <div class="detail-id" id="detailId"></div>
      <div class="detail-cat" id="detailCat"></div>
      <h4 style="margin-top:12px;font-size:12px;color:var(--text2)">Outgoing</h4>
      <ul class="refs-list" id="detailOutgoing"></ul>
      <h4 style="margin-top:12px;font-size:12px;color:var(--text2)">Incoming</h4>
      <ul class="refs-list" id="detailIncoming"></ul>
    </div>
  </div>

  <!-- DASHBOARD TAB -->
  <div id="tab-dashboard" class="tab-content dashboard">
    <div class="stats-row" id="statsRow"></div>
    <div class="card-grid" id="cardGrid"></div>
  </div>

  <!-- HIERARCHY TAB -->
  <div id="tab-hierarchy" class="tab-content hierarchy" id="hierContainer"></div>

  <!-- DATA CONFIDENCE TAB -->
  <div id="tab-confidence" class="tab-content confidence" id="confContainer"></div>

  <!-- STATISTICS TAB -->
  <div id="tab-statistics" class="tab-content statistics" id="statsContainer"></div>
</div>

<!-- CHAT WIDGET -->
<div class="chat-widget">
  <button class="chat-toggle" onclick="toggleChat()">?</button>
  <div class="chat-panel" id="chatPanel">
    <div class="chat-header"><span>Knowledge Search</span><button onclick="toggleChat()" style="background:none;border:none;color:var(--text2);cursor:pointer;">x</button></div>
    <div class="chat-input"><input id="chatInput" placeholder="Ask about districts, costs, rankings..." onkeyup="chatSearch(event)" /></div>
    <div class="chat-results" id="chatResults"></div>
  </div>
</div>

<script>
// DATA
{data_block}

// EDGE COLORS
var EDGE_COLORS = {{
  "HAS_BURDEN": "#f85149",
  "SUPPLIES": "#2ea043",
  "REACHABLE_FROM": "#58a6ff",
  "ADDRESSES": "#d29922",
  "RANKED_AT": "#d29922",
  "DERIVED_FROM": "#8b949e",
  "COSTS": "#bc8cff"
}};

// TAB SWITCHING
function switchTab(tab) {{
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  document.querySelectorAll('.navbar .tabs button').forEach(btn => btn.classList.remove('active'));
  event.target.classList.add('active');
}}

// GLOBAL SEARCH
document.addEventListener('keydown', function(e) {{
  if ((e.ctrlKey || e.metaKey) && e.key === 'k') {{
    e.preventDefault();
    var inp = document.getElementById('globalSearch');
    inp.classList.toggle('visible');
    if (inp.classList.contains('visible')) inp.focus();
  }}
}});

// GRAPH
var svg = d3.select('#graphSvg');
var width, height;
var simulation, nodeElements, linkElements, labelElements;
var currentFilter = 'all';
var currentEdgeFilter = 'all';
var showPending = true;
var selectedNode = null;

function initGraph() {{
  var container = document.querySelector('.graph-svg-container');
  width = container.clientWidth;
  height = container.clientHeight;
  svg.attr('viewBox', [0, 0, width, height]);

  var g = svg.append('g');

  // Zoom
  var zoom = d3.zoom().scaleExtent([0.2, 4]).on('zoom', function(event) {{
    g.attr('transform', event.transform);
  }});
  svg.call(zoom);

  // Arrow markers
  var defs = svg.append('defs');
  Object.keys(EDGE_COLORS).forEach(function(type) {{
    defs.append('marker').attr('id', 'arrow-' + type).attr('viewBox', '0 -5 10 10')
      .attr('refX', 20).attr('refY', 0).attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto').append('path').attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', EDGE_COLORS[type] || '#555');
  }});

  // Build links
  var links = graphEdges.map(function(e) {{
    return {{source: e.source, target: e.target, type: e.type}};
  }});

  // Build node map
  var nodeMap = {{}};
  graphNodes.forEach(function(n) {{ nodeMap[n.id] = n; }});

  // Filter valid links
  links = links.filter(function(l) {{ return nodeMap[l.source] && nodeMap[l.target]; }});

  simulation = d3.forceSimulation(graphNodes)
    .force('link', d3.forceLink(links).id(function(d) {{ return d.id; }}).distance(120))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(35));

  linkElements = g.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', function(d) {{ return EDGE_COLORS[d.type] || '#555'; }})
    .attr('stroke-opacity', 0.5)
    .attr('stroke-width', 1.5)
    .attr('marker-end', function(d) {{ return 'url(#arrow-' + d.type + ')'; }});

  nodeElements = g.append('g').selectAll('circle').data(graphNodes).join('circle')
    .attr('r', function(d) {{ return d.level === 1 ? 16 : d.level === 2 ? 10 : 7; }})
    .attr('fill', function(d) {{ return d.color; }})
    .attr('stroke', function(d) {{ return d.isCore ? '#fff' : 'none'; }})
    .attr('stroke-width', function(d) {{ return d.isCore ? 2 : 0; }})
    .attr('opacity', function(d) {{ return d.isPending ? 0.4 : 1; }})
    .attr('cursor', 'pointer')
    .on('click', function(event, d) {{ selectNode(d); }})
    .call(d3.drag().on('start', dragStart).on('drag', dragging).on('end', dragEnd));

  labelElements = g.append('g').selectAll('text').data(graphNodes.filter(function(d) {{ return d.level <= 2; }})).join('text')
    .text(function(d) {{ return d.title.length > 20 ? d.title.substring(0, 18) + '..' : d.title; }})
    .attr('font-size', function(d) {{ return d.level === 1 ? '11px' : '9px'; }})
    .attr('fill', 'var(--text3)')
    .attr('dx', 14)
    .attr('dy', 4);

  simulation.on('tick', function() {{
    linkElements.attr('x1', function(d) {{ return d.source.x; }}).attr('y1', function(d) {{ return d.source.y; }})
      .attr('x2', function(d) {{ return d.target.x; }}).attr('y2', function(d) {{ return d.target.y; }});
    nodeElements.attr('cx', function(d) {{ return d.x; }}).attr('cy', function(d) {{ return d.y; }});
    labelElements.attr('x', function(d) {{ return d.x; }}).attr('y', function(d) {{ return d.y; }});
  }});

  // Click background to deselect
  svg.on('click', function(event) {{
    if (event.target === svg.node()) {{ deselectNode(); }}
  }});
}}

function dragStart(event, d) {{ if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }}
function dragging(event, d) {{ d.fx = event.x; d.fy = event.y; }}
function dragEnd(event, d) {{ if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }}

function selectNode(d) {{
  selectedNode = d;
  var panel = document.getElementById('detailPanel');
  panel.classList.add('visible');
  document.getElementById('detailTitle').textContent = d.title;
  document.getElementById('detailId').textContent = d.id;
  var catEl = document.getElementById('detailCat');
  catEl.textContent = d.category;
  catEl.style.background = d.color + '33';
  catEl.style.color = d.color;

  // Outgoing refs
  var outgoing = graphEdges.filter(function(e) {{ return e.source === d.id || (e.source && e.source.id === d.id); }});
  var outList = document.getElementById('detailOutgoing');
  outList.innerHTML = outgoing.map(function(e) {{
    var tid = typeof e.target === 'string' ? e.target : e.target.id;
    var title = entityData.find(function(n) {{ return n.id === tid; }});
    return '<li onclick="navigateToNode(\\'' + tid + '\\')">' + (title ? title.title : tid) + ' <span style="color:var(--text2)">(' + e.type + ')</span></li>';
  }}).join('');

  // Incoming refs
  var incoming = graphEdges.filter(function(e) {{ return e.target === d.id || (e.target && e.target.id === d.id); }});
  var inList = document.getElementById('detailIncoming');
  inList.innerHTML = incoming.map(function(e) {{
    var sid = typeof e.source === 'string' ? e.source : e.source.id;
    var title = entityData.find(function(n) {{ return n.id === sid; }});
    return '<li onclick="navigateToNode(\\'' + sid + '\\')">' + (title ? title.title : sid) + ' <span style="color:var(--text2)">(' + e.type + ')</span></li>';
  }}).join('');

  // Highlight connected
  var connected = new Set();
  outgoing.forEach(function(e) {{ connected.add(typeof e.target === 'string' ? e.target : e.target.id); }});
  incoming.forEach(function(e) {{ connected.add(typeof e.source === 'string' ? e.source : e.source.id); }});
  connected.add(d.id);
  nodeElements.attr('opacity', function(n) {{ return connected.has(n.id) ? 1 : 0.15; }});
  linkElements.attr('stroke-opacity', function(l) {{
    var sid = typeof l.source === 'string' ? l.source : l.source.id;
    var tid = typeof l.target === 'string' ? l.target : l.target.id;
    return (sid === d.id || tid === d.id) ? 0.8 : 0.05;
  }});
}}

function deselectNode() {{
  selectedNode = null;
  document.getElementById('detailPanel').classList.remove('visible');
  nodeElements.attr('opacity', function(d) {{ return d.isPending && !showPending ? 0 : (d.isPending ? 0.4 : 1); }});
  linkElements.attr('stroke-opacity', 0.5);
}}

function navigateToNode(id) {{
  var node = graphNodes.find(function(n) {{ return n.id === id; }});
  if (node) selectNode(node);
}}

function filterCategory(cat) {{
  currentFilter = cat;
  nodeElements.attr('display', function(d) {{
    if (cat === 'all') return 'block';
    return d.category === cat ? 'block' : 'none';
  }});
  linkElements.attr('display', function(l) {{
    if (cat === 'all') return 'block';
    var sn = typeof l.source === 'object' ? l.source : graphNodes.find(function(n){{ return n.id === l.source; }});
    var tn = typeof l.target === 'object' ? l.target : graphNodes.find(function(n){{ return n.id === l.target; }});
    return (sn && sn.category === cat) || (tn && tn.category === cat) ? 'block' : 'none';
  }});
  labelElements.attr('display', function(d) {{
    if (cat === 'all') return 'block';
    return d.category === cat ? 'block' : 'none';
  }});
}}

function filterEdgeType(type) {{
  currentEdgeFilter = type;
  linkElements.attr('display', function(l) {{
    if (type === 'all') return 'block';
    return l.type === type ? 'block' : 'none';
  }});
}}

function highlightCategory(cat) {{
  filterCategory(cat);
  document.getElementById('catFilter').value = cat;
}}

function highlightByEdge(type) {{
  filterEdgeType(type);
  document.getElementById('edgeFilter').value = type;
  filterCategory('all');
  document.getElementById('catFilter').value = 'all';
}}

function highlightCore() {{
  filterCategory('all');
  document.getElementById('catFilter').value = 'all';
  nodeElements.attr('opacity', function(d) {{ return d.isCore ? 1 : 0.15; }});
  linkElements.attr('stroke-opacity', function(l) {{
    var sn = typeof l.source === 'object' ? l.source : graphNodes.find(function(n){{ return n.id === l.source; }});
    var tn = typeof l.target === 'object' ? l.target : graphNodes.find(function(n){{ return n.id === l.target; }});
    return (sn && sn.isCore) || (tn && tn.isCore) ? 0.8 : 0.05;
  }});
}}

function togglePending() {{
  showPending = !showPending;
  nodeElements.attr('display', function(d) {{ return d.isPending && !showPending ? 'none' : 'block'; }});
}}

// DASHBOARD
function initDashboard() {{
  var statsRow = document.getElementById('statsRow');
  var stats = [
    {{val: graphNodes.length, label: 'Total Nodes'}},
    {{val: graphEdges.length, label: 'Total Edges'}},
    {{val: totalQA, label: 'Q&A Pairs'}},
    {{val: acronymData.length, label: 'Acronyms'}},
    {{val: glossaryData.length, label: 'Glossary Terms'}},
    {{val: graphNodes.filter(function(n){{ return n.isCore; }}).length, label: 'Core Entities'}},
  ];
  statsRow.innerHTML = stats.map(function(s) {{
    return '<div class="stat-tile"><div class="stat-val">' + s.val + '</div><div class="stat-label">' + s.label + '</div></div>';
  }}).join('');

  var grid = document.getElementById('cardGrid');
  grid.innerHTML = graphNodes.filter(function(n) {{ return n.level <= 2; }}).map(function(n) {{
    return '<div class="card"><div class="card-title">' + n.title + '</div><div class="card-id">' + n.id + '</div><div class="card-badge" style="background:' + n.color + '33;color:' + n.color + '">' + n.category + '</div>' + (n.isPending ? '<div class="card-badge" style="background:var(--orange);color:#000">No facility data</div>' : '') + '</div>';
  }}).join('');
}}

// HIERARCHY
function initHierarchy() {{
  var container = document.getElementById('tab-hierarchy');
  var categories = {{}};
  graphNodes.forEach(function(n) {{
    if (!categories[n.category]) categories[n.category] = [];
    categories[n.category].push(n);
  }});
  var html = '<div style="padding:24px;width:100%">';
  Object.keys(categories).sort().forEach(function(cat, i) {{
    var items = categories[cat];
    html += '<div class="hier-section"><div class="hier-header" onclick="this.nextElementSibling.classList.toggle(\\'expanded\\')">' +
      '<span style="color:' + (CATEGORY_COLORS[cat]||'#fff') + '">' + cat + '</span><span class="badge">' + items.length + '</span></div>' +
      '<div class="hier-children">' + items.map(function(n) {{
        return '<div class="hier-item">' + n.title + '<span class="badge">L' + n.level + '</span>' + (n.refCount ? '<span class="badge">' + n.refCount + ' refs</span>' : '') + '</div>';
      }}).join('') + '</div></div>';
  }});
  html += '</div>';
  container.innerHTML = html;
}}

// DATA CONFIDENCE
function initConfidence() {{
  var container = document.getElementById('tab-confidence');
  var districts = graphNodes.filter(function(n) {{ return n.category === 'Geography' && n.level === 2 && n.id !== 'MMD-STG-patna'; }});
  var high = districts.filter(function(n) {{ return !n.isPending && n.isCore; }});
  var medium = districts.filter(function(n) {{ return !n.isPending && !n.isCore; }});
  var low = districts.filter(function(n) {{ return n.isPending; }});

  var html = '<div style="padding:24px;width:100%">';
  html += '<h3 style="margin-bottom:16px">District Data Confidence Assessment</h3>';
  html += '<div class="conf-bar" style="height:16px;margin-bottom:16px"><div class="seg" style="width:' + (high.length/districts.length*100) + '%;background:var(--green)"></div><div class="seg" style="width:' + (medium.length/districts.length*100) + '%;background:var(--orange)"></div><div class="seg" style="width:' + (low.length/districts.length*100) + '%;background:var(--red)"></div></div>';
  html += '<p style="font-size:12px;color:var(--text2);margin-bottom:20px">High: ' + high.length + ' | Medium: ' + medium.length + ' | Low (no facility data): ' + low.length + '</p>';

  [['high', high, 'Top-ranked, measured gap'], ['medium', medium, 'Reachable, partial data'], ['low', low, 'No facility data (R2 — investigate)']].forEach(function(group) {{
    html += '<div class="conf-group ' + group[0] + '"><h4>' + group[0].toUpperCase() + ' confidence (' + group[1].length + ') — ' + group[2] + '</h4>';
    group[1].forEach(function(n) {{
      html += '<div class="conf-item">' + n.title + '</div>';
    }});
    html += '</div>';
  }});
  html += '</div>';
  container.innerHTML = html;
}}

// STATISTICS
function initStatistics() {{
  var container = document.getElementById('tab-statistics');
  var maxCat = catDist.length ? catDist[0][1] : 1;
  var maxRef = top10.length ? top10[0][1] : 1;

  var html = '<div style="padding:24px;width:100%">';
  html += '<div class="chart-section"><h3>Category Distribution</h3><div class="bar-chart">';
  catDist.forEach(function(item) {{
    var pct = (item[1] / maxCat * 100);
    var color = CATEGORY_COLORS[item[0]] || '#58a6ff';
    html += '<div class="bar-row"><div class="bar-label">' + item[0] + '</div><div class="bar-fill" style="width:' + pct + '%;background:' + color + '"></div><div class="bar-value">' + item[1] + '</div></div>';
  }});
  html += '</div></div>';

  html += '<div class="chart-section"><h3>Top 10 Most-Referenced Entities</h3><div class="bar-chart">';
  top10.forEach(function(item) {{
    var pct = (item[1] / maxRef * 100);
    html += '<div class="bar-row"><div class="bar-label">' + item[0] + '</div><div class="bar-fill" style="width:' + pct + '%;background:var(--blue)"></div><div class="bar-value">' + item[1] + '</div></div>';
  }});
  html += '</div></div>';
  html += '</div>';
  container.innerHTML = html;
}}

// CHAT
function toggleChat() {{
  document.getElementById('chatPanel').classList.toggle('open');
}}

function chatSearch(event) {{
  if (event.key !== 'Enter') return;
  var query = document.getElementById('chatInput').value.toLowerCase();
  var results = [];

  // Search Q&A
  qaData.forEach(function(qa) {{
    if (qa.question.toLowerCase().includes(query) || qa.answer.toLowerCase().includes(query)) {{
      results.push({{type: 'Q&A', q: qa.question, a: qa.answer}});
    }}
  }});

  // Search acronyms
  acronymData.forEach(function(ac) {{
    if (ac.acronym.toLowerCase().includes(query) || ac.definition.toLowerCase().includes(query)) {{
      results.push({{type: 'Acronym', q: ac.acronym, a: '<p>' + ac.definition + '</p>'}});
    }}
  }});

  // Search glossary
  glossaryData.forEach(function(gl) {{
    if (gl.term.toLowerCase().includes(query) || gl.definition.toLowerCase().includes(query)) {{
      results.push({{type: 'Glossary', q: gl.term, a: '<p>' + gl.definition + '</p>'}});
    }}
  }});

  // Search entities
  entityData.forEach(function(e) {{
    if (e.title.toLowerCase().includes(query) || e.id.toLowerCase().includes(query)) {{
      results.push({{type: 'Entity', q: e.title, a: '<p>' + e.category + ' (Level ' + e.level + ') — ' + e.refCount + ' refs</p>'}});
    }}
  }});

  var el = document.getElementById('chatResults');
  if (results.length === 0) {{
    el.innerHTML = '<div style="color:var(--text2);padding:12px;font-size:12px">No results found.</div>';
  }} else {{
    el.innerHTML = results.slice(0, 15).map(function(r) {{
      return '<div class="chat-result-item"><div class="q">[' + r.type + '] ' + r.q + '</div><div class="a">' + r.a + '</div></div>';
    }}).join('');
  }}
}}

// INIT
document.addEventListener('DOMContentLoaded', function() {{
  initGraph();
  initDashboard();
  initHierarchy();
  initConfidence();
  initStatistics();
}});
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print("Querying Databricks for district + facility data...")
    districts, supply = query_databricks()
    print(f"  Got {len(districts)} districts, {len(supply)} facility clusters")

    print("Building knowledge graph nodes and edges...")
    nodes, edges, rankings = build_graph(districts, supply)
    print(f"  Built {len(nodes)} nodes, {len(edges)} edges")

    print("Generating Q&A data...")
    qa_data = build_qa(districts, rankings)
    print(f"  Generated {len(qa_data)} Q&A pairs")

    print("Generating HTML visualization...")
    html = generate_html(nodes, edges, qa_data, rankings)

    output_path = Path(__file__).parent / "output" / "knowledge_graph.html"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"  Written to {output_path} ({len(html):,} bytes)")
    print("Done! Open output/knowledge_graph.html in a browser.")


if __name__ == "__main__":
    main()
