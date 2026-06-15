#!/usr/bin/env python3
"""Git workflow automation (C016): auto-commit, feature branches, release branches, PRs.

Encodes the branching policy in docs/harness/git-workflow.md as executable
commands instead of advice. Standard library only (C006).

Commands:
  auto-commit [-m MSG]   stage everything and commit (no-op when clean)
  start-feature NAME     create feature/<name> from main
  cut-release [NAME]     create release/<name or today> from main
  open-pr [--base BASE]  push current branch and open a PR via gh
  --selftest             validate the pure helpers
"""
from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=check
    )


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a branch slug from {name!r}")
    return slug


def feature_branch_name(name: str) -> str:
    return f"feature/{slugify(name)}"


def release_branch_name(name: str | None, today: datetime.date) -> str:
    return f"release/{slugify(name) if name else today.isoformat()}"


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def auto_commit(message: str | None) -> int:
    if not _git("status", "--porcelain").stdout.strip():
        print("auto-commit: working tree clean, nothing to do")
        return 0
    branch = current_branch()
    if branch == "HEAD":
        print("auto-commit: refusing to commit on a detached HEAD", file=sys.stderr)
        return 1
    _git("add", "-A")
    summary = _git("diff", "--cached", "--stat").stdout.strip().splitlines()
    subject = message or f"Auto-commit work in progress on {branch}"
    body = summary[-1] if summary else ""
    _git("commit", "-m", subject, "-m", body)
    print(f"auto-commit: committed on {branch}: {subject}")
    return 0


def start_feature(name: str) -> int:
    branch = feature_branch_name(name)
    _git("checkout", "-b", branch, "main")
    print(f"start-feature: created {branch} from main")
    return 0


def cut_release(name: str | None) -> int:
    branch = release_branch_name(name, datetime.date.today())
    _git("checkout", "-b", branch, "main")
    print(f"cut-release: created {branch} from main")
    return 0


def open_pr(base: str | None) -> int:
    branch = current_branch()
    if branch in ("main", "HEAD") or branch.startswith("release/"):
        print(f"open-pr: refusing to open a PR from {branch}", file=sys.stderr)
        return 1
    push = _git("push", "-u", "origin", branch, check=False)
    if push.returncode != 0:
        print(f"open-pr: push failed:\n{push.stderr}", file=sys.stderr)
        return 1
    target = base
    if not target:
        releases = _git(
            "branch", "-r", "--list", "origin/release/*", "--sort=-committerdate"
        ).stdout.split()
        target = releases[0].removeprefix("origin/") if releases else "main"
    result = subprocess.run(
        ["gh", "pr", "create", "--base", target, "--head", branch, "--fill"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"open-pr: gh failed:\n{result.stderr}", file=sys.stderr)
        return 1
    print(f"open-pr: opened PR {branch} -> {target}: {result.stdout.strip()}")
    return 0


def selftest() -> int:
    today = datetime.date(2026, 6, 11)
    assert slugify("Agent Dashboard UI!") == "agent-dashboard-ui"
    assert feature_branch_name("Agent Dashboard UI") == "feature/agent-dashboard-ui"
    assert release_branch_name(None, today) == "release/2026-06-11"
    assert release_branch_name("v1", today) == "release/v1"
    for bad in ("", "!!!"):
        try:
            slugify(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"slugify({bad!r}) should fail")
    print("git_workflow selftest passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")
    commit = sub.add_parser("auto-commit")
    commit.add_argument("-m", "--message")
    feature = sub.add_parser("start-feature")
    feature.add_argument("name")
    release = sub.add_parser("cut-release")
    release.add_argument("name", nargs="?")
    pr = sub.add_parser("open-pr")
    pr.add_argument("--base")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if args.command == "auto-commit":
        return auto_commit(args.message)
    if args.command == "start-feature":
        return start_feature(args.name)
    if args.command == "cut-release":
        return cut_release(args.name)
    if args.command == "open-pr":
        return open_pr(args.base)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
