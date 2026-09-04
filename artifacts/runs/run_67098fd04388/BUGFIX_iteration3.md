STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "F-ADV-01 is a defect inside the already-authorized fail-closed requirement; the fix, its regression tests and the doc correction follow directly from the explicit requirement text and needed no user-owned product or scope decision.",
  "scope": "This phase's own conduct at this iteration."
}
```

# BUGFIX iteration 3 — F-ADV-01: a renewal failure landing after the last checkpoint

## 1. Reproduction, before the fix

The Final Reviewer's deterministic reproducer, run unchanged on the pre-fix tree:

```text
$ PYTHONPATH=. python3 artifacts/runs/run_67098fd04388/final_reviewer/repro_checkpoint_write_race.py
thread_alive=False
executor_error=None
executor_returned=True        <- success returned
ledger_status=SETTLED
settlement_written=True
effect_count=1
```

Two independent holes let that happen:

1. `_settle_now()` / `_collect()` / `_recover()` checkpointed ownership only *before* each
   write. The injected failure is a `RuntimeStateLockTimeout`, **not** a rotation, so the
   lease token stayed valid and `_fenced()` correctly accepted the write. F-01 fencing cannot
   see this class of failure.
2. `LeaseKeeper.__exit__()` raised only on `not stopped` (orphaned cleanup). It never looked
   at `self.failure`, so a renewal failure the keeper had already recorded died with it.

## 2. The fix

### `scripts/deterministic_workflow/executor.py`

* `executor.py:105` — new `_committed(keeper, write, *args)`: takes the ownership checkpoint,
  performs one ownership-sensitive write, then takes the checkpoint **again**. Whatever the
  keeper learned while the write was in flight is honoured before this executor writes
  anything further, reports success, or advances any state.
* `executor.py:140` (`_settle_now` → `settle`), `executor.py:169` (`_collect` → `settle`),
  `executor.py:188` (`_recover` → `settle`), `executor.py:209` (`_recover` →
  `record_receipt`): every ownership-sensitive write now goes through `_committed`.

The write that has already landed is left standing on purpose. It is the durable record of an
external effect that really did settle; a successor claiming the intent adopts it as
`ALREADY_SETTLED` instead of re-running the work. What fails closed is *this* executor — it
raises `LeaseRenewalFailed` → `IDEMPOTENCY_LEASE_LOST` and advances no workflow state.

### `scripts/deterministic_workflow/lease_keeper.py`

* `lease_keeper.py:178-193` — `__exit__` now fails closed on **both** halves of `degraded`.
  `not stopped` still reports `cleanup_error()` first (iteration 2 behaviour unchanged); a
  recorded renewal failure is then re-raised by `raise_if_lost()`. Exit is the last checkpoint,
  the one covering the instant between the final write and the block closing, where the body
  has no checkpoint left to take.
* The iteration 2 property is preserved verbatim: both branches run **only** when
  `exc_type is None`, so an exception the body already raised is never masked.

### `docs/DETERMINISTIC_WORKFLOW.md`

* New bullet "Checkpoints on both sides of a write, and at the exit" in *Lease renewal during
  long external work*.
* The "Known limits" sentence the reviewer flagged as dishonest is corrected: the fence covers
  a *rotated* token in that window; a renewal failure that leaves the token valid is caught by
  the post-write checkpoint (or, for the last write, at the keeper's exit).

### `scripts/test_deterministic_workflow_lease_keeper.py`

* `test_a_failed_renewal_stops_the_keeper_and_is_raised_at_the_checkpoint` (line 370) updated:
  its `with keeper:` block ends after a recorded failure, which under the corrected contract
  must now raise at exit. The assertion was tightened, not weakened — the body's own
  `raise_if_lost()` assertion and the "one renewal, never retried" assertion are unchanged.

Nothing established in iterations 1–2 was altered: the lease-derived period, the `RLock`,
wedged-shutdown handling (orphaned / revoked / retained handle), determinism, the six
scenarios, and original-exception preservation are all untouched.

## 3. Regression tests — `scripts/test_deterministic_workflow_lease_keeper.py`

New class `CheckpointToWriteRaceTests` (line 1144) plus helpers `ParkedSettleLedger` (1038),
`BeatOnExit` (1070), `ReceiptWriteLedger` (1098), `LookupOnlyAdapter` (1124). All Event-driven;
no wall-clock sleeps, `ManualLeaseClock` for anything that turns on lease time.

| test | gap it closes |
| --- | --- |
| `test_a_renewal_failing_while_the_settlement_write_is_in_flight_is_not_swallowed` (1156) | The reviewer's exact interleaving, ported: settle parked → failing beat → write released. Asserts `IDEMPOTENCY_LEASE_LOST`, no returned state, `effect_count == 1`. |
| `test_a_renewal_failing_after_the_last_write_fails_the_keepers_exit_closed` (1191) | Failure recorded in the one gap no write can cover — after the last checkpoint, as the keeper is retired. Asserts the exit reports it. |
| `test_a_renewal_failing_during_the_receipt_write_stops_before_the_next_call` (1229) | `_recover` rung: renewal fails inside `record_receipt`; asserts `adapter.resume_calls == 0`, i.e. a superseded owner never touches the external runtime again. |
| `test_the_exit_checkpoint_never_masks_the_exception_the_body_raised` (1254) | Iteration 2 property: `ZeroDivisionError` still propagates; the failure stays observable. |
| `test_a_clean_run_still_exits_without_raising` (1273) | The exit checkpoint fences out failure, not healthy completion. |

Pre-fix run of the new class, both guards removed:

```text
$ python3 -m unittest scripts.test_deterministic_workflow_lease_keeper.CheckpointToWriteRaceTests
FAIL: test_a_renewal_failing_after_the_last_write_fails_the_keepers_exit_closed
AssertionError: None is not an instance of <class '...IdempotencyRecoveryError'> : a renewal
  failure recorded after the last write must be reported by the keeper's exit, not die with
  the keeper
FAIL: test_a_renewal_failing_while_the_settlement_write_is_in_flight_is_not_swallowed
AssertionError: None is not an instance of <class '...IdempotencyRecoveryError'> : a renewal
  that failed during the write must fail the node closed, not be swallowed because the token
  was still valid
Ran 4 tests in 0.005s
FAILED (failures=2)
```

Post-fix, focused file, 5 consecutive repeats:

```text
$ for i in 1 2 3 4 5; do python3 -m unittest scripts.test_deterministic_workflow_lease_keeper; done
Ran 27 tests in 0.775s   OK
Ran 27 tests in 0.796s   OK
Ran 27 tests in 0.789s   OK
Ran 27 tests in 0.786s   OK
Ran 27 tests in 0.769s   OK
```

22 → 27 tests: +5 new, no test removed.

## 4. The reviewer's reproducer, after the fix

```text
$ PYTHONPATH=. python3 artifacts/runs/run_67098fd04388/final_reviewer/repro_checkpoint_write_race.py
thread_alive=False
executor_error=IdempotencyRecoveryError('IDEMPOTENCY_LEASE_LOST:LEASE_RENEWAL_FAILED:intent_e61b1c02802aa70d84a25aa7: the lease could not be renewed during the external call (RuntimeStateLockTimeout: injected renewal failure); this executor no longer owns the intent and must not record a receipt or settlement')
executor_returned=False       <- fail-closed
ledger_status=SETTLED
settlement_written=True
effect_count=1
```

`executor_returned=False` with the named `IDEMPOTENCY_LEASE_LOST` terminal: the node no longer
reports success and the workflow does not advance. `settlement_written=True` is the write that
had already been released into the store before the failure could be observed — see §2 for why
that record is kept rather than orphaning a real external effect, and §7 for the residual.

## 5. Mutation verification (each guard independently load-bearing)

`md5` before mutation, after mutation, and after restoration:

| file | md5 (fixed) | md5 (restored) |
| --- | --- | --- |
| `scripts/deterministic_workflow/executor.py` | `c8db72aab31900b74a5ebba652d9de27` | `c8db72aab31900b74a5ebba652d9de27` |
| `scripts/deterministic_workflow/lease_keeper.py` | `29b7f9a30e81f3b2e61d5255b993fead` | `29b7f9a30e81f3b2e61d5255b993fead` |

**M1 — drop the post-write `_still_owned(keeper)` from `_committed`:**

```text
FAIL: test_a_renewal_failing_during_the_receipt_write_stops_before_the_next_call
AssertionError: 1 != 0 : an executor whose renewal failed during the receipt write must not go
  on to touch the external runtime again
Ran 5 tests in 0.006s
FAILED (failures=1)
```

**M2 — drop `self.raise_if_lost()` from `LeaseKeeper.__exit__`:**

```text
FAIL: test_a_renewal_failing_after_the_last_write_fails_the_keepers_exit_closed
AssertionError: None is not an instance of <class '...IdempotencyRecoveryError'>
FAIL: test_a_failed_renewal_stops_the_keeper_and_is_raised_at_the_checkpoint
AssertionError: LeaseRenewalFailed not raised
Ran 27 tests in 0.789s
FAILED (failures=2)
```

After restoring both files the md5 digests match the fixed originals exactly (table above) and
`Ran 27 tests ... OK`.

## 6. Full verification

| check | result |
| --- | --- |
| full suite ×2 | `Ran 1973 tests in 334.118s` / `OK (skipped=6)` and `Ran 1973 tests in 336.756s` / `OK (skipped=6)` |
| focused file ×5 | `Ran 27 tests` / `OK` (all five) |
| `validate_workflow_graph_docs.py` | `Workflow graph documentation validation PASSED` |
| `validate_skills.py` | `Skill validation PASSED (730 checks)` |
| `verify_package.py` | `Package verification PASSED (239 source files)` |
| dependency-absent lane | `Ran 221 tests` / `OK (skipped=82)` / `LANE errors=0 failures=0 skipped=82` |
| `run_workflow.py --demo` | `terminal_status=COMPLETED reason=WORKFLOW_COMPLETED steps=68`, exit 0 |
| `git diff --check` | clean |
| mirror parity | `diff -r -x __pycache__ scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow` → no output |

Suite count 1968 → 1973: exactly the five new tests. No pre-existing test was deleted or
weakened; the one edited test (§2) asserts strictly more than it did before.

Files modified: `scripts/deterministic_workflow/executor.py`,
`scripts/deterministic_workflow/lease_keeper.py`,
`scripts/test_deterministic_workflow_lease_keeper.py`,
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/executor.py`,
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/lease_keeper.py`,
`docs/DETERMINISTIC_WORKFLOW.md`.
No existing run artifact was touched; the only new artifact is this file.

## 7. Residual limitations, stated plainly

* **A write already released into the store cannot be un-written.** When the renewal fails
  while the settle write is in flight, that ledger record lands. The executor fails closed —
  it returns nothing and advances no workflow state — but the record stands. This is a
  deliberate choice, and the alternative is worse: refusing to record a settlement for an
  external effect that really did complete orphans that effect. A successor claiming the intent
  reads `ALREADY_SETTLED` and adopts the settlement, so the work is never duplicated and never
  lost. Making the refusal *atomic* instead would require the guard to be evaluated inside the
  store's own critical section — an optional `guard` parameter threaded through
  `RuntimeStatePort.settle`/`record_receipt` and every implementation and test double of them.
  That is a port-contract change well outside what this review raised, so it was not taken.
* The receipt of a lease that merely **expired with nobody taking over** is still written, as
  required — only an *explicitly failed* renewal fails closed.
* Unchanged from iteration 2: renewal cannot interrupt a blocking adapter call; a renewal
  already inside the ledger when `stop()` runs may extend an abandoned lease by at most one
  period; the default waiter uses real seconds, so a test-clock run must inject a waiter.
* Not re-verified this iteration (unchanged code, and out of the review's scope): a new
  multi-process ledger-corruption stress harness and a many-intent throughput test.
