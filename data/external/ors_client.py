"""
ors_client.py — Road reachability via OpenRouteService, with a guaranteed fallback.

WHY THIS EXISTS
    The cost-per-impact chain needs road travel time + distance from a staging point to
    each candidate district. We use OpenRouteService (ORS) Matrix (free, OSM-based).

THE NON-NEGOTIABLE DESIGN RULE (from context/architecture.md, Risk 3)
    All routing goes through ONE function (`reachability_matrix`) that:
      1. Returns cached results if present (we PRE-COMPUTE for demo districts — never
         call ORS live during the 3-minute demo).
      2. Calls ORS if an API key is present and the pair is not cached.
      3. Falls back to a straight-line (haversine) distance x road-circuity factor if ORS
         is unavailable or fails — LABELLED "estimated, straight-line" so the app never
         hard-fails on routing, and never presents an estimate as a real road measurement.

USAGE
    export ORS_API_KEY=...                      # free key: https://openrouteservice.org/sign-up/
    from data.external.ors_client import reachability_matrix
    m = reachability_matrix(
            origins=[(28.61, 77.20)],           # (lat, lon) staging city, e.g. Delhi
            destinations=[(19.07, 72.87), ...],  # district centroids
    )
    # m[(o_idx, d_idx)] -> {"distance_km", "duration_min", "source": "ors"|"straight_line"}
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "ors_matrix.json"
ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/{profile}"

# Road distance is longer than straight-line. ~1.3 is a common India-rural circuity factor.
CIRCUITY_FACTOR = 1.3
# Rural driving average speed (km/h) used to turn the estimated distance into a duration.
FALLBACK_SPEED_KMH = 40.0


# --------------------------------------------------------------------------- cache
def _load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _key(o: tuple[float, float], d: tuple[float, float]) -> str:
    # Round to ~100m so near-identical points reuse cached values.
    return f"{round(o[0],3)},{round(o[1],3)}->{round(d[0],3)},{round(d[1],3)}"


# --------------------------------------------------------------------------- math
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    r = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _straight_line(o: tuple[float, float], d: tuple[float, float]) -> dict:
    """Always-available estimate: haversine x circuity, labelled as such."""
    dist = haversine_km(o, d) * CIRCUITY_FACTOR
    return {
        "distance_km": round(dist, 2),
        "duration_min": round(dist / FALLBACK_SPEED_KMH * 60, 1),
        "source": "straight_line",  # honest label — NOT a real road measurement
    }


# --------------------------------------------------------------------------- ORS
def _ors_matrix(origins, destinations, profile, api_key) -> dict | None:
    """Call ORS Matrix once for the full many-to-many set. Returns None on any failure."""
    try:
        import requests  # local import so the module loads even without requests installed
    except ImportError:
        return None

    # Route SSL through the OS trust store if available (TLS-intercepting proxies, e.g. Zscaler).
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

    # ORS expects [lon, lat] order.
    locations = [[o[1], o[0]] for o in origins] + [[d[1], d[0]] for d in destinations]
    sources = list(range(len(origins)))
    dests = list(range(len(origins), len(origins) + len(destinations)))
    try:
        resp = requests.post(
            ORS_MATRIX_URL.format(profile=profile),
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={"locations": locations, "sources": sources, "destinations": dests,
                  "metrics": ["distance", "duration"], "units": "km"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        out = {}
        for i in range(len(origins)):
            for j in range(len(destinations)):
                dist = data["distances"][i][j]
                dur = data["durations"][i][j]
                if dist is None or dur is None:
                    # ORS couldn't route this pair (centroid not near a road) — fall back to
                    # straight-line for THIS pair only, not the whole batch.
                    out[(i, j)] = _straight_line(origins[i], destinations[j])
                else:
                    out[(i, j)] = {"distance_km": round(dist, 2),
                                   "duration_min": round(dur / 60, 1), "source": "ors"}
        return out
    except Exception as e:  # network, rate limit, bad key — degrade gracefully
        print(f"[ors_client] ORS call failed ({e}); falling back to straight-line.")
        return None


# --------------------------------------------------------------------------- public
def reachability_matrix(origins, destinations, profile="driving-car", use_cache=True) -> dict:
    """
    Many-to-many reachability. Returns {(origin_idx, dest_idx): {distance_km, duration_min, source}}.

    Resolution order per pair: cache -> ORS (if key + not cached) -> straight-line fallback.
    Pre-compute for the demo districts and commit the cache so the demo runs offline.
    """
    cache = _load_cache() if use_cache else {}
    api_key = os.environ.get("ORS_API_KEY")
    result, missing = {}, []

    for i, o in enumerate(origins):
        for j, d in enumerate(destinations):
            ck = _key(o, d)
            if use_cache and ck in cache:
                result[(i, j)] = cache[ck]
            else:
                missing.append((i, j, o, d, ck))

    if missing and api_key:
        # Batch all missing pairs into a single ORS Matrix call where possible.
        uniq_o = sorted({(o[0], o[1]) for _, _, o, _, _ in missing})
        uniq_d = sorted({(d[0], d[1]) for _, _, _, d, _ in missing})
        oi = {o: k for k, o in enumerate(uniq_o)}
        di = {d: k for k, d in enumerate(uniq_d)}
        matrix = _ors_matrix(uniq_o, uniq_d, profile, api_key)
        if matrix:
            for i, j, o, d, ck in missing:
                cell = matrix[(oi[(o[0], o[1])], di[(d[0], d[1])])]
                result[(i, j)] = cell
                cache[ck] = cell
            if use_cache:
                _save_cache(cache)
            missing = []

    # Anything still missing (no key, or ORS failed) -> straight-line estimate.
    for i, j, o, d, ck in missing:
        result[(i, j)] = _straight_line(o, d)

    return result


def validate_setup() -> dict:
    """Report whether ORS is usable right now (for the data-gate notebook / CI)."""
    return {
        "ors_api_key_present": bool(os.environ.get("ORS_API_KEY")),
        "requests_installed": _requests_available(),
        "cache_exists": CACHE_FILE.exists(),
        "cached_pairs": len(_load_cache()),
        "fallback_available": True,  # straight-line always works
    }


def _requests_available() -> bool:
    try:
        import requests  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    print("ORS setup:", json.dumps(validate_setup(), indent=2))
    # Smoke test the fallback (works with no key): Delhi -> Mumbai.
    m = reachability_matrix([(28.61, 77.20)], [(19.07, 72.87)], use_cache=False)
    print("Delhi -> Mumbai:", m[(0, 0)])
