---
name: orca-worker-reviewer-orchestration
description: >
  Orca built-in orchestration을 실행 레이어로 사용하여 정확히 하나의 Worker와
  하나의 Reviewer를 supervised task/dispatch로 조정하고, 각 development phase가
  Reviewer PASS를 받을 때까지 Worker 수정과 Reviewer 재검토를 반복하는
  2-agent software development orchestration skill.
---

# Orca Worker-Reviewer Orchestration

## 1. Purpose

이 Skill은 `orca-worker-reviewer-loop`와 동일한 Worker/Reviewer 개발 정책을 사용하지만,
agent 실행/상태 추적/완료 대기를 **Orca built-in `orchestration` runtime**에 위임한다.

```text
User Request
    ↓
Coordinator
    ↓
Orca Run / Task / Dispatch
    ↓
Worker
    ↓ worker_done
Reviewer
    ↓
PASS ─────────────→ next phase / COMPLETED
FAIL → Worker fix → Reviewer re-review
```

정확히 두 역할만 지원한다.

```text
Worker
Reviewer
```

3-agent 이상의 topology는 이 Skill의 범위 밖이다.

## 2. Mandatory Orca Orchestration Contract

이 Skill의 실행은 반드시 real Orca orchestration state를 생성해야 한다.
단순히 Orca terminal 두 개를 열고 prompt를 보내는 것만으로 orchestration이라고 간주하지 않는다.

실행 전에 현재 Orca binary가 제공하는 version-matched orchestration guide를 읽는다.

1. Orca CLI executable을 공식 `orchestration` Skill의 규칙에 따라 한 번 resolve한다.
2. 다음을 실행하여 현재 binary의 전체 orchestration guide를 읽는다.

```text
<ORCA> skills get orchestration
```

3. Run / Task / Dispatch / worker completion / wait / decision 관련 command는 반드시 해당 guide의 현재 grammar를 따른다.
4. Custom agent command를 위해 Orca terminal 생성, terminal send/read/wait 등 terminal lifecycle 제어가 필요한 경우 다음도 읽는다.

```text
<ORCA> skills get orca-cli
```

5. terminal 생성/입력/대기/읽기 관련 command는 version-matched `orca-cli` guide의 현재 grammar를 따른다.
6. command/subcommand/flag를 기억이나 이 Skill 문서만 보고 추측하지 않는다.
7. 가능하면 `--json`을 사용한다.
8. 작업을 orchestrated라고 완료 보고하기 전에 실제 Task/Dispatch provenance를 확인한다.

공식 orchestration guide가 로드되지 않거나 runtime을 사용할 수 없다면 direct-session 방식으로
조용히 fallback하지 않는다.

```text
STATUS: BLOCKED
REASON: ORCA_ORCHESTRATION_UNAVAILABLE
```

## 3. Help Mode

다음 호출은 orchestration state를 만들지 않고 usage만 출력한다.

```text
/orca-worker-reviewer-orchestration
/orca-worker-reviewer-orchestration help
/orca-worker-reviewer-orchestration --help
/orca-worker-reviewer-orchestration -h
/orca-worker-reviewer-orchestration usage
```

실제 요청이 함께 있으면 정상 실행한다.

```text
orca-worker-reviewer-orchestration

Usage:
/orca-worker-reviewer-orchestration [worker=<agent>] [reviewer=<agent>] [max-iterations=<1-10>] [phases=<...>] <request>

Default:
  worker=claude-glm
  reviewer=claude-gemma
  max-iterations=5

Phases:
  Sequential: analysis → plan → design → implementation → test
  Specialized: bugfix, refactoring

Examples:
  /orca-worker-reviewer-orchestration phases=implementation <request>
  /orca-worker-reviewer-orchestration phases=design,implementation <request>
  /orca-worker-reviewer-orchestration phases=bugfix <request>
```

## 4. Runtime Parameters

```text
worker=<agent-command>
reviewer=<agent-command>
max-iterations=<integer>
phases=<phase1,phase2,...>
```

우선순위:

```text
1. 명시적 key=value parameter
2. 사용자 자연어 지시
3. Skill default
```

기본값:

```text
DEFAULT_WORKER = claude-glm
DEFAULT_REVIEWER = claude-gemma
DEFAULT_MAX_ITERATIONS = 5
```

`phases`가 없으면 자연어 요청에서 phase를 결정한다.

## 5. Agent Policy

기본 allowlist:

```text
claude-glm
claude-gemma
```

allowlist 밖의 agent는 실행하지 않는다.

```text
STATUS: BLOCKED
REASON: AGENT_NOT_ALLOWED
```

Worker와 Reviewer는 서로 달라야 한다.

```text
STATUS: BLOCKED
REASON: WORKER_REVIEWER_MUST_DIFFER
```

agent command는 PATH를 통해 resolve한다.

```bash
command -v <worker>
command -v <reviewer>
```

찾을 수 없으면:

```text
STATUS: BLOCKED
REASON: AGENT_COMMAND_NOT_FOUND
```

실제 agent process 실행 시 기본적으로 다음 permission mode를 사용한다.

```text
<agent-command> --dangerously-skip-permissions
```

절대 사용자 경로를 hard-code하지 않는다.

## 6. Orca-native Worker Placement

Worker와 Reviewer는 별도의 supervised assignment여야 하며 같은 agent session으로 역할을 바꾸지 않는다.

```text
Worker Dispatch != Reviewer Dispatch
Worker session != Reviewer session
```

각 phase에서 Coordinator는 현재 version-matched orchestration guide에 따라:

1. Run을 생성하거나 현재 Run에 bind한다.
2. Worker Task를 생성한다.
3. Worker용 terminal/agent process를 생성 또는 재사용한다.
4. Worker Task를 실제 Orca Dispatch에 연결한다.
5. worker completion (`worker_done`) 또는 escalation/question을 orchestration wait/check로 기다린다.
6. Worker 완료 후 Reviewer Task를 생성하고 별도 Dispatch로 Reviewer에게 전달한다.
7. Reviewer 결과를 PASS/FAIL contract로 평가한다.

### Custom command handling

`claude-glm`, `claude-gemma`처럼 Orca Settings에 Custom CLI Agent로 등록되지 않은 command도 사용할 수 있어야 한다.

현재 Orca guide가 selected command를 supervised `worker-start`의 recognized agent로 직접 시작할 수 있으면 그 경로를 우선한다.

그렇지 않으면 다음 원칙을 사용한다.

- 먼저 `<ORCA> skills get orca-cli`를 읽어 현재 Orca binary의 terminal create/send/read/wait grammar를 확인한다.
- Orca terminal에서 selected command process를 시작한다. terminal 생성과 prompt delivery는 `orca-cli` guide를 따른다.
- Task/Dispatch provenance는 반드시 Orca orchestration에 생성한다.
- runtime이 해당 terminal을 recognized agent로 inject할 수 있는 경우에만 injected dispatch를 사용한다.
- bare/unrecognized terminal이면 `orchestration` guide의 tracked dispatch 절차와 `orca-cli` guide의 terminal prompt delivery 절차를 조합한다.
- `orchestration` 또는 `orca-cli` command/flag를 기억으로 추측하지 않는다.
- 어느 경우에도 non-Orca subagent API로 대체하지 않는다.

### Completed Worker Lifecycle

현재 version-matched orchestration guide에 따라 Coordinator는 accepted `worker_done`을 처리한 뒤,
다음 Delivery를 acknowledge하거나 다시 wait하기 전에 settled worker terminal의 다음 owner를 반드시 결정한다.

허용되는 lifecycle decision은 정확히 다음 세 가지다.

#### 1. Immediate worker reuse

동일한 agent가 즉시 수행할 후속 Task가 있으면 완료된 Dispatch에서 agent terminal handle을 확인하고,
현재 version-matched orchestration guide의 worker inspection/reuse 절차에 따라 그 terminal의 cleanup ownership을
새 Dispatch로 이전한다.

reuse는 같은 session에서 Worker와 Reviewer 역할을 바꾸는 것을 허용하지 않는다.
동일 역할의 동일 agent가 즉시 이어지는 correction 또는 re-review Task를 수행하는 경우에만 사용한다.

#### 2. Worker release

즉시 재사용하지 않는 succeeded/failed `worker_done`은 현재 version-matched orchestration guide의
`worker-release` 절차로 release한다.

`worker-release`는 cancellation이 아니라 post-completion cleanup이다. Orca가 inspectable output을 보존한 뒤
해당 settled Dispatch가 소유한 terminal만 정리하도록 맡긴다. Coordinator는 이를 임의의
`terminal close`나 process kill로 대체하지 않는다.

#### 3. Explicit worker retain

사용자가 debugging을 위해 completed worker를 live 상태로 유지해 달라고 명시한 경우에만 retain한다.
현재 version-matched orchestration guide의 `worker-retain` 절차를 사용한다.

retain 사유를 최종 보고에 기록한다. 보존 필요가 끝나면 같은 Dispatch를 `worker-release`에 전달하여 정리한다.

#### Lifecycle safety

- timeout, TUI idle, heartbeat, status, question, escalation, rejected/stale `worker_done`만으로 worker를 release하지 않는다.
- `worker-release`가 `release_pending` 또는 `release_unknown`을 반환하면 `terminal close`로 우회하지 않고 receipt의 recovery action을 따른다.
- accepted `worker_done`마다 reuse, retain, release 중 하나를 기록한다.
- completed worker를 output 확인만을 위해 무기한 live 상태로 방치하지 않는다. release 후에도 output은 orchestration의 worker read 경로로 확인한다.
- Coordinator는 모든 settled worker terminal의 lifecycle을 account하기 전에는 다음 wait를 시작하거나 최종 완료를 보고하지 않는다.
- 이 section은 lifecycle policy/invariant만 정의한다. 구체 command/subcommand/flag grammar는 항상 실행 시점에 로드한 version-matched orchestration guide가 우선하며, 이 Skill에서 기억이나 과거 예시를 근거로 재구성하지 않는다.

## 7. Phase Model

지원 phase:

```text
ANALYSIS
PLAN
DESIGN
IMPLEMENTATION
TEST
BUGFIX
REFACTORING
```

Sequential lifecycle canonical order:

```text
ANALYSIS → PLAN → DESIGN → IMPLEMENTATION → TEST
```

Specialized:

```text
BUGFIX
REFACTORING
```

Phase routing:

```text
ANALYSIS       → templates/analysis.md       + reviews/common.md + reviews/analysis.md
PLAN           → templates/plan.md           + reviews/common.md + reviews/plan.md
DESIGN         → templates/design.md         + reviews/common.md + reviews/design.md
IMPLEMENTATION → templates/implementation.md + reviews/common.md + reviews/implementation.md
TEST           → templates/test.md           + reviews/common.md + reviews/test.md
BUGFIX         → templates/bugfix.md         + reviews/common.md + reviews/implementation.md
REFACTORING    → templates/refactoring.md    + reviews/common.md + reviews/refactoring.md
```

## 8. Phase Sequence Contract

`phases=A,B,C`는 A → B → C의 실행 순서를 뜻한다.
Sequential phase가 canonical order를 거스르면 자동 재정렬하지 않는다.

```text
STATUS: BLOCKED
REASON: INVALID_PHASE_ORDER
```

명시 `phases=`와 본문의 자연어 phase 요청/제외가 충돌하면:

```text
STATUS: BLOCKED
REASON: PHASE_CONFLICT
```

각 phase는 독립 PASS gate를 가진다. 현재 phase가 PASS하기 전에는 다음 phase Task를 dispatch하지 않는다.

### Specialized Phase Combination Policy

`BUGFIX`, `REFACTORING`은 일반 sequential lifecycle의 고정 단계가 아니라 specialized work phase다.

단독 실행은 허용한다.

```text
phases=bugfix
phases=refactoring
```

다른 phase와 조합하는 경우 Skill에 의미가 명확히 정의된 조합만 허용한다. Coordinator가 임의 순서를 추론하거나 specialized phase를 sequential phase처럼 끼워 넣지 않는다.

지원되지 않거나 의미가 불명확한 조합이면:

```text
STATUS: BLOCKED
REASON: UNSUPPORTED_PHASE_COMBINATION
```

대표적으로 지원하는 조합:

```text
phases=analysis,plan,design,implementation,test
phases=design,implementation
phases=design,implementation,test
phases=bugfix
phases=refactoring
```

## 9. Approved Phase Output

PASS된 이전 phase 결과는 다음 phase의 approved input이다.

```text
DESIGN PASS
→ APPROVED_DESIGN
→ IMPLEMENTATION Worker Task input
```

다음 phase Worker에게 최소한 다음 context를 전달한다.

```text
ORIGINAL_REQUEST
PHASES
CURRENT_PHASE
CURRENT_ITERATION
APPROVED_PREVIOUS_PHASE_OUTPUT
PREVIOUS_REVIEW_FINDINGS
```

approved 이전 phase 자체의 변경이 필요하면 임의 변경하지 않는다.

```text
STATUS: BLOCKED
REASON: PREVIOUS_PHASE_CHANGE_REQUIRED
```

## 10. Worker Contract

Worker는 phase별 `templates/*.md`를 따른다.

공통 결과 형식:

```text
# Worker Result

STATUS: COMPLETE | BLOCKED

## Summary
## Analysis
## Changes
## Modified Files / Artifacts
## Validation
## Unit Tests / Testing Strategy
## Review Feedback Resolution
```

Worker는 active Dispatch의 lifecycle preamble/guide를 따라 완료를 Orca orchestration에 보고해야 한다.
Coordinator는 terminal output만 보고 임의로 완료 처리하지 않는다.

## 11. Reviewer Contract

Reviewer는 `reviews/common.md`와 phase별 review policy를 따른다.
Worker 설명을 사실로 가정하지 않고 실제 repository/artifact/diff/test result를 확인한다.
Reviewer는 code/artifact를 직접 수정하지 않는다.

결과:

```text
# Review Result

RESULT: PASS | FAIL

## Summary
## Blocking Findings
## Non-Blocking Findings
## Test Review
## Final Decision
```

Blocking Finding:

```text
ID:
Severity: CRITICAL | MAJOR | MINOR
Location:
Issue:
Reason:
Required Action:
```

CRITICAL 또는 MAJOR가 존재하면 FAIL한다.

## 12. FAIL Loop

Reviewer PASS:

```text
current phase COMPLETED
→ 다음 phase 또는 전체 COMPLETED
```

Reviewer FAIL:

```text
Reviewer findings
      ↓
새 Worker correction Task / Dispatch
      ↓
Worker fix
      ↓ worker_done
새 Reviewer Task / Dispatch
      ↓
PASS / FAIL
```

Reviewer 자신이 fix를 수행하지 않는다.
Orca의 Task/Dispatch provenance가 각 attempt에 남아야 한다.

## 13. Iteration

기본:

```text
max-iterations=5
```

허용:

```text
1 <= max-iterations <= 10
```

범위를 벗어나면:

```text
STATUS: BLOCKED
REASON: INVALID_MAX_ITERATIONS
```

각 phase별 Reviewer attempt를 iteration으로 센다.
최대치를 넘기면 추가 Dispatch를 만들지 않는다.

```text
STATUS: ESCALATED
```

미해결 finding, 반복 원인, Worker/Reviewer 의견 차이를 보고한다.

## 14. Mandatory Test Gates

IMPLEMENTATION:

```text
Production Code Change
+ Unit Test Add/Modify
+ Unit Test Execution
+ PASS
```

BUGFIX:

```text
Regression Test required
```

REFACTORING:

```text
Behavior preservation
+ relevant Unit Test execution
+ PASS
```

필수 test가 없거나 실행이 기술적으로 불가능하면 Worker가 조용히 생략하지 않는다.

```text
UNIT_TEST_STATUS: BLOCKED
```

Reviewer는 자동 PASS하지 않는다.

## 15. Repository / Security Policy

사용자가 명시하지 않는 한 금지:

- git push / force push
- branch 삭제
- release / deployment
- production / infrastructure 변경
- destructive database operation
- 외부 network 접근
- 외부 package 임의 다운로드
- secret 출력/기록/외부 전송

Coordinator는 직접 production code를 수정하지 않는다.

## 16. Final Verification

전체 완료 전에:

1. 모든 requested phase가 PASS했는지 확인한다.
2. 각 phase/iteration에 필요한 Worker/Reviewer Task/Dispatch가 Orca state에 존재하는지 확인한다.
3. unresolved Blocking Finding이 없는지 확인한다.
4. 마지막 test/validation 결과를 확인한다.
5. 모든 settled Worker/Reviewer Dispatch가 reuse, retain 또는 release로 account되었는지 확인한다.
6. retain된 terminal이 있다면 사용자 요청과 retain 사유를 최종 보고에 기록한다.

최종 보고:

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
## Unit Tests / Validation
## Orca Orchestration State
## Final Review
RESULT: PASS
## Non-Blocking Recommendations
```

## 17. Core Invariants

```text
Exactly 2 roles: Worker + Reviewer
Worker != Reviewer
Worker Dispatch != Reviewer Dispatch
Real Orca Run/Task/Dispatch provenance required
Load version-matched Orca orchestration guide before orchestration commands
Load version-matched Orca CLI guide before terminal lifecycle commands
Never guess Orca CLI grammar
Every settled worker terminal → immediate reuse, explicit retain, or release
Never leave a completed worker live indefinitely
Specialized phase combinations must be explicitly supported
Reviewer never fixes its own findings
Reviewer FAIL → new Worker correction dispatch
Current phase PASS required before next phase
Production code change → Unit Test required
BUGFIX → Regression Test required
Agent command → allowlist + PATH based
No silent fallback to direct-session loop
```
