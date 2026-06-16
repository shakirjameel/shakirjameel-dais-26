"""
03_nfhs_ingest.py — pull the FULL NFHS-5 district indicator table (706 districts × 109 columns).

Our earlier extract kept only 5 indicators. The optimizer now needs per-capability DEMAND signals
(oncology = cancer-screening gaps, ICU = hypertension/diabetes prevalence, maternity = ANC/
institutional birth, …) plus affordability/insurance/equity columns. Per the user's "prefer having
the data": we pull EVERY column (SELECT *) into data/cache/nfhs5_districts.csv; mission_core/burden.py
maps the specific columns each capability uses (with an honest gradient — some capabilities have no
NFHS proxy). Cells keep '*' (suppressed) / '(x)' (low-confidence) markers verbatim — parsed downstream.

RUN
    export PATH="$HOME/bin:$PATH"
    ./.venv/bin/python data/03_nfhs_ingest.py
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import truststore
truststore.inject_into_ssl()

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState

CACHE = Path(__file__).resolve().parent / "cache"
OUT_CSV = CACHE / "nfhs5_districts.csv"
WAREHOUSE_ID = os.environ.get("DBSQL_WAREHOUSE_ID", "3027e674d4e2102b")
TABLE = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset.nfhs_5_district_health_indicators"


def _rows(w, resp):
    sid, chunk = resp.statement_id, resp.result
    while chunk is not None:
        for row in (chunk.data_array or []):
            yield row
        nxt = chunk.next_chunk_index
        if nxt is None:
            break
        chunk = w.statement_execution.get_statement_result_chunk_n(sid, nxt)


def main() -> None:
    w = WorkspaceClient()
    print(f"pulling FULL NFHS-5 ({TABLE.split('.')[-1]}) …")
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=f"SELECT * FROM {TABLE}",
        disposition=Disposition.INLINE, format=Format.JSON_ARRAY, wait_timeout="50s")
    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"statement {resp.status.state}: {resp.status.error}")
    cols = [c.name for c in resp.manifest.schema.columns]
    n = 0
    CACHE.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        for row in _rows(w, resp):
            wr.writerow([("" if v is None else str(v).replace("\r", " ").replace("\n", " ")) for v in row])
            n += 1
    print(f"wrote {n} districts × {len(cols)} columns -> {OUT_CSV}")


if __name__ == "__main__":
    main()
