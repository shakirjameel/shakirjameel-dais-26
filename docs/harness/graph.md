# Code Review Graph

Source: user request to make code context available through graph queries.
Applies when: using code-review-graph tools for structural, relational, review,
debugging, or architecture questions.
Remove when: the repo stops using code-review-graph or replaces it with another
graph context system.

## USE graph — structural/relational queries

| Task | Tool |
|------|------|
| Entry point / scoping | `get_minimal_context` (~100 tokens, gives risk + suggested tools) |
| Who calls X? | `query_graph` callers_of |
| What does X call? | `query_graph` callees_of |
| What breaks if I change X? | `get_impact_radius` |
| Explore outward from X | `traverse_graph` with token_budget |
| Review PR/diff | `detect_changes` |
| Which execution paths hit? | `get_affected_flows` |
| Architecture overview | `get_architecture_overview` |
| Architectural hotspots | `get_hub_nodes` (most connected) |
| Architectural chokepoints | `get_bridge_nodes` (betweenness centrality) |
| Dead code | `refactor_tool` dead_code |
| Test coverage for X | `query_graph` tests_for |
| Untested hotspots / gaps | `get_knowledge_gaps` |
| Unexpected coupling | `get_surprising_connections` |
| Inheritors of X | `query_graph` inheritors_of |
| Find code by vague concept | `semantic_search_nodes` |

## PARALLEL strategy — when uncertain

- Fire graph tool + Grep/Glob simultaneously.
- Use whichever is better; combine if both are useful.
- Costs ~500 tokens. Guarantees finding what you need.

## Dynamic calibration — `get_minimal_context`

Run once on first graph use per session.

Call `get_minimal_context` with the task description. It returns ~100 tokens:
graph stats, risk score, top communities/flows, and next tools.

Use output to set params. Calculate density = edges ÷ nodes:

| Nodes | Density | limit | max_depth |
|---------|---------|---------|---------|
| <500 | >10 (dense) | 20 | 2 |
| <500 | ≤10 | 25 | 3 |
| 500–2K | >10 | 30 | 3 |
| 500–2K | ≤10 | 35 | 3 |
| 2K–10K | any | 40 | 3 |
| 10K+ | >5 | 50 | 3 |
| 10K+ | ≤5 (sparse) | 50 | 4 |

## Default params on every graph call

- `detail_level="standard"` for full context.
- `detail_level="minimal"` only for triage: "is this relevant at all?"
- `include_source=false` always → Read file instead for full file context.
- `traverse_graph`: use `token_budget` to cap output; default 2000, increase
  for deep dives.

## Escalation

- Shared core node in `max_depth` results → re-run +1 depth on that node.
- `limit` results truncated → increase to 2x limit.
- Deep exploration: `traverse_graph` with BFS mode + higher `token_budget`.

## Rules to avoid wasted tokens

1. Graph error or 0 results → Grep immediately. No retry with different query.
2. Graph returns file+line → Read file directly. Do not chain more graph calls.
3. Never use `semantic_search_nodes` for file lookup — Glob is faster and exact.
4. Full picture before responding. Better context > tokens.

## PR review workflow

1. `get_minimal_context` with task="review PR" for scoping and risk (~100 tokens).
2. `detect_changes` for risk-scored, prioritized changes.
3. `get_affected_flows` to understand cross-module impact.
4. Read flagged high-risk files for full understanding.
5. `query_graph` tests_for on changed/high-risk functions.
6. Thorough reviews: `get_hub_nodes` + `get_bridge_nodes` to flag if changed
   code is a hotspot/chokepoint.

## Debug / investigation workflow

1. `get_minimal_context` with task description.
2. `semantic_search_nodes` or `query_graph` to locate relevant code.
3. `traverse_graph` BFS from suspect node (`token_budget=3000`).
4. `get_affected_flows` to trace execution paths.
5. Read files for actual code.

## Install and platform setup

Install:

```bash
pip install code-review-graph
# or:
pipx install code-review-graph
```

Auto-detect and configure supported platforms:

```bash
code-review-graph install
```

Configure specific platforms:

```bash
code-review-graph install --platform codex
code-review-graph install --platform cursor
code-review-graph install --platform claude-code
code-review-graph install --platform gemini-cli
code-review-graph install --platform kiro
code-review-graph install --platform copilot
code-review-graph install --platform copilot-cli
```
