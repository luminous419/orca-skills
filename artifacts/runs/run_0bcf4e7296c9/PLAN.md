# Worker Result

STATUS: COMPLETE
DECISION_GATE_STATE: CLEAR

## Goal

OS-40을 `langgraph==0.2.76`의 `StateGraph`가 유일한 executable workflow transition definition인 runtime-neutral engine으로 구현한다. 기존 `e2e_harness.py`의 검증된 pure logic은 새 core로 추출하고 imperative `run_workflow`는 production-equivalent engine으로 남기지 않으며, Orca와 fake 실행은 동일 graph가 발행한 stable intent를 수행하는 adapter로 제한한다. typed/closed checkpoint state, decision-first gate, 두 iteration domain, correction/downstream revalidation, final review, terminal result, idempotency와 capability fail-closed를 자동화된 negative/mutation-sensitive tests로 입증한다.

## Scope / Out of Scope

### In scope와 파일 배치 결정

저장소의 Python 실행·테스트 관례가 `scripts/` 평면 module과 `python3 -m unittest discover -s scripts -p 'test_*.py'`인 점을 유지하되, 상태/port/adapter의 순환 결합을 피하기 위해 `scripts/deterministic_workflow/`를 하나의 새 subpackage로 만든다. 새 최상위 directory는 만들지 않는다. `scripts/`는 이미 release root다 (`release_manifest.py:41`); installed Skill은 repository `scripts/`를 복사하지 않으므로 동일 package를 `orca-worker-reviewer-orchestration/tools/deterministic_workflow/`에 설치하고 exact tree parity validator를 둔다.

계획된 파일은 다음과 같다.

| 파일 | 책임 |
| --- | --- |
| `scripts/deterministic_workflow/__init__.py` | public API와 engine dependency availability error만 export; Orca symbol 없음 |
| `contracts.py` | phase/role/node/action/event/verdict/status/capability closed vocabulary, frozen normalized records, canonical serialization과 schema version |
| `state.py` | JSON-safe `TypedDict` graph state와 construction/snapshot validation; handle/client/credential 금지 |
| `routing.py` | decision-first gate, phase/final budget, responsible-phase/D 계산, event ordering/replay 판정 pure functions |
| `graph_spec.py` | node/edge/route-map의 단일 declarative specification과 START reachability/nonterminal→terminal linter |
| `graph.py` | `StateGraph` nodes와 conditional edges/`Command`, compile factory, `MemorySaver` injection; routing은 `routing.py` 함수를 직접 호출 |
| `ports.py` | `AgentExecutionPort`, `ArtifactStorePort`, `RuntimeStatePort`, `HumanApprovalPort` compatibility protocol, `ClockPort`, `IdPort`, capability declaration |
| `executor.py` | graph node가 intent를 먼저 상태에 기록하고 adapter settlement event를 별도 반영하는 thin effect node; 독립 transition loop 없음 |
| `fake_adapter.py` | deterministic in-memory agent/artifact/runtime/clock/id adapters, idempotency-key receipt map와 logical trace |
| `orca_adapter.py` | `OrcaRuntimeHarness`의 call/start/wait/settlement/lifecycle primitive를 composition하여 port 구현; terminal/dispatch credential은 adapter state에만 보관 |
| `migration.py` | legacy `e2e_harness` scenario/result를 normalized event/trace로 바꾸는 test-only parity bridge; routing 규칙 없음 |
| `scripts/test_deterministic_workflow_contracts.py` | closed schema, validation, pure routing, invalid/out-of-order/replay/mutation tests |
| `scripts/test_deterministic_workflow_graph.py` | happy/fail/correction/final/checkpoint/reachability graph tests; LangGraph 미설치 시 module 단위 skip |
| `scripts/test_deterministic_workflow_adapters.py` | fake idempotency/capability/side-effect restart와 Orca logical parity fixtures |
| `scripts/validate_workflow_graph_docs.py` | graph spec ↔ Skill anchor/migration matrix parity validator; LangGraph import 없이 동작 |
| `requirements-langgraph.txt` | OS-40 opt-in runtime pins |
| `docs/DETERMINISTIC_WORKFLOW.md` | state/graph/routing, ports, checkpoint/idempotency, trace, OS-31/OS-37 guide, migration matrix |
| `docs/LANGGRAPH_DEPENDENCIES.md` | version compatibility, transitive dependency/license, optional install/package impact |
| `docs/examples/DETERMINISTIC_WORKFLOW_TRACE.md` | happy, correction, block, escalation logical traces |

`scripts/e2e_harness.py`에서는 reusable pure functions/parsers를 새 core로 이동하고 기존 imports를 compatibility re-export로 전환한다. `E2EHarness.run_workflow`는 parity oracle로 한 release 동안 test-only 존치하되 새 engine/Skill production path가 import하지 못하도록 static test를 둔다; 신규 routing 변화는 graph spec에만 적용하고 legacy oracle에는 복제하지 않는다. `scripts/orca_runtime_harness.py`는 Orca-specific primitive를 유지하고 adapter가 composition하며, 전체 graph routing을 추가하지 않는다.

Skill 설치본에는 `tools/deterministic_workflow/`와 thin `tools/run_workflow.py` launcher를 추가하고, `release_manifest.required_skill_paths` 및 `validate_skills.py` exact-tree parity를 갱신한다. 두 Skill 중 Orca execution adapter가 없는 loop Skill에는 engine copy를 넣지 않으며, shared phase/review template의 기존 parity는 유지한다.

### Dependency 정책 결정

- engine runtime에는 `langgraph==0.2.76`, `langgraph-checkpoint==2.1.1`, `langgraph-sdk==0.1.74`, `langchain-core==0.3.80`, `langsmith==0.3.45`를 `requirements-langgraph.txt`에 exact pin한다. 첫 세 항목의 declared compatible range와 현재 검증 조합은 문서에 별도로 기록한다. transitive solver drift까지 막기 위해 IMPLEMENTATION에서 `python3 -m pip freeze`를 복사하지 않고 직접 사용되는 검증 조합 및 보안/패키징상 중요한 transitive versions를 문서 표로 기록한다.
- 이는 repository/Skill의 **optional install dependency**이나 deterministic engine을 실행할 때는 필수다. import 실패 시 engine entrypoint는 `LANGGRAPH_DEPENDENCY_MISSING`으로 명시적으로 실패하며 fallback loop를 실행하지 않는다.
- pure `contracts/state/routing/graph_spec/ports`와 docs validator는 LangGraph를 import하지 않는다. 신규 테스트도 모두 `unittest.TestCase`로 작성한다. graph-specific class는 `importlib.util.find_spec("langgraph")`와 `importlib.metadata.version("langgraph") == "0.2.76"`을 계산한 뒤 `unittest.skipUnless`를 적용하고, import 자체가 필요한 module setup에서는 `raise unittest.SkipTest`를 사용한다. 기존 suite는 그대로 실행되며 pytest 전용 기능이나 `langgraph.__version__`에 의존하지 않는다.
- `docs/LANGGRAPH_DEPENDENCIES.md`에 LangGraph/checkpoint/SDK/langchain-core/langsmith와 direct transitives의 license inventory, LangSmith가 transitive Python package일 뿐 hosted service를 사용하지 않는다는 점, 프로젝트 자체 license 미결정과의 구분을 기록한다.

### Out of scope

- OS-31: production durable checkpointer 선정, cross-process/cross-session ownership recovery, human decision consumption/resume. `RuntimeStatePort`, checkpointer injection, `WAITING_FOR_INPUT`-compatible pending clarification field만 둔다.
- OS-37: direct Claude/Codex CLI PTY/process adapter. `AgentExecutionPort` capability/settlement/idempotency 구현 가이드와 conformance tests만 제공한다.
- OS-38: Orca source extraction/연구. 현재 public Orca CLI와 harness receipts만 사용한다.
- LangChain Agent, LangSmith tracing, Agent Server, hosted deployment, GUI/Slack/Jira/GitHub approval transport, adaptive presets, historical artifact rewrite, 기존 Orca adapter 제거는 하지 않는다.

## Work Items

### W1. Contract와 dependency baseline

1. `requirements-langgraph.txt` 및 dependency/license 문서를 추가한다.
2. `contracts.py`에 schema version과 closed vocabulary를 정의한다: phases, phase/final roles, intent kinds, settlement/event kinds, quality/decision states, terminal `COMPLETED|BLOCKED|ESCALATED`, capability keys.
3. stable identity는 canonical JSON(SHA-256)로 `run_id + phase + phase_iteration/final_iteration + role + round_kind + action_kind + artifact/head binding`에서 도출한다. 동일 logical action은 재실행에도 같은 ID, 의미가 다른 action은 다른 ID를 갖는다.
4. `state.py`는 요구된 모든 필드를 typed state에 포함하고 strict unknown/missing/type/domain/cross-field validation을 한다. processed command/event ID는 ordered tuple과 membership set의 canonical form으로 저장하며 duplicate는 no-op settlement로 판정한다.

### W2. Single graph specification과 pure routing

1. `graph_spec.py`에 nodes/edges/conditional route maps를 한 번만 선언한다. 예상 node family는 `validate_state`, `prepare_intent`, `execute_intent`, `validate_settlement`, `route_phase_gate`, `route_correction`, `route_revalidation`, `route_final_review`, `terminal`이다.
2. `routing.py`는 기존 `e2e_harness.py`의 `downstream_revalidation_set`, unit-test gate/parser semantics, budget-first/decision-first 순서를 추출한다. 다음 node/action 계산은 이 함수만 수행하고 adapter/Skill은 판단하지 않는다.
3. graph linter는 unknown targets/entrypoint는 LangGraph compile 결과와 함께 검사하고, 모든 declared node의 START reachability, 모든 nonterminal의 terminal 역도달 가능성, conditional output의 path-map coverage, terminal outgoing edge 부재를 검사한다.
4. `graph.py`는 같은 spec과 router를 `StateGraph` conditional edges 또는 `Command`에 연결한다. graph 외부 while/for workflow loop를 만들지 않는다.

### W3. Checkpoint, intent/settlement, idempotency

1. side effect node 진입 전에 `pending_intent`를 graph update로 checkpoint하고, 다음 node에서 port가 stable ID를 실행한다. effect 결과는 별도 `SettlementEvent`로 validation node에 들어온 뒤에만 counters/results/processed IDs를 변경한다.
2. adapter는 `intent_id -> immutable receipt` map을 가진다. 동일 ID/same payload는 기존 receipt 반환, 동일 ID/different payload는 `IDEMPOTENCY_CONFLICT`로 BLOCKED, duplicate settlement/event는 no-op, out-of-order settlement는 fail closed한다.
3. MemorySaver와 동일 `thread_id` 재호출로 pending intent를 복구하고 같은 ID를 재실행해 side effect count가 증가하지 않음을 보인다. corrupted checkpoint/state validation은 terminal ERROR를 새 vocabulary로 추가하지 않고 계약된 `BLOCKED` + reason으로 닫는다.

### W4. Fake adapter와 deterministic suite

1. fake ports는 in-memory immutable artifact/evidence store, scripted Worker/Reviewer results, deterministic clock/ID, capability set과 call counters를 제공한다.
2. `fake_worker.py`/`fake_reviewer.py` 결과 shape를 fixture source로 재사용하고 정상, phase FAIL→fresh correction review, final FAIL→responsible correction→HIGH D→fresh final, budget exhaustion, clarification block 시나리오를 graph로 실행한다.
3. 같은 initial state/event script를 두 번 실행해 byte-equal normalized trace/state를 비교한다.

### W5. Orca adapter integration과 parity

1. `OrcaRuntimeHarness`의 version/capability probe, Task/Dispatch creation, typed wait, settlement verification, terminal lifecycle를 port method로 감싼다. adapter receipt에는 handle이 있을 수 있으나 graph state로 투영할 때 opaque stable resource ID와 normalized settlement만 남긴다.
2. existing `orca_fake_agent.py`를 사용하는 offline/opt-in Orca fixture를 같은 event script로 실행한다. Fake trace와 Orca trace에서 `node, phase, role, round_kind, intent_id, event_kind, gate/verdict, terminal_status/reason`을 비교하고 runtime handle/timestamp/raw receipt는 제외한다.
3. unavailable capability fixture는 첫 intent 전에 BLOCKED되고 adapter call count가 0임을 검증한다.

### W6. Skill migration과 drift prevention

1. orchestration Skill의 phase selection/gate/retry/T0~T5a prose를 engine-owned declaration과 이유/semantic reviewer guidance로 축소한다. Coordinator가 next action을 다시 판단하라는 문장을 제거한다.
2. stable machine-readable `workflow-graph-contract` anchor를 Skill에 두되, values는 graph spec의 public constants와 validator가 비교한다. duplicate executable routing table을 Skill에 만들지 않는다.
3. `validate_workflow_graph_docs.py`와 `validate_skills.py` 연결로 node/phase/status/risk/final-review mandatory/iteration domains/migration ownership 및 installed-tree parity를 검사한다.

### W7. Packaging, docs, full verification

1. `release_manifest.py`에 requirements/docs와 installed engine tree/launcher를 명시한다. source archive는 `scripts/deterministic_workflow`를 기존 recursive root로 포함하며 installed Skill unexpected-file guard를 정확히 갱신한다.
2. README/INSTALL/COMPATIBILITY에 optional install, missing dependency behavior, tested exact versions, no hosted dependency, OS-31/37 boundary와 실행 예를 추가한다.
3. deterministic archive를 임시 경로에 build/verify하고 repository `dist/`나 historical artifact를 건드리지 않는다.

## Dependencies / Execution Order

| 순서 | Jira implementation order 번역 | 선행 조건 | phase별 산출 경계 |
| --- | --- | --- | --- |
| 1 | dependency/compatibility + typed state | 승인 PLAN | **DESIGN:** schema/ID/version/packaging ADR 수준 명세. **IMPLEMENTATION:** requirements/docs/contracts/state. **TEST:** dependency-present/absent 증거. |
| 2 | graph node/edge + pure routing | W1 | DESIGN이 exact nodes/route truth table/lint invariant 확정; IMPLEMENTATION이 spec/router/StateGraph 작성; TEST가 branch mutation 검출. |
| 3 | checkpoint/intent/settlement/idempotency | W1~W2 | DESIGN이 2-step checkpoint protocol과 conflicts 확정; IMPLEMENTATION이 ports/executor; TEST가 crash/replay/corruption 검증. |
| 4 | fake adapter/scenarios | W1~W3 | DESIGN이 scripted fixture/trace schema; IMPLEMENTATION이 fake adapter; TEST가 Orca-independent full graph와 determinism 수행. |
| 5 | Orca harness adapter | W3~W4 | DESIGN이 handle projection/capabilities; IMPLEMENTATION이 composition adapter; TEST가 receipt/idempotency/parity 수행. |
| 6 | Skill migration/parity | W2와 W5 logical trace 안정 | DESIGN이 migration matrix final form; IMPLEMENTATION이 prose/validator/installed copy; TEST가 drift mutation을 검출. |
| 7 | compatibility/package/release verification | W1~W6 | IMPLEMENTATION이 docs/manifest 갱신; TEST가 full suite, archive, parity, whitespace 증거를 수집. |

DESIGN은 production code를 쓰지 않고 `DESIGN.md`에 schemas, node diagram, route truth table, port signatures, stable ID examples, checkpoint sequence, file dependency direction, legacy loop disposition을 확정한다. IMPLEMENTATION은 위 파일을 만들고 unit tests를 함께 추가하며 relevant tests를 통과시킨다. TEST는 구현을 재서술하지 않고 mutation/negative probes, dependency-absent lane, full regression/package/archive/source-installed parity의 독립 실행 결과를 `TEST.md`에 남긴다.

## Validation / Test Plan

### Acceptance Criteria 1~16 mapping

| AC | Deliverable | 자동화 증거 |
| --- | --- | --- |
| 1 | StateGraph happy flow + terminal state | `test_deterministic_workflow_graph.py::test_full_happy_path_reaches_completed_through_final_review`; final node/remove-one-phase mutation 시 trace mismatch |
| 2 | responsible correction + fresh reviewer intent | `...::test_phase_reviewer_fail_routes_same_phase_correction_and_fresh_review`; FAIL edge를 next-phase로 mutation하면 실패 |
| 3 | phase/final budgets | `...::test_phase_budget_exhaustion_escalates_without_new_intent`, `...::test_final_budget_guard_runs_before_responsible_routing`; `>=`→`>`/guard reorder mutation 검출 |
| 4 | decision-first BLOCKED | `...::test_needs_input_and_conflict_block_before_quality_route`; PASS quality를 주어도 adapter calls 불변, ordering mutation 검출 |
| 5 | separate phase/final PASS | `...::test_phase_pass_cannot_complete_without_final_pass`, `...::test_final_pass_cannot_replace_missing_phase_pass`; bypass edge mutation 검출 |
| 6 | pure deterministic router/trace | `test_deterministic_workflow_contracts.py::test_same_validated_state_event_has_same_route_and_action`; randomized dict insertion/order에도 canonical equality |
| 7 | command/event replay | `test_deterministic_workflow_adapters.py::test_replayed_command_event_does_not_duplicate_effect_or_budget`; dedupe 제거 mutation 시 counters/calls 증가 |
| 8 | checkpoint reconstruction | `test_deterministic_workflow_graph.py::test_same_thread_memory_checkpoint_reconstructs_same_next_node`; snapshot field 제거/변조 시 validation failure |
| 9 | node re-execution idempotency | `test_deterministic_workflow_adapters.py::test_crash_after_effect_before_settlement_reuses_receipt`; idempotency lookup bypass mutation 시 call count 2로 실패 |
| 10 | fail-closed validation | `test_deterministic_workflow_contracts.py::test_unknown_malformed_out_of_order_and_post_terminal_events_rejected`; validator allow mutation마다 terminal/action assertions 실패 |
| 11 | graph compile + lint | `test_deterministic_workflow_graph.py::test_unknown_target_and_no_entry_compile_fail`, `...::test_custom_lint_rejects_unreachable_dead_end_bad_route_and_terminal_edge`; dead node/route-map mutation 검출 |
| 12 | capability declaration | `test_deterministic_workflow_adapters.py::test_missing_required_capability_blocks_before_dispatch`; silent fallback mutation 시 call count/terminal reason 실패 |
| 13 | normalized adapter parity | `...::test_fake_and_orca_fixture_logical_traces_match`; route/order/verdict/ID 필드 하나를 변형해 comparator sensitivity 확인 |
| 14 | graph-doc parity | `test_validate_skills.py::test_workflow_graph_contract_parity_and_installed_tree`; Skill token 또는 graph constant mutation 시 validator 실패 |
| 15 | OS-28~30 compatibility | 기존 `test_decision_*`, `test_os29_*`, `test_clarification_protocol.py`, `test_run_logging.py` + new `test_existing_schema_versions_and_log_columns_unchanged`; golden constants mutation 검출 |
| 16 | full verification evidence | full unittest discover, `validate_skills.py`, `verify_package.py`, temp archive verify, `docs/LANGGRAPH_DEPENDENCIES.md`의 pinned inventory/package-membership assertions, installed parity, `git diff --check` 모두 성공 |

모든 deliverable은 AC 1~16 중 하나 이상에 연결된다. docs/OS-31/37 guide는 AC 8~9/12, dependency doc/requirements는 AC 16, migration bridge는 AC 13~15, fake adapter는 AC 1~13, Orca adapter는 AC 12~13에 연결되므로 orphan deliverable이 없다.

### 사용자 지정 11개 검증 항목

| 검증 항목 | 담당 test | 실패 조건 감도 |
| --- | --- | --- |
| 정상 단계 진행/PASS | graph `test_full_happy_path...` | phase/final node 누락 또는 순서 swap 시 exact trace 실패 |
| Reviewer FAIL 후 동일 단계 재실행 | graph `test_phase_reviewer_fail...` | correction phase/iteration/fresh intent ID가 다르면 실패 |
| iteration 초과 ESCALATED | graph 두 budget tests | 추가 intent count 0과 reason/counter를 함께 assert |
| clarification BLOCKED | graph `test_needs_input_and_conflict...` | quality PASS여도 다음 action 없음과 pending clarification ID assert |
| checkpoint 재구성 | graph `test_same_thread_memory_checkpoint...` | same next node/action ID/state digest를 비교하고 corrupt state 거부 |
| replay 결정성 | contracts `test_same_validated...`, adapter replay test | byte-equal canonical trace와 unchanged counters assert |
| side effect 중복 방지 | adapter `test_crash_after_effect...` | receipt reuse, effect count 1, settlement count 1을 각각 assert |
| 잘못된 전이/도달 불가 거부 | graph compile/lint + contracts invalid events | unknown edge, unreachable, dead-end, bad route, terminal edge를 별도 mutation fixture로 주입 |
| fake adapter Orca-independent | adapters `test_fake_adapter_full_workflow_without_orca_symbols` | PATH에서 Orca 제거/Orca methods monkeypatch fail 상태에서도 completed trace |
| 기존 Orca parity | adapters `test_fake_and_orca_fixture...` | normalized 필드별 diff를 출력하고 한 필드 mutation으로 comparator 검증 |
| 기존 test/skill/package 회귀 | full commands 아래 | command exit code와 skip 허용 목록, archive/source membership, whitespace를 독립 확인 |

### 실행 명령과 시점

IMPLEMENTATION 중 각 W item 뒤 targeted tests를 실행하고, W6 뒤 `python3 scripts/validate_skills.py`, W7 뒤 `python3 scripts/verify_package.py`를 실행한다. TEST gate에서는 다음을 깨끗한 repo root에서 다시 실행한다.

```text
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/validate_skills.py
python3 scripts/verify_package.py
python3 scripts/validate_workflow_graph_docs.py
python3 -m unittest scripts.test_deterministic_workflow_contracts
python3 -m unittest scripts.test_deterministic_workflow_graph scripts.test_deterministic_workflow_adapters
python3 scripts/build_release.py --output <mktemp-dir>/orca-skills-<version>.tar.gz
python3 scripts/verify_package.py --archive <mktemp-dir>/orca-skills-<version>.tar.gz
git diff --check
```

두 lane 모두 동일한 `unittest discover` 명령과 unittest의 `Ran N tests ... OK (skipped=M)` 결과를 사용한다. **Dependency-present lane**은 사전 구성된 pinned 환경에서 실행하며 현재 baseline skip 6개는 그대로 allowlist하고 OS-40 LangGraph test skip은 0개여야 한다; 따라서 총 skip은 baseline allowlist와 정확히 일치해야 한다. **Dependency-absent lane**은 uninstall, 새 venv 또는 network download를 하지 않고 test helper의 temporary `MetaPathFinder`가 `langgraph` 및 그 submodule import에만 `ModuleNotFoundError`를 내도록 한 subprocess에서 동일 discover를 실행한다(이미 import된 해당 entries는 child process의 `sys.modules`에서 제거하고 종료 시 process와 함께 폐기). 이 lane은 기존 baseline 6개와 명시적으로 열거한 graph-dependent test classes만 skip되고, pure contract/routing/spec/validator 및 모든 legacy test는 실행되어야 하며 unexpected error/failure/skip은 실패다. 버전은 `importlib.metadata.version("langgraph")`로 확인한다. package test는 `requirements-langgraph.txt`와 `docs/LANGGRAPH_DEPENDENCIES.md`의 exact pins/license 표, canonical/installed package tree가 source/archive에 포함되고 `artifacts/`, credentials, runtime handles가 없는지를 assertion으로 확인하므로 별도 미정의 “dependency/license validator”를 전제하지 않는다.

## Risks

| ANALYSIS 위험 | 구체적 완화 |
| --- | --- |
| 산문↔legacy loop↔graph 3자 drift | graph spec/router를 executable source로 지정; legacy loop production import 금지; Skill anchor와 legacy parity oracle을 validator/test로 결속; routing 변경은 graph에만 반영 |
| 너무 이른 경직화 | OS-40 authoritative five phases와 현행 BUGFIX/REFACTORING만 closed vocabulary로 모델링; adaptive composition/preset plugin API는 만들지 않음 |
| 잘못된 전이의 결정론적 확산 | decision-first, budget-first, phase/final PASS 분리, responsible/D order에 truth-table + negative mutation fixtures 적용 |
| 실행 권한 확대 | core에 subprocess/Orca/agent command/terminal type 금지 static scan; structured intent와 adapter capability allowlist; OS-37 process 실행 미구현 |
| 관찰성 회귀 | normalized logical trace를 필수 state/evidence로 하고 기존 ORCHESTRATOR/TIMING/final audit adapter writes와 stable ID로 연결; parity에서 trace 필드를 비교 |
| checkpoint 오염/중복 side effect | intent-before-effect/settlement-after-effect 두 checkpoint, canonical stable ID, receipt conflict detection, crash window test |
| capability 축소 | required capability set을 state validation 전에 비교하고 missing set을 explicit BLOCKED reason으로 기록; dispatch count 0 assert |
| OS-28~30 schema 회귀 | schema/version/log-column golden tests와 기존 full suite 유지; new namespace만 사용하고 historical artifact rewrite 없음 |
| optional dependency가 legacy install 파괴 | pure modules/validators의 LangGraph import 금지, explicit engine error, expected-only skip lane + installed full lane |
| installed Skill에서 engine 누락/불일치 | release manifest required paths, exact recursive source-installed parity, archive content test |

ANALYSIS Reviewer의 N-001~N-003은 correction된 승인 산출물에 이미 모두 반영되었고 PLAN에서도 `skill_policy`, `final_report`, review isolation/eval, exact ledger/lifecycle 근거를 work/impact 대상으로 유지한다. 추가 non-blocking finding은 승인된 review에 없다.

## Review Feedback Resolution

- **F-001 — RESOLVED:** `In scope`, dependency policy, AC 16, 실행 명령과 두 lane 통과조건을 repository/CI의 실제 runner인 `python3 -m unittest discover -s scripts -p 'test_*.py'`로 교체했다. 신규 test는 `unittest.TestCase`, dependency skip은 `find_spec` + `importlib.metadata.version` + `unittest.skipUnless`/`SkipTest`로 고정했으며 `pytest.importorskip`와 `langgraph.__version__` 전제를 제거했다. present lane은 기존 baseline skip 6개만, absent lane은 baseline 6개와 명시된 graph-dependent class만 허용하고 unexpected skip/error/failure를 거부한다.
- **N-001 — 반영:** AC 16의 존재하지 않는 dependency/license validator를 제거하고 `docs/LANGGRAPH_DEPENDENCIES.md` pinned license inventory와 package-membership assertions를 실제 증거로 지정했다.
- **N-002 — 반영:** dependency 제거/venv/network 작업 없이 child process의 temporary `MetaPathFinder`로 `langgraph` namespace import만 차단하고 같은 unittest discover를 실행하는 absent-lane 절차를 명시했다.

## Completion Criteria

- DESIGN이 typed schema, graph truth table/lint, port contracts, stable ID/checkpoint sequence, legacy loop disposition과 normalized parity trace를 모호함 없이 확정할 수 있다.
- AC 1~16과 사용자 지정 11개 검증 각각에 deliverable/test/mutation-sensitive failure assertion이 연결되어 있다.
- graph가 유일한 executable transition definition이며 Skill, adapters, compatibility bridge 어디에도 독립 routing loop가 추가되지 않는다.
- dependency-present full engine과 dependency-absent legacy 환경의 기대 behavior, exact pins/licenses/package paths가 명시되어 있다.
- OS-31/37/38 및 hosted/process/historical rewrite 범위가 extension point 이상으로 확대되지 않는다.
- IMPLEMENTATION은 production code와 unit tests를 함께 작성하고, TEST는 full regression/skill/package/archive/parity/diff 증거를 독립 수집한다.
- 이 PLAN 외 repository 파일을 변경하지 않았고 승인된 ANALYSIS를 수정하지 않는다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "The authoritative OS-40 requirements and approved analysis resolve the implementation scope; all remaining engineering choices are fixed in this plan without requiring user-authority input.",
  "scope": "This phase's own conduct at this iteration."
}
```
