#!/usr/bin/env python3
"""Recall@k for retrieval evaluation (generic metrics harness).

The eval half of the hybrid stack: the TS app produces retrieval results, this
computes the metric. Reads a golden set (JSONL: one object per query with a list
of relevant ids) and a predictions file (JSONL: query + ranked retrieved ids),
joins them by query, and reports mean Recall@k. Standard library only, so it runs
in CI before any app dependencies are installed.

  python3 eval/recall_at_k.py --selftest
  python3 eval/recall_at_k.py --golden eval/datasets/golden.example.jsonl \
      --predictions runs/preds.jsonl --k 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def recall_at_k(relevant: list[str], retrieved: list[str], k: int) -> float:
    rel = set(relevant)
    if not rel:
        return 0.0
    hits = sum(1 for doc_id in retrieved[:k] if doc_id in rel)
    return hits / len(rel)


def mean_recall_at_k(golden: list[dict], predictions: list[dict], k: int) -> float:
    preds = {row["query"]: row.get("retrieved", []) for row in predictions}
    scores = [
        recall_at_k(g.get("relevant_ids", []), preds.get(g["query"], []), k)
        for g in golden
    ]
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def selftest() -> int:
    assert recall_at_k(["a", "b"], ["a", "x", "b"], 3) == 1.0
    assert recall_at_k(["a", "b"], ["a", "x", "y"], 3) == 0.5
    assert recall_at_k(["a"], ["x", "y"], 2) == 0.0
    golden = [
        {"query": "q1", "relevant_ids": ["d1", "d2"]},
        {"query": "q2", "relevant_ids": ["d9"]},
    ]
    preds = [
        {"query": "q1", "retrieved": ["d1", "d5", "d7"]},  # finds d1, misses d2 -> 0.5
        {"query": "q2", "retrieved": ["d3", "d9"]},          # finds d9 -> 1.0
    ]
    assert mean_recall_at_k(golden, preds, 3) == 0.75, mean_recall_at_k(golden, preds, 3)
    print("recall_at_k selftest passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recall@k retrieval eval")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--golden", type=Path)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--min-recall", type=float, default=None,
                        help="fail (exit 1) if mean Recall@k is below this")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.golden or not args.predictions:
        parser.error("--golden and --predictions are required (or use --selftest)")

    score = mean_recall_at_k(_load_jsonl(args.golden), _load_jsonl(args.predictions), args.k)
    print(f"mean Recall@{args.k}: {score}")
    if args.min_recall is not None and score < args.min_recall:
        print(f"below threshold {args.min_recall}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
