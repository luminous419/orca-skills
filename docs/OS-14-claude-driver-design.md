# OS-14 — the real Claude Code model-selection driver: DESIGN ONLY

**This document is a design. Nothing in it is implemented.** No driver here exists in the
tree, no vocabulary member is added by it, no `/model` experiment was run for it, and no
company (PortKey/GLM) environment was touched. It is the written form of the four design
items OS-14 owes, so that the *common* Worker/Reviewer pair-preparation work — which is
implemented separately, against the existing reference (fake) driver — can proceed without
pre-deciding any of them.

Two further statements, made here because they are the ones most easily lost:

- **A standalone-only implementation of anything below would not be Orca integration.** The
  OS-37 standalone runtime is a different adapter with a different capability set; shipping a
  driver there leaves the Orca adapter path exactly as fail-closed as it is today.
- **Nothing observed locally is evidence about the company environment.** Every measurement
  cited here was taken on a personal, OAuth-authenticated host with `claude -p`. Whether the
  same launch method works through a company gateway is unverified, and §7 lists what would
  have to be re-measured there.

## Vocabulary used in this document

| Term | Meaning | Term NOT used |
|---|---|---|
| **CLI-reported model** | the canonical model id the CLI itself prints for a turn — in `system/init.model`, in an `assistant` event's `message.model`, in `result.modelUsage` keys | "observed actual model" — no local channel proves what a provider actually processed |
| **`assistant_request_id`** | the `request_id` field present on an `assistant` stream event and on the matching transcript record, format `req_01…`, different per turn | "`assistant_server_request_id`" — the *issuer* of that value was never observed, so the name must not assert one |
| **preflight turn** | a discarded turn whose only purpose is to make the CLI report a model before the real task is delivered | — |
| **real-task turn** | the turn that carries the dispatched task | — |

Evidence tags carried over from the analysis run: **실제 관측** = measured, **추론** =
inferred from those measurements, **미검증** = not verified, **문서상 지원** =
declared/documented only.

## Evidence basis, and its exact scope

The measurements this design rests on live in `artifacts/runs/run_e1fd48c8cf9b/` (read-only
analysis run; `ANALYSIS.md` approved, `FINAL_REVIEW.md` PASS WITH NOTES). The two that carry
most of the weight:

- **E16** — one `claude -p --input-format stream-json` process: 12 s of empty stdin produced
  **zero** events and no `system/init`; a discarded `"ping"` message produced
  `system/init.model = claude-sonnet-5`, an `assistant` event with its own
  `assistant_request_id`, and **that turn's own `result`**; the process was **still alive**
  after that result; the real task was then delivered in the **same** process and the **same**
  `session_id`, producing a **second** `system/init`, a **different** `assistant_request_id`
  and a **second** `result`. (실제 관측. Scope: one process, two turns, `claude -p`.)
- **E17** — seven `-p` steps with identifiers preserved: two sessions both launched on
  `sonnet`, one changed to `haiku` through `--resume … --model haiku`, the other re-observed
  and **still** `sonnet`. Change isolation holds in that scope. Side finding: `--resume`
  **without** `--model` silently inherits the session's last selection. (실제 관측. Scope:
  `-p` one-shot processes, launch or `--resume`.)

**What is 미검증 and is not designed around as if it were known:** long-lived interactive
sessions (every measurement is `claude -p`), in-session `/model`, the `--settings` and
`--resume` paths' pre-delivery verifiability, Orca's own `worker-start --model` execution and
its `launch.requested` / `launch.effective` receipt fields (문서상 지원 only), and any
strictly independent evidence of the model a provider actually processed.

---

## 1. B1 — Request semantics: can "mint a ticket, then launch from its requested model" be
`driver_select_and_verify`?

### The contract as it stands

`scripts/orca_runtime_harness.py:788-798` closes the request vocabulary to one member and
states the exclusion in its own words:

> `launch_argv` is NOT a member: a model-pinned wrapper's argv was composed **before this
> session existed**, so it is not a request attributable to THIS attempt.

`ModelSelectionTicket` (`:906-968`) carries `terminal: str` (`:932`) — an **existing**
session — plus `token` (`:964`) and `stamp` (`:966-970`), the two ordinals the harness counts.
`MODEL_SELECTION_OBSERVATION_METHODS` (`:826`) is likewise one fake-only member.

### Recommendation (conditional on D2)

**Treat "this attempt's driver composes an argv from *this ticket's* `requested_model` and
the session comes into existence by that argv" as a request attributable to the attempt —
and say so by adding a *named* request method, not by stretching the existing one.** The
recommendation is **conditional on D2** (below) and must be withdrawn if D2 is declined; under
the contract as literally written today, the `-p` launch path is **not** eligible and there is
no other recommendable path (in-session `/model` is 미검증 and risks writing a global default).

The distinction that makes the conditional recommendation coherent — and the reason this is a
decision rather than a reading — is that `:789-792` excludes an argv *composed before the
session existed*, whereas here the argv is composed **after** the ticket exists, **from** the
ticket's own `requested_model`, by code running inside the driver's single `select_and_verify`
call. The contract's **field** (`terminal`), however, still presumes a session that already
exists, and its **comment** states the exclusion unconditionally. A design may not resolve
that by interpretation.

### Three separable changes, deliberately kept apart

| # | Change | What it is | Depends on |
|---|---|---|---|
| **B1-a** | **Vocabulary**: add `cli_launch_model_flag` to `MODEL_SELECTION_REQUEST_METHODS` (`:798`) and `stream_json_preflight_assistant` to `MODEL_SELECTION_OBSERVATION_METHODS` (`:826`) | the "visible, reviewable act" those comments already require | **D1** (names), and D2 for whether it may happen at all |
| **B1-b** | **Ticket**: let a ticket describe a session the attempt is about to **create**, rather than one it already holds — e.g. `terminal` becoming a slot the driver fills and the barrier then binds, or a second ticket shape for the create-and-select case | changes a frozen dataclass and the six-part freshness key (`:4105-4121`), which compares `observed_at_terminal` against the `terminal` the barrier was called with | **D2**; cannot be done without it |
| **B1-c** | **Execution-target binding**: who owns the process the selection created, and until when | a *lifetime* contract, not a vocabulary one | **D8** (§3) |

Conflating these is the main design hazard. B1-a alone, without B1-b, produces a driver that
can **name** its request method and still has nowhere to put a session it created. B1-b
without B1-c produces a ticket bound to a process whose owner is unspecified.

### Guarantees this recommendation would preserve

- The two-ordinal arithmetic is untouched: leg 1's `request_stamp` is drawn immediately
  before `spawn`, leg 2's `observe_stamp` immediately after the preflight turn's `result` is
  read and checked. E16 measured those two instants to be genuinely separable in one process.
- `selection_token` equality (`:964`), the closed-set membership checks, the six-part
  freshness key and leg (i)'s per-`(phase, role)` consistency check all keep their exact
  current meaning.
- The driver still owns **satisfaction** (`_verify_model_identity`'s docstring, `:3545-3550`):
  the harness implements no alias table, and a resolved value that does not satisfy the
  request must be reported as `mismatch` by the driver.

### Non-guarantees — stated so they are not read as covered

- **A launch argv is still not evidence.** It is evidence of what was asked of the operating
  system. Verification remains leg 2's job, and leg 2 must read the CLI's own report for the
  **preflight turn** (§2).
- **No local channel proves the provider's processing model.** Five independent CLI code paths
  agreeing (two `system/init` events, the `assistant` event, the `modelUsage` ledger, the
  `--debug api` dispatch line) is a **consistency** argument (추론), not independence.
- **It does not make the fake seam's success mean more.** The reference driver's leg 2
  re-reads the value its own leg 1 stored (`fake_adapter.py:291-293`): an echo.

### Change scope if approved

`scripts/orca_runtime_harness.py` (`:798`, `:826`, and the ticket shape at `:906-975` plus the
freshness key at `:4105-4121`), the mirrored copy under
`orca-worker-reviewer-orchestration/tools/`, `scripts/test_os49_vocabulary_locks.py` (whose
whole purpose is to make such an addition a visible, reviewed edit), and `docs/COMPATIBILITY.md`.
**None of this is needed by, or touched by, the common pair-preparation work.**

### Alternatives, with cost

| Alternative | Cost |
|---|---|
| **(i) Do nothing to the contract; no real `-p` driver.** | The honest status quo: model-aware routing stays fail-closed on every real runtime (`AGENT_MODEL_NOT_SUPPORTED` before a Run exists). Cost: OS-14's user-visible goal is not delivered at all. |
| **(ii) Keep `driver_select_and_verify` and make the driver change an *existing* session's model.** | Requires in-session `/model` (미검증, and its persistence branch writes global user settings — `evidence/06-model-slash-command-static.txt`) or `--resume --model`, which is a **new process** and therefore the same B1-b problem wearing different clothes. Cost: either an unverified mechanism or a disguised version of the same decision. |
| **(iii) Use Orca's own `worker-start --model`.** | 문서상 지원 only (`orca orchestration worker-start --help` on Orca 1.4.197 shows `--model`/`--effort`, and notes neither can combine with `--terminal`). Unexecuted. Cost: D7 must be decided and execution verified first; and `--terminal` incompatibility collides head-on with the prepared-session model this repository uses. |

---

## 2. B2 — Evidence and validity windows

### What is verifiable *before* the real task, and what is only confirmable after

| Channel | Pre-verifiable? | What it proves | Trap |
|---|---|---|---|
| `system/init.model` of the **preflight turn** | **yes**, but only after a turn starts (E16 M1: zero events on empty stdin) | the CLI's own canonical resolution of the alias (`sonnet` → `claude-sonnet-5`) | arrives in 0.025–0.057 s ⇒ **client-side resolution** (추론). **Never sufficient alone.** |
| `assistant.message.model` + `assistant_request_id` of the **same preflight turn** | **yes** | the model named on the response message object, with a per-turn identifier | the identifier's issuer was not observed; `message.model` being response-derived is 추론 |
| `result.modelUsage` keys | **no** — only at turn end | cost/token attribution | filled on **failed** turns too (budget exhaustion); a **successful** turn carried an *unrequested* auxiliary model key. **Equality tests produce false mismatches.** |
| `--debug api` dispatch line | yes (request side) | what the client dispatched | no response-side model in the log; the id there is a client-generated `x-client-request-id` |
| hook payloads | n/a | **no model field at all** | not a model channel |
| Orca receipt `launch.effective` | **미검증** | — | — |

### Recommendation

**`verified` requires all four of these, for the preflight turn, and nothing less:**

1. that turn's `system/init.model`, and
2. the **same turn's** `assistant` event `message.model`, both agreeing with
3. the canonical resolution of this ticket's `requested_model`, and
4. that `assistant` event's `session_id` equal to the caller-supplied `--session-id` uuid,
   with a non-empty `assistant_request_id` present.

Turns are distinguished by **`result` event order and the per-turn `assistant_request_id`** —
**never** by `num_turns`, which was `1` for both turns in E16. `modelUsage` is read only for
*containment* of the requested model and never for equality, and `subtype` /
`terminal_reason` are read **separately** from model evidence. **Absent evidence is
`model_selection_unverified`, always** — a killed process left zero bytes and no transcript.

### Authority revocation, reusing what already exists

The repository already splits **authority** from **history**, and the real driver must reuse
that split rather than invent one. `_stale_model_evidence` (`scripts/orca_runtime_harness.py:3359-3427`)
revokes authority for **one physical session** — clearing `_model_session_identity`,
`_model_pending_evidence` and every `_model_identity` record naming that terminal, plus the
ledger row's model cells (`:3419-3427`) — while deliberately **preserving** both history maps,
because erasing history turned the first drift refusal into a laundering step for the second
attempt. `requested_model` is left alone: it is the routing's declaration, not evidence.

| Event | Authority | History | Why |
|---|---|---|---|
| **Process restart / new OS process** | **gone by construction** — all model authority lives in process memory and `resume_run` restores only the delivery ledger (`:5673-5710`) | not available either; it is run-scoped process state | a successor must positively re-verify; it may never restore a stored verdict |
| **`--resume` of a session** | **revoke.** `--resume` without `--model` silently inherits the last selection (E17 S4/S6), so reading it verifies nothing anyone requested | preserve | reading an unrequested state is exactly what OS-49 forbids |
| **Timeout during `select_and_verify`** | **revoke for that session** — the existing handler normalizes any `Exception` to `model_selection_unverified` (`:3754-3790`) | preserve | the session may have been switched already; unknown is not unchanged |
| **Mismatch (resolved ≠ requested)** | **revoke for that session**; the driver reports `mismatch` | preserve | the session is now on *something*, and the record that named the old model has stopped describing it |
| **Refusal of the counterpart** | revoke **that** session only; **no pair-wide revocation** | preserve | the other session was not asked to select anything by this attempt |

**Preflight completion is never real-task completion.** They are two turns with two `result`
events and two different `assistant_request_id` values (E16). A driver that reports the
preflight `result` as the dispatch's settlement would be reporting a discarded turn as the
delivered work.

### Guarantees / non-guarantees

**Guarantees:** evidence is bound to one turn of one session identified by a
caller-chosen uuid; absence is never a pass; a stale or inherited selection cannot be read as
a verification; history survives a refusal, so drift legs (i) and (k) keep working.
**Non-guarantees:** nothing here proves the real-task turn ran on the verified model — only
that the turn immediately before it reported that model in the same process and session; a
driver that deliberately lies about leg 1 remains undetectable from inside the harness
(`:3551-3560` says so already); per-dispatch re-verification costs one extra preflight turn
(≈2 s measured) every time (**D5**).

### Change scope

A new driver module (its own file; provider-specific CLI syntax and stream parsing live
**only** there), plus B1-a's vocabulary members. **No** change to
`_stale_model_evidence`, to the authority/history split, or to any common module —
`orca_adapter.py`, `pause_store.py`, `pause_policy.py` and `launcher.py` learn nothing about
any provider's CLI.

### Alternatives, with cost

- **Post-hoc confirmation only** (deliver first, check the real-task turn's reported model
  afterwards): cheaper by one turn, and it observes the turn that actually matters — but it
  delivers before verification, which is precisely what the OS-49 barrier exists to prevent.
  **Not recommended**; recorded because it is the only variant that observes the real turn.
- **Both** (preflight gate *and* a post-hoc check of the real-task turn): strictly more
  evidence, at the cost of a second evidence path, a second failure vocabulary mapping and a
  new question about what to do when the post-hoc check disagrees after the work is done.
  Recommended **later**, not first.
- **`init.model` alone**: rejected. Client-side resolution read as a provider verdict.

---

## 3. B3 — Execution lifetime

### The three candidates

| | **(A) New non-interactive process per attempt: preflight → real task in the same process → exit** | **(B) One-shot** (single input, prompt at process creation) | **(C) Long-lived session reused across dispatches** |
|---|---|---|---|
| Measured? | **yes, mechanically** (E16: 1 process, 2 turns, same `session_id`, process alive after the preflight `result`) — scope `claude -p` | **yes** — this is what the shipped standalone Claude driver already does (`standalone_drivers.py:1249-1313`, prompt positional, **no** `--input-format`) | **미검증**; no CLI measurement in this project covers a long-lived interactive session |
| Pre-delivery verification | **possible** | **impossible** — the prompt is supplied at process creation, so there is no instant between selection and delivery | possible in principle, but the model is not bound to the session (E9/E17 S3) and `--resume` without `--model` inherits silently (E17 S4/S6) |
| Extra verification needed | turn attribution by `result` order + `assistant_request_id`; liveness of the held process between the two turns | none, because it verifies nothing | per-dispatch re-verification **and** a session-liveness proof; drift detection between dispatches |
| Contract difference from today | the driver holds a process **inside** one `select_and_verify` call and the task is delivered **after** it returns — so something must own that process across the boundary (**B1-c**) | none; and it cannot satisfy the barrier | ownership, cleanup and reuse-gate interaction all change; Orca's reuse gate already refuses evidence from a previous dispatch (`:2118-2168`) |

### Recommendation

**(A), explicitly scoped to `claude -p`, and explicitly conditional on D8.** It is the only
candidate in which a positive verification can precede delivery and for which the mechanism
has actually been measured. The scope limit is not decoration: Orca operates long-lived
terminal sessions, and **E16/E17 do not represent that**.

Two consequences must be accepted with it, not discovered later:

1. **The process outlives the driver call.** `select_and_verify` returns evidence; the task is
   delivered afterwards. Either the driver keeps the process and the engine gains a "deliver
   into this held process" step, or the process is re-entered — and re-entry is `--resume`,
   which inherits the last selection silently and therefore needs its own re-verification.
   Who owns that process, and until when, is **B1-c / D8**, undecided.
2. **Cost is one extra turn per attempt** (≈2 s measured locally, plus whatever a gateway
   adds). **D5** decides whether that is acceptable per dispatch.

**Guarantees:** one process = one selection act (the argv boundary), one caller-chosen
`session_id` echoed on every observation channel, verification strictly before delivery,
and a crash before verification leaves nothing delivered.
**Non-guarantees:** nothing about interactive long-lived sessions; no claim that the real-task
turn cannot drift from the preflight turn within one process (unmeasured in the adverse
direction); no claim that a held process survives arbitrary delay.

**Change scope:** a new driver module; the engine step that delivers into a held process
(**only if** D8 chooses that shape).

> **RETRACTION (OS-14 correction run).** This paragraph previously asserted that the held
> `claude -p` preflight -> task path attaches with **no change to the common preparation
> code**. That assertion is withdrawn: it is not established, and it was stated more
> firmly than the evidence supports.
>
> Further changes to the common preparation code **may** be required, and whether they are
> depends on two things this design has not settled: the **binding** between the process
> that was verified and the process that actually receives the task, and the **lifetime**
> of that binding across the interval between verification and delivery. Today preparation
> calls `verify_model_identity(...)` for each role, holds no process and assumes no
> liveness — so if D8 chooses the held-process shape, preparation would have to carry a
> process handle (or a binding to one) from the verification through to delivery, which is
> a process-ownership and cleanup contract it does not have. If instead D8 chooses
> re-entry, the re-entry is `--resume`, which inherits the last selection silently and
> needs its own re-verification at the delivery target; where that re-verification is
> placed may also touch preparation.
>
> **This is to be settled under D8 (§5), not here.** Nothing in this retraction implements
> a driver, changes a standalone switch, or makes a preparation change: it records that the
> question is open and that the earlier "no change needed" statement is not a finding.

**Alternatives and cost:** (B) is cheapest and already exists, but cannot satisfy the barrier
at all — it is the right choice only if the answer to OS-14 is "do not verify", which the
request rejects. (C) is the closest fit to how Orca actually runs agents and is the only
option that avoids per-dispatch process churn, but it requires re-measuring every independence
and drift property in the interactive scope **before** a design can rest on it.

---

## 4. B4 — Attachment target: Orca adapter vs OS-37 standalone

### What each side actually offers today

| | **Orca adapter** (`scripts/deterministic_workflow/orca_adapter.py`) | **OS-37 standalone** (`standalone_adapter.py`, `standalone_drivers.py`, `standalone_runtime.py`) |
|---|---|---|
| Model-selection barrier | **present**: Gate A at profile validation, Gate B before both delivery acts, the pre-pass `verify_model_identity` (`orca_runtime_harness.py:3457-3507`), pair admission (`:3686-3701`), the reuse gate's model conditions (`:2118-2168`), ledger model provenance | **absent**: no `MODEL_SELECTION_*`, no ticket, no evidence state, no pair admission (grep over `scripts/deterministic_workflow/` finds the vocabulary only in `orca_adapter.py`, `fake_adapter.py` and `contracts.py`) |
| Driver seam | `model_driver` injected once and threaded to both gates (`launcher.py:3021-3078`) | its drivers are **process/PTY** drivers (`graceful_hint`, turn-start evidence, completion types) — a different concept that shares the word |
| Does a real CLI already launch there? | no — it dispatches Orca Tasks | **yes** — `ClaudeDriver.argv` (`standalone_drivers.py:1249-1313`) is a measured, shipped `claude -p` launch |
| Capability honesty | declines `external_resume`; declines `model_selection_verified` with its two stated reasons (`orca_adapter.py:68-97`) | declares `external_resume` when the journal and identity fence are wired (`standalone_adapter.py:139-150`) |

### The ONE minimal recommendation

**Attach the real model-selection driver at the existing Orca-adapter driver seam — the
`model_driver` object `launcher.build_orca_adapter` already injects — and let that driver
*use* the standalone project's measured `claude -p` launch knowledge, without moving the
workflow onto the standalone adapter.**

Why this is the minimal change rather than the larger-sounding one:

- The Orca side already owns **every** verification mechanism the request demands. Attaching
  there adds **one object implementing one method** (`select_and_verify(ticket) -> ModelEvidence`)
  plus B1's vocabulary decision. Attaching to standalone would require re-creating the ticket,
  the ordinal window, the evidence states, the pair-admission rule and the reuse gate inside
  the standalone runtime — or shipping a path that selects a model and verifies nothing.
- The seam is already proven to be injectable end-to-end without touching the adapter, the
  harness, the graph or the launcher (`scripts/test_os49_bugfix_regressions.py:945-991` drives
  the real `build_orca_adapter` with a substituted process boundary).
- The provider-specific half stays in one file. That is the C6 rule the common work also
  obeys: no provider CLI syntax and no stream-event parser in common logic.

**Standalone-only is never Orca integration.** Were the driver attached to the standalone
adapter instead, the Orca adapter would still declare no `model_selection_verified`, a
model-declaring profile on the Orca path would still be refused at Gate A before a Run
exists, and no Orca-dispatched Worker/Reviewer pair would be model-verified. Reporting that as
OS-14 integration would be false.

**Guarantees:** the fail-closed default is unchanged (no driver ⇒ `AGENT_MODEL_NOT_SUPPORTED`
at Gate A, before any Run); no automatic fallback exists anywhere — a driver that cannot
verify refuses, and nothing silently substitutes a weaker path; the attachment point stays
**one** object, so a later `/model`-based driver (or an Orca `worker-start --model` driver,
D7) enters at the same place behind the same interface, with no second integration path and
no behavioural fork in the engine.
**Non-guarantees:** that the locally measured launch works in the company environment (§7);
that a verified Orca-adapter dispatch exists at all until B1/D2 are settled; that the
standalone path gains model verification — it does not, and this document does not propose
giving it one.

**Change scope:** one new driver module + its tests, B1-a's two vocabulary members,
`docs/COMPATIBILITY.md`, and the mirror copies under
`orca-worker-reviewer-orchestration/tools/`. **No** change to `orca_adapter.py`,
`pause_store.py`, `pause_policy.py`, `standalone_*` or the common preparation code.

**Alternatives, with cost:**

| Alternative | Cost |
|---|---|
| Attach to OS-37 standalone | Re-implements the entire OS-49 barrier in a second runtime, or verifies nothing. Its one real advantage — a measured `claude -p` launch already lives there — is obtainable without the move, by reusing the same launch knowledge inside an Orca-side driver. |
| Attach to both | Two verification implementations free to disagree, which is the exact defect `_counterpart_model_identity`'s docstring (`:3339-3344`) was written to prevent, one layer up. |
| Wait for Orca `worker-start --model` (D7) | Zero code now, and it may be the cleanest end state — but it is 문서상 지원 only, unexecuted, and its documented `--terminal` incompatibility conflicts with the prepared-session model this repository uses. Needs execution verification first, at Coordinator level. |

---

## 5. Where a real-driver decision is genuinely coupled to the common preparation code

The common pair-preparation work (implemented separately) is **independent of D1–D8**: it
runs the existing reference driver through the existing seam and decides nothing about any
real provider. Three boundaries are nevertheless genuinely coupled, and each has an
alternative that keeps the common work independent — which is the alternative the common work
takes.

| Coupled decision | The exact code boundary | Alternative that keeps the common work independent |
|---|---|---|
| **D2** — does launching with the ticket's requested model count as this attempt's request? | the single `create_fake_terminal(...)` call inside the adapter's preparation step, where a model-pinned launch would have to replace a model-neutral one. Note `create_fake_terminal` records `requested_model` as a **separate** ledger field and never concatenates a model into `--command` (`orca_runtime_harness.py:2869-2890`) | **(taken)** keep preparation model-**neutral** and let the driver make its request against the session preparation created. Needs no D2. The alternative — a `launch_model=` keyword gated on D2 — also changes the reuse gate's condition-2 key, at a cost of one new seam plus one new reuse-gate case |
| **D8** — execution lifetime | the `verify_model_identity(...)` call inside preparation, and whether the **same** process must then run the task | **(taken, for the reference driver only)** one self-contained driver call per role: preparation holds no process and assumes no liveness. The alternative — preparation hands the driver a process it must keep alive until delivery — gives preparation a process-ownership and cleanup contract it does not have today. **OS-14 correction run:** the earlier claim that the held `claude -p` path (§3, option (A)) attaches with no change to this code is **retracted**. Whether a change is needed depends on the **binding** between the verified process and the actual task recipient and on the **lifetime** of that binding, and that is to be settled **here, under D8** — not asserted in §3 |
| **Proof-grade re-adoption of a prepared session across processes** | `pause_policy.resolve_prepared_terminal` and the prepared entry's `terminal_digest` | **(taken)** digest-proved adoption plus **mandatory re-verification** on every pass. A real driver may additionally need a **session-liveness** proof — is the agent process behind this terminal still the one that was verified? — which the durable record deliberately does **not** claim |

---

## 6. Still-undecided items

Each is stated as a decision with a recommendation, not as a bare permission request. **None
of them blocks the common pair-preparation work.**

### D1 — the two vocabulary member names

- **Recommendation:** `cli_launch_model_flag` (request) and `stream_json_preflight_assistant`
  (observation). Both name the *mechanism*, not the provider, matching the existing members'
  style.
- **Guarantees:** the addition is a visible, reviewable edit — the closed sets' own comments
  (`:796-797`, `:824-825`) say that is the intent — and
  `scripts/test_os49_vocabulary_locks.py` forces it to be reviewed.
- **Non-guarantees:** a name does not make a mechanism honest; it only lets an honest one be
  named.
- **Change scope:** `:798`, `:826`, their mirror, the vocabulary lock test, `COMPATIBILITY.md`.
- **Alternatives + cost:** provider-named members (`claude_launch_model_flag`) — cheap now,
  but invites a per-provider branch in common logic, which C6 forbids. Or no members at all —
  then no real driver can name a request method, and the refusal stands.
- **Owner:** implementation decision, downstream of D2.

### D2 — does "launch a session with this ticket's requested model" count as this attempt's request? **(the blocking one)**

- **Recommendation:** **yes**, narrowly: an argv composed **after** the ticket exists, **from**
  that ticket's `requested_model`, by the driver, inside the attempt — and **no** for a
  pre-composed model-pinned wrapper, which is what `:789-792` actually excludes. Record the
  distinction in the comment rather than deleting the exclusion.
- **Guarantees:** keeps the ordinal arithmetic, the token equality and the freshness key
  exactly as they are; keeps a pre-composed wrapper argv inadmissible.
- **Non-guarantees:** does **not** make an argv evidence of a resolution; leg 2 still has to
  observe (§2). Does not decide B1-b's ticket shape.
- **Change scope:** `:789-792` (comment + its premise), the ticket shape (`:906-975`), the
  freshness key (`:4105-4121`), mirrors, locks, `COMPATIBILITY.md`.
- **Alternatives + cost:** decline ⇒ §1's recommendation is withdrawn and **no** recommendable
  real-driver path exists under the current contract (cost: OS-14's goal is unreachable as
  specified). Or accept broadly, admitting any model-pinned argv ⇒ cheap, and it readmits
  exactly the "evidence of what was asked of the OS" that the exclusion exists to keep out.
- **Owner:** **user / contract owner. Required before any real-driver work starts.**

### D3 — the independence standard for leg 2

- **Recommendation:** the four-part conjunction of §2 (preflight `init.model` **and** the same
  turn's `assistant.message.model` **and** canonical agreement with the request **and**
  `session_id` equality with a present `assistant_request_id`). Write it down as the standard,
  and write down that the reference driver's echo (`fake_adapter.py:291-293`) does **not** meet
  it and is never cited as a model verification.
- **Guarantees:** the strongest locally obtainable standard; no single-channel verdict.
- **Non-guarantees:** **not** server-independent. The issuer of `assistant_request_id` was not
  observed (추론), and `message.model` being response-derived is also 추론.
- **Change scope:** the driver module and this document; no common code.
- **Alternatives + cost:** `init.model` alone (cheap, wrong — client-side resolution);
  `modelUsage` equality (wrong — a successful turn carried an unrequested auxiliary model key,
  so equality produces false mismatches).
- **Owner:** contract decision, with D2.

### D4 — where the two ordinals are drawn

- **Recommendation:** leg 1 immediately **before** `spawn`; leg 2 immediately **after** the
  preflight turn's `result` is read and the four checks pass. E16 measured those instants to
  be separable in one process.
- **Guarantees:** request strictly precedes observation, arithmetically, as the harness
  already counts it.
- **Non-guarantees:** does not decide who owns the held process afterwards (D8/B1-c).
- **Change scope:** driver module only.
- **Alternatives + cost:** drawing both after the turn — destroys the ordering proof;
  drawing leg 1 before argv composition — indistinguishable in effect, and less obviously
  tied to the act it attests.
- **Owner:** implementation decision.

### D5 — per-dispatch re-verification cost

- **Recommendation:** **accept it.** Re-verify every dispatch. E9/E17 S3–S6 justify it
  empirically: a session's model is not permanently bound, and `--resume` without `--model`
  inherits the last selection silently — so a verification from an earlier dispatch describes
  nothing about this one.
- **Guarantees:** no evidence is carried across dispatches; the reuse gate's existing
  `MODEL_IDENTITY_STALE` / `MODEL_IDENTITY_UNVERIFIED` refusals (`:2146-2160`) stay meaningful
  rather than becoming conservative decoration.
- **Non-guarantees:** cost is real — one extra preflight turn (≈2 s locally) per dispatch,
  more through a gateway.
- **Change scope:** operational policy; no code change beyond the driver.
- **Alternatives + cost:** verify once per session (cheaper; accepts silent drift, which is
  the exact defect the staling rules exist to prevent); verify once per run (cheaper still,
  strictly worse for the same reason).
- **Owner:** operations policy.

### D6 — mapping real failure modes onto the existing closed failure vocabulary

- **Recommendation:** map each measured failure explicitly, and add **no** new member:
  unknown model (`[claude-code:unrecognized_model]`, exit 1, empty `modelUsage`) ⇒
  `model_selection_unverified`; not-logged-in ⇒ `model_selection_unsupported` (a capability
  failure, not a model one); evidence lost to a killed process ⇒ `model_selection_unverified`;
  budget-exhausted turn ⇒ read `subtype` / `terminal_reason` separately and refuse as
  unverified; resolved ≠ requested ⇒ `mismatch`, reported by the driver. Write down that
  `modelUsage` **equality** must never be the basis of `mismatch`.
- **Guarantees:** the closed failure vocabulary (`:831-852`) gains nothing; every refusal is an
  existing name.
- **Non-guarantees:** a gateway may produce failure shapes none of these measurements covers
  (§7).
- **Change scope:** driver module + `COMPATIBILITY.md`.
- **Alternatives + cost:** new members per failure kind — more diagnostic precision, at the
  cost of widening a deliberately closed vocabulary and editing its locks.
- **Owner:** implementation decision.

### D7 — is Orca's own `worker-start --model` in scope?

- **Recommendation:** **not yet.** Keep it out of the first driver, and keep the attachment
  point (§4) general enough that it could become a second driver behind the same interface.
  Verify its execution and the shape of its `launch.requested` / `launch.effective` receipt
  fields at Coordinator level first.
- **Guarantees:** no design here depends on those fields.
- **Non-guarantees:** the flag's existence on Orca 1.4.197 is observed; its **behaviour** is
  문서상 지원 and unexecuted — and its own help text says `--model` cannot combine with
  `--terminal`, which is how this repository dispatches to prepared sessions.
- **Change scope:** none now.
- **Alternatives + cost:** make it the primary path — potentially much less code, on an
  unverified mechanism that conflicts with prepared-session delivery.
- **Owner:** scope decision.

### D8 — `-p` one-shot vs long-lived interactive target **(required before real-driver work)**

- **Recommendation:** target **`claude -p`** first (§3 candidate A), and state the scope limit
  in the driver's own docstring. If the long-lived interactive terminal is the real target,
  **re-measure** independence, change isolation and per-dispatch re-verification in that scope
  before designing further.
- **Guarantees:** every claim made stays inside the scope actually measured.
- **Non-guarantees:** `-p` results say nothing about long-lived interactive sessions — which
  is what Orca actually operates. This is the single largest gap between this design and
  production reality.
- **Change scope:** decides whether §3's "held process" step exists at all, and therefore
  B1-c.
- **Alternatives + cost:** target interactive first — closer to production, but requires a new
  measurement campaign (including in-session `/model`, which is 미검증 and risks writing a
  global default) before anything can be designed.
- **Owner:** **scope decision; required before real-driver work starts.**

### The in-session `/model` enablement action the user would have to take

`/model` was **not** run. The persistence branch was confirmed **statically** — it writes
`userSettings {model}` — and the global `~/.claude/settings.json` has **no** top-level `model`
key today and was byte-identical across the whole analysis run. To make that experiment safe,
**one** of these is needed from the user:

- **(a) Recommended:** an experiment-only Claude account/authentication, logged in once inside
  a `CLAUDE_CONFIG_DIR`-isolated environment. Isolation itself was measured to work; what does
  not carry over is authentication (the isolated run answered `Not logged in`).
- **(b)** explicit permission to change the global `~/.claude/settings.json` default, plus
  approval of the restore (removing the `model` key again, since none exists now).

**Guarantees either way:** none about `/model` until it is actually run.
**Non-guarantees:** the static strings do not reveal which user action selects the
"this session only" branch. **Cost of doing nothing:** `/model` stays 미검증 and cannot be the
basis of any driver — which is exactly why this design does not use it.

---

## 7. Company (PortKey/GLM) environment — not discharged by anything here

Listed as a pointer, not as work: alias resolution vs plain echo (a plain echo weakens leg 2
in exactly the way this design forbids); `modelUsage`'s `provider` / `canonicalModel` /
`costBasis` values through a gateway; whether `assistant_request_id` survives a gateway at all
(**the single most important check**, since leg 2 depends on it); whether `system/init.model`
is the gateway-resolved value or the request value; the shape of an unknown-model rejection
(local was a tagged stderr line, exit 1, empty `modelUsage`, `duration_api_ms = 0`);
authentication and isolation style (an API-key path may finally make `/model` safely testable);
the two GLM models' concurrent-session independence and change isolation, re-measured;
whether `--safe-mode --setting-sources ''` erases gateway configuration — if it does, the
isolation set this project relies on must be redesigned; and how the unrequested auxiliary
model call appears through a gateway.

## 8. What this document does not claim

- It does **not** implement a driver, and no code in the tree changes because of it.
- It does **not** claim OS-14 is complete, that real Claude model selection works, or that any
  real-model verification has happened.
- It does **not** claim anything about the company environment.
- It does **not** treat the reference (fake) driver's success as evidence about any provider's
  model: its observation leg re-reads the value its own request leg stored.
- It does **not** decide D1–D8. Items marked **user / contract owner** are the user's.
