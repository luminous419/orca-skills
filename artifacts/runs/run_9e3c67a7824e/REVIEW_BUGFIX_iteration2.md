# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The correction delta, explicit malformed-state requirement, and directly observed compiled-graph behavior fully determine this re-review; no user-authority decision remains open.",
  "scope": "This phase's own conduct at this iteration."
}
```

## Summary

Iteration 1 F-001 is fixed for synchronous `invoke`: the exact unknown-only reproduction now returns `BLOCKED/MALFORMED_STATE` with zero effects, and the new test isolates that key from all known-field errors. However, `GuardedWorkflowGraph.__getattr__` exposes standard compiled-graph entry points such as `batch` and `ainvoke` without the guard; both independently reproduced the original failure (`COMPLETED`, three effects). One new blocking finding therefore remains.

UNIT_TEST_STATUS: PASS

## Blocking Findings

ID: F-002  
Quality Attribute: G2  
Severity: MAJOR  
Blocking: YES  
Location: `scripts/deterministic_workflow/graph.py:27-72` and mirrored installed file  
Issue: The unknown-field guard covers `invoke` and `stream`, but other exposed compiled-graph execution APIs bypass it.  
Reason / Evidence: `__getattr__` delegates every unimplemented method directly to the raw compiled graph. An otherwise-valid state with only `surprise_field` passed to `build_graph(...).batch([state])` produced `COMPLETED / WORKFLOW_COMPLETED / effect_count=3`; the same state passed to `await build_graph(...).ainvoke(state)` produced the identical result. These are ordinary execution methods of the object returned by `build_graph`, so malformed graph state can still reach external effects. This contradicts the class claim that every entry point is guarded and the explicit fail-closed malformed-state requirement.  
Required Action: Guard every exposed state-ingress execution method (`ainvoke`, `batch`, `abatch`, `astream`, and any other supported ingress), or restrict the façade so unguarded execution APIs are not exposed. Add unknown-only regressions for at least synchronous batch and asynchronous invoke, asserting `BLOCKED/MALFORMED_STATE` and zero effects while retaining resume behavior.

## Non-Blocking Findings

None.

## Test Review

Direct executions:

- Exact iteration 1 unknown-only synchronous invocation: `BLOCKED`, reason `MALFORMED_STATE`, effect count 0, unknown absent from normalized terminal state.
- New tests were inspected: `test_unknown_only_field_blocks_at_the_compiled_graph_entry` changes only `surprise_field`; its base state is otherwise valid and it asserts the required terminal fields plus zero effects. The matrix test likewise does not mix in a malformed known field.
- Targeted regression set (`malformed`, `recovery`, `launcher`, adapter parity, control plane): 48 tests, OK.
- `python3 -m unittest discover -s scripts -p 'test_*.py'`: 1,808 tests in 323.959s, OK, skipped=6 (required minimum 1,805 satisfied).
- Dependency-absent import-block lane across seven modules: 83 tests, OK, skipped=43, errors=0, failures=0.
- Launcher `--demo --json`: `COMPLETED`, trace length 68, exit 0; source-installed copied launcher produced the same result and runtime probe reported LangGraph 0.2.76.
- `validate_skills.py`: PASSED, 729 checks; `verify_package.py`: PASSED, 234 files; graph-doc validator: PASSED.
- Temporary release archive build and archive verification: PASSED, 234 source files.
- Source/install deterministic-workflow mirror recursive diff: no differences; `git diff --check`: no output; prior-run artifact status: no output.

Mutation/injection table:

| Input/API | Result | Effects |
| --- | --- | ---: |
| unknown-only / `invoke` | `BLOCKED / MALFORMED_STATE` | 0 |
| unknown-only / launcher `execute_state` | `BLOCKED / MALFORMED_STATE` (targeted test) | 0 |
| valid input / `invoke` and checkpoint `invoke(None, config)` | normal completion/resume (targeted tests) | expected |
| unknown-only / `batch` | **`COMPLETED / WORKFLOW_COMPLETED`** | **3** |
| unknown-only / `ainvoke` | **`COMPLETED / WORKFLOW_COMPLETED`** | **3** |
| fresh-adapter recovery/crash tests | 15 tests OK | no duplicate effect |
| FAIL→PASS and identity/binding mutation tests | rejected before apply | 0 unauthorized |

No repository source was modified for mutations; all injections were runtime-only, so restoration was unnecessary. The only file written by this reviewer is this iteration-2 review artifact.

## Final Decision

Iteration 1 F-001: RESOLVED for the specifically requested synchronous `invoke` reproduction and launcher entry guard.  
C-001 / M-001 / M-003 / M-004: regression samples remain passing.  
M-002 overall: NOT FULLY RESOLVED because standard execution paths exposed by the compiled-graph façade still accept the same unknown-only malformed input and execute effects.

F-002 is a working-result failure under G2, so this BUGFIX correction re-review remains `RESULT: FAIL` with one blocking finding.
