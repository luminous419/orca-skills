# Review Result

RESULT: FAIL
IMPLEMENTATION_REVIEW: FAIL

## Summary

I re-derived every iteration-2 blocking finding against the working tree rather than reading the
Worker's closure claims. Four of the five are genuinely closed and I reproduced each fix by
execution: the effective-decision head now replays append-only lineage and returns the
post-cancellation decision that `ingest` reported (I-201), identical republication of a request
returns `EXISTING` while divergent content still conflicts (I-202), publication failure is appended
to `ORCHESTRATOR_LOG.md` at both seams and is readable through the new reader (I-203), and folded
Worker B2 / Reviewer B3 judgements that disagree on `(state, reason_code)` refuse instead of
silently presenting the producer's question (I-205). Driving both real seam methods against real
OS-29 ledgers, a folded B2+B3 pair publishes exactly one request carrying both ledger keys and the
producer's label, republication is now idempotent where iteration 2 raised
`ClarificationConflict`, and a missing Coordinator declaration still publishes nothing without
erroring. Scope is correct: no resume token, no response consumer, no transport, no dispatch, and
no lifecycle-status vocabulary was added, and OS-28/OS-29 contract files and every historical
artifact are untouched.

The phase gate nevertheless fails, on I-204 and on a defect the I-203 fix introduced. I-204's
required remedy was three named tests plus fixture wiring; one of the three is real, one is absent,
and one cannot fail — its "zero lifecycle delta" assertion watches `status`, `dispatch_count` and
`current_iteration`, three attribute names that exist on neither harness class, so it is fabricated
by the test itself and guards nothing. That inert assertion is not merely cosmetic: it is the exact
check that would have caught the second finding. In `E2EHarness`, the new durable-failure log call
sits unguarded inside the `except` block, so when that write fails the exception escapes
`_publish_clarifications_for_terminal_block`, unwinds `run_workflow`, and destroys an
already-constructed terminal `BLOCKED` result. I reproduced that end to end on a real blocking
workflow scenario: the run raises `OSError` where it must return `final_status: BLOCKED`.
`OrcaRuntimeHarness` guards the identical call with `_safe_log`; `E2EHarness` does not.

Both blocking findings are repository-local, small, and fail-loud rather than fail-open. Nothing
here silently approves anything, and no user-owned decision is open.

## Iteration-2 Finding Closure

| Finding | Verdict | How I verified it |
| --- | --- | --- |
| I-201 effective-decision head loses a post-cancellation decision | CLOSED | Executed `create → decide(staging) → cancel → decide(production)`; `_effective_decision` and `show` both return the second decision, matching `ingest`'s `DECIDED`. Lexicographic seeding would have picked the other record (`sorted([d1,d2]) != [d1,d2]`). `expand_scope` on the same item now succeeds where iteration 2 refused. Tampered lineage with two concurrent heads raises `decision lineage fork`. |
| I-202 identical republication fails as a conflict | CLOSED | Three identical `create` calls: `CREATED, EXISTING, EXISTING`, one stable `request_id`, original `created_at` preserved. Divergent content mints a different id; a tampered stored record still raises `ClarificationConflict`. Through both real seams, a second `_publish_clarifications_for_terminal_block()` leaves one request and zero errors. |
| I-203 publication failure recorded nowhere | CLOSED | Both seams append `clarification_publication_failed` with `result=BLOCKED`, and `run_logging.read_clarification_publication_errors()` returns the row. The `detail` cell carries only `type(exc).__name__`; a canary planted in the declaration's question text does not reach the log. |
| I-204 neither harness seam has any test | **NOT CLOSED** | See I-301. |
| I-205 folded judgements not checked for agreement | CLOSED | A Worker `NEEDS_INPUT`/`user_choice_required` with a bound Reviewer B3 declaring `CONFLICT`/`conflicting_instructions` raises `folded judgements disagree`; an agreeing pair still folds to one source with both keys and the producer's label. Through both seams the disagreement publishes nothing and logs durably. |

## Blocking Findings

### I-301 — I-204 is not closed: the zero-delta seam test cannot fail, and the missing-declaration test is absent

`scripts/test_clarification_protocol.py:225-241`. I-204's required remedy named three tests and the
fixture wiring. Delivered:

- **Fake-port cardinality — real.** `test_both_harness_seams_call_fake_port_once_and_mutate_no_lifecycle_counters`
  drives both concrete seam methods with an injected fake port and asserts `1 == len(port.calls)`
  for one folded B2/B3 pair. This part is sound and I confirmed it independently.
- **Zero-delta snapshot — inert.** The same test's second assertion is:

  ```python
  harness.status="BLOCKED"; harness.dispatch_count=7; harness.current_iteration=3
  before=(harness.status,harness.dispatch_count,harness.current_iteration)
  ...
  self.assertEqual(before,(harness.status,harness.dispatch_count,harness.current_iteration))
  ```

  The instance is built with `object.__new__(cls)`, and all three attributes are invented by the
  test. Neither harness has them:

  ```text
  grep -c "self.status"            scripts/e2e_harness.py -> 0   scripts/orca_runtime_harness.py -> 0
  grep -c "self.dispatch_count"    scripts/e2e_harness.py -> 0   scripts/orca_runtime_harness.py -> 0
  grep -c "self.current_iteration" scripts/e2e_harness.py -> 0   scripts/orca_runtime_harness.py -> 0
  ```

  The lifecycle state DESIGN §12 actually protects is `WorkflowRunResult.final_status` in
  `E2EHarness` and the `log_run_status("BLOCKED", ...)` write in `OrcaRuntimeHarness`. Neither is
  observed by any test. `IMPLEMENTATION.md`'s claim that the test "proves zero status/dispatch/
  iteration delta" is therefore not supported by the repository — and I-302 below is precisely the
  regression a real zero-delta test would have caught.

- **Missing-declaration fail-closed — absent.** `test_publication_failure_is_durable_and_reader_exposes_it`
  passes `clarification_inputs={}`, but it also makes the Reviewer disagree, so
  `terminal_block_sources` raises at the agreement check (`clarification_protocol.py:136-137`)
  before the declaration lookup at `:139` is ever reached. No test covers the Testing Strategy's
  "Missing/invalid request declarations never create vague requests and never un-block the run."
  I verified that behaviour myself against real ledgers through both seams — zero requests, zero
  errors, no status change — so the code is right; the gate simply does not hold it.

Required: assert the real terminal state (a `run_workflow` BLOCKED scenario that still returns
`final_status == blocked_status`, and the `OrcaRuntimeHarness` `BLOCKED` status write) instead of
fabricated attributes, and add the missing-declaration seam test.

### I-302 — A failed publication-failure log escapes `E2EHarness` and destroys the terminal BLOCKED result

`scripts/e2e_harness.py:1902-1911`, reached from `scripts/e2e_harness.py:1932-1954`. The
`except Exception` handler that closes I-203 calls `run_logging.log_orchestrator_event(...)`
directly. `OrcaRuntimeHarness` routes the identical call through `_safe_log`
(`orca_runtime_harness.py:2765-2771`, guard at `:2150-2154`); `E2EHarness` does not. Because
`snapshot()` constructs the `WorkflowRunResult` first and only then calls the seam, an escape means
`return result` never runs.

Reproduced on a real blocking workflow scenario (`DecisionGateTransitionTests.blocking_phase()`),
with only the OS-30 log write made to fail:

```text
BASELINE  final_status: BLOCKED
LOGFAIL   run_workflow RAISED -> OSError disk full
```

and at the method level directly:

```text
e2e : LOG FAILURE ESCAPES -> OSError: disk full
orca: contained (via _safe_log)
```

This contradicts approved DESIGN §12 — "Publication failure is logged as a closed OS-30 artifact
error and the run remains `BLOCKED`" — and the handler's own comment, "artifact failure never
changes terminal BLOCKED". It is the same structural class as iteration 2's N-203, which asked that
the claim be made structural rather than probabilistic; the read was moved inside the `try` as
requested, but the new write was added outside any guard. The direction is fail-loud, not
fail-open — no run is silently un-blocked and nothing is auto-approved — but a terminal decision
block is a run's most important output and it must survive an artifact-write failure, which is the
entire premise of the seam.

Required: route the `E2EHarness` handler's log write through the same swallow-and-record guard
`OrcaRuntimeHarness` already uses, so no OS-30 artifact write can unwind a settled terminal state.

## Non-Blocking Findings

- **N-301 — Disagreement refuses the whole publication, not the group.**
  `clarification_protocol.py:136-137` raises out of `terminal_block_sources` before any source is
  emitted, so one disagreeing group suppresses every other well-formed group in the same terminal
  block. I reproduced this with one clean declared group plus one disagreeing pair: nothing was
  published. I-204's remedy line said "refuse the group"; DESIGN §3 says only that disagreement
  "fails closed for Coordinator resolution", which this satisfies in direction if not in
  granularity. Non-blocking for the same reason iteration 2's N-201 was: OS-29 A5 admits one
  verification Reviewer while an item is open, so more than one open group is not reachable today.
  It becomes real the moment that changes.
- **N-302 — The invalid fixtures are never run through the validator.**
  `test_published_fixture_files_are_exercised` feeds only `valid/needs_input_request.json` to
  `create()`. For `invalid/recommended_default.json` and `invalid/oversized_bundle.json` it asserts
  their JSON content (`description` prefix, `default_applicable` true, `item_count > 3`) rather than
  that `create()` rejects them, so the negative fixtures prove nothing about the validator.
- **N-303 — `read_clarification_publication_errors` unescapes an escape the writer never emits.**
  `run_logging.py:461` applies `.replace(r"\\", "\\")` although `_escape` (`:313-315`) escapes only
  `|`, never doubling backslashes. Harmless for today's exception-name-only `detail`, wrong if that
  column ever carries a literal backslash.
- **N-304 — Carried forward, unfixed from iteration 2.** N-201 (no antichain selection or dependency
  ordering in `publish`), N-202 (bundle metadata taken from the last source's item body), N-204
  (`_effective_decision` still performs little of §9's reader validation beyond fork and
  head-count rejection) and N-205 (`CHANGELOG.md` still appends a **second** `## Unreleased`
  heading at end of file, with no blank line, below the released sections, while the file already
  carries one at the top) all remain. None is blocking.

## Test Review

Every gate below I ran myself in this worktree.

```text
PYTHONPATH=. python3 -m unittest scripts.test_clarification_protocol
Ran 18 tests in 0.125s -- OK

PYTHONPATH=. python3 -m unittest scripts.test_e2e_harness scripts.test_orca_runtime_contract \
  scripts.test_orca_runtime scripts.test_release_package scripts.test_validate_skills
Ran 672 tests in 75.734s -- OK (skipped=6)

PYTHONPATH=. python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1675 tests in 316.583s -- OK (skipped=6)

python3 scripts/validate_skills.py           Skill validation PASSED (697 checks)
python3 scripts/verify_package.py            Package verification PASSED (195 source files)
diff -q scripts/clarification_protocol.py orca-worker-reviewer-orchestration/tools/clarification_protocol.py   (identical)
diff -q scripts/run_logging.py             orca-worker-reviewer-orchestration/tools/run_logging.py             (identical)
python3 -m compileall (4 changed modules)    OK
git diff --check                             clean
```

The Worker's reported evidence reproduces exactly, and the suite grew by the six tests the Worker
added (1669 -> 1675). Four of the six are genuinely load-bearing:
`test_cancel_then_new_decision_uses_lineage_order_not_decision_id_order` and
`test_identical_request_republication_is_idempotent_despite_created_at` are direct regressions for
I-201 and I-202 and both fail against the iteration-2 code;
`test_folded_worker_reviewer_disagreement_fails_closed` pins I-205 by message; and
`test_publication_failure_is_durable_and_reader_exposes_it` is the first test in the repository to
assert on a durable OS-30 artifact error, though it covers only the `E2EHarness` seam and not the
`OrcaRuntimeHarness` one (I verified the latter by execution). The seam test's cardinality half is
real. Its zero-delta half is I-301. `test_published_fixture_files_are_exercised` closes the
fixture-wiring half of I-204 for the valid fixture only (N-302).

## Evidence Checked

- Live Jira OS-30 (`getJiraIssue`, `luminous419.atlassian.net`): Goal, Scope, all nine Acceptance
  Criteria, Dependencies (OS-28 and OS-29's `NEEDS_INPUT`/`CONFLICT` producer contract), and Out of
  Scope (durable resume engine, Slack/Jira/GitHub approval UI, org-specific option catalogs).
  AC-by-AC: AC1 stable structured request verified through both seams; AC2 `_validate_item` requires
  1..8 options and `recommended_option_id` in the option set; AC3/AC4 `default_applicable: false`
  and `on_timeout: "no selection; run remains blocked"` with no default-application path anywhere;
  AC5 `raw_response.txt` plus the normalized decision record; AC6 bounded re-clarification capped at
  two revisions; AC7 supersession and cancellation are append-only and no decision file is deleted;
  AC8 the CLI has no `input(`, no `orca orchestration ask`, and `--help` runs from the installed
  copy; AC9 the sensitive canary reaches only `raw_response.txt` (mode 0600) and the new durable
  failure row carries an exception class name and nothing else.
- Approved `DESIGN.md` iteration 4 §3, §5, §9, §12, its Testing Strategy and Resolution Trace, and
  `REVIEW_DESIGN_iteration4.md`'s PASS-with-notes verdict.
- `REVIEW_IMPLEMENTATION_iteration2.md` findings I-201 through I-205 and notes N-201 through N-205,
  each re-derived rather than assumed.
- `IMPLEMENTATION.md` iteration 3 Changes, Finding Closure, Validation Evidence, decision record.
- `ORCHESTRATOR_LOG.md` and `TIMING_LOG.md`: implementation worker iteration 3 settled `completed`
  as a `correction` round with `retained_external_terminal`; no new phase, round or status
  vocabulary appears in either table.
- Complete read of `scripts/clarification_protocol.py` (695 lines) and
  `scripts/test_clarification_protocol.py` (257 lines); full `git diff` of `scripts/run_logging.py`,
  `scripts/e2e_harness.py`, `scripts/orca_runtime_harness.py`, `scripts/release_manifest.py`,
  `scripts/test_release_package.py` and `scripts/test_validate_skills.py`.
- OS-28/OS-29 boundary sources read directly: `decision_gate.ledger_key`, `RECORD_IDENTITIES`,
  `CLOSED_LEDGER_RECORD_FIELDS`, `record_identity_defect`, `VERIFIES_FIELDS`;
  `run_logging.read_decision_ledger`, `_published_ledger_keys`, `append_decision_ledger_record`,
  `open_decision_ledger`. `git status --porcelain` shows `decision_gate.py`, `decision_policy.py`,
  `workflow_contract.py` and `skill_policy.py` unmodified.
- Executed seam probes against real appended OS-29 ledgers (run-entry declaration at sequence 0,
  Worker B2 at 1, bound Reviewer B3 at 2) through both concrete
  `_publish_clarifications_for_terminal_block` implementations, in three scenarios each — folded
  agreeing pair, folded disagreeing pair, and missing declaration — plus a republication of each.
- Executed protocol probes: post-cancellation head, `expand_scope` after cancel-then-redecide,
  triple identical `create`, tampered-record conflict, tampered two-head lineage, folded
  disagreement with and without a declaration, and the multi-group blast radius in N-301.
- Ledger-sequence sanity: `append_decision_ledger_record` allocates from 0 and
  `_ledger_parts` rejects `#0`, but sequence 0 is always the coordinator's B1 run-entry declaration
  (`state: CLEAR`, `open_decision_item: false`), which `terminal_block_sources` filters before any
  key is parsed. Both harnesses call `open_decision_ledger` at run open, so no real B2/B3 judgement
  can land on the rejected sequence. Not a defect.
- Historical artifact preservation: `git status --porcelain artifacts/` reports no modification and
  no deletion of any tracked artifact; every pre-existing run directory and `artifacts/archive/`
  is intact.
- Scope containment: `clarification_protocol.py` imports only the standard library plus the shipped
  `run_logging` redaction-policy constant. Grep over the module for `resume|pause|checkpoint|slack|
  jira|github|webhook|transport|urllib|socket|subprocess|input\(|orchestration ask|dispatch|
  lifecycle_status` matches only the docstring line disclaiming them. The seam diffs add no
  dispatch, no retry, no status value and no iteration mutation; `publish()`'s return value is
  still read by no caller. OS-31 pause/resume and transport expansion are correctly absent.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: The verdict rests on the live Jira OS-30 text, approved DESIGN iteration 4 §3/§9/§12 and
its Testing Strategy, the unmodified OS-28/OS-29 contracts read from `scripts/decision_gate.py` and
`scripts/run_logging.py`, and repository evidence I produced by executing the shipped code in this
worktree. Both blocking findings are reproduced execution results or empty greps, not judgement
calls, and both are producer defects with repository-local, reversible remedies. No user-owned
choice is open.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "The live Jira OS-30 ticket, approved DESIGN iteration 4 sections 3, 9 and 12 with its Testing Strategy, the unmodified OS-28/OS-29 contracts, and directly executed repository evidence fully determine this review verdict; I-201, I-202, I-203 and I-205 were confirmed closed by reproduction, and both blocking findings were reproduced by running the shipped code rather than inferred, so no user-owned choice is open.",
  "iteration": 3,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "implementation",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T09:05:00Z",
  "responsible_phase": "implementation",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer verdict for implementation iteration 3 of Jira OS-30 only, re-deriving findings I-201 through I-205 against approved DESIGN iteration 4, the OS-28/OS-29 contracts, both harness seams, the protocol module, its focused tests and fixtures, and the preservation of historical artifacts, excluding OS-31 resume and transport expansion.",
  "sequence": 10,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_IMPLEMENTATION_iteration3.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": {
    "iteration": 3,
    "phase": "implementation",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/implementation/3/B2#9"
  }
}
```
