# OS-37 standalone CLI execution adapter — MVP plan

**This document is ticket-scoped and disposable.** It says what the standalone CLI execution adapter
builds first, what it deliberately does not build, what could go wrong, how it will be proven, and
how every acceptance criterion of the investigation ticket maps onto one of this ticket's.

It **re-specifies nothing**. Every obligation it references is stated normatively in
[`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md), and every factual claim about Orca is
evidenced in [`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md), which pins the revision
under investigation: tag **`v1.4.197`**, commit **`5ee4ace516080891731d100f843b074408a9ce0e`**. Where
this document repeats a rule, it is repeating it as a *deliverable*, not redefining it.

**What this document does not authorize.** No production standalone runtime is implemented by the
investigation ticket that produced these three documents. Nothing here changes existing workflow
routing, decision-gate, review or recovery policy. Nothing here forks Orca or rebuilds it headlessly
— see [`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md#excluded-orca-specific-layers).

---

## MVP scope

### Platform envelope

**POSIX (macOS, Linux), local host, CPython 3.11+, Python standard library only.**

The PTY layer is built on `pty`, `os.forkpty` / `os.openpty`, `os.setsid`, `os.killpg`,
`os.waitpid`, `termios` / `tty` and `select`. **No new runtime dependency is added.** That is not a
preference: `skills:docs/COMPATIBILITY.md:44-46` states that this project "uses only the Python
standard library" on CPython 3.11+, and changing that is a project-policy decision this work does not make.

`node-pty`, `@xterm/*` and `ssh2` are Orca's dependencies and are rejected for this project —
[`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md#capability-decision-table), row C36. The
native-build burden that makes `node-pty` expensive (`src/shared/node-pty-spawn-helper.ts:1-11`;
`src/main/orcad/node-pty-prebuilt-slot.ts`, `src/main/orcad/node-pty-precondition.ts`) is exactly what
a pure-Python path avoids.

A pipe-only, no-PTY design was considered and rejected on evidence: Orca launches agents as a shell
command string **inside a PTY** (`src/shared/tui-agent-launch-command.ts:24-109`), and the entire
delivery-acknowledgement design exists because the target is a TUI
(`src/shared/agent-prompt-injection.ts:3-83`;
`src/main/runtime/agent-prompt-submission-verification.ts:69-127`). A pipe-only driver cannot express
delivery acknowledgement at all, cannot observe blocked prompts
(`src/shared/runtime-terminal-contracts.ts:317-324`), and would have to substitute exit status for
every lifecycle question — the exact failure `src/shared/terminal-exit-cause.ts:1-12` documents.

### In scope for the MVP

Twenty-four acceptance criteria. Each names its obligation source in the contract document; none of
them restates a rule.

| ID | Acceptance criterion |
| --- | --- |
| **AC-37-01** | **PTY session start.** Spawn the agent CLI on a standard-library PTY; mint a durable `session_id` and a `process_incarnation`; pin the tty at spawn; return the required `start()` receipt keys with `start_outcome ∈ {ready, failed, start_unknown}`. A start that cannot prove process identity **fails**; a failed start proves its own teardown or raises. |
| **AC-37-02** | **Process-group ownership discovery.** Discover group membership from the OS process table, tty-scoped; implement the three refusals (unbound tty, tty shared with the driver, captured-tty mismatch) and the child-groups-before-leader kill ordering. Ownership is never assumed. |
| **AC-37-03** | **Agent CLI launch composition and ordering.** Compose the launch command from explicit configuration (not a 43-agent table); bind process identity **before** any readiness gate; obtain a second, independent provider-side proof where the provider offers one. |
| **AC-37-04** | **Verified prompt delivery.** Sanitize and frame the prompt, write the frame in one PTY write, gate or delay before Enter with **no cap**, write Enter, then verify against the three named proofs. Return the closed `delivery` vocabulary. `not_observed` is **never** auto-retried. |
| **AC-37-05** | **Bounded output capture.** A cursored, bounded read of agent output that remains readable after the session is released. Recorded as a first-class deliverable because no dedicated Orca module for the raw stream was located (carried unknown U6). |
| **AC-37-06** | **Normalized lifecycle state machine.** The ten states, the named events, and the five transition invariants. `LOST` always carries `lost_reason`. |
| **AC-37-07** | **Unknown-carrying vocabulary preservation.** No member of any listed vocabulary is reduced to a boolean or defaulted at the port boundary. Shipped with a validator that refuses a value outside each closed set. |
| **AC-37-08** | **Per-surface decision priority.** The four surfaces are implemented as **four separate decisions**, never one merged precedence list, with the seven portable refusals and the per-situation fail-closed table. |
| **AC-37-09** | **Completion is settlement.** A completion is established only by an authority-checked settlement **plus** a re-read that first matches this Task, this Dispatch and the expected terminal status exactly, and then confirms the stored settling report is a worker report carrying **this outcome** and **identity-matched to this reporter** by *either* this message's own id *or* — for an accepted idempotent retry — the same reporting handle (`src/cli/handlers/orchestration-worker-settlement.ts:75-94`). Implement **both** identity paths: a message-id-only check refuses settlements Orca accepts, which breaks AC-37-20 parity. A re-read that fails any exact check, or matches neither identity path, is a named refusal routed to recovery — never an inferred completion. |
| **AC-37-10** | **Session identity, reuse, release and cancellation.** Reuse only on proven identity; release only what was requested; a fence performs no process action and is never silently upgraded to a stop. |
| **AC-37-11** | **Interrupt ladder.** Graceful → bounded wait → force → proof of death, expressed as the semantics of the existing `interrupt()` with the closed `interrupt_outcome` vocabulary; `exit_unproven` maps to `LOST`. **Includes correcting `orca-worker-reviewer-orchestration/tools/deterministic_workflow/orca_adapter.py:479-482`** (see [inherited work items](#named-work-items-inherited-from-the-investigation)). |
| **AC-37-12** | **Rediscovery after crash or restart.** A durable claim written before the effect; leases unreconciled on load; probe-driven adjudication where only an identity match proves ownership; release reconciliation that finishes only previously requested work and defers on unresolved identity. |
| **AC-37-13** | **Four-axis accounting with a typed carrier.** Add the typed four-field carrier to `orca-worker-reviewer-orchestration/tools/deterministic_workflow/contracts.py` with exactly the field set and closed vocabularies given in the contract, plus a validator. All four axes recorded for every dispatch; no axis substitutes for another. |
| **AC-37-14** | **Host scoping.** A closed tagged union; the MVP declares `local` only; anything unparsable resolves to `None`, never to a default. No cross-host liveness question is asked by accident. |
| **AC-37-15** | **`external_resume` honesty.** Ship **either** with all four declaration conditions demonstrably met and the capability declared, **or** without the declaration and with the `IDEMPOTENCY_RECOVERY_UNSUPPORTED` → BLOCKED path exercised in a test. A declaration without the four conditions is a defect. |
| **AC-37-16** | **OS-31 integration.** Declare `lifecycle_settlement` only when all five `LifecycleSettlementPort` methods are honoured durably, including the stranger-process rule; otherwise do not declare it and let pause fall back to BLOCK. |
| **AC-37-17** | **OS-43 integration.** Implement a standalone `RunObservationPort.orca_state` over the adapter's own durable dispatch state, obeying the raise / absent / unsupported three-way discipline, and wire `declared_capabilities`. **No new port, no signature change, no new module in the Supervisor core.** |
| **AC-37-18** | **Excluded-layer independence.** The adapter is a sibling module of the Orca-driving adapter and imports nothing from an excluded layer. It requires no Electron, no renderer, no mobile projection and no relay. |
| **AC-37-19** | **Licence and dependency discipline.** No Orca source is vendored; no in-file third-party notice is created; no runtime dependency is added; the standard-library-only policy is preserved unchanged. |
| **AC-37-20** | **Policy parity and no duplication.** The standalone adapter and the Orca-driving adapter preserve the **same deterministic policy** and the **same recovery semantics**. Neither the adapter nor the Supervisor duplicates workflow routing, decision gates, review policy or Responsible Phase; the Supervisor core keeps its single route into the engine. |
| **AC-37-21** | **Capability decision table conformance.** Every `reuse` and `adapt` row of the decision table has a corresponding implementation obligation in this AC set; every `reject` row has no implementation. Any deviation is recorded as an explicit amendment, not a silent drift. |
| **AC-37-22** | **Additive port implementation.** The six existing `AgentExecutionPort` signatures at `orca-worker-reviewer-orchestration/tools/deterministic_workflow/ports.py:17-24` are unchanged. New capability tokens are added to `contracts.py`; no PTY-level port is created. |
| **AC-37-23** | **Evidence discipline.** Any new claim about Orca carries a citation at a stated pinned revision. The unknowns carried forward (U3, U4, U6, U7, U8) are re-checked before being relied on; none is silently upgraded to a fact. |
| **AC-37-24** | **MVP exit criteria.** Every criterion above is either met with recorded evidence or explicitly deferred with a named reason. The [verification strategy](#verification-strategy) is executed and its output recorded; a check that cannot run is reported as not established, never as a pass. |

### Explicitly follow-up scope

Named, not hidden. Each of these is a deliberate exclusion from the MVP, and none is an unknown.

| Deferred | Why | Evidence |
| --- | --- | --- |
| **Windows / ConPTY** | ConPTY "has no graceful signal — its first bare kill closes the pseudoconsole, so treat it as a final force request", and its tree kill is gated on an identity probe. A standard-library Python driver cannot reproduce it. | `src/main/providers/local-pty-termination.ts:140-141`; `src/main/windows-pty-root-identity.ts:14` |
| **Remote host scopes (`wsl`, `ssh`)** | The host scope is a closed tagged union with three members; the MVP declares only `local`. Remote liveness needs a transport the MVP does not have. | `src/main/runtime/orchestration/worker-terminal-process-liveness.ts:3-37` |
| **Linux `/proc` env-token scanning** | It is diagnostic evidence only and is never ownership proof, so it buys nothing the MVP needs. If added later it must keep the `unverifiable`-on-other-hosts semantics. | `src/main/runtime/agent-session-spawn-token-process-scan.ts:1-9, 54-58` |
| **Federation, relay, mobile projection, desktop surface** | Excluded layers. | [`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md#excluded-orca-specific-layers) |
| **A per-agent launch table covering 43 CLIs** | The MVP configures the agents it actually drives. The mechanism is reused; the table is not. | `src/shared/tui-agent.ts:3-39` |
| **Updating `skills:docs/COMPATIBILITY.md`** | That matrix records *verifications*, and no integration suite was run at 1.4.197. Updating it requires a real run, which is a separate act. | [`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md#pinned-revision-and-method) |

### Named work items inherited from the investigation

Four items were identified during the investigation and deliberately **not** applied there, because
each touches production runtime code or a protected path. They are this ticket's.

| # | Work item | Why it was deferred | Shape of the fix |
| --- | --- | --- | --- |
| **W-1** | Correct `orca-worker-reviewer-orchestration/tools/deterministic_workflow/orca_adapter.py:479-482`, which invokes a CLI verb that does not exist at the pinned revision. | Production runtime code; out of scope for a documentation-only ticket. | Either implement `interrupt` against a real non-settling primitive, **or** make that adapter's `interrupt` raise a named unsupported refusal and stop declaring `agent_interrupt`. Not both, and not a silent no-op. Note the pinned spec defines exactly eight `worker-*` verbs (`src/cli/specs/orchestration-worker-specs.ts:3-118`); whether the missing verb existed in an earlier release was not investigated (U8). |
| **W-2** | Add the typed four-axis carrier to `orca-worker-reviewer-orchestration/tools/deterministic_workflow/contracts.py`. | A protected path. | Exactly the four fields and closed vocabularies given in [the contract](./AGENT_EXECUTION_CONTRACT.md#the-four-axes-their-vocabularies-and-their-order), plus a validator that refuses a member outside each set. |
| **W-3** | Implement a standalone `RunObservationPort.orca_state` and wire `declared_capabilities`. | Depends on a standalone adapter that does not exist yet. | One adapter method and one wiring. Without it, every run classifies fail-closed and no recovery is ever attempted — see the six-step trace in [the contract](./AGENT_EXECUTION_CONTRACT.md#os-43-supervisor-integration). |
| **W-4** | Decide `external_resume` honestly. | No implementation existed to be honest about. | AC-37-15: declare with all four conditions met, or do not declare and exercise the BLOCK path. |

---

## Risks

### Subject-matter risks carried forward from the investigation

These are risks to the *thing being built*, established from the pinned source. They are carried
forward unchanged rather than restated in weaker words.

| # | Risk | Severity | Mitigation in this plan |
| --- | --- | --- | --- |
| **R1** | **Trust-ordering divergence — and there is no single order to copy.** Orca arbitrates on four surfaces with different orderings; any adapter that collapses them into one precedence list changes behaviour on at least three of the four. The source explicitly warns against merging (`src/renderer/src/lib/pane-agent-evidence.ts:59-63`). | High | AC-37-08 requires four separate decisions. Verification V-4 tests each surface independently. |
| **R1b** | **A readiness result mistaken for completion evidence.** Every accepting tier of the readiness waiter is a title or a screen reading (`src/main/runtime/runtime-terminal-wait.ts:58-72, 108-129, 136-159`). | High | RULE 2 in the contract; AC-37-06 forbids the transition; V-3 asserts it as a negative test. |
| **R2** | **`external_resume` remains undeclarable**, so the adapter still fails closed on the exact recovery case it exists to fix (`orca-worker-reviewer-orchestration/tools/deterministic_workflow/orca_adapter.py:57-83`). | High | AC-37-15 makes *either* outcome acceptable and *both* explicit. Failing closed is a valid ship state; a dishonest declaration is not. |
| **R3** | **`interrupt` is unimplementable against the pinned runtime** and no port expressed the ladder at all. | High | W-1 plus AC-37-11. The ladder is now specified rather than invented at implementation time. |
| **R4** | **A driver that owns process groups can kill more than Orca would.** Losing any one of the identity guards is a blast-radius regression (`src/main/pty/posix-pty-process-groups.ts:62-69`; `src/main/providers/local-pty-termination.ts:179-181`). | High | AC-37-02 lists the guards as obligations; V-2 tests each refusal path with an injected process table. |
| **R5** | **"Unknown ⇒ success" leakage.** Flattening any unknown-carrying member to a boolean reintroduces the defect `src/shared/terminal-exit-cause.ts:1-12` documents. | High | AC-37-07 plus a shipped validator; V-5 is a property test over every closed set. |
| **R6** | **A prompt-delivery false negative treated as retryable.** A stall "only ever means 'not observed'" (`src/main/runtime/agent-prompt-submission-verification.ts:10-11`); a naive retry double-submits a prompt that did land. | High | AC-37-04 forbids the auto-retry; V-3 asserts that `not_observed` produces no second write. |
| **R7** | **Native PTY build burden** would break the portability posture (`package.json:95`). | Medium | Avoided entirely by the standard-library envelope. Residual: no Windows support (R-M1 below). |
| **R8** | **Licence and provenance ambiguity** — copying MIT code into a repository with undefined outbound terms (`skills:docs/LICENSE-DECISION.md`). | Medium | Avoided: nothing is copied. AC-37-19 keeps it that way. |
| **R9** | **Port churn.** Additive methods on the shared port risk breaking both existing adapters and the executor's capability gating. | Medium | AC-37-22: no signature changes; new capability tokens only. No PTY-level port is created. |
| **R10** | **Version drift in documentation.** The compatibility matrix records 1.4.196; the pinned subject is 1.4.197, and the runtime running on the investigating host was separately observed to be 1.4.197 ([`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md#the-live-runtime-established-separately)). | Low | Recorded as an observation, not an edit. Correcting it requires a real integration run. |
| **R11** | **Scope creep toward rebuilding Orca headlessly.** Orca's own Node daemon makes it look easy, and it is the first prohibition. | Medium | AC-37-18 and AC-37-24; the exclusion list is a rule, not a preference. |

### Risks specific to this MVP

| # | Risk | Status |
| --- | --- | --- |
| **R-M1** | **The POSIX-only envelope means a Windows operator cannot use the MVP.** | **Accepted and documented.** Windows is named follow-up scope above, not a hidden gap. |
| **R-M2** | **The OS-43 integration described in the contract does not exist yet.** Every run against a standalone adapter classifies fail-closed until W-3 lands. | **Explicitly scoped.** The contract states what works, what does not, and the exact failure trace. Nothing asserts a working integration. |
| **R-M3** | **The `@xterm` licence evidence is package metadata, not licence text.** | **Recorded as a residual gap** in the provenance table. Harmless here because nothing is adopted; it must not be restated elsewhere as verified text. |
| **R-M4** | **Two lifecycle rows are provisional on the Orca side** (`INTERRUPTED`'s Orca-side mapping and `WAITING_FOR_INPUT`'s provenance clause). A reader could treat a provisional row as ratified. | **Mitigated** by labelling them in the contract itself. AC-37-23 requires re-checking before relying on either. |
| **R-M5** | **Orca's measured constants may not transfer.** The settle-delay ingest rates and the ladder timeouts were measured on Orca's stack. | **Named.** AC-37-04 and AC-37-11 treat them as starting values to re-measure, not as truths to transcribe. The *never-cap* rule, by contrast, is a design invariant and transfers unchanged. |
| **R-M6** | **A deployment without LangGraph, or without a per-run graph factory, gets no recovery at all.** | **Stated as an operational precondition** in [the contract](./AGENT_EXECUTION_CONTRACT.md#preconditions-langgraph-and-the-per-run-graph-factory), so it is discovered by reading rather than in production. |
| **R-M7** | **A future document could quote a whole Orca module while technically "citing" it.** | **Bounded.** The rule forbids reproducing any runnable unit, and a change-set check catches vendored files. Residual: a very long quotation inside a Markdown file is not mechanically caught. Named rather than hidden. |

---

## Verification strategy

### What must be proven, and how

| # | Verification | Method | Proves |
| --- | --- | --- | --- |
| **V-1** | The port contract is satisfied | Contract tests run against **both** adapters through the same test body, asserting identical engine-visible policy for identical inputs | AC-37-20, AC-37-22 |
| **V-2** | Ownership refusals hold | Unit tests with an **injected process table**: unbound tty, tty shared with the driver, captured-tty mismatch, stale snapshot. Each must refuse; none may fall through to a signal | AC-37-02, AC-37-12 |
| **V-3** | Fail-closed behaviour, as negative tests | For each fail-closed row: readiness-only ⇒ never a completion; readiness timeout ⇒ `TIMED_OUT`; absent exit status ⇒ `LOST` with `lost_reason`, never `exited{0}`; `not_observed` ⇒ no second write; a re-read that fails any exact check **or** matches neither identity path ⇒ named refusal | AC-37-04, AC-37-06, AC-37-09 |
| **V-4** | Four surfaces stay four | One test per surface asserting its own ordering, plus a test that a merged ordering **fails** at least one of them | AC-37-08 |
| **V-5** | No vocabulary is flattened | A property test enumerating every member of every closed set through `status()` and asserting the member survives the round trip; the validator refuses out-of-set values | AC-37-07, AC-37-13, AC-37-14 |
| **V-6** | Capability honesty | With the four `external_resume` conditions unmet, recovery reaches BLOCKED; with `lifecycle_settlement` undeclared, pause falls back to BLOCK. Both asserted, not assumed | AC-37-15, AC-37-16 |
| **V-7** | Supervisor integration | With the standalone observation wired, a stalled run reaches a recoverable classification; **without** it, the run classifies fail-closed and escalates. Both directions tested, so the fail-closed path is proven rather than described | AC-37-17 |
| **V-8** | Real-runtime smoke | An end-to-end run against a real agent CLI on a real PTY: spawn → identity bind → readiness → verified delivery → turn → interrupt ladder → proof of exit. Recorded with the actual output | AC-37-01, AC-37-03, AC-37-05, AC-37-11 |
| **V-9** | No excluded coupling, no new dependency | Import-graph assertion over the new module; dependency manifest unchanged; no vendored Orca file in the change set | AC-37-18, AC-37-19 |
| **V-10** | Nothing else regressed | The repository's existing validation suite, unchanged, run before and after | AC-37-20, AC-37-24 |
| **V-11** | **The settlement predicate is reproduced whole, both branches** | Three cases against the fake adapter, mirroring the pinned regression set one-for-one. **(a)** Same message id ⇒ confirmed. **(b)** *Different* message id, same reporting handle, same outcome, Task/Dispatch/status all matching ⇒ **confirmed**, not refused — the case `src/cli/handlers/orchestration-lifecycle-rejection.test.ts:248-288` pins. **(c)** Matching message id but a non-matching dispatch id ⇒ **refused** with the named refusal — the case `src/cli/handlers/orchestration-lifecycle-rejection.test.ts:315-354` pins. (b) without (c) is an amnesty; (c) without (b) is a divergence from Orca. Both directions are required | AC-37-09, AC-37-20 |

### The fake-adapter / real-runtime split

The two levels answer different questions and neither replaces the other.

- **The fake adapter is where policy is proven.** It is deterministic, needs no PTY and no agent CLI,
  and can be driven into every fail-closed state on demand. V-1 through V-7 and V-11 belong here. A policy
  claim that is only demonstrated against a real runtime is a claim about one lucky run.
- **The real runtime is where the mechanism is proven.** PTY spawn, process-group discovery, paste
  framing timing, the interrupt ladder and exit-cause resolution cannot be faked without faking the
  thing under test. V-8 belongs here, and it is a smoke test with recorded output, not a policy oracle.
- **Neither level may substitute for the other**, and a check that cannot run is recorded as **not
  established** — never as a pass, and never waived on the grounds that something is "only
  configuration".

---

## Traceability: OS-38 AC → OS-37 AC

Two enumerated sets, mapped in both directions.

- **OS-38 acceptance criteria** = the twelve mandatory investigation areas, **A1–A12**, plus the
  eleven mandatory deliverables, **D1–D11**. Twenty-three in total.
- **OS-37 acceptance criteria** = **AC-37-01 … AC-37-24**, authored in
  [In scope for the MVP](#in-scope-for-the-mvp). No identifier outside these two sets appears below.

### Forward — every OS-38 AC maps to at least one OS-37 AC

| OS-38 AC | What it required | OS-37 AC |
| --- | --- | --- |
| **A1** | PTY / process spawn and process-group ownership | AC-37-01, AC-37-02 |
| **A2** | Claude/Codex CLI launch and prompt delivery | AC-37-01, AC-37-03 |
| **A3** | Delivery acknowledgement, retry and output capture | AC-37-04, AC-37-05 |
| **A4** | Lifecycle detection for the ten states | AC-37-06, AC-37-07 |
| **A5** | Trust ordering between hook/structured events, machine-readable output, exit status and PTY heuristics | AC-37-08, AC-37-20 |
| **A6** | Session identity, reuse, settlement, release and cancellation | AC-37-09, AC-37-10, AC-37-15 |
| **A7** | Graceful interrupt → bounded wait → forced termination | AC-37-11 |
| **A8** | Process/session rediscovery after crash or restart | AC-37-12, AC-37-15 |
| **A9** | Run / repository / worktree / agent / task / dispatch ownership | AC-37-13, AC-37-14 |
| **A10** | OS-31 recovery and OS-43 Watchdog/Supervisor integration points | AC-37-16, AC-37-17, AC-37-20 |
| **A11** | Coupling between the UI / Electron / mobile / remote layers and the runtime primitives | AC-37-18 |
| **A12** | MIT licence, attribution, dependencies and copied/derived code provenance | AC-37-19 |
| **D1** | Investigation document with the pinned revision and source/test paths for every claim | AC-37-03, AC-37-05, AC-37-23 |
| **D2** | Per-capability reuse / adapt / reimplement / reject decision table | AC-37-20, AC-37-21 |
| **D3** | Adopted code, concepts and dependencies with licence and provenance | AC-37-19 |
| **D4** | List of excluded Orca-specific layers | AC-37-18 |
| **D5** | `AgentExecutionPort` draft | AC-37-01, AC-37-04, AC-37-11, AC-37-15, AC-37-22 |
| **D6** | Normalized lifecycle state/event and transition contract | AC-37-06, AC-37-07 |
| **D7** | Process/session ownership and repository scoping contract | AC-37-02, AC-37-10, AC-37-12, AC-37-13, AC-37-14 |
| **D8** | Completion/liveness decision priority and fail-closed rules | AC-37-08, AC-37-09 |
| **D9** | OS-43 Supervisor observation/action port integration design | AC-37-16, AC-37-17 |
| **D10** | OS-37 MVP implementation plan, risks and verification strategy | AC-37-24 |
| **D11** | Traceability mapping from OS-38 AC to the follow-up OS-37 AC | AC-37-24 |

**Forward completeness:** all twelve investigation areas and all eleven deliverables appear as a
source row. There is no OS-38 acceptance criterion without a target.

### Backward — every OS-37 AC traces to at least one OS-38 AC

| OS-37 AC | Traces back to |
| --- | --- |
| **AC-37-01** | A1, A2, D5 |
| **AC-37-02** | A1, D7 |
| **AC-37-03** | A2, D1 |
| **AC-37-04** | A3, D5 |
| **AC-37-05** | A3, D1 |
| **AC-37-06** | A4, D6 |
| **AC-37-07** | A4, D6 |
| **AC-37-08** | A5, D8 |
| **AC-37-09** | A6, D8 |
| **AC-37-10** | A6, D7 |
| **AC-37-11** | A7, D5 |
| **AC-37-12** | A8, D7 |
| **AC-37-13** | A9, D7 |
| **AC-37-14** | A9, D7 |
| **AC-37-15** | A6, A8, D5 |
| **AC-37-16** | A10, D9 |
| **AC-37-17** | A10, D9 |
| **AC-37-18** | A11, D4 |
| **AC-37-19** | A12, D3 |
| **AC-37-20** | A5, A10, D2 |
| **AC-37-21** | D2 |
| **AC-37-22** | D5 |
| **AC-37-23** | D1 |
| **AC-37-24** | D10, D11 |

**Backward completeness:** every one of AC-37-01 … AC-37-24 names at least one source, and every
identifier named is a member of the enumerated OS-38 set. There are no orphans on either side and no
invented identifiers.

---

## Where to go next

| You want | Read |
| --- | --- |
| The evidence behind every Orca claim | [`ORCA_RUNTIME_PRIMITIVES.md`](./ORCA_RUNTIME_PRIMITIVES.md) |
| The obligations these criteria implement | [`AGENT_EXECUTION_CONTRACT.md`](./AGENT_EXECUTION_CONTRACT.md) |
