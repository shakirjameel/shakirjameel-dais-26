# Verification

Completion requires external evidence. The agent who wrote the change should not
be the only judge that it is done.

## Required evidence

- For harness changes: `make harness-check`.
- For feature completion: `python3 scripts/harness.py verify-feature <ID>`.
- For app changes: the feature's own verification command (e.g. its test/eval).

## Levels

| Level | Confirms | How |
| --- | --- | --- |
| 1. Static and structure | Required files present, parseable feature list, valid states | `validate_workspace` inside `harness check` |
| 2. Runtime behavior | Harness unit tests, generated dashboard, app tests/evals | `make check` |
| 3. System confirmation | Product-specific end-to-end behavior (a query returns a grounded, cited answer) | App-level e2e once components exist |

## Human understanding (definition of done)

A feature is not complete until the human understands it. After the verification
command passes, teach the change to mastery per `teaching-protocol.md` and check
off its `understanding-<topic>.md` doc. Working code the human cannot explain is
not done.

## Agent-facing error pattern

Avoid vague messages such as `test failed` when a script can explain what to
repair. A check should name the missing file, the invalid state, or the feature
that is passing without evidence — so the fix is obvious.
