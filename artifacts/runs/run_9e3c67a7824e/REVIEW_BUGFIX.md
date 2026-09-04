# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The explicit five review findings, repository evidence, and mandatory BUGFIX test gate fully determine this iteration's verdict; no user-authority decision remains open.",
  "scope": "This phase's own conduct at this iteration."
}
```

## Summary

Four findings are resolved and C-001's fresh-adapter/crash-window behavior is meaningfully exercised. M-002 remains partially unresolved: an unknown-only field supplied directly to the compiled graph is silently discarded by LangGraph, after which the workflow executes three effects and completes instead of returning `BLOCKED/MALFORMED_STATE`. This contradicts the explicit compiled-graph malformed-state requirement, so one blocking finding remains.

UNIT_TEST_STATUS: PASS

## Blocking Findings

ID: F-001  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `scripts/test_deterministic_workflow_malformed.py:81-94`, `scripts/deterministic_workflow/graph.py:17-43`, `scripts/deterministic_workflow/launcher.py:66-86` (and installed mirror)  
Issue: Unknown fields do not fail closed when state is submitted directly to the compiled graph.  
Reason / Evidence: The requirement explicitly calls for missing, unknown, invalid-type, and incoherent phase/index/budget inputs to be covered through compiled-graph tests and to return a valid `BLOCKED/MALFORMED_STATE` terminal result. The new unknown-field assertion instead tests `launcher.execute_state`; its compiled-graph companion combines the unknown field with `requested_phases="bad"`, so the known-field error causes the block and the unknown-field behavior is not tested. My unknown-only compiled invocation produced `{'terminal_status': 'COMPLETED', 'reason': 'WORKFLOW_COMPLETED', 'effect_count': 3, 'unknown_present': False}`. Thus arbitrary unrecognized input is accepted and effects run.  
Required Action: Make the compiled graph entry preserve/detect the raw closed-field set (or provide an equivalent compiled invocation boundary that cannot bypass it), and add an unknown-only compiled-graph regression asserting `BLOCKED/MALFORMED_STATE` and zero effects.

## Non-Blocking Findings

None.

## Test Review

Directly executed validations:

- `python3 -m unittest scripts.test_deterministic_workflow_recovery scripts.test_deterministic_workflow_malformed scripts.test_deterministic_workflow_launcher scripts.test_workflow_control_plane` — 43 tests, OK.
- `python3 -m unittest discover -s scripts -p 'test_*.py'` — 1,804 tests in 321.949s, OK, skipped=6 (baseline 1,761; no test-count regression).
- Dependency-absent import-block lane across seven engine/control-plane modules — 79 tests, OK, skipped=39, errors=0, failures=0.
- `python3 scripts/validate_skills.py` — PASSED, 729 checks.
- `python3 scripts/verify_package.py` — PASSED, 234 source files.
- A fresh temporary archive built with `scripts/build_release.py --output ...`, then `verify_package.py --archive ...` — PASSED, 234 source files.
- `python3 scripts/validate_workflow_graph_docs.py` — PASSED.
- `diff -r --exclude=__pycache__ scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow` — no differences.
- Installed-copy `tools/run_workflow.py --demo --json` — `COMPLETED`, trace length 68, exit 0; `--check-runtime` reported LangGraph 0.2.76.
- `python3 -m unittest scripts.test_deterministic_workflow_adapters.LangGraphAdapterParityTests` — 1 test, OK.
- `git diff --check` — no output. `git status --short artifacts/runs/run_0bcf4e7296c9` — no output.

Direct mutation/injection results:

| Scenario | Observed result |
| --- | --- |
| Fresh file store + fresh FakeAdapter settlement recovery | PASS; only two not-yet-run effects executed |
| Effect occurs, process dies before receipt, fresh adapter resumes | PASS; `IDEMPOTENCY_RECOVERY_REQUIRED`, zero duplicate effects |
| Fresh OrcaAdapter + fresh harness resumes settled intent | PASS; no Task/Dispatch calls, identical settlement |
| Settlement result FAIL→PASS with original digest/ID | Rejected as `SETTLEMENT_INTEGRITY` before apply |
| Payload digest, event ID, intent binding, command binding mutations | All rejected as `SETTLEMENT_INTEGRITY` |
| Missing fields, invalid types, bad phase/index/budget through compiled graph | `BLOCKED/MALFORMED_STATE`, zero effects |
| Unknown-only field through compiled graph | **FAIL**: unknown discarded; `COMPLETED`, three effects |
| Removed graph-owned demotion marker in an in-memory drift copy | Rejected: `graph-owned section is not demoted`; source SHA-256 unchanged before/after (`652202f9...ff333`) |

Before-fix evidence was independently sampled without modifying the worktree: `git archive HEAD` was extracted to a temporary directory, the new malformed-state test was copied in, and the test produced 13 errors including the original `KeyError` paths. Current source/mirror hashes and `git diff --check` remained unchanged afterward. The tests are not tautological for C-001, M-001, or M-003: they use fresh adapters/file persistence, subprocess CLI execution, and mutation of checkpointed data respectively. The M-002 unknown-field test is the exception described in F-001 because it masks the compiled-graph gap with a separate malformed known field.

## Final Decision

C-001: RESOLVED — the graph can claim through `RuntimeStatePort` before the effect; settled recovery and the uncertain crash window use fresh adapters, with duplicate Task/Dispatch creation prevented.  
M-001: RESOLVED — launcher executes the canonical fake workflow, exposes terminal output/exit status, and handles the 68-step recursion requirement explicitly.  
M-002: NOT RESOLVED — missing/type/coherence cases fail closed, but unknown-only compiled input completes and executes effects.  
M-003: RESOLVED — canonical settlement digest/event identity and intent/command binding are recomputed before apply; mutation tests pass.  
M-004: RESOLVED — graph-owned routing prose is explicitly non-authoritative, safety/lifecycle/test/decision rules remain authoritative, and drift rejection was directly verified.

Because F-001 violates an explicit requirement, the BUGFIX phase gate is `RESULT: FAIL` with one blocking finding.
