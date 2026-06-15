"""
data_access.py — THE single data-access module (architecture.md "Design rule").

Reads the same data from two backends with NO change to anything downstream:
  - Local dev / tests: the cached CSVs (data/cache/district_base.csv, reachability_patna.csv).
  - Databricks App: Lakebase Postgres (mission.district_base, mission.reachability) for sub-10ms reads.

Backend is chosen by environment: if PGHOST or LAKEBASE_ENDPOINT is set, use Lakebase; else CSV.
This is the only file that changes between "build locally" and "deploy on Lakebase".
"""

from __future__ import annotations

import csv
import json
import os
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

# --- demo configuration (single source of truth; build scripts import these) ---
STAGING = {"name": "Patna", "lat": 25.5941, "lon": 85.1376}   # Bihar
CANDIDATE_STATES = {"bihar", "jharkhand"}                      # the core credible-need cluster

_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
DISTRICT_BASE_CSV = _CACHE / "district_base.csv"
REACHABILITY_CSV = _CACHE / "reachability_patna.csv"
FACILITY_CLAIMS_CSV = _CACHE / "facility_claims.csv"
DISTRICT_CAPABILITY_CSV = _CACHE / "district_capability.csv"
_CAPABILITY_ALIAS = {"maternal_health": "maternity"}
_INT_COLS = {"facilities", "maternal_supply_facilities", "public", "private",
             "maternal_claim_high", "maternal_claim_medium", "maternal_claim_unverified",
             "maternal_verified_supply"}


def normalize_name(s: str) -> str:
    """Match a district name across sources (mirrors data/geo_resolve.normalize_name).
    Kept here so the app runtime needs no geo/shapely dependency."""
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[._]", " ", s)
    s = re.sub(r"\b(district|distt|dist|division|circle)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- backend select
def _lakebase_mode() -> bool:
    return bool(os.environ.get("PGHOST") or os.environ.get("LAKEBASE_ENDPOINT"))


_conn = None


def _credentials() -> tuple[str, str]:
    """(token, user). PGTOKEN env (local) wins; else mint via the SDK from LAKEBASE_ENDPOINT (app)."""
    if os.environ.get("PGTOKEN"):
        return os.environ["PGTOKEN"], os.environ.get("PGUSER", "")
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    token = w.postgres.generate_database_credential(endpoint=os.environ["LAKEBASE_ENDPOINT"]).token
    return token, os.environ.get("PGUSER") or w.current_user.me().user_name


def _get_conn():
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn
    import psycopg
    token, user = _credentials()
    _conn = psycopg.connect(
        host=os.environ["PGHOST"], port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ.get("PGDATABASE", "databricks_postgres"),
        user=user, password=token, sslmode=os.environ.get("PGSSLMODE", "require"),
        connect_timeout=30, autocommit=True)
    return _conn


def _query(sql: str) -> list[dict]:
    """Run a read query; reconnect once on a stale/expired connection (token TTL, scale-to-zero)."""
    global _conn
    import psycopg
    for attempt in (1, 2):
        try:
            with _get_conn().cursor() as cur:
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except (psycopg.OperationalError, psycopg.InterfaceError):
            _conn = None  # force reconnect (refreshes token) and retry once
            if attempt == 2:
                raise


# --------------------------------------------------------------------------- public API
def load_districts() -> list[dict]:
    """District base table (one row per NFHS-5 district): identity + supply + all NFHS indicators."""
    if _lakebase_mode():
        rows = _query("SELECT * FROM mission.district_base")
    else:
        if not DISTRICT_BASE_CSV.exists():
            raise FileNotFoundError(f"{DISTRICT_BASE_CSV} not found. Run: ./.venv/bin/python -m data.geo_resolve")
        with DISTRICT_BASE_CSV.open() as f:
            rows = list(csv.DictReader(f))
    for r in rows:
        for c in _INT_COLS:
            r[c] = int(r[c]) if r.get(c) not in (None, "") else 0
    return rows


def load_reachability() -> dict:
    """{normalized district key -> (distance_km, drive_hours)} from the staging city."""
    if _lakebase_mode():
        rows = _query("SELECT district_key, distance_km, duration_min FROM mission.reachability")
        return {r["district_key"]: (float(r["distance_km"]), float(r["duration_min"]) / 60.0) for r in rows}
    out = {}
    with REACHABILITY_CSV.open() as f:
        for r in csv.DictReader(f):
            out[r["district_key"]] = (float(r["distance_km"]), float(r["duration_min"]) / 60.0)
    return out


@lru_cache(maxsize=1)
def _all_facility_claims() -> tuple:
    """Per-facility×capability CLAIM rows (the cited evidence + provenance), dual-backend."""
    if _lakebase_mode():
        rows = _query("SELECT * FROM mission.facility_claims")
    else:
        if not FACILITY_CLAIMS_CSV.exists():
            return tuple()
        with FACILITY_CLAIMS_CSV.open() as f:
            rows = list(csv.DictReader(f))
    return tuple(rows)


_CLAIM_ORDER = {"high": 0, "medium": 1, "unverified": 2}


def load_facility_claims(district: str = None, capability: str = None,
                         verified_only: bool = False) -> list[dict]:
    """Cited facility claims, filterable by district (name) and capability, ordered
    high -> medium -> unverified. verified_only drops flag-only ('unverified') rows. Empty list if
    the claim table isn't built yet (run data/02_facility_text_ingest.py + data/geo_resolve.py)."""
    rows = list(_all_facility_claims())
    if district:
        key = normalize_name(district)
        rows = [r for r in rows if r.get("district_key") == key]
    if capability:
        cap = _CAPABILITY_ALIAS.get(capability, capability)
        rows = [r for r in rows if r.get("capability") == cap]
    if verified_only:
        rows = [r for r in rows if r.get("claim_confidence") in ("high", "medium")]
    rows.sort(key=lambda r: _CLAIM_ORDER.get(r.get("claim_confidence"), 9))
    return rows


@lru_cache(maxsize=1)
def _all_district_capability() -> tuple:
    """District×capability trust-weighted coverage aggregate (the primary coverage view), dual-backend."""
    if _lakebase_mode():
        rows = _query("SELECT * FROM mission.district_capability")
    else:
        if not DISTRICT_CAPABILITY_CSV.exists():
            return tuple()
        with DISTRICT_CAPABILITY_CSV.open() as f:
            rows = list(csv.DictReader(f))
    for r in rows:
        for c in ("high", "medium", "unverified", "verified_supply", "total_signal"):
            r[c] = int(r.get(c) or 0)
    return tuple(rows)


def load_district_capability(capability: str = None, state: str = None) -> list[dict]:
    """Coverage rows for a capability (and optionally one state). Accepts the 'maternal_health' alias."""
    rows = list(_all_district_capability())
    if capability:
        cap = _CAPABILITY_ALIAS.get(capability, capability)
        rows = [r for r in rows if r.get("capability") == cap]
    if state:
        s = state.strip().lower()
        rows = [r for r in rows if r.get("state_ut", "").strip().lower() == s]
    return rows


def list_states() -> list[str]:
    """States/UTs present in the coverage data (for the geography selector)."""
    return sorted({r["state_ut"].strip() for r in _all_district_capability() if r.get("state_ut", "").strip()})


@lru_cache(maxsize=1)
def make_reach_fn():
    """reach_fn(district_row) -> (distance_km, drive_hours) | None for mission_core.chain."""
    table = load_reachability()
    def reach_fn(district_row: dict):
        return table.get(normalize_name(district_row["nfhs_district"]))
    return reach_fn


# =========================================================================== #
# PERSISTENCE — user actions (the "persist their work" requirement).
#
# Same dual-backend discipline as the read side, ONE module:
#   - Databricks App: Lakebase Postgres, schema `mission_app` which the app SP OWNS
#     (it CREATEs it — CAN_CONNECT_AND_CREATE — so no cross-owner GRANT is needed, unlike
#     the read-only `mission` reference schema owned by the deploying user).
#   - Local dev / tests: a SQLite file (data/cache/app_state.db, gitignored/regenerable).
# Tables are self-provisioning (CREATE IF NOT EXISTS) so the deployed app needs no migration
# step. Ids are uuid4 hex (TEXT) and created_at is an ISO-8601 string stamped in Python, so
# the SQL is identical across both engines (no SERIAL vs AUTOINCREMENT / RETURNING divergence).
# =========================================================================== #

_APP_DB = _CACHE / "app_state.db"
_APP_SCHEMA = "mission_app"

# table name -> column DDL (without id/created_at, which every table shares).
_STORE_TABLES = {
    "scenarios": "name TEXT, inputs_json TEXT, snapshot_json TEXT",
    "reviews": "district_key TEXT, district TEXT, state TEXT, verdict TEXT, note TEXT",
    "shortlist": "district_key TEXT, district TEXT, state TEXT",
    "notes": "district_key TEXT, district TEXT, note_text TEXT",
}

_tables_ready = False


def _t(name: str) -> str:
    """Qualified table name: schema-qualified on Postgres, bare on SQLite."""
    return f"{_APP_SCHEMA}.{name}" if _lakebase_mode() else name


def _store_execute(sql: str, params: tuple = (), fetch: bool = False):
    """Run one write/read against the active store. SQL is written with '?' placeholders
    (SQLite dialect); translated to '%s' for Postgres. Reuses the autocommit Lakebase
    connection (reconnect-once on a stale token, mirroring _query)."""
    if _lakebase_mode():
        global _conn
        import psycopg
        pg_sql = sql.replace("?", "%s")
        for attempt in (1, 2):
            try:
                with _get_conn().cursor() as cur:
                    cur.execute(pg_sql, params)
                    if fetch:
                        cols = [d[0] for d in cur.description]
                        return [dict(zip(cols, r)) for r in cur.fetchall()]
                    return None
            except (psycopg.OperationalError, psycopg.InterfaceError):
                _conn = None
                if attempt == 2:
                    raise
    else:
        import sqlite3
        _APP_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(_APP_DB)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(sql, params)
            if fetch:
                return [dict(r) for r in cur.fetchall()]
            conn.commit()
            return None
        finally:
            conn.close()


def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    if _lakebase_mode():
        _store_execute(f"CREATE SCHEMA IF NOT EXISTS {_APP_SCHEMA}")
    for name, cols in _STORE_TABLES.items():
        _store_execute(
            f"CREATE TABLE IF NOT EXISTS {_t(name)} "
            f"(id TEXT PRIMARY KEY, {cols}, created_at TEXT)")
    _tables_ready = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def store_backend() -> str:
    """For surfacing in the UI: where user actions persist."""
    return "Lakebase (mission_app)" if _lakebase_mode() else f"SQLite ({_APP_DB.name})"


def store_probe() -> str:
    """Round-trip a throwaway row to prove the active store can WRITE+READ. Returns 'ok' or raises.
    Used by the app.yaml startup probe to verify the deployed SP's write path before judging."""
    _ensure_tables()
    pid = _new_id()
    _store_execute(f"INSERT INTO {_t('notes')} (id, district_key, district, note_text, created_at) "
                   f"VALUES (?, ?, ?, ?, ?)", (pid, "__probe__", "__probe__", "writeprobe", _now()))
    rows = _store_execute(f"SELECT id FROM {_t('notes')} WHERE id = ?", (pid,), fetch=True)
    _store_execute(f"DELETE FROM {_t('notes')} WHERE district_key = ?", ("__probe__",))
    return "ok" if rows else "empty"


# ---- scenarios (named input set + ranking snapshot) -----------------------
def save_scenario(name: str, inputs: dict, snapshot: dict) -> str:
    _ensure_tables()
    sid = _new_id()
    _store_execute(
        f"INSERT INTO {_t('scenarios')} (id, name, inputs_json, snapshot_json, created_at) "
        f"VALUES (?, ?, ?, ?, ?)",
        (sid, name, json.dumps(inputs), json.dumps(snapshot), _now()))
    return sid


def list_scenarios() -> list[dict]:
    _ensure_tables()
    rows = _store_execute(
        f"SELECT id, name, created_at FROM {_t('scenarios')} ORDER BY created_at DESC", fetch=True)
    return rows or []


def get_scenario(scenario_id: str) -> dict | None:
    _ensure_tables()
    rows = _store_execute(
        f"SELECT id, name, inputs_json, snapshot_json, created_at FROM {_t('scenarios')} "
        f"WHERE id = ?", (scenario_id,), fetch=True)
    if not rows:
        return None
    r = rows[0]
    return {"id": r["id"], "name": r["name"], "created_at": r["created_at"],
            "inputs": json.loads(r["inputs_json"]), "snapshot": json.loads(r["snapshot_json"])}


def delete_scenario(scenario_id: str) -> None:
    _ensure_tables()
    _store_execute(f"DELETE FROM {_t('scenarios')} WHERE id = ?", (scenario_id,))


# ---- review decisions (approve / reject / needs-investigation) ------------
def save_review(district_key: str, district: str, state: str, verdict: str, note: str = "") -> str:
    _ensure_tables()
    rid = _new_id()
    _store_execute(
        f"INSERT INTO {_t('reviews')} (id, district_key, district, state, verdict, note, created_at) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rid, district_key, district, state, verdict, note, _now()))
    return rid


def list_reviews() -> list[dict]:
    _ensure_tables()
    rows = _store_execute(
        f"SELECT id, district_key, district, state, verdict, note, created_at FROM {_t('reviews')} "
        f"ORDER BY created_at DESC", fetch=True)
    return rows or []


def latest_reviews() -> dict:
    """Most recent verdict per district_key (for showing the current decision in the UI)."""
    out: dict[str, dict] = {}
    for r in list_reviews():               # already newest-first; first seen per key wins
        out.setdefault(r["district_key"], r)
    return out


# ---- shortlist (pinned districts) -----------------------------------------
def add_to_shortlist(district_key: str, district: str, state: str) -> None:
    _ensure_tables()
    if any(s["district_key"] == district_key for s in list_shortlist()):
        return
    _store_execute(
        f"INSERT INTO {_t('shortlist')} (id, district_key, district, state, created_at) "
        f"VALUES (?, ?, ?, ?, ?)", (_new_id(), district_key, district, state, _now()))


def remove_from_shortlist(district_key: str) -> None:
    _ensure_tables()
    _store_execute(f"DELETE FROM {_t('shortlist')} WHERE district_key = ?", (district_key,))


def list_shortlist() -> list[dict]:
    _ensure_tables()
    rows = _store_execute(
        f"SELECT id, district_key, district, state, created_at FROM {_t('shortlist')} "
        f"ORDER BY created_at DESC", fetch=True)
    return rows or []


# ---- notes (free text per district) ---------------------------------------
def save_note(district_key: str, district: str, note_text: str) -> str:
    _ensure_tables()
    nid = _new_id()
    _store_execute(
        f"INSERT INTO {_t('notes')} (id, district_key, district, note_text, created_at) "
        f"VALUES (?, ?, ?, ?, ?)", (nid, district_key, district, note_text, _now()))
    return nid


def list_notes(district_key: str = None) -> list[dict]:
    _ensure_tables()
    if district_key:
        rows = _store_execute(
            f"SELECT id, district_key, district, note_text, created_at FROM {_t('notes')} "
            f"WHERE district_key = ? ORDER BY created_at DESC", (district_key,), fetch=True)
    else:
        rows = _store_execute(
            f"SELECT id, district_key, district, note_text, created_at FROM {_t('notes')} "
            f"ORDER BY created_at DESC", fetch=True)
    return rows or []
