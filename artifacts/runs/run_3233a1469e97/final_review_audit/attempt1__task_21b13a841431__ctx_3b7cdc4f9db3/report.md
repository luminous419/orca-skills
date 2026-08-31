RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

Final adversarial review found two blocking correctness defects. First, the validator pins only the two `forbidden` transition cells, so both Skills can simultaneously weaken `NEEDS_INPUT -> CLEAR` or `CONFLICT -> CLEAR` from `requires_user_decision` to `allowed` while all 622 static checks remain green. Second, `validate_transition()` treats every non-empty `user_decision.source` except five exact blacklist tokens as user authority, so model-confidence and Worker/Reviewer-agreement aliases are accepted despite the explicit requirement that those can never authorize a decision. The requested baseline commands otherwise pass, the 11 boundary-element names and 18 reason codes are present, the risk-axis behavioral checks are non-vacuous, and no OS-29/30/31 runtime wiring or protected-file change was found.

The ANALYSIS Reviewer's first dispatch failed at `agent_readiness` because of a Codex TUI update prompt; the prompt was skipped, the Task was manually restored to ready for recovery, and a fresh dispatch completed. This provenance exception is recorded here and does not change the technical verdict.

## Blocking Findings

### FR-1

- ID: FR-1
- Quality Attribute: G3
- Severity: MAJOR
- Blocking: YES
- Responsible Phase: test
- Location: `scripts/validate_skills.py:2173-2178`, `scripts/test_validate_skills.py:134-144`, both `SKILL.md` transition tables
- Issue: The exact allowed-transition semantics are not pinned. C8 compares only the set of cells whose value is `forbidden`; C11c checks only closed-set membership. Consequently, changing both Skills' `NEEDS_INPUT -> CLEAR` value from `requires_user_decision` to the legal value `allowed` retains a green validator.
- Reason: The contract promises that unresolved `NEEDS_INPUT` and `CONFLICT` cannot continue and that reaching `CLEAR` requires an actual user decision. A static contract validator that accepts simultaneous removal of that requirement does not protect the machine-readable authorization boundary. This is distinct from the disclosed M-21 coordinated prose-plus-constant gap: the mutation changes only the two Skill contracts and does not edit an expected constant.
- Evidence: In a disposable `git archive HEAD` copy, both Skill files were changed from `"NEEDS_INPUT": {"CLEAR": "requires_user_decision"` to `"NEEDS_INPUT": {"CLEAR": "allowed"`. `python3 scripts/validate_skills.py` still reported `Skill validation PASSED (622 checks)` with exit code 0. The existing mutation test covers only relaxing a `forbidden` transition to `allowed`, so it does not exercise either user-decision-required edge.
- Required Action: Pin the complete 4x4 transition matrix (or at minimum every non-`allowed` cell and its exact value) in the validator, add disposable-copy regressions for both `NEEDS_INPUT -> CLEAR` and `CONFLICT -> CLEAR`, and demonstrate that each mutation fails while the unmodified repository remains green.

### FR-2

- ID: FR-2
- Quality Attribute: G3
- Severity: MAJOR
- Blocking: YES
- Responsible Phase: implementation
- Location: `scripts/decision_policy.py:502-516`, `SKILL.md` `user_decision_fields` / `forbidden_authority_sources`, `scripts/test_decision_policy.py:261-275`
- Issue: User authority is implemented as an open-ended source string minus five exact forbidden tokens. Values such as `high_confidence`, `worker_reviewer_consensus`, and `automated_default` satisfy a `requires_user_decision` transition.
- Reason: A denylist of spellings cannot enforce the categorical rules that model confidence, Worker+Reviewer agreement, recommended defaults, timeout, and silence are never user approval. The contract therefore permits semantically forbidden evidence under any unlisted spelling, contradicting the explicit authority boundary and the claim that confidence can never be a basis for decision authority.
- Evidence: Against the unmodified contract, direct calls to `validate_transition(policy, "NEEDS_INPUT", "CLEAR", record)` rejected only the exact source `model_confidence`; otherwise identical records with sources `high_confidence`, `worker_reviewer_consensus`, and `automated_default` were accepted. The current test iterates only the five configured blacklist strings and therefore proves exact-token rejection, not the claimed authority property.
- Required Action: Define a closed, positive machine-readable vocabulary for evidence that constitutes actual user authority (or an equivalently strict typed proof), reject every other source fail-closed, and add positive tests for genuine user evidence plus adversarial alias/unknown-source tests. Do not solve this by expanding an inevitably incomplete synonym blacklist.

## Non-Blocking Recommendations

- Preserve the disclosed M-21 coordinated three-file variant as a known static-review limitation; neither blocking finding above is that known gap.
- Preserve UD-2's exact limitation: contract tests prove permission for a safe reversible item, not that a live model will avoid over-escalation.
- Preserve UD-4's wording as an unverified assumption that the 11 boundary elements cover important repository-policy classes.
- Keep the existing `evaluate_invocation()` schema-version behavior documented as the UD-3 pre-existing out-of-scope defect.

## Test Review

- `python3 scripts/validate_skills.py` — PASS, 622 checks. It verifies current Skill parity, expected decision-policy constants, shared templates/reviews, routing, and policy gates; FR-1 demonstrates that it does not verify the exact full transition matrix.
- `python3 -m unittest discover -s scripts -p 'test_*.py'` — PASS, 1337 tests in 299.711s, skipped=6. It verifies the present loader/evaluator behavior and repository regressions; it does not close FR-1 or detect semantic aliases in FR-2.
- `python3 scripts/verify_package.py` — PASS, 165 source files.
- `python3 scripts/build_release.py` — PASS; built `dist/orca-skills-0.9.0.tar.gz`.
- `python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"` — PASS, 165 source files.
- `git diff --check` — PASS with no output.
- Disposable mutation: changed both Skill contracts' `NEEDS_INPUT -> CLEAR` rule to `allowed`; `python3 scripts/validate_skills.py` incorrectly remained PASS at 622 checks. A partial mutated full-suite run showed failures and was interrupted after the static-validator result was established; it is not claimed as a completed test run.
- Direct authority probe: exact `model_confidence` was rejected, while `high_confidence`, `worker_reviewer_consensus`, and `automated_default` were accepted as `user_decision.source` for `NEEDS_INPUT -> CLEAR`.
- Scope checks: `git diff --quiet c264e79..HEAD -- VERSION LICENSE .orca scripts/skill_policy.py scripts/quality_profile.py scripts/agent_profile.py scripts/workflow_contract.py` returned 0. A production-code search found no decision-policy runtime importer outside the new loader and validator, so OS-29/30/31 wiring is absent as intended.
- Contract inspection confirmed all 11 required boundary-element names, exactly four decision states, 18 reason codes per UD-4, explicit axis-independence declarations, and optional Decision Record sections in the shared templates/reviewer contract. These structural facts do not remedy the two authorization-enforcement failures.

## Final Decision

FAIL. FR-1 allows the machine-readable user-decision transition boundary to be weakened in both Skills without validator failure, and FR-2 accepts non-user evidence under arbitrary source spellings as user authority. Both contradict explicit P0/High requirements and require correction plus focused regression evidence before Final Review can pass.
