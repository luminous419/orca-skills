RESULT: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The iteration-3 change and its acceptance boundary are determined by the already-authorized fail-closed, fencing, recovery, and no-duplicate-effect requirements; no user-owned product or scope decision remains open.",
  "scope": "This phase's own conduct at this iteration."
}
```

UNIT_TEST_STATUS: PASS

# BUGFIX review iteration 3

## Executive verdict

F-ADV-01 is closed. The executor now checks keeper failure both after every ownership-sensitive
write and on clean context exit, so a renewal failure in either checkpoint-to-write race no longer
allows the node to return or advance workflow state. I found no blocking G1-G5 regression.

The remaining `settlement_written=True` in the deliberately parked-write interleaving is
**acceptable**. It is not a write by an owner known to have lost ownership: the token remains the
current valid token and the store operation was already in flight before a non-rotating heartbeat
error became observable. The executor nevertheless fails closed, the stored event is the adapter's
actual completed result, and a successor adopts that exact event through `ALREADY_SETTLED` without
creating another effect. Removing that fact after an external effect completed would instead create
an orphan/duplication hazard. Atomic refusal would require a new store transaction/guard contract;
that is not necessary to satisfy the stated ownership-loss prohibition or the operational
fail-closed outcome, so the Worker's scope judgment is justified rather than evasive.

## 1. Production lease keeper

- Inspection: `scripts/deterministic_workflow/executor.py` wraps every non-settled claimed intent
  in the production `lease_keeper_factory`; `scripts/deterministic_workflow/lease_keeper.py::_run`
  invokes `runtime_state.heartbeat(intent_id, lease_token)`.
- Period: `heartbeat_interval_for(runtime_state.lease_seconds)` derives lease/3 (with only a 1 ms
  floor), and `interval_seconds` plus `waiter` are injectable.
- Result: focused lease-keeper suite passed five consecutive times, each `Ran 27 tests ... OK`.

## 2. Blocking-path coverage

Inspection of `_settle_now`, `_collect`, and `_recover` confirms the keeper covers `start`,
`resume`, lookup/settlement observation, receipt recording, and settlement recording. Every
ownership-sensitive write uses `_committed`, whose checks bracket the write; exit supplies the
last checkpoint after the final write.

## 3. Fail-closed and the accepted write boundary

Command:

```text
PYTHONPATH=. python3 artifacts/runs/run_67098fd04388/reviewer_iteration3_semantics.py
```

Output:

```text
a_failed_closed=True
stored_marker=truth
b_adopted_same_event=True
b_external_effects=0
expired_without_takeover_receipt_status=EFFECTED
```

Thus A returns no state after the renewal failure; the record contains the real adapter outcome,
not fabricated/stale content; and B reads precisely that stored event with zero external effects.
The store also validates settlement shape/digest before persistence. The wording in the Worker
report and `docs/DETERMINISTIC_WORKFLOW.md` explicitly discloses that an already-released write can
land and distinguishes it from executor/workflow success; it is candid, not minimized.

## 4. Cleanup and wedged shutdown

`LeaseKeeper.__exit__` always invokes `stop()`. Existing deterministic focused tests cover success,
body exception/cancellation-equivalent unwinding, a wedged heartbeat, sticky orphan status,
revocation before join, retained live thread handle, and original-exception preservation. All 27
passed in each of five repeats. The revocation checks immediately before and after heartbeat ensure
a surviving wedged thread performs at most its already-issued renewal and cannot continue renewing.

## 5. Thread safety

`FileRuntimeStateStore` uses a real `threading.RLock` and thread-local recursion depth. The focused
`ThreadSafeLedgerLockTests` passed; a renewal thread could not inherit another thread's depth or
enter its critical section early. No regression from the iteration-2 repair was found.

## 6. Recovery and Round-2 fencing/race regression

Command:

```text
python3 -m unittest scripts.test_deterministic_workflow_ownership scripts.test_deterministic_workflow_round2
```

Output:

```text
Ran 115 tests in 7.602s
OK
```

This includes stale-token receipt/settlement/heartbeat fencing, expired-lease takeover, and the real
8-process claim race. `DeadOwnerRecoveryTests` also passed in all five focused repetitions: stopping
beats permits expiry and takeover/recovery, and an existing effect is collected rather than rerun.

## 7. Six required concurrency scenarios

`HealthyLongRunningOwnerTests`, `DeadOwnerRecoveryTests`, and `RenewalFailureTests` collectively
exercise all six required scenarios with `ManualLeaseClock`, `Event`, and an injected waiter. The
healthy-owner test proves A runs past a lease interval, B remains observer/no second effect, and A
alone records receipt/settlement. The dead-owner tests prove takeover after beats cease. Token
rotation and renewal-error tests prove A is fenced and workflow state does not advance. All passed
five times as part of the 27-test focused file.

## 8. Determinism and flakiness

The new tests do not use `sleep` to advance lease time. `Event.wait(JOIN_TIMEOUT)` and bounded joins
are watchdog ceilings only; progress is released explicitly by events/pacers and lease time by
`ManualLeaseClock.advance`. Focused suite x5 was stable (`27/27 OK` each). Full suite x2 was stable:

```text
Run 1: Ran 1973 tests in 334.867s — OK (skipped=6)
Run 2: Ran 1973 tests in 336.384s — OK (skipped=6)
```

## 9. Independent mutation evidence and restoration

Initial SHA-256:

```text
a2549c1c1f1175896ad8f5c79aee8bc98a68ea84870fd0e1771677c42c16f911  scripts/deterministic_workflow/executor.py
6d35cbc6032ae20d8bf30ba5f75e49ab89b154171f3f2f0533ec4503c8aae6f6  scripts/deterministic_workflow/lease_keeper.py
```

- Removed only `_committed`'s post-write `_still_owned`: the five-test race class failed
  `test_a_renewal_failing_during_the_receipt_write_stops_before_the_next_call` because
  `resume_calls` became 1.
- Restored it, then removed only `LeaseKeeper.__exit__`'s `raise_if_lost`: the exit-race test
  failed because `error` was `None`; the existing keeper test also failed because
  `LeaseRenewalFailed` was not raised.
- Restored both. SHA-256 values exactly matched the two initial values, and the race class returned
  `Ran 5 tests ... OK`.

These mutations independently establish both guards are load-bearing and reproduce the respective
pre-fix behavior without elapsed-time scheduling.

## 10. Complete gates

```text
python3 -m unittest discover -s scripts -p 'test_*.py'
  Run 1: Ran 1973 tests in 334.867s / OK (skipped=6)
  Run 2: Ran 1973 tests in 336.384s / OK (skipped=6)
python3 artifacts/runs/run_8288bf8f1d89/dependency_absent_lane.py
  Ran 221 tests in 2.437s / OK (skipped=82) / LANE errors=0 failures=0 skipped=82
python3 scripts/validate_skills.py
  Skill validation PASSED (730 checks)
python3 scripts/verify_package.py
  Package verification PASSED (239 source files)
python3 scripts/validate_workflow_graph_docs.py
  Workflow graph documentation validation PASSED
python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo
  terminal_status=COMPLETED reason=WORKFLOW_COMPLETED ... steps=68
diff -r -x '__pycache__' scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow
  no output (identical)
git diff --check
  no output (clean)
```

## Non-blocking notes

- A heartbeat already inside the store when shutdown revokes the keeper can extend the lease once;
  it cannot repeat, so takeover is delayed by at most one lease interval. This is accurately
  documented and does not violate the existing expiry/recovery behavior.
- The reviewer-only semantic reproduction is
  `artifacts/runs/run_67098fd04388/reviewer_iteration3_semantics.py`; production sources were not
  changed by this review.
