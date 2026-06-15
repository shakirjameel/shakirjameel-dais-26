"""
india_geo.py — build the bundled India state GeoJSON the map view renders.

WHY bundle (not hotlink): Databricks Apps run with restricted egress; we must NOT fetch a CDN at
runtime. So this BUILD-TIME script pulls the open `udit-001/india-maps-data` district GeoJSON once,
dissolves districts → state polygons with shapely (already a dev dep), lightly simplifies, and writes
`assets/india_states.geojson` (36 states, property `st_nm`). The app reads that local file → offline.

The `st_nm` spellings here are the join key the app reconciles our NFHS `state_ut` against
(see mission_core/geo_names.py) — e.g. topo "Maharashtra" vs our data "Maharastra".

RUN (build-time, local; output is committed):
    ./.venv/bin/python -m data.external.india_geo
"""

from __future__ import annotations

import json
from pathlib import Path

import truststore
truststore.inject_into_ssl()
import requests
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

SRC = "https://cdn.jsdelivr.net/gh/udit-001/india-maps-data@main/geojson/india.geojson"
ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
OUT = ASSETS / "india_states.geojson"
SIMPLIFY_TOL = 0.01   # ~1km; keeps the file small without visibly distorting a national choropleth


def main() -> None:
    print(f"fetching district geometry: {SRC}")
    districts = requests.get(SRC, timeout=60).json()
    by_state: dict[str, list] = {}
    for feat in districts["features"]:
        st = feat["properties"].get("st_nm")
        if not st:
            continue
        by_state.setdefault(st, []).append(shape(feat["geometry"]))

    feats = []
    for st_nm in sorted(by_state):
        geom = unary_union(by_state[st_nm]).simplify(SIMPLIFY_TOL, preserve_topology=True)
        feats.append({"type": "Feature", "properties": {"st_nm": st_nm}, "geometry": mapping(geom)})

    ASSETS.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"wrote {len(feats)} states -> {OUT}  ({OUT.stat().st_size // 1024} KB)")
    print("states:", ", ".join(f["properties"]["st_nm"] for f in feats))


if __name__ == "__main__":
    main()
