"""
nfhs6_trend.py — NFHS-6 (2023-24) state-level trajectory ("is this region improving?").

WHY THIS EXISTS
    Provided NFHS-5 data is 2019-21 and static. To answer "is this region getting better or
    worse?" we layer NFHS-6 (2023-24). IMPORTANT RESOLUTION CAVEAT (from context/use_case.md):
    NFHS-6 was released at NATIONAL and STATE/UT level; clean district-level CSVs were not
    reliably available as of mid-2026. So:
        - district burden  -> NFHS-5 (706 districts)
        - state trajectory -> NFHS-6 (state level only)
    The resolution gap is a VISIBLE uncertainty the app must disclose, not paper over.

SOURCE
    NFHS-6 fact sheets: https://www.nfhsiips.in/  and data.gov.in NFHS-6 catalog.
    These are published as PDFs / fact-sheet tables; a clean machine-readable state CSV must be
    assembled from the fact sheets. This module loads such a CSV once it exists and computes the
    trend; it does NOT fabricate values when NFHS-6 is unavailable.

USAGE
    from data.external.nfhs6_trend import load_nfhs6_state, compute_state_trend
    nfhs6 = load_nfhs6_state()                       # reads cache/nfhs6_state.csv (you supply it)
    trend = compute_state_trend(nfhs5_state_means, nfhs6, "institutional_birth_5y_pct")
    # trend[state] -> {"nfhs5", "nfhs6", "direction": improving|worsening|flat|unknown}
"""

from __future__ import annotations

import csv
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
NFHS6_CSV = CACHE_DIR / "nfhs6_state.csv"

# A change smaller than this (percentage points) is treated as "flat" — within survey noise.
FLAT_THRESHOLD_PP = 1.0


def load_nfhs6_state(path: Path = NFHS6_CSV) -> dict:
    """
    Load NFHS-6 state-level indicators from a CSV you assemble from the fact sheets.
    Expected header: state_ut, <indicator_1>, <indicator_2>, ...
    Returns {state_ut: {indicator: float}}. Returns {} (and a clear message) if absent —
    the app then shows "NFHS-6 trajectory unavailable", never a guessed trend.
    """
    if not path.exists():
        print(f"[nfhs6_trend] {path} not found. Assemble NFHS-6 state fact-sheet values into a CSV "
              f"(header: state_ut, <indicator columns>) to enable the trajectory layer.")
        return {}

    out: dict[str, dict[str, float]] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            state = (row.get("state_ut") or "").strip()
            if not state:
                continue
            vals = {}
            for k, v in row.items():
                if k == "state_ut" or v is None:
                    continue
                try:
                    vals[k] = float(str(v).strip().strip("()"))  # tolerate '(x)' low-confidence
                except ValueError:
                    pass  # suppressed / non-numeric -> omit (never coerce to 0)
            out[state] = vals
    print(f"[nfhs6_trend] loaded NFHS-6 state values for {len(out)} states/UTs.")
    return out


def _direction(nfhs5: float, nfhs6: float, higher_is_better: bool) -> str:
    delta = nfhs6 - nfhs5
    if abs(delta) < FLAT_THRESHOLD_PP:
        return "flat"
    improved = (delta > 0) if higher_is_better else (delta < 0)
    return "improving" if improved else "worsening"


def compute_state_trend(nfhs5_state: dict, nfhs6_state: dict,
                        indicator: str, higher_is_better: bool = True) -> dict:
    """
    Compare an indicator between NFHS-5 (state means) and NFHS-6 (state values).

    Args:
        nfhs5_state: {state_ut: value}  (aggregate NFHS-5 districts to state mean first)
        nfhs6_state: output of load_nfhs6_state()
        higher_is_better: e.g. institutional birth % -> True; anaemia % -> False.

    Returns {state_ut: {nfhs5, nfhs6, direction}}. direction='unknown' when NFHS-6 lacks it.
    """
    out = {}
    for state, v5 in nfhs5_state.items():
        v6map = nfhs6_state.get(state, {})
        v6 = v6map.get(indicator)
        if v5 is None or v6 is None:
            out[state] = {"nfhs5": v5, "nfhs6": v6, "direction": "unknown"}
        else:
            out[state] = {"nfhs5": v5, "nfhs6": v6,
                          "direction": _direction(v5, v6, higher_is_better)}
    return out


def validate_setup() -> dict:
    return {
        "nfhs6_csv_present": NFHS6_CSV.exists(),
        "note": "district-level NFHS-6 not reliably available; trajectory is STATE-level only "
                "(resolution gap must be disclosed in the app).",
    }


if __name__ == "__main__":
    import json
    print("nfhs6_trend setup:", json.dumps(validate_setup(), indent=2))
