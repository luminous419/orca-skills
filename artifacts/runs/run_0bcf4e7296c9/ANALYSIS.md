# Worker Result

STATUS: COMPLETE
DECISION_GATE_STATE: CLEAR

## Request Summary

OS-40의 권위 있는 요구사항을 기준으로 현재 prompt-owned workflow control과 이미 구현된 deterministic primitive를 식별하고, Orca I/O와 runtime-neutral core의 경계, LangGraph 0.2.76의 실제 검증 범위, 배포·라이선스 영향, 보존해야 할 OS-28~OS-30 계약을 분석했다. 이 단계는 production code를 변경하지 않으며 다음 PLAN/DESIGN 단계의 migration matrix와 경계 결정을 위한 근거만 제공한다.

## Current State

현재 production control plane은 `orca-worker-reviewer-orchestration/SKILL.md`를 Coordinator LLM이 해석하는 방식이다. 반면 `scripts/orca_runtime_harness.py`에는 실제 Orca를 호출하는 integration harness와 상당한 fail-closed primitive가 함께 존재하지만, 일반 workflow 전체의 단일 실행 graph는 아니다. 두 discovery 문서도 이 상태를 각각 “검증된 primitive는 있으나 production event loop는 prompt가 소유”한다고 진단한다 (`docs/deterministic_flow_idea.review_by_gpt_sol.md:104-122`, `docs/deterministic_flow_idea.review_by_opus.md:24-53`).

현재 브랜치는 `feat/os-40-langgraph-engine`이며, 작업 시작 시 다른 run과 archive를 포함한 기존 untracked artifact가 다수 존재했다. 이 분석은 지정된 이 파일 외에는 변경하지 않는다.

## Findings

### A. workflow control ownership / migration matrix 입력

| 규칙 | 현재 소유 분류 | 근거와 해석 |
| --- | --- | --- |
| canonical phase와 허용 phase/order | 둘 다 소유(중복) | 산문은 phase vocabulary/order와 invalid handling을 정의한다 (`SKILL.md:877-940`). 코드는 `task_context.CANONICAL_PHASES`, `require_workflow_phase`를 harness가 사용하고, run 시작에서 requested phase를 검증한다 (`orca_runtime_harness.py:1543-1560`). 다만 전체 phase-to-phase execution graph는 산문 소유다. |
| phase gate 및 PASS 후 다음 phase/final review | 둘 다 소유(중복) | 독립 gate와 PASS routing은 `SKILL.md:940,1775-1793`; `E2EHarness.run_workflow`도 requested phase를 순차 실행하고 전부 통과한 뒤 final-review loop로 진입한다 (`e2e_harness.py:1925,2081-2121`). Orca harness는 task dependency primitive만 소유한다 (`orca_runtime_harness.py:1667-1693`). |
| reviewer FAIL correction loop | 둘 다 소유(중복) | 산문 loop는 `SKILL.md:1795-1824`. 실행 코드는 phase-local bounded loop (`e2e_harness.py:1098-1106`)와 final finding correction helper (`1857-1924`)를 소유하며, Orca harness도 attempt/settlement primitive를 제공한다 (`orca_runtime_harness.py:1837-2149`). |
| risk axis | 둘 다 소유(중복) | LOW/MEDIUM/HIGH 차이는 `SKILL.md:972-1075`; `skill_policy.load_risk_contract`가 embedded risk contract를 단일 parser로 읽고 (`skill_policy.py:48-69,129-164`), `e2e_harness.py:19`가 이를 실행 경로에서 사용한다. Orca harness도 run-scoped risk와 LOW task graph를 코드화한다 (`orca_runtime_harness.py:688-735,1554-1560,1676-1693`). |
| phase/final iteration 두 domain과 exhaustion | 둘 다 소유(중복) | 두 counter/ESCALATED 의미는 `SKILL.md:1826-1879`; `run_workflow`는 `phase_iterations`와 `final_review_iterations`를 독립 run state로 보유 (`e2e_harness.py:1925-1946`)하고 final/phase exhaustion을 dispatch 전에 차단한다 (`2254-2261,2306-2310,2400-2404`). |
| mandatory test gate | 둘 다 소유(중복) | 산문은 `SKILL.md:1883-1931`. 코드는 `UNIT_TEST_GATED_PHASES`와 strict parser를 정의 (`e2e_harness.py:108,365-391`)하고 LOW affirmative evidence/BLOCKED branch를 실제 gate에서 강제한다 (`1320-1343`). `fake_worker.py:201-206`은 이 입력 vocabulary를 생성한다. |
| Final Adversarial Review T0~T5a | 둘 다 소유(중복) | 산문 state machine은 `SKILL.md:2063-2248`; executable loop는 T1 PASS (`e2e_harness.py:2249-2251`), T2 budget-first (`2253-2261`), T3 routing (`2263-2295`), T4 correction (`2296-2369`), T5a revalidation (`2370-2437`)을 구현한다. Orca harness에는 final context/audit primitive도 있다 (`orca_runtime_harness.py:2285-2347,3439-3519`). |
| downstream set D | 둘 다 소유(중복) | 산문 HIGH-only suffix는 `SKILL.md:2199-2241`; `downstream_revalidation_set`은 canonical suffix를 계산하는 순수 함수 (`e2e_harness.py:497-517`)이고 HIGH-only call site는 `2382-2392`다. |
| decision boundary B1/B2/B3와 decision-first ordering | 둘 다 소유(중복) | boundary/ordering은 `SKILL.md:1077-1104,1775-1780`. `decision_policy.py:498-516,982-1047,1189-1329`가 policy/transition validation을, `decision_gate.py:317-436,442-514,655-806,987-1023`이 closed record, parsing, admission, verification을 소유하며 harness가 `_b1_guard`와 settlement recording에서 호출한다 (`orca_runtime_harness.py:2348-2625`). |
| decision ledger/idempotent immutable write | 이미 코드가 소유 | ledger identity는 `decision_gate.py:292-300`, schema/version과 validation은 `61-62,366-436`; append/read/collision 처리는 `run_logging.py:1943-2149`. 산문은 의미와 boundary를 설명·고정한다. |
| lifecycle settlement/terminal ownership/reuse | 둘 다 소유(중복) | Skill §6의 lifecycle rules에 대응해 harness가 role/origin vocabulary, settlement, reuse eligibility, four-axis accounting, finalize-once를 구현한다 (`orca_runtime_harness.py:79-231,834-1512`). 이는 graph core가 아니라 Orca adapter로 이동할 부분이다. |
| quality profile 및 agent profile resolution | 이미 코드가 소유(산문 계약도 존재) | quality parsing/resolution/gate mapping은 `quality_profile.py:331-638`; role routing과 command safety는 `agent_profile.py:351-877`. harness는 quality를 run boundary에서 한 번 resolve한다 (`orca_runtime_harness.py:1561-1572`). |
| clarification terminal block | 이미 코드가 소유(transport-independent schema), resume은 미구현 | `clarification_protocol.py:28-32,71-116,118-223,526-1294`가 v2 artifacts와 `HumanApprovalPort`를 제공한다. Skill은 resume이 terminal-only라고 명시 (`SKILL.md:1102`, `2274-2284`); consumption/cross-session resume은 OS-31이다. |
| log/audit/provenance schemas | 이미 코드가 소유 | append-only log columns은 `run_logging.py:63-108`, final-review audit schema는 `917-925`, write/read/export는 `2511-3138`. Skill과 validator가 parity를 잠근다. |

결론적으로 OS-40은 routing을 새로 발명하는 작업이 아니라 `e2e_harness.py`가 이미 코드화한 phase/final-review routing, 두 iteration domain, correction/D 계산을 LangGraph의 단일 executable definition으로 승격·대체하는 작업이다. 기존 schema validators, ledger/audit writers, quality/agent/clarification 계약은 graph node가 호출하는 domain service로 재사용해야 한다.

### B. OrcaRuntimeHarness 결합 지도와 분리 경계

| 영역/메서드 | 현재 역할 | 목표 경계 |
| --- | --- | --- |
| `_resolve_orca`, `_exec_orca`, `call`, `preflight` (`794-830`, `1513-1541`) | executable resolution, subprocess, JSON protocol/version guide | 전부 `OrcaAgentExecutionAdapter`/capability probe에 남긴다. Core import 금지. |
| `start_run`, `create_task`, `create_phase_graph`, `task_status` (`1543-1721`) | workflow validation과 Orca Run/Task 생성이 한 메서드에 결합 | phase/risk validation과 intent 생성은 core pure function; Run/Task/dependency materialization은 adapter. |
| `start_worker`, `_check`, `_ack`, `wait_for_done` (`1837-1971`) | dispatch/typed wait/ack/settlement observation | `AgentExecutionPort` adapter 구현. terminal/dispatch handle은 adapter-owned runtime state이며 checkpoint에 넣지 않는다. |
| `cleanup_authority`, `close_allowed`, reuse/settlement/accounting (`295-323`, `834-1512`) | 대부분 deterministic이지만 Orca receipt vocabulary에 결합 | generic stable intent/settlement/idempotency는 core; Orca ownership/release receipt 해석과 terminal cleanup은 adapter policy. |
| reviewer output parsers, decision guards (`634-686`, `2348-2625`) | normalized result와 OS-29 validation | pure contract layer로 승격 가능. Markdown parsing은 ingress compatibility adapter로 격리한다. |
| logging/audit/clarification calls (`2150-2811`) | workflow facts와 filesystem/Orca evidence capture 혼합 | core는 normalized immutable evidence intent만 emit; `ArtifactStorePort`, `HumanApprovalPort`, Orca provenance capture가 수행한다. |
| `run_existing_task`, `run_attempt`, `finish` (`2840-3050`, `3221-3294`) | attempt orchestration, lifecycle, decision/log settlement | node/action executor로 분해한다. 이 메서드들을 core loop로 그대로 승격하지 않는다. |
| scenario runners (`3373-3974`) | fake agents를 쓰지만 real-Orca-shaped integration scenarios | parity fixtures로 재사용하되 새 fake adapter의 core trace를 기준으로 비교한다. |

핵심 경계는 “core가 stable action intent와 normalized settlement event를 만들고 검증”하며, adapter는 “그 intent를 Orca Run/Task/Dispatch/terminal 동작으로 실행하고 관측”하는 것이다. `RuntimeAttempt`에 있는 terminal/dispatch 필드는 checkpoint state가 아니라 adapter settlement envelope 또는 runtime state port에 저장해야 한다. side effect 전에 stable intent ID를 checkpoint하고, adapter는 그 ID로 create/start/write를 deduplicate한 뒤 별도 settlement event를 반환해야 한다.

`e2e_harness.py`는 이 경계와 별개로 이미 완전한 imperative workflow loop이므로 새 graph와 함께 production-equivalent transition engine으로 남길 수 없다. PLAN의 처분 선택지는 (1) 순수 parser/router/D 계산을 추출해 graph nodes/conditional edges가 직접 사용하는 core로 승격하고 imperative `run_workflow`는 제거, (2) graph가 같은 behavior를 대체한 뒤 파일 전체를 폐기, (3) 변경 없이 test-only legacy parity oracle로 존치하되 새 engine의 실행/라우팅 경로에서는 import하지 않는 것이다. (1)은 검증된 로직 재사용이 크고 점진적이나 추출 중 결합 해체가 필요하고, (2)는 단일 source가 명료하나 회귀 위험과 재작성량이 크며, (3)은 비교 baseline을 보존하지만 산문↔legacy loop↔graph의 3자 drift를 validator로 관리해야 한다.

기존 fake baseline은 세 층이다. `fake_worker.py`는 machine-readable Worker 결과와 `UNIT_TEST_STATUS`를 생성 (`fake_worker.py:201-213`), `fake_reviewer.py`는 `responsible_phases`를 입력받아 finding을 생성 (`fake_reviewer.py:84-100`), `orca_fake_agent.py`는 Orca injected task/dispatch/capability를 파싱하고 두 fake CLI를 호출·settle한다 (`orca_fake_agent.py:21-42,45-83,86-120`). AC 13의 비교 양 끝은 동일 graph가 `FakeAgentExecutionAdapter`를 통해 이 deterministic fixture 결과를 소비한 logical trace와, `OrcaAgentExecutionAdapter`가 `orca_fake_agent.py`/기존 harness primitive를 통해 얻은 normalized logical trace다. Task/Dispatch/terminal receipt 같은 adapter 전용 필드는 비교에서 제외하되 stable intent/event identity와 node/phase/role/verdict/terminal transition은 일치해야 한다.

### C. LangGraph 0.2.76 실측

설치본 introspection으로 `StateGraph(state_schema=...)`, `add_conditional_edges(source, path, path_map, then)`, `Command(update, resume, goto)`, `interrupt(value)`, `compile(checkpointer=..., interrupt_before/after=...)`, `MemorySaver`를 확인했다. 따라서 typed state schema, conditional routing/Command, interrupt boundary, thread-configured in-memory checkpoint/reconstruction을 제공한다. 다만 Python `TypedDict` 자체는 runtime domain validation이 아니므로 closed vocabulary, event order, digest/head binding은 별도 pure validator가 책임져야 한다.

AC 11 실측 결과:

- 존재하지 않는 edge target은 compile 시 `ValueError: Found edge ending at unknown node`로 거부되었다.
- START edge가 없는 graph는 compile 시 `ValueError: Graph must have an entrypoint`로 거부되었다.
- START에서 도달할 수 없는 `dead` node는 compile에 성공했고 invoke에서도 무시되었다. 즉 0.2.76 compile은 일반 unreachable-node 검사를 제공하지 않는다.
- conditional router가 `path_map` 밖의 `bogus`를 반환하는 graph도 compile에 성공하고 invoke 시 `KeyError`가 났다. 가능한 route의 정적 완전성은 자동 증명되지 않는다.

그러므로 AC 11은 (1) LangGraph compile의 invalid target/entrypoint 검증, (2) workflow contract에서 START 기준 reachability와 모든 terminal/correction path의 역도달 가능성을 검사하는 자체 graph-lint pure function, (3) closed router output-to-node map 검사 및 mutation test의 조합으로 만족해야 한다. 이 lint는 별도 transition engine이 아니라 동일 graph specification을 정적으로 검사해야 한다.

### D. dependency, license, packaging 영향

환경 metadata 실측: `langgraph==0.2.76`은 `langchain-core>=0.2.43,<0.4.0`(긴 제외 범위 포함), `langgraph-checkpoint>=2.0.10,<3`, `langgraph-sdk>=0.1.42,<0.2`를 요구한다. 현재 설치본은 각각 0.3.80, 2.1.1, 0.1.74이다. `langgraph-checkpoint`는 `ormsgpack`, `langgraph-sdk`는 `httpx`/`orjson`, `langchain-core`는 `langsmith`, pydantic, PyYAML, requests 계열 등을 끌어온다. LangSmith hosted 사용은 필요하지 않지만 Python distribution은 transitive dependency다.

설치된 distribution license files 기준 LangGraph, checkpoint, SDK는 MIT이고, metadata 기준 langchain-core/langsmith도 MIT이다. `ormsgpack`과 `orjson`은 MIT/Apache-2.0 dual-license 파일을, requests는 Apache-2.0, httpx는 BSD-3-Clause를 제공한다. PLAN에서는 lock 형식과 전체 transitive license inventory 생성/검증 방식을 확정해야 한다. 저장소 자체 license 선택은 아직 열려 있음이 `docs/COMPATIBILITY.md:162`와 `README.md:738-748`에 명시되어 있으므로 dependency license와 프로젝트 license 결정을 혼동하면 안 된다.

현재 저장소는 standard-library-only를 전제로 한다 (`validate_skills.py:1086-1092`, `docs/COMPATIBILITY.md:93-100`). release manifest는 `scripts/` 전체를 archive에 포함하지만 installed orchestration Skill은 명시된 template/review와 `tools/run_logging.py`, `tools/clarification_protocol.py`만 허용한다 (`release_manifest.py:25-48,76-88,95-133`). 따라서 engine package 위치와 installed Skill에 포함할 launcher/engine files를 manifest에 명시적으로 추가해야 하며, `verify_package.py:19-61`의 exact archive content 검증도 함께 갱신해야 한다. source/installed exact parity는 현재 logging tool에 대해 `validate_skills.py:2974-3000`이 강제한다; OS-40 tool에도 동일한 단일 canonical source/copy parity 또는 import 가능한 packaged module 전략이 필요하다.

“LangGraph 미설치 환경에서 기존 테스트가 깨지지 않음”은 기존 standard-library validators가 새 module을 import하는 경로에서 LangGraph를 eager import하지 않도록 분리하여 만족할 수 있다. 권장 형태는 core package의 LangGraph-dependent entrypoint만 명시적 dependency error를 내고, 기존 validators/package verification은 AST/text contract 또는 optional test marker로 동작하게 하는 것이다. 단, OS-40 engine 자체를 dependency 없이 지원한다고 조용히 축소해서는 안 된다. CI에는 pinned dependency를 설치한 OS-40 full lane과 dependency가 없는 legacy regression lane이 모두 필요하다.

### E. OS-28/OS-29/OS-30 불변 계약

다음은 OS-40이 schema generation이나 의미를 변경하지 않고 consumer로만 사용해야 한다.

- `decision_policy`의 state/reason-code/entry predicate/evidence/transition 계약 (`decision_policy.py:138-193,498-516,982-1047,1189-1329`).
- decision gate declaration은 정확히 하나, fenced authority record가 우선이며 decision axis가 quality axis보다 먼저라는 규칙 (`SKILL.md:1077-1104,1775-1780`; `decision_gate.py:442-514`).
- decision ledger record schema v1, `(run, phase, iteration, boundary, sequence)` identity, append-only/collision behavior (`decision_gate.py:61-62,335-436`; `run_logging.py:1943-2149`).
- B1/B2/B3 및 verification binding/admission 규칙 (`decision_gate.py:560-806,932-1023`).
- clarification request/response v2, decision/lineage v1, stable identity, immutable lineage와 기존 v1 historical artifact 비마이그레이션 (`clarification_protocol.py:28-39,225-308,409-524`; `docs/ROADMAP.md:303-307`).
- `HumanApprovalPort` 형상과 OS-31 이전에는 terminal block일 뿐 resume 소비를 구현하지 않는 범위 (`clarification_protocol.py:111-116`; `SKILL.md:1102,2274-2284`).
- `ORCHESTRATOR_LOG_COLUMNS`와 `TIMING_LOG_COLUMNS`의 순서 및 append-only semantics (`run_logging.py:63-108,318-335`).
- final review audit schema 1.0, export schema 2.0, redaction/path/provenance contract (`run_logging.py:917-979,1385-1432,2511-3138`).
- historical artifacts와 OS-28~30 evidence는 rewrite하지 않으며 새 checkpoint/event namespace로 참조만 한다.

### F. 구체적 위험

1. **정책 3자 중복:** 현재 `SKILL.md:1775-1875`, `e2e_harness.py:1925-2437`, 새 graph가 각각 FAIL/budget 전이를 소유하면 세 구현이 drift한다. 특히 산문의 final T2 budget-first guard (`SKILL.md:2211`)는 기존 코드에도 “FIRST statement on the FAIL edge”로 중복되어 있다 (`e2e_harness.py:2253-2261`); graph 추가 후 어느 한쪽만 바뀌면 budget 소진 뒤 dispatch가 발생할 수 있다. graph를 executable source로 만들고 legacy loop의 처분을 확정하며 docs/parity assertion을 그 source에 결속해야 한다.
2. **너무 이른 경직화:** specialized phase 조합과 LOW/MEDIUM/HIGH 차이 (`SKILL.md:942-1075`)까지 확장 가능한 preset abstraction으로 일반화하면 OS-40 밖의 adaptive composition을 고정한다. authoritative 5-phase + 현행 specialized contract만 모델링한다.
3. **잘못된 전이의 결정론적 확산:** responsible phase나 D suffix 계산 오류는 모든 replay에서 동일하게 downstream을 누락한다. mutation-sensitive tests는 budget-first, decision-first, phase-PASS/final-PASS 비대체, D ordering을 각각 뒤집었을 때 실패해야 한다.
4. **실행 권한 확대:** harness는 subprocess로 Orca CLI를 실행하고 terminal command를 구성한다 (`orca_runtime_harness.py:801-814,1723-1836`). 이를 core에 넣으면 checkpoint에 credential/handle이 새고 arbitrary argv 권한이 넓어진다. capability-declared adapter와 structured intent allowlist로 제한한다.
5. **관찰성 회귀:** 기존 harness가 생성하는 ORCHESTRATOR/TIMING rows, lifecycle four-axis와 final audit를 새 graph가 생략하면 control은 결정론적이어도 provenance가 약해진다. logical transition trace와 adapter execution trace를 분리하되 stable action/event ID로 결합해야 한다.
6. **checkpoint 오염/중복 side effect:** node 재실행 시 checkpoint보다 Orca create/write가 먼저 수행되면 duplicate Task/Dispatch/artifact가 생긴다. intent-before-effect와 settlement-after-effect, adapter idempotency lookup을 강제해야 한다.
7. **capability 축소:** fake/direct adapter에 dependency edge나 authoritative settlement가 없는데 Orca와 같은 보증으로 처리하면 안전성이 낮아진다. 요구 capability가 없으면 dispatch 전 BLOCKED/ESCALATED로 fail closed해야 한다.

## Impact Scope

직접 영향은 새 runtime-neutral engine package/state schema/router/graph lint, port 및 fake/Orca adapters, checkpoint/idempotency storage boundary, scenario/parity tests다. 통합 영향은 `e2e_harness.py` loop의 추출/대체/격리, `fake_worker.py`·`fake_reviewer.py`·`orca_fake_agent.py` fixture 활용, `orca_runtime_harness.py` primitive 분리, `skill_policy.py` risk parser와 `final_report.py:45-65` terminal counter vocabulary, `workflow_contract.py`와 OS-28~30 validators 재사용, `review_isolation.py`/`final_review_eval.py`가 보호하는 final-review evidence 경계, Skill control prose 및 graph-doc parity validator, README/INSTALL/COMPATIBILITY/release manifest/package verifier다. historical artifact, 기존 ledger/audit/clarification schemas, Jira/PR transport, production checkpointer, direct PTY 구현은 영향 범위 밖이다.

## Dependencies / Constraints

- Python 3.11과 `langgraph==0.2.76`을 기준점으로 삼되 transitive compatible set을 재현 가능하게 pin해야 한다.
- `StateGraph`가 executable transition의 단일 정의여야 한다. graph 밖 별도 workflow loop 또는 동일 전이 table을 만들 수 없다. pure router/validator는 graph conditional edge가 직접 호출하는 동일 함수여야 한다.
- graph state는 JSON/checkpointer-safe domain 값과 stable IDs만 포함한다. process/terminal handles, credentials, live clients는 adapter/runtime port에 남긴다.
- MemorySaver는 OS-40의 테스트/동일-session reconstruction에 적합하지만 production durability 보증이 아니다. production durable checkpointer와 완전한 resume은 OS-31 extension port로 남긴다.
- artifact/repository head binding은 immutable evidence에 포함하고, 같은 command/event ID replay는 state/counter/artifact를 변화시키지 않아야 한다.
- dependency가 없는 legacy environment와 pinned dependency full environment를 별도 검증해야 하며, 필수 capability/engine dependency 누락은 명시적으로 fail closed한다.

## Risks

가장 높은 implementation risk는 기존 harness를 통째로 graph node 안에서 호출해 “LangGraph wrapper + 기존 imperative semantics”라는 이중 engine을 만드는 것이다. 다음은 schema를 새로 발명해 OS-29/30의 identity와 lineage를 깨뜨리는 것, 그리고 MemorySaver 실험을 durable resume으로 과대 주장하는 것이다. 마지막으로 package manifest에 module을 포함하고 installed copy parity를 추가하지 않으면 source test는 통과해도 실제 Skill 설치본에서 engine이 사라질 수 있다.

## Assumptions / Unknowns

확인된 사실과 별개로 PLAN에서 결정해야 할 열린 선택지는 다음과 같다.

- canonical graph contract의 파일 형식: Python typed constants/dataclasses를 단일 source로 할지, versioned JSON schema를 생성 입력으로 할지.
- graph state reducer 모델과 event/action closed vocabulary, stable ID canonicalization/hash 규칙.
- `Command` 중심 routing과 conditional edges 중 어느 조합이 checkpoint-before-effect를 가장 명료하게 보장하는지.
- artifact/head binding에서 dirty worktree를 표현하는 canonical digest와 immutable evidence storage layout.
- `e2e_harness.py`를 순수 로직 추출 후 제거할지, graph로 전면 대체할지, test-only parity oracle로 격리할지. 어떤 선택도 새 graph와 독립적인 production transition engine을 남겨서는 안 된다.
- Orca adapter가 기존 `orca_runtime_harness.py`를 composition으로 감쌀지, 검증된 primitive를 작은 service로 추출할지.
- installed Skill packaging: engine package를 Skill 내부에 복사하고 exact parity를 검증할지, repository-level package를 설치 dependency로 만들지.
- dependency lock/constraints 파일과 license inventory 생성 방식. 프로젝트 자체 license 선택은 별도 owner decision으로 남는다.
- “fake와 Orca parity”의 canonical comparison 단위: adapter-specific receipt가 아닌 normalized logical node/action/event trace가 적합하나 정확한 필드 집합을 DESIGN에서 잠가야 한다.
- AC 11 graph lint가 요구할 reachability 정의: 모든 declared node의 START reachability 외에 각 nonterminal에서 terminal로의 도달 가능성과 correction cycle의 bounded escape까지 포함할지.

이 선택들은 모두 reversible architecture choices이며 현재 phase에서 사용자 권한을 요구하지 않는다. authoritative 요구사항, repository 정책 및 후속 PLAN/DESIGN gate가 결정을 내릴 충분한 근거를 제공한다.

## Review Feedback Resolution

- **F-001 — RESOLVED:** §A의 여섯 오분류를 모두 `둘 다 소유(중복)`으로 고쳤다. phase sequence/gate (`e2e_harness.py:1925,2081-2121`), correction (`1098-1106,1857-1924`), 두 iteration domain/exhaustion (`1925-1946,2254-2261,2306-2310,2400-2404`), mandatory test gate (`108,365-391,1320-1343`), T1~T5a (`2249-2437`), D (`497-517,2382-2392`)를 직접 확인해 근거로 추가했다. §F1도 산문↔`e2e_harness`↔새 graph의 3자 drift와 동일한 T2 budget-first 중복을 명시했다.
- **F-002 — RESOLVED:** §B에 기존 imperative loop의 세 처분 옵션(순수 로직 추출/graph 대체 후 폐기/test-only parity oracle 격리)과 trade-off를 추가했다. 또한 `fake_worker.py:201-213`, `fake_reviewer.py:84-100`, `orca_fake_agent.py:21-120`을 기존 fake baseline으로 식별하고, AC 13의 양 끝을 Fake adapter logical trace와 Orca adapter normalized logical trace로 정의했다.
- **N-001 — 반영:** §A risk 행에 `skill_policy.load_risk_contract`를 추가하고 §Impact Scope에 `final_report.py`, `review_isolation.py`, `final_review_eval.py` 및 fake fixture들을 포함했다.
- **N-002 — 반영:** ledger identity 근거를 실제 정의인 `decision_gate.py:292-300`으로 좁혀 추가했다.
- **N-003 — 반영:** `cleanup_authority`/`close_allowed` 인용을 실제 정의 범위 `orca_runtime_harness.py:295-323`으로 좁혔다.

## Recommended Next Step

PLAN은 먼저 versioned typed state/action/event 계약과 single-source graph specification을 정하고, 그 specification에서 LangGraph graph와 reachability/parity validation이 함께 파생되도록 작업 순서를 고정해야 한다. 이어 intent checkpoint → adapter effect(idempotency key) → settlement event의 protocol, capability matrix, legacy-without-LangGraph/full-with-LangGraph test lanes, packaging copy/parity 전략을 명시한다. DESIGN 전 gate로 OS-28~30 schema 재사용 목록과 “core 내 `orca`, subprocess, terminal/session/credential symbol 0건” 검사를 확정하는 것이 안전하다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "Repository evidence and the authoritative OS-40 requirements are sufficient for this analysis; remaining architecture choices are explicitly assigned to PLAN/DESIGN and require no user-authority decision at this boundary.",
  "scope": "This phase's own conduct at this iteration."
}
```
