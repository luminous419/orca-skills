# Worker Result
STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "이번 phase의 모든 판단은 사용자가 번호로 지정한 5개 필수 수정과 PR #28 외부 리뷰 전문, 그리고 저장소의 기존 계약(unittest runner, mirror parity, FORBIDDEN_KEYS, ROUTE_TOKENS)에서 직접 도출되었다. 설계 재량이 필요한 두 지점 — (1) crash window에서 recovery 불가 시 fail-closed 할 것인가, (2) M-004에서 삭제 대신 강등을 택할 것인가 — 은 각각 리뷰가 요구한 '중복 생성 금지'와 '제거 **또는** 명확한 강등' 문언 안에 있는 선택지이며, 사용자 권한이 필요한 미해결 항목이 아니다. 새 권한, 비가역 작업, 범위 확대는 없었다.",
  "scope": "This phase's own conduct at this iteration."
}
```

## Summary

PR #28 외부 리뷰의 1 CRITICAL + 4 MAJOR 를 모두 원인 수준에서 해결했다. 각 finding 은
**수정 전 코드에서 실패하는 회귀 테스트로 먼저 재현**한 뒤 고쳤고, Before FAIL / After PASS
실측 출력을 `artifacts/runs/run_9e3c67a7824e/evidence/` 에 남겼다. full suite 는
1761 → **1831 tests, OK (skipped=6)** 로 늘었고 회귀는 없다.

**iteration 2 (correction round):** phase Reviewer 가 blocking finding 1건(F-001)을 냈다 —
unknown field 를 compiled graph 에 **직접** 제출하면 fail closed 되지 않는다는 지적이다.
독립 재현 후 compiled graph 경계 자체에 guard 를 넣어 해소했다.

**iteration 5 (Final Review correction round, 마지막 iteration):** Final Adversarial Review
attempt 2 가 blocking 1건으로 FAIL 했다 — `GuardedWorkflowGraph.compiled` 가 public attribute 라
누구나 guard 되지 않은 raw graph 를 꺼낼 수 있다는 지적이다. compiled graph 를 façade 에서
완전히 제거해 해소했다. 상세는 `## Review Feedback Resolution` 의 FR2-M-002 절에 있다.

**iteration 4 (Final Review correction round):** 모든 phase gate PASS 후 실행된 Final
Adversarial Review 가 blocking 1건으로 FAIL 했다 — crash-safe idempotency 가 **opt-in** 이라
`runtime_state` 를 주지 않는 기본 실행 경로가 여전히 Task/Dispatch 를 중복 생성할 수 있다는
지적이다. durable idempotency 를 **모든 외부 효과 경로에서 필수**로 만들어 해소했다. 상세는
`## Review Feedback Resolution` 의 FR-F-001 절에 있다.

**iteration 3 (correction round):** iteration 2 의 guard 가 `invoke`/`stream`/`update_state`
만 덮고 `__getattr__` 이 나머지를 그대로 위임해, `batch`/`ainvoke` 등으로 우회 가능하다는
blocking finding(G2)을 받았다. 지시대로 **allow-by-default `__getattr__` 자체를
deny-by-default 로 전환**해 구조적으로 해소했다. 상세는 `## Review Feedback Resolution` 의
F-001 / G2 절에 있다.

수정 도중 기존 adapter parity 테스트가 내가 처음 택한 settlement identity 설계(타임스탬프를
event_id 에 bind)의 오류를 잡아냈다. 이는 deterministic replay 와 adapter parity 를 동시에
깨뜨리는 설계였으므로, event identity 를 canonical logical payload 만의 순수 함수로 바꾸었다.
자세한 근거는 M-003 절에 적었다.

## Analysis

### 재현으로 확인한 root cause (추정 아님)

| finding | 확인 방법 | 실제 관측 |
| --- | --- | --- |
| C-001 | `grep -rn RuntimeStatePort` | `ports.py:34` Protocol 정의 외 import/사용처 **0건**. `execute_intent_node` 가 `adapter.start(intent)` 를 직접 호출하고 receipt 는 `orca_adapter.py:19-20` 의 process-local dict 에만 존재 |
| M-001 | `wc -l tools/run_workflow.py` = 18줄 | version 확인 후 `print("...runtime ready")` 만 수행. `build_graph`/`invoke`/adapter 참조 없음 |
| M-002 | compiled graph 에 malformed state 투입 | `KeyError: 'current_phase'` / `KeyError: 'logical_trace'` — `executor.py:36` `route_node` → `_trace` 가 무조건 인덱싱 |
| M-003 | `validate_event` 코드 경로 | field 집합·verdict vocabulary 만 검사. `payload_digest`/`event_id` 재계산 없음 |
| M-004 | SKILL.md §8/§12/§13/§17 | graph 로 이전된 routing 이 여전히 독립 normative 규칙으로 존재 |

재현 스크립트 실측 (수정 전):

```text
missing logical_trace    -> KeyError: 'logical_trace'
unknown field            -> (LangGraph 가 조용히 drop -> validate_node 도달 못 함)
                            iteration 1 은 이를 launcher 진입점에서만 막았고,
                            iteration 2 에서 compiled graph 경계까지 막았다 (F-001)
invalid type             -> status=BLOCKED reason=MALFORMED_STATE   (유일하게 정상)
missing current_phase    -> KeyError: 'current_phase'
phase not in requested   -> validate_state: OK  | graph: KeyError: 'DESIGN'
index out of range       -> validate_state: OK  | graph: (검증 통과, 무결성 없이 진행)
```

---

## C-001 (CRITICAL) — Crash/restart 가 외부 Task/Dispatch 를 중복 생성

### Root cause

`RuntimeStatePort` 는 선언만 되어 있고 graph 실행 경로에 연결되지 않았다. idempotency receipt 가
adapter 인스턴스의 process-local dict 에만 살아 있으므로, 새 프로세스/새 adapter 로 재개하면
동일 stable intent 가 다시 `create_task` + `run_existing_task` 를 실행한다.

### 수정

1. **`deterministic_workflow/runtime_state.py` (신규)** — `RuntimeStatePort` 의 durable 구현.
   `FileRuntimeStateStore` 는 temp + `os.replace` 로 원자적 쓰기를 하고,
   `InMemoryRuntimeStateStore` 는 테스트용이다. record 상태는 `CLAIMED → EFFECTED → SETTLED`.
2. **intent-before-effect** — `execute_intent_node` 가 외부 효과 **이전에**
   `runtime_state.claim(intent)` 로 stable intent 를 영속 claim 한다.
3. **stable identity 로 복구** — 재시작 후 record 가 `SETTLED` 면 저장된 settlement 를 그대로
   반환하고 adapter 를 아예 호출하지 않는다. `CLAIMED`/`EFFECTED` 면 `adapter.settlement()` 로
   복구를 시도한다.
4. **crash window 는 fail closed** — 복구가 불가능하면(새 adapter 에 기억이 없음)
   `StateError("IDEMPOTENCY_RECOVERY_REQUIRED:<status>:<intent_id>")` 로 종료한다.
   **중복 생성 대신 정지**를 택했다. 리뷰가 금지한 것은 중복 side effect 이고, 엔진 전체가
   따르는 fail-closed 원칙과 일치한다.
5. **window 축소** — `OrcaAdapter` 가 `create_task` 직후 즉시 `record_receipt({"task_id": ...})`
   를 기록한다. 따라서 "외부 작업 완료 ↔ receipt 저장" 사이 구간이 실질적으로 사라진다.
6. **checkpoint-safe** — 영속 record 에는 durable 식별자(`task_id`, `dispatch_id`)만 넣는다.
   runtime handle 인 `terminal` 은 **의도적으로 저장하지 않으며**, store 가 `FORBIDDEN_KEYS`
   위반을 거부한다.

### 테스트 (사용자 요구: 같은 adapter 객체 재사용으로 완료 처리 금지)

`scripts/test_deterministic_workflow_recovery.py` 의 모든 복구 단언은 **file-backed store +
새 store 인스턴스 + 새 adapter 인스턴스**로 프로세스 경계를 흉내낸다:

- `test_fresh_adapter_recovers_settlement_without_a_second_effect` — 첫 프로세스가 effect 1건
  생성 후 사망, **새 store/새 adapter** 로 재개 → 첫 intent 는 store 에서 복구, 새 adapter 의
  `effect_count == 2`(미실행분만), workflow COMPLETED.
- `test_effect_completed_before_receipt_storage_is_never_recreated` — 외부 effect 생성 직후
  `KeyboardInterrupt` 로 사망 → **새 adapter** 재개 시 `IDEMPOTENCY_RECOVERY_REQUIRED`,
  `effect_count == 0`.
- `test_fresh_orca_adapter_does_not_recreate_task_or_dispatch` — 새 harness + 새 `OrcaAdapter`
  로 재개 시 `harness.calls == []`. 즉 **Task/Dispatch 재생성 0건**이고 동일 settlement 복구.
- `test_runtime_state_port_is_wired_into_the_graph_execution_path` — graph 실행이 실제로
  파일을 남기고 3 record 전부 `SETTLED` 임을 확인(포트가 정말 연결되었는지의 증거).

### Before Fix FAIL / After Fix PASS

```text
# Before  (evidence/C-001_M-003_before_fix.txt)
ERROR: test_runtime_state_port_is_wired_into_the_graph_execution_path
ERROR: test_fresh_adapter_recovers_settlement_without_a_second_effect
ERROR: test_effect_completed_before_receipt_storage_is_never_recreated
ERROR: test_fresh_orca_adapter_does_not_recreate_task_or_dispatch
ERROR: test_store_satisfies_the_runtime_state_port
Ran 13 tests -- FAILED (errors=13)

# After  (evidence/recovery_after_fix.txt)
Ran 15 tests in 0.057s
OK
```

변경 파일: `runtime_state.py`(신규), `ports.py`, `executor.py`, `graph.py`,
`fake_adapter.py`, `orca_adapter.py` (+ 설치본 mirror)

---

## M-001 (MAJOR) — launcher 가 workflow 를 실행하지 않음

### Root cause

`tools/run_workflow.py` 18줄 전체가 LangGraph import + 버전 확인 + 문자열 출력이었다.
state 구성, adapter 선택, `build_graph`, invoke/resume, terminal 결과, exit code 가 모두 없었다.

### 수정

- **`deterministic_workflow/launcher.py` (신규)** — 실제 실행 엔트리. `build_state`,
  `execute_state`, `default_recursion_limit`, `EXIT_CODES`, `run_cli`.
  mirror 되는 패키지 안에 두어 repo/설치본 양쪽에서 동일 코드가 쓰인다.
- **`tools/run_workflow.py`** — 얇은 CLI shim. `--check-runtime` 로 기존 probe 동작 보존.
- **recursion limit 명시 처리 + 문서화** — LangGraph 기본값은 25. settled intent 1건 = 5 step,
  phase advance 1건 = 2 step 이므로 canonical 5-phase 는 약 68 step 이 필요하다.
  `default_recursion_limit()` 이 요청 phase 수와 iteration budget 으로 계산하고,
  모든 엔트리가 명시적으로 설정한다. `--recursion-limit` 로 override 가능.
- **exit code**: `COMPLETED=0 / BLOCKED=1 / ESCALATED=2 / 입력·런타임 오류=3`.

### Orca 없이 실행 (실측)

```text
$ python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo --json
{"exit_code": 0, "final_review_iterations": 1,
 "phase_iterations": {"ANALYSIS":1,"DESIGN":1,"IMPLEMENTATION":1,"PLAN":1,"TEST":1},
 "requested_phases": ["ANALYSIS","PLAN","DESIGN","IMPLEMENTATION","TEST"],
 "run_id": "run_demo",
 "terminal_reason": {"code":"WORKFLOW_COMPLETED","message":"WORKFLOW_COMPLETED","phase":"TEST"},
 "terminal_status": "COMPLETED", "trace_length": 68,
 "workflow_id": "os40.standard.v1"}
exit=0
```

`trace_length: 68` 이 문서화한 step 수와 정확히 일치한다.

### recursion limit 회귀 증거

`test_default_limit_is_used_when_the_caller_supplies_none` 은 명시 설정이 없으면 canonical
workflow 가 `GraphRecursionError` 로 실패함을 **적극적으로 단언**한다 — 기본값 처리 누락이
재도입되면 이 테스트가 깨진다.

### Before Fix FAIL / After Fix PASS

```text
# Before  (evidence/M-001_before_fix.txt)
FAIL:  test_launcher_exposes_an_execution_entry_point
ERROR: test_canonical_five_phase_workflow_completes_with_default_limit
ERROR: test_demo_scenario_runs_without_orca_and_exits_zero
ERROR: test_default_recursion_limit_exceeds_the_langgraph_default
ERROR: test_terminal_exit_codes_cover_every_terminal_status
ERROR: test_cli_runs_a_supplied_state_and_result_script
ERROR: test_blocked_and_escalated_runs_return_distinct_nonzero_exit_codes (x2)
FAIL:  test_malformed_input_state_exits_blocked_without_traceback
Ran 10 tests -- FAILED (failures=2, errors=7)

# After  (evidence/launcher_after_fix.txt)
Ran 10 tests in 2.180s
OK
```

변경 파일: `launcher.py`(신규), `tools/run_workflow.py`, `INSTALL.md` (+ mirror)

---

## M-002 (MAJOR) — malformed state 가 fail-closed 하지 않고 raise

### Root cause

`executor.py:24-27` `validate_node` 의 실패 경로가 `{**state, "route_token": "BLOCK", ...}` 로
**원본 malformed dict 를 그대로** 반환했다. 이어지는 `route_node` 의 `_trace` 가
`logical_trace` / `current_phase` / `phase_iterations` 를 무조건 인덱싱해 `KeyError` 가 났다.

추가로 확인한 두 가지 (리뷰에 명시되지 않았으나 같은 root cause 의 일부):

- `validate_state` 가 container **타입**을 검사하지 않아 `logical_trace: "문자열"` 같은 입력이
  통과했다.
- `current_phase` 가 `requested_phases` 에 없거나 `current_phase_index` 가 범위를 벗어나도
  통과해서, 이후 `phase_iterations[current_phase]` 에서 `KeyError` 가 났다.

### 수정

1. **정규화** — `state.normalize_malformed_state()` 를 추가했다. 임의 입력을 닫힌 WorkflowState
   키 집합 위로 투영해 **유효한** state 를 만들고 BLOCKED terminal 경로에 bind 한다.
   자체적으로 검증을 통과하는 identity 필드(run_id, phases, risk 등)만 보존한다.
   `validate_node` 는 이제 이것을 반환한다.
2. **2차 방어** — `_trace` 를 전부 `.get()` 기반으로 바꿔, 어떤 형태의 state 가 와도
   trace append 가 계약된 BLOCKED 를 KeyError 로 바꾸지 못하게 했다.
3. **검증 강화** — `validate_state` 에 list/dict/int/str 타입 검사, `round_kind` 어휘 검사,
   phase/index 정합성 검사를 추가했다. 정합성 규칙은 정확히 다음과 같다:
   - `0 <= current_phase_index < len(phases)` (항상)
   - `current_phase in phases` (항상)
   - `current_phase == phases[index]` — **`round_kind == "PHASE_GATE"` 일 때만**.
     CORRECTION / DOWNSTREAM_REVALIDATION round 는 index 와 다른 phase 를 정당하게 가리키므로
     이 규칙을 적용하면 안 된다.
4. **unknown field** — LangGraph 는 선언되지 않은 입력 채널을 **조용히 버린다**(실측 확인).
   따라서 unknown field 는 `validate_node` 에 도달조차 하지 않는다. 닫힌 field 집합은
   graph **내부**에서 강제할 수 없으므로 invocation 경계에서 검사해야 한다.
   **두 경계 모두** fail closed 다:
   - `graph.GuardedWorkflowGraph` — `build_graph` 가 돌려주는 compiled graph 경계.
     `invoke`/`stream` 이 unknown key 를 발견하면 graph 를 실행하지 않고
     BLOCKED/MALFORMED_STATE terminal 을 반환하며, `update_state` 는 `StateError` 를 낸다.
   - `launcher.execute_state` — 프로세스 진입점. raw mapping 을 검증해 정확한 `StateError`
     사유를 보고한다.
   LangGraph 의 drop 동작 자체는 `test_raw_langgraph_still_drops_unknown_channels` 가
   **guard 를 우회한** `.compiled` 에 대해 고정하므로, guard 가 그 사실을 가리지 못한다.

### 테스트 (compiled graph 경유)

`scripts/test_deterministic_workflow_malformed.py` — missing field 6종, invalid type 5종,
phase/index/budget 잘못된 조합 6종을 **compiled graph invoke** 로 덮는다. 모두
`BLOCKED` + `MALFORMED_STATE` + `effect_count == 0`. 추가로
`test_terminal_state_of_malformed_entry_is_itself_valid` 가 결과 terminal state 자체가
`validate_state` 를 통과함을 확인한다(정규화가 실제로 유효한 state 를 만들었다는 증거).

### Before Fix FAIL / After Fix PASS

```text
# Before  (evidence/M-002_before_fix.txt)
KeyError: 'current_phase'   <- route_node -> _trace, executor.py:14
ERROR: test_missing_required_fields_block_without_keyerror (missing='logical_trace')
ERROR: test_missing_required_fields_block_without_keyerror (missing='current_phase')
ERROR: test_incoherent_phase_index_and_budget_combinations_block (case='phase_outside_request')
ERROR: test_incoherent_phase_index_and_budget_combinations_block (case='index_out_of_range')
... Ran 6 tests -- FAILED (errors=13)

# After  (evidence/malformed_after_fix.txt)
Ran 6 tests in 0.053s
OK
```

변경 파일: `state.py`, `executor.py`, `launcher.py` (+ mirror)

---

## M-003 (MAJOR) — settlement identity/digest 무결성 미검증

### Root cause

`contracts.validate_event` 가 field 집합과 verdict 어휘만 확인하고 `payload_digest` / `event_id`
를 재계산하지 않았다. checkpoint 된 settlement 의 `result` 를 FAIL→PASS 로 바꾸고 ID/digest 를
그대로 두면 권위 있는 결과로 적용되었다.

### 수정

1. **canonical settlement payload 정의** — `settlement_payload(intent, result)` 가 적용된
   settlement 가 영향을 줄 수 있는 모든 필드를 bind 한다: schema/kind/outcome,
   `intent_id`, `command_id`, `role`, intent 의 `payload_digest`, 그리고 `result`.
2. **적용 전 재계산 검증** — `validate_event` 가 digest 와 event ID 를 재계산해
   `hmac.compare_digest` 로 비교하고, event 의 intent/command binding 을 intent 와 대조한다.
   불일치 시 `EventValidationError("SETTLEMENT_INTEGRITY", ...)`.
3. **거부된 settlement 는 절대 적용 안 됨** — `EVENT_REJECTION_CODES` 를 도입하고
   `apply_result_node` 가 이 집합을 사용하도록 바꿨다. 새 코드를 추가하면서 이 집합을
   갱신하지 않으면 변조된 결과가 적용되는 구멍이 생기는데, 그 구멍을 닫았다.
4. `make_settlement_event()` 를 두 adapter 가 공용으로 쓰게 해서, 유효한 event 를 만드는
   경로가 하나만 존재하게 했다.

### 설계 정정 — event identity 는 clock 에 의존하면 안 된다

처음에는 `occurred_at` 을 `event_id` 에 bind 했다. 기존 adapter parity 테스트가 이를 즉시
잡아냈다(fake 는 `2026-01-01T...`, Orca 는 `1970-01-01T...`). 이것은 두 가지를 동시에 깬다:

- **deterministic replay** — 재시작한 프로세스가 같은 settlement 를 재도출해도 event_id 가 달라진다.
- **adapter parity** — 시계가 다른 adapter 사이의 logical trace 가 갈라진다.

둘 다 리뷰가 명시적으로 요구한 성질이므로, event identity 를 **canonical logical payload 만의
순수 함수**로 바꿨다. `occurred_at` 은 형식(ISO-8601)만 검증하고 identity 에서 제외한다 —
어떤 gate 도 읽지 않으므로 적용되는 결정에 영향을 줄 수 없다. 이 트레이드오프는
`settlement_event_id` docstring 에 근거와 함께 기록했다.

### mutation-sensitive 테스트 (FAIL → PASS 포함)

- `test_fail_to_pass_mutation_is_rejected` — reviewer 결과를 FAIL→PASS 로 변조 → `SETTLEMENT_INTEGRITY`.
- `test_digest_event_id_and_binding_mutations_are_rejected` — `payload_digest`, `event_id`,
  intent binding, command binding 각각 변조 → 전부 `SETTLEMENT_INTEGRITY`.
- `test_checkpointed_fail_to_pass_mutation_blocks_before_apply` — **compiled graph + checkpointer**
  로 실제 시나리오 재현: 저장된 reviewer settlement 의 result 만 FAIL→PASS 로 바꾸고 ID/digest 를
  유지 → `BLOCKED` / `SETTLEMENT_INTEGRITY`, `phase_passes["ANALYSIS"] is None`,
  `reviewer_result.result != "PASS"`. 즉 변조가 **적용 전에** 차단된다.
- `test_event_identity_is_reproducible_and_clock_independent` — 위 설계 정정의 회귀 방지.
- `test_role_bound_payload_differs_across_roles` — role binding 이 digest 에 실제로 반영됨.

### Before Fix FAIL / After Fix PASS

```text
# Before  (evidence/C-001_M-003_before_fix.txt)
ERROR: test_canonical_event_validates
ERROR: test_fail_to_pass_mutation_is_rejected
ERROR: test_digest_event_id_and_binding_mutations_are_rejected
ERROR: test_checkpointed_fail_to_pass_mutation_blocks_before_apply
Ran 13 tests -- FAILED (errors=13)

# After  (evidence/recovery_after_fix.txt)
Ran 15 tests in 0.057s
OK
```

변경 파일: `contracts.py`, `executor.py`, `fake_adapter.py`, `orca_adapter.py` (+ mirror)

---

## M-004 (MAJOR) — prompt-owned routing 이 병행 control plane 을 만듦

### 판단 근거와 경계 (사용자가 명시적으로 요구한 항목)

리뷰 문언은 "remove **or** clearly demote duplicated normative routing rules to
generated/non-authoritative documentation, with a validator preventing reintroduction" 이다.
**강등(demote)** 을 택했다. 이유:

1. 과제 지시가 "지나치게 넓게 잘라내 안전 규칙까지 지우면 그것 자체가 G4/G1 위반" 이라고
   명시했다. §12/§13/§17 산문에는 routing 규칙과 안전/운영 규칙이 섞여 있어, 절 단위 삭제는
   안전 규칙 손실 위험이 크다.
2. 강등은 리뷰가 허용한 두 선택지 중 하나이며, validator 로 강제하면 삭제와 동등한
   "단일 권위" 효과를 얻으면서 사람이 읽는 운영 문서를 보존한다.

**graph 로 이전된 normative routing (강등 대상)** — engine 의 route token 이 실제로 이 결정을
내리기 때문에 선정했다:

| decision | route tokens | 강등된 절 |
| --- | --- | --- |
| PHASE_TRANSITION | `ADVANCE_PHASE` | `## 8. Phase Sequence Contract` |
| PHASE_GATE | `PREPARE_WORKER`, `PREPARE_PHASE_REVIEWER`, `BLOCK` | `### Risk Axis` |
| CORRECTION_LOOP | `PREPARE_CORRECTION` | `## 12. FAIL Loop` |
| ITERATION_BUDGET | `ESCALATE` | `## 13. Iteration` |
| FINAL_REVIEW_ROUTING | `PREPARE_FINAL_REVIEWER`, `PREPARE_REVALIDATION`, `COMPLETE` | `## 17. Final Adversarial Review` |

9개 route token **전부**가 정확히 하나의 decision 에 귀속된다(validator 가 강제).

**보존한 안전/지침 (강등 금지 대상)** — graph 가 소유하지 않는 것들이다. engine 은 route token 과
terminal status 만 결정하며, 아래 어느 것도 결정하지 않는다:

| 보존 절 | 왜 graph 소유가 아닌가 |
| --- | --- |
| `## 5. Agent Policy` | agent 선택·행동 규범. graph 는 role 만 알고 agent 를 모른다 |
| `## Decision Policy` | OS-28/29 decision gate 의 인간 권한 경계 |
| `### Completed Worker Lifecycle` | terminal/lifecycle ownership. runtime 소유권 규칙 |
| `## 14. Mandatory Test Gates` | Worker 산출물의 내용 요건 |
| `## 15. Repository / Security Policy` | 금지 작업. 안전 규칙 |
| `## Structured Human Clarification (OS-30)` | 인간 개입 프로토콜 |

### 수정

1. **`## Workflow Control Plane Authority` 절 신설** — engine 이 5개 결정을 **단독 소유**하고
   Coordinator 는 독립 재판정하지 않는다는 delegation clause 를 명시.
2. **`workflow-control-plane` 기계 판독 block** — graph-owned decision(+route token +절) 과
   skill-owned safety 절을 선언.
3. **강등 표시** — 5개 graph-owned 절 머리에 `NON-AUTHORITATIVE (graph-owned)` marker 를 넣어,
   해당 산문이 engine 동작의 파생 문서이며 불일치 시 engine 이 정답임을 명시.
4. **`graph_spec.GRAPH_OWNED_DECISIONS`** — engine 이 decision 목록의 source of truth.
5. **자동 drift 검출** — `validate_workflow_graph_docs.validate_control_plane()` 이
   `validate_skills.py`(729 checks) 경유로 매 CI 실행마다 돈다.

### validator 가 잡는 drift (양방향)

- graph-owned 절에서 강등 marker 제거 → `graph-owned section is not demoted`
- engine 의 decision 집합과 문서 선언 불일치 → `graph-owned decision set drifted from the engine`
- route token 이 어떤 decision 에도 귀속되지 않음 → `every route token must be owned...`
- **안전 절 삭제** → `preserved safety section is missing`
- **안전 절을 강등** → `safety section must stay authoritative`
- delegation clause 제거 / control-plane block 중복 → 각각 검출

즉 재도입(routing 승격)과 과잉 삭제(안전 규칙 손실)를 **둘 다** 막는다.

구현 중 validator 자체의 결함도 잡아 고쳤다: 처음 구현은 절 본문을 다음 `## ` 까지로 잡아서
**하위 절의 marker 가 상위 절의 검사를 충족**시켰다(§8 의 marker 를 지워도 `### Risk Axis` 의
marker 때문에 통과). `_section_body` 를 "임의 레벨의 다음 heading 까지 = 그 절 **자신의** 산문"
으로 바꿔 해결했고, `test_removing_a_non_authoritative_marker_is_detected` 가 이를 고정한다.

### Before Fix FAIL / After Fix PASS

```text
# Before  (evidence/M-004_before_fix.txt)
ImportError: cannot import name 'GRAPH_OWNED_DECISIONS' from
  'scripts.deterministic_workflow.graph_spec'
Ran 1 test -- FAILED (errors=1)

# After  (evidence/control_plane_after_fix.txt)
Ran 12 tests in 0.006s
OK
```

변경 파일: `SKILL.md`, `graph_spec.py`, `validate_workflow_graph_docs.py` (+ mirror)

---

## Changes

- 신규 production 모듈 2개: `runtime_state.py`(durable idempotency ledger), `launcher.py`(실행 엔트리)
- 신규 test 모듈 4개, 총 **+67 tests** (1761 → 1828)
- iteration 2: `graph.GuardedWorkflowGraph` 추가(compiled graph 경계 guard), malformed
  test 모듈에 unknown-only compiled-graph 회귀 5종 추가
- iteration 3: 그 façade 를 deny-by-default 로 전환하고 ingress surface 회귀 11종 추가
- iteration 4: durable idempotency 를 모든 외부 효과 경로에서 필수화(port 없는 모드 삭제),
  launcher 기본 ledger 도입, default-path 회귀 9종 추가
- iteration 5: compiled graph 를 façade 에서 제거(`.compiled` public handle 삭제),
  pin 테스트를 test-owned raw graph 로 재구성, unwrap 불가 회귀 3종 추가
- `tools/run_workflow.py` 를 실제 실행 CLI 로 교체(기존 `--check-runtime` probe 동작 보존)
- 무관한 refactoring 이나 동작 변경은 넣지 않았다. 유일한 계약 변경은 M-003 이 요구한
  settlement identity 계산 방식이며, adapter 두 곳과 validator 한 곳에 국한된다.

## Modified Files / Artifacts

### 신규 (production)
- `scripts/deterministic_workflow/runtime_state.py`
- `scripts/deterministic_workflow/launcher.py`
- `orca-worker-reviewer-orchestration/tools/deterministic_workflow/runtime_state.py` (mirror)
- `orca-worker-reviewer-orchestration/tools/deterministic_workflow/launcher.py` (mirror)

### 신규 (test)
- `scripts/test_deterministic_workflow_recovery.py`   (C-001, M-003)
- `scripts/test_deterministic_workflow_malformed.py`  (M-002)
- `scripts/test_deterministic_workflow_launcher.py`   (M-001)
- `scripts/test_workflow_control_plane.py`            (M-004)

### 수정 (production)
- `scripts/deterministic_workflow/contracts.py`      (+ mirror)
- `scripts/deterministic_workflow/executor.py`       (+ mirror)
- `scripts/deterministic_workflow/state.py`          (+ mirror)
- `scripts/deterministic_workflow/ports.py`          (+ mirror)
- `scripts/deterministic_workflow/graph.py`          (+ mirror)
- `scripts/deterministic_workflow/graph_spec.py`     (+ mirror)
- `scripts/deterministic_workflow/fake_adapter.py`   (+ mirror)
- `scripts/deterministic_workflow/orca_adapter.py`   (+ mirror)
- `scripts/validate_workflow_graph_docs.py`
- `orca-worker-reviewer-orchestration/tools/run_workflow.py`

### 수정 (문서)
- `orca-worker-reviewer-orchestration/SKILL.md`
- `INSTALL.md`

### Artifacts
- `artifacts/runs/run_9e3c67a7824e/BUGFIX.md` (이 파일)
- `artifacts/runs/run_9e3c67a7824e/evidence/*.txt` (Before/After 실측 출력)

과거 run artifact 는 읽지도 쓰지도 않았다. `artifacts/runs/run_0bcf4e7296c9/` 는 미변경이다.

## Validation

### full unit tests
```text
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1831 tests in 328.578s

OK (skipped=6)
```
baseline 1761 / OK / skipped=6 → **1831 / OK / skipped=6**. 회귀 0.

### 신규/수정 test module 개별 실행
```text
scripts.test_deterministic_workflow_malformed    Ran 24 tests  OK
scripts.test_deterministic_workflow_recovery     Ran 24 tests  OK
scripts.test_deterministic_workflow_launcher     Ran 10 tests  OK
scripts.test_workflow_control_plane              Ran 12 tests  OK
```

### dependency-absent lane (import 차단 방식)
`sys.meta_path` MetaPathFinder 로 `langgraph` import 를 차단하고 engine test 7개 모듈 실행:
```text
Ran 106 tests in 0.497s

OK (skipped=66)
LANE errors=0 failures=0 skipped=66
```
**errors=0.** guard 는 import 기반이며 metadata 만으로 통과하지 않는다.

### Skill validation
```text
$ python3 scripts/validate_skills.py
Skill validation PASSED (729 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
```
(727 → 729; control-plane drift 검출이 이 경로로 편입되었다)

### package / archive verification
```text
$ python3 scripts/verify_package.py
Package verification PASSED (234 source files)

$ python3 scripts/build_release.py && \
  python3 scripts/verify_package.py --archive dist/orca-skills-0.9.0.tar.gz
Package verification PASSED (234 source files)
Verified archive: dist/orca-skills-0.9.0.tar.gz
```
(226 → 234; 신규 engine 모듈 2개 × source/mirror + 신규 test 4개)

### graph-doc validator
```text
$ python3 scripts/validate_workflow_graph_docs.py
Workflow graph documentation validation PASSED
```

### source ↔ installed mirror byte parity
```text
$ diff -r --exclude=__pycache__ scripts/deterministic_workflow \
       orca-worker-reviewer-orchestration/tools/deterministic_workflow
(차이 없음 — byte-identical)

$ diff scripts/run_logging.py .../tools/run_logging.py                 -> identical
$ diff scripts/clarification_protocol.py .../tools/clarification_protocol.py -> identical
```

### source-installed parity (설치본을 복사해 독립 실행)
```text
$ cp -R orca-worker-reviewer-orchestration <tmp>/ && cd <tmp>/orca-worker-reviewer-orchestration
$ python3 tools/run_workflow.py --demo --json
{... "terminal_status": "COMPLETED", "trace_length": 68, "exit_code": 0}
exit=0
$ python3 tools/run_workflow.py --check-runtime
deterministic workflow runtime ready (langgraph 0.2.76)
```
설치본이 repo 의 `scripts/` 없이 단독으로 workflow 를 끝까지 실행한다.

### fake adapter ↔ Orca adapter logical trace parity
```text
$ python3 -m unittest scripts.test_deterministic_workflow_adapters.LangGraphAdapterParityTests
Ran 1 test in 0.021s

OK
```
이 테스트는 이번 작업 중 실제로 설계 오류를 잡아냈다(M-003 절 참조). 현재는 두 adapter 의
normalize_trace 결과가 완전히 일치한다.

### whitespace / working tree
```text
$ git diff --check
(출력 없음 — clean)
```

## Unit Tests / Testing Strategy

각 finding 마다 **수정 전 코드에서 실패하는 테스트를 먼저 작성**했고 Before/After 출력을
`evidence/` 에 보관했다. 테스트가 "같은 in-memory 객체" 나 "구현 자체의 상수" 만 확인하지
않도록 다음을 의도적으로 설계했다:

- **동일 객체 재사용 배제 (C-001)** — 모든 복구 단언이 새 store 인스턴스 + 새 adapter
  인스턴스를 쓰고, 상태는 실제 파일을 통해서만 전달된다. `assertIsNot(first, second)` 로
  객체가 다름을 명시 확인한다.
- **compiled graph 경유 (M-002, M-003)** — 노드 함수 단독 호출이 아니라 실제 컴파일된 graph 를
  invoke 하고, checkpointer 로 저장된 상태를 변조한 뒤 재개하는 실제 시나리오를 쓴다.
- **외부 프로세스 경유 (M-001)** — launcher 는 `subprocess` 로 실제 CLI 를 실행해 stdout 과
  exit code 를 검증한다. 구현 상수를 읽지 않는다.
- **역방향 단언 (M-001, M-004)** — "고치지 않으면 깨진다" 를 능동적으로 증명한다:
  recursion limit 미설정 시 `GraphRecursionError` 발생을 단언하고, validator 는 marker 제거 /
  안전 절 삭제 / 안전 절 강등 / decision 집합 drift 각각에 대해 예외 발생을 단언한다.
- **외부 라이브러리 동작 고정 (M-002)** — LangGraph 가 unknown 입력 채널을 버리는 동작을
  테스트로 고정해, 그 전제가 바뀌면 감지되게 했다.

## Review Feedback Resolution

| finding | 상태 | 근거 |
| --- | --- | --- |
| C-001 | RESOLVED | RuntimeStatePort 를 graph 실행 경로에 연결, intent-before-effect claim, 새 adapter 로 stable identity 복구, crash window 는 fail closed. 새 adapter 인스턴스 기반 테스트 4종 |
| M-001 | RESOLVED | 실제 실행 CLI, fake adapter 로 Orca 없이 canonical workflow 완주(68 step, exit 0), recursion limit 명시 처리 + INSTALL.md 문서화 |
| M-002 | RESOLVED | malformed 입력을 유효 state 로 정규화 후 BLOCKED/MALFORMED_STATE, compiled graph 경유 17 케이스 |
| M-003 | RESOLVED | canonical payload 정의, 적용 전 digest/event ID 재계산, FAIL→PASS 포함 mutation 테스트, 거부된 event 는 적용 경로에서 차단 |
| M-004 | RESOLVED | delegation clause + control-plane 선언 + 5개 절 강등 + 양방향 drift validator(validate_skills 편입). 안전 절 6개는 보존되고 강등 금지가 강제됨 |
| F-001 | RESOLVED | compiled graph 경계에 `GuardedWorkflowGraph` 도입 (iteration 2). 아래 참조 |
| G2 (guard API bypass) | RESOLVED | 그 façade 를 deny-by-default 로 전환 (iteration 3). 아래 참조 |
| FR-F-001 (Final Review) | RESOLVED | durable idempotency 를 기본 경로에서 필수화 (iteration 4). 아래 참조 |
| FR2-M-002 (Final Review) | RESOLVED | `.compiled` public handle 제거 (iteration 5). 아래 참조 |

### F-001 (iteration 2, phase Reviewer blocking) — unknown field 가 compiled graph 에서 fail closed 되지 않음

**Reviewer 지적의 정당성**: 그대로 유효하다. iteration 1 의 unknown-field 단언은
`launcher.execute_state` 를 테스트했고, compiled-graph 짝
(`test_compiled_graph_drops_unknown_input_channels`)은 unknown field 를
`requested_phases="bad"` 와 **함께** 넣어 known-field 오류가 block 을 일으켰다. 즉
unknown-field 동작 자체는 검증되지 않았다. 독립 재현으로 확인했다.

**Root cause**: LangGraph `StateGraph` 는 입력 mapping 을 선언된 channel 로 필터링한다.
unknown key 는 어떤 node 에도 도달하지 않으므로 `validate_state` 가 볼 기회가 없다.
따라서 닫힌 field 집합은 graph **내부**에서는 원리적으로 강제 불가능하며, invocation
경계에서 검사해야 한다.

**수정**: `graph.GuardedWorkflowGraph` — `build_graph` 가 반환하는 compiled graph façade.

- `invoke` / `stream`: 입력 mapping 에 closed field set 밖의 key 가 있으면 graph 를
  **실행하지 않고** `BLOCKED` / `MALFORMED_STATE` terminal state 를 반환한다(effect 0건).
  mapping 이 아닌 입력도 같은 방식으로 거부한다.
- `update_state`: unknown key 에 `StateError` 를 낸다(상태 변경 API 이므로 반환값이 아니라 예외).
- `invoke(None, config)` — **checkpoint resume 경로는 그대로 통과**시킨다. resume 은 검사할
  입력 mapping 이 없고, 저장된 state 는 이미 진입 시 검증되었다.
- `__getattr__` 로 나머지 compiled graph API 를 위임하고, 원본은 `.compiled` 로 노출한다.

`build_graph` 가 돌려주는 유일한 객체가 이 façade 이므로 **우회 가능한 unguarded compiled
graph 가 존재하지 않는다.** launcher 의 기존 guard 는 지시대로 제거하지 않았다 — 두 경계가
독립적으로 fail closed 다.

**unknown-only 실측 (지시된 before/after)**:

```text
# BEFORE  (evidence/F-001_before_fix_reproduction.txt)
=== F-001 unknown-ONLY field submitted directly to the compiled graph (BEFORE FIX) ===
terminal_status : COMPLETED   (expected BLOCKED)
reason          : WORKFLOW_COMPLETED   (expected MALFORMED_STATE)
effect_count    : 3   (expected 0)
unknown present : False   (LangGraph silently dropped it)

# AFTER  (evidence/F-001_after_fix_reproduction.txt)
=== F-001 unknown-ONLY field submitted directly to the compiled graph (AFTER FIX) ===
terminal_status : BLOCKED   (expected BLOCKED)
reason          : MALFORMED_STATE   (expected MALFORMED_STATE)
effect_count    : 0   (expected 0)
unknown present : False
```

Coordinator/Reviewer 가 보고한 수치(COMPLETED / 3 effects)와 before 값이 정확히 일치한다.

**추가한 회귀 테스트** (`scripts/test_deterministic_workflow_malformed.py`):

- `test_unknown_only_field_blocks_at_the_compiled_graph_entry` — 다른 모든 field 가 유효한
  상태에서 unknown key **하나만** 넣는다. known-field 오류가 block 을 대신 일으킬 수 없다.
  `BLOCKED` / `MALFORMED_STATE` / `effect_count == 0` 을 assert 한다.
- `test_unknown_field_matrix_blocks_at_the_compiled_graph_entry` — scalar / nested dict /
  기존 key 를 닮은 이름 / 복수 unknown 4종.
- `test_unknown_field_is_rejected_by_stream_and_update_state` — `invoke` 외의 진입 경로도 막힘.
- `test_raw_langgraph_still_drops_unknown_channels` — **정정된 pin 테스트**. 지시대로 삭제하지
  않고 현실에 맞게 고쳤다: 이제 guard 를 우회한 `.compiled` 에 대해 LangGraph 의 drop 동작을
  고정하므로, guard 자신의 동작이 이 사실을 가리지 못한다.
- `test_valid_state_is_not_misread_as_malformed` — guard 가 정상 경로와 **checkpoint resume** 을
  malformed 로 오판하지 않음을 확인한다(지시된 확인 항목).

**resume 경로 무결성**: C-001 의 fresh-adapter 복구 테스트를 함께 돌렸다 —
`scripts.test_deterministic_workflow_recovery` Ran 15 tests OK
(`evidence/F-001_recovery_after_fix.txt`). launcher 정상 경로도 무회귀
(`--demo --json` → COMPLETED / trace_length 68 / exit 0).

**mirror**: `scripts/deterministic_workflow/graph.py` 와 `launcher.py` 를 고쳤고 설치본
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/` 를 함께 갱신했다.
`diff -r` 결과 byte-identical 이며, 설치본 복사본에서 unknown-only 입력이
`BLOCKED / MALFORMED_STATE / effects 0` 으로 막히는 것을 독립 실행으로 확인했다.

### G2 (iteration 3, phase Reviewer blocking) — guard API bypass: batch / ainvoke 가 무방비 위임

**Reviewer 지적의 정당성**: 그대로 유효하다. iteration 2 의 façade 는 `invoke`/`stream`/
`update_state` 세 개만 override 하고 `__getattr__` 이 나머지를 compiled graph 로 그대로
위임했다. 독립 재현 결과 `invoke` 만 막히고 나머지는 전부 통과했다.

**왜 batch/ainvoke 두 개만 추가하는 것으로는 부족한가**: 그것은 iteration 2 와 정확히 같은
실패다. 열거된 구멍만 막으면 `abatch`/`astream`/`transform`/`astream_events`, 그리고 앞으로
LangGraph 가 추가할 어떤 ingress API 도 같은 구멍을 다시 연다. 근본 원인은 개별 method 가
아니라 **`__getattr__` 이 allow-by-default 라는 것**이다. 따라서 지시대로 위임 정책 자체를
뒤집었다.

**ingress 목록을 도출한 방법 (추정 아님)**: 설치된 langgraph 0.2.76 의 compiled graph
(`CompiledStateGraph` → `CompiledGraph` → `Pregel` → `PregelProtocol`)를 `dir()` + 
`inspect.signature()` 로 열거해, **graph state 를 받는 public callable** — 즉 `input` /
`inputs` / `values` 라는 parameter 를 가진 것 — 을 뽑았다. 실측 결과 14개다:

```text
invoke   ainvoke   stream   astream   batch   abatch   update_state   aupdate_state
batch_as_completed   abatch_as_completed   transform   atransform
astream_events   astream_log
```

**수정**: `GuardedWorkflowGraph` 를 deny-by-default 로 전환했다.

- `GUARDED_INGRESS` (8개) — façade 가 **자기 구현으로 override** 한다:
  `invoke`, `ainvoke`, `stream`, `astream`, `batch`, `abatch`, `update_state`, `aupdate_state`.
- 나머지 ingress 6개(`batch_as_completed`, `abatch_as_completed`, `transform`, `atransform`,
  `astream_events`, `astream_log`)는 **노출되지 않는다** — `__getattr__` 이 거부한다.
  쓰이지 않는 API 를 굳이 구현하는 것보다 노출하지 않는 쪽이 더 좁고 안전하다.
- `batch`/`abatch` 는 malformed 항목만 거부하고 나머지는 native batch 로 넘겨,
  정상 항목을 과잉 차단하지 않으면서 fail closed 한다.
- `__getattr__` 은 `READ_ONLY_PASSTHROUGH` 에 있는 이름만 위임하고 그 밖의 모든 이름에
  `AttributeError` 를 낸다. 따라서 `bind`/`pipe`/`map`/`assign`/`pick`/`with_config`/
  `with_retry`/`with_fallbacks`/`as_tool`/`copy`/`validate`/`builder` 처럼
  **unguarded runnable 을 돌려주는 composition API 도 전부 막힌다.** 이것들은
  parameter 이름 heuristic 에는 안 걸리지만 실질적 우회 경로이며, deny-by-default 이기에
  따로 열거하지 않아도 자동으로 막힌다.
- resume 경로(`invoke(None, config)` 와 `ainvoke(None, config)`)는 계속 통과한다.
- launcher 의 기존 guard 는 지시대로 그대로 두었다.

**`READ_ONLY_PASSTHROUGH` 에 남긴 것과 그 근거** — 어느 것도 graph state 를 입력으로 받지
않으며, unguarded runnable 을 돌려주지도 않는다:

| 이름 | 왜 state ingress 가 아닌가 |
| --- | --- |
| `get_state`, `aget_state`, `get_state_history`, `aget_state_history` | `config` 만 받아 **이미 저장된** checkpoint snapshot 을 읽는다. 쓰기 경로가 없다 |
| `get_graph`, `aget_graph`, `get_subgraphs`, `aget_subgraphs` | topology 조회. 실행하지 않는다 |
| `get_input_schema`, `get_output_schema`, `get_input_jsonschema`, `get_output_jsonschema`, `config_schema`, `get_config_jsonschema` | schema 조회 |
| `get_name`, `name` | 식별자 |
| `config_specs`, `checkpointer`, `input_channels`, `output_channels`, `stream_channels`, `stream_mode` | read-only metadata |

`builder` 는 **의도적으로 제외**했다 — StateGraph 를 돌려주므로 재compile 로 guard 없는
graph 를 만들 수 있다. `validate` 도 제외했다: `Self`(= raw compiled graph)를 반환한다.
`.compiled` 는 남겼지만 문서화된 test 전용 handle 이며, LangGraph 의 drop 동작을 pin 하는
테스트가 guard 에 가려지지 않게 하는 용도다(클래스 docstring 에 명시).

**batch / ainvoke 실측 (지시된 before/after)**:

```text
# BEFORE (Coordinator 재현과 동일)
invoke   -> BLOCKED   / MALFORMED_STATE    / effects=0
batch    -> COMPLETED / WORKFLOW_COMPLETED / effects=3     <-- 우회
ainvoke  -> COMPLETED / WORKFLOW_COMPLETED / effects=3     <-- 우회
abatch   -> COMPLETED / WORKFLOW_COMPLETED / effects=3     <-- 우회 (추가 발견)
astream  -> COMPLETED / WORKFLOW_COMPLETED / effects=3     <-- 우회 (추가 발견)

# AFTER (evidence/G2_ingress_after_fix_reproduction.txt)
invoke   -> BLOCKED / MALFORMED_STATE / effects=0
batch    -> BLOCKED / MALFORMED_STATE / effects=0
ainvoke  -> BLOCKED / MALFORMED_STATE / effects=0
abatch   -> BLOCKED / MALFORMED_STATE / effects=0
stream   -> BLOCKED / MALFORMED_STATE / effects=0
astream  -> BLOCKED / MALFORMED_STATE / effects=0

# 이전에 우회 가능하던 나머지 표면은 아예 도달 불가
batch_as_completed   denied      transform        denied      bind        denied
abatch_as_completed  denied      atransform       denied      pipe        denied
astream_events       denied      astream_log      denied      with_config denied
copy                 denied      builder          denied
```

Reviewer/Coordinator 가 보고한 before 수치(COMPLETED / 3 effects)와 정확히 일치하며,
`abatch` / `astream` 도 같은 방식으로 우회 가능했음을 추가로 확인했다.

**재발 방지 테스트의 형태** (이 finding 계열을 구조적으로 막는 부분):

- `test_no_state_ingress_api_is_reachable_unguarded` — **불변식 자체를 assert 한다.**
  compiled graph 에서 state ingress 이름을 signature 로 동적으로 뽑아, 각각에 대해
  (a) façade 가 자기 override 를 갖고 있거나 (b) 접근 시 `AttributeError` 여야 한다고 단언한다.
  하드코딩된 목록을 비교하는 것이 아니므로, LangGraph 가 새 ingress API 를 추가해도
  deny-by-default 때문에 자동으로 (b) 를 만족한다. 반대로 누군가 `__getattr__` 을 다시
  allow-by-default 로 되돌리면 이 테스트가 **그 즉시 실패한다.**
- `test_declared_guard_list_matches_the_installed_runtime` — `GUARDED_INGRESS` 에 런타임에
  존재하지 않는 이름이 남아 있으면 실패(stale 목록 방지).
- `test_readonly_allowlist_contains_no_state_ingress` — allowlist 의 어떤 이름도 ingress
  signature 를 갖지 않으며 실제로 존재함을 확인.
- `test_composition_apis_that_would_unwrap_the_guard_are_denied` — `bind`/`pipe`/`builder` 등
  12개가 거부됨을 확인.
- ingress 별 unknown-only 차단(sync/async), 정상 state 정상 동작(과잉 차단 방지),
  `batch` 혼합 입력에서 malformed 만 차단, sync/async resume 통과.

**mirror**: `scripts/deterministic_workflow/graph.py` 를 고쳤고 설치본을 함께 갱신했다.
`diff -r` byte-identical 이며, **설치본 복사본에서 직접** `batch`/`ainvoke` 가
`BLOCKED / MALFORMED_STATE / effects=0` 으로 막히고 `bind` 가 denied 임을 확인했다.

### FR-F-001 (iteration 4, Final Adversarial Review blocking) — crash-safe idempotency 가 opt-in

**Reviewer 지적의 정당성**: 전적으로 유효하다. iteration 1 에서 나는 `RuntimeStatePort` 를
graph 실행 경로에 연결했지만 **optional** 로 두었다. `execute_intent_node(..., runtime_state=None)`
은 `_settle_now` 를 골랐고 그 첫 동작이 `adapter.start(intent)` 였다 — 외부 효과 이전의 claim 이
없다. `build_graph` / `execute_state` 기본값도 `None` 이고 CLI 도 `--runtime-state` 없으면 `None`
이었다. 기존 recovery 테스트는 store 를 항상 주입했으므로 **shipped default 계약이 안전하다는 것을
증명하지 못했다.** 이것이 C-001 의 핵심이었으므로 해소되지 않은 상태였다.

독립 재현 (수정 전):

```text
one stable intent: intent_9d7f856f7db38f58164d6947
  process 1 (default, fresh adapter): external_task_creates=1
  process 2 (default, fresh adapter): external_task_creates=1
  -> total external Task creates for ONE stable intent: 2   (safe would be 1)
```

**택한 방안: (a) + (b).** port 를 **요구**하되(a), 호출자가 adapter 에만 wiring 한 경우
그 port 를 안전하게 도출·검증한다(b). (c) 단독(runtime 시점 fail closed)을 고르지 않은 이유는,
port 부재는 **설정 오류**이지 workflow 결과가 아니기 때문이다. 실행 중 terminal 로 만들면
"BLOCKED workflow" 와 "잘못 구성된 배포" 가 구분되지 않는다. 대신 graph 를 **만들 수 없게** 해서
중복 가능한 graph 자체가 존재하지 못하게 했다 — 이것이 리뷰의 "없으면 생성 불가" 요구에 정확히
대응한다. state 를 처리하기 전, adapter 를 건드리기 전에 실패한다.

**수정**:

1. `runtime_state.resolve_runtime_state(adapter, runtime_state)` — 단일 해석 지점.
   명시 인자 → adapter-bound port 순으로 해석하고, **둘 다 있는데 서로 다른 객체면**
   `RuntimeStateConflict` 를 낸다(ledger 가 둘로 갈리면 receipt 가 분리되어 보장이 깨진다).
   아무것도 없으면 `IdempotencyPortRequired`.
2. `execute_intent_node` 는 **node 생성 시점에** port 를 해석한다. port 없는 모드를 삭제했다 —
   `_settle_now(adapter, None, ...)` 분기가 사라지고 모든 실행이 claim-before-effect 인
   `_execute_recoverable` 하나를 지난다.
3. `build_graph` 가 `StateGraph` 를 만들기 전에 해석한다 → **build time 에 거부**.
4. `execute_state` 도 state 검사 전에 해석한다.
5. **launcher 는 기본 store 를 스스로 제공한다** — `--runtime-state` 가 없으면
   `$ORCA_OS40_RUNTIME_STATE_DIR`(기본: 시스템 temp 아래 `orca-os40-runtime-state`) 에
   `<run_id>__<thread_id>.json` 을 쓴다. **in-memory 가 아니라 실제 파일**이므로 프로세스 사망을
   견딘다 — crash window 가 요구하는 성질이 정확히 그것이다. temp 는 주기적으로 비워지므로
   장기 운용은 env 나 `--runtime-state` 로 고정하라고 INSTALL.md 에 명시했다.

**default 경로 실측 before / after**:

```text
# BEFORE
process 1 (default, fresh adapter): external_task_creates=1
process 2 (default, fresh adapter): external_task_creates=1
-> total for ONE stable intent: 2

# AFTER  (evidence/FR-F-001_after_fix_reproduction.txt)
(a) port 가 전혀 없을 때 — adapter.start 이전에 거부
  execute_intent_node(adapter)  refused: IDEMPOTENCY_PORT_REQUIRED: pass runtime_state=...
  external_task_creates = 0                      (was 1 per process)
  build_graph(adapter)          refused at build time
  effects = 0
(b) adapter-bound port 도출 — 두 개의 fresh process
  process 1 (fresh harness + fresh adapter): external_task_creates=1
  process 2 (fresh harness + fresh adapter): external_task_creates=0
  -> total external Task creates for ONE stable intent: 1   (was 2)
```

**launcher 기본 동작이 안전해진 근거** (플래그 없이 실행):

```text
$ python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo --json
{... "terminal_status": "COMPLETED", "trace_length": 68, "exit_code": 0}
exit=0
$ ls $ORCA_OS40_RUNTIME_STATE_DIR
run_demo__demo.json
  run_demo__demo.json: 11 records, all SETTLED = True
```

정상 경로는 그대로 COMPLETED / trace 68 / exit 0 이며, 이제 **기본값으로** durable ledger 를
남긴다.

**회귀 테스트** (`DefaultPathIdempotencyTests`, 전부 `runtime_state` 를 주지 않고 public API 호출):

- `test_default_execute_intent_node_refuses_without_a_durable_port` — 거부되고
  `harness.creates == 0`, 즉 **`adapter.start` 이전에** 막힌다.
- `test_default_build_graph_refuses_without_a_durable_port` / `..._execute_state_...` — 동일.
- `test_adapter_bound_port_is_derived_without_an_explicit_argument` — (b) 경로 동작 확인.
- `test_two_ports_that_disagree_are_rejected` — ledger 분리 방지.
- `test_one_stable_intent_creates_one_external_task_across_fresh_processes` — Final Reviewer 의
  시나리오 그대로: fresh harness + fresh adapter 2회, 명시 인자 없이 → 외부 Task 생성 **총 1건**.
- `test_launcher_default_runtime_state_is_a_durable_file`,
  `test_cli_without_runtime_state_flag_still_persists_a_ledger` (11 records 전부 SETTLED),
  `test_cli_rerun_recovers_from_the_default_ledger_without_new_effects`.

**기존 호출부 갱신**: port 없는 모드를 없앴으므로 `build_graph(adapter)` 형태의 기존 테스트
호출부 21곳을 명시적 ledger 를 넘기도록 고쳤다. in-process 단위 테스트는
`InMemoryRuntimeStateStore` 를 **명시적으로 선택**한다(기본값으로 주어지는 것이 아니다 —
그 기본값이 바로 이 finding 이었다). crash/restart 테스트는 종전대로 file store 를 쓴다.

**부수적으로 드러난 것**: launcher CLI 테스트 중 두 subtest 가 같은 `run_id` 를 공유했는데,
기본 ledger 경로가 `run_id__thread_id` 에서 파생되므로 두 번째 subtest 가 첫 번째의 settlement 를
복구해 BLOCKED 로 끝났다. 이는 **엔진이 올바르게 동작한 결과**다(같은 stable intent → 같은 결과).
테스트 쪽을 고쳐 subtest 마다 별도 ledger 디렉터리를 쓰게 했다. 같은 run_id 를 재사용하면 기존
receipt 를 복구한다는 성질 자체는 의도된 것이며 INSTALL.md 에 기술했다.

**mirror / INSTALL.md**: `runtime_state.py`, `executor.py`, `graph.py`, `launcher.py` 를 고치고
설치본을 함께 갱신했다(`diff -r` byte-identical). **설치본 복사본에서 직접** 기본 경로가
`IDEMPOTENCY_PORT_REQUIRED` 로 거부되고(`external_task_creates=0`) demo 는 COMPLETED / trace 68 /
exit 0 임을 확인했다. INSTALL.md 는 "durable idempotency 는 필수" 절과 기본 ledger 위치·환경변수를
설명하도록 갱신했다.

### FR2-M-002 (iteration 5, Final Adversarial Review attempt 2 blocking) — public `.compiled` 이 guard 를 벗겨낸다

**Reviewer 지적의 정당성**: 유효하다. `__getattr__` 의 deny-by-default 는 `compiled` 에
작동하지 않았다 — `__init__` 이 설정한 **실재 instance attribute** 이므로 `__getattr__` 이
호출조차 되지 않는다. 독립 재현:

```text
# BEFORE  (evidence/FR2-M002_before_fix_reproduction.txt)
via facade    -> BLOCKED   MALFORMED_STATE     effects=0
via .compiled -> COMPLETED WORKFLOW_COMPLETED  effects=3
```

**내 기존 근거에 대한 정정**: iteration 3 에서 나는 `.compiled` 를 "문서화된 test 전용 handle"
로 남기고 docstring 에 그렇게 적었다. Reviewer 의 판단이 옳다 — 문제는 목적이 아니라 표면이고,
"문서에 쓰지 말라고 적어 둔 것" 은 fail-closed 가 아니다. 공개 attribute 하나로 위의 모든 guard
가 무력화되면 의도와 무관하게 loophole 이다.

**택한 방향과 근거**: **compiled graph 를 façade 에 저장하지 않는다.**
module-private `WeakKeyDictionary`(`_COMPILED_GRAPHS`)에 façade 인스턴스를 key 로 보관하고,
guard 된 method 들이 그 map 을 직접 조회한다.

- 이름만 바꾸는 방향(`_compiled`)은 택하지 않았다. Reviewer 가 지적한 대로 Python 에서 접근을
  막지 못한다. **name mangling(`self.__compiled`)도 실제로 시도했다가 버렸다** — 내가 쓴
  invariant 테스트가 `dir(facade)` 에서 `_GuardedWorkflowGraph__compiled` 를 잡아냈다.
  같은 이유로 `_graph` property 도 넣었다가 제거했다: property 역시 "raw graph 를 돌려주는
  member" 다. 결국 **어떤 형태의 member 도 두지 않는 것**만이 요구를 만족한다.
- 결과적으로 façade 에는 public/private 을 불문하고 raw graph 를 돌려주는 attribute, property,
  method 가 **하나도 없다**. weak map 이므로 façade 가 버려지면 graph 도 함께 해제된다.

**AFTER 실측** (evidence/FR2-M002_after_fix_reproduction.txt):

```text
via facade    -> BLOCKED MALFORMED_STATE effects=0
via .compiled   -> AttributeError (denied)
via ._compiled  -> AttributeError (denied)
via ._graph     -> AttributeError (denied)
via .graph      -> AttributeError (denied)
via .pregel     -> AttributeError (denied)
via .raw        -> AttributeError (denied)
via .builder    -> AttributeError (denied)
via .validate   -> AttributeError (denied)
via .copy       -> AttributeError (denied)
members of the facade yielding a raw Pregel: none
subgraphs reachable through the facade: none
```

**pin 테스트의 의도를 어떻게 보존했는가**: 테스트가 **자기 raw graph 를 직접 만든다**.
`_raw_compiled_graph()` 는 동일한 `WorkflowState` schema 위에 passthrough node 하나짜리
`StateGraph` 를 compile 한다 — 같은 `CompiledStateGraph` class, 같은 public API 표면, 같은
입력 channel filtering 동작이다. 따라서:

- `test_raw_langgraph_still_drops_unknown_channels` 는 여전히 **guard 를 거치지 않은** 진짜
  LangGraph 동작을 관찰한다(guard 가 그 사실을 가릴 수 없다는 성질 유지).
- iteration 3 의 재발 방지 invariant(`test_no_state_ingress_api_is_reachable_unguarded`,
  `test_declared_guard_list_matches_the_installed_runtime`,
  `test_readonly_allowlist_contains_no_state_ingress`)는 ingress 이름을 이 test-owned raw
  graph 에서 뽑아 그대로 동작한다. **production 에는 이 경로가 존재하지 않는다** — 테스트가
  `langgraph.graph.StateGraph` 를 직접 쓸 뿐, `build_graph` 가 돌려준 객체를 벗기지 않는다.

**추가한 회귀 테스트**:

- `test_raw_graph_is_not_reachable_from_the_facade` — `compiled`, `_compiled`, `_graph`,
  `graph`, `pregel`, `raw` 접근이 전부 `AttributeError` 이고 effect 0건.
- `test_no_public_member_of_the_facade_yields_an_unguarded_graph` — **불변식**: `dir(facade)` 의
  모든 non-dunder member 를 읽어 어느 것도 `Pregel` 인스턴스가 아님을 단언하고,
  `get_subgraphs()` 가 비어 있음(subgraph 도 unguarded Pregel 이므로)을 확인한다.
  이 테스트가 name-mangled 저장과 `_graph` property 를 실제로 잡아냈다.
- `test_the_facade_is_the_only_runnable_reachable_from_itself` — 어떤 member 도 `invoke` 를
  가진 객체를 돌려주지 않음을 단언.

**mirror**: `scripts/deterministic_workflow/graph.py` 를 고치고 설치본을 함께 갱신했다
(`diff -r` byte-identical). 설치본 복사본에서 façade 가 BLOCKED/0 effects 이고 `.compiled` 가
`AttributeError` 이며 raw Pregel 을 돌려주는 member 가 없음을, demo 가 COMPLETED / trace 68 /
exit 0 임을 직접 확인했다.

## 알려진 제한사항 (PR Description 갱신용)

Worker 는 commit/push/PR 을 수행하지 않았다. Coordinator 가 phase gate + Final Review PASS 이후
반영할 수 있도록 실제 상태를 아래에 정리한다.

1. **durable `RuntimeStatePort` 는 선택이 아니라 필수다.** `build_graph` /
   `execute_intent_node` / `execute_state` 는 port 없이 호출하면 `IdempotencyPortRequired` 를
   낸다. `run_workflow.py` 는 기본 ledger 를 스스로 제공하므로 CLI 사용자는 플래그가 필요 없다.
   기본 위치는 `$ORCA_OS40_RUNTIME_STATE_DIR` 이하이며, 지정하지 않으면 시스템 temp 아래다 —
   프로세스 사망은 견디지만 temp 는 주기적으로 비워지므로 장기 운용은 경로를 고정해야 한다.
   같은 `run_id`/`thread_id` 로 재실행하면 같은 ledger 를 재사용해 기존 settlement 를 복구한다.
2. **crash window 의 잔여 구간은 fail-closed 로 처리한다.** claim 직후 ~ 외부 효과 생성 사이에
   프로세스가 죽고 adapter 가 settlement 를 복구하지 못하면, workflow 는 중복 생성 대신
   `IDEMPOTENCY_RECOVERY_REQUIRED` 로 정지한다. 이를 자동 재개로 바꾸려면 Orca 측에
   "stable intent id 로 기존 Task 조회" API 가 필요하며, 현재 harness 에는 없다.
3. **`occurred_at` 은 settlement identity 에 포함되지 않는다.** deterministic replay 와
   adapter parity 를 보장하기 위한 의도적 선택이며, 형식만 검증한다. 어떤 gate 도 이 필드를
   읽지 않으므로 적용되는 결정에 영향이 없다.
4. **unknown state field 는 graph 내부가 아니라 invocation 경계에서 차단된다.** LangGraph 가
   선언되지 않은 입력 채널을 조용히 버려 node 가 볼 기회 자체가 없기 때문이다. compiled graph
   경계(`GuardedWorkflowGraph`)와 launcher 진입점 **양쪽**이 독립적으로 fail closed 하며,
   LangGraph 의 이 동작은 guard 를 우회한 `.compiled` 에 대한 테스트로 고정되어 있다.
5. **`build_graph` 는 `CompiledStateGraph` 가 아니라 deny-by-default façade 를 반환한다.**
   `invoke`/`ainvoke`/`stream`/`astream`/`batch`/`abatch`/`update_state`/`aupdate_state`,
   그리고 read-only 조회 allowlist 만 노출한다. `transform`, `astream_events`, `bind`,
   `pipe`, `with_config`, `builder` 등은 도달할 수 없다 — 필요해지면 guard 를 붙여
   명시적으로 노출해야 하며, 이는 우회 재발을 막기 위한 의도적 제약이다.
   **raw compiled graph 를 돌려주는 member 는 존재하지 않는다.** 이전의 `.compiled` handle 은
   제거되었고, raw LangGraph 동작을 관찰해야 하는 테스트는 동일 schema 로 자기 graph 를
   compile 한다(`_raw_compiled_graph()`).
6. **M-004 는 삭제가 아니라 강등이다.** 리뷰가 허용한 두 선택지 중 강등을 택했고, 안전 규칙
   보존을 validator 로 강제한다. 산문 분량 자체는 줄지 않았다.
7. **launcher 가 제공하는 adapter 는 fake 뿐이다.** OrcaAdapter 는 살아있는 harness 를 요구하므로
   CLI 선택지에 넣지 않았다. 프로그램적으로는 `execute_state(adapter=OrcaAdapter(...))` 로 사용한다.
