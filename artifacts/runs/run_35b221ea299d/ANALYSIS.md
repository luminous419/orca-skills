# Worker Result

STATUS: COMPLETE

Run: run_35b221ea299d · Phase: analysis · Iteration: 2 · Role: worker
Iteration 2 is a CORRECTION of iteration 1 against REVIEW_ANALYSIS.md findings F-001
and F-002; see "Review Feedback Resolution" at the end of this document. Everything not
named there is unchanged from iteration 1.
Branch: os-29-continuous-decision-gates (base main @ b13f191)
Ticket: OS-29 "Add Continuous Decision and Escalation Gates to Every Phase" (read live, 2026-08-31)

All file:line references below are to the working tree of this branch at the time of
writing. Line numbers were read directly out of the files, not inferred.

---

## Request Summary

OS-29 must run the OS-28 decision check at the transitions the **existing**
Worker → Reviewer → correction loop already has, so that two outcomes become
distinguishable on separate axes:

* **quality failure** — the Worker can fix it → the existing correction loop, and it
  consumes a correction iteration;
* **user decision required / conflict** (`NEEDS_INPUT` / `CONFLICT`) — not the
  Worker's to fix → the correction Worker and the next phase are **not dispatched**,
  the pause does **not** consume a correction iteration, and the judgement plus its
  provenance is left behind as verifiable run evidence.

It must do this **without** adding a Review Gate, a second Reviewer, or a parallel
review/correction loop, and without implementing OS-30 (question UX / request-response
identity) or OS-31 (durable pause/resume).

The live Jira issue (OS-29, updated 2026-08-31, labels `bounded-autonomy`,
`phase-gate`) agrees with `artifacts/runs/run_35b221ea299d/ORIGINAL_REQUEST.md` and
adds two items the ORIGINAL_REQUEST states only implicitly: escalation **before phase
completion** when a blocking decision is discovered mid-work, and a
"decision/assumption ledger with append-only provenance". Nothing in the live ticket
contradicts the ORIGINAL_REQUEST; where the ticket is terser, the ORIGINAL_REQUEST is
the more specific of two consistent sources.

---

## Current State

### The two engines that exist today

| Engine | File | What it is |
| --- | --- | --- |
| Deterministic reference loop | `scripts/e2e_harness.py` (1724 lines) | `E2EHarness.run()` is the single-phase Worker↔Reviewer correction loop; `E2EHarness.run_workflow()` is the whole-run transition engine (phase gates → Final Review T0–T5a). It drives `fake_worker.py` / `fake_reviewer.py` subprocesses. |
| Live Orca runtime path | `scripts/orca_runtime_harness.py` (3469 lines) | `OrcaRuntimeHarness` performs real Orca `task-create` / `worker-start` / `wait` / settlement with the four-axis lifecycle. It has **no** phase loop and **no** iteration counter — `iteration` is a caller-supplied parameter. |

Neither is imported by any production entry point; both are exercised by tests. The
*shipped product* is the two Markdown Skills, and the Python is the executable contract
that keeps them honest.

### Where OS-28 stands

`scripts/decision_policy.py` (1329 lines) is a complete, tested contract loader and
evaluator, and it is **wired to nothing**. Its only non-test importer is
`scripts/validate_skills.py:12-21`, which validates the *contract text*, not any
runtime behaviour. Verified by grep: no import of `decision_policy` exists in
`e2e_harness.py`, `orca_runtime_harness.py`, `run_logging.py`, `review_isolation.py`,
`task_context.py`, `workflow_contract.py`, or `final_review_eval.py`. The module's own
docstring states this deliberately:

> `scripts/decision_policy.py:22-26` — "No import of orca_runtime_harness, run_logging,
> review_isolation, e2e_harness or task_context. No dispatch, gate, phase, pause, wait
> or question logic. This is the OS-28 contract; running the check at a phase gate is
> OS-29, asking the question is OS-30, and waiting for the answer is OS-31."

and the SKILL.md prose says the same
(`orca-worker-reviewer-orchestration/SKILL.md:368-370`).

**So OS-29's job is precisely: make `decision_policy.py` the authority at boundaries
the loop already has — and add no loop.**

### Baseline validation state (this branch, before any OS-29 change)

* `python3 scripts/validate_skills.py` → `Skill validation PASSED (648 checks)`.
* `python3 -m unittest discover -s scripts -p 'test_*.py'` → `Ran 1496 tests in 303.782s` → `OK (skipped=6)`, exit 0
  (the CI invocation, `.github/workflows/*.yml:37`).

---

## Findings

Answers A1–A10 are given below, each separately labelled, each with file:line
evidence.

---

### A1. Duplication risk with the existing review loop

**Where the existing loop lives.** There is exactly one correction loop, and it is a
`for` loop inside one function.

| Element | Location |
| --- | --- |
| The in-phase correction loop | `scripts/e2e_harness.py:904` — `for iteration in range(1, self.max_iterations + 1):` inside `E2EHarness.run()` (`scripts/e2e_harness.py:898-1228`) |
| Worker dispatch | `scripts/e2e_harness.py:981-987` (`subprocess.run(worker_command …)`) |
| Worker result parse | `scripts/e2e_harness.py:997-1000` (`parse_worker_output`) |
| **Transition point W** — Worker declared BLOCKED | `scripts/e2e_harness.py:1015-1026` → returns `final_status=BLOCKED`, `reason="WORKER_BLOCKED"` |
| Safety-floor guard (LOW only) | `scripts/e2e_harness.py:1029-1051` |
| Finding-resolution trace guard | `scripts/e2e_harness.py:1053-1066` |
| **Transition point L** — LOW: Worker result *is* the phase gate | `scripts/e2e_harness.py:1070-1081` → returns `COMPLETED`, no Reviewer ever dispatched |
| Reviewer dispatch | `scripts/e2e_harness.py:1158-1164` |
| Reviewer result parse | `scripts/e2e_harness.py:1183-1186` |
| **Transition point R-PASS** | `scripts/e2e_harness.py:1198-1208` → returns `COMPLETED` |
| **Transition point R-FAIL** | `scripts/e2e_harness.py:1210-1215` → records findings, sets `previous_blocking_findings`, falls to the next `iteration` — *this is the correction dispatch* |
| **Transition point X** — budget exhausted | `scripts/e2e_harness.py:1216-1226` → `ESCALATED`, `reason="MAX_ITERATIONS_REACHED"` |

Around that single-phase loop, `E2EHarness.run_workflow()`
(`scripts/e2e_harness.py:1424-1723`) is the whole-run engine with exactly three call
sites that dispatch a phase round, all of which funnel into the *same*
`_phase_harness(...).run(...)`:

| Round kind | Call site |
| --- | --- |
| Phase gate | `scripts/e2e_harness.py:1510-1526` (`for phase in scenario.phases:` … `:1520`) |
| Correction (T4, Final-Review-driven) | `scripts/e2e_harness.py:1611-1668`, via `_run_correction_round()` at `:1391-1422` which calls `run()` at `:1409` |
| Downstream revalidation (T5a, HIGH only) | `scripts/e2e_harness.py:1691-1721`, `run()` at `:1706` |

and one Reviewer-only round, the Final Adversarial Review:
`_run_final_review_attempt()` at `scripts/e2e_harness.py:1251-1341`, driven by the
`while True:` at `:1531` with branches T1 (`:1565-1567`), T2 (`:1573-1577`), T3
(`:1584-1609`).

The Markdown authority for the same structure is
`orca-worker-reviewer-orchestration/SKILL.md:1712-1755` (§12 FAIL Loop) and
`:1989-2100` (§17 Final Adversarial Review); the invariant
`Reviewer FAIL → new Worker correction dispatch` is at `SKILL.md:2217`.

**Which transition points can carry the decision check without duplicating the loop.**
All three OS-29 boundaries are *already existing branch points* in this one loop.
Adding the check means adding **conditions to existing branches**, not adding rounds:

1. **Before phase entry** → the top of the three round-dispatch call sites above
   (`:1510-1520`, `:1611-1624`, `:1691-1704`). The check is a `return snapshot(...)`
   guard placed before `_phase_harness(...).run(...)`, structurally identical to the
   `if phase_iterations[phase] >= self.max_iterations:` guard that already sits there
   at `:1612` and `:1692`.
2. **After the Worker result** → immediately at the existing Transition point W,
   `scripts/e2e_harness.py:1015`. That branch already exists and already returns
   `BLOCKED`; OS-29 refines *why* and *with what evidence*.
3. **After the Reviewer result** → at the existing Transition points R-PASS
   (`:1198`) and R-FAIL (`:1210`), plus the Final Review T1 branch at `:1565`.

**No part of the ticket requires a second loop.** The one clause that could be
misread — "Only a Reviewer dispatch that verifies the CURRENT phase's decision
classification may be permitted" (ORIGINAL_REQUEST, *State and iteration rules*) — does
**not** call for a new Reviewer round. In the existing loop, the Reviewer for iteration
*n* is already dispatched at `:1158` *after* the Worker result of iteration *n* has been
parsed at `:997`. So when the Worker of iteration *n* returns `NEEDS_INPUT`/`CONFLICT`,
the classification-verifying Reviewer is **the Reviewer that this iteration was going to
dispatch anyway**. The non-duplicating reading is therefore:

> On `NEEDS_INPUT`/`CONFLICT` at Transition point W, do **not** take the early
> `BLOCKED` return at `:1015`; continue into the *already-scheduled* Reviewer dispatch
> at `:1158` with the decision-verification framing, and then terminate BLOCKED from
> the Reviewer branch instead of looping to `iteration + 1` at `:1210`.

That adds **zero** dispatches relative to a normal iteration and adds **zero** new
functions on the round-dispatch path.

**How non-duplication is actually proved (corrected — F-002).** An earlier draft of
this section proposed asserting that `run_logging.ROUND_KIND_VALUES` stays at four
(`scripts/run_logging.py:111-116`: `phase_gate`, `correction`,
`downstream_revalidation`, `final_review`, validated at every settled dispatch at
`scripts/orca_runtime_harness.py:2119-2123`) as *the* machine-checkable
non-duplication assertion. **That is insufficient and must not be load-bearing.**
`ROUND_KIND_VALUES` is a *classification* vocabulary; it constrains the label a round
may carry, never how many rounds or dispatches exist. A second Reviewer dispatch, or a
whole second correction loop, can label itself `phase_gate` or `correction` and leave
the set at exactly four. The vocabulary assertion therefore cannot distinguish
"no duplicate loop" from "a duplicate loop that reused a label", which is precisely the
completion condition OS-29 has to satisfy.

The load-bearing evidence must instead be a **direct invariant over dispatch topology
and cardinality**, read off ledgers that already exist and that record *every* agent
invocation:

| Ledger | Where | What it records |
| --- | --- | --- |
| `WorkflowRunResult.sessions` / `WorkflowResult.sessions` | `e2e_harness.py:282`, `:170`; appended in `_record_session` at `:835-864` | append-only `SessionEvent(role, phase, iteration, session_id, created)` for **every** Worker and Reviewer invocation, in every round kind |
| `worker_attempts` / `reviewer_attempts` | `WorkflowResult` (`:160-170`), appended at `:1012` and `:1194` | per-round attempt lists, one entry per settled subprocess dispatch |
| `correction_dispatches`, `revalidation_dispatches`, `reviewer_gates_skipped` | `e2e_harness.py:1631-1634`, `:1708-1711`, `:1514` | `(phase, iteration)` pairs written **before** any verdict is applied |
| `dispatch_settled` rows | `run_logging.py:158`, funnelled through `orca_runtime_harness.py:_log_attempt` (`:2100-2125`) | the runtime-layer append-only row per settled dispatch, carrying role, phase, iteration **and** `round_kind` |

The three invariants OS-29 should carry forward, stated over those ledgers:

* **INV-D1 — per-iteration role cardinality.** Inside one round (one
  `E2EHarness.run()` execution), the `sessions` ledger holds **exactly one**
  `role="worker"` event per iteration and **at most one** `role="reviewer"` event per
  iteration; equivalently `len(reviewer_attempts) <= len(worker_attempts)` for that
  round. A duplicated Reviewer breaks this whatever label it carries.
* **INV-D2 — no dispatch after a blocking decision.** In a `NEEDS_INPUT`/`CONFLICT`
  run, the `sessions` ledger contains **no** event whose `(phase, iteration)` is
  ordered after the settling Reviewer event, and `correction_dispatches` gains **no**
  entry for the blocked phase. The control half is the matching `CLEAR` run, where
  both ledgers must be non-empty (A10).
* **INV-D3 — dispatch-site cardinality.** The round-dispatch call sites stay exactly
  three (`_phase_harness(...).run(...)` at `:1520` and `:1705`, `_run_correction_round`
  at `:1625`, whose own single `run()` call is `:1405`), and the agent-invoking
  `subprocess.run(...)` sites inside `run()` stay exactly two (Worker `:981`, Reviewer
  `:1158`). A new Reviewer loop must either add a call site — caught statically — or
  re-enter an existing one — caught by INV-D1/INV-D2.

The four-value `round_kind` assertion is retained only as **supplementary** evidence
that no new *phase/round vocabulary* was introduced (an ORIGINAL_REQUEST out-of-scope
item in its own right); it is explicitly not the non-duplication proof. The
non-vacuity control that makes this distinction concrete is specified in A10.

**Residual duplication risk to watch (for DESIGN):** `E2EHarness.run()` is entered from
three different callers (`:1520`, `:1409`, `:1706`). A guard placed inside `run()`
applies to all three uniformly; a guard placed in `run_workflow()` must be repeated
three times. The former is where the loop actually is; the latter is where duplication
starts.

---

### A2. OS-28 OPTIONAL Decision Record vs. the OS-29 GATE RESULT

**What "optional" means today — four distinct facts, all verified:**

1. **The section is optional by contract, and the *sentence saying so* is itself
   validated.** `scripts/validate_skills.py:2488-2505` (checks C13/C14) walks every
   `templates/<phase>.md` and `reviews/common.md` in **both** Skills and asserts the
   presence of `DECISION_RECORD_TEMPLATE_ANCHOR = "## Decision Record (optional)"`
   (`scripts/validate_skills.py:728`) and
   `DECISION_RECORD_OPTIONALITY_ANCHOR = "optional section이다. 없어도 계약 위반이 아니다."`
   (`scripts/validate_skills.py:725-727`). Deleting the word "optional" from the
   templates fails the validator.
2. **An absent record is explicitly declared *not a finding*.**
   `orca-worker-reviewer-orchestration/reviews/common.md:200` — "Decision Record가
   **존재할 때만** 아래를 판정한다. 섹션이 없는 것은 finding이 아니다."
3. **When present, the record is judged strictly and fails closed.**
   `decision_policy.validate_record()` (`scripts/decision_policy.py:1189-1298`) raises
   on: an unknown state (`:1192-1193`), a missing/foreign reason code (`:1198-1207`), a
   boundary element that does not match the code's binding (`:1209-1229`), any empty
   required-evidence field (`:1231-1239`), an undeclared safety fact under
   `ASSUMPTION_ALLOWED` (`:1279-1288`), and grounds that do not actually justify the
   declared state (`:1292-1294`). `parse_decision_policy` raises rather than returning
   `None` for a malformed contract, and the module docstring explains that this is a
   deliberate departure from `skill_policy.load_risk_contract`'s fail-**open**
   convention (`scripts/decision_policy.py:14-26`).
4. **But none of that ever runs in a workflow.** As established under *Current State*,
   `validate_record()` has **no** production caller, and — decisively — **there is no
   parser anywhere in `scripts/` that reads a `DECISION_STATE:` line out of a
   Worker/Reviewer Markdown result.** Grep for `DECISION_STATE` over `scripts/*.py`
   returns only `scripts/decision_policy.py:44,229-230,254,259,351,378` (the constant
   and its own contract checks) and `scripts/test_decision_policy.py:27,181,263`. By
   contrast, the *quality* vocabulary does have such parsers:
   `parse_worker_output` (`scripts/e2e_harness.py:307`), `parse_reviewer_output`
   (`:357`), `parse_final_review_output` (`:496`), `_reviewer_gate_result`
   (`scripts/orca_runtime_harness.py:595-624`), `_reviewer_review_verdict` (`:626-647`),
   `run_logging.parse_final_review_report` (`scripts/run_logging.py:1549`).

**Conclusion: "optional" today means (a) documentation-optional, positively enforced,
and (b) operationally inert — an absent record and a present record produce the same
transition, because nothing reads either.** The gap OS-29 must close is not "make the
section mandatory"; it is "there is no gate input at all".

**What OS-29 must require at a gate boundary.** The requester's own framing settles the
category question — "A section being optional in a general document is NOT the same
thing as a gate result that determines a transition being omissible" (ORIGINAL_REQUEST,
*Fail-closed rules*). The requirements that follow from that, stated as requirements
(the *mechanism* is DESIGN's):

* **R-A2-1 — the gate result is a distinct object from the `## Decision Record`
  section.** The narrative section stays optional; the optionality anchor at
  `validate_skills.py:725-727` and the "absent section is not a finding" sentence at
  `reviews/common.md:200` must both survive, because removing them weakens the OS-28
  guarantee and the ORIGINAL_REQUEST's *Out of scope* forbids weakening existing
  guarantees.
* **R-A2-2 — at a gate boundary the decision result is REQUIRED and EXPLICIT.** "No
  decision was needed here" must be *asserted* as `CLEAR` with grounds that satisfy the
  `no_open_decision_item` predicate (`scripts/decision_policy.py:867-877`: requires
  `open_decision_item is False` **and** no triggering element **and** no declared
  contradiction), never *inferred* from the absence of a section.
* **R-A2-3 — the fail-closed list is a validation failure or a blocked result, never a
  `CLEAR` presumption.** Each of the eight cases the ORIGINAL_REQUEST names maps onto
  machinery that already exists and already fails closed *when called*: missing field →
  the same shape as `OutputContractError` from `_parse_choice`
  (`scripts/e2e_harness.py:294-305`); unknown state / unknown reason code →
  `validate_record` `:1192,1198-1207`; unsupported schema → `SUPPORTED_SCHEMA_VERSIONS`
  (`scripts/decision_policy.py:42`) and `parse_decision_policy`; missing safety fact →
  `_undeclared_safety_facts` (`:803`) via `:1279-1288`; model confidence /
  Worker-Reviewer agreement / timeout / no-response / recommended default →
  `forbidden_authority_sources` in the shared contract
  (`orca-worker-reviewer-orchestration/SKILL.md:314`) enforced by
  `_user_decision_defect` (`scripts/decision_policy.py:682`) and pinned by
  `validate_skills.py:701-707` (the list) and `:2445-2449` (check C10). **OS-29 adds no new fail-closed semantics; it
  adds the call.**
* **R-A2-4 — the human-readable summary is never the authority.** Reuse the existing
  precedent: the machine-readable channel is the column/field
  (`ORCHESTRATOR_LOG_COLUMNS`, `scripts/run_logging.py:62-86`) and the parsed contract
  line; the Markdown section is the human explanation. A validator must reject drift
  between the two, in the same spirit as `run_logging.parse_final_review_report`
  (`scripts/run_logging.py:1549-1617`) reconciling a report against its audit record.

---

### A3. The three decision-check boundaries mapped onto concrete call sites

| OS-29 boundary | Deterministic engine (`e2e_harness.py`) | Live Orca path (`orca_runtime_harness.py`) | Markdown authority |
| --- | --- | --- | --- |
| **B1 — before phase entry** (forbid dispatching a new phase while an unresolved blocking decision exists) | Phase gate: `:1510-1520`. Correction (T4): `:1611-1624`, guard slot beside the existing budget guard at `:1612`. Revalidation (T5a): `:1691-1704`, guard slot beside `:1692`. Final Review attempt open: `:1531-1542`. | `run_attempt()` `:2498-2564` → `create_task()` `:1593` → `run_existing_task()` `:2382-2497`; the actual pre-dispatch instant is `:2440-2456` (`dispatch_context` render → `create_fake_terminal` → `_open_phase_iteration_boundary` at `:2453` → `start_worker` at `:2456`). A B1 guard belongs **before** `:2456`, i.e. before any Dispatch exists. The pre-dispatch failure path already exists and already logs: `_log_pre_dispatch_failure` `:2265`, called at `:2436` and `:2544`. | SKILL.md §8 `:908`, §12 `:1712`, §17 step 1 `:2013` |
| **B2 — after the Worker result** | `:997-1000` parse → `:1015-1026` the existing BLOCKED branch. The two guards that already sit between them (`:1029-1051` safety floor, `:1053-1066` resolution trace) are the structural precedent for a third. | `wait_for_done()` `:1864-1897` → `settle_attempt()` `:1898-2075`; the settled body is available as `attempt.body` and is already read post-settlement at `:2138-2139`. | SKILL.md §10 `:1481-1502` |
| **B3 — after the Reviewer result** | `:1183-1186` parse → `:1198-1208` PASS branch and `:1210-1215` FAIL branch. Final Review: `:1553-1567` (attempt settles, audit written at `:1562`, verdict read at `:1565`). | `_reviewer_gate_result()` `:595-624` and `_reviewer_review_verdict()` `:626-647`, both called from the single logging funnel `_log_attempt()` at `:2138-2139`. This funnel is where the Reviewer's parsed verdict already becomes machine-readable, and it is the one place every Worker, Reviewer, correction, revalidation and Final Review dispatch passes through (`:2104-2113`). | SKILL.md §11 `:1504`, §17 `:1989` |

**Two structural facts that constrain B1–B3:**

* On the live path the *iteration number is an input, not a counter*
  (`orca_runtime_harness.py:2498-2513`, `:2382-2400`). There is no `phase_iterations`
  dict in that file (grep: none). Deterministic iteration accounting exists **only** in
  `e2e_harness.run_workflow()`. So B1's "do not dispatch the next phase" is enforceable
  in code in `run_workflow()`, and on the live path is enforceable only as a
  pre-`start_worker` guard plus the Coordinator contract in SKILL.md.
* `_log_attempt()` runs **after** settlement by construction
  (`orca_runtime_harness.py:2126-2131`), so it can *record* a B2/B3 judgement but can
  never *gate* the dispatch it describes. A B1 guard and a B2/B3 record are therefore
  two different code positions, not one.

---

### A4. Keeping Quality verdict and Decision State as separate axes

**The existing vocabularies, and what each can and cannot say.**

| Vocabulary | Values | Defined at | Parsed by |
| --- | --- | --- | --- |
| Worker `STATUS:` | `COMPLETE` \| `BLOCKED` | SKILL.md `:1490` / loop SKILL.md `:879` | `workflow_contract._find_choice` (`:41-60`), `parse_worker_output` (`e2e_harness.py:307`) |
| Reviewer `RESULT:` (the gate) | `PASS` \| `FAIL` | SKILL.md `:1515` | `parse_reviewer_output` (`e2e_harness.py:357`), `_reviewer_gate_result` (`orca_runtime_harness.py:595`) |
| `REVIEW_VERDICT:` (report annotation) | `PASS` \| `PASS WITH NOTES` \| `FAIL` \| `BLOCKED` | `reviews/common.md:172` | `workflow_contract._find_review_verdict_choice` (`:63-88`), `_reviewer_review_verdict` (`orca_runtime_harness.py:626`) |
| `RUN_STATUS` (terminal) | `COMPLETED` \| `BLOCKED` \| `ERROR` \| `ESCALATED` | `run_logging.py:105` (`RUN_STATUS_VALUES`); validated eagerly at `orca_runtime_harness.py:2298-2303` | `log_run_status` (`run_logging.py:514`) |
| Decision state | `CLEAR` \| `ASSUMPTION_ALLOWED` \| `NEEDS_INPUT` \| `CONFLICT` | `decision_policy.py:44-49`; shared contract SKILL.md `:232-323` | *nothing* |

**The axis separation is already asserted in prose and pinned by a validator.**
`SKILL.md:333-335`: "decision state는 RUN_STATUS / Worker STATUS / REVIEW_VERDICT와
별개의 축이다. OS-28은 그 셋 중 어느 것도 바꾸지 않는다. decision state `CONFLICT`는
invocation 검증 error code `PHASE_CONFLICT`와 무관하고, `NEEDS_INPUT`은 Worker의
`STATUS: BLOCKED`와 다르다." That first sentence is a byte-checked prose anchor:
`validate_skills.py:714` inside `DECISION_POLICY_SKILL_PROSE_ANCHORS`, checked as C12 at
`:2474-2478`. `PHASE_CONFLICT` is a genuinely unrelated invocation error code
(`SKILL.md:226`, `skill_policy.evaluate_invocation`).

**Can the existing terminal vocabulary express the OS-29 blocked outcome?** Evidence
for both sides, no decision taken here.

*Evidence that it can:*

* `RUN_STATUS: BLOCKED` already exists (`run_logging.py:105`) and
  `run_workflow()` already returns it for a Worker-declared block
  (`e2e_harness.py:1015-1026`, `reason="WORKER_BLOCKED"`).
* The `reason` field is free text on both writers (`log_run_status`
  `run_logging.py:514-563`; `WorkflowRunResult.reason` `e2e_harness.py:270-292`), and
  the repository already uses it to carry a closed-set-looking constant:
  `MAX_ITERATIONS_REACHED` (`:1224`), `FINAL_REVIEW_MAX_ITERATIONS_REACHED` (`:1576`),
  `OUT_OF_SCOPE_FINAL_REVIEW_FINDING` (`:1606`), `UNIT_TEST_BLOCKED` /
  `UNIT_TEST_EVIDENCE_MISSING` (`:109-110`). SKILL.md uses the same shape:
  `STATUS: BLOCKED / REASON: ORCA_ORCHESTRATION_UNAVAILABLE` (`:75-77`),
  `REASON: PREVIOUS_PHASE_CHANGE_REQUIRED` (`:1086-1088`).
  **So a new REASON constant is an established, non-vocabulary-widening extension.**
* `ORCHESTRATOR_LOG_COLUMNS` is explicitly an extensible, sparse-by-design schema —
  `risk`, `risk_source`, `requested_phases` and `round_kind` were added by OS-3 for
  exactly this reason (`run_logging.py:74-80`), and the comment there names sparseness
  as the design.

*Evidence that it strains:*

* `Blocking expresses gate failure` and `PASS WITH NOTES and BLOCKED are review report
  annotations, never new lifecycle states` are core invariants
  (`SKILL.md:2240`, `:2244`). `REVIEW_VERDICT: BLOCKED` therefore **cannot** be reused
  to mean "decision-blocked": it already means "insufficient information for a
  trustworthy verdict" and maps to `RESULT: FAIL` — which would route into the
  correction loop and consume an iteration, the exact opposite of what OS-29 needs.
* A Worker `STATUS: BLOCKED` is likewise already load-bearing: it terminates the round
  at `e2e_harness.py:1015` *without* Reviewer verification, which contradicts "the
  current-phase Reviewer MAY verify the classification". And `SKILL.md:335` explicitly
  says `NEEDS_INPUT` **differs from** `STATUS: BLOCKED`.
* `RUN_STATUS: BLOCKED` today carries no way to say *why* in a machine-readable column —
  `reason` is a free-text cell, and the ORIGINAL_REQUEST forbids making workflow control
  depend on free-form interpretation.

*Options for DESIGN, with the evidence attached (not decided here):*

| Option | Shape | Supporting evidence | Cost |
| --- | --- | --- | --- |
| **O1 — reuse `RUN_STATUS: BLOCKED` + a new closed REASON constant + a new sparse `decision_state` column** | No new lifecycle state. `BLOCKED` + e.g. `DECISION_BLOCKED (<phase>)`; `decision_state` / `decision_reason_code` join `ORCHESTRATOR_LOG_COLUMNS`. | The four precedent REASON constants above; the OS-3 sparse-column precedent `run_logging.py:74-80`. | Requires a machine-readable column so the reason is not free text. |
| **O2 — a fifth `RUN_STATUS` value** | e.g. `WAITING_FOR_INPUT`. | — | **Contra-indicated by the live OS-31 ticket**, whose Scope names "`WAITING_FOR_INPUT` durable run state와 허용 transition" as *OS-31's* deliverable. Taking that name in OS-29 would encroach on OS-31 and imply a resume capability OS-29 must not claim. Also `RUN_STATUS_VALUES` is validated eagerly and any addition is a lifecycle-contract change. |
| **O3 — a new Worker `STATUS:` value** | e.g. `DECISION_REQUIRED`. | — | Widens a two-valued vocabulary parsed by `workflow_contract._find_choice`, which asserts *exactly* the pair `{COMPLETE, BLOCKED}` (`workflow_contract.py:96-98`, `:46-60`). Highest blast radius of the three; touches both Skills and the loop Skill's §14. |

**Minimal lifecycle-compatible change, on this evidence:** O1. It adds no state to any
of the four existing vocabularies, reuses two established extension mechanisms, and
leaves `WAITING_FOR_INPUT` to OS-31. *DESIGN decides.*

---

### A5. Iteration accounting — where it is counted, and what must change

**Where it is counted today.** All deterministic counting is in
`E2EHarness.run_workflow()`:

* Declaration: `phase_iterations: dict[str, int] = {p: 0 for p in scenario.phases}`
  (`e2e_harness.py:1425`); `final_review_iterations = 0` (`:1431`). Reported via
  `WorkflowRunResult.phase_iterations` / `.final_review_iterations`
  (`:272-273`).
* The definition of what counts: `gate_attempts()` at `:1501-1507` —
  `len(result.worker_attempts if risk == "low" else result.reviewer_attempts)`.
* The three increment sites: `:1521` (phase gate), `:1635` (T4 correction), `:1712`
  (T5a revalidation). Two ledgers are appended just before the T4/T5a increments
  (`:1631-1634`, `:1708-1711`), and the comments there state the rule the DESIGN must
  not break: *"The ledger is written BEFORE any verdict is applied: these Reviewer
  dispatches physically happened and are never rewound."*
* `final_review_iterations += 1` at `:1533`, bounded at `:1573`.
* The in-phase bound is the loop header itself: `for iteration in range(1,
  self.max_iterations + 1)` (`:904`), exhaustion → `ESCALATED` at `:1216-1226`.
* Markdown authority: `SKILL.md:1758-1806` (§13), including the two-domain rule
  "두 domain은 서로 소비하지 않는다" (`:1801`), pinned machine-readably by
  `FINAL_REVIEW_COUNTER_DOMAINS = ("phase_iterations", "final_review_iterations")`
  (`validate_skills.py:272`).

**The precise code path that must change — and a load-bearing asymmetry already
present.** Because `gate_attempts()` counts **Reviewer** attempts at MEDIUM/HIGH
(`:1506`):

* A round that ends at Transition point W (`:1015`, Worker BLOCKED, no Reviewer
  dispatched) already contributes **0** to `phase_iterations` at MEDIUM/HIGH.
* The *same* round at LOW contributes **1**, because `gate_attempts()` counts Worker
  attempts there.

So "a pause does not consume a correction iteration" is **already true at MEDIUM/HIGH
for the Worker-blocked shape, and already false at LOW.** This is the single most
important A5 finding, and it has two consequences:

1. **The LOW path (`:1070-1081`) is where non-consumption must be actively
   implemented.** At LOW the phase gate *is* the Worker result, and the Worker attempt
   that declared `NEEDS_INPUT` is counted at `:1521`/`:1635`/`:1712`.
2. **If A1's non-duplicating design is taken** — i.e. `NEEDS_INPUT`/`CONFLICT` continues
   into the *already-scheduled* Reviewer at `:1158` for classification verification —
   then at MEDIUM/HIGH that Reviewer attempt **is** appended to `reviewer_attempts` at
   `:1194-1196` and **will** be counted by `gate_attempts()`. So non-consumption then
   requires a change at exactly one of two places:
   * `gate_attempts()` (`:1501-1507`) — exclude the terminating decision-verification
     attempt from the count; or
   * the three increment sites (`:1521`, `:1635`, `:1712`) — skip the increment when the
     round terminated on a decision block rather than a quality verdict.

   The first is one edit in one closure that all three sites already call, and it does
   not touch the correction/revalidation dispatch ledgers at `:1631-1634` / `:1708-1711`
   — which must keep recording the dispatch, because it physically happened. The second
   is three edits. **Quality `FAIL` must remain untouched on both routes**: a FAIL round
   is the `:1210-1215` path, which loops to `iteration + 1` and whose Reviewer attempt
   is exactly what `gate_attempts()` is counting.

**What must NOT change:** `final_review_iterations` (`:1533`) and its bound (`:1573`),
and the T2 last-attempt guard's position as the first statement on the FAIL edge
(`:1569-1577`, whose comment records that moving it below the routing block was a real
past defect).

---

### A6. Parity surface between the two Skills

**What is shared today.**

* The whole ```` ```policy-contract ```` JSON block, `decision_policy` key included:
  `orca-worker-reviewer-orchestration/SKILL.md:160-323` and
  `orca-worker-reviewer-loop/SKILL.md:156-319` (same content). Loaded by one parser,
  `skill_policy.load_policy_contract` (`skill_policy.py:109`) →
  `decision_policy.load_decision_policy` (`decision_policy.py:498`).
* Every `templates/<phase>.md` and `reviews/common.md` carries the Decision Record
  section and the optionality sentence in **both** Skills.

**How the validators enforce it.**

* `validate_skills.validate_decision_policy_contract()` (`:2253-2505`) runs ~25 named
  checks per Skill, then the cross-Skill equality check **C4** at `:2480-2486`:
  `left.reason_codes == right.reason_codes and left.transitions == right.transitions and
  left.raw == right.raw`. **`left.raw == right.raw` means the two blocks must be
  byte-equivalent as parsed JSON — any OS-29 key added to one must be added to the
  other.**
* **C7 — a hard size budget**: `0 < len(block body lines) <= DECISION_POLICY_MAX_LINES`
  (`validate_skills.py:2312-2318`), with `DECISION_POLICY_MAX_LINES = 90`
  (`decision_policy.py:130`). **Measured: the shipped `decision_policy` body is exactly
  90 lines.** The shared block is at its budget; *any* addition fails C7 unless the
  budget is raised or lines are removed.
* **C11a** — every key in the block must be classified as a state-selection input or a
  declarative key: `set(block) == STATE_SELECTION_INPUTS | DECLARATIVE_KEYS`
  (`validate_skills.py:2451-2457`; the two sets live at `decision_policy.py:58-83`). A
  new key therefore also requires an edit to `decision_policy.py`.
* **C11b** — no axis token (`risk`, `low`, `medium`, `high`, `profile`, …) may appear
  anywhere inside a state-selection subtree (`validate_skills.py:2458-2463`,
  `_axis_token_hits` at `:2508+`, `AXIS_TOKENS` at `decision_policy.py:90-92`).
* **C12** — six prose sentences are byte-anchored in both SKILL.md files
  (`DECISION_POLICY_SKILL_PROSE_ANCHORS`, `validate_skills.py:712-724`, checked at
  `:2474-2478`).
* **C13/C14** — the template/reviews anchors, `:2488-2505`.
* `scripts/test_policy_smoke.py` runs every deterministic invocation assertion against
  **both** `SKILL_PATHS` (`:21-25`), so an invocation-policy divergence fails there too;
  `test_decision_policy.py:1462-1470` independently asserts the two blocks are identical.

**Where an OS-29 addition must be mirrored, and where it must not.**

*Must be mirrored (decision semantics):* anything that changes what a state means, what
evidence a state requires, or what a Worker/Reviewer must emit as a decision result.
Practically: the Worker/Reviewer result-contract change (orchestration `SKILL.md:1481`
§10 / `:1504` §11 ↔ loop `SKILL.md:874` §14 / `:916` §16; the emitted
lines themselves at orchestration `:1490`/`:1515` and loop `:879`/`:921`), the templates, and
`reviews/common.md`.

*Must NOT be mirrored (Orca-only lifecycle):* dispatch blocking, `RUN_STATUS`, the
ORCHESTRATOR_LOG/TIMING_LOG columns, `round_kind`, terminal/Dispatch provenance, and
Final Review audit records. The loop Skill has no Orca Run/Task/Dispatch at all
(`orca-worker-reviewer-loop/SKILL.md:1-40`, `:578` "Mandatory Independent Orca
Sessions" is about sessions, not orchestration state).

*The established mechanism for the Orca-only half* is the `#### <Name> contract`
anchor-block pattern: a `#### … contract` heading followed by a ```` ```text ````
`KEY = value` list, validated against a dict in `validate_skills.py`. **Nine of these
exist today and all nine are orchestration-only** — verified by grep: `SKILL.md:688`
Session reuse, `:840` Lifecycle accounting, `:1034` Risk profile, `:1090` Task boundary,
`:1117` Artifact path, `:1544` Reviewer context, `:1584` Quality profile, `:1686` Agent
profile, `:2170` Final review; the loop SKILL.md has **zero**. Their validator dicts are
at `validate_skills.py:114, 166, 200, 234, 265, 333, 407, 755`. The comment at
`validate_skills.py:745-750` states the division of labour explicitly: "The SHARED
policy contract owns everything both skills need …; this block owns what only the
orchestration runtime has."

**Consequence for DESIGN:** given C7 is already at its limit and C11a requires a
`decision_policy.py` key-partition edit, the low-risk shape is *no change to the shared
`decision_policy` block*, plus a tenth orchestration-only anchor contract for the gate
lifecycle, plus mirrored result-contract/template text for the decision semantics.

---

### A7. Run-scoped artifact/log machinery OS-29 must REUSE

**What exists.**

| Machinery | Location |
| --- | --- |
| `ORCHESTRATOR_LOG.md` — append-only table, one row per event | `run_logging.py:62-86` (columns), `:346-411` (`log_orchestrator_event`), `:336` (path) |
| `TIMING_LOG.md` | `run_logging.py:87-103` (columns), `:413` (`log_timing_event`), `RunTimingTracker` `:564-859` |
| Terminal run status row | `run_logging.py:514` (`log_run_status`), `:105` (`RUN_STATUS_VALUES`) |
| Per-dispatch Final Review audit record (immutable, staged-then-published, redacted) | `run_logging.py:2120-2317` (`write_final_review_audit_record`), `:2358-2373` (`_REQUIRED_RECORD_FIELDS`), `:1779` (`_stage_and_publish_audit_record`), `:1129` (`redact_text`) |
| Evidence bundle export | `run_logging.py:2549` (`export_final_review_evidence`) |
| Run artifact root, created before the first dispatch | `task_context.py:263` (`ensure_run_artifact_root`), `:234` (`run_artifact_root`); SKILL.md `:1117-1130` Artifact path contract |
| The one logging funnel every settled dispatch passes | `orca_runtime_harness.py:2094-2201` (`_log_attempt`) |
| Logging never mutates lifecycle | `orca_runtime_harness.py:2076-2081` (`_safe_log`), `e2e_harness.py:1387-1389` |

**Coverage of the thirteen required machine-readable fields.**

| Required field | Carried today? | Where |
| --- | --- | --- |
| run | ✅ | `run_id` is the directory identity (`run_logging.py:306-345`); `requested_phases` column `:77` |
| phase | ✅ | `ORCHESTRATOR_LOG_COLUMNS` `phase` (`:65`); TIMING `:90` |
| iteration | ✅ | `iteration` column (`:67`); TIMING `:92` |
| role (Worker/Reviewer) | ✅ | `role` column (`:66`) |
| verdict | ✅ (quality only) | `gate_result` `:72` and `review_verdict` `:73`, parsed at `orca_runtime_harness.py:595` / `:626` |
| timestamp | ✅ | `timestamp` column `:64`; `now_iso()` `:165`; `recorded_at` in the audit record `:2365` |
| responsible phase | ⚠️ partial | Exists in the Final Review finding contract and is parsed (`e2e_harness.py:79-86` `RESPONSIBLE_PHASE_LINE`, `:496` `parse_final_review_output`, routed at `:1592-1605`) and reaches `corrected_findings` (`:1657-1667`) — but **it is not a column** in `ORCHESTRATOR_LOG.md`. |
| source binding | ⚠️ partial | Task/Dispatch identity: `task_id` / `dispatch_id` / `terminal` columns (`:68-70`); audit record `stored_task_spec`, `dispatch_key`, repository state (`run_logging.py:1461`). No binding of a *decision item* to an artifact/source. |
| **decision state** | ❌ | no column, no field, no parser |
| **reason code** | ❌ | — |
| **evidence** | ❌ | — |
| **assumption** | ❌ | — |
| **open question / conflict** | ❌ | — |

**So five of thirteen are entirely missing and two are partial.** The
`ORCHESTRATOR_LOG_COLUMNS` comment (`run_logging.py:59-61`) states "The columns are the
whole schema. Both tables are append-only and every row fills every column (blank string
where a field does not apply)", and OS-3's own four added columns
(`risk`/`risk_source`/`requested_phases`/`round_kind`, `:74-80`) are the precedent for a
sparse-by-design addition. The append-only, immutable, redacted, staged-then-published
per-dispatch record (`run_logging.py:2120-2317`) is the precedent for the
"decision/assumption ledger with append-only provenance" the live Jira ticket asks for —
**it must be reused, not re-invented**, and note its own rule: "The record is complete
when it is written and is never edited … Correcting a record means writing a new record
under a new dispatch key" (`:2148-2151`).

---

### A8. Boundary against OS-30 and OS-31 — what OS-29 must NOT do

Read from the live tickets (fetched 2026-08-31).

**OS-30 owns** (must not appear in OS-29): stable decision/request identity as a
protocol; question composition (what to decide, selectable options, per-option
trade-offs, a recommendation and its grounds, default/timeout behaviour, which phase is
blocked); bundling independent questions; normalizing a natural-language reply into an
explicit option or bounded custom decision; response provenance/actor/timestamp;
**supersession lineage**; cancel/change/scope-expansion handling; the machine-readable
request/response contract for a future `HumanApprovalPort`.

**OS-31 owns** (must not appear in OS-29): the **`WAITING_FOR_INPUT` durable run state**
and its permitted transitions; settling active Task/Dispatch and terminal ownership at
pause; binding a pending request to run/phase/head/artifact digest; discovery and resume
from a *new* Coordinator; duplicate/stale/conflicting response handling; stale-decision
revalidation on head/policy change; re-entry into the responsible phase after an answer;
crash/restart/replay idempotency.

**What OS-29 may therefore do:** produce the `NEEDS_INPUT`/`CONFLICT` classification,
validate it against the OS-28 contract at a boundary, **block the correction Worker and
the next phase in code**, terminate the run with an explicit blocked outcome, and leave
the judgement plus provenance as run evidence. The ORIGINAL_REQUEST says the same:
"Implement Decision ID and change lineage ONLY to the extent OS-29's dispatch blocking
and audit actually require."

**Limitations that consequently remain and must be documented:**

* **L1** — A run that hits `NEEDS_INPUT`/`CONFLICT` **terminates**. It does not pause and
  cannot be resumed; answering the question means starting a new run. (OS-31.)
* **L2** — No question is asked in any structured form; the blocked outcome names the
  item and its grounds only. (OS-30.)
* **L3** — No supersession lineage: if a user decision is later changed or widened, OS-29
  can require a *new* decision event but cannot link it to the old one as a superseding
  version. (OS-30.) Note the tension: ORIGINAL_REQUEST validation scenario 8
  ("downstream expands an existing user decision → a new decision or escalation is
  required") is in OS-29 scope, but the *lineage record* for it is OS-30's. OS-29's
  answer must be the escalation, not the lineage.
* **L4** — No timeout semantics beyond the negative rule already in the contract
  (`forbidden_authority_sources` includes `timeout` and `no_response`,
  `SKILL.md:320`): OS-29 must show that a timeout yields neither approval nor an
  iteration charge, but must not define what *does* happen after one. (OS-30/OS-31.)
* **L5** — At LOW risk there is no phase Reviewer (`e2e_harness.py:1070-1081`;
  `SKILL.md:1745-1750`), so a Worker's decision **misclassification** at LOW is checked
  only by the Final Adversarial Review. This is not new (SKILL.md `:2062` already
  says the Final Review is the only gate at LOW) but OS-29 makes it decision-relevant and
  must state it.

---

### A9. Risk / Quality / Agent profile axis independence

**What is asserted today.**

* Contract-level: `independent_axes: ["risk", "quality_profile", "agent_profile"]`
  (`SKILL.md:322`), parsed at `decision_policy.py:93` (`CANONICAL_INDEPENDENT_AXES`) and
  pinned by C11d (`validate_skills.py:2469-2472`).
* Structural: `permitted_states()` has **no** `risk` parameter, and its docstring says
  the risk-independence is structural and is asserted via `inspect.signature`
  (`decision_policy.py:1000-1005`). The corresponding test is
  `test_decision_policy.py:1244-1300` — including `:1298`, "the comparison is the
  anti-vacuity move: it proves risk is INERT, not merely absent".
* Token-level: C11b forbids `risk` / `low` / `medium` / `high` / `profile` anywhere in a
  state-selection subtree (`validate_skills.py:2458-2463`).
* Invariants: `SKILL.md:2219-2221` — "Risk and phases are independent axes; risk never
  expands or contracts the requested phase set", "Risk selects which task graph nodes
  exist, never when they are created", "Final Adversarial Review is mandatory and
  identical at every risk level"; and `SKILL.md:957` (§8 Risk Axis) with the machine
  block at `:1034`.

**What in the current code could accidentally let risk widen decision authority.** Three
concrete vectors, all reachable:

* **V1 (highest) — the LOW early return.** `e2e_harness.py:1070-1081` returns
  `COMPLETED` from `run()` *before* the Reviewer half executes. **Any OS-29 check placed
  only on the Reviewer branch (`:1183`–`:1215`) silently does not exist at LOW**, so a
  LOW Worker's self-declared `CLEAR` would be final and unverified. That is decision
  authority widened by a risk level, by omission. The mitigation is placement: the
  decision check must sit on the **Worker-result** boundary (B2, `:1015`), which runs at
  every risk level, and the Reviewer-side check is an *additional* verification, never
  the only one.
* **V2 — T5a is HIGH-only.** `downstream_revalidation_set(...) if risk == "high" else ()`
  (`e2e_harness.py:1686-1690`). A "decision drift detected downstream" check hung on the
  T5a call site would exist only at HIGH.
* **V3 — `gate_attempts()` changes meaning with risk** (`:1501-1507`), so any
  iteration-accounting rule expressed in terms of "the gate attempt" is silently a
  different rule at LOW. See A5.

**What must be asserted to prevent it (for TEST):**

* **P1** — the decision check function's signature contains no `risk`, `profile`, or
  quality-profile parameter, asserted by `inspect.signature`, exactly as
  `test_decision_policy.py:1244-1300` already does for `permitted_states`.
* **P2** — the same decision-state input produces the same block/allow outcome at
  `low`, `medium`, `high`, with the *equality across levels* asserted and a
  co-located non-vacuity guard proving the three runs really differ elsewhere (this is
  ORIGINAL_REQUEST scenario 7).
* **P3** — the check executes on the LOW path: assert that a LOW run with a
  `NEEDS_INPUT` Worker result blocks, i.e. that V1 was actually closed.
* **P4** — C11b's axis-token rule extends over whatever new contract block OS-29 adds.

---

### A10. Non-vacuity — how the repository proves a guard is real

**The existing techniques, with examples.**

| Technique | Example |
| --- | --- |
| **Co-located cardinality guard** — a data-driven loop asserts its collection's expected size *inside the same test function*, before the loop | Stated as a rule in `test_decision_policy.py:4-8` ("A guard in a separate test can be deleted or skipped independently of the loop it protects; a co-located guard cannot"), marked `# D4-F guard`, e.g. `:1866`; `self.assertEqual(checked, 10)` at `:2156`, `:2178` |
| **Negative fixture / injected non-triggering value, with an anti-vacuity precheck** | `test_decision_policy.py:2157-2178` — inject a value, first assert `assertFalse(_element_is_triggering(spec, value))` ("the injected value must really not fire"), then assert rejection. Valid/invalid fixture trees live under `scripts/fixtures/decision_policy/{valid,invalid}` (`test_decision_policy.py:45`) |
| **Mutation of the contract itself** — mutate the parsed block and assert the parser raises | `test_decision_policy.py:196, 491, 797, 1046, 1054, 1330-1345` (`parse_decision_policy(mutated)` inside `assertRaises`) |
| **Golden-artifact mutation** — prove a byte-comparison can fail | `test_e2e_harness.py:4762-4810` (method opens at `:4762`) — four whitespace-only mutants, each asserted `assertNotEqual(mutant, original, "the mutation was a no-op")` then required to fail the real comparison helper |
| **Paired positive/negative halves** — the "must not change" test is accompanied by a "must be able to change" test | `test_e2e_harness.py:1369-1371` and `:1494-1497` ("The non-vacuity half: the comparisons above must be able to fail") |
| **Proving a static walker finds anything at all** | `test_os22_required_tests.py:549-557` — "a negative assertion over a walker that finds nothing proves nothing" |

**What OS-29 must do to prove its three claims non-vacuous.** Each claim needs a
positive fixture, a negative fixture, and a co-located guard that the fixture really has
the property:

* **Dispatch blocking.** The mutation is *remove the B1 guard* (or force the decision
  state to `CLEAR`) and assert the previously-blocked scenario now dispatches. The
  observable that makes this cheap already exists: `run_workflow()` records every
  dispatch in `correction_dispatches` (`e2e_harness.py:1631-1634`) and
  `revalidation_dispatches` (`:1708-1711`), and every phase-gate round appears in
  `sessions` (`_record_session`, `:835`). So "was the correction Worker dispatched?" is a
  list-length assertion on a returned value, not a log grep — **and the non-vacuity half
  is asserting that the same ledger is non-empty in the `CLEAR` control run.**
* **Iteration non-consumption.** Assert `phase_iterations[p]` is unchanged across the
  blocked round, *and* co-locate the control assertion that a quality-`FAIL` round
  increments it — otherwise "unchanged" would also pass if the counter were broken
  everywhere. Both values are on `WorkflowRunResult` (`:272`).
* **Fail-closed checks.** For each of the eight presumption sources in the
  ORIGINAL_REQUEST, one negative fixture whose *only* difference from a passing fixture
  is that source, plus the `test_decision_policy.py:2167`-style precheck that the
  injected value genuinely is the forbidden one. `forbidden_authority_sources` is
  already a closed five-item set (`SKILL.md:322`), so the loop over it can carry a
  `assertEqual(len(...), 5)` co-located guard.
* **Non-duplication (load-bearing, not a bonus — corrected per F-002).** The proof is
  the direct dispatch-topology invariant set INV-D1/INV-D2/INV-D3 from A1, asserted
  over the `sessions`, `worker_attempts`/`reviewer_attempts`,
  `correction_dispatches`/`revalidation_dispatches` and `dispatch_settled` ledgers —
  not over `ROUND_KIND_VALUES`.

  **The required non-vacuous mutation (M-DUP).** Build a mutant harness that adds an
  extra Reviewer round **while reusing an existing `round_kind`**: subclass
  `E2EHarness` and, after the Reviewer PASS branch at `e2e_harness.py:1198`, dispatch
  the phase Reviewer a second time for the same `(phase, iteration)` (or, at the
  `run_workflow` level, run a second `_phase_harness(phase, 1).run(...)` for an already
  passed phase); log both under the existing `phase_gate` label. The test then asserts,
  in this order, inside one test function:

  1. **Anti-vacuity precheck — the mutant is genuinely a duplicate.** `assertGreater`
     on the mutant's total `sessions` length and on its count of
     `role == "reviewer"` events versus the unmutated control run
     ("the mutation must not be a no-op", the `test_e2e_harness.py:4762-4810` and
     `test_decision_policy.py:2157-2178` pattern).
  2. **Proof that the round-kind proxy is blind.** `assertEqual` that the set of
     `round_kind` values observed in the mutant's `dispatch_settled` rows is still
     exactly `set(run_logging.ROUND_KIND_VALUES)` intersected with the kinds the
     scenario uses — i.e. the four-value assertion **still passes on the mutant**.
     This is the co-located guard that makes the demotion of the proxy a fact of the
     test suite rather than a claim in prose.
  3. **The invariant must fail.** `assertRaises`/`assertFalse` that INV-D1 (at most one
     Reviewer event per `(phase, iteration)`, `len(reviewer_attempts) <=
     len(worker_attempts)`) rejects the mutant, and that INV-D3's static call-site
     count rejects the call-site-adding variant of the same mutation.

  The control half is the unmutated run, where the same invariant checker must return
  clean — otherwise "the checker rejects the mutant" would also hold for a checker that
  rejects everything.

  Supplementary (kept, non-load-bearing): `run_logging.ROUND_KIND_VALUES` still has
  exactly four members and OS-29 introduces no fifth (`run_logging.py:111-116`), which
  evidences the separate out-of-scope item "no new phase vocabulary".

---

## Impact Scope

**Files that OS-29 will almost certainly have to touch** (evidence above; the *how* is
DESIGN's):

| File | Why |
| --- | --- |
| `scripts/e2e_harness.py` | B1/B2/B3 guards at `:1015`, `:1070`, `:1198`, `:1210`, `:1510-1520`, `:1611-1624`, `:1691-1704`, `:1565`; `gate_attempts()` at `:1501` |
| `scripts/decision_policy.py` | becomes an importable evaluator for the boundary; possibly a key-partition edit if the shared block changes (C11a) |
| `scripts/run_logging.py` | new sparse columns for decision state / reason code; the decision ledger reusing the audit-record machinery |
| `scripts/orca_runtime_harness.py` | pre-`start_worker` B1 guard (`:2440-2456`); decision fields into `_log_attempt` (`:2094`) |
| `scripts/validate_skills.py` | a tenth orchestration-only anchor contract + the anti-drift validator between the Markdown summary and the machine record |
| `scripts/workflow_contract.py` | only if the Worker/Reviewer result contract gains a parsed field |
| both `SKILL.md` + `templates/*.md` + `reviews/common.md` | mirrored decision semantics (A6) |
| `scripts/test_*.py` | the fourteen ORIGINAL_REQUEST scenarios + the non-vacuity proofs |

**Files that must NOT be touched:** `artifacts/runs/run_*` of prior runs (ORIGINAL_REQUEST
*Out of scope*); `scripts/review_isolation.py` and `scripts/final_review_eval.py` (OS-22
evaluation machinery, unrelated to the decision axis); `scripts/agent_profile.py`,
`scripts/quality_profile.py` (independent axes).

---

## Dependencies / Constraints

* **C-1 (hard).** The shared `decision_policy` block is at exactly 90 of
  `DECISION_POLICY_MAX_LINES = 90` lines (`decision_policy.py:130`,
  `validate_skills.py:2312-2318`). Any addition to it fails C7 today.
* **C-2 (hard).** `left.raw == right.raw` (C4, `validate_skills.py:2480-2486`) — the two
  Skills' blocks must stay byte-identical as parsed JSON.
* **C-3 (hard).** `RUN_STATUS_VALUES` is validated eagerly and *raises*, not logs, on an
  unknown value (`orca_runtime_harness.py:2298-2303`).
* **C-4 (hard).** `ROUND_KIND_VALUES` is validated at every settled dispatch
  (`orca_runtime_harness.py:2119-2123`).
* **C-5 (hard).** `workflow_contract._find_choice` asserts the Worker field's value set
  is *exactly* `{COMPLETE, BLOCKED}` (`workflow_contract.py:46-60`, `:96-98`).
* **C-6.** Six byte-anchored prose sentences in both SKILL.md files must survive
  (`validate_skills.py:712-724`).
* **C-7.** Logging must never mutate a lifecycle judgement (`_safe_log`,
  `orca_runtime_harness.py:2076-2081`; `e2e_harness.py:1387-1389`).
* **C-8.** The T2 last-attempt guard must remain the first statement on the Final Review
  FAIL edge (`e2e_harness.py:1569-1577`).
* **C-9.** Dispatch ledgers are written before any verdict is applied and are never
  rewound (`e2e_harness.py:1628-1634`, `:1706-1711`).
* **C-10.** CI gates: `python3 scripts/validate_skills.py` (648 checks today),
  `python3 -m unittest discover -s scripts -p 'test_*.py'`, `verify_package.py`,
  `build_release.py`, `git diff --check` (`.github/workflows/*.yml:34-48`).
* **D-1.** OS-28 is merged (`b13f191`) and is the sole vocabulary source.
* **D-2.** OS-30 and OS-31 are unimplemented; A8's L1–L5 are the resulting limitations.

---

## Risks

| # | Risk | Evidence / trigger | Severity |
| --- | --- | --- | --- |
| R-1 | **Decision check placed only on the Reviewer branch → no check at all at LOW risk**, silently widening decision authority by risk level | `e2e_harness.py:1070-1081` returns before the Reviewer half | High |
| R-2 | **The classification-verifying Reviewer attempt consumes a correction iteration** because `gate_attempts()` counts Reviewer attempts | `e2e_harness.py:1501-1507` + `:1194-1196` | High |
| R-3 | **An addition to the shared `decision_policy` block breaks CI immediately** (C7 at 90/90, C11a key partition, C4 byte-parity) | `validate_skills.py:2312-2318`, `:2451-2457`, `:2480-2486` | High |
| R-4 | **Reusing `REVIEW_VERDICT: BLOCKED` for decision-blocked** collapses into `RESULT: FAIL` and routes into the correction loop — the opposite of the requirement | `reviews/common.md` verdict mapping; invariant `SKILL.md:2244` | High |
| R-5 | **Taking `WAITING_FOR_INPUT` as a RUN_STATUS encroaches on OS-31** and implies a resume capability that does not exist | live OS-31 Scope | Medium-High |
| R-6 | **Free-form Markdown becomes the control input**, contrary to the ORIGINAL_REQUEST, because today no machine-readable decision channel exists at all | A7 table: 5 of 13 fields missing | Medium-High |
| R-7 | **A duplicated Reviewer dispatch or a second loop is introduced** to carry the verification dispatch — note it need NOT add a fifth `round_kind`: it can reuse `phase_gate`/`correction` and stay invisible to the vocabulary check, so the guard is INV-D1/INV-D2/INV-D3 + mutation M-DUP (A1, A10), with `run_logging.py:111-116` only supplementary | `e2e_harness.py:1158`, `:1520`, `:1625`, `:1705`; `run_logging.py:111-116` | Medium |
| R-8 | **Guards added in `run_workflow()` rather than `run()` get duplicated three times** and drift | `e2e_harness.py:1520`, `:1409`, `:1706` all enter `run()` | Medium |
| R-9 | **Vacuous tests**: a "next phase was not dispatched" assertion that would also pass if nothing were ever dispatched | the repository's own stated rule, `test_decision_policy.py:4-8` | Medium |
| R-10 | **Scenario 8 (downstream expands a user decision) drifts into OS-30's supersession lineage** | live OS-30 Scope | Medium |
| R-11 | The live runtime path has **no deterministic iteration counter**, so parts of OS-29 are enforceable in `e2e_harness` code but only contractually in SKILL.md on the Orca path | no `phase_iterations` in `orca_runtime_harness.py` | Medium |

---

## Assumptions / Unknowns

Assumptions made, each with its determining or supporting source. None of these is a
user decision; each is settled by the ticket text, the repository, or the phase contract.

* **AS-1** — The live Jira OS-29 and `ORIGINAL_REQUEST.md` are consistent; where the
  ORIGINAL_REQUEST is more specific it is the operative statement of the same intent.
  *Grounds:* both read this session; no clause of either contradicts the other.
* **AS-2** — "Continuous" means "at every phase's three boundaries", not a monitoring
  process. *Grounds:* ORIGINAL_REQUEST, *Decision check boundaries*, first line.
* **AS-3** — This ANALYSIS names options and evidence for the terminal-vocabulary
  question (A4) and does not choose. *Grounds:* the task spec, "Do not decide it here --
  DESIGN decides".
* **AS-4** — `scripts/e2e_harness.py` is the deterministic authority for transitions and
  `orca_runtime_harness.py` for live dispatch; SKILL.md is the shipped contract both
  serve. *Grounds:* the module docstrings and `validate_skills.py`'s dependency
  direction.
* **AS-5** — Reading the branch's working tree (which contains untracked artifacts from
  earlier runs) is equivalent to reading `main @ b13f191` for every `scripts/`,
  `orca-worker-reviewer-*/` and `docs/` file cited. *Grounds:* `git status` shows only
  untracked files under `artifacts/`; no tracked source file is modified.

**Unknowns (genuine, and all resolvable by DESIGN without user input):**

* **U-1** — Whether the decision gate result is a new parsed line in the Worker/Reviewer
  result contract, or a Coordinator-side derivation from the optional
  `## Decision Record` section. A2 states the requirement; the mechanism is DESIGN's.
  Note C-5: a new *value* in `STATUS:` is high-cost, but a new *field* is not covered by
  `_find_choice`.
* **U-2** — Whether non-consumption is implemented in `gate_attempts()` (one edit) or at
  the three increment sites (three edits). A5 gives the trade-off.
* **U-3** — Whether the decision ledger is a new run-scoped append-only artifact modelled
  on `write_final_review_audit_record` or additional columns on `ORCHESTRATOR_LOG.md`, or
  both. A7 gives the precedent for each.
* **U-4** — How far "Decision ID and change lineage" goes before it becomes OS-30's
  supersession protocol (risk R-10).

### Open Questions / Conflicts

**None requiring user authority.** Every ambiguity encountered while analysing was
settled by an explicit requirement in the live ticket or `ORIGINAL_REQUEST.md`, by the
current code, or by the ANALYSIS phase contract, and each such call is recorded as
AS-1..AS-5 or U-1..U-4 above. The two candidates that looked like conflicts on first
reading, and why neither is one:

* *"The `## Decision Record` section is contractually optional (validated at
  `validate_skills.py:2488-2505`) vs. a missing decision record must not be presumed
  `CLEAR`."* Not a conflict: the requester explicitly separates the two objects — "A
  section being optional in a general document is NOT the same thing as a gate result
  that determines a transition being omissible." Resolved in A2 (R-A2-1/R-A2-2).
* *"OS-29 blocks a run before Final Review vs. the invariant `Final Adversarial Review
  runs after every requested phase set, with no exception` (`SKILL.md:2230`)."* Not a
  conflict: that invariant conditions the **`COMPLETED`** outcome
  (`SKILL.md:2231`, "All requested phases PASS + Final Adversarial Review PASS required
  for COMPLETED"). A decision-blocked run terminates `BLOCKED`, never `COMPLETED`, and a
  run that already terminates `BLOCKED` today likewise never reaches Final Review
  (`e2e_harness.py:1521-1526`).

No `NEEDS_INPUT` and no `CONFLICT` item arose in this phase, so this phase does not stop.

---

## Recommended Next Step

PLAN should carry forward, as the minimal change surface:

1. Place the decision check on the **Worker-result** boundary first (`e2e_harness.py:1015`),
   because that is the only boundary that executes at every risk level (risk R-1).
2. Reuse the **already-scheduled** Reviewer dispatch (`:1158`) for classification
   verification, adding no round and no dispatch site (A1). Prove it with the **direct
   dispatch-topology invariants INV-D1/INV-D2/INV-D3** over the `sessions`,
   `reviewer_attempts`, `correction_dispatches`/`revalidation_dispatches` and
   `dispatch_settled` ledgers, and carry the **M-DUP** mutation (an extra Reviewer round
   that reuses an existing `round_kind`) as the required non-vacuity control (A10). The
   "`ROUND_KIND_VALUES` stays at four" assertion is kept only as supplementary evidence
   for "no new phase vocabulary"; it is explicitly NOT the non-duplication proof.
3. Make non-consumption an edit to `gate_attempts()` (`:1501`) rather than to the three
   increment sites, and keep both dispatch ledgers writing (A5, C-9).
4. Do **not** modify the shared `decision_policy` block (C-1/C-2/C-3); add a tenth
   orchestration-only `#### … contract` anchor block for the gate lifecycle, mirroring
   only the decision *semantics* into the loop Skill (A6).
5. Choose the terminal-vocabulary option in DESIGN from A4's O1/O2/O3, with O1
   (`RUN_STATUS: BLOCKED` + closed REASON constant + sparse `decision_state` column) as
   the lowest-cost candidate and OS-31's ownership of `WAITING_FOR_INPUT` as the reason
   O2 is contra-indicated.
6. Add the five missing machine-readable fields (A7) via the existing sparse-column and
   append-only-record precedents, and a validator that rejects drift between the Markdown
   summary and the machine record.
7. Write the fourteen ORIGINAL_REQUEST scenarios as positive/negative fixture pairs, each
   with a co-located non-vacuity guard in the style the repository already mandates
   (A10).
8. Document limitations L1–L5 (A8) in the Skill, so the absence of pause/resume and of a
   question protocol is a stated boundary rather than a silent gap.

---

## Decision Record

The record itself is the machine-readable JSON at
`artifacts/runs/run_35b221ea299d/records/analysis_decision_record.json`. It is the
authority; the prose below only describes it. Per the OS-28 contract, `CLEAR` carries
**no** reason code — `policy.states["CLEAR"].reason_code_required` is false and
`validate_record()` rejects any non-null `reason_code`
(`scripts/decision_policy.py:1197`, `:1208-1212`) — and `required_evidence["CLEAR"]` is the
empty list (`orca-worker-reviewer-orchestration/SKILL.md:305`), so the grounds that
carry the state are the declared facts, not a code.

```json
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "run": "run_35b221ea299d",
  "phase": "analysis",
  "iteration": 2,
  "responsible_phase": "analysis",
  "role": "worker",
  "grounds": "No boundary element declared by this phase is triggering, and no contradiction between two explicit requirements was found. The two apparent contradictions examined are resolved in 'Open Questions / Conflicts' from the ticket's own text and from orca-worker-reviewer-orchestration/SKILL.md:2230-2231.",
  "scope": "Covers the ANALYSIS phase's own conduct. Items deferred to DESIGN (U-1..U-4) are phase-scope handoffs mandated by the task spec, not open decision items crossing the autonomy boundary."
}
```

**This record was VALIDATED, not merely described.** Command and verbatim output:

```text
$ python3 -c "
import json, sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from decision_policy import load_decision_policy, validate_record, DecisionPolicyError
p = load_decision_policy(Path('orca-worker-reviewer-orchestration/SKILL.md'))
rec = json.load(open('artifacts/runs/run_35b221ea299d/records/analysis_decision_record.json'))
validate_record(p, rec)
print('POSITIVE: validate_record accepted the record as written.')
mut = dict(rec); mut['reason_code'] = '(none - CLEAR carries no reason code)'
try:
    validate_record(p, mut); print('NEGATIVE CONTROL FAILED: mutant accepted')
except DecisionPolicyError as e:
    print('NEGATIVE CONTROL: rejected ->', e)
mut2 = dict(rec); mut2['open_decision_item'] = True
try:
    validate_record(p, mut2); print('CONTROL-2 FAILED: accepted')
except DecisionPolicyError as e:
    print('CONTROL-2: rejected ->', e)
"
POSITIVE: validate_record accepted the record as written.
NEGATIVE CONTROL: rejected -> state CLEAR must not carry a reason_code
CONTROL-2: rejected -> CLEAR declares ['open_decision_item'] as grounds, but they do
not satisfy the CLEAR entry condition -- state CLEAR requires any_of of
['determining_policy_source', 'explicit_user_authorization', 'no_open_decision_item'];
unsatisfied: ['determining_policy_source', 'explicit_user_authorization',
'no_open_decision_item']
```

The two controls are the non-vacuity halves: **NEGATIVE CONTROL** is exactly the defect
the previous iteration shipped — a parenthetical string supplied as `reason_code` — and
it is rejected, proving the acceptance above is not the validator ignoring the field.
**CONTROL-2** flips the single fact that carries the state and is rejected by the CLEAR
entry condition (`no_open_decision_item`, `scripts/decision_policy.py:867-877`), proving
the acceptance is not the validator ignoring the grounds. The same record was also
validated against `orca-worker-reviewer-loop/SKILL.md`'s policy and accepted, which is
consistent with C-2 (the two blocks are byte-identical as parsed JSON).

---

## Review Feedback Resolution

FINDING F-001: RESOLVED

The iteration-1 Decision Record supplied `REASON_CODE: (none — CLEAR carries no reason
code)`, which is a non-null `reason_code` value and is rejected by `validate_record()`
for state `CLEAR` (`scripts/decision_policy.py:1197`, `:1208-1212`). The record was
rewritten as a machine-readable JSON record at
`artifacts/runs/run_35b221ea299d/records/analysis_decision_record.json` that supplies
`"reason_code": null` and carries the CLEAR entry-condition fact
`"open_decision_item": false`, and it was **actually run through `validate_record()`**
rather than described. The command, its verbatim output, and two controls (the exact
iteration-1 defect as a negative control, and a flipped-fact control) are embedded in
the `## Decision Record` section above. Changed: `## Decision Record` section (rewritten
in full); new file `records/analysis_decision_record.json`.

FINDING F-002: RESOLVED

The claim that "a fifth `round_kind` would be the observable signature of a duplicated
loop" and the recommendation to assert only that `ROUND_KIND_VALUES` stays at four were
removed as load-bearing evidence and replaced with a direct dispatch-topology invariant
set. Changed in three places:

* **A1** (`### A1. Duplication risk with the existing review loop`) — the paragraph that
  made the round-kind claim is replaced by "How non-duplication is actually proved
  (corrected — F-002)", which states explicitly that `ROUND_KIND_VALUES` is a
  classification vocabulary that imposes no dispatch cardinality, lists the four
  existing append-only dispatch ledgers (`sessions` `e2e_harness.py:282`/`:170`,
  `_record_session` `:835-864`; `worker_attempts`/`reviewer_attempts` `:1012`/`:1194`;
  `correction_dispatches`/`revalidation_dispatches` `:1632`/`:1709`; `dispatch_settled`
  rows via `orca_runtime_harness.py:2100-2125`), and defines **INV-D1** (per-iteration
  role cardinality), **INV-D2** (no dispatch ordered after a blocking decision) and
  **INV-D3** (dispatch-site cardinality: three round-dispatch sites `:1520`, `:1625`
  (`:1405`), `:1705`; two agent-invoking `subprocess.run` sites, Worker `:981` and Reviewer
  `:1158`).
* **A10** (`### A10. Non-vacuity`) — the "Non-duplication (bonus, cheap)" bullet is now
  load-bearing and specifies mutation **M-DUP**: a mutant that dispatches a second
  Reviewer for the same `(phase, iteration)` after the PASS branch at `:1198`
  **while reusing the existing `phase_gate` label**, with (1) an anti-vacuity precheck
  that the mutant really added dispatches, (2) a co-located guard asserting the
  four-value round-kind assertion **still passes on the mutant** — the proof that the
  proxy is blind — and (3) the requirement that INV-D1/INV-D3 reject the mutant while
  returning clean on the unmutated control.
* **Recommended Next Step 2** — rewritten to carry INV-D1/INV-D2/INV-D3 and M-DUP, with
  the four-value assertion demoted to supplementary evidence for the separate
  "no new phase vocabulary" out-of-scope item. Risk row **R-7** was corrected for the
  same conflation.

No other section of the iteration-1 analysis was rewritten, and no new scope was added.
