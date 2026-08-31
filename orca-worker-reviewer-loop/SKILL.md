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
profile=<name>
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
  "agent_profile": {
    "parameter": "profile",
    "project_source": ".orca/agent-profiles.yaml",
    "user_source": "~/.orca/agent-profiles.yaml",
    "schema_versions": [1],
    "source_precedence": ["project_local", "user_global"],
    "merge": "whole_definition"
  },
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
    "unsupported_phase_combination": "UNSUPPORTED_PHASE_COMBINATION",
    "invalid_agent_profile": "INVALID_AGENT_PROFILE",
    "unknown_agent_profile": "UNKNOWN_AGENT_PROFILE",
    "agent_role_unresolved": "AGENT_ROLE_UNRESOLVED"
  },
  "decision_policy": {
    "schema_version": 1,
    "state_scope": "per_decision_item_with_derived_check_aggregate",
    "aggregate_order": ["CONFLICT", "NEEDS_INPUT", "ASSUMPTION_ALLOWED", "CLEAR"],
    "states": {
      "CLEAR": {"workflow": "continue", "user_decision_required": false, "reason_code_required": false},
      "ASSUMPTION_ALLOWED": {"workflow": "continue_and_review", "user_decision_required": false, "reason_code_required": true},
      "NEEDS_INPUT": {"workflow": "pause_and_ask", "user_decision_required": true, "reason_code_required": true},
      "CONFLICT": {"workflow": "pause_and_request_resolution", "user_decision_required": true, "reason_code_required": true}
    },
    "transitions": {
      "CLEAR": {"CLEAR": "allowed", "ASSUMPTION_ALLOWED": "allowed", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"},
      "ASSUMPTION_ALLOWED": {"CLEAR": "requires_retraction", "ASSUMPTION_ALLOWED": "allowed", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"},
      "NEEDS_INPUT": {"CLEAR": "requires_user_decision", "ASSUMPTION_ALLOWED": "forbidden", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"},
      "CONFLICT": {"CLEAR": "requires_user_decision", "ASSUMPTION_ALLOWED": "forbidden", "NEEDS_INPUT": "allowed", "CONFLICT": "allowed"}
    },
    "downstream_rule": "an unresolved NEEDS_INPUT or CONFLICT item may not be reported CLEAR by a later phase",
    "entry_clauses": {
      "NEEDS_INPUT": {
        "N-1": "a boundary element is true, is not determined by a policy source, and is not decided by an explicit authorization",
        "N-2": "required user intent is absent",
        "N-3": "the item crosses the autonomy boundary but cannot be classified under these closed vocabularies"
      },
      "CONFLICT": {
        "C-1": "two or more explicit requirements are contradictory",
        "C-2": "an explicit requirement contradicts an already-accepted decision of this run",
        "C-3": "an explicit requirement contradicts a non-overridable project invariant"
      }
    },
    "clause_predicates": {"N-1": "undetermined_boundary_element", "N-2": "absent_user_intent", "N-3": "unclassifiable_item", "C-1": "declared_contradiction", "C-2": "declared_contradiction", "C-3": "declared_contradiction"},
    "reason_codes": {
      "repository_policy": {"state": "ASSUMPTION_ALLOWED"},
      "explicit_requirement": {"state": "ASSUMPTION_ALLOWED"},
      "phase_contract": {"state": "ASSUMPTION_ALLOWED"},
      "quality_profile_attribute": {"state": "ASSUMPTION_ALLOWED"},
      "ambiguous_requirement": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "ambiguity"},
      "missing_user_intent": {"state": "NEEDS_INPUT", "clause": "N-2", "boundary_element": "ambiguity"},
      "irreversible_action": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "reversibility"},
      "blast_radius_beyond_scope": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "blast_radius"},
      "monetary_cost": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "monetary_cost"},
      "security_impact": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "security"},
      "privacy_impact": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "privacy"},
      "compliance_impact": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "compliance"},
      "long_term_lock_in": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "long_term_lock_in"},
      "authority_reserved_to_user": {"state": "NEEDS_INPUT", "clause": "N-1", "boundary_element": "explicit_user_authority"},
      "unclassifiable_decision": {"state": "NEEDS_INPUT", "clause": "N-3", "required_evidence": ["reason_code", "what_is_missing", "why_policy_cannot_decide", "classification_attempted"]},
      "requirement_contradiction": {"state": "CONFLICT", "clause": "C-1"},
      "requirement_vs_accepted_decision": {"state": "CONFLICT", "clause": "C-2"},
      "requirement_vs_safety_floor": {"state": "CONFLICT", "clause": "C-3"}
    },
    "entry_conditions": {
      "CLEAR": {"any_of": ["no_open_decision_item", "determining_policy_source", "explicit_user_authorization"]},
      "ASSUMPTION_ALLOWED": {"all_of": ["all_safety_facts_declared", "reversible_in_run", "blast_radius_within_scope", "no_high_impact_element", "supporting_policy_source", "no_reserved_user_authority"]},
      "NEEDS_INPUT": {"any_of": ["undetermined_boundary_element", "absent_user_intent", "unclassifiable_item"]},
      "CONFLICT": {"any_of": ["declared_contradiction"]}
    },
    "boundary_elements": {
      "ambiguity": {"kind": "declared", "triggering": true},
      "explicit_requirement_conflict": {"kind": "citations", "minimum": 2, "triggering": "at_minimum"},
      "reversibility": {"kind": "enum", "values": ["reversible_in_run", "reversible_with_effort", "irreversible"], "triggering": ["irreversible"]},
      "blast_radius": {"kind": "enum", "values": ["current_change", "module", "repository", "external_system"], "triggering": ["repository", "external_system"]},
      "monetary_cost": {"kind": "boolean", "triggering": true},
      "security": {"kind": "boolean", "triggering": true},
      "privacy": {"kind": "boolean", "triggering": true},
      "compliance": {"kind": "boolean", "triggering": true},
      "long_term_lock_in": {"kind": "boolean", "triggering": true},
      "repository_project_policy": {"kind": "policy_source", "triggering": null},
      "explicit_user_authority": {"kind": "user_decision", "triggering": ["reserved"]}
    },
    "authority_precedence": {"policy_source_cannot_resolve": ["explicit_user_authority", "explicit_requirement_conflict"]},
    "policy_source_roles": ["determines", "supports"],
    "policy_source_kinds": ["file_path", "requirement_id", "quality_attribute_id", "phase_contract_section"],
    "required_evidence": {
      "CLEAR": [],
      "ASSUMPTION_ALLOWED": ["reason_code", "policy_source", "reversibility", "impact", "retraction_condition"],
      "NEEDS_INPUT": ["reason_code", "boundary_element", "what_is_missing", "why_policy_cannot_decide"],
      "CONFLICT": ["reason_code", "citations", "why_they_cannot_both_hold"]
    },
    "assumption_allowed_requires": {"policy_source_role": "supports", "all_required_evidence_non_empty": true, "declared_safety_facts": ["blast_radius", "monetary_cost", "security", "privacy", "compliance", "long_term_lock_in"], "absent_explicit_user_authority": "not_reserved"},
    "assumption_allowed_forbidden_when": {
      "reversibility_in": ["irreversible"],
      "blast_radius_in_with_irreversible": ["repository", "external_system"],
      "any_true_of": ["monetary_cost", "security", "privacy", "compliance", "long_term_lock_in"],
      "explicit_user_authority_reserved": true,
      "exception_allowed": false
    },
    "user_decision_fields": ["source", "where_recorded", "resolves"],
    "user_decision_sources": ["explicit_user_reply", "prior_explicit_user_authorization"],
    "forbidden_authority_sources": ["model_confidence", "timeout", "no_response", "worker_reviewer_agreement", "recommended_default"],
    "citation_minimum": {"CONFLICT": 2},
    "independent_axes": ["risk", "quality_profile", "agent_profile"]
  }
}
```

## Decision Policy

위 `decision_policy` block은 bounded autonomy의 machine-readable 계약이다. 네 decision state는
`CLEAR` / `ASSUMPTION_ALLOWED` / `NEEDS_INPUT` / `CONFLICT`이며, 아래는 그 block이 강제하는
규칙의 *이유*다 — 규칙만 알고 이유를 모르면 막혔을 때 우회하게 된다.

**축 분리.** decision state는 RUN_STATUS / Worker STATUS / REVIEW_VERDICT와 별개의 축이다.
OS-28은 그 셋 중 어느 것도 바꾸지 않는다. decision state `CONFLICT`는 invocation 검증 error code
`PHASE_CONFLICT`와 무관하고, `NEEDS_INPUT`은 Worker의 `STATUS: BLOCKED`와 다르다.

**두 pause state의 구분.** NEEDS_INPUT은 정보가 없는 것이고 CONFLICT는 정보가 모순되는 것이다.
전자는 "무엇을 원하는가"를 묻고 후자는 "둘 다 만족할 수 없으니 어느 쪽인가"를 묻는다.

**답변의 귀결.** 답변을 받은 항목은 CLEAR가 되며 ASSUMPTION_ALLOWED가 되지 않는다. 사용자가
결정한 뒤에는 가정할 것이 남지 않기 때문이다. `NEEDS_INPUT`/`CONFLICT`에서 `ASSUMPTION_ALLOWED`로
가는 전이는 `user_decision` 유무와 무관하게 금지다.

**고영향 항목.** INV-4에는 예외가 없다. 되돌릴 수 없거나 monetary / security / privacy /
compliance / long-term lock-in 중 하나가 참이면 `ASSUMPTION_ALLOWED`가 될 수 없으며, 이를
결정하는 policy source나 명시적 authorization이 있어도 마찬가지다 — 그런 항목은
`ASSUMPTION_ALLOWED`가 열리는 것이 아니라 `CLEAR`로 이동한다.

**증명되지 않은 것은 안전이 아니다.** `ASSUMPTION_ALLOWED`는 `declared_safety_facts`가 지명한
여섯 fact — blast radius, monetary cost, security, privacy, compliance, long-term lock-in —
을 record가 **명시적으로 선언했을 때에만** 허용된다. 선언하지 않은 fact는 거짓이 아니라 미상이며,
미상은 자동 진행의 근거가 되지 못한다. 자유 서술 `impact` 문자열은 이 여섯 fact를 대신하지 않는다.
사용자 권한이 선언되지 않았을 때 그것이 무엇을 뜻하는지는 `absent_explicit_user_authority`가
계약에서 명시한다 — 코드의 암묵적 default가 아니다.

**clause는 선언이 아니라 증명이다.** reason code가 rest하는 clause는 record에서 실제로
증명되어야 한다. `clause_predicates`가 각 clause(N-1/N-2/N-3, C-1/C-2/C-3)에 그것을 증명하는
predicate를 지정하며, `validate_record()`는 `permitted_states()`와 같은 predicate로 그것을
평가한다. `missing_user_intent`는 N-2를, `unclassifiable_decision`은 N-3를 스스로 증명해야
하고, 다른 clause의 증거로 대신할 수 없다.

**권한이 아닌 것.** 모델 확신, Worker/Reviewer 합의, 권고 default, timeout, 무응답은 사용자
권한의 근거가 아니다.

**축 독립성.** risk / quality profile / agent profile은 decision authority와 독립적인 축이며
state 선택 입력이 아니다. risk level을 바꾸어도 자동으로 결정할 수 있는 범위는 넓어지지 않는다.

이 계약은 **정의**다. 각 phase gate에서 검사를 실행하는 것(OS-29), 질문을 구성하는 것(OS-30),
응답을 기다렸다 재개하는 것(OS-31)은 이 Skill에 아직 구현되어 있지 않다.

이 Skill에는 risk 축이 없지만 decision policy 계약은 동일하다 — 계약이 risk에 의존하지 않으므로
risk 축이 있는 Skill과 같은 문언을 읽는다.

**gate 결과와 문서 section은 다른 객체다.** gate 경계에서 decision 결과는 필수이며 명시적이다.
섹션의 optional 여부와 다른 객체다. `## Decision Record` section은 여전히 optional이며 없어도
계약 위반이 아니다. 그러나 결과 본문의 `DECISION_GATE_STATE`와 그 record는 경계마다 필수이고,
없으면 그 경계는 진행하지 않는다.

**부재는 CLEAR가 아니다.** "결정할 것이 없었다"는 CLEAR로 단언되어야 하며 기록의 부재로 추정될
수 없다. 기록 없음, 형식 오류, 알 수 없는 schema, 빠진 safety fact, 알 수 없는 state나 reason
code, 모델 확신, Worker/Reviewer 합의, timeout, 무응답, 권고 default의 존재 — 어느 것도 CLEAR로
진행할 근거가 되지 못한다.

**두 채널, 하나의 권위.** 기계가 읽는 record가 authority이고 Markdown 요약은 사람을 위한
설명이다. 둘이 어긋나면 진행하지 않고 막힌다.

decision **semantics**는 두 Skill이 동일하다. Orca lifecycle 위에서 gate가 실행되는 위치·terminal
기록·dispatch 차단은 orchestration Skill 전용이며 이 Skill에는 없다.

## 5. Agent Command Policy

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

## Agent Profile

`profile=<name>`는 named Agent Profile을 선택한다. Agent Profile은 **누가 실행하는가**만 정한다 —
requested phase 집합, risk level, gate/correction/revalidation lifecycle, Final Review 요구,
PASS/FAIL 기준을 바꾸지 않는다.

profile 파일은 두 곳에서 읽는다.

```text
project-local  <project>/.orca/agent-profiles.yaml
user-global    ~/.orca/agent-profiles.yaml
```

같은 이름이 양쪽에 있으면 **project-local 정의가 통째로 이긴다.** field-level merge는 하지 않는다 —
project-local이 생략한 필드를 user-global에서 빌려오지 않는다.

resolution은 **short-circuit**한다. requested profile을 project-local에서 먼저 찾고, 거기 있으면
그 정의로 즉시 확정한다 — user-global 파일은 이 경우 아예 열지 않는다. project-local에 그 이름이
없을 때에만 user-global을 consult한다. project-local 자체가 malformed면 (그 파일이 요청된 이름을
가졌는지 판단할 수 없으므로) 그대로 fail closed하지만, project-local이 정상적으로 파싱되어 요청된
profile을 찾았다면 user-global 파일의 상태 — malformed든 무엇이든 — 는 그 selection에 영향을 주지
않는다.

schema는 다음이 전부다.

```yaml
version: 1
profiles:
  <name>:
    defaults:
      worker: <agent-command>
      reviewer: <agent-command>
    phases:
      <phase>:
        worker: <agent-command>
        reviewer: <agent-command>
    final_review:
      reviewer: <agent-command>
```

`<phase>`는 이 Skill이 지원하는 7개 phase(analysis, plan, design, implementation, test, bugfix,
refactoring)뿐이다. profile에 어떤 phase의 routing이 있다는 것이 그 phase를 실행한다는 뜻은 아니다 —
실행 대상은 `phases` contract만 정한다.

role별 resolution 순서는 다음과 같다. phase role과 Final Reviewer는 **1순위가 서로 반대**다.

```text
Phase Worker   : explicit worker   > profile.phases.<phase>.worker   > profile.defaults.worker   > unresolved
Phase Reviewer : explicit reviewer > profile.phases.<phase>.reviewer > profile.defaults.reviewer > unresolved
Final Reviewer : profile.final_review.reviewer > explicit reviewer   > profile.defaults.reviewer > unresolved
```

명시적으로 선택된 profile은 **self-contained resolution domain**이다. 다른 profile이나 profile 없는
경로의 default에서 빠진 필드를 채우지 않는다.

`profile`을 생략하면 기존 동작을 그대로 유지한다. profile 파일을 읽지 않으며 `worker=`/`reviewer=`와
default 처리, `WORKER_REVIEWER_MUST_DIFFER` 검사가 지금과 동일하게 적용된다.

`profile`이 명시된 경우 Run/Task/Dispatch를 만들기 **전에** 다음 순서로 처리한다.

```text
1. profile source 읽기 + schema 검증                       (여기서는 command를 검사하지 않는다)
2. selected profile 전체 정의 + 참여하는 explicit worker/reviewer에
   token -> allowlist 검사 (requested phase, required 여부와 무관)
3. requested phase와 Final Review에 대한 routing materialize
4. required role의 resolved command에만 PATH 검사
5. required role이 전부 resolve되었는지 검사
6. 위가 모두 통과한 경우에만 Run 생성
```

2번의 검사 대상은 **selected profile이 선언한 모든 command**다 — `defaults`, 이 invocation이 요청하지
않은 phase를 포함한 모든 `phases.<phase>`, `final_review`, 그리고 이 invocation에 실제로 주어진 explicit
`worker=`/`reviewer=` 값까지 전부 포함하며, requested phase 여부나 required 여부와 무관하다. profile은
하나의 trust document이므로, 이번 invocation이 `refactoring`을 요청하지 않았다고 해서
`phases.refactoring.worker: bash`가 안전해지지는 않는다 — 같은 profile의 다음 invocation이 그 phase를
요청할 수 있고, audit evidence는 실제로 materialize된 entry를 optional 포함 전부 기록하기 때문이다.
2번은 phase나 risk 정보 없이 selection 직후 정확히 한 번 실행되며 PATH는 절대 확인하지 않는다.
4번의 PATH 검사만 **required role로 좁힌다** — PATH 존재 여부는 trust 문제가 아니라 이 machine의 환경
사실이며, dispatch되지 않는 role의 command가 설치되어 있지 않다는 이유로 run을 막을 필요는 없다.
required role은 이 run에서 실행될 수 있는 command 집합과 정확히 같으므로 이 좁힘이 trust boundary에
빈틈을 만들지 않는다.

profile을 arbitrary shell execution 통로로 쓰지 않는다. resolved command에는 §5의 기존 agent
command 정책(safe token / trust boundary / PATH resolution)을 그대로 적용하며 인자를 붙이지 않는다.

실패는 전부 Run/Task/Dispatch 생성 이전의 validation failure이며 correction loop 대상이 아니다.

```text
STATUS: BLOCKED
REASON: INVALID_AGENT_PROFILE
```

```text
STATUS: BLOCKED
REASON: UNKNOWN_AGENT_PROFILE
```

```text
STATUS: BLOCKED
REASON: AGENT_ROLE_UNRESOLVED
```

`INVALID_AGENT_PROFILE`은 파일이 존재하지만 malformed YAML / 미지원 `version` / unknown 또는 중복 키 /
unknown phase key / 비어 있거나 문자열이 아닌 command 값 / 읽을 수 없는 경로인 경우다.
`UNKNOWN_AGENT_PROFILE`은 그 이름의 profile이 두 source 어디에도 없는 경우이며, 값이 없는
`profile=`도 생략이 아니라 명시적으로 잘못된 값이므로 여기에 해당한다.
`AGENT_ROLE_UNRESOLVED`는 required role이 precedence 체인을 모두 거쳐도 command를 얻지 못한 경우다.

resolved routing은 이 run 동안 immutable하다. profile 파일이 run 중 변경되어도 correction과
re-review는 profile을 다시 읽지 않고 최초 resolution을 그대로 사용한다.

이 Skill에는 risk 축이 없으므로 모든 phase에서 Reviewer가 required다. Worker만 정의한 profile은 이
Skill에서는 언제나 `AGENT_ROLE_UNRESOLVED`로 막힌다.

이 Skill에는 run 전체를 대상으로 하는 최종 Reviewer gate가 없다. `final_review.reviewer`는 schema에
존재하는 **알려진 키이므로 unknown key가 아니며**, 이 Skill은 그 값을 읽지 않고 무시한다. 하나의
profile 파일이 두 Skill을 모두 서비스할 수 있어야 하기 때문이다. 그 role에 대한 evidence도 이
Skill에는 해당하지 않는다.

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

agent는 PATH를 통해 resolve한 command token 자체를 entry point로 실행한다.

```text
<agent-command>
```

Skill은 model, permission 또는 vendor-specific argument를 추가하지 않는다. 필요한 옵션은
해당 CLI의 configuration 또는 model/permission-pinned wrapper command가 소유한다.

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

known command도 PATH에서 resolve되어야 한다. wrapper 내부 모델명이나 vendor별 model-selection
syntax는 해석하지 않으며, generic CLI의 현재 configuration 또는 model-pinned wrapper가 모델을
선택할 책임을 가진다.

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

재사용 조건에는 agent identity도 포함된다. 이전 session과 다음 Task의 **resolved role command**가
같을 때에만 재사용하며, resolved role command가 달라지면 같은 role이라도 새 session을 사용한다.
Agent Profile이 선택된 run에서는 phase마다 resolved command가 달라질 수 있다.

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

명시적 `phases=`의 각 값은 위 지원 phase 중 하나여야 한다. 알 수 없는 phase가 포함되면:

```text
STATUS: BLOCKED
REASON: INVALID_PHASE
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
- Reviewer policy: `reviews/common.md` + `reviews/bugfix.md`

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
2. agent command token 형식 검증
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

`profile`이 선택된 run에서는 다음 두 값도 함께 보존한다. profile이 없는 run에서는 존재하지 않는다.

```text
AGENT_PROFILE
AGENT_ROUTING
```

보존된 routing은 그 실행 동안 immutable하다. iteration이 바뀌어도 profile 파일을 다시 읽지 않는다.

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
<worker>
```

## 14. Worker Result Contract

```text
# Worker Result

STATUS: COMPLETE | BLOCKED
DECISION_GATE_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT

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
<reviewer>
```

Reviewer는 실제 repository, diff, artifact, tests, test result를 가능한 한 직접 확인한다.

## 16. Review Result Contract

```text
# Review Result

RESULT: PASS | FAIL
DECISION_GATE_STATE: CLEAR | ASSUMPTION_ALLOWED | NEEDS_INPUT | CONFLICT

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

Production code를 변경하는 다음 작업에서는 대응 Unit Test 추가/수정과 실행이 필수다.

```text
IMPLEMENTATION
BUGFIX
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

REFACTORING은 behavior preservation과 relevant existing Unit Test 실행/PASS가 필수다.
새 테스트 추가/수정은 기존 테스트만으로 preservation evidence가 충분하지 않을 때 필요하다.

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

`profile`이 선택된 run에서만 최종 보고에 다음 두 줄을 추가한다. profile이 없는 run의 최종 보고는
지금과 문자 단위로 동일하며 이 두 줄이 존재하지 않는다 — `AGENT_PROFILE: none`이라고 적지도 않는다.

```text
AGENT_PROFILE: <name> (<project_local|user_global>)
AGENT_ROUTING:
  <phase>  worker=<command> (<origin>)  reviewer=<command> (<origin>[, optional])
```

`AGENT_ROUTING:`에는 requested phase 전체의 Worker/Reviewer가 나타나며, 그 risk level에서 dispatch되지
않는 optional Reviewer도 `optional` 표시와 함께 기록한다. optional은 dispatch 요건이지 기록 면제가
아니다. profile이 선택된 run에서는 `WORKER:` / `REVIEWER:` 줄이 phase마다 달라질 수 있으므로
`(per phase — see AGENT_ROUTING)`을 담고, profile이 없는 run에서는 지금과 같은 단일 값을 유지한다.

이 Skill에는 그 gate가 없으므로 `AGENT_ROUTING:`에 final reviewer 줄이 존재하지 않는다.

## 29. Core Invariants

```text
Exactly 2 roles: Worker + Reviewer
Worker != Reviewer
Worker Session != Reviewer Session
Reviewer = Review / Decide only
Reviewer FAIL → Return to Worker
IMPLEMENTATION production code change → Unit Test add/modify required
BUGFIX → Regression Test required
REFACTORING → relevant existing Unit Test execution + conditional test changes
No required Unit Test → FAIL
Agent command → safe token + PATH resolution
phases=A,B,C → A then B then C
Invalid canonical order → BLOCK
Explicit phases vs natural-language conflict → BLOCK
Next phase starts only after current phase PASS
```
