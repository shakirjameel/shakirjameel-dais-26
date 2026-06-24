#!/usr/bin/env python3
"""Context-harness gate for the Medical Desert Planner repository.

A single, self-contained CLI that keeps the Markdown "brain" in docs/harness/
honest: required files exist, the feature queue parses, every feature has a
valid id/state/verification command, the context-harness skill is real, and the
unit tests pass. Standard library only — no third-party dependencies.

The skill file owns the *what* and *when*; this script owns the *how*; the test
suite proves the *how* still works.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = ROOT / "docs" / "harness"
FEATURES_PATH = HARNESS_DIR / "features.md"
DASHBOARD_PATH = HARNESS_DIR / "context-dashboard.html"

START_MARKER = "<!-- harness:features:start -->"
END_MARKER = "<!-- harness:features:end -->"
VALID_STATES = ("not_started", "active", "blocked", "failing", "passing")
FEATURE_HEADER = ["id", "behavior", "verification", "state", "evidence"]

REQUIRED_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "Makefile",
    "scripts/harness.py",
    "scripts/test_harness.py",
    "docs/harness/context-map.md",
    "docs/harness/constraints.md",
    "docs/harness/decisions.md",
    "docs/harness/diagnostics.md",
    "docs/harness/evaluator-rubric.md",
    "docs/harness/features.md",
    "docs/harness/progress.md",
    "docs/harness/quality.md",
    "docs/harness/sprint-contract-template.md",
    "docs/harness/startup-readiness.md",
    "docs/harness/verification.md",
    "docs/harness/project-context.md",
    "docs/harness/graph.md",
    "docs/harness/teaching-protocol.md",
    "docs/harness/loops.md",
    "docs/harness/context-dashboard.html",
    ".env.example",
    ".github/workflows/ci.yml",
    ".claude/agents/explorer.md",
    ".claude/agents/implementer.md",
    ".claude/agents/verifier.md",
    ".claude/skills/context-harness/SKILL.md",
    ".claude/skills/autoreview/SKILL.md",
]


@dataclass
class Feature:
    id: str
    behavior: str
    verification: str
    state: str
    evidence: str


# --- markdown table parsing / rendering -------------------------------------

def split_markdown_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def table_escape(text: str) -> str:
    return text.replace("|", "\\|")


def strip_code_span(text: str) -> str:
    return text.strip().strip("`").strip()


def _is_separator(cells: list[str]) -> bool:
    joined = "".join(cells)
    return bool(joined) and set(joined) <= set("-: ")


def parse_features_text(text: str) -> list[Feature]:
    if START_MARKER not in text or END_MARKER not in text:
        raise ValueError("features.md is missing harness feature markers")
    block = text.split(START_MARKER, 1)[1].split(END_MARKER, 1)[0]
    features: list[Feature] = []
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = split_markdown_row(line)
        if cells == FEATURE_HEADER:
            continue
        if _is_separator(cells):
            continue
        if len(cells) != len(FEATURE_HEADER):
            continue
        features.append(Feature(*cells))
    return features


def load_features() -> tuple[str, list[Feature]]:
    text = FEATURES_PATH.read_text(encoding="utf-8")
    return text, parse_features_text(text)


def render_features(features: list[Feature]) -> str:
    lines = [
        "| " + " | ".join(FEATURE_HEADER) + " |",
        "| " + " | ".join("---" for _ in FEATURE_HEADER) + " |",
    ]
    for f in features:
        cells = [f.id, f.behavior, f.verification, f.state, f.evidence]
        lines.append("| " + " | ".join(table_escape(c) for c in cells) + " |")
    return "\n".join(lines)


def write_features(features: list[Feature]) -> None:
    text = FEATURES_PATH.read_text(encoding="utf-8")
    before = text.split(START_MARKER, 1)[0]
    after = text.split(END_MARKER, 1)[1]
    new = (
        before
        + START_MARKER
        + "\n"
        + render_features(features)
        + "\n"
        + END_MARKER
        + after
    )
    FEATURES_PATH.write_text(new, encoding="utf-8")


# --- queries ----------------------------------------------------------------

def find_feature(features: list[Feature], feature_id: str) -> Feature | None:
    for f in features:
        if f.id == feature_id:
            return f
    return None


def next_feature(features: list[Feature]) -> Feature | None:
    for state in ("active", "failing", "not_started"):
        for f in features:
            if f.state == state:
                return f
    return None


def evidence_signal(output: str) -> str:
    keywords = ("passed", "successfully", "wrote", "ok")
    for line in reversed(output.splitlines()):
        if any(k in line.lower() for k in keywords):
            return line.strip()
    return ""


# --- validation / tests -----------------------------------------------------

def validate_workspace(include_dashboard: bool = True) -> list[str]:
    issues: list[str] = []
    required = (
        REQUIRED_FILES
        if include_dashboard
        else [p for p in REQUIRED_FILES if p != "docs/harness/context-dashboard.html"]
    )
    for relative in required:
        if not (ROOT / relative).exists():
            issues.append(f"missing required file: {relative}")

    try:
        _, features = load_features()
    except Exception as exc:  # noqa: BLE001 - validation should report all context
        issues.append(str(exc))
        features = []

    seen: set[str] = set()
    for feature in features:
        if feature.id in seen:
            issues.append(f"duplicate feature id: {feature.id}")
        seen.add(feature.id)
        if not re.fullmatch(r"[A-Z]+[0-9]+", feature.id):
            issues.append(f"feature id should look like H001 or MDP001: {feature.id}")
        if feature.state not in VALID_STATES:
            issues.append(f"{feature.id} has invalid state: {feature.state}")
        if not strip_code_span(feature.verification):
            issues.append(f"{feature.id} is missing a verification command")
        if feature.state == "passing" and not feature.evidence.strip():
            issues.append(f"{feature.id} is passing without evidence")

    skill_path = ROOT / ".claude" / "skills" / "context-harness" / "SKILL.md"
    if skill_path.exists():
        skill_text = skill_path.read_text(encoding="utf-8")
        if "[TODO]" in skill_text:
            issues.append("context-harness skill still contains template TODO text")
        if "name: context-harness" not in skill_text:
            issues.append("context-harness skill frontmatter is missing its name")
        if "description:" not in skill_text:
            issues.append("context-harness skill frontmatter is missing its description")

    return issues


def run_unittests() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "scripts.test_harness"],
        cwd=ROOT,
        text=True,
    )
    return result.returncode


# --- dashboard --------------------------------------------------------------

def dashboard_html() -> str:
    _, features = load_features()
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for f in features:
        rows.append(
            "      <tr>"
            f"<td>{html.escape(f.id)}</td>"
            f"<td>{html.escape(f.behavior)}</td>"
            f"<td><code>{html.escape(strip_code_span(f.verification))}</code></td>"
            f'<td class="state state-{html.escape(f.state)}">{html.escape(f.state)}</td>'
            f"<td>{html.escape(f.evidence)}</td>"
            "</tr>"
        )
    body = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Medical Desert Planner — Context Harness Dashboard</title>
<style>
 body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; color: #1c1c1e; }}
 h1 {{ font-size: 1.4rem; }}
 table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
 th, td {{ border: 1px solid #d2d2d7; padding: 0.5rem 0.7rem; text-align: left; }}
 th {{ background: #1d3557; color: #fff; }}
 code {{ background: #f2f2f7; padding: 0.1rem 0.3rem; border-radius: 4px; }}
 .state {{ font-weight: 600; }}
 .state-passing {{ color: #1a7f37; }}
 .state-failing {{ color: #c1121f; }}
 .state-blocked {{ color: #b07d00; }}
 .state-active {{ color: #1d3557; }}
 .state-not_started {{ color: #6e6e73; }}
 footer {{ margin-top: 1.5rem; color: #6e6e73; font-size: 0.8rem; }}
</style>
</head>
<body>
<h1>Medical Desert Planner — Context Harness Dashboard</h1>
<p>Generated by <code>scripts/harness.py</code>. This file is regenerated on every
<code>make harness-check</code>; do not edit by hand.</p>
<table>
  <thead><tr><th>ID</th><th>Behavior</th><th>Verification</th><th>State</th><th>Evidence</th></tr></thead>
  <tbody>
{body}
  </tbody>
</table>
<footer>Generated {generated}</footer>
</body>
</html>
"""


def write_dashboard() -> None:
    DASHBOARD_PATH.write_text(dashboard_html(), encoding="utf-8")


# --- commands ---------------------------------------------------------------

def cmd_clock_in(args) -> int:
    _, features = load_features()
    nxt = next_feature(features)
    if nxt is None:
        print("No actionable feature. Add one to docs/harness/features.md.")
    else:
        print(f"Next feature: {nxt.id} — {nxt.behavior}")
        print(f"Verify with: {strip_code_span(nxt.verification)}")
    print("Run before done: make harness-check")
    return 0


def cmd_next_feature(args) -> int:
    _, features = load_features()
    nxt = next_feature(features)
    if nxt is None:
        print("No actionable feature.")
        return 0
    print(f"{nxt.id}\t{nxt.state}\t{nxt.behavior}")
    return 0


def cmd_dashboard(args) -> int:
    write_dashboard()
    print(f"wrote {DASHBOARD_PATH.relative_to(ROOT)}")
    return 0


def cmd_check(args) -> int:
    if not args.no_dashboard:
        write_dashboard()
    issues = validate_workspace(include_dashboard=not args.no_dashboard)
    if not args.skip_tests:
        test_code = run_unittests()
        if test_code != 0:
            return test_code
    if issues:
        print("Harness check failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("Harness check passed.")
    return 0


def cmd_clock_out(args) -> int:
    code = cmd_check(args)
    print("\nClock-out checklist:")
    for item in (
        "Progress updated",
        "Diagnostics updated for any failure",
        "Feature evidence updated through verify-feature when applicable",
        "No stale temporary artifacts intentionally left behind",
    ):
        print(f"- {item}")
    return code


def cmd_verify_feature(args) -> int:
    _, features = load_features()
    feature = find_feature(features, args.feature_id)
    if feature is None:
        print(f"unknown feature: {args.feature_id}", file=sys.stderr)
        return 1
    command = strip_code_span(feature.verification)
    if not command:
        print(f"{feature.id} has no verification command", file=sys.stderr)
        return 1
    print(f"Verifying {feature.id}: {command}")
    result = subprocess.run(
        command, cwd=ROOT, shell=True, text=True, capture_output=True
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode == 0:
        feature.state = "passing"
        signal = evidence_signal(result.stdout + "\n" + result.stderr)
        feature.evidence = signal or f"verified `{command}`"
        write_features(features)
        print(f"{feature.id} -> passing")
        return 0
    feature.state = "failing"
    write_features(features)
    print(f"{feature.id} -> failing", file=sys.stderr)
    return result.returncode or 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Medical Desert Planner context-harness gate")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("clock-in", help="print next feature and the closeout reminder").set_defaults(
        func=cmd_clock_in
    )
    sub.add_parser("next-feature", help="print the next actionable feature").set_defaults(
        func=cmd_next_feature
    )
    sub.add_parser("dashboard", help="regenerate the HTML dashboard").set_defaults(
        func=cmd_dashboard
    )

    check = sub.add_parser("check", help="run the harness gate (the closeout check)")
    check.add_argument("--no-dashboard", action="store_true")
    check.add_argument("--skip-tests", action="store_true")
    check.set_defaults(func=cmd_check)

    clock_out = sub.add_parser("clock-out", help="run check + print the clock-out checklist")
    clock_out.add_argument("--no-dashboard", action="store_true")
    clock_out.add_argument("--skip-tests", action="store_true")
    clock_out.set_defaults(func=cmd_clock_out)

    verify = sub.add_parser(
        "verify-feature", help="run a feature's verification command and record evidence"
    )
    verify.add_argument("feature_id")
    verify.set_defaults(func=cmd_verify_feature)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
