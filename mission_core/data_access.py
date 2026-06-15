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
import os
import re
from functools import lru_cache
from pathlib import Path

# --- demo configuration (single source of truth; build scripts import these) ---
STAGING = {"name": "Patna", "lat": 25.5941, "lon": 85.1376}   # Bihar
CANDIDATE_STATES = {"bihar", "jharkhand"}                      # the core credible-need cluster

_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
DISTRICT_BASE_CSV = _CACHE / "district_base.csv"
REACHABILITY_CSV = _CACHE / "reachability_patna.csv"
_INT_COLS = {"facilities", "maternal_supply_facilities", "public", "private"}


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
def make_reach_fn():
    """reach_fn(district_row) -> (distance_km, drive_hours) | None for mission_core.chain."""
    table = load_reachability()
    def reach_fn(district_row: dict):
        return table.get(normalize_name(district_row["nfhs_district"]))
    return reach_fn
