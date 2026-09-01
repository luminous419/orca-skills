# ORIGINAL_REQUEST — Jira OS-29

Ticket: OS-29 "Add Continuous Decision and Escalation Gates to Every Phase"
Jira URL: https://luminous419.atlassian.net/browse/OS-29
Repo: /Users/luminous/aiAssistedProjects/orca-skills
Branch: os-29-continuous-decision-gates (based on main @ b13f191)
Run: run_35b221ea299d   Risk: HIGH (explicit)
Phases: analysis -> plan -> design -> implementation -> test
Worker: claude-opus   Reviewer: codex-sol   max-iterations: 5

## Reading order (live sources beat past documents)

Read the LIVE Jira OS-29 issue and the LATEST code on this branch (based on main).
Past documents in docs/ and artifacts/ are historical context only; where they
disagree with live Jira or the current code on main, live Jira and main win.

## Goal (from the requester, verbatim intent)

OS-29 is NOT a new Review Gate and NOT a separate review loop.

The purpose is to REUSE the existing phase review/correction structure while
integrating the OS-28 decision state into workflow transitions, so that two
outcomes become distinguishable:

* Quality failure: the Worker can fix it -> existing correction loop.
* User decision required or conflict: not the Worker's to fix -> block execution
  and terminate with a blocked outcome.

Core completion condition:

> When `NEEDS_INPUT` or `CONFLICT` exists, the correction Worker and the next
> phase are NOT dispatched, the user-waiting state does NOT consume a correction
> iteration, and that judgement plus its provenance is left behind as verifiable
> run evidence.

## Combining with the existing gate

Quality verdict and Decision State stay SEPARATE AXES.

| Decision State       | Quality Gate                                     | Next action                                    |
| -------------------- | ------------------------------------------------ | ---------------------------------------------- |
| `CLEAR`              | existing Reviewer runs                            | PASS -> next phase; FAIL -> correction         |
| `ASSUMPTION_ALLOWED` | record assumption + grounds, existing Reviewer runs | PASS -> next phase; FAIL -> correction       |
| `NEEDS_INPUT`        | current-phase Reviewer MAY verify the classification and its grounds | block correction Worker AND next phase |
| `CONFLICT`           | current-phase Reviewer MAY verify the classification and its grounds | block correction Worker AND next phase |

The current-phase Reviewer can verify whether the Worker's decision
classification is correct, but CANNOT decide on the user's behalf.

A Reviewer may NOT downgrade `NEEDS_INPUT` or `CONFLICT` to `CLEAR` or
`ASSUMPTION_ALLOWED` without grounds. Worker/Reviewer agreement does NOT
substitute for user authority.

## Decision check boundaries

Do NOT implement "continuous" as a separate real-time monitoring process.
Check at exactly these three boundaries:

1. Before phase entry
   * If an unresolved blocking decision exists, forbid dispatching a new phase.
2. After receiving the Worker result
   * The Worker returns either a normal phase result or a structured blocked outcome.
   * Validate decision state, reason code and evidence against the OS-28 contract.
   * If the Worker discovered a blocking decision mid-work, it must return a blocked
     outcome rather than pretending the phase completed.
3. After receiving the Reviewer result
   * Verify decision misclassification, unauthorized assumption, and decision drift.
   * Combine Quality verdict and Decision State to determine the next transition.

The Final Adversarial Review checks unresolved decisions, unapproved high-impact
assumptions, decision drift, illegal dispatch after a blocking state, and missing
provenance.

## Fail-closed rules

A gate boundary requires an explicit machine-readable decision result.
NONE of the following may be presumed `CLEAR`:

* missing decision record
* malformed or unsupported schema
* missing required safety fact
* unknown state or reason code
* model confidence
* Worker/Reviewer agreement
* timeout or user non-response
* the existence of a recommended default

Explicitly DESIGN the relationship between the OS-28 Decision Record — which is
optional in ordinary documents — and the OS-29 gate input. A section being
optional in a general document is NOT the same thing as a gate result that
determines a transition being omissible.

A missing or malformed gate result must become an explicit validation failure or
blocked result, never automatic progression.

## State and iteration rules

* Quality `FAIL` consumes the existing responsible-phase correction iteration.
* `NEEDS_INPUT` and `CONFLICT` are NOT quality failures and do NOT consume a
  correction iteration.
* Block the correction Worker and the next phase IN CODE after a blocking decision.
* Only a Reviewer dispatch that verifies the CURRENT phase's decision classification
  may be permitted.
* OS-31 is not implemented yet, so do NOT implement or claim cross-session resume.
* This ticket implements only up to leaving an explicit blocked outcome plus the
  required evidence — not completion.
* If the blocked outcome cannot be expressed with the existing terminal vocabulary,
  analyse a minimal change compatible with the existing lifecycle contract and record
  the design rationale BEFORE arbitrarily adding a new state.

## Artifact and provenance

Reuse the existing run-scoped artifact and log structure as much as possible.
Do NOT make workflow control depend on free-form Markdown interpretation alone.

Record and validate AT LEAST the following machine-readably:

* run, phase, iteration
* decision state and reason code
* evidence and assumption
* open question or conflict
* responsible phase
* Worker/Reviewer role and verdict
* source binding and timestamp

Implement Decision ID and change lineage ONLY to the extent OS-29's dispatch
blocking and audit actually require. Do NOT encroach on OS-30 scope: user
request/response identity, response normalization, and the supersession protocol.

State explicitly the relationship between the human-readable Markdown summary and
the machine-readable authority the engine uses, and prevent drift between duplicate
records with a validator.

## Policy parity

Do NOT duplicate or redefine the OS-28 decision vocabulary; use the existing source
of truth.

Policy shared by `orca-worker-reviewer-orchestration` and `orca-worker-reviewer-loop`
is intentionally kept identical; block drift between the two Skills with a validator
and regression tests. Orca-specific lifecycle behaviour stays only in the
orchestration Skill, but decision semantics must not differ.

Risk, Quality Profile and Agent Profile stay independent axes. LOW/MEDIUM/HIGH risk
must NOT expand decision authority.

## Required validation scenarios

At minimum verify these as positive/negative fixtures:

1.  `CLEAR` + Review PASS -> proceed to next phase
2.  `ASSUMPTION_ALLOWED` + valid grounds -> record then proceed
3.  `NEEDS_INPUT` -> correction Worker and next phase blocked, iteration NOT consumed
4.  `CONFLICT` -> correction Worker and next phase blocked
5.  high-impact decision discovered during IMPLEMENTATION -> blocked outcome, not phase completion
6.  Worker approves a high-impact decision without authority -> Reviewer detects it as a blocking finding
7.  LOW risk run -> decision authority is NOT expanded
8.  downstream expands an existing user decision -> a new decision or escalation is required
9.  Final Review with an unresolved decision -> completion forbidden
10. Worker and Reviewer agree on the same unauthorized assumption -> no automatic approval
11. timeout or non-response -> no automatic approval and no iteration consumption
12. illegal dispatch attempt after a blocking decision -> fail closed
13. missing or malformed decision result -> no `CLEAR` presumption
14. decision-semantics drift between the two Skills -> validation failure

Prove that dispatch blocking, iteration non-consumption and the fail-closed checks
are NON-VACUOUS via mutation testing or an equivalent method.

## Out of scope

* a separate Reviewer or duplicate gate loop
* a real-time monitoring agent
* user question UX and request/response protocol: OS-30
* cross-session durable pause/resume: OS-31
* evaluation benchmarks and success metrics: OS-32
* the full production deterministic engine and Orca adapter separation: OS-27 follow-up
* new phase vocabulary
* modifying past run artifacts
* unrelated large-scale refactoring
* weakening lifecycle, Risk, Quality Profile, Agent Profile or Final Review guarantees

## Working and verification method

* Create a separate feature branch from the latest main.  (DONE: os-29-continuous-decision-gates)
* ANALYSIS first analyses the possibility of duplication with the existing review loop,
  and the relationship between the OS-28 optional record and the gate result.
* PLAN and DESIGN present the existing components to reuse and the minimal change surface.
* Implement only the approved scope; do NOT delete or weaken existing tests/validators.
* Run targeted tests AND the full regression suite.
* Each phase must get an independent Reviewer PASS.
* After all phases complete, run a fresh Final Adversarial Review.
* If the Final Review FAILs, fix at the finding's responsible phase and revalidate.

## Completion conditions

All of the following must hold:

* the existing review/correction loop was NOT duplicated
* Quality verdict and Decision State are expressed as separate axes
* OS-28's four states are integrated consistently into the existing transitions
* `NEEDS_INPUT` and `CONFLICT` block the correction Worker and the next phase
* current-phase Reviewer classification verification and the forbidden next dispatch are clearly distinguished
* user-waiting does NOT consume a correction iteration
* a missing or malformed decision result does NOT fail open
* Reviewer and Final Review block unauthorized assumptions and unresolved decisions
* the two Skills' shared decision semantics match
* positive/negative and non-vacuity verification and the full CI pass
* the current limitations in the absence of OS-30/31 are documented accurately

## Jira OS-29 acceptance criteria (verbatim from the live ticket)

* The five phases and the Final Review use the same state/reason contract from OS-28.
* `CLEAR` and `ASSUMPTION_ALLOWED` runs proceed without unnecessary user pauses for clarification.
* If `NEEDS_INPUT` or `CONFLICT` exists, the next phase is not dispatched.
* If a Worker wrongly auto-approves a high-impact decision, the phase Reviewer can FAIL it as a blocking finding.
* If a user decision is changed or expanded in a later phase, a new decision event or escalation is required.
* Gate result, judgement grounds, responsible phase and reviewer verdict remain in run-scoped artifacts/logs.
* LOW/MEDIUM/HIGH risk does not expand decision authority.
* Each phase has a positive/negative end-to-end test.

Jira OS-29 scope (verbatim): apply the OS-28 decision policy to every phase artifact
and transition; common artifact sections Decision State / New Decisions / Open
Questions / Assumptions; escalate as soon as a blocking decision is found even before
phase completion; the phase Reviewer independently verifies unauthorized assumptions
and wrong decision-state classification; a deterministic transition that forbids the
next Task/phase dispatch on `NEEDS_INPUT`/`CONFLICT`; the Final Reviewer checks
unresolved decisions, unapproved high-impact assumptions and decision drift; a
decision/assumption ledger with append-only provenance.

## Repository / security policy for every Worker and Reviewer in this run

Forbidden unless the user explicitly asks: git push / force push, branch deletion,
release/deployment, production/infrastructure change, destructive database
operations, external network access, arbitrary external package downloads, and
printing/recording/transmitting secrets.
