# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The correction closes the originally demonstrated `0`/negative hole at the CLI,
`review_isolation.repatriate()`, and `review_isolation.isolate()` boundaries, defines a clear
fail-closed error contract, preserves valid attempts including 100, and specifies meaningful CLI,
direct-function, malformed-value, no-side-effect, and positive-path tests. It also records the
dependency between INV-ATTEMPT and the seven `.gitattributes` rules without reopening any settled
sandbox, relay, redaction, evidence-bundle, D-6, mandatory-pass-B, or D-I design.

However, the specification does not close **every real direct-call path that receives or converts
`attempt`**. It inventories two such paths and deliberately leaves both without complete boundary
validation: `build_attestation()` can directly construct retained attestation data with an invalid
`final_review_attempt`, and `run_logging.final_review_report_ladder_path()` can directly construct a
same-family filename from a non-integer such as `2.0`. That contradicts this review's explicit
all-entry-point requirement and the design's own exact domain statement.

## Blocking Findings

ID: F-801
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `artifacts/runs/run_028d416e596a/DESIGN.md` D-A.7.4, D-A.7.6, RK-20; current code `scripts/review_isolation.py:2448-2510` and `scripts/run_logging.py:1494-1499`
Issue: The declared attempt-domain invariant is not enforced at every real public direct-call boundary.
Reason / Evidence: D-A.7.1 declares that `attempt` has exact type `int`, excludes `bool`, and is at least 1. The entry-point census itself identifies `build_attestation(attempt=...)` as a public module-level function and `final_review_report_ladder_path(run_id, attempt)` as a distinct public producer of the same filename family. D-A.7.4 then explicitly declines a `build_attestation()` check because shipped `isolate()` calls it through a proposed gate, but a direct Python caller bypasses `isolate()` and the current function copies the unchecked value directly into the retained document's `final_review_attempt` field. Likewise RK-20 explicitly leaves `final_review_report_ladder_path(..., 2.0)` able to return `FINAL_REVIEW_iteration2.0.md`; guarding current internal callers does not guard the public helper's direct-call boundary. The task requires independent confirmation of “every real entry point,” including “any other direct-call path,” so call-graph reachability from shipped CLI paths is insufficient. The current-code reproduction also confirmed the motivating behavior: direct `repatriate()` calls with `0` and `-1` successfully created `FINAL_REVIEW_iteration0.md` / `FINAL_REVIEW_iteration-1.md` and matching workspace directories.
Required Action: Extend D-A.7 so every public boundary that accepts or turns this quantity into retained identity/path data validates the same domain. At minimum, validate first in `build_attestation()` and in `run_logging.final_review_report_ladder_path()` (prefer one shared invariant or explicitly identical checks without introducing a prohibited import cycle), then specify direct tests for `0`, negative integers, `bool`, float, string, and valid attempts at those boundaries. Update the entry-point census and dependency statement so no known bypass is categorized merely as guarded by its current callers.

## Non-Blocking Findings

None.

## Test Review

The proposed tests are strong for the three selected gates:

- T-13.1 covers direct `repatriate()` and `isolate()` calls with `0`, `-1`, and `-12`, including the important no-directory/no-session side-effect assertion.
- T-13.2 covers CLI `0` and negative values, all CLI branches, exit code 1, and the expected message.
- T-13.3 covers malformed CLI text with argparse exit 2 and non-`int` direct objects, including the `bool` aliasing case.
- T-13.5 is an adequate positive regression matrix and deliberately includes attempt 100, preserving the documented `.gitattributes` undermatch behavior.

The plan is incomplete only because it has no direct invalid/valid tests for the two omitted public
boundaries in F-801. T-13.4's source census assertion cannot close that gap: it asserts that
`repatriate()` and `isolate()` contain gates and that no second CLI exists, but it does not require
all public `attempt` consumers/producers to enforce the invariant.

## Evidence Checked

- Read the complete corrected `artifacts/runs/run_028d416e596a/DESIGN.md`, including D-A.6″ and the appended D-A.7 correction.
- Read `artifacts/runs/run_75c5c6046f35/REVIEW_DESIGN_iteration5.md` for the predecessor F-701/F-602 finding.
- Independently searched the repository for `attempt` and `--attempt`; inspected `final_review_eval.py`, `review_isolation.py`, and `run_logging.py` call surfaces rather than relying on M-19.
- Confirmed current CLI parsing uses `type=int, default=1` with no domain validator and both CLI branches forward `args.attempt` unchanged.
- Ran an equivalent minimal current-code reproduction against real temporary session files: direct `repatriate(..., attempt=0)` and `attempt=-1` both succeeded and created the unexempted filename/workspace forms.
- Confirmed the design preserves valid-attempt behavior, documents the seven-rule `.gitattributes` dependency, and leaves the settled remediation areas explicitly untouched.
- Loaded the version-matched Orca orchestration guide and applied the supplied common and DESIGN review policies.

## Final Decision

FAIL. The selected three-gate design is internally coherent and well tested, but the explicit
all-real-entry-points phase contract is not met while two known public direct-call boundaries remain
outside the invariant. Closing F-801 at the specification level is required before F-602/F-701 can
be considered fully resolved.
