# Worker Result

STATUS: COMPLETE

## Goal

Implement Jira OS-30 end to end on `feat/os-30-structured-clarification`: a standard-library, runtime-neutral Structured Human Clarification and Decision Protocol for OS-29 `NEEDS_INPUT` and `CONFLICT` results. It must create immutable structured requests, accept explicit CLI responses, retain original and normalized evidence, support bounded re-clarification and append-only lifecycle lineage, fail closed around malformed or sensitive data, ship with both Skills at their truthful capability boundary, and leave durable resume/transports to OS-31.

## Scope / Out of Scope

In scope:

- A separate run-scoped clarification namespace and closed schemas for requests, responses, normalized decisions, and lineage events, linked one way to the unchanged OS-29 `ledger_key`.
- Stable `decision_item_id`, request revision/`request_id`, `response_id`, `decision_id`, and optional bounded `bundle_id`; traversal-safe, collision-safe, atomic, immutable publication.
- Request generation from Coordinator/adapter-supplied clarification input. Each item declares its dependencies and independence in the clarification namespace—not the closed OS-29 ledger—and supplies: source state/reason, phase/iteration, question, context, `what_is_blocked`, actionable options with IDs/actions/trade-offs, recommendation and rationale, `default_applicable: false`, `on_timeout: no selection; run remains blocked`, optional real deadline, accepted response modes, sensitivity guidance, and a closed custom-decision envelope.
- A fixed maximum bundle size of **3** independent, dependency-ready items. Reject duplicates, oversize bundles, cycles, ancestor/descendant co-membership, and undeclared independence; publish dependent items sequentially only after predecessors have effective decisions.
- CLI/artifact operations `clarification create`, `respond`, and `show`. Prefer `--option-id`; accept exact bytes through `--response-file`; any direct text convenience is documented as non-sensitive. Commands never prompt, call Orca `ask`, use a TTY, infer consent, resume a run, or dispatch an agent.
- Deterministic normalization to a tagged `OPTION` or request-bounded `CUSTOM` decision. A unique closed-rule option match is accepted; a custom value is accepted only when explicitly allowed and wholly within declared subject/type/shape/size/safety bounds. Multiple interpretations, non-executable text, or out-of-envelope input is `AMBIGUOUS`/invalid and creates no decision.
- A maximum of **2 re-clarification revisions after the initial request**. Preserve each ambiguous response; issue a narrowed immutable revision with lineage; at the bound record `AMBIGUOUS_RESPONSE_LIMIT_REACHED` and remain blocked.
- Actor/timestamp/provenance on every accepted response and lifecycle event: authenticated-or-declared actor ID/type, `source=explicit_user_reply`, capture mechanism/location (`where_recorded`), `responded_at`, `normalized_at`/event time, and `resolves=<ledger_key>`.
- Append-only `decision_superseded`, `decision_cancelled`, and `decision_scope_expanded` lineage. Changes create a new response/decision before superseding; cancellation requires an explicit response and leaves the item unresolved; expansion preserves the original bounded decision, mints new item IDs/dependency edges, and never widens old approval. Reject missing/cross-run/cross-item/self targets, cycles, forks, multiple heads, stale revisions, and malformed events.
- Idempotent duplicate handling: replay of the same create/submission identity returns the already-published object only when content matches byte-for-byte/semantically as specified; conflicting reuse fails closed. Stale responses to non-current request revisions and duplicate or out-of-order lifecycle events are retained where evidentiary but cannot alter the effective head.
- One authoritative raw response copy at mode `0600`, with digest/byte count and sensitivity classification; sparse/redacted JSON, stdout/stderr, errors, general logs, ledgers, task specs, lineage summaries, and exports. Persistence/redaction uncertainty fails closed before normalization or authority.
- Runtime adapters in both harnesses through a `HumanApprovalPort` protocol exposing publish/show/ingest operations without Orca, terminal, TTY, transport, or resume dependencies. Runtime integration ends after publishing requests from authoritative blocked results; the workflow stays `BLOCKED`, incurs no correction/phase dispatch or iteration, and response ingestion has zero dispatch/command/status delta.
- Fixtures/regressions for every Jira AC and Scope rule; packaging, install, compatibility, roadmap, README, changelog, and both-Skill documentation; preservation of all historical/untracked artifacts without migration or backfill.
- Full validation, then commit, push the feature branch, and open a PR describing OS-28/29 compatibility, OS-31 exclusions, validation evidence, and security behavior.

Out of scope:

- OS-31 durable resume, cross-session continuation, or consuming a stored decision to transition/dispatch a later execution.
- Terminal UI, Orca `ask`, web/chat/Slack/Jira transports, notification delivery, and organization-specific/global option catalogs.
- Changes to OS-28 states/transitions/authority rules, OS-29 ledger schema/version/reserved-field rejection, workflow statuses/round kinds/roles/dispatch cardinality/iteration accounting, historical run contents, release publication/deployment, Jira mutation, or merge.

## Work Items

1. **Freeze protocol constants and schemas.** Add `scripts/clarification_protocol.py` with closed versioned schemas and constants `MAX_BUNDLE_ITEMS = 3` and `MAX_RECLARIFICATION_REVISIONS = 2`. Define the runtime-neutral `HumanApprovalPort` using plain Python values/protocol types. Specify strict IDs, UTC timestamps, actor/provenance, request option/custom envelopes, response/decision tagged unions, lifecycle events, effective-head derivation, and explicit unknown/missing/extra/type rejection (including bool-as-int).
2. **Build the immutable artifact store and request generator.** Create the `clarifications/{requests,responses,decisions,lineage,.staging}` layout using stage/fsync/atomic rename/no overwrite. Bind each item directly to the already run-qualified OS-29 `ledger_key` (no redundant `run_id + ledger_key` derivation). Accept dependency/independence declarations only from validated Coordinator/adapter input to `clarification create`; validate the DAG and bundle bound before publication. Implement stable first-writer-wins identities, matching replay, conflicting duplicate rejection, and legacy absence as valid history.
3. **Implement response ingestion and normalization.** Add non-interactive create/respond/show CLI entry points. Persist raw bytes safely before processing, then normalize exact option IDs, deterministic unique text matches, or explicitly bounded custom values. Record complete actor/time/provenance, preserve both raw response and normalized object, and ensure recommendation/default/timeout/no-response/empty input never supplies authority.
4. **Implement ambiguity and lifecycle lineage.** Generate at most two narrowed re-clarification revisions; keep the same decision item and executable choices unless an explicit justified subset is recorded. Add change/supersede, cancel, and scope-expand operations/events with immutable history and single-head derivation. Validate stale, duplicate, malformed, cross-run, cycle, fork, and scope-reuse cases fail closed without mutating effective authority.
5. **Enforce the sensitive boundary.** Reuse the adjacent `run_logging.redact_text` policy without creating an unshipped dependency. Store one mode-`0600` raw copy, digest/count/reference metadata, and redacted sensitive normalized values. Make exceptions and CLI output ID/status-only; add a canary-secret scan over all ordinary artifacts/logs/exports. Abort publication/normalization when safe storage, permissions, validation, or redaction cannot be guaranteed.
6. **Integrate adapters without resume behavior.** Update `scripts/orca_runtime_harness.py` and `scripts/e2e_harness.py` to translate authoritative `NEEDS_INPUT`/`CONFLICT` results plus Coordinator declarations into dependency-ready bundles and call `HumanApprovalPort`. Preserve B1/B2/B3 ordering, decision-over-quality priority, `BLOCKED` settlement, subprocess/agent count, dispatch sites, status vocabulary, and iteration totals. Exercise CLI ingestion only after settlement and assert it cannot resume or complete the run.
7. **Create the installed twin and two-Skill parity.** Copy the finalized module byte-identically to `orca-worker-reviewer-orchestration/tools/clarification_protocol.py`; ensure it runs after `cp -R` with repository `scripts/` unavailable. Update orchestration `SKILL.md` with the executable contract, paths, bounds, lifecycle/security rules, and OS-31 boundary. Update loop `SKILL.md` with identical shared semantics and explicit statement that its direct-session mode has no orchestration artifact/CLI runtime; validate shared policy text for semantic parity without claiming feature parity.
8. **Add fixtures and focused regressions.** Create `scripts/fixtures/clarification_protocol/{valid,invalid}` and `scripts/test_clarification_protocol.py`. Cover AC1-AC9 plus: `NEEDS_INPUT` and `CONFLICT`; exact request fields (`what_is_blocked`, `default_applicable: false`, timeout behavior); bundle 1/3/4 and dependency DAG ordering; option/custom/ambiguous/out-of-bounds normalization; actor/time/provenance; two-revision ambiguity exhaustion; change/cancel/expand lineage; duplicate/stale/malformed/collision/traversal/partial-staging handling; sensitive canary isolation; legacy absent directory; and concurrency/single-head behavior.
9. **Lock compatibility in existing suites.** Extend harness/runtime tests for request publication and zero resume/dispatch/iteration delta. Keep `record_carries_os30_supersession.json` invalid and assert OS-28 policy and OS-29 closed fields/schema/constants, roles/statuses/rounds/dispatch cardinality remain unchanged. Add static no-TTY/no-`input()`/no-Orca-ask checks and prove the installed copy is byte-identical and repository-independent.
10. **Package and document.** Add the installed tool to `scripts/release_manifest.py`, install file lists, portability/archive assertions, and relevant validation tests. Update `README.md`, `INSTALL.md`, `CHANGELOG.md`, `docs/ROADMAP.md`, and `docs/COMPATIBILITY.md`; document artifact paths, CLI examples, bounds, sensitive-file handling, legacy preservation, two-Skill boundary, and OS-31/transports as out of scope. Do not rewrite prior changelog entries or old run artifacts.
11. **Validate, review, and deliver.** Run focused protocol, harness, runtime, OS-28/29, validation, and packaging tests; then the full Python 3.11-3.13-compatible unittest suite, skill validator, package verifier, deterministic release build/archive verification, and `git diff --check`. Inspect status/diff to exclude historical and unrelated untracked files, commit only OS-30 changes, push `feat/os-30-structured-clarification`, and open a PR with Scope/AC mapping, risk/boundary notes, and exact validation results. Do not publish a release or merge.

## Dependencies / Execution Order

`1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11` is the safe critical path. Schema/identity and numeric bounds must stabilize before fixtures or installed parity; secure persistence precedes runtime exposure; harness integration precedes OS-31 boundary assertions; documentation/packaging follows the actual CLI. Focused tests run continuously from item 2 onward, while full validation, branch push, and PR occur only after all code/docs/package diffs are reviewed.

## Validation / Test Plan

- Focused: `python -m unittest scripts.test_clarification_protocol scripts.test_e2e_harness scripts.test_orca_runtime_contract scripts.test_os29_decision_gate scripts.test_validate_skills scripts.test_release_package` (adjust only to the repository's actual unittest module invocation if discovery requires it).
- Contract fixtures: validate every valid fixture, reject every invalid fixture, and assert all AC/Scope scenarios named in Work Item 8. Directly lock request defaults/timeout/blocked text, both producer states, bundle/dependency rules, and all normalization/lineage/provenance cases.
- Security: submit a unique canary via `--response-file`; verify exact bytes occur only once in the restricted raw artifact, mode is `0600`, and the canary is absent from stdout/stderr, JSON, OS-29 ledger, orchestration/timing logs, task specs, lineage summaries, exceptions, and exported/package content.
- Boundary: prove response ingestion changes no run status, OS-29 ledger head, dispatch/command count, phase iteration, role/round vocabulary, or agent process count; old blocked runs without `clarifications/` remain readable and untouched.
- Parity/portability: compare repository and installed tool bytes; run the installed CLI with repository paths removed; validate both Skills and their shared semantic assertions.
- Repository gates: run `python scripts/validate_skills.py`, full `python -m unittest discover` per repository convention, `python scripts/verify_package.py`, release build plus archive verification per `docs/RELEASING.md`, and `git diff --check`. Record exact commands/results in the PR.
- Delivery: verify `git status --short` and staged diff contain only intended OS-30 files, confirm historical artifacts are byte-unchanged/untracked as before, push the named branch, and verify the PR base/head and description.

## Risks

- **OS-29 boundary erosion:** reject all OS-30 fields in its ledger and keep the new namespace one-way linked by `ledger_key`.
- **Implicit approval or accidental OS-31:** require explicit CLI evidence and assert no response, recommendation, default, timeout, ambiguity, or normalized artifact resumes execution.
- **Dependency/bundle error:** accept declarations only from the Coordinator/adapter, validate the complete DAG, cap bundles at three, and publish successors only when ready.
- **Secret replication:** raw-file input, one restricted copy, sparse output, canary scans, and fail-closed persistence/redaction.
- **Lineage races or stale writes:** immutable objects, atomic publication, idempotency keys, revision checks, and derived single-head validation.
- **Two-copy drift:** implement once, copy byte-identically, and enforce equality plus installed-only execution in tests.
- **Scope creep:** no transports, global catalog, resume engine, release, Jira update, or historical migration; PR must call these out explicitly.

## Completion Criteria

- All Jira OS-30 Scope bullets and AC1-AC9 have named passing fixtures/regressions, including both OS-29 producer states and every request field.
- The runtime-neutral port, artifact layout, CLI response path, fixed bounds, normalization, provenance, lifecycle lineage, duplicate/stale/malformed behavior, and sensitive fail-closed boundary operate as specified.
- OS-28 and OS-29 contracts remain unchanged; blocked runs do not resume and OS-31/transports remain absent.
- Repository and installed tool copies are byte-identical and portable; orchestration and loop Skills state matching semantics with accurate capability boundaries.
- Historical/user-owned artifacts are untouched; docs and packaging are current; all focused/full/release validation passes.
- Intended changes alone are committed and pushed on `feat/os-30-structured-clarification`, and a PR against the correct base contains AC mapping and validation evidence. Release and merge remain pending human action.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: Jira OS-30, the approved analysis/review, repository release rules, and reversible repository-local choices fully determine this executable plan. The plan fixes bundle size at three and re-clarification revisions at two as explicit tested defaults; no user-owned decision is open.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B2",
  "evidence": {},
  "grounds": "Jira OS-30, the approved analysis and review, and repository contracts fully determine an executable plan; the selected numeric bounds are reversible repository-local defaults and no user-owned choice remains open.",
  "iteration": 1,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "plan",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T09:00:00+00:00",
  "responsible_phase": "plan",
  "role": "worker",
  "run": "run_db374a3fd83a",
  "scope": "Executable minimal implementation and delivery plan for Jira OS-30 only, preserving OS-28/OS-29 and excluding OS-31 resume and transports.",
  "sequence": 5,
  "source": "worker",
  "source_binding": "artifacts/runs/run_db374a3fd83a/PLAN.md",
  "state": "CLEAR",
  "verdict": "",
  "verifies": null
}
```
