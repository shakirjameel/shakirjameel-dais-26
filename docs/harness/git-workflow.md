# Git Workflow

The branching and commit policy for this repository (constraint C016). The
policy is executable via `scripts/git_workflow.py` — use the Make targets, do
not improvise.

## The rules

1. **Every code change is committed automatically.** After any code change
   lands, run `make auto-commit` (optionally `MSG="..."`). Never leave work
   uncommitted at the end of a step. A PostToolUse hook attempts the same
   per-edit commit when permission mode allows; `make auto-commit` is the
   fallback that must always run at closeout.
2. **Significant feature changes get a feature branch.** A change is
   significant when it adds/renames a component, touches more than one area, or
   maps to a feature in `features.md`. Create it with
   `make feature NAME="agent dashboard ui"` → `feature/agent-dashboard-ui`,
   always branched from `main`.
3. **Release branches are always cut from `main`.** `make cut-release`
   (optionally `NAME=v1`) creates `release/<name-or-date>` from `main` — never
   from a feature branch. Features merge **into** the release branch, the
   release branch merges into `main`, like a normal repository.
4. **PRs are opened autonomously.** `make open-pr` (optionally
   `BASE=release/...`) pushes the current feature branch and opens a PR with
   `gh pr create` against the newest release branch (falling back to `main`).
   CI must pass on the PR (C014); the verifier subagent reviews it (C013).

## The flow

```
main ──┬── make feature NAME=...   → feature/<name>   (work + auto-commits)
       └── make cut-release        → release/<date>
                                        ▲
            make open-pr  ─────  PR: feature/<name> → release/<date>
                                        │ merge after CI + review
            release/<date> ──── PR ──→ main
```

## Branch naming

- Features: `feature/<kebab-case-name>`
- Releases: `release/<YYYY-MM-DD>` (default) or `release/<version>`

## Worked example (2026-06-11)

The dashboard UI import lives on `feature/agent-dashboard-ui`; the release cut
from `main` is `release/2026-06-11`; the PR merges the feature into the
release.
