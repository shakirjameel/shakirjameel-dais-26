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

CACHE = Path(__file__).resolve().parent / "cache"

# district_base columns -> Postgres types. NFHS indicators stay TEXT to preserve '*'/'(x)' markers.
DISTRICT_COLS = [
    ("nfhs_district", "TEXT"), ("state_ut", "TEXT"),
    ("facilities", "INT"), ("maternal_supply_facilities", "INT"),
    ("public", "INT"), ("private", "INT"),
    ("institutional_birth_5y_pct", "TEXT"),
    ("mothers_who_had_at_least_4_anc_visits_lb5y_pct", "TEXT"),
    ("births_attended_by_skilled_hp_5y_10_pct", "TEXT"),
    ("all_w15_49_who_are_anaemic_pct", "TEXT"),
    ("child_u5_who_are_stunted_height_for_age_18_pct", "TEXT"),
]
REACH_COLS = [
    ("district_key", "TEXT"), ("district", "TEXT"), ("state", "TEXT"),
    ("distance_km", "DOUBLE PRECISION"), ("duration_min", "DOUBLE PRECISION"), ("source", "TEXT"),
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
        n1 = _load_table(cur, "district_base", DISTRICT_COLS, CACHE / "district_base.csv")
        n2 = _load_table(cur, "reachability", REACH_COLS, CACHE / "reachability_patna.csv")
        cur.execute("CREATE INDEX ON mission.district_base (nfhs_district)")
        cur.execute("CREATE INDEX ON mission.reachability (district_key)")
        conn.commit()
        print(f"loaded mission.district_base: {n1} rows")
        print(f"loaded mission.reachability:  {n2} rows")
        cur.execute("SELECT state_ut, COUNT(*) FROM mission.district_base GROUP BY state_ut ORDER BY 2 DESC LIMIT 5")
        print("district_base by state:", cur.fetchall())


if __name__ == "__main__":
    main()
