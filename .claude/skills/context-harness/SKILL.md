---
name: context-harness
description: "Maintain and enforce the repo's context harness — the Markdown brain in docs/harness/ gated by scripts/harness.py. Use at session start (clock-in), when adding or moving a feature, and at closeout (check + clock-out)."
---

# Context Harness

This skill owns the *what* and *when* of the harness. `scripts/harness.py` owns
the *how*; `scripts/test_harness.py` proves the *how* still works.

## When to use

- Session start — orient and pick work.
- Adding, moving, or completing a unit of work in `docs/harness/features.md`.
- Closeout — before calling anything done.

## Steps

1. **Clock in.** Run `make agent-clock-in`. Read the next feature it prints, then
   read `docs/harness/progress.md` for where the last session stopped.
2. **Read the rules.** `docs/harness/constraints.md` (C0xx) and
   `docs/harness/context-map.md` (which file owns which instruction).
3. **Do the work** for one feature. Keep it small enough for one session.
4. **Verify with evidence.** Completion needs external proof — the author is not
   the only judge. Run `python3 scripts/harness.py verify-feature <ID>`; only
   this command may move a feature to `passing`.
5. **Gate.** Run `make harness-check`. It must exit 0: required files exist, the
   feature queue parses, every feature has a valid id/state/verification command,
   this skill's frontmatter is valid with no leftover template text, and the unit tests pass.
6. **Closeout.** Update `progress.md`; record any failure in `diagnostics.md`
   with its layer; run `make clock-out`.

## Decision rules

- If a rule can be checked, add a verification command — do not leave it advisory.
- Prefer the smallest file that holds a durable instruction (`context-map.md`
  update protocol). Keep `AGENTS.md` a routing layer.
- When the gate logic itself changes, update `scripts/test_harness.py` in the
  same change so the test still proves the behavior.
- After non-trivial code edits, run the `autoreview` skill as a closeout review.

## Commands

| Command | Use |
| --- | --- |
| `make agent-clock-in` | Print next feature + closeout reminder |
| `make harness-check` | The gate (required files, features, skill, tests) |
| `python3 scripts/harness.py verify-feature <ID>` | Run a feature's check, record evidence, move to passing |
| `make dashboard` | Regenerate `docs/harness/context-dashboard.html` |
| `make clock-out` | Gate + clock-out checklist |
