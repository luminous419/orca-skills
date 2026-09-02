# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

**iteration 1 의 blocking finding 3건은 모두 실제로 해소되었다.** 각각을 Worker 의 주장이 아니라
직접 실행으로 확인했다.

- **F-001 FIXED** — T4 guard 가 T2 **직후, `PREPARE_CORRECTION` 반환 전**에 정확히 배치되었다
  (`routing.py:69-71`). iteration 1 에서 내가 만든 재현 시나리오를 그대로 다시 돌린 결과
  `ESCALATED` / `MAX_ITERATIONS_REACHED` / `phase: ANALYSIS`, `phase_iterations=2`(최대 2),
  `remaining_phase_budget=0`(음수 아님), `effect_count=5`(iteration 1 은 8)로 예산 소진 후
  추가 dispatch 가 0건이다.
- **F-002 FIXED** — `OrcaAdapter` 가 호출하는 method 가 전부 `OrcaRuntimeHarness` 에 **실재한다**:
  `create_task`(:1667), `run_existing_task`(:2840, signature 와 `(attempt, terminal)` 반환까지 일치),
  `task_status`(:1713), `call`(:816); `attempt.body`/`attempt.dispatch_id` 도 `RuntimeAttempt`
  에 존재한다(:369, :360). parity 테스트도 형식만이 아니다 — 동일 scripted scenario 를
  FakeAdapter 와 `OrcaAdapter(OfflineHarness)` 두 경로로 graph 를 **실제로 실행**해
  `normalize_trace` 동일성을 assert 하고, live Orca 를 요구하지 않는다.
- **F-003 FIXED** — 요구한 5종 테스트가 모두 존재하고 통과한다. 그리고 **내가 직접 mutation 을
  다시 넣어 확인했다**: iteration 1 에서 통과해버렸던 **M4(event dedupe 제거)와 M5(T2 순서 뒤집기)가
  이제 둘 다 FAILED** 다. 새 mutation 3개(T4 guard 제거, capability 검사 무력화, D 강제 공집합)도
  전부 검출되어 새 테스트가 tautology 가 아님을 확인했다. 5개 mutation 모두 원복했고
  sha256 로 확인했다(`routing.py c6465b82…`, `executor.py fef9fa37…`, `fake_adapter.py e27daf8d…`).
  `test_phase_pass_does_not_replace_final_pass` 도 이제 이름대로 AC 5 양방향을 assert 한다.

IMPLEMENTATION.md 가 인용한 명령 출력도 **전부 재실행으로 확인했고 하나도 틀리지 않았다** —
full suite `Ran 1751 tests / OK (skipped=6)`(baseline 1725 대비 +26, skip 6 그대로 = 회귀 없음),
`validate_skills` 727 checks, `verify_package` 226 files, graph-doc validator PASSED,
`git diff --check` 무출력, staging/commit 없음. N-001 도 해소되었다(`ports.py` 조건부 import,
설치 layout 에서 import 성공을 직접 실행 확인). CF-1~CF-5 도 유지된다.

**그러나 이번 correction 이 새 결함 하나를 만들었다.** F-002 를 고치려고 추가한 parity 테스트
`test_fake_and_orca_adapter_have_identical_graph_trace` 가 **guard 없는 `AdapterTests` 클래스
안에서 `build_graph` 를 import** 한다. 그 결과 dependency-absent lane 에서 이 테스트가 skip 이
아니라 **ERROR** 로 끝난다 — 직접 재현했다:

```text
ModuleNotFoundError: No module named 'langgraph'
  scripts/test_deterministic_workflow_adapters.py:46  from ...graph import build_graph
Ran 26 tests ... FAILED (errors=1, skipped=12)
```

iteration 1 의 adapters module 은 langgraph 를 끌어오지 않았으므로 이는 **이번 correction 이
새로 만든 회귀**다. 승인된 DESIGN 은 absent lane 에 대해 "fail on any error/failure/unlisted skip"
을 명시하고, CF-1 의 요지가 정확히 "absent lane 은 error 가 아니라 skip 이어야 한다" 이며,
DESIGN 은 test 파일 수정을 IMPLEMENTATION 의 몫으로 못박고 있다. 따라서 TEST 가 이 lane 을
실행하면 자신이 고칠 수 없는 실패를 만나게 된다. 수정은 한 줄이다(해당 테스트를 guard 된
클래스로 옮기거나 `AdapterTests` 에 동일 `skipUnless` 를 적용).

이 한 건 때문에 RESULT 는 FAIL 이다. 그 외에는 이번 correction 이 범위를 넘지 않았고
(DESIGN.md 추가 수정 없음, 다른 run/artifact 무접촉, OS-28~30 자산 무변경), 통과 자산도 모두
유지된다.

## Blocking Findings

### F-004

```text
ID: F-004
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
```

**Location**: `scripts/test_deterministic_workflow_adapters.py:19` (`class AdapterTests` — guard 없음),
`:46,61,63` (`build_graph` import 및 사용)

**Issue**: F-002 correction 으로 추가된 parity 테스트가 LangGraph 를 필요로 하는데
`AdapterTests` 에는 `@unittest.skipUnless(_langgraph_ok(), ...)` guard 가 없다. 따라서
dependency-absent lane 에서 skip 이 아니라 ERROR 가 발생한다.

**Reason (직접 재현)**: `sitecustomize` 로 `langgraph` import 를 차단하는 `MetaPathFinder` 를
주입하고 저장소 명령을 그대로 실행했다.

```text
# dependency-ABSENT lane
PYTHONPATH=<blocker> python3 -m unittest discover -s scripts -p 'test_deterministic_workflow*.py'
  File ".../test_deterministic_workflow_adapters.py", line 46, in
        test_fake_and_orca_adapter_have_identical_graph_trace
    from scripts.deterministic_workflow.graph import build_graph
  ModuleNotFoundError: No module named 'langgraph'
  Ran 26 tests ... FAILED (errors=1, skipped=12)

# 대조군(차단 없음)
python3 -m unittest discover -s scripts -p 'test_deterministic_workflow*.py'
  Ran 26 tests in 0.133s ... OK
```

`WorkflowGraphTests` 는 `test_deterministic_workflow_graph.py:17` 의 guard 로 12개가 정상 skip
되지만, `AdapterTests` 만 guard 밖에 있다. iteration 1 의 adapters module 은
`contracts/fake_adapter/migration/state` 만 import 했고 `build_graph` 를 쓰지 않았으므로
**이 회귀는 이번 correction 이 새로 도입한 것**이다.

위반 대상:
- **승인된 DESIGN** (`DESIGN.md` §"Dependency-present/absent compatibility and CF-1"):
  "Absent lane permits those six plus an explicit class-name allowlist of graph-runtime tests;
  … Both use unittest discover and **fail on any error/failure/unlisted skip**." error 는
  allowlist 대상인 skip 이 아니다.
- **[CF-1]** 의 목적: absent lane 은 error 가 아니라 skip 으로 끝나야 한다. guard 함수 자체는
  import 기반으로 올바르지만(CF-1 의 문언은 충족), guard 를 우회하는 테스트가 새로 생겨
  그 목적이 무너졌다.
- 결과적으로 **TEST phase 가 자신이 수정할 수 없는 실패를 만난다** — DESIGN
  ("Only IMPLEMENTATION may modify these production/test/docs files")이 test 파일 수정을
  IMPLEMENTATION 에 귀속시키기 때문이다.

**Required Action**: `test_fake_and_orca_adapter_have_identical_graph_trace` 를 langgraph guard
아래로 옮긴다 — `AdapterTests` 에 `@unittest.skipUnless(_langgraph_ok(), ...)` 를 적용하거나
(공유 helper 를 모듈로 추출), 해당 테스트만 guard 된 별도 클래스로 분리한다. 수정 후
차단 환경에서 `errors=0` 이고 skip 이 예상 클래스에서만 발생하는지 확인한다.

## Non-Blocking Findings

### N-002 (iteration 1 에서 이월 — 미해결)

```text
ID: N-002
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `scripts/e2e_harness.py:497`(무변경), `scripts/deterministic_workflow/routing.py:14`

**Issue**: `downstream_revalidation_set` 이 여전히 두 곳에 각각 존재한다. `e2e_harness.py` 는
이번에도 수정되지 않았다(`git status` 확인). DESIGN Implementation step 2 의 추출/compatibility
re-export 는 미이행 상태다.

**Reason**: dispatch 가 이 항목을 "OS-40 의 중복 transition engine 금지와 직결되므로 미해결이면
명확히 기록하라" 고 지시해 여기 남긴다. 다만 **금지 대상인 "중복 transition engine" 은 아니다** —
LangGraph graph 밖에 workflow loop 는 없고, `e2e_harness.run_workflow` 는 PLAN 이 명시적으로
허용한 test-only parity oracle 이며, 새 engine package 는 이를 import 하지 않는다. 중복된 것은
순수 함수 하나이고 두 구현의 동작은 현재 일치한다. Worker 는 이번 correction 의 좁은 범위 밖이라
보류했다고 명시했다 — 타당한 판단이다.

**Required Action**: optional — 향후 `e2e_harness.py` 가 `routing.py` 를 re-export 하도록 정리하거나
DESIGN 의 해당 단계를 현실에 맞게 정정한다. 그때까지 두 구현의 D 정의가 갈라지지 않도록 주의한다.

### N-003 / N-004 (iteration 1 에서 이월 — 미해결)

```text
ID: N-003, N-004
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `orca-worker-reviewer-orchestration/SKILL.md`(prose 미축소),
`scripts/test_deterministic_workflow_adapters.py:32-38`(AST scan),
`scripts/deterministic_workflow/graph_spec.py:58`(cycle guard 상수 비교)

**Issue**: PLAN W6.1 의 Skill routing prose 축소는 여전히 미이행이고(anchor block 만 추가됨),
core AST scan 은 아직 `orca`/`subprocess` 만 검사하며(DESIGN §1 은 `terminal`/`session`/
`credential`/`claude`/`codex` 및 field name 까지 요구), cycle guard 검사는 여전히 실제 cycle
분석이 아니라 상수 집합 비교다.

**Reason**: AC 14(graph↔Skill parity 자동 검증)는 `validate_workflow_graph_docs.py` 로 충족되어
실제로 통과하고, checkpoint 안전성은 `state.py:37` 의 `FORBIDDEN_KEYS` 와 `_checkpointable()` 이
validation 시점에 실제로 강제한다. Worker 가 correction 범위 밖으로 보류한다고 명시했고
blocking 해소에 필요하지 않았다 — 타당하다.

**Required Action**: optional — 후속 작업으로 남긴다면 그 사실을 명시한다.

## Test Review

**내가 직접 실행한 명령과 결과**

| 명령 | 결과 |
| --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1751 tests in 324.986s` / `OK (skipped=6)` — IMPLEMENTATION.md 주장과 일치. baseline 1725 대비 +26, skip 정확히 6개 유지 → **회귀 없음** |
| `python3 -m unittest discover -s scripts -p 'test_deterministic_workflow*.py'` | `Ran 26 tests` / `OK` — 주장과 일치 |
| `python3 scripts/validate_skills.py` | `Skill validation PASSED (727 checks)` |
| `python3 scripts/verify_package.py` | `Package verification PASSED (226 source files)` |
| `python3 scripts/validate_workflow_graph_docs.py` | `Workflow graph documentation validation PASSED` |
| `git diff --check` / `git diff --cached --stat` | exit 0 무출력 / 비어 있음 (staging·commit 없음) |
| `PYTHONPATH=orca-worker-reviewer-orchestration/tools python3 -c 'import deterministic_workflow.ports'` | `installed ports import OK` — N-001 해소 확인 |
| **dependency-absent lane** (blocker 주입) | **`FAILED (errors=1, skipped=12)`** — F-004 |

**F-001 재현 (iteration 1 과 동일 시나리오: `phases=("ANALYSIS",)`, `risk=high`,
`max_iterations=2`, ANALYSIS 예산 2회 소진 후 Final Review FAIL)**
```text
phase_iterations[ANALYSIS] = 2 of max 2        (iteration 1: 3 of 2)
remaining_phase_budget     = {'ANALYSIS': 0}   (iteration 1: -1)
terminal_status            = ESCALATED         (iteration 1: COMPLETED)
terminal_reason            = {'code':'MAX_ITERATIONS_REACHED', 'phase':'ANALYSIS'}
adapter effect_count       = 5                 (iteration 1: 8)
PREPARE_INTENT             = worker/reviewer(PHASE_GATE), worker/reviewer(CORRECTION),
                             final_reviewer(FINAL_REVIEW)  — 예산 소진 후 추가 dispatch 0건
```
guard 위치도 코드로 확인했다: `routing.py:67` T2 → `:68` 빈 queue BLOCK → `:69-71` **T4 phase
budget guard** → `:72` `PREPARE_CORRECTION`. 순서가 계약대로다.

**F-002 검증**: `OrcaAdapter` 가 호출하는 harness method 를 원본에서 하나씩 확인했다 —
`create_task`(1667), `run_existing_task`(2840, `role, iteration, mode, task_id, *, phase, spec,
round_kind` 및 `-> tuple[RuntimeAttempt, str]` 일치), `task_status`(1713), `call`(816),
`RuntimeAttempt.dispatch_id`(360)/`body`(369). iteration 1 의 가상 method
(`execute_intent`/`send_intent`/`intent_status`/`interrupt_intent`)는 전부 제거되었다.
parity 테스트는 `OfflineHarness` 가 그 실제 이름·signature 를 구현하고, 같은 scripted results 로
graph 를 두 번 실행해 `normalize_trace` 동일성과 harness 호출 집합
(`{create_task, run_existing_task}`)을 assert 한다. live Orca 불필요.

**F-003 mutation 재확인 (직접 주입·실행·원복)**

| Mutation | iteration 1 | iteration 2 (내가 재확인) |
| --- | --- | --- |
| M4 settlement event dedupe 제거 (`if False:`) | **OK — 미검출** | **FAILED (failures=2)** ✓ |
| M5 T2 guard 를 finding/phase routing 뒤로 이동 | **OK — 미검출** | **FAILED (failures=1)** ✓ |
| M6 (신규) F-001 의 T4 guard 제거 | — | **FAILED (errors=1)** ✓ |
| M7 (신규) `validate_node` 의 capability 검사 무력화 | — | **FAILED (failures=1)** ✓ |
| M8 (신규) `downstream_revalidation_set` 강제 공집합 | — | **FAILED (failures=1, errors=1)** ✓ |

신규 mutation 3개가 모두 검출되므로 새 테스트는 tautology 가 아니다. **원복 확인 (sha256)**:
`routing.py c6465b82eec4190b4c8a3ecb67332d6ca9ce40cfe2062ad73fafd9b573322be5`,
`executor.py fef9fa375ac3a282553ae07a1521b27d8c1cbdb45f5506ab39327fb00fbdcff6`,
`fake_adapter.py e27daf8d18aa6469a31c9cbfd5fa383de50e58f0d0f4e3d6efb693a9d048c523`
— 모두 mutation 이전 값과 일치하며, Worker 가 보고한 두 hash 와도 동일하다.
source/installed mirror 5개 파일도 `cmp` 로 byte-identical 을 재확인했다.

**추가된 5종 테스트 실재 확인**: `test_final_fail_exhausted_responsible_phase_escalates_before_dispatch`,
`test_final_budget_guard_precedes_responsible_phase_mapping`,
`test_missing_capability_blocks_without_effect`(AC 12, `effect_count==0` assert),
`test_high_final_correction_runs_downstream_revalidation`(D round 가 trace 에 실제로 나타나는지 assert),
`test_replayed_and_malformed_events_fail_closed_at_graph_node` +
`test_compiled_graph_dedupes_replayed_settlement_event`(compiled graph 경계까지) 전부 존재하고 통과한다.
`test_phase_pass_does_not_replace_final_pass` 는 이제 (a) phase PASS 가 있어도 final FAIL 이면
COMPLETED 가 아니고 (b) phase pass 를 제거하면 final PASS 상태에서 `route` 가 `BLOCK` 을 반환함을
양방향으로 assert 한다 — iteration 1 의 지적이 해소되었다.

**범위 확인 (판정 항목 5)**: tracked 수정은 iteration 1 과 동일한 7개 파일뿐이고 추가 변경이 없다.
`DESIGN.md` 는 이번 라운드에 추가 수정되지 않았다. `artifacts/` 의 다른 run, `archive/`,
루트 `artifacts/*.md` 는 무접촉이다. OS-28~30 자산(`decision_gate.py`, `decision_policy.py`,
`clarification_protocol.py`, `run_logging.py`)과 `e2e_harness.py`, `orca_runtime_harness.py` 는
**무변경**이다(AC 15 유지). staging/commit/push 없음.

**decision gate 형식 검증**: `DECISION_GATE_STATE:` 1개, ```` ```decision-gate ```` record 1개,
`STATUS:`/`UNIT_TEST_STATUS:` 각 1개. record 는 CLEAR 5개 key 만 사용하고 선언과 일치한다.
**오분류 없음.**

## Final Decision

이 phase gate 판정은 **FAIL** 이다 — blocking 1건(F-004, MAJOR, G1), non-blocking 3건(이월).

먼저 분명히 해둘 것: **iteration 1 의 blocking 3건은 전부 진짜로 고쳐졌다.** 나는 Worker 의
주장을 믿지 않고 F-001 재현 시나리오를 다시 돌렸고(ESCALATED/MAX_ITERATIONS_REACHED,
effect_count 8→5, budget 음수 해소), OrcaAdapter 가 호출하는 harness method 를 원본에서 하나씩
대조했고, iteration 1 에서 통과해버렸던 M4·M5 mutation 을 직접 다시 주입해 이제 둘 다 실패함을
확인했으며, 신규 mutation 3개로 새 테스트가 tautology 가 아님까지 검증했다. 인용된 명령 출력도
전부 사실이었고 회귀는 없다(1751 tests, skip 6 유지).

FAIL 의 이유는 단 하나, 이번 correction 이 새로 만든 회귀다. F-002 를 고치려고 추가한 parity
테스트가 guard 없는 클래스에 놓여 dependency-absent lane 을 skip 이 아니라 **error** 로 만든다.
직접 차단 환경에서 재현했다. 승인된 DESIGN 은 그 lane 이 어떤 error 도 없어야 한다고 못박았고,
CF-1 의 존재 이유가 정확히 이 실패 모드이며, test 파일은 DESIGN 상 IMPLEMENTATION 만 고칠 수
있으므로 TEST 는 자신이 해결할 수 없는 실패를 만나게 된다. 그래서 여기서 막는 편이 옳다.

수정은 한 줄이다 — 해당 테스트를 langgraph guard 아래로 옮기면 된다. 그 외에는 손댈 것이 없다.
이미 통과 중인 1751 tests, 727 checks, 226 files, parity/packaging 자산과 세 blocking 수정은
그대로 유지된다. 이월된 N-002~N-004 는 blocking 근거가 아니며, Worker 가 correction 범위 밖으로
보류한다고 명시한 판단은 타당하다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This re-review's verdict follows from explicit OS-40 requirements, the approved DESIGN and the carried-forward CF-1 constraint applied to evidence I executed directly — the full regression suite, every validator the Worker cited, a re-run of the iteration-1 defect reproduction, five applied-and-reverted mutations verified by sha256, and a reproduced dependency-absent lane error; the single required correction is fully determined by the approved DESIGN, so no item at this boundary needs user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```
