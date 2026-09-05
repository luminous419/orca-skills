# Worker Result

STATUS: COMPLETE

**Run:** run_c2166e75bb02 · **Phase:** IMPLEMENTATION · **Iteration:** 1 · **Role:** worker · **Round:** PHASE_GATE
**Repository:** /Users/luminous/aiAssistedProjects/orca-skills · branch `main` · HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35` · worktree dirty (tracked production/test files modified, uncommitted; nothing staged, no branch created, nothing pushed)
**Ticket:** Jira OS-31 — Implement Durable Pause and Resume for Human Decisions
**Specification implemented:** `artifacts/runs/run_c2166e75bb02/DESIGN.md` (iteration 5, gated PASS with 0 blocking findings).
**Read, not modified:** `ANALYSIS.md`, `PLAN.md`, `DESIGN.md` and the `REVIEW_*` files of this run.
**Off-limits and untouched:** `artifacts/runs/run_8e8f9451ad44/` and every other pre-existing historical run/artifact.

---

## Summary / Analysis

OS-31 is implemented as the DESIGN specifies: a decision block is no longer an absorbing
terminal. When the adapter declares both `human_approval` and `lifecycle_settlement`,
`NEEDS_INPUT`/`CONFLICT` routes to a new **PAUSE** node, the graph's `TERMINAL` node writes
**no** terminal status, and the run carries `run_lifecycle = "WAITING_FOR_INPUT"` — a value
deliberately absent from `TERMINAL_STATUSES`, so there is nothing for a resume to clear.

The two durable tiers are built and the authority is unambiguous:

- **Tier 1** `checkpoint_store.FileCheckpointSaver` — an in-repository `BaseCheckpointSaver`
  over the already-pinned `langgraph-checkpoint==2.1.1` serializer (**no new pinned
  dependency**, OD-4). It keeps an explicit `head` pointer written inside the same critical
  section as the `put`, a per-`(thread, ns)` monotonic `sequence`, a payload digest, and
  thread **retirement** rather than deletion. It is the sole input to state reconstruction.
- **Tier 2** `pause_store.FilePauseRecordStore` (`.pause_state.json`) — discovery identity,
  the run-scoped claim/lease fence, the checkpoint pointer + digest, the disposition, the
  applied set and a **subordinate** `projection`. Beside it,
  `pause_store.FileSettlementJournal` (`.settlement_journal.json`) records one row per
  dispatch, every write landing strictly **before** the external effect it describes.

`pause_runtime` owns the sequencing and C1–C4. Resume is exactly-once by construction: one
complete decision bundle → one `resume_bundle_id` → one atomic applied entry written
**before** the single graph re-entry, so no partial per-item state can exist. Terminal
ownership is settled before the run may wait: every dispatch is accounted on the four axes
and must reach `released`, `exited` or `retained_by_named_owner`, or the pause is **refused**
and the run falls back to exactly where a pre-OS-31 decision block left it. There is no
`transferred` disposition; an abandon that cannot discharge a row records it `residual`,
enumerates it in the `run_end` reason with `residual_terminal_count`, and the run then does
**not** claim AC-1.

The engine owns pause/resume **policy** and stays runtime-neutral: `pause_policy` and
`pause_store` import neither LangGraph nor Orca (proven in a subprocess with `langgraph`
blocked), the §4.2.1a handle-resolution decision table is a **pure** function over an
already-fetched listing, and the adapter only translates lifecycle signals and performs I/O.

---

## Changes

### 1. Vocabulary and state machine (WU-1)

| Constant | File | Change |
|---|---|---|
| `ROUTE_TOKENS`, `RouteToken` | `contracts.py` | `+ PAUSE, CANCEL, ABANDON` |
| `TERMINAL_STATUSES` | `contracts.py` | `+ CANCELLED, ABANDONED` (**not** `WAITING_FOR_INPUT`) |
| `RUN_LIFECYCLE_STATES` *(new)* | `contracts.py` | `("ACTIVE", "WAITING_FOR_INPUT", "SETTLED")` |
| `PAUSE_CAPABILITIES`, `LIFECYCLE_SETTLEMENT` *(new)* | `contracts.py` | `{human_approval, lifecycle_settlement}`; `CAPABILITIES` extended |
| `NODES`, `STATIC_EDGES`, `ROUTE_TARGETS` | `graph_spec.py` | `+ PAUSE, DISPOSE` nodes and their `→ TERMINAL` edges; `PAUSE→PAUSE`, `CANCEL/ABANDON→DISPOSE` |
| `GRAPH_OWNED_DECISIONS` | `graph_spec.py` | `+ "PAUSE_RESUME"` (appended last; the tuple comparison is order-sensitive) |
| `RUN_STATUS_VALUES` | `run_logging.py` | `+ WAITING_FOR_INPUT, CANCELLED, ABANDONED` — one tuple edit satisfying all four enforcement points |
| `EXIT_CODES` | `launcher.py` | `+ WAITING_FOR_INPUT:4, CANCELLED:5, ABANDONED:6` |

`WorkflowState` gains exactly four fields — `run_lifecycle`, `pause_binding`,
`binding_generation`, `phase_pass_floor` — and `state._assert_lifecycle_coherence` enforces
the coherence invariant in one place, fail-closed, including the
`SETTLED ⇔ terminal_status is not None` biconditional that makes "was terminal and now is
not" unrepresentable.

### 2. Routing (WU-5/WU-8)

`routing.pause_admissible` gates the new edge on both capabilities, so **every existing
assertion that a decision block yields `BLOCK` stays green unedited** — `BASE_CAPABILITIES`
contains neither capability, exactly as DESIGN §1.4 predicted. `route()` gained two checks
above the decision axis (`requested_disposition`, then `run_lifecycle == WAITING_FOR_INPUT`)
so a disposition can leave a paused run and re-entering a paused checkpoint re-routes to
itself idempotently. `all_phase_passes_current` became a real currency check over
`phase_pass_floor`, and is a no-op (`{}` / generation `0`) for every run that never paused.

### 3. The PAUSE and DISPOSE nodes (`executor.py`)

`pause_node` performs, in order: **(0)** resolve the plaintext handle per row and refuse
before any mutating verb if it cannot be proved; **(1)** four-axis accounting with the named
recovery path (`worker-abandon` → `worker-release`, or `task-update --status failed`),
recorded `recovered`, never `settled`, with no role promotion; **(2)** the terminal
disposition exit invariant; **(3)** the OS-30 publish; **(4)** build `pause_binding`, set
`pending_clarification_id`, `run_lifecycle` and `route_token`. Four named refusals
(`DISPATCH_UNACCOUNTED`, `TERMINAL_OWNERSHIP_UNKNOWN`, `TERMINAL_ORPHAN_POSSIBLE`,
`TERMINAL_IDENTITY_UNVERIFIED`) each fall back to `BLOCK`/`BLOCKED` **carrying the refusal
code** on the terminal reason. `dispose_node` freezes the bindings — a moved head is never a
reason to refuse a cancel — and, on abandon only, discovers residual dispatches durably.

### 4. Ports, adapters and harness seams (WU-4)

`ports.py` gains `RunPauseStatePort` and `LifecycleSettlementPort`. `OrcaAdapter` implements
the settlement port and writes the journal at its five stages, each strictly before the
effect it covers:

```
PLANNED  (before `task-create`)     role/origin/intended_role/created_by, the run-unique
                                    title, and the STABLE id:<repo-id>::<path> selector
OPENED   (before `terminal create`) task_id
INTENDED (before `worker-start`)    sha256(handle) + provenance_source="journal"
ACCOUNTED / DISPOSED                the four-axis outcome, then the proof it is finished
```

`origin_worktree_selector()` reads `worktree current` — a verb the harness already executes
during contract validation — and **refuses before E1** rather than falling back to the
`current`/`active` alias. Five additive, fully defaulted harness seams make this possible:
`create_fake_terminal(title=..., worktree=...)`, `run_existing_task(terminal_observer=...,
terminal_title=..., terminal_worktree=...)`, and two new **read-only** verbs,
`list_terminals(worktree=...)` and `resolve_worktree(selector)`. Every existing call site
binds unchanged, which the contract suite asserts directly.

`FakeAdapter` implements the same port over `FileExternalWorld`, with an injectable scripted
axis outcome per dispatch and a per-selector terminal listing, so every OS-31 behaviour is
exercised with **no Orca**.

### 5. Gate preservation (WU-9)

`graph.PROTECTED_STATE_FIELDS` closes the raw `update_state` ingress against
`terminal_status`, `terminal_reason`, `run_lifecycle`, `pause_binding`, `phase_passes`,
`phase_pass_floor`, `binding_generation` and the processed-id lists. A monotonicity guard
refuses a `RESUME_PAUSE` that would lower a generation or a floor
(`MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:generation must not decrease`), so a resume can only
make completion **harder**. `terminal_node` now calls `verify_final_review_binding` when
stamping `COMPLETED`, turning a test-only helper into a production cross-check.

### 6. Cancel, abandon and the audit trail (WU-10/11/12)

`dispose_run` runs X1 (**CANCEL only** — abandon fabricates no decision), re-enters the graph
to reach the `CANCELLED`/`ABANDONED` terminal, settles the record, and **retires** the
checkpoint thread. `run_logging` gained the eight OS-31 event constants; `pause_runtime`
appends `pause_settlement_accounted` / `run_paused` / `run_resumed` / `run_resume_refused` /
`run_cancelled` / `run_abandoned` / `pause_takeover_claimed` / `pause_takeover_refused`, and
writes the **second** `run_end` with closed timing scopes and a non-blank `started_at`
recovered from `.timing_state.json`. `clarification_protocol.run_disposition` (a ~20-line
dependency-free closed-schema read, implemented *inside* that module because it imports
nothing else from `scripts/`) makes `promote_pending` a no-op for a disposed run.
`verify_full_workflow_example.py` now reads the **last** `run_end` rather than `any(...)`.

### 7. Documentation and contracts (WU-13)

Both fenced SKILL.md blocks were updated consistently; a new demoted
`## Durable Pause and Resume (OS-31)` section carries the exact `NON_AUTHORITATIVE
(graph-owned)` marker; limitations L1/L3/L6/L7 were rewritten (L2/L4/L5/L8 unchanged — **L4,
"no timeout semantics", stays true and this change adds no timeout-driven default
anywhere**); the Core Invariants gained six OS-31 sentences. `INSTALL.md` and
`docs/DETERMINISTIC_WORKFLOW.md` document the three new exit codes, the two CLI verbs and the
two tiers. `requirements-langgraph.txt` and `release_manifest.py` are unchanged, as DESIGN
§12 predicted: `required_skill_paths` enumerates the installed engine by `rglob("*.py")`, so
the five new mirrored files are manifest-covered automatically.

---

## Modified Files / Artifacts

### New engine files (each byte-mirrored into the skill in the same change)

| file | unit |
|---|---|
| `scripts/deterministic_workflow/durable_store.py` | WU-2 — flock critical section + atomic replace, LangGraph-free |
| `scripts/deterministic_workflow/pause_policy.py` | WU-3 — the pure policy half |
| `scripts/deterministic_workflow/pause_store.py` | WU-2(b) — `FilePauseRecordStore` **and** `FileSettlementJournal` |
| `scripts/deterministic_workflow/checkpoint_store.py` | WU-2(a) — Tier-1 authority |
| `scripts/deterministic_workflow/pause_runtime.py` | WU-5/6/7/8/10 — finalise, C1–C4, takeover, resume, dispose |
| `orca-worker-reviewer-orchestration/tools/deterministic_workflow/{durable_store,pause_policy,pause_store,checkpoint_store,pause_runtime}.py` | the byte-parity mirrors |

### Modified production files

```
scripts/deterministic_workflow/contracts.py       vocabulary, capabilities
scripts/deterministic_workflow/graph_spec.py      nodes, edges, route targets, decisions
scripts/deterministic_workflow/state.py           4 fields, coherence, 2 typed commands
scripts/deterministic_workflow/routing.py         pause_admissible, route, currency check
scripts/deterministic_workflow/executor.py        pause_node, dispose_node, terminal_node
scripts/deterministic_workflow/graph.py           PAUSE/DISPOSE, PROTECTED_STATE_FIELDS,
                                                  DurableCheckpointerRequired
scripts/deterministic_workflow/launcher.py        exit codes, resolve_checkpoint_path,
                                                  default saver, discover/resume verbs
scripts/deterministic_workflow/ports.py           RunPauseStatePort, LifecycleSettlementPort
scripts/deterministic_workflow/fake_adapter.py    settlement port, terminal listing
scripts/deterministic_workflow/orca_adapter.py    journal writes, recover_handle, port impl
scripts/orca_runtime_harness.py                   5 defaulted seams; WAITING_FOR_INPUT publish
scripts/run_logging.py                            RUN_STATUS_VALUES + 8 event constants
scripts/clarification_protocol.py                 run_disposition, promote_pending guard
scripts/verify_full_workflow_example.py           last-run_end-wins reader
orca-worker-reviewer-orchestration/SKILL.md       both fenced blocks, new demoted section,
                                                  limitations, core invariants
orca-worker-reviewer-orchestration/tools/deterministic_workflow/*.py   mirrors
orca-worker-reviewer-orchestration/tools/run_logging.py                mirror
orca-worker-reviewer-orchestration/tools/clarification_protocol.py     mirror
INSTALL.md, docs/DETERMINISTIC_WORKFLOW.md        exit codes, verbs, the two tiers
```

### New test files

```
scripts/test_deterministic_workflow_pause.py            (NOT LangGraph-gated -- that is V8 evidence)
scripts/test_deterministic_workflow_checkpoint.py       (gated on the pinned LangGraph)
scripts/test_deterministic_workflow_pause_fixture.py    shared offline fixture
scripts/test_deterministic_workflow_pause_e2e.py        pause/resume end to end
scripts/test_deterministic_workflow_settlement.py       journal, handle recovery, ownership
scripts/test_deterministic_workflow_cancel.py           cancel/abandon, audit, run-status
scripts/test_os31_orca_adapter_contract.py              the REAL adapter + REAL harness (F-002)
```

### Modified test files (call-site updates and vocabulary, never weakened)

```
scripts/test_deterministic_workflow_{adapters,graph,launcher,lease_keeper,malformed,
                                     ownership,recovery,round2}.py
scripts/test_os29_decision_gate.py, scripts/test_run_logging.py
```

Nothing was staged, no branch was created, nothing was pushed. No historical run or artifact
under `artifacts/` was modified; the only artifact written is this report.

---

## Unit Tests

### Added / Modified Tests

**Added — 7 new files, 178 new tests.** Every one runs offline on `FakeAdapter` or on the
real `OrcaAdapter`/`OrcaRuntimeHarness` through `OfflineHarnessTestCase`, which stubs only
`_exec_orca`. No Orca runtime and no network are used anywhere.

**Modified — call sites and vocabulary only.** The eight engine test files gained
`require_durable_checkpointer=False` on their `build_graph` calls (the design's named
test-only escape hatch, §2.5) and a `checkpoint_store_path` on their `execute_state` calls,
so the suite does not write `artifacts/runs/` into the working tree. Three assertions were
updated because OS-31 is the ticket that changes what they pin, and each keeps its original
substance:

| test | before | after |
|---|---|---|
| `test_terminal_exit_codes_cover_every_terminal_status` | 3 codes | 6 codes, all still distinct |
| `test_the_gate_adds_no_round_kind_and_no_run_status` (OS-29) | the whole tuple | the first four are still exactly OS-29's; the three after them are named as OS-31's |
| `test_the_two_sparse_columns_exist_and_stay_blank_by_default` | `len == 4` | `len == 7` |
| `test_replayed_and_malformed_events_fail_closed_at_graph_node` | hand-built terminal state | also sets `run_lifecycle="SETTLED"`; the `POST_TERMINAL_EVENT` assertion is unchanged |

No test was deleted, skipped, or had an assertion removed.

### Behavior Covered

Every regression the phase contract requires, by name:

| required regression | where |
|---|---|
| crash **before** pause | `CrashWindowTests.test_a_crash_before_the_pause_checkpoint_leaves_the_run_active`, `..._reconstructs_the_dispatch_set_from_disk_alone` |
| crash **after** pause | `CrashWindowTests.test_a_crash_after_the_checkpoint_is_repaired_forward_and_idempotently` (C4, byte-identical second reindex), `..._asymmetry_between_c1_and_c4` |
| duplicate response | `ResumeTests.test_a_replayed_response_creates_no_second_effect_and_no_second_log_pair` |
| concurrent resume race | `ResumeTests.test_a_concurrent_resume_race_produces_exactly_one_winner_and_no_effect_loser` (loser `effect_count == 0`) |
| stale checkpoint | `ResumeTests.test_a_stale_checkpoint_head_refuses_the_resume_and_performs_no_effect` + the `update_pointer` recovery twin |
| stale response | `ResumeTests.test_a_stale_response_revision_is_refused_and_never_applied` (driven through OS-30's own reclarification path) |
| conflicting response | `ResumeTests.test_a_conflicting_response_is_refused_and_never_arbitrated_by_recency`, `MultiItemBundleTests.test_a_differing_answer_after_the_bundle_resumed_is_a_conflict` |
| changed source / policy | `StaleSourceRevalidationTests` (head, policy digest, and the unchanged control) |
| orphan task / dispatch | `TerminalOwnershipTests.test_an_unsettled_dispatch_is_recovered_and_recorded_recovered_never_settled` |
| terminal ownership leak | `TerminalOwnershipTests` — 8 cases incl. the D-6/R8-iii negative twin, plus the exhaustive `TerminalDispositionTests` cross-product property |
| artifact duplication / overwrite | `ArtifactImmutabilityTests`, and the digest snapshot in the replay test |
| cancel / abandon | `CancelTests`, `AbandonTests`, `DispositionArbitrationTests` (TC-4) |
| Orca 1.4.196 compatibility | `OrcaVersionCompatibilityTests` — the tuple and the refusal, proving OS-31 changes neither |
| no-LangGraph fallback | `ImportIsolationTests` — run in a **subprocess** with `langgraph` blocked; `discover` works, `require_runtime` still refuses with `LANGGRAPH_DEPENDENCY_MISSING` |
| cross-worktree fresh-object recovery/refusal | `FreshProcessRecoveryTests` — the creator is bound to worktree **A**, the successor to **B**, and the recovery still succeeds because the recorded selector is an identity; six fail-closed twins refuse |

Property-style obligations from DESIGN §13.4 that are asserted rather than assumed: the
transition table over the whole `(state × event)` cross product; refusal ∩ revalidation
codes = ∅; the engine's three re-exported vocabularies equal the harness's;
`"transferred" ∉ TERMINAL_DISPOSITIONS` and `"residual" ∉ AC1_DISCHARGING_DISPOSITIONS`;
`terminal_disposition` total over the whole eight-axis cross product; a plaintext handle used
**only** in the one cell where a durable digest proved it; `normalize_terminal_title`
idempotent, inert on undecorated titles, and rejecting a foreign run's title; every journalled
`terminal_worktree` matching `^id:[^:]+::/` and no `--worktree` argument ever equal to
`current`/`active`; the plaintext handle absent from the checkpoint, the journal and the
ledger; `applied` never holding more than one entry; `project_pause` covering exactly
`PAUSE_PROJECTION_KEYS`; `resume_bundle_id` stable across a restart and insensitive to
bindings but sensitive to every decision; and no OS-31 path calling `delete_thread`.

### Execution

Command:
```
python3 -m unittest discover -s scripts -p 'test_*.py'
```
Result: PASS

UNIT_TEST_STATUS: PASS

---

## Additional Validation

### Full test suite (the mandatory gate)

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 2221 tests in 388.459s

OK (skipped=6)
```

Baseline was **2014 tests OK (6 skipped)**. This phase adds **207 tests** and the skip count
is **unchanged at 6** -- the same pre-existing opt-in `requires --orca-runtime` cases. No test
was deleted, skipped or weakened.

### Skill validation

```
$ python3 scripts/validate_skills.py
Skill validation PASSED (737 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

Baseline was **732 checks PASSED**; the five new mirrored engine files add five parity checks,
so the count rises to **737** and the suite stays green.

### Workflow graph documentation

```
$ python3 scripts/validate_workflow_graph_docs.py
Workflow graph documentation validation PASSED
VALIDATE_GRAPH_DOCS_EXIT=0
```

Both fenced blocks match the engine: `route_tokens` now carries `PAUSE`/`CANCEL`/`ABANDON`,
`terminal_statuses` carries `CANCELLED`/`ABANDONED`, `graph_owned_decisions` ends with
`PAUSE_RESUME` (the comparison is order-sensitive), every route token is owned by a declared
decision, the new `## Durable Pause and Resume (OS-31)` section carries the exact
`NON_AUTHORITATIVE (graph-owned)` marker, and no `skill_owned_safety` section was demoted.

### scripts/ ↔ skill tools/ byte parity

```
$ diff -r scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__
PARITY_DIFF_EXIT=0
```

(No output: the trees are byte-identical.) `run_logging.py` and `clarification_protocol.py`
were mirrored in the same change; `validate_skills.py`'s own parity checks confirm both.

### Packaging / archive / source-installed parity

```
$ python3 -m unittest discover -s scripts -p 'test_release_package.py'
Ran 13 tests in 3.882s

OK
```

`release_manifest.required_skill_paths` enumerates the installed engine by `rglob("*.py")`,
so the five new mirrored modules are manifest-covered with no edit -- as DESIGN §12 predicted
and this gate confirms.

### Orca 1.4.196 compatibility, per OD-2

`SUPPORTED_ORCA_APP_VERSIONS == ("1.4.196",)` is unchanged and `validate_orca_contract` still
refuses every other version; both are asserted in
`test_os31_orca_adapter_contract.OrcaVersionCompatibilityTests` and in the pre-existing
1.4.196 contract regressions, all green in the run above.

**No live-runtime evidence was produced, and that is reported rather than implied.** The live
runtime on this host is 1.4.197, which `validate_orca_contract` refuses by design, so the
live leg is **not produced**; every OS-31 path is exercised offline instead -- on `FakeAdapter`
and, for the F-002 property, on the real `OrcaAdapter` + `OrcaRuntimeHarness` through
`OfflineHarnessTestCase`, which stubs only the subprocess boundary.

### No-LangGraph fallback

Proven in a **subprocess** with `langgraph` blocked by an import hook, so nothing can leak
into the parent interpreter: `pause_policy`, `pause_store` and `durable_store` import and
function; `checkpoint_store` raises `ImportError`; `discover_paused_runs` works; and
`launcher.require_runtime()` still refuses with `LANGGRAPH_DEPENDENCY_MISSING`.
`INSTALL.md:254-255` -- "it does not use the prompt loop as a fallback" -- is unedited and
still true.

### Shipped command line, end to end with no Orca

```
$ ORCA_OS40_RUNTIME_STATE_DIR=<tmp> ORCA_OS40_CHECKPOINT_DIR=<tmp> \
  python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo --json
{"exit_code": 0, ..., "run_lifecycle": "SETTLED",
 "terminal_reason": {"code": "WORKFLOW_COMPLETED", ...},
 "terminal_status": "COMPLETED", "trace_length": 68, ...}

$ ls <tmp>
run_demo__demo.checkpoints.json        run_demo__demo.checkpoints.json.lock
run_demo__demo.json                    run_demo__demo.json.lock
```

The canonical five-phase workflow still completes with no Orca runtime, and it now leaves a
**durable checkpoint** beside the durable ledger with no extra flags.

---

## Review Feedback Resolution

Iteration 1: no prior IMPLEMENTATION findings exist. The three upstream findings this phase
inherits as binding constraints are implemented and asserted:

| finding | how the code satisfies it |
|---|---|
| **ANALYSIS F-001** — cancel/abandon must be a distinct durable run lifecycle path | `DISPOSE` node, `PAUSE_RECORD_STATUSES` `CANCELLED`/`ABANDONED`, `terminal_node` stamping them, `claim` returning `ALREADY_CANCELLED`/`ALREADY_ABANDONED` thereafter, and `retire_thread` keeping the checkpoint as audit evidence |
| **PLAN F-001** — the OS-40 checkpoint is authoritative; the record is index/fence/projection; a production durable checkpointer is REQUIRED | `pause_runtime.reconstruct` reads the checkpoint and **only** the checkpoint (`record["projection"]` is read solely inside `assert_c3`, proven by mutating it and asserting the reconstruction is unchanged); `FileCheckpointSaver` is installed by default and `build_graph` refuses without a durable one |
| **DESIGN F-001** — fail-closed terminal disposition exit invariant | `TERMINAL_DISPOSITIONS` has no `transferred`; `residual ∉ AC1_DISCHARGING_DISPOSITIONS`; `ac1_discharged` is **computed** by `pause_policy.ac1_discharged(rows)`, never asserted; a grep-style test forbids any `actor:` owner string in the policy module and the executor |
| **DESIGN F-002** — provenance persisted before the effect; the origin worktree resolved once to `id:<repo-id>::<path>`; fresh-process recovery narrows by title then VERIFIES by digest; an unresolvable selector returning `ok:true` with an empty array is never success | the five journal stages, `origin_worktree_selector()` refusing before E1, `resolve_terminal_handle` (pure) proving the survivor with `sha256(handle) == terminal_digest`, and the `worktree show` guard that must echo the recorded id byte-for-byte before any "absent" verdict |
| **DESIGN F-003** — one atomic bundle-level application identity, single effect owner, defined partial-write recovery | `resume_bundle_id` over the complete sorted item/decision set; `record_applied` writing **one** whole entry under the flock critical section and refusing an incomplete, over-complete or differing bundle; the `RECORDED` ladder asking the **head**, not the record, whether the effect landed |

### Deviations from DESIGN, with reasons

Three places where the letter of DESIGN could not be implemented as written. Each is a
narrower change than the design's own rationale, and none weakens a stated guarantee.

1. **`RESUME_PAUSE` and `REQUEST_DISPOSITION` additionally clear `route_token` and
   `terminal_reason`.** DESIGN §8.3 lists neither. But `executor.route_node` short-circuits
   to the recorded token whenever a terminal reason is present, so a re-entry that left the
   pause's reason standing would route straight back into `PAUSE` and the resume would be a
   no-op. Both fields are *cleared, never set* — `typed_update` refuses any non-`None` value
   with `MALFORMED_UPDATE_COMMAND:...:route_token and terminal_reason may only be cleared` —
   and both remain refused on the raw ingress via `PROTECTED_STATE_FIELDS`. §8.3's stated
   property is untouched: neither command names `worker_result`, `reviewer_result`,
   `final_reviewer_result`, `phase_passes`, `final_review_iterations` or any budget.

2. **`FileCheckpointSaver` does not treat "a checkpoint whose `channel_versions` names a
   missing blob" as corruption.** DESIGN §2.2 lists it. It is factually wrong against real
   LangGraph 0.2.76: the runtime records versions for trigger/managed channels that carry no
   value, and the reference `InMemorySaver._load_blobs` skips exactly those. Enforcing it
   refused every real checkpoint the graph writes. The other three corruption conditions
   (unknown top-level key, unknown schema version, a head naming a missing checkpoint) are
   enforced, and a fourth was added: a checkpoint whose stored `WorkflowState` schema version
   differs is refused, which is the migration rule §"Compatibility" asks for.

3. **The §4.2.1a candidate branch is keyed on "no digest exists" rather than on
   `stage == "OPENED"`.** These coincide for a live W-C row, but a row promoted to
   `DISPOSED` on the abandon path would otherwise re-read as `unverified`, which means "a
   match the digest **contradicts**" — and with no digest there is nothing to contradict.
   Keying on the fact makes the verdict stable under stage promotion and is the substantive
   discriminator the design's own prose gives ("the row never reached `INTENDED`, so **no
   digest was ever written**").

### Open issues carried to TEST

- **OI-1 (Orca CLI grammar).** Two verbs were added, both **reads**: `terminal list` and
  `worktree show`. Neither is invented — DESIGN executed both against the live 1.4.197
  runtime and recorded their grammar and response shapes — but no live-runtime evidence was
  produced *in this phase*, because `validate_orca_contract` refuses 1.4.197 and the pinned
  version is 1.4.196. Per OD-2 that leg is reported as **not produced, with the reason
  stated**, never as passing. The whole path is exercised offline.
- **OI-2 (`checkpoint_ns`).** Only `""` is exercised end to end; a non-empty namespace is
  stored and read correctly and is covered by one unit test, as designed.
- **OI-3 (medium/low-risk downstream revalidation).** Inherited engine property, asserted as
  a named limitation test rather than discovered.
- **OI-4 (checkpoint store growth).** No pruning is implemented, deliberately: pruning could
  delete the checkpoint a pause record points at.
- **W-C remains open and is enforced, not papered over.** A terminal created before its
  digest was journalled is addressable-but-unproven. The pause refuses
  (`TERMINAL_ORPHAN_POSSIBLE`), an abandon records `residual`, reports the candidate handle,
  and the run does not claim AC-1. This is asserted as behaviour in three tests.

---

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none

Every choice this phase made was decided by the approved DESIGN, by the existing repository
contracts, or by observed runtime behaviour. The three deviations above are recorded with
their evidence; none required user authority, none is irreversible, and each is narrower than
the design's own stated rationale. No boundary element needed a decision that policy could
not make.

One boundary element **did** fire, and the record says which and why it still resolves to
CLEAR. `blast_radius: "repository"` is a triggering value under the SKILL policy contract
(`boundary_elements.blast_radius.triggering == ["repository", "external_system"]`), because
this phase changes tracked production files rather than only the current change. That
element is therefore an open item under `no_open_decision_item`, so CLEAR is reached through
the contract's other entry clause instead: the gate record carries a `policy_source` with
role `determines`, locating the approved `DESIGN.md` and the dispatched IMPLEMENTATION phase
contract — which is literally true here, since the design is the specification this phase was
told to implement rather than redesign. Neither of the two things a determining policy source
cannot resolve is present: no explicit user authority is reserved, and no two explicit
requirements contradict each other.

---

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "IMPLEMENTATION",
  "iteration": 1,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "python3 -m unittest discover -s scripts -p 'test_*.py' -> Ran 2221 tests in 388.459s, OK (skipped=6), up from the 2014-test/6-skip baseline with no test deleted or weakened; python3 scripts/validate_skills.py -> Skill validation PASSED (737 checks), exit 0, up from 732 because the five new mirrored engine files add five parity checks; python3 scripts/validate_workflow_graph_docs.py -> Workflow graph documentation validation PASSED, exit 0; diff -r scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow -x __pycache__ -> no output, exit 0 (byte-identical); python3 -m unittest discover -s scripts -p 'test_release_package.py' -> Ran 13 tests, OK. All observed, none predicted. Per OD-2 no live-runtime evidence was produced because the live runtime here is 1.4.197 and validate_orca_contract refuses it; that leg is reported as not produced, never as passing.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "IMPLEMENTATION",
  "role": "worker",
  "verdict": "COMPLETE",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, worktree dirty (tracked production and test files modified, uncommitted; nothing staged, no branch created, nothing pushed)",
  "recorded_at": "2026-09-05T11:49:49Z",
  "boundary": "B2",
  "open_decision_item": false,
  "grounds": "Every choice was determined by the approved DESIGN, by an existing repository contract, or by observed runtime behaviour. The three recorded deviations were each forced by verified behaviour of the installed LangGraph runtime or of the engine's own existing routing code, are narrower than the design's stated rationale, and preserve every guarantee the design names. No boundary element was reached that policy could not decide.",
  "scope": "The OS-31 durable pause and resume implementation in this repository: five new engine modules and their byte-parity mirrors, the modified engine/adapter/harness/logging production files, the two fenced SKILL.md contract blocks and the new demoted section, INSTALL.md and docs/DETERMINISTIC_WORKFLOW.md, seven new test files and the enumerated test call-site updates.",
  "classification_attempted": true,
  "policy_source": {"kind": "phase_contract_section", "role": "determines", "locator": "artifacts/runs/run_c2166e75bb02/DESIGN.md - Proposed Design SS1-SS13, Expected Changed Files / Implementation Steps, and Testing Strategy; plus the dispatched IMPLEMENTATION phase contract"},
  "reversibility": "reversible_in_run",
  "blast_radius": "repository",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "Tracked production files across the deterministic workflow engine, the Orca adapter and harness, run logging, the clarification protocol and the orchestration Skill document were modified, and five engine modules plus their mirrors were added. Everything is uncommitted and git-tracked, so the whole change is revertible within this run. No branch was created, nothing was staged or pushed, no historical run or artifact was touched, no new pinned dependency was introduced, and the no-LangGraph fallback is unchanged and proven unchanged."
}
```
