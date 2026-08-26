# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

Iteration 4 makes a well-supported choice of the outbound relay over options (a), (b), and (d),
keeps B1 honest, places `orca_check_probe()` before the expensive checks, and leaves the settled
iteration-1-to-3 contracts closed. F-501 and F-503 are nevertheless not ready for unambiguous
IMPLEMENTATION: the relay's specified validation/redaction data flow can execute an argv containing
unredacted agent text, and the two new attribute patterns exempt path names outside the finite
language produced by `repatriate()` while the proposed regression test checks only literal rule
strings.

## Blocking Findings

ID: F-601
Quality Attribute: G4
Severity: MAJOR
Blocking: YES
Location: `DESIGN.md` D-7.3, D-7.8 `relay_validate()` / `relay_serve()` data flow, RK-16
Issue: The enforcement interface returns a complete argv before redaction, while the serving loop
is specified as `relay_validate()` then `redact_text()` then `subprocess.run(argv)`. Redacting the
request after argv construction does not redact the already-constructed argv, so an implementation
following the stated sequence can send raw session paths, usernames, or credential-shaped text to
the Run mailbox despite D-7.3's claim that the relay redacts before building argv.
Reason / Evidence: D-7.3 says the relay applies `run_logging.redact_text()` to `subject` and `body`
"before building the argv." D-7.8 instead defines `relay_validate(request, credential) -> list[str]`
as returning "the complete argv," then defines `relay_serve()` as
`relay_validate() -> redact_text() over subject/body -> subprocess.run(argv)`. RK-16 relies on the
redaction as a security mitigation, and the mailbox is expressly outside B3's retained-family grep,
so this is not harmless ordering prose. The T-11 plan checks exact argv construction but does not
require a raw path-bearing subject/body to be absent from the executed stub argv and present only in
redacted form.
Required Action: Specify a single enforceable order and type boundary: for example, parse and
validate into a normalized request, redact its `subject` and `body`, and only then build the argv
from the redacted values (or pass a redacted request into the pure argv builder). Add a test whose
request contains a real P-PATH-rejected username/session spelling and assert the recording stub
receives only the redacted value and the raw value appears nowhere in the relay log.

ID: F-602
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `DESIGN.md` D-A.6-prime; proposed `.gitattributes` rules 2 and 3; M-10/M-11 and proposed
`test_the_gitattributes_rules_are_exactly_the_ones_designed`
Issue: The proposed patterns are not exact descriptions of `repatriate()` output. They exempt
arbitrary suffixes after `FINAL_REVIEW` and `final_review_workspace`, including names that the code
cannot generate, while the stated requirement and review contract require coverage without
over-widening.
Reason / Evidence: Actual `repatriate()` computes only `suffix = "" if attempt == 1 else
f"_iteration{attempt}"`. Therefore it produces only `FINAL_REVIEW.md` or
`FINAL_REVIEW_iteration<integer>.md`, and only `final_review_workspace/` or
`final_review_workspace_iteration<integer>/`. The proposed `FINAL_REVIEW*.md` also matches such
names as `FINAL_REVIEW_secret.md` and the design's own cited
`FINAL_REVIEW_iteration3_voided_ctx_55d1c349a3e5.md`; `final_review_workspace*/**` likewise matches
`final_review_workspace_backup/**`. M-10 tests an outside-run near miss but no same-directory
suffix near miss. The renamed fixed-list test proves only that the broad patterns were copied
verbatim; it cannot prove their match set is narrow.
Required Action: Choose patterns/rules that distinguish the base destination and the numeric retry
form as tightly as `.gitattributes` syntax permits, explicitly document any irreducible glob
overmatch, and add `git check-attr` negative cases inside `artifacts/runs/<run>/` for non-generated
suffixes (including the cited voided form and a workspace backup form). If exact numeric matching
cannot be expressed in one glob, do not preserve the arbitrary "exactly three rules" count at the
expense of the exact-scope requirement; make the count a consequence of the narrow patterns.

## Non-Blocking Findings

None.

## Test Review

- F-501's measured premise and option analysis are persuasive. Option (b) remains incompatible
  with the fail-closed scan, option (a) loses live lifecycle messages, and option (d) would erase
  B1's distinct settlement assertion; choosing (c) is not a default-to-easiest decision.
- The channel capability is otherwise bounded coherently: the sandbox supplies typed request data
  only; the relay injects dispatch authority from unreadable `control/`; forbidden verbs and
  authority-bearing keys are refused; no inbound reply channel is designed; and the real-CLI
  denial remains part of the probe battery.
- The probe ordering is explicit and testable: profile write, unconditional `orca_check_probe()`,
  `preflight_probe()`, then `run_probes()`. T-11.8 directly guards that ordering.
- F-503 correctly identifies both destinations in the actual code and correctly leaves the JSON
  destinations unexempted. The positive whitespace test is useful, but same-directory negative
  match cases are required to substantiate the claimed narrowness.

## Evidence Checked

- Read the complete `DESIGN.md` iteration-4 delta, especially D-7.1 through D-7.9, D-A.6-prime,
  interfaces, error handling, implementation steps, tests, and risks.
- Read TEST F-501 and F-503 and `REVIEW_TEST_iteration2.md`; compared the design claims with the
  independently confirmed failure modes.
- Inspected `scripts/review_isolation.py` `repatriate()` directly: its suffix language is the empty
  string or `_iteration{attempt}`, and its destinations are the report, JSON attestation, and
  workspace tree described above.
- Checked that the delta explicitly leaves F-401, F-402, D-H.2, RK-7, mandatory pass B, D-I, and
  D-6.0 through D-6.9 closed; neither finding requires reopening them.

## Final Decision

FAIL. The selected F-501 architecture and probe ordering are viable, but the redaction/argv order
must be made internally consistent before implementation can preserve the claimed outbound-channel
security property. F-503 also remains open until the attribute match set and its tests are narrowed
to the names `repatriate()` can generate, or any unavoidable syntax-level overmatch is explicitly
bounded and justified rather than called exact.
