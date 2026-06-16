"""
district_polygons.py — India district (ADM2) boundaries + point-in-polygon assignment.

WHY THIS EXISTS
    The data gate proved a naive district NAME-join links only ~85% of NFHS-5 districts
    (and is ambiguous for names repeated across states). The robust fix is to resolve every
    facility / PIN coordinate to a district by POINT-IN-POLYGON against real boundaries.
    This dependency was missing from the original plan's provenance ledger — it lives here.

SOURCE
    geoBoundaries (open, CC-BY) ADM2 = districts for India (ISO IND).
    API: https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/
    The API returns a JSON whose `gjDownloadURL` points at the GeoJSON FeatureCollection.
    Alternative if blocked: DataMeet India Maps (https://github.com/datameet/maps).

USAGE
    from data.external.district_polygons import fetch_india_districts, build_index, assign_district
    fc = fetch_india_districts()                 # downloads + caches the GeoJSON
    idx = build_index(fc)                        # prepares shapes (shapely if available)
    d = assign_district(23.01, 72.56, idx)       # -> {"district","state","shapeID"} or None
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Cache dir honours DATA_CACHE_DIR so the Databricks Job (whose bundle source lives on the
# read-only /Workspace mount) can redirect writes to a writable tmp dir; defaults to data/cache.
CACHE_DIR = Path(os.environ.get("DATA_CACHE_DIR") or (Path(__file__).resolve().parent.parent / "cache"))
GEOJSON_FILE = CACHE_DIR / "india_adm2.geojson"
GEOBOUNDARIES_API = "https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/"


def _use_os_trust_store() -> None:
    """Route Python SSL through the OS trust store (no-op if `truststore` absent).

    Needed on machines behind a TLS-intercepting proxy (e.g. Zscaler) whose root CA is in
    the OS keychain but not in Python's bundled certifi store.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


# --------------------------------------------------------------------------- fetch
def fetch_india_districts(force: bool = False) -> dict:
    """
    Download the India ADM2 (district) GeoJSON FeatureCollection and cache it.
    Returns the parsed GeoJSON dict. Re-uses the cache unless force=True.
    """
    if GEOJSON_FILE.exists() and not force:
        return json.loads(GEOJSON_FILE.read_text())

    _use_os_trust_store()  # so it works behind TLS-intercepting proxies (e.g. Zscaler)
    import requests  # required only for the live fetch

    meta = requests.get(GEOBOUNDARIES_API, timeout=30)
    meta.raise_for_status()
    download_url = meta.json()["gjDownloadURL"]

    gj = requests.get(download_url, timeout=120)
    gj.raise_for_status()
    fc = gj.json()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    GEOJSON_FILE.write_text(json.dumps(fc))
    print(f"[district_polygons] cached {len(fc.get('features', []))} district polygons -> {GEOJSON_FILE}")
    return fc


# --------------------------------------------------------------------------- index
def _feature_name(props: dict) -> str:
    # geoBoundaries uses 'shapeName'; be tolerant of schema drift across sources.
    return props.get("shapeName") or props.get("DISTRICT") or props.get("district") or ""


def build_index(feature_collection: dict):
    """
    Prepare polygons for fast point lookup.
    Uses shapely + STRtree when available (fast, correct); otherwise returns a plain list
    used by a pure-Python ray-casting fallback (slower but dependency-free).
    """
    features = feature_collection["features"]
    try:
        from shapely.geometry import shape
        from shapely.strtree import STRtree

        shapes, meta = [], []
        for f in features:
            geom = shape(f["geometry"])
            shapes.append(geom)
            meta.append({"district": _feature_name(f["properties"]),
                         "shapeID": f["properties"].get("shapeID")})
        return {"engine": "shapely", "tree": STRtree(shapes), "shapes": shapes, "meta": meta}
    except ImportError:
        polys = []
        for f in features:
            polys.append({"district": _feature_name(f["properties"]),
                          "shapeID": f["properties"].get("shapeID"),
                          "geometry": f["geometry"]})
        return {"engine": "pure", "polys": polys}


# --------------------------------------------------------------------------- assign
def assign_district(lat: float, lon: float, index) -> dict | None:
    """
    Point-in-polygon: return the district containing (lat, lon), or None if uncoded.
    NOTE: spatial join on coordinates — NOT string-matching district names.
    """
    if lat is None or lon is None:
        return None

    if index["engine"] == "shapely":
        from shapely.geometry import Point
        pt = Point(lon, lat)  # shapely is (x=lon, y=lat)
        for i in index["tree"].query(pt):  # STRtree pre-filters candidates by bounding box
            if index["shapes"][i].contains(pt):
                return index["meta"][i]
        return None

    # Pure-Python fallback: ray casting against each polygon's rings.
    for p in index["polys"]:
        if _point_in_geometry(lon, lat, p["geometry"]):
            return {"district": p["district"], "shapeID": p["shapeID"]}
    return None


def _point_in_geometry(x: float, y: float, geom: dict) -> bool:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    for poly in polys:
        if poly and _point_in_ring(x, y, poly[0]):  # outer ring; holes ignored (rare for districts)
            return True
    return False


def _point_in_ring(x: float, y: float, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def validate_setup() -> dict:
    try:
        import shapely  # noqa: F401
        shapely_ok = True
    except ImportError:
        shapely_ok = False
    return {
        "geojson_cached": GEOJSON_FILE.exists(),
        "shapely_installed": shapely_ok,  # if False, pure-Python fallback is used
        "cached_features": (len(json.loads(GEOJSON_FILE.read_text())["features"])
                            if GEOJSON_FILE.exists() else 0),
    }


if __name__ == "__main__":
    print("district_polygons setup:", json.dumps(validate_setup(), indent=2))
    fc = fetch_india_districts()
    idx = build_index(fc)
    # Ahmedabad, Gujarat (a known-good facility coordinate from the data gate).
    print("23.01, 72.56 ->", assign_district(23.01, 72.56, idx))
