"""
reach.py — travel distance/time from a volunteer ORIGIN (home base) to a district.

The optimizer baselines mission cost on WHERE the team is based (Delhi → Bihar ≠ Patna → Bihar).
Precise ORS road travel exists only for the Patna staging region (Bihar+Jharkhand); everywhere else
we use straight-line (haversine) origin→district-centroid × a road factor, clearly labeled "estimated".
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
    """Returns {distance_km, drive_hours, source}. ORS road travel when the team is in Patna and the
    district is in the precomputed region; else straight-line from the origin city to the district
    centroid × road factor, labeled estimated. distance_km None if the district has no centroid."""
    if origin_name == DEFAULT_ORIGIN:                 # Patna staging → precise ORS where available
        km_hrs = load_reachability().get(district_key)
        if km_hrs:
            return {"distance_km": round(km_hrs[0], 1), "drive_hours": round(km_hrs[1], 2),
                    "source": "ORS road (Patna)"}
    o = origin_latlon(origin_name)
    cen = load_district_centroids().get(district_key)
    if not o or not cen:
        return {"distance_km": None, "drive_hours": None, "source": "unknown (no centroid)"}
    km = haversine_km(o[0], o[1], cen[0], cen[1]) * ROAD_FACTOR
    return {"distance_km": round(km, 1), "drive_hours": round(km / AVG_SPEED_KMH, 2),
            "source": "straight-line est."}
