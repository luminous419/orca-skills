# Worker Result

STATUS: COMPLETE

Phase: IMPLEMENTATION · Iteration 1 · Run `run_3233a1469e97` · risk `high`
Ticket: **Jira OS-28 "Define Bounded Autonomy Decision Policy Contract"** (P0/High)
Branch: `feat/os-28-bounded-autonomy-policy` (base `main` @ `c264e79`) — 5 commits, **not pushed**.

Specification: `DESIGN.md` (1022 lines, gate PASS). Implemented as specified. The two places where
implementation forced a detail DESIGN did not fix are recorded in *Analysis* with their reasons; no
DESIGN decision was overturned and no user decision was reinterpreted.

---

## Summary

All six required commands were run on the committed tree and their real output is recorded below.
**Both baselines rose: 501 → 604 validator checks, 1269 → 1326 tests, both suites green, nothing
skipped to hide a failure.**

Three things are worth the Reviewer's attention before the detail:

1. **The mutation table corrects itself.** My first mutation harness reported **22/22 caught**,
   including M-21 — the prose case DESIGN F-5 explicitly records as a residual gap. That was **a
   harness artifact**: the runner re-serialized the whole JSON block, which changed its formatting
   and tripped unrelated checks. Re-run surgically, **M-21 is genuinely MISSED**, exactly as DESIGN
   predicted. It is reported as MISSED below. 21 of 22 are caught, and the two headline mutations
   (M-3 simultaneous deletion, M-17 closed-value) were each re-verified surgically.
2. **I initially skipped a DESIGN deliverable and then implemented it.** D4-E's validator regression
   tests were missing from my first pass — the mutation run proved the checks work *today*, but
   DESIGN requires them as permanent tests. Thirteen were added in commit `a7e9278`.
3. **One implementation-discovered defect in my own test**, found by running it rather than by
   reading: an assertion expected the wrong (weaker) failure message. Corrected to the real one.

---

## Analysis

### Commands run — actual output

Every number below came from the command beside it, run on the committed tree at `a7e9278`.

| # | command | result |
|---|---|---|
| 1 | `python3 scripts/validate_skills.py` | **Skill validation PASSED (604 checks)**, exit 0 |
| 2 | `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1326 tests in 293.950s — OK (skipped=6)**, exit 0 |
| 3 | `python3 scripts/verify_package.py` | **Package verification PASSED (165 source files)** |
| 4 | `python3 scripts/build_release.py` | **Built reproducible release archive: dist/orca-skills-0.9.0.tar.gz** |
| 5 | `python3 scripts/verify_package.py --archive "dist/orca-skills-0.9.0.tar.gz"` | **PASSED (165 source files); Verified archive** |
| 6 | `git diff --check` | clean, no output |

Against the PLAN baseline, which the task brief sets as a floor rather than a target:

```text
validator checks   501 -> 604   (+103)
tests             1269 -> 1326   (+57)   44 in test_decision_policy.py
                                         13 in test_validate_skills.py (DESIGN D4-E)
skipped               6 ->    6   unchanged; nothing was skipped to hide a failure
failures              0 ->    0
```

The `DeprecationWarning: invalid escape sequence '\s'` that PLAN recorded on the untouched baseline
is still present and still not attributable to this change.

### Files implemented

| kind | path | note |
|---|---|---|
| new | `scripts/decision_policy.py` | loader, `STATE_SELECTION_INPUTS` / `DECLARATIVE_KEYS` / `AXIS_TOKENS`, `permitted_states`, `validate_record`, `validate_transition` |
| new | `scripts/test_decision_policy.py` | 44 tests |
| new | `scripts/fixtures/decision_policy/**` | **46 JSON fixtures**; `valid/` holds exactly 18, one per reason code |
| edit | `orca-worker-reviewer-{orchestration,loop}/SKILL.md` | the `decision_policy` block (identical in both) + the `## Decision Policy` prose |
| edit | `…/templates/{analysis,plan,design,implementation,test,bugfix,refactoring}.md` × 2 | UD-1 optional section |
| edit | `…/reviews/common.md` × 2 | UD-1 section + Reviewer misclassification rules |
| edit | `scripts/validate_skills.py` | `validate_decision_policy_contract()` with C1-C14, registered in `main()` |
| edit | `scripts/test_validate_skills.py` | copy-tuple entry **+** 13 D4-E regression tests |
| edit | `CHANGELOG.md` | one Unreleased entry |

`git diff --stat c264e79 HEAD` → **69 files changed, 3015 insertions(+), 0 deletions**.

### Commits

Ordered so every commit leaves the tree green — content lands before the check that requires it.

```text
c19501d  contract data (both SKILL.md blocks) + loader + fixtures + loader tests
a54ed6b  UD-1 optional section in templates x7 x2 and reviews/common.md x2
9862f85  validator C1-C14  +  the test_validate_skills.py copy tuple   <-- SAME COMMIT, required
122003e  CHANGELOG
a7e9278  the 13 D4-E validator regression tests
```

`9862f85` bundles the validator's `from decision_policy import …` with adding `"decision_policy.py"`
to the copy tuple at `scripts/test_validate_skills.py`, as PLAN V9-V1 requires. Splitting them would
have produced a commit where all 137 validator regression tests fail on an import crash with empty
stdout — the failure that tuple's own OS-4 comment records.

### Two details DESIGN did not fix, resolved during implementation

Neither changes a DESIGN decision; both are mechanics DESIGN left open and the code forced.

**(a) `permitted_states` accepts two input shapes.** DESIGN D2-1 describes `facts` as a flat mapping
carrying `policy_source_role`, while `validate_record` consumes a record carrying the nested
`policy_source: {"role": …}`. Requirement 4 and 5's tests pass **one** object to **both** functions.
Rather than maintain two parallel fixture shapes that could drift apart, a helper resolves the role
from either shape. Found by running the test, not by reading: the first run failed with
`'ASSUMPTION_ALLOWED' not found in frozenset({'CLEAR', 'CONFLICT', 'NEEDS_INPUT'})`. Risk-independence
is untouched — neither shape can carry an axis value into the computation.

**(b) `parse_decision_policy` is public, alongside `load_decision_policy`.** DESIGN's API lists only
the path-taking loader, but requirement 9's tests must feed a malformed *object* without writing a
malformed SKILL.md. The split keeps `load_decision_policy` as the file entry point and makes the
object validator testable directly.

### Fail-closed, as required

`scripts/decision_policy.py` raises `DecisionPolicyError` on every one of D2-2's conditions and never
returns `None`. The module docstring carries the reason verbatim, so a later reviewer does not
"correct" it toward the wrong neighbour:

> This module deliberately does NOT copy `skill_policy.load_risk_contract`'s convention. That
> function returns `None` for a malformed block, and its caller (`evaluate_invocation`) reads `None`
> as "this Skill has no risk axis" — so a malformed block silently removes the axis at runtime. That
> is a fail-OPEN, and OS-28 validation requirement 9 requires the opposite.

Mutation **M-9** proves it: reverting the version check to a no-op is caught.

### Anti-vacuity guards (DESIGN D4-F)

All six loops carry a co-located cardinality guard, each marked `# D4-F guard` in the source, so the
guard cannot be deleted independently of the loop it protects:

| test | guard asserted before the loop |
|---|---|
| `test_forbidden_transitions_are_rejected` | the forbidden-cell set **equals** the two expected cells |
| `test_reason_code_is_required_for_each_non_clear_state` | exactly 3 states |
| `test_each_required_evidence_field_is_enforced` | per-state field counts are 5 / 4 / 3, **and** the executed case count is asserted after the loop |
| `test_each_high_impact_element_alone_forbids_assumption_allowed` | the list is exactly the five elements |
| `test_forbidden_authority_source_cannot_justify_a_transition` | the list is exactly the five entries |
| `test_every_reason_code_has_a_constructible_record` | exactly 18 codes |

### The 18-code liveness suite

`test_every_reason_code_has_a_constructible_record` asserts **C1** (the record's state matches, and
the clause the contract assigns exists in that state's `entry_clauses`), **C2** (every field of the
code's *effective* required-evidence set is present and non-empty), and **C3** (`validate_record`
accepts it) for all 18 codes. `test_every_reason_code_has_exactly_one_fixture` compares the fixture
directory against the code list **in both directions**, so a stale fixture fails too.

### User decisions, as implemented

| UD | requirement | how it is honoured, and what proves it |
|---|---|---|
| **UD-1** | the section is **optional** | the validator checks only that the optionality sentence is present and that a *present* record matches the contract. No check requires any artifact to contain the section. `fixtures/decision_policy/clear/absence_is_valid.json` is a record-free instance; `test_decision_record_optionality_sentence_removed_fails` (M-13) fails the validator if the sentence is turned into a requirement |
| **UD-2** | requirement 5 to **permission** level only | the test is named `test_a_safe_reversible_item_is_permitted_to_be_assumption_allowed`, and its docstring states in full that it cannot detect a real model's over-escalation and that this belongs to OS-32. Repeated in *Testing Strategy* below |
| **UD-3** | requirement 9 covers **this loader only** | `scripts/skill_policy.py` is **not modified** — `git diff --stat c264e79 HEAD -- scripts/skill_policy.py` is empty. `test_this_change_does_not_alter_evaluate_invocation_behaviour` pins that by asserting `skill_policy.py` still contains no schema-version gate. The pre-existing defect is neither fixed nor worsened, and is not claimed as addressed |
| **UD-4** | 18 codes, no `requirement_vs_repository_policy`, C-1/C-2/C-3 untouched | `grep -c requirement_vs_repository_policy` over both SKILL.md files → **0**. The contract carries exactly 18 codes (4/11/3), asserted by C5 and by `test_reason_code_count_is_eighteen` |

### Out-of-scope proofs (DESIGN D8) — run, with output

| # | proof | result |
|---|---|---|
| 1 | `grep -rn 'decision_policy' scripts/ orca-worker-reviewer-*/` | the only `.py` files are `decision_policy.py`, `test_decision_policy.py`, `validate_skills.py`, `test_validate_skills.py`. **No runtime module** — nothing executes the contract |
| 2 | `git diff --name-only c264e79 HEAD \| xargs grep -rniE 'waiting_for_input\|humanapprovalport\|durable pause\|resume from\|orchestration ask\|slack\|approval adapter'` | **no hits at all** |
| 3 | `git diff --stat c264e79 HEAD -- scripts/run_logging.py scripts/orca_runtime_harness.py scripts/review_isolation.py scripts/e2e_harness.py scripts/task_context.py` | **empty** — `RUN_STATUS_VALUES` untouched |
| 4 | `git diff --shortstat c264e79 HEAD` | 69 files, **3015 insertions, 0 deletions** — purely additive; no existing behaviour was rewritten |

---

## Changes

Implemented the OS-28 decision policy contract exactly as `DESIGN.md` specifies: the machine
contract in the shared JSON block of both Skills, the loader and evaluator, the validator checks, the
optional Result Contract section, the SKILL.md prose with named anchors, the test suite with the
18-code liveness assertions and D4-E regression tests, and 46 fixtures.

## Modified Files / Artifacts

See the *Files implemented* table above. Artifact written by this phase:
`artifacts/runs/run_3233a1469e97/IMPLEMENTATION.md`.

`git status --porcelain -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs CHANGELOG.md VERSION` is **empty** — everything is committed. `dist/` holds the build artifact and is
git-ignored. No file outside this ticket's scope was touched; the user's pre-existing untracked
`artifacts/` trees were not modified.

## Validation

### Mutation verification — executed, not predicted

Every mutation was applied to a disposable copy of the repository, then `validate_skills.py`,
`test_decision_policy` and `test_validate_skills` were run against it. **21 of 22 caught.**

| id | mutation | detector | verdict |
|---|---|---|---|
| M-1 | fifth state in both blocks | C11a / loader state check | **CAUGHT** |
| M-2 | delete a reason code from **one** Skill | deep-equality | **CAUGHT** |
| M-3 | delete a reason code from **both** Skills | `DECISION_POLICY_REASON_CODES` constant (C3/C5) | **CAUGHT** — re-verified surgically |
| M-4 | T-F2 relaxed to `allowed` | C8 | **CAUGHT** |
| M-5 | T-F2 made conditional | C8 | **CAUGHT** |
| M-6 | INV-4 exception for `monetary_cost` | C9 + C3 | **CAUGHT** |
| M-7 | `model_confidence` removed from the reject list | C10 | **CAUGHT** |
| M-8 | a `NEEDS_INPUT` evidence field made optional | evidence tests | **CAUGHT** |
| M-9 | loader returns `None` instead of raising (the fail-open) | requirement-9 tests | **CAUGHT** |
| M-10 | a risk level as a value in a selection input | C11b | **CAUGHT** |
| M-11 | a prose anchor deleted from **both** Skills | C12 | **CAUGHT** |
| M-12 | a reason code's fixture deleted, code kept | bidirectional fixture test | **CAUGHT** |
| M-13 | the optional section made required | C14 | **CAUGHT** |
| M-14 | one `templates/analysis.md` copy edited | byte-equality | **CAUGHT** |
| M-15 | `risk` as a twelfth boundary element | C11b | **CAUGHT** |
| M-16 | a risk gate added to INV-4 | C11b | **CAUGHT** |
| M-17 | risk-conditional transition value, **no exact token** | C11c | **CAUGHT** — re-verified surgically; **C11b misses it**, which is why C11c exists |
| M-18 | new top-level `risk_overrides` key | C11a | **CAUGHT** |
| M-19 | `independent_axes` trimmed | C11d | **CAUGHT** |
| M-20 | risk-conditional `workflow` value | C11c | **CAUGHT** |
| **M-21** | **risk smuggled into a PROSE entry clause** | — | **MISSED** — see below |
| M-22 | `classification_attempted` emptied (RD-N1) | per-code evidence override | **CAUGHT** |

**M-21, reported honestly.** My first harness said CAUGHT. That was **wrong**, and the reason
matters: the runner re-serialized the entire JSON block with `json.dumps(indent=2)`, reformatting it
and tripping unrelated checks. Re-applied as a surgical one-sentence edit to both Skills — changing
only `entry_clauses.NEEDS_INPUT["N-1"]` to *"a boundary element is true, unless the run risk is
low"* — the result is:

```text
python3 scripts/validate_skills.py      ->  Skill validation PASSED (604 checks), exit 0
python3 -m unittest scripts.test_decision_policy  ->  Ran 44 tests, OK
```

**Nothing catches it.** This is exactly the residual gap DESIGN records as F-5, and it is not
covered. Entry-clause prose cannot be closed-enumerated, and a sentence does not match an exact axis
token. What *would* catch it: a one-sided or accidental edit (deep-equality and byte-equality still
apply). What does not: a deliberate, coordinated edit of both Skills. The mitigation is a human
reading the diff — which is why `REVIEW_DESIGN.md`'s `RD-N2` asks the IMPLEMENTATION review to
inspect the entry-clause and `downstream_rule` prose diff directly. **That prose is unchanged from
DESIGN in this implementation; the Reviewer should confirm it rather than take my word.**

Two further mutations were re-verified surgically because they carry the design's headline claims:

```text
M-3 (surgical, one line deleted from each Skill, no reformatting)
  -> decision policy contract values drifted
     decision policy reason-code cardinality drifted (expected 18)
     decision policy per-state reason-code split drifted

M-17 (surgical, one transition cell value in each Skill)
  -> Skill validation FAILED (2 errors, 503 checks)
     transitions[NEEDS_INPUT][CLEAR]='requires_user_decision_unless_risk_low'
       is outside the closed set
```

### Defects found during implementation

| id | what | how it was found | resolution |
|---|---|---|---|
| **I-1** | `permitted_states` and `validate_record` disagreed about the `policy_source` shape | **running the test** — it failed with `'ASSUMPTION_ALLOWED' not found in frozenset(...)` | a documented helper accepting both shapes; see *Analysis (a)* |
| **I-2** | DESIGN D4-E's validator regression tests were absent from my first pass | re-reading DESIGN against the diff before reporting | 13 tests added in `a7e9278` |
| **I-3** | `test_decision_policy_axis_token_in_a_selection_input_fails` asserted the wrong failure message | **running it** — it failed, and the real message was *better* (C11b's specific one) than the generic one I had asserted | assertion corrected to the actual message |
| **I-4** | my mutation harness produced a false CAUGHT for M-21 | re-running it surgically because DESIGN said it should be MISSED | reported as MISSED; see above |

I-1, I-3 and I-4 were each found by executing rather than by reading. I-4 in particular would have
put a false claim in this report.

## Unit Tests / Testing Strategy

`scripts/test_decision_policy.py` — 44 tests over requirements 1-7 and 9, the 18-code liveness suite,
cross-Skill agreement, and UD-1's optionality. `scripts/test_validate_skills.py` — 13 new tests, one
per validator check, each asserting a **named** failure message.

Three limits are restated here because the user decisions require it, and because this repository's
recurring failure is a claim wider than its evidence:

- **(UD-2)** requirement 5 is proven to the **permission** level only. The contract *permits*
  `ASSUMPTION_ALLOWED` for a safe, reversible, scope-local item and does not *require*
  `NEEDS_INPUT` there. **A contract-level test cannot detect a real model's over-escalation**; that
  is OS-32's territory and is **not solved here**.
- **(UD-3)** the missing `schema_version` gate in the existing `evaluate_invocation()` is a
  **pre-existing** defect. It is untouched, neither fixed nor worsened, and remains a follow-up
  ticket candidate.
- **(UD-4)** the choice that no dedicated reason code covers a repository-policy conflict rests on
  the assumption that **the eleven boundary elements catch every policy class that matters**. That
  assumption was **not verified by enumerating policy classes** and is carried forward as an
  assumption, not a fact.

And the ticket-level limit: **nothing executes this contract.** D8 proof 1 is the mechanical form of
that statement. Running the check at a phase gate is OS-29, asking the question is OS-30, waiting for
the answer is OS-31 — none of which is implemented here.

## Correction — iteration 2 (Final Review FR-2)

Five phases had passed their gates when Final Adversarial Review raised two blocking findings. **FR-2
(Responsible Phase: implementation) is this correction.** FR-1 belongs to a separate round and was
not touched.

### The defect, reproduced before changing anything

`user_decision.source` was an **open-ended string minus five exact tokens**. Running
`validate_transition(policy, "NEEDS_INPUT", "CLEAR", record)` on the unmodified contract:

```text
rejected  'model_confidence'
ACCEPTED  'high_confidence'
ACCEPTED  'worker_reviewer_consensus'
ACCEPTED  'automated_default'
ACCEPTED  'anything_at_all'
```

Only the exact listed spelling was refused. `high_confidence` is the same *category* as
`model_confidence`; `worker_reviewer_consensus` is the same category as `worker_reviewer_agreement`.
**A denylist of spellings cannot enforce a categorical rule** — it admits every synonym nobody
enumerated, and the ticket's requirements ("model confidence is never authority", "Worker+Reviewer
agreement is not user approval", "a recommended default is not user approval") are categorical.
Expanding the list would have been chasing an infinite set, which the Final Reviewer explicitly
forbade.

### The fix — a closed positive vocabulary

```json
"user_decision_sources": ["explicit_user_reply", "prior_explicit_user_authorization"]
```

Not invented: these are the only two shapes ANALYSIS A4-0 identifies — an answer to a structured
question put during the run, and a standing authorization carried from the original request.
**Enforcement is membership; an unrecognised source is rejected rather than assumed valid.**

`forbidden_authority_sources` is **retained and demoted, and this document says so plainly**: it no
longer enforces anything. It names the five excluded categories where a reader meets them, and its
one remaining machine job is to stay **disjoint** from the allowlist — checked at load time in both
Skills and by validator check C25 — so a forbidden category can never be promoted into the positive
vocabulary. Enforcement belongs to the allowlist alone.

### Verification — both directions, executed

Over-blocking is the opposite defect; the ticket calls classifying everything `NEEDS_INPUT` a wrong
implementation too. So both directions were probed in one run, with the **same record shape** so the
only variable is the source string:

| direction | inputs | result |
|---|---|---|
| must reject | the three FR-2 aliases, an invented source, empty string, missing `source` key, missing `user_decision`, non-dict `user_decision`, all five denylist entries, `EXPLICIT_USER_REPLY`, `explicit_user_reply ` (trailing space), `user_reply`, `confidence`, `consensus`, `default` | **19 / 19 rejected** |
| must accept | `explicit_user_reply`, `prior_explicit_user_authorization`, on both the `NEEDS_INPUT → CLEAR` and `CONFLICT → CLEAR` edges | **2 / 2 accepted** |

That the two genuine sources pass with an otherwise identical record is the built-in control: the
check discriminates on the source, not on some earlier field.

**UD-1…UD-4 expressibility — the empirical width test.** Each of the four decisions this run actually
recorded is an answer to a structured question the Coordinator put to the repository owner, so each
is `explicit_user_reply` with a locator into `USER_DECISIONS.md`. All four validate on both edges;
`test_the_four_recorded_user_decisions_are_expressible` asserts it. A vocabulary that could not
express them would be too narrow, and an empty vocabulary now fails to load.

### Mutation check on the new enforcement

| id | mutation | verdict |
|---|---|---|
| A-1 | revert enforcement to the denylist — **the exact FR-2 defect** | **CAUGHT** (adversarial tests) |
| A-2 | widen the vocabulary with one permissive spelling | **CAUGHT** (C24 + tests) |
| A-3 | promote a forbidden category into the allowlist | **CAUGHT** (C25 + loader) |
| A-4 | empty the allowlist (over-blocking) | **CAUGHT** |
| A-5 | delete the disjointness check from the loader | **CAUGHT** |
| A-6 | delete the source check entirely | **CAUGHT** (adversarial tests) |

Six of six, and each by a *different* detector combination — A-1 and A-6 only by the decision-policy
tests, A-2/A-3/A-4 by all three, A-5 by two. That variety is why I read these as real signals rather
than one blunt detector firing: the harness aborts if `test_validate_skills` fails to import, which
is the false-CAUGHT shape this run has been burned by twice.

### Commands after the correction

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (626 checks)** — was 622 |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1349 tests in 294.837s — OK (skipped=6)** — was 1337 |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** — was 165; +8 new fixtures |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `python3 scripts/verify_package.py --archive …` | **PASSED (173 source files); Verified archive** |
| `git diff --check` | no output |

Skips unchanged at 6. `git diff --stat c264e79 HEAD -- VERSION LICENSE .orca scripts/skill_policy.py
scripts/quality_profile.py scripts/agent_profile.py scripts/workflow_contract.py
scripts/run_logging.py` is **empty**, so UD-3 and the protected surfaces are intact.

### DESIGN.md updated in the same round

Documentation drifting from code is the next finding, so six sections were updated: D1-1 (the JSON
block), D1-2 (the key table, recording the demotion), D2-2 (the fail-closed table), D3 (checks C24
and C25), D3-2 (the partition listing gains the new selection input), and D4-F (two new guarded
loops). New section **D2-2a** states the finding, the fix, why a denylist cannot enforce a category,
and what the change still does not prove.

### What this does not prove

Membership in the vocabulary is still **a claim the Worker writes**. This change makes an unknown or
aliased source impossible to pass off as authority; it does **not** establish that a human actually
replied. That is OS-30's clarification protocol and OS-31's durable record, both out of scope. The
`test_alias_and_unknown_sources_are_rejected` docstring says so.

Commits: `699a6ed` (contract, loader, validator, tests, fixtures), `b912655` (DESIGN). Not pushed.

## Review Feedback Resolution

Iteration 1 of IMPLEMENTATION. No prior IMPLEMENTATION Reviewer findings.

Carried in from the DESIGN review (`REVIEW_DESIGN.md`, `RESULT: PASS`):

```text
FINDING RD-N1: RESOLVED   classification_attempted missing/empty is mutation M-22 -- CAUGHT
FINDING RD-N2: ADDRESSED  the residual prose gap is re-measured and reported as MISSED, with
                          the entry-clause prose diff flagged for the Reviewer to inspect
```

No new question requires user authority. UD-1 through UD-4 answered everything this phase needed,
and the two implementation details in *Analysis* are mechanics rather than authority choices.

STATUS: COMPLETE
