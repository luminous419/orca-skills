# Orca Security Assessment

> Security review notes for introducing Orca into a controlled software-development environment.
>
> Last reviewed: 2026-08-20

## 1. Purpose

This document summarizes security-relevant characteristics of Orca based on its public documentation and public source repository, and proposes a conservative deployment profile suitable for review by a Security Engineering team.

The goal is **not** to claim that Orca is a security sandbox or that its use is risk-free. Orca should be treated as an orchestration/IDE layer that launches and coordinates existing CLI coding agents. Security therefore depends on both Orca's own behavior and the permissions/network configuration of the underlying agents it launches.

## 2. Executive Summary

The most important security conclusions are:

1. **Orca primarily runs locally.** Orca's privacy policy describes the product as a downloadable desktop application that primarily operates on the user's machine.
2. **Orca states that it does not collect source code, proprietary files, or third-party AI-agent API keys.** Its telemetry documentation also states that file contents, prompts, agent output, terminal output, repository names, branch names, URLs, paths, and commit messages are not transmitted as telemetry.
3. **Telemetry can be disabled.** Orca documents `DO_NOT_TRACK=1` and `ORCA_TELEMETRY_DISABLED=1` as disabling telemetry.
4. **Orca uses Git worktrees to isolate parallel agent working directories.** This reduces accidental file interference between agents, but it is **not an OS-level security sandbox**.
5. **The default agent permission mode is a significant security concern.** Orca currently pre-fills permission-bypass/full-autonomy flags for supported coding agents, including Claude Code's `--dangerously-skip-permissions`, Codex's `--dangerously-bypass-approvals-and-sandbox`, and `--yolo`-style flags for several other agents.
6. **A safer enterprise profile is available.** Orca documents a global `Settings → Agents → Agent Permissions` setting that can switch uncustomized agents from Yolo to Manual mode. Enterprise deployments should prefer Manual mode unless a separately controlled sandbox justifies broader autonomy.
7. **Orca is not itself an AI model/provider.** Its documentation describes Orca as launching CLI agents as processes. This means an organization can constrain model access to already-approved internal agent/gateway paths rather than introducing a new model-provider data path through Orca itself.
8. **Public security-governance material is limited.** At the time of this review, the public GitHub Security page reports no `SECURITY.md` policy and no published security advisories. Public documentation found during this review should therefore not be treated as equivalent to a formal third-party security certification or vendor security whitepaper.

## 3. Security Model

A useful way to describe Orca to Security Engineering is:

> Orca is an orchestration and development-environment layer for CLI coding agents. It is not a model, not a hosted source-code execution service by default, and not a replacement for the security controls of the underlying coding agent, operating system, Git credentials, or network.

The effective security boundary therefore spans:

- Orca desktop/runtime process
- Orca-managed terminals and worktrees
- underlying CLI coding agents such as Claude Code, Codex, Gemini, or custom agents
- local filesystem and developer credentials
- Git remotes and repository permissions
- internal or external LLM/API endpoints used by those agents
- optional remote Orca server / SSH configuration
- optional telemetry and third-party integrations

## 4. Privacy and Telemetry

### 4.1 Data Orca says it does not collect

Orca's Privacy Policy states that it does not collect:

- source code
- API keys for third-party AI agents
- proprietary files

The Privacy & Telemetry documentation further states that Orca telemetry does not transmit:

- file contents
- prompts
- agent output
- terminal output
- repository names
- branch names
- URLs
- filesystem paths
- commit messages
- hostname
- username
- IP address as telemetry payload data

Telemetry events are described as being keyed by a random locally stored ID and including basic build/platform information and product-usage events.

### 4.2 Telemetry opt-out

The Orca documentation states that telemetry is always off when either of the following environment variables is set:

```bash
DO_NOT_TRACK=1
```

or

```bash
ORCA_TELEMETRY_DISABLED=1
```

### 4.3 Recommended enterprise control

For a security-sensitive or isolated corporate environment, use:

```bash
export ORCA_TELEMETRY_DISABLED=1
```

and, where practical:

```bash
export DO_NOT_TRACK=1
```

Security Engineering should independently verify outbound network behavior of the exact Orca build approved for corporate use, for example through host firewall policy, proxy logs, or packet-level observation.

**Important:** vendor documentation is evidence of intended behavior, not a substitute for runtime verification in the organization's approved build.

## 5. Agent Execution and Permission Risk

### 5.1 Default behavior

This is the most important security caveat in Orca's current documented behavior.

Orca's supported-agents documentation states that new launches are pre-filled with each agent's permission-bypass/full-autonomy flag. Examples include:

```text
Claude Code  → --dangerously-skip-permissions
Codex        → --dangerously-bypass-approvals-and-sandbox
Gemini       → --yolo
```

Equivalent autonomy flags are used for a number of other supported agents where available.

Orca's stated rationale is that agents work inside disposable Git worktrees, allowing users to review and discard changes without confirming every command.

### 5.2 Security interpretation

A Git worktree protects primarily against **source-tree collision and accidental branch/file interference**. It does not inherently prevent an agent process from:

- reading files outside the worktree when OS permissions allow it
- reading environment variables or credentials accessible to the process
- invoking network clients
- executing local tools or scripts
- changing files outside the repository
- invoking Git operations with the developer's credentials
- accessing cloud/CLI credentials available to the logged-in user

Therefore:

> **Git worktree isolation must not be described as an OS sandbox or a complete security boundary.**

### 5.3 Recommended enterprise control

Set:

```text
Settings → Agents → Agent Permissions → Manual
```

for corporate deployments unless Security Engineering has explicitly approved a separate isolation mechanism that makes autonomous execution acceptable.

Also review any per-agent custom launch arguments, because Orca documents that customized agents may be excluded from later global permission-mode changes.

## 6. Worktree Isolation

Orca is worktree-native. Each task can receive:

- its own Git branch
- its own on-disk checkout
- its own agent terminals

This is valuable for multi-agent orchestration because parallel agents do not normally edit the same working directory.

Security benefits include:

- reduced accidental overwrites between agents
- easier diff review before merge
- easier discard/cleanup of untrusted or poor-quality changes
- clearer attribution of changes to a task/worktree

However, worktree isolation has limitations.

### 6.1 Shared paths

Orca supports shared directories and copied gitignored files across worktrees. Its documentation gives examples such as dependency caches and `.env` files.

For a corporate deployment, Security Engineering should review:

- `worktree.sharedDirectories`
- repository-level Worktree Shared Paths
- `.worktreeinclude`

Do not automatically share secrets merely for convenience. In particular, `.env` files should only be propagated when their contents and necessity have been reviewed.

## 7. Network and Remote Access

Orca supports local use, SSH targets, and self-hosted remote Orca servers.

For remote deployment, Orca documentation recommends private connectivity such as Tailscale and documents access-link/token-based connection behavior.

A conservative enterprise policy should:

- prefer local execution when remote execution is unnecessary
- avoid exposing an Orca server directly to the public Internet
- use company-approved VPN/private-network/SSH mechanisms
- restrict listening interfaces and inbound firewall rules
- protect pairing/access links and tokens as secrets
- use unprivileged service accounts for headless/server deployments

The public repository's headless-server documentation explicitly warns that running the AppImage as root with Chromium's `--no-sandbox` disables a security boundary and recommends an unprivileged service user, especially when the service is reachable beyond localhost.

## 8. Model and Data-Flow Considerations

Orca documentation describes the agent picker as launching a CLI process in a terminal. Orca itself is not the LLM.

This distinction is important in a corporate deployment.

A controlled architecture can look like:

```text
Developer
   │
   ▼
Orca
   │
   ├── approved CLI agent A
   │       │
   │       └── approved internal LLM/API gateway
   │
   └── approved CLI agent B
           │
           └── approved internal LLM/API gateway
```

For example, where an organization already exposes approved internal models through controlled Claude Code wrappers, Orca can be configured to launch only those approved commands.

The desired security property is:

> **Orca should not introduce an additional unapproved model-provider path. The approved CLI agent and its existing internal gateway configuration should remain the only route through which source-code context reaches an LLM.**

This property should be verified against the actual corporate Orca configuration and agent definitions rather than assumed from product architecture alone.

## 9. CLI Runtime Security Boundary

The public Orca source repository contains an implementation comment in `src/main/runtime/runtime-rpc.ts` describing that module as the bundled CLI's "single security boundary" and co-locating:

- transport setup
- auth-token enforcement
- admission control
- keepalive framing
- orphan-socket sweeping

This is useful evidence that authentication and transport security are explicitly considered in the runtime architecture.

However, a source-code comment should not be treated as proof of formal security certification. Security Engineering may still wish to review the relevant runtime implementation for the exact approved version.

## 10. Public Security Governance / Assurance Gaps

As of this review, the public `stablyai/orca` GitHub Security page reports:

- no detected `SECURITY.md` security policy
- no published GitHub security advisories

This means the currently available public materials do **not** provide the same assurance as a formal vendor security package such as:

- a documented vulnerability-disclosure policy
- SOC 2 report
- ISO 27001 certification
- independent penetration-test report
- formal secure-SDLC documentation
- published security whitepaper

Absence of those public artifacts does not itself demonstrate insecurity, but it is a relevant vendor/project maturity consideration for an enterprise security review.

Because Orca is open source, an organization can compensate partially by performing its own:

- source review
- dependency/SCA scan
- SAST
- binary provenance validation
- outbound-network observation
- permission/configuration review

## 11. Recommended Corporate Deployment Profile

The following baseline is recommended for a security-sensitive development environment.

| Area | Recommended control |
|---|---|
| Telemetry | Set `ORCA_TELEMETRY_DISABLED=1`; optionally also `DO_NOT_TRACK=1` |
| Agent permissions | Set global Agent Permissions to **Manual** |
| Agent allowlist | Permit only company-approved CLI agent commands |
| Model endpoints | Permit only approved internal LLM/API gateways |
| External agents | Do not configure unapproved public model endpoints |
| Worktrees | Use separate worktrees per task/agent |
| Shared paths | Minimize and explicitly review shared directories/files |
| Secrets | Do not broadly copy `.env`, cloud credentials, or tokens into worktrees |
| Network | Apply corporate egress/firewall/proxy restrictions |
| Remote server | Keep private; do not directly expose to the public Internet |
| Runtime user | Run as an unprivileged user; avoid `--no-sandbox` deployments |
| Git | Keep existing branch protection, PR review, and repository authorization controls |
| Audit | Review agent diffs and retain appropriate Git/CI evidence |
| Versioning | Pin/approve a specific Orca version and reassess security-sensitive changes during upgrade |

## 12. Risk Matrix

| Risk | Default/Observed Concern | Recommended Mitigation | Residual Risk |
|---|---|---|---|
| Source/prompt telemetry leakage | Orca documentation says content is not included in telemetry | Disable telemetry; independently verify network traffic | Low, subject to build verification |
| Agent executes unsafe shell commands | Default supported-agent launches use bypass/full-autonomy flags | Use Manual permission mode; OS/network controls | Medium |
| Agent accesses files outside worktree | Worktree is not an OS sandbox | Least-privilege user, filesystem permissions, dedicated environment if required | Medium |
| Agent sends code to unauthorized LLM | Depends on underlying CLI configuration | Agent allowlist + approved internal gateway + egress policy | Low/Medium |
| Secret exposure via shared worktree files | Shared paths / `.worktreeinclude` can include gitignored files | Explicit review; do not broadly replicate secrets | Medium |
| Remote runtime exposure | Remote server expands attack surface | Private network/VPN/SSH, token protection, firewall | Low/Medium |
| Supply-chain compromise | Open-source Electron/Node application and dependencies | Version pinning, SCA/SAST, artifact/provenance review | Medium |
| Weak vendor security assurance | No public SECURITY.md/advisories found | Internal review; request vendor/project security documentation if required | Medium |

## 13. Proposed Security-Team Positioning

A defensible description to Security Engineering is:

> Orca is not being proposed as a new AI provider or as a security sandbox. It is a local orchestration/IDE layer for CLI coding agents. In the corporate configuration, only already-approved agent commands and internal LLM gateways should be permitted. Telemetry should be disabled, default full-autonomy/Yolo agent execution should be replaced with Manual permissions, and existing endpoint, filesystem, Git, network, and CI controls should remain in force. Git worktrees provide useful task-level source isolation, but they are not considered an OS-level sandbox.

This framing avoids overstating Orca's security guarantees while clearly identifying the controls required to deploy it safely.

## 14. Items Security Engineering Should Verify Before Approval

1. Exact Orca version/build proposed for deployment.
2. Hash/signature/provenance of distributed binaries.
3. Runtime outbound connections with telemetry disabled.
4. Effective launch command and arguments for every enabled CLI agent.
5. Confirmation that Agent Permissions is set to Manual where required.
6. No unapproved model-provider endpoints in agent configuration.
7. Filesystem access available to Orca and child agent processes.
8. Treatment of environment variables, credentials, `.env`, SSH keys, cloud credentials, and Git credentials.
9. Worktree Shared Paths and `.worktreeinclude` configuration.
10. Remote-server exposure, if remote mode is enabled.
11. GitHub/GitLab/other SCM token scopes.
12. Upgrade and vulnerability-monitoring process for Orca and its dependencies.

## 15. Official/Public References

The following public sources were reviewed on 2026-08-20:

- Orca Docs — Privacy & Telemetry  
  https://www.onorca.dev/docs/telemetry

- Orca — Privacy Policy  
  https://www.onorca.dev/privacy

- Orca Docs — Supported agents  
  https://www.onorca.dev/docs/agents/supported

- Orca Docs — Agents & sessions  
  https://www.onorca.dev/docs/model/agents-sessions

- Orca Docs — Worktrees  
  https://www.onorca.dev/docs/model/worktrees

- Orca Docs — Remote Orca Servers  
  https://www.onorca.dev/docs/remote-servers

- Orca Docs — Ways to run Orca  
  https://www.onorca.dev/docs/ways-to-run

- Orca public repository  
  https://github.com/stablyai/orca

- Orca GitHub Security page  
  https://github.com/stablyai/orca/security

- Orca runtime RPC source (`runtime-rpc.ts`)  
  https://github.com/stablyai/orca/blob/main/src/main/runtime/runtime-rpc.ts

- Headless Linux server security note  
  https://github.com/stablyai/orca/blob/main/docs/reference/headless-linux-server.md

## 16. Disclaimer

This document is an engineering security assessment based on publicly available Orca documentation and source material reviewed on the date above. It is not a certification, penetration-test result, or representation from the Orca maintainers. Product behavior may change between releases; Security Engineering should validate the exact version and configuration proposed for deployment.
