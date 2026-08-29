# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The design covers D-A through D-F in substantial detail and generally follows the repository's
run-scoped artifact, stdlib-only, open log-event, byte-parity, fixture, and validator conventions.
However, three concrete contradictions prevent implementation from satisfying the approved PLAN:
the neutrality test normalizes away byte changes, the metric schema makes byte-identical reruns
impossible, and the three-file immutable writer has no recoverable publication protocol after a
partial I/O failure. These violate explicit DEC-1/B5 and required audit/failure-handling behavior.

## Blocking Findings

ID: D-001
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `DESIGN.md:1494-1502`; actual helper `scripts/test_e2e_harness.py:1089-1104`; PLAN DEC-1 `PLAN.md:92-111`
Issue: The proposed neutrality golden is not a byte-identity test because it applies the existing `_normalize_artifact()` to Task specs before comparison.
Reason / Evidence: `_normalize_artifact()` does more than replace a workspace path and timestamps: it calls `splitlines()`, tokenizes every line with `split()`, rejoins tokens with one space, and omits the final newline. Consequently indentation changes, repeated-space changes, trailing-space changes, and final-newline changes in reviewer-visible Task specs all compare equal. DESIGN nevertheless calls the result “character for character,” while approved DEC-1 requires the bytes produced by `render_task_spec()` to be character-for-character identical and explicitly rejects weakening that proof to a semantic argument. The direct cases are stated to be deterministic and timestamp-free, so this normalization is also unnecessary for that family.
Required Action: Define a Task-spec-specific capture/canonicalization contract that replaces only explicitly enumerated nondeterministic values without tokenizing, stripping, or reserializing the spec, and compare the resulting UTF-8 bytes (including whitespace and terminal newline). Keep log/artifact normalization separate from the neutrality-spec proof, and add a mutation test demonstrating that whitespace-only Task-spec changes fail T-6.

ID: D-002
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `DESIGN.md:1211-1221`, `DESIGN.md:1810-1813`; PLAN DEC-9 B5 `PLAN.md:450-455`
Issue: The scorer output includes a clock-derived `generated_at`, yet the approved B5 criterion requires byte-identical metrics when scoring the same stored output again.
Reason / Evidence: E.4 claims the algorithm reads no clock and therefore B5 holds, but E.5 makes `generated_at` a top-level timestamp, and T-4 weakens the assertion to byte identity “apart from `generated_at`.” That silently overrides PLAN B5, whose pass condition is byte-identical metrics and whose failure condition is any metric difference across identical inputs. An exception in the test is not the contracted behavior.
Required Action: Make the serialized scoring result deterministic for identical inputs—for example, remove wall-clock data from the metric document or supply a deterministic provenance value outside the byte-compared metric payload—and restore an unqualified byte-for-byte rerun assertion in T-4/B5.

ID: D-003
Quality Attribute: G2
Severity: MAJOR
Blocking: YES
Location: `DESIGN.md:221-236`; `DESIGN.md:1530-1547`; PLAN B3 `PLAN.md:450-455`
Issue: The immutable three-file writer cannot meet its stated all-or-nothing or recoverability behavior after an I/O failure between exclusive creates.
Reason / Evidence: The design prechecks for existing paths, then writes input, report, and record sequentially with `open(..., "x")`, record last. If writing the report or record fails after the input was created (disk full, interruption, permission transition), `_safe_log` swallows the error as required, but the next invocation sees the surviving input and treats it as a permanent collision: “if any exists, nothing is written.” With no overwrite, cleanup, resume, or staging/publish protocol, that dispatch can never acquire the required complete record/report/input set. Creating the record last prevents a partial set from masquerading as complete, but it does not preserve B3's required artifacts or provide recovery. The precheck also cannot make the three creates atomic.
Required Action: Specify a crash/failure-safe publication protocol that preserves immutable final paths while allowing recovery—such as exclusive creation of a dispatch-scoped staging directory/files, fsync as appropriate, and one atomic directory/manifest publication step, with a defined reader rule and cleanup/resume behavior for abandoned staging state. Add fault-injection tests at every write boundary and prove that retry for the same dispatch either completes the original record safely or reports an explicit recoverable incomplete state without blocking future completion.

## Non-Blocking Findings

None.

## Test Review

No implementation tests are expected in DESIGN. The proposed test plan is broad and maps to the
ticket's audit, failure, security, evaluation, regression, and neutrality groups, but T-6 and B5
currently encode weakened assertions and the writer tests do not inject failures between its three
creates. Those omissions are part of D-001 through D-003 and must be corrected in DESIGN before
implementation.

## Evidence Checked

- Full `artifacts/runs/run_804e35d29531/DESIGN.md` (1,956 lines).
- Approved `PLAN.md`, including DEC-1 through DEC-10, D-A through D-F, B1-B5, implementation ordering, tests, risks, and completion criteria.
- Verbatim OS-22 request from `orca orchestration task-list --run run_804e35d29531 --json`.
- Review policies: common and DESIGN-specific.
- Repository conventions and claimed extension points in `scripts/run_logging.py`, `scripts/task_context.py`, `scripts/e2e_harness.py`, `scripts/orca_runtime_harness.py`, `scripts/test_e2e_harness.py`, `scripts/validate_skills.py`, `scripts/release_manifest.py`, `orca-worker-reviewer-orchestration/SKILL.md`, and existing `artifacts/runs/*` layouts.

## Final Decision

FAIL. D-A through D-F are present, but D-001 and D-002 silently weaken two approved,
non-negotiable PLAN contracts, and D-003 leaves required per-dispatch evidence unrecoverable after
a realistic partial write. Correct these design contracts and re-review before implementation.
