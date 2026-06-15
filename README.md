# Mission Copilot — Rung 0 core (deterministic spine)

Pure-Python grounding + cost layer. No LLM, no Databricks dependency. Runs locally tonight.

## Run
    python tests/test_core.py      # 15 unit tests (honesty rules + cost breakdown)
    python demo_chain.py           # end-to-end ranking on synthetic districts

## Files
- core/cost.py    — mission_cost(): transparent, adjustable, full breakdown (THE CENTERPIECE)
- core/burden.py  — burden_score (suppressed='*'->None, '(x)'->low-confidence),
                    people_reached (hedged heuristic), impact_per_cost (ranking metric)
- tests/test_core.py — proves: total = sum of parts; suppressed != zero;
                    low-confidence propagates; ranking direction correct
- demo_chain.py   — shows the signature beat: highest-burden district ranks LAST
                    because reach-time-cost kills its impact-per-dollar

## Next (Rung 0 cont. -> Rung 1)
1. Inspect real facilities schema -> fill INTERVENTION_INDICATORS with real NFHS-5 columns
2. Add geo.py (point-in-polygon district assignment; PIN dedupe to join grain)
3. Add ors_client.py (cached reachability matrix) -> replace synthetic distance/drive_hours
4. Ground COST_ASSUMPTIONS in real norms (label as assumptions)
5. Wrap the chain in the agent (rank + explain + brief) — Rung 1
