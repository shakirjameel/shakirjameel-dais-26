"""
02_facility_text_ingest.py — pull the facility FREE-TEXT columns the original extract dropped.

WHY (the summit "claims to verify" requirement)
    The provided facilities table carries uneven free-text the FDR pipeline (web crawl -> GenAI
    extraction) produced: description (100% coverage), capability (99.7%), procedure (92.5%),
    equipment (77%). Our first extract (facilities_geo.csv) kept only coordinates + a derived
    maternal_supply flag — so the app could assert facility PRESENCE but never CITE or VERIFY the
    claimed capability. The summit rubric is explicit: "cite the underlying facility text" and
    "treat noisy fields as claims to verify, not ground truth." This script brings that text local.

WHAT it produces
    data/cache/facilities_text.csv — a superset of facilities_geo.csv (same row grain + the
    maternal_supply flag, derived identically from `specialties`), PLUS the raw free-text columns.
    data/geo_resolve.py consumes this (when present) to classify + aggregate per-facility CLAIMS.

HOW (Free Edition, no notebook required)
    Runs LOCALLY against the serverless SQL warehouse via the Databricks SDK. truststore routes
    HTTPS through the macOS keychain CA so Zscaler TLS interception doesn't 403 us (same pattern as
    data/external/*). Text fields are truncated server-side (claim terms + a citable snippet live in
    the first few hundred chars) to keep the result inline-pageable.

RUN
    export PATH="$HOME/bin:$PATH"     # databricks CLI auth (DEFAULT profile) is reused by the SDK
    ./.venv/bin/python data/02_facility_text_ingest.py
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
OUT_CSV = CACHE / "facilities_text.csv"

WAREHOUSE_ID = os.environ.get("DBSQL_WAREHOUSE_ID", "3027e674d4e2102b")  # Serverless Starter
CATALOG = "databricks_virtue_foundation_dataset_dais_2026"
SCHEMA = "virtue_foundation_dataset"

# Same India bbox the data gate used to exclude corrupt coordinates (never "fix" them).
INDIA = "latitude BETWEEN 6.0 AND 37.5 AND longitude BETWEEN 68.0 AND 97.5"

# maternal_supply derived IDENTICALLY to the original gate (specialties mentioning ob/gyn), so the
# row grain + flag stay consistent with facilities_geo.csv; the text columns are the new payload.
STATEMENT = f"""
SELECT
  unique_id,
  name,
  address_city AS city,
  address_stateOrRegion AS state_region,
  address_zipOrPostcode AS pincode,
  latitude, longitude,
  operatorTypeId AS operator,
  CASE WHEN lower(specialties) LIKE '%obstetric%' OR lower(specialties) LIKE '%gynec%'
       THEN 1 ELSE 0 END AS maternal_supply,
  substr(specialties, 1, 400) AS specialties,
  substr(description,  1, 400) AS description,
  substr(capability,   1, 600) AS capability,
  substr(procedure,    1, 600) AS procedure,
  substr(equipment,    1, 400) AS equipment,
  substr(source_urls,  1, 300) AS source_urls,
  -- supply magnitude (winsorized: cap absurd outliers like 200000 beds; these are CLAIMS too)
  CASE WHEN try_cast(capacity AS double) BETWEEN 1 AND 5000 THEN try_cast(capacity AS double) END AS capacity_beds,
  CASE WHEN try_cast(numberDoctors AS double) BETWEEN 1 AND 500 THEN try_cast(numberDoctors AS double) END AS number_doctors,
  -- VF placement + operational-readiness signals
  CASE WHEN lower(cast(acceptsVolunteers AS string)) IN ('true','1','yes') THEN 1 ELSE 0 END AS accepts_volunteers,
  organization_type, facilityTypeId AS facility_type,
  substr(coalesce(officialPhone, phone_numbers), 1, 120) AS phone,
  substr(coalesce(officialWebsite, websites), 1, 200) AS website,
  yearEstablished AS year_established
FROM {CATALOG}.{SCHEMA}.facilities
WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND {INDIA}
"""


def _rows(w: WorkspaceClient, resp):
    """Yield every result row, paging through inline chunks."""
    sid = resp.statement_id
    chunk = resp.result
    while chunk is not None:
        for row in (chunk.data_array or []):
            yield row
        nxt = chunk.next_chunk_index
        if nxt is None:
            break
        chunk = w.statement_execution.get_statement_result_chunk_n(sid, nxt)


def main() -> None:
    w = WorkspaceClient()
    print(f"executing facility-text pull on warehouse {WAREHOUSE_ID} …")
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=STATEMENT,
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
            # normalize: nulls -> "", collapse newlines so the CSV stays one row per facility
            wr.writerow([("" if v is None else str(v).replace("\r", " ").replace("\n", " ")) for v in row])
            n += 1
    print(f"wrote {n} facilities (with free-text) -> {OUT_CSV}")
    print(f"columns: {cols}")
    print("next: ./.venv/bin/python -m data.geo_resolve   (classifies + aggregates maternal claims)")


if __name__ == "__main__":
    main()
