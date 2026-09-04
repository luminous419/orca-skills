OS-40 FINAL ADVERSARIAL REVIEW (attempt 1)

=== RUN CONTEXT ===
run_id: run_0bcf4e7296c9
repository: /Users/<REDACTED:absolute_local_path>/aiAssistedProjects/orca-skills
branch: feat/os-40-langgraph-engine  (already created from origin/main @ 7bc228a; DO NOT switch branches)
ARTIFACT_ROOT: artifacts/runs/run_0bcf4e7296c9/
risk: high      requested_phases: ANALYSIS,PLAN,DESIGN,IMPLEMENTATION,TEST
max-iterations: 5   worker: codex-sol   reviewer: claude-opus

=== ORIGINAL OBJECTIVE (verbatim user request) ===
Jira OS-40 을 구현한다.

GOAL: Python LangGraph 기반의 runtime-neutral deterministic workflow engine을 구현한다.
워크플로 전이와 gate 판정은 코드가 결정하고, LLM은 각 단계의 분석/설계/구현/리뷰만 담당한다.
Core engine은 Orca 없이도 실행/테스트할 수 있어야 한다.

작업 기준:
- Jira OS-40 의 최신 Description 과 Acceptance Criteria 를 기준으로 한다 (본 spec의 OS-40 REQUIREMENTS 절에 전문 인용).
- docs/deterministic_flow_idea.review_by_gpt_sol.md 와 docs/ROADMAP.md 를 참고한다.
- main 최신 상태에서 만든 feat/os-40-langgraph-engine 브랜치 위에서 작업한다.
- 중단된 이전 run 이나 삭제된 브랜치의 상태를 이어받지 않는다. 이 run 은 새 run 이다.
- 기존 historical artifact 및 이 작업과 무관한 untracked 파일은 수정/삭제/커밋하지 않는다.
  (`artifacts/` 아래 다른 run 디렉터리, artifacts/archive/, artifacts/*.md 는 절대 건드리지 않는다.)

필수 설계:
- Python LangGraph StateGraph 를 사용한다.
- typed state 와 conditional edge 또는 Command 로 전이를 정의한다.
- graph 를 우회하는 별도 workflow loop 나 중복 transition engine 을 만들지 않는다.
- 상태 전이 조건, gate 판정, validation 은 순수 함수로 분리해 단위 테스트할 수 있게 한다.
- Core 에서 다음 요소를 분리한다: workflow state/transition, phase/gate/iteration 상태,
  terminal state (COMPLETED, BLOCKED, ESCALATED), agent 실행, artifact 저장,
  runtime state/checkpoint, human approval, clock 및 ID 생성.
- Orca 연동은 adapter 로 구현하고 Core 에서 Orca API, terminal/session handle, credential 에
  직접 의존하지 않게 한다.
- fake adapter 를 제공해 Orca 없이 전체 workflow 를 검증한다.
- 향후 OS-37 standalone CLI adapter 를 추가할 수 있는 경계를 유지한다.

LangGraph 제약:
- LangChain Agent, LangSmith, Agent Server 또는 hosted service 를 필수 의존성으로 만들지 않는다.
- 사용 버전을 고정하고 호환성/라이선스/패키징 영향을 문서화한다.
- checkpoint state 에는 process handle, terminal handle, credential 을 저장하지 않는다.
- node 재실행을 고려해 외부 side effect 에 stable ID 와 idempotency 를 적용한다.
- production durable checkpointer 와 완전한 cross-session resume 은 OS-31 범위로 남긴다.
  OS-40 에서는 이를 연결할 port 와 테스트 가능한 경계까지만 구현한다.

검증 (자동화된 테스트로 입증할 것):
- 정상 단계 진행과 PASS 전이
- Reviewer FAIL 후 동일 단계 재실행
- iteration 한도 초과 시 ESCALATED
- clarification 필요 시 BLOCKED
- checkpoint 기반 상태 재구성
- 동일 입력 replay 의 결정성
- node 재실행 시 side effect 중복 방지
- 잘못된 전이 및 도달 불가능 경로 거부
- fake adapter 기반 Orca-independent 실행
- 기존 Orca 경로와 핵심 전이 결과의 parity
- 기존 테스트, skill validation 및 package verification 회귀 없음
테스트가 단순히 구현 결과를 재서술하지 않고, 잘못된 전이/중복 실행/checkpoint 손상 같은
실패 조건을 실제로 검출하는지 확인한다.

완료 조건 (전체 run 기준, 각 phase 가 자기 몫만 담당):
- 관련 문서와 예제를 함께 갱신한다.
- OS-40 요구사항과 테스트 증거를 PR Description 에 연결한다.
- 변경 파일만 명시적으로 stage 한다. `git add -A` 는 사용하지 않는다.
- commit/push/Draft PR 생성은 Coordinator 가 모든 phase gate 와 Final Adversarial Review
  PASS 이후에 수행한다. Worker 는 commit/push/PR 을 하지 않는다.
- Jira 상태 변경과 PR merge 는 하지 않는다.

=== OS-40 REQUIREMENTS (Jira, authoritative, fetched 2026-09-02) ===
Summary: Implement Runtime-Neutral Deterministic Workflow Engine   Priority: P0/High

Goal: 현재 SKILL.md 의 프롬프트 해석에 의존하는 workflow control logic 을 Python LangGraph
기반의 runtime-neutral deterministic engine 으로 구현한다. Engine 은 ANALYSIS -> PLAN ->
DESIGN -> IMPLEMENTATION -> TEST 및 Final Adversarial Review 의 상태 전이, Worker/Reviewer
gate, correction loop, iteration budget 과 Responsible Phase routing 을 코드로 결정한다.
LLM 은 산출물 생성과 의미적 검토를 담당하지만, 다음 단계 선택/재시도/종료/차단 여부는
검증 가능한 graph state 와 routing rule 이 결정한다. LangGraph 가 graph execution, node
transition 과 checkpoint 경계를 담당한다. Orca 는 workflow semantics 의 소유자가 아니라
첫 번째 execution adapter 가 되며, 향후 standalone CLI adapter 도 동일 graph 를 사용한다.

LangGraph Architecture Requirements:
- Python StateGraph, typed state 와 conditional edge 또는 Command 를 사용한다.
- LangGraph graph 와 testable routing function 을 workflow transition 의 실행 가능한 단일 정의로 사용한다.
- LangGraph 와 별도로 동일한 전이 규칙을 수행하는 독자 event loop 나 병렬 transition engine 을 만들지 않는다.
- domain validation 과 routing 은 LangGraph runtime 과 분리해 순수 함수로 단위 테스트할 수 있어야 한다.
- LangChain agent abstraction, LangSmith, Agent Server 또는 hosted service 를 필수 의존성으로 추가하지 않는다.
- 사용할 LangGraph 버전과 호환 범위를 고정하고 dependency, license 와 package/release 영향을 문서화한다.
- checkpoint 에서 복구할 수 없는 process handle, credential 또는 runtime 객체를 graph state 에 저장하지 않는다.
- node 가 interruption 이후 재실행될 수 있으므로 외부 side effect 는 stable identity 와 idempotency guard 를 가진다.
- production durable checkpointer 선택과 완전한 human-decision resume 은 OS-31 범위로 유지한다.

Graph State and Flow — typed, closed machine-readable state 에는 최소 다음이 포함된다:
run/workflow identity; current phase 와 iteration; pending role 및 dispatch intent;
Worker/Reviewer/Final Reviewer result; quality verdict 와 decision state; blocking finding 과
Responsible Phase; correction 및 downstream revalidation 상태; consumed/remaining iteration
budget; pending clarification identity; artifact 와 repository head binding; terminal status 와
reason; processed command/event identity.
Graph 는 다음 흐름을 실행한다: phase Worker -> deterministic result validation -> phase Reviewer
-> PASS 시 다음 phase / FAIL 시 responsible phase correction -> 필요한 downstream revalidation
-> Final Adversarial Review -> complete, block 또는 escalate.
Side effect 이전에 intent 를 checkpoint 하고 실행 결과는 별도 settlement state/event 로 반영한다.

Ports: AgentExecutionPort (agent 시작, 명령 전달, 상태 조회, interrupt, settlement);
ArtifactStorePort (phase artifact 와 immutable evidence 저장/조회); RuntimeStatePort 또는
LangGraph checkpointer adapter (state, ownership, checkpoint); HumanApprovalPort (OS-30
request/response 및 OS-31 resume 입력); deterministic clock 또는 명시적 timestamp input;
adapter capability declaration.
Adapters: 기존 Orca orchestration adapter; fake adapter; 향후 OS-37 standalone CLI execution adapter.
Core graph 에는 Orca CLI, terminal handle, subprocess 또는 Claude/Codex 전용 타입을 포함하지 않는다.
Adapter 는 execution 과 observation 만 담당하며 workflow semantics 를 중복 구현하지 않는다.
Adapter 가 필요한 capability 를 제공하지 못하면 기능을 조용히 축소하지 말고 fail closed 한다.

Scope: OS-27 과 현재 repository 를 기준으로 LangGraph state/node/edge/routing contract 확정;
command/event/action identity 와 closed vocabulary 정의; checkpoint 기반 state reconstruction;
duplicate invocation, command/event replay 와 side-effect idempotency; malformed/unknown/
out-of-order input 의 fail-closed 처리; fake adapter 와 deterministic scenario test suite;
기존 OrcaRuntimeHarness 의 검증된 primitive 를 Orca adapter 로 분리/재사용; fake adapter 와
Orca adapter 의 logical transition trace parity; prompt-owned control logic 과 graph-owned
control logic 의 migration matrix; graph 로 이전된 routing 을 Coordinator LLM 이 다시 판단하지
않도록 Skill 축소; graph contract 와 Skill prose 의 drift 를 막는 validator 또는 parity test;
OS-31 WAITING_FOR_INPUT/resume extension point; OS-37 AgentExecutionPort implementation guide.

Acceptance Criteria:
1. 정상 경로에서 ANALYSIS -> PLAN -> DESIGN -> IMPLEMENTATION -> TEST -> Final Review ->
   COMPLETED 가 LangGraph node transition 으로 실행된다.
2. Reviewer FAIL 시 responsible phase correction Worker 와 fresh Reviewer 로 routing 된다.
3. iteration budget 소진 시 추가 dispatch 없이 ESCALATED 또는 계약된 terminal state 가 생성된다.
4. NEEDS_INPUT 또는 CONFLICT 이면 quality verdict 와 무관하게 다음 Worker/phase 가 실행되지 않는다.
5. phase PASS 와 Final Review PASS 는 서로 대체되지 않는다.
6. 동일 validated state/event 가 항상 동일 next node/action 을 선택한다.
7. 동일 command/event replay 가 중복 Task, Dispatch, artifact 또는 iteration consumption 을 만들지 않는다.
8. 같은 thread_id 의 checkpoint 를 복구하면 동일 next node 가 선택된다.
9. interruption/crash 이후 node 재실행이 외부 side effect 를 중복 생성하지 않는다.
10. malformed, unknown, out-of-order state/event 와 terminal 이후 transition 은 fail closed 한다.
11. graph compile/validation 으로 invalid edge 와 unreachable workflow path 를 탐지한다.
12. adapter capability 부족 시 명시적으로 차단한다.
13. fake adapter 와 Orca adapter 가 동일 scenario 에서 동일 logical transition trace 를 생성한다.
14. graph contract 와 Skill documentation 의 parity 가 자동 검증된다.
15. OS-28~OS-30 policy/schema 와 historical run artifacts 를 rewrite 하거나 의미 변경하지 않는다.
16. full unit tests, Skill validation, package/archive verification, dependency/license 검증,
    source/installed parity 와 `git diff --check` 가 통과한다.

Required Deliverables: LangGraph state/graph/routing specification; executable LangGraph engine;
port interfaces 와 normalized action/event schema; checkpoint 와 idempotency contract; fake
adapter 및 Orca adapter integration; deterministic scenario and mutation-sensitive test suite;
prompt-owned logic -> graph-owned logic migration matrix; OS-31/OS-37 integration guide;
dependency, compatibility 와 migration documentation; transition trace 및 validation evidence.

Out of Scope: Claude/Codex CLI 의 직접 PTY/process 구현 (OS-37); Orca source extraction study
(OS-38); production durable checkpointer 및 완전한 cross-session resume (OS-31); LangSmith,
Agent Server 또는 특정 hosted deployment; 특정 GUI/Slack/Jira/GitHub approval transport;
새로운 workflow preset 또는 adaptive phase composition; historical run/artifact rewrite;
기존 Orca adapter 제거.

Implementation Order: 1) LangGraph dependency/compatibility 결정과 typed state contract
2) graph node, edge 와 pure routing function  3) checkpoint, intent/settlement 와 idempotency
4) fake adapter 및 deterministic tests  5) Orca harness adapter integration
6) Skill routing migration 과 parity validation  7) compatibility, package 와 release verification

=== ENVIRONMENT FACTS (verified by Coordinator, do not re-derive) ===
- python3 = /Users/<REDACTED:absolute_local_path>/program/anaconda/anaconda3/envs/common/bin/python3 (3.11.8)
- langgraph 0.2.76 이미 설치됨; langgraph-checkpoint 2.1.1; langgraph-sdk 0.1.74;
  langchain-core 0.3.80; langsmith 0.3.45 (langchain-core 의 transitive dep).
  langgraph 는 MIT 라이선스.
- Orca CLI: <REDACTED:foreign_absolute_path> (runtime ready, appVersion 1.4.192)
- **[CORRECTED 2026-09-02 by Coordinator — 이전 dispatch 의 오류]** 저장소 테스트 runner 는
  pytest 가 **아니라** unittest 다. Coordinator 가 직접 실행해 확인한 사실:
    * `scripts/test_*.py` 20개 전부 unittest 기반이고 pytest 를 import 하는 파일은 0개다.
    * CI 는 `.github/workflows/ci.yml:37` 에서
      `python3 -m unittest discover -s scripts -p 'test_*.py'` 를 실행한다.
    * `python3 -m pytest scripts/ -q` 는 `scripts/fixtures/final_review_eval/subject/{base,head}<REDACTED:foreign_absolute_path>/`
      의 동일 basename 5쌍(test_validation/test_config/test_quota/test_policy/test_pipeline) 때문에
      `5 errors during collection` 으로 **중단되며 테스트가 하나도 실행되지 않는다.**
  따라서 이 run 의 authoritative 회귀 명령은 다음 하나다:
      python3 -m unittest discover -s scripts -p 'test_*.py'
  새 테스트도 unittest 로 작성하고 이 discover 경로에서 실행되게 한다. pytest 전용 기능
  (`pytest.importorskip`, `pytest.mark.*`, fixture 등)에 의존하지 않는다 — unittest discover
  아래에서는 skip 이 아니라 error 가 된다.
- langgraph 는 `__version__` 속성을 노출하지 않는다(`hasattr(langgraph,'__version__')` == False).
  버전 확인이 필요하면 `importlib.metadata.version('langgraph')` 를 쓴다. 조건부 skip 이 필요하면
  `unittest.skipUnless` / `raise unittest.SkipTest` 를 쓴다.
- 저장소에 이미 존재하는 결정론적 primitive: scripts/orca_runtime_harness.py,
  scripts/decision_gate.py, scripts/decision_policy.py, scripts/run_logging.py,
  scripts/clarification_protocol.py, scripts/workflow_contract.py, scripts/quality_profile.py,
  scripts/agent_profile.py, scripts/task_context.py, scripts/validate_skills.py,
  scripts/e2e_harness.py, scripts/fake_worker.py, scripts/fake_reviewer.py.
- Skill 패키지 원본: orca-worker-reviewer-orchestration/ 과 orca-worker-reviewer-loop/
  (설치본과 source/installed parity 가 validate_skills.py 로 검증된다).

=== RISK PROFILE ===
risk: high (source: explicit)
- 각 phase 는 Worker -> phase Reviewer 로 검증된다.
- Final Adversarial Review 는 필수다.
- Final Review FAIL 시 책임 phase correction -> 그 phase Reviewer -> downstream revalidation
  -> 새 Final Review.
risk 는 요청된 phase 집합을 늘리거나 줄이지 않으며, mandatory test gate 를 완화하지 않는다.

=== QUALITY GATE (profile-first) ===
profile_status: absent
profile_path: .orca/quality-profile.yaml (존재하지 않음 — 정상 상태)
applicable_quality_attributes: (none)
blocking_quality_attributes: (none)
general_gate:
  G1 explicit requirement violation
  G2 result does not work
  G3 severe regression
  G4 data loss / security / irreversible side effect
  G5 missing validation evidence
decision_priority: explicit_requirements > project_quality_attributes(none) >
                   current_phase_contract > minimal_general_gate
non_blocking_by_default: profile 에 없는 일반적 best practice / 설계 취향 / minor improvement 는
                         blocking finding 이 아니다.
verdict_semantics:
  PASS            -> RESULT: PASS
  PASS WITH NOTES -> RESULT: PASS   (blocking 없음 + non-blocking finding 1개 이상)
  FAIL            -> RESULT: FAIL   (blocking violation 1개 이상)
  BLOCKED         -> RESULT: FAIL   (신뢰할 수 있는 verdict 에 필요한 evidence 부족)

=== DECISION GATE (OS-28/OS-29, mandatory) ===
결과 본문에 다음 두 가지가 **각각 정확히 하나** 있어야 한다. 없거나 둘 이상이거나 서로
어긋나면 이 경계는 fail-closed 로 막히고 run 이 종료된다.

(1) 선언 line:
DECISION_GATE_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT

(2) authority record — fenced JSON block, 언어 태그는 반드시 `decision-gate`:
```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "<왜 이 경계에 열린 decision item 이 없는지>",
  "scope": "This phase's own conduct at this iteration."
}
```
- run/phase/iteration/boundary/sequence 등 ledger mechanics 는 Coordinator 가 stamp 한다.
  agent 는 decision 절반만 쓴다. 위 5개 key 외에 CLEAR 에 다른 key 를 넣지 않는다.
- ASSUMPTION_ALLOWED 를 쓰려면 reason_code(repository_policy | explicit_requirement |
  phase_contract | quality_profile_attribute), policy_source{role:"supports",kind,locator},
  reversibility, impact, retraction_condition, assumption, 그리고 여섯 safety fact
  (blast_radius, monetary_cost, security, privacy, compliance, long_term_lock_in) 를 모두
  명시해야 한다. 여섯 중 하나라도 참이거나 reversibility 가 irreversible 이면
  ASSUMPTION_ALLOWED 를 쓸 수 없다.
- NEEDS_INPUT / CONFLICT 는 run 을 종료시킨다. 실제로 사용자 권한이 필요한 경우에만 쓴다.
  모델 확신, Worker/Reviewer 합의, 권고 default, timeout, 무응답은 사용자 권한이 아니다.
- "결정할 것이 없었다" 는 CLEAR 로 **단언**한다. 기록의 부재는 CLEAR 가 아니다.

=== REPOSITORY / SECURITY POLICY ===
금지: git push / force push, branch 삭제, release/deployment, production/infra 변경,
destructive DB operation, 외부 network 접근, 외부 package 임의 다운로드,
secret 출력/기록/외부 전송, `git add -A`, 다른 브랜치로 switch, commit (Coordinator 소관).

=== CARRIED-FORWARD ITEMS (앞선 phase 의 검증된 finding — 반드시 반영할 것) ===

[CF-1] (출처: REVIEW_PLAN_iteration2.md N-003, G2/MAJOR/non-blocking, Reviewer 가 직접 재현 및
       수정안 실행 검증 완료). **dependency-absent lane 의 guard 는 반드시 import 기반이어야 한다.**
  문제: `importlib.util.find_spec("langgraph")` guard 와 "langgraph import 를 막는 임시
  MetaPathFinder" blocker 를 함께 쓰면 absent lane 이 skip 이 아니라 **error** 로 끝난다.
    - finder 가 find_spec 에서 raise 하면 → guard 가 module import 시점에 전파 → unittest ERROR
    - finder 가 spec 을 주고 loader 가 raise 하면 → find_spec 은 truthy → guard 가 HAVE=True 로
      오판 → test 실행 중 import 실패 → ERROR
  또한 blocker 가 import 를 막아도 `importlib.metadata.version("langgraph")` 는 여전히
  `"0.2.76"` 을 돌려준다 (metadata 는 import 가 아니라 dist-info 에서 온다).
  **따라서 metadata 버전만으로 absent lane 을 판별할 수 없다.**
  검증된 형태 (present / find_spec-raise / loader-raise 세 경우 모두 올바르게 동작 확인됨):
```python
def _langgraph_ok() -> bool:
    try:
        import langgraph            # noqa: F401
        import langgraph.graph      # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False
```
  IMPLEMENTATION/TEST 는 이 형태(또는 동등하게 import 기반이며 세 경우를 모두 통과하는 형태)를
  사용한다. TEST phase 는 dependency-absent lane 을 실행하기 **전에** 이것이 적용되었는지 확인한다.

[CF-2] (Coordinator 가 직접 실행해 확인한 baseline — 2026-09-02)
  `python3 -m unittest discover -s scripts -p 'test_*.py'`
    -> `Ran 1725 tests in 333.718s` / `OK (skipped=6)` / exit 0
  이것이 이 run 의 회귀 baseline 이다. present lane 의 기대 skip 은 정확히 이 6개이며,
  그 외의 skip / error / failure 는 회귀로 간주한다.
  (참고: 이 명령은 약 5분 30초가 걸린다. 충분한 timeout 을 두고 실행할 것.)

[CF-3] (출처: REVIEW_DESIGN.md N-001, NONE/MINOR/non-blocking)
  `terminal_status` / `terminal_reason` 를 **어느 node 가 쓰는지** 가 DESIGN §2 Nodes 표에
  지정되어 있지 않다. `route` 가 ESCALATE/COMPLETE 를 반환하는 경로와 decision block 에서 유래한
  BLOCK 에는 기록 주체가 없다. **값** 은 DESIGN §6 이 status 별 진입조건과 closed reason code 로
  이미 확정해 두었고 **기록 위치** 만 비어 있다.
  → IMPLEMENTATION 은 `ROUTE` 또는 `TERMINAL` 중 하나에 write 책임을 **명시적으로 확정**하고
    그 선택을 코드와 DESIGN.md 양쪽에 반영한다. 추측으로 넘어가지 않는다.
    (Reviewer 가 지목한 자연스러운 해석: TERMINAL 이 route_token + §6 조건으로 기록)

[CF-4] (출처: REVIEW_DESIGN.md N-002, NONE/MAJOR/non-blocking — 착수 전 확정 권고)
  DESIGN §4 의 `route` strict priority 4번("pending intent PREPARED → EXECUTE path;
  SETTLED → validation/apply path")은 `RouteToken` 9개 어휘로 **표현할 수 없는 분기**이며,
  §8 의 `set(RouteToken) == ROUTE_TARGETS.keys()` 총체성 요구와 충돌한다.
  Reviewer 가 직접 재현한 사실: §7 data flow 의 이 구간은 static edge 이고, checkpoint resume 은
  `ROUTE` 를 거치지 않고 `next == ("EXECUTE",)` 로 바로 복귀한다.
  → 따라서 항목 4 는 route 의 분기가 아니라 구조적 edge 서술이며 문장의 위치가 잘못된 것이다.
  → IMPLEMENTATION 은 둘 중 하나로 **확정**한다: (a) §4 항목 4 를 "구조적 edge 이며 route 의
    반환 분기가 아니다" 로 명시, 또는 (b) RouteToken 에 member 를 추가하고 ROUTE_TARGETS 를 확장.
    (a) 가 Reviewer 의 실측과 일치한다. 어느 쪽이든 §8 coverage linter 가 불일치를 즉시 드러낸다.

[CF-5] (출처: REVIEW_DESIGN.md N-003, NONE/MINOR/non-blocking)
  DESIGN §8 의 lint 규칙 "every cycle contains ROUTE and is budget-guarded by declared
  `phase` or `final` guard metadata" 를 문자 그대로 구현하면, 설계가 명시한 정상 cycle
  `ROUTE → ADVANCE_PHASE → ROUTE` 가 linter 에 걸린다. 이 cycle 은 iteration budget 이 아니라
  `current_phase_index` 의 단조 증가(상한 `len(requested_phases)`)로 종료하기 때문이다.
  → guard metadata 에 `phase_index_monotonic` 같은 세 번째 종류를 추가하거나 해당 cycle 을
    규칙에서 명시적으로 예외 처리한다. 정상 graph 가 자기 linter 에 걸리지 않아야 한다.

[CF-6] (IMPLEMENTATION phase 에서 명시적으로 **미해결로 남긴** non-blocking 항목들.
        TEST 가 반드시 고쳐야 하는 것은 아니다. 다만 알고 있어야 하고, 최종 보고의
        "알려진 제한사항" 에 그대로 실릴 항목들이다. TEST 범위에서 자연스럽게 해소할 수
        있으면 하되, 이것 때문에 TEST 범위를 넓히지는 않는다.)
  N-002  D 계산 중복 — e2e_harness.py 의 pure function 추출이 이행되지 않아
         downstream_revalidation_set 계산이 e2e_harness.py 와 deterministic_workflow/routing.py
         양쪽에 존재한다. OS-40 의 "중복 transition engine 금지" 와 맞닿는 항목이다.
  N-003  SKILL prose 축소 미이행 — graph 로 이전된 routing 을 Coordinator LLM 이 다시 판단하지
         않도록 SKILL.md 를 줄이는 작업이 실질적으로 이루어지지 않았다(9줄 추가만).
  N-004  validator 강도 — (a) core AST scan 이 import 문에서 orca/subprocess 만 보고
         DESIGN §1 이 요구한 terminal/session/credential/claude/codex 및 field name 을 보지 않는다.
         (b) cycle guard 검사가 상수 비교일 뿐 실제 cycle 분석이 아니다.
  N-005  `_langgraph_ok` helper 가 두 test module 에 복제되어 있다.
=== REVIEW POLICY (all phases) ===
반드시 읽는다: orca-worker-reviewer-orchestration/reviews/common.md
              orca-worker-reviewer-orchestration/reviews/<phase>.md

- Worker 의 설명을 사실로 가정하지 않는다. 실제 repository / artifact / diff / test 결과를
  직접 확인한다. 특히 "테스트가 통과한다"는 주장은 **직접 실행해서** 확인한다.
- Reviewer 는 code/artifact 를 직접 수정하지 않는다. REVIEW 파일만 쓴다.
- delta 는 시작점이지 경계가 아니다. 필요하면 repository 어디든 확인한다. 범위 제한 없음.
- approved_baseline 은 immutable truth 가 아니다. 이전 phase 결과와 이번 delta 가 명백히
  모순되면 그것은 넘어갈 사항이 아니라 blocking finding 이다.
- session 이 재사용되어 이전 PASS 를 기억하더라도 그 기억은 증거가 아니다.
  이번 delta 를 처음 보는 것처럼 확인한다.

판단은 profile-first:
  1 explicit user/project requirements  (위 ORIGINAL OBJECTIVE / OS-40 REQUIREMENTS)
  2 project quality attributes          (none — profile absent)
  3 current phase contract
  4 minimal general gate G1-G5
이 범위 밖의 일반적 software-quality concern 을 스스로 blocking finding 으로 승격하지 않는다.
generic best practice, 설계 취향, minor improvement 는 FAIL 의 근거가 아니다.

Severity 와 Blocking 은 다른 축이다.
  Severity  = finding 의 영향도 (CRITICAL|MAJOR|MINOR)
  Blocking  = 이번 gate 를 실패시켜야 하는가 (YES|NO)
`Blocking: YES` 는 blocking quality attribute 위반(none) 또는 G1-G5 위반일 때만 성립한다.
`Quality Attribute: NONE` 인 finding 은 언제나 `Blocking: NO`.
Blocking finding 이 하나라도 있으면 RESULT: FAIL.

Finding 형식:
ID:
Quality Attribute: G1 | G2 | G3 | G4 | G5 | NONE
Severity: CRITICAL | MAJOR | MINOR
Blocking: YES | NO
Location:
Issue:
Reason:
Required Action:

=== RESULT CONTRACT (all reviewers) ===
# Review Result
RESULT: PASS | FAIL
REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED
DECISION_GATE_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT

## Summary
## Blocking Findings
## Non-Blocking Findings
## Test Review
## Final Decision

본문에 정확히 하나의 ```decision-gate fenced JSON record.
Reviewer 는 decision 분류를 검증할 수 있으나 사용자를 대신해 결정할 수 없다.
NEEDS_INPUT/CONFLICT 를 근거 없이 CLEAR/ASSUMPTION_ALLOWED 로 낮추지 않는다.

=== COMPLETION REPORTING ===
dispatch preamble 대로 worker_done 을 보낸다.
--outcome succeeded = 리뷰를 정상 수행함 (RESULT: FAIL 이어도 succeeded 다).
--outcome failed     = 리뷰 자체를 수행하지 못함.
--files-modified 에 작성한 REVIEW 파일 경로, body 에 RESULT / REVIEW_VERDICT /
DECISION_GATE_STATE 와 blocking finding 개수를 적는다.
=== TASK BOUNDARY ===
current_role: reviewer (Final Adversarial Reviewer — fresh session, no inherited context)
current_phase: final_review
artifact_contract:
  WRITE: artifacts/runs/run_0bcf4e7296c9/FINAL_REVIEW.md   (attempt 1)
  READ : artifacts/runs/run_0bcf4e7296c9/{ANALYSIS,PLAN,DESIGN,IMPLEMENTATION,TEST}.md
         artifacts/runs/run_0bcf4e7296c9/REVIEW_*.md
         전체 변경: `git status --short` + `git diff` + untracked 파일 직접 열람
         (아직 commit 이 없으므로 git diff 7bc228a..HEAD 는 비어 있다)
         test/validation 결과: 각 phase 보고서의 인용 + **직접 재실행**

=== ROLE ===
당신은 세 번째 역할이 아니다. reviews/common.md 를 그대로 따르는 Reviewer instance 이며,
이전 판정 context 를 상속하지 않은 새 session 이다.
**앞선 phase gate 가 PASS 였다는 사실을 옳다고 가정하지 않는다.**
이 run 의 모든 phase Reviewer 판정은 재검증 대상이다.

reviews/common.md 를 읽는다. §11 Reviewer delta-context 계약은 여기에 적용되지 않는다 —
Final Adversarial Review 는 자기 checklist 전체를 스스로 수행한다.

=== REVIEW CHECKLIST (blocking finding 을 찾을 탐색 축; 그 자체가 blocking criterion 은 아니다) ===
A objective alignment        원래 요청(OS-40 AC 16개 + 사용자 검증 11항목)이 실제로 충족되었는가
B cross-phase consistency    phase 산출물들이 서로 모순되지 않는가
C contract vs implementation 문서화된 계약과 코드가 일치하는가
D implementation vs tests    test 가 실제 위험을 검증하는가, 통과를 위해 약화되지 않았는가
                             (mutation-sensitivity 주장을 표본 검증하라)
E docs vs behavior           문서가 실제 동작을 설명하는가
F lifecycle state machine    상태 전이와 counter 가 문서와 코드에서 동일한가
G security destructive       파괴적 동작, secret, 범위 밖 파일 변경이 없는가
                             (`git status --short` 로 이 run 과 무관한 파일이 건드려졌는지 확인)
H over-engineering           요청되지 않은 abstraction 이나 범위 확대가 없는가
I hidden coupling            의도치 않은 공유 자산/외부 계약 변경이 없는가
                             (OS-28~OS-30 schema, historical run artifact, 설치본 parity)
J decision provenance        미해결 decision, 승인되지 않은 고영향 가정, decision drift 가 없는가
                             - artifacts/runs/run_0bcf4e7296c9/decision_ledger 에 해결되지 않은
                               NEEDS_INPUT/CONFLICT 가 남아 있는가 (남아 있으면 완료 금지)
                             - 사용자 권한 없이 승인된 고영향 가정이 있는가
                             - 각 경계의 gate 결과와 근거가 run-scoped artifact/log 에 남아 있는가

반드시 직접 실행해 확인할 것 (인용된 결과를 신뢰하지 말 것):
  python3 -m unittest discover -s scripts -p 'test_*.py'   # baseline [CF-2]: Ran 1725, OK (skipped=6)
  새로 추가된 테스트 스위트
  python3 scripts/validate_skills.py
  python3 scripts/verify_package.py
  git diff --check
  git status --short
  langgraph 없이도 기존 테스트가 통과하는지 (해당 설계를 채택했다면)
  core 모듈이 Orca 를 참조하지 않는지 (grep 으로 직접)

Final Reviewer 는 무한한 generic quality checklist 를 생성하지 않는다. project profile 에 없는
일반적 improvement 나 non-blocking finding 만 존재하면 verdict 는 PASS (WITH NOTES) 이며
그것만으로 correction loop 를 시작하지 않는다.

=== FINDING CONTRACT ===
Blocking finding 은 §11 형식에 `Responsible Phase` 한 필드를 추가한다:
ID:
Quality Attribute: G1 | G2 | G3 | G4 | G5 | NONE
Severity: CRITICAL | MAJOR | MINOR
Blocking: YES | NO
Responsible Phase: analysis | plan | design | implementation | test
Location:
Issue:
Reason / Evidence:
Required Action:

Responsible Phase 는 blocking finding 에만 의미가 있다. 하나의 finding 은 정확히 하나의
Responsible Phase 를 가지며, 두 phase 에 걸친 결함은 서로 다른 id 의 두 finding 으로 나눈다.
값은 다음 ladder 의 첫 일치로 정한다:
 1. Location 이 artifacts/runs/run_0bcf4e7296c9/<PHASE>.md 면 → 그 PHASE
 2. 아니면 결함의 성격으로 매핑:
    잘못된 전제 / 요구사항 오독 / 놓친 제약  → analysis
    범위 누락 / 순서 / 계획된 검증의 부재    → plan
    코드는 명세를 따르는데 명세가 틀림       → design
    production code 동작 결함 / 계약 위반    → implementation
    test 부재 / 불충분 / 결함을 못 잡는 test → test
    문서와 실제 동작 불일치                 → 그 동작을 소유한 phase

=== RESULT CONTRACT ===
artifacts/runs/run_0bcf4e7296c9/FINAL_REVIEW.md 에:
# Review Result
RESULT: PASS | FAIL
REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED
DECISION_GATE_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT

## Summary
## Blocking Findings
## Non-Blocking Findings
## Test Review
## Final Decision

본문에 정확히 하나의 ```decision-gate fenced JSON record.
Final Adversarial Reviewer 도 자신이 찾은 결함을 직접 고치지 않는다. code/artifact 를
수정하지 않으며 FINAL_REVIEW.md 만 쓴다.

=== COMPLETION REPORTING ===
dispatch preamble 대로 worker_done. --outcome succeeded (리뷰 수행 성공; RESULT: FAIL 이어도
succeeded), --files-modified 에 artifacts/runs/run_0bcf4e7296c9/FINAL_REVIEW.md,
body 에 RESULT / REVIEW_VERDICT / DECISION_GATE_STATE / blocking finding 개수와
각 finding 의 Responsible Phase.

=== PHASE GATE HISTORY (이 run 의 실제 경과 — 검증 대상이지 신뢰 대상이 아니다) ===
ANALYSIS       PASS at iteration 2  (iter1 FAIL: e2e_harness.py 누락으로 소유권 census 6행 오분류)
PLAN           PASS at iteration 2  (iter1 FAIL: 저장소가 쓰지 않는 pytest runner 전제)
DESIGN         PASS at iteration 1
IMPLEMENTATION PASS at iteration 3  (iter1 FAIL: T4 phase-budget guard 부재로 예산 소진 후에도
                                     COMPLETED / OrcaAdapter 가 없는 method 호출 / 테스트가
                                     M4·M5 mutation 미검출;
                                     iter2 FAIL: parity 테스트가 absent lane 을 error 로 만듦)
TEST           PASS at iteration 2  (iter1 FAIL: _checkpointable forbidden-key guard 를 제거해도
                                     suite 가 통과 — checkpoint 요구사항의 evidence 부재)
FINAL_REVIEW_ITERATIONS = 1 (이번이 첫 attempt)
max-iterations = 5

**이 이력을 근거로 "이미 검증되었다" 고 가정하지 말 것.** 위 PASS 는 phase Reviewer 의 판정이며
이 attempt 는 그것들을 재검증하는 독립 gate 다.

=== 마지막 phase Reviewer 가 남긴 미해결 항목 (Remaining Gaps) ===
아래는 IMPLEMENTATION/TEST 가 **의도적으로 범위 밖으로 남긴** non-blocking 항목이다.
이것들이 실제로 non-blocking 인지, 아니면 OS-40 요구사항 위반으로 blocking 인지 스스로 판정하라.
  N-002  D 계산 중복 — e2e_harness.py 의 pure function 추출 미이행으로
         downstream_revalidation_set 계산이 e2e_harness.py 와 deterministic_workflow/routing.py
         양쪽에 존재. OS-40 의 "LangGraph 와 별도로 동일한 전이 규칙을 수행하는 독자 event loop 나
         병렬 transition engine 을 만들지 않는다" 와 직접 맞닿는다. **특히 주의해서 판정할 것.**
  N-003  SKILL prose 축소 미이행 (SKILL.md 에 9줄 추가만). OS-40 Scope 의 "graph 로 이전된
         routing 을 Coordinator LLM 이 다시 판단하지 않도록 Skill 축소" 항목이다.
  N-004  validator 강도 — core AST scan 이 orca/subprocess 만 보고 DESIGN §1 이 요구한
         terminal/session/credential/claude/codex 및 field name 을 보지 않음.
         cycle guard 검사가 상수 비교일 뿐 실제 cycle 분석이 아님.
  N-005  `_langgraph_ok` helper 가 두 test module 에 복제됨.
  AC 15  (OS-28~30 policy/schema 및 historical artifact 무변경) 증거가 수동 검토 의존.

=== COORDINATOR 가 직접 확인한 사실 (참고용 baseline; 그대로 믿지 말고 재확인해도 좋다) ===
- 회귀 baseline (이 브랜치 작업 시작 전): `Ran 1725 tests / OK (skipped=6)`
- 현재: full suite `Ran 1753 / OK (skipped=6)`, targeted 28, absent lane 28 errors=0 skipped=13
- diff base = 7bc228a (origin/main), 현재 HEAD = 7bc228a (아직 commit 없음 — 모든 변경은
  working tree 에 있다. 따라서 `git diff 7bc228a..HEAD` 가 아니라 `git status --short` 와
  `git diff` / untracked 파일을 함께 보아야 전체 변경을 볼 수 있다.)
- Coordinator 가 독립적으로 재현한 mutation: M5(T2 순서), M4(event dedupe),
  FORBIDDEN_KEYS 제거 — 세 개 모두 현재 suite 가 검출함을 확인했고 전부 원복(해시 확인)했다.