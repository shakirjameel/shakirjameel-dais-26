"""
reach.py — travel distance/time from a volunteer ORIGIN (home base) to a district.

The optimizer baselines mission cost on WHERE the team is based (Delhi → Bihar ≠ Patna → Bihar).
We use the most accurate DISTANCE we have: measured ORS road distance for the Patna staging region
(Bihar+Jharkhand, the only region precomputed), and straight-line (haversine) origin→centroid × a
road factor everywhere else — each labeled with its provenance ("source") for the UI.

Travel TIME is modelled uniformly for EVERY origin as distance ÷ AVG_SPEED_KMH. This is deliberate:
mission cost is dominated by the value of clinician-time lost to travel (∝ drive_hours), so if
drive_hours came from a different model than distance for some rows (e.g. raw ORS road-time, which is
independent of distance), a NEARER district could cost MORE than a farther one and toggling the home
base would silently switch the cost basis (see VERIFICATION.md, finding F1). Deriving time from
distance keeps cost monotonic in distance and comparable across origins. AVG_SPEED_KMH and ROAD_FACTOR
are named, adjustable assumptions like every other coefficient.
"""

from __future__ import annotations

from math import radians, sin, cos, asin, sqrt

from .data_access import load_reachability, load_district_centroids
from .geo_names import origin_latlon, DEFAULT_ORIGIN

ROAD_FACTOR = 1.3        # straight-line → approx road distance (rural India)
AVG_SPEED_KMH = 45.0     # assumed average road speed → drive-hours estimate


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


def distance_from_origin(origin_name: str, district_key: str) -> dict:
    """Returns {distance_km, drive_hours, source}. DISTANCE is measured ORS road distance when the team
    is in Patna and the district is in the precomputed region, else straight-line origin→centroid ×
    road factor. TIME is always distance ÷ AVG_SPEED_KMH (uniform across origins → cost monotonic in
    distance and origin-comparable; see module docstring / F1). distance_km None if no centroid."""
    if origin_name == DEFAULT_ORIGIN:                 # Patna staging → measured ORS road distance
        km_hrs = load_reachability().get(district_key)
        if km_hrs:
            km = km_hrs[0]                            # keep ORS road DISTANCE (accurate); standardise TIME
            return {"distance_km": round(km, 1), "drive_hours": round(km / AVG_SPEED_KMH, 2),
                    "source": "ORS road (Patna)"}
    o = origin_latlon(origin_name)
    cen = load_district_centroids().get(district_key)
    if not o or not cen:
        return {"distance_km": None, "drive_hours": None, "source": "unknown (no centroid)"}
    km = haversine_km(o[0], o[1], cen[0], cen[1]) * ROAD_FACTOR
    return {"distance_km": round(km, 1), "drive_hours": round(km / AVG_SPEED_KMH, 2),
            "source": "straight-line est."}
