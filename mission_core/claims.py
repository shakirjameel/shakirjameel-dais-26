"""
claims.py — Treat the facility free-text as CLAIMS to verify, not ground truth.

This is the summit's core discipline ("treat noisy fields as claims to verify, not ground truth";
"cite the underlying facility text"). The FDR pipeline (web crawl -> GenAI extraction) fills each
facility's capability / procedure / equipment / description with uneven free-text, and a structured
`maternal_supply` flag derived from `specialties`. That flag is ITSELF an extracted claim — and it
is noisy: e.g. an eye hospital can carry maternal_supply=1 from a stray specialty token.

So for a chosen intervention we grade each facility's claim by CORROBORATION across its own fields:

  high       — the capability/description text CLAIMS the service AND the procedure/equipment text
               CORROBORATES it with a service-specific signal (a delivery, a C-section, an NICU…).
  medium     — the capability/description text claims the service, but nothing in procedure/
               equipment corroborates it (a claim on the facility's own say-so, uncorroborated).
  unverified — the structured flag asserts the service, but the facility's OWN narrative does not
               claim it and nothing corroborates it (likely extraction noise — never a fact).
  none       — neither the flag nor the text indicates the service (not a relevant facility).

Deliberately transparent keyword matching (not a model): every grade cites the exact matched text,
so the app and brief can show WHY a claim was graded — and so a planner can overrule it.
"""

from __future__ import annotations

import json
import re

# Per CAPABILITY: the words that CLAIM the service (in capability/description/specialties) and the
# service-SPECIFIC words that CORROBORATE it procedurally (in procedure/equipment). Corroborators
# are intentionally narrow/clinical — generic "ultrasound"/"scan"/"delivery" are excluded because
# they are not specific to one capability and would over-credit the claim. Each grade cites the
# exact matched text, so the app can show WHY it was graded and a planner can overrule it.
CAPABILITY_TERMS = {
    "maternity": {
        "claim": [
            "obstetric", "obstetrics", "gynaecolog", "gynecolog", "ob/gyn", "obgyn",
            "maternity", "maternal", "antenatal", "prenatal", "childbirth", "midwif",
            "labour ward", "labor ward", "labour room", "labor room", "birthing",
        ],
        # Childbirth/neonatal-SPECIFIC procedural evidence. Bare "delivery" is excluded (matches
        # radiation/drug "delivery"); "obstetric" is excluded (it is the claim noun — circular).
        "corroborate": [
            "caesarean", "cesarean", "c-section", "lscs", "normal delivery", "vaginal delivery",
            "deliveries conducted", "childbirth", "labour room", "labor room", "labour ward",
            "labor ward", "neonatal", "nicu", "newborn", "postnatal", "post-natal", "antenatal",
            "forceps", "vacuum extraction", "incubator", "fetal monitor", "foetal", "birthing",
            "labour analgesia", "labor analgesia", "midwif",
        ],
    },
    "icu": {
        "claim": [
            "intensive care", "critical care", "intensive care unit", "high dependency",
            "icu", "hdu", "intensivist",
        ],
        "corroborate": [
            "ventilator", "mechanical ventilation", "ventilated", "invasive ventilation",
            "life support", "central line", "multipara monitor", "multi-para monitor",
            "infusion pump", "hemodynamic", "haemodynamic", "icu bed", "intensive care bed",
            "arterial line", "inotrop",
        ],
    },
    "nicu": {
        "claim": [
            "neonatal intensive", "nicu", "newborn intensive", "neonatology", "neonatal care",
            "newborn icu", "level iii neonatal", "special newborn care",
        ],
        "corroborate": [
            "incubator", "neonatal ventilator", "infant warmer", "radiant warmer", "phototherapy",
            "cpap", "surfactant", "neonatal bed", "level iii", "level 3 nicu", "neonatal monitor",
        ],
    },
    "emergency": {
        "claim": [
            "emergency", "casualty", "accident and emergency", "emergency department",
            "trauma centre", "trauma center", "24x7 emergency", "round the clock emergency",
        ],
        "corroborate": [
            "ambulance", "resuscitation", "trauma bay", "emergency surgery", "defibrillator",
            "triage", "crash cart", "24x7", "24/7", "round-the-clock", "golden hour", "casualty",
        ],
    },
    "oncology": {
        "claim": [
            "oncolog", "cancer", "tumour", "tumor", "malignanc", "haemato-oncolog",
            "hemato-oncolog", "cancer care", "cancer hospital",
        ],
        "corroborate": [
            "chemotherapy", "radiation therapy", "radiotherapy", "linear accelerator", "linac",
            "brachytherapy", "pet-ct", "pet ct", "tumor board", "tumour board", "cancer surgery",
            "immunotherapy", "bone marrow transplant", "cyberknife", "oncolog surgery",
        ],
    },
    "trauma": {
        "claim": [
            "trauma", "polytrauma", "accident care", "orthopaedic trauma", "orthopedic trauma",
            "trauma centre", "trauma center",
        ],
        "corroborate": [
            "trauma surgery", "orthopedic surgery", "orthopaedic surgery", "fracture fixation",
            "internal fixation", "external fixator", "trauma bay", "fracture", "plating",
            "spinal fixation", "reduction and fixation", "damage control",
        ],
    },
}

# Friendly labels for the UI / agent.
CAPABILITY_LABELS = {
    "maternity": "Maternity / Ob-Gyn", "icu": "ICU", "nicu": "NICU (neonatal ICU)",
    "emergency": "Emergency", "oncology": "Oncology", "trauma": "Trauma",
}
CAPABILITIES = list(CAPABILITY_TERMS)

# Back-compat: the cost chain + older callers use the burden key "maternal_health" for the maternal
# capability. Map it to "maternity" so one capability vocabulary serves both.
_CAPABILITY_ALIAS = {"maternal_health": "maternity"}


def _resolve_capability(name: str) -> str:
    return _CAPABILITY_ALIAS.get(name, name)


def _items(text: str) -> list[str]:
    """Split a free-text field (stored as a JSON-array string, sometimes truncated) into items.
    Falls back to splitting on the array delimiter if the JSON is truncated/unparseable."""
    if not text:
        return []
    s = text.strip()
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x) for x in v if x]
        return [str(v)]
    except (ValueError, TypeError):
        # truncated array like '["a","b","c' — recover the items between the quote-comma delimiters
        s = s.lstrip("[").rstrip("]")
        parts = re.split(r'"\s*,\s*"', s)
        return [p.strip().strip('"').strip() for p in parts if p.strip().strip('"').strip()]


def _first_hit(items: list[str], terms: list[str]) -> tuple[str | None, str | None]:
    """First (matched_term, citable item snippet) where any term occurs in an item."""
    for it in items:
        low = it.lower()
        for t in terms:
            if t in low:
                snippet = it.strip()
                return t, (snippet[:160] + "…" if len(snippet) > 160 else snippet)
    return None, None


def _all_hits(items: list[str], terms: list[str]) -> list[str]:
    blob = " ".join(items).lower()
    return sorted({t for t in terms if t in blob})


def classify_claim(facility: dict, capability: str = "maternity") -> dict:
    """Grade one facility's claim for a CAPABILITY. `facility` carries the free-text columns
    (capability/procedure/equipment/description/specialties as JSON-array strings). Claim source is
    capability+description+specialties; corroboration is procedure+equipment. Returns the grade + the
    matched terms + citable evidence snippets (never invents text). Accepts the "maternal_health"
    alias for the maternity capability.

    The maternity capability also honours the derived `maternal_supply` flag (an extracted claim):
    when the flag is set but the facility's own text doesn't claim maternity, the grade is
    'unverified' — the flag's word with nothing behind it, surfaced not trusted."""
    capability = _resolve_capability(capability)
    terms = CAPABILITY_TERMS.get(capability)
    flag = int(facility.get("maternal_supply") or 0) if capability == "maternity" else 0
    if terms is None:
        return {"capability": capability, "confidence": "not_applicable",
                "claimed": bool(flag), "corroborated": False,
                "claim_terms": [], "corroborating_terms": [],
                "capability_evidence": None, "procedure_evidence": None, "flag": flag}

    claim_items = (_items(facility.get("capability")) + _items(facility.get("description"))
                   + _items(facility.get("specialties")))
    corrob_items = _items(facility.get("procedure")) + _items(facility.get("equipment"))

    claim_terms = _all_hits(claim_items, terms["claim"])
    corrob_terms = _all_hits(corrob_items, terms["corroborate"])
    claimed = bool(claim_terms)
    corroborated = bool(corrob_terms)

    if claimed and corroborated:
        conf = "high"
    elif claimed:
        conf = "medium"
    elif flag:
        conf = "unverified"
    else:
        conf = "none"

    _, cap_ev = _first_hit(claim_items, terms["claim"])
    _, proc_ev = _first_hit(corrob_items, terms["corroborate"])
    return {
        "capability": capability, "confidence": conf,
        "claimed": claimed, "corroborated": corroborated, "flag": flag,
        "claim_terms": claim_terms, "corroborating_terms": corrob_terms,
        "capability_evidence": cap_ev, "procedure_evidence": proc_ev,
    }


# Verified = the facility's own narrative claims the service (corroborated or not). "unverified"
# (flag only) is deliberately NOT counted as supply — that is the honesty beat.
VERIFIED = {"high", "medium"}


def summarize_claims(facilities: list[dict], capability: str = "maternity") -> dict:
    """Aggregate per-facility grades for a district. Returns counts per grade, the text-verified
    supply count (high+medium), and a representative high/medium facility's cited evidence."""
    counts = {"high": 0, "medium": 0, "unverified": 0, "none": 0}
    best = None
    for f in facilities:
        c = classify_claim(f, capability)
        conf = c["confidence"]
        if conf in counts:
            counts[conf] += 1
        if best is None and conf in ("high", "medium"):
            best = {**c, "unique_id": f.get("unique_id")}
        elif best is not None and best["confidence"] == "medium" and conf == "high":
            best = {**c, "unique_id": f.get("unique_id")}  # prefer a high-confidence exemplar
    return {
        "n_facilities": len(facilities),
        "high": counts["high"], "medium": counts["medium"],
        "unverified": counts["unverified"], "none": counts["none"],
        "verified_supply": counts["high"] + counts["medium"],
        "best_evidence": best,
    }
