---
name: orca-worker-reviewer-loop
description: >
  Orca에서 하나의 Worker와 하나의 Reviewer를 독립 session으로 실행하고,
  Reviewer가 PASS할 때까지 Worker 수정과 Reviewer 재검토를 반복하는
  2-agent 전용 software development orchestration skill.
---

# Orca Worker-Reviewer Loop

## 1. Purpose

이 Skill은 Orca에서 정확히 두 개의 역할만 사용하는 software development orchestration workflow를 제공한다.

```text
User Request
     ↓
Coordinator
     ↓
Worker
     ↓
Reviewer
     ↓
 PASS?
 /    \
YES    NO
 ↓      ↓
DONE  Findings
        ↓
      Worker
        ↓
     Re-review
```

지원 역할은 `Worker`와 `Reviewer`뿐이다. 3-agent 이상 topology는 지원하지 않는다.

## Help Mode

다음 호출은 orchestration을 시작하지 않고 간단한 usage만 출력한다.

```text
/orca-worker-reviewer-loop
/orca-worker-reviewer-loop help
/orca-worker-reviewer-loop --help
/orca-worker-reviewer-loop -h
/orca-worker-reviewer-loop usage
```

단, `/orca-worker-reviewer-loop` 뒤에 실제 업무 요청이 함께 있으면 help mode가 아니라 정상 실행한다.

Help 출력은 간단하게 유지한다.

```text
orca-worker-reviewer-loop

Usage:
/orca-worker-reviewer-loop [worker=<agent>] [reviewer=<agent>] [max-iterations=<1-10>] [phases=<...>] <request>

Default:
  worker=claude-glm
  reviewer=claude-gemma
  max-iterations=5

Phases:
  Sequential: analysis → plan → design → implementation → test
  Specialized: bugfix, refactoring

Examples:
  /orca-worker-reviewer-loop phases=implementation <request>
  /orca-worker-reviewer-loop phases=design,implementation <request>
  /orca-worker-reviewer-loop phases=bugfix <request>
  /orca-worker-reviewer-loop worker=claude-glm reviewer=claude-gemma phases=design,implementation <request>
```

Help mode에서는 Worker/Reviewer session, run, task 또는 runtime artifact를 생성하지 않는다.

## 2. Core Role Model

Worker:
- 분석, 설계, 구현, 버그 수정
- 리뷰 피드백 반영
- 테스트 작성/수정
- 산출물 생성

Reviewer:
- Worker 결과 독립 검토
- 요구사항 충족 여부 판단
- 코드/설계/테스트 검증
- Blocking / Non-Blocking finding 작성
- PASS / FAIL 결정

Reviewer는 직접 production code나 artifact를 수정하지 않는다.
FAIL이면 수정 책임은 항상 Worker에게 돌아간다.

## 3. Runtime Parameters

Skill 호출 시 다음 parameter convention을 지원한다.

```text
worker=<agent-command>
reviewer=<agent-command>
max-iterations=<integer>
phases=<phase1,phase2,...>
```

예:

```text
/orca-worker-reviewer-loop worker=claude-glm reviewer=claude-gemma max-iterations=5 phases=design,implementation

아래 기능을 설계한 후 구현해줘.
...
```

이 값들은 shell option parser가 아니라 Skill 입력 텍스트에서 해석하는 runtime parameter convention이다.

우선순위:

```text
1. 명시적 key=value parameter
2. 사용자 자연어 지시
3. Skill default
```

## 4. Defaults

```text
DEFAULT_WORKER = claude-glm
DEFAULT_REVIEWER = claude-gemma
DEFAULT_MAX_ITERATIONS = 5
```

따라서 parameter를 생략하면 기본적으로 GLM Worker / Gemma Reviewer 조합을 사용한다.

`phases`가 생략되면 사용자의 자연어 요청으로 단일 Task Type 또는 multi-phase 실행 계획을 판단한다.

예:

```text
"이 기능을 구현해줘."
→ phases=implementation

"상세 설계 후 구현까지 진행해줘."
→ phases=design,implementation
```

## Machine-Readable Policy Contract

다음 JSON block은 deterministic policy smoke test의 source of truth다.
사람이 읽는 위/아래의 정책 설명과 의미가 일치해야 하며 두 Skill에서 동일하게 유지한다.
자유 형식 자연어의 전체 의미 해석은 여전히 Coordinator/LLM의 책임이고,
여기에는 대표적인 명시 phase 표현만 machine-readable term으로 정의한다.

```policy-contract
{
  "schema_version": 1,
  "help": {
    "tokens": ["help", "--help", "-h", "usage"],
    "empty_request": true
  },
  "defaults": {
    "worker": "claude-glm",
    "reviewer": "claude-gemma",
    "max_iterations": 5
  },
  "agent_allowlist": ["claude-glm", "claude-gemma"],
  "max_iterations": {
    "min": 1,
    "max": 10
  },
  "sequential_phases": [
    "analysis",
    "plan",
    "design",
    "implementation",
    "test"
  ],
  "specialized_phases": ["bugfix", "refactoring"],
  "supported_specialized_combinations": [
    ["bugfix"],
    ["refactoring"]
  ],
  "natural_language_phase_terms": {
    "analysis": ["analysis", "분석"],
    "plan": ["plan", "계획"],
    "design": ["design", "설계"],
    "implementation": ["implementation", "implement", "구현"],
    "test": ["test", "테스트"],
    "bugfix": ["bugfix", "bug fix", "버그 수정"],
    "refactoring": ["refactoring", "refactor", "리팩터링"]
  },
  "errors": {
    "agent_not_allowed": "AGENT_NOT_ALLOWED",
    "worker_reviewer_must_differ": "WORKER_REVIEWER_MUST_DIFFER",
    "invalid_max_iterations": "INVALID_MAX_ITERATIONS",
    "invalid_phase_order": "INVALID_PHASE_ORDER",
    "phase_conflict": "PHASE_CONFLICT",
    "unsupported_phase_combination": "UNSUPPORTED_PHASE_COMBINATION"
  }
}
```

## 5. Agent Allowlist

runtime agent command는 allowlist 기반으로 제한한다.

기본 allowlist:

```text
claude-glm
claude-gemma
```

allowlist에 없는 agent가 지정되면 실행하지 않는다.

```text
STATUS: BLOCKED
REASON: AGENT_NOT_ALLOWED
```

새 agent를 사용하려면 이 Skill의 allowlist를 명시적으로 수정한다.

## 6. Worker and Reviewer Must Differ

Worker와 Reviewer는 서로 다른 agent command여야 한다.

금지 예:

```text
worker=claude-glm reviewer=claude-glm
worker=claude-gemma reviewer=claude-gemma
```

동일 agent가 지정되면:

```text
STATUS: BLOCKED
REASON: WORKER_REVIEWER_MUST_DIFFER
```

## 7. Agent Command Resolution

agent는 PATH를 통해 실행한다.

```bash
claude-glm --dangerously-skip-permissions
claude-gemma --dangerously-skip-permissions
```

절대 경로를 Skill 내부 실행 명령으로 hard-code하지 않는다.

실행 전:

```bash
command -v <worker>
command -v <reviewer>
```

로 확인한다.

PATH에서 발견되지 않으면:

```text
STATUS: BLOCKED
REASON: AGENT_COMMAND_NOT_FOUND
```

실제 실행은 command name을 사용한다.

## 8. Mandatory Independent Orca Sessions

Worker와 Reviewer는 반드시 독립된 Orca terminal / agent session으로 실행한다.

```text
Coordinator
    ├── Orca Session A → Worker
    └── Orca Session B → Reviewer
```

같은 Claude session 안에서 role을 변경하지 않는다.

```text
Worker Session != Reviewer Session
```

동일 업무의 여러 iteration에서는 역할별 기존 session을 재사용할 수 있으나 역할을 바꾸지 않는다.

## 9. No Custom CLI Agent Dependency

이 Skill은 Orca Settings의 Custom CLI Agent 등록 기능에 의존하지 않는다.

agent identity는 Orca 설정이 아니라 runtime에 선택된 command로 결정한다.

## 10. Phase Model

지원하는 phase:

```text
ANALYSIS
PLAN
DESIGN
IMPLEMENTATION
TEST
BUGFIX
REFACTORING
```

### Sequential Development Phases

Canonical order:

```text
ANALYSIS
→ PLAN
→ DESIGN
→ IMPLEMENTATION
→ TEST
```

### Specialized Work Phases

```text
BUGFIX
REFACTORING
```

이 둘은 일반 feature lifecycle의 고정 순차 단계라기보다 독립 작업 유형으로 취급한다.

모든 정의된 phase는 실행 가능한 Worker template과 Reviewer policy를 가진다.

### Phase Template Routing

ANALYSIS:
- Worker template: `templates/analysis.md`
- Reviewer policy: `reviews/common.md` + `reviews/analysis.md`

PLAN:
- Worker template: `templates/plan.md`
- Reviewer policy: `reviews/common.md` + `reviews/plan.md`

DESIGN:
- Worker template: `templates/design.md`
- Reviewer policy: `reviews/common.md` + `reviews/design.md`

IMPLEMENTATION:
- Worker template: `templates/implementation.md`
- Reviewer policy: `reviews/common.md` + `reviews/implementation.md`

TEST:
- Worker template: `templates/test.md`
- Reviewer policy: `reviews/common.md` + `reviews/test.md`

BUGFIX:
- Worker template: `templates/bugfix.md`
- Reviewer policy: `reviews/common.md` + `reviews/implementation.md`

REFACTORING:
- Worker template: `templates/refactoring.md`
- Reviewer policy: `reviews/common.md` + `reviews/refactoring.md`

## 10.1 Explicit Phase Sequence

`phases=A,B,C`는 실행 대상뿐 아니라 실행 순서까지 정의한다.

예:

```text
phases=design,implementation
```

의 의미:

```text
DESIGN
  ↓ PASS
IMPLEMENTATION
```

각 phase는 독립적인 Worker → Reviewer PASS/FAIL loop를 가진다.
현재 phase가 Reviewer PASS를 받아야 다음 phase로 진행한다.

## 10.2 Invalid Phase Order

Sequential Development Phase는 canonical order를 따라야 한다.

허용 예:

```text
phases=analysis,plan,design
phases=design,implementation
phases=implementation,test
phases=analysis,design,implementation,test
```

잘못된 예:

```text
phases=implementation,design
phases=design,plan
phases=test,implementation
```

잘못된 순서를 사용자가 명시한 경우 Coordinator가 자동 재정렬하지 않는다.

```text
STATUS: BLOCKED
REASON: INVALID_PHASE_ORDER
```

## 10.3 Specialized Phase Ordering

`BUGFIX`, `REFACTORING`은 specialized phase다. 단독 사용은 허용한다.

```text
phases=bugfix
phases=refactoring
```

다른 phase와 조합하는 경우 의미가 명확해야 하며, Skill에 명시적으로 지원되는 조합이 아니면 임의 순서를 추론하지 않는다.

```text
STATUS: BLOCKED
REASON: UNSUPPORTED_PHASE_COMBINATION
```

대표 조합:

```text
phases=analysis,plan,design,implementation,test
phases=design,implementation
phases=design,implementation,test
phases=bugfix
phases=refactoring
```

## 10.4 Explicit Phase Override and Natural Language Conflict

`phases=`가 명시되어 있으면 authoritative execution plan이다.

하지만 사용자 본문의 자연어가 명시적 phases와 충돌하면 한쪽을 조용히 무시하지 않는다.

```text
STATUS: BLOCKED
REASON: PHASE_CONFLICT
```

## 10.5 Phase Source Priority

```text
1. 명시적 phases= parameter
2. 자연어 phase 요청
3. 단일 Task Type 자동 분류
```

단, 1과 2가 충돌하면 `PHASE_CONFLICT`로 차단한다.

## 10.6 Multi-Phase Gate

각 phase는 독립 PASS gate를 가진다. 이전 phase가 PASS하지 않으면 다음 phase로 넘어가지 않는다.

## 10.7 Approved Phase Output

이전 phase의 PASS된 산출물은 다음 phase의 approved input으로 전달한다.

```text
ORIGINAL_REQUEST
CURRENT_PHASE
APPROVED_PREVIOUS_PHASE_OUTPUT
CURRENT_ITERATION
```

구현 중 approved design 자체에 Blocking 문제가 발견되면 임의로 설계를 변경하지 않는다.

```text
STATUS: BLOCKED
REASON: PREVIOUS_PHASE_CHANGE_REQUIRED
```

## 11. Coordinator Responsibilities

Coordinator는 orchestration만 담당한다.

1. runtime parameter 해석
2. allowlist 검증
3. worker != reviewer 검증
4. PATH command 검증
5. phases 해석
6. phase order 검증
7. 명시 phases와 자연어 conflict 검증
8. 현재 phase의 Worker template 선택
9. Reviewer policy 선택
10. Worker session 실행/재사용
11. Worker 결과 수집
12. Reviewer session 실행/재사용
13. PASS / FAIL 결과 수집
14. FAIL이면 feedback을 Worker에게 전달
15. current-phase iteration 관리
16. PASS 시 approved phase output 보존
17. 다음 phase로 이동
18. 최종 결과 보고

Coordinator가 직접 production code를 수정하지 않는다.
Coordinator가 Reviewer FAIL을 임의로 PASS로 변경하지 않는다.

## 12. Original Request Preservation

모든 iteration에서 다음 context를 보존한다.

```text
ORIGINAL_REQUEST
PHASES
CURRENT_PHASE
CURRENT_ITERATION
WORKER_AGENT
REVIEWER_AGENT
```

Iteration > 1:

```text
PREVIOUS_REVIEW_FINDINGS
CURRENT_ARTIFACT_OR_IMPLEMENTATION
```

## 13. Worker Execution

Worker에게 다음을 전달한다.

```text
WORKER TEMPLATE
ORIGINAL_REQUEST
PHASES
CURRENT_PHASE
CURRENT_ITERATION
REPOSITORY CONTEXT
APPROVED_PREVIOUS_PHASE_OUTPUT
PREVIOUS REVIEW FINDINGS
```

실행:

```text
<worker> --dangerously-skip-permissions
```

## 14. Worker Result Contract

```text
# Worker Result

STATUS: COMPLETE | BLOCKED

## Summary
## Analysis
## Changes
## Modified Files / Artifacts
## Validation
## Unit Tests
## Review Feedback Resolution
```

DESIGN에서는 `Testing Strategy` 사용 가능.
IMPLEMENTATION / BUGFIX / REFACTORING에서는 Unit Test 관련 정보가 필수다. TEST phase에서는 Test Scope / Execution 결과가 필수다.

## 15. Reviewer Execution

별도 Reviewer session에서 다음을 전달한다.

```text
ORIGINAL_REQUEST
PHASES
CURRENT_PHASE
CURRENT_ITERATION
WORKER_AGENT
REVIEWER_AGENT
WORKER_RESULT
PREVIOUS_FINDINGS
```

실행:

```text
<reviewer> --dangerously-skip-permissions
```

Reviewer는 실제 repository, diff, artifact, tests, test result를 가능한 한 직접 확인한다.

## 16. Review Result Contract

```text
# Review Result

RESULT: PASS | FAIL

## Summary
## Blocking Findings
## Non-Blocking Findings
## Test Review
## Final Decision
```

각 Blocking Finding:

```text
ID:
Severity:
Location:
Issue:
Reason:
Required Action:
```

Severity:

```text
CRITICAL
MAJOR
MINOR
```

CRITICAL 또는 MAJOR finding이 하나라도 있으면 FAIL한다.

## 17. PASS / FAIL Loop

PASS:

```text
RESULT: PASS
STATUS: COMPLETED
```

FAIL:

```text
Worker
  ↓
Reviewer
  ↓
FAIL
  ↓
Blocking Findings
  ↓
Worker Fix
  ↓
Reviewer Re-review
```

Reviewer가 직접 수정해서는 안 된다.

## 18. Finding Tracking

Worker는 이전 finding에 대해 다음 중 하나를 기록한다.

```text
RESOLVED
DISPUTED
BLOCKED
```

DISPUTED에는 기술적 근거가 필요하다. Reviewer는 실제 결과를 재검증한다.

## 19. Maximum Iterations

기본:

```text
DEFAULT_MAX_ITERATIONS = 5
```

runtime override 가능:

```text
max-iterations=3
```

허용 범위:

```text
1 <= max-iterations <= 10
```

범위를 벗어나면:

```text
STATUS: BLOCKED
REASON: INVALID_MAX_ITERATIONS
```

각 phase별로 최대 iteration까지 PASS하지 못하면:

```text
STATUS: ESCALATED
```

## 20. Mandatory Unit Test Policy

Production code를 변경하는 다음 작업에서는 Unit Test가 필수다.

```text
IMPLEMENTATION
BUGFIX
REFACTORING
```

완료 조건:

```text
Production Code Change
        +
Unit Test Add / Modify
        +
Unit Test Execution
        +
Test PASS
```

Unit Test 없이 production code만 변경해서는 안 된다.

## 21. BUGFIX Regression Test

BUGFIX에서는 해당 버그를 검증하는 regression test가 반드시 필요하다.

가능하면:

```text
Before Fix → Regression Test FAIL
Apply Fix
After Fix → Regression Test PASS
```

Regression test가 없으면 Reviewer는 FAIL한다.

## 22. Unit Test Exception

Unit Test 작성 또는 실행이 기술적으로 불가능하면:

```text
UNIT_TEST_STATUS: BLOCKED

Reason:
Evidence:
Alternative Validation:
```

Reviewer는 자동 PASS하지 않는다.

```text
RESULT: FAIL
ESCALATION_REQUIRED: true
```

## 23. Scope Control

Reviewer가 관련 없는 대규모 refactoring을 Blocking Finding으로 요구해서는 안 된다.

```text
BLOCKING
NON_BLOCKING
```

PASS / FAIL은 BLOCKING issue 기준이다.

## 24. Repository Safety

사용자가 명시적으로 요청하지 않는 한 금지:

- git push
- force push
- branch 삭제
- release
- deployment
- production 변경
- infrastructure 변경
- destructive database operation

## 25. Company Environment Security

- 외부 network 접근 금지
- 외부 package 임의 다운로드 금지
- credentials/token/password/API key 출력 금지
- secret을 source/log/artifact/review 문서에 기록 금지
- 회사 내부 repository 내용을 외부 서비스에 전달 금지
- 범위를 넘어선 repository 탐색 최소화

## 26. Git Policy

기본 read-only 허용:

```text
git status
git diff
git diff --stat
git log
```

사용자 요청 없이 commit/push하지 않는다.

## 27. Completion Conditions

ANALYSIS:
- 실제 repository 근거 기반 분석
- current state / problem / impact / constraints / risks 정리
- 핵심 unknown 명시
- Reviewer PASS

PLAN:
- 목표/scope/work items/dependencies/order 명확
- validation/test plan 포함
- completion criteria 명확
- Reviewer PASS

DESIGN:
- 요구사항 충족
- responsibility/interface/data flow/error handling/compatibility/testing strategy 명확
- 구현 가능한 상세도
- Reviewer PASS

IMPLEMENTATION:
- 요구사항 구현
- 기존 convention 준수
- 불필요한 변경 없음
- Unit Test 작성/수정 및 실행
- 새/관련 테스트 PASS
- Reviewer PASS

TEST:
- 요구사항/구현에 대한 테스트 coverage 검토
- 필요한 테스트 추가/수정
- 신규/관련 테스트 실행
- 테스트 PASS 또는 production defect를 Blocking finding으로 보고
- Reviewer PASS

BUGFIX:
- root cause 분석
- bug fix 구현
- regression test 작성
- regression test PASS
- 관련 Unit Test PASS
- Reviewer PASS

REFACTORING:
- 요구된 구조 개선 달성
- behavior preservation
- 관련 Unit Test 작성/수정 필요 시 수행
- 관련 테스트 PASS
- Reviewer PASS

## 28. Final Report

```text
# Final Result

STATUS: COMPLETED

PHASES:
COMPLETED_PHASES:
WORKER:
REVIEWER:
ITERATIONS_BY_PHASE:

## Summary
## Changed Files / Artifacts
## Unit Tests
## Validation
## Final Review

RESULT: PASS

## Non-Blocking Recommendations
```

## 29. Core Invariants

```text
Exactly 2 roles: Worker + Reviewer
Worker != Reviewer
Worker Session != Reviewer Session
Reviewer = Review / Decide only
Reviewer FAIL → Return to Worker
Production code change → Unit Test required
BUGFIX → Regression Test required
No required Unit Test → FAIL
Agent command → allowlist only
Agent resolution → PATH based
phases=A,B,C → A then B then C
Invalid canonical order → BLOCK
Explicit phases vs natural-language conflict → BLOCK
Next phase starts only after current phase PASS
```
