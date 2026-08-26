# Worker Result

STATUS: COMPLETE

## Summary / Analysis

Implemented DESIGN.md's approved attempt-domain invariant — the iteration-2 "Expected Changed
Files / Implementation Steps" list, steps 1–10 — in one commit on
`agent/final-review-observability-evaluation`.

**What was built.** INV-ATTEMPT-2, as D-A.7.3′/D-A.7.4′ specify it: one predicate,
`run_logging.attempt_domain_violation()`, hosted in the import-graph sink, with two exception
facades over it (`run_logging.assert_attempt_in_domain()` raising `RunLoggingError`, and
`review_isolation.assert_attempt_in_domain()` raising `IsolationAttemptDomainError`), enforced as
the first statement at all seven public boundaries that turn an `attempt` into identity, a path,
retained document content, or reported output. The rule and its message text exist exactly once;
the "two independent but textually identical checks" fallback is not used, because C-9's import
census shows it is not necessary.

**State of the tree before this task, verified rather than assumed.** The DESIGN worker's prototype
was measured and then reverted, so `scripts/` carried **no** part of this remediation. Checked
directly before editing: `run_logging.py` had no `ATTEMPT_MIN`, no `attempt_domain_violation()` and
no `assert_attempt_in_domain()`; `review_isolation.py` had no `IsolationAttemptDomainError` and no
facade; `final_review_eval._dispatch_isolate()` had **no** GATE 3 and a single-class
`except review_isolation.IsolationSeedGrammarError` clause. Step 5's "this may already partially
exist — verify" resolved to **nothing existed**; GATE 3 and the widened `except` tuple were both
written in this task.

**The one deviation from the literal step text, stated plainly.** Step 6 says to *update* the two
`.gitattributes` comment lines. D-A.6″'s seven-rule block is **not in the tree** — the shipped
`.gitattributes` still carries A.6's single `report.md` rule, and `GITATTRIBUTES_RULES` does not
exist anywhere in `scripts/` (`grep -rn "GITATTRIBUTES_RULES" scripts/` returns nothing). D-A.6″'s
implementation is a separate, earlier design item that has not run. There were therefore no comment
lines to update. The invariant D-A.7.7 point 1 requires be discoverable in the file was **added** to
the existing comment block instead, naming all seven boundaries and the shared predicate
`run_logging.attempt_domain_violation()`. No rule was added, removed, reordered or re-spelled;
`GITATTRIBUTES_RULES` trivially does not change because it does not exist. This is a comments-only
change to a file whose single rule line is untouched.

## Changes

| step | file | change |
|---|---|---|
| 1 | `scripts/run_logging.py` | added `ATTEMPT_MIN`, `attempt_domain_violation()`, `assert_attempt_in_domain()` immediately above `final_review_dispatch_key()`; refactored that function's two inline checks into one call (`label="final_review_attempt"`); replaced `final_review_report_ladder_path()`'s `if attempt < 1:` block with one call (**GATE 5**); added one call as the first statement of `read_final_review_attempt_provenance()` (**GATE 7**). The `suffix` expression is untouched |
| 2 | `orca-worker-reviewer-orchestration/tools/run_logging.py` | `cp scripts/run_logging.py` — byte-identical mirror, same commit |
| 3 | `scripts/review_isolation.py` | added `IsolationAttemptDomainError` and the delegating `assert_attempt_in_domain()` immediately above `IsolationSeedGrammarError`; **no** `ATTEMPT_MIN` here (D-A.7.3′); added `attempt = assert_attempt_in_domain(attempt)` as the first statement of `build_attestation()` (**GATE 4**, before `verdicts = {…}`), `repatriate()` (**GATE 1**, before `session = Path(session)` and therefore before the `mkdir`) and `isolate()` (**GATE 2**, before the `enforcement` checks and `build_session()`) |
| 4 | `scripts/e2e_harness.py` | replaced `final_review_artifact_path()`'s `if attempt < 1: raise ValueError(...)` with `attempt = run_logging.assert_attempt_in_domain(attempt)` (**GATE 6**). No new import |
| 5 | `scripts/final_review_eval.py` | added **GATE 3** as the first statement inside `_dispatch_isolate()`'s existing `try:`, after `import review_isolation` and before the `--teardown`/`--repatriate` branch, as `args.attempt = review_isolation.assert_attempt_in_domain(args.attempt, label="--attempt")`; widened the existing `except` to the two-element tuple. `add_argument("--attempt", …)` unchanged |
| 6 | `.gitattributes` | comment lines only (see the deviation above). The rule line is byte-identical |
| 7 | `scripts/test_review_isolation.py` | `AttemptDomainTests` — T-13.1, T-13.3, T-13.4′, T-13.5, T-13.6; plus the `_function_body_statements()` AST helper T-13.4′ needs |
| 8 | `scripts/test_final_review_eval.py` | `AttemptDomainCliTests` — T-13.2 |
| 9 | `scripts/test_run_logging.py` | `AttemptDomainLadderTests` (T-13.7) and `AttemptDomainProvenanceTests` (T-13.8) |
| 10 | `scripts/test_e2e_harness.py` | `FinalReviewArtifactPathAttemptDomainTests` — T-13.9 |

**Production diff size, measured** (`git diff --numstat`), against the design's recorded prototype:

| file | measured | DESIGN prototype |
|---|---|---|
| `scripts/run_logging.py` | **+36 / −13** | +36 / −13 |
| `scripts/review_isolation.py` | **+21 / −0** | +19 / −0 |
| `scripts/e2e_harness.py` | **+1 / −2** | +1 / −2 |
| `scripts/final_review_eval.py` | +11 / −1 | (D-A.7 step 2, not separately sized) |
| `.gitattributes` | +10 / −0 | comments only |
| `orca-worker-reviewer-orchestration/tools/run_logging.py` | +36 / −13 | byte-identical mirror |

`review_isolation.py` is two lines above the prototype's count: the facade block's blank-line
spacing, not extra logic. Its content is D-A.7.3′ verbatim plus the three one-line gates.

**Behaviour-preservation check on the extraction.** `final_review_dispatch_key()`'s two refusal
messages are now produced by the shared predicate. T-13.7 asserts all five of them **verbatim**
against the shipped strings (`0`, `-1`, `False`, `2.0`, `"2"`), which makes M-24d's byte-identity
measurement executable rather than a claim.

## Modified Files

```
.gitattributes
orca-worker-reviewer-orchestration/tools/run_logging.py
scripts/e2e_harness.py
scripts/final_review_eval.py
scripts/review_isolation.py
scripts/run_logging.py
scripts/test_e2e_harness.py
scripts/test_final_review_eval.py
scripts/test_review_isolation.py
scripts/test_run_logging.py
artifacts/runs/run_028d416e596a/IMPLEMENTATION.md   (new, this report)
```

## Unit Tests

### Added / Modified Tests

| id | class / file | what it pins |
|---|---|---|
| **T-13.1** | `AttemptDomainTests` (`test_review_isolation.py`) | `repatriate()` and `isolate()` refuse `0`, `-1`, `-12` with `"attempt must be >= 1, got <repr>"` **and create nothing** — no `artifacts/` under the tmp base, and no new session directory (the half a bare `assertRaises` would miss; M-14 measured the shipped code creating the run directory before it looked at `attempt`) |
| **T-13.3** | same | (a) `False`, `True`, `2.0`, `"2"`, `None` refused at both function boundaries with `"must be an int >= 1"`, bools named with the M-14 citation; (b) `abc`/`1.5`/`0x2`/`1e3` at the CLI still exit **2** with `invalid int value`, commented as pre-existing and deliberate (D-A.7.5); (c) `001`/`+2`/`1_0`/` 3 ` parse to `1`/`2`/`10`/`3` and are accepted |
| **T-13.4′** | same | the census made executable, **corrected**: D-A.7's reachability clause about `build_attestation()` is deleted. AST-level, the gate is the **first** statement of `repatriate()`, `isolate()`, `build_attestation()`, `final_review_report_ladder_path()`, `read_final_review_attempt_provenance()` and `final_review_artifact_path()`; `review_isolation` still declares no `__main__`, no `import argparse`, no `main()`; and the two `run_logging.py` copies are **byte-identical** (C-10 / RK-22's second tripwire) |
| **T-13.5** | same | `attempt ∈ {1,2,3,9,10,42,99,100}` returns M-18's exact destinations and `report_digest == sha256_path(source)`. `100` is in the list for D-A.7.2's reason |
| **T-13.6** | same | **GATE 4.** All eight out-of-domain values refused with the right half of the message; the domain error **wins over** an otherwise-invalid `readable` that would raise `IsolationError` if the body ran (this is what places the gate before `verdicts = {…}`); and `{1, 2, 100}` round-trip through `json.dumps` as JSON **numbers** — the counter-assertion to M-24c's `false`/`null`/`"2"` leak |
| **T-13.2** | `AttemptDomainCliTests` (`test_final_review_eval.py`) | **GATE 3.** `--attempt 0` and `-1` exit **1** with `input error: --attempt must be >= 1, got 0` and **no traceback**, on the `--repatriate` form and on the `--teardown` form (which proves the gate precedes the branch, and tears nothing down). Plus the regression half: `--attempt 2` still reaches `repatriate()`, which refuses for its own reason with the **contract** exit, not the input-error exit |
| **T-13.7** | `AttemptDomainLadderTests` (`test_run_logging.py`) | **GATE 5** over all eight values, `"must be an int >= 1"` for the type half shipped code lacked; `{1,2,3,9,10,42,99,100}` return the shipped ladder names; and the extraction's own regression — `final_review_dispatch_key()`'s five refusal messages asserted **verbatim** |
| **T-13.8** | `AttemptDomainProvenanceTests` (`test_run_logging.py`) | **GATE 7 and CLI door 2.** The reader refuses all eight values; a refused attempt **scans no record directory** (a well-formed record is present, and `iter_final_review_audit_records` is asserted **not called**); door 2's `-provenance --attempt 0` exits non-zero with **empty stdout** — the counter-assertion to M-26's shipped `rc=0` plus `"final_review_attempt": 0`; both door-2 subcommands now refuse alike and write nothing (the sibling parity); and `--attempt 1` still prints the provenance JSON unchanged while `--attempt 2` still groups by the record's own field |
| **T-13.9** | `FinalReviewArtifactPathAttemptDomainTests` (`test_e2e_harness.py`) | **GATE 6.** All eight values refused, written as `assertRaises(**ValueError**)` on purpose with the comment that `RunLoggingError` subclasses `ValueError` — this is what pins D-A.7.3′'s subtype substitution; the shared predicate's message asserted; `{1,2,3,99,100}` return the shipped strings. The existing `test_final_review_artifact_paths_follow_the_attempt_suffix_rule` is left untouched as the end-to-end positive regression |

### Behavior Covered

Every one of the seven gates has direct negative coverage over the same eight-value matrix
(`0`, `-1`, `-12`, `False`, `True`, `2.0`, `"2"`, `None`) and a positive regression asserting the
shipped result is unchanged. Both CLI doors are covered at the process boundary. The
`.gitattributes`-relevant claim (`_iteration0` / `_iteration2.0` are no longer generatable) is
covered at each producer rather than inferred.

One test-authoring trap worth recording, because it bit once and would bite a future editor:
`False == 0` is `True`, so classifying which half of the message to expect by
`attempt in OUT_OF_RANGE` mis-files `False` as an out-of-**range** value. Both helpers classify by
`type(attempt) is int` instead, with a comment saying why.

### Execution

**1. The new tests, per gate** (all four suites' new classes):

```
python3 -m pytest scripts/test_review_isolation.py -q -k AttemptDomain
  → 12 passed, 44 subtests passed
python3 -m pytest scripts/test_final_review_eval.py -q -k AttemptDomain
  →  3 passed,  4 subtests passed
python3 -m pytest scripts/test_run_logging.py -q -k AttemptDomain
  →  8 passed, 31 subtests passed
python3 -m pytest scripts/test_e2e_harness.py -q -k FinalReviewArtifactPathAttemptDomain
  →  3 passed, 13 subtests passed
```

**2. The full relevant suites** (DESIGN M-28's command, real counts, not claims):

```
python3 -m pytest scripts/test_run_logging.py scripts/test_e2e_harness.py \
    scripts/test_os22_required_tests.py scripts/test_orca_runtime_contract.py \
    scripts/test_validate_skills.py -q
  → 2 failed, 727 passed, 4692 subtests passed in 60.45s
```

**The 2 failures are the pre-existing baseline failure DESIGN M-28b recorded, and this was
re-verified on this host rather than taken from the design.** They are
`RetainedReportWhitespaceExemptionTests::test_the_whitespace_gate_passes_over_the_whole_os22_range`
and `::test_the_gate_fails_again_once_the_exemption_is_removed`, caused by trailing whitespace in
committed review artifacts (this host's run reports `artifacts/runs/run_75c5c6046f35/
REVIEW_TEST_iteration1.md:49-51`). Verified by stashing **all** of this task's changes —
`scripts/`, the Skill mirror **and** `.gitattributes` — and re-running that class alone on the
unmodified tree:

```
git stash push --include-untracked -- scripts/ orca-worker-reviewer-orchestration/ .gitattributes
python3 -m pytest scripts/test_run_logging.py -q -k RetainedReportWhitespaceExemption
  → 2 failed, 5 passed, 38 subtests passed        # THE SAME TWO
git stash pop
```

Stashing `.gitattributes` too is the point: that file is exactly what this test class exercises, so
a baseline check that left my comment lines in place would not have been a baseline check. This
implementation neither causes nor fixes these two.

**3. The isolation suites** (DESIGN M-29's command — the full sandbox mechanism under real
`sandbox-exec`, the NEG-0…NEG-8 battery, the relay channel, repatriation, teardown and every CLI
subcommand):

```
python3 -m pytest scripts/test_review_isolation.py scripts/test_final_review_eval.py -q
  → 205 passed, 360 subtests passed, 0 failed in 687.32s (11m27s), exit 0
```

DESIGN M-29 recorded 190 passed / 312 subtests for the prototype; this run is 205 / 360 because it
includes the 15 new tests and 48 new subtests of T-13.1/T-13.2/T-13.3/T-13.4′/T-13.5/T-13.6 that
the prototype did not have. **Zero failures: no existing test changes behaviour at any of the seven
gates.**

Result: PASS

## Additional Validation

* **`python3 scripts/validate_skills.py` → `Skill validation PASSED (463 checks)`**, run after the
  mirror update. The count is M-27's, unchanged. The mirror is `diff -q`-clean, and T-13.4′ asserts
  byte-identity from inside the suite as a second, faster tripwire (RK-22).
* **Door 2's shipped convention re-measured on this host, not assumed.** Before the change,
  `final-review-audit-provenance --run-id r --attempt 1` exits 0 with a JSON report; after the
  change `--attempt 0` exits **1** having printed nothing, surfacing as a `RunLoggingError`
  traceback. That is **RK-21**, named in the DESIGN as shipped, pre-existing and explicitly out of
  scope: `run_logging.py`'s `main()` has no `except RunLoggingError` mapping, so gates 5 and 7
  inherit the same convention its sibling `final-review-audit-write --attempt 0` already had
  (re-measured: `rc=1`, nothing written). It is fail-closed, so F-801's safety property holds. Not
  fixed here, and T-13.8 comments the reason at the assertion.
* **Import-graph claim re-verified.** No import was added anywhere: `review_isolation` already
  imported `run_logging` at `:68`, `e2e_harness` at `:17`. `run_logging` imports neither, so no
  cycle is possible.
* **Scope held.** Nothing was touched in the sandbox isolation mechanism, the F-501 relay shim, the
  F-601 redaction-ordering fix, the evidence-bundle sanitization, D-6.0–D-6.9, mandatory pass B,
  D-I, `VERSION`, `LICENSE-DECISION.md`, or any lifecycle/Risk/Quality/Agent-Profile semantics. No
  signature, default, `--attempt` declaration, exit code, exception hierarchy or `ISOLATION.json`
  schema version changed.
* **The one type change, as designed:** `e2e_harness.final_review_artifact_path()` now raises
  `RunLoggingError` where it raised a bare `ValueError`. `RunLoggingError` subclasses `ValueError`
  (`run_logging.py:161`), so it is a subtype-compatible substitution; T-13.9 pins it with a
  deliberate `assertRaises(ValueError)`.

## Review Feedback Resolution

Iteration 1 of IMPLEMENTATION for run `run_028d416e596a` — no prior implementation-level findings to
resolve. The DESIGN-level findings this work implements (F-602, F-701, F-801) were closed at the
design level across two runs and two iterations; `REVIEW_DESIGN_iteration2.md` records `RESULT: PASS`
with no blocking or non-blocking findings.

**Open and deliberately not closed here**, both named by the approved design:

* **RK-19** — `.gitattributes` undermatches above attempt 99. Unchanged, and T-13.5 asserts attempt
  100 remains legal.
* **RK-21** — door 2's refusals surface as tracebacks rather than `input error:` lines. Shipped,
  pre-existing, fail-closed, and explicitly out of F-801's scope.
* **D-A.6″'s seven-rule `.gitattributes` block is not implemented in this tree**, which is why
  step 6 was an add rather than an update. Flagged above under Summary so the coordinator can route
  it rather than have it discovered later.
