# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The attempt-domain implementation itself remains correct, and the required regression suites
independently reproduce the Worker's counts. Negative matrices at the inspected GATE 2, GATE 6,
and GATE 7 tests genuinely invoke their production boundaries and assert the expected exception or
message; positive path/name/provenance assertions, including the relevant attempt `100` cases, are
not tautologies. However, T-13.1's claimed proof that invalid `isolate()` attempts build no session
observes the wrong directory and remains green when the gate is moved after `build_session()`.
That missing validation evidence is blocking under G5.

## Blocking Findings

ID: F-1001  
Quality Attribute: G5  
Severity: MAJOR  
Blocking: YES  
Location: `scripts/test_review_isolation.py:2269-2292`, especially the snapshots of
`Path(tempfile.gettempdir()).glob(f"{SESSION_PREFIX}*")`; `scripts/review_isolation.py:1896-1897`  
Issue: T-13.1's “builds no session” assertion cannot observe sessions created by the call under
test. The test passes `session_base=self.base`, while `build_session()` creates the session directly
under that supplied base; the test instead snapshots only direct children of the system temp
directory. Its companion `assertNothingCreated()` checks `self.base / "artifacts"`, whereas an
isolation session would create content below `self.base / <SESSION_PREFIX...> / review_root`.
  
Reason / Evidence: In a detached throwaway worktree at current HEAD, I moved GATE 2 from the first
statement of `isolate()` to immediately after `build_session()`. The focused command
`python3 -m pytest scripts/test_review_isolation.py -q -k 't131_isolate'` still returned
`1 passed, 3 subtests passed`; therefore the side-effect assertion would pass even after the exact
regression it claims to detect. On the same mutant, T-13.4's AST ordering test failed specifically
for `isolate`, confirming that production ordering is independently protected but not rescuing the
false T-13.1 coverage claim.  
Required Action: Snapshot `self.base.glob(f"{SESSION_PREFIX}*")` before and after the invalid calls
(and assert no change), or remove the inert side-effect assertions and correct the coverage claim if
the AST-order test is intentionally the sole ordering evidence.

## Non-Blocking Findings

None.

## Test Review

- GATE 2: the negative tests call `review_isolation.isolate()` over the full invalid matrix and
  assert `IsolationAttemptDomainError` plus both message forms. Gate-removal/relocation does not
  produce a false negative for exception presence, and T-13.4 pins first-statement ordering, but the
  specific no-session assertion is inert as described in F-1001.
- GATE 6: `FinalReviewArtifactPathAttemptDomainTests` calls the production path function over all
  eight invalid values, asserts the preserved `ValueError` supertype and shared-predicate message,
  and checks exact shipped strings for valid attempts including `100`.
- GATE 7: `AttemptDomainProvenanceTests` calls the production provenance reader over all eight
  invalid values, asserts exact messages, patches the real record iterator to prove no scan on
  refusal, exercises the CLI refusal, and verifies valid provenance output/grouping.
- Regression assertions are value-producing checks rather than tautologies: ladder/artifact tests
  compare exact filenames, repatriation checks exact destinations and digest, attestation
  round-trips `1`, `2`, and `100` as JSON integers, and provenance checks actual JSON output.

## Evidence Checked

1. Required four-suite run:
   `python3 -m pytest scripts/test_review_isolation.py scripts/test_final_review_eval.py scripts/test_run_logging.py scripts/test_e2e_harness.py -q`
   -> `2 failed, 562 passed, 1 warning, 1462 subtests passed in 715.93s`. The only failures were the
   two `RetainedReportWhitespaceExemptionTests` named by the Worker.
2. Required broader run:
   `python3 -m pytest scripts/test_os22_required_tests.py scripts/test_orca_runtime_contract.py scripts/test_validate_skills.py -q`
   -> `370 passed, 8 warnings, 3590 subtests passed in 16.71s`.
3. Required validator: `python3 scripts/validate_skills.py`
   -> `Skill validation PASSED (463 checks)`.
4. Pre-existing baseline reproduction: detached worktree at `8411cce`, the parent of attempt-domain
   commit `467cdc9`; focused whitespace command returned the same two failures with
   `2 failed, 5 passed, 176 deselected, 1 warning, 38 subtests passed`.
5. Independent mutant: relocating only GATE 2 after `build_session()` left T-13.1 green
   (`1 passed, 3 subtests passed`) while T-13.4 failed for `function='isolate'`.

## Final Decision

FAIL. The production invariant and general regression evidence are sound, and the two suite
failures are independently confirmed as pre-existing. Nevertheless, the TEST phase explicitly
requires genuine negative-case evidence, and one asserted side-effect guarantee is demonstrably
inert; F-1001 violates G5 until the test or the associated claim is corrected.
