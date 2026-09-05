# Worker Result

STATUS: COMPLETE

**Run:** run_c2166e75bb02 · **Phase:** PLAN · **Iteration:** 2 · **Role:** worker · **Round:** CORRECTION
**Repository:** /Users/luminous/aiAssistedProjects/orca-skills · branch `main` · HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35` · tracked worktree clean (only untracked `artifacts/` run dirs)
**Ticket:** Jira OS-31 — Implement Durable Pause and Resume for Human Decisions
**Approved baseline:** `artifacts/runs/run_c2166e75bb02/ANALYSIS.md` (iteration 2, Reviewer PASS, 0 blocking findings) — read, not modified.
**Context read, not modified:** `REVIEW_ANALYSIS.md`, `REVIEW_ANALYSIS_iteration2.md`, `REVIEW_PLAN.md`.
**Correction round.** Iteration 1 returned `RESULT: FAIL` with exactly one blocking finding, **F-001**.
This iteration resolves it in place; see `## Review Feedback Resolution` at the end for the per-finding
change list. The rest of the plan is preserved, not rewritten.
**Off-limits and untouched:** `artifacts/runs/run_8e8f9451ad44/` and every other pre-existing historical run/artifact.

This phase plans the work. **No production code, test, configuration or SKILL document was changed.**
No branch was created, nothing was staged, nothing was pushed. All file/line citations below are
either carried from the approved ANALYSIS or verified by me at this HEAD; where I verified something
myself in this phase it is marked **[verified in PLAN]**.

---

## Goal

Turn OS-31 into an ordered set of individually reviewable work units that, together, make a run that
stops at `NEEDS_INPUT`/`CONFLICT` a **durable, explicitly named, resumable lifecycle state** rather
than a terminal failure — surviving Coordinator process exit, settling every active Task/Dispatch and
recording terminal ownership on the way in, binding the pending question to run + phase +
checkpoint/head + artifact digest, letting a *new* Coordinator discover it and resume it exactly
once, failing closed on duplicate / stale / conflicting responses and on changed source, policy or
artifacts, never bypassing the phase Reviewer or the Final Adversarial Review gate, and providing an
explicit cancel/abandon path with append-only audit and timing evidence.

The plan must land all of that while keeping the observed baseline green — **2014 tests, `OK
(skipped=6)`; `validate_skills.py` 732 checks PASSED** — and while respecting every closed contract
the repository already enforces.

---

## Scope / Out of Scope

### In scope (this is what OS-31 builds)

1. A `WAITING_FOR_INPUT` durable run state plus the entry, resume and cancel/abandon transitions,
   defined once and owned by the runtime-neutral engine.
2. Settlement of every active Task/Dispatch and explicit recording of terminal ownership at pause,
   against the four-axis model (`SKILL.md:855-925`, invariant `SKILL.md:2404`).
3. Durable persistence of the OS-40 LangGraph checkpoint as the **authoritative** execution/resume
   state, plus a run-scoped pause **index/fence record** that binds the pending decision to that
   checkpoint's head identity and to run / phase / repository head / artifact digest / policy digest,
   and that makes a paused run discoverable with no live process.
4. Discovery and exactly-once takeover of a paused run by a new Coordinator process.
5. Fail-closed rejection of duplicate, stale and conflicting responses.
6. Stale-decision revalidation when repository head, policy, requirement or artifact changed after
   the request was created.
7. Safe re-entry of the responsible phase after a response, with the downstream revalidation the
   engine already models, such that no phase Reviewer and no Final Adversarial Review gate is
   bypassed.
8. Crash / restart / replay idempotency, including a crash *inside* the pause transition.
9. An explicit run-level cancel and abandon path (OS-31's X3 — distinct from OS-30 decision-item
   cancellation and from Orca `worker-abandon`).
10. Append-only audit and timing evidence for pause / resume / cancel / error.
11. Adapter-side translation of Orca lifecycle signals only; the policy stays in the engine.
12. Full exercisability with a fake / in-memory adapter and no Orca present.

### Out of scope — restating the ticket's own walls (non-goals, see also `## Non-Goals`)

- No arbitrary running-Worker **process memory snapshot / restore**.
- No **timeout-based or automatic default decision** when there is no response.
- No **GUI, notification channel or transport-specific UI**.
- No expansion into a **general Orca-independent CLI orchestration**.
- No modification of any **pre-existing historical run or artifact**.

---

## Recorded Decisions

Seven decisions are recorded here. **D1–D3** are the three the phase contract named. **OD-1–OD-3**
are the items the approved ANALYSIS told PLAN to resolve before DESIGN begins (`ANALYSIS ##
Recommended Next Step`, items 2, 3 and 4). **OD-4** resolves ANALYSIS **U-2**, which iteration 1
wrongly left open to DESIGN — F-001 requires a production durable checkpointer to be *required by the
plan*, not chosen later. Each decision is justified against constraints ANALYSIS already established;
none re-opens a settled fact.

D2 (and with it OD-4, WU-2, WU-5, WU-6, WU-7, WU-8, WU-13, traceability row REQ-1, risk R-8 and
completion criterion 7) was **rewritten in iteration 2** to resolve F-001. Iteration 1's D2 made the
run-scoped pause record the "sole source of truth" and the checkpoint an "optimisation"; that
inverted an explicit OS-31 requirement and is withdrawn.

### D1 — Placement of the pause/resume state machine: **engine-owned, in a LangGraph-free pure module; the LangGraph graph and the live Orca harness are both clients; adapters translate only.**

**Decision.** The pause/resume/cancel *policy* — the legal lifecycle states, the legal transitions,
the guards, the closed refusal reason codes and the pause/resume/cancel identities — lives in a new
pure module inside `scripts/deterministic_workflow/` that imports neither `langgraph` nor any Orca
symbol. Two clients consume it: (a) the OS-40 graph path (`routing.py` / `executor.py` /
`graph.py`), and (b) the live Skill-document path (`orca_runtime_harness.py`). `OrcaAdapter` and
`FakeAdapter` translate lifecycle *signals* (settle this dispatch, release this terminal) through a
port and make no policy decision.

**Why, against the constraints ANALYSIS established.**

- The ticket states it verbatim: "Orca lifecycle 신호는 adapter에서 변환하되, pause/resume 정책은
  runtime-neutral engine이 소유한다." ANALYSIS §3 records that this is currently true only because
  the engine owns *nothing* about pause (P1), so OS-31 must make it true by construction.
- Putting the policy only in the LangGraph graph is refused by FI-12 / §9a: `graph.py:9` imports
  `langgraph.graph` at module top level and `launcher.require_runtime()` refuses to run without the
  exact pin, "it does not use the prompt loop as a fallback" (`INSTALL.md:254-255`). A no-LangGraph
  Coordinator could then neither create nor discover a paused run, and the Skill-document path is
  the one a real Coordinator runs today.
- Putting the policy in the harness is refused by the OS-27 boundary (`ANALYSIS ## Dependencies /
  Constraints`) and would create the second workflow controller `validate_workflow_graph_docs.py`
  exists to prevent (`ControlPlaneError`, "graph_owned_decision set drifted from the engine").
- **[verified in PLAN]** the placement is achievable: `scripts/deterministic_workflow/__init__.py`
  imports only `contracts`, `routing`, `state`, and
  `python3 -c "import deterministic_workflow.runtime_state; 'langgraph' in sys.modules"` prints
  `False`. A new sibling module is therefore importable with LangGraph absent, exactly like
  `runtime_state.py`.

**Consequence for the plan.** WU-3 creates that module; WU-5/WU-7/WU-8/WU-10 wire the graph path to
it; the harness path is wired in WU-5/WU-10 and covered offline by `test_orca_runtime_contract.py`.
Adapter work is confined to WU-4.

### D2 — Durable state authority: **when LangGraph is available the OS-40 checkpointed `WorkflowState` is the authoritative durable execution/resume state; the run-scoped pause record is an index, a coordination fence and a projection of it — never a competing authority.**

**Decision.** OS-31 makes the OS-40 checkpoint real and authoritative, in two tiers with one
explicit authority rule between them.

**Tier 1 — authority (LangGraph present).** OS-31 ships a **production durable checkpointer** (OD-4)
and installs it by default on the graph path. The checkpointed `WorkflowState` on the run's
`thread_id` is the **single authoritative durable execution/resume state**: on resume the engine
**reconstructs `WorkflowState` from the checkpoint**, never from the pause record. Everything the
run's execution depends on — phase index, `phase_passes`, `phase_iterations` /
`remaining_phase_budget`, `correction_queue`, `processed_command_ids`, `processed_event_ids`,
`logical_trace`, `repository_binding`, `artifact_binding`, `decision_state`,
`pending_clarification_id` — is read back from the checkpoint. That list is exactly what ANALYSIS §1
observed is lost on process exit today, and the checkpoint is where OS-40 already models it.

**Tier 2 — index / fence / projection (not authority).** A run-scoped durable record — a sibling of
`runtime_state.py`, reusing the same `fcntl.flock` critical section + atomic-replace + closed-record
+ lease-fence discipline (`runtime_state.py:10-16`, `:24-34`, `:72-76`) — holds one record per paused
run, stored beside `.timing_state.json` under the run's artifact root. Its job is the three things a
checkpoint store cannot do, and **only** those three:

1. **Index / discovery.** Make the paused run findable by a brand-new Coordinator process that
   enumerates run artifact roots — with no live process, no shared memory and without having to open
   and scan every checkpoint thread. This is what makes AC-2 and AC-3 mechanically reachable.
2. **Coordination fence.** Provide the run-scoped exclusive claim, owner, lease and expiry that
   arbitrate concurrent takeover and cancel-vs-resume (WU-6, WU-10). LangGraph's checkpointer offers
   no run-scoped single-writer semantics, and `RuntimeStatePort.claim` is per-`intent_id` only
   (`runtime_state.py:8-22`, `ports.py:79`).
3. **Checkpoint pointer + read-only projection.** The pointer — `thread_id`, `checkpoint_id`, and a
   digest of the checkpointed values — plus a **projection** of the human-facing resume coordinates
   (`current_phase`, `phase_iteration`, `round_kind`, `pending_role`, the OS-30 `request_id` /
   `decision_item_id`, the OS-29 `source_ledger_key`, `repository_binding`, `artifact_binding`,
   the policy digest, and the four-axis settlement outcomes from ANALYSIS §4). The projection exists
   so a human or an auditor can read the pause record alone and understand the run, and so resume can
   **cross-check** the checkpoint. It is never the input from which execution state is rebuilt.

Fields the record owns **authoritatively on its own** are only these: the run's discovery identity,
the claim/owner/lease/expiry, the checkpoint pointer, and the terminal disposition
(`CANCELLED` / `ABANDONED`) that makes a disposed run un-re-adoptable. Everything else in it is a
projection and is subordinate to the checkpoint.

**The authority rule, stated once, explicitly.**

> When LangGraph is available, the checkpointed `WorkflowState` is authoritative for all execution
> and resume state, and the pause record's projected fields are used only for discovery, display and
> cross-checking. When the checkpoint and the projection disagree, **neither side wins silently**:
> the run fails closed (rule C3 below). When LangGraph is absent the graph engine does not run at
> all — that is the pre-existing, deliberate fallback (`launcher.require_runtime()`,
> `INSTALL.md:254-255`, "it does not use the prompt loop as a fallback") — and in that environment
> the pause record remains readable as a **discovery index and audit record only**, for the
> Skill-document Coordinator path, which has no checkpoint and therefore no execution state to be
> authoritative over. **The no-LangGraph fallback never supersedes checkpoint authority when
> LangGraph is present**; it is a strictly smaller capability, and it is preserved unchanged.

**Checkpoint ↔ pause-record consistency rules (fail-closed; C1–C4).** These are normative for
WU-5, WU-6, WU-7 and WU-10, and each has a named closed reason code and a proving test.

| # | Rule | Violation → | Fail-closed behaviour |
|---|---|---|---|
| C1 | **The checkpoint commit is the commit point of a pause.** The checkpoint that carries `WAITING_FOR_INPUT` is committed *first*; the pause record is written *after* and references the committed `checkpoint_id`. A pause record may therefore never name a checkpoint that does not exist. | `PAUSE_CHECKPOINT_MISSING` | Resume is refused. The run stays `WAITING_FOR_INPUT`; discovery reports the record as **unresumable, needs explicit disposition**. Nothing is reconstructed from the projection. |
| C2 | **The named checkpoint must be the head of its own thread.** On resume the engine asks the checkpointer for the current head of `thread_id` and compares it to the pointer. | `STALE_CHECKPOINT_HEAD` | Resume is refused (this is ANALYSIS F3, which has no analogue today). No effect is performed; the run stays resumable by a correct claimant after the record is refreshed under the run-scoped claim. |
| C3 | **The projection must agree with the checkpoint.** Every projected field is re-derived from the checkpointed `WorkflowState` on resume and compared field by field. | `PAUSE_PROJECTION_DIVERGED` | Refused. The engine **must not** silently prefer either side, must not "repair" the checkpoint from the projection, and must not proceed on the checkpoint alone. The run stays `WAITING_FOR_INPUT` and requires an explicit human disposition — a re-projection under the claim, or cancel/abandon (WU-10). |
| C4 | **Crash between C1's two writes is repaired forward, in the direction of authority only.** A committed `WAITING_FOR_INPUT` checkpoint with **no** pause record is repaired by re-deriving the record *from the checkpoint*, under the run-scoped claim, idempotently. The converse — a pause record with no reachable checkpoint — is C1 and is **not** repairable, because the projection is not authority. | `PAUSE_RECORD_MISSING` (repairable) / `PAUSE_CHECKPOINT_MISSING` (not) | The asymmetry is deliberate and is the operational expression of "the checkpoint is the authority". |

**Why this shape.**

- **The ticket names the authority.** OS-31's 핵심 요구사항 states it as a direct instruction:
  "OS-40의 LangGraph checkpoint/state를 durable pause/resume의 기준 상태로 사용한다." An explicit
  requirement outranks a convenience argument in the decision priority the quality gate states. The
  checkpoint is therefore the basis state, by instruction, not by preference.
- **The hole this closes is real and observed.** At this HEAD
  `build_graph(adapter, *, checkpointer=None, ...)` (`graph.py:262`) accepts an optional
  checkpointer, `launcher.execute_state` configures `configurable.thread_id` **only if** a
  checkpointer was passed (`launcher.py:121-122`), and `run_cli` never passes one
  (`launcher.py:215`); `MemorySaver` appears only in tests (ANALYSIS §1). Without OD-4 and WU-2(c),
  a plan could satisfy every completion criterion while OS-40 checkpoint persistence was never
  actually installed. That is the F-001 hole, and it is closed by *requiring* the saver and its
  default wiring, not by describing one.
- **FI-6 still forecloses putting the binding in the OS-30/OS-29 schemas.** `ITEM_INPUT_FIELDS` /
  `REQUEST_FIELDS` (`clarification_protocol.py:398-407`) and `CLOSED_LEDGER_RECORD_FIELDS` with its
  named `OS30_RESERVED_FIELDS` boundary (`decision_gate.py:190-205`) are closed sets defended by
  prose and tests. The Tier-2 record *references* them and extends neither.
- **FI-2 / M5 still forecloses `runtime_state.py` as-is:** it is keyed on `intent_id` and cannot
  answer a run-scoped question. Tier 2 reuses its *discipline*, which is what P2 asks for.
- **A checkpoint store alone cannot serve discovery or arbitration**, which is the only reason
  Tier 2 exists at all. Reducing it to index + fence + projection is what removes the competing
  authority F-001 identified, while keeping AC-2/AC-3 achievable.
- **The mutable-control-area placement still matters,** because the Tier-2 record legitimately
  changes (claim, lease renewal, status, applied-set) while `run_logging` guarantees artifact
  immutability with `rename`-onto-existing-directory, explicitly "NOT `os.replace`"
  (`run_logging.py:1913-1919`), and has no `force`/`--overwrite` (`:1046-1049`, `:1067-1071`).
  `.timing_state.json` (`run_logging.py:57`, `:840-853`, `:886-899`) is the existing precedent, and
  is also the only place a new Coordinator can recover `run_started_at` (`:570-578`, `:600-601`).
  The durable checkpoint store lives in the same mutable-control area for the same reason.

**Consequence for the plan.** WU-2 grows to three explicitly separable parts — (a) the durable
checkpointer, (b) the Tier-2 index/fence record and its port, (c) the default wiring that installs
the checkpointer on every graph path. WU-5 commits the checkpoint first and then writes the record
(C1). WU-6 discovers via the record and **rebuilds `WorkflowState` from the checkpoint**. WU-7
enforces C2 and C3 before any effect. WU-10 marks the record terminal and retires the checkpoint.

### D3 — Resume re-entry path: **re-enter through the existing correction / revalidation machinery; never by un-setting a terminal status.**

**Decision.** `WAITING_FOR_INPUT` is a **non-terminal** engine state, so the graph leaves it by a
legal edge rather than by erasing anything. On resume the engine re-enters the responsible phase
through the vocabulary that already exists — `responsible_phases` (`routing.py:22-29`),
`active_correction_phase` (`routing.py:89-96`), `downstream_revalidation_set` (`routing.py:14-19`,
high-risk only), `advance_phase_node` (`executor.py:424-444`) and the `PREPARE_CORRECTION` /
`PREPARE_REVALIDATION` tokens. Resume is expressible only through a typed `UPDATE_COMMANDS` entry;
the raw `GuardedWorkflowGraph.update_state` path is closed against `terminal_status` and the
lifecycle fields.

**Why.** This is ANALYSIS's own recommendation (§8/S3) and it is the only shape that satisfies AC-6
by construction rather than by discipline:

- S1 / FI-8 / R-3: setting `terminal_status = None` on a `BLOCKED` state is **accepted** by
  `validate_state` today and the next `route()` returns `PREPARE_WORKER` (observed, ANALYSIS steps
  6–7). If resume is "clear the terminal", the gate bypass is expressible by anyone with graph
  access and is unaudited. A non-terminal pause state removes the need for that operation entirely.
- `prepare_intent_node` already clears `worker_result` / `reviewer_result` on every Worker round
  (`executor.py:85`), so a correction re-entry cannot inherit a stale Reviewer verdict.
- The correction path is the tested expression of "go back to the phase that owns this and
  revalidate everything downstream", which is exactly the ticket's "source, policy 또는 artifact가
  변경된 경우 … 책임 phase부터 재검증한다".
- It leaves the risk-dependent gate intact: at medium/high `phase_gate` still requires a
  `reviewer_result` (`routing.py:32-43`), and `final_gate` still requires Final Review `PASS` plus
  `all_phase_passes_current` plus `final_review_binding_current` (`routing.py:46-50`, `:112-114`).
  Resume never touches those predicates.
- It exposes FI-9 / R-4 as a required fix rather than an optional one: `all_phase_passes_current`
  (`routing.py:53-54`) is a pure presence test and `phase_pass_binding` (`routing.py:81-86`) has
  zero callers. That is harmless only while a run is one uninterrupted process; after a durable
  pause and a head change it lets a stale phase pass satisfy completion. WU-8 makes phase-pass
  currency real.

### OD-1 (resolves ANALYSIS U-1, widened by U-8) — **extend the closed lifecycle tuples; do not overload `BLOCKED` with a reason code. Cancel and abandon are two distinct outcomes, not one outcome with a reason.**

**Decision.** OS-31 adds `WAITING_FOR_INPUT`, `CANCELLED` and `ABANDONED` to `RUN_STATUS_VALUES`
(`run_logging.py:116`), and adds `CANCELLED` and `ABANDONED` — but **not** `WAITING_FOR_INPUT` — to
the engine's `TERMINAL_STATUSES` (`contracts.py:26`). The three are decided **once, together**, per
CC-1 / FI-15. The tuples stay closed and stay eagerly fail-closed; what changes is which values the
contract names.

**Why.**

- Explicit requirement outranks everything else in the decision priority the quality gate states.
  OS-31 says the waiting state "실패나 미정 settlement로 가장하지 않고 명시적인 lifecycle state로
  보존한다", and AC-2 requires that run state *alone* explain why the run stopped. Recording a
  paused run as `BLOCKED` in `ORCHESTRATOR_LOG.md` — the run-lifecycle authority
  (`SKILL.md:1566-1572`) — is precisely the masquerade the ticket forbids, whatever reason string
  rides along.
- The rationale comment defending the closed tuple (`run_logging.py:112-115`) argues against
  *unrecognised* strings: "a fifth value is not a stricter status this module invented — it is a
  caller passing something that is not one of the four the **contract names**". Having the contract
  name three more values, at all three enforcement points plus the CLI `choices`, honours that
  rationale rather than contradicting it.
- `WAITING_FOR_INPUT` must not be a `TERMINAL_STATUS` — that is D3/S1: a terminal pause would have
  to be un-set to resume, which is the bypass seam.
- Cancel and abandon stay two statuses because TC-1 has an OS-30 response to bind and TC-2/TC-3 have
  none (ANALYSIS §10e). TT-2 must be able to assert that an abandon fabricated **no** decision record
  and no `decision_cancelled` event for an unanswered item; if both outcomes collapse into one
  status, that distinction is unobservable from run state and AC-2 fails for the abandon case.

**Blast radius this decision accepts, and where it is paid for.** `run_logging.py:116` and its
validator `:570-588`; `orca_runtime_harness.py:2923-2936`; the CLI `choices=RUN_STATUS_VALUES`
(`run_logging.py:3269`); `contracts.py:21-26`; the two fenced SKILL.md contract blocks; every
`run_end` reader. All of it is scheduled in WU-1, WU-11 and WU-13. This is R-1 and R-2, accepted
knowingly.

**What is still DESIGN's.** The exact token/state *names*, the reason-code vocabulary, the record
field names and types, the node/edge topology, and the port signatures. PLAN fixes the shape and the
blast radius; DESIGN fixes the API.

### OD-2 (resolves ANALYSIS R-9 / U-5) — **the "Orca 1.4.196 compatibility regression" is evidenced by the offline contract suite; no live-runtime evidence is claimed, and its absence is stated, not papered over.**

**Decision.** ANALYSIS §9b Option 1. TEST runs the offline unit/contract regression that encodes the
1.4.196 contract as data — `test_orca_runtime_contract.py` asserts `SUPPORTED_ORCA_APP_VERSIONS`
is exactly `("1.4.196",)` (`:8957`, `:9014`), and `test_orca_runtime.py:675`, `:743` cover the
version gate — and proves OS-31 changes neither the supported set nor the refusal behaviour. The
result is reported as what it is: a contract regression, **not** live-runtime evidence.

**Why.**

- Observed at this HEAD (ANALYSIS §9b, and unchanged): the live runtime reports `1.4.197` while
  `SUPPORTED_ORCA_APP_VERSIONS = ("1.4.196",)` (`orca_runtime_harness.py:249`) and
  `validate_orca_contract` refuses it (`:458-463`). The real-runtime Step 4 suite is refused before
  any dispatch, which is why six tests skip with `requires --orca-runtime and a ready Orca runtime`.
- Option 2 (obtain a real 1.4.196 host) is not something OS-31 can perform unilaterally; it is an
  environment procurement item.
- Option 3 (point-verify 1.4.197 and add it to the supported set) requires a fresh real-runtime run
  *of the revision that adds it* plus the guide-grammar check (`orca_runtime_harness.py:239-241`) —
  a substantial piece of work that ANALYSIS records as "on its face **outside OS-31's stated
  scope**". Taking it would be scope expansion, which the ticket forbids.
- Producing narrative "evidence" for a suite that never ran would be fabricated evidence and a G5
  violation. This decision exists specifically to prevent that (R-9).

**Standing offer, not a hidden assumption.** If a 1.4.196 runtime becomes available before TEST
closes, TEST additionally runs the opt-in `--orca-runtime` suite and records the actual output. If
the Coordinator or the user requires live-runtime evidence as a gate, that is an environment
decision for them; the plan surfaces it here rather than letting TEST discover it.

### OD-3 (resolves ANALYSIS U-9) — **an abandon is invoked only by an explicit human instruction, recorded with an actor identity in the pause record; no new operator role, and never a timeout.**

**Decision.** Both TC-1 (cancel) and TC-2/TC-3 (abandon) are initiated by an explicit human
instruction arriving through the same channel any decision arrives through, and the pause record
stores the actor and the submission identity that authorised it. OS-31 introduces no operator-role
concept, no privileged actor class and no automatic invoker.

**Why.** The ticket says "explicit cancel/abandon path" and forbids "응답 없을 때 위험한 default
자동 적용". `SKILL.md:2443` and limitation L4 (`SKILL.md:2380`) already state that a timeout is never
grounds for user authority. The repository has no operator-role concept to borrow (ANALYSIS U-9), and
inventing one would be scope expansion into run management (R-11). Recording the actor is required
anyway by AC-7's audit obligation.

### OD-4 (resolves ANALYSIS U-2, and is required by D2) — **ship an in-repository `BaseCheckpointSaver` over the already-pinned `langgraph-checkpoint`; install it by default; add no new pinned dependency.**

**Decision.** OS-31 implements a durable LangGraph checkpointer **inside the repository**, as a
`langgraph.checkpoint.base.BaseCheckpointSaver` subclass in its own engine module, persisting to the
run's mutable-control area with the same `fcntl.flock` critical section + atomic-replace + closed
on-disk record discipline `runtime_state.py` already proves out (`runtime_state.py:10-16`, `:24-34`).
It is **required, not optional**: `run_cli` installs it by default, `execute_state` installs one when
the caller supplies none, and the `configurable.thread_id` that today is set only when a checkpointer
was passed (`launcher.py:121-122`) is set unconditionally on the graph path. `MemorySaver` remains a
test-only convenience. This is *not* left to DESIGN; DESIGN fixes the module name, the on-disk record
field names and the pruning/retention policy, nothing about whether the saver exists.

**Why.**

- **[verified in PLAN]** `langgraph-checkpoint==2.1.1` is **already pinned**
  (`requirements-langgraph.txt:3`) and already installed here;
  `pkgutil.iter_modules(langgraph.checkpoint.__path__)` → `['base', 'memory', 'serde']`;
  `from langgraph.checkpoint.base import BaseCheckpointSaver` imports, exposing
  `get_tuple / list / put / put_writes / aget_tuple / alist / aput / aput_writes / get_next_version /
  delete_thread / config_specs / serde`; and `from langgraph.checkpoint.serde.jsonplus import
  JsonPlusSerializer` imports. The base interface and a serializer therefore already ship with the
  pinned set.
- **[verified in PLAN, carried from ANALYSIS §2/M4]** no durable saver ships:
  `from langgraph.checkpoint.sqlite import SqliteSaver` → `ModuleNotFoundError`. The two options
  ANALYSIS named were a new pinned dependency (`langgraph-checkpoint-sqlite`) or an in-repository
  saver.
- The in-repository saver is chosen because it adds **zero** new pinned dependencies, so
  `requirements-langgraph.txt`, `release_manifest.py:30` and the offline-wheel story
  (`INSTALL.md:250-256`) are untouched by a *dependency* change — which is what R-8 feared. The new
  engine module is carried by the ordinary source↔installed mirror obligation (FI-13/R-10) that every
  engine-touching unit already owes, and `release_manifest.py:90-95` gains it like any other tool
  file.
- It also keeps the durable checkpoint on the **same** locking and atomic-replace discipline as the
  Tier-2 record and `.timing_state.json`, so there is one durability story to review and one POSIX
  constraint to state (`runtime_state.py:36-38`), rather than two.
- It does not weaken the no-LangGraph fallback: the saver module deliberately **does** import
  `langgraph.checkpoint.base` and is therefore LangGraph-dependent by design, exactly like `graph.py`
  is. It is a separate module from the Tier-2 store and from the WU-3 policy module, both of which
  stay import-clean of LangGraph (D1) and are proven so by an import-isolation test.

**Cost this decision accepts.** OS-31 owns and maintains a checkpoint serializer/store rather than
consuming one. That is R-8 as re-stated, and it is paid for by WU-2(a)'s test obligations: round-trip
fidelity of every `WorkflowState` field, head resolution per thread, concurrent-writer safety under
the flock fence, corrupt-store refusal, and a restart proving reconstruction.

---

## Work Items

Fourteen units, each independently reviewable, each landing with its own tests and its own green
`validate_skills.py`. Every unit that touches `scripts/deterministic_workflow/**` mirrors into
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/**` **in the same change**
(FI-13/R-10) — this is stated once here and is a completion criterion for every such unit.

### WU-1 — Lifecycle vocabulary: extend the closed tuples and their two policing documents

- **What changes.** `contracts.py` — `TERMINAL_STATUSES` gains `CANCELLED`/`ABANDONED`;
  `ROUTE_TOKENS` gains the pause, cancel and abandon tokens; the `RouteToken` `Literal` and any
  reason-code sets follow. `graph_spec.py` — `NODES`/`STATIC_EDGES`/`ROUTE_TARGETS` gain the pause
  and disposal nodes, and `GRAPH_OWNED_DECISIONS` gains the pause/resume decision.
  `run_logging.py:116` — `RUN_STATUS_VALUES` gains `WAITING_FOR_INPUT`, `CANCELLED`, `ABANDONED`
  (the CLI `choices=RUN_STATUS_VALUES` at `:3269` follows automatically; the validator at `:570-588`
  and the harness repeat at `orca_runtime_harness.py:2923-2936` are re-verified, not loosened).
  `orca-worker-reviewer-orchestration/SKILL.md` — the `workflow-graph-contract` block (line 159) and
  the `workflow-control-plane` block (line 173), plus the `NON_AUTHORITATIVE` demotion marker on
  whichever sections the new graph-owned decision claims.
- **Why.** FI-1 and M1–M3: `WAITING_FOR_INPUT` is not a value, not a route token and not a run
  status anywhere; all three tuples are closed and eagerly validated. Every later unit is written in
  this vocabulary, so it must land first. OD-1 is what this unit executes.
- **Done when.** `graph_spec.validate_graph_spec()` passes (route-target coverage, reachability,
  every node reaches `TERMINAL`, `TERMINAL` has no outgoing edge); `validate_workflow_graph_docs.py`
  prints `PASSED` (both fenced blocks match the engine, every route token is owned by a declared
  decision, every graph-owned section carries the demotion marker, no safety section was demoted);
  `validate_skills.py` PASSED; the full suite green; new tests assert each of the three run-status
  enforcement points now **accepts** the three new values and still **refuses** an unrecognised one.
- **Deliberate test edits.** `test_deterministic_workflow_graph.py:101` and `:133`, and
  `test_deterministic_workflow_contracts.py:45` **[verified in PLAN]** encode "a decision block is
  terminal". They are updated deliberately in WU-5, not here, and the old assertion is preserved for
  the "pause not admissible" branch. This is a planned contract change, not a regression.

### WU-2 — Durable OS-40 checkpoint persistence (authority) + run-scoped pause index/fence (D2, OD-4)

Three separable parts, landed as one unit because (b) is meaningless without (a) and (a) is inert
without (c). Each part is separately reviewable and separately tested.

**(a) The production durable checkpointer — the authority.**

- **What changes.** A new engine module under `scripts/deterministic_workflow/` implementing
  `langgraph.checkpoint.base.BaseCheckpointSaver` (`get_tuple` / `list` / `put` / `put_writes` /
  `get_next_version` and the async pair, `serde` via `JsonPlusSerializer`), persisting checkpoints
  per `thread_id` into the run's mutable-control area under the same `fcntl.flock` critical section +
  atomic-replace + closed on-disk record discipline as `runtime_state.py:10-16`, `:24-34`. This is
  the module that is deliberately **LangGraph-dependent** (OD-4); it is a *different* module from
  (b) and from WU-3, both of which stay import-clean.
- **Why.** REQ-1 and D2: without a real durable checkpointer there is no OS-40 checkpoint to be the
  basis state, and OS-31 would be shipping pause/resume with no authoritative durable state at all.
  ANALYSIS §2/M4 observed that no durable saver ships and `langgraph.checkpoint.sqlite` does not
  exist; OD-4 resolves that in-repository.
- **Done when.** Round-trip fidelity is proven for **every** `WorkflowState` field the closed schema
  declares (`state.py`), not a sample; the head of a thread is resolvable and monotonic across
  writes; a second process opening the same store reads the first process's committed head; two
  concurrent writers are serialised by the flock fence with no lost or interleaved checkpoint; a
  corrupt store is **refused**, mirroring `RuntimeStateCorrupt` (`runtime_state.py:125-130`), never
  read as empty; the POSIX-only construction refusal is preserved (`runtime_state.py:36-38`); and a
  restart proves a full `WorkflowState` is reconstructed from the store alone.

**(b) The Tier-2 run-scoped pause index / fence record — explicitly not the authority.**

- **What changes.** A second new module under `scripts/deterministic_workflow/` implementing the
  run-scoped record family: closed record key set, `fcntl.flock` critical section, atomic-replace
  write, owner/lease/expiry fence, claim outcomes including `ALREADY_SETTLED`-style and
  `ALREADY_CANCELLED` (ANALYSIS §10f), and a corrupt-store refusal. `ports.py` gains the run-scoped
  port (or `RuntimeStatePort` grows a run-scoped family — DESIGN picks, per P2). Record location:
  beside `.timing_state.json` under the run's artifact root. The record's own authoritative content
  is limited to the discovery identity, the claim/owner/lease/expiry, the **checkpoint pointer**
  (`thread_id`, `checkpoint_id`, checkpoint digest) and the terminal disposition; every other field
  is a declared **projection** of the checkpointed `WorkflowState` and is documented as such in the
  module, so no later reader mistakes it for authority.
- **Why.** D2 Tier 2: discovery, arbitration and audit are the three things the checkpoint store
  cannot do. FI-2/M5 (the ledger is intent-keyed), FI-6 (neither closed schema can hold this), P2.
  Discovery (WU-6), exactly-once resume (WU-7) and cancel arbitration (WU-10) all read it.
- **Done when.** Unit tests cover: closed-record refusal of an unknown key; corrupt store refused
  rather than read as empty; lease fencing on every mutating call; two concurrent claimants →
  exactly one winner; replayed claim → the already-settled/already-cancelled outcome; POSIX-only
  construction refusal preserved. **This module imports with `langgraph` absent**, asserted by an
  import-isolation test, not by inspection — and the test asserts specifically that it does not
  transitively import the (a) saver.

**(c) Default wiring — the checkpointer is installed, not merely accepted.**

- **What changes.** `graph.py:262` — `build_graph` stops treating a checkpointer as an optional
  nicety on the production path: the graph path requires one, in the same "required port, refused at
  build time" spirit as `IdempotencyPortRequired` (`graph.py:278`), so no graph capable of pausing can
  be constructed without durable checkpointing. `launcher.py:121-122` — `configurable.thread_id` is
  set **unconditionally** on the graph path, not only when a checkpointer was passed.
  `launcher.py:198-215` — `run_cli` constructs the (a) saver by default beside the existing default
  `FileRuntimeStateStore`, with a path derived the same way as `default_runtime_state_path`
  (`launcher.py:57-59`), so the shipped command line is checkpoint-durable without extra flags,
  exactly as it is already ledger-durable without extra flags.
- **Why.** This is the specific hole F-001 found: today `checkpointer=None` is accepted, `run_cli`
  supplies none, and `thread_id` is configured only when one is passed — so every completion
  criterion could be met with no durable checkpoint anywhere. Requiring the saver in code is what
  makes REQ-1 true by construction rather than by intention.
- **Blast radius this accepts.** Six existing test files call `launcher.execute_state`
  (`test_deterministic_workflow_launcher.py`, `_recovery.py`, `_ownership.py`, `_malformed.py`,
  `_round2.py`, `_lease_keeper.py`) **[verified in PLAN]** and today run with no checkpointer; the
  24 existing `MemorySaver` uses in tests stay valid as an explicit override. Each of those call
  sites is re-verified in this unit — given a temp-directory default they should be unaffected, and
  any that is affected is fixed here rather than discovered in WU-14. This is R-21.

### WU-3 — Runtime-neutral pause/resume/cancel policy module (D1)

- **What changes.** A new pure module under `scripts/deterministic_workflow/` owning: the lifecycle
  states and the legal transition table (enter-pause, resume, TC-1 cancel, TC-2 abandon, TC-3
  crashed-mid-pause abandon, TC-4 cancel-vs-resume arbitration); the guards; the closed refusal
  reason codes; and the three identities — `pause_record_id`, `resume_id` over
  `(run_id, request_id, decision_item_id, decision_id, pause_record_id)`, and `cancellation_id` over
  `(run_id, pause_record_id, cancel_submission_id, cancel_kind)`. No `langgraph` import, no Orca
  import.
- **Why.** D1. Also FI-10/I2 and ANALYSIS §10f: the resume-application identity and the cancellation
  identity must deliberately **exclude** `repository_binding`, `artifact_binding` and
  `phase_iteration` — unlike `intent_id` (`contracts.py:225-240`) — because revalidation may
  legitimately move those bindings, and a cancel must remain possible after the head moved (CC-4).
- **Done when.** An exhaustive transition-table test asserts every legal transition and refuses every
  illegal one with a closed reason code; identity derivation is tested to be stable across a process
  restart and insensitive to binding changes; the module is proven importable with LangGraph absent.

### WU-4 — Settlement and terminal-ownership port, adapters, and the four-axis pause accounting

- **What changes.** `ports.py` gains a lifecycle-settlement port expressing "settle this dispatch"
  and "release / retain terminal ownership" (P3 — `AgentExecutionPort.interrupt` alone cannot say
  this). `orca_adapter.py` translates it to the real Orca verbs; `fake_adapter.py` implements it
  fully so the whole path runs with no Orca. Engine-side: the accounting that, for every dispatch the
  run created and has not yet accounted, decides axis (a) rather than assuming it, records axis (b)
  as one of reuse/retain/release/`unsupervised`, observes axis (c1) independently with the ~10 s
  eventual-consistency caveat, and evaluates axis (c2) to `authorized`/`not_authorized`/`unknown`
  with its evidence.
- **Why.** AC-1, FI-4, FI-5, and ANALYSIS §4. `OrcaAdapter` deliberately withholds `external_resume`
  (`orca_adapter.py:31-38`), so a new Coordinator hits `IDEMPOTENCY_RECOVERY_UNSUPPORTED`
  (`executor.py:121-125`) for any intent still dispatched at pause time. Pause **must** settle first;
  leaving one running and hoping to re-adopt it is unrecoverable by design.
- **Orca grammar discipline.** `SKILL.md:2396` forbids guessing Orca CLI grammar, and ANALYSIS U-4
  explicitly did not load the guides. IMPLEMENTATION loads the version-matched Orca orchestration and
  CLI guides before writing a single `worker-*` invocation. Until then the grammar is a DESIGN
  placeholder, and the whole path is exercised through `FakeAdapter`.
- **Done when.** After a pause no dispatch remains `dispatched`; every dispatch carries all four axis
  outcomes in the pause record; `unknown` ownership is recorded as retain-and-report rather than
  resolved by closing (`SKILL.md:913`); the Coordinator's own terminal is never in scope
  (`SKILL.md:898`, `:2407`); the accounting is idempotent when re-run after a crash between
  dispatch *n* and *n+1*.

### WU-5 — Pause entry: `NEEDS_INPUT` / `CONFLICT` → `WAITING_FOR_INPUT`, with the full binding

- **What changes.** `routing.py` — the decision axis routes to the pause node instead of straight to
  `BLOCK` when a pause is admissible (and still to `BLOCK` when it is not; both `routing.py:33` in
  `phase_gate()` and `routing.py:102` in `route()`). `executor.py` — the pause node, which runs WU-4's
  accounting, then **commits the `WAITING_FOR_INPUT` checkpoint (D2 rule C1 — this commit, not the
  record write, is the commit point of a pause)**, then writes the Tier-2 index record carrying the
  committed `checkpoint_id`, its digest and the projection, then writes the run status and the
  audit/timing rows.
  `state.py` — the durable pause fields, their validation, and typed `UPDATE_COMMANDS` entries;
  `pending_clarification_id` (`state.py:33`, `:63`, `:103`, `:291`) stops being a reserved slot and
  becomes a read field. The record captures `repository_binding` and `artifact_binding` via
  `contracts.binding_snapshot` (`contracts.py:162-164`), the checkpoint identity, **and the policy
  digest** over the `decision-policy` source that `decision_policy.load_decision_policy` parses out
  of `SKILL.md` — which is the missing input F5/FI-7 needs, since a `SKILL.md` edit silently changes
  the policy in force. All of these are written **into the checkpointed `WorkflowState`** and only
  *projected* into the index record (D2); the checkpoint is what a resume reads them back from. The OS-30 request is published through the existing path and its `request_id`
  / `decision_item_id` / `source_ledger_key` are recorded into the pause record.
- **Why.** Scope bullets 1–3 and 6; AC-2 and AC-7. This is the unit that makes "the process may now
  exit" true.
- **Deliberate test edits land here.** `test_deterministic_workflow_graph.py:101`, `:133` and
  `test_deterministic_workflow_contracts.py:45`.
- **Done when.** After a pause, the durable checkpoint (authority) plus the index record plus
  `ORCHESTRATOR_LOG.md` plus `TIMING_LOG.md` alone answer: why the run stopped, which decision is
  awaited, and where it resumes — with no live process and no in-memory state; the checkpoint alone
  is sufficient to rebuild `WorkflowState`, and the index record alone is sufficient to *find* the
  run and name that checkpoint. Ordering is asserted: a crash after the checkpoint commit and before
  the record write leaves the C4-repairable state, and a record can never name a non-existent
  checkpoint. A `WAITING_FOR_INPUT` run status row and a pause event row exist in both logs.

### WU-6 — Discovery and exactly-once takeover by a new Coordinator

- **What changes.** `launcher.py` and the installed `tools/run_workflow.py` gain exactly two verbs —
  discover paused runs, and resume one — plus the run-scoped exclusive claim over the index record
  that arbitrates concurrent takeover. The loser observes the settled outcome instead of acting,
  modelled on `_observe_then_take_over` (`executor.py:258-279`). **Resume reconstructs
  `WorkflowState` by loading the checkpoint named by the record's checkpoint pointer** — the record's
  projected fields are used to find and display the run and to cross-check (C3), never as the input
  from which state is rebuilt. C1/C2/C4 are enforced here before the graph is re-entered: a missing
  checkpoint is `PAUSE_CHECKPOINT_MISSING` and unresumable; a pointer that is not the thread head is
  `STALE_CHECKPOINT_HEAD`; a committed pause checkpoint with no index record is re-indexed forward
  from the checkpoint under the claim.
- **Why.** Scope bullet 4, AC-3, and I3/R-7: `RuntimeStatePort.claim` gives single-writer semantics
  per **intent** only (`runtime_state.py:8-22`, `ports.py:79`), so nothing today stops two
  Coordinators both resuming one paused run.
- **Scope wall.** Two verbs, nothing more. Run listing, run administration and a general
  Orca-independent orchestration CLI are out of scope (R-11), as is any move into OS-37 process/PTY
  ownership (R-12, `orca_adapter.py:36-38`).
- **Done when.** A brand-new process with no inherited state finds the paused run, adopts it, and
  **rebuilds the complete `WorkflowState` from the durable checkpoint** — proven by asserting the
  reconstructed state field-for-field against the pre-crash state, not by asserting that the run
  merely continued; two concurrent resumers produce exactly one winner and one non-destructive
  observer; a discovery pass over a checkpoint with no index record re-indexes rather than
  duplicating; the run's exit-code semantics for a paused (non-`BLOCKED`) run are defined and tested.

### WU-7 — Response application: exactly-once, fail-closed on duplicate / stale / conflicting

- **What changes.** `HumanApprovalPort` (`ports.py:90-95`) is wired for real and the `human_approval`
  capability (`contracts.py:39`) is checked before use, the way `EXTERNAL_LOOKUP`/`EXTERNAL_RESUME`
  already are (`executor.py:121`, `:162`). An artifact-backed implementation reads the OS-30 response;
  `FakeAdapter` supplies an in-memory one. The engine derives WU-3's `resume_id` and consults a
  durable applied-set **before** any effect. Closed refusals for: duplicate (already applied →
  non-destructive already-applied outcome); stale response (OS-30 revision superseded — `_current_request`
  `clarification_protocol.py:853`, `StaleItem` `:54`); **stale checkpoint head** (D2 rule C2 — the
  record's checkpoint pointer is not the head of its own thread — ANALYSIS F3, which has no analogue
  today); **checkpoint↔projection divergence** (D2 rule C3 — refused with its own reason code, and
  explicitly *not* resolved by preferring either side); conflicting responses (two different
  decisions for one item arriving at two Coordinators — fail closed, never arbitrate by recency).
  Every one of these checks runs **against the checkpointed state**, after reconstruction and before
  any effect.
- **Why.** Scope bullets 5 and 8; AC-3 and AC-4; F1–F4; FI-10.
- **Done when.** Replaying the identical response creates no second Task/Dispatch and overwrites no
  artifact; a stale response, a **stale checkpoint head**, a **diverged projection** and a conflicting
  response are each refused with a distinct closed reason code, none of them silently picks a side,
  and each leaves the run in a state a correct response (or an explicit disposition) can still
  resolve.

### WU-8 — Stale-decision revalidation and the resume re-entry (D3), plus phase-pass currency

- **What changes.** On resume the engine compares the **checkpointed** frozen `head_sha` /
  `tree_digest` / artifact digest / policy digest (read back from the reconstructed `WorkflowState`,
  the D2 authority — the index record's copies are only cross-checked under C3) to the current ones. Unchanged → re-enter the
  responsible phase. Changed → re-enter through `PREPARE_CORRECTION` / `PREPARE_REVALIDATION` at the
  responsible phase, with `downstream_revalidation_set` (`routing.py:14-19`) at high risk. Separately,
  `all_phase_passes_current` (`routing.py:53-54`) is made a real currency check using
  `phase_pass_binding` (`routing.py:81-86`), which has zero production callers today, and
  `verify_final_review_binding` (`routing.py:71-78`) becomes reachable from production rather than
  only from `test_deterministic_workflow_round2.py`.
- **Why.** Scope bullets 6 and 7; AC-5 and AC-6; D3; FI-9/R-4. Without the currency fix, a phase pass
  recorded against an old tree still satisfies completion after a resume moved the head — which is
  exactly the bypass AC-6 forbids.
- **Done when.** A resume after a head, artifact or policy change cannot reach `COMPLETE` without the
  responsible phase re-passing and, at high risk, its downstream phases revalidating; at medium/high
  the phase Reviewer runs again; Final Adversarial Review remains mandatory and identical at every
  risk level (`SKILL.md:2416`).

### WU-9 — Gate-bypass closure: the raw `update_state` seam

- **What changes.** `GuardedWorkflowGraph.update_state` (`graph.py:143-170`) is closed against raw
  writes to `terminal_status` and to the lifecycle/pause fields — today it checks only that key
  *names* are inside `CLOSED_STATE_FIELDS`, and `terminal_status` is one of them. Resume and cancel
  become expressible **only** through typed commands.
- **Why.** FI-8, R-3, CC-3, and the cancel-side form at ANALYSIS §10h.2. Observed steps 6–7: clearing
  `terminal_status` by hand is accepted and the next `route()` returns `PREPARE_WORKER`. Even with a
  non-terminal pause state (D3), leaving this seam open lets someone hand-write their way past every
  gate.
- **Done when.** A regression test proves the raw path refuses those fields; a second proves no
  resume or cancel code path ever clears a terminal status; a third proves a cancelled run cannot be
  resumed or re-discovered through the raw path (TT-6).

### WU-10 — Explicit run cancel and abandon (X3: TC-1 … TC-4)

- **What changes.** `routing.py` cancel/abandon edges; a disposal node that, under the run-scoped
  claim: (i) cancels the outstanding OS-30 request(s) through the existing replay-exact X1 path so
  the lineage carries a real `decision_cancelled` per item (`clarification_protocol.py:936-949`,
  `:1041-1051`) and `resolved_items` correctly refuses to promote dependents (`:673-679`); (ii) marks
  the pause record terminal so discovery can never re-adopt it; (iii) **freezes** rather than
  re-validates the bindings — a moved head is not a reason to refuse a cancel (CC-4); (iv) retires
  rather than deletes the checkpoint; (v) closes the `.timing_state.json` phase/iteration scopes and
  writes the second `run_end` pair with a non-blank `started_at` recovered from that file
  (`run_logging.py:57`, `:840-853`, `:886-899`, `:620-646`). TC-3 (crash inside pause) additionally
  runs the sanctioned `worker-abandon` → `worker-release` recovery over residual dispatches,
  accounting each as **recovered, not settled**, `worker_done` count 0, no role promotion, and
  therefore closing **no** terminal (`SKILL.md:869`, `:900`, `:928-930`;
  `orca_runtime_harness.py:3546-3571`, `:3568-3570`). TC-4 arbitrates cancel-vs-resume to exactly one
  winner. Ordering inside the node: X1 first (replay-exact), then the non-idempotent log rows, with
  the applied-set consulted **before** the rows are written (ANALYSIS §10f).
- **Why.** Scope bullet 9; AC-7; FI-14 (neither OS-30 item cancellation nor `worker-abandon` is this
  operation — a grep for every run-level cancel/abandon verb returns zero hits); FI-16; FI-17; R-13
  through R-17; TC-1…TC-4; OD-3 for who may invoke it.
- **Done when.** TT-1 … TT-9 pass (see the test plan), including: cancel replay yields
  already-cancelled with one set of byte-identical OS-30 artifacts and **no** second pair of `run_end`
  rows; an abandon with no response in existence fabricates no decision record; a cancel succeeds
  when the head has moved (the case where resume correctly refuses).

### WU-11 — `run_end` reader conformance for the second row

- **What changes.** Every `run_end` reader is audited against the stated rule "run_end는 terminal이
  아니다 … 마지막 `run_status` row를 authoritative status로 삼는다 … 뒤의 `run_end`가 앞의 것을
  대체한다" (`SKILL.md:1566-1572`). `verify_full_workflow_example.py:262-267` uses
  `any(row["event"]=="run_end" and row["result"]=="COMPLETED" …)` and is fixed to last-row-wins.
- **Why.** CC-2, FI-16, R-16. A paused run writes a `run_end`; a later cancel writes a second one.
  An `any(...)` reader then reports a cancelled run as its pre-cancel status. `validate_skills.py`
  enforces only that the *sentence* exists (`FINAL_REVIEW_AUDIT_RUN_END_ANCHOR` at `:2881`, checked
  `:2925-2932`) — reader conformance is a test obligation, not a documentation one.
- **Done when.** A test builds a log with two `run_end` rows and asserts every reader reports the
  last status; the anchor sentence still validates.

### WU-12 — Suppress post-cancel clarification republication

- **What changes.** `clarification_protocol.promote_pending` (`:662-671`), which the CLI runs after
  **every** response including a cancel (`:1336-1338`) and which deliberately reads no run status
  because none was available to it, consults the durable run-level fact before publishing a new
  request for a cancelled or abandoned run.
- **Why.** R-18 and TT-7: today, cancelling a run and then answering some *other* still-open item can
  publish a brand-new clarification request against an already-cancelled run. WU-2 is what finally
  makes a run-level fact available to this path.
- **Done when.** TT-7 passes: after a run cancel, `respond`/`promote` publishes no new request for
  that run, and still behaves unchanged for a live run.

### WU-13 — SKILL.md contract update, source↔installed parity, packaging

- **What changes.** `orca-worker-reviewer-orchestration/SKILL.md` — limitations L1, L3, L6 and L7
  (`:2377-2383`) become false and are rewritten; the Core Invariants block (`:2387-2450`) gains the
  pause/resume/cancel invariants; both fenced blocks and the demotion markers are final (WU-1 sets
  them, this unit reconciles them at the end). Every engine file added or changed under
  `scripts/deterministic_workflow/` is byte-mirrored into
  `orca-worker-reviewer-orchestration/tools/deterministic_workflow/`; `scripts/run_logging.py` is
  mirrored to `tools/run_logging.py`; `release_manifest.py` (`:30`, `:90-95`) is updated if a new
  tool file or a new pinned dependency appears; `INSTALL.md` is updated if a CLI verb or a dependency
  changes.
- **Why.** FI-13, R-10, R-2. `validate_deterministic_workflow_parity` compares file **sets** and then
  every file's bytes (`validate_skills.py:3003-3021`) and `validate_run_logging_tool_parity` does the
  same (`:2974-3001`); a missing mirror fails late, after the work looks done. Several `SKILL.md`
  anchors are asserted to appear exactly once.
- **Done when.** `validate_skills.py` PASSED with at least the baseline 732 checks;
  `test_release_package.py` and `test_validate_skills.py` green; the archive/package parity and
  source-installed parity checks green.
- **Note on the durable checkpoint (ANALYSIS U-2 — resolved by OD-4, not deferred).** OD-4 already
  fixes the mechanism: an **in-repository** `BaseCheckpointSaver` over the already-pinned
  `langgraph-checkpoint==2.1.1`. Therefore `requirements-langgraph.txt` gains **no** new pin and the
  offline-wheel story (`INSTALL.md:250-256`) is unchanged by a dependency. What this unit does own is
  the consequence of a new *engine file*: `release_manifest.py:90-95` enumerates it,
  `validate_deterministic_workflow_parity` requires the byte mirror, and `INSTALL.md` gains the
  sentence that the shipped command line is now checkpoint-durable by default (WU-2(c)) — stated
  alongside the unchanged "no prompt-loop fallback when LangGraph is absent" sentence at `:254-255`,
  so the two are not confused. The durable checkpoint is **correctness**, not an optimisation: it is
  the D2 authority for resume.

### WU-14 — End-to-end regression suite and validation evidence

- **What changes.** The tests below, in the files named, plus the final full-suite run and the
  honest evidence statement OD-2 requires.
- **Why.** AC-8 and the ticket's "필수 검증" list.
- **Done when.** Every row of the test plan has a passing test; the full suite, `validate_skills.py`,
  the package/archive check and the source-installed parity check are green and their **actual**
  output is recorded.

---

## Requirement → Work-Unit Traceability

Sources: **S** = OS-31 Jira *Scope* bullet; **AC** = OS-31 *Acceptance Criterion*; **REQ** = the
user's 핵심 요구사항 bullet; **V** = the user's 필수 검증 item. Every row maps to at least one unit.

| # | Source | Requirement | Work units | Proving tests |
|---|---|---|---|---|
| S1 | Scope | `WAITING_FOR_INPUT` durable run state와 허용 transition | WU-1, WU-2, WU-3, WU-5 | T-01, T-02, T-03, T-10 |
| S2 | Scope | active Task/Dispatch settlement 및 terminal ownership 정리 | WU-4, WU-5 | T-11, T-12 |
| S3 | Scope | pending decision request ↔ run/phase/head/artifact digest 결합 | WU-2, WU-5 | T-03, T-10, T-13, T-42, T-44 |
| S4 | Scope | 새 Coordinator에서 pending request 발견 및 resume | WU-6, WU-7 | T-13, T-14, T-15 |
| S5 | Scope | 중복·stale·conflicting 응답 fail-closed | WU-3, WU-7 | T-16, T-17, T-18, T-19 |
| S6 | Scope | head/policy/requirement/artifact 변경 시 stale-decision revalidation | WU-5, WU-8 | T-19, T-20 |
| S7 | Scope | 사용자 응답 후 책임 phase 재진입 + downstream revalidation | WU-8 | T-20, T-21, T-22 |
| S8 | Scope | crash/restart/replay idempotency | WU-2, WU-3, WU-4, WU-7, WU-10 | T-04, T-08, T-09, T-16, T-27, T-45 |
| S9 | Scope | explicit cancel/abandon path | WU-10, WU-12 | T-24 … T-32 |
| AC-1 | Accept | pause 시 orphan Task / active Dispatch / 불명확 ownership 없음 | WU-4, WU-5 | T-11, T-12 |
| AC-2 | Accept | run state만으로 왜 멈췄는지·어떤 decision·어디서 재개할지 복구 | WU-2, WU-5, WU-6 | T-10, T-13, T-41, T-43 |
| AC-3 | Accept | 새 Coordinator에서 응답 적용 후 정확히 한 번 재개 | WU-6, WU-7 | T-14, T-15, T-16, T-43 |
| AC-4 | Accept | 같은 response replay가 중복 Task/Dispatch·artifact overwrite 안 만듦 | WU-7 | T-16, T-23 |
| AC-5 | Accept | source/policy 변경 시 무조건 적용하지 않고 revalidation | WU-5, WU-8 | T-19, T-20 |
| AC-6 | Accept | resume 후 phase Reviewer / Final Review 규칙 우회 불가 | WU-8, WU-9 | T-21, T-22, T-33, T-34 |
| AC-7 | Accept | pause/resume/cancel/error event가 append-only log + timing evidence에 남음 | WU-1, WU-5, WU-10, WU-11 | T-30, T-31, T-32 |
| AC-8 | Accept | interruption / duplicate / stale head / conflicting에 대한 e2e regression | WU-14 | T-08, T-09, T-16, T-18, T-19 |
| REQ-1 | 요구 | OS-40 checkpoint/state를 durable pause/resume 기준 상태로 사용 | WU-2(a), WU-2(c), WU-5, WU-6, WU-7 | T-41, T-42, T-43, T-44, T-45, T-17 |
| REQ-2 | 요구 | `WAITING_FOR_INPUT` 및 진입·재개·취소 전이 명시적 정의 | WU-1, WU-3 | T-01, T-05 |
| REQ-3 | 요구 | pause 시 task/dispatch 정산 및 terminal ownership 안전 해제 | WU-4 | T-11, T-12 |
| REQ-4 | 요구 | pending clarification ↔ run/phase/checkpoint·head/artifact digest 결합 | WU-2(a), WU-2(b), WU-5 | T-03, T-10, T-42, T-44 |
| REQ-5 | 요구 | 새 Coordinator의 발견·인수·재개 | WU-6 | T-13, T-14, T-15 |
| REQ-6 | 요구 | 중복·stale·conflicting response fail-closed 거부 | WU-7 | T-16 … T-19 |
| REQ-7 | 요구 | 응답 적용·resume의 crash/retry idempotent + exactly-once | WU-3, WU-7 | T-08, T-09, T-16 |
| REQ-8 | 요구 | source/policy/artifact 변경 시 책임 phase부터 재검증 | WU-8 | T-19, T-20, T-21 |
| REQ-9 | 요구 | resume이 phase review·final review gate를 우회 불가 | WU-8, WU-9 | T-21, T-22, T-33, T-34 |
| REQ-10 | 요구 | cancel/abandon 경로 + append-only audit/timing 기록 | WU-10, WU-11 | T-24 … T-32 |
| REQ-11 | 요구 | Orca lifecycle 신호는 adapter 변환, 정책은 engine 소유 | WU-3, WU-4 (D1) | T-05, T-06, T-35 |
| REQ-12 | 요구 | Orca 없는 fake/in-memory adapter 환경에서 핵심 동작 검증 | WU-4, WU-14 | T-06, every T-1x/T-2x runs on `FakeAdapter` |
| V1 | 검증 | pause 직전·직후 crash 및 restart | WU-4, WU-5, WU-10 | T-08, T-09, T-27 |
| V2 | 검증 | 동일 응답 재전송 + 동시 resume 경쟁 | WU-6, WU-7 | T-15, T-16 |
| V3 | 검증 | stale checkpoint · stale response · 변경된 source/policy | WU-7, WU-8 | T-17, T-18, T-19, T-20 |
| V4 | 검증 | orphan task/dispatch 및 terminal ownership 누수 | WU-4 | T-11, T-12 |
| V5 | 검증 | artifact 중복 생성·overwrite 방지 | WU-7, WU-10 | T-23, T-28 |
| V6 | 검증 | cancel/abandon | WU-10 | T-24 … T-32 |
| V7 | 검증 | Orca 1.4.196 compatibility regression | WU-13, WU-14 (OD-2) | T-36 |
| V8 | 검증 | LangGraph 의존성이 없는 환경의 기존 fallback 동작 | WU-2(b), WU-3, WU-14 | T-35, T-37 |
| V9 | 검증 | 전체 test, skill validation, package/archive, source-installed parity | WU-13, WU-14 | T-38, T-39, T-40 |

---

## Dependencies / Execution Order

```text
WU-1  vocabulary + both fenced blocks
   |
   +--> WU-2  run-scoped durable pause store + port
   |        |
   |        +--> WU-3  runtime-neutral policy module (states, transitions, identities)
   |                  |
   +--> WU-4  settlement/ownership port + adapters + four-axis accounting
                      |
                      v
                   WU-5  pause entry + full binding capture      <-- first end-to-end pause
                      |
        +-------------+-----------------+
        v                               v
     WU-6  discovery + takeover      WU-9  raw update_state seam closed
        |                               |
        v                               |
     WU-7  response application         |
        |                               |
        v                               |
     WU-8  revalidation + re-entry <----+   <-- first end-to-end resume
        |
        v
     WU-10 cancel / abandon (TC-1..TC-4)
        |
        +--> WU-11 run_end reader conformance
        +--> WU-12 post-cancel clarification suppression
                      |
                      v
                   WU-13 SKILL.md + parity + packaging
                      |
                      v
                   WU-14 regression suite + validation evidence
```

**Why this order.**

- **WU-1 first, and alone.** Every later unit is written in the vocabulary it adds, and the two
  policing documents (`workflow-graph-contract`, `workflow-control-plane`) must move in the same
  change as the tuples or `validate_workflow_graph_docs.py` fails immediately. Landing the vocabulary
  by itself keeps that failure cheap and isolated.
- **WU-2 before WU-3.** The policy module's transitions are expressed against the record the store
  defines; building the policy against an imaginary record invites a second refactor.
- **WU-4 in parallel with WU-2/WU-3**, because it touches the port/adapter axis rather than the state
  axis, but it must land **before WU-5**: pause is not safe until every dispatch can be accounted
  (FI-5 — `OrcaAdapter` withholds `external_resume`, so a dispatch left running at pause is
  unrecoverable by any new Coordinator).
- **WU-5 is the first integration point.** It is also where the three deliberate test edits land, so
  it should be a single reviewable change rather than a by-product of a later unit.
- **WU-9 may land any time after WU-1 but must land before WU-8.** WU-8 is the first unit that makes
  completion reachable after a resume; the bypass seam must already be closed when it does.
- **WU-6 → WU-7 → WU-8** is a strict chain: you cannot apply a response before you can claim the run,
  and you cannot decide re-entry before you have applied a response.
- **WU-10 after WU-8** because TC-4 arbitrates cancel *against* a working resume; writing the
  arbitration before resume exists means testing one side of a race.
- **WU-11 and WU-12 after WU-10** because both are consequences of a run-level cancel existing.
- **WU-13 last of the code units** so parity and the manifest are reconciled once against the final
  file set, and the SKILL limitations are rewritten against what was actually built. WU-13's mirror
  obligation is nevertheless satisfied *per unit* along the way (FI-13/R-10); WU-13 is the final
  reconciliation, not the first time anyone thinks about it.
- **WU-14 last**, because its evidence must describe the finished system.

**External dependencies.** OS-28, OS-29, OS-30 and OS-40 are already merged at this HEAD (commits
`7bc228a`, `ba8d4fe`, `c279005` and their predecessors). No unmerged ticket blocks OS-31. The one
non-code dependency is OD-2's environment question (a 1.4.196 runtime), which is explicitly **not**
allowed to block: OD-2 chooses the offline path.

---

## Validation / Test Plan

### Baseline confirmed at this HEAD (ACTUAL observed output, not predicted)

`python3 scripts/validate_skills.py`:

```
Skill validation PASSED (732 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

`python3 -m unittest discover -s scripts -p 'test_*.py'`:

```
Ran 2014 tests in 339.873s

OK (skipped=6)
UNITTEST_EXIT=0
```

**Re-confirmed in iteration 2, at the same HEAD, after this correction was written** (ACTUAL
observed output, not predicted):

`python3 -m unittest discover -s scripts -p 'test_*.py'`:

```
Ran 2014 tests in 344.235s

OK (skipped=6)
UNITTEST_EXIT=0
```

`python3 scripts/validate_skills.py`:

```
Skill validation PASSED (732 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

The counts are identical to iteration 1's baseline, which is the expected result: this phase changed
one artifact document and no production code, test or configuration.

### Where the tests live, and how they run without Orca

- `scripts/test_deterministic_workflow_checkpoint.py` *(new)* — WU-2(a)/WU-2(c): the durable
  `BaseCheckpointSaver`, its round-trip fidelity over every `WorkflowState` field, thread-head
  resolution, concurrent-writer serialisation, corrupt-store refusal, and the default wiring in
  `build_graph` / `execute_state` / `run_cli`. This file **is** gated on `_langgraph_ok()`, because
  OD-4 makes the saver deliberately LangGraph-dependent — that asymmetry against the next bullet is
  itself the evidence that authority and index are different modules.
- `scripts/test_deterministic_workflow_pause.py` *(new)* — WU-2(b)/WU-3 unit level: index/fence
  record, closed record, lease fence, transition table, identities. **Not** gated on
  `_langgraph_ok()`, because D1 keeps these modules LangGraph-free; that ungated status is itself
  part of V8's evidence.
- `scripts/test_deterministic_workflow_pause_e2e.py` *(new)* — WU-4 … WU-9 end to end on
  `FakeAdapter`, with no Orca present: pause → checkpoint commit → process exit → discover → claim →
  **reconstruct `WorkflowState` from the checkpoint** → apply → resume.
  Crash injection is expressed as "stop driving the graph at step *n* and start a new driver", which
  is the same shape `test_deterministic_workflow_ownership.py` already uses to prove a new process
  can finish work an earlier process started.
- `scripts/test_deterministic_workflow_cancel.py` *(new)* — WU-10/WU-12: TC-1 … TC-4 and TT-1 … TT-9.
- `scripts/test_deterministic_workflow_graph.py`, `_contracts.py`, `_round2.py`, `_recovery.py`,
  `_ownership.py`, `_malformed.py` *(extended)* — the vocabulary, the routing edges, the deliberate
  edits at `graph.py:101`/`:133` and `contracts.py:45`, and the raw-`update_state` refusal.
- `scripts/test_run_logging.py` *(extended)* — the run-status tuple at all enforcement points, the
  second `run_end` pair, the timing-scope closure and the non-blank `started_at`.
- `scripts/test_verify_full_workflow_example.py` *(extended)* — WU-11 reader conformance.
- `scripts/test_workflow_control_plane.py` *(extended)* — the two fenced blocks against the engine.
- `scripts/test_orca_runtime_contract.py` *(extended)* — the harness-side pause/cancel logic offline,
  and the unchanged 1.4.196 contract assertions (`:8957`, `:9014`).
- `scripts/test_clarification_protocol.py` *(extended)* — WU-12's `promote_pending` suppression.
- `scripts/test_release_package.py`, `scripts/test_validate_skills.py` *(extended)* — parity and
  packaging.

Everything except the opt-in `--orca-runtime` suite runs with **no Orca runtime and no network**.
`FakeAdapter` is the vehicle: it already is the reference implementation of the optional recovery
capabilities and is declared to make a new process able to look up and finish an earlier process's
work (`fake_adapter.py:18-25`), which is exactly the property resume needs.

### Test → requirement map

Forty-five rows. T-41 … T-45 were added in iteration 2 to prove D2's checkpoint authority (F-001).

| Test | Proves | Requirement |
|---|---|---|
| T-01 | the three closed tuples name the new values and still refuse an unknown one, at all three run-status enforcement points and the CLI `choices` | S1, REQ-2, OD-1 |
| T-02 | `validate_graph_spec()` passes with the new nodes/edges; `TERMINAL` still has no outgoing edge; every node still reaches `TERMINAL` | S1 |
| T-03 | the Tier-2 index record's closed key set; unknown key refused; corrupt store refused rather than read as empty; every non-pointer field is declared a projection | S1, S3, REQ-4 |
| T-04 | lease fence on every mutating call; two concurrent claimants → exactly one winner | S8 |
| T-05 | exhaustive transition table: every legal transition accepted, every illegal one refused with a closed reason code | REQ-2, REQ-11 |
| T-06 | the settlement/ownership port is fully exercised through `FakeAdapter` with no Orca | REQ-11, REQ-12 |
| T-07 | `resume_id` and `cancellation_id` are stable across a process restart and **insensitive** to `repository_binding` / `artifact_binding` / `phase_iteration` | S8, REQ-7 |
| T-08 | **crash immediately before the pause commits** → restart leaves a determinate disposition, no half-paused run | V1, S8, AC-8 |
| T-09 | **crash immediately after the pause commits** → restart discovers a legally paused run and changes nothing | V1, S8, AC-8 |
| T-10 | after a pause, the durable checkpoint + the index record + `ORCHESTRATOR_LOG.md` + `TIMING_LOG.md` alone answer why / what decision / where to resume, with no live process — the index record naming the checkpoint and the checkpoint carrying the state | AC-2, S1, S3 |
| T-11 | after a pause **no dispatch remains `dispatched`**; each carries an axis-(a) verdict, or the named `worker-abandon` → `worker-release` recovery accounted as *recovered, not settled* | AC-1, S2, V4 |
| T-12 | axis (b), (c1) and (c2) recorded per dispatch; `unknown` ownership retained-and-reported, never closed; the Coordinator's own terminal never in scope | AC-1, S2, V4 |
| T-13 | a brand-new process with no inherited state discovers the paused run from the artifact root alone | AC-2, AC-3, S4 |
| T-14 | the new process claims the run and resumes it exactly once | AC-3, S4 |
| T-15 | **two concurrent resumers** → exactly one winner; the loser observes the settled outcome and performs no effect | V2, AC-3, S4 |
| T-16 | **the identical response resent** → already-applied; no second Task/Dispatch; no artifact overwrite | V2, V5, AC-4, S5 |
| T-17 | **stale checkpoint head** (D2 rule C2 — the index record's checkpoint pointer is not the head of its own thread) → refused with `STALE_CHECKPOINT_HEAD`, no effect performed | V3, S5, REQ-1 |
| T-18 | **stale response** (OS-30 revision superseded) → refused, distinct reason code | V3, S5, AC-8 |
| T-19 | **changed source / policy** (head moved, or the parsed decision-policy digest changed) → the response is not applied unconditionally; revalidation is triggered | V3, AC-5, S6 |
| T-20 | revalidation re-enters the **responsible phase** via `PREPARE_CORRECTION`/`PREPARE_REVALIDATION`, with `downstream_revalidation_set` at high risk | S6, S7, AC-5 |
| T-21 | after a resume, at medium/high risk the phase Reviewer runs again; a stale phase pass no longer satisfies `all_phase_passes_current` | AC-6, S7, REQ-9 |
| T-22 | after a resume, Final Adversarial Review is still mandatory and `final_review_binding_current` still gates `COMPLETE` | AC-6, REQ-9 |
| T-23 | a replayed response creates **no duplicate artifact** and overwrites none (`_write_directory` content-idempotency preserved) | V5, AC-4 |
| T-24 | TT-1 — `WAITING_FOR_INPUT` → cancelled, end to end, on `FakeAdapter` with no Orca | V6, S9 |
| T-25 | TT-2 — abandon with **no** OS-30 response in existence fabricates no decision record and no `decision_cancelled` for an unanswered item | V6, S9 |
| T-26 | TT-9 — cancel **succeeds when the head has moved**, the case where resume correctly refuses (CC-4) | V6, S9 |
| T-27 | TT-3 — crash-inside-pause → abandon: residual dispatches disposed via `worker-abandon` → `worker-release`, *recovered not settled*, `worker_done` count 0, every residual terminal recorded `not_authorized`/retained | V1, V6, S8 |
| T-28 | TT-4 — cancel replay → already-cancelled, one byte-identical set of OS-30 cancel artifacts, **no second pair of `run_end` rows** | V5, V6, AC-4 |
| T-29 | TT-5 — TC-4: one Coordinator resuming, one cancelling → exactly one wins, the loser observes, no duplicate Task/Dispatch | V2, V6, S9 |
| T-30 | TT-8 — the cancel writes a cancel/abandon event row and a second `run_end` row that is authoritative under the last-row rule | AC-7, V6 |
| T-31 | TT-8 — the `TIMING_LOG` `run_end` row has a non-blank `started_at` recovered from `.timing_state.json`, and the phase/iteration scopes are closed | AC-7, V6 |
| T-32 | pause, resume and error events each appear in both the append-only log and the timing evidence | AC-7, REQ-10 |
| T-33 | TT-6 — a cancelled run cannot be resumed or re-discovered, **including** through the raw guarded `update_state` path | AC-6, S9, CC-3 |
| T-34 | the raw `update_state` path refuses `terminal_status` and the lifecycle fields; no resume/cancel code path clears a terminal status | AC-6, REQ-9 |
| T-35 | the Tier-2 index store and the policy module import and function with `langgraph` absent, and neither transitively imports the WU-2(a) saver; the saver module is the one engine module that is deliberately LangGraph-dependent | V8, REQ-11, REQ-12 |
| T-36 | the 1.4.196 contract is unchanged by OS-31: `SUPPORTED_ORCA_APP_VERSIONS == ("1.4.196",)` and the refusal behaviour for any other version is intact (offline; OD-2) | V7 |
| T-37 | with `langgraph` absent the graph launcher still fails explicitly (`LANGGRAPH_DEPENDENCY_MISSING`, exit 3) and everything that is not the graph still works | V8 |
| T-38 | full suite: `python3 -m unittest discover -s scripts -p 'test_*.py'` green | V9 |
| T-39 | `python3 scripts/validate_skills.py` green, including engine and `run_logging` byte parity and the workflow-graph documentation contract | V9 |
| T-40 | package/archive and source-installed parity green (`test_release_package.py`, `release_manifest.py`) | V9 |
| T-41 | **the durable checkpointer is installed by default and cannot be skipped**: `run_cli` produces an on-disk checkpoint with no extra flags; `configurable.thread_id` is set on the graph path unconditionally; constructing a production graph without a durable checkpointer is refused at build time | REQ-1, AC-2 |
| T-42 | round-trip fidelity: every field of the closed `WorkflowState` survives a checkpoint write/read cycle, and the thread head is resolvable and monotonic; two concurrent writers are serialised, and a corrupt checkpoint store is refused rather than read as empty | REQ-1, REQ-4 |
| T-43 | **restart/resume is reconstructed FROM THE CHECKPOINT**: a process pauses, exits, and a brand-new process rebuilds the complete `WorkflowState` from the durable checkpoint and asserts it field-for-field against the pre-exit state — with the index record's projected fields deliberately mutated in a second variant to prove the reconstruction did **not** read them | REQ-1, AC-2, AC-3 |
| T-44 | **stale checkpoint heads are rejected** and **divergence never silently picks a side**: C2 refuses a pointer that is not the thread head; C3 refuses a projection that disagrees with the checkpoint with `PAUSE_PROJECTION_DIVERGED`, performs no effect, and neither repairs the checkpoint from the projection nor proceeds on the checkpoint alone | REQ-1, REQ-6, AC-5 |
| T-45 | C1/C4 ordering and forward repair: a crash after the checkpoint commit and before the index write leaves a committed pause that discovery re-indexes **from the checkpoint**, idempotently; an index record naming a non-existent checkpoint is `PAUSE_CHECKPOINT_MISSING`, unresumable, and is **not** repaired from the projection | REQ-1, S8, V1 |

### Evidence rules for TEST

- Record **actual** output, never predicted. A skipped test is reported as skipped with its reason.
- OD-2 applies verbatim: T-36 is reported as an **offline contract regression**, and the report says
  in plain words that no live 1.4.196 runtime evidence was produced and why.
- The six existing opt-in skips (`requires --orca-runtime and a ready Orca runtime`) must remain
  exactly six unless OS-31 deliberately adds an opt-in test, in which case the new count is stated.

---

## Known Hard Constraints the Plan Respects

1. **`RUN_STATUS_VALUES` is a closed, eagerly fail-closed tuple with three enforcement points plus a
   CLI `choices`.** `run_logging.py:116` (rationale `:112-115`), validated at `:570-588`; repeated
   outside `_safe_log` at `orca_runtime_harness.py:2923-2936`; `choices=RUN_STATUS_VALUES` at
   `run_logging.py:3269`. Observed refusals for `CANCELLED`, `ABANDONED` and `WAITING_FOR_INPUT`.
   OD-1 changes the named set at all four places at once, in WU-1, and keeps the set closed.
2. **The two fenced SKILL.md contract blocks.** `workflow-graph-contract` (SKILL.md line 159) must
   equal the engine's `WORKFLOW_ID` / `SCHEMA_VERSION` / `PHASES` / `ROUTE_TOKENS` /
   `TERMINAL_STATUSES` exactly; `workflow-control-plane` (line 173) must declare exactly
   `GRAPH_OWNED_DECISIONS`, must have **every** route token owned by some declared decision, must
   carry the `NON_AUTHORITATIVE` demotion marker on every graph-owned section, and must **not** carry
   it on any preserved safety section. Policed by `scripts/validate_workflow_graph_docs.py`, invoked
   from `validate_skills.py:3006-3027`. **[verified in PLAN]** Any new route token therefore forces a
   `GRAPH_OWNED_DECISIONS` entry and a demoted SKILL.md section in the *same* change (WU-1).
3. **Append-only logs, and the reader rule.** `_append_row` opens in `"a"` mode and never dedupes
   (`run_logging.py:331-335`); `log_run_status` writes exactly one `ORCHESTRATOR_LOG` `run_end` and
   one `TIMING_LOG` `run_end` (`:589-611`). The `--event` column has **no** `choices` by design
   (`:144`), so pause/resume/cancel *events* need no schema change. `SKILL.md:1566-1572` states
   "run_end는 terminal이 아니다 … 마지막 `run_status` row를 authoritative status로 삼는다", anchored by
   `validate_skills.py:2881`, `:2925-2932`. WU-10 relies on it; WU-11 makes every reader obey it.
4. **Byte parity between `scripts/` and the installed `tools/` mirror.**
   `validate_deterministic_workflow_parity` compares the file **sets** then every file's bytes
   (`validate_skills.py:3003-3021`); `validate_run_logging_tool_parity` does the same
   (`:2974-3001`); `release_manifest.py:30`, `:90-95` enumerate the installed tools and
   `requirements-langgraph.txt`. Every unit that touches the engine mirrors in the same change.
5. **`graph_spec.validate_graph_spec` is a static topology gate.** Route-target coverage must equal
   `ROUTE_TOKENS`; every node must be reachable from `VALIDATE` and must reach `TERMINAL`; `TERMINAL`
   must have no outgoing edge (`graph_spec.py:43-63`).
6. **OS-30 and OS-29 schemas are closed and must not be extended.** `ITEM_INPUT_FIELDS` /
   `REQUEST_FIELDS` (`clarification_protocol.py:398-407`) and `CLOSED_LEDGER_RECORD_FIELDS` with its
   named `OS30_RESERVED_FIELDS` boundary (`decision_gate.py:190-205`). D2's third record family
   *references* them and never adds a field to either.
7. **Artifacts are immutable.** `_write_directory` is content-idempotent and raises on divergence
   (`clarification_protocol.py:343-350`); `run_logging` uses `rename`-onto-existing-directory,
   explicitly "NOT `os.replace`" (`:1913-1919`); there is no `force`/`--overwrite` anywhere
   (`:1046-1049`, `:1067-1071`). The Tier-2 index record and the durable checkpoint store are
   therefore *mutable control files* beside `.timing_state.json`, not published artifacts.
8. **The engine has no LangGraph-free execution path, and that is deliberate.**
   `launcher.require_runtime()` raises `LANGGRAPH_DEPENDENCY_MISSING` / `LANGGRAPH_VERSION_UNSUPPORTED`
   for anything but exactly `0.2.76`; `INSTALL.md:254-255` — "it does not use the prompt loop as a
   fallback". The fallback OS-31 must not break is "everything that is not the graph keeps working",
   which `validate_skills.py`'s 732 checks demonstrate. D1 keeps the policy module and the Tier-2
   index store import-clean of LangGraph so that path is preserved. D2 states the boundary
   explicitly: preserving that fallback does **not** demote the checkpoint. When LangGraph is present
   the checkpointed `WorkflowState` is the authority for execution/resume state; when it is absent the
   graph does not run at all, and the index record degrades to a discovery and audit artifact for the
   Skill-document path, which never had execution state to be authoritative over.
9. **POSIX-only durable store.** `fcntl.flock`; on a non-POSIX host the store raises
   `RuntimeStateLockUnavailable` at construction rather than degrading (`runtime_state.py:36-38`).
   The new store keeps that behaviour.
10. **Four-axis settlement/ownership model.** `SKILL.md:855-925`, invariant `:2404`; `worker-abandon`
    is recovery, **not** settlement, contributes `worker_done` count 0 and promotes no terminal role
    (`SKILL.md:869`, `:928-930`; `orca_runtime_harness.py:3568-3570`); `unknown` ownership is
    retain-and-report (`:913`); the Coordinator never closes its own, a setup, or an adopted terminal
    (`:898`, `:2407`).
11. **Orca CLI grammar is never guessed.** `SKILL.md:2396`; the version-matched guide is loaded before
    any orchestration or terminal-lifecycle command (WU-4).
12. **Review gates are not negotiable.** Final Adversarial Review is mandatory and identical at every
    risk level (`SKILL.md:2416`); all requested phases PASS + Final Review PASS are required for
    `COMPLETED` (`:2426`); the current phase must PASS before the next (`:2413`).
13. **Durable checkpointing is currently optional in code, and OS-31 must remove that optionality.**
    `build_graph(adapter, *, checkpointer=None, ...)` (`graph.py:262`) accepts no checkpointer;
    `launcher.execute_state` sets `configurable.thread_id` **only if** one was passed
    (`launcher.py:121-122`); `run_cli` passes none (`launcher.py:215`); `MemorySaver` is test-only
    (24 uses across `test_*.py` **[verified in PLAN]**). `langgraph-checkpoint==2.1.1` is already
    pinned (`requirements-langgraph.txt:3`) and ships `base`, `memory`, `serde` — a
    `BaseCheckpointSaver` interface and a `JsonPlusSerializer`, but no durable saver
    (`langgraph.checkpoint.sqlite` → `ModuleNotFoundError`) **[verified in PLAN]**. OD-4 supplies the
    saver in-repository and WU-2(c) makes it mandatory on the production graph path, so REQ-1's
    checkpoint authority is enforced by the code rather than assumed by the plan.

---

## Risks

Carrying ANALYSIS R-1 … R-18 forward, with the mitigation each unit provides. Severity is ANALYSIS's.

| # | Risk | Severity | Mitigation in this plan |
|---|---|---|---|
| R-1 | A fifth `RUN_STATUS` value is a lifecycle-contract change with an eager validator and multiple callers | High | OD-1 decides it once for all three values; WU-1 changes all four enforcement sites in one reviewable unit; T-01 proves acceptance **and** continued refusal of an unknown value |
| R-2 | New route tokens / terminal statuses trip the static topology and documentation validators unless `SKILL.md` moves in the same change | Med-High | WU-1 lands tuples + both fenced blocks + demotion markers together; T-02 and T-39 gate it; WU-13 reconciles at the end |
| R-3 | Resume implemented as "clear `terminal_status`", reachable through the raw guarded `update_state` | **High** | D3 makes the pause state non-terminal so the operation is never needed; WU-9 closes the raw seam; T-33 and T-34 prove it |
| R-4 | Phase passes are not currency-checked, so a post-resume head change leaves a stale pass satisfying completion | **High** | WU-8 makes `all_phase_passes_current` a real currency check via `phase_pass_binding`; T-21 |
| R-5 | Pausing without settling leaves a dispatch no new Coordinator can re-collect | **High** | WU-4 lands before WU-5 by explicit ordering; pause is refused unless every dispatch is accounted; T-11 |
| R-6 | A crash *inside* pause is a new window the per-intent recovery ladder does not cover | High | WU-4's accounting is idempotent per dispatch; TC-3 gives the crash a named disposition; T-08, T-09, T-27 |
| R-7 | Two Coordinators both resume the same paused run | High | WU-2's run-scoped claim + lease fence; WU-6's takeover; T-15 |
| R-8 | The durable checkpointer OS-31 now **requires** could pull in a new pinned dependency, touching packaging and the offline-wheel story — or, taken in-repository, becomes a checkpoint store this repository must maintain | Medium *(re-scoped in iteration 2; the dependency half is eliminated, the maintenance half is accepted)* | OD-4 chooses the in-repository `BaseCheckpointSaver` over the **already-pinned** `langgraph-checkpoint==2.1.1`, so `requirements-langgraph.txt` gains no pin and `INSTALL.md:250-256` is unchanged by a dependency. The maintenance cost is paid down by WU-2(a)'s test obligations (full-field round-trip T-42, head resolution, concurrent writers, corrupt-store refusal) and by reusing the `runtime_state.py` flock/atomic-replace discipline rather than inventing a second one. WU-13 owns the new-engine-file manifest and mirror consequences |
| R-20 *(new, PLAN iter 2)* | The durable checkpoint and the Tier-2 index record drift apart, and a resume silently prefers whichever is convenient — re-creating, in a subtler form, the competing-authority defect F-001 identified | **High** | D2 fixes one authority in writing and gives the four consistency rules C1–C4 named closed reason codes; C3 makes divergence a **refusal**, never a preference; T-43 proves reconstruction ignores the projection, T-44 proves divergence and stale heads are refused, T-45 proves the C1/C4 ordering and that repair only ever flows checkpoint → record |
| R-21 *(new, PLAN iter 2)* | Installing a durable checkpointer by default changes the environment of the six existing test files that call `launcher.execute_state` with none, and of `run_cli`'s demo path | Medium | WU-2(c) re-verifies all six call sites (`test_deterministic_workflow_launcher.py`, `_recovery.py`, `_ownership.py`, `_malformed.py`, `_round2.py`, `_lease_keeper.py` **[verified in PLAN]**) inside its own unit, with a temp-directory default; the 24 existing `MemorySaver` uses stay valid as an explicit override; any breakage is fixed in WU-2, not discovered in WU-14 |
| R-9 | The live 1.4.196 regression cannot run here; fabricating evidence would be a G5 violation | **High** (evidence integrity) | OD-2 chooses the offline contract regression and requires the report to state plainly what was and was not evidenced; T-36 |
| R-10 | Forgetting the `tools/` mirror fails `validate_skills.py` late | Medium | Mirroring is a completion criterion of **every** engine-touching unit, not only WU-13 |
| R-11 | Scope creep into a general Orca-independent CLI while building discovery and resume | Medium | WU-6 is capped at exactly two verbs; restated in `## Non-Goals` |
| R-12 | Scope creep into OS-37 process/PTY ownership | Medium | WU-4 is confined to the settlement/ownership port; the settlement-recollection window stays OS-37's (`orca_adapter.py:36-38`) |
| R-13 | Cancel implemented as an OS-30 item cancel or a `worker-abandon` and mistaken for a run-level cancel | **High** | WU-10 treats X1 as *one step of* X3 and X2 as the TC-3-only recovery; T-24, T-25, T-30 assert the run-level facts |
| R-14 | A cancelled run stays resumable because the pause record was never marked terminal | **High** | WU-10 marks the record terminal under the run-scoped claim; T-33 |
| R-15 | Cancel given resume's currency check (stale run uncancellable) or resume given cancel's (stale answer applied) | High | D2 + WU-10 state both rules explicitly and oppositely; T-19 (resume refuses) and T-26 (cancel succeeds) are the paired proofs |
| R-16 | An `any(...)`-style reader reports a cancelled run as its pre-cancel status | Med-High | WU-11 audits every reader and fixes `verify_full_workflow_example.py:262-267`; T-30 |
| R-17 | Cancel/abandon leaves `.timing_state.json` scopes open or writes a blank `started_at` — the OS-19 defect the tracker exists to prevent | Medium | WU-10 step (v); T-31 |
| R-18 | After a run cancel, answering another open item republishes a request against the cancelled run | Med-High | WU-12; T-25's sibling assertion and TT-7 |
| R-19 *(new, PLAN)* | The deliberate edits to `test_deterministic_workflow_graph.py:101`/`:133` and `test_deterministic_workflow_contracts.py:45` are mistaken for a regression, or silently delete coverage of the still-valid "block" path | Medium | They land in WU-5 alone, and the old assertion is **kept** for the pause-not-admissible branch rather than deleted |

### How the plan keeps the 2014 tests and 732 checks green

- **Additive vocabulary.** Nothing is removed from `ROUTE_TOKENS`, `TERMINAL_STATUSES` or
  `RUN_STATUS_VALUES`; the tuples stay closed and every existing value keeps its meaning. Existing
  routing behaviour for `COMPLETED` / `BLOCKED` / `ESCALATED` is untouched.
- **The one intentional behaviour change is isolated and named.** The decision axis stops going
  straight to `BLOCK` when a pause is admissible. Exactly three existing assertions encode the old
  behaviour (`test_deterministic_workflow_graph.py:101`, `:133`;
  `test_deterministic_workflow_contracts.py:45` **[verified in PLAN]**), and R-19 governs how they
  change. The `BLOCK` path itself remains and stays tested for the not-admissible case.
- **The second intentional behaviour change is the default checkpointer, and it is named too.**
  WU-2(c) makes durable checkpointing mandatory on the production graph path where today it is
  optional (`graph.py:262`, `launcher.py:121-122`, `:215`). Six test files call
  `launcher.execute_state` with no checkpointer **[verified in PLAN]** and are re-verified inside
  WU-2(c) against a temp-directory default; the 24 existing `MemorySaver` uses remain valid as an
  explicit override. This is R-21.
- **Per-unit gate.** Every unit runs the full suite plus `validate_skills.py` before it is considered
  done. A unit that reddens either is not done.
- **Parity in the same commit.** The engine mirror and `run_logging` mirror move with their source,
  so `validate_skills.py` never discovers a gap late.
- **Rollback.** Each unit is a separate reviewable commit on a feature branch, with no cross-unit
  squashing. Reverting is done in reverse dependency order; reverting WU-5 alone restores the
  terminal decision block, and WU-1's vocabulary is inert without it (new values become unreachable,
  not incorrect). Nothing in this plan rewrites history, edits an existing artifact, or migrates
  existing on-disk data — a run that never paused has no pause record and reads identically before
  and after.

---

## Completion Criteria

OS-31 is complete when all of the following hold:

1. Every one of the fourteen work units is landed, reviewed and green.
2. Every row of the traceability matrix maps to at least one landed unit and at least one passing
   test — no OS-31 Scope bullet, Acceptance Criterion, 핵심 요구사항 or 필수 검증 item is unmapped.
3. `python3 -m unittest discover -s scripts -p 'test_*.py'` is green, with the skip count stated and
   explained.
4. `python3 scripts/validate_skills.py` PASSES with at least the baseline 732 checks, including
   engine byte parity, `run_logging` parity and the workflow-graph documentation contract.
5. `python3 scripts/validate_workflow_graph_docs.py` prints `PASSED`.
6. The package/archive check and the source-installed parity check pass.
7. A paused run created by one process is discovered, adopted and resumed exactly once by a different
   process, on `FakeAdapter`, with no Orca — and the resumed `WorkflowState` is **reconstructed from
   the durable OS-40 checkpoint**, asserted field-for-field against the pre-exit state, with the
   index record's projected fields proven not to be the reconstruction input (T-43).
8. Duplicate, stale-response, stale-checkpoint and conflicting-response cases each fail closed with a
   distinct reason code, and none of them creates a duplicate Task/Dispatch or overwrites an artifact.
9. A resume after a changed head, artifact or policy cannot reach `COMPLETE` without the responsible
   phase re-passing, its phase Reviewer running at medium/high risk, and Final Adversarial Review
   passing against a current binding.
10. Cancel and abandon are expressible, replay-safe, terminal for discovery, and recorded in the
    append-only log and the timing evidence with a non-blank `started_at`.
11. The Orca 1.4.196 evidence statement matches OD-2 exactly: the offline contract regression is
    green and the report states plainly that no live-runtime evidence was produced, and why.
12. `SKILL.md` limitations L1/L3/L6/L7 are rewritten to be true, and no historical run or artifact
    under `artifacts/` was modified.
13. **Durable OS-40 checkpoint persistence actually ships and is actually installed.** The
    in-repository `BaseCheckpointSaver` (OD-4) exists, `run_cli` produces an on-disk checkpoint with
    no extra flags, `configurable.thread_id` is set unconditionally on the graph path, and a
    production graph cannot be constructed without a durable checkpointer (T-41, T-42). No completion
    criterion of this plan is satisfiable while the checkpointer is absent.
14. **The checkpoint↔index consistency rules hold.** C2 rejects a stale checkpoint head, C3 refuses a
    diverged projection without preferring either side, and C1/C4 repair only ever flows
    checkpoint → record (T-44, T-45).
15. **The no-LangGraph fallback is preserved and is not confused with authority.** With LangGraph
    absent the graph launcher still fails explicitly (`LANGGRAPH_DEPENDENCY_MISSING`, exit 3) and
    everything that is not the graph still works (T-37); the Tier-2 index store and the WU-3 policy
    module still import and function (T-35); and no document or test claims that this fallback
    supersedes checkpoint authority when LangGraph is present.

---

## Non-Goals (restating the ticket's out-of-scope list)

- **No arbitrary running-Worker process memory snapshot or restore.** Durability comes from the pause
  record and the artifacts, never from freezing a live process.
- **No timeout-based or automatic default decision.** No default is applied when there is no
  response; a timeout is never grounds for user authority (`SKILL.md:2443`, limitation L4 at
  `:2380`). OD-3 makes cancel and abandon explicitly human-initiated for the same reason.
- **No GUI, notification channel or transport-specific UI.** Requests and responses continue to move
  as OS-30 artifacts.
- **No full Orca-independent CLI orchestration.** WU-6 adds exactly two verbs — discover and resume —
  and no run administration surface beyond them.
- **No modification of any pre-existing historical run or artifact.** `artifacts/runs/run_8e8f9451ad44/`
  and every other historical run stay off-limits; this run writes only
  `artifacts/runs/run_c2166e75bb02/PLAN.md`.
- **No design or implementation in this phase.** Token names, record field names and types, node/edge
  topology and port signatures are DESIGN's; this plan fixes only the shape, the ordering and the
  blast radius.

---

## Review Feedback Resolution

Iteration 1 phase gate: `RESULT: FAIL`, one blocking finding. Iteration 2 resolves it. Nothing else
in the plan was rewritten; the fourteen work units, the execution order, the constraints, the risks,
the non-goals and the rest of the traceability matrix are preserved.

### F-001 — MAJOR, blocking — "The plan does not require OS-40's LangGraph checkpoint/state to be the durable basis for pause/resume." → **RESOLVED**

The finding is accepted in full and is not argued with. OS-31's 핵심 요구사항 states, as a direct
instruction, "OS-40의 LangGraph checkpoint/state를 durable pause/resume의 기준 상태로 사용한다".
Iteration 1's D2 declared a third, mutable run-scoped pause record the "sole source of truth" and any
LangGraph checkpoint an "optimisation". That inverted the requirement, and the Reviewer was right
that the plan could then satisfy every one of its own completion criteria without ever installing
durable checkpoint persistence — `build_graph(adapter, *, checkpointer=None, ...)` accepts none
(`graph.py:262`), `run_cli` supplies none (`launcher.py:215`), and `configurable.thread_id` is set
only when a checkpointer is passed (`launcher.py:121-122`). The old D2 is **withdrawn**, not softened.

Per the Reviewer's Required Action, item by item:

| Required action | Where it is resolved now |
|---|---|
| Make the OS-40 checkpointed `WorkflowState` the authoritative durable execution/resume state when LangGraph is available | **D2**, rewritten end to end: Tier 1 is the checkpoint and is named "the single authoritative durable execution/resume state"; resume "reconstructs `WorkflowState` from the checkpoint, never from the pause record" |
| Demote the run-scoped record to an index / coordination fence / projection rather than a competing authority | **D2 Tier 2**, which limits the record's own authoritative content to four things — discovery identity, claim/owner/lease/expiry, checkpoint pointer, terminal disposition — and declares every other field a projection; **WU-2(b)** requires that declaration to be carried in the module itself so no later reader mistakes it for authority |
| Require a production durable checkpointer (dependency or in-repository saver); do not leave it an open DESIGN choice | **OD-4**, a new recorded decision that resolves ANALYSIS U-2 here instead of deferring it: an in-repository `BaseCheckpointSaver` over the **already-pinned** `langgraph-checkpoint==2.1.1` (**[verified in PLAN]**: `langgraph.checkpoint` ships `['base','memory','serde']`, `BaseCheckpointSaver` and `JsonPlusSerializer` import, `langgraph.checkpoint.sqlite` does not exist). **WU-2(a)** builds it and **WU-2(c)** installs it by default — `run_cli` constructs one, `execute_state` supplies one, `thread_id` is configured unconditionally, and a production graph without a durable checkpointer is refused at build time. DESIGN now fixes only the module name, the on-disk field names and the retention policy |
| Define checkpoint↔pause-record consistency and fail-closed recovery rules; disagreement must never silently pick one side | **D2 rules C1–C4**, a normative table with named closed reason codes: C1 the checkpoint commit is the commit point (`PAUSE_CHECKPOINT_MISSING`); C2 the pointer must be the thread head (`STALE_CHECKPOINT_HEAD`); C3 divergence is a **refusal** (`PAUSE_PROJECTION_DIVERGED`) that explicitly may not repair the checkpoint from the projection nor proceed on the checkpoint alone; C4 repair flows checkpoint → record only (`PAUSE_RECORD_MISSING`, repairable), never the reverse. **WU-7** enforces C2/C3 before any effect; **WU-6** enforces C1/C2/C4 before re-entering the graph |
| Add tests proving restart/resume is reconstructed from the checkpoint and stale checkpoint heads are rejected; update the test plan | Five new rows, **T-41 … T-45**: T-41 the checkpointer is installed by default and cannot be skipped; T-42 full-field round-trip, head resolution, concurrent writers, corrupt-store refusal; **T-43 restart/resume reconstructed FROM THE CHECKPOINT**, asserted field-for-field, with a second variant that mutates the record's projected fields to prove they were not read; **T-44 stale checkpoint heads rejected and divergence never silently resolved**; T-45 C1/C4 ordering and forward-only repair. **T-17** was rewritten from "stale checkpoint" to the specific C2 rule, and **T-03** and **T-35** were rewritten to stop attributing checkpoint duties to the index store |
| Preserve the explicit no-LangGraph fallback without letting it supersede checkpoint authority; state the boundary | **D2's authority rule**, quoted as a single normative paragraph, plus **Known Hard Constraint 8**, rewritten to say both halves: with LangGraph present the checkpoint is authority; with it absent the graph does not run at all (`launcher.require_runtime()`, `INSTALL.md:254-255`) and the index record degrades to a discovery/audit artifact for the Skill-document path, which never had execution state to be authoritative over. **Completion criterion 15** makes "no document or test claims the fallback supersedes checkpoint authority" a checkable condition |
| Fix every dependent artifact: WU-2, WU-13, traceability REQ-1, R-8, Completion Criterion 7; make the matrix honest | **WU-2** rewritten into parts (a) saver, (b) index/fence, (c) default wiring, each with its own "done when". **WU-5** now commits the checkpoint first (C1) and only projects into the record. **WU-6** rebuilds state from the checkpoint. **WU-7** enforces C2/C3. **WU-8** compares the *checkpointed* bindings. **WU-13**'s note no longer defers U-2 and no longer calls the checkpoint an optimisation; it states that no new pin is added and that the durable checkpoint is correctness. **REQ-1** no longer claims coverage from WU-2/WU-5 and T-03/T-10 — tests that validate the separate record — and now cites WU-2(a), WU-2(c), WU-5, WU-6, WU-7 against T-41 … T-45 and T-17, every one of which actually exercises checkpoint authority. **R-8** is re-scoped (the dependency half eliminated by OD-4, the maintenance half accepted and paid for by WU-2(a)'s tests) and **R-20** is added for the drift risk this correction itself creates. **Completion criterion 7** drops "no LangGraph-dependent step in the durable path" and requires reconstruction from the checkpoint; criteria **13, 14, 15** are added so no completion criterion is satisfiable while the checkpointer is absent |

**Two consequences this correction introduces, declared rather than hidden.** (1) A new risk
**R-20** — checkpoint and index drifting apart and a resume silently preferring one — which C1–C4 and
T-43/T-44/T-45 are the mitigation for. (2) A new risk **R-21** — making the checkpointer mandatory
changes the environment of the six existing test files that call `launcher.execute_state` with none
(**[verified in PLAN]**: `test_deterministic_workflow_launcher.py`, `_recovery.py`, `_ownership.py`,
`_malformed.py`, `_round2.py`, `_lease_keeper.py`), which WU-2(c) re-verifies inside its own unit
rather than leaving for WU-14 to discover.

**Scope discipline.** This correction added no process memory snapshot/restore, no timeout-based
default decision, no GUI or notification transport, no Orca-independent CLI orchestration beyond
WU-6's two verbs, and no edit to any historical run. The unit count is unchanged at fourteen and the
dependency graph is unchanged in shape.

### Non-blocking findings

The iteration 1 review recorded none.

---

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: Seven decisions are now recorded and all seven were decidable from authority this
phase already holds. D1, D2 and D3 were named by the phase contract and are each determined by
constraints the approved ANALYSIS established (FI-12/§9a for D1, FI-6/FI-2/M5 for D2, §8/S1+S3 and
FI-9 for D3). OD-1, OD-2 and OD-3 are the items ANALYSIS's `## Recommended Next Step` told PLAN to
resolve; each is decided by explicit ticket text (OD-1: the waiting state must not masquerade as a
failure; OD-3: "explicit" plus the no-timeout wall) or by the ticket's own scope wall combined with
observed environment facts (OD-2: the live runtime is 1.4.197, and point-verifying it is outside
OS-31's scope). OD-4, added in iteration 2, is decided by an explicit OS-31 requirement (the
checkpoint is the named basis state) combined with two observed environment facts (`langgraph-checkpoint
==2.1.1` is already pinned and ships `BaseCheckpointSaver` and `JsonPlusSerializer`; no durable saver
ships, `langgraph.checkpoint.sqlite` raising `ModuleNotFoundError`) — so requiring an in-repository
saver adds no dependency and needs no user authority. Iteration 2's rewrite of D2 was compelled by an
explicit requirement the Reviewer correctly cited, not by a preference: where the plan previously chose
between two defensible storage shapes, the ticket had already chosen, and the plan now follows it.
Model confidence, a recommended default, a timeout and the absence of a response
were not used as grounds for anything. Nothing in producing this plan required user authority: no
irreversible action was taken, no production code was changed, and every item this plan does not
decide is explicitly assigned to DESIGN rather than silently assumed.

---

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "PLAN",
  "iteration": 2,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "ACTUAL observed output at branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, never predicted. ITERATION 2 (CORRECTION round for F-001), all observed in this iteration after the correction was written: (1) `python3 -m unittest discover -s scripts -p 'test_*.py'` -> 'Ran 2014 tests in 344.235s' / 'OK (skipped=6)' / UNITTEST_EXIT=0. (2) `python3 scripts/validate_skills.py` -> 'Skill validation PASSED (732 checks)' / 'Validated both skills, shared templates/reviews, routing, and policy gates.' / VALIDATE_SKILLS_EXIT=0. Both counts are identical to the iteration 1 baseline, which is expected: this phase changed one artifact document and no production code, test or configuration; `git status` shows the tracked worktree clean with only untracked artifacts/ run directories. (3) The F-001 hole re-verified in code before rewriting D2: `scripts/deterministic_workflow/graph.py:262` declares `def build_graph(adapter, *, checkpointer: Any = None, ...)`; `scripts/deterministic_workflow/launcher.py:121-122` sets `config['configurable'] = {'thread_id': ...}` ONLY inside `if checkpointer is not None:`; `launcher.run_cli` (`:198-215`) constructs a FileRuntimeStateStore and a FakeAdapter and calls `execute_state(...)` with no checkpointer argument. So durable OS-40 checkpoint persistence is genuinely absent from every production path at this HEAD, exactly as the Reviewer stated. (4) OD-4 feasibility verified in this iteration: `requirements-langgraph.txt` already pins `langgraph-checkpoint==2.1.1` (line 3); `pip show langgraph-checkpoint` -> Version 2.1.1, 'Library with base interfaces for LangGraph checkpoint savers.'; `pkgutil.iter_modules(langgraph.checkpoint.__path__)` -> ['base', 'memory', 'serde']; `from langgraph.checkpoint.base import BaseCheckpointSaver` imports and exposes get_tuple/list/put/put_writes/get_next_version/delete_thread/config_specs/serde plus the async pair; `from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer` imports. Therefore an in-repository BaseCheckpointSaver adds NO new pinned dependency. (5) The ANALYSIS-carried fact that no durable saver ships was left unchanged: `langgraph.checkpoint.sqlite` -> ModuleNotFoundError. (6) R-21 blast radius measured in this iteration, not estimated: exactly six test files call `launcher.execute_state` -- test_deterministic_workflow_lease_keeper.py, _launcher.py, _ownership.py, _malformed.py, _round2.py, _recovery.py -- and `grep -c MemorySaver scripts/test_*.py` totals 24 uses. (7) Internal consistency of the corrected artifact checked programmatically: 45 test rows T-01..T-45 with no gaps, every T-nn cited in the traceability matrix defined in the test map, 14 work units WU-1..WU-14, 7 recorded decisions D1/D2/D3/OD-1/OD-2/OD-3/OD-4, exactly one DECISION_GATE_STATE line and exactly one decision-gate fence. ITERATION 1 evidence, carried forward unchanged and still true at this HEAD: `python3 -m unittest discover -s scripts -p 'test_*.py'` -> 'Ran 2014 tests in 339.873s' / 'OK (skipped=6)' / UNITTEST_EXIT=0; `python3 scripts/validate_skills.py` -> 'Skill validation PASSED (732 checks)' / VALIDATE_SKILLS_EXIT=0; D1 placement feasibility (deterministic_workflow.runtime_state imports with 'langgraph' not in sys.modules -> False); the two policing documents (validate_workflow_graph_docs.py requires one workflow-graph-contract block at SKILL.md line 159 matching WORKFLOW_ID/SCHEMA_VERSION/PHASES/ROUTE_TOKENS/TERMINAL_STATUSES and one workflow-control-plane block at line 173 whose declared graph_owned_decisions equal graph_spec.GRAPH_OWNED_DECISIONS, invoked from validate_skills.py:3006-3027 alongside byte-parity checks at :2974-3001 and :3003-3021); the three existing assertions encoding 'a decision block is terminal' at test_deterministic_workflow_graph.py:101, :133 and test_deterministic_workflow_contracts.py:45; and engine/mirror file sets identical by name (15 files each). All other repository facts relied on here are carried unchanged from the approved ANALYSIS.md (iteration 2, Reviewer PASS, 0 blocking findings), which was read and not modified.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "PLAN",
  "role": "worker",
  "verdict": "COMPLETE",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, tracked worktree clean (untracked artifacts/ run directories only)",
  "recorded_at": "2026-09-05T17:32:00Z",
  "boundary": "B2",
  "open_decision_item": false,
  "grounds": "No boundary in this correction round required user authority. PLAN produces a work plan; this iteration edited exactly one artifact document and wrote no production code, no test and no configuration, created no branch, staged nothing and pushed nothing. F-001 was resolved by following an explicit OS-31 requirement -- 'OS-40의 LangGraph checkpoint/state를 durable pause/resume의 기준 상태로 사용한다' -- which is a direct instruction about which state is authoritative, so the previously recorded D2 was withdrawn rather than defended. Where iteration 1 had treated the storage authority as a defensible design choice, the ticket had already decided it; following an existing instruction is not a boundary. OD-4, the one new decision, is determined by that same requirement combined with two facts observed in this iteration (langgraph-checkpoint==2.1.1 is already pinned and ships BaseCheckpointSaver and JsonPlusSerializer; no durable saver ships), so it adds no dependency, no cost and no lock-in that a user would need to authorise. Everything this plan still deliberately does not decide -- module and token names, on-disk field names and types, checkpoint retention/pruning policy, node/edge topology and port signatures -- is assigned to DESIGN in writing rather than assumed. Model confidence, worker/reviewer agreement, a recommended default, a timeout and the absence of a response were not used as grounds for anything. The subject matter of this ticket is durable pause/resume machinery for human decisions; that is not a property of this worker's own gate state.",
  "scope": "The PLAN artifact at artifacts/runs/run_c2166e75bb02/PLAN.md, updated in place for iteration 2: the OS-31 goal and scope walls, fourteen ordered work units with per-unit file targets and completion criteria, a requirement-to-work-unit traceability matrix covering every OS-31 Scope bullet, Acceptance Criterion, user requirement and required-validation item, seven recorded decisions (D1, D2, D3, OD-1, OD-2, OD-3 and the new OD-4), the execution order and its rationale, a forty-five-row test plan with file placement and no-Orca execution, thirteen known hard constraints, twenty-one risks with mitigations and a rollback story, fifteen completion criteria, the restated non-goals, and a new Review Feedback Resolution section recording per-finding what changed and where. The iteration 2 correction is confined to F-001: the durable-state authority decision D2 and its dependents WU-2, WU-5, WU-6, WU-7, WU-8, WU-13, traceability rows REQ-1/REQ-4/S3/S8/AC-2/AC-3/V8, test rows T-03/T-10/T-17/T-35 and new rows T-41..T-45, hard constraints 8 and 13, risks R-8/R-20/R-21, and completion criteria 7/13/14/15. It covers no production code change, no design-level API, and no historical run or artifact; artifacts/runs/run_8e8f9451ad44/ and REVIEW_PLAN.md were not modified.",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "This iteration modified one file in place, artifacts/runs/run_c2166e75bb02/PLAN.md, inside this run's own artifact directory. No production code, test, configuration or SKILL document was modified; no pre-existing historical run or artifact was modified, copied or cited, and artifacts/runs/run_8e8f9451ad44/ was treated as off-limits throughout. The approved ANALYSIS.md, both REVIEW_ANALYSIS files and REVIEW_PLAN.md were read and left unchanged. The effect on the run is that the plan now matches the ticket's explicit durable-state requirement: the OS-40 checkpointed WorkflowState is the authoritative execution/resume state when LangGraph is available, a production durable checkpointer is required rather than deferred, checkpoint-to-index consistency rules C1-C4 fail closed with named reason codes instead of silently preferring a side, five new test rows prove reconstruction happens from the checkpoint and that stale heads and diverged projections are rejected, and the no-LangGraph fallback is preserved with its boundary stated explicitly rather than used to justify demoting the checkpoint. Two new risks (R-20 drift, R-21 default-checkpointer blast radius) are declared rather than absorbed silently."
}
```
