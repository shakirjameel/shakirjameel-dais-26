"""
mission_core — the deterministic cost-per-impact spine. No LLM, no Databricks dependency.

The agent decides over these grounded facts; it never computes or invents them.
  burden.py   — NFHS-5 burden scoring (suppressed/low-confidence honesty rules)
  coverage.py — coverage gap (burden x low reachable supply)
  cost.py     — transparent, adjustable mission cost (full breakdown)
  impact.py   — need-addressed-per-cost ranking metric (+ population-gated people_reached)
  data_access — THE single data source (district_base.csv now; Lakebase later)
  chain.py    — orchestrates the chain -> ranked districts (reachability injected)
"""
