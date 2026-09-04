# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The five explicit remediation requirements, repository evidence, and mandatory validation results fully determine this review; no user-authority decision remains open.",
  "scope": "This phase's own conduct at this iteration."
}
```

## Summary

M-001 through M-004 are resolved and their regression tests meaningfully exercise the reported risks. C-001 is only conditionally resolved: durable claim/recovery works when callers pass the same `RuntimeStatePort` separately to both adapter and graph, but the public graph and launcher APIs default it to `None` and then deliberately execute the external effect without any claim. A direct fresh-`OrcaAdapter` replay through that default path created two external Tasks for the same stable intent, so one blocking finding remains.

UNIT_TEST_STATUS: PASS

## Blocking Findings

ID: F-001  
Quality Attribute: G1  
Severity: CRITICAL  
Blocking: YES  
Responsible Phase: bugfix  
Location: `scripts/deterministic_workflow/executor.py:75-110`, `scripts/deterministic_workflow/graph.py:168-188`, `scripts/deterministic_workflow/launcher.py:74-98`, `scripts/deterministic_workflow/launcher.py:151-166`, `INSTALL.md:270-277`, and installed mirrors  
Issue: Crash-safe idempotency remains opt-in, leaving the default external execution path able to duplicate Task/Dispatch creation.  
Reason / Evidence: `execute_intent_node(..., runtime_state=None)` selects `_settle_now`, whose first operation is `adapter.start(intent)`; no stable intent is claimed before that external effect. Both `build_graph` and `execute_state` default `runtime_state` to `None`, and the CLI also uses `None` unless the optional `--runtime-state` flag is supplied. The recovery tests always inject the store into both objects (`build_graph(..., runtime_state=store)` and `Adapter(..., runtime_state=store)`), so they do not prove that the shipped/default execution contract is safe. In a direct reproduction using one stable intent, two fresh `OrcaAdapter` instances, and the default `execute_intent_node(OrcaAdapter(...))` path, the observed result was `{'fresh_adapters': 2, 'external_task_creates': 2}`. This violates the explicit requirement that the graph persist/claim the intent before external execution and that the same Task/Dispatch not be recreated after a fresh-adapter restart.  
Required Action: Make durable idempotency mandatory for any external-effect execution path: require a `RuntimeStatePort`, safely derive and validate a single adapter-bound port, or fail closed before `adapter.start` when none is available. Remove/document the unsafe optional behavior and add a regression that invokes the public graph/API with a fresh Orca adapter under its default configuration and proves that a second Task/Dispatch cannot be created.

## Non-Blocking Findings

None.

## Test Review

The required full suite passed: `python3 -m unittest discover -s scripts -p 'test_*.py'` ran 1,819 tests in 322.856 seconds with `OK (skipped=6)`. The import-blocked dependency-absent lane ran 94 tests with `OK (skipped=54)`, zero errors, and zero failures. The targeted malformed/recovery/launcher/adapter/control-plane set ran 65 tests successfully; an additional 17-test adversarial set directly covered fresh-store/fresh-adapter recovery, the true crash window, all guarded state-ingress APIs, checkpointed FAIL-to-PASS settlement mutation, and fake/Orca logical-trace parity.

The before-fix evidence in `artifacts/runs/run_9e3c67a7824e/evidence/` covers each original finding and is consistent with the reviewed code history. Current tests are not merely same-object or implementation-constant checks: C-001 uses distinct adapter/store instances and a file-backed ledger, M-001 launches a subprocess, M-002 exercises compiled graph ingress (including sync/async/batch/update surfaces), M-003 mutates checkpointed data before apply, and M-004 performs negative drift mutations. The remaining C-001 gap is specifically that every successful recovery test opts into the new port and no test forbids the public default from taking the old unsafe branch.

Direct validation results:

- Installed launcher demo: `COMPLETED`, trace length 68, exit code 0.
- `validate_skills.py`: PASSED, 729 checks.
- Source package verification: PASSED, 234 files.
- Fresh temporary archive build and archive verification: PASSED, 234 files.
- Workflow graph documentation validator: PASSED.
- Source-to-installed deterministic-workflow recursive diff: no differences.
- `git diff --check`: no output.
- Past run `artifacts/runs/run_0bcf4e7296c9/`: no working-tree changes.
- Control-plane drift injection: removing a graph-owned demotion marker was rejected; source SHA-256 remained unchanged (`652202f97295f7c6547d96d0260d4bad9bf56c109a2d4669807bae909ccff333`).
- Settlement FAIL-to-PASS mutation with original digest/event ID: rejected before apply with `SETTLEMENT_INTEGRITY`.
- Unknown-only malformed input across all exposed state-ingress APIs: blocked with no effects; unguarded ingress/composition surfaces are denied.

## Final Decision

FAIL. The launcher, malformed-state handling, settlement integrity, and single-control-plane remediation satisfy their explicit requirements, and the mandatory validation evidence is complete. C-001 still permits the exact pre-fix no-claim behavior through default public APIs, so the run cannot pass until external execution is impossible without durable idempotency protection.
