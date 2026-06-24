# Sprint Contract: <Feature Name>

Copy this template per non-trivial feature. It binds scope, evidence, and
handoff before work starts.

## Scope

What is in scope for this sprint. One feature ID from `features.md`.

## Verification Standards

- **Unit / static:** the feature's test(s); `make harness-check` stays green.
- **Runtime:** `python3 scripts/harness.py verify-feature <ID>` exits 0 with evidence.
- **End-to-end:** product-specific confirmation (if the component is user-facing).

## Exclusions

What is explicitly *not* in scope — so the work does not sprawl.

## Handoff

Generator records changed files and self-check results. Evaluator runs the
verification commands and scores with `docs/harness/evaluator-rubric.md`.
