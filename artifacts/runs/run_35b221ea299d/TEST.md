# TEST — Jira OS-29 "Add Continuous Decision and Escalation Gates to Every Phase"

Run: `run_35b221ea299d`  Phase: TEST  Iteration: 1  Risk: HIGH (explicit)
Branch: `os-29-continuous-decision-gates`  Base of this phase: `f072b4f`
Template: `orca-worker-reviewer-orchestration/templates/test.md`
Approved inputs read: `ORIGINAL_REQUEST.md`, `PLAN.md` (P4, P6a, P6b, P7, P10), `DESIGN.md`,
`IMPLEMENTATION.md`, `REVIEW_IMPLEMENTATION_iteration3.md` (PASS).

---

## Test Scope / Existing Test Assessment

### What IMPLEMENTATION already left behind

The suite at `f072b4f` (1,582 tests) already carried a substantial OS-29 body: the gate
parser/evaluator and A1–A6 in `scripts/test_decision_gate.py`, the transition matrix and
the M-DUP non-duplication mutation in `scripts/test_e2e_harness.py`, the ledger producer
in `scripts/test_run_logging.py`, the three drift directions in
`scripts/test_validate_skills.py`, and the live pre-`start_worker` half in
`scripts/test_orca_runtime_contract.py`. That work is **not** re-done here.

### The three material gaps this phase found

1. **Named fixtures did not exist.** P10's first TEST exit criterion asks for a *named*
   positive and a *named* negative per scenario, mapped to the P4 module. The cases
   existed; the **names** did not, so the mapping was an interpretation of test bodies
   rather than a lookup. A fixture name that stops naming a real case was undetectable.
2. **Six P4/P6a/P6b cells had no case at all** — scenario 2 end-to-end, scenario 11,
   B1 sites 2/3/4 for scenario 12, P6b row 5 through the real subprocess, the reachable
   A6 case (F10), and the exact iteration-1 Markdown-vs-machine defect as a fixture.
3. **A production defect the existing tests could not see** — finding **T-001** below.
   `test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked` asserts scenario 5
   at **LOW only**, while P4 requires it "at HIGH through P6b row 2 → row 4, **and** at
   LOW". Running the same fixture at HIGH is what exposed it. This is the third time in
   this run that a green suite coexisted with a fail-open-shaped defect, and it is the
   reason the whole matrix below is executed rather than described.

### What was deliberately NOT done

No production code was repaired. T-001 is reported as a finding, per the TEST template's
Mandatory Invariants. No existing test, fixture or validator was deleted, renamed,
skipped or weakened: the diff is 1,348 insertions and **0 deletions**.

---

## Added / Modified Tests

| File | Added | Nature |
| --- | --- | --- |
| `scripts/test_e2e_harness.py` | +964 lines, 9 tests in 3 new classes | the named fixtures, the executable scenario matrix, the six missing transition cells, T-001 |
| `scripts/test_decision_gate.py` | +236 lines, 5 tests in 3 new classes | scenario 11 at the contract level, scenario 8's drift rule, the exact F-001 string |
| `scripts/test_run_logging.py` | +119 lines, 3 tests in 1 new class | scenario 2's and F10's producer halves, append-only |
| `scripts/fake_worker.py` | +9 lines | one additive CLI seam (below) |

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
| 5 | high-impact decision found during IMPLEMENTATION → blocked outcome | `implementation_midwork_block` → `DecisionGateTransitionTests.test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked` (LOW) + `DecisionGateFindingT001Tests.test_the_low_terminal_is_correct_today`; **MEDIUM/HIGH is finding T-001**, pinned by `DecisionGateFindingT001Tests.test_t001_a_midwork_block_must_be_a_decision_terminal_at_every_risk_level` | `implementation_same_item_declared_clear` → matrix row 5−; the `WORKER_BLOCKED` control → `test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked` | `test_e2e_harness.py` |
| 6 | Worker auto-approves without authority → Reviewer FAILs it as blocking | `worker_unauthorized_high_impact` → `DecisionGateTransitionTests.test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing` + matrix row 6+ | `worker_high_impact_with_explicit_authorization` → matrix row 6−; the axis-separation control (row 9, iteration **charged**) → same transition test | `test_e2e_harness.py` (+ `test_decision_gate.py` for INV-4) |
| 7 | LOW risk → decision authority NOT expanded | `same_record_at_low_medium_high` → `RiskIndependenceTests.test_no_gate_function_takes_a_risk_or_profile_parameter` (**structural**, `inspect.signature`) + `test_the_module_source_never_branches_on_a_risk_level`; **behavioural** → `test_needs_input_blocks_identically_at_every_risk_level` | co-located guard: the three runs must differ elsewhere (`reviewer_attempts` 0 at LOW, 1 at HIGH), asserted in the same function | `test_decision_gate.py` + `test_e2e_harness.py` |
| 8 | downstream expands a user decision → new decision or escalation | `downstream_expands_decision` → `DecisionGateTransitionTests.test_a_downstream_expansion_is_a_new_decision_event_not_a_lineage_link` + matrix row 8+; drift rule → `DownstreamExpansionTests.test_downstream_expands_decision_opens_a_new_item_and_links_nothing` | `downstream_within_original_decision` → same tests | `test_e2e_harness.py` + `test_decision_gate.py` |
| 9 | Final Review with an unresolved decision → completion forbidden | `final_review_unresolved_decision` → `DecisionGateTransitionTests.test_an_unresolved_decision_forbids_final_review_completion` and `test_a_final_review_quality_pass_cannot_complete_over_a_blocking_decision` + matrix row 9+ | `final_review_all_decisions_resolved` → matrix row 9−; defect half → `test_a_defective_final_review_decision_result_fails_closed` | `test_e2e_harness.py` |
| 10 | Worker and Reviewer agree on the same unauthorized assumption → no approval | `worker_reviewer_agree_unauthorized` → matrix row 10+ (`DECISION_DOWNGRADE_REJECTED`); ledger half → `AdmissibilityTests.test_a_worker_reviewer_agreement_never_resolves_an_open_item` | the closed-set precheck (`len(forbidden_authority_sources) == 5`, `worker_reviewer_agreement` ∈ it) co-located in both tests | `test_decision_gate.py` + `test_e2e_harness.py` |
| 11 | timeout / non-response → no approval, no iteration | `timeout_no_response` → `TimeoutAndNonResponseTests.test_timeout_no_response_is_never_an_approval` and `test_a_timeout_never_resolves_an_open_ledger_item`; end-to-end → `DecisionGateNamedScenarioTests.test_scenario_11_a_timeout_or_non_response_approves_nothing_and_charges_nothing` | `quality_fail_consumes_iteration` (shared control) → same e2e function, plus the admitted-source control (`explicit_user_reply` takes a different path) | `test_decision_gate.py` + `test_e2e_harness.py` |
| 12 | illegal dispatch after a blocking decision → fail closed | `illegal_dispatch_after_block` → site 1 `DecisionGateTransitionTests.test_an_open_ledger_item_blocks_the_next_phase_dispatch`; sites 2/3/4 `DecisionGateNamedScenarioTests.test_scenario_12_an_illegal_dispatch_is_refused_at_every_remaining_b1_site`; live path `DecisionGateLiveDispatchTests.test_an_unresolved_open_item_refuses_the_next_dispatch` | `guard_removed_mutant` → `test_nv1_removing_the_guard_lets_the_same_scenario_dispatch` and mutants **M-B1-FR / M-B1-CORR / M-B1-REVAL** below; F11 `red_offered_at_later_boundary` → `test_the_declaration_cannot_stand_in_for_a_deleted_settled_record` | `test_e2e_harness.py` + `test_orca_runtime_contract.py` |
| 13 | missing or malformed decision result → no `CLEAR` presumption | the exact RED admitted through the complete A1–A6 path → `AdmissibilityTests.test_the_run_entry_declaration_is_admissible_at_exactly_one_position` | F1–F8 → `GateResultParsingTests.test_a_valid_declaration_is_admitted_and_every_defect_is_refused` and end-to-end `test_a_silent_or_broken_agent_never_presumes_clear`; F9–F14 → the F-table below; mutant **M-PRESUME-CLEAR** | `test_decision_gate.py`, `test_e2e_harness.py`, `test_run_logging.py` |
| 14 | decision-semantics drift between the two Skills → validation failure | the shipped pair validates → `ValidatorRegressionTests.test_valid_repository_passes` (697 checks) | (a) one Skill only → `test_os29_mirrored_semantics_changed_in_one_skill_fails`; (b) deleted from **both** → `test_os29_mirrored_semantics_deleted_from_both_skills_fails`; (c) orchestration-only block copied into the loop Skill → `test_the_orchestration_only_gate_block_leaking_into_the_loop_fails`; plus `test_decision_gate_contract_removed_fails` / `test_decision_gate_contract_value_drift_fails` | `test_validate_skills.py` |

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
| **B3 row 5** — verification **stricter** ⇒ the Reviewer's own state | `NEEDS_INPUT` → `CONFLICT` through the real subprocess, with the confirming and the CLEAR controls co-located | **new** `test_p6b_row_5_a_stricter_verification_carries_the_reviewers_own_state`; contract half `VerificationTests.test_a_stricter_verification_carries_the_reviewers_own_state` |
| **B3 row 6** — proposed **downgrade** ⇒ decided only by `validate_transition()` | both targets rejected; `timeout`/`no_response`/`worker_reviewer_agreement` rejected; `explicit_user_reply` accepted and **still terminal** | `test_a_reviewer_downgrade_is_rejected_and_still_terminal`, `TimeoutAndNonResponseTests`, matrix rows 10+/11+ |
| **B3 row 7** — verification record **not bound** | forged `verifies` ⇒ `DECISION_GATE_INPUT_UNBOUND`, with the bound control | `test_an_unbound_verification_record_fails_closed`, `VerificationTests.test_an_unbound_verification_record_is_its_own_defect` |
| **B3 row 8** — Reviewer discovers the block in **normal** mode | finding recorded in `finding_traces` **and** no iteration charged; Final-Review edge too | `test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing`, `test_a_final_review_quality_pass_cannot_complete_over_a_blocking_decision` |
| **B3 row 9** — valid `CLEAR`/`ASSUMPTION_ALLOWED`: **existing** routing untouched | PASS → next phase; FAIL → correction with the iteration **charged** | co-located axis-separation control in `test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing`; `test_a_fully_clear_run_adds_artifacts_without_changing_a_transition` |
| **B3 row 10** — missing/malformed Reviewer result | B3 and the Final Review edge, seven defect shapes | `test_a_silent_or_broken_agent_never_presumes_clear`, `test_a_defective_final_review_decision_result_fails_closed` |

**Cross-risk equality (rows 2 and 4)** is asserted on all three of `final_status`,
`decision_state` and `reason_code` in
`test_needs_input_blocks_identically_at_every_risk_level`, with the co-located guard that
the three runs genuinely differ elsewhere (`len(reviewer_attempts)` is 0 at LOW and 1 at
HIGH). The same equality **fails** for the `STATUS: BLOCKED` shape — that is T-001.

### The three non-vacuity proofs

| Proof | Construction | Co-located control, in the SAME function |
| --- | --- | --- |
| **NV-1** dispatch blocking | `test_nv1_removing_the_guard_lets_the_same_scenario_dispatch` patches `decision_gate.admit_head` to always admit; the previously-blocked run then `COMPLETED`s with non-empty `sessions` | the unpatched run in the same function blocks with `sessions == ()`; `red_present_first_phase_dispatches` inside `test_the_first_phase_needs_the_run_entry_declaration`; and mutants M-B1-FR/CORR/REVAL for the three sites the phase gate cannot reach |
| **NV-2** iteration non-consumption | `test_a_decision_block_charges_no_iteration_and_a_quality_fail_still_does`; also asserted for scenario 11 in `test_scenario_11_...` | the quality-`FAIL` round in the same function increments `phase_iterations` to 2 |
| **NV-3** non-duplication (M-DUP) | `DecisionGateNonDuplicationTests.test_m_dup_fails_the_invariants_while_the_control_passes` — (1) the mutation is not a no-op (`assertGreater` on session and reviewer counts), (2) the `ROUND_KIND_VALUES` proxy **still passes** on the mutant, (3) INV-D1/INV-D3 reject it | the unmutated blocked run and a CLEAR run both return **clean** from the same invariant checker |

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

```text
Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  PASS
         Ran 1599 tests in 314.222s
         OK (skipped=6, expected failures=1)
```

Baseline at `f072b4f` was **1,582** tests (re-measured this phase before any edit:
`Ran 1582 tests in 309.584s / OK (skipped=6)`). **+17, −0.** The one expected failure is
T-001, deliberate and documented below.

### The other four CI gates (`.github/workflows/*.yml:33-48`)

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
Result:  PASS -- Ran 1 test in 1.833s / OK  (19 fixture rows, 10 scenarios)

Command: python3 -m unittest scripts.test_e2e_harness.DecisionGateFindingT001Tests
Result:  PASS -- Ran 2 tests in 0.235s / OK (expected failures=1)
```

### Mutation / control pairs

Seven single-point mutations, each applied to the working tree, the named test run,
the mutation reverted, and the same test re-run. **Every mutant was killed and every
control returned green.** Verbatim output:

```text
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

ALL MUTANTS KILLED, ALL CONTROLS GREEN
```

| Mutant | Edit | Killed by |
| --- | --- | --- |
| **M-A6** | `decision_gate.py`: `if declared != recomputed:` → `if False:` | `test_f10_the_run_entry_declaration_is_recomputed_and_never_rubber_stamped` |
| **M-AUTH** | `decision_policy.py`: `if source not in policy.user_decision_sources:` → `if False:` | `test_scenario_11_...` + `TimeoutAndNonResponseTests` (8 failures) |
| **M-FACTS** | `decision_policy.py`: `_undeclared_safety_facts` → `return ()` | `test_scenario_2_...` (6 failures, one per fact) |
| **M-B1-FR** | `e2e_harness.py`: `refusal = b1("final_review")` → `refusal = None` | `test_scenario_12_an_illegal_dispatch_is_refused_at_every_remaining_b1_site` |
| **M-B1-CORR** | `e2e_harness.py`: `refusal = b1("correction")` → `refusal = None` | same |
| **M-B1-REVAL** | `e2e_harness.py`: `refusal = b1("downstream_revalidation")` → `refusal = None` | same |
| **M-PRESUME-CLEAR** | `decision_gate.parse_gate_result` returns an unconditional `CLEAR` — the literal fail-open the ticket forbids | `test_scenario_1_clear_pass_proceeds_and_an_absent_record_does_not` |

The runner is `mutate.py` in this session's scratchpad; it restores the file in a
`finally` block and the working tree was verified clean afterwards
(`git diff --stat` shows insertions in four files and no change to `decision_gate.py`,
`decision_policy.py` or `e2e_harness.py`).

The already-present **M-DUP** mutation (NV-3) continues to pass unchanged:
`DecisionGateNonDuplicationTests.test_m_dup_fails_the_invariants_while_the_control_passes`.

---

## Failures / Findings

### T-001 — a mid-work block is not a decision terminal at MEDIUM/HIGH

* **Severity:** MAJOR. **Blocking:** the phase Reviewer's call (see below).
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

**The behaviour.** `verification_only = True` is set correctly, and the next branch then
returns `WORKER_BLOCKED` before the already-scheduled Reviewer is reached. Measured:

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
   run-level columns are empty here. The *ledger record* is still written correctly at
   every risk level (asserted in `test_the_low_terminal_is_correct_today`), so this is a
   defect of the **transition**, not of the record — which also bounds the fix.
3. **No verification Reviewer runs**, so P6b row 2's MEDIUM/HIGH half is not exercised
   for this shape.

**What is NOT affected** (measured, so the finding is not overstated): no correction
iteration is charged in either case — `gate_attempts()` returns 0 because no Reviewer
attempt exists on this path — and the outcome is still a terminal `BLOCKED`, so nothing
fails **open**. The phase does not complete and no next phase is dispatched. This is a
loss of decision-axis accounting and provenance, not a fail-open.

**Why the existing suite did not see it.** `test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked`
exercises scenario 5 at `"low"` only. P4 asked for both.

**Not fixed here.** The TEST template forbids repairing a production defect found in this
phase. It is pinned by `DecisionGateFindingT001Tests.test_t001_...`, marked
`@unittest.expectedFailure` rather than skipped: a skip stops reporting, whereas an
unexpected success is itself a failure, so the marker must be deleted deliberately when
the defect is fixed. `test_the_low_terminal_is_correct_today` asserts the half that
holds, so the finding is scoped rather than read as "scenario 5 is broken".

**Correctness vs. environment.** T-001 is a correctness failure, reproduced
deterministically at every attempt. No flaky or environment-dependent failure was
observed in this phase; the full suite passed on every run.

---

## Remaining Gaps

1. **T-001 is unrepaired by design.** Until IMPLEMENTATION addresses it, P6b row 2's
   MEDIUM/HIGH half is unexercised for the `STATUS: BLOCKED` Worker shape.
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
5. **The live (`orca_runtime_harness`) path is covered at B1 only.** Its B2/B3 halves
   remain covered by the deterministic harness, as PLAN P2 scoped them.

---

## Decision Record (optional)

```text
DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE:
  open_decision_item: false
  grounds: Every ambiguity met in this phase was settled by an explicit requirement in
    ORIGINAL_REQUEST.md, by the approved PLAN (P4, P6a, P6b, P7, P10) or by the current
    code. T-001 is a defect in already-approved scope, reported as a finding for the
    phase Reviewer to adjudicate; it is not a request for user authority, and no
    assumption was made in order to proceed past it.
  scope: This phase's own conduct at this iteration.
```

No user decision is required by this phase. `CLEAR` carries no reason code (OS-28), and
this record validates under `decision_policy.validate_record()` from both Skills.
