# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The implementation is broadly coherent and its full validation suite passes, but the final repository state does not satisfy OS-22. Direct inspection found four blocking defects: retained audit evidence exposes an environment-specific absolute path and username, the claimed neutral baseline explicitly cues four seeded-defect archetypes and their contract sections, closed-world false-positive-rate computation reports zero while an unmatched finding is present, and committed baseline outputs expose answer-key identities. The byte-strict observability-neutrality golden, immutable per-dispatch publication, ordinary incomplete-adjudication refusal, versioned contract checks, and existing lifecycle/profile regression suite otherwise behaved as documented.

## Blocking Findings

ID: R1
Quality Attribute: G4
Severity: MAJOR
Blocking: YES
Responsible Phase: design
Location: scripts/run_logging.py:1037-1067,1598-1608; artifacts/runs/run_ff587481a820/final_review_audit/attempt1__task_0c55cde37456__ctx_33c8c8414587/input.md:10; artifacts/runs/run_ff587481a820/final_review_audit/attempt1__task_0c55cde37456__ctx_33c8c8414587/record.json:50-52
Issue: The retained audit family is not local-path safe and the shipped baseline artifacts contain a raw environment-specific absolute path with the local username embedded in it.
Reason / Evidence: Redaction policy `redaction/1.0` recognizes only `/Users/<segment>`, `/home/<segment>`, and `/root/<segment>`. The real baseline path begins `/private/tmp/claude-501/-Users-luminous-aiAssistedProjects-orca-skills/...`, so `_relative_artifact_path()` passes it through unchanged. That exact path is retained in `input.md`, `record.json.report.contract_path`, and `EXPORT_BUNDLE.json`; the username `luminous`, scratch UUID, and local workspace encoding remain visible. The record reports no input/report redactions even though this local path is present. This violates §4's secret-safe/environment-safe retention requirement and Completion Criterion 8.
Required Action: Make out-of-artifact-root absolute paths safe independently of home-directory spelling (for example, replace the entire absolute path with a deterministic placeholder or retain only a non-sensitive logical/hashed reference), apply the same rule to input/report content and `contract_path`, regenerate the affected retained baseline evidence, and add a regression test using `/private/tmp/.../-Users-<user>-...` and other non-home absolute paths.

ID: R2
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Responsible Phase: test
Location: artifacts/runs/run_ff587481a820/final_review_audit/attempt1__task_0c55cde37456__ctx_33c8c8414587/input.md:36-41; artifacts/runs/run_804e35d29531/BASELINE_RESULT.md:25-31,113-119
Issue: The retained §7 baseline input leaks seeded-defect identities/search hints and therefore is not a baseline of the unchanged Final Review detection policy.
Reason / Evidence: The Reviewer was explicitly told to “Pay particular attention” to value-vs-presence, boundary/equality, precedence/fallback, and validation-scope defects, and was pointed to CONTRACT.md sections 1, 2, 3, and 5. Those phrases directly identify four of the five answer-key archetypes (`value_vs_presence`, `equality_boundary`, `losing_precedence_fallback`, `validation_scope_gap`) and focus the search on their governing sections. This is materially stronger than the ordinary §17 A-I checklist and is precisely a detection/search-depth prompt alteration, despite `BASELINE_RESULT.md` claiming no detection/search policy change and no seeded-defect identity leakage. Grepping only for `SD-\d`, “answer key”, and “seeded defect” cannot detect this semantic leak. It violates §§5 and 7, Completion Criteria 10 and 13, and the explicit exclusion of OS-23 detection/search-quality work.
Required Action: Re-run the baseline with a neutral Reviewer input containing only the ordinary §11/§17 contract and the materialized subject evidence, without archetype names, targeted contract sections, expected defect classes, or fixture identity. Extend leak validation to compare semantic answer-key archetypes/hints rather than only literal IDs and a few phrases, and replace the retained baseline artifacts and result write-up.

ID: R3
Quality Attribute: G2
Severity: MAJOR
Blocking: YES
Responsible Phase: implementation
Location: scripts/final_review_eval.py:840-878; scripts/test_final_review_eval.py:603-621
Issue: Closed-world false-positive-rate computation is internally inconsistent and produces a false zero for unmatched findings.
Reason / Evidence: Unmatched findings remain classified `UNADJUDICATED` and increment neither `true_positives` nor `false_positives`. When `closed_world` is true, line 872 nevertheless enters the computed branch; precision's denominator implicitly penalizes every unmatched finding, but `false_positive_rate` uses only the explicit `false_positives` counter. The shipped test supplies `PERFECT_REPORT + NOISE_FINDING`, an exhaustive closed-world attestation, and no verdicts, then checks only that computation occurred. The resulting model necessarily has one unmatched finding yet reports `false_positive_rate = 0`, rather than `1 / findings_total`; it also calls adjudication status `partial`. Thus the metric contract is not correctly implemented on one of the two expressly permitted computation paths.
Required Action: Define and implement closed-world classification consistently: under a valid exhaustive attestation, classify all otherwise-unmatched findings as false positives (or require explicit adjudications and refuse computation). Assert exact precision, false-positive rate, classifications, and adjudication status in the closed-world regression test.

ID: R4
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Responsible Phase: test
Location: artifacts/runs/run_804e35d29531/BASELINE_RESULT.md:64-81; artifacts/runs/run_ff587481a820/attempt1_scoring/METRICS.json:2-50
Issue: The fixture answer-key identities are reachable from committed baseline artifacts.
Reason / Evidence: The committed metrics publish the real fixture id, the seeded-defect total, missed IDs `SD-2`/`SD-4`, and finding-to-key mappings for `SD-1`, `SD-3`, and `SD-5`; `BASELINE_RESULT.md` repeats them and gives the answer-key path. Running the shipped `scan-leak` against the committed baseline result and `run_ff587481a820` reports these key-derived tokens. Post-review scoring separation protected the already-finished Reviewer execution, but it does not satisfy the explicit final-review check that no committed baseline artifact make the answer key reachable.
Required Action: Keep answer-key-bearing scorer output outside committed run evidence or publish a deliberately sanitized aggregate with no fixture id, seeded ids/mappings, expected count, or answer-key path. Add a leak scan over the exact artifact set proposed for commit.

## Non-Blocking Findings

None.

## Test Review

- `python3 scripts/validate_skills.py`: PASS, 463 checks. Direct source inspection confirmed `validate_final_review_contract()` parses the real §17 machine-readable block, compares exact keys and values to `FINAL_REVIEW_CONTRACT`, enforces the 17-line bound, and separately checks §9/§16 audit prose; this is not a vacuous success.
- `python3 -m unittest discover -s scripts -p 'test_*.py'`: PASS, 984 tests, 6 opt-in skips. The suite meaningfully exercises most audit publication, provenance, redaction, neutrality, scoring refusal, and lifecycle/profile compatibility paths, but it misses R1's `/private/tmp` path shape, R2's semantic prompt leak, and R3's exact closed-world metric values.
- `python3 scripts/verify_package.py`: PASS, 107 source files.
- `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py`: PASS, byte-identical.
- `git diff --check 1045815..HEAD`: PASS.
- Neutrality verification is byte-strict: `canonicalize_task_spec()` preserves reviewer-visible bytes except for the declared temporary workspace placeholder, and the mutation test rejects whitespace-only changes. The fixture records `captured_from_commit: 1045815`; no semantic/whitespace normalizer is used for the equality assertion.
- The ordinary incomplete-adjudication path genuinely refuses both precision and false-positive-rate calculation (`null`, `REFUSED`) and `--require-precision` exits nonzero. R3 is confined to the separately authorized closed-world computation path.

## Evidence Checked

- Read the verbatim OS-22 request from `task_c862feea878c.spec` through the live Orca task list.
- Confirmed HEAD `5642e5b` on `agent/final-review-observability-evaluation` and inspected `git diff 1045815..HEAD`, changed-file status, core implementation/docs/tests, fixture trees, answer key, phase artifacts, reviews, baseline report, and retained audit/export artifacts.
- Recomputed SHA-256 for the real baseline `input.md` and `report.md`; both match their recorded post-redaction digests. Pre/post digests, policy versions, sizes, and metadata fields are structurally present, but the policy's path coverage is insufficient as described in R1.
- Inspected the materializer: it copies only `subject/head` plus generated `DIFF.patch`/`MANIFEST.json`, rejects `key`, `adjudications`, and `.git` path components, and runs the leak scanner without an exemption. The materialized workspace itself does not expose the answer key; R2 is introduced by the dispatched baseline Task input and R4 by the committed post-score artifact set.
- Confirmed attempt provenance is explicit and immutable publication uses a staged directory plus one atomic rename; malformed/unknown records fail closed rather than defaulting to accepted.
- Confirmed `VERSION` and `LICENSE-DECISION.md` have no diff. Existing Risk, Quality Profile, Agent Profile, phase lifecycle, correction/downstream revalidation, and fresh-final-review semantics remain covered by unchanged/additive regression assertions; no production search/falsification/model-optimization policy was added, apart from the baseline prompt defect in R2.

## Final Decision

FAIL. OS-22 cannot complete while retained evidence leaks a local path, the sole required baseline is search-hinted rather than neutral, one permitted metric path computes an incorrect false-positive rate, and committed baseline outputs expose answer-key identities. Route R1 to IMPLEMENTATION, R3 to DESIGN, and R2/R4 to TEST, then repeat the required correction gates, downstream validation, and a fresh Final Adversarial Review.
