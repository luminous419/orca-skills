# Worker Result

STATUS: COMPLETE

run: run_8ff2f4f0acb3 · phase: BUGFIX · iteration 1 · risk: high
branch: `feat/os-28-bounded-autonomy-policy` · base head: `cef080b`
source of findings: external review of PR #25 (GPT-5.6 Sol, review id 5061977892,
head cef080b) — CRITICAL ×1, MAJOR ×2, verdict NOT MERGE-READY.

This file is **append-only** for this run. It does not modify anything under
`artifacts/runs/run_3233a1469e97/`; that run's artifacts are read-only inputs and
were committed byte-for-byte (one redaction excepted and recorded in §F-003).

---

## Bug / Root Cause Evidence

All three findings were reproduced **before any change**, on a pristine
`git archive HEAD` copy of `cef080b` in a scratch directory, so the "before" column
below is measured on the shipped head and not on a partially-edited tree.

### F-001 (CRITICAL) — an undeclared safety fact read as a safe one

`scripts/fixtures/decision_policy/valid/repository_policy.json` at `cef080b` carried
exactly these keys:

```
['impact', 'policy_source', 'reason_code', 'retraction_condition', 'reversibility', 'state']
```

Measured on the pristine copy:

| probe | before (cef080b) |
| --- | --- |
| `validate_record(policy, repository_policy.json)` | ACCEPTED |
| `permitted_states(policy, repository_policy.json)` | `['ASSUMPTION_ALLOWED']` |
| same record with `blast_radius` absent | ACCEPTED / `['ASSUMPTION_ALLOWED']` |
| `monetary_cost` absent | ACCEPTED / `['ASSUMPTION_ALLOWED']` |
| `security` absent | ACCEPTED / `['ASSUMPTION_ALLOWED']` |
| `privacy` absent | ACCEPTED / `['ASSUMPTION_ALLOWED']` |
| `compliance` absent | ACCEPTED / `['ASSUMPTION_ALLOWED']` |
| `long_term_lock_in` absent | ACCEPTED / `['ASSUMPTION_ALLOWED']` |

Every one of the six was absent from the shipped record to begin with, so the
"absent" rows are the shipped state, not a mutation.

Root cause, at `scripts/decision_policy.py:784-792` on that head — two predicates
that each read a DECLARED value and neither of which asked whether anything was
declared:

* `blast_radius_within_scope` evaluated `facts.get("blast_radius") not in
  ("repository", "external_system")`. `None` is not in that tuple, so **an omitted
  blast radius was within scope.**
* `no_high_impact_element` evaluated `not _assumption_allowed_is_forbidden(...)`,
  whose test for each of the five flags is `facts.get(element) is True`. An omitted
  flag is not `True`, so **an omitted impact flag was false.**
* `no_reserved_user_authority` evaluated `facts.get("explicit_user_authority") !=
  "reserved"`, so **an absent authority declaration satisfied the predicate** through
  an `!=` on a missing key rather than through any stated rule.

The only field in the shipped record that reads like an impact statement is
`impact`, which is required non-empty and never read for meaning. Its shipped value
was `"current_change"` — a member of the `blast_radius` enum — so it *looked* like a
machine-readable fact while proving nothing.

This is the same defect class the previous run closed four times (FR-8, RI8-1, RI9-1,
FR-9) and is the largest of them: **the domain of a declared value was checked; that
a required fact was declared at all was not.**

### F-002 (MAJOR) — a reason code's clause was declared, never enforced

Measured on the same pristine copy:

| probe | before (cef080b) |
| --- | --- |
| `valid/missing_user_intent.json` contains `user_intent_absent` | **False** |
| `validate_record` on it (code binds clause **N-2**) | ACCEPTED |
| `valid/unclassifiable_decision.json` contains `unclassifiable` | **False** |
| `validate_record` on it (code binds clause **N-3**) | ACCEPTED |
| `unclassifiable_decision` + `user_intent_absent: true` instead of its own fact | ACCEPTED |
| valid fixtures whose own `state` is NOT in `permitted_states(record)` | **6 of 18** |
| valid fixtures carrying `no_determining_policy_source` | **10** |

Root cause, `scripts/decision_policy.py:942-981` on that head: `_grounds_defect()`
verified that a NEEDS_INPUT code's bound boundary **element** had fired and never
evaluated the N-1/N-2/N-3 **clause** the same code declares. `missing_user_intent`
rests on N-2, "required user intent is absent", and its fixture asserted nothing at
all about user intent — it named the `ambiguity` element and therefore stood up as
**N-1**, the clause `ambiguous_requirement` rests on. The code→clause edge existed
only in prose.

The two fields the fixture did carry to look like N-1 evidence,
`no_determining_policy_source` and `no_explicit_authorization`, appear on **ten**
shipped NEEDS_INPUT fixtures — every one that binds a boundary element; only
`unclassifiable_decision`, which binds none, was without them — and are read by
**no contract, no validator and no test**
anywhere in the repository (`grep` over `--include=*.py --include=*.md`, excluding
`artifacts/`, returns zero non-fixture hits).

The six-fixture parity gap is the same finding seen from the other API: exactly the
records whose declaration is carried by the **reason code** — the two `ambiguity`
records, `unclassifiable_decision`, and all three CONFLICT records — were accepted by
`validate_record()` while `permitted_states()` returned the **empty set** for the
identical mapping. The other twelve declare a boundary value of their own and did not
diverge.

### F-003 (MAJOR) — the decision and review evidence was not on this head

`git ls-files artifacts/runs/run_3233a1469e97/` at `cef080b` returned exactly three
files: `DESIGN.md`, `IMPLEMENTATION.md`, `TEST.md`. Those three cite `ANALYSIS.md`,
`PLAN.md` and `USER_DECISIONS.md` as normative inputs. The eleven remaining top-level
artifacts and all six `final_review_audit/` attempts existed in the workspace,
untracked. `git check-ignore -v` matched **none** of them, so this was an omission,
not a policy.

---

## Fix / Modified Files

### F-001 — the contract now names what must be PROVEN, and absence proves nothing

Smallest change that closes it, and the argument that it is smallest:

1. **One new contract key**, `assumption_allowed_requires.declared_safety_facts`,
   listing the six facts an `ASSUMPTION_ALLOWED` record must declare. The list is
   *data*, not code, so it is pinned by C18 and identical in both Skills.
2. **One new entry predicate**, `all_safety_facts_declared`, added to
   `entry_conditions.ASSUMPTION_ALLOWED.all_of`. Both `permitted_states()` and
   `validate_record()` already evaluate that condition through the shared
   `_entry_condition_defect`, so no third code path was introduced.
3. **One new contract key**, `assumption_allowed_requires.absent_explicit_user_authority`,
   stating what an undeclared `explicit_user_authority` means.

No default was added anywhere. No value is manufactured for a missing key; a missing
key makes the predicate false. `parse_decision_policy()` refuses to load a contract
whose `declared_safety_facts` is absent, empty, or names a non-element, or whose
`absent_explicit_user_authority` is anything but `not_reserved` / `reserved` — so the
fail-open cannot return by deleting the contract data.

**Why presence is a separate predicate rather than folded into the two value
predicates.** "Is there a value?" and "is the declared value safe?" are different
questions. Folding them makes the diagnostic name the wrong defect, and collapsing a
prohibition into a permission is exactly what TR4-2 already cost this module — INV-4
stays a prohibition (missing ≠ forbidden) and the entry condition is the permission
(missing ≠ permitted).

**Why the free-form `impact` field stays.** It is required non-empty and is genuine
prose evidence, like `retraction_condition`. What it may no longer do is stand in for
a fact: the six machine-readable facts are now mandatory alongside it, and the four
shipped fixtures' `impact` values were changed from enum-lookalikes
(`"current_change"`) to plain prose so the distinction is visible to a reader.

**Why the authority allowlist was not widened.** `explicit_user_authority` is
deliberately **not** in `declared_safety_facts`. Its domain admits only `reserved`
(RI8-1), so requiring it to be declared would make `ASSUMPTION_ALLOWED` unreachable,
and widening that domain would widen the user-authority vocabulary FR-2 closed.
Instead the absence rule is stated in the contract and read from there. Flipping it to
`reserved` makes an unstated authority block the state, with no code change — proven
by a test that parses a modified contract and asserts the flip.

### F-002 — the clause is proven, and the decorative fields are gone

1. **One new contract key**, `clause_predicates`, binding each declared entry clause
   to the entry predicate that proves it (`N-1 → undetermined_boundary_element`,
   `N-2 → absent_user_intent`, `N-3 → unclassifiable_item`, `C-1/C-2/C-3 →
   declared_contradiction`). Declared rather than inferred from the ORDER of
   `entry_conditions[NEEDS_INPUT]`, which happens to line up today and would re-break
   silently the first time a clause or predicate is added. Load-time validation
   requires exact coverage of the declared clauses and membership in `ENTRY_PREDICATES`.
2. `_grounds_defect()` evaluates that predicate through **`_evaluate_predicate` — the
   same function `permitted_states()` calls**, so the two APIs cannot answer "does
   this clause hold?" differently. The existing element/clause **mismatch** checks run
   first, so their more specific diagnostics are preserved.
3. **`record_facts()`** (new, public) makes a reason code's own declarations explicit:
   a `declared`-kind boundary element named in `boundary_element`, and a CONFLICT
   record's clause fixed by its code. It uses `setdefault`, so a record that declares
   a *different* value keeps it and the mismatch checks reject it — the normalisation
   cannot paper over a contradiction, and a test asserts exactly that.
4. **Decorative fields removed, not newly read.** `no_determining_policy_source` and
   `no_explicit_authorization` asserted precisely what `undetermined_boundary_element`
   now proves from real evidence (`policy_source.role` and the whole `user_decision`
   record). A second, unchecked way to state the same claim is how a record and its
   contract drift apart, so they were deleted from all ten fixtures. A test asserts
   neither name appears in any fixture or either SKILL.md.

### Modified files (commit `f5dace0`)

```
orca-worker-reviewer-loop/SKILL.md                  contract + prose (2 new anchors)
orca-worker-reviewer-orchestration/SKILL.md         idem, contract block byte-identical
orca-worker-reviewer-loop/reviews/common.md         Reviewer misclassification criteria
orca-worker-reviewer-orchestration/reviews/common.md idem, byte-identical (cmp exit 0)
scripts/decision_policy.py                          predicates, parse-time validation,
                                                    record_facts(), clause enforcement
scripts/validate_skills.py                          C18/C30 updated, C32 added,
                                                    2 prose anchors added
scripts/test_decision_policy.py                     +23 tests (2 new classes), helpers
scripts/test_validate_skills.py                     +4 mutation guards, 1 target repaired
scripts/fixtures/decision_policy/valid/*.json        18 of 18 (4 AA + 10 decor + 2 clause)
scripts/fixtures/decision_policy/invalid/inv4/*.json 8 files completed
```

The contract block is **90 lines**, at the `DECISION_POLICY_MAX_LINES = 90` ceiling
(it was 89). The ceiling was **not** raised.

Untouched, as required: Risk / Quality Profile / Agent Profile semantics, Final
Review guarantees, Orca lifecycle guarantees, `evaluate_invocation()` (UD-3), VERSION,
LICENSE. No OS-29/30/31 behaviour was implemented.

### F-003 — evidence committed (commit `6d54f56`)

**29 files**, committed verbatim from the workspace with no content rewritten:

* 11 top-level: `ANALYSIS.md`, `PLAN.md`, `USER_DECISIONS.md`, `ORCHESTRATOR_LOG.md`,
  `TIMING_LOG.md`, `FINAL_REVIEW.md`, `REVIEW_ANALYSIS.md`, `REVIEW_PLAN.md`,
  `REVIEW_DESIGN.md`, `REVIEW_IMPLEMENTATION.md`, `REVIEW_TEST.md`
* 18 audit files: six `final_review_audit/attemptN__task_*__ctx_*/` directories,
  each with `input.md`, `record.json`, `report.md`

`DESIGN.md` / `IMPLEMENTATION.md` / `TEST.md` were **not** modified
(`git diff HEAD --name-only` over the three returns zero) — hiding the missing
provenance by editing them would defeat the point.

**Recoverability.** All eight owner decisions are present and recoverable:
`USER_DECISIONS.md` carries `UD-1` … `UD-8`. **F-003 is therefore not BLOCKED.**

**Redaction — verified, not asserted.** Each `record.json` declares
`redaction_policy_version: "redaction/1.1"`, its redaction categories and counts, and
pre/post byte lengths and SHA-256 digests. I recomputed
`artifact_digest_post_redaction` against the files on disk for all six attempts:

| attempt | policy | redactions | bytes match | digest match | settlement | provenance |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | redaction/1.1 | absolute_local_path ×1 | yes | yes | settled | accepted |
| 2 | redaction/1.1 | absolute_local_path ×1 | yes | yes | settled | accepted |
| 3 | redaction/1.1 | absolute_local_path ×1 | yes | yes | settled | accepted |
| 4 | redaction/1.1 | absolute_local_path ×1 | yes | yes | settled | accepted |
| 5 | redaction/1.1 | absolute_local_path ×1 | yes | yes | settled | accepted |
| 6 | redaction/1.1 | absolute_local_path ×1, foreign_absolute_path ×1 | yes | yes | settled | accepted |

Secret / credential / PII / external-channel scan over all 29 files: **no** email
addresses, **no** API keys, tokens, bearer credentials or private keys, **no**
`dcap_`/`cap_` capability strings, **no** Slack/Discord/Teams identifiers or webhook
URLs, **no** URLs at all. The `slack` string hits are inside quoted `grep` commands
that assert OS-30/31 vocabulary is *absent* from the tree.

**One content change, recorded here because it is the only one.**
`FINAL_REVIEW.md` line 26 carried an unredacted absolute home path
(`/Users/<username>/aiAssistedProjects/orca-skills`) — the single occurrence of a
personal identifier in the set. It now carries the same
`<REDACTED:absolute_local_path>` marker this run's own audit records already use
thirteen times. This is the repository's redaction policy applied consistently, not a
rewrite of the finding's substance. If the coordinator prefers the raw path, revert
that one substitution.

**`FINAL_RESULT.md` does not exist and never did — for this run.** It appears in no
commit in this repository's history (`git log --all --diff-filter=A --name-only`
returns nothing for that name) and the PR description never cited it. The reviewer's
list has it because a file of that name exists in the workspace under
`artifacts/runs/run_c854db299e7a/` — a **different ticket** (OS-4, agent profile
separation, branch `agent/agent-profile-separation`). It was not fabricated for
run_3233a1469e97 and is not included.

---

## Regression Test

Test files: `scripts/test_decision_policy.py`, `scripts/test_validate_skills.py`
New cases: **27** — 23 in `test_decision_policy.py` (154 → 177), in two new classes
plus one restated existing test, and 4 in `test_validate_skills.py` (170 → 174),
plus one repaired mutation target. See "A guard fired" below.

**Before Fix: FAIL.** Two independent demonstrations:

1. *Reproduction on the pristine head.* Every probe table in the Bug Evidence section
   above was executed against a `git archive HEAD` copy of `cef080b`, before any edit
   to the working tree. The assertions the new tests make are false on that tree.
2. *Anti-vacuity mutation of the fixed tree.* With the fix in place, the two halves
   were reverted one at a time and the suite re-run:

   | mutation | new-suite result |
   | --- | --- |
   | `_undeclared_safety_facts()` forced to return `()` ("missing means safe") | **FAILED (failures=18)** |
   | clause-predicate enforcement in `_grounds_defect()` disabled | **FAILED (failures=17)** |
   | neither (the shipped fix) | **OK (177 tests)** |

**After Fix: PASS.**

### `F001UndeclaredSafetyFactsAreNotSafe` (12 cases)

| case | what it holds |
| --- | --- |
| `test_the_safe_baseline_covers_every_declared_safety_fact` | D4-F guard: the probe set **is** the contract's list; a seventh fact fails here rather than going unprobed |
| `test_omitting_any_single_safety_fact_refuses_assumption_allowed` | the six one-at-a-time omissions, through **both** APIs on identical input, each anchored on the missing fact's own name |
| `test_dropping_every_safety_fact_at_once_refuses_too` | the shipped shape verbatim |
| `test_free_text_impact_is_not_a_substitute_for_the_facts` | four `impact` strings incl. `"current_change"` |
| `test_an_unknown_or_mistyped_safety_fact_is_rejected_not_ignored` | 9 unknown / wrong-typed values must **raise**, not read as "did not fire" |
| `test_a_blast_radius_outside_the_requested_scope_is_refused` | `repository`, `external_system` |
| `test_a_high_impact_boolean_declared_true_is_refused` | all five flags |
| `test_a_contract_without_the_new_keys_does_not_load` | 4 malformed contracts fail at load |
| `test_authority_absence_is_a_contract_rule_not_a_code_default` | flipping `absent_explicit_user_authority` to `reserved` flips behaviour with no code change |

### `F002AReasonCodesClauseMustBeProven` (11 cases)

| case | what it holds |
| --- | --- |
| `test_every_declared_clause_names_the_predicate_that_proves_it` | all six clauses bound, all predicates in the closed set |
| `test_a_contract_whose_clause_binding_is_incomplete_does_not_load` | 3 malformed bindings |
| `test_n2_requires_the_absence_of_user_intent_to_be_declared` | N-2 predicate false / missing → rejected, anchored on `"N-2"` |
| `test_n3_requires_the_item_to_be_declared_unclassifiable` | N-3 idem |
| `test_n1_requires_a_fired_element_and_no_resolving_authority` | all 9 N-1 codes: a complete `user_decision` rejects; a determining `policy_source` rejects **except** for `authority_reserved_to_user`, the one A4-0 says policy cannot resolve |
| `test_facts_of_another_clause_do_not_establish_this_one` | three cross-clause substitutions, each anchored on the clause **and** the predicate name |
| `test_a_conflict_record_may_not_borrow_another_clause` | all three CONFLICT codes |
| `test_the_two_apis_agree_on_every_shipped_record` | parity for all 18 fixtures on identical input |
| `test_record_facts_never_overrides_what_the_record_declares` | the normalisation cannot mask a contradiction |
| `test_no_shipped_fixture_carries_an_unread_evidence_field` | the two retired field names appear in no fixture and neither SKILL.md |

### Positive controls (the over-blocking guard)

Every negative above has a paired positive. Enumerated so a "refuse everything" fix
would fail visibly:

1. `test_a_fully_declared_safe_record_is_still_permitted` — all **four**
   `ASSUMPTION_ALLOWED` reason codes still validate and still appear in
   `permitted_states`.
2. `test_every_in_scope_blast_radius_still_permits_the_state` — `current_change` and
   `module`.
3. `test_the_clear_path_is_untouched_by_the_narrowing` — CLEAR still reachable by a
   determining policy source, by an `explicit_user_reply`, by a
   `prior_explicit_user_authorization`, and by "nothing open"; and both pause states
   still validate.
4. `test_every_clause_has_a_valid_positive_fixture` — one valid record per clause,
   14 codes across all six clauses.
5. `test_a_safe_item_is_still_assumption_allowed_by_both` (existing, updated).
6. `test_both_apis_still_permit_the_safe_cases` (existing).
7. `test_omitting_an_element_remains_legal` (existing) — an undeclared element still
   raises nothing.
8. All **18** valid fixtures still validate; all **8** INV-4 negatives still reject,
   and after completing them with the six facts each is rejected **on INV-4 grounds**
   rather than on the new omission rule — checked by reading the message, because a
   negative that starts passing for a new reason is a probe defect, not a fix.

One existing test was restated rather than deleted:
`test_omitting_the_element_remains_legal` → `..._but_is_no_longer_safe`. Omission is
still *legal* (no domain error); what changed is that it no longer *buys*
`ASSUMPTION_ALLOWED`. The docstring records both halves.

---

## Related Unit Tests / Validation

All six commands executed on the final tree. Nothing below is quoted from memory.

Every "before" figure below was measured on the pristine `git archive HEAD` copy of
`cef080b`, not quoted from the dispatch brief.

| command | before (`cef080b`) | after |
| --- | --- | --- |
| `python3 scripts/validate_skills.py` | PASSED (642 checks) | **PASSED (648 checks)** |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | OK — 1469 tests, skipped=6 | **OK — 1496 tests, skipped=6** |
| `python3 scripts/verify_package.py` | PASSED (173 source files) | **PASSED (173 source files)** |
| `python3 scripts/build_release.py` | built `dist/orca-skills-0.9.0.tar.gz` | **built `dist/orca-skills-0.9.0.tar.gz`** |
| `python3 scripts/verify_package.py --archive dist/orca-skills-0.9.0.tar.gz` | PASSED (173 source files) | **PASSED (173 source files)** |
| `git diff --check` | clean (no diff) | **clean** |

Check delta 642 → 648: `+2` C32 (clause→predicate binding pinned, once per Skill),
`+4` the two new prose anchors (two per Skill). Test delta 1469 → 1496: `+27` —
`+23` in `scripts/test_decision_policy.py`, `+4` in `scripts/test_validate_skills.py`.

### A guard fired, and it is reported rather than smoothed over

The first full-suite run on the fixed tree was **not** green: `Ran 1492 tests …
FAILED (failures=1, skipped=6)`. The failure was
`ValidatorRegressionTests.test_assumption_allowed_entry_condition_weakened_fails`,
which mutates the shipped SKILL.md by exact string replacement and asserts the
validator rejects the result. Its target string was the OLD five-conjunct `all_of`
list, so after the contract change the mutation target no longer existed and the test
failed at `assertIn(old, text, "mutation target not found")` — i.e. it failed because
it could no longer perform its mutation, not because the validator stopped catching
one.

Repaired by updating the target to the six-conjunct list, and the occasion was used to
add four guards the contract change now needs rather than only to restore the old one:
dropping **only** `all_safety_facts_declared` (which leaves a condition that still
looks complete while restoring exactly the reviewed defect), shortening
`declared_safety_facts`, flipping `absent_explicit_user_authority`, and repointing
`N-2` at N-1's predicate. All four assert the specific validator message.

**Skips did not increase: 6 before, 6 after.** No test was skipped, weakened or
deleted to get green; the two existing tests that changed were changed because the
contract's semantics changed, and both record why in their docstrings.

---

## Review Feedback Resolution

| finding | severity | before | after |
| --- | --- | --- | --- |
| F-001 undeclared impact facts fail open to `ASSUMPTION_ALLOWED` | CRITICAL | reproduced on `cef080b` | **RESOLVED** — contract requires the six facts to be declared; both APIs refuse otherwise; 12 regression cases + 8 positive controls |
| F-002 NEEDS_INPUT clause declared but not enforced | MAJOR | reproduced on `cef080b` | **RESOLVED** — `clause_predicates` enforced through the shared predicate; decorative fields removed; 11 regression cases; full 18-fixture parity |
| F-003 decision / review evidence absent from this head | MAJOR | reproduced on `cef080b` | **RESOLVED (not BLOCKED)** — 29 files committed verbatim; redaction verified by digest; `FINAL_RESULT.md` reported as never having existed for this run |

**Merge-readiness is not claimed here.** The three findings are addressed and the six
commands are green on this tree, but this is a Worker result; the merge decision
belongs to the Coordinator's gate and to whatever re-review it schedules. No claim is
made in the PR description about evidence that is not now readable on this head.

---

## Decision Record

```text
DECISION_STATE: ASSUMPTION_ALLOWED
REASON_CODE: explicit_requirement
EVIDENCE:
  policy_source: {kind: requirement_id, locator: "PR#25 review 5061977892 F-001",
                  role: supports}
  reversibility: reversible_in_run
  blast_radius: current_change
  monetary_cost: false
  security: false
  privacy: false
  compliance: false
  long_term_lock_in: false
  impact: chooses declared_safety_facts + a separate presence predicate over folding
          presence into the two existing value predicates
  retraction_condition: a reviewer prefers presence folded into blast_radius_within_scope
                        and no_high_impact_element
```

The task fixed *what* the invariant must be, not *how* the contract should express it.
The shape above was chosen and is retractable. It is filed under the record format this
change itself introduced, and it validates under it.

One further decision is recorded as `ASSUMPTION_ALLOWED` rather than `NEEDS_INPUT`
because a policy source determines it: the single redaction in `FINAL_REVIEW.md`
(§F-003), supported by `redaction/1.1` as already applied thirteen times inside this
same run's audit records. Retraction condition: the coordinator prefers the raw
absolute path, in which case revert that one substitution.

---

## Commits

| commit | scope |
| --- | --- |
| `f5dace0` | F-001 + F-002 — contract, evaluator, validator pins, fixtures, tests, Reviewer guidance |
| `6d54f56` | F-003 — 29 evidence files from `run_3233a1469e97`, committed verbatim |
| `dbe446e` | validator regression guards — repaired mutation target + 4 new guards |

Not pushed. The Coordinator handles push after the gate.
