"""
app.py — Medical Desert Planner (Databricks App, Streamlit) — Track 2.

A non-technical planner picks a CAPABILITY (maternity, ICU, NICU, emergency, oncology, trauma) and
a GEOGRAPHY (state), and sees TRUST-WEIGHTED coverage by district — distinguishing real care gaps
from data-poor regions. Drilling into a district shows the underlying facility records (name, the
facility's own claimed text, a source link, a claim-confidence). The planner saves scenarios, pins a
shortlist, records review decisions and notes — all persisted (Lakebase / local SQLite).

The cost-per-impact "Deployment optimizer" (maternal, from Patna) is a deep-dive tab. Everything
deterministic works with NO LLM; the agent tab degrades gracefully if the model is unreachable.
"""

import os
import pandas as pd
import streamlit as st

from mission_core.data_access import STAGING
from mission_core import data_access as da
from mission_core.claims import CAPABILITY_LABELS, CAPABILITIES
from mission_core.coverage_view import coverage_by_geography, coverage_summary
from agent import tools as T

VERDICTS = ["approve", "reject", "needs-investigation"]
GAP_BADGE = {
    "confirmed_coverage": ("pill-hi", "✅ confirmed coverage"),
    "unverified_claims": ("pill-med", "⚠ unverified claims"),
    "no_claim_desert": ("pill-lo", "🟥 no-claim desert"),
}


def _key(capability: str, district: str) -> str:
    """Capability-scoped persistence key, so triage of 'Saran for NICU' ≠ 'Saran for maternity'."""
    return f"{capability}:{da.normalize_name(district)}"


def _cap_of(key: str) -> str:
    return key.split(":", 1)[0] if ":" in key else ""


# ----------------------------------------------------------------------------- page + theme
st.set_page_config(page_title="Medical Desert Planner", page_icon="🩺", layout="wide")
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
  a {{ color:{DBX_RED}; }}
</style>""", unsafe_allow_html=True)

st.markdown(
    '<div class="hero"><h1>🩺 Medical Desert Planner</h1>'
    '<p>Pick a capability and a state to see <b>trust-weighted</b> coverage by district — telling real '
    'care gaps apart from data-poor regions. Every facility claim is graded against its own text and '
    '<b>cited with a source</b>; your shortlist, notes, reviews and scenarios are saved. '
    'Virtue Foundation facility data (DAIS 2026).</p></div>', unsafe_allow_html=True)


def _pill(cls: str, text: str) -> str:
    return f'<span class="pill {cls}">{text}</span>'


def _conf_pill(conf: str) -> str:
    cls = "pill-hi" if conf == "high" else "pill-med" if conf == "medium" else "pill-lo"
    return _pill(cls, conf)


# A saved scenario is applied BEFORE the widgets are instantiated (Streamlit forbids mutating a
# widget's state after creation), so "Load" stashes it here and reruns; we apply it up top.
if "_pending_scenario" in st.session_state:
    _inp = st.session_state.pop("_pending_scenario")["inputs"]
    st.session_state["capability"] = _inp.get("capability", "maternity")
    st.session_state["state"] = _inp.get("state", "Bihar")
    st.session_state["count_unverified"] = bool(_inp.get("count_unverified", False))
    st.session_state["team_size"] = int(_inp.get("team_size", 6))
    st.session_state["days"] = int(_inp.get("days", 7))

# ----------------------------------------------------------------------------- sidebar
_STATES = da.list_states() or ["Bihar"]
_def_state = st.session_state.get("state", "Bihar" if "Bihar" in _STATES else _STATES[0])
with st.sidebar:
    st.subheader("What & where")
    capability = st.selectbox("Capability", CAPABILITIES,
                              index=CAPABILITIES.index(st.session_state.get("capability", "maternity")),
                              format_func=lambda c: CAPABILITY_LABELS.get(c, c), key="capability")
    state = st.selectbox("State / UT", _STATES, index=_STATES.index(_def_state), key="state")
    count_unverified = st.toggle("Count unverified claims as supply?",
                                 value=st.session_state.get("count_unverified", False),
                                 key="count_unverified",
                                 help="Off (default): only text-verified facilities count as supply — "
                                      "the honest, trust-weighted view. On: unverified claims count "
                                      "at a discount, shrinking apparent gaps.")
    st.caption("Deployment optimizer (maternal) settings:")
    team_size = st.slider("Team size", 2, 20, st.session_state.get("team_size", 6), key="team_size")
    days = st.slider("Mission days", 1, 21, st.session_state.get("days", 7), key="days")
    backend = "Lakebase" if (os.environ.get("PGHOST") or os.environ.get("LAKEBASE_ENDPOINT")) else "local CSV"
    st.caption(f"Data backend: **{backend}**")

    # --- saved scenarios (persisted work) -------------------------------------
    st.divider()
    st.subheader("💾 Scenarios")
    try:
        _scenarios = da.list_scenarios()
    except Exception as e:                       # persistence store unreachable — degrade, don't crash
        _scenarios = None
        st.caption(f"⚠️ scenario store unavailable: {e}")
    if _scenarios is not None:
        _name = st.text_input("Name this scenario",
                              placeholder=f"{capability} · {state}", key="scenario_name")
        if st.button("Save current scenario", width="stretch"):
            label = _name.strip() or f"{CAPABILITY_LABELS.get(capability, capability)} · {state}"
            rows = coverage_by_geography(capability, state, count_unverified, top_n=15)
            da.save_scenario(label, {"capability": capability, "state": state,
                                     "count_unverified": count_unverified,
                                     "team_size": team_size, "days": days},
                             {"summary": coverage_summary(rows), "top": rows[:10]})
            st.success(f"Saved “{label}”"); st.rerun()
        if _scenarios:
            opts = {f"{s['name']}  ·  {s['created_at'][:16]}": s["id"] for s in _scenarios}
            choice = st.selectbox("Load a saved scenario", list(opts), key="scenario_load_pick")
            cload, cdel = st.columns(2)
            if cload.button("Load", width="stretch"):
                sc = da.get_scenario(opts[choice])
                if sc:
                    st.session_state["_pending_scenario"] = sc; st.rerun()
            if cdel.button("Delete", width="stretch"):
                da.delete_scenario(opts[choice]); st.rerun()
        st.caption(f"Persisted to: **{da.store_backend()}**")

# ============================================================================= PRIMARY: coverage
cap_label = CAPABILITY_LABELS.get(capability, capability)
rows = coverage_by_geography(capability, state, count_unverified)
if not rows:
    st.warning(f"No coverage data for {cap_label} in {state}.")
    st.stop()
summary = coverage_summary(rows)

st.markdown(f'<div class="tierbar">Coverage &amp; trust — <b>{cap_label}</b> across <b>{state}</b> '
            f'({summary["districts"]} districts)</div>', unsafe_allow_html=True)
m1, m2, m3, m4 = st.columns(4)
m1.metric("✅ Confirmed coverage", summary["confirmed_coverage"])
m2.metric("⚠ Unverified-claim only", summary["unverified_claims"])
m3.metric("🟥 No-claim deserts", summary["no_claim_desert"])
m4.metric("Text-verified facilities", summary["verified_facilities"])

has_burden = rows[0]["has_burden"]
table = pd.DataFrame([{
    "rank": r["rank"], "district": r["district"],
    "gap": GAP_BADGE[r["gap_classification"]][1],
    "verified": r["verified_supply"], "unverified": r["unverified"],
    "trust_ratio": r["trust_ratio"], "facilities": r["total_facilities"],
    **({"burden": r["burden"]} if has_burden else {}),
    "desert_score": r["desert_score"],
} for r in rows])
st.dataframe(table, width="stretch", hide_index=True, height=330)
st.caption(("Desert score = burden × (1 − trust-weighted adequacy). " if has_burden else
            "No NFHS burden indicator for this capability — desert score = supply scarcity only. ") +
           "‘Trust-weighted’: corroborated claims count fully, claimed-only at 0.6, flag-only at 0 "
           f"(or 0.3 with the toggle{' — currently ON' if count_unverified else ''}).")

# ----------------------------------------------------------------------------- drill into a district
st.divider()
st.markdown("#### Drill into a district — the facility records behind the aggregate")
pick = st.selectbox("District", [r["district"] for r in rows], key="cov_pick")
row = next(r for r in rows if r["district"] == pick)
cls_cls, cls_txt = GAP_BADGE[row["gap_classification"]]
st.markdown(
    f'**{pick}, {state}** &nbsp; {_pill(cls_cls, cls_txt)}<br>'
    f'<span class="muted">text-verified supply <b>{row["verified_supply"]}</b> '
    f'(high {row["high"]}, medium {row["medium"]}) · unverified {row["unverified"]} · '
    f'trust-weighted {row["trust_weighted_supply"]} · '
    + (f'burden {row["burden"]} ({row["burden_confidence"]}) · ' if has_burden else "")
    + f'desert score {row["desert_score"]}</span>', unsafe_allow_html=True)

claims = da.load_facility_claims(pick, capability)
if claims:
    st.markdown(f"**Facility evidence** — capability is a *claim to verify, not ground truth*. "
                f"{len(claims)} facility record(s):")
    for c in claims[:12]:
        link = f' · <a href="{c["source_url"]}" target="_blank">source</a>' if c.get("source_url") else ""
        cap_ev = c.get("capability_evidence") or ""
        proc_ev = c.get("procedure_evidence") or ""
        st.markdown(
            f'<div class="card">{_conf_pill(c["claim_confidence"])} &nbsp;<b>{c.get("name") or "(unnamed)"}</b>'
            f'<span class="muted"> · {c.get("city") or ""} · {c.get("operator") or ""}{link}</span>'
            + (f'<br><b>claims:</b> “{cap_ev}”' if cap_ev else
               '<br><b>claims:</b> <i>flag/specialty asserts it, but the facility’s own text doesn’t — unverified</i>')
            + (f'<br><b>corroborated by:</b> “{proc_ev}”' if proc_ev else
               ('<br><span class="muted">not corroborated by procedure/equipment text</span>' if cap_ev else ''))
            + '</div>', unsafe_allow_html=True)
    st.caption("Each line is the facility's own extracted text — cited with its source link, not paraphrased.")
else:
    st.info("No facility even claims this capability here — a candidate care desert (or a data gap).")

# ----------------------------------------------------------------------------- persisted actions (capability-scoped)
st.divider()
st.markdown("**Your decisions** — saved to the workspace (scoped to this capability)")
pkey = _key(capability, pick)
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
        else:
            if st.button("📌 Pin to shortlist", key="pin", width="stretch"):
                da.add_to_shortlist(pkey, label, state); st.rerun()
    with crev:
        cur = reviewed["verdict"] if reviewed else "(none)"
        vchoice = st.radio(f"Review decision (current: **{cur}**)", VERDICTS, horizontal=True,
                           key="verdict_pick", index=VERDICTS.index(reviewed["verdict"]) if reviewed else 2)
        rnote = st.text_input("Reason (optional)", key="verdict_note", value=reviewed["note"] if reviewed else "")
        if st.button("Record decision", key="record_verdict"):
            da.save_review(pkey, label, state, vchoice, rnote); st.success(f"Recorded: {vchoice}"); st.rerun()
    note_text = st.text_area("Add a note", key="note_text",
                             placeholder=f"e.g. call DH re: {cap_label} capacity")
    if st.button("Save note", key="save_note") and note_text.strip():
        da.save_note(pkey, label, note_text.strip()); st.rerun()
    for n in existing_notes:
        st.caption(f"📝 {n['created_at'][:16]} — {n['note_text']}")

# ============================================================================= deep-dive tabs
st.divider()
tab_opt, tab_workspace, tab_agent = st.tabs(
    ["🚑 Deployment optimizer (maternal · Patna)", "🗂 My workspace", "💬 Ask the copilot"])

with tab_opt:
    st.caption("Cost-per-impact ranking: which district does a volunteer team do the most good per "
               "dollar from Patna — burden × trust-weighted gap ÷ mission cost. Available for "
               "**maternity** (the capability with NFHS burden + road-reachability data).")
    res = T.rank_districts_tool("maternal_health", team_size=team_size, days=days, top_n=6,
                                count_unverified=count_unverified)
    conf, cand = res["confirmed_gaps"], res["candidate_gaps"]
    if conf:
        top = conf[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Top confirmed pick", f"{top['district']}, {top['state']}")
        c2.metric("Need addressed / $", f"{top['need_per_dollar']:.2e}")
        c3.metric("Est. mission cost", f"${top['cost_total_usd']:,.0f}")
    left, right = st.columns(2)
    with left:
        st.markdown('<div class="tierbar">✅ CONFIRMED GAPS — measured facility data</div>', unsafe_allow_html=True)
        for r in conf:
            st.markdown(
                f'<div class="card"><b>#{r["rank"]} {r["district"]}, {r["state"]}</b><br>'
                f'<span class="muted">burden {r["burden_score"]} · gap {r["gap"]} · verified supply '
                f'{r["verified_maternal_supply"]} · {r["drive_hours"]}h / {r["distance_km"]}km · '
                f'${r["cost_total_usd"]:,.0f} · need/$ {r["need_per_dollar"]:.2e}</span></div>',
                unsafe_allow_html=True)
    with right:
        st.markdown('<div class="tierbar">🔍 CANDIDATE GAPS — no facility data; investigate</div>', unsafe_allow_html=True)
        for r in cand:
            st.markdown(
                f'<div class="card"><b>#{r["rank"]} {r["district"]}, {r["state"]}</b><br>'
                f'<span class="muted">burden {r["burden_score"]} · gap {r["gap"]} · '
                f'{r["drive_hours"]}h / {r["distance_km"]}km · NO facility data — verify on the ground</span></div>',
                unsafe_allow_html=True)
    st.divider()
    cr, cb = st.columns(2)
    with cr:
        st.markdown("**📈 Robustness** — does the #1 pick survive a cost-assumption sweep?")
        coef = st.selectbox("Sweep", list(T.COEFFICIENTS), format_func=lambda k: T.COEFFICIENTS[k][2])
        s = T.sensitivity_analysis("maternal_health", coef, team_size, days)
        if s.get("error"):
            st.warning(s["error"])
        else:
            st.success(f"**{s['verdict']}**")
            df = pd.DataFrame([{"value": p["value"], "need_per_$": p["top_metric"]} for p in s["points"]]).set_index("value")
            st.line_chart(df)
    with cb:
        st.markdown("**📄 Mission brief** — cited one-pager for a confirmed pick")
        bnames = [r["district"] for r in conf]
        if bnames:
            bpick = st.selectbox("District for brief", bnames, key="brief_pick")
            out = T.generate_brief("maternal_health", bpick, team_size, days)
            brief = out.get("brief", out.get("error", ""))
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
        st.markdown("**📌 Shortlist**")
        for s in sl:
            ca, cb = st.columns([4, 1])
            ca.write(f"{s['district']}, {s['state']}")
            if cb.button("✕", key=f"sl_{s['id']}"):
                da.remove_from_shortlist(s["district_key"]); st.rerun()
        if not sl:
            st.caption("No districts pinned yet.")
        st.markdown("**✅ Review decisions**")
        for r in rv:
            emoji = {"approve": "✅", "reject": "🚫"}.get(r["verdict"], "🔍")
            st.caption(f"{emoji} **{r['verdict']}** — {r['district']}"
                       + (f" · {r['note']}" if r["note"] else "") + f"  ·  {r['created_at'][:16]}")
        if not rv:
            st.caption("No decisions recorded yet.")
    with w2:
        st.markdown("**📝 Notes**")
        for n in nt:
            st.caption(f"{n['district']} · {n['created_at'][:16]} — {n['note_text']}")
        if not nt:
            st.caption("No notes yet.")
        st.markdown("**💾 Saved scenarios**")
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
            st.info("The coverage view + deployment optimizer above are fully deterministic — they work without the LLM.")
