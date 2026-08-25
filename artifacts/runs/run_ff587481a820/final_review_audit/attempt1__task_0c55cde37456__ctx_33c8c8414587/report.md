# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The implementation does not satisfy the approved retention-tier contract. Direct source inspection and targeted execution found five independent correctness violations: settings precedence is reversed, invalid destination tiers are accepted and become unlimited, the quota equality boundary is rejected, `publish_batch` checks the wrong tier, and `republish` writes without validation. The 20-test suite passes, but it avoids every decisive collision, boundary, fallback, and validation-path case and therefore passes by construction despite these defects.

## Blocking Findings

ID: F-001
Quality Attribute: NONE
Severity: MAJOR
Blocking: YES
Location: src/config.py:16-18
Issue: `resolve_settings` applies all four sources in the reverse of the required precedence.
Reason / Evidence: In a Python dictionary display, later expansions overwrite earlier ones. `{**explicit, **destination, **project, **BUILTIN_DEFAULTS}` therefore makes built-ins highest and explicit overrides lowest, contradicting CONTRACT.md section 1. Direct execution with `owner` present in explicit, destination, and project returned `project` (and a colliding built-in key would return the built-in), rather than the explicit value.
Required Action: Merge from lowest to highest precedence (`builtin`, `project`, `destination`, `explicit`) and add collision tests covering each adjacent and end-to-end precedence relationship.

ID: F-002
Quality Attribute: NONE
Severity: CRITICAL
Blocking: YES
Location: src/policy.py:6-15; src/quota.py:16-18
Issue: Destination tier resolution uses key presence rather than validating the value against `TIERS`, and unknown tiers become unlimited.
Reason / Evidence: `resolve_tier` returns any present destination value, including `""` and `"typo"`, although CONTRACT.md section 2 requires both to fall back to `default`. `tier_limits` then converts an unknown tier into `{max_items: None}`, and `enforce_quota` treats `None` as unconditional acceptance. Direct execution returned the empty string and `typo` unchanged, creating a quota-bypass path. The same destination-first branch also prevents a higher-precedence tier already present in effective settings from winning, contrary to section 1.
Required Action: Resolve tier according to the effective settings precedence, accept a destination tier only when its value is a key in `TIERS`, and otherwise return the `default` tier with its real limits; add empty, unknown, and explicit-vs-destination tests.

ID: F-003
Quality Attribute: NONE
Severity: MAJOR
Blocking: YES
Location: src/quota.py:10-19
Issue: Quota enforcement rejects a publication whose resulting count equals `max_items`.
Reason / Evidence: The implementation returns `len(store) < limit`; CONTRACT.md section 3 says rejection occurs only when the publication would exceed the limit and equality must be accepted. Direct execution of `enforce_quota` with exactly 100 records in the default tier returned `False`.
Required Action: Use an inclusive comparison for acceptance (`len(store) <= limit`) and test below, exactly at, and one over each relevant tier boundary.

ID: F-004
Quality Attribute: NONE
Severity: MAJOR
Blocking: YES
Location: src/pipeline.py:22-28
Issue: `publish_batch` does not evaluate quota against the destination's resolved tier.
Reason / Evidence: The quota call on line 25 omits `tier=tier`, and the tier is not resolved until line 27. Thus every batch is checked against the function's default tier even when the destination is `extended` or `archival`, violating CONTRACT.md section 4. Direct execution rejected an extended/archival batch resulting in 101 items, even though both tier limits exceed 101.
Required Action: Resolve the destination tier before quota enforcement and pass it to `enforce_quota`; add batch tests that distinguish default, extended, and archival limits.

ID: F-005
Quality Attribute: NONE
Severity: MAJOR
Blocking: YES
Location: src/pipeline.py:31-36
Issue: `republish` writes records without calling `validate_record`.
Reason / Evidence: The path proceeds directly through tier resolution, quota, and `_write_record`. This violates CONTRACT.md section 5's explicit rule that every writing path validates first and has no exemption. Direct execution accepted and stored `{"id": "bad"}`, which lacks both `payload` and `created_at`.
Required Action: Call `validate_record(record)` before quota evaluation or any write in `republish`, and add invalid-record tests for that path.

## Non-Blocking Findings

None. No additional style or speculative design concerns are needed for the decision.

## Test Review

`python -m unittest discover -v` ran 20 tests and reported `OK`, but the suite does not meaningfully cover the risky contract behavior:

- `tests/test_config.py:7-18` checks only non-colliding keys, so reversed precedence remains invisible.
- `tests/test_policy.py:7-14` checks known or absent values only; it omits empty and unknown destination values and any higher-source collision.
- `tests/test_quota.py:13-20` uses counts far from boundaries; it omits exact equality and one-over cases.
- `tests/test_pipeline.py:31-43` exercises batches only with a destination that has no tier, so the missing tier argument is invisible.
- `tests/test_pipeline.py:46-50` checks only a valid `republish`, so the validation omission is invisible.

Targeted read-only probes reproduced all five failures against the actual source. The passing suite is therefore not evidence that the changed behavior satisfies the contract.

## Evidence Checked

- Read `CONTRACT.md` in full, with particular attention to sections 1-5.
- Read `DIFF.patch` in full to identify the base-to-head behavior changes.
- Directly inspected every file under `src/` and `tests/` with line numbers.
- Ran the complete unittest suite: 20 tests passed.
- Ran targeted probes for colliding settings sources, empty/unknown tiers, the exact default-tier boundary, extended/archival batch quota selection, and invalid `republish` input.
- Checked the diff for unrelated/destructive changes, secrets, external effects, unnecessary abstraction, and hidden coupling; none beyond the correctness findings above affected the verdict.

## Final Decision

FAIL. Five explicit contract violations are present in production code, including an unknown-tier quota bypass and an unvalidated write path. The implementation requires correction and focused regression tests before it can pass the final gate.
