"""
bootstrap_cache.py — Pull cache CSVs from Databricks using the SQL connector.

Generates the same files that 02_facility_text_ingest.py + geo_resolve.py would produce,
using the databricks.sql connector (PAT auth) instead of WorkspaceClient.

RUN:
    python data/bootstrap_cache.py
    python -m data.geo_resolve
"""

import csv
from pathlib import Path
from databricks import sql

DB_HOST = "dbc-2f9d7b87-5aa9.cloud.databricks.com"
DB_HTTP_PATH = "/sql/1.0/warehouses/248996ee378e4a9d"
DB_TOKEN = "dapi30966aeb7adc407b4cf4826b042eb53b"
CATALOG = "databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset"

CACHE = Path(__file__).resolve().parent / "cache"


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    conn = sql.connect(server_hostname=DB_HOST, http_path=DB_HTTP_PATH, access_token=DB_TOKEN)
    cursor = conn.cursor()

    # 1. NFHS-5 district roster
    print("Pulling NFHS-5 districts...")
    cursor.execute(f"""
        SELECT TRIM(district_name) AS district_name, state_ut,
            institutional_birth_5y_pct,
            mothers_who_had_at_least_4_anc_visits_lb5y_pct,
            births_attended_by_skilled_hp_5y_10_pct,
            all_w15_49_who_are_anaemic_pct,
            child_u5_who_are_stunted_height_for_age_18_pct
        FROM {CATALOG}.nfhs_5_district_health_indicators
        ORDER BY state_ut, district_name
    """)
    cols = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    nfhs_csv = CACHE / "nfhs5_districts.csv"
    with nfhs_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["district_name", "state_ut"] + cols[2:])
        for row in rows:
            w.writerow([str(v).strip() if v is not None else "" for v in row])
    print(f"  {len(rows)} rows -> {nfhs_csv}")

    # 2. Facilities with text columns
    print("Pulling facilities (with text)...")
    cursor.execute(f"""
        SELECT
            unique_id, name,
            address_city AS city,
            address_stateOrRegion AS state_region,
            address_zipOrPostcode AS pincode,
            latitude, longitude,
            operatorTypeId AS operator,
            CASE WHEN lower(specialties) LIKE '%obstetric%' OR lower(specialties) LIKE '%gynec%'
                 THEN 1 ELSE 0 END AS maternal_supply,
            SUBSTR(specialties, 1, 400) AS specialties,
            SUBSTR(description, 1, 400) AS description,
            SUBSTR(capability, 1, 600) AS capability,
            SUBSTR(procedure, 1, 600) AS procedure_text,
            SUBSTR(equipment, 1, 400) AS equipment,
            SUBSTR(source_urls, 1, 300) AS source_urls
        FROM {CATALOG}.facilities
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
          AND latitude BETWEEN 6.0 AND 37.5 AND longitude BETWEEN 68.0 AND 97.5
    """)
    fac_cols = [desc[0] for desc in cursor.description]
    fac_rows = cursor.fetchall()
    fac_csv = CACHE / "facilities_text.csv"
    with fac_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(fac_cols)
        for row in fac_rows:
            w.writerow([("" if v is None else str(v).replace("\r", " ").replace("\n", " ")) for v in row])
    print(f"  {len(fac_rows)} facilities -> {fac_csv}")

    cursor.close()
    conn.close()
    print("\nDone. Next: python -m data.geo_resolve")


if __name__ == "__main__":
    main()
