"""External-data connectors for the Mission Copilot.

Each external dependency lives behind ONE module with a cache and a graceful fallback, so
the live demo never depends on a network call (see context/architecture.md, Design rule).

- ors_client        : road reachability (ORS Matrix) + straight-line fallback
- district_polygons : India ADM2 boundaries (geoBoundaries) + point-in-polygon
- nfhs6_trend       : NFHS-6 state-level trajectory loader (resolution gap disclosed)
"""
