"""
Tests for the Genie "Ask the data" wrapper (agent/genie.py). The Databricks SDK is MOCKED — no
network. Verifies the defensive response parsing (text + generated SQL + result rows), the
unconfigured path, and graceful error handling (incl. rate-limit messaging).

Run: ./.venv/bin/python tests/test_genie.py   (or: ./.venv/bin/python -m pytest tests/test_genie.py)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import genie


# ---------- fakes mimicking the Genie SDK response shapes ----------
class _Text:
    def __init__(self, content): self.content = content
class _Query:
    def __init__(self, query): self.query = query
class _Att:
    def __init__(self, attachment_id=None, text=None, query=None):
        self.attachment_id = attachment_id; self.text = text; self.query = query
class _Msg:
    def __init__(self, conversation_id, id, attachments, content=None):
        self.conversation_id = conversation_id; self.id = id
        self.attachments = attachments; self.content = content
class _Schema:
    def __init__(self, names): self.columns = [type("C", (), {"name": n}) for n in names]
class _Manifest:
    def __init__(self, names): self.schema = _Schema(names)
class _Result:
    def __init__(self, rows): self.data_array = rows
class _StmtResp:
    def __init__(self, names, rows): self.manifest = _Manifest(names); self.result = _Result(rows)
class _QueryResult:
    def __init__(self, names, rows): self.statement_response = _StmtResp(names, rows)

class _FakeGenie:
    def __init__(self, msg, qresult=None, raise_on_start=None):
        self._msg = msg; self._qresult = qresult; self._raise = raise_on_start
    def start_conversation_and_wait(self, sid, content):
        if self._raise:
            raise self._raise
        return self._msg
    def get_message_attachment_query_result(self, sid, conv, mid, aid):
        return self._qresult

class _FakeW:
    def __init__(self, genie_api): self.genie = genie_api


def _patch(monkeypatch_w):
    genie._client = lambda: monkeypatch_w  # noqa: E731  (test seam)


# ---------- tests ----------
def test_unconfigured_returns_error_not_exception():
    os.environ.pop("GENIE_SPACE_ID", None)
    assert genie.configured() is False
    r = genie.ask("anything")
    assert r["error"] and "not configured" in r["error"].lower()
    assert r["rows"] == [] and r["sql"] is None


def test_parses_text_sql_and_rows():
    os.environ["GENIE_SPACE_ID"] = "space123"
    msg = _Msg("conv1", "msg1", attachments=[
        _Att(text=_Text("The top maternity deserts are in Nagaland.")),
        _Att(attachment_id="att1", query=_Query(
            "SELECT district FROM workspace.mission_uc.district_coverage ORDER BY desert_score DESC")),
    ])
    qres = _QueryResult(["district", "state"], [["Mon", "Nagaland"], ["Tuensang", "Nagaland"]])
    _patch(_FakeW(_FakeGenie(msg, qres)))
    r = genie.ask("highest maternity desert?")
    assert r["error"] is None
    assert "Nagaland" in r["text"]
    assert r["sql"].startswith("SELECT district")
    assert r["columns"] == ["district", "state"]
    assert r["rows"][0] == ["Mon", "Nagaland"]


def test_text_only_answer_no_query():
    os.environ["GENIE_SPACE_ID"] = "space123"
    msg = _Msg("c", "m", attachments=[_Att(text=_Text("There are 695 districts."))])
    _patch(_FakeW(_FakeGenie(msg)))
    r = genie.ask("how many districts?")
    assert r["error"] is None and "695" in r["text"]
    assert r["sql"] is None and r["rows"] == []


def test_rate_limit_is_friendly():
    os.environ["GENIE_SPACE_ID"] = "space123"
    _patch(_FakeW(_FakeGenie(None, raise_on_start=Exception("HTTP 429 Too Many Requests"))))
    r = genie.ask("q")
    assert r["error"] and "rate-limited" in r["error"].lower()


def test_ask_genie_tool_dispatches_and_returns_structured():
    # The merged copilot calls Genie as a tool — verify tools.dispatch routes it + shapes the result.
    from agent import tools as T
    os.environ["GENIE_SPACE_ID"] = "space123"
    msg = _Msg("c", "m", attachments=[
        _Att(text=_Text("There are 258 facilities in Bihar.")),
        _Att(attachment_id="a1", query=_Query("SELECT COUNT(*) FROM facilities WHERE state='Bihar'"))])
    qres = _QueryResult(["n"], [["258"]])
    _patch(_FakeW(_FakeGenie(msg, qres)))
    out = T.dispatch("ask_genie", {"question": "how many facilities in Bihar?"})
    assert out.get("error") is None
    assert "258" in (out.get("answer") or "")
    assert out["sql"].startswith("SELECT COUNT(*)")
    assert out["rows"][0] == ["258"]


def test_ask_genie_tool_graceful_when_unconfigured():
    from agent import tools as T
    os.environ.pop("GENIE_SPACE_ID", None)
    out = T.dispatch("ask_genie", {"question": "x"})
    assert "error" in out and "not configured" in out["error"].lower()


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
