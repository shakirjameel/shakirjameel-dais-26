"""
Tests for the Rung 1 agent. The orchestrator loop is tested with a MOCK llm (scripted tool
calls — no network). The tool layer is tested against the real cached data (district_base +
reachability), which also confirms tools return grounded numbers the agent can only narrate.

Run: ./.venv/bin/python tests/test_agent.py   (or: ./.venv/bin/python -m pytest tests/test_agent.py)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import tools as T
from agent.orchestrator import run


# ---------- mock LLM plumbing (mimics the OpenAI response shape) ----------
class _Fn:
    def __init__(self, name, args): self.name = name; self.arguments = json.dumps(args)
class _ToolCall:
    def __init__(self, id, name, args): self.id = id; self.function = _Fn(name, args)
class _Msg:
    def __init__(self, content=None, tool_calls=None): self.content = content; self.tool_calls = tool_calls
    def model_dump(self, exclude_none=True): return {"role": "assistant", "content": self.content}
class _Choice:
    def __init__(self, msg): self.message = msg
class _Resp:
    def __init__(self, msg): self.choices = [_Choice(msg)]

def scripted(*responses):
    """Return an llm(messages, tools, max_tokens) that yields the given responses in order."""
    it = iter(responses)
    def _llm(messages, tools=None, max_tokens=4096, **kw):
        return _Resp(next(it))
    return _llm


# ---------- tool layer (real data) ----------
def test_list_interventions_includes_maternal():
    out = T.list_interventions()
    names = [i["intervention"] for i in out["interventions"]]
    assert "maternal_health" in names
    assert out["staging_city"] == "Patna"

def test_rank_districts_returns_two_tiers_with_grounded_numbers():
    out = T.rank_districts_tool("maternal_health", top_n=5)
    assert "confirmed_gaps" in out and "candidate_gaps" in out
    assert out["confirmed_gaps"], "expected at least one confirmed gap"
    top = out["confirmed_gaps"][0]
    # grounded fields the agent can only narrate, not invent
    for k in ("district", "burden_score", "gap", "cost_total_usd", "need_per_dollar", "tier"):
        assert k in top
    assert top["tier"] == "confirmed_gap"
    assert out["candidate_gaps"][0]["tier"] == "candidate_gap"

def test_rank_districts_rejects_unknown_intervention():
    out = T.rank_districts_tool("dentistry")
    assert "error" in out and "valid" in out

def test_get_district_detail_has_cost_breakdown_and_provenance():
    ranked = T.rank_districts_tool("maternal_health", top_n=1)
    name = ranked["confirmed_gaps"][0]["district"]
    detail = T.get_district_detail("maternal_health", name)
    assert detail["cost"]["breakdown"]   # itemized, not a bare total
    assert detail["cost"]["assumptions_used"]  # named assumptions = provenance
    assert "score" in detail["burden"]

def test_dispatch_unknown_tool_is_structured_error():
    assert "error" in T.dispatch("nonexistent_tool", {})

def test_sensitivity_analysis_reports_robustness():
    out = T.sensitivity_analysis("maternal_health", "surgeon_day_value_usd")
    assert out["baseline_top"]                 # a concrete #1 district
    assert "verdict" in out and out["points"]  # swept points + a plain-language verdict
    # every swept value records the top district at that coefficient value
    assert all("top_district" in p for p in out["points"])

def test_generate_brief_is_cited():
    name = T.rank_districts_tool("maternal_health", top_n=1)["confirmed_gaps"][0]["district"]
    out = T.generate_brief("maternal_health", name)
    brief = out["brief"]
    assert "MISSION BRIEF" in brief
    assert "assumptions:" in brief             # cost provenance
    assert "FLAGGED UNCERTAINTIES" in brief    # honesty section present


# ---------- orchestrator loop (mock llm) ----------
def test_loop_executes_tool_then_returns_final_text():
    llm = scripted(
        _Msg(tool_calls=[_ToolCall("c1", "rank_districts", {"intervention": "maternal_health"})]),
        _Msg(content="Top confirmed pick: Sitamarhi. Candidate gaps flagged separately."),
    )
    res = run("maternal health, 6 for 7 days, from Patna", llm=llm)
    assert res.iterations == 2
    assert len(res.tool_trace) == 1
    step = res.tool_trace[0]
    assert step["tool"] == "rank_districts"
    # the tool actually ran against real data — grounded result, not fabricated
    assert "confirmed_gaps" in step["result"]
    assert "Sitamarhi" in res.final_text

def test_clarifying_question_path_no_tools():
    # Model asks a question instead of calling a tool -> returned immediately, no tool calls.
    llm = scripted(_Msg(content="Which intervention — maternal health, anaemia, or child nutrition?"))
    res = run("help me plan a mission", llm=llm)
    assert res.iterations == 1
    assert res.tool_trace == []
    assert "?" in res.final_text


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}"); passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}  {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}  {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
