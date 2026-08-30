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

## Correction — iteration 3 (Final Review attempt 2: FR-4 and FR-3)

Both findings are Responsible Phase: implementation. FR-1 and FR-2 from attempt 1 are intact and were
not touched — `user_decision_sources` is still in both Skills and the FR-1 matrix constants are still
in the validator.

### FR-4 (CRITICAL) — high-impact, unauthorized items were permitted `CLEAR`

**Reproduced first.** `permitted_states()` fixed its result to `{CLEAR, NEEDS_INPUT, CONFLICT}` and
computed only whether to add `ASSUMPTION_ALLOWED`:

```text
facts = {"reversibility": "irreversible", "blast_radius": "external_system", "security": True}
        no policy source, no authorization
  ->  ['CLEAR', 'CONFLICT', 'NEEDS_INPUT']        CLEAR permitted with no authority
```

That is automatic approval of an irreversible high-impact decision without explicit authority — the
thing the ticket forbids outright. The old requirement-4 test asserted only the **absence of
`ASSUMPTION_ALLOWED`**, which is a strictly narrower property than "this item is safe", so 638/1360
were all green over a live defect.

**The entry-condition contract data.** A3-1's approved wording, transcribed rather than redesigned:

```json
"entry_conditions": {
  "CLEAR": {"any_of": ["no_open_decision_item", "determining_policy_source", "explicit_user_authorization"]},
  "ASSUMPTION_ALLOWED": {"all_of": ["reversible_in_run", "blast_radius_within_scope",
                                    "no_high_impact_element", "supporting_policy_source",
                                    "no_reserved_user_authority"]},
  "NEEDS_INPUT": {"any_of": ["undetermined_boundary_element", "absent_user_intent", "unclassifiable_item"]},
  "CONFLICT": {"any_of": ["declared_contradiction"]}
}
```

Predicate names are a **closed twelve-entry vocabulary** (`ENTRY_PREDICATES`), so a typo fails at
load instead of silently making a condition unsatisfiable. Each boundary element also gains a
`triggering` value — which of its values make it true in A3-1's sense. Those come from A4-1, not from
me: `irreversible`; blast radius in `{repository, external_system}`; the five booleans `true`;
authority `reserved`; `null` for `repository_project_policy`, which A4-0 classifies as a boundary
*input* rather than a trigger.

**`permitted_states()` before and after, same probe:**

| facts | before | after |
|---|---|---|
| irreversible + external_system + security, no authority | `['CLEAR', 'CONFLICT', 'NEEDS_INPUT']` | **`['NEEDS_INPUT']`** |
| …+ determining policy source | — | `['CLEAR']` |
| …+ allowlisted authorization | — | `['CLEAR']` |
| …+ **forbidden** authorization (`model_confidence`) | — | **`['NEEDS_INPUT']`** — FR-2's allowlist gates this route too |
| safe item, supporting policy source | `['CLEAR', 'ASSUMPTION_ALLOWED', 'CONFLICT', 'NEEDS_INPUT']` | **`['ASSUMPTION_ALLOWED']`** |
| nothing open | — | `['CLEAR']` |
| declared contradiction | — | `['CONFLICT']` |

One test assertion had to change, and it was **wrong rather than merely outdated**:
`test_the_contract_does_not_require_needs_input_for_a_safe_item` asserted `CLEAR in permitted` for a
safe item with a *supporting* policy source. A3-1 admits `CLEAR` only when nothing is open, a policy
source **determines** the choice, or an authorization decides it — none of which holds for an item
whose policy source merely supports. It now asserts the substantive UD-2 property: `ASSUMPTION_ALLOWED`
is permitted and `NEEDS_INPUT` is not forced.

### FR-3 — reason code and boundary element could disagree

**Reproduced first:** the shipped fixture `valid/security_impact.json` with only its
`boundary_element` changed to `privacy` **passed** `validate_record()`. `validate_record` checked
that the effective evidence field was non-empty, never that it matched the element the code binds,
so misclassification — the thing a Reviewer is required to be able to judge — was not
machine-checkable. `ReasonCodeLiveness` compared the two values in *test* code, which proves the
fixture is self-consistent, not that production rejects an inconsistent record.

**Enforcement:** `validate_record()` now requires **exact equality** between the record's
`boundary_element` and the element the reason code binds. `unclassifiable_decision`'s deliberate
absence of a bound element is kept as a **separate positive control** that also rejects smuggling one
in.

**Negative test scope:** `test_every_bound_code_rejects_a_mismatched_boundary_element` injects a
mismatch into **each** of the 10 boundary-bound codes, with a **co-located** guard asserting exactly
10 (the eleventh `NEEDS_INPUT` code, `unclassifiable_decision`, deliberately binds none) — the D4-F
rule. `test_every_bound_code_accepts_its_declared_element` is the paired positive control, so the
check cannot be satisfied by rejecting everything.

### The two-axis sweep — three more found

FR-4's shape is *"forbids but never permits"*; FR-3's is *"checks presence but never consistency"*. I
swept both axes and probed each candidate rather than reading:

| axis | field | probe result before | now |
|---|---|---|---|
| (b) consistency | `reversibility` | accepted `sort_of_reversible`, outside its own enum | **rejected** |
| (b) consistency | `blast_radius` | accepted `the_whole_internet` | **rejected** |
| (b) consistency | `policy_source.kind` | accepted `model_hunch`, outside `policy_source_kinds` | **rejected** |
| (b) consistency | `reason_code` vs state | already rejected | unchanged |
| (b) consistency | CONFLICT record clause | records carry no `clause` field; the code→clause binding is validated at load | not a gap |
| (a) permit-side | `CONFLICT` | was in the fixed starting set, so "permitted" for every input | now requires a declared contradiction |

The enum gap was not a trigger escape but something subtler and still wrong: an unrecognised value
matched no triggering value, so `permitted_states` returned an **empty set** — degenerate rather than
fail-closed. Declared values are now checked for membership; **omitting** an element stays legal, so
this does not over-block.

### Over-blocking guard — legitimate states are still reachable

Every negative check has a positive control beside it, and each is asserted in the suite:

```text
CLEAR              reachable via nothing-open, a determining policy source, and each of the
                   two allowlisted authorization sources
ASSUMPTION_ALLOWED reachable for a safe, reversible, scope-local item with a supporting source
CONFLICT           reachable for each of the three declared clauses C-1/C-2/C-3
enum values        all 7 declared enum members accepted; all 4 policy_source kinds accepted
```

### Mutation verification — 8/8 caught, control green

| id | mutation | verdict |
|---|---|---|
| F-0 | *(control)* no mutation | **green**, as required |
| F-1 | `CLEAR` condition widened | CAUGHT |
| F-2 | `ASSUMPTION_ALLOWED` loses `no_high_impact_element` | CAUGHT |
| F-3 | `NEEDS_INPUT` loses `undetermined_boundary_element` | CAUGHT |
| F-4 | `irreversible` no longer triggering | CAUGHT |
| F-5 | `security` made non-triggering | CAUGHT |
| F-6 | FR-3 equality check disabled | CAUGHT |
| F-7 | `permitted_states` reverted to a fixed starting set — **the FR-4 defect itself** | CAUGHT |
| F-8 | declared-facts consistency check disabled | CAUGHT |

F-6, F-7 and F-8 are caught **only by the loader tests** (validator passes), while F-1/F-2 are caught
by the validator and F-3/F-4/F-5 by all three. That variety is why I read these as real detections
rather than one blunt detector firing — this run has produced three harness false positives, so the
harness does literal substitution only, aborts unless its target appears exactly once, and aborts
rather than scoring an `ImportError` as detection.

### Commands after the correction

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (640 checks)** — was 638 |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1384 tests in 302.894s — OK (skipped=6)** — was 1360 |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `python3 scripts/verify_package.py --archive …` | **PASSED (173 source files); Verified archive** |
| `git diff --check` | no output |

Skips unchanged at 6. `git diff --stat c264e79 HEAD -- VERSION LICENSE .orca scripts/skill_policy.py
scripts/quality_profile.py scripts/agent_profile.py scripts/workflow_contract.py
scripts/run_logging.py` is **empty**, so UD-3 and the protected surfaces hold.

### DESIGN.md updated in the same round

D1-1 gains `entry_conditions` and the per-element `triggering` values; D3 gains C30 and records C27's
extension; the state-selection partition gains the new key; new section **D2-2b** states both
findings, the before/after probe output, the two-axis sweep, and the positive controls.

Commits: `fa3a935` (contract, loader, validator, tests), `8f9c80c` (DESIGN). Not pushed.

## Correction — iteration 4 (RI3-1: authority precedence)

FR-3 was confirmed RESOLVED and is untouched. FR-1, FR-2 and FR-3 are all verified intact.

### The defect — predicates evaluated independently

Each predicate was **correct alone and wrong in combination**. Reproduced before any change:

```text
reserved alone                -> ['NEEDS_INPUT']           correct
reserved + determining policy -> ['CLEAR']                 WRONG, expected ['NEEDS_INPUT']
C-1 alone                     -> ['CONFLICT']              correct
C-1 + determining policy      -> ['CLEAR', 'CONFLICT']     WRONG, expected ['CONFLICT']
```

### Where precedence went, and how

ANALYSIS A4-0 already names exactly two cells a determining policy source cannot resolve — *"a
policy source cannot un-reserve it → `NEEDS_INPUT`"* and *"a policy source cannot arbitrate two
explicit requirements → `CONFLICT`"*. Transcribed, not redesigned, into contract data:

```json
"authority_precedence": {"policy_source_cannot_resolve": ["explicit_user_authority", "explicit_requirement_conflict"]}
```

Both names are existing `boundary_elements` entries, so the loader rejects an unknown one. The
evaluator applies the rule in **one** place, and `validate_skills.py` pins the list by value (C31).

**The rule had to be stated on both sides, and finding that was the real work.** Applying it only to
`determining_policy_source` removed `CLEAR` correctly but left `undetermined_boundary_element` still
treating a determining source as resolving the item — so the reserved case returned an **empty set**.
That is the same "right alone, wrong together" shape RI3-1 reported, one predicate over, and my own
expected-value check caught it. Two further refinements followed from the same reading:

- `no_open_decision_item` now follows A3-1's actual wording — a triggering element or a declared
  contradiction **is** an open decision item, so asserting `open_decision_item: false` beside one of
  them is self-contradictory and must not reach `CLEAR`.
- `declared_contradiction` yields to a valid user decision, symmetric with
  `undetermined_boundary_element`, because A4-0 gives **one** destination per cell rather than
  leaving the unresolved state simultaneously permitted.

### Before / after

| facts | before | after |
|---|---|---|
| reserved + determining policy | `['CLEAR']` | **`['NEEDS_INPUT']`** |
| C-1 + determining policy | `['CLEAR','CONFLICT']` | **`['CONFLICT']`** |
| C-2 / C-3 + determining policy | `['CLEAR','CONFLICT']` | **`['CONFLICT']`** |
| C-1/2/3 + allowlisted authorization | `['CLEAR','CONFLICT']` | **`['CLEAR']`** |
| open=false + triggered element | `['CLEAR','NEEDS_INPUT']` | **`['NEEDS_INPUT']`** |
| open=false + contradiction | `['CLEAR','CONFLICT']` | **`['CONFLICT']`** |

### Combination sweep — what I ran and what I found

Every triggering element × every CONFLICT clause × every pair of the two × five resolver states
(none / determining / supporting / allowlisted / forbidden) = **105 combinations**, executed.

The brief's specific list, all now matching A4-0: reserved + supporting → `NEEDS_INPUT`; reserved +
allowlisted → `CLEAR`; reserved + forbidden → `NEEDS_INPUT`; each of C-1/C-2/C-3 + determining →
`CONFLICT`, + allowlisted → `CLEAR`; high-impact + determining → `CLEAR` (A4-0's row for
monetary/security/privacy/compliance/lock-in, and for irreversible, is `→ CLEAR` with a determining
source, so this is correct rather than a leak); `open_decision_item: false` + anything open → the
open item's state.

**A result I first mis-scored, and the correction.** The sweep flagged **36 mismatches**, all one
case: a triggered element *and* a contradiction together yield `{NEEDS_INPUT, CONFLICT}` while my
expectation said `{CONFLICT}`. Investigating rather than "fixing" it: **my expectation was wrong.**
A3-1 makes `NEEDS_INPUT` *missing* information and `CONFLICT` *contradictory* information — different
decision items — and OQ-1 settled that state is per **item** with a per-check aggregate, so
`aggregate_order` (`CONFLICT` first) reduces a multi-item check to one reported state. Both are
pausing states, so nothing is weakened. Re-run with the corrected expectation: **0 mismatches.**

The safety-relevant invariant across all 105: **0 leaks** — no combination permits a continuing state
without a valid resolver. It is now a permanent test,
`test_no_combination_permits_a_continuing_state_without_a_resolver`, with a co-located cardinality
guard.

### Positive controls — the anti-over-blocking half

| control | result |
|---|---|
| determining policy resolves all **7** ordinary elements (security, privacy, compliance, monetary_cost, long_term_lock_in, ambiguity, reversibility, blast_radius) | `['CLEAR']` each |
| allowlisted authorization resolves **both** precedence cells and all three clauses | `['CLEAR']` each |
| `open_decision_item: false` alone | `['CLEAR']` |
| safe supporting-policy item | `['ASSUMPTION_ALLOWED']` |
| a declared item always permits **something** | asserted for all 105 |

The 7-element control carries a D4-F guard asserting it equals *every triggering element minus the
two A4-0 excludes*, so the control cannot silently shrink.

### Empty facts — judged, and now documented

`permitted_states(policy, {})` returns `frozenset()`. **Deliberate fail-closed behaviour, not an
oversight.** A3-1 admits `CLEAR` on three grounds and the first is *affirmative* — "no decision item
is open". A caller that declared nothing has not asserted that; it asserted nothing. Returning
`CLEAR` for silence would make **the absence of analysis indistinguishable from a clean result**,
which is exactly what this contract exists to prevent, and it is the same reasoning that makes the
loader raise rather than return `None`. Reaching `CLEAR` requires stating `open_decision_item: false`
— a claim someone can be held to. Recorded in DESIGN **D2-2c** and pinned by
`test_undeclared_facts_permit_nothing_by_design`.

### Mutation verification — 7/7 caught, control green

| id | mutation | verdict |
|---|---|---|
| R-0 | *(control)* none | **green** |
| R-1 | precedence list emptied — **restores the RI3-1 defect** | CAUGHT |
| R-2 | reserved-authority cell dropped | CAUGHT |
| R-3 | conflict cell dropped | CAUGHT |
| R-4 | precedence bar removed from `determining_policy_source` | CAUGHT |
| R-5 | mirror rule removed from `undetermined_boundary_element` | CAUGHT |
| R-6 | `no_open_decision_item` refinement removed | CAUGHT |
| R-7 | `declared_contradiction` ignores a user decision | CAUGHT |

R-4 through R-7 are caught **only by the loader tests** (validator passes); R-1/R-2/R-3 by all three.
That split is why I read these as real detections. The harness does literal substitution only, aborts
unless its target appears exactly once, and aborts rather than scoring an `ImportError` as detection.

### Commands after the correction

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (642 checks)** — was 640 |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1399 tests in 302.533s — OK (skipped=6)** — was 1384 |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `python3 scripts/verify_package.py --archive …` | **PASSED (173 source files); Verified archive** |
| `git diff --check` | no output |

Skips unchanged at 6. Protected-surface diff is **empty**. FR-1's matrix constants, FR-2's
`user_decision_sources`, and FR-3's code-element equality are all still present.

### DESIGN.md updated in the same round

D1-1 gains `authority_precedence`; D3 gains C31; the state-selection partition gains the new key; new
section **D2-2c** records the finding, the A4-0 transcription, the both-sides requirement, the
105-combination sweep including the 36 I mis-scored and why, the positive controls, and the
empty-facts reasoning.

Commits: `ca42872` (contract, loader, validator, tests), `8cc5484` (DESIGN). Not pushed.

## Correction — iteration 5 (Final Review attempt 3: FR-5)

Scope held to FR-5. FR-1, FR-2, FR-3, FR-4 and RI3-1 are untouched and verified intact.

### The defect — two APIs, opposite answers, same evidence

`permitted_states()` and `validate_transition()` both decide whether a `user_decision` is evidence of
user authority, and they decided it differently. Reproduced before any change:

```text
facts = {"explicit_user_authority": "reserved", "security": true,
         "user_decision": {"source": "explicit_user_reply"}}

permitted_states()    -> ['CLEAR']
validate_transition() -> rejected: user_decision requires a non-empty 'where_recorded'
```

The permissive one is the one that gates the high-impact path. A bare **category claim** bought
`CLEAR` for a reserved-authority, security-relevant item without showing where the answer is recorded
or what it resolves.

### The unified helper

```text
_user_decision_defect(policy, decision) -> str | None
    returns the reason the decision is NOT valid evidence, or None when it is

    the decision is a non-empty mapping
    every field in policy.user_decision_fields is present and non-empty
    source is a member of policy.user_decision_sources        (FR-2's allowlist, moved in)

    read by:  _evaluate_predicate("explicit_user_authorization")  -> `defect is None`
              validate_transition()                               -> raises with `defect`
```

Returning the *reason* rather than a boolean keeps `validate_transition`'s per-field diagnostics
while giving the rule one home. A5-3 and INV-5 require the whole record because a source name is an
assertion that a user decided; `where_recorded` and `resolves` are what make it checkable.

### Before / after, both APIs

| | before | after |
|---|---|---|
| `permitted_states` on the FR-5 facts | `['CLEAR']` | **`['NEEDS_INPUT']`** |
| `validate_transition` on the same facts | rejected | rejected *(unchanged)* |

Parity across every field omission, executed:

```text
drop 'source'          no CLEAR | rejected  AGREE
drop 'where_recorded'  no CLEAR | rejected  AGREE     (was: CLEAR | rejected -- DISAGREE)
drop 'resolves'        no CLEAR | rejected  AGREE     (was: CLEAR | rejected -- DISAGREE)
complete record        CLEAR    | accepted  AGREE
```

### What the parity test asserts

`test_the_two_apis_agree_on_every_field_omission` and `test_the_two_apis_agree_on_every_source`
assert that `permitted_states()` and `validate_transition()` reach the **same** verdict for every
declared field's omission and every source (2 allowlisted + 5 forbidden + 1 invented), **and** that
the verdict is *correct* rather than merely consistent — two APIs agreeing on a wrong answer would
otherwise pass. Both carry co-located cardinality guards (3 fields, 8 sources). This pair is the
recurrence guard for the defect class.

### Negative sweep and its positive control

| | scope | guard |
|---|---|---|
| negative | 5 situations requiring authority (reserved, C-1, C-2, C-3, high-impact) × 4 incomplete records (source-only + one per dropped field) = **20** assertions that `CLEAR` is not permitted | `assertEqual(len(situations), 1 + len(clauses) + 1)`, `assertEqual(len(fields), 3)`, `assertEqual(len(incomplete), 4)`, `assertEqual(checked, 5 * 4)` — all co-located |
| positive | the same 5 situations × both genuine sources with **complete** records, each required to equal exactly `{CLEAR}` = **10** assertions | `assertEqual((len(situations), len(sources)), (5, 2))`, `assertEqual(checked, 5 * 2)` |

Refusing everything cannot satisfy the pair.

### The two tests that were pinning the defect

`test_an_allowlisted_authorization_permits_clear` built a **source-only** record, so it asserted that
a category claim *is* authority — the test was holding the defect in place. Its sibling
`test_a_forbidden_authority_source_does_not_permit_clear` did the same and would have passed for the
wrong reason: with a source-only record it fails on the missing fields, not on the source. Both now
use a complete record via a shared `complete_decision()` builder, whose docstring says that anything
asserting a decision is *valid* must use it.

### Concept comparison — run, not assumed

FR-5's shape is "the same concept judged in two places". I compared the other concepts both APIs touch:

| concept | result |
|---|---|
| `policy_source` validity — kind membership, role membership, missing `locator`, missing `kind` | **agree on all four** |
| `reason_code` vs. state | **not a parity gap** — `permitted_states` takes *facts* and has no reason-code notion, so this is outside its contract, not a divergent judgement |
| code ↔ `boundary_element` binding | **not a parity gap** — same domain difference |

Authorization was the only genuine divergence. I did not widen scope beyond it.

### Mutation verification — control green first, then 5/5 caught

The control was confirmed green **before** running any mutation, since this run has produced three
harness false positives and one red-control run whose results were void.

```text
M-0  control, no mutation                                     green
M-1  revert the predicate to source-only (the FR-5 defect)     CAUGHT
M-2  helper stops checking required fields                     CAUGHT
M-3  helper stops checking the source allowlist                CAUGHT
M-4  helper accepts a non-dict decision                        CAUGHT
M-5  user_decision_fields trimmed in the contract              CAUGHT
```

M-1 through M-4 are caught **only by the loader tests** (validator passes); M-5 by all three. That
split is consistent with where each rule lives and is why I read these as real detections.

### Commands after the correction

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (642 checks)** — unchanged |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1408 tests in 308.840s — OK (skipped=6)** — was 1404 |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `python3 scripts/verify_package.py --archive …` | **PASSED (173 source files); Verified archive** |
| `git diff --check` | no output |

Check count is unchanged at 642 because the fix is in the loader and the tests, not a new validator
check. Skips unchanged at 6. Protected-surface diff empty.

### DESIGN.md

New section **D2-2d**: the finding, the shared helper and why it returns a reason rather than a
boolean, parity as a stated requirement, the two tests that were pinning the defect, and the concept
comparison.

Commits: `8dd899c` (helper, both call sites, corrected controls, parity and sweep tests), `0a55c83`
(DESIGN). Not pushed.

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
