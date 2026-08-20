# Orca 보안 검토 자료

> 통제된 소프트웨어 개발 환경에 Orca를 도입하기 위한 Security Engineering 검토용 문서
>
> 최종 검토일: 2026-08-20

## 1. 문서 목적

이 문서는 Orca 공식 문서와 공식 공개 GitHub 저장소를 기준으로 보안 관련 특성을 정리하고, 기업 개발 환경에서 적용할 수 있는 보수적인 통제 방안을 제안한다. ([Orca 공식 문서](https://www.onorca.dev/docs), [Orca 공식 GitHub](https://github.com/stablyai/orca))

본 문서는 **Orca가 보안 sandbox이거나 위험이 없는 제품이라고 주장하지 않는다.** Orca 공식 문서는 Orca를 여러 AI coding agent를 실행하는 desktop IDE로 설명하며, Orca 자체는 model이 아니고 사용자가 이미 사용하는 Claude Code, Codex, OpenCode 등의 agent를 실행한다고 설명한다. ([공식 문서 → What is Orca? → What Orca is not](https://www.onorca.dev/docs#what-orca-is-not))

따라서 실제 보안 수준은 Orca 자체뿐 아니라 하위 CLI agent의 권한, 운영체제 권한, Git credential, 네트워크 정책, LLM/API endpoint 설정을 함께 평가해야 한다. 이는 Orca의 agent 실행 모델에서 도출되는 보안 검토 원칙이다. ([공식 문서 → Supported agents](https://www.onorca.dev/docs/agents/supported))

---

## 2. 핵심 요약

1. **Orca는 기본적으로 사용자의 로컬 장비에서 실행되는 데스크톱 애플리케이션이다.** Privacy Policy는 Orca를 "downloadable desktop application that primarily operates locally on your machine"이라고 설명한다. ([Privacy Policy → 1. Information We Collect](https://www.onorca.dev/privacy#1-information-we-collect))

2. **Orca는 source code, third-party AI agent API key, proprietary file을 수집하지 않는다고 명시한다.** ([Privacy Policy → 1. Information We Collect](https://www.onorca.dev/privacy#1-information-we-collect))

3. **Telemetry에서도 file contents, prompts, agent output, terminal output, repo names, branch names, URLs, paths, commit messages를 전송하지 않는다고 명시한다.** ([Privacy & Telemetry → Summary](https://www.onorca.dev/docs/telemetry#summary), [Privacy & Telemetry → What we never send](https://www.onorca.dev/docs/telemetry#what-we-never-send))

4. **Telemetry는 사용자 설정 또는 환경 변수로 비활성화할 수 있다.** `Settings → Privacy`에서 끄거나 `DO_NOT_TRACK=1`, `ORCA_TELEMETRY_DISABLED=1`을 사용할 수 있다. ([Privacy & Telemetry → How to opt out](https://www.onorca.dev/docs/telemetry#how-to-opt-out))

5. **Telemetry를 활성화하면 데이터는 PostHog Cloud의 United States region으로 전송된다.** 따라서 기업 환경에서는 이 outbound communication을 별도 검토해야 한다. ([Privacy & Telemetry → Where the data goes](https://www.onorca.dev/docs/telemetry#where-the-data-goes))

6. **Orca는 각 작업을 별도 Git worktree로 분리한다.** 공식 문서는 각 worktree가 자체 branch, 자체 파일, 자체 agent terminal을 가지며, 이 구조가 병렬 agent가 서로의 파일을 덮어쓰지 않도록 한다고 설명한다. ([Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model))

7. **Git worktree는 작업공간 분리이지 OS-level security sandbox는 아니다.** Orca 문서는 worktree를 agent 작업 단위로 사용하지만, 별도의 OS sandbox라고 설명하지 않는다. 따라서 worktree 밖의 filesystem/network 접근 통제는 underlying agent와 OS 정책으로 별도 관리해야 한다. ([Worktrees](https://www.onorca.dev/docs/model/worktrees), [Agents & sessions → Launch defaults](https://www.onorca.dev/docs/model/agents-sessions#launch-defaults))

8. **Orca의 기본 agent permission 정책은 Security Engineering 관점에서 가장 중요한 위험 요소 중 하나다.** Orca는 새 agent launch에 각 CLI의 permission-bypass/full-autonomy flag를 기본 적용한다고 명시한다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

9. **Claude Code는 기본적으로 `--dangerously-skip-permissions`, Codex는 `--dangerously-bypass-approvals-and-sandbox`, 여러 다른 agent는 `--yolo` 또는 이에 상응하는 flag가 사용된다.** ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

10. **이 기본 동작은 `Settings → Agents → Agent Permissions`에서 Yolo와 Manual 사이를 전환할 수 있다.** 기업 환경에서는 별도 sandbox가 검증되지 않았다면 Manual을 기본 정책으로 사용하는 것을 권고한다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

11. **Orca 자체는 AI model/provider가 아니다.** 공식 문서는 agent combobox가 실제 CLI agent process를 terminal에서 실행한다고 설명한다. ([Supported agents](https://www.onorca.dev/docs/agents/supported))

12. **Remote Orca Server는 paired client마다 별도의 revoke 가능한 token을 사용한다.** ([Remote Orca Servers → Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security))

13. **Orca 공식 문서는 Orca port를 Public Internet에 직접 노출하지 말고 Tailscale, WireGuard, trusted LAN, SSH forwarding 또는 authenticated tunnel을 사용하라고 권고한다.** ([Remote Orca Servers → Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security))

14. **현재 공개 GitHub Security 페이지에는 `SECURITY.md`가 없고 공개 Security Advisory도 없는 것으로 표시된다.** 따라서 공개 자료만으로 정식 보안 인증이나 vendor security whitepaper 수준의 보증을 대체할 수는 없다. ([GitHub → Security](https://github.com/stablyai/orca/security))

---

## 3. Orca의 보안 모델

Orca는 여러 AI coding agent를 병렬로 실행하기 위한 desktop IDE이며, 각 task에 Git worktree와 agent terminal을 제공한다. ([What is Orca?](https://www.onorca.dev/docs))

Orca는 자체 AI model이 아니라 기존 agent를 실행하는 계층이며, 공식 문서에는 "Not a model"이라고 명확히 설명되어 있다. ([What is Orca? → What Orca is not](https://www.onorca.dev/docs#what-orca-is-not))

또한 supported agent 문서에서는 agent combobox가 agent process를 terminal에서 launch한다고 설명한다. ([Supported agents](https://www.onorca.dev/docs/agents/supported))

따라서 기업 환경의 실제 보안 경계는 다음을 함께 포함한다고 보는 것이 적절하다.

- Orca Desktop / Runtime process
- Orca-managed terminal 및 worktree
- Claude Code, Codex, Gemini 또는 custom CLI agent
- 로컬 filesystem 및 developer credential
- Git remote와 repository 권한
- agent가 접속하는 LLM/API endpoint
- Remote Orca Server 또는 SSH/Tunnel
- Telemetry 및 기타 외부 integration

위 구성요소들은 Orca의 로컬 실행, agent process launch, worktree 및 remote runtime 구조를 기반으로 한 위협 모델링 범위다. ([What is Orca?](https://www.onorca.dev/docs), [Supported agents](https://www.onorca.dev/docs/agents/supported), [Remote Orca Servers → What runs where](https://www.onorca.dev/docs/remote-servers#what-runs-where))

---

## 4. Privacy 및 Telemetry

### 4.1 로컬 실행

Orca Privacy Policy는 Orca가 사용자의 machine에서 주로 동작하는 다운로드형 desktop application이라고 명시한다. ([Privacy Policy → 1. Information We Collect](https://www.onorca.dev/privacy#1-information-we-collect))

### 4.2 Orca가 수집하지 않는다고 명시한 데이터

Privacy Policy는 다음 정보를 수집하지 않는다고 명시한다. ([Privacy Policy → 1. Information We Collect](https://www.onorca.dev/privacy#1-information-we-collect))

- Source code
- Third-party AI agent API key
- Proprietary file

Privacy & Telemetry 문서는 telemetry에서 다음 데이터를 보내지 않는다고 설명한다. ([Privacy & Telemetry → What we never send](https://www.onorca.dev/docs/telemetry#what-we-never-send))

- File contents
- Prompts
- Agent responses/output
- Terminal contents/output
- Repository names
- Branch names
- URLs
- Filesystem paths
- Commit messages
- Current working directory
- Raw error messages / stack frames
- Orca user account information

Telemetry event는 random local ID와 build/platform 정보 및 제한된 product-usage event를 중심으로 구성된다고 설명한다. ([Privacy & Telemetry → Summary](https://www.onorca.dev/docs/telemetry#summary), [Privacy & Telemetry → What we collect](https://www.onorca.dev/docs/telemetry#what-we-collect))

공식 문서는 telemetry field가 fixed enum, version string 또는 anonymous local ID이며 UI의 free-form string은 전송하지 않는다고 설명한다. ([Privacy & Telemetry → What we collect](https://www.onorca.dev/docs/telemetry#what-we-collect))

### 4.3 Telemetry 비활성화

공식 문서에 따르면 UI에서 `Settings → Privacy → Share anonymous usage data`를 끌 수 있다. ([Privacy & Telemetry → How to opt out](https://www.onorca.dev/docs/telemetry#how-to-opt-out))

또는 다음 환경 변수 중 하나를 사용할 수 있다. ([Privacy & Telemetry → How to opt out](https://www.onorca.dev/docs/telemetry#how-to-opt-out))

```bash
DO_NOT_TRACK=1
```

```bash
ORCA_TELEMETRY_DISABLED=1
```

### 4.4 Telemetry 목적지

Telemetry가 활성화된 경우 공식 문서는 vendor를 **PostHog Cloud, United States region**으로 명시한다. ([Privacy & Telemetry → Where the data goes](https://www.onorca.dev/docs/telemetry#where-the-data-goes))

공식 문서는 PostHog project membership access가 소수 Orca maintainer로 제한된다고 설명한다. ([Privacy & Telemetry → Where the data goes](https://www.onorca.dev/docs/telemetry#where-the-data-goes))

**회사 권고:** 사내 보안 검토를 완료하기 전에는 `ORCA_TELEMETRY_DISABLED=1`을 기본 배포 설정으로 적용하고, firewall/proxy log로 실제 outbound traffic이 정책과 일치하는지 검증한다.

---

## 5. Agent 실행 구조와 데이터 흐름

Orca 공식 문서는 Orca가 CLI agent를 terminal process로 launch하는 구조라고 설명한다. ([Supported agents](https://www.onorca.dev/docs/agents/supported))

또한 Orca 자체는 model이 아니며 사용자가 이미 사용하는 agent/subscription을 가져와 실행하는 구조라고 설명한다. ([What is Orca? → What Orca is not](https://www.onorca.dev/docs#what-orca-is-not))

일반적인 구조는 다음과 같이 볼 수 있다.

```text
Developer
   │
   ▼
Orca
   │
   └── launch CLI agent process
             │
             ▼
      Claude Code / Custom CLI
             │
             ▼
       Configured LLM/API
```

따라서 **Orca를 사용한다고 해서 Orca 자체가 자동으로 새로운 LLM provider data path가 되는 것은 아니다.** 실제 모델 통신 경로는 Orca가 실행하는 CLI agent의 configuration에 의해 결정된다. 이 판단의 근거는 Orca가 model이 아니라 기존 CLI agent process를 launch하는 계층이라는 공식 architecture 설명이다. ([What is Orca? → What Orca is not](https://www.onorca.dev/docs#what-orca-is-not), [Supported agents](https://www.onorca.dev/docs/agents/supported))

회사 환경에서 승인된 custom wrapper와 내부 LLM Gateway만 허용한다면 다음 형태의 배포가 가능하다.

```text
Developer Laptop
      │
      ▼
     Orca
      │
      ├── approved CLI wrapper A ── Internal LLM Gateway
      │
      └── approved CLI wrapper B ── Internal LLM Gateway
```

**회사 권고:** Orca 자체만 승인 대상으로 보지 말고 실제 agent launch argument, environment variable, API base URL, credential source, DNS/egress destination을 함께 검증한다.

---

## 6. Agent Permission 위험

### 6.1 기본값

Orca 공식 문서는 새로운 agent launch에 각 supported CLI의 permission-bypass flag를 pre-fill한다고 명시한다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

Claude Code의 기본값에는 `--dangerously-skip-permissions`가 포함된다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

Codex의 기본값에는 `--dangerously-bypass-approvals-and-sandbox`가 포함된다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

Gemini, Cursor, Crush, Kimi, Rovo Dev, Hermes, GitHub Copilot, Command Code 등 여러 agent에는 `--yolo` 또는 이에 상응하는 permission bypass flag가 적용된다고 명시되어 있다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

Agents & Sessions 문서 역시 Orca가 supported agent를 "full-autonomy permission flag pre-applied" 상태로 실행하며, worktree 자체를 sandbox로 삼는 의도라고 설명한다. ([Agents & sessions → Launch defaults](https://www.onorca.dev/docs/model/agents-sessions#launch-defaults))

### 6.2 보안 의미

Permission bypass가 활성화된 agent는 일반적으로 각 CLI가 제공하는 human approval 또는 sandbox control을 우회할 수 있으므로 기업 환경에서는 별도 검토가 필요하다. 구체적인 bypass flag 자체와 Orca의 기본 적용 사실은 공식 문서에서 확인할 수 있다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

이때 위험 시나리오에는 예상하지 않은 shell command, 파일 변경/삭제, credential 접근, network command 실행 등이 포함될 수 있다. **이 목록은 Orca가 해당 행위를 실제로 수행한다는 주장이 아니라, 높은 로컬 실행 권한을 가진 coding agent에 대한 일반적인 위협 모델이다.**

### 6.3 Manual mode

Orca는 `Settings → Agents → Agent Permissions`에서 uncustomized agent를 Yolo와 Manual launch 사이에서 전환할 수 있다고 공식 문서에 명시한다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

개별 agent launch argument를 직접 override한 경우 global permission-mode migration이 해당 agent 설정을 덮어쓰지 않는다고 설명한다. ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

**회사 권고:** 별도 OS/container sandbox가 검증되지 않은 환경에서는 `Manual`을 기본 정책으로 사용한다.

**회사 권고:** custom agent wrapper가 `--dangerously-skip-permissions`, `--yolo` 등 위험 flag를 내부에서 재추가하지 않는지 별도로 검증한다.

---

## 7. Git Worktree 기반 작업공간 분리

Orca는 task마다 실제 Git worktree를 생성한다고 공식 문서에 설명한다. ([Worktrees](https://www.onorca.dev/docs/model/worktrees))

각 worktree는 자체 branch, 자체 on-disk files, 자체 agent terminals를 가진다. ([Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model))

Orca 문서는 이 구조가 병렬 agent가 서로의 파일을 덮어쓰지 않도록 한다고 설명한다. ([Worktrees](https://www.onorca.dev/docs/model/worktrees))

```text
repository
├── main working tree
├── agent-A worktree
├── agent-B worktree
└── agent-C worktree
```

이 구조는 다음과 같은 개발 작업 격리 효과를 제공한다.

- Agent별 working directory 분리 ([Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model))
- Agent별 branch 분리 ([Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model))
- Agent terminal을 해당 worktree에 scope ([Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model))
- 작업 완료 후 diff review 및 선택적 commit/push/PR 흐름 제공 ([Worktrees → Per-feature lifecycle](https://www.onorca.dev/docs/model/worktrees#per-feature-lifecycle))

그러나 공식 문서가 설명하는 worktree는 Git 작업공간 격리이며 별도의 OS-level security sandbox라고 정의되지 않는다. ([Worktrees](https://www.onorca.dev/docs/model/worktrees))

또한 Orca의 agent launch 문서는 worktree를 sandbox처럼 활용하려는 의도 때문에 full-autonomy flag를 기본 사용한다고 설명한다. 이 점은 worktree 격리와 OS security sandbox를 동일시하면 안 되는 중요한 이유다. ([Agents & sessions → Launch defaults](https://www.onorca.dev/docs/model/agents-sessions#launch-defaults))

```text
Git worktree
  = 작업공간 분리
  ≠ OS security sandbox
```

**회사 권고:** filesystem, process, credential, network 경계가 필요하면 OS sandbox/container/VM/endpoint policy를 별도로 적용한다.

---

## 8. Remote Access 보안

Remote Orca Server에서 project, worktree, terminal, agent session은 server machine에 존재하며 client는 UI와 input을 제공한다. ([Remote Orca Servers → What runs where](https://www.onorca.dev/docs/remote-servers#what-runs-where))

공식 문서는 Remote Orca Server 기능을 Beta로 표시하고, server와 client를 Tailscale tailnet 또는 LAN 같은 사용자가 통제하는 private network path에 두도록 안내한다. ([Remote Orca Servers](https://www.onorca.dev/docs/remote-servers))

각 paired client에는 별도의 revoke 가능한 token이 생성된다. ([Remote Orca Servers → Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security))

Grant를 revoke하면 해당 grant를 사용하는 active client가 즉시 disconnect된다고 설명한다. ([Remote Orca Servers → Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security))

공식 문서는 Orca port를 public Internet으로 직접 forward하지 말라고 명시한다. ([Remote Orca Servers → Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security))

대신 Tailscale, WireGuard, trusted LAN, SSH forwarding 또는 authenticated tunnel을 권고한다. ([Remote Orca Servers → Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security))

Pairing URL은 Orca runtime에 접근할 수 있는 capability이므로 password처럼 취급하고 의도한 client에게만 전달하라고 안내한다. ([Remote Orca Servers → Create an access link](https://www.onorca.dev/docs/remote-servers#1-create-an-access-link-on-the-server))

**회사 권고:** Remote 기능이 필요하지 않다면 사용하지 않는다.

**회사 권고:** 필요한 경우 사내 VPN/private network/SSH tunnel 내부로 제한하고 public inbound를 차단한다.

---

## 9. Secret 및 Credential 보호

Orca의 `computer use` CLI 문서는 secret을 shell history에 남기지 않도록 stdin으로 전달하라고 안내한다. ([Computer use → Sensitive input](https://www.onorca.dev/docs/cli/computer-use#sensitive-input))

`set-value`에는 `--value-stdin`, `type-text`와 `paste-text`에는 `--text-stdin`을 사용할 수 있다고 설명한다. ([Computer use → Sensitive input](https://www.onorca.dev/docs/cli/computer-use#sensitive-input))

**회사 권고:** API key를 source repository에 저장하지 않는다.

**회사 권고:** `.env` 및 credential file은 Git에서 제외하고, 가능하면 OS Keychain 또는 회사 Secret Manager를 사용한다.

**회사 권고:** Production credential은 개발용 AI agent에서 사용하지 않고 최소 권한/단기 credential을 우선한다.

---

## 10. Runtime 보안 구현에서 확인 가능한 사항

Orca 공개 source의 `runtime-rpc.ts`는 파일 자체를 bundled CLI의 "single security boundary"라고 설명하며 transport setup, auth-token enforcement, admission control 등을 한 위치에 모아 검토할 수 있도록 했다고 명시한다. ([GitHub source → runtime-rpc.ts](https://github.com/stablyai/orca/blob/main/src/main/runtime/runtime-rpc.ts#L1-L2))

동일 source에서 runtime RPC server가 random auth token을 생성하는 구현을 확인할 수 있다. ([GitHub source → runtime-rpc.ts](https://github.com/stablyai/orca/blob/main/src/main/runtime/runtime-rpc.ts))

Remote device registry source는 paired device마다 개별 revoke 가능한 token을 사용하는 이유를 코드 주석으로 설명한다. ([GitHub source → device-registry.ts](https://github.com/stablyai/orca/blob/main/src/main/runtime/device-registry.ts#L1-L4))

**주의:** 공개 source에서 특정 보안 기능을 확인할 수 있다는 사실은 penetration test, formal verification, SOC 2 또는 ISO 인증을 의미하지 않는다.

---

## 11. Open Source 및 검토 가능성

Orca 공식 GitHub 저장소는 공개되어 있고 MIT License를 사용한다. ([Orca GitHub README → License](https://github.com/stablyai/orca#license))

따라서 Security Engineering 팀은 source review, SAST, dependency/SCA scan, secret scan, license scan, network endpoint review 등을 자체 절차에 따라 수행할 수 있다. 이 항목들은 오픈소스라는 특성에서 가능한 **회사 검토 활동**이며 Orca가 공식적으로 보증하는 보안 인증 항목은 아니다. ([Orca 공식 GitHub](https://github.com/stablyai/orca))

---

## 12. 공개 보안 거버넌스 자료의 한계

2026-08-20 검토 시점에 GitHub Security 페이지는 **No security policy detected**라고 표시하며 `SECURITY.md`가 설정되지 않았음을 보여준다. ([GitHub → Security](https://github.com/stablyai/orca/security))

같은 페이지에는 published security advisory가 없다고 표시된다. ([GitHub → Security](https://github.com/stablyai/orca/security))

따라서 공개 자료만을 근거로 다음 항목이 존재하거나 충족된다고 가정해서는 안 된다.

- SOC 2
- ISO 27001
- 독립 penetration test report
- 공식 Security Whitepaper
- 정식 Vulnerability Disclosure Policy

**회사 권고:** 위 항목이 도입 승인 요건이라면 Orca maintainer/vendor에게 별도로 확인하거나 내부 source/binary 검증으로 보완한다.

---

## 13. 권고 Enterprise Security Profile

아래 항목은 Orca 공식 기본값이 아니라 **본 문서가 제안하는 사내 배포 정책**이다.

| 영역 | 권고 정책 | 공식 근거 |
|---|---|---|
| Telemetry | 기본 비활성화 | [How to opt out](https://www.onorca.dev/docs/telemetry#how-to-opt-out) |
| Telemetry outbound | PostHog US 통신 여부 검증 | [Where the data goes](https://www.onorca.dev/docs/telemetry#where-the-data-goes) |
| Agent permission | Manual 기본 | [Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default) |
| Agent binary | 회사 승인 CLI/wrapper만 허용 | [Supported agents](https://www.onorca.dev/docs/agents/supported) |
| Model/API | 회사 승인 endpoint만 허용 | [Orca is not a model](https://www.onorca.dev/docs#what-orca-is-not) |
| Worktree | Agent별 worktree 유지 | [Worktrees → The model](https://www.onorca.dev/docs/model/worktrees#the-model) |
| OS sandbox | 필요 시 별도 적용 | [Launch defaults](https://www.onorca.dev/docs/model/agents-sessions#launch-defaults) |
| Remote access | 기본 미사용 또는 private network 제한 | [Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security) |
| Public exposure | Orca port 직접 공개 금지 | [Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security) |
| Secrets | CLI argument보다 stdin/secret manager 선호 | [Sensitive input](https://www.onorca.dev/docs/cli/computer-use#sensitive-input) |
| Source review | 필요 시 내부 SAST/SCA 수행 | [Official GitHub](https://github.com/stablyai/orca) |

---

## 14. Security Engineering 검증 체크리스트

아래는 공식 기능 설명과 별도로 실제 회사 환경에서 검증할 것을 권고하는 항목이다.

- [ ] `ORCA_TELEMETRY_DISABLED=1` 적용 및 실제 outbound traffic 검증 — [공식 opt-out 문서](https://www.onorca.dev/docs/telemetry#how-to-opt-out)
- [ ] PostHog US endpoint가 차단 또는 허용 정책과 일치하는지 확인 — [공식 telemetry destination](https://www.onorca.dev/docs/telemetry#where-the-data-goes)
- [ ] `Settings → Agents → Agent Permissions → Manual` 적용 확인 — [공식 permission 문서](https://www.onorca.dev/docs/agents/supported#permissions-default)
- [ ] Custom agent command에 permission-bypass flag가 포함되지 않았는지 확인 — [공식 permission 문서](https://www.onorca.dev/docs/agents/supported#permissions-default)
- [ ] Agent가 회사 승인 API/LLM endpoint만 사용하는지 확인 — [Orca가 model이 아니라는 공식 설명](https://www.onorca.dev/docs#what-orca-is-not)
- [ ] Agent wrapper의 environment variable / credential source 검토
- [ ] Agent process의 filesystem 접근 범위 검토
- [ ] SSH key / Git credential / cloud credential 접근 여부 검토
- [ ] Git worktree가 OS sandbox와 혼동되지 않도록 보안 설계 문서에 명시 — [Worktree 문서](https://www.onorca.dev/docs/model/worktrees), [Launch defaults](https://www.onorca.dev/docs/model/agents-sessions#launch-defaults)
- [ ] Remote Orca Server 미사용 여부 또는 private network 제한 확인 — [Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security)
- [ ] Public Internet으로 Orca port가 노출되지 않았는지 확인 — [Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security)
- [ ] Binary hash / version 고정 및 배포 provenance 관리
- [ ] Dependency/SCA/SAST/secret scan 수행 — [Official GitHub](https://github.com/stablyai/orca)

---

## 15. Security Engineering에 전달할 핵심 메시지

> **Orca는 새로운 AI model/provider가 아니라, 기존 CLI coding agent를 실행하고 여러 작업을 Git worktree 단위로 orchestration하는 개발환경 계층이다.** ([What is Orca? → What Orca is not](https://www.onorca.dev/docs#what-orca-is-not), [Supported agents](https://www.onorca.dev/docs/agents/supported), [Worktrees](https://www.onorca.dev/docs/model/worktrees))
>
> **Orca는 source code, third-party AI agent API key, proprietary file을 수집하지 않는다고 공식 Privacy Policy에 명시하며, telemetry에서도 prompts, agent output, terminal output, repository/path 정보 등을 전송하지 않는다고 설명한다.** ([Privacy Policy → Information We Collect](https://www.onorca.dev/privacy#1-information-we-collect), [Privacy & Telemetry → What we never send](https://www.onorca.dev/docs/telemetry#what-we-never-send))
>
> **Telemetry는 비활성화할 수 있다.** ([Privacy & Telemetry → How to opt out](https://www.onorca.dev/docs/telemetry#how-to-opt-out))
>
> **다만 Orca의 기본 agent launch는 permission-bypass/full-autonomy flag를 사용하므로, 기업 환경에서는 Manual permission과 별도 endpoint/credential/network 통제를 적용하는 것이 중요하다.** ([Supported agents → Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default))

즉 Orca의 안전성을 단순히 제품 자체의 주장으로 설명하기보다, **공식 문서로 확인되는 privacy/local execution/worktree 특성을 활용하면서 위험한 기본 permission 정책을 명시적으로 제한하는 Enterprise Deployment Profile을 구성한다**는 접근이 적절하다.

---

## 16. 공식 근거 문서 모음

- [Orca Docs — What is Orca?](https://www.onorca.dev/docs)
- [Orca Docs — Privacy & Telemetry](https://www.onorca.dev/docs/telemetry)
- [Orca Privacy Policy](https://www.onorca.dev/privacy)
- [Orca Docs — Supported agents / Permissions default](https://www.onorca.dev/docs/agents/supported#permissions-default)
- [Orca Docs — Agents & sessions / Launch defaults](https://www.onorca.dev/docs/model/agents-sessions#launch-defaults)
- [Orca Docs — Worktrees / The model](https://www.onorca.dev/docs/model/worktrees#the-model)
- [Orca Docs — Remote Orca Servers / Access and security](https://www.onorca.dev/docs/remote-servers#access-and-security)
- [Orca Docs — Computer use / Sensitive input](https://www.onorca.dev/docs/cli/computer-use#sensitive-input)
- [Orca GitHub — Security](https://github.com/stablyai/orca/security)
- [Orca GitHub — runtime-rpc.ts](https://github.com/stablyai/orca/blob/main/src/main/runtime/runtime-rpc.ts)
- [Orca GitHub — device-registry.ts](https://github.com/stablyai/orca/blob/main/src/main/runtime/device-registry.ts)
- [Orca Official GitHub Repository](https://github.com/stablyai/orca)
