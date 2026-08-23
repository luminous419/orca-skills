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

## Machine-Readable Policy Contract

다음 JSON block은 deterministic policy smoke test의 source of truth다.
사람이 읽는 위/아래의 정책 설명과 의미가 일치해야 하며 두 Skill에서 동일하게 유지한다.
자유 형식 자연어의 전체 의미 해석은 여전히 Coordinator/LLM의 책임이고,
여기에는 대표적인 명시 phase 표현만 machine-readable term으로 정의한다.
자연어 Worker/Reviewer/max-iterations 지시는 deterministic helper가 해석하지 않으며 Coordinator/LLM이 판정한다.

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
  "natural_language_automation": {
    "deterministic_representative_terms_for": ["phases"],
    "llm_interpretation_required_for": [
      "worker",
      "reviewer",
      "max-iterations",
      "free-form phase requests"
    ]
  },
  "known_agent_commands": ["claude", "codex", "claude-glm", "claude-gemma"],
  "agent_command_pattern": "[A-Za-z0-9._-]+",
  "custom_agent_command_pattern": "(?:claude|codex)-[A-Za-z0-9._-]+",
  "agent_launch_arguments": [],
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
    "invalid_agent_command": "INVALID_AGENT_COMMAND",
    "agent_command_not_found": "AGENT_COMMAND_NOT_FOUND",
    "worker_reviewer_must_differ": "WORKER_REVIEWER_MUST_DIFFER",
    "invalid_max_iterations": "INVALID_MAX_ITERATIONS",
    "invalid_phase": "INVALID_PHASE",
    "invalid_phase_order": "INVALID_PHASE_ORDER",
    "phase_conflict": "PHASE_CONFLICT",
    "unsupported_phase_combination": "UNSUPPORTED_PHASE_COMBINATION"
  }
}
```

## 5. Agent Policy

기본 known commands:

```text
claude
codex
claude-glm
claude-gemma
```

이 목록 외에는 `claude-` 또는 `codex-` prefix를 가진 model-pinned wrapper만 허용한다.
예: `claude-opus`, `codex-sol`. 안전한 token이라도 이 trust boundary 밖의 command는
실행하지 않는다.

agent parameter는 shell fragment나 경로가 아니라 하나의 simple PATH command token이다.

```text
[A-Za-z0-9._-]+
```

공백, slash, argument, shell metacharacter가 포함된 값은 실행하지 않는다.

```text
STATUS: BLOCKED
REASON: INVALID_AGENT_COMMAND
```

```text
STATUS: BLOCKED
REASON: AGENT_NOT_ALLOWED
```

따라서 PATH에 존재하더라도 `bash`, `sh`, `python3`, `env` 같은 일반 shell/interpreter
command는 agent로 승인하지 않는다.

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

known command도 PATH에서 resolve되어야 한다. wrapper 내부 모델명이나 vendor별 model-selection
syntax는 해석하지 않으며, generic CLI의 현재 configuration 또는 model-pinned wrapper가 모델을
선택할 책임을 가진다.

실제 agent process는 선택된 command token 자체를 entry point로 실행한다.

```text
<agent-command>
```

Skill은 model, permission 또는 vendor-specific argument를 추가하지 않는다. 필요한 옵션은
해당 CLI의 configuration 또는 model/permission-pinned wrapper command가 소유한다.

절대 사용자 경로를 hard-code하지 않는다.

## 6. Orca-native Worker Placement

Worker와 Reviewer는 별도의 supervised assignment여야 하며 같은 agent session으로 역할을 바꾸지 않는다.

```text
Worker Dispatch != Reviewer Dispatch
Worker session != Reviewer session
```

각 phase에서 Coordinator는 현재 version-matched orchestration guide에 따라:

1. Run을 생성하거나 현재 Run에 bind한다. 이 run-id는 이후 모든 artifact 경로(§9 Artifact path contract)의
   directory identity로 쓰이며, 이 run에서 처음 이 단계를 거칠 때 그 `<ARTIFACT_ROOT>` 디렉터리를 생성한다
   (§9 참조). 이후 phase/iteration에서는 이미 존재하므로 아무 것도 하지 않는다.
2. 이 phase/iteration의 Task graph 전체를 먼저 생성한다. Worker Task와 Reviewer Task를 함께 만들고, Reviewer Task는 Worker Task를 dependency로 선언한다.
3. Worker용 terminal/agent process를 생성 또는 재사용한다.
4. Worker Task를 실제 Orca Dispatch에 연결한다.
5. worker completion (`worker_done`) 또는 escalation/question을 orchestration wait/check로 기다린다.
6. accepted `worker_done` 이후, 이미 존재하던 Reviewer Task가 dependency 충족으로 ready가 된 것을 확인하고 별도 Dispatch로 Reviewer에게 전달한다. 이 시점에 Reviewer Task를 새로 생성하지 않는다.
7. 해당 Dispatch의 lifecycle을 아래 "Four-axis lifecycle accounting" 절차로 정확히 한 번 종결한다.
8. Reviewer 결과를 PASS/FAIL contract로 평가한다.

### Task graph ordering

Orca orchestration graph의 dependency promotion은 dependency가 completed되는 순간에 평가된다.
따라서 Task를 만드는 시점이 Reviewer Task가 ready가 되는지 여부를 결정한다.

- Coordinator는 Worker를 dispatch하기 전에 그 phase/iteration의 Task graph 전체를 생성한다. Reviewer Task는 Worker Task를 dependency로 선언한다.
- Dependency promotion은 edge-triggered다. dependency가 completed되는 순간에 이미 존재하며 그 Task를 dependency로 올린 dependent만 ready가 된다. 전역 재평가 sweep은 없다.
- 따라서 accepted `worker_done` 이후에 dependent Task를 생성하는 것을 금지한다. 그렇게 만든 Task는 영구히 pending으로 남는다.
- FAIL loop의 correction/re-review Task도 동일 규칙을 따른다. 다음 iteration의 graph는 현재 iteration의 Worker를 dispatch하기 전에 만든다.
- ready task 조회를 dispatch 대상 선택의 external memory로 사용한다.
- force-ready(수동 status override)는 recovery 전용이다. dependency validation을 전혀 수행하지 않으므로(미완료 dependency를 가진 Task도 promote된다) 정상 readiness 경로로 쓰지 않는다. 사용한 경우 사유와 receipt를 최종 보고에 기록한다.

> 이 sub-section은 concrete flag 문자열을 본문에 쓰지 않는다. "dependency로 선언한다", "ready task 조회",
> "수동 status override"처럼 역할 이름으로 서술하며, 실제 grammar는 실행 시점에 로드한 version-matched
> orchestration guide가 소유한다.

### Custom command handling

`claude-glm`, `claude-gemma`처럼 Orca Settings에 Custom CLI Agent로 등록되지 않은 command도 사용할 수 있어야 한다.

현재 Orca guide가 selected command를 supervised `worker-start`의 recognized agent로 직접 시작할 수 있으면 그 경로를 우선한다.

worker placement는 다음 순서를 따른다. 위 단계가 불가능할 때에만 아래 단계로 내려간다.

```text
1. worker-start가 selected command를 recognized agent로 직접 시작할 수 있으면 그 경로를 쓴다.
2. 그것이 "설정되지 않은 agent" 오류를 반환하면 — 이는 worker-start가 terminal을 *생성*할 때
   등록된 agent id를 요구하기 때문이며 정상 동작이다. 같은 호출을 재시도하지 않는다.
3. orca-cli guide에 따라 Orca terminal에서 selected command process를 먼저 기동하고, TUI가 idle이
   될 때까지 대기한 뒤, 그 terminal handle을 worker-start에 넘겨 입양시킨다. 이 경로는 supervised
   worker resource를 생성하므로 axis (b)를 확보한다. 항상 4번보다 우선한다.
4. 3번이 실패한 경우에만 low-level terminal 생성 + tracked/injected dispatch로 내려간다.
```

- 2번의 오류는 경로 실패가 아니라 분기 신호다. 이것만으로 4번으로 내려가지 않는다.
- 4번을 사용한 경우, 그 Dispatch가 supervised worker resource가 아니라는 사실을 phase 시작 시점에 ledger에 기록한다. axis (b) 판정을 예측 가능하게 만들기 위해서다.
- 4번에서 terminal을 직접 생성했다면 생성 시점에 `handle + phase + 생성 명령 + terminal role + terminal origin`을 ledger에 기록한다. 런타임에는 이 정보가 남지 않으므로, 기록하지 않으면 그 terminal은 영구히 `unknown_role`이 되어 닫을 수 없다. role은 그 Dispatch의 역할에 따라 `phase_worker` 또는 `phase_reviewer`이며, dispatch가 settle되기 전까지는 `active_worker`로 둔다.
- Coordinator가 만들지 않은 handle(사용자 지정 / 이전 phase 인계 / 입양)은 처음부터 `terminal role = external_or_adopted`로 기록하며, 이는 "close 대상 아님"과 동의어다.
- Coordinator 자신의 handle은 이 ladder의 어느 단계에도 입력으로 넣지 않는다. 자기 세션을 worker resource로 등록시키면 이후 release가 자기 세션을 닫으려 시도하게 된다. 3번에서 넘기는 handle이 Coordinator 자신의 handle과 같으면 중단하고 사용자에게 보고한다.
- `orchestration` 또는 `orca-cli` command/flag를 기억으로 추측하지 않는다.
- Task/Dispatch provenance는 반드시 Orca orchestration에 생성한다.
- 어느 경우에도 non-Orca subagent API로 대체하지 않는다.

### Completed Worker Lifecycle

현재 version-matched orchestration guide에 따라 Coordinator는 accepted `worker_done`을 처리한 뒤,
다음 Delivery를 acknowledge하거나 다시 wait하기 전에 settled worker terminal의 다음 owner를 반드시 결정한다.

허용되는 lifecycle outcome은 정확히 다음 네 가지다.

네 outcome은 axis (b)(supervised worker resource 등록 여부)의 답이며, settlement(axis (a)),
process liveness(axis (c1)), cleanup authority(axis (c2))는 각각 따로 확인한다.

#### Lifecycle ledger

네 축 판정과 중복 종결 방지는 Coordinator가 유지하는 하나의 in-context ledger 위에서 이루어진다.
새 파일도 새 CLI도 만들지 않는다.

```text
dispatchId | taskId | phase | iteration | terminal handle | terminal role | terminal origin |
(a) | (b) | (c1) | (c2) | finalized
```

`finalized` 컬럼은 boolean이 아니라 3-상태다. `no` / `in_progress` / `yes`
(Coordinator 자신의 행만 `n/a (never-close)`). `in_progress`는 "lifecycle 동작을 시작했고 아직
끝내지 못했다"는 뜻이며, 이 상태의 행은 자동 재시도 대상이 아니라 recovery 대상이다.

- Coordinator는 Run 동안 Dispatch id를 키로 하는 in-context ledger 한 개를 유지한다. Dispatch가 없는 `coordinator_session` 행은 예약 키 `-`로 단 한 행 존재한다.
- `terminal role` 값은 7개다. `coordinator_session` / `setup_terminal` / `active_worker` / `external_or_adopted` / `phase_worker` / `phase_reviewer` / `unknown_role`.
- `terminal origin` 값은 4개다. `self_created` / `adopted` / `pre_existing` / `unknown`. 두 컬럼은 서로를 대체하지 못한다. origin은 "누가 만들었나", role은 "무엇으로 쓰이는가"이며, axis (c2) = `authorized`는 둘 다 만족할 때만 나온다.
- ledger 기록 규칙은 다음 네 가지다.
  1. Run 시작 시 가장 먼저 Coordinator 자신의 handle을 `role = coordinator_session`, `origin = self_created`로 한 행 기록한다. 이 행은 영구히 `(c2) = not_authorized`, `finalized = n/a (never-close)`이며 어떤 phase도 이 값을 바꾸지 않는다.
  2. 그 밖의 terminal을 만들거나 입양하는 즉시 행을 만들고 `role`과 `origin`을 함께 기록한다. 이 두 값은 나중에 복구할 수 없는 유일한 axis (c2) 근거다. terminal 조회에는 둘 다 없다.
  3. 어떤 lifecycle 동작(release / retain / reuse / close)도 수행 전에 그 행의 `finalized`를 확인한다. 이 확인은 그 동작의 첫 문장이며, 조회·기록·판정보다도 앞선다. `finalized = yes`면 아무 동작도 하지 않고 기록된 값을 그대로 재사용한다(어떤 명령도 보내지 않는다). `finalized = in_progress`면 같은 동작을 다시 수행하지 않고 guide의 recovery 절차를 따른다. 게이트가 동작 뒤에 있으면 그것은 게이트가 아니라 사후 보고일 뿐이다.
  4. 동작을 시작할 때 `in_progress`로, 네 축 outcome을 모두 채운 시점에 `yes`로 표시한다. 한 Dispatch에 대한 finalization은 정확히 한 번이며, 그 이후의 모든 재진입은 재사용(replay)이다. 이 규칙의 키는 Dispatch id이지 Task id가 아니다. 하나의 Task가 recovery로 두 번 dispatch되면 행도 finalization도 각각 따로 존재한다.
- role 승격 금지 규칙. 한 번 `coordinator_session` / `setup_terminal` / `external_or_adopted`로 기록된 행은 어떤 이유로도 `phase_worker` / `phase_reviewer`로 바뀌지 않는다. 더 보수적인 방향(`phase_worker` → `active_worker` → `external_or_adopted`)으로의 변경만 허용한다. `active_worker` → `phase_worker`/`phase_reviewer` 승격은 그 Dispatch가 axis (a)로 settled 확인된 뒤에만 허용되는 유일한 상향 전이다.
- 이 ledger는 파일도 새 CLI도 아니다. Coordinator의 phase state이며, section 16 최종 보고의 `## Orca Orchestration State`가 그 직렬화 형태다.

#### 1. Immediate worker reuse

동일한 agent가 즉시 수행할 후속 Task가 있으면 완료된 Dispatch에서 agent terminal handle을 확인하고,
현재 version-matched orchestration guide의 worker inspection/reuse 절차에 따라 그 terminal의 cleanup ownership을
새 Dispatch로 이전한다.

reuse는 같은 session에서 Worker와 Reviewer 역할을 바꾸는 것을 허용하지 않는다.
동일 역할의 동일 agent가 즉시 수행할 다음 Task — 같은 phase의 correction 또는 re-review Task와
다음 phase의 Task를 모두 포함한다 — 를 수행하는 경우에 사용한다.
reuse 가능 여부는 아래 `#### Session reuse contract`의 8개 eligibility 조건을 전부 만족할 때에만 참이며,
하나라도 만족하지 못하면 그 handle을 버리고 새 terminal을 만든다.

단 하나의 예외가 있다. §17 Final Adversarial Review Dispatch에는 이 reuse 권장이 적용되지 않는다.
모든 Final Adversarial Review attempt는 새로 생성한 terminal을 사용하며, 그 Dispatch의 axis (b)는
retain / release / unsupervised 중 하나이고 절대 reuse가 아니다.

reuse는 이전 Dispatch에 어떤 lifecycle mutation 명령도 보내지 않는다. axis (b)의 reuse outcome은 그
Dispatch의 finalization으로만 기록하며, finalization은 ledger 연산이지 명령 발행이 아니다.
실제 cleanup ownership 이전은 다음 Task를 같은 terminal로 시작시키는 worker 기동 절차가 수행한다.
따라서 순서는 언제나 이전 Dispatch의 axis (a) 검증 → 이전 Dispatch finalization(명령 0개)
→ 다음 Task를 같은 terminal로 시작시키는 worker 기동 절차다.
`worker-retain`은 reuse의 수단이 아니다. 그것은 아래 `#### 3. Explicit worker retain`의 사용자 요청
전용이며, reuse 경로에서 발행하면 사용자 요청 없이 해소되지 않는 retention record가 쌓여 section 16의
retain 보고 의무를 만족시킬 수 없다.
동일 terminal을 다음 Dispatch로 넘기면 cleanup authority(axis (c2))도 함께 이전되므로, 이후 그
terminal의 close 판단은 새 Dispatch를 기준으로 한다.

#### Session reuse contract

reuse는 아래 8개 조건을 전부 만족할 때에만 허용된다. 하나라도 만족하지 못하면 그 handle을 버리고 새
terminal을 만들며, 만족하지 못한 조건의 이름을 section 16 보고에 남긴다. 판정은 fail-closed다 —
확인되지 않은 값은 통과가 아니라 실패다.

1. `same_role` — 이전 Dispatch와 다음 Dispatch의 역할이 같다. Worker에서 Worker로, Reviewer에서
   Reviewer로만 이어지며 역할 교체는 어떤 경우에도 reuse가 아니다.
2. `same_agent_command` — 두 Dispatch가 같은 agent command로 기동된다. 양쪽 값이 모두 기록되어 있고
   문자열이 일치해야 하며, 한쪽이라도 비어 있으면 reuse하지 않는다.
3. `live_process` — reuse 판정 직전에 새로 관측한 값이 terminal이 살아 있음을 긍정적으로 보여준다.
   이전 attempt에 기록해 둔 process liveness 값은 근거가 되지 못한다. axis (c1)은 약 10초까지 stale일 수
   있기 때문이다. 관측값이 비어 있거나 살아 있다고 인정된 값 목록에 없으면 그것은 "괜찮음"이 아니라
   "확인되지 않음"이며 reuse하지 않는다.
4. `previous_dispatch_settled` — 이전 Dispatch가 axis (a)로 settle 확인되었고 그 finalization이
   완료되었다.
5. `ownership_transferable` — 이전 Dispatch가 그 terminal의 ownership을 실제로 쥐고 있고, terminal
   effect가 ledger에 기록되어 있으며, 런타임이 그 terminal을 자기 소유로 붙들고 있지 않다.
6. `not_explicitly_retained` — 사용자 요청에 의한 explicit retain 상태가 아니다. retain 기록이 살아 있는
   동안은 reuse하지 않으며, 그 기록은 release가 수행될 때에만 해제된다.
7. `not_coordinator_or_adopted` — Coordinator 자신의 세션도, setup terminal도, Coordinator가 만들지 않은
   terminal도 아니다. Coordinator가 스스로 만든 close-eligible role의 terminal만 chain에 들어간다.
8. `not_in_lifecycle_recovery` — 이전 Dispatch가 recovery/error 상태가 아니다. settlement가 진행 중으로
   남아 있거나, settle되지 못한 사유가 기록되어 있거나, 이전 attempt가 outcome을 남기지 못한 경우는
   전부 여기에 걸린다.

section 17 Final Adversarial Review Dispatch에는 이 계약이 적용되지 않는다. 모든 Final Adversarial
Review attempt는 새로 생성한 terminal을 쓰며 어떤 reuse chain에도 들어가지 않는다.

```text
REUSE_SCOPE = same_role_across_phases_and_iterations
REUSE_ELIGIBILITY = same_role, same_agent_command, live_process, previous_dispatch_settled, ownership_transferable, not_explicitly_retained, not_coordinator_or_adopted, not_in_lifecycle_recovery
REUSE_TERMINATION = zero_lifecycle_commands, finalize_exactly_once
REUSE_ORDER = verify_settlement, finalize_previous_dispatch, start_next_task_on_same_terminal
```

#### 2. Worker release

즉시 재사용하지 않는 succeeded/failed `worker_done`은 현재 version-matched orchestration guide의
`worker-release` 절차로 release한다.

`worker-release`는 cancellation이 아니라 post-completion cleanup이다. Orca가 inspectable output을 보존한 뒤
해당 settled Dispatch가 소유한 terminal만 정리하도록 맡긴다. Coordinator는 이를 임의의
`terminal close`나 process kill로 대체하지 않는다.

release가 `retained` + 외부 terminal 사유 + process action 없음을 반환하는 것은 정상 결과이며
resource는 계속 retained로 남는다.
그 응답은 axis (c1)을 해결하지 않으며, 뒤이어 terminal을 직접 close해도 된다는 허가가 아니다.

#### 3. Explicit worker retain

사용자가 debugging을 위해 completed worker를 live 상태로 유지해 달라고 명시한 경우에만 retain한다.
현재 version-matched orchestration guide의 `worker-retain` 절차를 사용한다.

retain 사유를 최종 보고에 기록한다. 보존 필요가 끝나면 같은 Dispatch를 `worker-release`에 전달하여 정리한다.

#### 4. Unsupervised dispatch — no worker resource

- supervised worker 조회가 "해당 dispatch 없음"을 반환하면, 그 Dispatch는 애초에 supervised worker resource로 등록된 적이 없다(low-level 경로). 이것이 정상적인 네 번째 outcome이다.
- 이는 release skip도 실패도 아니다. `worker-release`를 반복하거나 Dispatch를 재생성하지 않는다. 다만 이 경로는 정상 종결 상태일 뿐 선호 경로가 아니다. 가능하면 `### Custom command handling` ladder의 3번으로 supervised 경로를 확보한다.
- 이 응답은 settlement에 대해 아무것도 증명하지 않는다(settlement 전후 동일). settlement 판정은 오직 axis (a)로 한다.

#### Four-axis lifecycle accounting

accepted `worker_done`마다 서로 독립적인 네 축을 각각 확인하고 각각의 outcome을 기록한다.
한 축의 결과를 다른 축의 근거로 쓰지 않는다. 특히 (c1) process liveness와 (c2) cleanup authority는
별개의 질문이며, terminal을 닫는 행위는 (c1)이 아니라 (c2)가 허가한다.

**(a) Dispatch outcome / settlement**

- 권위 있는 근거는 Task/Dispatch provenance 조회뿐이다.
- `worker_done`이 expected Task ID와 expected Dispatch ID **양쪽 모두**와 일치하고, 명시적 `succeeded`/`failed` outcome을 담고 있으며, lifecycle rejection 없이 accepted되었고, 실제 provenance에 `completed`/`failed` outcome과 completion timestamp가 남아 있으면 settled로 account한다. 둘 중 한쪽 ID만 일치하거나 outcome 필드가 없거나 provenance에 completion timestamp가 없으면 settled가 아니다.
- settlement는 worker 기동 방식과 무관하다. supervised든 low-level이든 accepted `worker_done`은 동일하게 Dispatch를 settle한다. low-level Dispatch가 auto-settle되지 않는다고 가정하지 않는다.
- 아직 `dispatched`면 settled가 아니다. `worker_done`이 rejected/stale이었을 수 있으므로 release나 cleanup으로 진행하지 않고 guide의 recovery 절차를 따른다.
- 이 판정은 read-only이며, 그 Dispatch의 **첫 lifecycle mutation(`worker-release` / `worker-retain` / close)보다 반드시 먼저** 끝난다. 중복 종결 방지 gate는 "이 Dispatch를 이미 종결했는가"(idempotency)만 답하므로 "이 Dispatch가 실제로 settle되었는가"(correctness)를 대신 답하지 못한다. 두 gate는 모두 필요하며 순서는 **중복 종결 방지 gate → (a) 검증 → lifecycle mutation**이다.
- (a)가 `completed`/`failed`를 증명하지 못하면 **settlement/finalization 경로**(`worker-release` / `worker-retain` / close)에서는 그 Dispatch에 대해 lifecycle mutation을 **하나도** 실행하지 않는다. 아직 `dispatched`이거나 rejected/stale `worker_done`이거나 outcome 필드 없는 `worker_done`이거나 worker가 outcome 없이 사라진 경우는 조용히 넘어가지 않고 명시적 error로 보고하거나 recovery/escalation 경로로 보낸다. 중복 종결 방지 gate가 이미 잡은 행은 그대로 두고 사유를 기록한다. 임의로 되돌려 재시도하지 않는다.
- 이 금지의 **유일한 예외는 명시적 recovery 경로**다. worker가 `worker_done` 없이 사라진 Dispatch를 회수할 때는, 아직 `dispatched`인 그 Dispatch에 대해 supervised worker record가 outcome을 남기지 않았으면 `worker-abandon`을 먼저 실행하고 이어서 `worker-release`를 실행한다(이미 `failed`/`stopped`로 관측된 worker는 `worker-release`만). 두 명령 모두 **의도적으로** not-settled Dispatch에 실행된다. supervised worker resource가 없는 low-level Dispatch의 recovery는 Task를 `failed`로 표시할 뿐 worker-resource lifecycle 명령을 실행하지 않는다. unsettled Dispatch를 회수한다는 말이 곧 그 뜻이며, 위 금지는 settlement 경로가 unsettled Dispatch를 settled인 것처럼 종결하는 것을 막는 규칙이지 recovery를 막는 규칙이 아니다. recovery는 settlement로 account되지 않고, `worker_done` count는 0이며, terminal을 close-eligible role로 승격시키지 않는다.
- settled 확인 후 수동 completed 표시를 중복하지 않는다.

**(b) Supervised worker-resource registration**

- supervised worker 조회 계열은 supervised worker resource 레지스트리만 다루며, settlement를 묻는 명령이 아니다.
- 조회가 resource와 함께 성공하면 supervised다. reuse / retain / release 중 하나를 기록한다.
- 조회가 "해당 dispatch 없음"이면 네 번째 outcome `unsupervised`다.
- "해당 dispatch 없음"은 settlement에 대해 양방향 모두 무정보다. 역으로 supervised resource의 존재도 settlement의 증거가 아니다. 그것은 reuse/retain/release가 적용된다는 사실만 증명한다.

**(c1) Residual process liveness**

- (a), (b)의 결과와 무관하게 항상 확인한다.
- 권위 있는 근거는 terminal 조회이며, 이 조회는 process 생존 여부만 답한다.
- 죽어 있으면 정리할 것이 없다("no residual terminal"). 살아 있어도 어떤 close 권한도 생기지 않는다.
- 이 값은 eventually consistent다. 이미 닫힌 terminal에 대해 약 10초간 live로 보일 수 있다. terminal listing에 handle이 없거나 supervised 조회의 terminal 상태와 불일치하면 그 불일치 자체를 "이미 닫혔거나 내 대상이 아님"으로 기록한다. 불일치를 close로 해소하지 않는다.

**(c2) Cleanup authority**

세 상태 `authorized` / `not_authorized` / `unknown` 중 하나다. 판정은 두 개의 독립 조건이 모두
성립할 때만 `authorized`다. (i) terminal role이 close 가능한 부류이고, (ii) provenance/ownership이
이 Dispatch의 소유를 증명한다. 하나라도 빠지면 `authorized`가 아니다. terminal 조회는 이 질문에
답하지 못한다. 생성자·소유자·takeover·identity 필드가 없으며, 자기가 만든 terminal / 무관한 사용자
terminal / Coordinator 자신의 terminal이 모두 동일한 liveness 값을 반환한다.

STEP 4-0 — terminal role / resource class 게이트. provenance보다 먼저 본다.

| `terminal role` | 무엇인가 | axis (c2) |
|---|---|---|
| `coordinator_session` | 이 orchestration을 실행 중인 Coordinator 자신의 세션 terminal | 언제나 `not_authorized`. 예외 없음. provenance를 조회하지 않는다 |
| `setup_terminal` | 사용자/이전 세션이 만든 setup·configured tab | 언제나 `not_authorized` |
| `active_worker` | dispatch가 아직 settle되지 않은 worker/reviewer terminal | 언제나 `not_authorized`. settle 전 close는 axis (a) 위반이다 |
| `external_or_adopted` | **Coordinator가 만들지 않은** terminal. pre-existing / 사용자 지정 / 외부 세션에서 인계받음 / user-taken-over. Coordinator 자신이 만든 terminal을 자기 reuse chain으로 이어받는 것은 여기에 해당하지 않는다(§6 `#### 1. Immediate worker reuse`) | 언제나 `not_authorized` |
| `phase_worker` \| `phase_reviewer` | Coordinator가 이번 phase의 이 Dispatch를 위해 직접 생성한 worker/reviewer terminal | STEP 4a/4b로 진행 |
| `unknown_role` | 역할이 기록되지 않음 | `unknown` (= close 금지, 보고 대상) |

`authorized`로 갈 수 있는 role은 `phase_worker` / `phase_reviewer` 둘 뿐이다.
`terminal origin == self_created`는 필요조건일 뿐 충분조건이 아니다. Coordinator 자신의 세션도,
setup terminal도, 아직 실행 중인 worker도 모두 "누군가 만든" terminal이기 때문이다.

- STEP 4a supervised. STEP 4-0 통과 후 terminal effect가 **기록되어 있고**(`created` 또는 `reused`), ownership이 이 Dispatch로 기록되어 있고, retain 사유 없음, ownership이 다른 Dispatch로 이전되지 않음, worker identity 증명됨이 전부 성립할 때만 `authorized`. 이때도 직접 close하지 않고 release가 런타임 자신의 손으로 닫게 한다.
- release가 실제로 process를 종료했는지는 axis (c2)의 판정이 아니라 런타임 응답의 process action이 답한다. process action이 종료를 증명하지 못하면 그 terminal은 닫히지 않은 것이며 `retained`로 기록·보고한다. authority가 `authorized`였다는 사실은 이 기록을 바꾸지 않는다.
- STEP 4b unsupervised. STEP 4-0 통과 후 Coordinator 자신의 생성 receipt가 유일한 근거이며, 그 receipt는 `handle + phase + role + 이 Dispatch id`를 모두 담아야 한다. 넷이 모두 있고 이후 ownership transfer/takeover receipt가 없으면 `authorized`(orca-cli 경로로 close, receipt 기록), 하나라도 없으면 `unknown`이다.
- (c2)는 (c1)의 값과 무관하게 **항상** 계산해서 기록한다. (c1)이 live가 아닌 Dispatch도 네 축 모두에 outcome을 남긴다. 다만 그때 취해지는 action은 기록된 (c2) 값이 무엇이든 `nothing to do`다.
- `unknown`은 close 금지 측면에서 `not_authorized`와 동일하게 취급하고, 차이는 보고 의무뿐이다. 권한이 적극적으로 증명되지 않은 모든 terminal의 기본 동작은 retain-and-report다.
- 절대 닫지 않는 목록은 위 표에서 `not_authorized`/`unknown`으로 고정된 role 전체다. 이 목록은 산문이 아니라 role enum이므로 machine-checkable하다.

순서 규정은 STEP 1 → 2 → 3 → 4다. (a) 확인 → (b) worker-resource 처리 → (c1) 확인 → (c2) 확인이며,
(a) 확인은 이 Dispatch의 첫 lifecycle mutation보다 앞에 온다. 네 축 outcome은 모든 Dispatch에 대해
항상 기록한다. (c1)이 live가 아니어도 (c2)는 reporting/accounting 목적으로 계산해 기록하며, 실제
close/mutation 결정만이 (c1)이 live인 terminal에 대해서만 의미를 갖는다. 죽어 있으면 (c2) 값이
무엇이든 action은 `nothing to do`다. (c2) 안에서는 STEP 4-0(role) → 4a/4b(provenance) 순서를
지킨다. role 게이트에서 탈락한 terminal에 대해서는 provenance 조회 자체를 하지 않는다.
조회하면 "내가 만들었다"는 사실이 다시 close 유혹으로 되돌아온다.

예외 하나가 보존된다. release가 `release_pending`/`release_unknown`이면 (c2)가 `authorized`여도
`terminal close`로 우회하지 않고 receipt의 recovery action을 따른다.

#### Lifecycle safety

- timeout, TUI idle, heartbeat, status, question, escalation, rejected/stale `worker_done`만으로 worker를 release하지 않는다.
- settlement 경로의 어떤 lifecycle mutation도 그 Dispatch의 axis (a)가 Task/Dispatch provenance로 `completed`/`failed`임을 증명한 뒤에만 실행한다. 중복 종결 방지 gate 통과는 이 증명을 대신하지 못한다. 명시적 recovery 경로의 `worker-abandon`/`worker-release`는 axis (a)가 not-settled임을 확인한 뒤에 실행되는 별개의 동작이며 이 규칙의 예외다.
- `worker-release`가 `release_pending` 또는 `release_unknown`을 반환하면 `terminal close`로 우회하지 않고 receipt의 recovery action을 따른다.
- accepted `worker_done`마다 네 축 각각에 기록된 outcome이 있어야 하며, axis (b)의 outcome은 reuse, retain, release 또는 unsupervised 중 하나다.
- `dispatch_not_found`와 같은 missing/unaddressable 결과는 axis (b)의 답이며 settlement에 대해 양방향 모두 무정보다. 역으로 supervised resource의 존재도 settlement의 증거가 아니다. settlement는 expected `worker_done` acceptance와 Task/Dispatch provenance로만 판정한다.
- Dispatch가 settled되어도 residual terminal/resource가 남아 있으면 (c1) 기록과 (c2) 기록을 각각 남긴다.
- completed worker를 output 확인만을 위해 무기한 live 상태로 방치하지 않는다. release 후에도 output은 orchestration의 worker read 경로로 확인한다. 단, 정리 불가 사유가 authority 부재라면 그것은 방치가 아니라 명시적 retain 기록이다.
- Coordinator는 모든 settled worker terminal의 lifecycle을 account하기 전에는 다음 wait를 시작하거나 최종 완료를 보고하지 않는다.
- close한 terminal마다 왜 닫을 권한이 있었는지를 terminal role과 provenance 두 가지 모두로 한 줄로 기록한다. 둘 중 하나라도 적을 수 없다면 애초에 닫지 말았어야 하는 terminal이다.
- Coordinator는 자기 자신의 terminal(`coordinator_session`)에 대해 어떤 lifecycle 동작도 수행하지 않는다. release도, close도, retain 기록 이외의 어떤 것도 하지 않는다. 자기 handle은 phase 시작 시 ledger에 `coordinator_session`으로 못 박고, 이후 어떤 판정 경로도 그 행을 `authorized`로 만들 수 없다.
- 이 section은 lifecycle policy/invariant만 정의한다. 구체 command/subcommand/flag grammar는 항상 실행 시점에 로드한 version-matched orchestration guide가 우선하며, 이 Skill에서 기억이나 과거 예시를 근거로 재구성하지 않는다.

#### Lifecycle accounting contract

아래 블록은 이 section 산문의 요약이자 회귀 잠금이다. 새 policy contract가 아니며 두 스킬 사이에서 공유되지 않는다.

```text
AXIS_A_SETTLEMENT = dispatch_and_task_provenance
AXIS_B_WORKER_RESOURCE = supervised_worker_registry
AXIS_C1_PROCESS_LIVENESS = terminal_inspection
AXIS_C2_CLEANUP_AUTHORITY = launch_provenance_and_ownership
LIFECYCLE_OUTCOMES = reuse, retain, release, unsupervised
CLEANUP_AUTHORITY_STATES = authorized, not_authorized, unknown
TERMINAL_ROLE_CLASSES = coordinator_session, setup_terminal, active_worker, external_or_adopted, phase_worker, phase_reviewer, unknown_role
NEVER_CLOSE_TERMINAL_ROLES = coordinator_session, setup_terminal, active_worker, external_or_adopted, unknown_role
CLOSE_ELIGIBLE_TERMINAL_ROLES = phase_worker, phase_reviewer
CLOSE_ALLOWED_ONLY_WHEN = authorized_and_close_eligible_role
DEFAULT_WHEN_NOT_AUTHORIZED = retain_and_report
FINALIZATION_PER_DISPATCH = exactly_once, gate_before_lifecycle_action, settlement_verified_before_lifecycle_action
TASK_GRAPH_ORDERING = create_graph_before_worker_dispatch
FORCE_READY_USE = recovery_only
CUSTOM_COMMAND_PLACEMENT_ORDER = worker_start_agent, terminal_create_then_tui_idle_then_worker_start_terminal, dispatch_inject
```

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

명시적 `phases=`의 각 값은 위 지원 phase 중 하나여야 한다. 알 수 없는 phase가 포함되면:

```text
STATUS: BLOCKED
REASON: INVALID_PHASE
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
BUGFIX         → templates/bugfix.md         + reviews/common.md + reviews/bugfix.md
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

#### Task boundary contract

한 Task의 경계는 두 층으로 나뉜다. 층 1은 Coordinator가 Task를 만들 때 spec 본문에 직접 써넣는 값이고,
층 2는 dispatch 시점에 Orca가 preamble로 주입하는 authoritative identity다. Task spec 본문은
write-once이며 Task id와 Dispatch id는 그 본문을 조립하는 시점에 아직 존재하지 않으므로, 두 id는
층 1의 키가 될 수 없다. Coordinator는 층 2를 쓰지 않고, 대신 매 attempt마다 새 값으로 갈렸는지를
검증한다. worker가 보내는 완료/heartbeat 보고의 task id와 dispatch id가 그 새 값과 일치하지 않으면
stale이며 어떤 lifecycle mutation도 실행하지 않는다.

reuse된 session에서도 이 경계는 매 Task마다 새로 세운다. 유지되는 session 기억은 탐색 최적화일 뿐
권위가 아니다. 새 Task의 층-1 경계, `artifact_contract`가 가리키는 artifact, repository 직접 확인보다
결코 우선하지 않으며 충돌하면 언제나 새 Task와 repository가 이긴다. 이전 Task의 "남은 일"은 새 Task의
명시적 요구사항으로 다시 쓰거나 전달하지 않는다. "아까 하던 것을 계속하라" 형태의 문장은 쓰지 않는다.
이전 phase를 참조할 때는 id가 아니라 artifact 경로로 참조한다.

이 절의 다섯 키는 이 section 앞의 여섯 키 목록을 대체하지 않는다. `current_phase`/`current_iteration`은
양쪽에 공통이고, `APPROVED_PREVIOUS_PHASE_OUTPUT`은 `artifact_contract`의 읽기 항목에,
`PREVIOUS_REVIEW_FINDINGS`는 `relevant_previous_findings`에 대응한다. `current_role`과
`artifact_contract`의 쓰기 항목만이 새로 필수가 되는 축이다.

```text
TASK_BOUNDARY_KEYS = current_role, current_phase, current_iteration, artifact_contract, relevant_previous_findings
DISPATCH_INJECTED_IDENTITY = task_id, dispatch_id, dispatch_capability, coordinator_handle
DISPATCH_IDENTITY_RULE = injected_by_orca_at_dispatch, new_value_every_attempt, never_written_by_coordinator
TASK_BOUNDARY_NEVER_CARRIED = previous_task_id, previous_dispatch_id, unfinished_instruction
```

#### Artifact path contract

`artifact_contract`이 가리키는 모든 산출물은 이 phase/iteration이 속한 현재 Orca Run의 디렉터리 아래에만
쓴다. run-id는 §6에서 이미 생성하거나 bind한 그 Run의 id이며, 이 절은 그 id를 directory identity로 재사용할
뿐 새 user-facing parameter를 추가하지 않는다.

```text
ARTIFACT_ROOT = artifacts/runs/<run-id>/
```

`<ARTIFACT_ROOT>`는 §6 1단계에서 run-id를 얻은 직후, 그 phase/iteration의 첫 Task를 만들거나 dispatch하기
**전에** Coordinator가 생성한다 (예: `mkdir -p`). 이는 Worker 개별의 판단에 맡기지 않는 orchestration
contract의 일부다 -- 어떤 Task spec도 아직 존재하지 않는 디렉터리를 `artifact_contract`로 가리키며
dispatch되지 않는다. 이미 존재하면 아무 것도 하지 않는다 (`mkdir -p`와 동일하게 idempotent).

역할/phase별 경로는 다음 규칙의 첫 일치로 정한다.

```text
1. phase가 final_review면        → <ARTIFACT_ROOT>FINAL_REVIEW.md            (attempt 1)
                                    <ARTIFACT_ROOT>FINAL_REVIEW_iteration<N>.md (attempt N>=2)
2. role이 worker면                → <ARTIFACT_ROOT><PHASE>.md                 (in-place 갱신, suffix 없음)
3. role이 reviewer면 (그 외)      → <ARTIFACT_ROOT>REVIEW_<PHASE>.md           (iteration 1)
                                    <ARTIFACT_ROOT>REVIEW_<PHASE>_iteration<N>.md (iteration N>=2)
```

`_iteration1` 형태는 어디에도 존재하지 않는다. BUGFIX/REFACTORING은 `<PHASE>`에 `BUGFIX`/`REFACTORING`을
대입하는 것 외에 다른 규칙을 갖지 않는다 -- specialized phase도 동일한 run 디렉터리 아래, 동일한 모양의
경로를 쓴다. Coordinator가 그 run 안에서 부가적으로 남기는 기록(예: ORCHESTRATOR_LOG.md, TIMING_LOG.md)도
같은 `<ARTIFACT_ROOT>` 아래에 쓴다. 이 절 앞의 다섯 키는 이 규칙으로 대체되지 않으며, `artifact_contract`의
값이 이 규칙을 따를 뿐이다.

이 run-id가 바뀌지 않는 한 어떤 phase/role/iteration의 산출물도 다른 run의 `<ARTIFACT_ROOT>`를 참조하지
않는다. `approved_baseline`, `current_delta`, `PREVIOUS_REVIEW_FINDINGS`가 가리키는 경로, §17 downstream
revalidation이 재검증하는 산출물은 전부 이 규칙 그대로 현재 run의 `<ARTIFACT_ROOT>` 아래에 있다. 다른 run이
이전에 `artifacts/` root 또는 다른 `<ARTIFACT_ROOT>`에 남긴 산출물은 migration하거나 삭제하지 않으며,
새 run이 그것을 자신의 approved_baseline이나 current_delta로 참조하지도 않는다.

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
REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED

## Summary
## Blocking Findings
## Non-Blocking Findings
## Test Review
## Final Decision
```

Finding:

```text
ID:
Quality Attribute: <ATTRIBUTE-ID> | G1 | G2 | G3 | G4 | G5 | NONE
Severity: CRITICAL | MAJOR | MINOR
Blocking: YES | NO
Location:
Issue:
Reason:
Required Action:
```

Severity와 Blocking은 서로 다른 축이다. Severity는 finding의 영향도를, Blocking은 이번 gate를
실패시켜야 하는지를 뜻한다. severity 자체는 FAIL의 근거가 아니며, `Blocking: YES`는 위반한 Quality
Attribute의 `blocking`이 true이거나 Minimal General Gate G1-G5를 위반했을 때만 성립한다.
`Quality Attribute: NONE`인 finding은 언제나 `Blocking: NO`다.
Blocking finding이 하나라도 존재하면 FAIL한다.

#### Reviewer context contract

phase Reviewer에게 전체 history를 다시 붓지 않는다. 아래 여덟 키로 delta부터 전달하고, 확인 범위는
제한하지 않는다.

- `original_objective` — 사용자 원문 요구. 이번 phase 때문에 축약하지 않는다.
- `current_phase` — 이번 review의 phase와 iteration.
- `approved_baseline` — 이미 PASS된 이전 phase 결과의 artifact 경로.
- `current_delta` — 이번 attempt가 실제로 바꾼 것. 변경된 파일과 섹션, diff 범위.
- `new_claims` — Worker가 이번에 새로 주장한 사실.
- `previous_findings` — 이번 Task가 해소해야 할 finding의 id와 원문.
- `validation` — 이번 attempt의 검증/테스트 실행 결과.
- `drill_down` — delta 밖으로 나가 확인할 때 쓸 진입점. 비워 보내지 않는다.

규칙 여섯 개.

1. delta는 시작점이지 경계가 아니다. Reviewer는 필요하다고 판단하면 repository 어디든 직접 확인한다.
   이 section 첫 문단이 규정한 직접 확인 의무는 이 계약으로 축소되지 않는다.
2. `drill_down`은 선택이 아니라 필수다. 그것이 규칙 1의 실행 수단이다.
3. `approved_baseline`은 immutable truth가 아니다. 이전 phase 결과와 이번 delta가 명백히 모순되면
   그것은 넘어갈 사항이 아니라 blocking finding이다.
4. correction re-review에서는 finding id별 Worker resolution, 변경된 파일과 섹션, 새 validation 결과를
   먼저 전달한다.
5. session이 재사용되어 Reviewer가 자기 이전 PASS를 기억하고 있더라도 그 기억은 증거가 아니다.
   이전 PASS를 옳다고 가정하지 않고 이번 delta를 처음 보는 것처럼 확인한다.
6. 이 계약은 section 17 Final Adversarial Reviewer에는 적용되지 않는다. Final Adversarial Review는
   attempt마다 새 session에서 자기 checklist 전체를 수행한다.

여덟 키는 `reviews/common.md`의 `## Direct Verification` 목록을 대체하지 않고 그 위에 매핑된다.
original requirement는 `original_objective`, approved 이전 phase 결과는 `approved_baseline`,
repository와 git diff는 `current_delta`와 `drill_down`, 테스트 실행 결과는 `validation`,
Worker validation evidence는 `new_claims`에 대응한다. 매핑은 전달 형식이며 확인 의무를 줄이지 않는다.

```text
REVIEWER_CONTEXT_KEYS = original_objective, current_phase, approved_baseline, current_delta, new_claims, previous_findings, validation, drill_down
REVIEWER_CONTEXT_MODE = delta_first
REVIEWER_DRILL_DOWN = mandatory_and_unrestricted
REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review
```

#### Quality profile contract

Reviewer의 PASS/FAIL 판단은 broad generic quality checklist가 아니라 profile-first다. 판단 근거는
아래 네 tier이며, Reviewer는 이 범위 밖의 일반적인 software-quality concern을 스스로 blocking finding으로
승격하지 않는다. generic best practice, 설계 취향, minor improvement는 Project Quality Profile이
`blocking: true`로 정의하지 않은 이상 FAIL의 근거가 아니다.

```text
1 explicit user/project requirements
2 applicable project quality profile attributes
3 current phase contract
4 minimal general gate
```

Minimal General Gate는 다섯 개이며 의도적으로 여기서 더 늘리지 않는다.

```text
G1 explicit requirement violation                명시적 요구사항 위반
G2 result does not work                          결과가 명백히 동작하지 않음
G3 severe regression                             심각한 regression
G4 data loss security irreversible side effect   데이터 손상 / 보안 / irreversible side effect 위험
G5 missing validation evidence                   판단에 필요한 최소 validation evidence 부재
```

프로젝트 profile은 `.orca/quality-profile.yaml`에서 읽는다. schema는 `version`과
`quality_attributes`(`id`, `category`, `name`, `blocking`, `description`, `applies_to`,
`verification`)뿐이며, inheritance / remote / organization-wide / merge hierarchy는 없다.
`applies_to`를 생략하면 all applicable workflow phases를 뜻한다. `applies_to`가 있으면 그 phase의
Worker/Reviewer에게만 전달되고, Final Adversarial Review는 requested workflow의 phase에 applicable한
attribute 전체를 받는다. BUGFIX / REFACTORING은 다른 phase와 같은 규칙을 쓰며 canonical order에 없으므로
downstream 개념을 갖지 않는다.

profile 상태는 세 가지이며 absent와 invalid를 절대 같은 것으로 취급하지 않는다.

```text
loaded   applicable attribute가 tier 2로 전달된다.
absent   경로가 존재하지 않는 경우에만 해당한다. 정상 상태이며 Explicit Requirements /
         Current Phase Contract / Minimal General Gate만 사용하고
         broad generic checklist를 복구하지 않는다.
invalid  Coordinator는 §6에서 Run을 bind한 직후, 첫 Task를 만들기 전에 profile을 resolve한다.
         invalid면 어떤 Task도 dispatch하지 않는다. 경로가 존재하지만 regular file이
         아니거나(디렉터리, 끊어진 symlink 등) 읽을 수 없는 경우도 absent가 아니라 invalid다.
```

profile은 Run 경계에서 **정확히 한 번** resolve하고, 그 하나의 resolution을 그 Run의 모든
Worker / phase Reviewer / correction / downstream revalidation / Final Reviewer spec에
그대로 전달한다. attempt마다 다시 읽지 않는다 -- Worker가 실행되는 동안 profile이 수정되면
그 Worker를 심사하는 Reviewer가 다른 quality model을 받게 되고, 그것이 바로 아래 "Worker와
Reviewer가 서로 다른 기준을 듣는" 상태다.

```text
STATUS: BLOCKED
REASON: INVALID_QUALITY_PROFILE
```

`invalid`를 dispatch 이전의 validation failure로 처리하는 이유: 이 실패는 Worker가 고칠 수 있는
결함이 아니라 판단 기준 자체의 부재이므로 correction loop에 넣을 대상이 아니고, Task가 아예 만들어지지
않으므로 "조용히 generic checklist로 되돌아가는" 경로가 습관이 아니라 구조적으로 존재하지 않게 된다.
이는 §7 INVALID_PHASE와 같은 모양의 pre-dispatch 검증이며 새 lifecycle state를 만들지 않는다.

verdict는 report 4개, workflow gate 2개다. PASS WITH NOTES와 BLOCKED는 report annotation이며 새
orchestration lifecycle state가 아니다. 따라서 task settlement, §12 FAIL Loop, §17 downstream
revalidation, Final Review trigger는 이 변경으로 달라지지 않는다.

```text
PASS            -> RESULT: PASS
PASS WITH NOTES -> RESULT: PASS      blocking violation 없음 + non-blocking finding 1개 이상
FAIL            -> RESULT: FAIL      blocking violation 1개 이상
BLOCKED         -> RESULT: FAIL      신뢰할 수 있는 verdict에 필요한 정보/evidence 부족
```

같은 quality model이 Worker에게도 전달된다. Worker Task spec과 Reviewer Task spec은 같은 resolution에서
만들어진 동일한 block을 받으므로 두 역할이 서로 다른 기준을 듣는 일이 없다. 전달 형식은 §9 Task boundary와
같은 방식으로 dispatch되는 spec 본문 안의 block이다.

```text
=== QUALITY GATE (profile-first) ===
```

```text
QUALITY_PROFILE_STATUS = loaded, absent, invalid
QUALITY_PROFILE_ABSENT_CONDITION = path_does_not_exist
QUALITY_PROFILE_RESOLUTION_SCOPE = resolved_once_per_run_never_per_attempt
QUALITY_PROFILE_INVALID_HANDLING = validation_failure_before_dispatch
QUALITY_PROFILE_ABSENT_BASIS = explicit_requirements, current_phase_contract, minimal_general_gate
QUALITY_ATTRIBUTE_APPLIES_TO_DEFAULT = all_applicable_workflow_phases
QUALITY_GATE_DECISION_PRIORITY = explicit_requirements, project_quality_attributes, current_phase_contract, minimal_general_gate
QUALITY_GATE_GENERAL_IDS = g1, g2, g3, g4, g5
QUALITY_GATE_SEVERITY_RULE = severity_is_not_blocking
QUALITY_GATE_BLOCKING_SOURCES = blocking_quality_attribute, minimal_general_gate
QUALITY_GATE_VERDICTS = pass, pass_with_notes, fail, blocked
QUALITY_GATE_WORKFLOW_VALUES = pass, fail
QUALITY_GATE_CONTEXT_KEYS = profile_status, profile_path, applicable_quality_attributes, blocking_quality_attributes, general_gate, decision_priority, non_blocking_by_default, verdict_semantics
QUALITY_GATE_CONTEXT_ROLES = worker, reviewer, final_reviewer
```

## 12. FAIL Loop

Reviewer PASS:

```text
current phase COMPLETED
→ 남은 requested phase가 있으면 다음 phase
→ 남은 requested phase가 없으면 §17 Final Adversarial Review gate
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

Final Adversarial Review FAIL이 유발한 correction도 이 FAIL Loop와 정확히 같은 모양이다.
차이는 correction 대상 phase를 Reviewer가 아니라 Final Reviewer의 `Responsible Phase`가 지정한다는 점뿐이다.
correction된 phase의 downstream phase를 재검증하는 §17 T5a revalidation round도 같은 모양이다.
차이는 해소할 finding이 없다는 점이며, 첫 Worker에게 resolution trace를 요구하지 않는다.

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
iteration counter는 두 개의 독립 domain을 가지며, 둘 다 같은 max-iterations 값으로 bound된다.

PHASE_ITERATIONS[p]      = phase p의 Reviewer attempt 총합. Final Adversarial Review가 유발한
                           correction의 Reviewer attempt도 이 counter를 계속 사용한다.
                           보고 필드: ITERATIONS_BY_PHASE  (기존 필드, 기존 의미)
FINAL_REVIEW_ITERATIONS  = fresh Final Adversarial Reviewer가 dispatch된 횟수.
                           보고 필드: FINAL_REVIEW_ITERATIONS  (신규, orchestration 보고 전용)

두 domain은 서로 소비하지 않는다. 새 user-facing parameter는 없다.
소진 시 STATUS: ESCALATED이며 REASON만 다르다.
  phase budget 소진        → REASON: MAX_ITERATIONS_REACHED (phase p)
  final review budget 소진 → REASON: FINAL_REVIEW_MAX_ITERATIONS_REACHED
```

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
+ Add/Modify Unit Test only if existing evidence is insufficient
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

1. 모든 requested phase가 PASS했고, §17 Final Adversarial Review가 PASS했는지 확인한다.
   둘 중 하나라도 아니면 STATUS는 COMPLETED가 아니다.
2. 각 phase/iteration에 필요한 Worker/Reviewer Task/Dispatch가 Orca state에 존재하는지 확인한다.
3. unresolved Blocking Finding이 없는지 확인한다.
4. 마지막 test/validation 결과를 확인한다.
5. 각 Dispatch가 네 축 (a)/(b)/(c1)/(c2) 모두에 기록된 outcome을 갖는지 확인한다 (axis (b)는 reuse, retain, release 또는 unsupervised 중 하나).
6. retain된 terminal이 있다면 사용자 요청과 retain 사유를 최종 보고에 기록한다.
7. close한 terminal마다 close 권한 근거(terminal role + provenance)가 기록되었는지, 각 Dispatch의
   finalization이 정확히 한 번인지, 그리고 모든 lifecycle 동작이 그 Dispatch의 finalization
   게이트를 통과한 뒤에 수행되었는지 확인한다.
8. Final Adversarial Review attempt마다 고유한 Task/Dispatch provenance와 새로 생성한 terminal,
   axis (b) != reuse인 네 축 행이 있는지, 그리고 attempt마다 artifacts/FINAL_REVIEW_*가 있는지 확인한다.
   그리고 T5a에서 재검증된 downstream phase가 FINAL_REVIEW_REVALIDATIONS에 빠짐없이 기록되었는지 확인한다.
9. reuse chain이 있다면 chain마다 terminal handle과 Dispatch 순서, chain의 종결 방식(retain/release/
   unsupervised)을 기록했는지, reuse 단계에서 lifecycle mutation 명령이 하나도 발행되지 않았는지,
   efficiency 수치가 placement 경로 라벨과 함께 기록되었는지 확인한다. 그 수치의 근거는 런타임 응답의
   terminal effect / process action / retained reason이며 ledger에 기록된 action 라벨이 아니다.

`## Orca Orchestration State`에는 Dispatch마다 다음 형식으로 네 축의 outcome을 기록한다.

```text
Dispatch <id> (task <id>, phase <PHASE>, iteration <n>)
  (a) settlement         : completed|failed @ <ts> | not-settled
  (b) worker resource    : reuse -> <다음 dispatch id> | retain | release | unsupervised
  (c1) process liveness  : live | already exited | disputed
  terminal role          : coordinator_session | setup_terminal | active_worker |
                           external_or_adopted | phase_worker | phase_reviewer | unknown_role
  (c2) cleanup authority : authorized <role + 근거 receipt> | not_authorized <role 또는 사유> | unknown
  -> action taken        : released by runtime | closed by coordinator | retained | nothing to do
```

```text
Reuse chains
  worker        : <terminal handle>  <dispatch id> -> <dispatch id> -> ...  (final: retain|release|unsupervised)
  reviewer      : <terminal handle>  <dispatch id> -> <dispatch id> -> ...  (final: retain|release|unsupervised)
  final review  : no chain (attempt마다 새 terminal)

Efficiency
  terminal_creations              : rung_3_observed = <n> | rung_1_unobserved = unknown (unobserved path)
  end_of_run_retained_terminals   : rung_3_observed = <n> | rung_1_unobserved = unknown (unobserved path)
  evidence                        : terminal effect / release process action / retained reason (ledger action 아님)
```

`(c2)`를 `authorized`로 적을 때는 `terminal role`이 `phase_worker`/`phase_reviewer`임과 근거 receipt를
둘 다 적는다. 하나라도 적을 수 없으면 `unknown`이며, `unknown`의 action은 언제나 `retained`다.
Coordinator 자신의 세션 handle은 이 보고에 `terminal role: coordinator_session` /
`(c2) not_authorized (own session)` / `action taken: retained`로 정확히 한 행 나타난다.

최종 보고:

```text
# Final Result

STATUS: COMPLETED
PHASES:
COMPLETED_PHASES:
WORKER:
REVIEWER:
ITERATIONS_BY_PHASE:
FINAL_REVIEW_ITERATIONS:

## Summary
## Changed Files / Artifacts
## Unit Tests / Validation
## Orca Orchestration State
## Final Adversarial Review
FINAL_REVIEW: PASS
FINAL_REVIEW_TASKS: task_<id> / dispatch_<id> (attempt 1)
FINAL_FINDINGS: none
FINAL_REVIEW_REVALIDATIONS: none
## Non-Blocking Recommendations
```

## 17. Final Adversarial Review

모든 requested phase가 Reviewer PASS를 받은 직후 Coordinator는 예외 없이 Final Adversarial Review gate를 한 번 실행한다.
이 gate는 phase가 아니다. `phases=`에 쓸 수 없고 끄는 parameter도 없으며, 5-phase 전체 run이든 `phases=bugfix` 단독이든
동일하다. PASS하지 못한 Run은 어떤 경로로도 `STATUS: COMPLETED`가 되지 않는다.

#### Role and freshness

세 번째 역할이 아니다. §11 Reviewer Contract를 그대로 따르는 Reviewer instance이며 출력도 §11과 같다. terminal role은
`phase_reviewer`, origin은 `self_created`, agent command는 phase Reviewer와 같은 `reviewer=`다. attempt마다 **새 terminal을
생성한다.** 이전 attempt의 terminal도 어떤 phase Reviewer terminal도 재사용하지 않는다. freshness는 model이 아니라 session의
속성이며, 목적은 이전 판정 context의 상속을 끊는 것이다. §6 `#### 1. Immediate worker reuse`의 재사용 권장은 여기에 적용되지 않는다.

#### Coordinator procedure

```text
1. 마지막 requested phase의 Reviewer 판정이 PASS이고 남은 requested phase가 없음을 확인한다.
2. FINAL_REVIEW_ITERATIONS += 1.
3. 이번 attempt의 Task graph를 만든다. Final Review Task는 dependency가 없는 단일 node다.
4. 새 terminal을 생성하고 즉시 ledger에 handle + role(active_worker) + origin(self_created) +
   intended_role(phase_reviewer)를 기록한다. §6 placement ladder로 입양시켜 Dispatch에 연결한다.
5. worker_done을 기다린 뒤 그 Dispatch의 lifecycle을 §6 four-axis 절차로 정확히 한 번 종결한다.
   axis (b)는 reuse가 아니다.
6. verdict를 평가하고 아래 T1/T2/T3로 분기한다.
```

dependency edge를 걸지 않는 것은 §6 Task graph ordering의 요구다. trigger가 마지막 Reviewer Task의 **완료된** 판정이므로 그 Task를 dependency로 선언하면 "dependency 완료 이후 dependent 생성"이라는 금지 패턴이 된다.

입력은 ORIGINAL_REQUEST / PHASES / provenance+ledger 요약 / FINAL_REVIEW_ITERATIONS와 max-iterations / 직전 attempt의
finding·resolution 표를 inline으로, phase별 approved·REVIEW artifact, 전체 diff(base..HEAD), 변경된 production file 목록,
test·validation 결과를 경로로 전달한다. 직전 attempt가 FAIL이었다면 이번 round에서 **어떤 phase가 어떤 finding id 때문에
수정되었고, 그로 인해 어떤 downstream phase가 T5a에서 재검증되었는지** 반드시 명시한다. 이 attempt는 그 재검증을
대체하는 것이 아니라 그 위에 추가로 걸리는 global gate다.

#### Review checklist

Final Reviewer도 §11 `#### Quality profile contract`의 quality model을 그대로 쓴다. 우선순위는
Explicit Requirements → blocking Project Quality Attributes → Minimal General Gate → cross-phase
consistency이며, 대상은 final implementation / diff와 test·validation evidence다. 이 attempt는
requested workflow의 phase에 applicable한 attribute 전체를 재검증할 수 있다.

Final Reviewer는 무한한 generic quality checklist를 생성하지 않는다. project profile에 없는 일반적
improvement나 non-blocking finding만 존재하는 경우 verdict는 PASS(WITH NOTES)이며, 그것만으로
correction loop를 시작하지 않는다. 아래 A-I는 blocking finding을 찾을 **탐색 축**이지 그 자체로
blocking criterion 목록이 아니다.

```text
A objective alignment        원래 요청이 실제로 충족되었는가
B cross-phase consistency    phase 산출물들이 서로 모순되지 않는가
C contract vs implementation 문서화된 계약과 코드가 일치하는가
D implementation vs tests    test가 실제 위험을 검증하는가, 통과를 위해 약화되지 않았는가
E docs vs behavior           문서가 실제 동작을 설명하는가
F lifecycle state machine    상태 전이와 counter가 문서와 코드에서 동일한가
G security destructive       파괴적 동작, secret, 범위 밖 파일 변경이 없는가
H over-engineering           요청되지 않은 abstraction이나 범위 확대가 없는가
I hidden coupling            의도치 않은 공유 자산/외부 계약 변경이 없는가
```

이전 phase Reviewer의 PASS 판정을 옳다고 가정하지 않는다.
Do NOT assume any previous Reviewer PASS decision is correct.

#### Final Review Finding Contract

§11의 Blocking Finding에 `Responsible Phase` 한 필드만 추가한다.

```text
ID: R1
Quality Attribute: <ATTRIBUTE-ID> | G1 | G2 | G3 | G4 | G5 | NONE
Severity: CRITICAL | MAJOR | MINOR
Blocking: YES | NO
Responsible Phase: analysis | plan | design | implementation | test | bugfix | refactoring
Location:
Issue:
Reason / Evidence:
Required Action:
```

판정 규칙은 §11과 같다. Blocking finding이 하나라도 남으면 FAIL, non-blocking finding만 있으면 PASS이며
그 finding은 `## Non-Blocking Recommendations`에 기록한다. `Responsible Phase`는 blocking finding에만
의미가 있다 -- non-blocking finding은 correction 대상이 아니므로 T3 매핑에 들어가지 않는다. 하나의 finding은 정확히 하나의 `Responsible Phase`를 가지며, 두
phase에 걸친 결함은 서로 다른 id의 두 finding으로 나눈다. 값은 아래 ladder의 첫 일치로 정한다.

```text
1. Location이 §9 Artifact path contract의 `<ARTIFACT_ROOT><PHASE>.md`면 → 그 PHASE.
2. 아니면 결함의 성격으로 매핑한다.
   잘못된 전제 / 요구사항 오독 / 놓친 제약   → ANALYSIS
   범위 누락 / 순서 / 계획된 검증의 부재     → PLAN
   코드는 명세를 따르는데 명세가 틀림        → DESIGN
   production code 동작 결함 / 계약 위반     → IMPLEMENTATION (specialized run이면 BUGFIX/REFACTORING)
   test 부재 / 불충분 / 결함을 못 잡는 test  → TEST
   문서와 실제 동작 불일치                  → 그 동작을 소유한 phase
3. 결과가 requested phase 집합에 없으면 canonical order에서 그 이하의 마지막 requested phase로 낮춘다.
4. 그래도 소유자가 없으면 STATUS: ESCALATED / REASON: OUT_OF_SCOPE_FINAL_REVIEW_FINDING.
```

#### Verdict and state machine

```text
T0  모든 requested phase PASS    → 새 attempt 생성 (FINAL_REVIEW_ITERATIONS += 1)
T1  attempt PASS                → STATUS: COMPLETED
T2  attempt FAIL이고 FINAL_REVIEW_ITERATIONS == max-iterations
                                → STATUS: ESCALATED
                                  REASON: FINAL_REVIEW_MAX_ITERATIONS_REACHED
                                  (correction Dispatch를 만들지 않는다)
T3  attempt FAIL이고 budget 잔여 → 이 Dispatch를 먼저 종결하고 finding을 phase로 매핑
T4  각 responsible phase p에 대해 PHASE_ITERATIONS[p] == max-iterations
                                → STATUS: ESCALATED / REASON: MAX_ITERATIONS_REACHED (phase p)
    아니면 correction Worker → p의 Reviewer 재검토 (§12 FAIL Loop 그대로)
T5a 모든 p가 다시 PASS했으면 downstream revalidation set D를 계산해 canonical order로 순차
    재검증한다. D가 공집합이면 아무 Dispatch도 만들지 않는다. 각 q ∈ D에 대해
    PHASE_ITERATIONS[q] == max-iterations
                                → STATUS: ESCALATED / REASON: MAX_ITERATIONS_REACHED (phase q)
    아니면 revalidation Worker → q의 Reviewer 재검토 (§12 FAIL Loop 그대로)
T5  D의 모든 q가 PASS         → T0으로 돌아가 새 fresh attempt를 만든다
```

T2의 마지막-attempt guard는 FAIL edge에서 **가장 먼저** 평가한다. finding을 phase로 매핑하기 전에, `PHASE_ITERATIONS`를 읽기 전에 평가한다. 순서가 뒤바뀌면 예산 소진 뒤에도 correction이 dispatch된다.

#### Downstream revalidation

correction은 그 phase의 산출물을 바꾸므로, 그보다 뒤 phase가 correction 이전 상태에 대해 받은 PASS는 더 이상
유효한 증거가 아니다. fresh Final Review는 그 재검증의 대체재가 아니라 추가적인 global gate다.

```text
D = requested phase 중, canonical order(analysis, plan, design, implementation, test)에서
    이번 attempt가 correction한 phase 중 **가장 이른** 것보다 뒤에 있는 phase 전부.
```

D는 그 phase가 실제로 바뀌었는지와 무관하게 **항상 전부** 재검증한다. "정말 바뀌었는가"는 Coordinator가 값싸게
증명할 수 없는 판단이며, 조건부 재검증은 stale PASS 구멍을 그대로 남긴다. bugfix / refactoring은 canonical
order에 없으므로 downstream을 갖지도, 남의 downstream이 되지도 않는다 — specialized run에서 D는 항상 공집합이고
T5a는 no-op이다. D는 canonical order의 suffix이므로 순차 실행만으로 연쇄가 자동 처리된다. IMPLEMENTATION
재검증이 코드를 바꾸면 그 뒤에 오는 TEST 재검증이 바뀐 코드를 본다.

revalidation round는 correction round가 아니다. q는 Final Reviewer에게 직접 FAIL을 받은 것이 아니므로
해소해야 할 finding 목록이 없고, 새 round 첫 Worker에게 resolution trace를 요구하지 않는다. 대신
`PREVIOUS_REVIEW_FINDINGS` 자리에 UPSTREAM_CORRECTION(어떤 phase가 어떤 finding id 때문에 어떻게 바뀌었는지)을
전달하고, "corrected upstream artifact에 비추어 네 phase 산출물을 재검증하고 무효화된 부분을 갱신하라.
바뀔 것이 없으면 없다고 명시하고 근거를 대라"를 요구한다. 그 다음은 보통의 Worker→Reviewer gate이며,
FAIL하면 §12 FAIL Loop가 그대로 적용된다.

산출물은 §9 Artifact path contract의 `<ARTIFACT_ROOT><PHASE>.md`를 in-place 갱신하고, Reviewer 기록은
기존 규칙대로 `<ARTIFACT_ROOT>REVIEW_<PHASE>_iteration<N>.md`다. counter는 `PHASE_ITERATIONS[q]`를 그대로
쓴다. 새 counter도 새 user-facing parameter도 없고, 소진 시 REASON은 T4와 같은
`MAX_ITERATIONS_REACHED (phase q)`다.

review 기록은 attempt 1이 `<ARTIFACT_ROOT>FINAL_REVIEW.md`, attempt N>=2가
`<ARTIFACT_ROOT>FINAL_REVIEW_iteration<N>.md`다.
`_iteration1` 형태는 존재하지 않으며 `<N>`은 그 attempt의 `FINAL_REVIEW_ITERATIONS` 값이다. 이 파일은 review 기록이지
production artifact가 아니므로 §11의 "Reviewer는 code/artifact를 직접 수정하지 않는다"에 위배되지 않으며, Final Adversarial
Reviewer도 자신이 찾은 결함을 직접 고치지 않는다.

#### Final review contract

```text
FINAL_REVIEW_TRIGGER = after_every_requested_phase_set
FINAL_REVIEW_STRUCTURE = orchestration_only_implicit_gate
FINAL_REVIEW_ROLE = phase_reviewer
FINAL_REVIEW_TERMINAL_FRESHNESS = new_terminal_per_attempt
FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = retain, release, unsupervised
FINAL_REVIEW_TASK_GRAPH = single_node_no_dependencies
FINAL_REVIEW_COUNTER_DOMAINS = phase_iterations, final_review_iterations
FINAL_REVIEW_ITERATION_BOUND = max_iterations
FINAL_REVIEW_LAST_ATTEMPT_FAIL = escalate_before_correction_routing
FINAL_REVIEW_EXHAUSTION_REASON = final_review_max_iterations_reached
FINAL_REVIEW_OUT_OF_SCOPE_REASON = out_of_scope_final_review_finding
FINAL_REVIEW_DOWNSTREAM_REVALIDATION = all_requested_phases_after_earliest_corrected_phase
FINAL_REVIEW_COMPLETION_GATE = requested_phases_pass_and_final_review_pass
```

## 18. Core Invariants

```text
Exactly 2 roles: Worker + Reviewer
Worker != Reviewer
Worker Dispatch != Reviewer Dispatch
Real Orca Run/Task/Dispatch provenance required
Load version-matched Orca orchestration guide before orchestration commands
Load version-matched Orca CLI guide before terminal lifecycle commands
Never guess Orca CLI grammar
Every settled worker terminal → immediate reuse, explicit retain, release, or unsupervised (no worker resource)
Same role session reuse may cross phase and iteration boundaries; role swap is never reuse
Reuse issues no lifecycle mutation command; ownership transfers when the next task starts on the same terminal
Every dispatch carries freshly injected task and dispatch identity; no identity is ever carried over
Reviewer delta context is a starting point, never a boundary on direct verification
Task graph created before worker dispatch; dependents never created after dependency completion
Manual task readiness override is recovery-only
Settlement, worker-resource registration, process liveness, and cleanup authority are four separate axes
Terminal close requires proven cleanup authority; otherwise retain and report
Cleanup authority requires a close eligible terminal role as well as proven ownership
The coordinator never closes its own terminal, a setup terminal, or an adopted terminal
No lifecycle action runs before the per dispatch finalization gate has been checked
Never leave a completed worker live indefinitely
Specialized phase combinations must be explicitly supported
Reviewer never fixes its own findings
Reviewer FAIL → new Worker correction dispatch
Current phase PASS required before next phase
IMPLEMENTATION production code change → Unit Test add/modify required
BUGFIX → Regression Test required
REFACTORING → relevant existing Unit Test execution + conditional test changes
Agent command → safe token + PATH resolution
No silent fallback to direct-session loop
Final Adversarial Review is a Reviewer instance in a fresh session, not a third role
Final Adversarial Review runs after every requested phase set, with no exception
All requested phases PASS + Final Adversarial Review PASS required for COMPLETED
Every Final Adversarial Review attempt uses a newly created terminal
Final Adversarial Review dispatch worker resource is never reuse
Final Adversarial Review task has no dependencies and is created before its dispatch
Final Review FAIL at the iteration bound escalates before any correction dispatch
Final Adversarial Reviewer never fixes its own findings
One quality profile resolution per run reaches every worker, reviewer, correction, revalidation and final reviewer spec
A quality profile path that exists but is unreadable or not a regular file is invalid, never absent
Reviewer verdict is decided profile-first: requirements, applicable quality attributes, phase contract, minimal general gate
Severity expresses impact; blocking expresses gate failure, and only a blocking quality attribute or the minimal general gate makes a finding blocking
Generic best practice outside the project quality profile never becomes a blocking finding
Missing project quality profile never restores the broad generic checklist
Invalid project quality profile is a pre-dispatch validation failure, never a silent fallback
PASS WITH NOTES and BLOCKED are review report annotations, never new lifecycle states
Worker and Reviewer receive the same applicable quality attributes from one resolution
```
