STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "Both blocking findings were fully specified by the reviewer (verify the join and block the orphan; make the scenario 1-4 proof and its mutation deterministic). Every remaining choice -- how to signal a failed shutdown, which primitive paces the observer -- is an engineering decision inside the phase contract, resolved against the existing fail-closed rule and the explicit determinism requirement, with no user-owned authority involved.",
  "scope": "This phase's own conduct at this iteration."
}
```

# BUGFIX iteration 2 — correction round

Scope: the two blocking findings in `BUGFIX_REVIEW_iteration1.md`. The iteration-1 behaviour the
Coordinator verified directly (lease-derived renewal period, fail-closed checkpoints, the
`_locked()` RLock, the six scenarios' meaning) is unchanged.

---

## BLOCKING 1 — `stop()` orphaned the beat thread

### Reproduction before the fix

`artifacts/runs/run_67098fd04388/iteration2/repro_orphan.py` wedges a renewal inside the ledger
and calls `stop()`. Run against the pre-fix `stop()`/beat loop
(`iteration2/repro_orphan_prefix.txt`):

```
stop() returned: None
keeper._thread is now: None
keeper.orphaned: False
   STILL LIVE: name=lease-keeper-intent_x alive=True daemon=True
live lease-keeper threads after stop(): 1
heartbeat calls in total: 1
beats the keeper counted: 1
```

The join timed out, nobody checked, the handle was already `None`, and the keeper reported a
clean shutdown while still holding a live renewal thread.

Same script after the fix (`iteration2/repro_orphan_postfix.txt`):

```
stop() returned: False
keeper._thread is now: <Thread(lease-keeper-intent_x, started daemon 6150221824)>
keeper.orphaned: True
   STILL LIVE: name=lease-keeper-intent_x alive=True daemon=True
live lease-keeper threads after stop(): 1
heartbeat calls in total: 1
beats the keeper counted: 0
```

The thread that is wedged *inside* a ledger write still cannot be aborted — nothing can abort
it — but it is now reported, observable, revoked, and credited with nothing.

### What changed

`scripts/deterministic_workflow/lease_keeper.py` (mirror byte-identical):

* `lease_keeper.py:50` — new `LeaseKeeperNotStopped(LeaseRenewalFailed)`. Cleanup that fails is
  the mirror image of renewal that fails, so it is the same kind of exception and takes the same
  fail-closed path.
* `lease_keeper.py:139` `stop()` — now returns `bool` and does three things in order:
  **revoke** (`_revoked`) before anything else, so no further renewal may be written; **wake and
  join** (the waiter's `cancel()` makes the bound a safety limit, not a pause); **verify** —
  `self._thread` is dropped only once `is_alive()` is False, and a thread still running after the
  bound sets `_orphaned` (`lease_keeper.py:169`) and returns `False`. `_orphaned` is sticky: a
  cleanup that failed is never later reported as clean. Repeat calls stay safe and re-attempt the
  join, reaping a thread that has since finished.
* `lease_keeper.py:178` `__exit__` — raises `cleanup_error()` when the shutdown failed **and** the
  body did not raise; when the body is already raising, the original exception is not masked and
  the failure stays observable on the keeper.
* `lease_keeper.py:205/211/216/220` — `orphaned`, `degraded`, `revoked`, `cleanup_error()`.
* `lease_keeper.py:254` and `:266` — the beat loop re-reads the revocation flag immediately
  **before** the renewal write (a period can fall due in the gap between the loop reading the stop
  event and issuing the write) and immediately **after** it (a renewal that was already in flight
  is not counted as a beat, and the thread exits instead of looping). An abandoned lease therefore
  lapses after at most that one in-flight period instead of being renewed indefinitely.
* `scripts/deterministic_workflow/executor.py:222-234` — comment only; `LeaseKeeperNotStopped` is a
  `LeaseRenewalFailed`, so the existing `except` already turns it into `IDEMPOTENCY_LEASE_LOST`
  and the executor cannot report success on top of a keeper it could not retire.

### Fixing tests

`scripts/test_deterministic_workflow_lease_keeper.py`, new `KeeperShutdownTests` (`:418`) and
`KeeperShutdownExecutorTests` (`:562`) — 7 tests:

| test | line | proves |
| --- | --- | --- |
| `test_a_stop_that_cannot_retire_the_thread_reports_failure_and_keeps_the_handle` | 452 | the reviewer's reproduction as an assertion: `stop()` False, `orphaned`, handle retained, stickiness |
| `test_a_renewal_completing_after_revocation_is_not_counted_and_ends_the_loop` | 474 | the in-flight write lands but buys nothing; the thread exits, `beats == 0` |
| `test_a_beat_that_falls_due_at_the_instant_of_revocation_writes_nothing` | 495 | the pre-write re-check: the constructed race writes zero renewals |
| `test_the_context_manager_refuses_to_report_a_clean_exit_when_cleanup_failed` | 529 | `__exit__` raises `LeaseKeeperNotStopped` on the success path |
| `test_a_failed_cleanup_never_masks_the_exception_the_body_raised` | 536 | the body's exception wins; the failure stays observable |
| `test_a_clean_shutdown_reports_success_and_stop_stays_idempotent` | 544 | the normal path still returns True, twice |
| `test_an_executor_that_cannot_retire_its_keeper_fails_closed` | 565 | end to end: `IDEMPOTENCY_LEASE_LOST` carrying `LEASE_KEEPER_NOT_STOPPED` |

### Mutation — the fix is load-bearing

`iteration2/mutate_prefix_stop.py apply` restores the pre-fix `stop()`/`__exit__` and removes both
revocation re-checks. All 7 fail (`iteration2/mutation_prefix_stop_output.txt`):

```
FAIL: test_a_beat_that_falls_due_at_the_instant_of_revocation_writes_nothing
AssertionError: 1 != 0 : a revoked keeper renewed the lease of an intent its executor had already released
FAIL: test_a_clean_shutdown_reports_success_and_stop_stays_idempotent
AssertionError: None is not true
FAIL: test_a_failed_cleanup_never_masks_the_exception_the_body_raised
AssertionError: False is not true : the cleanup failure stays observable even when it is not raised
FAIL: test_a_renewal_completing_after_revocation_is_not_counted_and_ends_the_loop
AssertionError: 1 != 0 : a renewal completed after revocation is not a beat this keeper may claim credit for
FAIL: test_a_stop_that_cannot_retire_the_thread_reports_failure_and_keeps_the_handle
AssertionError: False is not true
FAIL: test_the_context_manager_refuses_to_report_a_clean_exit_when_cleanup_failed
AssertionError: LeaseKeeperNotStopped not raised
FAIL: test_an_executor_that_cannot_retire_its_keeper_fails_closed
AssertionError: IdempotencyRecoveryError not raised
Ran 7 tests in 0.376s
FAILED (failures=7)
```

Restored, md5 identical before and after: `e5a28dedbadb402b0e1e3cfffa6dd3bb`
(`iteration2/pre_mutation_md5_lease_keeper.txt`). The mirror copy carries the same digest.

---

## BLOCKING 2 — the scenario 1-4 proof depended on wall-clock time

### Before

```
scripts/test_deterministic_workflow_lease_keeper.py:369-378   step = 0.02 ... threading.Event().wait(step)
scripts/test_deterministic_workflow_lease_keeper.py:399       threading.Event().wait(LEASE_SECONDS + 0.2)
LEASE_SECONDS = 1.5
```

Both waits existed so that real time would pass: a 20 ms poll loop until a heartbeat timestamp
happened to overtake the deadline, and a 1.7 s wait for a lease to actually expire.

### After

Neither wait exists. `grep -n "Event().wait\|time.sleep" scripts/test_deterministic_workflow_lease_keeper.py`
returns nothing, and the file's only remaining unbounded-progress wait is `entered.wait(0.3)` at
`:1012` in the pre-existing `ThreadSafeLedgerLockTests`, which the reviewer passed — it asserts a
*negative* (a second thread must not enter the critical section), which cannot be proved without a
bound. Every other `wait` is `JOIN_TIMEOUT`, an upper limit no passing run reaches.

Changes:

* `:62-72` — `LEASE_SECONDS = rs.DEFAULT_LEASE_SECONDS` (60 s, the production value, now harmless
  because nothing waits for it) and `BEAT_SECONDS = heartbeat_interval_for(LEASE_SECONDS)`, so the
  clock advance per beat cannot drift from the period the production factory derives.
* `:196` new `PacedObserverClock` — B reads the shared `ManualLeaseClock` but cannot *make* time
  pass. `observe()` sleeps between polls and a manual clock's `sleep` advances it, so an observer
  thread left to itself would silently expire the owner's lease; here each poll is granted by the
  test, and `release()` (called only after A has finished) hands B its normal behaviour back.
* `:611` `test_a_healthy_owner_outlasting_its_lease_keeps_it_and_b_never_takes_over` — rewritten.
  Only the keeper's *waiter* and the lease clock are injected; the keeper is the production
  `LeaseKeeper` built by the production `lease_keeper_factory` at the production period
  (asserted: `keeper._interval == BEAT_SECONDS`). The scenarios are now counted facts:
  1. `beats_to_outlast = LEASE_SECONDS // BEAT_SECONDS + 1` beats, each advancing the injected
     clock by exactly one period, then `assertGreater(clock.time(), deadline)`;
  2. `store_b.claim(intent)` raises `RuntimeStateLeaseHeld` mid-flight;
  3. B's node parks in `observe` (`wait_until_parked()`), renewal keeps working while it watches,
     `adapter_b.effect_count == 0`, owner and token unchanged;
  4. A alone settles; B adopts A's `event_id`; the token was never rotated.
* `:692` `test_the_lease_keeper_is_load_bearing` — the wall-clock expiry wait became
  `clock.advance(LEASE_SECONDS + 1.0)`, one line of arithmetic on the injected clock. It now also
  asserts the *harm*: A really created the effect (`effect_count == 1`), its settlement is refused,
  and the ledger holds no settlement.
* `:733` new `test_the_executor_wires_the_production_keeper_when_nothing_is_injected` — because the
  scenario test injects a waiter, this pins what it no longer can: with **no** `keeper_factory`,
  `execute_intent_node` builds a real `LeaseKeeper` around the ledger's own lease token, with the
  lease-derived period and the real `_default_waiter`, and shuts it down cleanly.

### Mutation — still load-bearing after the rewrite

Production heartbeat call replaced with `pass` (the keeper renews nothing):

```
FAIL: test_a_healthy_owner_outlasting_its_lease_keeps_it_and_b_never_takes_over
AssertionError: 1000000.0 not greater than 1000060.0 : the executor must renew the lease during the external call
FAIL: test_a_healthy_owner_outlasting_its_lease_keeps_it_and_b_never_takes_over
AssertionError: True is not false : a worker thread outlived its test
Ran 3 tests in 30.092s
FAILED (failures=2)
```

(`iteration2/mutation_noop_heartbeat_output.txt`.) Restored, md5 identical:
`e5a28dedbadb402b0e1e3cfffa6dd3bb`.

### Determinism, measured

The rewritten file runs in **0.74–0.79 s** (was ~4 s, most of it waiting). Five consecutive runs
(`iteration2/new_tests_repeat5.txt`):

```
Ran 22 tests in 0.778s   OK
Ran 22 tests in 0.742s   OK
Ran 22 tests in 0.762s   OK
Ran 22 tests in 0.786s   OK
Ran 22 tests in 0.791s   OK
```

---

## Verification

| gate | result |
| --- | --- |
| full suite ×2 | `Ran 1968 tests in 333.219s` / `OK (skipped=6)` and `Ran 1968 tests in 335.332s` / `OK (skipped=6)` |
| new file ×5 | 22 tests, OK each time (above) |
| focused regressions | `ownership` + `round2` + `recovery`: `Ran 139 tests in 7.814s` / OK |
| stale-owner fencing + cross-process race | `Ran 2 tests in 2.531s` / OK |
| `validate_workflow_graph_docs.py` | PASSED |
| `validate_skills.py` | `Skill validation PASSED (730 checks)` |
| `verify_package.py` | `Package verification PASSED (239 source files)` |
| dependency-absent lane | `Ran 221 tests` / `OK (skipped=82)`, `errors=0 failures=0` |
| `run_workflow.py --demo` | `terminal_status=COMPLETED ... steps=68`, exit 0 |
| `git diff --check` | exit 0 |
| mirror `diff -r -x __pycache__` | no output |

Suite count 1960 → 1968: the lease-keeper file went from 14 to 22 tests (+7 shutdown, +1 default
wiring). No existing test was weakened or removed.

## Files changed this iteration

* `scripts/deterministic_workflow/lease_keeper.py` (+ mirror)
* `scripts/deterministic_workflow/executor.py` (comment only, + mirror)
* `scripts/test_deterministic_workflow_lease_keeper.py`
* `docs/DETERMINISTIC_WORKFLOW.md`

## Remaining limits (stated, not hidden)

* A renewal already inside the ledger when `stop()` runs cannot be recalled. It lands, is not
  counted, and is followed by no other, so an abandoned lease lapses after at most one further
  period. Closing that window entirely would mean holding a lock across the ledger write, which
  would make `stop()` itself block on the wedged call.
* `ThreadSafeLedgerLockTests` still bounds a negative assertion with `entered.wait(0.3)`. A
  negative cannot be proved without a bound; the reviewer passed this in iteration 1 and it is
  unchanged.
* The keeper still cannot interrupt a blocking adapter call, so a lost lease is detected at the
  next ownership checkpoint rather than the instant it happens — the fence, not the keeper, is what
  prevents the stale write in that window. Unchanged from iteration 1 and documented.
