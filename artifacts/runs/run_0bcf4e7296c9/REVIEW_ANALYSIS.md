# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

ANALYSIS.md 의 인용 정확도는 매우 높다. SKILL.md, `decision_gate.py`, `decision_policy.py`,
`run_logging.py`, `clarification_protocol.py`, `workflow_contract.py`, `release_manifest.py`,
`verify_package.py`, `validate_skills.py`, docs 인용을 라인 단위로 직접 열어 확인했고
(§"Evidence Checked"), 확인한 40여 개 인용 중 실제 내용과 어긋나는 것은 없었다.
특히 `SKILL.md:2211`(T2 budget-first guard), `COMPATIBILITY.md:162`(license 미결정),
`decision_gate.py:61-62`(ledger schema v1) 같은 단일 라인 인용까지 정확하다.

§C 의 LangGraph 0.2.76 실측 주장 4건은 내가 같은 인터프리터에서 독립적으로 재현했고
**4건 모두 인용된 예외 메시지까지 정확히 일치**했다. §D 의 dependency/license 주장도
`importlib.metadata` 와 dist-info LICENSE 파일로 재확인했고 전부 일치했다. 이 두 절은
기억이 아니라 실측이며, AC 11 에 대한 판단(“LangGraph compile 은 일반 unreachable 검사를
제공하지 않으므로 자체 graph-lint 가 필요하다”)은 근거가 확인된 올바른 결론이다.

그러나 이 phase 의 **1차 산출물인 “어떤 workflow 규칙이 이미 코드에 있고 어떤 것이 산문에만
있는가” 분류(§A migration matrix)가 사실과 다르다.** `scripts/e2e_harness.py`(2,438 라인)는
phase 순차 실행, phase gate correction loop, 두 iteration domain 과 소진, mandatory test gate,
Final Adversarial Review T0~T5a, downstream set D 계산을 **실행 가능한 결정론적 코드로 이미
소유**하고 있으며 7개 test 파일이 이를 검증한다. ANALYSIS.md 는 이 파일을 단 한 번도 언급하지
않은 채 위 6개 규칙을 모두 “산문만 소유”로 분류했다. dispatch 의 ENVIRONMENT FACTS 가
`scripts/e2e_harness.py`, `scripts/fake_worker.py`, `scripts/fake_reviewer.py` 를 “저장소에 이미
존재하는 결정론적 primitive” 로 명시해 건넸음에도 그렇다.

결과적으로 PLAN 이 이 matrix 를 입력으로 받으면 이미 검증된 순수 함수
(`downstream_revalidation_set` 등)를 재발명하거나, OS-40 이 명시적으로 금지한
“동일 전이 규칙을 수행하는 중복 transition engine” 을 만들게 된다. 이는 G1/G2 위반이므로
blocking 이다. 나머지는 non-blocking note 다.

## Blocking Findings

### F-001

```text
ID: F-001
Quality Attribute: G1, G2
Severity: CRITICAL
Blocking: YES
```

**Location**: `artifacts/runs/run_0bcf4e7296c9/ANALYSIS.md:22-36` (§A migration matrix),
대조 대상 `scripts/e2e_harness.py`

**Issue**: §A 의 “현재 소유 분류” 6개 행이 `산문만 소유` 로 되어 있으나, 해당 규칙은
`scripts/e2e_harness.py` 에 실행 가능한 코드로 이미 구현되어 있다. ANALYSIS.md 전문에
`e2e_harness` 라는 문자열이 0회 등장한다.

| ANALYSIS §A 행 | ANALYSIS 의 분류 | 실제 코드 (직접 확인) |
| --- | --- | --- |
| phase gate 및 PASS 후 다음 phase/final review | 산문만 소유(전체 routing) | `e2e_harness.py:2081-2108` requested phase 순차 loop → 미완료 시 전파, 완료 시 `2109` final review loop 진입 |
| reviewer FAIL correction loop | 산문만 소유(전체 loop) | `e2e_harness.py:1098-1106` `run()` 의 `for iteration in range(1, self.max_iterations + 1)`; correction round 는 `_run_correction_round` (`1857`) |
| phase/final iteration 두 domain 과 exhaustion | 산문만 소유(일반 engine) | `run_workflow` (`1925`) 가 `phase_iterations`(1926) 와 `final_review_iterations`(1932) 를 **둘 다 run 단위 state 로 소유**; 소진 판정 `2259-2261`(final), `2306`, `2400`(phase) |
| mandatory test gate | 산문만 소유(실행 gate) | `parse_unit_test_status` (`365-391`) + 실제 gate 강제 `1320-1343` (`UNIT_TEST_GATED_PHASES`, LOW affirmative evidence, BLOCKED reason 분기) |
| Final Adversarial Review T0~T5a | 산문만 소유(일반 state machine) | 코드에 T 라벨이 그대로 있다: T1 `2249-2251`, T2 `2253-2261`, T3 `2263-2295`, T4 `2296-2300+`, T5a `2382-2392` |
| downstream set D | 산문만 소유 | `downstream_revalidation_set(corrected, requested)` 순수 함수 `497-517`, HIGH-only 호출 `2386-2390` |

**Reason / Evidence**:
- `scripts/e2e_harness.py:1` docstring: `"""Minimal deterministic Worker/Reviewer loop harness for fake-agent E2E tests."""`
- `run_workflow()` 의 state 집합(`1926-1934`)은 OS-40 이 graph state 에 요구하는 것과 거의 일대일이다:
  `phase_iterations`, `final_review_iterations`, `correction_dispatches`, `revalidation_dispatches`,
  `final_review_verdict`, `final_status`, `decision_state`, `decision_reason_code`, `risk`,
  `reviewer_gates_skipped`.
- `e2e_harness.py:2253-2261` 의 T2 guard 에는 주석이 명시적으로 달려 있다:
  `"T2: LAST-ATTEMPT GUARD. This is the FIRST statement on the FAIL edge."`
  이는 ANALYSIS §F1 이 “`SKILL.md:2211` 의 budget-first guard 가 한쪽에서 바뀌면 drift 한다” 고
  경고한 바로 그 규칙이며, 따라서 drift risk 는 ANALYSIS 가 서술한 2자(산문 ↔ 새 router) 가 아니라
  **3자(산문 ↔ `e2e_harness` ↔ 새 graph)** 다. risk 서술 자체가 과소평가되어 있다.
- 이 코드는 죽은 코드가 아니다. `scripts/test_e2e_harness.py`, `test_decision_gate.py`,
  `test_os29_decision_gate.py`, `test_os22_required_tests.py`, `test_clarification_protocol.py`,
  `test_orca_runtime_contract.py`, `test_review_isolation.py` 7개 파일이 import 한다.
- 분류 누락이 “test 범위 코드는 의도적으로 제외” 라는 선언된 scope 때문이 아니다. ANALYSIS 는
  같은 §A/§B 에서 `orca_runtime_harness.py:3373-3974` scenario runners(역시 fake agent 전용
  test 경로)를 근거로 인용한다. 즉 배제 기준이 일관되지 않고 선언되지도 않았다.
- dispatch 의 ENVIRONMENT FACTS 절이 `scripts/e2e_harness.py` 를 “저장소에 이미 존재하는
  결정론적 primitive” 로 이미 지목해 전달했다.

**왜 blocking 인가 (G1/G2)**:
- G1: ORIGINAL OBJECTIVE 와 Jira Scope 가 요구하는 산출물은 “prompt-owned control logic →
  graph-owned control logic 의 migration matrix” 이고, REVIEWER CONTEXT 의 `new_claims (a)`
  가 “어떤 workflow 규칙이 이미 코드에 있고 어떤 것이 산문에만 있는지” 를 이 phase 의 핵심
  주장으로 지정했다. 그 census 가 6개 행에서 틀렸다.
- G2: 이 matrix 를 그대로 입력받은 PLAN 은 두 가지 중 하나로 귀결된다 — (1) `downstream_revalidation_set`
  처럼 이미 검증된 순수 함수를 재발명하거나, (2) 기존 코드 loop 를 남겨둔 채 graph 를 얹어
  OS-40 이 명시적으로 금지한 “LangGraph 와 별도로 동일한 전이 규칙을 수행하는 독자 event loop 나
  병렬 transition engine” 을 만든다. 어느 쪽도 요구사항을 만족하지 못한다.
- reviews/analysis.md 의 FAIL 예 “핵심 문제를 잘못 이해하거나 repository 구조와 불일치”,
  “중요한 영향 범위/제약 누락” 에 직접 해당한다.

**Required Action**:
1. `scripts/e2e_harness.py` 를 §A 의 소유 분류에 3번째 owner 로 반영하고, 위 6개 행을
   `산문만 소유` → `둘 다 소유(중복)` 또는 `이미 코드가 소유` 로 근거와 함께 재분류한다.
2. §F1 의 정책 이중화 risk 를 3자 drift 로 갱신한다.
3. 재분류의 근거는 파일:라인 인용으로 제시한다(기존 인용 품질을 유지할 것).

### F-002

```text
ID: F-002
Quality Attribute: G1, G5
Severity: MAJOR
Blocking: YES
```

**Location**: `ANALYSIS.md:39-52` (§B 결합 지도), `ANALYSIS.md:118-132` (§Assumptions / Unknowns)

**Issue**: F-001 의 결과로, 이 phase 가 반드시 답해야 할 두 가지가 비어 있다.
(a) 이미 코드로 존재하는 workflow loop 를 OS-40 이 **어떻게 처분할 것인가**(추출 / 폐기 /
parity oracle 로 존치)에 대한 판단이 없다. (b) OS-40 의 필수 deliverable 인 fake adapter 의
기존 baseline (`scripts/fake_worker.py`, `scripts/fake_reviewer.py`, `scripts/orca_fake_agent.py`)
이 식별되지 않았다.

**Reason / Evidence**:
- §B “OrcaRuntimeHarness 결합 지도와 분리 경계” 는 `orca_runtime_harness.py` 만 다룬다.
  그러나 OS-40 의 “중복 transition engine 금지” 제약이 실제로 충돌하는 대상은
  `orca_runtime_harness.py`(전체 phase graph 없음, ANALYSIS 자신도 그렇게 서술)가 아니라
  `e2e_harness.py`(전체 phase graph 있음)다. 즉 §B 는 충돌 지점을 다루지 않았다.
- AC 13 은 “fake adapter 와 Orca adapter 가 동일 scenario 에서 동일 logical transition trace
  를 생성” 할 것을 요구하고, §Assumptions 마지막 항목은 “fake 와 Orca parity 의 canonical
  comparison 단위” 를 PLAN 으로 넘긴다. 그런데 비교의 한쪽 끝인 기존 fake agent 구현
  (`fake_worker.py:204-206` 이 `UNIT_TEST_STATUS` 를 출력하고, `fake_reviewer.py:85` 가
  `responsible_phases` 를 소비한다)이 evidence 로 전혀 등장하지 않는다.
- 이는 G5(판단에 필요한 최소 validation evidence 부재)에 해당한다. PLAN 이 “무엇을 재사용하고
  무엇을 대체하는가” 를 결정할 근거가 artifact 안에 없다.

**Required Action**:
1. §B 또는 §A 에 `e2e_harness.py` 의 처분 옵션(추출하여 core routing 으로 승격 / graph 로
   대체 후 폐기 / 변경 없이 parity oracle 로 존치)을 사실과 trade-off 수준에서 제시한다.
   이 phase 에서 **선택을 확정할 필요는 없다** — 선택지와 근거만 있으면 된다(§Assumptions 로
   넘겨도 무방하다).
2. `fake_worker.py` / `fake_reviewer.py` / `orca_fake_agent.py` 를 fake adapter 의 기존
   baseline 으로 식별하고, AC 13 parity 비교의 양 끝단을 명시한다.

## Non-Blocking Findings

### N-001

```text
ID: N-001
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `ANALYSIS.md:20-36` (§A), `ANALYSIS.md:101-103` (§Impact Scope)

**Issue**: workflow 전이를 소유하지는 않지만 OS-40 경계와 맞닿는 모듈 몇 개가 census 에 없다.
`scripts/final_report.py:55-56` 은 terminal 보고 vocabulary(`ITERATIONS_BY_PHASE`,
`FINAL_REVIEW_ITERATIONS`)를 소유하며 이는 graph state 가 만들어내야 할 값이다.
`scripts/skill_policy.py` 의 `load_risk_contract` 는 risk 계약 파싱을 소유하고
`e2e_harness.py:19` 가 이를 import 한다 — §A “risk axis” 행은 `orca_runtime_harness.py` 만 인용한다.
`scripts/review_isolation.py`(3,415 라인)와 `scripts/final_review_eval.py`(1,597 라인)도 미언급이다.

**Reason**: F-001/F-002 와 달리 이들은 workflow 전이 소유자가 아니므로 matrix 의 정확성을
직접 깨뜨리지 않는다. 다만 §Impact Scope 의 “통합 영향” 목록이 불완전해진다.

**Required Action**: F-001 수정 시 census 를 다시 훑으면서 함께 반영하면 충분하다.
독립적으로는 optional.

### N-002

```text
ID: N-002
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `ANALYSIS.md:31` (§A), `ANALYSIS.md:83` (§E)

**Issue**: `(run, phase, iteration, boundary, sequence)` ledger identity 의 근거로
`decision_gate.py:366-436` / `335-436` 을 인용했다. 그 identity 문자열을 실제로 **정의** 하는
곳은 `decision_gate.py:293-300` (`ledger_key`, docstring
`` `run/phase/iteration/boundary#sequence`. An identity for ONE judgement. ``) 로 인용 범위 밖이다.

**Reason**: 인용된 범위(`validate_ledger_record`, `405-409` 의 `sequence` 검증 포함)가 그 identity
필드를 실제로 검증하므로 **주장 자체는 참**이다. 포인터만 한 단계 넓다. G2/G5 아님.

**Required Action**: optional — `293-300` 을 함께 인용하면 더 정확하다.

### N-003

```text
ID: N-003
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `ANALYSIS.md:46` (§B)

**Issue**: `` `cleanup_authority`, `close_allowed` `` 의 위치를 `269-323` 으로 적었다. 실제 정의는
`orca_runtime_harness.py:295` 와 `:312` 이며, `269` 는 무관한 `validate_orca_contract` 의 시작이다.

**Reason**: 범위가 두 함수를 포함하므로 오인용은 아니고, 시작점이 넓을 뿐이다. 나머지 §B 인용
(`794-830`, `1513-1541`, `1543-1721`, `1837-1971`, `634-686`, `2348-2625`, `2840-3050`,
`3221-3294`, `3373-3974`)은 모두 함수 경계와 정확히 일치함을 확인했다.

**Required Action**: optional — `295-323` 으로 좁히면 정확하다.

## Test Review

이 phase 는 production code 를 변경하지 않는 분석 phase 이므로 test 실행 evidence 가 아니라
**인용 정확성** 이 validation 이다(dispatch `validation` 절).

코드 변경 없음 확인:
- `git status --short` — tracked 파일 수정 0건. 출력은 전부 `??` untracked artifact 디렉터리이며
  기존 historical artifact(`artifacts/archive/`, 다른 run 디렉터리, `artifacts/*.md`)는 그대로다.
- `git diff` / `git diff --cached` — 빈 출력.
- 현재 branch `feat/os-40-langgraph-engine`, HEAD `7bc228a` — dispatch 가 지정한 상태와 일치.
- ANALYSIS.md 는 artifact_contract 가 허용한 유일한 WRITE 경로에 있다.

인용 검증 결과 (직접 열어 대조):
- SKILL.md: `877-940`, `940`(정확), `942-1075`, `972-1075`, `1077-1104`, `1102`, `1775-1793`,
  `1795-1824`, `1826-1879`, `1883-1931`, `2063-2248`, `2183-2212`, `2211`(정확), `2199-2241`,
  `2274-2284` — 전부 일치.
- `orca_runtime_harness.py`: `79-231`, `634-686`, `794-830`, `834-1512`, `1513-1541`,
  `1543-1560`, `1667-1693`, `1723-1836`, `1837-1971`, `1972-2149`, `2285-2347`, `2348-2625`,
  `2840-3050`, `3221-3294`, `3373-3974`, `3439-3519` — 함수 경계와 일치(N-003 제외).
- `decision_gate.py` / `decision_policy.py` / `run_logging.py` / `clarification_protocol.py` /
  `workflow_contract.py` / `quality_profile.py` / `agent_profile.py` — 전부 일치(N-002 제외).
- packaging: `release_manifest.py:25-48,76-88,95-133`, `verify_package.py:19-61`,
  `validate_skills.py:1086-1092,2974-3000` — 전부 일치. §D 의
  “installed orchestration Skill 은 명시된 파일만 허용” 은 `release_manifest.py:113-133` 의
  `unexpected Skill package files` 거부로 실제로 확인되며, 이는 OS-40 packaging 에 대한
  정확하고 중요한 지적이다. “source/installed exact parity 는 **현재 logging tool 에 대해**
  강제된다” 는 표현도 정확하다 — `validate_skills.py` 에는 `clarification_protocol.py` 에 대한
  parity validator 가 없음을 grep 으로 확인했고, ANALYSIS 는 그것을 과대 주장하지 않았다.
- docs: `gpt_sol:104-122`, `opus:24-53`, `ROADMAP:303-307`, `COMPATIBILITY:93-100`,
  `COMPATIBILITY:162`, `README:738-748` — 전부 일치.

§C LangGraph 실측 독립 재현 (동일 인터프리터 3.11.8, 4/4 일치):
```text
1 UNKNOWN-TARGET : ValueError: Found edge ending at unknown node `nope`
2 NO-ENTRYPOINT  : ValueError: Graph must have an entrypoint: add at least one edge from START to another node
3 UNREACHABLE    : compiled OK; invoke -> {'x': 1}          (dead node 무시)
4 BAD-ROUTE      : compiled OK; invoke raised KeyError: 'bogus'
```
API 표면도 introspection 으로 재확인: `StateGraph(state_schema=...)`,
`add_conditional_edges(source, path, path_map, then)`, `Command(graph, update, resume, goto)`,
`interrupt(value)`, `compile(checkpointer, *, store, interrupt_before, interrupt_after, ...)`,
`MemorySaver`(= `InMemorySaver`) 전부 존재. **AC 11 에 대한 결론은 실측에 근거한 올바른 판단이다.**

§D dependency/license 독립 재현 (전부 일치):
```text
langgraph 0.2.76  MIT  requires langchain-core(>=0.2.43,<0.4.0, !=0.3.0..!=0.3.22),
                            langgraph-checkpoint(>=2.0.10,<3.0.0), langgraph-sdk(>=0.1.42,<0.2.0)
installed: langchain-core 0.3.80 / langgraph-checkpoint 2.1.1 / langgraph-sdk 0.1.74
LICENSE files: langgraph, langgraph-checkpoint, langgraph-sdk 모두 "MIT License"
ormsgpack 1.10.0 Apache-2.0 OR MIT / orjson 3.11.3 Apache+MIT / requests Apache-2.0 / httpx BSD-3-Clause
langsmith 0.3.45 MIT (langchain-core 의 transitive dep)
```

decision gate 형식 검증: `DECISION_GATE_STATE:` 선언 1개, ```` ```decision-gate ```` fenced record
1개, `state: CLEAR` 로 서로 일치. CLEAR 에 허용된 5개 key 만 사용. 근거를 선언한 CLEAR 이며
(`grounds`), 열린 결정 항목을 §Assumptions 에 나열하고 “모두 reversible architecture choice 이며
사용자 권한을 요구하지 않는다” 고 단언한 것은 진입 조건을 만족한다. 나열된 9개 항목을 검토했고
되돌릴 수 없거나 monetary/security/privacy/compliance/lock-in 이 참인 항목은 없다.
**decision 분류 오분류 없음.**

## Evidence Checked

- `git status --short`, `git diff`, `git diff --cached`, `git log --oneline -5`, `git rev-parse --abbrev-ref HEAD`
- `orca-worker-reviewer-orchestration/reviews/common.md`, `reviews/analysis.md`
- `artifacts/runs/run_0bcf4e7296c9/ANALYSIS.md` 전문
- 인용된 18개 파일 전부의 해당 라인 범위 (위 Test Review 참조)
- `scripts/e2e_harness.py` 전수 구조 확인: module docstring, top-level def/class 목록,
  `E2EHarness` 메서드 목록, `run_workflow` (`1925-2438`), `run` (`1098`),
  `downstream_revalidation_set` (`497`), `parse_unit_test_status` (`365`), test gate (`1320-1343`),
  T1~T5a (`2249-2412`)
- `scripts/*.py` 비-test 모듈 23개에 대한 ANALYSIS.md 언급 여부 전수 대조
- `grep -rn "UNIT_TEST_STATUS|PHASE_ITERATIONS|FINAL_REVIEW_ITERATIONS|max_iterations|downstream|responsible_phase" scripts/*.py`
  (test 파일 제외) — 코드 소유 여부 교차 확인
- Python 3.11.8 인터프리터에서 LangGraph AC 11 시나리오 4건 직접 실행
- `importlib.metadata` 로 dependency requirement / license / classifier 조회,
  site-packages dist-info 의 LICENSE 파일 직접 read

## Final Decision

이 phase gate 판정은 **FAIL** 이다 — blocking finding 2건(F-001 CRITICAL, F-002 MAJOR).

이 FAIL 은 분석의 품질이 낮아서가 아니다. 인용 정확도, LangGraph/dependency 실측, OS-28~30
불변 계약 목록, packaging 영향 분석은 모두 이 phase 가 요구하는 수준을 충족하거나 상회하며,
사실과 가정도 §Assumptions 로 명확히 분리되어 있다(리뷰 질문 6번 충족). 범위 확대나 조기
설계 확정도 발견되지 않았다 — §F2 가 오히려 “너무 이른 경직화” 를 스스로 risk 로 경계하고
있고, 아키텍처 선택은 전부 PLAN/DESIGN 으로 넘겨져 있다(리뷰 질문 7번 충족).

FAIL 의 이유는 단 하나다: 이 phase 의 핵심 주장인 **소유권 census 가 저장소에 실재하는
2,438 라인짜리 결정론적 workflow loop 를 빠뜨렸고**, 그 결과 6개 규칙의 분류가 뒤집힌다.
OS-40 의 명시적 제약이 “중복 transition engine 을 만들지 않는다” 이므로, 이미 존재하는
transition engine 을 보지 못한 분석 위에서 PLAN 을 시작하는 것은 안전하지 않다.

수정 범위는 좁다. 새 조사가 아니라 이미 지목되어 있던 파일 하나를 census 에 넣고 6개 행을
재분류한 뒤, 그 처분 옵션과 fake adapter baseline 을 적으면 된다. §C·§D·§E 는 손댈 필요가 없다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This review's verdict follows from explicit OS-40 requirements and the ANALYSIS phase contract applied to directly verified repository evidence; the two blocking findings are factual gaps I confirmed by reading the cited files and scripts/e2e_harness.py, so no item at this boundary needs user authority to resolve.",
  "scope": "This phase's own conduct at this iteration."
}
```
