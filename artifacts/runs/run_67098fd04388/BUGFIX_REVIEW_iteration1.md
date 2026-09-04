RESULT: FAIL
DECISION_GATE_STATE: CLEAR
```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The phase can be judged against the explicit cleanup and deterministic-test requirements without any user-owned choice.",
  "scope": "This phase's own conduct at this iteration."
}
```
UNIT_TEST_STATUS: PASS

# BUGFIX review, iteration 1

## Blocking findings

### B1 — G1: `LeaseKeeper.stop()` does not reliably clean up its execution resource

`stop()` sets the event, calls an optional waiter cancel hook, joins only for
`join_seconds`, and then discards `self._thread` even when the thread is still alive. A
heartbeat implementation can block on file I/O, lock acquisition, or any wrapped ledger
operation. In that case context-manager exit returns with the daemon thread still executing;
when the call later unblocks it can even land one late renewal after the protected external
call has ended. Daemon status only affects interpreter shutdown and does not satisfy the
explicit requirement to clean heartbeat resources on every success, exception, and
cancellation path.

Reproduction:

```
PYTHONPATH=. python3 artifacts/runs/run_67098fd04388/reviewer/repro_keeper_cleanup.py
threads_alive_after_stop=1
keeper_reference_after_stop=None
threads_alive_after_release=0
```

The deterministic reproducer blocks `heartbeat()`, uses a 10 ms join bound, calls `stop()`,
and inspects `threading.enumerate()`. Full output is in
`reviewer/repro_keeper_cleanup_output.txt`.

### B2 — G1/G5: the principal healthy-owner concurrency test depends on wall-clock expiry

The requested scenarios 1–4 test uses a real clock, a 1.5-second lease, and repeated 20 ms
real-time waits until a heartbeat timestamp passes the original deadline. Its load-bearing
mutation test explicitly waits `LEASE_SECONDS + 0.2`. This contradicts the explicit request
to avoid unnecessary real-time sleeps and use synchronization primitives or a controlled
clock for deterministic concurrency testing. The separately injected keeper tests show the
necessary mechanism already exists, but the actual A/B production concurrency proof remains
scheduler/timing dependent.

Evidence:

```
scripts/test_deterministic_workflow_lease_keeper.py:369-378
  step = 0.02
  ...
  threading.Event().wait(step)
scripts/test_deterministic_workflow_lease_keeper.py:399
  threading.Event().wait(LEASE_SECONDS + 0.2)
```

Five repetitions happened to pass; that does not turn a wall-clock assertion into a
deterministic test. Output: `reviewer/new_tests_repeat.txt`.

## Ten required review areas

1. **Keeper exists — PASS.** Production call site is
   `lease_keeper.py:172`; executor constructs it for every non-settled claim. The interval is
   derived as `lease_seconds / 3` (default 20s for a 60s lease), with injectable interval and
   waiter. Focused tests passed.

2. **Blocking-path coverage — PASS.** Direct inspection with
   `rg -n "adapter\\.(start|resume)|_settle_now|_collect|_recover|heartbeat" ...` shows the
   context in `_execute_recoverable()` covers CREATED `_settle_now/start`, recovery
   `settlement/lookup`, and EFFECTED `_collect/resume`. Ownership checkpoints precede ledger
   writes following calls.

3. **Fail closed — PASS.** Rotated-token and injected-renewal-error tests both produce
   `IDEMPOTENCY_LEASE_LOST`; settlement remains absent. Launcher projection test leaves phase
   iterations at zero and terminal status BLOCKED. The loss exception is not
   `RuntimeStateLeaseHeld`, so it does not re-enter observer/takeover retry.

4. **Resource cleanup — FAIL.** Normal and ordinary exception tests pass, but blocked
   heartbeat reproduction leaves one keeper thread alive after `stop()` (B1). There is no
   automated cancellation-path executor test in the new suite.

5. **Thread safety — PASS.** `FileRuntimeStateStore` now serializes threads with an RLock held
   over the full critical section; the adversarial cross-thread depth test passed and observed
   `[1]` only after the holder released.

6. **Recovery regression — PASS.** Dead-owner CLAIMED and EFFECTED recovery tests passed.
   Direct stale-owner fencing plus real process race command:
   `python3 -m unittest -v ...ConcurrentClaimTests.test_two_processes_racing_one_intent_start_the_effect_exactly_once ...LeaseFencingTests.test_the_full_takeover_sequence_refuses_every_stale_write`
   ran 2 tests in 2.637s, OK.

7. **Six scenarios — PARTIAL/FAIL.** All represented tests pass, including B creating zero
   effects and A owning the only settlement, takeover after stopped beats, and rotated/error
   renewal fencing. Scenarios 1–4 are not deterministic as explicitly required (B2).

8. **Flakiness — PARTIAL/FAIL.** New file repeated five times, each `Ran 14 tests ... OK`;
   full suite ran twice identically. Nevertheless the core scenario and mutation depend on
   elapsed real time, so the explicit determinism gate is not met.

9. **Mutation — PASS.** I replaced the production heartbeat call with `pass`; the keeper
   renewal unit test failed with `1000060.0 not greater than 1000180.0`. After restoration,
   SHA-256 was identical before and after:
   `fc2e7dcb2a2d0df6c5e0bc0224f4a8b173a39ab8f903e7320e58a51f2928d576`.
   Both source and installed mirror have that digest. Evidence:
   `reviewer/mutation_noop_output.txt`, `pre_mutation_sha256.txt`, and
   `post_mutation_sha256.txt`.

10. **Mirror and gates — PASS except the blocking findings above.** Commands and results:

    - `diff -r -x '__pycache__' scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow` — exit 0.
    - `python3 scripts/validate_skills.py` — `Skill validation PASSED (730 checks)`.
    - `python3 scripts/verify_package.py` — `Package verification PASSED (239 source files)`.
    - `python3 scripts/validate_workflow_graph_docs.py` — PASSED.
    - `python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo` — `terminal_status=COMPLETED ... steps=68`, exit 0.
    - `python3 artifacts/runs/run_67098fd04388/dependency_absent_lane.py` — 235 tests, errors=0, failures=0, skipped=83.
    - `git diff --check` — exit 0.

## Full suite (direct reviewer runs)

Run 1 final lines:

```
Ran 1960 tests in 341.644s
OK (skipped=6)
```

Run 2 final lines:

```
Ran 1960 tests in 337.918s
OK (skipped=6)
```

## Non-blocking notes

The first attempted demo command used a nonexistent path
`scripts/deterministic_workflow/run_workflow.py`; I corrected it to the packaged entry point
`orca-worker-reviewer-orchestration/tools/run_workflow.py`, which completed successfully.
Reviewer-created files are confined to the new run artifact directory; production code was
not modified, and the temporary mutation was fully restored.
