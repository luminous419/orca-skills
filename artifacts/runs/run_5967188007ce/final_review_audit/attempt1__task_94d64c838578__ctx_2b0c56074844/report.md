# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The implementation does not satisfy the approved publication contract. Direct source inspection and focused runtime probes found five independent contract violations: settings precedence is reversed, invalid destination tiers do not fall back, exact quota limits are rejected, batch publication ignores the resolved destination tier for quota, and republish writes without validation. The supplied 20-test suite passes, but it omits the contract boundaries and cross-source conflicts that expose all five defects.

## Blocking Findings

ID: R1
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Responsible Phase: implementation
Location: src/config.py:16-18
Issue: Effective settings use the opposite precedence from the contract for colliding keys.
Reason / Evidence: CONTRACT.md:3-7 requires explicit > destination > project > built-in. Python mapping unpacking is last-write-wins, but `resolve_settings` constructs `{**explicit, **destination, **project, **BUILTIN_DEFAULTS}`. A direct probe with `max_items` values 1, 2, and 3 returned the built-in value 100 instead of the explicit value 1; project values likewise override destination values.
Required Action: Merge sources from lowest to highest precedence (built-in, project, destination, explicit) and add collision tests covering every adjacent and end-to-end precedence relationship.

ID: R2
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Responsible Phase: implementation
Location: src/policy.py:6-15
Issue: Unknown or empty destination tier values are returned as effective tiers instead of falling back to `default`.
Reason / Evidence: CONTRACT.md:9-12 permits a destination tier only when it names an entry in `TIERS`; unknown, typo, and empty values must resolve to `default`. `resolve_tier` returns any present value without membership validation, while `tier_limits` converts an unknown tier into `max_items: None`; a direct probe resolved `{"retention_tier": "typo"}` to `typo`. This also turns malformed configuration into unlimited quota.
Required Action: Validate destination tier membership in `TIERS` and return `default` for every invalid or empty value; remove the unlimited fallback for unknown tiers and test typo and empty-string cases.

ID: R3
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Responsible Phase: implementation
Location: src/quota.py:10-19
Issue: A publication resulting in exactly `max_items` records is rejected.
Reason / Evidence: CONTRACT.md:14-16 explicitly accepts exactly the maximum and rejects only values that exceed it. `enforce_quota` uses `len(store) < limit`; a direct probe of a 100-record resulting store against the default limit returned `False`.
Required Action: Make the boundary inclusive (`<=`) and add tests for exactly the limit and one above it for at least the default and a non-default tier.

ID: R4
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Responsible Phase: implementation
Location: src/pipeline.py:22-28
Issue: `publish_batch` does not evaluate quota against the destination's resolved tier.
Reason / Evidence: CONTRACT.md:18-20 requires all three publication paths to use the destination tier. Unlike `publish_one` and `republish`, `publish_batch` calls `enforce_quota(..., settings)` before resolving the tier and therefore uses the function's default tier. A direct probe of an extended-tier batch with a 100-record existing store raised `QuotaExceeded`, even though the extended tier permits up to 500 records.
Required Action: Resolve the destination tier before quota enforcement and pass it to `enforce_quota`; add per-path integration tests demonstrating a non-default tier changes the quota decision.

ID: R5
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Responsible Phase: implementation
Location: src/pipeline.py:31-36
Issue: `republish` writes records without calling `validate_record()`.
Reason / Evidence: CONTRACT.md:22-24 requires every store-writing path to validate first with no exemption. `republish` proceeds directly from tier/quota resolution to `_write_record`; a direct probe with only an `id` field was accepted and written rather than raising `InvalidRecord`.
Required Action: Validate the record at the start of `republish`, before quota evaluation or writing, and add invalid-record tests for that path.

## Non-Blocking Findings

None.

## Test Review

`python3 -m unittest discover -v` ran all 20 supplied tests successfully. That passing result is not meaningful evidence for the principal feature risks because the tests check only non-colliding settings, known tier names, values well below or above quota, default-tier batch behavior, and valid republish input. Focused probes directly derived from CONTRACT.md failed for precedence collisions, typo-tier fallback, the exact-limit boundary, extended-tier batch quota, and republish validation. Tests should be expanded around those contract boundaries and applied uniformly to all publication entry points.

## Evidence Checked

- Read CONTRACT.md in full and mapped each normative clause to production behavior.
- Read DIFF.patch in full to identify the base-to-head changes and possible omissions.
- Inspected every file under `src/` and `tests/` directly with line numbers rather than relying on the diff.
- Read MANIFEST.json and confirmed the intended review fixture file set.
- Ran the complete supplied unittest suite: 20 tests passed.
- Ran focused runtime probes for source precedence, invalid-tier resolution, exact-limit quota, extended-tier batch quota, and invalid republish input.
- Checked the diff for unrelated scope, destructive behavior, secrets, and hidden external-contract changes; no additional issue was found beyond the blocking behavioral defects above.

## Final Decision

FAIL. The change violates explicit requirements in all core areas of the feature: resolution precedence, tier validity, quota boundary semantics, consistent tier application, and validation coverage. The five blocking findings must be corrected and covered by adversarial tests before this implementation can pass final review.
