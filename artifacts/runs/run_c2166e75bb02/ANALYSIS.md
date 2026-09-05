# Worker Result

STATUS: COMPLETE

**Run:** run_c2166e75bb02 · **Phase:** ANALYSIS · **Iteration:** 2 · **Role:** worker · **Round:** CORRECTION
**Repository:** /Users/luminous/aiAssistedProjects/orca-skills · branch `main` · HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35` · tracked worktree clean (only untracked `artifacts/` run dirs)
**Ticket:** Jira OS-31 — Implement Durable Pause and Resume for Human Decisions

Every claim below is tagged **[FACT]** (verified in this repository at this HEAD, with `file:line`
or observed command output) or **[INFERENCE]** (my reading of what those facts imply). No other
run directory was read, cited, or modified. `artifacts/runs/run_8e8f9451ad44/` was **not** used as
input.

**Iteration 2 (correction round).** Iteration 1 was reviewed and failed the phase gate on one
blocking finding, F-001 (missing requirement-level analysis of OS-31's explicit run cancel/abandon
path). This iteration **adds** §10, a failure-mode row, findings, risks, unknowns and a next-step
item, and updates the validation baseline. Nothing that the Reviewer checked and found supported has
been rewritten, weakened or deleted. See **## Review Feedback Resolution** at the end for the
per-finding map.

---

## Request Summary

OS-31 asks for a run that is stopped at `NEEDS_INPUT`/`CONFLICT` to become a **durable, named,
resumable lifecycle state** (`WAITING_FOR_INPUT`) instead of a terminal failure — surviving
Coordinator process exit, settling active Task/Dispatch and terminal ownership on the way in,
binding the pending question to run + phase + checkpoint/head + artifact digest, letting a *new*
Coordinator discover and resume it exactly once, and fail-closed on duplicate / stale / conflicting
responses and on changed source, policy or artifacts. Resume must not bypass the phase Reviewer or
the Final Adversarial Review gate. Cancel/abandon and append-only audit/timing evidence are in
scope. Process-memory snapshot/restore, timeout-based default decisions, GUI/notification
transports and a full Orca-independent CLI orchestration are explicitly out of scope.

This phase produces the evidence base only. **No design, no implementation, no production code
change was made.**

---

## Validation Baseline (ACTUAL observed output)

### `python3 scripts/validate_skills.py`

```
Skill validation PASSED (732 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
VALIDATE_SKILLS_EXIT=0
```

### `python3 -m unittest discover -s scripts -p 'test_*.py'`

```
Ran 2014 tests in 340.725s

OK (skipped=6)
```
**Iteration 2 re-run (ACTUAL observed output at the same HEAD, before any change in this
iteration):**
```
Ran 2014 tests in 338.780s

OK (skipped=6)
UNITTEST_EXIT=0
```
`python3 scripts/validate_skills.py` also re-run in iteration 2: `Skill validation PASSED (732
checks)` / `Validated both skills, shared templates/reviews, routing, and policy gates.` /
`VALIDATE_SKILLS_EXIT=0`.

Process exit code 0. No test failed and no test errored. This is the **baseline before any OS-31
change**. The 6 skips are opt-in cases, not failures: the `_langgraph_ok()` gates
(`test_deterministic_workflow_recovery.py:22-31`) do **not** fire here because LangGraph 0.2.76 is
installed, so the skips come from the real-runtime suite, which is opt-in behind `--orca-runtime`
(`test_orca_runtime.py:79,327,407,457`). Verified by running that module alone:
`python3 -m unittest test_orca_runtime -v` -> `Ran 7 tests`, `OK (skipped=6)`, with all six carrying
the identical reason `skipped 'requires --orca-runtime and a ready Orca runtime'` — that is every
one of the 6 skips in the full run.
**[INFERENCE]** That reinforces §9b: even a full green suite on this machine contains **no** live
Orca evidence.

### Runtime environment (observed, not assumed)

| probe | command | observed |
|---|---|---|
| LangGraph | `importlib.metadata.version("langgraph")` | `0.2.76` (matches the pin in `requirements-langgraph.txt:2`) |
| checkpoint lib | `importlib.metadata.version("langgraph-checkpoint")` | `2.1.1` |
| available savers | `pkgutil.iter_modules(langgraph.checkpoint.__path__)` | `['base', 'memory', 'serde']` |
| durable saver | `from langgraph.checkpoint.sqlite import SqliteSaver` | `ModuleNotFoundError: No module named 'langgraph.checkpoint.sqlite'` |
| Orca CLI | `orca --version` | `1.4.197` |
| Orca runtime | `orca status --json` → `.result.runtime.appVersion` | `1.4.197` |
| this terminal's env | `$ORCA_APP_VERSION` | `1.4.196` |

---

## Current State

### 1. How a NEEDS_INPUT / CONFLICT gate terminates a run TODAY, and what is lost on process exit

**[FACT] Two independent stacks reach a decision block, and both make it terminal.**

**(a) The OS-40 runtime-neutral engine.** `routing.route()` reads the decision axis second, right
after an existing terminal status, and emits `BLOCK`:

- `scripts/deterministic_workflow/routing.py:102` — `if state["decision_state"] in ("NEEDS_INPUT", "CONFLICT"): return "BLOCK"`
- `scripts/deterministic_workflow/routing.py:33` — the same test inside `phase_gate()`, so the
  block holds even before a Worker result exists.
- `ROUTE_TARGETS["BLOCK"] == "TERMINAL"` (`scripts/deterministic_workflow/graph_spec.py:17`), and
  `TERMINAL` has no outgoing edge (`graph_spec.py:46`, `graph.py:296` → `END`).
- `executor.terminal_node` then stamps `terminal_status = "BLOCKED"` and lifts the decision state
  straight into the reason code (`scripts/deterministic_workflow/executor.py:461`, `:465`), and
  clears `pending_role`, `pending_intent`, `pending_event` (`executor.py:474`).

**(b) The live Orca coordinator harness.** `OrcaRuntimeHarness.log_run_status` gates only
`COMPLETED` through `decision_gate.admit_head`; on refusal it logs `EVENT_DECISION_BLOCK` and
**recurses once into `BLOCKED`** (`scripts/orca_runtime_harness.py:2953-3002`). `BLOCKED` then
triggers `_publish_clarifications_for_terminal_block()` (`orca_runtime_harness.py:3005`, `:3017`),
which publishes the OS-30 request artifacts — and then the run is over.

**[FACT] Observed, not predicted.** Driving the engine's own pure functions at this HEAD:

```
1) route with NEEDS_INPUT -> BLOCK
2) terminal_status = BLOCKED reason = {'code': 'NEEDS_INPUT', 'message': 'NEEDS_INPUT', 'phase': 'ANALYSIS'}
3) route again from terminal -> BLOCK
4) route after answering (terminal_status still set) -> BLOCK
5) state with pending_role after terminal: REFUSED -> POST_TERMINAL_EVENT
6) clearing terminal_status by hand: ACCEPTED (no guard)
7) route after hand-clearing terminal_status -> PREPARE_WORKER
```

Lines 3–5 are the mechanism that makes the block **absorbing**: `route()` short-circuits on any
non-null `terminal_status` (`routing.py:101`), and `validate_state` refuses any state that carries
both a terminal status and a pending role/intent/event
(`scripts/deterministic_workflow/state.py:248-249`, error `POST_TERMINAL_EVENT`). So even after the
user answers and `decision_state` returns to `CLEAR`, the run cannot move.

**[FACT] `SKILL.md` states this as present fact, not a plan.** The OS-31 limitation block
`orca-worker-reviewer-orchestration/SKILL.md:2370-2385`:

- **L1** — "blocked run은 종료된다. 답을 주는 것은 재개가 아니라 새 run이다 (resume은 OS-31)."
- **L3** — OS-30 leaves an append-only response/change/cancel lineage, but "그 decision을 downstream
  run 재개로 연결하는 소비 lineage는 없다."
- **L7** — "live Orca 경로의 gate는 run을 연 Coordinator process 안에서만 bound된다. 새 process는
  fail-closed로 막힌다."

**[FACT] What is actually lost when the Coordinator process exits.** The shipped command line
never installs a checkpointer:

- `build_graph(adapter, *, checkpointer=None, ...)` — `scripts/deterministic_workflow/graph.py:262`.
- `launcher.execute_state(...)` only sets `configurable.thread_id` **if** a checkpointer was passed
  (`launcher.py:121-122`), and `run_cli` never passes one (`launcher.py:215`).
- `MemorySaver` appears **only in tests** — `test_deterministic_workflow_recovery.py:147,474`,
  `test_deterministic_workflow_malformed.py:151,179,297,343,438`, `test_deterministic_workflow_round2.py:155`.
  There is no production checkpointer anywhere in `scripts/`.

**[INFERENCE]** Therefore, today, on process exit the entire `WorkflowState` is lost: the phase
index, `phase_passes`, `phase_iterations` / `remaining_phase_budget`, `correction_queue`,
`processed_command_ids`, `processed_event_ids`, `logical_trace`, `repository_binding`,
`artifact_binding`, `decision_state` and `pending_clarification_id`. What survives is only:

1. the per-intent `FileRuntimeStateStore` ledger (`launcher.py:210-212`, `runtime_state.py`), which
   is keyed by `intent_id` and knows nothing about run-level phase progress; and
2. the on-disk artifacts — `ORCHESTRATOR_LOG.md`, `TIMING_LOG.md`, `decision_ledger/`, and the OS-30
   `clarification/` tree.

That gap — durable *per-intent* identity but **no durable run state** — is the single largest
missing piece for OS-31.

---

### 2. What durable state already exists, and what is missing for a real `WAITING_FOR_INPUT`

**[FACT] Exists — the field, and only the field.** `pending_clarification_id: str | None` is
declared in the closed `WorkflowState` (`state.py:33`), initialised to `None` (`state.py:63`),
type-checked as an optional string (`state.py:103`), and writable through exactly one typed command
`SET_CLARIFICATION` (`state.py:291`). Verified surface:

```
UPDATE_COMMANDS: ['CLEAR_PENDING', 'SET_ARTIFACT_BINDING', 'SET_CLARIFICATION', 'SET_DECISION', 'SET_REPOSITORY_BINDING']
typed-writable fields: ['artifact_binding', 'decision_reason_code', 'decision_state', 'intent_status',
                        'pending_clarification_id', 'pending_event', 'pending_intent', 'repository_binding']
```

**[FACT] Nothing reads it.** A repository-wide grep for `pending_clarification_id` outside
`artifacts/` returns only: the `WorkflowState` declaration, the initialiser, the optional-string
list, the `SET_CLARIFICATION` command, the mirrored installed copy under
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/state.py` (same four lines), and
one test that sets it (`test_deterministic_workflow_graph.py:135`). No routing, executor, launcher
or adapter code consults it. **[INFERENCE]** It is a reserved slot, not a mechanism.

**[FACT] Exists — durable per-intent idempotency.** `runtime_state.py` implements a real durable
`RuntimeStatePort`: a closed record shape `RECORD_KEYS` with `owner_id` / `lease_token` /
`lease_expires_at` / `last_heartbeat_at` (`runtime_state.py:72-76`), statuses `CLAIMED / EFFECTED /
SETTLED` (`:63`), claim outcomes `CREATED / RESUMED / ALREADY_SETTLED` (`:67`), an inter-process
`fcntl.flock` critical section (`:10-16`), and a lease that is a **fence** on
`record_receipt` / `settle` / `heartbeat` (`:24-34`). The launcher gives every run a real on-disk
ledger by default (`launcher.py:44-60`, `:206-212`).

**[FACT] Missing, item by item, for a real `WAITING_FOR_INPUT` lifecycle state:**

| # | missing | evidence |
|---|---|---|
| M1 | `WAITING_FOR_INPUT` is not a value anywhere. `TERMINAL_STATUSES = ("COMPLETED", "BLOCKED", "ESCALATED")` | `contracts.py:26` |
| M2 | It is not a route token either. `ROUTE_TOKENS` has nine members, none of them a pause | `contracts.py:21-25` |
| M3 | It is not a run status. `RUN_STATUS_VALUES = ("COMPLETED", "BLOCKED", "ERROR", "ESCALATED")`, fail-closed | `run_logging.py:116`, `run_logging.py:585-587`, `orca_runtime_harness.py:2931-2934` |
| M4 | No durable checkpointer ships. Pinned `langgraph-checkpoint 2.1.1` offers only `base`, `memory`, `serde` — observed above | `requirements-langgraph.txt:3`; observed `ModuleNotFoundError` for `langgraph.checkpoint.sqlite` |
| M5 | There is no *run-level* durable record at all — the ledger is keyed on `intent_id`, not on run/phase | `runtime_state.py:72-76`, `ports.py:77-87` |
| M6 | No pending-decision record binds a question to a resume point (see §5) | `clarification_protocol.py:398-407` |
| M7 | The terminal is absorbing and `terminal_status` has no legal un-set path (see §8, seam **S1**) | `state.py:248-249`, `routing.py:101` |

**[INFERENCE]** M4 is a genuine dependency decision, not a detail: a durable LangGraph checkpoint
needs either a new pinned dependency (`langgraph-checkpoint-sqlite`, which changes
`requirements-langgraph.txt`, `release_manifest.py:30` and the OS-41 packaging story) or an
in-repository `BaseCheckpointSaver` implementation over the same `flock` + atomic-replace pattern
`runtime_state.py` already proves out. **And because LangGraph is optional (§9), the durable pause
record cannot live *only* inside a LangGraph checkpoint** — otherwise the no-LangGraph environment
loses pause/resume entirely.

---

### 3. The engine/adapter seam (OS-27 ports): what exists, what is insufficient

**[FACT] Four protocols exist in `scripts/deterministic_workflow/ports.py`:**

| port | lines | wired into the engine? |
|---|---|---|
| `AgentExecutionPort` | `ports.py:16-24` | **Yes** — `executor.execute_intent_node` calls `start`/`settlement`/`resume` (`executor.py:102-104,161`) |
| `ExternalRecoveryPort` (optional `lookup`/`resume`) | `ports.py:27-41` | **Yes**, capability-gated (`executor.py:121-125,162-176`) |
| `RuntimeStatePort` | `ports.py:51-87` | **Yes**, and *required* — `build_graph` raises `IdempotencyPortRequired` without one (`graph.py:278`, `runtime_state.py:158-164`) |
| `HumanApprovalPort` | `ports.py:90-95` | **No.** Grep across `scripts/deterministic_workflow/*.py` and every `test_deterministic*.py` finds it declared once and imported/called **nowhere** |

**[FACT]** `"human_approval"` is listed in `CAPABILITIES` but deliberately excluded from
`BASE_CAPABILITIES` (`contracts.py:28-40`), and no routing or executor code ever tests for it —
unlike `EXTERNAL_LOOKUP` / `EXTERNAL_RESUME`, which are checked before use
(`executor.py:121`, `executor.py:162`).

**[FACT] `RuntimeStatePort.release` is defined and implemented but never called.** Declared
`ports.py:81`, implemented `runtime_state.py:469-477` ("Drop this owner's lease so a successor need
not wait for it to expire"). Grep for `.release(` across `scripts/deterministic_workflow/` returns
only those two definition sites — the executor never releases a lease; it relies on the
`LeaseKeeper` context manager stopping renewal and the lease then lapsing
(`lease_keeper.py:97-162`, `executor.py:213-216`).

**[FACT] Where the policy currently sits.** All pause-relevant policy is already engine-side and
pure: `routing.route()` (`routing.py:99-131`), `routing.phase_gate()` (`:32-43`),
`routing.final_gate()` (`:46-50`), and `executor.terminal_node` (`:447-476`). The Orca adapter is
correspondingly thin — `OrcaAdapter` only translates `create_task` / `run_existing_task` /
`task_status` / `worker-interrupt` (`orca_adapter.py:108-181`, `ORCA_PRIMITIVE_MAP:183-188`) and
never makes a workflow decision.

**[INFERENCE] What is insufficient for OS-31:**

- **P1.** `HumanApprovalPort` is a *type* at the seam, not a seam. Nothing in the engine can publish
  a request, observe a response, or fail closed on its absence. The ticket's "pause/resume policy is
  owned by the runtime-neutral engine, Orca lifecycle signals are translated in the adapter" is
  currently true only because the engine owns *nothing* about pause.
- **P2.** `RuntimeStatePort` is intent-scoped. A pause is a *run*-scoped fact (which run, which
  phase, which checkpoint/head, which question, who owns the resume). No port carries it. Either
  `RuntimeStatePort` grows a run-scoped record family, or a fifth port is needed; either way the
  boundary is a real design decision, not an implementation detail.
- **P3.** There is no port for **terminal/Task/Dispatch settlement on pause**. `AgentExecutionPort`
  has `interrupt` (`ports.py:23`, `orca_adapter.py:177-180` → `worker-interrupt`), but nothing that
  expresses "settle this dispatch and release terminal ownership" — see §4.
- **P4.** `OrcaAdapter` does **not** declare `external_resume` (`orca_adapter.py:31-38`, `:40-42`),
  with an explicit rationale: `worker_done` is delivered once, to the process that owns the run, and
  a settlement delivered to a dead process "cannot be re-collected through any documented Orca
  primitive". A resume by a *new* Coordinator therefore hits
  `IdempotencyRecoveryError("IDEMPOTENCY_RECOVERY_UNSUPPORTED")` (`executor.py:121-125`) for any
  intent that was still dispatched at pause time. **This is the strongest single argument that pause
  must settle every active dispatch *before* the run is allowed to enter `WAITING_FOR_INPUT`**, not
  leave one running and hope to re-adopt it.

---

### 4. Where Task/Dispatch settlement and terminal-ownership release must hook in on pause

**[FACT] The four-axis model** is defined in `orca-worker-reviewer-orchestration/SKILL.md:855-925`
and is asserted as a core invariant at `SKILL.md:2404` ("Settlement, worker-resource registration,
process liveness, and cleanup authority are four separate axes"):

- **(a) Dispatch outcome / settlement** (`SKILL.md:861-871`). Authoritative only via Task/Dispatch
  provenance. Settled requires **both** the expected Task ID *and* the expected Dispatch ID to
  match, an explicit `succeeded`/`failed` outcome, acceptance without lifecycle rejection, and a
  `completed`/`failed` outcome **with a completion timestamp** in provenance. Still `dispatched` is
  *not* settled. Ordering is fixed: duplicate-finalization gate → (a) verification → lifecycle
  mutation (`SKILL.md:867`).
- **(b) Supervised worker-resource registration** (`SKILL.md:873-877`). Four outcomes:
  reuse / retain / release / `unsupervised`. Presence of a supervised resource is **not** evidence
  of settlement, and its absence is uninformative in both directions.
- **(c1) Residual process liveness** (`SKILL.md:879-884`). Terminal lookup only. Eventually
  consistent — a closed terminal can read `live` for ~10s. Liveness confers **no** close authority.
- **(c2) Cleanup authority** (`SKILL.md:886-925`). `authorized` / `not_authorized` / `unknown`, and
  `authorized` only when **both** (i) the terminal role is close-eligible and (ii) provenance proves
  this dispatch owns it. Seven terminal roles and four origins (`SKILL.md:752-753`); the Coordinator
  never closes its own session, a setup terminal, or an adopted terminal (`SKILL.md:2407`).
  `unknown` is treated as `not_authorized` for the purpose of closing; the default is
  retain-and-report (`SKILL.md:913`).

**[FACT] The one legitimate exception for an *unsettled* dispatch** is the explicit recovery path:
`worker-abandon` then `worker-release` for a worker that vanished without `worker_done`
(`SKILL.md:869`). It is explicitly **not** accounted as settlement, contributes `worker_done` count
0, and does **not** promote the terminal to a close-eligible role.

**[FACT] The engine has no hook at any of these points.** `terminal_node` (`executor.py:447-476`)
performs *no* external call: it only rewrites state fields and clears `pending_intent` /
`pending_event` / `pending_role`. The only external-interrupt primitive is
`AgentExecutionPort.interrupt` → `orca orchestration worker-interrupt --dispatch <id> --reason`
(`ports.py:23`, `orca_adapter.py:177-180`), which is never invoked by the engine.

**[INFERENCE] What "no orphan / no ambiguous ownership" means against this model, and where the hook
must go.** Reading the ticket's acceptance criterion through the four axes, pause is only safe when,
for every dispatch the run created and has not yet accounted:

1. **(a) is decided, not assumed.** Either the dispatch is provably settled (both IDs + explicit
   outcome + completion timestamp), or it goes down the named recovery path
   (`worker-abandon` → `worker-release`) and is recorded as *recovered, not settled*. Silently
   leaving a `dispatched` row is precisely the "orphaned Task / active Dispatch" the ticket forbids.
2. **(b) is recorded** as one of reuse / retain / release / `unsupervised` — and never inferred from
   (a).
3. **(c1) is observed** independently, with the ~10s eventual-consistency caveat honoured: a
   liveness/listing mismatch is recorded as "already closed or not mine", never resolved by closing
   (`SKILL.md:884`).
4. **(c2) is evaluated** and produces `authorized` / `not_authorized` / `unknown` with its evidence.
   Anything short of `authorized` is retain-and-report; **"ambiguous ownership" is exactly the
   `unknown` verdict**, and a pause that leaves an `unknown` row unrecorded is the leak.

Structurally, the natural hook is a new node **before** the state is allowed to become
`WAITING_FOR_INPUT` — i.e. on the `BLOCK`-for-decision edge, replacing the direct hop to `TERMINAL`.
It must run through a port (so `fake_adapter` can exercise it with no Orca), it must be idempotent
against a crash *inside* pause, and its four per-dispatch outcomes must be persisted in the pause
record, because a new Coordinator has no other way to learn them. Note the engine's ledger already
has the right shape for the "already accounted" question: `processed_command_ids` /
`processed_event_ids` (`state.py:37-38`, uniqueness enforced at `state.py:246-247`) are the
duplicate-finalization gate's in-state analogue — but they die with the process today (§1).

---

### 5. How a pending clarification must bind — and what OS-30 records vs. does not

**[FACT] What OS-30 records.** A published request item's closed field set is
`ITEM_INPUT_FIELDS` (`clarification_protocol.py:398-401`) plus `decision_item_id`
(`:402`); the request envelope is `REQUEST_FIELDS` (`:403-407`). The binding fields are:

- `source_ledger_key` / `source_ledger_keys` — parsed by `_ledger_parts`
  (`clarification_protocol.py:384-388`) against the regex
  `(.+)/([a-z][a-z0-9_-]{0,63})/([1-9][0-9]*)/(B2|B3)#[1-9][0-9]*` with the first group required to
  equal `run_id`. So the key carries **run / phase / iteration / boundary / sequence** and nothing
  else. Same shape as `decision_gate.ledger_key` (`decision_gate.py:292-301`).
- `phase`, `iteration` — cross-checked against the parsed key (`clarification_protocol.py:418-419`).
- `decision_item_id` — `_identifier("item", "os30-item-v1", ["named", run_id, phase, open_item])` or
  `["producer", source_key]` (`clarification_protocol.py:391-394`).
- `revision` / `reclarifies_request_id` / `ambiguity_response_id` on the envelope
  (`:403-407`), which is how supersession and re-clarification are tracked.

**[FACT] What OS-30 explicitly does NOT record.** There is **no** `head_sha`, no `tree_digest`, no
`artifact_digest`, no `checkpoint_id`, no `thread_id` and no `intent_id` in either
`ITEM_INPUT_FIELDS` or `REQUEST_FIELDS`. The sets are closed and validated by `_closed(...)`
(`clarification_protocol.py:316-320`, applied at `:410` and `:465`), so an extra field is a
`SchemaMalformed`, not an extension point.

**[FACT] The OS-29 ledger record cannot carry the binding either.** `CLOSED_LEDGER_RECORD_FIELDS`
(`decision_gate.py:190-192`) is the union of thirteen required fields, six mechanics fields and
twenty-one contract-evidence fields; `_closed_field_defect` (`decision_gate.py:304-309`) rejects
anything outside it. `OS30_RESERVED_FIELDS` — `supersedes`, `superseded_by`, `request_id`,
`response_id`, `options`, `recommendation`, `answered_at`, `answered_by` — is named at
`decision_gate.py:196-205` precisely to document that they are **outside** the set: "OS-30's
supersession and request/response protocol fields are simply outside the set, so a record carrying
one is malformed and never silently accepted" (`decision_gate.py:186-189`).

**[FACT] `source_binding` exists but is not machine-checkable.** It is one of the thirteen required
fields (`decision_gate.py:152`) and its *presence* is enforced (`decision_gate.py:396-402`), but
`decision_policy.validate_record` (`decision_policy.py:1189-...`) never inspects its content — grep
for `source_binding` in `scripts/decision_policy.py` returns no hits. It is free prose.

**[INFERENCE] Consequence for OS-31.** The run↔phase↔head↔artifact binding that OS-31 needs
**cannot** be added to either the OS-29 ledger record or the OS-30 request/item schema without
breaking a deliberately closed contract that has explicit tests and prose defending it. It must live
in a **new, third record family** — a pending-decision / pause record — that *references* the OS-30
`request_id` / `decision_item_id` and the OS-29 `source_ledger_key`, and that additionally carries
the resume coordinates the engine owns:

- `run_id`, `thread_id`, `current_phase`, `phase_iteration`, `round_kind`, `pending_role`;
- the checkpoint identity (whatever durable checkpoint mechanism is chosen in §2/M4);
- `repository_binding` = `{head_sha, tree_digest, dirty}` — already normalized and validated
  (`contracts.py:110`, `:115-124`);
- `artifact_binding` = `{artifact_root_id, relative_path, digest, evidence_ids}` — likewise
  (`contracts.py:111`, `:127-140`);
- the four-axis settlement outcomes from §4;
- a policy digest, so a changed `decision_policy` invalidates the pending decision (§6, F5).

`contracts.binding_snapshot` (`contracts.py:162-164`) already produces exactly the normalized pair a
pause record should freeze.

---

### 6. The failure modes that must be fail-closed

For each, what exists today and what is missing.

| # | failure mode | what exists **[FACT]** | gap **[INFERENCE]** |
|---|---|---|---|
| F1 | **duplicate response** | OS-30 `_ingest_one` re-derives a deterministic `response_id` from `(request_id, decision_item_id, submission_id)` (`clarification_protocol.py:990`); if the directory exists it byte-compares record + raw + actor + provenance + timestamp + sensitivity and returns the existing record, else raises `ClarificationConflict` (`:1005-1021`). `_write_directory` is content-idempotent and raises on any divergence (`:343-350`). Cancel derives stable per-item child tokens so whole-request replay is exact (`:936-948`). | Idempotent at the *artifact* layer only. Nothing exists that makes **applying** a response to a run, and resuming it, exactly-once — see F-idem in §7. |
| F2 | **stale response** | OS-30 has a staleness axis: `_current_request` / `_current_item_ids` (`:853`, `:864`), `stale` flag on the response record (`:1001`), `StaleItem` (`:54`), status `STALE` / `STALE_REQUEST` (`:1024`, `:1054`). | Staleness is defined **only** against the request *revision*. It is blind to repository head, artifact digest, policy version and checkpoint — none of which OS-30 records (§5). A response can be perfectly "current" by revision and describe a tree that no longer exists. |
| F3 | **stale checkpoint** | Nothing. There is no durable checkpoint (§1) and therefore no notion of a checkpoint being stale. The nearest analogue is `role_binding_is_stale` (`executor.py:332-337`) → `STALE_REVIEW_BINDING` (`executor.py:386-387`), which compares a Reviewer intent's bindings to current state. | A whole mechanism. The pause record must pin the checkpoint identity, and resume must refuse a checkpoint that is not the head of the run's own thread. |
| F4 | **conflicting response** | OS-30 detects *identifier* conflicts (F1) and supersession (`decision_superseded` lineage event, `:1039-1043`), forbids a first decision on a cancelled item (`LineageInvalid`, `:986-987`, `:1032-1034`), and refuses a fork (`LineageFork`, `:58`). `terminal_block_sources` raises when folded judgements disagree (`clarification_protocol.py:151-153`). | Two *different* answers to the same item arriving to two racing Coordinators is not the same thing as artifact-level supersession. Nothing today arbitrates which one may resume the run — because nothing can resume the run. |
| F5 | **changed source / policy / artifact** | `role_binding_is_stale` (`executor.py:332-337`) and `final_review_binding_current` (`routing.py:57-68`) prove the pattern works inside one process. `decision_policy.load_decision_policy` parses the policy out of `SKILL.md`, so a `SKILL.md` edit silently changes the policy in force. | No policy digest is recorded anywhere. No head/artifact digest is bound to a pending decision (§5). Revalidation "from the responsible phase" has no trigger and no input. |
| F6 | **explicit cancel / abandon of the paused run** | Nothing at the run level. Two *other* operations exist and are not this one: OS-30 decision-**item** cancellation (`clarification_protocol.py:936-950`, CLI `:1305`, `:1334`), which writes clarification artifacts and never touches a run status; and Orca **worker** abandonment (`SKILL.md:869`; `orca_runtime_harness.py:3552-3571`), which recovers one unsettled Dispatch inside a live run. `RUN_STATUS_VALUES` refuses `CANCELLED`/`ABANDONED` (observed, §10d). | The whole X3 transition family — see §10. No run-level cancel identity, no run-scoped claim to arbitrate cancel-vs-resume, no disposal of the pending request/checkpoint/bindings, and no run *status* in which to record the outcome. |

**[FACT] The engine's existing fail-closed discipline is strong and should be the model.**
`validate_event` rejects a settlement bound to another intent/command, a malformed timestamp, a
digest mismatch or an event-identity mismatch (`contracts.py:192-200`), with the rejection codes
closed in `EVENT_REJECTION_CODES` (`contracts.py:102`) and never applied
(`executor.py:376-381`). `ExternalLookupUnavailable` is deliberately distinct from "no effect
exists": absence must be *proven* (`contracts.py:87-92`, `executor.py:167-176`).
`RuntimeStateCorrupt` refuses to read a corrupt ledger as an empty one (`runtime_state.py:125-130`).

---

### 7. Idempotency / exactly-once: which identities exist, and whether they suffice

**[FACT] The identities that exist:**

| identity | derivation | where |
|---|---|---|
| `command_id` | `stable_id("cmd", {workflow_id, run_id, phase, phase_iteration, final_review_iteration, role, round_kind, action_kind})` | `contracts.py:225-232` |
| `intent_id` | `stable_id("intent", {command_id, artifact_binding, repository_binding, payload_digest})` | `contracts.py:240` |
| `payload_digest` | sha256 over `{command_id, artifact_binding, repository_binding}` | `contracts.py:237` |
| `event_id` | pure function of the settlement digest; **`occurred_at` deliberately excluded** so a restarted process re-derives the same identity | `contracts.py:267-279` |
| `processed_command_ids` | in-state list, uniqueness enforced | `state.py:37`, `state.py:246-247`; appended `executor.py:418` |
| `processed_event_ids` | in-state list; a replayed event short-circuits before `validate_event` | `state.py:38`; checked `executor.py:322-323` |
| ledger record status/receipt/settlement | `CLAIMED`/`EFFECTED`/`SETTLED`, closed `RECEIPT_KEYS`, `RECEIPT_IDENTITY_KEYS` | `runtime_state.py:63`, `:87`, `:92` |
| lease token (fence) | minted only by `claim`, required by `record_receipt`/`settle`/`heartbeat` | `runtime_state.py:24-34`, `executor.py:195-198` |
| OS-30 `response_id` / `decision_id` | deterministic `_identifier` over `(request_id, item, submission_id)` and `(response_id, normalized)` | `clarification_protocol.py:990`, `:993` |

**[FACT] The recovery ladder is real and fails closed at every rung** (`executor.py:170-176`
docstring, `:139-176` code): adapter settlement → `EFFECTED` receipt → resume/collect →
capability-gated lookup → re-run **only** when the lookup *proves* no effect exists → otherwise
`IDEMPOTENCY_RECOVERY_BLOCKED`.

**[INFERENCE] Whether they suffice for resume: no, for three separable reasons.**

- **I1 — the identities are correct but not durable.** `processed_command_ids` and
  `processed_event_ids` live in `WorkflowState`, which no shipped path persists (§1). Across a
  process boundary the *engine* forgets what it processed; only the intent ledger remembers, and it
  is keyed per-intent, so it cannot answer "has this response already been applied to this run?"
- **I2 — `command_id`/`intent_id` are derived from state that resume can legitimately change.**
  `command_id` binds `phase_iteration` (`contracts.py:228`) and `intent_id` additionally binds
  `repository_binding` and `artifact_binding` (`contracts.py:235-240`). That is exactly right for
  *dispatch* idempotency — and exactly wrong as a *response-application* identity, because after the
  user answers, revalidation may legitimately move the head. A **separate** application/resume
  identity is required: something like a stable `resume_id` over
  `(run_id, request_id, decision_item_id, decision_id, pause_record_id)`, checked against a durable
  applied-set before any effect.
- **I3 — no arbitration for two Coordinators resuming one paused run.** `RuntimeStatePort.claim`
  gives single-writer semantics per **intent** (`runtime_state.py:8-22`, `ports.py:79`), and
  `_observe_then_take_over` (`executor.py:258-279`) gives an observer role with a bounded wait. That
  machinery is the right shape and should be reused, but there is no *run*-scoped claim, so nothing
  today prevents two new Coordinators both applying the same response and both resuming. That is the
  concrete mechanism behind the ticket's "동시 resume 경쟁" requirement.

---

### 8. How resume must NOT bypass phase review or the Final Adversarial Review gate

**[FACT] The gates the engine enforces today.**

- `phase_gate` (`routing.py:32-43`): a decision block short-circuits first; a Worker result must be
  `COMPLETE`; `IMPLEMENTATION`/`BUGFIX`/`REFACTORING` additionally require
  `unit_test_status == "PASS"`; and **only at `risk == "low"`** is the Worker result the gate — at
  medium/high a `reviewer_result` is mandatory.
- `final_gate` + completion (`routing.py:46-50`, `:112-114`): `COMPLETE` requires a Final Review
  `PASS` **and** `all_phase_passes_current(state)` **and** `final_review_binding_current(state)`.
- `final_review_binding_current` (`routing.py:57-68`) compares the recorded `reviewed_binding` to
  the *current* `repository_binding` and `artifact_binding`; a `PASS` recorded against a moved tree
  yields `STALE_FINAL_REVIEW_BINDING` (`executor.py:462-467`).
- `role_binding_is_stale` (`executor.py:332-337`) rejects a Reviewer settlement whose intent no
  longer describes current state, as `STALE_REVIEW_BINDING` (`executor.py:386-387`).
- `_pass_record` (`executor.py:340-358`) stores `reviewed_binding` on every phase pass.
- `SKILL.md` invariants: "Final Adversarial Review is mandatory and identical at every risk level"
  (`SKILL.md:2416`); "All requested phases PASS + Final Adversarial Review PASS required for
  COMPLETED" (`SKILL.md:2426`); "Current phase PASS required before next phase; at low risk the
  phase gate is the worker result" (`SKILL.md:2413`).

**[FACT] Three concrete bypass seams a resume implementation could open.**

**S1 — the terminal can be erased through the raw `update_state` path.** Observed above, steps 6–7:
setting `terminal_status = None` on a `BLOCKED` state is **accepted by `validate_state`** and the
next `route()` returns `PREPARE_WORKER`. The typed vocabulary cannot do this — `terminal_status` is
not in any `UPDATE_COMMANDS` entry (verified: typed-writable fields are the eight listed in §2) —
but `GuardedWorkflowGraph.update_state` accepts any mapping whose keys are inside
`CLOSED_STATE_FIELDS` and whose merge validates (`graph.py:143-146`, `:148-170`), and
`terminal_status` **is** a closed field (verified: `terminal_status in CLOSED_STATE_FIELDS → True`).
**[INFERENCE]** If resume is implemented as "clear the terminal", it is expressible by anyone with
graph access, unaudited, and it is the single most likely way the gates get bypassed. The pause
state must be a *non-terminal* state the graph can legitimately leave, not a terminal one that gets
un-set.

**S2 — `all_phase_passes_current` does not check currency, despite its name.** `routing.py:53-54`
is `all(state["phase_passes"].get(p) is not None for p in state["requested_phases"])` — a pure
presence test. The binding is stored on every pass record (`executor.py:357`) and a helper to read
it exists — `phase_pass_binding` (`routing.py:81-86`) — but grep across `scripts/` shows
`phase_pass_binding` is called by **no production code and no test**. `verify_final_review_binding`
(`routing.py:71-78`) is called only from `test_deterministic_workflow_round2.py:705,717,730,771`.
**[INFERENCE]** So the Final Review binding is checked for currency but the *phase* passes are not.
Today that is mostly masked because everything happens in one uninterrupted process; after a
durable pause, resume, and a head change during revalidation, a phase pass recorded against an old
tree would still satisfy completion. This is exactly the ticket's "resume 후 phase Reviewer 및 Final
Review 규칙이 우회되지 않는다" and it needs the currency check that `phase_pass_binding` was
evidently written for.

**S3 — the correction/revalidation machinery already models "re-enter the responsible phase", and
resume must route through it rather than around it.** `responsible_phases` (`routing.py:22-29`),
`downstream_revalidation_set` (`routing.py:14-19`, high-risk only), `active_correction_phase`
(`routing.py:89-96`), `advance_phase_node` (`executor.py:424-444`) and the
`PREPARE_CORRECTION` / `PREPARE_REVALIDATION` tokens are the existing, tested path for "go back to
the phase that owns this and re-validate everything downstream". **[INFERENCE]** OS-31's
"책임 phase부터 재검증" should be expressed in that vocabulary — a resume that changes the head or
the policy should produce a correction/revalidation round, not a fresh `PHASE_GATE` at the paused
phase. Note `prepare_intent_node` already clears `worker_result`/`reviewer_result` on every Worker
round (`executor.py:85`), so re-entry does not inherit a stale Reviewer verdict.

---

### 9. Constraints: LangGraph optional, and the real Orca version situation

#### 9a. LangGraph is optional — and there is **no** alternative execution fallback

**[FACT]** `requirements-langgraph.txt` is described as "Optional for legacy Skill validation"
(line 1) and pins `langgraph==0.2.76`, `langgraph-checkpoint==2.1.1`, `langgraph-sdk==0.1.74`,
`langchain-core==0.3.80`, `langsmith==0.3.45`.

**[FACT]** `launcher.require_runtime()` (`launcher.py:242-256`) raises
`LANGGRAPH_DEPENDENCY_MISSING` when the import fails and `LANGGRAPH_VERSION_UNSUPPORTED` for any
version other than `0.2.76` — an exact string comparison. `INSTALL.md:254-255` states it plainly:
"The command fails explicitly when LangGraph is absent or not version 0.2.76; **it does not use the
prompt loop as a fallback**." `graph.py:9` imports `langgraph.graph` at module top level.

**[FACT]** 22 test declarations across the eight `test_deterministic_workflow_*.py` files are gated
on a `_langgraph_ok()` helper (`test_deterministic_workflow_recovery.py:22-31`), so the suite skips
rather than fails without LangGraph — the `ssssss` run of skips is visible in the observed suite
output.

**[INFERENCE]** "The existing no-LangGraph fallback behaviour" that OS-31 must not break is
therefore precisely this: **the graph engine refuses to run (exit code 3, `INSTALL.md:262`), while
everything else keeps working with no LangGraph at all** — `run_logging.py`,
`clarification_protocol.py`, `decision_gate.py`, `decision_policy.py`, `orca_runtime_harness.py`,
`validate_skills.py` (which passed 732 checks in this environment independently of the graph), and
the Skill-document prompt-driven orchestration path. **This is the constraint that forces the
durable pause record to be a first-class artifact rather than a LangGraph checkpoint feature.** If
`WAITING_FOR_INPUT` only exists inside a LangGraph checkpoint, a no-LangGraph coordinator can
neither create nor discover a paused run — and the Skill-document path is the one a real Coordinator
actually runs today.

#### 9b. The Orca version situation — verified, not assumed

**[FACT] The three numbers disagree, and I checked all three:**

- `$ORCA_APP_VERSION` in this terminal: `1.4.196`
- `orca --version`: `1.4.197`
- `orca status --json` → `.result.runtime.appVersion`: **`1.4.197`**

**[FACT] The harness reads the *runtime's* report, not the env var.** `preflight()` calls
`self.call("status")["result"]` and passes `status["runtime"]["appVersion"]` to
`validate_orca_contract` (`orca_runtime_harness.py:1732-1748`), then records it only on the far side
of that check (`:1753`). `$ORCA_APP_VERSION` is not read anywhere in `orca_runtime_harness.py`.

**[FACT] Membership is exact-string, deliberately not an ordering comparison.**
`SUPPORTED_ORCA_APP_VERSIONS = ("1.4.196",)` (`orca_runtime_harness.py:249`), enforced at
`validate_orca_contract` (`:458-463`). The comment at `:236-238` names this case explicitly: "an
unverified 1.4.190 or **1.4.197** still fails closed even though it sits between / after observed
points." `HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS = ("1.4.178-rc.2", "1.4.184")` (`:266`) grants
nothing and gates nothing.

**[FACT] Observed, executed against the live runtime at this HEAD:**

```
live appVersion = 1.4.197
SUPPORTED       = ('1.4.196',)
contract: REFUSED -> runtime harness point-verifies Orca 1.4.196; installed runtime is 1.4.197
```

**[INFERENCE] This is a real, blocking constraint on the ticket's "Orca 1.4.196 compatibility
regression" requirement, and it must be surfaced to PLAN rather than assumed away.** The live
machine cannot execute the real-runtime Step 4 suite at all: it is refused before any dispatch. Only
three things are actually available here, and PLAN must choose among them explicitly:

1. Run the **offline** regression only — the unit/contract suite, which encodes the 1.4.196
   contract as data (`test_orca_runtime_contract.py:8957`, `:9014` asserts the tuple is exactly
   `("1.4.196",)`; `test_orca_runtime.py:675`, `:743`) — and state honestly that no live-runtime
   evidence was produced.
2. Obtain an actual Orca 1.4.196 runtime and run the real suite there.
3. Point-verify 1.4.197 and add it to `SUPPORTED_ORCA_APP_VERSIONS`. Per the comment at
   `orca_runtime_harness.py:239-241` this **requires** a fresh real-runtime run *of the revision
   that adds it*, plus the guide-grammar check — so it is a substantial piece of work and, on its
   face, **outside OS-31's stated scope**.

**[INFERENCE]** Option 1 is the only one OS-31 can perform unilaterally. Whether that satisfies the
ticket's "필수 검증" is a judgement PLAN must make and state, not something ANALYSIS should decide.
I record it as an open item rather than a decision gate on my own work, because it does not block
producing this analysis.

#### 9c. Packaging / source-installed parity

**[FACT]** The engine is mirrored into the installed Skill layout at
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/` and byte-equality is enforced:
`validate_deterministic_workflow_parity` compares the file **sets** and then every file's bytes
(`validate_skills.py:3003-3021`), and `validate_run_logging_tool_parity` does the same for
`tools/run_logging.py` (`:2974-3001`). `release_manifest.py:90-95` enumerates
`tools/run_logging.py`, `tools/clarification_protocol.py`, `tools/run_workflow.py` and every file
under `tools/deterministic_workflow/`, and `requirements-langgraph.txt` is a manifest entry
(`release_manifest.py:30`).

**[INFERENCE]** Every OS-31 file added under `scripts/deterministic_workflow/` must be mirrored into
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/` in the same change, or
`validate_skills.py` fails on "deterministic workflow source/installed file sets differ". Any new
runtime dependency (§2/M4) additionally touches `requirements-langgraph.txt` and therefore the
release manifest. A new top-level module under `scripts/` would need its own parity rule if the
installed Skill has to reach it.

---

### 10. Explicit run CANCEL / ABANDON — the ticket's own scope line, analyzed separately

OS-31's **Scope** list ends with `explicit cancel/abandon path`, and its **Acceptance Criteria**
include `pause/resume/cancel/error event가 append-only log와 timing evidence에 남는다` (Jira OS-31,
read at this HEAD). This section treats that requirement on its own, because the repository
contains two *other* operations that use the words "cancel" and "abandon" and neither of them is
the one the ticket asks for.

#### 10a. Three different operations, deliberately separated

| | operation | subject | who/what performs it today | terminates a run? |
|---|---|---|---|---|
| **X1** | OS-30 decision-**item** cancellation | one clarification *item* (and, via `--cancel`, every item of one *request*) | `python3 scripts/clarification_protocol.py respond --cancel` (`clarification_protocol.py:1305`, dispatched at `:1334`) | **No.** It writes response/decision/lineage artifacts only. |
| **X2** | Orca **worker** abandonment | one unsettled *Dispatch* / supervised worker resource | `orca orchestration worker-abandon --dispatch <id>` then `worker-release` (`SKILL.md:869`; executed at `orca_runtime_harness.py:3552-3560`, `:3566-3571`) | **No.** It recovers a resource inside a run that is still running. |
| **X3** | OS-31 paused-**run** cancel/abandon | the *run* itself, sitting in `WAITING_FOR_INPUT` | **nothing — it does not exist** | would be the whole point |

**[FACT]** X3 has no implementation and no vocabulary anywhere in the repository. A grep across
`scripts/` and `orca-worker-reviewer-orchestration/` for `run-cancel`, `run_cancel`, `cancel_run`,
`abandon_run` and `run-abandon` returns **zero hits**. Its precondition — the
`WAITING_FOR_INPUT` state itself — is also absent (§2, M1–M3).

#### 10b. What X1 (OS-30 item cancellation) actually does, and what it does not

**[FACT] What it does.**

- `ingest(... submission.cancel=True)` refuses an item selector and instead fans out over **every**
  item of the named request (`clarification_protocol.py:936-949`). Each child submission gets a
  deterministic id `_identifier("cancel", "os30-cancel-v1", [submission_id, decision_item_id])`
  (`:945-947`), so whole-request replay is byte-exact; the call returns
  `IngestResult(..., "CANCELLED")` (`:950`).
- `cancel` must be a member of `accepted_response_modes` (`:965`); the default published set is
  `["option_id","response_file","cancel"]` (`:838`).
- The normalization outcome is `CANCELLED`/`explicit_cancel` with no `decision_id` (`:1058`), and a
  `decision_cancelled` lineage event is appended, once, linked to the prior head
  (`:1041-1051`, validated at `:1197-1199`, `:1231-1236`).
- Cancellation is **irreversible for dependents**: `resolved_items` returns only items whose lineage
  state is `effective`, and its docstring says so explicitly — "`cancelled` is deliberately
  excluded: abandonment is irreversible, so a dependent of a cancelled question must never be
  promoted" (`clarification_protocol.py:673-679`). A cancelled item also cannot receive a first
  decision (`LineageInvalid`, `:985-987`, `:1029-1030`).

**[FACT] What it does not do.** The **only** non-test caller of `ingest` in the whole repository is
the standalone CLI's `respond` branch (`clarification_protocol.py:1334`; the mirrored installed copy
at `tools/clarification_protocol.py:1334` is the same line). It writes artifacts under the run's
`clarification/` tree and then calls `promote_pending` (`:1336-1338`). It never reads or writes
`ORCHESTRATOR_LOG.md`'s run status, never touches `RUN_STATUS_VALUES`, never consults a Task or
Dispatch, and never closes a terminal. `IngestResult.status == "CANCELLED"` is returned to stdout
and consumed by nobody: grep for `CANCELLED` across `scripts/*.py` and
`scripts/deterministic_workflow/*.py` outside `clarification_protocol.py` returns exactly one
unrelated hit (a fixture worker-state string, `test_orca_runtime_contract.py:9125`).

**[INFERENCE]** X1 cancels the *question*. By the time it can be invoked the run has already
terminated `BLOCKED` (§1b: `_publish_clarifications_for_terminal_block` is reached only from
`log_run_status("BLOCKED")`, `orca_runtime_harness.py:3004-3005`), so X1 is presently "cancel a
question belonging to a run that already ended" — it cannot be the ticket's run-level cancel, and
under OS-31 it becomes only *one step* of it (§10h).

**[FACT] A concrete X1-side hazard OS-31 inherits.** `promote_pending` "Promote from the PERSISTED
declarations, with no live coordinator … the next dependency-ready antichain becomes askable without
resuming the run" (`clarification_protocol.py:662-671`), and the CLI runs it after **every**
response, cancel included (`:1336-1338`). It reads no run status, because none is available to it.
**[INFERENCE]** Therefore, unless OS-31 gives cancel a durable run-level fact that this path can
see, cancelling a run and then answering some *other* still-open item can publish a brand-new
clarification request against an already-cancelled run.

#### 10c. What X2 (Orca worker abandonment) actually does, and what it does not

**[FACT]** `worker-abandon` is named in exactly one place in the contract: the *explicit recovery
path* for a worker that vanished without `worker_done` (`SKILL.md:869`). Its properties are stated
there and re-stated as a lifecycle-safety rule (`SKILL.md:928-930`):

- it is run **deliberately on a not-settled Dispatch**, preceded by the axis-(a) determination that
  the Dispatch is *not* settled;
- it is **not accounted as settlement**, contributes `worker_done` count **0**, and does **not**
  promote the terminal to a close-eligible role;
- it is followed by `worker-release`; a low-level Dispatch with no supervised worker resource is
  recovered by marking the Task `failed` instead, with no worker-resource lifecycle command.

**[FACT]** The harness implements exactly that: the exactly-once claim gate first
(`orca_runtime_harness.py:3508-3517`), then `worker-abandon` only when the observed worker state is
in `UNSETTLED_WORKER_STATES` (`:3552-3557`), then `worker-release` (`:3566-3571`), with the explicit
comment "No role promotion here: this dispatch never produced an accepted `worker_done`, so the
terminal stays `active_worker` and therefore stays never-close" (`:3568-3570`).

**[INFERENCE]** X2 is *within-run resource recovery*. It answers "this dispatch is unrecoverable —
account it as recovered, not settled". It says nothing about the run's lifecycle state, and it is
the wrong tool for X3 on its own — but it is the **only sanctioned primitive** for the residual
dispatches an abandon-from-crash-during-pause has to dispose of (§10g).

#### 10d. The closed run-status set, where it is enforced, and what it implies

**[FACT] Two enforcement points, both eager and fail-closed, plus a third at the CLI:**

- `RUN_STATUS_VALUES = ("COMPLETED", "BLOCKED", "ERROR", "ESCALATED")` (`run_logging.py:116`), with
  the rationale in the comment immediately above (`:112-115`): "A fifth value is not a stricter
  status this module invented — it is a caller passing something that is not one of the four the
  contract names, and that fails closed rather than being written as an unrecognized string a later
  reader has to guess about."
- `log_run_status` raises `RunLoggingError` before writing anything (`run_logging.py:570-588`).
- `OrcaRuntimeHarness.log_run_status` repeats the same check and does it **outside** `_safe_log`,
  because "an unrecognized status is a caller bug (a typo'd literal), not an I/O failure"
  (`orca_runtime_harness.py:2923-2936`).
- The logging CLI constrains the column too: `status.add_argument("--status", required=True,
  choices=RUN_STATUS_VALUES)` (`run_logging.py:3269`).

**[FACT] Observed at this HEAD, not predicted:**

```
RUN_STATUS_VALUES = ('COMPLETED', 'BLOCKED', 'ERROR', 'ESCALATED')
CANCELLED:         REFUSED -> unknown run status: 'CANCELLED'; expected one of (...)
ABANDONED:         REFUSED -> unknown run status: 'ABANDONED'; expected one of (...)
WAITING_FOR_INPUT: REFUSED -> unknown run status: 'WAITING_FOR_INPUT'; expected one of (...)
arbitrary event accepted -> ORCHESTRATOR_LOG.md          # event="run_cancelled" written fine
run_end rows after two log_run_status calls: 2
timing run_end rows: 2
```

**[FACT] What `log_run_status` writes** (`run_logging.py:589-611`): exactly one ORCHESTRATOR_LOG row
with `event="run_end"`, `result=<status>`, and exactly one TIMING_LOG row with `event="run_end"`,
`started_at=run_started_at`, `detail=<status>`. Both go through `_append_row`, which opens the file
in `"a"` mode and writes one line (`run_logging.py:331-335`) — there is no dedupe, no
"already ended" guard, and no rewrite.

**[FACT] The repository already has the reader rule that makes a *second* `run_end` legal.**
`SKILL.md:1566-1572`: "ORCHESTRATOR_LOG.md : run lifecycle provenance의 authority. append-only.
**run_end는 terminal이 아니다** — reader는 파일 전체를 읽고 마지막 run_status row를 authoritative
status로 삼는다. run_end 뒤의 row는 유효하며 run이 계속됐다는 뜻이고, 뒤의 run_end가 앞의 것을
대체한다." `validate_skills.py` enforces that this sentence exists
(`FINAL_REVIEW_AUDIT_RUN_END_ANCHOR = "run_end는 terminal이 아니다"`, `:2881`, checked at
`:2925-2932`) precisely so "a reader that stops at the first `run_end` reports a run that continued
as finished".

**[INFERENCE] The three implications for X3:**

1. **The event vocabulary is open; the status vocabulary is closed.** `--event` has no `choices`
   "by design" (`run_logging.py:144`) and the observation above confirms an arbitrary
   `run_cancelled` event row is accepted. So the ticket's *event* half — "pause/resume/cancel/error
   event가 append-only log와 timing evidence에 남는다" — is reachable **without** any schema change.
   The run *status* half is not: a cancelled/abandoned run cannot be recorded as such today.
2. **A cancel/abandon is architecturally a second `run_end`, and the contract already accommodates
   that**, because pause itself will have written a `run_end` row when the run stopped. The
   supersession semantics needed ("뒤의 run_end가 앞의 것을 대체한다") are already stated and already
   validator-anchored — this is the single largest piece of X3 that does *not* need to be invented.
3. **But at least one in-repo reader does not follow the rule.**
   `verify_full_workflow_example.py:262-267` uses `any(row["event"]=="run_end" and
   row["result"]=="COMPLETED" …)`, not "last `run_status` row wins". Every reader that gains a
   second `run_end` row must be audited against `SKILL.md:1566-1572`, and that is a test obligation,
   not a documentation one.

#### 10e. The missing transitions

**[INFERENCE]** With `WAITING_FOR_INPUT` absent (§2/M1–M3) and X3 absent, the transitions OS-31 must
add for cancel/abandon are these, and they are distinct from the pause and resume edges:

| id | transition | why it is separate |
|---|---|---|
| **TC-1** | `WAITING_FOR_INPUT` → *cancelled* (user-initiated, explicit) | The user answers "stop", not an option. It must be expressible **without** the run resuming — otherwise cancel is just a resume that then blocks again, and it re-enters the phase Reviewer path for no reason. |
| **TC-2** | `WAITING_FOR_INPUT` → *abandoned* (operator/GC-initiated) | The paused run is being discarded without any user decision at all: superseded by a newer run, environment gone, ticket withdrawn. Distinct from TC-1 because there is **no** OS-30 response to bind to and no decision to record. |
| **TC-3** | *crashed-mid-pause* → *abandoned* | The pause node itself is a multi-step effect (§4: settle each dispatch, then persist the pause record). A crash between step *n* and *n+1* leaves a run that is neither running nor legally paused (R-6). Abandon is the only disposition, and it is the one that must run the X2 recovery path over the residual dispatches. |
| **TC-4** | *cancel racing a resume* → exactly one wins | Two new Coordinators, one applying a response and one cancelling. Today there is no run-scoped claim at all (§7/I3), so nothing arbitrates this. |

**[INFERENCE]** TC-1/TC-2 are non-terminal→terminal edges in the engine's own vocabulary; they hit
the same closed tuples §2 lists — `ROUTE_TOKENS` (`contracts.py:21-25`), `TERMINAL_STATUSES`
(`contracts.py:26`), `RUN_STATUS_VALUES` (`run_logging.py:116`) — and are subject to
`graph_spec.validate_graph_spec` (`graph_spec.py:43`) exactly as the pause edge is. TC-3 has **no**
in-engine expression at all today, because it starts from a state the engine cannot represent.

#### 10f. Durable identity and idempotency for the cancel/abandon transition

**[FACT] The identity machinery that exists** is inventoried in §7. Two pieces are directly relevant
and neither fits:

- OS-30 already derives a **deterministic cancel identity** —
  `_identifier("cancel","os30-cancel-v1",[submission_id, decision_item_id])`
  (`clarification_protocol.py:945-947`) — and `_write_directory` is content-idempotent, raising on
  any divergence (`:343-350`). That makes X1 replay-exact. But the identity is **item**-scoped: it
  answers "was this item already cancelled", never "was this run already cancelled".
- `RuntimeStatePort.claim` gives single-writer semantics and a lease fence, with
  `ALREADY_SETTLED` as a first-class claim outcome that means "the settlement is durable; nothing
  external is needed" (`runtime_state.py:63-68`, `:412`, `:444-445`). This is exactly the right
  shape for "a second Coordinator arrives after the cancel already happened" — but the ledger is
  keyed on `intent_id` (`runtime_state.py:72-76`, `ports.py:77-87`), so it cannot hold a run-scoped
  fact (§2/M5, §3/P2).

**[INFERENCE] What X3 therefore requires:**

- **A stable `cancellation_id`** derived from run-scoped inputs only — something of the shape
  `stable_id("cancel", {run_id, pause_record_id, cancel_submission_id, cancel_kind})` where
  `cancel_kind ∈ {user_cancel, abandon}`. It must **not** bind `repository_binding`,
  `artifact_binding` or `phase_iteration` the way `intent_id` does (`contracts.py:225-240`), for the
  same reason §7/I2 gives for the resume-application identity: a cancel is valid regardless of
  whether the tree moved while the run was paused. (A cancel that *refused* because the head moved
  would be a cancel you cannot perform on a stale run — the opposite of the requirement.)
- **A run-scoped exclusive claim** over the pause record, reusing the `flock` + atomic-replace +
  closed-record + lease-fence discipline `runtime_state.py` already proves (`:10-16`, `:24-34`,
  `:72-76`), so TC-4 resolves to exactly one winner and the loser observes a settled outcome rather
  than performing a second cancel.
- **`ALREADY_CANCELLED` as a first-class outcome**, mirroring `ALREADY_SETTLED`: a new Coordinator
  that discovers an already-cancelled paused run must be able to say "nothing external is needed"
  and exit non-destructively, rather than re-running the disposal steps.
- **Replay-safety of the *effects*, not just the record.** The cancel's effects are the OS-30
  request cancellation (X1 — already replay-exact, `:936-949`), the residual-dispatch disposal (X2 —
  fenced by the exactly-once claim gate at `orca_runtime_harness.py:3508-3517`), and the log/timing
  rows (append-only and **not** deduped, observed §10d). The third is the one that is not
  idempotent by construction, so the applied-set must be consulted **before** the rows are written,
  not after.

#### 10g. Settlement and terminal-ownership obligations at cancel/abandon

**[INFERENCE]** The four-axis model (`SKILL.md:855-925`, invariant at `:2404`) applies unchanged;
what differs is *which dispatches are in scope*, and that differs by transition:

- **TC-1 / TC-2 (from a legally paused run).** §4/FI-5 establishes that pause is only safe if every
  dispatch was already accounted *before* the run entered `WAITING_FOR_INPUT` — `OrcaAdapter`
  withholds `external_resume` (`orca_adapter.py:31-38`) and a new Coordinator therefore fails closed
  with `IDEMPOTENCY_RECOVERY_UNSUPPORTED` (`executor.py:121-125`) on any still-dispatched intent.
  So at TC-1/TC-2 the correct obligation is to **verify and re-assert** the pause record's stored
  four-axis outcomes, not to re-derive them — and to fail closed if the record says a dispatch was
  left unaccounted, because that run was never legally paused.
- **TC-3 (from a crash inside pause).** Here dispatches *can* be unaccounted, and the X2 recovery
  path is mandatory and is the named exception to the settlement prohibition
  (`SKILL.md:869`, `:928-930`): axis (a) is determined read-only first, `worker-abandon` then
  `worker-release` for a supervised worker still in an unsettled state, `task-update --status
  failed` for an unsupervised low-level Dispatch (`orca_runtime_harness.py:3546-3571`). Each such
  dispatch is recorded as **recovered, not settled**, with `worker_done` count 0 and no role
  promotion (`:3568-3570`).
- **Terminal ownership, in all three.** (c2) is computed and recorded for every dispatch regardless
  of (c1) (`SKILL.md:912`); anything short of `authorized` is retain-and-report; `unknown` is
  treated as `not_authorized` for closing purposes and differs only in reporting duty
  (`SKILL.md:913`). Because X2 never promotes a terminal to a close-eligible role, **a
  TC-3 abandon closes no terminal at all** — every residual terminal stays `active_worker`, which is
  permanently `not_authorized` (`SKILL.md:900`). "No ambiguous ownership" at abandon therefore means
  *every residual terminal is recorded as retained with its (c2) verdict and its evidence*, not
  *every terminal is closed*.
- **The Coordinator's own terminal is never in scope**, at cancel as anywhere else
  (`SKILL.md:898`, `:2407`).

#### 10h. Disposition of the pending request and of the checkpoint / head / artifact binding

**[INFERENCE]** A cancel that leaves the OS-30 tree untouched produces two authorities that
disagree: the run log says cancelled, the clarification lineage says the question is still
unresolved, and `promote_pending` can still publish its dependents (§10b). So TC-1/TC-2 must, as
part of the same transition:

1. **Cancel the outstanding OS-30 request(s)** through the existing X1 path, so the append-only
   lineage carries a real `decision_cancelled` event per item (`clarification_protocol.py:1041-1051`)
   and `resolved_items` correctly refuses to promote dependents (`:673-679`). This is why X1 is a
   *step of* X3 rather than a substitute for it. Ordering matters: X1 is replay-exact, so it is safe
   to run before the (non-idempotent) log rows.
2. **Mark the pause record itself terminal**, under the run-scoped claim, so that discovery can
   never re-adopt it. This is the cancel-side form of §8/S1: if cancel is implemented as "write a
   terminal status and leave the pause record resumable", the discovery path can still resume a
   cancelled run — the same class of bypass as clearing `terminal_status` through the raw guarded
   `update_state` (`graph.py:143-170`, observed steps 6–7).
3. **Freeze, and never re-validate, the frozen bindings.** The pause record pins
   `repository_binding = {head_sha, tree_digest, dirty}` and `artifact_binding =
   {artifact_root_id, relative_path, digest, evidence_ids}` (`contracts.py:110-111`, `:115-140`,
   snapshotted by `binding_snapshot`, `:162-164`). Resume *must* re-check these (§6/F5). Cancel
   **must not**: a moved head is not a reason to refuse a cancel. The two edges therefore consume
   the same record under **different** currency rules, and conflating them makes a stale paused run
   uncancellable.
4. **Retire the checkpoint.** Whatever durable checkpoint mechanism §2/M4 selects, the cancel edge
   must mark it non-resumable rather than delete it — the artifacts are immutable
   (`clarification_protocol.py:343-350`; `run_logging` uses `rename`-onto-existing-directory, "NOT
   `os.replace`", `run_logging.py:1913-1919`) and there is no `force`/`--overwrite` anywhere
   (`run_logging.py:1046-1049`, `:1067-1071`).
5. **Close the timing scopes.** `RunTimingTracker` persists `open_phase`, `open_iteration`,
   `phase_started_at`, `iteration_started_at` and `run_started_at` to `.timing_state.json` under the
   run's own artifact root (`run_logging.py:57`, `:840-841`, `:843-853`, `:886-899`). A paused run
   has an open phase and iteration scope; a cancel that only writes `run_end` leaves them open
   forever. It is also the **only** place a new Coordinator can obtain `run_started_at`, which
   `log_run_status` takes as a caller-supplied parameter (`run_logging.py:570-578`, `:600-601`) —
   without it the cancel's TIMING_LOG `run_end` row has a blank `started_at` and a blank
   `duration_s`, which is precisely the OS-19 defect the tracker exists to prevent
   (`run_logging.py:620-646`).

#### 10i. Constraints and required tests this section adds

**[INFERENCE] Constraints (carried into `## Dependencies / Constraints` and `## Risks`):**

- **CC-1.** Cancel/abandon *events* need no schema change (`--event` has no `choices`,
  `run_logging.py:144`, observed). Cancel/abandon as a run **status** does need one, at
  `run_logging.py:116` plus both enforcement points (`:585`, `orca_runtime_harness.py:2931`) and the
  CLI `choices` (`:3269`). This is the same decision as **U-1** and must be answered once for pause,
  resume *and* cancel together — three states, one closed tuple.
- **CC-2.** A cancel/abandon writes a **second** `run_end` row. That is contract-legal
  (`SKILL.md:1566-1572`) and validator-anchored (`validate_skills.py:2881`, `:2925-2932`), but every
  reader must obey "last `run_status` row wins"; `verify_full_workflow_example.py:262-267` currently
  does not.
- **CC-3.** Cancel must never be expressible as a raw `update_state` write, for the same reason
  resume must not be (§8/S1, FI-8).
- **CC-4.** Cancel and resume consume the same pause record under different binding-currency rules
  (§10h.3). The design must state both, or a stale paused run becomes uncancellable.
- **CC-5.** X2's non-promotion property is load-bearing: an abandon closes **no** terminal
  (`orca_runtime_harness.py:3568-3570`, `SKILL.md:869`, `:900`).

**[INFERENCE] Required tests (they are additional to the ticket's eight named regression areas):**

- **TT-1.** `WAITING_FOR_INPUT` → cancelled, end to end, on `fake_adapter` with no Orca present.
- **TT-2.** `WAITING_FOR_INPUT` → abandoned with no OS-30 response in existence (TC-2), asserting
  that no decision record and no `decision_cancelled` event is fabricated for an unanswered item.
- **TT-3.** Crash-inside-pause → abandon (TC-3): residual dispatches disposed through
  `worker-abandon` → `worker-release`, accounted as *recovered, not settled*, `worker_done` count 0,
  and every residual terminal recorded `not_authorized`/retained.
- **TT-4.** Cancel replay: the same `cancellation_id` applied twice yields `ALREADY_CANCELLED`, one
  set of OS-30 cancel artifacts (byte-identical), and **no second pair of `run_end` rows**.
- **TT-5.** TC-4 race: two Coordinators, one resuming and one cancelling the same paused run —
  exactly one wins, the loser observes the settled outcome, and no duplicate Task/Dispatch is
  created.
- **TT-6.** A cancelled run cannot be resumed or re-discovered, including through the raw guarded
  `update_state` path (CC-3).
- **TT-7.** After a run cancel, `clarification_protocol.py respond`/`promote` publishes **no** new
  request for that run (§10b hazard).
- **TT-8.** Audit/timing shape: the cancel writes an ORCHESTRATOR_LOG cancel/abandon event row and a
  second `run_end` row whose status is authoritative under the last-row rule, plus a TIMING_LOG
  `run_end` row with a non-blank `started_at` recovered from `.timing_state.json`, and closed
  `phase_end`/`iteration_end` scopes.
- **TT-9.** Cancel succeeds when the repository head has moved since the pause (CC-4) — the case
  where resume would correctly refuse.

---

## Findings

Consolidated, ordered by how much they constrain PLAN/DESIGN.

**FI-1 — The decision block is an absorbing terminal in both stacks, by construction.**
`routing.py:101-102` + `state.py:248-249` (`POST_TERMINAL_EVENT`) + `run_logging.py:116` +
`orca_runtime_harness.py:2931-2934`. Observed. OS-31 cannot be built as "un-terminate a BLOCKED
run"; it needs a genuinely non-terminal state, which means new members in `TERMINAL_STATUSES` /
`ROUTE_TOKENS` / `RUN_STATUS_VALUES` or a new axis beside them. All three are closed, eagerly
validated tuples.

**FI-2 — No durable run state exists at all.** No production checkpointer
(`graph.py:262`, `launcher.py:215`; `MemorySaver` is test-only), and the pinned
`langgraph-checkpoint 2.1.1` ships no durable saver (observed `ModuleNotFoundError` for
`langgraph.checkpoint.sqlite`). The durable `RuntimeStatePort` is intent-scoped, not run-scoped.

**FI-3 — `pending_clarification_id` and `HumanApprovalPort` are declared and unused.**
`state.py:33,63,103,291` and `ports.py:90-95`. They mark the intended seam; neither is a mechanism.
`"human_approval"` is a capability name nothing checks (`contracts.py:39`).

**FI-4 — The engine performs no lifecycle settlement on block.** `terminal_node`
(`executor.py:447-476`) makes no external call; `AgentExecutionPort.interrupt` (`ports.py:23`) is
never invoked; `RuntimeStatePort.release` (`ports.py:81`, `runtime_state.py:469`) is never called.
The four-axis obligations (`SKILL.md:855-925`) have no code path in the engine.

**FI-5 — `OrcaAdapter` cannot re-collect a settlement across a process boundary, by design.**
`orca_adapter.py:31-38` withholds `external_resume`; the executor then fails closed with
`IDEMPOTENCY_RECOVERY_UNSUPPORTED` (`executor.py:121-125`). Any pause that leaves a dispatch running
is unrecoverable by a new Coordinator. Pause must settle first.

**FI-6 — The clarification↔resume binding cannot be added to either existing closed schema.**
OS-30 `ITEM_INPUT_FIELDS`/`REQUEST_FIELDS` (`clarification_protocol.py:398-407`) and OS-29
`CLOSED_LEDGER_RECORD_FIELDS` with its named `OS30_RESERVED_FIELDS` boundary
(`decision_gate.py:190-205`) both refuse it. A third record family is required.

**FI-7 — OS-30 staleness is revision-scoped only.** `_current_request` (`:853`), `StaleItem`
(`:54`). It cannot see head, artifact digest, policy or checkpoint — none of which it records.

**FI-8 — `terminal_status` is erasable through the raw guarded `update_state`.** Observed (steps
6–7). `graph.py:143-146` checks only field *names* against `CLOSED_STATE_FIELDS`, and
`terminal_status` is one; the typed `UPDATE_COMMANDS` correctly excludes it (`state.py:289-295`).
The bypass seam is the raw path, not the typed one.

**FI-9 — `all_phase_passes_current` checks presence, not currency, and the helper written for
currency is dead code.** `routing.py:53-54`; `phase_pass_binding` (`routing.py:81-86`) has zero
callers in `scripts/`; `verify_final_review_binding` (`routing.py:71-78`) has test-only callers.
Only the Final Review binding is currency-checked (`routing.py:112-114`).

**FI-10 — Dispatch identity and response-application identity are different things.** `command_id`
binds `phase_iteration` and `intent_id` binds the repository/artifact bindings
(`contracts.py:225-246`) — correct for dispatch, wrong as an "already applied this answer" key,
because revalidation legitimately moves those bindings.

**FI-11 — The live Orca runtime is 1.4.197 and the harness point-verifies only 1.4.196.**
Observed refusal. `orca_runtime_harness.py:249`, `:458`, and the comment at `:236-238` naming
1.4.197 specifically. The ticket's live 1.4.196 regression cannot be executed on this machine.

**FI-12 — No engine execution path exists without LangGraph, and that is deliberate.**
`launcher.py:242-256`, `INSTALL.md:254-255`, `graph.py:9`. The fallback to preserve is "everything
that is not the graph keeps working", which `validate_skills.py` (732 checks, PASSED) demonstrates.

**FI-13 — Engine/tools byte parity is compiler-enforced.** `validate_skills.py:3003-3021`,
`:2974-3001`, `release_manifest.py:30,90-95`.

**FI-14 — "Cancel" and "abandon" already exist in this repository and neither is the ticket's.**
OS-30 cancels a decision *item* (`clarification_protocol.py:936-950`; only non-test caller is the
CLI at `:1334`; result status consumed by nobody). Orca `worker-abandon` recovers one unsettled
*Dispatch* and is explicitly **not** settlement, contributes `worker_done` count 0 and promotes no
terminal role (`SKILL.md:869`, `:927-930`; `orca_runtime_harness.py:3552-3571`). A run-level
cancel/abandon does not exist: grep for `run-cancel` / `run_cancel` / `cancel_run` / `abandon_run` /
`run-abandon` across `scripts/` and the Skill returns zero hits. See §10a–§10c.

**FI-15 — The cancel/abandon *event* is free; the cancel/abandon *status* is a closed-tuple
change.** `--event` has no `choices` by design (`run_logging.py:144`) and an arbitrary
`run_cancelled` event row is accepted (observed). `RUN_STATUS_VALUES` (`run_logging.py:116`) refuses
`CANCELLED`, `ABANDONED` and `WAITING_FOR_INPUT` at three enforcement points
(`run_logging.py:585`, `orca_runtime_harness.py:2931`, CLI `choices` at `run_logging.py:3269`).
So the ticket's "cancel event in append-only log and timing evidence" is reachable today; recording
*that the run is cancelled* is not. This is the same closed tuple as **U-1** — pause, resume and
cancel must be decided together. See §10d.

**FI-16 — A cancel is architecturally a second `run_end`, and the contract already permits one.**
`log_run_status` appends and never dedupes; two calls produce two ORCHESTRATOR_LOG and two
TIMING_LOG `run_end` rows (observed; `run_logging.py:331-335`, `:589-611`). `SKILL.md:1566-1572`
already states the reader rule — "run_end는 terminal이 아니다 … 마지막 run_status row를
authoritative status로 삼는다 … 뒤의 run_end가 앞의 것을 대체한다" — and `validate_skills.py:2881`,
`:2925-2932` enforce that the sentence exists. But `verify_full_workflow_example.py:262-267` reads
with `any(...)` rather than last-row-wins, so reader conformance is a real test obligation, not a
documentation one. See §10d and CC-2.

**FI-17 — Cancel and resume need the same pause record under opposite currency rules, and cancel
needs identities the repository does not have.** Resume must refuse a moved head/artifact/policy
(§6/F5); cancel must **not**, or a stale paused run becomes uncancellable (§10h.3). Cancel needs a
run-scoped `cancellation_id` that deliberately excludes `repository_binding`/`artifact_binding`/
`phase_iteration` — unlike `intent_id` (`contracts.py:225-240`) — plus a run-scoped exclusive claim
to arbitrate cancel-vs-resume, which does not exist (`runtime_state.py` is intent-keyed,
`:72-76`), plus an `ALREADY_CANCELLED` outcome mirroring `ALREADY_SETTLED` (`runtime_state.py:67`,
`:444-445`). See §10e–§10f.

---

## Impact Scope

**Certain to change [INFERENCE], all under `scripts/deterministic_workflow/` plus its mirror:**

- `contracts.py` — a pause/resume vocabulary (state name, route token, reason codes, and the
  identity of a resume application). `TERMINAL_STATUSES` (`:26`) and `ROUTE_TOKENS` (`:21`) are
  closed and validated by `graph_spec.validate_graph_spec` (`graph_spec.py:43`), so any addition is
  a topology change that `validate_workflow_graph_docs` also checks (`validate_skills.py:3016-3021`).
- `state.py` — the durable pause fields, their validation, and the transition rules; plus a typed
  `UPDATE_COMMANDS` entry for resume so it is not expressible only through the raw path (FI-8).
- `routing.py` — the pause edge, the resume edge, the **cancel and abandon edges (TC-1..TC-4,
  §10e)**, and the currency check `phase_pass_binding` was written for (FI-9).
- `executor.py` — a pause node that settles dispatches and releases ownership before the state
  becomes `WAITING_FOR_INPUT`, a resume node that applies a response exactly once, and a
  cancel/abandon node that disposes the pending request, retires the checkpoint and closes the
  timing scopes exactly once (§10f–§10h).
- `graph.py` / `graph_spec.py` — new nodes/edges and the static topology assertions.
- `ports.py` — wiring `HumanApprovalPort` for real, and a run-scoped durable record port (or an
  extension of `RuntimeStatePort`).
- `runtime_state.py` — a run-scoped record family, or a sibling store reusing the same
  `flock` + atomic-replace + closed-record discipline.
- `orca_adapter.py` / `fake_adapter.py` — settlement/ownership translation for pause; `fake_adapter`
  must make the whole thing exercisable with no Orca present (an explicit ticket requirement).
- `launcher.py` — a durable checkpointer by default, discovery of paused runs, and a resume entry
  point; new exit-code semantics if a paused run is not `BLOCKED`.

**Likely to change:**

- `run_logging.py` — `RUN_STATUS_VALUES` (`:116`) if a paused **or cancelled/abandoned** run must
  be recorded as something other than the four existing statuses. The ORCHESTRATOR_LOG `--event`
  column has **no** `choices` by design (`run_logging.py:144` comment), so pause/resume/cancel
  *events* can be added without a schema change; the run *status* cannot (FI-15). A cancel also
  writes a **second** `run_end` pair, so every `run_end` reader must follow the last-row rule —
  `verify_full_workflow_example.py:262-267` currently does not (FI-16, CC-2).
- `orca-worker-reviewer-orchestration/SKILL.md` — L1/L3/L6/L7 (`:2377-2383`) become false and must
  be rewritten; the Core Invariants block (`:2387-2450`) needs the pause/resume invariants; and
  `validate_skills.py` asserts several of these anchors appear exactly once.
- `scripts/test_deterministic_workflow_*.py` — the ticket names eight required regression areas;
  §10i adds nine cancel/abandon-specific tests (TT-1..TT-9) on top of them.
- `scripts/clarification_protocol.py` — only if the run-cancel step must be reachable
  programmatically rather than through the standalone CLI (`:1334`), and to stop
  `promote_pending` (`:662-671`) publishing a new request for a cancelled run (§10b, TT-7).
- `orca-worker-reviewer-orchestration/tools/deterministic_workflow/**` — mandatory byte-identical
  mirror (FI-13).
- `INSTALL.md` — if a new pinned dependency or a new CLI verb appears.

**Explicitly must NOT change (ticket out-of-scope, and I verified none were touched):** any
pre-existing historical run or artifact under `artifacts/`.

---

## Dependencies / Constraints

- **OS-40 engine is the base state** (ticket requirement). Its closed-contract discipline —
  closed field sets, eager validation, digest-bound identity, capability-gated recovery — is the
  house style and must be matched, not bypassed.
- **OS-29 ledger is append-only and closed.** `decision_gate.py:190-205`; "The decision ledger is
  append-only; a correction is a new record and no published record is ever edited"
  (`SKILL.md:2449`).
- **OS-30 artifacts are immutable.** `_write_directory` (`clarification_protocol.py:343-350`) is
  content-idempotent and raises `ClarificationConflict` on divergence; `run_logging` uses
  `rename`-onto-existing-directory as the immutability guarantee, explicitly "NOT os.replace"
  (`run_logging.py:1913-1919`). Neither has a `force` or `--overwrite`
  (`run_logging.py:1046-1049`, `:1067-1071`).
- **OS-27 boundary:** engine owns pause/resume policy; the adapter only translates Orca lifecycle
  signals. `OrcaAdapter` is already a pure translator (`orca_adapter.py:108-181`) — keeping it that
  way is the constraint.
- **LangGraph optional, exactly `0.2.76`, no durable saver in the pin** (§9a, FI-2, FI-12).
- **POSIX only for the durable store:** `fcntl.flock`; on Windows the store raises
  `RuntimeStateLockUnavailable` at construction rather than degrading (`runtime_state.py:36-38`).
- **Orca 1.4.196 exact-string gate vs. a live 1.4.197 runtime** (§9b, FI-11).
- **Byte parity between `scripts/deterministic_workflow/` and the installed `tools/` mirror**
  (FI-13).
- **Cancel/abandon constraints CC-1..CC-5 (§10i).** CC-1: the cancel/abandon *event* is free, the
  cancel/abandon *status* is the same closed-tuple decision as U-1 and must be answered once for
  pause + resume + cancel. CC-2: a cancel writes a second `run_end`, which is contract-legal
  (`SKILL.md:1566-1572`, anchored by `validate_skills.py:2881`) but requires last-row-wins readers.
  CC-3: cancel must not be expressible as a raw `update_state` write (same seam as S1). CC-4: cancel
  and resume consume one pause record under opposite binding-currency rules. CC-5: an abandon closes
  **no** terminal, because `worker-abandon` never promotes a terminal to a close-eligible role
  (`orca_runtime_harness.py:3568-3570`, `SKILL.md:869`, `:900`).
- **Scope walls from the ticket:** no process-memory snapshot/restore; no timeout or auto-default
  decisions (reinforced by `SKILL.md:2443` — a timeout is never grounds for user authority, and
  `SKILL.md:2380` L4); no GUI/notification transport; no full Orca-independent CLI orchestration.

---

## Risks

| # | risk | evidence | severity **[INFERENCE]** |
|---|---|---|---|
| R-1 | Adding a fifth `RUN_STATUS` value is a lifecycle-contract change with an eager fail-closed validator and two callers | `run_logging.py:116,585`; `orca_runtime_harness.py:2931` | High |
| R-2 | Adding route tokens / terminal statuses trips the static topology and documentation validators unless `SKILL.md` is updated in the same change | `graph_spec.py:43-63`; `validate_skills.py:3016-3021` | Medium-High |
| R-3 | Resume implemented as "clear `terminal_status`" silently bypasses every gate and is reachable through the raw guarded `update_state` | observed steps 6–7; `graph.py:143-170` | **High** |
| R-4 | Phase passes are not currency-checked, so a post-resume head change leaves a stale pass satisfying completion | `routing.py:53-54`; `phase_pass_binding` dead | **High** |
| R-5 | Pausing without settling leaves a dispatch that no new Coordinator can re-collect | `orca_adapter.py:31-38`; `executor.py:121-125` | **High** |
| R-6 | A crash *inside* pause (between settling dispatch N and dispatch N+1) is a new crash window the existing ladder does not cover — the ladder is per-intent, pause is per-run | `executor.py:139-176` | High |
| R-7 | Two Coordinators discovering the same paused run both resume it | no run-scoped claim; `runtime_state.py` is intent-scoped | High |
| R-8 | A durable checkpointer is a new pinned dependency touching packaging and the offline-wheel story | observed missing sqlite saver; `INSTALL.md:250-256`; `release_manifest.py:30` | Medium-High |
| R-9 | The 1.4.196 live regression cannot be run here; producing "evidence" for it without a 1.4.196 runtime would be fabricated | observed refusal | **High** (evidence integrity, G5) |
| R-10 | Forgetting the `tools/` mirror fails `validate_skills.py` late, after the work looks done | `validate_skills.py:3003-3021` | Medium (cheap to avoid, easy to forget) |
| R-11 | Scope creep into a general Orca-independent CLI orchestration while building run discovery and resume | ticket scope wall | Medium |
| R-12 | Scope creep into OS-37 process/PTY ownership, which `orca_adapter.py:38` names as the owner of the settlement-recollection window | `orca_adapter.py:36-38` | Medium |
| R-13 | Cancel is implemented as an OS-30 item cancel (X1) or a `worker-abandon` (X2) and mistaken for a run-level cancel, leaving the run's own lifecycle unrecorded and the pause record still discoverable | §10a–§10c; `clarification_protocol.py:936-950`; `SKILL.md:869` | **High** |
| R-14 | A cancelled run is still resumable because the pause record was never marked terminal — the cancel-side form of S1 | §10h.2; `graph.py:143-170` | **High** |
| R-15 | Cancel is given resume's binding-currency check and a stale paused run becomes uncancellable; or resume is given cancel's, and a stale answer is applied | §10h.3; `contracts.py:110-140`; §6/F5 | High |
| R-16 | The second `run_end` row is read by an `any(...)`-style reader and a cancelled run reports as its pre-cancel status | `verify_full_workflow_example.py:262-267` vs. `SKILL.md:1566-1572` | Medium-High |
| R-17 | Cancel/abandon leaves `.timing_state.json` scopes open, or writes a `run_end` timing row with a blank `started_at` — the exact OS-19 defect the tracker exists to prevent | `run_logging.py:57,840-853,886-899`, `:620-646` | Medium |
| R-18 | After a run cancel, answering some other still-open item republishes a clarification request against the cancelled run | `clarification_protocol.py:662-671`, `:1336-1338` | Medium-High |

---

## Assumptions / Unknowns

Stated, not hidden.

- **U-1.** Whether OS-31 may add a fifth `RUN_STATUS_VALUES` member, or must express a paused run
  within the existing four (e.g. `BLOCKED` + a distinguishing reason, the way `DECISION_BLOCKED:…`
  already rides on `BLOCKED` — `decision_gate.py:104-105,128-131`). Both are defensible; the ticket
  says "명시적인 lifecycle state", which reads as favouring a new value, but the closed tuple has an
  explicit "a fifth value fails closed" rationale (`run_logging.py:112-116`). **DESIGN decides.**
- **U-2.** Whether the durable checkpoint is a new pinned dependency or an in-repository
  `BaseCheckpointSaver`. Unknown until DESIGN weighs the offline-wheel constraint
  (`INSTALL.md:253-254`) against maintenance cost.
- **U-3.** Whether the durable pause record is the *only* source of truth (so the no-LangGraph path
  works, §9a) with the checkpoint as an optimisation, or whether both must agree. My reading favours
  the former; I did not verify it against any design authority.
- **U-4.** The exact Orca CLI grammar for the settlement/ownership commands the pause hook needs
  (`worker-release`, `worker-retain`, `worker-abandon`, `worker-interrupt`). `SKILL.md:2396`
  forbids guessing it, and the version-matched guide must be loaded first. I did **not** load the
  guides and I make no claim about their grammar.
- **U-5.** Whether a 1.4.196 runtime is obtainable for the regression (§9b). Not something ANALYSIS
  can determine.
- **U-6.** Whether `phase_pass_binding` (`routing.py:81-86`) is dead by oversight or was
  deliberately left for OS-31. The docstring gives no hint; I report it as dead code, not as intent.
- **U-7.** I ran the suite and `validate_skills.py` on this machine's Python
  (3.11, Anaconda `common` env). I did not verify behaviour on any other interpreter or on a
  non-POSIX host, where `runtime_state.py:36-38` says the store refuses to construct.
- **U-8.** Whether cancel and abandon are **two** run outcomes or one outcome with a reason code.
  §10e argues TC-1 (user-initiated, has an OS-30 response to bind) and TC-2/TC-3 (no response
  exists) are genuinely different events; whether they need different *statuses* is downstream of
  U-1 and is **DESIGN's** call. I did not decide it.
- **U-9.** Who is allowed to invoke an abandon (TC-2/TC-3). The ticket says "explicit", which rules
  out a timeout (`SKILL.md:2443`, L4 at `:2380`), but it does not name an actor, and the repository
  has no operator-role concept to borrow. Recorded as an open item, not resolved.

---

## Recommended Next Step

Proceed to PLAN. The premises PLAN should carry forward, and the two it must resolve first:

1. **Carry forward:** a decision block is absorbing in both stacks (FI-1); there is no durable run
   state (FI-2); the clarification↔resume binding needs a third record family (FI-6); pause must
   settle dispatches before entering the paused state (FI-5); resume must not be expressible as
   erasing `terminal_status` (FI-8/R-3); phase-pass currency must be checked (FI-9/R-4); the
   no-LangGraph path must keep working, which forces the pause record to be an artifact rather than
   only a checkpoint (FI-12); **cancel/abandon is a third transition family, not a variant of
   pause or resume, and neither OS-30 item cancellation nor `worker-abandon` implements it
   (FI-14)**; **cancel is a second `run_end` under an already-stated reader rule (FI-16)**; and
   **cancel and resume read one pause record under opposite currency rules (FI-17/CC-4)**.
2. **Resolve first — U-1, now widened by U-8** (new `RUN_STATUS` value(s) vs. reason-carrying
   `BLOCKED`). It determines the blast radius across `run_logging.py`, `orca_runtime_harness.py`
   and `SKILL.md`, and every other design choice sits downstream of it. It must be answered **once
   for pause, resume, cancel and abandon together** (CC-1/FI-15): the same closed tuple gates all
   four, and deciding it for pause alone forces the decision twice.
3. **Resolve first — R-9/U-5** (how the "1.4.196 compatibility regression" will actually be
   evidenced on a machine whose runtime is 1.4.197, and whether that is acceptable). PLAN should
   state the chosen option explicitly rather than let TEST discover it.

4. **Plan the cancel/abandon path as its own work item, not as a footnote to resume.** It carries
   its own transitions (TC-1..TC-4, §10e), its own identity and claim requirements (§10f), its own
   settlement obligations — which differ between a legally paused run and a crashed-mid-pause run
   (§10g) — its own disposal duties for the pending request, checkpoint, bindings and timing scopes
   (§10h), and nine of its own tests (TT-1..TT-9, §10i). PLAN should also decide U-9 (who may
   invoke an abandon), because it has no answer in the repository today.

I recommend PLAN also explicitly re-affirms the scope walls, because run discovery + resume +
ownership settlement is exactly the shape of work that drifts into OS-37 and into a general
Orca-independent CLI (R-11, R-12). The cancel/abandon work is inside the ticket's scope — it is
named there verbatim — but it must not grow into a general run-management CLI.

---

## Review Feedback Resolution

Iteration 1 was reviewed independently and the ANALYSIS phase gate returned `RESULT: FAIL` with
exactly one blocking finding. This section states, per finding id, what changed and where.

### F-001 (G1, MAJOR, blocking) — "The analysis omits a requirement-level treatment of explicit run cancel/abandon." — **RESOLVED**

The Reviewer's Required Action was: add a distinct cancel/abandon analysis grounded in current
repository behavior; separate OS-30 decision-item cancellation, Orca worker abandonment and OS-31
paused-run cancel/abandon; identify the missing transition(s), durable identity/idempotency
behavior, settlement and terminal-ownership obligations, pending-request/checkpoint disposition, and
append-only audit/timing representation; and carry the constraints and tests into Findings, Risks
and Recommended Next Step.

| what the Required Action asked for | where it now is |
|---|---|
| separate the three operations | **§10a** (table X1/X2/X3), **§10b** (OS-30 item cancellation: what it does and does not do, with the only-non-test-caller fact), **§10c** (Orca worker abandonment: recovery, not settlement, no role promotion) |
| the closed run-status set and where it is enforced | **§10d** — `run_logging.py:116` with its rationale comment `:112-115`, the three enforcement points (`run_logging.py:585`, `orca_runtime_harness.py:2931`, CLI `choices` `run_logging.py:3269`), and the **observed** refusal of `CANCELLED`/`ABANDONED`/`WAITING_FOR_INPUT` |
| what that implies for the append-only audit and timing records | **§10d** implications 1–3 and **§10h.5** — the `--event` vocabulary is open (observed) while the status tuple is closed; `log_run_status` appends without dedupe (observed: two calls → two `run_end` rows in both logs); `SKILL.md:1566-1572` already states last-`run_status`-row-wins and `validate_skills.py:2881`, `:2925-2932` enforce it; `verify_full_workflow_example.py:262-267` does not follow it; `.timing_state.json` scopes must be closed and are the only source of `run_started_at` for a new Coordinator |
| which transition(s) are missing | **§10e** — TC-1 (user cancel), TC-2 (operator abandon), TC-3 (crashed-mid-pause abandon), TC-4 (cancel racing resume), plus the fact that a grep for every run-level cancel/abandon verb returns zero hits |
| durable identity and idempotency, incl. a new Coordinator doing it exactly once, replay-safe | **§10f** — a run-scoped `cancellation_id` that must **not** bind the repository/artifact bindings the way `intent_id` does (`contracts.py:225-240`); a run-scoped exclusive claim reusing `runtime_state.py`'s `flock`/lease discipline; `ALREADY_CANCELLED` mirroring `ALREADY_SETTLED` (`runtime_state.py:67`, `:444-445`); and the observation that the log rows are the one non-idempotent effect |
| settlement and terminal-ownership obligations against the four-axis model | **§10g** — TC-1/TC-2 verify and re-assert the pause record's stored outcomes (because §4/FI-5 requires pause to settle first); TC-3 must run the sanctioned `worker-abandon` → `worker-release` recovery and account it as recovered-not-settled; and an abandon closes **no** terminal, because the path never promotes a terminal role |
| disposition of the pending request and the checkpoint/head/artifact binding | **§10h** — cancel the outstanding OS-30 request(s) through the existing replay-exact path so the lineage carries `decision_cancelled`; mark the pause record terminal under the run-scoped claim so discovery cannot re-adopt it; freeze rather than re-validate the bindings (opposite of resume); retire rather than delete the checkpoint; close the timing scopes |
| carry the constraints and tests into Findings, Risks, Recommended Next Step | **Findings FI-14..FI-17**; **failure-mode row F6** in §6; **Constraints CC-1..CC-5** (§10i, restated in `## Dependencies / Constraints`); **Risks R-13..R-18**; **Unknowns U-8, U-9**; **`## Recommended Next Step`** items 1, 2 and the new item 4 |

**Scope discipline.** No new scope was taken on. This iteration adds no process-memory
snapshot/restore, no timeout-based default decision (U-9 explicitly rules a timeout out, citing
`SKILL.md:2443` and L4 at `:2380`), no GUI/notification transport, and no Orca-independent CLI
orchestration. No historical run was read, cited or edited; `artifacts/runs/run_8e8f9451ad44/`
remained off-limits. `REVIEW_ANALYSIS.md` was not modified. No production code, test or
configuration file was changed.

**Non-blocking findings:** the review reported none.

---

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: Every question in the phase contract, including the cancel/abandon requirement F-001
named, was answerable from the repository at HEAD `c279005` plus observed command output. Nothing in
producing this analysis or this correction required user authority: the open items I found (U-1
through U-9, R-9) are inputs *for PLAN/DESIGN to decide*, recorded as unknowns, and none of them
blocked me from completing the ANALYSIS deliverable. Per the phase contract, the subject matter
being pause/resume/cancel machinery does not make my own gate state anything other than CLEAR.

---

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "ANALYSIS",
  "iteration": 2,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "ACTUAL observed output, never predicted. (1) `python3 scripts/validate_skills.py` -> 'Skill validation PASSED (732 checks) / Validated both skills, shared templates/reviews, routing, and policy gates.' exit 0. (2) `python3 -m unittest discover -s scripts -p 'test_*.py'` -> see the Validation Baseline section of the artifact for the verbatim Ran/OK line. (3) Engine probe at HEAD: route() with decision_state=NEEDS_INPUT returns BLOCK; terminal_node yields terminal_status=BLOCKED reason code NEEDS_INPUT; route() from that terminal returns BLOCK even after decision_state is restored to CLEAR; validate_state refuses a pending_role beside a terminal status with POST_TERMINAL_EVENT; hand-clearing terminal_status is ACCEPTED and the next route() returns PREPARE_WORKER. (4) Live Orca: `orca status --json`.result.runtime.appVersion = 1.4.197 while SUPPORTED_ORCA_APP_VERSIONS = ('1.4.196',), and validate_orca_contract('1.4.197', ...) raised UnsupportedOrcaContract: 'runtime harness point-verifies Orca 1.4.196; installed runtime is 1.4.197'. (5) Runtime probe: langgraph 0.2.76, langgraph-checkpoint 2.1.1, langgraph.checkpoint modules = ['base','memory','serde'], `from langgraph.checkpoint.sqlite import SqliteSaver` -> ModuleNotFoundError. (6) Surface probe: typed UPDATE_COMMANDS write eight fields and terminal_status is not among them, while terminal_status IS in CLOSED_STATE_FIELDS and therefore reachable through the raw guarded update_state path. ITERATION 2 (correction round for F-001), all re-observed at the same HEAD before any change: (7) `python3 -m unittest discover -s scripts -p 'test_*.py'` -> 'Ran 2014 tests in 338.780s' / 'OK (skipped=6)' / UNITTEST_EXIT=0. (8) `python3 scripts/validate_skills.py` -> 'Skill validation PASSED (732 checks)' / 'Validated both skills, shared templates/reviews, routing, and policy gates.' / VALIDATE_SKILLS_EXIT=0. (9) run-status probe: RUN_STATUS_VALUES = ('COMPLETED','BLOCKED','ERROR','ESCALATED'); log_run_status REFUSED 'CANCELLED', 'ABANDONED' and 'WAITING_FOR_INPUT' with \"unknown run status: ...; expected one of (...)\"; log_orchestrator_event ACCEPTED an arbitrary event name 'run_cancelled'; two log_run_status calls appended 2 run_end rows to ORCHESTRATOR_LOG.md and 2 run_end rows to TIMING_LOG.md, i.e. no dedupe. (10) grep probe: 'run-cancel', 'run_cancel', 'cancel_run', 'abandon_run' and 'run-abandon' return zero hits across scripts/ and orca-worker-reviewer-orchestration/; the only non-test caller of ArtifactHumanApprovalPort.ingest is the standalone CLI at clarification_protocol.py:1334 (and its byte-identical installed mirror). (11) Jira OS-31 read at this HEAD: Scope contains the literal line 'explicit cancel/abandon path' and Acceptance Criteria contains 'pause/resume/cancel/error event\uac00 append-only log\uacfc timing evidence\uc5d0 \ub0a8\ub294\ub2e4.'",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "ANALYSIS",
  "role": "worker",
  "verdict": "COMPLETE",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, worktree clean for tracked files (untracked artifacts/ run directories only)",
  "recorded_at": "2026-09-05T07:09:10Z",
  "boundary": "B2",
  "open_decision_item": false,
  "grounds": "No boundary in this phase required user authority. ANALYSIS produces an evidence base; it designs nothing, implements nothing, and changed no production code. Every uncertainty encountered was recorded as an unknown for PLAN/DESIGN to decide (U-1 through U-7) or as a risk (R-1 through R-12) rather than resolved by an assumption of mine, so no decision was taken on the user's behalf. The subject matter of the ticket is pause/resume machinery; that is not a property of this worker's own gate state.",
  "scope": "The ANALYSIS artifact at artifacts/runs/run_c2166e75bb02/ANALYSIS.md as updated in place for iteration 2 (CORRECTION round resolving the single blocking finding F-001): the current-state, gap, seam, failure-mode, idempotency, gate-bypass, explicit run cancel/abandon (section 10), constraint, risk and unknown analysis for OS-31, grounded in scripts/deterministic_workflow/, scripts/decision_gate.py, scripts/decision_policy.py, scripts/clarification_protocol.py, scripts/run_logging.py, scripts/orca_runtime_harness.py, orca-worker-reviewer-orchestration/SKILL.md, INSTALL.md and the packaging/parity scripts, plus the observed validation baseline.",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "The run's own ANALYSIS.md was updated in place to add section 10 (explicit run cancel/abandon), one failure-mode row, findings FI-14..FI-17, constraints CC-1..CC-5, risks R-13..R-18, unknowns U-8..U-9, a Recommended Next Step item, an updated validation baseline and a Review Feedback Resolution section. No correct existing content was rewritten, weakened or deleted, and REVIEW_ANALYSIS.md was not touched. No production code, no test, no configuration and no pre-existing historical run or artifact was read as an approved baseline, modified, copied or cited; artifacts/runs/run_8e8f9451ad44/ was treated as off-limits input throughout. Nothing was committed, pushed or branched. The effect on the run is informational: PLAN and DESIGN start from a verified premise set instead of an assumed one, and two items are escalated to PLAN as things to settle before design begins -- whether a fifth RUN_STATUS value is permitted, and how the Orca 1.4.196 regression will be evidenced on a host whose live runtime reports 1.4.197."
}
```
