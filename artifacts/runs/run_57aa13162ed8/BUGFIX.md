# Worker Result

STATUS: COMPLETE

Run: run_57aa13162ed8 · Phase: BUGFIX · Iteration: 1 · Role: worker
Branch: os-31-durable-pause-resume (existing PR #30 branch, no new branch, nothing staged or committed)
PR head at start: baebc475400602fa34019113505f555ea4cdfe95

UNIT_TEST_STATUS: PASS

---

## Bug / Root Cause Evidence

### DEFECT 1 (CRITICAL) — the resume lease was never renewed

`resume_run()` took a lease with `store.claim()` and then held the whole claimed section —
read the decision, C1/C2/C3 revalidation, `record_applied`, `update_state_command`,
`graph.invoke()`, `promote_applied`, `mark_resumed` — without ever calling the store's own
`heartbeat()`. `grep -n heartbeat scripts/deterministic_workflow/pause_runtime.py` at HEAD
matched only the record *field* `last_heartbeat_at`; `FilePauseRecordStore.heartbeat()`
existed and had no caller in this module.

Consequence, reproduced as a test: once the lease lapses (default 60s, and `graph.invoke()`
is an unbounded blocking call), a second Coordinator's `claim()` legally succeeds against a
perfectly healthy owner. It then finds the bundle at stage `RECORDED` and a checkpoint head
that still says `WAITING_FOR_INPUT`, so it falls through to `update_state_command()` /
`invoke()` and drives the same re-entry concurrently. In the mutation run this does not
merely duplicate work — coordinator A, unparked afterwards, dies on
`MALFORMED_STATE:lifecycle coherence` because B has already moved the run underneath it.

The package already contained the right mechanism for exactly this failure: `lease_keeper`,
written for the executor's blocking `adapter.start()`. It was simply never wired to the
run-scoped pause lease.

### DEFECT 2 (CRITICAL) — `create()` dropped every generation after the first

`FilePauseRecordStore.create()` read the run-scoped record and did
`if existing is not None: return deepcopy(existing)` unconditionally. A run that pauses,
resumes, and pauses again therefore had its second pause discarded: the new
`checkpoint_id`, `checkpoint_digest`, `pause_record_id`, request and projection never
reached disk, and `discover` kept advertising generation 1.

Two halves, and the second was invisible in the reported symptom: nothing called
`finalize_pause` after a resume at all. A resumed run that blocked again committed its
pause to the checkpoint (the authority) and had **no** Tier-2 record written for it, so even
with `create()` fixed the generation would still have been lost.

### DEFECT 3 (MAJOR) — the observation window was half the lease

`DEFAULT_OBSERVE_TIMEOUT_SECONDS = 30.0` against `DEFAULT_LEASE_SECONDS = 60.0`
(pause_store.py:74-75 at HEAD). `takeover()` documents a single observe-then-takeover call,
but `observe()` gives up 30 seconds before the incumbent's lease can possibly lapse, so that
path could never legally reach the takeover and an undocumented manual retry was required.
`launcher.py`'s `--observe-timeout` repeated the same 30.0 constant.

## Fix / Modified Files

Production (mirrored byte-identically into `orca-worker-reviewer-orchestration/tools/`):

| File | Change |
| --- | --- |
| `scripts/deterministic_workflow/pause_runtime.py` | lease keeper across the whole claimed section of `resume_run` and `dispose_run`; `_still_owned` / `_committed` ownership checkpoints; `PAUSE_CLAIM_LOST` fail-closed path; `finalize_pause` of the next pause generation; `next_pause_record` on `ResumeOutcome`; `observe_timeout_seconds` defaults to the store's derived window |
| `scripts/deterministic_workflow/pause_store.py` | explicit pause-generation policy in `create()`; retained `superseded` history in the durable document; `superseded()` reader; `DEFAULT_OBSERVE_GRACE_SECONDS` + `observe_timeout_for()`; `observe()` default derived from the lease |
| `scripts/deterministic_workflow/pause_policy.py` | two new closed reason codes: `PAUSE_GENERATION_ACTIVE`, `PAUSE_GENERATION_LINEAGE` |
| `scripts/deterministic_workflow/launcher.py` | `--observe-timeout` defaults to the derived window and documents the retry contract; the resume summary reports `next_pause_record_id` |
| `orca-worker-reviewer-orchestration/SKILL.md` | the public contract for the three behaviours above (generation policy, lease held across the claimed section, observation covers the lease) |

New test: `scripts/test_os31_pause_fencing.py` (21 tests).

### 1. Resume lease fencing

The claimed section of `resume_run` now runs inside `lease_keeper.LeaseKeeper`, the same
component the executor already uses for its blocking adapter calls — no second mechanism was
invented. `FilePauseRecordStore` already exposes exactly the surface the keeper needs
(`heartbeat(id, token)` and `lease_seconds`), so the keeper is constructed as
`factory(store, run_id, lease_token)`.

* renewal covers the decision read, revalidation, `record_applied`, the checkpoint update
  **and** `graph.invoke()`, at a period derived from the lease (`lease / 3`);
* every ownership-sensitive write goes through `_committed()`, which takes an ownership
  checkpoint before *and* after the write (a renewal that fails mid-write need not rotate the
  token, so a check only before the write would swallow the loss);
* `_still_owned()` is also taken immediately after `graph.invoke()` returns, and the keeper's
  `__exit__` takes the final checkpoint on the success path;
* losing ownership raises `LeaseRenewalFailed`, which is caught and returned as
  `ResumeOutcome(status="REFUSED", code="PAUSE_CLAIM_LOST")` — a named, closed reason code.
  No further effect and no further state mutation happen after that point: in particular the
  superseded owner never writes `promote_applied` or `mark_resumed`.
* `dispose_run` gets the identical treatment. It is the same defect in the same claimed
  section (revalidate → `invoke` → write the terminal disposition) and shares one helper;
  leaving it out would have left half the reported race open.

Duplicate execution under a *legitimate* takeover is unchanged and still prevented by the
pre-existing layers: the bundle-scoped `resume_bundle_id` dedupe entry written strictly
before the effect, the C2 head check, and the intent-level ledger inside the executor.
Fencing stops the *concurrent* case, which is the one that had no defence.

### 2. Repeated pause generations

`create()` now implements an explicit, stated policy instead of an unconditional return:

| existing record | candidate | outcome |
| --- | --- | --- |
| none | any | persisted — generation 1 |
| same `pause_record_id`, `WAITING_FOR_INPUT` | any | idempotent: the stored record is returned, live lease columns preserved |
| `WAITING_FOR_INPUT`, different `pause_record_id` | — | **refused** `PAUSE_GENERATION_ACTIVE` |
| `CANCELLED` / `ABANDONED` | — | **refused** `RUN_ALREADY_CANCELLED` / `RUN_ALREADY_ABANDONED` |
| `RESUMED`, same `pause_record_id` and same `checkpoint_id` | — | idempotent re-finalise |
| `RESUMED`, same `checkpoint_id`, different `pause_record_id` | — | **refused** `PAUSE_GENERATION_LINEAGE` |
| `RESUMED`, `binding_generation` moving backwards | — | **refused** `PAUSE_GENERATION_LINEAGE` |
| `RESUMED`, new checkpoint, non-decreasing `binding_generation` | — | **superseded**: the answered generation is retained, the new one becomes active |

All three discriminators the review asked for are used: `pause_record_id` identifies the
generation, `checkpoint_id` is its lineage pin (a successor must name its own checkpoint),
and `projection.binding_generation` must not move backwards.

**Terminal-generation policy: supersede the active slot, RETAIN the generation.** The
answered generation is moved whole into a `superseded` list in the same durable document and
is readable through `FilePauseRecordStore.superseded(run_id)`. Retention rather than deletion
is deliberate: a `RESUMED` generation's `applied` set *is* the OS-30 consumption lineage and
the evidence that its bundle was applied exactly once, so it must not evaporate when the run
pauses again. The `superseded` key is omitted entirely while the history is empty, so a
single-generation document is byte-identical to the previous format and the C4
"a second reindex is a byte-identical no-op" property is untouched.

An **active** `WAITING_FOR_INPUT` generation is never overwritten under any condition: that
is a human's open decision, and overwriting it would strand it with no record of what was
asked. It is a fail-closed refusal with the named code `PAUSE_GENERATION_ACTIVE`.

`resume_run` now finalises the next generation itself, after the keeper is retired and after
generation 1 is `RESUMED` on disk (which is what makes the write a supersede rather than an
overwrite of a live pause). It is written outside the lease on purpose: the new generation
must be claimable by the next Coordinator, so it is created with no owner and no lease
exactly like a first pause. The result is reported as `ResumeOutcome.next_pause_record`, and
the CLI's resume summary carries `next_pause_record_id` so a resumed-then-re-paused run is
not reported as a bare "RESUMED".

### 3. Observation / lease coherence

`DEFAULT_OBSERVE_TIMEOUT_SECONDS` is now `DEFAULT_LEASE_SECONDS + DEFAULT_OBSERVE_GRACE_SECONDS`
(60 + 5 = 65), and `observe()`'s default is derived per store by `observe_timeout_for(lease)`
rather than being a constant — the same reasoning `lease_keeper.heartbeat_interval_for` already
applies to the renewal period, so reconfiguring the lease cannot re-create the defect. The
wait stays explicitly bounded: one lease plus the grace, never unbounded.

The retry contract is now stated in the public docstring and in SKILL.md: an explicit
`timeout_seconds` is honoured exactly as given, and `PAUSE_OBSERVATION_TIMEOUT` is a
*retryable* outcome that claims nothing and performs no effect — it says only that this
observer's window closed while a live lease was held, and is never a verdict about the run.
`takeover()`'s documented single-call observe-then-takeover path now actually works with the
defaults, which is what the review asked for.

## Regression Test

Test file: `scripts/test_os31_pause_fencing.py` — 21 tests, fake/in-memory adapter, no Orca.
Store-level suites use the injected `ManualLeaseClock` and never sleep; the two suites that
must exercise a real renewal thread are paced by an explicit short lease (1.0s) plus an
explicit `threading.Event` handshake, and the keeper beats every 0.02s (50 beats per lease),
so a stalled machine cannot make a healthy owner look dead.

Before Fix: FAIL (proved by mutation, below — the fix is not separable from the tests, so
each mutation restores exactly the defective behaviour and the test dies).
After Fix: PASS

| # | Required regression | Test | Mutation that kills it |
| --- | --- | --- | --- |
| 1 | A/B concurrency, short lease, blocked `graph.invoke()` | `ResumeLeaseFencingTests.test_b_cannot_reach_the_effect_while_a_is_inside_the_invocation` | remove the lease heartbeat |
| 1b | the same race one step earlier, where an unfenced B reaches a **second** effect | `ResumeLeaseFencingTests.test_b_cannot_drive_a_second_effect_while_a_owns_the_unmoved_checkpoint` | remove the lease heartbeat |
| 2 | A exits without heartbeating → B takes over and completes | `ResumeLeaseFencingTests.test_an_owner_that_stops_heartbeating_is_taken_over_in_one_call` | (single-call takeover; also covers M3 end-to-end) |
| 1c | a lost lease stops the resume dead with a named code | `ResumeLeaseFencingTests.test_a_lost_lease_stops_the_resume_dead_with_a_named_code` | remove the `PAUSE_CLAIM_LOST` fail-closed path |
| 3 | pause #1 → resume #1 → pause #2 → resume #2, both generations distinct and durable | `RepeatedPauseGenerationE2ETests.test_a_run_pauses_resumes_pauses_and_resumes_again` | remove the generation check |
| 4 | duplicate resume of the SAME generation is no-effect | same test (tail) and `ResumeCrashBoundaryTests.test_a_duplicate_resume_of_the_same_generation_performs_no_second_effect` | — |
| 5a | crash **before** the applied record write | `ResumeCrashBoundaryTests.test_a_crash_before_the_applied_record_write_leaves_nothing_applied` | — |
| 5b | crash **after** the applied write, **before** the checkpoint update | `..._after_the_applied_write_and_before_the_checkpoint_update_re_drives` | — |
| 5c | crash **after** the checkpoint update | `..._after_the_checkpoint_update_refuses_rather_than_re_driving` | — |
| 5d | crash after `invoke`, before record promotion | `..._after_invoke_and_before_promotion_never_repeats_the_effect` | — |
| 2/3 | store-level generation policy (8 cases) | `PauseGenerationStoreTests` | remove the generation check |
| 3 | observation covers the lease, bounded, with a retry contract | `ObservationLeaseCoherenceTests` (3 cases) | revert the observe default to a fixed 30s |

### Mutation evidence (actually executed, not predicted)

**Mutation 1 — remove the lease heartbeat** (`LeaseKeeper._run` patched to return
immediately, i.e. the lease is claimed and never renewed):

```
FAILED (failures=1, errors=1)
FAILED TESTS:
  - test_b_cannot_reach_the_effect_while_a_is_inside_the_invocation
      AssertionError: 'STALE_CHECKPOINT_HEAD' not found in
      ('PAUSE_CLAIM_HELD', 'PAUSE_OBSERVATION_TIMEOUT')   <- B claimed a run A still owned
  - test_b_cannot_drive_a_second_effect_while_a_owns_the_unmoved_checkpoint
      StateError: MALFORMED_STATE:lifecycle coherence     <- B re-entered the run under A
```

The other two fencing tests still pass under this mutation, which is correct: they do not
depend on renewal.

**Mutation 2 — remove the generation check** (`create()` restored to
`if existing is not None: return deepcopy(existing)`):

```
Ran 9 tests   FAILED (failures=7)
  - test_a_second_generation_is_persisted_and_never_silently_reuses_the_first
  - test_the_superseded_generation_is_retained_whole_with_its_applied_lineage
  - test_an_active_waiting_generation_is_never_overwritten
  - test_a_successor_naming_the_same_checkpoint_is_refused_on_lineage
  - test_binding_generation_never_moves_backwards
  - test_a_disposed_run_takes_no_further_generation
  - test_a_run_pauses_resumes_pauses_and_resumes_again          (the E2E)
```

**Mutation 3 — revert the observation window to a fixed 30s**:

```
Ran 3 tests   FAILED (failures=1, errors=1)
  - test_the_default_observation_window_covers_a_whole_lease_and_stays_bounded
  - test_one_observe_then_takeover_call_reaches_the_takeover
      PauseObservationTimeout: PAUSE_OBSERVATION_TIMEOUT:run_x:owner=host:pid1
```

## Related Unit Tests / Validation

Actually observed output, after the fix, on the final tree:

```
python3 -m unittest discover -s scripts -p 'test_*.py'
  Ran 2260 tests in 402.026s
  OK (skipped=6)

python3 scripts/validate_skills.py
  Skill validation PASSED (737 checks)
  Validated both skills, shared templates/reviews, routing, and policy gates.

python3 scripts/validate_workflow_graph_docs.py
  Workflow graph documentation validation PASSED

diff -r scripts/deterministic_workflow \
        orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__
  (no output — mirror parity clean)

python3 scripts/verify_package.py
  Package verification PASSED (258 source files)
```

Baseline was 2239 tests; 2260 = 2239 + the 21 new regressions. No existing test was
weakened, skipped, xfailed or deleted, and no existing test file was edited.

## Review Feedback Resolution

| Review finding | Resolution |
| --- | --- |
| CRITICAL 1 — live resume loses its lease, second Coordinator executes the same effect | Fixed. Lease renewed across the whole claimed section by `LeaseKeeper`, ownership checkpointed before and after every write and immediately after `graph.invoke()`, `PAUSE_CLAIM_LOST` fail-closed on loss. Pinned by a short-lease/blocking-invoke race test that dies when the heartbeat is removed. |
| CRITICAL 1 inline @ pause_runtime.py:533 | Same fix, at that exact call site: `update_state_command` and `graph.invoke()` are both inside the keeper and are followed by an ownership checkpoint. |
| CRITICAL 2 inline @ pause_store.py:368 — the unconditional existing-record return | Fixed. Explicit generation policy with `pause_record_id` / checkpoint lineage / `binding_generation`, a stated retain-the-superseded policy, and a fail-closed `PAUSE_GENERATION_ACTIVE` refusal for a live WAITING generation. `resume_run` now also finalises the next generation, without which pause #2 had no record at all. |
| MAJOR 3 — observation window shorter than the lease | Fixed. Default window = lease + grace, derived per store, bounded; explicit shorter windows honoured, with `PAUSE_OBSERVATION_TIMEOUT` documented as a retryable outcome that claims nothing. CLI default follows. |

Out of scope and deliberately untouched: `runtime_state.py`'s own 60/30 pair (the
intent-scoped ledger, not the run-scoped pause fence, and not part of the reported defect),
the OS-31 contracts, and every historical run artifact.

Two honest notes, neither a blocker:

1. `resume_run`'s pre-existing "RECORDED + head already moved" adoption branch is only
   reachable if the head moves between `assert_c2` and the reconstruct, because `assert_c2`
   refuses a moved head first. Test 5d therefore asserts the behaviour that actually happens
   at that crash boundary — `REFUSED / STALE_CHECKPOINT_HEAD`, no second effect, and the
   bundle left at `RECORDED` so an unproven resume is never promoted — rather than asserting a
   branch the code cannot reach. Repairing that pointer is the pre-existing, already-tested
   `update_pointer`-under-the-claim path. Changing it was not part of the three defects.
2. If the successor generation itself could not be recorded, `resume_run` returns the
   generation's refusal code even though the re-entry committed. The run is not lost: the
   checkpoint is the authority and C4 `reindex` re-derives the record forward.

### One deviation from the dispatch, and why

The dispatch said to use `"blast_radius": "repository"` in the gate record. The real
validator refuses that: `blast_radius` declares `["repository", "external_system"]` as
*triggering* values (SKILL.md:389), so `repository` fires clause N-1 and forces NEEDS_INPUT,
and the self-check printed

```
GATE REFUSED -> DECISION_GATE_INPUT_MALFORMED -- CLEAR declares ['open_decision_item'] as
grounds, but they do not satisfy the CLEAR entry condition
```

The dispatch's own copy-this-shape block (which it states is verified against the real
validator) uses `current_change`, and the dispatch also requires the self-check to print
GATE OK. The record therefore declares `current_change`, which is also the honest reading:
this work is one reviewed changeset on an existing PR branch, nothing is staged, committed
or pushed, and it reaches no system outside this working tree. Flagging it here so the
Coordinator can see the substitution rather than discover it.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: Every choice this phase required was decided by the explicit requirements in the
task and by the existing package contracts. The one genuinely open design question — what
happens to a terminal pause generation — was named by the task as the Worker's to decide and
implement ("Decide and IMPLEMENT an explicit policy ... and state which you chose and why").
It was decided as supersede-the-active-slot / retain-the-generation, on evidence internal to
this repository (a RESUMED generation's applied set is the OS-30 consumption lineage L3 and
is required evidence), and it is reversible within this run. No user authority boundary was
reached.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "run": "run_57aa13162ed8",
  "phase": "BUGFIX",
  "iteration": 1,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "Observed on the final tree: python3 -m unittest discover -s scripts -p 'test_*.py' -> Ran 2260 tests in 402.026s, OK (skipped=6); python3 scripts/validate_skills.py -> Skill validation PASSED (737 checks); python3 scripts/validate_workflow_graph_docs.py -> Workflow graph documentation validation PASSED; diff -r scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__ -> no output (parity clean); python3 scripts/verify_package.py -> Package verification PASSED (258 source files). Mutation runs executed: removing the lease heartbeat fails test_b_cannot_reach_the_effect_while_a_is_inside_the_invocation and test_b_cannot_drive_a_second_effect_while_a_owns_the_unmoved_checkpoint; restoring the unconditional create() fails 7 generation tests including the pause->resume->pause->resume E2E; reverting the observation window to a fixed 30s fails 2 observation tests.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "BUGFIX",
  "role": "worker",
  "verdict": "COMPLETE",
  "source_binding": "branch os-31-durable-pause-resume, HEAD baebc475400602fa34019113505f555ea4cdfe95, worktree dirty (uncommitted fixes, tests, mirror and SKILL.md; nothing staged or committed by this worker)",
  "recorded_at": "2026-09-05T14:10:00Z",
  "boundary": "B2",
  "open_decision_item": false,
  "grounds": "Every decision was settled by the explicit requirements, the existing package contracts and repository-internal evidence. The terminal-generation retention policy was explicitly delegated to this phase by the task and is reversible in run; no boundary required user authority.",
  "scope": "The three reported PR #30 defects (resume lease fencing, repeated pause generations, observation/lease coherence), their regression tests, the source/installed mirror and the SKILL.md public contract for those three behaviours.",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "Changes the durable pause runtime and its Tier-2 store: the run-scoped lease is now renewed for the whole claimed section and fails closed on loss, a run may hold successive pause generations with the answered one retained, and the observation window covers the lease it observes. Two closed reason codes were added and the resume CLI summary gained one additive key. No existing test was weakened and the source/installed mirror stays byte-identical."
}
```
