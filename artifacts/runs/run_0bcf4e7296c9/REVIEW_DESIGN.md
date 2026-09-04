# Review Result

RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES
DECISION_GATE_STATE: CLEAR

## Summary

이 DESIGN 은 IMPLEMENTATION 이 착수할 수 있는 수준으로 구체적이고, 근거로 삼은 API 와
repository 사실이 실제와 일치한다. 직접 실행해 확인한 결과 blocking 위반은 없다.

**LangGraph 주장을 실측으로 검증했다.** 이 설계의 안전성 논증 전체가 “side effect 이전에 intent 가
checkpoint 된다” 에 걸려 있어 그것을 재현했다: `PREPARE_INTENT` 가 `pending_intent`/`PREPARED` 를
쓰고 `EXECUTE_INTENT` 가 crash 하도록 만든 graph 에서, checkpoint 에는 intent 가 durable 하게
남고 `next == ("EXECUTE",)` 였다. **§7 의 boundary A 는 실제로 성립한다.** 9개 RouteToken 을
3개 node 로 보내는 total `path_map` 도 compile·routing 모두 정상이었고, 같은 `thread_id` 의
checkpoint 는 동일 next node 를 반환했다(AC 8). AC 11 경계도 §8 서술과 정확히 일치한다 —
compile 은 unknown edge target 과 missing entrypoint 만 잡고 **unreachable node 와 부분
`path_map` 은 통과시킨다.** 즉 자체 `validate_graph_spec` 을 두는 §8 의 판단은 실측에 근거한다.

**인용한 repository API 가 전부 실재한다.** §9 가 composition 대상으로 지목한
`orca_runtime_harness` 의 10개 method(`preflight`, `create_task`, `create_phase_graph`,
`start_worker`, `wait_for_done`, `settle_attempt`, `claim_settlement`, `verify_settlement`,
`finalize_once`, `account_axes`)가 모두 존재하고, `quality_profile.workflow_gate_value:638`,
`skill_policy.load_risk_contract:129` 도 존재한다. §1 의 `HumanApprovalPort` 재사용 주장은
`clarification_protocol.py:111-116` 의 실제 Protocol signature 와 **문자 그대로 일치**한다 —
schema 를 fork 하지 않는다는 주장이 사실이다. baseline 도 재실행해 확인했다:
`Ran 1725 tests … OK (skipped=6)` 로 CF-2 및 DESIGN `:18` 과 일치한다.

**요구된 항목을 빠짐없이 덮는다.** OS-40 이 열거한 typed state 최소 항목 12종이 전부 필드로
존재하고 각각 타입·허용값·불변조건이 붙어 있다(이름만 나열한 필드가 없다). checkpoint 안전성은
서술이 아니라 강제 장치다 — `json.dumps(allow_nan=False)` + 재귀 타입 허용목록 +
`process_handle|terminal_handle|credential|access_token|client|session_handle` key 스캔 +
core module 에 대한 static AST symbol 스캔의 4중 구조다. 전이는 `route(state) -> RouteToken`
하나로 모이고 host loop 부재를 AST test 로 막으며, PLAN 이 확정한 `e2e_harness` 처분
(pure logic 추출 + `run_workflow` 는 test-only oracle + production import 금지 static test)이
§11 에 일관되게 반영되어 두 번째 transition engine 이 남지 않는다. phase PASS 와 final PASS 의
비대체는 `PhasePass{generation, tree_digest}` 와 `all_phase_passes_current()` 로 state 수준에서
보장된다. T2 budget-first, 두 iteration domain, D 의 HIGH-only suffix 정의는 `SKILL.md:2211`,
`:1855-1868`, `:2217-2223` 과 대조해 일치를 확인했다. **[CF-1] 은 검증된 import 기반 형태 그대로
반영되었고**(`:426-441`), 세 mode guard unit test 를 absent lane 의 선행 조건으로 못박았다.

non-blocking note 3건은 모두 **설계 내부 상호참조의 불일치**다: terminal_status 를 어느 node 가
쓰는지 미지정(N-001), `route` 우선순위 4번이 RouteToken 어휘로 표현 불가(N-002), §8 cycle 규칙이
ROUTE↔ADVANCE_PHASE cycle 을 잘못 걸러낼 수 있음(N-003). 어느 것도 architecture 를 무너뜨리지
않고 설계 자신의 linter/test 가 즉시 드러내며 §6·§7 이 정답을 이미 결정해 두었으므로 G1-G5
위반이 아니다. IMPLEMENTATION 이 착수 전에 정리하면 된다.

## Blocking Findings

없음. 이번 라운드에서 gate 를 실패시켜야 할 G1-G5 위반은 발견하지 못했다.

## Non-Blocking Findings

### N-001

```text
ID: N-001
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `DESIGN.md:239-248` (§2 Nodes 표), `DESIGN.md:316-322` (§6)

**Issue**: `terminal_status` / `terminal_reason` 를 **어느 node 가 쓰는지** 가 지정되지 않았다.
표에서 `VALIDATE` 는 “terminal BLOCKED reason”, `VALIDATE_SETTLEMENT` 는 “terminal BLOCKED or
validated event” 를 쓰지만, `ROUTE` 의 Writes 는 “route trace and `route_token`” 뿐이고
`TERMINAL` 은 terminal status/reason 을 **읽고** “final trace only” 를 쓴다. 따라서 `route` 가
`ESCALATE`(§4 항목 5·9·11·12·13) 또는 `COMPLETE`(항목 10)를 반환하는 경로, 그리고 decision
block 에서 유래한 `BLOCK`(항목 2)에는 terminal 필드를 기록할 주체가 없다.

**Reason**: 구현 불가가 아니다. §6 이 status 별 진입 조건과 closed reason code 를 전부 확정해
두었으므로 **값** 은 결정되어 있고 **기록 위치** 만 비어 있다. capability BLOCK 은 §1 이
`VALIDATE` 로 명시해 이미 덮여 있다. 자연스러운 해석(TERMINAL 이 `route_token` + §6 조건으로
기록)이 존재하며 어느 선택도 외부 동작을 바꾸지 않는다. G1-G5 어디에도 해당하지 않는다.

**Required Action**: optional — §2 표에서 `ROUTE` 또는 `TERMINAL` 중 하나에 terminal 필드 write
책임을 명시한다.

### N-002

```text
ID: N-002
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
```

**Location**: `DESIGN.md:291` (§4 우선순위 4번) 대 `DESIGN.md:257-261` (RouteToken) 및
`DESIGN.md:352` (§8 coverage 규칙)

**Issue**: §4 는 `route` 의 strict priority 4번으로 “pending intent `PREPARED` → EXECUTE path;
`SETTLED` → validation/apply path” 를 둔다. 그런데 `RouteToken` 9개
(`BLOCK, ESCALATE, PREPARE_WORKER, PREPARE_PHASE_REVIEWER, ADVANCE_PHASE,
PREPARE_FINAL_REVIEWER, PREPARE_CORRECTION, PREPARE_REVALIDATION, COMPLETE`)에는 이 두 결과를
표현할 member 가 없고, §8 은 `set(RouteToken) == ROUTE_TARGETS.keys()` 총체성을 요구한다.
즉 `route` 가 반환할 수 없는 분기가 우선순위표에 들어 있다.

**Reason**: 설계 안에 정답이 이미 있다. §7 의 data flow(`PREPARE_INTENT → EXECUTE_INTENT →
VALIDATE_SETTLEMENT → APPLY_RESULT → ROUTE`)는 이 구간을 **static edge** 로 두며, 실제로
재현해 확인한 결과 checkpoint resume 은 `ROUTE` 를 거치지 않고 `next == ("EXECUTE",)` 로 바로
복귀한다. 따라서 항목 4 는 `route` 의 분기가 아니라 구조적 edge 서술이며, 문장의 위치가
잘못된 것이다. 다른 해석(token 2개 추가)도 무해하고, 어느 쪽이든 §8 의 coverage linter 가
구현 즉시 불일치를 드러낸다. 구현 가능성이나 외부 동작을 해치지 않으므로 G2 가 아니다.

**Required Action**: §4 항목 4 를 “구조적 edge 이며 `route` 의 반환 분기가 아니다” 로 명시하거나,
`RouteToken` 에 해당 member 를 추가하고 `ROUTE_TARGETS` 를 확장한다. IMPLEMENTATION 착수 전에
둘 중 하나로 확정하는 것이 좋다.

### N-003

```text
ID: N-003
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `DESIGN.md:355` (§8 마지막 lint 규칙)

**Issue**: “every cycle contains ROUTE and is budget-guarded by declared `phase` or `final` guard
metadata” 라고 했으나, 설계가 명시한 `ROUTE → ADVANCE_PHASE → ROUTE` cycle(§3 `:282`)은
iteration budget 이 아니라 `current_phase_index` 의 단조 증가로 종료한다. 규칙을 문자 그대로
구현하면 정상 graph 가 linter 에 걸린다.

**Reason**: 종료성 자체는 보장된다(index 는 `len(requested_phases)` 로 유계). guard metadata
종류를 하나 더 인정하면 해소되는 linter 규칙 서술 문제다.

**Required Action**: optional — guard metadata 에 `phase_index_monotonic` 같은 세 번째 종류를
추가하거나, 해당 cycle 을 규칙에서 명시적으로 예외 처리한다.

## Test Review

이 phase 는 production code 를 변경하지 않는다. `git status --short` 에 tracked 수정 0건,
branch `feat/os-40-langgraph-engine`, HEAD `7bc228a`. delta 는 `DESIGN.md`(510 lines) 신규 생성뿐이며,
내가 만든 검증 스크립트는 scratchpad 에만 두고 삭제했다(저장소 무변경 확인).

**LangGraph 0.2.76 실측 (설치본 직접 실행)**

| 설계 주장 | 실측 결과 |
| --- | --- |
| §7 boundary A — side effect 이전에 intent 가 checkpoint 된다 | **HOLDS.** `PREPARE` 후 `EXECUTE` crash 시 checkpoint 에 `pending_intent={'intent_id':'i1',…}`, `intent_status='PREPARED'`, `next=('EXECUTE',)` |
| §3 dict/tuple/JSON-safe state 가 checkpoint 를 통과 | 통과 — `{'ANALYSIS':0}`, `['a']` 보존, `json.dumps(sort_keys=True)` 성공 |
| §7 `build_graph(checkpointer: BaseCheckpointSaver \| None)` | `langgraph.checkpoint.base.BaseCheckpointSaver` import 가능; `compile(checkpointer=…)` 시그니처 일치 |
| §3 total `ROUTE_TARGETS` (9 token → 3 node) conditional edges | compile OK, `PREPARE_CORRECTION`/`ADVANCE_PHASE`/`ESCALATE` 모두 정상 routing |
| AC 8 — 같은 `thread_id` checkpoint 가 동일 next node | `('B',) == ('B',)` → True |
| §8 “compile 이 unknown edge target 을 잡는다” | `ValueError: Found edge ending at unknown node` |
| §8 “compile 이 missing entrypoint 를 잡는다” | `ValueError: Graph must have an entrypoint` |
| §8 “compile 은 unreachable node 를 못 잡는다” (자체 linter 필요 근거) | **compiled OK (미검출)** — 서술과 일치 |
| §8 “compile 은 path_map coverage 를 보장하지 않는다” | **compiled OK (미검출)** — 서술과 일치 |

AC 11 경계가 승인된 ANALYSIS 의 실측과도 모순되지 않는다.

**repository 근거 검증**

| 설계 주장 | 확인 |
| --- | --- |
| §9 OrcaAdapter 가 composition 할 harness primitive 10종 | 전부 존재 — `preflight:1513`, `create_task:1667`, `create_phase_graph:1676`, `start_worker:1837`, `wait_for_done:1938`, `settle_attempt:1972`, `claim_settlement:1207`, `verify_settlement:1281`, `finalize_once:1475`, `account_axes:1388` |
| §1 `HumanApprovalPort` 를 fork 하지 않고 재사용 | `clarification_protocol.py:111-116` 의 `publish/show/ingest` signature 와 문자 그대로 일치 |
| §12 `quality_profile` 의 `workflow_gate_value` | 존재 (`quality_profile.py:638`) |
| §12 `skill_policy` 기존 risk parser | 존재 (`skill_policy.py:129 load_risk_contract`) |
| `:14` `e2e_harness.py:1925-2437` 이 imperative loop 소유 | `run_workflow` 가 1925 행, 파일 2438 행 — 정확 |
| `:18` baseline `1725 tests, OK (skipped=6)` | **직접 재실행 확인** — `Ran 1725 tests in 328.186s` / `OK (skipped=6)` |
| §4 T2 budget-first 를 FAIL edge 최초 평가로 | `SKILL.md:2211` 과 일치 |
| §4 항목 14 D = HIGH-only canonical suffix | `SKILL.md:2217-2223` 과 일치 |
| §5 두 counter 의미(LOW=Worker, MEDIUM/HIGH=Reviewer gate attempt; final=dispatch 횟수) | `SKILL.md:1855-1868` 과 일치 — final counter 를 PREPARE 에서 증가시키는 선택도 “dispatch된 횟수” 정의와 부합 |
| §5 decision block 은 iteration 을 소비하지 않음 | `SKILL.md:1850-1854` 와 일치 |

**dispatch 질문별 판정**

1. **typed state 최소 항목 전부 덮는가** — 덮는다. run/workflow identity, phase·iteration,
   pending role·intent, 세 result slot, quality verdict·decision state, blocking finding·
   responsible phase, correction·revalidation 상태, consumed/remaining budget, pending
   clarification, artifact·repository head binding, terminal status·reason, processed
   command/event ID **12종 모두** 필드로 존재하며 각 행에 타입·허용값·불변조건이 있다.
   `remaining_*` 를 파생값이면서도 state 에 두는 것은 OS-40 이 “consumed/remaining” 을 명시적으로
   요구하기 때문이며, 파생 검증 규칙이 함께 정의되어 있다.
2. **checkpoint 안전성이 구조적으로 강제되는가** — 그렇다. 4중 장치(JSON 직렬화 강제, 재귀 타입
   허용목록, handle/credential key 스캔, core module AST symbol 스캔)이며 위반 시
   `NON_CHECKPOINTABLE_STATE` 로 fail-closed 한다. 서술이 아니라 코드로 강제할 형태다.
3. **전이가 단일 순수 함수이고 우회 loop 가 불가능한가** — `route(state) -> RouteToken` 하나이며
   signature 가 확정되어 있다. host loop 부재는 §2 와 AST test(`run_workflow`, `graph.invoke`
   주위 `while`, routing/spec 의 adapter import 거부)로 막는다. §11 이 PLAN 의 처분을 그대로
   반영해 두 번째 transition engine 이 production 에 남지 않는다.
4. **phase PASS 와 final PASS 비대체가 state 수준에서 보장되는가** — 보장된다.
   `PhasePass{generation=phase_iterations[p], tree_digest}` + `all_phase_passes_current()` +
   “correction 이 p 와 D 의 pass generation 을 dispatch 전에 무효화” 로 양방향 대체가 막힌다.
5. **두 iteration domain 과 decision-block 비소비** — §5 에 있고 SKILL.md 와 일치한다.
6. **T0~T5a·D 가 SKILL.md 와 일치하는가** — 일치한다. 특히 T2 가 FAIL edge 의 **최초** 평가로
   설계되어(“ESCALATE before finding mapping or phase budget read”) 예산 소진 후 dispatch 가
   구조적으로 불가능하다.
7. **port signature 가 구현 가능하고 capability fail-closed 인가** — 가능하다. 모든 port 가
   `Protocol` 이고 domain record 만 오간다. closed `Capability` 12종, base 8 + 조건부 추가,
   `missing_capabilities()` 가 PREPARE_INTENT 이전 VALIDATE 에서 돌며 `calls=0` 을 assert 한다.
8. **stable ID 도출과 intent/settlement 순서가 구체적인가** — `command_id`/`intent_id`/`event_id`
   각각의 canonical 입력 집합이 §4 `:176` 에 명시되고 timestamp·handle 을 identity 에서 배제한다.
   §7 이 crash 창(pre-A, A→B, B→C)별 결과까지 서술한다.
9. **AC 11 설계가 실측과 일치하는가** — 일치한다(위 표).
10. **OS-28~30 를 consumer 로만 쓰는가** — §12 가 6개 모듈을 façade/re-export/ingress 로만
    사용하고 ledger schema v1·log column·audit schema 를 그대로 둔다고 명시한다.
11. **범위 확대 없는가** — 없다. OS-31 은 checkpointer 주입 + `WAITING_FOR_INPUT` 여지만,
    OS-37 은 동일 port + conformance suite 만 남기고 `ingest` resume 은 명시적으로 미구현이다.
    BUGFIX/REFACTORING 포함은 현행 SKILL.md 계약 범위이지 신규 preset 이 아니다.
12. **[CF-1] 반영** — 반영되었다(`:426-441`). 검증된 import 기반 guard 를 그대로 채택하고,
    metadata 만으로는 판별 불가라는 근거까지 적었으며, present / finder-raise / loader-raise
    3-mode guard unit test 를 absent lane 실행의 **선행 조건**으로 못박았다(`:441`, `:500`).

**Testing Strategy 판정** — 3개 신규 test module 의 담당 범위가 분리되어 있고, 12종 mutation
(phase edge swap/remove, FAIL→advance, `>=`→`>`, T2 를 finding mapping 뒤로, quality 를 decision
앞으로, event dedupe 제거, receipt lookup 우회, terminal edge 허용, route-map member 삭제,
dead node 추가, parity 필드 변형, Skill token 변경)이 “named assertion failure 를 일으켜야 한다”
로 규정되어 단순 재서술이 아니다. 실행 명령이 전부 unittest 이고 full discover 에 6분 초과
timeout 을 명시한 점도 CF-2 와 부합한다.

**decision gate 형식 검증** — `DECISION_GATE_STATE:` 1개, ```` ```decision-gate ```` record 1개,
`STATUS:` 1개, template 이 요구한 8개 section 모두 존재(`Decision Record` 는 optional 이며 없음).
record 는 CLEAR 5개 key 만 사용하고 선언과 일치한다. §Risks 첫 줄이 “No open user decision”
으로 열린 항목 부재를 **단언**하며, 확정된 항목 중 되돌릴 수 없거나 blast radius/monetary/
security/privacy/compliance/lock-in 이 참인 것은 없다. **오분류 없음.**

## Final Decision

**RESULT: PASS** / REVIEW_VERDICT: **PASS WITH NOTES** — blocking 0건, non-blocking 3건.

이 설계는 DESIGN phase 가 요구하는 것을 충족한다. 요구된 typed state 항목을 빠짐없이 덮고 각
필드에 불변조건을 붙였으며, 전이를 단일 순수 함수로 모으고 우회 loop 를 정적 검사로 막았고,
phase/final PASS 비대체와 두 budget domain 과 T2 budget-first 를 state 수준에서 보장한다.
port signature 는 실제 구현 가능한 형태이고 capability 부족 시 dispatch 이전에 fail-closed 한다.
무엇보다 **설계가 의존하는 LangGraph 동작을 내가 직접 재현했고 전부 사실이었다** — 특히
intent-before-effect checkpoint 경계와 AC 11 의 compile 한계(자체 linter 가 필요한 이유)가
그렇다. 인용한 repository API 도 하나도 빠짐없이 실재하며 baseline 도 일치한다.
승인된 ANALYSIS/PLAN 과 모순되는 지점은 없고, `e2e_harness` 처분은 PLAN 의 결정을 그대로 따른다.
[CF-1] 은 검증된 형태 그대로 반영되었고 3-mode guard test 를 absent lane 의 선행 조건으로 못박아
오히려 요구보다 한 걸음 더 나아갔다.

남은 3건은 모두 **설계 내부 상호참조의 불일치**이지 architecture 결함이 아니다. terminal 필드의
write 주체(N-001), `route` 우선순위 4번과 RouteToken 어휘의 불일치(N-002), cycle lint 규칙이
`ADVANCE_PHASE` cycle 을 잘못 거를 가능성(N-003) — 세 건 모두 §6·§7·§8 이 정답을 이미 결정해
두었고, 어느 것도 외부 동작이나 구현 가능성을 바꾸지 않으며, 설계 자신의 linter 와 test 가
구현 즉시 드러낸다. 따라서 G1-G5 위반이 아니고 `Quality Attribute: NONE` / `Blocking: NO` 다.
IMPLEMENTATION 이 착수 전에 N-002 를 한 문장으로 확정하는 것을 권한다.

**DESIGN phase gate: PASS.**

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This review's verdict follows from explicit OS-40 requirements and the DESIGN phase contract applied to evidence I executed directly — LangGraph 0.2.76 introspection, the checkpoint-boundary reproduction, the repository API checks and the regression baseline; the remaining notes are internal cross-reference gaps that violate no gate, so no item at this boundary needs user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```
