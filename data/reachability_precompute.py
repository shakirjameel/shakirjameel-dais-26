"""
reachability_precompute.py — Pre-compute & cache ORS road reachability for the demo.

Step (b) of the build. The cost-per-impact chain needs road distance + drive time from the
staging city to each candidate district. We PRE-COMPUTE these via ORS and cache them, so the
live demo never makes a network call (architecture.md Design rule).

Pipeline:
  1. compute a centroid per district polygon (shapely), reconcile polygon->NFHS name
  2. select candidate districts (states near the staging city)
  3. ORS Matrix: staging city -> district centroids (chunked, cached, straight-line fallback)
  4. write data/cache/reachability_<staging>.csv  ->  consumed by make_reach_fn()

RUN
    set -a; . ./.env; set +a            # load ORS_API_KEY
    ./.venv/bin/python -m data.reachability_precompute
"""

from __future__ import annotations

import csv
from pathlib import Path

from shapely.geometry import shape

from data.external.district_polygons import fetch_india_districts, _feature_name
from data.external.ors_client import reachability_matrix
from data.geo_resolve import normalize_name, DISTRICT_ALIASES, load_nfhs_districts
from mission_core.data_access import STAGING, CANDIDATE_STATES  # single source of truth

CACHE = Path(__file__).resolve().parent / "cache"

ORS_CHUNK = 25                                                 # destinations per ORS matrix call


def district_centroids() -> dict:
    """{normalized NFHS district name -> (lat, lon)} from polygon centroids, reconciled to NFHS."""
    fc = fetch_india_districts()
    nfhs_keys = {normalize_name(d["district_name"]) for d in load_nfhs_districts()}
    out = {}
    for f in fc["features"]:
        poly_key = normalize_name(_feature_name(f["properties"]))
        nfhs_key = poly_key if poly_key in nfhs_keys else DISTRICT_ALIASES.get(poly_key)
        if not nfhs_key:
            continue
        c = shape(f["geometry"]).centroid
        out[nfhs_key] = (round(c.y, 5), round(c.x, 5))  # (lat, lon)
    return out


def precompute() -> dict:
    centroids = district_centroids()
    nfhs = load_nfhs_districts()
    # candidate districts: in the target states AND we have a centroid for them
    candidates = []
    for d in nfhs:
        if d["state_ut"].strip().lower() in CANDIDATE_STATES:
            key = normalize_name(d["district_name"])
            if key in centroids:
                candidates.append((d["district_name"].strip(), d["state_ut"].strip(), key, centroids[key]))
    print(f"staging={STAGING['name']} | candidate districts with centroids: {len(candidates)}")

    origin = [(STAGING["lat"], STAGING["lon"])]
    results = {}
    sources = 0
    for i in range(0, len(candidates), ORS_CHUNK):
        chunk = candidates[i:i + ORS_CHUNK]
        dests = [c[3] for c in chunk]
        matrix = reachability_matrix(origin, dests)   # cached + straight-line fallback inside
        for j, (name, state, key, _) in enumerate(chunk):
            cell = matrix[(0, j)]
            results[key] = {"district": name, "state": state, **cell}
            if cell["source"] == "ors":
                sources += 1
    print(f"reachability computed for {len(results)} districts ({sources} via ORS, "
          f"{len(results)-sources} via straight-line fallback)")

    out_csv = CACHE / f"reachability_{STAGING['name'].lower()}.csv"
    with out_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["district_key", "district", "state", "distance_km", "duration_min", "source"])
        for key, r in sorted(results.items()):
            w.writerow([key, r["district"], r["state"], r["distance_km"], r["duration_min"], r["source"]])
    print(f"wrote -> {out_csv}")
    return results


def make_reach_fn(staging_name: str = "Patna"):
    """Return a reach_fn(district_row) -> (distance_km, drive_hours) | None for mission_core.chain,
    backed by the cached reachability table. None for districts outside the candidate set."""
    path = CACHE / f"reachability_{staging_name.lower()}.csv"
    table = {}
    with path.open() as f:
        for r in csv.DictReader(f):
            table[r["district_key"]] = (float(r["distance_km"]), float(r["duration_min"]) / 60.0)

    def reach_fn(district_row: dict):
        return table.get(normalize_name(district_row["nfhs_district"]))
    return reach_fn


if __name__ == "__main__":
    precompute()
