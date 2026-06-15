"""
app.py — Medical Mission Deployment Copilot (Databricks App, Streamlit).

Reads the district base + reachability from Lakebase (sub-10ms) via mission_core.data_access,
runs the deterministic cost-per-impact chain, and exposes the tool-orchestrating agent. The
deterministic dashboard works with NO LLM; the "Ask the copilot" panel uses the agent and
degrades gracefully if the model is unreachable.
"""

import os
import streamlit as st

from mission_core.data_access import STAGING
from mission_core.burden import INTERVENTION_INDICATORS
from agent import tools as T

# ----------------------------------------------------------------------------- page + theme
st.set_page_config(page_title="Mission Deployment Copilot", page_icon="🩺", layout="wide")

DBX_RED, DBX_INK, DBX_SAND = "#FF3621", "#0B2026", "#F9F7F4"
st.markdown(f"""
<style>
  .stApp {{ background: {DBX_SAND}; color:{DBX_INK}; }}
  /* force readable dark text everywhere on the light background */
  .stApp, .stApp p, .stApp span, .stApp label, .stApp li,
  .stApp .stMarkdown, [data-testid="stMetricValue"], [data-testid="stMetricLabel"] {{ color:{DBX_INK}; }}
  [data-testid="stMetricValue"] {{ color:{DBX_INK} !important; font-weight:700; }}
  [data-testid="stMetricLabel"] p {{ color:#5b6770 !important; }}
  .hero, .hero * {{ color:#fff !important; }}
  .hero {{ background:{DBX_INK}; padding:18px 24px; border-radius:12px; margin-bottom:8px; }}
  .hero h1 {{ margin:0; font-size:1.5rem; }}
  .hero p {{ margin:.25rem 0 0; opacity:.85; font-size:.9rem; }}
  .tierbar {{ border-left:5px solid {DBX_RED}; padding:.2rem 0 .2rem .7rem; margin:.4rem 0;
              font-weight:700; color:{DBX_INK}; }}
  .card {{ background:#fff; border:1px solid #e6e3dd; border-radius:10px; padding:14px 16px; margin-bottom:10px; }}
  .card b {{ color:{DBX_INK}; }}
  .pill {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.72rem; font-weight:600; }}
  .pill-hi {{ background:#e8f5e9; color:#1b5e20; }}
  .pill-med {{ background:#fff3e0; color:#e65100; }}
  .pill-lo {{ background:#fdecea; color:#b71c1c; }}
  .muted {{ color:#5b6770; font-size:.82rem; }}
</style>""", unsafe_allow_html=True)

st.markdown(
    '<div class="hero"><h1>🩺 Medical Mission Deployment Copilot</h1>'
    '<p>Where should a volunteer medical team go — and which intervention — to do the most good per dollar? '
    'Grounded in NFHS-5 burden, facility coverage, and road reachability. Built for the Virtue Foundation (DAIS 2026).</p></div>',
    unsafe_allow_html=True)


def _pill(conf: str) -> str:
    cls = "pill-hi" if conf.startswith("high") else "pill-lo" if conf.startswith("low") else "pill-med"
    return f'<span class="pill {cls}">{conf}</span>'


# ----------------------------------------------------------------------------- sidebar (constraints)
with st.sidebar:
    st.subheader("Mission constraints")
    intervention = st.selectbox("Intervention", list(INTERVENTION_INDICATORS),
                                index=list(INTERVENTION_INDICATORS).index("maternal_health"))
    team_size = st.slider("Team size", 2, 20, 6)
    days = st.slider("Mission days", 1, 21, 7)
    st.caption(f"Staging city: **{STAGING['name']}**  ·  candidate region: Bihar + Jharkhand")
    backend = "Lakebase (sub-10ms)" if (os.environ.get("PGHOST") or os.environ.get("LAKEBASE_ENDPOINT")) else "local CSV"
    st.caption(f"Data backend: **{backend}**")

# ----------------------------------------------------------------------------- ranking (deterministic, no LLM)
res = T.rank_districts_tool(intervention, team_size=team_size, days=days, top_n=6)
conf, cand = res["confirmed_gaps"], res["candidate_gaps"]

if conf:
    top = conf[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Top confirmed pick", f"{top['district']}, {top['state']}")
    c2.metric("Need addressed / $", f"{top['need_per_dollar']:.2e}")
    c3.metric("Est. mission cost", f"${top['cost_total_usd']:,.0f}")

left, right = st.columns(2)

with left:
    st.markdown('<div class="tierbar">✅ CONFIRMED GAPS — measured facility data; act on these</div>',
                unsafe_allow_html=True)
    for r in conf:
        st.markdown(
            f'<div class="card"><b>#{r["rank"]} {r["district"]}, {r["state"]}</b> &nbsp; {_pill(r["data_confidence"])}'
            f'<br><span class="muted">burden {r["burden_score"]} · gap {r["gap"]} · '
            f'reachable supply {r["reachable_supply"]} · {r["drive_hours"]}h / {r["distance_km"]}km · '
            f'${r["cost_total_usd"]:,.0f} · need/$ {r["need_per_dollar"]:.2e}</span></div>',
            unsafe_allow_html=True)

with right:
    st.markdown('<div class="tierbar">🔍 CANDIDATE GAPS — no facility data; investigate (data needs work)</div>',
                unsafe_allow_html=True)
    for r in cand:
        st.markdown(
            f'<div class="card"><b>#{r["rank"]} {r["district"]}, {r["state"]}</b> &nbsp; {_pill("low")}'
            f'<br><span class="muted">burden {r["burden_score"]} · gap {r["gap"]} · '
            f'{r["drive_hours"]}h / {r["distance_km"]}km · NO facility data — verify on the ground</span></div>',
            unsafe_allow_html=True)
    st.caption("These may rank *higher* by need/$ precisely because supply is unknown — never a confident pick.")

# ----------------------------------------------------------------------------- detail + robustness + brief
st.divider()
tab_detail, tab_robust, tab_brief, tab_agent = st.tabs(
    ["🔬 Where the numbers come from", "📈 Robustness", "📄 Mission brief", "💬 Ask the copilot"])

with tab_detail:
    names = [r["district"] for r in conf] + [r["district"] for r in cand]
    pick = st.selectbox("District", names, key="detail_pick")
    d = T.get_district_detail(intervention, pick, team_size, days)
    if d.get("error") or d.get("excluded"):
        st.warning(d.get("reason") or d.get("error"))
    else:
        cc = d["cost"]
        st.markdown(f"**{d['district']}, {d['state']}** — {d['data_confidence']}")
        b = d["burden"]
        st.write(f"**Burden** {b['score']} ({b['confidence']}), {b['indicators_used']}/{b['indicators_total']} indicators used"
                 + (f" · missing: {', '.join(b['missing_indicators'])}" if b.get('missing_indicators') else ""))
        st.write(f"**Coverage gap** {d['gap']['gap']} · reachable relevant supply {d['supply']['reachable_relevant']}")
        st.write(f"**Reach** {d['reach']['distance_km']} km / {round(d['reach']['drive_hours'],1)} h (estimated road travel)")
        st.write(f"**Cost ${cc['total_usd']:,.0f}** = transport ${cc['breakdown']['transport_usd']:,.0f} "
                 f"+ stay ${cc['breakdown']['stay_usd']:,.0f} + reach-time ${cc['breakdown']['reach_time_cost_usd']:,.0f}")
        st.caption(f"Assumptions: ${cc['assumptions_used']['transport_per_km_usd']}/km · "
                   f"${cc['assumptions_used']['per_diem_usd']}/diem · ${cc['assumptions_used']['surgeon_day_value_usd']}/surgeon-day")

with tab_robust:
    coef = st.selectbox("Sweep cost assumption", list(T.COEFFICIENTS),
                        format_func=lambda k: T.COEFFICIENTS[k][2])
    s = T.sensitivity_analysis(intervention, coef, team_size, days)
    if s.get("error"):
        st.warning(s["error"])
    else:
        st.success(f"**{s['verdict']}**")
        import pandas as pd
        df = pd.DataFrame([{"value": p["value"], "need_per_$": p["top_metric"], "top": p["top_district"]}
                           for p in s["points"]]).set_index("value")
        st.line_chart(df[["need_per_$"]])
        st.caption(f"#1 pick at each swept value: " +
                   " · ".join(f"{p['value']}→{p['top_district']}" for p in s["points"]))

with tab_brief:
    bnames = [r["district"] for r in conf]
    if bnames:
        bpick = st.selectbox("District for brief", bnames, key="brief_pick")
        out = T.generate_brief(intervention, bpick, team_size, days)
        brief = out.get("brief", out.get("error", ""))
        st.code(brief, language="text")
        st.download_button("Download brief", brief, file_name=f"brief_{bpick}.txt")
    else:
        st.info("No confirmed-gap district to brief for this intervention.")

with tab_agent:
    st.caption("Free-form planning — the agent orchestrates the tools and explains. Needs the LLM key.")
    q = st.text_input("Ask the copilot",
                      placeholder="e.g. 6 volunteers, 7 days, maternal health from Patna — and is the top pick robust?")
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
            st.info("The dashboard above is fully functional without the LLM — it's all deterministic over Lakebase.")
