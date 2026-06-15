"""
ingest_marketplace.py — Pull the Virtue Foundation Unity Catalog tables into the local
cache CSVs the offline pipeline expects.

  nfhs_5_district_health_indicators  -> data/cache/nfhs5_districts.csv   (columns already match)
  facilities                         -> data/cache/facilities_geo.csv    (derive maternal_supply + operator)

RUN
    DATABRICKS_CONFIG_PROFILE=dbc-8ee3f787-8d83 ./.venv/bin/python -m data.ingest_marketplace
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format

CACHE = Path(__file__).resolve().parent / "cache"
CATALOG = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset"

w = WorkspaceClient()
WAREHOUSE = next(wh.id for wh in w.warehouses.list())


def fetch_csv_rows(sql: str) -> list[dict]:
    """Run a query and return rows as dicts, pulling all chunks via external links (CSV)."""
    r = w.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE, wait_timeout="50s",
        disposition=Disposition.EXTERNAL_LINKS, format=Format.CSV)
    cols = [c.name for c in r.manifest.schema.columns]
    out: list[dict] = []
    chunk = r.result
    while chunk is not None:
        for link in (chunk.external_links or []):
            text = requests.get(link.external_link).text
            out.extend(csv.DictReader(io.StringIO(text), fieldnames=cols))
        nxt = chunk.next_chunk_index
        chunk = w.statement_execution.get_statement_result_chunk_n(r.statement_id, nxt) if nxt else None
    return out


def ingest_nfhs():
    rows = fetch_csv_rows(f"SELECT * FROM {CATALOG}.nfhs_5_district_health_indicators")
    out = CACHE / "nfhs5_districts.csv"
    with out.open("w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader(); wtr.writerows(rows)
    print(f"wrote {len(rows)} NFHS districts -> {out}")


def ingest_facilities():
    rows = fetch_csv_rows(
        f"SELECT latitude, longitude, specialties, operatorTypeId FROM {CATALOG}.facilities "
        f"WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    out = CACHE / "facilities_geo.csv"
    kept = 0
    with out.open("w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=["latitude", "longitude", "maternal_supply", "operator"])
        wtr.writeheader()
        for r in rows:
            try:
                lat, lon = float(r["latitude"]), float(r["longitude"])
            except (TypeError, ValueError):
                continue
            if not (6.0 < lat < 38.0 and 68.0 < lon < 98.0):   # crude India bbox; drop junk coords
                continue
            spec = (r.get("specialties") or "").lower()
            maternal = 1 if ("obstetric" in spec or "gynecolog" in spec) else 0
            op = (r.get("operatorTypeId") or "").strip().lower()
            operator = "public" if op in ("public", "government") else "private" if op == "private" else "other"
            wtr.writerow({"latitude": lat, "longitude": lon,
                          "maternal_supply": maternal, "operator": operator})
            kept += 1
    print(f"wrote {kept} facilities (of {len(rows)} fetched) -> {out}")


if __name__ == "__main__":
    ingest_nfhs()
    ingest_facilities()
