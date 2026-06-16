# 🩺 TrueNorth Health — Medical Desert Planner

**DAIS 2026 · Apps & Agents for Good (Track 2) · Virtue Foundation**

TrueNorth Health tells a **real care desert** (people, real need, no care) apart from a **data gap**
(we simply have no records) — and ranks where a volunteer medical mission does the **most good per
dollar**. It's a map-first Databricks App that turns noisy, web-scraped facility data into decisions a
non-technical mission planner can trust.

**Live app:** https://mission-copilot-7474644988049446.aws.databricksapps.com *(behind Google SSO)*

---

## The problem

Facility data is web-scraped and self-reported. A blank cell can mean two opposite things — a genuine
care desert, or just missing records — and treating them the same sends volunteer teams to the wrong
place. TrueNorth Health makes that distinction explicit, grades every facility's claim against its own
evidence, and only ever shows numbers it can trace to a source.

## What it does

- **Map-first coverage** across 6 capabilities (maternity, ICU, NICU, emergency, oncology, trauma) × any
  Indian state — district care-gap (desert) scores on a choropleth, with a per-district table.
- **Trust, not claims** — each facility's capability is graded `high` / `medium` / `unverified` against
  its own procedure/equipment text, with the exact cited words. Unverified claims never count as supply.
- **Deployment optimizer** — for a team of N volunteers based in a home city, ranks districts by
  *need addressed per dollar* (measured demand × unmet gap ÷ mission cost from that base).
- **🤖 AI recommendation column** — Databricks `ai_query` (an open Foundation Model) reasons over each
  district's *computed* numbers to suggest one concrete action the Foundation can actually take.
- **One AI copilot** — orchestrates the deterministic tools **and** runs **Genie** text-to-SQL for ad-hoc
  data questions; it only states numbers the tools or Genie returned.

## How it works

A **deterministic spine** does the math (auditable, reproducible); **AI adds judgment, never the numbers.**

- **Trust-weighted supply** = `high × 1.0 + medium × 0.6`
- **Adequacy saturates:** `adequacy = supply ÷ (supply + k)`
- **Care-gap (desert) score** = `demand × (1 − adequacy)` — `demand` is an honest NFHS-5 proxy where one
  exists, else ranked on supply scarcity and labelled as such.
- **Cost-per-impact** = `desert ÷ mission-cost`, where
  `mission-cost = 0.70·distance + 60·(team × days) + 200·(drive-hrs × team)` and `drive-hrs = distance ÷ 45`,
  so a closer district never costs more than a farther one (regression-tested).
- **Geography by coordinates** — facilities are resolved to districts by point-in-polygon (~99.98%
  coverage), then reconciled to NFHS-5 districts.

More detail lives in the in-app **"How does it all work?"** page and in [`VERIFICATION.md`](VERIFICATION.md)
(a full stress-test + live reconciliation: claims→coverage reconciles 4,164/4,164 exact).

## Built on Databricks (Free Edition)

- **Databricks Apps** — hosts the Streamlit app.
- **Unity Catalog + Delta Sharing** — reads the shared Virtue Foundation dataset.
- **Serverless Job (DABs)** — `data/job_ingest.py` resolves geography, grades claims, computes the
  analytics, runs `ai_query`, and publishes curated **UC Delta** tables (`workspace.mission_uc.*`).
- **Lakebase (Postgres)** — sub-10ms app reads + persists user actions (saved scenarios, reviews, notes).
- **SQL Warehouse (serverless) + Genie** — power the in-copilot text-to-SQL.
- **Foundation Model API (`ai_query`), Secrets, SDK (service-principal auth), DABs bundle** — config + deploy.

## Repo layout

```
app.py                # the Streamlit app (map, optimizer, copilot, dialogs)
mission_core/         # deterministic spine: coverage, claims, cost, burden, desert score, reach, impact
agent/                # the copilot: tools, orchestrator, prompts, Genie client, mission brief
data/                 # ingestion: job_ingest (UC Delta + ai_query), geo_resolve, load_lakebase, external/
tests/                # 64 tests (core + agent + genie) and re-runnable audits/
assets/               # bundled India state GeoJSON (offline map)
databricks.yml        # DABs bundle (app, ingest job, Lakebase, secret, Genie space)
app.yaml              # app runtime config
VERIFICATION.md       # stress-test + live reconciliation report
```

## Run & deploy

```bash
# Local tests (no Databricks needed; uses cached CSVs)
./.venv/bin/python tests/test_core.py && ./.venv/bin/python tests/test_agent.py && ./.venv/bin/python tests/test_genie.py   # 64/64

# On Databricks (DABs bundle)
databricks bundle deploy
databricks bundle run mission_ingest      # build curated UC Delta tables + the ai_query recommendations
python -m data.load_lakebase              # load Lakebase mission.* (run locally; OOMs on serverless)
databricks bundle run mission_copilot     # (re)start the app
```

Provider config is via `.env` (gitignored — see `.env.example`): the copilot is provider-agnostic
(OpenAI-compatible); on Databricks the AI column uses an open served model via `ai_query`.

## Honest limits & what's next

- **Relative, not absolute, need** — the source data has no population, so need is relative (configurable,
  never fabricated).
- **Free-Edition realities** — Databricks-served Claude/Gemini are rate-limited to 0 (copilot uses an
  external key via the same client; the AI column uses an open served model); the Lakebase bulk-load OOMs
  on a serverless Job, so the Job owns the UC-Delta/AI half and Lakebase loads locally.
- **Next:** a richer mission-cost model (multi-modal transport + place-specific lodging/boarding + live
  routing), absolute per-capita need, Mosaic AI Vector Search for semantic facility matching, scheduled
  data refresh + NFHS-6 trend, and a multi-turn copilot.

## Data & credit

Virtue Foundation facility dataset (via Databricks Marketplace / Delta Sharing) + NFHS-5 district health
indicators. Built for the DAIS 2026 "Apps & Agents for Good" hackathon.
