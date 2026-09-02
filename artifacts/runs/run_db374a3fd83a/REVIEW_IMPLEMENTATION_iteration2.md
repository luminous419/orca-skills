# Review Result

RESULT: FAIL
IMPLEMENTATION_REVIEW: FAIL

## Summary

All four implementation-iteration-1 gaps are present in the working tree and the two harness
seams, the unconditional validated B3-to-B2 fold, the complete known-item DAG validation, and the
append-only scope-expansion event are all really implemented rather than merely asserted. I
reproduced the terminal-BLOCK seam end to end against a real OS-29 ledger and confirmed that one
Worker B2 plus its bound Reviewer B3 with a different label publishes exactly one request carrying
both ledger keys and the producer's label, that a missing Coordinator declaration publishes nothing
without erroring, and that no OS-28/OS-29 contract file, schema constant, historical run artifact,
or lifecycle vocabulary was touched. Scope is correct for OS-30: there is no resume token, no
response consumer, no dispatch, and no transport in the module or in either seam.

The phase gate nevertheless fails. Direct execution proves that the effective-decision head is
derived from lexicographic decision-ID order rather than from immutable creation order plus replay,
so an explicit user decision made after an explicit cancellation is reported `DECIDED` by `ingest`
and simultaneously reported as no effective decision by `show`, `expand_scope`, and dependency
readiness. Three further defects are contract-alignment failures against the approved DESIGN
iteration 4: identical republication of a terminal-BLOCK request raises
`CLARIFICATION_ID_CONFLICT` instead of being idempotent, publication failure is recorded nowhere,
and neither harness seam has any test at all even though §12/N-006 and the Testing Strategy name
fake-port cardinality and zero-delta tests as the verification anchors for exactly the gap this
iteration was correcting.

## Blocking Findings

### I-201 — Effective-decision head derivation loses an explicit post-cancellation decision

`scripts/clarification_protocol.py:592-605`. `_effective_decision` seeds the head with
`decisions[0]`, where `decisions` is collected by iterating `sorted((root/"decisions").glob(...))`,
i.e. by lexicographic `decision_id` — a truncated SHA-256 digest with no relation to creation
order. The head then advances only through `decision_superseded` / `decision_cancelled` events
whose `prior_decision_id` equals the current head. A decision that no lineage event points at can
therefore never become effective.

Reproduced directly (`ArtifactHumanApprovalPort`, temp base, no harness involved):

```text
d1     DECIDED  decision_938f366f885fbdc8862fafe9
cancel CANCELLED
d2     DECIDED  decision_e21e4b9b36f4d93dad35a862
effective head: None
expected  head: decision_e21e4b9b36f4d93dad35a862
lineage events: ['decision_cancelled']
```

The user cancelled, then explicitly chose `production`. `ingest` returned `DECIDED` with a real
published decision record, and the authority reader returned `None`. The two readings of the same
artifact store disagree.

This violates approved DESIGN §9 — "Effective state is replayed from immutable decisions and valid
events in sequence order. Initially an item is unresolved. A first valid decision becomes
effective" — and it is not a cosmetic reporting defect. `_effective_decision` is the single
authority source for three other behaviours in this change:

- `_validate_known_dag` (`:405-408`) refuses a dependent item whose predecessor is "not effective",
  so a re-decided predecessor permanently blocks every successor item;
- `expand_scope` (`:416-417`) refuses with "scope expansion requires effective decision", so gap 4
  is unreachable for any item that was ever cancelled and re-decided;
- `show` (`:624`) reports `effective_decisions` to the CLI, which is the only response path OS-30
  ships.

The direction of the error is unresolved rather than auto-approved, so no silent approval occurs,
but an explicitly recorded user decision is discarded and the item can never be advanced. That is a
correctness defect in the decision protocol's core state machine.

The existing `test_changed_answer_supersedes_and_cancel_is_append_only` passes only because in the
supersede-then-cancel ordering every lexicographic seeding of the head happens to converge on the
same answer; it never exercises a decision published after a cancellation.

Required: derive the head by replaying immutable records in publication order (lineage sequence and
a recorded creation ordering for decisions), treating a decision with no predecessor event as the
first effective decision when the item is currently unresolved, and reject rather than silently
absorb a fork or a second concurrent head as §9 requires.

### I-202 — Request publication is not idempotent; identical republication fails closed as a conflict

`scripts/clarification_protocol.py:349-356` with `_write_directory` at `:199-206`. `request_id` is
derived from `{items, revision, contract}` only, but the persisted `record.json` also contains
`"created_at": _now()`. `_write_directory` compares the existing directory's bytes against the
bytes it is about to write and raises `ClarificationConflict` on any difference. Because
`created_at` moves on every call, republishing an identical request always mints the same
`request_id` and always fails the byte comparison.

Reproduced directly:

```text
port.create(run_id="run_test", data=<identical input>)   -> CREATED
port.create(run_id="run_test", data=<identical input>)   -> ClarificationConflict:
                                                            identifier conflict: request_7a3496e3d32388e2762612ca
```

Also reproduced through the real seam: calling
`E2EHarness._publish_clarifications_for_terminal_block()` twice on the same ledger yields
`clarification_errors == ['ClarificationConflict']` on the second call.

This contradicts approved DESIGN §12 — "deterministic identity makes retries idempotent" — and the
Testing Strategy line "Repeated create/submission/event with identical content is idempotent;
conflicting reuse fails". It also makes `PublishResult(..., "EXISTING")` and the
`if actual == expected: return False` branch of `_write_directory` unreachable for requests, and it
makes the documented `create` CLI subcommand fail with `CLARIFICATION_ID_CONFLICT` (exit 4) on an
honest retry after a transient failure. The response path at `:495-508` implements exactly the
required semantic-equality comparison; the request path simply does not.

Required: compare the identity-bearing fields rather than raw bytes (or keep `created_at` outside
the compared record), so an identical republication returns `EXISTING` and only a genuine content
divergence raises `ClarificationConflict`.

### I-203 — Publication failure is recorded nowhere, so every fail-closed path is silent

`scripts/e2e_harness.py:1899-1906` and `scripts/orca_runtime_harness.py:2751-2758`. Both seams
catch `Exception` and append `type(exc).__name__` to `self.clarification_errors`. A repository-wide
grep finds no reader of that attribute: it is not in `WorkflowRunResult`, not written to any
artifact, not passed to `run_logging`, and not returned to any caller.

Approved DESIGN §12 states "Publication failure is logged as a closed OS-30 artifact error and the
run remains `BLOCKED`". Only the second half is implemented. The consequence is not theoretical —
it is what conceals I-202 and the bundle failure in the note below. I observed a terminal BLOCK
that published zero clarification requests while reporting nothing anywhere:

```text
D bundle no-independence: requests=0  errors=['ClarificationError']   # in-memory only
```

An operator reading the run artifacts of that BLOCKED run sees no clarification request and no
indication that one was attempted and refused. Because OS-30 AC1 requires `NEEDS_INPUT` to produce
a structured request artifact, an unlogged suppression of that artifact is a contract-alignment
failure on the acceptance criterion itself.

Required: emit the closed OS-30 artifact error through the existing `run_logging` orchestrator-event
path (or the equivalent artifact write) at both seams, keeping the terminal status unchanged.

### I-204 — Neither harness seam has any test, contrary to §12/N-006 and the Testing Strategy

`grep -rn "clarification" scripts/test_e2e_harness.py scripts/test_orca_runtime_contract.py
scripts/test_orca_runtime.py` returns nothing; the only hits anywhere outside
`test_clarification_protocol.py` are packaging and validator file lists in
`scripts/test_release_package.py` and `scripts/test_validate_skills.py`. The twelve tests in
`scripts/test_clarification_protocol.py` all drive `ArtifactHumanApprovalPort` or
`terminal_block_sources` directly; none constructs a harness, none injects a fake port, and none
asserts anything about `_publish_clarifications_for_terminal_block`.

Approved DESIGN §12 requires "The deterministic harness accepts an injected fake port for
assertions", resolution-trace N-006 names "fake-port cardinality and zero-delta tests" as its
verification anchor, and the Testing Strategy's "Harness and regression gates" section requires
"Fake port captures request creation at valid B2/B3 blocks. Missing/invalid request declarations
never create vague requests and never un-block the run" plus the zero-delta snapshot. None of these
exist. The `human_approval_port` and `clarification_inputs` constructor parameters added to both
harnesses have no test that ever passes a non-default value.

This matters specifically because gap 1 — the harness seam — is the gap this iteration was
dispatched to close. Its correctness is currently asserted by `IMPLEMENTATION.md` prose and by my
ad-hoc probes, not by the repository gate. The three unlisted fixture files under
`scripts/fixtures/clarification_protocol/{valid,invalid}/` are likewise referenced by no test
(`grep -rn "fixtures/clarification_protocol" scripts/ --include=*.py` is empty), so the fixture
coverage the Testing Strategy calls for is also absent.

Required: add the fake-port cardinality test, the missing-declaration fail-closed test, and the
zero-delta snapshot test named in §12, and wire the published fixtures into assertions.

### I-205 — Judgements folded onto one identity are not checked for agreement

`scripts/clarification_protocol.py:117-146`. `terminal_block_sources` folds a validated Reviewer B3
onto its Worker B2 producer and then reads `state`, `reason_code`, `phase`, and `iteration`
exclusively from the producer. It never compares the folded records' classifications.

Approved DESIGN §3 states: "Judgements sharing a key must agree on the state/reason and request
contract; disagreement fails closed for Coordinator resolution instead of splitting identity."
Neither behaviour is implemented — the disagreement neither fails closed nor splits identity; it is
silently discarded.

Reproduced with a Worker `NEEDS_INPUT` / `user_choice_required` and its validly bound Reviewer B3
declaring `CONFLICT` / `conflicting_instructions`:

```text
sources: 1  state=NEEDS_INPUT  reason=user_choice_required
keys=('run_dis/implementation/1/B2#1', 'run_dis/implementation/1/B3#2')
```

The published request tells the user this is a `NEEDS_INPUT` choice while the Reviewer's
authoritative record says the two agents are in `CONFLICT` — a materially different question, with
a different reason code, presented to the human as settled. The Reviewer's disagreement survives
only as an opaque ledger key inside `source_ledger_keys`. Fail-closed here is the approved
behaviour precisely because the Coordinator, not OS-30, owns that resolution.

Required: before emitting a `ClarificationSource`, verify that every record folded into the group
agrees with the producer on `state` and `reason_code`, and refuse the group (recorded through
I-203's logging path) when it does not.

## Non-Blocking Findings

- **N-201 — No antichain selection or dependency ordering in the harness adapter.**
  `ArtifactHumanApprovalPort.publish` (`:308-318`) forwards every source into a single
  `_publish_items` bundle. DESIGN §5 and §12 require the adapter to sort the dependency-ready set by
  `decision_item_id` and take at most three items from the first independent antichain, holding
  dependent items until their predecessors are effective. With four or more open groups the
  publication raises `bundle: requires 1..3 items` and nothing is published. OS-29 A5 admits only a
  single verification Reviewer while an item is open, so more than two open groups is not reachable
  in practice today, which is why this is a note rather than a blocker — but the adapter's shape
  does not match the approved design and would fail as soon as that assumption changes.

- **N-202 — Bundle metadata is taken from the last source's item body.** `publish` sets
  `request_meta = source.request_input` inside the loop (`:317`), so a genuine multi-item bundle is
  published with `bundle_rationale: ""` and the default `independence_declared_by`, since an item
  dict has none of those keys. `ITEM_INPUT_FIELDS` cannot carry bundle-level metadata, so the
  bundle contract is silently defaulted rather than declared.

- **N-203 — `read_decision_ledger` is called outside the `try` in both seams.**
  `scripts/e2e_harness.py:1891` and `scripts/orca_runtime_harness.py:2745`. `read_decision_ledger`
  degrades a malformed record to a sentinel and a missing directory to `[]`, so this is very
  unlikely to raise, but an escape here would propagate out of
  `_publish_clarifications_for_terminal_block` and, in `orca_runtime_harness.log_run_status`,
  prevent the `BLOCKED` status write that immediately follows it. Moving the read inside the `try`
  makes the "publication cannot alter lifecycle state" claim structural rather than probabilistic.

- **N-204 — `_effective_decision` performs almost none of the §9 reader validation.** Beyond JSON
  parseability and an item-ID filter it does not reject unsupported versions, missing or extra
  fields, sequence/path mismatch, missing targets, cross-run links, self-links, forks, more than one
  current head, duplicate event IDs with differing content, or out-of-order predecessor references.
  §9 requires all of these, and requires one malformed event to make the item `invalid` rather than
  fall back to an older authority. Folded here as a note because I-201 already requires this
  function to be rewritten.

- **N-205 — CHANGELOG entry placement.** The `## Unreleased` heading is appended at the end of
  `CHANGELOG.md`, directly after a bullet with no blank line, below the older released sections. The
  file's own Keep-a-Changelog framing puts unreleased work at the top.

## Test Review

I re-ran every gate myself in the worktree.

```text
PYTHONPATH=. python3 -m unittest scripts.test_clarification_protocol
Ran 12 tests in 0.096s -- OK

PYTHONPATH=. python3 -m unittest scripts.test_clarification_protocol scripts.test_e2e_harness \
  scripts.test_orca_runtime_contract scripts.test_orca_runtime scripts.test_release_package \
  scripts.test_validate_skills
Ran 684 tests in 76.142s -- OK (skipped=6)

PYTHONPATH=. python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1669 tests in 316.279s -- OK (skipped=6)

python3 scripts/validate_skills.py
Skill validation PASSED (697 checks)

python3 scripts/verify_package.py
Package verification PASSED (195 source files)

diff -q scripts/clarification_protocol.py \
        orca-worker-reviewer-orchestration/tools/clarification_protocol.py
(no output -- byte-identical)

git diff --check
(clean)
```

The Worker's reported validation evidence reproduces exactly. The suite is green, and that is the
problem: it is green while I-201, I-202 and I-205 are all reproducible in a few lines against the
shipped public API, and while the seam that closes gap 1 has no test at all (I-204). The twelve
tests in `scripts/test_clarification_protocol.py` are well chosen for the paths they cover —
cross-label folding, unknown dependency, cycle, scope-expansion child identity and edges, retained
parent head, sensitive-canary containment, bounded re-clarification, stale revision — but they cover
only happy-path orderings of the lifecycle. Two specific holes let the blocking defects through:
`test_changed_answer_supersedes_and_cancel_is_append_only` never publishes a decision *after* a
cancellation, and no test calls `create` twice with identical content, which is the exact case
DESIGN's Testing Strategy names as required.

## Evidence Checked

- Live Jira OS-30 (`getJiraIssue`, `luminous419.atlassian.net`): Goal, Scope, all nine Acceptance
  Criteria, Dependencies (OS-28, OS-29 `NEEDS_INPUT`/`CONFLICT` producer contract), and Out of
  Scope (durable resume engine; Slack/Jira/GitHub approval UI; org-specific option catalogs).
- Approved `DESIGN.md` iteration 4 §1-§13, Testing Strategy, Risks, and the Resolution Trace rows
  F-001 through F-005 and N-001 through N-006.
- `REVIEW_DESIGN_iteration4.md` PASS-with-notes verdict and its decision record.
- `IMPLEMENTATION.md` iteration 2 Changes, Contract Evidence, Validation Evidence, decision record.
- `ORCHESTRATOR_LOG.md` and `TIMING_LOG.md`: iteration 1 `failed: four approved-design gaps remain`,
  iteration 2 `completed correction`, `retained_external_terminal`, no phase/round vocabulary change.
- Full `git diff` of `scripts/e2e_harness.py` and `scripts/orca_runtime_harness.py`, read in place
  with surrounding context at `e2e_harness.py:1890-1950` and `orca_runtime_harness.py:2650-2760`.
- Complete read of `scripts/clarification_protocol.py` (662 lines) and
  `scripts/test_clarification_protocol.py` (175 lines).
- OS-29 boundary sources: `scripts/decision_gate.py` `ledger_key`, `VERIFIES_FIELDS`,
  `verification_admission_defect`, `admit_head`; `scripts/run_logging.py`
  `read_decision_ledger` / `_published_ledger_keys` / `append_decision_ledger_record`. All
  unmodified — `git status` shows no change to `decision_gate.py`, `decision_policy.py`, or
  `run_logging.py`.
- End-to-end seam probes I wrote and ran against a real appended OS-29 ledger through
  `E2EHarness._publish_clarifications_for_terminal_block`: folded B2+B3 cross-label pair (one
  request, both keys, producer label), repeated publication (conflict), missing declaration (no
  request, no error), partially declared multi-group set (one request), cross-iteration reviewer
  binding rejected (own group).
- Protocol probes: post-cancellation decision head, identical re-`create`, disagreeing folded
  Worker/Reviewer classification.
- Historical artifact preservation: `git status --porcelain` shows zero deletions and zero
  modifications under `artifacts/`; all pre-existing run directories and
  `artifacts/archive/` are intact. The untracked repository-root `e2e_harness.py` predates this run
  (mtime 03:17 vs. run start 06:37) and is not attributable to this iteration.
- Scope containment: `clarification_protocol.py` imports only `argparse`, `dataclasses`,
  `datetime`, `hashlib`, `json`, `os`, `re`, `stat`, `sys`, `tempfile`, `unicodedata`, `pathlib`,
  `typing` plus the shipped `run_logging` redaction-policy constant. No Orca import, no
  `orchestration ask`, no `input(`, no network, no subprocess, no resume token, no response
  consumer, and no caller of `publish()`'s return value in either harness. OS-31 pause/resume and
  transport expansion are correctly absent.
- Documentation and packaging diffs: `CHANGELOG.md`, `INSTALL.md`, `README.md`,
  `docs/COMPATIBILITY.md`, `docs/ROADMAP.md`, both `SKILL.md` files, `scripts/release_manifest.py`,
  `scripts/test_release_package.py`, `scripts/test_validate_skills.py` — all consistent with the
  approved scope and all correctly disclaiming resume and transports.

## Gap Closure Assessment

| Iteration-1 gap | Implemented | Verified by me | Blocking defect |
| --- | --- | --- | --- |
| 1. Single terminal-BLOCK publication seam in both harnesses via injected runtime-neutral port | Yes | Yes — one request from a folded B2/B3 pair; missing declaration publishes nothing; publication never resumes, dispatches, or mutates status | I-202, I-203, I-204 |
| 2. Unconditional validated B3-to-B2 fold before label selection | Yes | Yes — cross-label pair yields one item with the producer's label and both keys | I-205 |
| 3. Complete persisted-plus-incoming DAG validation and effective-predecessor readiness | Yes | Yes — unknown node, self cycle, and cross-node cycle all rejected | I-201 (readiness reads the broken head) |
| 4. Scope expansion as immutable child identities plus explicit dependency edges | Yes | Yes — child IDs, exact edges, `decision_scope_expanded`, parent head retained | I-201 (gating reads the broken head) |

Every gap is genuinely addressed. Gaps 3 and 4 are correct in their own logic but rest on the
defective head derivation in I-201, and gap 1's seam carries the three contract-alignment failures
above.

## Final Decision

FAIL. The implementation matches OS-30's ticket scope and correctly excludes OS-31 resume and
transport work, the OS-28/OS-29 contracts and every historical artifact are intact, and all four
iteration-1 gaps are really implemented. It does not pass the implementation phase gate: I-201 is a
reproducible correctness defect that discards an explicit user decision and permanently strands
dependent items and scope expansion, and I-202 through I-205 are contract-alignment failures
against named clauses of the approved DESIGN iteration 4 (§3 agreement, §9 replay, §12 idempotency,
§12 failure logging, §12/N-006 harness tests).

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: The verdict is fully determined by the live Jira OS-30 text, the approved DESIGN
iteration 4 §3/§5/§9/§12 and its Testing Strategy, the OS-28/OS-29 contracts read directly from
`scripts/decision_gate.py` and `scripts/run_logging.py`, and repository evidence produced by my own
execution in this worktree. Every blocking finding rests on a reproduced execution result or on an
empty grep over the repository, not on a judgement call. No user-owned choice arose: all five
blocking findings are producer defects with repository-local, reversible remedies.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "The live Jira OS-30 ticket, the approved DESIGN iteration 4 sections 3, 5, 9 and 12 with its Testing Strategy, the unmodified OS-28/OS-29 contracts, and directly executed repository evidence fully determine this review verdict; each blocking finding was reproduced by running the shipped code rather than inferred, and no user-owned choice is open.",
  "iteration": 2,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "implementation",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T08:35:00Z",
  "responsible_phase": "implementation",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer verdict for implementation iteration 2 of Jira OS-30 only, verifying the four implementation-iteration-1 gaps against approved DESIGN iteration 4, the OS-28/OS-29 contracts, both harness seams, the protocol module, its focused tests and fixtures, and the preservation of historical artifacts, excluding OS-31 resume and transport expansion.",
  "sequence": 8,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_IMPLEMENTATION_iteration2.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": {
    "iteration": 2,
    "phase": "implementation",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/implementation/2/B2#7"
  }
}
```
