# Loops — automating the harness instead of hand-prompting

This file is the loop layer that sits on top of the harness. The harness makes
work *checkable*; the loop makes it *self-driving*. The point is to stop being
the person who prompts the agent and instead design the system that prompts it —
while staying the engineer who reviews the output.

## The five pieces (+ memory) and where they live here

| Piece | What it does | In this repo |
| --- | --- | --- |
| Automations (heartbeat) | Fire on a schedule, do discovery/triage | `.github/workflows/nightly-triage.yml` + `scripts/triage.py`; interactively, `/loop` and `/goal` |
| Worktrees | Keep parallel agents from colliding | `git worktree` + `isolation: worktree` on the implementer subagent |
| Skills | Write down project knowledge once | `.claude/skills/` (`context-harness`, `autoreview`) |
| Plugins / connectors | Touch real tools | `.mcp.json` (`code-review-graph`); add more MCP servers as needed |
| Sub-agents | Separate the maker from the checker | `.claude/agents/` (`explorer`, `implementer`, `verifier`) |
| Memory (the spine) | Hold what's done / next outside the chat | `docs/harness/*.md` (progress, decisions, diagnostics, features) |

## The maker/checker split (C013)

The agent that writes the code is too generous grading its own work, so the loop
keeps roles separate:

1. **explorer** (read-only) → gathers context and returns a build plan.
2. **implementer** (maker) → builds one feature + its test/eval; stops before
   marking it done.
3. **verifier** (checker, stronger model) → runs `verify-feature`, scores the
   rubric, records quality/diagnostics. Only this step moves a feature to
   `passing`.

## Running a loop

- **Interactive, bounded:** `/loop 30m /triage` re-runs triage on a cadence.
- **Until a condition holds:** `/goal "all MDP003 checks pass and make check is
  green"` — a separate model decides when the goal is met (maker/checker applied
  to the stop condition itself).
- **Unattended heartbeat:** the nightly workflow posts `scripts/triage.py` to the
  job summary. The agentic step (commented, opt-in) needs an `ANTHROPIC_API_KEY`
  secret and dispatches implementer → verifier on the next feature, opening a PR.

## Parallel work without collisions

Run each implementer in its own checkout so edits cannot touch another's:

```bash
git worktree add ../mdp-MDP004 -b feat/MDP004
```

Or let a spawned subagent isolate itself with `isolation: worktree`. Your review
bandwidth — not the tooling — is the real ceiling on how many you run at once.

## Cost control (C010, C012)

Every loop declares stop conditions and a cost ceiling. Record spend with
`scripts/cost_ledger.py record ...` and gate it with
`scripts/cost_ledger.py check --ceiling <usd>` so an unattended loop cannot
silently burn budget.

## Stay the engineer

A loop running unattended is also a loop making mistakes unattended. The verifier
makes "done" mean something, but "done" is still a claim, not a proof — read what
the loop produced. The same loop accelerates someone who understands the work and
buries someone who uses it to avoid understanding it. Build the loop; review the
diffs.
