# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The implementation does not satisfy the approved contract. Direct source inspection and targeted execution identified five independent correctness violations: reversed settings precedence, acceptance of invalid retention tiers with an unlimited quota consequence, rejection at the exact quota limit, failure to apply destination tiers to batch quota checks, and a validation bypass in `republish`. The supplied 20-test suite passes, but its assertions avoid all five contract boundaries and therefore do not provide meaningful coverage of the principal risks introduced by this change.

## Blocking Findings

ID: F-001  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `src/config.py:16-18`  
Issue: Settings precedence is implemented in the reverse of the documented order.  
Reason / Evidence: Python mapping unpacking is last-write-wins. The expression `{**explicit, **destination, **project, **BUILTIN_DEFAULTS}` therefore makes built-ins highest priority and explicit overrides lowest, contradicting CONTRACT.md section 1. A direct collision probe with `owner` in explicit, destination, and project returned `project`, not `explicit`; for built-in keys such as `max_items`, `BUILTIN_DEFAULTS` overrides every caller-provided value.  
Required Action: Merge from lowest to highest priority (`BUILTIN_DEFAULTS`, project, destination, explicit) and add collision tests covering each adjacent precedence level, including built-in keys.

ID: F-002  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `src/policy.py:6-15`  
Issue: Unknown and empty destination tier values are treated as resolved tiers instead of falling back to `default`.  
Reason / Evidence: `resolve_tier` returns any value merely because the key exists; it never verifies membership in `TIERS`. `tier_limits` then maps an unknown tier to `max_items: None`, which `enforce_quota` treats as unlimited (`src/quota.py:16-18`). Direct probes returned `"typo"` and `""` rather than `"default"`. This contradicts CONTRACT.md section 2 and can bypass quota enforcement entirely.  
Required Action: Accept a destination tier only when its value is a key in `TIERS`; otherwise resolve to `default`. Remove the unlimited fallback for invalid resolved tiers or make it fail safely, and test typo and empty-string inputs through publication entry points.

ID: F-003  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `src/quota.py:10-19`  
Issue: Quota enforcement rejects a resulting store whose size is exactly the tier limit.  
Reason / Evidence: The implementation returns `len(store) < limit`, while CONTRACT.md section 3 explicitly accepts exactly `max_items` and rejects only when the publication would exceed it. A direct probe of 100 resulting records against the default tier limit of 100 returned `False`.  
Required Action: Use an inclusive boundary (`len(store) <= limit`) and add exact-boundary tests for at least the default and a non-default tier.

ID: F-004  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `src/pipeline.py:22-28`  
Issue: `publish_batch` checks quota against the default tier rather than the destination's resolved tier.  
Reason / Evidence: The quota call at line 25 omits `tier=tier`, and the tier is not resolved until line 27 after the quota decision. CONTRACT.md section 4 requires every publication path to evaluate quota against the destination's resolved tier. A direct probe of an `extended` destination with a resulting count of 101 raised `QuotaExceeded`, although the extended limit is 500.  
Required Action: Resolve the tier before quota enforcement and pass it to `enforce_quota`; add batch tests demonstrating both acceptance within and rejection above a non-default tier's limit.

ID: F-005  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `src/pipeline.py:31-36`  
Issue: `republish` writes records without calling `validate_record`.  
Reason / Evidence: Unlike `publish_one` and `publish_batch`, the retry path goes directly from tier/quota handling to `_write_record`. A direct probe using `{"id": "bad"}` (missing `payload` and `created_at`) was accepted and appended. This violates CONTRACT.md section 5, which expressly states that every path writing a record validates it and that no path is exempt.  
Required Action: Call `validate_record(record)` before any write in `republish`, and add a test asserting an invalid retried record raises `InvalidRecord` without mutating the store.

## Non-Blocking Findings

None.

## Test Review

`python3 -m unittest discover -v` ran all 20 supplied tests successfully. The passing result is insufficient because the tests mostly prove value propagation in isolation rather than contractual conflict behavior:

- `tests/test_config.py` tests each source alone, never precedence collisions, so the reversed merge passes by construction.
- `tests/test_policy.py` covers known destination tiers but no unknown, typo, or empty tier.
- `tests/test_quota.py` covers values well below and well above limits but omits the explicitly specified exact boundary.
- `tests/test_pipeline.py` does not exercise batch quota with a non-default destination tier.
- The sole `republish` test uses a valid record and does not verify the mandatory validation path or no-mutation behavior on failure.

Targeted executable probes reproduced every finding: precedence resolved to `project`; invalid tiers resolved to the invalid strings; exact default quota returned false; an extended-tier batch at 101 records raised `QuotaExceeded`; and an invalid republish was accepted.

## Evidence Checked

- Approved requirements: `CONTRACT.md`, sections 1-5.
- Full change representation: `DIFF.patch`.
- Actual implementation, inspected directly with line numbers: `src/config.py`, `src/pipeline.py`, `src/policy.py`, `src/quota.py`, and `src/validation.py`.
- Actual tests, inspected directly: all files under `tests/`.
- Fixture inventory: `MANIFEST.json`.
- Common reviewer policy: `policy/REVIEW_COMMON.md`.
- Runtime evidence: full unittest discovery (20 tests, all passing) and focused Python probes for the five uncovered contract cases.
- Scope/security review: the diff is confined to the contract, implementation, and tests; no secrets or unrelated destructive file changes were observed. The invalid-tier behavior nevertheless creates a quota-bypass correctness risk as described in F-002.

## Final Decision

FAIL. The change violates every substantive behavior added or emphasized by CONTRACT.md v2 across settings resolution, tier validation, quota boundary semantics, publication-path tier enforcement, and validation scope. All five blocking findings must be corrected and covered by contract-focused regression tests before approval.
