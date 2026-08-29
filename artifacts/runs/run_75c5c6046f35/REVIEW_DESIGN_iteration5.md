# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

F-601 is closed: D-7.3-prime and D-7.8-prime place redaction inside `relay_validate()`, construct a
frozen residue-checked `RelayRequest`, and make that type the only accepted input to
`relay_build_argv()`. The specified serving loop cannot construct argv from the raw mapping, and
T-11.10 through T-11.12 provide end-to-end, boundary, and source/behavior regression evidence.

F-602 is not fully closed. The proposed globs correctly reject the three required near misses, but
the design's claimed output language does not match the shipped `repatriate()` interface: both the
function and CLI accept arbitrary integers, including 0 and negative values, and generate names the
seven rules do not cover. IMPLEMENTATION therefore lacks an unambiguous contract for whether to
reject those attempts or exempt their generated paths.

## Blocking Findings

ID: F-701
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `DESIGN.md` iteration 5, Current Architecture item 3, D-A.6-double-prime, M-13d, T-12;
`scripts/review_isolation.py:2594-2615`; `scripts/final_review_eval.py:1361`
Issue: The design states that `repatriate()` generates only the base name for attempt 1 and numeric
retry names for integers N >= 2, yet the actual callable and CLI do not enforce that domain.
`repatriate(..., attempt=0)` generates `FINAL_REVIEW_iteration0.md` and
`final_review_workspace_iteration0/`; a negative value generates an `_iteration-1` form. The
proposed rules deliberately leave these paths unexempted and T-12 calls `_iteration0` a
non-generated near miss, so the specified match set is not derived from the actual output language.
Reason / Evidence: Direct source inspection found `suffix = "" if attempt == 1 else
f"_iteration{attempt}"` with no range check in `repatriate()`. The CLI uses
`add_argument("--attempt", type=int, default=1)` with no positive-integer validator. Independent
`git check-attr` against the seven proposed rules returned `unset` for the base and iteration-2
forms, and `unspecified` for `_secret`, `_backup`, the voided filename, `_iteration0`, and
`_iteration-1`; thus the near-miss correction works as measured, but the latter two are currently
constructible outputs rather than proven near misses. The explicit review contract requires the
patterns to be checked against the exact names `repatriate()` generates.
Required Action: Define and enforce the attempt domain. Prefer specifying validation at both the
public `repatriate()` boundary and CLI parsing boundary that refuses `attempt < 1`, then add direct
function and CLI tests for 0 and negative attempts; alternatively, revise the attribute language
and tests to cover every integer the API intentionally accepts. Re-run `git check-attr` after the
contract and implementation shape agree.

## Non-Blocking Findings

None.

## Test Review

- T-11.10 checks that a real raw path/capability-bearing request reaches the recording stub only in
  redacted form and leaves no raw residue in relay/session artifacts.
- T-11.11 and T-11.12 make the F-601 boundary structural: direct raw construction is refused, a
  mapping cannot reach argv construction, and the serving path passes only `RelayRequest`.
- The proposed T-12 inventory contains the required `_secret`, `_backup`, and voided-attempt
  negatives and uses real `git check-attr`; however, its classification of attempts 0 and below
  depends on a range invariant that neither the function nor CLI currently has.

## Evidence Checked

- Read the complete DESIGN iteration-5 correction and REVIEW_DESIGN_iteration4.md.
- Inspected `repatriate()` and the `--attempt` argparse declaration directly.
- Ran `git check-attr whitespace --stdin` using the exact seven D-A.6-double-prime rules against
  generated positive shapes and the required same-directory near misses, plus attempts 0 and -1.
- Confirmed the delta does not reopen option (c), B1 preservation, probe ordering, F-401, F-402,
  D-H.2, RK-7, mandatory pass B, D-I, or D-6.0 through D-6.9.

## Final Decision

FAIL. F-601 is genuinely closed, and the corrected patterns solve the named overmatch cases, but
F-602 remains blocked by the unstated and unenforced attempt-domain assumption. Because this is the
last DESIGN iteration, IMPLEMENTATION must not guess whether invalid attempts are forbidden or are
part of `repatriate()`'s supported output language.
