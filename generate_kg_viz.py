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
from pathlib import Path
from databricks import sql

try:
    from mission_core.coverage_view import coverage_by_geography, state_rollup
    from mission_core.coverage import trust_weighted_supply, supply_adequacy, gap_classification
    from mission_core.data_access import load_facility_claims, normalize_name
    from mission_core.claims import CAPABILITIES as MC_CAPABILITIES, CAPABILITY_LABELS
    COVERAGE_AVAILABLE = True
except ImportError:
    COVERAGE_AVAILABLE = False

try:
    from agent.tools import rank_districts_tool
    RANKING_AVAILABLE = True
except ImportError:
    RANKING_AVAILABLE = False

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
DB_HOST = "dbc-2f9d7b87-5aa9.cloud.databricks.com"
DB_HTTP_PATH = "/sql/1.0/warehouses/248996ee378e4a9d"
DB_TOKEN = "dapi30966aeb7adc407b4cf4826b042eb53b"
CATALOG = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset"

CANDIDATE_STATES = None  # None = all states (queried dynamically)
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

CAPABILITIES = ["maternity", "icu", "nicu", "emergency", "oncology", "trauma"]

CLAIM_GRADES = [
    ("high", "Text + procedural corroboration"),
    ("medium", "Text claim only"),
    ("unverified", "Flag only, no text support"),
    ("none", "No claim or flag"),
]

GAP_CLASSIFICATIONS = [
    ("confirmed_coverage", "At least one corroborated claim"),
    ("unverified_claims", "Claims exist but none corroborated"),
    ("no_claim_desert", "No facility claims this capability"),
]

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
    "Verification": "#d29922",
    "Analysis": "#e3b341",
    "Cost Model": "#bc8cff",
    "Data Provenance": "#8b949e",
    "User Workflow": "#a371f7",
}

# ---------------------------------------------------------------------------
# DATA EXTRACTION
# ---------------------------------------------------------------------------

def query_databricks():
    conn = sql.connect(server_hostname=DB_HOST, http_path=DB_HTTP_PATH, access_token=DB_TOKEN)
    cursor = conn.cursor()

    cursor.execute(f"""
        SELECT TRIM(district_name) AS district_name, state_ut,
            institutional_birth_5y_pct,
            mothers_who_had_at_least_4_anc_visits_lb5y_pct,
            births_attended_by_skilled_hp_5y_10_pct,
            all_w15_49_who_are_anaemic_pct,
            child_u5_who_are_stunted_height_for_age_18_pct
        FROM {CATALOG}.nfhs_5_district_health_indicators
        ORDER BY state_ut, district_name
    """)
    districts = []
    for row in cursor.fetchall():
        districts.append({
            "district_name": row[0].strip() if row[0] else "",
            "state": row[1].strip() if row[1] else "",
            "institutional_birth_5y_pct": _parse_val(row[2]),
            "mothers_who_had_at_least_4_anc_visits_lb5y_pct": _parse_val(row[3]),
            "births_attended_by_skilled_hp_5y_10_pct": _parse_val(row[4]),
            "all_w15_49_who_are_anaemic_pct": _parse_val(row[5]),
            "child_u5_who_are_stunted_height_for_age_18_pct": _parse_val(row[6]),
        })

    cursor.execute(f"""
        SELECT LOWER(TRIM(address_city)) AS city, address_stateOrRegion AS state,
            COUNT(*) AS total_facilities,
            SUM(CASE WHEN operatorTypeId = 'public' THEN 1 ELSE 0 END) AS public_fac,
            SUM(CASE WHEN operatorTypeId = 'private' THEN 1 ELSE 0 END) AS private_fac,
            SUM(CASE WHEN specialties LIKE '%gynecologyAndObstetrics%' THEN 1 ELSE 0 END) AS maternal_fac
        FROM {CATALOG}.facilities
        WHERE latitude BETWEEN 6.0 AND 38.0 AND longitude BETWEEN 68.0 AND 98.0
        GROUP BY LOWER(TRIM(address_city)), address_stateOrRegion
        ORDER BY total_facilities DESC
    """)
    supply = {}
    for row in cursor.fetchall():
        supply[row[0]] = {"city": row[0], "state": row[1], "total": row[2],
                          "public": row[3], "private": row[4], "maternal": row[5]}

    # District-level centroids from India Post PIN codes (best coverage: 165K entries)
    cursor.execute(f"""
        SELECT LOWER(TRIM(p.district)) AS district_key,
            TRIM(p.statename) AS state,
            AVG(TRY_CAST(p.latitude AS DOUBLE)) AS lat,
            AVG(TRY_CAST(p.longitude AS DOUBLE)) AS lon,
            COUNT(*) AS pin_count
        FROM {CATALOG}.india_post_pincode_directory p
        WHERE TRY_CAST(p.latitude AS DOUBLE) IS NOT NULL
          AND TRY_CAST(p.longitude AS DOUBLE) IS NOT NULL
          AND TRY_CAST(p.latitude AS DOUBLE) BETWEEN 6.0 AND 38.0
          AND TRY_CAST(p.longitude AS DOUBLE) BETWEEN 68.0 AND 98.0
        GROUP BY LOWER(TRIM(p.district)), TRIM(p.statename)
    """)
    geo_coords = {}
    for row in cursor.fetchall():
        try:
            geo_coords[row[0]] = {"lat": float(row[2]), "lon": float(row[3]), "state": row[1]}
        except (TypeError, ValueError):
            continue

    # Supplement with facility centroids for districts not found via PIN
    cursor.execute(f"""
        SELECT LOWER(TRIM(n.district_name)) AS district_key,
            n.state_ut AS state,
            AVG(f.latitude) AS lat, AVG(f.longitude) AS lon
        FROM {CATALOG}.facilities f
        JOIN {CATALOG}.nfhs_5_district_health_indicators n
          ON LOWER(TRIM(f.address_city)) = LOWER(TRIM(n.district_name))
          AND f.address_stateOrRegion = n.state_ut
        WHERE f.latitude BETWEEN 6.0 AND 38.0 AND f.longitude BETWEEN 68.0 AND 98.0
        GROUP BY LOWER(TRIM(n.district_name)), n.state_ut
    """)
    for row in cursor.fetchall():
        key = row[0]
        if key not in geo_coords:
            try:
                geo_coords[key] = {"lat": float(row[2]), "lon": float(row[3]), "state": row[1]}
            except (TypeError, ValueError):
                continue

    # Top facilities per district for detail panel enrichment
    cursor.execute(f"""
        SELECT LOWER(TRIM(address_city)) AS district_key,
            name, operatorTypeId, specialties,
            COALESCE(TRY_CAST(numberDoctors AS INT), 0) AS doctors,
            COALESCE(TRY_CAST(capacity AS INT), 0) AS capacity
        FROM {CATALOG}.facilities
        WHERE latitude BETWEEN 6.0 AND 38.0 AND longitude BETWEEN 68.0 AND 98.0
          AND address_city IS NOT NULL AND address_city != ''
        ORDER BY address_city, COALESCE(TRY_CAST(capacity AS INT), 0) DESC
    """)
    facilities_by_district = {}
    for row in cursor.fetchall():
        key = row[0]
        if key not in facilities_by_district:
            facilities_by_district[key] = []
        if len(facilities_by_district[key]) < 5:
            facilities_by_district[key].append({
                "name": row[1] or "Unknown",
                "type": row[2] or "unknown",
                "specialties": (row[3] or "")[:60],
                "doctors": int(row[4]) if row[4] else 0,
                "capacity": int(row[5]) if row[5] else 0,
            })

    cursor.close()
    conn.close()
    return districts, supply, geo_coords, facilities_by_district


def _parse_val(raw):
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
    indicators = INTERVENTION_INDICATORS[intervention]
    scores = []
    for col, direction in indicators:
        val = district.get(col)
        if val is None:
            continue
        scores.append(val / 100.0 if direction == "high_is_worse" else 1.0 - val / 100.0)
    return sum(scores) / len(scores) if scores else None


def estimate_reachability(district_name):
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
    return known.get(key, (200, 4.0))


# ---------------------------------------------------------------------------
# BUILD GRAPH DATA
# ---------------------------------------------------------------------------

def build_graph(districts, supply):
    nodes = []
    edges = []
    node_id_map = {}

    def add_node(nid, title, category, level, is_core=False, is_pending=False):
        nodes.append({"id": nid, "title": title, "category": category,
                      "color": CATEGORY_COLORS.get(category, "#888"),
                      "refs": [], "refCount": 0, "isCore": is_core,
                      "level": level, "isPending": is_pending})
        return nid

    # --- INTERVENTION NODES (Level 1) ---
    for intv in INTERVENTION_INDICATORS:
        nid = add_node(f"MMD-INT-{intv}", intv.replace("_", " ").title(),
                       "Health Burden", 1, is_core=True)
        node_id_map[intv] = nid

    # --- INDICATOR NODES (Level 3) ---
    unique_indicators = {}
    for intv, indicators in INTERVENTION_INDICATORS.items():
        for col, direction in indicators:
            if col not in unique_indicators:
                unique_indicators[col] = add_node(
                    f"MMD-IND-{col}", INDICATOR_LABELS.get(col, col),
                    "Health Burden", 3)

    # ADDRESSES edges
    for intv, indicators in INTERVENTION_INDICATORS.items():
        for col, _ in indicators:
            edges.append({"source": node_id_map[intv], "target": unique_indicators[col], "type": "ADDRESSES"})

    # --- CAPABILITY NODES (Level 2) ---
    cap_ids = {}
    for cap in CAPABILITIES:
        cap_ids[cap] = add_node(f"MMD-CAP-{cap}", cap.title(), "Verification", 2, is_core=True)

    # --- CLAIM GRADE NODES (Level 3) ---
    grade_ids = {}
    for grade, desc in CLAIM_GRADES:
        grade_ids[grade] = add_node(f"MMD-GRD-{grade}", f"{grade.title()}: {desc}", "Verification", 3)

    # CORROBORATED_BY edges: Capability → ClaimGrade
    for cap in CAPABILITIES:
        for grade, _ in CLAIM_GRADES[:3]:
            edges.append({"source": cap_ids[cap], "target": grade_ids[grade], "type": "CORROBORATED_BY"})

    # --- GAP CLASSIFICATION NODES (Level 3) ---
    gap_ids = {}
    for gap_type, desc in GAP_CLASSIFICATIONS:
        gap_ids[gap_type] = add_node(f"MMD-GAP-{gap_type}", f"{gap_type.replace('_', ' ').title()}: {desc}",
                                     "Analysis", 3)

    # --- STAGING CITY NODE (Level 2) ---
    stg_id = add_node("MMD-STG-patna", f"{STAGING['name']} (Staging City)", "Geography", 2, is_core=True)

    # --- COST ASSUMPTION NODES (Level 3) ---
    for field, info in COST_ASSUMPTIONS.items():
        add_node(f"MMD-CST-{field}", f"{info['label']}: {info['value']} {info['unit']}", "Cost Model", 3)

    # --- DATA SOURCE NODES (Level 4) ---
    sources = [
        ("nfhs5", "NFHS-5 District Health Indicators (2019-21)"),
        ("facilities", "Virtue Foundation Facilities Dataset (2024)"),
        ("india_post", "India Post PIN Directory"),
        ("facility_text", "Facility Free-Text Ingest (claims.py)"),
    ]
    for src_key, src_title in sources:
        add_node(f"MMD-SRC-{src_key}", src_title, "Data Provenance", 4)

    # --- USER WORKFLOW NODES (Level 3-4) ---
    add_node("MMD-WF-scenario", "Scenario (saved inputs + ranking snapshot)", "User Workflow", 3)
    add_node("MMD-WF-review", "Review (approve / reject / investigate)", "User Workflow", 3)
    add_node("MMD-WF-shortlist", "Shortlist (pinned districts)", "User Workflow", 3)
    add_node("MMD-WF-note", "Note (free-text annotation)", "User Workflow", 4)

    # --- DISTRICT NODES (Level 2) ---
    rankings = {}
    for intv in INTERVENTION_INDICATORS:
        scored = []
        for d in districts:
            burden = compute_burden(d, intv)
            if burden is None:
                continue
            dist_km, hours = estimate_reachability(d["district_name"])
            cost = dist_km * 2 * 0.35 + 60 * 6 * 7 + (hours * 2 / 8) * 800 * 6
            metric = burden / cost if cost > 0 else 0
            scored.append((d["district_name"], burden, metric, dist_km, hours, cost))
        scored.sort(key=lambda x: -x[2])
        rankings[intv] = scored[:10]

    top_districts = set()
    for ranked in rankings.values():
        for name, *_ in ranked:
            top_districts.add(name.lower())

    district_ids = {}
    for idx, d in enumerate(districts):
        nid = f"MMD-DST-{idx:03d}"
        district_ids[d["district_name"].lower()] = nid
        is_core = d["district_name"].lower() in top_districts
        add_node(nid, f"{d['district_name']}, {d['state']}", "Geography", 2, is_core=is_core)
        edges.append({"source": nid, "target": stg_id, "type": "REACHABLE_FROM"})
        edges.append({"source": nid, "target": "MMD-SRC-nfhs5", "type": "DERIVED_FROM"})

    # HAS_BURDEN edges (top-10 per intervention)
    for intv in INTERVENTION_INDICATORS:
        for name, *_ in rankings[intv]:
            key = name.lower()
            if key not in district_ids:
                continue
            for col, _ in INTERVENTION_INDICATORS[intv]:
                edges.append({"source": district_ids[key], "target": unique_indicators[col], "type": "HAS_BURDEN"})

    # RANKED_AT edges
    for intv, ranked in rankings.items():
        for name, *_ in ranked:
            key = name.lower()
            if key in district_ids:
                edges.append({"source": district_ids[key], "target": node_id_map[intv], "type": "RANKED_AT"})

    # --- SUPPLY CLUSTER NODES (Level 3) ---
    for d in districts:
        key = d["district_name"].lower()
        sup = supply.get(key, {"total": 0, "public": 0, "private": 0, "maternal": 0})
        nid = f"MMD-FAC-{key.replace(' ', '_')[:20]}"
        add_node(nid, f"Supply: {d['district_name']} ({sup['total']} fac, {sup['public']} pub)",
                 "Supply", 3, is_pending=(sup["total"] == 0))
        if key in district_ids:
            edges.append({"source": nid, "target": district_ids[key], "type": "SUPPLIES"})
        edges.append({"source": nid, "target": "MMD-SRC-facilities", "type": "DERIVED_FROM"})

        # CLAIMS_CAPABILITY: supply clusters with maternal facilities → maternity capability
        if sup.get("maternal", 0) > 0:
            edges.append({"source": nid, "target": cap_ids["maternity"], "type": "CLAIMS_CAPABILITY"})

    # HAS_COVERAGE: top districts → capability
    for name, *_ in rankings.get("maternal_health", []):
        key = name.lower()
        if key in district_ids:
            edges.append({"source": district_ids[key], "target": cap_ids["maternity"], "type": "HAS_COVERAGE"})

    # CLASSIFIED_AS: districts with 0 supply → no_claim_desert; others → confirmed
    for d in districts:
        key = d["district_name"].lower()
        if key not in district_ids:
            continue
        sup = supply.get(key, {"total": 0, "maternal": 0})
        if sup["total"] == 0:
            edges.append({"source": district_ids[key], "target": gap_ids["no_claim_desert"], "type": "CLASSIFIED_AS"})
        elif sup.get("maternal", 0) > 0:
            edges.append({"source": district_ids[key], "target": gap_ids["confirmed_coverage"], "type": "CLASSIFIED_AS"})
        else:
            edges.append({"source": district_ids[key], "target": gap_ids["unverified_claims"], "type": "CLASSIFIED_AS"})

    # User workflow edges (conceptual — scenario/review/shortlist → district pattern)
    edges.append({"source": "MMD-WF-scenario", "target": district_ids.get("gaya", list(district_ids.values())[0]), "type": "SAVES"})
    edges.append({"source": "MMD-WF-review", "target": district_ids.get("vaishali", list(district_ids.values())[1]), "type": "REVIEWS"})
    edges.append({"source": "MMD-WF-shortlist", "target": district_ids.get("nalanda", list(district_ids.values())[2]), "type": "PINS"})

    # Compute refs
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
    qa = []
    idx = 0

    for intv, ranked in rankings.items():
        idx += 1
        top5 = ", ".join([f"{r[0]} (score: {r[1]:.2f})" for r in ranked[:5]])
        qa.append({"id": f"qa_{idx:04d}", "source": "computation", "sourceId": intv,
                   "category": "ranking",
                   "question": f"Which districts rank highest for {intv.replace('_', ' ')}?",
                   "answer": f"<p>Top 5 by need-per-dollar: {top5}</p>", "compliance": "Y"})

    for intv, ranked in rankings.items():
        for name, burden, metric, dist_km, hours, cost in ranked[:5]:
            idx += 1
            qa.append({"id": f"qa_{idx:04d}", "source": "computation", "sourceId": name,
                       "category": "district_detail",
                       "question": f"What is the burden score of {name} for {intv.replace('_', ' ')}?",
                       "answer": f"<p>{name} burden: <b>{burden:.3f}</b> (0-1, higher=greater need).</p>", "compliance": "Y"})
            idx += 1
            qa.append({"id": f"qa_{idx:04d}", "source": "computation", "sourceId": name,
                       "category": "reachability",
                       "question": f"How far is {name} from Patna?",
                       "answer": f"<p>{name}: ~<b>{dist_km:.0f} km</b> ({hours:.1f}h drive).</p>", "compliance": "Y"})
            idx += 1
            qa.append({"id": f"qa_{idx:04d}", "source": "computation", "sourceId": name,
                       "category": "cost",
                       "question": f"What would a mission to {name} cost?",
                       "answer": f"<p>Est. total: <b>${cost:,.0f}</b> (6-person, 7 days).</p>", "compliance": "Y"})

    general = [
        ("What interventions are available?", "<p>Maternal Health, Anaemia, Child Nutrition.</p>"),
        ("What capabilities can be verified?", "<p>Maternity, ICU, NICU, Emergency, Oncology, Trauma — each graded by claim evidence (high/medium/unverified/none).</p>"),
        ("How is burden score computed?", "<p>Mean of normalized NFHS-5 indicators. Direction-aware: high_is_worse=value/100, low_is_worse=1-value/100.</p>"),
        ("What is need-per-dollar?", "<p>Ranking metric: coverage_gap / mission_cost. Higher = more impact per dollar.</p>"),
        ("What are claim grades?", "<p><b>High:</b> text + procedural corroboration. <b>Medium:</b> text claim only. <b>Unverified:</b> flag only. <b>None:</b> no claim.</p>"),
        ("What is a gap classification?", "<p><b>Confirmed coverage:</b> corroborated claim exists. <b>Unverified claims:</b> claimed but not corroborated. <b>No-claim desert:</b> zero facilities claim this service.</p>"),
        ("What are scenarios?", "<p>Saved snapshots of planner inputs + ranking results. Allows comparing different mission configurations.</p>"),
        ("What cost assumptions are used?", "<p>Transport: $0.35/km, Per diem: $60/person/day, Team: 6, Days: 7, Surgeon day value: $800.</p>"),
        ("What is the anti-hallucination architecture?", "<p>The agent reasons but never computes. All numeric results come from deterministic Python functions. The agent explains and presents — never calculates.</p>"),
        ("What is the deterministic chain?", "<p>burden → coverage gap → cost → impact-per-dollar → rank. A forward-only pipeline that produces district rankings.</p>"),
        ("What is a two-tier ranking?", "<p><b>Confirmed gaps:</b> districts with facility data where gap is measured. <b>Candidate gaps:</b> districts with no facility data — potentially worse deserts, flagged for investigation.</p>"),
        ("What is trust-weighted supply?", "<p>Facilities are weighted by claim evidence grade: high=1.0, medium=0.6, unverified=0.3. Sum gives trust-weighted supply count.</p>"),
        ("What is a coverage desert?", "<p>A district where no facility claims a given capability. Desert score = burden × (1 - supply_adequacy).</p>"),
        ("How does facility text ingest work?", "<p>Pulls facility free-text descriptions from Databricks warehouse. Claims.py then searches for terminology matches against 6 capability dictionaries.</p>"),
        ("What is point-in-polygon resolution?", "<p>Spatial assignment of facilities to districts using ADM2 boundary polygons. Achieves 99.98% resolution rate.</p>"),
        ("What data sources are used?", "<p>NFHS-5 (2019-21) district indicators, Virtue Foundation facilities dataset (2024), India Post PIN directory, facility free-text claims.</p>"),
        ("What is supply adequacy?", "<p>A saturating curve: facilities / (facilities + 3.0). Diminishing returns — going from 0→1 facility matters more than 10→11.</p>"),
        ("What is the staging city?", "<p>Patna — the deployment hub from which all mission teams depart. Reachability is measured as road distance/time from Patna.</p>"),
        ("How are districts ranked?", "<p>By need-per-dollar: coverage_gap / mission_cost. Higher = more impact per dollar spent on deployment.</p>"),
        ("What is a sensitivity sweep?", "<p>Perturbs cost assumptions ±30% to test if the #1 ranked district changes. Robust recommendations hold across assumption ranges.</p>"),
        ("What are the 6 verifiable capabilities?", "<p>Maternity, ICU, NICU, Emergency, Oncology, Trauma. Each facility can claim zero or more, verified by text evidence.</p>"),
        ("What is corroboration?", "<p>When a facility's text description contains procedural terminology matching a claimed capability. Elevates claim from 'medium' to 'high' grade.</p>"),
        ("How does the user workflow work?", "<p>Planners can save Scenarios (input snapshots), Review districts (approve/reject/investigate), Pin to Shortlist, and attach Notes.</p>"),
        ("What states are covered?", "<p>Bihar and Jharkhand (eastern India). 62 districts total across both states.</p>"),
        ("What is R2 data risk?", "<p>Districts with no facility data resolved to their polygon. Could be a true desert or a data gap — flagged as 'candidate gap' for investigation.</p>"),
    ]
    for q, a in general:
        idx += 1
        qa.append({"id": f"qa_{idx:04d}", "source": "glossary", "sourceId": "system",
                   "category": "general", "question": q, "answer": a, "compliance": "Y"})
    return qa


ACRONYMS = [
    {"acronym": "NFHS", "definition": "National Family Health Survey — India's largest household health survey (NFHS-5: 2019-21)"},
    {"acronym": "ORS", "definition": "OpenRouteService — open-source road routing API for drive time/distance calculation"},
    {"acronym": "ANC", "definition": "Antenatal Care — medical care during pregnancy (4+ visits is WHO standard)"},
    {"acronym": "VF", "definition": "Virtue Foundation — partner NGO deploying medical missions"},
    {"acronym": "DAIS", "definition": "Data + AI Summit — Databricks annual conference (hackathon context)"},
    {"acronym": "ADM2", "definition": "Administrative Level 2 — district-level administrative boundary"},
    {"acronym": "ICU", "definition": "Intensive Care Unit — critical care facility for severe illness/injury"},
    {"acronym": "NICU", "definition": "Neonatal Intensive Care Unit — specialized care for critically ill newborns"},
    {"acronym": "PIN", "definition": "Postal Index Number — India Post 6-digit code used for geographic resolution"},
    {"acronym": "IFA", "definition": "Iron and Folic Acid — supplements for anaemia prevention in pregnancy"},
    {"acronym": "PNC", "definition": "Postnatal Care — medical care in the 6 weeks after delivery"},
    {"acronym": "MMR", "definition": "Maternal Mortality Ratio — deaths per 100,000 live births"},
    {"acronym": "IMR", "definition": "Infant Mortality Rate — deaths of infants under 1 year per 1,000 live births"},
    {"acronym": "NMR", "definition": "Neonatal Mortality Rate — deaths within first 28 days per 1,000 live births"},
    {"acronym": "U5MR", "definition": "Under-5 Mortality Rate — deaths before age 5 per 1,000 live births"},
    {"acronym": "EmOC", "definition": "Emergency Obstetric Care — life-saving interventions during childbirth complications"},
    {"acronym": "BEmONC", "definition": "Basic Emergency Obstetric and Newborn Care — 7 signal functions for safe delivery"},
    {"acronym": "CEmONC", "definition": "Comprehensive Emergency Obstetric and Newborn Care — BEmONC + surgery + transfusion"},
    {"acronym": "PHC", "definition": "Primary Health Centre — first-contact public health facility in rural India"},
    {"acronym": "CHC", "definition": "Community Health Centre — 30-bed referral hospital at block level"},
    {"acronym": "DH", "definition": "District Hospital — highest public facility at district level, typically 100-500 beds"},
    {"acronym": "SBA", "definition": "Skilled Birth Attendant — trained health professional attending delivery"},
    {"acronym": "ASHA", "definition": "Accredited Social Health Activist — community health worker in India's NHM"},
    {"acronym": "NHM", "definition": "National Health Mission — India's flagship public health program"},
    {"acronym": "KG", "definition": "Knowledge Graph — structured representation of entities and relationships"},
    {"acronym": "PAT", "definition": "Personal Access Token — authentication credential for Databricks API"},
    {"acronym": "ETL", "definition": "Extract, Transform, Load — data pipeline pattern"},
    {"acronym": "CSV", "definition": "Comma-Separated Values — tabular data file format"},
    {"acronym": "WHO", "definition": "World Health Organization — UN agency for international public health"},
    {"acronym": "SDG", "definition": "Sustainable Development Goals — UN 2030 targets including SDG 3 (health)"},
    {"acronym": "LLM", "definition": "Large Language Model — AI model powering the agent reasoning layer"},
]

GLOSSARY = [
    {"term": "Burden Score", "definition": "Normalized 0-1 composite of NFHS-5 indicators. Higher = greater need. Computed as mean of direction-adjusted indicator values."},
    {"term": "Coverage Gap", "definition": "burden × (1 - supply_adequacy). Quantifies unmet healthcare need after accounting for existing facilities."},
    {"term": "Supply Adequacy", "definition": "Saturating curve: facilities / (facilities + 3.0). Captures diminishing returns of additional facilities."},
    {"term": "Need-Per-Dollar", "definition": "Primary ranking metric: coverage_gap / mission_cost. Higher = more health impact per dollar spent."},
    {"term": "Claim Grade", "definition": "Confidence level in a facility's capability assertion. Four tiers: high (text + procedural corroboration), medium (text claim only), unverified (flag only), none (no claim)."},
    {"term": "Trust-Weighted Supply", "definition": "Sum of facilities weighted by claim grade (high=1.0, medium=0.6, unverified=0.3). More conservative than raw facility count."},
    {"term": "Desert Score", "definition": "burden × (1 - supply_adequacy). Identifies districts where high health need meets low service availability."},
    {"term": "Confirmed Gap", "definition": "District with facility data present. Coverage gap is measured with confidence. Tier 1 in ranking."},
    {"term": "Candidate Gap", "definition": "District with no facility data resolved. Could be a true service desert or a data gap (Risk R2). Tier 2 in ranking."},
    {"term": "Sensitivity Sweep", "definition": "Tests ranking robustness by varying cost assumptions ±30%. A robust recommendation holds #1 across the sweep."},
    {"term": "Deterministic Chain", "definition": "The fixed computation pipeline: burden → supply verification → coverage gap → reachability → cost → impact ranking. No LLM in the loop."},
    {"term": "Anti-Hallucination", "definition": "Architecture principle: the agent reasons and presents but never computes. All numbers come from deterministic functions."},
    {"term": "Staging City", "definition": "Deployment hub (Patna) from which mission teams travel. All reachability and transport costs measured from here."},
    {"term": "Reachability", "definition": "Road distance (km) and drive time (hours) from staging city to district center. Source: ORS API or fallback estimates."},
    {"term": "Point-in-Polygon", "definition": "Spatial method assigning facilities to districts by checking which ADM2 boundary polygon contains the facility's coordinates."},
    {"term": "Corroboration", "definition": "Verification step: facility free-text is searched for procedural terminology matching the claimed capability. Elevates grade from medium to high."},
    {"term": "Terminology Dictionary", "definition": "Curated word lists per capability (e.g., maternity: 'delivery', 'obstetric', 'labour ward'). Used by claims.py for text matching."},
    {"term": "Mission", "definition": "A planned medical deployment: team of specialists traveling to a district for a set number of days to deliver healthcare services."},
    {"term": "Intervention", "definition": "Type of medical mission: maternal health, anaemia treatment, or child nutrition. Each has its own burden indicators and supply mapping."},
    {"term": "Indicator", "definition": "A specific NFHS-5 metric (e.g., % institutional births, % women anaemic). Direction-tagged: high_is_worse or low_is_worse."},
    {"term": "Facility Cluster", "definition": "Aggregated supply node: all facilities within a district grouped by type. Used instead of individual facility nodes for graph performance."},
    {"term": "Scenario", "definition": "User-saved configuration: intervention choice, team size, cost assumptions, and resulting ranking snapshot. Enables comparison of mission plans."},
    {"term": "Review", "definition": "Planner decision on a district: approve (deploy), reject (skip), or investigate (needs more data). Persisted per user."},
    {"term": "Shortlist", "definition": "Pinned districts the planner wants to compare side-by-side before final mission selection."},
    {"term": "Data Provenance", "definition": "Traceability from any recommendation back through transformations to source data rows. Every claim can be audited."},
    {"term": "Ontology", "definition": "Formal schema defining entity types, their attributes, and the typed relationships between them in the knowledge graph."},
    {"term": "Knowledge Graph", "definition": "Network of entities (districts, facilities, capabilities) connected by typed relationships. Enables multi-hop queries the linear chain cannot express."},
    {"term": "Force-Directed Graph", "definition": "D3.js physics simulation that positions nodes by repulsion and edge attraction, creating an organic layout revealing structure."},
]

VOCABULARY = [
    {"term": "Institutional Delivery", "definition": "Childbirth occurring at a health facility (hospital, PHC, CHC) rather than at home. Key NFHS-5 indicator.", "category": "Health"},
    {"term": "Anaemia", "definition": "Low haemoglobin condition. In India, affects >50% of women aged 15-49. Key burden indicator for mission planning.", "category": "Health"},
    {"term": "Stunting", "definition": "Low height-for-age in children under 5. Indicator of chronic malnutrition. NFHS-5 threshold: height-for-age Z-score < -2.", "category": "Health"},
    {"term": "Antenatal Visit", "definition": "Medical checkup during pregnancy. WHO recommends minimum 4 visits. Low % indicates poor maternal care access.", "category": "Health"},
    {"term": "Skilled Birth Attendance", "definition": "Delivery attended by a trained health professional (doctor, nurse, midwife). Critical for reducing maternal/neonatal mortality.", "category": "Health"},
    {"term": "Obstetrics", "definition": "Medical specialty dealing with pregnancy, childbirth, and postpartum care. Key specialty for maternal health missions.", "category": "Medical"},
    {"term": "Gynaecology", "definition": "Medical specialty dealing with female reproductive system disorders. Often combined with obstetrics (Ob/Gyn).", "category": "Medical"},
    {"term": "Trauma Surgery", "definition": "Emergency surgical treatment of injuries. One of 6 verifiable capabilities in the claims system.", "category": "Medical"},
    {"term": "Oncology", "definition": "Medical specialty dealing with cancer diagnosis and treatment. Verifiable capability in facility claims.", "category": "Medical"},
    {"term": "Neonatal Care", "definition": "Medical care for newborns, especially in first 28 days. NICU capability is verified separately from general maternity.", "category": "Medical"},
    {"term": "Bihar", "definition": "Indian state in eastern India. Population ~125 million. One of highest burden states for maternal/child health indicators.", "category": "Geography"},
    {"term": "Jharkhand", "definition": "Indian state carved from Bihar in 2000. Population ~38 million. Significant tribal population with limited healthcare access.", "category": "Geography"},
    {"term": "Patna", "definition": "Capital of Bihar, staging city for all missions. Major transport hub with rail, air, and road connectivity.", "category": "Geography"},
    {"term": "District", "definition": "ADM2 administrative unit in India. Average population 1-3 million. Unit of analysis for burden and supply.", "category": "Geography"},
    {"term": "Lakebase", "definition": "Databricks managed PostgreSQL-compatible database. Used as persistence backend alongside Unity Catalog tables.", "category": "Technical"},
    {"term": "Unity Catalog", "definition": "Databricks governance layer for data assets. Three-level namespace: catalog.schema.table.", "category": "Technical"},
    {"term": "Databricks SQL Warehouse", "definition": "Serverless compute endpoint for SQL queries against Unity Catalog tables. Used for live data extraction.", "category": "Technical"},
    {"term": "D3.js", "definition": "JavaScript library for data-driven document manipulation. Used for the force-directed knowledge graph visualization.", "category": "Technical"},
    {"term": "Force Simulation", "definition": "Physics-based layout algorithm in D3.js. Nodes repel, edges attract, producing organic cluster arrangements.", "category": "Technical"},
    {"term": "Deterministic Spine", "definition": "Core Python pipeline that never uses randomness or LLM inference. Same inputs always produce same ranking output.", "category": "Architecture"},
    {"term": "Agent Tool", "definition": "Function exposed to the LLM agent for calling. Each tool has a schema and returns structured data the agent can reason about.", "category": "Architecture"},
    {"term": "Orchestrator", "definition": "Component that routes user queries to the appropriate agent tools and assembles the final response.", "category": "Architecture"},
    {"term": "Grounding Layer", "definition": "The deterministic computation modules (burden, coverage, cost, impact) that produce verifiable numbers for the agent.", "category": "Architecture"},
]


# ---------------------------------------------------------------------------
# MAP DATA
# ---------------------------------------------------------------------------

# Approximate state centroids for districts without facility match
STATE_CENTROIDS = {
    "Andaman & Nicobar Islands": (11.7, 92.7), "Andhra Pradesh": (15.9, 79.7),
    "Arunachal Pradesh": (28.2, 94.7), "Assam": (26.2, 92.9),
    "Bihar": (25.6, 85.1), "Chandigarh": (30.7, 76.8),
    "Chhattisgarh": (21.3, 81.6), "Dadra and Nagar Haveli & Daman and Diu": (20.4, 72.8),
    "Goa": (15.3, 74.0), "Gujarat": (22.3, 71.2),
    "Haryana": (29.0, 76.1), "Himachal Pradesh": (31.1, 77.2),
    "Jammu & Kashmir": (33.8, 76.6), "Jharkhand": (23.6, 85.3),
    "Karnataka": (15.3, 75.7), "Kerala": (10.9, 76.3),
    "Ladakh": (34.2, 77.6), "Lakshadweep": (10.6, 72.6),
    "Madhya Pradesh": (23.5, 78.6), "Maharastra": (19.7, 75.7),
    "Manipur": (24.7, 93.9), "Meghalaya": (25.5, 91.4),
    "Mizoram": (23.2, 92.9), "NCT of Delhi": (28.6, 77.2),
    "Nagaland": (26.2, 94.6), "Odisha": (20.5, 84.0),
    "Puducherry": (11.9, 79.8), "Punjab": (31.1, 75.3),
    "Rajasthan": (27.0, 74.2), "Sikkim": (27.5, 88.5),
    "Tamil Nadu": (11.1, 78.7), "Telangana": (18.1, 79.0),
    "Tripura": (23.9, 91.9), "Uttar Pradesh": (27.2, 80.0),
    "Uttarakhand": (30.1, 79.0), "West Bengal": (22.6, 87.8),
}


def build_map_data(districts, geo_coords, rankings, facilities_by_district=None,
                   coverage_by_district=None):
    """Build map marker data for all districts."""
    if facilities_by_district is None:
        facilities_by_district = {}
    if coverage_by_district is None:
        coverage_by_district = {}

    top_names = set()
    for ranked in rankings.values():
        for name, *_ in ranked:
            top_names.add(name.lower())

    map_points = []
    for idx, d in enumerate(districts):
        key = d["district_name"].lower()
        state = d["state"].strip()

        if key in geo_coords:
            lat = geo_coords[key]["lat"]
            lon = geo_coords[key]["lon"]
            geo_source = "pincode"
        elif state in STATE_CENTROIDS:
            base_lat, base_lon = STATE_CENTROIDS[state]
            lat = base_lat + ((idx * 7) % 20 - 10) * 0.05
            lon = base_lon + ((idx * 13) % 20 - 10) * 0.05
            geo_source = "estimated"
        else:
            continue

        burden = compute_burden(d, "maternal_health")
        is_top = key in top_names
        facs = facilities_by_district.get(key, [])

        cov = coverage_by_district.get(key, {})

        point = {
            "id": f"MMD-DST-{idx:03d}",
            "name": d["district_name"],
            "state": state,
            "lat": round(lat, 3),
            "lon": round(lon, 3),
            "burden": round(burden, 3) if burden else 0,
            "isTop": is_top,
            "geoSource": geo_source,
            "indicators": {
                "institutional_birth": d.get("institutional_birth_5y_pct"),
                "anc_4plus": d.get("mothers_who_had_at_least_4_anc_visits_lb5y_pct"),
                "skilled_birth": d.get("births_attended_by_skilled_hp_5y_10_pct"),
                "anaemia": d.get("all_w15_49_who_are_anaemic_pct"),
                "stunting": d.get("child_u5_who_are_stunted_height_for_age_18_pct"),
            },
            "facilities": facs,
            "coverage": cov,
        }
        map_points.append(point)

    return map_points


# ---------------------------------------------------------------------------
# COVERAGE + CLAIMS + RANKING DATA (from mission_core)
# ---------------------------------------------------------------------------

def build_coverage_data():
    """Build per-district coverage data for all 6 capabilities using mission_core."""
    if not COVERAGE_AVAILABLE:
        return {}, {}

    coverage_by_district = {}  # {district_key: {capability: {gc, ds, v, h, m, u}}}
    state_rollup_data = {}     # {capability: [{st_nm, fill_category, n_confirmed, n_desert, verified}]}

    for cap in MC_CAPABILITIES:
        try:
            rows = coverage_by_geography(cap, None)
        except Exception:
            continue

        for r in rows:
            key = normalize_name(r["district"])
            if key not in coverage_by_district:
                coverage_by_district[key] = {}
            coverage_by_district[key][cap] = {
                "gc": r["gap_classification"],
                "ds": round(r["desert_score"], 3),
                "v": r["verified_supply"],
                "h": r["high"],
                "m": r["medium"],
                "u": r["unverified"],
            }

        try:
            roll = state_rollup(cap, False)
            state_rollup_data[cap] = [
                {"st": r["st_nm"], "fc": r["fill_category"],
                 "nc": r["n_confirmed"], "nd": r["n_desert"], "vf": r["verified_facilities"]}
                for r in roll
            ]
        except Exception:
            pass

    return coverage_by_district, state_rollup_data


def build_claim_evidence():
    """Build top-2 facility claims per district×capability for the detail panel."""
    if not COVERAGE_AVAILABLE:
        return {}

    try:
        all_claims = load_facility_claims()
    except Exception:
        return {}

    grouped = {}  # {district_key: {capability: [claims...]}}
    for c in all_claims:
        key = c.get("district_key", "")
        cap = c.get("capability", "")
        if not key or not cap:
            continue
        if key not in grouped:
            grouped[key] = {}
        if cap not in grouped[key]:
            grouped[key][cap] = []
        grouped[key][cap].append(c)

    CONF_ORDER = {"high": 0, "medium": 1, "unverified": 2}
    evidence = {}
    for dkey, caps in grouped.items():
        evidence[dkey] = {}
        for cap, claims in caps.items():
            claims.sort(key=lambda x: CONF_ORDER.get(x.get("claim_confidence", ""), 9))
            top2 = claims[:2]
            evidence[dkey][cap] = [
                {
                    "n": (cl.get("name") or "Unknown")[:40],
                    "c": (cl.get("claim_confidence") or "u")[0],  # h/m/u
                    "e": (cl.get("capability_evidence") or "")[:100],
                    "p": (cl.get("procedure_evidence") or "")[:100],
                    "u": (cl.get("source_url") or ""),
                }
                for cl in top2
            ]

    return evidence


def build_ranking_data():
    """Build deployment ranking data (confirmed + candidate gaps)."""
    if not RANKING_AVAILABLE:
        return None

    try:
        res = rank_districts_tool("maternal_health", team_size=6, days=7, top_n=15)
        if "error" in res:
            return None
        return {
            "confirmed": [
                {
                    "d": r["district"], "s": r["state"],
                    "npd": r["need_per_dollar"],
                    "b": round(r["burden_score"], 3) if r.get("burden_score") else None,
                    "ds": round(r.get("desert_score", 0), 3),
                    "cost": round(r["cost_total_usd"]),
                    "hrs": round(r["reach"]["drive_hours"], 1) if r.get("reach") else None,
                    "km": round(r["reach"]["distance_km"]) if r.get("reach") else None,
                }
                for r in res.get("confirmed_gaps", [])[:10]
            ],
            "candidate": [
                {
                    "d": r["district"], "s": r["state"],
                    "npd": r["need_per_dollar"],
                    "b": round(r["burden_score"], 3) if r.get("burden_score") else None,
                    "cost": round(r["cost_total_usd"]),
                }
                for r in res.get("candidate_gaps", [])[:5]
            ],
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# HTML TEMPLATE
# ---------------------------------------------------------------------------

def generate_html(nodes, edges, qa_data, rankings, map_points=None,
                  state_rollup_data=None, claim_evidence=None, ranking_data=None,
                  districts=None, supply=None, coverage_by_district=None):
    if map_points is None:
        map_points = []
    if state_rollup_data is None:
        state_rollup_data = {}
    if claim_evidence is None:
        claim_evidence = {}
    if ranking_data is None:
        ranking_data = {}
    if districts is None:
        districts = []
    if supply is None:
        supply = {}
    if coverage_by_district is None:
        coverage_by_district = {}

    category_dist = {}
    for n in nodes:
        category_dist[n["category"]] = category_dist.get(n["category"], 0) + 1
    cat_dist_list = sorted(category_dist.items(), key=lambda x: -x[1])

    incoming = {}
    for e in edges:
        incoming[e["target"]] = incoming.get(e["target"], 0) + 1
    top_referenced = sorted(incoming.items(), key=lambda x: -x[1])[:10]
    title_map = {n["id"]: n["title"] for n in nodes}
    top10_display = [(title_map.get(nid, nid), count) for nid, count in top_referenced]

    # Build hierarchy data for the reasoning chain
    hierarchy_data = _build_hierarchy(nodes, rankings, ranking_data, districts, supply,
                                      coverage_by_district)

    # Extract unique states from Geography nodes for the Geo dropdown
    all_states = sorted(set(
        n["title"].split(", ")[-1] for n in nodes
        if n["category"] == "Geography" and n["level"] == 2 and ", " in n["title"]
        and n["id"] != "MMD-STG-patna"
    ))

    cap_labels = CAPABILITY_LABELS if COVERAGE_AVAILABLE else {
        c: c.title() for c in CAPABILITIES
    }

    data_block = f"""
var entityData = {json.dumps(nodes)};
var graphNodes = {json.dumps(nodes)};
var graphEdges = {json.dumps(edges)};
var qaData = {json.dumps(qa_data)};
var acronymData = {json.dumps(ACRONYMS)};
var glossaryData = {json.dumps(GLOSSARY)};
var vocabData = {json.dumps(VOCABULARY)};
var CATEGORY_COLORS = {json.dumps(CATEGORY_COLORS)};
var categoryGroups = {json.dumps({cat: [n["id"] for n in nodes if n["category"] == cat] for cat in CATEGORY_COLORS})};
var totalQA = {len(qa_data)};
var catDist = {json.dumps(cat_dist_list)};
var top10 = {json.dumps(top10_display)};
var hierarchyData = {json.dumps(hierarchy_data)};
var allStates = {json.dumps(all_states)};
var mapPoints = {json.dumps(map_points)};
var stateRollup = {json.dumps(state_rollup_data)};
var claimEvidence = {json.dumps(claim_evidence)};
var rankingData = {json.dumps(ranking_data)};
var capLabels = {json.dumps(cap_labels)};
var capabilities = {json.dumps(CAPABILITIES)};
"""

    html = _html_template(data_block)
    return html


def _ranking_hierarchy_children(ranking_data, fallback_top_ranked):
    """Build the Impact Ranking section with confirmed/candidate tiers if available."""
    if ranking_data and ranking_data.get("confirmed"):
        confirmed = [
            {"id": "", "title": f"{r['d']}, {r['s']} · need/$: {r['npd']:.1e} · desert: {r['ds']} · ${r['cost']:,} · {r['hrs']}h"}
            for r in ranking_data["confirmed"] if r.get("hrs")
        ]
        candidate = [
            {"id": "", "title": f"{r['d']}, {r['s']} · need/$: {r['npd']:.1e} · ${r['cost']:,} (investigate)"}
            for r in ranking_data.get("candidate", [])
        ]
        children = [{"title": "Confirmed Gaps (measured)", "children": confirmed}]
        if candidate:
            children.append({"title": "Candidate Gaps (investigate)", "children": candidate})
        return children
    return [{"title": "Top Ranked Districts", "children": fallback_top_ranked[:15]}]


def _build_hierarchy(nodes, rankings, ranking_data=None, districts=None, supply=None,
                     coverage_by_district=None):
    """Build reasoning-chain hierarchy structure with metrics per item."""
    if districts is None:
        districts = []
    if supply is None:
        supply = {}
    if coverage_by_district is None:
        coverage_by_district = {}

    node_map = {n["id"]: n for n in nodes}

    # Pre-compute district metrics for enrichment
    district_metrics = {}
    for d in districts:
        key = d["district_name"].lower()
        burden = compute_burden(d, "maternal_health")
        sup = supply.get(key, {"total": 0, "public": 0, "private": 0, "maternal": 0})
        cov = coverage_by_district.get(key, {}).get("maternity", {})
        district_metrics[key] = {
            "burden": round(burden, 3) if burden else None,
            "facilities": sup.get("total", 0),
            "maternal_fac": sup.get("maternal", 0),
            "gap": cov.get("gc", ""),
            "desert": cov.get("ds", 0),
        }

    # Compute aggregate metrics per capability
    cap_metrics = {}
    for cap in CAPABILITIES:
        confirmed = sum(1 for d in coverage_by_district.values()
                       if d.get(cap, {}).get("gc") == "confirmed_coverage")
        deserts = sum(1 for d in coverage_by_district.values()
                     if d.get(cap, {}).get("gc") == "no_claim_desert")
        cap_metrics[cap] = f"{confirmed} confirmed, {deserts} deserts"

    def items_for_cat(cat, level=None):
        items = []
        for n in nodes:
            if n["category"] != cat or (level is not None and n["level"] != level):
                continue
            item = {"id": n["id"], "title": n["title"]}
            # Attach metrics based on node type
            if cat == "Geography" and n["level"] == 2:
                name_part = n["title"].split(",")[0].strip().lower()
                dm = district_metrics.get(name_part)
                if dm and dm["burden"]:
                    item["metric"] = f"burden: {dm['burden']} · {dm['facilities']} fac"
                    if dm["gap"]:
                        item["metric"] += f" · {dm['gap'].replace('_', ' ')}"
            elif cat == "Verification" and n["level"] == 2:
                cap_key = n["title"].lower()
                if cap_key in cap_metrics:
                    item["metric"] = cap_metrics[cap_key]
            elif cat == "Supply" and n["level"] == 3:
                pass  # title already has counts
            elif cat == "Health Burden" and n["level"] == 1:
                intv_key = n["title"].lower().replace(" ", "_")
                ranked = rankings.get(intv_key, [])
                if ranked:
                    item["metric"] = f"top: {ranked[0][0]} ({ranked[0][1]:.3f})"
            items.append(item)
        return items

    # Intervention items with top district
    intervention_items = items_for_cat("Health Burden", 1)

    # Indicator items with aggregate stats
    indicator_items = []
    for n in nodes:
        if n["category"] == "Health Burden" and n["level"] == 3:
            item = {"id": n["id"], "title": n["title"]}
            # Compute mean across all districts
            col_name = n["id"].replace("MMD-IND-", "")
            vals = [d.get(col_name) for d in districts if d.get(col_name) is not None]
            if vals:
                avg = sum(vals) / len(vals)
                item["metric"] = f"mean: {avg:.1f}% · n={len(vals)}"
            indicator_items.append(item)

    # Capability items
    cap_items = items_for_cat("Verification", 2)

    # Claim grade items with counts
    grade_items = []
    grade_counts = {"high": 0, "medium": 0, "unverified": 0, "none": 0}
    for d_cov in coverage_by_district.values():
        mat = d_cov.get("maternity", {})
        grade_counts["high"] += mat.get("h", 0)
        grade_counts["medium"] += mat.get("m", 0)
        grade_counts["unverified"] += mat.get("u", 0)
    for n in nodes:
        if n["category"] == "Verification" and n["level"] == 3:
            item = {"id": n["id"], "title": n["title"]}
            grade_key = n["id"].replace("MMD-GRD-", "")
            if grade_key in grade_counts:
                item["metric"] = f"{grade_counts[grade_key]} facilities (maternity)"
            grade_items.append(item)

    # Gap classification items with counts
    gap_items = []
    gap_counts = {"confirmed_coverage": 0, "unverified_claims": 0, "no_claim_desert": 0}
    for d_cov in coverage_by_district.values():
        gc = d_cov.get("maternity", {}).get("gc", "")
        if gc in gap_counts:
            gap_counts[gc] += 1
    for n in nodes:
        if n["category"] == "Analysis" and n["level"] == 3:
            item = {"id": n["id"], "title": n["title"]}
            gap_key = n["id"].replace("MMD-GAP-", "")
            if gap_key in gap_counts:
                item["metric"] = f"{gap_counts[gap_key]} districts (maternity)"
            gap_items.append(item)

    # Cost model items (already have values in title)
    cost_items = items_for_cat("Cost Model", 3)

    # Reachability
    stg_item = {"id": "MMD-STG-patna", "title": "Patna (Staging City)",
                "metric": f"hub for {len(districts)} districts"}

    # Data provenance with row counts
    source_metrics = {
        "nfhs5": f"{len(districts)} districts",
        "facilities": f"{sum(s.get('total', 0) for s in supply.values())} facility clusters",
        "india_post": "165K PIN codes",
        "facility_text": "9,964 text records",
    }
    source_items = []
    for n in nodes:
        if n["category"] == "Data Provenance" and n["level"] == 4:
            item = {"id": n["id"], "title": n["title"]}
            src_key = n["id"].replace("MMD-SRC-", "")
            if src_key in source_metrics:
                item["metric"] = source_metrics[src_key]
            source_items.append(item)

    # Workflow items with descriptions
    wf_items = items_for_cat("User Workflow")

    top_ranked = []
    for intv, ranked in rankings.items():
        for name, *_ in ranked[:5]:
            key = name.lower()
            nid = f"MMD-DST-{next((i for i, n in enumerate(nodes) if n['id'].startswith('MMD-DST-') and key in n['title'].lower()), 0):03d}"
            matching = [n for n in nodes if n["id"] == nid]
            if matching:
                top_ranked.append({"id": nid, "title": matching[0]["title"]})

    hierarchy = [
        {"title": "1. Burden Assessment", "color": "#f85149", "children": [
            {"title": "Interventions", "children": intervention_items},
            {"title": "NFHS-5 Indicators", "children": indicator_items},
        ]},
        {"title": "2. Supply Verification", "color": "#d29922", "children": [
            {"title": "Capabilities", "children": cap_items},
            {"title": "Claim Grades", "children": grade_items},
        ]},
        {"title": "3. Coverage Gap Analysis", "color": "#e3b341", "children": [
            {"title": "Gap Classifications", "children": gap_items},
        ]},
        {"title": "4. Reachability", "color": "#58a6ff", "children": [
            {"title": "Staging City", "children": [stg_item]},
        ]},
        {"title": "5. Cost Model", "color": "#bc8cff", "children": [
            {"title": "Assumptions", "children": cost_items},
        ]},
        {"title": "6. Impact Ranking (Maternal · Patna)", "color": "#58a6ff", "children":
            _ranking_hierarchy_children(ranking_data, top_ranked)
        },
        {"title": "7. Planner Workflow", "color": "#a371f7", "children": [
            {"title": "Decision Tools", "children": wf_items},
        ]},
        {"title": "8. Data Provenance", "color": "#8b949e", "children": [
            {"title": "Sources", "children": source_items},
        ]},
    ]
    return hierarchy


def _html_template(data_block):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Medical Mission Copilot — Knowledge Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
:root {{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--bg4:#30363d;--border:#30363d;--text:#e6edf3;--text2:#8b949e;--text3:#c9d1d9;--blue:#58a6ff;--green:#2ea043;--orange:#d29922;--red:#f85149;--purple:#bc8cff;--lavender:#a371f7;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:14px;overflow:hidden;height:100vh;}}
.navbar{{position:fixed;top:0;left:0;right:0;height:48px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;align-items:center;padding:0 16px;z-index:1000;}}
.navbar .logo{{font-weight:700;font-size:15px;color:var(--blue);margin-right:20px;}}
.navbar .tabs{{display:flex;gap:4px;}}
.navbar .tabs button{{background:none;border:none;color:var(--text2);padding:8px 14px;cursor:pointer;border-radius:6px;font-size:12px;font-weight:500;}}
.navbar .tabs button.active{{background:var(--bg3);color:var(--text);}}
.navbar .tabs button:hover{{color:var(--text);}}
.navbar .info{{margin-left:auto;font-size:10px;color:var(--text2);}}
.main{{position:fixed;top:48px;left:0;right:0;bottom:0;}}
.tab-content{{display:none;width:100%;height:100%;position:relative;}}
.tab-content.active{{display:block;}}
.map-controls{{position:absolute;top:12px;right:12px;z-index:10;display:flex;gap:6px;flex-wrap:wrap;}}
.map-controls select,.map-controls button{{background:var(--bg2);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:5px;font-size:11px;cursor:pointer;}}
.map-controls button:hover{{background:var(--bg3);}}
.map-state{{fill:var(--bg3);stroke:var(--border);stroke-width:0.5;transition:fill 0.2s,opacity 0.3s;}}
.map-outline{{fill:none;stroke:var(--text2);stroke-width:1.8;pointer-events:none;}}
.map-state:hover{{fill:var(--bg4);}}
.map-state.dimmed{{opacity:0.25;}}
.map-state.highlighted{{fill:var(--bg4);stroke:var(--blue);stroke-width:1.2;opacity:1;}}
.map-marker{{cursor:pointer;transition:r 0.15s;}}
.map-marker:hover{{stroke:#fff;stroke-width:1.5;}}
.map-marker.dimmed{{opacity:0.15;}}
#mapTooltip{{position:absolute;display:none;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 14px;font-size:11px;pointer-events:none;z-index:20;max-width:240px;box-shadow:0 4px 12px rgba(0,0,0,0.5);}}
#mapTooltip b{{color:var(--blue);}}
.detail-panel{{position:absolute;top:0;right:0;width:320px;height:100%;background:var(--bg2);border-left:1px solid var(--border);padding:16px;overflow-y:auto;transform:translateX(100%);transition:transform 0.25s ease;z-index:15;}}
.detail-panel.open{{transform:translateX(0);}}
.detail-panel .dp-close{{position:absolute;top:10px;right:12px;background:none;border:none;color:var(--text2);font-size:16px;cursor:pointer;}}
.detail-panel .dp-close:hover{{color:var(--text);}}
.detail-panel h2{{font-size:15px;margin-bottom:4px;color:var(--text);}}
.detail-panel .dp-state{{font-size:11px;color:var(--blue);margin-bottom:12px;}}
.detail-panel .dp-section{{margin-bottom:14px;}}
.detail-panel .dp-section h4{{font-size:10px;text-transform:uppercase;color:var(--text2);margin-bottom:6px;letter-spacing:0.5px;}}
.detail-panel .dp-row{{display:flex;justify-content:space-between;padding:4px 0;font-size:11px;border-bottom:1px solid var(--border);}}
.detail-panel .dp-row .dp-label{{color:var(--text3);}}
.detail-panel .dp-row .dp-val{{color:var(--text);font-weight:600;}}
.detail-panel .dp-badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:9px;font-weight:600;margin-top:4px;}}
.detail-panel .dp-links{{margin-top:10px;}}
.detail-panel .dp-links a{{display:block;padding:3px 0;font-size:11px;color:var(--blue);cursor:pointer;text-decoration:none;}}
.detail-panel .dp-links a:hover{{text-decoration:underline;}}
.map-legend{{position:absolute;top:56px;left:12px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 14px;font-size:10px;z-index:10;}}
.map-legend .ml-title{{font-weight:600;margin-bottom:6px;color:var(--text3);}}
.map-legend .ml-row{{display:flex;align-items:center;gap:6px;margin-bottom:3px;color:var(--text2);cursor:pointer;padding:2px 4px;border-radius:4px;transition:background 0.15s;}}
.map-legend .ml-row:hover{{background:var(--bg3);color:var(--text);}}
.map-legend .ml-row.active-filter{{background:var(--bg4);color:var(--text);}}
.map-legend .ml-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0;}}
.hierarchy{{padding:20px;width:100%;height:100%;overflow-y:auto;}}
.hier-section{{margin-bottom:12px;}}
.hier-header{{padding:9px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;font-size:13px;}}
.hier-header:hover{{background:var(--bg3);}}
.hier-children{{padding:6px 0 6px 20px;display:none;}}
.hier-children.expanded{{display:block;}}
.hier-sub-header{{padding:5px 8px;font-size:11px;color:var(--text2);font-weight:600;margin-top:4px;}}
.hier-item{{padding:5px 8px;font-size:11px;color:var(--text3);border-left:2px solid var(--border);margin-bottom:1px;cursor:pointer;border-radius:0 4px 4px 0;}}
.hier-item:hover{{background:var(--bg3);color:var(--blue);}}
.dp-conf{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;font-weight:600;margin-right:4px;}}
.dp-conf-h{{background:rgba(46,160,67,0.15);color:var(--green);}}
.dp-conf-m{{background:rgba(210,153,34,0.15);color:var(--orange);}}
.dp-conf-u{{background:rgba(248,81,73,0.15);color:var(--red);}}
.dp-gap{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:9px;font-weight:600;}}
.dp-gap-confirmed{{background:rgba(46,160,67,0.15);color:var(--green);}}
.dp-gap-unverified{{background:rgba(210,153,34,0.15);color:var(--orange);}}
.dp-gap-desert{{background:rgba(248,81,73,0.15);color:var(--red);}}
.dp-evidence{{padding:6px 0;border-bottom:1px solid var(--border);font-size:10px;}}
.dp-evidence-text{{color:var(--text2);font-style:italic;margin-top:2px;}}
.dp-evidence a{{color:var(--blue);text-decoration:none;font-size:9px;}}
.dp-evidence a:hover{{text-decoration:underline;}}
/* Chat Assistant */
.chat-wrapper{{display:flex;height:100%;gap:0;}}
.chat-container{{display:flex;flex-direction:column;height:100%;width:60%;padding:20px;border-right:1px solid var(--border);}}
.chat-messages{{flex:1;overflow-y:auto;padding:12px 0;}}
.chat-msg{{padding:10px 14px;margin-bottom:10px;border-radius:8px;font-size:12px;line-height:1.5;max-width:85%;}}
.chat-msg.user{{background:var(--bg4);margin-left:auto;color:var(--text);}}
.chat-msg.bot{{background:var(--bg2);border:1px solid var(--border);color:var(--text3);}}
.chat-msg b,.chat-msg strong{{color:var(--text);}}
.chat-input-row{{display:flex;gap:8px;padding-top:12px;border-top:1px solid var(--border);}}
.chat-input-row input{{flex:1;background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px 14px;color:var(--text);font-size:12px;outline:none;}}
.chat-input-row input:focus{{border-color:var(--blue);}}
.chat-input-row button{{background:var(--blue);border:none;border-radius:6px;padding:10px 16px;color:#fff;font-size:12px;font-weight:600;cursor:pointer;}}
.chat-input-row button:hover{{opacity:0.85;}}
.chat-prompts{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;}}
.chat-prompts .prompt-chip{{background:var(--bg3);border:1px solid var(--border);border-radius:16px;padding:5px 12px;font-size:10px;color:var(--text2);cursor:pointer;transition:all 0.15s;}}
.chat-prompts .prompt-chip:hover{{background:var(--bg4);color:var(--text);border-color:var(--blue);}}
/* Glossary */
.glossary-container{{height:100%;overflow-y:auto;padding:20px;width:40%;}}
.glossary-search{{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:10px 14px;color:var(--text);font-size:12px;outline:none;margin-bottom:16px;}}
.glossary-search:focus{{border-color:var(--blue);}}
.glossary-tabs{{display:flex;gap:4px;margin-bottom:16px;}}
.glossary-tabs button{{background:var(--bg3);border:1px solid var(--border);border-radius:5px;padding:6px 12px;font-size:11px;color:var(--text2);cursor:pointer;}}
.glossary-tabs button.active{{background:var(--blue);border-color:var(--blue);color:#fff;}}
.glossary-card{{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:10px 14px;margin-bottom:8px;}}
.glossary-card .gc-term{{font-weight:600;font-size:12px;color:var(--text);}}
.glossary-card .gc-def{{font-size:11px;color:var(--text2);margin-top:3px;line-height:1.4;}}
.glossary-card .gc-cat{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;background:var(--bg4);color:var(--text3);margin-top:4px;}}
</style>
</head>
<body>
<div class="navbar">
  <div class="logo">Mission Copilot KG</div>
  <div class="tabs">
    <button class="active" onclick="switchTab('map',this)">Map</button>
    <button onclick="switchTab('hierarchy',this)">Hierarchy</button>
    <button onclick="switchTab('chat',this)">Ask Copilot</button>
  </div>
  <div class="info"><span id="nodeCount"></span> districts across <span id="stateCount"></span> states</div>
</div>
<div class="main">
  <div id="tab-map" class="tab-content active">
    <div class="map-controls">
      <select id="capSelect" onchange="selectCapability(this.value)">
      </select>
      <select id="stateSelect" onchange="selectState(this.value)">
        <option value="all">All India</option>
      </select>
      <select id="colorBy" onchange="recolorMarkers(this.value)">
        <option value="gap">Color: Coverage Gap</option>
        <option value="burden">Color: Burden</option>
        <option value="desert">Color: Desert Score</option>
        <option value="npd">Color: Need-per-$</option>
        <option value="top">Color: Top Ranked</option>
      </select>
      <button onclick="resetMapView()">Reset View</button>
    </div>
    <div id="mapTooltip"></div>
    <svg id="mapSvg"></svg>
    <div class="detail-panel" id="detailPanel">
      <button class="dp-close" onclick="closePanel()">&times;</button>
      <h2 id="dpName"></h2>
      <div class="dp-state" id="dpState"></div>
      <div id="dpContent"></div>
    </div>
    <div class="map-legend" id="mapLegend">
      <div class="ml-title" id="legendTitle">Coverage Gap</div>
      <div id="legendRows">
        <div class="ml-row" onclick="filterByLegend('confirmed_coverage',this)"><div class="ml-dot" style="background:var(--green)"></div>Confirmed Coverage</div>
        <div class="ml-row" onclick="filterByLegend('unverified_claims',this)"><div class="ml-dot" style="background:var(--orange)"></div>Unverified Claims</div>
        <div class="ml-row" onclick="filterByLegend('no_claim_desert',this)"><div class="ml-dot" style="background:var(--red)"></div>No-Claim Desert</div>
        <div class="ml-row" onclick="filterByLegend('no_data',this)"><div class="ml-dot" style="background:var(--text2)"></div>No Data</div>
      </div>
      <div class="ml-row" style="margin-top:6px;border-top:1px solid var(--border);padding-top:4px;cursor:default"><div class="ml-dot" style="background:none;border:2px solid var(--blue)"></div>Staging City (Patna)</div>
      <div class="ml-row" onclick="filterByLegend(null,this)" style="margin-top:4px;font-size:9px;color:var(--blue)">Show All</div>
    </div>
  </div>
  <div id="tab-hierarchy" class="tab-content hierarchy"></div>
  <div id="tab-chat" class="tab-content">
    <div class="chat-wrapper">
      <div class="chat-container">
        <div class="chat-prompts" id="chatPrompts"></div>
        <div class="chat-messages" id="chatMessages">
          <div class="chat-msg bot">Welcome! I can answer questions about this medical mission deployment system. Try a sample question above, or type your own below.</div>
        </div>
        <div class="chat-input-row">
          <input type="text" id="chatInput" placeholder="Ask about burden, coverage, costs, rankings..." onkeydown="if(event.key==='Enter')askQuestion()">
          <button onclick="askQuestion()">Ask</button>
        </div>
      </div>
      <div class="glossary-container">
        <h4 style="font-size:13px;color:var(--text);margin-bottom:12px">Glossary &amp; Vocabulary</h4>
        <input type="text" class="glossary-search" id="glossarySearch" placeholder="Search terms, acronyms, vocabulary..." oninput="filterGlossary()">
        <div class="glossary-tabs" id="glossaryTabs">
          <button class="active" onclick="switchGlossaryTab('all',this)">All</button>
          <button onclick="switchGlossaryTab('glossary',this)">Definitions</button>
          <button onclick="switchGlossaryTab('acronyms',this)">Acronyms</button>
          <button onclick="switchGlossaryTab('vocab',this)">Vocabulary</button>
        </div>
        <div id="glossaryContent"></div>
      </div>
    </div>
  </div>
</div>
<script>
{data_block}
var projection,mapG,mapZoomBehavior,markers,currentState='all',currentCap='maternity';
var FILL_COLORS={{'strong':'#2ea043','moderate':'#3fb950','weaker':'#56d364','claim_only':'#d29922','no_claim_desert':'#f85149','no_data':'#484f58'}};
var GAP_COLORS={{'confirmed_coverage':'#2ea043','unverified_claims':'#d29922','no_claim_desert':'#f85149'}};

function switchTab(tab,btn){{
  document.querySelectorAll('.tab-content').forEach(function(el){{el.classList.remove('active');}});
  document.getElementById('tab-'+tab).classList.add('active');
  document.querySelectorAll('.navbar .tabs button').forEach(function(b){{b.classList.remove('active');}});
  if(btn)btn.classList.add('active');
}}

function initMap(){{
  var w=window.innerWidth,h=window.innerHeight-48;
  var svg=d3.select('#mapSvg').attr('width',w).attr('height',h);
  mapG=svg.append('g');
  mapZoomBehavior=d3.zoom().scaleExtent([1,15]).on('zoom',function(ev){{mapG.attr('transform',ev.transform);}});
  svg.call(mapZoomBehavior);
  projection=d3.geoMercator().center([82,23]).scale(w*0.96).translate([w/2,h/2]);
  var path=d3.geoPath().projection(projection);

  d3.json('https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson').then(function(india){{
    mapG.append('g').attr('id','stateLayer').selectAll('path').data(india.features).join('path')
      .attr('d',path).attr('class','map-state');
    mapG.append('path').datum({{type:'FeatureCollection',features:india.features}}).attr('d',path).attr('class','map-outline');

    markers=mapG.append('g').attr('id','markerLayer').selectAll('circle').data(mapPoints).join('circle')
      .attr('class','map-marker')
      .attr('cx',function(p){{return projection([p.lon,p.lat])[0];}})
      .attr('cy',function(p){{return projection([p.lon,p.lat])[1];}})
      .attr('r',function(p){{return p.isTop?5:3;}})
      .attr('opacity',0.85)
      .on('mouseover',function(ev,p){{
        d3.select(this).attr('r',8);
        var tip=document.getElementById('mapTooltip');
        tip.style.display='block';
        tip.style.left=(ev.offsetX+15)+'px';tip.style.top=(ev.offsetY-10)+'px';
        var cov=p.coverage&&p.coverage[currentCap];
        var gcLabel=cov?cov.gc.replace(/_/g,' '):'no data';
        tip.innerHTML='<b>'+p.name+'</b><br>'+p.state+'<br>Gap: '+gcLabel+(cov?'<br>Desert: '+cov.ds+' · Verified: '+cov.v:'')+(p.isTop?'<br><span style="color:#2ea043">Top Ranked</span>':'');
      }})
      .on('mouseout',function(ev,p){{
        d3.select(this).attr('r',p.isTop?5:3);
        document.getElementById('mapTooltip').style.display='none';
      }})
      .on('click',function(ev,p){{ev.stopPropagation();openPanel(p);}});

    var stg=projection([85.1376,25.5941]);
    mapG.append('circle').attr('cx',stg[0]).attr('cy',stg[1]).attr('r',7).attr('fill','none').attr('stroke','var(--blue)').attr('stroke-width',2.5);
    mapG.append('text').attr('x',stg[0]+10).attr('y',stg[1]+4).text('Patna').attr('font-size','9px').attr('fill','var(--blue)');

    recolorMarkers('gap');
    recolorStates();
  }}).catch(function(){{
    mapG.append('text').attr('x',w/2).attr('y',h/2).attr('text-anchor','middle').attr('fill','var(--text2)').text('Map requires internet connection.');
  }});

  // Populate dropdowns
  var capSel=document.getElementById('capSelect');
  capabilities.forEach(function(c){{var o=document.createElement('option');o.value=c;o.textContent=capLabels[c]||c;capSel.appendChild(o);}});
  var sel=document.getElementById('stateSelect');
  allStates.forEach(function(s){{var o=document.createElement('option');o.value=s;o.textContent=s;sel.appendChild(o);}});
  document.getElementById('nodeCount').textContent=mapPoints.length;
  document.getElementById('stateCount').textContent=allStates.length;
  svg.on('click',function(){{closePanel();}});
}}

function selectCapability(cap){{
  currentCap=cap;
  recolorMarkers(document.getElementById('colorBy').value);
  recolorStates();
}}

function recolorStates(){{
  var roll=stateRollup[currentCap]||[];
  var byName={{}};roll.forEach(function(r){{byName[r.st.toLowerCase()]=r.fc;}});
  d3.selectAll('.map-state').each(function(feat){{
    var n=(feat.properties.NAME_1||feat.properties.name||'').toLowerCase();
    var fc=null;
    Object.keys(byName).forEach(function(k){{if(n.includes(k)||k.includes(n))fc=byName[k];}});
    d3.select(this).attr('fill',fc?FILL_COLORS[fc]||'var(--bg3)':'var(--bg3)').attr('fill-opacity',fc&&fc!=='no_data'?0.35:0.12);
  }});
}}

function burdenColor(v,max){{if(!v)return'var(--text2)';var t=v/max;if(t>0.7)return'var(--red)';if(t>0.4)return'var(--orange)';return'var(--green)';}}

function selectState(state){{
  currentState=state;
  closePanel();
  if(state==='all'){{
    markers.classed('dimmed',false).style('pointer-events','all');
    d3.selectAll('.map-state').classed('dimmed',false).classed('highlighted',false);
    recolorStates();
    return;
  }}
  var stateLC=state.toLowerCase();
  markers.classed('dimmed',function(p){{return p.state.toLowerCase()!==stateLC;}})
    .style('pointer-events',function(p){{return p.state.toLowerCase()===stateLC?'all':'none';}});
  d3.selectAll('.map-state').classed('dimmed',true).classed('highlighted',false);
  d3.selectAll('.map-state').each(function(feat){{
    var n=(feat.properties.NAME_1||feat.properties.name||'').toLowerCase();
    if(n.includes(stateLC)||stateLC.includes(n))d3.select(this).classed('dimmed',false).classed('highlighted',true);
  }});
}}

function resetMapView(){{
  markers.classed('dimmed',false).style('pointer-events','all');
  d3.selectAll('.map-state').classed('dimmed',false).classed('highlighted',false);
  document.getElementById('stateSelect').value='all';currentState='all';
  recolorStates();
}}

function recolorMarkers(mode){{
  if(!markers)return;
  var bMax=d3.max(mapPoints,function(p){{return p.burden;}})||1;
  var dsMax=d3.max(mapPoints,function(p){{var c=p.coverage&&p.coverage[currentCap];return c?c.ds:0;}})||1;
  markers.attr('fill',function(p){{
    if(mode==='gap'){{
      var c=p.coverage&&p.coverage[currentCap];
      return c?GAP_COLORS[c.gc]||'var(--text2)':'var(--text2)';
    }}
    if(mode==='burden')return burdenColor(p.burden,bMax);
    if(mode==='desert'){{
      var c=p.coverage&&p.coverage[currentCap];
      if(!c)return'var(--text2)';
      var t=c.ds/dsMax;
      return t>0.7?'var(--red)':t>0.4?'var(--orange)':'var(--green)';
    }}
    if(mode==='npd'){{
      if(!rankingData||!rankingData.confirmed)return'var(--text2)';
      var nm=p.name.toLowerCase();
      var found=rankingData.confirmed.find(function(r){{return r.d.toLowerCase()===nm;}});
      if(found)return'var(--purple)';
      var cand=rankingData.candidate&&rankingData.candidate.find(function(r){{return r.d.toLowerCase()===nm;}});
      if(cand)return'var(--lavender)';
      return'var(--text2)';
    }}
    return p.isTop?'var(--green)':'var(--text2)';
  }});
  updateLegend(mode);
}}

var activeLegendFilter=null;

function updateLegend(mode){{
  activeLegendFilter=null;
  var title=document.getElementById('legendTitle');
  var rows=document.getElementById('legendRows');
  if(mode==='gap'){{
    title.textContent='Coverage Gap ('+( capLabels[currentCap]||currentCap)+')';
    rows.innerHTML='<div class="ml-row" onclick="filterByLegend(\\'confirmed_coverage\\',this)"><div class="ml-dot" style="background:#2ea043"></div>Confirmed Coverage</div><div class="ml-row" onclick="filterByLegend(\\'unverified_claims\\',this)"><div class="ml-dot" style="background:#d29922"></div>Unverified Claims</div><div class="ml-row" onclick="filterByLegend(\\'no_claim_desert\\',this)"><div class="ml-dot" style="background:#f85149"></div>No-Claim Desert</div><div class="ml-row" onclick="filterByLegend(\\'no_data\\',this)"><div class="ml-dot" style="background:var(--text2)"></div>No Data</div><div class="ml-row" onclick="filterByLegend(null,this)" style="margin-top:4px;font-size:9px;color:var(--blue)">Show All</div>';
  }} else if(mode==='burden'){{
    title.textContent='Maternal Burden';
    rows.innerHTML='<div class="ml-row" onclick="filterByLegend(\\'high\\',this)"><div class="ml-dot" style="background:var(--red)"></div>High (&gt;0.7)</div><div class="ml-row" onclick="filterByLegend(\\'medium\\',this)"><div class="ml-dot" style="background:var(--orange)"></div>Medium (0.4-0.7)</div><div class="ml-row" onclick="filterByLegend(\\'low\\',this)"><div class="ml-dot" style="background:var(--green)"></div>Low (&lt;0.4)</div><div class="ml-row" onclick="filterByLegend(null,this)" style="margin-top:4px;font-size:9px;color:var(--blue)">Show All</div>';
  }} else if(mode==='desert'){{
    title.textContent='Desert Score ('+( capLabels[currentCap]||currentCap)+')';
    rows.innerHTML='<div class="ml-row" onclick="filterByLegend(\\'high\\',this)"><div class="ml-dot" style="background:var(--red)"></div>High desert (&gt;0.7)</div><div class="ml-row" onclick="filterByLegend(\\'medium\\',this)"><div class="ml-dot" style="background:var(--orange)"></div>Medium (0.4-0.7)</div><div class="ml-row" onclick="filterByLegend(\\'low\\',this)"><div class="ml-dot" style="background:var(--green)"></div>Low (&lt;0.4)</div><div class="ml-row" onclick="filterByLegend(null,this)" style="margin-top:4px;font-size:9px;color:var(--blue)">Show All</div>';
  }} else if(mode==='npd'){{
    title.textContent='Need-per-$ (Maternal · Patna)';
    rows.innerHTML='<div class="ml-row" onclick="filterByLegend(\\'confirmed\\',this)"><div class="ml-dot" style="background:var(--purple)"></div>Confirmed Gap (ranked)</div><div class="ml-row" onclick="filterByLegend(\\'candidate\\',this)"><div class="ml-dot" style="background:var(--lavender)"></div>Candidate (investigate)</div><div class="ml-row" onclick="filterByLegend(\\'none\\',this)"><div class="ml-dot" style="background:var(--text2)"></div>Not ranked</div><div class="ml-row" onclick="filterByLegend(null,this)" style="margin-top:4px;font-size:9px;color:var(--blue)">Show All</div>';
  }} else {{
    title.textContent='Ranking';
    rows.innerHTML='<div class="ml-row" onclick="filterByLegend(\\'top\\',this)"><div class="ml-dot" style="background:var(--green)"></div>Top Ranked</div><div class="ml-row" onclick="filterByLegend(\\'other\\',this)"><div class="ml-dot" style="background:var(--text2)"></div>Other</div><div class="ml-row" onclick="filterByLegend(null,this)" style="margin-top:4px;font-size:9px;color:var(--blue)">Show All</div>';
  }}
}}

function filterByLegend(cat,el){{
  document.querySelectorAll('#legendRows .ml-row').forEach(function(r){{r.classList.remove('active-filter');}});
  if(cat===null||activeLegendFilter===cat){{
    activeLegendFilter=null;
    markers.classed('dimmed',false).style('pointer-events','all');
    if(currentState!=='all')selectState(currentState);
    return;
  }}
  activeLegendFilter=cat;
  if(el)el.classList.add('active-filter');
  var mode=document.getElementById('colorBy').value;
  markers.classed('dimmed',function(p){{return !matchesLegendFilter(p,mode,cat);}})
    .style('pointer-events',function(p){{return matchesLegendFilter(p,mode,cat)?'all':'none';}});
}}

function matchesLegendFilter(p,mode,cat){{
  if(mode==='gap'){{
    var c=p.coverage&&p.coverage[currentCap];
    var gc=c?c.gc:'no_data';
    if(cat==='no_data')return !c;
    return gc===cat;
  }}
  if(mode==='burden'){{
    if(cat==='high')return p.burden>0.7;
    if(cat==='medium')return p.burden>0.4&&p.burden<=0.7;
    return p.burden<=0.4;
  }}
  if(mode==='desert'){{
    var c=p.coverage&&p.coverage[currentCap];
    var ds=c?c.ds:0;var dsMax=d3.max(mapPoints,function(pp){{var cc=pp.coverage&&pp.coverage[currentCap];return cc?cc.ds:0;}})||1;
    var t=ds/dsMax;
    if(cat==='high')return t>0.7;
    if(cat==='medium')return t>0.4&&t<=0.7;
    return t<=0.4;
  }}
  if(mode==='npd'){{
    if(!rankingData)return cat==='none';
    var nm=p.name.toLowerCase();
    var inConf=rankingData.confirmed&&rankingData.confirmed.find(function(r){{return r.d.toLowerCase()===nm;}});
    var inCand=rankingData.candidate&&rankingData.candidate.find(function(r){{return r.d.toLowerCase()===nm;}});
    if(cat==='confirmed')return !!inConf;
    if(cat==='candidate')return !!inCand;
    return !inConf&&!inCand;
  }}
  if(cat==='top')return p.isTop;
  return !p.isTop;
}}

function openPanel(p){{
  var panel=document.getElementById('detailPanel');
  document.getElementById('dpName').textContent=p.name;
  document.getElementById('dpState').textContent=p.state;
  var cap=currentCap;
  var cov=p.coverage&&p.coverage[cap];
  var html='';

  // Coverage section
  html+='<div class="dp-section"><h4>Coverage: '+(capLabels[cap]||cap)+'</h4>';
  if(cov){{
    var gcClass=cov.gc==='confirmed_coverage'?'confirmed':cov.gc==='unverified_claims'?'unverified':'desert';
    html+='<div style="margin-bottom:6px"><span class="dp-gap dp-gap-'+gcClass+'">'+cov.gc.replace(/_/g,' ')+'</span></div>';
    html+='<div class="dp-row"><span class="dp-label">Desert Score</span><span class="dp-val" style="color:'+(cov.ds>0.7?'var(--red)':cov.ds>0.4?'var(--orange)':'var(--green)')+'">'+cov.ds+'</span></div>';
    html+='<div class="dp-row"><span class="dp-label">Verified Supply</span><span class="dp-val">'+cov.v+'</span></div>';
    html+='<div class="dp-row"><span class="dp-label">High / Med / Unverified</span><span class="dp-val">'+cov.h+' / '+cov.m+' / '+cov.u+'</span></div>';
  }} else {{
    html+='<div style="font-size:10px;color:var(--text2)">No coverage data for this capability</div>';
  }}
  html+='</div>';

  // Claim evidence section
  var dkey=p.name.toLowerCase().replace(/[^a-z0-9 ]/g,'').replace(/\\s+/g,' ').trim();
  var ev=claimEvidence[dkey]&&claimEvidence[dkey][cap];
  if(ev&&ev.length){{
    html+='<div class="dp-section"><h4>Facility Evidence ('+cap+')</h4>';
    ev.forEach(function(cl){{
      var conf=cl.c==='h'?'High':cl.c==='m'?'Medium':'Unverified';
      html+='<div class="dp-evidence"><span class="dp-conf dp-conf-'+cl.c+'">'+conf+'</span><strong>'+cl.n+'</strong>';
      if(cl.e)html+='<div class="dp-evidence-text">"'+cl.e+'"</div>';
      if(cl.p)html+='<div class="dp-evidence-text" style="color:var(--text3)">Corroborated: '+cl.p+'</div>';
      if(cl.u)html+='<div><a href="'+cl.u+'" target="_blank">Source ↗</a></div>';
      html+='</div>';
    }});
    html+='</div>';
  }}

  // Health indicators
  html+='<div class="dp-section"><h4>Health Indicators (NFHS-5)</h4>';
  html+='<div class="dp-row"><span class="dp-label">Burden Score (Maternal)</span><span class="dp-val" style="color:'+(p.burden>0.7?'var(--red)':p.burden>0.4?'var(--orange)':'var(--green)')+'">'+(p.burden?p.burden.toFixed(3):'N/A')+'</span></div>';
  if(p.indicators){{
    var labels={{'institutional_birth':'Institutional Births %','anc_4plus':'4+ ANC Visits %','skilled_birth':'Skilled Birth Attendance %','anaemia':'Women Anaemic %','stunting':'Child Stunting %'}};
    Object.keys(labels).forEach(function(k){{
      var v=p.indicators[k];
      html+='<div class="dp-row"><span class="dp-label">'+labels[k]+'</span><span class="dp-val">'+(v!=null?v.toFixed(1):'—')+'</span></div>';
    }});
  }}
  if(p.isTop)html+='<div class="dp-badge" style="background:rgba(46,160,67,0.15);color:var(--green);margin-top:4px">Top Ranked District</div>';
  html+='</div>';

  // Facilities
  if(p.facilities&&p.facilities.length){{
    html+='<div class="dp-section"><h4>Facilities ('+p.facilities.length+')</h4>';
    p.facilities.forEach(function(f){{
      html+='<div style="padding:4px 0;border-bottom:1px solid var(--border);font-size:10px">';
      html+='<div style="color:var(--text);font-weight:500">'+f.name+'</div>';
      html+='<div style="color:var(--text2)">'+f.type+(f.doctors?' · '+f.doctors+' docs':'')+(f.capacity?' · cap:'+f.capacity:'')+'</div>';
      if(f.specialties)html+='<div style="color:var(--text2);font-style:italic">'+f.specialties+'</div>';
      html+='</div>';
    }});
    html+='</div>';
  }}

  // Location
  html+='<div class="dp-section"><h4>Location</h4>';
  html+='<div class="dp-row"><span class="dp-label">Coordinates</span><span class="dp-val">'+p.lat+', '+p.lon+'</span></div>';
  html+='<div class="dp-row"><span class="dp-label">Geo Source</span><span class="dp-val">'+p.geoSource+'</span></div>';
  html+='</div>';
  document.getElementById('dpContent').innerHTML=html;
  panel.classList.add('open');
}}

function closePanel(){{document.getElementById('detailPanel').classList.remove('open');}}

function initHierarchy(){{
  var c=document.getElementById('tab-hierarchy');
  var html='<div style="max-width:900px;margin:0 auto"><h3 style="margin-bottom:16px;font-size:16px">Reasoning Chain Hierarchy</h3><p style="font-size:11px;color:var(--text2);margin-bottom:20px">Deterministic chain: Burden → Supply Verification → Coverage Gap → Reachability → Cost → Ranking → Planner Workflow</p>';
  hierarchyData.forEach(function(section){{
    html+='<div class="hier-section"><div class="hier-header" onclick="this.nextElementSibling.classList.toggle(\\'expanded\\')" style="border-left:3px solid '+(section.color||'var(--border)')+'"><span>'+section.title+'</span><span style="font-size:10px;color:var(--text2)">&#9662;</span></div><div class="hier-children">';
    (section.children||[]).forEach(function(sub){{
      html+='<div class="hier-sub-header">'+sub.title+'</div>';
      (sub.children||[]).forEach(function(item){{
        html+='<div class="hier-item" onclick="focusOnNode(\\''+item.id+'\\')">';
        html+='<span>'+item.title+'</span>';
        if(item.metric)html+='<span style="float:right;font-size:9px;color:var(--text2);font-weight:400">'+item.metric+'</span>';
        html+='</div>';
      }});
    }});
    html+='</div></div>';
  }});
  html+='</div>';c.innerHTML=html;
}}

function focusOnNode(id){{
  var p=mapPoints.find(function(pt){{return pt.id===id;}});
  if(p){{
    switchTab('map',document.querySelector('.navbar .tabs button'));
    setTimeout(function(){{
      var c=projection([p.lon,p.lat]);
      var w=window.innerWidth,h=window.innerHeight-48;var scale=6;
      d3.select('#mapSvg').transition().duration(600).call(mapZoomBehavior.transform,
        d3.zoomIdentity.translate(w/2-c[0]*scale,h/2-c[1]*scale).scale(scale));
      setTimeout(function(){{openPanel(p);}},650);
    }},100);
  }}
}}

// --- Chat Assistant ---
var SAMPLE_PROMPTS = [
  "Which districts rank highest for maternal health?",
  "What is need-per-dollar?",
  "How is burden score computed?",
  "What are claim grades?",
  "What is a coverage desert?",
  "What cost assumptions are used?",
  "What is trust-weighted supply?",
  "What is the anti-hallucination architecture?",
  "How does facility text ingest work?",
  "What are the 6 verifiable capabilities?"
];

function initChat(){{
  var el=document.getElementById('chatPrompts');
  SAMPLE_PROMPTS.forEach(function(q){{
    var chip=document.createElement('span');
    chip.className='prompt-chip';
    chip.textContent=q;
    chip.onclick=function(){{document.getElementById('chatInput').value=q;askQuestion();}};
    el.appendChild(chip);
  }});
}}

function askQuestion(){{
  var input=document.getElementById('chatInput');
  var q=input.value.trim();
  if(!q)return;
  input.value='';
  var msgs=document.getElementById('chatMessages');
  msgs.innerHTML+='<div class="chat-msg user">'+escHtml(q)+'</div>';

  var answer=findAnswer(q);
  msgs.innerHTML+='<div class="chat-msg bot">'+answer+'</div>';
  msgs.scrollTop=msgs.scrollHeight;
}}

function findAnswer(q){{
  var ql=q.toLowerCase();
  var best=null,bestScore=0;
  qaData.forEach(function(qa){{
    var score=fuzzyMatch(ql,qa.question.toLowerCase());
    if(score>bestScore){{bestScore=score;best=qa;}}
  }});
  if(best&&bestScore>0.3)return best.answer+'<div style="margin-top:6px;font-size:9px;color:var(--text2)">Source: '+best.source+' · Match: '+(bestScore*100).toFixed(0)+'%</div>';

  // Search glossary/acronyms
  var found=null;
  glossaryData.forEach(function(g){{if(ql.includes(g.term.toLowerCase()))found=g;}});
  if(found)return '<p><b>'+escHtml(found.term)+':</b> '+escHtml(found.definition)+'</p>';
  acronymData.forEach(function(a){{if(ql.includes(a.acronym.toLowerCase()))found=a;}});
  if(found)return '<p><b>'+escHtml(found.acronym)+':</b> '+escHtml(found.definition)+'</p>';
  vocabData.forEach(function(v){{if(ql.includes(v.term.toLowerCase()))found=v;}});
  if(found)return '<p><b>'+escHtml(found.term)+':</b> '+escHtml(found.definition)+'</p>';

  return '<p>I don\\'t have a specific answer for that. Try asking about: burden scores, coverage gaps, claim grades, cost assumptions, or district rankings.</p>';
}}

function fuzzyMatch(a,b){{
  var words=a.split(/\\s+/);
  var hits=0;
  words.forEach(function(w){{if(w.length>2&&b.includes(w))hits++;}});
  return words.length?hits/words.length:0;
}}

function escHtml(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}

// --- Glossary ---
var glossaryTab='all';

function initGlossary(){{
  filterGlossary();
}}

function switchGlossaryTab(tab,btn){{
  glossaryTab=tab;
  document.querySelectorAll('.glossary-tabs button').forEach(function(b){{b.classList.remove('active');}});
  if(btn)btn.classList.add('active');
  filterGlossary();
}}

function filterGlossary(){{
  var search=(document.getElementById('glossarySearch').value||'').toLowerCase();
  var items=[];
  if(glossaryTab==='all'||glossaryTab==='glossary'){{
    glossaryData.forEach(function(g){{
      if(!search||g.term.toLowerCase().includes(search)||g.definition.toLowerCase().includes(search))
        items.push({{type:'definition',term:g.term,def:g.definition}});
    }});
  }}
  if(glossaryTab==='all'||glossaryTab==='acronyms'){{
    acronymData.forEach(function(a){{
      if(!search||a.acronym.toLowerCase().includes(search)||a.definition.toLowerCase().includes(search))
        items.push({{type:'acronym',term:a.acronym,def:a.definition}});
    }});
  }}
  if(glossaryTab==='all'||glossaryTab==='vocab'){{
    vocabData.forEach(function(v){{
      if(!search||v.term.toLowerCase().includes(search)||v.definition.toLowerCase().includes(search)||( v.category||'').toLowerCase().includes(search))
        items.push({{type:'vocabulary',term:v.term,def:v.definition,cat:v.category}});
    }});
  }}
  var html='';
  items.forEach(function(it){{
    html+='<div class="glossary-card"><div class="gc-term">'+escHtml(it.term)+'</div><div class="gc-def">'+escHtml(it.def)+'</div>';
    if(it.cat)html+='<div class="gc-cat">'+escHtml(it.cat)+'</div>';
    else html+='<div class="gc-cat">'+it.type+'</div>';
    html+='</div>';
  }});
  if(!items.length)html='<div style="color:var(--text2);padding:20px;text-align:center">No matching terms found.</div>';
  document.getElementById('glossaryContent').innerHTML=html;
}}

document.addEventListener('DOMContentLoaded',function(){{initMap();initHierarchy();initChat();initGlossary();}});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print("Querying Databricks...")
    districts, supply, geo_coords, facilities_by_district = query_databricks()
    print(f"  {len(districts)} districts, {len(supply)} facility clusters, {len(geo_coords)} geocoded, {len(facilities_by_district)} districts with facility details")

    print("Building coverage data (mission_core)...")
    coverage_by_district, state_rollup_data = build_coverage_data()
    print(f"  {len(coverage_by_district)} districts with coverage, {len(state_rollup_data)} capabilities with state rollup")

    print("Building claim evidence...")
    claim_evidence = build_claim_evidence()
    print(f"  {len(claim_evidence)} districts with claim evidence")

    print("Building ranking data...")
    ranking_data = build_ranking_data()
    print(f"  ranking: {'available' if ranking_data else 'unavailable'}")

    print("Building knowledge graph...")
    nodes, edges, rankings = build_graph(districts, supply)
    print(f"  {len(nodes)} nodes, {len(edges)} edges")

    print("Generating Q&A...")
    qa_data = build_qa(districts, rankings)
    print(f"  {len(qa_data)} Q&A pairs")

    print("Building map data...")
    map_points = build_map_data(districts, geo_coords, rankings, facilities_by_district,
                                coverage_by_district)
    print(f"  {len(map_points)} map points")

    print("Writing HTML...")
    html = generate_html(nodes, edges, qa_data, rankings, map_points,
                         state_rollup_data, claim_evidence, ranking_data,
                         districts, supply, coverage_by_district)
    output_path = Path(__file__).parent / "output" / "knowledge_graph.html"
    output_path.parent.mkdir(exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"  {output_path} ({len(html):,} bytes)")
    print("Done!")


if __name__ == "__main__":
    main()
