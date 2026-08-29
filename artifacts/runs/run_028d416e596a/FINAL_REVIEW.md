# Final Adversarial Review

RESULT: PASS
REVIEW_VERDICT: PASS

## Summary

The attempt-domain defect is closed. One shared predicate accepts only exact Python `int` values
greater than or equal to 1 (therefore rejecting `bool`) and every independently reachable boundary
that turns an attempt into identity, a path, retained content, attestation content, or CLI output
enforces it. The two CLI doors also fail closed: the isolation CLI maps parsed out-of-domain
integers to input-error exit 1, while unparseable text remains argparse exit 2; the provenance CLI
returns non-zero and emits no JSON for an invalid attempt.

No blocking or non-blocking finding remains. The seven-gate design plus the second CLI door is
proportionate rather than over-engineered: the census found distinct public/direct-call surfaces
in three modules, and relying only on upstream callers would leave constructible filenames or
documents through library entry points. The existing `final_review_dispatch_key()` guard was
refactored onto the same predicate without changing its exception or message contract.

## Blocking Findings

None.

## Non-Blocking Findings

None.

## Independent Review Evidence

- Audited `git diff 6c65240..HEAD` and independently re-censused attempt consumers. The effective
  boundaries are `review_isolation.repatriate()`, `isolate()`, `build_attestation()`, the
  `final_review_eval.py isolate --attempt` handler, `run_logging.final_review_report_ladder_path()`,
  `read_final_review_attempt_provenance()`, `e2e_harness.final_review_artifact_path()`, and the
  pre-existing `run_logging.final_review_dispatch_key()` extraction site.
- Ran a direct-call matrix over `0`, `-1`, `-12`, `False`, `True`, `2.0`, `"2"`, and `None` against
  the path, dispatch-key, provenance, and e2e artifact producers. Every call failed with a
  `ValueError`-compatible facade and no invalid artifact was produced.
- Independently invoked the isolation CLI. Attempts `0` and `-1` returned exit 1, empty stdout,
  and the shared domain message; `abc` returned argparse exit 2 with empty stdout. Valid attempts,
  including 100, remain covered by exact-name/content assertions.
- Confirmed `scripts/run_logging.py` and the Skill's shipped mirror are byte-identical. Confirmed
  `git diff --check 6c65240..HEAD` passes.
- Ran the complete prescribed regression command over all seven test files: **932 passed, 2 failed,
  5052 subtests passed** in 753.60 seconds. The only failures are the two
  `RetainedReportWhitespaceExemptionTests` cases.
- Checked out predecessor commit `6c65240` in a detached throwaway worktree and ran the focused
  retained-report class: it reproduced the same **2 failed, 5 passed, 38 subtests passed** result
  against historical trailing whitespace. The worktree was removed afterward; these failures are
  pre-existing baseline noise, not regressions from this run.
- Ran `python3 scripts/validate_skills.py`: **Skill validation PASSED (463 checks)**.

## Checklist Assessment

- **A — Objective alignment:** F-602/F-701 are closed. Invalid attempts cannot create the
  `_iteration0`, `_iteration-1`, `_iterationFalse`, or `_iteration2.0` filename families, cannot
  alias `True` to attempt 1, and cannot enter attestation/provenance output.
- **B/C — Cross-phase consistency and contract vs code:** The iteration-2 design census, final
  implementation, and final tests agree on the shared predicate, exception facades, seven new
  gates, pre-existing dispatch-key extraction, and two CLI doors.
- **D — Tests vs real risk:** Negative tests exercise all invalid value classes, side-effect
  ordering, exact messages, CLI exits, valid values, mirror identity, and the corrected live
  session-directory assertion. The F-1001 mutation evidence is consistent with the current test.
- **E/F — Docs and lifecycle:** The `.gitattributes` explanation accurately distinguishes seven
  newly specified boundaries from the already guarded dispatch key. No lifecycle semantics were
  changed.
- **G/H/I — Security, scope, complexity, coupling:** The shared pure predicate preserves each
  module's established exception facade and avoids a cross-module exception-identity regression.
  No destructive behavior was introduced. The sandbox isolation, relay shim, redaction ordering,
  evidence-bundle sanitization, D-6.x, mandatory pass B, D-I, VERSION, LICENSE-DECISION.md, and
  Risk/Quality/Agent Profile semantics have no diff from `6c65240`.

## Final Decision

PASS. The requested fail-closed attempt domain is enforced at every real entry point, its negative
evidence is meaningful, the full regression result matches the independently reproduced baseline
exception, and the protected settled surfaces remain untouched.
