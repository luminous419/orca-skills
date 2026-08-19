# Step 5 — Real GLM/Gemma Smoke Test 최종 보고

> 이 문서는 실행 당시의 evidence snapshot이다. 이후 release-readiness 판정과 lifecycle
> clarification은 [`COMPATIBILITY.md`](COMPATIBILITY.md)를 따른다.

> 실행 일자: 2026-08-20
> 대상 Skill: `orca-worker-reviewer-orchestration`
> Worker: `claude-glm` / Reviewer: `claude-gemma` / 실제 Orca runtime / 실제 Run·Task·Dispatch·`worker_done` provenance
> fake agent 사용 금지, 외부 LLM/API/network 사용 금지, production repo/branch/environment 사용 금지

## 1. 실행 환경
- Machine: 회사 개발 노트북 (darwin 25.4.0)
- Orca CLI: `/Users/<user>/Applications/Orca.app/Contents/Resources/bin/orca`
- Worker: `claude-glm` → `/Users/<user>/script/claude-glm`
  (Claude Code CLI wrapper, `CLAUDE_CONFIG_DIR=~/.claude-glm`, `--dangerously-skip-permissions`)
- Reviewer: `claude-gemma` → `/Users/<user>/script/claude-gemma`
  (동일 구조, `~/.claude-gemma`)
- Fixture: `/tmp/orca-step5-fixture` — Python stdlib 전용 calculator 프로젝트 + `unittest`
  Orca repo 등록(`a6a11cbd-1964-4bc4-833e-1f3449abfde7`) 후 `step5-smoke` worktree에서 실행.
- Coordinator terminal: `term_13b3d86a-fec2-4622-bc03-2af768d78ca5` (bash, worktree 내)

## 2. Orca version
- 설치된 runtime: **`1.4.178-rc.2`** (`runtimeState: ready`, `runtimeId: 5f7d614a-57b9-4cfc-8831-90a2b38b3831`)
- repo compatibility gate pin: **`1.4.184`** → **버전 불일치** (설치 버전이 더 오래됨)
- 단, 설치 binary의 version-matched `orchestration`/`orca-cli` guide grammar가
  pinned grammar(orchestration 9개 snippet + orca-cli 3개 snippet)와 **모두 일치**.
  real-agent smoke test는 설치 runtime의 guide를 source of truth로 사용하므로 진행 가능.
  deterministic Step 4 harness(`validate_orca_contract`의 strict version equality)는
  이 runtime을 거부함 — 이는 Step 5로 인한 것이 아니라 환경 조건.

## 3. command resolution
- `command -v claude-glm` → `/Users/<user>/script/claude-glm`
- `command -v claude-gemma` → `/Users/<user>/script/claude-gemma`
- 서로 다른 executable (inode `208060049` vs `208060223`, sha 상이). 동일 executable 아님.

## 4. scenario별 결과

| Scenario | Phase | Result | Iteration |
 |---|---|---|---|
| A | ANALYSIS | **PASS** | 1 |
| B | DESIGN | **PASS** | 1 |
| C | IMPLEMENTATION | **PASS** | 1 |
| D | BUGFIX | **PASS** | 1 |
| E | DESIGN→IMPLEMENTATION | **PASS** | DESIGN 2 / IMPL 1 |
| FAIL→correction→PASS | (E DESIGN) | **PASS** | R1 RESOLVED |

전 시나리오 PASS. BLOCKED / ESCALATED 없음.

## 5. phase별 iteration 수
- A ANALYSIS: 1
- B DESIGN: 1
- C IMPLEMENTATION: 1
- D BUGFIX: 1
- E DESIGN: 2 (iter1 FAIL → iter2 PASS)
- E IMPLEMENTATION: 1

## 6. 실제 Gemma blocking finding 사례
- Scenario E DESIGN iter1, finding **R1 (MAJOR)**:
  `pow(x)`가 음수 base + 분수 지수일 때 `complex`를 반환하여 float accumulator contract를 위반.
  `div(0)`가 real-domain invalid 연산에 대해 raise하는 것과 불일치.
- Required action: 결과가 complex가 될 조건에서 `ValueError` raise.

## 7. GLM correction 결과
- iter2: `DESIGN_E.md` 수정
    - `self.value < 0 and not float(x).is_integer()`일 때 mutation/record 이전에 `ValueError` raise
    - `self.value` 불변, history 기록 안 함
    - 정수 지수는 음수 base 허용
    - regression test 2종 추가
      (`test_pow_negative_base_fractional_raises`, `test_pow_negative_base_integer_exponent_allowed`)
- **R1 = RESOLVED**

## 8. Unit Test / regression test 결과
- C: `python3 -m unittest test_calc` → 17 tests OK (신규 12 + 기존 5)
- D: regression test `test_mul_zero_yields_zero_and_is_recorded`
  — Before Fix FAIL(`5 != 0`) → After Fix PASS, suite 18/18
- E IMPL: 36 tests OK (신규 18 + 기존 18), R1 guard test 포함
- 모든 테스트는 Python stdlib `unittest`만 사용, 외부 package/네트워크 없음.

## 9. Run / Task / Dispatch provenance (real Orca state)

| Run | Scenario | Tasks |
 |---|---|---|
| `run_e101580c47b0` | A | 2 completed |
| `run_e1f26e0901a8` | B | 2 completed |
| `run_c79c17cf89ac` | C | 2 completed |
| `run_b9e7236e73d4` | D | 2 completed |
| `run_a06f823d3d2b` | E | 6 completed (design-w/r, correction-w, re-review, impl-w/r) |

모든 worker/reviewer가 실제 Orca `dispatch --inject`로 dispatch되고
`worker_done`(`outcome succeeded`)로 완료됨. fake agent 미사용.

### Scenario A — ANALYSIS (PASS, iteration 1)
- Run: `run_e101580c47b0`
- Worker: `claude-glm`, task `task_54f3e52c5366`, dispatch `ctx_984b57dce3d6`, outcome succeeded
- Reviewer: `claude-gemma`, task `task_f03bcc7216ad`, dispatch `ctx_beb1bc430d96`, outcome succeeded
- Review RESULT: PASS (gemma가 `calc/core.py`, `test_calc.py`, `ANALYSIS.md` 직접 확인)
- Artifacts: `ANALYSIS.md` (6762 B), `REVIEW_ANALYSIS.md`

### Scenario B — DESIGN (PASS, iteration 1)
- Run: `run_e1f26e0901a8`
- Worker: `claude-glm`, task `task_37be6d80cd79`, dispatch `ctx_6bb65dcdc1db`, succeeded → `DESIGN.md`
- Reviewer: `claude-gemma`, task `task_d96ae929af2f`, dispatch `ctx_b71bca057103`, succeeded → `REVIEW_DESIGN.md`
- Review RESULT: PASS (non-blocking low finding 2건)

### Scenario C — IMPLEMENTATION (PASS, iteration 1)
- Run: `run_c79c17cf89ac`
- Worker: `claude-glm`, task `task_f1a1b3855381`, dispatch `ctx_5c4e6c0309bf`, succeeded
  → `calc/core.py` + `test_calc.py` 수정, 12 new tests(17 total), `python3 -m unittest test_calc` OK
  → design의 name collision(`self.history`가 `history()` 가림)을 스스로 발견해 `self._history`로 수정, public contract 유지
- Reviewer: `claude-gemma`, task `task_241ea16d4974`, dispatch `ctx_f1bbdf947a85`, succeeded
  → `REVIEW_IMPLEMENTATION.md` RESULT: PASS (gemma가 `git diff` + `python3 -m unittest test_calc -v` 직접 실행, 17 pass)

### Scenario D — BUGFIX (PASS, iteration 1)
- 의도적 bug commit: `mul(0)` early-return guard → 누적값(5) 반환, 0이 아님. 기존 17 테스트는 green (untested path).
- Run: `run_b9e7236e73d4`
- Worker: `claude-glm`, task `task_6a288c908988`, dispatch `ctx_6aafcdcc806c`, succeeded
  → root cause: `if x == 0: return self.value` guard, 최소 2-line 삭제
  → regression test 추가, Before Fix FAIL(`5 != 0`) → After Fix PASS, suite 18/18
- Reviewer: `claude-gemma`, task `task_931c3a345f1d`, dispatch `ctx_34503a2d4b9a`, succeeded
  → `REVIEW_BUGFIX.md` RESULT: PASS (gemma가 `git diff` + full suite + standalone repro로 buggy HEAD vs fixed 비교)

### Scenario E — DESIGN→IMPLEMENTATION (PASS)
- Run: `run_a06f823d3d2b`
- DESIGN iter1 Worker: `claude-glm`, task `task_4cbdd06a0229`, dispatch `ctx_31136fd217d4`, succeeded → `DESIGN_E.md`
- DESIGN iter1 Reviewer: `claude-gemma`, task `task_6afb9ac5d563`, dispatch `ctx_0e55f8723f5e`, succeeded
  → `REVIEW_DESIGN_E.md` RESULT: **FAIL**, blocking finding R1(MAJOR)
- CORRECTION iter2 Worker: `claude-glm`, task `task_0c291ba4c3f1`, dispatch `ctx_fc12aa6c5432`, succeeded → R1 = RESOLVED
- RE-REVIEW iter2 Reviewer: `claude-gemma`, task `task_ab6d2ed6fb7a`, dispatch `ctx_a5601826c23b`, succeeded
  → `REVIEW_DESIGN_E.md` RESULT: **PASS** (R1 RESOLVED, 신규 finding 없음)
- IMPLEMENTATION Worker: `claude-glm`, task `task_9152abd52bdf`, dispatch `ctx_85a12e91a7f2`, succeeded
  → `calc/core.py` + `test_calc.py`, pow(x)+last() + R1 guard, 18 new tests, 36 tests OK
- IMPLEMENTATION Reviewer: `claude-gemma`, task `task_0b43d7f1aa6b`, dispatch `ctx_1dac033bcfef`, succeeded
  → `REVIEW_IMPLEMENTATION_E.md` RESULT: PASS (gemma가 `git diff` + `python3 -m unittest test_calc -v` 직접 실행, 36 pass)
- multi-phase contract: DESIGN PASS 전에 IMPLEMENTATION으로 이동하지 않았고, 승인된 design이 조용히 변경되지 않음.

## 10. worker_done 및 lifecycle 결과
- 모든 `worker_done`은 명시적 `outcome=succeeded`, 일치하는 `taskId`/`dispatchId`.
  Orca lifecycle rejection 없음.
- **Lifecycle finding**:
    - valid `worker_done` 시 dispatch는 자동 `completed` 됨.
    - 이후 `worker-show` / `worker-release`는 `dispatch_not_found` 반환.
    - **worker terminal은 자동 release되지 않고 idle 상태로 잔존**
      (최종 14개 terminal 잔존 확인).
    - 정리는 `orca terminal stop --worktree <wt>`로 수행.
- 모든 settled worker를 `release:auto-on-worker_done`(dispatch) +
  `terminal stop`(orphaned terminal cleanup)로 account.
- Worker/Reviewer 역할 교환 없음. 동일 역할 correction/re-review는 별도 dispatch.

## 11. fake-agent 대비 실제 모델에서 발견된 차이
1. **Workspace trust prompt**: fresh `CLAUDE_CONFIG_DIR` 첫 실행 시 Claude Code가 trust prompt 표시
   (`blockedReason: codex-trust-workspace`). Enter로 "1. Yes, I trust this folder" 확인 필요.
   fake agent에는 없는 단계.
2. **Lifecycle API 동작**: fake-agent harness는 `worker_done` 후 ack 이전에 `worker-release`가 성공하지만,
   실제 injected Claude Code worker는 dispatch가 자동 완료되어 `worker-release`가 `dispatch_not_found`.
   terminal도 잔존.
3. **`check --wait` 출력**: keepalive heartbeat NDJSON 라인이 섞여 나옴.
   파싱 시 `_keepalive` 라인 필터링 필요.
4. **비결정성**: 실제 모델은 자율 추론.
    - GLM이 design의 name collision(`self.history`가 `history()` 가림)을 스스로 발견·수정.
    - Gemma가 bugfix 검증 시 standalone 재현 스크립트를 직접 작성해 buggy HEAD vs fixed 비교까지 수행.

## 12. GLM Worker 강점/약점
- **강점**:
    - 정확한 repository 분석, 구현 가능한 최소 설계
    - 자체적으로 design 결함(name collision) 발견·수정
    - 풍부한 test coverage
    - bugfix에서 명확한 root cause + Before/After 증명
    - correction 시 finding 해결을 design에 정확히 반영
- **약점**:
    - design 단계에서 `pow`의 complex 반환을 "자연스러운 `**`"으로 방치해 reviewer에게 R1을 유발
      — real-domain contract 사전 고려 부족
    - `pow(0)` 타입(int 1 vs 1.0) 등 사소한 결정을 "flagged decisions"로 위임하는 경향

## 13. Gemma Reviewer 강점/약점
- **강점**:
    - 진정한 독립 검증 — Worker 요약을 신뢰하지 않고 직접 source/diff/테스트 실행
    - bugfix에서 standalone repro로 Before/After까지 입증
    - R1 같은 의미 있는 MAJOR finding을 근거 있게 제기
    - finding identity(R1)를 iteration 간 보존하고 RESOLVED 추적
    - production code/artifact 수정 없음 (Review only)
- **약점**:
    - Scenario E IMPL에서 git diff에 포함된 Scenario D의 `mul(0)` fix를 E Worker의 행위로
      Attribution한 non-blocking note (사소)
    - design iter1의 testing 전략이 "documenting complex"를 해법으로 제안한 것을
      R1에서 정정하기는 했으나 초기 검증에서 더 일찍 flag할 수 있었음

## 14. Skill에서 수정이 필요한 부분 (후보, 별도 PR)
1. **Completed Worker Lifecycle 보완**:
   `orca-worker-reviewer-orchestration` §6은 `worker-release`를 `worker_done` 후 호출 가능하다고 가정하나,
   injected Claude Code worker는 dispatch가 자동 완료되어 `worker-release`가 `dispatch_not_found`이고
   terminal이 잔존함.
   "release before ack" 또는 "orphaned idle terminal은 `terminal stop --worktree`로 정리" 명시 필요.
2. **Workspace trust prompt**: custom Claude Code wrapper(`CLAUDE_CONFIG_DIR` 분리) 첫 실행 시
   trust prompt 처리 절차를 placement 절차에 추가.
3. **`check --wait` 출력 파싱**: keepalive heartbeat 라인 처리 안내 추가
   (또는 coordinator가 NDJSON을 견딜 수 있도록 script 예시 보완).
4. **Compatibility gate**: `1.4.178-rc.2` 설치 환경에서는 `orca_runtime_harness.py`의
   strict version equality가 Step 4 통합 suite를 거부.
   guide grammar가 일치하면 통과시키는 완화(또는 pin 갱신 절차) 검토.
   단, 이는 Step 5 smoke test 결과를 반영할 때 별도 branch/PR로 분리 필요.

## 15. stable release 전 남은 blocker
- **Compatibility gate 버전 불일치**: 설치 Orca가 `1.4.178-rc.2`로 gate pin `1.4.184`와 다름.
  real-agent smoke는 통과했으나, deterministic Step 4 suite를 이 환경에서 재실행하려면
  gate pin을 설치 버전에 맞추거나(별도 검증) Orca를 `1.4.184`로 업데이트해야 함.
- **Lifecycle 정책 문서 부재**: 위 14.1 항목이 Skill에 명시되기 전까지,
  real Claude Code worker 사용 시 terminal 잔존/정리 동작이 Skill contract와 불일치.
- 이외 real GLM/Gemma 기능 검증(ANALYSIS/DESIGN/IMPLEMENTATION/BUGFIX/multi-phase/correction-loop)은
  **모두 PASS**하여 stable release를 막는 기능적 blocker는 없음.

 ---

## Repository regression validation (orca-skills repo)
- orca-skills tracked changes: **NONE**
  (사전 존재 untracked `.idea/`, `issue_update.txt`, `orca-skills.iml` 외 변경 없음)
- `git diff --check`: clean
- `python3 scripts/validate_skills.py`: **PASSED** (252 checks)
- `python3 -m unittest discover -s scripts -p 'test_*.py'`: **Ran 49 tests, OK (skipped=1)**
  (skipped = `test_orca_runtime`, opt-in `ORCA_RUNTIME_TEST=1`;
  `test_orca_runtime_contract`은 offline/mock으로 PASS)
- `python3 scripts/verify_package.py`: **PASSED** (58 source files)
- Step 5는 orca-skills repo를 수정하거나 깨뜨리지 않음.

## Cleanup
- `orca worktree rm step5-smoke`: ok
- `rm -rf /tmp/orca-step5-fixture`: ok
- `orca repo rm`: 이 Orca 버전에 미지원 (only `repo add`).
  fixture repo registration `a6a11cbd-...` 는 삭제된 path를 가리키는 cosmetic orphan로 잔존
  (Orca UI에서 제거 가능).
- `orca terminal stop --worktree`: 잔존하던 14개 idle worker/reviewer terminal 전체 정리.

## repository 변경 정책 준수
- Step 5는 smoke test로 `orca-skills`의 Skill/policy/runtime implementation을 수정하지 않음
  (추적된 변경 0건, `git diff --check` clean, validator/deterministic test 전 통과).
- 개선점(14항)은 finding/evidence로만 정리했고 별도 branch/PR로 분리 필요.
- 실행 결과를 `COMPATIBILITY.md`/`CHANGELOG.md`에 반영할 가치는 있으나,
  **별도 branch + Draft PR**에서 처리해야 함 (이 session에서는 반영하지 않음).
- disposable workspace(worktree + `/tmp` fixture)는 정리 완료.

## 기대되는 다음 상태
Step 5가 성공했으므로 `COMPATIBILITY.md`의 다음 상태를 갱신할 근거가 확보됨.

 ```text
 claude-glm Worker       BLOCKED / NOT YET VERIFIED → VERIFIED
 claude-gemma Reviewer   BLOCKED / NOT YET VERIFIED → VERIFIED
 Real GLM/Gemma smoke    BLOCKED / NOT YET VERIFIED → VERIFIED
 ```

## 결론
실제 `claude-glm` Worker와 `claude-gemma` Reviewer가 real Orca Run/Task/Dispatch/`worker_done`
provenance 하에 모든 요구 시나리오에서 정상 동작함을 검증.
Reviewer 독립성과 FAIL→correction→PASS 루프, mandatory test gate, multi-phase 선후행 제약이
실제 모델에서도 유지됨.
`COMPATIBILITY.md`의 `claude-glm Worker` / `claude-gemma Reviewer` / `Real GLM/Gemma smoke`
항목을 `VERIFIED`로 갱신할 근거가 확보되었고,
stable release는 위 15항의 문서/환경 blocker만 해소되면 가능.
