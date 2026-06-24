# Eval

The Python half of the hybrid stack: retrieval/extraction metrics that grade the
TypeScript app. Kept stdlib-only so it runs in CI before app deps install.

- `recall_at_k.py` — mean Recall@k from a golden set + predictions (`--selftest`).
- `datasets/golden.example.jsonl` — example golden labels (query → relevant ids).
  Replace with a real labeled set per domain; one object per line:
  `{"query": "...", "relevant_ids": ["doc#section", ...]}`.
- `cost_ledger.jsonl` — runtime token/cost ledger (gitignored), written by
  `scripts/cost_ledger.py`.

Per C009, new retrieval/extraction/scoring behavior ships with an eval here, not
just a demo. A feature's verification can chain its test with `recall_at_k.py`.
