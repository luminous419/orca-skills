# Worker Result

STATUS: COMPLETE

Phase: TEST · Iteration 1 · Task `task_cfb92198ced6` · Dispatch `ctx_22ff34804e8a`
Branch: `agent/final-review-observability-evaluation` (verified with `git branch --show-current`)
Baseline read: `d614c89` (IMPLEMENTATION's last commit) · TEST commit: `f3d5792`

## Test Scope / Existing Test Assessment

This phase verifies that IMPLEMENTATION's 965 tests actually discharge the ticket's §9
`Required Tests` when reorganized onto PLAN's six groups — it does not re-derive the case list
and does not re-test what is already covered. I read PLAN's `## Work Items` TEST table
(`PLAN.md:566-575`) case by case, located the test that discharges each case in the real test
files, and only wrote a test where a case was genuinely uncovered or where the existing coverage
stopped short of the case as PLAN states it.

Result: **5 of PLAN's 6 groups were already fully covered; 5 cases across T-1/T-2/T-5/T-6 were
covered only partially and now have tests.** No case was found completely untested. One
non-blocking defect was found in the scorer while testing and is reported below rather than fixed.

### T-1 — Audit / provenance

| PLAN case | status | evidence |
|---|---|---|
| per-dispatch input artifact created | **covered** | `test_run_logging.AuditRecordWriteTests.test_the_published_unit_is_a_directory_holding_exactly_three_files` (`input.md` is one of the three); `AuditCaptureFailureTests.test_a_captured_spec_is_redacted_before_it_reaches_disk` |
| per-dispatch report artifact created | **covered** | same test (`report.md`); `AuditRecordWriteTests.test_the_report_snapshot_is_parsed_verbatim`; `AuditRecordWriteTests.test_report_resolution_records_which_path_rule_applied` |
| retry/correction produces a *new* record; writer refuses to overwrite | **covered** | `AuditRecordWriteTests.test_the_writer_refuses_to_overwrite_a_published_record`; `..test_a_retry_under_a_new_identity_produces_a_separate_record`; `test_e2e_harness.DeterministicFinalReviewAuditTests.test_a_second_attempt_never_overwrites_the_first_record` |
| `accepted` provenance path | **covered** | `AuditProvenanceTests.test_one_accepted_dispatch_is_returned`; `test_orca_runtime_contract.FinalReviewAuditEmissionTests.test_a_final_review_dispatch_writes_one_record` |
| each `void_reason` path | **covered** | `AuditProvenanceTests.test_every_void_reason_round_trips` (all six of `VOID_REASONS`); `ProvenanceLadderTests.test_every_row_of_the_ladder`; `..test_every_void_reason_the_ladder_emits_is_in_the_enum` |
| a voided report is never returned as an accepted verdict | **covered** | `AuditProvenanceTests.test_a_voided_record_is_never_returned_as_a_verdict`; `AuditReaderCompatibilityTests.test_an_unreadable_record_can_never_be_the_accepted_one`; `AuditCliTests.test_no_cli_surface_can_ask_for_accepted_by_default` |
| log ↔ input ↔ report identity join on `task_id`/`dispatch_id` | **PARTIAL → test added** | the existing `FinalReviewAuditEmissionTests.test_the_record_is_joined_to_the_log_on_the_existing_columns` joins the log row to `record.json` and stops there — the two retained artifacts are never re-read off disk, so a record naming an `input.md`/`report.md` it did not actually publish still passes it. Closed by `test_os22_required_tests.LogInputReportIdentityJoinTests` (4 tests) |

### T-2 — Failure handling

| PLAN case | status | evidence |
|---|---|---|
| a dispatch-input failure preserves the pre-failure input evidence | **covered** | `AuditRecordWriteTests.test_a_retry_under_a_new_identity_produces_a_separate_record`; `AuditCaptureFailureTests.test_an_unavailable_capture_still_writes_the_record` (record written with null digests + non-empty `capture_error` on all five capture-failure shapes) |
| a retry is recorded under a **separate** task/dispatch identity, not merged | **covered** | `..test_a_retry_under_a_new_identity_produces_a_separate_record`; `FinalReviewAuditEmissionTests.test_two_attempts_produce_two_records_under_separate_identities` |
| the failure record satisfies §3 while leaving the §7 baseline unsatisfied | **PARTIAL → test added** | `AuditProvenanceTests` proves each half separately (`test_a_voided_record_is_never_returned_as_a_verdict`, `test_an_attempt_with_no_accepted_dispatch_produced_no_verdict`) but never together, and "evidence retained **and** baseline still open" is one state, not two. Closed by `test_os22_required_tests.FailureEvidenceWithoutBaselineTests` (3 tests) |
| a malformed/incomplete record reads `unknown`, never `accepted` | **covered** | `AuditReaderCompatibilityTests` (6 tests: missing → `missing`, unparseable → `malformed`, missing required field → `malformed`, unknown major refused, higher minor tolerated); `AuditProvenanceTests.test_the_default_provenance_is_unknown_and_never_accepted` |
| `observed_input_bytes` + `failure_detail` recorded | **covered** | `AuditRecordWriteTests.test_a_retry_under_a_new_identity_produces_a_separate_record`; `AuditCaptureFailureTests.test_an_unavailable_capture_still_writes_the_record` |
| guard: no `14805`/`5553`/`2269`/`14.8`/`5.5`/`2.3` as a threshold constant | **PARTIAL → test added** | `RetainedArtifactSecurityTests.test_the_implementation_hard_codes_no_observed_input_size` guards the OS-22 section of `scripts/run_logging.py` **only** — not the installed twin, not either emission site, not the scorer, all of which are equally "the implementation". Closed by `test_os22_required_tests.ObservedSizeThresholdGuardTests` (4 tests) |

### T-3 — Security

| PLAN case | status | evidence |
|---|---|---|
| redaction is deterministic (same input twice → identical bytes and digests) | **covered** | `RedactionPolicyTests.test_redaction_is_deterministic` |
| `artifact_digest_post_redaction` re-hashes the file on disk | **covered** | `RetainedArtifactSecurityTests.test_the_retained_artifact_carries_the_redacted_text_and_nothing_else` — hashes `read_bytes()` of the published file, not an in-memory string. Re-asserted for **both** artifacts by the new `LogInputReportIdentityJoinTests.test_both_retained_artifacts_rehash_to_the_digests_the_record_states` |
| a synthetic `dcap_…` and a `/Users/<name>/…` path do not survive into the retained artifact | **covered** | `RetainedArtifactSecurityTests.test_no_secret_survives_into_the_retained_report`; `RecordMetadataRedactionTests.test_no_credential_and_no_home_path_survives_into_record_json` and `..test_every_injection_route_is_redacted_field_by_field` (I-002/I-002-R1) |
| `redactions` carries no redacted value | **covered** | `RedactionPolicyTests.test_the_counts_carry_no_redacted_value_and_no_offset`; `..test_nothing_matched_reads_as_an_empty_list` |
| pre/post identity re-derivable by re-running the pipeline on the same source | **covered** | `RetainedArtifactSecurityTests.test_the_pre_and_post_identity_is_rederivable`; `..test_the_four_identity_fields_are_all_present` |

T-3 needed no additions. Its one weak spot — over-redaction — is itself covered
(`RecordMetadataRedactionTests.test_the_identities_the_record_exists_to_prove_are_not_redacted`,
`..test_a_well_formed_report_keeps_its_ids_and_its_enums`).

### T-4 — Evaluation (the cases checkable without a live Reviewer dispatch)

Per §9, this section **shows** the fixture and key rather than counting tests.

| PLAN case | status | evidence |
|---|---|---|
| each intended seeded defect actually exists in `subject/` — **demonstrated, not asserted** | **covered** | `FixtureIntegrityTests.test_each_entry_is_demonstrated_by_running_the_head_tree` executes `head/` in a scratch tree and observes each behaviour (below). Ran live: `verify-fixture` → `fixture verification PASSED` (exit 0) |
| answer-key correctness | **covered** | `FixtureIntegrityTests.test_every_key_entry_names_a_real_symbol_in_a_changed_range` (each entry's symbol is a real `def` inside the stated range **and** that range is in the base→head diff); `..test_both_subject_suites_pass` (a green head suite, so no test localizes an entry for free) |
| `subject/` contains no key token or key path (`scan-leak`, no exclusions) | **covered** | `LeakScanTests` (5 tests, incl. three positive controls proving the scanner fires); `MaterializeTests.test_the_scanner_takes_no_exclusion_argument` (I-001). Ran live: `scan-leak --target …/subject` → `leak scan PASSED` (exit 0) |
| the retained reviewer input carries no key token and no expected-count statement | **covered** | `MaterializeTests.test_the_key_and_the_adjudications_never_reach_the_workspace`; `..test_the_workspace_the_reviewer_reads_is_clean`; `..test_the_manifest_names_the_fixture_opaquely`; `NoTargetCountTests` (2 tests). Ran live against a real materialized workspace — see `## Execution` |
| recall computed with an explicit denominator | **covered** | `MatchingTests.test_a_missed_entry_is_reported_with_an_explicit_denominator`. Ran live: `seeded_recall = {"value": 0.2, "numerator": 1, "denominator": 5, "population": "seeded_defects_only"}` |
| an unmatched finding is `UNADJUDICATED`, never auto-FP | **covered** | `MatchingTests.test_an_unmatched_finding_is_unadjudicated_and_never_an_auto_false_positive`; `..test_a_finding_with_no_key_match_is_labelled_no_key_match`. Ran live: `{"finding_id": "F2", "reason": "no_key_match", "classification": "UNADJUDICATED"}` |
| precision **refused** with non-zero exit under insufficient adjudication | **covered** | `PrecisionRefusalTests` (7 tests, incl. partial adjudication still refusing and recall still computable); `ExitCodeTests.test_three_when_precision_is_refused_and_required`. Ran live: exit **3**, `precision = null`, `false_positive_rate = null` |
| the §7 baseline is PASS only over a settled, scored report | **out of scope here (procedural, DEC-9/B-5)** | the scorer half is enforced — `ExitCodeTests.test_one_for_a_missing_input` refuses to score an absent report. The recording rule itself belongs to the Coordinator's B-1…B-5, deferred (see `## Remaining Gaps`) |

**The five seeded defects, as the key states them** (`scripts/fixtures/final_review_eval/key/answer_key.json`, `schema_version` 1.0, `expected_finding_count_is_not_a_contract: true`):

| id | archetype | location | contract | what is wrong |
|---|---|---|---|---|
| SD-1 | `value_vs_presence` | `src/policy.py::resolve_tier` 6-10 | CONTRACT.md 2 | tests for the *presence* of `retention_tier` instead of validating its *value* against `TIERS`, so an unknown tier is accepted and its limit vanishes |
| SD-2 | `omitted_call_site_propagation` | `src/pipeline.py::publish_batch` 22-28 | CONTRACT.md 4 | `publish_batch` resolves the destination tier but leaves its `enforce_quota` call at the pre-feature form, so the tier never propagates |
| SD-3 | `equality_boundary` | `src/quota.py::enforce_quota` 10-19 | CONTRACT.md 3 | strict `<` where the contract permits equality, so a store sitting exactly on its limit is refused |
| SD-4 | `losing_precedence_fallback` | `src/config.py::resolve_settings` 16-18 | CONTRACT.md 1 | a dict-splat literal whose later keys win, so textual source order is the exact inverse of precedence order |
| SD-5 | `validation_scope_gap` | `src/pipeline.py::republish` 31-36 | CONTRACT.md 5 | the retry path reaches `_write_record` without `validate_record`, so it writes a record no validator saw |

**Demonstrated, not asserted** — the observations `test_each_entry_is_demonstrated_by_running_the_head_tree` makes by executing `head/`: `resolve_tier({'retention_tier': 'typo'}, {}) == 'typo'` and `tier_limits('typo')['max_items'] is None` and a 9999-item store passing (SD-1); the batch path not using the destination tier (SD-2); `enforce_quota([{}]*100, {})` returning False at exactly the limit (SD-3); `resolve_settings({'max_items': 7}, {}, {})['max_items'] == 100`, i.e. the explicit override never taking effect (SD-4); `republish` writing one record with no validation (SD-5).

Every entry also carries a `negative_space_argument` (R-5): SD-4's, for example, is that all four precedence sources appear in the textually correct order, so the defect is invisible to anyone reading source order rather than dict-splat semantics — none of the five is findable by string search.

### T-5 — Regression

| PLAN case | status | evidence |
|---|---|---|
| full `unittest discover` green | **covered** | see `## Execution` — 984 tests, OK |
| `validate_skills.py` green | **covered** | 463 checks PASSED |
| `verify_package.py` green | **covered** | 107 source files |
| existing lifecycle / Risk / Quality Profile / Agent Profile tests untouched and passing | **covered** | no pre-existing test file was edited this phase (`git show --stat f3d5792` = one added file); `RiskLoggingTests`, `test_risk_policy.py`, `test_quality_profile.py`, `test_agent_profile.py`, `test_workflow_contract.py` all green inside the full run |
| no workflow path runs `git add` (DEC-6, `PLAN.md:664`) | **PARTIAL → test added** | `EvidenceBundleTests.test_this_module_runs_no_write_side_git_command` covers `run_logging.py` alone, and it inspects argv literals only — `run_logging.py` shells out as `["git", *args]` through a `_git()` wrapper, so the subcommand is not in the literal at all. Closed by `test_os22_required_tests.NoWriteSideGitOnAnyWorkflowPathTests` (5 tests) over five workflow modules + the installed twin, through argv literals, `_git()` call sites **and** the shelled-out string form |

### T-6 — Neutrality (DEC-1)

| PLAN case | status | evidence |
|---|---|---|
| `FinalReviewObservabilityNeutralityTests` byte-identity across all captured workflows | **covered** | the class exists at `scripts/test_e2e_harness.py:4639` with **12** tests; `test_every_workflow_spec_is_byte_identical_to_the_pre_os22_capture` and `test_every_direct_spec_is_byte_identical_to_the_pre_os22_capture` compare against `scripts/fixtures/os22_neutrality/pre_os22_task_specs.json` (350 003 bytes, committed in `e168344`, the branch's first commit) |
| …**including a `final_review` spec** | **covered** | `test_the_golden_carries_a_final_review_spec`; `test_the_golden_covers_both_skills_all_workflows_and_both_profiles` |
| the golden is strict, not a normalizer in disguise | **covered** | `test_a_whitespace_only_change_fails_the_neutrality_golden` (shows `_normalize_artifact()` would have accepted three of the mutations the real helper rejects) |
| `render_task_spec()` gained no parameter | **covered** | `test_render_task_spec_gained_no_parameter` — verified live, passes |
| the redaction/audit module is not reachable from spec-assembly→dispatch | **PARTIAL → test added** | `test_the_audit_module_is_not_reachable_from_the_dispatch_path` is a genuine non-invocation tripwire, but it drives `e2e_harness` only. The **orca runtime** is a separate module with its own spec assembly and its own dispatch call, and it is the path a live Final Review actually takes. Closed by `test_os22_required_tests.OrcaRuntimeDispatchPathNeutralityTests` (3 tests) |
| `LegacyByteIdentityTests` / `pre_os4_artifacts.json` untouched | **covered** | `test_the_os4_legacy_evidence_is_untouched`; `git show --stat f3d5792` touches neither |

**Call graph, inspected rather than assumed** (the Task asked for this explicitly). In
`scripts/orca_runtime_harness.py`, `write_final_review_audit_record` has exactly **one** enclosing
function: `_log_final_review_audit` (`:2202`), which is called from the settlement path at
`:2200` — *after* four-axis finalization, guarded by `if round_kind == "final_review"`. The
spec-assembly function `dispatch_context()` (`:446`), which calls `render_task_spec` at `:582`, names no audit surface at all. Both
facts are now asserted by `test_the_runtime_module_reaches_the_writer_from_settlement_only` and
`test_the_spec_builder_never_names_an_audit_surface`.

## Added / Modified Tests

One new file. **No existing test was edited, weakened, skipped or deleted**, and no production
file was touched.

| file | classes | tests | group |
|---|---|---|---|
| `scripts/test_os22_required_tests.py` (new) | `LogInputReportIdentityJoinTests` (4), `FailureEvidenceWithoutBaselineTests` (3), `ObservedSizeThresholdGuardTests` (4), `NoWriteSideGitOnAnyWorkflowPathTests` (5), `OrcaRuntimeDispatchPathNeutralityTests` (3) | **19** | T-1, T-2, T-5, T-6 |

Committed as `f3d5792` on `agent/final-review-observability-evaluation`. No `git push`.

## Behavior Covered

* **T-1 / file-level join.** From the `ORCHESTRATOR_LOG.md` row's own `task_id`/`dispatch_id`
  columns, the dispatch key is re-derived and must name the published directory; `input.md` and
  `report.md` must exist there; each must re-hash to the `artifact_digest_post_redaction` and
  `byte_length_post_redaction` its section states; each section's `artifact_path` must name the
  file it hashed. Two dispatches in one attempt must join to their own row and their own bytes —
  asserted through differing `input_digest_pre_redaction` values, so a writer that reused one
  input for both fails.
* **T-2 / evidence-and-open-baseline as one state.** A `dispatch_input_rejected` record must
  simultaneously (a) carry captured pre-failure input evidence, a non-empty `input.md`, the
  correct `input_digest_pre_redaction`, `failure_detail` and `observed_input_bytes`, and
  (b) leave `read_final_review_attempt_provenance()` with `accepted_dispatch_key is None` and a
  `no_accepted_dispatch` violation. A later dispatch under a new identity that settles with a
  usable report is what — and the only thing that — opens it, and the failed record's bytes must
  be unchanged afterwards. Settling alone is not the bar: `report_missing` must not become an
  accepted verdict merely because the dispatch came back.
* **T-2 / widened threshold guard.** The six forbidden values are absent from the OS-22 section of
  **both** `run_logging.py` copies, from the bodies of both emission methods
  (`_log_final_review_audit`, `_write_final_review_audit`, extracted by AST so a rename fails the
  test), and from the scorer's numeric literals (checked as numbers, not substrings, so a digest
  that happens to contain the digits does not fire a false alarm). Plus: `observed_input_bytes`
  is never an operand of any comparison, which is what would make it a threshold whatever number
  sat on the other side.
* **T-5 / no write-side git.** Five workflow modules plus the installed twin, read three ways —
  `["git", …]` argv literals, the arguments passed to any local wrapper that builds one, and the
  single-string `"git add …"` form that would slip past an AST walk. Docstrings are excluded
  (the prose that promises the rule also contains the words). Non-vacuity is asserted: the reader
  must actually find `rev-parse` in `run_logging.py`, and every git argument either copy passes
  must be in the read-only set.
* **T-6 / orca-runtime non-invocation.** `redact_text`, `capture_stored_task_spec`,
  `capture_delivery_evidence` and `write_final_review_audit_record` are patched to raise on **any**
  call; a real `final_review` dispatch is then driven through `OrcaRuntimeHarness.run_attempt()`
  with the settlement-path emission suppressed. The dispatch must settle `succeeded`, a Task spec
  must actually have been assembled (`recorder.specs` non-empty, or nothing was proved), and
  `harness._logging_errors` must be empty.

**Falsification of the new tests** (a passing test that cannot fail proves nothing):

| new assertion | falsified by | result |
|---|---|---|
| the T-6 tripwires are armed and the audit surface is genuinely reachable | re-running the same tripwires with the settlement-path suppression **removed** | trips: `_logging_errors == ["…: tripped:write_final_review_audit_record"]` — so the tripwire fires when the writer is called, and the passing test means it was not called before dispatch |
| `input.md` re-hashes to the recorded digest | overwriting `input.md` with `tampered\n` after publication | detected |
| the log row names the right directory | rewriting `ctx_bbb` → `ctx_zzz` in `ORCHESTRATOR_LOG.md` | detected (and the parser really found the row: 1 row, `final_review_audit_written task_aaa ctx_zzz`) |
| the git guard catches a write-side subcommand | a synthetic `_git("add", "artifacts/")`, a synthetic `subprocess.run(["git","add","x"])`, and a synthetic `"git add x"` string | all three detected |

## Execution

```text
Command: python3 scripts/validate_skills.py
Result:  PASS — "Skill validation PASSED (463 checks)"
                "Validated both skills, shared templates/reviews, routing, and policy gates."

Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  PASS — "Ran 984 tests in 62.203s / OK (skipped=6)"
                (965 pre-existing + 19 added. The 6 skips are test_orca_runtime.py's opt-in
                 live-runtime tests — "requires --orca-runtime and a ready Orca runtime" —
                 pre-existing and unrelated to OS-22.)

Command: python3 scripts/verify_package.py
Result:  PASS — "Package verification PASSED (107 source files)"
                (106 before; +1 is the added test file, which release_manifest.py packages
                 like every other scripts/test_*.py)

Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result:  PASS — no output, exit 0 (byte-identical; no run_logging.py copy was touched this phase)
```

Per-group runs:

```text
Command: python3 -m unittest scripts.test_os22_required_tests
Result:  PASS — Ran 19 tests, OK                     (the additions: T-1, T-2, T-5, T-6)

Command: python3 -m unittest scripts.test_run_logging.FinalReviewDispatchKeyTests \
           scripts.test_run_logging.AuditRecordWriteTests \
           scripts.test_run_logging.AuditProvenanceTests \
           scripts.test_run_logging.AuditReaderCompatibilityTests \
           scripts.test_run_logging.AuditWriteBoundaryFaultTests \
           scripts.test_run_logging.RedactionPolicyTests \
           scripts.test_run_logging.RetainedArtifactSecurityTests \
           scripts.test_run_logging.RecordMetadataRedactionTests \
           scripts.test_run_logging.AuditCaptureFailureTests \
           scripts.test_run_logging.EvidenceBundleTests \
           scripts.test_run_logging.AuditCliTests \
           scripts.test_run_logging.ProvenanceLadderTests
Result:  PASS — Ran 81 tests, OK                     (T-1, T-2, T-3)

Command: python3 -m unittest scripts.test_orca_runtime_contract.FinalReviewAuditEmissionTests \
           scripts.test_e2e_harness.DeterministicFinalReviewAuditTests
Result:  PASS — Ran 12 tests, OK                     (T-1, T-2 emission)

Command: python3 -m unittest scripts.test_final_review_eval
Result:  PASS — Ran 56 tests, OK                     (T-4)

Command: python3 -m unittest scripts.test_e2e_harness.FinalReviewObservabilityNeutralityTests
Result:  PASS — Ran 12 tests, OK                     (T-6)
```

T-4 evidence run live against the shipped fixture, not only through the suite:

```text
Command: python3 scripts/final_review_eval.py verify-fixture \
           --key scripts/fixtures/final_review_eval/key/answer_key.json
Result:  "fixture verification PASSED"  exit 0

Command: python3 scripts/final_review_eval.py scan-leak \
           --key scripts/fixtures/final_review_eval/key/answer_key.json \
           --target scripts/fixtures/final_review_eval/subject
Result:  "leak scan PASSED"  exit 0        (no --exclude flag exists; the scanner takes none)

Command: python3 scripts/final_review_eval.py materialize --dest <scratch>/ws
Result:  exit 0 — {"fixture_digest": "sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d",
                   "files": 14}
         Workspace holds DIFF.patch, MANIFEST.json, CONTRACT.md, src/ (6), tests/ (5).
         `find <ws> -name .git | wc -l` -> 0.  No key/ and no adjudications/ directory.

Command: python3 scripts/final_review_eval.py scan-leak \
           --key .../answer_key.json --target <scratch>/ws
Result:  "leak scan PASSED"  exit 0        (the workspace a Reviewer would actually read)

Command: python3 scripts/final_review_eval.py parse-report --report <synthetic FINAL_REVIEW.md> \
           --workspace <ws> --out <ws>/findings.json
Result:  exit 0

Command: python3 scripts/final_review_eval.py score --findings <findings.json> \
           --key .../answer_key.json --workspace <ws> --require-precision --out <metrics.json>
Result:  exit 3 (EXIT_PRECISION_REFUSED), stderr:
         "precision refused: adjudication_incomplete: 1 unmatched finding(s) carry no
          independent adjudication verdict, and no closed_world exhaustive attestation is present"
         metrics.json:
           seeded_recall = {"value": 0.2, "numerator": 1, "denominator": 5,
                            "population": "seeded_defects_only"}
           precision            = null
           false_positive_rate  = null
           precision_status     = "REFUSED"
           unmatched_findings   = [{"finding_id": "F2", "reason": "no_key_match",
                                    "classification": "UNADJUDICATED"}]
```

That last run exercises four §9 Evaluation requirements at once against real bytes: an explicit
recall denominator, `UNADJUDICATED` as the default for an unmatched finding, no auto-false-positive,
and a non-zero exit on refused precision.

## Failures / Findings

No test failed. One defect was found **while** testing; per this phase's Mandatory Invariant it is
reported, not fixed.

### T-001 — `final_review_eval.py score` does not validate the shape of a findings document, so a malformed one escapes the documented exit-code ladder with a traceback

**Severity:** MINOR · **Blocking:** NO · **Responsible phase:** IMPLEMENTATION (I-12) /
DESIGN (D-E) · **Location:** `scripts/final_review_eval.py:748` (`match_findings`), reached from
`:805` (`score`) via `:1123`.

**Issue.** `_dispatch` validates the findings document's *schema major* (`require_major`,
`:1123`) and `score()` checks for duplicate ids (`:810`), but nothing validates that each entry
has the fields the scorer then indexes directly. `score()` does `finding["id"]` and
`match_findings()` does `finding["location_file"]`, both unguarded.

**Failure scenario, reproduced.**

```text
$ cat bad_findings.json
{"schema_version": "1.0", "findings": [{"id": "F1", "claim": "x"}]}
$ python3 scripts/final_review_eval.py score --findings bad_findings.json --key .../answer_key.json
  File ".../final_review_eval.py", line 748, in match_findings
    elif finding["location_file"] is None:
KeyError: 'location_file'

$ cat bad2.json
{"schema_version": "1.0", "findings": ["not-a-dict"]}
$ python3 scripts/final_review_eval.py score --findings bad2.json --key .../answer_key.json
    identifiers = [finding["id"] for finding in findings]
TypeError: string indices must be integers, not 'str'
```

**Reason it matters.** `main()` catches `EvalInputError` / `EvalContractError` / `FixtureError`
and maps them onto the exit ladder `ExitCodeTests` documents (1 / 2 / 4). A `KeyError` or
`TypeError` is caught by none of them, so this input leaves the ladder entirely and surfaces as a
Python traceback. It is also asymmetric: `load_adjudications()` (`:759`) *does* enforce a hard
contract — unknown top-level key, third verdict value, empty rationale, duplicate id are all
`EvalContractError` — and `AdjudicationContractTests` proves it. Findings get the version check
and nothing else.

**Why it is not blocking.** The intended producer is `parse-report`, which always emits every
field `score` reads (`:600-620`), so the supported pipeline never hits this. The failure is loud,
not silent — no wrong metric is ever produced — and the exit code still happens to be non-zero.
It fails none of G1-G5.

**Suggested fix (for a later phase, not applied here).** A `load_findings()` alongside
`load_adjudications()` that raises `EvalContractError` for a non-dict entry, a missing `id`, or a
missing `location_file`/`location_line`, called from `:1123` in place of the bare `require_major`.
That maps the case onto exit 2 and makes the two input contracts symmetric.

### Observations (not defects, recorded so a reviewer need not re-derive them)

* `verify_package.py`'s file count moved 106 → 107. That is the added test file;
  `release_manifest.py` packages every `scripts/test_*.py`, and the manifest needed no edit.
* The T-2 threshold guard's original scope was narrower than PLAN's wording ("**the
  implementation**"), but the numbers were in fact absent from every widened surface — the gap was
  in the *guard*, not in the code. Nothing had to change.

## Remaining Gaps

1. **The live baseline dispatch (§7 / PLAN B-1…B-5) is deferred to the Coordinator as a separate
   post-phase activity — it is not a gap in this Task's work.** The Task explicitly places it out
   of scope, PLAN's ordering constraint 6 requires T-1 and T-3 to pass first (they do), and every
   part of §7 that is checkable without a live Reviewer session has been checked here: the fixture
   materializes clean, the leak scan is empty, the scorer refuses precision with a non-zero exit,
   and `ExitCodeTests.test_one_for_a_missing_input` proves the scorer will not run on an absent
   report. What remains is B-2/B-3/B-3R/B-4/B-5 against a real Reviewer dispatch, plus DEC-9's
   recording rule that a captured dispatch-layer failure is a §7 **FAIL**, never a pass.
2. **T-4's `baseline execution 성공` case has no automated test and cannot have one here.** It is a
   recording rule over a live run, discharged by B-5, not by the scorer. Noted rather than faked.
3. **T-001 is open by design** — reported above, not fixed, because TEST verifies and does not
   implement. It needs an IMPLEMENTATION-phase correction or an explicit accept-as-is.
4. **The `agent_prompt_blocked` delivery limit remains a stated limit, not a closed gap.** DEC-1
   already carries it: what is retained is the *stored* Task spec, and the record says so in
   `is_stored_spec_not_delivered_bytes`. No test can close it, because Orca exposes no read-back
   of the bytes a terminal received. Recorded so it is not mistaken for an oversight.
5. **No H-1/H-2/H-4/H-5 conclusion is drawn, and none is available from this phase.** Nothing here
   measures Final Review detection quality; the fixture and scorer exist so that a later ticket
   can, which is exactly OS-22's scope boundary.
