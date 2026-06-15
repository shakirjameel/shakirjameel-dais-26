#!/usr/bin/env python3
"""Token / cost ledger for agent runs (supports constraint C010 cost ceiling).

Append a record per agent run, report totals, and optionally fail when spend
exceeds a ceiling — so an unattended loop cannot silently burn budget. The
ledger is runtime data (gitignored); this module is harness tooling and stays
standard library only (C006).

Usage:
  python3 scripts/cost_ledger.py record --model claude-opus-4-8 \
      --tokens-in 1200 --tokens-out 800 --usd 0.04 --label MDP001-build
  python3 scripts/cost_ledger.py report
  python3 scripts/cost_ledger.py check --ceiling 25.0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "eval" / "cost_ledger.jsonl"


def _records() -> list[dict]:
    if not LEDGER.exists():
        return []
    out: list[dict] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def totals(records: list[dict]) -> dict:
    return {
        "runs": len(records),
        "tokens_in": sum(int(r.get("tokens_in", 0)) for r in records),
        "tokens_out": sum(int(r.get("tokens_out", 0)) for r in records),
        "usd": round(sum(float(r.get("usd", 0.0)) for r in records), 4),
    }


def record(model: str, tokens_in: int, tokens_out: int, usd: float, label: str) -> int:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "usd": round(usd, 6),
        "label": label,
    }
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(f"recorded {label}: {tokens_in}+{tokens_out} tok, ${usd:.4f}")
    return 0


def report() -> int:
    print(json.dumps(totals(_records()), indent=2))
    return 0


def check(ceiling: float) -> int:
    spent = totals(_records())["usd"]
    if spent > ceiling:
        print(f"cost ceiling exceeded: ${spent} > ${ceiling}", file=sys.stderr)
        return 1
    print(f"cost ok: ${spent} <= ${ceiling}")
    return 0


def selftest() -> int:
    sample = [
        {"tokens_in": 10, "tokens_out": 5, "usd": 0.01},
        {"tokens_in": 20, "tokens_out": 7, "usd": 0.02},
    ]
    t = totals(sample)
    assert t["runs"] == 2, t
    assert t["tokens_in"] == 30 and t["tokens_out"] == 12, t
    assert abs(t["usd"] - 0.03) < 1e-9, t
    print("cost_ledger selftest passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent token/cost ledger")
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    rec = sub.add_parser("record", help="append a run to the ledger")
    rec.add_argument("--model", default="unknown")
    rec.add_argument("--tokens-in", type=int, default=0)
    rec.add_argument("--tokens-out", type=int, default=0)
    rec.add_argument("--usd", type=float, default=0.0)
    rec.add_argument("--label", default="run")

    sub.add_parser("report", help="print token/cost totals as JSON")

    chk = sub.add_parser("check", help="fail if spend exceeds a ceiling")
    chk.add_argument("--ceiling", type=float, required=True)

    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.cmd == "record":
        return record(args.model, args.tokens_in, args.tokens_out, args.usd, args.label)
    if args.cmd == "check":
        return check(args.ceiling)
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
