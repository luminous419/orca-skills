# BUGFIX — iteration 3 (Final Adversarial Review remediation)

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The blocking finding named one required correction with an explicit contract (make the lease token mandatory on every effect-writing transition and propagate it through the nine production call sites); the fix and its evidence follow directly from the existing ownership contract and needed no user-authority choice.",
  "scope": "This phase's own conduct at this iteration."
}
```

Scope of this iteration: the single CRITICAL from `FINAL_ADVERSARIAL_REVIEW.md` (F-01,
stale owner bypasses fencing) plus the one non-blocking note the reviewer raised
(`OrcaAdapter.lookup()` substring matching). M2-001/002/003/004 and the already-passing
parts of C2-001 were not touched.

---

## 1. Reproduction before the fix

The lease was enforced only on `heartbeat`. `record_receipt()` and `settle()` took
`lease_token: str | None = None` and checked ownership only under
`if lease_token is not None`, and no production caller ever supplied one — so the fence was
dead code. Reproduced with the real file store and an injected clock (pre-fix source):

```text
A_TOKEN 1495139d268c5bbb3a9e3825e9f5804d
B_TOKEN 0e83cecdef36cddf2e3ec4c8283359bc
A_HEARTBEAT_AFTER_TAKEOVER RuntimeStateLeaseHeld LEASE_LOST
A_RECEIPT_AFTER_TAKEOVER ACCEPTED B {'external_id': 'effect-created-by-stale-A'}
LEDGER_OWNER_AND_RECEIPT B {'external_id': 'effect-created-by-stale-A'}
```

The ledger record owned by B named A's external effect: one stable intent, two external
effects, arriving through the recovery path rather than the race path.

The same scenario on the fixed source:

```text
A_TOKEN 8cfefeb7178c465b75dd1171d2a5a74e
B_TOKEN e755e4e97f6edf023e5e36094f8f3bcb
A_HEARTBEAT         REFUSED RuntimeStateLeaseHeld     LEASE_LOST
A_RECEIPT           REFUSED RuntimeStateLeaseHeld     LEASE_LOST
A_RECEIPT_NOTOKEN   REFUSED RuntimeStateLeaseRequired LEASE_REQUIRED
A_SETTLE            REFUSED RuntimeStateLeaseHeld     LEASE_LOST
LEDGER B None
```

---

## 2. Changes

### 2.1 The fence itself — `deterministic_workflow/runtime_state.py`

| Location | Change |
|---|---|
| `runtime_state.py:137` | new `RuntimeStateLeaseRequired(RuntimeStateLeaseHeld)` — a missing token is a missing capability, and it is a *subclass* so every caller that already fails closed on a lost lease also fails closed on an absent one |
| `runtime_state.py:508` | new `_RuntimeStateStore._fenced()` — the single ownership check: record must exist, token must be a non-empty `str`, and both `lease_token` and `owner_id` must match the caller. There is deliberately **no** "token not supplied" branch |
| `runtime_state.py:531` | `record_receipt(intent_id, receipt, lease_token)` — `lease_token` is now a **required positional** argument and goes through `_fenced` |
| `runtime_state.py:544` | `settle(intent_id, event, lease_token)` — same |
| `runtime_state.py:18-39` | module docstring documents the fence and the exact takeover-versus-returning-owner window it closes |

`heartbeat` already checked token *and* owner and is unchanged. `release` only shortens the
caller's own lease and returns silently on a token mismatch, so it cannot advance effect
state; it was left as is.

Expiry is deliberately **not** part of the fence: takeover rotates the token
(`_new_lease`), so a still-uncontested owner whose lease merely lapsed keeps a valid token
and can finish, while a superseded one cannot. That is standard fencing-token semantics and
avoids failing a slow-but-sole executor.

### 2.2 The nine production call sites — token propagation

All nine call sites named in the review now carry the token minted by `claim()`:

| Review's call site | Now |
|---|---|
| `fake_adapter.py:114` `record_receipt` | `fake_adapter.py:121-122` — `record_receipt(..., lease_token)` |
| `fake_adapter.py:115` `settle` | `fake_adapter.py:123` — `settle(..., lease_token)` |
| `executor.py:98` `settle` | `executor.py:102` (`_settle_now`) — `settle(..., lease_token)` |
| `executor.py:125` `settle` | `executor.py:129` (`_collect`) — `settle(..., lease_token)` |
| `executor.py:144` `settle` | `executor.py:148` (`_recover`) — `settle(..., lease_token)` |
| `executor.py:163` `record_receipt` | `executor.py:168` (`_recover`) — `record_receipt(..., lease_token)` |
| `orca_adapter.py:96` `_record_receipt` | `orca_adapter.py:124` — `_record_receipt(intent, {...}, lease_token)` |
| `orca_adapter.py:112` `_record_receipt` | `orca_adapter.py:140-141` — `_record_receipt(intent, {...}, lease_token)` |
| `orca_adapter.py:114` `settle` | `orca_adapter.py:143` — `settle(..., lease_token)` |

Supporting signature changes:

- `executor.py:185` — `_execute_recoverable` reads `lease_token = record["lease_token"]`
  straight off the `claim()` result; it is the only place a token is obtained.
- `executor.py:95 / 114 / 134` — `_settle_now`, `_collect`, `_recover` all take
  `lease_token` and thread it down (`executor.py:152, 167, 169, 194, 195`).
- `executor.py:99` — `adapter.start(intent, lease_token=lease_token)`.
- `fake_adapter.py:96` and `orca_adapter.py:108` — `start(self, intent, *, lease_token=None)`.
- `orca_adapter.py:147` — `_record_receipt(self, intent, receipt, lease_token)`.
- `ports.py:20` — `AgentExecutionPort.start(intent, *, lease_token: str | None = None)`.
- `ports.py:85, 87` — `RuntimeStatePort.record_receipt` / `settle` require `lease_token`.
- `ports.py:69-73` — the fence is documented in the port contract.

`start()` keeps `lease_token` keyword-optional at the *port* boundary so an adapter used
without a ledger still works; with a ledger wired in, omitting it is refused by the store
(`RuntimeStateLeaseRequired`), i.e. it fails closed rather than skipping the check.

Runtime-neutrality is preserved: the token is an opaque `str`, no Orca/terminal/credential
dependency was added to the core, and no new module import crosses the OS-40 boundary.

### 2.3 Non-blocking note: `OrcaAdapter.lookup()` matching

`orca_adapter.py:62` previously matched `json.dumps(intent_id) in task["spec"]` — a quoted
substring search over the raw spec. It now parses each spec and compares the **top-level
`intent_id`**:

- `orca_adapter.py:73-91` — new `_spec_intent_id(spec)` helper: returns the top-level
  `intent_id` of a JSON object spec, else `None` (non-string, non-JSON and non-object specs
  belong to no intent).
- `orca_adapter.py:68` — the loop compares parsed identity.

Behaviour for the "listing unreadable / spec-less task" paths is unchanged: they still raise
`ExternalLookupUnavailable` rather than reporting absence.

### 2.4 Documentation

`docs/DETERMINISTIC_WORKFLOW.md` — the "Ownership, leases, and recovery" section now states
that the lease token is a fence on `record_receipt` / `settle` / `heartbeat`, describes the
window it closes, and records that the executor propagates it into
`AgentExecutionPort.start`. The `external_lookup` known-limitation entry now records that
matching is by parsed `intent_id`. The stale-writer window is **not** listed as a known
limitation — it is fixed.

---

## 3. Regression tests

New — `scripts/test_deterministic_workflow_ownership.py`:

`LeaseFencingTests` (store level, `ManualLeaseClock`, no sleeps):

- `test_a_superseded_owner_cannot_record_a_receipt`
- `test_a_superseded_owner_cannot_settle`
- `test_the_full_takeover_sequence_refuses_every_stale_write` — the reported reproduction
  verbatim: A claim → lease expiry → B takeover → A's heartbeat, receipt **and** settle all
  refused, and B's record still has `receipt=None, settlement=None`
- `test_a_missing_token_is_refused_rather_than_skipping_the_check` — `None` and `""` both
  raise `RuntimeStateLeaseRequired`, for both transitions
- `test_the_current_owner_writes_normally` — positive control
- `test_the_effect_write_fence_is_load_bearing` — in-test mutation: restore the old optional
  check and the stale write lands again

`LeaseFencingInProductionCallersTests` (executor/adapter level — a guard nobody invokes is
dead code, which is how the optional argument passed review):

- `test_the_executor_hands_the_claim_token_to_the_adapter` — asserts the token reaching
  `adapter.start` is exactly the one persisted by `claim()`
- `test_a_slow_orca_start_cannot_land_its_task_after_takeover` — `OrcaAdapter` over a harness
  whose `create_task` advances the injected clock past A's lease and lets B claim; A returns
  and is refused with `LEASE_LOST ... owner=B` (asserted **not** to be
  `RuntimeStateLeaseRequired`, so a build that stopped propagating the token cannot pass this
  test for the wrong reason), and B's record still has `receipt=None`

New — `scripts/test_deterministic_workflow_round2.py`:

- `test_the_orca_lookup_matches_the_parsed_intent_id_not_a_substring` — a foreign spec that
  quotes our `intent_id`, a non-JSON spec and a JSON scalar spec all fail to match

Updated existing call sites (mechanical, token now required):
`test_deterministic_workflow_ownership.py` (`test_the_committed_ledger_is_always_readable_after_a_write`,
`test_a_runtime_handle_never_reaches_the_ledger`),
`test_deterministic_workflow_round2.py` (`test_effected_without_resume_capability_fails_closed`,
`test_effected_with_resume_collects_the_existing_settlement`,
`test_the_recovery_ladder_is_load_bearing` patch lambda arity),
`test_deterministic_workflow_recovery.py` (`test_persisted_records_hold_no_forbidden_handles`,
`DyingAdapter.start` signature).

Focused run:

```text
python3 -m unittest scripts.test_deterministic_workflow_ownership.LeaseFencingTests \
  scripts.test_deterministic_workflow_ownership.LeaseFencingInProductionCallersTests \
  scripts.test_deterministic_workflow_round2.CrashRecoveryLadderTests.test_the_orca_lookup_matches_the_parsed_intent_id_not_a_substring

Ran 9 tests in 0.013s

OK
```

---

## 4. Mutation verification

Three independent mutations were applied to the *source* (not to a patched copy), the
targeted tests re-run, then the files restored and proven byte-identical by md5.

Baseline md5 before mutating:

```text
06c78d2ff53a79696cb59f47f619544f  scripts/deterministic_workflow/runtime_state.py
3d2aed7b2a35505a59fcbcde6c90cced  scripts/deterministic_workflow/orca_adapter.py
3a37242e77a0e69df5fec3721c1ee58a  scripts/deterministic_workflow/executor.py
```

**M1 — revert the fence to the optional check** (`_fenced` back to
`if lease_token is not None and ...`, and both writers back to `lease_token=None` defaults),
plus **M2 — revert `lookup` to the quoted-substring search**:

```text
FAIL: test_a_missing_token_is_refused_rather_than_skipping_the_check (transition='record_receipt')
FAIL: test_a_missing_token_is_refused_rather_than_skipping_the_check (transition='settle')
ERROR: test_a_missing_token_is_refused_rather_than_skipping_the_check (transition='empty token')
FAIL: test_the_orca_lookup_matches_the_parsed_intent_id_not_a_substring
Ran 115 tests in 7.617s
FAILED (failures=3, errors=1)
```

**M3 — keep the store strict but remove propagation** (`adapter.start(intent)` and
`OrcaAdapter._record_receipt` passing `None`), i.e. exactly the reviewed defect:

```text
FAIL: test_the_executor_hands_the_claim_token_to_the_adapter
FAILED (failures=1)
```

**M4 — remove only `OrcaAdapter`'s propagation**:

```text
FAIL: test_a_slow_orca_start_cannot_land_its_task_after_takeover
Ran 2 tests in 0.007s
FAILED (failures=1)
```

Restored and re-hashed — identical to the baseline above:

```text
MD5 (scripts/deterministic_workflow/runtime_state.py) = 06c78d2ff53a79696cb59f47f619544f
MD5 (scripts/deterministic_workflow/orca_adapter.py)  = 3d2aed7b2a35505a59fcbcde6c90cced
MD5 (scripts/deterministic_workflow/executor.py)      = 3a37242e77a0e69df5fec3721c1ee58a
```

---

## 5. Full verification

| Gate | Result |
|---|---|
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1946 tests in 334.558s` / `OK (skipped=6)` (baseline 1937 → +9 new) |
| `python3 scripts/validate_workflow_graph_docs.py` | `Workflow graph documentation validation PASSED` |
| `python3 scripts/validate_skills.py` | `Skill validation PASSED (729 checks)` |
| `python3 scripts/verify_package.py` | `Package verification PASSED (236 source files)` |
| `python3 artifacts/runs/run_8288bf8f1d89/dependency_absent_lane.py` | `OK (skipped=82)` / `LANE errors=0 failures=0 skipped=82` (221 tests) |
| `python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo` | `terminal_status=COMPLETED reason=WORKFLOW_COMPLETED ... steps=68`, exit 0 |
| `git diff --check` | clean |
| `diff -r -x '__pycache__' scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow` | no output (mirror byte-identical) |

No commit, push, PR update, branch switch, merge or Jira change was performed; no previous
run's artifacts were modified.

---

## 6. Remaining limitations

Unchanged from iteration 2, and none of them is the stale-writer window (that is fixed, not
documented away):

- POSIX-only exclusive claim (`fcntl.flock`); the file store refuses to construct elsewhere.
- `OrcaAdapter` still declares `external_lookup` but not `external_resume`: `worker_done` is
  delivered once to the owning process's stream, so an already-dispatched Orca effect is
  reconciled as `IDEMPOTENCY_RECOVERY_UNSUPPORTED` → BLOCKED. Closing that window is OS-37.
- The residual create-then-crash window (durable identifier only exists after `create_task`
  returns) is made safe by the lookup rung, not eliminated — Orca exposes no caller-supplied
  idempotency key.
- `InMemoryRuntimeStateStore` offers no inter-process exclusion; single-process tests only.
- The runtime-state schema is `os40.runtime_state.v2`; a `v1` ledger is refused, not ignored.

New note on the fence's boundary, stated for completeness rather than as an open defect: the
fence is checked at the moment of the ledger write, inside the same lock that commits it, so
there is no window between the ownership check and the write. It does **not** and cannot stop
a superseded owner from having *already created* an external effect before its lease lapsed —
that effect is exactly what the recovery ladder's lookup rung is for, and the ledger now
refuses to be repointed at it by the process that lost ownership.
