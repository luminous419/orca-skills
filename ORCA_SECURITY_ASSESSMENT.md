# Orca 보안 검토 자료

> 통제된 소프트웨어 개발 환경에 Orca를 도입하기 위한 보안 검토용 문서
>
> 최종 검토일: 2026-08-20

## 1. 문서 목적

이 문서는 Orca 공식 문서와 공개 소스 저장소를 기준으로 보안 관련 특성을 정리하고, Security Engineering 팀이 검토할 수 있도록 보수적인 도입 방안을 제안한다.

본 문서의 목적은 **Orca가 보안 샌드박스이거나 위험이 없는 제품이라고 주장하는 것이 아니다.** Orca는 기존 CLI 기반 AI 코딩 에이전트를 실행하고 조정하는 오케스트레이션/IDE 계층으로 보는 것이 적절하다. 따라서 실제 보안 수준은 Orca 자체의 동작뿐 아니라 Orca가 실행하는 하위 에이전트의 권한, 네트워크 경로, 파일시스템 접근 권한, Git 자격증명 정책 등에 의해 함께 결정된다.

---

## 2. 핵심 요약

보안 관점에서 중요한 결론은 다음과 같다.

1. **Orca는 기본적으로 로컬 애플리케이션으로 동작한다.** 공식 Privacy Policy는 Orca IDE가 사용자의 장비에서 주로 동작하는 다운로드형 데스크톱 애플리케이션이라고 설명한다.
2. **Orca는 소스코드, proprietary file, 외부 AI 에이전트용 API Key를 수집하지 않는다고 명시한다.** Telemetry 문서에서도 파일 내용, prompt, agent output, terminal output, repository 이름, branch 이름, URL, filesystem path, commit message 등을 telemetry로 전송하지 않는다고 설명한다.
3. **Telemetry는 비활성화할 수 있다.** 공식 문서에 따르면 `DO_NOT_TRACK=1` 또는 `ORCA_TELEMETRY_DISABLED=1`을 설정하면 telemetry를 끌 수 있다.
4. **병렬 에이전트는 Git worktree를 이용해 작업 디렉토리를 분리한다.** 이 구조는 에이전트 간 파일 충돌이나 실수에 의한 덮어쓰기를 줄이는 데 유리하지만, **OS 수준 보안 샌드박스는 아니다.**
5. **기본 Agent Permission 설정은 보안상 중요한 검토 대상이다.** Orca는 일부 지원 에이전트에 대해 permission bypass 또는 full-autonomy 성격의 옵션을 기본 적용할 수 있다. 예를 들어 Claude Code의 `--dangerously-skip-permissions`, Codex의 `--dangerously-bypass-approvals-and-sandbox`, 기타 agent의 `--yolo` 계열 설정이 공식 문서에 명시되어 있다.
6. **더 보수적인 기업용 설정이 가능하다.** Orca의 `Settings → Agents → Agent Permissions`에서 기본 동작을 Yolo가 아닌 Manual로 전환할 수 있다. 별도의 강력한 sandbox가 보장되지 않는다면 기업 환경에서는 Manual 모드를 기본값으로 권고한다.
7. **Orca 자체가 AI 모델 제공자는 아니다.** 공식 문서는 Orca가 CLI agent를 process 형태로 실행하는 구조임을 설명한다. 따라서 조직이 이미 승인한 Claude Code 또는 사내 LLM Gateway 경로를 그대로 유지하면서 Orca를 상위 orchestration layer로 사용할 수 있다.
8. **공개된 보안 거버넌스 자료는 제한적이다.** 본 검토 시점 기준으로 공개 GitHub Security 페이지에는 `SECURITY.md` 보안 정책과 공개된 Security Advisory가 확인되지 않았다. 따라서 공개 자료만으로 SOC 2, ISO 27001, penetration test 보고서, 정식 Security Whitepaper와 동등한 보증 수준을 기대해서는 안 된다.

---

## 3. Orca의 보안 모델

Security Engineering 팀에는 Orca를 다음과 같이 설명하는 것이 가장 정확하다.

> Orca는 CLI 기반 AI 코딩 에이전트를 실행하고 조정하는 오케스트레이션 및 개발환경 계층이다. Orca 자체가 AI 모델이나 외부 소스코드 실행 SaaS인 것은 아니며, 하위 에이전트의 보안 통제, 운영체제 권한, Git 권한, 네트워크 정책을 대체하지 않는다.

실질적인 보안 경계는 다음 구성요소를 포함한다.

- Orca Desktop / Runtime process
- Orca가 관리하는 terminal과 worktree
- Claude Code, Codex, Gemini 또는 custom CLI agent
- 로컬 filesystem과 개발자 credential
- Git remote 및 repository 권한
- Agent가 사용하는 사내 또는 외부 LLM/API endpoint
- 선택적으로 사용하는 Orca remote server 및 SSH/Tunnel 구성
- Telemetry 및 기타 외부 integration

따라서 Orca 단일 애플리케이션만 평가하기보다 **Orca + Agent + Network + Credential + Repository** 전체 경로를 하나의 실행 체계로 평가해야 한다.

---

## 4. Privacy 및 Telemetry

### 4.1 Orca가 수집하지 않는다고 명시한 데이터

Orca Privacy Policy에는 다음 정보를 수집하지 않는다고 명시되어 있다.

- Source code
- Third-party AI agent API key
- Proprietary file

Privacy & Telemetry 문서에서는 telemetry에 다음 데이터를 포함하지 않는다고 설명한다.

- File contents
- Prompts
- Agent output
- Terminal output
- Repository names
- Branch names
- URLs
- Filesystem paths
- Commit messages
- Hostname
- Username
- IP address를 telemetry payload 데이터로 직접 전송하는 방식

Telemetry event는 로컬에서 생성된 임의 ID와 기본적인 build/platform 정보 및 제품 사용 이벤트 중심으로 구성된다고 설명되어 있다.

### 4.2 Telemetry 비활성화

공식 문서에 따르면 아래 환경 변수 중 하나를 설정해 telemetry를 끌 수 있다.

```bash
DO_NOT_TRACK=1
```

또는

```bash
ORCA_TELEMETRY_DISABLED=1
```

### 4.3 기업 환경 권고

사내 보안 검토를 통과하기 전에는 telemetry를 기본적으로 비활성화하는 것을 권고한다.

예:

```bash
export ORCA_TELEMETRY_DISABLED=1
```

이 정책은 Orca의 제품 사용 통계 전송을 차단하면서, 실제 개발 코드나 agent 통신 경로를 변경하지 않는다.

다만 보안팀에서는 실행 시점의 실제 outbound connection을 별도로 확인하는 것이 바람직하다. 공식 문서의 설명과 실제 binary/runtime의 네트워크 동작이 일치하는지 방화벽 로그, proxy 로그, packet capture 등의 방법으로 검증할 수 있다.

---

## 5. Agent 실행 구조와 데이터 흐름

Orca 공식 문서는 Agent 선택이 실제로 해당 CLI agent process를 terminal에서 실행하는 방식임을 설명한다.

즉 일반적으로 구조는 다음과 같이 이해할 수 있다.

```text
Developer
   │
   ▼
Orca
   │
   ├─ launch CLI agent process
   │
   ▼
Claude Code / Custom CLI Agent
   │
   ▼
Configured LLM/API Endpoint
```

중요한 점은 **Orca 자체가 반드시 별도의 외부 AI provider로 소스코드를 전송하는 구조는 아니라는 것**이다.

예를 들어 조직이 이미 승인한 Claude Code와 사내 LLM Gateway를 운영하고 있다면 다음 구조가 가능하다.

```text
Developer Laptop
      │
      ▼
     Orca
      │
      ├── claude-glm
      │       │
      │       └── Internal LLM Gateway → GLM
      │
      └── claude-gemma
              │
              └── Internal LLM Gateway → Gemma
```

이 경우 Orca의 역할은 새로운 AI provider를 추가하는 것이 아니라, 이미 승인된 CLI agent 실행 경로를 상위에서 orchestration하는 것이다.

단, 이 설명은 실제 배포 환경에서 custom wrapper/script가 어떤 network endpoint와 credential을 사용하는지 별도로 검증한다는 전제를 가진다.

---

## 6. Agent Permission 위험

### 6.1 기본 permission bypass 동작

보안 검토 시 가장 중요하게 확인해야 할 부분이다.

Orca 공식 문서에는 지원 agent에 대해 다음과 같은 permission bypass / full-autonomy 계열 옵션을 사용할 수 있음이 명시되어 있다.

```text
Claude Code
  --dangerously-skip-permissions

Codex
  --dangerously-bypass-approvals-and-sandbox

Gemini 및 일부 기타 agent
  --yolo 계열 옵션
```

이 설정은 에이전트가 shell 명령 실행, 파일 변경 등에서 매번 사용자의 승인을 요구하지 않도록 할 수 있다.

개발 생산성 측면에서는 편리하지만, 기업 보안 환경에서는 다음 위험이 있다.

- Agent가 예상하지 않은 shell command 실행
- 파일 삭제 또는 대규모 변경
- Git credential 사용
- 외부 network command 실행
- 로컬 secret 또는 configuration file 접근
- 잘못된 prompt나 prompt injection에 의한 command 실행

따라서 Orca의 기본 편의 설정을 그대로 기업 보안 정책으로 간주해서는 안 된다.

### 6.2 권고 설정

공식 문서에서 제공하는 다음 설정을 권고한다.

```text
Settings
  → Agents
    → Agent Permissions
      → Manual
```

별도로 보안이 검증된 sandbox를 구축하지 않는 한 **Manual permission을 기본 정책으로 사용하는 것을 권고한다.**

또한 custom agent wrapper를 사용할 경우 wrapper 내부에서도 dangerous permission bypass option을 강제로 추가하지 않는지 확인해야 한다.

---

## 7. Git Worktree 기반 격리

Orca는 여러 AI agent가 동시에 작업할 때 Git worktree를 활용하여 독립적인 작업 디렉토리를 제공한다.

예:

```text
repository
├── main working tree
├── agent-A worktree
├── agent-B worktree
└── agent-C worktree
```

### 장점

- Agent 간 파일 변경 충돌 감소
- 한 agent가 다른 agent의 미완성 코드를 덮어쓸 가능성 감소
- Agent별 branch/commit 추적 용이
- 병렬 작업 결과를 review/merge하기 쉬움

### 보안상 한계

Git worktree는 **보안 sandbox가 아니다.**

즉 worktree가 분리되어 있어도 agent process가 운영체제상 충분한 권한을 가진다면 다음 영역에 접근할 수 있다.

- 사용자 Home directory
- SSH key
- Git credential
- 환경 변수
- 다른 repository
- OS command
- Network

따라서 다음과 같이 구분해야 한다.

```text
Git worktree
  = 작업공간 격리
  ≠ OS security sandbox
```

Security Engineering 평가에서 이 차이를 명확히 해야 한다.

---

## 8. Remote Access 보안

Orca는 remote server 사용 시 access token과 원격 연결 구조를 제공한다.

공식 문서에서는 remote 환경에서 다음과 같은 방향을 권고한다.

- Client별 token 사용
- Token revoke 가능
- Orca port를 Public Internet에 직접 노출하지 않음
- Tailscale, WireGuard, SSH Tunnel 등 보호된 network path 사용

기업 환경에서는 추가로 다음을 권고한다.

- Remote 기능이 필요하지 않다면 비활성화
- 필요할 경우 사내 VPN 또는 SSH tunnel 내부로 제한
- Public inbound firewall 차단
- Token rotation 및 revoke 절차 수립
- Remote access log 확인

---

## 9. Secret 및 Credential 보호

Orca 공식 CLI 문서에는 민감한 값을 command-line argument가 아닌 stdin 등을 이용해 전달하는 방식을 권장하는 내용이 있다.

이는 다음 위치에 secret이 남는 위험을 줄인다.

- Shell history
- Process list
- Command log
- Terminal transcript

기업 환경에서는 추가로 다음 정책을 권고한다.

- API Key를 project repository에 저장하지 않음
- `.env` 파일을 Git에서 제외
- 가능한 경우 OS Keychain 또는 회사 secret manager 사용
- Agent에게 장기 credential보다 최소 권한의 단기 credential 제공
- Production credential은 개발용 AI agent에서 사용하지 않음

---

## 10. Open Source 특성

Orca는 공개 GitHub repository를 통해 소스코드를 확인할 수 있다.

이는 기업 보안 검토 측면에서 장점이 있다.

Security Engineering 팀이 필요하다면 다음을 직접 수행할 수 있다.

- Source review
- SAST
- Dependency scan / SCA
- Secret scan
- License scan
- Network endpoint review
- Dangerous command execution path review
- Dependency supply-chain 검토

특히 폐쇄형 SaaS와 달리 실행 코드의 상당 부분을 직접 검토할 수 있다는 점은 보안 검증 가능성 측면에서 장점이다.

그러나 Open Source라는 사실 자체가 안전성을 보장하는 것은 아니다. 실제 도입 전에는 조직 자체 검증이 필요하다.

---

## 11. 공개 보안 거버넌스 자료의 한계

본 문서 검토 시점의 공개 자료 기준으로 다음 항목은 충분히 확인되지 않는다.

- SOC 2 인증
- ISO 27001 인증
- 정식 Security Whitepaper
- 외부 Penetration Test 보고서
- Secure SDLC 상세 문서
- 공개 `SECURITY.md` 기반 Vulnerability Disclosure Policy
- 공개 Security Advisory 이력

따라서 Security Engineering 팀이 위 자료를 필수 조건으로 요구하는 경우에는 Orca 프로젝트 측에 별도 확인이 필요하다.

공개 문서만으로 이러한 인증 또는 보증이 존재한다고 가정해서는 안 된다.

---

## 12. 권고 Enterprise Security Profile

사내 개발환경에서 Orca를 도입할 경우 다음 profile을 권고한다.

### 12.1 Telemetry

```bash
export ORCA_TELEMETRY_DISABLED=1
```

또는 조직 표준에 따라:

```bash
export DO_NOT_TRACK=1
```

### 12.2 Agent Permission

```text
Agent Permissions = Manual
```

`--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`, `--yolo` 등 자동 승인 설정은 원칙적으로 사용하지 않는다.

### 12.3 Agent 제한

회사에서 승인한 CLI Agent만 허용한다.

예:

```text
Allowed
  claude-glm
  claude-gemma

Not Allowed
  arbitrary external agent
  unapproved model CLI
```

### 12.4 Model Network Path

AI 요청은 회사에서 승인한 사내 Gateway를 통해서만 전달하도록 한다.

```text
Orca
  ↓
Approved CLI Agent
  ↓
Internal LLM Gateway
  ↓
Approved Model
```

### 12.5 Remote Access

필요하지 않다면 Remote 기능을 사용하지 않는다.

필요한 경우:

```text
VPN / Tailscale / WireGuard / SSH Tunnel
```

등 보호된 경로만 사용하고 Orca port를 Internet에 직접 공개하지 않는다.

### 12.6 Repository / Git 권한

- Production repository에 대한 write 권한 최소화
- Protected branch 적용
- Pull Request 기반 merge
- Agent의 직접 main/master push 금지
- Git credential 최소 권한화

---

## 13. Risk Matrix

| 위험 | 설명 | 초기 위험도 | 권고 통제 | 잔여 위험 |
|---|---|---:|---|---:|
| Agent의 임의 명령 실행 | Permission bypass 상태에서 shell command 자동 실행 가능 | 높음 | Agent Permission = Manual | 중간 |
| Source code 외부 전송 | Agent 또는 잘못된 endpoint가 외부로 코드 전송 가능 | 높음 | 승인된 internal gateway만 허용 | 낮음~중간 |
| Orca telemetry | 제품 사용 데이터가 외부 telemetry backend로 전송 가능 | 중간 | `ORCA_TELEMETRY_DISABLED=1` | 낮음 |
| Agent 간 파일 충돌 | 병렬 agent가 동일 코드 수정 | 중간 | Git worktree | 낮음 |
| Worktree를 sandbox로 오인 | Agent가 worktree 밖 filesystem에 접근 가능 | 높음 | OS/Agent permission 별도 통제 | 중간 |
| Credential 노출 | Agent가 local credential 또는 env var 접근 | 높음 | 최소 권한 credential, secret manager, Manual permission | 중간 |
| Remote server 노출 | Orca service가 인터넷에 노출될 가능성 | 높음 | VPN/SSH tunnel, public port 차단 | 낮음 |
| Supply-chain 위험 | Orca 및 dependency 업데이트에 악성 코드 유입 가능 | 중간 | 버전 고정, SCA/SAST, hash 검증 | 낮음~중간 |
| 취약점 대응 체계 불확실 | 공개 SECURITY.md / 공식 인증 자료 부족 | 중간 | 내부 source review 및 업데이트 검증 | 중간 |

---

## 14. Security Engineering 검증 체크리스트

도입 전 다음 항목을 실제 환경에서 확인하는 것을 권고한다.

### Network

- [ ] Orca 실행 시 outbound connection 목록 확인
- [ ] Telemetry 비활성화 후 외부 telemetry connection이 사라지는지 검증
- [ ] 승인되지 않은 AI endpoint 호출 여부 확인
- [ ] Remote server port가 외부에 노출되지 않는지 확인

### Filesystem

- [ ] Orca가 생성/수정하는 directory 확인
- [ ] Agent가 worktree 밖의 파일에 접근 가능한 범위 확인
- [ ] SSH key / cloud credential / production secret 접근 여부 확인

### Agent

- [ ] Agent Permission = Manual 적용 여부 확인
- [ ] 실제 실행 command에 dangerous bypass flag가 포함되는지 확인
- [ ] 승인된 agent binary/script만 실행되는지 확인
- [ ] Arbitrary executable 등록이 가능한지 확인

### Source / Supply Chain

- [ ] Orca source review
- [ ] Dependency SCA
- [ ] SAST
- [ ] License scan
- [ ] Secret scan
- [ ] Binary/package hash 검증
- [ ] Offline installation package 무결성 검증

### Git

- [ ] Main/Master protected branch 적용
- [ ] Agent 직접 push 제한
- [ ] PR review 필수화
- [ ] Git credential 최소 권한 적용

---

## 15. 보안팀 설명 시 권장 메시지

다음과 같은 표현이 가장 정확하다.

> Orca를 새로운 AI Provider로 도입하는 것이 아니라, 이미 회사에서 승인한 CLI 기반 AI Agent와 사내 LLM Gateway 위에 로컬 orchestration layer를 추가하는 것이다.
>
> Orca 자체는 source code, prompt, terminal output 등을 telemetry로 수집하지 않는다고 공식 문서에 명시하고 있으며 telemetry 전체 비활성화 기능도 제공한다.
>
> 다만 Orca의 기본 agent permission은 개발 편의성을 위해 aggressive하게 설정될 수 있으므로, 회사 환경에서는 Manual permission을 강제하고 승인된 agent와 internal gateway만 사용하도록 제한한다.
>
> 또한 Git worktree는 작업공간 격리 기능이지 보안 sandbox가 아니므로 OS permission, credential, network access는 별도의 회사 보안 정책으로 통제한다.

이 접근은 Orca를 무조건 안전한 제품으로 주장하기보다 **Orca가 가진 위험 요소를 명확히 공개하고, 조직의 기존 보안 통제 안에 제한적으로 배치하는 방식**이다.

---

## 16. 공식 참고 자료

본 문서는 다음 공개 자료를 근거로 작성되었다.

- Orca Privacy & Telemetry  
  https://www.onorca.dev/docs/telemetry

- Orca Privacy Policy  
  https://www.onorca.dev/privacy

- Orca Worktrees  
  https://www.onorca.dev/docs/model/worktrees

- Orca Supported Agents / Agent Permissions  
  https://www.onorca.dev/docs/agents/supported

- Orca Remote Servers  
  https://www.onorca.dev/docs/remote-servers

- Orca Computer Use / CLI  
  https://www.onorca.dev/docs/cli/computer-use

- Orca GitHub Repository  
  https://github.com/stablyai/orca

- Orca GitHub Security  
  https://github.com/stablyai/orca/security

---

## 17. 결론

Orca는 CLI 기반 AI coding agent의 orchestration과 병렬 개발을 지원하는 도구이며, 공식 문서상 source code나 prompt 자체를 telemetry로 수집하지 않고 telemetry 비활성화 기능도 제공한다.

그러나 Orca를 보안 sandbox로 간주해서는 안 된다. 특히 기본 agent permission의 bypass 동작과 underlying CLI agent가 가진 filesystem/network 권한은 기업 도입 시 반드시 통제해야 한다.

따라서 기업 환경에서는 최소한 다음 정책을 적용하는 것이 적절하다.

```text
Telemetry Disabled
        +
Manual Agent Permission
        +
Approved CLI Agents Only
        +
Internal LLM Gateway Only
        +
Protected Git Workflow
        +
Restricted Remote Access
```

위 통제를 적용하고 실제 runtime/network/source 검증을 수행한다면, Orca는 기존 승인 AI 개발환경을 대체하는 새로운 외부 AI 서비스라기보다 **기존 승인된 AI 개발도구를 안전하게 orchestration하기 위한 로컬 상위 계층**으로 평가할 수 있다.
