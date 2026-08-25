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

---

## IMPLEMENTATION iteration 4 — downstream revalidation (§17 T5a)

Not a correction round against IMPLEMENTATION's own gate — that passed after iterations 2 and 3.
This is the §17 T5a revalidation triggered by a **corrected upstream artifact**: the Final
Adversarial Review's R1 and R3 were adjudicated as DESIGN-phase gaps, DESIGN.md's D-C and D-E were
rewritten (commit `476dcc9`), and that commit states plainly that "the redaction-pattern and
closed-world-formula code fixes implied by the corrected DESIGN spec have not landed yet". They
land here.

### Summary / Analysis

**Something did have to change, and it was not cosmetic.** Both corrected sections change the
*shipped behaviour* the previously-approved code implements, so "nothing needed to change" was
never available:

* **R1 / D-C.** `redaction/1.0` stated the absolute-path rule as an **allowlist of three home
  roots**, so every root nobody thought of failed open. `_relative_artifact_path()`'s fallback ran
  `redact_text()` over a path that the policy did not recognise and returned it **unchanged**.
* **R3 / D-E.** The precision gate was `if closed_world or not unadjudicated`, and the false-
  positive numerator was `adjudicated_false_positives` alone. Under a closed-world attestation with
  an unmatched finding, the gate opened, `precision` charged the finding against itself, and
  `false_positive_rate` reported **0.0** while `unadjudicated_count` was 1 — two metrics
  contradicting each other about the same finding.

Both were reproduced against the **pre-fix code at `HEAD`** (`git show HEAD:scripts/…` executed
directly), then re-run against the fixed code. See *Additional Validation* for the transcripts.

### Changes

| # | commit | what |
|---|---|---|
| 1 | `9e19ce0` | D-C: `redaction/1.1`, category 5, C.7's P-PATH classifier + postcondition, the `_relative_artifact_path()` ladder, SKILL.md §9 + validator anchor, T-3 tests. `tools/run_logging.py` byte-parity in the **same** commit. |
| 2 | `2d863ea` | D-E: `classify_unmatched()`, `ATTESTED_FALSE_POSITIVE`, `attested_false_positives`, `complete_by_attestation`, the three consistency invariants, E.3's coupling in both directions, C.7 P-PATH for the scorer's own path fields, T-4 tests. |

**Commit 1 — D-C, exactly as the corrected spec states it.**

* `FINAL_REVIEW_REDACTION_POLICY_VERSION = "redaction/1.1"`. `"redaction/1.0"` now raises like any
  other unknown version (C.2: exactly one policy is *executable* at a time; older records keep the
  `1.0` stamp as **data**).
* `_PATH_SEGMENT`, `_HOME_ABSOLUTE_PATH` and `_FOREIGN_ABSOLUTE_PATH` copied **verbatim** from
  C.3.1 — not re-derived — and `REDACTION_CATEGORIES` is now the ordered **5**-tuple. Category 4 is
  unchanged (user-name segment only, path stays readable); category 5 replaces the **whole** match
  with `FOREIGN_PATH_PLACEHOLDER` and borrows nothing from the input. **No segment-count floor**:
  `/tmp`, `/luminous`, `/workspace-501` and `/session-<uuid>` are matched, which is the D3-001 fix.
* C.7's literals — `FOREIGN_PATH_PLACEHOLDER`, `_SAFE_RELATIVE_PATH`, `_NON_FILE_URL` — and the
  total pair `normalize_retained_path_field()` / `assert_retained_path_field()`. The postcondition
  re-runs the **total classifier**; it is not a fixed-point test, so a value the free-text policy
  fails to recognise has no way through.
* `_relative_artifact_path(path, root)` keeps its name and signature and gains rung 2
  (`<REPO>/…`, project root **derived** from `<ARTIFACT_ROOT>` via `_artifact_project_root()` — no
  `.git` walk, no `__file__`, no env var) and rung 3 (the whole value replaced, **without** calling
  `redact_text()`), then normalizes and asserts.
* `FINAL_REVIEW_RETAINED_PATH_FIELDS` is the closed table, checked by `_assert_retained_path_fields()`
  at record assembly **after every field is in place and before anything is staged**, and the
  exporter runs the same assertion over `orchestrator_log.path` and each embedded artifact path
  before serialising. A violation is `RunLoggingError` and nothing is published.
* SKILL.md §9's *secret-safe* paragraph now states `redaction/1.1`, category 5 with no segment
  floor, and the P-PATH property in its own words; `validate_skills.py`'s anchor moves with it
  (`test_a_redaction_policy_version_that_drifts_fails` now derives the expected string from the
  constant instead of hard-coding it, so this cannot drift again).

**Commit 2 — D-E, and the scorer's own path fields.**

* `classify_unmatched(unmatched, verdicts, *, closed_world)` — **one** function over both paths,
  whose only inputs are `closed_world`, the E.4 step-6 `reason` and the verdict map, so the two
  numerators cannot drift apart again. An explicit verdict always wins; closed world +
  `no_key_match` + no verdict → `ATTESTED_FALSE_POSITIVE`; `unresolvable_location` and
  `ambiguous_match` stay `UNADJUDICATED` and **refuse both metrics** with
  `closed_world_incomplete_match_evaluation`.
* `attested_false_positives` is unconditional; `adjudication_status` gains
  `complete_by_attestation`; `false_positive_rate = (adjudicated + attested) / findings_total`,
  which reduces to the unchanged open-world formula because `attested` is 0 on path B.
* `_assert_metric_consistency()` **aborts rather than serializes** (`RuntimeError`): COMPUTED
  implies `unadjudicated_count == 0`, every finding accounted for exactly once, and
  `precision + false_positive_rate == 1`. `findings_total == 0` refuses both metrics on both paths.
* E.3's coupling is now validated in **both** directions — an `exhaustive_attestation` supplied
  while `closed_world` is false is exit 2, like an unsigned closed world.
* C.7 for the scorer: `final_review_eval.py` **imports** `run_logging` and routes `parse-report`'s
  `source_report` and the metrics' `findings_source` through `normalize_retained_path_field()` /
  `assert_retained_path_field()`. A second copy of the policy is precisely the drift R1 punished;
  "standard library only" forbids third-party dependencies, not this repository's own module.

### Modified Files

| file | change |
|---|---|
| `scripts/run_logging.py` | policy version, `_PATH_SEGMENT` / `_HOME_ABSOLUTE_PATH` / `_FOREIGN_ABSOLUTE_PATH`, category 5, `FOREIGN_PATH_PLACEHOLDER`, `_SAFE_RELATIVE_PATH`, `_NON_FILE_URL`, `REPO_RELATIVE_PREFIX`, `FINAL_REVIEW_RETAINED_PATH_FIELDS`, `normalize_retained_path_field()`, `assert_retained_path_field()`, `_assert_retained_path_fields()`, `_artifact_project_root()`, the `_relative_artifact_path()` ladder, the record-assembly and exporter postconditions |
| `orca-worker-reviewer-orchestration/tools/run_logging.py` | byte-identical twin, same commit (`cmp` clean) |
| `orca-worker-reviewer-orchestration/SKILL.md` | §9 *secret-safe*: `redaction/1.1`, category 5, P-PATH |
| `scripts/validate_skills.py` | the redaction-policy anchor |
| `scripts/final_review_eval.py` | `import run_logging`, `ATTESTED_FALSE_POSITIVE`, `INCOMPLETE_MATCH_REASONS`, `classify_unmatched()`, `_retained_path_field()`, `_assert_metric_consistency()`, the rewritten gate, `attested_false_positives`, `complete_by_attestation`, E.3's reverse coupling, `source_report` / `findings_source` |
| `scripts/test_run_logging.py` | +14 tests (below) |
| `scripts/test_final_review_eval.py` | +13 tests (below) |
| `scripts/test_validate_skills.py` | the drift test derives the version from the constant |

**Not touched, deliberately.** I-001 / I-002 / I-002-R1 (resolved, unrelated); the two
Coordinator-owned residual disclosures documented in TEST.md (append-only/immutable by design); and
the two historical baseline runs `artifacts/runs/run_ff587481a820/` and
`artifacts/runs/run_92759e0e1034/`, which were executed **before** this fix and retain the pre-fix
path-leak artifacts as forensic evidence of R1's prior state. D-C C.7's *regeneration of
already-retained evidence* rule therefore remains **open and deferred** — regenerating a baseline is
a fresh capture, which is a TEST-phase activity, not part of this revalidation.

### Unit Tests

**Added — `scripts/test_run_logging.py`**

| class / test | behaviour covered |
|---|---|
| `RedactionPolicyTests.test_the_superseded_policy_version_is_refused_too` | `"redaction/1.0"` raises; older records keep the stamp as data |
| `ForeignAbsolutePathRedactionTests` (5 tests) | the 9 category-5 positives — including the shipped-baseline shape and **all four one-segment D3-001 cases** — each replaced whole, with a non-zero `foreign_absolute_path` count and no username / uid / session fragment surviving; `file://` keeps its scheme; the 7 guaranteed exemptions survive; a home path stays readable and is **not** swallowed by category 5; idempotence over both tables |
| `RetainedPathFieldClassifierTests` (4 tests) | the classifier is total — 12 values replaced in full (4 of them one-segment), 7 returned unchanged (P1/P2/P3/P4); normalizer/postcondition agreement; and `assert_retained_path_field("/luminous")` **raises**, which a fixed-point test would not have done |
| `RetainedPathFieldRecordTests` (4 tests) | the three ladder rungs produce `FINAL_REVIEW.md` / `<REPO>/docs/FINAL_REVIEW.md` / the placeholder through the **real writer**; a scratch path leaves no fragment in `record.json`; the generic sweep — no string anywhere in the record begins with `/`, and every closed-table field is P1–P4; and with the ladder stubbed to a raw path (multi-segment **and** one-segment) the write raises and publishes nothing |

**Added — `scripts/test_final_review_eval.py`**

| class / test | behaviour covered |
|---|---|
| `ClosedWorldFalsePositiveRateTests.test_an_unmatched_finding_under_attestation_is_an_attested_false_positive` | **the R3 reproduction, by exact value**: `findings_total 6`, 5 matched, reason `no_key_match`, classification `ATTESTED_FALSE_POSITIVE`, `attested_false_positives 1`, `adjudicated_false_positives 0`, `unadjudicated_count 0`, status `complete_by_attestation`, `precision 5/6`, `false_positive_rate 1/6` — **not 0** — and `precision + fpr == 1`. Four of these assertions fail against the pre-fix code |
| `…test_an_explicit_verdict_beats_the_attestation` | verdict wins; `attested 0`, status `complete`, precision 1, fpr 0 |
| `…test_closed_world_refuses_an_incompletely_evaluated_match` | both incomplete reasons forced: REFUSED with `closed_world_incomplete_match_evaluation`, `UNADJUDICATED`, status `partial`; a verdict for that id flips the same input to COMPUTED |
| `…test_the_open_world_path_never_auto_false_positives` | path B unchanged: still `UNADJUDICATED`, `attested 0`, `adjudication_incomplete` |
| `…test_the_gate_is_a_single_decision_across_every_case` | `precision_status == false_positive_rate_status` everywhere, and COMPUTED ⇒ `unadjudicated_count == 0` and the sum is 1 |
| `…test_no_findings_refuses_both_metrics_on_both_paths` | zero denominator refused, never divided |
| `PrecisionRefusalTests.test_a_closed_world_run_refuses_an_unresolvable_noise_finding` | the honest half of the rule, at the same call site as the old test |
| `ScorerPathFieldTests` (3 tests) | a scratch `source_report` **and** `/luminous` both become the placeholder; a repository path stays readable; no string in the metrics document begins with `/` |
| `AdjudicationContractTests.test_an_attestation_without_a_closed_world_claim_is_refused` | E.3's reverse coupling |
| `ExitCodeTests.test_three_when_a_closed_world_run_cannot_finish_a_match` | `--require-precision` still exits **3** under a closed-world refusal |
| `ExitCodeTests.test_two_when_closed_world_and_the_attestation_disagree` | exit **2** in both directions, at the CLI boundary |

**Modified.** `PrecisionRefusalTests.test_a_closed_world_attestation_computes_precision` now uses a
noise finding whose location **resolves** (the only shape an attestation can speak about); the old
input, whose location is unresolvable, moved to the new refusal test rather than being deleted.
`test_a_redaction_policy_version_that_drifts_fails` derives the version from the constant.

**Execution.** `python3 -m unittest discover -s scripts -p 'test_*.py'` → **1011 tests, OK**
(6 pre-existing skips); 984 before this iteration, so +27 net.

### Additional Validation

| check | result |
|---|---|
| `python3 scripts/validate_skills.py` | PASSED (463 checks) |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | OK — 1011 tests, 6 skipped |
| `python3 scripts/verify_package.py` | PASSED (107 source files) |
| `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py` | byte-identical |
| `python3 scripts/final_review_eval.py verify-fixture` | PASSED (exit 0) |

**R1 reproduction, at the code level, through the real writer.** A record written with
`report_path` pointing at a real file under the session scratch root
(`/private/tmp/claude-<uid>/-Users-<user>-…/<session-uuid>/scratchpad/…/REPORT.md`), and a second
with the one-segment `/luminous`:

```text
PRE-FIX  (git show HEAD:scripts/run_logging.py)
  report.contract_path : /private/tmp/claude-501/-Users-luminous-…/<uuid>/scratchpad/…/REPORT.md
  report.redactions    : []

POST-FIX (deep scratch path)          POST-FIX (one-segment "/luminous")
  contract_path : <REDACTED:foreign_absolute_path>   <REDACTED:foreign_absolute_path>
  "luminous" in record.json   : False                False
  "claude-501" in record.json : False                False
  any string value starts "/" : False                False
  redaction_policy_version    : redaction/1.1        redaction/1.1
```

**R3 reproduction, end to end through the CLI.** `parse-report` → `score --adjudications
<closed-world attestation, zero verdicts> --require-precision`, on a perfect report plus one noise
finding whose location resolves and matches no key entry:

```text
PRE-FIX  (git show HEAD:scripts/final_review_eval.py)
  unadjudicated_count 1 | classification UNADJUDICATED | adjudication_status partial
  precision 0.8333 | false_positive_rate 0.0            <- the false zero
  findings_source /private/tmp/claude-501/-Users-luminous-…/repro_r3/REPORT.md   <- the path leak

POST-FIX
  findings_total 6 | unadjudicated_count 0 | adjudication_status complete_by_attestation
  unmatched: [{finding_id R9, reason no_key_match, classification ATTESTED_FALSE_POSITIVE}]
  attested_false_positives 1 | adjudicated_false_positives 0
  precision 0.8333 (COMPUTED) | false_positive_rate 0.1667 (COMPUTED) | sum 1.0
  findings_source <REDACTED:foreign_absolute_path>
  exit 0 under --require-precision
```

---

## IMPLEMENTATION iteration 5 — downstream revalidation for R5 (§17 T5a)

Not a correction round against IMPLEMENTATION's own gate. This is the §17 T5a revalidation
triggered by a **corrected upstream artifact**: the Final Adversarial Review's R5 was adjudicated
as a DESIGN-phase gap, DESIGN.md gained a new subsection **A.6** (commit `a8bec44`), and A.6's
mechanism plus its T-5a regression requirement land here. R1–R4 are settled and are not reopened;
the two Coordinator-owned residual disclosures are untouched.

### Summary / Analysis

**The defect, restated from the evidence rather than from the report.** The repository's required
final validation is `git diff --check <base>..HEAD`. Measured at the start of this iteration:

```text
$ git diff --check 1045815..HEAD ; echo EXIT=$?
… 80 lines of output …
EXIT=2
```

All 80 output lines are 40 `trailing whitespace.` errors, every one of them in a single file:

```text
artifacts/runs/run_92759e0e1034/final_review_audit/attempt1__task_936f73b5d2eb__ctx_1f82fd26c92b/report.md
```

Those 40 lines end in two spaces — the Markdown **hard line break**, which is what makes the
`ID:` / `Severity:` / `Blocking:` finding blocks render as separate lines in the Reviewer's report.

**Why the bytes could not be trimmed, verified rather than asserted.** That file's `record.json`
records `report.artifact_digest_post_redaction = sha256:6f91033e…e748e18` and
`report.byte_length_post_redaction = 6028`, and the committed bytes still hash to exactly that.
A.3 makes a published `<dispatch_key>/` byte-for-byte immutable and gives the writer no mutation
surface, so trimming would either falsify the published record or require rewriting it. The fix
therefore had to make the gate pass **without changing one byte of any published record** — which
is what A.6 specifies and what was applied here, unchanged. Nothing in A.6 was redesigned; it had
already been verified empirically by the DESIGN correction, and this iteration's job was to apply
it for real and pin it with a test.

### Changes

| # | commit | what |
|---|---|---|
| 1 | `7718ea5` | **Step 0**: new repository-root `.gitattributes` — one commented, path-scoped `-whitespace` rule. Committed **before** any other change in this iteration, so every later step's own `git diff --check` run was already clean and the fix cannot be read as a retroactive patch over a failing gate. |
| 2 | *(this commit)* | T-5a — `RetainedReportWhitespaceExemptionTests` in `scripts/test_run_logging.py` (7 tests), and this IMPLEMENTATION section. |

**Commit 1 — the exemption, exactly as A.6 spells it.**

```gitattributes
artifacts/runs/*/final_review_audit/**/report.md -whitespace
```

`-whitespace` (attribute *unset*) tells `git diff --check`, `git apply --whitespace` and `git am`
to apply **no** whitespace rules to matching paths. It is honoured for already-committed content
because `git diff` resolves attributes from the working-tree `.gitattributes`, not from the commits
being compared, so a range ending at `HEAD` is covered the moment the file exists in the checkout.
The scope is `report.md` alone — **not** the audit directory. `input.md` is a canonicalized capture
of the stored Task spec and `record.json` is writer-serialized JSON; both are produced by this
codebase under its own formatting control and both stay fully gated. The rule is not repo-wide,
does not use `* -whitespace`, and does not touch `core.whitespace`.

**Commit 2 — T-5a, both halves in one class.** The point of the test is that A.6's two halves must
hold **together**: passing the gate is worthless if it was bought by editing a digest-bound file,
and an intact digest is worthless if the gate still fails. Neither assertion can be satisfied by
the other's fix.

### Modified Files

| file | change |
|---|---|
| `.gitattributes` | **new** (repository root). One commented rule, `artifacts/runs/*/final_review_audit/**/report.md -whitespace`. |
| `scripts/test_run_logging.py` | `import hashlib`; new `RetainedReportWhitespaceExemptionTests` (7 tests) plus the pinned constants `WHITESPACE_GATE_BASE_COMMIT`, `HARD_BREAK_REPORT`, `HARD_BREAK_REPORT_DIGEST`, `HARD_BREAK_REPORT_BYTES`, `GITATTRIBUTES_RULE`. |
| `artifacts/runs/run_804e35d29531/IMPLEMENTATION.md` | this section, appended. |

**Not touched, deliberately.** No published record unit — no `report.md`, `input.md` or
`record.json` under any `final_review_audit/` directory was read-modify-written, and `git diff`
confirms every one of them is byte-identical to `HEAD`. `scripts/run_logging.py` and its installed
twin are unchanged, so no byte-parity risk was introduced. R1–R4's fixes and the two
Coordinator-owned residual disclosures are untouched.

### Unit Tests

**Added — `scripts/test_run_logging.py::RetainedReportWhitespaceExemptionTests`**

| test | half of A.6 | behaviour covered |
|---|---|---|
| `test_the_whitespace_gate_passes_over_the_whole_os22_range` | (a) | `git diff --check 1045815..HEAD` as a subprocess from the repository root: **exit 0 and empty stdout** |
| `test_the_gitattributes_rule_is_exactly_the_one_designed` | (a) | the file's non-comment lines are **exactly** the one scoped rule — a repo-wide or broadened pattern fails here |
| `test_every_retained_artifact_still_matches_its_recorded_digest` | (b) | for **every** record unit under `artifacts/runs/*/final_review_audit/*/`, both `report` and `stored_task_spec`: re-hash the file named by `artifact_path` and assert SHA-256 == `artifact_digest_post_redaction` and size == `byte_length_post_redaction`. Guarded against a vacuous pass — the hard-break report **must** appear in the verified set |
| `test_the_hard_break_report_keeps_its_forty_trailing_space_lines` | (b) | the pinned file still hashes to `sha256:6f91033e…e748e18` at 6028 bytes **and** still carries exactly **40** two-trailing-space lines. This is the assertion trimming would break |
| `test_only_retained_reports_are_exempt` | (c) | `git check-attr whitespace` is `unset` for each retained `report.md` and `unspecified` for every sibling `input.md` and `record.json`, for `scripts/run_logging.py`, and for the root `README.md` |
| `test_the_pattern_does_not_leak_outside_the_audit_directories` | (c) | the glob boundaries: `report.md`, `artifacts/report.md`, `artifacts/runs/<run>/report.md` and `final_review_audit/x/report.md` all resolve `unspecified` |
| `test_the_gate_fails_again_once_the_exemption_is_removed` | (d) | in a scratch `git clone` of the repository: `.gitattributes` **is present** (proving it is committed, not merely sitting in a working tree), the gate exits **0** there; then the file is deleted and the same command must exit **2** naming the hard-break report. This is what makes T-5a a regression test rather than one that passes because the condition happens not to occur |

**Never a silent pass.** `_require_git_range()` **skips** — not passes — when `git` is absent from
`PATH`, when there is no `.git` directory, or when commit `1045815` is unreachable (shallow or
grafted checkout). The clone-based test skips if the clone itself cannot be made.

**Execution.** `python3 -m unittest discover -s scripts -p 'test_*.py'` → **1026 tests, OK**
(6 pre-existing skips). A clean clone of `HEAD` before this commit runs **1019**, so this iteration
is **+7 net**, matching the 7 tests added and confirming nothing was displaced.

### Additional Validation

| check | result |
|---|---|
| `git diff --check 1045815..HEAD` | **exit 0**, no output (was exit 2 / 40 errors) |
| `python3 scripts/validate_skills.py` | PASSED (463 checks) |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | OK — **1026** tests, 6 skipped |
| `python3 scripts/verify_package.py` | PASSED (107 source files) |
| `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py` | byte-identical |
| `python3 -m unittest scripts.test_run_logging.RetainedReportWhitespaceExemptionTests` | OK — 7 tests |

**Digest re-verification over every published record unit** (step 3 of this task, run directly
rather than only through the test):

```text
OK  stored_task_spec  run_804e35d29531/…__ctx_4b509b12a0b1/input.md   9322  sha256:cb503eeb…
OK  stored_task_spec  run_804e35d29531/…__ctx_6478d2923ca0/input.md   9322  sha256:cb503eeb…
OK  stored_task_spec  run_804e35d29531/…__ctx_99cc7e6b886c/input.md   9322  sha256:cb503eeb…
OK  report            run_92759e0e1034/…__ctx_1f82fd26c92b/report.md  6028  sha256:6f91033e…
OK  stored_task_spec  run_92759e0e1034/…__ctx_1f82fd26c92b/input.md   4104  sha256:03001ef4…
OK  report            run_ff587481a820/…__ctx_33c8c8414587/report.md  6503  sha256:c9aecb9f…
OK  stored_task_spec  run_ff587481a820/…__ctx_33c8c8414587/input.md   3936  sha256:e084234f…
```

`git diff --quiet -- 'artifacts/runs/*/final_review_audit/*/report.md'` exits **0**: the working
copies are byte-identical to `HEAD`. (`run_804e35d29531`'s three units carry
`report.capture_status = "absent"` with an empty `artifact_path` — there are no retained report
bytes to bind for those, so only their `input.md` is digest-checked.)

**Negative controls — the exemption suppresses nothing else** (step 4; each mutation was reverted
immediately and `git status` confirmed a clean tree afterwards):

| injected trailing whitespace | `git diff --check` |
|---|---|
| `scripts/_ws_probe_tmp.py` (throwaway file outside the pattern) | **exit 2**, flagged `trailing whitespace.` |
| `…/final_review_audit/…__ctx_1f82fd26c92b/input.md` (sibling in the *same* record unit) | **exit 2**, flagged |
| `…/final_review_audit/…__ctx_1f82fd26c92b/report.md` (the exempted path) | **exit 0**, correctly not flagged |

**Attribute resolution, measured:**

```text
unset        artifacts/runs/*/final_review_audit/*/report.md          (all 5)
unspecified  artifacts/runs/*/final_review_audit/*/input.md           (all 5)
unspecified  artifacts/runs/*/final_review_audit/*/record.json        (all 5)
unspecified  scripts/run_logging.py, README.md,
             report.md, artifacts/report.md, artifacts/runs/<run>/report.md
```

### Residual / deferred

None introduced by this iteration. A.6's exemption is future-proof in the intended direction: it
covers any *future* retained report whose Reviewer uses Markdown hard breaks, so this class of
gate failure cannot recur, while a whitespace defect in real code, in a skill, in a script, in a
test, in documentation — or in the other two files of the very same record unit — is still caught.
