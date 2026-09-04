# BUGFIX — iteration 2 (correction round): C2-001 durable ledger record integrity

run_id: run_8288bf8f1d89 · branch: feat/os-40-langgraph-engine (head 4d30217, not switched)
worker: claude-opus · scope: **C2-001 only**. M2-001..M2-004 already PASS and were not touched.

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The blocking finding named the six reproduction cases, the two missing validation layers and the required guards exactly; the receipt key set was read off the adapters that actually write it (OrcaAdapter task_id/dispatch_id, FakeAdapter external_id, the lookup path intent_id) rather than guessed, and the settlement key set is the existing canonical SettlementEvent vocabulary. No option required user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```

---

## 1. What was wrong

Two layers were missing, and neither implies the other:

1. **`validate_record()` did not close the record's *contents*.** A receipt only had to be a
   `dict`; a settlement only had to be a `dict` whose `intent_id`/`command_id` matched. So an
   empty receipt, an unknown receipt key, a truncated settlement or a padded settlement all
   read back as a healthy record.
2. **`claim()` did not compare the *stored identity* with the intent asking for it.** It
   compared `payload_digest` alone (`runtime_state.py:344-345` pre-fix), leaving `run_id`,
   `phase`, `role`, `round_kind` and `command_id` forgeable — a forged record hands an intent
   another intent's external effect.

`validate_record` cannot do the second job: it never sees the intent. `claim` alone cannot do
the first: it validates nothing about content. Both layers were implemented.

`empty receipt {}` is the worst of the six: `EFFECTED` asserts "the external effect exists",
so a receipt naming no durable external identifier is the exact strand M2-003's review
described — "crash after `create_task` leaves no durable external identifier to reconcile".
It is now **corrupt on read**, not resumable.

## 2. Reproduction — before the fix

Reproduction harness (committed as durable evidence):
`artifacts/runs/run_8288bf8f1d89/ledger_integrity_repro.py`. It writes a *valid* v2 ledger
holding exactly **one** record, tampers with that single record, and calls
`FileRuntimeStateStore.claim()` for the matching intent.

```
$ python3 artifacts/runs/run_8288bf8f1d89/ledger_integrity_repro.py     # pre-fix
empty receipt {}           -> ACCEPTED  outcome=RESUMED
unknown receipt key        -> ACCEPTED  outcome=RESUMED
conflicting run_id         -> ACCEPTED  outcome=RESUMED
conflicting phase          -> ACCEPTED  outcome=RESUMED
conflicting role           -> ACCEPTED  outcome=RESUMED
conflicting command_id     -> ACCEPTED  outcome=RESUMED
exit=1
```

All six reproduce exactly as the Coordinator reported, including the three
(`phase`, `role`, `command_id`) that were not in the Reviewer report.

## 3. Reproduction — after the fix

```
$ python3 artifacts/runs/run_8288bf8f1d89/ledger_integrity_repro.py     # post-fix
empty receipt {}           -> REFUSED   RuntimeStateCorrupt: MALFORMED_RUNTIME_STATE:intent_d01a806e80c237a2f6dd96ad:receipt external identity missing (one of ['task_id', 'external_id'] is required once the effect exists)
unknown receipt key        -> REFUSED   RuntimeStateCorrupt: MALFORMED_RUNTIME_STATE:intent_d01a806e80c237a2f6dd96ad:receipt unknown keys ['smuggled']
conflicting run_id         -> REFUSED   RuntimeStateConflict: IDEMPOTENCY_CONFLICT:intent_d01a806e80c237a2f6dd96ad:['run_id']
conflicting phase          -> REFUSED   RuntimeStateConflict: IDEMPOTENCY_CONFLICT:intent_d01a806e80c237a2f6dd96ad:['phase']
conflicting role           -> REFUSED   RuntimeStateConflict: IDEMPOTENCY_CONFLICT:intent_d01a806e80c237a2f6dd96ad:['role']
conflicting command_id     -> REFUSED   RuntimeStateConflict: IDEMPOTENCY_CONFLICT:intent_d01a806e80c237a2f6dd96ad:['command_id']
exit=0
```

## 4. The change

All production edits are in **one file**, mirrored byte-identically.

### 4.1 Closed receipt contract — `scripts/deterministic_workflow/runtime_state.py:73-80, 216-237, 292-293`

The key set was **read off the adapters that actually write receipts**, not invented:

| writer | receipt written |
|---|---|
| `orca_adapter.py:103` (right after `create_task`) | `{"task_id"}` |
| `orca_adapter.py:117` (after the dispatch) | `{"task_id", "dispatch_id"}` |
| `orca_adapter.py:63` `lookup()` → `executor.py:163` | `{"task_id", "intent_id"}` |
| `fake_adapter.py:114` | `{"external_id"}` |
| `fake_adapter.py:56` `FileExternalWorld.find()` → `executor.py:163` | `{"external_id", "intent_id"}` |

```python
RECEIPT_KEYS = frozenset({"task_id", "dispatch_id", "external_id", "intent_id"})
RECEIPT_IDENTITY_KEYS = ("task_id", "external_id")
```

`_validate_receipt()` (`runtime_state.py:216`) enforces, on every ledger read:
unknown keys refused; every value an exact non-empty `str`; a `receipt["intent_id"]` that
disagrees with the record key refused; and — for `EFFECTED` **and** `SETTLED` — at least one
non-empty `RECEIPT_IDENTITY_KEYS` entry, which is what kills `{}`.

`SETTLED` with `receipt is None` is still permitted (unchanged): a settlement path whose
adapter records no receipt is pre-existing behaviour and is not part of this finding.

### 4.2 Closed settlement contract — `runtime_state.py:82-85, 240-258, 294-295`

```python
SETTLEMENT_KEYS = frozenset(SettlementEvent.__required_keys__)
```

`_validate_settlement()` requires the key set to be **exactly** the canonical
`SettlementEvent` vocabulary (unknown *and* missing keys reported), every non-`result` field
an exact non-empty `str`, and `result` a `dict`. The pre-existing `intent_id`/`command_id`
identity checks in `validate_record` are unchanged and still run.

### 4.3 Full stored-identity cross-check in `claim()` — `runtime_state.py:87-90, 414-421`

```python
IDENTITY_KEYS = ("run_id", "phase", "role", "round_kind", "command_id", "payload_digest")
...
mismatched = [key for key in IDENTITY_KEYS if existing.get(key) != intent[key]]
if mismatched:
    raise RuntimeStateConflict(f"IDEMPOTENCY_CONFLICT:{intent_id}:{mismatched}")
```

This replaces the digest-only comparison and runs **before** the `SETTLED` branch, so every
claim outcome is protected. `round_kind` was added beyond the five fields the Coordinator
reproduced, because the same argument applies to it verbatim.

### 4.4 Documentation

- `scripts/deterministic_workflow/ports.py:59-66` — the `RuntimeStatePort` docstring now
  states the closed record contract and the two-layer split.
- `docs/DETERMINISTIC_WORKFLOW.md:40-56` — same contract in the durable-ledger section.

## 5. Regression tests

New class `LedgerRecordIntegrityTests` — `scripts/test_deterministic_workflow_ownership.py:528`
(22 tests, module total 61). It is already inside the dependency-absent lane module list.

| reproduction case | test |
|---|---|
| empty receipt `{}` | `test_an_effected_record_with_an_empty_receipt_is_refused` |
| unknown receipt key | `test_an_unknown_receipt_key_is_refused` |
| conflicting `run_id` | `test_a_conflicting_stored_run_id_is_refused` |
| conflicting `phase` | `test_a_conflicting_stored_phase_is_refused` |
| conflicting `role` | `test_a_conflicting_stored_role_is_refused` |
| conflicting `command_id` | `test_a_conflicting_stored_command_id_is_refused` |

Plus, beyond the six: `test_a_settled_record_whose_receipt_names_no_effect_is_refused`,
`test_a_non_string_receipt_value_is_refused`, `test_an_empty_string_receipt_identifier_is_refused`,
`test_a_receipt_bound_to_another_intent_is_refused`, `test_a_truncated_settlement_is_refused`,
`test_an_unknown_settlement_key_is_refused`, `test_a_settlement_result_of_the_wrong_type_is_refused`,
`test_a_non_string_settlement_field_is_refused`, `test_a_conflicting_stored_round_kind_is_refused`,
`test_a_conflicting_stored_payload_digest_is_refused`, `test_every_identity_field_is_covered`,
`test_the_untampered_effected_record_is_resumed` (the fixture must be *accepted*, so each
refusal above is attributable to its single tamper), and
`test_a_tampered_record_fails_closed_before_any_external_effect` (runs `execute_intent_node`
and asserts `adapter.effect_count == 0` for both an empty receipt and a forged `run_id` —
the refusal happens *before* the external effect, not after).

```
$ python3 -m unittest scripts.test_deterministic_workflow_ownership.LedgerRecordIntegrityTests
Ran 22 tests in 0.017s
OK
```

### 5.1 The tests fail on the pre-fix code

The three guards were reverted in place (receipt+settlement validation calls removed, the
identity loop restored to the digest-only comparison) and the new class re-run:

```
Ran 22 tests in 0.023s
FAILED (failures=15, errors=2)
```

17 of 22 fail. The 5 that pass pre-fix are the three `*_is_load_bearing` mutation tests
(which assert the *defect* reappears and so must pass in both directions),
`test_a_conflicting_stored_payload_digest_is_refused` (already covered pre-fix — kept as a
regression guard) and `test_the_untampered_effected_record_is_resumed` (the positive fixture).

## 6. Mutation verification (each guard removed individually, then restored)

`md5 scripts/deterministic_workflow/runtime_state.py` **before** any mutation:
`96fb556e9ccc82af166d421297832ab6`

| guard removed | tests that fail |
|---|---|
| `_validate_receipt` call (`runtime_state.py:292-293`) | `failures=6, errors=1` — `test_an_effected_record_with_an_empty_receipt_is_refused`, `test_an_unknown_receipt_key_is_refused`, `test_a_non_string_receipt_value_is_refused`, `test_an_empty_string_receipt_identifier_is_refused`, `test_a_receipt_bound_to_another_intent_is_refused`, `test_a_settled_record_whose_receipt_names_no_effect_is_refused`, `test_a_tampered_record_fails_closed_before_any_external_effect` |
| `_validate_settlement` call (`runtime_state.py:294-295`) | `failures=4` — `test_a_truncated_settlement_is_refused`, `test_an_unknown_settlement_key_is_refused`, `test_a_settlement_result_of_the_wrong_type_is_refused`, `test_a_non_string_settlement_field_is_refused` |
| `IDENTITY_KEYS` loop (`runtime_state.py:420-422`) | `failures=5, errors=1` — the five `test_a_conflicting_stored_{run_id,phase,role,round_kind,command_id}_is_refused` plus `test_a_tampered_record_fails_closed_before_any_external_effect` |

Every guard is individually load-bearing. Three in-suite mutation tests keep this true for
future edits: `test_the_closed_receipt_contract_is_load_bearing`,
`test_the_closed_settlement_contract_is_load_bearing`,
`test_the_stored_identity_check_is_load_bearing`.

`md5 scripts/deterministic_workflow/runtime_state.py` **after** restoring:
`96fb556e9ccc82af166d421297832ab6` — identical, so no mutation survives in the tree.

## 7. Tests adjusted (3 lines) — no coverage removed

The tighter contract made three existing assertions describe the *old* shape. Each was
narrowed to the new contract; none was weakened or deleted.

1. `scripts/test_deterministic_workflow_round2.py:379` — recorded
   `dict(world.create(intent))`, i.e. the world entry including `outcome: None` and
   `occurred_at: None`. Now records `dict(world.find(intent["intent_id"]))`, which is exactly
   what the adapter path persists. Same assertions, same recovery being proven.
2. `scripts/test_deterministic_workflow_ownership.py:409` and `:415` — used two-key stub
   settlements, which now fail on the closed key set *before* reaching the identity check the
   test is about. A `valid_settlement()` helper builds the full canonical event and tampers
   only `intent_id` / `command_id`, so both tests still assert `settlement identity` /
   `settlement command`.

## 8. Effect on M2-001..M2-004 (PASS, untouched)

No M2-00x code path was modified. The stricter ledger validation touches their paths only in
this way, and it strengthens them rather than changing behaviour:

- **M2-003 (recovery ladder)** — an `EFFECTED` record that names no external identity now
  raises `RuntimeStateCorrupt` on read instead of entering the ladder and stranding at
  `_collect`. This is the closing of the strand M2-003's review described, and every M2-003
  test still passes unchanged (`CrashRecoveryLadderTests`, including the fresh-process
  restart test) except for the one receipt-shape line noted in §7.1.
- **M2-001, M2-002, M2-004** — untouched; `state.py`, `graph.py`, `contracts.py` and
  `executor.py` were not modified in this iteration.

## 9. Verification

| gate | result |
|---|---|
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1937 tests in 337.503s` / `OK (skipped=6)` — baseline 1915 + the 22 new tests, exit 0 |
| `python3 scripts/validate_workflow_graph_docs.py` | `Workflow graph documentation validation PASSED` |
| `python3 scripts/validate_skills.py` | `Skill validation PASSED (729 checks)` |
| `python3 scripts/verify_package.py` | `Package verification PASSED (236 source files)` |
| `python3 artifacts/runs/run_8288bf8f1d89/dependency_absent_lane.py` | `LANE errors=0 failures=0 skipped=80` (212 tests) |
| `python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo` | `terminal_status=COMPLETED ... steps=68`, exit 0 |
| `git diff --check` | clean (exit 0) |
| `diff -r -x '__pycache__' scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow` | no output — byte-identical mirror |
| repro harness | exit 0 (all six refused) |

## 10. Files modified

- `scripts/deterministic_workflow/runtime_state.py` (+ mirror)
- `scripts/deterministic_workflow/ports.py` — docstring only (+ mirror)
- `orca-worker-reviewer-orchestration/tools/deterministic_workflow/runtime_state.py`
- `orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py`
- `scripts/test_deterministic_workflow_ownership.py` — new class + 2 narrowed assertions
- `scripts/test_deterministic_workflow_round2.py` — 1 narrowed assertion
- `docs/DETERMINISTIC_WORKFLOW.md`
- `artifacts/runs/run_8288bf8f1d89/ledger_integrity_repro.py` (new, evidence)
- `artifacts/runs/run_8288bf8f1d89/BUGFIX_iteration2.md` (this report)

No commit, push, PR update, branch switch, merge or Jira change was made. No prior run
artifact was touched (`run_0bcf4e7296c9`, `run_9e3c67a7824e` untouched).

## 11. Remaining limitations (honest)

1. **`SETTLED` with `receipt is None` is still accepted.** A settlement can be recorded by a
   path whose adapter never wrote a receipt (`executor._settle_now` with an adapter that has
   no bound ledger). Requiring a receipt there would reject pre-existing valid ledgers and is
   outside this finding; the settlement itself is fully validated.
2. **`get_receipt()` / `get_settlement()` remain identity-blind**, because they take an
   `intent_id` and never see the intent. The full cross-check lives in `claim()`, which the
   executor always runs first (`executor._execute_recoverable`), and the adapters' own
   `start()` re-checks `payload_digest` against the durable record — two layers, but a caller
   who bypasses `claim()` entirely gets only the digest check.
3. **The receipt key set is closed to today's adapters.** A future adapter that needs another
   identifier must add it to `RECEIPT_KEYS` deliberately. That is the intended cost of a
   closed contract, but it is a real maintenance obligation.
4. **`fcntl.flock` is POSIX-only** (unchanged from iteration 1): `FileRuntimeStateStore`
   refuses to construct on Windows rather than degrade to an unlocked claim.
5. **OS-37 scope is unchanged.** Production process/PTY ownership, and the Orca `task-create`
   idempotency key that would close the `create_task`-crash window at the source, remain out
   of OS-40.
