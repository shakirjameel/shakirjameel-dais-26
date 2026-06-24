# Understanding Checklist — The Context Harness

Running record of what you (the human) should understand about this session's
work. An item is checked only when you have **demonstrated** understanding in
your own words — not when it was merely explained. Per
`teaching-protocol.md`.

## 1. The problem

- [ ] What problem the harness solves (what goes wrong without it)
- [ ] *Why* that problem exists for agent-built repos specifically
- [ ] The branches/alternatives we could have taken (fat AGENTS.md, chat-only context, etc.)

## 2. The solution

- [ ] The three-layer split: SKILL.md (what/when) vs scripts (how) vs tests (proof)
- [ ] What `make harness-check` actually enforces (the 5 gate checks)
- [x] *Why* a feature can only reach `passing` via `verify-feature` (evidence, not self-judgement)
- [ ] The Markdown "brain" layout and which file owns which kind of instruction
- [ ] Edge cases: the `[TODO]` substring trip, the feature-table markers, stdlib-only constraint

## 3. The broader context

- [ ] Why "the system is 70%, the model is 30%" drives this whole design
- [ ] How this scaffolds the Medical Desert Planner (ingest → claim grading → coverage view → planner UI → persistence)
- [ ] What the teaching protocol + autoreview skill change about how future sessions run

## Status

In progress. Mastered: verify-feature is the only path to `passing` (evidence,
not self-judgement). Re-testing: gate-vs-feature-command distinction, and why the
gate is an executable script rather than a prose doc.
