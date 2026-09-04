# BUGFIX — OS-40 / PR #28 External Review Round 3 (MAJOR)

run_id: `run_67098fd04388` · branch `feat/os-40-langgraph-engine` · reviewed head `a30c1af`
worker: claude-opus · iteration 1 / 5 · phase BUGFIX

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The review named one MAJOR with an explicit required behaviour, and the task fixed the design constraints (executor-managed keeper, lease-derived period, fail-closed, guaranteed cleanup, preserved takeover) and the six test scenarios. Every remaining choice - where the keeper lives, how the beat is paced in tests, how the loss is projected onto a terminal code - was settled inside the existing code's own conventions and verified by tests, so no question required user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```

---

## 1. The finding, reproduced first

`_execute_recoverable()` minted a 60-second lease (`DEFAULT_LEASE_SECONDS`) and then blocked
inside `adapter.start()` for the whole external dispatch without ever calling
`runtime_state.heartbeat()`. Confirmed independently: `heartbeat()` had **zero** production
call sites — only the protocol definition (`ports.py:80`), the implementation
(`runtime_state.py:454`) and docstrings.

Reproduction script (new, this run):
`artifacts/runs/run_67098fd04388/repro_round3_major.py` — the *production* executor node for
Coordinator A with a blocking `adapter.start()` (standing in for a 5–15 minute Claude/Codex
dispatch), a real `FileRuntimeStateStore` on one ledger file, two distinct owner ids, and an
injected `ManualLeaseClock`. No real time passes.

**Before the fix** (`python3 artifacts/runs/run_67098fd04388/repro_round3_major.py`):

```
A claimed the intent and entered adapter.start()
  A lease_token=07b3ad41667f  lease_expires_at=1000060.0
clock +300s (A is healthy and still working, nothing renewing)
B claim -> RESUMED   *** B TOOK OVER A HEALTHY OWNER ***
  B lease_token=934d4af243b8 (rotated: True)
A finishes its healthy work -> IdempotencyRecoveryError: IDEMPOTENCY_OBSERVATION_TIMEOUT:...owner=B
ledger owner=B status=CLAIMED settlement=no
```

A was healthy the entire time, created the only external effect, and still ended BLOCKED with
no settlement recorded. With a 60-second lease and 5–15 minute dispatches this is not an edge
case — it is every real dispatch.

**After the fix** (`... repro_round3_major.py --paced`, each keeper beat *is* 20 s of the
injected clock):

```
A claimed the intent and entered adapter.start()
  A lease_token=147ac5ec4517  lease_expires_at=1000060.0
clock +300s via 15 lease renewals (A is healthy)
B claim -> REFUSED (LEASE_HELD:intent_...:owner=A:expires_at=1000360.0)
A finishes its healthy work -> SETTLED
ledger owner=A status=SETTLED settlement=yes
```

---

## 2. What was changed

### New: `scripts/deterministic_workflow/lease_keeper.py` (201 lines)

| Element | Purpose |
| --- | --- |
| `heartbeat_interval_for(lease_seconds)` | Renewal period **derived from the lease**: `lease / HEARTBEAT_LEASE_DIVISOR (3.0)`, floor 1 ms, falling back to the 60 s default for a nonsensical value. Never hard-coded, so `interval < lease` holds when the lease is reconfigured. 60 s → 20 s. |
| `LeaseKeeper` | Renews one claim on a **daemon** thread for the whole blocking call. Context manager: `stop()` runs on success, exception and cancellation, and joins the thread. |
| fail-closed | The first failed renewal stores the cause, stops the keeper, and is re-raised as `LeaseRenewalFailed` by `raise_if_lost()`. No retry — a retry would paper over a takeover. |
| `LeaseRenewalFailed(RuntimeStateConflict)` | Deliberately **not** a `RuntimeStateLeaseHeld`: losing a lease mid-flight must not send the executor back into the claim/observe path, because this process may already have created the effect. |
| injection | `waiter(stop_event, interval)` and `interval_seconds` are injectable (`lease_keeper_factory`), which is what makes the tests deterministic. The default waits in *real* seconds on purpose: a test clock's `sleep` returns instantly and would busy-spin. |

### `scripts/deterministic_workflow/executor.py`

* `executor.py:95` `_still_owned(keeper)` — the ownership checkpoint, taken **before every
  write that follows an external call**.
* `executor.py:220-231` `_execute_recoverable()` now wraps *all* post-claim execution in
  `with factory(runtime_state, intent_id, lease_token) as keeper:` and converts
  `LeaseRenewalFailed` into `IdempotencyRecoveryError("IDEMPOTENCY_LEASE_LOST", ...)`, which
  the launcher already projects onto a BLOCKED terminal.
  The `ALREADY_SETTLED` branch is outside the keeper: it performs no external call.
* `executor.py:261-283` `execute_intent_node(..., heartbeat_interval_seconds=None,
  keeper_factory=None)` — the injection points; production needs neither.
* `_observe_then_take_over()` threads the same factory into its takeover attempt.

**Every blocking execution path was checked, not just `_settle_now`:**

| Path | Blocking external call | Renewed | Checkpoint before write |
| --- | --- | --- | --- |
| `_settle_now` (`executor.py:105`) | `adapter.start()` | yes | `:113` before `settlement()`, `:116` before `runtime_state.settle()` |
| `_collect` (`executor.py:128`) | `adapter.resume()` | yes | `:139` after resume, `:146` before `settle()` |
| `_recover` (`executor.py:151`) | `adapter.settlement()`, `adapter.lookup()` | yes | `:166` before `settle()`, `:185` before `record_receipt()` |
| `_recover` → `_settle_now` / `_collect` | delegated | yes (same keeper) | as above |
| `_execute_recoverable` `ALREADY_SETTLED` | none (ledger read only) | n/a | n/a |
| `_observe_then_take_over` → `_execute_recoverable` | via the ladder | yes | as above |

### `scripts/deterministic_workflow/runtime_state.py` — thread-safe ledger lock

The keeper renews from a second thread, so `FileRuntimeStateStore._locked()`'s re-entrancy
depth was no longer safe: re-entrancy is per *thread*, and the unguarded counter let a second
thread see `self._depth != 0` and **skip `flock` entirely**, reading and writing the ledger
with no inter-process lock at all. `_locked()` now takes a `threading.RLock` (same idiom as
`InMemoryRuntimeStateStore`) and the real `flock` frame moved into `_flocked()`. Re-entrancy
inside one thread is unchanged; a different thread now blocks.

### `scripts/test_deterministic_workflow_round2.py`

One line: the `_recover` mutation lambda in `test_the_recovery_ladder_is_load_bearing` now
accepts (and forwards) the keeper argument. Behaviour of the test is unchanged; without it the
mutation patch raises `TypeError` on the new signature.

### `docs/DETERMINISTIC_WORKFLOW.md`

New subsection **“Lease renewal during long external work”**: why a fence without renewal
fences out healthy work, the derived period rule, the fail-closed rule and its terminal code,
guaranteed cleanup, why takeover/recovery is preserved, the thread-safety consequence for
`_locked()`, and the remaining limits.

### Mirror

`diff -r -x '__pycache__' scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow`
→ **no output** (byte-identical, `lease_keeper.py` included).

---

## 3. The six required scenarios

New file: `scripts/test_deterministic_workflow_lease_keeper.py` (unittest, 14 tests, 5.2 s).
No test sleeps its way to an assertion: waits are on `Event`/`Condition` primitives, and every
lease-expiry scenario uses `ManualLeaseClock`. The one wall-clock wait is in the mutation test,
where waiting out a lease is the point.

| # | Requirement | Test |
| --- | --- | --- |
| 1 | A's external work outlasts one lease period | `HealthyLongRunningOwnerTests.test_a_healthy_owner_outlasting_its_lease_keeps_it_and_b_never_takes_over` — “outlasted” is read from the ledger (`_wait_for_renewal_past`: a heartbeat timestamped **after** the original expiry), not assumed from a sleep |
| 2 | B claims the same intent mid-flight | same test — `store_b.claim(intent)` raises `RuntimeStateLeaseHeld`, then B's node runs as an observer |
| 3 | B neither takes over nor creates a second effect | same test — `adapter_b.effect_count == 0`; B's result script is empty, so any attempt to start would raise; the ledger token is never rotated |
| 4 | A alone records receipt and settlement | same test — ledger `owner_id == coordinator-A`, `status == SETTLED`, receipt present, and B *adopts* A's `event_id` |
| 5 | A dies → lease lapses → B's existing recovery path runs | `DeadOwnerRecoveryTests.test_when_the_beats_stop_the_lease_lapses_and_b_recovers` (CLAIMED → lookup proves absence → B runs it) and `..._test_a_dead_owner_that_already_created_the_effect_is_collected_not_rerun` (EFFECTED → collected, `effect_count == 0`). Both **pass on the pre-fix code too** — they are regression guards, not new behaviour |
| 6 | Rotated token / failed renewal → A's later writes refused | `RenewalFailureTests.test_a_token_rotated_mid_flight_fails_the_owner_closed` (a *real* rotation by B) and `..._test_a_renewal_error_fails_closed_before_any_settlement` (renewal raises). Both end in `IDEMPOTENCY_LEASE_LOST` with `settlement is None` |

Supporting tests: period derivation and fallbacks; renewal + clean thread shutdown; failure
not retried; keeper released when the wrapped block raises; daemon thread; the launcher
projecting the loss onto a BLOCKED terminal with **no phase advanced**; and the ledger lock
thread-safety property.

### Pre-fix → post-fix

Pre-fix run of the two headline classes against `a30c1af`'s `executor.py` + `runtime_state.py`
(full log: `artifacts/runs/run_67098fd04388/prefix_regression_run.txt`):

```
test_a_healthy_owner_outlasting_its_lease_keeps_it_and_b_never_takes_over ... FAIL
test_the_lease_keeper_is_load_bearing ... FAIL
test_a_dead_owner_that_already_created_the_effect_is_collected_not_rerun ... ok
test_when_the_beats_stop_the_lease_lapses_and_b_recovers ... ok

AssertionError: no lease renewal landed after the original expiry: the executor never kept
the healthy claim alive (this is the round 3 MAJOR defect)

Ran 4 tests in 71.160s
FAILED (failures=2)
```

The whole new module against pre-fix code: `Ran 13 tests in 191.057s / FAILED (failures=7)`
(the 14th test was added afterwards). Post-fix: `Ran 14 tests in 5.170s / OK`.

---

## 4. Mutation verification

Two mutations are permanent, in-suite tests (so the guards cannot be deleted silently):

* `HealthyLongRunningOwnerTests.test_the_lease_keeper_is_load_bearing` — injects a keeper that
  renews nothing. B's claim then returns `RESUMED`, the token is rotated, and the healthy
  owner's `record_receipt` is refused with `RuntimeStateLeaseHeld`: the defect, exactly.
* `RenewalFailureTests.test_the_fail_closed_checkpoint_is_load_bearing` — keeps a keeper that
  never reports the loss. The superseded owner no longer stops at `IDEMPOTENCY_LEASE_LOST`; it
  attempts the write, is refused by the round 2 fence, and re-enters the observe path.

Manual mutation of the production sources (revert → run → restore), md5-verified:

```
before  MD5 (scripts/deterministic_workflow/executor.py)      = 421458645947b9bd08fc20f2523ba37e
        MD5 (scripts/deterministic_workflow/runtime_state.py) = 965a554ec069347a21768a43219a5a99
git checkout -- executor.py runtime_state.py      # the a30c1af (keeper-free) code
python3 -m unittest scripts.test_deterministic_workflow_lease_keeper  -> FAILED (failures=7)
restore
after   MD5 (scripts/deterministic_workflow/executor.py)      = 421458645947b9bd08fc20f2523ba37e
        MD5 (scripts/deterministic_workflow/runtime_state.py) = 965a554ec069347a21768a43219a5a99
python3 -m unittest scripts.test_deterministic_workflow_lease_keeper  -> OK (14 tests)
```

The thread-safety guard is separately load-bearing: pre-fix,
`test_a_renewal_thread_cannot_slip_inside_another_threads_critical_section` fails with
*“a second thread entered the ledger critical section while another thread held it.”*

---

## 5. Verification evidence

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` (run 1) | `Ran 1960 tests in 342.914s` / `OK (skipped=6)` / exit 0 |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` (run 2) | `Ran 1960 tests in 340.319s` / `OK (skipped=6)` / exit 0 |
| `python3 -m unittest scripts.test_deterministic_workflow_lease_keeper` | `Ran 14 tests in 5.170s` / `OK` |
| focused: ownership + recovery + round2 | `Ran 139 tests in 9.297s` / `OK` |
| `python3 scripts/validate_workflow_graph_docs.py` | `PASSED` |
| `python3 scripts/validate_skills.py` | `PASSED (730 checks)` |
| `python3 scripts/verify_package.py` | `PASSED (239 source files)` |
| dependency-absent lane (this run's copy, incl. the new module) | `Ran 234 tests` / `LANE errors=0 failures=0 skipped=82` |
| dependency-absent lane (`run_8288bf8f1d89`, read-only, unmodified) | `LANE errors=0 failures=0 skipped=82` |
| `python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo` | `COMPLETED / WORKFLOW_COMPLETED / steps=68`, exit 0 |
| `git diff --check` | clean |
| source/installed mirror `diff -r -x '__pycache__'` | no output |

Because the fix introduces a thread, the full suite was run **twice**; both runs are recorded
in `full_suite_run1.txt` / `full_suite_run2.txt` in this run's artifact directory. Test count
moves from the 1946-test baseline to 1960 = 1946 + the 14 new tests, with no change in the
6 skips; both runs are identical, so no flakiness was observed.

---

## 6. Remaining limitations (stated, not hidden)

1. **Renewal cannot interrupt a blocking adapter call.** A lost lease is detected at the next
   beat or at the next checkpoint, not the instant it happens. In that window the round 2
   fence — not the keeper — is what refuses the stale write. That is by design: the executor
   does not kill an in-flight external dispatch.
2. **The default beat waits in real seconds**, while lease arithmetic reads the injected
   `LeaseClockPort`. A run wired to a test clock must inject a waiter (as the tests do);
   in production `SystemLeaseClock` and the beat share the same wall clock.
3. **A stopped process still loses its lease.** `SIGSTOP`, a very long GC pause or a suspended
   host for more than one lease period lapses the claim — deliberately, because that is
   indistinguishable from death, and the takeover ladder exists for exactly that case.
4. **Ledger lock contention could delay a beat.** A renewal waits on the ledger mutex/`flock`
   like any other write (10 s lock timeout by default). With a beat every `lease/3` this
   leaves ample margin at the 60 s default, but a lease configured near the lock timeout would
   not.
5. The keeper renews **one** intent per blocking call, which matches the executor: exactly one
   intent is in flight at a time.

---

## 7. PR description draft (for the Coordinator to apply)

> ### Round 3 MAJOR — a healthy long-running owner now renews its own lease
>
> `claim()` minted a 60-second lease and the executor then blocked inside `adapter.start()`
> for the whole external dispatch — 5–15 minutes for a real Claude/Codex worker — with no
> call to `heartbeat()` anywhere in production code. A healthy Coordinator was therefore
> indistinguishable from a dead one: its lease lapsed, a second Coordinator took over and
> rotated the token, and the round 2 fence refused the healthy owner's own receipt and
> settlement. The fence was right; renewal was missing.
>
> **Fix.** New `deterministic_workflow/lease_keeper.py`. Every executor path that blocks on
> the adapter — `_settle_now` (`start`), `_collect` (`resume`) and the `_recover` ladder
> including `lookup` — now runs inside a `LeaseKeeper` that renews the claim on a daemon
> thread for the whole call:
> * period **derived from the lease** (`lease / 3`, so 20 s on the 60 s default), never
>   hard-coded; period and wait are injectable so tests are deterministic;
> * **fail-closed**: the first failed renewal stops the keeper and is re-raised at the
>   ownership checkpoint taken before *every* write that follows an external call. A
>   Coordinator that lost ownership records no receipt, no settlement, and advances no
>   workflow state — the run stops as BLOCKED with `IDEMPOTENCY_LEASE_LOST`, and does not
>   re-enter the claim path;
> * **cleanup on every exit path** (success, exception, cancellation) via the context manager,
>   on a daemon thread that cannot block process exit;
> * **takeover preserved**: when the owning process dies the beats stop, the lease lapses on
>   schedule, and the existing observe/takeover/recovery ladder runs unchanged.
>
> Because renewal happens on a second thread, `FileRuntimeStateStore._locked()` now guards its
> re-entrancy depth with a `threading.RLock`; previously a second thread could see a non-zero
> depth and skip `flock` entirely.
>
> **Tests.** New `scripts/test_deterministic_workflow_lease_keeper.py` (14 tests) proves all
> six required scenarios, including a concurrency test where A's external call outlasts a
> lease while B claims: B stays an observer, creates no second effect, and adopts A's
> settlement. Two `*_is_load_bearing` mutation tests bring the defect back when the keeper or
> the checkpoint is disabled. The new tests fail on the previous head and pass here; the
> existing stale-owner fencing and cross-process race tests are unchanged and green.
> Docs: `docs/DETERMINISTIC_WORKFLOW.md` gains “Lease renewal during long external work”.

---

## 8. Files touched

```
scripts/deterministic_workflow/lease_keeper.py                              (new)
scripts/deterministic_workflow/executor.py
scripts/deterministic_workflow/runtime_state.py
scripts/test_deterministic_workflow_lease_keeper.py                        (new)
scripts/test_deterministic_workflow_round2.py                              (1 mutation lambda)
orca-worker-reviewer-orchestration/tools/deterministic_workflow/lease_keeper.py    (mirror, new)
orca-worker-reviewer-orchestration/tools/deterministic_workflow/executor.py        (mirror)
orca-worker-reviewer-orchestration/tools/deterministic_workflow/runtime_state.py   (mirror)
docs/DETERMINISTIC_WORKFLOW.md
artifacts/runs/run_67098fd04388/BUGFIX.md                                  (this report)
artifacts/runs/run_67098fd04388/repro_round3_major.py                      (reproduction)
artifacts/runs/run_67098fd04388/dependency_absent_lane.py                  (copy + new module)
artifacts/runs/run_67098fd04388/prefix_regression_run.txt                  (pre-fix evidence)
artifacts/runs/run_67098fd04388/repro_output.txt                           (repro, both modes)
artifacts/runs/run_67098fd04388/full_suite_run1.txt, full_suite_run2.txt   (suite logs)
```

No commit, push, PR update, branch switch, merge or Jira change was performed — those belong
to the Coordinator after the gate. No existing run artifact directory was modified.
