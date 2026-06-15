"""
app.py — Medical Desert Planner (Databricks App, Streamlit) — Track 2, map-first.

A non-technical planner picks a CAPABILITY, sees a trust-weighted India choropleth (lit states = we
have data; muted = no data yet), clicks a state to drill into its districts, and clicks a district to
inspect the underlying facility records (name, the facility's own claimed text, a source link, a
claim-confidence). Scenarios / shortlist / reviews / notes persist (Lakebase / local SQLite).

The map is Plotly over a BUNDLED GeoJSON (assets/india_states.geojson) — fully offline, no tiles/CDN.
The cost-per-impact "Deployment optimizer" (maternal, from Patna) is a deep-dive tab. Everything
deterministic works with NO LLM; the agent tab degrades gracefully if the model is unreachable.
"""

import json
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mission_core import data_access as da
from mission_core.claims import CAPABILITY_LABELS, CAPABILITIES
from mission_core.coverage import DESERT_SHADE_THRESHOLDS
from mission_core.coverage_view import coverage_by_geography, coverage_summary, state_rollup
from mission_core.geo_names import from_topo_state
from agent import tools as T

STATE_ALL = "India — all states"
VERDICTS = ["approve", "reject", "needs-investigation"]
_GEOJSON = json.loads((Path(__file__).resolve().parent / "assets" / "india_states.geojson").read_text())

# the shared five-state colour vocabulary + no-data (light fills; they read on dark too, per spec)
CAT_ORDER = ["no_data", "no_claim_desert", "claim_only", "weaker", "moderate", "strong"]
CAT_COLOR = {
    "strong": "#0F6E56", "moderate": "#1D9E75", "weaker": "#5DCAA5",
    "claim_only": "#EF9F27", "no_claim_desert": "#E24B4A", "no_data": "#D3D1C7",
}
CAT_LABEL = {
    "strong": "Strong coverage", "moderate": "Moderate coverage", "weaker": "Weaker coverage",
    "claim_only": "Claims only — not verified", "no_claim_desert": "No-claim desert",
    "no_data": "No data yet",
}


def _district_cat(r: dict) -> str:
    """Map a per-district coverage row to the shared colour vocabulary."""
    if r["total_facilities"] == 0:
        return "no_data"
    gc = r["gap_classification"]
    if gc == "no_claim_desert":
        return "no_claim_desert"
    if gc == "unverified_claims":
        return "claim_only"
    md = r["desert_score"]
    if md < DESERT_SHADE_THRESHOLDS["strong"]:
        return "strong"
    return "moderate" if md < DESERT_SHADE_THRESHOLDS["moderate"] else "weaker"


# ----------------------------------------------------------------------------- page + theme
st.set_page_config(page_title="Medical Desert Planner", page_icon="🩺", layout="wide")
ACCENT = "#FF3621"
st.markdown(f"""
<style>
  :root {{
    --bg:#F9F7F4; --surface:#ffffff; --border:#e7e4dd; --ink:#0B2026; --muted:#5b6770;
    --accent:{ACCENT}; --chip:#eef2f4; --nodata:#D3D1C7;
  }}
  .stApp {{ background: var(--bg); }}
  html, body, [class*="css"] {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; }}
  h1,h2,h3 {{ color: var(--ink); font-weight:500; letter-spacing:-0.01em; }}
  .hero {{ padding:6px 0 2px; }}
  .hero h1 {{ margin:0; font-size:1.5rem; }}
  .hero p {{ margin:.25rem 0 0; color:var(--muted); font-size:.92rem; max-width:60rem; }}
  .crumb {{ color:var(--muted); font-size:.95rem; margin:.2rem 0 .4rem; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
           padding:14px 16px; margin-bottom:10px; }}
  .kpi {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:14px 16px; }}
  .kpi .n {{ font-size:1.6rem; font-weight:500; letter-spacing:-0.02em; color:var(--ink); }}
  .kpi .l {{ color:var(--muted); font-size:.8rem; }}
  .pill {{ display:inline-block; padding:3px 11px; border-radius:999px; font-size:.74rem; font-weight:500; }}
  .pill-cap {{ background:var(--chip); color:var(--ink); border:1px solid var(--border); }}
  .pill-hi {{ background:rgba(15,110,86,.14); color:#0F6E56; }}
  .pill-med {{ background:rgba(239,159,39,.16); color:#9a6300; }}
  .pill-lo {{ background:rgba(226,75,74,.14); color:#b71c1c; }}
  .swatch {{ display:inline-block; width:11px; height:11px; border-radius:3px; margin-right:5px; vertical-align:middle; }}
  .muted {{ color:var(--muted); font-size:.84rem; }}
  .tile {{ border:1px solid var(--border); border-radius:12px; padding:10px 12px; margin-bottom:6px; color:#04342C; }}
  .tile b {{ font-weight:500; }}
  a {{ color:var(--accent); }}
  .stButton button {{ border-radius:10px; border:1px solid var(--border); font-weight:500; }}
  /* top nav bar: title + logo live in the Streamlit header (same bar as the ⋮ menu) */
  [data-testid="stHeader"] {{ background:var(--surface); border-bottom:1px solid var(--border); height:3.5rem; }}
  [data-testid="stHeader"]::before {{
    content:"🩺  Medical Desert Planner"; position:absolute; left:1.25rem; top:0; height:3.5rem;
    display:flex; align-items:center; font-size:1.15rem; font-weight:600; letter-spacing:-0.01em;
    color:var(--ink); white-space:nowrap; pointer-events:none;
  }}
</style>""", unsafe_allow_html=True)


def _pill(cls, text):
    return f'<span class="pill {cls}">{text}</span>'


def _conf_pill(conf):
    return _pill("pill-hi" if conf == "high" else "pill-med" if conf == "medium" else "pill-lo", conf)


def _legend():
    chips = " &nbsp; ".join(
        f'<span class="swatch" style="background:{CAT_COLOR[c]}"></span>{CAT_LABEL[c]}' for c in
        ["strong", "moderate", "weaker", "claim_only", "no_claim_desert", "no_data"])
    return f'<div class="muted" style="margin-top:6px">{chips}</div>'


def india_figure(roll):
    n = len(CAT_ORDER)
    scale = []
    for i, cat in enumerate(CAT_ORDER):
        scale += [[i / n, CAT_COLOR[cat]], [(i + 1) / n, CAT_COLOR[cat]]]
    cust = [[r["our_state"] or r["st_nm"], CAT_LABEL[r["fill_category"]],
             r["verified_facilities"], r["n_districts"]] for r in roll]
    ch = go.Choropleth(
        geojson=_GEOJSON, featureidkey="properties.st_nm",
        locations=[r["st_nm"] for r in roll], z=[CAT_ORDER.index(r["fill_category"]) for r in roll],
        zmin=0, zmax=n, colorscale=scale, showscale=False,
        marker_line_color="white", marker_line_width=0.6, customdata=cust,
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>"
                      "%{customdata[2]} verified facilities · %{customdata[3]} districts<extra></extra>")
    fig = go.Figure(ch)
    fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator", bgcolor="rgba(0,0,0,0)")
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=560, dragmode=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


def state_figure(st_nm, fill_cat):
    feats = [f for f in _GEOJSON["features"] if f["properties"]["st_nm"] == st_nm]
    ch = go.Choropleth(geojson={"type": "FeatureCollection", "features": feats},
                       featureidkey="properties.st_nm", locations=[st_nm], z=[0],
                       colorscale=[[0, CAT_COLOR[fill_cat]], [1, CAT_COLOR[fill_cat]]],
                       showscale=False, marker_line_color="white", marker_line_width=0.8,
                       hovertemplate=f"<b>{st_nm}</b><extra></extra>")
    fig = go.Figure(ch)
    fig.update_geos(fitbounds="locations", visible=False, projection_type="mercator", bgcolor="rgba(0,0,0,0)")
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=240, dragmode=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


# ----------------------------------------------------------------------------- session sync
ss = st.session_state
# apply a pending state-dropdown sync (from a map click / list / tile) BEFORE the widget is built
if "_pending_state" in ss:
    ss["state_select"] = ss.pop("_pending_state")
if "_pending_scenario" in ss:
    _inp = ss.pop("_pending_scenario")["inputs"]
    ss["capability"] = _inp.get("capability", "maternity")
    ss["state_select"] = _inp.get("state", STATE_ALL)
    ss["count_unverified"] = bool(_inp.get("count_unverified", False))
    ss["team_size"] = int(_inp.get("team_size", 6))
    ss["days"] = int(_inp.get("days", 7))
    ss["active_district"] = None
ss.setdefault("active_district", None)


def go_to_state(st_nm):
    ss["_pending_state"] = st_nm
    ss["active_district"] = None
    st.rerun()


def go_to_india():
    ss["_pending_state"] = STATE_ALL
    ss["active_district"] = None
    ss.pop("india_map", None)
    st.rerun()


def open_district(name):
    ss["active_district"] = name
    st.rerun()


# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.subheader("Filters")
    st.selectbox("Country", ["India"], index=0, disabled=True,
                 help="This dataset covers India only — fixed.")
    capability = st.selectbox("Capability", CAPABILITIES,
                              index=CAPABILITIES.index(ss.get("capability", "maternity")),
                              format_func=lambda c: CAPABILITY_LABELS.get(c, c), key="capability")
    count_unverified = st.toggle("Count unverified claims as supply?",
                                 value=ss.get("count_unverified", False), key="count_unverified",
                                 help="Off (default): only text-verified facilities count as supply — "
                                      "the honest, trust-weighted view. On: unverified claims count "
                                      "at a discount, shrinking apparent gaps.")
    roll = state_rollup(capability, count_unverified)
    topo2our = {r["st_nm"]: r["our_state"] for r in roll}
    lit_topo = sorted(r["st_nm"] for r in roll if r["lit"])
    _opts = [STATE_ALL] + lit_topo
    if ss.get("state_select") not in _opts:
        ss["state_select"] = STATE_ALL
    state_select = st.selectbox("State / UT", _opts,
                                index=_opts.index(ss.get("state_select", STATE_ALL)), key="state_select")
    if ss.get("_last_state") != state_select:          # dropdown change resets the district drill
        ss["active_district"] = None
        ss["_last_state"] = state_select
    backend = "Lakebase" if (os.environ.get("PGHOST") or os.environ.get("LAKEBASE_ENDPOINT")) else "local CSV"
    st.caption(f"Data backend: {backend}")

    st.divider()
    st.subheader("Scenarios")
    try:
        _scenarios = da.list_scenarios()
    except Exception as e:
        _scenarios = None
        st.caption(f"⚠️ scenario store unavailable: {e}")
    if _scenarios is not None:
        _name = st.text_input("Name this scenario", placeholder=f"{capability} · {state_select}",
                              key="scenario_name")
        if st.button("Save current scenario", width="stretch"):
            label = _name.strip() or f"{CAPABILITY_LABELS.get(capability, capability)} · {state_select}"
            da.save_scenario(label, {"capability": capability, "state": state_select,
                                     "count_unverified": count_unverified,
                                     "team_size": ss.get("team_size", 6), "days": ss.get("days", 7)},
                             {"summary": coverage_summary(coverage_by_geography(
                                 capability, topo2our.get(state_select), count_unverified))})
            st.success(f"Saved “{label}”"); st.rerun()
        if _scenarios:
            opts = {f"{s['name']}  ·  {s['created_at'][:16]}": s["id"] for s in _scenarios}
            choice = st.selectbox("Load a saved scenario", list(opts), key="scenario_load_pick")
            c1, c2 = st.columns(2)
            if c1.button("Load", width="stretch"):
                sc = da.get_scenario(opts[choice])
                if sc:
                    ss["_pending_scenario"] = sc; st.rerun()
            if c2.button("Delete", width="stretch"):
                da.delete_scenario(opts[choice]); st.rerun()
        st.caption(f"Persisted to: {da.store_backend()}")

cap_label = CAPABILITY_LABELS.get(capability, capability)
active_state = None if state_select == STATE_ALL else topo2our.get(state_select)

# ----------------------------------------------------------------------------- intro line (title is in the nav bar)
st.markdown(
    f'<div class="hero"><p>Trust-weighted facility coverage for {_pill("pill-cap", cap_label)} across India — '
    'telling real care deserts apart from data-poor regions. Every facility claim is graded against its own '
    'text and cited with a source.</p></div>', unsafe_allow_html=True)

# ============================================================================= INDIA view
if active_state is None:
    n_lit = sum(1 for r in roll if r["lit"])
    n_desert = sum(1 for r in roll if r["fill_category"] == "no_claim_desert")
    n_verified = sum(r["verified_facilities"] for r in roll)
    st.markdown(f'<div class="crumb">India</div>', unsafe_allow_html=True)
    left, rail = st.columns([3, 1])
    with left:
        ev = st.plotly_chart(india_figure(roll), key="india_map", on_select="rerun",
                             config={"displayModeBar": False}, width="stretch")
        st.markdown(_legend(), unsafe_allow_html=True)
        pts = (ev or {}).get("selection", {}).get("points", []) if ev else []
        if pts:
            clicked = pts[0].get("location")
            if clicked and clicked in lit_topo:
                go_to_state(clicked)
    with rail:
        st.markdown(f'<div class="kpi"><div class="n">{n_lit} / 36</div>'
                    f'<div class="l">states with facility data</div></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi" style="margin-top:8px"><div class="n">{n_desert}</div>'
                    f'<div class="l">states: no facility claims {cap_label}</div></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi" style="margin-top:8px"><div class="n">{n_verified:,}</div>'
                    f'<div class="l">text-verified facilities</div></div>', unsafe_allow_html=True)
        st.markdown("<div style='margin-top:10px' class='muted'>Drill into a state</div>", unsafe_allow_html=True)
        worst = sorted([r for r in roll if r["lit"]],
                       key=lambda r: (r["fill_category"] != "no_claim_desert", -(r["mean_desert_score"] or 0)))
        for r in worst[:10]:
            if st.button(f"{r['st_nm']}  ·  {CAT_LABEL[r['fill_category']].split('—')[0].strip()}",
                         key=f"rail_{r['st_nm']}", width="stretch"):
                go_to_state(r["st_nm"])

# ============================================================================= STATE view
else:
    rows = coverage_by_geography(capability, active_state, count_unverified)
    summ = coverage_summary(rows)
    fill_cat = next((r["fill_category"] for r in roll if r["st_nm"] == state_select), "no_data")
    cb, _ = st.columns([4, 1])
    with cb:
        bcol1, bcol2 = st.columns([1, 6])
        if bcol1.button("‹ India", key="back_india"):
            go_to_india()
        bcol2.markdown(f'<div class="crumb">India › <b>{state_select}</b></div>', unsafe_allow_html=True)

    head, mini = st.columns([3, 1])
    with mini:
        st.plotly_chart(state_figure(state_select, fill_cat), config={"displayModeBar": False},
                        width="stretch", key="state_map")
    with head:
        k1, k2, k3 = st.columns(3)
        k1.markdown(f'<div class="kpi"><div class="n">{summ["confirmed_coverage"]}</div>'
                    f'<div class="l">confirmed-coverage districts</div></div>', unsafe_allow_html=True)
        k2.markdown(f'<div class="kpi"><div class="n">{summ["unverified_claims"]}</div>'
                    f'<div class="l">claim-only districts</div></div>', unsafe_allow_html=True)
        k3.markdown(f'<div class="kpi"><div class="n">{summ["no_claim_desert"]}</div>'
                    f'<div class="l">no-claim deserts</div></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="muted" style="margin-top:8px">{summ["districts"]} districts · '
                    f'{summ["verified_facilities"]} text-verified facilities for {cap_label}. '
                    f'Click a district to see the facility records.</div>', unsafe_allow_html=True)
        st.markdown(_legend(), unsafe_allow_html=True)

    # district tile grid (clickable → drill)
    st.markdown("#### Districts")
    cols = st.columns(4)
    for i, r in enumerate(rows):
        cat = _district_cat(r)
        sub = (f'{r["verified_supply"]}/{r["total_facilities"]} verified'
               if r["total_facilities"] else "no facilities")
        if cat == "no_claim_desert":
            sub = "no facility claims it"
        with cols[i % 4]:
            st.markdown(f'<div class="tile" style="background:{CAT_COLOR[cat]}33;border-color:{CAT_COLOR[cat]}">'
                        f'<b>{r["district"]}</b><br><span class="muted">{sub} · score {r["desert_score"]}</span></div>',
                        unsafe_allow_html=True)
            if st.button("Open ›", key=f"tile_{r['district']}", width="stretch"):
                open_district(r["district"])

    # ranked table (reuse)
    with st.expander("Ranked district table", expanded=False):
        st.dataframe(pd.DataFrame([{
            "rank": r["rank"], "district": r["district"], "gap": CAT_LABEL[_district_cat(r)],
            "verified": r["verified_supply"], "unverified": r["unverified"],
            "trust_ratio": r["trust_ratio"], "facilities": r["total_facilities"],
            "desert_score": r["desert_score"],
        } for r in rows]), width="stretch", hide_index=True, height=300)

    # ----------------------- district drill (facility records + decisions) — single instance
    if ss.get("active_district"):
        pick = ss["active_district"]
        row = next((r for r in rows if r["district"] == pick), None)
        if row is not None:
            st.divider()
            cat = _district_cat(row)
            st.markdown(f'### {pick}, {state_select} &nbsp; {_pill("pill-" + ("hi" if cat in ("strong","moderate") else "med" if cat=="claim_only" else "lo"), CAT_LABEL[cat])}',
                        unsafe_allow_html=True)
            st.markdown(f'<span class="muted">text-verified {row["verified_supply"]} '
                        f'(high {row["high"]}, medium {row["medium"]}) · unverified {row["unverified"]} · '
                        f'trust-weighted {row["trust_weighted_supply"]}'
                        + (f' · burden {row["burden"]}' if row["has_burden"] else "")
                        + f' · desert score {row["desert_score"]}</span>', unsafe_allow_html=True)

            claims = da.load_facility_claims(pick, capability)
            if claims:
                st.markdown(f"**Facility evidence** — capability is a *claim to verify, not ground truth*. "
                            f"{len(claims)} record(s):")
                for c in claims[:12]:
                    link = f' · <a href="{c["source_url"]}" target="_blank">source</a>' if c.get("source_url") else ""
                    cap_ev, proc_ev = c.get("capability_evidence") or "", c.get("procedure_evidence") or ""
                    st.markdown(
                        f'<div class="card">{_conf_pill(c["claim_confidence"])} &nbsp;<b>{c.get("name") or "(unnamed)"}</b>'
                        f'<span class="muted"> · {c.get("city") or ""} · {c.get("operator") or ""}{link}</span>'
                        + (f'<br><b>claims:</b> “{cap_ev}”' if cap_ev else
                           '<br><b>claims:</b> <i>flag/specialty asserts it, but the facility’s own text doesn’t — unverified</i>')
                        + (f'<br><b>corroborated by:</b> “{proc_ev}”' if proc_ev else
                           ('<br><span class="muted">not corroborated by procedure/equipment text</span>' if cap_ev else ''))
                        + '</div>', unsafe_allow_html=True)
                st.caption("Each line is the facility's own extracted text — cited with its source link.")
            else:
                st.info("No facility even claims this capability here — a candidate care desert (or a data gap).")

            # the SINGLE, district-scoped "Your decisions" block
            st.markdown("**Your decisions** — saved to the workspace (scoped to this capability)")
            pkey = f"{capability}:{da.normalize_name(pick)}"
            label = f"{pick} ({cap_label})"
            try:
                pinned = any(s["district_key"] == pkey for s in da.list_shortlist())
                reviewed = da.latest_reviews().get(pkey)
                existing_notes = da.list_notes(pkey)
                store_ok = True
            except Exception as e:
                store_ok = False
                st.caption(f"⚠️ workspace store unavailable: {e}")
            if store_ok:
                cpin, crev = st.columns([1, 2])
                with cpin:
                    if pinned:
                        if st.button("📌 Unpin", key="unpin", width="stretch"):
                            da.remove_from_shortlist(pkey); st.rerun()
                    elif st.button("📌 Pin to shortlist", key="pin", width="stretch"):
                        da.add_to_shortlist(pkey, label, state_select); st.rerun()
                with crev:
                    cur = reviewed["verdict"] if reviewed else "(none)"
                    vchoice = st.radio(f"Review decision (current: {cur})", VERDICTS, horizontal=True,
                                       key="verdict_pick", index=VERDICTS.index(reviewed["verdict"]) if reviewed else 2)
                    rnote = st.text_input("Reason (optional)", key="verdict_note", value=reviewed["note"] if reviewed else "")
                    if st.button("Record decision", key="record_verdict"):
                        da.save_review(pkey, label, state_select, vchoice, rnote); st.success(f"Recorded: {vchoice}"); st.rerun()
                note_text = st.text_area("Add a note", key="note_text", placeholder=f"e.g. call DH re: {cap_label} capacity")
                if st.button("Save note", key="save_note") and note_text.strip():
                    da.save_note(pkey, label, note_text.strip()); st.rerun()
                for n in existing_notes:
                    st.caption(f"📝 {n['created_at'][:16]} — {n['note_text']}")

# ============================================================================= deep-dive tabs
st.divider()
tab_opt, tab_workspace, tab_agent = st.tabs(
    ["Deployment optimizer (maternal · Patna)", "My workspace", "Ask the copilot"])

with tab_opt:
    st.caption("Cost-per-impact ranking: which district does a volunteer team do the most good per "
               "dollar from Patna — burden × trust-weighted gap ÷ mission cost. Available for maternity "
               "(the capability with NFHS burden + road-reachability data).")
    team_size = st.slider("Team size", 2, 20, ss.get("team_size", 6), key="team_size")
    days = st.slider("Mission days", 1, 21, ss.get("days", 7), key="days")
    res = T.rank_districts_tool("maternal_health", team_size=team_size, days=days, top_n=6,
                                count_unverified=count_unverified)
    conf, cand = res["confirmed_gaps"], res["candidate_gaps"]
    if conf:
        top = conf[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Top confirmed pick", f"{top['district']}, {top['state']}")
        c2.metric("Need addressed / $", f"{top['need_per_dollar']:.2e}")
        c3.metric("Est. mission cost", f"${top['cost_total_usd']:,.0f}")
    lo, ro = st.columns(2)
    with lo:
        st.markdown("**Confirmed gaps** — measured facility data")
        for r in conf:
            st.markdown(f'<div class="card"><b>#{r["rank"]} {r["district"]}, {r["state"]}</b><br>'
                        f'<span class="muted">burden {r["burden_score"]} · gap {r["gap"]} · verified supply '
                        f'{r["verified_maternal_supply"]} · {r["drive_hours"]}h / {r["distance_km"]}km · '
                        f'${r["cost_total_usd"]:,.0f} · need/$ {r["need_per_dollar"]:.2e}</span></div>',
                        unsafe_allow_html=True)
    with ro:
        st.markdown("**Candidate gaps** — no facility data; investigate")
        for r in cand:
            st.markdown(f'<div class="card"><b>#{r["rank"]} {r["district"]}, {r["state"]}</b><br>'
                        f'<span class="muted">burden {r["burden_score"]} · gap {r["gap"]} · '
                        f'{r["drive_hours"]}h / {r["distance_km"]}km · no facility data — verify on the ground</span></div>',
                        unsafe_allow_html=True)
    st.divider()
    cr, cbf = st.columns(2)
    with cr:
        st.markdown("**Robustness** — does the #1 pick survive a cost-assumption sweep?")
        coef = st.selectbox("Sweep", list(T.COEFFICIENTS), format_func=lambda k: T.COEFFICIENTS[k][2])
        s = T.sensitivity_analysis("maternal_health", coef, team_size, days)
        if s.get("error"):
            st.warning(s["error"])
        else:
            st.success(s["verdict"])
            st.line_chart(pd.DataFrame([{"value": p["value"], "need_per_$": p["top_metric"]}
                                        for p in s["points"]]).set_index("value"))
    with cbf:
        st.markdown("**Mission brief** — cited one-pager for a confirmed pick")
        bnames = [r["district"] for r in conf]
        if bnames:
            bpick = st.selectbox("District for brief", bnames, key="brief_pick")
            brief = T.generate_brief("maternal_health", bpick, team_size, days).get("brief", "")
            st.code(brief, language="text")
            st.download_button("Download brief", brief, file_name=f"brief_{bpick}.txt")
        else:
            st.info("No confirmed-gap district to brief.")

with tab_workspace:
    st.caption("Everything you save — shortlist, review decisions, notes, scenarios — persisted across "
               "sessions, scoped per capability. The planner's working memory.")
    try:
        sl, rv, nt, sc = da.list_shortlist(), da.list_reviews(), da.list_notes(), da.list_scenarios()
        wfail = None
    except Exception as e:
        sl = rv = nt = sc = []; wfail = e
    if wfail:
        st.warning(f"Workspace store unavailable: {wfail}")
    w1, w2 = st.columns(2)
    with w1:
        st.markdown("**Shortlist**")
        for s in sl:
            a, b = st.columns([4, 1])
            a.write(f"{s['district']}, {s['state']}")
            if b.button("✕", key=f"sl_{s['id']}"):
                da.remove_from_shortlist(s["district_key"]); st.rerun()
        if not sl:
            st.caption("No districts pinned yet.")
        st.markdown("**Review decisions**")
        for r in rv:
            emoji = {"approve": "✅", "reject": "🚫"}.get(r["verdict"], "🔍")
            st.caption(f"{emoji} {r['verdict']} — {r['district']}"
                       + (f" · {r['note']}" if r["note"] else "") + f"  ·  {r['created_at'][:16]}")
        if not rv:
            st.caption("No decisions recorded yet.")
    with w2:
        st.markdown("**Notes**")
        for n in nt:
            st.caption(f"{n['district']} · {n['created_at'][:16]} — {n['note_text']}")
        if not nt:
            st.caption("No notes yet.")
        st.markdown("**Saved scenarios**")
        for s in sc:
            st.caption(f"{s['name']}  ·  {s['created_at'][:16]}")
        if not sc:
            st.caption("No scenarios saved yet — save one from the sidebar.")

with tab_agent:
    st.caption("Free-form planning — the agent orchestrates the tools, cites facility text, and explains. "
               "Needs the LLM key.")
    q = st.text_input("Ask the copilot",
                      placeholder="e.g. best-evidenced NICU facilities in Bihar — and where are the deserts?")
    if st.button("Run agent", type="primary") and q.strip():
        try:
            from agent.orchestrator import run
            with st.spinner("Agent reasoning + calling tools…"):
                result = run(q)
            with st.expander("Tool calls (the agent's reasoning trace)", expanded=True):
                for step in result.tool_trace:
                    st.write(f"→ `{step['tool']}`({step['args']})")
            st.markdown(result.final_text)
        except Exception as e:
            st.error(f"Agent unavailable (LLM not reachable from the app): {e}")
            st.info("The map + optimizer above are fully deterministic — they work without the LLM.")
