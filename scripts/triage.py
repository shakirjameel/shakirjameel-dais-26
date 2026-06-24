#!/usr/bin/env python3
"""Deterministic triage for the Medical Desert Planner loop.

Prints a Markdown briefing: gate status, the next actionable feature, failing
features, recent commits, and uncommitted files. The agentic loop (a scheduled
automation, ``/loop``, or ``/goal``) calls this to get a grounded starting point
instead of re-deriving repo state every run. Standard library only (C006).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402

ROOT = harness.ROOT


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, text=True, capture_output=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - triage must never crash the loop
        return ""


def brief() -> str:
    _, features = harness.load_features()
    nxt = harness.next_feature(features)
    failing = [f for f in features if f.state == "failing"]
    issues = harness.validate_workspace(include_dashboard=False)

    lines = ["# Triage brief", ""]
    lines.append(f"- Gate: {'clean' if not issues else f'{len(issues)} issue(s)'}")
    if nxt is not None:
        lines.append(f"- Next feature: {nxt.id} — {nxt.behavior} ({nxt.state})")
        lines.append(f"  - verify: `{harness.strip_code_span(nxt.verification)}`")
    else:
        lines.append("- Next feature: none actionable")
    if failing:
        lines.append("- Failing: " + ", ".join(f.id for f in failing))
    if issues:
        lines.append("")
        lines.append("## Gate issues")
        lines += [f"- {i}" for i in issues]

    recent = _git("log", "--oneline", "-5")
    if recent:
        lines += ["", "## Recent commits", "```", recent, "```"]
    dirty = _git("status", "--short")
    if dirty:
        lines += ["", "## Uncommitted", "```", dirty, "```"]
    return "\n".join(lines) + "\n"


def selftest() -> int:
    text = brief()
    assert "# Triage brief" in text, "brief missing header"
    assert "Gate:" in text, "brief missing gate status"
    print("triage selftest passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic loop triage")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    sys.stdout.write(brief())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
