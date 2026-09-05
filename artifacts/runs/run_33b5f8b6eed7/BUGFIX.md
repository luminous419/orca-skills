# Worker Result

STATUS: COMPLETE

Phase: BUGFIX · Iteration 1 · run_33b5f8b6eed7 · branch `os-31-durable-pause-resume` (PR #30)
Base commit at start: `38dfa77d486214ff27d7fd71322bd7b425f9b3b5`

## Bug / Root Cause Evidence

`discover()` and `resume_run()` asked the same question about the same durable bytes and
answered it differently.

At `38dfa77`, inside `discover()` (`scripts/deterministic_workflow/pause_runtime.py:519-521`):

```python
            assert_c1(record, saver)
            assert_c2(record, saver)
            entry["verdict"] = "RESUMABLE"
```

`continuation_evidence()` (C5) appeared nowhere in `discover()`. `resume_run()` consulted it
at line 704 inside the claimed section, deliberately **before** C2.

A run that crashed at the resume continuation boundary leaves exactly this durable state:

* the pause record is still `WAITING_FOR_INPUT`,
* it holds an in-flight bundle (`stage in (RECORDED, CONTINUING)`, i.e. claimed but not
  promoted),
* the thread's head has moved off the recorded pause checkpoint and **descends from it**.

`assert_c2()` only asks "is the head still the recorded checkpoint?". The answer for a
crashed continuation is "no", so it raised `STALE_CHECKPOINT_HEAD`, and `discover()`
recorded the exception's code as the verdict. The runs C5 was added to recover were
therefore precisely the runs discovery declared unrecoverable-stale.

Consequence, and why it is a real defect rather than a cosmetic one: a new
Coordinator/session that scans durable state finds no recovery candidate and never reaches
`resume_run()`. Recovery worked only if an operator already knew the run id and invoked the
resume path directly. That contradicts OS-31's own acceptance criteria — "새 Coordinator/
session에서 user response를 적용하고 정확히 한 번 재개할 수 있다" and "run state만으로 왜
멈췄는지, 어떤 decision을 기다리는지, 어디서 재개할지 복구할 수 있다".

Reproduced before the fix: crash a resume at boundary 3 (`after_checkpoint_update`), then
call `pause_runtime.discover(base)`:

```
AssertionError: 'STALE_CHECKPOINT_HEAD' != 'PAUSE_CONTINUATION_RECOVERABLE'
 : STALE_CHECKPOINT_HEAD:run_crashboundary: head='1f1a94ad-84fc-…' != record='1f1a94ad-849d-…'
```

## Fix / Modified Files

**The classification is shared, not copied.** The in-flight + lineage decision now lives in
exactly one function that both callers invoke; no second implementation exists that could
drift from C5.

`scripts/deterministic_workflow/pause_runtime.py`

* new `HeadClassification` dataclass and `classify_head(record, saver)` — the one
  classification of a pause record against its thread's durable head. It is literally the
  code lifted out of `resume_run()`'s claimed section:

  ```python
  in_flight = pause_policy.in_flight_bundle(record)
  if in_flight is not None and \
          continuation_evidence(record, saver) == CONTINUATION_COMMITTED:
      return HeadClassification(pause_policy.PAUSE_CONTINUATION_RECOVERABLE, in_flight)
  assert_c1(record, saver)
  assert_c2(record, saver)
  return HeadClassification(pause_policy.PAUSE_RESUMABLE, None)
  ```

* `resume_run()` now calls `classify_head()` and branches on its verdict, passing
  `classified.in_flight` to `_recover_continuation()`. The evaluation order (C5 before C2),
  the codes raised and the recovery path are unchanged — this is the same predicate, moved.
* `discover()` now sets `entry["verdict"] = classify_head(record, saver).verdict` inside its
  existing `except (PauseRefused, CheckpointStoreError)` block, so a refusal still becomes
  the exception's `code` and a `PauseRefused` from C5 surfaces as
  `PAUSE_CONTINUATION_UNRECOVERABLE`.

`scripts/deterministic_workflow/pause_policy.py` — the closed verdict schema (below).

`orca-worker-reviewer-orchestration/SKILL.md` — section 17: the discovery verdict table, why
the two actionable verdicts are two names, and the note on the C5 line that `discover` and
`resume` call one function rather than each implementing the rule.

`orca-worker-reviewer-orchestration/tools/deterministic_workflow/{pause_policy,pause_runtime}.py`
— byte-identical mirror (`diff -r … -x __pycache__` clean).

Tests: `scripts/test_os31_pause_fencing.py`,
`scripts/test_deterministic_workflow_pause_fixture.py` (one optional `run_id=` kwarg on
`PauseFixture.adapter`, so the end-to-end test can drive the run with the id it *discovered*
instead of the id the fixture already holds).

### Verdict vocabulary, and where it is recorded in the closed schema

Added to `pause_policy.py`, next to the existing closed vocabularies
(`PAUSE_REFUSAL_CODES`, `PAUSE_REVALIDATION_CODES`, `PAUSE_RECOVERY_CODES`):

```python
PAUSE_RESUMABLE = "RESUMABLE"                                # head IS the pause checkpoint
PAUSE_CONTINUATION_RECOVERABLE = "PAUSE_CONTINUATION_RECOVERABLE"  # validated descendant
PAUSE_DISCOVERY_ACTIONABLE_VERDICTS = frozenset({PAUSE_RESUMABLE,
                                                 PAUSE_CONTINUATION_RECOVERABLE})
PAUSE_DISCOVERY_VERDICTS = PAUSE_DISCOVERY_ACTIONABLE_VERDICTS | PAUSE_REFUSAL_CODES
```

| durable head vs. the recorded pause checkpoint | verdict | what a Coordinator then does |
|---|---|---|
| head **is** the pause checkpoint (C1+C2 hold) | `RESUMABLE` (unchanged) | run the resume from the top |
| head is a **validated descendant**, with an in-flight bundle (C5 `COMMITTED`) | `PAUSE_CONTINUATION_RECOVERABLE` (new) | finish the committed continuation from the head; never replay the re-entry |
| head is **neither** | `PAUSE_CONTINUATION_UNRECOVERABLE` | nothing — fail-closed |
| head moved, **no** in-flight bundle / digest mismatch | `STALE_CHECKPOINT_HEAD` | nothing |
| run no longer waiting, corrupt record, LangGraph absent | `RUN_ALREADY_*`, `PAUSE_RECORD_CORRUPT`, `CHECKPOINT_UNVERIFIED` | nothing |

`RESUMABLE` was **not** widened to mean both: the two lead to different work (re-drive from
the top vs. finish from the head), so a caller that acts on them differently needs two
names. `PAUSE_CONTINUATION_RECOVERABLE` is the discovery-side name whose refused counterpart
is the existing `PAUSE_CONTINUATION_UNRECOVERABLE`; every non-actionable verdict is drawn
from `PAUSE_REFUSAL_CODES`, so there is no third vocabulary source, and the two sets are
asserted disjoint by test.

### Requirement-by-requirement

1. **Same classification, shared not duplicated** — one `classify_head()`; `discover()` and
   `resume_run()` are its only callers. A regression test drives the untouched pause, the
   boundary-2 crash and the boundary-3 crash and asserts the discovery verdict equals what
   `classify_head()` returns inside the claimed section.
2. **Explicit verdicts** — three distinguished outcomes, added to the closed schema in
   `pause_policy` and documented in SKILL.md §17 (contract).
3. **A stale/unrelated head is still refused** — C2 is not a rubber stamp: the moved head
   only becomes actionable when `checkpoint_lineage()` proves the pause checkpoint is among
   its ancestors. The forked-head test asserts the verdict is
   `PAUSE_CONTINUATION_UNRECOVERABLE`, that it is *not* in the actionable set, and that the
   whole scan offers nothing.
4. **Every existing contract preserved** — exactly-once effects, lease fencing
   (`LeaseKeeper` + `PAUSE_CLAIM_LOST`), repeated pause generations
   (`PAUSE_GENERATION_ACTIVE` + superseded history), observe/lease coherence and the C5
   recovery itself: the full 2273-test suite is green and no existing test was weakened,
   skipped, xfailed or deleted. `resume_run()`'s behaviour is unchanged — the same predicate
   evaluates in the same order, only from a named function.
5. **Durable evidence only** — the head pointer and the checkpoint store's parent links,
   through the unmodified `continuation_evidence()` / `checkpoint_lineage()`. No in-memory
   state, no wall clock, no elapsed-time inference. `discover()` remains read-only: it takes
   no claim, performs no effect and moves no head (asserted).

## Regression Test

Test file: `scripts/test_os31_pause_fencing.py`
New suite: `DiscoveryContinuationTests` (+ one closed-schema case in
`ContinuationSchemaTests`). All run on the fake/in-memory adapter with **no Orca**, and are
deterministic (no sleeps; the crash is an injected exception, and every driver object is
rebuilt from the on-disk stores afterwards).

| # | Case | Before fix | After fix |
|---|---|---|---|
| 1 | `test_discovery_reports_a_boundary_3_crash_as_a_recoverable_continuation` — head ACTIVE, descendant of the pause | FAIL (`STALE_CHECKPOINT_HEAD`) | PASS |
| 2 | `test_discovery_reports_a_boundary_4_crash_as_a_recoverable_continuation` — head SETTLED, descendant of the pause | FAIL (`STALE_CHECKPOINT_HEAD`) | PASS |
| 3 | `test_discovery_refuses_a_head_that_does_not_descend_from_the_pause` — forked, non-descendant head | FAIL (`STALE_CHECKPOINT_HEAD`, i.e. right refusal for the wrong reason) | PASS (`PAUSE_CONTINUATION_UNRECOVERABLE`) |
| 4 | `test_a_fresh_coordinator_discovers_takes_over_and_resumes_exactly_once` — full discover → takeover → resume | FAIL (no actionable candidate: `len(candidates) == 0`) | PASS |
| 5 | `test_discovery_and_resume_read_the_same_classification` — discovery verdict == `classify_head()` for three durable states | FAIL | PASS |
| 6 | `test_an_untouched_pause_is_still_reported_resumable` — the fix widens nothing | PASS (guard against regression) | PASS |
| 7 | `ContinuationSchemaTests.test_the_discovery_vocabulary_is_closed_and_names_both_actionable_verdicts` | FAIL (`AttributeError`: no such vocabulary) | PASS |

Before Fix: **FAIL** — observed, by reverting only the production change (mutation 1 below):
all 6 `DiscoveryContinuationTests` failed.
After Fix: **PASS**.

### Test 4 in detail (the heart of the fix)

The reviewer's specific objection was that the crash tests call `resume_run()` directly.
Test 4 does not. It crashes a resume at boundary 3 under owner `host:dead`, then hands a
successor **only `self.base`**:

```python
candidates = self.actionable()                 # pause_runtime.discover(self.base)
self.assertEqual(len(candidates), 1)
run_id = candidates[0]["run_id"]               # the ONLY way it learns the id
```

Everything after that flows from `run_id`: the pause record it reads for the bindings, the
adapter it builds (`self.adapter(..., run_id=run_id)`), and the `resume_run()` call itself.
The successor has its own `owner_id="host:fresh"` and `observe_timeout_seconds=None`, so it
must observe the dead owner's lease lapse and **take the run over** before touching
anything. It asserts:

* `outcome.status == "RESUMED"`, `outcome.code == "PAUSE_CONTINUATION_RECOVERED"`,
  `terminal_status == "COMPLETED"`,
* `adapter.effect_count == 3` — **exactly one round of effects for the whole run** (the
  crashed attempt performed none; the recovery performs one),
* the record is `RESUMED`, its bundle stage is `RESUMED`, and `owner_id == "host:fresh"`,
* the next scan offers nothing (`actionable() == []`, verdict `RUN_ALREADY_RESUMED`), and a
  further Coordinator acting on that gets `NO_EFFECT` with `effect_count == 0`.

### The negative (forked-head) case

Test 3 crashes at boundary 3, then writes a new checkpoint whose parent is the lineage
**root** — a head that has moved exactly as a recoverable one has, but that does not carry
the pause checkpoint among its ancestors. Discovery reports
`PAUSE_CONTINUATION_UNRECOVERABLE`, the verdict is asserted *not* in
`PAUSE_DISCOVERY_ACTIONABLE_VERDICTS`, the whole scan yields no actionable run, and
discovery is confirmed read-only (owner unchanged, head unchanged).

### Mutation sensitivity — which mutation kills which test

**Mutation 1 — remove the new discovery classification.** Restore `discover()` to
`assert_c1(); assert_c2(); entry["verdict"] = "RESUMABLE"`. Observed:

```
FAILED (failures=6)   # every test in DiscoveryContinuationTests
test_discovery_reports_a_boundary_3_crash_as_a_recoverable_continuation
  AssertionError: 'STALE_CHECKPOINT_HEAD' != 'PAUSE_CONTINUATION_RECOVERABLE'
test_discovery_reports_a_boundary_4_crash_as_a_recoverable_continuation
  AssertionError: 'STALE_CHECKPOINT_HEAD' != 'PAUSE_CONTINUATION_RECOVERABLE'
```

**Mutation 2 — make the moved head a rubber stamp.** Replace the lineage guard in
`continuation_evidence()` (`if record["checkpoint_id"] not in checkpoint_lineage(...)`) with
`if False:`. Observed:

```
FAILED (failures=2)
DiscoveryContinuationTests.test_discovery_refuses_a_head_that_does_not_descend_from_the_pause
  AssertionError: 'PAUSE_CONTINUATION_RECOVERABLE' != 'PAUSE_CONTINUATION_UNRECOVERABLE'
ResumeCrashBoundaryTests.test_a_head_that_does_not_descend_from_the_pause_is_never_continued
  AssertionError: PauseRefused not raised
```

**Mutation 3 — widen `RESUMABLE` to mean both** (return `PAUSE_RESUMABLE` from the
continuation branch of `classify_head`): kills tests 1, 2 and 4, since each asserts the
distinct actionable verdict.

Both applied mutations were reverted; the working tree contains the fix only.

## Related Unit Tests / Validation

UNIT_TEST_STATUS: PASS

Actual observed output (this working tree, base `38dfa77`):

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 2273 tests in 400.050s
OK (skipped=6)

$ python3 scripts/validate_skills.py
Skill validation PASSED (737 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.

$ python3 scripts/validate_workflow_graph_docs.py
Workflow graph documentation validation PASSED

$ python3 scripts/verify_package.py
Package verification PASSED (258 source files)

$ diff -r scripts/deterministic_workflow \
       orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__
(no output — clean)
```

Baseline was 2266 tests; 2273 now — +7, and the 6 skips are unchanged (the pinned-LangGraph
guard). No existing test was weakened, skipped, xfailed or deleted.

Targeted suite: `python3 -m unittest scripts.test_os31_pause_fencing` → `Ran 34 tests … OK`.

## Review Feedback Resolution

PR #30 review at `38dfa77` — CRITICAL 0 / MAJOR 1 / MINOR 0.

* **MAJOR 1 — "A crashed continuation is recoverable only when its run ID is already known;
  normal discovery still rejects it as stale"** (inline at `pause_runtime.py:520`):
  **RESOLVED.** `discover()` applies the same in-flight + lineage classification as
  `resume_run()`, through the shared `classify_head()`. The original pause head reports
  `RESUMABLE`, a validated descendant continuation head reports
  `PAUSE_CONTINUATION_RECOVERABLE`, and an unrelated head remains
  `PAUSE_CONTINUATION_UNRECOVERABLE`. Discovery tests exist for boundary 3, boundary 4 and a
  non-descendant fork, and the full discover → takeover → resume path is exercised end to
  end by a Coordinator that learns the run id from discovery and nothing else.

Scope: no unrelated refactoring, no new features outside OS-31, no contract changes beyond
the verdict vocabulary this fix requires. No historical run directory was touched.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: The defect, the required fix and the required tests were fully specified by the PR
review and the phase contract. Two judgement calls were made inside that scope and both are
settled by the contract itself rather than by user authority: the new verdict is named
`PAUSE_CONTINUATION_RECOVERABLE` (the contract required a name consistent with the existing
closed sets, and this is the discovery-side counterpart of the existing
`PAUSE_CONTINUATION_UNRECOVERABLE`), and the shared classification was factored into
`classify_head()` (the contract explicitly puts that in scope and forbids a divergent copy).
Nothing required user input.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "run": "run_33b5f8b6eed7",
  "phase": "BUGFIX",
  "iteration": 1,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "Observed on this working tree at base 38dfa77: `python3 -m unittest discover -s scripts -p 'test_*.py'` -> Ran 2273 tests in 400.050s, OK (skipped=6) (baseline 2266, +7 new); `python3 scripts/validate_skills.py` -> Skill validation PASSED (737 checks); `python3 scripts/validate_workflow_graph_docs.py` -> Workflow graph documentation validation PASSED; `python3 scripts/verify_package.py` -> Package verification PASSED (258 source files); `diff -r scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__` -> no output (mirror byte-identical). Before-fix failure observed by reverting only the production change: all 6 DiscoveryContinuationTests failed with 'STALE_CHECKPOINT_HEAD' != 'PAUSE_CONTINUATION_RECOVERABLE'. Removing the lineage guard instead killed test_discovery_refuses_a_head_that_does_not_descend_from_the_pause and test_a_head_that_does_not_descend_from_the_pause_is_never_continued.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "BUGFIX",
  "role": "worker",
  "verdict": "COMPLETE",
  "source_binding": "branch os-31-durable-pause-resume, HEAD 38dfa77d486214ff27d7fd71322bd7b425f9b3b5, worktree dirty (uncommitted fix: 5 tracked files under scripts/ and orca-worker-reviewer-orchestration/, plus this report; the Coordinator handles git)",
  "recorded_at": "2026-09-05T17:05:01Z",
  "boundary": "B2",
  "open_decision_item": false,
  "grounds": "No boundary required user authority. The defect, the fix requirements and the required regression tests were specified by the PR #30 review and the phase contract; the only judgement calls (the new verdict's name and factoring the shared classification into classify_head) were both explicitly authorised by that contract, are reversible within the run, and carry no cost, security, privacy, compliance or lock-in consequence.",
  "scope": "Covers the BUGFIX phase of run_33b5f8b6eed7: the discovery/resume classification asymmetry in the deterministic_workflow pause subsystem, its closed verdict vocabulary, the SKILL.md section documenting it, its byte-identical mirror under orca-worker-reviewer-orchestration/tools/, and the regression tests added for it.",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "module",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "A pause run whose resume crashed at the continuation boundary is now discoverable as PAUSE_CONTINUATION_RECOVERABLE instead of being reported STALE_CHECKPOINT_HEAD, so a fresh Coordinator that scans durable state can find it, take it over and finish it exactly once without knowing the run id in advance. Behaviour of resume_run is unchanged; a head that is neither the pause nor its descendant is still refused fail-closed. The change is confined to the pause subsystem, its mirror, its tests and one SKILL.md section, and is fully reverted by dropping the uncommitted diff."
}
```
