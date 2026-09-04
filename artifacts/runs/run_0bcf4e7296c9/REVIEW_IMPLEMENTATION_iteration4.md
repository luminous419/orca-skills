# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

**Final Review F-001 의 결함 자체는 고쳐졌다.** Final Adversarial Reviewer 가 제시한 증거 두
가지를 그대로 재현했고 둘 다 사라졌다.

verdict vocabulary 표(원본에서는 `UNKNOWN`/`''`/`None`/`pass`/`APPROVED` 전부
`route='ADVANCE_PHASE'`):
```text
reviewer.result='PASS'            phase_gate='PASS'   route='ADVANCE_PHASE'
reviewer.result='FAIL'            phase_gate='FAIL'   route='PREPARE_CORRECTION'
reviewer.result='UNKNOWN'         phase_gate='BLOCK'  route='BLOCK'
reviewer.result=''                phase_gate='BLOCK'  route='BLOCK'
reviewer.result=None              phase_gate='BLOCK'  route='BLOCK'
reviewer.result='pass'            phase_gate='BLOCK'  route='BLOCK'
reviewer.result='APPROVED'        phase_gate='BLOCK'  route='BLOCK'
```
end-to-end(`phases=("ANALYSIS","PLAN")`, ANALYSIS reviewer 가 `{"result":"UNKNOWN_VERDICT"}`):
```text
effect_count : 2      (Final Reviewer 가 관측한 값은 5)
dispatches   : [('ANALYSIS','WORKER'), ('ANALYSIS','PHASE_REVIEWER')]
              -> PLAN Worker / PLAN Reviewer / Final Reviewer 세 건이 모두 사라졌다
terminal     : BLOCKED / UNKNOWN_EVENT
phase_iterations: {'ANALYSIS': 0, 'PLAN': 0}   (budget 소비 없음)
```
Worker 는 Required Action 의 "최소한 둘 중 하나" 를 넘어 **(a)와 (b)를 모두** 구현했다 —
`contracts.validate_event()` 를 도입해 `VALIDATE_SETTLEMENT` 에서 closed vocabulary 를 검증하고,
동시에 `phase_gate`/`final_gate` 를 PASS/FAIL 외 값은 BLOCK 으로 정규화하며 `route` 의 마지막
줄을 `return "ADVANCE_PHASE" if gate == "PASS" else "BLOCK"` 으로 바꿨다. 두 gate 가 이제 같은
입력에 같은 방향으로 동작한다(판정 항목 2 충족). 정상 PASS/FAIL/PENDING 전이도 그대로다.
DESIGN 은 손대지 않았고 그것이 옳다 — DESIGN 은 이미 `validate_event`(`:270`)와 fail-closed
matrix(`:415`), "Unknown strings never map to a default"(`:58`)를 요구하고 있었다.

회귀도 없다. full suite `Ran 1754 / OK (skipped=6)`, targeted `Ran 29 / OK`, absent lane
`Ran 29 / OK (skipped=14)` errors=0, 세 validator 통과, `git diff --check` 무출력, staging 없음,
mirror 11개 파일 byte-identical. `UNIT_TEST_STATUS: PASS` 가 있고 재실행으로 확인된다.

**그러나 dispatch 항목 8(새 guard 가 실제로 load-bearing 인지 mutation 으로 확인)에서 문제가
나왔다.** 새로 들어온 guard 5개를 하나씩 되돌려 본 결과, **routing 계층 guard 3개는 되돌려도
29개 테스트가 전부 통과한다**:

| 되돌린 guard | 결과 |
| --- | --- |
| `phase_gate` 를 원래 결함 형태 `reviewer.get("result","BLOCK")` 로 | **OK — 미검출** |
| `route` 마지막 줄을 catch-all `return "ADVANCE_PHASE"` 로 | **OK — 미검출** |
| `final_gate` 를 permissive 형태로 | **OK — 미검출** |
| `validate_event` 호출 제거 | FAILED ✓ |
| `validate_event` 의 reviewer-result 검사 무력화 | FAILED ✓ |

즉 신규 regression test 가 실제로 고정하는 것은 `validate_event` 한 층뿐이다. 그리고 그 세
guard 는 장식이 아니다 — **checkpoint 복구 경로에서는 그것들이 유일한 방어선**임을 직접
확인했다. `validate_state` 는 `reviewer_result={"result":"UNKNOWN_VERDICT"}` 를 **수용**하므로,
복구된 state 는 `validate_event`(settlement 시점에만 동작)를 거치지 않고 곧장 ROUTE 로 간다.
guard 가 있으면 `BLOCK`, `route` 를 되돌리면 `ADVANCE_PHASE` 다. AC 8(checkpoint 복구)과
AC 10(unknown state fail-closed)이 정확히 이 경로를 요구한다.

이것은 이 run 의 Final Adversarial Review 가 **F-002 로 이미 blocking 처리한 것과 같은 형태**다
("guard 를 production code 에서 제거해도 테스트가 전부 통과한다"). 같은 기준을 새 guard 에도
적용해 blocking 으로 세운다. 수정은 작다 — state 에 직접 unknown verdict 를 심어
`route`/graph 가 BLOCK 하는지 보는 테스트 하나면 위 세 mutation 이 모두 잡힌다.

**내 자신의 기록도 밝혀 둔다.** F-001 은 내가 IMPLEMENTATION gate 를 3 iteration 동안 통과시키며
놓친 결함이다. 그래서 이번에는 "요구된 수정이 들어갔는가" 에서 멈추지 않고 각 guard 를 실제로
되돌려 보았고, 그 과정에서 이 gap 이 드러났다.

## Blocking Findings

### F-003

```text
ID: F-003
Quality Attribute: G5
Severity: MAJOR
Blocking: YES
```

**Location**: `scripts/deterministic_workflow/routing.py:52-53`(`phase_gate` closed vocabulary),
`:59-60`(`final_gate`), `:81`(`route` 의 `ADVANCE_PHASE if gate == "PASS" else "BLOCK"`),
`scripts/test_deterministic_workflow_graph.py:52-61`(유일한 F-001 regression test)

**Issue**: F-001 수정으로 추가된 **routing 계층 guard 3개에 mutation sensitivity 가 없다.**
셋을 각각 결함 이전 형태로 되돌려도 29개 테스트가 전부 통과한다.

**Reason (직접 주입·실행·원복)**:

```text
M1  phase_gate -> reviewer.get("result", "BLOCK")        (원래 결함 형태)  -> OK   미검출
M2  route 마지막 줄 -> return "ADVANCE_PHASE"            (catch-all 복원)  -> OK   미검출
M5  final_gate -> result.get("result", "BLOCK")          (permissive 복원) -> OK   미검출
M3  validate_settlement_node 의 validate_event 호출 제거                   -> FAILED  검출 ✓
M4  validate_event 의 reviewer-result vocabulary 검사 무력화               -> FAILED  검출 ✓
```

신규 test `test_unknown_phase_reviewer_verdict_blocks_before_next_phase_effect` 는 unknown
verdict 를 **adapter settlement 로** 주입하므로 항상 `validate_event` 에서 먼저 걸린다. 따라서
그 뒤에 있는 routing guard 는 이 테스트로 한 번도 실행되지 않는다.

**그 guard 들이 죽은 코드가 아님을 확인했다** — checkpoint 복구 경로에서는 유일한 방어선이다:
```text
validate_state(state with reviewer_result={"result":"UNKNOWN_VERDICT"})  -> ACCEPTS
  (closed vocabulary 검사가 state validator 에는 없다)
route(그 state)  -> "BLOCK"        (현재 guard 가 있어서)
route(M1/M2 되돌린 뒤)  -> "ADVANCE_PHASE"
```
`validate_event` 는 settlement 시점에만 동작하므로 복구된 state 에는 적용되지 않는다.

**왜 blocking 인가**:
- **AC 10**: "malformed, unknown, out-of-order **state**/event ... 는 fail closed 한다."
  unknown verdict 를 담은 복구 state 는 여기 해당하고, 그것을 막는 유일한 guard 에 테스트가 없다.
- **AC 8**: 같은 checkpoint 를 복구하면 동일 next node 가 선택되어야 한다. 이 경로의 정확성을
  고정하는 테스트가 없다.
- 이 run 의 Final Adversarial Review 가 **F-002 를 정확히 같은 근거로 blocking** 처리했다
  ("AC 를 지키는 guard 를 제거해도 28개 test 가 전부 통과한다"). 새 guard 에 같은 기준을
  적용하지 않으면 다음 Final Review 에서 동일 지적이 반복될 가능성이 높다.

**Required Action**: routing 계층 guard 를 직접 겨냥하는 테스트를 추가한다. settlement 를 거치지
않고 **state 에 직접** `reviewer_result={"result":"<unknown>"}` 을 넣고(또는 checkpoint 를 그
상태로 복구하고) `route(state) == "BLOCK"` 및 graph 가 다음 phase 를 dispatch 하지 않음을
assert 하면 된다. `final_gate` 에 대해서도 동일하게 한 줄 추가한다. 추가 후 위 M1/M2/M5 를
다시 넣어 실제로 FAILED 가 되는지 확인한다.

## Non-Blocking Findings

### N-007 (CF-6 이월 — 변동 없음)

```text
ID: N-007
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `artifacts/runs/run_0bcf4e7296c9/IMPLEMENTATION.md`(Remaining/Resolution 절)

**Issue**: CF-6 의 4건(N-002 D 계산 중복, N-003 SKILL prose 축소 미이행, N-004 validator 강도,
N-005 `_langgraph_ok` 중복)이 여전히 미해결이다. 이번 correction 은 이를 건드리지 않았다.

**Reason**: 이번 dispatch 는 Final Review F-001 correction 으로 범위가 좁혀져 있었고, 네 항목은
모두 그 범위 밖이며 blocking 근거가 아니다. 범위를 지킨 올바른 처리다.

**Required Action**: 없음. 최종 보고의 "알려진 제한사항" 으로 유지한다.

## Test Review

**내가 직접 실행한 명령 (IMPLEMENTATION.md 주장 전건 대조)**

| 명령 | IMPLEMENTATION.md 주장 | 내 실행 결과 |
| --- | --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | Ran 1754 / OK (skipped=6) | **`Ran 1754 tests in 323.867s` / `OK (skipped=6)`** — 일치, CF-2 baseline skip 6 유지 |
| `python3 -m unittest discover -s scripts -p 'test_deterministic_workflow*.py'` | Ran 29 / OK | **`Ran 29 tests` / `OK`** — 일치 |
| dependency-absent lane | Ran 29 / OK (skipped=14) / errors=0 | **`Ran 29 tests` / `OK (skipped=14)`** — 일치 (신규 test 가 guard 안에 있어 skip 13→14) |
| `python3 scripts/validate_skills.py` | 727 checks | **PASSED (727 checks)** |
| `python3 scripts/verify_package.py` | 226 source files | **PASSED (226 source files)** |
| `python3 scripts/validate_workflow_graph_docs.py` | PASSED | **PASSED** |
| `git diff --check` / `git diff --cached` | 무출력 / 없음 | **무출력 exit 0 / staged 없음** |

**F-001 재현 (판정 항목 1)** — 위 Summary 의 두 표가 그 결과다. verdict vocabulary 9종 중
`PASS` 만 `ADVANCE_PHASE` 로 가고 나머지는 전부 `BLOCK` 이며, end-to-end 에서 effect_count 가
5 → **2** 로 줄고 PLAN/Final dispatch 세 건이 사라졌다. `route` 의 마지막 줄이 더 이상
"그 외 전부" 를 삼키지 않는다(`routing.py:81`).

**`final_gate` 일관성 (판정 항목 2)**: 같은 입력 집합에 대해 `final_gate` 도
`PASS`/`FAIL` 외 값을 전부 `BLOCK` 으로 정규화한다. 두 gate 가 반대로 동작하던 문제는 해소되었다.

**정상 경로 (판정 항목 3)**: `PASS → ADVANCE_PHASE`, `FAIL → PREPARE_CORRECTION`,
reviewer 부재 → `PENDING → PREPARE_PHASE_REVIEWER` 가 그대로다. full suite 1754 통과가 이를
뒷받침한다(1753 → 1754, 신규 1건, skip 6 불변 = 회귀 없음).

**DESIGN 정합성 (판정 항목 4)**: DESIGN.md 는 이번 라운드에 **수정되지 않았다**(510 lines,
mtime 00:23). 그리고 수정이 필요 없는 것이 맞다 — DESIGN 은 이미
`validate_event(state, event) -> SettlementEvent`(`:270`), fail-closed matrix 의
`unknown/malformed event → BLOCKED/UNKNOWN_EVENT or MALFORMED_EVENT`(`:415`),
"Unknown strings never map to a default."(`:58`)를 요구하고 있었다. 구현이 명세를 따라온 것이지
명세가 바뀐 것이 아니다. IMPLEMENTATION.md 의 "DESIGN 정정은 필요 없었다" 는 서술이 정확하다.

**mandatory test gate (판정 항목 5)**: production code 3개 파일(`routing.py`, `executor.py`,
`contracts.py`)이 변경되었고 신규 test
`test_unknown_phase_reviewer_verdict_blocks_before_next_phase_effect` 가 추가되었으며
`UNIT_TEST_STATUS: PASS` 가 선언되어 있다. 재실행으로 PASS 를 확인했다. **gate 자체는 충족한다.**
그 test 가 어느 층까지 고정하는지가 F-003 이다.

**mutation 결과표 (판정 항목 8 — 내가 직접 주입·실행·원복)**

| # | mutation | 결과 |
| --- | --- | --- |
| M1 | `phase_gate` → 원래 결함 형태 `reviewer.get("result","BLOCK")` | **OK — 미검출** ✗ |
| M2 | `route` 마지막 줄 → catch-all `return "ADVANCE_PHASE"` | **OK — 미검출** ✗ |
| M3 | `validate_settlement_node` 의 `validate_event` 호출 제거 | **FAILED (failures=1)** ✓ |
| M4 | `validate_event` 의 reviewer-result vocabulary 검사 무력화 | **FAILED (failures=1)** ✓ |
| M5 | `final_gate` → permissive `result.get("result","BLOCK")` | **OK — 미검출** ✗ |

추가 확인: `validate_state` 가 `reviewer_result={"result":"UNKNOWN_VERDICT"}` 를 수용하므로
M1/M2/M5 가 되돌려진 상태에서 **복구된 checkpoint 는 unknown verdict 로 다음 phase 를 진행한다**.
즉 세 guard 는 도달 가능한 경로의 유일한 방어선이다.

**원복 검증 (engine 11개 파일 sha256, mutation 이전과 대조)**
```text
0fd01144b0af3bca...  __init__.py        96cb00d0eba1f8d9...  graph_spec.py
99f9b3d49df65fe2...  contracts.py       379a19853998b628...  graph.py
d7aebdbbd0c6b584...  executor.py        7c16b0da8e582236...  migration.py
e27daf8d18aa6469...  fake_adapter.py    371c221b5ef5ff60...  orca_adapter.py
977b734510693140...  ports.py           4dd4398113501501...  routing.py
369873af2688373c...  state.py
```
전부 일치하고, source ↔ 설치본 mirror 11개 파일 `cmp` byte-identical 이며, 원복 후 targeted
29 tests 가 다시 `OK` 다.

**범위 (판정 항목 7)**: 변경 파일은 `routing.py`, `executor.py`, `contracts.py`,
`test_deterministic_workflow_graph.py` 넷이다 — 전부 F-001 수정에 직접 관련된다.
`state.py`/`fake_adapter.py`/`orca_adapter.py`/`graph.py`/`graph_spec.py`/`ports.py`/`migration.py`
는 sha256 이 직전 라운드와 동일하고, DESIGN.md 도 무변경이다. tracked 수정은 이전과 같은 7개
파일뿐, `artifacts/` 의 다른 run·`archive/`·무관 파일 무접촉, staging/commit/push 없음.

**decision gate 형식 검증**: `DECISION_GATE_STATE:` 1개, ```` ```decision-gate ```` record 1개,
`STATUS:`/`UNIT_TEST_STATUS:` 각 1개. record 는 CLEAR 5개 key 만 사용하고 선언과 일치한다.
**오분류 없음.**

## Final Decision

이 phase gate 판정은 **FAIL** 이다 — blocking 1건(F-003, MAJOR, G5), non-blocking 1건(이월).

**Final Review F-001 의 결함 자체는 확실히 고쳐졌다.** Final Adversarial Reviewer 가 제시한
verdict vocabulary 표와 end-to-end dispatch leak 을 그대로 재현했고 둘 다 사라졌다 —
unknown verdict 는 이제 `BLOCK` 이고, effect_count 는 5에서 2로 줄어 PLAN/Final dispatch 세 건이
발생하지 않으며 budget 도 소비되지 않는다. Worker 는 Required Action 의 최소 요구를 넘어 (a)와
(b)를 모두 구현했고, 두 gate 를 일관되게 만들었으며, DESIGN 을 건드리지 않은 판단도 옳다
(DESIGN 은 처음부터 이 validator 를 요구하고 있었다). 회귀도 없고 범위도 지켰다.

FAIL 의 이유는 새로 들어온 **routing 계층 guard 3개에 mutation sensitivity 가 없다**는 것이다.
셋을 각각 결함 이전 형태로 되돌려도 29개 테스트가 전부 통과한다. 그리고 그 guard 들은 장식이
아니다 — `validate_state` 가 unknown verdict 를 담은 state 를 수용하므로, checkpoint 복구
경로에서는 `validate_event` 가 개입하지 않고 그 세 guard 만이 방어선이다. AC 8 과 AC 10 이
정확히 그 경로를 요구한다. 이 run 의 Final Adversarial Review 가 F-002 를 같은 근거로 blocking
처리한 이상, 새 guard 에 다른 기준을 적용할 수는 없다.

수정은 작다: settlement 를 거치지 않고 state 에 직접 unknown verdict 를 넣어 `route` 가 BLOCK
하는지 보는 테스트 하나(그리고 `final_gate` 용 한 줄)면 M1/M2/M5 가 모두 잡힌다. 이미 통과 중인
1754 tests, 두 lane, 세 validator, 그리고 F-001 수정 자체는 그대로 유지된다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This review's verdict follows from the OS-40 acceptance criteria and the Final Adversarial Reviewer's stated Required Action, applied to evidence I executed directly — the reproduced verdict-vocabulary table, the end-to-end dispatch-leak scenario, five guard mutations injected and reverted with sha256 verification, and a demonstration that validate_state admits an unknown verdict into the checkpoint path; the one required correction is a test, fully determined by the approved DESIGN, so no item at this boundary needs user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```
