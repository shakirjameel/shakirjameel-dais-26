# Constraints

Each durable instruction should say why it exists, when it applies, and when it
can be removed. Delete constraints that stop earning their keep.

| ID | Rule | Source | Applies when | Expires or remove when |
| --- | --- | --- | --- | --- |
| C001 | Agents must start with `AGENTS.md` and the harness docs it links | harness setup | Always | The entry-point model changes |
| C002 | A feature may transition to `passing` only through `scripts/harness.py verify-feature <ID>` | harness setup | Editing `features.md` | Never |
| C003 | Every feature must carry an executable verification command | harness setup | Adding/editing a feature | Never |
| C004 | `make harness-check` must exit 0 before any work is called done | harness setup | Closeout | Never |
| C005 | Keep `AGENTS.md` short; route detailed context to `docs/harness/` | harness setup | Editing entry point | Never |
| C006 | Harness tooling uses the Python standard library only — no third-party deps | harness setup | Editing `scripts/harness.py` | A dependency is consciously adopted and recorded in `decisions.md` |
| C007 | Run the `autoreview` skill as a closeout review after non-trivial code edits | harness setup | Before commit/ship | Never |
| C008 | Secrets (LLM keys, Databricks tokens, data-source creds) live in env/`.env`, never in tracked files | project build | Always | Never |
| C009 | New retrieval/extraction behavior must ship with an evaluation, not just a demo | swarm-engineering principle: evaluation-first | Building RAG/extraction/memory | Never |
| C010 | Agent execution loops must declare stop conditions (max steps, success, cost ceiling) | swarm-engineering principle: bound the loop | Building any agent loop | Never |
| C011 | Teach the human as work is done, per `teaching-protocol.md`; a session is not done until she has demonstrated understanding | owner request | Any non-trivial session | Never |
| C012 | Agent runs record token/cost via `scripts/cost_ledger.py`; loops gate spend with `cost_ledger.py check --ceiling <usd>` | project build / extends C010 | Running any agent loop | Never |
| C013 | The agent that implements a feature is not the one that verifies it — use the `verifier` subagent (maker/checker split) | loop-engineering | Verifying a feature in a loop | Never |
| C014 | `make check` must pass in CI on every PR (`.github/workflows/ci.yml`) | loop-engineering | Opening or updating a PR | Never |
| C015 | App stack is hybrid: TypeScript under `app/`, Python eval under `eval/`; match each feature's verification command to the right toolchain | decisions.md 2026-06-08 | Adding/editing app features | The stack decision is revisited in `decisions.md` |
| C016 | Follow `git-workflow.md`: auto-commit every code change (`make auto-commit`); significant features get `feature/<name>` branches; releases cut from `main` as `release/<name>`; features merge into release via autonomously opened PRs (`make open-pr`) | owner request 2026-06-11 | Any code change | The branching model is revisited in `decisions.md` |
