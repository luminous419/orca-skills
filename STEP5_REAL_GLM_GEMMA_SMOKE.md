# Step 5 — Real GLM/Gemma Smoke Test

이 문서는 회사 개발 노트북에서 `orca-worker-reviewer-orchestration`을 실제 GLM/Gemma agent command로 smoke test하기 위한 실행 프롬프트다.

## 실행 전 준비

repository를 최신화한다.

```bash
git checkout main
git pull --ff-only
```

다음 command가 PATH에서 서로 다른 executable로 resolve되는지 확인한다.

```bash
command -v claude-glm
command -v claude-gemma
```

둘 중 하나라도 resolve되지 않으면 이번 Step 5를 시작하지 말고 다음으로 종료한다.

```text
STATUS: BLOCKED
REASON: AGENT_COMMAND_NOT_FOUND
```

필요하다면 회사 노트북에서 실제 script가 있는 directory를 현재 shell의 `PATH`에 먼저 추가한 뒤 다시 확인한다. repository 문서나 Skill에는 사용자별 absolute path를 기록하지 않는다.

## Claude Code에 전달할 프롬프트

아래 내용을 회사 노트북의 Claude Code에 그대로 전달한다.

---

현재 `orca-skills` repository의 `main`을 기준으로 Step 5 실제 모델 smoke test를 수행해줘.

이번 작업의 목적은 `orca-worker-reviewer-orchestration`이 실제 회사 환경의 두 agent command로 정상 동작하는지 검증하는 것이다.

- Worker: `claude-glm`
- Reviewer: `claude-gemma`
- 실제 Orca runtime 사용
- 실제 Run / Task / Dispatch / worker_done provenance 필수
- fake agent 사용 금지
- 외부 LLM/API/network 사용 금지
- production repository, production branch, production environment 사용 금지

이번 단계는 기능 개발이 아니라 smoke test다. 새로운 orchestration framework나 runtime engine을 만들지 마라.

### 1. Preflight

가장 먼저 다음을 확인한다.

```bash
command -v claude-glm
command -v claude-gemma
```

둘 중 하나라도 PATH에서 resolve되지 않으면 즉시 종료한다.

```text
STATUS: BLOCKED
REASON: AGENT_COMMAND_NOT_FOUND
```

두 command가 같은 executable을 가리켜도 진행하지 않는다.

Orca도 확인한다.

- Orca CLI executable resolve
- Orca version
- runtime ready 여부
- repository registration 상태

그리고 실제 Orca command를 사용하기 전에 현재 설치된 binary의 version-matched guide를 반드시 읽는다.

```text
<ORCA> skills get orchestration
<ORCA> skills get orca-cli
```

현재 repository의 runtime integration compatibility gate가 설치된 Orca version과 맞는지도 확인한다.

Orca command/subcommand/flag는 기억이나 과거 예시로 추측하지 말고 현재 version-matched guide를 source of truth로 사용한다.

### 2. 안전한 테스트 workspace 준비

실제 업무 repository나 production branch를 테스트 대상으로 사용하지 않는다.

작은 disposable repository 또는 disposable worktree를 만든다. 외부 package 설치가 없어야 한다.

fixture는 Python standard library만으로 테스트 가능한 아주 작은 프로젝트면 충분하다. 예를 들어 calculator 형태의 모듈과 `unittest` 기반 테스트를 사용할 수 있다.

테스트가 끝난 뒤 disposable workspace만 정리할 수 있어야 한다.

### 3. 공통 orchestration contract

각 scenario는 다음 lifecycle을 실제 Orca state로 남겨야 한다.

```text
Run
→ Worker Task / Dispatch
→ claude-glm execution
→ worker_done
→ Reviewer Task / Dispatch
→ claude-gemma execution
→ worker_done
→ PASS 또는 FAIL
```

Reviewer FAIL이면:

```text
Blocking Finding
→ 새 Worker correction Task / Dispatch
→ finding resolution
→ 새 Reviewer re-review Task / Dispatch
→ PASS 또는 다음 iteration
```

모든 accepted `worker_done` 이후 settled worker lifecycle은 현재 guide에 따라 다음 중 하나로 account한다.

- immediate reuse
- explicit retain
- release

완료된 worker를 이유 없이 live 상태로 남기지 않는다.

같은 Claude session에서 Worker와 Reviewer 역할을 바꾸지 않는다.

```text
Worker Session != Reviewer Session
```

동일 역할의 correction/re-review에서는 기존 session reuse가 가능하다.

### 4. Scenario A — ANALYSIS

작은 fixture repository를 대상으로 다음 phase를 실행한다.

```text
phases=analysis
worker=claude-glm
reviewer=claude-gemma
```

GLM은 실제 repository를 분석해야 한다.
Gemma는 Worker summary만 신뢰하지 않고 가능한 범위에서 repository evidence를 직접 확인해야 한다.

확인:

- Run 존재
- Worker Task / Dispatch 존재
- Reviewer Task / Dispatch 존재
- worker_done settlement
- Reviewer PASS/FAIL
- 최종 phase 상태

### 5. Scenario B — DESIGN

fixture에 작은 기능 요구사항을 정의하고 실행한다.

```text
phases=design
worker=claude-glm
reviewer=claude-gemma
```

확인:

- design artifact가 구체적인가
- repository 구조와 일치하는가
- interface/data flow/error handling/testing strategy가 타당한가
- Gemma가 독립적으로 검증하는가

### 6. Scenario C — IMPLEMENTATION

fixture에 작고 명확한 기능 하나를 구현하게 한다.

```text
phases=implementation
worker=claude-glm
reviewer=claude-gemma
```

필수 gate:

```text
Production Code Change
+
Unit Test Add / Modify
+
Unit Test Execution
+
PASS
```

Gemma는 실제 source/diff/test/test result를 가능한 범위에서 직접 확인한다.

Unit Test가 없거나 meaningful하지 않거나 실행하지 않았거나 실패했다면 PASS시키지 않는다.

### 7. Scenario D — BUGFIX

의도적인 작은 bug를 가진 fixture를 사용한다.

```text
phases=bugfix
worker=claude-glm
reviewer=claude-gemma
```

필수 검증:

- root cause evidence
- symptom masking 여부
- 최소 범위 fix
- regression test 작성
- regression test 실행
- 가능하면 `Before Fix FAIL → After Fix PASS`
- 관련 기존 테스트 PASS

Gemma는 `reviews/bugfix.md` 기준으로 독립 검증한다.

### 8. Scenario E — DESIGN → IMPLEMENTATION

multi-phase를 실제로 실행한다.

```text
phases=design,implementation
worker=claude-glm
reviewer=claude-gemma
```

확인:

```text
DESIGN
→ Reviewer PASS
→ approved design output 보존/전달
→ IMPLEMENTATION
→ Reviewer PASS
```

DESIGN이 PASS하기 전에 IMPLEMENTATION으로 이동하면 실패다.
IMPLEMENTATION 과정에서 승인된 DESIGN을 조용히 변경하면 안 된다. 선행 phase 변경이 필요하면 기존 Skill contract에 맞게 명시적으로 처리한다.

### 9. 실제 FAIL → correction → PASS 경로

최소 하나의 scenario에서 자연스럽게 Reviewer blocking finding이 발생하도록 테스트 fixture 또는 acceptance criterion을 구성한다.

Reviewer에게 무조건 FAIL하라고 지시하지 않는다.

검증해야 하는 경로:

```text
GLM Worker COMPLETE
→ Gemma Reviewer FAIL
→ Blocking Finding R1
→ GLM correction
→ R1 = RESOLVED | DISPUTED | BLOCKED
→ Gemma re-review
→ PASS
```

Reviewer는 finding을 직접 수정하지 않는다.

finding identity와 resolution trace가 iteration 사이에서 유지되는지 확인한다.

### 10. Reviewer 독립성

Reviewer는 Worker result text만 읽고 판정하지 않는다.

가능한 범위에서 직접 확인한다.

- original requirement
- repository source
- artifact
- git diff
- tests
- test result

Reviewer는 production code나 artifact를 수정하지 않는다.
Reviewer가 수정했다면 smoke test failure로 기록한다.

### 11. Agent command 실행 정책

agent command는 PATH 기반으로만 사용한다.

```text
claude-glm --dangerously-skip-permissions
claude-gemma --dangerously-skip-permissions
```

repository나 Skill에 사용자별 absolute path를 추가하지 않는다.

실행 중 command resolution이 사라지면:

```text
STATUS: BLOCKED
REASON: AGENT_COMMAND_NOT_FOUND
```

### 12. iteration 및 실패 처리

각 phase는 기존 Skill의 `max-iterations` 정책을 따른다.

PASS하지 못하고 최대 iteration에 도달하면:

```text
STATUS: ESCALATED
```

무한 retry 금지.

worker/reviewer process unexpected exit, malformed output, worker_done 누락을 성공으로 처리하지 않는다.

Orca wait timeout 자체는 worker failure로 간주하지 않고 현재 version-matched guide의 recovery/check 절차를 따른다.

### 13. 결과 artifact

secret이나 회사 내부 소스 전체를 dump하지 않는 범위에서 각 scenario별 요약 artifact를 남긴다.

최소 항목:

```text
scenario
Orca version
Worker command
Reviewer command
Run ID
Task IDs
Dispatch IDs
phase
iteration count
Reviewer result
blocking findings
Worker resolution
unit/regression test command and result
worker lifecycle action
final status
elapsed time
```

절대 기록하지 말 것:

- API key
- token
- credential
- internal endpoint secret
- 전체 회사 repository source dump

### 14. 기존 repository regression validation

Step 5 실행 자체가 `orca-skills`의 기존 테스트를 깨뜨리지 않았는지 마지막에 확인한다.

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/verify_package.py
git diff --check
```

### 15. 성공 기준

가능하면 다음이 모두 실제 GLM/Gemma로 검증되어야 한다.

```text
ANALYSIS                 PASS
DESIGN                   PASS
IMPLEMENTATION           PASS
BUGFIX                    PASS
DESIGN → IMPLEMENTATION  PASS
FAIL → correction → PASS PASS
```

일부 scenario가 환경 문제로 실행 불가능하면 성공으로 가장하지 말고 BLOCKED와 이유를 기록한다.

### 16. 최종 보고

최종 응답에 다음을 정리한다.

1. 실행 환경
2. Orca version
3. `command -v claude-glm` / `command -v claude-gemma` 결과
4. scenario별 PASS/FAIL/BLOCKED
5. phase별 iteration 수
6. 실제 Gemma blocking finding 사례
7. GLM correction 결과
8. Unit Test / regression test 결과
9. Run / Task / Dispatch provenance
10. worker_done 및 lifecycle 결과
11. fake-agent 대비 실제 모델에서 발견된 차이
12. GLM Worker의 강점/약점
13. Gemma Reviewer의 강점/약점
14. Skill에서 수정이 필요한 부분
15. stable release 전 남은 blocker

### 17. repository 변경 정책

Step 5는 smoke test이므로 `orca-skills`의 Skill/policy/runtime implementation을 즉시 수정하지 않는다.

실제 모델 테스트에서 개선점이 발견되면 먼저 finding과 evidence를 정리한다.

Skill 변경이 필요하다면 별도 branch/PR로 분리하고, 기존 validator와 deterministic test를 모두 통과시킨다.

실제 GLM/Gemma smoke 결과를 repository 문서에 반영할 가치가 충분하면 별도 branch를 만들고 Draft PR을 생성한다. 반영 후보는 주로 다음이다.

- `COMPATIBILITY.md` verification status
- `CHANGELOG.md`
- 필요한 경우 Step 5 결과 요약 문서

실행하지 않은 결과를 repository에 기록하지 않는다.

---

## 기대되는 다음 상태

Step 5가 성공하면 현재 `COMPATIBILITY.md`의 다음 상태를 업데이트할 근거가 생긴다.

```text
claude-glm Worker       BLOCKED / NOT YET VERIFIED → VERIFIED
claude-gemma Reviewer   BLOCKED / NOT YET VERIFIED → VERIFIED
Real GLM/Gemma smoke    BLOCKED / NOT YET VERIFIED → VERIFIED
```

그 이후 첫 stable release readiness를 다시 판단한다.
