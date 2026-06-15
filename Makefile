PY ?= python3

.PHONY: agent-clock-in next-feature dashboard harness-check check clock-out triage cost-report hygiene auto-commit feature cut-release open-pr

## Print the next feature and the closeout reminder.
agent-clock-in:
	$(PY) scripts/harness.py clock-in

## Print the next actionable feature only.
next-feature:
	$(PY) scripts/harness.py next-feature

## Regenerate docs/harness/context-dashboard.html.
dashboard:
	$(PY) scripts/harness.py dashboard

## The gate: required files, parseable features, valid states, skill, unit tests.
harness-check:
	$(PY) scripts/harness.py check

## Repo hygiene gate: secrets, generated-artifact, and CI wiring.
hygiene:
	$(PY) scripts/check_repo_hygiene.py

## Print the deterministic triage brief (next feature, gate, recent work).
triage:
	$(PY) scripts/triage.py

## Print token/cost totals from the ledger.
cost-report:
	$(PY) scripts/cost_ledger.py report

## Run all known checks (also what CI runs). As app code lands, add its tests here.
check: harness-check
	$(PY) scripts/check_repo_hygiene.py
	$(PY) scripts/triage.py --selftest
	$(PY) scripts/cost_ledger.py --selftest
	$(PY) eval/recall_at_k.py --selftest
	$(PY) scripts/git_workflow.py --selftest

## Commit all pending changes on the current branch (C016).
auto-commit:
	$(PY) scripts/git_workflow.py auto-commit $(if $(MSG),-m "$(MSG)")

## Create feature/<NAME> from main: make feature NAME="agent dashboard ui".
feature:
	$(PY) scripts/git_workflow.py start-feature "$(NAME)"

## Create release/<NAME or today> from main.
cut-release:
	$(PY) scripts/git_workflow.py cut-release $(NAME)

## Push the current branch and open a PR via gh (defaults to newest release branch).
open-pr:
	$(PY) scripts/git_workflow.py open-pr $(if $(BASE),--base $(BASE))

## Run the gate and print the clock-out checklist.
clock-out:
	$(PY) scripts/harness.py clock-out
