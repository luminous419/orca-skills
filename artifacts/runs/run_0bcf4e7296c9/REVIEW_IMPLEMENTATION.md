# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

구현의 골격은 좋다. `route()` 는 실제로 단일 순수 함수이고, `PREPARE_INTENT → EXECUTE_INTENT →
VALIDATE_SETTLEMENT → APPLY_RESULT` static edge chain이 intent-before-effect 경계를 만들며,
checkpoint resume 이 실제로 동작한다. IMPLEMENTATION.md 가 인용한 **명령 출력은 전부 재실행으로
확인했고 하나도 틀리지 않았다** — full suite `Ran 1744 tests / OK (skipped=6)`(baseline 1725 +
신규 19, skip 6 그대로 = 회귀 없음), `validate_skills` 727 checks, `verify_package` 226 files,
`validate_workflow_graph_docs` PASSED, `git diff --check` 무출력. CF-1~CF-5 도 코드와 DESIGN.md
양쪽에 반영되었다. staging/commit 도 없고 OS-28~30 schema 파일과 historical artifact 도 손대지
않았다.

그러나 **workflow 의 핵심 안전 규칙 하나가 실제로 깨진다.** SKILL.md T4 와 AC 3 이 요구하는
"responsible phase 의 iteration budget 이 소진되면 추가 dispatch 없이 ESCALATED" 가 구현되어
있지 않다. 직접 graph 를 돌려 재현했다 — `max_iterations=2` 로 ANALYSIS 가 gate attempt 2회를
모두 쓴 뒤 Final Review 가 FAIL 하며 ANALYSIS 를 지목하면, engine 은 **budget 을 넘겨 correction
Worker 와 Reviewer 를 추가 dispatch** 하고 `phase_iterations[ANALYSIS]` 가 **3(최대 2)**,
`remaining_phase_budget` 이 **-1** 이 되며, 그러고도 최종 결과는 **`COMPLETED`** 다.
예산을 초과한 run 이 성공으로 종결된다.

**AC 13(fake↔Orca logical trace parity)은 구현도 검증도 되어 있지 않다.** `OrcaAdapter` 는
`harness.execute_intent/send_intent/intent_status/interrupt_intent` 를 호출하는데 이 네 method 는
`OrcaRuntimeHarness` 에 **존재하지 않는다**(직접 grep 확인). DESIGN §9 가 조합하라고 지정한 실제
primitive 들은 코드가 아니라 문자열 dict `ORCA_PRIMITIVE_MAP` 안에 설명으로만 있다. `OrcaAdapter`
를 생성하는 테스트도, fake↔Orca parity 테스트도 저장소에 **하나도 없다.**

그리고 테스트가 실패 조건을 실제로 검출하는지 직접 mutation 을 넣어 확인했더니 **DESIGN 이
스스로 열거한 mutation 목록 중 2개가 통과했다** — settlement event dedupe 제거(`OK`)와
T2 budget-first guard 순서 뒤집기(`OK`). 후자는 `SKILL.md:2211` 이 "순서가 뒤바뀌면 예산 소진
뒤에도 correction 이 dispatch 된다" 고 명시적으로 경고한 바로 그 결함이며, 실제로 F-001 이
그 상태다. 즉 이 suite 는 F-001 을 구조적으로 검출할 수 없다.

IMPLEMENTATION.md `## Behavior Covered` 는 "missing capability block", "HIGH downstream
revalidation", "normalized Orca/fake trace parity" 를 검증한다고 적었으나 셋 다 해당 테스트가
없다. 이는 G5 다.

blocking 3건이므로 RESULT: FAIL 이다. 다만 결함은 국소적이다 — route 의 T4 분기 한 곳,
Orca adapter 의 실제 primitive 결선, 그리고 누락된 테스트들이며, 이미 통과하는 1744 tests 와
packaging/parity 인프라는 그대로 살릴 수 있다.

## Blocking Findings

### F-001

```text
ID: F-001
Quality Attribute: G1, G2
Severity: CRITICAL
Blocking: YES
```

**Location**: `scripts/deterministic_workflow/routing.py:66-68` (`route`, FINAL_REVIEW FAIL edge)

**Issue**: Final Review FAIL 후 responsible phase correction 을 routing 할 때 **그 phase 의
iteration budget 을 확인하지 않는다.** T2(final budget) guard 는 있으나 T4(phase budget) guard 가
없다:

```python
# T2 is deliberately first on the FAIL edge.
if state["final_review_iterations"] >= state["max_iterations"]: return "ESCALATE"
return "PREPARE_CORRECTION" if state["correction_queue"] else "BLOCK"
```

`PREPARE_CORRECTION` 은 곧바로 `PREPARE_INTENT → EXECUTE_INTENT` 로 이어져 side effect 가
발생한다. 대조적으로 phase-gate FAIL 경로(`routing.py:75-76`)와 revalidation 경로
(`routing.py:73`)에는 budget guard 가 있다 — T4 만 빠졌다.

**Reason (직접 실행해 재현)**:

`route()` 단위 확인 — responsible phase 의 budget 이 0인 상태:
```text
responsible ANALYSIS budget: 0 (phase_iterations=2 of max 2)
route(final FAIL, responsible phase budget EXHAUSTED) -> PREPARE_CORRECTION
```

end-to-end graph 실행 (`phases=("ANALYSIS",)`, `risk=high`, `max_iterations=2`;
ANALYSIS 가 FAIL→PASS 로 gate attempt 2회를 모두 소비한 뒤 Final Review 가 FAIL 하며 ANALYSIS 지목):
```text
phase_iterations[ANALYSIS] = 3 of max 2
remaining_phase_budget      = {'ANALYSIS': -1}
terminal_status / reason    = COMPLETED WORKFLOW_COMPLETED
adapter effect_count        = 8        (기대 5)
budget 소진 후 추가 dispatch: [('ANALYSIS','WORKER','CORRECTION'),
                               ('ANALYSIS','PHASE_REVIEWER','CORRECTION'),
                               ('ANALYSIS','FINAL_REVIEWER','FINAL_REVIEW')]
```

세 가지가 동시에 깨진다: (1) 예산 소진 후 dispatch 가 발생하고, (2) `phase_iterations` 가
`max_iterations` 를 초과하며 `remaining_phase_budget` 이 음수가 되고(DESIGN §3 이 선언한
`0..max` 불변조건 위반), (3) 그럼에도 run 이 `ESCALATED` 가 아니라 **`COMPLETED`** 로 종결된다.

위반 대상:
- **AC 3**: "iteration budget 소진 시 **추가 dispatch 없이** ESCALATED 또는 계약된 terminal state"
- **SKILL.md:2193-2194 (T4)**: "각 responsible phase p에 대해 PHASE_ITERATIONS[p] ==
  max-iterations → STATUS: ESCALATED / REASON: MAX_ITERATIONS_REACHED (phase p)"
- **DESIGN §4 항목 13**: "T4 each responsible phase upstream-first: **phase budget guard then**
  correction Worker/fresh Reviewer"

구조적으로 재검증도 되지 않는다: `VALIDATE` node 는 `START → VALIDATE → ROUTE` 로 run 시작에만
실행되고, 이후 ROUTE 는 `ADVANCE_PHASE`/`APPLY_RESULT` 에서 직접 재진입하므로 음수 budget 이
`validate_state` 로 다시 걸리지 않는다.

**Required Action**: `route()` 의 FINAL_REVIEW FAIL edge 에서 T2 직후, `PREPARE_CORRECTION` 을
반환하기 전에 `correction_queue[correction_index]` 의 `remaining_phase_budget` 을 검사해
소진 시 `ESCALATE`(reason `MAX_ITERATIONS_REACHED`, 해당 phase)를 반환한다. 그리고 이 경로를
검출하는 회귀 테스트를 추가한다(F-003 참조: 현재 suite 는 이 계열을 전혀 검출하지 못한다).

### F-002

```text
ID: F-002
Quality Attribute: G1, G5
Severity: MAJOR
Blocking: YES
```

**Location**: `scripts/deterministic_workflow/orca_adapter.py:21-52`,
`scripts/deterministic_workflow/migration.py`, 테스트 부재

**Issue**: AC 13 "fake adapter 와 Orca adapter 가 동일 scenario 에서 동일 logical transition
trace 를 생성한다" 가 구현되지도, 검증되지도 않았다.

**Reason (직접 확인)**:
- `OrcaAdapter.start()` 는 `self.harness.execute_intent(intent)` 를 호출하고,
  `send/status/interrupt` 는 각각 `harness.send_intent/intent_status/interrupt_intent` 를
  호출한다. **이 네 method 는 `scripts/orca_runtime_harness.py` 에 존재하지 않는다**
  (`grep "def execute_intent\|def send_intent\|def intent_status\|def interrupt_intent"` → 0건).
  즉 adapter 는 기존 harness 가 아니라 가상의 인터페이스에 결선되어 있다.
- DESIGN §9 는 `preflight`, `create_task/create_phase_graph`, `start_worker`, `wait_for_done`,
  `settle_attempt`, `claim_settlement`, `verify_settlement`, `finalize_once`, `account_axes` 를
  composition 하라고 지정했다(이 method 들은 실재한다). 구현에서 이들은 코드가 아니라
  `ORCA_PRIMITIVE_MAP` 이라는 **문자열 dict** 안의 설명으로만 등장한다(`orca_adapter.py:46-52`).
- `OrcaAdapter` 를 생성하는 테스트가 저장소 전체에 **0건**이다
  (`grep -rn "OrcaAdapter" scripts/ --include=*.py` 는 정의 파일만 매치).
- parity 테스트도 **0건**이다. `migration.normalize_trace` 는 존재하지만 유일한 테스트
  (`test_trace_comparator_detects_mutation`)는 손으로 만든 1-entry dict 두 개를 비교할 뿐,
  실제 fake trace 와 Orca trace 를 비교하지 않는다.
- 그럼에도 IMPLEMENTATION.md `## Behavior Covered` 는 "normalized Orca/fake trace parity … 를
  검증한다" 고 적었다 — 근거 없는 주장이다(G5).

**Required Action**: `OrcaAdapter` 를 실재하는 `OrcaRuntimeHarness` primitive 위에 결선하거나
(DESIGN §9 대로), 최소한 그 primitive 를 감싸는 offline fixture 를 만들고, 동일 scenario 를
fake adapter 와 Orca adapter 로 각각 실행해 `normalize_trace` 결과의 동일성을 assert 하는
parity 테스트를 추가한다. 구현 전까지 IMPLEMENTATION.md 의 parity 검증 주장을 철회한다.

### F-003

```text
ID: F-003
Quality Attribute: G5
Severity: MAJOR
Blocking: YES
```

**Location**: `scripts/test_deterministic_workflow_{contracts,graph,adapters}.py`,
`IMPLEMENTATION.md:74` (`## Behavior Covered`)

**Issue**: 테스트가 사용자 요구("테스트가 단순히 구현 결과를 재서술하지 않고, 잘못된 전이/중복
실행/checkpoint 손상 같은 **실패 조건을 실제로 검출하는지** 확인한다")를 충족하지 못하는 구간이
있고, IMPLEMENTATION.md 의 coverage 주장이 실제 테스트보다 넓다.

**Reason (직접 mutation 을 넣어 실행하고 전부 원복함 — md5 로 원복 확인)**:

| Mutation (DESIGN §Testing Strategy 가 스스로 열거한 목록) | 결과 |
| --- | --- |
| M1 T2 guard `>=` → `>` | **FAILED (검출됨)** ✓ |
| M2 phase budget `<= 0` → `< 0` | **FAILED (검출됨)** ✓ |
| M3 FakeAdapter idempotency receipt 재사용 제거 | **FAILED (검출됨)** ✓ |
| M4 `validate_settlement_node` 의 processed-event dedupe 제거 (`if False:`) | **OK — 검출 실패** ✗ |
| M5 T2 budget-first guard 를 finding routing **뒤로** 이동 | **OK — 검출 실패** ✗ |

M5 는 `SKILL.md:2211` 이 "순서가 뒤바뀌면 예산 소진 뒤에도 correction 이 dispatch 된다" 고
명시적으로 경고한 결함이고 DESIGN 의 mutation 목록에도 "T2 after finding mapping" 으로 적혀
있는데, suite 가 이를 전혀 구분하지 못한다. F-001 이 그 계열의 실제 defect 라는 점이 이를
뒷받침한다. M4 는 사용자 검증 항목 "node 재실행 시 side effect 중복 방지" 의 graph-level 경로다
(adapter-level 은 M3 로 덮인다).

근거 없는 coverage 주장 (IMPLEMENTATION.md:74):
- "missing capability block" — capability 부족을 assert 하는 테스트 **없음**
  (`grep` 결과 `capabilities=` 인자 전달만 존재). AC 12 미검증.
- "HIGH downstream revalidation" — 순수 함수 `downstream_revalidation_set` 단위 테스트만 있고
  (`test_downstream_is_high_only_canonical_suffix`), graph 가 실제 revalidation round 를
  수행하는 시나리오 테스트가 **없다**.
- "normalized Orca/fake trace parity" — F-002 참조, **없음**.
- "invalid state/event/terminal transition fail-closed" — state 수준만 있고 malformed/
  out-of-order/post-terminal **event** 를 graph 로 주입하는 테스트가 없다.

또한 `test_phase_pass_does_not_replace_final_pass` 는 이름과 달리 AC 5 의 비대체성
(phase PASS 가 final PASS 없이 COMPLETE 될 수 없고, final PASS 가 결측 phase pass 를 대신할 수
없음)을 assert 하지 않고, final budget 소진 시 ESCALATED 만 확인한다.

**Required Action**: (1) F-001 경로(final FAIL → 소진된 responsible phase)를 검출하는 테스트,
(2) graph-level event replay/dedupe 테스트, (3) missing capability BLOCK + `effect_count == 0`
테스트, (4) HIGH downstream revalidation 시나리오 테스트, (5) malformed/out-of-order/post-terminal
event 테스트를 추가한다. 추가 후 위 M4/M5 mutation 이 실제로 실패하는지 확인한다.
그리고 IMPLEMENTATION.md 의 coverage 서술을 실제 테스트에 맞게 정정한다.

## Non-Blocking Findings

### N-001

```text
ID: N-001
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
```

**Location**: `orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:7`

**Issue**: 설치본 mirror 의 `ports.py` 가 `from scripts.clarification_protocol import ...` 를
그대로 담고 있어 설치본 layout 에서 import 할 수 없다. 설치 Skill 은 `scripts/` 를 복사하지
않기 때문이다(자기 `tools/clarification_protocol.py` 만 갖는다).

**Reason**: 설치 layout 을 복제해 직접 확인했다 —
`import tools.deterministic_workflow.ports` → `ModuleNotFoundError: No module named 'scripts'`
(패키지 `__init__` 자체는 ports 를 import 하지 않아 OK 로 통과한다). byte parity 는 성립하므로
`validate_skills` 는 통과하지만, 설치본의 port protocol 정의는 사용할 수 없는 상태다.
현재 `ports.py` 를 import 하는 모듈이 package 안에 없어 영향이 잠복해 있고, AC 가 설치본에서의
engine 실행을 요구하지는 않으므로 gate 를 막지 않는다.

**Required Action**: mirror 에서 `clarification_protocol` 을 상대 경로/조건부 import 로 해결하거나,
설치본에서 import 가능함을 확인하는 테스트를 추가한다.

### N-002

```text
ID: N-002
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `scripts/e2e_harness.py`(무변경), `scripts/deterministic_workflow/routing.py:14-19`

**Issue**: DESIGN Implementation step 2("Extract pure functions from `e2e_harness.py` to
`routing.py`, add compatibility imports")와 §11("`e2e_harness.py` imports extracted pure
functions for compatibility")이 이행되지 않았다. `e2e_harness.py` 는 전혀 수정되지 않았고
(`git status` 확인), `downstream_revalidation_set` 이 `routing.py:14` 와
`e2e_harness.py:497` 두 곳에 각각 존재한다.

**Reason**: 두 구현의 동작은 현재 일치하고 PLAN 이 legacy oracle 존치를 허용했으므로 AC 위반은
아니다. 다만 PLAN/DESIGN 이 없애려던 중복이 그대로 남아 향후 drift 원인이 된다.

**Required Action**: optional — `e2e_harness.py` 가 `routing.py` 의 함수를 re-export 하도록
바꾸거나, DESIGN 의 해당 단계를 현실에 맞게 정정한다.

### N-003

```text
ID: N-003
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `orca-worker-reviewer-orchestration/SKILL.md` (diff 9줄 추가만)

**Issue**: PLAN W6.1("orchestration Skill 의 phase selection/gate/retry/T0~T5a prose 를
engine-owned declaration 으로 축소하고 Coordinator 가 next action 을 다시 판단하라는 문장을
제거한다")이 이행되지 않았다. SKILL.md 는 `workflow-graph-contract` anchor 와 안내 문단만
추가되었고 T0~T5a 를 포함한 기존 routing prose 는 그대로다.

**Reason**: AC 14(graph contract ↔ Skill parity 자동 검증)는 `validate_workflow_graph_docs.py`
로 충족되었고 실제로 통과한다. prose 축소는 Jira Scope 항목이지만 AC 로 명시되지 않았고, 축소
범위 판단은 후속 작업으로 미룰 수 있다.

**Required Action**: optional — 축소를 후속으로 남긴다면 그 사실을 IMPLEMENTATION.md 에 명시한다.

### N-004

```text
ID: N-004
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
```

**Location**: `scripts/test_deterministic_workflow_adapters.py:29-35`,
`scripts/deterministic_workflow/graph_spec.py:58`

**Issue**: 두 validator 가 DESIGN 이 명세한 것보다 약하다.
(a) core AST scan 은 import 문에서 `orca`/`subprocess` 만 본다. DESIGN §1 은
`terminal`, `session`, `credential`, `claude`, `codex` 및 **field name** 까지 거부하라고 했다.
(b) cycle guard 검사는 `if spec.cycle_guards != CYCLE_GUARDS` 로 **상수 비교**일 뿐, DESIGN §8 의
"every cycle contains ROUTE and is budget-guarded" 라는 실제 cycle 분석이 아니다. 따라서
guard 없는 cycle 을 추가하는 mutation 은 이 규칙으로 검출되지 않는다(다른 규칙에 걸릴 수는 있다).

**Reason**: state 의 `FORBIDDEN_KEYS` 정규식이 runtime handle 을 validation 시점에 실제로 막고
있어 checkpoint 안전성 자체는 확보된다. 따라서 요구사항 위반은 아니고 검사 강도의 문제다.

**Required Action**: optional — AST scan 의 금지 심볼 목록을 DESIGN §1 대로 확장하고, cycle 규칙을
실제 cycle 열거로 구현한다.

## Test Review

**내가 직접 실행한 명령과 결과**

| 명령 | 결과 |
| --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1744 tests in 329.280s` / `OK (skipped=6)` — IMPLEMENTATION.md 주장과 일치, baseline 1725 대비 회귀 없음, skip 6개 그대로 |
| `python3 -m unittest discover -s scripts -p 'test_deterministic_workflow*.py'` | `Ran 19 tests in 0.066s` / `OK` — 주장과 일치 |
| `python3 scripts/validate_skills.py` | `Skill validation PASSED (727 checks)` — 주장과 일치 (baseline 714) |
| `python3 scripts/verify_package.py` | `Package verification PASSED (226 source files)` — 주장과 일치 (baseline 195) |
| `python3 scripts/validate_workflow_graph_docs.py` | `Workflow graph documentation validation PASSED` — 주장과 일치 |
| `git diff --check` | exit 0, 무출력 — 주장과 일치 |
| `git diff --cached --stat` | 비어 있음 — staging 없음, commit/push 없음 |

**UNIT_TEST_STATUS gate (mandatory, risk 무관)**: `IMPLEMENTATION.md:4` 에 `UNIT_TEST_STATUS: PASS`
가 있고, production code 변경과 함께 신규 test module 3개(19 tests)가 추가되었으며 재실행으로
PASS 를 확인했다. **gate 자체는 충족한다.** 다만 그 테스트가 검출해야 할 실패 조건을 놓치는
문제가 F-003 이다.

**Modified Files 대조**: IMPLEMENTATION.md `## Modified Files` 41개 항목과 `git status --short` 의
실제 변경을 대조했다. tracked 수정 7개(`INSTALL.md`, `README.md`, `docs/COMPATIBILITY.md`,
`docs/ROADMAP.md`, `SKILL.md`, `release_manifest.py`, `validate_skills.py`)와 untracked 신규
(engine package, 설치본 mirror, 3개 test module, `validate_workflow_graph_docs.py`,
`requirements-langgraph.txt`, docs 3종, `tools/run_workflow.py`)가 모두 목록에 있다.
**목록에 없는 변경은 발견되지 않았다.** `DESIGN.md` 수정은 CF-3/CF-5 가 명시적으로 지시한 것이며
목록에 정직하게 포함되어 있다.

**범위 밖 변경 없음**: `artifacts/` 의 다른 run 디렉터리, `artifacts/archive/`,
루트 `artifacts/*.md` 는 전부 untracked 상태 그대로이고 수정/삭제되지 않았다.
OS-28~30 자산(`decision_gate.py`, `decision_policy.py`, `clarification_protocol.py`,
`run_logging.py`)과 `e2e_harness.py`, `orca_runtime_harness.py` 는 **무변경**이다 —
ledger schema, clarification v2, audit schema, log column 순서가 그대로임을 뜻한다(AC 15 충족).

**core 의 Orca 격리 (직접 grep)**: `scripts/deterministic_workflow/` 의 core 모듈
(`contracts/state/routing/graph_spec/executor/graph`)에 `orca`/`subprocess`/terminal handle
import 가 없다. `orca_adapter.py` 만 Orca 어휘를 갖는데, 실제로는 harness 를 import 하지 않고
주입된 객체에 위임한다. `state.py:37` 의 `FORBIDDEN_KEYS` 정규식이
`process_handle|terminal_handle|session_handle|credential|access_token|client` 를 어느 깊이에서든
거부하고 `_checkpointable()` 이 `None|bool|int|str|list|dict` 외 타입을 거부한다 —
`test_non_checkpointable_and_unknown_state_fail_closed` 가 이를 검증한다. **checkpoint 안전성은
코드로 강제되고 테스트된다** (검사 강도는 N-004).

**중복 transition engine**: LangGraph graph 밖에 workflow loop 는 없다. `e2e_harness.run_workflow`
는 test-only oracle 로 남아 있고(PLAN 이 허용), 신규 package 는 이를 import 하지 않는다.

**CF 반영 확인**:
- **CF-1 반영** — `test_deterministic_workflow_graph.py:7-14` 가 검증된 import 기반 guard 그대로다.
  `test_guard_is_import_based_for_blocked_import` 가 `builtins.__import__` 를 막아 guard 가
  false 를 반환함을 확인한다. present lane 에서는 19 tests 가 skip 0 으로 실행된다.
- **CF-2 반영** — authoritative unittest runner 사용, skip 6개 유지.
- **CF-3 반영** — `executor.py:137-150` 의 `terminal_node` 가 `terminal_status`/`terminal_reason` 의
  유일한 writer이고, `DESIGN.md:248` 표에 "sole terminal-field writer (CF-3)" 로 기록되었다.
- **CF-4 반영** — `RouteToken` 은 9개 그대로이고 `DESIGN.md:291` 이 항목 4 를 "static structural
  edge chain, not a `route` return branch" 로 정정했다. `graph_spec.validate_graph_spec` 의
  `set(targets) != set(ROUTE_TOKENS)` 총체성 검사와 일관된다.
- **CF-5 반영** — `graph_spec.py:22` 가 `phase_index_monotonic` 을 포함하고 `DESIGN.md:355` 가
  근거를 기록했다(검사 강도는 N-004).

**mutation sensitivity 직접 확인 (요청 항목 11)**: 5개 mutation 을 실제로 넣고 suite 를 돌린 뒤
**세 파일 모두 md5 로 원복을 확인했다**(`routing.py` f6776d1c…, `executor.py` 8be64d9e…,
`fake_adapter.py` 60af2589…). 결과는 F-003 표 참조 — 3개 검출, **2개 미검출**.
원복 후 `validate_skills.py` 재실행으로 source/installed parity 가 유지됨도 확인했다.

**AC 재판정 요약**: AC 1/2/4/6/8/10(부분)/11/14/15/16 은 코드와 테스트로 뒷받침된다.
**AC 3 은 F-001 로 실패**, **AC 13 은 F-002 로 미구현**, **AC 12 와 AC 7 의 graph-level 경로는
F-003 으로 미검증**이다. AC 5 는 구현(`all_phase_passes_current` + `phase_passes` generation)은
있으나 해당 테스트가 이름과 달리 비대체성을 assert 하지 않는다.

**decision gate 형식 검증**: `DECISION_GATE_STATE:` 1개, ```` ```decision-gate ```` record 1개,
`STATUS:`/`UNIT_TEST_STATUS:` 각 1개. record 는 CLEAR 5개 key 만 사용하고 선언과 일치한다.
확정 항목 중 되돌릴 수 없거나 blast radius/monetary/security/privacy/compliance/lock-in 이 참인
것은 없다. **오분류 없음.**

## Final Decision

이 phase gate 판정은 **FAIL** 이다 — blocking 3건(F-001 CRITICAL, F-002 MAJOR, F-003 MAJOR),
non-blocking 4건.

FAIL 의 핵심은 F-001 이다. Final Review FAIL 이 예산 소진된 phase 를 지목했을 때 engine 이
추가 dispatch 를 수행하고, `phase_iterations` 가 `max_iterations` 를 넘어 `remaining_phase_budget`
이 음수가 되며, 그러고도 run 이 `COMPLETED` 로 끝난다. 이는 AC 3 과 SKILL.md T4 의 정면 위반이고
graph 실행으로 재현했다. F-002 는 AC 13 이 실체 없이 남아 있다는 것이고 — Orca adapter 가 존재하지
않는 harness method 에 결선되어 있으며 parity 테스트가 0건이다 — F-003 은 그 두 가지를 왜 아무도
잡지 못했는지 설명한다: DESIGN 이 스스로 열거한 mutation 중 T2 순서 뒤집기와 event dedupe 제거가
suite 를 그대로 통과한다.

동시에 분명히 해둘 것이 있다. **IMPLEMENTATION.md 가 인용한 명령 출력은 전부 사실이었다** —
1744 tests OK, skip 6 유지, 727 checks, 226 files, graph docs PASSED, diff clean. 회귀는 없고,
packaging/parity 인프라와 checkpoint 안전 장치는 실제로 동작하며, CF-1~CF-5 는 코드와 DESIGN
양쪽에 성실히 반영되었다. 부정확한 것은 실행 결과가 아니라 `## Behavior Covered` 의 coverage
서술이다.

수정 범위는 좁다. `route()` 의 FINAL_REVIEW FAIL edge 에 T4 phase-budget guard 한 개를 넣고,
Orca adapter 를 실재 primitive(또는 offline fixture)에 결선한 뒤 parity 테스트를 붙이고,
누락된 5종 테스트를 추가하면 된다. 이미 통과하는 1744 tests, packaging, Skill parity validator 는
그대로 유지된다.

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This review's verdict follows from explicit OS-40 acceptance criteria and the IMPLEMENTATION phase contract applied to evidence I executed directly — the full regression suite, every validator the Worker cited, a reproduced end-to-end budget-guard violation, and five applied-and-reverted mutations; the required corrections are fully determined by the approved DESIGN and SKILL contract, so no item at this boundary needs user authority.",
  "scope": "This phase's own conduct at this iteration."
}
```
