# Final Adversarial Review — attempt 2

RESULT: PASS

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The attempt-1 stale-owner defect is closed, the iteration-3 interactions inspected here fail closed, and no remaining issue requires a user-authority decision.",
  "scope": "This phase's own conduct at this iteration."
}
```

UNIT_TEST_STATUS: PASS

## Verdict

The attempt-1 CRITICAL is closed. After B takes over an expired lease, A's stale token can no
longer heartbeat, record a receipt, or settle; omitting the token is independently rejected, and
the durable record remains owned by B without A's receipt or settlement. I found no new blocking
interaction introduced by iteration 3.

`RuntimeStateLeaseRequired` subclasses `RuntimeStateLeaseHeld`, which preserves the existing
fail-closed exception boundary. The executor's catch can turn a missing-token failure into a
bounded observer/recovery attempt, but it cannot authorize an effect write: the live lease remains
held and the observation terminates at its configured timeout. Production obtains the token only
from `claim()`, passes it as an opaque keyword capability, and does not add it to workflow state,
trace entries, settlement events, or exception text. The only durable token is the ownership field
required by the runtime-state contract; recursive checkpoint-safety validation still excludes
terminal/process/session/credential keys.

The `AgentExecutionPort.start(..., lease_token=...)` addition remains runtime-neutral. It imports no
Orca, terminal, checkpointer, or credential type; `FakeAdapter` exercises the same contract without
Orca. The optional port argument supports adapters used without a ledger, while either production
adapter connected to a ledger reaches the mandatory store fence.

## Newly attempted attacks and actual results

### 1. Original stale-owner reproduction, rerun unchanged in substance

Command: a real `FileRuntimeStateStore` ledger, injected `ManualLeaseClock`, A claim, 31-second
logical advance, B takeover, then A heartbeat/receipt/no-token receipt/settlement attempts.

Actual output (tokens omitted here; they varied normally):

```text
A_HEARTBEAT_AFTER_TAKEOVER REFUSED RuntimeStateLeaseHeld LEASE_LOST
A_RECEIPT_AFTER_TAKEOVER REFUSED RuntimeStateLeaseHeld LEASE_LOST
A_RECEIPT_NO_TOKEN REFUSED RuntimeStateLeaseRequired LEASE_REQUIRED
A_SETTLE_AFTER_TAKEOVER REFUSED RuntimeStateLeaseHeld LEASE_LOST
LEDGER_OWNER_RECEIPT_SETTLEMENT B None None
```

This directly reverses attempt 1's `A_RECEIPT_AFTER_TAKEOVER ACCEPTED` result.

### 2. Iteration-3 focused regression and checkpoint-safety interaction

Command:

```text
python3 -m unittest \
  scripts.test_deterministic_workflow_ownership.LeaseFencingTests \
  scripts.test_deterministic_workflow_ownership.LeaseFencingInProductionCallersTests \
  scripts.test_deterministic_workflow_round2.CrashRecoveryLadderTests.test_the_orca_lookup_matches_the_parsed_intent_id_not_a_substring \
  scripts.test_deterministic_workflow_recovery.RuntimeStateStoreTests.test_persisted_records_hold_no_forbidden_handles
```

Actual output:

```text
Ran 10 tests in 0.014s

OK
```

These cover the exact stale-writer sequence, missing/empty token rejection, current-owner positive
control, executor and OrcaAdapter token propagation, parsed lookup matching, and the recursive
forbidden-handle policy together. An initial invocation used a nonexistent unittest class name and
reported one loader error; the corrected fully-qualified test above passed and no source was
changed.

### 3. Live Orca `task-list` through the production adapter lookup path

I supplied `OrcaAdapter` a minimal live harness whose `call()` invokes the installed Orca CLI, and
looked up a deliberately absent stable identity in this production run. This was read-only.

Command shape:

```text
OrcaAdapter(LiveHarness(run_id="run_8288bf8f1d89")).lookup(
    {"intent_id": "intent_does_not_exist_live"}
)
```

Actual output:

```text
LIVE_LOOKUP_ABSENCE None
LIVE_TASKS 10
ALL_HAVE_ID_SPEC True
CANONICAL_JSON_SPECS 0
```

Thus the production CLI accepts the documented run-scoped listing call, returns the shape the
adapter consumes, and the adapter reaches its proven-absence result on a successful listing. The
current run's ten tasks were created by the existing supervised orchestration path and their specs
are prompt text, not the canonical JSON emitted by `OrcaAdapter.start`; they therefore cannot serve
as a live positive-match fixture for this adapter.

### 4. Source interaction inspection

I traced every path from `claim()` through `_settle_now`, `_recover`, `_collect`, `OrcaAdapter`, and
`FakeAdapter`. No token is re-minted or recovered from untrusted checkpoint state. Both effect
writes call the same `_fenced()` primitive, which rejects non-string/empty tokens before comparing
both persisted token and store owner. A takeover rotates the token, so the returning predecessor
cannot regain authority merely because its local store still has owner ID A.

A repository/artifact search found no generated run ledger or workflow trace containing a minted
lease token. Matches outside source/tests were documentation, the fixed attempt-1 finding, and a
literal test fixture token. This is consistent with the design: runtime-state ledgers contain the
ownership capability, while checkpoint state and orchestration logs do not.

## Blocking findings

None.

## Non-blocking notes

- Because `RuntimeStateLeaseRequired` is a `RuntimeStateLeaseHeld`, an adapter integration bug that
  drops a token can spend the finite observation timeout before surfacing as recovery failure. It
  remains fail-closed and bounded, so this is diagnostic latency rather than a correctness defect.
- The current production run demonstrates the negative live lookup path and response schema, not a
  positive recovery of a task created by `OrcaAdapter.start`. Creating such a task solely for this
  review would be an external state mutation and was not performed.

## Areas not independently verified

- I did not repeat the coordinator's full suite, dependency-absent, package/archive, skill,
  graph-doc, demo, mirror, diff, eight-process race, malformed-ledger, or tamper-matrix gates.
- A live positive `OrcaAdapter.lookup` match and subsequent production task recovery remain
  unexecuted because this run contains no adapter-canonical JSON task spec and a new external Task
  was not created. The residual risk is integration-specific: a future Orca response could preserve
  the observed fields yet transform a newly created canonical spec. Source tests cover exact
  round-trip matching and the live negative path confirms today's listing contract.

