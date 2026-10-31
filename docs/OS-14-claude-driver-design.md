# OS-14 — the real Claude Code model-selection driver: DESIGN ONLY

**This document is a design. Nothing in it is implemented.** No driver here exists in the
tree, no vocabulary member is added by it, no `/model` experiment was run for it, and no
company (PortKey/GLM) environment was touched. It is the written form of the four design
items OS-14 owes, so that the *common* Worker/Reviewer pair-preparation work — which is
implemented separately, against the existing reference (fake) driver — could proceed without
pre-deciding any of them. **Status at merge base `7ec657f`: that common work is MERGED**
(PR #38, TRACK A), and it did proceed without deciding any of D1–D8. What is still
unimplemented is the real driver (TRACK B), which is what §3a, §6 and §9 are about.

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

Two further measurements were taken **in the OS-14 design run** (`run_fad8a8a816e7`), on
the **installed** versions — `claude 2.1.260`, `orca 1.4.197`, confirmed this phase rather
than assumed from an older release. They exist because the execution-target question below
could not be answered from E16/E17, which measured two **back-to-back** turns and no wait.
Raw records and scripts: `artifacts/runs/run_fad8a8a816e7/evidence/`; summary
`evidence/14-probe-findings.md`; ledger `evidence/00-commands-run.md`. **4 model turns** of
an 8-turn budget.

- **P1** — one held `claude -p --input-format stream-json` process, three turns, one **90 s
  idle interval** between turn 1 and turn 2. 8 s of empty held stdin produced **zero**
  events; the 90 s idle produced **zero** events and the process stayed **alive**; turn 2
  ran **in the same process and the same `session_id`**, producing its own `system/init`
  (`model=claude-sonnet-5`), its own `assistant` `request_id` and its own `result`; turn 3
  likewise; the process was still alive after turn 3 and exited **0** when stdin was
  closed. `num_turns` was `1` on all three turns. `--replay-user-messages` echoed each
  delivered message back on stdout as a session-bound `user` event. **Turns 2 and 3**
  carried an **unrequested** `claude-haiku-4-5-20251001` key in `modelUsage` on
  `subtype=success`. A `rate_limit_event` event type appeared, at a **varying** position.
  (실제 관측. Scope: one process, three turns, one 90 s wait, `claude -p`.)
- **P2** — the same process shape, `SIGKILL`ed between the preflight turn and the next: the
  driver's next stdin write raised **`BrokenPipeError` / `EPIPE`**, no second turn occurred,
  and the four turn-1 events the driver had already read remained in its memory while
  describing a session that no longer existed. (실제 관측. Scope: one process, one turn, one
  kill.)

A third set of measurements was taken in the **minimal-feasibility run**
(`run_fd87428a21f0`, ANALYSIS PASS WITH NOTES, Final Adversarial Review PASS WITH NOTES), on
the same installed versions — `claude 2.1.260`, `orca 1.4.197`, macOS `Darwin 25.5.0`. It
spent **2 of 4** experimental input turns. The committable, secret-free summary a remote
reviewer can read without local artifacts is **`docs/OS-14-feasibility-evidence-summary.md`**;
the raw records live in `artifacts/runs/run_fd87428a21f0/evidence/` and are **not** committed.
Two groups, labelled so every later section can cite one line instead of restating it:

**Stage 1 — five isolated probes, stdlib only, real processes, watchdog-bounded, 0 model
turns.** Each one implements the **documented** state machine of §3a.12/§3a.13 and is a
**toy model**, never a production reproduction: no shim, no channel, no bind, no lease and no
relay exists in this tree (the mapping is recorded in `evidence/40-probe-findings.md` §A).
What they establish is the **internal consistency of this specification**, which is what
F-005/F-006/DR-01…DR-03 are about.

| # | What was measured | Result |
|---|---|---|
| **X-1** | the bind deadline's origin, 4 client behaviours × 2 origins, `bind_deadline_ms = 1200`, watchdog 6000 ms; the measurement is the **actual exit**, never a comparison of two durations | under the documented origin (the `hello` write) the shim **never exits by itself** when no client connects (killed at 6026 ms) **and** when the peer dies before the `hello` write completes (killed at 6005 ms); the endpoint survives both times. A **listener-readiness** origin self-exits `rc=3` at ~1.23 s and unlinks the endpoint in **all four** cases. `child_spawns == 0` in every case under **both** origins |
| **X-2** | three separate `bind` concerns, 2 rules | same-connection identical resend: idempotent, **1** child. Another lease's token: `bind_unauthenticated`, 0 children. **Same lease, fresh incarnation, byte-identical captured `bind`: ACCEPTED, 1 child spawned** under the documented memory-only rule; **refused `stale_incarnation`, 0 children** under a per-incarnation freshness value. Also: the derived endpoint path is reused by the second incarnation, and no `hello` ever carried a `terminal` field |
| **X-3** | the §3a.2 order with the current adapter's call order, 3 target-lifetime policies | `release` **after settlement** and `release` **of the settled role only** both refuse the Reviewer round at its **pre-pass of the worker role** (`PAIR_PREPARATION_MODEL_UNOBSERVED`); holding the lease and channel until the **gate round** closes completes both rounds, with a **new** ticket drawn each time |
| **X-4** | §3a.13's admission over 5 target classes × 3 rules × **both** possible `worker-start` receipts (U-3 is 미검증) | a **genuine legacy** target behaves as the document claims. A **hosted** target whose channel is lost **after** Gate B takes the legacy path silently; on the `agent_unconfigured` receipt that spends a `tui-idle` timeout, **creates a Dispatch**, writes the prompt into the **shim's** stdin, and **records an attached row**, for a task the CLI child never receives. A recognition/deliverability split names both that case and a revoked-lease case. **Scope, stated because §3a.13 goes further than this probe:** all five classes are listed in the probe source, and every *hosted* one carried the capability conjunct **true** — the single capability-false class also had **no** launch lease. So **"a hosted launch cell exists but the driver's capability is incomplete" was never tested**, and §3a.13's refusal for that case is **추론** from DR-02's rule, not a measured result |
| **X-5** | the two-hop relay, real processes, 5 injected faults, child drained to EOF before ground truth is read | hop 1 (driver → shim, outer frame) is **150/150** bytes; hop 2 (shim → child stdin) is **73/73**. Two faults give the driver an **identical** observation with **opposite** ground truth. A dead child produces **no error at all** on the driver's hop-1 write. One fault reported `child_write_bytes == payload_bytes` **and** a non-null `hop2_error`, because the write was buffered and the **flush** failed |

**Stage 2 — the real Orca link.** Executed by the **Coordinator** of that run, not by its
Worker; every number below was re-derived from the raw files and from a read-only runtime
query, and the raw file wins wherever a summary could disagree.

- **S-0** — `orca terminal send --text '<one stream-json line>' --enter` into a terminal whose
  stdin is a **TTY**: the **70 body bytes** arrived **byte-identical**, terminated by **one
  `\r` (0x0d)** and nothing else — 71 bytes received, `tty = true`, verdict
  `["cr_present", "exact_match"]`. No bracketed paste, no `ESC` rewriting. Bytes were
  compared, not screen appearance. (실제 관측.)
- **S-1a** — **direct PTY hosting of `claude -p` is not possible** in this configuration. With
  the terminal's own process passing its **TTY** stdin straight through
  (`stdin_isatty = true`, `stdin_mode = inherited`), `claude -p` exited `rc=1` with the single
  line `Error: Input must be provided either through stdin or as a prompt argument when using
  --print`, emitting **no `assistant` and no `result` event** ⇒ **0 turns**. (실제 관측,
  negative result. Scope: this macOS build, `claude 2.1.260`, `claude -p --input-format
  stream-json` whose stdin **is** the Orca PTY.)
- **S-1b** — the **supervisor relay shape** works. The terminal's own process was a disposable
  launcher (pid **81932**, ppid 81876) reading the PTY and writing into the CLI child's
  **pipe** stdin (`stdin_mode = unframed_relay`); the CLI child was pid **81950**, and no
  `child_exit` was ever logged. One preflight turn: **87** bytes forwarded, the
  `--replay-user-messages` echo returned the exact delivered content, `system/init.model` **and**
  `assistant.message.model` both reported `claude-haiku-4-5-20251001` (the fully-qualified argv
  id), `result success`, `num_turns 1`, 1199 ms, and `session_id` was identical on **all 23**
  events of the stream and equal to the argv `--session-id`. `system/init.apiKeySource = "none"`
  and `permissionMode = "default"`. (실제 관측. **1 turn**.)
- **S-2** — an experimental Task and Dispatch were created against that terminal **without**
  `--inject`: `capability_hash = null`, `worker-show` → `state = unsupervised`,
  `stage = context_only`. `--dry-run` / `--return-preamble` / `--inject` were therefore never
  exercised and stay **미검증 (U-4)**. (실제 관측, **0 turns**.)
- **S-3** — that Dispatch's prompt was delivered **into the same process** (**494** bytes
  forwarded to child pid **81950**, the same pid as S-1b, with **no `child_exit` between the
  two forwards**), the process ran two `Bash` tool calls (`orca --version` → `1.4.197`, then
  one `orca orchestration send --type worker_done … --outcome succeeded --from <its own
  terminal>`), and **that exact Dispatch settled from that process's own report**: the runtime
  records `status = completed`, `result.provenance = worker_report`, and
  `reportedBy = completedBy = <that terminal>`. The Coordinator sent no `worker_done`. The
  dispatch row also carries **`capability_revoked_at == completed_at`** — **Orca itself revokes
  the dispatch capability at settlement**. (실제 관측. **1 turn**.)
- **S-4** — the **turn ↔ `request_id` correspondence**, derived from the raw stream rather than
  assumed. Two input turns, 23 events, **7** `assistant` events and **3** distinct
  `request_id`s: turn 1 = **one** `request_id` across **two** `assistant` events (a `thinking`
  block and a `text` block, one `message.id`); turn 2 = **two** `request_id`s across **five**
  `assistant` events, because a tool round-trip opens a second API request inside **one** input
  turn. **There is no one-`request_id`-per-turn rule and no one-`assistant`-event-per-turn
  rule**, and the design must not assume either (D3 conjunct 4). Also measured here:
  `request_id` lives at the **event's top level**, not on `message`; `rate_limit_event`
  appeared at two different positions (once **before** the replayed `user` echo); and three
  further event shapes the earlier design does not name appeared —
  `system/thinking_tokens`, `system/commands_changed` (both **after** the final `result`), and
  `assistant` events whose only content block is `thinking`. (실제 관측.)

**What the feasibility run proves, and what it does not.** It answers the one question that
decides the shape — *can an Orca task be delivered to the very execution target a model was
observed on, and can that exact Dispatch's completion be collected back?* — **yes**, and it
**refutes** the simplest alternative (S-1a). Four limits are load-bearing and are not rounded
away anywhere in this document:

1. **It is prototype wiring, not driver support.** The hosted process was launched by a
   Coordinator-created terminal running a disposable launcher, dispatched **without**
   `--inject` and **without** `worker-start` (`state = unsupervised`). Nothing in
   `build_orca_adapter`, `_prepare_pair` or `start_worker` took part, and **no** Gate A/B
   evidence was produced.
2. **The delivery route it exercised is NOT §3a.12's channel.** The bytes travelled
   `terminal send` → the launcher's PTY stdin → CR→LF → the child's pipe stdin. The
   `AF_UNIX` endpoint, the three-frame bootstrap, the launch lease and Gate B were **not**
   exercised at all; S-1b/S-3 are therefore **not** a verification of the
   AF_UNIX/lease/Gate B protocol, which stays **문서상 설계** (§3a.12, §8).
3. **The hold is measured to 68.8 s**, the interval between the two `forwarded_to_child_stdin`
   rows (1793457047.01 − 1793456978.17). That is **shorter** than P1's 90 s and far shorter
   than a real Worker turn, so §3a.8's and D5's hold question is **not** retired.
4. **The model claim is the CLI-reported one.** `apiKeySource = "none"` and two agreeing
   channels are the limit; **what the API actually served is 미검증**. And the launcher→child
   ppid reading (81950 → 81932) was taken while both pids existed and is **no longer
   re-verifiable** — the claim is carried instead by pid continuity across both forwards with
   no `child_exit`, the single launcher/child/terminal topology, and the recomputed replay byte
   counts.

**What is 미검증 and is not designed around as if it were known:** long-lived interactive
sessions (every measurement is `claude -p`), in-session `/model`, the `--settings` and
`--resume` paths' pre-delivery verifiability, Orca's own `worker-start --model` execution and
its `launch.requested` / `launch.effective` receipt fields (문서상 지원 only), **§3a.12's
`AF_UNIX` channel, its bootstrap and the launch lease — none of which any run has executed**,
**U-3** (`worker-start` against such a terminal) and **U-4** (`--inject` /
`--return-preamble`) and **U-5** (`terminal read` fidelity), a hold longer than the 90 s P1
measured (stage 2 reached 68.8 s), and any strictly independent evidence of the model a
provider actually processed.

**Line references.** Every `orca_runtime_harness.py` / `orca_adapter.py` line number in this
document is against merge base **`7ec657f`** (PR #38 merged). Sections written before that
merge carry pre-#38 numbers where they were not re-checked; the sections added or corrected
in the OS-14 design run (§1's Recommendation, §2's channel table / Recommendation / restart
row, §3a — including the new §3a.12 and §3a.13 — §6, §9 and the change-scope paragraphs) are
re-checked against `7ec657f`.

---

## 1. B1 — Request semantics: can "mint a ticket, then launch from its requested model" be
`driver_select_and_verify`?

### The contract as it stands

`scripts/orca_runtime_harness.py:835-844` closes the request vocabulary to one member and
states the exclusion in its own words:

> `launch_argv` is NOT a member: a model-pinned wrapper's argv was composed **before this
> session existed**, so it is not a request attributable to THIS attempt.

`ModelSelectionTicket` (`:951-987`) carries `terminal: str` (`:978`) — an **existing**
session — plus `token` (`:985`) and `stamp` (`:987`), the two ordinals the harness counts.
`MODEL_SELECTION_OBSERVATION_METHODS` (`:872`) is likewise one fake-only member.

### Recommendation (conditional on D2) — **corrected**

An earlier edition of this section recommended treating *"this attempt's driver composes an
argv from this ticket's `requested_model`, inside its single `select_and_verify` call"* as the
attempt's request. **Review F-001 showed that is unsatisfiable on the recommended lifecycle**,
and the recommendation is therefore replaced rather than re-affirmed: the terminal is created
at preparation step 4d, **before** any ticket for it exists, and Gate B's ticket is minted
when no `--model` act occurs at all (§3a.2). No driver call contains a launch.

**The replacement recommendation: a launch-scoped request, minted by the harness and durably
bound before the terminal is created, which every later driver call must cite and
revalidate — never re-pretend.** It is specified, with both options and the full binding and
revocation rules, in **§6 D2 (option B)**, and it is **승인 대기**: it widens the request
leg's meaning from "this attempt asked" to "this attempt exercised an unrevoked launch this
run made and recorded", and it replaces one locked contract value.

What survives from the earlier reading, and is still the reason this is a decision rather
than an interpretation: `:835-838` excludes an argv *composed before the session existed*,
and a lease is composed **for** this session, by this run, after the routing declared the
model, and is revocable — so the exclusion's actual target stays excluded. What does **not**
survive is the claim that the composing happens inside a driver call. The contract's **field**
(`terminal`) presumes a session that already exists, which under option B remains true and is
why option B needs **no** ticket-shape change; option A would change both the field and the
six-part freshness key. A design may not resolve that by interpretation.

### Three separable changes, deliberately kept apart

| # | Change | What it is | Depends on |
|---|---|---|---|
| **B1-a** | **Vocabulary**: add `cli_launch_model_flag` **and** `cli_launch_lease_revalidation` to `MODEL_SELECTION_REQUEST_METHODS` (`:844`) and `stream_json_preflight_assistant` to `MODEL_SELECTION_OBSERVATION_METHODS` (`:872`) | the "visible, reviewable act" those comments already require. **Two** request members, because the launch act and the revalidation of a launch are different acts (D1) | **D1** (names), and D2 for whether it may happen at all |
| **B1-b** | **Ticket**: let a ticket describe a session the attempt is about to **create**, rather than one it already holds — e.g. `terminal` becoming a slot the driver fills and the barrier then binds, or a second ticket shape for the create-and-select case | changes a frozen dataclass and the six-part freshness key (`:4347-4366`), which compares `observed_at_terminal` against the `terminal` the barrier was called with. **Needed only by D2 option A**, which §6 D2 does not recommend; option B leaves the ticket shape alone and adds the **launch lease** instead | **D2**; cannot be done without it |
| **B1-c** | **Execution-target binding**: who owns the process the selection created, and until when | a *lifetime* contract, not a vocabulary one | **D8** (§3) |

Conflating these is the main design hazard. B1-a alone, without B1-b, produces a driver that
can **name** its request method and still has nowhere to put a session it created. B1-b
without B1-c produces a ticket bound to a process whose owner is unspecified.

### Guarantees this recommendation would preserve

- The two-ordinal arithmetic is untouched: leg 1's `request_stamp` and leg 2's
  `observe_stamp` are still the ticket's two ordinals, in order, drawn exactly twice. Under
  option B leg 1 is drawn immediately before this call's **preflight input** (not before a
  `spawn`, which no driver call performs), and a **third** ordinal — `launch_stamp`, drawn
  once per lease from the same counter — attests the launch itself (D4). E16 measured the
  input and `result` instants to be genuinely separable in one process.
- `selection_token` equality (`:985`), the closed-set membership checks, the six-part
  freshness key and leg (i)'s per-`(phase, role)` consistency check all keep their exact
  current meaning.
- The driver still owns **satisfaction** (`_verify_model_identity`'s docstring, `:3792-3796`):
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
  re-reads the value its own leg 1 stored (`fake_adapter.py:291-295`): an echo.

### Change scope if approved

`scripts/orca_runtime_harness.py` (`:844`, `:872`, and the ticket shape at `:951-987` plus the
freshness key at `:4347-4366`), the mirrored copy under
`orca-worker-reviewer-orchestration/tools/`, `scripts/test_os49_vocabulary_locks.py` (whose
whole purpose is to make such an addition a visible, reviewed edit), and `docs/COMPATIBILITY.md`.

**Scoped claim, corrected (OS-14 design run).** What is listed above — the two vocabulary
members and the ticket/freshness-key shape — is not *needed by* the common
pair-preparation work, which runs the reference driver through the existing seam and
decides none of D1–D8. It does **not** follow that attaching a real driver needs no
common change: an earlier edition of this section read that way, and §3a now names the
common preparation/delivery changes the recommended execution-target shape does require.
B1-a alone touches no common module; **B1-b and B1-c do**, through
`OrcaAdapter._prepare_pair` and the delivery rung. See the §3 RETRACTION and §3a.

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
| `system/init.model` of the **preflight turn** | **yes**, but only after a turn starts (E16 M1: zero events on empty stdin) | the model id the CLI itself reports for that turn (an alias request such as `sonnet` was observed arriving as `claude-sonnet-5` — one version-scoped observation, **not** a resolution rule: see D3's `exact_equality.v1`) | arrives in 0.025–0.057 s ⇒ **client-side resolution** (추론). **Never sufficient alone.** |
| `assistant.message.model` + `assistant_request_id` of the **same preflight turn** | **yes** | the model named on the response message object, with a per-turn identifier | the identifier's issuer was not observed; `message.model` being response-derived is 추론 |
| `result.modelUsage` keys | **no** — only at turn end | cost/token attribution | filled on **failed** turns too (budget exhaustion); a **successful** turn carried an *unrequested* auxiliary model key. **Equality tests produce false mismatches.** |
| `--debug api` dispatch line | yes (request side) | what the client dispatched | no response-side model in the log; the id there is a client-generated `x-client-request-id` |
| hook payloads | n/a | **no model field at all** | not a model channel |
| Orca receipt `launch.effective` | **미검증** | — | — |

### Recommendation

**`verified` requires all four of these, for the preflight turn, and nothing less:**

1. that turn's `system/init.model`, and
2. the **same turn's** `assistant` event `message.model`, both **string-equal** to
3. this ticket's `requested_model` **itself**, which must therefore be a fully qualified
   model id — no alias, no normalisation (D3's `exact_equality.v1`; the earlier edition said
   "the canonical resolution of", which review F-004 showed is circular), and
4. that `assistant` event's `session_id` equal to the caller-supplied `--session-id` uuid,
   with a non-empty `assistant_request_id` present.

Turns are distinguished by **`result` event order and the per-turn `assistant_request_id`** —
**never** by `num_turns`, which was `1` for both turns in E16. `modelUsage` is read only for
*containment* of the requested model and never for equality, and `subtype` /
`terminal_reason` are read **separately** from model evidence. **Absent evidence is
`model_selection_unverified`, always** — a killed process left zero bytes and no transcript.

### Authority revocation, reusing what already exists

The repository already splits **authority** from **history**, and the real driver must reuse
that split rather than invent one. `_stale_model_evidence` (`scripts/orca_runtime_harness.py:3584-3652`)
revokes authority for **one physical session** — clearing `_model_session_identity`,
`_model_pending_evidence` and every `_model_identity` record naming that terminal, plus the
ledger row's model cells (`:3645-3652`) — while deliberately **preserving** both history maps,
because erasing history turned the first drift refusal into a laundering step for the second
attempt. `requested_model` is left alone: it is the routing's declaration, not evidence.

| Event | Authority | History | Why |
|---|---|---|---|
| **Process restart / new OS process** | **gone by construction** — all model authority lives in process memory and `resume_run` restores only the delivery ledger (`orca_runtime_harness.py:5914-5951`, `_restore_delivery_ledger_once`) | **DURABLE, and mandatory** — see the correction below | a successor must positively re-verify; it may never restore a stored verdict, and its re-verification is checked **against** the durable history |
| **`--resume` of a session** | **revoke.** `--resume` without `--model` silently inherits the last selection (E17 S4/S6), so reading it verifies nothing anyone requested | preserve | reading an unrequested state is exactly what OS-49 forbids |
| **Timeout during `select_and_verify`** | **revoke for that session** — the existing handler normalizes any `Exception` to `model_selection_unverified` (`:4003-4028`) | preserve | the session may have been switched already; unknown is not unchanged |
| **Mismatch (resolved ≠ requested)** | **revoke for that session**; the driver reports `mismatch` | preserve | the session is now on *something*, and the record that named the old model has stopped describing it |
| **Refusal of the counterpart** | revoke **that** session only; **no pair-wide revocation** | preserve | the other session was not asked to select anything by this attempt |

**Correction (OS-14 design run): the HISTORY column of the restart row.** An earlier
edition said role/session history was "not available either; it is run-scoped process
state". That conflated two different things, and PR #38 made the distinction load-bearing:

- **Authority** — `_model_identity`, `_model_session_identity`, `_model_pending_evidence`
  and the ledger row's model cells — is in-process only and is **never** restored. That
  half of the row was and remains correct (`_stale_model_evidence`,
  `orca_runtime_harness.py:3584-3652`; its public name `invalidate_model_authority`,
  `:3653`).
- **History** is now **durable on disk**, in `.pair_preparation.json`
  (`os14.pair_preparation.v3`) under its `role_history` and `sessions` sections
  (`pause_store.py:851`, `:1723-1772`, `:1773-1817`). A successor process reads the `(phase, role)`
  baseline through `OrcaAdapter._durable_model_baseline`
  (`orca_adapter.py:719-788`) **before any session exists**, and its own fresh
  re-verification is then compared against it: a disagreement is
  `PAIR_PREPARATION_MODEL_DRIFT` and a *missing* baseline row that some surviving
  observation still proves existed is `PAIR_PREPARATION_MODEL_HISTORY_CONFLICT`
  (`orca_adapter.py:618-653`, `:765-780`).

So a restart does **not** erase history, and the in-memory maps
`_model_role_history` / `_model_session_history` are no longer the only record of it. The
rule the row states is unchanged in direction — **a successor re-verifies positively and
may never restore a stored verdict** — but it is now *stricter* than "no history
survives": the successor must re-verify **and agree with** what this run durably observed
before. Nothing here makes a `VERIFIED` record a current verification.

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
(`:3798-3812` says so already); per-dispatch re-verification costs one extra preflight turn
(≈2 s measured) every time (**D5**).

### Change scope

A new driver module (its own file; provider-specific CLI syntax and stream parsing live
**only** there), plus B1-a's vocabulary members. **No** change to
`_stale_model_evidence` or to the authority/history split: this design reuses both exactly
as they are.

**Corrected (OS-14 design run).** The sentence that followed used to read "**No** change to
… any common module — `orca_adapter.py`, `pause_store.py`, `pause_policy.py` and
`launcher.py`". Two different claims were packed into it, and only one survives:

- **Stands (the C6 rule):** no common module learns any provider's CLI syntax or stream
  grammar. Every `claude`-specific flag, event name and parser lives in the driver module.
- **Withdrawn:** that no common module *changes*. Whether `orca_adapter.py` changes depends
  on **D8**, and under §3a's recommended shape it does — preparation must carry a
  process/target binding from verification through to delivery, and the delivery rung must
  write into the verified target. `pause_store.py` / `pause_policy.py` / `launcher.py`
  still need no change under that shape; §3a says which ones do and why.

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
| Contract difference from today | the driver holds a process **inside** one `select_and_verify` call and the task is delivered **after** it returns — so something must own that process across the boundary (**B1-c**) | none; and it cannot satisfy the barrier | ownership, cleanup and reuse-gate interaction all change; Orca's reuse gate already refuses evidence from a previous dispatch (`:2232-2272`) |

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

## 3a. The execution-target link: how the verified target receives the real Orca Task

This section answers the question the rest of the document had left open:
**how does the normal Orca Task/Dispatch work reach the very execution target whose model
the preflight turn verified?** It is written against the **current** Orca adapter
(merge base `7ec657f`, PR #38 merged), and it distinguishes what is measured from what is
not at every step.

### 3a.0 What does not answer the question

Three shapes are excluded before the design starts, because each one answers a different
question:

1. **Verify a probe process, dispatch to a different agent.** The barrier would attest a
   model for a process that then does nothing, while the work runs somewhere unverified.
   This is not a weaker version of the answer; it is the defect the barrier exists to
   prevent, dressed as a design.
2. **One-shot** (`-p` with the prompt as a positional argument — what the shipped
   standalone driver does, `standalone_drivers.py:1249-1313`). There is no instant between
   selection and delivery, so there is nothing to verify before delivering. 실제 관측, and
   it disqualifies the shape rather than merely costing something.
3. **A long-lived session reused across dispatches.** Excluded here not as impossible but
   as **미검증 and separately decided** (D8): every measurement in this project is
   `claude -p`, and a session's model is not permanently bound (E17 S3–S6).

What remains is §3's candidate (A): **one attempt → a dedicated non-interactive Claude
process → preflight turn → observe/verify that turn → deliver the real task into the SAME
target → collect/settle/terminate.** It is still a **candidate, not an approved contract**
(D8, 승인 대기). This section states what it costs to make it true on the existing adapter.

### 3a.1 The identity anchor already exists, and it is the Orca terminal handle

The design does not need to invent a binding between "the verified target" and "the task
recipient". The current code already names **one** identity in both places:

| Act | Call | The identity it names |
|---|---|---|
| preparation pre-pass verification | `OrcaAdapter._prepare_pair` → `harness.verify_model_identity("", handles[role], role=…, phase=…, attempt=…)` (`orca_adapter.py:599`) | `handles[role]` — the Orca terminal handle |
| the barrier's freshness leg (h) | `_verify_model_identity` compares the attested six-part key `(run, task, terminal, role, phase, attempt)` against the arguments it was called with (`orca_runtime_harness.py:4347-4365`) | the same handle, as `observed_at_terminal` |
| delivery-time Gate B | `start_worker` → `_gate_b_model_identity(task_id, terminal, …)` (`orca_runtime_harness.py:4683-4689`) | the same handle |
| rung 3 delivery | `worker-start --task <task> --terminal <handle>` (`:4707-4719`) | the same handle |
| rung 4 delivery | `dispatch --task <task> --to <handle>` then `terminal send --terminal <handle> --text <prompt> --enter` (`:4769-4790`) | the same handle |
| the durable record | `pause_policy.terminal_digest(handle)` / `pause_store.terminal_use_digest(handle)` — the preparation entry's `terminal_digest` and the `sessions` use row's key | the same handle, across processes |

So **sameness is a property of construction, not of comparison** — *provided* the real
Claude process is the process that terminal runs. The entire remaining design problem is
to make that true, and to keep it true across the interval between verification and
delivery. (추론, from the code above; the code reads are 실제 관측.)

The alternative — a driver that spawns its own process and reports
`observed_at_terminal = <the Orca handle>` for it — would make the attested key a
**false statement**, and would pass the barrier, because the harness compares strings it
issued and cannot see an OS process. That is the one thing no seam can prove
(`_verify_model_identity`'s own docstring, `orca_runtime_harness.py:3798-3812`), which is
exactly why it must be excluded by design rather than detected at run time.

### 3a.2 Creation order, end to end, with the code boundary of every step

`run` → `launch record` → `attempt` → `prepared session` → `ticket` → `task` → `dispatch`.
The ticket is minted **per driver call**, and there are **two** calls per delivered
attempt.

```text
launcher.build_orca_adapter(..., model_driver=RealClaudeDriver(...))   launcher.py:3042
  orca_run_routing(...)                                               launcher.py:2945
      Gate A: capability derived from the driver by
      agent_profile.model_selection_capabilities()                     agent_profile.py:1339
      agent_profile.driver_type_id(driver)  -> durable class identity   agent_profile.py:1380
  harness = OrcaRuntimeHarness(..., model_driver=driver)               launcher.py:3085
  harness.preflight(); run_id = harness.start_run(...)                 == the RUN exists
  pair_binding.record(... driver=<class id>, model_aware="true" ...)   == the LAUNCH RECORD

engine turn -> OrcaAdapter.start(intent)                               orca_adapter.py:271
 1  _assert_preparation_binding(intent)                                orca_adapter.py:459
      full launch identity cell-by-cell, DRIVER CLASS included
 2  phase/iteration derivations; prepare = pair_admission_required()   orca_adapter.py:286-300
 3  _journal_planned(..., pair_title=...)                              == the PLANNED row
 4  _prepare_pair(phase, attempt, planned, delivery_role)              orca_adapter.py:534
    4a entries + has_any; _assert_preparation_not_lost()               orca_adapter.py:560-564
    4b baselines = _durable_model_baseline(...) per role               orca_adapter.py:574
          reads role_history + role_observations BEFORE any effect
    4c _prepared_listing(selector), only if something was prepared     orca_adapter.py:579-582
    4d per role: _prepare_role(...)                                    orca_adapter.py:800
          pause_policy.resolve_prepared_terminal -> adopt | create | block
          _reuse_verdict(...)  -- DELIVERY ROLE ONLY                   orca_adapter.py:867
       [NEW, D2 option B] harness.mint_launch_lease(role, phase, attempt, ...)
          -- draws launch_stamp from the SAME counter as the ticket ordinals
          -- mints session_uuid, derives endpoint_path from the launch token
          -- composes the launch argv from THIS lease's requested_model
          store.record(stage="CREATE_INTENDED", requested_model=...,     orca_adapter.py:830
              [NEW] launch_token=, launch_stamp=, session_uuid=,
                    endpoint_digest=, launch_argv_digest=)
          harness.create_fake_terminal(...)  == `orca terminal create
              --worktree <sel> --title <t> --command <agent_command>`  orca_runtime_harness.py:2927
              -- `agent_command` is resolved_agent_command(role, phase) or the fake
                 shim (`:2978-2986`); under the hosted shape it is the lease's
                 composed argv, and the reuse gate's condition-2 key keeps reading
                 the PROFILE command, never the composed argv  [NEW, P-1]
          store.record(stage="CREATED", terminal_digest=...)           orca_adapter.py:862
              [NEW] the lease is BOUND to that digest here; before this line it
                    names an intent, after it, a physical session
       [NEW, P-8] harness.bind_prepared_target(handle, role, phase, attempt)
          -- the FIRST use of the handle, and it exists because the handle does
             not exist any earlier: `create` returns it (`:2996`) and the line
             above is what records it
          -- hosted capability absent by rule HC => returns `not_hosted`, changes
             nothing -- and the mint above wrote NO hosted launch cell, so this
             session is genuinely legacy and there is nothing hosted to recognise
             at step 8.  HC is read HERE (preparation gate) and at step 8 (a
             DELIVERABILITY condition, never a recognition one) by the SAME function
          -- hosted: driver.bind_target(handle, lease) reads the shim's `hello`
             (PRE-CREATE fields only, plus the shim's own `shim_incarnation`),
             sends ONE bind identity {terminal, terminal_digest, shim_incarnation,
             bind_nonce} -- up to 3 byte-identical retransmissions on the SAME
             connection -- and requires the `bound` echo + `child_started` (§3a.12)
          -- the shim spawns the `claude` child only on that verified bind, so a
             failed bootstrap spends ZERO model turns
          -- failure does NOT raise here: it leaves the target UNBOUND, and 4f's
             unconditional verify then refuses through the EXISTING path
             (model_selection_unverified -> PAIR_PREPARATION_MODEL_UNOBSERVED,
             orca_adapter.py:606-617), with release(handle) called once
    4e distinct-handle check                                           orca_adapter.py:590-594
    4f per role, UNCONDITIONALLY, EVERY pass:
          harness.verify_model_identity("", handle, ...)   <== DRIVER CALL 1 (pre-pass)
                _mint_model_selection_ticket(task_id="", terminal=handle, ...)
                driver.select_and_verify(ticket)
          resolved = harness.ledger_terminal(handle)["resolved_model"]  orca_adapter.py:604
          drift vs 4b baseline -> PAIR_PREPARATION_MODEL_DRIFT          orca_adapter.py:618
          store.record_role_history(...) then store.record("VERIFIED")  orca_adapter.py:645-661
 5  task_id = harness.create_task(spec)                                == the TASK exists
 6  _record_receipt(...); journal OPENED
 7  use = _record_delivery_intent(...)   == sessions row DELIVERY_INTENDED  orca_adapter.py:937
 8  harness.run_existing_task(..., terminal=prepared[role], ...)       orca_runtime_harness.py:7012
      -> start_worker(task_id, handle, spec, role, phase, attempt)     orca_runtime_harness.py:7139
           _gate_b_model_identity(...)                 <== DRIVER CALL 2 (delivery gate)
           [NEW, P-4] the TWO-STAGE admission of §3a.13.  HC is NOT a recognition
                      input: it is evaluated at DELIVERABILITY.
             STAGE 1 RECOGNITION -- ONE condition, nothing else is consulted:
                 R-1: the durable preparation entry for this (phase, attempt, role)
                      carries a hosted launch cell whose terminal_digest ==
                      pause_policy.terminal_digest(handle)
                 R-1 FALSE (no hosted cell) -> and ONLY then -> today's code,
                     byte-identical.  This is the GENUINE LEGACY target:
                       wait_for_tui_idle(handle)                      orca_runtime_harness.py:3273
                       rung 3: `worker-start --terminal` ... or rung 4: `dispatch`
                               + `terminal send`
             STAGE 2 DELIVERABILITY -- only for an R-1 TRUE target, FOUR conditions:
                 D-a: HC holds  (deliver_hosted AND bind_target both callable, the
                      SAME derivation bind_prepared_target read at step 4d)
                 D-b: the launch lease is unrevoked, delivery_mode == driver_hosted,
                      and its terminal_digest matches the handle
                 D-c: Gate B has just accepted (structural -- it is the line above)
                 D-d: the driver returns a live, bind-complete target for the handle
                 ALL FOUR TRUE  -> wait_for_hosted_idle, rung 4's own `dispatch`,
                     driver.deliver_hosted(handle, prompt) with the THREE-WAY verdict
                     (§3a.13.1); _attach_terminal ONLY on the `delivered` verdict
                 ANY ONE FALSE  -> HOSTED_TARGET_UNDELIVERABLE, a NAMED refusal that
                     creates NOTHING: no tui-idle wait, no worker-start, no dispatch,
                     no write, no attach row.  The cause is carried:
                       D-a false -> HOSTED_CAPABILITY_INCOMPLETE
                       D-b false -> model_selection_request_stale
                       D-d false -> DRIVER_TARGET_UNBOUND / DRIVER_TARGET_REBOUND
             ** An incomplete HC on a recognised target REFUSES; it does not demote
                to the legacy path.  The legacy path is reachable ONLY via R-1 false. **
      -> wait_for_done(dispatch_id, task_id)           == the OUTPUT COLLECTOR
      -> settle_attempt(...)                           == the SETTLEMENT
 9  record_session_use(DELIVERED, dispatch_id)                       orca_adapter.py:358-363
      ** step 8's settle_attempt is NOT the end of this role's round. **
      The engine classifies the settled record one node LATER, and may re-dispatch:
         EXECUTE_INTENT (the whole of step 8) -> VALIDATE_SETTLEMENT -> APPLY_RESULT -> ROUTE
         VALIDATE_SETTLEMENT: FORM defect?  -> pending_gate_defect + repair_binding
         ROUTE: FORM defect and budget left -> PREPARE_REPAIR -> PREPARE_INTENT
                   == the SAME role, phase and round, re-entering at step 1 above,
                      so step 4f re-verifies BOTH roles again
10  [NEW, D8-b] on ROUND CLOSE, and the predicate is the ONE in TL (§3a.12.1):
      (1a) every role REQUIRED by this round has reached VALIDATED --
             risk high/medium: the WORKER *and* the PHASE REVIEWER.  A Reviewer that is
               PREPARED BUT NOT YET DISPATCHED is REQUIRED and is ACTIVE, not VALIDATED,
               so (1a) is FALSE and the round is OPEN (routing.phase_gate returns
               PENDING while reviewer_result is None, and ROUTE emits
               PREPARE_PHASE_REVIEWER)
             risk low:         the WORKER only (phase_gate: risk == "low" => PASS)
      AND (1b) ROUTE's token is neither PREPARE_REPAIR (re-dispatches THIS role) nor
             PREPARE_PHASE_REVIEWER (dispatches ANOTHER role of this SAME round)
      -- or the round was refused/abandoned (clause 2) --
      then release(handle) for every handle prepared under the key, exactly once.
      So an accepted WORKER turn does NOT close a high/medium round: at that point
      (1a) is false AND (1b) is false.  NOT at step 9, and NOT at step 8's
      settle_attempt: a validation repair of EITHER role re-enters this whole sequence
      under the SAME key, and the Reviewer's turn re-verifies the Worker's session at
      ITS step 4f.
```

Three facts in that order matter more than the rest and are easy to miss:

- **One launch, several observations — and that asymmetry is the whole of F-001.** The
  act that carries `--model` happens **once per prepared session**, at step 4d. The acts
  that observe a model happen **once per driver call**: the pre-pass at 4f, Gate B at
  step 8, and the counterpart role's pre-pass in its own round. The existing contract
  wants one attested *request* and one attested *observation* per call
  (`orca_runtime_harness.py:4265-4281`, leg 1), so a second call on the same session has
  no new launch to attest. **Drawing a fresh ordinal before the second preflight input
  and calling it a request would attest an act that did not occur** — which is a weaker
  contract than today's, not a stronger one. §6 D2 states the two ways out and
  recommends one; the step marked `[NEW, D2 option B]` above is the recommended one, and
  **it is 승인 대기, so the sequence above is a proposal and not the current code**.
- **The Task does not exist when the pre-pass verifies.** Step 4f runs at step 4, step 5
  creates the Task. That is why `verify_model_identity` is called with `task_id=""`
  (`orca_adapter.py:599`) — and therefore why the pre-pass's attested key is
  `("", …, handle, role, phase, attempt)` while Gate B's is
  `(<task_id>, …, handle, role, phase, attempt)`. **The two keys differ, so Gate B
  structurally cannot accept the pre-pass's evidence**: leg (h) would refuse it as
  `model_selection_request_stale`. Each call is its own verification, by construction.
  (실제 관측, from the code.) This is the mechanism that makes "do not skip Gate B and do
  not reuse another dispatch's evidence" an invariant rather than a rule to remember.
- **What ties the two calls together is leg (i)**, the per-`(phase, routing_role)`
  consistency check on **resolved** values (`orca_runtime_harness.py:4400-4411`, reading
  `_model_role_history`), plus the durable `(phase, role)` baseline at step 4b. Both calls
  must resolve to the **same** model or the attempt is refused
  (`model_selection_ambiguous` in the barrier, `PAIR_PREPARATION_MODEL_DRIFT` in
  preparation).

### 3a.3 Ownership: who owns the process, who delivers input, who collects output, who terminates

| Responsibility | Owner under the recommended shape | Why it cannot be someone else |
|---|---|---|
| **Process existence** | the **Orca terminal** created at step 4d. The process is the terminal's `--command`. | The terminal handle is the identity every later step names (§3a.1), and its `terminal_digest` is the only cross-process identity the durable record has. A process Orca does not host cannot be the thing `worker-start`/`dispatch` targets. |
| **Process control channel** (write a turn, read its events) | the **driver module**, through the coupling chosen in §3a.6 | `select_and_verify` is the only method the harness calls, and leg 2 must read the CLI's own report for the preflight turn. No common module may learn the stream grammar (C6). |
| **Real-task input delivery** | under the recommended coupling, the **driver**, invoked by the delivery rung with the prompt `start_worker` already composes (`orca_runtime_harness.py:4780-4786`) | the write must be **byte-exact** into the verified process's stdin; see §3a.5 for why the generic `terminal send` path cannot be assumed to be. |
| **Output collection / settlement** | **`harness.wait_for_done(dispatch_id, task_id)`** (`orca_runtime_harness.py:7143`), i.e. the **Orca Dispatch**, parsed by `OrcaAdapter._parse_result` (`orca_adapter.py:257-265`) | this is unchanged, and it is the whole reason this is Orca integration rather than a standalone run. The CLI's stdout is **never** the settlement channel. |
| **Termination** | the **stdin owner** — measured: closing stdin exits the process `0` (P1 M6). Beyond that, the operator, through `orca terminal close`. | Nothing in the engine closes, reuses or counts a residual session on its own (`docs/COMPATIBILITY.md`, "Residual session procedure"), and this design does not change that. A driver that killed a session the operator procedure expects to find would break that procedure. |

**The gap this creates, stated plainly:** the driver holds a control channel to a process
whose lifetime is the **terminal's**, not the driver call's. `select_and_verify` returns
and the channel must survive. That is exactly **B1-c**, and it is the reason the §3
RETRACTION was correct: preparation today holds no process and assumes no liveness.

### 3a.4 How sameness of the verified target and the task recipient is established

Four independent conjuncts, all available locally, none of them sufficient alone:

1. **Handle identity.** The ticket's `terminal`, the attested `observed_at_terminal`, the
   Gate B argument and the delivery target are the same string, enforced by leg (h)
   (`orca_runtime_harness.py:4347-4365`). 실제 관측 (code).
2. **Digest identity across processes.** `terminal_digest` in the preparation entry and
   `terminal_use_digest` in the `sessions` row denote one physical session; adoption is
   digest-proved, never title-only (`pause_policy.resolve_prepared_terminal`). 실제 관측.
3. **Session-id echo.** Every event of every turn carried `session_id` equal to the
   caller-supplied `--session-id` uuid — on all three P1 turns and on P2's (실제 관측,
   P1/P2). The driver supplies that uuid, records it against the handle, and requires the
   echo on `system/init`, on the `assistant` event and on `result`.
4. **Input echo.** With `--replay-user-messages`, the delivered message comes back on
   stdout as a session-bound `user` event before that turn's `assistant` event (실제 관측,
   P1). This is the only locally available channel that speaks to *"the text I wrote
   entered the session I verified"*, and it is why the flag is recommended as mandatory for
   this driver rather than optional.
5. **Launch-lease identity** (new, and the conjunct F-001 turns on). The still-live
   session's model was requested by exactly one act, recorded durably **before** that act
   ran: a lease carrying `launch_token`, `launch_stamp`, `session_uuid`,
   `requested_model`, `launch_argv_digest` and — once step 4d's `CREATED` write lands —
   `terminal_digest`. Every driver call cites that lease by token; the harness checks the
   citation against its own registry **and** against the durable record; the channel
   **bootstrap** (§3a.12) checks the lease's **pre-create** fields against what the launched
   process reports in `hello`, and then pushes the **post-create** handle to it in one
   authenticated `bind` whose nonce the shim must echo before any turn exists.
   A call that cannot cite an unrevoked lease for the handle it was given has no request
   leg at all. 추론 (the mechanism); the durable record it rides on is 실제 관측 (code).

**What none of them establishes** (非보장, stated so it is not read as covered): that the
OS process behind the handle is the one the driver believes it is talking to, if the
coupling is indirect. Conjunct 3 binds the **session id**, which the driver itself chose;
a second process launched with the same `--session-id` would echo it too. The protection
against that is **ownership**, not observation: exactly one process is ever launched with
a given uuid, the uuid is minted per prepared session and recorded against its
`terminal_digest`, and a second launch under a recorded uuid is a named refusal (§3a.8,
`DRIVER_TARGET_REBOUND`). Conjunct 5 narrows this further but does **not** close it: the
bootstrap proves **agreement plus ordering** between the lease and whatever answered on the
endpoint — including that the peer accepted a handle binding composable only after
`terminal create` returned — and against a *same-uid* impostor on this host no local
mechanism distinguishes agreement from provenance — the same class of limit `_verify_model_identity`'s own docstring already
records for a lying driver (`orca_runtime_harness.py:3798-3812`). The endpoint's `0700`
directory and `0600` socket exclude a *different* user, and that is the whole of what they
exclude. 추론.

### 3a.5 The Orca-side links that are 미검증 — and the minimum observation for each

**Do not assume any of these.** They are the boundary this design stops at, and the
single largest remaining risk to §3a's recommendation.

Three of the six have since been **observed** in the minimal-feasibility run
(`run_fd87428a21f0`; see §Evidence basis for S-0…S-4 and
`docs/OS-14-feasibility-evidence-summary.md` for the committable detail), and their rows now
state what was measured **and the environment it was measured in**. Three remain **미검증**.
Every observation below is scoped to: **macOS `Darwin 25.5.0`, Orca `1.4.197`, Claude Code
`2.1.260`, `claude -p --input-format stream-json`**, personal OAuth auth, no API key. Nothing
here is a company-environment result (§7), and an observation on one configuration is not a
property of the mechanism.

| # | Link | Status | What is known, and from where | What would still have to be observed |
|---|---|---|---|---|
| U-1 | Can an Orca terminal host the observed process? | **split: direct = 반증 (실제 관측); supervisor = 확인 (실제 관측), in the tested configuration only** | **Direct hosting is refuted (S-1a):** with the terminal's own process passing its **TTY** stdin straight through, `claude -p` exits `rc=1` with `Error: Input must be provided either through stdin or as a prompt argument when using --print`, emitting no `assistant` and no `result` event. A `claude -p` whose stdin **is** the Orca PTY does not wait for stream-json input. **The supervisor shape works (S-1b):** a terminal whose own process was a launcher reading the PTY and writing into the child's **pipe** stdin hosted the CLI as a live child (pids 81932 → 81950, no `child_exit`) and ran a full turn. Both are scoped to macOS `Darwin 25.5.0` / Orca `1.4.197` / Claude Code `2.1.260`; `--command <text>` is "Command to run in the terminal on startup" (`orca terminal create --help`), and `create_fake_terminal` already passes an arbitrary resolved command string. | The shape §3a.12 actually specifies: that the terminal's process is the **repo-owned shim**, that it binds its `AF_UNIX` endpoint and completes the three-frame bootstrap, and that the CLI child appears only after a verified `bind`. **None of that was executed** — stage 2's launcher was a disposable relay over `terminal send`, not the channel (§Evidence basis, limit 2) |
| U-2 | Does `orca terminal send --text '<one JSON line>' --enter` reach that process's **stdin byte-exactly**? | **확인 (실제 관측), with one requirement attached** | **S-0:** the **70 body bytes** arrived byte-identical on a TTY stdin, with **no** bracketed paste and **no** `ESC` rewriting — and terminated by **one `\r` (0x0d)**, not `\n`. Bytes were compared, not screen appearance (`["cr_present", "exact_match"]`). So the documented-adverse **agent** prompt path (`docs/ORCA_RUNTIME_PRIMITIVES.md:316-337`) was **not** the path taken for this non-agent terminal on Orca `1.4.197`. **The requirement:** a stream-json reader expects **LF**, so the **CR→LF conversion must be owned by the supervisor** — it is not optional packaging (§3a.12, requirement H-2). Scope: one terminal, one line, one Orca build; the body contained no `ESC` byte, so ESC handling on this path stays unobserved. | Whether the same path is taken for a payload that **does** contain raw `ESC` or a newline, and on any other Orca build. Note what this does **not** change: the hosted branch still replaces `terminal send`, because the CLI child's stdin is a **pipe the shim owns** (§3a.12) and `terminal send` reaches the **shim's** stdin, not the child's |
| U-3 | Can `worker-start --terminal <handle>` adopt such a terminal and report `state == ready`? | **미검증, and documented-ADVERSE** | `start_worker` calls it **unconditionally** for every target (`orca_runtime_harness.py:4707-4717`) and **requires** `state == WORKER_START_READY_STATE`, raising otherwise (`:4745-4755`); an error descends to rung 4 **only** for `agent_unconfigured` and raises for every other code (`:4767-4768`). Readiness accepts only **OSC-title transitions from a recognized agent** or a known-ready **screen preview** (`ORCA_RUNTIME_PRIMITIVES.md:558-583`), and a `-p` process paints no TUI and emits no such title. **Correction to the earlier edition and to review F-002's premise:** `wait_for_tui_idle` is **not** the blocker — it passes `allow_error=True` and records `idle` / `timeout` / `unobserved` without raising, and its own docstring says adoption "still proceeds … rung 3 descends to rung 4 only when worker-start itself reports an unconfigured agent" (`:3273-3305`). The blocker is `worker-start`'s receipt, and the `tui-idle` wait's remaining cost on this target is a wasted `wait_timeout_ms` on **every** delivery. | One `worker-start --terminal` against such a handle: read `state` / `stage` / `failedStage` / `lastError` from the receipt. |
| U-4 | Does `dispatch --inject`'s preamble reach a stream-json stdin, in a form the CLI parses? | **미검증** | `--inject`, `--dry-run` and `--return-preamble` exist on `orca orchestration dispatch` (`--help`, read this phase) and carry **no** documented notes. The adapter's rung 4 does **not** use `--inject`: it dispatches and then composes its **own** prompt (`orca_runtime_harness.py:4766-4785`). | `dispatch --dry-run --return-preamble` to obtain the preamble text **without** delivering it, then one `--inject` against such a terminal and a stdout read. `--return-preamble` is the cheap half and is worth doing first. |
| U-5 | What does `terminal read` see of a stream-json stdout? | **미검증** | Its own `--help` (read this phase): the default is "accumulated terminal output with escape sequences stripped", `--screen` is the rendered frame, and `oldestCursor` reports **dropped** lines. A `-p` process does not repaint, so the stacked-fragment hazard the help warns about should not apply — but **truncation** would, and verification evidence must not be truncated. | One read with `--cursor`/`--limit` over a turn's output: are the JSON lines intact, and is `oldestCursor` stable? |
| U-6 | Can a `claude -p` process run `orca orchestration send --type worker_done`, and will that settle its own Dispatch? | **확인 (실제 관측) as PROTOTYPE WIRING — not as production driver support** | **S-3:** one dispatched `-p` turn ran two `Bash` tool calls and reported `worker_done` for its **own** Task and Dispatch; the runtime settled that exact Dispatch with `result.provenance = worker_report` and `reportedBy = completedBy = <that process's own terminal>`, and no Coordinator sent a `worker_done` on its behalf. The `worker_done` needed **no** `--dispatch-capability` — `--from <handle>` alone was accepted (the Dispatch was created without `--inject`, `state = unsupervised`). The permission scope that sufficed is in §3a.7, and it is **not** a product policy. `--safe-mode`'s own help ("Auth, model selection, built-in tools, and permissions work normally") is corroborated. | That the same holds for a **supervised** Dispatch (`worker-start`, which is U-3), for an `--inject`ed preamble carrying a `--dispatch-capability` (U-4), and under a **product** permission scope wide enough to read, edit and test code (§3a.7) rather than the two-command allow-list this probe used. **This observation is wiring evidence; it is not evidence that `build_orca_adapter` supports a real driver** |

**U-3 is now the one that decides the shape, and it is still 미검증 and
documented-adverse.** S-0 settled U-2 positively, which removes byte-exactness as a reason to
avoid Orca's own write path — but it does **not** make (C-1) viable, because (C-1) still
traverses **U-3** for delivery and **U-5** for evidence, and because on the hosted shape
`terminal send` reaches the **shim's** stdin while the CLI child reads the channel. A design
that assumed U-3 would be exactly the "unverified Orca behaviour" the request forbids. That is
why the recommendation below routes **around** it rather than through it — and why §3a.13's
admission rule is restated as a **recognition / deliverability split**, which removes the
*dependence* on U-3's receipt instead of pretending to answer it (X-4).

**What stage 2 did not move.** S-1b/S-3 exercised a `terminal send` → launcher → child-pipe
route. §3a.12's `AF_UNIX` endpoint, its `hello`/`bind`/`bound` bootstrap, the launch lease and
Gate B were **not** exercised, so the protocol below remains **문서상 설계**; U-4 and U-5 were
never reached; and the held-process question is measured only to **68.8 s**.

### 3a.6 Three couplings, and the recommendation

| | **(C-1) Orca-mediated** | **(C-2) Hosted process, side control channel** *(recommended)* | **(C-3) Orca owns delivery** |
|---|---|---|---|
| Terminal `--command` | the `claude` argv directly — **refuted** for a `-p` process: S-1a measured that a `claude -p` whose stdin is the Orca PTY exits `rc=1` without consuming input, so (C-1) needs a supervisor too and is not actually the "no new process" option it looks like | a small **repo-owned supervisor shim** that **spawns** the `claude` argv as a child — only once the harness has bound the channel to the created terminal — and stays alive to relay it (**not** `exec` — see §3a.12) | the `claude` argv directly — **same S-1a refutation** |
| Verification writes | `terminal send` → the supervisor's PTY stdin → CR→LF → the child's pipe (**the route stage 2 actually exercised**, S-0/S-1b) | the shim's `AF_UNIX` control channel, byte-exact, one newline-terminated line per turn (§3a.12) — **문서상 설계, unexecuted** | the same as (C-1) |
| Verification reads | `terminal read` (U-5, **미검증**) | the shim's stdout tee: one copy to the terminal (operator + `terminal read`), one to the driver as sequenced NDJSON frames (§3a.12) | `terminal read` (U-5, **미검증**) |
| Real-task delivery | `terminal send` into the supervisor | the driver, through the same channel, inside the **hosted delivery branch** of `start_worker` (§3a.13) | `worker-start --terminal` preamble (U-3) |
| Orca Task / Dispatch / `worker_done` / settlement | unchanged | **unchanged** — the Dispatch is still `orca orchestration dispatch --task … --to <handle>`, i.e. rung 4's own call (`orca_runtime_harness.py:4769-4779`) | unchanged |
| Depends on 미검증 Orca behaviour | **U-3 + U-5.** Corrected: U-2 is now **measured** (S-0, byte-exact + one CR), so byte-exactness is no longer (C-1)'s blocker — **U-3 is**, because `start_worker` calls `worker-start --terminal` unconditionally and raises on any receipt that is neither ready nor `agent_unconfigured` (`:4707-4768`) | **U-5 only — and ONLY with §3a.13's hosted branch.** Without that branch, (C-2) still traverses **U-3** for the same reason, because rung 4 is never reached; replacing the rung-4 `terminal send` alone does not help. U-1's supervisor half is now **observed** (S-1b), but §3a.12's own channel and bootstrap are **unexecuted**, which is a different and larger gap than a 미검증 Orca call | **U-3 + U-4** |
| New code | none beyond the driver | driver **+ one shim** + the hosted delivery branch (§3a.13) + the launch lease (§6 D2 option B) | none beyond the driver |

**Recommendation: (C-2), 승인 대기 under D8 — unchanged by the feasibility run, for a
narrower reason than before.** The reason is not elegance, it is evidence discipline:
(C-2) is the only coupling whose *evidence* path and *delivery* path depend on no
**undocumented Orca behaviour**. The terminal still **hosts and owns** the process, so the
handle, the digest, the reuse gate, the durable record, the Task, the Dispatch,
`worker_done` and the settlement are all untouched — and `terminal read` remains useful
for the operator without being load-bearing.

**What the feasibility run changed about this recommendation, stated precisely.** S-0 removed
the byte-exactness objection to Orca's own write path, so the honest argument for (C-2) is now
**U-3 and U-5**, not U-2: (C-1) and (C-3) still cannot deliver without `worker-start`'s
unobserved receipt, and still have no evidence channel other than `terminal read`. In exchange,
(C-2) carries a cost the run makes visible: its own channel protocol has **never been
executed**, while the `terminal send` → supervisor → child route **has** (S-1b/S-3). That is a
real argument in (C-1)'s favour and it is recorded as such in the alternatives below rather
than argued away; it does not carry, because a `terminal send` route still has to pass U-3 to
get a Dispatch at all, and because the evidence leg would then rest on U-5.

What (C-2) costs, stated rather than discovered later:

- one new provider-specific **supervisor shim** beside the driver (it is as much "the
  driver" as `argv()` is, and it is covered by the same C6 rule);
- a **delivery branch**, not merely a delivery seam: rung 3 is reached before rung 4, so
  the branch has to be taken **above** `worker-start`, not inside rung 4. §3a.13 specifies
  it, including the conjunction that admits it and the single default — today's code —
  for every target that does not satisfy the conjunction;
- the shim must open its side of the **control channel** rather than announce anything on
  its first terminal line: the coordinator-side driver must be able to bind the handle to a
  process it can name **without** `terminal read`, which is the whole point of calling
  `terminal read` non-load-bearing. And the binding can only run in **that** direction —
  the handle does not exist until `terminal create` returns, so the shim reports what it
  knew before the call and the harness pushes what only it can know afterwards (§3a.12);
- a **launch lease**, because the one `--model` act is not inside any driver call's
  ticket window (§6 D2 option B);
- it does **not** remove U-5, and with §3a.13 it does not depend on U-3 or U-4 — being
  independent of a 미검증 boundary is not the same as settling it;
- and it adds an **unexecuted protocol** of its own. The `AF_UNIX` endpoint, the three-frame
  bootstrap and the launch lease of §3a.12 have no observation behind them at all; the five
  stage-1 probes (X-1…X-5) measured a **toy** implementation of that specification and are
  evidence about the specification's internal consistency, not about a working channel. §9.3
  Group 3 and the `[live]` row of Group 5 are where that gap is closed, **after** approval.

If the user prefers to settle **U-3** first and take (C-1) — which S-0 and S-1b have now made
cheaper than this document previously assumed, at the price of U-5 for the evidence leg — or
(C-3), the remaining observations in §3a.5 are the complete list, and the decision belongs in
**D8**.

### 3a.7 One requirement the earlier design did not name: the delivered process must settle its own Dispatch

The real-task prompt the adapter delivers carries `taskId`, `dispatchId` and the
instruction to "Use `worker_done` exactly once with an explicit outcome"
(`orca_runtime_harness.py:4780-4786`); on the rung-3 path Orca's own preamble additionally
carries a `--dispatch-capability`. The reference agent satisfies this by **parsing the
prompt** and shelling out to `orca orchestration send`
(`scripts/orca_fake_agent.py:21-41`, `:120-152`).

A real Claude process must do the same, which means **the dispatched `-p` process has to
be able to run a command**. That is a real constraint the earlier design never states:

- the isolation set does not block it — `--safe-mode`'s help says built-in tools and
  permissions "work normally" (문서상 지원), and this is now **corroborated** by S-3, where a
  dispatched `-p` turn ran two `Bash` calls with `permissionMode = "default"` and
  `apiKeySource = "none"`, and no refusal occurred;
- but a non-interactive turn **cannot answer a permission prompt**, so what the process may
  execute has to be decided **in advance**, in its argv;
- `--restricted` removes the command-running tools and **refuses** `bypassPermissions`, so
  it is not compatible with a worker that must settle itself;
- widening what a dispatched process may execute is a **security decision**, not an
  implementation detail.

**S-3 answered the mechanism question and did not answer the policy question.** Those are two
separate pending items, and they are kept apart here because conflating them is how an
experiment's allow-list becomes a product default:

| | **the scope actually used in the experiment** (실제 관측) | **the product scope a real driver would need** (제안, 미검증) |
|---|---|---|
| argv | `--safe-mode --setting-sources '' --no-session-persistence --permission-prompts none --allowedTools "Bash(orca --version)" "Bash(orca orchestration send:*)"` | at minimum the lifecycle commands above **plus** whatever a development Worker/Reviewer genuinely needs: reading files, editing files, and running this repository's tests |
| what it was enough for | exactly two harmless commands, in one turn, in a probe that changed no file | unknown — no measurement covers it |
| `--permission-mode` | **not used at all**; `permissionMode` reported `default` | undecided. The narrow recommendation stays: take `permission_mode` from the **agent profile** (as the standalone driver already does — `standalone_drivers.py` `profile.permission_mode`) rather than hard-coding one in the driver |
| `bypassPermissions` / `--dangerously-skip-permissions` | **not used, and not proposed** | **not proposed.** A blanket mode would make the allow-list decorative |
| auth | personal OAuth only; no API key, no credential copying | unchanged by this document; the company environment is §7 |
| status | an **observation about one probe**, recorded so it is not mistaken for a policy | **승인 대기**, with D8's security half |

**Sufficiency for one harmless probe is not a product policy, and this document does not
propose it as one.** The product scope is the user's decision, and it is the one half of D8
that an approval of the execution shape does **not** automatically settle.

What S-3 does retire is the earlier edition's worst case: the claim that *if U-6 fails,
candidate (A) delivers work that can never settle its Dispatch*. It does not fail on this
configuration — a dispatched `-p` process settled its own Dispatch, and the runtime accepted it
on `--from <handle>` alone with no `--dispatch-capability`. **What stays open** is whether the
same holds for a **supervised** Dispatch (U-3) and for an `--inject`ed capability-bearing
preamble (U-4), neither of which was attempted.

### 3a.8 Validity while the Reviewer's prepared target waits for its own turn

Pair admission requires **both** sessions created and verified before the **first**
delivery of **either** role (`docs/COMPATIBILITY.md`, "Worker/Reviewer independence is
admitted as a PAIR"). So the Reviewer's process is launched and verified during the
**Worker's** `start`, and then waits — through the Worker's entire turn — until the
Reviewer's own graph turn re-adopts the same session under the same `(phase, attempt)` key.

**And the symmetric half, which DR-01 made load-bearing:** the Reviewer's round re-verifies
**both** roles, so the **Worker's** session must also still be answerable **after the Worker's
Dispatch has settled**. `_prepare_pair` verifies both roles **unconditionally on every pass**
(현행 코드), and `delivery_role` scopes only the reuse gate. That is why the target lifetime is
**round**-scoped and not settlement-scoped — the one definition is **§3a.12 TL**, and this
section does not restate it. Two targets are therefore held through this window, not one — and
through **each validation-repair turn** the engine asks for under the same key, which is the
case TL's rows 2 and 3 cover and the one a settlement-keyed release destroyed.

| Question | Answer | Status |
|---|---|---|
| Does a held process survive an idle interval and still run a later turn? | **Yes at 90 s**: zero events during the wait, process alive, turn 2 ran in the same process and session (P1 M2/M3). Independently, **68.8 s** between two delivered turns on a held hosted process, same child pid, no `child_exit` (S-3). | 실제 관측, **scope ≤ 90 s** |
| Does it survive a **Worker turn's** worth of waiting (minutes to tens of minutes)? | **미검증.** Neither 90 s nor 68.8 s is that claim, and the feasibility run's 68.8 s is **shorter** than P1's 90 s — so the measured ceiling did not rise. | 미검증 |
| Does the **settled Worker's** target have to survive it too? | **Yes, under TL** — and that is new relative to any reading in which the Worker's target is disposed at settlement. The Reviewer round's pre-pass writes a preflight turn into the Worker's **post-task** session. | 실제 관측 (code: the unconditional both-role loop) + 미검증 (that it survives the duration) |
| What happens if either did not? | The round re-prepares and **re-verifies unconditionally** (`orca_adapter.py:595-600`). A dead process cannot produce a preflight turn, so the driver reports `model_selection_unverified`, or the resolved value is unreadable and preparation refuses `PAIR_PREPARATION_MODEL_UNOBSERVED` (`orca_adapter.py:606-617`). **The round is refused; nothing is delivered.** Measured as the exact blocking step under a settlement-scoped release (X-3). | 실제 관측 (code) |
| Is that recovery automatic? | **No, and deliberately not.** A refusal is the designed outcome. The engine does not silently re-prepare a dead session, and this design does not propose that it should. | 실제 관측 (code) |

**Cost, stated:** a **Reviewer** process that dies during a long Worker turn costs the
**whole gate round** — and under TL so does a **Worker** process that dies between its own
settlement and the Reviewer's pre-pass. That is fail-closed and correct, but it is a real
operational cost, it is now a **two**-process exposure rather than one, and it is a
**D5/D8 input**, not a detail. **Minimum observation to retire it:** hold one such process
for the p95 duration of a real Worker turn in this repository's own runs and send a turn
afterwards. One turn, one long wait — deliberately **not** spent from this phase's budget,
because neither a 90 s nor a 68.8 s result can be extrapolated and a p95-length result needs a
real duration figure first.

**Do not mitigate it with keepalive turns.** A keepalive is a model turn: it costs money,
it is not attributable to any attempt's ticket, and an unattributed turn on a verified
session is exactly the kind of unrequested state change OS-49 refuses to read as evidence.

### 3a.9 Evidence invalidation and safe stop: process exit, replacement, timeout, interrupt

Every row reuses machinery that already exists. **No new failure reason is added** (D6).

| Event | Detection | Action | Existing mechanism |
|---|---|---|---|
| **Process exited / was killed** between verification and delivery — **DIRECT stdin shape** (the standalone driver, or any shape in which the driver writes the CLI's stdin itself) | the delivery **write itself**: `BrokenPipeError` / `EPIPE` (실제 관측, P2 K3) | revoke this session's authority, refuse the delivery as `model_selection_unverified`; deliver nothing | `invalidate_model_authority` → `_stale_model_evidence` (`orca_runtime_harness.py:3653`, `:3584`); `start_worker`'s `BaseException` handler restores the pre-barrier snapshot (`:4792-4793`) |
| **CLI child exited / was killed** between verification and delivery — **HOSTED relay shape (C-2)**. **Corrected: P2's result does NOT transfer here, and "No liveness poll needed" is withdrawn** | **not** the driver's write. The driver writes the **shim's socket**, the shim is alive, and the write completes with **no error of any kind** (반증, 실제 관측 X-5). The dead child is knowable only from a **shim-authored** `ctl` ack carrying `hop2_error` — and that ack is **absent precisely when the shim is what died**, which is **UNKNOWN**, never non-delivery (§3a.13.1) | on an ack reporting the failure: `not_delivered` ⇒ revoke, `release(handle)`, re-raise. On **no** ack: **UNKNOWN** ⇒ revoke, `release(handle)`, re-raise, and keep the open Dispatch obligation. **Never** resend, never settle as success | §3a.13.1's verdict table; `delivery_obligations()` for the open obligation. The narrow new trust statement: the shim is trusted for **liveness reporting**, never for **evidence attribution** |
| **Channel lost after Gate B accepted** (the DR-02 window) | §3a.13's deliverability test (**D-d**) at the branch, or the write verdict immediately after it | **`HOSTED_TARGET_UNDELIVERABLE`** — a **named** refusal. **Never** the legacy `worker-start` / `terminal send` path, which would spend a `tui-idle` timeout, create a Dispatch and record an attached row for a task the CLI child never receives (실제 관측, X-4) | §3a.13's recognition/deliverability split; `release(handle)` once (§3a.12 TL); the pre-barrier snapshot restored |
| **Hosted capability absent or incomplete on a RECOGNISED target** — the run's driver exposes only `deliver_hosted`, or only `bind_target` | §3a.13's deliverability test (**D-a**), reading the **same** HC derivation `bind_prepared_target` used at step 4d | **`HOSTED_TARGET_UNDELIVERABLE`**, cause **`HOSTED_CAPABILITY_INCOMPLETE`**. **Never** the legacy path: the terminal's process is the **shim**, so a `terminal send` would write the task into the shim's stdin while the CLI child waits on the channel. An incomplete capability is a hosted **deliverability** failure, not a demotion to legacy (F-002) | §3a.11 HC; §3a.13's verdict table. **추론**, not 실측 — X-4 never ran a hosted class with the capability conjunct false |
| **Process exited during `select_and_verify`** | the read ends / the write raises inside the driver call | the driver raises; the barrier **stales first**, then normalizes to `model_selection_unverified` | `orca_runtime_harness.py:4003-4014` — `BaseException`, stale, then `isinstance(exc, Exception)` |
| **Process replaced** (a second process under a recorded `session-id` / handle) | the driver's own registry: the handle already holds a target. Under D2 option B the bootstrap adds two more detectors — a `hello` whose pre-create fields disagree with the lease, and any **second `bind`** carrying a different handle, digest or nonce, which the shim refuses `rebind_refused` (§3a.12) | **named driver-side refusal** (`DRIVER_TARGET_REBOUND`), reported as `model_selection_unverified`, and the lease is **revoked** so no later call can cite it. Never silently rebind. | new, driver-local; the *vocabulary* member is existing |
| **Coordinator restarted** between verification and delivery | the driver's registry is empty while the durable entry still names a lease and an endpoint digest | **named driver-side refusal** (`DRIVER_TARGET_UNBOUND`) → `model_selection_unverified` → `PAIR_PREPARATION_MODEL_UNOBSERVED`. No re-bind, by design (§3a.12) | `orca_adapter.py:606-617`; the *vocabulary* member is existing |
| **Crash between `terminal create` and the bind** (named by review F-003; **bounded** only after the F-005 correction) | the next driver call finds a `CREATED` record with its `terminal_digest` and **no** binding | the same **`DRIVER_TARGET_UNBOUND`** refusal: nothing delivered, no re-bootstrap. The residual process is the **shim only** — no CLI child is spawned before a verified bind (실제 관측: `child_spawns == 0` in every X-1 case) — and it exits non-zero on the **one bootstrap deadline armed at listener readiness**, which is what makes the no-client and pre-`hello`-drop sub-cases bounded at all | `orca_adapter.py:606-617` + §3a.12's Timeouts rule; the existing residual-session procedure enumerates the terminal |
| **A `bound` echo that does not match the `bind` that was sent** | the driver, field by field (handle, digest, nonce) | driver-local `DRIVER_BIND_UNCONFIRMED` → `model_selection_unverified`; **no input is ever written**, `release(handle)` once | new, driver-local; the *vocabulary* member is existing |
| **Timeout** waiting for the preflight `result` | the driver's own deadline | report `model_selection_unverified`; do not deliver | the barrier already normalizes any driver `Exception` to this member (`:4015-4028`) |
| **Interrupt** (`KeyboardInterrupt` / `SystemExit`) mid-selection | raised through the driver | **stale, then re-raise as itself** — the harness already distinguishes invalidation from normalization: invalidation is keyed on the *side effect*, not on the exception type | `orca_runtime_harness.py:4003-4012` and the post-selection `BaseException` boundary at `:4030-4067` |
| **Mismatch** (reported ≠ requested, under `exact_equality.v1`) | the driver owns **satisfaction**, and under D3 that is pure string equality — including the adverse case where both reported channels agree on the **wrong** model | report `mismatch`; the harness refuses `model_selection_mismatch` | `_verify_model_identity`'s docstring (`:3792-3796`); `fake_adapter.py:298-301` is the reference shape |
| **A refusal after any preparation effect** | — | revoke authority, **preserve history**, refuse by name before the next effect | `_revoke_model_authority` + `_refuse_preparation` (`orca_adapter.py:611-638`) |

The invariant all of these share, and the one the driver must not break: **absent,
orphaned or unreadable evidence is `model_selection_unverified`, always.** P2 is the
concrete case — four valid-looking events, already read, describing a session that no
longer exists.

### 3a.10 Separating the preflight `result` from real-task completion

There are **three** distinct completions, and conflating any two of them is a defect:

| # | Completion | Who reads it | What it means |
|---|---|---|---|
| 1 | the **preflight turn's `result`** | the **driver**, inside `select_and_verify` | leg 2's observation window has closed. **Evidence only.** The turn is discarded. Three were measured in one process (P1), each with its own `assistant_request_id` and all with `num_turns = 1`. |
| 2 | the **real-task turn's `result`** | the **driver**, as delivery/liveness hygiene at most | the CLI finished a turn. It is **not** the dispatch outcome and must never be reported as one. |
| 3 | **`worker_done` → Dispatch settlement** | `harness.wait_for_done(dispatch_id, task_id)` → `settle_attempt` → `OrcaAdapter._parse_result` (`orca_runtime_harness.py:7143-7153`, `orca_adapter.py:257-265`) | **the only completion the engine acts on.** Unchanged by this design. |

**A fourth thing that is none of the three: the hosted delivery RECEIPT.** On the hosted
shape the shim's `relay_result` ack is a statement about **hop 2 of the delivery**, not about
any completion — and a delivery can complete the task while producing **no** receipt at all,
and produce a receipt that says nothing about the Dispatch (measured, X-5). The three-way
delivery verdict and its relation to these three completions are defined once, in
**§3a.13.1**, and are not restated here.

Turns are attributed by **turn membership** — the echo of the line the driver wrote, that
turn's `init`, its `assistant` events and its own `result` — and never by `num_turns` (`1` on
every P1 turn and on S-1b's; `3` on S-3's — 실제 관측). Note that one turn may carry
**several** `request_id`s and several `assistant` events (S-4), so attribution is to a turn's
`request_id` **set**, not to a single value (D3 conjunct 4). A driver that
returned the preflight `result` as the dispatch's settlement would be reporting a
discarded turn as the delivered work; a driver that waited for completion 2 before
returning from `select_and_verify` would be delivering **before** verification, which is
the barrier's whole purpose.

### 3a.11 The preparation and delivery changes the recommended shape needs

This is the concrete content of the §3 RETRACTION, and of §5's D8 row. All of it is
**승인 대기**.

| # | Change | Where | Why it cannot be avoided under (C-2) |
|---|---|---|---|
| **P-1** | the prepared session's `--command` must be the **lease's** composed argv (shim + `claude` + `--model <requested>` + `--session-id <uuid>` + `--endpoint <path>`), not the fake shim and not the profile command alone | `create_fake_terminal` (`orca_runtime_harness.py:2927`) gains a launch-command seam; `_prepare_role` (`orca_adapter.py:841-844`) passes it | the verified process must be the terminal's process (§3a.1). **Two existing disciplines must be preserved, not rediscovered:** (1) the model stays a **separate** ledger field and is never concatenated into the reuse gate's condition-2 identity (`:2978-2986`), so condition 2 keeps comparing `resolved_agent_command(role, phase)` — the **profile** command — while the composed argv is recorded as its own `launch_argv_digest` field; (2) the locked contract value `AGENT_PROFILE_MODEL_SELECTION = declared_driver_capability_only_never_launch_argument` (`scripts/validate_skills.py:938-940`, mirrored at `orca-worker-reviewer-orchestration/SKILL.md:2009` inside an exactly-25-line anchor block asserted by `scripts/test_os49_contract_locks.py:99-111`) **forbids this change as written** and must be replaced — in the block, in the validator and in the lock test, in one commit. **This is the D2 question in its concrete form, and that contract value is its concrete cost.** |
| **P-2** | the **launch lease** must be minted and recorded durably **before** `terminal create`: `launch_token`, `launch_stamp`, `session_uuid`, `requested_model`, `endpoint_digest`, `launch_argv_digest`, and `terminal_digest` at `CREATED` | new cells on the `.pair_preparation.json` entry (schema bump), written in the `CREATE_INTENDED` call that already runs strictly before the external effect (`orca_adapter.py:830-838`) | this is the request leg under D2 option B. The uuid is what the echo conjunct binds to (§3a.4), the token is what every driver call cites, and a successor process must be able to tell "this handle's target" from "a second process claiming the same uuid" |
| **P-3** | the **target binding** (handle → live, bind-complete control channel) must survive from verification to delivery **and through the whole gate ROUND**, and `release(handle)` must be **idempotent** | driver-local registry plus `release(handle)` called from every `_refuse_preparation` path, every bootstrap refusal, the EPIPE/dead-target detection, §3a.13's undeliverable refusal, and **round close** — the one definition is **§3a.12 TL**, which P-3 does not restate | `select_and_verify` returns before delivery, **and** the Reviewer's round re-verifies the **settled Worker's** session (DR-01, measured X-3), so a settlement-scoped release refuses the Reviewer round outright. This is **B1-c**. The binding is driver-local **on purpose**: §3a.12's restart rule is a named refusal, so there is nothing durable to resurrect |
| **P-4** | the **delivery branch** must be taken **above** `worker-start`, not inside rung 4 | `start_worker` (`orca_runtime_harness.py:4690-4790`): a hosted branch after Gate B that replaces the `tui-idle` wait, does not call `worker-start`, reuses rung 4's own `dispatch` call and prompt, and substitutes a driver write for `terminal send`. Full specification, including the **recognition / deliverability** admission split and its named refusal: **§3a.13** | `worker-start` is called unconditionally and raises on any receipt that is neither ready nor `agent_unconfigured`, so rung 4 is never reached on this target. Replacing only `terminal send` — the earlier edition's P-4 — fixes nothing |
| **P-5** | the pre-pass and Gate B must be **two turns on one process** — and exactly **one** launch | driver-local only | measured feasible (P1 M3). It is a cost, not a change: **two preflight turns per delivered attempt**, plus one per counterpart role per round. The user's own constraint forbids the alternative: the two verification calls of one attempt must not create duplicate processes nor swap the verified session |
| **P-6** | the driver must declare **hosted delivery** as a capability, through **one** derivation — **the rule HC below**, which is the single definition this document uses everywhere | `agent_profile.model_selection_capabilities`'s sibling (`scripts/agent_profile.py:1339-1363`), same shape as the existing one-rule function | **the two call sites that must not be able to disagree — and neither of them is recognition.** HC is read at the **preparation gate** `bind_prepared_target` (step 4d, P-8) and at §3a.13's **deliverability** condition **D-a** (step 8), by the **same** function over the **same** driver object. **Recognition is not keyed on capability:** §3a.13 Stage 1 recognises a hosted target on the durable **hosted launch cell alone** (R-1), and an absent or incomplete HC on a **cell-bearing** target is the named refusal **`HOSTED_TARGET_UNDELIVERABLE`** with cause **`HOSTED_CAPABILITY_INCOMPLETE`** — **never** a demotion to the legacy `worker-start` / `terminal send` path. When HC does **not** hold, the preparation gate mints **no** hosted launch cell at all, so the sessions it prepares are **genuinely legacy** and take today's path by **R-1 false**, with Gate B and every existing check unchanged. A delivery branch keyed on anything the harness does not itself derive would let the declaration gate and the delivery path disagree, which is the defect `model_selection_capabilities` exists to prevent — and in the earlier edition they **did** disagree (DR-02's C-1: §3a.13 said one name, P-6/P-8/§6 D8/§9.1 I-2 all said two) |
| **P-8** | the **post-create bind** must be a statement of the preparation sequence, immediately after the `CREATED` write: one harness method `bind_prepared_target(handle, role, phase, attempt)` beside `verify_model_identity`, and **one** call to it from `_prepare_role` | `orca_runtime_harness.py` (the method; it checks the lease, re-derives `pause_policy.terminal_digest(handle)`, and calls `driver.bind_target` **only** when the hosted capability is derived) + `orca_adapter.py:862-864` (the call, right after the `CREATED` record) | the handle does not exist before `terminal create` returns (`orca_runtime_harness.py:2996`) and its digest is recorded only at `CREATED`, so **no** shim-authored frame can carry either (review F-003). The bind is the only non-circular way the running shim learns which Orca terminal it is: the harness holds the create receipt, the shim echoes. It is **one** call and **no new refusal branch** — a failed bind leaves the target unbound and 4f's existing unconditional verification refuses it through `PAIR_PREPARATION_MODEL_UNOBSERVED` (`orca_adapter.py:606-617`). The capability is derived by the **same** rule as P-6 (both `deliver_hosted` and `bind_target` callable), so a driver that can deliver but cannot bind is not hosted-capable |
| **P-7** | the two **request-method members** and the **observation member** must be added, and the launch leg's two new attested fields (`launch_token`, `launch_stamp`) added to `ModelEvidence` | `orca_runtime_harness.py:844`, `:872`, `:1009-1019` + mirror + `scripts/test_os49_vocabulary_locks.py` | §6 D1/D2. Additive fields with `""`/`0` defaults, so every existing construction binds unchanged — the same pattern `ReuseObservation` and `RuntimeAttempt` already use. **Last**, after D2 |

> **HC — the hosted-capability rule, defined once, here.** The hosted-delivery capability
> exists **iff** `deliver_hosted` **and** `bind_target` are **both** callable on the driver
> object. One function, beside `agent_profile.model_selection_capabilities` and of the same
> shape; `None` ⇒ the **empty** set, so the real runtime stays fail-closed by default rather
> than by remembering to pass nothing. A driver that can deliver but cannot bind is **not**
> hosted-capable, and a driver that can bind but cannot deliver is **not** hosted-capable
> either. **Both** places that need the answer read **this one function over the same driver
> object**: `bind_prepared_target` at preparation step 4d (P-8), and §3a.13's **deliverability**
> test at delivery step 8. §6 D8, §9.1 I-2, §9.2's seam row and §9.3's Group-2 rows all
> reference HC and must not restate it with a different name list.
>
> **What HC is, and what it is NOT.** HC is a **preparation gate** and a **deliverability
> requirement** for a target. It is **not** a *recognition* condition, and §3a.13 must not use
> it as one. The distinction is load-bearing and it is the whole of correction F-002:
>
> - **as a preparation gate (step 4d):** a hosted launch is only *composed* for a
>   hosted-capable driver. When HC does not hold, `mint_launch_lease` writes **no hosted
>   launch cell**, the prepared session's `--command` stays the profile command, and
>   `bind_prepared_target` returns `not_hosted` and changes nothing. Such a session is
>   **genuinely legacy** and there is nothing hosted about it to recognise later;
> - **as a deliverability requirement (step 8):** if a session **does** carry a hosted launch
>   cell and HC is then absent or incomplete, that is a **capability failure on a hosted
>   target** — a hosted *deliverability* problem — and it is a **named refusal**. It is
>   **never** evidence that the target became legacy. The durable cell is the record that
>   *this physical session was launched in the hosted shape*: its terminal process is the
>   **shim**, and the CLI child reads the **channel**, so routing it to `terminal send` would
>   write the task into the shim's stdin and the model would never receive it. That is exactly
>   the DR-02 hazard, reached by a different door.
>
> **Should the second case be reachable?** Normally no: HC is derived from the run's driver
> object, and the launch record already binds the driver **class** — a mid-run swap is refused
> `PAIR_PREPARATION_BINDING_MISMATCH` (현행 코드). But a rule whose safety rests on
> "unreachable" is what DR-02 was about, so it is a named refusal rather than an assumption,
> and a partially-constructed driver, a degraded object or a repo-vs-installed mirror import
> mismatch are the concrete ways it could happen.

**What does NOT change, and must not:** `_stale_model_evidence` / the authority-history
split; the durable baseline and drift refusals; `pause_policy.resolve_prepared_terminal`
and digest-proved adoption; the reuse gate's nine conditions; pair admission; the
`sessions` use-row discipline; the launch-record binding (which already binds the
**driver class**, so swapping in a real driver mid-run is refused
`PAIR_PREPARATION_BINDING_MISMATCH`); and `pause_store.py` / `pause_policy.py` /
`launcher.py` beyond P-2's schema cell.

**Seven items became eight in this correction, and the eighth is the smallest.** P-8 adds
one harness method and one adapter statement; it adds no durable cell (the `CREATED` write
it follows already carries the digest), no failure member, and no new refusal path.

**Cost summary for one HIGH-risk gate round** (worker + reviewer, same command,
model-aware): 2 processes and **exactly 6 preflight turns + 2 real-task turns**. The number is
**counted off the §3a.2 trace**, not estimated — 2 graph turns × 2 roles verified
unconditionally per pass = 4 pre-pass turns, plus 1 Gate B turn per delivery = 2, total 6 — and
the derivation is written out in **D5**, together with what a validation repair adds. Per-turn
latency is **not** part of this figure and is not a budget commitment (D5). That is the honest
price of per-dispatch re-verification, and D5 is where it is accepted or refused.

### 3a.12 The hosted control channel: the concrete IPC and owner model

Review F-003 refused the earlier description, and correctly: a process cannot `exec` the
CLI and still be the thing that tees its output, and a channel path announced on the
terminal's first line can only be discovered by `terminal read` — which the same section
called non-load-bearing. Both are corrected here.

**Everything in this subsection is a specification, and the exact state of the evidence behind
it is this:** nothing in it is implemented — no socket has been bound, no `bind` frame sent, no
lease minted, by this run or any earlier one. What **is** measured is the *supervisor shape*
the subsection assumes (S-1b: a terminal whose own process relays into the CLI child's pipe
stdin hosted a live `claude -p` and ran two turns) and its *negative* counterpart (S-1a: direct
PTY hosting of `claude -p` exits `rc=1` immediately) — but that prototype used `terminal send`
into the supervisor's stdin, **not** this channel. The five stage-1 probes (X-1…X-5) implement
the state machine **described below** and are evidence about **this specification's internal
consistency**, which is what F-005, F-006 and DR-03 are about; they are **not** evidence about
a working channel or about product behaviour. Corrections marked against X-1/X-2/X-5 below are
therefore specification corrections, and §9.3 Group 3 plus the `[live]` rows of Group 5 are
where the specification would first meet a real process.

**Owner model — the shim SPAWNS and supervises; it does not `exec`.**

| Process | What it is | Lifetime |
|---|---|---|
| the **Orca terminal's** process | `claude_target_shim.py`, the repo-owned supervisor. It binds the endpoint, and — **only after the bind of the bootstrap below** — spawns the CLI as a **child** with `stdin`/`stdout`/`stderr` pipes, then relays: child stdout → (terminal stdout **and** the channel), channel → child stdin. | the terminal's. It is what `terminal show` shows and what the residual-session procedure finds. |
| the **CLI** | `claude -p --input-format stream-json …`, the shim's child | the shim's child, and it is the process every model observation is about |
| the **driver** | in the Coordinator process, the single channel client | the Coordinator's |

The cost of the supervisor is one extra process and one extra relay hop per prepared
session. The alternative — `exec`, so the terminal's process *is* the CLI — was what the
earlier edition said, and it cannot work: after `exec` there is no code left to tee, and
the CLI's own stdout can go to exactly one place. A pty-splitting variant (the shim keeps
a pty and the terminal reads the pty) buys nothing here, because the terminal copy is
operator-facing only and never evidence.

**The endpoint is derived, not discovered.**

- Type: `AF_UNIX` / `SOCK_STREAM`, **not** a FIFO. A listening socket accepts exactly one
  connection and can refuse a second, `close()` is an unambiguous end-of-channel, and
  `open()` does not block on a missing peer. A FIFO gives none of those, which is why the
  earlier edition's FIFO needed an out-of-band announcement to be usable at all.
- Path: `<state_dir>/os14/<run_id>/<sha256(launch_token)[:16]>.sock`, where `state_dir` is
  `$XDG_RUNTIME_DIR` when set and `tempfile.gettempdir()` otherwise. **Never** inside the
  repository or a worktree, and the **digest** — never the absolute path — is what any
  artifact records. **`sun_path` is 104 bytes on this macOS build, not 108** — i.e. at most
  **103** path characters before the terminating NUL, measured when a probe's descriptive
  per-case directory names made `bind()` fail for every path longer than 103 characters
  (실제 관측, `run_fd87428a21f0`; the 108 figure is Linux's and is not this platform's
  budget). So the short digest is load-bearing, not cosmetic, and the implementation must
  **assert** the composed path's length against the platform's own `sun_path` size rather
  than against a constant from this document.
- Creation order: the **harness** creates `<state_dir>/os14/<run_id>/` at `0700` and
  records the path digest in the `CREATE_INTENDED` write (§3a.2 step 4d) — i.e. **before**
  `terminal create`, strictly before the external effect, in a write that already happens.
  The **shim** binds and `listen(1)`s on the path it is given in its argv, `chmod`s it
  `0600`, **arms its bootstrap deadline at that moment** (the Timeouts subsection below:
  the origin is **listener readiness**, not the `hello` write), writes its `hello` frame to
  the first accepted connection — and **spawns nothing** until the harness has bound the
  channel to a created terminal (the bootstrap below). The **driver** connects, with a
  bounded retry deadline for the window in which the shim has not yet bound; the deadline
  expiring is `model_selection_unverified` and **never** a fall back to `terminal read`.
- A path that already exists when the shim binds is a **refusal**, not a reuse: the shim
  exits non-zero without spawning anything, and the terminal shows that. A stale socket
  from a dead run at a colliding digest therefore cannot be adopted. **What this interlock
  is, exactly (실제 관측, X-2):** because the path is derived `sha256(launch_token)[:16]` and
  the shim unlinks its **own** endpoint on every exit path, a *later* incarnation of the
  **same** lease finds the path free and binds it normally. The refusal is therefore a
  **concurrency** interlock — it excludes a *simultaneous* second binder — and **never** a
  freshness one. Freshness is the `shim_incarnation` value below, and nothing else.

**Two hosting requirements that are measurements, not packaging.** Both come from stage 2 and
both are load-bearing for the shim's implementation:

| # | Requirement | Why it is a requirement |
|---|---|---|
| **H-1** | the shim must give its child a **pipe** stdin and relay into it. It must **not** pass the terminal's PTY through to the CLI. | **S-1a, 실제 관측:** a `claude -p` whose stdin **is** the Orca PTY exits `rc=1` with `Error: Input must be provided either through stdin or as a prompt argument when using --print`, before consuming any input and before emitting any `assistant` or `result` event. Direct PTY hosting of `claude -p` is not available on this configuration, so the supervisor's pipe is not an implementation convenience — it is the only shape measured to work (S-1b). |
| **H-2** | every line the shim writes to the child must be **LF**-terminated, and any **CR** the shim received must be converted, not forwarded. | **S-0, 실제 관측:** `orca terminal send --enter` delivers the body **byte-exactly** and terminates it with **one `\r` (0x0d)**. A stream-json reader expects LF. The conversion therefore has to be owned by the supervisor; stage 2's launcher did exactly this (`stdin_mode = unframed_relay`) and the turn parsed. This applies to anything the shim reads from its **own** stdin (the operator, or an Orca `terminal send` that reaches the terminal's process) — frames arriving on the `AF_UNIX` channel are LF-delimited by the protocol itself. |

**Bootstrap — three frames, two authorities, and the one thing the shim cannot know.**

Review F-003 is right that the earlier single shim-authored frame was unsatisfiable, and
the reason is the creation order, not the field list. The shim's whole input is the argv
composed **before** `terminal create` (§3a.2 step 4d); the Orca **handle** comes only from
the create receipt (`handle = created["result"]["terminal"]["handle"]`,
`orca_runtime_harness.py:2996`), and `terminal_digest` is recorded only afterwards, at
`CREATED` (`orca_adapter.py:862-864`). A frame authored by the shim therefore cannot carry
either. So the identity is **pushed to** the shim, never reported by it:

| Datum | Authority | Known to the shim? |
|---|---|---|
| `launch_token`, `session_uuid`, `requested_model`, `run_id`, role / phase / attempt, the endpoint path, `launch_argv_digest` | the **lease**, minted and durably recorded **before** `terminal create` (`CREATE_INTENDED`, `orca_adapter.py:830-838`) | **yes** — they are its own argv |
| the Orca **terminal handle**, and `terminal_digest` | the **create receipt**, held by the harness (`orca_runtime_harness.py:2996`), durably recorded at `CREATED` as `pause_policy.terminal_digest(handle)` (`orca_adapter.py:862-864`) | **no — and it never becomes an assertion of the shim's.** It is told, and it echoes |
| `shim_incarnation` — the **per-incarnation freshness value** (the replay interlock below) | the **shim**, minted at its own startup | **yes** — it mints it. It is shim-authored **and it is not an identity claim**: it names *which run of this shim is speaking*, carries no Orca handle and no durable meaning, and the harness never stores it (so O4 is untouched) |
| `claude_pid`, and the argv actually executed | the **shim**, but only **after** it spawns its child — which it does only after a verified bind | not until then |

`terminal_digest` is a pure `sha256(handle)` (`pause_policy.py:414-416`), so the bind
transmits no secret: the digest is the artifact-safe *form* of the handle, and the handle is
the single post-create datum there is. What the bind establishes is therefore not
confidentiality but an **ordering fact** — the only peer that can echo this nonce is the
peer the harness bound *after* the create receipt existed.

**Frame 1 — `hello`, shim → driver, pre-create fields only.**

```json
{"frame":"hello","protocol":"os14.hosted.v1","launch_token":"<lease token>",
 "run_id":"<run>","session_uuid":"<uuid>","requested_model":"<model>",
 "role":"<role>","phase":"<phase>","attempt":1,"endpoint_digest":"<digest>",
 "launch_argv_digest":"<sha256 of the child argv it WILL execute>",
 "shim_incarnation":"<128-bit hex, minted by THIS shim at its own startup>",
 "shim_pid":1234,"state":"unbound","claude_pid":null,"started_at":"<iso8601>"}
```

Every **lease** field is checked for equality against the lease's own value,
`launch_argv_digest` against the digest of the argv the harness composed. Any disagreement, a
truncated or non-JSON frame, or no frame inside the driver's bootstrap deadline is
`model_selection_unverified`: **no `bind` is sent**, nothing is delivered, `release(handle)`
runs, and the shim — never bound — exits non-zero without having spawned anything. There
are no `terminal` or `terminal_digest` fields in this frame **by construction**, and a
`hello` that contains one is refused; that absence is the whole of the F-003 correction.

`shim_incarnation` is the **one** field the driver does **not** compare against anything it
already holds — it has nothing to compare it to, which is the point. The driver records the
value and is required to **echo it back** in the `bind`; its only job is to make a `bind`
captured from an **earlier incarnation** of this same lease unusable against a **later** one
(the Replay subsection below). It is a freshness value, never an identity: it never appears in
a durable record, it is never read back after the incarnation that minted it has exited, and
nothing in the harness or the preparation document stores it.

**Frame 2 — `bind`, harness/driver → shim, strictly after the `CREATED` write.**

**Cardinality, stated exactly (review N-001).** There is **exactly one bind identity** per
lease — one `bind_nonce`, one handle, one digest, minted once and never re-minted — and that
identity may be **transmitted up to three times** on the **same** connection inside the
driver's bootstrap deadline, byte-identical, to cover an ambiguous acknowledgement. One
logical bind, at most three identical frames; any frame that differs in any field is not a
retransmission but a rebind attempt, and is refused below.

```json
{"frame":"bind","protocol":"os14.hosted.v1","launch_token":"<lease token>",
 "terminal":"<handle>","terminal_digest":"<sha256(handle)>",
 "shim_incarnation":"<the value echoed from THIS connection's hello>",
 "bind_nonce":"<128-bit hex, minted once for THIS lease>","bound_at":"<iso8601>"}
```

The shim verifies, in this order: `protocol` equal; `launch_token` equal to its own argv
token (constant-time compare); **`shim_incarnation` equal to the value THIS process minted at
startup** (constant-time compare) ⇒ otherwise `stale_incarnation`; its own state still
`unbound`; `bind_nonce` well-formed and not already seen **in this incarnation**;
`terminal_digest == sha256(terminal)`. It then records the binding **in memory only**. The
shim writes **no** durable record of the handle — and **no** durable record of the
incarnation value either: the authority for the handle is the harness's `CREATED` write, and
a second writer of it would be a second authority, while a *durable* freshness value would
be a stored secret with nothing to protect it.

**The six non-secret fields this frame carries** are `protocol`, `launch_token`, `terminal`,
`terminal_digest`, `shim_incarnation` and `bind_nonce`, plus the `bound_at` timestamp. The
handle's only transmitted derivative is a pure `sha256` (`pause_policy.py` `terminal_digest`),
there is **no** model string and **no** credential in it, and `launch_token` is the
per-endpoint capability §3a.12 already states is readable by a same-uid `ps`. §9.2's
security-test row asserts exactly this list.

**Frame 3 — `bound`, shim → driver — and then, only then, the child.**

```json
{"frame":"bound","launch_token":"<lease token>","terminal":"<handle>",
 "terminal_digest":"<digest>","bind_nonce":"<echo>","shim_pid":1234,
 "accepted_at":"<iso8601>"}
```

immediately followed, once the child is up, by
`{"frame":"ctl","op":"child_started","claude_pid":1235,"argv_digest":"<sha256 of the argv
actually executed>"}`. The driver requires the `bound` echo to equal — field by field — the
handle, digest and nonce it just sent, and `child_started`'s `argv_digest` to equal the
`launch_argv_digest` it already accepted in `hello`. A mismatched, malformed or absent echo
is driver-local `DRIVER_BIND_UNCONFIRMED` → `model_selection_unverified`: nothing is written
to the child and `release(handle)` runs.

The bind and every later turn share the **one** accepted connection — there is no second
connect on this protocol, which is why `channel_already_bound` (below) stays an impostor
signal rather than a normal reconnect.

**The child is spawned after the bind, not before — and that is a choice with a reason.**
Orca's terminal semantics (the `--command` runs at create, `orca terminal create --help`)
are satisfied by the **shim** being the terminal's process; nothing in Orca inspects the
shim's children, and the hosted path makes no Orca call that waits for a TUI (§3a.13 skips
`wait_for_tui_idle` and `worker-start` entirely). Deferring the spawn therefore costs Orca
nothing and buys three things: the `--model`-carrying argv never runs on an unbound channel;
an endpoint that is never bound spends **no model turn and touches no authentication**; and
a failed bootstrap leaves **no model-aware CLI session** behind for the residual-session
procedure to find — only the shim's own non-zero exit, visible in `terminal show`. The
alternative (spawn at startup and gate only the *delivery* of turns) is one state less in
the shim and strictly worse on all three counts, so it is **not** taken. What does not
change either way: the shim is the terminal's process for the terminal's whole life, so the
handle, the digest, the reuse gate and the durable record keep naming exactly what they name
today.

**Ordering, as six invariants an implementation can be tested against.**

| # | Invariant | Enforced by |
|---|---|---|
| **O1** | the lease — token, uuid, endpoint digest, `launch_argv_digest` — is durably recorded **before** `terminal create` | the existing `CREATE_INTENDED` write (`orca_adapter.py:830-838`), which already runs strictly before the only external effect |
| **O2** | the shim binds, listens and sends `hello` **before** it spawns anything, and spawns **only** after a verified `bind` — and **every** path that does not reach a verified bind is bounded by the one bootstrap deadline armed at listener readiness | the shim's own state machine. An `in` frame arriving while it is `unbound` is refused `unbound_channel` and the shim exits non-zero without spawning. Measured: `child_spawns == 0` in all four client behaviours under both deadline origins (X-1) |
| **O3** | the `CREATED` write (`terminal_digest`) happens **before** the bind | `_prepare_role`'s existing order (`orca_adapter.py:862-864`); the bind is the statement immediately after it (§3a.2, the step marked **P-8**) |
| **O4** | the bind is the **first** appearance of the handle on the channel, and no shim-authored frame ever carries it | the `hello` schema has no such field and the driver rejects a `hello` that supplies one. `shim_incarnation` is shim-authored but is **not** an identity claim — no handle, no digest, nothing durable — so it does not weaken this |
| **O5** | the first model turn is written **only** after `bound` **and** `child_started` | the driver's target state: `select_and_verify` on an unbound target refuses **before** writing any input, so a failed bootstrap costs **zero** turns |
| **O6** | **Gate B binds nothing.** The second driver call re-reads the lease, re-derives `sha256(handle)`, and requires the *existing* binding's handle, digest and nonce to be unchanged | the driver's registry. A Gate B that finds no binding is `DRIVER_TARGET_UNBOUND`, never a fresh bind; a second nonce is never minted for a lease |

**Timeouts: ONE shim deadline, armed at listener readiness.** Review F-005 refused the
earlier description and a probe confirmed it (X-1, 실제 관측). The earlier rule measured the
shim's deadline **from its `hello` write**, and `hello` is written only to the first accepted
connection — so in the exact window the rule exists for, the deadline never started: with **no
client connecting**, the shim was still alive at **5×** its own deadline, had logged nothing
but `listener_ready`, and had **not** unlinked its endpoint. The same held when a client
connected and **died before the `hello` write completed** (`listener_ready`, `accepted`,
`hello_write_failed`, `peer_eof`, then no self-exit and a surviving endpoint). So "no client
ever connects" was not the whole hole, and **moving the origin to the first `accept()` would
still leave it open**; the origin has to be an event the shim observes **before** any peer
exists.

| | Rule |
|---|---|
| **what it is** | **ONE** deadline, `--bootstrap-deadline-ms` in the shim's argv, covering **accept AND bind** together. There is no separate accept timer and no separate bind timer; two timers with two origins are what produced F-005. |
| **origin (start point)** | **listener readiness** — the instant `bind()` + `listen(1)` have both returned successfully, before the first `accept()`. The shim arms it there and nowhere else. |
| **what does NOT start or restart it** | an `accept()`; a successful `hello` write; a failed `hello` write; a peer disconnect; a partial `bind`; a `bind` that fails any check. None of these touch the timer. |
| **who ends it (disarms it)** | **only** the `unbound → bound` transition on a **verified** `bind` — the same frame that authorises the child spawn. Nothing else disarms it, so every path that does not reach a verified bind is bounded. |
| **on expiry** | best-effort `{"frame":"ctl","op":"refused","reason":"bootstrap_timeout"}` on the connection **if** one exists (write errors ignored — in the no-client case there is nobody to tell), then unlink the endpoint, close the listener, and **exit non-zero, having spawned nothing**. |
| **measured** | with a listener-readiness origin the shim self-exited `rc=3` at ~1.23 s against a 1200 ms deadline, with **0** children and the endpoint unlinked, in **all four** client behaviours — including both cases the documented origin left unbounded (X-1). |
| **child spawn** | unchanged and confirmed: `child_spawns == 0` in **every** case under **both** origins (X-1). Spawn-after-bind holds, so a failed bootstrap costs **zero** model turns and leaves no model-aware CLI session behind. |

**Single-verdict reporting, re-derived — because it can no longer be argued from the two
durations.** The earlier text derived it from `driver deadline < shim deadline`. That
derivation is **withdrawn**: the two deadlines now have **different origins** (the shim's
starts at *its own* listener readiness; the driver's starts when *it* begins connecting, which
may be before the listener exists), so their configured lengths do **not** order their
expiries, and this document does not claim they do. The single verdict comes from
**ownership** instead, which does not depend on timing at all:

1. **Only the driver's verdict is reported.** The driver holds the lease, owns the preparation
   step and is the only party that can return `model_selection_unverified` into
   `verify_model_identity` ⇒ `PAIR_PREPARATION_MODEL_UNOBSERVED`. The shim has no channel into
   the harness's verdict at all: its `ctl refused` frame is a **diagnostic the driver may log**
   and is never a second refusal, and its exit status is visible only through `terminal show`.
2. **The shim's deadline is cleanup, whenever it fires.** Whether it fires before or after the
   driver gives up, its effect is the same and is idempotent with the driver's: endpoint
   unlinked, nothing spawned, non-zero exit. `release(handle)` and the shim's own unlink both
   tolerate `ENOENT`, so neither depends on being first.
3. **Both sides are independently bounded.** That is the property F-005 actually needed, and it
   is now true by construction rather than by comparing constants.

**The constants.** Recommended `--bootstrap-deadline-ms = 30 000` for the shim and 15 000 ms
for the driver's bootstrap wait, chosen so the shim normally outlives the driver's wait and the
driver's refusal is the one an operator sees first. **They are configuration constants, not
measurements, and the recommendation does not depend on them being in that order** — no
latency figure in this document is a guarantee (**D5**).

**Replay, rebind, and the asymmetry that makes them decidable.** The shim has **no**
authority over the handle, so it cannot judge whether a handle is stale — and this design
does not pretend it can. Each check lives where its authority is.

**Three different things, measured separately and kept separate here** (X-2 ran them as three
isolated concerns, because conflating them is what let the earlier edition claim a refusal it
did not have):

| # | The case | What it tests | Verdict |
|---|---|---|---|
| **i** | the **identical** `bind` re-sent **on the same connection** — same incarnation value, same nonce, same handle, same digest | **idempotency** | **accepted, idempotently.** Measured: the identical `bound` is re-sent, **one** child exists, state unchanged. This is the ambiguous-ack case the three-frame cardinality above permits, and it must stay accepted |
| **ii** | a `bind` carrying **another lease's** `launch_token` | **authentication** | **refused `bind_unauthenticated`.** Measured: `rc=4`, 0 children, endpoint unlinked. **This case is not evidence about replay** and is never cited as such — it was the earlier plan's mistake |
| **iii** | a **byte-identical `bind` captured from a completed bootstrap of the SAME lease**, replayed to a **fresh incarnation** of that lease | **freshness** | **this is F-006.** Under the earlier memory-only rule it was **ACCEPTED and spawned a child** (실제 관측, X-2): same lease ⇒ same token; fresh process ⇒ state `unbound`; memory-only seen-set ⇒ empty; the frame was valid when captured ⇒ the digest matched. **Every documented check passed.** With `shim_incarnation` it is **refused `stale_incarnation`** with **0** children (실제 관측) |

| Attack / accident | Refused by | How |
|---|---|---|
| a `bind` from something that does not hold the lease token (case ii) | the **shim** | constant-time token compare ⇒ `bind_unauthenticated`, unlink, exit non-zero, no child |
| a `bind` **replayed to a later incarnation of the same lease** (case iii) | the **shim** | its `shim_incarnation` is not the one **this** process minted ⇒ `stale_incarnation`, unlink, exit non-zero, no child. **The seen-nonce set is not what refuses this** — a fresh process's set is empty, which is exactly what F-006 established — and `state == unbound` is not either, for the same reason |
| a `bind` **replayed inside the incarnation that minted the value** (a second connection carrying a captured frame) | the **shim** | state no longer `unbound`, and the nonce already seen **in this incarnation** ⇒ `rebind_refused`. The in-memory seen-set is correct here and only here: it is an *in-incarnation* check |
| the **identical** `bind` re-sent by the same driver on the same connection (case i; at most 3 identical frames inside the deadline) | nobody — it is **idempotent** | the shim re-sends the identical `bound` frame, does **not** re-spawn, and does not change state |
| a `bind` carrying a **different** handle, digest or nonce after one was accepted | the **shim** | ⇒ `rebind_refused`: the channel closes and the endpoint is unlinked; a child, if one exists by then, is left alive and relayed to the terminal only (the unbind rule below). The already-bound driver sees the closed channel on its next write and reports `DRIVER_TARGET_REBOUND` |
| a **stale** bind — one naming a previous session's handle | the **harness**, which is where that authority is | the harness composes a bind only from the handle `terminal create` has just returned, and leg (a2) refuses any lease whose recorded `terminal_digest` ≠ `sha256(ticket.terminal)` (§6 D2 item 3), while leg (h) already pins the ticket's handle at every later call. A stale bind is therefore not producible by the harness, and one produced by anything else fails the token or nonce check above |
| a **second** binding of one handle from the harness side (a repeated preparation pass) | the **driver** | an already-bound handle asked to bind again ⇒ `DRIVER_TARGET_REBOUND`; no second nonce is ever minted for a lease |
| a `bound` echo that does not match what was sent | the **driver** | `DRIVER_BIND_UNCONFIRMED` ⇒ `model_selection_unverified`, nothing written, `release(handle)` |

**What `shim_incarnation` guarantees, and what it does not — stated at the measured
boundary and not one step past it.**

- **보장 (measured, X-2):** a `bind` captured from one incarnation of a lease is refused by any
  **later** incarnation of that same lease, with no child spawned; and adding it leaves case i
  (idempotency) and case ii (the token check) unchanged.
- **비보장, by construction:** a **same-incarnation, same-uid interposer** can read the live
  value — the lease token is already in the shim's argv and readable by a same-uid `ps`
  (§3a.12's standing limit), and the `hello` frame is on a `0600` socket in a `0700`
  directory, which excludes a *different* user and nothing more. A later incarnation cannot
  know a *future* value; a concurrent same-uid party can learn the *current* one.
- **This is not a general replay-prevention claim.** It refuses exactly case iii. It says
  nothing about replay on any other channel, nothing about provenance (§3a.12's "what the
  bootstrap proves" paragraph is unchanged), and it does not narrow the lying-driver limit
  `_verify_model_identity`'s own docstring records.
- **O4 is untouched.** The value is shim-authored but carries **no Orca handle, no digest and
  no durable meaning**, and the harness neither stores nor interprets it. "No shim-authored
  frame ever carries the handle" still holds.

**The lease-minting rule, stated because candidate (A) depends on it and the earlier edition
left it unstated (review F-006's gaps G-A1/G-A2).** F-006's Required Action offered a second
way out — *scope the claim to one incarnation and prove the harness can never launch a second
shim for one lease*. That proof needs a mint rule, so here it is, as a protocol invariant:

> **ONE lease per `terminal create` call.** A `launch_token` is minted exactly once, in the
> `CREATE_INTENDED` write that immediately precedes the create it belongs to (§3a.2 step 4d),
> and it is **never re-exercised by a second create**. Every path that reaches a create mints a
> **new** token and revokes any token the superseded entry held, in that same write:
> the **first** create (`entry is None`), the **retry after a parsed refusal**
> (`CREATE_REFUSED`, where the runtime's own receipt proves nothing was created), and a
> **reuse-gate supersession** (`_reuse_verdict` returning a mapping, which already writes its
> supersession provenance into the same `CREATE_INTENDED` call and increments
> `create_attempt`). There is no "re-exercise the recorded lease across a create" path, and
> D2's phrase "exercised an unrevoked launch this run made" means *exercised by a later driver
> call on the same session*, never *reused by a later create*.

With that rule stated, the **harness** has no path to a second shim for one lease — 현행 코드
confirms the surrounding decision table: `pause_policy.resolve_prepared_terminal` returns
`block` / `PAIR_PREPARATION_OUTCOME_UNKNOWN` **unconditionally** for a `CREATE_INTENDED` entry
(the listing "NEVER changes the verdict"), which is precisely the window that could otherwise
leave a live, unknown first shim. **Candidate (A) nevertheless stays 미검증, and is not
recorded as a resolved alternative**, for two reasons that the mint rule does not remove:

- the rule above is **문서상 설계** — nothing in `_prepare_role` mints anything today, and
  §9.3's `test_superseded_session_gets_a_new_lease_not_the_old_one` is a planned test, not an
  implemented fact;
- a second incarnation of one lease does not require a second **harness** create. It requires
  only that the same argv run twice — an Orca restart of the terminal's `--command`, an
  operator re-run, or a same-uid party re-launching an argv `ps` already shows. None of that
  is observed, and none of it is excluded by the decision table (G-A3: the derived endpoint
  path is a concurrency interlock, not a freshness one).

So **(B) — `shim_incarnation` — is what this design adopts**, because it refuses case iii
outright for one non-durable field and no durable cell, instead of resting on a premise about
what can never happen. (A) is recorded as the unproven alternative it is.

**Crash between `terminal create` and the bind — including the two cases F-005 measured.**
The durable record stands at `CREATED` with its `terminal_digest`, and no binding exists
anywhere. That state is **named, not repaired**: the next driver call reports driver-local
`DRIVER_TARGET_UNBOUND` → `model_selection_unverified` →
`PAIR_PREPARATION_MODEL_UNOBSERVED` (`orca_adapter.py:606-617`), so nothing is delivered and
no re-bootstrap is attempted — the same rule, for the same reason, as the Coordinator-restart
refusal below.

The residual process is the **shim alone**, because no CLI child was ever spawned, and it is
bounded in **all** of these sub-cases by the single listener-readiness deadline:

| sub-case | what the shim observed | bounded? |
|---|---|---|
| the harness crashed before the driver ever connected — **no client at all** | `listener_ready` only | **yes**, by the bootstrap deadline. Under the earlier `hello`-origin rule this case had **no running timer**: measured alive at 5× its own deadline with the endpoint intact (X-1 case a) |
| the driver connected and the peer died **before the `hello` write completed** | `listener_ready`, `accepted`, `hello_write_failed`, `peer_eof` | **yes**. Under the earlier rule this case was also unbounded — and it is why the origin could not simply have moved to the first `accept()` (X-1 case d) |
| the driver read `hello` and then died without sending a `bind` | `listener_ready`, `accepted`, `hello_sent` | yes under either rule (X-1 case b) |
| the driver sent a **partial** `bind` and closed | as above plus a truncated read | yes under either rule (X-1 case c) |

In every one of them the shim exits **non-zero** having spawned nothing and unlinks its
endpoint, and the terminal it ran in is enumerated and closed by the existing
residual-session procedure (`docs/COMPATIBILITY.md`, "Residual session procedure"),
unchanged by this design. If the **harness** is what crashed, its successor finds the
durable lease and refuses it under the restart rule. Either way, the first thing that
touches this handle again is a refusal, and `release(handle)` is called exactly once on the
path that discovers it.

**What the bootstrap proves, and what it does not.** It proves **agreement plus ordering**:
whatever answered on this endpoint holds the lease's token, uuid, requested model and argv
digest, and it accepted — and echoed — a handle binding that could only have been composed
after `terminal create` returned. It does **not** prove **provenance** against a same-uid
impostor on this host — the `0700` directory and `0600` socket exclude a *different* user
and nothing more, and the lease token appears in the shim's argv, so it is readable by a
same-uid `ps`. That is the same limit `_verify_model_identity` already records for a lying
driver (`orca_runtime_harness.py:3798-3812`); adding frames does not narrow it, and the
token confers nothing beyond this one endpoint. Peer-credential checks (`SO_PEERCRED` on
Linux, `LOCAL_PEERCRED` on macOS) would narrow it to "the peer is this uid and this pid";
Python exposes neither portably, so the recommendation is to assert the peer's credentials
**where the platform exposes them** and to treat their absence as an unnarrowed limit rather
than as a failure. 미검증 on this platform; minimum observation: one `getsockopt` on a
local `AF_UNIX` pair on this macOS build.

**Framing and turn correlation.** Newline-delimited JSON in both directions, matching the
CLI's own stream-json shape:

| Direction | Frame |
|---|---|
| shim → driver | `{"frame":"out","ch":"stdout"\|"stderr","seq":<int>,"turn":<int\|null>,"line":"<raw line>"}` |
| driver → shim | `{"frame":"in","ch":"stdin","seq":<int>,"turn":<int>,"line":"<one stream-json line>"}` |
| either | `{"frame":"ctl","op":"ping"\|"close_stdin"\|"child_started"\|"relay_result"\|"refused","reason":"<name>"}`, where `reason` is one of `channel_already_bound`, `bind_unauthenticated`, `stale_incarnation`, `bootstrap_timeout`, `rebind_refused`, `unbound_channel`. `relay_result` is the hop-2 **ack** and carries `payload_bytes`, `child_write_bytes` and `hop2_error` — the three fields §3a.13.1's verdict is computed from |

The three bootstrap frames (`hello`, `bind`, `bound`) are **not** sequenced — the protocol
itself orders them and only one of each may ever appear. `seq` starts at 1 with the first
`out`/`in` frame **after** `bound`, so a stream whose first `out` frame is numbered 1 is
itself evidence that no relayed output preceded the binding.

`seq` is monotone per channel and per direction, so a **gap** is detectable — which is
exactly the hazard `terminal read`'s `oldestCursor` warns about (U-5) and the reason
`terminal read` cannot carry evidence. A gap makes the turn's evidence incomplete:
`model_selection_unverified`.

**Turn attribution uses the in-band markers and treats the shim's `turn` label as
advisory.** The authoritative sequence per turn is the `--replay-user-messages` echo of
the delivered line, then that turn's `system/init`, then its `assistant` event with its
own `request_id`, then its `result` (실제 관측, P1 — three turns, three `init`s, three
distinct `request_id`s, three `result`s). A design that trusted the shim's label would
make the shim trusted for **evidence attribution**, which is precisely the property the
barrier refuses to delegate. The practical consequence: a preflight response and a
real-task response can never be confused even if the shim mislabels both, because the
driver knows which line it wrote and reads the echo of that line.

**Single writer.** The listening socket accepts **one** connection; a second `accept()` is
answered `{"frame":"ctl","op":"refused","reason":"channel_already_bound"}` and closed
immediately. Inside the Coordinator there is one driver object per run and `start_worker`
is single-threaded, so a second in-process writer does not exist by construction; the
socket's single-accept rule is what covers the cross-process case.

**Coordinator restart: a named refusal, not a re-bind.** On a successor Coordinator
the driver's registry is empty while the durable record still names a lease and an endpoint
digest. The driver **refuses** to adopt it — driver-local `DRIVER_TARGET_UNBOUND`, reported
through the existing member `model_selection_unverified`, which `_prepare_pair` then turns
into `PAIR_PREPARATION_MODEL_UNOBSERVED` (`orca_adapter.py:606-617`). The reason is not
caution, it is circularity: after a restart the only evidence that the process on the other
end is the one the record names **is the record**, and the barrier exists to refuse
self-grounding evidence. **Cost, stated:** a Coordinator restart costs every prepared
hosted session in flight, and the operator sees two live terminals the engine will not
reuse. The alternative — a **re-bind** by the successor, with peer credentials plus
`argv_digest` plus a fresh preflight turn — is strictly more code, needs the lease-exercise
rule relaxed across processes, and needs the peer-credential observation above first; it is
recommended **later, not first**, and it is part of D8's pending scope. Note what such a
successor would face: the binding is driver-local in-memory state (O6), so there is nothing
durable for it to read, and a re-bind would be a **new** bootstrap against a child that is
already running — the one case the spawn-after-bind rule cannot cover.

#### 3a.12.1 Cleanup, and the ONE target-lifetime rule — **TL**

**This subsection is the single definition of when a hosted target, its channel and its lease
are disposed of.** §3a.8, §3a.11 P-3, §3a.13, §6 D2's revocation paragraph, §6 D8 and §9.3 all
reference **TL** and do not restate it. Anything that contradicts TL elsewhere in this document
is an error in that place, not a second rule.

**Why it is not "after settlement" (DR-01, 실제 관측 X-3).** An earlier edition called
`release(handle)` "after settlement". `_prepare_pair` verifies **BOTH roles,
UNCONDITIONALLY, on EVERY pass** (현행 코드, and its loop says so in capitals) — the
`delivery_role` parameter scopes **only** `_reuse_verdict`, whose own docstring says the
counterpart "is not a delivery target at all, only a pair-admission participant". So the
**Reviewer's** round always re-verifies the **Worker's** session, after the Worker has
settled. A probe ran the §3a.2 order against the current adapter's call order and measured the
consequence:

| policy | Worker round | Reviewer round | where it stops |
|---|---|---|---|
| release **after settlement** (the earlier text) | completed | **REFUSED** | the Reviewer round's **pre-pass of the `worker` role** ⇒ the released target cannot answer a preflight turn ⇒ `PAIR_PREPARATION_MODEL_UNOBSERVED` |
| release only the **settled role's** target | completed | **REFUSED** | identical — the settled role **is** the role the next round re-verifies |
| **hold until the gate ROUND closes** (TL, below) | completed | **completed** | nothing; a **new** ticket is drawn on every call |

The cheap alternatives are unavailable, and that is 현행 코드 rather than preference: the
ticket token is `run:task:terminal:role:phase:attempt:ordinal`, the pre-pass's `task_id` is
`""` and Gate B's is the real one, so **Gate B structurally cannot accept pre-pass evidence**
(`model_selection_request_stale`); skipping the counterpart's re-verification would delete the
invariant `_prepare_pair` exists for; and putting the settled session through the reuse gate is
already excluded, because the counterpart is not a delivery candidate. The only remaining lever
is the **target lifetime**, which is what TL changes — and the **only** thing it changes.

**The engine order TL has to match — read in this tree, not inferred.** TL is a statement
about when a target may be disposed of, so it is only correct if it matches the order in which
the deterministic engine actually finishes with a role. That order is (실제 관측, code):

| fact | where |
|---|---|
| the graph is `PREPARE_INTENT → EXECUTE_INTENT → VALIDATE_SETTLEMENT → APPLY_RESULT → ROUTE` | `graph_spec.STATIC_EDGES` |
| `settle_attempt` runs inside **EXECUTE_INTENT** (`OrcaAdapter.start` → `run_existing_task` → `wait_for_done` → `settle_attempt`) | `executor.execute_intent_node`, `orca_runtime_harness.run_existing_task` |
| a gate defect is classified **only** in **VALIDATE_SETTLEMENT**, which runs **one node later** — it sets `pending_gate_defect` and `repair_binding = {phase, phase_iteration, role, round_kind}`, and **deliberately sets no `terminal_reason`** | `executor.validate_settlement_node` |
| `kinds == {"FORM"}` (an **equality**) ⇒ `DECISION_GATE_FORM_DEFECT`; any `SEMANTIC` ⇒ `DECISION_GATE_SEMANTIC_BLOCK`; otherwise `DECISION_GATE_LIFECYCLE_DEFECT` | same |
| **ROUTE** returns `PREPARE_REPAIR` **iff** the code is `DECISION_GATE_FORM_DEFECT` **and** `remaining_repair_budget > 0`; otherwise `BLOCK`. The branch sits **after** the decision axis and **before** the capability and quality axes, so `phase_gate` is never consulted for a defective round | `routing.route` |
| `PREPARE_REPAIR` re-enters **`PREPARE_INTENT`** for **the same role, the same phase and the same round** — `current_phase`, `phase_iterations`, `correction_queue` and `correction_index` do not move; `repair_attempts += 1`, `remaining_repair_budget -= 1`, bounded by `MAX_REPAIR_ATTEMPTS = 2` | `graph_spec.ROUTE_TARGETS`, `executor.prepare_intent_node`, `contracts.MAX_REPAIR_ATTEMPTS` |
| **every token other than `PREPARE_REPAIR`** resets the repair domain (`repair_attempts = 0`, `remaining_repair_budget = MAX_REPAIR_ATTEMPTS`, `repair_binding = None`, `pending_gate_defect = None`) — *"Every ordinary preparation opens a NEW gate round, so the repair domain resets"* | `executor.prepare_intent_node` |
| `CANCEL` / `ABANDON` route to the **DISPOSE** node; `BLOCK` / `ESCALATE` / `COMPLETE` to **TERMINAL**; `PAUSE` to **PAUSE** → TERMINAL, which writes **no** terminal status | `graph_spec.ROUTE_TARGETS`, `STATIC_EDGES` |

**The consequence, and it is what the earlier edition of this subsection got wrong:**
`settle_attempt` returning is **not** the engine's "finished with this role" signal. A
malformed decision-gate record is detected a node **later**, and the engine then re-dispatches
**the same role, in the same phase, in the same round**, which re-runs `OrcaAdapter.start` and
therefore step 4f's verification of **both** roles. A rule that released at settlement would
tear down the targets the repair it has not yet heard about is about to need. **The engine owns
the repair decision itself** (`VALIDATION_REPAIR` is in `graph_spec.GRAPH_OWNED_DECISIONS`);
TL only states the target-lifetime consequence and must not restate the routing.

**TL — the rule.**

> A hosted target's **channel and lease** are held from its `CREATED` write until the **gate
> ROUND** that prepared it **closes**. The round is one `(phase, gate iteration)` key together
> with **every** graph turn the engine runs under it — the Worker's turn, the phase-Reviewer's
> turn, **and every validation-repair turn of either role**. `release(handle)` is called for
> every handle prepared under that key, **exactly once**, at round close. It is **idempotent**
> and is the only thing that tears a channel down from the harness side: it revokes the lease,
> closes the socket, unlinks the path, and removes the per-run directory when it is empty.

**Per role, the target has four states — and only two of them permit release.** The state is a
property of the role's *latest* settlement for this key, not of the handle.

| state | entered when | release permitted? |
|---|---|---|
| **`ACTIVE`** | the `CREATED` write, through verification and delivery | **no** |
| **`SETTLED_PENDING_VALIDATION`** | `settle_attempt` has run for this role's Dispatch inside EXECUTE_INTENT, and `VALIDATE_SETTLEMENT` has not yet classified the result | **no — this is the state the earlier edition did not have.** A settlement whose form nobody has checked yet is not a finished role |
| **`REPAIR_PENDING`** | `VALIDATE_SETTLEMENT` produced a `DECISION_GATE_FORM_DEFECT` **and** ROUTE returned `PREPARE_REPAIR` for this same role / phase / round kind / gate iteration | **no.** The repair's own `EXECUTE_INTENT` re-runs `OrcaAdapter.start`, whose step 4f re-verifies **both** roles |
| **`VALIDATED`** | `VALIDATE_SETTLEMENT` produced **no** `pending_gate_defect` for this role's settlement — i.e. the result's form was accepted | **only together with the close condition below** |

**The ROLES REQUIRED BY A ROUND — the set the close predicate quantifies over.** This is
defined first, because the predicate is wrong without it. It is **not** "the roles dispatched
so far"; it is the set the **engine** will not let the round finish without, and the engine
states it in one place (`routing.phase_gate`, 실제 관측):

| run `risk` | roles required by one gate round | the engine's own rule |
|---|---|---|
| **`low`** | the **Worker** only | `phase_gate` returns `PASS` from a `COMPLETE` worker result alone — `if state["risk"] == "low": return "PASS"` — and ROUTE then emits `ADVANCE_PHASE`. No phase Reviewer is ever prepared or dispatched, so none is required |
| **`medium` / `high`** | the **Worker** **and** the **phase Reviewer** | past that same line, `phase_gate` reads `reviewer_result` and returns **`PENDING`** while it is `None`; with a worker result present, ROUTE then emits **`PREPARE_PHASE_REVIEWER`**. The round cannot advance without a Reviewer result, so the Reviewer is **required** — whether or not it has been dispatched yet |

A **prepared-but-not-yet-dispatched** Reviewer is therefore a **required** role in a HIGH/MEDIUM
round, and its state is `ACTIVE` (not `VALIDATED`). That is the whole of the correction below.

**The round-close condition, stated exactly.** A round closes on the **first** of these:

1. **validated completion** — **both** of:
   - **(1a)** every role **REQUIRED by this round** (the table above — HIGH/MEDIUM: Worker
     **and** phase Reviewer; LOW: Worker only) has reached **`VALIDATED`**. A required role
     that is `ACTIVE`, `SETTLED_PENDING_VALIDATION` or `REPAIR_PENDING` — including one that
     has been **prepared but never dispatched** — makes this false; **and**
   - **(1b)** ROUTE has emitted a token that neither re-dispatches **this** role
     (`PREPARE_REPAIR`) nor dispatches **another role of this same round**
     (`PREPARE_PHASE_REVIEWER`). The **closing** tokens are therefore exactly:
     `ADVANCE_PHASE`, `PREPARE_CORRECTION`, `PREPARE_REVALIDATION`, `PREPARE_WORKER`,
     `PREPARE_FINAL_REVIEWER`, `COMPLETE`, `BLOCK`, `ESCALATE`, `PAUSE`, `CANCEL`, `ABANDON`
     — the full `contracts.RouteToken` set minus the two above (실제 관측).

   It is **not** "both `settle_attempt` calls have run" (that fires one node too early, before
   the engine has classified the result), and it is **not** "every role *dispatched* so far"
   (that is true immediately after an accepted Worker turn, when the Reviewer is required and
   has not run — the defect this edition replaces).
2. **refusal or abandonment** — any `_refuse_preparation` on this key, a Gate B refusal, a
   delivery failure that re-raises out of `start_worker`, a `CANCEL`/`ABANDON` disposition, or
   the run leaving this key for any other reason.

**Why clause 1 has two halves, and why neither is redundant.** (1a) says *the round's work is
done*; (1b) says *the engine has moved on*. `VALIDATED` alone is not enough — it describes the
*form* of one role's result, not the round's end. And (1b) alone is not enough either, which is
exactly what the earlier edition got wrong: after an accepted Worker turn ROUTE emits
`PREPARE_PHASE_REVIEWER`, which is not `PREPARE_REPAIR`, so a predicate resting on (1b) and
"dispatched roles" was **true** while the Reviewer still had to run on both held targets.
Excluding `PREPARE_PHASE_REVIEWER` in (1b) and quantifying (1a) over **required** roles make the
two halves agree on every path rather than merely coincide on most of them. With (1a) in force,
(1b)'s `PREPARE_PHASE_REVIEWER` exclusion is belt-and-braces — a round whose Reviewer is already
`VALIDATED` cannot draw that token, because `phase_gate` would have read `reviewer_result` and
returned `PASS`/`FAIL` instead of `PENDING` — and it is kept because a close rule should be
readable without re-deriving the gate.

**Every row of the table below is DERIVED from this predicate, and none of them overrides it.**
Each row names the required-role states and the ROUTE token, so (1a) and (1b) can be evaluated
from the row itself.

**Hold or dispose, case by case.** Every row is a consequence of the two clauses above and of
the engine order above; none of them is a separate rule, and **no row both holds and releases**.

| # | situation | required-role states after the triggering step | ROUTE token | (1a) | (1b) | round closed? | who disposes, and when |
|---|---|---|---|---|---|---|---|
| **1a** | **normal Worker turn at `risk` HIGH/MEDIUM**, result accepted | worker `VALIDATED`; **phase Reviewer REQUIRED and `ACTIVE`** — prepared, never dispatched | `PREPARE_PHASE_REVIEWER` | **false** — a required role is not `VALIDATED` | **false** — the token dispatches another role of this round | **no** | nobody. Both targets stay live, which is what the Reviewer's step 4f needs |
| **1b** | **normal Worker turn at `risk` LOW**, result accepted | worker `VALIDATED`; **no Reviewer is required** | `ADVANCE_PHASE` | **true** | **true** | **yes**, clause 1 | that ROUTE path. This is the only shape in which one accepted Worker turn closes a round, and it is the engine's own rule (`phase_gate`: `risk == "low"` ⇒ `PASS`) |
| **2** | **Worker validation repair** — a FORM defect in the **Worker's** decision-gate record, budget left | worker `REPAIR_PENDING`; Reviewer `ACTIVE` (HIGH/MEDIUM) | `PREPARE_REPAIR` | **false** | **false** | **no** | nobody. **Both** targets stay live: the repair's `EXECUTE_INTENT` re-runs `OrcaAdapter.start`, and step 4f re-verifies **both** roles |
| **3** | **phase-Reviewer validation repair** — a FORM defect in the **Reviewer's** record, budget left | reviewer `REPAIR_PENDING`, worker `VALIDATED` | `PREPARE_REPAIR` | **false** | **false** | **no** | nobody. **Both** targets stay live — including the Worker's **already-validated, already-settled** one, because the repair turn re-verifies it at step 4f just as the Reviewer's first turn did |
| **4** | **repair settles clean** (either role) | the repaired role `VALIDATED`; the other required role as it stood | whatever the gate then yields | **true only if** every required role is now `VALIDATED` | **true only if** the token is a closing token | **only when both are true** — e.g. a repaired **Worker** at HIGH risk yields `PREPARE_PHASE_REVIEWER` and the round stays **open** (row 1a applies); a repaired **Reviewer** yields `ADVANCE_PHASE` / `PREPARE_CORRECTION` and the round **closes** | the path that takes that ROUTE decision |
| **5** | **repair exhaustion** — a FORM defect with `remaining_repair_budget == 0` | that role `SETTLED_PENDING_VALIDATION` → defect classified, no repair available | `BLOCK` (terminal `GATE_REPAIR_EXHAUSTED`) | — | — | **yes**, clause 2 | the terminating path, before TERMINAL |
| **6** | **SEMANTIC or LIFECYCLE gate defect** — never repairable, and **never spends a repair attempt** | that role's settlement classified `DECISION_GATE_SEMANTIC_BLOCK` / `_LIFECYCLE_DEFECT` | `BLOCK` | — | — | **yes**, clause 2 | the terminating path |
| **7** | **Reviewer FAIL** — a well-formed FAIL verdict, no defect | both `VALIDATED` | `PREPARE_CORRECTION` (or `ESCALATE` when the phase budget is spent) | **true** | **true** | **yes**, clause 1 | that ROUTE path. The next iteration is a **new** `(phase, attempt+1)` key whose entries are `None` ⇒ **fresh** sessions and **fresh** leases. **No target, channel or lease is carried across rounds** |
| **8** | **phase PASS** | both `VALIDATED` | `ADVANCE_PHASE` | **true** | **true** | **yes**, clause 1 | that ROUTE path |
| **9** | **BLOCKED** — a role's `worker_done` reports a non-`COMPLETE` outcome, so `phase_gate` returns `BLOCK` | that role `VALIDATED` (the record's **form** was fine; its **content** blocks) | `BLOCK` | — | — | **yes**, clause 2 | the terminating path. Note the ordering: the settlement is still **validated** before this is decided, so row 9 can never pre-empt rows 2/3 |
| **10** | **ESCALATED** — phase or final budget spent | as reached | `ESCALATE` | — | — | **yes**, clause 2 | the escalating path releases every handle of the key. The *run*'s escalation does not change this: TL is per round |
| **11** | **cancel / abandon** | as reached | `CANCEL` / `ABANDON` → the **DISPOSE** node | — | — | **yes**, clause 2 | the disposing path, before TERMINAL |
| **12** | **PAUSE** (`WAITING_FOR_INPUT`, or an admissible `NEEDS_INPUT` / `CONFLICT`) | as reached | `PAUSE` → PAUSE → TERMINAL with **no** terminal status | — | — | **yes**, clause 2 | the pausing path. **Release rather than hold, deliberately:** a resume is a different process, the binding is in-process (O6) and the restart rule is a **named refusal**, so holding across a pause would preserve nothing and would leave two live processes nobody will reuse. The resumed run prepares fresh sessions |
| **13** | **a refusal inside step 4**, before any delivery | the round never got off the ground | — (the refusal raises) | — | — | **yes**, clause 2 | the `_refuse_preparation` path, which already calls `release(handle)` |
| **14** | **Coordinator process exit** | frozen wherever they were | — (nothing runs) | — | — | the round never closes | **nobody, and this document does not pretend otherwise.** The lease registry and the binding are in-process state (O6), so they die with the process; the shim and its CLI child **survive** as residual sessions and are the operator's, through the existing residual-session procedure. A successor Coordinator refuses the durable lease (`DRIVER_TARGET_UNBOUND`, the restart rule above) and never rebinds. A leftover endpoint file can never be adopted, because the path is derived from the launch token and the mint rule gives every create a new token; `release` and the shim's own unlink both tolerate `ENOENT` |

**The worst-case hold is bounded, and the bound is the engine's.**
`MAX_REPAIR_ATTEMPTS = 2` and every non-repair token resets the counter, so one gate round holds
its targets across at most **two** repair turns **per role** on top of the two ordinary turns.
The hold is bounded in *turns*; it is **not** bounded in *wall-clock*, which is the §3a.8 / D5
question and stays **미검증**.

**What TL does NOT decide: whether a repair reuses the held session.** TL keeps the target
*available*; it has no vote on whether the repair delivers into it, and it must not pre-empt the
one authority that does. On a repair of the **delivery** role the session's `session_use` row is
at `DELIVERED` (step 9 recorded it), so `_prepare_role` → `_reuse_verdict` asks the **shipped
reuse gate** through its one production consumer `terminal_for_next_dispatch`, with a **fresh**
liveness observation and the OS-49 model reuse conditions (현행 코드). Two outcomes, both
already implemented today:

- **the gate permits** ⇒ `_reuse_verdict` returns `None` ⇒ the same handle is adopted, and the
  repair delivers into the **held** target. This is the case TL exists for;
- **the gate refuses, or cannot be asked** ⇒ `_reuse_verdict` returns a supersession mapping ⇒
  `_prepare_role` falls through to a **new** `CREATE_INTENDED` with `create_attempt + 1`, mints
  a **new** lease (§3a.12's mint rule), creates a **new** session, and `release(handle)` runs on
  the **superseded** one. That is the existing supersession path, **not** a TL special case.

So TL **does not bypass the reuse gate** — it removes a reason the gate could never have been
asked, because the target had already been torn down. And a repair on the **counterpart** role
never consults the gate at all: `_reuse_verdict` is scoped to `is_delivery_target`, and the
counterpart is a pair-admission participant only (현행 코드).

**TL is NOT authority reuse, and the distinction is the whole reason it is admissible.** Three
lifetimes were tracked separately in the probe, and TL moves exactly one of them (실제 관측,
X-3):

| lifetime | what TL does to it |
|---|---|
| **Dispatch settlement** | **nothing.** The Worker's Dispatch settles correctly and is never reopened. DR-01 was never a settlement defect |
| **current model authority** | **nothing.** It is still revoked on exactly the paths it is revoked on today (`invalidate_model_authority` → `_stale_model_evidence`), and the Reviewer round's observation of the Worker's session is a **new** ticket with a fresh ordinal, never a restored one. Measured: under TL the end-state authority key is a new ticket, not a resurrected one |
| **the Dispatch capability** | **nothing — and it must not try.** **Orca itself revokes the dispatch capability at settlement**: the dispatch row carries `capability_revoked_at == completed_at` (실제 관측, S-3). TL extends the **target/channel/lease** only; it has no claim on Orca's capability and could not hold one open if it wanted to |
| **target / channel / lease** | **this one, and only this one**, from settlement-scoped to round-scoped |

So: Gate B stays mandatory and stays the **first** statement of `start_worker`; no verification
is skipped; the reuse gate's nine conditions are untouched; the authority/history split is
untouched; and nothing is carried **across** rounds. **This design does not add cross-round
reuse and does not add Coordinator-restart rebinding** — both remain named refusals.

**`release(handle)` is still called from every refusal path, unchanged.** Specifically: every
`_refuse_preparation` path, `_revoke_model_authority`, every bootstrap refusal above (`hello`
mismatch, the bootstrap deadline, `DRIVER_BIND_UNCONFIRMED`, `DRIVER_TARGET_REBOUND`), the
EPIPE/dead-target detection of §3a.9, the recognised-but-undeliverable refusal of §3a.13, and
**round close under either clause**. A second call is a no-op and is asserted to be. The shim also
unlinks its **own** endpoint on every exit path, so both unlinks tolerate `ENOENT` and neither
relies on the socket's inode identity — the authoritative "this endpoint is finished" fact is
the revoked lease, not the file.

**TL's cost, stated and not minimised.** The Reviewer's round spends **one preflight turn on
the Worker's post-task session** (it is one of the six counted in **D5**), and the Worker's
shim, CLI child and lease stay live through the **whole Reviewer turn** — and, when the engine
asks for one, through **each validation-repair turn** of either role as well. That inherits
§3a.8's open question and now extends it to the **settled** role: a held process is 실제 관측
only to **90 s** (P1) and to **68.8 s** (S-3's measured interval between two delivered turns),
and a realistic Worker turn is much longer, while a repaired round is longer still. The hold is
bounded in **turns** by `MAX_REPAIR_ATTEMPTS = 2` per role; it is **not** bounded in wall-clock.
**TL is therefore recommended with its hold cost unmeasured**, which is a **D5/D8** input and
not a settled number.

**The shim does NOT terminate its child when the channel goes away.** It unlinks the
endpoint and keeps relaying to the terminal only. That is deliberate: closing the CLI's
stdin exits it `0` (실제 관측, P1 M6), but nothing in this engine closes, reuses or counts
a residual session on its own (`docs/COMPATIBILITY.md`, "Residual session procedure"), and
a shim that tore down its child would change what the operator's procedure finds. **Cost,
stated:** after a restart or a refusal the CLI child stays alive until the operator closes
the terminal — which is the existing contract for every Orca terminal this engine creates,
not a new leak class. The alternative (shim closes stdin after a bounded unbound interval)
is cheaper in processes and is a **change to the residual-session procedure**, so it is not
taken here.

### 3a.13 The hosted delivery branch in `start_worker`

Review F-002 is right that the earlier edition's P-4 was in the wrong place. What it got
wrong in the premise is which call hard-fails, and the difference matters for the design:

| Step | Line | What it does on a `claude -p` target |
|---|---|---|
| Gate B | `:4683-4689` | unchanged, and **first** — ahead of everything below |
| `wait_for_tui_idle(terminal)` | `:4706`, body `:3273-3305` | **does not raise.** `allow_error=True`; records `idle` / `timeout` / `unobserved` and returns. A `-p` process has no OSC title, so the realistic outcome is a full `wait_timeout_ms` of wasted wall clock recorded as `timeout` |
| `worker-start --task … --terminal …` | `:4707-4717` | **called unconditionally.** This is the blocker: a success receipt whose `state` is not `ready` raises (`:4745-4755`), and an error raises unless its code is exactly `agent_unconfigured` (`:4767-4768`) |
| rung 4: `dispatch` + prompt + `terminal send` | `:4769-4790` | reached **only** through `agent_unconfigured`, and its `terminal send` is U-2 |

So replacing the rung-4 `terminal send` alone cannot help: rung 4 is not reached. The
branch has to be taken **above** `worker-start`.

**Admission is a TWO-STAGE test — recognition, then deliverability — and that is the
correction DR-02 forced.** The earlier edition used a single four-way conjunction and routed
**every** false conjunct into "today's code, byte-identical". A probe over five target classes,
three rules and **both** possible `worker-start` receipts measured what that actually does
(실제 관측, X-4): it is true for a **genuine legacy** target and **false for a hosted one**. A
hosted target whose channel is lost **after** Gate B has already passed took today's path
**silently**, and on the `agent_unconfigured` receipt that spent a `tui-idle` timeout,
**created a Dispatch**, wrote the prompt into the **shim's** stdin (the CLI child reads the
**socket**), and **recorded `_attach_terminal(…, "low_level_tracked")`** — for a task the CLI
child never received. `worker_done` never arrives, `wait_for_done` does not settle, and what is
left is an **open Dispatch obligation plus a ledger row that reads like a live tracked
worker**. Which of the two receipts occurs is decided by **U-3, which nobody has observed**, so
the earlier text's central safety claim resolved one way on one branch of an unmeasured call
and the other way on the other. A claim like that is not established.

The split below was measured as the minimal repair, and the cheaper variant was measured to be
**insufficient**: keying recognition on "capability ∧ unrevoked lease" still routes a
**revoked-lease** target into the legacy path, because revocation erases the very fact the rule
recognises a hosted target by (X-4, candidate `split`). Recognition therefore has to rest on
something revocation does not erase.

**Stage 1 — RECOGNITION: is this handle a hosted target at all?** **ONE** condition, owned by
the harness, and not a probe of the terminal (a probe would be a guess about a target the run
itself launched):

| # | Condition |
|---|---|
| **R-1** | the durable preparation entry for this `(phase, attempt, role)` carries a **hosted launch cell** whose `terminal_digest` equals `pause_policy.terminal_digest(handle)` — i.e. *this run recorded a hosted launch for this physical session*. That record is **sufficient**: nothing else is consulted, and nothing can take it away. `launch_revoked_at` is a stamp **on** the cell, so a revoked lease is still a recognised hosted target; and **the driver's current capability is not part of this test** |

**Why recognition rests on the cell ALONE — correction F-002.** An earlier edition made
recognition `HC ∧ the cell`. That had a hole with the shape of the very defect this section
exists to close: a session with a valid hosted launch cell, whose driver exposed **only**
`deliver_hosted` or **only** `bind_target`, was classified *"not recognised"* and sent to
today's legacy `tui-idle` / `worker-start` / `terminal send` tail. But the cell is the record
that this physical session's terminal process is the **shim** and that the CLI child reads the
**channel** — so that route writes the task into the **shim's stdin** and the model never
receives it. **An incomplete capability is a hosted deliverability failure, not a demotion to
legacy.** So HC moved to Stage 2, and recognition is the one fact that cannot be erased by
revocation, by capability loss, or by anything else short of the record itself being gone.

> **Scope of the evidence for this, stated precisely.** X-4 **did not test this combination.**
> Its five target classes are the only inputs it ran, and every *hosted* class (T2…T5) carried
> `c1_capability = True`; the one class with `c1_capability = False` (T1) also had
> `lease_exists = False` — it is described in the probe as *"a real TUI agent terminal: no
> hosted capability, no launch lease"*. The probe's own `split_v2` candidate was
> `recognition = c1 ∧ lease_exists`, i.e. it carries this same hole and was simply never fed an
> input that exposes it. **The correction above is therefore 추론 from DR-02's own safety
> requirement, not a measured result** — what X-4 measured is the *channel-loss* and
> *revoked-lease* classes (T4/T5), which §3a.13 still handles as it did. §9.3's Group-2 rows are
> where the HC-absent-on-a-recognised-target case is actually pinned down.

**Stage 2 — DELIVERABILITY: may it be delivered to right now?** **Four** conditions. A
recognised target that fails **any** of them is a **named refusal**, never the legacy path:

| # | Condition |
|---|---|
| **D-a** | **HC holds** (§3a.11) — `deliver_hosted` **and** `bind_target` both callable on the run's driver object, read through the **same one** derivation `bind_prepared_target` used at step 4d. Absent or incomplete ⇒ cause **`HOSTED_CAPABILITY_INCOMPLETE`** |
| **D-b** | the launch lease is **unrevoked**, its `delivery_mode` is `driver_hosted`, and its `terminal_digest` matches the handle (§6 D2) ⇒ otherwise cause `model_selection_request_stale` |
| **D-c** | Gate B has **just accepted** for this `(task, terminal, role, phase, attempt)`. Structural: the branch lives inside `start_worker`, after the Gate B call |
| **D-d** | the driver returns a **live, bind-complete** target for that handle — `hello` accepted, the harness's `bind` echoed in `bound`, `child_started` seen (§3a.12) — with its last turn's `result` already read. Gate B **re-checks** that binding and never creates one (§3a.12 O6) ⇒ otherwise cause `DRIVER_TARGET_UNBOUND` or `DRIVER_TARGET_REBOUND` |

**The three verdicts.**

| verdict | condition | what happens |
|---|---|---|
| **not recognised** | **R-1 false** — there is **no** hosted launch cell for this handle | **today's code runs, byte-identical** — the `tui-idle` wait, the unconditional `worker-start`, the ready-state requirement, the stateless-receipt version check, the `agent_unconfigured`-only descent, and rung 4. **The legacy path is reserved for exactly this case**: a session this run never launched in the hosted shape, whose terminal process really is a recognised TUI agent. For it the earlier edition's asymmetry claim is true and is preserved exactly |
| **recognised and deliverable** | R-1 ∧ D-a ∧ D-b ∧ D-c ∧ D-d | the hosted branch below |
| **recognised and NOT deliverable** | R-1, and any of **D-a** / D-b / D-d false | **a NAMED REFUSAL — `HOSTED_TARGET_UNDELIVERABLE`** — and never, under any circumstance, the legacy path. **D-a is in this list, which is correction F-002:** an absent or incomplete HC on a recognised target refuses, it does not demote |

**The one hole left, and where it is closed.** Recognition now rests on a single durable fact,
so the only way a hosted target can still reach the legacy path is if its **launch cell goes
missing** — and that case cannot reach here. A preparation document that lost cells it is
required to carry is refused **earlier**, at step 4, by the existing fail-closed readers:
`_assert_preparation_not_lost`, the `PAIR_PREPARATION_RECORD_CORRUPT` path, and (after I-4's
bump) a v4 reader meeting an older document. So preparation never returns a handle whose cell
is absent-but-required, the recognition test reads a record whose presence or absence is
**meaningful**, and the only handles that legitimately have no hosted launch cell are the ones
**no hosted launch ever created** — which, by HC's preparation gate (§3a.11), is exactly the set
of sessions a non-hosted-capable driver prepared. Recognition and the preparation gate are
therefore two halves of one fact, not two independent tests that could disagree.

§9.3 keeps this true from four directions: `test_preparation_schema_v4_rejects_v3` (a lost cell
is a refusal, never a silent legacy demotion), `test_recognition_survives_lease_revocation` (a
revoked lease is still recognised), `test_one_name_driver_is_refused_not_demoted` (an incomplete
HC is still recognised), and `test_legacy_path_requires_no_hosted_cell` (the legacy path is
reserved for the no-cell case).

**What the named refusal does, precisely.** It is raised as the runtime error it is (it is a
**delivery** failure, not a model-selection one, so it adds no member to either closed
vocabulary — D6), carrying the specific driver-local cause: `DRIVER_TARGET_UNBOUND` (no
binding), `DRIVER_TARGET_REBOUND` (a second process under this handle), or
`model_selection_request_stale` (a revoked or mis-bound lease). And it creates **nothing**: no
`wait_for_tui_idle` call, **no `worker-start`**, no `dispatch`, no write, and **no
`_attach_terminal` row**. `start_worker`'s existing `except BaseException` restores the
pre-barrier model snapshot and re-raises; the lease is revoked and `release(handle)` runs once
(§3a.12 TL). Measured: the split gives a named refusal for **both** the lost-channel case and
the revoked-lease case, with nothing created in either (X-4, candidate `split_v2`) — **for the
two classes X-4 ran**. The probe's `split_v2` was `recognition = capability ∧ a hosted launch
cell`, which carries correction F-002's hole; the scope note above says why that never showed
up in its results, and recognition here is the cell **alone**.

**The two windows this closes, named so the test plan can address them separately.**

| window | earlier behaviour | under the split | evidence for the "earlier behaviour" column |
|---|---|---|---|
| channel dead **before** Gate B | Gate B refuses by itself (O6) — nothing created. **This was never the hazard** | unchanged | 실측, X-4 class T3 |
| channel lost **right after Gate B** | legacy path, silently; on `agent_unconfigured` a Dispatch and an attach row for an undelivered task | **`HOSTED_TARGET_UNDELIVERABLE`**, nothing created | 실측, X-4 class T4 |
| lease revoked / mis-bound | legacy path, silently; same two outcomes | **`HOSTED_TARGET_UNDELIVERABLE`**, nothing created | 실측, X-4 class T5 |
| **HC absent or incomplete on a recognised target** (a driver exposing only `deliver_hosted`, or only `bind_target`) | **legacy path, silently** — and this one survived the first correction, because recognition itself was `HC ∧ the cell`. The prompt would be written into the **shim's** stdin while the CLI child waits on the channel | **`HOSTED_TARGET_UNDELIVERABLE`** with cause **`HOSTED_CAPABILITY_INCOMPLETE`**, nothing created | **추론, not 실측** — X-4 never ran this class (every hosted class it did run had `c1_capability = True`, and its one capability-false class also had no lease). The repair is required by DR-02's rule, not demonstrated by a probe |

**What this does NOT do: it does not settle U-3.** `worker-start`'s receipt for a `-p`/shim
terminal is still **미검증**, and no observation in any run has been taken against it. The split
**removes the design's dependence** on that receipt — a recognised hosted target never reaches
`worker-start` at all, on either branch — rather than answering what the receipt would say.
X-4 ran both possible receipts precisely because neither is known, and no result in this
document may be read as a verified U-3 behaviour.

**What the branch replaces, and what it skips.**

| Current step | Under the hosted branch | Why |
|---|---|---|
| `wait_for_tui_idle` | **replaced** by `wait_for_hosted_idle(handle)`: the bootstrap is complete (`bound` + `child_started`), the lease's `session_uuid` was echoed on every event of the last turn, that turn's `result` has been read, and the channel's write end is open. Driver-supplied, harness-called, its own bounded deadline, and a failure is a **hard error** exactly as a failed adoption is today | readiness here means "the stream-json reader is between turns", and that is observable: 8 s and 90 s of empty held stdin produced **zero** events with the process alive (실제 관측, P1 M2), and each turn's own `result` closes it (실제 관측, P1). That the two notions of readiness are equivalent is **추론**, not measured |
| `worker-start --terminal` | **not attempted** | U-3 is documented-adverse for this target. Note precisely what is and is not claimed: this is "we do not ask", **not** "we established that it would fail". Asking would convert a documented-adverse call into a hard error on the delivery path |
| rung 4 `dispatch` | **unchanged, and reused verbatim**: `orca orchestration dispatch --task <task> --to <terminal> --from <owner>`, `result.dispatch.id` (`:4769-4779`) | this is where the **tracked Dispatch** comes from, and it is the id `wait_for_done(dispatch_id, task_id)` settles on. Nothing about the Dispatch is new |
| rung 4 prompt composition | **unchanged** (`:4780-4786`) | and sufficient: it carries `taskId`, `dispatchId` and the `worker_done` instruction and **no** `--dispatch-capability`, which is exactly what the reference agent already handles — it splices a capability into its `send` only when the prompt contained one (`scripts/orca_fake_agent.py:30`, `:147`) |
| rung 4 `terminal send` | **replaced** by `driver.deliver_hosted(handle, prompt)`: one newline-terminated stream-json `user` line written as one `in` frame on the channel, with the **three-way verdict** below. **Corrected:** the earlier edition said "byte count asserted equal to the line length" and that count covers **hop 1 only** | **Corrected reason.** U-2 is no longer the reason — S-0 measured `terminal send --enter` to be **byte-exact** (plus one CR). The reason is **ownership**: on the hosted shape the terminal's process is the **shim**, so `terminal send` reaches the **shim's** stdin while the CLI child reads the **channel**. A write that does not go through the channel does not reach the process the model was verified on. (The CR→LF requirement H-2 of §3a.12 is a second, independent reason not to forward Orca's line verbatim) |
| `_attach_terminal(handle, dispatch_id, "low_level_tracked")` | **unchanged, and still last** (`:4790`) | so a failed write never produces a row that reads like a live tracked worker |

**The dispatch-acquisition options, and why only one is taken.**

| Option | Status | Decision |
|---|---|---|
| `dispatch` without `--inject`, driver delivers the harness-composed prompt | **실제 관측 (code)** — this is rung 4 today, and the reference agent settles from exactly this prompt | **taken** |
| `dispatch --inject` | flag exists on `orca orchestration dispatch --help` (orca 1.4.197, re-read this phase) with **no documented semantics** ⇒ **문서상 지원**, behaviour **미검증**, and injection would go through Orca's own write path, i.e. through U-2 | not used |
| `dispatch --dry-run --return-preamble` to obtain Orca's own preamble and deliver it through the channel | flags exist on the same `--help` ⇒ **문서상 지원**, content and `--dispatch-capability` presence **미검증** | not used in the first implementation; it is the cheap, non-delivering observation worth taking **if** a capability-bearing preamble is later wanted (U-4) |
| `worker-start --terminal` preamble | **미검증, documented-adverse** (U-3) | not used |

#### 3a.13.1 The delivery verdict — **delivered / not_delivered / UNKNOWN**

**This subsection is the single definition of what "delivered" means on the hosted shape.**
§3a.9's process-exit row, §3a.10, §6 D6, §6 D8 and §9.3 reference it and do not restate it. The
earlier edition declared delivery on "the successful full-length write" and compared "the byte
count it wrote against the line length" — a rule written for a **one-hop** stdin write. The
hosted shape has **two** hops, and a probe with real processes and five injected faults
measured what that changes (실제 관측, X-5).

**The two hops, and the byte counts that belong to each.** Confusing them is the defect.

| hop | who writes | who can report it | measured size in the probe |
|---|---|---|---|
| **hop 1** | the **driver**, one `in` frame on the `AF_UNIX` channel | the driver itself, from its own `write()` return | the **outer frame**: **150** bytes written of **150** |
| **hop 2** | the **shim**, into the CLI child's **pipe** stdin | **only the shim**, in a `ctl` ack frame | the **payload**: **73** bytes of **73** |

> **A hop-1 byte count is never evidence that the child received anything.** It is evidence
> that the frame reached the shim. The probe's row 3 shows hop 1 complete (150/150) with hop 2
> **short** (24 of 73), and its row 4 shows hop 1 complete with **no error of any kind** while
> the child was already **dead**.

**The verdict, as the exact test a driver must perform.**

| verdict | condition |
|---|---|
| **`delivered`** | hop 1 wrote the full frame, **and** an ack arrived whose `payload_bytes == len(payload)` **and** `child_write_bytes == payload_bytes` **and** `hop2_error is null` |
| **`not_delivered`** | hop 1 raised; **or** an ack arrived reporting `child_write_bytes < payload_bytes`; **or** an ack arrived reporting a **non-null `hop2_error`** — *including when its byte count is full*. Measured: one fault reported `child_write_bytes = 73` of 73 **and** `hop2_error = BrokenPipeError`, because the shim's write was **buffered** and the **flush** failed. A full count is not a success |
| **`UNKNOWN`** | hop 1 wrote the full frame and **no ack arrived** — the channel closed, the ack deadline expired, or the shim itself died |

**UNKNOWN is irreducible, and that is measured rather than assumed.** Two of the five faults
give the driver an **identical** observation — hop 1 complete, no ack, channel closed — and
have **opposite** ground truth: in one, nothing reached the child; in the other, the child
received the line **in full and completed the task**. **No observation available to the driver
separates them.** Every clause below follows from that, and none of them is a policy choice:

- **UNKNOWN never becomes `delivered`** and never becomes confirmed non-delivery;
- **no auto-resend.** A resend in the "delivered and completed" row delivers the task **twice**;
- **no delivery to another process**, ever: neither row tells you the first delivery did not
  happen;
- **no success settlement from UNKNOWN.** The other row completed nothing;
- **the existing Dispatch obligation is kept.** The Dispatch stays open and operator-visible
  through `delivery_obligations()`, exactly as rung 4's equivalent window does today. It is
  **reported, not resolved** — this design adds no recovery path for it.

**There are TWO short-write windows and one buffered-write trap**, where the earlier edition
named one. And a third correction: **the shim becomes trusted for liveness *reporting*** —
`not_delivered` on a dead child is knowable **only because the shim survived to say so** — while
staying **untrusted for evidence attribution** (the driver keys turn attribution on the line it
wrote and the echo of that line, never on the shim's `turn` label; §3a.12). That is a new,
narrow trust statement, and it is stated here rather than inherited.

**Partial failure, window by window — actor, observation, record, next action.** W1/W3/W4 are
windows rung 4 already has; W2 is restated for two hops and W5 is new.

| Window | What happened | **Actor** (who can see it) | **Observation** | **Record** | **Next action** |
|---|---|---|---|---|---|
| **W0** the normal path | hop 1 full, ack full, `hop2_error` null | driver | `delivered` | `_attach_terminal(handle, dispatch_id, "low_level_tracked")`, **last** | `wait_for_done(dispatch_id, task_id)`, then `settle_attempt`. The ack is **not** the outcome |
| **W1** dispatch created, hop 1 fails | the channel write raises `BrokenPipeError`/`EPIPE`, or `wait_for_hosted_idle` fails | driver | `not_delivered` | **no** `_attach_terminal` row | `start_worker`'s `except BaseException` restores the pre-barrier model snapshot and re-raises (`:4792-4794`); revoke the lease, `release(handle)` (§3a.12 TL). The Dispatch stays open in `delivery_obligations()`. **Never** retried against another process |
| **W2a** hop-1 short write | fewer bytes written than the frame length | driver | `not_delivered` | no attach row | as W1. Benign by construction: the frames are line-delimited, so a truncated frame is a parse failure at the shim, not a half-delivered task |
| **W2b** hop-2 short write | the ack reports `child_write_bytes < payload_bytes` | **the shim**, reported to the driver | `not_delivered` | no attach row | as W1 |
| **W2c** hop-2 buffered-write trap | the ack reports a **full** byte count **and** a non-null `hop2_error` | **the shim** | `not_delivered` — the count is ignored when `hop2_error` is set | no attach row | as W1 |
| **W5** hop 1 complete, **no ack** | the channel closed, the ack deadline expired, or the shim died | driver — and this is all it can ever see | **UNKNOWN** | **no** `_attach_terminal` row: the row must not claim a worker for a delivery nobody can confirm | re-raise, revoke, `release(handle)`. **No resend. No success settlement. No second target.** The Dispatch stays open and is reported through `delivery_obligations()`; if the task *did* arrive, its `worker_done` settles the Dispatch in Orca while **this attempt has already refused** — which is the honest cost and is the same shape rung 4 has today when a `terminal send` succeeds and the engine then fails |
| **W3** verdict `delivered`, `_attach_terminal` not yet run | same window rung 4 has today | driver | `delivered` | not yet written | same handler; the ordering is what keeps the ledger honest |
| **W4** delivered, the process dies before `worker_done` | unchanged | — | `delivered`, then nothing settles | the attach row stands | `wait_for_done` does not settle; the existing settlement and residual-session procedures apply. This design adds nothing here |

**Three completions stay three completions** (§3a.10), and the verdict above is **none** of
them: a **delivery receipt** (the ack) is not the CLI's **`result`**, and neither is
`worker_done` / **settlement**. Measured concretely: the "delivered and completed" fault
produced **no receipt at all** while completing the task, and the clean row's receipt said
**nothing** about the Dispatch. Delivery is declared on the verdict table above, **not** on the
`--replay-user-messages` echo — the echo is a useful *post*-delivery confirmation (실제 관측,
P1/S-1b) and the driver may log it, but making it a gate would make settlement depend on a
second channel, and **settlement is `wait_for_done` and nothing else**.

**Preserved by construction, and asserted by the tests in §9.3:** Gate B strictly before
the branch; pair admission (the branch is below the barrier that enforces it); the
`sessions` use-row discipline (`orca_adapter.py:937`, `:358-363`); the Dispatch,
`worker_done` and `settle_attempt` path; and the `_attach_terminal` provenance value
`low_level_tracked`, which is what this delivery honestly is.

---

## 4. B4 — Attachment target: Orca adapter vs OS-37 standalone

### What each side actually offers today

| | **Orca adapter** (`scripts/deterministic_workflow/orca_adapter.py`) | **OS-37 standalone** (`standalone_adapter.py`, `standalone_drivers.py`, `standalone_runtime.py`) |
|---|---|---|
| Model-selection barrier | **present**: Gate A at profile validation, Gate B before both delivery acts, the pre-pass `verify_model_identity` (`orca_runtime_harness.py:3699-3754`), pair admission (`:3248-3271`), the reuse gate's model conditions (`:2232-2272`), ledger model provenance | **absent**: no `MODEL_SELECTION_*`, no ticket, no evidence state, no pair admission (grep over `scripts/deterministic_workflow/` finds the vocabulary only in `orca_adapter.py`, `fake_adapter.py` and `contracts.py`) |
| Driver seam | `model_driver` injected once and threaded to both gates (`launcher.py:3042-3100`) | its drivers are **process/PTY** drivers (`graceful_hint`, turn-start evidence, completion types) — a different concept that shares the word |
| Does a real CLI already launch there? | no — it dispatches Orca Tasks | **yes** — `ClaudeDriver.argv` (`standalone_drivers.py:1249-1313`) is a measured, shipped `claude -p` launch |
| Capability honesty | declines `external_resume`; declines `model_selection_verified` with its two stated reasons (`orca_adapter.py:148-175`) | declares `external_resume` when the journal and identity fence are wired (`standalone_adapter.py:16-22`) |

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
  harness, the graph or the launcher (`scripts/test_os49_bugfix_regressions.py:945-1032` drives
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
`orca-worker-reviewer-orchestration/tools/`. **No** change to `pause_store.py`,
`pause_policy.py` or `standalone_*`.

**Corrected (OS-14 design run).** This list used to include `orca_adapter.py` and "the
common preparation code". It no longer can: the *attachment point* is indeed one object at
one seam — that part of the recommendation is unchanged and is what makes it minimal — but
*delivering into the verified target* is a second, separable change, and it lands in
`orca_adapter.py` / `start_worker`'s delivery rung. §3a enumerates it. The §3 RETRACTION
is the first record of this correction; this is the same correction applied at the place
that stated the change scope.

**Alternatives, with cost:**

| Alternative | Cost |
|---|---|
| Attach to OS-37 standalone | Re-implements the entire OS-49 barrier in a second runtime, or verifies nothing. Its one real advantage — a measured `claude -p` launch already lives there — is obtainable without the move, by reusing the same launch knowledge inside an Orca-side driver. |
| Attach to both | Two verification implementations free to disagree, which is the exact defect `_counterpart_model_identity`'s docstring (`:3557-3583`) was written to prevent, one layer up. |
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
| **D2** — what is this attempt's model request, when the prepared session outlives the attempt? | the single `create_fake_terminal(...)` call inside the adapter's preparation step, where a model-pinned launch would have to replace a model-neutral one. Note `create_fake_terminal` records `requested_model` as a **separate** ledger field and never concatenates a model into `--command` (`orca_runtime_harness.py:2926-3009`) — **and** that the locked contract value `AGENT_PROFILE_MODEL_SELECTION = declared_driver_capability_only_never_launch_argument` (`scripts/validate_skills.py:938-940`) currently forbids the model being a launch argument at all | **(taken)** keep preparation model-**neutral** and let the driver make its request against the session preparation created. Needs no D2. The alternative — D2 option B's **launch lease**, minted before `terminal create` — also changes the reuse gate's condition-2 key unless condition 2 keeps reading the **profile** command, and it replaces that locked contract value, at a cost of one new seam, one schema bump and one contract-lock edit |
| **D8** — execution lifetime | the `verify_model_identity(...)` call inside preparation, and whether the **same** process must then run the task | **(taken, for the reference driver only)** one self-contained driver call per role: preparation holds no process and assumes no liveness. The alternative — preparation hands the driver a process it must keep alive until delivery — gives preparation a process-ownership and cleanup contract it does not have today. **OS-14 correction run:** the earlier claim that the held `claude -p` path (§3, option (A)) attaches with no change to this code is **retracted**. Whether a change is needed depends on the **binding** between the verified process and the actual task recipient and on the **lifetime** of that binding. **Settled as a recommendation in §3a** (and §3a.11 names the eight concrete preparation/delivery changes, P-1…P-8); the **decision** remains D8's, 승인 대기 — not asserted in §3 |
| **Proof-grade re-adoption of a prepared session across processes** | `pause_policy.resolve_prepared_terminal` and the prepared entry's `terminal_digest` | **(taken)** digest-proved adoption plus **mandatory re-verification** on every pass. A real driver may additionally need a **session-liveness** proof — is the agent process behind this terminal still the one that was verified? — which the durable record deliberately does **not** claim |

---

## 6. Still-undecided items — the D1–D8 decision table

Each is stated as a decision with a recommendation, not as a bare permission request. **None
of them blocks the common pair-preparation work**, which is merged and runs the reference
driver. Every item below carries the same seven fields — **추천안 / 근거 / 보장·비보장 /
변경 범위 / 대안·비용 / 추가 관측 / 사용자 승인 필요 여부** — so the set can be decided in
one pass. The numbers and meanings are unchanged from the earlier edition of this document.

**Neither a DESIGN PASS nor the publication of a design PR is a user approval of any item
below.** Items marked **승인 대기** stay pending until the user decides them explicitly.

| # | Subject | 추천안 (one line) | 승인 필요? |
|---|---|---|---|
| **D1** | request/observation vocabulary names | add `cli_launch_model_flag` **and** `cli_launch_lease_revalidation` (request) + `stream_json_preflight_assistant` (observation) as **named** members | downstream of D2 — implementation decision |
| **D2** | what is **this attempt's** request, when the session outlives the attempt? | **a launch-scoped request, minted and durably bound before the terminal exists, revalidated — never re-pretended — by every later call** (option B) | **YES — 승인 대기. Blocking.** Changes one locked contract value |
| **D3** | the trust standard for the model observation | the four-part conjunction **plus `exact_equality.v1`**: fully qualified model ids only, aliases refused without spending a turn | **YES — 승인 대기** (contract) |
| **D4** | where the ordinals are drawn | `launch_stamp` once per lease, immediately before `terminal create`; leg 1 immediately before the preflight input; leg 2 immediately after the `result` passes all four checks | implementation decision |
| **D5** | verification cost and validity window | re-verify **every dispatch**; same-attempt re-calls are separate verifications on **one** process | **YES — 승인 대기** (operational cost) |
| **D6** | failure mapping | map every measured failure onto the **existing** closed vocabulary; add **no** member | implementation decision |
| **D7** | Orca's own `worker-start --model` | **out of the first scope**; 문서상 지원 only, unexecuted | scope decision, not blocking |
| **D8** | execution target and lifetime | **one dedicated non-interactive process per prepared session, hosted by the Orca terminal, controlled through an `AF_UNIX` channel owned by a supervisor shim (§3a.12), delivered through the hosted branch of `start_worker` (§3a.13)**, with the target/channel/lease held for the whole gate **ROUND** (§3a.12 **TL**) and a **three-way** delivery verdict in which **UNKNOWN is irreducible** (§3a.13.1) | **YES — 승인 대기. Blocking.** Four separable sub-decisions: **D8-a** target, **D8-b** round lifetime, **D8-c** delivery verdict, **D8-d** termination responsibility |
| **permission** | what a dispatched `-p` process may execute | **separate the two**: the **experimental** allow-list that sufficed for one harmless probe is an *observation*, not a policy; the **product** scope for a driver that must read, edit and test code is not written yet (§3a.7) | **YES — 승인 대기, both halves, independently.** Neither is approved by approving D8-a |

### D1 — the vocabulary member names

- **추천안:** **three** members, not two — `cli_launch_model_flag` and
  `cli_launch_lease_revalidation` (request) and `stream_json_preflight_assistant`
  (observation). The second request member is what D2 option B needs and the earlier
  edition did not have: the launch act and the revalidation of a launch are **different
  acts**, and one name for both is how a call with no request act comes to look like one
  that has one. `cli_launch_model_flag` names the launch itself and is the member a
  future shape — or the standalone adapter — would attest; on the Orca adapter path under
  option B, **every** call attests `cli_launch_lease_revalidation`.
- **근거:** both name the **mechanism**, not the provider, matching the existing members'
  style; and the closed sets' own comments say an addition is meant to be a visible,
  reviewable act (`orca_runtime_harness.py:835-843`, `:858-871`).
- **보장:** the addition is forced through review by
  `scripts/test_os49_vocabulary_locks.py`, whose whole purpose is to make the edit visible.
- **비보장 (and the trap this item exists to avoid):** **a name does not make a mechanism
  honest, and adding the two members does not make the verification hold.** The members are
  *labels* for the mechanism D2 and D3 have to authorise. Added without D2, they produce a
  driver that can **name** a request method it is not entitled to make; added without D3,
  they name an observation whose standard nobody wrote down. Naming is the **last** step of
  this change, not the first.
- **변경 범위:** `orca_runtime_harness.py:844`, `:872`, the mirror under
  `orca-worker-reviewer-orchestration/tools/`, `scripts/test_os49_vocabulary_locks.py`,
  `docs/COMPATIBILITY.md`.
- **대안·비용:** provider-named members (`claude_launch_model_flag`) — cheap now, invites a
  per-provider branch in common logic, which C6 forbids. **One member for both acts** —
  cheapest, and it is exactly the defect review F-001 found: a call that made no request
  would attest the same string as a call that did. Or no members at all — then no real
  driver can name a request method and the refusal stands (which is the current, honest
  state).
- **추가 관측:** none. This is a naming decision, not an empirical one.
- **사용자 승인 필요 여부:** **no** as a standalone item — it is an implementation decision
  **downstream of D2**. It must not be merged ahead of D2, because the member's existence is
  what would make the ineligible path look eligible.

### D2 — what is **this attempt's** model request, when the session outlives the attempt? **(blocking)**

**The problem, restated after review F-001 (the earlier edition of this item got it
wrong).** The contract wants, per driver call, one attested **request** and one attested
**observation** (`orca_runtime_harness.py:4265-4281`, `:4347-4366`). But on the
recommended shape the two do not occur at the same rate:

| | how often | where |
|---|---|---|
| the act that carries `--model` | **once per prepared session** | §3a.2 step 4d, **before** any ticket for that session exists |
| an act that observes a model | **once per driver call** — pre-pass (4f), Gate B (step 8), and the counterpart's pre-pass in its own round | inside a ticket window |

The earlier edition's recommendation ("admit an argv composed after the ticket exists,
from that ticket's `requested_model`") is therefore **unsatisfiable as sequenced**: at the
pre-pass the terminal already exists, and at Gate B no `--model` act occurs at all.
**Drawing a fresh ordinal before Gate B's preflight input and calling it a request would
attest an act that did not happen** — a weaker request-presence guarantee than the one the
closed vocabulary has today. That is the defect, and it is why this item is re-stated
rather than re-affirmed.

**Two ways out, and only one of them is admissible under the request's own constraints.**

| | **(A) one honest request act per ticket** | **(B) one launch-scoped request + per-call revalidation** *(recommended)* |
|---|---|---|
| sequence | mint the ticket **before** the terminal exists; the driver performs the launch inside that window; **every later ticket needs its own request act** | mint a **launch lease** before `terminal create`, durably, and let every driver call cite it; each call's own leg is the **observation**, plus a proof the leased session is still the live one |
| what Gate B's request act would be | a **second launch** — a new process, or `--resume … --model` (also a new process) | **none is pretended.** Gate B attests `cli_launch_lease_revalidation`: no new request was made, this lease's session was re-observed |
| admissible? | **no.** The user's own constraint is explicit: the two verification calls of one attempt must **not** create duplicate processes and must **not** swap the verified session for another. A relaunch does both. The only other candidate — a per-turn model field inside `--input-format stream-json` — is **미검증** and nothing in this project has observed one, so it cannot carry a design | yes, at the cost of a **contract extension** |
| ticket shape | changes: `terminal` becomes an intent, and the six-part `observed_at_*` key changes with it (`:4347-4366`) | **unchanged** — `terminal: str` is still an existing session |
| what it costs if taken | two processes per delivered role per round, and the verified-then-discarded first process is exactly the "verify a probe, deliver elsewhere" shape §3a.0 excludes | the request leg's meaning widens from "this attempt asked" to "this attempt exercised an unrevoked launch this run made and durably recorded" |

**추천안 — (B), with the launch leg moved OFF the driver's self-report.** This is the part
worth stating plainly, because it makes (B) a *strengthening* rather than a concession:

- **today** the request leg is a driver **claim** the harness admits it cannot check — "a
  driver that deliberately lies … is undetectable from inside the harness"
  (`orca_runtime_harness.py:3798-3812`);
- **under (B)** the launch is performed by the **harness**: it composes the argv from the
  routing's declared model, draws `launch_stamp` from its **own** monotone counter
  immediately before `terminal create`, and writes the lease into the `CREATE_INTENDED`
  record that already precedes the external effect. The driver supplies the argv *template*
  and reads the stream; it does not attest the launch. So the request leg becomes, for the
  first time, a fact the harness **owns** rather than one it trusts.

**The contract extension, stated as the exact edits a reviewer would look for:**

1. **`MODEL_SELECTION_REQUEST_METHODS` gains two members** (`:844`):
   `cli_launch_model_flag` — admissible only on a call whose ticket window contains the
   launch act, which under (B) is **no call at all**, so it is added for the standalone/
   future shape and is **refused on the Orca adapter path** until a shape that can satisfy
   it exists; and `cli_launch_lease_revalidation` — the member every Orca-path call uses.
   Adding the first without the second is what would let an unauthorised path look
   eligible, which is D1's 비보장 in its concrete form.
2. **`ModelEvidence` gains two attested fields** (`:1009-1019`): `launch_token: str = ""`
   and `launch_stamp: int = 0`, both **required** when `request_method ==
   cli_launch_lease_revalidation`. Additive with defaults, so all existing constructions
   bind unchanged, and both go into the hoisted fifteen-field type gate (`:4219-4258`),
   which becomes seventeen.
3. **New barrier legs, inserted as (a2) immediately after leg (a)** — all of them integer
   arithmetic and string equality over values the harness itself issued, which is the
   same thing every other leg is, and **no new failure member** (D6 holds):

   | check | refusal if it fails |
   |---|---|
   | the lease named by `launch_token` exists in the harness registry **and** in the durable entry for `(phase, attempt, role)` | `model_selection_request_absent` |
   | `lease.requested_model == routing's requested model` | `model_selection_mismatch` |
   | `lease.handle == terminal` and `lease.terminal_digest == pause_policy.terminal_digest(terminal)` | `model_selection_request_stale` |
   | `attested launch_stamp == lease.launch_stamp` and `0 < launch_stamp < request_stamp` | `model_selection_request_stale` |
   | the lease is not revoked | `model_selection_request_stale` |

4. **The two per-call ordinals keep their exact arithmetic** — `issued+1`, `issued+2`,
   counter ending at `issued+2` (`:3959-3968`, `:4336-4346`) — so
   `test_os49_delivery_barrier.py` needs no edit. What changes is `request_stamp`'s
   *documented meaning*: "the ordinal at which this attempt exercised the launch lease",
   drawn immediately before the preflight input is written. `launch_stamp` is a **third**
   ordinal from the same counter, drawn once per lease, outside every ticket window — so
   launch and observation are totally ordered in one sequence and a launch cannot be
   back-dated behind an observation.
5. **The locked contract value changes.**
   `AGENT_PROFILE_MODEL_SELECTION = declared_driver_capability_only_never_launch_argument`
   (`scripts/validate_skills.py:938-940`; mirrored at
   `orca-worker-reviewer-orchestration/SKILL.md:2009` in an anchor block asserted to be
   **exactly 25 lines** by `scripts/test_os49_contract_locks.py:99-111`) is **false under
   (B)**: the model becomes a launch argument. It must be **replaced**, in the block, in the
   validator and in the lock test, in one commit — e.g.
   `declared_driver_capability_and_leased_launch_argument_never_wrapper_argv`. The 25-line
   cap means it is a replacement, not a 26th line. **This is the single most reviewable
   consequence of approving D2, and the earlier edition did not name it.**

**Binding — token / task / attempt, stated exactly.**

| Bound to | How | Consequence |
|---|---|---|
| the **run** | `launch_token` carries `run_id`; the registry is per-harness | another run's lease is `request_absent` |
| the **session** | `terminal_digest` written at `CREATED`; `session_uuid` echoed on every event of every turn (실제 관측, P1/P2) | a lease cannot be exercised against a different handle |
| the **attempt** | the lease records the `(phase, attempt, role, create_attempt)` that minted it; the **evidence** is still keyed to the six-part `observed_at_*` key including `attempt` | the *evidence* is never carried across attempts; the *request* can be, and only while the next check holds |
| the **task** | not at all, deliberately — the pre-pass runs with `task_id=""` and Gate B with the real id, which is what makes Gate B structurally unable to accept the pre-pass's evidence (§3a.2) | unchanged |

**The honest residual, and it is the reason this is the user's decision.** Under (B) a
later gate iteration that **adopts** the same live session delivers on a `--model` act
made by an **earlier** attempt. The protections that remain are: the lease binding is
unforgeable (the token comes from the harness's counter and the durable record), the
session identity is digest- and uuid-proved, the model is **re-observed on every call**,
and the run-scoped `(phase, role)` durable baseline plus leg (i) refuse any drift
(`orca_adapter.py:618-637`, `orca_runtime_harness.py:4400-4411`). What is **not**
preserved is the sentence "the request was made by this attempt". The stricter variant — a
lease exercisable **only** within the attempt that minted it — is available and costs a
fresh process per role per gate iteration; it is named here as the alternative rather than
recommended, because the **reuse gate** is already this repository's single authority on
whether a used session may serve again, and inventing a second, stricter one beside it
would give two gates one job.

**The two acts, side by side — this is the distinction the decision turns on.** They are
*different acts* with *different rates*, and giving them one name is the defect review F-001
found:

| | **the first launch request** | **a per-call lease revalidation** |
|---|---|---|
| attested member | `cli_launch_model_flag` | `cli_launch_lease_revalidation` |
| how often | **once per prepared session**, at §3a.2 step 4d | **once per driver call** — the pre-pass, Gate B, and the counterpart's pre-pass in each round |
| who performs it | the **harness**: it composes the argv from the routing's declared model, draws `launch_stamp` from its own counter, and writes the lease in the `CREATE_INTENDED` record that already precedes the external effect | the **driver**, inside its single `select_and_verify` call: it cites the lease by token and re-observes the model on a fresh preflight turn |
| what it asserts | "a `--model` act was made, by this run, for this session, at this ordinal" | "**no new request was made**; this lease is unrevoked, still bound to this handle, and its session re-observed the same model" |
| what it must never assert | — | that a request occurred. **Drawing a fresh ordinal before Gate B's preflight input and calling it a request would attest an act that did not happen** |
| admissible on the Orca adapter path? | **no** — no driver call's ticket window contains a launch, so the member exists for the standalone/future shape and is **refused** here until a shape that can satisfy it exists | **yes** — this is the member every Orca-path call uses |

**Round attribution — which round a lease belongs to, and what that does and does not mean.**
A lease is minted inside one `(phase, attempt, role)` preparation entry and records that key
(plus `create_attempt`), so it is **attributed** to exactly one gate round. Three consequences,
stated because they are easy to confuse with each other:

- **the lease's DISPOSAL is round-scoped**: it is revoked at round close, per **§3a.12 TL** —
  not at the settlement of the Dispatch that used it, which is what DR-01 was about. TL is the
  one definition; this item does not restate it;
- **the lease is never carried ACROSS rounds.** The next gate iteration is a new `(phase,
  attempt+1)` key whose entry is `None`, so it creates fresh sessions and mints fresh leases
  (the **mint rule** in §3a.12: one lease per `terminal create`, never re-exercised by a second
  create). This design adds **no** cross-round reuse;
- **the EVIDENCE is still never carried at all** — not across rounds and not across calls
  within one round. The six-part `observed_at_*` key includes `attempt`, and the pre-pass's
  `task_id` is `""` while Gate B's is the real one. The *request* can be cited again by a later
  call **in the same round**; the *observation* never can.

**Revocation — one function, every path.** `release(handle)` revokes the lease, and it is
called from every `_refuse_preparation` path, from `_revoke_model_authority`, when
`_reuse_verdict` supersedes a session (`orca_adapter.py:867`, in which case a **new** lease
is minted for the replacement in the same `CREATE_INTENDED` write that publishes the
supersession), on the EPIPE/dead-target detection of §3a.9, on `DRIVER_TARGET_REBOUND`, on
§3a.13's `HOSTED_TARGET_UNDELIVERABLE` refusal, and at **round close** (**§3a.12 TL** —
corrected twice: "after settlement" was what DR-01 measured to refuse the Reviewer round
outright, and "both roles settled" still fired one node too early, before the engine has
classified the settled record and possibly re-dispatched the same role as a validation repair.
Round close is the ONE predicate in §3a.12.1: **every role REQUIRED by the round** —
HIGH/MEDIUM: Worker **and** phase Reviewer, a prepared-but-undispatched Reviewer included;
LOW: Worker only — is `VALIDATED`, **and** ROUTE's token is neither `PREPARE_REPAIR` nor
`PREPARE_PHASE_REVIEWER`). A revoked lease is dropped from the registry and stamped
`launch_revoked_at` in the durable entry; a later citation is `model_selection_request_stale`.
Idempotent, and asserted to be.

**One thing the lease must NOT try to hold: Orca's dispatch capability.** The dispatch row
carries `capability_revoked_at == completed_at` — **Orca itself revokes the dispatch capability
at settlement** (실제 관측, S-3). The launch lease governs the **target, channel and model
request** and has no claim on that capability; TL extends the former and could not extend the
latter if it wanted to.

**Pair admission is untouched.** It reads *verified evidence per role per phase*, which is
still produced fresh by every call (`require_pair_admission=True` at Gate B,
`False` at the pre-pass — `orca_runtime_harness.py:3670-3697`). A lease adds a required
conjunct to each call; it removes none, and it cannot admit a pair whose counterpart has no
verified evidence.

- **근거:** (A) is excluded by the request's own no-duplicate-process / no-session-swap
  constraint, and its only non-relaunch instantiation is 미검증. (B) is the only remaining
  shape that keeps Gate B mandatory, keeps both calls' evidence separate, creates exactly
  one process per prepared session, and does not attest an act that did not occur. The
  exclusion comment's actual target — "a model-pinned wrapper's argv was composed **before
  this session existed**" (`:835-837`) — stays excluded: a lease is composed **for** this
  session, by this run, after the routing declared the model, and it is revocable.
- **보장:** the ordinal arithmetic, `selection_token` equality, the closed-set membership
  checks, the six-part freshness key and leg (i)'s consistency check all keep their exact
  current meaning; a pre-composed wrapper argv stays inadmissible; the launch leg stops
  being a driver self-report; exactly one process is launched per prepared session; a
  revoked or mis-bound lease cannot be re-exercised.
- **비보장:** it does **not** make an argv evidence of a resolution — leg 2 still observes
  (§2/D3). It does **not** make the request per-attempt on an adopted session (the residual
  above). It does **not** prove provenance against a same-uid impostor (§3a.12). It does
  **not** decide the lifetime (D8) or the satisfaction rule (D3).
- **변경 범위:** items 1–5 above, plus §3a.11's P-1, P-2, P-3 and P-7, plus the
  `.pair_preparation.json` schema bump (v3 → v4) and `docs/COMPATIBILITY.md`.
  **Explicitly preserved:** the model stays a separate ledger field; the reuse gate's
  condition-2 identity keeps comparing the **profile** command, never the composed argv
  (`:2978-2986`).
- **대안·비용:** **(A)** — inadmissible, as argued. **Decline both** ⇒ §1's recommendation
  is withdrawn and **no** recommendable real-driver path exists under the current contract
  (cost: OS-14's user-visible goal is unreachable as specified; the honest status quo is
  the fail-closed refusal). **Accept broadly**, admitting any model-pinned argv ⇒ cheap, and
  it readmits exactly the "evidence of what was asked of the OS" the exclusion exists to
  keep out. **(B) with the stricter per-attempt lease** ⇒ one extra process per role per
  gate iteration, and a second authority beside the reuse gate.
- **추가 관측:** none would settle the contract question. One observation would narrow the
  *excluded* option rather than the recommendation: whether `claude -p --input-format
  stream-json` accepts a per-message model field at all (미검증). It is **not** worth a turn
  from this run's remaining budget, because a positive result would still leave (A) needing
  a second launch for the *process*-scoped flags, and a negative result changes nothing.
- **사용자 승인 필요 여부:** **YES. 승인 대기.** It changes the OS-49 per-attempt request
  contract and one locked contract value. **Required before any real-driver work starts, and
  before D1's members are added.**


### D3 — the independence standard for leg 2

- **추천안:** `verified` requires **all four** conjuncts below, for the **preflight turn**,
  and nothing less. Each is stated as the exact comparison a driver must perform:

  | # | Channel | Exact condition |
  |---|---|---|
  | 1 | **`system/init.model` of the preflight turn** | the `system` event with `subtype == "init"` **belonging to this turn** — i.e. the one emitted after this turn's input and before this turn's `result` — has `model` **string-equal to `ticket.requested_model` itself**, byte for byte, with no normalisation (see the satisfaction rule below). Measured: one `init` **per turn**, three turns, three `init`s (P1). |
  | 2 | **`assistant.message.model` of the SAME turn** | **EVERY** `assistant` event of this turn has `message.model` **string-equal to the same value** — not "the" assistant event, because a turn emits several (measured: 2 in turn 1 and 5 in turn 2, S-4). One disagreeing event is a refusal, not a tie-break. "Same turn" is established by **turn membership** (below), **never** by `num_turns` (`1` on all three P1 turns, and `1` / `3` on S-4's two turns). |
  | 3 | **`session_id`** | **every** event read for conjuncts 1, 2 and 4 carries `session_id` **string-equal** to the uuid this driver passed as `--session-id` for this prepared session. Measured on every event of every turn (P1, P2; all 23 events of S-1b/S-3). |
  | 4 | **`assistant_request_id`** | **the SET of `request_id`s this turn's `assistant` events carry** is non-empty, every member is present, non-empty and matches `req_…`, and the set is **disjoint** from the union of every previous turn's set on this session. It is used for **turn attribution**, never as a model channel. |

  **Turn membership, and why conjunct 4 is a SET — measured, not assumed (S-4).** The raw
  stream of two input turns was walked end to end, and it refutes two assumptions an
  implementation would naturally make:

  | measured | consequence for the rule |
  |---|---|
  | **one `request_id` can span several `assistant` events.** Turn 1 carried **one** `request_id` across **two** `assistant` events (a `thinking` block then a `text` block), both with the same `message.id` | "the assistant event's `request_id`" is not well-defined. Conjunct 2 must hold for **all** of a turn's `assistant` events, and conjunct 4 must be a set membership test |
  | **one input turn can span several `request_id`s.** Turn 2 carried **two** `request_id`s across **five** `assistant` events, because each `Bash` tool round-trip opens a new API request inside the **same** input turn | **there is no one-`request_id`-per-turn rule**, and a driver that required exactly one would refuse a correct turn. The design must not assume it, and this document does not |
  | `request_id` is on the **event's top level**, not on `message` | read it from the event, not from `message.request_id` (which is absent) |
  | the turn's **`result`** is what closes it; `system/commands_changed` events arrived **after** the final `result` | turn membership = *everything between this turn's own input and this turn's own `result`*, and anything after a `result` belongs to **no** turn — it must not be folded into the next one |
  | three event shapes the earlier design does not name appeared: `system/thinking_tokens`, `system/commands_changed`, and `assistant` events whose only content block is `thinking`; `rate_limit_event` appeared at **two different positions**, once **before** the replayed `user` echo | the "tolerate unknown event types" rule below is not hypothetical. Note also that a `system` event is only `init` when `subtype == "init"` — conjunct 1 is already subtype-scoped and must stay so |

  **Turn membership, as the exact rule:** a turn opens when the driver writes its input line
  and is confirmed by the `--replay-user-messages` echo of **that exact line**; it contains that
  echo, that turn's `system/init` (`subtype == "init"`), every `assistant` event up to and
  including that turn's `result`, and nothing after that `result`. The driver knows which line
  it wrote, which is why this does not depend on the shim's `turn` label (§3a.12).

  **The satisfaction rule: `exact_equality.v1` — fully qualified model ids only, no
  aliases.** Review F-004 refused the earlier edition's basis, and correctly. "Take the
  canonical resolution from the same preflight turn being verified" is circular: agreement
  between `init.model` and `assistant.message.model` proves those two reported fields agree
  with each other, and nothing more. It does **not** establish that `sonnet` is satisfied by
  `claude-sonnet-5`, and it would **not** detect a turn that consistently reported
  `claude-haiku-4-5-20251001` for a `sonnet` request. The measured
  `sonnet` → `claude-sonnet-5` pair is one version-scoped observation on `claude 2.1.260`
  (실제 관측, P1/P2), not an algorithm.

  The rule, versioned so that widening it is a visible act:

  | | |
  |---|---|
  | **name** | `exact_equality.v1`, recorded in the driver's class docstring and in `docs/COMPATIBILITY.md` |
  | **authoritative test** | `reported_model == ticket.requested_model`, pure string equality. **No** alias table, **no** case folding, **no** whitespace trimming, **no** prefix or family matching, **no** "resolution" step |
  | **pre-turn screen** | before any input is written, the driver refuses a `requested_model` that does not match a **fully-qualified** pattern (the measured shapes are `claude-sonnet-5`, `claude-opus-5`, `claude-haiku-4-5-20251001`; note `agent_profile.MODEL_TOKEN_PATTERN` — `[A-Za-z0-9][A-Za-z0-9._+-]*`, `scripts/agent_profile.py:117` — accepts `sonnet` too, so the driver needs its **own**, stricter pattern). Verdict `unverifiable` ⇒ `model_selection_unsupported`, and **zero model turns are spent** |
  | **why the screen cannot be load-bearing** | correctness rests on the equality test, not on the pattern. Even a pattern that wrongly admitted an alias would still be refused by equality, because an alias is by definition not what the CLI reports. The screen only saves a turn and gives a clearer refusal |
  | **an alias request** | `model_selection_unsupported`, not `mismatch`: the driver has **no supported way to decide** whether the request is satisfied, which is a capability failure. (`mismatch` is the alternative and is less accurate — it asserts the resolved model is wrong, when what is actually true is that satisfaction is undecidable here.) Both are existing members; D6 still adds none |
  | **a changed canonical spelling** | `mismatch`, correctly: the run asked for X and the CLI reported Y. The observed `claude --version` is recorded with the evidence, so a CLI update that renames a canonical id surfaces as a refused dispatch rather than as a silent re-interpretation |
  | **company gateway aliases** | **explicitly unsupported** until independently mapped (§7). A gateway that echoes the request instead of resolving it would make equality trivially true, which is the failure mode this rule is least able to see — so it is excluded by scope, not by test |
  | **widening it** | a **new rule version** plus its own approval. An alias table is never added silently, and `exact_equality.v1` is what the first implementation ships |

  **The operational consequence, stated because it is user-visible:** leg (g) requires the
  evidence to echo back the routing's own `requested_model` unchanged
  (`orca_runtime_harness.py:4368-4372`), so the driver cannot rewrite an alias into a
  canonical id. An agent profile that declares `model: sonnet` is therefore **refused** on
  this driver; profiles must declare `claude-sonnet-5`. That is a configuration change, not
  a code change, and it is the price of a fail-closed first rule.

  **`modelUsage` is read for CONTAINMENT only, never for equality.** Measured this phase:
  P1 turn 1 held `['claude-sonnet-5']`, while turns 2 and 3 held
  `['claude-haiku-4-5-20251001', 'claude-sonnet-5']` on `subtype=success`, `is_error=false`.
  An equality test would have reported a mismatch on two successful turns that resolved
  exactly the requested model. **`subtype` / `terminal_reason` are read separately from
  model evidence.**

  **Unknown event types must be tolerated.** `rate_limit_event` appeared this phase at a
  **varying** position (after `assistant` in P1 turn 1, before it in P2) and is named nowhere
  in the earlier design. A parser that assumes a fixed sequence, or that refuses unknown
  types, breaks on a CLI update. The driver selects events **by `type` and turn membership**,
  not by position.

  Write down, in the driver's own docstring, that the reference driver's echo
  (`fake_adapter.py:291-295`) does **not** meet this standard and is never cited as a model
  verification.
- **근거:** no single channel is sufficient. `init.model` arrives within ~0.1 s of the input
  (실제 관측, P1) ⇒ client-side resolution (추론), so it is the client's own reading of the
  request, not a provider verdict; `message.model` being response-derived is also 추론; `modelUsage` is only
  available at turn end and carries unrequested keys; the **issuer** of `assistant_request_id`
  was never observed.
- **보장:** the strongest locally obtainable standard; evidence is bound to **one turn of one
  session identified by a caller-chosen uuid**; absence is never a pass; no single-channel
  verdict.
- **비보장 — and this is the field that must not be softened:**
  - **not server-independent.** Five agreeing CLI code paths are a **consistency** argument
    (추론), not independence. Nothing local proves what a provider processed.
  - **the standard is the CLI-REPORTED model, and the API-served model is 미검증.** This is
    stated as a limit rather than as a caveat because the strongest observation available
    reaches exactly this far and no further: in S-1b/S-3 `system/init.model` **and** every
    `assistant.message.model` of both turns reported the fully-qualified argv id
    `claude-haiku-4-5-20251001`, with `apiKeySource = "none"` (personal auth, no API key) and
    one session id on all 23 events. That is **two agreeing CLI channels plus a known
    launch argument** — it is not a provider receipt, and no channel in this project is. A
    driver must therefore name its verdict as a statement about what the **CLI reported**, in
    its own docstring and in `docs/COMPATIBILITY.md`, and must never present it as what was
    served.
  - **the preflight observation does NOT guarantee the model of the next turn.** What it
    establishes is exactly: *the turn immediately before the real task, in the same process
    and the same `session_id`, reported this model.* It is **not** a statement about the
    real-task turn. No measurement in this project tests drift **within** one process in the
    adverse direction, and the correct reading of P1's three same-model turns is "no drift
    was observed in three turns", not "drift cannot occur".
  - a driver that deliberately lies about leg 1 stays undetectable from inside the harness
    (`orca_runtime_harness.py:3798-3812` says so already).
- **변경 범위:** the driver module, `docs/COMPATIBILITY.md` (the rule's name and scope) and
  this document. **No common code** — and no agent-profile schema change either, though
  profiles that declare an alias must be edited to declare a fully-qualified id.
- **대안·비용:** `init.model` alone — cheap, wrong (client-side resolution read as a provider
  verdict). `modelUsage` equality — wrong, and now **measured** wrong twice in one probe.
  **An alias table in the driver** — it is what makes `sonnet` usable, and it goes stale
  silently on a CLI or gateway change, which is exactly the failure a fail-closed first rule
  must not ship with; it is the natural `v2` **after** an authoritative resolution source
  exists. **Asking the CLI to resolve the alias in a separate, discarded turn** — costs a
  turn, and the answer is still the same CLI reporting about itself, so it buys consistency
  and not independence. Post-hoc verification of the real-task turn **in addition** —
  strictly more evidence, at the cost of a second evidence path, a second failure mapping and
  a new question about what to do when it disagrees after the work is done; recommended
  **later**, not first.
- **추가 관측:** (a) whether `assistant_request_id` survives a company gateway at all — the
  single most important company-environment check, since conjunct 4 depends on it (§7);
  (b) a deliberate adverse drift test **within** one process, if the non-guarantee above is
  to be narrowed rather than merely stated; (c) an authoritative alias→canonical source
  (anything other than the turn being verified) if `exact_equality.v1` is ever to be widened.
  **None of the three is spent from this run's budget**, and none of them is needed for the
  recommendation above, which is deliberately the option that needs no new observation.
- **사용자 승인 필요 여부:** **YES — 승인 대기.** It is the contract that defines what
  `verified` means for a real provider, and it is decided **with D2**.

### D4 — where the two ordinals are drawn

- **추천안:** **three** draw points, from **one** counter, so launch and observation are
  totally ordered in a single sequence:
  1. `launch_stamp` — **once per launch lease**, immediately before `terminal create`
     (§3a.2 step 4d), outside every ticket window. It attests the act that carries
     `--model`, and it is drawn by the **harness**, not by the driver (D2 option B).
  2. `request_stamp` (leg 1, the ticket's **first** ordinal) — immediately before the
     driver writes this call's preflight input into the leased session. Under option B its
     meaning is "**this attempt exercised that lease here**", and the barrier additionally
     requires `0 < launch_stamp < request_stamp`.
  3. `observe_stamp` (leg 2, the ticket's **second** ordinal) — **immediately after** the
     preflight turn's `result` has been read **and all four D3 conjuncts have passed**, not
     merely after the `result` arrives.

  The earlier edition drew leg 1 "immediately before the launch/attach act". That is the
  text review F-001 made impossible: on this shape no driver call contains a launch, so
  leg 1 has to attest something that actually happens inside the call.
- **근거:** the barrier knows the counter's value before and after the driver call and
  requires exactly `issued+1` then `issued+2` with the counter ending at `issued+2`
  (`orca_runtime_harness.py:3950-3968`, `:4336-4346`). Drawing leg 2 before the checks pass
  would attest an observation the driver had not yet accepted. The two instants were measured
  to be genuinely separable in one process (E16; re-confirmed by P1's per-turn
  `init` → `assistant` → `result` sequence).
- **How the stamps bind to the execution-target identity:** the two per-call ordinals do
  not, directly — and that is deliberate. They carry **ordering**; the **identity** is
  carried by `selection_token` and by the six-part `observed_at_*` key, whose
  `observed_at_terminal` the barrier compares against the handle it was called with
  (`:4347-4366`). So a driver cannot satisfy ordering for one target and identity for
  another: the same ticket supplies both. `launch_stamp` is the one ordinal that **is**
  identity-bound, because the lease that carries it is bound to the handle's
  `terminal_digest` at `CREATED` (§6 D2).
- **보장:** request strictly precedes observation, arithmetically, as the harness already
  counts it; a fabricated, reused, missing or extra ordinal is detectable without any
  provider knowledge.
- **비보장:** does not decide who owns the held process afterwards (D8/B1-c); does not make
  leg 1 evidence of a resolution; and `launch_stamp < request_stamp` proves the launch
  preceded the observation, **not** that the launch was made for this attempt — that is the
  lease binding's job (D2), not the counter's.
- **변경 범위:** the driver module, plus the harness's lease mint for draw point 1
  (D2 option B). Without option B, draw point 1 does not exist.
- **대안·비용:** drawing both per-call ordinals after the turn — destroys the ordering proof.
  Drawing `launch_stamp` after `terminal create` — it would no longer attest the act, only
  record that it happened. Drawing leg 2 on `result` arrival rather than after the checks —
  weaker, and for no saving.
- **추가 관측:** none.
- **사용자 승인 필요 여부:** **no** — implementation decision.

### D5 — verification cost and validity window

- **추천안:** **re-verify every dispatch, and accept the cost.** Explicitly distinguish the
  two re-verification kinds, because they are different acts with different costs:

  | | **same-attempt re-call** (pre-pass → Gate B) | **new-dispatch re-verification** |
  |---|---|---|
  | how many processes | **one** — the second preflight runs on the **same held process and session** (실제 관측, P1 M3) | one per prepared session; a new gate round prepares a **fresh** pair under its own key |
  | how many turns | **one extra preflight turn** | one preflight turn per role per round |
  | why it cannot be skipped | the two attested keys **differ** (`task_id=""` vs the real `task_id`), so Gate B structurally cannot accept the pre-pass's evidence — it would be `model_selection_request_stale` (§3a.2) | the reuse gate already refuses evidence belonging to another dispatch (`reuse_eligible`, `orca_runtime_harness.py:2080`) |
  | what links them | leg (i) + the durable `(phase, role)` baseline: both must resolve to the **same** model, or `model_selection_ambiguous` / `PAIR_PREPARATION_MODEL_DRIFT` | the same two checks, across processes |

  **Expiry and process replacement while a prepared target waits:** there is **no time-based
  expiry**, and none should be added. Validity ends on **re-verification failure**, which is
  mandatory and unconditional on every preparation pass (`orca_adapter.py:595-600`), or on
  the **delivery write failing** — `EPIPE` on a **direct** stdin write (실제 관측, P2), and on
  the **hosted relay** the three-way verdict of §3a.13.1 instead, because the driver's own
  hop-1 write completes with no error while the child is dead (반증, 실제 관측 X-5). A second
  process under a recorded session uuid or handle is a **named driver-side refusal**, never a
  silent rebind (§3a.9).

  **The total preflight count for one normal Worker→Reviewer round — COUNTED off the §3a.2
  trace.** This is the number D5 is actually deciding, so it is derived rather than asserted,
  and it is derived from the **call sites**, not from any latency figure and not from "one more
  than before":

  | # | where, on the trace | driver calls | turns |
  |---|---|---|---|
  | 1 | Worker's graph turn, step 4f, `role = worker` | `verify_model_identity("", worker_handle, …)` | **1** preflight, on the Worker's session |
  | 2 | Worker's graph turn, step 4f, `role = reviewer` | `verify_model_identity("", reviewer_handle, …)` | **1** preflight, on the Reviewer's session |
  | 3 | Worker's graph turn, step 8 | `start_worker` → `_gate_b_model_identity(task, worker_handle, …)` | **1** preflight, on the Worker's session |
  | — | Worker's delivery | — | **1 real-task turn** |
  | 4 | Reviewer's graph turn, step 4f, `role = worker` | `verify_model_identity("", worker_handle, …)` | **1** preflight — **on the Worker's POST-TASK session.** This is the turn **TL** exists to make possible, and the one DR-01 showed a settlement-scoped release destroys |
  | 5 | Reviewer's graph turn, step 4f, `role = reviewer` | `verify_model_identity("", reviewer_handle, …)` | **1** preflight, on the Reviewer's session |
  | 6 | Reviewer's graph turn, step 8 | `start_worker` → `_gate_b_model_identity(task, reviewer_handle, …)` | **1** preflight, on the Reviewer's session |
  | — | Reviewer's delivery | — | **1 real-task turn** |
  | | **total per round** | | **6 preflight + 2 real-task** |

  **Why it is 6 and not 4.** Two independent multipliers, both of them 현행 코드 rather than
  proposals: `_prepare_pair`'s verification loop runs for **BOTH** roles **unconditionally on
  EVERY pass** (its own comment says so in capitals, and `delivery_role` scopes only
  `_reuse_verdict`), which gives **2 passes × 2 roles = 4**; and Gate B **cannot** accept the
  pre-pass's evidence, because the ticket token is
  `run:task:terminal:role:phase:attempt:ordinal` and the pre-pass's `task_id` is `""` while
  Gate B's is the real one — leg (h) refuses it as `model_selection_request_stale` — which adds
  **1 per delivery = 2**. Neither multiplier is removable without deleting a protection: the
  first is the pair-admission invariant, the second is what makes "do not reuse another
  dispatch's evidence" structural instead of a rule to remember.

  **What a validation repair adds — per attempt, per role, and bounded.** A repair is the
  engine re-dispatching **the same role, in the same phase, in the same round**
  (`PREPARE_REPAIR` → `PREPARE_INTENT`; 현행 코드), so it re-enters `OrcaAdapter.start` under
  the **same** `(phase, attempt)` key and is therefore a further full pass of step 4:

  | per repair attempt | turns |
  |---|---|
  | step 4f, **both** roles re-verified unconditionally | **+2** preflight |
  | step 8 Gate B on the repaired role's handle | **+1** preflight |
  | the repaired role's re-delivery | **+1** real-task |
  | **subtotal** | **+3 preflight + 1 real-task** |

  It applies to a **Worker** repair and to a **phase-Reviewer** repair identically, because the
  both-role verification loop does not know which role is being repaired. The engine bounds it:
  `MAX_REPAIR_ATTEMPTS = 2` per role, and **every** non-repair token resets the counter, so one
  gate round's **worst case** is `6 + 2` ordinary turns plus `2 × (3 + 1)` per role —
  **≤ 18 preflight + 6 real-task** if both roles exhaust their repair budget. That is a
  worst case, not an expectation, and the round does **not** close until the repairs finish
  (**§3a.12 TL**, rows 2–4).

  **Two shapes, because TL does not decide reuse.** If the **shipped reuse gate** permits the
  repaired delivery role to be delivered to again, the repair runs on the **held** session and
  the counts above are exact. If the gate **refuses** (or cannot be asked), `_prepare_role`
  supersedes: a **new** session with a **new** lease and a **new** bootstrap for that role, and
  `release(handle)` on the superseded one. The turn counts are the same — a failed bootstrap
  costs **zero** model turns — but the process count rises by one for that role. Which happens
  is the **reuse gate's** decision, not TL's (§3a.12.1).

  **A Reviewer FAIL is not a repair** and is not an increment: it closes the round (both roles
  `VALIDATED`, ROUTE emits `PREPARE_CORRECTION`), and the next gate iteration is a fresh
  `(phase, attempt+1)` key with fresh sessions — another **full** 6 + 2.

  **The latency figure is deliberately not part of this count.** "≈2 s per preflight" is a
  **past** measurement from the analysis run; this project has also measured ~1.0–1.2 s (P1),
  1199 ms (S-1b) and 3895 ms for a turn containing two tool round-trips (S-3). A company
  gateway is unmeasured. The decidable quantity is **the turn count above**; wall-clock is an
  order of magnitude and never a budget commitment.
- **근거:** E9/E17 S3–S6 — a session's model is not permanently bound and `--resume` without
  `--model` inherits the last selection silently, so a verification from an earlier dispatch
  describes nothing about this one.
- **보장:** no evidence is carried across dispatches; the reuse gate's
  `MODEL_IDENTITY_STALE` / `MODEL_IDENTITY_UNVERIFIED` refusals stay meaningful rather than
  becoming conservative decoration.
- **비보장 / cost, honestly:** **exactly 6 preflight turns + 2 real-task turns per HIGH-risk
  gate round**, counted above, plus **3 + 1** per validation repair; and under **TL** two
  hosted processes stay live through the whole round, one of them **after its own Dispatch has
  settled**. The hold is 실제 관측 only to **90 s** (P1) and **68.8 s** (S-3), so the hold
  itself is **미검증** at a realistic Worker-turn length. No wall-clock figure here is a
  guarantee.
- **변경 범위:** operational policy; no code change beyond the driver and §3a.11's P-1…P-8.
- **대안·비용:** verify once per session (cheaper; accepts silent drift, the exact defect the
  staling rules exist to prevent); verify once per run (cheaper still, strictly worse for the
  same reason); add a time-based validity window (invents a guarantee no measurement
  supports).
- **추가 관측:** the **hold-duration** question — does a held process survive a real Worker
  turn's worth of waiting? 90 s is measured; a p95-length hold is not (§3a.8). One turn plus
  one long wait, after a real p95 figure exists.
- **사용자 승인 필요 여부:** **YES — 승인 대기**, because it is a recurring cost per dispatch
  and the user owns that trade-off. It is not blocking for *design*; it is blocking for
  *operation*.

### D6 — mapping real failure modes onto the existing closed failure vocabulary

- **추천안:** map each failure explicitly and add **no** new member. The vocabulary is
  `orca_runtime_harness.py:884-902`.

  | Failure | Mapped to | Note |
  |---|---|---|
  | **unknown / unrecognized model** (`[claude-code:unrecognized_model]`, exit 1, empty `modelUsage`) | `model_selection_unverified` | a selection was requested and nothing was positively observed |
  | **authentication failure** (not logged in) | `model_selection_unsupported` | a **capability** failure, not a model one. The isolation set keeps auth working (`--safe-mode` help, read this phase), so this is a host-state failure |
  | **budget exhaustion** | `model_selection_unverified` | read `subtype` / `terminal_reason` **separately** from model evidence; `modelUsage` is populated on failed turns too, so its presence is not a pass |
  | **missing event** (no `init`, or no `assistant` for the turn) | `model_selection_unverified` | absence is never a pass |
  | **wrong / unexpected event** (an unknown `type`, e.g. `rate_limit_event`) | **not a failure at all** | ignore unknown types; select by `type` + turn membership, never by position (실제 관측, this phase) |
  | **an auxiliary model key in `modelUsage`** | **not a failure at all** | measured on two **successful** turns that resolved exactly the requested model (P1). Containment only; **`modelUsage` equality must never be the basis of `mismatch`** |
  | **timeout** awaiting the preflight `result` | `model_selection_unverified` | the barrier already normalizes any driver `Exception` to this member (`:4015-4028`) |
  | **resolved ≠ requested** | `mismatch` (→ `model_selection_mismatch`) | reported **by the driver**, which owns satisfaction |
  | **process died / was replaced between verification and delivery** | `model_selection_unverified` | detected by `EPIPE` on the delivery write (실제 관측, P2); authority revoked, history preserved |
  | **control-flow exceptions** — `KeyboardInterrupt`, `SystemExit`, `GeneratorExit` | **NOT mapped into the vocabulary.** They are **re-raised as themselves**, with the same type and traceback — but **only after** the session's evidence has been invalidated | this is the existing, deliberate split: invalidation is keyed on the **side effect** (selection may already have switched the session), normalization is keyed on the exception type (`orca_runtime_harness.py:4003-4012`, and the post-selection `BaseException` boundary at `:4030-4067`). A driver must **not** catch them and must **not** convert them into a refusal; it must only make sure it leaves no half-written state behind |
  | **a non-primitive attested field** (a driver returning an object) | `observation_method` → `model_selection_unsupported`; `resolved_model` → `model_selection_unverified`; every other field → `model_selection_unverified` | already enforced by the hoisted type gate and `ATTESTED_FIELD_TYPE_REFUSALS` (`:910-929`) — the driver inherits it, it does not re-implement it |
  | **an ALIAS request** (`sonnet`, or any value the fully-qualified screen refuses) | `unverifiable` → `model_selection_unsupported` | D3's `exact_equality.v1`: satisfaction is **undecidable**, which is a capability failure and not a wrong answer. Refused **before** any turn is written, so it costs nothing |
  | **a consistent WRONG canonical model** (both `init.model` and `assistant.message.model` report `claude-haiku-*` for a `claude-sonnet-5` request) | `mismatch` → `model_selection_mismatch` | the adverse case review F-004 named. Internal consistency is not satisfaction |
  | **an absent / revoked / mis-bound launch lease** | `model_selection_request_absent` (absent) or `model_selection_request_stale` (revoked, wrong handle, wrong ordinal order) or `model_selection_mismatch` (wrong `requested_model`) | D2 option B's leg (a2). Four new checks, **zero** new members |
  | **a `hello` that disagrees with the lease**, a **sequence gap**, a **partial/non-JSON `hello`**, or a **second connection** | `model_selection_unverified` | §3a.12. Driver-local names (`DRIVER_TARGET_REBOUND`, `DRIVER_TARGET_UNBOUND`, `DRIVER_BIND_UNCONFIRMED`) exist for the diagnostic; the reported member is the existing one |
  | **a bootstrap that never binds** — the **one** bootstrap deadline armed at listener readiness expires (including the no-client and pre-`hello`-drop cases), the `bound` echo is absent or disagrees, or the harness crashed between `terminal create` and the bind | `model_selection_unverified` → `PAIR_PREPARATION_MODEL_UNOBSERVED` | §3a.12's Timeouts rule. `DRIVER_BIND_UNCONFIRMED` / `DRIVER_TARGET_UNBOUND` are the diagnostics, and `bootstrap_timeout` is the shim's own `ctl` reason — a **diagnostic the driver may log, never a second verdict**. **Zero** model turns are spent, because the shim spawns its child only on a verified bind (실제 관측, X-1) |
  | **a `bind` replayed to a LATER incarnation of the same lease** (the F-006 case) | `model_selection_unverified` | refused by the **shim** as `stale_incarnation`, on the per-incarnation freshness value (§3a.12). **Not** by the seen-nonce set, which a fresh process's is empty — that was the unsupported claim. No new member |
  | **a replayed or rebinding `bind` frame inside one incarnation** (a nonce already seen **in this incarnation**, or a second `bind` with a different handle/digest/nonce) | `model_selection_unverified` | refused by the **shim** (`rebind_refused`, `bind_unauthenticated`) and, on the driver side, `DRIVER_TARGET_REBOUND`. No new member |
  | **a coordinator restart** facing a durable lease with no live channel | `model_selection_unverified` → `PAIR_PREPARATION_MODEL_UNOBSERVED` | §3a.12's named refusal. Not a repair path |
  | **a short write** on the hosted delivery — at **hop 1** (the driver's own frame), at **hop 2** (the shim's write to the child), or the **buffered-write trap** (a full hop-2 count with a non-null `hop2_error`) | **not a model failure** — it is a delivery failure (`not_delivered`), raised as the runtime error it is, with the lease revoked and the model snapshot restored | §3a.13.1. Three windows, not one (실제 관측, X-5). It belongs to neither closed vocabulary, which is why it is named here rather than mapped |
  | **a delivery whose outcome is UNKNOWN** — hop 1 complete, **no ack** | **not a model failure, and not a mapped failure at all.** It is `UNKNOWN`: re-raise, revoke, `release(handle)`, and **keep** the open Dispatch obligation. **Never** resent, **never** settled as success, **never** converted into confirmed non-delivery | §3a.13.1. Irreducible by measurement: two faults give an identical driver observation with opposite ground truth (X-5). Inventing a vocabulary member for it would be worse than naming it — a member implies a verdict, and there is none |
  | **a recognised hosted target that is not deliverable** — lease revoked/mis-bound, the channel lost after Gate B, **or the hosted capability HC absent/incomplete** | **not a model failure** — `HOSTED_TARGET_UNDELIVERABLE`, a **named** delivery refusal that creates nothing | §3a.13's recognition/deliverability split. The specific cause is carried from the existing driver-local names (`DRIVER_TARGET_UNBOUND` / `DRIVER_TARGET_REBOUND`), from `model_selection_request_stale`, or — for the capability case — from **`HOSTED_CAPABILITY_INCOMPLETE`**. **Zero** new members of either closed vocabulary, and it must never fall through to the legacy `worker-start` / `terminal send` path (실제 관측, X-4, for the lease and channel classes; **추론** for the capability class, which X-4 did not run) |
- **근거:** the closed vocabulary is closed on purpose, and each failure above already has a
  member whose stated meaning fits. Widening it would require editing its locks for no
  behavioural gain.
- **보장:** every refusal is an existing name; the reuse gate, pair admission and the drift
  legs keep working unchanged.
- **비보장:** a **gateway** may produce failure shapes none of these measurements covers
  (§7). The mapping is then incomplete, not wrong. And the two **delivery**-side failures in
  the table above (the short write, and a dispatch that was created before the write failed)
  are deliberately outside both closed vocabularies: they are runtime errors, and inventing a
  model-selection name for them would hide a delivery problem behind a model refusal.
- **변경 범위:** driver module + `docs/COMPATIBILITY.md`. **No failure-vocabulary change** —
  note that D1's three **request/observation** members are a different vocabulary and are
  D2's cost, not D6's.
- **대안·비용:** a new member per failure kind — more diagnostic precision, at the cost of
  widening a deliberately closed vocabulary and editing its locks.
- **추가 관측:** the company-environment failure shapes (§7); and U-6 (§3a.5) — whether a `-p`
  process can run the settlement command at all, which would otherwise surface as a
  **dispatch** failure rather than a model failure and belongs in neither vocabulary.
- **사용자 승인 필요 여부:** **no** — implementation decision, because it adds nothing and
  renames nothing.

### D7 — is Orca's own `worker-start --model` in scope?

- **추천안:** **not yet.** Keep it out of the first driver, and keep §4's attachment point
  general enough that it could later become a **second** driver behind the same interface.
- **근거, re-read this phase rather than inherited:** `orca orchestration worker-start --help`
  on **Orca 1.4.197** shows `--model <id>` and `--effort <level>`, and states in its own
  Notes: *"`--model` supports Claude, Codex, and Cursor opaque provider model ids;
  `--effort` requires `--model`. **Neither can combine with `--terminal`.**"* That
  incompatibility collides head-on with the prepared-session model this repository uses:
  every delivery in `start_worker` targets `--terminal <handle>`
  (`orca_runtime_harness.py:4707-4719`), and pair admission **requires** a prepared session
  to exist and be verified before the first delivery. A path that cannot pass `--terminal`
  cannot deliver to a prepared, verified, digest-proved session.
- **보장:** no design here depends on `--model`, `--effort`, or on the shape of any
  `launch.requested` / `launch.effective` receipt field.
- **비보장 — and the line this item exists to hold:** the flag's **existence** on Orca 1.4.197
  is **실제 관측** (its `--help` was read this phase). Its **behaviour** is **문서상 지원 and
  unexecuted**, and this phase did not and could not execute it — a dispatched worker may not
  run `worker-start` (`nested_worker_depth_exceeded`), so **only `--help` was read**. Reading
  a help text is not a behaviour observation, and this document does not convert one into the
  other.
- **변경 범위:** **none now.**
- **대안·비용:** make it the primary path — potentially much less code, on an unverified
  mechanism that is documented as incompatible with the prepared-session delivery every other
  protection in this repository is built on. Were it to become the new default path, the scope
  decision would have to cover: how pair admission admits two sessions it did not prepare, how
  the reuse gate identifies them, what replaces `terminal_digest` as the cross-process
  identity, and what `launch.effective` actually contains.
- **추가 관측 (the minimum, and who may take it):** one `worker-start --model <id> --agent
  <id>` execution **at Coordinator or user level**, recording the receipt's full JSON —
  specifically whether any `launch.requested` / `launch.effective` field exists and what it
  holds; plus one attempt combining `--model` with `--terminal` to confirm the documented
  refusal is a refusal.
- **사용자 승인 필요 여부:** **no** for keeping it out of scope (that is the recommendation
  and the status quo). **YES — 승인 대기** to make it the primary path.

### D8 — execution target and lifetime **(blocking; required before real-driver work)**

- **추천안 — ONE path:** **a dedicated non-interactive `claude -p --input-format stream-json`
  process per prepared session, hosted by the Orca terminal that preparation creates, and
  controlled by the driver through the `AF_UNIX` control channel of §3a.12, and delivered
  through the hosted branch of §3a.13.** The three candidates stay
  distinguished:

  | | **(A) attempt-dedicated non-interactive process** *(recommended, in the (C-2) coupling)* | **(B) one-shot** | **(C) long-lived interactive, reused across dispatches** |
  |---|---|---|---|
  | measured? | **yes** — one process, **three** turns, one **90 s** wait, same `session_id`, alive throughout, exits 0 on stdin close (실제 관측, P1) | yes — this is the shipped standalone driver (`standalone_drivers.py:1249-1313`) | **미검증** — no measurement in this project covers it |
  | pre-delivery verification | **possible** | **impossible** — the prompt is supplied at process creation | possible in principle, but the model is not bound to the session (E9/E17 S3) and `--resume` without `--model` inherits silently (E17 S4/S6) |
  | can satisfy the barrier | **yes, but only with D2 option B.** Review F-001: on this shape the one `--model` act falls outside every driver call's ticket window, so the request leg needs the launch lease. Without option B this candidate cannot satisfy the barrier honestly either | **no** | only after a new measurement campaign |
  | lifetime scope | one prepared session = one gate round for one role | one turn | across dispatches, which the reuse gate already refuses evidence for |

  **The four things approving D8 actually decides** — separated because they are four, and an
  approval of one is not an approval of the others:

  | # | What is being decided | Where it is specified | Status |
  |---|---|---|---|
  | **D8-a** | **the execution target**: coupling (C-2) — one `claude -p` process per prepared session, hosted by the Orca terminal whose own process is the repo-owned supervisor shim, controlled through the shim's `AF_UNIX` channel, delivered through the hosted branch above `worker-start`, admitted by **recognition on the durable hosted launch cell alone** with **HC** and the lease/channel as **deliverability** requirements whose failure is a **named refusal** (§3a.13) | §3a.6, §3a.11 HC, §3a.12, §3a.13 | **승인 대기.** The shape is supported by S-1a (direct hosting refuted) and S-1b/S-3 (a supervisor-hosted process received a task and settled its own Dispatch), but §3a.12's **own** channel, bootstrap and lease are **unexecuted** |
  | **D8-b** | **the round lifetime**: the target, channel and lease are held for the whole gate ROUND and released at round close, where "close" is the ONE predicate of **§3a.12.1 TL**: **(1a)** every role **REQUIRED by the round at the run's risk level** is `VALIDATED` — HIGH/MEDIUM the Worker **and** the phase Reviewer, counting a **prepared-but-not-yet-dispatched** Reviewer as required; LOW the Worker only — **and (1b)** ROUTE's token is neither `PREPARE_REPAIR` nor `PREPARE_PHASE_REVIEWER`. Never bare `settle_attempt`, and never "every role *dispatched* so far", which would close a HIGH-risk round after the accepted Worker turn. TL's four per-role states and its 15-row hold/dispose table are **derived** from that predicate and covering Worker repair, phase-Reviewer repair, repair success, repair exhaustion, SEMANTIC/LIFECYCLE block, Reviewer FAIL, PASS, BLOCKED, ESCALATED, cancel/abandon, PAUSE, a step-4 refusal and process exit | §3a.12.1 TL, §3a.8 | **승인 대기.** Required for the normal Worker→Reviewer flow to work at all (DR-01, measured X-3) **and** for a validation repair of either role to be able to run at all (현행 코드: the engine classifies a gate defect one node **after** settlement and re-dispatches the same role under the same key). Costs one preflight turn per round on a post-task session, `+3 preflight + 1 real` per repair attempt (bounded by `MAX_REPAIR_ATTEMPTS = 2` per role), and a two-process hold whose duration is **미검증** beyond 90 s / 68.8 s |
  | **D8-c** | **the delivery verdict**: three-way `delivered` / `not_delivered` / **UNKNOWN**, with UNKNOWN **irreducible**, no auto-resend, no success settlement, no second target, and the existing Dispatch obligation kept | §3a.13.1 | **승인 대기.** Measured (X-5). This is an acceptance of a **permanent operational residue**: some deliveries end in a state nobody can resolve automatically, and the design chooses to report that rather than guess |
  | **D8-d** | **termination responsibility**: nothing in this engine terminates a hosted CLI child. The shim does **not** kill its child when the channel goes away; `release(handle)` tears down the channel and the lease only; the **operator** closes terminals through the existing residual-session procedure; and Orca revokes the **dispatch capability** itself at settlement (실제 관측, S-3) | §3a.3, §3a.12, §3a.12.1 | **승인 대기.** The cost is explicit: after a refusal, a restart or a Coordinator exit, the CLI child stays alive until the operator closes the terminal. That is the existing contract for every Orca terminal this engine creates, not a new leak class — but under TL it now also applies to a **settled** role's process |

  The scope limit is stated in the driver's own docstring: **every measurement behind this
  recommendation is `claude -p`.** Orca operates long-lived terminal sessions, and
  P1/P2/E16/E17/S-1b/S-3 do **not** represent that. If the long-lived interactive terminal is the real target,
  independence, change isolation and per-dispatch re-verification must be **re-measured** in
  that scope before any design rests on it.
- **근거:** (A) is the only candidate in which a positive verification can precede delivery
  **and** for which the mechanism is measured; and in the (C-2) coupling **plus §3a.13's
  hosted delivery branch** it is the only shape whose evidence path and delivery path depend
  on **no undocumented Orca behaviour** (§3a.5: **U-3** is 미검증 and documented-adverse;
  U-2 is now 실제 관측, so it is no longer part of this argument). Note
  the correction review F-002 forced: (C-2) alone does **not** route around U-3, because
  `start_worker` calls `worker-start --terminal` unconditionally and rung 4 is reached only
  on `agent_unconfigured` (`orca_runtime_harness.py:4707-4768`). The branch is what routes
  around it, and the branch is part of this item's scope.
- **보장:** one process = one selection act (the argv boundary), and **exactly one process
  per prepared session** — the two verification calls of one attempt neither duplicate the
  process nor swap the session, which is the request's own constraint; one caller-chosen
  `session_id` echoed on every observation channel of every turn (실제 관측); verification
  strictly before delivery, with Gate B still the first statement of `start_worker`; a crash
  before verification leaves nothing delivered; the Orca Task, **Dispatch** (rung 4's own
  `dispatch` call), `worker_done` and settlement path are **unchanged**, so this is Orca
  integration and not a standalone run.
- **비보장:**
  - nothing about long-lived **interactive** sessions;
  - no claim that the real-task turn cannot drift from the preflight turn within one process
    (unmeasured in the adverse direction; three same-model turns were observed);
  - **no claim that a held process survives an arbitrary delay — 90 s is measured, a Worker
    turn's worth is not** (§3a.8). A dead held Reviewer process costs the **whole gate
    round**, fail-closed;
  - **U-3, U-4 and U-5 remain 미검증.** U-1's *supervisor* half and U-6 are now 실제 관측
    (S-1a/S-1b/S-3) — a supervisor-hosted `-p` process received a dispatched task and settled
    its **own** Dispatch — but that observation is **prototype wiring, not driver support**:
    it used a disposable launcher over `terminal send`, an **unsupervised** Dispatch created
    without `--inject`, and none of `build_orca_adapter`, `_prepare_pair`, Gate A/B or
    §3a.12's channel. **§3a.12's `AF_UNIX` channel, its three-frame bootstrap and the launch
    lease have never been executed**, and the five stage-1 probes (X-1…X-5) are toy models of
    this specification, not of the product;
  - the **product permission scope** for a dispatched process is a **separate** pending item
    from the experimental allow-list that sufficed for one probe (§3a.7's two-column table);
  - the channel bootstrap proves **agreement plus ordering**, not **provenance** against a
    same-uid impostor (§3a.12), and the lease token is readable in the shim's argv by a
    same-uid `ps`; a **coordinator restart costs every prepared hosted session**, by design
    — the restart rule is a named refusal, not a repair — and so does a **crash between
    `terminal create` and the bind**, which is refused `DRIVER_TARGET_UNBOUND` rather than
    retried;
  - the bootstrap deadlines (shim 30 000 ms, driver 15 000 ms) are **chosen constants, not
    measurements**, and the recommendation does **not** rest on their order — single-verdict
    reporting comes from **ownership**, not from comparing two durations with different
    origins (§3a.12 Timeouts). No latency figure in this document is a guarantee (D5);
  - the **delivery UNKNOWN** state is permanent and not recoverable by design (D8-c), and
    `shim_incarnation` refuses **only** replay to a later incarnation of one lease — it is not a
    general replay-prevention claim, and a same-incarnation same-uid interposer stays an
    unnarrowed limit (§3a.12).
- **변경 범위 — the preparation/delivery changes this path needs** (full detail in §3a.11,
  **eight** items): **P-1** the prepared session's `--command` is the lease's
  composed argv — which **replaces a locked contract value**, `AGENT_PROFILE_MODEL_SELECTION`
  (§6 D2 item 5); **P-2** the durable **launch lease** on the preparation entry (schema bump
  v3 → v4); **P-3** a handle → bind-complete channel binding that survives
  `select_and_verify`'s return, plus an **idempotent** `release(handle)` on every refusal
  path (**B1-c**); **P-4** the **hosted delivery branch above `worker-start`**, not a rung-4
  seam (§3a.13); **P-5** two preflight turns per delivered attempt on **one** process, with
  exactly **one** launch; **P-6** one capability derivation beside
  `model_selection_capabilities` — **the rule HC**, defined once in §3a.11 and referenced
  everywhere else, never restated with a different name list;
  **P-7** the three vocabulary members and `ModelEvidence`'s two new attested fields;
  **P-8** the **post-create bind** — one harness method plus one `_prepare_role` statement
  immediately after the `CREATED` write, which is the only non-circular way the running shim
  can learn which Orca terminal it is (review F-003). Plus the **control-channel
  specification** (§3a.12: supervisor shim, derived `AF_UNIX` endpoint, the
  `hello`/`bind`/`bound` bootstrap with spawn-after-bind, framing, single writer, restart
  refusal, exactly-once release) and a `--permission-mode` / tool allow-list decision (§3a.7), which
  is a **security** decision and is pending with this item.
  **Unchanged and must stay unchanged:** `_stale_model_evidence` and the authority/history
  split, the durable baseline and drift refusals, digest-proved adoption, the reuse gate's
  nine conditions, pair admission, the `sessions` use-row discipline, and the launch-record
  binding (which already binds the **driver class**, so a mid-run driver swap is refused
  `PAIR_PREPARATION_BINDING_MISMATCH`).
- **대안·비용:** **(B)** is cheapest and already exists but cannot satisfy the barrier at all
  — it is the right choice only if the answer to OS-14 is "do not verify", which the request
  rejects. **(C)** is the closest fit to how Orca actually runs agents and is the only option
  that avoids per-dispatch process churn, but it requires re-measuring every independence and
  drift property in the interactive scope **first**. Within (A), the couplings **(C-1)** and
  **(C-3)** are cheaper in code (no shim, no delivery seam) but each depends on 미검증,
  미검증 Orca behaviour — **U-3** for delivery and **U-5** for the evidence leg (§3a.6);
  taking either means settling **U-3** first. S-0 and S-1b made them cheaper than this
  document previously assumed, and §3a.6 records that as a real argument rather than
  dismissing it; it does not change the recommendation, because a `terminal send` route still
  has to pass U-3 to obtain a Dispatch at all.
- **추가 관측, in priority order — revised, because the two cheapest were taken.** U-6 and
  U-2 are now 실제 관측 and U-1's direct half is refuted, so what remains is:
  1. **the channel itself** — one `[live]` exercise of §3a.12's bootstrap: a real shim started
     from a real `terminal create --command` with pre-create inputs only, a `hello` carrying no
     handle, **no child yet**, then one `bind` composed from the create receipt, the `bound`
     echo, `child_started` with a distinct pid, and one turn arriving on both the terminal copy
     and the channel. This is the largest unexecuted thing in the design, and it is §9.3's
     `test_live_shim_spawns_and_tees`;
  2. **the hold-duration test** at a real p95 Worker-turn length — now needed for **two**
     processes under TL rather than one, and the measured ceilings are 90 s (P1) and 68.8 s
     (S-3);
  3. **U-3** — worth taking to retire a dependence the split has already removed; it is no
     longer on the critical path;
  4. **U-5** (`terminal read` fidelity, operator-facing only) and **U-4** (`--inject` /
     `--return-preamble`), neither of which the recommended shape uses.
- **사용자 승인 필요 여부:** **YES. 승인 대기. Required before real-driver work starts** —
  it is the scope decision that determines whether §3a.11's common-code changes happen at
  all.


### Permission scope for the dispatched process — **two separate pending items**

Carried in §6 because it is a user decision with the same seven fields as D1–D8, and because
D8's security half is the one thing an approval of the execution shape does **not** settle.
The side-by-side table is in **§3a.7** and is not restated here.

- **추천안 — two decisions, not one, and in this order:**
  1. **the experimental scope is an observation and is closed.** `--safe-mode
     --setting-sources '' --no-session-persistence --permission-prompts none --allowedTools
     "Bash(orca --version)" "Bash(orca orchestration send:*)"` was **sufficient** for one
     probe's two harmless commands, with no refusal, `permissionMode = "default"` and
     `apiKeySource = "none"` (실제 관측, S-3). **Recorded, and not proposed as a product
     policy.**
  2. **the product scope is unwritten and is what needs deciding.** A real Worker or Reviewer
     must read files, edit files and run this repository's tests, which the two-command
     allow-list cannot express. The recommendation for *how* to express it is unchanged:
     take `permission_mode` from the **agent profile** (as `standalone_drivers.py` already
     reads `profile.permission_mode`) and scope the tool allow-list in the driver's argv, so
     the policy is a reviewed configuration value rather than a constant inside a driver.
- **근거:** sufficiency for one harmless probe is not a policy, and the two scopes differ in
  kind, not in degree — one allows two named commands, the other allows arbitrary file
  mutation and test execution inside a worktree. Deciding them together is how an experiment's
  allow-list becomes a production default.
- **보장:** `bypassPermissions` and `--dangerously-skip-permissions` are **not used and not
  proposed**; no credential is copied; a non-interactive turn still cannot answer a prompt, so
  whatever is approved must be expressible **entirely in argv**, in advance, and is therefore
  reviewable as text.
- **비보장:** nothing about the company environment (§7); nothing about a **supervised**
  Dispatch (U-3) or an `--inject`ed capability-bearing preamble (U-4), neither of which the
  probe reached; and no measurement at all of whether a product-width scope changes the CLI's
  behaviour on this path.
- **변경 범위:** the agent profile's `permission_mode` reading plus the driver's argv
  composition (§9.1 **I-9**). **No** common module, **no** schema and **no** contract value.
- **대안·비용:** **a blanket `--permission-mode`** — cheapest, and it makes the allow-list
  decorative, which is the opposite of a reviewable policy. **`--restricted`** — removes the
  command-running tools and refuses `bypassPermissions`, so the process cannot settle its own
  Dispatch (§3a.7) and the shape does not work. **Ship the experimental scope as the product
  scope** — the process could settle its Dispatch and could do nothing else, i.e. a Worker that
  cannot work.
- **추가 관측:** one fixture turn under a candidate **product** scope, doing one real file edit
  and one real test invocation, to confirm it is expressible in argv and that nothing prompts.
  Cheap, and **not** spent here.
- **사용자 승인 필요 여부:** **YES — 승인 대기, for item 2.** Item 1 needs no approval because
  it is a record of what already happened, and it is recorded precisely so that it is not
  mistaken for item 2.

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
- It does **not** decide D1–D8. Items marked **승인 대기** are the user's, and neither a
  DESIGN PASS nor the publication of a design PR is recorded as a user approval of any of
  them.
- **§3a does not claim that the Orca-side link works as a whole.** Of the six boundaries,
  **U-1 (supervisor half), U-2 and U-6 are now 실제 관측**, U-1's *direct* half is **반증**,
  and **U-3, U-4 and U-5 remain 미검증** — U-3 documented-**adverse**. The recommended coupling
  plus §3a.13's split is chosen to be independent of U-3, which is not the same as settling it.
- **The feasibility run's positive result is PROTOTYPE WIRING, not driver support, and this
  document does not convert one into the other.** A task was delivered to the very process a
  model had been observed on and that exact Dispatch settled from that process's own report
  (S-3) — using a Coordinator-created terminal running a **disposable launcher**, an
  **unsupervised** Dispatch created **without `--inject`** and **without `worker-start`**, and
  **none** of `build_orca_adapter`, `_prepare_pair`, Gate A, Gate B or §3a.12's channel. No
  Gate A/B evidence was produced or fabricated, and nothing about `build_orca_adapter`
  supporting a real driver follows from it.
- **§3a.12's control-channel protocol and §3a.13's delivery branch are specifications, not
  observations.** **No socket has ever been bound, no shim has ever been written, no `bind`
  frame has ever been sent, and no launch lease has ever been minted** — by this run or by
  any earlier one. The feasibility run's two turns travelled `terminal send` → a launcher's
  PTY → the child's pipe, which is a **different route**; its five stage-1 probes (X-1…X-5)
  are **toy models of this specification** and are evidence about the specification's internal
  consistency, never about a working channel or about product behaviour. The platform question
  §3a.12 names — whether this macOS build exposes peer credentials to Python — is **미검증**.
- **It does not claim general replay prevention.** `shim_incarnation` refuses a `bind` captured
  from one incarnation of a lease and replayed to a **later** incarnation of that same lease,
  measured, and nothing more: a same-incarnation same-uid interposer stays an unnarrowed limit,
  and the alternative candidate — proving the harness can never start a second shim per lease —
  is recorded as **미검증**, not as a resolved alternative.
- **It does not claim that a delivery can always be resolved.** The three-way verdict's
  **UNKNOWN** state is irreducible by measurement (X-5) and permanent by design: no resend, no
  success settlement, no second target. Approving D8-c is accepting that residue.
- **It does not claim the round-scoped hold is affordable.** **TL** is required for the normal
  Worker→Reviewer flow to work at all **and** for a validation repair of either role to be able
  to run at all, and its hold cost is **미검증** at a realistic Worker-turn length — now for
  **two** processes, one of them already settled, across up to two repair turns per role.
- **TL's round-close condition is matched to the engine by READING the engine, and nothing
  more.** The graph order, the fact that a gate defect is classified in `VALIDATE_SETTLEMENT`
  one node **after** `settle_attempt`, the `PREPARE_REPAIR` → `PREPARE_INTENT` same-role /
  same-phase / same-round re-entry, and `MAX_REPAIR_ATTEMPTS = 2` are all **실제 관측 (code)**
  in this tree. What is **not** claimed is that the hook has ever been built or run: no
  `release(handle)`, no round-close observer and no lease exist, so TL's agreement with the
  engine is a **문서상** agreement that §9.3's Group-3 rows are what would actually hold.
- **It does not claim the capability-loss refusal is measured.** Recognising a hosted target on
  its **durable launch cell alone**, and refusing an incomplete **HC** as a hosted
  deliverability failure, is **추론** from DR-02's own rule. X-4 never ran that combination —
  every hosted class it ran carried the capability conjunct **true**, and its one
  capability-false class had no launch lease, so the probe's own `split_v2` candidate carries
  the same hole and never showed it. What **is** measured (X-4) is the channel-loss and
  revoked-lease classes.
- **It does not propose a product permission policy.** The scope that sufficed for one harmless
  probe is recorded as an observation; the product scope is **승인 대기** and unwritten (§3a.7,
  §6 Permission scope).
- It does **not** record approval for the two contract changes the recommendation needs: the
  replaced `AGENT_PROFILE_MODEL_SELECTION` value and the widened request leg (§6 D2). Both are
  **승인 대기**, and D3's `exact_equality.v1` is pending with them.
- It does **not** claim that a held process survives an arbitrary wait. **90 s was measured**
  (P1) and **68.8 s** between two delivered turns on a hosted process (S-3); a Worker turn's
  worth was not, and the later measurement is the **shorter** of the two.
- It does **not** claim the preflight observation says anything about the real-task turn
  beyond this: the turn immediately before it, in the same process and session, reported
  that model.
- It does **not** claim that `orca orchestration worker-start --model` works. Only its
  `--help` was read (D7), on Orca 1.4.197.
- **§9 is a plan, not work done.** No unit in it is implemented, no test in it exists, and
  it is to be executed only after D2/D3/D5/D8 are approved.
- It does **not** re-run, revise or reinterpret any earlier run's experiments. E16/E17 (the
  analysis run), P1/P2 (the design run) and X-1…X-5 / S-0…S-4 (the minimal-feasibility run) are
  **cited** with their own scopes and labels; no earlier `ANALYSIS.md`, review or audit was
  modified, and two presentation notes those reviews raised are corrected **here** rather than
  retroactively: the DR-03 hop-1 byte count is **150/150** (hop 2 is 73/73) in §3a.13.1, and the
  raw-stream home-path hygiene item was closed by that run's Coordinator and is recorded as
  closed in `docs/OS-14-feasibility-evidence-summary.md`.


## 9. After approval: the minimum implementation units and the verification plan

**Nothing in this section is executed by this design run.** It exists so that, once D2, D3,
D5 and D8 are decided, an implementer starts without new core decisions. Every unit names
its file, and every test names what it asserts. The order is the order they must be done in:
a unit that could pass while an earlier one is wrong is placed after it.

### 9.1 Implementation units

The order is the order they must be done in: a unit that could pass while an earlier one is
wrong is placed after it. **Renumbered in this correction** — the launch lease and the
capability derivation are prerequisites of the driver and of the delivery branch, so they
move to the front, and the vocabulary members stay deliberately last.

| # | Unit | File(s) | Content | Gated on |
|---|---|---|---|---|
| **I-1** | the **launch lease** | `scripts/orca_runtime_harness.py` (a `ModelLaunchLease` dataclass beside `ModelSelectionTicket`, `:951-987`; a per-harness registry; `mint_launch_lease()`; `release(handle)`), and the new barrier leg **(a2)** after `:4281` | mint before `terminal create`, draw `launch_stamp` from the existing `_model_selection_seq` counter, bind `terminal_digest` at `CREATED`. **One lease per `terminal create`, never re-exercised by a second create** (§3a.12's mint rule), so the first create, a `CREATE_REFUSED` retry and a reuse supersession each mint a **new** token and revoke the superseded one in the **same** `CREATE_INTENDED` write. Revoke on every refusal path, on supersession, and at **round close** — **§3a.12 TL**, **not** at `settle_attempt`: the engine classifies a gate defect one node later and may re-dispatch the same role under the same key, so the lease must survive every validation-repair turn. Leg (a2)'s five checks, all of them equality or integer order over values this harness issued, mapping onto **existing** failure members (§6 D2 item 3) | **D2**, and **D8-b** for the round-scoped release |
| **I-2** | the **hosted capability derivation** — **the rule HC** | `scripts/agent_profile.py`, beside `model_selection_capabilities` (`:1339-1363`) | **exactly the rule HC as §3a.11 defines it, and no other spelling of it anywhere**: one function, same shape as the existing one, over **two** names — the capability exists **iff** `deliver_hosted` **and** `bind_target` are **both** callable on the driver object; one name is not hosted-capable, either way round. `None` ⇒ empty set, so the real runtime stays fail-closed by default. **Both** readers call this one function over the same driver object: `bind_prepared_target` (I-7, the **preparation gate** — no HC ⇒ no hosted launch cell is minted at all) and the **deliverability** test (I-8). **HC is never a recognition input** (F-002): on a target that already carries a hosted launch cell, an absent or incomplete HC is `HOSTED_TARGET_UNDELIVERABLE` / `HOSTED_CAPABILITY_INCOMPLETE`, never a demotion to the legacy path | **D8-a** |
| **I-3** | the **launch-command seam**, and the **contract-lock replacement it forces** | `orca_runtime_harness.py:2927` (`create_fake_terminal`), `orca_adapter.py:841-844` (`_prepare_role`); **and** `scripts/validate_skills.py:938-940` + `orca-worker-reviewer-orchestration/SKILL.md:2009` + `scripts/test_os49_contract_locks.py:99-111` | let the prepared session's `--command` be the lease's composed argv, default unchanged. **Preserve**: the model stays a separate ledger field, and the reuse gate's condition-2 identity keeps comparing `resolved_agent_command(role, phase)` — the **profile** command — while the composed argv is recorded as its own `launch_argv_digest` (`:2978-2986`). **Replace** `AGENT_PROFILE_MODEL_SELECTION`'s value in the block, the validator and the lock test in ONE commit; the block stays exactly 25 lines | **D2** (§3a.11 P-1) |
| **I-4** | the **lease cells** on the durable record | `scripts/deterministic_workflow/pause_store.py` (`PAIR_PREPARATION_SCHEMA_VERSION`, `:851`), written at `CREATE_INTENDED` in `orca_adapter.py:830-838` | `launch_token`, `launch_stamp`, `session_uuid`, `endpoint_digest`, `launch_argv_digest`, `launch_revoked_at`, plus `terminal_digest` at `CREATED`; a **schema bump** (v3 → v4) with the same fail-closed reading the v2 → v3 bump has (older document ⇒ `PAIR_PREPARATION_RECORD_CORRUPT`, never "nothing was prepared") | **D2**, **D8** (P-2) |
| **I-5** | the **control channel** and its **supervisor shim** | **new** `scripts/deterministic_workflow/claude_target_shim.py` (+ mirror) and the driver-side client in I-6 | mints its own **`shim_incarnation`** at startup; binds the `AF_UNIX` endpoint the harness derived, `listen(1)`, refuses a pre-existing path, **arms the ONE bootstrap deadline at listener readiness** (not at the `hello` write — F-005), writes its `hello` (pre-create fields only, plus the incarnation value), and runs the three-state machine `unbound → bound → relaying`: it **spawns** the `claude` argv as a child (**not** `exec`) only on a `bind` that passes the token **and** the incarnation compare, echoes `bound` + `child_started`, then relays NDJSON frames with per-direction `seq`. **Gives the child a pipe stdin and converts CR→LF** (§3a.12 H-1/H-2). Reports hop-2 results in a `ctl` ack carrying **both** `child_write_bytes` and `hop2_error`, so §3a.13.1's verdict is computable. Asserts the endpoint path against the platform's **own** `sun_path` size (104 bytes here, not 108). Refusal reasons `bind_unauthenticated` / `stale_incarnation` / `bootstrap_timeout` / `rebind_refused` / `unbound_channel` / `channel_already_bound`, each unlinking the endpoint, and a non-zero exit whenever it never bound. Permissions `0700` / `0600`. Full specification: **§3a.12**. Provider-specific; same C6 rule as I-6 | **D8-a** (coupling (C-2)) |
| **I-6** | the **driver module** | **new** `scripts/deterministic_workflow/claude_model_driver.py` (+ its mirror under `orca-worker-reviewer-orchestration/tools/deterministic_workflow/`) | `class ClaudeModelDriver` with `REQUEST_METHOD` / `OBSERVATION_METHOD` / `SATISFACTION_RULE = "exact_equality.v1"` class attributes, `select_and_verify(ticket) -> ModelEvidence`, `bind_target(handle, lease)` and `deliver_hosted(handle, prompt)`; argv composition (`-p --input-format stream-json --output-format stream-json --verbose --replay-user-messages --model <requested> --session-id <uuid> --safe-mode --setting-sources ''`); the `hello` check against the lease, the single `bind` it composes from the create receipt's handle, and the `bound`/`child_started` echo check (§3a.12); a turn-scoped stream reader keyed on the in-band markers, with **turn membership** as D3 defines it — a turn's `request_id`s are a **set**, every `assistant` event of the turn must agree on the model, events after a `result` belong to **no** turn, and **no one-`request_id`-per-turn assumption** anywhere (S-4); the four D3 conjuncts; the **fully-qualified pre-turn screen** and the **exact-equality** satisfaction test; `modelUsage` **containment**; unknown event types and unknown `system` subtypes ignored (`thinking_tokens`, `commands_changed`, `rate_limit_event` at any position are all measured); `seq`-gap detection; the **three-way delivery verdict** of §3a.13.1 in `deliver_hosted`, including the `hop2_error`-with-full-count case; the D6 mapping. **All provider syntax and stream grammar live only here** (C6) | **D2**, **D3** |
| **I-7** | the **post-create bind, the target binding + round-scoped release** | `orca_runtime_harness.py` (`bind_prepared_target`, beside `verify_model_identity`, gated on **HC**) + **one** call from `orca_adapter.py:862-864`, right after the `CREATED` write; driver-local registry; `release(handle)` from every `_refuse_preparation` path, every bootstrap refusal, the dead-target detection, §3a.13's undeliverable refusal, **and at round close (§3a.12 TL)** | the bind nonce is minted **once per lease** and held in memory only, and the `bind` **echoes the `shim_incarnation` from this connection's `hello`**; handle → live, **bind-complete** channel, surviving `select_and_verify`'s return **and the whole gate round** (**B1-c**, **TL**). Ordering invariants **O1–O6** of §3a.12 are what this unit is tested against. A second process under a recorded handle/uuid/lease, or a second `bind` with a different identity, is `DRIVER_TARGET_REBOUND`; an unmatched `bound` echo is `DRIVER_BIND_UNCONFIRMED`; a durable lease with no live channel — after a restart **or** after a crash between `terminal create` and the bind — is `DRIVER_TARGET_UNBOUND`. All three report the existing member and **never** rebind. `release` is **idempotent**, and tolerates an endpoint the shim already unlinked. **The round-close hook is part of this unit**, because nothing else owns the round's end. It must implement §3a.12.1's predicate **exactly**: **(1a)** every role **REQUIRED by the round** is `VALIDATED` — so it needs the run's `risk` to know whether the phase Reviewer is required, and it must count a Reviewer that is **prepared but not yet dispatched** as required and `ACTIVE` — **and (1b)** ROUTE's token is neither `PREPARE_REPAIR` nor `PREPARE_PHASE_REVIEWER`. It must track the four per-role states (`ACTIVE` / `SETTLED_PENDING_VALIDATION` / `REPAIR_PENDING` / `VALIDATED`). Three hooks that must all fail their tests: one firing on `settle_attempt` releases the targets a validation repair is about to need; one firing on `VALIDATED` alone releases them while a repair is pending; and one quantifying (1a) over **dispatched** rather than **required** roles releases both handles after the accepted Worker turn, before the Reviewer runs. It owns no routing: `VALIDATION_REPAIR` is the engine's decision (`graph_spec.GRAPH_OWNED_DECISIONS`), and this hook only observes the token | **D8-a**, **D8-b** (P-3, P-8) |
| **I-8** | the **hosted delivery branch**, with the **recognition / deliverability split** | `orca_runtime_harness.py:4690-4790` (`start_worker`), after the Gate B call at `:4683-4689` | the **two-stage** admission — recognition (**a hosted launch cell matching this handle's digest, and nothing else**) then deliverability (**HC** ∧ unrevoked lease ∧ Gate B accepted ∧ a live bind-complete target) — producing **three** verdicts: **not recognised, i.e. no hosted cell ⇒ today's code, byte-identical**; recognised ∧ deliverable ⇒ the hosted path (`wait_for_hosted_idle` in place of the `tui-idle` wait, **no** `worker-start` call, rung 4's own `dispatch` and prompt reused verbatim, the driver's write in place of `terminal send`); **recognised ∧ not deliverable ⇒ `HOSTED_TARGET_UNDELIVERABLE`, creating nothing**. `_attach_terminal(..., "low_level_tracked")` runs **only** on the `delivered` verdict, and still last — **not** on `not_delivered` and **not** on `UNKNOWN`. Full specification: **§3a.13** and **§3a.13.1** | **D8-a**, **D8-c** (P-4) |
| **I-9** | **permission scope** for the dispatched process | the agent profile (`permission_mode`, as the standalone driver already reads) + the driver's argv | whatever the **product** permission decision approves (§6 Permission scope, §3a.7): expressible **entirely in argv**, in advance, because a non-interactive turn cannot answer a prompt; wide enough for a driver that must read, edit and test code; **never** `bypassPermissions` or `--dangerously-skip-permissions`. The experimental two-command allow-list is **not** this unit's content — it is a recorded observation | **the product permission decision** (승인 대기, separate from D8-a) |
| **I-10** | **vocabulary members** and `ModelEvidence`'s two new attested fields | `scripts/orca_runtime_harness.py:844`, `:872`, `:1009-1019`, the type gate at `:4219-4258` + mirror | add `cli_launch_model_flag`, `cli_launch_lease_revalidation` and `stream_json_preflight_assistant`; add `launch_token: str = ""` and `launch_stamp: int = 0`. **Last**, deliberately — adding them earlier makes an unauthorised path look eligible (D1's 비보장) | **D1**, **after** D2 |
| **I-11** | **documentation** | `docs/COMPATIBILITY.md` (OS-14 TRACK B section), this document, `docs/OS-14-feasibility-evidence-summary.md` | record the new members, the schema bump, the lease and its **mint rule**, the control-channel protocol version `os14.hosted.v1`, the satisfaction rule `exact_equality.v1`, the hosted delivery branch and its **three-way verdict**, the **round-scoped target lifetime (TL)**, the replaced contract value, and what stays fail-closed. State the **CLI-reported** scope of every model verdict explicitly, so no reader takes it for an API-served one | all of the above |

### 9.2 Regression tests — the existing protections that must still hold

These are the tests that must be **run and still pass unchanged**. Where a new case is added
to an existing module it is marked **(+)**; everything else must pass with no edit, and an
edit to one of them is a review signal, not a chore.

| Module | What it protects | Expected effect of I-1…I-11 |
|---|---|---|
| `scripts/test_os49_vocabulary_locks.py` | the request/observation sets are closed and an addition is a **visible, reviewed** edit | **(+)** the **three** new members are added to the lock's expected tuples in the same commit as I-10, and nowhere else |
| `scripts/test_os49_contract_locks.py` | the seven `AGENT_PROFILE_*` contract values, the **exactly-25-line** anchor block, and `AGENT_PROFILE_MODEL_GATES` (3 gates) / `AGENT_PROFILE_MODEL_LIFECYCLE` (5 ordered steps) as **exact ordered equalities** | **(+), and this is the correction the earlier edition got wrong.** The gates and the lifecycle are **unchanged** — this design adds no gate and no lifecycle step — but `AGENT_PROFILE_MODEL_SELECTION`'s value is **replaced** by I-3, so `test_the_block_and_the_validator_agree_on_keys_and_values` and `test_the_block_holds_exactly_twenty_five_keys` must both still pass against the **new** value, in the same commit, and the block must stay 25 lines |
| `scripts/test_os49_delivery_barrier.py` | Gate B legs (a)–(j): request present, closed sets, token identity, the two ordinals exactly twice in order, `state == verified`, `resolved_model` well-formed, `requested_model` echo, the six-part freshness key, leg (i) consistency, pair admission | **unchanged.** The new driver must satisfy these as the fake does |
| `scripts/test_os49_driver_seam.py` | the seam contract: a driver that raises, a driver returning non-primitives, `select_and_verify` callability as the **only** capability test | **unchanged** for `select_and_verify`, and it is the module I-6 is written against first; **(+)** three cases on **HC**: that `deliver_hosted` **and** `bind_target` callability are derived by **one** function; that a driver missing **either** name is not hosted-capable (both directions tested separately), so `bind_prepared_target` returns `not_hosted` and **no hosted launch cell is minted** for the sessions it prepares — which is why *those* sessions legitimately take today's delivery path, and is **not** a licence to demote a session that already carries a cell (F-002; §9.3's `test_one_name_driver_is_refused_not_demoted` is the row for that); and that the **same** function is the one `bind_prepared_target` and the deliverability test both call, so the two reads cannot diverge (the DR-02 C-1/C-3 defect) |
| `scripts/test_os49_model_reuse.py` | the reuse gate's 9 conditions incl. `compatible_model_identity`; `MODEL_IDENTITY_STALE` / `_UNVERIFIED` | **unchanged** — a real driver changes no reuse condition |
| `scripts/test_os49_effective_identity.py` | same-command pair classification: `independent` / `refused` / `pending_model_verification` | **unchanged** |
| `scripts/test_os49_model_provenance.py` | `agent_identity_bound`, `ORCHESTRATOR_LOG_COLUMNS` byte-unchanged, the audit record's `1.1` MINOR bump | **unchanged** |
| `scripts/test_os49_security_boundary.py` | no secret reaches an argv or a log | **(+)** three cases: the composed launch argv is asserted free of credentials; the `hello` and `child_started` frames carry `argv_digest` and never the argv itself, and the `bind` frame carries **exactly** `protocol`, `launch_token`, `terminal`, `terminal_digest`, `shim_incarnation`, `bind_nonce` and `bound_at` — the handle's only transmitted derivative is a pure `sha256`, so no model string and no credential crosses the channel (review N-001: the earlier wording said "only handle, digest and nonce", which the declared frame never matched); and no **absolute** endpoint path reaches any artifact or log — only its digest. The lease token is a per-endpoint capability that **does** appear in the shim's argv (§3a.12 states that limit); the assertion is that it reaches **no artifact and no log** |
| `scripts/test_os49_launcher_parity.py` | both doors (Coordinator and `launcher`) apply the same Gate A | **unchanged** |
| `scripts/test_os49_profile_schema.py` | v1/v2 parsing, `WORKER_REVIEWER_MUST_DIFFER` | **unchanged** |
| `scripts/test_os49_bugfix_regressions.py` | the real `build_orca_adapter` driven end-to-end with a substituted process boundary | **(+)** the same scenario with the real driver class **and** a substituted process boundary — the shape that proves the seam without launching a CLI |
| `scripts/test_os14_pair_preparation.py` | preparation of both roles, dual verification, pair admission before the first delivery, durable provenance, re-entry/restart, the `PAIR_PREPARATION_*` refusals | **unchanged** for the reference driver; **(+)** the same assertions against a stub real driver with a stub lease and a stub channel, so I-3/I-7/I-8 cannot weaken them. **The both-role unconditional verification loop is the protection TL depends on**, so this module's existing assertion that the counterpart is re-verified on **every** pass must pass **unchanged** — a change to it would be a regression, not an accommodation |
| `scripts/test_os14_bugfix_regressions.py` | the PR #38 fail-closed refusals: `PAIR_SESSION_USE_RECORD_LOST`, `PAIR_PREPARATION_SESSION_USE_UNKNOWN`, `PAIR_PREPARATION_MODEL_DRIFT`, `PAIR_PREPARATION_MODEL_HISTORY_CONFLICT`, `PAIR_PREPARATION_MODEL_UNOBSERVED`, `_assert_preparation_not_lost`, the exact three-section `.pair_preparation.json` set | **must all still fail closed after I-4's schema bump.** This is the single highest-risk regression surface in the plan, because I-4 adds cells to the document those refusals read |
| `scripts/validate_skills.py` | the repo ↔ installed-Skill byte mirror **and** the `AGENT_PROFILE_CONTRACT` anchor block | **(+)** the new driver module and shim must be mirrored, or this fails; and I-3's replaced contract value must be edited in the validator and in `SKILL.md:2009` in the same commit, or the block/validator comparison fails |

### 9.3 New tests

Named, with what each asserts. **These are test SPECIFICATIONS, not results: none of them
exists, none has been run, and nothing below is evidence.** No row launches a real CLI except
the four marked **[live]**, which must be opt-in (env-gated) and are not part of the default
suite.

The four groups after the first exist because of review findings F-001…F-004 respectively, and
each group's first row is the adverse case the earlier plan did not catch. A second layer of
rows was added or **corrected** by the minimal-feasibility run, and those are the ones a
reviewer should read first, because several of them replace rows that asserted a behaviour the
specification did not produce:

| finding | rows added or corrected |
|---|---|
| **F-005** (no operative deadline in the create→bind window) | Group 3: `test_bootstrap_deadline_bounds_every_unbound_path` (four client behaviours, **replacing** `test_bind_timeout_exits_shim_and_refuses_delivery`), `test_bootstrap_deadline_is_not_restarted_by_any_peer_event`, `test_single_verdict_does_not_depend_on_deadline_ORDER` |
| **F-006** (a fresh shim of one lease accepts a captured `bind`) | Group 3: `test_stale_or_replayed_bind_is_refused` **case (2) corrected** — it previously asserted a refusal the documented rule did not produce — plus `test_incarnation_value_is_never_durable_and_never_an_identity`, `test_derived_endpoint_path_is_a_concurrency_interlock_not_a_freshness_one`, `test_every_create_mints_a_new_lease` |
| **DR-01** (the Reviewer round is refused at the settled role's pre-pass) | Group 3: `test_target_is_held_until_the_ROUND_closes_not_until_settlement`, `test_holding_the_target_restores_no_authority_and_skips_no_verification`, `test_round_close_disposes_on_every_terminating_path`, `test_coordinator_exit_leaves_a_refusal_not_a_rebind`, `test_tl_does_not_touch_the_dispatch_capability`; Group 5: `test_live_hosted_round_holds_both_targets` |
| **F-001** (TL released the pair before a validation repair could run) | Group 3: `test_settlement_alone_does_not_close_the_round`, `test_validation_repair_of_either_role_holds_both_targets`, `test_repair_hold_does_not_bypass_the_reuse_gate`, `test_repair_budget_bounds_the_hold`, and the **rewritten** `test_round_close_disposes_on_every_terminating_path` whose held and released cases no longer overlap |
| **DR-02** (a hosted failure falls through to the legacy path, and the capability rule is stated two ways) | Group 2: `test_recognition_survives_lease_revocation`, `test_recognised_but_undeliverable_is_a_named_refusal`, `test_channel_lost_right_after_gate_b_never_takes_the_legacy_path`, `test_not_recognised_targets_are_byte_identical_to_today` |
| **F-002** (an incomplete capability demoted a recognised hosted target to the legacy path) | Group 2: `test_one_name_driver_is_refused_not_demoted` — which **replaces** `test_recognition_requires_both_capability_names` and asserts the **opposite** verdict for the same two drivers — plus `test_hc_is_one_derivation_read_by_both_call_sites`, `test_no_hosted_cell_is_minted_without_hc` and `test_legacy_path_requires_no_hosted_cell` |
| **DR-03** (one byte count for a two-hop relay; UNKNOWN unnamed) | Group 2: `test_hop1_short_write_is_not_delivered`, `test_hop2_short_write_is_not_delivered`, `test_full_hop2_count_with_hop2_error_is_not_delivered`, `test_no_ack_is_UNKNOWN_and_stays_UNKNOWN`, `test_hop1_byte_count_alone_never_declares_delivery`, `test_dead_child_produces_no_hop1_error`, `test_attach_terminal_only_on_delivered` |
| **S-4** (turn ↔ `request_id` is not one-to-one) | Group 0: `test_one_request_id_across_several_assistant_events_is_one_turn`, `test_several_request_ids_inside_one_input_turn_is_one_turn`, `test_model_must_agree_on_every_assistant_event_of_the_turn`, `test_request_id_is_read_from_the_event_not_from_message`, `test_events_after_the_result_belong_to_no_turn`, `test_unknown_system_subtypes_are_ignored` |

**Group 0 — the driver's observation legs** (`scripts/test_os14_claude_driver.py`, new)

| Test | Asserts |
|---|---|
| `test_driver_requests_before_observing` | with a **scripted stream** (no process), the driver draws `request_stamp` before writing the preflight input and `observe_stamp` only after all four D3 conjuncts pass; the barrier accepts |
| `test_driver_refuses_init_only` | a stream with `system/init` but **no** `assistant` event for the turn ⇒ `model_selection_unverified`; `init.model` alone is never a pass |
| `test_driver_refuses_session_id_mismatch` | an `assistant` event whose `session_id` ≠ the supplied uuid ⇒ unverified, even when `message.model` is correct |
| `test_driver_requires_request_id` | an `assistant` event with an absent/empty `request_id` ⇒ unverified |
| `test_driver_attributes_turns_by_result_order_not_num_turns` | a two-turn stream where **both** turns carry `num_turns == 1` is attributed correctly, and the **second** turn's `request_id` **set** is required to be disjoint from the first's. (Locks P1's measurement.) |
| `test_one_request_id_across_several_assistant_events_is_one_turn` | a turn whose **two** `assistant` events (a `thinking`-only block, then a `text` block) carry the **same** `request_id` and the same `message.id` ⇒ **verified**, and conjunct 2 is asserted against **both** events, not one. Locks S-4 turn 1, and refuses an implementation that reads "the" assistant event |
| `test_several_request_ids_inside_one_input_turn_is_one_turn` | a turn with **two** `request_id`s across **five** `assistant` events, split by two tool round-trips, ⇒ **one** turn, **verified**, with the turn's `request_id` **set** of size 2. **A driver that requires exactly one `request_id` per turn must FAIL this test.** Locks S-4 turn 2, which is the assumption the earlier plan would have encoded |
| `test_model_must_agree_on_every_assistant_event_of_the_turn` | a turn whose first `assistant` event reports the requested model and whose second reports a different one ⇒ **refusal**, never a tie-break or a "last one wins" |
| `test_request_id_is_read_from_the_event_not_from_message` | a stream whose `assistant` events carry `request_id` at the event's top level and nothing on `message` ⇒ accepted; a driver reading `message.request_id` sees `None` and refuses, which this test forbids |
| `test_events_after_the_result_belong_to_no_turn` | `system/commands_changed` events arriving **after** a turn's `result` are not folded into that turn and not into the next one; the next turn opens only on the echo of the next line the driver wrote. Locks S-4 |
| `test_unknown_system_subtypes_are_ignored` | `system/thinking_tokens` and `system/commands_changed` interleaved anywhere ⇒ still verified; only `subtype == "init"` is read as the turn's `init`. Locks S-4 |
| `test_model_usage_auxiliary_key_is_not_mismatch` | a successful turn whose `modelUsage` holds `['claude-haiku-4-5-20251001', 'claude-sonnet-5']` for a `claude-sonnet-5` request ⇒ **verified**, not `mismatch`. (Locks P1's turn-2/3 measurement, which is the one a naive implementation gets wrong.) |
| `test_unknown_event_type_is_ignored` | a `rate_limit_event` inserted **before** and **after** the `assistant` event both still verify. (Locks the varying position measured this phase.) |
| `test_driver_maps_each_failure_to_existing_member` | one case per D6 row; the asserted value is a member of `MODEL_SELECTION_FAILURE_REASONS` and the tuple itself is **unchanged** |
| `test_control_flow_exception_stales_then_propagates` | a driver raising `KeyboardInterrupt` mid-selection: the session's authority is revoked **and** the exception propagates as itself, same type — never converted into a refusal |

**Group 1 — the launch lease: no call may pass by stamping an old launch** (F-001;
`scripts/test_os14_launch_lease.py`, new)

| Test | Asserts |
|---|---|
| `test_gate_b_attests_revalidation_not_a_new_request` | the pre-pass **and** Gate B of one attempt each attest `cli_launch_lease_revalidation`, each cite the **same** `launch_token`, and **exactly one** launch act is recorded for the session. A driver that attests `cli_launch_model_flag` at Gate B is **refused** — this is the F-001 case the earlier plan had no test for |
| `test_fabricated_launch_token_is_request_absent` | a `launch_token` this harness never minted ⇒ `model_selection_request_absent`, whatever the ordinals look like |
| `test_lease_absent_from_the_durable_record_is_request_absent` | a lease in the in-memory registry with no matching cell in `.pair_preparation.json` ⇒ `request_absent`. Registry-only is never sufficient |
| `test_revoked_lease_cannot_be_re_exercised` | a lease revoked by `_refuse_preparation`, by `_revoke_model_authority`, by a reuse supersession and by settlement — four cases — each ⇒ `model_selection_request_stale` on the next citation |
| `test_lease_for_another_handle_is_stale` | a valid, unrevoked lease whose `terminal_digest` is another session's ⇒ `request_stale`; delivery does not happen |
| `test_lease_with_a_different_requested_model_is_mismatch` | routing declares `claude-sonnet-5`, the lease records `claude-opus-5` ⇒ `model_selection_mismatch` |
| `test_launch_stamp_must_precede_request_stamp` | `launch_stamp == 0`, `launch_stamp == request_stamp`, and `launch_stamp > request_stamp` ⇒ `request_stale` in all three |
| `test_superseded_session_gets_a_new_lease_not_the_old_one` | when `_reuse_verdict` supersedes, the replacement's `CREATE_INTENDED` carries a **new** `launch_token` and the old one is revoked in the same write |
| `test_pair_admission_still_requires_counterpart_evidence` | a lease satisfies the request leg and does **not** substitute for pair admission: a same-command Worker with an unverified counterpart is still `model_selection_pair_unadmitted` |
| `test_prepass_evidence_cannot_be_replayed_at_gate_b` | the pre-pass's evidence (attested with `task_id == ""`) offered at Gate B is refused `model_selection_request_stale`. Locks §3a.2's structural guarantee |
| `test_second_verification_reuses_one_target` | with a **fake target factory**, the pre-pass and Gate B of one attempt produce **exactly one** target creation and **two** preflight turns, on the same handle and uuid |
| `test_preparation_schema_v4_rejects_v3` *(append to `scripts/test_os14_bugfix_regressions.py`)* | a v3 document under the v4 reader ⇒ `PAIR_PREPARATION_RECORD_CORRUPT`, never "nothing was prepared" / "unused" / "no baseline". Mirrors the existing v1→v3 case exactly |
| `test_contract_value_and_block_agree_after_the_replacement` *(append to `scripts/test_os49_contract_locks.py`)* | `AGENT_PROFILE_MODEL_SELECTION`'s **new** value is identical in the validator and in the `SKILL.md` block, and the block is still exactly 25 lines |

**Group 2 — the hosted delivery branch** (F-002; `scripts/test_os14_hosted_delivery.py`, new)

| Test | Asserts |
|---|---|
| `test_hosted_branch_delivers_when_tui_idle_and_worker_start_are_unavailable` | a harness whose `terminal wait --for tui-idle` returns **not satisfied** and whose `worker-start` returns `state != ready`: with **recognition and deliverability both true**, the delivery still succeeds through `dispatch` + the hosted write, `worker-start` is **never called**, and `_attach_terminal` records `low_level_tracked`. This is the F-002 case the earlier plan had no test for |
| `test_one_name_driver_is_refused_not_demoted` | **correction F-002, and the row whose earlier version asserted the opposite.** A handle with a valid hosted launch cell, in **two** cases — a driver exposing only `deliver_hosted`, and a driver exposing only `bind_target`. Both are **still RECOGNISED** (recognition is the cell alone) and both get **`HOSTED_TARGET_UNDELIVERABLE`** with cause **`HOSTED_CAPABILITY_INCOMPLETE`**: the call log contains **no** `terminal wait`, **no** `worker-start`, **no** `dispatch`, **no** `terminal send` and **no** `_attach_terminal`, the snapshot is restored and `release(handle)` runs once. **The earlier edition's row asserted "today's code path, byte-identical" for exactly these two drivers; an implementation that still does that must FAIL this test.** Asserted additionally: nothing was written to the shim's stdin |
| `test_hc_is_one_derivation_read_by_both_call_sites` | the two-name rule itself: `deliver_hosted` only, `bind_target` only, and both — and that `bind_prepared_target` (step 4d) and the deliverability test (step 8) called the **same** derivation function over the **same** driver object, so the preparation gate and the delivery path cannot disagree (the DR-02 C-1/C-3 defect) |
| `test_no_hosted_cell_is_minted_without_hc` | HC's **preparation** half: with a non-hosted-capable driver, step 4d mints **no** hosted launch cell, the prepared session's `--command` stays the **profile** command, and `bind_prepared_target` returns `not_hosted` and changes nothing. Such a session is then **not recognised** at step 8 and takes today's path — which is correct, because nothing hosted was ever created for it |
| `test_legacy_path_requires_no_hosted_cell` | the reservation, asserted from the other side: the **only** input that reaches today's `tui-idle` / `worker-start` / `terminal send` tail is a handle with **no** hosted launch cell. Parameterised over a revoked lease, a mis-bound digest, a dead channel and an incomplete HC — **none** of which may reach it |
| `test_recognition_survives_lease_revocation` | a revoked lease on a handle whose durable entry still carries its hosted launch cell is **still recognised** ⇒ the verdict is `HOSTED_TARGET_UNDELIVERABLE`, **not** today's path. This is the measured failure of the cheaper `split` candidate (X-4), and a rule that recognises on "capability ∧ unrevoked lease" must fail this test |
| `test_recognised_but_undeliverable_is_a_named_refusal` | for each of: lease revoked; lease bound to another digest; the driver returns **no** bound target — the result is `HOSTED_TARGET_UNDELIVERABLE` and the recorded call log contains **no** `terminal wait`, **no** `worker-start`, **no** `dispatch` and **no** `terminal send`; `_attach_terminal` was not called; the pre-barrier model snapshot was restored; `release(handle)` ran once |
| `test_channel_lost_right_after_gate_b_never_takes_the_legacy_path` | Gate B accepts, then the binding disappears before the branch evaluates deliverability ⇒ `HOSTED_TARGET_UNDELIVERABLE`. Asserted for **both** simulated `worker-start` receipts (`agent_unconfigured` and a non-ready `state`), because U-3 is 미검증 and the result must not depend on it. **The earlier behaviour — a `tui-idle` timeout, a created Dispatch and an attached row for an undelivered task — must be unreachable** |
| `test_not_recognised_targets_are_byte_identical_to_today` | a genuine legacy target (no hosted capability, no hosted launch cell) runs the unchanged tail: the `tui-idle` wait, the unconditional `worker-start`, the ready-state requirement, the stateless-receipt version check, the `agent_unconfigured`-only descent, and rung 4 |
| `test_every_other_adoption_failure_stays_fail_closed` | for a **non-hosted** target: a non-ready `state`, a stateless receipt on a runtime that is not the point-verified version, and a `worker-start` error whose code is not `agent_unconfigured` — each still raises, nothing is attached, and the pre-barrier model snapshot is restored |
| `test_gate_b_runs_before_the_hosted_branch` | the branch is unreachable without a Gate B acceptance; a refused Gate B produces no `dispatch` and no write |
| `test_hosted_idle_replacement_timeout_is_a_hard_error` | `wait_for_hosted_idle` failing ⇒ no `dispatch`, no write, snapshot restored, lease revoked |
| `test_hop1_short_write_is_not_delivered` | a hop-1 write whose byte count is less than the **frame** length ⇒ `not_delivered`: refusal, lease revoked, `release` called once, `_attach_terminal` **not** called, and the open Dispatch reported through `delivery_obligations()` |
| `test_hop2_short_write_is_not_delivered` | hop 1 **complete** and an ack reporting `child_write_bytes < payload_bytes` ⇒ `not_delivered`. **A test that only exercises hop 1 does not cover this**, which is the DR-03 W2 defect |
| `test_full_hop2_count_with_hop2_error_is_not_delivered` | an ack reporting `child_write_bytes == payload_bytes` **and** a non-null `hop2_error` ⇒ `not_delivered`, **not** delivered. Locks the measured buffered-write trap (X-5 row 4), where the shim's own count was full because the **flush** is what failed |
| `test_no_ack_is_UNKNOWN_and_stays_UNKNOWN` | hop 1 complete and **no ack** (channel closed / deadline / shim died) ⇒ verdict **`UNKNOWN`**. Asserted: **no resend** of any kind; **no** success settlement; **no** delivery to a second target; `_attach_terminal` **not** called; the Dispatch left **open** and reported through `delivery_obligations()`; `release(handle)` once. Asserted for **both** ground truths — nothing delivered, and delivered-and-completed — with the **same** driver-visible inputs, so a test that distinguishes them is itself wrong (X-5 rows 2 and 5) |
| `test_hop1_byte_count_alone_never_declares_delivery` | an ack-less path in which hop 1 wrote the full frame ⇒ **never** `delivered`. The driver's own byte count is hop 1 and may not be read as child input |
| `test_dead_child_produces_no_hop1_error` | the child is dead before hop 2 and the shim is alive: the driver's hop-1 write raises **nothing**, and the verdict comes from the ack's `hop2_error`. Locks the 반증 of P2's EPIPE transferring to the relay (X-5 row 4), and asserts the design does **not** poll for liveness instead |
| `test_attach_terminal_only_on_delivered` | `_attach_terminal(..., "low_level_tracked")` is called on the `delivered` verdict and on **neither** `not_delivered` **nor** `UNKNOWN`, and when it is called it is **last** |
| `test_hosted_branch_does_not_call_worker_start_or_terminal_send` | neither command appears in the recorded call log |
| `test_hosted_branch_preserves_session_use_and_settlement` | the `sessions` use row moves `DELIVERY_INTENDED` → `DELIVERED` with the dispatch id, and settlement still runs only through `wait_for_done` → `settle_attempt` |
| `test_preflight_result_is_not_settlement` | the driver returns on the preflight `result` and the attempt still settles only through `wait_for_done`; a driver that returns the real-task `result` as the outcome is refused |

**Group 3 — the control channel and its post-create bootstrap** (F-003;
`scripts/test_os14_hosted_channel.py`, new). The first six rows are the iteration-3
additions: they start the shim with **pre-create inputs only** and obtain the terminal
handle afterwards, so an implementation cannot pass them by handing a test factory a
pre-filled handle/digest.

| Test | Asserts |
|---|---|
| `test_shim_bootstrap_binds_after_terminal_create` | the **ordering test the iteration-2 plan had no equivalent of, and the one this group now starts from.** A real shim process is started with **pre-create inputs only** (endpoint path, launch token, session uuid, requested model, run id, role/phase/attempt, `--bootstrap-deadline-ms`) and a stub child program in place of `claude`. Asserted, in order: the shim sends a `hello` carrying **no** `terminal` and **no** `terminal_digest`, and carrying its own `shim_incarnation`; **no child process exists** at that point (`pgrep`/`os.waitpid(WNOHANG)` on the recorded pid set); a **synthetic create receipt** is then produced and its handle recorded exactly as `CREATED` does (`terminal_digest = sha256(handle)`); one `bind` is sent, echoing that incarnation; the `bound` echo equals the handle, digest and nonce sent; `child_started` then reports a **new** pid; and only after that does the first `in` frame get written. Also asserted: the first `out` frame's `seq` is 1, so nothing was relayed before the binding |
| `test_missing_bind_cannot_verify_or_deliver` | the same shim, never bound: `select_and_verify` refuses `model_selection_unverified` with **zero** inputs written and **no** child ever spawned, `deliver_hosted` refuses, `_prepare_role`'s existing path turns it into `PAIR_PREPARATION_MODEL_UNOBSERVED`, and `release(handle)` is called once. A `hello` that *does* carry a `terminal`/`terminal_digest` field is refused as well — the shim may not assert them |
| `test_stale_or_replayed_bind_is_refused` | **Corrected: case (2) previously asserted a refusal the documented rule did not produce** (review F-006, measured X-2 — the captured frame was **accepted** and spawned a child, because a fresh shim has the same token, state `unbound`, an **empty** seen-nonce set and a matching digest). Four cases, each testing **one** concern and never cited for another: **(1) authentication** — a `bind` whose `launch_token` is not the shim's ⇒ `bind_unauthenticated`, endpoint unlinked, non-zero exit, no child. **(2) freshness, the F-006 case** — a byte-identical `bind` **captured from a completed bootstrap of the SAME lease** and replayed to a **fresh incarnation of that lease** ⇒ refused **`stale_incarnation`**, non-zero exit, **0** children. The assertion must name `stale_incarnation`: a test that passes because the token is unknown, or because a nonce was already seen, is testing case (1) or case (4) and must be rejected in review. **(3) echo integrity** — a `bound` echo returning a **different** nonce or handle ⇒ `DRIVER_BIND_UNCONFIRMED` → `model_selection_unverified`, no input written. **(4) in-incarnation replay** — a captured frame replayed to the **same** incarnation that minted the value ⇒ `rebind_refused` (state no longer `unbound`, nonce already seen **in this incarnation**). Plus **idempotency**, which must stay accepted: the **identical** `bind` re-sent on the **same** connection yields the identical `bound` and **one** child, not two |
| `test_incarnation_value_is_never_durable_and_never_an_identity` | the shim writes **no** durable record of `shim_incarnation`; the harness neither stores nor interprets it; a restarted shim's value differs from its predecessor's; and the value appears in **no** preparation cell, artifact or log. Asserted together with O4: the `hello` still carries **no** `terminal` and **no** `terminal_digest`, so adding a shim-authored field did not make the shim an identity authority |
| `test_derived_endpoint_path_is_a_concurrency_interlock_not_a_freshness_one` | a second incarnation of the **same** lease binds the **same** derived path successfully once the first has exited and unlinked it (실제 관측, X-2) — so the test asserts that **nothing in the design relies on the path for freshness**, and that the refusal of a *pre-existing* path still blocks a **concurrent** second binder |
| `test_every_create_mints_a_new_lease` | §3a.12's **mint rule**, as the three reachable creates: a first create (`entry is None`), a retry after a parsed `CREATE_REFUSED`, and a reuse-gate supersession. Each writes a **new** `launch_token` in its own `CREATE_INTENDED` call, and a supersession revokes the old token **in the same write**. Asserted additionally: **no** path re-exercises a recorded token for a second create, and a `CREATE_INTENDED` entry still blocks `PAIR_PREPARATION_OUTCOME_UNKNOWN` unconditionally |
| `test_rebind_with_different_identity_is_refused` | after a successful bind, a second `bind` with a different `terminal`/`terminal_digest`/`bind_nonce` ⇒ `rebind_refused`, the channel closed and the endpoint unlinked, **the child left alive** and still teed to the terminal; the already-bound driver's next write sees the closed channel and reports `DRIVER_TARGET_REBOUND` → `model_selection_unverified`, nothing delivered. And from the harness side: `bind_prepared_target` called twice for one handle refuses `DRIVER_TARGET_REBOUND` and mints **no** second nonce |
| `test_bootstrap_deadline_bounds_every_unbound_path` | **Corrected and widened — this is the F-005 case the earlier plan could not catch.** A real shim is started with a small `--bootstrap-deadline-ms` and **four** client behaviours are run against it: **(a) no client ever connects**; **(b)** a client connects, reads `hello`, never binds; **(c)** a client sends a **partial** `bind` and closes; **(d)** a client connects and closes **before the `hello` write completes**. In **all four**, asserted: the shim **self-exits non-zero** inside a bound multiple of its own deadline (not killed by the test's watchdog — the watchdog firing is a **failure**), the endpoint is **unlinked**, and `child_spawns == 0`. Cases **(a)** and **(d)** are the ones the `hello`-origin rule left unbounded (measured alive at 5× the deadline with the endpoint intact, X-1), and **(d)** is why an `accept()`-origin correction would not have been enough |
| `test_bootstrap_deadline_is_not_restarted_by_any_peer_event` | the deadline is armed once at listener readiness and is **not** reset by an `accept()`, a successful `hello` write, a failed `hello` write, a peer disconnect, a partial `bind`, or a `bind` that fails a check; it is disarmed **only** by the `unbound → bound` transition on a verified `bind`. Asserted by arming a short deadline and generating each event inside it |
| `test_single_verdict_does_not_depend_on_deadline_ORDER` | **the relation the earlier edition argued from durations.** Run the same refusal **twice** — once with the shim's deadline longer than the driver's, once **shorter** — and assert the **same** single reported verdict both times: `model_selection_unverified` → `PAIR_PREPARATION_MODEL_UNOBSERVED`, from the **driver**. The shim's `ctl refused bootstrap_timeout` frame, when it arrives, appears only as a logged diagnostic and never as a second refusal; the `CREATED` entry **stays**, the terminal is enumerable as residual, `release(handle)` runs once, and **no** `terminal read` appears in the call log |
| `test_endpoint_path_is_asserted_against_the_platform_sun_path` | a composed endpoint path longer than the platform's own `sun_path` budget is refused **before** `bind()` with a named error, and the assertion reads the platform's value rather than a constant from the design (104 on the build this was measured on, not 108) |
| `test_crash_between_create_and_bind_is_named_not_repaired` | a `CREATED` record with its `terminal_digest` and no binding anywhere ⇒ `DRIVER_TARGET_UNBOUND`, nothing delivered, no second bootstrap attempted, and the residual process set contains the **shim only** |
| `test_coordinator_restart_refuses_a_durable_lease` | a successor harness with an empty registry, a v4 record naming a lease and an endpoint digest, and a socket that still answers ⇒ `DRIVER_TARGET_UNBOUND` → `model_selection_unverified` → `PAIR_PREPARATION_MODEL_UNOBSERVED`; **nothing is delivered and no re-bind is attempted** |
| `test_spoofed_handshake_is_refused` | **amended for the bootstrap: no case starts with a pre-filled handle or digest.** On the `hello` leg, one case each for a wrong `launch_token`, `session_uuid`, `requested_model`, `launch_argv_digest`, `endpoint_digest` and `protocol` ⇒ `model_selection_unverified`, with **no `bind` sent** in any of them. On the `bound` leg, one case each for a wrong `terminal`, `terminal_digest` and `bind_nonce` in the echo ⇒ `DRIVER_BIND_UNCONFIRMED`. The handle and digest are supplied **only** by the driver's own `bind`, composed from a create receipt the test produces after the shim is already running |
| `test_partial_handshake_is_unverified` | a truncated `hello`, an absent `hello`, and a `hello` that is not JSON ⇒ unverified on a bounded deadline, with **no** fall back to `terminal read`; and a `bound` reply that is truncated or non-JSON ⇒ `DRIVER_BIND_UNCONFIRMED` |
| `test_stale_endpoint_path_is_refused_not_reused` | the shim refuses to bind a path that already exists and exits non-zero without spawning a child |
| `test_second_connection_is_refused` | a second connect is answered `channel_already_bound` and closed; the first writer keeps the channel |
| `test_sequence_gap_is_unverified` | a missing `seq` in the shim→driver direction makes the turn's evidence incomplete ⇒ unverified |
| `test_turn_correlation_uses_in_band_markers_not_shim_labels` | a shim that mislabels every frame's `turn` still yields correct attribution, because the driver keys on the replayed `user` echo, the turn's `init`, its `assistant.request_id` and its `result` |
| `test_driver_never_issues_terminal_read_for_channel_discovery` | the recorded call log contains no `terminal read`; the endpoint path is **derived** from the lease, not discovered |
| `test_release_is_exactly_once_and_idempotent` | every `_refuse_preparation` path, every bootstrap refusal, the dead-target detection, §3a.13's `HOSTED_TARGET_UNDELIVERABLE` refusal and the **round close** all call `release(handle)`; a second call is a no-op; the socket is unlinked and the per-run directory removed when empty; no target leaks |
| `test_target_is_held_until_the_ROUND_closes_not_until_settlement` | **the DR-01 case the earlier plan had no test for.** A full `(phase, attempt)` round with a fake target factory: the Worker's graph turn prepares both roles, verifies both, delivers and **settles**; its settlement is then **validated clean**; then the Reviewer's graph turn runs. Asserted: `release(handle)` was **not** called for either handle at the Worker's `settle_attempt` **nor** at its validation; the Reviewer round's pre-pass of the **worker** role writes a preflight turn into the Worker's **post-task** session and succeeds; and `release(handle)` is called **once per handle** only after the Reviewer's settlement is validated **and** ROUTE emits a token that is neither `PREPARE_REPAIR` nor `PREPARE_PHASE_REVIEWER`. **Asserted explicitly at the Worker→Reviewer boundary:** with the Worker `VALIDATED` and ROUTE emitting `PREPARE_PHASE_REVIEWER`, the round is **open** and **neither** handle is released — because the phase Reviewer is a **required** role of this round and is still `ACTIVE`. **Three implementations must fail this test:** a settlement-scoped release, a `VALIDATED`-only release, and one whose (1a) quantifies over **dispatched** roles instead of **required** ones. A settlement-scoped release fails at `PAIR_PREPARATION_MODEL_UNOBSERVED`, which is what was measured (X-3) |
| `test_required_roles_not_dispatched_roles_decide_the_close` | the F-001 predicate, isolated. **Case A (`risk` high/medium):** Worker `VALIDATED`, phase Reviewer **prepared but never dispatched** (`ACTIVE`), ROUTE emits `PREPARE_PHASE_REVIEWER` ⇒ (1a) **false**, (1b) **false**, round **open**, **no** release. **Case B (`risk` low):** Worker `VALIDATED`, **no** Reviewer prepared or required, ROUTE emits `ADVANCE_PHASE` ⇒ (1a) **true**, (1b) **true**, round **closed**, `release(handle)` once. Asserted additionally: the required-role set is read from the run's `risk` and agrees with `routing.phase_gate`'s own rule (`risk == "low"` ⇒ `PASS` from the Worker alone; otherwise `PENDING` while `reviewer_result is None`), so the predicate and the engine cannot disagree about what the round needs |
| `test_holding_the_target_restores_no_authority_and_skips_no_verification` | over the same round: **six** driver calls occurred (4 pre-pass + 2 Gate B), **each with its own ticket token** and its own pair of ordinals; no call reused another's evidence; Gate B refused a replayed pre-pass attestation as `model_selection_request_stale`; the reuse gate was consulted **only** for the delivery role; and the end-state authority key is a **new** ticket, never a restored one |
| `test_settlement_alone_does_not_close_the_round` | **the F-001 case, and the one a settlement-keyed hook fails.** A role's `settle_attempt` has run and `VALIDATE_SETTLEMENT` has **not yet** been reached: the target is `SETTLED_PENDING_VALIDATION` and `release(handle)` **must not** have been called for either handle. Asserted for the Worker's turn **and** for the phase-Reviewer's turn — the Reviewer's is the one the earlier edition released |
| `test_validation_repair_of_either_role_holds_both_targets` | for a `DECISION_GATE_FORM_DEFECT` with budget left, in **two** separate cases — a defect in the **Worker's** record and a defect in the **phase-Reviewer's** record — the role goes `REPAIR_PENDING`, ROUTE yields `PREPARE_REPAIR`, and **neither** handle is released; the repair's re-entry re-verifies **both** roles with **fresh** tickets (6 distinct ticket tokens over the round so far, none reused); and the repaired role's re-delivery succeeds on the held target. **A specification in which the Reviewer's settlement released the pair cannot pass this test**, which is why the two expectations are now stated once, in one direction |
| `test_round_close_disposes_on_every_terminating_path` | **Parameterized over the rows of §3a.12 TL's 15-row hold/dispose table in THREE case groups with THREE DIFFERENT expectations, and no case carries two of them.** Each case's close outcome is read from the ONE close predicate in §3a.12.1 — **clause 1** = **(1a)** every role REQUIRED by the round is `VALIDATED` **AND (1b)** ROUTE's token is neither `PREPARE_REPAIR` nor `PREPARE_PHASE_REVIEWER`; **clause 2** = refusal or abandonment — and this row states **no** close condition of its own. **Only the clause-1 cases are evaluated by "(1a) AND (1b)"; the clause-2 cases are not and must not be written as if they were.** **And because (1a) quantifies over the roles REQUIRED by the round — which the run's `risk` decides — `risk` is itself an outcome-changing branch on every row that involves a completion or a repair: the cases below are parameterized over it, and no such row is given one risk level as though it had only one.** **A. HOLD cases — TL rows 1a, 2, 3 and row 4's HIGH/MEDIUM held branch. Expectation: `release(handle)` is called ZERO times.** In the window that observes that triggering state the round is **open** — TL's own columns read "round closed? **no**" and "who disposes: **nobody**" — and the target, channel and lease of **every role prepared under the key** are asserted still **held**: a **HIGH/MEDIUM** Worker turn accepted with the required Reviewer still `ACTIVE` (ROUTE `PREPARE_PHASE_REVIEWER`; (1a) and (1b) both false; row 1a — the LOW counterpart of this shape is **not** here, it is group B's row 1b); **Worker repair pending, as TWO risk cases** (row 2) — at **HIGH/MEDIUM** the phase Reviewer is additionally REQUIRED and `ACTIVE`, which is the scope of TL row 2's own "(HIGH/MEDIUM)" qualifier, and at **LOW**, where **no Reviewer is required, prepared or dispatched**, the round is **still open** because ROUTE's `PREPARE_REPAIR` makes **(1b)** false on its own, so the Worker's single prepared target is held with **zero** releases just the same; **phase-Reviewer repair pending** (row 3 — **reachable only at HIGH/MEDIUM**: at LOW no phase Reviewer is ever required or prepared, so this row has **no** LOW instance to classify); and a **repaired Worker at HIGH/MEDIUM**, whose clean settlement still yields `PREPARE_PHASE_REVIEWER` because the Reviewer is required and `ACTIVE` (row 4's held branch) — **the risk-dependent twin of group B's row-4 trigger (a): one repaired-Worker shape, HOLDING at HIGH/MEDIUM and RELEASING at LOW, which is why neither group may state it without its risk level.** **The observed window ends at that state**, and the release count of a trace later allowed to run on to a normal close belongs to group B and is never read back onto these cases. **"A clean repair followed by any non-`PREPARE_REPAIR` token" is NOT the release condition and must not be asserted as one** — `PREPARE_PHASE_REVIEWER` is such a token and is group A's row-4 case, which is the overlap the earlier editions of this row contained. **B. RELEASE cases — TL row 1b, BOTH of row 4's closing triggers, and rows 5–13. Expectation: EXACTLY ONE `release(handle)` per handle that was actually prepared, on the closing / refusal / disposal path TL names for that row — and no release is required for a handle that was never created.** Under **clause 1**: a **LOW**-risk Worker-only round with no repair (`ADVANCE_PHASE`; row 1b); **row 4's closing branch as TWO separate cases, because it has two triggers and they differ in risk and in role** — **(a) a repaired Worker at LOW risk**, whose clean settlement leaves the Worker, the round's **only** required role, `VALIDATED`, so `phase_gate` returns `PASS` from the `COMPLETE` Worker alone and ROUTE emits **`ADVANCE_PHASE`** — the engine's own rule, read from the code and not inferred (`routing.py`: `if state["risk"] == "low": return "PASS"`, then `return "ADVANCE_PHASE" if gate == "PASS"`), the round **closes**, and each prepared handle is released **once** — and **(b) a repaired phase Reviewer at HIGH/MEDIUM**, whose clean settlement leaves **every** role REQUIRED by the round `VALIDATED` under a closing token, yielding **`ADVANCE_PHASE`** or **`PREPARE_CORRECTION`**; **Reviewer FAIL** (`PREPARE_CORRECTION`, or `ESCALATE` when the phase budget is spent — two branches, same expectation; row 7); and **phase PASS with both required roles `VALIDATED`** (`ADVANCE_PHASE`; row 8). **Rows 7 and 8 are reachable only at HIGH/MEDIUM**, both presupposing a phase Reviewer that has produced a verdict; **a LOW round's single-role completion is row 1b when no repair ran and row 4 trigger (a) when one did — never row 8.** Under **clause 2**: **repair exhaustion** (`remaining_repair_budget == 0` ⇒ `BLOCK`; row 5); a **SEMANTIC** and a **LIFECYCLE** defect (⇒ `BLOCK`, and **no** repair attempt spent; row 6); **BLOCKED** — a non-`COMPLETE` `worker_done` (row 9); **ESCALATED** (row 10); **cancel** and **abandon** (⇒ the DISPOSE node; row 11); **PAUSE** (row 12 — which TL disposes **deliberately** rather than holds); and a **step-4 refusal** in **two** branches (row 13) — one raised **after** a handle exists, whose handle the `_refuse_preparation` path releases **once**, and one raised **before** any create, where **no handle exists and therefore no release is required**. **Rows 5–6 and 9–13 were each checked for a risk-dependent branch and have none that changes the outcome:** `risk` changes only **which** role can be the defecting one (at LOW, only the Worker) and **how many** handles were prepared (at LOW, the Worker's alone), and the per-prepared-handle count already absorbs both without a second case. **The one-per-handle count is asserted over group B and over group B only** — these are exactly the paths on which a **live** Coordinator executes `release`. The deliberate **second** `release` call that proves idempotency stays in `test_release_is_exactly_once_and_idempotent`; **no case of this test ever requires "exactly once" and "called twice" in the same observed window.** **C. COORDINATOR EXIT — TL row 14: EXCLUDED from this parameterized release-count test.** Its round **never closes** and **nobody** calls `release` — the registry and the binding are in-process state that dies with the process (O6) and the surviving CLI child is the **operator's residual** — so there is no release count here to assert, and asserting one would contradict TL row 14. It is covered **solely** by `test_coordinator_exit_leaves_a_refusal_not_a_rebind` below, which keeps the successor's named `DRIVER_TARGET_UNBOUND` refusal, the **no-rebind** rule and the residual sessions, and **assumes no cleanup by the exited Coordinator**. This row adds **no** automatic-cleanup policy and changes nothing in TL; row 14 is reached the same way at every risk level, so it needs no risk split. **Asserted over groups A and B alike, and the only claim in this row that spans both:** no target, channel or lease **prepared under one `(phase, attempt)` key is ever observed under a different one**. That is a **cross-key** claim only; it does **not** deny a group-A hold, which keeps every prepared target under the **same** key for the next turn of the same round |
| `test_repair_hold_does_not_bypass_the_reuse_gate` | on a repair of the **delivery** role, whose `session_use` row is at `DELIVERED`: `_reuse_verdict` **is** consulted and the shipped reuse gate is asked with a fresh liveness observation. Two cases — gate **permits** ⇒ the held handle is adopted and the repair delivers into it; gate **refuses** ⇒ a supersession with a **new** `CREATE_INTENDED`, a **new** `launch_token`, a **new** session, and `release` on the superseded handle. Asserted: TL never short-circuits the gate, and a repair on the **counterpart** role does not consult it at all (`is_delivery_target` scoping) |
| `test_repair_budget_bounds_the_hold` | `MAX_REPAIR_ATTEMPTS = 2` per role is what bounds the hold in turns: a third FORM defect on the same role yields `BLOCK` (`GATE_REPAIR_EXHAUSTED`) and the round closes; and a non-repair token resets the counter, so the next round starts with a full budget. Asserted as a **turn** bound only — the wall-clock hold stays unmeasured (D5) |
| `test_coordinator_exit_leaves_a_refusal_not_a_rebind` | the process-exit row of TL: a successor harness with an empty registry facing a durable lease ⇒ `DRIVER_TARGET_UNBOUND` and **no** rebind; a leftover endpoint file at a previous lease's derived path is **never** adopted, because the new create minted a new token; `release` and the shim's unlink both tolerate `ENOENT`. Asserted explicitly: the design claims **no** process-exit cleanup of the CLI child, and the test records the child as the operator's residual |
| `test_tl_does_not_touch_the_dispatch_capability` | the held lease makes **no** attempt to retain or renew Orca's dispatch capability, and a settled Dispatch's capability being revoked (observed: `capability_revoked_at == completed_at`) does **not** release the target. The two lifetimes are asserted to be independent |
| `test_shim_does_not_terminate_its_child_on_unbind` | the channel closing unlinks the endpoint and leaves the CLI child alive and the terminal intact, so the residual-session procedure finds what it finds today |
| `test_dead_target_refuses_delivery_and_revokes` | a target whose write raises `BrokenPipeError` ⇒ no `worker-start`, no `dispatch`, no write; authority revoked, **history preserved**; the pre-barrier snapshot restored. Locks P2 |
| `test_second_process_under_recorded_uuid_is_refused` | a factory that returns a **new** target for an already-bound handle ⇒ `DRIVER_TARGET_REBOUND`, reported as `model_selection_unverified`; nothing delivered |

**Group 4 — the satisfaction rule `exact_equality.v1`** (F-004; appended to
`scripts/test_os14_claude_driver.py`)

| Test | Asserts |
|---|---|
| `test_consistent_wrong_canonical_is_mismatch` | **both** `init.model` and `assistant.message.model` report `claude-haiku-4-5-20251001` for a `claude-sonnet-5` request, every other conjunct passing ⇒ `mismatch` → `model_selection_mismatch`, **not** verified. This is the F-004 case the earlier plan had no test for |
| `test_fully_qualified_request_equal_to_reported_is_verified` | the positive case: `claude-sonnet-5` requested, `claude-sonnet-5` reported on both channels ⇒ verified |
| `test_alias_request_is_refused_before_any_turn` | `sonnet` requested ⇒ `unverifiable` → `model_selection_unsupported`, and the scripted stream records **zero** written inputs. The unknown/changed-alias negative |
| `test_changed_canonical_spelling_is_mismatch_not_normalized` | request `claude-sonnet-5`, report `claude-sonnet-5-20260101` ⇒ `mismatch`; no prefix or family matching is applied |
| `test_no_normalization_is_applied` | `Claude-Sonnet-5`, ` claude-sonnet-5 ` and `claude_sonnet_5` as reported values all ⇒ refusal, never a pass |
| `test_gateway_style_alias_is_unsupported` | a company-gateway-shaped alias ⇒ `model_selection_unsupported`, and the refusal text names the rule version |
| `test_satisfaction_rule_version_is_recorded` | the driver exposes `SATISFACTION_RULE == "exact_equality.v1"` and the value appears in `docs/COMPATIBILITY.md`, so widening it cannot be silent |

**Group 5 — live, env-gated** (`scripts/test_os14_claude_driver_live.py`, new)

| Test | Asserts |
|---|---|
| `test_live_preflight_then_delivery` **[live]** | fixture cwd, one process, two preflight turns + one delivery; asserts one process, one session, three distinct `request_id`s. The automated form of P1 |
| `test_live_kill_between_turns` **[live]** | the kill case, asserting `EPIPE` and a refused delivery. The automated form of P2 |
| `test_live_shim_spawns_and_tees` **[live]** | **the first execution of §3a.12's channel, and the single most important row in this plan** — the protocol has never been run (§8). The shim is started from a real `terminal create --command` with pre-create inputs only; asserted in order: a `hello` with no handle/digest, carrying a `shim_incarnation`, and **no child process yet**; then the real create receipt's handle bound in one `bind` echoing that incarnation; the `bound` echo; `child_started` with a **distinct** pid; and only then one turn whose events arrive on **both** the terminal copy and the channel. Additionally asserted, because they are measured requirements rather than implementation details: the child's stdin is a **pipe** and **not** the terminal's PTY (**H-1** — a PTY stdin was measured to make `claude -p` exit `rc=1` immediately, S-1a), and a **CR**-terminated line reaching the shim's own stdin is delivered to the child **LF**-terminated (**H-2**, S-0). This is the live form of `test_shim_bootstrap_binds_after_terminal_create`, and it is the row that exercises U-1 for the shape the design actually specifies |
| `test_live_hosted_round_holds_both_targets` **[live]** | the live form of `test_target_is_held_until_the_ROUND_closes_not_until_settlement`: two real hosted sessions through one gate round, asserting the **6 preflight + 2 real-task** turn count of D5 against the actual stream, that the Worker's post-task session answers the Reviewer round's pre-pass, and the **wall-clock duration of the hold** — which is the hold-duration observation §3a.8 and D5 both still need and which **no** run has taken (measured ceilings: 90 s, 68.8 s). **With one injected FORM defect** in the phase-Reviewer's decision-gate record, so the live form also covers the repair hold and its `+3 preflight + 1 real` — the longest hold the design admits |

### 9.4 What the plan deliberately does NOT include

- **No change** to `_stale_model_evidence`, the authority/history split, `pause_policy.py`,
  `launcher.py` (beyond mirroring), the reuse gate's conditions, pair admission, the failure
  vocabulary, `ORCHESTRATOR_LOG_COLUMNS`, or the standalone runtime.
- **No CLI resume wiring.** `launcher.run_pause_cli` and `launcher._watchdog_wiring` still
  pass no routing and no driver, so a model-aware run still cannot be resumed through the CLI
  (`docs/COMPATIBILITY.md`, "Recovery support scope"). That is the existing fail-closed
  outcome and completing it is **not** part of this plan.
- **No alias support.** `exact_equality.v1` ships fully qualified ids only; an alias table is
  a later rule version with its own approval, and a profile that declares `sonnet` must be
  edited rather than accommodated.
- **No channel re-bind after a coordinator restart** (nor after a crash between `terminal
  create` and the bind), and no shim-initiated teardown of the CLI child. All are named
  alternatives or named refusals in §3a.12, and all are out of the first scope.
- **No cross-round target reuse.** The target, channel and lease are round-scoped (§3a.12 TL)
  and a new gate iteration prepares fresh sessions. Extending a hold **across** rounds would
  be a second authority beside the reuse gate and is not proposed.
- **No automatic recovery from a delivery `UNKNOWN`.** No resend, no probe, no second target,
  no success settlement. The open Dispatch is **reported** through `delivery_obligations()` and
  resolved by the operator. A recovery protocol would have to distinguish two states that no
  driver-visible observation separates (§3a.13.1), so there is nothing to build.
- **No general replay prevention.** `shim_incarnation` covers one measured case. A
  peer-credential check, a signed bootstrap or a same-incarnation interposer defence are each
  separate work with their own observations, and none is in this plan.
- **No company-environment work** (§7), **no** `/model` experiment, **no** Orca
  `worker-start --model` execution (D7).
- **No `-p` → interactive migration.** If D8 later chooses the interactive target, this plan
  is superseded, not extended.
