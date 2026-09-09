# Orca runtime primitives — investigation at v1.4.197

**What this document is.** A record of what the Orca runtime actually does, read from its own
source and its colocated regression tests at one pinned revision, together with the decisions this
project took about that material: which capabilities it reuses, adapts, reimplements or rejects;
what it adopts and under what licence; and which Orca-specific layers it excludes.

**What this document is not.** It is not a requirement document and it states no obligation on any
implementation. Every "must" that binds an adapter lives in
[`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md); every "will build" lives in
[`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md). This document is evidence,
and the other two cite it rather than re-deriving it.

**Two prohibitions govern the whole investigation and are restated in
[Excluded Orca-specific layers](#excluded-orca-specific-layers):** this project does not fork Orca,
and it does not build a headless Orca. Orca was read as a reference implementation, not as a
codebase to take.

---

## Pinned revision and method

### The revision under investigation

| Fact | Value | How it was established |
| --- | --- | --- |
| Upstream repository | `https://github.com/stablyai/orca` (public, MIT) | repository metadata |
| Tag | **`v1.4.197`** | `git describe --tags --exact-match HEAD` in the read-only checkout |
| Commit | **`5ee4ace516080891731d100f843b074408a9ce0e`** | `git rev-parse HEAD` in the read-only checkout |
| Declared version | `package.json:3` → `"version": "1.4.197"` | matches the tag |
| Working tree | clean (`git status --porcelain` empty) | the evidence was not modified |

Everything in that table is a fact about the **pinned source**. It says nothing about which
build is running on the investigating host; that is a separate fact with a separate authority,
established in [The live runtime, established separately](#the-live-runtime-established-separately)
below.

The checkout was **read-only throughout**. Every pinned read went through `git -C <checkout>
show/ls-tree/grep` or a read-only file read; nothing was written, fetched or checked out inside it.

### The live runtime, established separately

The **pinned source version** and the **running application version** are two different facts. They
are recorded here with two different authorities and are never inferred from one another.

| Fact | Value | Authority |
| --- | --- | --- |
| Pinned source version | `v1.4.197` @ `5ee4ace516080891731d100f843b074408a9ce0e` | the git tag, the commit, and `package.json:3` at that commit — the table above |
| Live runtime version on the investigating host | `1.4.197` | `orca status --json` → `result.runtime.appVersion`, produced by `src/cli/runtime/status.ts:40, 60` at the pinned commit |

**Why `status --json` is the authority and `orca --version` is not.** The two commands read
different things at this revision:

- `orca --version` is a **disk read of the shipped CLI build's own package metadata**. The fast
  path at `src/cli/index.ts:63-73` is taken only when `argv.length === 1`, and it calls
  `readOrcaCliVersion()` (`src/cli/cli-version.ts:5-13`), which `readFileSync`s a sibling
  `package.json` and returns `null` on any failure. It never contacts the running application, and
  any second argument falls through to the generic parser, which prints help instead — which is how
  a help page can be mistaken for a version. It is therefore not relied upon here.
- `orca status --json` reports `result.runtime.appVersion` obtained **over RPC from the running
  runtime** (`status.get`, `src/cli/runtime/status.ts:40`), spread into the response at
  `src/cli/runtime/status.ts:60`.

**What was actually run, and what it returned.** On the investigating host:

```text
$ /usr/local/bin/orca status --json     # exit 0
  ok                            = true
  result.runtime.appVersion     = "1.4.197"
  result.runtime.reachable      = true
  result.runtime.state          = "ready"
```

A redacted copy of that capture is preserved at
`artifacts/runs/run_a4484a738299/orca_status_observed.json`; the artifact states what was
removed and why.

**Two traps this evidence deliberately avoids**, both read from the same file. `ok` is *not* a
health signal — `buildCliStatusResponse` hardcodes `ok: true`
(`src/cli/runtime/status.ts:91-100`), so even a stopped runtime answers `ok: true`. And
`appVersion` is a *conditional* spread (`src/cli/runtime/status.ts:60`), absent whenever the RPC
returned none — the `not_running` / `stale_bootstrap` / `starting` branches
(`src/cli/runtime/status.ts:19-37, 71-88`), which also set `reachable: false`. So the observation
above is only accepted because `reachable` is `true` (set only on the successful RPC path,
`src/cli/runtime/status.ts:54`) *and* `appVersion` is present as a string *and* it equals the
expected value.

That acceptance rule is not prose. It is enforced by a reproducible check,
`artifacts/runs/run_a4484a738299/check_runtime_version.py`, which takes the expected version as an
explicit argument and reports three deliberately distinct outcomes. It **passes** (exit 0) only for
a document that satisfies every check above. It **rejects the evidence** (exit 1) when a document
*was* obtained but does not establish the version — help-page text, unparseable or truncated JSON,
`ok: false`, an unreachable runtime, an absent or non-string `appVersion`, or a mismatched
`appVersion` — and that includes the canonical case of a CLI that exits non-zero while still
printing a help page. It reports a distinct **blocked** result (exit 2) — never a pass — when no
document was obtained at all: an unreadable or absent source, or an empty or whitespace-only
document from any input source, which covers a launched `orca status --json` that prints nothing on
stdout whether it exits zero or non-zero. That last boundary is deliberate rather than incidental:
a runtime that is simply down must be reported as *could not check*, not as *version evidence
rejected*. A blocked command result carries the child's exit status and, deliberately, **no part
of the child's stderr**: because `--orca-bin` can launch any executable, that stderr is arbitrary
text that no finite denylist could make safe to reproduce, and an excerpt that merely *looks*
scrubbed would invite the trust it cannot earn. The diagnostic therefore reports only facts *about*
the stderr — whether it was empty, its length in bytes, and a truncated SHA-256 digest that lets two
runs be compared without disclosing any content.

**The derived observation.** Given both facts and their separate evidence, the pinned source and
the live runtime on this host **happen to be the same version, 1.4.197**. That equality is an
observation about this host at this moment, not a property of the pinned revision: it is what makes
the claims below claims about the runtime this repository actually talks to, and it would have to
be re-established on any other host.

For completeness: `orca --version` also printed `1.4.197` (exit 0) on this host. That is recorded
as a corroborating convenience observation only. It is **not** relied upon, because as shown above
it reads shipped files rather than the running process and is not guaranteed to return a version on
every installation.

### Citation convention

Two roots are cited in this document set, and they must stay distinguishable because both trees
contain a `docs/` directory and a `README.md`.

| Written as | Root | Baseline |
| --- | --- | --- |
| `src/…`, `config/…`, `tests/…`, `package.json`, `pnpm-workspace.yaml`, `LICENSE` | the **pinned Orca checkout** | commit `5ee4ace516080891731d100f843b074408a9ce0e` (`v1.4.197`) |
| `orca:docs/…`, `orca:README.md` | the **pinned Orca checkout**, where the root collides | same |
| `orca-worker-reviewer-orchestration/…`, `scripts/…`, `artifacts/…` | **this repository** (`orca-skills`) | branch working tree |
| `skills:docs/…` | **this repository**, where the root collides | branch working tree |

A citation is a repo-relative path plus a line or line range. A bare `docs/…` or `README.md`
citation with no prefix is not a valid citation in these documents.

Two kinds of evidence in this document are deliberately **not** written as `path:line` citations,
because they do not live in either tree and a resolver must not try to open them: the installed
Orca application bundle at `/Applications/Orca.app/Contents/Resources/node_modules/…`, and npm
registry tarballs. Both appear below as prose provenance statements that name what was read and
where it came from.

### What was read

- **Source and colocated regression tests.** Colocated `*.test.ts` / `*.spec.ts` files were read as
  evidence in their own right, not skipped in favour of the implementation. Where a claim rests on
  a test assertion, the test path and line are cited alongside the implementation.
- **Entry method.** The tree is large — `src/main` alone holds 7,710 `.ts` files, of which 3,096
  are colocated tests — so it was not enumerated. Investigation was dependency-anchored
  (`rg -l "node-pty" src --type ts` to find every PTY owner), then concept-grepped per area
  (`tui-idle|tuiIdle`, `worker_done|workerDone`, `type AgentStatus`, `'worker-[a-z-]+'`), then
  directory-anchored on `src/main/pty/`, `src/main/providers/`, `src/main/runtime/`,
  `src/main/runtime/orchestration/`, `src/shared/`, `src/cli/specs/`, `src/cli/handlers/`.
- **Coupling was measured, not estimated** — `rg -l "from 'electron'" <dir> --type ts` per
  top-level source directory, plus the repository's own reachability gate
  `config/scripts/check-runtime-electron-ratchet.mjs`.
- **`node_modules` was never read from the checkout**; it is absent there. Where dependency licence
  text was needed it was read from the installed application bundle and from published registry
  tarballs, and that provenance is stated at each claim.

### What was not read, and what an unread thing is worth

Where only a CLI surface or a bundled skill guide was found and no implementation, that is recorded
as a gap, not inferred into a conclusion. In particular `src/cli/bundled-skill-guides.ts` embeds
large Markdown skill text; every grep hit inside it was discarded as documentation rather than
implementation.

**An unread thing is recorded as unread.** Nothing in this document is upgraded from "not verified"
to "verified" by plausibility. The surviving unknowns are listed in
[Open items carried forward](#open-items-carried-forward) with their status intact.

### Compatibility observation (recorded, not acted on)

`skills:docs/COMPATIBILITY.md` records the row *"Real Orca runtime with fake agents | Orca 1.4.196 |
VERIFIED for the current head as a single point observation, compatibility-gated by the opt-in
Step 4 integration suite."* The subject pinned here is **1.4.197**, and the runtime running on the
investigating host was separately observed to be **1.4.197** — see
[The live runtime, established separately](#the-live-runtime-established-separately) for each
side's evidence.

That document's own governing rule is quoted here because it decides what may be done about the
drift: *"Every observation below is a point observation, never a continuous supported range, and an
observation is bound to the repository revision that produced it."*

**No 1.4.197 integration suite was run in this ticket.** A version observation — even the
machine-readable `orca status --json` → `result.runtime.appVersion` one above — establishes a
*version* fact, not a *verification* fact. Editing a verification matrix on the strength of a
version string would assert a verification nobody performed, so `skills:docs/COMPATIBILITY.md` is
**not modified** by this work. Updating that matrix is a separate act that requires a real run.

---

## Evidence by investigation area

Twelve areas, matching the twelve mandatory investigation areas of the ticket.

---

### Area 1 — PTY / process spawn and process-group ownership

| Concern | Path |
| --- | --- |
| Spawn entry point | `src/main/providers/local-pty-spawn.ts:21-122` |
| The `node-pty` call itself | `src/main/providers/local-pty-spawn.ts:71-88` |
| PTY id + incarnation minting | `src/main/providers/local-pty-spawn.ts:39-40` |
| Reattach-before-spawn | `src/main/providers/local-pty-spawn.ts:25-35, 66-70` |
| POSIX process-group discovery | `src/main/pty/posix-pty-process-groups.ts:55-83` |
| POSIX foreground-group resolution | `src/main/pty/posix-pty-foreground-group.ts:93-116` |
| Group-scoped force kill | `src/main/pty/posix-pty-process-groups.ts:90-139` |
| macOS `spawn-helper` platform fact | `src/shared/node-pty-spawn-helper.ts:1-11` |

**Orca does not own the agent's process group.** It spawns a shell through `node-pty` and then
*discovers* group membership from the OS process table via `ps`, keyed on the PTY's tty.
`readPtyProcessTable` (`src/main/pty/posix-pty-process-groups.ts:28-37`) runs
`ps -p <rootPid> -o pid=,pgid=,tty=` and then `ps -t <tty>` — deliberately tty-scoped rather than
`ps -ax`, because "a whole-host `ps -ax` takes nearly a second on large machines"
(`src/main/pty/posix-pty-process-groups.ts:34-35`).

The root pid is not the right signal target, and the source says why
(`src/main/pty/posix-pty-foreground-group.ts:84-92`): "the shell calls `setpgid` for job control, so
it leaves the root's group immediately, and on macOS the root is `login(1)` — which neither handles
nor forwards SIGWINCH. A foreground TUI is a third group again."

**Three independent safety guards, all fail-closed:**

1. **Unbound tty → refuse.** `tty === '?' || '??'` returns `null`
   (`src/main/pty/posix-pty-process-groups.ts:62-64`).
2. **Shared tty → refuse.** If Orca's own pid shares the root's tty, group signalling is abandoned
   and the caller falls back to a root-scoped kill
   (`src/main/pty/posix-pty-process-groups.ts:65-69`, mirrored at
   `src/main/pty/posix-pty-foreground-group.ts:109-113`).
3. **Recycled-pid guard.** The captured-at-spawn pts name is compared to what `ps` reports now; a
   mismatch returns `null` (`src/main/pty/posix-pty-foreground-group.ts:104-108`) — "without pinning
   the tty we captured at spawn, a recycled pid could aim a group signal at a real terminal".

`signalPosixPtyForegroundGroup` is scoped to SIGWINCH by its callers on purpose: "a destructive
signal must keep the narrower root-pid target plus the descendant-sweep identity machinery"
(`src/main/pty/posix-pty-foreground-group.ts:118-124`).

**Regression tests** — `src/main/pty/posix-pty-process-groups.test.ts`:

- `src/main/pty/posix-pty-process-groups.test.ts:29` "returns every group attached to the root PTY
  with the root group last" — pins the ordering contract, so the root group cannot reap its children
  before they are signalled.
- `src/main/pty/posix-pty-process-groups.test.ts:33` "refuses an unbound root or a PTY shared with
  Orca itself".
- `src/main/pty/posix-pty-process-groups.test.ts:39` "kills foreground and background groups before
  the PTY leader".
- `src/main/pty/posix-pty-process-groups.test.ts:54` "falls back when the process table cannot prove
  PTY ownership".
- `src/main/pty/posix-pty-process-groups.test.ts:67` "ignores groups that exited after the snapshot
  but preserves real signal errors" — the ESRCH contract at
  `src/main/pty/posix-pty-process-groups.ts:121-126`.
- `src/main/pty/posix-pty-process-groups.test.ts:90` "uses the existing fallback on Windows without
  reading ps".
- `src/main/pty/posix-pty-process-groups.test.ts:105` and
  `src/main/pty/posix-pty-process-groups.test.ts:120` — the breadcrumb ledger records only groups it
  actually signalled.

Also read: `src/main/pty/posix-pty-process-groups.integration.test.ts` and
`src/main/pty/posix-pty-foreground-group.test.ts`.

---

### Area 2 — Agent CLI launch and prompt delivery

| Concern | Path |
| --- | --- |
| Known agent union (43 CLIs) | `src/shared/tui-agent.ts:3-39` |
| Launch command resolution | `src/shared/tui-agent-launch-command.ts:24-109` |
| Per-agent command/config table | `src/shared/tui-agent-config.ts` |
| Structured (resumed) TUI launch | `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:12-148` |

**The agent is launched as a shell command string inside a PTY, not as a direct `execve` of the
agent binary.** `resolveAgentLaunchCommand` composes the per-agent config (or a user
`cmdOverrides[agent]`) with a tokenized, shell-quoted argument suffix
(`src/shared/tui-agent-launch-command.ts:34-55`); session options can be merged with or override
user args (`src/shared/tui-agent-launch-command.ts:49-70`).

The structured-launch path is the strongest evidence for the **ordering** a standalone driver has to
reproduce, and it is worth reading as a sequence:

1. Provider must be `codex` or `claude`, else `agent_session_identity_required`
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:15-18`).
2. `ensureAgentSession(...)` with an explicit `spawnToken` and `providerRoot`
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:21-32`).
3. **Refuse to proceed without process identity** — "The resumed terminal did not publish a process
   identity." when `processId`/`paneKey`/`tabId`/`ptyId` is missing
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:37-39`).
4. Bind owner identity from a *process* identity proof, keyed on `rootPid` **and** `spawnToken`
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:48-61`).
5. **Then** `waitForTerminal(handle, { condition: 'tui-idle', timeoutMs: 30000 })`
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:79`) — readiness gating
   happens *after* identity binding, never before.
6. **Then** wait for a provider-side proof (Codex's thread or Claude's `projects/` transcript leaf
   uuid), with `minimumProviderSessionReceivedAt` as an anti-replay fence
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:80-95`).
7. On any failure: close the terminal **and prove the process exited**, else raise
   `StructuredTuiLaunchCleanupError`
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:119-146`). A launch that
   cannot prove teardown is an error, never a silent cleanup.

Steps 3 → 5 are the load-bearing order: identity first, readiness second. Step 5 alone establishes
nothing about identity or completion.

---

### Area 3 — Delivery acknowledgement, retry and output capture

This is the most directly reusable design in the codebase for a standalone driver.

| Concern | Path |
| --- | --- |
| Paste framing + sanitization | `src/shared/agent-prompt-injection.ts:3-83` |
| Write/submit sequencing | `src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:22-99` |
| Delivery verification | `src/main/runtime/agent-prompt-submission-verification.ts:69-127` |
| Raw terminal send (non-agent) | `src/main/runtime/terminal-send-payload.ts:6-22` |
| Bounded worker output read | `src/main/runtime/orchestration/worker-output-cursor.ts`, `src/main/runtime/orchestration/worker-output-archive.ts`, `src/main/runtime/orchestration/worker-transcript-read.ts` |

**Prompt delivery is a two-write protocol with a verified acknowledgement.**

*Write 1 — the paste frame.* `buildAgentPromptPasteBytes` wraps the prompt in bracketed paste
`ESC[200~ … ESC[201~` (`src/shared/agent-prompt-injection.ts:3-4, 70-72`), after
`sanitizeAgentPromptText` replaces every raw `ESC` with the literal `<ESC>`
(`src/shared/agent-prompt-injection.ts:54-68`) so prompt text cannot inject control sequences. The
whole frame is written in **one** PTY write: "Keep the bracketed paste frame in one PTY write;
Claude's composer can drop the beginning when a large frame is split into independently processed
chunks" (`src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:49-52`).

*The gap before Enter.* Either a **render gate** (`createAgentPromptRenderGate`, armed before the
write and awaited after — `src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:37, 51,
60-65`) or, when no render signal exists, an **open-loop delay** computed from a measured host
ingest rate: `AGENT_PROMPT_SUBMIT_SETTLE_MS = 500` plus `byteLength / rate`, where the rate is
64 B/ms on Windows ConPTY and 4,096 B/ms elsewhere (`src/shared/agent-prompt-injection.ts:19-49`).
The constants are justified by a benchmark table in the source comment
(`src/shared/agent-prompt-injection.ts:7-28`), and the delay is deliberately **never capped**: "a cap
silently reintroduces the mid-paste Enter it exists to prevent"
(`src/shared/agent-prompt-injection.ts:44-46`).

*Write 2 — Enter.* `AGENT_PROMPT_SUBMIT = '\r'` (`src/shared/agent-prompt-injection.ts:5`), written
at `src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:89-91`.

*Acknowledgement.* `verifyAgentPromptSubmission`
(`src/main/runtime/agent-prompt-submission-verification.ts:69-93`) polls every 50 ms until a deadline
and accepts **exactly three** proofs
(`src/main/runtime/agent-prompt-submission-verification.ts:95-104`):

1. `workingSequence` advanced — a synthetic-title-derived `→working` edge;
2. `explicitWorkingStartedAt` advanced past the baseline — a **hook**-reported turn start. Why it
   exists (`src/main/runtime/agent-prompt-submission-verification.ts:106-108`): "hook status reaches
   the runtime directly, so it survives a hidden window and headless serve — the synthetic-title
   route that feeds `workingSequence` does not (#16095)";
3. **only if the agent was already `working` at baseline**, `outputSequence` advanced. The comment
   at `src/main/runtime/agent-prompt-submission-verification.ts:119-121`: "a `→working` edge is
   unreachable for an agent that is already working… An idle agent still owes a real turn start,
   which keeps a swallowed Enter detectable."

Timeout raises `agent_prompt_stalled`. The export comment
(`src/main/runtime/agent-prompt-submission-verification.ts:10-11`) is the honest failure semantics:
**"The prompt bytes are written before verification, so this only ever means 'not observed'."** A
stall is *unknown delivery*, not *not delivered*, and is therefore **not** automatically retryable.

**There is no blind retry.** There are pre-flight and mid-flight aborts instead:

- `assertPromptNotBlocked` throws `agent_prompt_blocked` if the pane is or becomes `permission`
  (`src/main/runtime/agent-prompt-submission-verification.ts:138-142`);
- `assertSamePromptGeneration` throws `terminal_handle_stale` if the pane's generation changed
  (`src/main/runtime/agent-prompt-submission-verification.ts:129-136`);
- the PTY write gate is re-checked before *every* write
  (`src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:33, 48, 74, 88`);
- `ptyController.write(...) === false` ⇒ `terminal_not_writable`
  (`src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:52-54, 89-91`).

**Timeout source:** `AGENT_PROMPT_EFFECT_TIMEOUT_MS` from
`src/shared/orchestration-timing-budgets.ts`, re-exported at
`src/main/runtime/agent-prompt-submission-verification.ts:1-5`;
`resolveAgentPromptEffectTimeoutMs`
(`src/main/runtime/agent-prompt-submission-verification.ts:37-41`) branches on
`HOOK_OBSERVED_TURN_START_AGENTS = {codex, kimi}`
(`src/main/runtime/agent-prompt-submission-verification.ts:8`), pinned by the regression test at
`src/main/runtime/agent-prompt-submission-verification.test.ts:254-257`.

**Output capture — recorded as a gap, not as an absence.** No distinct capture module for the raw
agent stream was located beyond the orchestration-scoped `worker-read` path
(`src/main/runtime/orchestration/worker-output-cursor.ts`,
`src/main/runtime/orchestration/worker-transcript-read.ts`,
`src/main/runtime/orchestration/worker-output-archive.ts`) and the PTY tail buffer
(`tailBuffer` / `tailPartialLine` / `preview` on `RuntimePtyWorktreeRecord`). Carried forward as
[U6](#open-items-carried-forward).

---

### Area 4 — Lifecycle detection: five orthogonal vocabularies

**Orca has no single ten-value lifecycle enum.** It has five orthogonal vocabularies, and that
separation is itself the finding: collapsing them into one enum would erase distinctions Orca keeps
apart deliberately.

| Orca vocabulary | Path | Values |
| --- | --- | --- |
| Agent turn state (hook-reported) | `src/shared/agent-status-types.ts:24-25` | `working` \| `blocked` \| `waiting` \| `done` |
| Title-derived status (weak) | `src/shared/agent-title-core.ts:15` | `working` \| `permission` \| `idle` |
| Terminal process state | `src/shared/runtime-terminal-contracts.ts:186` | `running` \| `exited` \| `unknown` |
| Terminal exit *cause* | `src/shared/terminal-exit-cause.ts:13-33` | `operator_close` \| `signaled` \| `exited` \| `unknown{stop_unverified\|host_status_unavailable\|cause_unreported}` |
| Supervised worker dispatch state | `src/main/runtime/orchestration/types.ts:136-146` | `starting` \| `ready` \| `start_unknown` \| `failed` \| `succeeded` \| `stopping` \| `stop_unknown` \| `stopped` \| `abandoned` |

Three more matter for ownership: `DispatchStatus` =
`pending|dispatched|completed|failed|circuit_broken` (`src/main/runtime/orchestration/types.ts:22`),
`TaskStatus` = `pending|ready|dispatched|completed|failed|blocked`
(`src/main/runtime/orchestration/types.ts:20`), and process-incarnation liveness
`live|exited|unverifiable` (`src/main/runtime/orchestration/worker-terminal-process-liveness.ts:39-62`).

**`WAITING_FOR_INPUT` evidence carries its own provenance.**
`RuntimeTerminalInteractiveWaitSource = 'hook' | 'prompt-text' | 'title'`
(`src/shared/runtime-terminal-contracts.ts:170`) — the wait reports *which* evidence proved it. The
tri-state discipline is spelled out in the `worker-show` CLI spec note
(`src/cli/specs/orchestration-worker-specs.ts:49`):

> "Null means Orca looked and found no wait. An **absent** field means it never looked … and never
> means the worker is not waiting. A waiting worker is healthy, not failed."

The blocked-reason vocabulary is closed and named — `codex-update-prompt | codex-trust-workspace |
codex-cwd-prompt | codex-model-migration-prompt | codex-hooks-review-prompt |
codex-interactive-prompt | agent-approval-prompt` (`src/shared/runtime-terminal-contracts.ts:317-324`),
detected by `detectTerminalWaitBlockedReason` (`src/main/runtime/terminal-wait-detection.ts:44-49`).

**The exit-cause module is the single best statement of the fail-closed rule.**
`src/shared/terminal-exit-cause.ts:1-12`:

> "Orca used to record one number and let every reader guess. That number is not evidence: the stop
> paths synthesize it, node-pty reports 0 for a signalled death, and macOS's TCC `login(1)` wrapper
> returns its own status instead of the shell's. A clean finish, an OOM kill and an operator close
> all arrived as 'code 0' (STA-4536, STA-4603). So a cause is only ever built from evidence someone
> actually holds, and the absence of evidence is spelled `unknown` rather than guessed."

`UNVERIFIED_PROCESS_EXIT_CODE = -1`, with the rule at `src/shared/terminal-exit-cause.ts:42-45`: "A
reader handed an *optional* status by a host must default to this, never to `0`: `exitCode ?? 0`
mints a clean finish out of an absence of evidence."

**Regression tests** — `src/shared/terminal-exit-cause.test.ts`:

- `src/shared/terminal-exit-cause.test.ts:11` "reports a signalled death as a signal, not as the zero
  node-pty pairs with it"
- `src/shared/terminal-exit-cause.test.ts:18` "refuses to read a status the host cannot report"
- `src/shared/terminal-exit-cause.test.ts:29` "treats the stop paths' negative sentinel as absence of
  evidence"
- `src/shared/terminal-exit-cause.test.ts:53` "refuses to turn a bare zero into a clean finish"
- `src/shared/terminal-exit-cause.test.ts:59` "keeps a bare nonzero status, which nothing fabricates"
- `src/shared/terminal-exit-cause.test.ts:94` — `isDeliberateTerminalExit` asserts **only**
  `operator_close` is deliberate; `exited{0}`, `signaled{9}` and `unknown{stop_unverified}` are all
  `false` (`src/shared/terminal-exit-cause.test.ts:95-98`)
- `src/shared/terminal-exit-cause.test.ts:108` "rejects the synthetic loss sentinel"
  (`src/shared/terminal-exit-cause.test.ts:110`)

The normalized ten-state vocabulary this project uses, and its mapping onto the evidence above, is
**not** in this document: it is a contract, and it lives in
[`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md).

---

### Area 5 — Trust ordering: four arbitration surfaces, S1–S4

Orca names **six** signal classes explicitly, at `src/shared/agent-status-observation.ts:12-25`:

| Origin | Meaning (verbatim from source) |
| --- | --- |
| `hook` | "Provider hook event (loopback HTTP or relayed), run through a provider normalizer." |
| `osc` | "OSC 9999 structured payload parsed out of PTY bytes. Canonical payload, no provider normalizer." |
| `title` | "Inferred from a terminal title. **The weakest evidence Orca acts on.**" |
| `process` | "Derived from the pane's own process/output evidence." |
| `launch` | "Seeded when Orca launched the agent itself, before any provider signal." |
| `orchestration` | "Stamped by orchestration dispatch rather than by the agent." |

The header of `src/shared/agent-status-types.ts:1-3` states an aspiration — "status comes from hooks
(Claude, Codex, etc.) — never inferred from terminal titles" — but that sentence governs
`AgentStatusEntry`, the hook row type declared in that same file. It does **not** describe every
readiness decision in the runtime, and reading it as a global rule is a mistake this investigation
made once and corrected.

#### 5.1 `pty.lastAgentStatus` is title-derived evidence

`pty.lastAgentStatus` is **title-derived**; it is not a hook signal and not an OSC-9999 signal, and
it is therefore not a structured-evidence tier. This is a fact about the producer and it is
enumerable rather than inferred: every assignment to `lastAgentStatus` in `src/**` (excluding
comparisons and tests) is one of four, and all four are title readings or clear-downs.

| # | Producer | Value assigned | Origin |
| --- | --- | --- | --- |
| P1 | `src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:27, 39, 99` | `detectAgentStatusFromTitle(rawTitle)` | OSC terminal title on the PTY byte stream |
| P2 | `src/main/runtime/orca-runtime-maybe-hydrate-headless-from-renderer.ts:115, 133-135` | `detectAgentStatusFromTitle(title)` on a hydration seed | persisted terminal title replayed after a main-process restart |
| P3 | `src/main/runtime/orca-runtime-serialize-agent-prompt-submission.ts:94-104` | `tracker.restoreLastAgentExit()` | a previously title-detected status, restored only when a foreground-process read proves a recognized agent still owns the PTY |
| P4 | `src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:148, 151, 162` | `null` | clear-down on provider-generation change |

`detectAgentStatusFromTitle` is a memoized pure function of the title string
(`src/shared/agent-title-status.ts:270-271`) over the weak three-value vocabulary
`working | permission | idle` (`src/shared/agent-title-core.ts:15`).

Three consequences:

1. **No hook writes this field.** Hook rows travel a separate carrier —
   `getFreshExplicitAgentStatusForHandle`
   (`src/main/runtime/orca-runtime-serialize-agent-prompt-submission.ts:138-151`) reads a hook-row
   snapshot and never touches the title-derived `pty.lastAgentStatus`.
2. **OSC 9999 does not write it either.** The structured OSC payload is handled by per-PTY
   processors (`src/main/runtime/orca-runtime-fit-override-listeners.ts:99-107`,
   `src/main/runtime/orca-runtime-schedule-wait-blocked-check.ts:117-120`), which appear in the
   assignment enumeration only as deletions on teardown.
3. **Orca itself classifies the field as the title tier.** `getSnapshot`
   (`src/main/runtime/runtime-terminal-agent-status-query.ts:159-225`) returns a field literally named
   `titleStatus`, and when no live title record exists it falls back to the title-derived
   `pty.lastAgentStatus` (`src/main/runtime/runtime-terminal-agent-status-query.ts:196-198`) /
   `leaf.lastAgentStatus` (`src/main/runtime/runtime-terminal-agent-status-query.ts:222`) *as that
   title status*, with `titleStatusIsLive: false`.

The lifecycle map is the same story: `recordAgentPromptLifecycleState(ptyId, agentStatus)` is called
with the title-derived status at `src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:28`, so
`agentPromptLifecycleByPtyId` is title-derived too.

#### 5.2 Four arbitration surfaces with different orderings

**There is no single global trust order in Orca**, and the source says so directly:
`src/renderer/src/lib/pane-agent-evidence.ts:59-63` returns the layers separately "because consumers
combine them differently; a single merged status would silently change behavior."

**S1 — Renderer pane display arbitration.** `resolvePaneAgentActivity`
(`src/renderer/src/lib/pane-agent-evidence.ts:86-123`). Strict three-branch precedence:

1. Fresh hook row wins — `source:'hook'`, `confidence:'authoritative'`, `livePtyRequired:false`
   (`src/renderer/src/lib/pane-agent-evidence.ts:89-104`). Freshness gate at
   `src/renderer/src/lib/pane-agent-evidence.ts:18-30`, TTL
   `AGENT_STATUS_STALE_AFTER_MS = 30 min` (`src/shared/agent-status-freshness.ts:7`).
2. Otherwise the title, as an explicitly weaker fallback — `source:'title'`,
   `confidence:'fallback'`, `livePtyRequired: !input.hasLivePty`
   (`src/renderer/src/lib/pane-agent-evidence.ts:105-114`). The field comment
   (`src/renderer/src/lib/pane-agent-evidence.ts:75-76`): "True when the only claim is a title without
   live-PTY proof — **liveness-gated consumers must treat it as absent**."
3. Otherwise `source:'none'` (`src/renderer/src/lib/pane-agent-evidence.ts:115-122`).

**S2 — Main-process terminal agent status query.** `RuntimeTerminalAgentStatusQuery.readStatus`
(`src/main/runtime/runtime-terminal-agent-status-query.ts:64-129`), backing `terminal.getAgentStatus`.
It consumes hook rows but its order is **blocked-evidence-first, not hook-first**:

1. A live `permission` **title** wins outright
   (`src/main/runtime/runtime-terminal-agent-status-query.ts:85-87`) — a title beating a hook, in the
   safe direction only.
2. Blocked wait text ⇒ `permission`, subject to a recency contest between the hook row, the
   title-derived lifecycle row and `waitBlockedAt`
   (`src/main/runtime/runtime-terminal-agent-status-query.ts:69-83, 88-95`). `agent-approval-prompt`
   is unconditional.
3. Fresh hook row, but only if the title does not block it and the PTY's foreground process is not a
   plain shell (`src/main/runtime/runtime-terminal-agent-status-query.ts:96-108`) — "current
   shell/management evidence wins" (`src/main/runtime/runtime-terminal-agent-status-query.ts:97-98`).
4. Title status, resolved through identity/foreground evidence when the title is an OpenCode marker
   or a lone quarter-circle spinner
   (`src/main/runtime/runtime-terminal-agent-status-query.ts:109-125`).
5. Otherwise `status: null` with `isRunningAgent` from a process probe
   (`src/main/runtime/runtime-terminal-agent-status-query.ts:127-129`).

The same recency arbitration is duplicated for the blocked-reason answer in
`src/main/runtime/orca-runtime-resolve-authoritative-terminal-wait-permission.ts:20-51`.

**S3 — The `tui-idle` waiter.** `RuntimeTerminalWait.wait`
(`src/main/runtime/runtime-terminal-wait.ts:39-166`). **This surface has no hook tier at all.** Its
order, on both the live-PTY path (`src/main/runtime/runtime-terminal-wait.ts:47-131`) and the leaf
path (`src/main/runtime/runtime-terminal-wait.ts:132-166`):

1. **Blocked-reason detection first, and it wins over idle**
   (`src/main/runtime/runtime-terminal-wait.ts:58-61, 108-117, 136-140`). A pane sitting on an
   approval prompt is never reported idle.
2. A title-derived `lastAgentStatus === 'idle'` reading
   (`src/main/runtime/runtime-terminal-wait.ts:63-65, 118-119, 149-151`) — an OSC terminal-title
   reading, not structured evidence. The comment above the leaf branch
   (`src/main/runtime/runtime-terminal-wait.ts:141-148`) says exactly that: "This uses the same **OSC
   title detection** that powers the renderer's 'Task complete' notifications", and "only `'idle'`
   satisfies tui-idle, **not** `'permission'`."
3. An adopted/renderer-synced title reading, or a known-ready screen preview
   (`src/main/runtime/runtime-terminal-wait.ts:66-72, 120-125, 152-159`). Both are weak:
   `getAdoptedPtyExplicitIdleStatus` (`src/main/runtime/orca-runtime-resolve-exit-waiters.ts:89-92`)
   reads the renderer-synced tab/pane title — it exists because "the primary OSC-title signal can't
   fire for daemon-hosted terminals" (`src/main/runtime/orca-runtime-resolve-exit-waiters.ts:88`) —
   and `isKnownReadyPromptPreview` (`src/main/runtime/terminal-wait-detection.ts:33-44`) is screen
   text.
4. Otherwise register a waiter with a mandatory default timeout
   (`src/main/runtime/runtime-terminal-wait.ts:163-166`): "tui-idle depends on OSC title transitions
   from a recognized agent. If no agent is detected, the waiter would hang forever. Enforce a default
   timeout so unsupported CLIs **fail predictably** instead of silently blocking." On the PTY path a
   waiter with neither a status nor tail text additionally arms a visible read probe
   (`src/main/runtime/runtime-terminal-wait.ts:126-129`).

**Every accepting tier of S3 is a title or a screen reading.**

**S4 — Structured-TUI session stop adjudication.** `structuredTuiStatus`
(`src/main/runtime/orca-runtime-stop-structured-session-process.ts:71-92`). This surface *is*
hook-first, and it ranks the two signals against each other explicitly:

1. The hook row decides if present
   (`src/main/runtime/orca-runtime-stop-structured-session-process.ts:74-77`).
2. Otherwise, on a connected PTY: blocked ⇒ busy; a known-ready screen preview ⇒ idle
   (`src/main/runtime/orca-runtime-stop-structured-session-process.ts:78-83`).
3. Otherwise `hasStructuredTuiIdleEvidence`, whose `status` input is the title-derived
   `pty.lastAgentStatus` (`src/main/runtime/orca-runtime-stop-structured-session-process.ts:84-90`).
4. Not connected ⇒ busy (`src/main/runtime/orca-runtime-stop-structured-session-process.ts:92`).

The title-derived `pty.lastAgentStatus` is the **last** tier — below the hook row and below a
screen-text preview.

#### 5.3 What `hasStructuredTuiIdleEvidence` actually is

`src/main/runtime/structured-tui-idle-evidence.ts:3-9` returns
`!blocked && status === 'idle' && statusObservedLive`. Its only production caller is S4
(`src/main/runtime/orca-runtime-stop-structured-session-process.ts:84-90`); it is **not** used by the
`tui-idle` waiter, so it does not harden `terminal.wait`. "Structured" here does not mean "hook":
the `status` it receives is the title-derived `pty.lastAgentStatus`. What the predicate adds over a
bare title reading is the live-observation gate (`lastAgentStatusObservedLive`, set true only on a
live title frame at `src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:40` and forced false on
provider-generation reset at `src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:151`) plus the
blocked-prompt refusal.

Its colocated tests (`src/main/runtime/structured-tui-idle-evidence.test.ts`) assert three refusals:
`src/main/runtime/structured-tui-idle-evidence.test.ts:5` "does not treat a ready prompt preview as proof that a turn is idle"; `src/main/runtime/structured-tui-idle-evidence.test.ts:13` "requires an
explicit idle state and still rejects blocked prompts"; `src/main/runtime/structured-tui-idle-evidence.test.ts:22` "does not authorize a restored idle
status before live observation".

#### 5.4 What this means for the hard constraint

- **`terminal.wait --condition tui-idle` is a readiness gate, not completion evidence.** Every tier
  that resolves it is a title or a screen reading (§5.2, S3). Orca itself never uses it as completion
  proof: completion runs through `worker_done` settlement, authority-checked against pane identity and
  Dispatch id (Area 6), and the structured launch path uses `tui-idle` only as step 5 of 7 — after
  process-identity binding and before a provider-side transcript proof
  (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:79-95`).
- **Where Orca does rank signals, the rank is surface-specific.** S1 and S4 put a fresh hook above a
  title; S2 lets a live `permission` title and blocked screen text override a hook in the
  conservative direction and gates a hook behind foreground-process evidence; S3 has no hook input at
  all. A standalone adapter therefore has to reproduce **four decisions**, not one precedence list.
- **The portable rule is the refusal set, not a ranking.** The refusals are stated normatively in
  [`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md).

#### 5.5 Cross-cutting refusals

- **`snapshot` is never proof of a new turn.** `AgentStatusObservationKind = 'transition' |
  'snapshot' | 'identity-only'`, with "`snapshot` — a repaint/replay of the state already believed
  true… **Never proof of a new turn**" (`src/shared/agent-status-observation.ts:28-37`).
- **A restored, unconfirmed row is never fresh.** `restoredUnconfirmed`
  (`src/shared/agent-status-types.ts:170-173`), enforced identically in
  `src/shared/agent-status-freshness.ts:45-51` and
  `src/renderer/src/lib/pane-agent-evidence.ts:26-29`: "an unconfirmed hydrated row may describe a
  turn that ended while no receiver was up; never fresh."
- **Cross-authority order is *incomparable*, not *older*.**
  `src/shared/agent-status-observation.ts:85-90`: "`(authorityId, incarnation, revision)` is a total
  order ONLY within one `authorityId`. A different `authorityId` means 'incomparable', not 'older'…
  Consumers must fall back to today's timestamp rule across authorities, never mix the two orders."
- **The decay rule.** A mirrored remote row decays against the replica's own receipt clock, not the
  host's, because "a host running minutes fast made every remote row look permanently fresh"
  (`src/shared/agent-status-observation.ts:92-110`; `src/shared/agent-status-freshness.ts:19-29`).
- **The title detector is deliberately narrow**, not a general natural-language matcher:
  `detectExplicitIdleStatusFromTitle` (`src/main/runtime/terminal-wait-detection.ts:13-31`) requires
  the title classifier to already say `idle` **and** then one of
  `/(^|\s)(ready|idle|done)(\s|$|[.!?])/i`, an OpenCode native title, or a small set of per-agent
  glyph prefixes — "launch titles like 'Codex YOLO' contain an agent name but aren't readiness
  signals; `terminal.wait` needs explicit idle evidence." This stricter detector guards tier 3 of S3
  but **not** tier 2.
- **A title that stops classifying clears the status.** The same title channel that sets
  the title-derived `pty.lastAgentStatus` also clears it
  (`src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:91-99`): "when a new OSC title doesn't
  classify as an agent state (e.g. bare shell title after the agent exits), clear `lastAgentStatus`
  so it is no longer sticky."

#### 5.6 Regression tests read for this area

- `src/renderer/src/lib/pane-agent-evidence.test.ts` pins **S1** only:
  `src/renderer/src/lib/pane-agent-evidence.test.ts:78` "reports a fresh hook row as the authoritative source and keeps the title layer visible";
  `src/renderer/src/lib/pane-agent-evidence.test.ts:95` "treats a stale hook row as absent and falls back to the title";
  `src/renderer/src/lib/pane-agent-evidence.test.ts:109` "flags title-only evidence without a live PTY so liveness-gated consumers drop it";
  `src/renderer/src/lib/pane-agent-evidence.test.ts:120` "reports none when there is no fresh hook and the title carries no status";
  `src/renderer/src/lib/pane-agent-evidence.test.ts:137` "passes hook state through raw, including done";
  `src/renderer/src/lib/pane-agent-evidence.test.ts:30` / `src/renderer/src/lib/pane-agent-evidence.test.ts:40` — the staleness boundary is inclusive at exactly `AGENT_STATUS_STALE_AFTER_MS`.
- `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts` pins **S2**:
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:162` "reports permission from blocked terminal wait text";
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:203` "keeps blocked prompt text authoritative over an OpenCode marker";
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:254` "reports permission from blocked wait text over title-only working state";
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:295` "lets a live non-permission title supersede stale blocked wait text";
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:337` "maps fresh explicit waiting hook state to permission over a working title";
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:392` "does not treat a restored-unconfirmed hook row as live terminal status";
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:448` "does not let stale wait text override a fresh explicit working state";
  `src/main/runtime/orca-runtime-tests/agent-status-and-waits.spec.ts:504` "reports permission when blocked wait text is newer than explicit working state".
- `src/main/runtime/orca-runtime-tests/terminal-handles-and-agent-status-part-05.spec.ts:152-176` is
  the decisive test for §5.1 — "publishes hook-only identity for a pane that never emitted an agent
  title", whose own comment states that with no launch hint and no recognized OSC title the
  title-derived `pty.lastAgentStatus` stays unset while the hook row still carries the full identity.
  A hook and that field are independent channels, not two names for one signal.
- `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts` pins **S3**'s producer:
  `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:79` "resolves tui-idle when a completion title is coalesced with the next working title";
  `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:282` "clears a stale working title after 3s of title-less output"; plus bare-title identity
  refusals at `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:99`, `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:126`, `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:158`, `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:189`, `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:226`, `src/main/runtime/orca-runtime-tests/pty-title-status.spec.ts:256`.
- `src/main/runtime/structured-tui-idle-evidence.test.ts` pins **S4**'s predicate (§5.3).

---

### Area 6 — Session identity, reuse, settlement, release, cancellation

**Settlement — what `worker_done` actually requires. Two independent gates.**

*Gate 1, runtime-side authority* — `src/main/runtime/orchestration/lifecycle-reconciliation.ts:174-305`.
A `worker_done` is rejected unless **every** one of these holds: the payload is a JSON object
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:193-201`) → `taskId` is a non-empty
string (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:203-206`) → `dispatchId` is a non-empty string (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:208-217`) → `outcome ∈ {succeeded,
failed}` (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:219-228`) → the task exists (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:230-239`) → **the dispatch exists** (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:243-252`) →
`dispatch.task_id === taskId` (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:253-261`) → **the sender holds lifecycle authority** (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:262-267`) →
`db.settleWorkerReport` accepts (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:288-296`).

`hasLifecycleAuthority` (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:16-28`) is
**pane-key based, not handle-based**, whenever a pane key exists; handle equality is used only for
legacy rows created before pane identity existed — "payload knowledge alone is not authority"
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:25-27`). `isSamePane`
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:7-14`) tolerates a changed tab half
(pane break-out) but requires the same leaf UUID.

The rejection vocabulary is closed
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:41-52`): `sender_not_assignee |
dispatch_capability_invalid | invalid_payload | missing_task_id | missing_dispatch_id |
invalid_outcome | unknown_task | unknown_dispatch | task_dispatch_mismatch | inactive_dispatch |
stale_dispatch`.

Why `dispatchId` is mandatory, verbatim
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:241-242`): "taskId alone is not a
completion authority; retried tasks can have stale `worker_done` messages racing the current active
dispatch."

*Gate 2, CLI-side settlement proof* — `src/cli/handlers/orchestration-worker-settlement.ts:4-45`.
After the runtime accepts the send, the CLI **re-reads** the dispatch and the task list and requires
all four of: the dispatch id matches, the dispatch status matches the expected terminal status, the
task status matches, and the task's stored result satisfies `isExactWorkerReport`
(`src/cli/handlers/orchestration-worker-settlement.ts:27-35`).

That fourth clause is **not** an exact-message equality, and reading it as one is the single easiest
mistake to make here. It parses the stored result as JSON and evaluates a **conjunction of two
required fields with a disjunction of two identity paths**
(`src/cli/handlers/orchestration-worker-settlement.ts:75-94`):

| Clause | Predicate | Kind |
| --- | --- | --- |
| provenance | `parsed.provenance === 'worker_report'` | **required** |
| outcome | `parsed.outcome === outcome` | **required** |
| identity (a) — *this exact message* | `parsed.messageId === receipt.messageId` | one of two; either suffices |
| identity (b) — *accepted idempotent retry* | `receipt.fromHandle !== undefined && parsed.reportedBy === receipt.fromHandle` | one of two; either suffices |

Path (b) is intentional and regression-tested. The colocated test
`src/cli/handlers/orchestration-lifecycle-rejection.test.ts:248-288` is named "accepts an idempotent
retry whose first report already settled": it supplies the current receipt as `msg_retry` while the
stored report holds `msg_first`, and the send **succeeds**, because both name the reporting handle
`term_worker` and the outcome `succeeded`. A different message id is therefore deliberately accepted
once the Task and Dispatch have already been verified.

Note the naming trap, because it is why this survived earlier review: the function is *called*
`isExactWorkerReport` and the refusal it guards speaks of *the exact report*, but the body implements
neither. A symbol-presence check confirms the identifier is on the cited lines and cannot evaluate
the predicate underneath it.

Anything that fails **any** required clause, or matches **neither** identity path, throws
`operation_unknown` (`src/cli/handlers/orchestration-worker-settlement.ts:40-45`):

> "The runtime accepted worker_done but did not confirm that the exact report settled its Task and
> Dispatch. Retry from the assigned worker after verifying its active Dispatch."

The fail-closed half is equally load-bearing and is separately regression-tested:
`src/cli/handlers/orchestration-lifecycle-rejection.test.ts:315-354` ("rejects a terminal Dispatch
with the wrong identity") supplies a stored report whose `messageId` *does* match the current
receipt and still rejects with `operation_unknown`, because the dispatch id does not match. So the
contract is: **accepted ≠ settled**, and the retry tolerance is scoped to the same reporter and the
same outcome on an already-verified Task and Dispatch — it is not a general amnesty.

**Heartbeats are authority-checked the same way and are not liveness on their own**
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:124-172`). A heartbeat for a dispatch
that is not `status === 'dispatched'` is *suppressed* — marked read, retained for audit — rather than
surfaced (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:150-157`); a wrong-pane heartbeat is *rejected*, because "a wrong-pane heartbeat must not
refresh liveness — it would mask a hung assignee behind another agent's timer" (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:159-166`). After
settlement, earlier same-dispatch heartbeats are suppressed
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:331-347`).

**Supervised vs. unsupervised dispatch — two creation paths with different ownership.**

| | `orca orchestration worker-start` | `orca orchestration dispatch` |
| --- | --- | --- |
| Creates | supervised worker resource + Dispatch | Dispatch context only ("context-only") |
| Terminal ownership | Orca-owned (`ownership_state='owned'`) | adopted, **not owned** |
| Reported worker state | a `WorkerDispatchState` | literal `'unsupervised'` |
| `worker-stop` | stops the agent terminal | "fenced **without closing** its unsupervised terminal process" |
| `worker-release` | closes the exact owned terminal | "no owned terminal resource … reported retained without process action" |

Sources: `src/cli/specs/orchestration-worker-specs.ts:5-40`, `src/cli/specs/orchestration-worker-specs.ts:48`, `src/cli/specs/orchestration-worker-specs.ts:60`, `src/cli/specs/orchestration-worker-specs.ts:65-73`, `src/cli/specs/orchestration-worker-specs.ts:84-96`,
`src/cli/specs/orchestration-worker-specs.ts:110-118`; `src/main/runtime/orchestration/worker-terminal-ownership.ts:61`
(`WorkerDispatchListState = WorkerDispatchState | 'unsupervised'`);
`src/main/runtime/orchestration/context-only-dispatch-release.ts:21` ("The assignment was abandoned;
its **unsupervised terminal process was retained**").

`releaseContextOnlyDispatch` (`src/main/runtime/orchestration/context-only-dispatch-release.ts:25-58`)
is a pure DB transition — it sets `status='failed'`, revokes the capability, and blocks the task if
no other dispatch is live. **It performs no process action at all.**

**Session identity is a durable, leased, single-writer record.**
`AGENT_SESSION_LEASE_TTL_MS = 30_000` and `AGENT_SESSION_LEASE_RENEW_INTERVAL_MS = 10_000`
(`src/main/runtime/agent-session-record-store.ts:70-71`); retired claim keys stay verifiable for 30
days "so a rotation cannot strand a running agent"
(`src/main/runtime/agent-session-record-store.ts:72-73`). On open, **every persisted lease is marked
unreconciled** (`src/main/runtime/agent-session-record-store.ts:80-84`): "a restart grants no writer
on the strength of what the previous process wrote."

**Cancellation.** `src/shared/agent-interrupt-intent.ts:3-19`: `AgentInterruptInputIntent =
'plain-escape' | 'ctrl-c'`, `AGENT_INTERRUPT_SETTLE_MS = 500`, and an inference request carrying a
full baseline (`updatedAt`, `stateStartedAt`, `prompt`, `agentType`) so a synthesized `done` can only
be attributed to *this* turn.

---

### Area 7 — Graceful interrupt → bounded wait → forced termination

**Implementation:** `src/main/providers/local-pty-termination.ts`.

Real constants (`src/main/providers/local-pty-termination.ts:26-28`):

```text
LOCAL_PTY_PHYSICAL_EXIT_TIMEOUT_MS  = 8_000   // hard wait for proven physical exit
LOCAL_PTY_GRACEFUL_FORCE_TIMEOUT_MS = 5_000   // SIGTERM -> SIGKILL deadline
LOCAL_PTY_FORCE_KILL_RETRY_MS       =   250   // retry after a failed force attempt
```

1. **Graceful** — `proc.kill('SIGTERM')` on POSIX
   (`src/main/providers/local-pty-termination.ts:44-54`).
2. **Bounded wait** — `armLocalPtyForceKill` sets a 5 s timer
   (`src/main/providers/local-pty-termination.ts:56-87`), re-checking ownership and mode at fire time
   (`src/main/providers/local-pty-termination.ts:67-70`) so a natural exit or an ownership change cancels the escalation. A *failed* force
   attempt reverts the mode and re-arms with one fewer attempt — "a transient native rejection must
   not consume the only SIGKILL owner while shutdown still awaits physical exit"
   (`src/main/providers/local-pty-termination.ts:76-82`).
3. **Force** — `forceKillPosixPtyProcessGroups(proc.pid, () => proc.kill('SIGKILL'))`
   (`src/main/providers/local-pty-termination.ts:53`), group-scoped where provable and root-scoped
   otherwise (Area 1).
4. **Proof of death** — `waitForPtyPhysicalExit`
   (`src/main/providers/local-pty-termination.ts:34-42`) awaits a physical-exit tracker for up to 8 s
   and **rejects** if the OS never confirms. Shutdown does not report success on an unconfirmed exit
   (`src/main/providers/local-pty-termination.ts:198`).

**Windows is treated as a different machine, not a variant.**
`src/main/providers/local-pty-termination.ts:140-141`: "ConPTY has no graceful signal — its first
bare kill closes the pseudoconsole, so treat it as a final force request." Hence
`requestedMode = immediate || win32 ? 'force' : 'graceful'`
(`src/main/providers/local-pty-termination.ts:141`).

**What Orca refuses to kill — an identity rule, not a heuristic.**

- Tree kill runs only through `killWithDescendantSweep`
  (`src/main/providers/local-pty-termination.ts:182-194`), and the comment at
  `src/main/providers/local-pty-termination.ts:179-181` is explicit: "Windows tree-kills **only when
  the identity probe returns `own`** so agent/MCP orphans cannot hold the worktree cwd (#10004).
  **`unknown`/`foreign`/`absent` skip taskkill** and rely on root close alone."
- The probe vocabulary is `WindowsTreeKillTarget = 'own' | 'absent' | 'foreign' | 'unknown'`
  (`src/main/windows-pty-root-identity.ts:14`), and `verifyWindowsTreeKillTarget` returns `'unknown'`
  on any unreadable link (`src/main/windows-pty-root-identity.ts:72, 84, 138, 145`) — never `'own'`.
- POSIX identity safety uses a pre-kill descendant snapshot with `ps -axo pid=,ppid=,pgid=,lstart=`
  under `LANG=C LC_ALL=C` (`src/main/pty-descendant-termination.ts:60-87`), and the snapshot's
  `capturedAtMs` is stamped **before** `ps` starts, because "stamping the result later could make a
  capture-second PID look safe after a rollover" (`src/main/pty-descendant-termination.ts:63-65`). A
  delayed SIGKILL additionally requires "an unambiguous capture-second boundary and matching pgid"
  (`src/main/pty-descendant-termination.ts:19-21`). Grace window: `DESCENDANT_KILL_GRACE_MS = 2_000`
  (`src/main/pty-descendant-termination.ts:9`).
- `createProcessTableSnapshotReader` (`src/main/pty-descendant-termination.ts:91-121`) coalesces
  same-turn bursts but **never serves a completed or already-started scan to a later request,
  "because stale PIDs are unsafe to signal"** (`src/main/pty-descendant-termination.ts:89-90`).
- `destroyPtyProcess` (`src/main/providers/local-pty-termination.ts:92-105`) neutralises `proc.kill`
  before `destroy()` on POSIX, "whose close-listener SIGHUPs a possibly-recycled POSIX pid".
- Every group actually signalled is recorded to a self-initiated-tree-kill breadcrumb ledger
  (`src/main/pty/posix-pty-process-groups.ts:130-134`), and only for groups that did **not** return
  ESRCH.

CLI-level statements of the same rule: `worker-stop` — "Never deletes the worktree, setup terminal,
configured tabs, or unrelated processes" (`src/cli/specs/orchestration-worker-specs.ts:72`);
`worker-release` — "Never closes setup terminals, configured tabs, reused or pre-existing terminals,
user-taken-over terminals, or **unproven identities**"
(`src/cli/specs/orchestration-worker-specs.ts:93`); `worker-abandon` — "Retains all possibly-live
resources and **performs no process or filesystem action**"
(`src/cli/specs/orchestration-worker-specs.ts:81`).

**There is no `worker-interrupt` verb in 1.4.197.**
`src/cli/specs/orchestration-worker-specs.ts` defines exactly eight `worker-*` verbs — `worker-start
| worker-show | worker-read | worker-stop | worker-abandon | worker-release | worker-retain |
worker-list` (`src/cli/specs/orchestration-worker-specs.ts:3-118`) — and `rg -n "worker-interrupt"
src/cli src/main` at the pinned commit returns zero hits. The nearest real primitives are
*settlement* operations with different semantics. The consequence for this project's own code is
recorded in [Capability decision table](#capability-decision-table) and scheduled in
[`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md).

---

### Area 8 — Rediscovery after crash/restart

**What survives a restart (durable):**

| Artefact | Path |
| --- | --- |
| Agent-session records + operation ledger (on disk, protected) | `src/main/runtime/agent-session-record-store.ts:70-90`, `src/main/runtime/agent-session-record-store-file.ts`, `src/main/runtime/agent-session-record-store-security.ts` |
| Orchestration Run/Task/Dispatch/message/worker-terminal rows (SQLite) | `src/main/runtime/orchestration/db.ts` |
| `process_incarnation`, `host_scope`, `pane_key` on the worker terminal resource | `src/main/runtime/orchestration/worker-terminal-ownership.ts:30-50` |
| Persisted last-status rows | `src/shared/agent-status-types.ts:168-173` |

**What explicitly does not survive:**

- **Write authority.** Every lease is marked unreconciled on load
  (`src/main/runtime/agent-session-record-store.ts:80-84`).
- **Freshness of a restored non-`done` status.** `restoredUnconfirmed` makes such a row stale
  immediately (`src/shared/agent-status-types.ts:170-173`).
- **A delivered `worker_done`.** It is delivered once, to the message stream of the process that owns
  the run.

**Rediscovery is probe-driven adjudication, not assumption.**

- `collectAgentSessionRestartProbes`
  (`src/main/runtime/agent-session-restart-reconciliation.ts:18-36`) — when a batch probe returns no
  result for a record, the outcome is `{outcome:'indeterminate', reason:'owner batch probe returned
  no result'}`, **never** "gone".
- `AgentSessionOwnerProbe` (`src/shared/agent-session-lease-adjudication.ts:21-33`) is six-valued:
  `exit-observed | pid-absent | identity-mismatch{field} | identity-matched{matchedOn[]} |
  reservation-unused | indeterminate{reason}`. Only `identity-matched` with a non-empty `matchedOn`
  proves ownership (`src/shared/agent-session-lease-adjudication.ts:75`); only the first three prove
  absence (`src/shared/agent-session-lease-adjudication.ts:64-66`); `indeterminate` proves nothing
  (`src/shared/agent-session-lease-adjudication.ts:252`).
- `applyAgentSessionRestartProbes`
  (`src/main/runtime/agent-session-restart-reconciliation.ts:37-57`) mutates
  only records that are **both** unreconciled **and** whose reconciliation target still matches.
- Terminal-resource rediscovery — `reconcileRequestedWorkerTerminalReleases`
  (`src/main/runtime/orchestration/worker-terminal-release-reconciliation.ts:22-97`). The header
  comment (`src/main/runtime/orchestration/worker-terminal-release-reconciliation.ts:19-21`) is the
  contract: "Finishes **ONLY previously requested** releases after startup/reconnect terminal
  discovery. It **never invents release intent**: resources outside requested/releasing are untouched,
  and unresolved identity **defers** (`release_pending`) rather than settling or broadening the
  close." Outcome counters are `released | pending | unknown | retained`
  (`src/main/runtime/orchestration/worker-terminal-release-reconciliation.ts:4-10`).
- Process liveness after restart — `classifyWorkerTerminalProcessIncarnation`
  (`src/main/runtime/orchestration/worker-terminal-process-liveness.ts:39-62`) matches
  `${session.id}:${incarnationId}` and returns **`'unverifiable'`**, not `'exited'`, when a candidate
  session exists but its incarnation id is missing or untrimmed
  (`src/main/runtime/orchestration/worker-terminal-process-liveness.ts:57-61`).
- Env-token scan — `scanAgentSessionSpawnTokenProcesses`
  (`src/main/runtime/agent-session-spawn-token-process-scan.ts:22-52`) reads `/proc/<pid>/environ` on
  Linux only. The module header
  (`src/main/runtime/agent-session-spawn-token-process-scan.ts:1-9`) states the rule verbatim: "macOS
  and Windows answer `null`, meaning '**this host cannot enumerate**', NEVER 'no process carries it':
  reporting an empty result there would free a reservation whose child is alive and hand a second
  writer to the same provider session." The result is typed
  `{status:'unverifiable', processes:null, platform}` vs `{status:'verified', processes}`
  (`src/main/runtime/agent-session-spawn-token-process-scan.ts:16-19`) and is marked "**Diagnostic
  evidence only** … callers must never use this Linux read-back as ownership or orphan-reaping proof"
  (`src/main/runtime/agent-session-spawn-token-process-scan.ts:54-58`).
- Session-store transaction machinery:
  `src/main/runtime/agent-session-store-transaction-queue.ts`,
  `src/main/runtime/agent-session-store-transaction-lock.ts`,
  `src/main/runtime/agent-session-backup-recovery-fence.ts`,
  `src/main/runtime/agent-session-orphan-child-reaper.ts`.

---

### Area 9 — Run / repository / worktree / agent / task / dispatch ownership

**The row that carries ownership** — `WorkerTerminalResourceRow`
(`src/main/runtime/orchestration/worker-terminal-ownership.ts:30-50`) has separate columns for
`origin_dispatch_id`, `owner_dispatch_id`, `prior_owner_dispatch_ids`, `worktree_id`,
`terminal_handle`, `pane_key`, `process_incarnation`, `host_scope`, `ownership_state`,
`release_state`, `retained_reason`, `archive_status`. Ownership is transferable and its history is
retained.

**Three orthogonal vocabularies, deliberately kept apart**
(`src/main/runtime/orchestration/worker-terminal-ownership.ts:3-28`):

- `WorkerTerminalOwnershipState = owned | transferred | user_owned | external | released`
- `WorkerTerminalReleaseState = not_requested | retained | requested | releasing | released | unknown`
- `WorkerTerminalRetainedReason = external_terminal | ownership_transferred | user_takeover |
  user_requested | no_owned_resource | identity_unproven | legacy_ambiguous | federation_unsupported`

**Terminal accounting is explicitly not task outcome.**
`src/main/runtime/orchestration/worker-terminal-ownership.ts:52` — "Terminal state exposed by
worker-list; **process accounting, never Task/Dispatch outcome**";
`src/main/runtime/orchestration/worker-terminal-ownership.ts:80` — "Process accounting for
worker-list; **deliberately independent of Task/Dispatch outcome**". The CLI repeats it
(`src/cli/specs/orchestration-worker-specs.ts:116`): "Terminal state is process accounting and is
reported separately from Task status; **a completed Task can still own a live terminal**."

`deriveWorkerTerminalListState`
(`src/main/runtime/orchestration/worker-terminal-ownership.ts:81-111`) is the decision function, and
its ordering is itself the contract: `released` → `release_unknown` → `release_pending` → `retained`
(if not owned, or explicitly retained) → `reclaimable` (only for `succeeded`/`failed` **and** not
unsupervised) → `retained` (settled) → `active`.

**Repository / host scoping** — `WorkerTerminalHostScope`
(`src/main/runtime/orchestration/worker-terminal-process-liveness.ts:3-37`) is a closed tagged union
`local | wsl{distro} | ssh{targetId}`, and `parseWorkerTerminalHostScope` returns `null` for anything
that does not match exactly (`src/main/runtime/orchestration/worker-terminal-process-liveness.ts:36`)
— including malformed JSON
(`src/main/runtime/orchestration/worker-terminal-process-liveness.ts:12-17`), an empty distro or an
empty target id. Liveness questions are therefore never asked cross-host by accident.

**Worktree scoping at launch** — `worker-start` takes `--worktree
<current|selector|new-child|new-top-level>`, `--repo <selector>`, `--base-branch`, `--on
<saved-environment>` (`src/cli/specs/orchestration-worker-specs.ts:8`), with hard rules in the notes:
creation flags "are rejected for current/existing worktrees"
(`src/cli/specs/orchestration-worker-specs.ts:35`); "`--on` selects only the worker server; the Run
and this command remain on the current Orca server"
(`src/cli/specs/orchestration-worker-specs.ts:36`); "Remote current and new-child are invalid"
(`src/cli/specs/orchestration-worker-specs.ts:37`); "`--retry-of` links the replacement attempt but
**does not inherit placement**" (`src/cli/specs/orchestration-worker-specs.ts:38`).

**Exit-code semantics of `worker-start`** (`src/cli/specs/orchestration-worker-specs.ts:39`): "The
call exits 0 **only for ready**. Failed or **`outcome_unknown` exits 1** and JSON includes
stage/failedStage, setup, effects, residualResources, and recovery commands when needed." An unknown
start is a failure exit, not a success.

**Regression tests** — `src/main/runtime/orchestration/lifecycle-reconciliation.test.ts` is the
ownership/authority suite:
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:11` "rejects handle churn when neither side has stable pane identity";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:38` "completes worker_done from the dispatched pane after a handle remint";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:143` "completes worker_done from the same leaf after a pane break-out changed the tab half";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:162` "rejects mismatched opaque pane keys instead of treating them as legacy";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:179` "rejects worker_done from a foreign pane that claims the assignee handle";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:222` "does not let a caller-supplied rejection marker turn completion into success";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:251` "rejects a coordinator completion for a pane-bound dispatch";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:307` "does not release a dependent when a foreign completion wins the arrival race";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:344` "does not let a foreign replay overwrite an authorized completion";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:88` "replays an identical terminal outcome without mutating settled state";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:402` / `src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:436` "surfaces a heartbeat sent from a different pane without recording liveness";
`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:476` "suppresses same-dispatch heartbeats once worker_done is reconciled".

Related suites: `src/main/runtime/orchestration/db-task-dispatch-invariant.test.ts`,
`src/main/runtime/orchestration/db-task-dispatch-races.test.ts`,
`src/main/runtime/orchestration/db-task-dispatch-lifecycle-guards.test.ts`,
`src/main/runtime/orchestration/dispatch-failure-idempotency.test.ts`,
`src/main/runtime/orchestration/db-heartbeat-straggler-guard.test.ts`,
`src/main/runtime/orchestration-message-delivery-identity.test.ts`.

---

### Area 10 — OS-31 and OS-43 attachment points on the Orca side

*(The orca-skills side — which ports a Supervisor actually plugs into, and what does and does not
work today — is normative and lives in
[`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md).)*

Orca exposes the following as the observation/action surface a supervisor would read:

| Need | Orca surface | Path |
| --- | --- | --- |
| Enumerate dispatches of a run | `task-list --run`, `dispatch-show` | `src/cli/specs/orchestration.ts` |
| Inspect one supervised worker | `worker-show --dispatch` (incl. `observation.agentWait`) | `src/cli/specs/orchestration-worker-specs.ts:43-52` |
| Read bounded worker output | `worker-read --dispatch [--source auto\|transcript\|terminal] [--cursor]` | `src/cli/specs/orchestration-worker-specs.ts:53-63` |
| Terminal resource accounting | `worker-list [--terminal-state …]` | `src/cli/specs/orchestration-worker-specs.ts:110-118` |
| Fence without claiming a stop | `worker-abandon` | `src/cli/specs/orchestration-worker-specs.ts:76-82` |
| Stop the agent terminal | `worker-stop` | `src/cli/specs/orchestration-worker-specs.ts:65-75` |
| Release / retain a settled terminal | `worker-release` / `worker-retain` | `src/cli/specs/orchestration-worker-specs.ts:84-108` |
| Idempotency for mutations | `--retry-request <id>`; `request-show` | `src/cli/specs/orchestration-worker-specs.ts:72, 80, 88, 102` |
| Human decision gates | `gate-create` / `gate-resolve` / `gate-list` | `src/cli/specs/orchestration.ts` |
| Blocking question | `ask` | `src/cli/specs/orchestration.ts` |

Two Orca-side properties a supervisor design must honour:

- **`worker-release` is idempotent and its exit code is meaningful**: "repeating the call reports
  `already_released`. **Only `release_unknown` exits 1**; retained, release_pending, and
  already_released exit 0" (`src/cli/specs/orchestration-worker-specs.ts:94`).
- **`worker-read` survives release**: "An inspectable output archive is preserved before the terminal
  closes, so `worker-read` still returns output afterwards"
  (`src/cli/specs/orchestration-worker-specs.ts:92`; implementation
  `src/main/runtime/orchestration/worker-output-archive.ts`).

---

### Area 11 — Coupling with Electron / renderer / mobile / relay (the measurement)

**Measured, not inferred** (`rg -l "from 'electron'" <dir> --type ts`, counting files):

| Directory | Files importing `electron` |
| --- | --- |
| `src/main` (whole) | **485** |
| `src/main/providers` (PTY provider) | **0** |
| `src/main/runtime/orchestration` | **0** |
| `src/main/runtime` (whole) | 13 (all browser / desktop-surface / emulator / mobile-session modules) |
| `src/shared` | **0** |
| `src/cli` | **0** |
| `src/relay` | **0** |
| `src/renderer` | 1 |

Every module cited in Areas 1–9 has **zero** direct Electron imports:
`src/main/providers/local-pty-spawn.ts`, `src/main/providers/local-pty-termination.ts`,
`src/main/pty/posix-pty-process-groups.ts`,
`src/main/runtime/agent-prompt-submission-verification.ts`,
`src/main/runtime/runtime-terminal-wait.ts`,
`src/main/runtime/orchestration/lifecycle-reconciliation.ts`,
`src/main/runtime/orchestration/db.ts`, `src/shared/agent-status-types.ts`,
`src/shared/agent-prompt-injection.ts`, `src/shared/terminal-exit-cause.ts`.

**Orca enforces this as a build gate, and the baseline is empty.**
`config/scripts/check-runtime-electron-ratchet.mjs` bundles three entry points with esbuild and reads
the metafile for *transitive* Electron importers
(`config/scripts/check-runtime-electron-ratchet.mjs:34-42`). The script header
(`config/scripts/check-runtime-electron-ratchet.mjs:6-18`) states the intent: the runtime "is meant to
become host-agnostic so it can also run on plain Node", and "This is a **reachability check, not a
lint rule**."

`config/runtime-electron-baseline.txt` contains **no entries**, only a header stating that the list
"is EMPTY and must stay that way: the runtime boots on plain Node"
(`config/runtime-electron-baseline.txt:1-5`), and that any entry means the
runtime got less portable and the module should move behind a host port.

The host-port directory `src/main/host/` holds exactly the Electron-specific adapters
(`src/main/host/electron-app-environment.ts`, `src/main/host/electron-browser-commands.ts`,
`src/main/host/electron-http-client.ts`, `src/main/host/electron-runtime-desktop-surface.ts`,
`src/main/host/electron-secret-store.ts`, `src/main/host/electron-speech-services.ts`) — Electron is
already the plug-in, not the substrate.

`src/main/orcad/` is a Node daemon (`src/main/orcad/main.ts`, `src/main/orcad/orcad-entry.ts`,
`src/main/orcad/orcad-health.ts`, `src/main/orcad/orcad-instance-lock.ts`,
`src/main/orcad/orcad-daemon-supervision.ts`, `src/main/orcad/node-pty-precondition.ts`,
`src/main/orcad/node-pty-prebuilt-slot.ts`, `src/main/orcad/native-host-abi.ts`), built by
`pnpm run build:orcad` and smoke-tested by `pnpm run smoke:orcad-terminal`
(`package.json:34, 36`). It is cited here **as evidence of module-graph portability only**; rebuilding
it is a prohibited goal for this project, see
[Excluded Orca-specific layers](#excluded-orca-specific-layers).

**Standalone-usable in principle (no Electron, no renderer, no IPC):** `src/shared/**`,
`src/main/pty/**`, `src/main/providers/**`, `src/main/runtime/orchestration/**`,
`src/main/pty-descendant-termination.ts`, `src/cli/**`, `src/relay/**`.

**Consolidated coupling table** (the decision-relevant summary):

| Primitive | Standalone-usable? | Blocking coupling |
| --- | --- | --- |
| PTY spawn (`src/main/providers/local-pty-spawn.ts`) | Yes (0 Electron) | native `node-pty` build only |
| POSIX group discovery / kill (`src/main/pty/posix-pty-process-groups.ts`) | Yes | `ps` availability; POSIX only |
| Descendant sweep (`src/main/pty-descendant-termination.ts`) | Yes | `ps`; the Windows path needs a process-tree kill |
| Prompt injection framing (`src/shared/agent-prompt-injection.ts`) | Yes — pure functions | none |
| Prompt delivery verification (`src/main/runtime/agent-prompt-submission-verification.ts`) | Yes — pure, dependency-injected | needs an activity source |
| Exit-cause resolution (`src/shared/terminal-exit-cause.ts`) | Yes — pure | none |
| Status types / observation / freshness (`src/shared/agent-status-observation.ts`) | Yes — pure types + one sequencer | none |
| Trust arbitration S1 — renderer pane (`src/renderer/src/lib/pane-agent-evidence.ts`) | **Concept only** | lives in `src/renderer/` |
| Trust arbitration S2 (`src/main/runtime/runtime-terminal-agent-status-query.ts`) | Yes (0 Electron) | bound to leaf/PTY record shapes and a hook-row snapshot source |
| Trust arbitration S4 (`src/main/runtime/orca-runtime-stop-structured-session-process.ts`) | Concept only in practice | mixed into the runtime service class chain |
| `tui-idle` waiter (`src/main/runtime/runtime-terminal-wait.ts`) | Mostly — dependency-injected. Accepts only title/screen evidence, so it is a readiness gate | bound to leaf/PTY record shapes and the waiter registry |
| Lifecycle reconciliation (`src/main/runtime/orchestration/lifecycle-reconciliation.ts`) | Yes (0 Electron) | bound to the SQLite orchestration schema |
| Worker terminal ownership / liveness (`src/main/runtime/orchestration/worker-terminal-ownership.ts`) | Yes — pure | schema-shaped row types |
| Federation / relay / mobile / desktop surface | **No** | Orca-specific by design |

---

### Area 12 — License, attribution, dependencies, provenance

**Orca's licence (fact).** `LICENSE` is the verbatim MIT text with
`Copyright (c) 2026 Lovecast Inc.` (`LICENSE:1-3`) — the copyright holder is **Lovecast Inc.**, not "stablyai", which
is the `package.json:6` `"author"` value and the GitHub organisation. `orca:README.md:8` carries an
MIT badge and `orca:README.md:270-272` states "Orca is free and open source under the MIT License."

**What MIT obliges (fact, not policy).** The permission grant covers use, copy, modify, merge,
publish, distribute, sublicense and sell (`LICENSE:5-10`), subject to exactly one condition: the
copyright notice and permission notice "shall be included in all copies or substantial portions of
the Software" (`LICENSE:12-13`). There is
no patent grant, no attribution-in-UI requirement and no copyleft. Copying or deriving code requires
carrying the MIT text and the copyright line; **reimplementing a concept read in the source carries
no notice obligation under that text.**

**Orca's own relevant runtime dependencies** (declared in `package.json`):

| Dependency | Version | Role |
| --- | --- | --- |
| `node-pty` | `^1.1.0` (`package.json:167`) | the PTY itself; `pty.spawn` at `src/main/providers/local-pty-spawn.ts:79` |
| `@xterm/headless` | `6.1.0-beta.302` (`package.json:162`) | headless terminal emulation |
| `@xterm/addon-serialize` | `0.15.0-beta.300` (`package.json:161`) | terminal state serialization |
| `ssh2` | `^1.17.0` | remote PTY transport |
| `electron` | `^43.4.1` (devDependency) | desktop host only |
| `@xterm/xterm` + addons | `6.1.0-beta.303` etc. (`package.json:221-227`) | renderer only |

`node-pty` is rebuilt natively (`package.json:95`), requires a `spawn-helper` binary **only on
macOS** (`src/shared/node-pty-spawn-helper.ts:1-11`), and Orca ships its own prebuilt-slot logic
(`src/main/orcad/node-pty-prebuilt-slot.ts`, `src/main/orcad/node-pty-precondition.ts`,
`src/main/orcad/node-pty-loader-diagnosis.ts`). Any runtime that wants a real PTY *via node-pty*
inherits that native-build burden.

Orca also **patches** `node-pty@1.1.0` via `config/patches/node-pty@1.1.0.patch`
(`pnpm-workspace.yaml:43-44`), alongside four `@xterm` patches (`pnpm-workspace.yaml:45-48`). A
patched copy is a derivative and its notice obligation is Orca's own; recorded here as a fact about
the subject, with no implication for this repository.

There is **no** `THIRD-PARTY`, `NOTICE` or `ATTRIBUTION` file at the Orca repository root, and
`pnpm-lock.yaml` records no license fields.

**This repository's own position.** `skills:docs/LICENSE-DECISION.md:1-16`, read in full:

> "No existing license grant or documented owner choice was found in this repository. Accordingly,
> this release-engineering change does **not** select MIT, Apache-2.0, or any other license on the
> owner's behalf. … Until that decision is made, the absence of a `LICENSE` file is an **explicit
> release blocker**; no permission to copy, modify, or redistribute should be inferred from this
> repository."

The policy this project adopted in response is stated in
[Adopted material, license and provenance](#adopted-material-license-and-provenance).

---

## Open items carried forward

Every item below is recorded with its status intact. None is upgraded to a fact.

### Closed in this investigation

| # | Item | Status |
| --- | --- | --- |
| **U1** | Licences of `node-pty`, `@xterm/*`, `ssh2` | **CLOSED with one residual gap** — see the dependency-provenance table in [Adopted material, license and provenance](#adopted-material-license-and-provenance). The two `@xterm` packages ship no LICENSE text; only a declared SPDX identifier was read, and that is recorded as metadata-only, not as verified licence text. |
| **U2** | `docs/design/node-only-runtime-backend.html` | **CLOSED as a verified absence at the pinned commit** — see below. |
| **U5** | Whether the OS-43 Supervisor can already drive a standalone adapter end-to-end | **CLOSED as answered — the Supervisor *core* already can by construction; the *shipped observation adapter* cannot.** `sweep(...)` (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_supervisor.py:88-137`) takes all five dependencies — discovery, observation, liveness, recovery, audit — as injected ports, and the module names no concrete implementation (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_supervisor.py:3-4`), so supervising a standalone adapter needs no Supervisor change. But with no Orca listing authority wired, `orca_state` raises `ObservationUnsupported` (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:885-895`); F6 and F7 have `"orca"` as their only contributor in `FACT_CONTRIBUTORS` (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:89-99`), which sets F11 and classifies every run `UNSUPPORTED_FAIL_CLOSED` (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_classifier.py:94-97`). That is correct and fail-closed, but it is **not** end-to-end. It becomes drivable only once a standalone `RunObservationPort.orca_state` (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:214-232`) implementation exists and `declared_capabilities` is wired (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:1051-1058`). The full six-step trace is in [`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md#os-43-supervisor-integration). |

**U2 in detail.** `git ls-tree -r --name-only 5ee4ace516080891731d100f843b074408a9ce0e -- docs/design`
returns nothing: the directory does not exist at the pinned commit. Five source files reference the
file — `config/scripts/build-orcad.mjs:5`,
`config/scripts/check-runtime-electron-ratchet.mjs:6` and
`config/scripts/check-runtime-electron-ratchet.mjs:147`,
`config/scripts/runtime-serve-terminal-smoke.mjs:13`, `src/main/orcad/orcad-entry.ts:6` — so it is a
**dangling reference in the pinned tree**, and the design rationale for the node-only runtime backend
is unavailable at this revision.

Consequently, Area 11's statements about the Electron ratchet rest on the gate's own mechanics
(`config/scripts/check-runtime-electron-ratchet.mjs:34-42`) and its empty baseline file
(`config/runtime-electron-baseline.txt`), which were read. **No intent, plan or architecture is
attributed to the missing document** beyond the one sentence the scripts themselves quote.

### Still open

| # | Unknown | Why it is still open |
| --- | --- | --- |
| **U3** | Whether `pnpm run build:orcad` actually produces a working Node-only runtime on this machine | Not executed. Building the pinned checkout would risk writing into it, which is forbidden. The *gate* is verified; the *artifact* is not. |
| **U4** | The full implementation of `agentWait` evidence resolution | The type (`src/shared/runtime-terminal-contracts.ts:170-176`) and its consumer (`src/main/runtime/rpc/methods/orchestration-worker-observation.ts:22-147`) were read; the **producer** that chooses between `hook` / `prompt-text` / `title` was not. |
| **U6** | A dedicated raw agent-output capture module beyond the PTY tail buffer and the orchestration `worker-read` path | Searched; only `src/main/runtime/orchestration/worker-output-cursor.ts`, `src/main/runtime/orchestration/worker-output-archive.ts`, `src/main/runtime/orchestration/worker-transcript-read.ts` and the tail/preview fields were found. Reported as a gap, not as an absence. |
| **U7** | `TUI_AGENT_CONFIG` per-agent command strings | `resolveAgentLaunchCommand` (`src/shared/tui-agent-launch-command.ts:24-109`) was read; the full `src/shared/tui-agent-config.ts` table was not. The *mechanism* is verified; the *per-agent values* are not. |
| **U8** | Whether the missing `worker-interrupt` verb existed in an earlier Orca release | Only `v1.4.197` is pinned and no history was searched. Its absence *at this revision* is verified; its absence *at every revision* is not claimed. |

### Standing inferences (reasoned from the source, not asserted by it)

1. **The runtime primitives specifically are standalone-usable.** The empty Electron baseline and
   `src/main/orcad/` prove module-graph portability; they do not prove that a third party can embed
   those modules cleanly.
2. **Orca "has the ingredients but no single primitive" for re-collecting a lost `worker_done`.** The
   ingredients were verified to exist (`worker-show`; `worker-read --source auto|transcript|terminal
   --cursor`, whose output survives release via
   `src/main/runtime/orchestration/worker-output-archive.ts`; `worker-list --terminal-state`;
   `process_incarnation` + `host_scope`; `--retry-request` / `request-show`). It was **not** verified
   that no combination of them suffices. This inference is carried forward unchanged.

**No unverifiable state is reported as working anywhere in this document.**

---

## Capability decision table

### Legend — and what these four words mean here

This project **adopts no Orca source code**. The four verdicts are therefore about *concepts and
obligations*, never about vendored lines, and they must be read that way:

| Verdict | Meaning in this table |
| --- | --- |
| **reuse** | Adopt the concept and its refusal set unchanged, **reimplemented in Python**. It never means "copied source". |
| **adapt** | Adopt the concept with a named, justified deviation, reimplemented. |
| **reimplement** | Build a different mechanism for the same obligation, because Orca's mechanism does not transfer. |
| **reject** | Out of scope for an Orca-independent runtime; not carried forward at all. |

**No row is classified "reuse (copied)" or "adapt (derived source)", and no row may be.** The
rationale is in [Adopted material, license and provenance](#adopted-material-license-and-provenance).

"OS-37 target" names where the obligation lands next; the normative statement of each is in
[`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md) and the build order is in
[`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md).

| # | Capability | Orca source | Verdict | Rationale | OS-37 target |
| --- | --- | --- | --- | --- | --- |
| C1 | PTY spawn with a minted session id and process incarnation | `src/main/providers/local-pty-spawn.ts:21-122`, `src/main/providers/local-pty-spawn.ts:39-40` | **reimplement** | Same obligation, different mechanism: Python's stdlib `pty` / `os.forkpty` replaces `node-pty`. The *identity* discipline (mint an incarnation at spawn, pin the tty) is reused within it. | adapter internals; `start()` receipt keys |
| C2 | Process-group discovery from the OS process table, keyed on the pinned tty | `src/main/pty/posix-pty-process-groups.ts:28-37, 55-83` | **reuse** | The concept transfers exactly: discover, never assume ownership; tty-scoped `ps`, not `ps -ax`. | adapter internals |
| C3 | The three group-signalling refusals (unbound tty, shared tty, recycled pid) | `src/main/pty/posix-pty-process-groups.ts:62-69`; `src/main/pty/posix-pty-foreground-group.ts:104-108` | **reuse** | Losing any one is a blast-radius regression, not a simplification. Reused verbatim as obligations. | adapter internals; `interrupt()` obligations |
| C4 | Kill-ordering contract: child groups before the PTY leader | `src/main/pty/posix-pty-process-groups.ts:90-139`; test `src/main/pty/posix-pty-process-groups.test.ts:39` | **reuse** | Prevents the leader reaping children before they are signalled. | adapter internals |
| C5 | Windows ConPTY termination and identity-probe-gated tree kill | `src/main/providers/local-pty-termination.ts:140-141, 179-181`; `src/main/windows-pty-root-identity.ts:14` | **reject** | Not reproducible from Python stdlib, and the MVP platform envelope is POSIX. Named follow-up scope, not a hidden gap. | out of MVP scope |
| C6 | Launching the agent as a shell command string inside a PTY | `src/shared/tui-agent-launch-command.ts:24-109` | **adapt** | Concept reused; the 43-agent config table is not. A standalone adapter carries a much smaller, explicitly configured command set. | adapter configuration |
| C7 | Launch ordering: identity binding **before** readiness gating | `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:37-39, 79` | **reuse** | The ordering is the safety property. | `start()` obligations |
| C8 | A failed launch must prove its own teardown or raise | `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:119-146` | **reuse** | A silent cleanup is indistinguishable from a leaked process. | `start()` obligations |
| C9 | Provider-side transcript proof with an anti-replay fence | `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:80-95` | **adapt** | The concept — a second, independent proof outside the PTY — is reused; the specific Codex/Claude file layouts are provider details a standalone adapter configures rather than hard-codes. | adapter configuration |
| C10 | Bracketed-paste framing with ESC sanitization, written in one PTY write | `src/shared/agent-prompt-injection.ts:3-4, 54-72`; `src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:49-52` | **reuse** | Pure, portable, and the split-frame failure it prevents is real. | `send()` obligations |
| C11 | The uncapped settle delay / render gate before Enter | `src/shared/agent-prompt-injection.ts:19-49` | **adapt** | Concept and the never-cap rule reused; the measured ingest-rate constants are Orca's measurements on Orca's stack and must be re-measured, not transcribed as truth. | `send()` obligations |
| C12 | Three-proof delivery verification, with the third proof admissible only from a working baseline | `src/main/runtime/agent-prompt-submission-verification.ts:69-127` | **reuse** | This is the single most directly transferable design in the codebase. | `send()` obligations |
| C13 | "Stall means not observed, not not-delivered" — no automatic retry | `src/main/runtime/agent-prompt-submission-verification.ts:10-11` | **reuse** | Directly implements the ticket's "never guess an uncertain state" constraint. | `send()` result vocabulary |
| C14 | Pre-flight / mid-flight aborts (blocked, stale handle, not writable) | `src/main/runtime/agent-prompt-submission-verification.ts:129-142` | **reuse** | Aborts are what replaces a blind retry. | `send()` result vocabulary |
| C15 | Five orthogonal lifecycle vocabularies rather than one enum | `src/shared/agent-status-types.ts:24-25`; `src/shared/terminal-exit-cause.ts:13-33`; `src/main/runtime/orchestration/types.ts:136-146` | **adapt** | A normalized ten-state vocabulary is required by this project; the five source vocabularies are carried alongside it, never replaced by it. | normalized lifecycle contract |
| C16 | Exit-cause resolution with an explicit `unknown{reason}` and the `-1` sentinel | `src/shared/terminal-exit-cause.ts:1-12, 42-45` | **reuse** | The clearest statement of the fail-closed rule in the whole codebase. | `status()` / exit handling |
| C17 | Four surface-specific arbitration orders (S1–S4), never merged | `src/renderer/src/lib/pane-agent-evidence.ts:59-63, 86-123`; `src/main/runtime/runtime-terminal-agent-status-query.ts:64-129`; `src/main/runtime/runtime-terminal-wait.ts:39-166`; `src/main/runtime/orca-runtime-stop-structured-session-process.ts:71-92` | **reuse** | The source itself forbids merging them; merging would change behaviour on at least three of four. | per-surface decision priority |
| C18 | `tui-idle` as a **readiness gate only** | `src/main/runtime/runtime-terminal-wait.ts:39-166`; `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:79-95` | **reuse** | Reused *with its limitation stated*: it is never completion proof. | readiness gating |
| C19 | Renderer pane display arbitration as executable code | `src/renderer/src/lib/pane-agent-evidence.ts:86-123` | **reject** | It lives in the renderer, which is an excluded layer. Its *ordering rule* is reused as concept under C17; the surface itself is not. | none |
| C20 | Two-gate settlement: runtime authority **plus** a CLI re-read that an identity-matched worker report carrying this outcome settled Task and Dispatch — matched by this message's id **or**, for an accepted idempotent retry, by the same reporting handle | `src/main/runtime/orchestration/lifecycle-reconciliation.ts:174-305`; `src/cli/handlers/orchestration-worker-settlement.ts:4-45, 75-94` | **reuse** | "Accepted ≠ settled" is the definition of COMPLETED. Reuse the **whole** predicate: both identity paths, and the refusal when neither matches. Reproducing only the message-id path would refuse settlements Orca accepts; dropping the refusal would settle on a foreign report. | settlement contract |
| C21 | Pane-key-based lifecycle authority ("payload knowledge alone is not authority") | `src/main/runtime/orchestration/lifecycle-reconciliation.ts:16-28` | **adapt** | The rule is reused; "pane key" becomes the standalone adapter's own process/session identity, since it owns no panes. | ownership contract |
| C22 | Heartbeat authority checks; a wrong-pane heartbeat never refreshes liveness | `src/main/runtime/orchestration/lifecycle-reconciliation.ts:124-172` | **reuse** | Prevents a hung assignee hiding behind another agent's timer. | ownership contract |
| C23 | Supervised vs. unsupervised ownership, and the retained-reason vocabulary | `src/main/runtime/orchestration/worker-terminal-ownership.ts:3-28`; `src/cli/specs/orchestration-worker-specs.ts:110-118` | **reuse** | Directly supplies the worker-resource axis of the four-axis contract. | ownership contract |
| C24 | Terminal accounting kept independent of Task/Dispatch outcome | `src/main/runtime/orchestration/worker-terminal-ownership.ts:52, 80` | **reuse** | This *is* the never-substitute rule between axes. | ownership contract |
| C25 | Closed tagged host scope with a `null` on any parse failure | `src/main/runtime/orchestration/worker-terminal-process-liveness.ts:3-37` | **reuse** | Prevents cross-host liveness questions. MVP declares only `local`. | ownership contract |
| C26 | The four-step interrupt ladder with proof of death | `src/main/providers/local-pty-termination.ts:26-28, 44-54, 56-87, 34-42` | **reuse** | The ticket's Area 7 requirement, and it transfers to POSIX signals directly. | `interrupt()` obligations |
| C27 | Re-checking ownership and mode at escalation fire time; re-arming after a *failed* force | `src/main/providers/local-pty-termination.ts:67-70, 76-82` | **reuse** | Prevents both a needless kill and a consumed-only-owner failure mode. | `interrupt()` obligations |
| C28 | Pre-kill descendant snapshot with capture-second stamping, never served stale | `src/main/pty-descendant-termination.ts:60-90` | **reuse** | "Stale PIDs are unsafe to signal" transfers unchanged. | `interrupt()` obligations |
| C29 | Probe-driven restart adjudication with a six-valued owner probe | `src/shared/agent-session-lease-adjudication.ts:21-33, 64-66, 75` | **reuse** | Only `identity-matched` proves ownership; `indeterminate` proves nothing. | rediscovery |
| C30 | Leases marked unreconciled on load — a restart grants no writer | `src/main/runtime/agent-session-record-store.ts:80-84` | **reuse** | Prevents a successor process inheriting authority it never proved. | rediscovery |
| C31 | Release reconciliation that finishes only *previously requested* releases and defers on unresolved identity | `src/main/runtime/orchestration/worker-terminal-release-reconciliation.ts:19-21` | **reuse** | "Never invents release intent" is a cleanup-authority rule. | rediscovery |
| C32 | Env-token process scan as *diagnostic evidence only*, with `unverifiable` on non-Linux hosts | `src/main/runtime/agent-session-spawn-token-process-scan.ts:1-9, 16-19, 54-58` | **adapt** | The refusal semantics are reused as an obligation; the Linux `/proc` read itself is optional for the MVP and must never be ownership proof. | liveness axis |
| C33 | Orca CLI orchestration verbs as the observation surface | `src/cli/specs/orchestration-worker-specs.ts:3-118` | **reject** | A standalone runtime has no Orca CLI. Its own durable state is the observation surface instead. | supervisor observation |
| C34 | Federation, relay, mobile projection, desktop surface | `src/relay/`, `src/main/runtime/mobile-session-layout-projection.ts`, `src/main/runtime/runtime-desktop-surface.ts` | **reject** | Orca-specific by design. | none |
| C35 | Electron host ports | `src/main/host/electron-app-environment.ts` | **reject** | Not applicable to a Python runtime. | none |
| C36 | `node-pty`, `@xterm/*`, `ssh2` as dependencies | `package.json:161-167` | **reject** | They are Orca's dependencies. This project stays standard-library-only; see [Adopted material, license and provenance](#adopted-material-license-and-provenance). | none |

---

## Adopted material, license and provenance

### Adopted concepts (reimplemented; no notice obligation)

The concepts below were read in the pinned Orca source and are reimplemented in Python. Under the
MIT text, reimplementation from a concept carries **no notice obligation**, because nothing is copied
and no substantial portion of the Software is distributed.

| Concept | Read at | Verdict row |
| --- | --- | --- |
| Discover, never assume, process-group ownership from the OS process table | `src/main/pty/posix-pty-process-groups.ts:28-37, 55-83` | C2 |
| The three group-signalling refusals | `src/main/pty/posix-pty-process-groups.ts:62-69` | C3 |
| Identity binding before readiness gating at launch | `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:37-39, 79` | C7 |
| Bracketed-paste framing, ESC sanitization, one-write frame | `src/shared/agent-prompt-injection.ts:54-72` | C10 |
| The uncapped pre-Enter settle rule | `src/shared/agent-prompt-injection.ts:44-46` | C11 |
| Three-proof delivery verification and its "not observed" semantics | `src/main/runtime/agent-prompt-submission-verification.ts:10-11, 69-127` | C12, C13 |
| Exit cause built only from held evidence; `unknown{reason}`; the `-1` sentinel | `src/shared/terminal-exit-cause.ts:1-12, 42-45` | C16 |
| Per-surface arbitration that is never merged into one order | `src/renderer/src/lib/pane-agent-evidence.ts:59-63` | C17 |
| Two-gate settlement, "accepted ≠ settled", including its two identity paths and its refusal when neither matches | `src/cli/handlers/orchestration-worker-settlement.ts:40-45, 75-94` | C20 |
| Process accounting kept independent of Task/Dispatch outcome | `src/main/runtime/orchestration/worker-terminal-ownership.ts:52` | C24 |
| Graceful → bounded wait → force → proof of death | `src/main/providers/local-pty-termination.ts:26-28, 34-87` | C26 |
| Six-valued owner probe where only `identity-matched` proves ownership | `src/shared/agent-session-lease-adjudication.ts:21-33` | C29 |
| "This host cannot enumerate" ≠ "no process carries it" | `src/main/runtime/agent-session-spawn-token-process-scan.ts:1-9` | C32 |

### Adopted code — none, and why

**No Orca source code is adopted into this repository, in any quantity.** No file is copied, no
function is transcribed, and no in-file MIT notice block is created — because nothing is copied,
there is nothing for a notice to attach to.

Three reasons, in order of weight:

1. **Nothing this work produces requires it.** The deliverables are an investigation record, a
   decision table, a provenance list, an exclusion list, a port specification, contracts, priority
   rules, an integration design, a plan and a traceability mapping. Not one of them is satisfied by
   importing source.
2. **The two runtimes do not share a language.** Orca's primitives are TypeScript on Node; this
   project is Python 3.11, standard library only (`skills:docs/COMPATIBILITY.md`). Line-level reuse is
   not mechanically available; every "reuse" candidate is in fact a reimplementation.
3. **Carrying a notice obligation into a repository whose own outbound terms are undefined is the
   owner's call, not this ticket's.** `skills:docs/LICENSE-DECISION.md` declines to select a licence
   on the owner's behalf and calls the missing `LICENSE` an explicit release blocker. Copying MIT
   code in would be *legal* and would still be a decision about this project's licensing posture that
   nobody here is authorized to make. It is avoided rather than taken.

**One bounded exception, and it is not copying.** This document quotes **short excerpts of Orca
source comments and identifiers as evidence for a cited claim**, each attributed to its `path:line`
at the pinned commit. A quotation is bounded to what the claim needs — a sentence or a few lines of a
comment, a type's member list. **No function body, module, or other runnable unit is reproduced
anywhere in these documents, and none may be.** Citing a factual record is what an investigation
document is made of; it is not distribution of the Software or of a substantial portion of it.

This decision is fully reversible. If the owner later selects a licence and a future ticket wants to
vendor Orca source, that ticket makes the decision with the owner's grant in hand; nothing written
here has to be unwound.

### Orca's own dependency provenance (not this repository's inbound licences)

The table below is **evidence about the subject of the investigation**. It must not be read as a
dependency list of `orca-skills`: under the standard-library-only policy this project depends on
none of these, and row C36 rejects all four.

| Package | Version | Finding | Provenance of the read |
| --- | --- | --- | --- |
| `node-pty` | 1.1.0 | **MIT.** `package.json` declares `"license": "MIT"` and the full MIT text is present in the package's `LICENSE`, `Copyright (c) 2012-2015, Christopher Jeffrey`. | Read read-only from the installed Orca 1.4.197 application bundle (`/Applications/Orca.app/Contents/Resources/node_modules/node-pty/`), whose version matches the pinned lockfile entry. The pinned checkout has no `node_modules`. |
| `ssh2` | 1.17.0 | **MIT text, verbatim**, `Copyright Brian White. All rights reserved.` Note: its `package.json` carries **no** `license` field; the LICENSE file is the only declaration. | Same application bundle. |
| `@xterm/headless` | 6.1.0-beta.302 | **`"license": "MIT"` declared in the published `package.json`. No LICENSE text ships in the package.** | The exact published tarball fetched from the npm registry and extracted read-only outside the repository. Absent from the app bundle (bundled at build time). |
| `@xterm/addon-serialize` | 0.15.0-beta.300 | Same: `"license": "MIT"` declared, **no LICENSE text in the package**. | Same. |

**Residual gap, stated as a gap.** For the two `@xterm` packages the evidence is *package metadata
only* — a declared SPDX identifier, not licence text that was read. That distinction is recorded
rather than closed by assumption. It is harmless here because nothing is adopted, but it must not be
restated elsewhere as "MIT licence text verified".

Also recorded as provenance: Orca patches `node-pty@1.1.0` and four `@xterm` packages
(`pnpm-workspace.yaml:43-48`). A patched copy is a derivative, and its notice obligation is Orca's
own.

### Attribution and this repository's own licence position

**Attribution.** The subject of this investigation is
[stablyai/orca](https://github.com/stablyai/orca), tag `v1.4.197`, commit
`5ee4ace516080891731d100f843b074408a9ce0e`, distributed under the MIT License,
`Copyright (c) 2026 Lovecast Inc.` This attribution is a courtesy owed to work that was studied
closely; it is not a licence grant, and it creates no obligation on either party.

**This repository's own position, unchanged by this work.** `orca-skills` has **no `LICENSE` file**.
`skills:docs/LICENSE-DECISION.md` calls that absence an explicit release blocker and refuses to
select a licence on the owner's behalf. This work does not change that, and adds no licence file, no
notice file and no dependency.

---

## Excluded Orca-specific layers

The layers below are **out of scope for an Orca-independent runtime**. They are excluded as layers,
not merely unimplemented: nothing in the contract or the build plan may depend on them.

| Layer | Where it lives | Why excluded |
| --- | --- | --- |
| Renderer / UI | `src/renderer/` | Electron-renderer code. Note that arbitration surface S1 lives here (`src/renderer/src/lib/pane-agent-evidence.ts:86-123`); its *ordering rule* is reused as a concept, the surface is not. |
| Desktop surface, windows, dock, tray | the 13 Electron importers under `src/main/runtime/`, e.g. `src/main/runtime/runtime-desktop-surface.ts`, `src/main/runtime/orca-runtime-emulator.ts` | Desktop-host concerns with no counterpart in a CLI-driven runtime. |
| Mobile session projection | `src/main/runtime/mobile-session-layout-projection.ts` and siblings | An Orca product surface. |
| Remote relay / federation | `src/relay/`, `src/main/runtime/orchestration/db/federation/federated-dispatch-store.ts` | Electron-free but semantically Orca-specific. |
| Electron host ports | `src/main/host/electron-app-environment.ts` and siblings | Electron is the plug-in; a Python runtime plugs in nothing. |
| Orca CLI orchestration verbs as an observation surface | `src/cli/specs/orchestration-worker-specs.ts:3-118` | A standalone runtime has no Orca CLI to call; its own durable state is the authority instead. |
| `node-pty`, `@xterm/*`, `ssh2` | `package.json:161-167` | Orca's dependencies. This project stays standard-library-only. |
| Windows / ConPTY termination, WSL and SSH host scopes | `src/main/providers/local-pty-termination.ts:140-141`; `src/main/runtime/orchestration/worker-terminal-process-liveness.ts:3-37` | Outside the MVP platform envelope; named follow-up scope in [`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md), not a hidden gap. |

### The two prohibitions, stated as rules

1. **This project does not fork Orca.** No Orca tree, subtree or vendored module enters this
   repository. `src/main/orcad/` is cited only as evidence that Orca's module graph is
   Electron-free; it is not a template to copy.
2. **This project does not build a headless Orca.** The goal is an Orca-independent agent execution
   runtime that reuses Orca's *reasoning* about PTY ownership, delivery acknowledgement, trust
   ordering and fail-closed accounting. Rebuilding Orca's daemon, its orchestration database, its CLI
   surface or its runtime service is explicitly not the goal, and any design that trends toward it has
   left the scope.

---

## Where to go next

| You want | Read |
| --- | --- |
| What a conforming adapter **must** do | [`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md) |
| What gets built first, with risks and verification | [`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md) |
