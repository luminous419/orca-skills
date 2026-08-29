# Worker Result

STATUS: COMPLETE

Phase: TEST · Iteration 1 · Task `task_cfb92198ced6` · Dispatch `ctx_22ff34804e8a`
Correction round: TEST · Iteration 2 · Task `task_236e21abdf42` · Dispatch `ctx_2952d62c5194` — see [Iteration 2](#iteration-2--r2--r4-correction-7-baseline-replacement) at the end of this document
Correction round: TEST · Iteration 3 · Task `task_51cfdb499ed2` · Dispatch `ctx_3efd5e30255f` — see [Iteration 3](#iteration-3--r4-t2-correction-arithmetic-disclosure-of-the-key-population) at the end of this document
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
| a synthetic `dcap_…` and a `/Users/<name>/…` path do not survive into the retained artifact *(**widened by iteration 4** — `redaction/1.1` adds category 5, so the case now covers EVERY absolute root, with no segment-count floor. See `## TEST iteration 4`.)* | **covered** | `RetainedArtifactSecurityTests.test_no_secret_survives_into_the_retained_report`; `RecordMetadataRedactionTests.test_no_credential_and_no_home_path_survives_into_record_json` and `..test_every_injection_route_is_redacted_field_by_field` (I-002/I-002-R1) |
| `redactions` carries no redacted value | **covered** | `RedactionPolicyTests.test_the_counts_carry_no_redacted_value_and_no_offset`; `..test_nothing_matched_reads_as_an_empty_list` |
| pre/post identity re-derivable by re-running the pipeline on the same source | **covered** | `RetainedArtifactSecurityTests.test_the_pre_and_post_identity_is_rederivable`; `..test_the_four_identity_fields_are_all_present` |

T-3 needed no additions. Its one weak spot — over-redaction — is itself covered
(`RecordMetadataRedactionTests.test_the_identities_the_record_exists_to_prove_are_not_redacted`,
`..test_a_well_formed_report_keeps_its_ids_and_its_enums`).

### T-4 — Evaluation (the cases checkable without a live Reviewer dispatch)

Per §9, this section **shows** the fixture and key rather than counting tests.

| PLAN case | status | evidence |
|---|---|---|
| each intended planted defect actually exists in `subject/` — **demonstrated, not asserted** | **covered** | `FixtureIntegrityTests.test_each_entry_is_demonstrated_by_running_the_head_tree` executes `head/` in a scratch tree and observes each behaviour (below). Ran live: `verify-fixture` → `fixture verification PASSED` (exit 0) |
| answer-key correctness | **covered** | `FixtureIntegrityTests.test_every_key_entry_names_a_real_symbol_in_a_changed_range` (each entry's symbol is a real `def` inside the stated range **and** that range is in the base→head diff); `..test_both_subject_suites_pass` (a green head suite, so no test localizes an entry for free) |
| `subject/` contains no key token or key path (`scan-leak`, no exclusions) | **covered** | `LeakScanTests` (5 tests, incl. three positive controls proving the scanner fires); `MaterializeTests.test_the_scanner_takes_no_exclusion_argument` (I-001). Ran live: `scan-leak --target …/subject` → `leak scan PASSED` (exit 0) |
| the retained reviewer input carries no key token and no expected-count statement | **covered** | `MaterializeTests.test_the_key_and_the_adjudications_never_reach_the_workspace`; `..test_the_workspace_the_reviewer_reads_is_clean`; `..test_the_manifest_names_the_fixture_opaquely`; `NoTargetCountTests` (2 tests). Ran live against a real materialized workspace — see `## Execution` |
| recall computed with an explicit denominator | **covered** | `MatchingTests.test_a_missed_entry_is_reported_with_an_explicit_denominator`. Ran live: the recall object came back carrying all four contracted members — `value`, `numerator`, `denominator`, `population` — each present and of the right type, with `denominator` a positive integer and `value == numerator / denominator`. The four values themselves are withheld under P-1 (see the note under `## Execution`); what the case requires is that the denominator is *stated* rather than implied, and that is what was observed |
| an unmatched finding is `UNADJUDICATED`, never auto-FP *(**qualified by iteration 4** — the second half is unchanged; the first is now the DEFAULT, which an explicit signed closed-world attestation may override. See `## TEST iteration 4`.)* | **covered** | `MatchingTests.test_an_unmatched_finding_is_unadjudicated_and_never_an_auto_false_positive`; `..test_a_finding_with_no_key_match_is_labelled_no_key_match`. Ran live: `{"finding_id": "F2", "reason": "no_key_match", "classification": "UNADJUDICATED"}` |
| precision **refused** with non-zero exit under insufficient adjudication | **covered** | `PrecisionRefusalTests` (7 tests, incl. partial adjudication still refusing and recall still computable); `ExitCodeTests.test_three_when_precision_is_refused_and_required`. Ran live: exit **3**, `precision = null`, `false_positive_rate = null` |
| the §7 baseline is PASS only over a settled, scored report | **out of scope here (procedural, DEC-9/B-5)** | the scorer half is enforced — `ExitCodeTests.test_one_for_a_missing_input` refuses to score an absent report. The recording rule itself belongs to the Coordinator's B-1…B-5, deferred (see `## Remaining Gaps`) |

**The fixture's planted defects, described without reproducing the key.** The key is deliberately
not restated here: this document names none of its entry ids, archetype names, file/symbol
locations, governing contract sections, or summaries, and does not give its path. That content is
exactly what a committed run artifact must not make reachable. What was verified about it:

* The key declares a small, fixed number of planted defects — the count lives in the key, not here.
  Every entry carries a distinct archetype drawn from the ticket's §5 category list, a file/symbol
  location, a governing contract section, and a summary.
* Every entry is **demonstrated, not asserted**.
  `FixtureIntegrityTests.test_each_entry_is_demonstrated_by_running_the_head_tree` imports the
  `head/` tree and executes it, observing each entry's stated wrong behavior as a real runtime
  result rather than trusting the key's prose. Every entry passes that check, so no entry is a
  claim the fixture does not actually exhibit.
* Every entry also carries a `negative_space_argument` (R-5) explaining why that defect is not
  findable by string search — for example, why code that reads correctly in textual order can
  still behave incorrectly at runtime. `FixtureIntegrityTests` asserts the field is present and
  non-empty on every entry.
* `verify-fixture` PASSES against the committed subject tree: the subject is what the key says it
  is, by digest.

*(Redaction note — finding R2-T1, see `## Review Feedback Resolution`. This passage previously
reproduced the key in full: every entry id, archetype name, location, contract section and summary,
plus the key's path and one of its field names. It was redacted during the iteration-2 correction
round. No verification claim was dropped; only the identifying content was.)*

### T-5 — Regression

| PLAN case | status | evidence |
|---|---|---|
| full `unittest discover` green | **covered** | see `## Execution` — 984 tests, OK |
| `validate_skills.py` green | **covered** | 463 checks PASSED |
| `verify_package.py` green | **covered** | 107 source files |
| existing lifecycle / Risk / Quality Profile / Agent Profile tests untouched and passing | **covered** | no pre-existing test file was edited this phase (`git show --stat f3d5792` = one added file); `RiskLoggingTests`, `test_risk_policy.py`, `test_quality_profile.py`, `test_agent_profile.py`, `test_workflow_contract.py` all green inside the full run |
| no workflow path runs `git add` (DEC-6, `PLAN.md:664`) | **PARTIAL → test added** | `EvidenceBundleTests.test_this_module_runs_no_write_side_git_command` covers `run_logging.py` alone, and it inspects argv literals only — `run_logging.py` shells out as `["git", *args]` through a `_git()` wrapper, so the subcommand is not in the literal at all. Closed by `test_os22_required_tests.NoWriteSideGitOnAnyWorkflowPathTests` (5 tests) over five workflow modules + the installed twin, through argv literals, `_git()` call sites **and** the shelled-out string form |
| `git diff --check <base>..HEAD` green *(**added by iteration 5** — this command was always one of PLAN/DESIGN's standing required regression commands, but iteration 1's case list did not name it, so no row of this table ever tracked it. DESIGN A.6 (R5) made that omission material. See `## TEST iteration 5`.)* | **covered** | `git diff --check 1045815..HEAD` → exit 0, no output, re-run at `ad22943`. Pinned as a test rather than only as a recorded run by `test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_whitespace_gate_passes_over_the_whole_os22_range` |
| the retained-report whitespace exemption is narrow and bought no digest change (T-5a, A.6) *(**added by iteration 5**)* | **covered** | `test_run_logging.RetainedReportWhitespaceExemptionTests` (7 tests), added by IMPLEMENTATION iteration 5 under `scripts/`, therefore inside the standard `unittest discover -s scripts -p 'test_*.py'` run — confirmed discovered and green there, not only under a targeted invocation. Independently re-derived by this phase: see `## TEST iteration 5` |

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

> **Two elisions apply to the quoted output below.**
>
> 1. *Schema field names* (R2-T1): `recall = …` rather than the tool's exact field name,
>    `"…_defects_only"` rather than the exact population label. The exact schema names are defined
>    in `DESIGN.md` and in `scripts/final_review_eval.py`; they are elided here only so this
>    document holds at zero hits under the reviewer-scope leak scan.
> 2. *Numeric metric values* (R4-T2): every value in
>    `{key population total, detected/matched count, missed count, unmatched count, reviewer
>    finding count, recall}` is withheld, marked `<withheld>`. Publishing any two of them lets a
>    reader solve for the key's population size, so withholding one field is not enough —
>    the rule is **P-1**, stated in `BASELINE_RESULT.md` and applied identically here: committed
>    evidence publishes at most one of those quantities, and only as a coarse bucket. This
>    document publishes none of them.
>
> Structure, which fields are present, exit codes and every non-metric value are quoted verbatim
> and unchanged. See findings R2-T1 and R4-T2 in `## Review Feedback Resolution`.

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
           --key <key>
Result:  "fixture verification PASSED"  exit 0

Command: python3 scripts/final_review_eval.py scan-leak \
           --key <key> \
           --target scripts/fixtures/final_review_eval/subject
Result:  "leak scan PASSED"  exit 0        (no --exclude flag exists; the scanner takes none)

Command: python3 scripts/final_review_eval.py materialize --dest <scratch>/ws
Result:  exit 0 — {"fixture_digest": "sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d",
                   "files": 14}
         Workspace holds DIFF.patch, MANIFEST.json, CONTRACT.md, src/ (6), tests/ (5).
         `find <ws> -name .git | wc -l` -> 0.  No key/ and no adjudications/ directory.

Command: python3 scripts/final_review_eval.py scan-leak \
           --key <key> --target <scratch>/ws
Result:  "leak scan PASSED"  exit 0        (the workspace a Reviewer would actually read)

Command: python3 scripts/final_review_eval.py parse-report --report <synthetic FINAL_REVIEW.md> \
           --workspace <ws> --out <ws>/findings.json
Result:  exit 0

Command: python3 scripts/final_review_eval.py score --findings <findings.json> \
           --key <key> --workspace <ws> --require-precision --out <metrics.json>
Result:  exit 3 (EXIT_PRECISION_REFUSED), stderr:
         "precision refused: adjudication_incomplete: <n> unmatched finding(s) carry no
          independent adjudication verdict, and no closed_world exhaustive attestation is present"
         metrics.json:
           recall = {"value": <withheld>, "numerator": <withheld>,
                     "denominator": <withheld, a positive integer>,
                     "population": "…_defects_only"}
           precision            = null
           false_positive_rate  = null
           precision_status     = "REFUSED"
           unmatched_findings   = [{"finding_id": "F2", "reason": "no_key_match",
                                    "classification": "UNADJUDICATED"}, …]
                                  (list length withheld)
```

That last run exercises four §9 Evaluation requirements at once against real bytes: an explicit
recall denominator (present, integral, and the divisor of the reported value — its magnitude
withheld under P-1), `UNADJUDICATED` as the default for an unmatched finding, no
auto-false-positive, and a non-zero exit on refused precision. Each of those is a *structural*
property, which is why withholding the magnitudes costs the evidence nothing: none of the four
claims depends on what the numbers are.

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
$ python3 scripts/final_review_eval.py score --findings bad_findings.json --key <key>
  File ".../final_review_eval.py", line 748, in match_findings
    elif finding["location_file"] is None:
KeyError: 'location_file'

$ cat bad2.json
{"schema_version": "1.0", "findings": ["not-a-dict"]}
$ python3 scripts/final_review_eval.py score --findings bad2.json --key <key>
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

---

## Iteration 2 — R2 / R4 correction (§7 baseline replacement)

Triggered by the Final Adversarial Review FAIL recorded in
`artifacts/runs/run_804e35d29531/FINAL_REVIEW.md`. Two blocking findings, R2 and R4, were routed to
this phase. Both concern the §7 fixture-based baseline that ran *after* iteration 1 of TEST passed
— not the tests reviewed and approved above. **Nothing in the sections above this line was
touched.** R1 and R3 belong to DESIGN and arrive here later through §17's T5a downstream
revalidation; they are deliberately not addressed in this round.

### What the two findings were, and what was done

**R2 — the retained baseline input was not neutral.** The Reviewer dispatched for the superseded
baseline had been told which classes of defect to weight most heavily and pointed at specific
numbered contract sections as the ones that mattered. That is a search-depth alteration of the very
mechanism the baseline exists to measure unchanged, so the resulting number was not a baseline. The
shipped `scan-leak` could not see it: it compares literal key vocabulary after collapsing
whitespace only, and the hint was spelled with hyphens where the key uses underscores.

Action taken: the fixture was re-materialized into a fresh scratch workspace and **one new Final
Adversarial Review attempt was dispatched in a new Orca Run, `run_92759e0e1034`**, with a Reviewer
input carrying only the ordinary §17/§11 framing — role, Direct Verification duty, the full
undifferentiated A–I search-axis list, the Review Result format, the Finding Contract — plus the
materialized subject and the report path. It names no defect class, weights no contract section
above another, does not disclose that the subject is a fixture or part of an evaluation, and never says
how many findings there are to find. `agent=codex-sol` as before; the attempt settled cleanly, provenance
`accepted`, zero violations. `parse-report` and `score` ran afterwards as a separate step, exactly
as before.

**R4 — key-derived identities were reachable from committed baseline evidence.** The superseded run
committed scorer output that republished the subject's identifier, the entry total, individual entry
ids, finding-to-entry mappings, the missed-id list, and the key's path.

Action taken: scorer output for the replacement run was written to a scratch directory **outside the
repository and is not committed**. What is committed is the audit trail (Task/Dispatch ids,
timestamps, accepted/voided provenance, redacted input and report text, the evidence bundle) plus a
deliberately sanitized top-line summary in the rewritten `BASELINE_RESULT.md`, which names none of
the identities R4 lists. (That summary's *numeric* sanitization was still insufficient and was
corrected again in iteration 3 — see R4-T2 below.)

### Leak validation, extended

R2's required action included extending leak validation beyond literal ids and a handful of phrases.
A new checker was added at
`artifacts/runs/run_92759e0e1034/tools/semantic_leak_scan.py`. It normalizes `_`, `-`, `/` and
whitespace to a single form before comparing, so every spelling of the same key vocabulary collapses
together, and it additionally flags partial key vocabulary co-occurring inside an eight-word window,
contract-section targeting, expected-defect-count statements, and framing or emphasis that narrows
the search. It reads its vocabulary from the key at runtime and hard-codes none of it, so the
checker is itself safe to commit next to reviewer-visible evidence. Two profiles: `prompt` runs
every check and is what a reviewer-visible input must pass; `evidence` runs only the identity checks
and is what a committed run artifact must pass.

Regression evidence that the checker actually detects R2: run against the superseded attempt's
retained input it reports **11 hits**, including the emphasis phrasing, the contract-section
pointer and the framing on that prompt's very first line. Run against the replacement attempt's input it reports **0**.

The shipped `scan-leak` was left untouched — it is IMPLEMENTATION-owned surface, and this round
changes no production script.

### R2-T1 — a third leak, found by the extended scan

The extended scan was run over the artifact set proposed for commit, as R4 required. It found a
leak that neither R2 nor R4 named, in this very file: the iteration-1 `## Test Scope` section
reproduced the key **in full** — every entry id, every archetype name, each entry's file/symbol and
line range, each entry's governing contract section, a near-verbatim restatement of each summary,
plus the key's path and one of its field names. Under R4's own yardstick that one file produced 25
literal hits and 32 semantic hits, while everything else in the set was already at zero. Reading it
was enough to sit the review.

It also showed that the superseded attempt's Reviewer input opened with a line naming the whole
exercise outright — the first line of `run_ff587481a820/…/input.md` labels the task as a
fixture-based baseline attempt. That is a stronger disclosure than R2 described, and it is what the
extended `fixture_framing` check is for.

The correction instruction told me not to touch anything else already reviewed and approved in this
file, so this was escalated rather than fixed unilaterally, and the Coordinator directed the
redaction. What was done:

* The `## Test Scope` passage was replaced with a non-identifying description that keeps **every**
  verification claim — that each entry is demonstrated by executing the head tree rather than
  asserted, that each carries a distinct archetype from the ticket's §5 category list, that each
  carries a non-empty negative-space argument, and that `verify-fixture` passes by digest — while
  naming none of the identifying content. A redaction note marks the passage.
* Remaining references to the key's path elsewhere in this file were replaced with `<key>`, and two
  quoted metric-output blocks had their schema field names elided (values, structure and exit codes
  quoted verbatim and unchanged). A note at the head of `## Execution` records that elision.
* The superseded run's key-derived scorer output was quarantined — see below.

### The superseded run's evidence: quarantined, not deleted

On the Coordinator's instruction, and without deleting the directory or its audit trail:

| artifact | action |
|---|---|
| `run_ff587481a820/attempt1_scoring/METRICS.json` | replaced with a placeholder recording what it held, why, and the original SHA-256; original moved out of the repository |
| `run_ff587481a820/attempt1_scoring/FINDINGS.json` | same treatment as the paired output of the same scorer step. It carried no key-derived content and is fully reproducible from the retained `report.md` via `parse-report`, so nothing forensic is lost |
| `run_ff587481a820/ORCHESTRATOR_LOG.md` run-end row | detail text replaced; it had named key-derived quantities. Row, timestamp and result untouched |
| `run_ff587481a820/…/record.json` `notes` | replaced with a supersession note; every other field, digest and provenance value untouched |
| `run_ff587481a820/EXPORT_BUNDLE.json` | regenerated from the redacted log and record |

`record.json`, `input.md` and `report.md` are intact. They are the evidence.

### Committed artifact set, and the leak-scan result over it

```
artifacts/runs/run_92759e0e1034/ORCHESTRATOR_LOG.md
artifacts/runs/run_92759e0e1034/TIMING_LOG.md
artifacts/runs/run_92759e0e1034/FINAL_REVIEW_EVIDENCE_BUNDLE.json
artifacts/runs/run_92759e0e1034/final_review_audit/attempt1__task_936f73b5d2eb__ctx_1f82fd26c92b/{input,report,record}.*
artifacts/runs/run_92759e0e1034/tools/semantic_leak_scan.py
artifacts/runs/run_804e35d29531/BASELINE_RESULT.md   (rewritten, sanitized)
artifacts/runs/run_804e35d29531/TEST.md              (this file, redacted per R2-T1)
artifacts/runs/run_ff587481a820/{ORCHESTRATOR_LOG.md, TIMING_LOG.md, EXPORT_BUNDLE.json}
artifacts/runs/run_ff587481a820/attempt1_scoring/{FINDINGS,METRICS}.json  (placeholders)
artifacts/runs/run_ff587481a820/attempt1_scoring/REPORT.md
artifacts/runs/run_ff587481a820/final_review_audit/attempt1__task_0c55cde37456__ctx_33c8c8414587/{input,report,record}.*
```

No scorer output for the replacement run appears in that list; it was written outside the repository.
`.timing_state.json` is git-ignored and is not part of it.

| scan | scope | result |
|---|---|---|
| `scan-leak` (shipped, literal) | materialized workspace, no exclusions | PASSED, 0 hits |
| `semantic_leak_scan --profile prompt` | the replacement attempt's retained Reviewer input | PASSED, 0 hits |
| `scan-leak` + `semantic_leak_scan --profile evidence` | every file above **except** the four retained forensic rows | PASSED, 0 hits, file by file |
| `scan-leak` + `semantic_leak_scan --profile evidence` | the four retained forensic rows (the superseded attempt's `input.md` and `report.md`, `attempt1_scoring/REPORT.md`, and the `EXPORT_BUNDLE.json` that embeds the input) | hits by design — see below |

*(That sweep used the two scanners as they stood in iteration 2. It was insufficient — both
returned zero on files that still disclosed the key population by arithmetic. The sweep was redone
in iteration 3 with a third check; see `## Iteration 3` for the current result.)*

Everything this correction produces is at zero hits under both scanners. The four retained rows are
the superseded attempt's own forensic evidence, kept with explicit Coordinator authorization: a scan
cannot come back clean over the artifact whose evidentiary purpose is to contain the defect. After
quarantine, what they still carry is archetype *vocabulary* — already published in the `ARCHETYPES`
tuple in `scripts/final_review_eval.py` — and the fact that four of those categories were pointed at
during that attempt. They carry no entry id, no finding-to-entry mapping, no total, no missed-entry
list and no key path.

### Result of the replacement baseline

Unchanged in form from the superseded attempt: `RESULT: FAIL` / `REVIEW_VERDICT: FAIL` from the
Reviewer, a non-empty set of blocking findings, perfect evidence grounding, precision and
false-positive rate `REFUSED` because unmatched findings carry no independent adjudication,
reproducibility `SINGLE_RUN_NOT_ASSERTED`. All five DEC-9 baseline criteria PASS. Counts and the
exact recall are withheld under P-1 (see R4-T2). The full sanitized write-up is
`artifacts/runs/run_804e35d29531/BASELINE_RESULT.md`.

Worth recording plainly: the neutral input landed in the **same** recall bucket as the hinted one,
and the underlying exact value did not move at all. That does not retire R2 — a measurement taken
through an altered mechanism is not a baseline whatever number it lands on, and one paired attempt
cannot show the hints were inert — but the recorded number itself did not move.

---

## Iteration 3 — R4-T2 correction (arithmetic disclosure of the key population)

Triggered by `artifacts/runs/run_804e35d29531/REVIEW_TEST_iteration2.md`, `RESULT: FAIL`, finding
**R4-T2** (G1, MAJOR, blocking). R2 is confirmed resolved and nothing R2 fixed was touched: the
baseline procedure, the neutral Reviewer prompt and `run_92759e0e1034`'s dispatch and audit trail
are all unchanged in this round. This correction is scoped entirely to what gets **committed**.

### What R4-T2 found

Iteration 2 redacted the denominator as a *field* and stopped there. That was not a disclosure
control, because the remaining published numbers solved for it two different ways:

* `TEST.md` twice quoted a live scoring result whose `denominator` member carried its real value —
  a direct disclosure, not an inference.
* `BASELINE_RESULT.md` published the Reviewer's finding count, the unmatched-finding count and an
  exact recall decimal. Subtract the second from the first for the matched count, divide by recall,
  and the withheld population comes out exactly.

Both files were at zero under the shipped literal `scan-leak` and under
`semantic_leak_scan --profile evidence` at the time. The finding is therefore as much about the
validation as about the documents: **token matching cannot detect a leak that consists of no
tokens.**

### The rule this round adopted, and applied to both files

Publishing any *two* of `{key population total, detected/matched count, missed count,
unmatched-finding count, reviewer finding count, recall}` determines the rest. Redacting one member
of that set is therefore not a control. The rule adopted instead is **P-1**, stated in full in
`BASELINE_RESULT.md`:

> Committed evidence publishes **at most one** quantity from that set, and publishes it only as a
> **coarse bucket**, never as an exact value. Everything else is reported qualitatively.

Applied consistently:

| document | before | after |
|---|---|---|
| `BASELINE_RESULT.md` | reviewer finding count, unmatched count, exact recall decimal, exact evidence-grounding ratio | recall as the bucket **50–75%** and nothing else numeric from the set; the finding count, the unmatched count and the exact recall are all withheld; the five B-5 criteria and every other row are qualitative |
| `TEST.md` | two live quotations whose `value` / `numerator` / `denominator` members carried their real magnitudes, an unmatched count inside a quoted stderr string, and a reviewer finding count plus an unadjudicated-finding count in the iteration-2 summary | every one of those replaced by `<withheld>` or by a qualitative statement; the quoted output keeps its structure, its field names, which fields are present, its exit codes and its non-metric values verbatim |

Nothing that the §9 cases actually require was lost. Each case here is structural — *is* there an
explicit denominator, *is* an unmatched finding defaulted to `UNADJUDICATED`, *does* refused
precision exit non-zero — and none of them depends on the magnitude of a number. That is what makes
P-1 cheap: it removes exactly the part of the evidence that carried no verification weight and all
of the disclosure.

### The disclosure check added to the tooling

`artifacts/runs/run_92759e0e1034/tools/semantic_leak_scan.py` gained a `metric_inference` check.
It extracts the evaluation's numeric metric fields from a document — as JSON-ish `field: value`
pairs and as prose, in digits or in number words — and reports whether any combination of them
algebraically determines the key population, under the relationships
`scripts/final_review_eval.py` actually computes. The relationships it checks, stated explicitly:

| id | relationship | fires when the document publishes |
|---|---|---|
| REL-1 | the denominator / population total is itself a published field | that field, in any spelling (`denominator`, `population_size`, `…_total`) |
| REL-2 | `recall = detected / total` → `total = detected / recall` | recall **and** the detected/matched/numerator count |
| REL-3 | `recall = 1 − missed / total` → `total = missed / (1 − recall)` | recall **and** the missed count |
| REL-4 | `total = detected + missed` | the detected count **and** the missed count |
| REL-5 | `detected = reported findings − unmatched`, then REL-2 | the Reviewer's finding count, the unmatched count **and** recall |
| REL-6 | recall written as the fraction `detected/total` | that fraction, in any spelling |

Three properties worth stating, because they are what make it a check rather than a gesture:

* **Buckets are not values.** `50-75%` and `between 50% and 75%` are stripped before extraction, so
  P-1's coarse recall is deliberately not a hit — the check permits exactly the presentation P-1
  prescribes.
* **Satisfiability, not correctness, is the trigger.** A hit is raised whenever the published
  numbers pin a positive integral total, whether or not that total is the real one. A reader doing
  the arithmetic does not know the answer in advance either. When the scanner is handed the real
  key it additionally annotates whether the solved value matched, which is diagnostic output, not
  the trigger.
* **It runs cross-file as well as per-file.** After the per-file pass it runs once more over the
  union of every metric found anywhere in the target set, because a commit set can jointly disclose
  what no single file in it discloses alone. `--no-cross-file` turns that off.
* **Three guards keep it from reading prose as data**, since a check that fires on every document
  would just be turned off: a number that is part of an identifier or label (`REL-5`, `P-1`,
  `F-005`, `1.0`) is not a value; a comparison (`recall < 1.0`) is not a published value, only an
  assignment or a table cell is; and the ambiguous English readings of *detected* and *missed*
  ("all three detected") count only inside an evaluation context — near `key`, `entr…`, `defect`,
  `finding`, `recall`, `population` or `seed…`. The JSON-ish `field: value` readings need no such
  gate. All three guards were added because they fired on this very document; none of them weakens
  the four controls above.

The check is in the `evidence` profile as well as `prompt`, since a committed artifact is exactly
what it exists to guard. The shipped `scan-leak` was again left untouched — it is
IMPLEMENTATION-owned surface and this round changes no production script.

**Regression evidence that it reproduces R4-T2.** Run against the pre-correction versions of the
two documents it fires precisely as the finding described:

```text
$ semantic_leak_scan.py --profile evidence --no-cross-file --target <pre-correction BASELINE_RESULT.md>
HIT metric_inference: REL-5: reported findings=<n> minus unmatched=<n> gives detected=<n>,
    with recall=<n> determines the key population size (solves to <n>; matches the key)
semantic leak scan [evidence] FAILED (1 files scanned, 1 hits)

$ semantic_leak_scan.py --profile evidence --no-cross-file --target <pre-correction TEST.md>
HIT metric_inference: REL-1/REL-6: an explicit denominator / population total determines the key
    population size (solves to <n>; matches the key)
HIT metric_inference: REL-2: recall=<n> with detected=<n> determines … (matches the key)
HIT metric_inference: REL-5: reported findings=<n> minus unmatched=<n> gives detected=<n>,
    with recall=<n> determines … (matches the key)
… 5 hits total
semantic leak scan [evidence] FAILED (1 files scanned, 5 hits)
```

(The operands are shown as `<n>` here for the obvious reason: quoting the tool's own output
verbatim in a committed document would republish the very numbers the check exists to catch. The
run is reproducible from `git show` of the pre-correction revision.)

Negative control, so the check is not vacuous: a document that publishes recall **only** as the
bucket `50-75%` and no other metric returns zero, and the same document with an exact recall
decimal and an unmatched count added returns a REL-3 hit. Buckets pass; exact pairs do not.

### The sweep over the exact final commit set

All three checks — the shipped literal `scan-leak`, the `semantic_leak_scan --profile evidence`
vocabulary checks, and `metric_inference` per-file **and** cross-file — were re-run over the exact
set this correction proposes to commit. The set is unchanged from iteration 2's list above, except
that `run_92759e0e1034/tools/semantic_leak_scan.py` now carries the new check.

```text
### literal scan-leak, file by file (14 non-forensic files)
  run_92759e0e1034/ORCHESTRATOR_LOG.md ................. leak scan PASSED
  run_92759e0e1034/TIMING_LOG.md ....................... leak scan PASSED
  run_92759e0e1034/FINAL_REVIEW_EVIDENCE_BUNDLE.json ... leak scan PASSED
  run_92759e0e1034/final_review_audit/attempt1__…/input.md   leak scan PASSED
  run_92759e0e1034/final_review_audit/attempt1__…/report.md  leak scan PASSED
  run_92759e0e1034/final_review_audit/attempt1__…/record.json leak scan PASSED
  run_92759e0e1034/tools/semantic_leak_scan.py ......... leak scan PASSED
  run_804e35d29531/BASELINE_RESULT.md .................. leak scan PASSED
  run_804e35d29531/TEST.md ............................. leak scan PASSED
  run_ff587481a820/ORCHESTRATOR_LOG.md ................. leak scan PASSED
  run_ff587481a820/TIMING_LOG.md ....................... leak scan PASSED
  run_ff587481a820/attempt1_scoring/FINDINGS.json ...... leak scan PASSED
  run_ff587481a820/attempt1_scoring/METRICS.json ....... leak scan PASSED
  run_ff587481a820/final_review_audit/attempt1__…/record.json leak scan PASSED
  -> 0 failures

### semantic_leak_scan --profile evidence, same 14 files, per-file AND cross-file union
  semantic leak scan [evidence] PASSED (14 files scanned, 0 hits)   exit 0

### metric_inference over the union INCLUDING the four retained forensic rows
  metric_inference hits: 0
  (that union still reports vocabulary hits on the forensic rows, by design and unchanged
   from iteration 2 — but not one arithmetic hit, so nothing in the commit set, forensic
   rows included, determines the key population)

### unchanged R2 evidence, re-verified after the tooling change
  --profile prompt, replacement Reviewer input ... PASSED, 0 hits
  --profile prompt, superseded Reviewer input .... FAILED, 11 hits (R2 still reproduced)
```

The last block matters: extending the scanner must not weaken what it already caught. The
superseded input still trips 11 checks and the replacement input still trips none, so R2's evidence
is exactly as it was.

### Out-of-scope disclosures found by the new check, escalated not fixed

Running `metric_inference` over the wider artifact tree surfaced the same arithmetic disclosure in
files this Task does not own and must not rewrite. They are reported here rather than edited:

| file | what it carries | why it was not fixed here |
|---|---|---|
| `artifacts/runs/run_804e35d29531/ORCHESTRATOR_LOG.md` (row for `dispatch_settled` / TEST iteration 2) | the review's own `detail` text restates the finding by quoting the exact denominator and the exact finding/unmatched/recall triple | Coordinator-owned append-only log; a worker rewriting a settled row is not a redaction, it is tampering with the audit trail |
| `artifacts/runs/run_804e35d29531/final_review_audit/attempt1__task_75d5e97d1679__ctx_*/input.md` (three records) | the Final Review dispatch input summarises the §7 baseline as an exact `detected/total` fraction with its decimal — REL-6 and REL-2 both fire | immutable per-dispatch audit records whose `artifact_digest_post_redaction` is recorded in the paired `record.json`; editing the bytes breaks the digest chain that T-1 exists to prove |

Both are real R4-class disclosures under R4-T2's own yardstick. They were escalated to the
Coordinator, who directed that both be left untouched and documented here. Recorded plainly rather
than laundered, and rather than silently claiming a clean sweep over a set that includes them.

Three things the Coordinator asked be stated explicitly, so this is not miscategorized:

1. **These are structural residuals, not omissions.** `ORCHESTRATOR_LOG.md` is append-only and the
   per-dispatch audit records are immutable — both by this ticket's own design (§4 and §3). Editing
   either is not redaction; it is destroying the guarantee the artifact exists to provide, and in
   the audit records' case it would break the exact digest chain T-1 was written to prove. No
   correction round scoped to TEST-phase evidence can or should fix them.
2. **The three leaking `input.md` files are a Coordinator-side drafting mistake, not a TEST-phase
   code defect.** They are the Coordinator's own Final Review Task specs; their provenance/ledger
   summary section stated this baseline's result as an exact matched-over-total fraction with its
   decimal rather than as a coarse bucket. The leak is in how that summary was written, not in
   anything the evaluation tooling or this phase's tests do. Flagged here for the record so it is
   filed against the right surface.
3. **Full resolution needs one of two things, neither of them in this round's scope.** Either a
   documented redaction/quarantine exception for provenance logs and immutable audit records — a
   policy decision above this correction — or the Coordinator drafting future Final Review inputs
   without exact baseline fractions, which the Coordinator has committed to doing for any further
   Final Review attempt in this run. No policy fix was attempted here.

## Review Feedback Resolution

| id | source | status | resolution |
|---|---|---|---|
| R2 | Final Adversarial Review (`FINAL_REVIEW.md`), G1 MAJOR, blocking | **resolved** | The baseline was re-run in a new Orca Run, `run_92759e0e1034`, against a freshly materialized fixture, with a Reviewer input carrying only the ordinary §17/§11 framing and the undifferentiated A–I axis list. No defect class is named, no contract section is weighted above another, the subject is not disclosed as a fixture or an evaluation, and no finding count is stated. Leak validation was extended past literal matching by `run_92759e0e1034/tools/semantic_leak_scan.py`, which reproduces R2 on the superseded input (11 hits) and returns 0 on the replacement input. The superseded write-up was replaced. |
| R4 | Final Adversarial Review (`FINAL_REVIEW.md`), G1 MAJOR, blocking | **resolved**, with a follow-up (R4-T2) | Scorer output for the replacement run was written outside the repository and is not committed. `BASELINE_RESULT.md` was rewritten to report an aggregate only, naming no entry id, mapping, total, missed-entry list, subject identifier or key path. A leak scan was run over the exact artifact set proposed for commit, file by file. The superseded run's key-derived scorer output was quarantined. The *identity* half of R4 is closed by this. Its *arithmetic* half was not — the aggregate still published enough numbers to solve for the withheld total — and is closed by R4-T2 below. |
| R2-T1 | found by this round's extended scan, escalated to the Coordinator, resolution directed by them | **resolved** | The iteration-1 `## Test Scope` passage in this file reproduced the key in full. It was replaced with a non-identifying description that keeps every verification claim; the key's path was replaced with `<key>` throughout; two quoted metric blocks had schema field names elided, with a note at the head of `## Execution`. This file is now at zero hits under both scanners. |
| R4-T2 | TEST Reviewer, iteration 2 (`REVIEW_TEST_iteration2.md`), G1 MAJOR, blocking | **resolved** | Iteration 2 redacted the denominator as a field but left enough other numbers published to solve for it. Rule **P-1** was adopted instead — committed evidence publishes at most one quantity from `{population total, detected, missed, unmatched, reviewer finding count, recall}`, and only as a coarse bucket — and applied to both files: `TEST.md`'s two live scoring quotations now carry `<withheld>` in place of every metric magnitude and its iteration-2 summary is qualitative, and `BASELINE_RESULT.md` publishes recall as the bucket `50-75%` and nothing else numeric from the set. Validation was extended past token matching with a `metric_inference` check in `run_92759e0e1034/tools/semantic_leak_scan.py` that solves the six relationships REL-1 … REL-6 over the metric values a document publishes, per file and across the whole set; it reproduces R4-T2 on the pre-correction files and returns zero on the corrected ones. The full sweep over the exact commit set is above. Two disclosures of the same class were found in Coordinator-owned files this Task must not rewrite; they are reported under `### Out-of-scope disclosures found by the new check`, not silently included in the clean result. |
| R1, R3 | Final Adversarial Review, routed to DESIGN | **not this round** | The redaction-pattern scope (R1) and the closed-world formula (R3) are being corrected in a parallel DESIGN round and reach this phase through §17's T5a downstream revalidation. This round's baseline may still exhibit either if it happened to touch those paths; that is expected and is re-verified after the downstream revalidation, not here. |

### Known residual

`artifacts/runs/run_ff587481a820/` is retained and still tracked in git, per the correction
instruction to preserve it as forensic evidence. After the quarantine above it no longer makes the
key's contents reachable, but its `input.md` still shows which categories that Reviewer was pointed
at, and `EXPORT_BUNDLE.json` embeds that text. Stated rather than laundered: that run is
**superseded**, is not the accepted baseline, and must not be cited as one. If the project would
rather the repository hold no such material at all, moving that directory into an out-of-band
forensic archive is the follow-up — a separate decision, not taken here.

---

## TEST iteration 4 — downstream revalidation (§17 T5a)

Not a correction round: no TEST-phase finding is open. This is the §17 T5a revalidation triggered
by two **corrected upstream artifacts**. DESIGN's D-C and D-E were rewritten (commit `476dcc9`) in
response to the Final Adversarial Review's R1 and R3, and IMPLEMENTATION landed the resulting code
in `9e19ce0` and `2d863ea` and recorded it as `## IMPLEMENTATION iteration 4`. Both precede TEST in
canonical order, so this phase re-checks its own claims against them.

**Something did have to change here too, and it is stated up front so that "nothing needed
updating" is visibly not what happened.** Two of iteration 1's coverage rows are no longer accurate
as written and now carry inline supersession markers, and the T-1/T-3 and T-4 groups this phase
owns had no case exercising either corrected behaviour: every test of the corrected code lived in
the module that owns it. Sections above the iteration-2 divider are otherwise untouched, per this
document's existing convention.

**The baseline procedure and `run_92759e0e1034` were not touched in this round** — deliberately,
and on the Task's instruction. A fresh baseline reflecting the code fix would be a new capture, not
a revalidation, and is a separate future activity.

### Test Scope / Existing Test Assessment

Two questions, asked group by group: *does the corrected behaviour have a test in the suite this
phase owns*, and *is any claim this document already made now false?*

**1 — T-1 / T-3, `redaction/1.1` and the P-PATH postcondition.**

IMPLEMENTATION's own module covers the corrected rule thoroughly:
`test_run_logging.ForeignAbsolutePathRedactionTests` (5) proves the pattern including all four
one-segment D3-001 cases, `RetainedPathFieldClassifierTests` (4) proves the classifier is total and
that `assert_retained_path_field("/luminous")` raises, and `RetainedPathFieldRecordTests` (4) drives
the three ladder rungs through the real writer and sweeps `record.json`.

What none of them does — and what PLAN's T-1 last case and T-3 third case are actually about — is
read the **published unit** back off disk. `RetainedPathFieldRecordTests` reads `record.json` only;
`input.md`, `report.md` and the `ORCHESTRATOR_LOG.md` row that names the directory are never
examined for a surviving absolute path, and the log file is not part of any record, so it has no
owning module at all. That is precisely the cross-file residue `test_os22_required_tests.py` exists
to hold. **PARTIAL → tests added.**

The iteration-1 T-3 row *a synthetic `dcap_…` and a `/Users/<name>/…` path do not survive* remains
true but now understates the rule: under `redaction/1.0` it was the whole rule, and every root
outside the three-home allowlist failed open. The row is annotated in place.

**2 — T-4, the closed-world `ATTESTED_FALSE_POSITIVE` classification.**

`test_final_review_eval.ClosedWorldFalsePositiveRateTests` (6) proves the corrected arithmetic by
exact value at the `score()` boundary, and `ExitCodeTests` covers the two new exit-code cases. But
PLAN's T-4 case is stated as a **contract over §6**, and iteration 1 discharged it by quoting a
live CLI run. After the correction that case has two answers rather than one, and nothing showed
that the *same* findings document takes both — that the reclassification follows from the
adjudication input and from nothing else. There was also no source-level guard that the attested
class is unreachable except through the closed-world branch, which is the surviving half of "never
auto-FP" and the half no per-call test can establish. **PARTIAL → tests added.**

The iteration-1 T-4 row *an unmatched finding is `UNADJUDICATED`, never auto-FP* is the one claim
in this document that the correction makes **inaccurate as written**. Its second half is unchanged
and still enforced; its first half is now the *default*, overridable only by an explicit signed
closed-world attestation. The row is annotated in place and the corrected rule is pinned by the new
tests.

**3 — T-2, T-5, T-6.** No claim was invalidated.

* **T-2.** The threshold guard reads the OS-22 section of both `run_logging.py` copies, both
  emission-method bodies and the scorer's numeric literals. All four surfaces were edited by the
  correction, so the guard was re-run over the edited sources: still clean, and
  `observed_input_bytes` is still an operand of no comparison. Nothing to change.
* **T-5.** Every regression command was re-run (below); all green. The recorded test count moves
  because IMPLEMENTATION added 27 and this round adds 8 — iteration 1's `984` is left as the
  historical record of that run, and the current figure is stated below.
* **T-6.** The concern the Task names — whether a redaction/scoring bugfix silently changed what a
  Reviewer sees — was checked three ways, not one, and the answer is no. See *Behavior Covered*.

### Added / Modified Tests

One existing file extended. **No existing test was edited, weakened, skipped or deleted, and no
production file was touched.**

| file | classes added | tests | group |
|---|---|---|---|
| `scripts/test_os22_required_tests.py` | `ForeignAbsolutePathAcrossThePublishedUnitTests` (4), `ClosedWorldMetricContractTests` (4) | **8** | T-1 × T-3, T-4 |

The module's import block gained `sys.path.insert(...)` and
`from scripts import test_final_review_eval as eval_fixtures`. That reuse is deliberate: the eval
module's report constants are shaped by the key, and a second copy of key-shaped content in
a second file is exactly what finding R2-T1 was about. The `sys.path` line is needed because
`test_final_review_eval` imports `run_logging` by its bare name, which resolves under
`unittest discover -s scripts` but not under a dotted import.

### Behavior Covered

**T-1 × T-3 — the corrected rule over the whole published unit.**

* `test_no_file_of_the_published_unit_matches_the_category_five_pattern` publishes one record
  through the **real writer** with a report living outside every root the ladder knows and a stored
  spec carrying two category-5 values (a deep session-scratch path of the shape the shipped
  baseline actually leaked, and a bare one-segment root). It then re-reads `input.md`, `report.md`,
  `record.json` **and `ORCHESTRATOR_LOG.md`**, and asserts each is a fixed point of
  `run_logging._FOREIGN_ABSOLUTE_PATH` — the **production** pattern, imported rather than
  restated, so an edit to the rule cannot leave this test asserting the superseded one — plus a
  fragment check for the identifying substrings.
* `test_the_record_counts_the_new_category_and_stamps_the_executable_policy` requires the category
  to be *exercised*, not merely present: `foreign_absolute_path` must appear in
  `REDACTION_CATEGORIES` **and** the record's published count for it must be ≥ 2. It also asserts
  category 4 still owns the home path and still leaves the tail readable, so category 5 did not
  swallow the readable half, and that the stamp is `redaction/1.1`.
* `test_the_superseded_policy_cannot_be_re_executed_over_the_retained_input` asserts that
  `redact_text(..., policy_version="redaction/1.0")` **raises**, which is what stops a reader from
  re-deriving the retained digests under the old rule and concluding the one-segment root was fine,
  and that the one-segment root is replaced whole with nothing borrowed from the input.
* `test_the_identity_join_still_holds_when_the_report_path_is_replaced_whole` is the case the two
  groups create together: T-1's join must not be collateral damage of T-3's fix. With
  `report.contract_path` reduced to the placeholder, the log row must still resolve to the
  directory, both artifacts must still re-hash to the digests the record states, every field of
  `FINAL_REVIEW_RETAINED_PATH_FIELDS` must still pass `assert_retained_path_field`, and
  `report_digest_pre_redaction` must still be the untouched source — so the record no longer says
  *where* the report was but still says *which bytes* it was.

**T-4 — §6's metric contract, end to end and at the source.**

* `test_the_default_for_an_unmatched_finding_is_still_unadjudicated` re-pins the half of the case
  the correction did not change, at the CLI boundary: no adjudications → `UNADJUDICATED`,
  `attested_false_positives` 0, both metrics `null`, `REFUSED`, exit **3**.
* `test_the_same_findings_become_an_attested_false_positive_under_attestation` runs the **same
  `findings.json`** with an attestation and nothing else changed → `ATTESTED_FALSE_POSITIVE`,
  `attested_false_positives` 1, `adjudicated_false_positives` 0, `unadjudicated_count` 0,
  `complete_by_attestation`, `COMPUTED`, exit **0**. The R3 regression is asserted structurally:
  the rate is **not** `0.0`, it equals the single unmatched finding's share of the findings total,
  and `precision + false_positive_rate == 1`. (Magnitudes withheld under P-1 — see the note below.)
* `test_the_two_metrics_are_one_decision_on_both_inputs` asserts
  `precision_status == false_positive_rate_status` on both inputs, and that COMPUTED implies
  `unadjudicated_count == 0`.
* `test_no_route_but_the_closed_world_branch_reaches_the_attested_class` is the surviving half of
  "never auto-FP" as a **source-level** property, which no per-call test can give: an AST walk
  requires that `ATTESTED_FALSE_POSITIVE` is assigned in exactly one function
  (`classify_unmatched`), and that at least one `If` whose subtree contains that assignment tests
  `closed_world`.

> **P-1 applies to this section too.** The new T-4 tests assert exact magnitudes in code, but the
> matched count and the findings total of the fixture report jointly determine the key population,
> so no magnitude from `{key population total, detected/matched count, missed count,
> unmatched-finding count, reviewer finding count, recall}` is published here. Every claim above is
> structural, so nothing is lost: "the rate is the finding's share, not zero" is the whole content
> of the R3 regression.

**Falsification of the new tests** (a passing test that cannot fail proves nothing — the same bar
iteration 1 set).

| new assertion | falsified by | result |
|---|---|---|
| the published-unit sweep is armed | re-running it with category 5 removed from `REDACTION_CATEGORIES`, i.e. `redaction/1.0`'s table | **FAIL** — the sweep fires, and so does the counts test |
| the one-segment root is replaced with no segment floor | redacting the same spec under the pre-1.1 table | pre-1.1 leaves `root: /luminous` **verbatim**; `redaction/1.1` gives `root: <REDACTED:foreign_absolute_path>` |
| the AST tripwire detects a second assignment site | appending `def sneaky_auto_fp(item): classification = ATTESTED_FALSE_POSITIVE` to the scorer source and re-running the tripwire's own logic | detected — `['classify_unmatched', 'sneaky_auto_fp']`, so the equality assertion fails |
| the T-4 attestation test is an R3 regression guard | running the identical CLI invocation against the pre-fix scorer (`git show 476dcc9:scripts/final_review_eval.py`) | **five** assertions fail: classification `UNADJUDICATED`, `attested_false_positives` **absent from the document**, `false_positive_rate` **0.0**, `unadjudicated_count` 1, status `partial` |

That last row is the R3 defect reproduced through this phase's own new test rather than through
IMPLEMENTATION's: the pre-fix scorer returned exit 0 under `--require-precision` while reporting a
zero false-positive rate and one unadjudicated finding at the same time.

### Execution

All of T-5's regression commands, re-run after the IMPLEMENTATION revalidation's changes **and**
after this round's additions.

```text
Command: python3 scripts/validate_skills.py
Result:  PASS — "Skill validation PASSED (463 checks)"
                (unchanged from iteration 1, after the SKILL.md §9 and validator-anchor edits)

Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  PASS — "Ran 1019 tests in 63.488s / OK (skipped=6)"
                (984 at iteration 1, +27 from IMPLEMENTATION iteration 4, +8 here. The 6 skips are
                 test_orca_runtime.py's opt-in live-runtime tests, pre-existing and unrelated.)

Command: python3 scripts/verify_package.py
Result:  PASS — "Package verification PASSED (107 source files)"
                (unchanged: this round added no file, only classes to an existing one)

Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result:  PASS — no output, exit 0 (byte-identical; the D-C commit edited both in one commit)
```

The suite was also run once **before** this round's additions, so the two effects are separable:
`1011 tests, OK` on `0582aed` with everything else identical. IMPLEMENTATION's revalidation was
therefore already green on its own, and the 8 added here take it to 1019.

Per-group runs:

```text
Command: python3 -m unittest scripts.test_os22_required_tests
Result:  PASS — Ran 27 tests, OK                (19 from iteration 1 + the 8 added here)

Command: python3 -m unittest scripts.test_e2e_harness.FinalReviewObservabilityNeutralityTests
Result:  PASS — Ran 12 tests, OK                (T-6, the byte-identity golden)

Command: python3 -m unittest scripts.test_os22_required_tests.OrcaRuntimeDispatchPathNeutralityTests
Result:  PASS — Ran 3 tests, OK                 (T-6, the other dispatch path)
```

T-6, checked three ways rather than trusting the label "pure bugfix":

```text
1. The neutrality golden, unchanged and still byte-identical.
   test_every_workflow_spec_is_byte_identical_to_the_pre_os22_capture and
   test_every_direct_spec_is_byte_identical_to_the_pre_os22_capture both PASS against
   scripts/fixtures/os22_neutrality/pre_os22_task_specs.json. `git status` reports the
   fixture, e2e_harness.py and orca_runtime_harness.py all clean, so the golden was not
   regenerated to fit — it is the same 350 003 bytes committed in e168344, compared against
   freshly rendered specs. Byte-identical is the assertion the class makes; it passed.

2. The reviewer-visible workspace, by digest.
   Command: python3 scripts/final_review_eval.py materialize --dest <scratch>/ws
   Result:  fixture_digest sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d
            files 14
   That is character-for-character the digest iteration 1 recorded. The bytes a Reviewer reads
   are unchanged by both corrections.

3. Search behaviour and fixture integrity, re-run.
   Command: python3 scripts/final_review_eval.py verify-fixture --key <key>
   Result:  "fixture verification PASSED"  exit 0
   Command: python3 scripts/final_review_eval.py scan-leak --key <key> --target <ws>
   Result:  "leak scan PASSED"  exit 0
   Command: python3 scripts/final_review_eval.py scan-leak --key <key> \
              --target scripts/fixtures/final_review_eval/subject
   Result:  "leak scan PASSED"  exit 0
```

The one code change that *could* have touched T-6 is `final_review_eval.py` importing
`run_logging`. It does not: the import is used only by `_retained_path_field()` on the scorer's own
output document, `final_review_eval` is not on the spec-assembly → dispatch path at all, and the
two neutrality tripwire classes — which patch `redact_text`, `capture_stored_task_spec`,
`capture_delivery_evidence` and `write_final_review_audit_record` to raise on any call — still pass
on both dispatch paths.

### Failures / Findings

**No test failed, and no new defect was found.** The two upstream corrections were re-derived
against this phase's own suite and both behave as DESIGN and IMPLEMENTATION describe.

Per this phase's Mandatory Invariant nothing was fixed here in any case; the one production defect
this phase has ever reported, **T-001**, is unrelated to R1/R3 and was re-checked rather than
assumed:

```text
$ python3 scripts/final_review_eval.py score --findings <a findings doc whose entry
    has no location_file> --key <key>
KeyError: 'location_file'
```

Still reproducible on `0582aed`. **T-001 remains open, unchanged in severity (MINOR, non-blocking)
and unchanged in its suggested fix.** One detail of its write-up has drifted and is corrected here
rather than silently left wrong: the two line numbers it cites moved with the D-E edit —
`match_findings`'s unguarded index is now `scripts/final_review_eval.py:763`, not `:748`. The
symbol names in the finding (`match_findings`, `score`, `load_adjudications`) are unchanged and
remain the reliable reference. The D-E correction rewrote the metric gate around it without
touching `match_findings`'s unguarded indexing, which is consistent — T-001 was routed to a later
IMPLEMENTATION correction or an explicit accept-as-is, not to this one.

### Disclosure re-check over the corrected upstream artifacts

Iteration 3's `metric_inference` check exists precisely because a correction can introduce an
arithmetic disclosure that no token scan sees, so it was re-run rather than assumed — over this
file, and over the two artifacts the correction actually rewrote.

| target | result |
|---|---|
| `TEST.md`, after this round's additions — literal `scan-leak` | **PASSED**, 0 hits |
| `TEST.md`, after this round's additions — `semantic_leak_scan --profile evidence` (identity checks + `metric_inference`) | **PASSED**, 0 hits |
| `BASELINE_RESULT.md`, unchanged this round | **PASSED**, 0 hits — re-verified, not assumed |
| the other 12 files of iteration 3's committed evidence set | byte-unchanged since that sweep (`git status` clean for all of them), so the recorded result stands |

One hit was found in this round's own first draft and fixed before commit: a sentence in *Added /
Modified Tests* named the key by its literal two-word phrase. That is the whole point of running
the scan on the draft rather than on the intention.

**The two corrected upstream artifacts were also scanned, and the correction did not materially
worsen either.** Reported as an observation, not fixed — they belong to other phases, and neither
is in iteration 3's reviewer-visible evidence set.

| artifact | before the correction | after | delta |
|---|---|---|---|
| `IMPLEMENTATION.md` (`f62047a~1` → now) | 2 hits (one key phrase, one partial-archetype coincidence on ordinary prose) | **identical 2 hits**, 0 `metric_inference` | none |
| `DESIGN.md` (`476dcc9~1` → now) | 378 hits, 7 of them `metric_inference` | 379 hits, 8 of them `metric_inference` | **+1**, and it is a REL-5 satisfiability hit that solves to a value which is **not** the key population — a false positive of the check's own "satisfiability, not correctness" trigger, not a new disclosure |

`DESIGN.md`'s pre-existing profile is a separate matter and is **escalated, not fixed**: it carries
one REL-1 hit that does solve to the key population, plus key vocabulary that a design document
describing the fixture will inevitably contain. It predates both R1/R3 corrections, it is a
DESIGN-phase artifact this Task must not rewrite, and it was never part of the 14-file evidence set
iteration 3 swept — the P-1 rule was adopted for *committed reviewer-visible evidence*, and whether
it should also bind phase design documents is a policy question above this revalidation. Recorded
here so the clean result above is not read as a clean result over the whole run directory.

### Remaining Gaps

Iteration 1's gap list is re-checked item by item rather than restated.

1. **The live baseline dispatch (§7 / PLAN B-1…B-5) is still deferred**, unchanged — and the
   correction adds a wrinkle worth recording explicitly: the two committed baselines
   (`run_ff587481a820`, `run_92759e0e1034`) were captured under `redaction/1.0` and retain the
   pre-fix path-leak artifacts as forensic evidence of R1's prior state. IMPLEMENTATION iteration 4
   deliberately left them alone and left D-C C.7's *regeneration of already-retained evidence* rule
   open. Regenerating a baseline under `redaction/1.1` is a fresh capture, which is a TEST-phase
   activity — **but it was explicitly placed out of scope for this revalidation**, so it is
   recorded here as the open item it is rather than performed.
2. **T-4's `baseline execution 성공` case** — unchanged, still a recording rule over a live run.
3. **T-001** — unchanged, open by design, re-verified above.
4. **The `agent_prompt_blocked` delivery limit** — unchanged, still a stated limit.
5. **No H-1/H-2/H-4/H-5 conclusion is available from this phase** — unchanged.
6. **New, and small: the P-PATH postcondition covers the closed table, not every future field.**
   `FINAL_REVIEW_RETAINED_PATH_FIELDS` is enumerated, and `_assert_retained_path_fields()` checks
   exactly those three. `RetainedPathFieldRecordTests.test_no_string_anywhere_in_the_record_begins_
   with_a_separator` is the generic sweep that would catch a fourth field added without routing it
   through the ladder, and this round's published-unit sweep extends that reasoning to the other
   three published files. Neither is a *proof* that a future field is safe — the closed table plus
   the two sweeps is the control, and it is a control that fails closed. Recorded so a reviewer
   does not have to re-derive why an enumerated table is acceptable here.

## TEST iteration 5 — downstream revalidation for R5 (§17 T5a)

Not a correction round: no TEST-phase finding is open, and `relevant_previous_findings` is empty.
This is the §17 T5a revalidation triggered by one **corrected upstream artifact**. The Final
Adversarial Review's R5 was adjudicated as a DESIGN-phase gap; DESIGN.md gained a new subsection
**A.6** (commit `a8bec44`); IMPLEMENTATION applied it as a repository-root `.gitattributes`
(`7718ea5`) and pinned it with `RetainedReportWhitespaceExemptionTests` (`d000d70`), recorded as
`## IMPLEMENTATION iteration 5` in `ad22943`. Both artifacts precede TEST in canonical order, so
this phase re-checks its own claims against them. This is the last iteration available to TEST
under this run's `max-iterations=5` budget.

**Something did have to change here, and it is stated up front so that "nothing needed updating"
is visibly not what happened.** `git diff --check <base>..HEAD` is one of this project's standing
required regression commands — PLAN and DESIGN both list it in T-5's command block (`DESIGN.md`
§T-5, "All four green (`git diff --check` exits 0 with no output)") — but **iteration 1's T-5 case
table never carried a row for it**. Every other required command had a row; this one did not. That
was harmless while the gate happened to pass, and it stopped being harmless the moment R5 showed
the gate had in fact been failing (exit 2, 40 errors) with no TEST-owned row tracking it. Two rows
were therefore added to the T-5 table in place, above the iteration-2 divider, following this
document's existing annotation convention.

**No test was added by this round.** IMPLEMENTATION iteration 5 already wrote T-5a in the module
that owns the mechanism, and the Task is explicit that this phase need not duplicate it. What this
phase owed instead was (a) the missing coverage rows, and (b) an *independent* re-derivation of
both halves of A.6 — run by this phase, from the tree as committed, not read out of
IMPLEMENTATION's write-up. Both were done and both are below.

### Test Scope / Existing Test Assessment

Two questions, asked group by group: *does the corrected behaviour have a test in the suite this
phase owns*, and *is any claim this document already made now false?*

**1 — T-5, the whitespace gate. The one real gap, and it is a coverage-table gap, not a test gap.**

`RetainedReportWhitespaceExemptionTests` (7 tests, `scripts/test_run_logging.py`) covers A.6's
mechanism thoroughly and in the module that owns it — the gate's exit status, the exact
`.gitattributes` rule text, the digest of every published record unit, the pinned hard-break
report's 40 lines, `git check-attr` narrowness including the glob boundaries, and a clone-based
mutation control. There is nothing this phase could add to that without duplicating it, and the
Task says not to.

What was missing is on this document's side. **T-5's case table did not name `git diff --check` at
all** — the string appeared nowhere in this file before this round. So the one required command
that R5 turned out to be failing was the one command T-5 was not tracking. **Gap → coverage rows
added**, not tests:

* a row for `git diff --check <base>..HEAD` as a standing required command, with its re-run result
  and the test that now pins it rather than only a recorded run;
* a row acknowledging that `RetainedReportWhitespaceExemptionTests` exists, lives under `scripts/`,
  and is therefore inside the standard `unittest discover -s scripts -p 'test_*.py'` run — a claim
  this phase verified by name in the discovery output rather than inferring from the file's
  location (below).

**2 — T-6, neutrality. Not affected, and checked rather than assumed.**

R5 is a repository *Git-attribute* change. It adds one file, `.gitattributes`, that no Python
module reads, and it changes the behaviour of `git diff --check`, `git apply --whitespace` and
`git am` only. It touches no detection policy, no search policy, no redaction rule, no scorer
arithmetic, no `render_task_spec()` surface, and no byte of the reviewer-visible fixture. The
mechanism is nonetheless *adjacent* to reviewer-visible bytes — its whole purpose is that certain
retained bytes must not change — so "pure attribute change" was verified, not asserted: the
neutrality golden was re-run, and the retained bytes were re-hashed independently. Both below.
**No claim invalidated.**

**3 — T-1, T-2, T-3, T-4.** No claim invalidated, and the reasoning is the same shape as T-6's: no
production module, fixture, or record byte was modified by R5. The strongest available evidence is
not that argument but the digest sweep in *Behavior Covered* — every published record unit under
every `final_review_audit/` directory still hashes to the value its own `record.json` recorded,
which is precisely what T-1's audit-integrity and T-3's redaction claims rest on. If R5 had
disturbed any of them, that sweep is where it would show.

### Added / Modified Tests

**None.** No test file was created, edited, weakened, skipped or deleted by this round, and no
production file was touched.

| file | change |
|---|---|
| `artifacts/runs/run_804e35d29531/TEST.md` | two rows added to the `### T-5 — Regression` table in place (each carrying an `**added by iteration 5**` marker, per this document's convention); this section appended. Nothing removed. |

The tests that discharge the new rows were written by IMPLEMENTATION iteration 5 and are named in
those rows. Restating them here as though this phase authored them would be the failure mode the
Task explicitly rules out.

### Behavior Covered

Every item below was run by this phase against the tree as committed at `ad22943`, before this
round's own edit. None of it is quoted from IMPLEMENTATION's write-up.

**1 — The gate passes, independently confirmed (step 1).**

```text
$ git diff --check 1045815..HEAD ; echo EXIT=$?
EXIT=0
```

No output, exit 0, from the repository root at `ad22943`.

**2 — `RetainedReportWhitespaceExemptionTests` is reached by the standard discovery run (step 3).**

Not inferred from the file living under `scripts/`. The full discovery run was executed with `-v`
and its output grepped for the class name: **7** test methods appear, all `ok`, under the module
name `test_run_logging` as `unittest discover -s scripts` resolves it:

```text
test_every_retained_artifact_still_matches_its_recorded_digest      ... ok
test_only_retained_reports_are_exempt                               ... ok
test_the_gate_fails_again_once_the_exemption_is_removed             ... ok
test_the_gitattributes_rule_is_exactly_the_one_designed             ... ok
test_the_hard_break_report_keeps_its_forty_trailing_space_lines     ... ok
test_the_pattern_does_not_leak_outside_the_audit_directories        ... ok
test_the_whitespace_gate_passes_over_the_whole_os22_range           ... ok
```

**3 — The exempted bytes were not bought by trimming (step 1's other half).**

Re-derived directly, not through the test. For **every** record unit under
`artifacts/runs/*/final_review_audit/*/`, both the `report` and the `stored_task_spec` member were
re-hashed from disk and compared against the `artifact_digest_post_redaction` /
`byte_length_post_redaction` its own `record.json` records:

```text
OK  stored_task_spec  run_804e35d29531/…__ctx_4b509b12a0b1/input.md   9322  sha256:cb503eeb…
OK  stored_task_spec  run_804e35d29531/…__ctx_6478d2923ca0/input.md   9322  sha256:cb503eeb…
OK  stored_task_spec  run_804e35d29531/…__ctx_99cc7e6b886c/input.md   9322  sha256:cb503eeb…
OK  report            run_92759e0e1034/…__ctx_1f82fd26c92b/report.md  6028  sha256:6f91033e…
OK  stored_task_spec  run_92759e0e1034/…__ctx_1f82fd26c92b/input.md   4104  sha256:03001ef4…
OK  report            run_ff587481a820/…__ctx_33c8c8414587/report.md  6503  sha256:c9aecb9f…
OK  stored_task_spec  run_ff587481a820/…__ctx_33c8c8414587/input.md   3936  sha256:e084234f…
ALL_DIGESTS_MATCH True
```

`run_804e35d29531`'s three units carry `report.capture_status = "absent"` with an empty
`artifact_path`, so they have no retained report bytes to bind and only their `input.md` is
checked — which is why the sweep must not be read as "3 of 5 reports were skipped for
convenience". The pinned file specifically:

```text
$ shasum -a 256 …/attempt1__task_936f73b5d2eb__ctx_1f82fd26c92b/report.md
6f91033e4e2f644ab64eb4e61292734671b588d51ff0eb1649c626f8ae748e18
$ wc -c < …/report.md            → 6028
$ grep -c '  $' …/report.md      → 40
$ git diff --quiet -- 'artifacts/runs/*/final_review_audit/*/report.md' ; echo $?  → 0
```

40 two-trailing-space hard-break lines still present, digest and length exactly as recorded, and
every retained `report.md` byte-identical to `HEAD`. The gate passing did **not** cost a byte.

**4 — The exemption is narrow, measured independently.**

```text
unset        artifacts/runs/*/final_review_audit/*/report.md      (5 of 5)
unspecified  artifacts/runs/*/final_review_audit/*/input.md       (5 of 5)  ┐ 10 sibling files,
unspecified  artifacts/runs/*/final_review_audit/*/record.json    (5 of 5)  ┘ all still gated
unspecified  scripts/run_logging.py, README.md
unspecified  report.md, artifacts/report.md, artifacts/runs/<run>/report.md   (glob boundaries)
```

The committed `.gitattributes` is four comment lines plus exactly one rule,
`artifacts/runs/*/final_review_audit/**/report.md -whitespace`. It is tracked
(`git ls-files --error-unmatch .gitattributes` succeeds), so it travels with a clone rather than
depending on an untracked working-tree file. There is no `* -whitespace` line and no
`core.whitespace` change.

**5 — The regression control, re-derived by this phase in a throwaway clone.**

A local clone of `HEAD` was made and three independent controls run in it. This is the assertion
that matters most, because a gate that passes for the wrong reason looks identical to one that
passes for the right reason:

| control | expected | observed |
|---|---|---|
| clone as-is: is `.gitattributes` present after `git clone`? | yes — it is committed | **YES** |
| clone as-is: `git diff --check 1045815..HEAD` | exit 0 | **exit 0**, no output |
| `.gitattributes` deleted: same command | exit 2, naming only the hard-break report | **exit 2, 40 `trailing whitespace.` errors**, all in `run_92759e0e1034/…__ctx_1f82fd26c92b/report.md` |
| `.gitattributes` restored, then trailing whitespace committed into `scripts/_ws_probe_tmp.py` | still flagged | **exit 2**, flagged |
| …and into the sibling `input.md` of the *same* record unit | still flagged | **exit 2**, flagged |
| …and into the exempted `report.md` itself | not flagged | **not flagged** (0 occurrences in the output) |

The clone was deleted afterwards and `git status` on the real repository confirms no probe file,
no mutation, and a working tree carrying only this round's TEST.md edit. **The exemption removes
exactly the 40 pre-existing errors and suppresses nothing else** — including nothing in the other
two files of the very same record unit. The last row is the honest cost of the mechanism and is
recorded as a gap below rather than buried here.

**6 — T-6 neutrality, re-run rather than argued (step 5).**

```text
Command: python3 -m unittest scripts.test_e2e_harness.FinalReviewObservabilityNeutralityTests -v
Result:  PASS — Ran 12 tests, OK
```

`test_every_workflow_spec_is_byte_identical_to_the_pre_os22_capture` and
`test_every_direct_spec_is_byte_identical_to_the_pre_os22_capture` both pass against the unchanged
`scripts/fixtures/os22_neutrality/pre_os22_task_specs.json`; `git status` reports the fixture,
`e2e_harness.py` and `orca_runtime_harness.py` all clean, so the golden was not regenerated to fit.
`test_render_task_spec_gained_no_parameter` and the two audit-unreachability tripwires also pass.
R5 changed no detection or search policy, and the golden confirms it changed no reviewer-visible
byte either.

### Execution

All of T-5's regression commands — now including the one this round added a row for — re-run at
`ad22943`, after IMPLEMENTATION iteration 5's changes and before this round's TEST.md edit.

```text
Command: python3 scripts/validate_skills.py
Result:  PASS — "Skill validation PASSED (463 checks)"
                (unchanged; the new repository-root .gitattributes does not perturb it)

Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  PASS — "Ran 1026 tests in 66.012s / OK (skipped=6)"
                (1019 at iteration 4, +7 from IMPLEMENTATION iteration 5's T-5a, +0 here —
                 this round adds no test. The 6 skips are test_orca_runtime.py's opt-in
                 live-runtime tests, pre-existing and unrelated.)

Command: python3 scripts/verify_package.py
Result:  PASS — "Package verification PASSED (107 source files)"
                (unchanged: .gitattributes is not a packaged source file)

Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result:  PASS — no output, exit 0 (byte-identical; R5 touched neither copy)

Command: git diff --check 1045815..HEAD
Result:  PASS — exit 0, no output   ← the command T-5 had no row for until this round
```

Per-group runs:

```text
Command: python3 -m unittest scripts.test_e2e_harness.FinalReviewObservabilityNeutralityTests
Result:  PASS — Ran 12 tests, OK                 (T-6, the byte-identity golden)

Command: python3 -m unittest discover -s scripts -p 'test_*.py' -v  | grep RetainedReport…
Result:  PASS — 7 methods of RetainedReportWhitespaceExemptionTests present and ok
                (T-5a is reached by standard discovery, verified by name, not by inference)
```

### Failures / Findings

**No test failed, and no new defect was found.** A.6 behaves exactly as DESIGN specifies and as
IMPLEMENTATION reports, and this phase's independent re-derivation reproduces every claim it
checked — including the two that could have been true for the wrong reason (the gate passing, and
the digests being intact).

**Stated explicitly, per step 6 of the Task:** beyond the two coverage rows added to the T-5 table
and this section, **nothing else needed updating**, and the evidence for that is the audit above
rather than an absence of investigation — 1026 tests green, five required commands green, 7 record
members re-hashed, 15 `check-attr` paths measured, 6 clone controls run, and the T-6 golden re-run.

Per this phase's Mandatory Invariant, no production defect was fixed here in any case. The one
production defect this phase has ever reported, **T-001** (`final_review_eval.py score` tracebacks
on a malformed findings document), is unrelated to R5 — R5 touches no Python module — and remains
**open, MINOR, non-blocking**, unchanged in severity and in its suggested fix from iteration 4's
re-verification. It was not re-executed this round because no code on its path changed since
`0582aed`; `git log --oneline 0582aed..HEAD -- scripts/final_review_eval.py` is empty.

### Disclosure re-check over this round's own draft and the corrected upstream artifact

Iteration 3's P-1 rule binds committed reviewer-visible evidence, and iteration 3's
`metric_inference` check exists precisely because a correction can introduce an arithmetic
disclosure that no token scan sees. Both were re-run on this round's **draft**, before commit,
rather than assumed.

| target | result |
|---|---|
| `TEST.md`, with this section appended — literal `scan-leak` | **PASSED**, 0 hits |
| `TEST.md`, with this section appended — `semantic_leak_scan --profile evidence` (identity checks + `metric_inference`) | **PASSED**, 0 hits |
| `BASELINE_RESULT.md`, unchanged this round | **PASSED**, 0 hits on both scanners — re-verified, not assumed |
| the other 12 files of iteration 3's committed evidence set | byte-unchanged since that sweep, so the recorded result stands |

**The R5 correction added no disclosure to either upstream artifact.** Reported as an observation,
not fixed — they belong to other phases.

| artifact | before R5 | after | delta |
|---|---|---|---|
| `DESIGN.md` (`a8bec44~1` → now, i.e. the A.6 subsection alone) | 379 hits, 8 of them `metric_inference` | **379 / 8** | **none** |
| `IMPLEMENTATION.md` (`ad22943~1` → now) | 2 hits, 0 `metric_inference` | **2 / 0** | **none** |

`DESIGN.md`'s pre-existing profile remains what iteration 4 escalated and did not fix: a
DESIGN-phase artifact carrying one REL-1 hit that solves to the key population, predating R1/R3 and
never part of the 14-file evidence set iteration 3 swept. R5 neither worsened nor addressed it, and
it stays escalated rather than fixed here for the same reason as before — it is above this
revalidation's scope and belongs to another phase.

### Remaining Gaps

Iteration 4's list stands unchanged — items 1 through 6 are all untouched by R5, which changes no
Python, no fixture and no baseline. One item is added, and it is the direct cost of A.6 rather than
an unrelated observation.

7. **New: trailing whitespace inside a retained `report.md` is now permanently unpoliced — by
   design, and this is the trade DESIGN A.6 made explicitly.** Control 6 of the clone experiment
   above demonstrates it directly: a *newly committed* trailing-whitespace line inside the exempted
   `report.md` is not flagged by `git diff --check`. That is the intended behaviour — the whole
   point is that Reviewer-authored Markdown hard breaks in a digest-bound, A.3-immutable snapshot
   must survive — and A.6 argues the case at length. It is recorded here anyway because it is a
   real reduction in gate coverage over one path class, it is invisible from the passing gate, and
   a future reader is entitled to see the cost stated by the phase that verified it rather than
   only by the phase that chose it. The countervailing controls are the ones measured above: the
   exemption is one path pattern, the two sibling files of the same record unit stay gated, and
   `RetainedReportWhitespaceExemptionTests` (b)/(d) mean a retained report cannot be *edited*
   without failing the digest assertions and the exemption cannot be *removed* without failing the
   mutation test. Nothing about the gap suggests a different mechanism; it is a limit, not a
   defect, and no Finding is raised for it.
