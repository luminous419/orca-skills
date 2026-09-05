# Worker Result

STATUS: COMPLETE

**Run:** run_c2166e75bb02 · **Phase:** TEST · **Iteration:** 2 · **Role:** worker · **Round:** CORRECTION
**Supersedes:** iteration 1 of this same file (updated in place, not forked). Iteration 1 found and
reported F-001 and declared `UNIT_TEST_STATUS: BLOCKED`; iteration 2 owns the fix and turns the suite green.
**Repository:** /Users/luminous/aiAssistedProjects/orca-skills · branch `main` · HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35` · worktree dirty (tracked + untracked production/test files, uncommitted; nothing staged, no branch created, nothing pushed)
**Ticket:** Jira OS-31 — Implement Durable Pause and Resume for Human Decisions
**Read, not modified:** `ANALYSIS.md`, `PLAN.md`, `DESIGN.md`, `IMPLEMENTATION.md` and the `REVIEW_*` files of this run.
**Off-limits and untouched:** `artifacts/runs/run_8e8f9451ad44/` and every other pre-existing historical run/artifact.
**Production code:** iteration 1 changed none. **Iteration 2 changed exactly one production module**,
`scripts/deterministic_workflow/pause_runtime.py` and its byte-identical skill mirror, under an explicit
coordinator CORRECTION dispatch that assigns the F-001 fix to this round. No test file was modified,
weakened, skipped, xfailed or deleted to reach green; the three red oracles are unchanged and now pass
because the code satisfies them.

---

## Headline

The IMPLEMENTATION suite is unusually strong: on 13 of the 14 required validations the
oracles are real — they name a closed refusal code, they drop the process objects they
claim to drop, and the cross-worktree recovery really does bind the successor to a
different worktree. I could not find a second false-confidence case in it.

I found exactly one, and it is the one the phase contract predicted:

> **F-001 (blocking, now FIXED).** The documented "`discover` works without LangGraph"
> fallback did not work. The shipped CLI crashed with an unhandled `ModuleNotFoundError`
> traceback and exit code 1. Two existing tests appeared to cover it; neither exercised it.

Everything else in the required-validation list is proven, and I added 18 tests that close
the remaining oracle gaps (real SIGKILL crash windows, a real two-thread resume race, an
end-to-end conflicting response, exact refusal codes, artifact-byte immutability).

**Iteration 2 (this round) fixed F-001 in production code.** `pause_runtime` no longer
imports the checkpoint tier at module scope; it imports it at the point of use behind a
memoised `_checkpoint_store()` accessor, guarded by a `TYPE_CHECKING`-only import for the
one annotation that needs the name. Degraded `discover` therefore never touches LangGraph
and returns `CHECKPOINT_UNVERIFIED`; `resume` still fails closed with
`LANGGRAPH_DEPENDENCY_MISSING` before any claim. The three red oracles were not touched.

UNIT_TEST_STATUS: PASS

That literal is asserted only because a green run was actually observed, pasted verbatim in
§4.7: `Ran 2239 tests ... OK (skipped=6)`, exit 0. Same 2239 tests as the red run, same 6
pre-existing opt-in skips, zero failures, zero errors — the delta is entirely in the code.

---

## 1. Requirement → test traceability audit

Verdict legend — **PROVEN**: the test drives the real scenario and its oracle names the
specific outcome. **PARTIAL**: the scenario is exercised but the oracle was weaker than the
requirement; strengthened this phase. **NOT PROVEN**: the named test does not exercise the
scenario at all.

| # | Required validation (ticket "필수 검증") | Test(s) that claim it | Oracle real? | Closed this phase |
|---|---|---|---|---|
| 1 | crash immediately **before** the pause checkpoint, and restart | `CrashWindowTests.test_a_crash_before_the_pause_checkpoint_leaves_the_run_active` | **PARTIAL** — it drives the fail-closed `DISPATCH_UNACCOUNTED` *refusal*, which is an orderly terminal, not a crash; and it never restarts. `pause_node` catches `Exception` broadly, so no in-process exception can express a crash. | `CrashAroundThePauseCheckpointTests.test_a_crash_before_the_pause_checkpoint_leaves_nothing_claiming_a_pause` + `..._the_restart_after_that_crash_reaches_a_real_pause_and_resumes` — a child process is **SIGKILLed** inside `pause_node` (returncode asserted `-9`); the parent then asserts no record exists, `discover()` is empty, and a successor drives the same run to a real pause and resumes it to `COMPLETED`. |
| 2 | crash immediately **after** the pause checkpoint, and restart | `CrashWindowTests.test_a_crash_after_the_checkpoint_is_repaired_forward_and_idempotently` | **PARTIAL** — the C4 repair and its byte-identical second `reindex` are genuinely asserted, but the "crash" is `finalize_pause` simply not being called, in the same interpreter that wrote the checkpoint. | `CrashAroundThePauseCheckpointTests.test_a_crash_after_the_checkpoint_is_repaired_forward_by_a_fresh_process` — the writer is SIGKILLed between `graph.invoke` returning `WAITING_FOR_INPUT` and `finalize_pause`; a different process then repairs, re-repairs idempotently, and resumes to `COMPLETED`. |
| 3 | duplicate / identical response re-submission | `ResumeTests.test_a_replayed_response_creates_no_second_effect_and_no_second_log_pair` | **PROVEN** — asserts `status == NO_EFFECT`, `code == RUN_ALREADY_RESUMED`, `effect_count == 0`, a byte-identical artifact digest map, and `len(applied) == 1`. Also `AuditAndTimingEvidenceTests.test_a_resume_records_exactly_one_run_resumed_row_and_a_replay_records_none`. | — |
| 4 | concurrent resume race (two claimants) | `ResumeTests.test_a_concurrent_resume_race_produces_exactly_one_winner_and_no_effect_loser` | **PARTIAL** — the fence is real (`PauseClaimHeld` is raised, loser `effect_count == 0`), but it is sequential: one claimant takes the lease, then the other is asked. The final oracle is `assertIn(status, ("REFUSED", "NO_EFFECT"))`. | `ThreadedResumeRaceTests.test_two_real_threads_produce_exactly_one_winner_and_one_effect_owner` — two real threads released from a `Barrier`; asserts exactly one `RESUMED`, the loser's code is in the named closed set, **the summed `effect_count` across both adapters is 3** (one round of effects for the whole race), and `len(applied) == 1`. Passes. |
| 5 | stale checkpoint | `ResumeTests.test_a_stale_checkpoint_head_refuses_the_resume_and_performs_no_effect`, `..._update_pointer_under_the_claim_makes_a_stale_record_resumable_again`, `CrashWindowTests.test_the_asymmetry_between_c1_and_c4_is_asserted_directly`, `DiscoveryAndDegradedModeTests.test_a_broken_checkpoint_is_reported_as_unresumable_not_as_resumable` | **PARTIAL** — real refusal and `effect_count == 0`, but the code oracle is `assertIn(code, ("STALE_CHECKPOINT_HEAD", "PAUSE_CHECKPOINT_MISSING"))`. | `ExactRefusalCodeTests.test_a_moved_head_pointer_refuses_with_exactly_stale_checkpoint_head` adds the missing half: whichever of the two fires, the run is left **exactly as found** — status `WAITING_FOR_INPUT`, `applied == {}`, and the lease provably **lapsed** (a fresh owner's `claim` returns `RESUMED`), so a refusal cannot leak a lease. |
| 6 | stale response | `ResumeTests.test_a_stale_response_revision_is_refused_and_never_applied` | **PROVEN** — driven through OS-30's own reclarification path (an ambiguous free-text answer really does republish at revision+1); asserts `RESPONSE_STALE_REVISION` and `effect_count == 0`. | — |
| 7 | changed source / policy / artifact | `StaleSourceRevalidationTests` (3 cases: moved head, changed policy digest, unchanged control) | **PROVEN** — the moved-head case asserts `revalidation_codes == ("STALE_SOURCE_BINDING",)`, `binding_generation >= 1` and that the responsible phase's floor is **raised to** that generation; the control asserts generation `0` and floor `{}`, so the positive result is not vacuous. | — |
| 8 | conflicting response | `ResumeTests.test_a_conflicting_response_is_refused_and_never_arbitrated_by_recency`, `MultiItemBundleTests.test_a_differing_answer_after_the_bundle_resumed_is_a_conflict` | **PARTIAL** — the second is a real store-level conflict (`record_applied` refuses a differing bundle with `RESPONSE_CONFLICT` and `applied` stays at 1). The first is a unit test of `read_decision_bundle` against a **hand-written stub port that raises `LineageFork` on command**; it never reaches `resume_run` and never asserts that no effect was performed. The ticket asks for an end-to-end regression. | `ConflictingResponseResumeTests` — three real submissions through the real `ArtifactHumanApprovalPort`, then the on-disk lineage is forked into the shape two racing writers leave (two supersessions out of one decision, with the event content hash **recomputed** so OS-30 refuses it for the conflict and not for a broken digest). Asserts `resume_run` → `REFUSED` / `RESPONSE_CONFLICT`, `effect_count == 0`, `applied == {}`, status still `WAITING_FOR_INPUT`, every pre-existing artifact byte unchanged, one `run_resume_refused` audit row naming `RESPONSE_CONFLICT`, plus an **unforked control** that resumes normally so the refusal is attributable to the fork. |
| 9 | orphan task / dispatch | `TerminalOwnershipTests.test_an_unsettled_dispatch_is_recovered_and_recorded_recovered_never_settled`, `..._a_w_c_row_refuses_the_pause_as_a_possible_orphan`, `CancelTests`/`AbandonTests` residual cases | **PROVEN** — asserts the named recovery verb actually issued (`("worker-abandon", "intent_1") in lifecycle_commands`), `settlement == "recovered"` (never `"settled"`), `recovery` prefixed `abandon:`, and that the role is **not** promoted. | — |
| 10 | terminal ownership leak | `TerminalOwnershipTests` (11 cases), `TerminalDispositionTests` (incl. the total cross-product property), `HandleResolutionTableTests` (9 cases + a cross-product property) | **PROVEN** — every non-discharging cell raises the specific closed code (`TERMINAL_IDENTITY_UNVERIFIED`, `TERMINAL_ORPHAN_POSSIBLE`, `TERMINAL_OWNERSHIP_UNKNOWN`, `DISPATCH_UNACCOUNTED`), and the negative twins (`test_the_observed_live_receipt_is_not_a_release`, `..._a_release_receipt_that_proves_no_termination_is_not_a_release`) rule out the "recording it discharges it" failure. | — |
| 11 | artifact duplicate creation / existing-artifact overwrite | `ArtifactImmutabilityTests` (2 cases), and the digest snapshot inside the replay test | **PARTIAL** — a replayed *pause* is asserted byte-identical and exactly one request directory exists, but no test asserted immutability across the **resume** and **cancel** exits over the whole tree. | `ArtifactImmutabilityAcrossDispositionTests` — full-tree digest snapshot before/after a successful resume and before/after a cancel; every pre-existing file must still exist and still have identical bytes (deletion and rewrite are separately named), plus "a resume publishes no second request directory". All pass. |
| 12 | cancel and abandon | `CancelTests` (7), `AbandonTests` (7), `DispositionArbitrationTests` (3), `DispositionVocabularyTests` (3), `AuditAndTimingEvidenceTests` (6) | **PROVEN** — including the two properties that matter most: `residual` completes the abandon but `ac1_discharged` is **false** and the residual terminal is enumerated by title/task/candidate handle in the `run_end` reason; and `test_no_engine_module_derives_a_disposition_from_a_timeout` forbids the out-of-scope behaviour by source inspection. | — |
| 13 | Orca 1.4.196 compatibility regression | `OrcaVersionCompatibilityTests` (2), plus the pre-existing `test_orca_runtime*.py` 1.4.196 contract suites, plus `test_os31_orca_adapter_contract` running the **real** `OrcaAdapter` + `OrcaRuntimeHarness` with only `_exec_orca` stubbed | **PROVEN, offline only** — asserts the pin is still `("1.4.196",)` and that `validate_orca_contract("1.4.197", …)` still raises. See §5 for the live-runtime leg, which genuinely cannot be produced here. | — |
| 14 | existing fallback behaviour with **no LangGraph** | `ImportIsolationTests` (2, subprocess with the import blocked), `DiscoveryAndDegradedModeTests.test_discovery_without_langgraph_never_claims_the_pause_is_fine` | **NOT PROVEN — see F-001.** The two tests cover the claim in two ways that both stop short of it. `ImportIsolationTests` blocks the import for real but calls `pause_store.discover_paused_runs`, which never touches the checkpoint tier. `DiscoveryAndDegradedModeTests` calls `pause_runtime.discover(..., langgraph_available=False)` **inside an interpreter where LangGraph is importable**, so the flag is simulated and the import never happens. Neither imports `pause_runtime` with LangGraph absent — which is exactly what the CLI's `discover` verb does. | `NoLangGraphImportContractTests` (3) and `ShippedCliWithoutLangGraphTests` (3). **3 of these 6 fail against the current code and that is the finding.** |
| 15 | full test suite, skill validation | see §4 | **PROVEN** | — |
| 16 | package / archive and source-installed parity | `test_release_package.py` (14), plus the release validators run directly | **PROVEN** — and independently re-verified end to end in §4: the archive is byte-reproducible across two builds, extracts to a tree byte-identical to `scripts/deterministic_workflow`, and the **shipped CLI run from the extracted archive** completes the canonical five-phase workflow. | — |

---

## 2. Finding

### F-001 — BLOCKING — the documented no-LangGraph `discover` fallback crashes

**Gate:** G1 (explicit requirement — the ticket's 필수 검증 item "LangGraph 의존성이 없는 환경의
기존 fallback 동작", and OS-31's runtime-neutrality requirement) and G2 (result does not work).

**What is documented.** Three places state the same contract:

- `INSTALL.md:284-285` — "`discover` lists every paused run under an artifact base and is
  read-only; without LangGraph it still works but reports every verdict as
  `CHECKPOINT_UNVERIFIED`, never `RESUMABLE`."
- `orca-worker-reviewer-orchestration/SKILL.md:2455-2456` — the same sentence in Korean.
- `launcher.run_pause_cli`'s own docstring — "``discover`` works with no LangGraph".

**What actually happens.** `launcher.run_pause_cli` reaches `from . import pause_runtime`
(`launcher.py:292`) on the `discover` branch. `pause_runtime.py:24` imports
`.checkpoint_store` at module scope, and `checkpoint_store.py:27` imports
`langgraph.checkpoint.base` at module scope. With LangGraph absent the import chain raises
and nothing catches it.

Observed, on the **installed** package extracted from the release archive and again in the
repository tree, with `langgraph` hidden by a `sys.meta_path` finder that raises the real
`ModuleNotFoundError`:

```
$ PYTHONPATH=<blocker> python3 orca-worker-reviewer-orchestration/tools/run_workflow.py \
      discover --artifact-base <base with one paused run> --json
Traceback (most recent call last):
  ...
  File ".../deterministic_workflow/launcher.py", line 292, in run_pause_cli
    from . import pause_runtime
  File ".../deterministic_workflow/pause_runtime.py", line 24, in <module>
    from .checkpoint_store import (CheckpointStoreError, CheckpointThreadRetired,
  File ".../deterministic_workflow/checkpoint_store.py", line 27, in <module>
    from langgraph.checkpoint.base import BaseCheckpointSaver, ChannelVersions, CheckpointTuple
ModuleNotFoundError: No module named 'langgraph'
EXIT=1
```

The same command with LangGraph present returns exit `0` and
`"verdict": "RESUMABLE"`, so the run and the artifact base are fine; only the fallback is
broken.

**Why the existing tests do not catch it.** Exactly the false-confidence shape this phase
was asked to hunt for — two tests that name the scenario and neither exercises it:

| test | what it actually does | what it misses |
|---|---|---|
| `ImportIsolationTests.test_discover_works_with_no_langgraph_and_never_reports_resumable` | genuinely blocks the import in a child process, then calls `pause_store.discover_paused_runs(base)` | `pause_store` has no checkpoint dependency at all; it is not the module the CLI imports |
| `DiscoveryAndDegradedModeTests.test_discovery_without_langgraph_never_claims_the_pause_is_fine` | calls `pause_runtime.discover(base, langgraph_available=False)` | runs in an interpreter where LangGraph **is** importable, so the module-level import chain that fails in production is never taken |

**Blast radius.** The `resume` verb's half of the same documented sentence is correct: it
refuses with `LANGGRAPH_DEPENDENCY_MISSING`, exit `3`, no traceback, and I verified on disk
that the pause record is left `WAITING_FOR_INPUT` with `owner_id == ""` and `applied == {}`
— **no claim is taken**. Nothing durable is corrupted by F-001; the failure is that the one
read-only degraded-mode capability the design promises an operator is unavailable, and it
fails with a stack trace rather than a named error.

**Iteration 1 did not fix it, deliberately.** The TEST template's Mandatory Invariant is
that a production defect found in this phase is reported as a finding, not repaired in
production code. Iteration 1 left `scripts/deterministic_workflow/pause_runtime.py` and its
skill mirror untouched and handed the finding back with three red regression tests.

**Responsible phase:** IMPLEMENTATION — reassigned to TEST iteration 2 by explicit
coordinator CORRECTION dispatch ("this correction round owns the fix"), which is what makes
the production edit below in-contract rather than a Mandatory-Invariant violation.

### F-001 — RESOLVED (iteration 2)

**Root cause.** One module-scope import, three modules deep:

```
launcher.py:292        from . import pause_runtime          # the discover branch
pause_runtime.py:24    from .checkpoint_store import (...)  # MODULE SCOPE  <- the defect
checkpoint_store.py:27 from langgraph.checkpoint.base import BaseCheckpointSaver, ...
```

`checkpoint_store` cannot avoid its LangGraph import — `FileCheckpointSaver` **subclasses**
`BaseCheckpointSaver`, and that base class is the whole point of the Tier-1 store. So the
import that had to move is the one in `pause_runtime`, which is the module the documented
degraded path actually reaches.

**The fix (`scripts/deterministic_workflow/pause_runtime.py`, mirrored byte-for-byte).**

1. The module-scope `from .checkpoint_store import (CheckpointStoreError,
   CheckpointThreadRetired, FileCheckpointSaver)` is deleted.
2. A memoised accessor replaces it:

   ```python
   _CHECKPOINT_STORE: Any = None


   def _checkpoint_store() -> Any:
       global _CHECKPOINT_STORE
       if _CHECKPOINT_STORE is None:
           from . import checkpoint_store

           _CHECKPOINT_STORE = checkpoint_store
       return _CHECKPOINT_STORE
   ```

3. The five use sites become `_checkpoint_store().<Name>`:
   `open_saver` (constructs the saver), `reindex` (constructs the saver), `assert_c2`,
   `discover`'s C1/C2 guard, and `dispose`'s retired-thread guard. The three that appear in
   `except` clauses are safe precisely because an `except` expression is evaluated only when
   an exception is actually raised — and every one of those paths has already opened a real
   saver, so LangGraph is present by construction.
4. The one annotation that still needs the name (`open_saver -> FileCheckpointSaver`) is
   served by `if TYPE_CHECKING: from .checkpoint_store import FileCheckpointSaver` plus a
   string annotation. `from __future__ import annotations` was already in force, so nothing
   is evaluated at run time.

**What the fix deliberately does not do.**

| constraint from the correction dispatch | how it is honoured |
|---|---|
| degraded `discover` must complete, not traceback | `discover`'s `if not langgraph_available:` branch `continue`s before `open_saver`, so `_checkpoint_store()` is never called on that path. Observed: exit `0`, `CHECKPOINT_UNVERIFIED`. |
| `resume` must stay fail-closed | Untouched. `run_pause_cli` still calls `require_runtime()` on the non-`discover` branch and returns `USAGE_EXIT_CODE`; observed exit `3`, `LANGGRAPH_DEPENDENCY_MISSING`, no traceback, record still `WAITING_FOR_INPUT` with `owner_id == ""`. |
| LangGraph must not become a hard dependency | Nothing was added to any requirements file; the import moved, it did not multiply. |
| the real checkpointer must survive | `checkpoint_store.py` is unmodified. Every checkpoint read/write still goes through `FileCheckpointSaver`. PLAN F-001 (checkpointed `WorkflowState` is authoritative when LangGraph is available) is unchanged — the control leg still reports `RESUMABLE`. |
| byte parity with the skill mirror | `diff -r` clean; see §4.7. |
| the three oracles must pass unchanged | `scripts/test_os31_gap_regressions.py` is byte-identical to iteration 1. |

---

## 3. Added / Modified Tests

**Added — 1 file, 18 tests: `scripts/test_os31_gap_regressions.py`.** Nothing existing was
deleted, skipped, weakened, or edited; every case is additive, and each class documents in
its docstring which existing test it complements and why that test stops short.

| class | tests | closes |
|---|---|---|
| `NoLangGraphImportContractTests` | 3 | F-001 at the module level: `pause_runtime` must import, and `pause_runtime.discover` must run, with LangGraph absent. Includes a **control** (`test_the_blocker_really_hides_langgraph`) so the class cannot pass vacuously. **2 fail.** |
| `ShippedCliWithoutLangGraphTests` | 3 | F-001 through the shipped `run_workflow.py`, over a real paused run: `discover` with LangGraph (control, passes), `discover` without (**fails**), and `resume` without — which passes and additionally asserts the record is left unclaimed. |
| `ConflictingResponseResumeTests` | 3 | required validation 8, end to end, with an unforked control. |
| `CrashAroundThePauseCheckpointTests` | 3 | required validations 1 and 2 as real SIGKILL process deaths plus a restart by a different process. |
| `ThreadedResumeRaceTests` | 1 | required validation 4 as a real two-thread race with a summed-effect oracle. |
| `ExactRefusalCodeTests` | 2 | the two places the existing suite accepts a set of outcomes; adds exact codes and a no-lease-leak assertion. |
| `ArtifactImmutabilityAcrossDispositionTests` | 3 | required validation 11 across the resume and cancel exits, over the whole clarification tree. |

Two notes on oracles I deliberately did **not** tighten, having checked the code:

- `ResumeTests.test_a_stale_checkpoint_head_...` accepting either `STALE_CHECKPOINT_HEAD` or
  `PAUSE_CHECKPOINT_MISSING` is defensible: which one fires depends on whether the forged
  pointer names an id the store still holds. I added the invariant that holds **either way**
  (the run is left exactly as found, lease lapsed) rather than over-pinning a code.
- The race loser's code is `PAUSE_OBSERVATION_TIMEOUT`, not `PAUSE_CLAIM_HELD`, because
  `takeover` observes a live incumbent for an explicit finite window before refusing. My
  test pins that exact code and names why; the existing loose `assertIn` is not wrong.

---

## 4. Execution — actual observed output

### 4.1 Baseline re-established before any change (coordinator's numbers reproduced)

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 2221 tests in 381.812s

OK (skipped=6)
EXIT=0
```

### 4.2 Full suite with this phase's tests added

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 2239 tests in 391.873s

FAILED (failures=3, skipped=6)
EXIT=1

FAIL: test_os31_gap_regressions.NoLangGraphImportContractTests.test_pause_runtime_imports_without_langgraph
FAIL: test_os31_gap_regressions.NoLangGraphImportContractTests.test_pause_runtime_discover_runs_without_langgraph
FAIL: test_os31_gap_regressions.ShippedCliWithoutLangGraphTests.test_discover_without_langgraph_still_works_and_degrades_the_verdict
```

2221 -> 2239 tests: **+18**, all from `scripts/test_os31_gap_regressions.py`. Skips
unchanged at **6** (the same pre-existing opt-in `requires --orca-runtime` cases). **Every
one of the 2221 pre-existing tests still passes** — the three failures are all new, all in
the file this phase added, and all three are F-001. No pre-existing test was deleted,
skipped, weakened, or edited by this phase.

Run in isolation the new file reports `Ran 18 tests ... FAILED (failures=3)`: 15 pass
(including both controls, the real-SIGKILL crash windows, the two-thread race, the
end-to-end conflict with its unforked control, the exact refusal codes and the
artifact-byte immutability cases) and 3 fail for the single identified cause.

### 4.3 Skill validation, graph docs, engine parity

```
$ python3 scripts/validate_skills.py
Skill validation PASSED (737 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
EXIT=0

$ python3 scripts/validate_workflow_graph_docs.py
Workflow graph documentation validation PASSED
EXIT=0

$ diff -r scripts/deterministic_workflow \
       orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__
EXIT=0            (no output: the trees are byte-identical)
```

All three are unchanged from the coordinator's start-of-phase baseline. This phase touched
no engine file, so parity cannot have moved; it is reported because it was asked for.

### 4.4 Package / archive / source-installed parity

```
$ python3 -c "import release_manifest as rm; rm.verify_source_tree(rm.REPO_ROOT)"
SOURCE_TREE_OK files=257 version=0.9.0        (256 before this phase's one new test file)

$ python3 scripts/build_release.py --output <scratch>/orca-skills.tar.gz
Built reproducible release archive: <scratch>/orca-skills.tar.gz
BUILD_EXIT=0

$ python3 scripts/verify_package.py --archive <scratch>/orca-skills.tar.gz
Package verification PASSED (256 source files)
VERIFY_EXIT=0

$ python3 scripts/build_release.py --output <scratch>/orca-skills-2.tar.gz   # reproducibility
$ shasum -a 256 <both archives> | awk '{print $1}' | sort -u | wc -l
1                                              (byte-identical across two builds)
6f467b06aee22f2397bfcbae7a34eb4f3ee506a0eb79e5764e34a7c2921919ab  orca-skills.tar.gz

$ python3 -m unittest discover -s scripts -p 'test_release_package.py'
Ran 14 tests in 3.9s
OK
```

Source-installed parity, exercised rather than asserted — the archive was extracted and the
**shipped** CLI was run from the extracted tree, with no access to the repository's
`scripts/`:

```
$ tar -xzf orca-skills.tar.gz -C <scratch>/inst
$ diff -r <inst>/orca-worker-reviewer-orchestration/tools/deterministic_workflow \
          scripts/deterministic_workflow -x __pycache__
INSTALLED_ENGINE_PARITY_OK          (no output)

$ cd <inst> && ORCA_OS40_RUNTIME_STATE_DIR=… ORCA_OS40_CHECKPOINT_DIR=… \
    python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo --json
{'exit_code': 0, 'terminal_status': 'COMPLETED', 'run_lifecycle': 'SETTLED', 'trace_length': 68}
CLI_EXIT=0
   → left run_demo__demo.checkpoints.json beside run_demo__demo.json
```

The archive count says 256 because the archive above was built before the new test file was
added; `verify_source_tree` re-run afterwards reports 257 and still passes, so the manifest
accepts the addition with no edit.

### 4.5 The no-LangGraph path, actually exercised (phase contract item 3)

LangGraph 0.2.76 **is** installed here, so a test that merely runs proves nothing. Every
result below comes from a child process where `import langgraph` genuinely raises
`ModuleNotFoundError`, injected by a `sys.meta_path` finder (not an `__import__` hook — this
is what an absent distribution actually looks like), with the blocker's own effectiveness
asserted as a control.

| command (shipped CLI, from the extracted archive) | observed |
|---|---|
| `import langgraph` (control) | `ModuleNotFoundError: No module named 'langgraph'` — the blocker works |
| `run_workflow.py --check-runtime` | `run_workflow: LANGGRAPH_DEPENDENCY_MISSING: install requirements-langgraph.txt`, exit **3** — correct, named, no traceback |
| `run_workflow.py --demo --json` | same named refusal, exit **3** — correct; `INSTALL.md`'s "it does not use the prompt loop as a fallback" holds |
| `run_workflow.py resume --run-id … --json` | `LANGGRAPH_DEPENDENCY_MISSING`, exit **3**, no traceback; and on disk afterwards `status=WAITING_FOR_INPUT owner_id='' applied={}` — **refused before any claim**, exactly as documented |
| `run_workflow.py discover --artifact-base … --json` | **`ModuleNotFoundError` traceback, exit 1** — F-001 |
| `pause_policy` / `pause_store` / `durable_store` import and function | yes (independently confirms `ImportIsolationTests`) |
| `checkpoint_store` import | raises `ImportError`, as designed |
| `pause_runtime` import | **raises `ModuleNotFoundError`** — the root cause of F-001 |

### 4.6 Negative properties, verified in code and not only in the design text (item 4)

| property | how verified |
|---|---|
| resume cannot bypass a phase Reviewer or the Final Adversarial Review | Three independent mechanisms, all read in source: (a) `graph.PROTECTED_STATE_FIELDS` refuses `terminal_status`, `terminal_reason`, `run_lifecycle`, `pause_binding`, `phase_passes`, `phase_pass_floor`, `binding_generation` and the processed-id lists through the raw `update_state` ingress, so a resume is expressible only through the typed `RESUME_PAUSE` command; (b) that command's monotonicity guard refuses a generation or floor that would **decrease**, so a resume can only make completion harder; (c) `terminal_node` calls `verify_final_review_binding` when stamping `COMPLETED` and otherwise stamps `BLOCKED`/`NO_FINAL_REVIEW_PASS`. Behaviourally confirmed by `GatePreservationTests` — the resumed run's `PREPARE_INTENT` roles are exactly `["WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"]`. |
| an unresolvable worktree selector (`ok:true` + empty array) is never success | `orca_adapter.recover_handle` only calls `resolve_worktree` when the listing is empty and requires the echoed `id` to equal the recorded one byte-for-byte; anything else sets `scope_resolved=False`, and `pause_policy.resolve_terminal_handle` then returns `scope_unresolved`, which `UNRECOVERED_HANDLE_REFUSALS` maps to `DISPATCH_UNACCOUNTED`. Asserted by `FreshProcessRecoveryTests.test_an_unresolvable_scope_is_unknown_never_empty` **and** its sharper twin `..._a_scope_that_echoes_a_different_id_is_refused_the_same_way`, which is what rules out an `ok`-only check. |
| `residual` never sets `ac1_discharged` | `AC1_DISCHARGING_DISPOSITIONS = frozenset({"released", "exited", "retained_by_named_owner"})` excludes `"residual"`, and `ac1_discharged(rows)` is a computed `all(...)` over the rows rather than a stored assertion. `AbandonTests.test_a_residual_row_completes_the_abandon_but_never_claims_ac1` asserts both the outcome and the persisted record. |
| no `"transferred"` disposition exists anywhere | `TERMINAL_DISPOSITIONS = ("released", "exited", "retained_by_named_owner", "residual")`. A repository-wide grep for `transferred` outside tests returns only prose that says the value does not exist (a comment in `pause_policy.py:65`, a human-facing sentence in `pause_runtime.py:652`, `SKILL.md:2438`, `docs/DETERMINISTIC_WORKFLOW.md:203`) — no code path, no vocabulary member. `AbandonTests.test_a_residual_row_is_never_labelled_transferred_and_never_names_an_actor` additionally forbids any `actor:` owner string in `pause_policy` and `executor` by source inspection. |

### 4.7 Iteration 2 — the fix, and the green run

**The three previously-red tests, now green, oracles unchanged:**

```
$ python3 -m unittest scripts.test_os31_gap_regressions -v
...
test_pause_runtime_discover_runs_without_langgraph (...NoLangGraphImportContractTests...) ... ok
test_pause_runtime_imports_without_langgraph (...NoLangGraphImportContractTests...) ... ok
test_the_blocker_really_hides_langgraph (...NoLangGraphImportContractTests...) ... ok
test_discover_with_langgraph_reports_the_run_as_resumable (...ShippedCliWithoutLangGraphTests...) ... ok
test_discover_without_langgraph_still_works_and_degrades_the_verdict (...ShippedCliWithoutLangGraphTests...) ... ok
test_resume_without_langgraph_refuses_by_name_and_takes_no_claim (...ShippedCliWithoutLangGraphTests...) ... ok
...
----------------------------------------------------------------------
Ran 18 tests in 5.605s

OK
```

**Full suite:**

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 2239 tests in 396.518s

OK (skipped=6)
EXIT=0
```

2239 tests, identical to the red run's count: **no test was added, removed, skipped or
xfailed to reach green**. Skips unchanged at 6 (the pre-existing opt-in
`requires --orca-runtime` cases).

**Validators and parity:**

```
$ python3 scripts/validate_skills.py
Skill validation PASSED (737 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
validate_skills EXIT=0

$ python3 scripts/validate_workflow_graph_docs.py
Workflow graph documentation validation PASSED
validate_graph_docs EXIT=0

$ python3 scripts/verify_package.py
Package verification PASSED (257 source files)
verify_package EXIT=0

$ diff -r scripts/deterministic_workflow \
       orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__
BYTE_PARITY_OK        # no output, exit 0
```

`scripts/test_release_package.py` (14 tests) runs inside the `discover -s scripts` run above
and is part of its `OK`.

**The fallback demonstrated directly, outside the test suite.** A real paused run
(`run_pause`, `WAITING_FOR_INPUT`, all responses submitted), driven through the SHIPPED
`orca-worker-reviewer-orchestration/tools/run_workflow.py`, in child processes where
`langgraph` is hidden by a `sys.meta_path` finder that raises the genuine
`ModuleNotFoundError`. Verbatim:

```
===== CONTROL: discover WITH LangGraph =====
$ python3 orca-worker-reviewer-orchestration/tools/run_workflow.py discover --artifact-base <base> --json
exit: 0
stdout: [{"checkpoint_id": "1f1a9270-a47b-6670-8004-6e8556ccc19e", ..., "detail": "",
          "run_id": "run_pause", "status": "WAITING_FOR_INPUT", "verdict": "RESUMABLE"}]

===== discover WITHOUT LangGraph =====
$ PYTHONPATH=<blocker> python3 orca-worker-reviewer-orchestration/tools/run_workflow.py discover --artifact-base <base> --json
exit: 0
stdout: [{"checkpoint_id": "1f1a9270-a47b-6670-8004-6e8556ccc19e", ...,
          "detail": "LangGraph is absent; C1/C2 could not be evaluated",
          "run_id": "run_pause", "status": "WAITING_FOR_INPUT", "verdict": "CHECKPOINT_UNVERIFIED"}]

===== resume WITHOUT LangGraph (fail-closed) =====
$ PYTHONPATH=<blocker> python3 orca-worker-reviewer-orchestration/tools/run_workflow.py resume --run-id run_pause --artifact-base <base> --json
exit: 3
stderr: run_workflow: LANGGRAPH_DEPENDENCY_MISSING: install requirements-langgraph.txt
```

That is exactly the sentence in `INSTALL.md:284-285` and `SKILL.md:2455-2456`, now true as
written: `discover` still works, every verdict degrades to `CHECKPOINT_UNVERIFIED` and never
`RESUMABLE`, no traceback, exit 0 — while `resume` refuses by name at exit 3.

And the module-level contract, with no test harness involved at all:

```
$ PYTHONPATH=<blocker> python3 -c "import langgraph"
ModuleNotFoundError: No module named 'langgraph'          # the control; exit=1

$ PYTHONPATH=<blocker> python3 -c "
import tempfile
from scripts.deterministic_workflow import pause_runtime
print('PAUSE_RUNTIME_IMPORT_OK')
print(pause_runtime.discover(tempfile.mkdtemp(), langgraph_available=False))
print('PAUSE_RUNTIME_DISCOVER_OK')"
PAUSE_RUNTIME_IMPORT_OK
()
PAUSE_RUNTIME_DISCOVER_OK
exit=0
```

---

## 5. Remaining gaps and what genuinely cannot be automated here

1. **Live Orca 1.4.196 runtime leg — cannot be produced on this host, and I am not marking
   it covered.** `orca --version` reports **1.4.197**, and `SUPPORTED_ORCA_APP_VERSIONS ==
   ("1.4.196",)` makes `validate_orca_contract` refuse it by design. There is no way to
   obtain a live 1.4.196 runtime from this worker, and downgrading the pin to make a test
   pass would be the opposite of the regression the ticket asks for. What *is* proven:
   the pin and its refusal are asserted; the real `OrcaAdapter` and the real
   `OrcaRuntimeHarness` are driven with only `_exec_orca` (the subprocess boundary) stubbed,
   so command grammar, argument values and call ordering are all exercised against the real
   code paths; and `test_os31_orca_adapter_contract.test_the_scripted_observations_carry_only_what_the_runtime_really_returns`
   prevents the fixture from rescuing a recovery by inventing a field the runtime does not
   return. This matches what IMPLEMENTATION reported and I confirm it rather than restate it
   as covered.
2. **Multi-host / multi-machine contention is not exercised.** The claim fence is a
   `flock`-based critical section over a shared filesystem; the two-thread race and the
   two-process crash tests cover same-host contention. Cross-host NFS-style lock semantics
   are out of reach of this suite and were not in scope.
3. **F-001's three tests are now green** and are no longer a gap. They passed unchanged
   once the checkpoint-tier import on the `discover` path was made lazy, which is exactly
   the prediction iteration 1 recorded — the oracles were never edited to accommodate the
   fix.

No other required validation is unproven.

---

## Failures / Findings

- **F-001 (blocking, G1/G2) — RESOLVED in iteration 2.** The documented no-LangGraph
  `discover` fallback crashed with an unhandled `ModuleNotFoundError`. Root cause, fix and
  the constraint-by-constraint check are in §2; the observed green evidence is in §4.7.

No other failure. No environment or flaky failure was observed, and no test oracle was
changed at any point: iteration 1's three failures were deterministic and traced to one
module-scope import, and removing that import turned all three green with the test file
byte-identical.

## Remaining Gaps

See §5. Items 1 and 2 are justified limitations, not silent omissions. Item 3 is closed.

## Review Feedback Resolution

### F-001 — MAJOR, blocking — RESOLVED

**Reviewer's required action (verbatim intent):** "Return to IMPLEMENTATION, make the
checkpoint/LangGraph dependency lazy or otherwise isolate degraded discovery, preserve the
named fail-closed behavior for resume, and rerun the unchanged regression and full suites
until all tests pass."

**What was changed.** `scripts/deterministic_workflow/pause_runtime.py` and its byte-identical
mirror `orca-worker-reviewer-orchestration/tools/deterministic_workflow/pause_runtime.py`.
The module-scope `from .checkpoint_store import (...)` is replaced by a memoised
`_checkpoint_store()` accessor called at the five points that genuinely need checkpoint
authority, plus a `TYPE_CHECKING`-only import for the single annotation. Full detail in §2,
"F-001 — RESOLVED (iteration 2)".

**Verification, all observed and none predicted:**

| the reviewer's evidence | what is observed now |
|---|---|
| targeted run: 18 tests, 3 failures at `test_os31_gap_regressions.py:108`, `:118`, `:158` | `Ran 18 tests in 5.605s / OK` — same 18 tests, same file, byte-identical oracles |
| full run: `FAILED (failures=3, skipped=6)` over 2,239 tests | ``Ran 2239 tests in 396.518s` / `OK (skipped=6)`, exit 0` over the same 2,239 tests |
| shipped `discover` exits 1 with a traceback from `checkpoint_store.py:27` | exits `0`, prints `"verdict": "CHECKPOINT_UNVERIFIED"`, no traceback (§4.7, verbatim) |
| G1 mandatory fallback validation absent | produced, both through the shipped CLI and at module level (§4.7) |
| G2 documented behaviour does not work | `INSTALL.md:284-285` / `SKILL.md:2455-2456` are now true as written, demonstrated by the control/degraded pair |
| G5 green validation evidence absent | `UNIT_TEST_STATUS: PASS`, asserted from an actually-observed green run |

**Not done, and why:** the reviewer routed the fix to IMPLEMENTATION. The coordinator's
CORRECTION dispatch for this iteration instead assigns it to TEST iteration 2 ("this
correction round owns the fix", "UPDATE IN PLACE: TEST.md"). I followed the dispatch. No
`REVIEW_*.md` was read for instructions beyond the verbatim findings block the dispatch
supplied, and none was edited.

**No test was weakened.** `scripts/test_os31_gap_regressions.py` is unchanged from
iteration 1 — no deletion, no skip, no `expectedFailure`, no loosened assertion. I found no
oracle in it that I believe to be wrong, so there is nothing to report under that clause.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: Every choice this phase made was inside the phase contract. In iteration 1,
whether to repair F-001 in production code was not an open decision: the TEST template's
Mandatory Invariant settled it (report as a finding, do not fix). In iteration 2 the same
question is settled the other way by an equally explicit source — the coordinator's
CORRECTION dispatch, which states that this round owns the fix. Both are contract lookups,
not decisions, and the two do not conflict because the dispatch is the later and more
specific instruction for this round. How to make the import lazy was an implementation
choice with one obvious shape (defer the import to its use sites) and no user-facing
consequence, so no user authority was required at any point.

---

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "TEST",
  "iteration": 2,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "All observed in this iteration, none predicted. python3 -m unittest scripts.test_os31_gap_regressions -v -> Ran 18 tests in 5.605s, OK, exit 0 (the same 18 tests and the same byte-identical oracles that were red in iteration 1; three of them -- test_os31_gap_regressions.py:108, :118 and :158 -- now pass because the production code was fixed, not because any test was weakened, skipped, xfailed or deleted). python3 -m unittest discover -s scripts -p 'test_*.py' -> Ran 2239 tests in 396.518s, OK (skipped=6), exit 0 (iteration 1's red run was Ran 2239 tests in 391.873s, FAILED (failures=3, skipped=6), exit 1 over the identical 2239-test set; the skip count is unchanged at 6). python3 scripts/validate_skills.py -> Skill validation PASSED (737 checks), exit 0. python3 scripts/validate_workflow_graph_docs.py -> Workflow graph documentation validation PASSED, exit 0. python3 scripts/verify_package.py -> Package verification PASSED (257 source files), exit 0. scripts/test_release_package.py runs inside the discover run above and is part of its OK. diff -r scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__ -> no output, exit 0 (byte parity preserved for the mirrored fix). Fallback demonstrated directly outside the suite, over a real WAITING_FOR_INPUT run driven through the SHIPPED orca-worker-reviewer-orchestration/tools/run_workflow.py, with langgraph hidden in the child by a sys.meta_path finder raising the genuine ModuleNotFoundError: discover WITH LangGraph -> exit 0, verdict RESUMABLE; discover WITHOUT LangGraph -> exit 0, verdict CHECKPOINT_UNVERIFIED, detail 'LangGraph is absent; C1/C2 could not be evaluated', no traceback; resume WITHOUT LangGraph -> exit 3, stderr 'run_workflow: LANGGRAPH_DEPENDENCY_MISSING: install requirements-langgraph.txt', no traceback, no claim taken. Module level, no harness: PYTHONPATH=<blocker> python3 -c 'import langgraph' -> ModuleNotFoundError, exit 1 (the control), while importing scripts.deterministic_workflow.pause_runtime and calling pause_runtime.discover(tmpdir, langgraph_available=False) -> exit 0. Live Orca 1.4.196 evidence was NOT produced because the host runtime is 1.4.197, which validate_orca_contract refuses by design; that leg is reported as not produced, never as passing.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "TEST",
  "role": "worker",
  "verdict": "COMPLETE",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, worktree dirty (uncommitted tracked and untracked OS-31 files; nothing staged, no branch, nothing pushed)",
  "recorded_at": "2026-09-05T12:47:11Z",
  "boundary": "B2",
  "open_decision_item": false,
  "grounds": "No boundary required user authority. The one judgement call in iteration 1 -- whether TEST should repair the production defect it found -- was settled by the TEST role template's Mandatory Invariant (report, do not fix). In iteration 2 the same question is settled the other way by the coordinator's CORRECTION dispatch, which states in writing that this round owns the fix and names the artifact to update in place. Both are contract lookups rather than decisions, and they do not conflict: the dispatch is the later and more specific instruction for this round. How to isolate the LangGraph dependency had one obvious shape (defer the import to its use sites) with no user-facing consequence, so no user authority arose there either.",
  "scope": "The iteration-2 CORRECTION round: the F-001 production fix in scripts/deterministic_workflow/pause_runtime.py and its byte-identical mirror at orca-worker-reviewer-orchestration/tools/deterministic_workflow/pause_runtime.py, the in-place update of artifacts/runs/run_c2166e75bb02/TEST.md (including its Review Feedback Resolution entry for F-001), and the full regression, validator, packaging, byte-parity and no-LangGraph fallback evidence reported above. It carries forward iteration 1's traceability audit, oracle-strength review and the 18 tests in scripts/test_os31_gap_regressions.py, none of which were altered.",
  "classification_attempted": true,
  "policy_source": {
    "kind": "phase_contract_section",
    "role": "determines",
    "locator": "The coordinator's TEST iteration 2 CORRECTION dispatch -- 'artifact_contract: UPDATE IN PLACE artifacts/runs/run_c2166e75bb02/TEST.md; FIX: the production code defect below (this correction round owns the fix)', its HOW TO FIX items 1-5 and its ABSOLUTE CONSTRAINTS -- read together with the TEST role template's Mandatory Invariants and Result Contract at /Users/luminous/.claude/skills/orca-worker-reviewer-orchestration/templates/test.md"
  },
  "reversibility": "reversible_in_run",
  "blast_radius": "repository",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "One production module changed, mirrored byte-for-byte into the skill's tools tree: pause_runtime no longer imports .checkpoint_store at module scope but through a memoised _checkpoint_store() accessor at its five use sites, with a TYPE_CHECKING-only import serving the single surviving annotation. The behavioural effect is exactly the documented one that was missing: degraded discover now completes and reports CHECKPOINT_UNVERIFIED instead of exiting 1 with a ModuleNotFoundError traceback, so INSTALL.md:284-285 and SKILL.md:2455-2456 are true as written. Nothing else moved: checkpoint_store.py is untouched and remains the real Tier-1 saver, langgraph did not become a hard dependency, resume still fails closed with LANGGRAPH_DEPENDENCY_MISSING before any claim, and PLAN F-001 (the checkpointed WorkflowState is authoritative when LangGraph is available) is unchanged -- the control leg still reports RESUMABLE. No test file, no historical run and no other artifact was modified; the change is one file plus its mirror and is undone by restoring the module-scope import."
}
```
