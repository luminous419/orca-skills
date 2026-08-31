# Worker Result

STATUS: COMPLETE

Phase: TEST · Iteration 1 · Run `run_3233a1469e97` · risk `high`
Ticket: **Jira OS-28 "Define Bounded Autonomy Decision Policy Contract"** (P0/High)
Branch: `feat/os-28-bounded-autonomy-policy` — 7 commits, **not pushed**.

Purpose of this phase, as the brief states it: not to write more tests, but to establish **what the
tests actually guard**. Specifically, to prove defect type (2) — *green but guarding nothing* — is
not still present.

---

## Summary

**It was still present. Five contract-weakening mutations passed every check, and one of them
defeated bounded autonomy outright.**

`NEEDS_INPUT`'s workflow could be changed from `pause_and_ask` to `continue` and the whole suite
stayed green: `"continue"` is a legal member of the closed workflow set, so the closed-value check
was satisfied while the state that must pause declared that it continues. Four more of the same
shape followed. All five are now closed by checks C15-C23 and ten regression tests, and re-running
every mutation against the strengthened checks catches all fourteen.

**I also caught two false CAUGHT results in my own harness**, which matters because IMPLEMENTATION
was burned by exactly that and the brief says to suspect a clean sweep first:

1. The first run reported **22/22 caught, including M-21** — which DESIGN records as a known gap.
   The `val_tests` column was scoring an **ImportError** as a failure: `test_validate_skills.py`
   does `import run_logging`, so invoking it as `scripts.test_validate_skills` from the repo root
   always errors. Every row's `val` column was noise. Fixed to run from `scripts/`, with an abort if
   the module fails to import. Re-run: **21/22, M-21 MISSED** — IMPLEMENTATION's figure confirmed.
2. The three-file coordinated M-21 variant first showed `val=FAIL`. That was **my own new
   regression test failing because its mutation target string no longer existed** in the
   already-mutated tree — not detection. It is reported as MISSED.

Final numbers, each from the command beside it: **622 validator checks** (was 604), **1337 tests OK,
skipped=6** (was 1326, skips unchanged).

---

## Analysis

### Commands run — actual output

| # | command | result |
|---|---|---|
| 1 | `python3 scripts/validate_skills.py` | **PASSED (622 checks)** |
| 2 | `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1337 tests in 295.460s — OK (skipped=6)**, exit 0 |
| 3 | `python3 scripts/verify_package.py` | **PASSED (165 source files)** |
| 4 | `python3 scripts/build_release.py` | **Built dist/orca-skills-0.9.0.tar.gz** |
| 5 | `python3 scripts/verify_package.py --archive dist/orca-skills-0.9.0.tar.gz` | **PASSED (165 source files); Verified archive** |
| 6 | `git diff --check` | no output |

```text
checks    604 -> 622   (+18: C15-C23 x 2 Skills)
tests    1326 -> 1337  (+11: 1 UD-3 characterization + 10 regression)
skipped     6 ->  6    unchanged -- nothing skipped to hide a failure
failures    0 ->  0
```

---

### T1. Does each requirement actually get proven?

For every requirement the question asked is **"can this test pass while the requirement is
violated?"** Where the answer was yes, the mutation is named and the test was strengthened.

| # | requirement | test(s) | can it pass while violated? |
|---|---|---|---|
| 1 | reject values outside the four states | `test_state_outside_the_four_is_rejected`, `test_a_fifth_state_in_the_contract_is_rejected`, `test_contract_states_are_exactly_the_four` | **No.** M-1 (fifth state in both blocks) fails the loader and the validator. The set equality is against a literal, so adding a state cannot pass |
| 2 | reject invalid transitions | `test_forbidden_transitions_are_rejected` + 6 named-edge tests | **No.** The loop's co-located guard asserts the forbidden set **equals** the two expected cells, so emptying it fails before the loop (T4, verified). M-4 and M-5 both caught. T-F2 is additionally tested **with a valid `user_decision` present** |
| 3 | no reason-less use of the three states | `test_reason_code_is_required_for_each_non_clear_state`, `..._outside_the_closed_set_...`, `test_each_required_evidence_field_is_enforced`, + the 18-code liveness suite | **It could, and now cannot.** N-2 (`CONFLICT.reason_code_required` → false) was caught by the dp tests, but the *contract-level* flip was unpinned; C15 now pins all three flags. M-8 and N-7 caught |
| 4 | high-impact/irreversible not weakened to auto-proceed | `test_high_impact_irreversible_cannot_be_assumption_allowed`, `..._not_lifted_by_a_determining_policy_source`, `..._not_lifted_by_a_user_decision`, `test_each_high_impact_element_alone_...` | **It could, and now cannot.** **N-5 emptied `blast_radius_in_with_irreversible` and everything stayed green.** C17 pins the whole `assumption_allowed_forbidden_when` object by value. Also proven non-constant: the safe fixture yields `{CLEAR, ASSUMPTION_ALLOWED, NEEDS_INPUT, CONFLICT}` and the high-impact fixture `{CLEAR, NEEDS_INPUT, CONFLICT}` — **different results, so requirements 4 and 5 cannot both be satisfied by a constant-returning implementation** |
| 5 | safe/reversible not forced to `NEEDS_INPUT` | `test_a_safe_reversible_item_is_permitted_to_be_assumption_allowed`, `test_the_contract_does_not_require_needs_input_for_a_safe_item` | **No, at the permission level UD-2 fixes.** The assertion is falsifiable — verified by executing `permitted_states` on the safe fixture and confirming `ASSUMPTION_ALLOWED` is genuinely present, not vacuously so. **UD-2 limit, restated: this cannot detect a real model's over-escalation. That is OS-32 and is not claimed** |
| 6 | model confidence is never authority | `test_forbidden_authority_source_cannot_justify_a_transition` (5 subtests), `test_forbidden_authority_list_is_exactly_five_entries` | **No.** The loop's guard asserts the list equals the five entries; emptying it fails at the guard (T4, verified). M-7 and N-6 caught |
| 7 | risk change does not change authority | 7.1-7.5 in `Requirement7RiskIndependence` | **No.** 7.5 varies the actual `facts` mapping across four calls and compares each against the no-`risk`-key baseline. M-10, M-15…M-20 all caught. Note 7.3's closed-value check is what catches M-17, which carries no exact axis token — and N-1 proved closed-set membership alone is insufficient, hence C15 |
| 8 | two-Skill contract drift fails | deep-equality, byte-equality, expected constant, prose anchors | **No.** M-2 (one Skill) caught by deep-equality; M-3 (both Skills — the blind spot deep-equality cannot see) caught by the expected constant; M-11 (prose deleted from both) by the anchors; M-14 (one template copy) by byte-equality |
| 9 | malformed/unknown schema fail-closed | 4 fail-closed tests + the new UD-3 characterization test | **No.** M-9 (loader stops raising) caught. **UD-3 scope holds:** `evaluate_invocation()` is untouched, and the new test pins its *existing* accepting behaviour rather than asserting a gate |
| 10 | no lifecycle/package regression | commands 1-6, plus T5 below | **No.** `git diff --stat c264e79 HEAD` over `run_logging.py`, `orca_runtime_harness.py`, `review_isolation.py`, `e2e_harness.py`, `task_context.py`, `skill_policy.py`, `quality_profile.py`, `agent_profile.py`, `VERSION` is **empty** |

**Three requirements — 3, 4 and 7's neighbourhood — could be violated while their tests passed.**
All three shared one root cause, given in T2 below.

---

### T2. Mutation results — all executed surgically

The harness does **literal text substitution only**. It never parses or re-serializes JSON, and a
mutation whose target is not found exactly once aborts as MALFORMED rather than reporting a result
from a repo it did not change. That is the direct fix for the re-serialization artifact that gave
IMPLEMENTATION a false CAUGHT.

#### The 22 from IMPLEMENTATION, re-run — **21 CAUGHT, 1 MISSED**

| id | mutation | verdict |
|---|---|---|
| M-1 | fifth state added to both blocks | CAUGHT |
| M-2 | reason code deleted from **one** Skill | CAUGHT |
| M-3 | reason code deleted from **both** Skills | CAUGHT |
| M-4 | T-F2 relaxed to `allowed` | CAUGHT |
| M-5 | T-F2 made conditional | CAUGHT |
| M-6 | INV-4 exception enabled | CAUGHT |
| M-7 | `model_confidence` dropped from the reject list | CAUGHT |
| M-8 | a `NEEDS_INPUT` evidence field made optional | CAUGHT |
| M-9 | loader stops raising on an unknown version | CAUGHT |
| M-10 | risk level as a value in a selection input | CAUGHT |
| M-11 | prose anchor deleted from both Skills | CAUGHT |
| M-12 | a code's fixture deleted, code kept | CAUGHT |
| M-13 | the optional section made required | CAUGHT |
| M-14 | one `templates/analysis.md` copy edited | CAUGHT |
| M-15…M-20 | six risk-injection shapes | all CAUGHT |
| **M-21** | **risk in a prose entry clause, both Skills** | **now CAUGHT** — see below |
| M-22 | `classification_attempted` emptied | CAUGHT |

**M-21's status changed during this phase, in both directions.** First run: falsely CAUGHT (the
`val_tests` ImportError). Corrected harness: **MISSED**, confirming IMPLEMENTATION. Then C22 pinned
the entry-clause text by value, and the two-file variant became genuinely CAUGHT:

```text
M-21  two-file (both Skills, expected constant untouched)
  -> Skill validation FAILED (2 errors, 622 checks)
     - orca-worker-reviewer-loop: entry clause text drifted
     - orca-worker-reviewer-orchestration: entry clause text drifted

M-21b three-file coordinated (both Skills AND the expected constant)
  -> validator PASSED (622 checks); decision-policy tests OK   ->  MISSED
```

**M-21b remains MISSED and is not claimed otherwise.** Its first `val=FAIL` was my own new
regression test failing because its mutation target string no longer existed in the mutated tree —
an artifact, not detection, verified by reading the failure. This is exactly the residual gap
DESIGN F-5 records: a coordinated edit of both Skills and the expected constant passes every static
check, and only human review of the diff catches it. What changed is the cost: two files no longer
suffice, three are required.

#### 14 new mutations devised for this phase — **5 initially MISSED**

All target the contract in the weakening direction the brief names.

| id | mutation | before C15-C23 | after |
|---|---|---|---|
| **N-1** | **`NEEDS_INPUT` workflow `pause_and_ask` → `continue`** | **MISSED** | CAUGHT (C15) |
| N-2 | `CONFLICT` stops requiring a reason code | CAUGHT | CAUGHT |
| N-3 | `citation_minimum` 2 → 1 | CAUGHT (validator tests only) | CAUGHT (C20) |
| N-4 | INV-3 accepts a **determining** policy source | CAUGHT | CAUGHT |
| **N-5** | **`blast_radius_in_with_irreversible` emptied** | **MISSED** | CAUGHT (C17) |
| N-6 | `user_decision_fields` trimmed to `source` | CAUGHT | CAUGHT |
| N-7 | `classification_attempted` dropped from the override | CAUGHT | CAUGHT |
| **N-8** | **`aggregate_order` inverted so `CLEAR` dominates `CONFLICT`** | **MISSED** | CAUGHT (C16) |
| N-9 | entry clause `N-3` deleted, its code kept | CAUGHT | CAUGHT |
| N-10 | `AXIS_TOKENS` gutted in the loader | CAUGHT | CAUGHT |
| N-11 | a D4-F guard deleted **and** its collection emptied | CAUGHT | CAUGHT |
| N-12 | `downstream_rule` (T-F6) emptied | CAUGHT | CAUGHT |
| **N-13** | **`ASSUMPTION_ALLOWED` `continue_and_review` → `continue`** | **MISSED** | CAUGHT (C15) |
| **N-14** | **`NEEDS_INPUT.user_decision_required` true → false** | **MISSED** | CAUGHT (C15) |

**Root cause of all five.** The expected Python constant pinned the *reason codes* and nothing else.
The `states` block, `aggregate_order`, and `assumption_allowed_forbidden_when`'s sub-lists were
checked only for closed-set membership or not at all. **`"continue"` is a legal workflow value**, so
C11c passed while N-1 turned the pausing state into a continuing one. Membership in a closed set is
not the same as a correct value.

**Fix.** C15-C23 pin by value: per-state workflow and both flags (C15), aggregate order (C16),
INV-4's forbidden-when object (C17), INV-3's requirements (C18), `user_decision` fields (C19), the
CONFLICT citation minimum (C20), per-state required evidence (C21), entry clause text (C22), and the
downstream rule (C23). Ten regression tests reproduce the motivating mutation for each, so a gap
that reopens is a red test rather than silence.

---

### T3. RI-N1 — both options taken, and why

The Reviewer offered (a) rename or (b) add a characterization test. **I did both**, because they fix
different halves and neither alone is sufficient.

**(a) Renamed** `test_this_change_does_not_alter_evaluate_invocation_behaviour` →
`test_skill_policy_source_declares_no_schema_version_gate`. The old name promised a behavioural
guarantee the body did not give: it read source text and checked token absence. Leaving the name
would have been precisely the "claim wider than the evidence" defect this run kept hitting, sitting
inside the test suite meant to prevent it.

**(b) Added** `test_evaluate_invocation_still_accepts_an_unknown_top_level_schema_version`, which
**executes** the shipped path: it builds a temporary Skill whose *top-level* `schema_version` is 99,
calls `evaluate_invocation()`, and asserts the result is still `VALID`.

This deliberately pins a **pre-existing defect's** behaviour. UD-3 puts fixing it out of scope, so
the correct assertion is that it is **unchanged** — not that a gate exists. If a later change adds
the gate, this test fails, and that failure is the signal: whoever adds it owns updating this test,
in the follow-up ticket that owns the defect. The docstring says so, so nobody "fixes" the test by
weakening the assertion.

`evaluate_invocation()` itself is untouched — `git diff c264e79 HEAD -- scripts/skill_policy.py` is
empty. The test also makes the requirement-9 asymmetry concrete: the same unsupported-version input
that this shipped path **accepts** is **rejected** by the new loader.

---

### T4. Anti-vacuity guards — collections actually emptied

Not read; **executed**. Each guarded collection was emptied in a disposable copy and the specific
test was run.

| collection emptied | guarded test | result |
|---|---|---|
| forbidden transitions | `test_forbidden_transitions_are_rejected` | **FAILED** — `Items in the second set but not the first` |
| reason-required states | `test_reason_code_is_required_for_each_non_clear_state` | **FAILED** — `0 != 3` |
| required evidence | `test_each_required_evidence_field_is_enforced` | **FAILED** — `{'ASSUMPTION_ALLOWED': 5, 'CONFLICT': 3} != {...}` |
| high-impact elements | `test_each_high_impact_element_alone_forbids_assumption_allowed` | **FAILED** — `[] != ['monetary_cost', ...]` |
| forbidden authority sources | `test_forbidden_authority_source_cannot_justify_a_transition` | **FAILED** — `Items in the second set but not the first` |
| reason codes | `test_every_reason_code_has_a_constructible_record` | **FAILED** — `17 != 18` |

All six fail **at the co-located guard**, before the loop can pass vacuously. N-11 additionally
proves the combined attack — deleting a guard *and* emptying its collection — is still caught, by
the validator and the other tests.

---

### T5. Regression

| check | result |
|---|---|
| lifecycle and policy modules unchanged | `git diff --stat c264e79 HEAD -- scripts/run_logging.py scripts/orca_runtime_harness.py scripts/review_isolation.py scripts/e2e_harness.py scripts/task_context.py scripts/skill_policy.py scripts/quality_profile.py scripts/agent_profile.py VERSION` → **empty** |
| change set matches expectation | `git diff c264e79 HEAD --stat` → **69 files changed, 3432 insertions(+), 0 deletions** — the OS-28 contract, loader, fixtures, tests, validator, shared prose/templates and changelog only |
| package | commands 3-5 pass; 165 source files both loose and in the archive |
| whitespace | `git diff --check` clean |

The file count is unchanged from IMPLEMENTATION (69); this phase added lines to three existing test
and validator files and created no new file.

---

## Changes

Three changes, all in the strengthening direction:

1. **RI-N1**: renamed the source-scope UD-3 test to describe what it checks, and added a behavioural
   characterization test that executes `evaluate_invocation()`.
2. **C15-C23** in `validate_skills.py`: pin the decision policy's semantic core by value.
3. **Ten regression tests** in `test_validate_skills.py`, one per mutation that motivated a check.

No contract was weakened to make a test pass. No user decision was reinterpreted. `VERSION`,
`LICENSE`, and the Risk / Quality Profile / Agent Profile / Final Review / lifecycle surfaces are
untouched.

## Modified Files / Artifacts

| path | change |
|---|---|
| `scripts/test_decision_policy.py` | RI-N1 rename + characterization test (+1 test) |
| `scripts/validate_skills.py` | C15-C23 and their expected constants |
| `scripts/test_validate_skills.py` | 10 regression tests |
| `artifacts/runs/run_3233a1469e97/TEST.md` | created (this file) |

Commits (not pushed):

```text
635be76  Make the UD-3 regression test say what it checks, and add the behavioural half
1efcc54  Pin the decision policy's semantic core, found unpinned by mutation
```

`git status --porcelain` over `scripts`, both Skills, `docs`, `CHANGELOG.md` and `VERSION` is empty.

## Validation

Every number in this document came from the command recorded beside it, run on the committed tree at
`1efcc54`. Two results I initially reported to myself and then retracted after investigating:

| retracted claim | why it was wrong | corrected result |
|---|---|---|
| "22/22 mutations caught" | the `val_tests` column scored an **ImportError** as a failure; `test_validate_skills.py` needs `scripts/` as cwd. Every row's `val` signal was noise | **21/22**, M-21 MISSED — matching IMPLEMENTATION |
| "M-21b coordinated variant caught" | the `val=FAIL` was **my own new regression test** failing because its mutation target no longer existed in the mutated tree | **MISSED**, as DESIGN F-5 predicts |

The harness now aborts if `test_validate_skills` fails to import, so that specific false signal
cannot recur silently.

**Not verified — stated so rather than implied:**

- **(UD-2)** requirement 5 is proven at the **permission** level only. No test here detects a real
  model's over-escalation; that needs an LLM in the loop and belongs to OS-32.
- **(UD-4)** the choice that no dedicated reason code covers a repository-policy conflict still rests
  on the unverified assumption that the eleven boundary elements catch every policy class that
  matters. I did not enumerate policy classes to test it.
- **M-21b** — a coordinated three-file prose edit — is not caught by any automated check.
- Nothing executes this contract. Running the check at a phase gate is OS-29, asking the question is
  OS-30, waiting is OS-31; none is implemented, and `grep -rn 'decision_policy'` still shows no
  runtime importer.

## Unit Tests / Testing Strategy

11 tests added (1 characterization, 10 regression); total 1326 → **1337**, skips unchanged at 6.
Validator checks 604 → **622**.

The strategy this phase settles: **closed-set membership is not a correctness check.** A value can
be legal and wrong at the same time, which is how `NEEDS_INPUT` could declare `"continue"` while
every check passed. Anything that carries meaning — a workflow, an ordering, an invariant's
condition list, a clause's text — is now pinned by value against an expected constant, and each pin
has a regression test reproducing the mutation that motivated it.

## Correction — iteration 2 (Final Review FR-1)

**FR-1 (Responsible Phase: test) is this correction.** FR-2 was resolved in a separate round and its
phase gate re-passed; nothing here touches it, and both Skills still carry `user_decision_sources`.

### The defect, reproduced before changing anything

On a disposable `git archive HEAD` copy, both Skills' `NEEDS_INPUT → CLEAR` was changed from
`requires_user_decision` to the equally legal `allowed`:

```text
python3 scripts/validate_skills.py
  -> Skill validation PASSED (626 checks)
     exit 0
```

C8 compared only the **set** of cells whose value is `forbidden`; C11c compared only closed-set
membership. Neither pinned what a permitted edge *means*. The mutation removes two promises at once —
an unresolved `NEEDS_INPUT` or `CONFLICT` cannot continue, and reaching `CLEAR` takes a real user
decision — and the static validator accepted it.

This is **not** the disclosed M-21 gap: M-21 needs the expected constant edited too, while this
changed only the two Skill contracts and no constant at all.

### The same-shape sweep — four more keys, each verified by mutation

FR-1's shape is "checks the set, never the value". TEST iteration 1 had applied value-pinning to
states, flags, aggregate order and INV-4 in C15-C23, but **not to the edges**. I swept every
remaining contract key and mutated each candidate rather than reading the validator:

| key | mutation applied | before | after |
|---|---|---|---|
| `transitions` | `NEEDS_INPUT → CLEAR` → `allowed` | validator **green** | **CAUGHT** (C26a) |
| `transitions` | `CONFLICT → CLEAR` → `allowed` | validator **green** | **CAUGHT** (C26a) |
| `transitions` | `ASSUMPTION_ALLOWED → CLEAR` → `allowed` | validator **green** | **CAUGHT** (C26) |
| `boundary_elements` | `reversibility.values` emptied | **MISSED entirely** | **CAUGHT** (C27) |
| `boundary_elements` | `blast_radius` loses `repository`, `external_system` | **MISSED entirely** | **CAUGHT** (C27) |
| `boundary_elements` | `explicit_requirement_conflict.minimum` 2 → 1 | **MISSED entirely** | **CAUGHT** (C27) |
| `policy_source_roles` | drops `supports` | validator **green** | **CAUGHT** (C28) |
| `policy_source_kinds` | widened with `model_hunch` | **MISSED entirely** | **CAUGHT** (C28) |
| `state_scope` | reversed to `per_check_only` | **MISSED entirely** | **CAUGHT** (C29) |

Two findings inside that table are worth naming. The **validator was green in all nine cases** before
this fix, including the four the tests happened to catch — FR-1 is specifically about the static
validator, so those were gaps too. And `blast_radius` losing `repository`/`external_system` is not
cosmetic: **INV-4's blast-radius clause names exactly those two values**, so removing them from the
element would make that clause unreachable while every check stayed green.

**Two keys are correctly *not* value-pinned, and I checked rather than assumed:** `independent_axes`
was already pinned by C11d's positive equality, and `schema_version` is properly checked by
*membership* in `SUPPORTED_SCHEMA_VERSIONS`, because a supported version legitimately varies.

### What was added

| check | pins |
|---|---|
| **C26** | all sixteen transition cells, by value |
| **C26a** | the two authority edges named separately, each required to equal `requires_user_decision`, so a failure says *which promise broke* — and any other value is rejected, not merely the `allowed` spelling |
| **C27** | each boundary element's `{kind, values, minimum}` payload, not just its name |
| **C28** | `policy_source_roles` and `policy_source_kinds` |
| **C29** | `state_scope` |

Eleven regression tests reproduce each mutation on a disposable copy and assert the named failure,
including **both authority edges as FR-1 requires**, a subtler relaxation to `requires_retraction`
(still not `allowed`, still not a user decision), and a **tightening** mutation — over-restriction is
the mirror defect of relaxation, and the matrix is pinned in both directions.

### Both halves demonstrated

The sweep runs an **unmutated control in the same batch**, so a validator that merely failed on
everything would be visible:

```text
S-0  no mutation (control)          valid=pass  dp=pass  val=pass   <- green, as required
S-1..S-9  each mutation             valid=FAIL                      <- all nine caught
```

Before the fix that same control was green and S-1…S-9 were green at the validator. After, the
control is still green and all nine fail. This run has produced three mutation-harness false
positives, all self-corrected, so the harness used here does literal substitution only, aborts unless
its target is found exactly once, and aborts rather than scoring an `ImportError` as detection.

### Commands after the correction

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (638 checks)** — was 626 |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1360 tests in 302.548s — OK (skipped=6)** — was 1349 |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `python3 scripts/verify_package.py --archive …` | **PASSED (173 source files); Verified archive** |
| `git diff --check` | no output |

Skips unchanged at 6. `git diff --stat c264e79 HEAD -- VERSION LICENSE .orca scripts/skill_policy.py
scripts/quality_profile.py scripts/agent_profile.py scripts/workflow_contract.py
scripts/run_logging.py` is **empty**. FR-2's result is intact — `user_decision_sources` is still
present in both Skills.

### DESIGN.md updated in the same round

D3 gains rows for C26, C26a, C27, C28 and C29; new section **D3-3** records the finding, the sweep
table, and the two keys correctly checked by membership rather than value.

Commits: `264a5cb` (validator + tests), `8e48993` (DESIGN). Not pushed.

## Downstream revalidation — iteration 3 (§17 T5a)

Not a correction. The FR-3/FR-4 and RI3-1 rounds materially enlarged the contract, so this pass
re-checks whether the TEST-phase safety net still bites on the larger surface. Two gaps in the
**verification layer** were found and closed; the contract semantics IMPLEMENTATION settled were not
touched.

### T1 — the existing safety net still bites (20 mutations, control green)

Every pin from earlier TEST rounds was mutation-tested against the enlarged contract. Surgical
substitution only; the harness aborts unless its target appears exactly once and refuses to score an
`ImportError` as detection.

```text
T-0   control, no mutation                                      green
T-1 .. T-9    C15-C23  states / aggregate_order / INV-4 / INV-3 / user_decision_fields /
              citation_minimum / required_evidence / entry-clause prose / downstream_rule   9/9 CAUGHT
T-10 .. T-12  C26, C26a  both authority edges + the retraction edge                         3/3 CAUGHT
T-13 .. T-16  C27, C28, C29  enum values, triggering, policy_source_roles, state_scope      4/4 CAUGHT
T-17 .. T-20  C30, C31  entry conditions (widened / conjunct dropped / combinator flipped),
              authority precedence emptied                                                  4/4 CAUGHT
```

**20/20 caught with the control green.** The new surfaces do bite: C30 catches all three
entry-condition mutations and C31 catches the precedence one.

### T2 — the seven recurring defect types, applied to the new surfaces

`entry_conditions`, the twelve `ENTRY_PREDICATES`, the per-element `triggering` values, and
`authority_precedence` are all new since those types were catalogued. Each was applied deliberately.

| type | applied to the new surfaces | result |
|---|---|---|
| **(a)** unreachable clause | every one of the 12 predicates given a witness that makes it **true**, a witness that makes it **false**, and checked for being **referenced** by some entry condition | **clean** — 12/12 satisfiable, 12/12 falsifiable, 0 defined-but-unused, 0 used-but-undeclared |
| **(b)** vacuous pass / empty loop | all four entry conditions probed for satisfiability and refutability | **clean** — each is both |
| **(c)** membership checked, value not | unknown predicate, unknown combinator, missing state, empty predicate list, two combinators in one condition, precedence naming an unknown element | **clean at the contract/loader level** — all six rejected at load. Says nothing about record validation; see the scope correction below |
| **(d)** denylist enforcing a category | `authority_precedence` is a positive list of what a policy source *cannot* resolve, and `user_decision_sources` remains the allowlist | **clean** — no denylist introduced |
| **(e)** presence checked, consistency not | `triggering` values checked against the element's **own value set** | **DEFECT FOUND** — see below |
| **(f)** forbid-only, permit-unchecked | every predicate has a positive witness; every state is reachable | **clean** |
| **(g)** predicates evaluated independently | the 63-case no-resolver sweep plus the new 42-case resolver sweep | **clean** — 0 leaks |


> **Scope correction (added in iteration 6, after FR-7).** Every row above was executed, but
> the verdicts are about **the surface this round introduced**, not about the decision-policy
> code as a whole. The **Decision Record surface** — `validate_record()`'s per-reason-code
> evidence path — was **not** inside this round's scope and was never swept for (c) or (e)
> here. FR-6 lived exactly there: the reason code's boundary element was checked by name and
> never by value. A reader who took "clean" as a statement about the whole surface would have
> been misled, and that gap is why FR-7 was raised against this document as well as against
> the tests.

**The (e) finding, and why it mattered more than it looks.** An enum boundary element could name a
`triggering` value it does **not declare** — `"reversibility": {"values": [...], "triggering":
["not_a_member"]}` loaded without complaint. Nothing could ever equal that value, because
`_validate_declared_facts` already rejects out-of-enum declarations, so the element became a **dead
trigger**: an irreversible item would silently stop escalating. Probed before and after:

```text
before:  irreversible declared, contract with an orphan triggering value -> []
after :  the contract is rejected at load, naming the orphaned values
```

Type (e) producing type (a). **The shipped contract is correct** — every triggering value is a
member — so this is a missing loader consistency check, not a contract change, which puts it on the
verification side of the boundary. C27 catches *drift from the pinned value*; this catches a contract
inconsistent **on its own terms**.

### T3 — the count was wrong, corrected by counting

The Reviewer's non-blocking finding is right. Counted directly:

```text
9 triggering elements + 3 CONFLICT clauses + 9 element-with-C-1 pairs = 21 fact-cases
21 x 3 resolver states carrying NO authority (none / supporting / forbidden) = 63
the test's own final assertion: assertEqual(checked, 21 * 3) -> 63
```

The docstring said **105**. That figure came from an ad-hoc probe that also swept the two resolver
states which *do* carry authority. **Rather than only relabel**, those 42 are now a sibling test with
their own expected outcome — an allowlisted decision resolves every case; a determining policy source
resolves all but the two A4-0 excludes — which is also the positive control the no-resolver sweep
needed. So the label is now 63 **and** the 42 it wrongly implied are permanently covered.

### T4 — every prior fix still holds (11/11, executed)

| fix | check | result |
|---|---|---|
| FR-1 | both authority edges still `requires_user_decision` | ok |
| FR-2 | `high_confidence` rejected; `explicit_user_reply` accepted | ok |
| FR-3 | mismatched `boundary_element` rejected; matching accepted | ok |
| FR-4 | high-impact, no authority → `['NEEDS_INPUT']` | ok |
| RI3-1 | reserved + determining → `['NEEDS_INPUT']`; C-1 + determining → `['CONFLICT']` | ok |
| positive | ordinary element + determining → `['CLEAR']`; safe supporting item → `['ASSUMPTION_ALLOWED']` | ok |

### A red control, and what it cost

The first verification-layer mutation run reported `V-0 control: val=FAIL`. **The mutation results
were unusable until that was fixed**, and it turned out two mutation assertions were stale rather
than any check being broken: the new loader consistency rule fires *earlier* than C27 for two
boundary-element mutations, so both were still caught but with a different, more specific message.
The assertions were repointed at the message that now fires.

Root cause worth naming: after changing the loader I re-ran the module I had edited
(`test_decision_policy`) and not the module that depends on it (`test_validate_skills`). Running only
the file you touched is how a dependent module goes red unnoticed.

**With the control green, two results flipped from CAUGHT to MISSED**, and they are reported as
MISSED:

```text
V-0  control                                          green
V-1  triggering consistency check disabled            CAUGHT
V-2  a predicate removed from ENTRY_PREDICATES        CAUGHT
V-3  the 63-sweep cardinality guard -> tautology      MISSED
V-4  the 42-sweep cardinality guard -> tautology      MISSED
```

**V-3/V-4 are a known and inherent limit, not a fixable gap.** Rewriting a D4-F guard as
`assertEqual(checked, checked)` cannot be detected by the suite that contains it — no test suite
detects the deletion of its own assertion. This is the same class as the M-21 coordinated-edit gap
that DESIGN F-5 already records, and the mitigation is the same: a human reading the diff. It is
stated here rather than left implied, and their earlier CAUGHT was a false positive from the red
control.

### Commands after this pass

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED (642 checks)** — unchanged |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1404 tests in 305.920s — OK (skipped=6)** — was 1399 |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `python3 scripts/verify_package.py --archive …` | **PASSED (173 source files); Verified archive** |
| `git diff --check` | no output |

Check count is unchanged at 642 because both gaps were closed in the loader and the test suite rather
than by adding a validator check. Skips unchanged at 6. Protected-surface diff empty.

### DESIGN.md

One row added to D2-2's fail-closed table: an enum element naming a `triggering` value outside its
own value set is rejected at load. No other section needed changing — this pass altered verification,
not contract semantics.

Commits: `7fddd56` (loader consistency check, reachability tests, corrected count and its sibling
test), `8532cb1` (two stale mutation assertions repointed). Not pushed.

## Review Feedback Resolution

```text
FINDING RI-N1: RESOLVED   both options taken -- renamed for accuracy AND a behavioural
                          characterization test added; evaluate_invocation() untouched
```

No new question requires user authority. UD-1 through UD-4 are unchanged and unreinterpreted: the
decision record section is still optional, requirement 5 is still permission-level with the limit
stated, `skill_policy.py` is still untouched, and the code set is still 18.

STATUS: COMPLETE

---

## Downstream revalidation — iteration 4 (§17 T5a), after FR-5

The FR-5 correction moved user-decision authorization into one helper,
`_user_decision_defect(policy, decision) -> str | None`, read by both
`permitted_states()` and `validate_transition()`. This pass re-ran the safety net on
top of that change, swept the new surface for the nine recurring defect types, and
re-confirmed every prior fix. **Nothing was loosened. Three defects were found and are
reported, not fixed** — closing any of them changes evaluator semantics, and the
implementation budget is spent.

### T1 — the existing safety net still bites (20 mutations, control green)

Control first: the unmodified tree passes `validate_skills.py`,
`test_decision_policy`, and `test_validate_skills`, so the run below is valid.
The set is the same one that scored 20/20 in iteration 3, re-executed against
`a12bbe2`.

| Group | Mutations | Result |
| --- | --- | --- |
| C15–C23 value pins | T-1 … T-9 | **9/9 CAUGHT** |
| C26 / C26a transition matrix | T-10 … T-12 | **3/3 CAUGHT** |
| C27 / C28 / C29 | T-13 … T-16 | **4/4 CAUGHT** |
| C30 / C31 entry conditions and precedence | T-17 … T-20 | **4/4 CAUGHT** |

**20/20 CAUGHT, nothing flipped to MISSED.** The set was run twice, independently,
with a green control both times; the two runs agree row for row. `T-0` reports
`MISSED` by construction — that is the control row, and an unmodified tree that
"escapes detection" is the result you want there.

### T2 — the nine recurring defect types, applied to the new surface

The new surface is the helper and the parity tests. Every row below was executed.

| # | Type | On `_user_decision_defect` / the parity tests | Verdict |
| --- | --- | --- | --- |
| (a) | unreachable clause | all three branches reached: non-mapping/empty → field missing/empty → source not allowlisted. The source branch is reachable only for a non-empty, non-allowlisted source, and six sources reach it. | clean |
| (b) | vacuous / empty loop | see the emptying experiment below | clean |
| (c) | membership only, value unchecked | source membership *is* the rule (allowlist); the fields are free text, so non-emptiness is the only checkable property | clean **for this helper only** |
| (d) | denylist for a category | the helper decides on `user_decision_sources` (allowlist). `forbidden_authority_sources` is read **only** to word the error message. Mutation N-3 reverts it to a denylist → **CAUGHT** | clean |
| (e) | presence without consistency | **GAP FOUND** — mutation N-4 (`field not in decision`, dropping the emptiness half) was **MISSED**. Closed, see below | fixed |
| (f) | forbid without permit | every negative sweep is paired with a positive control; P-5 confirms over-blocking is caught | clean |
| (g) | predicates independent, broken in combination | RI3-1 precedence re-verified across the predicate grid | clean |
| (h) | dead trigger | `validate_skills.py:2383` already forbids an allowlist/denylist overlap; today's overlap is empty | clean |
| (i) | same concept judged differently in two places | **TWO FOUND** — TR4-1 and TR4-2 below | reported |


> **Scope correction (added in iteration 6, after FR-7).** Every row above was executed, but
> the verdicts are about **the surface this round introduced**, not about the decision-policy
> code as a whole. The **Decision Record surface** — `validate_record()`'s per-reason-code
> evidence path — was **not** inside this round's scope and was never swept for (c) or (e)
> here. FR-6 lived exactly there: the reason code's boundary element was checked by name and
> never by value. A reader who took "clean" as a statement about the whole surface would have
> been misled, and that gap is why FR-7 was raised against this document as well as against
> the tests.


> **Narrowed again in iteration 7, after FR-9.** Iteration 6 narrowed this to "the surface
> swept". Four more value-domain defects surfaced afterwards — FR-8 (`boolean` values),
> RI8-1 (`user_decision` element values), RI9-1 (the locator's shape) and the two RI9-1's lens
> exposed (`where_recorded`/`resolves` text, citation entries) — every one of them alive
> while this table said "clean". So the accurate scope is narrower still: **(c) and (e) were
> swept over the CONTRACT and the shared helpers, never over the VALUE DOMAINS a declared
> fact may carry.** That whole axis had no coverage until iteration 7's register.

#### (b) — the collections were actually emptied

Each of the four new loops was run with the contract collection it iterates replaced
by an empty one, in a disposable tree:

| Test | `user_decision_fields` emptied | `user_decision_sources` emptied |
| --- | --- | --- |
| `..._agree_on_every_field_omission` | **guard bit** | guard bit (verdict assertion) |
| `..._agree_on_every_source` | passes — iterates *sources*, still 8 | **guard bit** |
| `..._never_permits_clear_anywhere` | **guard bit** | passes — iterates *fields*, still 3 |
| `..._still_permits_clear_everywhere` | passes — iterates *sources* | **guard bit** |

Every loop's own collection is cardinality-guarded inside the same function. A test
passing when a collection it does not iterate is emptied is correct behaviour, not
vacuity: its own loop still runs and its assertions still discriminate.

#### (e) — the gap that was found, and closed

`test_an_empty_field_is_not_evidence_either` (new). Every existing test removed a
field with `pop`, so a helper checking only `field not in decision` — presence
without content — passed the entire suite. A `where_recorded` of `""` is a filled-in
form with nothing written on it. The new test drives each of the 3 declared fields
through 4 empty values (`""`, `None`, `[]`, `{}`), 12 assertions, cardinality-guarded
on both loops, with a positive control that the same record with real content is
still accepted. Re-running the mutation as **P-1: CAUGHT**.

### T2(i) — concept comparison, and what it found

Every concept judged by more than one of the three public APIs was enumerated from
the `_require`/`raise` sites and compared on a chosen sample, not assumed.

| Concept | Where judged | Verdict |
| --- | --- | --- |
| user_decision authorization | `permitted_states` + `validate_transition` | **agree** — 12 cases (3 omissions × complete, 8 sources) |
| boundary-element enum membership | `permitted_states` + `validate_record`, both via `_validate_declared_facts` | **agree** |
| `policy_source.kind` membership | same shared helper | **agree** |
| `policy_source.role` membership | `permitted_states` only | **DISAGREE → TR4-1** |
| "may ASSUMPTION_ALLOWED apply?" | entry conditions vs `assumption_allowed_forbidden_when` | **DISAGREE → TR4-2** |

The implementation report's claim that `policy_source` agrees is correct **for
`kind`**, which goes through the shared helper. It does not hold for `role`.

#### TR4-1 — `policy_source.role` membership is judged in one place only (MAJOR)

`permitted_states` raises `unknown policy_source role 'invented_role'`.
`validate_record` accepts the identical value on a `NEEDS_INPUT` record. Executed:

```
role='invented_role'  permitted_states: REJECT: unknown policy_source role 'invented_role'
                      validate_record : accept
```

`kind` is checked inside `_validate_declared_facts`, which `validate_record` calls;
`role` is checked inline in `permitted_states`, which it does not. The asymmetry is
arbitrary — both are closed sets in the contract and both are pinned by C28. Effect:
a record naming a misspelled role passes record validation, and the two APIs
contradict each other on the same value. `ASSUMPTION_ALLOWED` is unaffected, because
that branch compares the role by equality to `supports`.

#### TR4-2 — the two ASSUMPTION_ALLOWED rules disagree on the middle band (MAJOR)

`entry_conditions.ASSUMPTION_ALLOWED` requires `reversible_in_run` **and**
`blast_radius_within_scope`. `assumption_allowed_forbidden_when` forbids only
`irreversible`, and `repository`/`external_system` **with** `irreversible`. Over all
48 combinations of reversibility × blast_radius × security × reserved authority, the
two answers to "may this item be ASSUMPTION_ALLOWED?" differ on **6**:

```
reversible_in_run    + repository       + no flags : permitted=False  record_valid=True
reversible_in_run    + external_system  + no flags : permitted=False  record_valid=True
reversible_with_effort + current_change + no flags : permitted=False  record_valid=True
reversible_with_effort + module         + no flags : permitted=False  record_valid=True
reversible_with_effort + repository     + no flags : permitted=False  record_valid=True
reversible_with_effort + external_system+ no flags : permitted=False  record_valid=True
```

The permissive side is `validate_record` — the API a Reviewer uses to check a filed
record. It accepts an `ASSUMPTION_ALLOWED` record for an item the evaluator would
never permit to enter that state, which is autonomous assumption on an item the entry
contract excludes. The band is bounded: **all 42 other combinations agree**, and every
case involving `irreversible`, any of the five high-impact flags, or reserved
authority agrees. A prohibition list narrower than a permission gate is not a logical
contradiction, but it does mean the contract's two enforcement points answer the same
operational question differently — the FR-5 shape.

#### TR4-3 — whitespace-only text counts as evidence (MINOR)

`_is_empty` treats `"   "` as content, so a `user_decision` whose `where_recorded` is
three spaces is accepted as evidence of user authority by both APIs. They agree, so
this is not an (i) defect; it is (e) at the value level. `_is_empty` is shared by the
whole loader, so narrowing it is an evaluator-semantics change. The new test's
`empties` tuple deliberately excludes whitespace and says so in a comment rather than
pinning either behaviour.

#### What was added to the net, and what deliberately was not

`CrossApiConceptParity` (new, 4 tests) pins the concepts that **do** agree —
enum membership and `policy_source.kind` — over both APIs, and pins the
**dangerous half** of the ASSUMPTION_ALLOWED question: whenever the item is
irreversible, carries any of the five high-impact flags, or reserves authority to
the user, both APIs must refuse. A positive control asserts a safe item is still
ASSUMPTION_ALLOWED by both, so the class cannot be satisfied by refusing everything.

TR4-1's and TR4-2's divergent bands are **not** asserted in either direction. A test
that asserted today's behaviour would pin a defect — which is exactly the mistake
FR-5 found in two tests — and a test that asserted the corrected behaviour would fail
against code this phase is not permitted to change. Both are recorded here instead.

### T3 — every prior fix still holds (11/11, executed)

| Fix | Probe | Result |
| --- | --- | --- |
| FR-1 | `NEEDS_INPUT→CLEAR` is `requires_user_decision`; `CONFLICT→ASSUMPTION_ALLOWED` is `forbidden` | intact |
| FR-2 | an invented source is rejected (allowlist, not denylist) | intact |
| FR-3 | `security_impact` filed with `boundary_element: privacy` is rejected | intact |
| FR-4 | irreversible + external_system + security does not permit CLEAR | intact |
| RI3-1 | a determining policy source cannot un-reserve user authority → `{NEEDS_INPUT}` | intact |
| FR-5 | a source-only record is refused by **both** APIs | intact |

Positive controls, so this is not over-blocking:

| Control | Result |
| --- | --- |
| complete decision → `['CLEAR']` | holds |
| ordinary item + determining source → `['CLEAR']` | holds |
| safe item + supporting source → `['ASSUMPTION_ALLOWED']` | holds |

**8/8 regression probes intact, 3/3 positive controls hold.**

### T4 — the two corrected tests pass for the right reason

`test_a_forbidden_authority_source_does_not_permit_clear` now builds a **complete**
record and varies only the source. Two independent demonstrations:

1. Directly: with a complete record, the only defect the helper reports for each of
   the five forbidden sources and one invented source names the **source**
   (`'timeout' is not evidence of user authority; …`), never a missing field. The
   contrast case — an allowlisted source with a source-only record — reports
   `user_decision requires a non-empty 'where_recorded'`, a different message.
2. By mutation: delete **only** the source-allowlist branch and leave the field
   checks intact. The test fails on all six sources. Had it been passing because of
   field omission, it would still have passed.

`test_an_allowlisted_authorization_permits_clear` is the paired positive control: it
asserts a complete authorization *does* buy CLEAR, and mutation N-2 (helper checks
only the first declared field) is **CAUGHT**, so the assertion depends on the whole
record rather than on the source alone.

### The one thing this suite still cannot detect

Mutation **N-6** — deleting the parity test's own "the verdict must be right"
assertion — is **MISSED**. No test suite detects the removal of its own assertion;
this is the same inherent limit recorded for V-3/V-4 in iteration 3, not a new
weakness. It is stated here rather than left implicit.

### Mutation results for this pass — control verified green first

| # | Mutation | Result |
| --- | --- | --- |
| N-0 / P-0 | control, unmodified tree | **green** (results below are valid) |
| N-1 | the predicate stops using the shared helper — the FR-5 defect itself | CAUGHT |
| N-2 | the helper checks only the first declared field | CAUGHT |
| N-3 | the helper reverts to a denylist for the source | CAUGHT |
| N-4 | the helper checks presence but not non-emptiness | **MISSED** → gap closed, re-run as P-1 |
| N-5 | `validate_transition` stops using the helper | CAUGHT |
| N-6 | the parity test loses its own correctness assertion | **MISSED** (inherent limit) |
| P-1 | N-4 re-run against the new test | **CAUGHT** |
| P-2 | `validate_record` stops calling `_validate_declared_facts` | CAUGHT |
| P-3 | the declared-facts check stops validating `policy_source.kind` | CAUGHT |
| P-4 | the INV-4 forbidden-check is neutered in `validate_record` | CAUGHT |
| P-5 | `permitted_states` refuses ASSUMPTION_ALLOWED always (over-blocking probe) | CAUGHT |

P-5 is the check on the check: a suite that could be satisfied by refusing everything
would not have caught it.

### Commands after this pass

| Command | Result |
| --- | --- |
| `python3 scripts/validate_skills.py` | **PASSED (642 checks)** — unchanged; this pass added tests, not validator checks |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **1413 tests OK (skipped=6)** — was 1408; the 5 new tests are the whole delta and **skips did not increase** |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `verify_package.py --archive …` | **PASSED (173 source files)**, archive verified |
| `git diff --check` | clean |

Protected surfaces re-checked and untouched: `VERSION`, `LICENSE`, `quality_profile.py`,
`agent_profile.py`, `skill_policy.py`, and `evaluate_invocation()` (UD-3). No change to
`decision_policy.py`, to either `SKILL.md`, or to any template or review file — this
pass edited tests only.

### What the next phase owns

TR4-1, TR4-2 and TR4-3 are handed to the implementation phase, not to this one. Each
was reproduced by execution and is recorded above with the exact facts that produce it.
None of them is a regression introduced by FR-5: TR4-1 and TR4-3 predate this run's
corrections, and TR4-2 dates from FR-4, when `entry_conditions` was introduced beside
the pre-existing `assumption_allowed_forbidden_when` without the two being reconciled.

---

## Downstream revalidation — iteration 5 (§17 T5a), after TR4-1/2/3

The final TEST iteration. The IMPLEMENTATION correction changed evaluator code only —
**no contract edit** — routing three rules through helpers both fact-reading APIs call, and
adding a call-closure test as the structural guard. This pass re-ran the whole net on top
of it, exercised that guard in both directions, and reproduced the parity numbers
independently.

### T1 — the safety net still bites (27 mutations, control green)

Control verified green before either set. Both sets re-run against `a24e70d`.

| Set | Mutations | Result |
| --- | --- | --- |
| contract value pins C15–C23 | T-1 … T-9 | **9/9 CAUGHT** |
| transition matrix C26 / C26a | T-10 … T-12 | **3/3 CAUGHT** |
| C27 / C28 / C29 | T-13 … T-16 | **4/4 CAUGHT** |
| C30 / C31 entry conditions, precedence | T-17 … T-20 | **4/4 CAUGHT** |
| TR4 fixes R-1 … R-7 (incl. both over-blocking probes) | R-1 … R-7 | **7/7 CAUGHT** |

**27/27 CAUGHT, nothing flipped to MISSED.** `T-0` / `R-0` report MISSED by construction —
those are the control rows, where an unmodified tree escaping detection is the desired
result.

### T2 — the call-closure device, exercised in both directions

The guard's whole purpose is to fail when a fact rule reaches one API and not the other.
That was tested by actually doing it, not by reading the test.

| # | Mutation | Closure test | Expected |
| --- | --- | --- | --- |
| C-0 | none (control) | PASS | PASS |
| C-1 | a new helper called from `validate_record` **only** | **FAIL** | FAIL |
| C-2 | a new helper called from `permitted_states` **only** | **FAIL** | FAIL |
| C-3 | the same helper called from **both** | **PASS** | PASS |
| C-4 | an **inline** `_require` added to one API only | **PASS** | — see below |
| C-5 | `_entry_condition_defect` removed from **both** | **FAIL** | FAIL |

C-1 and C-2 show it bites symmetrically; C-3 shows it does not simply refuse change; C-5
shows the named-helper guard catches the coordinated deletion that set equality alone would
miss.

**C-4 is the device's real boundary, found by executing it.** An inline `_require` adds no
name to a call closure, so the closure test passes straight through it. The device catches
a rule added as a **helper call** to one side — which is where FR-5, TR4-1 and TR4-2 each
actually lived — but the DESIGN wording ("fails if a judgement is added to one and not the
other") is slightly wider than what the mechanism does. Recorded rather than left implicit.

**Half of that boundary is now closed, in the direction that matters.**
`test_the_evaluator_delegates_every_judgement` asserts `permitted_states` contains zero
inline `_require`/`raise` — it reads nothing but declared facts, so it has no legitimate
inline rule, and today has exactly zero. Under the C-4 mutation the closure test passes and
this one **fails**, which is the gap closing. The mirror is deliberately not asserted:
`validate_record` has twelve inline `_require` calls that are record-**shape** rules
(reason_code/state agreement, citation minimum, evidence presence), not fact rules, and
forbidding them there would be over-blocking. The new test carries a positive control that
its own AST query can see such a node, so it cannot pass by being broken.

### T3 — parity reproduced independently

Reproduced from scratch, with the base record's validity asserted **first** — a probe whose
base fixture is invalid reports false divergences everywhere, which is exactly how an
invalid reason code produced a false divergence earlier in this run.

| Measure | Reported | Reproduced |
| --- | --- | --- |
| ASSUMPTION_ALLOWED combinations | 48 | **48** |
| disagreements | 0 | **0** |
| combinations still permitting the state | 2 | **2** |
| multi-site concepts | 9 | **9** |
| total parity cases | 109 | **109** |
| divergences | 0 | **0** |

The two permitted combinations are `reversible_in_run` × `current_change` and
`reversible_in_run` × `module`, both with security false and no reserved authority. Fewer
than two would be over-blocking; more would mean the contract had been loosened. It is
exactly two.

### T4 — the nine types, applied to the new surfaces for the last time

Surfaces: `_entry_condition_defect`, the role check inside `_validate_declared_facts`, the
stripped `_is_empty`, and the call-closure tests. Every row executed.

| # | Type | Finding | Verdict |
| --- | --- | --- | --- |
| (a) | unreachable clause | both branches of `_entry_condition_defect` reached, for `all_of` and `any_of` states alike; R-4/R-5 confirm by mutation | clean |
| (b) | vacuous / empty loop | collections actually emptied, see below | clean |
| (c) | membership only, value unchecked | the entry predicates read values, not just key presence; R-5 (combinator ignored) is CAUGHT | clean **for the entry predicates only** |
| (d) | denylist for a category | the entry condition is a positive gate; INV-4 remains as the prohibition rather than as the rule | clean |
| (e) | presence without consistency | `_is_empty` now strips, so a present-but-blank field is empty; R-6 CAUGHT | clean **for `_is_empty` only** |
| (f) | forbid without permit | every negative paired with a positive control; R-4 and R-7 are the over-blocking probes and both are CAUGHT | clean |
| (g) | predicates independent, broken in combination | RI3-1 precedence re-verified: determining + reserved → `{NEEDS_INPUT}`, determining + contradiction → `{CONFLICT}` | clean |
| (h) | dead trigger | every enum element's `triggering` ⊆ its own `values`, enforced at load time | clean |
| (i) | same concept judged in two places | 9 concepts, 109 cases, 0 divergences | clean |

**Nothing new was found on the surfaces listed above.**
> **Narrowed again in iteration 7, after FR-9.** Iteration 6 narrowed this to "the surface
> swept". Four more value-domain defects surfaced afterwards — FR-8 (`boolean` values),
> RI8-1 (`user_decision` element values), RI9-1 (the locator's shape) and the two RI9-1's lens
> exposed (`where_recorded`/`resolves` text, citation entries) — every one of them alive
> while this table said "clean". So the accurate scope is narrower still: **(c) and (e) were
> swept over the CONTRACT and the shared helpers, never over the VALUE DOMAINS a declared
> fact may carry.** That whole axis had no coverage until iteration 7's register.


> **Claim narrowed in iteration 6, after FR-7.** As originally written this paragraph read as
> a general all-clear. It was not one. The sweep covered `_entry_condition_defect`, the role
> check, `_is_empty` and the closure tests; it did **not** cover `validate_record()`'s
> per-reason-code evidence path, where FR-6 was live and unfound at the time this was
> written. The correct reading of that round is: **nothing new on four surfaces, one surface
> never examined.**
> **Scope correction (added in iteration 6, after FR-7).** Every row above was executed, but
> the verdicts are about **the surface this round introduced**, not about the decision-policy
> code as a whole. The **Decision Record surface** — `validate_record()`'s per-reason-code
> evidence path — was **not** inside this round's scope and was never swept for (c) or (e)
> here. FR-6 lived exactly there: the reason code's boundary element was checked by name and
> never by value. A reader who took "clean" as a statement about the whole surface would have
> been misled, and that gap is why FR-7 was raised against this document as well as against
> the tests.


#### (b) — the collections were emptied, not inspected

| Test | Collection emptied | Result |
| --- | --- | --- |
| `..._every_legal_role_is_accepted...` | `policy_source_roles` | **guard bit** |
| `..._agree_on_all_forty_eight_combinations` | `reversibility` + `blast_radius` values | **guard bit** |
| `..._refuse_every_middle_band_case` | same | **guard bit** |
| `..._refuse_every_hard_case` | `assumption_allowed_forbidden_when.any_true_of` | **guard bit** |
| `..._whitespace_only_evidence_is_refused...` | `user_decision_fields` | **guard bit** |
| `..._an_invented_role_is_rejected...` | `policy_source_roles` | passes — iterates a literal tuple of four spellings, not the contract, so contract drift cannot empty it |

### T5 — every fix still holds

**10/10 by execution:** FR-1 (both named edges), FR-2, FR-3, FR-4, RI3-1, FR-5, TR4-1,
TR4-2, TR4-3. **3/3 positive controls:** complete decision → `{CLEAR}`, ordinary +
determining → `{CLEAR}`, safe + supporting → `{ASSUMPTION_ALLOWED}`.

### What this suite still cannot detect

Unchanged and restated so it is not lost: **no suite detects deletion of its own
assertion** (mutation N-6, iteration 4). The C-4 boundary above is the second such honest
limit, and it is now half-closed rather than merely documented.

### Commands after this pass

| Command | Result |
| --- | --- |
| `python3 scripts/validate_skills.py` | **PASSED (642 checks)** — unchanged; this pass added a test, not a validator check |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **1426 tests OK (skipped=6)** — was 1425; the +1 is the delegation test and **skips did not increase** |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `verify_package.py --archive …` | **PASSED (173 source files)**, archive verified |
| `git diff --check` | clean |

Protected surfaces re-checked and untouched: `VERSION`, `LICENSE`, `skill_policy.py` (so
`evaluate_invocation()` is unchanged, UD-3), `quality_profile.py`, `agent_profile.py`,
**both `SKILL.md` contracts**, `templates/**`, `reviews/**`, and `decision_policy.py`. This
pass edited one test file.

### Verdict

The TEST-phase guarantees hold on top of the TR4 correction. 27/27 mutations caught with a
verified-green control, the parity numbers reproduce exactly (48/0/2 and 9/109/0), the
call-closure device bites in both directions and does not over-block, and the nine-type
sweep found nothing new on the new surfaces. One bounded limit of that device was found by
executing it, and the half that could be closed without over-blocking has been closed.

---

## Correction — iteration 6 (Final Review attempt 4, FR-7)

FR-6 was fixed by IMPLEMENTATION iteration 7. FR-7 is the other half of the same story:
**the tests had the same defect as the code they were guarding.** They injected a boundary
element **name** mismatch for all ten bound codes and never a non-triggering **value**, so
1426 tests and 642 checks were green while FR-6 was live. And this document's own sweep
tables claimed more than the sweeps had covered.

### The evidence that matters: do the new tests actually catch FR-6?

The only proof is a disposable copy with the FR-6 fix reverted. Four reverts were run — the
whole `_grounds_defect` call, and each state branch on its own:

| FR-6 reverted | new FR-7 tests | the pre-existing name-only tests |
| --- | --- | --- |
| the whole call | **FAILED (30 failures)** | **OK** |
| `NEEDS_INPUT` branch only | **FAILED (19)** | **OK** |
| `CLEAR` branch only | **FAILED (4)** | **OK** |
| `CONFLICT` branch only | **FAILED (7)** | **OK** |

The right-hand column is FR-7's finding restated as a measurement: with FR-6 fully reverted,
`Requirement3BoundaryElementMustMatchTheCode` and `ReasonCodeLiveness` **still pass**. They
could not have caught it, and saying so required running them against the defect rather than
reasoning about them.

### What was added

Everything is derived from the contract rather than listed by hand, so a new bound code is
covered the day it is added, not the day someone remembers to extend a tuple.

| # | Test | Coverage | Co-located guard |
| --- | --- | --- | --- |
| 1a | `..._accepts_its_shipped_triggering_value` | POSITIVE, all 10 bound codes; each shipped value asserted to be genuinely triggering | `len(bound) == 10`, `checked == 10` |
| 1b | `..._rejects_a_non_triggering_value` | NEGATIVE, all 10, value derived per element kind and asserted non-triggering before use | `len(bound) == 10`, `checked == 10` |
| 1c | `..._absent_boundary_fact_is_judged_by_the_elements_kind` | 8 value-carrying elements: absence **rejected**; 2 `declared` elements: absence **accepted** | both partitions non-empty, `8 + 2 == 10` |
| 2 | `..._accepts_its_own_clause_and_rejects_the_others` | 3 CONFLICT codes × own clause and none (accept) × 2 other clauses each (reject) | `(accepted, rejected) == (6, 6)`, clause set equality |
| 2 | `..._citation_minimum_is_enforced_at_its_boundary` | exactly the minimum accepted, one fewer rejected, per code | `minimum == 2`, 3 codes |
| 3 | `..._each_clear_entry_predicate_has_a_satisfying_record` | POSITIVE, one per CLEAR predicate | predicate set equality with the contract |
| 3 | `..._each_near_miss_is_rejected` | NEGATIVE: supports-only, source-only decision, forbidden source, still-open item | `len(near_miss) == 4` |
| 3 | `..._declaring_no_grounds_at_all_remains_valid` | anti-over-blocking control (UD-1) | — |

**Item 1c is deliberately not uniform, and that is the point.** For a value-carrying element,
omitting the fact is the same defect as declaring it false. For a `declared` element, A4-1
row 1 makes naming it in `boundary_element` the declaration itself, so absence is *correct*.
Asserting one rule for both would be over-blocking dressed up as coverage.

### The new tests are not themselves vacuous

Each collection they iterate was actually emptied in a disposable tree:

| Collection emptied | Test | Result |
| --- | --- | --- |
| every `boundary_element` binding removed | 1a, 1b, 1c | **guard bit** (3/3) |
| `entry_clauses["CONFLICT"]` | item 2 | **guard bit** |
| `entry_conditions["CLEAR"]` predicates | item 3 | **guard bit** |

### No over-blocking

**18/18 valid fixtures pass**, checked before starting and again after. Every negative above
is paired with a positive control in the same class.

### Claims narrowed in this document — the other half of FR-7

FR-7 is also a finding against TEST.md: conclusions were wider than the evidence. Three
statements were narrowed **in place**, with the original analysis left intact so the record
is corrected rather than rewritten:

| Where | Was | Now |
| --- | --- | --- |
| iteration 3, type (c) | "**clean** — all six rejected at load" | "**clean at the contract/loader level**… says nothing about record validation" |
| iteration 4, type (c) | "clean" | "clean **for this helper only**" |
| iteration 5, type (c) | "clean" | "clean **for the entry predicates only**" |
| iteration 5, type (e) | "clean" | "clean **for `_is_empty` only**" |
| iteration 5, summary | "**Nothing new was found.** This is the first pass in the run where the nine-type sweep turned up no gap" | "**Nothing new was found on the surfaces listed above**", plus an explicit note that the correct reading is **nothing new on four surfaces, one surface never examined** |

A scope correction was added under each of the three sweep tables naming what was **not**
covered: `validate_record()`'s per-reason-code evidence path — the Decision Record surface,
where FR-6 was live and unfound while those tables were being written.

**The honest summary of the run's sweeps:** each was executed and each was accurate about
the surface it was applied to. None of them ever covered the surface FR-6 was on, and the
tables did not say so. "Clean" without a named scope is the same defect as a green suite
that guards nothing — which is the failure mode this repository keeps producing, now
recorded against my own reports rather than only against the code.

### Commands after this pass

| Command | Result |
| --- | --- |
| `python3 scripts/validate_skills.py` | **PASSED (642 checks)** — unchanged; this pass added tests, not validator checks |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **1441 tests OK (skipped=6)** — was 1433; the +8 are the FR-7 tests and **skips did not increase** |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `verify_package.py --archive …` | **PASSED (173 source files)**, archive verified |
| `git diff --check` | clean |

Untouched, verified by empty diff: `VERSION`, `LICENSE`, `skill_policy.py` (UD-3),
`quality_profile.py`, `agent_profile.py`, `decision_policy.py`, both `SKILL.md` contracts,
`templates/**` and `reviews/**`. This pass edited one test file and this document — no
contract or evaluator semantics changed, and no defect was found that would have required
reporting rather than fixing.

---

## Correction — iteration 7 (Final Review attempt 5, FR-9)

FR-9 was raised as "the tests missed FR-8". By the time it reached me, implementation had
run three more rounds and **four** value-domain defects had come and gone — FR-8, RI8-1,
RI9-1, and the two RI9-1's lens exposed. Every one of them was alive while 1400+ tests and
642 checks passed. The common cause is not four missing tests; it is that coverage was
written **per defect, after the fact**, instead of **per value position, in advance**.

### The register

`Fr9EveryValuePositionHasADomainProbe` gives every position where the contract carries a
value one row: a violating value, a valid value, whether the position is checked, and the
**message its own rule produces**.

| # | position | classified | how it is pinned |
| --- | --- | --- | --- |
| 1 | element value, `enum` | checked | rejected, message `outside its closed value set` |
| 2 | element value, `boolean` | checked | rejected, `is a boolean element` |
| 3 | element value, `declared` | checked | rejected, `is a declared element` |
| 4 | element value, `citations` | checked | rejected, `is a citations element` |
| 5 | element value, `user_decision` | checked | rejected, `is the authority boundary` |
| 6 | element value, `policy_source` | **not checked** | **violation asserted ACCEPTED** |
| 7 | `policy_source.kind` | checked | rejected, `policy_source kind` |
| 8 | `policy_source.role` | checked | rejected, `unknown policy_source role` |
| 9 | `policy_source.locator` shape | checked | rejected, `non-empty textual locator` |
| 10 | `conflict_clause` | checked | rejected, `conflict_clause` |
| 11 | `reason_code` | checked | rejected, `reason_code from the closed set` |
| 12 | state name | checked | rejected, `unknown decision state` |
| 13 | `user_decision.source` | checked | rejected, `not evidence of user authority` |
| 14 | `user_decision.where_recorded` | checked | rejected, `'where_recorded' must be text` |
| 15 | `user_decision.resolves` | checked | rejected, `'resolves' must be text` |
| 16 | `citations` entries | checked | rejected, `citation 0 must be non-empty text` |
| — | locator **existence** | **not checked** | **acceptance asserted** (this layer does no I/O) |

**Positions classified "not checked" are pinned by asserting the violation is ACCEPTED.**
That converts a limit from an assumption a reader might make in either direction into a
decision on the record — and it fails if someone starts checking it without updating the
table.

**The count is guarded against a figure derived from the contract**, not the literal 16:
one row per element kind, three for the `policy_source` object, one per `user_decision`
field, four singletons. Adding a kind or a field fails this test until the new position has
a row. That is the structural part: the register cannot silently go stale the way the
per-defect tests did.

### Proof: reverted copies

The only evidence that a test would have caught a defect is watching it fail against that
defect. Control first — the unmodified tree is green for the register.

| fix reverted in a disposable copy | register |
| --- | --- |
| **FR-8** (boolean type check) | **FAILED (4)** |
| **RI8-1** (authority value domain) | **FAILED (2)** |
| **RI9-1** (locator shape) | **FAILED (2)** |
| `where_recorded`/`resolves` text check | **FAILED (2)** |
| citation entry text check | **FAILED (2)** |
| enum membership check | **FAILED (1)** |
| `policy_source.kind` check | **FAILED (2)** |
| `policy_source.role` check | **FAILED (1)** |
| `conflict_clause` check | **FAILED (2)** |
| `_domain_defect` never called | **FAILED (9)** |

**10/10 caught, control green.**

### A row of mine that passed for the wrong reason

The register's **first** version asserted only *"was it rejected?"* — and under a reverted
enum check it stayed **green**. An out-of-set `reversibility` is rejected by the
ASSUMPTION_ALLOWED entry condition whether or not the enum check exists, so the row was
never probing the enum domain at all. Three `user_decision` rows had the same flaw: all
three produced one generic CLEAR-grounds message that did not distinguish them.

Found by reverting, not by reading — which is the whole argument for step 3 of this task.
The fix is that **every row now anchors on the message its own rule produces**, so a
rejection by some other rule fails the assertion. That is exactly the defect class FR-7
named ("green but guards nothing"), reproduced inside the test written to prevent it, and
caught before it shipped rather than three Final Reviews later.

### Claims narrowed again

Iteration 6 narrowed the (c)/(e) sweep verdicts to "the surface swept". Four defects
surfaced after that, so the accurate scope is narrower still, and both tables now carry it:

> **(c) and (e) were swept over the CONTRACT and the shared helpers, never over the VALUE
> DOMAINS a declared fact may carry.** That axis had no coverage until this register.

This is the second time these sentences have been narrowed. Recording it plainly: the
original claims were wide, iteration 6's correction was still wide, and the reason both were
wrong the same way is that a sweep's scope was described by *what it examined* while the
verdict was written as though it covered *the subject*.

### No over-blocking

Every row carries a valid value, asserted to be **accepted** — 16/16. An implementation that
refused everything would pass the negative test and fail the positive one. **18/18 valid
fixtures** pass, counted before starting and after.

### Commands after this pass

| Command | Result |
| --- | --- |
| `python3 scripts/validate_skills.py` | **PASSED (642 checks)** — unchanged; this pass added tests, not validator checks |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **1469 tests OK (skipped=6)** — was 1463; the +6 are the register and **skips did not increase** |
| `python3 scripts/verify_package.py` | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` |
| `verify_package.py --archive …` | **PASSED (173 source files)**, archive verified |
| `git diff --check` | clean |

Untouched, verified by empty diff: `VERSION`, `LICENSE`, `skill_policy.py` (UD-3),
`quality_profile.py`, `agent_profile.py`, **`decision_policy.py`**, both `SKILL.md`
contracts, `templates/**` and `reviews/**`. This pass edited one test file and this
document; no contract or evaluator semantics changed, and no new defect was found that
would have required reporting rather than fixing.
