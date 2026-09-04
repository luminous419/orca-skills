# Final Adversarial Review — attempt 1

RESULT: FAIL

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The final adversarial gate is an evidence-based code verdict, and the reproduced stale-owner write violates the explicit ownership contract without requiring any user-authority choice.",
  "scope": "This phase's own conduct at this iteration."
}
```

UNIT_TEST_STATUS: PASS

## Newly attempted attacks and actual results

### 1. Lease takeover followed by the original owner returning

I used two store instances with distinct explicit owners and one injected clock. A claimed the
intent, its lease expired, B took ownership, and then A attempted both a heartbeat and a receipt
write. The important control is that A's old token is demonstrably stale: heartbeat rejects it.
The effect write nevertheless succeeds because the token is optional.

Command:

```text
python3 - <<'PY'
import tempfile
from pathlib import Path
from scripts.test_deterministic_workflow_ownership import _intent
from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore, ManualLeaseClock

with tempfile.TemporaryDirectory() as d:
    path = Path(d) / 'ledger.json'
    clock = ManualLeaseClock(1000)
    a = FileRuntimeStateStore(path, clock=clock, owner_id='A', lease_seconds=30)
    b = FileRuntimeStateStore(path, clock=clock, owner_id='B', lease_seconds=30)
    intent = _intent()
    claimed_a = a.claim(intent)
    clock.advance(31)
    claimed_b = b.claim(intent)
    print('A_TOKEN', claimed_a['lease_token'])
    print('B_TOKEN', claimed_b['lease_token'])
    try:
        a.heartbeat(intent['intent_id'], claimed_a['lease_token'])
    except Exception as exc:
        print('A_HEARTBEAT_AFTER_TAKEOVER', type(exc).__name__, str(exc).split(':')[0])
    stale_write = a.record_receipt(intent['intent_id'],
                                   {'external_id': 'effect-created-by-stale-A'})
    print('A_RECEIPT_AFTER_TAKEOVER', 'ACCEPTED', stale_write['owner_id'],
          stale_write['receipt'])
    current = b.get_receipt(intent['intent_id'])
    print('LEDGER_OWNER_AND_RECEIPT', current['owner_id'], current['receipt'])
PY
```

Actual output (tokens vary per run):

```text
A_TOKEN 1495139d268c5bbb3a9e3825e9f5804d
B_TOKEN 0e83cecdef36cddf2e3ec4c8283359bc
A_HEARTBEAT_AFTER_TAKEOVER RuntimeStateLeaseHeld LEASE_LOST
A_RECEIPT_AFTER_TAKEOVER ACCEPTED B {'external_id': 'effect-created-by-stale-A'}
LEDGER_OWNER_AND_RECEIPT B {'external_id': 'effect-created-by-stale-A'}
```

### 2. Focused regression suites

Command:

```text
python3 -m unittest scripts.test_deterministic_workflow_ownership \
  scripts.test_deterministic_workflow_round2 \
  scripts.test_deterministic_workflow_recovery
```

Actual output:

```text
Ran 130 tests in 7.650s

OK
```

This green result is useful negative evidence: the required interaction above is absent from the
current suite even though its individual claim/takeover and stale-heartbeat guards are covered.

### 3. Runtime-neutral boundary and documentation inspection

I inspected imports and persistence shapes in the core modules. Runtime state stores durable
identifiers and excludes terminal handles; the Orca-specific harness remains in `orca_adapter.py`,
and `FakeAdapter`/`FileExternalWorld` still provide an Orca-independent reference path. The v2
incompatibility and deliberate refusal of v1 ledgers are explicitly documented. I found no new
blocking OS-37/OS-31 boundary violation in those areas.

## Blocking finding

### F-01 (CRITICAL, G1/G2): takeover does not fence the previous executor's effect writes

The lease is enforced only for `heartbeat`. `record_receipt()` and `settle()` accept
`lease_token=None` and check ownership only when a token happens to be supplied
(`runtime_state.py:487-520`). Every production call omits it: executor settlement and recovery
writes (`executor.py:94-98, 109-125, 141-164`), both OrcaAdapter receipt writes and settlement
(`orca_adapter.py:93-119`), and FakeAdapter's receipt/settlement writes
(`fake_adapter.py:96-115`). `_execute_recoverable()` receives the claimed record and token but
never propagates that ownership capability (`executor.py:167-186`).

Consequently, after B takes over an expired lease, A can return from a slow external call and
mutate the ledger under B's still-current owner ID. The reproduction proves the stale A token is
invalid and then proves the same stale owner can write a receipt anyway. A realistic consequence
is the explicit adversarial window from the task: A blocks in `create_task`; its lease expires; B
takes over and lookup proves absence or starts recovery; A later returns and records its external
Task. The lease has not made B the sole executor, and receipts can be overwritten with a different
external identity. This violates “A만 executor”, takeover safety, and the ownership contract.

Required correction: make the current lease token mandatory for every ownership-sensitive
transition (`record_receipt`, `settle`, and any release/transition that changes effect state),
propagate the token returned by `claim()` through executor and adapter operations, and reject both
owner and token mismatch. Add a regression that pauses A after external creation, expires/takes
over with B, resumes A, and proves A cannot record or settle; the test should also show the guard
is mutation-sensitive.

## Non-blocking notes

- `OrcaAdapter.lookup()` implements lookup, rather than merely declaring it, via task-list and a
  stable intent marker in canonical specs. Its matching is a quoted substring search instead of
  parsing the JSON and comparing the top-level `intent_id`; this can conservatively produce a
  false positive for an unrelated spec containing that string. In the inspected paths this fails
  toward reuse/blocking rather than duplicate creation, so I do not elevate it above a note here.
- The documented known limitations accurately disclose POSIX-only locking, missing Orca external
  resume, the create-return crash window, in-memory-store limits, and v1 refusal. The newly found
  stale-writer window is not disclosed and should be fixed, not normalized as a limitation.

## Areas not independently verified

- I did not repeat the coordinator's full 1937-test, dependency-absent, package/archive, skill,
  graph-doc, demo, mirror, or diff-check gates.
- I did not rerun the already independently verified eight-process initial-claim race or malformed
  ledger/tamper matrices.
- I did not perform a live Orca task-list recovery against a production run; the review of that
  lookup path was source-level. The blocking stale-owner reproduction is runtime-neutral and uses
  the actual file-backed ownership implementation.

