# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The five explicit remediation requirements, current repository delta, and directly executed mandatory and adversarial checks fully determine this review; no user-authority decision remains open.",
  "scope": "This phase's own conduct at this iteration."
}
```

## Summary

C-001, M-001, M-003, and M-004 remain resolved, and the attempt-1 default-path idempotency finding is fixed: unsafe port-less construction now fails before effects, adapter-bound persistence survives fresh instances, and the launcher supplies a file-backed ledger by default. M-002 is still bypassable because the object returned by `build_graph` publicly exposes the raw compiled LangGraph as `.compiled`; unknown-only input through that handle completes and executes effects. One blocking finding remains.

UNIT_TEST_STATUS: PASS

## Blocking Findings

ID: F-001  
Quality Attribute: G2  
Severity: MAJOR  
Blocking: YES  
Responsible Phase: bugfix  
Location: `scripts/deterministic_workflow/graph.py:37-59` and installed mirror; `scripts/test_deterministic_workflow_malformed.py:104-116`  
Issue: The guarded graph can be publicly unwrapped through `.compiled`, restoring the malformed-state execution path the deny-by-default façade is intended to remove.  
Reason / Evidence: `GuardedWorkflowGraph.__init__` assigns the native graph to the public `compiled` attribute, and its own documentation describes that handle as deliberately reachable. Directly invoking an otherwise-valid state containing only `surprise_field` via `build_graph(...).compiled.invoke(...)` returned `COMPLETED / WORKFLOW_COMPLETED` and ran three adapter effects. This is not a hypothetical private-reflection path: the production wrapper explicitly publishes and documents the raw runnable. The structural ingress test excludes this unwrapping attribute, while `test_raw_langgraph_still_drops_unknown_channels` relies on it and weakens its fixture with a separate invalid known field (`requested_phases="bad"`), so no regression asserts that the shipped graph cannot be unwrapped and used with unknown-only input. The result contradicts the fail-closed malformed compiled-graph requirement and the façade claim that graph-unwrapping APIs are unreachable.  
Required Action: Remove the public raw-graph handle from the production façade (or make it inaccessible to callers), move any native LangGraph behavior probe to a separately constructed test-only graph/helper, and add an unknown-only regression proving no public attribute or callable returned by `build_graph` can reach unguarded state ingress or external effects.

## Non-Blocking Findings

None.

## Test Review

The full unittest lane ran **1,828 tests in 326.165s** with `OK (skipped=6)`. The import-blocked dependency-absent lane ran **103 tests** with `OK (skipped=63)`, zero errors, and zero failures. A 54-test targeted adversarial set covering fresh-store/fresh-adapter recovery, default-path idempotency, settlement mutation, malformed input, guarded ingress, adapter parity, and control-plane drift passed.

Required validations directly observed:

- Installed launcher `--demo --json`: `COMPLETED`, trace length 68, exit code 0.
- `validate_skills.py`: PASSED, 729 checks.
- `verify_package.py`: PASSED, 234 source files.
- Fresh temporary archive build and archive verification: PASSED, 234 files.
- `validate_workflow_graph_docs.py`: PASSED.
- Source-to-installed deterministic-workflow recursive diff: no differences; copied installed Skill independently completed the demo and reported LangGraph 0.2.76.
- `git diff --check`: no output. `git status --short artifacts/runs/run_0bcf4e7296c9/`: no output.
- Control-plane mutation: removing the phase-sequence demotion marker from a temporary copy was rejected as `graph-owned section is not demoted`; source SHA-256 remained `652202f97295f7c6547d96d0260d4bad9bf56c109a2d4669807bae909ccff333` before and after.

The tests are meaningful for C-001, M-001, M-003, and M-004: recovery/default-path cases use fresh adapters and separate file-store objects; launcher tests execute subprocess/API behavior; settlement tests mutate checkpointed FAIL into PASS while retaining identity; and control-plane tests perform negative document mutations. Supported façade ingress methods correctly block unknown-only state, including sync/async invoke, stream, batch, and update paths. The remaining test gap is specifically the public `.compiled` unwrapping route demonstrated above.

The attempt-1 correction is otherwise sound. Port absence is a configuration error, so build-time `IdempotencyPortRequired` is appropriate and prevents construction of an unsafe effecting graph; the CLI fills that requirement with a stable file ledger. INSTALL.md accurately states that the system-temp default survives process restart but can be cleared periodically and directs operators to configure a controlled durable location.

## Final Decision

FAIL. The default idempotency correction is effective and does not regress the launcher, settlement integrity, control-plane delegation, or guarded façade entry points. However, M-002 is not fully fail-closed while the same returned graph object exposes a documented raw runnable that accepts unknown-only state and executes external effects; F-001 must be corrected in the bugfix phase before the final gate can pass.
