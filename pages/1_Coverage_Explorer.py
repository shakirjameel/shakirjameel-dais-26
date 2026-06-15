"""
1_Coverage_Explorer.py — Track 2 (Medical Desert Planner) entry view.

Planner picks a CAPABILITY + GEOGRAPHY -> sees REGIONAL COVERAGE, with each district tiered
confirmed (measured gap) vs candidate (data-poor / unverified). The per-district aggregate is
the hand-off the facility drill-down (teammate) expands into trust-scored records.
"""

import streamlit as st

from mission_core.burden import INTERVENTION_INDICATORS
from mission_core.coverage_explorer import regional_coverage, list_states

st.set_page_config(page_title="Medical Desert Planner", page_icon="🗺️", layout="wide")

DBX_RED, DBX_INK, DBX_SAND = "#FF3621", "#0B2026", "#F9F7F4"
st.markdown(f"""
<style>
  .stApp {{ background:{DBX_SAND}; color:{DBX_INK}; }}
  .stApp, .stApp p, .stApp span, .stApp label, .stApp li,
  [data-testid="stMetricValue"] {{ color:{DBX_INK} !important; }}
  [data-testid="stMetricLabel"] p {{ color:#5b6770 !important; }}
  .hero, .hero * {{ color:#fff !important; }}
  .hero {{ background:{DBX_INK}; padding:16px 22px; border-radius:12px; margin-bottom:14px; }}
  .hero h1 {{ margin:0; font-size:1.4rem; }}
  .hero p {{ margin:.25rem 0 0; opacity:.85; font-size:.88rem; }}
  .tierbar {{ border-left:5px solid {DBX_RED}; padding:.2rem 0 .2rem .7rem; margin:.2rem 0 .6rem;
              font-weight:700; color:{DBX_INK}; }}
  .card {{ background:#fff; border:1px solid #e6e3dd; border-radius:10px; padding:12px 15px;
           margin-bottom:9px; }}
  .card b {{ color:{DBX_INK}; }}
  .bar {{ height:7px; border-radius:5px; background:#ece9e3; margin:.35rem 0 .2rem; }}
  .fill {{ height:7px; border-radius:5px; }}
  .pill {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:.7rem; font-weight:600; }}
  .pill-conf {{ background:#e8f5e9; color:#1b5e20; }}
  .pill-cand {{ background:#fff3e0; color:#e65100; }}
  .muted {{ color:#5b6770; font-size:.8rem; }}
</style>""", unsafe_allow_html=True)

st.markdown(
    '<div class="hero"><h1>🗺️ Medical Desert Planner</h1>'
    '<p>Where are the highest-risk gaps in care — and how confident are we they are real? '
    'Pick a capability and a geography to see regional coverage. Confirmed = measured under-supply; '
    'candidate = no facility data (real desert or data-poor).</p></div>',
    unsafe_allow_html=True)

# ----------------------------------------------------------------- controls
c1, c2 = st.columns([1, 1])
intervention = c1.selectbox("Capability", list(INTERVENTION_INDICATORS),
                            format_func=lambda s: s.replace("_", " ").title())
state = c2.selectbox("Geography (state / UT)", ["All India"] + list_states())
state_filter = None if state == "All India" else state

res = regional_coverage(intervention, state_filter)
s = res["summary"]

# ----------------------------------------------------------------- regional coverage summary
m1, m2, m3, m4 = st.columns(4)
m1.metric("Districts in scope", s["districts"])
m2.metric("Confirmed gaps", s["confirmed_gaps"])
m3.metric("Candidate (data-poor)", s["candidate_gaps"])
m4.metric("Mean burden", f"{s['mean_burden']:.2f}" if s["mean_burden"] is not None else "—")
st.caption(f"Scope **{s['scope']}** · {s['total_facilities']:,} facilities resolved · "
           f"data coverage {s['data_coverage_pct']*100:.0f}% of districts have facility data"
           if s["data_coverage_pct"] is not None else f"Scope {s['scope']}")
st.divider()


def _bar(value: float, color: str) -> str:
    pct = max(0, min(100, round((value or 0) * 100)))
    return f'<div class="bar"><div class="fill" style="width:{pct}%;background:{color}"></div></div>'


def _card(r: dict, idx: int, candidate: bool) -> str:
    pill = '<span class="pill pill-cand">candidate</span>' if candidate else '<span class="pill pill-conf">confirmed</span>'
    supply = ("NO facility data — verify on the ground" if candidate
              else f'relevant supply {r["relevant_supply"]} · {r["n_facilities"]} facilities')
    return (f'<div class="card"><b>#{idx} {r["district"]}, {r["state"]}</b> &nbsp; {pill}'
            f'{_bar(r["gap"], DBX_RED)}'
            f'<span class="muted">gap {r["gap"]} · burden {r["burden"]} ({r["burden_confidence"]}) · '
            f'{supply}<br>{r["confidence_label"]}</span></div>')


left, right = st.columns(2)
with left:
    st.markdown('<div class="tierbar">✅ CONFIRMED GAPS — measured under-supply; act on these</div>',
                unsafe_allow_html=True)
    if not res["confirmed"]:
        st.caption("No confirmed gaps in scope.")
    for i, r in enumerate(res["confirmed"][:25], 1):
        st.markdown(_card(r, i, candidate=False), unsafe_allow_html=True)

with right:
    st.markdown('<div class="tierbar">🔍 CANDIDATE GAPS — no facility data; investigate</div>',
                unsafe_allow_html=True)
    if not res["candidate"]:
        st.caption("No candidate (zero-data) districts in scope.")
    for i, r in enumerate(res["candidate"][:25], 1):
        st.markdown(_card(r, i, candidate=True), unsafe_allow_html=True)

# ----------------------------------------------------------------- drill-down hook (teammate fills facility records)
st.divider()
st.subheader("Drill into a district")
all_rows = res["confirmed"] + res["candidate"]
if all_rows:
    pick = st.selectbox("District", [f'{r["district"]}, {r["state"]}' for r in all_rows])
    chosen = all_rows[[f'{r["district"]}, {r["state"]}' for r in all_rows].index(pick)]
    d1, d2, d3 = st.columns(3)
    d1.metric("Coverage gap", chosen["gap"])
    d2.metric("Relevant supply", chosen["relevant_supply"])
    d3.metric("Burden", f'{chosen["burden"]} ({chosen["burden_confidence"]})')
    st.caption(f'Status: **{chosen["coverage_status"]}** — {chosen["confidence_label"]}. '
               f'Burden uses {chosen["indicators_used"]}/{chosen["indicators_total"]} NFHS-5 indicators'
               + (f' · missing: {", ".join(chosen["missing_indicators"])}' if chosen["missing_indicators"] else ""))
    st.info("⬇️ Facility-level drill-down (trust-scored records behind this aggregate) — wired in by the "
            "context-graph / trust workstream. This `district_coverage` dict is the hand-off contract.")
