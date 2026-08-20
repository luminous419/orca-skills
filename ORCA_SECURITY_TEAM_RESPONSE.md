# Orca 도입 보안 검토 후속 답변 자료

> 목적: Security Engineering의 후속 질의(Q1, Q2)에 대해 Orca 공식 문서를 근거로 답변하기 위한 위키용 정리 문서
>
> 기준: Orca 공식 문서(`onorca.dev`) 기준
>
> 최종 검토일: 2026-08-20

---

## 1. 배경

이번 도입 요청의 목적은 **새로운 외부 AI 모델이나 AI SaaS를 추가하는 것이 아니라**, 현재 회사에서 승인되어 사용 중인 AI 개발 환경을 보다 효율적으로 활용하기 위한 것입니다.

현재 회사의 AI 개발 환경은 다음과 같습니다.

- 승인된 개발 도구: Claude Code, IntelliJ, VS Code
- LLM Serving: AWS Instance + vLLM
- Gateway: Portkey
- 승인 모델: GLM-5.2, Gemma-4

Orca 공식 문서에서도 Orca 자체를 AI 모델로 설명하지 않고, 이미 사용 중인 CLI 기반 AI Agent를 실행·관리하는 환경으로 설명합니다. ([What is Orca? → What Orca is not](https://www.onorca.dev/docs#what-orca-is-not))

따라서 본 검토의 핵심은 **Orca를 통해 새로운 모델 접근 경로를 추가하는 것인지**, 또는 **기존 승인된 Claude Code 및 사내 LLM 연결 경로를 유지하면서 orchestration 기능만 추가하는 것인지**를 확인하는 것입니다.

---

# Q1. Orca 도입 시 AI-assisted development의 효율이 구체적으로 어떻게 향상되는가?

Orca의 도입 효과는 일반적인 IDE 기능보다는 **여러 AI Coding Agent를 동시에 실행하고, 역할을 분리하고, 작업 흐름을 관리하는 기능**에 있습니다.

## 1. Multi-Agent Orchestration

Orca는 여러 AI Agent를 하나의 작업 흐름 안에서 관리하기 위한 구조를 제공합니다. 공식 Orchestration 문서에서는 `Run`, `Task`, `Dispatch`, `supervised worker`, `message`, `decision gate`를 이용해 여러 Agent의 작업을 구조화할 수 있다고 설명합니다. ([Orchestration → Core model](https://www.onorca.dev/docs/cli/orchestration#core-model))

또한 작업 간 dependency와 상태(`pending`, `ready`, `dispatched`, `completed`, `failed`, `blocked`)를 관리할 수 있으며, 특정 결정이 완료될 때까지 다음 작업을 중단시키는 Decision Gate도 제공합니다. ([Orchestration → Decision gates](https://www.onorca.dev/docs/cli/orchestration#decision-gates))

이를 실제 개발 프로세스에 적용하면 다음과 같은 형태가 가능합니다.

```text
Plan 작성 Agent
      │
      ▼
Plan Review Agent
      │
   PASS / FAIL
      │
      ├─ FAIL → Plan 재작성
      │
      └─ PASS
           │
           ▼
       Design Agent
           │
           ▼
       Review Agent
           │
           ▼
     Implementation
```

즉 기존에는 개발자가 각각의 AI Session을 직접 생성하고 결과를 복사하여 다른 Agent에게 전달하고, PASS/FAIL 여부를 직접 판단하면서 다음 단계로 이동해야 했다면, Orca에서는 이러한 **Agent 간 작업 분배, 상태 추적, 완료 확인, 다음 단계 전환**을 orchestration workflow로 관리할 수 있습니다.

기대 효과는 단순히 “AI를 하나 더 사용하는 것”이 아니라 다음과 같습니다.

- 작성자(Creator)와 검토자(Reviewer)의 역할 분리
- Plan → Design → Implementation 등 단계별 결과 검증
- Review FAIL 시 이전 단계 자동 재수행 구조 구성
- 여러 Agent 작업 상태를 한 곳에서 추적
- 개발자가 Agent 간 결과 전달과 상태 관리에 사용하는 반복 작업 감소

공식 문서도 Orchestration을 “structured multi-agent layer”로 설명하며, ownership, completion tracking, DAG가 필요한 경우 사용하도록 안내합니다. ([Orchestration](https://www.onorca.dev/docs/cli/orchestration))

---

## 2. Worktree 기반 병렬 AI 작업

Orca는 AI 작업 단위마다 실제 Git worktree를 생성하는 방식을 기본 모델로 사용합니다. 각 worktree는 독립적인 branch, 파일, Agent terminal을 가집니다. ([Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model))

따라서 동일한 문제를 여러 Agent가 동시에 서로 다른 방식으로 해결하도록 실행하고 결과를 비교할 수 있습니다.

```text
                 동일한 요구사항
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   Worktree A      Worktree B      Worktree C
     Agent A         Agent B         Agent C
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                 결과 비교 / Review
```

Orca 공식 문서에서도 동일한 작업을 여러 Agent에게 병렬로 실행한 뒤 결과를 비교하고 가장 나은 결과를 선택하는 방식을 주요 사용 사례로 소개하고 있습니다. ([Race three agents on the same task](https://www.onorca.dev/docs/recipes/parallel-agents))

이 방식은 특히 다음 작업에 효과적입니다.

- 설계안 비교
- 동일 버그에 대한 여러 해결 방법 비교
- Refactoring 접근 방법 비교
- 서로 다른 Agent가 작성한 구현 결과 비교

중요한 점은 이것이 단순히 여러 terminal을 띄우는 것과 다르게 **각 Agent의 변경 사항이 독립된 Git worktree에 존재하므로 결과를 비교하고 선택하기 쉽다는 점**입니다. ([Worktrees](https://www.onorca.dev/docs/model/worktrees))

---

## 3. AI 작업 대기 및 Context Switching 비용 감소

AI Coding Agent는 작업 중 수십 초~수분 동안 실행되는 경우가 많기 때문에, 단일 Agent Session만 사용할 경우 개발자가 응답을 기다리는 시간이 발생합니다.

Orca는 여러 Agent terminal, diff, editor, browser를 split pane으로 동시에 배치할 수 있습니다. 공식 문서에서는 Agent terminal, diff, browser, editor를 같은 pane tree 안에 동시에 배치할 수 있다고 설명합니다. ([Tabs, panes & split layouts → Split panes](https://www.onorca.dev/docs/model/tabs-panes-splits#split-panes))

또한 각 worktree마다 별도의 browser가 제공되며, 해당 worktree에서 개발 중인 애플리케이션을 별도의 browser context로 확인할 수 있습니다. ([Per-worktree browser → Worktree scoping](https://www.onorca.dev/docs/browser/overview#worktree-scoping))

따라서 개발자는 예를 들어 다음 작업을 동시에 진행할 수 있습니다.

```text
Agent A : 설계 문서 작성
Agent B : 기존 코드 분석
Agent C : 테스트 코드 작성

Developer : Agent 결과 Review / 코드 확인 / Browser 테스트
```

이 부분은 일반적인 코드 편집 IDE 기능 자체보다는 **AI Agent 여러 개를 동시에 운영할 때 발생하는 대기시간과 Session 전환 비용을 줄이는 효과**가 핵심입니다.

---

# Q2. Orca에서 LLM 연결을 어떻게 허용·통제할 수 있으며, 보안상 어떤 점을 확인해야 하는가?

## 1. Orca의 LLM 연결 구조

Orca 공식 GLM-5.2 문서에서 가장 중요한 설명은 다음 구조입니다.

> GLM-5.2는 Orca 자체에 직접 설정하는 것이 아니라, 사용자가 이미 사용하는 Agent Harness(예: Claude Code)에 설정하고 Orca가 해당 Agent를 실행하는 방식입니다.

공식 문서에서는 “GLM-5.2 works in Orca through the agent harness you already use”라고 명시하며, Orca가 worktree, terminal, browser, review flow, session management를 제공하고 **model access는 Agent configuration이 제공한다**고 설명합니다. ([How to use GLM-5.2 in Orca ADE](https://www.onorca.dev/docs/agents/glm-agent))

또한 Claude Code의 경우 `~/.claude/settings.json`에서 모델 설정을 읽는다고 명시되어 있습니다. ([GLM-5.2 in Orca → Claude Code](https://www.onorca.dev/docs/agents/glm-agent#claude-code))

따라서 현재 회사 환경에서는 다음 구조를 유지할 수 있습니다.

```text
Developer
    │
    ▼
   Orca
    │
    ▼
Claude Code
    │
    ▼
Portkey
    │
    ▼
AWS vLLM
    │
    ├─ GLM-5.2
    └─ Gemma-4
```

즉 **LLM endpoint 및 model access 정책의 실질적인 통제 지점은 기존 Claude Code 설정과 Portkey/vLLM 계층을 그대로 유지할 수 있습니다.**

Orca 공식 Supported Agents 문서 역시 “agent combobox just launches a process in a terminal”이라고 설명합니다. ([Supported agents](https://www.onorca.dev/docs/agents/supported))

따라서 본 도입은 별도의 외부 LLM Provider를 추가하는 것보다는 **기존 승인된 Claude Code 실행 환경을 여러 Agent 작업으로 orchestration하는 계층을 추가하는 것**으로 보는 것이 적절합니다.

### 단, 중요한 보안 해석

Orca 자체에 “회사 승인 모델만 사용하도록 강제하는 중앙 Model Allowlist 기능”이 있다는 공식 문서는 확인되지 않습니다.

따라서 기업 환경에서 강제력이 필요한 경우에는 Orca UI 설정에 의존하기보다 기존 보안 경계인 다음 계층에서 통제하는 것이 적절합니다.

- Claude Code 설정
- 사내 Portkey 정책
- vLLM에서 제공하는 승인 모델
- 단말기 outbound network 정책

즉 Orca가 임의의 외부 모델을 사용하지 못하도록 하는 실질적인 보안 통제는 **Network/Gateway/Agent Harness 계층에서 유지하는 것이 가장 명확합니다.**

---

## 2. Agent 실행 권한

LLM 연결 외에 보안팀이 반드시 확인해야 하는 가장 중요한 항목은 **Agent의 로컬 명령 실행 권한**입니다.

Orca는 기본적으로 지원 Agent를 실행할 때 permission bypass 성격의 옵션을 미리 설정합니다.

예를 들어 공식 문서에는 다음이 명시되어 있습니다.

- Claude Code: `--dangerously-skip-permissions`
- Codex: `--dangerously-bypass-approvals-and-sandbox`
- Gemini 및 다수 Agent: `--yolo`

([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

이는 AI Agent가 파일 수정이나 shell command 실행 시 매번 승인을 요구하지 않도록 하기 위한 기능이지만, 기업 단말기에서는 위험도가 높을 수 있습니다.

다행히 Orca는 전역 Agent Permission을 `Yolo`와 `Manual` 사이에서 변경할 수 있습니다. 공식 문서에서는 다음 메뉴를 안내합니다.

```text
Settings
  → Agents
    → Agent Permissions
      → Manual
```

([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

**회사 도입 시에는 Manual mode를 기본값으로 사용하는 것을 권고합니다.**

---

## 3. Source Code / Prompt / Agent Output의 Telemetry 전송 여부

Orca 공식 Privacy & Telemetry 문서에서는 다음 정보를 telemetry로 전송하지 않는다고 명시합니다.

- Source file contents
- Agent prompts
- Agent responses
- Terminal contents
- Repository names
- Branch names
- Repository URL
- Filesystem path
- Commit messages

([Privacy & Telemetry → What we never send](https://www.onorca.dev/docs/telemetry#what-we-never-send))

Telemetry 자체는 기본 제품 사용 통계를 위해 존재하며, PostHog Cloud US region으로 전송된다고 설명되어 있습니다. ([Privacy & Telemetry → Where the data goes](https://www.onorca.dev/docs/telemetry#where-the-data-goes))

기업 환경에서는 불필요한 외부 통신을 제거하기 위해 telemetry를 비활성화하는 것이 적절합니다.

공식적으로 다음 세 가지 방법을 지원합니다.

- Settings → Privacy → `Share anonymous usage data` Off
- `DO_NOT_TRACK=1`
- `ORCA_TELEMETRY_DISABLED=1`

([Privacy & Telemetry → How to opt out](https://www.onorca.dev/docs/telemetry#how-to-opt-out))

회사 환경에서는 아래 설정을 기본 적용하는 것을 권고합니다.

```bash
export ORCA_TELEMETRY_DISABLED=1
```

---

## 4. Worktree는 보안 Sandbox가 아님

Orca는 Agent별 worktree를 분리하기 때문에 서로의 코드 변경을 직접 덮어쓰는 문제를 줄일 수 있습니다. 공식 문서에서는 각 worktree가 자체 branch, 파일, terminal을 가진다고 설명합니다. ([Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model))

다만 Git worktree는 **filesystem/OS security sandbox가 아닙니다.**

즉 Agent process가 해당 사용자 권한으로 실행된다면 이론적으로 다음 자원에 접근할 수 있습니다.

- 사용자 Home directory
- SSH/Git credential
- 환경 변수
- 다른 local repository
- Network

따라서 worktree isolation은 다음과 같이 이해하는 것이 정확합니다.

```text
Git worktree
  = AI Agent 간 작업공간 격리
  ≠ OS 수준 Security Sandbox
```

따라서 Agent Permission을 Manual로 설정하고, 기존 회사 단말 보안 정책과 network policy를 그대로 적용하는 것이 중요합니다.

---

## 5. Orca 자체의 기타 외부 연결 지점

“LLM 연결은 기존 Claude Code 설정을 따른다”는 사실과 별개로, Orca 전체의 outbound traffic은 별도로 검토할 필요가 있습니다.

공식 문서에서 확인되는 대표적인 외부 연결 가능 지점은 다음과 같습니다.

- Telemetry: PostHog Cloud ([Telemetry → Where the data goes](https://www.onorca.dev/docs/telemetry#where-the-data-goes))
- GitHub OAuth / Linear / Jira / MCP 등의 Integration ([Settings → Integrations](https://www.onorca.dev/docs/settings#integrations))
- Orca Skill 설치/업데이트 시 `npx skills` 사용 ([Orca skills registry](https://www.onorca.dev/docs/cli/skills))
- 선택적으로 Remote Orca Server 사용 가능 ([Remote Orca Servers](https://www.onorca.dev/docs/remote-servers))

따라서 회사 도입 시에는 필요하지 않은 Integration/Remote 기능을 사용하지 않고, 단말 outbound 정책을 통해 허용된 endpoint만 접근 가능하도록 제한하는 것이 적절합니다.

Remote Server를 사용하는 경우 공식 문서에서도 Orca port를 Public Internet에 직접 노출하지 말고 Tailscale, WireGuard, trusted LAN, SSH forwarding 또는 authenticated tunnel을 사용하도록 권고합니다. ([Remote Orca Servers → Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security))

이번 도입 범위가 **개발 노트북 로컬 실행**이라면 Remote Server 기능은 사용하지 않는 것으로 제한할 수 있습니다.

---

# 권고하는 회사용 Orca 사용 Profile

보안 검토 승인 시 다음과 같은 제한 조건을 적용하는 방안을 권고합니다.

| 항목 | 권고 정책 |
|---|---|
| AI Agent | 회사 승인 Claude Code만 사용 |
| Model Endpoint | 기존 Portkey → AWS vLLM 경로 유지 |
| Model | GLM-5.2, Gemma-4 등 회사 승인 모델만 Gateway에서 허용 |
| Agent Permission | `Manual` 사용 |
| Telemetry | `ORCA_TELEMETRY_DISABLED=1` |
| External Integration | 업무상 필요한 기능만 허용 |
| MCP | 별도 승인 없이 외부 MCP Server 추가 금지 |
| Remote Orca Server | 이번 도입 범위에서는 사용하지 않음 |
| Worktree | Agent 작업 분리 목적으로 사용하되 Security Sandbox로 간주하지 않음 |
| Network Enforcement | 기존 단말/Portkey outbound 정책 유지 |

---

# Security Team 답변 요약안

## Q1. 기대 효과

Orca 도입 목적은 새로운 AI 모델을 추가하는 것이 아니라, 현재 회사에서 승인된 AI 환경을 **Multi-Agent 방식으로 효율적으로 활용하는 것**입니다.

주요 효과는 다음 세 가지입니다.

1. **Orchestration**: Creator/Reviewer 등의 Agent 역할을 분리하고 Plan → Design → Implementation과 같은 단계별 작업 및 PASS/FAIL 흐름을 자동화할 수 있습니다. Orca는 공식적으로 Run, Task, Worker, Decision Gate 등을 제공하여 multi-agent workflow를 관리합니다. ([공식 문서](https://www.onorca.dev/docs/cli/orchestration))
2. **Worktree 기반 병렬 실행**: 동일한 작업을 여러 Agent가 독립된 Git worktree에서 수행하도록 하고 결과를 비교할 수 있습니다. ([공식 문서](https://www.onorca.dev/docs/recipes/parallel-agents))
3. **AI 작업 대기시간 감소**: 여러 Agent terminal, diff, editor, browser를 동시에 관리할 수 있어 Agent 응답을 기다리는 동안 다른 AI 작업이나 Review를 진행할 수 있습니다. ([공식 문서](https://www.onorca.dev/docs/model/tabs-panes-splits#split-panes))

따라서 기대 효과는 일반 IDE 편집 기능보다 **AI Agent의 병렬화, 역할 분리, Review 자동화, 작업 상태 관리에 따른 개발 생산성 향상**입니다.

## Q2. LLM 연결 및 Security Checkpoint

Orca 공식 문서에 따르면 GLM-5.2 등의 모델은 Orca가 직접 연결하는 것이 아니라 **Claude Code와 같은 기존 Agent Harness에 설정된 provider/model configuration을 사용**합니다. ([GLM-5.2 공식 문서](https://www.onorca.dev/docs/agents/glm-agent))

따라서 현재 회사 환경에서는 다음 연결 구조를 그대로 유지할 수 있습니다.

```text
Orca → Claude Code → Portkey → AWS vLLM → 승인 모델(GLM-5.2 / Gemma-4)
```

Orca 자체에 회사 승인 모델만 사용하도록 강제하는 중앙 Model Allowlist 기능은 공식 문서에서 확인되지 않으므로, 실제 접근 통제는 기존과 동일하게 **Claude Code 설정 + Portkey + vLLM + 단말 Network Policy**에서 수행하는 것이 적절합니다.

추가 Security Checkpoint로는 다음을 적용할 수 있습니다.

- Agent Permission을 `Manual`로 설정 ([공식 문서](https://www.onorca.dev/docs/agents/supported#permissions-default))
- Orca telemetry 비활성화 ([공식 문서](https://www.onorca.dev/docs/telemetry#how-to-opt-out))
- 불필요한 MCP/외부 Integration 사용 제한 ([공식 문서](https://www.onorca.dev/docs/settings#integrations))
- Remote Server 미사용 또는 사내 보호망으로 제한 ([공식 문서](https://www.onorca.dev/docs/remote-servers#access-and-security))

이와 같은 조건을 적용하면 본 요청은 **신규 외부 AI Provider 도입이 아니라, 기존 회사 승인 AI 인프라 위에 Multi-Agent orchestration 환경을 추가하는 형태**로 한정하여 검토할 수 있습니다.

---

## 주요 공식 문서

- [What is Orca?](https://www.onorca.dev/docs)
- [Orchestration](https://www.onorca.dev/docs/cli/orchestration)
- [Worktrees](https://www.onorca.dev/docs/model/worktrees)
- [Race three agents on the same task](https://www.onorca.dev/docs/recipes/parallel-agents)
- [Supported agents / Agent Permissions](https://www.onorca.dev/docs/agents/supported#permissions-default)
- [How to use GLM-5.2 in Orca ADE](https://www.onorca.dev/docs/agents/glm-agent)
- [Privacy & Telemetry](https://www.onorca.dev/docs/telemetry)
- [Settings / Integrations](https://www.onorca.dev/docs/settings#integrations)
- [Remote Orca Servers / Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security)
