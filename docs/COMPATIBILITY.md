# Compatibility and Verification Status

The repository version is read from [`VERSION`](../VERSION). This document distinguishes
supported deterministic tooling from runtime configurations that have only been verified
in a specific environment.

## Compatibility matrix

| Component | Supported or verified environment | Status |
| --- | --- | --- |
| `orca-worker-reviewer-loop` | Markdown Skill package; no Orca orchestration state required | Deterministic policy and fake-agent E2E verified |
| `orca-worker-reviewer-orchestration` | Orca-native Run/Task/Dispatch lifecycle | Deterministic policy and fake-agent E2E verified |
| Repository validator and tests | CPython 3.11, 3.12, and 3.13 | Supported by CI |
| Real Orca runtime with fake agents | Orca 1.4.184 and Orca 1.4.196 | **VERIFIED** as two independent point observations, compatibility-gated by the opt-in Step 4 integration suite |
| `claude-glm` Worker | Distinct PATH-resolved command in the tested company environment | **VERIFIED on Orca 1.4.178-rc.2** |
| `claude-gemma` Reviewer | Separate PATH-resolved command and session in the tested company environment | **VERIFIED on Orca 1.4.178-rc.2** |
| Real GLM/Gemma smoke | Isolated company fixture on Orca 1.4.178-rc.2 | **VERIFIED**; ANALYSIS, DESIGN, IMPLEMENTATION, BUGFIX, DESIGN → IMPLEMENTATION, and FAIL → correction → PASS |

Python 3.11 is the minimum supported version. The code may run on earlier versions,
but they are outside the tested support policy. The project uses only the Python
standard library.

## Release readiness

- Deterministic policy validation: **VERIFIED**
- Fake-agent E2E: **VERIFIED**
- Real Orca with fake agents: **VERIFIED on Orca 1.4.184 and, separately, on Orca 1.4.196**
- Real GLM/Gemma smoke test: **VERIFIED on Orca 1.4.178-rc.2 in the tested company environment**
- Stable production-ready release: **NOT YET CLAIMED**

Verified environments are point observations, not a continuous supported range:

- Orca 1.4.184: deterministic real-Orca integration with fake agents.
- Orca 1.4.196: deterministic real-Orca integration with fake agents (OS-41).
- Orca 1.4.178-rc.2: real `claude-glm` Worker and `claude-gemma` Reviewer smoke test.

Nothing about the two fake-agent observations is a range. `validate_orca_contract()`
tests **set membership** against `SUPPORTED_ORCA_APP_VERSIONS`, never an ordering
comparison, so 1.4.190 (between the two verified points) and 1.4.197 (just past the
newer one) are both refused, and adding an entry requires actually running the suite
on that runtime. A version that is listed still has to pass the guide-grammar check.

The Step 4 runtime harness remains deliberately compatibility-gated. The
Step 5 environment's version-matched `orchestration` and `orca-cli` grammar contained
the required contract and the real-agent scenarios passed, but that does not establish
support for every version between these two observations. The Skill itself reads the
installed version-matched guides and does not hard-code either version as universal
command grammar.

## Orca 1.4.196 point verification (OS-41)

Verified by running `python3 scripts/test_orca_runtime.py --orca-runtime` against an
installed Orca 1.4.196. This section records what 1.4.196 does **differently** from the
1.4.184 observation. Every item below was read from the live runtime; none is inferred
from a version number, and none of it is claimed for any version in between.

### Supervised `worker-start --terminal` no longer adopts a non-agent process

This is the substantive change. On 1.4.184 the repository's deterministic fake agent
was adopted as a supervised worker, and 250 supervised attempts are recorded in the
historical artifacts. On 1.4.196 `worker-start` runs a `dispatch_input` stage that
delivers the task preamble into the terminal as a bracketed paste and then waits for the
agent's own **acknowledgement** before promoting the Dispatch from `pending` to
`dispatched`. Only a genuine recognized agent session produces that acknowledgement. A
scripted process cannot, and this was tested rather than assumed: a fake that stays
alive, echoes the prompt and prints continuously for 30+ seconds still leaves the
Dispatch at `status: pending`, `worker.state: starting`, `worker.stage:
authority_attached`, and `worker-start` ends with `state: failed`, `failedStage:
dispatch_input`, `lastError: agent_prompt_stalled`. That outcome also marks the **Task**
`failed`, and only `ready` tasks can be dispatched, so there is no recovery and no
fallback from it. Orca exposes no way to register a custom agent command.

Consequences, all of them deliberate:

- The test-only shim is `scripts/fake_bin/fake-agent`, not `scripts/fake_bin/codex`. It
  no longer borrows a recognized agent's name, so 1.4.196 refuses it up front with
  `agent_unconfigured` — creating **no** Dispatch and leaving the Task `ready` — and the
  run takes the version-matched guide's documented tracked-Dispatch path
  (`orchestration dispatch` plus `terminal send`). That is rung 4 of the placement
  ladder, and it is the path the guide itself prescribes for a target that is not a
  recognized agent CLI.
- On 1.4.196 the fake-agent suite therefore exercises the **tracked** lifecycle path.

### NOT VERIFIED on Orca 1.4.196: supervised worker-resource adoption and reuse

Stated plainly, because a reader must not infer it from the passing suite:

**The supervised worker-resource adoption and reuse path is NOT VERIFIED on Orca
1.4.196.** It cannot be, with deterministic fake agents: 1.4.196's `dispatch_input`
acknowledgement stage is completable only by a genuine recognized agent session, and
this repository's runtime suite is required to use deterministic fakes and never a real
LLM worker. **Orca 1.4.184 remains the point observation for the supervised path**, and
the offline contract suite in `scripts/test_orca_runtime_contract.py` continues to cover
the supervised code paths deterministically.

Concretely, on 1.4.196 the reuse gate `reuse_eligible()` refuses every same-role
transition, naming `worker_state_not_reusable`, `release_state_missing`,
`ownership_not_transferable` and `terminal_effect_unrecorded` — the four conditions
whose evidence exists only for a supervised dispatch. Each refusal returns `None` and the
attempt opens a fresh terminal, so the scenario records **eight refused decisions and ten
terminals for ten dispatches**, not two
(`artifacts/orca-runtime/os41-final/scenario-k.json`). That refusal is correct and is
the gate's documented fail-closed behaviour; the gate was **not** widened to accept
tracked evidence. Scenario K therefore verifies **"reuse correctly refused (fail-closed)
on the tracked path"** on this runtime. It does **not** verify that session reuse works,
and it must not be described that way.

The 1.4.184 records above are unchanged; this section adds to them and edits none of
them.

### `worker-start` reports a non-ready launch with `ok: true`

`orca agent-context --json` states that the call "exits 0 only for ready". The JSON body
carries the real outcome in `state`, alongside `stage`, `failedStage`, `lastError`,
`effects`, `residualResources` and `mutation` — and it carries a `dispatchId` **even for
a failed start**, because the Dispatch row really was created. Reading that id and
recording a supervised worker is exactly "prompt delivery inferred from Task/Dispatch
existence", so the harness now admits a supervised attachment only for
`state == "ready"`, and refuses every other value with the whole launch diagnosis
attached. A result carrying no `state` key at all keeps its legacy reading, because that
is the 1.4.184 shape and 1.4.184 is still a point verification.

### A Dispatch is `pending` while its prompt is being delivered

An agent holding the injected preamble reads `dispatch.status: pending` until
`worker-start`'s composition finishes. `worker_done` sent against a pending Dispatch is
refused (`inactive_dispatch`), so `pending` is not a state an agent may act on — but
exiting on it is worse: it ends the agent process mid-start, and `worker-start` then
fails the whole composition with `dispatch_inactive`. The fake agent therefore **waits**
for `dispatched` under a bounded deadline, and still fails closed on a settled,
abandoned or unrecognized status.

### The runtime publishes its own unexpected-exit escalation

When a dispatched agent process ends without settling, 1.4.196 sends an `escalation`
("Agent exited unexpectedly (Agent process ended; this host cannot report why)") whose
payload carries `taskId`, `dispatchId`, `exitCode`, `exitCause` and `handle`, and whose
stored top-level `sender_pane_key` is **present and null**. 1.4.184 published nothing.

The harness treats that message as **evidence**, and the distinction that makes it safe
to do so is worth stating precisely, because getting it wrong once let a worker-authored
message through:

- **Not authorship evidence:** the message type, the subject wording, the `high`
  priority, and every payload field — `taskId`, `dispatchId`, `handle`, `exitCode`,
  `exitCause`. `orca orchestration send` accepts `--type`, `--subject`, `--priority` and
  arbitrary `--payload`, so a dispatched agent can reproduce all of them. They are
  checked as **identity and shape validation, and as defence in depth**: they bind the
  message to the exact Task, Dispatch and terminal under observation and reject a
  malformed report.
- **The sole observed authorship discriminator:** `sender_pane_key`, a **top-level
  stored field on the message record** rather than a payload member, which no
  `orchestration send` flag can reach. It is required **present and null**; an absent
  key is rejected rather than read as the runtime's null, and a non-null value is a
  worker-authored message. Four attempts to null it from a dispatched terminal (a plain
  send, `env -u ORCA_PANE_KEY`, `setsid`, and `nohup … & disown` / a no-tty subshell)
  all produced a non-null pane key, so Orca resolves the sending pane from process
  ancestry rather than from the agent's environment.

Scope, and the direction it fails in: the authorship claim is proven for a dispatched
agent sending through `orchestration send`, which is the channel this threat model
covers; it is not a claim that no process anywhere can produce a null pane key. If a
genuine runtime report ever arrives without the key or with a non-null value, the
harness refuses it and the observation fails loudly rather than being downgraded to an
acceptance.

Any other message in that delivery, a `worker_done` above all, is still a contract
violation, because the claim under test is that the dispatch produced no lifecycle
result of its own.

### A late dependent Task is `ready`, not `pending`

`task-create --deps [<already-completed task>]` reports `ready` on 1.4.196 and reported
`pending` on 1.4.184. This is a satisfied-dependency answer and not a lost edge: on the
same runtime a dependent whose dependency is still open is `pending`. The scenario that
covers this asserts the invariant it actually exists for — the coordinator never
dispatches such a Task — and pins the status to a two-value allowlist so an
unrecognized third value still fails closed.

### One defect this uncovered was ours, not Orca's

The OS-29 decision-gate cursor (`_last_settled`) was harness-scoped but is
semantically **Run**-scoped, so a second Run started on the same harness inherited the
previous Run's settled round and had its own legitimate first boundary refused as
`DECISION_GATE_INPUT_UNBOUND`. The multi-Run sequence is exercised only by this opt-in
suite, which had been skipping since the version pin — so the defect shipped unexecuted.
It is reset in `start_run()` beside the other per-Run resets.

## Real-agent lifecycle observation

The Step 5 injected Claude workers sent valid `worker_done` messages and their Tasks and
Dispatches became completed. On Orca 1.4.178-rc.2, the completed Dispatch then became
unaddressable to `worker-show` and `worker-release` (`dispatch_not_found`), while its
terminal remained idle. This differs from the 1.4.184 fake-agent harness path, where the
coordinator can release the settled worker before acknowledging its Delivery.

Two further observations refine that report's interpretation. First, an accepted
`worker_done` settles the Dispatch regardless of how the worker was started: a low-level,
tracked Dispatch is not exempt from auto-settlement. Second, `dispatch_not_found` is
returned both before and after settlement, so it is evidence about the supervised
worker-resource registry and not about settlement in either direction.

The lifecycle invariant is therefore expressed as four independent axes, each with its
own recorded outcome:

1. **(a) Settlement** — accepted completion read from Task/Dispatch provenance. Do not
   repeatedly release a Dispatch the runtime has already settled.
2. **(b) Supervised worker-resource registration** — `reuse`, `retain`, `release`, or
   `unsupervised` when the dispatch was never registered as a supervised worker resource.
3. **(c1) Residual process liveness** — checked always, from terminal inspection, and
   answering only whether a process is still alive.
4. **(c2) Cleanup authority** — `authorized` only when a close-eligible terminal role and
   proven ownership both hold. Liveness never grants it, and self-creation alone never
   grants it. Anything else is retain-and-report.

Splitting (c1) from (c2) is what keeps a live terminal from being read as permission to
close it, and the terminal role gate is what keeps the coordinator's own session, setup
tabs, and adopted terminals permanently out of the close path. Residual terminals are
still cleaned up only through the installed version-matched guides and runtime receipts;
arbitrary process kills or undocumented cleanup remain unacceptable. Detailed evidence is
in the dated
[`GLM/Gemma smoke report`](validation/historical/GLM_GEMMA_SMOKE_REPORT_2026-08-20.md).
Use the separate
[`GLM/Gemma smoke procedure`](validation/GLM_GEMMA_SMOKE_PROCEDURE.md) for a new
point verification; do not rewrite the historical report.

## Agent Profile

Agent Profile is an optional abstraction and requires no Orca version beyond what the two
skills already need. It changes which agent command each phase runs, not how Orca is
driven: no new orchestration or CLI verb is used, no argument is added to an agent launch,
and the Run/Task/Dispatch lifecycle is unchanged.

An invocation without `profile=` behaves exactly as it did before the feature existed —
the profile files are not read, so a malformed or unreadable `~/.orca/agent-profiles.yaml`
cannot affect a run that does not ask for a profile.

Profile files are plain data read with the repository's own restricted-subset YAML reader;
no third-party dependency is introduced.

## Final Review audit records and evaluation tooling

The per-dispatch Final Adversarial Review audit records
(`artifacts/runs/<run-id>/final_review_audit/`), the evidence-bundle export, the evaluation
fixture and the scorer require no Orca version beyond what the two skills already need, and
introduce no third-party dependency: standard library only, CPython 3.11+.

**Additive by construction.** They add a directory under an existing artifact root, new
`--event` values in a vocabulary that was already open (no `ORCHESTRATOR_LOG.md` column is
added, so every file on disk keeps its width), new functions and subcommands in the shared
`run_logging.py`, and a new repository-side `scripts/final_review_eval.py`. No existing
column, path, schema or function signature changes. `RESULT:` stays two-valued and
`REVIEW_VERDICT:` stays four-valued — both are copied verbatim into a record, never
re-derived or collapsed.

**No migration, and none is possible to need.** No existing artifact changes meaning, so no
existing consumer can misread one. A run that completed before these records existed simply
has no `final_review_audit/` directory, and every reader treats an absent record as
`unknown` — the correct reading for a run that never wrote one. Historical runs are not
backfilled, deliberately: a backfilled record would carry a `recorded_at` that is not the
settlement time and a report snapshot taken long after any overwrite, which is precisely the
stale self-referential provenance these records exist to prevent.

**Capture degrades, it does not fail.** The two capture sources are post-dispatch `orca`
CLI reads. If `orca` is absent from `PATH`, exits non-zero, times out or returns
unparseable JSON, the record is still written with `capture_status: unavailable` and a
non-empty `capture_error` — a record that says why the input could not be captured is
evidence; a missing record is not.

**Redaction policy `redaction/1.1` covers POSIX paths only.** The policy has five ordered
categories: Orca dispatch capability, URL credential, secret-named environment assignment,
home-rooted absolute path (the user-name segment is replaced, the rest stays readable), and
— added in 1.1 — every other absolute POSIX path, replaced whole with no minimum segment
count, so an unanticipated shape fails closed rather than being left unchanged. Windows
`C:\Users\<name>` is deliberately not a category: this document does not claim Windows
support for the runtime path, and an untested pattern is worse than a stated gap. Adding it
is a MINOR policy bump. The same policy governs the exported evidence bundle, including the
copy of `ORCHESTRATOR_LOG.md` embedded in it; text that is not residue-free under the policy
is omitted from the bundle with a stated reason and a digest rather than embedded. The
authoritative local log is never rewritten.

**Packaging, and what the baseline capture guarantees.** `scripts/` is included in the release
archive, so a downloaded tarball contains
`scripts/fixtures/final_review_eval/key/answer_key.json`. That is unchanged and is stated rather
than worked around: the key must ship for a downstream user to score anything.

What changed is the claim, which is now about the *execution environment* rather than only about
the retained input. A §7 baseline capture dispatches the Final Reviewer under an enforced
filesystem scope (`scripts/review_isolation.py`, `sandbox-exec` on darwin): its working directory
is an ephemeral session containing only the materialized subject and a closed list of review-policy
files, every path it can read has been exhaustively scanned for key material, and the key-bearing
roots — the repository checkout, its `.git`, and any release archive found by the scan — are denied
for both content and metadata, so the key cannot be read *or discovered*. A negative test with a
positive control proves this per capture and its result is recorded in
`artifacts/runs/<run>/FINAL_REVIEW_ISOLATION.json`.

Two boundaries, stated rather than implied. First, the guarantee is scoped to a capture whose
attestation says `scope_enforcement: seatbelt`; a capture on a host without an enforcement backend
records `scope_enforcement: unenforced`, fails the baseline's B6 criterion, and may not be called a
baseline. Second, the threat model is an unconstrained but well-behaved reviewer agent — one that
reads absolute paths, runs `git` and greps broadly — not an adversary that escapes a kernel
sandbox. Ordinary (non-baseline) Final Review dispatches are unaffected and are not claimed to be
isolated; for those, the older and narrower claim still holds and is still verified per run: no key
material appears in the reviewer's retained input.

## Stable release blockers

- **License decision:** the owner must select and add a license as documented in
  [`LICENSE-DECISION.md`](LICENSE-DECISION.md).

The lifecycle discrepancy is no longer a documentation blocker after the policy
clarification above. The strict Step 4 compatibility gate remains an intentional test-scope
constraint, not proof of a broader Orca version range. A 1.0.0 release is not declared by
this document update and still requires an explicit final release decision.
### OS-30 compatibility

### OS-40 deterministic engine compatibility

The engine is verified on Python 3.11 and the exact optional versions in
`requirements-langgraph.txt`. Existing standard-library validation remains usable without
LangGraph; invoking the graph itself fails explicitly instead of falling back. The installed
orchestration Skill carries a byte-equal engine copy and launcher. Durable checkpointers and
cross-session resume are not claimed.

New clarification requests and responses use schema generation v2; homogeneous historical v1 single-item artifacts remain immutable and are never migrated or rewritten.

OS-30 adds a separate `clarifications/` namespace and does not widen or migrate the OS-28/OS-29 decision ledger. Historical blocked runs without that directory remain valid historical evidence. The installed orchestration tool uses only Python 3.11+ standard-library APIs and its adjacent shipped `run_logging.py`; the loop Skill documents the semantics but does not expose the artifact runtime.
