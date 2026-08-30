# Worker Result

STATUS: COMPLETE

Phase: PLAN · Iteration 1 · Run `run_3233a1469e97` · risk `high`
Ticket: **Jira OS-28 "Define Bounded Autonomy Decision Policy Contract"** (P0/High)
Branch: `feat/os-28-bounded-autonomy-policy` (base `main` @ `c264e79`)
Scope of this document: **planning only. No repository source file was created or modified.**
The only file this Task wrote is this artifact.

Inputs consumed: `ANALYSIS.md` (1372 lines, gate PASS), `REVIEW_ANALYSIS.md` (RA-1…RA-6 all
resolved), `USER_DECISIONS.md` (UD-1/2/3). ANALYSIS's settled sections — A3-1/A3-1a, A3-2 with
T-F1…T-F6, A4-0/A4-1, A5-2, A5-3, A5-4, INV-3/INV-4/INV-5, A6 — are **carried forward, not
redesigned**. Where this plan departs from an ANALYSIS recommendation it says so and gives the
reason.

---

## Summary

The work is **additive and contract-only**: one new loader module, one new test module, one new
fixture directory, a new sub-object inside a JSON block both Skills already share, an optional
section in prose files that are already byte-shared, and one new validator function. Nothing wires
a runtime gate, and P8 gives the checkable proof of that.

Five things drive the plan's shape:

1. **The machine block goes in the shared ` ```policy-contract ` JSON (OQ-4(a) confirmed, P7-1).**
   Two reasons, both checked: the block is already asserted deep-equal between the Skills
   (`scripts/validate_skills.py:1118-1122`), so requirement 8 costs no new machinery; and the block
   already carries a sub-object that no Python reads — I grepped `"project_source"`,
   `"user_source"`, `"source_precedence"`, `"merge"` across `scripts/*.py` and found **no reader**
   — so a declarative sub-object is an established shape there, not a novelty.
2. **A Python expected-constant mirrors the block, because byte/deep equality alone does not catch
   simultaneous deletion.** Deep-equal fails when the Skills *diverge*; it passes happily when a key
   is deleted from *both*. The repository's answer to that is the `RISK_CONTRACT` /
   `AGENT_PROFILE_CONTRACT` idiom — a duplicated dict in `validate_skills.py` compared for equality.
   P3 adopts it. This is the single most important structural decision in the plan and it is not in
   ANALYSIS; the reason is given in P3-2.
3. **Rejection-only testing is what let RA-4 and RA-5 through.** Every reason code gets a *positive*
   three-check liveness assertion (P4-B), not just negative tests.
4. **One question goes to the user: OQ-9.** ANALYSIS itself says a PLAN phase should put it the way
   UD-1/2/3 were put. Five other open questions (OQ-1/3/4/5/8) are technical and are confirmed here
   with reasons (P7). Deciding OQ-9 myself would be the exact failure this ticket exists to prevent.
5. **Two concrete regression traps are already identified and verified** — a test-harness copy list
   and a parser collision — and both have a mitigation in P9 rather than being discovered later.

---

## Analysis

### Baseline (measured, with the commands that produced the numbers)

Run on branch `feat/os-28-bounded-autonomy-policy`, working tree with no tracked source
modification. These are the regression reference for every later phase.

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **Skill validation PASSED (501 checks)**, exit 0 |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1269 tests in 290.636s — OK (skipped=6)**, exit 0 |

Both figures are from **this PLAN task's own runs**, not quoted from `ANALYSIS.md`. The unittest run
emitted one pre-existing `DeprecationWarning: invalid escape sequence '\s'` at `<unknown>:1214`,
which is present on the untouched baseline and is therefore not attributable to OS-28; it is
recorded here so a later phase does not mistake it for a new defect.

Regression rule for IMPLEMENTATION and TEST: **501 checks and 1269 tests are floors, not targets.**
Both numbers must rise (new checks, new tests) and neither suite may report a failure. A drop in
either count is a regression signal even if the suite still passes.

---

### P1. Implementation targets — files, role, and why there

**New files (3).**

| # | path | role | why here |
|---|---|---|---|
| N1 | `scripts/decision_policy.py` | The loader and the only parser of the decision-policy contract. Exposes the closed vocabularies (states, reason codes, boundary elements, forbidden-authority list), the transition matrix, the per-state required-evidence map, and the INV-3/4/5 checks. Fail-closed on schema version. | `scripts/` is where every other policy loader lives (`skill_policy.py`, `quality_profile.py`, `agent_profile.py`), and `release_manifest.py:41 INCLUDED_ROOTS` already contains `scripts`, so it packages with **no manifest edit**. A separate module rather than an addition to `skill_policy.py` because that module is the *invocation-time* parameter gate and this is *phase-time* policy; keeping them apart is what lets P8 prove nothing executes the new contract. |
| N2 | `scripts/test_decision_policy.py` | Unit tests for N1 — requirements 1-7 and 9, plus the 17/18-code liveness suite. | `test_*.py` under `scripts/` is what CI discovers (`.github/workflows` runs `python3 -m unittest discover -s scripts -p 'test_*.py'`). |
| N3 | `scripts/fixtures/decision_policy/` | Positive and negative record fixtures as JSON — `valid/` (one per reason code, the A5-4 minimal fixtures) and `invalid/` (one per forbidden transition, per missing-evidence case, per reject-list entry, per malformed schema). | Follows `scripts/fixtures/final_review_eval/`, the existing fixture-directory precedent. `release_files()` walks `scripts` with `rglob`, so these ship in the archive like the existing fixtures do — no manifest edit, but see P9-V4. |

**Modified files (6 groups).**

| # | path(s) | change | why here |
|---|---|---|---|
| M1 | `orca-worker-reviewer-orchestration/SKILL.md`, `orca-worker-reviewer-loop/SKILL.md` — the ` ```policy-contract ` JSON block | add one `decision_policy` sub-object carrying its own `schema_version`, the four states, the transition matrix, the boundary-element value sets, the reason codes, the reject list, and the per-state required-evidence map | the block is deep-equal asserted across both Skills (P3-1). Its own `schema_version`, **not** the block's top-level one: the top-level key governs the invocation-time contract and UD-3 keeps that path out of scope, so the decision policy must version independently or requirement 9 would collide with UD-3. |
| M2 | `orca-worker-reviewer-{orchestration,loop}/SKILL.md` — prose | the human-readable half: what each state means, why `NEEDS_INPUT ≠ CONFLICT`, why an answered question is `CLEAR` and not `ASSUMPTION_ALLOWED` (T-F2/T-F3), why nothing lifts INV-4 | OS-27's dual structure (ANALYSIS A4-2): code enforces, prose explains *why*, so a blocked Worker does not route around the block. Anchored by named prose constants (P3-3). |
| M3 | `orca-worker-reviewer-{orchestration,loop}/templates/{analysis,plan,design,implementation,test,bugfix,refactoring}.md` — 7 files × 2 Skills | UD-1's **optional** decision record section in the Result Contract, plus the Worker-facing "how to classify" guidance | byte-equal asserted (`validate_skills.py:800-822`), so this is **one edit set applied twice**, not fourteen edits. All 7 phases, not 5 — see P6-2. |
| M4 | `orca-worker-reviewer-{orchestration,loop}/reviews/common.md` | Reviewer misclassification-judgment rules, per reason code; and how to review the optional section when present | `reviews/common.md` is what the phase Reviewer actually reads, so a misclassification rule anywhere else has no effect. Also byte-equal asserted. |
| M5 | `scripts/validate_skills.py` | new `validate_decision_policy_contract()`, registered in `main()`; imports N1 rather than re-parsing | the precedent is exact: `validate_risk_profile_contract` imports `load_risk_contract` from `skill_policy` so "the runtime evaluator and this validator cannot disagree about the block" (that function's own docstring). |
| M6 | `scripts/test_validate_skills.py` | drift regression tests, **and** `decision_policy.py` added to the disposable-tree copy list | mandatory, not optional — see P9-V1. |
| M7 | `CHANGELOG.md` | one entry | repository convention: `git log --oneline -6 --name-only -- CHANGELOG.md` shows every recent feature PR (#23, #21, #20, #18, #17, #16) touching it. |

**Not touched, deliberately:** `run_logging.py` (`RUN_STATUS_VALUES`), `skill_policy.py`,
`agent_profile.py`, `quality_profile.py`, `orca_runtime_harness.py`, `review_isolation.py`,
`e2e_harness.py`, `final_review_eval.py`, `release_manifest.py`, `VERSION`, `docs/ROADMAP.md`.
P8 turns this list into a checkable claim.

**On OQ-4, since ANALYSIS recommended rather than decided it.** Adopted as recommended, option (a).
The deciding reason stays the one ANALYSIS gave — grammar fit: the anchor-block grammar is flat
(`LIFECYCLE_CONTRACT_LINE_PATTERN` is `([A-Z][A-Z0-9_]*) = (.+)` with values split on `,` and each
value matched against `[a-z][a-z0-9_]*`), and this contract is nested (a 4×4 transition matrix, a
per-state evidence map, a per-element value-set map). Flattening produces one key per cell against
existing `*_MAX_LINES` budgets of 4/14/17/18/20. Confirming (a) also makes **OQ-8 moot** (P7-5).

---

### P2. Order and dependencies

```text
S1  contract data        M1  the decision_policy JSON sub-object, in BOTH SKILL.md files
        |                    (identical bytes; deep-equality is the guard, not the author)
        v
S2  loader               N1  scripts/decision_policy.py -- parses S1, fail-closed on version
        |
        +---------------------------+
        v                           v
S3  loader tests + fixtures     S4  validator            M5 validate_skills.py imports N1
    N2, N3                          |
        |                           v
        |                       S5  validator regression tests   M6 (incl. the copy-list fix)
        |                           |
        v                           v
S6  prose             M2, M3, M4  SKILL.md prose + 7 templates x2 + reviews/common.md x2
        |                          validator prose anchors land with this step
        v
S7  mutation verification    P5 -- run the mutation list against the finished suite
        |
        v
S8  baseline re-run + package verification    validate_skills, unittest, verify_package,
                                              build_release + archive verify
```

Why this order, edge by edge:

| edge | reason |
|---|---|
| S1 → S2 | the loader has nothing to parse until the block exists. Writing the loader first invites a parser shaped around an imagined block rather than the real one — the RA-4 failure mode one level up. |
| S2 → S4 | M5 **imports** N1. Building the validator first would mean writing a second parser, which is the drift the `load_risk_contract` precedent exists to prevent. |
| S2 → S3 | tests bind to the loader's real interface. |
| S4 → S5 | a regression test that mutates a Skill and asserts a *named* failure needs that named check to exist. |
| S1 → S6 | prose indexes the vocabulary. Writing prose against a still-moving contract is how a prose anchor ends up describing something the block no longer says. |
| S5, S6 → S7 | mutation verification tests the *whole* guard set; running it earlier only proves the parts that exist. |
| S7 → S8 | the final baseline must be taken after the last change, or it certifies an intermediate tree. |

**One-commit-per-step is not required, but S4 and M6's copy-list fix must be in the same commit**
(P9-V1): a commit where `validate_skills.py` imports `decision_policy` but the test harness does not
copy it leaves 124 validator regression tests failing on an import crash with empty stdout.

---

### P3. Two-Skill drift prevention (validation requirement 8)

Four layers, three of them existing mechanisms reused rather than built.

**P3-1. Machine contract → the existing deep-equality assertion.** The `decision_policy` sub-object
lives inside the block that `validate_machine_readable_contracts()` already compares whole:

```python
    if len(contracts) == len(SKILL_DIRS):
        validation.check(
            contracts[0][1] == contracts[1][1],
            "machine-readable policy contracts differ between skills",
        )
```

Verified at `scripts/validate_skills.py:1118-1122` (the assertion itself is line 1120). Any
divergence in any nested key fails with that message. **No new parity machinery.**

> Citation correction, made deliberately rather than silently: `ANALYSIS.md` A6 row 8 cites this as
> `validate_skills.py:1122-1126`. Re-read at PLAN time, the block is **1118-1122**. The mechanism
> ANALYSIS described is exactly right; only the line numbers were off by one block. This plan uses
> the re-verified numbers.

**P3-2. A Python expected-constant, because deep-equality has a blind spot.** Deep-equality proves
the two Skills *agree*. It cannot detect that they agree on something **wrong** — delete a reason
code from both blocks and the check still passes. The repository already solved this for its anchor
contracts: `RISK_CONTRACT`, `AGENT_PROFILE_CONTRACT`, `QUALITY_PROFILE_CONTRACT` and the others are
duplicated as Python dicts in `validate_skills.py` and asserted key-equal *and* value-equal against
the parsed block.

So `validate_decision_policy_contract()` carries `DECISION_POLICY_CONTRACT` as a module constant and
asserts:

```text
set(parsed) == set(DECISION_POLICY_CONTRACT)     keys drifted
parsed == DECISION_POLICY_CONTRACT               values drifted
0 < block_size <= DECISION_POLICY_MAX_LINES      budget (R-4)
```

This is the one structural addition this plan makes beyond ANALYSIS, and the reason is the blind
spot above. Mutation M3 in P5 is its dedicated proof.

**P3-3. Prose → byte-equality plus named anchors.** `validate_shared_directories()`
(`scripts/validate_skills.py:800-822`) asserts identical file-name sets and identical bytes for
`templates/` and `reviews/` across both Skills. That covers M3 and M4 with no new code. It has the
same blind spot as P3-1 — deleting a sentence from both copies passes — so the sentences the machine
block only *indexes* get named prose-anchor constants, exactly as
`AGENT_PROFILE_SAFETY_ALL_ENTRIES_PROSE_ANCHOR` and `LOOP_AGENT_PROFILE_PROSE_ANCHORS` already do.
Anchors to define at minimum: the T-F2 sentence, the "nothing lifts INV-4" sentence, the
`NEEDS_INPUT ≠ CONFLICT` sentence, and UD-1's optionality sentence.

M2's SKILL.md prose is **not** byte-shared (the two SKILL.md files legitimately differ), so its
anchors are checked in **both** files by iterating `SKILL_DIRS` — the pattern
`validate_agent_profile_contract` already uses.

**P3-4. A drift regression test.** `test_validate_skills.py` copies the repo to a disposable tree,
mutates one Skill, and asserts the validator fails with the named message — following
`test_workflow_output_contract_drift_fails` and `test_shared_template_drift_fails`.

---

### P4. Test plan

**P4-A. The ten validation requirements → the test that proves each.**

| # | requirement | test | location | notes |
|---|---|---|---|---|
| 1 | reject any state outside the four | `test_unknown_state_is_rejected` — feed a fifth state to the loader; plus a validator check that the block's state list equals `DECISION_STATES` | N2 + M5 | closed tuple; loader raises |
| 2 | reject invalid transitions | one test **per forbidden cell**, table-driven from the matrix so a new forbidden cell without a test is impossible: T-F1…T-F6 at minimum, including the unconditional `NEEDS_INPUT\|CONFLICT → ASSUMPTION_ALLOWED` prohibition **with** a valid `user_decision` present (the case iteration 2 got wrong) | N2 | negative fixtures in `N3/invalid/transition/` |
| 3 | no reason-less use of the three non-`CLEAR` states | (i) one negative test per state with `reason_code` omitted; (ii) **P4-B's positive liveness suite** | N2 | (ii) is the RA-4/RA-5 lesson; see below |
| 4 | high-impact irreversible fixture is not weakened | fixture `irreversible` + `blast_radius=repository`; assert `ASSUMPTION_ALLOWED` is **not permitted** in three variants: bare (only `{NEEDS_INPUT, CONFLICT}` permitted), **plus** a `policy_source{determines}`, **plus** a `user_decision` (both → `CLEAR`, never `ASSUMPTION_ALLOWED`) | N2 + `N3/invalid/inv4/` | the two "plus" variants are A4-0's anti-weakening cases — INV-4 has no exception |
| 5 | safe fixture not forced to `NEEDS_INPUT` | fixture reversible + scope-local + no boundary element true + `policy_source{supports}`; assert `ASSUMPTION_ALLOWED` is **permitted** and that the contract does **not** require `NEEDS_INPUT` | N2 + `N3/valid/` | **UD-2: permission level only.** The test name and docstring must both say so, and the PR must state the limit — a contract-level test cannot detect a real model's over-escalation. Not reported as solved. |
| 6 | confidence is never authority | one test **per reject-list entry** (`model_confidence`, `timeout`, `no_response`, `worker_reviewer_agreement`, `recommended_default`): a `NEEDS_INPUT → CLEAR` transition citing it as `user_decision.source` is rejected | N2 | five tests, one per entry, so adding an entry without a test is visible |
| 7 | risk change does not change authority | (i) parametrize one fixture over `risk ∈ {low, medium, high}` and assert an **identical permitted-state set**; (ii) validator check that no `decision_policy` key or value names a risk level or a profile name | N2 + M5 | mirrors `RISK_QUALITY_PROFILE_AXIS = independent_never_read_or_gate_on_each_other` |
| 8 | two-Skill drift fails | P3's four layers; the proof is the regression test that mutates one Skill's copy and asserts the named failure | M6 | plus mutation M2/M3 in P5 |
| 9 | malformed / unknown schema version fails closed | `SUPPORTED_SCHEMA_VERSIONS` in N1 that **raises** — following `quality_profile.py:521-528` and `agent_profile.py:462-467`, **not** `load_risk_contract`'s return-`None` convention (R-5). Tests: unknown version raises; malformed JSON raises; missing version key raises | N1 + N2 | **UD-3: scope is OS-28's own loader only.** `evaluate_invocation()`'s missing top-level gate is a pre-existing defect, recorded as a follow-up candidate, neither fixed nor worsened, and **must not** be described as addressed. |
| 10 | no lifecycle / package regression | re-run all four CI steps and compare against the P-baseline: `validate_skills.py` (≥501 checks, PASS), unittest (≥1269 tests, OK), `verify_package.py`, `build_release.py` + archive verify | S8 | `INCLUDED_ROOTS` already covers `scripts`; `required_skill_paths()` enumerates existing template/review paths only, so editing them in place adds no manifest path |

**P4-B. The 17/18-code liveness suite — the RA-4/RA-5 lesson made executable.**

RA-4 was an evidence failure and RA-5 an entry-condition failure, and **a rejection-only suite would
have passed with both defects in place**. So each reason code gets a positive test asserting all
three checks A5-4 defines:

```text
C1  the fixture's cited basis satisfies the WORDING of that state's entry condition
C2  every required evidence field in A5-3 is populated -- not just the headline one
C3  no INV-3 / INV-4 / INV-5 violation
```

Table-driven over the code list so a code added without a fixture fails collection, not silently.
Counts: **17 codes confirmed by ANALYSIS A5-4**, **+1 if OQ-5(c) is adopted** (P7-4:
`unclassifiable_decision`, making 18), **±1 pending OQ-9** (`requirement_vs_repository_policy`,
suspended). The suite must assert the code-list length against the contract constant so the count
cannot drift unnoticed.

**P4-C. Fixture placement.**

```text
scripts/fixtures/decision_policy/
    valid/          one JSON record per reason code -- the A5-4 minimal fixtures (P4-B)
    invalid/
        transition/     one per forbidden cell (req. 2)
        evidence/       one per missing required field (req. 3)
        inv4/           high-impact + authorization variants (req. 4)
        authority/      one per forbidden-authority reject-list entry (req. 6)
        schema/         unknown version, malformed JSON, missing version (req. 9)
```

JSON rather than Python literals so the same fixtures can be reused by OS-29/OS-32 without importing
this ticket's test module — and so a fixture diff is readable in review.

---

### P5. Mutation verification plan

Purpose: prove the tests actually **catch** a weakening of the contract, rather than merely passing
alongside it. Procedure per mutation: apply it to a scratch copy, run
`python3 scripts/validate_skills.py` and `python3 -m unittest discover -s scripts -p 'test_*.py'`,
record which named check or test fails, then revert. A mutation that produces **no** failure is a
gap in the suite and must be closed before the phase completes.

| id | mutation | must be caught by |
|---|---|---|
| M-1 | add a fifth state to the `decision_policy` block in both Skills | validator keys-drifted check (P3-2) + requirement 1 |
| M-2 | delete one reason code from **one** Skill only | deep-equality, "machine-readable policy contracts differ between skills" |
| M-3 | delete the same reason code from **both** Skills | `DECISION_POLICY_CONTRACT` values-drifted check — **this is the mutation that justifies P3-2**; if only M-2 is caught, the blind spot is real |
| M-4 | flip `NEEDS_INPUT → ASSUMPTION_ALLOWED` from forbidden to allowed | requirement 2, the T-F2 case |
| M-5 | make T-F2 conditional on a `user_decision` (iteration 2's original error) | requirement 2's "with a valid user_decision present" variant |
| M-6 | add an authorization exception to INV-4 for `monetary_cost` | requirement 4's two anti-weakening variants |
| M-7 | remove `model_confidence` from the forbidden-authority reject list | requirement 6's per-entry test |
| M-8 | make one `NEEDS_INPUT` required-evidence field optional | requirement 3 |
| M-9 | change the loader to return `None` instead of raising on an unknown schema version (the R-5 fail-open) | requirement 9 |
| M-10 | add a risk level as a key or value inside `decision_policy` | requirement 7(ii) validator check |
| M-11 | delete a named prose anchor sentence from **both** Skills | P3-3 prose-anchor check |
| M-12 | delete one reason code's fixture while leaving the code in the contract | P4-B liveness suite (count assertion) |
| M-13 | change UD-1's optional section to be treated as required by the validator | P6-3's optionality test |
| M-14 | edit one `templates/analysis.md` copy only | `validate_shared_directories` byte-equality |

M-3, M-5, M-12 and M-13 are the ones with no existing analogue in the repository's test suite; the
rest have a precedent the new test can be modelled on. Results go in a table in the IMPLEMENTATION or
TEST artifact — **a mutation that nothing catches is a blocking finding against this plan's own
test design**, not a note.

---

### P6. UD-1 — the optional decision record section

UD-1 (user decision): add an **optional** decision record section; its absence is **not** a contract
violation; the state / reason code / evidence format is validated **only when the section is
present**.

**P6-1. Files.** 7 templates × 2 Skills + `reviews/common.md` × 2 = 16 files, but only **8 distinct
contents**, because `templates/**` and `reviews/**` are byte-shared. Editing procedure: edit the
orchestration copy, copy the file verbatim to the loop Skill, let `validate_shared_directories`
prove they match. Never hand-edit both.

**P6-2. All 7 phases, not 5.** `docs/ROADMAP.md`'s Bounded Autonomy Model names ANALYSIS, PLAN,
DESIGN, IMPLEMENTATION, TEST and Final Review. BUGFIX and REFACTORING are specialized phases that
also produce work and can meet a decision, and exempting them would create a boundary gap with no
stated reason. The section is optional, so covering all 7 costs nothing at runtime. This is a plan
decision, not a UD-1 term.

**P6-3. How the validator expresses "optional".** Precisely:

```text
DOES check    the OPTIONALITY SENTENCE is present in the shared templates and reviews/common.md
              (a prose anchor -- so "optional" cannot be silently deleted or upgraded to required)
DOES check    the section's FORMAT contract -- state vocabulary, reason-code set, evidence field
              names -- matches the decision_policy block, so a present section cannot use a
              different vocabulary
DOES NOT      assert that any artifact, template output, or Result Contract instance contains
              the section
```

A negative test locks this in: a fixture Result Contract **without** the section must validate
clean. Mutation M-13 is its adversarial counterpart.

**P6-4. Parser-collision constraint, verified empirically.** `workflow_contract.py` scans SKILL.md,
`templates/implementation.md` and `reviews/common.md` with two regexes. I ran the candidate section
lines against the real patterns:

| candidate line | `CHOICE_LINE` | `REVIEW_VERDICT_LINE` |
|---|---|---|
| `DECISION_STATE: CLEAR \| ASSUMPTION_ALLOWED \| NEEDS_INPUT \| CONFLICT` | no match | **no match** |
| `DECISION_STATE: CLEAR \| CONFLICT` | **MATCHES** | no match |
| `DECISION_RECORD: PRESENT \| ABSENT` | **MATCHES** | no match |
| `REASON_CODE: repository_policy` | no match | no match |

Command: a Python snippet importing `CHOICE_LINE` and `REVIEW_VERDICT_LINE` from
`scripts/workflow_contract.py` and testing each string. The four-state line is safe because
`REVIEW_VERDICT_LINE`'s value pattern is `[A-Z]+`, which excludes the underscore in
`ASSUMPTION_ALLOWED`. **Constraint for implementation: never write a two-valued all-caps
`FIELD: A | B` line into these files.** Such a line matches `CHOICE_LINE`; today `_find_choice`
filters by expected value set so `{CLEAR, CONFLICT}` is ignored, but the margin is one careless
value name wide, and a collision would surface as a confusing `inconsistent fields` error rather
than a clear one. Use the four-state form or a non-`|` form.

---

### P7. Open questions — confirmed here vs. raised to the user

The honest split. Five are technical choices with recorded reasons and are **confirmed**; one
decides how often the workflow pauses for a user and is **raised**.

#### Confirmed in PLAN (with reasons)

| id | decision | reason |
|---|---|---|
| **OQ-1** | **(b)** — a decision state is carried **per decision item**, with a derived per-check aggregate ordered `CONFLICT > NEEDS_INPUT > ASSUMPTION_ALLOWED > CLEAR` | (a) makes T-F6 and per-item evidence undefinable; (c) contradicts the ROADMAP's "A check … produces one of four states". (b) satisfies both. Purely structural — it changes no authority. |
| **OQ-3** | **(a)** — decision states are a **separate axis** from `RUN_STATUS_VALUES`, Worker `STATUS`, and `REVIEW_VERDICT`; OS-28 changes none of them | confirming (a) means *changing nothing*, which needs no authority. (b) would collapse "budget exhausted, gave up" into "waiting for a decision, will resume"; (c) is OS-31's scope. The contract states the separation so OS-31 is not foreclosed. |
| **OQ-4** | **(a)** — the shared ` ```policy-contract ` JSON hosts the machine block | grammar fit (nested contract vs the anchor block's flat `KEY = token, token`) plus zero new parity machinery. See P1. |
| **OQ-5** | **(c)** — closed set **plus** `unclassifiable_decision`, which itself forces `NEEDS_INPUT` | this is a conclusion, not a preference: (a) leaves a novel reason with no expressible code, blocking a live run; (b)'s `OTHER` becomes the within-run default, which A5-1 already rejects. (c) is the only option with no named defect, and it fails safe in the direction `docs/ROADMAP.md` Architecture Principle 4 requires ("Fail closed when provenance is uncertain"). Cost: the confirmed code set becomes **18**. |
| **OQ-8** | **moot** — no `####` heading is added to the loop Skill | follows from OQ-4(a). If OQ-4 were ever revisited, OQ-8 returns with it. |
| **R-7** (ANALYSIS risk, "decide once in PLAN") | contract **prose uses `docs/ROADMAP.md`'s wording** for the three boundary elements the ticket words differently (`contradiction`, `cost`, `existing project policy`); machine tokens stay snake_case (`monetary_cost`, …) | the ROADMAP is the published artifact, so adopting its wording needs **no ROADMAP edit** and creates no "which is authoritative" question later. Zero-risk direction. |

#### Raised to the Coordinator for the user — **one question**

**OQ-9 — the disposition of `requirement_vs_repository_policy`.** ANALYSIS states outright that "a
PLAN phase should put it to the user the way UD-1/2/3 were put", and I agree: this decides whether a
whole class of policy conflict **pauses for the user or proceeds without one**, which is decision
authority, not implementation detail. Deciding it myself would be precisely the failure this ticket
defines. Question, options, impact and recommendation are carried verbatim from ANALYSIS A8 and
restated in this document's *Review Feedback Resolution* section for the Coordinator to lift.

Everything else in the plan is **independent of OQ-9's answer**: it changes the reason-code count by
±1 and adds at most one fixture and one liveness row. No other file, test, or invariant moves. So
IMPLEMENTATION can proceed on the 18 confirmed codes and add the 19th only if the user picks (a) or
(b).

---

### P8. Proving OS-29 / OS-30 / OS-31 were not built

The claim to prove: **OS-28 defines what a decision state means and when it is legal; it does not
run the check, ask the question, or wait.** Four checkable proofs, all to be run and pasted into the
PR description rather than asserted:

| # | proof | command |
|---|---|---|
| 1 | **nothing executes the contract.** The only importers of the new loader are the validator and its own tests — no runtime module | `grep -rn 'decision_policy' scripts/ orca-worker-reviewer-*/` — expected: `decision_policy.py`, `test_decision_policy.py`, `validate_skills.py`, `test_validate_skills.py`, and SKILL.md/prose only |
| 2 | **no OS-30/31 vocabulary entered the tree** | `grep -rniE 'waiting_for_input\|HumanApprovalPort\|durable pause\|resume from\|orchestration ask\|slack\|approval adapter' <changed files>` — expected: matches only inside the *out-of-scope* prose that names them as **not** built |
| 3 | **no lifecycle surface moved** | `git diff --stat -- scripts/run_logging.py scripts/orca_runtime_harness.py scripts/review_isolation.py scripts/e2e_harness.py scripts/task_context.py` — expected: **empty**. Covers `RUN_STATUS_VALUES` specifically |
| 4 | **no gate was wired** | `git diff --stat` over the whole tree, shown in the PR: the changed set is exactly P1's list. No phase-dispatch, task-graph, or gate-evaluation call site appears |

Proof 2 needs care in wording: the out-of-scope table legitimately *names* these terms. The PR should
show the grep and state that every hit is a scope statement, not an implementation — with the file
and line of each hit, so the reader can check rather than trust.

---

### P9. Risks and rollback

| id | risk | evidence it is real | mitigation |
|---|---|---|---|
| **V1** | **Test-harness copy list.** `scripts/test_validate_skills.py` copies a fixed tuple of scripts into a disposable tree and runs the validator as a subprocess there. If `validate_skills.py` imports `decision_policy` and that file is not copied, **every** validator regression test fails on an import crash with empty stdout. | Verified at `scripts/test_validate_skills.py:49-63`; the tuple's own comment records this exact failure happening for OS-4: *"a missing dependency here is an import crash with an empty stdout rather than the named failure a test is asserting on."* | add `"decision_policy.py"` to that tuple **in the same commit** as M5's import (P2). Listed as a required step, not a follow-up. |
| **V2** | **Parser collision** in `workflow_contract.py` when new lines land in `templates/implementation.md` or `reviews/common.md`. | Measured, not reasoned — see the P6-4 table. The four-state line is safe; a two-valued all-caps line **does** match `CHOICE_LINE`. | the P6-4 constraint: no two-valued all-caps `FIELD: A \| B` lines in shared files. Re-run the full suite after S6, since `test_workflow_contract.py` covers this parser. |
| **V3** | **Fail-open by precedent (R-5).** The nearest neighbour, `load_risk_contract`, returns `None` on a malformed block and the runtime reads `None` as "no risk axis". Copying it would violate requirement 9. | `scripts/skill_policy.py`'s `load_risk_contract` docstring states the return-`None` convention explicitly. | follow `quality_profile.py:521-528` / `agent_profile.py:462-467` — **raise**. Called out here so a later reviewer does not "correct" it toward the wrong precedent. Mutation M-9 is the guard. |
| **V4** | **Package surface.** New `scripts/**` files ship automatically via `INCLUDED_ROOTS` + `rglob`, including the fixture tree. | `scripts/release_manifest.py:41` and `release_files()`. `required_skill_paths()` enumerates existing template/review paths only, so in-place edits add no path. | no manifest edit expected — but **run `verify_package.py` and `build_release.py` + archive verification anyway** (S8). Keep fixtures small; they enter the released archive. |
| **V5** | **Byte-equality divergence** across 16 hand-edited prose files. | `validate_shared_directories` fails on any mismatch — the risk is churn, not escape. | edit one copy, `cp` to the other, never hand-edit both (P6-1). |
| **V6** | **Prompt budget (R-4).** The orchestration SKILL.md is already ~131 KB / 2110 lines. | the `*_MAX_LINES` budgets (4/14/17/18/20) exist for exactly this. | adopt `DECISION_POLICY_MAX_LINES` and assert it (P3-2). Prose in `templates/**` and `reviews/**` where it is read per-phase, rather than bloating SKILL.md. |
| **V7** | **Over-claiming at completion (R-3 / UD-2 / UD-3).** The repository's recurring blocking cause is "narrow citation, broad conclusion" — five consecutive rounds in ANALYSIS. | RA-3 and RA-6 were both this. | the PR and completion report must state: the contract is not executed until OS-29; requirement 5 is permission-level only (UD-2); the `evaluate_invocation` schema gap is pre-existing and untouched (UD-3). Each as an explicit limit, not a footnote. |

**Rollback.** The change is purely additive: a new module, a new test module, a new fixture
directory, a new nested JSON object, and new optional prose. There is no schema migration, no
lifecycle state change, and no artifact-format break — the UD-1 section is optional, so every
existing artifact stays valid. Rollback is `git revert` of the commit range; the only ordering
constraint is that M5 (validator import) and M6 (copy list) revert together, for the same reason
they must land together.

---

## Changes

None. This phase produced a plan only; no repository source file, Skill, script, test, template, or
document was created or modified.

## Modified Files / Artifacts

| path | change |
|---|---|
| `artifacts/runs/run_3233a1469e97/PLAN.md` | created (this file) |

No other file was written. Verified by `git status --porcelain` and `git diff --stat` over
`scripts`, both Skills, `docs`, `.orca`, and `VERSION` — both returned empty output.

## Validation

Baseline commands executed by this task, with their results:

| command | result |
|---|---|
| `python3 scripts/validate_skills.py` | **Skill validation PASSED (501 checks)**, exit 0 |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1269 tests in 290.636s — OK (skipped=6)**, exit 0 |
| `git status --porcelain -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs VERSION .orca` | **empty** |
| `git diff --stat -- scripts orca-worker-reviewer-orchestration orca-worker-reviewer-loop docs` | **empty** |

Repository facts verified by reading the cited lines during this task:

| claim | how verified |
|---|---|
| the policy-contract deep-equality assertion is at `validate_skills.py:1118-1122` | `grep -n 'contracts\[0\]\[1\] == contracts\[1\]\[1\]' -B 3 -A 3 scripts/validate_skills.py` |
| `validate_shared_directories` is at `validate_skills.py:800-822` | `grep -n 'def validate_shared_directories' scripts/validate_skills.py` and reading the body |
| the fail-closed schema-version precedent | `sed -n '519,530p' scripts/quality_profile.py`; `sed -n '460,470p' scripts/agent_profile.py` — both `raise` on an unsupported version |
| `test_validate_skills.py` copies a fixed script tuple (V1) | `sed -n '45,76p' scripts/test_validate_skills.py`, including the OS-4 comment describing this exact failure |
| `INCLUDED_ROOTS` contains `scripts`; `required_skill_paths()` lists existing template/review paths only | `sed -n '76,112p' scripts/release_manifest.py`; `grep -n 'INCLUDED_ROOTS' scripts/release_manifest.py` (line 41) |
| the JSON block already carries a sub-object no Python reads | `grep -rn '"project_source"\|"user_source"\|"source_precedence"\|"merge"' scripts/*.py` — the only hit is an unrelated git-verb set in `test_os22_required_tests.py:496` |
| CHANGELOG.md is updated per feature PR | `git log --oneline -6 --name-only -- CHANGELOG.md` — PRs #23, #21, #20, #18, #17, #16 |
| the P6-4 regex-collision table | a Python snippet importing `CHOICE_LINE` and `REVIEW_VERDICT_LINE` from `scripts/workflow_contract.py` and matching each candidate line |
| `RISK_SAFETY_FLOOR` line reference used by ANALYSIS | `grep -n 'RISK_SAFETY_FLOOR' orca-worker-reviewer-orchestration/SKILL.md` → line 923 |

Explicitly **확인하지 않음**:

- the Jira OS-28 issue body itself — scope is taken from the task brief, `ANALYSIS.md`, and
  `docs/ROADMAP.md`;
- the Jira OS-29/30/31/32 issue bodies;
- whether any *other* repository policy class exists that trips none of the eleven boundary elements
  (ANALYSIS's stated OQ-9(c) residual assumption is carried forward unverified, not resolved);
- runtime behaviour of any planned module — nothing was implemented, so no execution evidence
  exists for N1, N2, or N3.

## Unit Tests / Testing Strategy

No test was added or modified in this phase — this is PLAN, and the task brief forbids writing code.

The test strategy is P4 in full: a per-requirement mapping table (P4-A), the positive three-check
liveness suite over every reason code (P4-B), and the fixture layout (P4-C). P5 adds fourteen named
mutations whose job is to prove the suite catches a weakening rather than merely coexisting with one.

Baseline for regression comparison:

| suite | baseline | rule for later phases |
|---|---|---|
| `python3 scripts/validate_skills.py` | 501 checks, PASSED | must stay PASSED; check count must **rise** |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | 1269 tests, OK (skipped=6), 290.6s | must stay OK; test count must **rise** |

Three limits carried forward and required to be restated at completion:

- **(UD-2)** requirement 5 is proven to the **permission** level only; a contract-level test cannot
  detect a real model's over-escalation, and this must not be reported as solved;
- **(UD-3)** the missing `schema_version` gate in the existing `evaluate_invocation()` is a
  **pre-existing** defect left in place — neither fixed nor worsened, and not to be claimed;
- **(R-3)** nothing executes this contract until OS-29; P8's proof 1 is what makes that checkable
  rather than asserted.

## Review Feedback Resolution

Iteration 1 of PLAN. No prior PLAN Reviewer findings — `artifacts/runs/run_3233a1469e97/` contained
`ANALYSIS.md`, `REVIEW_ANALYSIS.md`, `USER_DECISIONS.md`, `ORCHESTRATOR_LOG.md` and `TIMING_LOG.md`
before this Task.

ANALYSIS feedback carried into this plan rather than re-litigated: A3-1/A3-1a, A3-2 with T-F1…T-F6,
A4-0/A4-1, A5-2, A5-3, A5-4's 17-code audit, INV-3/INV-4/INV-5 and A6 are inputs, not open items.
The plan departs from ANALYSIS in exactly two places, both stated where they occur: **P3-2** adds a
Python expected-constant that ANALYSIS did not call for (reason: deep/byte equality cannot detect
simultaneous deletion from both Skills), and **P3-1** corrects ANALYSIS's `validate_skills.py`
line citation from `1122-1126` to the re-verified `1118-1122`.

### Question for the user (P7) — OQ-9, not decided here

Carried for the Coordinator to put to the user in the UD-1/2/3 format.

**Question.** When an explicit requirement contradicts a repository policy that is **not** a
quality-profile attribute — a convention, project configuration, code structure, or a security /
privacy / compliance / tooling policy — what is the decision state, and what happens to the
suspended `requirement_vs_repository_policy` reason code?

**Options.**

- **(a)** restore the code under `CONFLICT` — this class is resolved by the user.
- **(b)** restore the code under `NEEDS_INPUT` — the user supplies the missing authority.
- **(c)** keep the removal — only the quality-gate-tier case is `CLEAR` by the requirement, and
  everything outside that tier is routed by the existing eleven boundary elements.

**Impact.** Overridability is machine-checkable **only** where a policy is already a named key in a
parsed contract — today that is `RISK_SAFETY_FLOOR` alone. There is no overridability marker
anywhere else in the repository, so the *undistinguishable* case is the normal case. (a) requires
widening the `CONFLICT` entry condition the Reviewer just confirmed and pauses on every convention
disagreement; it also fits poorly, since an overridable convention *can* be satisfied by following
the requirement. (b) needs no entry-condition change and fails closed, but pauses on most policy
disagreements and duplicates `security_impact` / `privacy_impact` / `compliance_impact`, which
already reach `NEEDS_INPUT` on their own. (c) adds nothing and resolves the undistinguishable case
by asking whether any of the eleven elements is true — its residual risk is an assumption I did not
test: that the eleven elements catch every policy class that matters.

**Recommendation.** (c), on the ground that it adds no reason code, needs no new authority rule, and
changes no confirmed text. Its weakness is a stated assumption rather than a stated fact.

**Why this is raised rather than decided.** It determines whether a class of conflict pauses for the
user or proceeds without one. That is decision authority, and this ticket exists to keep exactly
that kind of choice with the user.

STATUS: COMPLETE
