# Evaluator Rubric

Score a sprint independently of the agent that generated it. Grade each
dimension A–D.

| Dimension | A | B | C | D |
| --- | --- | --- | --- | --- |
| Correctness | Behaves correctly across the stated scope and edge cases | Correct for the stated scope | Works on the happy path only | Incorrect or unverified |
| Test coverage | Verification command + meaningful tests/evals | Verification command present | Manual check only | None |
| Architecture compliance | Matches `project-context.md` + constraints | Minor deviation, documented | Deviates without note | Violates a constraint |
| Agent understandability | Another agent can resume from the docs alone | Mostly clear | Needs the author to explain | Opaque |
| Cleanup | No stale artifacts; progress/diagnostics updated | Minor leftovers | Noticeable debt | Mess left behind |

**Threshold:** a sprint fails if any dimension is D, or if correctness is below
B for the stated scope.

## Handoff

The generator records changed files and self-check results. The evaluator runs
the verification commands and scores with this rubric, recording the result in
`quality.md` (and any failure in `diagnostics.md`).
