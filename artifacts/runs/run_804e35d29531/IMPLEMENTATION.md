# Worker Result

STATUS: COMPLETE

Phase: IMPLEMENTATION · Iteration 3 (correction) · Task `task_e90dac6734a8` · Dispatch `ctx_1d468af9310d`
Iteration 2: Task `task_674e13c91919` · Dispatch `ctx_9d3dac35dbf3`
Iteration 1: Task `task_85eadd5ee0db` · Dispatch `ctx_2ba7cbd46a1d`
Branch: `agent/final-review-observability-evaluation` (verified with `git branch --show-current`)
Baseline: `1045815` ("Validate Final Adversarial Review effectiveness (#19)")

## Summary / Analysis

PLAN work items **I-0 … I-13 are implemented, committed and green**, and the two blocking review
findings **I-001 and I-002 are corrected** (`9e34320`, `e3c39ff`), as is the re-review's
**I-002-R1** (`d614c89` — see `## Review Feedback Resolution`). The tree validates
(`validate_skills.py`: 463 checks PASSED), the full deterministic suite passes
(`965 tests, OK, skipped=6` — all six skips are the pre-existing opt-in
`test_orca_runtime.py` live-runtime tests, unrelated to OS-22), the release package inputs verify
(106 source files), and `git diff --check` is clean. I-14 (the Draft PR) is deliberately **not**
done — it belongs to the Coordinator after IMPLEMENTATION and TEST both pass — and no `git push`
was run. PLAN's T-1…T-6 formal suite and B-1…B-5 baseline dispatch are the next phase's work and
were not attempted here; the per-component unit tests this Skill's Mandatory Test Gate requires
were written and executed for every I-item (see `## Unit Tests`).

**Dispatch history, stated plainly.** This is a retry of a dispatch that died mid-flight. The
previous IMPLEMENTATION dispatch (`ctx_df3dfb438d2e`, terminal `term_77d0274c…`) authored the
eight commits listed below and then hit an unrecoverable API auth error (403) **after** all eight
had landed but **before** writing this artifact or sending `worker_done` — the Coordinator
recorded that as `unexpected_exit` in `ORCHESTRATOR_LOG.md`. This dispatch therefore did not
re-author work that already existed: it verified the landed tree against DESIGN.md end-to-end,
re-derived the one piece of evidence whose value depends on *when* it was produced (the I-0
neutrality golden — see `## Additional Validation`), executed every test group, exercised the new
CLI surfaces live, and wrote this report. Two deviations from DESIGN.md's literal prose were found
in the landed code; both are the resolution of an internal contradiction inside DESIGN.md itself
and both are documented below rather than left silent.

**Iteration 3 (this dispatch) is a second, narrower correction.** The re-review confirmed I-001
fully resolved and the ten metadata fields properly redacted, and found one remaining channel:
`parse_final_review_report()` assigned the RAW `RESULT:`/`REVIEW_VERDICT:` capture into the parsed
structure *before* validating it and left it there when validation failed, so report-controlled
free text reached `record.json` as `report.parsed.result` outside the covered list — while the
durable guard exempted that field as if it were a validated enum. Fixed in `d614c89`, alone;
I-001's fix and the ten fields' handling are untouched, and nothing else was reopened.

**Iteration 2 was a correction, not a rewrite.** Review finding I-001 rejected the
way deviation 2 resolved the D.5/D.6 manifest contradiction — by exempting `MANIFEST.json` from
`materialize`'s own leak scan — and I-002 found that `record.json` persisted free-form
delivery/runtime metadata without passing it through the D-C redaction pipeline that already
covered `input.md` and `report.md`. Both are fixed, each with tests that fail without the fix;
nothing the Reviewer confirmed (commit hygiene, the existing unit coverage, the neutrality golden,
byte-parity) was rewritten, and no unflagged DESIGN decision was reopened.

**The three §2/§5/§6 load-bearing properties, and what makes each checkable rather than asserted:**

* *Observability neutrality.* The golden `scripts/fixtures/os22_neutrality/pre_os22_task_specs.json`
  is compared as UTF-8 **bytes**, through `canonicalize_task_spec()` (one substitution: the temp
  workspace path) and never through OS-4's whitespace-collapsing `_normalize_artifact()`. Its
  strictness is *proven* by `test_a_whitespace_only_change_fails_the_neutrality_golden`, which
  mutates whitespace only and requires the comparison to fail — and demonstrates in the same body
  that the OS-4 normalizer would have accepted three of the four mutations.
* *Fail-closed provenance.* `PROVENANCE_DEFAULT = unknown` everywhere: the CLI default, the reader
  on a missing/unparseable/unknown-MAJOR record, and the ladder. `voided` always carries one of the
  six enumerated `void_reason`s; two `accepted` records for one attempt are reported as a violation
  rather than silently resolved.
* *Evaluation integrity.* The answer key lives outside `subject/`, is never opened by
  `materialize`, and a mechanical `scan-leak` (ids, archetypes, fixture id, 6-word shingles of
  every `summary`/`negative_space_argument`, a fixed marker set, plus an independent
  expected-count detector) covers `subject/`, the materialized workspace and the retained
  reviewer input. Since I-001, that scanner has **no exclusion parameter at all**: the materialized
  workspace passes the identical, unexempted scan over every one of its files.
* *Secret-safe retained artifacts.* Since I-002, all three retained files pass the same versioned
  policy — `input.md`, `report.md`, and `record.json`'s free-form metadata through one choke point
  — while the identities the record exists to prove are deliberately left intact. Since I-002-R1
  that choke point also covers the record's **report-derived** text (`report.parsed.parse_error`
  and both finding-id lists), and an invalid `RESULT:`/`REVIEW_VERDICT:` capture never becomes a
  persisted field value at all: those two fields are constrained to their enum ∪ `{"", INVALID}`
  *before* persistence, which is what makes them safe unredacted.

## Changes

Eleven commits: the eight of iteration 1, in the order the HARD ordering constraints require,
the two iteration-2 correction commits for I-001 and I-002, and the one iteration-3 correction
commit for I-002-R1. Each is listed with the constraint it satisfies.

| # | hash | message | ordering constraint satisfied |
|---|---|---|---|
| 1 | `e168344` | Capture the pre-OS-22 Task-spec neutrality golden (I-0) | **rule 1** — first commit on the branch, before any product change |
| 2 | `cc9ee8e` | Add the Final Review audit record family to run_logging (I-1..I-5) | **rule 3** — `scripts/run_logging.py` and `orca-worker-reviewer-orchestration/tools/run_logging.py` edited in the same commit; **rule 4** — writer + redaction land before any emission |
| 3 | `78d9287` | Contract the Final Review audit record in SKILL.md and the validator (I-6..I-9) | **rule 2** — §9 + §17 + §16 + `FINAL_REVIEW_CONTRACT`/`FINAL_REVIEW_CONTRACT_MAX_LINES` in **one** commit |
| 4 | `0ea8d60` | Emit a Final Review audit record at both settlement points (I-10) | **rule 4** — after the writer exists; both `run_logging.py` copies again edited together (**rule 3**) |
| 5 | `5d006ed` | Add the Final Review evaluation fixture subject tree | **rule 5** / DEC-7.4 — commit 1 of 2, message names only the fixture and says nothing about what it seeds |
| 6 | `08a9866` | Add the evaluation answer key and the adjudication input contract | **rule 5** / DEC-7.4 — commit 2 of 2, key + adjudication contract separate and later |
| 7 | `aa3a564` | Add the Final Review evaluation scorer (I-12) | **rule 5** — the scorer is written against a fixture that already exists |
| 8 | `2dcca37` | Document the Final Review audit records and evaluation tooling (I-13) | — |
| 9 | `9e34320` | Scan the materialized workspace with no exemption (I-001) | — (correction; touches no `run_logging.py` copy, so **rule 3** does not apply) |
| 10 | `e3c39ff` | Redact record.json's free-form metadata (I-002) | **rule 3** — both `run_logging.py` copies edited in the same commit, byte-parity re-verified |
| 11 | `d614c89` | Keep report-derived text out of record.json (I-002-R1) | **rule 3** — both `run_logging.py` copies edited in the same commit, byte-parity re-verified |

### What each commit contains

**`e168344` — I-0 (DESIGN Step 0, N-1).** New `NEUTRALITY_WORKFLOWS`, `NEUTRALITY_DIRECT_CASES`,
`NEUTRALITY_CANONICALIZATION`, `_TASK_SPEC_NONDETERMINISM_TRIPWIRES`, `canonicalize_task_spec()`
and `capture_neutrality_task_specs()` in `scripts/test_e2e_harness.py` — **new** functions, not an
extension of `capture_legacy_artifacts()`, and never calling `_normalize_artifact()` on a Task
spec. `_normalize_artifact()` itself is byte-for-byte unmodified, so OS-4's
`LegacyByteIdentityTests` keeps its exact input. The golden covers both skills × every workflow ×
`profile=none|multi` (family A) and an enumerated role × phase × iteration × optional-block matrix
including `final_reviewer`/`final_review` (family B) — `profile=multi` is what makes a
`final_review` spec appear at all, so without it the §2 claim would not cover the dispatch OS-22
observes. `scripts/fixtures/os22_neutrality/README.md` records the generation command, the
byte-strictness rationale and the regeneration rule (regenerating to make the test pass destroys
the evidence).

**`cc9ee8e` — I-1…I-5 (D-A, D-B, D-C, D-F).** In both `run_logging.py` copies, stdlib only, zero
`scripts/` imports:
* I-1: `FINAL_REVIEW_AUDIT_SCHEMA_VERSION = "1.0"`, `FINAL_REVIEW_AUDIT_DIRNAME`,
  `FINAL_REVIEW_AUDIT_STAGING_DIRNAME = ".staging"`, `PROVENANCE_STATES`, `VOID_REASONS` (six),
  `SETTLEMENT_STATES`, `FinalReviewAuditError`/`FinalReviewAuditCollision`/`FinalReviewAuditWriteFailed`,
  `sha256_text()`, `sha256_bytes()`, `final_review_audit_dir()`, `final_review_dispatch_key()`,
  `_stage_and_publish_audit_record()` (A.3 P1–P3: `os.mkdir` an exclusive
  `.staging/<key>.<pid>-<nonce>/`, write + fsync all three files, publish with **one**
  `os.rename()`), `sweep_final_review_audit_staging()` (A.3 P6),
  `write_final_review_audit_record()`, `read_final_review_audit_record()`,
  `read_final_review_attempt_provenance()`.
* I-2: `FINAL_REVIEW_REDACTION_POLICY_VERSION = "redaction/1.0"`, `REDACTION_CATEGORIES` as the
  ordered 4-tuple `orca_dispatch_capability` → `url_credential` → `env_secret_pattern` →
  `absolute_local_path` with D-C's exact patterns and replacement tokens, `redact_text()`.
* I-3: `capture_stored_task_spec()`, `capture_delivery_evidence()`, `CAPTURE_TIMEOUT_SECONDS = 30`,
  `_orca_command()`.
* I-4: the new `--event` spellings, added to the vocabulary with **no new column**.
* I-5: `FINAL_REVIEW_EXPORT_SCHEMA_VERSION`, `export_final_review_evidence()`, and the three CLI
  subcommands `final-review-audit-write` / `-provenance` / `-export`, with `--provenance`
  defaulting to `unknown`.

**`78d9287` — I-6…I-9 (one commit, HARD).** `SKILL.md` §9 gains
`#### Final Review audit artifacts (OS-22)`: the path rule, A.3's reader rule (a published
`<dispatch_key>/` is a complete record, `.staging/` is never a record and is never parsed,
digested, exported, counted or used to answer provenance), the schema-version + reader
compatibility rule, D-B's provenance enum with the fail-closed default, D-C's secret-safe
requirement, F.4's three authorities including the `run_end`-is-not-terminal reader rule, F.3's
retention/commit policy, and the three CLI call points. §17's input and report paragraphs gain the
audit obligation and the "preservation is a post-dispatch read, there is no hook in the spec
assembly path" statement; the contract block gains
`FINAL_REVIEW_AUDIT_RECORD = artifact_root_final_review_audit_per_dispatch` and
`FINAL_REVIEW_PROVENANCE_DEFAULT = unknown`. §16 step 8 moves from `artifacts/FINAL_REVIEW_*` to
`<ARTIFACT_ROOT>FINAL_REVIEW*` and `## Final Adversarial Review` gains the per-dispatch
`FINAL_REVIEW_AUDIT:` citation plus the no-unsupported-claim rule; the four-axis
`## Orca Orchestration State` ledger is **not** trimmed (DEC-5). `validate_skills.py` gains the two
contract keys, `FINAL_REVIEW_CONTRACT_MAX_LINES 15 → 17`, and
`validate_final_review_audit_contract()` (registered in the run list at `:2151`), which cross-checks
the prose against `run_logging.FINAL_REVIEW_AUDIT_SCHEMA_VERSION` and
`FINAL_REVIEW_REDACTION_POLICY_VERSION` so the two statements of each value cannot drift.

**`0ea8d60` — I-10 (D-B, Step 3).** `OrcaRuntimeHarness._log_final_review_audit()` runs at the
final-review settlement path **after** four-axis finalization and **before** the verdict branch,
entirely through `_safe_log` — a collision, an `OSError` at any staging boundary, or an
unavailable capture becomes one log row and the run continues; an audit-write failure never
mutates settled lifecycle state. `scripts/e2e_harness.py` exercises the identical path, so the
deterministic harness covers it. Shared helpers `probe_final_review_report()` and
`resolve_final_review_provenance()` live in `run_logging.py` (both copies).

**`5d006ed` / `08a9866` — I-11 (D-D, two commits, HARD).** `subject/base` and `subject/head` — a
~230-line record-publication library (config ladder, retention tiers, quota, validation, three
publication entry points) plus `CONTRACT.md` and a green test suite — with five defects introduced
*by the feature work* and no marker, id, annotation or reference to `key/`. The key and the
adjudication authoring contract land separately and later.

**`aa3a564` — I-12 (D-E).** `scripts/final_review_eval.py`, stdlib only, five subcommands:
`materialize`, `verify-fixture`, `scan-leak`, `parse-report`, `score`. `parse-report` and `score`
are separate so the normalized findings JSON is auditable before any metric exists;
`--provenance-out` is the only place `score` reads a clock and it writes a sidecar that is never
merged into the metrics document; an unknown key inside an adjudication verdict is a hard exit 2,
which makes §6's forbidden historical-corpus inference unrepresentable rather than discouraged.
This commit also rewords two `answer_key.json` strings whose 6-word shingles collided with real
`subject/` text and so tripped the scanner's own leak check — a scanner fix, not a content change.

**`2dcca37` — I-13.** `README.md` "Run-Scoped Artifacts and Logs" gains the `final_review_audit/`
row and the bundle; `CHANGELOG.md` and `COMPATIBILITY.md` record the additive surface. `VERSION`
and `LICENSE-DECISION.md` are untouched (verified against the full `1045815..HEAD` name list).

**`9e34320` — I-001 (correction).** `scan_leak()` loses its `exclude_names` parameter entirely;
new `workspace_fixture_ref()` / `WORKSPACE_FIXTURE_REF_FORM`; `materialize()` writes
`fixture_id: workspace_fixture_ref(_fixture_id(fixture))` plus `fixture_id_form`, and calls
`scan_leak(key, [staging])` with no exclusion. `MaterializeTests` gains
`test_the_manifest_names_the_fixture_opaquely` and
`test_the_scanner_takes_no_exclusion_argument`, and
`test_the_workspace_the_reviewer_reads_is_clean` now scans the complete workspace with no
exclusion and first asserts that `MANIFEST.json`, `CONTRACT.md` and `DIFF.patch` are among the
files it scanned.

**`e3c39ff` — I-002 (correction).** New `FINAL_REVIEW_REDACTED_METADATA_FIELDS` (a closed, dotted
tuple of ten field paths) and `_redact_record_metadata()`, called as the last step of record
assembly so no free-form string reaches `json.dumps()` — or the evidence bundle, which inlines the
record verbatim — unredacted. New `metadata_redaction` block on the record
(`redaction_policy_version`, `covered_fields`, `redactions`). Both `run_logging.py` copies in the
same commit. `SKILL.md` §9's *secret-safe* paragraph states the rule and names both the covered
free-form set and the never-redacted identity set. `scripts/test_run_logging.py` gains
`RecordMetadataRedactionTests` (8).

### Deviations from DESIGN.md

1. **Five audit `--event` spellings, not three.** Step 1's I-4 row says "the three new `--event`
   spellings", but D-A §B.4 — as **corrected** under review finding D-003 — tabulates five and
   §2340's Review Feedback Resolution explicitly states "two new events:
   `final_review_audit_write_failed` and `final_review_audit_incomplete_publication`". The
   normative corrected table was followed; Step 1's count is stale prose from the pre-correction
   revision. All five are in the vocabulary with no column added, as I-4 requires.
2. **`materialize`'s workspace names its fixture by a digest, not by the `fixture_id` literal.**
   D.5's manifest schema requires `fixture_id` in the workspace `MANIFEST.json`, D.6 lists the
   `fixture_id` literal as a leak token, and D.5 rule 4 requires the scan over **every** file in
   `<dir>` to return zero hits — the three cannot all hold as literally written. Iteration 1
   resolved this by exempting `MANIFEST.json` from `materialize`'s own gate; review finding I-001
   rejected that, correctly: a scanner that can be told to skip reviewer-visible content proves
   nothing about the content it skipped. The resolution is now on the **value** side instead. The
   manifest keeps its `fixture_id` key, so D.5's shape is unchanged; its value is
   `workspace_fixture_ref(fixture_id)` — `"sha256:" + sha256(fixture_id)` — with a sibling
   `fixture_id_form: "sha256-of-fixture-id"` saying so in the file itself. The real id stays in
   `key/answer_key.json` and in `verify-fixture`, where the reviewer never looks. D.6's literal
   check therefore does not fire, D.5 rule 4 holds with **no exemption anywhere**, and
   `scan_leak()` no longer accepts an exclusion parameter at all. See `## Review Feedback
   Resolution` for the reproduction.

Deviation 1 changes no field name, path, schema or default. Deviation 2 changes one field's
**value form** inside the materialized workspace only (`MANIFEST.json.fixture_id`) and adds one
sibling field that documents it; the key name, the file, `fixture_digest`, and every consumer are
unchanged — `score(workspace=...)` reads `fixture_digest`, and nothing anywhere read `fixture_id`
out of a workspace. Both deviations are recorded here rather than resolved unilaterally.

## Modified Files

New (23):

```text
scripts/final_review_eval.py
scripts/test_final_review_eval.py
scripts/fixtures/os22_neutrality/pre_os22_task_specs.json
scripts/fixtures/os22_neutrality/README.md
scripts/fixtures/final_review_eval/README.md
scripts/fixtures/final_review_eval/subject/base/**            (8 files)
scripts/fixtures/final_review_eval/subject/head/**            (8 files)
scripts/fixtures/final_review_eval/key/answer_key.json
scripts/fixtures/final_review_eval/adjudications/README.md
```

Edited (10):

```text
scripts/run_logging.py                                        (+1534)
orca-worker-reviewer-orchestration/tools/run_logging.py       (+1534, byte-parity twin)
orca-worker-reviewer-orchestration/SKILL.md                   (sections 9, 16, 17)
scripts/validate_skills.py
scripts/orca_runtime_harness.py
scripts/e2e_harness.py
scripts/test_run_logging.py
scripts/test_e2e_harness.py
scripts/test_orca_runtime_contract.py
scripts/test_validate_skills.py
README.md, CHANGELOG.md, COMPATIBILITY.md
```

The iteration-2 correction edited five of these files and added no file:
`scripts/final_review_eval.py` + `scripts/test_final_review_eval.py` (I-001) and both
`run_logging.py` copies + `orca-worker-reviewer-orchestration/SKILL.md` +
`scripts/test_run_logging.py` (I-002).

This is exactly DESIGN.md's "Files summary" list, plus `test_orca_runtime_contract.py` and
`test_validate_skills.py` — the two test modules this Skill's Mandatory Test Gate requires for the
I-10 emission points and the I-9 validator. `VERSION` and `LICENSE-DECISION.md`: untouched.
`git status --porcelain` shows no modified or staged tracked files; the working tree is clean.

## Unit Tests

### Added / Modified Tests

| module | classes | tests | I-items covered |
|---|---|---|---|
| `scripts/test_run_logging.py` | `FinalReviewDispatchKeyTests` (5), `AuditRecordWriteTests` (9), `AuditProvenanceTests` (8), `AuditReaderCompatibilityTests` (6), `AuditWriteBoundaryFaultTests` (5), `RedactionPolicyTests` (11), `RetainedArtifactSecurityTests` (5), **`RecordMetadataRedactionTests` (8, new — I-002)**, `AuditCaptureFailureTests` (4), `EvidenceBundleTests` (5), `AuditCliTests` (5), `ProvenanceLadderTests` (6) | **77** | I-1 … I-5, I-10, I-002 |
| `scripts/test_e2e_harness.py` | `FinalReviewObservabilityNeutralityTests` (12), `DeterministicFinalReviewAuditTests` (4) | **16** | I-0, I-10 |
| `scripts/test_orca_runtime_contract.py` | `FinalReviewAuditEmissionTests` (8) | **8** | I-10 |
| `scripts/test_validate_skills.py` | 12 new cases in the existing contract classes | **12** | I-6 … I-9 |
| `scripts/test_final_review_eval.py` | `FixtureIntegrityTests`, `LeakScanTests`, **`MaterializeTests` (9, +2 for I-001)**, `MatchingTests`, `PrecisionRefusalTests`, `AdjudicationContractTests`, `DeterminismTests`, `ExitCodeTests`, `NoTargetCountTests` | **56** | I-11, I-12, I-001 |

### Behavior Covered

* **I-0 / neutrality.** Every workflow spec and every direct spec is byte-identical to the
  pre-OS-22 capture; the golden records its own provenance and its `captured_from_commit` is
  checked against `git rev-parse --short 1045815`; a whitespace-only mutation to a worker, a
  reviewer and a `final_reviewer` spec must **fail** the real comparison helper (and the test shows
  `_normalize_artifact()` would have accepted three of them); `render_task_spec()` gained no
  parameter; no captured spec gained a terminal newline; the audit module is structurally
  unreachable from the dispatch path; the tripwired names all still exist; the OS-4 legacy fixture
  and normalizer are untouched.
* **I-1 / writer.** `dispatch_key` derivation and its fail-closed component validation; publication
  is a single rename of a fully-fsynced staging directory; a published record is never overwritten
  and a second write for the same key raises `FinalReviewAuditCollision` leaving the published
  record intact.
* **I-1 / A.3 fault injection.** An `OSError` is injected at **every** write boundary — the staging
  `mkdir`, each of the three `open("x")`, each `write`, each `fsync`, and the publishing `rename` —
  and at each point the suite asserts (a) no `final_review_audit/<key>/` exists, (b) provenance
  never reads `accepted` for it, (c) exactly one `final_review_audit_write_failed` row and no
  lifecycle mutation, and (d) **a later write for the same dispatch key still succeeds**. (d) is
  the regression guard: the pre-D-003 precheck-then-sequential-create protocol fails it at three
  of those points.
* **I-1 / abandoned staging.** An abandoned `.staging/` entry is never counted, never parsed, never
  read as a record; it is retained as the sole surviving evidence when no published record exists
  for its key, reported once as `final_review_audit_incomplete_publication` with its
  `files_present` list, surfaced in the bundle's `integrity.incomplete_publications`, and swept
  only once a published record for that key exists.
* **I-2 / redaction.** Each of the four categories, in order — including a `dcap_` token inside an
  environment assignment being attributed to `orca_dispatch_capability`, not `env_secret_pattern`;
  an already-placeheld `/Users/<name>/` not being double-redacted; and the record carrying only
  pre-digest, policy version, post-digest and per-category counts, never the replaced value or its
  offset.
* **I-3 / capture failure.** `orca` absent from `PATH`, a non-zero exit, a timeout, unparseable
  JSON and a task id missing from the result each yield
  `capture_status = "unavailable"` with a non-empty `capture_error`, a record that is **still**
  written with null digests, and one `final_review_audit_incomplete` row.
* **I-4 / I-5.** The new event spellings add no column; the export bundle's schema, its
  `integrity` block and its regenerability; the three CLI subcommands including
  `--provenance`'s `unknown` default and the exit codes.
* **I-6 … I-9 / contract.** Dropping the §9 subsection, drifting the schema version or the
  redaction policy version from `run_logging.py`, dropping the `.staging/`-is-never-a-record rule,
  dropping the `run_end`-is-not-terminal rule, dropping a CLI call point, reverting §16 to the
  stale `artifacts/FINAL_REVIEW_` path, losing the `FINAL_REVIEW_AUDIT:` citation, losing the
  four-axis ledger, dropping either new contract key, or flipping the provenance default to
  `accepted` — each must **fail** validation.
* **I-10 / emission.** Both harnesses write exactly one record per final-review dispatch, at the
  correct point (after four-axis finalization, before the verdict branch); a dispatch-layer failure
  with no report still produces a record whose provenance is `voided/report_missing`; an
  audit-write failure produces a log row and mutates no settled lifecycle state.
* **I-11 / I-12 / evaluation.** Fixture digest integrity; the leak scan over `subject/` returning
  zero hits; `materialize` refusing a non-empty destination, creating no `.git`, never opening
  `key/`, and failing closed on a digest mismatch with no `--update-digest` escape; deterministic
  one-to-one matching with location tolerance and symbol hits; refusal to report precision without
  adjudications; rejection of any unknown key in an adjudication verdict; byte-identical reruns
  (the unqualified B5 assertion) and a patched-clock test proving no timestamp reaches the metrics
  document; the documented exit-code ladder; and the absence of any target-count field.

### Execution

```text
Command: python3 scripts/validate_skills.py
Result:  PASS — "Skill validation PASSED (463 checks)"

Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  PASS — "Ran 965 tests in 60.095s / OK (skipped=6)" (re-run after the I-002-R1 correction)
         (the 6 skips are test_orca_runtime.py's opt-in live-runtime tests:
          "requires --orca-runtime and a ready Orca runtime"; pre-existing, not OS-22)

Command: python3 -m unittest scripts.test_run_logging
Result:  PASS — Ran 142 tests, OK

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
Result:  PASS — Ran 81 tests, OK        (I-1 … I-5, I-10, I-002, I-002-R1)

Command: python3 -m unittest scripts.test_e2e_harness
Result:  PASS — Ran 165 tests, OK

Command: python3 -m unittest scripts.test_e2e_harness.FinalReviewObservabilityNeutralityTests \
           scripts.test_e2e_harness.DeterministicFinalReviewAuditTests
Result:  PASS — Ran 16 tests, OK        (I-0, I-10)

Command: python3 -m unittest scripts.test_orca_runtime_contract.FinalReviewAuditEmissionTests
Result:  PASS — Ran 8 tests, OK         (I-10)

Command: python3 -m unittest scripts.test_validate_skills
Result:  PASS — Ran 120 tests, OK       (I-6 … I-9; 12 new cases)

Command: python3 -m unittest scripts.test_final_review_eval
Result:  PASS — Ran 56 tests, OK        (I-11, I-12, I-001)

Command: python3 -m unittest scripts.test_run_logging.RecordMetadataRedactionTests
Result:  PASS — Ran 8 tests, OK         (I-002)

Command: python3 -m unittest scripts.test_final_review_eval.MaterializeTests
Result:  PASS — Ran 9 tests, OK         (I-001)

Command: python3 -m unittest scripts.test_orca_runtime_contract
Result:  PASS — Ran 223 tests, OK
```

No test failures. No test was skipped, xfailed or weakened to make this report green.

## Additional Validation

**The I-0 ordering claim, re-derived rather than trusted.** The whole value of the neutrality
golden depends on it having been produced from pre-OS-22 code, and the commit that carries it is
first on the branch — but a commit's position is a weaker claim than the bytes themselves. I
therefore reproduced it independently: extracted `git archive 1045815` into a clean scratch
checkout, copied only `scripts/test_e2e_harness.py` in (the capture function is new in OS-22, per
the fixture README's documented technique), ran `capture_neutrality_task_specs(Path('.'))` there,
and compared:

```text
Command: cmp <regenerated>/regen.json scripts/fixtures/os22_neutrality/pre_os22_task_specs.json
Result:  PASS — byte-identical
         (and: git rev-parse --short 1045815 == the fixture's captured_from_commit == 1045815)
```

The committed golden is exactly what the pre-OS-22 tree produces, independently of commit order.

**Byte-parity of the two `run_logging.py` copies (ordering rule 3, enforced by
`validate_skills.py`):**

```text
Command: diff scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result:  PASS — no differences
```

**Live CLI exercise of the new surfaces** (scratch base, `--no-capture`, so the `orca` reads are
skipped):

```text
final-review-audit-write   → exit 0; published
    artifacts/runs/run_demo0001/final_review_audit/attempt1__task_aaaa1111__ctx_bbbb2222/
    {record.json,input.md,report.md}; record.json's first key is schema_version and the
    key order matches D-A A.4; one final_review_audit_incomplete row in ORCHESTRATOR_LOG.md
    (correct — --no-capture means the input could not be captured)
final-review-audit-provenance → exit 0; accepted_dispatch_key resolved, violations []
final-review-audit-export     → exit 0; FINAL_REVIEW_EVIDENCE_BUNDLE.json written with
    integrity {records_found: 1, records_ok: 1, digest_mismatches: [], unreadable: [],
    missing_artifacts: [], incomplete_publications: []}
```

**Live exercise of the evaluation tooling:**

```text
final_review_eval.py verify-fixture  → exit 0, "fixture verification PASSED"
final_review_eval.py materialize     → exit 0, 14 files, fixture_digest
                                       sha256:b63f5a9f…9cf70f1d, no .git, no key/
final_review_eval.py scan-leak --target <workspace>
                                     → exit 0, "leak scan PASSED" — the same scanner, no
                                       exclusion, over the complete workspace including
                                       MANIFEST.json (re-run after the I-001 correction; it
                                       returned exit 4 on one manifest hit before)
```

**Remaining CI parity checks:**

```text
Command: python3 scripts/verify_package.py
Result:  PASS — "Package verification PASSED (106 source files)"

Command: git diff --check
Result:  PASS — clean

Command: git diff --name-only 1045815..HEAD | grep -E '^(VERSION|LICENSE-DECISION.md)$'
Result:  PASS — no match (both untouched, as required)
```

**Scope discipline.** Nothing the ticket excludes was implemented: no OS-23 detection or
search-quality work, no Final Review falsification/search-depth policy, no reviewer or model
optimization, no H-1/H-2/H-4/H-5 conclusion, no unrelated lifecycle change. I-14 (Draft PR) was not
created and `git push` was not run — both belong to the Coordinator after the TEST phase. PLAN's
T-1…T-6 formal suite and the B-1…B-5 live baseline dispatch were not attempted, as instructed.

## Review Feedback Resolution

### Iteration 3 (this correction) — I-002-R1, MAJOR / blocking

| finding | verdict | commit |
|---|---|---|
| I-002-R1 — invalid report enum text was copied into `record.json` outside the redaction pipeline | RESOLVED | `d614c89` |

**I-002-R1 — report-derived text reaching `record.json` unredacted.**

*The defect, precisely.* `parse_final_review_report()` assigned the capture to the field first and
validated it second — `parsed["result"] = result.group(1)` and only then the membership test — so
on the invalid path the raw `RESULT:` text stayed in the field the writer persists, while
`parse_status`/`parse_error` merely *reported* that it was invalid. `report.parsed.result` was not
in `FINAL_REVIEW_REDACTED_METADATA_FIELDS`, and the durable guard listed it under the "validated
enum" exemption — an exemption that is true on the valid path and false on exactly the path that
matters. `REVIEW_VERDICT:` had the identical shape, `parse_error` quoted the offending capture into
a field that was likewise exempt-by-name, and finding ids were `\S+` of report-controlled text
retained verbatim. Reproduced before the fix through the real writer: a report whose `RESULT:` line
carried `dcap_AAAAAAAAAAAAAAAAAAAA`, `ORCA_TOKEN=topsecretvalue` and `/Users/<name>/private/repo`
left all three in the persisted `record.json`.

*What changed, and where.* The report is the one input the writer does not control, so parser
output is now treated as such rather than trusted as an enum. Both `scripts/run_logging.py` and
`orca-worker-reviewer-orchestration/tools/run_logging.py`, in the one commit (byte-parity
re-verified with `cmp`):

* `parse_final_review_report()` — the capture goes into a **local**, and the field is assigned only
  after it passes. On failure the field takes the fixed sentinel `PARSED_ENUM_INVALID = "INVALID"`,
  so `result` and `review_verdict` can only ever hold their enum, `""`, or that sentinel. This is
  option (a) of the correction instruction, chosen after reading the code because it is how
  `parse_status`/`parse_error` already divide the work here: the *fields* carry contract values, and
  the *error field* carries the diagnosis. The valid path is untouched — a `PASS WITH NOTES` is
  still carried through verbatim and is still not collapsed into `PASS`.
* `parse_error` still quotes the offending capture (removing it would destroy the diagnosis), and is
  therefore **added to the covered list** so it passes the same policy as the other free-form
  fields.
* finding ids — `_finding_id()` applies `_SAFE_FINDING_ID = ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and
  substitutes `PARSED_FINDING_ID_INVALID = "INVALID_ID"` for anything else, so a `/Users/<name>/…`
  path or a `TOKEN=…` value cannot be retained at all and the two lists keep their honest lengths.
  Because a credential *can* be id-shaped (`dcap_…` passes any reasonable ID pattern), both lists
  are **also added to the covered list** — the shape check and redaction are layered, not
  alternatives.
* `_redact_record_metadata()` — one small generalization: a covered field is now a string **or a
  list of strings**, via a local `substitute()` that both lists and scalars share, so the counting
  and the C.4 output shape stay identical. Everything else about the choke point, its call site
  (still last, after every field is in place) and its aggregate output is unchanged.

`FINAL_REVIEW_REDACTED_METADATA_FIELDS` therefore grows from ten paths to thirteen —
`report.parsed.parse_error`, `report.parsed.blocking_finding_ids`,
`report.parsed.non_blocking_finding_ids` — and `metadata_redaction.covered_fields` says so in every
record it writes. `FINAL_REVIEW_REDACTION_POLICY_VERSION` stays `redaction/1.0`: no category,
pattern or replacement changed, and the record states its own covered set, so a reader is never
left inferring coverage from the version.

*The guard test no longer exempts a field by naming it.* `report.parsed.result` and
`report.parsed.review_verdict` moved out of `exempt` into a new `constrained` map that declares the
closed set each may hold, and the test **asserts the persisted value is inside it** — an exemption
that has to be earned against the bytes on disk rather than asserted in a list. `parse_error` and
the id lists are gone from `exempt` entirely; they are covered. A list leaf (`…ids.[]`) resolves to
its containing field, so a new list of report text cannot slip in unnoticed either.

*Writer-level regressions, all through the real writer and against the bytes on disk.* The shared
poisoned fixture now writes a poisoned **report** as well, so every test in the class exercises this
route:

| test | injects | asserts |
|---|---|---|
| `test_a_malformed_result_never_reaches_the_record_as_raw_text` | `RESULT: <dcap_ + ORCA_TOKEN= + /Users/<name>/…>` | `result == "INVALID"`, `parse_status == "malformed"`, all three categories substituted **inside `parse_error`**, and none of the three values anywhere in `record.json` |
| `test_a_malformed_review_verdict_never_reaches_the_record_raw` | the same through `REVIEW_VERDICT:` | `review_verdict == "INVALID"`, `result == "PASS"` still, same byte assertions |
| `test_report_controlled_finding_ids_are_constrained_and_redacted` | three hostile `ID:` tokens — one id-shaped credential, one `TOKEN=…`, one `/Users/<name>/…` | the id-shaped one is **redacted**, the two non-id-shaped ones become `INVALID_ID`, list lengths preserved, no secret in the bytes |
| `test_a_well_formed_report_keeps_its_ids_and_its_enums` | `FAILING_REPORT` | `FAIL`/`FAIL`/`["R1"]`/`["R2"]` and an empty `parse_error` — over-redaction is the other failure |

Each fails without the fix: reverting `run_logging.py` alone puts `dcap_AAAAAAAAAAAAAAAAAAAA` back
into `record.json` and the byte assertions fire.

*Documentation.* SKILL.md §9 gains the rule in the same commit: the covered list now names the two
report-derived entries, and a new paragraph states why `result`/`review_verdict` are safe to persist
unredacted — *because they are constrained to a closed set before persistence*, not because they are
called enums — and how finding ids are constrained and then redacted.

*Scope.* I-001's fix, the ten previously-redacted metadata fields, the identity non-redaction rule,
`redaction/1.0` itself, and every other previously-approved part of the implementation are
untouched. Full suite re-run after the change: `validate_skills.py` 463 checks PASSED,
`unittest discover` 965 tests OK (6 pre-existing skips), `verify_package.py` 106 source files
PASSED, `cmp` byte-parity clean, `final_review_eval.py verify-fixture` PASSED.

### Iteration 2 — I-001 and I-002, both MAJOR / blocking

| finding | verdict | commit |
|---|---|---|
| I-001 — the materialized Reviewer workspace carried a leak token that `materialize` exempted from its own scan | RESOLVED | `9e34320` |
| I-002 — `record.json` persisted free-form delivery/runtime metadata without redaction | RESOLVED | `e3c39ff` |

**I-001 — fixture-identity leak in the materialized workspace.**

*What changed, and where.* The exemption is gone rather than justified.
`scripts/final_review_eval.py`:

* `scan_leak(key, targets)` — the `exclude_names` parameter is **removed from the signature**, not
  merely unused, and its docstring now states why: a scanner that can be told to skip
  reviewer-visible content proves nothing about the content it skipped.
* new `WORKSPACE_FIXTURE_REF_FORM` and `workspace_fixture_ref(fixture_id)` →
  `"sha256:" + sha256_text(fixture_id)`, one-way and deterministic.
* `materialize()` writes `"fixture_id": workspace_fixture_ref(_fixture_id(fixture))` and
  `"fixture_id_form": WORKSPACE_FIXTURE_REF_FORM` into the workspace manifest, and calls
  `scan_leak(key, [staging])` — **no exclusion**.

*Why this branch of the two the correction offered.* Removing `MANIFEST.json` from the workspace
would have broken D.5's stated output layout **and** `score(workspace=…)`, which reads
`<workspace>/MANIFEST.json` to compare `fixture_digest` against the key's. Tokenizing the value
keeps the file, the key name, the digest comparison and every consumer intact, and moves exactly
one literal. The real `fixture_id` is untouched in `key/answer_key.json` and in `verify-fixture`.

*Tests covering it* (`scripts/test_final_review_eval.py`, `MaterializeTests`):

* `test_the_workspace_the_reviewer_reads_is_clean` — the **required** test: it enumerates every
  file in the materialized workspace, asserts `MANIFEST.json`, `CONTRACT.md` and `DIFF.patch` are
  among them, then asserts `scan_leak(key, [destination]) == []` with no exclusion argument.
* `test_the_manifest_names_the_fixture_opaquely` — the manifest's raw bytes contain no
  `fixture_id` literal, and its `fixture_id` / `fixture_id_form` are the digest form.
* `test_the_scanner_takes_no_exclusion_argument` — `inspect.signature(scan_leak)` has no
  `exclude_names`, so the exemption cannot be reintroduced silently.

*Reproduction, the same one the finding used:*

```text
Command: python3 scripts/final_review_eval.py materialize --dest <ws>
Result:  exit 0 — 14 files, fixture_digest sha256:b63f5a9f…9cf70f1d

Command: python3 scripts/final_review_eval.py scan-leak \
           --key scripts/fixtures/final_review_eval/key/answer_key.json --target <ws>
Result:  exit 0 — "leak scan PASSED"      (was: exit 4, MANIFEST.json / final_review_eval/v1)

<ws>/MANIFEST.json:
  "fixture_id": "sha256:74716459a19bd48927e29c08f067d0fb29920bff01dd34ac4f83d9d2ef1585da",
  "fixture_id_form": "sha256-of-fixture-id",
  "fixture_digest": "sha256:b63f5a9f…9cf70f1d",
```

**I-002 — unredacted `record.json` metadata.**

*What changed, and where.* `scripts/run_logging.py` and its byte-identical twin
`orca-worker-reviewer-orchestration/tools/run_logging.py`, in one commit:

* new `FINAL_REVIEW_REDACTED_METADATA_FIELDS` — a **closed, dotted tuple**:
  `reviewer_agent_command`, `reviewer_agent_origin`, `failure_detail`, `notes`,
  `stored_task_spec.capture_error`, `report.capture_error`,
  `delivery_evidence.capture_error`, `delivery_evidence.process_incarnation`,
  `delivery_evidence.last_failure`, `delivery_evidence.termination_reason`. Its comment states
  what is deliberately absent and why, so the identity/free-form line is visible at the constant
  rather than buried in an assembly expression.
* new `_redact_record_metadata(record)` — routes each covered field through the **existing**
  `redact_text()` (D-C, `redaction/1.0`, unchanged) in place, and returns aggregated
  `[{category, count}]` in C.3 policy order, in C.4's shape: an entry only for a category that
  matched, no offsets, no per-occurrence digest, never the removed value. Counts are aggregated
  across fields rather than per field, because a per-field breakdown localizes which field held
  the secret.
* it is called **last** in `write_final_review_audit_record()`, after every field is in place and
  before `json.dumps()` — one choke point, which also covers the export bundle (it inlines the
  record verbatim) and the `ORCHESTRATOR_LOG` `error=` cell (it reads the same, now-redacted
  `capture_error` strings).
* new record key `metadata_redaction` = `{redaction_policy_version, covered_fields, redactions}`,
  which is the redaction-occurrence metadata for these fields.
* `SKILL.md` §9's *secret-safe* paragraph states the same rule, naming both the covered free-form
  set and the never-redacted identity set.

*Identities preserved, deliberately.* `run_id`, `task_id`, `dispatch_id`, `dispatch_key`,
`reviewer_terminal` (§1 requires it by name), `delivery_evidence.assignee_handle`, the
already-hashed `capability_hash`, and the validated enums (`provenance_state`, `void_reason`,
`settlement_state`, `input_altered_across_retry`) are **not** redacted — over-redaction destroys
the evidence the record exists to preserve. `report.contract_path` is already
relativized-or-redacted at construction by `_relative_artifact_path()`, so it is not redacted
twice and not double-counted.

*Schema version.* `FINAL_REVIEW_AUDIT_SCHEMA_VERSION` stays `1.0`. v1.0 is introduced by this very
branch and has never shipped, so there is no reader anywhere that saw a 1.0 record without
`metadata_redaction`; bumping would only desynchronize the validator anchors and SKILL.md prose
for a version no one has consumed. The additive-field-is-MINOR rule remains stated in §9 for
records that *have* shipped.

*Tests covering it* (`scripts/test_run_logging.py`, `RecordMetadataRedactionTests`, 8 tests). Each
poisons its route with all three of a `dcap_` capability, an `ORCA_TOKEN=` value and a
`/Users/<name>/private/repo` path:

| test | route(s) covered |
|---|---|
| `test_no_credential_and_no_home_path_survives_into_record_json` | the raw bytes on disk: none of the three survives; all three placeholders present |
| `test_every_injection_route_is_redacted_field_by_field` | one subTest per route the finding names — agent command, agent origin, failure detail, notes, `stored_task_spec.capture_error`, and delivery evidence's `process_incarnation` / `last_failure` / `termination_reason` — so a partial fix fails |
| `test_the_identities_the_record_exists_to_prove_are_not_redacted` | over-redaction: terminal, ids, `assignee_handle`, `capability_hash`, enums survive verbatim |
| `test_the_record_states_what_was_covered_and_what_matched` | `metadata_redaction`: policy version, covered field list, C.4 shape and policy order, no extra keys |
| `test_a_clean_record_records_no_substitution` | `redactions == []` unambiguously means nothing was substituted |
| `test_the_report_capture_error_route_is_covered` | `report.capture_error` directly through the choke point |
| `test_the_choke_point_skips_absent_and_non_string_values` | a missing / `None` / empty field is left exactly as the writer left it |
| `test_no_free_form_string_field_escapes_the_covered_list` | **durable guard**: every string leaf of a real written record must be either covered or in an explicit identity/enum allowlist, so a future free-form field cannot be added silently |

*Reproduction, the same one the finding used* (`process_incarnation='pid:7:/Users/<name>/private/repo'`,
`last_failure='TOKEN=topsecret /Users/<name>/private/repo'`), now in the persisted `record.json`:

```text
"process_incarnation": "pid:7:/Users/<REDACTED:absolute_local_path>/private/repo",
"last_failure":        "TOKEN=<REDACTED:env_secret_pattern> /Users/<REDACTED:absolute_local_path>/private/repo",
"assignee_handle":     "term_assignee"          <- identity, untouched
"capability_hash":     "sha256:0123456789abcdef" <- already a hash, untouched
"metadata_redaction":  {"redaction_policy_version": "redaction/1.0",
                        "covered_fields": [...10 paths; 13 since I-002-R1...],
                        "redactions": [{"category": "orca_dispatch_capability", "count": 1},
                                       {"category": "env_secret_pattern",       "count": 2},
                                       {"category": "absolute_local_path",      "count": 8}]}
```

*Scope.* Nothing the Reviewer confirmed was rewritten: commit hygiene, unit coverage, the
neutrality golden, and the byte-parity rule are untouched, and no DESIGN decision outside I-001's
stated D.5/D.6 conflict was reopened.

### Iteration 1

IMPLEMENTATION had not been reviewed yet, and `relevant_previous_findings` was `none`.

The DESIGN-phase findings this implementation is built on top of are carried through as follows,
for the reviewer's convenience:

| finding | status in the code |
|---|---|
| D-001 — the neutrality golden was not a byte-identity test | RESOLVED — `canonicalize_task_spec()` performs exactly one substitution and `_normalize_artifact()` is never applied to a Task spec; strictness is proven by `test_a_whitespace_only_change_fails_the_neutrality_golden`, not asserted |
| D-002 — clock-derived `generated_at` broke B5's byte-identity | RESOLVED — `score` reads a clock only under `--provenance-out`, into a sidecar; a patched-clock test asserts no timestamp reaches the metrics document, and the rerun assertion is unqualified |
| D-003 — the three-file writer could permanently orphan a dispatch | RESOLVED — A.3's stage-and-one-rename protocol, with the fault-injection suite asserting at every write boundary that a later write for the same dispatch key still succeeds |
