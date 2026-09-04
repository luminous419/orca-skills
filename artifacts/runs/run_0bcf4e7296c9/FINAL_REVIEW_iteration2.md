# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

Run: run_0bcf4e7296c9 | Phase: final_review | Attempt: 2 | Reviewer: fresh session, no inherited verdict context
Diff base: 7bc228a (no commits on branch; all changes are in the working tree)

## Summary

OS-40 의 산출물을 독립적으로 재검증했다. 인용된 수치는 신뢰하지 않고 full suite, targeted suite,
dependency-absent lane, skill validation, package verification, graph-doc validator, `git diff --check`,
core purity grep 을 전부 직접 재실행했으며 모두 재현되었다. 그 위에 **48건의 mutation 을 직접 주입**해
테스트의 실제 검출력을 표본이 아니라 계통적으로 측정했고(34/48 검출), 이어서 **57,000건의 random scenario
와 1,650건의 directed role-aware scenario** 로 compiled graph 를 fuzz 했다.

그 결과 **이전 attempt 와 다섯 번의 phase gate 가 모두 놓친 실제 production 결함 1건**을 발견했다.
final review budget 이 소진되는 시점에 correction round 가 이미 한 번 이상 완료되어 있으면
`terminal_node` 가 소비된 `correction_queue` 를 범위 밖 index 로 읽어 **uncaught `IndexError` 로 죽는다.**
계약된 `ESCALATED` terminal state 가 생성되지 않으며, checkpointer 를 붙이면 checkpoint 가
`next == ('TERMINAL',)` / `terminal_status: None` 상태로 고착되어 **재개할 때마다 같은 지점에서 다시 죽는다.**
이는 AC 3 의 정면 위반이자 G2(결과가 동작하지 않음)다. 그래서 이번 gate 는 FAIL 이다.

그 한 건을 제외하면 엔진의 품질은 높다. AC 1,2,4,5,6,7,8,9,11,12,13,14,15,16 은 내가 직접 재현한 증거로
충족을 확인했다. 회귀는 없다(1725 → 1759, skipped 는 6 그대로). core 는 Orca/subprocess/handle/credential 을
전혀 참조하지 않고, source/installed 두 사본은 11개 파일 전부 byte-identical 이며, decision ledger 30개
boundary 는 전부 CLEAR 로 미해결 NEEDS_INPUT/CONFLICT 가 없다. 범위 밖 파일 변경도 없다
(tracked 변경은 7파일 83 insertions / 0 deletions, historical artifact 는 mtime 기준으로도 무변경).

CF-7 을 포함한 carried-forward 미해결 5건은 각각 개별 판정했고 **전부 non-blocking** 으로 결론냈다.
근거는 각 항목에 적었다. 즉 이번 FAIL 은 CF-7 때문이 아니라 오직 F-001 때문이다.

## Blocking Findings

### F-001

ID: F-001
Quality Attribute: G2
Severity: CRITICAL
Blocking: YES
Responsible Phase: implementation
Location: `scripts/deterministic_workflow/executor.py:155` (동일 패턴이 `:167` 에도 있음)

Issue:
`terminal_node` 의 ESCALATE 분기가 `correction_queue` / `correction_index` 를 bounds check 없이 읽는다.

```python
responsible_exhausted = (new["round_kind"] == "FINAL_REVIEW"
                         and bool(new["correction_queue"])
                         and new["remaining_phase_budget"][new["correction_queue"][new["correction_index"]]] <= 0)
```

`bool(correction_queue)` 는 queue 가 비어 있지 않다는 것만 보장할 뿐 `correction_index` 가 유효 범위에
있다는 것은 보장하지 않는다. correction round 를 모두 소비하고 나면 `advance_phase_node` 가
`correction_index` 를 `len(correction_queue)` 까지 올려놓는데(executor.py:129), `correction_queue` 자체는
그대로 남는다. 그 상태에서 마지막 final review 가 FAIL 하면 `apply_result_node` 의 queue 재설정 조건
(`final_review_iterations < max_iterations`, executor.py:114)이 거짓이라 **queue 도 index 도 정리되지 않은 채**
`route` 의 T2 guard 가 ESCALATE 를 반환하고, `terminal_node` 가 `correction_queue[len(correction_queue)]` 를
읽어 `IndexError` 를 던진다.

Reason / Evidence:
Reviewer 가 직접 재현했다. `phases=("ANALYSIS",)`, `risk=high`, script
`[W, R(PASS), R_final(FAIL,F), W, R(PASS), R_final(FAIL,F)]` (F 의 responsible_phase = ANALYSIS):

```
Traceback (most recent call last):
  ...
  File "scripts/deterministic_workflow/executor.py", line 155, in terminal_node
    and new["remaining_phase_budget"][new["correction_queue"][new["correction_index"]]] <= 0)
IndexError: list index out of range
During task with name 'TERMINAL'
```

- **max_iterations = 2, 3, 4, 5 전부에서 재현된다.** 이 run 의 실제 설정값은 5다.
- downstream revalidation 경로(`phases=("ANALYSIS","PLAN")`, 교정 후 PLAN 재검증까지 통과한 뒤 두 번째
  final review FAIL)에서도 동일하게 재현된다.
- role-aware directed sweep 825 scenario 중 4건에서 발생했다. 나머지 57,000건의 random scenario 와
  825건의 PASS-padding directed sweep 에서는 이 외의 crash class 가 하나도 나오지 않았다 —
  즉 우연한 edge case 가 아니라 **유일하게 특정된 결함**이다.
- `MemorySaver` 를 붙이면 복구 불가능하다. checkpoint 가 `next=('TERMINAL',)`, `terminal_status=None`,
  `route_token='ESCALATE'` 로 고착되고 resume 3회 모두 같은 `IndexError` 로 죽었다.

위반한 계약:
- **AC 3** "iteration budget 소진 시 추가 dispatch 없이 ESCALATED 또는 계약된 terminal state 가 생성된다."
  → terminal state 가 생성되지 않고 예외가 전파된다. (추가 dispatch 가 없다는 절반만 지켜진다.)
- **AC 8** "같은 thread_id 의 checkpoint 를 복구하면 동일 next node 가 선택된다."
  → 동일 next node 가 선택되기는 하나 그 node 가 항상 죽어 resume 자체가 불가능하다.
- **AC 10 / DESIGN §6** terminal state 는 absorbing 이고 `FINAL_REVIEW_MAX_ITERATIONS_REACHED` 또는
  `MAX_ITERATIONS_REACHED` 를 내야 한다. 그 경로가 도달 불가능하다.
- G2: OS-40 이 목표로 한 "예산 소진 시 결정론적 종료" 라는 핵심 기능이 이 조건에서 동작하지 않는다.

이것이 왜 지금까지 통과했는가: 기존 34개 test 중 이 근처를 다루는 두 건
(`test_final_fail_exhausted_responsible_phase_escalates_before_dispatch`,
`test_final_budget_guard_precedes_responsible_phase_mapping`)은 **correction round 가 완료되기 전에**
ESCALATE 하는 경로만 검증한다. 그 경로에서는 `correction_index == 0` 이라 index 가 항상 유효하다.
"correction 을 끝까지 소비한 뒤 final budget 이 소진되는" 조합은 어떤 test 도 실행하지 않는다.

Required Action:
1. `terminal_node` 의 ESCALATE 분기가 `correction_index` 유효성을 확인하도록 고친다
   (`:155` 의 `responsible_exhausted` 계산과 `:167` 의 `reason_phase` 선택 양쪽). queue 소비 완료 상태에서
   ESCALATE 가 들어오면 어떤 `phase` 와 어떤 reason code 를 기록할지 DESIGN §6 의 closed vocabulary 안에서
   명시적으로 확정하고, 그 선택을 DESIGN.md §6 에 반영한다. (round_kind 가 FINAL_REVIEW 이고 responsible
   phase 가 이미 전부 교정된 상태이므로 `FINAL_REVIEW_MAX_ITERATIONS_REACHED` 가 §6 과 일관된다 —
   다만 이 판단은 Worker 가 확정할 몫이다.)
2. 동일 결함이 `route`(routing.py:72), `prepare_intent_node`(executor.py:52-53)에도 같은 패턴으로
   존재하는지 확인한다. Reviewer 가 확인한 바로는 두 곳은 현재 도달 가능한 경로에서 index 가 항상
   유효하나, 세 곳이 같은 불변식에 암묵적으로 의존하고 있으므로 그 불변식을 한 곳에서 보장하는 편이
   재발을 막는다.
3. **회귀 테스트를 반드시 함께 추가한다.** 최소 두 시나리오:
   (a) correction round 완료 후 final budget 소진 → `ESCALATED` + 계약된 reason code, 예외 없음;
   (b) downstream revalidation 완료 후 동일 조건 → `ESCALATED`, 예외 없음.
   두 테스트가 수정 전 코드에서 실제로 실패하는지 확인해 mutation-sensitivity 를 증명한다.
4. 범위는 위 3항으로 한정한다. 아래 Non-Blocking Findings 는 이번 correction 의 요구사항이 아니다.

## Non-Blocking Findings

아래는 전부 `Quality Attribute: NONE` 이며 따라서 `Blocking: NO` 다. 이번 gate 를 실패시키지 않는다.
CF-6/CF-7 로 이월된 항목은 이번 attempt 가 독립적으로 재판정한 결과를 함께 적었다.

### N-001 (CF-7 / N-009 / N-012 재판정) — mandatory unit-test gate 의 coverage 공백
Quality Attribute: NONE | Severity: MAJOR | Blocking: NO
Location: `scripts/deterministic_workflow/routing.py:37-38`

Reviewer 가 직접 재현했다. 해당 두 줄을 제거해도 34개 test 가 전부 통과한다(실패 0건). CF-7 이 요구한
독립 판정 결과는 **non-blocking** 이며, 근거는 세 가지다.

1. 이 guard 는 OS-40 의 acceptance criterion 16개 중 어디에도, 사용자 검증 11항목 중 어디에도 없다.
   Tier 1 explicit requirement 가 아니다. 반면 attempt 1 의 F-002 가 다룬 4건은 AC 7/AC 10 에 직접
   대응했다(그래서 blocking 이었다).
2. **현재 blast radius 가 0이다.** `tools/run_workflow.py` 는 dependency 버전 확인 18줄이고 어떤
   coordinator 도 `build_graph` 를 호출하지 않는다. 실제 운영되는 §14 gate 는 여전히 SKILL.md prose 쪽이며
   그쪽은 `validate_skills.py` 의 727 checks 로 계속 검증된다. 그래프 쪽 guard 가 조용히 퇴화해도 오늘
   production 에 미치는 영향이 없다.
3. 허위 주장이 없다. TEST.md:165 가 이 공백을 알려진 non-blocking follow-up 으로 정직하게 명시했다.
   G5 는 "판단에 필요한 최소 evidence 의 부재"인데, 이 항목이 없어도 OS-40 AC 충족 여부를 판정할 수 있다.

Required Action: 후속 이슈로 남긴다(우선순위 1위). OS-37/OS-31 에서 graph 가 실제 실행 경로가 되기 전에
반드시 채워야 한다 — 그 시점에는 blast radius 가 0이 아니게 되므로 판정도 달라진다.

### N-002 — blocking flag filter 미검증
Quality Attribute: NONE | Severity: MAJOR | Blocking: NO
Location: `scripts/deterministic_workflow/routing.py:27`

`responsible_phases` 의 `if finding.get("blocking") is not True: continue` 를 제거해도 34개 test 가 전부
통과한다(Reviewer 직접 재현). 모든 test fixture 가 `blocking: True` 인 finding 만 쓰기 때문이다.
"blocking finding 만 correction 을 유발한다"(AC 2 의 실질 계약)가 회귀로부터 보호되지 않는다.
Required Action: `blocking: False` finding 이 correction_queue 를 만들지 않음을 확인하는 test 1건. 후속.

### N-003 — `validate_event` 의 두 분기 미검증
Quality Attribute: NONE | Severity: MAJOR | Blocking: NO
Location: `scripts/deterministic_workflow/contracts.py:83-84`, `:91-92`

(a) closed field-set 검사(`set(event) != required` → `MALFORMED_EVENT`)를 제거해도 34개 test 가 전부
통과한다. `MALFORMED_EVENT` reason code 를 실제로 발생시키는 test 가 하나도 없다
(`MALFORMED_EVENT` 문자열: core 2회, test 0회).
(b) worker status 어휘 검사를 제거해도 전부 통과한다. 다만 이 경우 `phase_gate` 가 뒤에서 BLOCK 하므로
terminal status 자체는 동일하고 reason code 만 달라진다(영향 작음).

AC 10 전체는 충족된다 — unknown event(5건 검출), out-of-order binding, post-terminal, malformed state 는
모두 mutation 으로 검출됨을 확인했다. 미검증인 것은 malformed-event 하위 분기 하나뿐이라 evidence 의
"부재"가 아니라 "불완전"이다. 그래서 non-blocking 으로 둔다.
Required Action: 필드 하나를 누락/추가한 event 가 `MALFORMED_EVENT` 로 종료됨을 확인하는 test 1건. 후속.

### N-004 — graph_spec 의 두 정적 검사가 서로를 가려준다
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
Location: `scripts/deterministic_workflow/graph_spec.py:49`, `:57`

`test_unreachable_and_route_coverage_mutations_are_rejected` 가 쓰는 fixture(고립된 `DEAD` node)는
forward-reachability 위반이자 동시에 dead-end 위반이다. 따라서 두 검사 중 **어느 쪽을 제거해도** test 가
통과한다(둘 다 Reviewer 가 직접 재현). AC 11 의 "unreachable path 탐지" 자체는 만족한다(spec 은 거부된다).
정밀도만 부족하다.
Required Action: 한쪽만 위반하는 fixture 를 쓴다. 예: `("X","ROUTE")` edge 를 가진 node X 는 TERMINAL 에
도달하므로 dead-end 가 아니지만 VALIDATE 로부터 도달 불가능하다. 후속.

### N-005 — `validate_node` 의 malformed-state 차단이 graph 수준에서 미검증
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
Location: `scripts/deterministic_workflow/executor.py:25-27`

해당 try/except 블록을 통째로 제거해도 34개 test 가 전부 통과한다. malformed state 의 fail-closed 는
`validate_state` pure function 수준에서는 충분히 검증되어 있으나(4종 mutation 모두 검출), 그것이 graph
진입점에 실제로 연결되어 있다는 증거는 없다. 후속.

### N-006 — CF-3 의 "TERMINAL 이 terminal field 의 유일한 writer" 주장이 부정확
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
Location: DESIGN.md:248, IMPLEMENTATION.md:132 vs `executor.py:27,30,85,117`

DESIGN §2 node 표와 IMPLEMENTATION.md 가 "TERMINAL 은 `terminal_status`/`terminal_reason` 의 유일한
writer"라고 적었지만, 실제로는 `validate_node`, `validate_settlement_node`, `apply_result_node` 도
`terminal_reason` 을 쓴다. 코드 패턴 자체는 합리적이다 — 중간 node 가 reason 후보를 staging 하고
TERMINAL 이 `(state.get("terminal_reason") or {}).get("code") or ...` 로 그것을 읽어 최종화한다.
`terminal_status` 에 한해서는 주장이 참이다.

부수적으로 실제 손실이 하나 있다: `validate_node` 가 기록한 `missing_capabilities` 필드를 `terminal_node`
가 `{code, message, phase}` 로 덮어써 버린다. DESIGN §6 의 `TerminalReason` 은
`{code, message, phase, finding_ids, missing_capabilities}` 를 규정하는데 뒤 두 필드는 구현되지 않았다.
AC 12(capability 부족 시 명시적 차단)는 `ADAPTER_CAPABILITY_MISSING` code 로 충족되므로 영향은 없다.
Required Action: 문구를 "TERMINAL 은 `terminal_status` 의 유일한 writer이며 `terminal_reason` 은 중간
node 가 staging 하고 TERMINAL 이 최종화한다"로 정정하거나, `TerminalReason` 을 §6 대로 구현한다. 후속.

### N-007 — DESIGN §6 의 closed vocabulary 중 미구현 code
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
Location: DESIGN.md:319 vs `scripts/deterministic_workflow/`

DESIGN §6 이 선언한 reason code 중 `DECISION_GATE_INVALID`, `PENDING_CLARIFICATION`,
`ARTIFACT_BINDING_MISMATCH`, `REPOSITORY_BINDING_MISMATCH`, `MALFORMED_FINAL_REVIEW_OUTPUT` 은 core 에
등장 횟수가 0이다. 또한 DESIGN §4 항목 12 는 "malformed final review output → BLOCK" 이라고 하지만
구현은 `responsible_phases` 의 `ValueError` 를 통해 `OUT_OF_SCOPE_FINAL_REVIEW_FINDING` 으로 **ESCALATE**
한다. 두 경우 모두 fail-closed 이고 추가 dispatch 도 없으므로 동작상의 위험은 없다. 문서가 구현보다 넓다.
Required Action: DESIGN §6 을 구현된 부분집합으로 좁히거나 미구현 code 를 명시적으로 deferred 표시. 후속.

### N-008 — `unit_test_status` 처리와 DESIGN §4 항목 6 의 미세한 불일치
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
Location: `routing.py:37` vs DESIGN.md:293

DESIGN 은 "Worker BLOCKED or mandatory unit-test evidence **absent/BLOCKED** → BLOCK" 이라고 쓰지만
구현은 `unit_test_status != "PASS"` 로 판정하므로 IMPLEMENTATION phase 에서 `NOT_APPLICABLE` 도 BLOCK 된다.
더 엄격한 쪽이므로 안전 방향이다. 또한 그 경우 terminal code 가 `UNIT_TEST_BLOCKED` 가 아니라
`WORKER_BLOCKED` 로 기록된다(`executor.py:163` 은 `== "BLOCKED"` 만 본다). 후속.

### N-009 (CF-6 N-003 재판정) — SKILL prose 축소 미이행
Quality Attribute: NONE | Severity: MAJOR | Blocking: NO
Location: `orca-worker-reviewer-orchestration/SKILL.md` (+9 lines, -0)

OS-40 Scope 의 "graph 로 이전된 routing 을 Coordinator LLM 이 다시 판단하지 않도록 Skill 축소" 는
문자 그대로는 이행되지 않았다. 독립 판정 결과는 **non-blocking** 이다.

- 이 항목은 Scope 에 있고 Acceptance Criteria 에는 없다. AC 쪽 대응 항목인 AC 14(graph contract 와 Skill
  documentation 의 parity 자동 검증)는 `validate_workflow_graph_docs.py` 로 충족되었고 Reviewer 가 직접
  실행해 PASS 를 확인했다.
- 추가된 9줄은 Scope 가 명시한 **목적**을 직접 진술한다: "The deterministic engine owns phase transitions,
  retries, iteration budgets, downstream revalidation, and terminal routing. Coordinators execute the action
  selected by the graph and do not independently choose a next phase or retry."
- 결정적으로, 지금 prose 를 삭제하면 **실제로 동작 중인 유일한 경로가 사라진다.** graph 는 아직 어떤
  coordinator 에도 연결되어 있지 않다(`run_workflow.py` 는 버전 확인 stub). 삭제는 G3(심각한 regression)를
  만든다. 축소는 graph 가 실행 경로가 되는 시점(OS-37)에 해야 하는 작업이다.

Required Action: OS-37 에서 수행. 후속.

### N-010 (CF-6 N-002 재판정) — D 계산 중복
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
Location: `scripts/e2e_harness.py:497` vs `scripts/deterministic_workflow/routing.py:14`

가장 주의해서 판정하라고 지목된 항목이다. 독립 판정 결과는 **non-blocking** 이며 근거는 다음과 같다.

- `scripts/e2e_harness.py` 는 **이 브랜치가 만든 것이 아니다.** main 에 이미 존재했고 이번 run 에서
  단 한 줄도 수정되지 않았다(`git status --short` 로 확인 — modified 목록에 없다).
  OS-40 의 제약은 "만들지 않는다"이지 "기존 것을 제거한다"가 아니며, "기존 Orca adapter 제거"는
  Out of Scope 에 명시되어 있다.
- e2e_harness 는 production workflow engine 이 아니라 prompt-owned workflow 를 시뮬레이션하는 **테스트
  하네스**다. 새 graph 와 병렬로 실행되는 event loop 가 아니다.
- migration matrix 가 이 중복을 은폐하지 않고 "test-only parity oracle for one compatibility release"
  로 명시적으로 기록해 두었다.

다만 그 matrix 표현에는 실현되지 않은 부분이 있다: `e2e_harness.run_workflow` 를 실제 parity oracle 로
사용하는 test 는 존재하지 않는다. AC 13 이 요구하는 parity(fake adapter ↔ Orca adapter)는 별도로
충족되어 있으므로 요구사항 위반은 아니고, 문서 표현이 앞서간 것이다.
Required Action: matrix 문구를 현재 상태에 맞게 정정하거나 실제 oracle test 를 추가. 후속.

### N-011 (CF-6 N-004 / N-005 재판정) — validator 강도와 helper 중복
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
Location: `scripts/test_deterministic_workflow_adapters.py:52-58`, `:20-29` / `test_..._graph.py:7-14`

(a) core AST scan 이 import 문의 `orca`/`subprocess` 만 본다. DESIGN §1 이 요구한
terminal/session/credential/claude/codex 및 field name 은 보지 않는다. 다만 Reviewer 가 core 11개 파일 전체에
대해 직접 grep 한 결과 실제 위반은 0건이었다(유일한 hit 는 `fake_adapter.py` docstring 의 "Orca-independent"
와 `state.py` 의 `FORBIDDEN_KEYS` 정규식 자체). 즉 현재 상태는 깨끗하고 validator 만 약하다.
(b) cycle guard 검사(`graph_spec.py:58`)는 실제 cycle 분석이 아니라 상수 집합 비교다.
(c) `_langgraph_ok` 가 두 test module 에 복제되어 있다. 다만 CF-1 이 요구한 import 기반 형태를 정확히
따르고 있고, `LangGraphGuardTests` 가 그 형태를 고정한다. absent lane 을 직접 실행해 errors=0 을 확인했다.
Required Action: 후속.

### N-012 (CF-7 의 N-011 재판정) — TEST.md 의 mutation 실패 개수 과소 기재
Quality Attribute: NONE | Severity: MINOR | Blocking: NO
TEST.md 가 인용한 mutation 실패 개수가 실측보다 작다. 정성적 주장("검출된다")은 참이며 Reviewer 가
독립적으로 재확인했다. 후속.

### N-013 — CI 에서 langgraph 34개 test 중 18개가 항상 skip 된다
Quality Attribute: NONE | Severity: MAJOR | Blocking: NO
Location: `.github/workflows/ci.yml`

어떤 workflow step 도 `requirements-langgraph.txt` 를 설치하지 않는다(`grep "pip install\|requirements"
.github/workflows/*.yml` → 0 hit). 따라서 CI 에서는 `_langgraph_ok()` 가 False 가 되어 compiled graph 를
실제로 실행하는 18개 test 가 전부 skip 되고, **CI 는 엔진의 graph 수준 동작을 한 번도 검증하지 않는다.**
pure function 계층(16개 test)만 CI 에서 실행된다.

AC 16 이 요구하는 "full unit tests 통과" 는 langgraph 가 설치된 환경에서 충족된다(Reviewer 가 직접 실행:
1759 / OK / skipped=6). 그리고 langgraph 를 필수 의존성으로 만들지 않는 것은 OS-40 의 명시적 제약이므로
CI 가 설치하지 않는 것 자체는 위반이 아니다. 그래서 non-blocking 이다. 다만 F-001 같은 결함이 CI 를
그냥 통과하는 이유가 정확히 이것이라는 점은 기록해 둔다.
Required Action: langgraph 를 설치하는 optional CI job(또는 matrix leg) 추가를 후속으로 검토. 후속.

## Test Review

### 직접 재실행한 결과 (인용 아님)

| 명령 | 결과 | 판정 |
| --- | --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1759 tests / OK (skipped=6)** / exit 0 / 327.8s | baseline 1725 / OK(skipped=6) 대비 +34, skip 동일 → **회귀 없음** |
| targeted 3 module | Ran 34 / OK / 0.15s | 재현됨 |
| dependency-absent lane (meta_path blocker) | Ran 34 / **OK (skipped=18) / errors=0** | CF-1 형태가 정확히 적용됨 |
| `python3 scripts/validate_skills.py` | PASSED (727 checks) | 재현됨 |
| `python3 scripts/verify_package.py` | PASSED (226 source files) | 재현됨 |
| `python3 scripts/validate_workflow_graph_docs.py` | PASSED | 재현됨 (AC 14) |
| `git diff --check` | 무출력 / exit 0 | 재현됨 |
| source ↔ installed 11 파일 byte diff | 전부 동일 | parity 재현됨 |
| core purity grep (orca/subprocess/terminal/session/credential/claude/codex/pty) | 실질 hit 0 | 재현됨 |

CF-1 guard 는 `import langgraph` + `import langgraph.graph` 후 `importlib.metadata.version` 을 확인하는
검증된 형태 그대로다. 실제 `sys.meta_path` blocker 로 import 를 막아 absent lane 을 돌렸고 skip 18 / error 0
을 확인했다 — metadata 만 보는 잘못된 형태였다면 여기서 error 가 났을 것이다.

### Mutation sensitivity — Reviewer 가 직접 주입한 48건

TEST.md 의 mutation 주장을 표본 검증하는 대신 계통적으로 측정했다. **34/48 검출 (71%).**

검출된 34건(요약): 예산 guard 2종, 결정 gate, capability gate, catch-all fail-open, reviewer verdict
어휘(5건 실패), 상태 closed-field / budget 일관성 / 중복 identity / FORBIDDEN_KEYS / post-terminal,
event dedupe(2건), settlement binding, processed-command replay, intent/command id 결정성(uuid 주입 시
각각 2·3건 실패), iteration 회계 3종, correction 대상 phase 전환, downstream 계산 2종, risk gate,
phase_passes 기록(4건 실패), terminal reason code, adapter idempotency(2건), route coverage totality,
terminal back-edge, ADVANCE_PHASE 미교체 확인 등.

살아남은 14건은 전부 위 Non-Blocking Findings(N-001 ~ N-005)에 귀속되며, 그 중 실제로 의미 있는 것은
5건이다. 나머지는 이미 다른 층이 동일 보호를 제공하는 중복 guard(`phase_gate` 의 decision 검사,
`route` 의 terminal 단락, final PENDING budget guard, `PREPARE_FINAL_REVIEWER` 의 result reset,
`EXECUTE_INTENT` 의 intent_status precondition)여서 제거해도 관측 가능한 전이가 바뀌지 않는다.
이는 결함이 아니라 defense-in-depth 다.

**두 층의 독립성 재확인:** `validate_event` 제거 시 graph module 의 settlement 주입 test 5건만,
`phase_gate` closed-vocab guard 제거 시 contracts module 의 direct-state test 5건만 실패해 두 실패 집합이
서로소라는 이전 라운드의 주장을 재현했다.

### Scenario fuzzing — Reviewer 가 추가로 수행

기존 test 가 다루지 않는 조합을 찾기 위해 compiled graph 를 두 방식으로 fuzz 했다.

- random script fuzz: 57,000 scenario (phases 1~3, max_iterations 1~5). crash 0건.
- directed role-aware sweep: 1,650 scenario (역할별로 유효한 결과를 생성하는 adapter, decision vector 전수).
  PASS-padding 825건 crash 0건, FAIL-padding 825건 중 **4건이 F-001 의 `IndexError`**.

즉 F-001 은 이 엔진에서 발견된 **유일한 crash class** 이며, 동시에 기존 34개 test 가 구조적으로 도달하지
못하는 조합("correction 을 끝까지 소비한 뒤 final budget 소진")에 정확히 위치한다.

### 테스트가 통과를 위해 약화되었는가

아니다. 34개 test 중 tautological 하거나 assertion 이 비어 있는 것은 없다. 대부분이
`adapter.effect_count`, `phase_iterations`, `phase_passes`, `terminal_reason["code"]` 처럼 관측 가능한
부작용과 상태를 함께 고정한다. 다만 N-004 처럼 fixture 가 두 검사를 동시에 위반해 어느 쪽을 고정하는지
불분명한 사례가 1건 있다.

## Evidence Checked

- OS-40 Jira 요구사항 전문(AC 1-16, Scope, Out of Scope, Implementation Order), 사용자 원문 요청 11개 검증 항목
- `artifacts/runs/run_0bcf4e7296c9/` 의 ANALYSIS / PLAN / DESIGN / IMPLEMENTATION / TEST 와 REVIEW_* 11개
- `git status --short`, `git diff`, `git diff --stat`(tracked 7파일 / **83 insertions / 0 deletions**),
  `git diff --check`
- 신규 core 11개 모듈 전문(817 LOC), 신규 test 3개 모듈 전문(430 LOC), `validate_workflow_graph_docs.py`,
  `tools/run_workflow.py`, `requirements-langgraph.txt`, 신규 docs 3종
- `scripts/validate_skills.py` / `scripts/release_manifest.py` diff, `scripts/e2e_harness.py`(중복 판정용)
- `.github/workflows/ci.yml`
- decision ledger 30개 record: **전부 `"state": "CLEAR"`, 미해결 NEEDS_INPUT/CONFLICT 0건.**
  B2/B3 경계가 phase·iteration 별로 빠짐없이 기록되어 있다(DESIGN 1 ~ TEST 4, FINAL_REVIEW 1).
- AC 15 확인: OS-28~30 모듈(`decision_policy.py`, `decision_gate.py`, `clarification_protocol.py`,
  `workflow_contract.py`, `e2e_harness.py`, `orca_runtime_harness.py`) 전부 무변경.
  `artifacts/` 하위 historical run 디렉터리 / `artifacts/archive/` / `artifacts/*.md` 는 mtime 기준으로도
  이번 run 중 변경 없음.
- G(보안/파괴) 확인: secret 출력·기록 없음, 파괴적 연산 없음, 범위 밖 파일 변경 없음, commit/push 없음.
  Reviewer 가 mutation 실험을 위해 임시 수정한 core 6개 파일은 md5 대조로 **원본과 완전히 동일하게 복원**
  되었음을 확인했다(작업 종료 시 `git status --short` 가 세션 시작 시점과 동일).

## Final Decision

RESULT 는 FAIL 이다. 근거는 F-001 하나다.

`terminal_node` 가 소비된 correction queue 를 범위 밖으로 읽어 uncaught `IndexError` 로 죽는다.
AC 3 이 요구하는 "iteration budget 소진 시 계약된 terminal state 생성"이 이 조건에서 성립하지 않고,
checkpointer 를 사용하면 run 이 복구 불가능하게 고착된다. 이것은 설계 취향이나 일반적 개선 제안이 아니라
G2(결과가 동작하지 않음) 이며, max_iterations 2~5 전부와 downstream revalidation 경로에서 재현되는
결정론적 결함이다. risk=high, max-iterations=5 인 이 run 자신의 설정에서도 도달 가능한 경로다.

Responsible Phase 는 **implementation** 이다. 결함은 production code 의 동작 결함이며(executor.py:155),
DESIGN 은 이 indexing 을 지시하지 않았다. 회귀 테스트 추가는 이 correction 의 일부다.

나머지 13건은 전부 non-blocking 이다. CF-7 이 명시적으로 독립 판정을 요구한 mandatory unit-test gate
coverage 공백(N-001)은 재현했고, OS-40 AC 에 없고 현재 blast radius 가 0이며 정직하게 공시되어 있다는
세 가지 근거로 **blocking 이 아니라고 판정한다.** CF-6 이월 4건도 각각 재판정해 전부 non-blocking 으로
결론냈다 — 특히 N-010(D 계산 중복)은 해당 코드가 이 브랜치가 만든 것이 아니고 단 한 줄도 수정되지
않았으며 제거가 Out of Scope 라는 사실관계로 결정했다. 따라서 이 non-blocking 항목들 중 어느 것도
correction loop 를 시작시키지 않는다.

이번 correction 의 범위는 F-001 의 Required Action 1-3 으로 한정한다. Non-Blocking Findings 는
이번 correction 에서 고칠 것을 요구하지 않으며, 최종 보고의 "알려진 제한사항" 에 그대로 실려야 한다.

이 경계에서 사용자 권한을 필요로 하는 열린 결정 항목은 없었다. 모든 판정이 명시적 요구사항 문언과
Reviewer 가 직접 재현한 실행 결과로 결정되었다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "이 경계의 모든 판정이 검증 가능한 증거로 결정되었다. blocking finding F-001 은 Reviewer 가 직접 재현한 uncaught IndexError 이며 max_iterations 2~5 와 downstream revalidation 경로에서 결정론적으로 재현되고 OS-40 AC 3/AC 8 문언에 직접 대응한다. CF-7 의 mandatory unit-test gate 와 CF-6 이월 4건의 non-blocking 판정도 OS-40 AC/Scope/Out of Scope 문언과 git 사실관계, 그리고 Reviewer 가 직접 주입한 48건 mutation 결과로 결정되었고 모델 재량이나 사용자 권한을 필요로 하지 않았다. run-scoped decision ledger 30개 boundary 는 전부 CLEAR 이며 미해결 NEEDS_INPUT/CONFLICT 가 없다.",
  "scope": "This phase's own conduct at this iteration."
}
```
