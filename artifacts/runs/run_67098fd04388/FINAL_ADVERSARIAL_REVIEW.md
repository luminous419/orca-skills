RESULT: FAIL
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The adversarial review found a deterministic implementation defect within the already-authorized heartbeat fail-closed requirement; fixing and retesting it requires no user-owned product or scope decision.",
  "scope": "This phase's own conduct at this iteration."
}
```

UNIT_TEST_STATUS: PASS

# Final adversarial review

The existing 22 lease-keeper tests pass, but a new deterministic interleaving demonstrates a
blocking G1 defect. A heartbeat failure can occur after the executor's final
`keeper.raise_if_lost()` checkpoint and before the corresponding ledger write. If that failure is
not itself an ownership rotation (for example a lock timeout or transient unreadable ledger), the
lease token remains valid, so the write succeeds and the node returns success despite the keeper
having recorded renewal failure.

## Newly attempted attacks and actual results

### 1. Heartbeat failure in the checkpoint-to-write gap — FAIL (blocking)

I added a reviewer-only deterministic reproducer at
`artifacts/runs/run_67098fd04388/final_reviewer/repro_checkpoint_write_race.py`. It uses Events,
not elapsed-time sleeps: the runtime-state wrapper parks `settle()` only after the executor has
passed its final `_still_owned()` call; the keeper is then instructed to beat and that heartbeat
raises `RuntimeStateLockTimeout`; only after the keeper has stored the failure is the already-valid
settlement token allowed through.

Command and complete output:

```text
$ PYTHONPATH=. python3 artifacts/runs/run_67098fd04388/final_reviewer/repro_checkpoint_write_race.py
thread_alive=False
executor_error=None
executor_returned=True
ledger_status=SETTLED
settlement_written=True
effect_count=1
```

The failure is not merely a stale-token race already covered by F-01. The injected renewal error
does not rotate the token, so `_RuntimeStateStore._fenced()` correctly accepts it. The executor has
no ownership/failure check atomically coupled to the write and no check after the write; moreover,
`LeaseKeeper.__exit__()` raises only for orphaned cleanup, not for `keeper.failure`. Consequently
the node advances workflow state to `SETTLED` after a heartbeat failure. This directly violates
the explicit requirements that heartbeat failure be fail-closed and that the affected Coordinator
must not record settlement or advance workflow state.

### 2. Exception-family catch interaction — no separate blocker found

```text
$ rg -n "except .*LeaseRenewalFailed|except .*RuntimeStateConflict" scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow
scripts/deterministic_workflow/executor.py:230:    except LeaseRenewalFailed as exc:
scripts/deterministic_workflow/launcher.py:127:    except RuntimeStateConflict as exc:
orca-worker-reviewer-orchestration/tools/deterministic_workflow/executor.py:230:    except LeaseRenewalFailed as exc:
orca-worker-reviewer-orchestration/tools/deterministic_workflow/launcher.py:127:    except RuntimeStateConflict as exc:
```

`LeaseKeeperNotStopped(LeaseRenewalFailed)` is intentionally mapped by the executor to
`IDEMPOTENCY_LEASE_LOST`; no additional broad catch was found that silently resumes execution.
The hierarchy itself did not produce a second defect in the inspected paths.

### 3. Runtime-neutral/checkpoint leakage boundary — no blocker found by static inspection

Keeper/thread objects stay in executor-local closures and context-manager locals. They are not
inserted into workflow state or logical trace, and no new OS-31 checkpointer or OS-37 CLI-adapter
implementation was introduced. `lease_token` remains in the separate runtime ledger rather than
LangGraph checkpoint state. The existing dependency-absent result is consistent with imports
remaining standard-library-only, although I did not repeat that previously completed lane.

### 4. Report honesty — blocking inconsistency with the reproduction

`docs/DETERMINISTIC_WORKFLOW.md` says the fence prevents a stale write in the interval between a
renewal failure and the next checkpoint. That is true only when ownership/token was rotated. The
reproducer shows it is false for a renewal failure such as lock timeout: the token remains valid,
the settlement lands, and the executor reports success. The documented limitation therefore
understates the fail-closed hole.

## Blocking finding

### F-ADV-01 — G1: renewal failure after the last checkpoint permits settlement and workflow advancement

Affected write sites include the checkpoint/write pairs in `_settle_now()`, `_collect()`, and
`_recover()`. The deterministic command and output above are the reproduction. A compliant fix
must close the race rather than merely add another non-atomic pre-check; the keeper failure state
and ownership-sensitive write need a synchronization/transaction boundary, or context exit must
otherwise prevent reporting/advancing success and roll back or refuse the write. The regression
test should force the exact Event-controlled interleaving and assert no receipt/settlement and no
successful node result.

## Focused regression status

The pre-existing focused file remains green, confirming this is an uncovered lifecycle gap rather
than a general test failure:

```text
$ python3 -m unittest scripts.test_deterministic_workflow_lease_keeper
......................
----------------------------------------------------------------------
Ran 22 tests in 0.793s

OK
```

Output: `artifacts/runs/run_67098fd04388/final_reviewer/lease_keeper_tests.txt`.

## Non-blocking notes

- Multiple simultaneous intents and multiple keeper threads were inspected conceptually but not
  exercised with a new stress harness after the deterministic blocker was established. Per-intent
  tokens and the store mutex suggest serialization rather than cross-intent corruption; this is
  not evidence of a second G1-G5 finding.
- A keeper blocked behind the same store instance's `RLock` cannot deadlock with a finite main
  critical section: it waits outside the depth-sensitive body and proceeds when the main thread
  releases the mutex. External flock contention can still cause the documented timeout path.
- An already-issued in-flight heartbeat after revocation may extend the abandoned lease once.
  This delays takeover by at most one lease interval and was disclosed; I found no evidence that
  it writes receipt, settlement, or workflow state.

## Areas not independently verified

- I did not repeat the full 1968-test suite, dependency-absent lane, package validators, demo, or
  the already-reviewed wedged-heartbeat and stale-owner scenarios; the coordinator explicitly
  directed this review not to repeat them.
- I did not mutate production code. Therefore no mutation restoration digest is applicable.
- I did not run a new real multi-process ledger-corruption stress test or a many-intent throughput
  test. The deterministic F-ADV-01 reproduction is sufficient to make the final verdict FAIL.

