OS-40 PR#28 review remediation — FINAL ADVERSARIAL REVIEW (attempt 1)

=== RUN CONTEXT ===
run_id: run_9e3c67a7824e   (NEW run — 이전 OS-40 run run_0bcf4e7296c9 을 재개하는 것이 아니다)
repository: /Users/<REDACTED:absolute_local_path>/aiAssistedProjects/orca-skills
branch: feat/os-40-langgraph-engine  (PR #28, head 83d4387; DO NOT switch branches)
ARTIFACT_ROOT: artifacts/runs/run_9e3c67a7824e/
risk: high   requested_phases: BUGFIX   max-iterations: 5
worker: claude-opus   reviewer: codex-sol

=== ORIGINAL OBJECTIVE (사용자 원문 요구) ===
PR #28 의 최신 외부 리뷰를 반영한다.

작업 기준:
- 대상 브랜치: feat/os-40-langgraph-engine
- 현재 PR head 와 최신 리뷰 코멘트를 다시 확인한 후 작업한다.
- 기존 OS-40 run 을 재개하지 말고 새로운 run ID 로 시작한다. (이 run 이 그 새 run 이다.)
- 과거 run artifact 는 수정하거나 다시 작성하지 않는다.
  특히 artifacts/runs/run_0bcf4e7296c9/ 이하 전부는 **읽기 전용**이다.
- 이번 작업 범위는 리뷰에서 지적된 1 CRITICAL, 4 MAJOR 의 원인 해결과 회귀 검증이다.

필수 수정 (사용자가 번호로 지정한 5개):
1. Crash-safe idempotency
   - RuntimeStatePort 를 graph 실행 경로에 실제로 연결한다.
   - 외부 실행 전에 stable intent 를 영속적으로 claim 한다.
   - 프로세스 재시작 후 fresh adapter 로 재개해도 기존 receipt 또는 settlement 를 복구해야 한다.
   - 외부 작업 완료와 receipt 저장 사이에 중단되는 crash window 에서도 동일 Task/Dispatch 가
     다시 생성되지 않게 한다.
   - **같은 adapter 객체만 재사용하는 테스트로 완료 처리하지 않는다.**
2. 실행 가능한 launcher
   - run_workflow.py 가 준비 상태만 출력하지 않고 실제 graph 를 실행하도록 구현한다.
   - state 입력, adapter 선택, graph invoke/resume, terminal 결과와 exit code 를 제공한다.
   - Orca 없이 fake adapter 로 정상 workflow 를 실행할 수 있어야 한다.
   - canonical 5-phase workflow 가 LangGraph 기본 recursion limit 때문에 실패하지 않도록
     명시적으로 처리하고 문서화한다.
3. Malformed state 의 fail-closed 처리
   - 불완전하거나 잘못된 초기 state 가 후속 trace 처리에서 예외를 일으키지 않게 한다.
   - compiled graph 가 유효한 BLOCKED/MALFORMED_STATE terminal result 를 반환하게 한다.
   - missing field, unknown field, invalid type, 잘못된 phase/index/budget 조합을 테스트한다.
4. Settlement 무결성
   - canonical settlement payload 로 payload_digest 와 event_id 를 재계산해 검증한다.
   - result, intent/command binding, digest 또는 event ID 가 변조되면 적용 전에 fail closed 한다.
   - FAIL → PASS 변조를 포함한 mutation-sensitive test 를 추가한다.
5. 단일 workflow control plane
   - graph 로 이전된 phase 전이, gate, correction, iteration budget 및 Final Review routing 을
     SKILL.md 의 독립적인 판단 규칙으로 남기지 않는다.
   - Skill 은 해당 결정을 deterministic engine 에 위임하도록 축소한다.
   - 사용자 안내와 안전 규칙은 보존하되 graph 와 경쟁하는 normative routing logic 은 제거하거나
     비권위적 설명으로 전환한다.
   - graph contract 와 Skill 문서의 drift 를 자동 검출한다.

검증 및 완료 (사용자 지정):
- 각 리뷰 finding 을 **실패하는 테스트로 먼저 재현**하고 수정 후 통과를 확인한다.
- full unit tests, dependency-absent lane, Skill validation, package/archive verification,
  source-installed parity 및 `git diff --check` 를 실행한다.
- fake adapter 와 Orca adapter 의 logical trace parity 를 재검증한다.
- 테스트가 같은 in-memory 객체나 구현 자체의 상수만 확인하는 형태가 아닌지 검토한다.
- PR Description 의 결과, 테스트 수치, 알려진 제한사항을 실제 상태에 맞게 갱신한다.
  (commit/push/PR 갱신은 Coordinator 가 phase gate + Final Review PASS 이후 수행한다.
   Worker 는 commit/push/PR 을 하지 않는다.)
- PR merge 와 Jira 상태 변경은 하지 않는다.

=== 외부 리뷰 전문 (PR #28, luminous419, 2026-09-03T16:20:17Z, verdict FAIL) ===
Head: 83d4387253336806c7d806d25282c03ee3120a90 / CI run #102: success / PR remains Draft
"Green CI does not cover the crash boundary, malformed compiled-graph entry, or settlement-integrity
mutations above."

--- C-001 (CRITICAL) — Crash/restart can duplicate external Task/Dispatch creation ---
`OrcaAdapter` keeps idempotency receipts only in process-local `_receipts`/`_events`, while
`execute_intent_node` invokes `adapter.start(intent)` directly. `RuntimeStatePort` is declared but is
not wired into graph execution. If a checkpoint resumes at `EXECUTE_INTENT` with a new adapter
process—or the process dies after `run_existing_task` creates the external effect but before the
in-memory receipt is stored—the same stable intent creates another Task/Dispatch. Existing tests
replay only against the same live adapter instance, so they do not cover the Jira AC:
"interruption/crash 이후 node 재실행이 외부 side effect를 중복 생성하지 않는다."
Required: persist/claim the intent through `RuntimeStatePort` before the external effect, recover the
receipt/settlement by stable identity after restart, and add a crash-window test using a fresh
adapter instance.

--- M-001 (MAJOR) — The shipped launcher does not execute a workflow ---
`tools/run_workflow.py` only imports LangGraph, checks version 0.2.76, and prints "runtime ready."
It does not construct state, select an adapter, call `build_graph`, invoke/resume the graph, or
return a terminal result. README/Skill documentation presents this command as the engine execution
entry point, and the graph contract names it as `launcher`. Therefore the required executable engine
and Orca-independent runnable path are not actually delivered.
Required: provide a real CLI/API execution entry point with at least a fake-adapter scenario,
explicit input/output contract, recursion-limit handling, and terminal exit behavior.

--- M-002 (MAJOR) — Malformed graph state raises instead of failing closed ---
`validate_node` catches validation failures by returning the original malformed dictionary plus
`route_token=<REDACTED:env_secret_pattern> The next `route_node` immediately calls `_trace`, which unconditionally indexes
fields such as `logical_trace`, `current_phase`, and `phase_iterations`. A state missing any of those
fields therefore ends in `KeyError`, not the contracted `BLOCKED/MALFORMED_STATE` terminal state.
Required: normalize malformed input into a valid terminal state or route it through a terminal path
that does not read unvalidated fields; cover missing fields, invalid types, and unknown fields
through compiled-graph tests.

--- M-003 (MAJOR) — Settlement identity/digest is accepted without integrity validation ---
`validate_event` checks the field set and verdict vocabulary but never recomputes or validates
`payload_digest` or `event_id`. A checkpointed settlement can have its `result` changed (for example
FAIL→PASS) while retaining the original IDs/digest and will be applied as authoritative. This
violates closed event identity and deterministic replay requirements.
Required: define the canonical event payload, recompute and compare its digest/ID before applying it,
and add mutation-sensitive tests for result, role/intent binding, digest, and event ID.

--- M-004 (MAJOR) — Prompt-owned routing remains active, creating the parallel control plane OS-40 forbids ---
The Jira scope requires graph-owned routing to replace Coordinator judgment and explicitly calls for
reducing the migrated Skill logic. The PR instead retains the existing phase/FAIL/iteration/
final-review routing prose and lists "SKILL prose not reduced" as a known limitation. That leaves the
prompt loop and LangGraph as two workflow controllers, contrary to the single executable definition
requirement.
Required: make the Skill delegate graph-owned decisions to the engine and remove or clearly demote
duplicated normative routing rules to generated/non-authoritative documentation, with a validator
preventing reintroduction.

=== COORDINATOR 가 직접 확인한 사실 (재확인해도 좋다) ===
- `RuntimeStatePort` 는 `scripts/deterministic_workflow/ports.py:34` 의 Protocol 정의에만 등장하고
  패키지 어디에서도 import/사용되지 않는다. (grep 결과 다른 매치 0건)
- `orca_adapter.py:19-20` 이 `self._receipts` / `self._events` 를 process-local dict 로 보유한다.
- `orca-worker-reviewer-orchestration/tools/run_workflow.py` 는 18줄이며 version 확인 후
  `print("deterministic workflow runtime ready ...")` 만 한다. build_graph / invoke / adapter 없음.
- `executor.py:24-27` `validate_node` 의 실패 경로가 `{**state, "route_token": "BLOCK", ...}` 로
  **원본 malformed dict 를 그대로** 반환한다.
- `contracts.py:120` 이 intent 생성 시 payload_digest 를 계산하지만 `validate_event` 는
  재계산/검증하지 않는다.
- 현재 head 는 local 과 origin 모두 83d4387 로 동일하다.
- CI run #102 success.

=== ENVIRONMENT FACTS ===
- 저장소 test runner 는 **unittest** 다. CI(.github/workflows/ci.yml:37):
      python3 -m unittest discover -s scripts -p 'test_*.py'
  `pytest` 는 scripts/fixtures 의 동일 basename 충돌로 collection 5 errors 로 중단되어
  테스트가 하나도 실행되지 않는다. **pytest 를 쓰지 않는다.**
- 이 브랜치의 현재 baseline (Coordinator 가 직전 run 에서 확인):
      full: `Ran 1761 tests` / `OK (skipped=6)`
      targeted (3 modules): 36 / OK
      dependency-absent lane: 36 / OK (skipped=20), errors=0
      validate_skills 727 checks / verify_package 226 files / graph-doc validator PASSED
  full suite 는 약 5분 30초 걸린다. 충분한 timeout 을 둘 것.
- langgraph 0.2.76 설치됨. `langgraph.__version__` 은 **없다** —
  `importlib.metadata.version("langgraph")` 를 쓴다.
- dependency-absent lane 은 uninstall 이 아니라 import 차단으로 시뮬레이션한다
  (`sys.meta_path` MetaPathFinder 또는 `patch.dict(sys.modules, {"langgraph": None})`).
  guard 는 반드시 **import 기반**이어야 한다 (metadata 는 import 를 막아도 남는다):
      def _langgraph_ok() -> bool:
          try:
              import langgraph; import langgraph.graph
          except ImportError: return False
          try: return importlib.metadata.version("langgraph") == "0.2.76"
          except importlib.metadata.PackageNotFoundError: return False
- `scripts/deterministic_workflow/` 와 `orca-worker-reviewer-orchestration/tools/deterministic_workflow/`
  는 **byte-identical mirror** 여야 한다. production code 를 고치면 mirror 도 함께 갱신한다.
  parity 가 깨지면 validate_skills / verify_package 가 실패한다.

=== RISK PROFILE ===
risk: high (source: explicit)
- BUGFIX phase 는 Worker -> phase Reviewer 로 검증된다.
- Final Adversarial Review 는 필수다.
- specialized phase 이므로 canonical order 가 없고 downstream 집합 D 는 공집합, §17 T5a 는 no-op.

=== QUALITY GATE (profile-first) ===
profile_status: absent   (.orca/quality-profile.yaml 없음 — 정상)
applicable_quality_attributes: (none)   blocking_quality_attributes: (none)
general_gate:
  G1 explicit requirement violation
  G2 result does not work
  G3 severe regression
  G4 data loss / security / irreversible side effect
  G5 missing validation evidence
decision_priority: explicit_requirements > project_quality_attributes(none) >
                   current_phase_contract > minimal_general_gate
non_blocking_by_default: profile 에 없는 일반적 best practice / 취향 / minor improvement 는
                         blocking finding 이 아니다.
verdict_semantics:
  PASS -> RESULT: PASS / PASS WITH NOTES -> RESULT: PASS / FAIL -> RESULT: FAIL /
  BLOCKED -> RESULT: FAIL (신뢰할 수 있는 verdict 에 필요한 evidence 부족)

=== MANDATORY TEST GATE (BUGFIX) ===
BUGFIX phase 는 **Regression Test 가 필수**다. risk 와 무관하게 항상 적용된다.
각 finding 에 대해 **수정 전 코드에서 실패하고 수정 후 통과하는** 회귀 테스트가 있어야 한다
(Before Fix FAIL / After Fix PASS evidence). 확인하지 못했다면 사유와 대체 evidence 를 제시한다.
결과 본문에 다음 한 줄을 반드시 포함한다:
    UNIT_TEST_STATUS: PASS
수행 불가하면 `UNIT_TEST_STATUS: BLOCKED` 이며 이 역시 gate 통과가 아니다. 조용한 생략은 금지다.

=== DECISION GATE (OS-28/OS-29, mandatory) ===
결과 본문에 다음 두 가지가 **각각 정확히 하나** 있어야 한다.
(1) 선언 line:
DECISION_GATE_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT
(2) authority record — fenced JSON, 언어 태그는 반드시 `decision-gate`:
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
  CLEAR 에는 위 5개 key 만 쓴다.
- ASSUMPTION_ALLOWED 는 reason_code + policy_source{role:"supports",kind,locator} + reversibility +
  impact + retraction_condition + assumption + 여섯 safety fact(blast_radius, monetary_cost,
  security, privacy, compliance, long_term_lock_in)를 모두 선언해야 한다. 여섯 중 하나라도 참이거나
  reversibility 가 irreversible 이면 쓸 수 없다.
- NEEDS_INPUT / CONFLICT 는 run 을 종료시킨다. 실제로 사용자 권한이 필요한 경우에만 쓴다.
  모델 확신, Worker/Reviewer 합의, 권고 default, timeout, 무응답은 사용자 권한이 아니다.
- "결정할 것이 없었다" 는 CLEAR 로 **단언**한다. 기록의 부재는 CLEAR 가 아니다.

=== REPOSITORY / SECURITY POLICY ===
금지: git push / force push, branch 삭제, release/deployment, production/infra 변경,
destructive DB operation, 외부 network 접근, 외부 package 임의 다운로드, secret 출력/기록/전송,
`git add -A`, 다른 브랜치로 switch, commit (Coordinator 소관), PR merge, Jira 상태 변경.
artifacts/runs/run_0bcf4e7296c9/ 및 그 밖의 과거 run artifact 는 읽기 전용이다.
=== TASK BOUNDARY ===
current_role: reviewer (FINAL ADVERSARIAL REVIEWER — fresh session, context 상속 없음)
current_phase: final_review
artifact_contract:
  WRITE: artifacts/runs/run_9e3c67a7824e/FINAL_REVIEW.md   (attempt 1)
  READ : artifacts/runs/run_9e3c67a7824e/BUGFIX.md, REVIEW_BUGFIX*.md
         전체 변경: `git status --short` + `git diff` + untracked 파일 직접 열람
         (PR #28 head 83d4387 이후의 working tree 변경이 이번 작업의 delta 다.
          아직 commit 이 없으므로 `git diff 83d4387..HEAD` 는 비어 있다.)

=== ROLE ===
세 번째 역할이 아니다. reviews/common.md 를 그대로 따르는 Reviewer instance 이며 이전 판정
context 를 상속하지 않은 새 session 이다.
**BUGFIX phase gate 가 PASS 였다는 사실을 옳다고 가정하지 않는다.** 그것은 phase Reviewer 의
판정이며 이 attempt 는 그것을 재검증하는 독립 gate 다.
reviews/common.md 를 읽는다. §11 Reviewer delta-context 계약은 여기에 적용되지 않는다 —
Final Adversarial Review 는 자기 checklist 전체를 스스로 수행한다.

=== 이 run 의 목적과 경과 ===
PR #28 의 외부 리뷰(verdict FAIL, 1 CRITICAL + 4 MAJOR)를 반영하는 것이 이 run 의 전부다.
BUGFIX phase 는 iteration 3 에서 PASS 했다.
  iteration 1: C-001/M-001/M-003/M-004 해소, **M-002 미해소로 FAIL**
               (unknown-only 입력이 compiled graph 에서 COMPLETED/3 effects)
  iteration 2: GuardedWorkflowGraph façade 도입 → invoke 는 해소.
               **batch/ainvoke 가 무방비 위임으로 FAIL** (같은 우회, 다른 API)
  iteration 3: __getattr__ 을 deny-by-default 로 반전. langgraph 0.2.76 의 state-ingress
               public callable 14개를 실측 열거해 8개는 façade 가 guard, 6개는 미노출.
               READ_ONLY_PASSTHROUGH 만 위임. bind/pipe/builder/validate 등 우회 경로도 차단.
               invariant 회귀 테스트 추가. → PASS (0 blocking, 0 non-blocking)

=== 외부 리뷰 원문 요구 (이것이 충족 기준이다) ===
C-001 CRITICAL — crash/restart 가 외부 Task/Dispatch 를 중복 생성할 수 있다.
  Required: RuntimeStatePort 로 외부 효과 **이전에** intent 를 persist/claim, 재시작 후 stable
  identity 로 receipt/settlement 복구, **fresh adapter instance 를 쓰는 crash-window 테스트** 추가.
M-001 — shipped launcher 가 workflow 를 실행하지 않는다.
  Required: 실제 CLI/API 실행 진입점, 최소 fake-adapter 시나리오, 명시적 input/output 계약,
  recursion-limit 처리, terminal exit 동작.
M-002 — malformed graph state 가 fail closed 되지 않고 raise 한다.
  Required: malformed 입력을 유효 terminal state 로 정규화하거나 검증 안 된 field 를 읽지 않는
  terminal 경로로 라우팅. **missing / unknown / invalid type 을 compiled-graph 테스트로** 덮을 것.
M-003 — settlement identity/digest 가 무결성 검증 없이 수용된다.
  Required: canonical event payload 정의, 적용 **전** digest/ID 재계산·비교,
  result / role·intent binding / digest / event ID 에 대한 mutation-sensitive 테스트.
M-004 — prompt-owned routing 이 살아 있어 OS-40 이 금지한 병렬 control plane 을 만든다.
  Required: Skill 이 graph-owned 결정을 engine 에 위임하게 하고, 중복된 normative routing 을
  제거하거나 generated/non-authoritative 문서로 명확히 강등, **재도입을 막는 validator**.

사용자가 추가로 명시한 검증 요구:
- 각 finding 을 **실패하는 테스트로 먼저 재현**하고 수정 후 통과 확인.
- full unit tests / dependency-absent lane / Skill validation / package·archive verification /
  source-installed parity / `git diff --check`.
- fake adapter 와 Orca adapter 의 logical trace parity 재검증.
- **테스트가 같은 in-memory 객체나 구현 자체의 상수만 확인하는 형태가 아닌지 검토.**

=== REVIEW CHECKLIST (blocking finding 을 찾는 탐색 축) ===
A objective alignment        외부 리뷰 5건이 실제로 원인 수준에서 해소되었는가
B cross-phase consistency    BUGFIX.md 서술과 실제 코드/테스트가 일치하는가
C contract vs implementation 문서화된 계약과 코드가 일치하는가
D implementation vs tests    test 가 실제 위험을 검증하는가, 통과를 위해 약화되지 않았는가
                             (사용자 요구: tautology / same-instance / 상수 확인 형태가 아닌지)
E docs vs behavior           문서가 실제 동작을 설명하는가 (INSTALL/README/SKILL 포함)
F lifecycle state machine    상태 전이와 counter 가 문서와 코드에서 동일한가
G security destructive       파괴적 동작, secret, 범위 밖 파일 변경이 없는가.
                             **특히 artifacts/runs/run_0bcf4e7296c9/ 등 과거 run artifact 가
                             수정되지 않았는지 `git status` 로 확인한다** (사용자 명시 제약)
H over-engineering           요청되지 않은 abstraction 이나 범위 확대가 없는가.
                             deny-by-default façade 가 정당한 범위인가, 과잉 차단은 없는가
I hidden coupling            의도치 않은 공유 자산/외부 계약 변경이 없는가
J decision provenance        미해결 decision, 승인되지 않은 고영향 가정, decision drift 가 없는가
                             (artifacts/runs/run_9e3c67a7824e/decision_ledger 확인)

반드시 **직접 실행**해 확인할 것 (인용된 결과를 신뢰하지 말 것):
  python3 -m unittest discover -s scripts -p 'test_*.py'     # 약 5분 30초. 기대 1819 / OK / skipped=6
  dependency-absent lane (import 차단)                        # errors=0
  python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo --json
  python3 scripts/validate_skills.py / verify_package.py / validate_workflow_graph_docs.py
  git diff --check ; git status --short
  source ↔ installed mirror byte parity
  C-001: fresh adapter + 별도 store 로 재개하는 경로를 직접 재현
  M-002: unknown-only 를 **모든** state-ingress API 로 넣어 BLOCKED/0 effects 확인
  M-003: result 를 FAIL→PASS 로 바꾸고 ID/digest 를 그대로 둔 event 주입 → fail closed 확인
  M-004: drift 를 실제로 넣어 validator 가 탐지하는지 확인하고 **원복**(해시 확인)

Final Reviewer 는 무한한 generic quality checklist 를 생성하지 않는다. project profile 에 없는
일반적 improvement 나 non-blocking finding 만 존재하면 verdict 는 PASS(WITH NOTES) 이며
그것만으로 correction loop 를 시작하지 않는다.

=== FINDING CONTRACT ===
Blocking finding 은 §11 형식에 `Responsible Phase` 를 추가한다.
이 run 의 requested phase 는 **bugfix 하나뿐**이므로 모든 blocking finding 의
Responsible Phase 는 `bugfix` 다 (specialized run 은 canonical order 가 없다).
ID / Quality Attribute (G1..G5|NONE) / Severity / Blocking / Responsible Phase /
Location / Issue / Reason·Evidence / Required Action

=== RESULT CONTRACT ===
artifacts/runs/run_9e3c67a7824e/FINAL_REVIEW.md 에:
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
Final Adversarial Reviewer 도 자신이 찾은 결함을 직접 고치지 않는다. FINAL_REVIEW.md 만 쓴다.

=== COMPLETION REPORTING ===
dispatch preamble 대로 worker_done. --outcome succeeded (RESULT: FAIL 이어도 succeeded),
--files-modified 에 artifacts/runs/run_9e3c67a7824e/FINAL_REVIEW.md,
body 에 RESULT / REVIEW_VERDICT / DECISION_GATE_STATE / blocking finding 개수.