# Reviewer Result — TEST iteration 1

TEST_REVIEW: FAIL

Reviewer: B3 (Claude Opus). Verifies: TEST Worker B2, `artifacts/runs/run_db374a3fd83a/TEST.md`.

## Verdict Summary

The TEST phase gate does **not** pass. The blocking cause is not reporting quality — it is that a
MAJOR OS-30 correctness defect (T-001) is live in shipped production code and the required
malformed/unsupported-schema negative regression is absent from the suite. Both are explicit blocking
criteria for this review.

Separately, and in the worker's favour: I re-derived every factual claim in `TEST.md` by independent
execution and **found no false validation claim**. Worker B2's analysis, defect attribution, honesty
about its own bad command invocation, and refusal to encode incorrect behaviour as a passing test are
all correct. This FAIL routes T-001 to IMPLEMENTATION; it is not a finding against B2's conduct.

## Independent Re-Derivation of Worker Claims

Every gate below was re-run by me in this worktree, not read from `TEST.md`.

| Worker claim | My independent result | Match |
| --- | --- | --- |
| Full suite `Ran 1678 tests`, `OK (skipped=6)` | `Ran 1678 tests in 326.315s`, `OK (skipped=6)`, exit 0 | YES |
| `validate_skills.py` — 697 checks | `Skill validation PASSED (697 checks)` | YES |
| `verify_package.py` — 195 source files | `Package verification PASSED (195 source files)` | YES |
| `compileall` clean | exit 0, no output | YES |
| `clarification_protocol.py` source/installed byte parity | `diff -q` identical | YES |
| `run_logging.py` source/installed byte parity | `diff -q` identical | YES |
| `git diff --check` clean | exit 0, no output | YES |
| No tracked historical artifact modified/deleted | `git diff --name-status -- artifacts` empty; no `D` filter hits | YES |
| 20 clarification tests | 20 `def test_` in `scripts/test_clarification_protocol.py` | YES |
| Archive builds and verifies, 195 members | built + verified, `MEMBERS=195`, all 3 fixtures present | YES |
| Archive SHA-256 `26deb284…4d9fdd21` | my rebuild: `26deb28456a01a30cfeeffbaf91c179bf7300f560ae6a6cb5bffc7c74d9fdd21` | **byte-identical** |
| First command failed on environment, not product | reproduced: `ModuleNotFoundError: No module named 'build_release'` under `PYTHONPATH=.`; passes under documented discovery | YES |

The reproducible archive digest matching to the byte is strong evidence the worker actually executed
what it reported rather than narrating it.

## T-001 — CONFIRMED, and slightly broader than reported

I reproduced the worker's exact probe on a freshly created request, mutating only
`schema_version: 1 → 2`:

```text
SHOW_ACCEPTED_SCHEMA_VERSION 2
INGEST_STATUS DECIDED
IngestResult(response_id='response_7644b292…', decision_id='decision_92c97731b544f5dc28ab74c8',
             status='DECIDED')
```

A real `decision_` artifact was minted from an unsupported schema version.

Root cause, read directly from `scripts/clarification_protocol.py:449-456`: `_request()` validates
only `request_id` and `schema == "orca.clarification.request"`. `CLARIFICATION_SCHEMA_VERSION`
(line 28) is **declared and never referenced anywhere else in the module** — a dead constant. Records
are written with a hardcoded `"schema_version": 1` and that field is never read back.

I then probed each sub-claim in the worker's Required Action. All are accurate; none overstated:

| Mutation to published `record.json` | `show()` | `ingest()` |
| --- | --- | --- |
| `schema_version` → 2 (unsupported) | ACCEPTED | `DECIDED` |
| `schema_version` deleted | ACCEPTED | `DECIDED` |
| unknown top-level extra field | ACCEPTED | `DECIDED` |
| `default_applicable` → `true` | ACCEPTED | `DECIDED` |
| `on_timeout` → `"auto approve recommended"` | ACCEPTED | `DECIDED` |
| nested item extra field | ACCEPTED | `DECIDED` |
| nested item `what_is_blocked` deleted | ACCEPTED | `DECIDED` |
| `recommended_option_id` → nonexistent id | ACCEPTED | `DECIDED` |

The last row extends the worker's enumeration: a request whose recommendation points at an option
that does not exist is also read and acted upon.

**Jira AC impact.** This is not merely a hygiene gap. Two OS-30 acceptance criteria are directly
defeated on the read path:

- *"추천은 사용자 승인을 가장한 자동 선택으로 취급되지 않는다"* — `default_applicable` can be flipped
  to `true` on a published request and is honoured without rejection.
- *"timeout이나 응답 부재가 암묵적인 승인으로 변환되지 않는다"* — `on_timeout` can be rewritten to an
  auto-approval string and is honoured without rejection.

The creation path enforces these correctly; the reader does not re-establish them. Since requests are
immutable on-disk artifacts consumed later by `show`/`ingest`, the reader is the enforcement point
that matters. Severity MAJOR and responsible phase IMPLEMENTATION are both correct as assigned.

## R-001 — Invalid fixtures are non-executable stubs (non-blocking, but explains the escape)

The two "invalid" fixtures are not request payloads at all:

```json
// invalid/recommended_default.json
{"description": "Invalid by contract: …", "default_applicable": true, "on_timeout": "select recommendation"}
// invalid/oversized_bundle.json
{"description": "Invalid by contract: …", "item_count": 4}
```

Neither has an `items` array, so neither can be fed to `create()`. `test_published_fixture_files_are_exercised`
correspondingly never passes them to production code — it asserts only that a description string
starts with `"Invalid by contract:"`, that `default_applicable` is truthy, and that `item_count > 3`.
Those assertions are tautological restatements of the stub's own literals and would pass unchanged if
the production rejection logic were deleted entirely.

I verified this is **not** an uncovered-contract gap: both underlying behaviours have genuine
executable coverage elsewhere — `test_needs_input_and_conflict_create_complete_non_default_request`
asserts `default_applicable is False` and the exact `on_timeout` string on the real published record,
and `test_bundle_bound_and_independence` asserts `ClarificationError` on a real 4-item bundle. So
R-001 is a test-quality finding, not an independent blocker, and I am not blocking on it.

It is recorded because it is the structural reason T-001 escaped: no fixture in the suite is ever
round-tripped *through the reader*, so the reader has no negative coverage at all. The T-001 fix
should close both together.

Consequently the worker's AC-table row citing the two invalid fixtures plus that test as evidence for
the "invalid bundle/default fixtures" category overstates what that test executes. The row also cites
`test_needs_input_and_conflict_create_complete_non_default_request`, which is real evidence, so I
treat this as an imprecise citation rather than a false validation claim.

## Scope, Contract, and Boundary Checks

- **OS-28/OS-29 alignment — clean.** The tracked diff is 15 files, +246/−3. `run_logging.py` changes
  are purely additive: one new event constant `clarification_publication_failed` and one new reader
  `read_clarification_publication_errors()`. No OS-29 ledger schema, closed-field rejection, reserved
  fields, statuses, roles, rounds, or dispatch cardinality were touched.
- **Both Skill variants — accurate.** Orchestration `SKILL.md` documents the executable CLI contract,
  the bundle bound of 3, the two-revision ambiguity bound, `default_applicable: false`, the
  `0600` sensitive-response rule, and the OS-31 exclusion. Loop `SKILL.md` states the shared semantics
  and explicitly disclaims having the artifact store or CLI. Semantic parity without a false claim of
  feature parity; `validate_skills.py` passes 697 checks.
- **Non-interactive guarantee — holds.** No `input(`, `getpass`, `isatty`, `/dev/tty`, or Orca `ask`
  in `clarification_protocol.py`.
- **Historical preservation — holds.** No tracked artifact modified or deleted; prior run directories
  remain untracked and byte-unchanged.
- **No scope expansion.** Nothing in the diff touches OS-31 resume, transports, or UI. I confined this
  review to the same boundary.

## Required Action (IMPLEMENTATION, iteration 6)

1. Validate every published request record through a closed, version-aware reader in `_request()`
   before `show()`, `_current_request()`, or `ingest()` consume it. Reject unknown/missing
   `schema_version`, unknown or missing top-level and nested fields, wrong types, `default_applicable`
   other than `false`, a non-canonical `on_timeout`, and a `recommended_option_id` absent from
   `options` — without writing any response, decision, or lineage artifact.
2. Make `CLARIFICATION_SCHEMA_VERSION` load-bearing rather than dead.
3. Add direct negatives covering each row of the T-001 table, plus a positive version-1 control, and
   assert the no-side-effect property (no new files under `responses/`, `decisions/`, `lineage/`).
4. Replace the two stub fixtures with real request payloads that are actually passed to `create()`
   and asserted to raise, and add malformed published-record fixtures exercised through the reader,
   so `test_published_fixture_files_are_exercised` stops being tautological.

Re-review scope for the next iteration is T-001 and R-001 only.

## Decision Record

DECISION_STATE: CLEAR

REASON_CODE: none

EVIDENCE: The verdict rests on repository evidence I produced by executing and mutating the shipped
code in this worktree, not on the worker's narration. I independently re-ran the full 1,678-test
suite, the skill validator, the package verifier, `compileall`, both parity diffs, `git diff --check`,
the historical-artifact preservation check, and a fresh release build whose SHA-256 matched the
worker's reported digest byte-for-byte. I reproduced T-001 on a freshly created request and then ran
an eight-case mutation matrix proving the request reader validates neither the schema version, the
closed top-level field set, nested item schemas, `default_applicable`, `on_timeout`, nor
`recommended_option_id` — every case reaching a `DECIDED` outcome with a real decision artifact. I
confirmed by source reading that `CLARIFICATION_SCHEMA_VERSION` is never referenced. I verified that
R-001's two contracts are nonetheless genuinely covered elsewhere, so R-001 is non-blocking. No
user-owned choice is open: T-001 is a deterministic IMPLEMENTATION correctness defect, and the FAIL
follows from the blocking criteria rather than from any judgement call reserved to the user.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "TEST iteration 1 is verified and does not pass the phase gate. Every validation claim in TEST.md was independently re-derived and matched, including a release archive whose SHA-256 reproduced byte-for-byte and a full suite of 1678 tests reporting OK (skipped=6), so there is no false validation claim. Blocking finding T-001 was reproduced directly: ArtifactHumanApprovalPort._request() validates only request_id and schema, CLARIFICATION_SCHEMA_VERSION is a dead constant, and an eight-case mutation matrix showed unsupported schema_version, deleted schema_version, unknown top-level fields, default_applicable flipped to true, a rewritten on_timeout, nested-item field tampering, and a dangling recommended_option_id are all accepted by show() and produce a DECIDED outcome with a real decision artifact. This defeats the OS-30 acceptance criteria that a recommendation is never disguised approval and that timeout or non-response never becomes implicit approval, so it is a MAJOR IMPLEMENTATION correctness defect and the required malformed/unsupported-schema negative regression is absent. Non-blocking finding R-001 records that the two invalid fixtures are descriptive stubs with no items array and that test_published_fixture_files_are_exercised asserts only their own literals; both underlying contracts are genuinely covered by test_needs_input_and_conflict_create_complete_non_default_request and test_bundle_bound_and_independence, so it does not independently block, but it explains why no fixture is ever round-tripped through the reader. OS-28 and OS-29 contracts are intact under a purely additive run_logging change, both Skill variants state accurate semantics and capability boundaries, the non-interactive guarantee holds, and no historical artifact was modified. No user-owned choice is open.",
  "iteration": 1,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "test",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T10:35:00Z",
  "responsible_phase": "implementation",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer verdict for TEST iteration 1 of Jira OS-30, limited to verifying TEST Worker B2: independent re-execution of the full regression, skill, package, compile, parity, historical-preservation and release-archive gates; direct reproduction and scoping of blocking finding T-001; fixture-category and Jira acceptance-criteria mapping to executable evidence; and OS-28/OS-29 contract and two-Skill boundary alignment. Excludes OS-31 resume and transport work, terminal or approval UI, and any production defect fix, which belong to IMPLEMENTATION.",
  "sequence": 16,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_TEST.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": {
    "iteration": 1,
    "phase": "test",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/test/1/B2#15"
  }
}
```
