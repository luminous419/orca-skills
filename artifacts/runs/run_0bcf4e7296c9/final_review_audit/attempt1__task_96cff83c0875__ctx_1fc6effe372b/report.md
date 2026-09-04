# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

OS-40의 골격은 실제로 동작한다. LangGraph `StateGraph` 하나가 전이의 유일한 실행 정의이고
(`scripts/deterministic_workflow/graph.py`), routing은 순수 함수로 분리되어 있으며, core 모듈은
Orca/terminal/session/credential/claude/codex/subprocess를 어떤 형태로도 참조하지 않는다(직접 grep 확인).
회귀는 없다. 나는 인용된 결과를 믿지 않고 아래 전부를 직접 재실행했고, 모두 재현되었다.

| 직접 실행한 검증 | 결과 |
| --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1753 in 326.784s` / `OK (skipped=6)` / exit 0 — CF-2 baseline(1725, skipped=6) 대비 +28, skip 동일, 회귀 없음 |
| 신규 3개 모듈 targeted | `Ran 28` / `OK` |
| dependency-absent lane (내가 직접 작성한 MetaPathFinder로 `langgraph*` ImportError 차단) | `Ran 28` / `OK (skipped=13)` / **errors=0** — CF-1 import 기반 guard 실제 적용 확인 |
| `python3 scripts/validate_skills.py` | `PASSED (727 checks)` / exit 0 |
| `python3 scripts/verify_package.py` | `PASSED (226 source files)` / exit 0 |
| `python3 scripts/validate_workflow_graph_docs.py` | `PASSED` / exit 0 |
| `git diff --check` | clean |
| `git status --short` | 이 run과 무관한 파일 변경 0건. `artifacts/` 아래 tracked 파일 변경 0건 |
| source/installed parity | `diff -r --exclude=__pycache__ scripts/deterministic_workflow orca-worker-reviewer-orchestration/tools/deterministic_workflow` → 동일 |
| decision ledger | 21개 boundary 전부 `CLEAR`, `open_decision_item: false`. 미해결 NEEDS_INPUT/CONFLICT 없음 |

그럼에도 gate는 FAIL이다. 두 개의 blocking finding이 있다.

첫째, **phase gate가 unknown reviewer verdict에 대해 fail-open한다.** AC 10과 DESIGN §2가 명시적으로
금지한 동작이며, "차단 전에 side effect 없음"이라는 DESIGN 자신의 fail-closed matrix 열을 위반한다.
둘째, **그 결함이 통과한 이유가 test suite에 있다.** AC 7(command 중복)과 AC 10(fail-closed default)의
guard를 코드에서 제거해도 suite가 초록으로 남는다는 것을 mutation으로 직접 확인했다.

나머지 지적사항(문서 heading 위치, replay 분기의 잠재 crash, carried-forward N-002~N-005)은
non-blocking이며 그 자체로 correction loop의 근거가 아니다. 특히 Coordinator가 "특히 주의해서
판정할 것"으로 지목한 carried-forward N-002(D 계산 중복)는 아래에서 blocking이 아니라고 판정했고,
그 근거를 명시했다.

## Blocking Findings

ID: F-001
Quality Attribute: G1
Severity: CRITICAL
Blocking: YES
Responsible Phase: implementation
Location: `scripts/deterministic_workflow/routing.py:42` (`phase_gate`), 그리고 그 값을 소비하는
`routing.py:73-81` (`route`)

Issue:
`phase_gate()`가 `reviewer.get("result", "BLOCK")`으로 reviewer verdict를 **검증 없이 그대로 반환**하고,
`route()`는 그 값이 `BLOCK`/`PENDING`/`FAIL` 중 어느 것도 아니면 마지막 줄에서 `ADVANCE_PHASE`로
떨어진다. 즉 closed vocabulary 밖의 어떤 문자열이든 "PASS와 동일한 전이"를 얻는다.

Reason / Evidence:
내가 직접 실행해 확인한 값이다(원본 코드, 수정 없음):

```
reviewer.result='PASS'       -> phase_gate='PASS'       route='ADVANCE_PHASE'
reviewer.result='UNKNOWN'    -> phase_gate='UNKNOWN'    route='ADVANCE_PHASE'
reviewer.result=''           -> phase_gate=''           route='ADVANCE_PHASE'
reviewer.result=None         -> phase_gate=None         route='ADVANCE_PHASE'
reviewer.result='pass'       -> phase_gate='pass'       route='ADVANCE_PHASE'
reviewer.result='APPROVED'   -> phase_gate='APPROVED'   route='ADVANCE_PHASE'
```

compiled graph에서 end-to-end로도 재현했다. phases=("ANALYSIS","PLAN"), ANALYSIS reviewer가
`{"result":"UNKNOWN_VERDICT"}`를 반환:

```
terminal_status: BLOCKED
effect_count (agents dispatched): 5
phase_passes: {'ANALYSIS': None, 'PLAN': {...}}
dispatches: [('ANALYSIS','WORKER'), ('ANALYSIS','PHASE_REVIEWER'),
             ('PLAN','WORKER'), ('PLAN','PHASE_REVIEWER'), ('PLAN','FINAL_REVIEWER')]
```

ANALYSIS가 PASS하지 않았는데도 graph는 ANALYSIS를 통과시키고 **PLAN Worker, PLAN Reviewer,
Final Reviewer 세 개를 추가로 dispatch했으며 PLAN의 iteration budget을 소비했다.** 최종적으로
`all_phase_passes_current`가 Final Review에서 걸려 BLOCKED로 끝나기는 하지만, 그것은 세 번의
외부 side effect가 이미 발생한 **뒤**다.

이것이 왜 명시적 요구사항 위반인지:
- OS-40 AC 10: "malformed, unknown, out-of-order state/event 와 terminal 이후 transition 은
  fail closed 한다." unknown verdict는 fail-closed하지 않고 다음 phase를 진행시킨다.
- DESIGN.md §2 (line 51, 그리고 §2 말미): `ReviewResult = Literal["PASS","FAIL"]`,
  `QualityVerdict = Literal["PASS","PASS_WITH_NOTES","FAIL","BLOCKED"]`를 closed vocabulary로
  선언하고 **"Unknown strings never map to a default."** 라고 못박았다.
- DESIGN.md "Fail-closed input matrix" (line 415): `unknown/malformed event` → `validate_event`
  schema/vocabulary → `BLOCKED/UNKNOWN_EVENT or MALFORMED_EVENT`. 이 표의 세 번째 열 제목은
  **"Outcome before side effect"** 다.
- `grep -rn "validate_event\|ReviewResult\|QualityVerdict\|PASS_WITH_NOTES\|UNKNOWN_EVENT\|
  MALFORMED_EVENT" scripts/deterministic_workflow/` → **매치 0건.** DESIGN이 요구한 event
  vocabulary validator는 코드에 존재하지 않는다. 명세는 옳고 구현이 명세를 따르지 않았다
  (그래서 Responsible Phase는 design이 아니라 implementation이다).

참고로 `final_gate`의 unknown 값은 FAIL 경로로 떨어져 사실상 fail-closed다. 두 gate가 같은 종류의
입력에 대해 반대 방향으로 동작한다는 점도 이 결함이 의도가 아니라 누락임을 보여준다.
`worker_result["status"]`는 `!= "COMPLETE"`를 BLOCK으로 처리해 올바르다. 문제는 reviewer verdict 하나다.

Required Action:
settlement 결과의 verdict를 closed vocabulary로 검증해 side effect **이전에** fail-closed 한다.
최소한 다음 둘 중 하나:
(a) `phase_gate`/`final_gate`가 `{"PASS","FAIL","BLOCK"}` 밖의 값을 받으면 `"BLOCK"`을 반환하고,
    `route`가 unknown gate 값에 대해 `ADVANCE_PHASE`로 떨어지지 않게 한다. 또는
(b) DESIGN이 명시한 `validate_event`를 실제로 도입해 `VALIDATE_SETTLEMENT` 단계에서
    `MALFORMED_EVENT`/`UNKNOWN_EVENT`로 차단한다.
어느 쪽이든 `route`의 마지막 `return "ADVANCE_PHASE"`가 "그 외 전부"를 삼키지 않도록,
`ADVANCE_PHASE`는 gate가 정확히 `"PASS"`일 때만 반환되어야 한다.
DESIGN §2/fail-closed matrix와 코드가 일치하는지 확인하고, 불일치가 남으면 DESIGN도 함께 정정한다.

---

ID: F-002
Quality Attribute: G5
Severity: MAJOR
Blocking: YES
Responsible Phase: test
Location: `scripts/test_deterministic_workflow_graph.py`,
`scripts/test_deterministic_workflow_contracts.py`,
`artifacts/runs/run_0bcf4e7296c9/TEST.md` (AC 4 / AC 7 / AC 10 행)

Issue:
TEST.md는 AC 7과 AC 10을 실행 증거에 연결했다고 주장하지만, 그 AC들을 지키는 guard를
production code에서 제거해도 28개 test가 전부 통과한다. 즉 해당 AC에 대한 mutation sensitivity가 없다.

Reason / Evidence:
나는 guard를 하나씩 제거하고 targeted suite(28 tests)를 재실행한 뒤 **매번 원본으로 복원**했다
(최종 `git status`와 md5로 복원 확인). 결과:

| 제거한 guard | 대응 AC | suite 결과 |
| --- | --- | --- |
| `validate_settlement_node`의 event replay dedupe 분기 | AC 7 (event) | **FAILED (failures=2)** — 검출됨 |
| `all_phase_passes_current` → `return True` | AC 5 | **FAILED (failures=1)** — 검출됨 |
| `route`의 T4 responsible-phase budget guard | AC 3 | **FAILED (errors=1)** — 검출됨 |
| `phase_gate`의 missing-result default `"BLOCK"` → `"PASS"` | AC 10 | `OK` — **미검출** |
| `prepare_intent_node`의 `processed_command_ids` 중복 dispatch guard 제거 | AC 7 (command) | `OK` — **미검출** |
| `phase_gate`의 `worker.status != "COMPLETE"` block 제거 | AC 10 / BLOCKED terminal | `OK` — **미검출** |
| `route`의 `decision_state in (NEEDS_INPUT, CONFLICT)` block 제거 | AC 4 | `OK` — **미검출** |

미검출 4건의 의미:
- AC 7은 "중복 **Task, Dispatch**, artifact 또는 iteration consumption"을 요구한다. event 쪽 dedupe는
  검증되지만 **command 쪽(=중복 Dispatch를 실제로 막는 guard)** 은 제거해도 아무 test가 울지 않는다.
- AC 4의 decision block은 `test_decision_block_states_override_quality_without_budget_consumption`이
  덮는다고 TEST.md가 주장하지만, 그 test는 `round_kind == "PHASE_GATE"`에서만 실행된다. 이 경로에서는
  `phase_gate`가 먼저 BLOCK을 반환하므로 `route`의 decision guard는 사용되지 않는다.
  `round_kind == "FINAL_REVIEW"`에서 decision guard는 `route`의 그 한 줄이 유일한 방어인데,
  그 조합을 실행하는 test가 없다.
- AC 10의 fail-closed default 역시 negative test가 없다.

그리고 이 공백의 대가가 F-001이다. unknown verdict를 reviewer 결과로 흘려보내는 test가 하나도 없기
때문에 fail-open이 IMPLEMENTATION gate(3 iterations)와 TEST gate(2 iterations)를 모두 통과했다.
"test가 통과한다"는 사실이 "요구된 실패 조건을 검출한다"를 의미하지 않는 전형적인 사례다.

이것은 generic coverage 취향 지적이 아니라, Task spec이 명시적으로 요구한 검증
("테스트가 단순히 구현 결과를 재서술하지 않고, 잘못된 전이/중복 실행/checkpoint 손상 같은 실패
조건을 실제로 검출하는지 확인한다")과 Required Deliverable("mutation-sensitive test suite")에
대한 evidence 부재다.

Required Action:
위 표의 미검출 4건 각각에 대해, guard를 제거하면 실패하는 test를 추가한다. 범위는 그 4건으로 한정한다.
1. reviewer verdict가 closed vocabulary 밖(`"UNKNOWN"`, `""`, `None`, `"pass"`)일 때 `route`가
   `ADVANCE_PHASE`를 반환하지 않고, compiled graph에서 추가 dispatch(`effect_count`)가
   발생하지 않음을 검사한다. (F-001 수정 후 이 test가 통과해야 한다.)
2. `reviewer_result`에 `result` 키가 없을 때 BLOCK으로 떨어지는지 검사한다.
3. 이미 `processed_command_ids`에 있는 command_id로 intent를 준비하면
   `OUT_OF_ORDER_EVENT:processed command prepared`가 발생하는지 검사한다.
4. `round_kind == "FINAL_REVIEW"`이고 `decision_state`가 `NEEDS_INPUT`/`CONFLICT`일 때
   `route`가 `BLOCK`을 반환하는지, 그리고 worker `status != "COMPLETE"`가 BLOCK을 만드는지 검사한다.
추가 후 TEST.md의 AC 4/7/10 행을 실제 증거에 맞게 갱신한다.

## Non-Blocking Findings

ID: N-001
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
Location: `scripts/deterministic_workflow/executor.py:80-81` + `graph.py:34`
Issue: replay dedupe 분기가 `pending_intent`/`pending_event`를 `None`으로 만들지만
`VALIDATE_SETTLEMENT → APPLY_RESULT`는 **static edge**여서 `apply_result_node`가 곧바로 실행된다.
Reason / Evidence: 직접 실행 확인 —
`apply_result_node(validate_settlement_node(replayed_state))` → `TypeError: 'NoneType' object is
not subscriptable`. DESIGN의 fail-closed matrix는 이 행의 결과를 "no-op; counters/artifacts/effects
unchanged"로 규정하는데, 실제로는 uncaught TypeError다. 이 분기를 유일하게 실행하는 test
(`test_compiled_graph_dedupes_replayed_settlement_event`)는 `interrupt_before=["APPLY_RESULT"]`로
compile하기 때문에 `APPLY_RESULT`가 아예 실행되지 않아 crash가 가려진다.
Blocking이 아닌 이유: 정상 graph 흐름만으로는 도달할 수 없다. `APPLY_RESULT`가 끝나면
`pending_intent`가 `None`이 되므로 같은 event가 `intent_status == "SETTLED"` 상태로 다시 나타나는
checkpoint 조합이 만들어지지 않는다. `graph.update_state(..., as_node="EXECUTE_INTENT")` 같은
out-of-band 주입이 있어야 도달한다. 현재 동작하는 결과를 깨뜨리지 않는다.
Required Action (권고): F-001 수정으로 IMPLEMENTATION이 다시 열리는 김에 함께 처리하는 것이 자연스럽다.
dedupe 분기가 `route_token`을 세우고 `ROUTE`로 우회하게 하거나, `apply_result_node` 진입부에서
`pending_intent`/`pending_event`가 `None`이면 상태를 그대로 반환하게 한다.

ID: N-002
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: `scripts/deterministic_workflow/routing.py:22-29` (`responsible_phases`)
Issue: `responsible_phase`가 `requested`에 없으면 **blocking 여부를 보기 전에** raise한다.
따라서 `responsible_phase`가 없거나(→ `None`) 범위 밖인 **non-blocking** finding 하나만으로도
`apply_result_node`가 `OUT_OF_SCOPE_FINAL_REVIEW_FINDING`으로 run 전체를 ESCALATE시킨다.
Reason / Evidence: 직접 실행 —
`responsible_phases([{'finding_id':'N','blocking':False,'responsible_phase':'TEST'}], ('ANALYSIS','PLAN'))`
→ `ValueError: OUT_OF_SCOPE_FINAL_REVIEW_FINDING`. 이 run의 Finding Contract 자체가
"Responsible Phase 는 blocking finding 에만 의미가 있다"고 규정하므로, 실제 Final Review 결과를
그대로 먹이면 non-blocking note 때문에 escalate될 수 있다.
Blocking이 아닌 이유: DESIGN의 `Finding` TypedDict는 `responsible_phase`를 필수 필드로 정의하므로
스키마를 지킨 입력에서는 발생하지 않는다. 어떤 AC도 non-blocking finding의 처리를 규정하지 않는다.
Required Action: `if finding.get("blocking") is not True: continue`를 범위 검사보다 앞에 둔다(선택).

ID: N-003
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: `README.md:750-757`, `INSTALL.md:237-250`, `docs/COMPATIBILITY.md:168-176`,
`docs/ROADMAP.md:306-313`
Issue: 네 문서 모두 새 OS-40 절이 **기존 heading 바로 다음, 그 heading의 본문 앞**에 삽입되어
기존 본문이 엉뚱한 heading 아래로 밀려났다. 예: README에서 `## Execution-layer Difference` 바로
아래에 `## Deterministic workflow engine (OS-40)`가 오고, 원래 그 절의 본문
("The two skills intentionally share development policy…")이 OS-40 절의 내용이 되었다.
INSTALL.md의 `### OS-30 clarification tool`, COMPATIBILITY.md의 `### OS-30 compatibility`도 동일한
패턴으로 본문이 OS-40 절 아래로 옮겨졌다.
Reason / Evidence: `git diff INSTALL.md README.md docs/COMPATIBILITY.md docs/ROADMAP.md`의 context
줄에서 직접 확인. 내용 자체는 정확하고 검증기도 통과한다 — 위치만 틀렸다.
Blocking이 아닌 이유: documentation polish이며 동작에 영향이 없다(common.md "Not Blocking by Default").
Required Action: 각 절을 해당 heading의 본문 뒤로 옮긴다(선택).

ID: N-004
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: `artifacts/runs/run_0bcf4e7296c9/DESIGN.md:248` vs
`scripts/deterministic_workflow/executor.py:27,106`
Issue: DESIGN §2 Nodes 표는 CF-3 해소로 `TERMINAL`을 "sole terminal-field writer"로 확정했으나,
`validate_node`와 `apply_result_node`도 `terminal_reason`을 쓴다.
Reason / Evidence: 실제로는 두 node가 **reason code carrier**로만 쓰고 `terminal_node`가
`(new.get("terminal_reason") or {}).get("code")`로 읽어 최종 값을 단독으로 확정하므로 동작은 CF-3의
의도와 일치한다. 문장이 코드보다 강하게 쓰였을 뿐이다.
Required Action: DESIGN 문장을 "sole writer of the final terminal_status/terminal_reason value;
upstream nodes may only stage a reason code"로 정밀화한다(선택).

### Carried-forward 항목에 대한 판정

Coordinator가 "실제로 non-blocking인지 스스로 판정하라"고 지목한 다섯 항목을 각각 판정했다.
**다섯 개 모두 non-blocking으로 확인했다.** 근거는 다음과 같다.

**CF-6 N-002 (D 계산 중복) — non-blocking.** "특히 주의해서 판정할 것"으로 지목된 항목이다.
사실관계는 확인했다: `downstream_revalidation_set`이 `scripts/e2e_harness.py:497`과
`scripts/deterministic_workflow/routing.py:14` 양쪽에 존재하고, `e2e_harness.py`는
`deterministic_workflow`를 import하지 않는다(grep 확인). 그럼에도 OS-40 위반이 아닌 이유:
(1) 금지 조항의 문언은 "LangGraph 와 별도로 동일한 전이 규칙을 수행하는 독자 event loop 나 병렬
transition engine 을 **만들지 않는다**"이다. `e2e_harness.py`는 이 run 이전부터 존재하는 tracked
파일이고 이번 diff에서 **전혀 수정되지 않았다**(`git diff --name-only`에 없음). 새로 만들어진 병렬
engine은 없다. (2) OS-40 Out of Scope는 "기존 Orca adapter 제거"를 명시적으로 범위 밖에 둔다.
(3) OS-40 Scope는 "prompt-owned control logic 과 graph-owned control logic 의 migration matrix"를
요구하는데, 이는 두 구현의 **공존을 전제**한 항목이다. 실제로 `docs/DETERMINISTIC_WORKFLOW.md`의
Migration matrix가 `e2e_harness.run_workflow`를 "test-only parity oracle for one compatibility
release"로, `downstream_revalidation_set`을 "pure router / canonical HIGH-only suffix calculation"으로
명시해 소유권을 문서화했다. 따라서 제거가 아니라 소유권 선언이 이 릴리스의 계약이며, 그것은 이행되었다.
다만 두 구현이 일치한다는 자동 증거는 없다(사용자 검증 항목 "기존 Orca 경로와 핵심 전이 결과의
parity"에 대응하는 test는 존재하지 않으며, AC 13이 검증하는 것은 fake↔Orca **adapter** parity로
서로 다른 대상이다). 이는 다음 correction에서 test를 하나 추가하면 닫히는 non-blocking 공백이다.
(참고: 두 함수의 signature도 다르다 — routing.py는 `risk` 인자로 HIGH-only를 강제하고,
e2e_harness는 인자가 없고 호출부에서 risk를 건다. 동작은 같지만 순수 함수 계약은 다르다.)

**CF-6 N-003 (SKILL prose 축소 미이행) — non-blocking.** SKILL.md 변경은 9줄 추가가 전부다
(`git diff` 확인). 다만 그 9줄은 `workflow-graph-contract` 블록과 함께 "Coordinators execute the
action selected by the graph and do not independently choose a next phase or retry"라는 지시문을
포함하며, 이는 Scope 항목("Coordinator LLM 이 다시 판단하지 않도록")의 기능적 목적을 명시적으로
달성한다. AC에는 "Skill 축소"가 없고, 관련 AC 14("graph contract 와 Skill documentation 의 parity 가
자동 검증된다")는 충족되었다 — 내가 mutation으로 직접 확인했다(아래 Test Review). prose 분량 축소는
polish이며 G1-G5 어디에도 걸리지 않는다.

**CF-6 N-004 (validator 강도) — non-blocking.** 사실이다.
`test_core_checkpoint_modules_have_no_runtime_specific_imports_or_fields`는 import 문에서
`orca`와 `subprocess`만 보고, DESIGN.md:36이 요구한 `terminal`/`session`/`credential`/`claude`/
`codex` 및 field name은 보지 않는다. 그러나 나는 core 7개 모듈 전체를 직접 grep해
(`orca|terminal_handle|session|credential|claude|codex|subprocess`) **현재 위반이 0건**임을
확인했다(`state.py`의 `FORBIDDEN_KEYS` 정규식은 guard 자체이므로 위반이 아니다). 따라서 지금 결과는
옳고, 노출된 것은 미래 회귀 위험뿐이다. 마찬가지로 `graph_spec`의 cycle guard 검사는 실제 cycle
분석이 아니라 상수 비교지만, unreachable/dead-end 검사는 실제 그래프 순회로 구현되어 있고
AC 11이 요구하는 invalid edge/unreachable path 탐지는 mutation으로 검출됨을 확인했다.

**CF-6 N-005 (`_langgraph_ok` 중복) — non-blocking.** 두 test module에 복제되어 있다. minor
duplication이며 common.md의 "Not Blocking by Default" 목록에 명시적으로 포함된 항목이다. 더구나
`test_deterministic_workflow_adapters.py`의 `LangGraphGuardTests`가 `graph` module의 helper를
import해 blocked-import 상황을 실제로 검사하므로 CF-1이 요구한 성질은 보장된다.

**AC 15 증거가 수동 검토 의존 — non-blocking, 그리고 나는 직접 재확인했다.**
`git diff --name-only`는 7개 파일만 반환하며 그중 `artifacts/` 아래 파일은 0건,
OS-28~30 schema 모듈(`decision_policy.py`, `decision_gate.py`, `clarification_protocol.py`,
`workflow_contract.py`, `quality_profile.py`, `agent_profile.py`)도 0건이다. historical run
디렉터리는 전부 untracked 상태 그대로다. AC 15는 충족되었다.

## Test Review

**suite가 실제로 검출하는 것 (mutation으로 직접 확인).**
event replay dedupe 제거 → `FAILED (failures=2)`. `all_phase_passes_current` → `True` 고정
(AC 5, phase PASS가 Final PASS를 대체) → `FAILED (failures=1)`. T4 responsible-phase budget guard
제거 (AC 3) → `FAILED (errors=1)`. AC 14 parity validator도 진짜로 동작한다: SKILL.md의
`workflow-graph-contract` 블록에서 route token 하나를 빼거나 `downstream_revalidation`을
`high_only` → `all_risks`로 바꾸자 `validate_workflow_graph_docs.py`와 `validate_skills.py`가
**둘 다 exit 1**로 실패했고, 복원 후 다시 exit 0이 되었다. 즉 graph contract와 Skill prose의
drift는 자동으로 막힌다.

**suite가 검출하지 못하는 것.** F-002의 표에 있는 4건. AC 7의 command 중복 guard, AC 10의
fail-closed default 두 개, AC 4의 FINAL_REVIEW 경로 decision guard. 이 네 guard는 제거해도
28 tests가 전부 통과한다.

**dependency-absent lane (CF-1).** 검증된 형태가 실제로 적용되었다. 두 test module 모두
`import langgraph` / `import langgraph.graph`를 try/except로 감싼 뒤에야
`importlib.metadata.version`을 확인하는 import 기반 guard를 쓴다. 내가 직접 작성한
MetaPathFinder(`find_spec`에서 raise하는 형태)로 `langgraph*`를 차단하고
`unittest discover`를 돌린 결과 `Ran 28 / OK (skipped=13) / errors=0`이었다. iteration 2의
FAIL 원인(absent lane이 skip이 아니라 error로 끝남)은 실제로 해소되었다.

**회귀.** full suite `Ran 1753 / OK (skipped=6) / exit 0`. CF-2 baseline `Ran 1725 / OK (skipped=6)`
대비 +28이고 skip 수와 구성이 동일하다. 새 test는 CI의 `unittest discover -s scripts -p 'test_*.py'`
경로에서 실행되며 pytest 전용 기능에 의존하지 않는다(`unittest.skipUnless`만 사용).

**증거 무결성.** 이 리뷰를 위해 임시로 수정한 파일은 `routing.py`, `executor.py`,
`SKILL.md` 셋뿐이며 매 mutation 직후 원본으로 복원했다. 최종 `git status --short`가 리뷰 시작
시점과 동일하고, targeted suite 재실행이 `Ran 28 / OK`임을 확인했다. repository는 손대지 않은 상태다.

## Final Decision

이 gate의 결과는 FAIL이다.

blocking finding 2건이다.
- **F-001 (Responsible Phase: implementation)** — unknown reviewer verdict fail-open. AC 10과
  DESIGN §2/fail-closed matrix 위반이며, 차단 전에 3개의 agent dispatch가 실제로 발생함을
  compiled graph에서 재현했다. 명세는 옳고 구현이 명세를 따르지 않았다.
- **F-002 (Responsible Phase: test)** — AC 7(command 중복), AC 10(fail-closed default),
  AC 4(FINAL_REVIEW decision guard)의 guard를 제거해도 suite가 통과한다. F-001이 두 개의 phase
  gate를 통과한 직접적 원인이다.

correction 순서는 implementation → test이며, risk=high이므로 implementation 수정 후
downstream revalidation이 test에 도달한다.

나머지 non-blocking finding(N-001~N-004)과 carried-forward 다섯 항목은 이 gate를 실패시키는
근거가 아니다. carried-forward 항목은 다섯 개 모두 실제로 non-blocking임을 위에서 개별 판정했고,
특히 지목된 D 계산 중복은 "새 병렬 engine을 만들지 않는다"는 금지 조항을 위반하지 않는다
(`e2e_harness.py`는 pre-existing이며 이번 diff에서 수정되지 않았고, migration matrix가 소유권을
명시했다). 이 항목들은 최종 보고의 "알려진 제한사항"에 그대로 실리면 된다.

그 외 OS-40의 골격 — 단일 StateGraph, 순수 routing 함수, port/adapter 경계, Orca 비의존 core,
checkpoint/idempotency 계약, closed graph spec 검증, source/installed parity, dependency/license
문서화, 회귀 없음 — 은 내가 직접 재실행해 확인했고 모두 성립한다. 위 두 건을 닫으면 이 변경은
OS-40의 Acceptance Criteria를 충족한다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "이 경계의 판정은 전부 검증 가능한 증거로 결정되었다. 두 blocking finding은 명시적 요구사항(OS-40 AC 10, AC 7, AC 4)과 DESIGN.md의 문서화된 계약에 직접 대응하며, 내가 직접 재실행한 mutation 결과와 compiled graph 재현으로 뒷받침된다. carried-forward 다섯 항목의 non-blocking 판정도 OS-40 Scope/Out of Scope 문언과 git diff 사실관계로 결정되었고 모델 재량이나 사용자 권한을 필요로 하지 않았다. run-scoped decision ledger 21개 boundary는 전부 CLEAR이고 미해결 NEEDS_INPUT/CONFLICT가 없다. 사용자 권한이 필요한 열린 결정 항목은 없다.",
  "scope": "This phase's own conduct at this iteration."
}
```
