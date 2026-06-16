"""
load_lakebase.py — load the app's serving tables into Lakebase Postgres.

Loads the derived district_base (point-in-polygon resolved burden + supply) and the cached
reachability table into a `mission` schema. The Databricks App reads these for sub-10ms lookups.

Connection comes from env (set by the wrapper that mints a Lakebase token via the CLI):
    PGHOST, PGTOKEN, PGUSER (default = the project owner email), PGDATABASE (default databricks_postgres)

RUN
    export PATH="$HOME/bin:$PATH"
    EP=projects/mission-copilot/branches/production/endpoints/primary
    export PGHOST=$(databricks postgres get-endpoint $EP -o json | python3 -c "import json,sys;print(json.load(sys.stdin)['status']['hosts']['host'])")
    export PGTOKEN=$(databricks postgres generate-database-credential $EP -o json | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
    export PGUSER=shakirjameel17@gmail.com
    ./.venv/bin/python -m data.load_lakebase
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import psycopg

from mission_core.data_access import _STORE_TABLES, _APP_SCHEMA  # the persistence (write) schema/tables

CACHE = Path(__file__).resolve().parent / "cache"

# The deployed app's service principal (Postgres role). load_lakebase runs as the OWNER, so it
# provisions the write schema + grants the SP — otherwise mission_app ends up owned by whoever
# created it first and the SP gets "permission denied for schema mission_app" at runtime.
APP_SP = os.environ.get("APP_SP_CLIENT_ID", "355b9275-0af4-4e57-b04c-f60cca3d9311")

# district_base now carries ALL 109 NFHS indicators (so demand for any capability is available) +
# supply/claim aggregates. Schema is built DYNAMICALLY from the CSV header: the counts below are INT,
# everything else (every NFHS indicator) stays TEXT to preserve '*' (suppressed) / '(x)' markers.
_DISTRICT_INT = {"facilities", "maternal_supply_facilities", "public", "private",
                 "maternal_claim_high", "maternal_claim_medium", "maternal_claim_unverified",
                 "maternal_verified_supply"}


def _district_cols_from_header(csv_path: Path) -> list[tuple[str, str]]:
    with csv_path.open() as f:
        header = next(csv.reader(f))
    return [(c, "INT" if c in _DISTRICT_INT else "TEXT") for c in header]


REACH_COLS = [
    ("district_key", "TEXT"), ("district", "TEXT"), ("state", "TEXT"),
    ("distance_km", "DOUBLE PRECISION"), ("duration_min", "DOUBLE PRECISION"), ("source", "TEXT"),
]
# Per-facility×capability claims (long) — the underlying facility TEXT + provenance + placement/contact.
FACILITY_CLAIMS_COLS = [
    ("unique_id", "TEXT"), ("name", "TEXT"), ("city", "TEXT"), ("pincode", "TEXT"),
    ("source_url", "TEXT"), ("operator", "TEXT"),
    ("capacity_beds", "TEXT"), ("number_doctors", "TEXT"), ("accepts_volunteers", "INT"),
    ("organization_type", "TEXT"), ("facility_type", "TEXT"), ("phone", "TEXT"), ("website", "TEXT"),
    ("district_key", "TEXT"), ("nfhs_district", "TEXT"), ("state_ut", "TEXT"),
    ("capability", "TEXT"), ("claim_confidence", "TEXT"),
    ("claim_terms", "TEXT"), ("corroborating_terms", "TEXT"),
    ("capability_evidence", "TEXT"), ("procedure_evidence", "TEXT"),
]
# District × capability trust-weighted coverage aggregate (long) — the primary coverage view source.
DISTRICT_CAPABILITY_COLS = [
    ("district_key", "TEXT"), ("nfhs_district", "TEXT"), ("state_ut", "TEXT"), ("capability", "TEXT"),
    ("high", "INT"), ("medium", "INT"), ("unverified", "INT"),
    ("verified_supply", "INT"), ("total_signal", "INT"),
    ("accepts_volunteers", "INT"), ("verified_beds", "INT"),
]
# District centroids (lat/lon) — nationwide distance from a volunteer origin.
CENTROID_COLS = [
    ("district_key", "TEXT"), ("nfhs_district", "TEXT"), ("state_ut", "TEXT"),
    ("lat", "DOUBLE PRECISION"), ("lon", "DOUBLE PRECISION"),
]


def _connect():
    return psycopg.connect(
        host=os.environ["PGHOST"], dbname=os.environ.get("PGDATABASE", "databricks_postgres"),
        user=os.environ["PGUSER"], password=os.environ["PGTOKEN"],
        sslmode="require", connect_timeout=30)


def _load_table(cur, table: str, cols: list[tuple[str, str]], csv_path: Path) -> int:
    coldefs = ", ".join(f'"{n}" {t}' for n, t in cols)
    names = [n for n, _ in cols]
    cur.execute(f"DROP TABLE IF EXISTS mission.{table}")
    cur.execute(f"CREATE TABLE mission.{table} ({coldefs})")
    with csv_path.open() as f:
        rows = list(csv.DictReader(f))
    placeholders = ", ".join(["%s"] * len(names))
    collist = ", ".join(f'"{n}"' for n in names)
    def cast(n, t, v):
        if v in (None, ""):
            return None
        return int(v) if t == "INT" else (float(v) if t.startswith("DOUBLE") else v)
    data = [[cast(n, t, r.get(n)) for n, t in cols] for r in rows]
    with cur.copy(f"COPY mission.{table} ({collist}) FROM STDIN") as cp:
        for d in data:
            cp.write_row(d)
    return len(rows)


def main() -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS mission")
        district_cols = _district_cols_from_header(CACHE / "district_base.csv")   # all 109 NFHS + counts
        n1 = _load_table(cur, "district_base", district_cols, CACHE / "district_base.csv")
        n2 = _load_table(cur, "reachability", REACH_COLS, CACHE / "reachability_patna.csv")
        cur.execute("CREATE INDEX ON mission.district_base (nfhs_district)")
        cur.execute("CREATE INDEX ON mission.reachability (district_key)")
        print(f"loaded mission.district_base: {n1} rows")
        print(f"loaded mission.reachability:  {n2} rows")

        # facility_claims is optional: present only once data/02_facility_text_ingest.py + geo_resolve
        # have produced it. Load it when available (the app cites it; degrades gracefully if absent).
        claims_csv = CACHE / "facility_claims.csv"
        if claims_csv.exists():
            n3 = _load_table(cur, "facility_claims", FACILITY_CLAIMS_COLS, claims_csv)
            cur.execute("CREATE INDEX ON mission.facility_claims (district_key, capability)")
            print(f"loaded mission.facility_claims: {n3} rows")
        else:
            print("facility_claims.csv not found — skipping (run data/02_facility_text_ingest.py "
                  "then data/geo_resolve.py to enable cited facility claims)")

        dc_csv = CACHE / "district_capability.csv"
        if dc_csv.exists():
            n4 = _load_table(cur, "district_capability", DISTRICT_CAPABILITY_COLS, dc_csv)
            cur.execute("CREATE INDEX ON mission.district_capability (capability, state_ut)")
            cur.execute("CREATE INDEX ON mission.district_capability (district_key, capability)")
            print(f"loaded mission.district_capability: {n4} rows")
        else:
            print("district_capability.csv not found — skipping (run data/geo_resolve.py)")

        cen_csv = CACHE / "district_centroids.csv"
        if cen_csv.exists():
            n5 = _load_table(cur, "district_centroids", CENTROID_COLS, cen_csv)
            cur.execute("CREATE INDEX ON mission.district_centroids (district_key)")
            print(f"loaded mission.district_centroids: {n5} rows")
        else:
            print("district_centroids.csv not found — skipping (run data/geo_resolve.py)")

        _provision_app_schema(cur)
        conn.commit()
        cur.execute("SELECT state_ut, COUNT(*) FROM mission.district_base GROUP BY state_ut ORDER BY 2 DESC LIMIT 5")
        print("district_base by state:", cur.fetchall())


def _provision_app_schema(cur) -> None:
    """Create the persistence (write) schema + tables and grant the app SP DML on them. Idempotent:
    CREATE IF NOT EXISTS + grants can be re-run safely. Tables are created owner-side here so the SP
    only needs DML (not ownership); ALTER DEFAULT PRIVILEGES covers any table the SP creates itself."""
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_APP_SCHEMA}")
    for name, cols in _STORE_TABLES.items():
        cur.execute(f"CREATE TABLE IF NOT EXISTS {_APP_SCHEMA}.{name} "
                    f"(id TEXT PRIMARY KEY, {cols}, created_at TEXT)")
    cur.execute(f'GRANT USAGE, CREATE ON SCHEMA {_APP_SCHEMA} TO "{APP_SP}"')
    cur.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {_APP_SCHEMA} TO "{APP_SP}"')
    cur.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA {_APP_SCHEMA} '
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{APP_SP}"')
    print(f"provisioned {_APP_SCHEMA} (scenarios/reviews/shortlist/notes) + granted app SP {APP_SP[:8]}… DML")


if __name__ == "__main__":
    main()
