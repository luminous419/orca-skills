# Agent execution contract for an Orca-independent runtime

**This document is normative.** Everything here is an obligation on a conforming
`AgentExecutionPort` adapter. It states no new evidence: every factual claim about Orca is cited
from [`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md), which pins the revision under
investigation — tag **`v1.4.197`**, commit **`5ee4ace516080891731d100f843b074408a9ce0e`** — and
records what was read, what was not, and what remains unknown. Citations here use that document's
convention: bare `src/…` / `config/…` / `package.json` is the pinned Orca checkout; bare
`orca-worker-reviewer-orchestration/…` / `scripts/…` is this repository; colliding roots carry an
explicit `orca:` / `skills:` prefix.

**Scope.** This contract covers the lifecycle vocabulary, the ownership and scoping rules, the
per-surface completion and liveness decision priority, the additive `AgentExecutionPort`
specification, and the OS-43 Supervisor integration. It does **not** schedule work; that is
[`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md).

**Four rules govern the whole document. They are stated once here and are binding everywhere
below.**

> **RULE 1.** A terminal title, a screen state, or a single line of natural-language output is
> **never** sole evidence of completion or of identity. Where such evidence is all there is, the
> state does not advance, and the adapter reports the evidence together with its tier.
>
> **RULE 2.** `tui-idle` (and any equivalent readiness probe) is a **readiness gate**. It is never
> completion proof, in any surface, in any composition, under any timeout.
>
> **RULE 3.** `pty.lastAgentStatus` and every equivalent field is **title-derived** evidence. It is
> not a structured-evidence tier and may never be reported as one.
>
> **RULE 4.** An uncertain or unsupported state is `LOST` or an explicit failure. It is never
> guessed as success, and "no contrary evidence" is never evidence.

---

## Normalized lifecycle states, events and transitions

### Framing: ten normalized names over five Orca vocabularies, not a claim of an enum

The ten state names below are **a normalization for this project's runtime**. They are **not** a
claim that Orca has a ten-value enum. Orca has five orthogonal vocabularies plus `DispatchStatus`,
`TaskStatus` and process-incarnation liveness, and the separation between them is deliberate — see
[`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md#evidence-by-investigation-area), Area 4.

Therefore: **an adapter carries the source vocabulary alongside the normalized state, never instead
of it.** A `status()` result that reports `LOST` without the member of the original vocabulary that
produced it has destroyed information the engine needs, and is non-conforming.

### The ten states and their accepting evidence

Two rows are labelled **PROVISIONAL**. That label is a decided outcome, not unfinished text: it
means the normalized requirement stands but one Orca-side mapping rests on a producer that was not
read at the pinned commit. A provisional row is implementable; it is simply not asserted as verified
on the Orca side.

| State | Accepting evidence | Notes and amendments |
| --- | --- | --- |
| **STARTING** | `WorkerDispatchState='starting'` (`src/main/runtime/orchestration/types.ts:136-137`). For a standalone adapter: PTY spawned and a process incarnation minted, with no readiness proof yet. | Nothing about identity is established here. |
| **READY** | `WorkerDispatchState='ready'` (`src/main/runtime/orchestration/types.ts:136,138`). For a standalone adapter, READY requires **process-identity binding first, then a readiness gate**, in that order (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:37-39` refuses without a published process identity; `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:79` gates readiness only afterwards). | A readiness probe alone is never READY and never anything stronger — RULE 2. |
| **PROMPT_DELIVERED** | `verifyAgentPromptSubmission` returned without throwing (`src/main/runtime/agent-prompt-submission-verification.ts:69-93`), i.e. one of the three named proofs was observed. | A timeout is **not** the complement of this state; see TIMED_OUT. |
| **RUNNING** | A **fresh hook / structured turn-start row only** (`src/shared/agent-status-types.ts:24`; freshness `src/shared/agent-status-freshness.ts:31-52`). | Amendment, stated as a prohibition: the title-derived `pty.lastAgentStatus === 'working'` (`src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:27, 39`) may **never** establish RUNNING on its own — RULE 3. |
| **WAITING_FOR_INPUT** | Hook `waiting`/`blocked`, or a `RuntimeTerminalInteractiveWait` (`src/shared/runtime-terminal-contracts.ts:170-176`). | **PROVISIONAL** on the provenance clause only. The state **carries its provenance** (`hook \| prompt-text \| title`) and preserves the value / `null` / absent tri-state (`src/cli/specs/orchestration-worker-specs.ts:49`: "Null means Orca looked and found no wait. An absent field means it never looked … A waiting worker is healthy, not failed."). A `title`-sourced wait may gate but never settle. The requirement stands; what is provisional is the Orca-side mapping, because the producer that chooses between the three sources was not read (U4). |
| **COMPLETED** | An accepted `worker_done` with `outcome='succeeded'` that settles Task **and** Dispatch, **plus** a re-read confirming that the stored settling report is a `worker_report` carrying **this outcome** and **identity-matched to this reporter** — by this message's own id, **or**, for an accepted idempotent retry, by the same reporting handle (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:174-305`; `src/cli/handlers/orchestration-worker-settlement.ts:4-45, 75-94`). | "Accepted ≠ settled" is part of the definition of this state, not a footnote. The re-read is **not** an unconditional current-message-id equality — see [Completion is settlement](#completion-is-settlement-and-it-is-none-of-s1s4). When **neither** identity path matches, settlement is refused and this state is not entered. |
| **FAILED** | The same gates with `outcome='failed'`; or `WorkerDispatchState='failed'` (`src/main/runtime/orchestration/types.ts:136-146`). | Same authority requirements as COMPLETED. |
| **INTERRUPTED** | For a standalone adapter: **its own interrupt-ladder result**, `interrupted_confirmed` or `terminated_forced` (see [the port draft](#agentexecutionport--additive-draft)). | **PROVISIONAL** on the Orca-side mapping. The Orca-side evidence is the hook-row flag `AgentStateHistoryEntry.interrupted` / `AgentStatusEntry.interrupted` (`src/shared/agent-status-types.ts:59, 64-66, 101, 152-154`); the producer of that flag was not read at the pinned commit. A standalone adapter has no hook channel, so its own ladder result is authoritative for it. |
| **TIMED_OUT** | Waiter rejection (`src/main/runtime/runtime-terminal-wait.ts:94-97`) or `agent_prompt_stalled` (`src/main/runtime/agent-prompt-submission-verification.ts:11, 92`). | Amendment, and it is the important one: `agent_prompt_stalled` "only ever means 'not observed'" (`src/main/runtime/agent-prompt-submission-verification.ts:10-11`). TIMED_OUT is therefore **an unknown-delivery state, not a proven-not-delivered state**, and is **not** automatically retryable. |
| **LOST** | The fail-closed sink for `TerminalExitCause.unknown{…}` (`src/shared/terminal-exit-cause.ts:13-33`), `start_unknown` / `stop_unknown` (`src/main/runtime/orchestration/types.ts:136-146`), `release_unknown` (`src/main/runtime/orchestration/worker-terminal-ownership.ts:58, 93-94`), liveness `'unverifiable'` (`src/main/runtime/orchestration/worker-terminal-process-liveness.ts:39-62`), probe `'indeterminate'` (`src/shared/agent-session-lease-adjudication.ts:21-33`). | LOST **carries `lost_reason`**, naming the original vocabulary member. Entering the sink must not itself flatten six vocabularies into one. |

### The unknown-carrying vocabularies that must survive normalization

**Normative rule.** At a port boundary, **no member of any vocabulary below may be reduced to a
boolean, and none may be defaulted.** A reader that cannot decide reports the unknown member it
actually holds.

| Vocabulary | Where | Unknown members that must survive |
| --- | --- | --- |
| `TerminalExitCause` | `src/shared/terminal-exit-cause.ts:13-33` | `unknown{stop_unverified \| host_status_unavailable \| cause_unreported}` — three distinct reasons, not one |
| `WorkerDispatchState` | `src/main/runtime/orchestration/types.ts:136-146` | `start_unknown`, `stop_unknown` |
| `WorkerTerminalReleaseState` | `src/main/runtime/orchestration/worker-terminal-ownership.ts:3-28` | `unknown` |
| process-incarnation liveness | `src/main/runtime/orchestration/worker-terminal-process-liveness.ts:39-62` | `unverifiable` |
| `AgentSessionOwnerProbe` | `src/shared/agent-session-lease-adjudication.ts:21-33` | `indeterminate{reason}` |
| `agentWait` | `src/cli/specs/orchestration-worker-specs.ts:49` | the **tri-state**: a value, `null` ("looked, found none"), and **absent** ("never looked") |
| spawn-token scan | `src/main/runtime/agent-session-spawn-token-process-scan.ts:6, 17-19` | `{status:'unverifiable', processes:null}` — "this host cannot enumerate", never "no process carries it" |
| Windows tree-kill target | `src/main/windows-pty-root-identity.ts:14` | `unknown`, `foreign`, `absent` — only `own` authorizes |
| observation support (this repository) | `orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:63-67` | `SUPPORTED \| ABSENT_DECLARED \| UNSUPPORTED \| UNREADABLE` — four values, and `ABSENT_DECLARED` is not `UNSUPPORTED` |
| coordinator liveness (this repository) | `orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:241-251` | four-valued and "never boolean"; ABSENT is not EXPIRED, UNREADABLE is not EXPIRED |

Three derived rules:

1. **`UNVERIFIED_PROCESS_EXIT_CODE = -1` is not an exit code.** A reader handed an optional status
   must default to the sentinel, never to `0` (`src/shared/terminal-exit-cause.ts:42-45`: "`exitCode
   ?? 0` mints a clean finish out of an absence of evidence").
2. **LOST is a sink, not an eraser.** Entering LOST records `lost_reason` = the original vocabulary
   member, so the distinctions survive the normalization.
3. **"Cannot read" ≠ "is not there."** An authority that exists and raised is *unreadable*; an
   authority that answered "not present" is *absent*; a fact no authority covers is *uncovered*. The
   three are distinct, and **none of them is `false`**. This is already this repository's own rule
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:141-148`;
   `orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:157-180`)
   and it binds any conforming adapter.

### Events and the transition table

Events are named for **the observable that causes a transition**, never for the state it produces.
An adapter that names an event after its destination has begun assuming the destination.

| Event | What was observed |
| --- | --- |
| `spawned` | a child process exists on a PTY this adapter created |
| `identity_bound` | a process identity proof matched the minted incarnation |
| `readiness_observed` | a readiness gate resolved (a title or screen reading — RULE 2) |
| `prompt_written` | the paste frame and Enter were both written |
| `delivery_proof_observed` | one of the three named delivery proofs was seen |
| `delivery_unobserved` | the verification deadline passed with no proof |
| `turn_start_observed` | a fresh structured turn-start row arrived |
| `wait_observed` | an interactive wait was reported, with its provenance |
| `wait_cleared` | the wait's authority reported it gone |
| `interrupt_requested` | the ladder was entered |
| `exit_observed` | the OS confirmed an exit, with a cause |
| `exit_unproven` | the exit-proof deadline passed with no OS confirmation |
| `settlement_accepted` | a settlement report was accepted by its authority |
| `settlement_confirmed` | a re-read confirmed that an **identity-matched** worker report carrying this outcome settled Task and Dispatch — matched by this message's id **or**, for an accepted idempotent retry, by the same reporting handle |
| `deadline_expired` | a bounded wait elapsed |
| `evidence_unreadable` | an authority that exists could not be read |

### Illegal transitions and the fail-closed default

The transition contract is stated as **five invariants**, not a 10×16 matrix. A matrix invites a
reader to look up a cell; these are what a reviewer can actually check.

1. **Forward-only through the start sequence.** `STARTING → READY` requires `identity_bound`
   **and** `readiness_observed`, **in that order**. `readiness_observed` alone never advances the
   state (RULE 2), and it never establishes identity (RULE 1).
2. **`PROMPT_DELIVERED` requires a proof, never the absence of a failure.** `delivery_unobserved`
   transitions to `TIMED_OUT` — not to `PROMPT_DELIVERED`, and not back to `READY`.
3. **`COMPLETED` and `FAILED` have exactly one entry edge each:** `settlement_confirmed` with the
   corresponding outcome. `settlement_accepted` alone does not reach them. **No other event, on any
   surface, may enter these two states.** `settlement_confirmed` is emitted only when the re-read's
   exact Task/Dispatch/status checks pass **and** one of its two identity paths matches; a re-read
   where neither identity path matches emits no event at all — it is a named refusal, and the state
   does not advance.
4. **Every unreadable authority routes to `LOST`,** with `lost_reason` set. `LOST` is enterable from
   any state. It is **never** entered by inference from a missing signal that was never looked for —
   that is *absent*, a distinct condition, and it is reported as such.
5. **No transition may be justified by a title, a screen reading, or a single line of
   natural-language output alone** (RULE 1). Where such evidence is the only evidence, the state does
   not advance and the adapter reports the evidence with its tier.

**Compatibility clause.** This contract is additive. An adapter that already satisfies
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:17-24` remains conforming
at a *lower capability level*; it simply declares fewer capability tokens.

---

## Ownership and repository scoping contract

### The four axes, their vocabularies and their order

Every dispatch is accounted on four axes. The axes come from this project's own policy contract
(`orca-worker-reviewer-orchestration/SKILL.md:875-940`), and Orca supplies the matching vocabularies
(`src/main/runtime/orchestration/worker-terminal-ownership.ts:3-28`).

| Axis | Field | Closed vocabulary |
| --- | --- | --- |
| (a) settlement | `settlement` | `settled_succeeded \| settled_failed \| not_settled \| settlement_unknown` |
| (b) supervised worker-resource registration | `worker_resource` | `reused \| retained \| released \| unsupervised \| registration_unknown` |
| (c1) residual process liveness | `process_liveness` | `live \| exited \| unverifiable` |
| (c2) cleanup authority | `cleanup_authority` | `authorized \| not_authorized \| unknown` |

Three rules travel with the shape and are normative:

1. **No axis is evidence for another.** In particular **(c1) never authorizes a close; (c2) does.**
   Orca states the same separation directly: "Terminal state exposed by worker-list; process
   accounting, never Task/Dispatch outcome"
   (`src/main/runtime/orchestration/worker-terminal-ownership.ts:52`), and "a completed Task can still
   own a live terminal" (`src/cli/specs/orchestration-worker-specs.ts:116`).
2. **The order is STEP 1 → 2 → 3 → 4**, and the (a) determination precedes this dispatch's first
   lifecycle mutation.
3. **All four are recorded for every dispatch**, including when (c1) is not `live`. Only the
   *action* is then "nothing to do"; the *record* is still four fields.

A typed carrier for this shape does not exist in
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/contracts.py` today — the file has
`settlement` and `runtime_ownership` as *capability tokens*
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/contracts.py:72-90`) but no type
carrying all four together. Adding it is scheduled in
[`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md); until then the shape is a
documented normative requirement on the `status()` result.

### Identity: session, PTY, process incarnation, host scope

A conforming adapter carries four identities, and they are not interchangeable.

| Identity | Obligation | Source of the rule |
| --- | --- | --- |
| **Session id** | Durable; must survive the process that minted it and be re-readable by a stranger process. On load, **every persisted lease is unreconciled** — a restart grants no writer on the strength of what the previous process wrote. | `src/main/runtime/agent-session-record-store.ts:80-84` |
| **PTY / tty** | Pinned **at spawn** and compared against what the process table reports now. A mismatch refuses the operation. | `src/main/pty/posix-pty-foreground-group.ts:104-108` |
| **Process incarnation** | Minted at spawn, matched on `${session}:${incarnation}`. A candidate session whose incarnation id is missing or untrimmed yields **`unverifiable`, not `exited`**. | `src/main/runtime/orchestration/worker-terminal-process-liveness.ts:39-62, 57-61` |
| **Host scope** | A closed tagged union `local \| wsl{distro} \| ssh{targetId}`; **anything unparsable is `None`**, never a default. Liveness questions are never asked cross-host by accident. | `src/main/runtime/orchestration/worker-terminal-process-liveness.ts:3-37, 36` |

**Authority is identity, not knowledge.** A settlement or heartbeat is authorized by the *identity*
of its sender, not by its ability to quote the right ids: "payload knowledge alone is not authority"
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:16-28, 25-27`). A standalone adapter,
which owns no panes, substitutes its own process/session identity for Orca's pane key and must apply
the rule with the same strictness — a foreign report claiming the right handle is rejected
(`src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:179`).

**A spawn token is diagnostic evidence, never ownership proof on its own**
(`src/main/runtime/agent-session-spawn-token-process-scan.ts:54-58`), and a host that cannot
enumerate processes answers `unverifiable`, never "no process carries it"
(`src/main/runtime/agent-session-spawn-token-process-scan.ts:1-9`).

### Repository / worktree scoping and what a standalone adapter may act on

- **An adapter acts only on resources it created or has provably adopted**, within one declared host
  scope. Ownership is a recorded, transferable fact with retained history
  (`src/main/runtime/orchestration/worker-terminal-ownership.ts:30-50`), not an inference from
  proximity.
- **Repository and worktree placement are inputs, not things the adapter invents.** Orca's own rules
  are the model: creation flags "are rejected for current/existing worktrees"
  (`src/cli/specs/orchestration-worker-specs.ts:35`); a retry link "does not inherit placement"
  (`src/cli/specs/orchestration-worker-specs.ts:38`).
- **An unknown start is a failure, not a success.** "The call exits 0 only for ready. Failed or
  `outcome_unknown` exits 1" (`src/cli/specs/orchestration-worker-specs.ts:39`) — RULE 4.
- **Rediscovery finishes only previously requested work.** A restart-time reconciliation "never
  invents release intent: resources outside requested/releasing are untouched, and unresolved
  identity **defers** rather than settling or broadening the close"
  (`src/main/runtime/orchestration/worker-terminal-release-reconciliation.ts:19-21`). Its outcome
  vocabulary is `released | pending | unknown | retained`
  (`src/main/runtime/orchestration/worker-terminal-release-reconciliation.ts:4-10`) — `unknown` is a
  reportable outcome, not an error to swallow.
- **Only `identity-matched` proves ownership.** The owner probe is six-valued; only the first three
  members prove absence and `indeterminate` proves nothing
  (`src/shared/agent-session-lease-adjudication.ts:21-33, 64-66, 75`).

### What must never be killed, closed or released

These are obligations. Losing any one of them is a blast-radius regression, not a simplification.

1. **Never signal a group on an unbound tty** (`tty === '?'`/`'??'`) —
   `src/main/pty/posix-pty-process-groups.ts:62-64`.
2. **Never signal a group on a tty shared with the driver's own process**; fall back to a root-scoped
   signal — `src/main/pty/posix-pty-process-groups.ts:65-69`.
3. **Never signal when the captured-at-spawn tty does not match what the process table reports now**
   (recycled pid) — `src/main/pty/posix-pty-foreground-group.ts:104-108`.
4. **Never tree-kill on an unproven identity.** Only an `own` probe authorizes; `unknown`, `foreign`
   and `absent` do not — `src/main/providers/local-pty-termination.ts:179-181`;
   `src/main/windows-pty-root-identity.ts:14`.
5. **Never signal from a stale process-table scan.** A snapshot is stamped before the scan starts and
   is never served to a later request, "because stale PIDs are unsafe to signal" —
   `src/main/pty-descendant-termination.ts:63-65, 89-90`.
6. **Never touch what was not requested**: no worktree, no setup terminal, no configured tab, no
   reused or pre-existing terminal, no user-taken-over terminal, no unrelated process
   (`src/cli/specs/orchestration-worker-specs.ts:72, 93`).
7. **A fence is not a stop.** An operation that fences a dispatch "retains all possibly-live
   resources and performs no process or filesystem action"
   (`src/cli/specs/orchestration-worker-specs.ts:81`); an adapter must not quietly upgrade one to the
   other.

---

## Completion and liveness decision priority (per surface)

### Completion is settlement, and it is none of S1–S4

Read this before the surfaces, because the surfaces do not answer it.

**COMPLETED is established only by an accepted, authority-checked settlement plus a re-read
confirmation that an *identity-matched* worker report carrying *this* outcome settled *this* Task and
*this* Dispatch.** Concretely: the dispatch id is mandatory ("taskId alone is not a completion
authority; retried tasks can have stale `worker_done` messages racing the current active dispatch" —
`src/main/runtime/orchestration/lifecycle-reconciliation.ts:241-242`); the sender must hold lifecycle
authority by identity, not by knowledge
(`src/main/runtime/orchestration/lifecycle-reconciliation.ts:262-267`); and only **after** the exact
dispatch-id, dispatch-status and task-status checks all pass does the re-read look at the stored
report at all (`src/cli/handlers/orchestration-worker-settlement.ts:27-35`).

**What the re-read actually asserts about the stored report** — two required fields, then a choice of
two identity paths, either of which suffices (`isExactWorkerReport`,
`src/cli/handlers/orchestration-worker-settlement.ts:75-94`):

| Clause | Predicate | Kind |
| --- | --- | --- |
| provenance | `parsed.provenance === 'worker_report'` | **required** |
| outcome | `parsed.outcome === outcome` | **required** |
| identity (a) — *this exact message* | `parsed.messageId === receipt.messageId` | one of two; either suffices |
| identity (b) — *accepted idempotent retry* | `receipt.fromHandle !== undefined && parsed.reportedBy === receipt.fromHandle` | one of two; either suffices |

**A retrying message is not *this exact report*, and this contract does not call it one.** Path (b)
is deliberate and regression-tested:
`src/cli/handlers/orchestration-lifecycle-rejection.test.ts:248-288` ("accepts an idempotent retry
whose first report already settled") supplies the current receipt as `msg_retry` while the stored
report holds `msg_first`, and the send **succeeds**, because both name the same reporting handle and
the same outcome. An adapter that implemented an unconditional current-message-id equality would
refuse a settlement Orca accepts — a policy divergence under AC-37-20, not extra strictness.

**The trap, stated so it is not repeated.** The predicate function is *named* `isExactWorkerReport`,
and the refusal it guards says "did not confirm that the exact report settled its Task and Dispatch"
(`src/cli/handlers/orchestration-worker-settlement.ts:40-45`). Both names assert an exactness the
body does not implement. Symbol presence is not logical equivalence: a citation checker that binds a
named symbol to a line range confirms the symbol is there and cannot evaluate what it computes. That
is why this claim survived three green citation gates, and why the run now carries a predicate-level
assertion for it (see [verification strategy](./STANDALONE_CLI_ADAPTER_PLAN.md#verification-strategy)).

**Fail-closed is unchanged, and must stay unchanged.** When **neither** identity path matches — or
provenance, outcome, dispatch id, dispatch status or task status fails — the settlement is **refused**
with `operation_unknown` and routed to recovery; it is never inferred
(`src/cli/handlers/orchestration-worker-settlement.ts:28-37, 40-45`). The sibling regression
`src/cli/handlers/orchestration-lifecycle-rejection.test.ts:315-354` ("rejects a terminal Dispatch
with the wrong identity") holds that line: a stored report whose `messageId` *does* match the current
receipt is still refused when the dispatch id does not. Path (b) narrows to *the same reporter, the
same outcome, on an already-verified Task and Dispatch*; widening it further — treating any report
from any sender as settling this dispatch — would be a worse defect than the over-claim this section
corrects.

**Accepted ≠ settled.** None of the four surfaces below can produce COMPLETED, individually or in
combination.

### S1–S4: the four surfaces and the decision each answers

**There is no single global trust order to copy.** The source forbids merging the layers: they are
returned separately because "consumers legitimately combine them differently … so a single merged
status would silently change behavior" (`src/renderer/src/lib/pane-agent-evidence.ts:58-63`). An adapter therefore
reproduces **four decisions**, each with its own ordering.

| Surface | Orca implementation | The decision it answers | Ordering the adapter must reproduce |
| --- | --- | --- | --- |
| **S1** | `resolvePaneAgentActivity`, `src/renderer/src/lib/pane-agent-evidence.ts:86-123` | *"What do I display or report as this agent's activity?"* | fresh hook (`authoritative`) → title (`fallback`, flagged when there is no live PTY, and liveness-gated consumers must then treat it as absent) → none. Layers reported separately, **never merged**. |
| **S2** | `RuntimeTerminalAgentStatusQuery.readStatus`, `src/main/runtime/runtime-terminal-agent-status-query.ts:64-129` | *"What is this terminal's agent status right now?"* | live `permission` title → blocked wait text (recency-contested; an approval prompt is unconditional) → fresh hook, gated on the foreground process not being a plain shell → identity-resolved title → `null` plus a process probe. |
| **S3** | `RuntimeTerminalWait.wait`, `src/main/runtime/runtime-terminal-wait.ts:39-166` | *"May I send a prompt yet?"* — **readiness only** | blocked-reason refusal → OSC title `idle` → adopted/renderer-synced title → known-ready screen preview → bounded timeout. **No hook tier at all**; every accepting tier is a title or a screen reading. |
| **S4** | `structuredTuiStatus`, `src/main/runtime/orca-runtime-stop-structured-session-process.ts:71-92` | *"May I stop this structured session?"* | fresh hook → blocked ⇒ busy / known-ready preview ⇒ idle → live-observed title-derived idle (`src/main/runtime/structured-tui-idle-evidence.ts:3-9`) → not connected ⇒ busy. |

Two prohibitions attach to this table and are unconditional:

- **RULE 2 applies to S3.** S3 answers readiness and nothing else. Its result is never completion
  proof, and it never establishes identity.
- **RULE 3 applies to S4's last tier.** The `status` input to that tier is the title-derived
  `pty.lastAgentStatus`; it sits **below** the hook row and below a screen-text preview, and it is
  not a structured tier.

### The seven portable refusals

The genuinely portable rule is the refusal set, not a ranking. These hold on **every** surface.

| # | Refusal |
| --- | --- |
| **R-1** | Blocked-prompt text always beats idle. A pane sitting on an approval prompt is never reported idle (`src/main/runtime/runtime-terminal-wait.ts:58-61`). |
| **R-2** | `permission` is never completion (`src/main/runtime/runtime-terminal-wait.ts:141-148`: only `'idle'` satisfies the readiness condition, not `'permission'`). |
| **R-3** | A restored, unconfirmed row is never fresh (`src/shared/agent-status-types.ts:170-173`; enforced identically at `src/shared/agent-status-freshness.ts:45-51` and `src/renderer/src/lib/pane-agent-evidence.ts:26-29`). |
| **R-4** | A status without live observation is never authorizing (`src/main/runtime/orca-runtime-apply-tracked-pty-title.ts:40, 151`). |
| **R-5** | A `snapshot` observation is never proof of a new turn (`src/shared/agent-status-observation.ts:28-37`). |
| **R-6** | Cross-authority order is *incomparable*, not *older*. Fall back to the timestamp rule across authorities; never mix the two orders (`src/shared/agent-status-observation.ts:85-90`). |
| **R-7** | Absence of evidence is `unknown`, never success — and never `0` (`src/shared/terminal-exit-cause.ts:1-12, 42-45`). |

### Fail-closed rules, per situation

| Situation | Verdict |
| --- | --- |
| S3 returns idle and nothing else is known | READY at most. Never a completion, never RUNNING, never identity. |
| S3 times out | TIMED_OUT (unknown). Never "not ready", and never "finished". |
| Exit status is absent or synthesized | `unknown{host_status_unavailable \| cause_unreported \| stop_unverified}` → LOST with `lost_reason`. **Never `exited{0}`.** |
| Prompt-delivery verification times out | TIMED_OUT with `delivery=not_observed`. **Not** auto-retried: the bytes were written before verification began. |
| Settlement accepted but the re-read does not confirm — any exact check fails, **or** neither identity path matches | Not a completion. A named refusal (`operation_unknown` in Orca's vocabulary), routed to recovery. |
| A liveness probe returns `unverifiable` / `indeterminate` | LOST for the liveness axis (c1). Never `exited`, and never permission to close — that is axis (c2). |
| Any surface's required evidence is missing entirely | The state is unknown → LOST or an explicit failure. Never a success guess (RULE 4). |

---

## AgentExecutionPort — additive draft

### Scope: additive to the existing port; no signature changes

This is an **additive delta** to the existing `AgentExecutionPort` at
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:17-24`. **No method is
added, removed or re-signed.** Everything below constrains the *contents* of the mappings those six
methods already exchange, plus the capability tokens an adapter may declare.

The six methods are `capabilities`, `start`, `send`, `status`, `interrupt` and `settlement`. Ten
further ports sit beside it in the same file, including the five OS-43 ports
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:196-342`), which were
added additively without touching the six-method core. This draft follows that precedent.

**PTY stays below the port line.** No `PtyExecutionPort` or `ProcessOwnershipPort` is proposed,
either here or for the implementing ticket. A POSIX-specific transport protocol in a runtime-neutral
boundary could only be satisfied by the Orca-driving adapter and the fake adapter through stubs, and
every PTY-level obligation is expressible as a required key with a closed vocabulary in the mappings
the six methods already return.

Adding the capability tokens and the typed four-axis carrier named below to
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/contracts.py` is implementation
work, scheduled in [`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md). This
document changes no source file.

### Per-method obligations and required result shapes

#### `capabilities() -> frozenset[str]`

Existing tokens keep their meanings
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/contracts.py:72-90`). An adapter
declares a token **only** when the underlying runtime really provides it; the engine's ladder fails
closed on an absent capability rather than pretending the primitive exists
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:28-41`).

**Proposed additional tokens.** These do **not** exist in `contracts.py` today and are proposed for
the implementing ticket to add: `pty_session`, `prompt_delivery_verified`, `interrupt_ladder`,
`process_group_ownership`, `session_rediscovery`. The same honesty rule applies to each.

#### `start(intent, *, lease_token=None) -> Mapping`

| Key | Meaning | Rule |
| --- | --- | --- |
| `session_id` | the adapter's durable session identity | must survive the process that minted it |
| `process_incarnation` | this spawn's incarnation id | a mismatch yields `unverifiable`, never `exited` |
| `host_scope` | `local` for the first implementation | a closed tagged union; anything unparsable is `None`, never a default |
| `pty_id` / captured tty | the tty pinned at spawn | required for the recycled-pid guard |
| `spawn_token` | identity token carried into the child environment | diagnostic evidence only; never ownership proof on its own |
| `start_outcome` | `ready \| failed \| start_unknown` | **`start_unknown` is a failure, not a success** |

Two rules travel with the table:

1. **A start that cannot prove process identity is a failure**
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:37-39`;
   `src/cli/specs/orchestration-worker-specs.ts:39`).
2. **A failed start must prove its own teardown or raise**
   (`src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:119-146`). A launch that
   cannot prove teardown is an error, never a silent cleanup.

#### `send(intent_id, command) -> Mapping`

The verified two-write prompt protocol is an **obligation**, not a suggestion:

1. Sanitize, then frame: bracketed paste `ESC[200~ … ESC[201~`, with every raw `ESC` in the payload
   replaced by a literal `<ESC>` so prompt text cannot inject control sequences
   (`src/shared/agent-prompt-injection.ts:54-72`).
2. **One PTY write for the whole frame** — a split frame can lose its beginning
   (`src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:49-52`).
3. A gap before Enter: a render gate where a render signal exists, otherwise a settle delay computed
   from byte length and a measured host ingest rate. **The delay is never capped** — "a cap silently
   reintroduces the mid-paste Enter it exists to prevent"
   (`src/shared/agent-prompt-injection.ts:19-49, 44-46`).
4. Write `\r` (`src/shared/agent-prompt-injection.ts:5`).
5. Verify by polling for one of the three named proofs, the third (`outputSequence` advanced)
   admissible **only** when the agent was already working at baseline
   (`src/main/runtime/agent-prompt-submission-verification.ts:69-127`).

Required result key `delivery`, closed vocabulary:
`delivered_confirmed | not_observed | blocked | stale_handle | not_writable`.

- **`not_observed` is not `not_delivered`.** The bytes were written before verification began
  (`src/main/runtime/agent-prompt-submission-verification.ts:10-11`), so it must **not** be
  auto-retried. It maps to TIMED_OUT with unknown delivery and is routed to the run's recovery
  policy, never to a resend.
- Pre-flight and mid-flight aborts are required, not optional: refuse if the pane is or becomes
  `permission` (`blocked`), refuse if the handle generation changed (`stale_handle`), refuse if the
  write is rejected (`not_writable`)
  (`src/main/runtime/agent-prompt-submission-verification.ts:129-142`;
  `src/main/runtime/orca-runtime-write-terminal-agent-prompt.ts:33, 48, 52-54, 74, 88-91`).

#### `status(intent_id) -> Mapping`

| Key | Contents |
| --- | --- |
| `state` | one of the ten normalized states |
| `lost_reason` | present **iff** `state == "LOST"`; the original vocabulary member |
| `evidence` | which surface answered (S1–S4) and which tier within it, so the decision is auditable |
| `axes` | the four-axis block, all four fields, always |
| `wait` | when waiting: the value **and** its provenance (`hook \| prompt-text \| title`), with the value / `null` / absent tri-state preserved |

Prohibitions restated on this method, because it is where they get violated:

- No boolean may stand in for a member of any unknown-carrying vocabulary.
- The `pty.lastAgentStatus` equivalent is title-derived evidence and may never be reported as a
  structured tier (RULE 3).
- A readiness result may never appear as a completion (RULE 2).

#### `interrupt(intent_id, reason) -> Mapping`

The **signature does not change.** What changes is that its semantics are now specified: the
four-step ladder, drawn from `src/main/providers/local-pty-termination.ts`.

1. **Graceful** — a terminating signal to the narrow, identity-proven target (POSIX `SIGTERM` to the
   root pid; group signalling only where ownership is provable)
   (`src/main/providers/local-pty-termination.ts:44-54`).
2. **Bounded wait** — a deadline, re-checking ownership and mode at fire time so a natural exit or an
   ownership change cancels the escalation
   (`src/main/providers/local-pty-termination.ts:56-87, 67-70`).
3. **Force** — group-scoped only where provable, root-scoped otherwise. A *failed* force attempt
   reverts the mode and re-arms with one fewer attempt rather than consuming the only owner
   (`src/main/providers/local-pty-termination.ts:76-82`).
4. **Proof of death** — a bounded wait for an OS-confirmed exit
   (`src/main/providers/local-pty-termination.ts:34-42`). **An unconfirmed exit is reported as
   unconfirmed; it is never reported as success**
   (`src/main/providers/local-pty-termination.ts:198`).

Reference constants, as measured in Orca and to be re-measured rather than copied as truth:
`LOCAL_PTY_PHYSICAL_EXIT_TIMEOUT_MS = 8_000`, `LOCAL_PTY_GRACEFUL_FORCE_TIMEOUT_MS = 5_000`,
`LOCAL_PTY_FORCE_KILL_RETRY_MS = 250` (`src/main/providers/local-pty-termination.ts:26-28`).

Required result key `interrupt_outcome`, closed vocabulary:
`interrupted_confirmed | terminated_forced | exit_unproven | not_owned | unsupported`.
**`exit_unproven` maps to LOST**, never to COMPLETED or FAILED.

The identity guards of [What must never be killed, closed or
released](#what-must-never-be-killed-closed-or-released) are obligations of this method.

**Interrupting is not settling.** The existing `LifecycleSettlementPort` docstring already draws the
line — "`AgentExecutionPort.interrupt` cannot express this: interrupting is not settling"
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:131-137`) — and this
contract does not collapse them.

**A known defect this contract does not fix.** `OrcaAdapter.interrupt`
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/orca_adapter.py:479-482`) invokes a
CLI verb that does not exist in 1.4.197; the pinned spec file defines exactly eight `worker-*` verbs
and none of them is an interrupt (`src/cli/specs/orchestration-worker-specs.ts:3-118`). It is latent
rather than live — no caller of `.interrupt(` exists under
`orca-worker-reviewer-orchestration/tools/`, and
`orca-worker-reviewer-orchestration/tools/deterministic_workflow/fake_adapter.py:389` returns a stub
— but it is real, and correcting it is scheduled in
[`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md). Whether that verb existed in an
earlier Orca release is **not** claimed here; no history was searched (U8).

#### `settlement(intent_id) -> SettlementEvent | None`

Signature unchanged. One rule: **`None` means proven-absent.** An adapter that cannot read its
settlement authority **raises** rather than returning `None`, for exactly the reason
`ExternalRecoveryPort.lookup` does
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:28-41`) and
`open_dispatches` does
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:141-148`). Returning
`None` for an unreadable authority is the "unknown ⇒ success" leak this contract exists to prevent.

### Capability declaration and honesty rules

**`external_resume` may be declared only when all four of these hold. If any is unmet, the adapter
must not declare it, and the engine's recovery ladder correctly fails closed
(`IDEMPOTENCY_RECOVERY_UNSUPPORTED` → BLOCKED).**

1. **A durable claim written before the effect is attempted**, keyed on the stable `intent_id`, so a
   successor process can distinguish "never started" from "may already exist" without re-running the
   effect.
2. **Re-readability by a stranger process.** The record must be reconstructible by a process holding
   none of the objects of the process that created it. An implementation that can only read its own
   memory does not satisfy this
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:141-148`).
3. **An identity fence on collection.** A settlement may be collected only when it carries the intent
   identity **and** a process/incarnation identity binding it to this attempt, so a replayed or
   foreign settlement cannot be harvested as this one's
   (`src/main/runtime/orchestration/lifecycle-reconciliation.ts:241-242`;
   `src/main/runtime/orchestration/lifecycle-reconciliation.test.ts:179`).
4. **Unknown is not absence.** `lookup` returns `None` only to *prove* no effect exists and raises
   whenever existence is merely unknown; `resume` may never synthesize a settlement from the absence
   of contrary evidence
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:28-41`).

Why the Orca-driving adapter does not declare it today, in its own words
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/orca_adapter.py:57-83`):
"`worker_done` is delivered once, to the message stream of the process that owns the run; a
settlement delivered to a process that has since died cannot be re-collected through any documented
Orca primitive, and `task-create` accepts no idempotency key that would let one be reconstructed."

**Differing declarations between adapters are not a policy divergence.** `capabilities()` is a
truthful statement about one runtime. The engine's recovery ladder is identical in both cases, and
the difference in declared capability is exactly the mechanism by which identical policy produces a
correct, runtime-appropriate outcome. What *would* violate the same-policy constraint is a standalone
adapter declaring `external_resume` without the four conditions in order to skip the BLOCK.

`lifecycle_settlement` is declarable only when all five methods of `LifecycleSettlementPort` are
honoured **durably**, including `open_dispatches`' stranger-process rule
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:141-148`). Conditional
declaration is the established pattern, not an invention: the Orca-driving adapter already declares
`lifecycle_settlement` only when the durable journal is wired and `human_approval` only when an
approval port is present
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/orca_adapter.py:57-83`).

**One inference is carried forward honestly.** The ingredients for re-collecting a lost settlement do
exist in Orca 1.4.197, and nobody verified that no combination of them suffices — see
[`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md#open-items-carried-forward). The four
conditions above are written so that they hold regardless of how that resolves.

### PTY-level obligations that stay below the port line

These are internal obligations of a conforming adapter. They are surfaced through the six methods
above and their result vocabularies; none of them is a new port.

| Obligation | Surfaced as | Rule source |
| --- | --- | --- |
| PTY spawn and incarnation minting | `start()` receipt keys | `src/main/providers/local-pty-spawn.ts:39-40` |
| Process-group discovery from the OS process table, tty-scoped | `interrupt()` obligations | `src/main/pty/posix-pty-process-groups.ts:28-37` |
| The three group-signalling refusals | `interrupt_outcome = not_owned` | `src/main/pty/posix-pty-process-groups.ts:62-69` |
| Kill ordering: child groups before the PTY leader | `interrupt()` obligations | `src/main/pty/posix-pty-process-groups.ts:90-139` |
| Paste framing, one-write frame, uncapped settle, Enter | `send()` steps 1–4 | `src/shared/agent-prompt-injection.ts:3-83` |
| Delivery acknowledgement with three proofs | `delivery` vocabulary | `src/main/runtime/agent-prompt-submission-verification.ts:69-127` |
| Readiness gating, composed with identity and never alone | `state = READY` only | `src/main/runtime/orca-runtime-structured-agent-session-launch-tui.ts:79-95` |
| The interrupt ladder and proof of death | `interrupt_outcome` | `src/main/providers/local-pty-termination.ts:26-42` |
| Exit-cause resolution with `unknown{reason}` and the `-1` sentinel | `lost_reason` | `src/shared/terminal-exit-cause.ts:13-33, 42-45` |
| Process-incarnation liveness | `axes.process_liveness` | `src/main/runtime/orchestration/worker-terminal-process-liveness.ts:39-62` |

**Platform envelope.** These obligations are satisfiable with the Python standard library on POSIX.
Windows/ConPTY is *not* in the envelope: "ConPTY has no graceful signal — its first bare kill closes
the pseudoconsole, so treat it as a final force request"
(`src/main/providers/local-pty-termination.ts:140-141`), with an identity-probe-gated tree kill
(`src/main/windows-pty-root-identity.ts:14`). A standard-library Python driver cannot reproduce
ConPTY, so declining Windows is an honest scope statement rather than a regression. The envelope is
recorded in [`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md).

---

## OS-43 Supervisor integration

### What is supported today, by construction

- **The Supervisor core is runtime-neutral and needs no change to supervise a standalone adapter.**
  `sweep(...)` (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_supervisor.py:88-137`)
  takes all five dependencies as injected ports — discovery, observation, liveness, recovery, audit —
  and the module names no concrete implementation anywhere. Its own docstring states the property:
  "Runtime-neutral by construction: every dependency arrives as an injected port and this module
  names no concrete implementation"
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_supervisor.py:3-4`).
- **Its only route into the engine is `RecoveryInvocationPort`**
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:279-293`, routed from
  `orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_supervisor.py:5`).
  That port's docstring records why this is structural: the core "does not import `routing`, `graph`,
  `executor`, `pause_runtime.resume_run`, `pause_store.claim` or `runtime_state.claim`"
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:279-292`).
  **The constraint "do not duplicate workflow routing, decision gates, review policy or Responsible
  Phase inside the Supervisor" is therefore already satisfied by construction, and nothing in this
  contract may weaken it.**
- **The observation is read-only and the action is the engine's.** The Supervisor holds no claim, and
  `RecoveryInvocationPort.recover` is its only action. A standalone adapter gains **no second route**
  into the engine.
- **Most observation facts are already runtime-neutral.** `RunDiscovery`
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:827-840`) and
  `CoordinatorLivenessReader`
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:842-863`) are
  filesystem-only. `durable_wait`
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:954-1005`) has
  three witnesses, of which only the decision-gate one uses the Orca CLI
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:988-999`); the
  pause-record and clarification-request witnesses are filesystem-based, so that fact stays covered
  without an Orca runner. `checkpoint_state`, `pause_state`, `delivery_obligations` and
  `foreign_lease` are all filesystem- or store-based.

### What is not supported today: the six-step fail-closed trace

**The shipped observation adapter cannot serve a standalone runtime, and the failure is total rather
than partial.** This is stated plainly because the alternative — implying an integration that does
not exist — is exactly what this document must not do.

1. `RunObservationAdapter.orca_state`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:885-895`)
   raises `ObservationUnsupported` when no Orca CLI runner is wired: "No Orca listing authority is
   wired for this runtime at all. That is UNSUPPORTED, not 'no dispatch is running'."
2. `FACT_CONTRIBUTORS`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:89-99`)
   gives **F6** (`ACTIVE_DISPATCH_CORROBORATED`) and **F7**
   (`DISPATCH_RECONCILIATION_OUTSTANDING`) exactly one contributor each: `"orca"`. (F5 has two, so it
   survives on the checkpoint alone.)
3. `fold_support`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:211-229`)
   folds a lone `UNSUPPORTED` contributor to `UNSUPPORTED`.
4. F6 and F7 are members of `SAFETY_RELEVANT_FACTS`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:86`), so
   `observation_unsupported`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:157-180`)
   sets **F11**.
5. Rule **R2**
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_classifier.py:94-97`)
   classifies any F11 run `UNSUPPORTED_FAIL_CLOSED`, which is not in `ACTIONABLE_STATES`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_classifier.py:53`).
6. `_escalate_undecidable`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_supervisor.py:295-326`)
   then emits an unsupported-capability escalation.

**Consequence.** Wired against a standalone adapter with no Orca listing authority, **every run
classifies `UNSUPPORTED_FAIL_CLOSED` and no recovery is ever attempted.** That behaviour is correct
and fail-closed — it is the design working — but it is not "drivable end-to-end", and this document
does not claim otherwise.

### What must be supplied, and nothing more

1. **One new implementation of one existing port method:** a standalone `RunObservationPort.orca_state`
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:214-232`) returning
   `{"active_dispatches": tuple, "runnable_actions": tuple}` from the standalone adapter's **own
   durable dispatch state**, obeying the three-way discipline exactly: raise when the authority exists
   and cannot be read, report a legitimately absent authority as an absence, and raise
   `ObservationUnsupported` only when nothing covers the fact.
2. **`declared_capabilities` must be wired**
   (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:1051-1058`),
   or F11 is set for the capability clause regardless of everything else.
3. **Nothing else changes.** The filesystem-based readers listed above are already runtime-neutral.
4. **No new port, no signature change, no new module in the Supervisor core.** The integration is one
   adapter method and one wiring.
5. **The read-only/one-action property is preserved.** No second route into the engine is created.

**What must not be done.** Relaxing F6/F7 to a single "best-effort" contributor would make a
safety-relevant veto guessable in the success direction — precisely what
`observation_unsupported` refuses
(`orca-worker-reviewer-orchestration/tools/deterministic_workflow/watchdog_observation.py:157-180`),
and a direct violation of RULE 4.

### Preconditions: LangGraph and the per-run graph factory

Two preconditions are not standalone-specific but are load-bearing, and an operator who does not meet
them gets no recovery at all:

- **`EngineRecoveryInvocation` requires exactly one of `graph_factory` / `graph_factory_for`**
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:1078-1084`).
  Resolution happens per run at request time
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:1103-1131`),
  refusing by name with `RECOVERY_GRAPH_UNAVAILABLE` rather than handing the engine a `None`.
- **`recover_stalled_run` refuses before any claim** with `UNSUPPORTED` /
  `LANGGRAPH_DEPENDENCY_MISSING` when the pinned LangGraph runtime is absent
  (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/recovery_runtime.py:394-396`). A
  deployment without LangGraph gets no recovery, and that is stated rather than discovered.

---

## Where to go next

| You want | Read |
| --- | --- |
| The evidence behind every citation here | [`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md) |
| What gets built first, with risks and verification | [`STANDALONE_CLI_ADAPTER_PLAN.md`](./STANDALONE_CLI_ADAPTER_PLAN.md) |
