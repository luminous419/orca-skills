# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The implementation does not satisfy the approved publication contract. Direct source inspection and focused runtime probes found five independent correctness violations: settings precedence is reversed, invalid destination tiers do not fall back to `default`, exact-quota publications are rejected, `publish_batch` ignores the destination tier for quota, and `republish` writes without validation. The shipped 20-test suite passes, but it does not exercise the contract boundaries that expose these defects.

## Blocking Findings

ID: F-001  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Responsible Phase: implementation  
Location: `src/config.py:16-18`  
Issue: Settings resolution implements the opposite of the required precedence.  
Reason / Evidence: `CONTRACT.md:3-7` requires explicit override > destination config > project defaults > built-in defaults. Python mapping unpacking is last-write-wins, but the implementation returns `{**explicit, **destination, **project, **BUILTIN_DEFAULTS}`, so lower-priority sources overwrite higher-priority sources. A direct probe with all three sources setting `owner` returned `project` instead of `explicit`; built-in keys likewise cannot be overridden.  
Required Action: Merge sources from lowest to highest precedence (built-in, project, destination, explicit) and add collision tests for every adjacent precedence boundary, including attempts to override built-in keys.

ID: F-002  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Responsible Phase: implementation  
Location: `src/policy.py:6-10`; `src/policy.py:13-15`  
Issue: Unknown and empty destination tier values are accepted instead of falling back to `default`.  
Reason / Evidence: `CONTRACT.md:9-12` permits a destination tier only when its value names an entry in `TIERS`; unknown, typo, and empty values must resolve to `default`. `resolve_tier` returns any present value without membership validation, while `tier_limits` converts unknown tiers into an unlimited quota. Direct probes returned `typo` and `""` unchanged.  
Required Action: Validate the destination value against `TIERS` and resolve every invalid value to `default`; add unknown, typo, empty-string, and valid-tier tests.

ID: F-003  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Responsible Phase: implementation  
Location: `src/quota.py:10-19`  
Issue: The quota comparison rejects a publication whose resulting size is exactly `max_items`.  
Reason / Evidence: `CONTRACT.md:14-16` says only a publication that exceeds the tier limit is rejected and explicitly accepts exactly `max_items`. The implementation uses `len(store) < limit`; a direct probe of 100 records against the default limit of 100 returned `False`.  
Required Action: Accept `len(store) <= limit` and add exact-boundary tests for each relevant tier, plus one-below and one-above coverage.

ID: F-004  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Responsible Phase: implementation  
Location: `src/pipeline.py:22-28`  
Issue: `publish_batch` enforces the default tier quota instead of the destination's resolved tier.  
Reason / Evidence: `CONTRACT.md:18-20` requires every publication path to evaluate quota against the destination's resolved tier. Unlike `publish_one` and `republish`, line 25 calls `enforce_quota` without the resolved tier, and the tier is not resolved until line 27. A focused probe publishing the 101st item to an `extended` destination raised `QuotaExceeded` even though that tier permits 500 items.  
Required Action: Resolve the tier before quota enforcement and pass it to `enforce_quota`; add batch quota tests that distinguish default, extended, and archival limits.

ID: F-005  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Responsible Phase: implementation  
Location: `src/pipeline.py:31-36`  
Issue: `republish` writes records without calling `validate_record`.  
Reason / Evidence: `CONTRACT.md:22-24` states that every store-writing path validates first with no exemptions. `republish` proceeds directly through tier and quota handling to `_write_record`; a direct probe with only an `id` field was accepted and written rather than raising `InvalidRecord`.  
Required Action: Call `validate_record(record)` before quota evaluation or writing and add republish tests for missing required fields and empty IDs.

## Non-Blocking Findings

None.

## Test Review

`python -m unittest discover -v` ran all 20 shipped tests successfully. That green result is not meaningful evidence for the highest-risk contract clauses because the tests pass by avoiding collisions and boundaries: `tests/test_config.py:7-18` never supplies competing keys; `tests/test_policy.py:6-14` omits invalid tier values; `tests/test_quota.py:12-20` omits exact limits; `tests/test_pipeline.py:31-43` never applies a non-default tier to a batch; and `tests/test_pipeline.py:46-50` gives `republish` only a valid record. Focused runtime probes directly demonstrated every blocking behavior described above.

## Evidence Checked

- Read the complete approved `CONTRACT.md` and full `DIFF.patch`.
- Inspected every file under `src/` and `tests/` directly with line numbers, plus `MANIFEST.json`.
- Verified the changed-file scope shown by `DIFF.patch`; no secret, destructive operation, or unrelated production abstraction was found.
- Ran the complete shipped test suite: 20 tests passed.
- Ran focused probes for precedence collisions, typo/empty tiers, exact quota, extended-tier batch quota, and invalid-record republishing.
- No project quality profile exists in the supplied review directory, so the explicit contract and Minimal General Gate govern the decision.

## Final Decision

FAIL. Findings F-001 through F-005 are direct violations of explicit requirements and therefore block approval under G1. The implementation and tests must be corrected and the full suite augmented with the omitted contract-boundary cases before re-review.
