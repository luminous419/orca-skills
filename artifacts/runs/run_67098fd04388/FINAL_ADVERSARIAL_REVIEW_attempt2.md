RESULT: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "Attempt 2 found F-ADV-01 closed and no remaining G1-G5 defect; all judgments follow from the already-authorized fail-closed, recovery, fencing, and deterministic-test requirements.",
  "scope": "This phase's own conduct at this iteration."
}
```

UNIT_TEST_STATUS: PASS

# Final adversarial review — attempt 2

## Executive verdict

F-ADV-01 is closed. The original interleaving now raises `IDEMPOTENCY_LEASE_LOST` and returns no
workflow state; the settlement already in flight remains a durable fact and is handled by the
previously verified `ALREADY_SETTLED` recovery boundary. I found no blocking G1-G5 defect in the
new post-write/exit checks, the newly exercised many-intent behavior, cross-process corruption
behavior, or focused Round 1–2 regressions.

## Newly attempted attacks and actual results

### 1. Original F-ADV-01 reproduction after iteration 3

Command:

```text
PYTHONPATH=. python3 artifacts/runs/run_67098fd04388/final_reviewer/repro_checkpoint_write_race.py
```

Output:

```text
thread_alive=False
executor_error=IdempotencyRecoveryError('IDEMPOTENCY_LEASE_LOST:LEASE_RENEWAL_FAILED:...RuntimeStateLockTimeout: injected renewal failure...')
executor_returned=False
ledger_status=SETTLED
settlement_written=True
effect_count=1
```

The executor no longer reports success or advances workflow state. The landed settlement is the
already-issued write, not a later write after failure; it preserves the completed external fact
for successor adoption and does not permit a duplicate effect.

### 2. Failures at other write locations

Command:

```text
python3 -m unittest scripts.test_deterministic_workflow_lease_keeper.CheckpointToWriteRaceTests
```

Output after restoration of the mutation described below:

```text
Ran 5 tests in 0.006s
OK
```

These five Event-driven cases cover failure while settlement is in flight, after the last write,
during receipt recording before `_collect` calls `resume`, original-exception preservation, and a
clean exit. Static enumeration also found no executor-owned receipt/settlement write outside
`_committed`: `_settle_now`, `_collect`, and both `_recover` write sites all use it. Adapter-owned
writes remain fenced by the same token and the executor checkpoints immediately after the blocking
adapter call; an already-issued adapter write cannot be rolled back, but cannot produce successful
workflow advancement after the recorded renewal failure.

### 3. Many simultaneous intents and keeper interference

New harness:
`artifacts/runs/run_67098fd04388/final_reviewer_attempt2/stress_guards.py`.
It claims 32 distinct intents in one real `FileRuntimeStateStore`, starts 32 keeper threads, and
releases their beats concurrently with synchronization primitives.

```text
PYTHONPATH=. python3 artifacts/runs/run_67098fd04388/final_reviewer_attempt2/stress_guards.py
many_intents=32 beat_ok=True records_ok=True errors=[]
corruptor_exit=0 all_keepers_lost=True ledger_not_repaired=True
```

The per-store `RLock` serializes the threads without deadlock, lost records, or cross-intent token
interference. This is a contention/stability result, not a throughput benchmark.

### 4. Real cross-process ledger corruption while keepers are active

The second half of the same harness uses multiprocessing `spawn`. A separate process acquires the
store's actual flock and replaces the records container with a closed-schema violation. All 32
subsequent keepers fail renewal, and a read proves the malformed ledger remains rejected rather
than being silently reset or overwritten (`all_keepers_lost=True ledger_not_repaired=True`). This
confirms fail-closed interaction between process locking, closed-ledger validation, and keeper
threads.

### 5. Production mutation proof and exact restoration

I removed only `_committed`'s post-write `_still_owned(keeper)` from the production source and ran:

```text
python3 -m unittest scripts.test_deterministic_workflow_lease_keeper.CheckpointToWriteRaceTests
```

Actual mutation output:

```text
FAIL: test_a_renewal_failing_during_the_receipt_write_stops_before_the_next_call
AssertionError: 1 != 0 : an executor whose renewal failed during the receipt write must not go on to touch the external runtime again
Ran 5 tests in 0.007s
FAILED (failures=1)
```

After restoring that exact line, all five passed. SHA-256 before and after restoration matched:

```text
a2549c1c1f1175896ad8f5c79aee8bc98a68ea84870fd0e1771677c42c16f911  scripts/deterministic_workflow/executor.py
6d35cbc6032ae20d8bf30ba5f75e49ab89b154171f3f2f0533ec4503c8aae6f6  scripts/deterministic_workflow/lease_keeper.py
```

This independently proves the new post-write checkpoint is load-bearing. Production files were
not left modified by the review.

## Iteration-3 risk review

- A post-write checkpoint exception intentionally leaves the already committed fact in the
  runtime ledger while preventing this executor from returning state. This is consistent rather
  than contradictory: durable runtime fact and workflow advancement are separate transitions,
  and successor recovery consumes the former.
- If cleanup failure and renewal failure coexist on a clean body, `LeaseKeeperNotStopped` wins.
  The renewal cause is not included in that exception, but neither condition is swallowed into
  success: both are subclasses of `LeaseRenewalFailed` and map to `IDEMPOTENCY_LEASE_LOST`.
- If the body already raises, `__exit__` preserves it. This can make a simultaneous keeper failure
  secondary diagnostic state rather than the surfaced exception, but the executor still cannot
  return or advance state.
- The executor's four ownership-sensitive write sites are all inside `_committed`. Direct writes
  in adapters use the same fenced lease token; they are not workflow-state writes.

## Regression evidence

Command:

```text
python3 -m unittest \
  scripts.test_deterministic_workflow_lease_keeper.CheckpointToWriteRaceTests \
  scripts.test_deterministic_workflow_ownership \
  scripts.test_deterministic_workflow_round2
```

Output:

```text
Ran 120 tests in 7.690s
OK
```

This covers the new race cases plus C2-001 exclusive claims, closed-ledger validation,
M2-001–004, F-01 stale-owner fencing, expired takeover, and the existing process-race tests.

## Blocking findings

None.

## Non-blocking notes

- When cleanup failure and renewal failure coincide, the cleanup exception has priority and does
  not embed the renewal cause. Preserving both could improve diagnostics, but fail-closed behavior
  and the terminal classification are unchanged.
- The 32-intent attack establishes absence of deadlock/corruption in the exercised synchronized
  burst. It does not establish a performance SLA or characterize throughput under sustained load.

## Areas not independently verified

- I did not repeat the coordinator/phase-reviewer full 1973-test runs, package validators,
  dependency-absent lane, demo, or mirror comparison. They were already independently run after
  iteration 3; this attempt instead exercised the previously omitted adversarial areas.
- The corruption stress ran one separate corrupter process against 32 keeper threads. It did not
  fuzz arbitrary byte-level corruption patterns or kill a process during atomic rename.
- I did not benchmark real 5–15 minute Orca/Claude/Codex dispatches; deterministic blocking and
  keeper lifecycle tests stand in for external runtime duration.

