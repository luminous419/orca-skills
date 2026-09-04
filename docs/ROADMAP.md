# Orca Skills Roadmap

This document describes the direction of `orca-skills`: why the project exists,
the architecture it is moving toward, its current position, and how work is
prioritized. Jira remains the source of truth for individual issue status and
acceptance criteria; this roadmap is not a copy of the Jira backlog.

## Vision

`orca-skills` provides reusable, auditable software-development workflows that
advance autonomously from requirements analysis through implementation and
verification while they remain inside an explicit decision boundary. One agent
produces work and an independent agent verifies it before the workflow advances.
When user intent, authority, or a high-impact choice cannot be derived safely,
the workflow escalates a minimal structured question, records the decision, and
resumes from the interrupted phase.

The goal is **bounded autonomy with principled escalation**, not zero user
interaction. A workflow that stops at the correct decision boundary is behaving
more safely than one that silently guesses. The project therefore aims to
eliminate unnecessary intervention while preserving necessary human authority,
without hiding execution state, weakening quality gates, or relying on an
in-memory conversation as evidence.

The long-term outcome is a compact verification-oriented orchestration system:

- deterministic workflow and review contracts;
- explicit Worker, Reviewer, and Final Reviewer responsibilities;
- observable Run, Task, Dispatch, artifact, and timing provenance;
- project-specific quality and agent configuration separated from lifecycle policy;
- safe recovery and evidence whose validity is bound to the source it reviewed;
- execution adapters that preserve one policy rather than reimplementing it.

## Bounded Autonomy Model

Decision checks apply continuously across ANALYSIS, PLAN, DESIGN,
IMPLEMENTATION, TEST, and Final Review. A check is not itself a user pause. It
produces one of four states:

| State | Meaning | Workflow action |
| --- | --- | --- |
| `CLEAR` | No unresolved decision crosses the autonomy boundary | Continue |
| `ASSUMPTION_ALLOWED` | A safe, reversible, policy-supported assumption is recorded | Continue and review |
| `NEEDS_INPUT` | Correctness depends on user intent or authority | Pause and ask a structured question |
| `CONFLICT` | Requirements or accepted decisions cannot all be satisfied | Pause and request resolution |

The decision boundary considers ambiguity, contradiction, reversibility, blast
radius, cost, security, privacy, compliance, long-term lock-in, existing project
policy, and explicit user authority. Model confidence alone is not authority.
Risk, Quality Profile, and Agent Profile remain separate axes and cannot silently
expand what the workflow is allowed to decide.

Success is not measured by maximizing question-free runs. The relevant outcomes
include required-escalation recall, unnecessary-question rate, silent-assumption
defects, valid autonomous decisions, decision-provenance completeness, resume
reliability, and final requirement satisfaction.

## Architecture Principles

1. **Independent verification is a gate.** A Reviewer does not repair its own
   findings, and completion requires the applicable phase gates plus the Final
   Adversarial Review.
2. **Autonomy stops at authority boundaries.** Safe, reversible, policy-supported
   choices may proceed with recorded assumptions; conflicting requirements and
   high-impact choices that depend on user intent require explicit resolution.
3. **A correct escalation is a valid workflow outcome.** `NEEDS_INPUT` and
   `CONFLICT` are durable states, not failures to disguise or invitations to
   guess. A response resumes the responsible phase without bypassing review.
4. **Fail closed when provenance is uncertain.** Missing runtime state, malformed
   configuration, unknown review provenance, and unsupported phase combinations
   are reported rather than silently inferred.
5. **Policy and execution are separate.** Workflow, risk, quality, and review
   policy must not be coupled to one model name, CLI wrapper, or user path.
6. **Deterministic checks precede subjective claims.** Validators, tests, manifests,
   and machine-readable contracts protect invariants that should not depend on an
   LLM's interpretation.
7. **Artifacts are evidence, not conversation residue.** Run-scoped artifacts and
   append-only logs are the durable record; historical evidence is not rewritten
   to make a newer implementation look consistent.
8. **Safety is independent of validation depth.** Risk settings may change review
   strength, but never remove mandatory test gates or authorize unsafe lifecycle
   cleanup.
9. **Point verification is stated narrowly.** A successful run on one Orca version
   or company environment does not imply support for an untested range.
10. **Prefer a small role vocabulary.** Worker, phase Reviewer, and fresh Final
   Reviewer remain the primary roles; additional complexity must demonstrate value
   without multiplying handoff loss.

## Current Architecture

Two Skill variants share one development policy:

```text
Direct-session Skill                 Orca-native Skill
Worker                               Run / Task / Dispatch
  -> Reviewer                          -> Worker
  -> correction loop                   -> phase Reviewer
                                        -> correction loop
                                        -> Final Adversarial Reviewer
```

- `orca-worker-reviewer-loop` is the direct-session baseline.
- `orca-worker-reviewer-orchestration` adds Orca-native state, risk-based
  validation, run-scoped evidence, agent profiles, quality profiles, and a
  mandatory Final Adversarial Review.
- Shared templates, review policy, and deterministic parsers keep the two Skill
  packages aligned where their contracts are intentionally the same.

## Target Architecture

The target architecture keeps the current Worker/Reviewer primitive while
strengthening the surrounding control and evidence layers:

```text
Task intent
  -> workflow, risk, and decision-policy resolution
  -> continuous decision / escalation checks
  -> structured human clarification when required
  -> durable wait and responsible-phase resume
  -> deterministic contract sensors
  -> Worker / Reviewer phase gates
  -> source-bound Final Review evidence
  -> durable run state and resumable execution
  -> release and learning feedback
```

The target is incremental. The bounded-autonomy decision contract, continuous
phase gates, structured clarification, durable decision resume, adaptive workflow
composition, source-bound review receipts, and a harness-neutral core are not
current behavior until their own Jira issues and acceptance criteria are
implemented and validated.

## Current Status

The repository remains pre-1.0; [`VERSION`](../VERSION) is the version source of
truth. The following foundations are in place as of the OS-20 roadmap baseline:

- project-specific quality profiles and simplified review gates;
- run-scoped artifact lifecycle;
- risk-based validation strength;
- agent profile and role separation;
- run-scoped orchestration and timing logs with corrected timestamp handling;
- validated Final Adversarial Review effectiveness;
- immutable Final Review audit records, evidence export, and evaluation tooling.

The current company-environment GLM/Gemma result is a historical point
verification, while broader compatibility and stable release readiness remain
explicitly unclaimed. See [Compatibility and Verification Status](COMPATIBILITY.md).

## Milestones

Milestones express outcomes and ordering. Jira determines the live status of the
issues linked below.

### Milestone 0 — Verification foundation

**Status: completed.** Establish the reusable policy, execution boundaries, and
evidence needed before expanding the workflow.

- [OS-1](https://luminous419.atlassian.net/browse/OS-1) Project-Specific Quality Profile
- [OS-2](https://luminous419.atlassian.net/browse/OS-2) Artifact Lifecycle / Run Directory
- [OS-3](https://luminous419.atlassian.net/browse/OS-3) Risk-Based Workflow
- [OS-4](https://luminous419.atlassian.net/browse/OS-4) Agent Mode / Profile Separation
- [OS-17](https://luminous419.atlassian.net/browse/OS-17) Run-scoped logs
- [OS-19](https://luminous419.atlassian.net/browse/OS-19) Timing correctness
- [OS-21](https://luminous419.atlassian.net/browse/OS-21) Final Review effectiveness validation
- [OS-22](https://luminous419.atlassian.net/browse/OS-22) Final Review observability and evaluation foundation

### Milestone 1 — Bounded autonomy and principled escalation

**Status: next.** Establish the decision boundary that lets routine work proceed
without intervention while making necessary human choices explicit, durable, and
resumable. Every phase performs a decision check, but only `NEEDS_INPUT` and
`CONFLICT` pause for the user.

- [OS-28](https://luminous419.atlassian.net/browse/OS-28) Bounded Autonomy Decision Policy Contract
- [OS-29](https://luminous419.atlassian.net/browse/OS-29) Continuous Decision and Escalation Gates — **implemented** in the orchestration Skill: the OS-28 policy is enforced at phase entry, after the Worker result and after the Reviewer result, `NEEDS_INPUT`/`CONFLICT` block the correction Worker and the next phase without consuming an iteration, and every judgement is recorded in a run-scoped append-only decision ledger. A blocked run terminates and is not resumable. Structured human clarification (OS-30) is implemented on this branch; durable cross-session resume (OS-31) is not yet implemented.
- [OS-30](https://luminous419.atlassian.net/browse/OS-30) Structured Human Clarification and Decision Protocol
- [OS-31](https://luminous419.atlassian.net/browse/OS-31) Durable Pause and Resume for Human Decisions
- [OS-32](https://luminous419.atlassian.net/browse/OS-32) Bounded Autonomy Evaluation and Success Metrics

The intended dependency flow is OS-28 → OS-29 → OS-30 → OS-31 → OS-32.
Jira issue links are authoritative for the exact dependency graph. OS-28 relates
to the deterministic engine/adapter analysis in OS-27; OS-31 concretizes the
durable-resume candidate in OS-26; OS-32 reuses the audit/evaluation foundation
from OS-22.

### Milestone 2 — Review quality and accountable handoff

**Status: planned.** Improve what the review detects, prove reviewer behavior,
and make correction ownership and human PR handoff explicit. Work that does not
compete with the Milestone 1 decision-policy foundation may proceed independently.

- [OS-23](https://luminous419.atlassian.net/browse/OS-23) Final Review Detection Quality Improvement
- [OS-24](https://luminous419.atlassian.net/browse/OS-24) Final Review Behavior Validation
- [OS-25](https://luminous419.atlassian.net/browse/OS-25) Responsible Phase and Correction Ownership
- [OS-5](https://luminous419.atlassian.net/browse/OS-5) Human PR Review Integration
- [OS-18](https://luminous419.atlassian.net/browse/OS-18) Adaptive iteration budget

### Milestone 3 — Operability and efficiency

**Status: planned.** Make long-running workflows easier to observe and operate
while reducing unnecessary review and artifact cost.

- [OS-6](https://luminous419.atlassian.net/browse/OS-6) Progress / Idle / Nudge
- [OS-7](https://luminous419.atlassian.net/browse/OS-7) Telemetry / Observability
- [OS-8](https://luminous419.atlassian.net/browse/OS-8) Artifact Retention Policy
- [OS-9](https://luminous419.atlassian.net/browse/OS-9) Reviewer Efficiency Phase 2
- [OS-10](https://luminous419.atlassian.net/browse/OS-10) Final Review Cost Optimization

### Milestone 4 — Workflow evolution and architecture

**Status: planned and exploratory.** Improve maintainability and workflow entry
points, and compare alternative execution architectures without weakening the
current contract.

- [OS-11](https://luminous419.atlassian.net/browse/OS-11) Skill Structure Refactoring
- [OS-12](https://luminous419.atlassian.net/browse/OS-12) Workflow Presets
- [OS-13](https://luminous419.atlassian.net/browse/OS-13) VirtusLab Orca Comparison PoC
- [OS-16](https://luminous419.atlassian.net/browse/OS-16) Non-Orca orchestration alternatives
- [OS-27](https://luminous419.atlassian.net/browse/OS-27) Deterministic flow execution and Orca adapter architecture

### Milestone 5 — Environment validation and stable release

**Status: planned.** Revalidate the latest Skill in the company environment,
complete repository information architecture, and make an explicit 1.0 decision.

- [OS-14](https://luminous419.atlassian.net/browse/OS-14) Company GLM/Gemma Latest Skill Validation
- [OS-20](https://luminous419.atlassian.net/browse/OS-20) Roadmap and Documentation Structure Cleanup
- [OS-15](https://luminous419.atlassian.net/browse/OS-15) Release Readiness

## Priority Model

| Priority | Meaning | Typical evidence required |
| --- | --- | --- |
| **P0** | Trust, correctness, or durable-state prerequisite | Explicit invariants, regression tests, and recovery/evidence semantics |
| **P1** | High-impact capability or quality improvement | End-to-end acceptance criteria and adversarial review |
| **P2** | Operability, efficiency, and bounded optimization | Before/after evidence without weakening safety or review gates |
| **P3** | Later architecture, validation, documentation, or release work | Dependency check, compatibility evidence, and full repository validation |

Priority is not execution order by itself. Dependencies, newly discovered risk,
and evidence from completed milestones can move an item. Jira is authoritative
when a ticket's current priority differs from this high-level model.

## Discovery Candidates

[OS-26](https://luminous419.atlassian.net/browse/OS-26) preserves an AI-DLC v2
architecture comparison. Its current candidates are:

- **P0 candidates:** durable Run state/resume and source-bound review receipts;
- **P1 candidates:** adaptive workflow composition, deterministic sensors, and a
  separate depth axis;
- **P2 candidates:** a human-confirmed learning loop, harness-neutral core, and a
  formal machine-readable state machine.

These labels rank the discovery findings against each other. They do not override
the committed OS backlog or authorize implementation. Adopt/adapt/reject analysis,
deduplication, and separate implementation tickets are required first.

## Backlog Summary

The active backlog falls into six themes:

1. bounded autonomy, decision policy, principled escalation, and durable resume;
2. review detection, behavior, ownership, and human handoff;
3. workflow observability, progress, retention, and efficiency;
4. workflow presets and maintainable Skill structure;
5. alternative orchestration and AI-DLC architecture discovery;
6. company-environment validation and 1.0 release readiness.

For live status, descriptions, and acceptance criteria, use the
[OS project backlog](https://luminous419.atlassian.net/issues/?jql=project%20%3D%20OS%20ORDER%20BY%20key%20ASC).

## Release Goal

The first stable release should meet all of these conditions:

- the owner has selected and added a license;
- version policy and the 1.0 entry criteria are explicit;
- README, installation, compatibility, and release documentation match behavior;
- deterministic validation, unit tests, package verification, and archive
  reproducibility all pass;
- the autonomy boundary, decision states, escalation path, and resume behavior
  are explicit and covered by positive and negative tests;
- unresolved high-impact assumptions cannot be reported as successful completion;
- tested Orca and company environments are described as point observations with
  known limitations;
- unresolved lifecycle or review-quality risks are either fixed or documented as
  explicit release blockers.

See [Releasing](RELEASING.md) and
[License Decision Required](LICENSE-DECISION.md).

## Non-Goals

- Reproduce AI-DLC's full agent roster or stage count.
- Eliminate all user interaction or guess preferences that were never supplied.
- Treat Worker/Reviewer agreement, model confidence, or a recommended default as
  evidence of user authorization.
- Turn Jira into a generated copy of this document, or this document into a Jira dump.
- Couple workflow policy to `claude-glm`, `claude-gemma`, Codex, or another command.
- Claim compatibility for an Orca version range from one successful point test.
- Rewrite historical run or validation evidence after the fact.
- Select a license or declare `1.0.0` without an explicit owner/release decision.
- Refactor Skill lifecycle or runtime semantics as part of documentation cleanup.

## Maintaining This Roadmap

### OS-40 deterministic workflow engine

The runtime-neutral LangGraph StateGraph now owns the standard phase sequence, bounded
correction, HIGH downstream revalidation, decision-first blocking, and mandatory final review.
Orca is an execution/provenance adapter; durable resume remains OS-31 and a standalone CLI
adapter remains OS-37. See `DETERMINISTIC_WORKFLOW.md`.

Update this document when a milestone outcome, architecture principle, target
direction, or release criterion changes. Routine Jira status changes do not require
a roadmap edit unless they materially change the project's current position or
recommended sequence.
### OS-30 structured human clarification

New clarification requests and responses use schema generation v2; homogeneous historical v1 single-item artifacts remain immutable and are never migrated or rewritten.

Implemented as immutable run artifacts plus an explicit CLI response path for `NEEDS_INPUT` and `CONFLICT`. Stable request/decision identities, bounded bundles and ambiguity, provenance, append-only lifecycle lineage, and single-copy sensitive raw evidence are included. Durable resume/decision consumption remains OS-31.
