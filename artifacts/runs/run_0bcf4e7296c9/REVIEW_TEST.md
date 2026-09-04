# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

이 TEST phase 의 실행 증거는 **전부 재현되었고 하나도 틀리지 않았다.** 인용된 7개 명령을 직접
다시 돌린 결과 full suite `Ran 1752 / OK (skipped=6)`, targeted `Ran 27 / OK`,
dependency-absent lane `Ran 27 / OK (skipped=13)` errors=0, `validate_skills` 727 checks,
`verify_package` 226 files, graph-doc validator PASSED, `git diff --check` 무출력이다.
TEST.md 의 mutation 표에서 3개를 골라 직접 재주입해 **셋 다 검출됨을 확인**했고,
mapping 표가 지목한 test 함수 9개가 전부 실재한다.

**production code 를 건드리지 않았음을 엄밀히 확인했다.** engine 11개 파일이 설치본 mirror 와
byte-identical 이고 mirror 의 mtime 은 IMPLEMENTATION 시점(00:23~01:01) 그대로다 — 즉 TEST 가
mutation 을 넣었다 원복한 뒤 IMPLEMENTATION 승인 상태로 정확히 복귀했다는 뜻이다.
`routing/executor/fake_adapter/orca_adapter` 의 sha256 도 내가 IMPLEMENTATION iteration 3 에서
기록한 값과 동일하다. staging/commit/branch 전환 없음, 무관 artifact 무접촉.

신규 테스트 2개도 실질적이다. decision-block test 는 NEEDS_INPUT/CONFLICT 두 상태 각각에서
`quality_verdict="PASS"` 를 준 상태로도 BLOCKED 가 되고 effect=0 이며 **budget tuple 이 전후
동일**함을 검사한다(AC 4 를 이전보다 강하게 고정한다). artifact replay test 는 동일 content
재저장이 artifact 1개만 남기고 다른 content 는 `IdempotencyConflict` 로 거부됨을 검사한다.

**그러나 dispatch 가 이번 phase 의 핵심 판정 기준으로 지정한 mutation-sensitivity 에 구멍이
하나 있다.** 내가 스스로 고안한 mutation 4개 중 3개(AC 5 비대체성, AC 10 settlement binding,
AC 7 processed-event 기록)는 잡혔지만, **`state.py` 의 checkpoint 안전 guard 를 통째로 무력화한
mutation 은 전혀 잡히지 않았다** — suite 가 그대로 `OK` 다. 그 guard 는 실제로 동작하는
load-bearing 코드임을 확인했고(`artifact_binding.terminal_handle` 을 `NON_CHECKPOINTABLE_STATE`
로 거부한다), 그것을 건드리는 유일한 기존 테스트는 **top-level** key 를 쓰기 때문에 guard 가
없어도 closed-field 검사가 대신 잡아준다. 즉 "checkpoint state 에 process handle / terminal
handle / credential 을 저장하지 않는다" 는 **명시적 사용자 요구를 뒷받침하는 테스트가 없다.**
dispatch 는 checkpoint 축의 미검출 mutation 을 blocking 으로 규정했다.

수정 범위는 좁다 — allowed field 안에 forbidden key 를 중첩시키는 assertion 한 줄이면
이 mutation 이 잡힌다. 그 외 TEST 산출물은 그대로 유지된다.

## Blocking Findings

### F-001

```text
ID: F-001
Quality Attribute: G5
Severity: MAJOR
Blocking: YES
```

**Location**: `scripts/deterministic_workflow/state.py:37,69-78`(`FORBIDDEN_KEYS` / `_checkpointable`),
`scripts/test_deterministic_workflow_contracts.py:21-24`(유일하게 관련된 테스트),
`artifacts/runs/run_0bcf4e7296c9/TEST.md:107-113`(mutation 표에 checkpoint 안전 축 없음)

**Issue**: checkpoint 에 runtime handle/credential 이 들어가는 것을 막는 guard 를 **완전히
제거해도 전체 suite 가 통과한다.** 해당 요구를 검증하는 테스트가 사실상 없다.

**Reason (직접 주입·실행해 확인)**:

내가 고안한 mutation — `_checkpointable()` 의 forbidden-key 검사만 삭제:
```python
# before
if not isinstance(key, str) or FORBIDDEN_KEYS.search(key):
# mutated
if not isinstance(key, str):
```
```text
python3 -m unittest discover -s scripts -p 'test_deterministic_workflow*.py'
  -> OK        (검출 실패. 다른 3개 신규 mutation 은 모두 FAILED 로 검출되었다.)
```

그 guard 는 죽은 코드가 아니라 **실제로 동작한다**:
```text
state["artifact_binding"]["terminal_handle"] = "term_abc123"
validate_state(...) -> StateError: NON_CHECKPOINTABLE_STATE:state.artifact_binding.terminal_handle
```

그런데 이 guard 를 건드리는 유일한 테스트
(`test_non_checkpointable_and_unknown_state_fail_closed`)는 **top-level** 에
`terminal_handle="x"` 를 넣는다. 그 경우 guard 가 없어도
`if set(raw) != required: raise StateError("MALFORMED_STATE:closed fields")` 가 잡아주므로
테스트는 통과한다 — **테스트가 의도한 이유가 아닌 다른 이유로 초록이다.** allowed field 안에
forbidden key 를 중첩시키는 테스트는 저장소 전체에 없다(`grep` 확인: 해당 문자열은 그 한 줄뿐).

**왜 blocking 인가**:
- dispatch 가 이번 phase 에 대해 명시했다: "잡지 못하는 mutation 이 사용자 명시 항목
  (잘못된 전이 / 중복 실행 / **checkpoint 손상** / iteration budget / decision gate fail-closed)에
  해당하면 blocking 이다." 이 mutation 은 `NON_CHECKPOINTABLE_STATE` 를 무력화하므로 그 축에
  정확히 해당한다.
- 사용자 요구사항 원문: "checkpoint state 에는 process handle, terminal handle, credential 을
  저장하지 않는다." OS-40 LangGraph Architecture Requirement 에도 동일 조항이 있다.
  이 phase 의 산출물은 그 요구에 대한 **검증 증거**인데 그것이 비어 있다(G5).
- 보강 정황: 이월 항목 N-004(a)에 따르면 core AST scan 도 `terminal/session/credential/
  claude/codex` 를 보지 않는다. 즉 이 축은 **runtime guard 도 static scan 도 테스트되지 않은**
  상태다. (N-004 자체는 이월 non-blocking 이며 여기서 새로 blocking 으로 세우지 않는다.)

**Required Action**: `_checkpointable()` 의 forbidden-key 경로를 직접 겨냥하는 assertion 을
추가한다 — allowed field 안에 중첩된 forbidden key(예:
`state["artifact_binding"]["terminal_handle"] = "term_x"`, 또는 `pending_intent` 하위)가
`NON_CHECKPOINTABLE_STATE` 로 거부되는지 검사하면 된다. 추가 후 위 mutation 을 다시 넣어
실제로 FAILED 가 되는지 확인한다.

## Non-Blocking Findings

### N-001

```text
ID: N-001
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `artifacts/runs/run_0bcf4e7296c9/TEST.md:11-16`(`## Added / Modified Tests`)

**Issue**: TEST 는 기존 `test_needs_input_blocks_before_any_effect` 를 **제거하고** 신규
`test_decision_block_states_override_quality_without_budget_consumption` 로 대체했는데,
TEST.md 는 신규 추가만 적고 제거/대체 사실을 밝히지 않는다.

**Reason**: 대체 자체는 **강화**다 — 직접 비교했다. 구 테스트는 NEEDS_INPUT 하나만,
BLOCKED + effect=0 만 검사했다. 신규는 NEEDS_INPUT/CONFLICT 둘 다, `quality_verdict="PASS"` 를
준 상태에서의 우선순위, `terminal_reason["code"]`, effect=0, budget 전후 동일성까지 검사한다.
따라서 은폐나 약화가 아니며 요구사항 위반도 아니다. 다만 test 수가 26→27 인데 신규가 2개라는
불일치를 독자가 스스로 추론해야 한다(1751→1752 도 동일).

**Required Action**: optional — TEST.md 에 "구 테스트를 신규 테스트로 대체했다" 한 줄을 추가한다.

### N-002

```text
ID: N-002
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `artifacts/runs/run_0bcf4e7296c9/TEST.md:36`(AC 15 행)

**Issue**: AC 15(OS-28~30 schema / historical artifact 보존)의 증거가
"status/diff 검토 결과 … 변경 없음" 이라는 **수동 검토**다. 다른 행과 달리 자동화된 assertion 이
아니다.

**Reason**: 내가 독립적으로 확인한 결과 주장 자체는 참이다 — `decision_gate.py`,
`decision_policy.py`, `clarification_protocol.py`, `run_logging.py`, `e2e_harness.py`,
`orca_runtime_harness.py` 모두 무변경이고 `artifacts/` 의 다른 run 도 무접촉이다. 또한 기존
1752개 suite 안의 OS-28~30 테스트들이 사실상 회귀 gate 역할을 한다. 요구사항 위반이 아니다.

**Required Action**: optional — 표현을 "수동 확인 + 기존 OS-28~30 test suite 통과" 로 정확히
적으면 증거 성격이 분명해진다.

### N-003 (CF-6 이월 — TEST 가 올바르게 유지·공시함)

```text
ID: N-003
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `artifacts/runs/run_0bcf4e7296c9/TEST.md:127-136`(`## Remaining Gaps`)

**Issue**: CF-6 의 4개 항목(N-002 D 계산 중복, N-003 SKILL prose 축소 미이행, N-004 validator
강도, N-005 `_langgraph_ok` 중복)이 여전히 미해결이다.

**Reason**: dispatch 가 "TEST 가 반드시 고쳐야 하는 것은 아니다 … 이것 때문에 TEST 범위를
넓히지는 않는다" 고 명시했고, TEST.md 는 이를 `Remaining Gaps` 에 **정확히 그대로 공시**했다.
범위를 지킨 올바른 처리이며 이번 gate 의 blocking 근거가 아니다. 다만 F-001 의 근거에 적었듯
N-004(a)는 checkpoint 안전 축의 약점과 맞물려 있으므로 후속에서 함께 다루는 편이 좋다.

**Required Action**: 없음(최종 보고의 "알려진 제한사항" 으로 그대로 싣는다).

## Test Review

**내가 직접 실행한 명령과 결과 (TEST.md 주장 전건 대조)**

| 명령 | TEST.md 주장 | 내 실행 결과 |
| --- | --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | Ran 1752 / OK (skipped=6) | **`Ran 1752 tests in 322.243s` / `OK (skipped=6)`** — 일치. CF-2 baseline 1725 대비 증가, skip 정확히 6개 |
| `python3 -m unittest discover -s scripts -p 'test_deterministic_workflow*.py'` | Ran 27 / OK | **`Ran 27 tests` / `OK`** — 일치 |
| dependency-absent lane (MetaPathFinder blocker) | Ran 27 / OK (skipped=13) / errors=0 | **`Ran 27 tests` / `OK (skipped=13)`** — 일치, errors=0 |
| `python3 scripts/validate_skills.py` | 727 checks | **`Skill validation PASSED (727 checks)`** — 일치 |
| `python3 scripts/verify_package.py` | 226 source files | **`Package verification PASSED (226 source files)`** — 일치 |
| `python3 scripts/validate_workflow_graph_docs.py` | PASSED | **PASSED** — 일치 |
| `git diff --check` | 무출력 exit 0 | **무출력 exit 0** — 일치. `git diff --cached` 도 비어 있음 |

**mutation 표본 재현 (TEST.md 가 인용한 것 중 3개)**

| 축 | mutation | 내 실행 결과 |
| --- | --- | --- |
| 잘못된 전이 | `routing` 마지막 `return "ADVANCE_PHASE"` → `"COMPLETE"` | **FAILED (failures=6)** — 검출 ✓ |
| iteration budget | T4 guard `<= 0` → `< 0` | **FAILED (errors=1)** — 검출 ✓ |
| decision fail-closed | `route`/`phase_gate` 의 NEEDS_INPUT·CONFLICT guard 무력화 | **FAILED (failures=1, errors=1)** — 검출 ✓ |

**내가 스스로 고안한 신규 mutation 4개 (TEST.md 가 다루지 않은 축)**

| # | mutation | 겨냥한 요구 | 결과 |
| --- | --- | --- | --- |
| NEW-1 | `all_phase_passes_current()` → `return True` | AC 5 phase/final PASS 비대체 | **FAILED (failures=1)** — 검출 ✓ |
| NEW-2 | `validate_settlement_node` 의 `command_id` binding 검사 제거 | AC 10 out-of-order settlement | **FAILED (failures=1)** — 검출 ✓ |
| NEW-3 | `apply_result_node` 가 `processed_event_ids` 를 기록하지 않음 | AC 7 replay dedupe | **FAILED (failures=1)** — 검출 ✓ |
| NEW-4 | `_checkpointable()` 의 forbidden-key 검사 제거 | checkpoint 에 handle/credential 금지 | **OK — 검출 실패** ✗ (F-001) |

7개 mutation 중 6개 검출, 1개 미검출. 미검출 1개가 dispatch 가 지정한 blocking 축에 해당한다.
mutation 실행 시 동일-second bytecode 오인을 피하려고 매 실행 전 `__pycache__` 를 제거했다.

**원복 검증 (전체 11개 engine 파일 sha256, mutation 이전 baseline 과 대조)**
```text
0fd01144b0af3bca...  __init__.py        96cb00d0eba1f8d9...  graph_spec.py
b576574c74de2623...  contracts.py       379a19853998b628...  graph.py
fef9fa375ac3a282...  executor.py        7c16b0da8e582236...  migration.py
e27daf8d18aa6469...  fake_adapter.py    371c221b5ef5ff60...  orca_adapter.py
977b734510693140...  ports.py           c6465b82eec4190b...  routing.py
369873af2688373c...  state.py
```
전부 일치한다. 추가로 **source ↔ 설치본 mirror 11개 파일 `cmp` 결과 byte-identical** 이고
mirror 의 mtime 은 IMPLEMENTATION 시점(00:23~01:01) 그대로다 — mirror 는 TEST 가 건드리지
않았으므로 이 동일성은 engine source 가 IMPLEMENTATION 승인 상태로 정확히 복귀했다는 독립
증거다. `routing/executor/fake_adapter/orca_adapter` 의 값은 내가 IMPLEMENTATION iteration 3
에서 기록한 값과도 동일하다.

**커버리지 대조 (판정 항목 1)**: TEST.md mapping 표가 지목한 test 함수 9개가 전부 실재함을
확인했고(`test_full_happy_path…`, `test_reviewer_fail…`, `test_phase_budget_exhaustion…`,
`test_final_fail_exhausted…`, `test_final_budget_guard…`, `test_phase_pass_does_not_replace…`,
`test_same_state_has_same_route…`, 신규 2개), 신규 테스트가 의존하는 `FakeArtifactStore` 도
`fake_adapter.py:48` 에 실재한다. 사용자 11개 검증항목과 AC 1~16 중 **AC 4·5·7·10 은 위
mutation 으로 실효성까지 확인**했다. 유일하게 실효성이 확인되지 않은 축이 checkpoint
handle/credential 금지(F-001)다.

**tautology / 약화 점검 (판정 항목 3·5)**: 신규 decision-block test 는 구 테스트 대비
검사 항목이 늘었다(2개 decision state, quality 우선순위, terminal reason code, budget 불변).
통과시키려고 assert 를 약화시킨 흔적은 없다. TEST 가 production code 를 수정하지 않았음은
sha256/mirror 대조로 확인했으므로 "구현 결함을 코드 수정으로 덮었다" 는 경우도 아니다.
다만 제거된 테스트를 보고에 적지 않은 점은 N-001 로 남긴다.

**범위 (판정 항목 6)**: tracked 수정은 IMPLEMENTATION 이 남긴 7개 파일 그대로이고 추가 변경이
없다. `artifacts/` 의 다른 run·`archive/`·루트 `artifacts/*.md` 무접촉, staging/commit/push 없음,
branch 전환 없음.

**decision gate 형식 검증**: `DECISION_GATE_STATE:` 1개, ```` ```decision-gate ```` record 1개,
`STATUS:`/`UNIT_TEST_STATUS:` 각 1개. record 는 CLEAR 5개 key 만 사용하고 선언과 일치한다.
**오분류 없음.**

## Final Decision

이 phase gate 판정은 **FAIL** 이다 — blocking 1건(F-001, MAJOR, G5), non-blocking 3건.

먼저 분명히 해둔다. **이 TEST 는 정직하다.** 인용한 7개 명령 출력이 전부 사실이었고,
mutation 표본 3개를 재주입해 모두 검출됨을 확인했으며, production code 는 sha256 과 mirror
대조로 IMPLEMENTATION 승인 상태와 바이트 동일함을 입증했다. 신규 테스트 2개도 실질적이고,
CF-6 이월 항목을 범위 확대 없이 `Remaining Gaps` 에 정확히 공시했다. 회귀도 없다.

FAIL 의 이유는 단 하나다. dispatch 는 이번 phase 의 **핵심 판정 기준**을 mutation-sensitivity 로
정하고, 내가 스스로 고안한 mutation 이 명시 축에서 잡히지 않으면 blocking 이라고 규정했다.
내가 넣은 4개 중 3개는 잡혔지만, checkpoint 안전 guard 를 통째로 무력화한 mutation 은
suite 를 그대로 통과했다. 그 guard 는 실제로 동작하는 코드이고(중첩 `terminal_handle` 을
거부한다), 그것을 건드리는 유일한 테스트는 top-level key 를 써서 guard 없이도 다른 검사에
걸린다 — 즉 "checkpoint 에 handle/credential 을 넣지 않는다" 는 명시적 사용자 요구에 대한
검증 증거가 비어 있다(G5).

수정은 좁다: allowed field 안에 중첩된 forbidden key 가 `NON_CHECKPOINTABLE_STATE` 로 거부되는지
검사하는 assertion 하나를 추가하고, 위 mutation 으로 실제 검출되는지 확인하면 된다.
이미 통과 중인 1752 tests, 두 lane, 세 validator, 그리고 이번 phase 가 만든 신규 테스트 2개는
그대로 유지된다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This review's verdict follows from the explicit user verification list, the OS-40 acceptance criteria and this dispatch's stated mutation-sensitivity standard, applied to evidence I executed directly — both test lanes, all three validators, three reproduced mutations and four self-devised ones, with restoration verified by sha256 and source/mirror comparison; the single required correction is one assertion, so no item at this boundary needs user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```
