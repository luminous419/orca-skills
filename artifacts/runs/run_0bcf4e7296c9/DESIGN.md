# Worker Result

STATUS: COMPLETE
DECISION_GATE_STATE: CLEAR

## Summary / Requirements

OS-40은 `scripts/deterministic_workflow/`의 typed, JSON-safe state를 `langgraph==0.2.76` `StateGraph`로 실행한다. Graph의 node/conditional edge와 그 edge가 직접 호출하는 pure router가 workflow transition의 유일한 executable definition이다. Worker/Reviewer는 normalized result event만 만들며 phase 선택, retry, budget, decision block, downstream set D, final completion은 graph가 결정한다.

이 설계는 승인 PLAN의 결정을 따른다: 기존 `e2e_harness.py`에서 pure validation/calculation을 추출하되 imperative `run_workflow`는 한 release 동안 test-only parity oracle로 격리하고 production path에서 금지한다. Orca는 `AgentExecutionPort` 등의 첫 adapter이고 fake adapter는 Orca 없이 같은 graph를 실행한다. OS-28~30 schema와 historical artifacts는 변경하지 않으며 OS-31 durable resume 및 OS-37 direct CLI는 port extension으로만 남긴다.

## Current Architecture

- `e2e_harness.py:1925-2437`은 phase loop, 두 budget, T1~T5a와 D를 executable imperative code로 소유한다. 새 graph와 동시에 production engine으로 남기면 중복 transition engine이다.
- `orca_runtime_harness.py`의 `_exec_orca/call`, task/dispatch creation, typed wait, settlement/lifecycle primitives는 Orca I/O와 provenance에 유효하지만 graph core로 승격할 수 없다.
- `decision_policy.py`, `decision_gate.py`, `clarification_protocol.py`, `run_logging.py`, `quality_profile.py`, `workflow_contract.py`는 이미 deterministic schema/validation을 제공한다. 새 package는 schema를 복제하지 않고 compatibility façade에서 호출한다.
- release는 `scripts/`를 재귀 포함하지만 installed Skill은 자기 `tools/`만 갖는다. canonical `scripts/deterministic_workflow/`와 installed mirror `orca-worker-reviewer-orchestration/tools/deterministic_workflow/`의 exact-tree parity가 필요하다.
- 테스트는 `unittest`; baseline은 `python3 -m unittest discover -s scripts -p 'test_*.py'`의 1725 tests, `OK (skipped=6)`이다.

## Proposed Design

### 1. Package dependency direction

```text
contracts.py  <- state.py <- routing.py <- graph_spec.py <- graph.py
     ^             ^           ^                           |
     +---------- ports.py <-----+---------------------------+
                       ^
              executor.py (effect nodes only)
                 ^             ^
          fake_adapter.py  orca_adapter.py

migration.py -> legacy e2e result normalization only
```

`contracts/state/routing/graph_spec/ports` use only Python stdlib and existing stdlib-only repository modules. `graph.py` is the only core module importing LangGraph. `orca_adapter.py` alone may import `orca_runtime_harness`, `subprocess` indirectly, or Orca vocabulary. A static AST test rejects `orca`, `subprocess`, `terminal`, `session`, `credential`, `claude`, `codex` imports/types/field names in core checkpoint modules; textual occurrences in documentation/error explanations are excluded from the AST symbol scan.

### 2. Closed vocabulary

```python
Phase = Literal["ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST",
                "BUGFIX", "REFACTORING"]
Role = Literal["WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"]
RoundKind = Literal["PHASE_GATE", "CORRECTION", "DOWNSTREAM_REVALIDATION", "FINAL_REVIEW"]
NodeName = Literal["VALIDATE", "ROUTE", "ADVANCE_PHASE", "PREPARE_INTENT",
                   "EXECUTE_INTENT", "VALIDATE_SETTLEMENT", "APPLY_RESULT", "TERMINAL"]
ActionKind = Literal["RUN_AGENT", "WRITE_ARTIFACT", "REQUEST_CLARIFICATION"]
EventKind = Literal["AGENT_SETTLED", "ARTIFACT_STORED", "CLARIFICATION_PUBLISHED"]
WorkerStatus = Literal["COMPLETE", "BLOCKED"]
ReviewResult = Literal["PASS", "FAIL"]
QualityVerdict = Literal["PASS", "PASS_WITH_NOTES", "FAIL", "BLOCKED"]
DecisionState = Literal["CLEAR", "ASSUMPTION_ALLOWED", "NEEDS_INPUT", "CONFLICT"]
TerminalStatus = Literal["COMPLETED", "BLOCKED", "ESCALATED"]
IntentStatus = Literal["NONE", "PREPARED", "SETTLED"]
Risk = Literal["low", "medium", "high"]
```

Unknown strings never map to a default. Serialization uses schema version `os40.workflow.v1`, sorted-key compact JSON, UTF-8 and SHA-256.

### 3. Complete typed graph state

`WorkflowState(TypedDict)` has no reducers that append implicitly; each node returns a complete replacement for fields it owns. Tuple-shaped values serialize as JSON arrays and are reconstructed/validated at ingress.

| Field | Type | Default | Allowed values / invariant |
| --- | --- | --- | --- |
| `schema_version` | `str` | required | exactly `os40.workflow.v1` |
| `run_id` | `str` | required | `run_[a-z0-9]+`; immutable |
| `thread_id` | `str` | required | nonempty; LangGraph config `thread_id` must equal it |
| `workflow_id` | `str` | required | stable definition ID, exactly `os40.standard.v1` |
| `requested_phases` | `tuple[Phase, ...]` | required | nonempty; canonical sequential order or one supported specialized phase; immutable |
| `risk` | `Risk` | `high` | immutable per run |
| `max_iterations` | `int` | `5` | exact int, `1..10`, bool rejected; immutable |
| `adapter_capabilities` | `tuple[Capability, ...]` | required | sorted, unique, closed values; immutable snapshot validated before any intent |
| `current_phase_index` | `int` | `0` | `0 <= i < len(requested_phases)` until final review/terminal |
| `current_phase` | `Phase` | first phase | equals `requested_phases[current_phase_index]`; remains last phase during final review |
| `round_kind` | `RoundKind` | `PHASE_GATE` | consistent with role and correction/revalidation queues |
| `pending_role` | `Role | None` | `WORKER` | FINAL_REVIEW iff FINAL_REVIEWER; terminal iff None |
| `phase_iterations` | `dict[Phase, int]` | each requested→0 | keys exactly requested phases; `0..max`; counts gate attempts only |
| `final_review_iterations` | `int` | `0` | `0..max`; increments once per prepared fresh final reviewer intent |
| `remaining_phase_budget` | `dict[Phase, int]` | max each | derived and validated as `max_iterations - phase_iterations[p]` |
| `remaining_final_budget` | `int` | max | derived as `max_iterations - final_review_iterations` |
| `correction_queue` | `tuple[Phase, ...]` | `()` | unique requested phases, canonical upstream order |
| `correction_index` | `int` | `0` | `0..len(correction_queue)`; equality means the queue is fully consumed, zero when empty |
| `corrected_phases` | `tuple[Phase, ...]` | `()` | phases corrected in current final attempt, unique canonical order |
| `revalidation_queue` | `tuple[Phase, ...]` | `()` | exact D for HIGH; empty LOW/MEDIUM/specialized |
| `revalidation_index` | `int` | `0` | within queue, zero when empty |
| `phase_passes` | `dict[Phase, PhasePass | None]` | each requested→None | keys exactly requested; pass generation/phase iteration/tree digest must be current |
| `worker_result` | `WorkerResult | None` | None | binding equals current phase/iteration/intent; cleared before next Worker |
| `reviewer_result` | `ReviewerResult | None` | None | phase reviewer binding equals immediately preceding Worker round |
| `final_reviewer_result` | `ReviewerResult | None` | None | role FINAL_REVIEWER and final attempt binding |
| `quality_verdict` | `QualityVerdict | None` | None | never routes before decision result validates |
| `decision_state` | `DecisionState` | `CLEAR` | authority from validated OS-29 record, not Markdown summary |
| `decision_reason_code` | `str | None` | None | must satisfy existing decision policy; null only as policy permits |
| `blocking_findings` | `tuple[Finding, ...]` | `()` | unique finding IDs; all `blocking=True`; final FAIL requires nonempty |
| `pending_clarification_id` | `str | None` | None | required for NEEDS_INPUT/CONFLICT terminal publication; otherwise None |
| `artifact_binding` | `ArtifactBinding` | required | root ID immutable; current artifact path/digest nullable before first write; no absolute user path |
| `initial_repository_binding` | `RepositoryBinding` | required | immutable starting HEAD/tree/dirty snapshot |
| `repository_binding` | `RepositoryBinding` | required | current accepted HEAD/tree/dirty snapshot; changes only through validated settlement |
| `route_token` | `RouteToken | None` | None | written only by ROUTE; consumed/cleared by ADVANCE_PHASE or PREPARE_INTENT |
| `pending_intent` | `ActionIntent | None` | None | PREPARED iff next node EXECUTE_INTENT; stable ID/payload immutable until settled |
| `intent_status` | `IntentStatus` | `NONE` | state relation: NONE→PREPARED→SETTLED→NONE |
| `pending_event` | `SettlementEvent | None` | None | SETTLED only; `intent_id` equals pending intent |
| `processed_command_ids` | `tuple[str, ...]` | `()` | unique, first-seen canonical order; immutable prefix |
| `processed_event_ids` | `tuple[str, ...]` | `()` | unique, first-seen canonical order; immutable prefix |
| `logical_trace` | `tuple[TraceEntry, ...]` | `()` | append by explicit replacement; contiguous `sequence` from 0 |
| `terminal_status` | `TerminalStatus | None` | None | once set, absorbing; pending role/intent/event must be None |
| `terminal_reason` | `TerminalReason | None` | None | required iff terminal; closed reason code + optional phase/finding IDs in structured fields |

Nested JSON-safe records:

```python
class Finding(TypedDict):
    finding_id: str
    blocking: Literal[True]
    responsible_phase: Phase
    quality_attribute: str
    severity: Literal["CRITICAL", "MAJOR"]

class WorkerResult(TypedDict):
    intent_id: str; phase: Phase; iteration: int
    status: WorkerStatus; decision_record_key: str
    artifact_id: str; artifact_digest: str
    unit_test_status: Literal["PASS", "BLOCKED", "NOT_APPLICABLE"]
    resolution_ids: tuple[str, ...]

class ReviewerResult(TypedDict):
    intent_id: str; role: Role; phase: Phase; iteration: int
    result: ReviewResult; review_verdict: QualityVerdict
    decision_record_key: str; artifact_id: str; artifact_digest: str
    findings: tuple[Finding, ...]

class ArtifactBinding(TypedDict):
    artifact_root_id: str; relative_path: str | None; digest: str | None
    evidence_ids: tuple[str, ...]

class RepositoryBinding(TypedDict):
    head_sha: str; tree_digest: str; dirty: bool

class PhasePass(TypedDict):
    phase: Phase; generation: int; tree_digest: str
    gate_intent_id: str; gate_event_id: str
```

`validate_state(raw, *, expected_thread_id) -> WorkflowState` rejects unknown/missing keys, bool-as-int, mutable/default objects, non-JSON values, NaN/Infinity, noncanonical queues/counters, impossible role/round combinations, terminal outgoing work, and recursive values. It calls `json.dumps(..., allow_nan=False)` and recursively accepts only `None|bool|int|str|list|dict[str, value]`; bytes, Path, Protocol instances, callable, file/process/client objects fail `NON_CHECKPOINTABLE_STATE`. A denylisted key scan rejects key segments matching `process_handle|terminal_handle|credential|access_token|client|session_handle` at any depth.

### 4. Stable normalized action/event contracts

```python
class ActionIntent(TypedDict):
    schema_version: Literal["os40.action.v1"]
    intent_id: str
    command_id: str
    action_kind: ActionKind
    run_id: str
    phase: Phase
    phase_iteration: int
    final_review_iteration: int
    role: Role
    round_kind: RoundKind
    artifact_binding: ArtifactBinding
    repository_binding: RepositoryBinding
    payload_digest: str

class SettlementEvent(TypedDict):
    schema_version: Literal["os40.event.v1"]
    event_id: str
    intent_id: str
    command_id: str
    event_kind: EventKind
    outcome: Literal["SUCCEEDED", "FAILED"]
    result: WorkerResult | ReviewerResult | ArtifactBinding | ClarificationReceipt
    occurred_at: str
    payload_digest: str
```

`command_id = sha256(canonical({workflow_id, run_id, phase, phase_iteration, final_review_iteration, role, round_kind, action_kind}))`. `intent_id = sha256(canonical({command_id, artifact_binding, repository_binding, payload_digest}))`. `event_id = sha256(canonical({intent_id, event_kind, outcome, result_digest}))`. Timestamps and adapter handles are excluded from identity. Same ID with unequal canonical payload is an idempotency conflict, never a replay.

## Components / Interfaces / Data Flow

### 1. Exact port signatures

All ports are `typing.Protocol`; domain parameters/results are mappings/records above and contain no runtime handle.

```python
@runtime_checkable
class AgentExecutionPort(Protocol):
    def capabilities(self) -> frozenset[str]: ...
    def start(self, intent: ActionIntent) -> AgentReceipt: ...
    def send(self, intent_id: str, command: AgentCommand) -> AgentReceipt: ...
    def status(self, intent_id: str) -> AgentObservation: ...
    def interrupt(self, intent_id: str, reason: str) -> AgentReceipt: ...
    def settlement(self, intent_id: str) -> SettlementEvent | None: ...

@runtime_checkable
class ArtifactStorePort(Protocol):
    def put(self, intent: ActionIntent, content: bytes) -> ArtifactReceipt: ...
    def get(self, artifact_id: str) -> bytes: ...
    def evidence(self, evidence_id: str) -> bytes: ...

@runtime_checkable
class RuntimeStatePort(Protocol):
    def get_receipt(self, intent_id: str) -> ExternalReceipt | None: ...
    def claim(self, intent: ActionIntent) -> ClaimResult: ...
    def settle(self, intent_id: str, event: SettlementEvent) -> ExternalReceipt: ...

@runtime_checkable
class HumanApprovalPort(Protocol):
    def publish(self, *, run_id: str,
                sources: Sequence[ClarificationSource]) -> PublishResult: ...
    def show(self, *, run_id: str, request_id: str) -> Mapping[str, object]: ...
    def ingest(self, *, run_id: str, request_id: str,
               decision_item_id: str | None,
               submission: ResponseSubmission) -> IngestResult: ...

class ClockPort(Protocol):
    def now(self) -> str: ...  # validated UTC ISO-8601; observation only

class IdGeneratorPort(Protocol):
    def stable_id(self, namespace: str, canonical_payload: bytes) -> str: ...
```

`HumanApprovalPort` is imported/re-exported from `clarification_protocol.py`; its v2 request/response semantics are not forked. `RuntimeStatePort` stores adapter ownership/receipt data that cannot enter graph checkpoint. `AgentReceipt`, `AgentObservation`, `ExternalReceipt`, `ClaimResult` are normalized immutable dataclasses in adapters; graph only receives `SettlementEvent`.

Required capability vocabulary is closed:

```python
Capability = Literal[
  "agent_start", "agent_command", "agent_status", "agent_interrupt",
  "settlement", "idempotent_intent", "artifact_immutable",
  "checkpoint", "human_approval", "dispatch_provenance",
  "dependency_edges", "runtime_ownership"
]
```

Base workflow requires the first eight; clarification path additionally requires `human_approval`; Orca parity profile additionally requires the last three. `missing_capabilities(required, offered) -> tuple[Capability, ...]` runs in VALIDATE before PREPARE_INTENT. Nonempty result enters BLOCKED `ADAPTER_CAPABILITY_MISSING` with sorted missing set and no port mutation.

### 2. Nodes

| Node | Reads | Writes | Side effect / replay basis |
| --- | --- | --- | --- |
| `VALIDATE` | all state, injected adapter capabilities, config thread_id | normalized state or terminal BLOCKED reason | pure; no side effect |
| `ROUTE` | role/round/results/decision/budgets/queues/terminal | route trace and `route_token` | pure `route(state)` only; it does not write terminal fields |
| `ADVANCE_PHASE` | PASS token/current pass/index | next phase/index, clears phase-local results/token | pure; refuses advance without current `PhasePass` |
| `PREPARE_INTENT` | next route token, bindings, counters | `pending_intent`, PREPARED, command trace; increments no phase budget except final iteration rule below | pure; node boundary checkpoints intent before effect |
| `EXECUTE_INTENT` | PREPARED intent | `pending_event`, SETTLED | only effect node; port `claim/get_receipt/start-or-put/settlement`; stable ID makes rerun idempotent |
| `VALIDATE_SETTLEMENT` | pending intent/event and state bindings | terminal BLOCKED or validated event | pure validation; duplicate event recognized before mutation |
| `APPLY_RESULT` | validated event | result slot, decision/quality/findings, processed IDs, counters, clears intent/event | pure; event ID append and counter mutation atomic in one graph update |
| `TERMINAL` | route token plus §6 conditions, no external work | `terminal_status`, structured `terminal_reason`, clears pending role/intent/event, final trace | pure absorbing node → END; sole terminal-field writer (CF-3) |

There is no host workflow loop. Caller invokes/resumes compiled graph; LangGraph repeatedly follows its own edges. `executor.py` supplies node callables and injected ports, but cannot choose next node. AST test permits loops used for collection validation only when functions do not invoke graph nodes/ports; it specifically rejects `run_workflow`, `while` around `graph.invoke/stream`, and adapter imports from routing/spec.

### 3. Single routing function and edge map

Exact signature:

```python
RouteToken = Literal[
  "BLOCK", "ESCALATE", "PREPARE_WORKER", "PREPARE_PHASE_REVIEWER",
  "ADVANCE_PHASE", "PREPARE_FINAL_REVIEWER", "PREPARE_CORRECTION",
  "PREPARE_REVALIDATION", "COMPLETE"
]

def route(state: WorkflowState) -> RouteToken: ...
```

Supporting functions are pure facts used by `route`; they do not execute transitions:

```python
def validate_state(raw: Mapping[str, object], *, expected_thread_id: str) -> WorkflowState: ...
def validate_event(state: WorkflowState, event: Mapping[str, object]) -> SettlementEvent: ...
def phase_gate(state: WorkflowState) -> Literal["PENDING", "PASS", "FAIL", "BLOCK"]: ...
def final_gate(state: WorkflowState) -> Literal["PENDING", "PASS", "FAIL", "BLOCK"]: ...
def responsible_phases(findings: Sequence[Finding],
                       requested: tuple[Phase, ...]) -> tuple[Phase, ...]: ...
def downstream_revalidation_set(corrected: tuple[Phase, ...],
                                requested: tuple[Phase, ...],
                                risk: Risk) -> tuple[Phase, ...]: ...
def missing_capabilities(required: frozenset[str],
                         offered: frozenset[str]) -> tuple[str, ...]: ...
```

`graph.py` adds conditional edges from ROUTE using `route` and a total `ROUTE_TARGETS` map. ADVANCE_PHASE is a pure node update then returns to ROUTE. PREPARE_* tokens all target PREPARE_INTENT with an explicit route token stored in state; PREPARE_INTENT may not recompute workflow semantics.

### 4. Routing truth table

Priority is strict and encoded in `route` in this order:

1. invalid/terminal/pending settlement inconsistency → BLOCK; terminal already set only routes TERMINAL and rejects new event.
2. decision `NEEDS_INPUT|CONFLICT`, missing/corrupt decision record, or OS-29 admission defect → BLOCK before reading quality verdict or consuming budget.
3. missing capability → BLOCK before intent.
4. PREPARE_INTENT→EXECUTE_INTENT→VALIDATE_SETTLEMENT→APPLY_RESULT is a static structural edge chain, not a `route` return branch. Checkpoint resume returns directly to the saved static successor and does not add RouteToken members (CF-4).
5. active phase Worker absent → PREPARE_WORKER if phase budget remains, else ESCALATE.
6. Worker BLOCKED or mandatory unit-test evidence absent/BLOCKED → BLOCK.
7. LOW Worker COMPLETE is phase gate result; MEDIUM/HIGH → PREPARE_PHASE_REVIEWER.
8. phase reviewer PASS → next phase via ADVANCE_PHASE, or T0 PREPARE_FINAL_REVIEWER when all requested phases passed.
9. phase reviewer FAIL → PREPARE_CORRECTION same phase if budget remains, else ESCALATE. Correction Worker then fresh phase Reviewer (MEDIUM/HIGH); LOW correction Worker alone re-establishes phase pass.
10. final reviewer PASS → COMPLETE only if every requested phase has a recorded current PASS generation.
11. final reviewer FAIL → first T2 final budget guard; if exhausted ESCALATE before finding mapping or phase budget read.
12. T3 validate blocking findings and map responsible phases; malformed→BLOCK, out-of-request→ESCALATE.
13. T4 each responsible phase upstream-first: phase budget guard then correction Worker/fresh Reviewer; any failure repeats same phase bounded loop.
14. T5a after all corrected phases pass, compute D; HIGH only, suffix strictly after earliest corrected requested phase in canonical requested order. LOW/MEDIUM and BUGFIX/REFACTORING D=().
15. T5 revalidate every D phase Worker→fresh Reviewer; then clear attempt queues/results and T0 starts a fresh final reviewer.

Phase pass carries `generation = phase_iterations[p]` and `repository_binding.tree_digest`. Any correction of phase p invalidates p and all D pass generations before dispatch. `all_phase_passes_current(state)` requires one current pass record per requested phase; `final_pass` additionally binds the resulting post-revalidation repository/artifact digest. Thus phase PASS cannot set COMPLETED and final PASS cannot compensate for a missing/stale phase pass.

### 5. Iteration accounting

- State의 `phase_iterations`와 `final_review_iterations`가 각각 기존 보고 계약의 `PHASE_ITERATIONS[p]`와 `FINAL_REVIEW_ITERATIONS` 두 domain이다. Adapter/report projection만 대문자 legacy field name으로 렌더링하며 별도 counter를 만들지 않는다.
- `phase_iterations[p]` increments only in APPLY_RESULT when a unique gate attempt settlement is admitted: LOW Worker gate or MEDIUM/HIGH Phase Reviewer gate. Preparing/settling a correction Worker does not independently increment; its fresh reviewer does. Decision blocks and malformed decision input consume none.
- `final_review_iterations` increments in PREPARE_INTENT for a unique fresh FINAL_REVIEWER intent, because the contract counts dispatch attempts and T2 must see the current attempt number after FAIL. Re-entering PREPARE with same command ID detects processed/pending identity and does not increment again.
- Guard is `>= max_iterations` before every additional phase gate/final intent. Terminal reasons: `MAX_ITERATIONS_REACHED` with structured phase; `FINAL_REVIEW_MAX_ITERATIONS_REACHED`.
- Remaining fields are derived invariants, never independently decremented.

### 6. Terminal states/reasons

| Status | Entry | Closed reason codes |
| --- | --- | --- |
| COMPLETED | all current requested phase passes + fresh final PASS bound to same repository/artifact generation | `WORKFLOW_COMPLETED` |
| BLOCKED | decision block, clarification, malformed/out-of-order/replay conflict/state corruption, missing capability, Worker/unit-test block, artifact/head mismatch | `NEEDS_INPUT`, `CONFLICT`, `DECISION_GATE_INVALID`, `PENDING_CLARIFICATION`, `ADAPTER_CAPABILITY_MISSING`, `WORKER_BLOCKED`, `UNIT_TEST_BLOCKED`, `MALFORMED_STATE`, `MALFORMED_EVENT`, `UNKNOWN_EVENT`, `OUT_OF_ORDER_EVENT`, `POST_TERMINAL_EVENT`, `IDEMPOTENCY_CONFLICT`, `NON_CHECKPOINTABLE_STATE`, `ARTIFACT_BINDING_MISMATCH`, `REPOSITORY_BINDING_MISMATCH`, `MALFORMED_FINAL_REVIEW_OUTPUT` |
| ESCALATED | phase/final budget exhausted or final finding responsible phase outside requested scope | `MAX_ITERATIONS_REACHED`, `FINAL_REVIEW_MAX_ITERATIONS_REACHED`, `OUT_OF_SCOPE_FINAL_REVIEW_FINDING` |

`TerminalReason` is `{code, message, phase: Phase|None, finding_ids: tuple[str,...], missing_capabilities: tuple[str,...]}` with field applicability validation. Terminal state is absorbing: validator rejects non-null pending role/intent/event and any new command/event; graph routes only TERMINAL→END.

For `FINAL_REVIEW` escalation, a responsible phase is active only when `0 <= correction_index < len(correction_queue)`. An active phase with exhausted phase budget records `MAX_ITERATIONS_REACHED` and that responsible phase. If `correction_index == len(correction_queue)`, all queued corrections have already completed; subsequent final-budget exhaustion records `FINAL_REVIEW_MAX_ITERATIONS_REACHED` and `current_phase`, without indexing the consumed queue. The same active-index predicate guards correction routing and preparation, which fail closed when no active phase exists.

### 7. Intent → effect → settlement data flow

```text
ROUTE
  -> PREPARE_INTENT
       update pending_intent/PREPARED
  -- LangGraph checkpoint boundary A (intent durable) --
  -> EXECUTE_INTENT
       RuntimeStatePort.claim(intent)
       existing equal receipt? reuse : adapter side effect once
       produce SettlementEvent
  -- LangGraph checkpoint boundary B (raw settlement captured) --
  -> VALIDATE_SETTLEMENT -> APPLY_RESULT
       atomically append processed IDs / counters / result / trace
  -- checkpoint boundary C (settlement applied) --
  -> ROUTE
```

Crash before A means no side effect. Crash A→B can repeat EXECUTE, but `claim(intent_id,payload_digest)` and adapter receipt lookup return the same receipt. Crash B→C revalidates the same event; APPLY_RESULT sees `event_id` already processed only if C had committed, otherwise applies exactly once. Duplicate command/event with identical digest is a no-op trace `REPLAY_IGNORED`; same ID/different digest is BLOCKED. ArtifactStore uses the same intent ID as immutable write key, preventing duplicate artifacts.

`MemorySaver` is injected at compile and configured with `{"configurable":{"thread_id": state.thread_id}}`. `RuntimeStatePort` is a separate ownership/receipt boundary; MemorySaver is not claimed durable. Checkpoint restoration calls VALIDATE before ROUTE and recomputes derived remaining budgets/next token, so same checkpoint yields same next node/action.

### 8. Graph validation

LangGraph compile is authoritative for unknown edge targets and missing START entrypoint. `validate_graph_spec(spec) -> None` additionally computes:

- forward reachability from START: every declared node reachable;
- reverse reachability to one of TERMINAL/END: every nonterminal can terminate;
- conditional route coverage: `set(RouteToken) == ROUTE_TARGETS.keys()` and all targets declared;
- no outgoing terminal edge except END;
- START has exactly VALIDATE, and effect node is reachable only through PREPARE_INTENT;
- every cycle contains ROUTE and is bounded by declared `phase_budget`, `final_budget`, or `phase_index_monotonic` guard metadata. The normal ROUTE→ADVANCE_PHASE→ROUTE cycle uses the third guard because `current_phase_index` strictly increases to `len(requested_phases)` (CF-5).

The linter consumes the same immutable `GraphSpec` used by `build_graph`; it is not a second transition table. Mutation fixtures add a dead node, missing route, unknown target, terminal back-edge and unguarded cycle and must fail.

### 9. Fake and Orca adapters

`FakeAdapter` implements all ports with in-memory dicts keyed by intent/event/artifact ID, scripted normalized Worker/Reviewer results, deterministic clock sequence and SHA ID generator. It imports no Orca module. Its call counters and receipts make duplicate-effect assertions observable.

`OrcaAdapter` composes, rather than subclasses, `OrcaRuntimeHarness` primitives:

- `preflight`, `create_task/create_phase_graph`, `start_worker`, `wait_for_done`, `settle_attempt` for capability/task/dispatch/settlement;
- `claim_settlement`, `verify_settlement`, `finalize_once`, `account_axes`, lifecycle methods for provenance/idempotency/ownership;
- existing reviewer parsers and `run_logging` audit writers as ingress/evidence services.

It does not call `run_workflow` and contains no phase/final routing. An adapter-private `intent_id -> {task_id, dispatch_id, terminal_handle, receipt}` store holds runtime handles; projection to `SettlementEvent` strips them. If live Orca cannot look up an intent natively, the adapter must persist this map via `RuntimeStatePort` before create and reconcile by intent label/provenance; it may not silently redispatch.

### 10. Logical trace parity

```python
class TraceEntry(TypedDict):
    sequence: int
    node: NodeName
    route: RouteToken | None
    phase: Phase
    phase_iteration: int
    final_review_iteration: int
    role: Role | None
    round_kind: RoundKind
    intent_id: str | None
    event_id: str | None
    gate: Literal["PENDING", "PASS", "FAIL", "BLOCK"] | None
    terminal_status: TerminalStatus | None
    reason_code: str | None
```

Parity canonicalizes trace entries to sorted-key JSON and compares all fields above. It intentionally excludes timestamp, terminal/task/dispatch handle, raw adapter receipt and filesystem absolute path. Same scenario inputs and deterministic bindings must yield byte-equal logical traces for Fake and Orca-fixture adapters. Comparator self-test mutates each compared field individually and expects a precise diff.

### 11. Skill parity and legacy disposition

`workflow-graph-contract` in orchestration Skill declares only stable public vocabulary/ownership anchors: workflow ID/schema, phases/order, risk gates, two counters, final review mandatory, terminal statuses, decision-first, HIGH-only D, launcher path. `validate_workflow_graph_docs.py` imports stdlib-only `contracts/graph_spec` constants and parses this block; it rejects missing/extra/different tokens and Coordinator-owned routing prose such as instructions to choose next phase/retry.

`e2e_harness.py` imports extracted pure functions for compatibility and its legacy `run_workflow` remains test-only oracle. Static import graph test rejects any `scripts/deterministic_workflow` production module or Skill launcher importing `E2EHarness/run_workflow/migration`; only parity tests may import `migration.py`. After one compatible release, deletion is follow-up cleanup, not required to prove OS-40 single production engine.

### 12. Existing contract reuse

- `decision_policy`/`decision_gate`: façade validates current record/schema/transition/admission; graph stores only normalized result/key. Ledger schema v1 unchanged.
- `clarification_protocol`: re-export exact `HumanApprovalPort` and v2 artifact operations. OS-40 may publish then BLOCK; ingest/resume consumption is OS-31.
- `run_logging`: Orca/Artifact adapter writes existing column order/audit schema; core trace is a new namespace and does not modify old rows.
- `quality_profile`: resolve once before initial state; store closed normalized risk-independent profile outcome, then `workflow_gate_value` maps verdict.
- `workflow_contract`: legacy Markdown ingress parser produces Worker/Reviewer normalized records; graph never routes on free prose.
- `skill_policy`: existing risk contract parser feeds validated initial state; no duplicate parser.

## Error Handling / Compatibility

### Fail-closed input matrix

| Input defect | Detection | Outcome before side effect |
| --- | --- | --- |
| missing/extra/wrong-type state field | `validate_state` | BLOCKED/MALFORMED_STATE |
| runtime object/forbidden handle key/non-JSON | checkpointability validator | BLOCKED/NON_CHECKPOINTABLE_STATE |
| unknown/malformed event | `validate_event` schema/vocabulary | BLOCKED/UNKNOWN_EVENT or MALFORMED_EVENT |
| event for other intent/phase/iteration/role | binding/order validator | BLOCKED/OUT_OF_ORDER_EVENT |
| replay same ID/equal digest | processed/pending identity | no-op; counters/artifacts/effects unchanged |
| same ID/different digest | canonical digest comparison | BLOCKED/IDEMPOTENCY_CONFLICT |
| event after terminal | absorbing-state validator | reject POST_TERMINAL_EVENT; no new graph action |
| head/artifact digest mismatch | binding validator | BLOCKED binding reason |
| unsupported schema version | ingress validator | BLOCKED/MALFORMED_STATE/EVENT |
| missing adapter capability | preflight pure comparison | BLOCKED/ADAPTER_CAPABILITY_MISSING, calls=0 |

### Dependency-present/absent compatibility and CF-1

Test guard is import-based, not `find_spec`-based:

```python
def _langgraph_ok() -> bool:
    try:
        import langgraph
        import langgraph.graph
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False
```

Graph test classes use `@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")`; module setup may raise `unittest.SkipTest` only after the same helper returns false. The absent-lane child process installs a temporary `MetaPathFinder` for `langgraph` imports and clears `langgraph*` from `sys.modules`. The helper catches both finder-raised and loader-raised ImportError; dist-info metadata remaining installed cannot make it return true. A guard unit test covers present, find-spec/finder raise, and loader raise before the absent lane runs.

Present lane requires baseline six existing skips exactly and zero OS-40 graph skips. Absent lane permits those six plus an explicit class-name allowlist of graph-runtime tests; pure contracts/routing/spec/docs/package tests still run. Both use unittest discover and fail on any error/failure/unlisted skip. No uninstall, venv creation, network or package download occurs during this run.

### OS-31 / OS-37 extension points

- OS-31 can replace MemorySaver through `build_graph(checkpointer: BaseCheckpointSaver | None)` and implement durable `RuntimeStatePort`; `pending_clarification_id`, processed IDs and PREPARED intent are already resumable. A future `WAITING_FOR_INPUT` nonterminal can be added between clarification publication and VALIDATE without changing the ports or event binding. OS-40 deliberately maps NEEDS_INPUT/CONFLICT to terminal BLOCKED and never invokes `HumanApprovalPort.ingest` to resume.
- OS-37 implements the same `AgentExecutionPort` and conformance suite. It must declare capabilities and provide stable receipt persistence/structured argv/ownership; absent capabilities block. Core/graph require no change.

## Expected Changed Files / Implementation Steps

1. Add `requirements-langgraph.txt`, `docs/LANGGRAPH_DEPENDENCIES.md`; implement stdlib `contracts.py`, `state.py`, `ports.py` first.
2. Extract pure functions from `e2e_harness.py` to `routing.py`, add compatibility imports, freeze routing truth-table tests.
3. Implement `graph_spec.py` linter then `graph.py` StateGraph and `executor.py` node functions.
4. Implement intent/event identity, RuntimeState receipt contract, MemorySaver reconstruction and crash-window tests.
5. Add `fake_adapter.py` and full deterministic scenarios.
6. Add `orca_adapter.py` composition and `migration.py` test-only normalizer; verify normalized parity.
7. Mirror package/launcher into orchestration Skill tools, update `release_manifest.py`, `validate_skills.py`, Skill prose/anchor and `validate_workflow_graph_docs.py`.
8. Update README/INSTALL/COMPATIBILITY and trace/integration docs; add archive/package/license inventory tests.
9. Run targeted unittest modules, full unittest discover, Skill/package/archive validators and `git diff --check`.

Only IMPLEMENTATION may modify these production/test/docs files. DESIGN creates this artifact only.

## Testing Strategy

Tests use `unittest.TestCase` and are discoverable by `python3 -m unittest discover -s scripts -p 'test_*.py'`.

- `test_deterministic_workflow_contracts.py`: every state field default/invariant, forbidden runtime objects/keys, stable ID determinism/conflict, decision-first, budget-first, responsible/D pure functions, malformed/unknown/out-of-order/post-terminal events.
- `test_deterministic_workflow_graph.py`: exact happy trace; phase FAIL correction/fresh reviewer; both exhaustion domains; clarification block; phase/final PASS non-substitution; MemorySaver reconstruction; compile errors and custom linter mutations.
- `test_deterministic_workflow_adapters.py`: Fake Orca-independent flow, duplicate command/event/artifact, crash A→B and B→C, missing capability calls=0, Fake/Orca normalized trace parity and comparator field mutations.
- `test_validate_skills.py` additions: graph-contract/Skill parity, production import ban, core forbidden-symbol AST scan, canonical/installed engine tree equality.
- Existing decision/clarification/logging/quality/risk/review-isolation/package tests guard OS-28~30 and evidence semantics.

Mutation sensitivity is explicit: swap/remove phase edge; FAIL→advance; `>=`→`>`; T2 after finding mapping; quality before decision; omit event dedupe; bypass receipt lookup; allow terminal edge; delete route-map member; add dead node; alter one parity field; change one Skill token. Each mutation must cause a named assertion failure, not merely a different report.

Validation order:

```text
python3 -m unittest scripts.test_deterministic_workflow_contracts
python3 -m unittest scripts.test_deterministic_workflow_graph scripts.test_deterministic_workflow_adapters
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/validate_skills.py
python3 scripts/validate_workflow_graph_docs.py
python3 scripts/verify_package.py
python3 scripts/build_release.py --output <mktemp-dir>/orca-skills-<version>.tar.gz
python3 scripts/verify_package.py --archive <mktemp-dir>/orca-skills-<version>.tar.gz
git diff --check
```

Full discover receives a timeout above 6 minutes because verified baseline is 333.718 seconds. Test evidence records total tests, failures/errors, named skips and exit code; only allowlisted skips pass.

## Risks / Open Issues

- **No open user decision:** package location, legacy disposition, route shape, ID inputs, checkpoint boundaries, adapters and tests are fixed here.
- **LangGraph checkpoint semantics:** checkpoint occurs after node completion, so side effect is deliberately separated into its own node after PREPARE. Adapter idempotency covers the unavoidable crash-after-effect/before-checkpoint window.
- **Legacy oracle drift:** it is not production and may diverge after the compatibility release; parity test makes divergence visible. It must never become fallback behavior.
- **Orca intent reconciliation:** public Orca primitives may not expose native idempotency keys. Adapter-owned RuntimeState mapping and provenance reconciliation are mandatory; inability to prove ownership blocks instead of redispatching.
- **State size:** processed IDs/trace grow with bounded iterations and requested phases; OS-40 max 10 bounds growth. Production compaction/durable retention is OS-31.
- **Reason compatibility:** old run log/audit vocabularies remain untouched; new terminal reason structure lives only in `os40.workflow.v1` and adapters map to existing log detail fields.
- **CF-1 is binding:** implementation cannot regress to `find_spec` or metadata-only presence detection. Three-mode guard test is a prerequisite for absent-lane evidence.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The approved analysis and plan plus carried-forward test-runner constraints fully determine an implementable design; no user-authority decision remains open at this boundary.",
  "scope": "This phase's own conduct at this iteration."
}
```
