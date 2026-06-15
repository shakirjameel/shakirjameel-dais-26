#!/usr/bin/env python3
"""Repo-hygiene gate: secrets, generated artifacts, and CI wiring.

Turns "should be ignored / should exist" from advice into an executable check
(diagnostics Tooling layer). Enforces C008 (secrets never tracked), the
generated-dashboard hygiene fix, and that CI exists. Standard library only (C006).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tracked(relative: str) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> int:
    issues: list[str] = []

    gitignore_path = ROOT / ".gitignore"
    gitignore = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    for pattern in (".env", "__pycache__", "node_modules", "context-dashboard.html"):
        if pattern not in gitignore:
            issues.append(f".gitignore is missing pattern: {pattern}")

    if not (ROOT / ".env.example").exists():
        issues.append("missing .env.example (required secrets must be documented)")

    if (ROOT / ".env").exists() and _tracked(".env"):
        issues.append(".env is tracked by git — secrets risk (C008)")

    if _tracked("docs/harness/context-dashboard.html"):
        issues.append("generated dashboard is tracked; gitignore + untrack it")

    if not (ROOT / ".github" / "workflows" / "ci.yml").exists():
        issues.append("missing CI workflow: .github/workflows/ci.yml")

    if issues:
        print("Repo hygiene check failed:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("Repo hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
