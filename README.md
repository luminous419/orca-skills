# Orca Worker-Reviewer Loop

`orca-worker-reviewer-loop`는 Orca에서 **Worker 1개 + Reviewer 1개**를 사용하여
software development 업무를 반복적으로 수행하기 위한 2-agent orchestration Skill이다.

핵심 workflow:

```text
Worker
  ↓
Reviewer
  ↓
PASS ─────────────→ 완료
FAIL → Worker 수정 → Reviewer 재검토
```

Reviewer가 PASS하기 전에는 현재 phase를 완료한 것으로 간주하지 않는다.

---

## 1. 주요 특징

- 정확히 2개의 역할만 사용
  - Worker
  - Reviewer
- Worker / Reviewer agent를 실행 시 선택 가능
- 기본 agent:
  - Worker = `claude-glm`
  - Reviewer = `claude-gemma`
- agent command는 PATH 기반으로 실행
- Worker와 Reviewer는 서로 다른 Orca session 사용
- Worker와 Reviewer가 동일 agent인 경우 실행 차단
- agent allowlist 지원
- 단일 phase 및 multi-phase 실행 지원
- 각 phase마다 독립적인 PASS / FAIL gate 적용
- IMPLEMENTATION에서 Unit Test 필수
- BUGFIX에서 Regression Test 필수
- 최대 iteration 제한 지원
- 잘못된 phase 순서 및 parameter/natural-language 충돌 감지
- Orca Custom CLI Agent 등록 기능에 의존하지 않음

---

## 2. 전체 Workflow

단일 phase:

```text
User Request
    ↓
Worker
    ↓
Reviewer
    ↓
PASS?
 ┌──┴──┐
NO    YES
↓       ↓
Worker  DONE
수정
↓
Reviewer
```

Multi-phase:

```text
Phase A
  ↓
Worker
  ↓
Reviewer
  ↓ PASS

Phase B
  ↓
Worker
  ↓
Reviewer
  ↓ PASS

Phase C
  ↓
Worker
  ↓
Reviewer
  ↓ PASS

COMPLETED
```

각 phase가 PASS해야 다음 phase로 진행한다.

---

## 3. 지원 Phase

### Sequential Development Phases

```text
ANALYSIS
  ↓
PLAN
  ↓
DESIGN
  ↓
IMPLEMENTATION
  ↓
TEST
```

### Specialized Work Phases

```text
BUGFIX
REFACTORING
```

모든 위 phase에는 Worker template과 Reviewer policy가 구현되어 있다.

---

## 4. Phase별 목적

### ANALYSIS
현재 코드와 요구사항을 분석하여 현재 구조, 문제/gap, 영향 범위, dependency, constraint, risk, unknown을 정리한다.

### PLAN
실제 개발 또는 개선을 진행하기 위한 scope, work items, dependency, 실행 순서, validation/test plan, completion criteria를 작성한다.

### DESIGN
실제 구현 가능한 수준의 상세 설계를 작성한다. current architecture, proposed design, component responsibility, interface, data flow, error handling, compatibility, files to change, testing strategy를 포함한다.

### IMPLEMENTATION
production code를 구현한다.

필수 조건:

```text
Production Code Change
        +
Unit Test Add / Modify
        +
Unit Test Execution
        +
Test PASS
```

Unit Test 없이 production code만 변경하면 Reviewer는 FAIL한다.

### TEST
구현 결과를 별도의 테스트 관점에서 검증한다. existing test 분석, coverage gap 확인, test 추가/수정, targeted test 및 관련 기존 test 실행을 수행한다.

### BUGFIX
bug의 root cause를 분석하고 수정한다. Regression Test가 필수다.

가능하면:

```text
Before Fix → Regression Test FAIL
After Fix  → Regression Test PASS
```

### REFACTORING
외부 behavior를 유지하면서 내부 구조를 개선한다. behavior preservation, public contract 유지, 관련 Unit Test PASS가 중요하다.

---

## 5. 기본 Runtime 설정

```text
worker=claude-glm
reviewer=claude-gemma
max-iterations=5
```

---

## 6. Runtime Parameters

```text
worker=<agent-command>
reviewer=<agent-command>
max-iterations=<1-10>
phases=<phase1,phase2,...>
```

예:

```text
/orca-worker-reviewer-loop \
worker=claude-glm \
reviewer=claude-gemma \
max-iterations=5 \
phases=design,implementation

아래 기능을 상세 설계한 후 구현까지 진행해줘.
```

Parameter 우선순위:

```text
1. 명시적 key=value parameter
2. 사용자 자연어 지시
3. Skill default
```

단, `phases=`와 자연어 phase 요청이 충돌하면 한쪽을 조용히 무시하지 않고 실행을 차단한다.

---

## 7. Help

다음 호출은 실제 orchestration을 시작하지 않고 간단한 사용법만 출력한다.

```text
/orca-worker-reviewer-loop
/orca-worker-reviewer-loop help
/orca-worker-reviewer-loop --help
/orca-worker-reviewer-loop -h
/orca-worker-reviewer-loop usage
```

단, Skill 호출 뒤에 실제 업무 요청이 함께 있으면 정상 실행한다.

---

## 8. 주요 사용 예시

### 기능 구현

```text
/orca-worker-reviewer-loop phases=implementation

Payment validation 기능을 구현해줘.
```

### 상세 설계 후 구현

```text
/orca-worker-reviewer-loop phases=design,implementation

아래 기능을 상세 설계한 후 구현까지 진행해줘.
```

### 전체 개발 workflow

```text
/orca-worker-reviewer-loop phases=analysis,plan,design,implementation,test

아래 요구사항에 대해 분석부터 테스트까지 진행해줘.
```

### Bug Fix

```text
/orca-worker-reviewer-loop phases=bugfix

아래 버그를 분석하고 수정해줘.
```

### Refactoring

```text
/orca-worker-reviewer-loop phases=refactoring

아래 모듈을 behavior 변경 없이 리팩터링해줘.
```

---

## 9. Phase 순서 규칙

`phases=A,B,C`는 **A → B → C의 실행 순서**를 의미한다.

Canonical order:

```text
ANALYSIS
→ PLAN
→ DESIGN
→ IMPLEMENTATION
→ TEST
```

잘못된 순서를 자동으로 바꾸지 않는다.

```text
STATUS: BLOCKED
REASON: INVALID_PHASE_ORDER
```

---

## 10. Phase Conflict

명시적인 `phases=`와 자연어 요청이 충돌하면 실행을 차단한다.

```text
STATUS: BLOCKED
REASON: PHASE_CONFLICT
```

---

## 11. PASS / FAIL Loop

```text
RESULT: PASS
```

또는:

```text
RESULT: FAIL
```

FAIL 시:

```text
Reviewer Findings
      ↓
Worker
      ↓
Fix
      ↓
Reviewer Re-review
```

Reviewer는 직접 production code나 artifact를 수정하지 않는다.

---

## 12. Iteration

기본 `max-iterations=5`, 허용 범위는 `1 ~ 10`이다. 각 phase별로 최대 iteration을 적용한다.

최대 iteration까지 PASS하지 못하면:

```text
STATUS: ESCALATED
```

---

## 13. Agent Selection

기본 allowlist:

```text
claude-glm
claude-gemma
```

Worker와 Reviewer는 서로 달라야 한다.

```text
STATUS: BLOCKED
REASON: WORKER_REVIEWER_MUST_DIFFER
```

---

## 14. Agent 실행 방식

agent command는 PATH에서 resolve한다.

```bash
claude-glm --dangerously-skip-permissions
claude-gemma --dangerously-skip-permissions
```

Skill은 특정 사용자의 절대 경로에 의존하지 않는다.

---

## 15. Orca Session 정책

Worker와 Reviewer는 반드시 서로 다른 Orca session에서 실행한다.

```text
Worker Session != Reviewer Session
```

이 Skill은 Orca Settings의 Custom CLI Agent 등록 기능에 의존하지 않는다.

---

## 16. Safety / Limitations

사용자가 명시적으로 요청하지 않는 한 git push, force push, branch 삭제, release, deployment, production 변경, infrastructure 변경, destructive database operation을 수행하지 않는다.

또한 외부 network 접근, 외부 package 임의 다운로드, credential/token/password/API key 출력, secret 기록을 금지한다.

---

## 17. Project-specific Rules

이 Skill에는 여러 프로젝트에서 공통으로 재사용할 orchestration 규칙만 둔다. 프로젝트별 기술 stack, architecture rule, coding convention, test/build command 등은 각 repository의 `CLAUDE.md` 등에 두는 것을 권장한다.

---

## 18. Installation

설치, 업데이트, 삭제 방법은 [`INSTALL.md`](INSTALL.md)를 참고한다.
