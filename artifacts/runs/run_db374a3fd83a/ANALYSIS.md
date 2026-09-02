# Worker Result

STATUS: COMPLETE

## Request Summary

Analyze Jira OS-30 only, from the current `origin/main` baseline represented by branch `feat/os-30-structured-clarification`, and identify the repository changes and tests required for a complete Structured Human Clarification and Decision Protocol. The full Jira goal, Scope, nine acceptance criteria, Dependencies, and explicit exclusions are authoritative; this artifact does not implement any proposal.

## Current State

### Repository facts

- No `AGENTS.md` exists in this repository. A direct `find` from the repository root found only files in sibling repositories, so none governs this worktree.
- The worktree is based on `origin/main` and already contains unrelated untracked historical/run artifacts. They must be preserved; OS-30 must not delete, migrate, rewrite, stage, or treat them as its baseline.
- OS-28 is the shared semantic authority in both `orca-worker-reviewer-loop/SKILL.md` and `orca-worker-reviewer-orchestration/SKILL.md`, parsed by `scripts/decision_policy.py`. It fixes the four states, requires an explicit user decision for `NEEDS_INPUT`/`CONFLICT` to become `CLEAR`, forbids either state from becoming `ASSUMPTION_ALLOWED`, and explicitly rejects timeout, no response, and a recommended default as authority.
- OS-29 is implemented only in the orchestration execution path. `scripts/decision_gate.py` parses the required agent declaration plus fenced record, validates the closed ledger schema, derives open items, admits only the already-scheduled verification Reviewer, and terminates an unresolved decision with the existing `BLOCKED` status. It imports only `decision_policy` plus the standard library.
- The OS-29 decision record field set is intentionally closed. `OS30_RESERVED_FIELDS` contains `supersedes`, `superseded_by`, `request_id`, `response_id`, `options`, `recommendation`, `answered_at`, and `answered_by`; existing fixtures prove those fields are malformed in a decision-ledger record. OS-30 therefore cannot safely overload or widen an OS-29 record without breaking its explicit boundary.
- `scripts/run_logging.py` owns the append-only decision ledger at `artifacts/runs/<run-id>/decision_ledger/<sequence>/record.json`. It stages, fsyncs, atomically renames, never edits a published entry, and is byte-identical to `orca-worker-reviewer-orchestration/tools/run_logging.py`. The installed orchestration Skill cannot import repository-only `scripts/` modules.
- `scripts/orca_runtime_harness.py` opens ledger sequence zero with the run, enforces B1 before dispatch, records B2/B3 results after settlement, and intentionally stores `_last_settled` only in process memory. A blocking result remains terminal even after Reviewer verification. `scripts/e2e_harness.py` mirrors that behavior deterministically and preserves the existing two-agent/two-subprocess topology.
- OS-29 currently has no structured request creation, response ingestion, normalized decision object, re-clarification, or decision supersession. The orchestration Skill names those omissions in its OS-30/OS-31 limitations; the roadmap likewise says asking is OS-30 and cross-session resume is OS-31.
- The direct-session loop has the OS-28 semantics and required result vocabulary but no OS-29 runtime gate, run-scoped artifact root, or durable ledger. A complete executable OS-30 protocol naturally extends the orchestration Skill; the loop Skill should only retain shared semantics and accurately state that it does not provide the orchestration artifact/CLI path.
- The analysis and review templates require repository evidence, fact/proposal separation, explicit unknowns, and a required machine-readable decision-gate result. The quality profile is absent, so only explicit requirements, the analysis phase contract, and G1-G5 apply.
- Release validation is standard-library-only on Python 3.11-3.13. The required gate before installation/release is `validate_skills.py`, full unittest discovery, `verify_package.py`, release build plus archive verification when preparing a release, and `git diff --check`. The manifest currently permits exactly one orchestration tool, so a second installed tool requires an explicit manifest and packaging-test update.
- Documentation that would become stale includes `CHANGELOG.md`, `README.md`, `INSTALL.md`, `docs/ROADMAP.md`, and `docs/COMPATIBILITY.md`. OS-30 is a backward-compatible policy capability and therefore a MINOR-class change under `docs/RELEASING.md`, but version bump/release publication is not part of this analysis and must follow the repository release process.

### Jira source facts

The following are requirements, not design choices:

- Each question carries context, actionable choices with trade-offs, an explicit recommendation, whether a default is applicable, and timeout behavior.
- Independent questions may be grouped only into a limited bundle; dependent questions are requested sequentially in dependency order.
- Natural-language responses normalize either to an explicit option or to a bounded custom decision declared by the request.
- Response and decision history records provenance, responding actor, timestamp, and supersession.
- The lifecycle handles decision cancellation, change, and scope expansion without erasing history.
- Both OS-29 producer states, `NEEDS_INPUT` and `CONFLICT`, feed this protocol; the port boundary remains runtime-neutral through a `HumanApprovalPort` contract.

1. A `NEEDS_INPUT` outcome creates a structured request artifact with a stable ID.
2. Every request has at least one actionable option and an explicit recommendation.
3. A recommendation is not approval.
4. Timeout or no response is not implicit approval.
5. The original response and normalized decision are both retained.
6. An ambiguous response causes bounded re-clarification.
7. A changed response supersedes the prior decision and preserves lineage.
8. The protocol uses artifacts and an explicit CLI response, not a terminal UI.
9. Secret/sensitive responses are not copied without limit into ordinary logs.

Out of scope is a durable resume engine, transport-specific UI/integration, and an organization-specific option catalog.

## Findings

### F1 — The protocol needs its own schema and artifact namespace

This is required by both Jira AC1/5/7 and the existing OS-29 closed-field contract. Adding OS-30 fields directly to `CLOSED_LEDGER_RECORD_FIELDS` would erase the repository's deliberate separation and invalidate the fixture `record_carries_os30_supersession.json` as historical contract evidence.

Proposal: add a standard-library `scripts/clarification_protocol.py` with a byte-identical installed copy at `orca-worker-reviewer-orchestration/tools/clarification_protocol.py`. Give it independent schema versions for request, response, normalized decision, and lineage event. Keep decision-ledger records unchanged and link across namespaces with the immutable OS-29 `ledger_key` string.

Proposed run-scoped layout:

```text
artifacts/runs/<run-id>/clarifications/
  requests/<request-id>/record.json
  responses/<response-id>/record.json
  responses/<response-id>/raw_response.txt
  decisions/<decision-id>/record.json
  lineage/<sequence>/event.json
  .staging/
```

All published objects should use the existing stage/fsync/atomic-rename/no-overwrite pattern. Readers must ignore `.staging`, surface malformed published objects as `unknown`/invalid rather than absence, and derive status from append-only lineage rather than modifying a prior record.

### F2 — Stable identity must distinguish the decision item, request revision, response, and decision

One undifferentiated ID cannot satisfy retries, re-clarification, and changed answers without collisions or accidental overwrite.

Proposal:

- `bundle_id`: immutable identity for a bounded group of mutually independent decision items; absent for a single-item request. The request records a fixed maximum bundle size and the member IDs.
- `decision_item_id`: stable for the originating open OS-29 item; derived from and bound to `run_id + ledger_key`.
- `request_id`: stable immutable identity for one request revision; generated once and persisted, with `decision_item_id`, `revision`, and optional `reclarifies_request_id`.
- `response_id`: immutable identity for one explicit submission attempt.
- `decision_id`: immutable identity for one successfully normalized decision.

IDs should be validated closed-format tokens, never trusted from an unvalidated path fragment, and creation must be first-writer-wins/collision-safe. Re-reading or re-rendering an existing request must return its persisted ID instead of silently creating another request for the same ledger head.

Independence is explicit input, never inferred from wording. A request may bundle only items whose dependency sets are empty relative to one another and only up to a documented small bound; dependent items remain unpublished until every predecessor has an effective decision. Readers must reject oversized bundles, duplicate membership, cycles, or a bundle containing an ancestor and descendant. This preserves stable per-item identity while allowing one request artifact to reference a bounded list of independent items.

### F3 — The request schema must encode context and choice cost, not merely a question string

Jira's goal requires the Coordinator to replace vague free-form questions with the minimum structured clarification containing context and choice cost. AC2 adds an actionable option and explicit recommendation.

Proposal: require these request fields:

- schema/version and identities: `run_id`, one or more `decision_item_id`/`source_ledger_key` bindings, optional `bundle_id`, `request_id`, `state`, `reason_code`, phase/iteration, creation time;
- question: concise `question`, `context`, `what_is_blocked`, and `decision_deadline` only when a real deadline exists;
- non-empty `options`, each with stable `option_id`, actionable `label`/`action`, consequence/trade-off (`cost`), and enough detail to execute;
- `recommended_option_id` plus a non-empty recommendation rationale;
- explicit `default_applicable: false` and `on_timeout: "no selection; run remains blocked"` fields; recommendation still requires an explicit response;
- a closed custom-decision envelope: `custom_decision_allowed`, a non-empty statement of permitted subject/value boundaries when true, and representation/size constraints;
- sensitivity guidance and accepted response modes.

The option catalog is request-local. No organization-specific global catalog should be added.

OS-30's Dependencies explicitly names the OS-29 `NEEDS_INPUT` / `CONFLICT` producer contract. Use the same request protocol for `CONFLICT` with conflict-specific context and mutually exclusive resolution options, while keeping AC1's mandatory `NEEDS_INPUT` creation as the regression lock.

### F4 — Explicit CLI ingestion and normalization must be separate operations

AC3/4/5/8 require a response event, not a default selection. A timeout, process exit, empty input, or omitted CLI call must leave the request unresolved.

Proposal: provide a non-interactive CLI in the installed tool:

```text
clarification create --run-id ... --ledger-key ... --input <request-json>
clarification respond --run-id ... --request-id ... (--option-id ... | --response-file ...)
clarification show --run-id ... --request-id ...
```

`--option-id` is the safest normal path. `--response-file` preserves exact bytes without exposing sensitive text in shell history/process arguments. If a convenience `--response` flag exists, docs must label it non-sensitive only. The tool must never prompt, read a TTY, invoke Orca `ask`, or infer acceptance from command timeout.

Normalization proposal:

- a valid explicit option ID normalizes to that option;
- free text that uniquely and deterministically maps under a documented closed rule normalizes to an allowed option;
- otherwise, free text normalizes to a first-class `CUSTOM` decision only when the request explicitly permits custom decisions and the submitted value is wholly inside its declared subject, type/shape, size, and safety envelope; the normalized object uses a tagged union (`kind: OPTION`, `option_id`, `action`) or (`kind: CUSTOM`, `custom_value_reference`, `bounded_by`) rather than forcing a custom answer into `option_id`;
- an answer outside that envelope, one with multiple plausible interpretations, or one that does not state an executable decision is `AMBIGUOUS`; it never creates a decision. Thus a novel but precise in-bounds answer is distinguishable from an unclear answer and an out-of-bounds request for scope;
- the response and normalized decision carry `source=explicit_user_reply`, capture mechanism/location, authenticated or declared `actor_id` plus actor type, `responded_at`, `normalized_at`, `resolves=<source ledger key>`, and response/decision identities so they can satisfy OS-28's user-decision evidence later;
- producing this decision artifact does not resume, dispatch, or change the already-blocked run. OS-31 owns consuming it in a new/durable resumed execution.

### F5 — Re-clarification must be bounded and durable

Proposal: use a fixed repository-wide maximum (for example two re-clarification revisions after the initial request), not an unbounded loop and not the phase correction counter. Each ambiguous response is preserved, then a new immutable request revision is created with narrowed context, the ambiguity explanation, the same executable option set (or an explicitly justified subset), and `reclarifies_request_id`. At the bound, publish an `AMBIGUOUS_RESPONSE_LIMIT_REACHED` outcome and remain unresolved; do not create a decision or approve the recommendation.

The exact numeric bound is a proposal to settle during design. It does not require user clarification because changing it within a bounded implementation is reversible, repository-local, and does not alter the Jira semantics; the chosen value must be explicit in contract/tests rather than hidden in code.

### F6 — Change, cancellation, and scope expansion require append-only lineage

AC7 says a changed answer supersedes the previous decision. Existing artifact policy says published records are immutable.

Proposal: a later explicit response creates a new response and normalized decision, then appends `decision_superseded` with item/decision/response IDs, actor, provenance, and timestamp. Explicit cancellation appends `decision_cancelled` referencing the effective decision and cancellation response; it produces no replacement decision and returns the item to an unresolved state. Scope expansion appends `decision_scope_expanded`, preserves the prior decision for its original bounded scope, creates stable new decision-item IDs and dependency edges for the added scope, and publishes those questions only under the independent-bundle/dependent-ordering rules. It must never silently reinterpret the old decision as approval of expanded work.

Earlier records remain byte-identical; readers derive `effective`, `superseded`, `cancelled`, and expanded-child states from lineage. Reject cycles, cross-run/cross-item links, nonexistent targets, self-supersession, multiple current heads, cancellation without an explicit response, and expansion that mutates or reuses an existing item identity. A changed answer that is ambiguous does not supersede the accepted decision until a new normalized decision exists.

This lineage belongs in the clarification namespace. OS-29's decision ledger must continue rejecting its reserved supersession fields.

### F7 — Sensitive raw responses need one authoritative restricted copy and redacted indexes

AC5 requires preservation while AC9 prohibits unlimited ordinary-log replication. `scripts/run_logging.py` is the relevant precedent: its versioned `redact_text()` policy is already byte-identical in the installed Skill, though it does not itself authorize logging a raw clarification response.

Proposal:

- preserve the exact original response once in `raw_response.txt`, mode `0600`, under its immutable response record; record its digest/byte count and sensitivity classification;
- make the JSON response record and normalized decision contain only the minimum normalized value and safe metadata; if the selected option itself is sensitive, store a redacted marker plus digest/reference rather than the raw value;
- never place raw response text in `ORCHESTRATOR_LOG.md`, `TIMING_LOG.md`, exceptions, CLI stdout, task specs, decision ledger, lineage summaries, or exports;
- ordinary logs record IDs, state/outcome, and redaction/sensitivity status only;
- use `--response-file` for sensitive input, do not echo it, and fail closed if safe persistence cannot be completed;
- document that run artifacts are not automatically staged or exported and that operators must treat the restricted raw file as sensitive.

The installed protocol tool may reuse its adjacent installed `run_logging.redact_text`; the repository copy may use the repository counterpart. Keep both shipped copies byte-identical and preserve the zero-uninstalled-dependency guarantee.

### F8 — Runtime integration ends at request publication

Proposal: define a runtime-neutral `HumanApprovalPort` contract whose operations publish/show requests and ingest explicit responses without depending on Orca terminal UI, transport, or resume machinery. In `scripts/orca_runtime_harness.py`, after authoritative terminal `NEEDS_INPUT`/`CONFLICT` results are known, the adapter orders dependency-ready items, partitions only mutually independent items into bounded request bundles, and invokes that port. The decision axis still wins over quality, the run still finishes `BLOCKED`, no correction/next-phase dispatch occurs, and no iteration is charged. Request-publication failure must never turn the decision into `CLEAR`.

Mirror the same deterministic behavior in `scripts/e2e_harness.py` without adding an agent subprocess, dispatch site, round kind, role, or run status. The CLI response path is exercised after the blocked result, as a separate artifact operation. It must not change the workflow result to `COMPLETED`.

### F9 — Documentation and validation must replace only the OS-30 limitations

Proposal:

- update orchestration `SKILL.md` with a machine-checkable clarification protocol contract, artifact layout, creation point, explicit response CLI, ambiguity bound, supersession, sensitive-data rules, and the explicit OS-31 no-resume boundary;
- update loop `SKILL.md` only where shared decision prose currently says OS-30 is absent, making the capability boundary accurate without claiming an orchestration ledger/artifact runtime it does not have;
- update `README`, `INSTALL`, `CHANGELOG`, roadmap, and compatibility statements;
- add the installed tool to `release_manifest.required_skill_paths`, installation file lists, portability tests, and archive assertions;
- preserve all historical artifacts and do not backfill old runs. An old blocked run with no clarification artifact remains historical evidence, not a malformed new request.

## Impact Scope

### Required implementation surfaces (proposal)

- New: `scripts/clarification_protocol.py` and `orca-worker-reviewer-orchestration/tools/clarification_protocol.py`.
- Runtime adapters: `scripts/orca_runtime_harness.py`, `scripts/e2e_harness.py`.
- Protocol contract: runtime-neutral `HumanApprovalPort` plus the Orca/runtime adapters that invoke it; no transport-specific approval UI.
- Tests: new `scripts/test_clarification_protocol.py`; focused additions to `test_e2e_harness.py`, `test_orca_runtime_contract.py`, `test_validate_skills.py`, `test_release_package.py`, and possibly `test_os29_decision_gate.py` to prove OS-29 boundaries remain unchanged.
- Fixtures: new `scripts/fixtures/clarification_protocol/{valid,invalid}/`; retain all existing decision-policy and decision-gate fixtures unchanged.
- Contracts/docs: both Skill files as scoped above, orchestration templates/review policy only if they need to tell agents what context a Coordinator will convert into options, plus `README.md`, `INSTALL.md`, `CHANGELOG.md`, `docs/ROADMAP.md`, and `docs/COMPATIBILITY.md`.
- Packaging: `scripts/release_manifest.py` and corresponding package tests.

### Surfaces that should not change

- OS-28 four states, reason codes, entry clauses, transition matrix, and forbidden authority sources.
- OS-29 ledger record schema/version and its rejection of OS-30 reserved fields.
- Existing run statuses, round kinds, Worker/Reviewer value vocabularies, dispatch cardinality, risk/quality/agent-profile independence, and iteration accounting.
- Existing historical artifacts, final-review audit schema, release history, and Jira status.
- No push, merge, release publication, deployment, Jira mutation, or transport-specific UI work.

## Dependencies / Constraints

- The normalized decision must be expressible as OS-28 user-decision evidence, but OS-31—not OS-30—will decide how a later execution consumes it and transitions an open item to `CLEAR`.
- The installed orchestration Skill must be self-contained after `cp -R`; any CLI named in `SKILL.md` must ship under `tools/` and may not depend on repository-only imports.
- Standard library only, Python 3.11-3.13.
- Artifact writes must be traversal-safe, collision-safe, immutable after publication, and fail closed on unknown schema or malformed lineage.
- Bundle size and custom-decision envelopes must be closed, explicit contract values; dependency ordering, cancellation, change, and expansion are derived from immutable lineage rather than implicit runtime state.
- Every accepted response or lifecycle event records validated provenance, actor identity/type, and an unambiguous timestamp; no anonymous synthetic default can satisfy that contract.
- CLI output and general logs must be deliberately sparse around sensitive input.
- The current untracked files are user/run-owned and outside the change scope.

## Acceptance Criteria and Fixture Matrix

| Jira AC / required scenario | Proposed fixture and assertion | Primary test owner |
| --- | --- | --- |
| AC1 structured request with stable ID | `valid/needs_input_request.json`; create from `worker_needs_input.json`, reopen twice, assert one published request and identical `request_id` bound to the same ledger key | `test_clarification_protocol.py`, runtime contract |
| AC2 actionable option + recommendation | valid one-option and multi-option fixtures; reject empty options, duplicate option IDs, non-actionable entries, absent/unknown recommendation, empty trade-off/context | protocol unit tests |
| AC3 recommendation is not approval | create request and do nothing; assert no response/decision, unresolved status; submit explicit non-recommended option and assert that exact option wins | protocol + E2E |
| AC4 timeout/no response is not approval | fixtures for no CLI call, explicit timeout marker, empty response, process timeout; assert no normalized decision and no supersession | protocol + E2E |
| AC5 raw + normalized retained | explicit option and uniquely mapped free-text fixtures; assert byte-exact restricted raw file, digest, response record, normalized decision, and cross-links | protocol unit tests |
| AC6 ambiguous bounded re-clarification | zero-match and multi-match text fixtures; assert preserved ambiguous response, request revision lineage, fixed maximum, terminal `AMBIGUOUS_RESPONSE_LIMIT_REACHED`, and no decision | protocol + E2E |
| AC7 changed answer supersedes with lineage | answer A then B; assert two immutable decisions, append-only supersession event, one derived current head, byte-identical A, and reject cycle/fork/cross-run/self links | protocol unit tests |
| AC8 artifact/CLI, no terminal UI | invoke CLI with `--option-id` and `--response-file` under closed stdin/no TTY; static/assertion tests prohibit `input()`, TUI/Orca ask, and extra agent subprocess/dispatch | protocol, OS-29 residue tests |
| AC9 sensitive response not copied to logs | canary secret via `--response-file`; assert exactly one restricted raw artifact contains it and it is absent from JSON, stdout/stderr, decision ledger, orchestration/timing logs, task specs, lineage, and exports | protocol, run logging, E2E/runtime contract |
| Stable/correct schemas | invalid schema version/type, unknown/missing/extra keys, bool-as-int, malformed JSON, path traversal IDs, empty payload, collision, partial staging publication | protocol unit tests |
| OS-28 compatibility | normalized decision carries complete `user_decision` (`explicit_user_reply`, location, resolved ledger key); validate transition semantics without modifying policy contract | decision-policy/protocol tests |
| OS-29 compatibility | existing `record_carries_os30_supersession.json` remains invalid; ledger schema/version/constants unchanged; no new gate/round/status/role/dispatch site | `test_os29_decision_gate.py` |
| OS-31 boundary | after response/decision creation, blocked workflow result, ledger open item, command delta, and phase iteration count remain unchanged; no automatic dispatch/resume | E2E + runtime contract |
| Packaging/portability | installed copy contains CLI and runs with repository `scripts/` unavailable; tool copies byte-identical; deterministic archive contains both | release/package tests |
| Historical compatibility | fixture with pre-OS-30 blocked run and absent clarification directory remains readable as legacy/absent, never backfilled or treated as approval | compatibility/protocol tests |
| Independent bundle bound | fixtures for one request containing independent items at/below the maximum; reject oversized, duplicate, ancestor/descendant, or falsely independent bundles | protocol + E2E ordering tests |
| Dependent request ordering | dependency-chain and DAG fixtures; assert successors are unpublished until predecessor decisions are effective, while ready independent siblings may share a bounded bundle | protocol + E2E ordering tests |
| Bounded custom decision | accept a precise in-envelope free-text decision as tagged `CUSTOM`; retain its raw response and bound reference without inventing an option ID | protocol normalization tests |
| Out-of-bounds custom / ambiguity | reject custom text outside declared type/subject/size/safety bounds; separately classify multi-interpretation or non-executable text as `AMBIGUOUS` and trigger bounded re-clarification | protocol normalization tests |
| Provenance / actor / time | fixtures for explicit option, custom, change, cancellation, and expansion assert capture source/location, actor ID/type, response time, normalization/event time; reject missing/malformed values | protocol schema tests |
| Cancellation / change / expansion | append cancellation, supersession, and scope-expansion events; prove prior bytes unchanged, cancellation unresolved, changed decision single-headed, and expanded children have new stable IDs/dependencies | protocol lineage + E2E tests |
| Runtime-neutral port | contract fake implements `HumanApprovalPort`; both harnesses use the same operations without TTY/transport imports, and response ingestion causes zero resume/dispatch delta | port contract + runtime tests |

## Risks

- **Boundary collapse:** putting request/response fields into the OS-29 ledger would undo an explicitly tested separation. Mitigation: separate namespace and schemas linked by ledger key.
- **Accidental resume:** treating normalized response as immediate authority inside the current harness would implement part of OS-31 and violate the current terminal-run contract. Mitigation: artifact-only response ingestion and tests proving command/dispatch delta zero.
- **Recommendation laundering:** a defaulted option on timeout or ambiguity would violate AC3/4 and OS-28. Mitigation: only an explicit option, unique closed-rule option match, or explicit in-envelope bounded custom response creates a decision.
- **Bundle/dependency inversion:** grouping dependent questions can solicit an answer against context that a predecessor changes. Mitigation: explicit dependency validation, a small fixed bundle bound, and publish-only-when-ready tests.
- **Scope laundering:** treating cancellation or expansion as ordinary supersession can retain authority beyond what the actor approved. Mitigation: distinct lineage events and new stable item IDs for expanded scope.
- **Secret proliferation:** raw response in argv, JSON, exception strings, or Markdown logs can spread through retained artifacts and CI. Mitigation: response-file path, one restricted raw copy, safe metadata only, canary scans.
- **Lineage mutation/races:** editing the previous decision or maintaining an unguarded mutable head can lose history under concurrent responses. Mitigation: immutable decisions plus atomic append-only lineage and single-head validation.
- **Duplicate requests:** multiple adapters can observe the same terminal block. Mitigation: stable decision-item binding and idempotent first-writer-wins request lookup.
- **Protocol overreach:** an organization-wide option catalog or transport adapter would increase scope and coupling. Mitigation: request-local options and CLI/artifact boundary only.
- **Documentation drift:** two Skills share decision semantics but only one has the executable gate/artifact runtime. Mitigation: validator-enforced shared semantics plus orchestration-only lifecycle contract.

## Assumptions / Unknowns

- No open user decision is required to proceed to plan/design. Jira fixes the semantics; remaining choices such as the exact re-clarification count and ID encoding are reversible, repository-local design choices that must be made explicit and tested.
- The phrase “user-required fixture” in the dispatch is interpreted as requiring a fixture for every Jira acceptance scenario. No additional fixture text was supplied outside the nine ACs.
- `CONFLICT` support is required by OS-30's Dependencies on the OS-29 `NEEDS_INPUT` / `CONFLICT` producer contract; AC1 retains the narrower mandatory regression for `NEEDS_INPUT` creation.
- File mode `0600` reduces ordinary exposure but is not encryption. If policy later requires encrypted-at-rest raw responses, that is a separate security decision and likely an external dependency; current AC9 only forbids unlimited replication into general logs.

## Recommended Next Step

Proceed to PLAN with the separate clarification namespace and runtime-neutral `HumanApprovalPort` as the working architecture. Plan the stable identities and complete request schema first; bounded independent bundling/dependency scheduling and option/custom normalization second; provenance plus cancellation/change/expansion lineage third; harness adapters fourth; then docs, packaging, and the full validation matrix. Preserve the OS-29 closed ledger schema and prove that creating, cancelling, changing, or expanding a decision artifact never resumes the blocked run; OS-31 remains the sole resume boundary.

## Changes

Analysis only. No production code, tests, contracts, documentation, Jira state, or historical artifact was modified.

## Modified Files / Artifacts

- `artifacts/runs/run_db374a3fd83a/ANALYSIS.md` — this run-scoped analysis artifact.

## Validation

- Verified no repository-owned `AGENTS.md` exists.
- Inspected both Skill contracts, both analysis templates and review policies, OS-28 policy parser/fixtures, OS-29 gate/ledger/schema/fixtures, both deterministic and Orca runtime harnesses, release manifest/build/package tests, changelog, roadmap, compatibility, install, and release rules.
- Confirmed the OS-30 reserved fields remain excluded from the OS-29 closed ledger schema.
- Confirmed the artifact stays under the dispatched run root and contains no implementation changes.

## Unit Tests / Testing Strategy

No tests were run because this phase is analysis-only. The required future test strategy is the acceptance/fixture matrix above plus the repository's complete validation and release checks.

## Review Feedback Resolution

- **F-001 — RESOLVED.** The authoritative source now includes full Jira Scope and Dependencies. Findings, impact, constraints, risks, next step, and fixture matrix now cover bounded independent bundles, sequential dependent requests, cancellation/change/scope expansion, provenance/actor/timestamps, and the runtime-neutral `HumanApprovalPort` contract.
- **F-002 — RESOLVED.** Bounded custom decision is a first-class tagged normalization result governed by a closed request-declared envelope; it is explicitly distinguished from option matching, out-of-bounds input, and ambiguous input, with dedicated fixtures.
- **N-001 — RESOLVED.** `CONFLICT` is grounded in OS-30's explicit OS-29 producer dependency rather than inferred symmetry.
- **N-002 — RESOLVED.** The redaction precedent now points to the shipped, byte-identical `run_logging.redact_text` policy.
- **N-003 — RESOLVED.** Request schema now carries explicit `default_applicable` and `on_timeout` fields.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B2",
  "evidence": {},
  "grounds": "The full Jira Scope, acceptance criteria and Dependencies, the phase contract, reviewer findings and repository evidence fully determine this corrected analysis; no user-owned choice is open.",
  "iteration": 2,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "analysis",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T08:10:00+00:00",
  "responsible_phase": "analysis",
  "role": "worker",
  "run": "run_db374a3fd83a",
  "scope": "Corrected analysis iteration 2 for Jira OS-30 only, resolving F-001 and F-002 while preserving OS-28, OS-29, and OS-31 boundaries.",
  "sequence": 3,
  "source": "worker",
  "source_binding": "artifacts/runs/run_db374a3fd83a/ANALYSIS.md",
  "state": "CLEAR",
  "verdict": "",
  "verifies": null
}
```
