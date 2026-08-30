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

## Review Feedback Resolution

```text
FINDING RI-N1: RESOLVED   both options taken -- renamed for accuracy AND a behavioural
                          characterization test added; evaluate_invocation() untouched
```

No new question requires user authority. UD-1 through UD-4 are unchanged and unreinterpreted: the
decision record section is still optional, requirement 5 is still permission-level with the limit
stated, `skill_policy.py` is still untouched, and the code set is still 18.

STATUS: COMPLETE
