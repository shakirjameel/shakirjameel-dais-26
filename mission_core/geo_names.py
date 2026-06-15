"""
geo_names.py — reconcile our NFHS `state_ut` spellings with the map topology's `st_nm`.

The map's GeoJSON (assets/india_states.geojson, property `st_nm`) and our data disagree on a handful
of state spellings — a mismatched key renders a state grey, so we curate the join exactly (same
discipline as the district aliases in data/geo_resolve.py):
  - "&" vs "and"           — "Jammu & Kashmir" -> "Jammu and Kashmir"  (handled by normalize)
  - misspelling            — "Maharastra"      -> "Maharashtra"        (curated alias)
  - renamed/abbreviated    — "NCT of Delhi"    -> "Delhi"              (curated alias)

`to_topo_state` maps our value -> the GeoJSON `st_nm` (to colour the map); `from_topo_state` maps
back (to look up coverage from a clicked state). States with no match resolve to None.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

GEOJSON = Path(__file__).resolve().parent.parent / "assets" / "india_states.geojson"

# curated overrides, keyed on the NORMALIZED our-side name -> normalized topo name
STATE_ALIAS = {
    "maharastra": "maharashtra",      # misspelled in NFHS source
    "nct of delhi": "delhi",
}


def normalize_state(s: str) -> str:
    """Lowercase, '&'->'and', drop punctuation, collapse spaces — so spellings compare across sources."""
    if not s:
        return ""
    s = s.lower().strip().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _topo() -> tuple:
    """(list of st_nm, {normalized st_nm -> st_nm})."""
    feats = json.loads(GEOJSON.read_text())["features"]
    names = [f["properties"]["st_nm"] for f in feats]
    return names, {normalize_state(n): n for n in names}


def list_topo_states() -> list[str]:
    return list(_topo()[0])


def to_topo_state(state_ut: str) -> str | None:
    """Our NFHS `state_ut` -> the GeoJSON `st_nm`, or None if it doesn't map."""
    norm = normalize_state(state_ut)
    norm = STATE_ALIAS.get(norm, norm)
    return _topo()[1].get(norm)


@lru_cache(maxsize=1)
def _reverse() -> dict:
    """normalized topo name -> the alias-resolved key, for from_topo_state lookups."""
    rev = {}
    for our_norm, topo_norm in STATE_ALIAS.items():
        rev[topo_norm] = our_norm
    return rev


def from_topo_state(st_nm: str, our_states: list[str]) -> str | None:
    """A clicked GeoJSON `st_nm` -> the matching value in `our_states` (our data's spellings)."""
    target = normalize_state(st_nm)
    for s in our_states:
        n = normalize_state(s)
        if STATE_ALIAS.get(n, n) == target:
            return s
    return None
