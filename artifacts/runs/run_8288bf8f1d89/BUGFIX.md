# BUGFIX — PR #28 external review round 2 (OS-40)

run_id: run_8288bf8f1d89 · branch feat/os-40-langgraph-engine · base head 4d30217
role: BUGFIX Worker (iteration 1 / 5) · agent claude-opus · risk high

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "All five findings had an explicitly specified fix shape in the task, and every ambiguity was resolvable from evidence inside the repository or from the Orca CLI's own documented surface. The one genuinely open question -- whether Orca exposes a stable idempotency key or an intent-keyed lookup -- was answered by reading `orca orchestration task-create --help` and `task-list --json`: no idempotency key exists, and task-list does return each Task's full spec. The task itself pre-authorised the resulting decision ('있는 척하지 말고 ... 명시적으로 fail closed'), so the capability boundary was implemented, not escalated. No user authority was required.",
  "scope": "This phase's own conduct at this iteration."
}
```

---

## Summary

All five round-2 findings are fixed, each reproduced first and each pinned by a regression
test that fails against the pre-fix code. The durable claim is now an inter-process
`fcntl.flock` critical section with an explicit ownership/lease contract, the ledger is
strictly validated instead of degrading to an empty map, `update_state` validates the whole
merged checkpoint from every allowed `as_node`, every iteration domain enforces exact integer
range and sum, the crash-recovery ladder is defined and proven across a real process
boundary, and repository/artifact bindings are advanced by Worker settlements so every review
is bound to the output it approves.

| Finding | Verdict | Production change | Regression tests |
| --- | --- | --- | --- |
| C2-001 (CRITICAL) exclusivity | fixed | `runtime_state.py` rewritten | `test_deterministic_workflow_ownership.py` (39) |
| C2-001 (CRITICAL) ledger validation | fixed | `runtime_state.py:validate_ledger/validate_record` | same file, `DurableLedgerValidationTests` |
| M2-001 (MAJOR) `update_state` | fixed | `graph.py`, `state.py` | `Round2::StateUpdateBoundaryTests` |
| M2-002 (MAJOR) budget invariants | fixed | `state.py:_assert_iteration_domain` | `Round2::BudgetInvariantTests` |
| M2-003 (MAJOR) crash recovery | fixed | `executor.py`, `fake_adapter.py`, `orca_adapter.py`, `ports.py` | `Round2::CrashRecoveryLadderTests` |
| M2-004 (MAJOR) bindings | fixed | `contracts.py`, `executor.py`, `routing.py` | `Round2::BindingAdvancementTests` |

Files changed (production, mirrored byte-identically into
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/`):
`contracts.py`, `executor.py`, `fake_adapter.py`, `graph.py`, `launcher.py`,
`orca_adapter.py`, `ports.py`, `routing.py`, `runtime_state.py`, `state.py`.
Tests: new `scripts/test_deterministic_workflow_ownership.py`,
new `scripts/test_deterministic_workflow_round2.py`, one assertion updated in
`scripts/test_deterministic_workflow_recovery.py` (renamed reason code).
Docs: `docs/DETERMINISTIC_WORKFLOW.md`.

---

## C2-001 (CRITICAL) — the "durable" claim was neither exclusive nor fail-closed

### Reproduction (pre-fix)

**(a) Not exclusive.** Four real processes, released from a barrier, running exactly the
sequence in `executor._execute_recoverable` (`get_receipt` → `None` → `claim` →
`adapter.start`):

```text
$ python3 /tmp/repro_race2.py
external starts across 20 trials x 4 procs: 80 (required: 20)
```

Every process observed an absent intent and every process started the external effect: four
Tasks per stable intent.

**(b) Not fail-closed.** `FileRuntimeStateStore._read()` (old `runtime_state.py:154-162`)
never looked at `schema_version` and returned `{}` whenever `records` was not a dict:

```text
$ python3 /tmp/repro_r2.py
  no schema_version:    _read() -> {}                            (no exception => BUG)
  wrong schema_version: _read() -> {'i': {}}                     (no exception => BUG)
  records not a dict:   _read() -> {}                            (no exception => BUG)
  record garbage:       _read() -> {'i': {'status': 'NONSENSE'}} (no exception => BUG)

$ grep -cE 'fcntl|flock|O_EXCL|lockf|filelock' scripts/deterministic_workflow/runtime_state.py
0
```

### Fix

`scripts/deterministic_workflow/runtime_state.py` rewritten.

- **Inter-process critical section.** `_RuntimeStateStore._locked()`
  (`runtime_state.py:448-486` in the file store) takes `fcntl.flock(fd, LOCK_EX|LOCK_NB)` on a
  sidecar `<ledger>.lock`, polling to an explicit deadline. Every mutation and every read runs
  `lock → read → validate → decide → persist → unlock`; the ledger is **re-read after** the
  lock is held, so no decision comes from a pre-lock snapshot. Re-entrant within one process
  so a composed operation cannot deadlock on itself.
- **Explicit lock timeout.** `lock_timeout_seconds` (default 10 s, injectable) raises
  `RuntimeStateLockTimeout` — never an unbounded wait.
- **Ownership contract.** Records carry `owner_id`, `lease_token`, `lease_expires_at`,
  `last_heartbeat_at` (`RECORD_KEYS`, `runtime_state.py:52-60`). `claim()` returns a
  non-persisted `claim_outcome` of `CREATED` / `RESUMED` / `ALREADY_SETTLED`, so the caller can
  tell "I am the first executor" from "someone died here". A live foreign lease raises
  `RuntimeStateLeaseHeld` and the caller is demoted to observer.
- **Observer role with a finite timeout.** `observe()` (`runtime_state.py:381-406`) returns the
  SETTLED record, or `None` when the owner's lease lapses, or raises
  `RuntimeStateObservationTimeout` at its deadline. `executor._observe_then_take_over`
  (`executor.py:181-201`) wires it in: a killed owner never strands a successor.
- **Injected clock.** All lease/lock/observation arithmetic reads `ports.LeaseClockPort`
  (`SystemLeaseClock` in production, `ManualLeaseClock` in tests). No `time.time()` call and no
  `sleep`-based test.
- **Process-scoped ownership.** `default_owner_id()` is `host:pid`, so two stores over one
  ledger inside one process are one executor resuming itself, while two processes are two
  Coordinators. Different `run_id`s use different ledger files and different `intent_id`s share
  only the short critical section, so the lock never serialises parallel work.
- **Strict ledger validation.** `validate_ledger()` / `validate_record()`
  (`runtime_state.py:161-252`) check the top-level container, require the exact
  `schema_version` (now `os40.runtime_state.v2`; a `v1` file is
  `INCOMPATIBLE_RUNTIME_STATE`), reject unknown top-level keys, require `records` to be a dict,
  and validate each record as a closed structure: exact key set, string/number types, status
  vocabulary, key ↔ `intent_id` agreement, status/content coherence
  (`CLAIMED` carries nothing, `EFFECTED` has a receipt, `SETTLED` has a settlement), receipt
  and settlement shape, and settlement `intent_id`/`command_id` identity. Validation runs on
  read *and* on write. `launcher.execute_state` projects any `RuntimeStateConflict` onto a
  BLOCKED terminal with a stable reason code instead of a traceback.

### Tests

`scripts/test_deterministic_workflow_ownership.py` — 39 tests. Real processes via
`multiprocessing.get_context("spawn")` contending on a real `flock`; no thread and no mocked
call counter stands in for a process.

```text
$ python3 -m unittest scripts.test_deterministic_workflow_ownership
Ran 39 tests in 2.223s
OK
```

Key cases:

- `test_two_processes_racing_one_intent_start_the_effect_exactly_once` — 6 trials × 4 spawned
  processes, exactly 1 external start each.
- `test_distinct_intents_in_one_ledger_are_not_serialized_away` and
  `test_distinct_run_ledgers_claim_in_parallel` — 4 concurrent claims all succeed, proving the
  lock does not globally serialise.
- `test_a_silently_killed_owner_never_makes_the_observer_wait_forever` — a child claims and
  `SIGKILL`s itself; the observer returns before its own deadline and then takes over exactly
  once.
- `test_lock_acquisition_has_an_explicit_timeout` — a foreign process holds the flock;
  `RuntimeStateLockTimeout` after 0.2 s.
- `test_the_ledger_is_re_read_inside_the_lock` — every `_read` during a claim happens at
  `_depth > 0`.
- `OwnershipContractTests` (8) — lease fields, live-lease refusal, single takeover after
  expiry, heartbeat keeping a rival out, stale-token refusal, release, observation timeout,
  non-positive timeout — all on `ManualLeaseClock`; the observation-timeout test asserts real
  elapsed time stays below the logical timeout, proving the clock is injected.
- `DurableLedgerValidationTests` (21) — the full mutation matrix: missing / wrong-typed /
  incompatible `schema_version`, wrong top-level container, unknown top-level key, wrong
  `records` container, wrong record container, unknown record key, missing record key, unknown
  status, key/identity mismatch, malformed receipt, `EFFECTED` without receipt, `SETTLED`
  without settlement, settlement bound to another intent, settlement bound to another command,
  wrong lease type, unparseable JSON.
- `test_a_corrupt_ledger_fails_closed_before_any_external_effect` — `adapter.effect_count == 0`.
- `CrashSafeWriteTests` (3) — **separate from the concurrency tests**: `os.replace` failure
  leaves the committed ledger byte-identical and no temp debris; a committed ledger is always
  re-readable; a runtime handle is still refused.

---

## M2-001 (MAJOR) — `update_state` validated only field names

### Reproduction (pre-fix)

```text
$ python3 /tmp/repro_update.py
  invalid decision_state:   ACCEPTED (BUG) -> decision_state='TOTALLY_BOGUS'
  negative budget:          ACCEPTED (BUG) -> phase_iterations={'ANALYSIS': -100}
  garbage pending_intent:   ACCEPTED (BUG)
  bogus terminal_status:    ACCEPTED (BUG) -> terminal='WAT'
```

### Fix

- `graph.py:_guard_update` / `_aguard_update` read the persisted snapshot, rebuild the closed
  field set (LangGraph omits `None` channels — `_merge_checkpoint`), merge the caller's values,
  and run the complete `validate_state` contract before committing. Both the sync and the async
  ingress use it.
- `as_node` is restricted to `ALLOWED_UPDATE_NODES` (the graph's real nodes); because `as_node`
  can resume past `VALIDATE`, validation happens at the boundary rather than relying on
  `VALIDATE` running afterwards.
- `state.py:_assert_value_domains`, `_assert_pending_intent`, `_assert_pending_event` close the
  remaining value domains: `route_token`, `terminal_status`, `intent_status`, `pending_role`,
  optional string/dict fields, `phase_passes` entries, queue membership and index ranges, and
  the full closed shapes of `pending_intent` / `pending_event` including their mutual binding.
- `state.UPDATE_COMMANDS` + `typed_update()` provide a closed typed-command vocabulary
  (`SET_DECISION`, `SET_CLARIFICATION`, `SET_REPOSITORY_BINDING`, `SET_ARTIFACT_BINDING`,
  `CLEAR_PENDING`), surfaced as `update_state_command` / `aupdate_state_command`; the raw
  mapping path still exists but is fully validated.

### Tests — `Round2::StateUpdateBoundaryTests` (9)

14 injections × sync and async: invalid decision state, negative/boolean phase budget, negative
final budget, unknown route token / terminal status / intent status / pending role, forged
pending intent, forged pending event, phase-index incoherence, post-terminal pending role,
out-of-vocabulary round kind, queue phase outside the vocabulary. All rejected, and the
checkpoint is asserted unchanged afterwards.
`test_every_allowed_as_node_is_validated` repeats the injections **for each of the eight allowed
`as_node` values**, sync and async. Also covered: unknown `as_node`, unknown field names,
non-mapping updates, an update to a thread that never ran, a valid update still committing, and
the typed-command vocabulary.

Post-fix output of the same reproduction script:

```text
  invalid decision_state:   rejected StateError: MALFORMED_STATE:decision
  negative budget:          rejected StateError: MALFORMED_STATE:phase budget:ANALYSIS consumed range
  garbage pending_intent:   rejected StateError: MALFORMED_STATE:pending intent shape
  bogus terminal_status:    rejected StateError: MALFORMED_STATE:terminal status
  as_node bypass VALIDATE:  rejected StateError: MALFORMED_STATE:decision
```

---

## M2-002 (MAJOR) — budget invariants accepted negative and non-domain counts

### Reproduction (pre-fix)

```text
=== M2-002: negative consumed / bool ===
  negative consumed ACCEPTED (BUG)     # phase_iterations=-100, remaining=105, max=5
  bool consumed ACCEPTED (BUG)         # phase_iterations=True, remaining=4
  negative final ACCEPTED (BUG)        # final_review_iterations=-3, remaining=8
```

### Fix

`state.py:_assert_iteration_domain` replaces the bare equality check and is applied to every
phase budget and to the Final Review budget:

```python
if type(value) is not int:                 raise StateError(... " type")
if not 0 <= value <= maximum:              raise StateError(... " range")
if consumed + remaining != maximum:        raise StateError(... " sum")
```

`type(value) is not int` rejects `bool` explicitly (`isinstance(True, int)` is True and
`True + 4 == 5`). `remaining_phase_budget` is also required to have exactly the requested
phases as keys.

### Tests — `Round2::BudgetInvariantTests` (7)

The review's own counterexample; boolean counts in **both** domains; the boundary table
`(0,5) (5,0) (3,2)` accepted and `(-1,6) (6,-1) (2,2) (2,4)` rejected in both domains;
non-integer values (`2.0`, `"2"`, `None`, `[2]`); a missing phase in the remaining map; and
`test_a_tampered_budget_cannot_buy_another_dispatch`, which drives `validate_node` and asserts
the run is bound to BLOCK/`MALFORMED_STATE` with the forged counter normalised away.

---

## M2-003 (MAJOR) — crash states recorded but not recoverable

### Reproduction (pre-fix)

Confirmed by reading `executor._execute_recoverable` (old lines 84-103): a `CLAIMED` record
with no adapter-local event always raised `IDEMPOTENCY_RECOVERY_REQUIRED`, and an `EFFECTED`
record without a settlement took the same path — permanently stranded, with no lookup or
reconciliation step of any kind.

### Fix

**First, what Orca actually offers** (checked directly, not assumed):

```text
$ orca orchestration task-create --help
Usage: ... --spec <text> [--task-title] [--display-name] [--deps] [--parent] [--run] [--from] ...
   -> no idempotency key of any kind

$ orca orchestration task-list --run run_8288bf8f1d89 --json
   -> tasks carry "id", "status", ... and the FULL "spec"
```

So an intent-keyed **lookup** is real (every spec this adapter creates is the canonical intent
JSON containing `intent_id`), but **resume** is not: `worker_done` is delivered once, to the
owning process's message stream, and a settlement delivered to a dead process cannot be
re-collected through any documented primitive. `OrcaAdapter` therefore declares
`external_lookup` and deliberately does **not** declare `external_resume`.

- `contracts.py` adds the optional capabilities `external_lookup` / `external_resume`
  (`RECOVERY_CAPABILITIES`, outside `BASE_CAPABILITIES`) and `ExternalLookupUnavailable`, which
  distinguishes "unknown" from "proven absent".
- `ports.ExternalRecoveryPort` declares the optional `lookup` / `resume` protocol.
- `executor._recover` implements the five-rung ladder exactly as specified:
  1. ask the adapter for a settlement of the stable identity;
  2. `EFFECTED` → `_collect()` resumes/observes the already-named external effect;
  3. `CLAIMED` → `adapter.lookup(intent)` by stable identity;
  4. lookup proves absence → and only then `_settle_now()` re-runs;
  5. capability missing, lookup unanswerable, or the effect still running → raise
     `IdempotencyRecoveryError` with code `IDEMPOTENCY_RECOVERY_UNSUPPORTED` /
     `IDEMPOTENCY_RECOVERY_BLOCKED`, never a second effect.
- `executor._execute_recoverable` now goes through the exclusive `claim()` and branches on
  `claim_outcome`; `RuntimeStateLeaseHeld` routes into the bounded observer role.
- `launcher.execute_state` projects `IdempotencyRecoveryError` onto a BLOCKED terminal
  (exit code 1) so the refusal is a terminal outcome, not a crash.
- `fake_adapter.FileExternalWorld` is the **verifiable reference implementation** of what an
  external runtime must provide: an effect discoverable by stable intent identity before it
  produces an outcome, and an outcome readable by a process that did not create it.
  `FakeAdapter` declares the recovery capabilities only when a world actually backs them.

### Tests — `Round2::CrashRecoveryLadderTests` (13)

- `test_claimed_without_lookup_capability_fails_closed_and_creates_nothing` —
  `IDEMPOTENCY_RECOVERY_UNSUPPORTED`, `effect_count == 0`.
- `test_claimed_with_a_lookup_proving_absence_reruns_exactly_once` — rung 4.
- `test_a_running_effect_is_observed_not_recreated` — created-but-unsettled Task →
  `IDEMPOTENCY_RECOVERY_BLOCKED`, `effect_count == 0`, and the discovered external identity is
  recorded durably (`status == EFFECTED`).
- `test_effected_without_resume_capability_fails_closed`.
- `test_effected_with_resume_collects_the_existing_settlement` — **fresh store, fresh adapter**,
  `effect_count == 0`, settlement collected.
- `test_a_fresh_process_resumes_the_workflow_to_completion` — **restart continuation across a
  real `subprocess` boundary**: a child process runs the workflow with a durable ledger and
  external world and is stopped mid-flight; this process then finishes the same run with brand
  new ledger, adapter and graph objects, reaches `COMPLETED`, runs only the not-yet-done work,
  and every intent the dead process settled keeps its one original `event_id`. This is
  continuation, not refusal.
- `test_an_identity_conflict_on_the_same_intent_is_refused`.
- Four `OrcaAdapter` tests: the declared capability set is exactly what Orca supports; the
  lookup raises `ExternalLookupUnavailable` for no bound run / unreadable CLI / a spec-less
  listing; it finds a Task by stable `intent_id`; and it proves absence when no Task carries it.
- `test_the_launcher_projects_a_recovery_refusal_onto_a_blocked_terminal`.

---

## M2-004 (MAJOR) — repository/artifact bindings never advanced

### Reproduction (pre-fix)

```text
$ grep -n 'repository_binding\|artifact_binding' scripts/deterministic_workflow/executor.py
155:  ... "tree_digest": new["repository_binding"]["tree_digest"] ...
160:  ... "tree_digest": new["repository_binding"]["tree_digest"] ...
```

Two read sites, zero write sites: every phase pass recorded the run's initial default
`tree_digest` ("clean" / `head_sha` `000…0`), and no Reviewer intent was bound to any Worker
output.

### Fix

- `contracts.py` defines the normalized binding contract:
  `normalize_repository_binding` (`head_sha` = 40 lowercase hex, non-empty `tree_digest`,
  `bool` `dirty`), `normalize_artifact_binding` (`artifact_root_id`, `relative_path`, `digest`,
  `evidence_ids`), `validate_settlement_binding` (pins `artifact_root_id` to the intent's own
  run) and `binding_snapshot`. `validate_event` rejects a malformed binding as
  `MALFORMED_EVENT` and a binding on a non-Worker settlement as `UNKNOWN_EVENT`. Because the
  binding lives inside `result`, it is already covered by the settlement digest, so tampering
  fails as `SETTLEMENT_INTEGRITY`.
- `executor.apply_result_node` advances `repository_binding` / `artifact_binding` from a Worker
  settlement **before** anything downstream reads them — i.e. before the Reviewer dispatch.
- `executor.role_binding_is_stale` + `_reject_settlement`: a Reviewer or Final Reviewer whose
  intent binding no longer matches state fails closed as `STALE_REVIEW_BINDING` (route BLOCK,
  result not applied, no pass recorded).
- `executor._pass_record` records `head_sha`, `tree_digest`, `artifact_digest` and the full
  `reviewed_binding` on every gate pass; `final_reviewer_result["reviewed_binding"]` records
  what the Final Review actually saw.
- `routing.final_review_binding_current` gates the COMPLETE edge, and
  `routing.verify_final_review_binding` / `phase_pass_binding` make "the Final Reviewer reviewed
  the final head and artifacts" a checkable fact. `terminal_node` reports
  `STALE_FINAL_REVIEW_BINDING`.

### Tests — `Round2::BindingAdvancementTests` (14)

Worker settlement advances both bindings while `initial_repository_binding` is left as history;
the Reviewer intent carries the advanced binding; the pass records the reviewed binding; stale
head and stale artifact each fail closed with no pass recorded; five malformed-binding shapes
rejected; a binding scoped to another run rejected; a binding on a Reviewer settlement rejected;
a tampered binding failing the digest; `verify_final_review_binding` returning the reviewed
tree; a Final Review PASS against a moved head routing BLOCK and terminating
`STALE_FINAL_REVIEW_BINDING`; a forged final result with no binding unable to complete; and a
full end-to-end graph run where the Worker's head reaches the phase pass and the Final Review
verification.

---

## Mutation sensitivity

Each guard was removed from the production source and the corresponding tests re-run. Every
guard is load-bearing — no guard can be deleted and leave the suite green.

```text
### M1: remove fcntl.flock exclusive lock
Ran 1 test    FAILED (failures=6)          # the 6 race subtests

### M2: restore the permissive _read (ignore schema_version/records)
Ran 21 tests  FAILED (failures=10, errors=3)

### M3: remove the iteration-domain invariants
Ran 7 tests   FAILED (failures=7, errors=3)

### M4: revert update_state to name-only checking
Ran 10 tests  FAILED (failures=38, errors=1)

### M5: remove the stale reviewer-binding guard
Ran 15 tests  FAILED (errors=2)

### M6: remove the recovery ladder (settle blindly)
Ran 13 tests  FAILED (failures=2, errors=3)

production files restored
```

In addition, six in-suite `*_is_load_bearing` tests assert the *defect returns* when the guard
is patched out, so the pairing is checked on every run, not only by this manual pass.

---

## Verification

```text
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1915 tests in 336.314s
OK (skipped=6)
```

Baseline was 1831 / `OK (skipped=6)` — **+84 tests, zero regressions**.

```text
$ python3 /tmp/dep_absent_lane.py          # sys.meta_path block on `langgraph`, 9 engine modules
Ran 190 tests in 2.391s
OK (skipped=80)
LANE errors=0 failures=0 skipped=80

$ python3 scripts/validate_skills.py
Skill validation PASSED (729 checks)

$ python3 scripts/verify_package.py
Package verification PASSED (236 source files)

$ python3 scripts/build_release.py && python3 scripts/verify_package.py --archive dist/orca-skills-0.9.0.tar.gz
Package verification PASSED (236 source files)
Verified archive: dist/orca-skills-0.9.0.tar.gz

$ python3 scripts/validate_workflow_graph_docs.py
Workflow graph documentation validation PASSED

$ python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo
terminal_status=COMPLETED reason=WORKFLOW_COMPLETED phases=['ANALYSIS', 'PLAN', 'DESIGN', 'IMPLEMENTATION', 'TEST'] steps=68   (exit 0)

$ git diff --check
(clean)

$ diff -r scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow
(no output — byte-identical mirror)
```

The dependency-absent lane grew from 106 to 190 tests because the new modules are largely
runtime-neutral; the langgraph-dependent cases carry the same import-based
`skipUnless(_langgraph_ok())` guard the existing modules use.

---

## Known limitations (documented in `docs/DETERMINISTIC_WORKFLOW.md`)

1. **POSIX only.** The exclusive claim requires `fcntl.flock`. Without it
   `FileRuntimeStateStore` refuses to construct (`RuntimeStateLockUnavailable`) rather than
   degrade to the unlocked behaviour that caused this finding. Windows is unsupported.
2. **`OrcaAdapter` has no `external_resume`.** Orca can find an existing Task by stable
   `intent_id` (`task-list --run` returns full specs) but cannot re-deliver a `worker_done`
   to a process that did not receive it. Recovery of an already-dispatched Orca effect
   terminates `BLOCKED` (`IDEMPOTENCY_RECOVERY_UNSUPPORTED`); reconciliation is an operator
   decision, and closing the window is OS-37's production process/PTY work.
3. **Residual create-then-crash window.** The claim is durable before `create_task`, so a
   crash is always *detectable*, but the external identifier only exists after the call
   returns. With no caller-supplied idempotency key the window cannot be eliminated — only
   made safe, which is what the lookup rung does when the adapter supports it.
4. **`InMemoryRuntimeStateStore` gives no inter-process exclusion** (thread lock only); it is
   for single-process tests.
5. **Schema bump to `os40.runtime_state.v2`.** A `v1` ledger from an earlier build is refused
   as `INCOMPATIBLE_RUNTIME_STATE` (BLOCKED, exit 1) rather than silently ignored, so a stale
   ledger in `$ORCA_OS40_RUNTIME_STATE_DIR` (or the system temp dir) must be removed
   deliberately. This is intentional fail-closed behaviour, and it is what a corrupt ledger
   must do.
6. The lease default is 60 s and the lock timeout 10 s; both are injectable per store. No
   automatic lease renewal runs in the graph today — a long-running external effect that
   outlives its lease can be taken over by a successor, which then enters the recovery ladder
   (and, on `OrcaAdapter`, fails closed rather than duplicating).

---

## PR description update (for the Coordinator to apply)

Worker does not touch the PR. Suggested replacements:

**Test results**

```
- Full unit suite: `python3 -m unittest discover -s scripts -p 'test_*.py'` — 1915 tests, OK (skipped=6).
- Dependency-absent lane (import-blocked `langgraph`, 9 engine modules): 190 tests, OK (skipped=80), errors=0.
- Skill validation: PASSED (729 checks). Package verification: PASSED (236 source files) + archive.
- Graph documentation validation: PASSED. `run_workflow.py --demo`: COMPLETED, 68 steps, exit 0.
- `git diff --check`: clean. Production/Skill mirror: byte-identical.
- Mutation sensitivity: each of the six new guards removed in turn; every one produced failures.
```

**Known limitations** — replace with items 1–6 above.

