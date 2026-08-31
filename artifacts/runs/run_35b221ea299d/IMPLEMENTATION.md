# Worker Result

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS

Run: run_35b221ea299d · Phase: implementation · Iteration: 3 · Role: worker
Branch: os-29-continuous-decision-gates (base main @ b13f191)
Ticket: OS-29 "Add Continuous Decision and Escalation Gates to Every Phase"
Approved input: `ANALYSIS.md`, `PLAN.md`, `DESIGN.md` — all PASSED their gates and **none is
modified by this phase**. The approved PLAN and the explicit requirement are the specification
implemented here; where DESIGN's prose is narrower than either, they govern (see *Iteration 3*).
Correction input (iteration 3): `REVIEW_IMPLEMENTATION_iteration2.md` — **RESULT: FAIL**, one
blocking finding **F-003**, a G1 explicit-requirement violation at the Final Review after-result
boundary. F-001 and F-002 were verified **RESOLVED** at iteration 2 and are neither reopened nor
regressed here.
Correction input (iteration 2): `REVIEW_IMPLEMENTATION.md` — **RESULT: FAIL**, blocking findings
**F-001** and **F-002**, both G1 explicit-requirement violations on the live Orca runtime path.

Every command output quoted below was executed on this branch during this phase. Nothing is
inherited from an earlier phase's artifact. **Iteration 1 is commit `5e1a6cb`; iteration 2 is commit
`0745a4d`; iteration 3 is a new commit on top of both — nothing was amended, rebased or pushed.**

---

## Iteration 3 — what changed and why (read this first)

The Reviewer FAILED iteration 2 on one G1 finding, and it was right.

**F-003 — RESOLVED.** The Final Review after-result boundary **is** B3, and it now behaves like one.
`run_workflow()` no longer reads the settled Final Reviewer result on the quality axis alone. In the
same place the attempt already settles — after the audit record is written, before T1 — it now:

1. **parses and validates** the Final Reviewer's own `DECISION_GATE_STATE` declaration and its fenced
   `decision-gate` record through `decision_gate.parse_gate_result()`, the same reader B2 and B3-N
   use, and **refuses** on missing, unparseable, unknown-state, duplicated-line and
   summary-disagrees-with-record inputs;
2. **refuses an unbound record** — a Final Review round has no Worker, so a record claiming to
   `verify` a B2 classification cannot resolve and is `DECISION_GATE_INPUT_UNBOUND`, never extra
   evidence;
3. **appends the bound B3 ledger record** (`phase: final_review`, `boundary: B3`, `role`/`source:
   reviewer`, the quality verdict beside — never as — the decision state) and rebinds the round so
   the next B1 site expects it;
4. **terminates** with the standard decision/input block for `NEEDS_INPUT`, `CONFLICT` and every
   defective shape, so a quality **PASS** can no longer reach `COMPLETED` over a blocking or broken
   decision axis.

**Iteration 2's third disclosure is WITHDRAWN, not re-argued.** Iteration 2 stated that PLAN W-4's
"Final Review T1" B3 guard was superseded by DESIGN's narrower control-flow section, which lists only
a B1 guard at the attempt open. That was not a defensible call. The B1 site refuses an **already
open** ledger item *before* the Final Review is dispatched; it cannot see a decision that is first
raised — or first broken — in the Final Reviewer's **own settled body**, which is the other half of
ORIGINAL_REQUEST's requirement that the five phases and the Final Review use the same state/reason
contract at "after receiving the Reviewer result". Where approved DESIGN prose is narrower than an
explicit requirement plus the approved PLAN, the requirement and the PLAN govern and the prose is
what was incomplete. The old scenario-9 test is **kept unchanged** — it still proves the B1 half —
and three new tests prove the half it never covered.

**The contract now says so in the Skill, not only in the tests.** The orchestration-only
`#### Decision gate contract` block gains one line,
`DECISION_GATE_FINAL_REVIEW_BOUNDARY = after_reviewer_result`, with its matching entry in
`validate_skills.py`'s `DECISION_GATE_CONTRACT`. It declares that the Final Review is **not** a
fourth boundary but the same `after_reviewer_result` boundary the five phases read — so "the Final
Review is exempt" is now a validation failure rather than an unstated assumption. Deleting the line
from the shipped Skill makes `validate_skills.py` FAIL with *decision gate contract keys drifted* /
*values drifted* (executed; output in *Validation*). The key is orchestration-only, matching P5's
"MUST NOT be mirrored" row; the loop Skill is untouched and still carries **zero** anchor contracts.

**No test, assertion, case, fixture or validator was deleted or weakened.** Two existing assertions
in `DecisionGateTransitionTests` were **extended** — the settled-boundary list gains
`("final_review", "reviewer", "B3")` and the CLEAR-run ledger length moves 5 → 6, with an added
assertion on the new record's phase/boundary/state. Both changes make those tests strictly stronger:
they now assert the boundary exists rather than being silent about it.

---

## Iteration 2 — what changed and why (carried forward, unchanged by iteration 3)

The Reviewer FAILED iteration 1 on two G1 findings, both on the live runtime, and both are now
resolved in production code with the negative tests the findings demanded.

**F-001 — RESOLVED.** `_record_decision_from_attempt()` no longer has a legacy exception. A settled
Worker or Reviewer result whose body declares no gate result is now the **missing-decision-record**
case: no ledger record is published, `_last_settled` **is** advanced, and the very next `_b1_guard()`
refuses `DECISION_GATE_INPUT_UNBOUND` — the identical fail-closed shape a declared-but-broken body
already produced. The columns say `INPUT` / `DECISION_GATE_INPUT_MISSING` rather than staying blank.
Iteration 1's D-2 deviation is **withdrawn, not re-disclosed**: disclosure does not authorize a
departure from an explicit requirement, and the Reviewer is right that it did not.

**F-002 — RESOLVED.** `observe_unexpected_exit()` — the second of the two centralized dispatch
initiators — now calls the **same** `_b1_guard()` as `run_existing_task()`, before `dispatch_context`,
before `create_task`, before the terminal and before `start_worker`. Five adversarial tests drive one
refusal shape each (absent ledger, unsupported schema, malformed record, unbound head, unresolved
open item) and assert **no Task, no terminal, no `worker-start`**; a sixth is the non-vacuity control
showing the same guard still admits a clean head.

**The one test the finding required be REPLACED.**
`test_a_legacy_body_that_declares_nothing_is_not_a_ledger_participant` asserted the removed
fail-open behaviour positively. It is deleted and replaced by three tests that assert refusal —
Worker (B2), Reviewer (B3), and the same refusal observed through the real `_log_attempt()` funnel.
**This is the correction the Reviewer demanded, not a weakening**; the count moved 229 → 238 test
functions in that module, one removed and ten added, and no other test, assertion, case or validator
was deleted or relaxed anywhere in the tree.

**The conflict the task told me to look for is NOT real, and here is the check.** Backward
compatibility for a non-declaring *settled* agent is not a competing explicit requirement — it was an
implementation convenience, and it is gone. What IS an explicit requirement is that OS-29 may not
weaken the existing lifecycle guarantees, and a **non-response** (`observe_unexpected_exit`:
`outcome=unknown`, `worker_done_count=0`, its own `unexpected_exit` event) is a different thing from
a settled result that stayed silent. B2 and B3 are defined by ORIGINAL_REQUEST as "after **receiving**
the Worker/Reviewer result", so a dispatch that delivered no result never reached either boundary and
has no gate result to be missing. The rule is therefore scoped to attempts that actually delivered a
result — a statement about *which attempts are gate boundaries*, not a tolerated shape of result at
one. Nothing is presumed `CLEAR` for a crashed round either: no record is published, the columns stay
blank, no `gate_result` is recorded, and the recovery dispatch that follows is itself B1-guarded by
F-002's fix. Poisoning there instead would have bricked crash recovery, which is exactly the
"weakening lifecycle guarantees" ORIGINAL_REQUEST puts out of scope. `test_a_non_response_is_not_a_
boundary_and_is_never_presumed_clear` pins this, and the two lifecycle regression tests that would
otherwise have had to be weakened
(`UnexpectedExitSettlementTests.test_recovery_path_mutates_once_and_replays_thereafter`,
`SameRoleSessionReuseTests.test_a_dispatch_in_lifecycle_recovery_forces_a_fresh_terminal`) are
**untouched and green**.

**Why the fixture edits were unavoidable, and why they are not a weakening.** The Reviewer's own
words: "the green suite CANNOT establish the required property because it currently encodes the
exception." The offline recorders answered every settled `worker_done` with a bare `"ok"` — a silent
result — which the removed exception tolerated and the new rule correctly refuses at the *next*
boundary. Those doubles now **declare**, using `fake_worker.render_decision_gate()` — the same
renderer the real fake agent subprocesses already use, so no decision vocabulary is restated and the
doubles cannot drift from the agents. `test_e2e_harness`'s byte-identity stripper gained one more
enumerated OS-29 addition (the declaration excerpt that now reaches the `detail` column) and, like
every other stripper in that block, **raises** when the thing it strips is absent — so a build that
stopped declaring turns those tests red instead of green.

---

## Summary

DESIGN's D1–D8 are implemented as approved, with the change surface a **subset of PLAN P2's C1–C13
table**. No new `RUN_STATUS`, `round_kind`, Worker `STATUS` or `REVIEW_VERDICT` value exists; no new
dispatch site, subprocess site or round exists; the shared ```` ```policy-contract ```` block is
untouched at **90/90** lines in both Skills; and no existing test or validator was deleted or
weakened.

The whole feature is nine things:

1. **`scripts/decision_gate.py` (new, C1)** — the fail-closed parser, the ledger-record validator,
   the A1–A6 admissibility rule and the B3-V verification evaluator. It imports `decision_policy`
   and the standard library and nothing else, and **no** function in it takes a `risk`, `profile` or
   quality-profile parameter.
2. **The append-only decision ledger (C3)** in `run_logging.py`, under
   `artifacts/runs/<run-id>/decision_ledger/<NNNNNN>/record.json`, plus two sparse
   `ORCHESTRATOR_LOG` columns and three new `--event` values. Byte-mirrored into the Skill's
   `tools/` copy in the same commit (C3b).
3. **B1 at four sites, B2 and B3 in `e2e_harness.py` (C2)** — every guard is a condition added to a
   branch that already exists.
4. **`gate_attempts()` keyed on `decision_block` (W-6)** — one edit covering both terminal shapes.
5. **The live-path B1 guard before `start_worker` (C4)**, plus the ledger record and the two columns
   at `_log_attempt`.
6. **The tenth orchestration-only anchor contract and the mirrored semantics (C5/C6/C7)**, with a
   validator that fails in all three drift directions.
7. **The `DECISION_GATE_STATE` result contract (C8)** in both Skills, all fourteen templates and both
   `reviews/common.md` — beside the **byte-unchanged** optionality sentence.
8. **The always-armed fake agents (C10)** — the OS-3 opt-in precedent deliberately inverted.
9. **The tests and fixtures (C13)**: two new modules, four new test classes in existing modules,
   thirteen fixtures. **1496 → 1582 tests** (iteration 1 reached 1570; iteration 2 added 9 net;
   iteration 3 adds 3). The only test function removed in the whole ticket is the one **the Reviewer
   required be replaced** (F-001), and it is replaced by three that assert the opposite.

Iteration 3 adds a tenth item: **the Final Review after-result boundary is a B3 like every other
(C2)** — the same reader, the same ledger writer, the same terminal vocabulary — plus the
orchestration-only `DECISION_GATE_FINAL_REVIEW_BOUNDARY` anchor-contract key that makes the
five-phases-and-the-Final-Review parity a validator check.

**One mechanism detail differs from DESIGN's prose and is disclosed rather than applied silently**
(*Deviations* below). It does not change an approved conclusion, it is not irreversible, and it does
not reach user authority — so, per the task spec, it is an ordinary settled implementation call and
is recorded here with its evidence. **Iteration 1's second disclosure (D-2) and iteration 2's third
(D-3) were withdrawn, not carried forward**: in both cases the Reviewer found a departure from an
explicit requirement, and in both cases it was right.

---

## Analysis

### What DESIGN fixed, and what implementation had to decide

DESIGN fixed the mechanism at `file:line` granularity, so implementation was mostly transcription.
Three things it could not fix in advance, because they are only visible once the code exists:

**(1) The agent's half of a record versus the harness's half.** DESIGN's D4 requires all thirteen
fields plus six mechanics fields on a *ledger* record, and D2 requires the agent to emit a record
that `validate_record()` accepts. An agent cannot know its own ledger `sequence`, and its claim
about its own `run`/`phase`/`iteration` would make A3 a restatement of what the agent said. The
split implemented is therefore: **the agent owns the decision half** (state, reason code, evidence,
assumption, open item, grounds, scope) and **the harness stamps the binding half** (run, phase,
iteration, role, boundary, source, verdict, sequence, timestamp, source binding). That is what makes
A3 a real check — F11 deletes a settled record and the head fails to bind, which is only possible
because the ledger is written from the harness's own round state.

Consequently there are two validators, not one: `validate_gate_record()` (closed field set +
`validate_record()`) runs on what the agent emitted, and `validate_ledger_record()` (closed field
set + all nineteen fields + typed mechanics + `validate_record()`) runs on every record A4 reads.
`decision_gate.py`'s public surface still contains `validate_ledger_record` exactly as DESIGN names
it.

**(2) Row 7's binding has to be reachable.** DESIGN requires a B3-V record whose `verifies` does not
resolve to be `DECISION_GATE_INPUT_UNBOUND`. If the harness stamped `verifies` itself, that branch
would be unreachable and the check vacuous. So the harness **passes the Worker's ledger key to the
already-scheduled Reviewer** (`--decision-gate-verifies`, added to the reviewer command *only* in
verification mode, so every ordinary round's dispatched command stays byte-identical) and then
**checks what came back**. `--decision-gate-verifies-raw` is the seam that makes row 7 a real test
rather than dead code.

**(3) `open_items()` needed one clause the prototype did not have.** The shipped contract permits a
`NEEDS_INPUT → NEEDS_INPUT` transition, so a Reviewer *confirming* a Worker's block would have read
as having *resolved* it. That would make agreement into resolution — precisely what the decision
contract forbids. A record whose own state is blocking now resolves nothing. This is a strengthening
of A5/A6, it changes no approved conclusion, and it has its own test
(`test_a_worker_reviewer_agreement_never_resolves_an_open_item`).

### The one behaviour change this ticket mandates, called out as PLAN requires

**A run whose agents declare nothing at B2/B3 no longer proceeds.** That is the *Fail-closed rules*,
not a regression. It is the single incompatibility, and it is why the two fake agents declare
`CLEAR` by default (D7) — every one of the ~1496 pre-existing tests keeps its transitions with no
fixture relaxed anywhere.

---

## Changes

### C1 `scripts/decision_gate.py` — new, 650 lines

| Surface | What it is |
| --- | --- |
| `LEDGER_RECORD_SCHEMA_VERSION` / `SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS` | the **record's** version, sole owner, deliberately not `decision_policy.SUPPORTED_SCHEMA_VERSIONS` |
| `GATE_STATE_FIELD` = `DECISION_GATE_STATE`, `GATE_RECORD_BLOCK` | D2's two halves. The name appears nowhere in the shipped templates before this change, which is what keeps it separable from the narrative `DECISION_STATE` |
| `GATE_REFUSAL_REASONS` (8) + `BLOCK_REASON_PATTERN` | D1's closed set plus the one grammar |
| `REQUIRED_LEDGER_RECORD_FIELDS` (13), `LEDGER_MECHANICS_FIELDS` (6), `CONTRACT_EVIDENCE_FIELDS`, `CLOSED_LEDGER_RECORD_FIELDS`, `OS30_RESERVED_FIELDS` | D4's schema and D5's enforced boundary |
| `parse_gate_result` / `parse_declared_state` / `validate_gate_record` / `validate_ledger_record` | B2/B3's fail-closed reader, and A4-i/ii/iii |
| `admit_head` | A1–A6 in the fixed order **A1 → A2 → A4 → A3 → A6 → A5** |
| `open_items` | A5's recomputation, delegating every resolution question to `validate_transition()` |
| `evaluate_verification` / `verification_binding_defect` | P6b rows 4–7 |
| `block_reason` / `decision_columns` / `ledger_key` / `declares_gate_result` | the terminal vocabulary and the two sparse column values |

`GateRefusal` carries a **closed** `.reason` and a free-text `.detail`; the detail is for the log and
is never routed on.

### C2 `scripts/e2e_harness.py` — +539 / −8 (iterations 1–2), +121 / −1 (iteration 3)

* `WorkflowResult` gains `decision_block`, `decision_state`, `decision_reason_code`;
  `WorkflowRunResult` gains the two reporting fields; `snapshot()` propagates them.
* `__init__`: `ensure_run_artifact_root(...)` → `run_logging.open_decision_ledger(...)`, one
  statement, same returned root; `self.policy = decision_policy.load_decision_policy(skill_path)`.
* `run_workflow`: the second run-open becomes `open_decision_ledger(...)`; `last_settled` is the
  loop's own round state; `b1(site)` is **one** guard used at **four** sites — the phase gate, the
  Final-Review attempt open, T4 correction and T5a revalidation.
* `run()`: **B2** between the `worker_attempts.append(...)` and the `STATUS: BLOCKED` branch — above
  the LOW safety floor and above the LOW gate return, so the check exists at every risk level;
  **B3** after `reviewer_attempts.append(...)`, two modes on one code path.
* `gate_attempts()`: `if result.decision_block is not None: return 0`, then the existing expression.
* `_decision_blocked`, `_append_decision_record`, `_log_decision_event` — three private helpers. None
  of them dispatches anything.

**Iteration 3 (F-003), +121 / −1.** The Final Review after-result boundary, in `run_workflow()`'s
`while True:` loop, between `self._write_final_review_audit(...)` and `# ---- T1`:

* `parse_gate_result(attempt.output, self.policy)` inside a `try` whose `except GateRefusal` logs
  `EVENT_DECISION_BLOCK` and returns `snapshot(blocked_status, refusal.reason, decision_state="INPUT",
  decision_reason_code=refusal.reason)` — P6b row 10, at the Final Review.
* the `record.get("verifies") is not None` refusal — the same rule B3-N applies, for the same reason:
  a Final Review round has no Worker B2 record, so a verification claim cannot resolve.
* `_append_decision_record(..., phase=FINAL_REVIEW_PHASE, iteration=final_review_iterations,
  role="reviewer", boundary="B3", source="reviewer", verdict=verdict or "", verifies=None)` — the
  **existing** writer, no new one, so the thirteen required fields and the binding half are stamped
  exactly as they are for a phase Reviewer.
* `last_settled = (self.run_id, FINAL_REVIEW_PHASE, final_review_iterations)` — the record is now the
  ledger head, so T4's B1 site must expect *this* round. Without it an ordinary routed FAIL would
  refuse as A3-unbound; `test_the_final_review_record_binds_the_next_boundary` is the control, and
  deleting the line turns it red (mutation NV-FR3, executed below).
* `if final_gate.state in decision_gate.BLOCKING_STATES:` → log + `snapshot(blocked_status,
  block_reason(state, code), …)` — P6b row 8, at the Final Review.
* `FinalReviewScenario` gains `decision_states` and `decision_args`; `_run_final_review_attempt()`
  gains two defaulted parameters and appends `--decision-gate-state` / the raw argv **only when the
  scenario asked**, so a scenario that says nothing dispatches the pre-OS-29 argv unchanged. These
  are the same two knobs `FakeScenario` already gives a phase Reviewer, named the same way.

**No new dispatch site, no new subprocess site and no new round.** This is the attempt the loop
already made, read on the decision axis before the quality axis (O-2). The audit write stays *above*
the parse on purpose: the audit record is evidence of a dispatch that physically happened and is
never rewound by the judgement that follows it.

### C3 / C3b `scripts/run_logging.py` and the Skill's `tools/` copy — +379 / −2, **byte-identical**

* Two sparse columns after `round_kind`; three `--event` constants; `--decision-state` /
  `--decision-reason-code` on the `orchestrator-event` subcommand.
* `DecisionLedgerError` / `DecisionLedgerCollision`; `decision_ledger_dir`,
  `decision_ledger_sequence_key`, `read_decision_ledger`, `append_decision_ledger_record`,
  `open_decision_ledger`.
* `parse_decision_record_section` / `reconcile_decision_record_section` — P-2's drift reader.
* `_stage_and_publish_audit_record` generalized in **exactly the two places DESIGN names**: the loop
  iterates `files.items()`, and an empty payload is refused before anything is staged.
* **The module still imports nothing from `scripts/`.** `ledger_schema_version` is a required
  keyword argument with no default on both `open_decision_ledger()` and
  `append_decision_ledger_record()`, supplied by the callers.

`open_decision_ledger` accepts and **validates** `risk` and deliberately does **not** record it: the
record's field set is closed and excludes it, because a decision record carrying a risk level would
invite exactly the coupling the contract forbids.

### C4 `scripts/orca_runtime_harness.py` — +163 / −2 (iteration 1), +55 / −13 (iteration 2)

* `DecisionGateRefused(OrcaRuntimeError)`; `_b1_guard()` at the **top** of `run_existing_task`,
  before `dispatch_context`, before any terminal is created and before `start_worker`.
* **Iteration 2 (F-002):** the *same* `_b1_guard()` call is now also the first statement of
  `observe_unexpected_exit()` — the other centralized dispatch initiator — ahead of
  `dispatch_context`, `create_task`, the terminal, `start_worker` and the timing-boundary open.
  Both dispatch initiators are now guarded; there is no third.
* `start_run` → `open_decision_ledger(...)`, adjacent to the existing log opens.
* `_record_decision_from_attempt()` at `_log_attempt`: **two** cases, and the difference between them
  is the whole fail-closed behaviour available on this path — a body that does not yield a valid gate
  result (whether it declared **nothing at all** or declared something **defective**) publishes no
  record and advances `_last_settled`, so the next B1 refuses as `UNBOUND`; a valid one is published,
  bound and indexed in the two columns. **Iteration 2 (F-001)** merged what used to be a third,
  tolerated case into the first. The method now takes the caller's `event`, because B2/B3 exist only
  for an attempt that actually **delivered** a result: an `unexpected_exit` (`worker_done_count == 0`)
  reached neither boundary, publishes nothing, claims nothing and is guarded by B1 on the next
  dispatch instead.

### C5 `scripts/validate_skills.py` — +197 (iterations 1–2), +6 (iteration 3)

`DECISION_GATE_CONTRACT` (**19** keys at iteration 3 — the new
`DECISION_GATE_FINAL_REVIEW_BOUNDARY = after_reviewer_result`, still inside the 20-line budget), its
pattern and its budget, parsed with the **shared**
`parse_anchor_contract`; `MIRRORED_DECISION_SEMANTICS_ANCHORS` (3 sentences);
`DECISION_GATE_RESULT_CONTRACT_ANCHOR`, built from `decision_gate`'s own constants so renaming the
field in code without editing the documents is a validation failure. `validate_decision_gate_contract()`
checks the block, its internal consistency against the code's own vocabulary, both mirrored
directions, the loop Skill's **absence** of the block, the result contract in both Skills and in all
sixteen routed documents, and the optionality sentence in the same place.

**Iteration 3 (F-003).** One key added to `DECISION_GATE_CONTRACT` with its five-line rationale
comment. It declares that the Final Review is **not** a fourth boundary but the same
`after_reviewer_result` boundary the five phases read — which is exactly ORIGINAL_REQUEST's "the five
phases and the Final Review use the same state/reason contract", now machine-checked. The existing
`len(DECISION_GATE_BOUNDARIES) == len(BOUNDARIES)` check is untouched and still passes: the new key
names a *participant*, not a boundary. Check count stays **697** because the block is validated by a
fixed set of key/value drift checks over the parsed dict rather than one check per key — and the key
is genuinely enforced: deleting the line from the shipped Skill FAILS the validator (executed below).

### C6 / C7 / C8 the Skill trees

* Orchestration `SKILL.md`: the **stale sentence corrected** (§Decision Policy no longer claims OS-29
  is unimplemented); the three mirrored paragraphs; the tenth anchor block at the end of §8; the
  `DECISION_GATE_STATE` line in §10 and §11 with the authority note; the decision-axis rule in §12;
  the iteration-accounting rule in §13; **Final Review axis J** (unresolved decisions, unapproved
  high-impact assumptions, decision drift, provenance); **L1–L8** written down; ten new Core
  Invariants. **Iteration 3 adds exactly one line** to the anchor block —
  `DECISION_GATE_FINAL_REVIEW_BOUNDARY = after_reviewer_result` — and nothing else in the file
  changes (`+1 / −0`).
* Loop `SKILL.md`: the same three mirrored paragraphs, the same result-contract line in §14/§16, an
  explicit statement that lifecycle is orchestration-only. Its "이 계약은 정의다 …" sentence **stays
  as-is**, because the loop Skill still does not gate. It still has **zero** anchor contracts.
* All fourteen `templates/*.md` and both `reviews/common.md`: a `#### Decision gate result (required,
  and a different object)` block placed *after* the optionality sentence, which is **byte-unchanged**.

### C10 `scripts/fake_worker.py`, `scripts/fake_reviewer.py`

Armed by default. `--decision-gate-state` (constrained), plus four unconstrained seams
(`--decision-gate-state-line-raw`, `--decision-gate-record-raw`, `--decision-gate-omit-field`,
`--decision-gate-omit-block`) and, on the reviewer, `--decision-gate-verifies[-raw]`. The record
builder lives in `fake_worker.py` and `fake_reviewer.py` imports it, so the two fake agents have one
definition of the declaration rather than two that can drift.

`--mode malformed` deliberately emits **no** declaration: that output is rejected by the STATUS parse
before B2 is reached, and emitting one would claim a boundary the result never crosses.

### C11 / C12

`scripts/test_validate_skills.py`'s copied-module list gains `decision_gate.py` (the trap documented
in that file). `CHANGELOG.md` gains three Unreleased/Added entries; `docs/ROADMAP.md` marks OS-29
implemented and restates what OS-30/OS-31 still do not do.

---

## Deviations from DESIGN's prose, disclosed

**One** deviation survives at iteration 3 (D-1). It preserves every approved conclusion and is not
escalated, because it is not irreversible and does not touch security, privacy, compliance, monetary
cost or long-term lock-in, and no two explicit requirements contradict. D-2 was withdrawn at
iteration 2 and **D-3 is withdrawn here** — both because a disclosure never authorizes departing
from an explicit requirement.

**D-1. The live-path refusal is RAISED, not returned.** DESIGN's snippet shows
`return self._pre_dispatch_refusal(refusal)`. `run_existing_task` returns
`tuple[RuntimeAttempt, str]`, so returning would require manufacturing a settled-looking attempt for
a dispatch that never happened — the opposite of what W-8 asks for. The implemented shape is the one
this class already gives every other pre-dispatch failure: log through the existing
`_log_pre_dispatch_failure` and **re-raise unchanged**. The DONE condition is met exactly — *no Task,
no Dispatch, no terminal is created* — and `test_a_missing_declaration_refuses_before_any_dispatch_exists`
asserts the recorder's command count is unchanged across the refusal.

**D-2 — WITHDRAWN at iteration 2 (review F-001).** Iteration 1 disclosed that on the live path a
body declaring NOTHING was "not a ledger participant" and left the B1 chain untouched. The Reviewer
correctly classified that as a G1 explicit-requirement violation: ORIGINAL_REQUEST's fail-closed list
opens with *missing decision record*, and it is unconditional. The exception is **removed**, not
re-argued. Silence and a declared-but-broken body now behave identically — no record published,
`_last_settled` advanced, the next B1 refusing `DECISION_GATE_INPUT_UNBOUND` — and the reasoning that
replaced it (a non-response is not a B2/B3 boundary at all, because no result was received) is stated
in full under *Iteration 2* above with its own test and its two untouched lifecycle controls. The
`_record_decision_from_attempt` docstring carries the same record in the code.

**D-3 — WITHDRAWN at iteration 3 (review F-003).** Iteration 2 disclosed that PLAN W-4's B3 guard at
"Final Review T1" was superseded by DESIGN's narrower control-flow section, which places only a B1
guard at the Final-Review attempt open. The Reviewer correctly classified that as a G1
explicit-requirement violation, and the reasoning behind the disclosure was wrong in kind:
**approved DESIGN prose that is narrower than an explicit requirement plus the approved PLAN does
not override either — it is the thing that was incomplete.** The Final Review after-result boundary
is now a full B3 (parse → validate → append → route), described under *Iteration 3* above. The B1
half is unchanged and its test is untouched; what the B1 site could never cover — a decision first
raised, or first broken, inside the Final Reviewer's own settled body — is covered by three new
adversarial tests plus a valid-CLEAR control. Only **D-1** remains disclosed.

---

## Modified Files / Artifacts

Production (C1–C12). Sizes are `git diff --numstat`.

| File | ± | Item |
| --- | --- | --- |
| `scripts/decision_gate.py` **(new)** | 650 | C1 |
| `scripts/e2e_harness.py` | +539 / −8 | C2 |
| `scripts/run_logging.py` | +379 / −2 | C3 |
| `orca-worker-reviewer-orchestration/tools/run_logging.py` | +379 / −2 | **C3b, byte-identical** |
| `scripts/orca_runtime_harness.py` | +163 / −2 | C4 |
| `scripts/validate_skills.py` | +197 | C5 |
| `orca-worker-reviewer-orchestration/SKILL.md` | +107 / −2 | C6 |
| `orca-worker-reviewer-loop/SKILL.md` | +18 | C7 |
| `templates/*.md` ×14, `reviews/common.md` ×2 | +22 each | C8 |
| `scripts/fake_worker.py` | +138 | C10 |
| `scripts/fake_reviewer.py` | +35 | C10 |
| `CHANGELOG.md`, `docs/ROADMAP.md` | +4 / +2−1 | C12 |
| `scripts/workflow_contract.py` | **0** | C9, proved unnecessary by DESIGN case D2-12 and re-proved by `test_adding_the_gate_field_left_the_two_contracts_identical` |

Tests and fixtures (C13):

| File | ± |
| --- | --- |
| `scripts/test_decision_gate.py` **(new)** | 667 |
| `scripts/test_os29_decision_gate.py` **(new)** | 224 |
| `scripts/test_e2e_harness.py` | +1014 / −8 |
| `scripts/test_run_logging.py` | +291 |
| `scripts/test_orca_runtime_contract.py` | +256 |
| `scripts/test_validate_skills.py` | +91 |
| `scripts/fixtures/decision_gate/{valid,invalid}/` **(new)** | 13 fixtures |

**Iteration 2 (F-001 + F-002) touched three files and nothing else** — `git diff --numstat` against
`5e1a6cb`:

| File | ± | Why |
| --- | --- | --- |
| `scripts/orca_runtime_harness.py` | +54 / −16 | C4: the F-001 fail-closed merge and the F-002 B1 guard |
| `scripts/test_orca_runtime_contract.py` | +342 / −43 | C13: the required replacement, the six `observe_unexpected_exit` tests, the non-response control, the declaring doubles |
| `scripts/test_e2e_harness.py` | +33 / −1 | C13: one more enumerated OS-29 stripper, with its own non-vacuity raise |

`git status --short` shows no other tracked file modified by this iteration.

**Iteration 3 (F-003) touched four files and nothing else** — `git diff --numstat` against `0745a4d`:

```text
1	0	orca-worker-reviewer-orchestration/SKILL.md
121	1	scripts/e2e_harness.py
180	1	scripts/test_e2e_harness.py
6	0	scripts/validate_skills.py
```

| File | ± | Why |
| --- | --- | --- |
| `scripts/e2e_harness.py` | +121 / −1 | C2: the Final Review B3 boundary, the two `FinalReviewScenario` knobs and their dispatch plumbing |
| `scripts/test_e2e_harness.py` | +180 / −1 | C13: three added tests; two existing ledger assertions **extended**, none removed or relaxed |
| `orca-worker-reviewer-orchestration/SKILL.md` | +1 | C6: `DECISION_GATE_FINAL_REVIEW_BOUNDARY = after_reviewer_result` |
| `scripts/validate_skills.py` | +6 | C5: the matching `DECISION_GATE_CONTRACT` entry and its rationale comment |

The single `−1` in each script is the one line each edit replaced in place (the widened
`_run_final_review_attempt` signature; the widened settled-boundary assertion). `run_logging.py` and
its Skill copy are **untouched at iteration 3** and remain byte-identical; `orca_runtime_harness.py`,
`decision_policy.py`, `task_context.py` and `workflow_contract.py` have no iteration-3 diff.

Artifacts written by this phase: `artifacts/runs/run_35b221ea299d/IMPLEMENTATION.md` (this file, in
place at the contracted path) and `artifacts/runs/run_35b221ea299d/records/implementation_decision_record.json`
(updated in place for iteration 3). **`ANALYSIS.md`, `PLAN.md` and `DESIGN.md` are unmodified**, and no
`REVIEW_*.md` was written.

### Every existing-test diff, justified

`git diff` over the four touched test modules deletes **no test, no assertion and no case**. The
deletions are import lines and three capture-helper expressions. Each edit:

1. **`test_e2e_harness.py` — the OS-29 strippers.** OS-29 is a transition no-op and *not* an artifact
   no-op: a fully-`CLEAR` run additionally carries the Worker's gate declaration inside the
   Reviewer's `current_delta`, and `ORCHESTRATOR_LOG.md` gains two columns. Two golden-capture tests
   compare byte-for-byte against a **pre-OS-4** and a **pre-OS-22** capture, which by construction
   cannot contain either addition. Rather than regenerate a golden whose whole value is that it is
   *not* what this code produces today, the two additions are **enumerated and removed** before the
   comparison, so the historical claim keeps its full strength for everything else. Each stripper
   **raises** when the thing it removes is absent — `strip_os29_spec_list` requires at least one
   dispatched spec to carry a declaration, `strip_os29_log_additions` requires the two columns — so a
   build that stopped emitting them turns these tests red instead of green.
2. **`test_orca_runtime_contract.py` — three offline harness helpers.** Each already bypasses
   `start_run()` and already stubs what `start_run()` would have done (`requested_phases`, with its
   own comment saying so). They now also stub `open_decision_ledger()`. Without it the pre-dispatch
   B1 guard refuses — **correctly**: a run id with no ledger is exactly the "no record present" state
   that must never read as `CLEAR`. No check anywhere in those 24 tests was relaxed.
3. **`test_validate_skills.py` — the copied-module list** gains `decision_gate.py`, which is the trap
   the file's own comment at that list documents.
4. **`test_run_logging.py`** — additive only.

**Iteration 2 adds exactly four more, all forced by F-001 and all enumerated:**

5. **`test_orca_runtime_contract.py` — the one required REPLACEMENT.**
   `test_a_legacy_body_that_declares_nothing_is_not_a_ledger_participant` asserted the fail-open
   behaviour F-001 required be removed, so leaving it would have meant shipping a suite that proves
   the violation. It is replaced by `test_a_silent_worker_result_poisons_the_next_boundary`,
   `test_a_silent_reviewer_result_poisons_the_next_boundary` and
   `test_a_silent_result_is_named_as_a_defect_on_the_live_log_row`. **This is a correction the
   Reviewer demanded, not a weakening.**
6. **`test_orca_runtime_contract.py` — the offline recorders now DECLARE.** `RecordingExec`'s
   `ACCEPTED_DONE`, seven other settled `worker_done` fixtures and the reviewer-body overrides gain a
   real `DECISION_GATE_STATE` line and its fenced record, rendered by
   `fake_worker.render_decision_gate()` — the same function the real fake-agent subprocesses call, so
   the doubles cannot drift from the agents and no vocabulary is duplicated. Nothing was relaxed: the
   two refusal fixtures that must stay silent (the stale and rejected `worker_done` payloads) are
   untouched, and every existing assertion in those tests still runs.
7. **`test_orca_runtime_contract.py` — one helper factored out.**
   `plant_unresolved_open_item()` is lifted verbatim out of
   `test_an_unresolved_open_item_refuses_the_next_dispatch` so the same planted shape can be driven
   through **both** dispatch initiators (F-002). The assertions stayed in the test; only the setup
   moved. `attempt()` gains three defaulted keyword arguments, so every existing call site binds
   unchanged.
8. **`test_e2e_harness.py` — one more enumerated OS-29 stripper.** The settled bodies now declare, so
   the declaration excerpt reaches `ORCHESTRATOR_LOG.md`'s `detail` column and the pre-OS-4 golden
   comparison would otherwise fail on an addition it cannot contain by construction.
   `strip_os29_detail_declaration()` removes exactly that suffix and `strip_os29_log_additions()`
   now **raises** when no logged dispatch quoted a declaration — the same non-vacuity half every
   other stripper in that block already carries, so a build that stopped declaring turns these
   byte-identity tests red rather than green.

**Iteration 3 adds exactly two more, both EXTENSIONS forced by F-003, and deletes nothing:**

9. **`test_e2e_harness.py` — `test_every_settled_boundary_leaves_a_complete_bound_record`.** Its
   settled-boundary list gains `(FINAL_REVIEW_PHASE, "reviewer", "B3")`. The test's own name states
   the property — *every* settled boundary leaves a bound record — and before iteration 3 the Final
   Review's settled boundary left none, so the old list encoded the omission rather than the
   property. Every prior element and every prior assertion is unchanged; four became five.
10. **`test_e2e_harness.py` — `test_a_fully_clear_run_adds_artifacts_without_changing_a_transition`.**
    `len(ledger)` moves 5 → 6, and a new assertion pins the added record's `(phase, boundary, state)`
    to `(final_review, B3, CLEAR)`. The test's claim is that a CLEAR run's **transitions** are the
    pre-OS-29 ones while its **artifacts** grow; iteration 3 adds an artifact and no transition, which
    is exactly what the surrounding assertions (`phase_iterations`, `correction_dispatches`,
    `revalidation_dispatches`, `final_review_iterations`, `reason`, the two decision columns) still
    assert, all unchanged.

    Both edits are strictly stronger: the pre-edit forms passed on a build with **no** Final Review
    decision boundary, which is the fail-open F-003 named. Mutation NV-FR1 confirms it — removing the
    boundary now fails 13 of the 20 tests in that class.

---

## Validation

Every command below was run on this branch, in this phase. Output is verbatim.

All figures below are **iteration 3** runs, executed after the F-003 fix.

| Check | Command | Result |
| --- | --- | --- |
| Skill validator, **check count above 648** | `python3 scripts/validate_skills.py` | `Skill validation PASSED (697 checks)` |
| Full unittest discovery, **≥ 1579, no deletions** | `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1582 tests in 309.100s` → `OK (skipped=6)`, exit 0 |
| Package verification | `python3 scripts/verify_package.py` | `Package verification PASSED (189 source files)` |
| Release build | `python3 scripts/build_release.py` | `Built reproducible release archive: dist/orca-skills-0.9.0.tar.gz` |
| Whitespace | `git diff --check` / `git diff --check main...HEAD` | clean (no output, exit 0) — both |
| **C3b parity** | `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py` | byte-identical (no output, exit 0) |
| **C-1: the shared block is untouched** | validator's own `DECISION_POLICY_BLOCK_PATTERN` | orchestration **90 / 90**, loop **90 / 90** |
| **D6: anchor asymmetry** | `grep -c '^#### .* contract$'` | orchestration **10**, loop **0** (both unchanged by iteration 3) |

The suite grew from **1496 → 1570** (iteration 1) **→ 1579** (iteration 2) **→ 1582** (iteration 3).
Across the whole ticket exactly **one** test function was removed — the one review F-001 required be
replaced — and it is replaced by three asserting the opposite behaviour. Iteration 3 removed none and
added three. No validator was deleted or weakened; see *Every existing-test diff, justified* above.

### F-003, verified as run output

```text
$ python3 -m unittest scripts.test_e2e_harness.DecisionGateTransitionTests
Ran 20 tests in 4.668s
OK
```

Three of those twenty are new and are the F-003 evidence:
`test_a_final_review_quality_pass_cannot_complete_over_a_blocking_decision` (NEEDS_INPUT and CONFLICT
as subtests, plus the valid-CLEAR control that completes),
`test_a_defective_final_review_decision_result_fails_closed` (seven defect subtests plus the
unmodified control) and `test_the_final_review_record_binds_the_next_boundary`.

### F-001 and F-002, re-verified as run output (not reopened, not regressed)

```text
$ python3 -m unittest scripts.test_orca_runtime_contract.DecisionGateLiveDispatchTests
Ran 15 tests in 0.065s
OK

$ python3 -m unittest scripts.test_e2e_harness.DecisionGateNonDuplicationTests
Ran 1 test in 0.196s
OK
```

Those fifteen are the F-001 and F-002 evidence in one class: three silent-result refusals (Worker
B2, Reviewer B3, and the live `_log_attempt` funnel), the non-response control, five
`observe_unexpected_exit` refusal shapes each asserting no Task / no terminal / no `worker-start`,
and the clean-head admission control that keeps those five non-vacuous. Iteration 3 touches no file
they cover.

### The `DECISION_GATE_FINAL_REVIEW_BOUNDARY` key is enforced, not decorative

```text
$ sed -i '' '/DECISION_GATE_FINAL_REVIEW_BOUNDARY = after_reviewer_result/d' \
      orca-worker-reviewer-orchestration/SKILL.md   # in a throwaway copy of the tree
$ python3 scripts/validate_skills.py
Skill validation FAILED (2 errors, 697 checks)
- orca-worker-reviewer-orchestration: decision gate contract keys drifted
- orca-worker-reviewer-orchestration: decision gate contract values drifted
```

The mutated copy was deleted; the tree under review carries the line.

### The decision record was VALIDATED, not merely described

```text
$ python3 -c "... load_decision_policy / validate_record over both Skills, plus three controls ..."
POSITIVE (orca-worker-reviewer-orchestration): accepted.
POSITIVE (orca-worker-reviewer-loop): accepted.
NEGATIVE CONTROL: rejected -> state CLEAR must not carry a reason_code
CONTROL-2: rejected -> CLEAR declares ['open_decision_item'] as grounds, but they do not satisfy
the CLEAR entry condition -- state CLEAR requi...
CONTROL-3 (closed field set): DECISION_GATE_INPUT_MALFORMED
```

**NEGATIVE CONTROL** is the ANALYSIS iteration-1 drift defect; **CONTROL-2** flips the single fact
that carries the state; **CONTROL-3** runs the *shipped* closed-field-set check over the same record
with an OS-30 lineage field added. All three are rejected, so the two acceptances are not the
validator ignoring the record.

### DESIGN's four prototypes, re-executed against the shipped tree

```text
a1_a6_admissibility.py       17/17 cases behaved as specified
d1_d2_d3_transition.py       40/40 cases behaved as specified
d4_d5_d8_ledger.py           40/40 cases behaved as specified
d6_d7_parity_migration.py    22/23 cases behaved as specified
```

The one non-passing case is **D6-15**, and it is a stale baseline rather than a regression: that case
asserts orchestration goes from **nine** anchor contracts to ten by adding the block to a copy of the
tree. The tenth block is now *shipped*, so re-running it against the post-implementation tree
measures ten → eleven. The shipped repository has exactly **ten** (measured above), the loop Skill
has **zero**, and `validate_decision_gate_contract` now enforces both — which is the property D6-15
was standing in for before the code existed.

### Mutation / non-vacuity runs, executed

| Proof | Test | What the run shows |
| --- | --- | --- |
| **NV-1** dispatch blocking | `test_nv1_removing_the_guard_lets_the_same_scenario_dispatch` | The blocked run has `sessions == ()`. With `admit_head` patched to always admit — the guard removed — **the same seeded scenario COMPLETES and dispatches**, so "nothing dispatched" is attributable to the guard |
| **NV-1** control | `test_the_first_phase_needs_the_run_entry_declaration` | F9: no declaration ⇒ `DECISION_GATE_INPUT_MISSING`, `sessions == ()`, every `phase_iterations == 0`. The same scenario **with** the declaration completes and dispatches |
| **NV-2** iteration non-consumption | `test_a_decision_block_charges_no_iteration_and_a_quality_fail_still_does` | blocked round: `phase_iterations == 0`, `correction_dispatches == []`. Co-located control: a quality-`FAIL` round still reaches **2** |
| **NV-3 / M-DUP** non-duplication | `test_m_dup_fails_the_invariants_while_the_control_passes` | (1) the mutant's `sessions` and reviewer-event count are strictly greater than the control's — the mutation is not a no-op; (2) the four-value `round_kind` assertion **still passes on the mutant**, which demotes that proxy from proof to supplementary evidence; (3) INV-D1 rejects the mutant and returns **clean** on both the blocked control and a `CLEAR` control |

**Iteration 3 adds three more, each executed against a mutated working tree and then reverted.**

| Mutation | What was changed | Result |
| --- | --- | --- |
| **NV-FR1** — the boundary is removed | the whole `# ======== OS-29 B3, Final Review edge` block deleted (4 823 chars), leaving the pre-iteration-3 control flow | `Ran 20 tests ... FAILED (failures=13)` |
| **NV-FR2** — the boundary is read but the block is ignored | `if final_gate.state in decision_gate.BLOCKING_STATES:` → `if False:` (parse, validate and ledger append all still happen) | `Ran 20 tests ... FAILED (failures=2)` |
| **NV-FR3** — the head is not rebound | the `last_settled = (self.run_id, FINAL_REVIEW_PHASE, final_review_iterations)` statement deleted | `Ran 20 tests ... FAILED (failures=2)` |

Restored tree: `Ran 20 tests in 4.668s ... OK`. NV-FR2 matters most: it proves the three new tests
are not satisfied by *recording* the Final Reviewer's decision — only by acting on it. NV-FR3 proves
the head rebinding is load-bearing rather than defensive.

`test_e2e_harness.DecisionGateTransitionTests` and `DecisionGateNonDuplicationTests`:
`Ran 20 tests ... OK` and `Ran 1 test ... OK` (both inside the full run above).

### The exit criteria, one by one

| P10 criterion | Where it is met |
| --- | --- |
| W-1…W-11 implemented; surface a subset of P2 | *Changes* + *Modified Files*. Only C1–C13 files are touched; `git status --short` shows nothing else |
| the existing loop is NOT duplicated; INV-D1/D2/D3 hold and M-DUP fails them | `test_m_dup_fails_the_invariants_while_the_control_passes`; `test_os29_decision_gate.DispatchSiteCardinalityTests` (2 subprocess sites, 3 round-dispatch sites, 4 `round_kind` values, static over the AST) |
| `NEEDS_INPUT`/`CONFLICT` block the correction Worker AND the next phase **in code** at low/medium/high, by P6b's single table, LOW terminal at B2 and MEDIUM/HIGH at B3-V with identical `final_status`/`decision_state`/`reason_code` | `test_needs_input_blocks_identically_at_every_risk_level` (equality across the three, plus the guard that they differ elsewhere); `test_an_open_ledger_item_blocks_the_next_phase_dispatch` |
| every B1 check — **including the first phase of a new run** — consumes an explicit, validated, bound ledger head under A1–A6, and no path reaches `CLEAR` from an absent, malformed or unbound record | `AdmissibilityTests` (A1–A6, ordering, F11); `test_the_first_phase_needs_the_run_entry_declaration`; `test_a_pre_seeded_ledger_is_unbound_at_the_runs_first_boundary`; `test_an_unreadable_or_unsupported_ledger_record_blocks_end_to_end` |
| a decision block consumes no correction iteration while quality FAIL still does | NV-2 above |
| a missing or malformed gate result fails closed at **all three** boundaries | B1: `test_the_first_phase_needs_the_run_entry_declaration`, `AdmissibilityTests`. B2/B3: `test_a_silent_or_broken_agent_never_presumes_clear`, run at low/medium/high through the **real** subprocess |
| **the five phases and the Final Review use the same state/reason contract at B3** *(F-003)* | Production: one B3 block, the same `parse_gate_result` reader, the same `_append_decision_record` writer and the same terminal vocabulary at both the phase and Final Review edges. Tests: `test_a_final_review_quality_pass_cannot_complete_over_a_blocking_decision`, `test_a_defective_final_review_decision_result_fails_closed`, `test_the_final_review_record_binds_the_next_boundary`. Contract: `DECISION_GATE_FINAL_REVIEW_BOUNDARY = after_reviewer_result`, enforced by `validate_skills.py` |
| the mandatory IMPLEMENTATION test gate, with affirmative evidence | `UNIT_TEST_STATUS: PASS` at the top and *Unit Tests* below |
| full CI green | *Validation* table |

### The fourteen required scenarios

| # | Scenario | Test |
| --- | --- | --- |
| 1 | `CLEAR` + PASS → next phase | `test_a_fully_clear_run_adds_artifacts_without_changing_a_transition`, `test_every_settled_boundary_leaves_a_complete_bound_record`; negatives `test_a_silent_or_broken_agent_never_presumes_clear`, `test_the_first_phase_needs_the_run_entry_declaration` |
| 2 | `ASSUMPTION_ALLOWED` + grounds → record, proceed | `LedgerRecordValidationTests.test_each_state_carries_all_thirteen_fields_and_the_contract_accepts_it` (the `worker_assumption_allowed` fixture, accepted); negative `needs_input_missing_required_evidence` |
| 3 | `NEEDS_INPUT` → blocked, iteration not consumed | `test_needs_input_blocks_identically_at_every_risk_level`, NV-2 |
| 4 | `CONFLICT` → blocked; downgrade rejected; unbound verification | `test_a_reviewer_downgrade_is_rejected_and_still_terminal`, `test_an_unbound_verification_record_fails_closed`, `VerificationTests` |
| 5 | high-impact decision during IMPLEMENTATION → blocked, not completion | `test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked`, with the `WORKER_BLOCKED` control |
| 6 | Worker auto-approves → Reviewer FAILs it as blocking | `test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing`, with the axis-separation control |
| 7 | LOW risk does not expand authority | `RiskIndependenceTests` (P1: `inspect.signature` over eleven functions + a source scan; P2/P3: the cross-risk equality above) |
| 8 | downstream expansion → new decision event, not lineage | `test_a_downstream_expansion_is_a_new_decision_event_not_a_lineage_link` (asserts every OS-30 field is absent from every record) |
| 9 | Final Review with an unresolved decision → completion forbidden | **Both halves.** (a) *already open before dispatch*: `test_an_unresolved_decision_forbids_final_review_completion` (unchanged — `final_review_iterations == 0`, `final_review_attempts == []`). (b) **iteration 3 (F-003)** *first raised or first broken in the Final Reviewer's own settled body*: `test_a_final_review_quality_pass_cannot_complete_over_a_blocking_decision`, `test_a_defective_final_review_decision_result_fails_closed`, `test_the_final_review_record_binds_the_next_boundary`, with mutations NV-FR1/NV-FR2/NV-FR3 |
| 10 | Worker+Reviewer agree on an unauthorized assumption → no approval | `test_a_worker_reviewer_agreement_never_resolves_an_open_item` (with the closed five-item cardinality precheck), `test_a_downgrade_is_decided_by_the_shared_contract_alone` |
| 11 | timeout / non-response → no approval, no iteration | `ReasonVocabularyTests` + NV-2; `forbidden_authority_sources` is asserted to contain `timeout`/`no_response` and to have exactly five members; **iteration 2** adds the live-path half, `test_a_non_response_is_not_a_boundary_and_is_never_presumed_clear` (no record, no state, no `gate_result`, and the following dispatch still B1-guarded) |
| 12 | illegal dispatch after a block → fail closed | `test_an_open_ledger_item_blocks_the_next_phase_dispatch` (e2e), `test_an_unresolved_open_item_refuses_the_next_dispatch` (live); **iteration 2 (F-002)** closes the second live dispatch initiator with five `test_unexpected_exit_refuses_*` tests and their clean-head admission control |
| 13 | missing/malformed → no `CLEAR` presumption | `GateResultParsingTests` (F1–F6), `LedgerRecordValidationTests` (F13/F14 + the cross-object control), `AdmissibilityTests` (F9–F12), and the end-to-end halves; **iteration 2 (F-001)** adds the live-path halves that were missing — `test_a_silent_worker_result_poisons_the_next_boundary` (B2), `test_a_silent_reviewer_result_poisons_the_next_boundary` (B3) and `test_a_silent_result_is_named_as_a_defect_on_the_live_log_row` |
| 14 | decision-semantics drift between the two Skills | `test_validate_skills`: one-Skill drift, deleted-from-both, the block leaking into the loop, plus the template and optionality checks |

---

## Unit Tests / Testing Strategy

**UNIT_TEST_STATUS: PASS**

Production code changed, unit tests were added and modified, they were executed, and they pass.

### Added

| Module | Classes | What they own |
| --- | --- | --- |
| `scripts/test_decision_gate.py` **(new, 667 lines)** | `GateResultParsingTests`, `LedgerRecordValidationTests`, `AdmissibilityTests`, `VerificationTests`, `RiskIndependenceTests`, `ReasonVocabularyTests`, `SchemaVersionCompatibilityTests` | the parser/evaluator, A1–A6, F1–F8, F13/F14, the closed field set, the reason vocabulary, risk-inertness |
| `scripts/test_os29_decision_gate.py` **(new, 224 lines)** | `ImportDirectionTests`, `DispatchSiteCardinalityTests`, `WorkerVocabularyTests` | the cross-cutting residue: the two AST import-direction assertions, the `tools/` byte parity, INV-D3, the untouched vocabularies |
| `scripts/test_e2e_harness.py` | `DecisionGateTransitionTests` (**20** after iteration 3), `DecisionGateNonDuplicationTests` (1) | every transition cell, F9–F14 end to end, NV-1, NV-2, NV-3/M-DUP; **(F-003)** the Final Review after-result boundary: a quality PASS over a blocking decision, seven defective-declaration shapes, and the record's binding of the next boundary, each with a co-located control |
| `scripts/test_run_logging.py` | `DecisionLedgerProducerTests` (10), `DecisionRecordSectionDriftTests` (3) | the producer, idempotence, D8's writer-side exclusivity **with its ENOTEMPTY grounds executed**, the empty-payload precondition, append-only byte identity, two-writer allocation, the columns, the CLI, P-2 drift |
| `scripts/test_orca_runtime_contract.py` | `DecisionGateLiveDispatchTests` (**15** after iteration 2, unchanged at iteration 3) | `start_run` writes the declaration; a missing one refuses with **no command issued**; an open item refuses; a declaring dispatch records a bound entry; a broken declaration poisons the next boundary; **(F-001)** a silent Worker result, a silent Reviewer result and the same silence seen through `_log_attempt` each poison it too, with the non-response control beside them; **(F-002)** five `observe_unexpected_exit` refusal shapes leaving no Task, no terminal and no `worker-start`, plus the clean-head admission control |
| `scripts/test_validate_skills.py` | 7 new regressions | scenario 14 (a)(b)(c) plus the block-removed, value-drift, template and optionality cases |

### Behaviour covered

Fail-closed at every boundary; the four states end to end; iteration accounting on both axes;
dispatch blocking at all four B1 sites and on the live path; the Markdown/machine authority rule and
its drift; the append-only ledger's durability and its collision primitive; the closed field set as
D5's boundary; risk inertness structurally and behaviourally; and the two Skills' shared semantics
against three drift directions.

**Two shapes DESIGN made mandatory are implemented as specified:** the `run_logging`
zero-`scripts/`-imports AST assertion (with its control asserting the walker sees the real imports —
otherwise a negative assertion over a walker that finds nothing proves nothing), and the empty-`files`
precondition's positive/negative pair.

### Execution

```text
Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  Ran 1582 tests in 309.100s
         OK (skipped=6)
         exit 0
```

Targeted, all re-executed at iteration 3:

```text
python3 -m unittest scripts.test_decision_gate            -> Ran  21 tests ... OK
python3 -m unittest scripts.test_os29_decision_gate       -> Ran   9 tests ... OK
python3 -m unittest scripts.test_e2e_harness              -> Ran 189 tests ... OK
python3 -m unittest scripts.test_run_logging              -> Ran 205 tests ... OK
python3 -m unittest scripts.test_orca_runtime \
                    scripts.test_orca_runtime_contract    -> Ran 244 tests ... OK (skipped=6)
cd scripts && python3 -m unittest test_validate_skills    -> Ran 181 tests ... OK
python3 -m unittest scripts.test_e2e_harness.DecisionGateTransitionTests \
                    scripts.test_e2e_harness.DecisionGateNonDuplicationTests
                                                          -> Ran  20 tests ... OK
python3 -m unittest \
  scripts.test_orca_runtime_contract.DecisionGateLiveDispatchTests
                                                          -> Ran  15 tests ... OK
```

The last two lines are the F-003 / NV-1 / NV-2 / NV-3-M-DUP transition-and-mutation runs and the
F-001 / F-002 evidence class respectively; both are inside the full run above and are quoted
separately only because they are the ones the corrections turn on.

**No existing test or validator was deleted or weakened.** The single removed test function in the
whole ticket is the one review F-001 required be REPLACED, and its replacement asserts refusal where
it asserted admission. **Iteration 3 removed none.** Its only edits to existing assertions are two
**extensions** in `DecisionGateTransitionTests` — the settled-boundary list gains
`("final_review", "reviewer", "B3")`, and the CLEAR-run ledger length moves 5 → 6 with an added
assertion pinning the new record's phase, boundary and state. Both are strictly stronger than what
they replaced: the old forms would have passed on a build with no Final Review boundary at all, which
is precisely the fail-open F-003 named. Every other edit to an existing test module is enumerated and
justified under *Every existing-test diff, justified*.

---

## Additional Validation

* `git status --short` shows **no file outside PLAN P2's C1–C13 surface** as modified.
* `scripts/release_manifest.py` needed nothing: `INCLUDED_ROOTS` takes `scripts/` wholesale, and
  `verify_package.py` / `build_release.py` both pass with the new module and fixtures included.
* The pre-existing `DeprecationWarning: invalid escape sequence '\s'` at `run_logging.py:1270` is in a
  docstring that predates this change; verified against `git show HEAD:scripts/run_logging.py`, which
  raises the same warning. Not introduced here and not in scope to fix.

### Limitations, restated because they are now shipped behaviour

OS-30 and OS-31 are **not implemented and are not claimed**. L1–L8 are written into the orchestration
Skill's own limitations block:

* **L1** a blocked run terminates and cannot be resumed — answering means a new run.
* **L2** no structured question is posed to the user in any form.
* **L3** no supersession lineage; scenario 8's answer is escalation, never a link.
* **L4** no timeout semantics beyond the contract's negative rule.
* **L5** at LOW there is no phase Reviewer, so a LOW Worker's *misclassification* is caught only by
  the Final Adversarial Review.
* **L6** a decision block is terminal at every risk level even when a downgrade is validly authorized.
* **L7** on the live Orca path the gate binds only within the Coordinator process that opened the run;
  a fresh process meeting a non-trivial ledger fails closed.
* **L8** the logging CLI writes and reads the ledger but does not contract-validate it; the gate
  judges.

---

## Review Feedback Resolution

Source (iteration 3): `artifacts/runs/run_35b221ea299d/REVIEW_IMPLEMENTATION_iteration2.md`
(**RESULT: FAIL**, one blocking finding, no non-blocking findings).
Source (iteration 2): `artifacts/runs/run_35b221ea299d/REVIEW_IMPLEMENTATION.md` (**RESULT: FAIL**,
two blocking findings, no non-blocking findings).

FINDING F-003: RESOLVED
FINDING F-001: RESOLVED
FINDING F-002: RESOLVED

**F-003 (G1, MAJOR) — the Final Review after-result boundary could fail open.** RESOLVED, and
resolved the way the Required Action words it, clause by clause.

* *"PARSE and VALIDATE the Final Reviewer's explicit decision-gate result BEFORE T1 quality
  routing."* Done. `decision_gate.parse_gate_result(attempt.output, self.policy)` — the **same**
  reader B2 and B3-N use, not a second one — runs between the audit write and `# ---- T1`. Nothing
  between the two reads the quality verdict.
* *"APPEND its bound B3 ledger record."* Done, through the **existing** `_append_decision_record()`,
  so the thirteen required fields, the harness-stamped binding half, the allocated sequence and the
  `EVENT_DECISION_RECORD_WRITTEN` row are produced exactly as they are for a phase Reviewer. The
  record carries `phase: final_review`, `boundary: B3`, `role`/`source: reviewer`, `verifies: null`,
  and the quality verdict in `verdict` **beside** — never as — the decision state.
* *"TERMINATE with the standard decision/input-block outcome for NEEDS_INPUT, CONFLICT, missing,
  malformed, unknown or unbound results."* Done, in the standard vocabulary and with no new one:
  `DECISION_BLOCKED:<state>:<code>` with the state and code in the two sparse columns for the two
  blocking states; `DECISION_GATE_INPUT_MISSING` / `_MALFORMED` /
  `DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD` / `DECISION_GATE_INPUT_UNBOUND` with
  `decision_state = INPUT` for the defective ones. No new `RUN_STATUS`, no new reason constant, no
  new round_kind.
* *"Add adversarial tests proving a Final Reviewer quality PASS CANNOT complete when its decision
  result blocks or is defective, plus a valid-CLEAR control."* Done — three test functions, eleven
  adversarial cases and three controls. Every blocked case asserts
  `final_review_verdict == "PASS"` alongside `final_status == "BLOCKED"`, which is the precise claim:
  the quality axis passed and it was not enough.
* *"This is the objective's required scenario 9 applied to a decision the Final Reviewer itself
  raises."* Understood and implemented as the second half of scenario 9. The first half — an
  already-open ledger item refused at B1 before dispatch — is **kept exactly as it was**; the row in
  *The fourteen required scenarios* now names both halves.
* *On the disputed reasoning.* The Reviewer is right and I withdraw the iteration-2 position without
  qualification. Approved DESIGN prose that is narrower than an explicit requirement plus the
  approved PLAN does not override either; the prose was the incomplete artifact. DESIGN is **not**
  modified by this phase — the correction is in production code, in the tests, and in the
  orchestration Skill's own anchor contract, which now states the parity in machine-checked form.
* *On the conflict check the decision discipline requires.* I looked for one and it is **not** real.
  Reading the decision axis first and leaving the quality routing byte-identical are both satisfiable
  at once: the new boundary either terminates the run or falls through untouched, and
  `test_the_final_review_record_binds_the_next_boundary` executes a full quality-FAIL → correction →
  quality-PASS run through the new code to prove the fall-through path is unchanged. So there is
  nothing to escalate, and STATUS is COMPLETE rather than BLOCKED.

**F-001 (G1, CRITICAL) — the live runtime failed open on a silent settled result.** RESOLVED, and
resolved the way the Required Action words it, not around it.

* *Poison the transition.* `scripts/orca_runtime_harness.py` `_record_decision_from_attempt()` no
  longer short-circuits on `declares_gate_result(body) == False`. A settled result with no
  declaration publishes no ledger record, returns
  `(decision_gate.INPUT_DEFECT_STATE, decision_gate.GATE_INPUT_MISSING)` for the two log columns, and
  **advances `_last_settled`**.
* *Bind the settled round so the next B1 cannot admit the old head.* Because `_last_settled` now
  binds, `admit_head()` sees a ledger whose head is still the previous round and refuses
  `DECISION_GATE_INPUT_UNBOUND`. The legacy branch and the defective branch are now one branch with
  one behaviour.
* *Replace the legacy-pass test with Worker AND Reviewer negative tests.* Done — see the tests named
  under *Iteration 2* and item 5 of *Every existing-test diff, justified*. The replacement asserts
  that no later dispatch can occur through **either** dispatch initiator after a missing gate result.
* *No Skill or contract text needed changing, which is itself evidence the finding is right.* The
  shipped orchestration `#### Decision gate contract` block already reads
  `DECISION_GATE_INPUT = explicit_machine_readable_record_never_absence`. Iteration 1's code
  contradicted the contract this very ticket wrote; iteration 2 makes the code match it. No SKILL.md,
  template, `reviews/common.md`, policy-contract or validator byte was touched, so the two Skills'
  shared decision semantics are untouched and `validate_skills.py` still reports **697 checks**.
* *On disclosure.* The iteration-1 Decision Record disclosed the exception and still classified the
  phase `CLEAR`. The Reviewer is right that disclosure does not authorize a departure; D-2 is
  withdrawn and the Decision Record for iteration 2 records that finding explicitly.
* *On the CONFLICT the task told me to test for.* I checked and it is **not** real. See *Iteration 2*
  above: the surviving requirement on the other side is "do not weaken existing lifecycle
  guarantees", and it is satisfied without any fail-open by observing that a **non-response** never
  reached B2 or B3 at all. Both requirements hold simultaneously, so there is nothing to escalate and
  STATUS is COMPLETE rather than BLOCKED.

**F-002 (G1, MAJOR) — `observe_unexpected_exit()` bypassed the B1 ledger guard.** RESOLVED.

* *The same pre-dispatch B1 guard before EVERY effect.* `self._b1_guard(phase=..., role=...,
  iteration=...)` is now the first statement in `observe_unexpected_exit()` — ahead of
  `dispatch_context()`, `create_task()`, `create_fake_terminal()`, `start_worker()` **and** ahead of
  `_open_phase_iteration_boundary()`, so a refused dispatch does not even open a timing scope. That
  is strictly the placement `run_existing_task()` already used.
* *Adversarial tests per refusal shape.* Five, one shape each — absent ledger
  (`DECISION_GATE_INPUT_MISSING`), unsupported schema (`DECISION_LEDGER_SCHEMA_UNSUPPORTED`),
  malformed record (`DECISION_GATE_INPUT_MALFORMED`), unbound head (`DECISION_GATE_INPUT_UNBOUND`)
  and an unresolved open item (`DECISION_BLOCKED:…`). Each asserts the recorder issued **zero**
  further commands, and separately that no `task-create`, no `terminal create` and no `worker-start`
  appears. A sixth test admits a clean head through the same guard, so the five are not vacuous.

Findings closed in the approved upstream phases stay closed, and their lessons are carried rather than
cited:

* **ANALYSIS F-001** (a `CLEAR` record that still supplied a reason code) is applied three times: as
  this phase's own negative control; as a shipped **fixture**
  (`invalid/clear_carries_a_reason_code.json`) asserted to be refused by both
  `validate_ledger_record` and `parse_gate_result`; and as the P-2 drift case in
  `test_the_motivating_drift_case_of_this_run`.
* **ANALYSIS F-002** (the `round_kind` proxy is not the non-duplication proof) is carried: M-DUP step
  (2) asserts the four-value check **still passes on the mutant**, making that demotion a fact of the
  suite.
* **PLAN F-001** (the ledger-record schema version distinct from the policy-block version) is
  implemented, not re-decided: `decision_gate.py` is the sole owner of both constants, A4-ii keeps its
  own terminal reason, and `test_every_ledger_record_declares_a_supported_schema_version` asserts the
  two constants are different objects and that the two reasons are distinct.
* **PLAN F-002** (one risk-specific transition table) is implemented as written, with the cross-risk
  equality and its "the three runs differ elsewhere" guard in one test function.
* **DESIGN's disclosed refinement** (`run_logging` must not import `decision_gate`) is implemented as
  approved *and* pinned by a test that would otherwise let it silently regress — a regression this
  repository's own CI could not see, because here `scripts/` is importable.

## Decision Record

The authority is the machine-readable JSON at
`artifacts/runs/run_35b221ea299d/records/implementation_decision_record.json`; this prose describes
it, which is the very rule this phase implements. Per the OS-28 contract, `CLEAR` carries **no**
reason code.

```text
DECISION_STATE: CLEAR
REASON_CODE: (none — CLEAR carries no reason code)
EVIDENCE: open_decision_item=false; grounds and scope as recorded in the JSON above
```

Re-validated at iteration 3 against **both** Skills with three negative controls:

```text
POSITIVE (orca-worker-reviewer-orchestration): accepted.
POSITIVE (orca-worker-reviewer-loop): accepted.
NEGATIVE CONTROL: rejected -> state CLEAR must not carry a reason_code
CONTROL-2: rejected -> CLEAR declares ['open_decision_item'] as grounds, but they do not satisfy
the CLEAR entry condition -- state C...
CONTROL-3 (closed field set): DECISION_GATE_INPUT_MALFORMED
```

`CLEAR` here is **not** the iteration-1 or iteration-2 grounds restated. Iteration 1 classified the
phase `CLEAR` while carrying a disclosed departure from an explicit requirement (D-2); iteration 2
did the same with a second one (D-3, the Final Review boundary). In both cases the Reviewer judged
those grounds substantively invalid and in both cases it was right. **Both departures are now
removed rather than re-disclosed**, and the record's `grounds` field says so, names the precedence
rule that settles it — an explicit requirement plus the approved PLAN over narrower approved DESIGN
prose — and states the conflict check that was actually performed and its negative result.

No explicit requirement is knowingly left unmet by this iteration; that is the condition under which
`CLEAR` is the honest classification, and it is the condition the two earlier iterations failed.

No `NEEDS_INPUT` and no `CONFLICT` item arose in this phase, so this phase does not stop.
