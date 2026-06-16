"""
app_kg.py — Knowledge Graph Visualization (Streamlit version for Databricks deployment).

Mirrors the self-contained knowledge_graph.html but uses Streamlit + Plotly for interactive
maps, plus native Streamlit widgets for chat, glossary, and hierarchy.

Usage:
    streamlit run app_kg.py
    # or on Databricks: configured as the app entrypoint
"""

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mission_core import data_access as da
from mission_core.claims import CAPABILITIES, CAPABILITY_LABELS
from mission_core.coverage_view import coverage_by_geography, state_rollup

try:
    from agent.tools import rank_districts_tool
    RANKING_AVAILABLE = True
except ImportError:
    RANKING_AVAILABLE = False

# --------------------------------------------------------------------------- config
_GEOJSON = json.loads((Path(__file__).resolve().parent / "assets" / "india_states.geojson").read_text())

GAP_COLORS = {
    "confirmed_coverage": "#2ea043",
    "unverified_claims": "#d29922",
    "no_claim_desert": "#f85149",
}
GAP_LABELS = {
    "confirmed_coverage": "Confirmed Coverage",
    "unverified_claims": "Unverified Claims",
    "no_claim_desert": "No-Claim Desert",
}
FILL_COLORS = {
    "strong": "#0F6E56", "moderate": "#1D9E75", "weaker": "#5DCAA5",
    "claim_only": "#EF9F27", "no_claim_desert": "#E24B4A", "no_data": "#D3D1C7",
}
FILL_LABELS = {
    "strong": "Strong coverage", "moderate": "Moderate", "weaker": "Weaker",
    "claim_only": "Claims only", "no_claim_desert": "No-claim desert", "no_data": "No data",
}

# --------------------------------------------------------------------------- glossary data
ACRONYMS = [
    {"acronym": "NFHS", "definition": "National Family Health Survey — India's largest household health survey (NFHS-5: 2019-21)"},
    {"acronym": "ANC", "definition": "Antenatal Care — medical care during pregnancy (4+ visits is WHO standard)"},
    {"acronym": "VF", "definition": "Virtue Foundation — partner NGO deploying medical missions"},
    {"acronym": "ICU", "definition": "Intensive Care Unit — critical care facility for severe illness/injury"},
    {"acronym": "NICU", "definition": "Neonatal Intensive Care Unit — specialized care for critically ill newborns"},
    {"acronym": "PIN", "definition": "Postal Index Number — India Post 6-digit code used for geographic resolution"},
    {"acronym": "EmOC", "definition": "Emergency Obstetric Care — life-saving interventions during childbirth complications"},
    {"acronym": "PHC", "definition": "Primary Health Centre — first-contact public health facility in rural India"},
    {"acronym": "CHC", "definition": "Community Health Centre — 30-bed referral hospital at block level"},
    {"acronym": "DH", "definition": "District Hospital — highest public facility at district level"},
    {"acronym": "SBA", "definition": "Skilled Birth Attendant — trained health professional attending delivery"},
    {"acronym": "NHM", "definition": "National Health Mission — India's flagship public health program"},
    {"acronym": "WHO", "definition": "World Health Organization — UN agency for international public health"},
    {"acronym": "SDG", "definition": "Sustainable Development Goals — UN 2030 targets including SDG 3 (health)"},
    {"acronym": "MMR", "definition": "Maternal Mortality Ratio — deaths per 100,000 live births"},
]

GLOSSARY = [
    {"term": "Burden Score", "definition": "Normalized 0-1 composite of NFHS-5 indicators. Higher = greater need."},
    {"term": "Coverage Gap", "definition": "burden × (1 - supply_adequacy). Quantifies unmet healthcare need."},
    {"term": "Supply Adequacy", "definition": "Saturating curve: facilities / (facilities + 3.0). Diminishing returns."},
    {"term": "Need-Per-Dollar", "definition": "Primary ranking metric: coverage_gap / mission_cost. Higher = more impact."},
    {"term": "Claim Grade", "definition": "Confidence: high (text + procedural), medium (text only), unverified (flag only)."},
    {"term": "Trust-Weighted Supply", "definition": "Facilities weighted by grade (high=1.0, medium=0.6, unverified=0.3)."},
    {"term": "Desert Score", "definition": "burden × (1 - supply_adequacy). High need + low supply."},
    {"term": "Confirmed Gap", "definition": "District with facility data — gap is measured. Tier 1 in ranking."},
    {"term": "Candidate Gap", "definition": "District with no facility data — possible desert or data gap. Tier 2."},
    {"term": "Deterministic Chain", "definition": "burden → supply verification → gap → reachability → cost → rank."},
    {"term": "Anti-Hallucination", "definition": "Agent reasons but never computes. All numbers from deterministic functions."},
    {"term": "Staging City", "definition": "Patna — deployment hub. All reachability measured from here."},
    {"term": "Corroboration", "definition": "Facility text searched for procedural terminology. Elevates grade to high."},
]

QA_PAIRS = [
    ("What is need-per-dollar?", "Ranking metric: coverage_gap / mission_cost. Higher = more impact per dollar."),
    ("How is burden score computed?", "Mean of normalized NFHS-5 indicators. Direction-aware: high_is_worse=value/100, low_is_worse=1-value/100."),
    ("What are claim grades?", "High: text + procedural corroboration. Medium: text claim only. Unverified: flag only. None: no claim."),
    ("What is a coverage desert?", "A district where no facility claims a given capability. Desert score = burden × (1 − supply_adequacy)."),
    ("What cost assumptions are used?", "Transport: $0.35/km, Per diem: $60/person/day, Team: 6, Days: 7, Surgeon day value: $800."),
    ("What is trust-weighted supply?", "Facilities weighted by claim evidence grade: high=1.0, medium=0.6, unverified=0.3."),
    ("What is the anti-hallucination architecture?", "The agent reasons but never computes. All numeric results come from deterministic Python functions."),
    ("What are the 6 verifiable capabilities?", "Maternity, ICU, NICU, Emergency, Oncology, Trauma. Each facility can claim zero or more."),
    ("What is the deterministic chain?", "burden → coverage gap → cost → impact-per-dollar → rank. A forward-only pipeline."),
    ("What is a two-tier ranking?", "Confirmed gaps: facility data present, gap measured. Candidate gaps: no data — flag for investigation."),
    ("How does facility text ingest work?", "Pulls free-text descriptions from Databricks. claims.py searches for terminology matches against 6 capability dictionaries."),
    ("What is point-in-polygon resolution?", "Spatial assignment of facilities to districts using ADM2 boundary polygons. Achieves 99.98% resolution."),
    ("What data sources are used?", "NFHS-5 (2019-21) district indicators, Virtue Foundation facilities dataset (2024), India Post PIN directory, facility free-text claims."),
    ("What is supply adequacy?", "A saturating curve: facilities / (facilities + 3.0). Going from 0→1 matters more than 10→11."),
    ("What is the staging city?", "Patna — the deployment hub from which all mission teams depart."),
    ("What is corroboration?", "Facility's text contains procedural terminology matching claimed capability. Elevates from medium to high."),
]

# --------------------------------------------------------------------------- page config
st.set_page_config(page_title="Mission Copilot — Knowledge Graph", page_icon="🧠", layout="wide")
st.markdown("""<style>
:root {--bg:#0d1117;--surface:#161b22;--border:#30363d;--ink:#e6edf3;--muted:#8b949e;--blue:#58a6ff;--green:#2ea043;--orange:#d29922;--red:#f85149;--purple:#bc8cff;}
.stApp {background:var(--bg);}
h1,h2,h3 {color:var(--ink);}
.hier-card {background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 14px;margin-bottom:6px;}
.hier-card b {color:var(--ink);}
.hier-card .metric {color:var(--muted);font-size:0.8rem;}
.gap-pill {display:inline-block;padding:2px 10px;border-radius:12px;font-size:0.75rem;font-weight:600;}
.gap-confirmed {background:rgba(46,160,67,0.15);color:#2ea043;}
.gap-unverified {background:rgba(210,153,34,0.15);color:#d29922;}
.gap-desert {background:rgba(248,81,73,0.15);color:#f85149;}
.chat-q {background:#21262d;border-radius:8px;padding:8px 12px;margin:4px 0;font-size:0.85rem;color:#e6edf3;}
.chat-a {background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 12px;margin:4px 0;font-size:0.85rem;color:#c9d1d9;}
</style>""", unsafe_allow_html=True)

# --------------------------------------------------------------------------- sidebar controls
with st.sidebar:
    st.header("Controls")
    capability = st.selectbox("Capability", CAPABILITIES,
                              format_func=lambda c: CAPABILITY_LABELS.get(c, c),
                              index=0, key="cap")
    color_mode = st.selectbox("Color Mode", ["Coverage Gap", "Burden", "Desert Score"],
                              key="color_mode")
    states_available = da.list_states()
    state_filter = st.selectbox("State Filter", ["All India"] + states_available, key="state_filter")
    st.divider()
    st.caption("Mission Copilot KG — Streamlit Edition")


# --------------------------------------------------------------------------- data loading
@st.cache_data(ttl=300)
def load_coverage_data(cap):
    rows = coverage_by_geography(cap, None)
    return rows


@st.cache_data(ttl=300)
def load_state_rollup(cap):
    return state_rollup(cap, False)


@st.cache_data(ttl=300)
def load_claims(district, cap):
    return da.load_facility_claims(district, cap)


@st.cache_data(ttl=600)
def load_ranking():
    if not RANKING_AVAILABLE:
        return None
    try:
        return rank_districts_tool("maternal_health", team_size=6, days=7, top_n=15)
    except Exception:
        return None


coverage_rows = load_coverage_data(capability)
roll = load_state_rollup(capability)

# --------------------------------------------------------------------------- TABS
tab_map, tab_hier, tab_chat = st.tabs(["Map", "Hierarchy", "Ask Copilot & Glossary"])

# ============================================================================= MAP TAB
with tab_map:
    # Build the choropleth
    cat_order = ["no_data", "no_claim_desert", "claim_only", "weaker", "moderate", "strong"]
    n = len(cat_order)
    scale = []
    for i, cat in enumerate(cat_order):
        scale += [[i / n, FILL_COLORS[cat]], [(i + 1) / n, FILL_COLORS[cat]]]

    fig = go.Figure(go.Choropleth(
        geojson=_GEOJSON, featureidkey="properties.st_nm",
        locations=[r["st_nm"] for r in roll],
        z=[cat_order.index(r["fill_category"]) for r in roll],
        zmin=0, zmax=n, colorscale=scale, showscale=False,
        marker_line_color="white", marker_line_width=0.6,
        customdata=[[r["st_nm"], FILL_LABELS.get(r["fill_category"], ""),
                     r["verified_facilities"], r["n_districts"]] for r in roll],
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>"
                      "%{customdata[2]} verified · %{customdata[3]} districts<extra></extra>",
    ))

    # Add district scatter points
    filtered_rows = coverage_rows
    if state_filter != "All India":
        filtered_rows = [r for r in coverage_rows if r.get("state", "").lower() == state_filter.lower()]

    if filtered_rows:
        lats, lons, texts, colors, sizes = [], [], [], [], []
        for r in filtered_rows:
            lat = r.get("lat")
            lon = r.get("lon")
            if not lat or not lon:
                continue
            lats.append(lat)
            lons.append(lon)
            gc = r.get("gap_classification", "")
            if color_mode == "Coverage Gap":
                colors.append(GAP_COLORS.get(gc, "#8b949e"))
            elif color_mode == "Burden":
                b = r.get("burden", 0) or 0
                colors.append("#f85149" if b > 0.7 else "#d29922" if b > 0.4 else "#2ea043")
            else:
                ds = r.get("desert_score", 0) or 0
                colors.append("#f85149" if ds > 0.7 else "#d29922" if ds > 0.4 else "#2ea043")
            sizes.append(8)
            texts.append(f"{r['district']}<br>{GAP_LABELS.get(gc, gc)}<br>desert: {r.get('desert_score', 'N/A')}")

        if lats:
            fig.add_trace(go.Scattergeo(
                lat=lats, lon=lons, mode="markers",
                marker=dict(size=sizes, color=colors, opacity=0.8,
                            line=dict(width=0.5, color="white")),
                text=texts, hoverinfo="text", name="Districts",
            ))

    fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator",
                    bgcolor="rgba(0,0,0,0)")
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=620, dragmode="zoom",
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      showlegend=False)

    col_map, col_panel = st.columns([3, 1])
    with col_map:
        st.plotly_chart(fig, width="stretch", key="kg_map")

    with col_panel:
        # Legend
        st.markdown("**Legend**")
        if color_mode == "Coverage Gap":
            for gc, color in GAP_COLORS.items():
                st.markdown(f'<span style="display:inline-block;width:10px;height:10px;'
                            f'border-radius:50%;background:{color};margin-right:6px"></span>'
                            f'{GAP_LABELS[gc]}', unsafe_allow_html=True)
        elif color_mode == "Burden":
            for label, color in [("High (>0.7)", "#f85149"), ("Medium (0.4-0.7)", "#d29922"), ("Low (<0.4)", "#2ea043")]:
                st.markdown(f'<span style="display:inline-block;width:10px;height:10px;'
                            f'border-radius:50%;background:{color};margin-right:6px"></span>{label}',
                            unsafe_allow_html=True)
        else:
            for label, color in [("High desert", "#f85149"), ("Medium", "#d29922"), ("Low", "#2ea043")]:
                st.markdown(f'<span style="display:inline-block;width:10px;height:10px;'
                            f'border-radius:50%;background:{color};margin-right:6px"></span>{label}',
                            unsafe_allow_html=True)

        st.divider()
        # Stats
        n_confirmed = sum(1 for r in coverage_rows if r.get("gap_classification") == "confirmed_coverage")
        n_unverified = sum(1 for r in coverage_rows if r.get("gap_classification") == "unverified_claims")
        n_desert = sum(1 for r in coverage_rows if r.get("gap_classification") == "no_claim_desert")
        st.metric("Confirmed Coverage", n_confirmed)
        st.metric("Unverified Claims", n_unverified)
        st.metric("No-Claim Deserts", n_desert)

    # District detail on selection
    st.divider()
    st.markdown("**District Detail** — select a district below to see facility evidence")
    district_names = sorted(set(r["district"] for r in filtered_rows))
    if district_names:
        selected_district = st.selectbox("Select district", ["(none)"] + district_names, key="district_pick")
        if selected_district != "(none)":
            row = next((r for r in filtered_rows if r["district"] == selected_district), None)
            if row:
                gc = row.get("gap_classification", "")
                gc_class = "confirmed" if gc == "confirmed_coverage" else "unverified" if gc == "unverified_claims" else "desert"
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Gap Classification", gc.replace("_", " ").title())
                c2.metric("Desert Score", f"{row.get('desert_score', 0):.3f}")
                c3.metric("Verified Supply", row.get("verified_supply", 0))
                c4.metric("Total Facilities", row.get("total_facilities", 0))

                # Facility claims
                claims = load_claims(selected_district, capability)
                if claims:
                    st.markdown(f"**Facility Evidence** ({len(claims)} records)")
                    for c in claims[:8]:
                        conf = c.get("claim_confidence", "unverified")
                        pill_class = "gap-confirmed" if conf == "high" else "gap-unverified" if conf == "medium" else "gap-desert"
                        cap_ev = c.get("capability_evidence", "") or ""
                        proc_ev = c.get("procedure_evidence", "") or ""
                        src = c.get("source_url", "")
                        st.markdown(
                            f'<div class="hier-card">'
                            f'<span class="gap-pill {pill_class}">{conf}</span> &nbsp;'
                            f'<b>{c.get("name", "Unknown")}</b>'
                            f'<div class="metric">{c.get("city", "")} · {c.get("operator", "")}'
                            f'{f" · <a href={src}>source</a>" if src else ""}</div>'
                            + (f'<div class="metric"><i>"{cap_ev[:100]}"</i></div>' if cap_ev else '')
                            + (f'<div class="metric">Corroborated: "{proc_ev[:100]}"</div>' if proc_ev else '')
                            + '</div>', unsafe_allow_html=True)
                else:
                    st.info("No facility claims this capability — candidate desert.")

                # Related districts (same state, same gap)
                related = [r for r in filtered_rows
                           if r["district"] != selected_district
                           and r.get("gap_classification") == gc
                           and r.get("state", "").lower() == row.get("state", "").lower()]
                if related:
                    st.markdown(f"**Related districts** — {len(related)} with same classification "
                                f"({gc.replace('_', ' ')}) in {row.get('state', 'same state')}")
                    rel_names = [r["district"] for r in related[:15]]
                    st.write(", ".join(rel_names))


# ============================================================================= HIERARCHY TAB
with tab_hier:
    st.markdown("### Reasoning Chain Hierarchy")
    st.caption("Deterministic chain: Burden → Supply Verification → Coverage Gap → Reachability → Cost → Ranking")

    with st.expander("1. Burden Assessment", expanded=False):
        st.markdown("**Interventions**: Maternal Health, Anaemia, Child Nutrition")
        st.markdown("**NFHS-5 Indicators**:")
        indicators = {
            "Institutional Births %": "institutional_birth_5y_pct",
            "4+ ANC Visits %": "mothers_who_had_at_least_4_anc_visits_lb5y_pct",
            "Skilled Birth Attendance %": "births_attended_by_skilled_hp_5y_10_pct",
            "Women Anaemic %": "all_w15_49_who_are_anaemic_pct",
            "Child Stunting %": "child_u5_who_are_stunted_height_for_age_18_pct",
        }
        for label, key in indicators.items():
            st.markdown(f"- {label}")

    with st.expander("2. Supply Verification", expanded=False):
        st.markdown("**6 Capabilities**: " + ", ".join(CAPABILITY_LABELS.get(c, c) for c in CAPABILITIES))
        st.markdown("**Claim Grades**: High (text + procedural) · Medium (text only) · Unverified (flag only)")
        # Metrics per capability
        for cap in CAPABILITIES:
            cap_rows = coverage_by_geography(cap, None)
            n_conf = sum(1 for r in cap_rows if r.get("gap_classification") == "confirmed_coverage")
            n_des = sum(1 for r in cap_rows if r.get("gap_classification") == "no_claim_desert")
            st.markdown(f'<div class="hier-card"><b>{CAPABILITY_LABELS.get(cap, cap)}</b>'
                        f'<span class="metric"> — {n_conf} confirmed, {n_des} deserts</span></div>',
                        unsafe_allow_html=True)

    with st.expander("3. Coverage Gap Analysis", expanded=False):
        for gc, label in GAP_LABELS.items():
            count = sum(1 for r in coverage_rows if r.get("gap_classification") == gc)
            color = GAP_COLORS[gc]
            st.markdown(f'<div class="hier-card"><b>{label}</b>'
                        f'<span class="metric"> — {count} districts ({capability})</span></div>',
                        unsafe_allow_html=True)

    with st.expander("4. Reachability", expanded=False):
        st.markdown("**Staging City**: Patna, Bihar (25.59°N, 85.14°E)")
        st.markdown("All distances measured as road km from Patna. Source: haversine × 1.4 road factor.")

    with st.expander("5. Cost Model", expanded=False):
        assumptions = [
            ("Transport", "$0.35/km"),
            ("Per Diem", "$60/person/day"),
            ("Team Size", "6 persons"),
            ("Mission Days", "7 days"),
            ("Surgeon Day Value", "$800/day"),
        ]
        for label, val in assumptions:
            st.markdown(f"- **{label}**: {val}")

    with st.expander("6. Impact Ranking (Maternal · Patna)", expanded=True):
        ranking = load_ranking()
        if ranking and not ranking.get("error"):
            col_conf, col_cand = st.columns(2)
            with col_conf:
                st.markdown("**Confirmed Gaps (measured)**")
                for r in ranking.get("confirmed_gaps", [])[:10]:
                    st.markdown(
                        f'<div class="hier-card"><b>{r["district"]}, {r["state"]}</b>'
                        f'<div class="metric">need/$: {r["need_per_dollar"]:.2e} · '
                        f'burden: {r["burden_score"]:.3f} · ${r["cost_total_usd"]:,.0f} · '
                        f'{r["drive_hours"]}h</div></div>', unsafe_allow_html=True)
            with col_cand:
                st.markdown("**Candidate Gaps (investigate)**")
                for r in ranking.get("candidate_gaps", [])[:5]:
                    st.markdown(
                        f'<div class="hier-card"><b>{r["district"]}, {r["state"]}</b>'
                        f'<div class="metric">need/$: {r["need_per_dollar"]:.2e} · '
                        f'burden: {r["burden_score"]:.3f} · ${r["cost_total_usd"]:,.0f}</div></div>',
                        unsafe_allow_html=True)
        else:
            st.info("Ranking data unavailable — ensure cache CSVs are built.")

    with st.expander("7. Planner Workflow", expanded=False):
        tools = ["Scenario (saved inputs + ranking snapshot)", "Review (approve / reject / investigate)",
                 "Shortlist (pinned districts)", "Note (free-text annotation)"]
        for t in tools:
            st.markdown(f"- {t}")

    with st.expander("8. Data Provenance", expanded=False):
        sources = [
            ("NFHS-5 District Health Indicators", "2019-21, 706 districts"),
            ("Virtue Foundation Facilities", "2024, ~10K facilities"),
            ("India Post PIN Directory", "165K PIN codes for geocoding"),
            ("Facility Free-Text Claims", "claims.py terminology matching"),
        ]
        for name, detail in sources:
            st.markdown(f'<div class="hier-card"><b>{name}</b><span class="metric"> — {detail}</span></div>',
                        unsafe_allow_html=True)


# ============================================================================= CHAT + GLOSSARY TAB
with tab_chat:
    col_chat, col_gloss = st.columns([3, 2])

    with col_chat:
        st.markdown("### Ask Copilot")
        st.caption("Ask questions about burden, coverage, costs, rankings, or architecture.")

        # Sample prompts
        st.markdown("**Sample questions:**")
        prompt_cols = st.columns(2)
        sample_prompts = [q for q, _ in QA_PAIRS[:8]]
        for i, prompt in enumerate(sample_prompts):
            with prompt_cols[i % 2]:
                if st.button(prompt, key=f"sp_{i}", width="stretch"):
                    st.session_state["chat_input"] = prompt

        # Chat input
        q = st.text_input("Your question", key="chat_input",
                          placeholder="e.g. What is need-per-dollar?")
        if q:
            st.markdown(f'<div class="chat-q">{q}</div>', unsafe_allow_html=True)
            # Find best match
            q_lower = q.lower()
            best_answer = None
            best_score = 0
            for question, answer in QA_PAIRS:
                words = q_lower.split()
                hits = sum(1 for w in words if len(w) > 2 and w in question.lower())
                score = hits / len(words) if words else 0
                if score > best_score:
                    best_score = score
                    best_answer = answer

            if best_answer and best_score > 0.3:
                st.markdown(f'<div class="chat-a">{best_answer}</div>', unsafe_allow_html=True)
            else:
                # Search glossary
                found = None
                for g in GLOSSARY:
                    if g["term"].lower() in q_lower:
                        found = f"**{g['term']}**: {g['definition']}"
                        break
                if not found:
                    for a in ACRONYMS:
                        if a["acronym"].lower() in q_lower:
                            found = f"**{a['acronym']}**: {a['definition']}"
                            break
                if found:
                    st.markdown(f'<div class="chat-a">{found}</div>', unsafe_allow_html=True)
                else:
                    st.markdown('<div class="chat-a">I don\'t have a specific answer for that. '
                                'Try asking about: burden scores, coverage gaps, claim grades, '
                                'cost assumptions, or district rankings.</div>', unsafe_allow_html=True)

    with col_gloss:
        st.markdown("### Glossary & Vocabulary")
        search = st.text_input("Search terms...", key="gloss_search", placeholder="e.g. burden, NFHS")
        gloss_tab = st.radio("Category", ["All", "Definitions", "Acronyms"], horizontal=True, key="gloss_cat")

        items = []
        if gloss_tab in ("All", "Definitions"):
            for g in GLOSSARY:
                if not search or search.lower() in g["term"].lower() or search.lower() in g["definition"].lower():
                    items.append(("definition", g["term"], g["definition"]))
        if gloss_tab in ("All", "Acronyms"):
            for a in ACRONYMS:
                if not search or search.lower() in a["acronym"].lower() or search.lower() in a["definition"].lower():
                    items.append(("acronym", a["acronym"], a["definition"]))

        if items:
            for typ, term, defn in items:
                st.markdown(f'<div class="hier-card"><b>{term}</b>'
                            f'<div class="metric">{defn}</div>'
                            f'<div class="metric" style="opacity:0.6">{typ}</div></div>',
                            unsafe_allow_html=True)
        else:
            st.info("No matching terms found.")
