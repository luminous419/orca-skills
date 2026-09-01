# TEST — Jira OS-29 "Add Continuous Decision and Escalation Gates to Every Phase"

Run: `run_35b221ea299d`  Phase: TEST  Iteration: 3  Risk: HIGH (explicit)
Branch: `os-29-continuous-decision-gates`  Base of this phase: `f072b4f`
Iteration-3 base: `1d1942c` (the IMPLEMENTATION iteration-4 correction)
Template: `orca-worker-reviewer-orchestration/templates/test.md`
Approved inputs read: `ORIGINAL_REQUEST.md`, `PLAN.md` (P4, P6a, P6b, P7, P10), `DESIGN.md`,
`IMPLEMENTATION.md`, `REVIEW_IMPLEMENTATION_iteration3.md` (PASS), `REVIEW_TEST.md` (FAIL, F-001),
`REVIEW_TEST_iteration2.md` (PASS), `FINAL_REVIEW.md` (FAIL, F-001),
`REVIEW_IMPLEMENTATION_iteration4.md` (PASS).

> **Iteration 3 in one line — downstream revalidation, not a correction round.**
> Nothing in the TEST phase was judged defective and there is **no finding to resolve**.
> The Final Adversarial Review FAILed the run on an **IMPLEMENTATION**-owned finding
> (the live `orca_runtime_harness` B1 guard refused the *permitted* current-phase
> verification Reviewer), IMPLEMENTATION iteration 4 fixed it at `1d1942c`, and this
> round re-argues the TEST phase's coverage claim against that changed tree. One
> statement was invalidated (**Remaining Gap 5**), one newly-reachable P6b cell was
> added (**live row 5**, +76 test lines, −0), and everything else revalidates
> unchanged with re-executed evidence. **No production code changed this round.**
>
> **Iteration 2 in one line.** The phase Reviewer FAILed iteration 1 on **F-001** — the
> same defect this artifact reported as **T-001** — and its Required Action was to
> *correct* it rather than continue to pin it. The ordering defect in
> `scripts/e2e_harness.py` is fixed, the `@unittest.expectedFailure` marker is gone
> because the requirement assertion now passes normally, and scenario 5 is asserted at
> HIGH like every other matrix row. The production delta is recorded under
> **"Production change made under a TEST-phase blocking finding"** below. Sections not
> touched by that correction are unchanged from iteration 1.

---

## Test Scope / Existing Test Assessment

### What IMPLEMENTATION already left behind

The suite at `f072b4f` (1,582 tests) already carried a substantial OS-29 body: the gate
parser/evaluator and A1–A6 in `scripts/test_decision_gate.py`, the transition matrix and
the M-DUP non-duplication mutation in `scripts/test_e2e_harness.py`, the ledger producer
in `scripts/test_run_logging.py`, the three drift directions in
`scripts/test_validate_skills.py`, and the live pre-`start_worker` half in
`scripts/test_orca_runtime_contract.py`. That work is **not** re-done here.

**Iteration 3 addendum.** IMPLEMENTATION iteration 4 (`1d1942c`, resolving Final Review
F-001) added a second live body: `DecisionGateLiveDispatchTests` grew from the B1-only
set to 23 tests covering the live B3-V verification admission and its negatives, and
`test_decision_gate.VerificationAdmissionTests` covers the admission predicate itself.
That work is **not** re-done here either — per this round's contract, the TEST phase does
not ship a second copy of IMPLEMENTATION's live tests. What this round does is re-argue
the TEST phase's own **coverage claim** against the tree those tests now describe, and
add the one P6b cell that became reachable and that they left uncovered (live row 5).

### The three material gaps this phase found

1. **Named fixtures did not exist.** P10's first TEST exit criterion asks for a *named*
   positive and a *named* negative per scenario, mapped to the P4 module. The cases
   existed; the **names** did not, so the mapping was an interpretation of test bodies
   rather than a lookup. A fixture name that stops naming a real case was undetectable.
2. **Six P4/P6a/P6b cells had no case at all** — scenario 2 end-to-end, scenario 11,
   B1 sites 2/3/4 for scenario 12, P6b row 5 through the real subprocess, the reachable
   A6 case (F10), and the exact iteration-1 Markdown-vs-machine defect as a fixture.
3. **A production defect the existing tests could not see** — finding **T-001**,
   raised as **F-001** by the phase Reviewer and **fixed at iteration 2** (below).
   `test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked` asserted scenario 5
   at **LOW only**, while P4 requires it "at HIGH through P6b row 2 → row 4, **and** at
   LOW". Running the same fixture at HIGH is what exposed it. This is the third time in
   this run that a green suite coexisted with a fail-open-shaped defect, and it is the
   reason the whole matrix below is executed rather than described.

### What was deliberately NOT done

**At iteration 1**, no production code was repaired: T-001 was reported as a finding, per
the TEST template's Mandatory Invariants, and the diff was 1,348 insertions and **0
deletions**.

**At iteration 2**, exactly one production line changed, and only because the phase
Reviewer's Required Action on F-001 directed it. That rule in the TEST template governs
*initial* TEST authoring — it is not a licence to leave a phase-gate FAIL unresolved once
the gate authority has ruled. Nothing else was widened: no refactor, no new guard, no
weakened guard, and no approved artifact (`ANALYSIS.md`, `PLAN.md`, `DESIGN.md`,
`IMPLEMENTATION.md`) was edited. See the labelled section below.

---

## Added / Modified Tests

| File | Added | Nature |
| --- | --- | --- |
| `scripts/test_e2e_harness.py` | +1,029 lines, 10 tests in 3 new classes | the named fixtures, the executable scenario matrix, the six missing transition cells, T-001/F-001 and its narrowness control |
| `scripts/test_decision_gate.py` | +236 lines, 5 tests in 3 new classes | scenario 11 at the contract level, scenario 8's drift rule, the exact F-001 string |
| `scripts/test_run_logging.py` | +119 lines, 3 tests in 1 new class | scenario 2's and F10's producer halves, append-only |
| `scripts/fake_worker.py` | +9 lines | one additive CLI seam (below) |
| `scripts/test_orca_runtime_contract.py` | **iteration 3:** +76 lines, 1 test | P6b **row 5 on the live path** — the cell the upstream correction made reachable and did not cover (below) |
| `scripts/test_decision_gate.py` | **iteration 3:** +34 lines, 1 test + 2 names on an existing closed list | scenario 7's structural claim had silently narrowed when `1d1942c` added a gate function; the list is widened and made self-checking (below) |

**The one non-test edit, and why it is not a production change.**
`scripts/fake_worker.py` is a test double. It gained `--decision-gate-record-extend`,
which merges JSON into the *default* record for a state. It is needed because
`--decision-gate-record-raw` discards the harness-stamped `verifies` binding, so a
record that must be **both bound and unusual** — a Reviewer offering a timeout as user
authority, which is scenario 11's whole shape — was unreachable through the real
subprocess. The extension is applied **before** `extra`, so the harness-supplied
`verifies` always wins: a test may add fields, never forge the binding edge. No existing
flag changed behaviour; `validate_skills.py` (697 checks) and the release manifest (189
source files) are unchanged.

---

## Behavior Covered

### The fourteen scenarios: named positive, named negative, module

Fixture names are the ones PLAN P4's table gives them. Every name in the "fixture"
columns is a real callable or test in the named module, and
`DecisionGateScenarioMatrixTests` executes the transition-shaped ones as a matrix — so a
name that stopped naming a real case breaks the suite, not only this document.

| # | Scenario | POSITIVE fixture → test | NEGATIVE fixture → test | Module (P4) |
| --- | --- | --- | --- | --- |
| 1 | `CLEAR` + PASS → next phase | `clear_pass_proceeds` → `DecisionGateNamedScenarioTests.test_scenario_1_clear_pass_proceeds_and_an_absent_record_does_not` | `clear_pass_but_absent_record` → same function; `red_absent` (F9) → `DecisionGateTransitionTests.test_the_first_phase_needs_the_run_entry_declaration` | `test_e2e_harness.py` |
| 2 | `ASSUMPTION_ALLOWED` + grounds → record, then proceed | `assumption_allowed_six_facts_declared` → `DecisionGateNamedScenarioTests.test_scenario_2_assumption_allowed_is_recorded_and_then_proceeds`; record half → `DecisionLedgerTestPhaseTests.test_scenario_2_the_assumption_and_its_grounds_survive_the_round_trip` | `assumption_allowed_one_fact_undeclared` (six variants, one per declared safety fact) → same function | `test_e2e_harness.py` + `test_run_logging.py` |
| 3 | `NEEDS_INPUT` → correction Worker and next phase blocked, iteration NOT consumed | `needs_input_blocks` → `DecisionGateTransitionTests.test_needs_input_blocks_identically_at_every_risk_level` | `quality_fail_consumes_iteration` → `DecisionGateTransitionTests.test_a_decision_block_charges_no_iteration_and_a_quality_fail_still_does` | `test_e2e_harness.py` |
| 4 | `CONFLICT` → correction Worker and next phase blocked | `conflict_requirement_contradiction` → `DecisionGateTransitionTests.test_a_reviewer_downgrade_is_rejected_and_still_terminal` (confirming control) + matrix row 4+ | `conflict_downgraded_without_grounds` → same test (both downgrade targets); row 7 → `test_an_unbound_verification_record_fails_closed`; contract half → `VerificationTests.test_a_downgrade_is_decided_by_the_shared_contract_alone` | `test_e2e_harness.py` + `test_decision_gate.py` |
| 5 | high-impact decision found during IMPLEMENTATION → blocked outcome | `implementation_midwork_block` → matrix row 5+ at **HIGH** (P6b row 2 → row 4) + `DecisionGateTransitionTests.test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked` (LOW) + `DecisionGateFindingT001Tests.test_the_ledger_records_the_route_each_risk_level_actually_took`; **cross-risk equality** (LOW = MEDIUM = HIGH) → `DecisionGateFindingT001Tests.test_t001_a_midwork_block_must_be_a_decision_terminal_at_every_risk_level`, no longer `expectedFailure` | `implementation_same_item_declared_clear` → matrix row 5−; the `WORKER_BLOCKED` control → `test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked` (LOW) and `DecisionGateFindingT001Tests.test_the_worker_blocked_terminal_still_exists` (LOW/MEDIUM/HIGH) | `test_e2e_harness.py` |
| 6 | Worker auto-approves without authority → Reviewer FAILs it as blocking | `worker_unauthorized_high_impact` → `DecisionGateTransitionTests.test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing` + matrix row 6+ | `worker_high_impact_with_explicit_authorization` → matrix row 6−; the axis-separation control (row 9, iteration **charged**) → same transition test | `test_e2e_harness.py` (+ `test_decision_gate.py` for INV-4) |
| 7 | LOW risk → decision authority NOT expanded | `same_record_at_low_medium_high` → `RiskIndependenceTests.test_no_gate_function_takes_a_risk_or_profile_parameter` (**structural**, `inspect.signature`; **iteration 3** widened its closed list to all 13 public gate functions and added `test_the_gate_function_list_is_every_public_function_in_the_module` so it can never silently narrow again) + `test_the_module_source_never_branches_on_a_risk_level`; **behavioural** → `test_needs_input_blocks_identically_at_every_risk_level` | co-located guard: the three runs must differ elsewhere (`reviewer_attempts` 0 at LOW, 1 at HIGH), asserted in the same function | `test_decision_gate.py` + `test_e2e_harness.py` |
| 8 | downstream expands a user decision → new decision or escalation | `downstream_expands_decision` → `DecisionGateTransitionTests.test_a_downstream_expansion_is_a_new_decision_event_not_a_lineage_link` + matrix row 8+; drift rule → `DownstreamExpansionTests.test_downstream_expands_decision_opens_a_new_item_and_links_nothing` | `downstream_within_original_decision` → same tests | `test_e2e_harness.py` + `test_decision_gate.py` |
| 9 | Final Review with an unresolved decision → completion forbidden | `final_review_unresolved_decision` → `DecisionGateTransitionTests.test_an_unresolved_decision_forbids_final_review_completion` and `test_a_final_review_quality_pass_cannot_complete_over_a_blocking_decision` + matrix row 9+ | `final_review_all_decisions_resolved` → matrix row 9−; defect half → `test_a_defective_final_review_decision_result_fails_closed` | `test_e2e_harness.py` |
| 10 | Worker and Reviewer agree on the same unauthorized assumption → no approval | `worker_reviewer_agree_unauthorized` → matrix row 10+ (`DECISION_DOWNGRADE_REJECTED`); ledger half → `AdmissibilityTests.test_a_worker_reviewer_agreement_never_resolves_an_open_item` | the closed-set precheck (`len(forbidden_authority_sources) == 5`, `worker_reviewer_agreement` ∈ it) co-located in both tests | `test_decision_gate.py` + `test_e2e_harness.py` |
| 11 | timeout / non-response → no approval, no iteration | `timeout_no_response` → `TimeoutAndNonResponseTests.test_timeout_no_response_is_never_an_approval` and `test_a_timeout_never_resolves_an_open_ledger_item`; end-to-end → `DecisionGateNamedScenarioTests.test_scenario_11_a_timeout_or_non_response_approves_nothing_and_charges_nothing` | `quality_fail_consumes_iteration` (shared control) → same e2e function, plus the admitted-source control (`explicit_user_reply` takes a different path) | `test_decision_gate.py` + `test_e2e_harness.py` |
| 12 | illegal dispatch after a blocking decision → fail closed | `illegal_dispatch_after_block` → site 1 `DecisionGateTransitionTests.test_an_open_ledger_item_blocks_the_next_phase_dispatch`; sites 2/3/4 `DecisionGateNamedScenarioTests.test_scenario_12_an_illegal_dispatch_is_refused_at_every_remaining_b1_site`; live path `DecisionGateLiveDispatchTests.test_an_unresolved_open_item_refuses_the_next_dispatch` (a **Worker** dispatch — see the adjacency note below) and, since `1d1942c`, its eight-row companion `DecisionGateLiveDispatchTests.test_every_other_dispatch_after_a_blocking_worker_stays_refused` | `guard_removed_mutant` → `test_nv1_removing_the_guard_lets_the_same_scenario_dispatch` and mutants **M-B1-FR / M-B1-CORR / M-B1-REVAL** below; F11 `red_offered_at_later_boundary` → `test_the_declaration_cannot_stand_in_for_a_deleted_settled_record` | `test_e2e_harness.py` + `test_orca_runtime_contract.py` |
| 13 | missing or malformed decision result → no `CLEAR` presumption | the exact RED admitted through the complete A1–A6 path → `AdmissibilityTests.test_the_run_entry_declaration_is_admissible_at_exactly_one_position` | F1–F8 → `GateResultParsingTests.test_a_valid_declaration_is_admitted_and_every_defect_is_refused` and end-to-end `test_a_silent_or_broken_agent_never_presumes_clear`; F9–F14 → the F-table below; mutant **M-PRESUME-CLEAR** | `test_decision_gate.py`, `test_e2e_harness.py`, `test_run_logging.py` |
| 14 | decision-semantics drift between the two Skills → validation failure | the shipped pair validates → `ValidatorRegressionTests.test_valid_repository_passes` (697 checks) | (a) one Skill only → `test_os29_mirrored_semantics_changed_in_one_skill_fails`; (b) deleted from **both** → `test_os29_mirrored_semantics_deleted_from_both_skills_fails`; (c) orchestration-only block copied into the loop Skill → `test_the_orchestration_only_gate_block_leaking_into_the_loop_fails`; plus `test_decision_gate_contract_removed_fails` / `test_decision_gate_contract_value_drift_fails` | `test_validate_skills.py` |

**Iteration 3 — scenario 12 and the PERMITTED dispatch that now sits beside it.**
The upstream correction put one *legal* dispatch adjacent to scenario 12's illegal ones,
so the question this round has to answer is whether any negative fixture accidentally
covers the permitted case as illegal. It does not, and the separation is structural
rather than incidental:

* **Site 1 / sites 2–4 (deterministic).** `test_an_open_ledger_item_blocks_the_next_phase_dispatch`
  dispatches the **next phase**; `test_scenario_12_an_illegal_dispatch_is_refused_at_every_remaining_b1_site`
  drives `b1("final_review")`, `b1("correction")` and `b1("downstream_revalidation")`.
  None of the four is a current-phase Reviewer, and none offers a `verifies` binding, so
  none of them can reach the exception even in principle: `admit_head()`'s `verification`
  parameter defaults to `None` and A5 refuses every caller that leaves it there. These
  four rows were never about the permitted dispatch and still are not.
* **The live row.** `test_an_unresolved_open_item_refuses_the_next_dispatch` dispatches
  `("worker", 2, "complete", …, phase="implementation")` — a **correction Worker**, read
  from the test body, not inferred. The role conjunct refuses it first, so it stays a
  true negative against exactly the ledger the positive now admits.
* **The boundary is asserted from the other side too.** `test_every_other_dispatch_after_a_blocking_worker_stays_refused`
  runs **eight** near-miss rows against the *same* ledger — an unbound Reviewer, a
  Reviewer bound to another record, another phase, another iteration, a correction
  Worker, a Worker *forging* the binding, the Final Review, and the next phase — and
  closes with the fully-bound Reviewer being **admitted** as a co-located control. So
  the permitted case is covered as permitted, in the same function that proves its
  eight neighbours are not.

Scenario 12's claim is therefore unchanged in substance and stronger in evidence: after a
blocking decision every dispatch fails closed **except** the one P6b row 2 names, and the
exception is now proved to be reachable rather than merely refused everywhere.

### P6a's B1 input-admissibility negatives (F9–F14)

| Fixture | Test | Module |
| --- | --- | --- |
| `red_present_first_phase_dispatches` (positive, NV-1's control) | co-located in `test_the_first_phase_needs_the_run_entry_declaration` | `test_e2e_harness.py` |
| **F9** `red_absent` | `test_the_first_phase_needs_the_run_entry_declaration` | `test_e2e_harness.py` |
| **F10** `red_disagrees_with_ledger` | `DecisionGateNamedScenarioTests.test_f10_the_run_entry_declaration_is_recomputed_and_never_rubber_stamped` (two reachable shapes) + `DecisionLedgerTestPhaseTests.test_f10_the_declaration_is_never_rewritten_by_a_later_open_item` + `AdmissibilityTests.test_the_declaration_is_recomputed_and_an_open_item_blocks` | `test_e2e_harness.py` + `test_run_logging.py` + `test_decision_gate.py` |
| **F11** `red_offered_at_later_boundary` | `test_the_declaration_cannot_stand_in_for_a_deleted_settled_record` (**with** its co-located control) + `AdmissibilityTests.test_the_run_entry_declaration_is_admissible_at_exactly_one_position` | `test_e2e_harness.py` + `test_decision_gate.py` |
| **F12** `two_sequence_zero_records` | `DecisionLedgerProducerTests.test_f12_the_producer_is_idempotent_and_first_writer_wins` + `AdmissibilityTests.test_absence_and_inconsistency_are_refusals_never_clear` | `test_run_logging.py` + `test_decision_gate.py` |
| **F13** `ledger_record_schema_version_missing` (absent / text / bool / smuggled policy key) | `LedgerRecordValidationTests.test_every_ledger_record_declares_a_supported_schema_version` + `test_an_unreadable_or_unsupported_ledger_record_blocks_end_to_end` | `test_decision_gate.py` + `test_e2e_harness.py` |
| **F14** `ledger_record_schema_version_unsupported` | `SchemaVersionCompatibilityTests.test_a_future_version_fails_closed_in_both_directions` (RED **and** B2 agent record) + the e2e refusal above | `test_decision_gate.py` + `test_e2e_harness.py` |

**F10, and why A6 needed two shapes.** P6a fixes the evaluation order at
**A1 → A2 → A4 → A3 → A6 → A5**. A3 therefore fires *before* A6 whenever the ledger is
non-empty at a run's **first** B1, because `expected_settled_round` is `None` there and
no head can bind. The consequence, verified rather than assumed: a literally reused
`run_id` refuses with `DECISION_GATE_INPUT_UNBOUND`, and `DECLARATION_DISAGREES_WITH_LEDGER`
is reachable at a **later** B1, where the head does bind and the declaration is then
recomputed against the ledger. Both are asserted in one function:

* **shape 1** — a ledger that gains an open item the declaration does not name ⇒
  `DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER`, first phase already dispatched,
  second phase never does. Controls in the same function: the *identical* planted ledger
  with an **honest** declaration refuses with `DECISION_BLOCKED:...` instead (one field
  differs and it changes which clause names the defect), and with nothing planted the
  same scenario `COMPLETED`s.
* **shape 2** — a first run legitimately **blocks** and leaves its open record on disk; a
  second run reuses the same `run_id` **and** workspace. `open_decision_ledger` is
  first-writer-wins, so the surviving declaration still claims `prior_open_decision_items:
  []` — asserted — and the second run dispatches **zero** agents. Control: the same
  scenario in a fresh workspace completes.

Mutant **M-A6** (below) makes A6 a rubber stamp and kills shape 1, so the clause is not
vacuous.

### Every cell of P6b's two tables

| Cell | Case | Where |
| --- | --- | --- |
| **B2 row 1** — valid `CLEAR`/`ASSUMPTION_ALLOWED` admits | `clear_pass_proceeds`, `assumption_allowed_six_facts_declared` | matrix rows 1+/2+, `test_scenario_1...`, `test_scenario_2...` |
| **B2 row 2** — valid blocking result: terminal at LOW, `verification_only` at MEDIUM/HIGH | `needs_input_blocks` at all three levels | `test_needs_input_blocks_identically_at_every_risk_level` |
| **B2 row 3** — missing/malformed/unknown: terminal at every level, **no Reviewer spent** | silent, unparseable and duplicated-field variants, `reviewer_attempts == []` asserted | `test_a_silent_or_broken_agent_never_presumes_clear` |
| **B3 row 4** — verification **confirms** ⇒ byte-identical to the LOW terminal | cross-risk equality on `final_status`, `decision_state`, `reason_code` | `test_needs_input_blocks_identically_at_every_risk_level`; contract half `VerificationTests.test_a_confirmation_is_byte_identical_to_the_low_terminal` |
| **B3 row 5** — verification **stricter** ⇒ the Reviewer's own state | `NEEDS_INPUT` → `CONFLICT` through the real subprocess, with the confirming and the CLEAR controls co-located | **new** `test_p6b_row_5_a_stricter_verification_carries_the_reviewers_own_state`; contract half `VerificationTests.test_a_stricter_verification_carries_the_reviewers_own_state`; **live half added at iteration 3** — `DecisionGateLiveDispatchTests.test_p6b_row_5_a_stricter_live_verification_carries_the_reviewers_own_state` |
| **B3 row 6** — proposed **downgrade** ⇒ decided only by `validate_transition()` | both targets rejected; `timeout`/`no_response`/`worker_reviewer_agreement` rejected; `explicit_user_reply` accepted and **still terminal** | `test_a_reviewer_downgrade_is_rejected_and_still_terminal`, `TimeoutAndNonResponseTests`, matrix rows 10+/11+ |
| **B3 row 7** — verification record **not bound** | forged `verifies` ⇒ `DECISION_GATE_INPUT_UNBOUND`, with the bound control | `test_an_unbound_verification_record_fails_closed`, `VerificationTests.test_an_unbound_verification_record_is_its_own_defect` |
| **B3 row 8** — Reviewer discovers the block in **normal** mode | finding recorded in `finding_traces` **and** no iteration charged; Final-Review edge too | `test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing`, `test_a_final_review_quality_pass_cannot_complete_over_a_blocking_decision` |
| **B3 row 9** — valid `CLEAR`/`ASSUMPTION_ALLOWED`: **existing** routing untouched | PASS → next phase; FAIL → correction with the iteration **charged** | co-located axis-separation control in `test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing`; `test_a_fully_clear_run_adds_artifacts_without_changing_a_transition` |
| **B3 row 10** — missing/malformed Reviewer result | B3 and the Final Review edge, seven defect shapes | `test_a_silent_or_broken_agent_never_presumes_clear`, `test_a_defective_final_review_decision_result_fails_closed` |

#### Iteration 3 — the live half of P6b, now that it exists

At iterations 1–2 every cell above was a **deterministic-harness** cell, and that was
correct then for a reason PLAN states: P6b's two tables are written against
`e2e_harness.py` line positions, and P2 row **C4** scoped the live harness to a
"pre-`start_worker` B1 guard" and nothing else. Final Review F-001 changed the scope
decision, not the table: the live path must implement the same P6b row 2 → row 4
transition, and `1d1942c` implemented it. So each row now has a live half, and the honest
question for this round is which of them has a case.

| P6b cell | Live case | Owner |
| --- | --- | --- |
| **B2 row 1** — valid `CLEAR`/`ASSUMPTION_ALLOWED` admits | `test_a_declaring_dispatch_records_a_bound_ledger_entry`, `test_an_ordinary_round_is_never_put_into_verification_mode` | IMPLEMENTATION |
| **B2 row 2** — blocking result falls through to the already-scheduled Reviewer | `test_the_already_scheduled_reviewer_verifies_a_blocking_classification`, `test_a_conflict_classification_takes_the_same_live_verification_path` | IMPLEMENTATION (iteration 4) |
| **B2 row 3** — missing/malformed/unsupported, terminal, never presumed `CLEAR` | `test_a_silent_worker_result_poisons_the_next_boundary`, `test_a_declared_but_broken_body_poisons_the_next_boundary`, `test_a_non_response_is_not_a_boundary_and_is_never_presumed_clear`. The row's *"no Reviewer spent"* half takes a different live form, and the three tests assert it: the live harness never decides *whether* to dispatch a Reviewer — the Coordinator does — so instead of withholding one it publishes no record, and the Reviewer's own B1 is then refused as `DECISION_GATE_INPUT_UNBOUND` before any dispatch happens | IMPLEMENTATION |
| **B3 row 4** — verification **confirms** ⇒ the Worker's own state and code | `test_the_already_scheduled_reviewer_verifies_a_blocking_classification` (asserts `DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope` on the log) | IMPLEMENTATION (iteration 4) |
| **B3 row 5** — verification **stricter** ⇒ the Reviewer's own state | **`test_p6b_row_5_a_stricter_live_verification_carries_the_reviewers_own_state`** — *the gap this round found and filled* | **TEST (iteration 3)** |
| **B3 row 6** — proposed **downgrade** ⇒ `validate_transition()` alone, still terminal | `test_an_accepted_downgrade_is_recorded_and_still_terminal` | IMPLEMENTATION (iteration 4) |
| **B3 row 7** — verification record **not bound** | `test_an_admitted_reviewer_still_owes_a_bound_verification_record` and its mirror `test_a_verifies_claim_outside_verification_mode_is_unbound` | IMPLEMENTATION (iteration 4) |
| **B3 rows 8–10** — normal-mode Reviewer, existing routing, malformed Reviewer result | `test_an_ordinary_round_is_never_put_into_verification_mode`, `test_a_silent_reviewer_result_poisons_the_next_boundary`, `test_a_silent_result_is_named_as_a_defect_on_the_live_log_row` | IMPLEMENTATION |

**Why row 5 was the one hole.** `evaluate_verification()` (`decision_gate.py:754`) has
three branches, and the live harness reaches all three through one call at
`orca_runtime_harness.py`. Iteration 4's live tests exercise the **confirm** branch
(`NEEDS_INPUT`→`NEEDS_INPUT`, and `CONFLICT`→`CONFLICT`, which is *also* a confirmation)
and the **downgrade** branch (`NEEDS_INPUT`→`CLEAR`). The **stricter** branch —
`reviewer.state in BLOCKING_STATES` **and** `(state, reason_code) != (worker's)` — had a
contract case and a deterministic case but no live one, because before F-001 was resolved
no live Reviewer could be admitted past an open blocking head at all. The new test drives
Worker `NEEDS_INPUT` → Reviewer `CONFLICT` through the real live path and asserts the
terminal carries `DECISION_BLOCKED:CONFLICT:requirement_contradiction` and **not** the
Worker's `DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope`.

**Its co-located control, in the same function**, is the confirming Reviewer in a fresh
workspace: one field different (`CONFLICT` → `NEEDS_INPUT` in the Reviewer's declaration)
and the assertion flips — the Worker's code appears and the Reviewer's does not. So the
`assertNotIn` is attributable to the branch and not to the log never having carried that
string. Terminality is asserted too, and row 5 turns out to be **more** terminal than
row 4: `open_items()` returns **both** the Worker's still-open B2 item (no
`verifies`-bearing record may resolve anything) **and** the Reviewer's own stricter
classification as a second open item, and the correction Worker is refused. Non-vacuity is
mutant **M-ROW5-LIVE** below.

**This is not a duplicate of IMPLEMENTATION's live tests.** It is one cell of P6b's own
table that had a contract half and a deterministic half and no live half; the file it
lives in is IMPLEMENTATION's, the class is IMPLEMENTATION's, and only the row is new.
Nothing else was added on the live path, and the eight live negatives, the admission
predicate tests and the terminality tests are left exactly as `1d1942c` shipped them.

**Cross-risk equality (rows 2 and 4)** is asserted on all three of `final_status`,
`decision_state` and `reason_code` in
`test_needs_input_blocks_identically_at_every_risk_level`, with the co-located guard that
the three runs genuinely differ elsewhere (`len(reviewer_attempts)` is 0 at LOW and 1 at
HIGH). At iteration 1 the same equality **failed** for the `STATUS: BLOCKED` shape — that
was T-001. Since the iteration-2 fix it holds for that shape too, asserted in
`DecisionGateFindingT001Tests.test_t001_a_midwork_block_must_be_a_decision_terminal_at_every_risk_level`
and demonstrated verbatim under **Execution → Cross-risk equality for scenario 5**.

### The three non-vacuity proofs

| Proof | Construction | Co-located control, in the SAME function |
| --- | --- | --- |
| **NV-1** dispatch blocking | `test_nv1_removing_the_guard_lets_the_same_scenario_dispatch` patches `decision_gate.admit_head` to always admit; the previously-blocked run then `COMPLETED`s with non-empty `sessions` | the unpatched run in the same function blocks with `sessions == ()`; `red_present_first_phase_dispatches` inside `test_the_first_phase_needs_the_run_entry_declaration`; and mutants M-B1-FR/CORR/REVAL for the three sites the phase gate cannot reach |
| **NV-2** iteration non-consumption | `test_a_decision_block_charges_no_iteration_and_a_quality_fail_still_does`; also asserted for scenario 11 in `test_scenario_11_...` | the quality-`FAIL` round in the same function increments `phase_iterations` to 2 |
| **NV-3** non-duplication (M-DUP) | `DecisionGateNonDuplicationTests.test_m_dup_fails_the_invariants_while_the_control_passes` — (1) the mutation is not a no-op (`assertGreater` on session and reviewer counts), (2) the `ROUND_KIND_VALUES` proxy **still passes** on the mutant, (3) INV-D1/INV-D3 reject it | the unmutated blocked run and a CLEAR run both return **clean** from the same invariant checker |

**Iteration 3 — do the three NV proofs still hold against the corrected upstream?** Yes,
and the reasoning is not "they still pass":

* **NV-1** patches `decision_gate.admit_head` with `lambda *args, **kwargs: {"state":
  "CLEAR"}` (`test_e2e_harness.py:5802-5803`). Iteration 4 added a keyword-only
  `verification=` parameter to the real `admit_head`; the `**kwargs` in the double
  absorbs it, so the mutation is still total rather than accidentally partial. The
  construction is also unaffected in substance: the run it neutralizes is a
  **deterministic** run whose caller offers no verification, so it is refused by A5
  before and after `1d1942c`, and the mutant still turns `BLOCKED`/`sessions == ()` into
  `COMPLETED` with non-empty `sessions`.
* **NV-2** is a counter claim about `gate_attempts()` in `e2e_harness.py`, which
  `1d1942c` did not touch. Re-run green, and the live half of the same claim is asserted
  independently by IMPLEMENTATION's `test_the_already_scheduled_reviewer_verifies_a_
  blocking_classification` (no third dispatch, ledger stays at three records).
* **NV-3 / M-DUP** is the proof that matters most for this correction, because F-001's
  fix is exactly the kind of change that could turn "the same Reviewer, in a different
  mode" into "a Reviewer ran twice". It still fails INV-D1/INV-D3 while both controls
  return clean. The live path is held to the same rule from the other side: the *second*
  Reviewer is one of the eight refused rows in `test_every_other_dispatch_after_a_
  blocking_worker_stays_refused`, and `ROUND_KIND_VALUES` is still four.

**Is anything now under-covered from the TEST phase's own perspective?** **Two** things
were, and both are fixed above:

1. **P6b row 5 on the live path** — the transition cell that became reachable and had no
   live case. Mutant **M-ROW5-LIVE**.
2. **Scenario 7's structural claim had silently narrowed.**
   `RiskIndependenceTests.GATE_FUNCTIONS` is a hand-written closed list, and the
   `inspect.signature` assertion is only as wide as that list. `1d1942c` added
   `verification_admission_defect()` — a *new decision-authority function* — to
   `decision_gate.py` and the list did not gain it, so the newest gate function was
   never inspected for a `risk` / `profile` / `severity` parameter while the test stayed
   green. (`declares_gate_result` had been missing since iteration 1 for the same
   reason.) The list now names all **13** public functions in the module, and a new
   co-located test asserts the list **equals** the module's public surface, so the next
   gate function that appears fails this test instead of quietly escaping it. Mutant
   **M-SIG-LIST** re-creates the exact omission and kills it. This is a strengthening —
   no assertion was removed, relaxed or renamed — and it is the TEST phase's own claim,
   not a second copy of anything IMPLEMENTATION wrote.

Nothing else. The scenario matrix, the F9–F14 table and the P7 drift tests are statements
about `decision_gate.py`'s contract or about `e2e_harness.py`, and the *behavioural* half
of `1d1942c`'s contract change — `admit_head`'s optional `verification` parameter and the
six conjuncts of `verification_admission_defect()` — is covered by IMPLEMENTATION's own
`VerificationAdmissionTests` plus its eight live negatives, which this round deliberately
does not copy.

Every control above is inside the test function whose claim it protects, on the
convention `scripts/test_decision_policy.py:4-8` records. The new classes follow it and
say so; the data-driven loops added this phase each carry a cardinality guard before the
loop (`len(declared) == 6`, `len(forbidden) == 5`, `len(sites) == 3`, the matrix's
`covered` set and its 19/18 row/name counts).

### The P7 Markdown-vs-machine drift validator, on the real historical defect

The fixture is `scripts/fixtures/decision_gate/invalid/clear_carries_a_reason_code.json`,
whose `reason_code` is the byte string this run's ANALYSIS iteration 1 actually wrote:
`(none - CLEAR carries no reason code)`. Two new tests, plus the two that already used it:

* `MarkdownVersusMachineDriftTests.test_the_iteration_one_reason_code_string_is_refused_by_the_gate`
  builds a document whose **human half reads exactly as it did in iteration 1** — the
  `## Decision Record (optional)` section carrying `DECISION_STATE: CLEAR` and the literal
  `REASON_CODE: (none - CLEAR carries no reason code)` line — beside the machine record
  that supplies that string as `reason_code`. The document is **refused**
  (`DECISION_GATE_INPUT_MALFORMED`). **The co-located control differs in exactly one
  field**: the same document with `reason_code: null` — the correction this run actually
  made — is **admitted**, prose unchanged. So the refusal is attributable to the record's
  `reason_code` and not to the prose, the section or the state.
* `MarkdownVersusMachineDriftTests.test_the_same_string_is_refused_as_a_ledger_record`
  repeats it through `validate_ledger_record` (A4-iii), i.e. when the record is read back
  off the ledger, with the null-reason-code control.
* Already present: `GateResultParsingTests.test_the_markdown_narrative_alone_never_admits`
  and `LedgerRecordValidationTests.test_each_state_carries_all_thirteen_fields_...`;
  `run_logging.reconcile_decision_record_section` and its
  `test_the_motivating_drift_case_of_this_run`.

---

## Execution

All commands from the repository root on `os-29-continuous-decision-gates`, working tree
as committed by this phase.

### Full regression suite

Iteration 3 (current — re-executed against `1d1942c`, the corrected upstream, plus this
round's two added tests):

```text
Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  PASS
         Ran 1615 tests in 318.762s
         OK (skipped=6)
```

**Zero expected failures**, no test deleted, no validator weakened. The whole iteration-3
diff under `scripts/` is `test_orca_runtime_contract.py` **+76 / −0** and
`test_decision_gate.py` **+34 / −0** — **no production file changed.** `1d1942c` had taken
the suite from 1,600 to **1,613**; this round takes it to **1,615** (**+2**, **−0**).

Iteration 2 (superseded — the tree it measured no longer exists):

```text
Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  PASS
         Ran 1600 tests in 323.139s
         OK (skipped=6)
```

**Zero expected failures.** The `@unittest.expectedFailure` marker on
`test_t001_a_midwork_block_must_be_a_decision_terminal_at_every_risk_level` was removed
because the requirement assertion now passes **normally** — that is the opposite of
weakening a test, and it is verified by mutant **M-T001** below, which reverts the
one-line production fix and watches the same assertion fail.

Baseline at `f072b4f` was **1,582** tests (re-measured before any edit:
`Ran 1582 tests in 309.584s / OK (skipped=6)`). Iteration 1 reached 1,599
(`OK (skipped=6, expected failures=1)`). Iteration 2 reaches **1,600**: **+18 against the
phase base, −0.** No test was deleted; one was **renamed**, disclosed below.

**The one rename.** `DecisionGateFindingT001Tests.test_the_low_terminal_is_correct_today`
→ `test_the_ledger_records_the_route_each_risk_level_actually_took`. The old name asserted
a fact about the *defect era* ("the LOW half is the half that still works"), which stopped
being true once every risk level was correct. Its body was **strengthened**, not weakened:
it now also asserts that MEDIUM/HIGH run exactly **one** Reviewer attempt (T-001 reported
zero) and that the B3 verification record is **bound** to the B2 record it verified. One
test was **added** — `test_the_worker_blocked_terminal_still_exists` — as the narrowness
control for the fix.

### The other four CI gates (`.github/workflows/*.yml:33-48`)

Re-executed at iteration 3 against `1d1942c` + this round's test edit; verbatim:

```text
Command: python3 scripts/validate_skills.py
Result:  PASS -- Skill validation PASSED (697 checks)
                 Validated both skills, shared templates/reviews, routing, and policy gates.

Command: python3 scripts/verify_package.py
Result:  PASS -- Package verification PASSED (189 source files)

Command: python3 scripts/build_release.py
Result:  PASS -- Built reproducible release archive:
                 /Users/luminous/aiAssistedProjects/orca-skills/dist/orca-skills-0.9.0.tar.gz

Command: git diff --check
Result:  PASS -- no output

Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result:  PASS -- byte-identical (untouched by iterations 2 and 3 alike)
```

The counts are unchanged from iteration 2 — 697 checks, 189 source files — which is the
expected outcome: neither the upstream correction nor this round added a Skill anchor, a
template, or a packaged source file.

Iteration 2 (superseded):

```text
Command: python3 scripts/validate_skills.py
Result:  PASS -- Skill validation PASSED (697 checks)
                 Validated both skills, shared templates/reviews, routing, and policy gates.

Command: python3 scripts/verify_package.py
Result:  PASS -- Package verification PASSED (189 source files)

Command: python3 scripts/build_release.py
Result:  PASS -- Built reproducible release archive: dist/orca-skills-0.9.0.tar.gz

Command: git diff --check
Result:  PASS -- no output
```

### Byte parity

```text
Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result:  PASS -- byte-identical (run_logging.py was not touched by this phase)
```

### Targeted

Iteration 3 (current), verbatim:

```text
Command: python3 -m unittest scripts.test_decision_gate scripts.test_os29_decision_gate \
           scripts.test_e2e_harness.DecisionGateTransitionTests \
           scripts.test_e2e_harness.DecisionGateNonDuplicationTests \
           scripts.test_e2e_harness.DecisionGateNamedScenarioTests \
           scripts.test_run_logging.DecisionLedgerProducerTests \
           scripts.test_run_logging.DecisionLedgerTestPhaseTests
Result:  PASS -- Ran 81 tests in 7.728s / OK      (75 at iteration 2; +5 from the
                 upstream VerificationAdmissionTests, +1 from this round's
                 RiskIndependenceTests addition)

Command: python3 -m unittest discover -s scripts -p 'test_validate_skills.py'
Result:  PASS -- Ran 181 tests in 20.342s / OK    (unchanged -- no Skill anchor moved)

Command: python3 -m unittest scripts.test_e2e_harness.DecisionGateScenarioMatrixTests
Result:  PASS -- Ran 1 test in 2.112s / OK
                 The matrix is what proves the fourteen-scenario table's fixture NAMES
                 still name real cases after the upstream correction: a name that
                 stopped naming one would fail here, not merely read wrong above.

Command: python3 -m unittest scripts.test_e2e_harness.DecisionGateFindingT001Tests
Result:  PASS -- Ran 3 tests in 0.433s / OK       (still no expected failures)

Command: python3 -m unittest scripts.test_orca_runtime_contract.DecisionGateLiveDispatchTests
Result:  PASS -- Ran 24 tests in 0.117s / OK      (23 from `1d1942c` + this round's
                 live P6b row 5)

Command: python3 -m unittest scripts.test_decision_gate.VerificationAdmissionTests \
           scripts.test_decision_gate.RiskIndependenceTests
Result:  PASS -- Ran 8 tests in 0.003s / OK       (5 upstream admission-predicate tests
                 + 3 risk-independence tests, one of them new this round)
```

Iteration 2 (superseded):

```text
Command: python3 -m unittest scripts.test_decision_gate scripts.test_os29_decision_gate \
           scripts.test_e2e_harness.DecisionGateTransitionTests \
           scripts.test_e2e_harness.DecisionGateNonDuplicationTests \
           scripts.test_e2e_harness.DecisionGateNamedScenarioTests \
           scripts.test_run_logging.DecisionLedgerProducerTests \
           scripts.test_run_logging.DecisionLedgerTestPhaseTests
Result:  PASS -- Ran 75 tests in 7.221s / OK

Command: python3 -m unittest discover -s scripts -p 'test_validate_skills.py'
Result:  PASS -- Ran 181 tests in 18.531s / OK
         (this module imports `run_logging` top-level, so it runs under discovery, not
          as `-m unittest scripts.test_validate_skills` -- a pre-existing property)

Command: python3 -m unittest scripts.test_e2e_harness.DecisionGateScenarioMatrixTests
Result:  PASS -- Ran 1 test in 1.833s / OK  (19 fixture rows, 10 scenarios;
                 row 5+ now runs at HIGH, not LOW)

Command: python3 -m unittest scripts.test_e2e_harness.DecisionGateFindingT001Tests
Result:  PASS -- Ran 3 tests in 0.419s / OK   (iteration 2: no expected failures)
```

### Cross-risk equality for scenario 5 (the F-001 exit criterion)

Direct execution of the `implementation_midwork_block` shape at each risk level, printing
the four columns the finding named plus the Reviewer-attempt count:

```text
risk    final_status  reason                                               decision_state  reason_code               rev
------------------------------------------------------------------------------------------------------------------------
low     BLOCKED       DECISION_BLOCKED:CONFLICT:requirement_contradiction  CONFLICT        requirement_contradiction 0
medium  BLOCKED       DECISION_BLOCKED:CONFLICT:requirement_contradiction  CONFLICT        requirement_contradiction 1
high    BLOCKED       DECISION_BLOCKED:CONFLICT:requirement_contradiction  CONFLICT        requirement_contradiction 1
------------------------------------------------------------------------------------------------------------------------
EQUAL across low/medium/high on (final_status, reason, decision_state, reason_code): True
```

Compare with iteration 1's table under **Failures / Findings** below: MEDIUM and HIGH
returned `WORKER_BLOCKED` with empty decision columns and **zero** Reviewer attempts. The
`rev` column is the intended asymmetry and not a violation of the equality: P6b row 2 puts
the LOW terminal at B2 with no Reviewer, and routes MEDIUM/HIGH through the
already-scheduled Reviewer at B3-V. Risk selects **where** the terminal is recorded; it
does not change **what it says**.

**The control, run the same way** — a Worker-declared `STATUS: BLOCKED` carrying **no**
decision block is still a plain `WORKER_BLOCKED` at every risk level, so the fix narrowed
one branch rather than deleting a terminal:

```text
risk    final_status  reason           decision_state  reason_code  rev
-----------------------------------------------------------------------
low     BLOCKED       WORKER_BLOCKED   ''              ''           0
medium  BLOCKED       WORKER_BLOCKED   ''              ''           0
high    BLOCKED       WORKER_BLOCKED   ''              ''           0
```

### Mutation / control pairs

**Ten** single-point mutations at iteration 3 — the eight from iteration 2, **all
re-executed against the corrected upstream `1d1942c`**, plus **M-ROW5-LIVE** and
**M-SIG-LIST** for the two cells this round added. Each is applied to the working tree,
the named tests are run, the mutation is reverted in a `finally` block, and the same tests are re-run as the
control. **Every mutant was killed and every control returned green**, with the same
failure counts as iteration 2 for the eight carried-over pairs — which is the evidence
that the upstream correction did not defang any of them. Verbatim output:

```text
KILLED   M-T001  the F-001 fix reverted: verification_only no longer crosses the
                 STATUS: BLOCKED branch
           mutant:  | FAILED (failures=4)
  control green:  | OK

KILLED   M-A6  A6 never fires (the declaration becomes a rubber stamp)
           mutant:  | FAILED (failures=1)
  control green:  | OK

KILLED   M-AUTH  a forbidden authority source is admitted as user authority
           mutant:  | FAILED (failures=8)
  control green:  | OK

KILLED   M-FACTS  an undeclared safety fact reads as declared
           mutant:  | FAILED (failures=6)
  control green:  | OK

KILLED   M-B1-FR  the Final Review B1 guard (site 2) removed
           mutant:  | FAILED (failures=1)
  control green:  | OK

KILLED   M-B1-CORR  the correction B1 guard (site 3) removed
           mutant:  | FAILED (failures=1)
  control green:  | OK

KILLED   M-B1-REVAL  the revalidation B1 guard (site 4) removed
           mutant:  | FAILED (failures=1)
  control green:  | OK

KILLED   M-PRESUME-CLEAR  a missing/defective gate result is presumed CLEAR
           mutant:  | FAILED (failures=1)
  control green:  | OK

KILLED   M-ROW5-LIVE  P6b row 5 flattened: a stricter verification carries the WORKER's state
           mutant:  | FAILED (failures=1)
  control green:  | OK

KILLED   M-SIG-LIST  the risk-independence closed list falls behind the module by one function
           mutant:  | FAILED (failures=1)
  control green:  | OK

ALL MUTANTS KILLED, ALL CONTROLS GREEN
```

| Mutant | Edit | Killed by |
| --- | --- | --- |
| **M-T001** | `e2e_harness.py`: `if worker_status == self.contract.worker_blocked and not verification_only:` → `if worker_status == self.contract.worker_blocked:` — i.e. the exact iteration-1 defect, restored | `DecisionGateFindingT001Tests` (3 failures) + `DecisionGateScenarioMatrixTests` (1) = 4 |
| **M-A6** | `decision_gate.py`: `if declared != recomputed:` → `if False:` | `test_f10_the_run_entry_declaration_is_recomputed_and_never_rubber_stamped` |
| **M-AUTH** | `decision_policy.py`: `if source not in policy.user_decision_sources:` → `if False:` | `test_scenario_11_...` + `TimeoutAndNonResponseTests` (8 failures) |
| **M-FACTS** | `decision_policy.py`: `_undeclared_safety_facts` → `return ()` | `test_scenario_2_...` (6 failures, one per fact) |
| **M-B1-FR** | `e2e_harness.py`: `refusal = b1("final_review")` → `refusal = None` | `test_scenario_12_an_illegal_dispatch_is_refused_at_every_remaining_b1_site` |
| **M-B1-CORR** | `e2e_harness.py`: `refusal = b1("correction")` → `refusal = None` | same |
| **M-B1-REVAL** | `e2e_harness.py`: `refusal = b1("downstream_revalidation")` → `refusal = None` | same |
| **M-PRESUME-CLEAR** | `decision_gate.parse_gate_result` returns an unconditional `CLEAR` — the literal fail-open the ticket forbids | `test_scenario_1_clear_pass_proceeds_and_an_absent_record_does_not` |
| **M-ROW5-LIVE** *(new, iteration 3)* | `decision_gate.evaluate_verification`: the stricter branch's `block_reason(reviewer.state, reviewer.reason_code)` → `block_reason(worker.state, worker.reason_code)`, i.e. row 5 collapsed into row 4 | `DecisionGateLiveDispatchTests.test_p6b_row_5_a_stricter_live_verification_carries_the_reviewers_own_state` |
| **M-SIG-LIST** *(new, iteration 3)* | `test_decision_gate.py`: `"verification_admission_defect"` deleted from `RiskIndependenceTests.GATE_FUNCTIONS` — i.e. **the exact under-coverage state this round found**, restored | `RiskIndependenceTests.test_the_gate_function_list_is_every_public_function_in_the_module` |

The runner is `mutate.py` in this session's scratchpad; it restores the file in a
`finally` block, and the working tree was verified clean afterwards. At **iteration 3**,
after the nine runs, `git status --short scripts/` lists **only**
`scripts/test_orca_runtime_contract.py` — the single file this round changed — and
`git diff --stat -- scripts/` reports `1 file changed, 76 insertions(+)`, so
`decision_gate.py`, `decision_policy.py` and `e2e_harness.py` all returned byte-clean.
(At iteration 2 the same check listed `scripts/e2e_harness.py` and
`scripts/test_e2e_harness.py`, the two files that iteration deliberately changed.)

**M-T001 is the load-bearing one.** It is the reason the removal of the
`@unittest.expectedFailure` marker is a strengthening rather than a concealment: with the
one-line fix reverted, the very assertion whose marker was removed fails again, together
with the newly-HIGH matrix row. A test that had been silently defanged would have stayed
green under this mutant.

The already-present **M-DUP** mutation (NV-3) continues to pass unchanged:
`DecisionGateNonDuplicationTests.test_m_dup_fails_the_invariants_while_the_control_passes`.

---

## Failures / Findings

**Iteration 3: no new finding.** Nothing in the corrected tree failed, and nothing in
this phase's deliverable was found defective. Two TEST-phase **coverage** gaps were found
and closed in the same round (live P6b row 5; the narrowed risk-independence signature
list) — those are gaps in this artifact's own claim, not defects in anybody's production
code, and they are recorded under *"Iteration 3 — downstream revalidation"* rather than
here. No production behaviour was found to differ from PLAN, DESIGN or the approved
IMPLEMENTATION.

### T-001 / F-001 — a mid-work block was not a decision terminal at MEDIUM/HIGH — **RESOLVED at iteration 2**

* **Severity:** MAJOR. **Blocking:** YES — the phase Reviewer ruled it blocking as `F-001` in `REVIEW_TEST.md`.
* **Status:** **RESOLVED.** Reported at iteration 1, fixed at iteration 2 under the Reviewer's Required Action. The description below is retained as the record of the defect; the fix is in the labelled section that follows.
* **Quality attribute:** none (`profile_status: absent`). Raised under the general gate
  **G1 — explicit requirement violation**, not as design taste.
* **Responsible phase:** IMPLEMENTATION.
* **Where:** `scripts/e2e_harness.py`, the `if worker_status == self.contract.worker_blocked:`
  branch immediately after the OS-29 B2 block.

**The requirement.** PLAN P6b **O-2**: the B2 guard sits above the `STATUS: BLOCKED`
branch precisely so that "a Worker that discovers a blocking decision mid-work and
reports it must be accounted on the decision axis …, not swallowed as a generic
`WORKER_BLOCKED`". PLAN P6b row 2 + row 4 require the LOW and MEDIUM/HIGH terminals to be
identical in `final_status`, `decision_state` and `reason_code`. PLAN P4 scenario 5
requires `implementation_midwork_block` "asserted at HIGH through P6b row 2 → row 4, and
at LOW through row 2's B2 terminal", with "`reason` must be `DECISION_BLOCKED:…`, **not**
`WORKER_BLOCKED`".

**The behaviour, as measured at iteration 1.** `verification_only = True` was set
correctly, and the next branch then returned `WORKER_BLOCKED` before the already-scheduled
Reviewer was reached:

| risk | `final_status` | `reason` | `decision_state` | `decision_reason_code` | reviewer attempts |
| --- | --- | --- | --- | --- | --- |
| low | BLOCKED | `DECISION_BLOCKED:CONFLICT:requirement_contradiction` | `CONFLICT` | `requirement_contradiction` | 0 |
| medium | BLOCKED | `WORKER_BLOCKED` | `""` | `""` | 0 |
| high | BLOCKED | `WORKER_BLOCKED` | `""` | `""` | 0 |

**Why it matters, precisely.**

1. **The cross-risk equality P6b guarantees does not hold for this shape.** LOW and
   MEDIUM/HIGH produce different `decision_state` and `reason_code` for the same Worker
   output. "Risk selects where the terminal is recorded, never what it says" is true for
   the `STATUS: COMPLETE` shape and false for the `STATUS: BLOCKED` shape.
2. **Provenance is lost on two of three risk levels.** ORIGINAL_REQUEST requires decision
   state and reason code to be recorded machine-readably as the run's evidence, and the
   run-level columns are empty here. The *ledger record* was still written correctly at
   every risk level, so this was a defect of the **transition**, not of the record — which
   is exactly what bounded the fix to one line.
3. **No verification Reviewer runs**, so P6b row 2's MEDIUM/HIGH half is not exercised
   for this shape.

**What is NOT affected** (measured, so the finding is not overstated): no correction
iteration is charged in either case — `gate_attempts()` returns 0 because no Reviewer
attempt exists on this path — and the outcome is still a terminal `BLOCKED`, so nothing
fails **open**. The phase does not complete and no next phase is dispatched. This is a
loss of decision-axis accounting and provenance, not a fail-open.

**Why the existing suite did not see it.** `test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked`
exercises scenario 5 at `"low"` only. P4 asked for both.

**Not fixed at iteration 1.** The TEST template's Mandatory Invariants forbid repairing a
production defect during *initial* TEST authoring, so it was pinned by
`DecisionGateFindingT001Tests.test_t001_...` and marked `@unittest.expectedFailure` rather
than skipped: a skip stops reporting, whereas an unexpected success is itself a failure,
so the marker had to be deleted deliberately once the defect was fixed. That is precisely
what happened at iteration 2.

**Correctness vs. environment.** T-001 was a correctness failure, reproduced
deterministically at every attempt. No flaky or environment-dependent failure was observed
in either iteration; the full suite passed on every run.

---

## Production change made under a TEST-phase blocking finding

> Recorded here, and **not** in `IMPLEMENTATION.md`, which is approved and was not edited.
> This section exists so the Final Adversarial Review can see the change on the
> cross-phase axis: an **IMPLEMENTATION-owned transition was corrected under a TEST-phase
> blocking finding**.

**Why the TEST-phase Worker made an IMPLEMENTATION-owned fix.** `SKILL.md` §12 routes a
phase-gate FAIL to *that phase's own* correction Worker; the responsible-phase ladder is
§17 Final-Review machinery, not phase-gate machinery, so there is no phase-gate mechanism
to reopen the closed IMPLEMENTATION phase. `PREVIOUS_PHASE_CHANGE_REQUIRED` was
deliberately **not** reported: it would deadlock the run with the defect unfixed, and no
approved *conclusion* is being changed — the code is being made to **match** approved PLAN
P6b, which it violated.

**The delta — one line of production code:**

```diff
--- a/scripts/e2e_harness.py     (run(), immediately after the OS-29 B2 block)
+++ b/scripts/e2e_harness.py
-            if worker_status == self.contract.worker_blocked:
+            if worker_status == self.contract.worker_blocked and not verification_only:
```

plus the comment that states the rule, so the next reader does not re-flatten it.

**Why this is the minimal correct fix.** B2 already sits above this branch and already
computes the right answer; the defect was that its **result was not carried across** the
branch. The fix carries it. It adds no dispatch site, no round, no new state, and no new
terminal: the MEDIUM/HIGH round falls through to the **already-scheduled** Reviewer at the
existing dispatch site in verification mode, which is what PLAN P6b row 2 → row 4 specifies
and what the code's own comment already claimed to do.

**What the fix deliberately does not do.** `verification_only` is set **only** when the
Worker's B2 gate result is in `decision_gate.BLOCKING_STATES`. A Worker-declared
`STATUS: BLOCKED` with **no** decision block therefore never sets it and still terminates
as a plain `WORKER_BLOCKED` — that distinction is existing correct behaviour, it is the
quality axis rather than the decision axis, and it now has its own explicit control at all
three risk levels (`test_the_worker_blocked_terminal_still_exists`, plus the printed
control table above). No existing guard was removed, relaxed or reordered; no refactor was
performed.

**Test changes that accompany it** (all in `scripts/test_e2e_harness.py`):

| Change | Kind | Why |
| --- | --- | --- |
| `@unittest.expectedFailure` removed from `test_t001_...` | **un**-weakening | the requirement assertion passes normally; leaving the marker would now make an unexpected success a failure |
| `test_the_low_terminal_is_correct_today` → `test_the_ledger_records_the_route_each_risk_level_actually_took` | rename + strengthen | the old name described the defect era; the body gained the Reviewer-attempt count and the B3→B2 binding assertion |
| `test_the_worker_blocked_terminal_still_exists` | **added** | the narrowness control for the fix, at LOW/MEDIUM/HIGH |
| `implementation_midwork_block` gained `reviewer_modes=("pass",)` / `reviewer_decision_states=("CONFLICT",)` | fixture | P4 requires scenario 5 "at HIGH through P6b row 2 → row 4"; at HIGH the route needs the Reviewer the fixture previously omitted |
| matrix row 5+ risk `"low"` → `"high"` | matrix | the row no longer needs the exception the defect forced on it |
| mutant **M-T001** | added | proves the removed marker was removed for the right reason |

**Files changed by this iteration:** `scripts/e2e_harness.py` (+12 / −1, of which one line
is the fix and the rest is its comment) and `scripts/test_e2e_harness.py` (+88 / −36).
Nothing else in `scripts/` was touched; `scripts/run_logging.py` remains byte-identical to
`orca-worker-reviewer-orchestration/tools/run_logging.py`.

---

## Iteration 3 — downstream revalidation against the corrected upstream

> **There is no finding to resolve in this section.** No TEST-phase deliverable was
> judged defective at any point in iteration 3; `REVIEW_TEST_iteration2.md` PASSed this
> phase. The round exists because `FINAL_REVIEW.md` F-001 was charged to
> **IMPLEMENTATION**, IMPLEMENTATION iteration 4 corrected it at `1d1942c`, and this
> phase's PASS had been granted against the tree that correction replaced.

### What the upstream correction actually changed, restated in TEST-phase terms

| Change at `1d1942c` | Does it touch a TEST-phase claim? |
| --- | --- |
| `decision_gate.VerificationDispatch` + `verification_admission_defect()` (six conjuncts) | **Yes, twice.** It is a new public gate function, so scenario 7's `inspect.signature` closed list had to grow (**M-SIG-LIST**); and its behaviour is IMPLEMENTATION's to test, which it did |
| `admit_head(..., verification=None)` — A5 exception, default unchanged | **No.** Every deterministic caller and every pre-existing test passes no `verification`, so A1–A6 behave exactly as the F9–F14 table and the A1–A6 prototype describe. NV-1's `lambda *args, **kwargs` double absorbs the new keyword |
| The same exception honoured at **A6** | **No.** F10's two shapes are about a *declaration that disagrees*; the new clause fires only when the disagreement **is** the head and the declaration **understates** it, which neither shape is |
| `open_items()` — a `verifies`-bearing record resolves nothing | **No, and it corroborates.** This is the structural form of the terminality TEST already asserted at P6b rows 4–6, now also enforced on the live path |
| Live `_b1_guard()` / `_record_decision_from_attempt()` B3-V | **Yes.** It creates a live half for P6b rows 2, 4, 5, 6, 7. Rows 2/4/6/7 got live cases in the same commit; **row 5 did not** (**M-ROW5-LIVE**) |
| `e2e_harness.py`, `run_logging.py`, `decision_policy.py`, `validate_skills.py`, the Skills, the templates | **Untouched by `1d1942c`.** So the deterministic matrix, the ledger tests, the P7 drift tests and the scenario-14 validator tests revalidate byte-for-byte |

### The four questions this round had to answer, and their answers

1. **Does the scenario-to-fixture mapping still hold, and is any statement that the live
   path refuses *all* dispatches after a blocking decision now stale?**
   The mapping holds — every fixture name in the fourteen-scenario table still names a
   real callable or test, re-verified by running `DecisionGateScenarioMatrixTests`, which
   fails on a name that stops naming a case. **One statement was stale and is corrected:
   Remaining Gap 5.** No row of the scenario table asserted "all dispatches are refused";
   scenario 12's rows each name a *specific* illegal dispatch, which is why the
   correction did not falsify any of them — see the adjacency note under the table.
2. **Does each of the fourteen scenarios still have a named POSITIVE and NEGATIVE, and
   is scenario 12's negative still asserting the right thing?**
   Yes to both. Scenario 12's four deterministic sites dispatch the next phase, the Final
   Review, a correction Worker and a downstream revalidation; its live row dispatches a
   correction Worker. None is a current-phase Reviewer and none offers a `verifies`
   binding, so **the permitted case is not covered as illegal anywhere.** It is covered
   as *permitted*, in IMPLEMENTATION's eight-negatives function, whose co-located control
   admits it.
3. **Are P6b's rows still fully covered now that the live path implements row 2 / B3-V?**
   Yes, after this round. The live half of every B2 and B3 cell is enumerated above; row
   5 was the only one without a case and now has one.
4. **Do the NV pairs still hold, and is anything under-covered?**
   All three NV proofs hold, for reasons argued above rather than by re-running alone.
   Two things were under-covered — live row 5 and the narrowed signature list — and both
   are fixed, each with a mutant.

### What this round did NOT do

No production code changed: the whole iteration-3 diff is
`scripts/test_orca_runtime_contract.py` **+76 / −0** and `scripts/test_decision_gate.py`
**+34 / −0** (`2 files changed, 110 insertions(+)`). No test was deleted, skipped,
renamed or weakened; `grep -c expectedFailure scripts/*.py` finds **no**
`@unittest.expectedFailure` decorator anywhere in the suite — the single remaining
occurrence of the word is the comment at `test_e2e_harness.py:7259` recording that the
marker was removed at iteration 2. No approved artifact (`ANALYSIS.md`, `PLAN.md`,
`DESIGN.md`, `IMPLEMENTATION.md`) was edited, and no `REVIEW_*.md` or `FINAL_REVIEW*.md`
was written. IMPLEMENTATION's live tests were **not** duplicated: this round added one
row to their table and left the other twenty-three tests untouched.

---

## Remaining Gaps

1. ~~**T-001 is unrepaired by design.**~~ **Closed at iteration 2.** P6b row 2's
   MEDIUM/HIGH half is now exercised for the `STATUS: BLOCKED` Worker shape, at HIGH
   through the matrix and at all three risk levels through
   `DecisionGateFindingT001Tests`. No gap remains here.
2. **A6 is unreachable at a run's first B1**, by the fixed A1→A2→A4→A3→A6→A5 order — A3
   refuses first. This is correct behaviour, not a gap, but it means F10's coverage of A6
   is necessarily at a later boundary; both shapes are asserted and named above.
3. **An accepted downgrade is only reachable by construction.** `explicit_user_reply` is
   asserted as the admitted-source control, but no in-run channel can supply a conforming
   `user_decision` while OS-30 is absent (L1/L6). The tests therefore prove the contract
   accepts it and that the round is **still terminal**; they cannot prove a live
   end-to-end resume, which is OS-31 and out of scope.
4. **Cross-session durability is out of scope** (OS-31). `test_a_pre_seeded_ledger_is_unbound_at_the_runs_first_boundary`
   and F10 shape 2 pin the fail-closed behaviour in its absence.
5. ~~**The live (`orca_runtime_harness`) path is covered at B1 only.** Its B2/B3 halves
   remain covered by the deterministic harness, as PLAN P2 scoped them.~~
   **STALE — corrected at iteration 3.** This sentence was true of the tree it was
   written against (PLAN P2 row C4 scoped the live harness to a pre-`start_worker` B1
   guard), and the Final Adversarial Review then ruled that scoping a G1 requirement
   violation: `FINAL_REVIEW.md` F-001 required the live path to implement the permitted
   current-phase verification Reviewer, and IMPLEMENTATION iteration 4 (`1d1942c`) did.
   **The live path now covers B1, B2 and B3-V.** Specifically: `_b1_guard()` describes
   the dispatch as a `decision_gate.VerificationDispatch` and the six-conjunct
   `verification_admission_defect()` decides it; `_record_decision_from_attempt()`
   publishes the B3 record, requires the `verifies` binding through the **shared**
   `verification_binding_defect()`, and computes the terminal through the **shared**
   `evaluate_verification()`. The live half of every P6b cell is enumerated in
   *"Iteration 3 — the live half of P6b, now that it exists"* above; row 5 was the one
   with no case, and it now has one. What is still true, and is the residual form of
   this gap: **no decision-axis code on the live path branches on risk.** The live
   harness reads `self.risk` only to log it and, at `orca_runtime_harness.py:1685`, to
   decide whether a Reviewer *task node* is created at all — the LOW half of P6b row 2
   arrives there as "the Coordinator never dispatches a Reviewer", not as a second
   terminal. `_b1_guard()`, `verification_admission_defect()`,
   `_record_decision_from_attempt()` and `evaluate_verification()` contain no risk
   branch, so the cross-risk **equality** of P6b rows 2 and 4 is still asserted where the
   risk routing actually lives — `E2EHarness.run()` — and is structurally guaranteed on
   the live side by `RiskIndependenceTests`. That is a property of where risk routing
   lives, not a coverage hole.

---

## Decision Record (optional)

```text
DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE:
  open_decision_item: false
  grounds: Every ambiguity met in this phase was settled by an explicit requirement in
    ORIGINAL_REQUEST.md, by the approved PLAN (P4, P6a, P6b, P7, P10), by FINAL_REVIEW.md
    F-001 and REVIEW_IMPLEMENTATION_iteration4.md, or by the current code. Iteration 3 is
    a downstream revalidation with no finding to resolve: the upstream correction was
    read, each TEST-phase claim was re-argued against it rather than re-run only, the one
    stale statement (Remaining Gap 5) was corrected, and the two coverage cells the
    correction left open were filled with a mutant each. No approved conclusion was
    changed and no production code was touched. No requirement is knowingly left unmet,
    no assumption was made in order to proceed, and no user authority was claimed or
    needed.
  scope: This phase's own conduct at this iteration.
```

No user decision is required by this phase. `CLEAR` carries no reason code (OS-28), and
this record validates under `decision_policy.validate_record()` from both Skills. The
phase is not classified `CLEAR` over an unmet requirement: the requirement F-001 named is
now met and demonstrated, which is what distinguishes iteration 2 from iteration 1, and
iteration 3 adds no unmet requirement of its own — it removes a stale claim and widens
two coverage cells, both demonstrated above.
