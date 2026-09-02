# FINAL ADVERSARIAL REVIEW — Jira OS-30 Structured Human Clarification and Decision Protocol

RUN: run_db374a3fd83a
REVIEWER: claude-opus (fresh session, Final Adversarial Review iteration 1 of max 5)
DELTA REVIEWED: working tree vs `main` (branch has zero commits ahead; `git diff main --stat` = 15 files, +246/-3, plus 7 untracked source/fixture files)

RESULT: FAIL

REVIEW_VERDICT: FAIL

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "Final Adversarial Review of Jira OS-30 FAILS on four independently reproduced blocking findings. FA-001: with two or more open decision items at terminal BLOCKED — the ordinary multi-question case OS-30 exists to serve — the harness adapter publishes ZERO clarification requests. ArtifactHumanApprovalPort.publish() concatenates every ClarificationSource into one request without implementing DESIGN section 5's 'the adapter takes at most three items from the first independent antichain' and without deriving independent_with, so symmetric-independence validation rejects the whole publication; the failure is swallowed by a bare except Exception that records only the string 'ClarificationError'. I reproduced this for n=2 and n=4 open items through the real E2EHarness seam: 0 requests published in both cases. FA-002: even when a bundle is published by hand, it is permanently unanswerable — _normalize() raises for every request whose item count is not 1, so --option-id, --response-file and --cancel all fail on a 2-item bundle I published successfully. Response record v1 (DESIGN section 6) and the respond CLI (section 11) carry no per-item designator, so the design itself never specified how a bundled request is answered. FA-003: persisted decision and lineage records are consumed with bare json.loads and .get() with no closed-schema, version, content-hash or reconciliation check, contradicting DESIGN section 7 ('No decision is valid unless its response, request, raw digest, item, and source key reconcile') and section 9 ('Readers validate every event before applying any event'). On a legitimately decided request I forged one decision record and rewrote one lineage event, and show() then reported the human had authorized 'deploy to production' when they had authorized 'deploy to staging'. This is the T-001 defect class on the artifact that carries MORE authority than the request, it is new code in this delta, and DESIGN specifies the missing validation for THIS ticket — deferring it as pre-existing and out of charter is not correct. FA-004: the CLI create path never verifies the OS-29 source record; clarification_protocol.py never imports run_logging.read_decision_ledger at all, contradicting DESIGN section 11 ('through read-only run_logging.read_decision_ledger for CLI use. Only a valid open B2/B3 NEEDS_INPUT/CONFLICT source is eligible'). I published an authority-bearing request bound to the fabricated key run_ghost/implementation/9/B2#7 in a run with no decision ledger, exit code 0. Test review: the bundle happy path has zero passing coverage anywhere in the repository — every create() call in the focused suite and every clarification_inputs mapping in test_e2e_harness.py uses exactly one item — so TEST.md's 'Independent bundles / dependent ordering — PASS' acceptance row is unsupported by any executed test. The claimed validation evidence that I re-ran does reproduce (focused suite 21 tests plus 18 subtests, source/installed byte parity confirmed). No user-owned decision is open: all four findings have objective required actions determined by the run's own approved DESIGN, so the decision gate is CLEAR and the correct route is a correction loop, not a question to the user.",
  "iteration": 1,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "final_review",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T21:45:00+09:00",
  "responsible_phase": "design",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Final Adversarial Review for Jira OS-30 across axes A-J: independent re-derivation of objective alignment, cross-phase consistency, contract vs implementation, implementation vs tests, docs vs behavior, lifecycle state machine, security/destructive change, over-engineering, hidden coupling, and decision provenance. Excludes fixing any finding, OS-31 resume and transport work, PR creation, merge, and Jira status changes.",
  "sequence": 21,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/FINAL_REVIEW.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": {
    "iteration": 2,
    "phase": "test",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/test/2/B2#19"
  }
}
```

---

## Summary

I re-derived every axis independently rather than accepting the five phase-gate PASSes. The delta is
genuinely strong on the axis every previous reviewer concentrated on — the **request** artifact. After
T-001, `_validate_request_record()` is a real closed, version-aware, content-hash-re-deriving
validator, it is wired into all four persisted-request consumers, and I confirmed source/installed
byte parity and that the focused suite passes as claimed.

The failure is on the axes nobody exercised end to end. **OS-30 works for exactly one open decision
item and silently does nothing for two.** Bounded independent bundles are not an optional nicety
here: they are named in the Jira scope carried into ANALYSIS §"Independent questions may be grouped
only into a limited bundle", PLAN work items 2 and 8, DESIGN §5 and §12, and the shipped
`orca-worker-reviewer-orchestration/SKILL.md` text "Bundles contain at most three explicitly
independent, dependency-ready items." Three separate defects sit on that path:

- the adapter cannot **produce** a bundle (FA-001) — it fails closed and publishes nothing at all;
- a bundle that is produced cannot be **answered** (FA-002) — every response mode raises;
- and there is **no test anywhere** that successfully publishes a multi-item request, which is why
  five phase gates passed over it.

Separately, the tamper-resistance work stopped one artifact short. The request is now validated; the
**decision** and the **lineage event** — the artifacts that actually carry the human's authority —
are not, and neither is the OS-29 source binding on the CLI `create` path. Both gaps are specified as
requirements in this run's own approved DESIGN (§7, §9, §11), so they are contract-vs-implementation
violations for OS-30, not successor-ticket work.

I set `DECISION_GATE_STATE: CLEAR` deliberately. Nothing here needs a user choice — the required
actions are all determined by the approved DESIGN. This is a correction loop, not a NEEDS_INPUT.

### On the carried-in judgement I was asked to form my own view about

I **disagree** with deferring **T2-002** to a successor ticket, and FA-003 below supersedes it as
blocking. The reasoning in `REVIEW_TEST_iteration2.md` — "same class as T-001 one artifact type over
but was outside T-001's scope, is unchanged by this delta" — does not hold up on two points.
`scripts/clarification_protocol.py` is a **new file in this delta**; nothing in it is pre-existing.
And `DESIGN.md` §9 states, for this ticket, "Readers validate every event before applying any event.
They reject unsupported versions, missing/extra fields, sequence/path mismatch... duplicate event IDs
with different content... One malformed published event makes the affected item `invalid`; it is
never skipped to recover an older authority." That is an unimplemented requirement of the approved
design, which makes it G1, and its effect is forging the human's authority, which makes it G4.

I **agree** with the existing non-blocking classification of N-601, T2-001 and T2-003, and I confirmed
T2-001 by execution.

---

## Blocking Findings

### FA-001 — Two or more open decision items at terminal BLOCKED publish zero clarification requests

- **ID:** FA-001
- **Quality Attribute:** explicit requirement (Jira OS-30 bounded independent bundles) — G1, G2
- **Severity:** CRITICAL
- **Blocking:** YES
- **Responsible Phase:** implementation
- **Location:** `scripts/clarification_protocol.py:386-400` (`ArtifactHumanApprovalPort.publish`);
  `scripts/e2e_harness.py` `_publish_clarifications_for_terminal_block`;
  `scripts/orca_runtime_harness.py:2745-2771` (same method)
- **Issue:** `publish()` merges **every** `ClarificationSource` into a single `_publish_items()` call.
  It performs none of the adapter behavior DESIGN §5 specifies — "A dependency-ready set is sorted by
  `decision_item_id`; the adapter takes at most three items from the first independent antichain" —
  and it never derives `independent_with`. `independent_with` can only arrive from the per-source
  coordinator declaration, which is keyed by one ledger key and therefore cannot name its peers'
  content-derived item IDs. `_publish_items()` then rejects the whole batch with
  `"bundle: symmetric independence required"`. With four or more sources it rejects with
  `"bundle: requires 1..3 items"` instead, because no antichain partitioning ever splits them into
  multiple requests. Either way the exception is caught by the adapter's bare `except Exception`,
  which appends only `type(exc).__name__` and logs `detail=ClarificationError` — no message, no
  ledger keys, no item count.
- **Reason / Evidence:** Reproduced through the real `E2EHarness` seam with a real
  `ArtifactHumanApprovalPort` and a well-formed per-key declaration for every open item:

  ```text
  n=2 open items -> requests published: 0  errors: ['ClarificationError']
     durable error rows: ['ClarificationError']
  n=4 open items -> requests published: 0  errors: ['ClarificationError']
     durable error rows: ['ClarificationError']
  ```

  The single-item case publishes correctly, which is why every existing test passes: the focused
  suite's `create()` helper and `test_e2e_harness.py:5367-5402`'s `clarification_inputs` mapping both
  use exactly one item. The user-visible outcome is the exact failure OS-30 was written to prevent —
  the run ends `BLOCKED` having asked the human **nothing**, and the only durable trace is a bare
  exception class name that cannot be diagnosed.
- **Required Action:** Implement the DESIGN §5 adapter behavior in the OS-30 seam: sort the
  dependency-ready set by `decision_item_id`, select the first mutually independent antichain of at
  most `MAX_BUNDLE_ITEMS`, derive `independent_with` for the selected members (the adapter is a
  declared producer of that field per DESIGN §5), and publish the remaining ready items as further
  requests rather than dropping them. Widen the swallowed error detail to carry the exception message
  and the affected ledger keys so a failed publication is diagnosable. Add a test that asserts a
  terminal BLOCKED with 2, 3 and 4 open decision items publishes a non-zero, correct number of
  requests covering every open item.

### FA-002 — A bundled request can be published but can never be answered by any response mode

- **ID:** FA-002
- **Quality Attribute:** explicit requirement (Jira OS-30 bounded independent bundles) — G1, G2
- **Severity:** CRITICAL
- **Blocking:** YES
- **Responsible Phase:** design
- **Location:** `artifacts/runs/run_db374a3fd83a/DESIGN.md` §6 (response record v1 field table) and
  §11 (`respond` CLI contract); realized at `scripts/clarification_protocol.py:625-627`
  (`_normalize`, `raise ClarificationError("respond: bundled response requires one item request")`)
- **Issue:** DESIGN §5 and §12 require requests carrying 1..3 items, and DESIGN §6 defines the
  response record as a closed schema whose only request-side binding fields are `request_id` and
  `request_revision`. There is **no `decision_item_id`, item index, or any other per-item
  designator** in the response record, and DESIGN §11's `respond` CLI likewise offers only
  `--request-id`. So the design never specifies which item of a bundle a response answers, and the
  design as written cannot express it. The implementation faithfully encodes that gap as a hard
  error for any request whose item count is not 1 — including `--cancel`, which is a lifecycle
  instruction that should never have depended on item count.
- **Reason / Evidence:** I published a valid, fully independence-declared 2-item bundle through the
  supported `create()` API and then attempted every accepted response mode:

  ```text
  A publish 2-item bundle: CREATED ('request_ebfbb6ad9fda0ab955afbf9f',) 2
  A respond FAILED: ClarificationError respond: bundled response requires one item request
  A cancel  FAILED: ClarificationError respond: bundled response requires one item request
  A show:   OK
  ```

  `show()` renders the bundle to the human, so a human is presented with a question that no CLI
  invocation can ever resolve; the item stays unresolved forever and the run stays `BLOCKED`. This
  contradicts the shipped `orca-worker-reviewer-orchestration/SKILL.md`, which documents both the
  three-item bundle and the `respond --request-id ID` command as working behavior (axis E), and it
  contradicts DESIGN §9's lifecycle, which assumes every published item can reach an effective head.
- **Required Action:** Close the design gap first, then implement it. Either (a) add a per-item
  designator to response record v1 and a corresponding `respond` argument, bumping the response
  schema version and specifying the bundle normalization/lineage rules per item; or (b) make the
  decision explicit that a request carries exactly one item, remove `MAX_BUNDLE_ITEMS`, `bundle_id`,
  `bundle_rationale`, symmetric-independence validation and the bundle language from ANALYSIS, PLAN,
  DESIGN, `README.md`, `docs/ROADMAP.md` and both `SKILL.md` files, and record that scope reduction
  as an explicit decision event — bundles are named in the Jira scope, so dropping them is a
  requirement change, not an implementation choice, and must be routed accordingly. Whichever is
  chosen, add a test that answers a multi-item request end to end (or asserts single-item-only is the
  contract) so this path stops being untested.

### FA-003 — Forged decision and lineage records are accepted; `show()` reports an authority the human never gave

- **ID:** FA-003
- **Quality Attribute:** decision authority integrity / artifact tamper-resistance — G1, G4
- **Severity:** CRITICAL
- **Blocking:** YES
- **Responsible Phase:** implementation
- **Location:** `scripts/clarification_protocol.py:682-711` (`_effective_decision`); read sites at
  lines 682-683 (`decisions/decision_*<REDACTED:foreign_absolute_path>`) and 689-690 (`lineage/*<REDACTED:foreign_absolute_path>`); also
  `scripts/clarification_protocol.py:588-595` (the `ingest` replay branch, which adopts an
  unvalidated persisted response record and its `decision_id`)
- **Issue:** After T-001, requests are validated by a closed, version-aware, content-hash-re-deriving
  validator. Decisions and lineage events get none of it. `_effective_decision()` reads both with
  bare `json.loads` plus `.get()` and checks only `decision_item_id` equality, `event_type`, and
  prior/next linkage. It never checks `schema`/`schema_version`, never rejects unknown or missing
  fields, never re-derives `decision_id` from `H(response_id, normalized)` or `event_id` from the
  canonical event body, never checks the event's `run_id` or its `sequence` against its directory
  name, and never reconciles a decision against its response, request, raw digest, item and source
  key. Both identifiers are content-derived and therefore fully checkable; the checks are simply
  absent.
- **Reason / Evidence:** This directly violates two named requirements of this run's own approved
  design. DESIGN §7: *"No decision is valid unless its response, request, raw digest, item, and
  source key reconcile."* DESIGN §9: *"Readers validate every event before applying any event. They
  reject unsupported versions, missing/extra fields, sequence/path mismatch, missing targets,
  cross-run or cross-item links, self-links, cycles, forks, more than one current head, duplicate
  event IDs with different content, out-of-order predecessor references... One malformed published
  event makes the affected item `invalid`; it is never skipped to recover an older authority."*
  On a legitimately decided request I added one forged decision directory and rewrote
  `next_decision_id` in the one existing lineage event:

  ```text
  human chose:              {'action': 'deploy to staging',    'option_id': 'staging'}
  effective before tamper:  decision_ed3e04af87e30260629d2769
  effective after tamper:   decision_ffffffffffffffffffffffff
  served option:            {'option_id': 'production', 'action': 'deploy to production'}
  ```

  `show()` — the operator-facing view of what the human authorized — now reports "deploy to
  production" for a human who chose "deploy to staging", with no error and no invalidation. The
  decision artifact carries strictly **more** authority than the request artifact whose identical
  weakness was correctly ruled blocking as T-001, so ruling this one non-blocking is inconsistent.
  `clarification_protocol.py` is new in this delta, so "pre-existing" does not apply.
- **Required Action:** Give decisions and lineage events the same treatment requests received: closed
  field sets, exact `schema`/`schema_version` match, `decision_id` and `event_id` re-derived from
  content and compared, `run_id` and `sequence`-vs-path checks, and the DESIGN §7 reconciliation of
  decision against response/request/raw digest/item/source key. Per DESIGN §9, one malformed event
  must mark the item invalid rather than silently falling back to an older head. Apply the same
  validation to the persisted response record adopted in the `ingest` replay branch. Extend the
  tamper matrix to decisions and lineage events, including the forged-decision and rewritten-event
  cases above.

### FA-004 — CLI `create` never verifies the OS-29 source record; requests publish against fabricated ledger keys

- **ID:** FA-004
- **Quality Attribute:** decision provenance / source binding integrity — G1, G4
- **Severity:** MAJOR
- **Blocking:** YES
- **Responsible Phase:** implementation
- **Location:** `scripts/clarification_protocol.py:748-754` (`main()`, the `create` branch) and
  `scripts/clarification_protocol.py:398-402` (`ArtifactHumanApprovalPort.create`); absence of any
  `run_logging.read_decision_ledger` import at `scripts/clarification_protocol.py:23-26`
- **Issue:** DESIGN §11 states: *"It verifies the named published OS-29 record through an
  adapter-supplied record for library use and through read-only `run_logging.read_decision_ledger`
  for CLI use. Only a valid open B2/B3 `NEEDS_INPUT`/`CONFLICT` source is eligible."* The library
  path satisfies this — `terminal_block_sources()` is handed the authoritative record set. The CLI
  path does not. `main()` only checks `sorted(--ledger-key values) == sorted(input item
  source_ledger_keys)`, which compares the input file against itself and verifies nothing external;
  `--ledger-key` is `action="append", default=[]`, so it is optional and the check is skipped
  entirely when omitted. `_ledger_parts()` validates only the key's *syntax* and run prefix. The
  module never imports `read_decision_ledger` at all — `grep read_decision_ledger
  scripts/clarification_protocol.py` returns nothing.
- **Reason / Evidence:** Executed against a run directory with no decision ledger whatsoever:

  ```text
  $ python3 scripts/clarification_protocol.py create --artifact-base $T --run-id run_ghost \
      --ledger-key "run_ghost/implementation/9/B2#7" --input $T/in.json
  {"item_ids":["item_b9699d185af99c90887e3e5e"],"operation":"create",
   "request_ids":["request_e887297dd5cb51f19c294648"],"schema_version":1,"status":"CREATED"}
  exit=0
  ```

  The published request claims a blocking `NEEDS_INPUT` at `run_ghost/implementation/9/B2#7`, an
  OS-29 record that does not exist, and asks the human "Approve deleting production data?". Every
  downstream artifact — the decision, `resolves`, `source_ledger_key` — then inherits a source
  binding to nothing. The whole point of binding a clarification to an OS-29 ledger key is that the
  question provably originated from a real blocked run; unverified, an OS-30 request can manufacture
  the appearance of a legitimate run-blocking decision point.
- **Required Action:** Implement the DESIGN §11 CLI verification: read the run's OS-29 decision
  ledger read-only, confirm each item's `source_ledger_key` resolves to a published record that is
  B2 or B3 with `open_decision_item: true` and state `NEEDS_INPUT` or `CONFLICT`, and confirm the
  item's `phase`, `iteration`, `source_state` and `source_reason_code` match that record. Make
  `--ledger-key` required for CLI `create`, or remove it as misleading. Fail closed with the existing
  `CLARIFICATION_INVALID` / `SOURCE_BINDING_MISMATCH` vocabulary and add a test covering a fabricated
  key, a cross-run key, a closed (non-open) record, and a state/reason mismatch.

---

## Non-Blocking Findings

### FA-N01 — Stray untracked `e2e_harness.py` at the repository root (axis G / axis H)

`<REDACTED:foreign_absolute_path>` is untracked, is **not** in `.gitignore`, is not tracked on `main`, and is not
listed as a deliverable in `IMPLEMENTATION.md` or DESIGN §12 (which names `scripts/e2e_harness.py`).
It is a **stale duplicate** of `scripts/e2e_harness.py`: 2,375 lines vs 2,434, `mtime 03:17` vs
`18:38`, and `diff` shows it predates both the `source_binding` harness-ownership fix and the entire
OS-30 seam. Its mtime is earlier than this run's first artifact (`ANALYSIS.md`, 15:53), so **this run
did not create it** — it is pre-existing working-tree debris, which is why I am not treating it as a
defect in the OS-30 delta. Nothing imports it (`grep` finds only `scripts.e2e_harness` imports plus
one bare import inside an unrelated archived run's prototype), so it does not currently shadow
anything.

It must nonetheless **not be committed**. `git add -A` during PR creation would add a top-level
`e2e_harness.py` that shadows nothing today but is importable as a bare module name, and whose
content is a pre-review snapshot including the `source_binding` `setdefault` that a prior external
review flagged as a MAJOR issue. **Required action for the PR step:** stage explicitly (the 15
tracked files plus the 6 OS-30 source/fixture paths); do not `git add -A`. Whether `artifacts/runs/`
is committed remains a human decision, as stated in the dispatch.

### FA-N02 — Carried-in findings, independently confirmed

- **N-601** (validator looser than DESIGN §4 on the revision upper bound and two text bounds) —
  confirmed: `_validate_request_record` applies `_strict_int(request["revision"])` with no upper
  bound, while `_reclarify` enforces `MAX_RECLARIFICATION_REVISIONS`. Conveys no additional authority
  and fails safe. Agreed non-blocking.
- **T2-001** (inert `oversized_bundle` fixture) — confirmed by reading
  `scripts/fixtures/clarification_protocol/invalid/oversized_bundle.json` (`base_fixture` items carry
  `run_fixture/...` keys, test calls `create(run_id="run_oversized")`, so `_ledger_parts` rejects
  before `repeat_items: 4` reaches the bound). The bound is genuinely covered by the four-item
  assertion in `test_bundle_bound_and_independence`. Agreed non-blocking. Note that the first
  assertion in that same test is also inert for the stated reason — it raises on missing symmetric
  independence, not on the bundle bound.
- **T2-003** (matrix asserts `ClarificationError` rather than the declared `CLARIFICATION_INVALID`
  code) — confirmed at `scripts/test_clarification_protocol.py:199-200`. Agreed non-blocking.
- **T2-002** — **not** agreed. Promoted to blocking as FA-003; see Summary.

### FA-N03 — `ORCHESTRATOR_LOG.md` `decision_state` column is blank for the analysis, plan and design rows

Rows for `analysis`, `plan` and `design` (lines 4-17) leave `decision_state` and
`decision_reason_code` empty, while every `implementation` and `test` row carries `CLEAR`. Every row
does record `gate_result`, `review_verdict`, phase, role and iteration, and the recovery of the
killed `ctx_38df4ba6d272` is documented in full with the axis decomposition and the `--retry-of`
replacement, so axis J is otherwise satisfied. Non-blocking log-completeness note only.

---

## Test Review

**What holds up.** I re-ran `scripts/test_clarification_protocol.py`: 21 tests, 18 subtests, all
pass in 0.20s. Source/installed byte parity is real (`diff scripts/clarification_protocol.py
orca-worker-reviewer-orchestration/tools/clarification_protocol.py` is empty), and the packaging
wiring is correct — `release_manifest.py` adds `tools/clarification_protocol.py` to the orchestration
skill's required paths and `test_validate_skills.py` copies it into the validator fixture. The
T-001 negative matrix is genuinely executable: `recommended_default.json` encodes twelve concrete
mutations applied to a real published record, the test runs both `show()` and `ingest()` and asserts
whole-tree byte equality before and after, and `REVIEW_TEST_iteration2.md` independently proved
mutation sensitivity by weakening the validator. That is good work and I am not disturbing it.

**Where the tests do not verify real risk (axis D).** The bundle path — an explicit Jira scope item
— has **no passing coverage at all**. Every `create()` call in the focused suite goes through a
helper that defaults to a single item; the only multi-item calls are two `assertRaises` cases; and
every `clarification_inputs` mapping in `scripts/test_e2e_harness.py` (line 5367 onward) has exactly
one key. There is no test in the repository that successfully publishes a 2- or 3-item request, and
therefore none that answers one. This is not a weakened test — it is an absent one, and it is the
direct reason FA-001 and FA-002 survived five phase gates. `TEST.md`'s acceptance row *"Independent
bundles / dependent ordering — bundle-bound, known-DAG, scope-expansion tests — PASS"* is not
supported by any executed test: the bundle-bound test asserts only rejections, and the known-DAG and
scope-expansion tests are single-item.

The tamper matrix is similarly scoped to one artifact type. It covers the request thoroughly and the
decision and lineage records not at all, which is what FA-003 exploits.

`REVIEW_TEST_iteration2.md` is otherwise a strong review — the 23-case matrix, the four independent
production weakenings, and the byte-matched release digest are real verification, and it correctly
identified and reported T2-001 through T2-003 rather than papering over them.

**Verification I performed for this review.** Working-tree diff and untracked-file provenance
(`git diff main --stat`, `git log -- e2e_harness.py`, mtime comparison, full `diff` of the two
harness copies); full read of `scripts/clarification_protocol.py`, `scripts/test_clarification_protocol.py`,
all three fixtures, and the harness diffs; `DESIGN.md` §5-§12 read against the code; four executable
reproductions (2-item bundle publish-then-respond, 2-item and 4-item terminal-block adapter publish
through the real `E2EHarness` seam, forged decision plus rewritten lineage event, fabricated-ledger-key
CLI `create`); focused suite re-execution; installed-copy parity; and grep sweeps for bundle coverage
and for `read_decision_ledger`.

---

## Final Decision

**RESULT: FAIL. REVIEW_VERDICT: FAIL. DECISION_GATE_STATE: CLEAR.**

Four blocking findings, all independently reproduced by execution, all traceable to a named
requirement of the Jira scope or of this run's own approved DESIGN:

| ID | Severity | Responsible Phase | Gate |
| --- | --- | --- | --- |
| FA-001 | CRITICAL | implementation | G1, G2 |
| FA-002 | CRITICAL | design | G1, G2 |
| FA-003 | CRITICAL | implementation | G1, G4 |
| FA-004 | MAJOR | implementation | G1, G4 |

No user-owned decision is open, so this is a correction loop rather than a NEEDS_INPUT escalation —
with one exception the Coordinator must route deliberately: **FA-002 option (b)** (dropping bundles
entirely) would be a reduction of stated Jira scope and must not be taken as an implementation-level
shortcut. If the correcting Worker proposes it, that is a user-owned decision and must be raised as
such rather than absorbed.

Suggested route: **design first** (FA-002 — decide and specify how a bundled request is answered, or
formally reduce scope), then **implementation** (FA-001, FA-003, FA-004), then **test**
re-validation covering the multi-item publish/answer path and a decision/lineage tamper matrix.

The PR must not be created from this state. When it is created, stage the OS-30 paths explicitly and
leave the stray root-level `e2e_harness.py` (FA-N01) out of it.
