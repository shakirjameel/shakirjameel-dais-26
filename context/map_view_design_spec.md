# Medical Desert Planner — Map View Design Spec

**For:** Claude Code
**Scope:** Add a modern, India-first geographic view to the existing Databricks App. Country → state drill-down only (no district polygons). All styling and data rules below. This replaces the table-first landing with a map-first one; the existing table, drill-down, decisions, and workspace logic are reused, not rebuilt.

**Non-negotiable principle (carried from the rest of the app):** the agent/UI decides over data, never invents it. A region with no facility rows renders as "no data yet" — never as score 0, never as good coverage. This is the whole point.

---

## 1. The geometry source (use real topology — do not hand-draw)

Two acceptable sources, in priority order. Verify before wiring data.

**Primary — D3 + TopoJSON (true clickable choropleth):**
- State boundaries: the `udit-001/india-maps-data` repo, served via jsDelivr. Country file with state-level features.
- Load with `d3.json(...)`, convert with `topojson.feature(topo, topo.objects[KEY])`.
- **Mandatory first step:** `curl` the topology file and print `features[0].properties`. Read the EXACT key (`st_nm`, `name`, `NAME_1`, etc.) and the EXACT state spellings. Key all data dictionaries on those literal strings. Do not guess — a mismatched key renders every state grey.
- Projection: `d3.geoMercator().fitExtent([[pad,pad],[W-pad,H-pad]], featureCollection)`.

**Fallback — static image backdrop (if topology won't load in the App's restricted network):**
- Wikimedia Commons file `India-locator-map-blank.svg` (CC BY-SA 3.0, free for commercial use, attribution required). Pre-download it during build, commit it to the app assets, reference locally. Do NOT hotlink `upload.wikimedia.org` at runtime.
- With the static image you cannot do true per-state fills, so overlay positioned markers/glow-dots at state centroids for the lit states only. Acceptable for a demo; the D3 route is better.

**Attribution:** if the Wikimedia asset is used, add a small credit line in the app footer: "India map © Wikimedia Commons, CC BY-SA 3.0."

---

## 2. The drill-down model (two levels, seamless)

```
INDIA view  ──click a lit state──▶  STATE view  ──click a district tile──▶ existing facility drill-down
     ▲                                    │
     └──────── breadcrumb "India" ────────┘
```

- One canvas, one breadcrumb (`India › Andhra Pradesh`). No tabs for navigation.
- The sidebar **State/UT** dropdown and the map are the SAME control — selecting a state in the dropdown drills the map; clicking the map updates the dropdown. Keep them in sync from one state variable. (This also fixes the long-standing scope mismatch where the optimizer ignored the sidebar.)
- The **Capability** selector re-renders whatever level is currently shown.
- Transition country↔state should ease (D3 projection re-fit with a transition, or a 200ms fade) — feel like zooming, not a page swap.

---

## 3. The five-state color vocabulary (identical at both levels)

Legend never changes between country and state view. Fills:

| State | Meaning | Fill (light) | Text on fill |
|---|---|---|---|
| strong coverage | low desert score | `#0F6E56` | `#E1F5EE` |
| moderate | mid desert score | `#1D9E75` | `#E1F5EE` |
| weaker coverage | high desert score | `#5DCAA5` | `#04342C` |
| claim only | claimed, not text-verified | `#EF9F27` | `#412402` |
| no-claim desert | nobody claims the capability | `#E24B4A` | `#FCEBEB` |
| no data yet | no facility rows at all | `#D3D1C7` (dark: `#3d3c37`) | muted |

Desert-score → coverage-shade thresholds (tune against real data): `<0.34` strong, `0.34–0.5` moderate, `>0.5` weaker. Document the thresholds as adjustable constants, same discipline as `COST_ASSUMPTIONS`.

**Dark mode:** every fill must have a dark-mode pair. Lit fills stay (they read on dark); the "no data" grey flips to `#3d3c37`. Strokes: white on lit states light-mode, `rgba(255,255,255,.2)` dark-mode.

---

## 4. The modern treatment (this is the "impactful" part)

1. **Glow on lit states only.** SVG `feDropShadow` with `stdDeviation ~5`, `flood-color` = that state's own fill, `flood-opacity ~0.55`. Emphasis through light, not heavier borders. No-data states get a thin flat stroke, no glow.
2. **Flat muted base.** No-data states sit quiet and low-contrast so the lit ones pop. The map should read at a glance as "most of India is unknown; these few states are known."
3. **No gradients, no drop-shadows on cards, no neon.** Flat surfaces, generous whitespace, one accent.
4. **Capability as a pill**, not a bare dropdown label — signals it's a live filter (`border-radius:999px`, info-tint background).
5. **Right-hand stat rail** (desktop) beside the map: metric cards for "states with data (N / 36)", a short clickable "drill into a state" list, and "no-claim deserts (N)". Turns dead space into a scannable summary.
6. **Typography:** one sans family, two weights (400/500), tight letter-spacing on the big numbers (`-0.02em`). Sentence case everywhere. No ALL CAPS.
7. **State labels** only on lit states (centroid text, white, 500 weight). Muted states reveal their name on hover/tooltip only — keeps the canvas clean.

---

## 5. State view layout

- Left: the selected state's outline as a small anchor (D3 single-feature, same fill as it had on the national map) + a one-line summary ("13 districts · state trust score 0.41").
- Right: district **tile-grid** — one tile per district, shaded by the same five-state vocabulary, each tile showing district name, "{verified}/{facilities} verified" (or "no facility claims it" for deserts), and the score. Tiles are clickable → existing facility drill-down.
- Below: the three KPI metric cards (confirmed / claim only / no-claim desert counts) + the existing ranked district table.
- District tiles are used here (not polygons) by design — avoids the AP 13-vs-26 district-boundary vintage problem entirely. Keys purely on Lakebase district names.

---

## 6. Data the map needs from Lakebase (queries to build)

1. **Lit-vs-dark set:** `SELECT DISTINCT state FROM facilities` (optionally × capability). Any state in this set is "lit"; everything else in the topology renders "no data yet." Drive this dynamically — never hardcode the lit list.
2. **State rollup fill:** for each lit state, aggregate its district desert scores into one state-level value. DECISION TO MAKE (pick and document one):
   - mean district score, OR
   - worst district score, OR
   - share of districts that are no-claim deserts.
   Recommendation: a state shows the desert color if it contains ANY no-claim desert (so deserts never hide under an average); otherwise shade by mean district score. State view always shows the true per-district breakdown regardless.
3. **Capability list:** confirm the dropdown is `SELECT DISTINCT capability` from facility data with NO hardcoded allow-list, NO silent `LIMIT`. The capability set is itself extracted data — show what's actually there (it's fine if that's fewer than older screenshots; that's honest). Optionally annotate each with its claim count.
4. **District rows per (state, capability):** name, verified count, total facilities, trust_ratio, desert_score, gap classification (confirmed / claim-only / desert). This already exists — reuse it.

---

## 7. Bug to fix while in here

The "Your decisions" / notes / "Pin to shortlist" block currently renders TWICE — once in the district drill-down (correct, district-scoped) and once orphaned at the bottom of a tab in white space (the visible breakage in the latest screenshot). Keep ONLY the district-scoped instance. Remove the stray one. The "My workspace" tab stays as the read-only review of everything saved.

---

## 8. Acceptance checks

- [ ] Topology `properties` key confirmed by inspection; no grey-hole states among the lit set.
- [ ] A state with zero facility rows renders "no data yet", never score 0.
- [ ] Capability dropdown derives from data with no hardcoded cap.
- [ ] Sidebar State dropdown and map click stay in sync from one variable.
- [ ] Legend identical at both levels; dark mode verified for all six fills.
- [ ] Orphaned decisions block removed; only district-scoped one remains.
- [ ] State rollup rule chosen and documented as an adjustable constant.
- [ ] If Wikimedia image used, attribution line present.

---

## 9. Reference mockups

Two HTML mockups were produced alongside this spec to convey the intent:
- The functional drill-down (stylized rectangles, full interaction, real data shape).
- The modern treatment (glow, stat rail, capability pill, muted base).

Both use MOCK data and a STAND-IN map. This spec is the source of truth for behavior and styling; the mockups are visual reference only. Real geometry + live Lakebase queries replace the stand-ins.
