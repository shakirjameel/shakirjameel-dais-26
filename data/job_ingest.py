# Databricks notebook source
# MAGIC %md
# MAGIC # Mission Copilot — ingestion Job, task 1 of 2: build curated UC Delta (serverless)
# MAGIC
# MAGIC Reproducible, on-platform replacement for the manual local pipeline
# MAGIC (`data/02_facility_text_ingest.py` → `03_nfhs_ingest.py` → `geo_resolve.py`).
# MAGIC
# MAGIC In this serverless task it:
# MAGIC 1. reads the **VF shared catalog** directly with Spark (no Statement Execution API / truststore needed in-cluster),
# MAGIC 2. reuses the existing **pure-Python transforms** (`geo_resolve`, `claims`, `coverage_view`), and
# MAGIC 3. writes the curated tables as **UC Delta** in `workspace.mission_uc` (the substrate Genie reads).
# MAGIC
# MAGIC The companion task `load_lakebase` (`data/job_load_lakebase.py`) then reads those Delta tables back
# MAGIC and loads **Lakebase** `mission.*` in a separate driver — Free-Edition serverless is too memory-tight
# MAGIC to do the Spark/shapely ingest AND the Lakebase load in one process. Runs as the **owner**.

# COMMAND ----------

# MAGIC %pip install psycopg[binary] shapely requests "databricks-sdk>=0.117"
# MAGIC %restart_python
# MAGIC # NOTE: the serverless base image ships databricks-sdk ~0.33 (no w.postgres) — pin >=0.117 so
# MAGIC # the Lakebase credential-minting step (w.postgres.get_endpoint / generate_database_credential) works.

# COMMAND ----------

import os
import shutil
import tempfile

dbutils.widgets.text("source_root", "", "Bundle files root (workspace.file_path)")
dbutils.widgets.text("lakebase_endpoint", "projects/mission-copilot/branches/production/endpoints/primary", "Lakebase endpoint")
dbutils.widgets.text("uc_catalog", "workspace", "UC catalog for curated Delta tables")
dbutils.widgets.text("uc_schema", "mission_uc", "UC schema for curated Delta tables")
dbutils.widgets.text("app_sp_client_id", "355b9275-0af4-4e57-b04c-f60cca3d9311", "App service-principal client id")

SOURCE_ROOT = dbutils.widgets.get("source_root").rstrip("/")
LAKEBASE_ENDPOINT = dbutils.widgets.get("lakebase_endpoint")
UC_CATALOG = dbutils.widgets.get("uc_catalog")
UC_SCHEMA = dbutils.widgets.get("uc_schema")
APP_SP = dbutils.widgets.get("app_sp_client_id")

# Redirect every module's cache dir to a WRITABLE tmp dir BEFORE importing data.* / mission_core.*
# (the bundle source on /Workspace is read-only). geo_resolve / load_lakebase / data_access / the
# polygon cache all honour DATA_CACHE_DIR.
CACHE_DIR = tempfile.mkdtemp(prefix="mission_cache_")
os.environ["DATA_CACHE_DIR"] = CACHE_DIR
os.environ["APP_SP_CLIENT_ID"] = APP_SP

import sys
if SOURCE_ROOT and SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

print("cache dir   :", CACHE_DIR)
print("source root :", SOURCE_ROOT)
print("uc target   :", f"{UC_CATALOG}.{UC_SCHEMA}")

# COMMAND ----------

# MAGIC %md ## 1. Pull source tables with Spark → upstream CSVs (what geo_resolve consumes)

# COMMAND ----------

from pathlib import Path

CATALOG = "databricks_virtue_foundation_dataset_dais_2026"
SCHEMA = "virtue_foundation_dataset"
INDIA = "latitude BETWEEN 6.0 AND 37.5 AND longitude BETWEEN 68.0 AND 97.5"

# Facility free-text + derived flags. Kept in sync with data/02_facility_text_ingest.py (same
# aliases / winsorization), but run via Spark SQL in-cluster instead of the Statement Execution API.
FACILITIES_SQL = f"""
SELECT
  unique_id, name,
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
  CASE WHEN try_cast(capacity AS double) BETWEEN 1 AND 5000 THEN try_cast(capacity AS double) END AS capacity_beds,
  CASE WHEN try_cast(numberDoctors AS double) BETWEEN 1 AND 500 THEN try_cast(numberDoctors AS double) END AS number_doctors,
  CASE WHEN lower(cast(acceptsVolunteers AS string)) IN ('true','1','yes') THEN 1 ELSE 0 END AS accepts_volunteers,
  organization_type, facilityTypeId AS facility_type,
  substr(coalesce(officialPhone, phone_numbers), 1, 120) AS phone,
  substr(coalesce(officialWebsite, websites), 1, 200) AS website,
  yearEstablished AS year_established
FROM {CATALOG}.{SCHEMA}.facilities
WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND {INDIA}
"""

NFHS_TABLE = f"{CATALOG}.{SCHEMA}.nfhs_5_district_health_indicators"


def _write_csv(pdf, path: Path):
    """Write a pandas frame the way the legacy ingest scripts did: nulls → '', newlines collapsed,
    everything stringified — so geo_resolve's csv.DictReader parses it identically."""
    import csv as _csv
    cols = list(pdf.columns)
    with path.open("w", newline="") as f:
        wr = _csv.writer(f)
        wr.writerow(cols)
        for _, row in pdf.iterrows():
            wr.writerow(["" if (v is None or (isinstance(v, float) and v != v)) else
                         str(v).replace("\r", " ").replace("\n", " ") for v in row])


fac_pdf = spark.sql(FACILITIES_SQL).toPandas()
_write_csv(fac_pdf, Path(CACHE_DIR) / "facilities_text.csv")
print(f"facilities_text.csv: {len(fac_pdf)} rows")

nfhs_pdf = spark.read.table(NFHS_TABLE).toPandas()
_write_csv(nfhs_pdf, Path(CACHE_DIR) / "nfhs5_districts.csv")
print(f"nfhs5_districts.csv: {len(nfhs_pdf)} rows × {len(nfhs_pdf.columns)} cols")

# Free the source frames immediately — geo_resolve re-reads from the CSVs, and Free-Edition
# serverless has a small driver (the cumulative heap otherwise OOMs at the Lakebase step).
import gc
del fac_pdf, nfhs_pdf
gc.collect()

# COMMAND ----------

# MAGIC %md ## 2. Resolve geography + claims (reuse geo_resolve unchanged) → district CSVs

# COMMAND ----------

from data import geo_resolve

out = geo_resolve.resolve()   # writes district_base / facility_claims / district_capability / district_centroids CSVs into CACHE_DIR
import json
print(json.dumps(out["stats"], indent=2))

# COMMAND ----------

# MAGIC %md ## 3. Materialize a curated analytics table (desert scores) via coverage_view (CSV mode)

# COMMAND ----------

# coverage_by_geography reads the CSVs we just wrote (data_access is in CSV mode — no PG env set yet)
# and applies the SAME trust-weighting / desert-score logic the app uses. We persist it so Genie can
# answer "highest desert score" questions over OUR computed metric, not just raw supply counts.
from mission_core.coverage_view import coverage_by_geography
from mission_core.claims import CAPABILITIES

_COVERAGE_FIELDS = ["district_key", "district", "state", "capability", "high", "medium", "unverified",
                    "verified_supply", "trust_weighted_supply", "supply_adequacy", "trust_ratio",
                    "gap_classification", "burden", "demand_available", "total_facilities",
                    "desert_score", "rank"]
coverage_rows = []
for cap in CAPABILITIES:
    for r in coverage_by_geography(cap):
        coverage_rows.append({k: r.get(k) for k in _COVERAGE_FIELDS})
print(f"district_coverage rows: {len(coverage_rows)} ({len(CAPABILITIES)} capabilities)")

# COMMAND ----------

# MAGIC %md ## 4. Write curated tables as UC Delta (the substrate Genie reads)

# COMMAND ----------

import pandas as pd

spark.sql(f"CREATE CATALOG IF NOT EXISTS {UC_CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {UC_CATALOG}.{UC_SCHEMA}")


def _save_delta(pdf: "pd.DataFrame", name: str):
    if pdf is None or len(pdf) == 0:
        print(f"  skip {name} (empty)")
        return
    # all-NaN object columns can confuse Spark inference; coerce object NaN → None
    pdf = pdf.astype(object).where(pd.notnull(pdf), None)
    sdf = spark.createDataFrame(pdf)
    (sdf.write.mode("overwrite").option("overwriteSchema", "true")
        .saveAsTable(f"{UC_CATALOG}.{UC_SCHEMA}.{name}"))
    print(f"  wrote {UC_CATALOG}.{UC_SCHEMA}.{name}: {len(pdf)} rows")


_C = Path(CACHE_DIR)
_save_delta(pd.read_csv(_C / "district_base.csv", dtype=str), "district_base")
_save_delta(pd.read_csv(_C / "district_capability.csv"), "district_capability")
_save_delta(pd.read_csv(_C / "facility_claims.csv", dtype=str), "facility_claims")
_save_delta(pd.read_csv(_C / "district_centroids.csv"), "district_centroids")
_save_delta(pd.DataFrame(coverage_rows), "district_coverage")

# COMMAND ----------

# MAGIC %md ## 4b. AI summary — Databricks ai_query reasons over each row's numbers (the AI column)

# COMMAND ----------

# Native Databricks AI: ai_query (open Foundation Model) turns each district's COMPUTED numbers into one
# concrete action THE VIRTUE FOUNDATION CAN ACTUALLY TAKE — it interprets the metrics, never invents them.
# VF is a volunteer-driven NGO: short-term volunteer medical/surgical MISSIONS (specialist teams) giving
# direct care + TRAINING local providers, DONATING equipment, and data-driven needs assessment — it does NOT
# build, register, or permanently staff facilities (those are off-model). failOnError=>false + a deterministic
# CASE fallback means the column is never blank even if a row errors or Free-Edition quota throttles.
print("generating ai_summary (VF-grounded) via ai_query(databricks-gpt-oss-20b) over district_coverage …")
spark.sql(f"""
CREATE OR REPLACE TABLE {UC_CATALOG}.{UC_SCHEMA}.district_coverage AS
SELECT *, COALESCE(
  ai_query('databricks-gpt-oss-20b',
    CONCAT('You advise the Virtue Foundation, a volunteer-driven medical NGO that runs SHORT-TERM volunteer ',
           'medical/surgical missions (specialist teams) giving direct care and TRAINING local providers, ',
           'DONATES medical equipment, and does data-driven needs assessment. It does NOT build, register, or ',
           'permanently staff facilities. In 18 words or fewer, recommend ONE action the Foundation can take in ',
           'this district, grounded ONLY in these numbers (do not invent facilities or statistics): deploy a ',
           'volunteer ', capability, ' mission, partner with a listed facility, donate equipment, train local ',
           'staff, or if facilities/claims are unverified or absent send a needs-assessment scout first. ',
           'District ', district, '. care-gap ', CAST(ROUND(desert_score,2) AS STRING), ' (0 to 1, higher worse); ',
           'coverage status ', gap_classification, '; verified providers ', CAST(verified_supply AS STRING),
           '; total facilities ', CAST(total_facilities AS STRING), '.'),
    failOnError => false).result,
  CASE WHEN total_facilities = 0 THEN 'No records yet - send a needs-assessment scout before planning a mission.'
       WHEN gap_classification = 'no_claim_desert' THEN 'Deploy a short-term volunteer mission for this service; verify the listed facilities first.'
       WHEN gap_classification = 'unverified_claims' THEN 'Verify the claimed capability (scout or records) before planning a mission.'
       ELSE 'Verified coverage - lower priority; consider a training or equipment top-up.' END) AS ai_summary
FROM {UC_CATALOG}.{UC_SCHEMA}.district_coverage
""")
_ai = spark.sql(f"SELECT COUNT(*) c, COUNT(ai_summary) s FROM {UC_CATALOG}.{UC_SCHEMA}.district_coverage").collect()[0]
print(f"ai_summary: {_ai['s']}/{_ai['c']} rows populated")
for _r in spark.sql(f"SELECT district, ai_summary FROM {UC_CATALOG}.{UC_SCHEMA}.district_coverage "
                    f"WHERE capability='maternity' ORDER BY desert_score DESC LIMIT 3").collect():
    print("  sample:", _r["district"], "->", _r["ai_summary"])

# COMMAND ----------

# Friendly table comments help Genie pick the right table.
spark.sql(f"COMMENT ON TABLE {UC_CATALOG}.{UC_SCHEMA}.district_coverage IS "
          "'Curated per-district × capability coverage: trust-weighted supply, supply_adequacy, "
          "NFHS demand (burden), gap_classification, and desert_score (higher = bigger, more-confident "
          "care gap). The primary curated analytics table.'")
spark.sql(f"COMMENT ON TABLE {UC_CATALOG}.{UC_SCHEMA}.district_capability IS "
          "'Per-district × capability supply claim counts (high/medium/unverified = trust grades), "
          "verified_supply, volunteer-accepting facilities and beds.'")

# COMMAND ----------

print("BUILD-UC COMPLETE — curated Delta tables in", f"{UC_CATALOG}.{UC_SCHEMA}.*")
print("Lakebase mission.* is loaded separately via the local `data.load_lakebase` path — the psycopg "
      "bulk-COPY OOMs on Free-Edition serverless's memory ceiling (see VERIFICATION.md). The app reads "
      "Lakebase; Genie reads these UC Delta tables.")

