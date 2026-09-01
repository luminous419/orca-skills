# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The implementation has extensive positive coverage and all reported CI commands pass independently, but the live Orca runtime still has two dispatch paths that violate OS-29's fail-closed boundary contract. In particular, an agent result with no decision declaration is explicitly treated as a legacy non-participant and leaves the next B1 check able to admit the unchanged run-entry head, while `observe_unexpected_exit()` reaches `start_worker()` without invoking the B1 guard at all. These are explicit requirement violations, so the implementation gate fails despite the green suite.

## Blocking Findings

ID: F-001
Quality Attribute: G1
Severity: CRITICAL
Blocking: YES
Location: `scripts/orca_runtime_harness.py:2356-2369`, `scripts/orca_runtime_harness.py:2361`, `scripts/test_orca_runtime_contract.py` (`DecisionGateLiveDispatchTests.test_a_legacy_body_that_declares_nothing_is_not_a_ledger_participant`)
Issue: The live runtime fails open when a settled Worker or Reviewer result omits the mandatory machine-readable decision result.
Reason / Evidence: `_record_decision_from_attempt()` returns `("", "")` immediately when `declares_gate_result(body)` is false. It deliberately does not advance `_last_settled` and writes no ledger record. Consequently the following `_b1_guard()` still receives `expected_settled_round=None` and admits the sequence-0 run-entry declaration as though no round had settled. The added test positively enshrines this legacy exception instead of asserting refusal. This contradicts ORIGINAL_REQUEST's unconditional rules that every B2/B3 boundary requires an explicit result, that a missing result is never presumed `CLEAR`, and that missing/malformed results fail closed at all three boundaries. It also contradicts PLAN P10 criterion 6. The implementation Decision Record discloses this exception but classifies the phase `CLEAR`; disclosure does not authorize a departure from an explicit requirement.
Required Action: Make a silent settled result poison or terminate the live transition immediately, bind the settled round so the next B1 cannot admit the old head, and replace the legacy-pass test with Worker and Reviewer negative tests proving no later dispatch can occur after a missing gate result.

ID: F-002
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `scripts/orca_runtime_harness.py:2726-2770`
Issue: `observe_unexpected_exit()` is a live dispatch-initiating path that bypasses the B1 ledger guard.
Reason / Evidence: The method calls `dispatch_context()`, creates a Task and terminal, and then calls `start_worker()` without first calling `_b1_guard()`. The sibling dispatch path `run_existing_task()` calls the guard at line 2571. Repository comments identify both methods as the two centralized dispatch initiators, and tests exercise `observe_unexpected_exit()` as a real dispatch path. Therefore an unresolved, malformed, unsupported-schema, or unbound ledger head can still reach `worker-start` through this path, violating the requirement to forbid any new phase dispatch before entry and P10's all-boundaries/illegal-dispatch exit criteria.
Required Action: Apply the same pre-dispatch B1 guard before every effect in `observe_unexpected_exit()` and add adversarial tests showing each relevant refusal shape produces no Task, terminal, or `worker-start` call through this path.

## Non-Blocking Findings

None.

## Test Review

- `python3 -m unittest scripts.test_decision_gate scripts.test_os29_decision_gate scripts.test_e2e_harness scripts.test_validate_skills` — PASS, 397 tests.
- `python3 -m unittest discover -s scripts -p 'test_*.py'` — PASS, 1570 tests, 6 skipped; above the 1496 baseline.
- `python3 scripts/validate_skills.py` — PASS, 697 checks.
- `python3 scripts/verify_package.py` — PASS, 189 source files.
- `python3 scripts/build_release.py` — PASS; reproducible archive built.
- `git diff --check main...HEAD` — PASS.
- M-DUP/control coverage is present in the passing targeted suite, and the decision-block/no-charge versus quality-FAIL/charge control passes.
- `scripts/run_logging.py` and `orca-worker-reviewer-orchestration/tools/run_logging.py` are byte-identical.
- `scripts/decision_policy.py`, `scripts/task_context.py`, and `scripts/workflow_contract.py` have no production diff; the shared policy source remains isolated. No new RUN_STATUS, round_kind, or Worker STATUS value was found.
- Test changes are net additive (2542 added lines, 9 deleted lines), and no test module or validator was deleted. However, the green suite cannot establish the required property because it explicitly accepts the silent-result exception and has no B1 refusal test for `observe_unexpected_exit()`.

## Evidence Checked

Read the full ORIGINAL_REQUEST, approved PLAN and DESIGN, IMPLEMENTATION report, the production diff, the required gate/runtime/logging/policy/workflow modules, both Skills and their shared review/template changes, fake agents, fixtures, and the complete changed test modules. Verified the implementation Decision Record: it uses `CLEAR` with no reason code, but its grounds are substantively invalid because they treat the live missing-result exception as requirement-preserving.

## Final Decision

FAIL. F-001 and F-002 are explicit fail-closed and dispatch-blocking requirement violations. Correction is required before the implementation phase can pass.
