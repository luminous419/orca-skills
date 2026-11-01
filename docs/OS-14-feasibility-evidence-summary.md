# OS-14 — minimal-feasibility evidence summary (run `run_fd87428a21f0`)

**Why this file exists.** `docs/OS-14-claude-driver-design.md` rests on measurements whose raw
records live under `artifacts/runs/run_fd87428a21f0/evidence/`, which is **not committed**. A
reviewer with no access to that directory needs the observations, the exact values and — above
all — the **limits**, without taking the design's word for them. **Nothing in THIS FILE is an
approval, and nothing in it was changed by one.** *(Superseded 2026-11-01: this line previously
read "D2, D3, D5, D8 and the product permission scope are 승인 대기".)* The current status, which
this file does not itself grant, is recorded in `docs/OS-14-claude-driver-design.md`'s top status
block and §6: **D2, D3, D5 and D8-a..d were APPROVED by the user on 2026-11-01** for the local
first implementation (Jira OS-14 comment 10101), and the **product permission scope remains
승인 대기 (PENDING)** with its written proposal explicitly unexecuted. **Approval is not
measurement:** every limit below stands exactly as measured, and an approved decision does not
convert a 미검증 item into a verified one.

**Provenance.** Run `run_fd87428a21f0` (ANALYSIS, risk high, analysis only): `ANALYSIS.md`
COMPLETE, `REVIEW_ANALYSIS.md` and `FINAL_REVIEW.md` both **PASS WITH NOTES**, `FINAL_RESULT.md`
COMPLETED. Both reviewers independently re-ran all five stage-1 probes and re-derived the stage-2
numbers from the raw streams and read-only runtime queries. That run changed **no** production
code, test, Skill file, schema, contract or design document — its output was a correction list,
which the current design edition folds in.

**Environment, and it is part of every claim below.** macOS `Darwin 25.5.0`; Orca `1.4.197`;
Claude Code `2.1.260`; `claude -p --input-format stream-json`; personal OAuth, **no API key**
(`system/init.apiKeySource = "none"`). Nothing here is a company-gateway (PortKey/GLM) result.
Tags: **실제 관측** = measured · **추론** = inferred · **미검증** = not observed ·
**문서상 설계** = specified only.

---

## 1. Stage 1 — five probes against the SPECIFICATION (0 model turns)

Each probe is one self-contained stdlib script with fixed deadlines and its own watchdog,
printing JSON, implementing the state machine the **design** describes. **Nothing in the product
was executed:** no shim, no `AF_UNIX` channel, no `bind`, no launch lease and no relay exists in
the repository (toy→doc→code mapping: `evidence/40-probe-findings.md` §A). What they establish is
the **internal consistency of the specification**; they are **not** production reproductions.
Command ledger, written *before* the probes ran: `evidence/00-commands-run.md`. Paths below are
relative to `artifacts/runs/run_fd87428a21f0/evidence/`.

| probe | script | results | verdict |
|---|---|---|---|
| **X-1** F-005 deadline origin | `10-f005-runner.py`, `10-f005-shim.py` | `11-f005-results.json`, `11-f005-cleanup.txt` | **확인, and widened** |
| **X-2** F-006 replay / freshness | `12-f006-runner.py` | `13-f006-results.json`, `14-f006-second-shim-trace.md` | **확인** |
| **X-3** DR-01 pair lifetime | `15-dr01-lifetime.py` | `16-dr01-results.json` | **확인** |
| **X-4** DR-02 hosted-failure fallback | `17-dr02-admission.py` | `18-dr02-results.json`, `19-dr02-capability-diff.md` | **확인**, conditional on the 미검증 U-3 |
| **X-5** DR-03 delivery verdict | `19a-dr03-relay.py` | `19b-dr03-results.json`, `19c-dr03-cleanup.txt` | **확인** |

### X-1 — the bind deadline never starts in the window it exists for

`bind_deadline_ms = 1200`, watchdog `6000` ms (5×). "self-exit" = the shim exited **without**
the runner killing it. The measurement is the **actual exit**, never a comparison of two
durations.

| client behaviour | origin = the `hello` write (as specified) | origin = **listener readiness** |
|---|---|---|
| (a) no client ever connects | **no self-exit** — killed at **6026 ms**; children **0**; endpoint **NOT** unlinked; only event logged: `listener_ready` | self-exit `rc=3` at **1227 ms**; 0 children; unlinked |
| (b) connects, reads `hello`, never binds | self-exit `rc=3` at 1237 ms; 0 children; unlinked | self-exit `rc=3` at 1240 ms; 0 children; unlinked |
| (c) connects, partial `bind`, closes | self-exit `rc=3` at 1230 ms; 0 children; unlinked | self-exit `rc=3` at 1241 ms; 0 children; unlinked |
| (d) connects and closes **before** the `hello` write completes | **no self-exit** — killed at **6005 ms**; 0 children; endpoint **NOT** unlinked; events `listener_ready`, `accepted`, `hello_write_failed`, `peer_eof` | self-exit `rc=3` at 1235 ms; 0 children; unlinked |

Case **(a)** is review F-005 exactly as stated; case **(d)** is a second instance the review did
not name, and it is why moving the origin to the first `accept()` would not have been enough.
**`child_spawns == 0` in every case under both origins** — spawn-after-bind holds, so a failed
bootstrap costs zero model turns.

**Incidental and load-bearing:** on this macOS build `sun_path` is **104** bytes, not 108 — a
first runner version using descriptive directory names failed `bind()` for every path longer than
**103** characters. **Real-duration corroboration:** an orphan shim from an earlier dispatch was
alive after **2086 s** (≈**1738×** its own 1200 ms deadline), having logged only `listener_ready`,
spawned 0 children and left its endpoint; recorded (`41-f005-orphan-observation.md`), then cleaned
(`42-f005-orphan-cleanup.txt`).

### X-2 — a captured `bind` replayed to a fresh shim of the SAME lease is accepted

Three **separate** concerns, deliberately not conflated:

| concern | case | documented rule (memory-only seen-set) | per-incarnation freshness value |
|---|---|---|---|
| idempotency | identical `bind` resent on the **same** connection | identical `bound`, **1** child, `rc=0` | identical, **1** child |
| authentication | another lease's `launch_token` | `bind_unauthenticated`, `rc=4`, **0** children, unlinked | same |
| **freshness** | **same lease**, **fresh incarnation**, byte-identical captured `bind` | **ACCEPTED** — `bound` echoed, **1** child spawned | **REFUSED** `stale_incarnation`, `rc=4`, **0** children |

Every documented check passes under the replay: same lease ⇒ same token; fresh process ⇒ state
`unbound`; memory-only ⇒ empty seen-set; the frame was valid when captured ⇒ the digest matches.
**The authentication case was not used as evidence for the freshness case.** Supporting:
`same_endpoint_path_reused_by_incarnation2 = true` (the derived path is a **concurrency**
interlock, never a freshness one, because the shim unlinks its own endpoint on exit), and
`hello_had_terminal_field = false` in every run. `14-f006-second-shim-trace.md` analyses the
alternative — proving the harness can never start a second shim per lease: current code is
strongly in its favour (the one window that could leave a live unknown first shim,
`CREATE_INTENDED`, is **blocked, not retried**), but three gaps leave it **미검증** — the
supersession mint rule exists only as a planned test, the retry path's mint rule is unstated, and
the derived path is not a freshness interlock (measured).

### X-3 — a settlement-scoped target release refuses the Reviewer round

The design's sequence was run against the current adapter's call order (both roles prepared, then
both verified unconditionally; Gate B first inside `start_worker`; settle; then release), tracking
three lifetimes separately.

| policy | Worker round | Reviewer round | blocking step |
|---|---|---|---|
| release **after settlement** | completed | **REFUSED** | the Reviewer round's **pre-pass of the `worker` role** ⇒ `PAIR_PREPARATION_MODEL_UNOBSERVED` |
| release only the **settled role's** target | completed | **REFUSED** | identical — the settled role **is** the role the next round re-verifies |
| hold lease + channel until the **gate round** closes | completed | **completed** | none |

The three lifetimes after the Worker settles under the first policy: **Dispatch settlement**
`worker: true` — correct and never reopened, so this is **not** a settlement defect; **current
model authority** `worker: null` — revoked; **target/channel/lease** — released. Only the third is
what the round-scoped policy changes, and under it the end-state authority is a **new** ticket,
never a restored one.

### X-4 — a hosted target's failure falls through to the legacy path

Five target classes × three rules × **both** possible `worker-start` receipts, because **U-3 is
미검증**.

| target (the probe's own class name) | capability conjunct | launch lease exists | documented rule | consequence |
|---|---|---|---|---|
| `T1_genuine_legacy` — *"a real TUI agent terminal: no hosted capability, no launch lease"* | **false** | **false** | legacy path | `agent_unconfigured` ⇒ the task reaches the agent (correct); non-ready ⇒ hard error (fail-closed) |
| `T2_hosted_all_true` | true | true | hosted branch | delivered through the channel |
| `T3_hosted_channel_dead_before_gate_b` | true | true | **Gate B refusal** | nothing created — **not** the hazard |
| **`T4_hosted_channel_lost_right_after_gate_b`** | true | true | **legacy path, silently** | on `agent_unconfigured`: a `tui-idle` timeout spent, a **Dispatch created**, the prompt written into the **shim's** stdin, an **attached row recorded** — for a task the CLI child never receives; `worker_done` never arrives. On a non-ready receipt: hard error, nothing created |
| `T5_hosted_lease_revoked` | true | true | **legacy path, silently** | the same two outcomes |

Recognition keyed on "capability ∧ unrevoked lease" was measured **insufficient** — the
revoked-lease case still took the legacy path, because revocation erases the fact the rule
recognises a hosted target by. A split whose recognition rests on a **durable hosted launch cell**
produced a **named refusal** for both cases, with nothing created.

> **What X-4 did NOT test, stated because the design goes further than this probe.** The five
> classes above are the probe's complete input set, and the capability column is the point:
> **every hosted class carried the capability conjunct `true`**, and the one class with it
> `false` also had **no launch lease**. So the combination *"a durable hosted launch cell exists
> **and** the driver's capability is absent or incomplete"* was **never run**. The probe's own
> winning candidate (`split_v2`) computed recognition as `capability ∧ lease_exists`, so it
> would have sent that combination to the **legacy** path — it was simply never fed an input
> that exposes it. The design therefore recognises a hosted target on the **durable cell alone**
> and treats capability as a **deliverability** requirement whose failure is a named refusal;
> that correction is **추론 from DR-02's safety rule, not a measured result**, and it is tagged
> that way in the design (§3a.11 HC, §3a.13 Stage 1).
`19-dr02-capability-diff.md` quotes five statements of the hosted-capability condition from the
design verbatim: the normative admission paragraph required **one** callable name
(`deliver_hosted`), while four other places required **two** (`deliver_hosted` **and**
`bind_target`) and two of those said explicitly that a one-name driver is not hosted-capable.

### X-5 — UNKNOWN is irreducible, and the byte counts belong to different hops

Real processes, driver → shim → child, five injected faults; the child is drained to EOF before
ground truth is read. **Hop 1** = the driver's `AF_UNIX` write of the **outer frame**; **hop 2**
= the shim's write of the **payload** into the child's stdin.

| fault | hop 1 | hop 2 | task completed | driver's knowledge |
|---|---|---|---|---|
| none | **150 / 150** bytes, ok | **73 / 73**, full | **true** | `delivered` |
| stop after the frame arrives, **before** the child write | 150/150, ok | none | false | **UNKNOWN** |
| partial child-stdin write | 150/150, ok | **24 of 73** | false | `not_delivered` |
| child killed **before** hop 2 | 150/150, ok, **no error at all** | none | false | `not_delivered` — only because the shim survived to say so. The ack reported `child_write_bytes = 73` of 73 **and** `hop2_error = BrokenPipeError`: the write was buffered and the **flush** failed |
| stop **after** the child write, before the ack | 150/150, ok | **full** | **true** | **UNKNOWN** |

**The core result:** rows 2 and 5 give the driver an **identical** observation — hop 1 complete,
no ack, channel closed — with **opposite** ground truth, so UNKNOWN is irreducible from the
driver's own observations. Two consequences: an `EPIPE` on a **direct** stdin write does **not**
transfer to the relay socket (the dead-child signal can only be a shim-authored frame, whose
**absence** is UNKNOWN), and a relay has **two** short-write windows plus one buffered-write
trap.

> **A review finding, recorded.** That run's own prose labelled the successful hop-1 count
> `73/73`; the phase review (N-001) and the Final Adversarial Review (R1) both recorded that the
> raw row has `socket_write_bytes = payload_bytes = 150` for hop 1 and `child_write_bytes =
> payload_bytes = 73` for hop 2. **The raw values above are the correct ones** and the design uses
> them; the past artifacts were not retroactively edited.

---

## 2. Stage 2 — the real Orca link (2 model turns)

Executed by that run's **Coordinator**, not by its Worker (a dispatched Worker may not create an
Orca Task, Dispatch or terminal). Runbook and tooling: `20-runbook.md`, `20-launch.py`,
`20-launch.sh`, `20-u2-capture.py`, `20-u2-expected.txt`; Coordinator summary
`30-stage2-results.md`; raw `30-launch-log*.jsonl`, `30-cli-stdout*.jsonl`, `30-u2-capture.json`.
Every value below was re-derived from the raw files, and the raw file wins over any summary.

### S-0 — `terminal send` stdin byte-exactness (0 turns)

Sent `{"type":"user","message":{"role":"user","content":"U2-PROBE-PAYLOAD"}}` — **70 bytes**.
Received the same **70 bytes + one `\r` (0x0d)** = **71 bytes**, `tty = true`, verdict
`["cr_present", "exact_match"]`. Bytes were compared, not screen appearance: `orca terminal send
--enter` delivered the body byte-exactly on a TTY stdin with **no** bracketed paste and **no**
`ESC` rewriting — terminated by **CR, not LF**, so the **CR→LF conversion must be owned by the
supervisor**. The payload contained no raw `ESC`, so ESC handling on this path is unobserved.

### S-1a — direct PTY hosting of `claude -p` is NOT possible (0 turns, negative result)

With the launcher passing its **TTY** stdin straight through (`stdin_isatty = true`,
`stdin_mode = inherited`), `claude -p` exited `rc=1` with the single line `Error: Input must be
provided either through stdin or as a prompt argument when using --print`, emitting **no
`assistant` and no `result` event** ⇒ **0 turns** (`30-launch-log.1a-inherited-tty.jsonl`,
`30-cli-stdout.1a-inherited-tty.jsonl`). A separate argv-validation step
(`30-cli-stdout.0b.jsonl`, **0 bytes**; `child_exit rc=0`; `stdout_eof lines=0`) confirmed the
21-token argv is accepted by Claude Code 2.1.260 while spending **0** turns.

### S-1b — the supervisor relay shape works (1 turn)

| field | observed |
|---|---|
| launcher = the terminal's own process | pid **81932**, ppid **81876**, `stdin_isatty = true`, `stdin_mode = unframed_relay` — reads the PTY, writes the child's **pipe** stdin |
| CLI child | pid **81950**; **`child_exit` never logged** anywhere in the file |
| argv flags recorded (names only, no values; 21 tokens) | `-p`, `--model`, `--session-id`, `--input-format`, `--output-format`, `--verbose`, `--replay-user-messages`, `--safe-mode`, `--setting-sources`, `--no-session-persistence`, `--permission-prompts`, `--allowedTools` |
| forwarded to child stdin | **87** bytes at `t_wall = 1793456978.167851`; the `--replay-user-messages` echo returned the exact delivered content |
| CLI-reported model, **both** channels | `system/init.model` **and** `assistant.message.model` = `claude-haiku-4-5-20251001` — the fully-qualified id passed on argv |
| `request_id` / outcome / init | `req_011CgaUTtkKsnfQUqG3Mbhwq`; `result success`, `num_turns 1`, `is_error false`, **1199 ms**; one `session_id` on **all 23** events, equal to the argv `--session-id`; `apiKeySource = "none"`, `permissionMode = "default"` |

### S-2 — the experimental Task and Dispatch (0 turns)

Created and dispatched to that terminal **without `--inject`**: `capability_hash = null`,
`launch_token_hash = null`, `worker-show` → `state = unsupervised`, `stage = context_only`,
`depth = 1`. The first runbook order succeeded, so `--dry-run`, `--return-preamble` and
`--inject` were **never exercised** and remain **미검증**.

### S-3 — the task reached the SAME process, and that exact Dispatch settled (1 turn)

| field | observed |
|---|---|
| forwarded to child stdin | **494** bytes to child pid **81950** — the *same* pid as S-1b — at `t_wall = 1793457047.011852`, with **no `child_exit` between the two forwards** |
| tool use inside the dispatched process | `Bash` × 2: `orca --version` → `1.4.197`; then one `orca orchestration send --type worker_done … --outcome succeeded --from <its own terminal handle>` |
| the send's own result, in the CLI's stdout | `ok: true`, `from_handle` = that terminal, payload `{"taskId":…,"dispatchId":…,"outcome":"succeeded"}`, `lifecycle.action = "completed"` |
| `request_id`s / outcome | `req_011CgaUYxtkU1FM66wWGZGqo`, `req_011CgaUZAK6Wnwz7WGCbDNed`; `result success`, `num_turns 3`, `is_error false`, **3895 ms** |
| settlement, re-read from the live runtime | dispatch `status = completed`, `completed_at = 2026-10-31 14:30:49`, `failure_count = 0`; task `completed`, `result.provenance = worker_report`, `result.reportedBy = result.completedBy =` that same terminal, `result.completedAt = 2026-10-31T14:30:49.673Z` |
| **the finding that matters for the design's lifetime rules** | the dispatch row carries **`capability_revoked_at = 2026-10-31 14:30:49` — identical to `completed_at`. Orca itself revokes the dispatch capability at settlement.** |

**The Coordinator did not settle this on the process's behalf.** The only `worker_done` for that
Task/Dispatch came from the hosted process's own terminal, and the command that issued it appears
**in the CLI's own stdout stream**. The Coordinator's audit rows (`coordinator_audit/000003…6`)
are **accounting of** that settlement, not a substitute for it.

**What "same process" rests on** — four independent legs, because a `session_id` alone would not
be proof (it is argv-supplied): **(1)** pid continuity — child pid 81950 in `child_spawned`, in
every `relayed` row and in **both** `forwarded_to_child_stdin` rows, with **no** `child_exit`;
**(2)** one terminal, one launcher, one child; **(3)** the replay echo of the exact delivered
content per turn, with both byte counts (**87**, **494**) recomputed from the decoded content and
matching the launcher's record; **(4)** one `session_id` on all 23 events — corroboration, not
the load-bearing claim.

### S-4 — turn ↔ `request_id` correspondence, derived from `30-cli-stdout.jsonl`

Two input turns, 23 events, **7** `assistant` events, **3** distinct `request_id`s. This was
derived because "two input turns, three request_ids" does **not** imply one per turn.

| turn | input echo | `system/init` | `assistant` events | `request_id`s | `result` |
|---|---|---|---|---|---|
| 1 | event 3 (the 87-byte line) | event 1 | 4, 5 — a `thinking` block then a `text` block, **one** `message.id` | **1**: `req_011CgaUTtk…` | event 6, `success`, `num_turns 1` |
| 2 | event 10 (the 494-byte line) | event 7 | 11, 12, 14 (`thinking`, two `tool_use`), then 19, 20 (`thinking`, `text`) | **2**: `req_011CgaUYxt…` (11/12/14), `req_011CgaUZAK…` (19/20) | event 21, `success`, `num_turns 3` |

Established, and used by the design: **one `request_id` can span several `assistant` events**, and
**one input turn can span several `request_id`s** (a tool round-trip opens a new API request inside
the same input turn). `request_id` is on the **event's top level**, not on `message`. Also observed:
`rate_limit_event` at **two different positions** — once *before* the replayed `user` echo — and
three event shapes the earlier design did not name: `system/thinking_tokens`,
`system/commands_changed` (both **after** the final `result`), and `assistant` events whose only
content block is `thinking`. `modelUsage` carried exactly `['claude-haiku-4-5-20251001']` on both
turns.

---

## 3. Limits — what this evidence does NOT establish (미검증)

1. **§3a.12's channel has never been executed.** No socket was bound, no `hello`/`bind`/`bound`
   bootstrap ran, no launch lease was minted, no shim was written — by this run or any earlier
   one. S-1b/S-3 delivered through `terminal send` → the launcher's PTY → CR→LF → the child's
   pipe, a **different route**. The AF_UNIX channel, the lease and Gate B are **문서상 설계**.
2. **The accepted `worker_done` is prototype wiring, not production driver support.** The process
   was launched by a Coordinator-created terminal running a disposable launcher, dispatched
   **without `--inject`** and **without `worker-start`** (`state = unsupervised`), and **none** of
   `build_orca_adapter`, `_prepare_pair`, Gate A or Gate B took part. No Gate A/B evidence was
   produced or fabricated.
3. **U-3 was not attempted** — `worker-start` against a `-p`/shim terminal. X-4 therefore reports
   **both** possible receipts; neither is a verified Orca behaviour, and nothing in the design may
   be read as one. **U-4** (`--inject` / `--dry-run` / `--return-preamble`) and **U-5**
   (`terminal read` fidelity on a stream-json stdout) were not attempted either.
4. **The held-process window is measured only to 68.8 s** — the gap between the two
   `forwarded_to_child_stdin` rows (1793457047.011852 − 1793456978.167851 = **68.844001 s**).
   That is **shorter** than the design run's 90 s result, and far shorter than a realistic Worker
   turn.
5. **The API-served model is unverified.** The scope is the **CLI-reported** model;
   `apiKeySource = "none"` and two agreeing CLI channels are the limit. No local channel is a
   provider receipt.
6. **The launcher→child ppid linkage (81950 → 81932) is no longer re-verifiable.** It was read
   while both pids existed; both are now gone, so that leg is **추론 from the launcher log**. The
   other three legs of the same-process claim are re-derivable from the raw files and carry it.
7. **Every stage-1 result is a toy-model result** about the specification, not the product (§1).
8. **No company-environment (PortKey/GLM) observation**, and **in-session `/model` was not run**.

---

## 4. The experimental permission scope actually used

Recorded as an **observation about one probe**. It is **not** a product policy and is not proposed
as one; a product scope for a driver that must read, edit and test code is a separate pending
decision (design §3a.7 and §6 "Permission scope").

```
--safe-mode --setting-sources '' --no-session-persistence --permission-prompts none
--allowedTools "Bash(orca --version)" "Bash(orca orchestration send:*)"
```

**Sufficient** for both commands, with no refusal; the CLI reported `permissionMode = "default"`
and `apiKeySource = "none"`. **No** `--permission-mode` flag, **no** `bypassPermissions`, **no**
`--dangerously-skip-permissions`, **no** credential copying; personal authentication only. The
`worker_done` required **no** dispatch capability — `--from <handle>` alone was accepted, because
the Dispatch was created without `--inject`.

---

## 5. Experimental turn ledger — 2 of 4

An "experimental turn" is one input turn consumed by the **experimental** `claude -p` process.
The Worker/Reviewer development calls of that orchestration are **not** experimental turns.

| step | turns | how that is evidenced |
|---|---|---|
| stage 1 (X-1…X-5) | **0** | no CLI launched; all five probes are stdlib processes |
| S-0 byte capture | **0** | no CLI involved |
| argv validation | **0** | `30-cli-stdout.0b.jsonl` is **0 bytes**; `child_exit rc=0`, `stdout_eof lines=0` → no `assistant`, no `result` |
| S-1a direct PTY attempt | **0** | the CLI exited `rc=1` before consuming input; no `assistant`, no `result` |
| S-1b preflight | **1** | `result success`, `num_turns 1` |
| S-2 Task + Dispatch | **0** | Orca-side only |
| S-3 delivery into the same process | **1** | `result success`, `num_turns 3` — one input turn, three internal turns including two tool round-trips |
| retries / re-measurements | **0** | each turn-spending step succeeded on its first attempt (`is_error false` both times) |
| **total** | **2 of 4** | exactly **two** `result` events exist in `30-cli-stdout.jsonl`; **2 unspent** |

---

## 6. Hygiene of the underlying artifacts

- **Secrets:** clean. The only pattern match in the run tree is in `00-commands-run.md`, where
  the scan pattern **itself** is documented. No token, dispatch-capability value or auth value in
  any artifact.
- **Absolute home paths: closed.** The Worker's scan found the real home path in
  `system/init.cwd` on the two `init` events of the Coordinator-written `30-cli-stdout.jsonl`;
  because that file was contractually read-only for the Worker it was **flagged rather than
  silently edited**, and that run's Coordinator then redacted the user segment in those two lines
  (before/after sha256 digests recorded in `30-stage2-results.md`). No other raw file carried one.
  **The Worker report's "still open" wording is stale; this is the current state** (phase review
  N-002, Final Adversarial Review R2).
- **Probe processes, sockets, temp dirs:** every probe observed a self-exit or killed its process
  inside its own watchdog and `wait()`ed it; work directories were under the OS temp dir only —
  never in the repository or a worktree — and each runner removed its own. The two watchdog-killed
  X-1 shims left their endpoints, which **is** the F-005 observation (`11-f005-cleanup.txt` names
  them). One orphan shim was found, recorded and cleaned; post-state verified: 0 stray probe
  processes, 0 temp residue.
- **Git:** `HEAD` unchanged throughout; the only tracked modification was the pre-existing
  uncommitted design document; no `checkout`/`reset`/`clean`/`stash`/`add`/`commit`/`push`, no PR,
  no Jira change.
- **Orca resources:** the experimental Task and Dispatch both **completed**; the three
  Coordinator-created terminals are closed; the two experimental pids no longer exist. Nothing was
  abandoned, cancelled or force-settled.
