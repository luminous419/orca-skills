# Compatibility and Verification Status

## OS-42 — validation repair: two schema versions move (breaking for in-flight runs)

`SCHEMA_VERSION` moves `os40.workflow.v1` -> `os40.workflow.v2` and
`ACTION_SCHEMA_VERSION` moves `os40.action.v1` -> `os40.action.v2`. `EVENT_SCHEMA_VERSION`
and `CHECKPOINT_STORE_SCHEMA_VERSION` do **not** move: `SettlementEvent`'s key set is
unchanged (the new gate data lives inside `result`, which was always an open dict) and the
checkpoint store's own document format is unchanged.

**Drain in-flight runs before upgrading.** A checkpoint written by the previous build
carries `os40.workflow.v1`; `validate_state` refuses it with `MALFORMED_STATE:schema` and
`validate_node` routes the run to a BLOCKED terminal. The same holds for an in-flight
`pending_intent` written under `os40.action.v1`. That is the fail-closed direction and it
is deliberate: a run interrupted across the upgrade must be RESTARTED, not resumed.

No migration shim is provided, and that is a decision rather than an omission. A shim
would have to invent `repair_attempt`, `gate_iteration`, `artifact_contract_path` and
`repair_instruction` for an intent created without them, and inventing an artifact path
for an in-flight dispatch is precisely the class of error OS-42 exists to remove.

`decision_gate.LEDGER_RECORD_SCHEMA_VERSION` stays `1` and
`decision_policy.SUPPORTED_SCHEMA_VERSIONS` stays `(1,)`: no field is added to a decision
ledger record and the `decision_policy` block is unchanged, so historical records stay
readable and `artifacts/runs/**` is neither read nor written by this change.


The repository version is read from [`VERSION`](../VERSION). This document distinguishes
supported deterministic tooling from runtime configurations that have only been verified
in a specific environment.

## Compatibility matrix

| Component | Supported or verified environment | Status |
| --- | --- | --- |
| `orca-worker-reviewer-loop` | Markdown Skill package; no Orca orchestration state required | Deterministic policy and fake-agent E2E verified |
| `orca-worker-reviewer-orchestration` | Orca-native Run/Task/Dispatch lifecycle | Deterministic policy and fake-agent E2E verified |
| Repository validator and tests | CPython 3.11, 3.12, and 3.13 | Supported by CI |
| Real Orca runtime with fake agents | Orca 1.4.196 | **VERIFIED for the current head** as a single point observation, compatibility-gated by the opt-in Step 4 integration suite. Orca 1.4.184 is a **historical** observation of an earlier revision — see "Verified for this head vs. historical" below |
| `claude-glm` Worker | Distinct PATH-resolved command in the tested company environment | **VERIFIED on Orca 1.4.178-rc.2** — *historical*, an observation of the revision current at that time |
| `claude-gemma` Reviewer | Separate PATH-resolved command and session in the tested company environment | **VERIFIED on Orca 1.4.178-rc.2** — *historical*, an observation of the revision current at that time |
| Real GLM/Gemma smoke | Isolated company fixture on Orca 1.4.178-rc.2 | **VERIFIED** — *historical*; ANALYSIS, DESIGN, IMPLEMENTATION, BUGFIX, DESIGN → IMPLEMENTATION, and FAIL → correction → PASS |

Python 3.11 is the minimum supported version. The code may run on earlier versions,
but they are outside the tested support policy. The project uses only the Python
standard library.

## Release readiness

- Deterministic policy validation: **VERIFIED**
- Fake-agent E2E: **VERIFIED**
- Real Orca with fake agents: **VERIFIED for the current head on Orca 1.4.196**
- Real GLM/Gemma smoke test: **VERIFIED on Orca 1.4.178-rc.2 in the tested company environment (historical: an earlier revision)**
- Stable production-ready release: **NOT YET CLAIMED**

## Verified for this head vs. historical observations

Read this section before quoting any version number from this document.

Every observation below is a **point observation**, never a continuous supported range,
and an observation is bound to the **repository revision that produced it**. A run that
passed against an older revision of this repository is evidence about that revision. It
is not evidence that the current head still passes, and it is therefore not a support
claim.

**Verified for the current head** (this is what `SUPPORTED_ORCA_APP_VERSIONS` contains,
and it is what the Step 4 gate accepts):

| Orca version | What was run | Evidence |
| --- | --- | --- |
| 1.4.196 | Deterministic real-Orca integration with fake agents, opt-in Step 4 suite: all six runtime scenarios executed, `skipped=0` (OS-41) | `artifacts/orca-runtime/` runs recorded on this branch |

**Historical point observations** (preserved, still true of the revision that produced
them, and **not** current support). These are recorded in code as
`HISTORICAL_ORCA_APP_VERSION_OBSERVATIONS`, a tuple that **gates nothing**: it grants no
runtime, is never consulted by `validate_orca_contract()`, and every version in it is
refused exactly like any other unverified version.

| Orca version | What was observed | On which revision |
| --- | --- | --- |
| 1.4.184 | Deterministic real-Orca integration with fake agents, including the **supervised** fake-agent adoption path and therefore granted session reuse | Pre-OS-41 harness, before this revision changed worker-start admission, the fake-agent shim name and path, unexpected-exit classification, scenario K, packaging and the run-scoped decision cursor |
| 1.4.178-rc.2 | Real `claude-glm` Worker and `claude-gemma` Reviewer smoke test in the company fixture | The revision current at that time |

Moving a historical entry back into the supported set requires running **the head that
makes the claim** against that runtime and recording the evidence. Until then the
historical records stay exactly as they are: `docs/validation/historical/` and the
existing run artifacts are unmodified, and this document adds to them rather than
rewriting them.

`validate_orca_contract()` tests **set membership** against
`SUPPORTED_ORCA_APP_VERSIONS`, never an ordering comparison, so 1.4.190 and 1.4.197 are
refused, 1.4.184 is refused, and adding an entry requires actually running the suite on
that runtime. A version that is listed still has to pass the guide-grammar check.
**No version range is claimed anywhere in this repository.**

The Step 4 runtime harness remains deliberately compatibility-gated. The
Step 5 environment's version-matched `orchestration` and `orca-cli` grammar contained
the required contract and the real-agent scenarios passed, but that does not establish
support for any other version, nor for any version between these observations. The
Skill itself reads the installed version-matched guides and does not hard-code any
version as universal command grammar.

## Orca 1.4.196 point verification (OS-41)

Verified by running `python3 scripts/test_orca_runtime.py --orca-runtime` against an
installed Orca 1.4.196. **1.4.196 is the only runtime the current head is verified on.**
This section records what 1.4.196 does **differently** from the historical 1.4.184
observation; the comparison is a description of two runtimes, not a support claim for
the older one. Every item below was read from the live runtime; none is inferred from a
version number, and none of it is claimed for any version in between.

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
LLM worker. **Orca 1.4.184 remains the HISTORICAL observation of the supervised path,
made against an earlier revision of this repository and not a claim about the current
head**, and the offline contract suite in `scripts/test_orca_runtime_contract.py`
continues to cover the supervised code paths deterministically on every revision.

Concretely, on 1.4.196 the reuse gate `reuse_eligible()` refuses every same-role
transition, naming `worker_state_not_reusable`, `release_state_missing`,
`ownership_not_transferable` and `terminal_effect_unrecorded` — the four conditions
whose evidence exists only for a supervised dispatch. Each refusal returns `None` and the
attempt opens a fresh terminal, so the scenario records **eight refused decisions and ten
terminals for ten dispatches**, not two
(`artifacts/orca-runtime/os41-final/scenario-k.json`). Scenario K's assertion **requires
exactly that** on this runtime and fails if any reuse is granted: it is keyed on the
point-verified runtime identity the harness recorded, it requires all eight decisions
refused with exactly those four reason names, and it requires ten distinct terminal
creations. The 1.4.184 supervised expectation — granted reuse, two terminals — is kept
separately as a historical record and is not an executable branch of that test. That refusal is correct and is
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
attached. A result carrying no `state` key at all keeps its legacy reading, because
that is the 1.4.184 shape. That allowance is pinned to the exact `1.4.184` identity and
is now **unreachable from a live run**: 1.4.184 is a historical observation and is
refused by `validate_orca_contract()` before `start_worker()` can be reached, so the
reading survives only as offline contract coverage. It is kept rather than deleted so a
future revision that re-verifies 1.4.184 does not have to re-derive it; keeping it makes
the live gate no less strict.

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

**The policy version and the coverage set are different axes.** `redaction/MAJOR.MINOR`
versions the five ordered categories above — the text transformation — and nothing else;
`redact_text()` is a pure function of (text, policy_version) that admits exactly one
version, because a digest is comparable only to a digest produced under the same policy.
WHICH record fields pass through it is versioned by `FINAL_REVIEW_AUDIT_SCHEMA_VERSION`
instead, and is additionally recorded verbatim in every record as
`metadata_redaction.covered_fields`, so a reader never deduces coverage from the version
string. OS-49 added five reviewer-model fields to the coverage set: that is the audit
schema's MINOR bump (1.0 → 1.1), not a policy bump, and the policy correctly stayed at
`redaction/1.1` — bumping it for a coverage change would make every historical
`*_digest_post_redaction` unverifiable through the single-version function, which is the
same reason a MAJOR audit-schema bump is forbidden.

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

## OS-49 — model-aware agent routing: `version: 2` is additive, and a declared model is refused

OS-49 makes an agent's **effective identity** `(command, model)` rather than the command
alone. The three historical GLM/Gemma rows in the compatibility matrix above are **not**
restated, re-scoped or re-dated by this section; it appends only, and it claims **no**
company-environment validation of any kind.

### Agent Profile schema versions

| version | role value | status |
| --- | --- | --- |
| 1 | a command **string**; a model is not representable at all | **FROZEN.** This version's meaning will not change, and every existing v1 document parses, routes and logs exactly as before |
| 2 | a command string **or** a `{command, model}` indented **block mapping** | the model-aware schema |

`SUPPORTED_SCHEMA_VERSIONS` is `(1, 2)` and the shared policy contract's
`agent_profile.schema_versions` is `[1, 2]` in **both** skills. A plain string role value in
a v2 document means exactly what it means in v1. Mixed forms in one document are legal. A
model may be written in exactly five positions — `defaults.worker`, `defaults.reviewer`,
`phases.<phase>.worker`, `phases.<phase>.reviewer`, `final_review.reviewer` — and nowhere
else.

The inline spelling `worker: {command: claude, model: glm-5.2}` does **not** work: the
restricted YAML reader supports flow sequences but not flow mappings, so the indented block
is the only valid form and the inline one is refused by name.

**Reading a newer document with an older installed Skill** fails closed by VERSION
(`unsupported schema version 2; supported: 1`) rather than by complaining about the role
value's type. That is deliberate: the message names the cause and the remedy, and the model
declaration stays in the file instead of being deleted — a deleted declaration would produce
a silently model-less run, which is the outcome this feature exists to prevent.

### The one v1 configuration value that does not survive

A `version: 1` profile that put the **same command** on both sides of a MEDIUM/HIGH-risk
gate is now refused with `WORKER_REVIEWER_MUST_DIFFER`, before any Run, Task, Dispatch or
terminal exists. The independence rule is **categorical** — it applies at v1 exactly as at
v2, because a model-selecting executable makes a command string insufficient evidence of
identity regardless of which schema version declared it.

Two separate sessions on one command remain a real invariant, owned by the session reuse
gate's role condition, but they are **no longer sufficient**. `.orca/agent-profiles.example.yaml`
was migrated in the same change, and its previous comment asserting the opposite was
replaced.

Unaffected: **distinct model-pinned wrapper commands.** A profile routing `claude-opus`
against `codex-sol` declares no model, carries evidence state `none` everywhere, and routes
exactly as before.

### Model selection is fail-closed, and the real runtime is UNSUPPORTED here

Declaring **no** model preserves pre-OS-49 behaviour byte-for-byte: no selection is
requested, no driver is consulted, no new refusal exists, and the logs are identical.

Declaring a model commits the run to this ordered lifecycle, per dispatch:

```text
create/attach session -> REQUEST model selection -> positively VERIFY the resolved model
-> deliver the task -> record provenance
```

A Worker or Reviewer task is **never** delivered before the requested model has been
positively verified **for that attempt**. Reading whatever model a session happens to
already be on is not evidence: a selection must have been *requested*, and the resolution
observed *afterwards*.

| failure | means |
| --- | --- |
| `AGENT_MODEL_NOT_SUPPORTED` | the model is well-formed but nothing in this run can select it and positively observe the result |
| `INVALID_AGENT_MODEL` | the model value is not a simple model token |
| `model_selection_unsupported` | a selection cannot be requested at all — no driver, or no supported request method |
| `model_selection_request_absent` | no selection was requested for this attempt |
| `model_selection_request_stale` | the request belongs to another attempt, or the two legs were not drawn in order |
| `model_selection_unverified` | requested, never positively observed |
| `model_selection_mismatch` | requested ≠ resolved |
| `model_selection_ambiguous` | contradictory resolved values for one identity |
| `model_selection_pair_unadmitted` | a same-command counterpart holds no positively verified model, so independence is not established and nothing may be delivered for either role |

**In this release a declared model is REFUSED on every real placement**, with
`AGENT_MODEL_NOT_SUPPORTED` at profile-validation time. Three independent mechanisms produce
that refusal, any one of them sufficient: the capability parameter defaults to empty and
neither production door passes one; the Orca adapter declares no model capability; and no
real adapter can name a member of the closed request-method vocabulary. This is a designed
fail-closed state, not a gap — Claude Code's in-band model-selection syntax, its
acknowledgement format and its output semantics have not been observed in this environment,
and Orca's `worker-start --model` is unreachable from this runtime. Nothing here parses a
model-selection acknowledgement, and no module composes such a command.

Model-aware routing is positively supported **only** on the deterministic driver seam, where
the request leg is honest: the reference driver records a requested model against the
session and reads that state back, composing no command and parsing no output.

**OS-14 — the normal workflow now performs the preparation itself, and it is DURABLY
recorded.** Before OS-14 the public pre-pass that admits a same-command pair had no caller
under `scripts/deterministic_workflow/`: only tests created both sessions and pre-admitted
the pair. The Orca-adapter workflow path now prepares both sessions, verifies both model
identities through the injected driver seam, and only then delivers the first task — and a
preparation or verification failure delivers **nothing**, for either role. The preparation's
provenance is durable, so re-entry and restart either continue with **no duplicate effect**
or stop with a named `PAIR_PREPARATION_*` reason. None of this changes the fail-closed
default above: with no driver a declared model is still refused `AGENT_MODEL_NOT_SUPPORTED`
at the declaration gate, before any Run exists, and the reference driver's success remains
evidence about the **wiring** only — never about any real provider model.

**Every Orca-adapter run now carries a run-root launch record**
(`.pair_launch_binding.json`), written at run OPEN, model-less runs included, with
`model_aware` recorded as the string `"true"` or `"false"`. The file's EXISTENCE is not the
signal — its content is — so an absent record can never be mistaken for a legacy run. A run
opened by a **pre-feature** engine therefore has none, and adopting it stops by name
(`PAIR_PREPARATION_LAUNCH_RECORD_ABSENT`); the operator relaunches it, and nothing was
prepared under it, so no prepared session is orphaned. An adoption never WRITES a launch
record, so it cannot manufacture the authority it is supposed to be checked against. A
relaunch of the **same run id** under a **different** routing or launch kind is refused at
the launch boundary as `ORCA_RUN_BINDING_CONFLICT`, before any Task exists.

**The launch identity a successor is reconciled against includes the DRIVER CLASS.** The
record binds the driver's layout-normalised, round-trip-verified import path
(`"<module>:<qualname>"`), not its short `__name__`, which two different classes can share.
A successor that reconstructs the same routing digest but injects a *different* capable
driver class — including one whose `__name__` is identical — is refused
`PAIR_PREPARATION_BINDING_MISMATCH` before any effect. A driver whose class has **no durable
name** (a function-local class, a `type("X", …)` built at run time) cannot open a model-aware
run at all: `AGENT_MODEL_DRIVER_UNIDENTIFIABLE`, at the routing gate, before a harness, Run,
Task, Dispatch or terminal exists. A driver's **injected behaviour** is explicitly NOT
bindable — a live callable has no stable durable identity — and is covered instead by
mandatory fresh re-verification on every pass plus the cross-process drift block
(`PAIR_PREPARATION_MODEL_DRIFT`).

**Automatic resume is not guaranteed at every crash window, and a named block is the
designed outcome** wherever creation, ownership, configuration or verification state is
unknown. A `VERIFIED` record is **never** restored as a current verification: authority lives
only in process memory, `resume_run` restores none of it, and every preparation pass re-runs
the driver's two legs for both roles. A model-aware adoption also reconstructs its routing
from the run's **own committed declaration** — its requested phases and its risk, from one
read — and refuses `ORCA_RUN_DECLARATION_UNREADABLE` rather than guessing; a **legacy**
adoption never performs that read and behaves exactly as it does today. One pre-OS-14
behaviour is deliberately replaced: a **resumed model-aware** run used to deliver
**unverified**, because the shipped recovery constructor reconstructed neither routing nor
driver; it now stops `PAIR_PREPARATION_BINDING_UNVERIFIABLE` before any Task, terminal or
Dispatch.

### Worker/Reviewer independence is admitted as a PAIR, before the first delivery

`requested` is a **declaration**, not an observation, so two distinct declared models on one
command are **not** evidence of two agents — two declared tokens can alias onto one resolved
model. Declaration-time classification therefore has **three** outcomes, not two:

| outcome | when | effect |
| --- | --- | --- |
| `independent` | the commands differ, **or** both models are positively resolved and differ | routes |
| `refused` | same command with the same declared model, one side declaring no model, or a placement that cannot select or observe a model | refused before any Run, Task, Dispatch or terminal exists |
| `pending_model_verification` | same command, two **distinct declared** models | an **obligation**, not a pass |

A `pending` pair carries an obligation the declaration gate structurally cannot discharge,
and a per-role barrier cannot discharge it either: a Worker dispatch precedes its Reviewer
session, so at the Worker's barrier there is no counterpart to compare against. The pair is
therefore admitted **as a pair**, before the first delivery of **either** role:

```text
create/attach BOTH sessions
  -> request + positively verify the worker's model
  -> request + positively verify the reviewer's model      == pair admission
  -> deliver the first task                                (either role)
```

Attempting to deliver either role while its same-command counterpart holds no positively
verified model is refused with `model_selection_pair_unadmitted`, and no `worker-start`, no
`dispatch` and no `terminal send` has run at that point. A path that cannot bring both
sessions up before the first delivery therefore **cannot route a same-command model-aware
pair at all** — a fail-closed outcome, not a gap.

`AGENT_PROFILE_MODEL_GATES` names three gates (`declaration_before_run`,
`verification_before_delivery`, `pair_admission_before_first_delivery`) and
`AGENT_PROFILE_MODEL_LIFECYCLE` names five ordered steps (`attach_both_sessions`,
`request_selection`, `verify_resolved`, `admit_pair`, `deliver_task`). The per-dispatch
lifecycle stated earlier in this section remains exactly true for every role; this
subsection adds the pair step that a same-command pair additionally requires. Both contract
values are exact ordered equalities checked by `scripts/validate_skills.py` and locked by
`scripts/test_os49_contract_locks.py`.

**Unaffected: distinct-command pairs.** Independence holds on the commands alone, so no
counterpart evidence is required and the delivery lifecycle is byte-identical to before
OS-49 — which is what keeps this repository's own `claude-opus` / `codex-sol` wrappers, and
every **distinct-command** document of either schema version, routing unchanged. A
model-less run has no model axis and therefore no obligation.

**Affected, and stated rather than implied: a same-command `version: 1` pair is now
rejected.** Schema v1 **parsing** is unchanged and stays frozen — a v1 role value is a
command string and a model cannot be expressed there — but the effective-identity rule is
**categorical**, so it applies at every schema version. A v1 profile that puts the same
command on both sides of a MEDIUM/HIGH phase pair (`worker: claude` + `reviewer: claude`)
is refused `WORKER_REVIEWER_MUST_DIFFER` at Gate A, before any Run exists, because same
command plus no distinguishing model is not two agents. That is a behaviour change for such
a document, and it is the one OS-49 change that is not purely additive on the v1 path. It is
not new policy so much as the old `WORKER_REVIEWER_MUST_DIFFER` invariant now being reached
through the profile path too; an earlier claim in this section that the lifecycle is
"byte-identical" and that "every v1 document" routes unchanged was true only of the
distinct-command case, and the narrower claim above is the accurate one.

**Also unaffected: an OPTIONAL counterpart.** The requirement is scoped to a counterpart
that is **required** and resolved, exactly as the declaration gate's pair check is. At LOW
risk the Reviewer entry exists but is optional and no Reviewer is ever dispatched, so there
is no pair to admit and a LOW-risk model-aware Worker routes unchanged — the same rule the
PATH check follows: a role nobody dispatches must not fail a run. An unresolved *required*
role is still `AGENT_ROLE_UNRESOLVED`'s business, not this gate's.

**OS-14 — who performs the pair admission.** The lifecycle above is now executed by the
Orca-adapter workflow path itself, inside `OrcaAdapter.start` and strictly before the Task
is created: it prepares both sessions, verifies both identities, and refuses the whole
attempt if either half fails. The predicate that decides whether a delivery needs an
admitted pair is a single public derivation on the harness
(`OrcaRuntimeHarness.pair_admission_required`), read by the adapter and copied nowhere, and
the same-command condition it rests on is one private helper read by all three of its call
sites — so the declaration gate, the delivery barrier's backstop and the workflow path
cannot disagree about which pairs the rule covers.

**Reviewer SESSION preparation is distinct from Reviewer TASK execution.** The Reviewer's
session is brought up with the Worker's, before the first delivery, because that is what
pair admission requires. Its **Task**, **Dispatch** and `worker-start` appear only at the
Reviewer's own graph turn, after the Worker's result has been applied — no dependency is
bypassed and nothing is force-ready. A correction or re-review round is a different gate
round, prepares a **fresh** pair under its own key, and is admitted only on evidence
re-earned during that round.

**Unaffected: distinct-command and LOW-risk model-aware runs, and model-less runs.** None of
them has a pair to admit, so none of them prepares anything: the command sequence is
byte-identical to before OS-14, with the single run-root launch record described in the
fail-closed section as the only delta.

### Session reuse is model-bound

The session reuse gate has **nine** conditions; OS-49 appended `compatible_model_identity`
and changed none of the eight. It can only ever refuse a reuse that would otherwise have
been allowed, so no OS-37/OS-48 ownership, finality or provenance protection is weakened and
nothing previously refused becomes allowed. A chain with no model anywhere produces exactly
the pre-OS-49 decisions.

Reuse is refused when the previous dispatch's model differs from the next request, when the
previous identity cannot be positively verified, when no record shows the model was actually
*requested* on that dispatch, when the evidence belongs to another dispatch, when the row is
internally contradictory, or when no model-selection capability exists.

### Model provenance

`ORCHESTRATOR_LOG_COLUMNS` is **byte-unchanged**. Provenance is a new event NAME,
`agent_identity_bound`, emitted once per settled dispatch beside the settlement row — not a
new column, because every reader skips a row whose cell count differs and a new column would
make every historical row invisible. Its `result` cell carries exactly one model-evidence
state and its `detail` cell carries the command, the requested and resolved models, the
request method, the selection token and both ordinals, so *"a selection was requested for
this attempt before the resolved model was observed"* is reconstructible from the artifact
alone by arithmetic.

The Final Review audit record takes a **MINOR** bump, `1.0` → `1.1`, for five additive
reviewer-model fields. A MAJOR bump is forbidden: the reader checks only the MAJOR
component, so bumping it would make every historical `1.0` record read as `unknown_major`.
The standalone journal gains **no** field — its digest covers the record, so a schema break
there is not authorised.

Correction, re-review, downstream revalidation and the Final Adversarial Review all preserve
the materialized routing identity. A round that resolves to a different model for the same
`(phase, role)` is **refused** rather than logged, and every round re-runs the full
lifecycle: a round cannot inherit an earlier round's verification.

### The loop skill

`orca-worker-reviewer-loop` needs no new mechanism. The profile parser is shared, so a
`version: 2` model-bearing document parses identically there and is not a syntax error.
Because that runtime has no Dispatch, no reuse chain and no pre-delivery barrier, a declared
model is refused there with `AGENT_MODEL_NOT_SUPPORTED` — through the **same** code path as
the orchestration runtime, not a runtime branch. No reuse condition, no delivery barrier and
no agent-profile anchor block is added to the loop skill.

### Two corrections made during OS-49's own TEST phase

Both were found by the TEST phase, reproduced independently by the phase Reviewer, and
fixed in `orca_runtime_harness.py` inside OS-49. They are recorded here because each one
changes an observable behaviour relative to the state the IMPLEMENTATION phase left, and
neither relaxes anything.

**The Final Adversarial Review's model is routed, requested and verified on the spelling
production dispatches.** A Final Review attempt is dispatched as role `reviewer` in phase
`final_review` — the only spelling any production initiator produces. The role → routing
key mapping recognised the Final Reviewer by the ROLE STRING alone, so that attempt looked
up an entry that does not exist, the pre-delivery barrier returned at "no model declared",
and a declared `final_review.reviewer.model` was never requested, never verified and never
recorded while the task was delivered anyway. The phase is now part of the mapping:
`final_review` is a reviewer-only gate over a whole run and therefore has exactly one
routing slot, which either spelling of the role resolves to. The Final Reviewer is now
subject to the same request → verify → deliver barrier every phase role already was, and
stays **outside** the pair-admission rule exactly as documented above, because
`final_reviewer` has no counterpart role to look up. The audit record's five reviewer-model
fields are also written as one bundle from one source, so a record can no longer name a
`reviewer_requested_model` without the evidence that resolved it.

**Model evidence is run-scoped.** Accepted model evidence is per-run state exactly like the
terminal ledger and the delivery cursor: every record was accepted against one run's
six-part attempt key, and the record already names the run it was observed in. It is now
cleared at the run boundary alongside those collections, and the counterpart read that
GRANTS pair admission additionally requires the counterpart's `observed_at_run` to be the
active run. Without both, a second run on one harness instance inherited the first run's
counterpart evidence and delivered a same-command Worker whose Reviewer did not exist in
that run at all. This can refuse nothing a same-run pair did before — a same-run record's
`observed_at_run` is the active run by construction — and a cross-run record now falls to
`model_selection_pair_unadmitted`, which is the fail-closed outcome.

### What remains for OS-14

OS-14 is split into two tracks, and only one of them is implemented.

**TRACK A — implemented.** The common Worker/Reviewer pair preparation described above:
preparation, dual verification and pair admission performed by the normal Orca-adapter
workflow, with durable preparation provenance, re-entry/restart guarantees and named
fail-closed refusals. It is driven by the **reference** driver, which is evidence about the
wiring only.

**TRACK B — DESIGN ONLY, nothing implemented.** `docs/OS-14-claude-driver-design.md` is the
design for a real Claude Code model-selection driver: request semantics, evidence and
validity windows, execution lifetime, and the attachment target, together with the coupled
code boundaries and the decisions that remain open. **No real driver, no interactive
model-switch experiment and no company-environment work is implemented by it**, and a
declared model on a real placement stays refused exactly as the fail-closed section above
states.

The items below remain open for a real environment:

- company-environment verification of model-aware routing; **nothing here claims it**
- which of GLM-5.2 and GLM-5.3-flash should be Worker and which Reviewer
- the real shape of a launch receipt's effective-model field (unobserved; nothing depends on it)
- real in-band model-selection syntax, acknowledgement format and output semantics
- whether the company environment's `claude` is reachable as a recognized Orca agent id
- whether `claude-gemma` leaves `known_agent_commands` (unchanged by OS-49)
- the first adapter or driver that can **honestly declare both legs** in a real environment,
  which is the one thing that lifts OS-49's fail-closed refusal for a real run

## OS-14 correction run — session use state, durable model history, preparation-record loss

This section records the three independent-review findings corrected after PR #38's first
green run, the two schema versions that move with them, and the operator procedures the
new refusals need. Nothing here is evidence about any real Claude or company model: the
pair-preparation path is still driven by the **reference** driver, and TRACK B of OS-14 is
still design only.

### Two schema versions move (breaking for in-flight model-aware runs)

| Document | Was | Now |
| --- | --- | --- |
| `.pair_launch_binding.json` | `os14.pair_launch_binding.v1` | `os14.pair_launch_binding.v2` |
| `.pair_preparation.json` | `os14.pair_preparation.v1` | `os14.pair_preparation.v3` |

A document at the older version is refused as `PAIR_PREPARATION_RECORD_CORRUPT`
(`INCOMPATIBLE_DURABLE_STORE`) and is **never** read as "nothing was prepared", "a legacy
run" or "an unused session". That is the fail-closed direction and it is required rather
than incidental: the v1 entry shape carries no statement about whether a prepared session
was ever delivered to and no run-scoped model baseline, so reading a v1 document under the
new rules would answer "unused" and "no baseline" for facts it never recorded — which is
exactly the two defects being fixed.

**Drain in-flight model-aware runs before upgrading.** A run whose preparation document
was written by the previous build must be restarted, not resumed; see the transition
procedure below. No migration shim is provided, for the same reason OS-42 provides none: a
shim would have to invent a use state and a model baseline for sessions whose history was
never recorded.

`PAIR_LAUNCH_IDENTITY_KEYS` gains **no** cell. The new binding cell
`preparation_started_at` is provenance, excluded from the launch identity by construction,
so setting it can never make a run's own binding mismatch itself.

**A present v2 document's top-level section set is exact.** `.pair_preparation.json` has
exactly three sections — `pairs`, `sessions`, `role_history` — and the single writer emits
all three on every write. An **unknown** top-level key was already corruption; a
**missing** one now is too, refused as `PAIR_PREPARATION_RECORD_CORRUPT` before any
section is interpreted, and therefore before any session creation, Task creation or
delivery. The reason is the same fail-closed reason as the version bump: read permissively,
a removed `sessions` section answers "this session was never delivered to" and a removed
`role_history` section answers "this role has no baseline, so a first observation may
establish one" — the two statements a *missing* record must never make. An **absent file**
is a different state and keeps its own path: there is no document for a section to be
missing from, and whether that absence is a genuine first write or a lost document is
decided by the launch record's `preparation_started_at` marker (B3), not by the reader.

### Session use state (B1): what a prepared session's use record states

Session use is recorded in the `sessions` section of `.pair_preparation.json`, keyed by the
session's **terminal digest** — the only key that denotes one physical session across
processes — and it is **append-only**. A new `create_attempt` on a preparation entry
therefore cannot overwrite the past use state, or the refusal grounds, of the session it
replaces.

| Recorded state | Read as | What may happen |
| --- | --- | --- |
| no row, and no entry claims one | UNUSED | first delivery proceeds, as before |
| no row, but an entry **claims** one | RECORD LOST | `PAIR_PREPARATION_RECORD_CORRUPT` (`PAIR_SESSION_USE_RECORD_LOST`) |
| `DELIVERY_INTENDED` (no `dispatch_id`) | USED, OUTCOME UNKNOWN | `PAIR_PREPARATION_SESSION_USE_UNKNOWN` |
| `DELIVERED` with a `dispatch_id` | USED | re-delivery **only** if the shipped reuse gate permits it |

**What makes "no row" a positive statement, exactly.** Two invariants, not one, and the
second was added in correction round 2 after the Final Adversarial Review reproduced its
absence:

1. the writer records `DELIVERY_INTENDED` strictly **before** the delivery, so a delivery
   that happened has a row; and
2. every row the writer creates is **claimed** by the pair entry it was written for — the
   entry cell `session_use_digests`, comma-joined and append-only — and the claim and the
   row are published by **one** atomic document write, so "a claim exists and its row does
   not" cannot be produced by any interleaving of the writers, not even by a crash between
   two writes, because there is only one write. `_read_document` refuses the **whole**
   document when a claimed row is gone.

The first invariant alone was not enough and the review proved it: with all three sections
present and every other row intact, removing one required per-digest row still made the
reader answer "this session was never delivered to" for an already-delivered session, so a
validation repair re-delivered to it without the reuse gate being asked and the run reached
`COMPLETED`. A section's presence says nothing about its rows.

The claim lives on the **entry**, not in a sibling section, so it cannot be lost
independently of the entry whose session it is about; it is **monotone** (a superseding
`create_attempt` adds its new digest and keeps the superseded one's, and
`_PAIR_ENTRY_ATTEMPT_CLEARED` does not touch it); and it does **not** misclassify a
genuinely prepared-but-undispatched session, which has no row because none was ever
written, so nothing claims it and nothing is missing.

A lost row is reported under the established, closed `PAIR_PREPARATION_RECORD_CORRUPT`
name — exactly as a removed **section** is — with the specific record named in the
message; the adapter's closed refusal-code set deliberately gained no member. Internally it
is its own exception class, `PairSessionUseRecordLost`, a subclass of
`PairPreparationCorrupt`, so every caller that already fails closed on an unreadable
document fails closed on a lost row too, unchanged.

Because the entry gained a cell, a `.pair_preparation.json` written before this round holds
use rows that no entry claims — so **its** missing rows would be undetectable. That is why
the preparation schema version moved a second time, to
`os14.pair_preparation.v3`: such a document is refused outright rather than read.

Re-use of a used session is decided by the shipped gate and by nothing else:
`OrcaRuntimeHarness.terminal_for_next_dispatch`, which is `reuse_eligible()`'s one
production consumer, asks all nine conditions with a **fresh** liveness observation and
includes the OS-49 model reuse conditions. A digest match, an adoption, and this round's
model re-verification are each **not** a reuse permission. When the gate refuses — or
cannot be asked, or its observation cannot be read — the run prepares a **new** session
recorded as a new `create_attempt` carrying `supersedes_digest` and `supersedes_reason`
(the gate's own condition names), and the superseded session is left alone.

Only the role this dispatch is about to deliver to is put through the gate. The
counterpart role of the same gate round is adopted for verification, because OS-49 pair
admission requires both sessions verified before the first delivery of either; it is not a
delivery target and is therefore not a reuse candidate.

### Durable model history (B2): the run-scoped non-drift baseline

The `role_history` section of `.pair_preparation.json` holds one **write-once** row per
`(phase, role)` for the whole run — deliberately not per gate iteration, which is the
scope the in-memory `_model_role_history` had and the durable record did not.

- The baseline is **history, never authority.** Nothing reads it to decide that a session
  *is* verified. It is read only to **refuse a change**. Current verification authority is
  earned only by a positive re-verification through the live driver.
- It is compared **before** a new identity is left in an approved state and **before** the
  Task is created: the comparison runs inside pair preparation, which is strictly before
  `create_task`. Since correction round 2 the **row-presence reconciliation** below runs
  earlier still — before the first `terminal list`, the first `terminal create`, the first
  adoption and the first `verify_model_identity` of the round, so a refusal on that path
  creates no session at all.
- On refusal **after selection** the session's current authority is **revoked**
  (`OrcaRuntimeHarness.invalidate_model_authority`, the public name of the existing
  `_stale_model_evidence`) and the history is **preserved**. Selection is the act that
  switches the session, so a post-selection refusal must not leave it advertising a
  verification that was just rejected. The reconciliation path needs no revocation: it
  runs before any session exists, so there is no authority yet to revoke.
- A **missing or contradictory** baseline is `PAIR_PREPARATION_MODEL_HISTORY_CONFLICT`,
  never an opportunity to mint a new one. The baseline row is written **before** every
  entry observation, so an observation standing with no row behind it means a required
  record is gone.
- **Which observations count: every gate iteration of the run, not only the current one.**
  Correction round 2's correction, after the Final Adversarial Review reproduced the gap.
  Reading the baseline row against the *current* entry alone made the row's own presence
  uncheckable precisely at an iteration boundary, where the current entry is legitimately
  absent: with the `role_history` **section** present and only the one required row
  removed, both legs read empty although the previous iteration's `VERIFIED` entry proved,
  on the same disk, that a baseline had been established — and the drifted identity was
  then minted as the new write-once baseline, stored as a `VERIFIED` observation, and the
  row recreated with `first_gate_iteration` pointing at the later iteration. The row is now
  reconciled against **every** entry for the same `(run, phase, role)` in `pairs`
  (`FilePairPreparationStore.role_observations`), and any observation standing with no row,
  or disagreeing with it, is that named refusal.
- A **genuinely new run** gets an independent baseline: the document is run-scoped and
  every row carries its run id, so a role may resolve to a different model in a new run.
  The cross-iteration reconciliation is run-scoped for the same reason — it reads only the
  entries of the document under that run's own root.
- The narrower per-iteration leg (`resolved_model_observed` on the entry) is retained and
  still compared. It is never cleared by a new `create_attempt`.

### Preparation-record loss (B3): an absent record is not a first preparation

`.pair_launch_binding.json` carries a one-way `preparation_started_at` marker, set once by
the preparation path strictly **before** the run's first `CREATE_INTENDED` entry. Because a
`CREATE_INTENDED` is itself written strictly before every `terminal create`:

- marker **absent** → no entry was ever written → no session was ever created → a genuine
  first preparation, which proceeds exactly as before;
- marker **present** and the preparation document holds **no entry at all** → an entry
  existed and is gone → `PAIR_PREPARATION_RECORD_LOST` before any new session, Task or
  Dispatch.

`mark_preparation_started()` mints no binding: a run with no launch record is refused
rather than having one manufactured, so a successor can never create the authority it is
supposed to be checked against.

**One documented conservative case.** A process that died between the marker write and the
first `CREATE_INTENDED` produced no external effect and is still refused, because this gate
reads the marker rather than guessing at that two-write window. The same conservative
`BLOCKED` covers a *failed* first entry write: the marker is set and the first
`CREATE_INTENDED` then cannot be written at all (a lock timeout, a full or read-only
volume, an I/O error). Because the order is *marker → `CREATE_INTENDED` → `terminal
create`*, a `CREATE_INTENDED` that was never written means the `terminal create` it
precedes was never issued, so **no session, Task or Dispatch exists** — and a later
re-entry meets "marker present, no entry at all" and refuses with
`PAIR_PREPARATION_RECORD_LOST`. The refusal is conservative in exactly that documented
way: it reports a possible loss where there was provably no external effect, rather than
guessing at the window. The safe write order is **not** changed to make the wording
narrower. The remedy is the operator procedure below — never an unconditional relaunch.

**The write ordering, exactly.** `_assert_preparation_not_lost()` runs before either role
is prepared and, when no entry exists, writes the marker there — and refuses with
`PAIR_PREPARATION_RECORD_CORRUPT` if that write fails. Only then does `_prepare_role()`
write the first `CREATE_INTENDED`, and only then is a session created. So the order is
always *marker → entry → session*, and "the marker is unset while an entry exists" is a
state this writer cannot produce. (An earlier revision of this section described a crash
"between the first entry write and the marker write" as a residual window; that ordering
does not exist and the claim is withdrawn.)

### Entry-level preparation loss (B3-E1): a record's own entry must still be there

The marker above answers "was *any* entry ever written for this run", so it detects the
loss of the **whole** document. On its own it did not detect a document that is still
present and still holds entries but has lost **some** of them: with another role's entry
remaining, `store.has_any()` was `True`, the marker gate passed, the removed role's own
`entry()` was `None`, and `pause_policy.resolve_prepared_terminal(None, ...)` returns
`create` without consulting the listing — so a new session was prepared under the same
title as the predecessor's still-live one. Reproduced directly on the review-basis SHA by
removing only the worker entry of an intact, admission-stage document: the re-entry from a
real successor process created a second `<run_id>-pair-<phase>-1-worker`, then issued three
`task-create`s and three dispatches and the run reached `COMPLETED`. **That reproduction is
no longer an accepted residual.**

**What is now detected and refused by name.** Two record→entry relations are checked in
`FilePairPreparationStore._read_document()`, beside the review R1 claim→row invariant, and
each is grounded in the writer order that produces it:

| Surviving record | Entry it requires | Why the writer guarantees it |
| --- | --- | --- |
| a `role_history` row for `(phase, role)` with `first_gate_iteration` = *N* | `pairs['<phase>#N'][role]` **exists** | `record_role_history` is reached only after `_prepare_role` published that iteration's entry (`CREATE_INTENDED` strictly before the `terminal create`, then `CREATED`; an adopted session's entry was already there), and the writer now refuses a row it cannot anchor |
| a `sessions` row for `(phase, gate_iteration, role, digest)` | `pairs['<phase>#<gate_iteration>'][role]` **exists and claims that digest** in `session_use_digests` | `record_session_use` refuses a row with no entry to claim it and publishes the row and the claim in **one** atomic write |

A violation raises `PairPreparationEntryRecordLost` — a `PairPreparationCorrupt` subclass,
so the adapter's closed refusal-code set gains no member and the run stops as
`PAIR_PREPARATION_RECORD_CORRUPT`, with the message naming which record was orphaned.
Because the checks live in the document reader, **every** read fails closed, including the
`has_any()` inside `OrcaAdapter._assert_preparation_binding` — the adapter's first store
read, which runs before the first `terminal list`, the first `terminal create`, any
adoption, any `verify_model_identity`, `create_task` and any dispatch. Nothing is
regenerated, no authority is minted, no verification is relaxed and no session is adopted
on a title match.

**The legitimate states this deliberately does not refuse.** Each relation is checked at
the *row's own* iteration, never at the current one:

- the current gate iteration having **no** entry yet while an earlier iteration's baseline
  row stands — the ordinary iteration boundary review R2 reconciles across;
- an entry at `CREATED`, or without `resolved_model_observed`, while its baseline row
  exists — the baseline is written *first*, deliberately, so "history written, `VERIFIED`
  not yet" is a legitimate interrupted state and only the entry's **existence** is
  required;
- a second role not prepared yet on a genuine first preparation — no record names it, so
  nothing is missing;
- a prepared-but-undispatched session with no use row — nothing claims it, so nothing is
  missing (review R1's own discrimination, unchanged).

**What is still not guaranteed.** This is a reference check over the records the document
itself holds, not a tamper-detection scheme, and it is not meant to become one:

- an entry removed together with **every** record that referenced it — for example an
  entry deleted before its first delivery and before its baseline row was written, or an
  entry and its own rows removed in one edit. Nothing then survives to prove the entry
  existed, and the whole-document marker only answers the "any entry at all" question.
- arbitrary concurrent edits by a writer outside this module, which can produce states no
  order of this module's writes can.
- a **rollback** of the whole file to an earlier version: every surviving record is then
  consistent with the entries that remain, so no relation is violated.

A lost launch record is still refused (`PAIR_PREPARATION_BINDING_LOST`), and a
**section**-level truncation is still refused as `PAIR_PREPARATION_RECORD_CORRUPT` because
a present document's top-level section set is exact. For the cases above, treat a partially
hand-edited or partially restored preparation document as unsafe and follow the operator
procedure below rather than re-entering the run.

### Residual prepared sessions: identification, state, ownership, procedure

A prepared session can legitimately outlive the dispatch it was prepared for. This is not
a leak and these sessions are **not** all ownerless orphans. No automatic termination is
added by this change, and none should be inferred.

**How a residual prepared session arises**

| Cause | What exists afterwards |
| --- | --- |
| the pair's second create failed, or verification refused, after the first succeeded | one created, verified-or-not session with a `CREATED`/`CREATE_INTENDED`/`VERIFIED` entry |
| a later named `BLOCKED` (model drift, record loss, unknown use, binding mismatch) | both sessions of the round, prepared and never delivered to |
| a Reviewer prepared for pair admission whose turn never came (the Worker's gate failed, or the run stopped) | the Reviewer session, `VERIFIED`, with no use row |
| a session superseded because the reuse gate refused it | the superseded session, with its `DELIVERED` use row intact |

**How to identify one.** Every prepared session is titled
`<run_id>-pair-<phase>-<gate_iteration>-<role>`, and its identity is the
`terminal_digest` in its preparation entry — the title narrows, the digest decides. Read
them from the run root:

- `.pair_preparation.json` → `pairs` for `stage`, `create_attempt`, `terminal_title`,
  `terminal_worktree`, `terminal_digest`, `requested_model`, `resolved_model_observed`,
  `supersedes_digest`, `supersedes_reason`;
- → `sessions` for whether that digest was ever delivered to, by which Dispatch, and what
  earlier Dispatches used it (`previous_dispatch_ids`);
- → `role_history` for the run's `(phase, role)` baseline;
- `.pair_launch_binding.json` for the run's launch identity and `preparation_started_at`.

**State and ownership.** A session with a `CREATED`/`VERIFIED` entry and no `sessions` row
was created by this run's Coordinator and has never been dispatched to: it is **owned by
the run** that prepared it, not ownerless. A session with a `DELIVERED` row is owned by the
Dispatch named there and is subject to the ordinary OS-31/OS-48 lifecycle and finality
rules. A session with a `CREATE_INTENDED` entry and no observed result has an **unknown**
existence state — the create's outcome was never read — and is the one case where the
record cannot say whether a session exists at all.

**Allowed operator procedure.** Read-only first, and in this order:

1. read the three documents above for the run;
2. list the live sessions in the recorded `terminal_worktree` and match them by title, then
   confirm by digest — a title match alone is not an identity, and the code never adopts a
   title-only candidate;
3. for each matched session, read its use row before touching it. A session with a
   `DELIVERED` row may still be the subject of an open Dispatch;
4. close only sessions you have positively identified by digest, that this run prepared,
   that have no open Dispatch, and after checking `orca orchestration task-list --run` and
   the open dispatches for the run. Closing is an operator decision, never an automatic
   one;
5. a session whose entry is `CREATE_INTENDED` with no observed result is **not** safe to
   assume absent and **not** safe to assume present: enumerate it, record it, and decide
   deliberately.

Nothing in the engine closes, reuses, adopts or counts a residual session on its own.

### Recovery support scope — what is wired and what is not

Recovery of a model-aware run is supported **only** where the routing and the driver are
explicitly injected into the reconstructing constructor. The distinction matters because
the shipped resume/watchdog behaviour does not do that.

**Supported.** `deterministic_workflow.launcher.build_orca_adapter_for_run(run_id, ...)`
called with a non-empty `agent_profile_name` **and** a `model_driver`. It reconstructs the
routing through the existing derivation from the run's own committed declaration — phases
and risk from one read — reopens the launch binding and the preparation document, and the
adapter then reconciles the full launch identity, cell by cell, before any effect
(`PAIR_PREPARATION_BINDING_MISMATCH` / `PAIR_PREPARATION_BINDING_UNVERIFIABLE` otherwise).
The OS-14 test suite's successor-process scenarios take exactly this door.

**Not wired: the CLI call sites.** There are exactly two in-repository callers of
`build_orca_adapter_for_run` outside the tests, and **neither passes
`agent_profile_name` or `model_driver`**:

- `launcher.run_pause_cli` — `scripts/deterministic_workflow/launcher.py:3631`, the
  `pause`/`resume` CLI path;
- `launcher._watchdog_wiring` — `scripts/deterministic_workflow/launcher.py:4257`, the
  OS-43 watchdog and one-shot recovery path.

Both pass only `run_id`, `artifact_base`, `runtime_state`, `run_owner`, `project_root`
(and, in the watchdog's case, `harness_factory`). A process built that way holds no routing, so
`harness.routing_binding()` reports `model_aware == "false"` against a binding that says
`"true"`, and the adapter refuses `PAIR_PREPARATION_BINDING_UNVERIFIABLE` before any
effect. **A model-aware run therefore cannot be resumed through the CLI today.** That is
the fail-closed outcome, not a gap that silently degrades — and completing the CLI wiring
is deliberately *not* part of this correction run.

**Pre-feature runs.** A run launched before this feature has no launch binding at all and
is refused `PAIR_PREPARATION_LAUNCH_RECORD_ABSENT`: an absence is not a verdict in either
direction, and a successor may not manufacture the authority it is supposed to be checked
against. A run launched under the v1 schemas is refused
`PAIR_PREPARATION_RECORD_CORRUPT`.

**Safe transition procedure for such a run — inspect before you relaunch.**

1. Do **not** relaunch unconditionally. The refusal means this process cannot prove what
   the old run did, not that the old run did nothing.
2. Enumerate the run's external effects first: `orca orchestration task-list --run <run>`
   for Tasks, the run's open dispatches, and the live sessions in the run's worktree
   matched by the pair title and confirmed by digest.
3. Settle or account for every open Dispatch found. A relaunch while a Dispatch is open
   duplicates work and can leave two agents on one artifact path.
4. Only once no open Dispatch and no unaccounted session remains, start a **new** run.
   Preserve the old run's artifacts and records; do not edit or delete them.
5. If step 2 cannot be completed — a listing that cannot be read, a `CREATE_INTENDED` entry
   with no observed outcome — stop and escalate. An unreadable source is unknown, never
   empty.

## OS-14 TRACK B, first foundation PR — the launch lease, the v4 schema and the round-scoped target lifetime

This section records what the **first foundation PR** of OS-14's real-driver work changed.
It is deliberately narrow, and the narrowness is the point: three implementation units
(**I-1** the launch lease, **I-4** its durable cells, **I-7** the post-create bind and the
round-scoped lifetime) plus the one prerequisite **I-2** needs, and nothing else.

**Nothing here is evidence about any real Claude or company model.** No real driver is
registered, none is enabled by default, no `claude` process is launched by any code path in
this release, and every test that exercises the new code drives it with a **stand-in** at
the production boundary. A stand-in's success is wiring evidence; it is **not** a
model-identity verification, and this document does not present it as one. The full
specification remains `docs/OS-14-claude-driver-design.md`; the per-unit implementation
status table lives at the top of that document.

### The pair-preparation schema moves again: v3 → v4 (breaking for in-flight model-aware runs)

| Document | Was | Now |
| --- | --- | --- |
| `.pair_preparation.json` | `os14.pair_preparation.v3` | `os14.pair_preparation.v4` |
| `.pair_launch_binding.json` | `os14.pair_launch_binding.v2` | **unchanged** |

The reading is **exactly** the fail-closed reading the v1 → v3 bump has, and it is
*inherited* rather than re-implemented: `durable_store.read_json_document` refuses any
document whose `schema_version` is not this exact string, raising
`PairPreparationCorrupt`, which `OrcaAdapter` projects onto
`PAIR_PREPARATION_RECORD_CORRUPT` (`INCOMPATIBLE_DURABLE_STORE`). An older, absent-versioned,
corrupt or mismatching document is therefore refused **by name** and is **never** read as
"nothing was prepared", "an unused session" or "no baseline yet". The behavioural half of
that rule is locked by `test_a_v3_document_is_refused_under_the_v4_reader`, which asserts
all four readings — `has_any`, `entry`, `session_use`, `role_history` — refuse on one
complete, well-formed v3 document.

**Nothing is converted, emptied or drained.** A v3 document stays byte-for-byte as it is on
disk; the run that owns it is refused rather than reinterpreted; and no past in-flight run's
sessions, Tasks or dispatches are cleaned up by this change. The transition procedure is the
one already documented above for a pre-v3 run — *inspect before you relaunch* — and it is
unchanged.

**Why a bump is required rather than additive tolerance.** A v3 entry records no statement
about whether the session it names was launched by this engine under a lease. Read under the
v4 rules it would answer "no lease" for a fact it never recorded, which makes a lease-less
session indistinguishable from a session nobody launched — the same class of silent-empty
reading the two earlier bumps exist to refuse.

### The new entry cells: the launch lease

Six cells are added to the `pairs` entry. Five are minted **together**, in the
`CREATE_INTENDED` write that already runs strictly **before** the one external effect of
preparation; the sixth is the revocation stamp.

| Cell | Written at | What it is |
| --- | --- | --- |
| `launch_token` | `CREATE_INTENDED` | the harness-minted capability every later driver call on this session must cite |
| `launch_stamp` | `CREATE_INTENDED` | decimal string; the ordinal of the mint, drawn from the **same** private monotone counter the verification tickets draw from, so "the launch preceded the verification that cites it" is integer order over harness-issued values |
| `session_uuid` | `CREATE_INTENDED` | the CLI `--session-id` the launch argv would be composed with |
| `endpoint_digest` | `CREATE_INTENDED` | `sha256` of the derived control-endpoint path — **never** the path |
| `launch_argv_digest` | `CREATE_INTENDED` | `sha256` of the launch **request** (command, requested model, session uuid, endpoint digest). See the honesty note below |
| `launch_revoked_at` | at revocation | set once; a revoked lease is stale |
| `terminal_digest` | `CREATED` | **pre-existing cell.** Bound only after the create receipt is observed, because the handle does not exist before `terminal create` returns |

**Whole or corruption.** The five mint cells are written in one `record()` call, so a
partial lease cannot be produced by any interleaving of the store's writers.
`validate_pair_entry` therefore refuses a partial lease, a non-decimal or zero
`launch_stamp`, and a `launch_revoked_at` with no `launch_token` — a lease that was never
minted cannot have been revoked.

**The mint rule, and its durable half.** **ONE lease per `terminal create`, never
re-exercised by a second create.** The three reachable creates — a first create, a retry
after a parsed `CREATE_REFUSED`, and a reuse-gate supersession — each mint a **new** token,
and the superseded token is revoked by the mint that replaces it. Durably,
`_PAIR_ENTRY_ATTEMPT_CLEARED` clears every lease cell when a new create attempt is
published, so a replacement can neither inherit the superseded lease's revocation stamp (and
be born stale) nor its endpoint (and share a path with another incarnation). Nothing about
the **superseded session** is lost by that: `supersedes_digest` / `supersedes_reason` are
written in the same call, the `sessions` use ledger is keyed per terminal digest and is
untouched, and `role_history` — the drift baseline — is untouched.

**`launch_argv_digest`, stated honestly.** The launch-command seam (**I-3**) and the driver
that composes a provider argv (**I-6**) are **not** in this release, so there is no literal
argv to digest yet, and this cell does not pretend otherwise: it digests the four inputs an
argv is composed from. I-3/I-6 replace it with the digest of the argv they actually compose.
No provider flag, syntax or stream grammar is invented anywhere in this release.

### The barrier gains one leg: the LAUNCH, attested

`ModelEvidence` gains two attested fields, `launch_token: str = ""` and
`launch_stamp: int = 0` — additive with `""`/`0` defaults, exactly as `ReuseObservation` and
`RuntimeAttempt` do it, so every existing construction binds unchanged. `ModelSelectionTicket`
carries the same two read-only values so a conforming driver knows **which** launch it is
being asked to re-validate.

**Whether the leg runs is decided by PROVENANCE, not by the presence of evidence (PR40-B1).**
The first edition entered the leg only when this process's registry held a lease for the
session **or** the driver cited a token — which answered *must this launch be verified* with
*is there any launch evidence to hand*. A successor process has an empty registry, so the
ticket offers `launch_token: ""` / `launch_stamp: 0` and a **conforming** driver that cites
what the ticket tells it about returns that empty citation; the leg was then skipped before
the durable lease was read at all, and a session this run had demonstrably launched under a
lease was verified by a process holding no lease and no binding for it. The obligation is now
read off **this run's own durable record**, attributed to the target by run, phase, gate
iteration, role and terminal digest — never off the registry, the citation or the current
capability. An empty citation, an empty registry and an absent live target are **not**
grounds for a legacy verdict. The durable provenance has **four** answers, not two, and the
distinction is load-bearing: `recorded` (a lease is on file for this target), `no_launch_recorded`
(the document positively states this run recorded none — the one answer that may read as
genuinely **legacy**), and `unreadable` / `no_reader` (the question could not be answered).
A reader failure, an absent reader and a corrupt record are **never** a verified no-lease
state. The genuine legacy and reference-driver paths are unchanged: nothing records a lease
for them, so nothing obliges them.

The leg **(a2)** performs **six** checks — the obligation, then the five the first edition
had — every one of them an equality or an integer order over a value this harness issued or
this run itself durably wrote. No provider knowledge, no stream parsing, no trust in the
driver anywhere in the leg:

| Check | What it compares | Reported as |
| --- | --- | --- |
| **(a2-0) obligation** (PR40-B1) | the **durable** record names a launch for **this** target and this process holds no lease for it | `model_selection_request_absent`; a record already stamped revoked is `model_selection_request_stale`; an **unreadable** record is `model_selection_unverified` |
| **(a2-1) citation** | a launch is cited **iff** one was minted for this session, and the cited token is one this harness minted | `model_selection_request_absent` |
| **(a2-2) durability** | the citation is confirmed by the **durable** record; a lease that exists only in this process's memory is never sufficient | `model_selection_request_absent` |
| **(a2-3) liveness** | neither the registry nor the record shows it revoked, and the session was not already released | `model_selection_request_stale` |
| **(a2-4) target** | the lease's `(run, phase, role, gate iteration)` key is this attempt's, it **has** a target, and that target is **this** session | `model_selection_request_stale`; an **unbound** lease is `model_selection_unverified` carrying `DRIVER_TARGET_UNBOUND` |
| **(a2-5) order** | `0 < launch_stamp == the minted stamp < request_stamp` | `model_selection_request_stale` |

plus one equality on the **model** — a lease that records a different requested model than
the routing declares is `model_selection_mismatch`, not a staleness.

**Every one of those is an EXISTING member of the closed failure vocabulary.** This release
adds **no** vocabulary member. The three members the design's P-7/I-10 would add —
`cli_launch_model_flag`, `cli_launch_lease_revalidation` and
`stream_json_preflight_assistant` — are deliberately **absent**, because adding a request
method early makes an unauthorised path look eligible. The consequence is checkable and is
asserted: a driver attesting `cli_launch_model_flag` today is refused
`model_selection_unsupported`, because the member does not exist. Adding it is the visible,
reviewed act that lifts that refusal, and it has not happened.

**D2's revalidation semantics, in the form this release can hold them.** The first launch of
a session is the act that selects a model; every later driver call on that session
**re-validates** that launch and performs no new one. Structurally enforced: a lease is
minted only on the preparation path, strictly once per create, and both the pre-pass and
Gate B of one attempt cite the **same** `launch_token`. The *member name* that would make
that attestation self-describing is I-10's and is deferred; the enforcement is not.

**A durable record restores nothing.** The lease registry and the target binding are
in-process state, deliberately: the design's restart rule is a **named refusal**, so there
is nothing durable to resurrect. A successor process starts with an empty registry, finds a
durable lease it did not mint, and refuses — it never rebinds. **And it refuses whatever the
driver cites, including nothing at all** (PR40-B1): the refusal is grounded in the durable
record of the launch, so an empty citation cannot route around it. The refusal is also only
a refusal — a successor mints no lease, binds nothing, relaunches nothing, attempts no
recovery, and leaves the durable record exactly as it found it. A durable lease with no live
channel is `DRIVER_TARGET_UNBOUND`; a second process under a recorded handle is
`DRIVER_TARGET_REBOUND`; an unmatched bind echo is `DRIVER_BIND_UNCONFIRMED`. All three are
driver-local target states reported through existing vocabulary members, and none of them is
ever repaired by rebinding.

### The target lifetime is now ROUND-scoped, not settlement-scoped (TL)

A prepared target's channel and lease are held from its `CREATED` write until the **gate
ROUND** that prepared it **closes**. The round is one `(phase, gate iteration)` key together
with **every** graph turn the engine runs under it — the Worker's turn, the phase-Reviewer's
turn, and every validation-repair turn of either role. `release(handle)` is called for every
handle prepared under that key, **exactly once**, at round close, and it is **idempotent**.

A round closes on the first of:

1. **validated completion** — **(1a)** every role **required by the round** has reached
   `VALIDATED` **and (1b)** the token ROUTE emitted is neither `PREPARE_REPAIR` nor
   `PREPARE_PHASE_REVIEWER`. The required-role set is the engine's own rule, read from
   `routing.phase_gate`: at `risk == "low"` the **Worker alone**; otherwise the **Worker and
   the phase Reviewer**, whether or not the Reviewer has been dispatched yet. An undeclared
   risk takes the stricter set.
2. **refusal or abandonment** — any preparation refusal on this key, a Gate B refusal, a
   delivery failure, a `CANCEL`/`ABANDON`, or a `PAUSE`.

**Why not "after settlement".** `settle_attempt` runs inside `EXECUTE_INTENT`; a malformed
decision-gate record is classified one node **later**, in `VALIDATE_SETTLEMENT`; and ROUTE
may then re-dispatch the **same role in the same round**. `_prepare_pair` verifies **both**
roles unconditionally on **every** pass, so the Reviewer's turn — and every repair turn —
re-verifies the **settled** Worker's own session. A settlement-scoped release tears down
exactly the target the next turn is about to re-verify, and the design measured the
consequence: `PAIR_PREPARATION_MODEL_UNOBSERVED`.

**What TL does NOT change.** Dispatch settlement, current model authority and Orca's
dispatch capability are all untouched — Orca revokes the dispatch capability at settlement
and TL has no claim on it. Nothing is carried **across** rounds: a new `(phase, attempt)`
key is a fresh session and a fresh lease. TL does not decide whether a repair **reuses** the
held session: the shipped reuse gate does, through its one production consumer, and TL only
removes a reason the gate could never have been asked. And TL assumes **no** cleanup on
Coordinator process exit — the in-process registry dies with the process, and the surviving
CLI child is the **operator's residual**, under the residual-session procedure already
documented above.

**`release(handle)` is channel and lease cleanup, and nothing more.** It revokes the lease,
drops the binding, asks the driver to close its own channel when it can, unlinks the derived
endpoint path and removes the per-run directory when empty — tolerating `ENOENT`, because
both sides unlink and the authoritative "this endpoint is finished" fact is the **revoked
lease**, not the file. It does **not** terminate a CLI child and does **not** close an Orca
terminal. A released session is also refused rather than silently read as legacy: a later
verification of a released handle is `model_selection_request_stale`.

**Clause 2 is connected to every FAILABLE part of a dispatch, not only to the refusal exit
(PR40-B2).** The first edition registered a handle as its round's disposal target only
**after** the post-create bind, and routed clause-2 disposal only through
`_refuse_preparation` and the engine's post-settlement observer. Four failures therefore
left a round's sessions, leases and bindings standing with nothing able to tear them down:
a bind the driver did not confirm (`DRIVER_BIND_UNCONFIRMED`), a preparation **pre-pass**
refusal, a **real Gate B** refusal (which happens one frame below, inside
`run_existing_task`), and a delivery failure after the Task already existed. Two things
changed and nothing else:

- **the handle is registered as its round's disposal target the moment its create receipt
  is in hand** — strictly before `bind_launch_lease` and `bind_prepared_target`, both of
  which can fail. Registration is the statement *this round created this session and
  therefore owes its teardown*; it is **not** a binding success and **not** a
  model-verification success, nothing downstream reads it as permission, and only a handle
  a receipt reported can reach it — no session is ever invented or adopted by title; and
- **`OrcaAdapter.start` is the one frame that sees all four failures**, so clause 2 is
  connected there. It is scoped to the round this call established (the key is compared
  against the intent's own `(contract_phase, gate_iteration)`), so a failure raised before
  the call entered a round cannot dispose of a previous one.

**Cleanup never replaces the failure it is cleaning up after.** The original exception is
re-raised unchanged, so every named refusal, the `IdempotencyRecoveryError` → BLOCKED
projection and the exception-normalisation contracts stand exactly as they are, and no
cleanup can turn a failure into a success. `BaseException` is **not** caught anywhere on
this path, so `KeyboardInterrupt` / `SystemExit` / `GeneratorExit` propagate as themselves
and **a hard process exit or `SIGKILL` still promises no cleanup at all** — that exclusion
is unchanged. An additional error **during** cleanup is recorded as its own diagnostic
(`RoundTargetLifetime.release_errors`, and a `dispose:clause2:error` row in
`round_lifecycle_log`) and may not cancel the remaining handles' cleanup attempts: the
disposal loop records a failed teardown and continues to the next handle.

**With a healthy store the two halves of a release agree; with an unhealthy one, nothing
claims they did.** `release(handle)` revokes the live lease and drops the binding, and the
adapter's own half stamps `launch_revoked_at` on the durable record. The live half runs
first, so an injected durable-write failure leaves the in-memory invalidation done, the
remaining handles still cleaned up and the failure diagnosed — and the durable cleanup is
reported as **not** having succeeded rather than assumed. A pre-delivery refusal (bind,
pre-pass, Gate B) performs **zero** deliveries; a delivery failure after the Task and the
delivery attempt exist preserves the obligation/UNKNOWN record the shipped path already
writes, settles nothing successfully, re-delivers nothing and delivers to no other target.
The authority/history separation and the shipped reuse gate are untouched.

### How the engine reports a round's lifetime (I-7's engine connection)

TL needs four facts about a round, and every one of them is a decision the deterministic
engine already owns. So the engine **reports** them and the adapter decides what they mean
for a target's lifetime. There is exactly **one** port —
`OrcaAdapter.observe_round_lifecycle(event, …)` — with a **closed** four-member event
vocabulary, and the engine calls it from five places:

| Node | Event | What it states |
| --- | --- | --- |
| `execute_intent_node`, once the settlement event is in hand | `settled` | this role's Dispatch settled and nothing has classified it yet ⇒ `SETTLED_PENDING_VALIDATION` |
| `validate_settlement_node`, on its no-defect returns | `validated` | the result's FORM was accepted ⇒ `VALIDATED`. A **defective** settlement is deliberately not reported: whether it becomes `REPAIR_PENDING` is ROUTE's decision one node later |
| `route_node`, after it computes the token | `route` | the token verbatim, plus the run's own `risk` and the role `repair_binding` names |
| `pause_node` | `dispose` | TL clause 2, the pausing path |
| `dispose_node` | `dispose` | TL clause 2, the disposing path (`CANCEL` / `ABANDON`), before TERMINAL |

**The hook observes; the graph decides.** `graph_spec.GRAPH_OWNED_DECISIONS` keeps owning
`PHASE_GATE`, `CORRECTION_LOOP` and `VALIDATION_REPAIR`. Four properties make that
checkable rather than asserted, and each has a test: every node computes its own answer
**before** reporting and **discards** the observer's return value; the observer wrapper is
total for ordinary exceptions, so a channel teardown that fails cannot change where the
workflow goes next (`BaseException` still propagates); the port is resolved **once**, at
`build_graph`, so no node can acquire a different observer mid-run; and an adapter with no
port — a legacy run, a `None` driver, the fake adapter — yields `None` and every call site
is inert. The comparison test runs one scenario three ways (wired, port removed, port
raising) and requires the terminal status, the repair budget and the whole node/token
sequence to be identical.

**The round key is recorded, not re-derived.** `OrcaAdapter.start` records
`(contract_phase, gate_iteration)` from the intent before any effect — the same key
`_prepare_pair` registers handles under. That is load-bearing: `apply_result_node` advances
`phase_iterations` for the gate round that just settled, so `gate_iteration` evaluated at
ROUTE would name the **next** round, and the round that is closing is the one that just ran.

**Which clause applies is decided in one place, and the predicate is always asked first.**
On every token the close predicate is consulted; if it closes, that is **clause 1**. If it
does not and the token is one of the two that keep a round open (`PREPARE_REPAIR`,
`PREPARE_PHASE_REVIEWER`), the round is **held**. Otherwise the engine has left the key
while a required role never reached `VALIDATED` — a gate block, an escalation, a pause, a
cancel, an abandon, or a phase/correction transition — and that is **clause 2**. Asking the
predicate first matters: an earlier edition answered `PREPARE_PHASE_REVIEWER` with a hold of
its own, which re-implemented (1b) outside the predicate and masked a hook whose (1a)
quantified over *dispatched* roles. The three wrong-hook negative controls are what catch
that, and they only can if the predicate decides.

**A FINAL REVIEW round's required role is the Final Reviewer**, at every risk level:
`routing.final_gate` reads no worker or phase-reviewer result at all, so quantifying such a
round over `worker`/`reviewer` would make its close unsatisfiable and report every completed
run's last round as a disposal. Nothing is ever prepared for it (the Final Reviewer is
outside the pair rule), so the release set is empty either way; what the distinction fixes
is the **clause**.

**At LOW risk no pair is prepared at all.** `pair_admission_required` is false because the
Reviewer routing entry is optional there, so `_prepare_pair` never runs and the round has no
handle to release. That is TL's own "no release is required for a handle that was never
created" — the round still closes under **clause 1**, over the Worker alone, with an empty
release set.

Every observation is appended to `OrcaAdapter.round_lifecycle_log`, a **diagnostic**:
nothing reads it to decide anything. It exists because a correct hold and a connection that
never fired both produce zero releases, and the log is what distinguishes them.

### One locked contract was adjusted, by the mechanism it already provides

OS-37 **DESIGN D4.1** byte-freezes four engine policy modules to `BASELINE_REV = d13b7fa`
(`scripts/test_os37_driver_isolation.py`): `graph.py`, `routing.py`, `executor.py`,
`state.py`. I-7's engine connection cannot be written without editing two of them — the
facts TL needs exist only in `executor.py`'s nodes, and the one place an observer can be
bound to the pure `ROUTE` node is `graph.build_graph`.

The lock was extended by **the same mechanism `contracts.py` already uses**, not re-pinned:

* the edit is declared by **line range** —
  `POLICY_MODULE_ADDITIVE_RANGES` = `graph.py` `(15, 16), (412, 417), (420, 421), (426, 428), (431, 432), (437, 438), (441, 442)`,
  `executor.py` `(68, 104), (106, 114), (116, 127), (372, 372), (391, 391), (401, 408), (509, 509), (517, 518), (522, 529), (592, 593), (601, 602), (606, 611), (693, 707), (718, 720), (738, 743), (757, 761)`;
* what is pinned is each module's **policy region** — every byte outside those ranges;
* `POLICY_MODULE_SUPERSEDED_RANGES` names the baseline lines the edit replaced — **seven**
  in `graph.py` and **five** in `executor.py`, all of them the import block, the five node
  registrations and the five node signatures;
* and `test_the_policy_pins_are_the_baseline_and_not_this_tickets_output` **proves** the
  region digests are the ones `d13b7fa` holds outside the superseded ranges, computed from
  the git object. `BASELINE_REV` is unchanged, so the authority is still the baseline and
  not this change's own output — a pin regenerated from a modified file fails there.

`routing.py` and `state.py` are **untouched and still pinned byte-for-byte**: the
connection adds no route target, no node and no branch on any routing, decision or review
policy. The independent second reading,
`test_policy_modules_carry_no_standalone_branch`, is unchanged. A change **outside** the
declared ranges still fails the gate, which is verified rather than assumed.

### The control endpoint: derived, never discovered

`<state_dir>/os14/<run_id>/<sha256(launch_token)[:16]>.sock`, where `state_dir` is
`$XDG_RUNTIME_DIR` when set and the platform temporary directory otherwise — **never**
inside the repository or a worktree. Only the **digest** reaches a durable cell, an artifact
or a log. The composed path's length is asserted against the **platform's own** `sun_path`
budget rather than against a constant from the design, because that budget is 104 bytes on
the macOS build this work was measured on and 108 on Linux. The probe is a `connect()`, which
CPython rejects for an over-long address **before any syscall**, so the assertion creates
nothing, unlinks nothing and disturbs no listener. An over-long path is
`LAUNCH_ENDPOINT_PATH_TOO_LONG`, refused before any effect, and the diagnostic reports the
**length** only.

**No socket is bound by this release, and no directory is created.** The supervisor shim that
would bind the endpoint is **I-5** and is not here, so the harness **derives** the path,
records its digest and creates nothing on the filesystem — a mint performs no filesystem
effect at all. Creating `<state_dir>/os14/<run_id>/` at `0700`, which the design places
before `terminal create`, moves with I-5. `release(handle)` already unlinks the path and
removes the per-run directory when empty, and tolerates both being absent, so the two halves
do not have to land together.

### What stays UNSUPPORTED by default, and what is still not implemented

- **No real driver is registered or enabled.** `model_driver` still defaults to `None`, and
  `None` still yields the **empty** capability set, so the real runtime stays fail-closed by
  construction rather than by remembering to pass nothing. A declared model with no driver is
  still refused at the declaration gate with `AGENT_MODEL_NOT_SUPPORTED`, and again at the
  pre-delivery barrier with `model_selection_unsupported`.
- **The hosted-delivery capability (the rule HC) is one derivation over two names.** It exists
  **iff** `deliver_hosted` **and** `bind_target` are **both** callable on the driver object.
  One name is not hosted-capable, either way round. When HC does **not** hold, the
  preparation gate mints **no** lease at all, writes **no** lease cell and derives **no**
  endpoint: the sessions it prepares are **genuinely legacy** and take today's delivery path
  because they carry nothing hosted to recognise — not because a capability was re-read later
  and found wanting. HC is **never** a recognition input.
- **The legacy and reference-driver paths are byte-unchanged.** The default path, the `None`
  driver and OS-49's `InProcessModelDriver` mint nothing, record nothing and create no
  directory.
- **Not implemented in this release, and named rather than implied:** **I-3** the
  launch-command seam and the contract-lock replacement it forces; **I-5** the `AF_UNIX`
  supervisor shim; **I-6** the Claude driver module and its stream parser; **I-8** the hosted
  delivery branch and its three-way `delivered` / `not_delivered` / `UNKNOWN` verdict;
  **I-9** permission scope (a **proposal only** — see
  `docs/OS-14-permission-scope-proposal.md`); and **I-10** the three vocabulary members.
  I-7's engine connection is **implemented** — see the next subsection.
- **Still 미검증, and no test in this release changes it:** everything that requires a real
  model turn. No model identity has been verified against any provider by this code, the
  hold's **wall-clock** cost remains unmeasured (D5), and the company (PortKey/GLM)
  environment is untouched.
