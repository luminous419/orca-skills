RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES

## Summary

The implementation matches the approved DESIGN and user decisions closely enough to proceed. The decision-policy contract loads fail-closed, all 18 reason codes have constructible records, the six data-driven tests fail when their guarded collections are emptied, and the required repository validation commands pass above the approved baseline. No blocking defect, hidden OS-29/30/31 runtime wiring, or protected-file scope change was found.

## Blocking Findings

None.

## Non-Blocking Findings

### RI-N1

- ID: RI-N1
- Quality Attribute: NONE
- Severity: LOW
- Blocking: NO
- Location: `scripts/test_decision_policy.py:377-388`
- Issue: The UD-3 regression test checks source-text token absence rather than executing `evaluate_invocation()` and comparing its behavior.
- Reason: This is weaker than the test name suggests, but it does not invalidate UD-3 in this change: `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, and the implementation neither calls nor modifies `evaluate_invocation()`. The approved DESIGN also scopes requirement 9 to the new loader.
- Required Action: Optional follow-up: rename the test to describe its source-scope assertion, or add a behavioral characterization test in the ticket that owns the pre-existing loader defect.

## Test Review

- Ran `python3 scripts/validate_skills.py`: PASS, 604 checks (baseline lower bound 501).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, 1326 tests in 296.952s, skipped=6 (baseline lower bound 1269). The skips were the existing reported six; no new skip-based concealment was observed.
- Ran `python3 scripts/verify_package.py`: PASS, 165 source files.
- Ran `python3 scripts/build_release.py`: PASS, produced `dist/orca-skills-0.9.0.tar.gz`.
- Ran archive verification with the current `VERSION`: PASS, 165 source files.
- Ran `git diff --check`: PASS with no output.
- Fail-closed execution: independently passed unknown version, missing version, malformed object, and non-object inputs to `parse_decision_policy`; all four raised `DecisionPolicyError`. A valid `load_decision_policy()` call returned a policy, never `None`.
- Anti-vacuity execution: independently emptied (1) forbidden transitions, (2) non-CLEAR reason-required states, (3) required-evidence collections, (4) high-impact elements, (5) forbidden authority sources, and (6) reason codes. Each corresponding D4-F test method failed from its co-located cardinality/equality guard before a vacuous loop could pass.
- Liveness: `test_every_reason_code_has_a_constructible_record` guards cardinality at 18 and performs C1 entry, C2 effective evidence, and C3 invariant validation for every code; the full suite passed. The fixture directory is also compared bidirectionally with the closed code set.
- Mutation reproduction: directly ran M-3 (delete one reason code from both Skills), M-17 (transition value outside the closed set), and shared-template single-copy drift. All three disposable-copy regression tests passed by observing the expected validator failure.
- UD checks: UD-1 remains explicitly optional in all fourteen shared phase templates and both `reviews/common.md`; UD-2's permission-only limitation is in the test docstring and implementation report; UD-3 leaves `scripts/skill_policy.py` unchanged; UD-4 has exactly 18 codes and zero occurrences of `requirement_vs_repository_policy` in implementation scope.
- Scope and provenance: `git diff c264e79 HEAD` contains the OS-28 contract, loader, fixtures, tests, validator, shared prose/templates, and changelog only. `VERSION`, `LICENSE`, `.orca`, existing Risk/Quality/Agent Profile implementations, and `scripts/skill_policy.py` are unchanged. Commit `9862f85` contains both the validator import and the `decision_policy.py` disposable-copy tuple update.

## Final Decision

PASS WITH NOTES. The implementation satisfies the DESIGN, required tests have demonstrated non-vacuous failure behavior, all required validation commands pass, and the only note is a non-blocking precision issue in the UD-3 regression test's naming/strength.

# Iteration 2 — Final Review FR-2 Correction

RESULT: PASS
REVIEW_VERDICT: PASS

## Summary

FR-2 is RESOLVED. User-decision authority is now enforced by membership in a closed positive vocabulary, every unknown or aliased source fails closed, and both genuine evidence forms remain usable. The correction is narrowly scoped, DESIGN is aligned, and the full regression suite passes above the approved baseline without additional skips.

## Blocking Findings

None.

## Non-Blocking Findings

None.

## Test Review

- FR-2 status: **RESOLVED**.
- Direct rejection probe: called `validate_transition(policy, "NEEDS_INPUT", "CLEAR", record)` with `high_confidence`, `worker_reviewer_consensus`, `automated_default`, `completely_new_source`, an empty string, a missing `source`, and a missing `user_decision`. All seven cases raised `DecisionPolicyError`.
- Positive control: `explicit_user_reply` and `prior_explicit_user_authorization` were each accepted for both `NEEDS_INPUT -> CLEAR` and `CONFLICT -> CLEAR`. Separate records using `explicit_user_reply` and `USER_DECISIONS.md#UD-1` through `#UD-4` all validated, so the run's four user decisions are representable.
- Enforcement structure: `validate_transition()` checks `claimed not in policy.user_decision_sources`; the allowlist is exactly `{explicit_user_reply, prior_explicit_user_authorization}` and unknown strings are rejected. The five-item denylist was not expanded and no longer enforces authorization; it is retained only as a documented excluded-category set and disjointness guard.
- Contract integrity: direct loading reported four states, sixteen transition cells, eighteen reason codes, reason-code-required on all three non-CLEAR states, the original two forbidden cells, and the original two `requires_user_decision` edges. INV-4 retains irreversible/high-impact restrictions and `exception_allowed: false`. The only Skill-contract delta from `1efcc54` is the added positive vocabulary line in each Skill.
- DESIGN alignment: DESIGN's contract JSON, key table, fail-closed behavior, C24/C25 validator checks, state-selection partition, and guarded authority tests now describe the allowlist implementation and the denylist's demoted role.
- UD-1~UD-4: maintained. In particular, `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, preserving UD-3's pre-existing `evaluate_invocation()` behavior.
- Ran `python3 scripts/validate_skills.py`: PASS, 626 checks (baseline 622).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, 1349 tests in 294.104s, skipped=6 (baseline 1337 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, 173 source files.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran archive verification using the current `VERSION`: PASS, 173 source files.
- Ran `git diff --check`: PASS with no output.
- Scope: no changes to `VERSION`, `LICENSE`, `.orca`, Risk/Quality/Agent Profile semantics, lifecycle/Final Review surfaces, or `scripts/skill_policy.py`. No production importer outside the loader/validator was added, so OS-29/30/31 runtime wiring remains absent.
- FR-1 was not evaluated and does not affect this phase verdict, per the correction boundary.

## Final Decision

PASS. FR-2 is resolved in both rejection and acceptance directions, the solution is a genuine closed positive vocabulary rather than an expanded blacklist, DESIGN and implementation agree, and no regression or scope violation was found.

# Iteration 3 — Final Review Attempt 2 Corrections

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

FR-3 is RESOLVED, but FR-4 is NOT RESOLVED. The reported irreversible/external/security probe now correctly returns only `NEEDS_INPUT`, and ordinary positive controls remain reachable; however, the new independent entry predicates allow a determining policy source to produce `CLEAR` even when explicit user authority is reserved, and allow `CLEAR` alongside a declared requirement conflict. Those results contradict the approved authority precedence and leave the same unauthorized-CLEAR class of defect open.

## Blocking Findings

### RI3-1

- ID: RI3-1
- Quality Attribute: G3
- Severity: CRITICAL
- Blocking: YES
- Location: `scripts/decision_policy.py:516-570`, especially `_evaluate_predicate()` branches `determining_policy_source`, `undetermined_boundary_element`, and `declared_contradiction`; both Skill `entry_conditions`
- Issue: FR-4 — **NOT RESOLVED**. Entry predicates are evaluated independently without enforcing the approved authority precedence. A determining policy source unconditionally satisfies CLEAR and simultaneously suppresses NEEDS_INPUT for a reserved-user-authority element.
- Reason: Direct execution returned `['CLEAR']` for `{"explicit_user_authority":"reserved","policy_source":{"role":"determines"}}`, even though ANALYSIS A4-0 states that a policy source cannot un-reserve explicit user authority and the result must be NEEDS_INPUT. It also returned `['CLEAR','CONFLICT']` for `{"conflict_clause":"C-1","policy_source":{"role":"determines"}}`, even though ANALYSIS A3-1a/A4-0 states that a policy source cannot arbitrate contradictory explicit requirements. Because `permitted_states()` explicitly calls every returned state permitted, retaining CLEAR is not repaired by the separately declared aggregate order, for which no evaluator is applied here.
- Required Action: Encode the precedence/exclusion rules in the machine-evaluated entry conditions or evaluator so reserved user authority cannot be cleared by policy and a declared C-1/C-2/C-3 contradiction cannot permit CLEAR through a policy source. Add direct negative tests for both combinations, with positive controls showing determining policy still clears ordinary resolvable boundary items.

## Non-Blocking Findings

None.

## Test Review

- FR-3 status: **RESOLVED**. Loaded unchanged valid fixtures for `security_impact`, `privacy_impact`, and `irreversible_action`; all three were accepted. After changing only `boundary_element` to a different declared element, all three were rejected by `validate_record()`.
- FR-3 anti-vacuity: `test_every_bound_code_rejects_a_mismatched_boundary_element` derives the ten bound codes, asserts `len(bound) == 10` inside the same test, and then mutates each. The sibling positive test has its own co-located `len(bound) == 10` guard. `unclassifiable_decision` remains a separate positive control: its unchanged fixture validates without `boundary_element`, while adding one is rejected.
- FR-4 required probe: `permitted_states(policy, {"reversibility":"irreversible","blast_radius":"external_system","security":True})` returned exactly `['NEEDS_INPUT']`.
- FR-4 ordinary positive controls: a safe reversible fixture changed to a determining policy source returned `['CLEAR']`; the unchanged valid `repository_policy` assumption fixture returned `['ASSUMPTION_ALLOWED']`; three contradiction-free inputs did not contain CONFLICT.
- FR-4 counterexamples found during approved-semantics comparison: reserved authority plus a determining policy source returned `['CLEAR']`; C-1 contradiction plus a determining policy source returned `['CLEAR','CONFLICT']`. These directly contradict ANALYSIS A4-0 rows stating respectively “a policy source cannot un-reserve it -> NEEDS_INPUT” and “a policy source cannot arbitrate two explicit requirements -> CONFLICT.”
- Entry-condition data comparison: the four combinators/predicate lists transcribe the headline A3-1 table, but they omit the cross-condition precedence clarified by A3-1a/A4-0. The omission is behaviorally material, as the two probes above demonstrate.
- Two-axis sampling: on the “forbid and permit” axis I exercised unauthorized high impact, determining-policy CLEAR, supporting-policy ASSUMPTION_ALLOWED, contradiction absence, declared contradiction, reserved authority plus policy, and contradiction plus policy. On the “present and consistent” axis I exercised three bound reason codes in both unchanged and mismatched forms, the unbound-code exception, enum membership tests through the suite, and FR-2 source membership. I did not independently mutate every one of the ten bound reason codes because the co-located loop covers all ten in the full suite.
- FR-1/FR-2 preservation: exact comparison of the loaded sixteen-cell matrix to C26 passed; C27 boundary specs, C28 policy roles/kinds, and C29 state scope matched their constants. The allowlist remains exactly `explicit_user_reply` / `prior_explicit_user_authorization`, and direct `high_confidence` / invented-source transition records were rejected.
- UD-1~UD-4: maintained. `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty.
- Ran `python3 scripts/validate_skills.py`: PASS, 640 checks (baseline 638).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, 1384 tests in 295.719s, skipped=6 (baseline 1360 / skipped=6). The green suite does not cover the two counterexamples above.
- Ran `python3 scripts/verify_package.py`: PASS, 173 source files.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran archive verification using the current `VERSION`: PASS, 173 source files.
- Ran `git diff --check`: PASS with no output.
- Scope: no changes to `VERSION`, `LICENSE`, `.orca`, `scripts/skill_policy.py`, Risk/Quality/Agent Profile semantics, lifecycle code, or Final Review guarantees. No OS-29/30/31 runtime wiring was added.

## Final Decision

FAIL. FR-3 is resolved and the originally reported FR-4 probe passes, but FR-4 remains open because the evaluator permits CLEAR where reserved user authority or an explicit contradiction must outrank a determining policy source. This is an authorization-boundary correctness defect, not a style or completeness note.

# Iteration 4 — RI3-1 Authority Precedence Correction

RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES

## Summary

RI3-1 is RESOLVED. The two precedence counterexamples now return only their approved pausing states, ordinary policy and user resolvers still reach CLEAR, the supported-assumption path remains live, and sampled combinations show no new continuing-state leak. FR-1/FR-2/FR-3 and protected scope remain intact; one non-blocking count/coverage wording issue remains in the combination-sweep claim.

## Blocking Findings

None.

## Non-Blocking Findings

### RI4-N1

- ID: RI4-N1
- Quality Attribute: NONE
- Severity: LOW
- Blocking: NO
- Location: `scripts/test_decision_policy.py`, `AuthorityPrecedenceAcrossPredicates.test_no_combination_permits_a_continuing_state_without_a_resolver`; `artifacts/runs/run_3233a1469e97/IMPLEMENTATION.md`, iteration 4 “Combination sweep”
- Issue: The permanent test's docstring calls itself a 105-combination sweep, but its `cases` list has 21 entries and `no_resolver` has three entries, and the test itself asserts `checked == 21 * 3` — 63 combinations.
- Reason: Determining-policy and allowlisted-user positive combinations are exercised in sibling tests, and my direct samples found no leak, so this does not reopen RI3-1 or block the next Final Review. However, the statement that the named permanent test itself covers 105 combinations is wider than its executable evidence.
- Required Action: Correct the docstring/report to call this the 63 no-resolver combinations, or parameterize one guarded test over the intended five resolver categories and assert 105 if that consolidated coverage is desired.

## Test Review

- RI3-1 status: **RESOLVED**.
- Required precedence probe 1, with `policy_source.kind=file_path`: reserved explicit user authority plus determining policy returned exactly `['NEEDS_INPUT']`.
- Required precedence probe 2, with the same valid policy-source shape: C-1 plus determining policy returned exactly `['CONFLICT']`.
- Positive controls: security plus determining policy returned `['CLEAR']`; the unchanged valid `repository_policy` fixture returned `['ASSUMPTION_ALLOWED']`; reserved authority plus allowlisted `explicit_user_reply` returned `['CLEAR']`.
- Direct combination samples: reserved + supporting policy -> `['NEEDS_INPUT']`; C-2 + forbidden `model_confidence` decision -> `['CONFLICT']`; `open_decision_item:false` + security -> `['NEEDS_INPUT']`; security + C-3 -> `['CONFLICT','NEEDS_INPUT']`; C-3 + allowlisted decision -> `['CLEAR']`. The dual pausing-state result matches the per-item state model and CONFLICT-first aggregate order; no continuing state leaked without a valid resolver.
- Combination coverage not independently rerun: I did not manually enumerate all trigger/clause/resolver products. I sampled five cross-predicate combinations, ran the full suite, inspected the co-located trigger/clause guards, and separately exercised both required precedence cells and all three positive-control routes.
- Approved semantics: `authority_precedence.policy_source_cannot_resolve` contains exactly `explicit_user_authority` and `explicit_requirement_conflict`, matching ANALYSIS A4-0's two “policy cannot resolve” rows. `no_open_decision_item`, determining-policy, NEEDS_INPUT, and contradiction predicates now apply the corresponding exclusions symmetrically; allowlisted user authorization relocates either precedence cell to CLEAR.
- Empty facts: `permitted_states(policy,{})` returned `[]`. DESIGN D2-2c explicitly records this as intentional fail-closed behavior: silence does not affirm A3-1's “no item is open”; callers must declare `open_decision_item:false` to obtain CLEAR. That rationale is consistent with the contract's goal and is pinned by a test.
- FR-1 preservation: the loaded sixteen-cell matrix exactly equals C26, and both authority edges remain `requires_user_decision`.
- FR-2 preservation: allowlist remains exactly `explicit_user_reply` / `prior_explicit_user_authorization`; direct `high_confidence` and invented sources were rejected.
- FR-3 preservation: an unchanged `security_impact` fixture validated, while changing only its boundary element from security to privacy was rejected.
- C27/C28/C29/C31 exact checks all matched the loaded contract.
- UD-1~UD-4 maintained; `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty.
- Ran `python3 scripts/validate_skills.py`: PASS, 642 checks (baseline 640).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, 1399 tests in 297.815s, skipped=6 (baseline 1384 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, 173 source files.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran archive verification using the current `VERSION`: PASS, 173 source files.
- Ran `git diff --check`: PASS with no output.
- Scope: no changes to `VERSION`, `LICENSE`, `.orca`, `scripts/skill_policy.py`, Risk/Quality/Agent Profile semantics, lifecycle code, or Final Review guarantees. No OS-29/30/31 runtime dispatch wiring was added.

## Final Decision

PASS WITH NOTES. RI3-1 is resolved in both negative and positive directions, no sampled combination reopened the authority boundary, and all regression/scope checks pass. The remaining 63-versus-105 description is a non-blocking evidence-label precision issue for Final Review to read with the executable count.

# Iteration 5 — FR-5 Shared User-Decision Judgment Correction

RESULT: PASS
REVIEW_VERDICT: PASS

## Summary

FR-5 is RESOLVED. `permitted_states()` and `validate_transition()` now share the same complete-record judgment: source-only, single-field omissions, empty required fields, forbidden sources, and invented sources fail closed, while both complete genuine-user sources still authorize CLEAR. The prior FR-1 through FR-4 and RI3-1 protections, positive policy/assumption routes, test counts, packaging, and protected scope remain intact.

## Blocking Findings

None.

## Non-Blocking Findings

None.

## Test Review

- FR-5 status: **RESOLVED**. Directly evaluated the reserved-authority/security facts with `user_decision` variants: source-only, missing `where_recorded`, missing `resolves`, empty `where_recorded`, and empty `resolves` each returned exactly `['NEEDS_INPUT']`; `validate_transition(NEEDS_INPUT, CLEAR, ...)` rejected each. Thus the two production APIs gave the same negative authorization verdict in all five probes.
- Parity positive/negative sampling: complete `explicit_user_reply` and complete `prior_explicit_user_authorization` records each returned `['CLEAR']` and passed the transition; a complete forbidden-source record returned `['NEEDS_INPUT']` and failed the transition. Together with the five incomplete cases, no one-sided authorization result was found.
- Over-blocking controls: both complete genuine-user sources authorize CLEAR. A valid determining `file_path` policy source on an ordinary boundary fact returned `['CLEAR']`; the valid supporting-policy assumption fixture path remains accepted by the full suite. Reserved authority plus determining policy returned `['NEEDS_INPUT']`, C-1 plus determining policy returned `['CONFLICT']`, and the unprivileged irreversible/external/security probe returned `['NEEDS_INPUT']`.
- Incorrect positive control: `test_an_allowlisted_authorization_permits_clear` now calls `complete_decision(source)`, which supplies `source`, `where_recorded`, and `resolves`; it no longer pins the source-only defect.
- Negative sweep and anti-vacuity: `test_an_incomplete_decision_never_permits_clear_anywhere` guards five authority-sensitive situations and three declared fields in the same test, constructs four incomplete shapes, asserts the derived collection size, and asserts exactly `5 * 4` probes. It covers reserved authority, C-1/C-2/C-3, and irreversible/external/security impact. Its sibling positive sweep guards the same five situations and both allowlisted sources, asserting ten successful complete records.
- Shared-judgment implementation: `_user_decision_defect()` is the single production predicate used by both entry-condition evaluation and transition validation. It requires a non-empty mapping, all three declared fields with non-empty values, and membership in the closed `user_decision_sources` allowlist.
- Concept comparison sampled directly: valid and invalid policy-source kind/role inputs produced matching accept/reject behavior in facts and records; missing kind/locator remained consistently treated by both APIs. Changing an `explicit_requirement` fixture's state or adding a mismatched boundary element was rejected by `validate_record()`. Reason-code/state and code/boundary equality are record semantics rather than inputs consumed by `permitted_states()`, so no false cross-API parity claim is made for them.
- Prior protections: the full validation/unit suites retain exact transition-matrix constants and authority-edge tests (FR-1), the closed two-source user allowlist (FR-2), code-to-boundary equality (FR-3), evaluated high-impact entry conditions (FR-4), and authority precedence (RI3-1). Direct probes confirmed high impact -> NEEDS_INPUT, reserved+determining -> NEEDS_INPUT, C-1+determining -> CONFLICT, and ordinary determining policy -> CLEAR.
- Ran `python3 scripts/validate_skills.py`: PASS, `Skill validation PASSED (642 checks)` (baseline 642).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, `Ran 1408 tests in 296.686s`, `OK (skipped=6)` (baseline 1404 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, `Package verification PASSED (173 source files)`.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"`: PASS, 173 source files and archive verified.
- Ran `git diff --check`: PASS with no output.
- Scope and UD preservation: `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, so `evaluate_invocation()` is unchanged. The FR-5 delta is limited to decision-policy code/tests and DESIGN/IMPLEMENTATION reports; no changes appeared in `VERSION`, `LICENSE`, `.orca`, ROADMAP, workflow contract, Risk/Quality/Agent Profile semantics, Final Review guarantees, or OS-29/30/31 runtime wiring. UD-1 through UD-4 remain represented by complete decision records and the unchanged two-source allowlist.

## Final Decision

PASS. FR-5 is resolved through a shared fail-closed positive authorization vocabulary and complete-record check, not a widened blacklist; direct negative and positive probes agree across both production APIs. All required regression commands pass at or above baseline with no added skips or protected-scope regression.

# Iteration 6 — TR4-1/TR4-2/TR4-3 Correction

RESULT: PASS
REVIEW_VERDICT: PASS

## Summary

TR4-1, TR4-2, and TR4-3 are all RESOLVED. Direct execution shows shared role validation, zero disagreement across all 48 ASSUMPTION_ALLOWED combinations, correct rejection of whitespace-only evidence, and preservation of legitimate determining/supporting/user-decision paths. FR-1 through FR-5 and RI3-1 remain intact, all required commands pass above baseline without added skips, and the UD-5-authorized delta does not expand runtime scope or alter protected contracts.

## Blocking Findings

None.

## Non-Blocking Findings

None.

## Test Review

- TR4-1 status: **RESOLVED**. Starting from a complete valid ASSUMPTION_ALLOWED record and changing only `policy_source.role` to `invented_role`, `permitted_states()` and `validate_record()` both rejected with the same `unknown policy_source role` reason. Positive controls used role-appropriate records: a `determines` source produced `['CLEAR']` and its CLEAR record validated; a `supports` source produced `['ASSUMPTION_ALLOWED']` and its complete ASSUMPTION_ALLOWED record validated.
- TR4-2 status: **RESOLVED**. Independently enumerated all `3 reversibility × 4 blast_radius × 2 security × 2 reserved-authority = 48` combinations, supplying the identical complete record to both APIs. Result: `checked=48`, `divergences=0`, and exactly two combinations permitted ASSUMPTION_ALLOWED in both APIs.
- TR4-2 normative rule: adopting A3-1's entry condition is supported by the approved specification. A3-1 requires all of reversible-in-run, blast radius within requested scope, no high-impact flag, a supporting policy source, and no reserved authority. A4-0/INV-4 states a non-overridable prohibition and no exception; passing that prohibition alone does not establish permission. Therefore rejecting all six former middle-band cases follows the approved permission gate rather than inventing an over-blocking rule.
- TR4-2 positive control: reversible-in-run/current-change plus a supporting `file_path` policy source returned exactly `['ASSUMPTION_ALLOWED']` and validated. The second legitimate in-scope blast-radius case is covered in the 48-case enumeration; total permitted remained exactly two rather than falling to zero.
- TR4-3 status: **RESOLVED**. For each of `source`, `where_recorded`, and `resolves`, substituting three spaces caused `permitted_states()` to omit CLEAR and caused `validate_transition()` to reject with the corresponding non-empty-field message. Legitimate text, text with surrounding spaces, and text containing an internal space still authorized CLEAR, so stripping rejects blank evidence without over-stripping real evidence.
- Cross-API concept sampling: invalid boundary enum membership and invalid `policy_source.kind` were rejected by both evaluator and record validator with matching concept-specific messages; invalid role was likewise rejected by both; complete/incomplete user authorization agreed across evaluator and transition; ASSUMPTION_ALLOWED permission agreed in all 48 cases; required-value whitespace agreed across evaluator/transition/record paths. I did not independently re-enumerate all 109 Worker table cases, but sampled more than the required four concepts and inspected the structural call-closure result: both fact-reading APIs reach the same nine helpers, including the seven explicitly pinned fact rules.
- FR-1 preservation: both NEEDS_INPUT→CLEAR and CONFLICT→CLEAR remain `requires_user_decision`; CONFLICT→ASSUMPTION_ALLOWED remains `forbidden`.
- FR-2 preservation: complete `invented` and `high_confidence` user decisions were rejected by the closed allowlist.
- FR-3 preservation: an otherwise valid `security_impact` record relabeled with boundary element privacy was rejected.
- FR-4 preservation: irreversible/external/security facts without authority returned exactly `['NEEDS_INPUT']`.
- RI3-1 preservation: reserved authority plus determining policy returned exactly `['NEEDS_INPUT']`; C-1 plus determining policy returned exactly `['CONFLICT']`.
- FR-5 preservation: a source-only `explicit_user_reply` record returned `['NEEDS_INPUT']` and its NEEDS_INPUT→CLEAR transition was rejected for missing `where_recorded`.
- Positive controls beyond TR4-2: a complete genuine user decision permits CLEAR; an ordinary open item plus a determining source returns CLEAR; the safe supporting-policy route returns ASSUMPTION_ALLOWED.
- UD-1 through UD-5: maintained. USER_DECISIONS.md records UD-5 as a phase-specific implementation-budget extension to seven iterations without widening decision authority or other phase budgets. `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, so `evaluate_invocation()` is unchanged.
- Ran `python3 scripts/validate_skills.py`: PASS, `Skill validation PASSED (642 checks)` (baseline 642).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, `Ran 1425 tests in 300.939s`, `OK (skipped=6)` (baseline 1413 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, `Package verification PASSED (173 source files)`.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"`: PASS, 173 source files and archive verified.
- Ran `git diff --check`: PASS with no output.
- Scope: the correction changes decision-policy evaluator/tests and DESIGN/IMPLEMENTATION documentation only. Diffs for both Skill contracts, templates, reviews, `VERSION`, `LICENSE`, `.orca`, `scripts/skill_policy.py`, Risk/Quality/Agent Profile code, and Final Review guarantees are empty. No OS-29/30/31 runtime dispatch, question, wait, approval-adapter, or pause/resume wiring was introduced.

## Final Decision

PASS. TR4-1/TR4-2/TR4-3 are resolved using shared judgments aligned with the approved specification, with full 48-case parity and negative/positive controls proving neither authority leakage nor blanket refusal. All prior corrections and protected surfaces remain intact, and the complete validation suite is green above baseline with unchanged skips.

# Iteration 7 — FR-6 Declared-Grounds Correction

RESULT: PASS
REVIEW_VERDICT: PASS

## Summary

FR-6 is RESOLVED. All eight shipped NEEDS_INPUT fixtures reject a one-field non-triggering mutation, all eighteen valid fixtures remain accepted, and explicit probes cover the approved grounds rules for CLEAR, ASSUMPTION_ALLOWED, NEEDS_INPUT, and CONFLICT without over-blocking the declared ambiguity or unclassifiable exceptions. The common Reviewer guidance is complete and byte-identical, all prior corrections remain live, and the full validation suite passes above baseline with unchanged skips and protected scope.

## Blocking Findings

None.

## Non-Blocking Findings

None.

## Test Review

- FR-6 status: **RESOLVED**.
- Required eight mutations: changed only the triggering fact in each shipped fixture and called `validate_record()`. `security_impact/security=false`, `privacy_impact/privacy=false`, `monetary_cost/monetary_cost=false`, `compliance_impact/compliance=false`, `long_term_lock_in/long_term_lock_in=false`, `irreversible_action/reversibility=reversible_in_run`, `blast_radius_beyond_scope/blast_radius=current_change`, and `authority_reserved_to_user/explicit_user_authority=delegated` were all rejected with a message naming the non-triggering value and stating that the boundary did not fire.
- Ambiguity case: unchanged `ambiguous_requirement` and `missing_user_intent` fixtures carry no separate `ambiguity` value and both remain accepted. This is consistent with approved ANALYSIS A4-1 row 1: the machine-checkable declaration is the reason code plus citation; whether the text is genuinely ambiguous is the judgment half. Supplying an explicit `ambiguity:false` value is rejected. Thus the Final Review's ninth probe is not silently ignored; its absence is the defined declared-element shape, while a contradictory supplied value fails.
- Eighteen-fixture anti-over-blocking control: enumerated `scripts/fixtures/decision_policy/valid/*.json`, asserted the observed count was 18, and passed every fixture to `validate_record()`. Result: `total=18`, `accepted=18`, `rejected=[]`. `unclassifiable_decision` remains accepted without a bound element.
- NEEDS_INPUT grounds: all eleven shipped NEEDS_INPUT fixtures validate with their positive evidence. The eight value-bearing codes use their triggering values; the two ambiguity codes use the A4-1 declared-element form; `unclassifiable_decision` remains the deliberate no-element exception.
- CONFLICT grounds: each of the three shipped code fixtures validates with implicit code-bound clause evidence and with an explicitly matching `conflict_clause`; each rejects a different declared C-1/C-2/C-3 clause. Citation minima and non-empty contradiction explanation continue to be enforced by the existing record checks.
- CLEAR grounds: `{state:CLEAR}`, explicit `open_decision_item:false`, determining policy, and complete genuine user-decision forms validate. A supporting-only policy source and an incomplete user decision are rejected because the declared grounds do not satisfy CLEAR's entry condition. Grounds-free CLEAR remains the existing optional/no-open-record form rather than a newly mandatory record.
- ASSUMPTION_ALLOWED grounds: a reversible-in-run/current-change record with supporting policy validates; a reversible-with-effort record is rejected by the shared entry-condition rule.
- Reviewer guidance: both `reviews/common.md` copies state a concrete criterion for CLEAR declared grounds, the full ASSUMPTION_ALLOWED entry condition and INV-4, NEEDS_INPUT code-bound triggering values plus the declared/unclassifiable exceptions, and CONFLICT code/clause/citation alignment. `cmp -s` returned exit 0, proving the two copies are byte-identical. `validate_shared_directories()` compares every shared relative file with `read_bytes()` equality and is executed by `validate_skills.py`; the 642-check green run confirms the shipped copies satisfy it.
- FR-1: both authority edges remain `requires_user_decision`, and CONFLICT→ASSUMPTION_ALLOWED remains forbidden.
- FR-2: a complete invented user-decision source is rejected.
- FR-3: a security-impact record relabeled as privacy is rejected.
- FR-4: irreversible/external/security without authority returns only NEEDS_INPUT.
- RI3-1: reserved+determining returns only NEEDS_INPUT; C-1+determining returns only CONFLICT.
- FR-5: source-only user evidence returns NEEDS_INPUT and fails the transition.
- TR4-1: invented policy role is rejected by both evaluator and record validator.
- TR4-2: the exhaustive identical-input enumeration returned `checked=48`, `divergence=0`, `allowed=2`, exactly reversible-in-run with current-change/module and no security/reserved authority.
- TR4-3: whitespace-only decision location returns NEEDS_INPUT and fails transition validation.
- Call-closure preservation: all three `FactReadingApisShareEveryRule` tests pass. Its bounded record-only difference now names exactly `_grounds_defect` and `_triggering_text`, while evaluator-only differences remain forbidden and the seven shared fact helpers remain pinned.
- UD-1 through UD-6: maintained. USER_DECISIONS.md records UD-6's explicit implementation/test/final-review budget extension without widening decision authority. `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, so `evaluate_invocation()` is unchanged.
- Ran `python3 scripts/validate_skills.py`: PASS, `Skill validation PASSED (642 checks)` (baseline 642).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, `Ran 1433 tests in 300.546s`, `OK (skipped=6)` (baseline 1426 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, `Package verification PASSED (173 source files)`.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"`: PASS, 173 source files and archive verified.
- Ran `git diff --check`: PASS with no output.
- Scope: the correction changes decision-record validation/tests, byte-shared Reviewer guidance, and DESIGN/IMPLEMENTATION documentation. Diffs for `VERSION`, `LICENSE`, `.orca`, `scripts/skill_policy.py`, Risk/Quality/Agent Profile code, templates, and runtime lifecycle surfaces are empty. No OS-29/30/31 dispatch, question, wait, approval-adapter, or pause/resume wiring was added. FR-7 remains correctly excluded from this implementation gate and is not used as a reason to fail it.

## Final Decision

PASS. FR-6 is resolved across every machine-checkable state-ground relationship required by the approved specification, and the 18/18 positive fixture control proves the correction does not obtain safety by refusing valid records. Prior fixes, shared Reviewer guidance, parity/closure protections, full regression commands, and protected scope all remain intact.

# Iteration 8 — FR-8 Boolean-Domain Correction

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

FR-8 is **RESOLVED**: every required non-boolean value is rejected by both production APIs, valid booleans and omission retain their intended behavior, and 18/18 valid fixtures pass. However, the required value-position audit exposed a new fail-open authority-domain gap: `explicit_user_authority` accepts arbitrary values and treats them as not reserved, so malformed or misspelled reserved-authority declarations permit `ASSUMPTION_ALLOWED`. That verified authority-boundary bypass is blocking even though the FR-8 correction and all regression commands are green.

## Blocking Findings

### RI8-1

- ID: RI8-1
- Quality Attribute: G1
- Severity: CRITICAL
- Blocking: YES
- Location: `scripts/decision_policy.py`, `_domain_defect()`, `_validate_declared_facts()`, `_element_is_triggering()`; policy contract `boundary_elements.explicit_user_authority`
- Issue: The `explicit_user_authority` boundary declares only `triggering: ["reserved"]` and no accepted-value domain, while `_domain_defect()` deliberately leaves the `user_decision` kind unchecked. Consequently any value other than exact lowercase `reserved` is treated as non-triggering rather than malformed, including values that plainly attempt to say authority is reserved.
- Reason: Direct identical-input probes with `explicit_user_authority` set to `"RESERVED"`, `"resrved"`, `1`, `{}`, and `None` all returned `['ASSUMPTION_ALLOWED']` from `permitted_states()` and were accepted by `validate_record()`. Only `delegated` among those is the known legitimate non-reserved value used by the approved A4-0 truth table and prior correction probes. This is the same fail-open shape as FR-8 on a more direct authority boundary, and V3 explicitly makes a newly found unchecked value position blocking; a typo or malformed value must not silently buy autonomous continuation.
- Required Action: Define a machine-readable accepted domain for `explicit_user_authority` that includes the approved legitimate forms (at least `reserved` and `delegated`) and reject every other declared value fail-closed through the shared declared-facts path. Add identical-input evaluator/record negative controls for case variants, misspellings, numbers, mappings, null, and containers, plus positive controls for every admitted value and omission.

## Non-Blocking Findings

None.

## Test Review

- FR-8 status: **RESOLVED**. For each of `'yes'`, `1`, `0`, `{'a': 1}`, `None`, `'false'`, and `[]` at `security`, direct calls showed `permitted_states()` and `validate_record()` both rejected with the same boolean-domain error. No required adversarial value was accepted by either API.
- Boolean positive controls: `security=True` produced `['NEEDS_INPUT']` and the shipped `security_impact` record validated; `security=False` produced `['ASSUMPTION_ALLOWED']` and a complete ASSUMPTION_ALLOWED record validated; omitting `security` also produced `['ASSUMPTION_ALLOWED']` and validated. Thus the fix distinguishes malformed declaration from true, false, and silence rather than rejecting everything.
- Fixture control: enumerated all files under `scripts/fixtures/decision_policy/valid`; result was `fixtures 18 18`, with no rejection.
- FR-8 permanent tests: `python3 -m unittest scripts.test_decision_policy.Fr8BooleanBoundariesFailClosed -v` ran 7 tests and returned `OK`, including 5 boolean elements × 7 malformed values, both-API parity, true/false controls, omission, kind-partition guard, and conflict-clause domain.
- Value-position samples: independently injected invalid values at five positions. Out-of-set `reversibility`, non-boolean `ambiguity`, non-list `explicit_requirement_conflict`, unknown `conflict_clause`, and unknown `policy_source.kind` were each rejected by both APIs; their concept-specific messages matched. This confirms the Worker table on those checked positions.
- New gap: the same audit directly probed the table's disclosed open `explicit_user_authority` position. Values `"RESERVED"`, `"resrved"`, `1`, `{}`, and `None` all yielded `['ASSUMPTION_ALLOWED']` and valid records; `delegated` did too, but is the legitimate positive control. The implementation report truthfully names the `"RESERVED"` residual limit, so this finding does not accuse it of hiding the gap; it classifies the verified unchecked authority surface under V3.
- Approved-spec comparison: ANALYSIS A4-0 says reserved authority forbids ASSUMPTION_ALLOWED and contrasts it with the legitimate delegated case used in prior probes; A4-1 makes declared combinations machine-checkable. Rejecting values outside the admitted authority vocabulary is therefore fail-closed enforcement, while continuing to admit `delegated` prevents over-blocking.
- Regression set: directly ran 92 tests spanning transition-value pinning, boundary/code equality, entry-condition evaluation, precedence, user-authority allowlisting and parity, 48-case parity, role/whitespace parity, call closure, FR-6, FR-7, and FR-8. Result: `Ran 92 tests ... OK`.
- Independent parity count: enumerated 3 reversibility × 4 blast-radius × 2 security × 2 reserved-authority combinations with identical records for both APIs. Result: `parity 48 divergences 0 permitted 2`.
- FR-6 regression: applied the eight approved one-field non-triggering mutations from `Fr6DeclaredEvidenceMustJustifyTheState.NON_TRIGGERING`; result: `FR6_nontriggering 8 rejected 8`.
- UD and scope: USER_DECISIONS.md contains UD-1 through UD-7. `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, so `evaluate_invocation()` is unchanged. The FR-8 delta changes decision-policy validation/tests and DESIGN/IMPLEMENTATION documentation only; protected Skill contracts, Risk/Quality/Agent Profile semantics, Final Review guarantees, `VERSION`, `LICENSE`, and OS-29/30/31 runtime surfaces are unchanged.
- Ran `python3 scripts/validate_skills.py`: PASS, `Skill validation PASSED (642 checks)` (baseline 642).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, `Ran 1448 tests in 301.338s`, `OK (skipped=6)` (baseline 1441 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, `Package verification PASSED (173 source files)`.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"`: PASS, 173 source files and archive verified.
- Ran `git diff --check`: PASS with no output.
- Not independently checked: I sampled five of the Worker’s sixteen value positions rather than reconstructing all sixteen, as permitted by V3. I did not treat FR-9 as a reason to fail this phase; RI8-1 arises independently from the implementation-phase value-position audit required here.

## Final Decision

FAIL. FR-8 itself is resolved without over-blocking and all prior regression baselines remain green, but the same domain audit proves that malformed explicit-authority values still erase a reserved-authority boundary and permit autonomous continuation. RI8-1 must be closed fail-closed before this implementation gate can pass.

# Iteration 9 — RI8-1 Authority-Domain Re-review

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

RI8-1 is **RESOLVED**: all seven required unrecognised authority values are rejected by both APIs, exact `reserved` still yields NEEDS_INPUT, omission remains the legal non-reserved representation, and all FR-8 boolean controls and 18 fixtures remain valid. The required review of the two remaining open positions found `repository_project_policy` reasonably harmless because it cannot trigger, but found the policy-source locator judgment incomplete: missing, blank, non-string, and nonexistent locators all satisfy the allegedly “locatable policy source” entry condition. That new verified provenance bypass is blocking even though every regression command is green.

## Blocking Findings

### RI9-1

- ID: RI9-1
- Quality Attribute: G1
- Severity: MAJOR
- Blocking: YES
- Location: `scripts/decision_policy.py`, `_validate_declared_facts()`, `_evaluate_predicate('supporting_policy_source')`; policy-source object schema and ASSUMPTION_ALLOWED fixtures
- Issue: `policy_source.locator` has no shape or presence validation. A policy source with a legal `kind` and `role` is treated as locatable even when `locator` is absent, empty, non-string, or points nowhere.
- Reason: Direct identical-input probes using a complete ASSUMPTION_ALLOWED record showed that policy sources `{role:'supports', kind:'file_path'}`, the same object with `locator:''`, with `locator:123`, and with `locator:'/definitely/not/existing'` all produced `['ASSUMPTION_ALLOWED']` and were accepted by `validate_record()`. ANALYSIS A3-1 makes a **locatable** supporting policy source constitutive of ASSUMPTION_ALLOWED, A4-1 names a file path/requirement id/quality-attribute id/phase-contract section and calls existence checkable, and the shipped four positive fixtures all carry non-empty locators. Filesystem existence may require an I/O-capable layer, but requiring a non-empty textual locator does not; leaving even presence and shape open lets a record claim policy support without naming any policy.
- Required Action: Require every declared `policy_source` used to determine or support a state to contain a non-empty textual `locator`, enforced through the shared declared-facts path for evaluator/record parity. Add negative controls for missing, empty, whitespace-only, null, numeric, mapping, and sequence locators; retain positive controls for all four admitted kinds. Keep actual locator existence resolution in the appropriate I/O-capable validation layer, and state that remaining limitation precisely rather than treating the entire locator position as open.

## Non-Blocking Findings

None.

## Test Review

- RI8-1 status: **RESOLVED**. With `explicit_user_authority` set to `'RESERVED'`, `'reserved '`, `'reserverd'`, `1`, `{'a':1}`, `None`, or `'delegated'`, both `permitted_states()` and `validate_record()` rejected; all seven pairs returned matching rejection messages.
- Authority positive controls: exact `'reserved'` returned `['NEEDS_INPUT']` and its shipped NEEDS_INPUT record validated. Omitting the element returned `['ASSUMPTION_ALLOWED']` and the complete ASSUMPTION_ALLOWED record validated. The corrected rationale is supported by repository evidence: `delegated` is not a contract value, and omission represents not-reserved.
- FR-8 over-blocking controls: `security=True` returned NEEDS_INPUT and its shipped record validated; `security=False` returned ASSUMPTION_ALLOWED and its record validated; omission also returned ASSUMPTION_ALLOWED and validated.
- Fixture count: enumerated `scripts/fixtures/decision_policy/valid/*.json`; result was `fixtures 18 18`, so all 18 shipped records remain accepted.
- Permanent correction tests: `python3 -m unittest scripts.test_decision_policy.Ri81AuthorityBoundaryFailsClosed scripts.test_decision_policy.Fr8BooleanBoundariesFailClosed -v` ran 13 tests and returned `OK`.
- Remaining open boundary element: injected `'anything'`, `123`, `{}`, `None`, and `[]` at `repository_project_policy`; both APIs continued to agree and the item remained ASSUMPTION_ALLOWED. This is not a blocking gap because the contract declares `triggering: null`, `_element_is_triggering()` is false for every value, and the actual policy authority is represented by the separately checked `policy_source` object. The new test pins this no-trigger premise so the judgment cannot silently survive a future triggering change.
- Locator probe: a supporting `file_path` source with missing locator, `locator:''`, `locator:123`, or `locator:'/definitely/not/existing'` was accepted by both APIs and yielded ASSUMPTION_ALLOWED. The Worker’s pure-function/I/O rationale justifies deferring existence checks, but does not justify omitting non-empty-string shape validation; RI9-1 is based on that narrower directly checkable gap.
- Value-position sampling: independently checked five positions beyond locator. Invalid enum `reversibility`, non-boolean declared `ambiguity`, non-list citations element, unknown `conflict_clause`, and unknown `policy_source.kind` were rejected by both APIs with matching concept messages. No additional domain gap was found in the sampled closed positions.
- Direct regression enumeration: the 48 identical-input combinations returned `parity 48 divergences 0 permitted 2`; FR-6’s eight non-triggering mutations returned `FR6 8 8`; FR-8’s seven malformed boolean values returned `FR8 7 7`.
- Prior correction suite: directly ran 98 tests spanning FR-1 through FR-8, RI3-1, RI8-1, and TR4-1 through TR4-3. Result: `Ran 98 tests ... OK`.
- UD and scope: USER_DECISIONS.md contains UD-1 through UD-7. `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, so `evaluate_invocation()` remains unchanged. The RI8-1 delta changes decision-policy validation/tests and DESIGN/IMPLEMENTATION documentation only; Skill contracts, templates/reviews, Risk/Quality/Agent Profile semantics, Final Review guarantees, `VERSION`, `LICENSE`, and OS-29/30/31 runtime surfaces are unchanged.
- Ran `python3 scripts/validate_skills.py`: PASS, `Skill validation PASSED (642 checks)` (baseline 642).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, `Ran 1454 tests in 300.722s`, `OK (skipped=6)` (baseline 1448 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, `Package verification PASSED (173 source files)`.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"`: PASS, 173 source files and archive verified.
- Ran `git diff --check`: PASS with no output.
- Not independently checked: I sampled five of the remaining value positions rather than reconstructing all sixteen, as V3b permits. I did not use FR-9 as a reason to fail; RI9-1 comes from the implementation-phase open-position review explicitly required by V3.

## Final Decision

FAIL. RI8-1 and FR-8 are resolved without regression or over-blocking, but ASSUMPTION_ALLOWED still accepts a policy source that is not locatable even at the machine-checkable presence/type level. RI9-1 must be fixed or explicitly reconciled with the approved locatable-source requirement before this implementation gate can pass.

# Iteration 10 — RI9-1 Locator-Shape Re-review

RESULT: PASS
REVIEW_VERDICT: PASS

## Summary

RI9-1 is **RESOLVED**. Missing, blank, whitespace-only, numeric, null, mapping, and sequence locators are rejected identically by both APIs, while real textual locators and all 18 shipped fixtures remain accepted. The 16-position audit now distinguishes machine-checkable shape from I/O-dependent existence honestly, all prior safety regressions remain live, and the complete validation suite passes above baseline with unchanged skips and protected scope.

## Blocking Findings

None.

## Non-Blocking Findings

None.

## Test Review

- RI9-1 status: **RESOLVED**. Direct identical-input probes with a missing `locator`, `""`, `"   "`, `42`, `None`, `{'a':1}`, and `[]` caused both `permitted_states()` and `validate_record()` to reject. Every pair agreed; no malformed locator passed one API.
- Locator positive controls: `docs/x.md`, `OS-28#record-fields`, `DOMAIN-001`, `scripts/`, and a padded but non-blank path all produced ASSUMPTION_ALLOWED and validated. Thus the correction enforces shape without restricting locator syntax beyond the approved non-empty-text requirement.
- Fixture count: enumerated `scripts/fixtures/decision_policy/valid/*.json`; result was `fixtures 18 18`, with no rejection.
- Permanent correction tests: `python3 -m unittest scripts.test_decision_policy.Ri91LocatorShapeIsEnforced scripts.test_decision_policy.Ri91SweptShapeGaps -v` ran 9 tests and returned `OK`. They cover missing/malformed locators, identical-API parity, genuine locators, explicit existence limitation, user-decision text fields, citation entry text, and the inert open element.
- Sixteen-position sampling: directly mutated eight representative positions. Invalid enum `reversibility`, malformed boolean `security`, unrecognised `explicit_user_authority`, blank `policy_source.locator`, numeric `user_decision.where_recorded`, numeric `user_decision.resolves`, numeric citation entries, and unknown `conflict_clause` were rejected by their relevant APIs. This exceeds the required four-position sample, and no new checkable shape gap was found.
- Checkability classification: the documentation now says locator **shape** is checked while target **existence** is not checked at this pure layer and requires I/O. A direct nonexistent locator, `no/such/file/anywhere.md#nope`, remained accepted by both evaluator and record validator, matching the disclosed limit rather than an existence-check claim.
- Inert open element: `repository_project_policy` remains unchecked as a direct value, but the contract gives it `triggering: null`, no reason code binds it, and direct arbitrary values cannot change the decision. Its operative policy-source object now enforces kind, role, and locator shape. The permanent test pins all three premises, so leaving it open is reasoned rather than accidental.
- Additional sweep corrections: `user_decision.where_recorded` and `resolves` now require text as well as non-emptiness; each citation entry must be non-empty text in addition to satisfying the count. Direct numeric probes were rejected, while genuine decision evidence and shipped citations remain accepted.
- Independent regression enumeration: 48 identical-input combinations returned `parity 48 divergences 0 permitted 2`; FR-6 returned `8/8` non-triggering mutations rejected; FR-8 returned `7/7` malformed boolean values rejected; RI8-1 returned `9/9` unrecognised authority values rejected.
- Prior correction suite: directly ran 107 tests spanning FR-1 through FR-8, RI3-1, RI8-1, RI9-1, and TR4-1 through TR4-3. Result: `Ran 107 tests ... OK`.
- UD and scope: USER_DECISIONS.md contains UD-1 through UD-8, including the authorized budget extension; UD-1 through UD-7 remain unchanged in meaning. `git diff c264e79 HEAD -- scripts/skill_policy.py` is empty, so `evaluate_invocation()` remains unchanged. The RI9-1 delta changes decision-policy validation/tests and DESIGN/IMPLEMENTATION documentation only; Skill contracts, templates/reviews, Risk/Quality/Agent Profile semantics, Final Review guarantees, `VERSION`, `LICENSE`, and OS-29/30/31 runtime surfaces are unchanged.
- Ran `python3 scripts/validate_skills.py`: PASS, `Skill validation PASSED (642 checks)` (baseline 642).
- Ran `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, `Ran 1463 tests in 299.041s`, `OK (skipped=6)` (baseline 1454 / skipped=6).
- Ran `python3 scripts/verify_package.py`: PASS, `Package verification PASSED (173 source files)`.
- Ran `python3 scripts/build_release.py`: PASS, built `dist/orca-skills-0.9.0.tar.gz`.
- Ran `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"`: PASS, 173 source files and archive verified.
- Ran `git diff --check`: PASS with no output.
- Not independently checked: I sampled eight of the sixteen value positions rather than reconstructing every Worker probe. I did not test filesystem existence because this layer explicitly does not claim to do so, and I did not use FR-9 as a reason to fail this implementation gate.

## Final Decision

PASS. RI9-1 is closed through a shared fail-closed shape check, genuine locators and fixtures remain usable, and the remaining existence limitation is accurately bounded to an I/O-capable layer. No blocking or non-blocking implementation finding remains in this delta.