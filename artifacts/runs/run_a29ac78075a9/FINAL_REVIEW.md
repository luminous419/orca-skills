RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES

## Summary

The run satisfies R1-R9. The production change removes Apple's marked `/usr/bin/python3` tool shim from both actual probe launch sites, resolves only to a real interpreter or raises fail-closed, and does not alter the Seatbelt profile, readable-set admission, recursive immutability proof, mandatory NEG-5 scan, or probe oracles. The historical pre-reinstall cause remains unreproducible and is labelled as inference in the phase artifacts and PR body; the narrower claim that the marked shim was on the launch line and is no longer executed is supported by the code, host observations, and regression tests.

I independently verified the four-file committed scope, unchanged prior run history/VERSION/LICENSE, Draft/open/unmerged PR #20, and green Actions run `33098354940` at exact head `db5f6018a37d34836bdec90e1f3a67a002b19d06` for Python 3.11/3.12/3.13. Recorded real-macOS evidence shows S1/S2/S3 and NEG-0 through NEG-8 PASS, including NEG-5 scanning 182,986 files across 11 admitted roots with zero hits and no subpath admission for any `NEVER_ADMITTED` root.

## Blocking Findings

None.

## Non-Blocking Findings

ID: N-201  
Quality Attribute: NONE  
Severity: MINOR  
Blocking: NO  
Responsible Phase: test  
Location: `artifacts/runs/run_a29ac78075a9/TEST.md:291`, final blank line; `artifacts/runs/run_a29ac78075a9/REVIEW_TEST.md:45`  
Issue: Both test artifacts report `git diff --check` as clean, but `git diff --check 90e2071..HEAD` currently reports `artifacts/runs/run_a29ac78075a9/TEST.md:590: new blank line at EOF.`  
Reason / Evidence: I reproduced the diagnostic against committed HEAD, and `git show --check db5f601` reports the same issue. Actions nevertheless passed its whitespace step, so this is a small artifact-format/validation-report discrepancy, not a functional, security, CI, or explicit-requirement failure.  
Required Action: In a future artifact-writing pass, run the final whitespace check after the artifact has reached its committed form.

## Test Review

- At HEAD, all 17 `ProbeInterpreterShimTests` and all 5 `ProbeLaunchWiringTests` pass.
- In a fresh `git clone --local` throwaway clone, I restored the defect by changing both real exec sites (`_run_probe` and `preflight_probe`) back to `SYSTEM_PYTHON`. The five launch-wiring tests then produced three failures and one error, proving the regression guard is non-vacuous without executing the real shim.
- Resolver tests cover marked-shim detection, cross-buffer marker scanning, unreadable inputs, real-interpreter passthrough, resolution through candidate developer directories, rejection of another shim, and hard failure with no fallback. The outer preflight test additionally proves an unresolvable interpreter launches zero commands, bounding the retained `git --version` shim check by construction order.
- The exact values of `DEFAULT_IMM_CANDIDATES` and all seven `NEVER_ADMITTED` roots are pinned. The production diff contains no change to profile rendering, admission computation, immutable proof, cleanup handling, NEG-5, or sandbox command wrapping.
- The retained `git --version` check is a bounded residual rather than a hole in the reported fix: interpreter resolution occurs while the checks list is constructed and before any subprocess launch. The untested outer `isolate()` response to a returned `preflight["ok"] == false` is pre-existing and outside this delta; the new resolver exception itself propagates through the existing `except BaseException` cleanup path.
- The first attempted module-style unittest command was invalid because this repository expects discovery with `scripts` on the import path; the correctly scoped discovery runs above passed. No conclusion relies on the invalid invocation.

## Final Decision

PASS WITH NOTES. The installer-triggering Python shim is removed from the isolation launch line without reinstalling CLT or widening the sandbox, the regression tests demonstrably fail under a realistic reversion, normal macOS Seatbelt evidence and Linux CI are green, PR #20 accurately labels the lost historical cause as inference, and repository/PR scope constraints are preserved. N-201 is non-blocking because it is limited to a trailing blank line and an overstated local validation claim, with no effect on runtime behavior or required security evidence.
