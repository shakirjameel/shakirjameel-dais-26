"""
genie.py — thin wrapper over the Databricks **Genie** Conversations API for the app's
"Ask the data" tab.

Genie is Databricks' native text-to-SQL: it answers natural-language questions over Unity Catalog
tables by generating + running SQL on a SQL warehouse. Our ingest Job publishes the curated tables to
`workspace.mission_uc.*`; a Genie space sits over those (plus the raw VF source tables), and this module
lets the Streamlit app converse with that space as the app service principal.

Design:
  - `configured()` — is a space wired up (GENIE_SPACE_ID set)? The tab degrades gracefully if not.
  - `ask(question)` — one turn. Returns a plain dict {text, sql, columns, rows, error}; the caller
    renders it. NEVER raises — Free-Edition Genie is rate-limited (~5 q/min) and the warehouse may be
    cold, so every failure is captured into `error` so the rest of the app is unaffected.

The response shape varies across SDK versions, so parsing is deliberately defensive (getattr / dict
fallbacks) — we extract whatever of {answer text, generated SQL, result rows} is present.
"""

from __future__ import annotations

import os


def space_id() -> str:
    return os.environ.get("GENIE_SPACE_ID", "").strip()


def configured() -> bool:
    return bool(space_id())


def _client():
    """A WorkspaceClient authenticated as the app SP (same pattern as data_access). truststore makes
    TLS work behind an intercepting proxy locally; it's a no-op in the deployed app."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def _attr(obj, *names):
    """First present attribute (or dict key) among names, else None — tolerates SDK shape drift."""
    for n in names:
        v = getattr(obj, n, None)
        if v is None and isinstance(obj, dict):
            v = obj.get(n)
        if v is not None:
            return v
    return None


def _extract_sql(att) -> str | None:
    q = _attr(att, "query")
    if q is None:
        return None
    return _attr(q, "query", "statement", "sql")


def _result_rows(w, sid, conv_id, msg_id, att) -> tuple[list, list]:
    """(columns, rows) for a query attachment, via get_message_attachment_query_result. Returns
    ([], []) if anything is missing — never raises."""
    att_id = _attr(att, "attachment_id", "id")
    if not att_id:
        return [], []
    try:
        res = w.genie.get_message_attachment_query_result(sid, conv_id, msg_id, att_id)
    except Exception:
        return [], []
    sr = _attr(res, "statement_response")
    if sr is None:
        return [], []
    manifest = _attr(sr, "manifest")
    schema = _attr(manifest, "schema") if manifest else None
    cols = [c.name for c in (_attr(schema, "columns") or [])] if schema else []
    result = _attr(sr, "result")
    rows = (_attr(result, "data_array") or []) if result else []
    return cols, rows


def ask(question: str) -> dict:
    """One Genie turn. Returns {text, sql, columns, rows, error}. Never raises."""
    sid = space_id()
    if not sid:
        return {"error": "Genie is not configured (GENIE_SPACE_ID is unset).",
                "text": None, "sql": None, "columns": [], "rows": []}
    try:
        w = _client()
        msg = w.genie.start_conversation_and_wait(sid, question)
        conv_id = _attr(msg, "conversation_id")
        msg_id = _attr(msg, "id", "message_id")
        text, sql, columns, rows = None, None, [], []
        for att in (_attr(msg, "attachments") or []):
            t = _attr(att, "text")
            if t is not None:
                text = _attr(t, "content") or text
            s = _extract_sql(att)
            if s:
                sql = s
                columns, rows = _result_rows(w, sid, conv_id, msg_id, att)
        # Genie sometimes puts a plain answer on the message content itself.
        text = text or _attr(msg, "content")
        return {"text": text, "sql": sql, "columns": columns, "rows": rows, "error": None}
    except Exception as e:
        msg = str(e)
        if "429" in msg or "rate" in msg.lower() or "quota" in msg.lower():
            msg = "Genie is rate-limited on Free Edition (~5 questions/min). Wait a moment and retry."
        return {"error": msg, "text": None, "sql": None, "columns": [], "rows": []}
