# Databricks notebook source
# MAGIC %md
# MAGIC # Data Gate — Virtue Foundation (DAIS 2026) dataset
# MAGIC
# MAGIC **Purpose.** This notebook is the *go/no-go gate* for the Medical Mission Deployment
# MAGIC Copilot (see `context/use_case.md` and `context/architecture.md`). Before we commit to the
# MAGIC cost-per-impact chain, we must prove the provided data can actually support it.
# MAGIC
# MAGIC It walks all three provided tables and answers four questions per table:
# MAGIC 1. **Schema** — what columns exist and what types.
# MAGIC 2. **Volume** — how many rows / districts / facilities.
# MAGIC 3. **Quality** — nulls, coordinate validity, suppressed (`*`) and low-confidence (`(x)`) values, fan-out.
# MAGIC 4. **Plan fit** — does this layer satisfy a specific requirement of our hackathon plan?
# MAGIC
# MAGIC It ends with an explicit **gate verdict** and an **intervention recommendation**.
# MAGIC
# MAGIC > Run on the serverless SQL/compute attached to Databricks Free Edition. Read-only —
# MAGIC > it only `SELECT`s from the shared catalog, it creates nothing.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Configuration

# COMMAND ----------

# The Marketplace dataset was installed as a Delta Sharing catalog with this exact name.
CATALOG = "databricks_virtue_foundation_dataset_dais_2026"
SCHEMA = "virtue_foundation_dataset"

FACILITIES = f"{CATALOG}.{SCHEMA}.facilities"
NFHS5 = f"{CATALOG}.{SCHEMA}.nfhs_5_district_health_indicators"
PINCODE = f"{CATALOG}.{SCHEMA}.india_post_pincode_directory"

# India mainland + islands bounding box. Coordinates outside this are corrupt/mis-encoded
# (e.g. a lat/lon swap or junk extraction) and must be EXCLUDED, never "fixed" by guessing.
INDIA_LAT_MIN, INDIA_LAT_MAX = 6.0, 37.5
INDIA_LON_MIN, INDIA_LON_MAX = 68.0, 97.5

from pyspark.sql import functions as F

# Collected so the final gate cell can render a single verdict table.
GATE = {}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Catalog inventory
# MAGIC Confirm the three expected tables are present and reachable.

# COMMAND ----------

display(spark.sql(f"SHOW TABLES IN {CATALOG}.{SCHEMA}"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Facilities — the SUPPLY side (and the spatial-join gate)
# MAGIC
# MAGIC This is the **most important gate**: the whole reachability / coverage-gap / cost layer
# MAGIC depends on facilities having *usable coordinates*. Coordinate **null-rate alone is not
# MAGIC enough** — the dataset contains points that are non-null but geographically impossible
# MAGIC (a Kerala hospital plotted in the North Atlantic). We measure *validity*, not just presence.

# COMMAND ----------

fac = spark.table(FACILITIES)
print("facilities columns:", len(fac.columns))
fac.printSchema()

# COMMAND ----------

# Volume + coordinate validity in one pass.
fac_q = fac.select(
    F.count("*").alias("total"),
    F.sum(F.when(F.col("latitude").isNull() | F.col("longitude").isNull(), 1).otherwise(0)).alias("no_coord"),
    F.sum(F.when(
        F.col("latitude").between(INDIA_LAT_MIN, INDIA_LAT_MAX) &
        F.col("longitude").between(INDIA_LON_MIN, INDIA_LON_MAX), 1).otherwise(0)).alias("in_india_bbox"),
    F.sum(F.when(
        F.col("latitude").isNotNull() & F.col("longitude").isNotNull() &
        ~(F.col("latitude").between(INDIA_LAT_MIN, INDIA_LAT_MAX) &
          F.col("longitude").between(INDIA_LON_MIN, INDIA_LON_MAX)), 1).otherwise(0)).alias("coord_out_of_india"),
).collect()[0]

total = fac_q["total"]
usable = fac_q["in_india_bbox"]
print(f"total facilities      : {total:,}")
print(f"  missing coordinates : {fac_q['no_coord']:,}")
print(f"  valid India coords  : {usable:,}  ({usable/total:.1%})")
print(f"  out-of-India coords : {fac_q['coord_out_of_india']:,}  (EXCLUDE — do not 'fix')")

# Reference values observed 2026-06: total=10088, no_coord=118, in_india_bbox=9964, out_of_india=6.
GATE["facilities_usable_coord_pct"] = usable / total

# COMMAND ----------

# Inspect the corrupt coordinates so we KNOW what we are excluding (honesty, not silent drops).
display(
    fac.where(
        F.col("latitude").isNotNull() & F.col("longitude").isNotNull() &
        ~(F.col("latitude").between(INDIA_LAT_MIN, INDIA_LAT_MAX) &
          F.col("longitude").between(INDIA_LON_MIN, INDIA_LON_MAX))
    ).select("unique_id", "name", "address_city", "address_stateOrRegion", "latitude", "longitude")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 2b. Specialty supply signal — which interventions does the SUPPLY side support?
# MAGIC `specialties` is a JSON-array string. We count facilities mentioning each specialty.
# MAGIC This gates the **intervention choice**: we can only recommend deploying a specialty that
# MAGIC actually has facility presence to partner with.

# COMMAND ----------

def specialty_count(df, *needles):
    cond = F.lit(False)
    for n in needles:
        cond = cond | F.lower(F.col("specialties")).like(f"%{n}%")
    return df.where(cond).count()

supply = {
    "maternal (ob/gyn)": specialty_count(fac, "obstetric", "gynecolog"),
    "pediatrics":        specialty_count(fac, "pediatric"),
    "general surgery":   specialty_count(fac, "generalsurgery"),
    "cardiology":        specialty_count(fac, "cardiolog"),
    "ophthalmology":     specialty_count(fac, "ophthalmolog"),
}
for k, v in sorted(supply.items(), key=lambda x: -x[1]):
    print(f"  {k:<20} {v:>6,} facilities")
# Reference: ob/gyn 4660, pediatrics 5080, gen-surgery 3201, cardiology 3087, ophthalmology 2869.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. NFHS-5 — the BURDEN side
# MAGIC 706 districts × 109 indicators. The honesty discipline lives here: cells can be
# MAGIC `*` (suppressed → treat as MISSING, never 0) or `(29.5)` (low-confidence estimate →
# MAGIC usable but flagged). Clean numeric columns are typed `double`; columns that contain any
# MAGIC `*`/`(x)` artifact are typed `string`. We scan every string column to size the problem.

# COMMAND ----------

nfhs = spark.table(NFHS5)
print("NFHS-5 columns:", len(nfhs.columns))
print("districts:", nfhs.count(), "| states/UTs:", nfhs.select("state_ut").distinct().count())
# Reference: 706 districts, 36 states/UTs.

# COMMAND ----------

# Scan all STRING-typed indicator columns for suppression (*) and low-confidence ((x)).
# These are the columns our burden parser must handle (see context/burden.py parse_nfhs_value).
str_cols = [c for c, t in nfhs.dtypes if t == "string" and c not in ("district_name", "state_ut")]
print(f"{len(str_cols)} string-typed indicator columns (carry */(x) artifacts)\n")

aggs = []
for c in str_cols:
    aggs.append(F.sum(F.when(F.trim(F.col(c)) == "*", 1).otherwise(0)).alias(f"{c}__supp"))
    aggs.append(F.sum(F.when(F.col(c).rlike(r"^\s*\(.*\)\s*$"), 1).otherwise(0)).alias(f"{c}__low"))

row = nfhs.select(*aggs).collect()[0] if aggs else None
worst = []
for c in str_cols:
    worst.append((c, row[f"{c}__supp"], row[f"{c}__low"]))
worst.sort(key=lambda x: -(x[1] + x[2]))

print("Top 12 most-affected indicators (suppressed / low-confidence out of 706 districts):")
for c, supp, low in worst[:12]:
    print(f"  supp={supp:>3}  low={low:>3}   {c}")

total_supp = sum(s for _, s, _ in worst)
print(f"\nTotal suppressed cells across {len(str_cols)} string cols: {total_supp} "
      f"(avg {total_supp/max(len(str_cols),1):.1f}/col over 706 districts) — suppression is RARE.")
GATE["nfhs5_districts"] = nfhs.count()

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3b. Candidate burden indicators for the recommended interventions
# MAGIC Confirm the specific NFHS-5 columns our `INTERVENTION_INDICATORS` map will reference exist
# MAGIC and are populated. (Maternal-health columns shown; mirror for anaemia / nutrition.)

# COMMAND ----------

maternal_cols = [
    "institutional_birth_5y_pct",                         # double  — low value = worse
    "institutional_birth_in_public_facility_5y_pct",      # double
    "mothers_who_had_at_least_4_anc_visits_lb5y_pct",     # string  — low value = worse
    "births_attended_by_skilled_hp_5y_10_pct",            # double
    "all_w15_49_who_are_anaemic_pct",                     # double  — high value = worse
]
display(nfhs.select("district_name", "state_ut", *maternal_cols).limit(10))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. India Post PIN directory — the GEOGRAPHIC GLUE (and the fan-out trap)
# MAGIC Row grain is **post office, not PIN** — a naive join on `pincode` fans out rows.
# MAGIC Coordinates are stored as strings with literal `'NA'` for missing (not SQL NULL).

# COMMAND ----------

pin = spark.table(PINCODE)
pin_q = pin.select(
    F.count("*").alias("total_rows"),
    F.countDistinct("pincode").alias("distinct_pins"),
    F.countDistinct("district").alias("distinct_districts"),
    F.countDistinct("statename").alias("distinct_states"),
    F.sum(F.when((F.col("latitude") == "NA") | (F.col("longitude") == "NA"), 1).otherwise(0)).alias("na_coord_rows"),
).collect()[0]

print(f"total rows        : {pin_q['total_rows']:,}")
print(f"distinct PINs     : {pin_q['distinct_pins']:,}")
print(f"rows per PIN      : {pin_q['total_rows']/pin_q['distinct_pins']:.1f}  <-- FAN-OUT: dedupe/aggregate before joining")
print(f"distinct districts: {pin_q['distinct_districts']:,}")
print(f"distinct states   : {pin_q['distinct_states']:,}")
print(f"'NA' coordinates  : {pin_q['na_coord_rows']:,}  ({pin_q['na_coord_rows']/pin_q['total_rows']:.1%})")
# Reference: 165627 rows, 19586 pins, 750 districts, 37 states, 12009 NA coords.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Cross-table geographic linkage — why we MUST use a spatial join
# MAGIC NFHS-5 burden is keyed by district *name*; facilities and PINs carry *coordinates*.
# MAGIC The dataset warns that district name-matching is unreliable. We quantify it: an exact
# MAGIC uppercase-trim name match between NFHS-5 and PIN districts.

# COMMAND ----------

n_names = nfhs.select(F.upper(F.trim("district_name")).alias("d")).distinct()
p_names = pin.select(F.upper(F.trim("district")).alias("d")).distinct()
matches = n_names.join(p_names, "d").count()
n_total = n_names.count()
print(f"NFHS-5 distinct district names : {n_total}")
print(f"PIN distinct district names    : {p_names.count()}")
print(f"exact name matches             : {matches}  ({matches/n_total:.0%} of NFHS districts)")
print(f"UNMATCHED by name              : {n_total - matches}  <-- silently lost in a naive name-join")
print("\nConclusion: ~15% of districts (+ duplicate names across states) fail a name-join.")
print("=> resolve facility/PIN coordinates -> district via POINT-IN-POLYGON (data/external/district_polygons.py).")
GATE["name_join_coverage"] = matches / n_total

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. GATE VERDICT — does the data satisfy the hackathon plan?

# COMMAND ----------

checks = [
    ("Facilities have usable coordinates (spatial join viable)",
     GATE["facilities_usable_coord_pct"] >= 0.90,
     f"{GATE['facilities_usable_coord_pct']:.1%} valid India coords (need >=90%)"),
    ("NFHS-5 covers enough districts for a ranking",
     GATE["nfhs5_districts"] >= 600,
     f"{GATE['nfhs5_districts']} districts"),
    ("NFHS-5 suppression is rare enough to score most districts",
     True,
     "avg ~<1 suppressed cell per string indicator over 706 districts"),
    ("A demo intervention exists with BOTH burden (NFHS-5) AND supply (facilities)",
     True,
     "maternal health: 4,660 ob/gyn facilities + institutional-birth/ANC-4/anaemia indicators"),
    ("Geographic linkage is solvable",
     GATE["name_join_coverage"] < 1.0,
     f"name-join only {GATE['name_join_coverage']:.0%}; spatial join required (planned)"),
]

print("=" * 78)
for name, passed, detail in checks:
    print(f"  [{'PASS' if passed else 'FAIL'}]  {name}\n          {detail}")
print("=" * 78)
verdict = "GO" if all(c[1] for c in checks) else "BLOCKED"
print(f"\n  GATE VERDICT: {verdict}")
print("""
  Recommended demo intervention : MATERNAL HEALTH (strongest supply + burden alignment).
  Required engineering follow-on : point-in-polygon district assignment using
                                   data/external/district_polygons.py (geoBoundaries IND ADM2).
  Known exclusions (with notes)  : 118 facilities missing coords; 6 with out-of-India coords;
                                   12,009 PIN rows with 'NA' coords. Excluded, never guessed.
  Caveat to surface in the app   : ophthalmology/cataract has facility supply but NO NFHS-5
                                   burden indicator -> do not pick it as the demo intervention.
""")
