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

- company-environment verification of model-aware routing; **nothing here claims it**
- which of GLM-5.2 and GLM-5.3-flash should be Worker and which Reviewer
- the real shape of a launch receipt's effective-model field (unobserved; nothing depends on it)
- real in-band model-selection syntax, acknowledgement format and output semantics
- whether the company environment's `claude` is reachable as a recognized Orca agent id
- whether `claude-gemma` leaves `known_agent_commands` (unchanged by OS-49)
- the first adapter or driver that can **honestly declare both legs** in a real environment,
  which is the one thing that lifts OS-49's fail-closed refusal for a real run
