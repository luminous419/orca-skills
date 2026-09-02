# Reviewer Result — DESIGN iteration 6

RESULT: FAIL

REVIEW_VERDICT: FAIL

DECISION_GATE_STATE: CLEAR

Run: `run_db374a3fd83a` · Phase: design · Iteration: 6 · Role: reviewer (Reviewer B3, Claude Opus)
Feature: Jira OS-30 Structured Human Clarification and Decision Protocol
Artifact under review: `artifacts/runs/run_db374a3fd83a/DESIGN.md` (1015 lines, mtime 2026-09-01 23:08)

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "The corrected DESIGN.md, the approved ANALYSIS/PLAN baselines, REVIEW_DESIGN_iteration5, REVIEW_IMPLEMENTATION_iteration8 (N-802, N-801, R8-002), the shipped scripts/clarification_protocol.py surface, scripts/test_clarification_protocol.py, both SKILL.md files, README.md, docs/ROADMAP.md and docs/COMPATIBILITY.md fully determine this verdict. Eight of the nine required specification points are genuinely SPECIFIED and I verified each by construction against the N-802 reproduction and the two legitimate lineage shapes: section 9's graph rejects two decisions with zero events as ORPHAN_DECISION, admits the changed-answer shape through a validated decision_superseded edge, and gives cancel-then-redecide an explicit validated cancelled-reset-anchor transition instead of the deleted `later` fallback; section 6 splits historical-v1 read from v2 write admissibility precisely enough to close R8-002, permits disjoint generations in one tree, forbids crossing within a lineage or item, and never rewrites v1 bytes; N-801's response_bindings/ is adopted into the section 2 layout with a closed record, a section 3 identity formula and a v2-only authority rule; the section 3 threat model states obtainable structural integrity and explicitly disclaims authenticity against an arbitrary writer without demanding an unobtainable primitive; bounded 1..3 bundles and the FA-002 per-item designator are preserved unchanged; and every document needing change is NAMED in Expected Changed Files with file modification times confirming DESIGN.md is the only artifact touched this round. The gate nonetheless fails on one blocking specification gap that the single remaining IMPLEMENTATION attempt cannot resolve without guessing: cancellation of an item that has no effective decision has no specified lineage shape and no derivable status, so section 11's own contract that request-level --cancel makes every item cancelled contradicts section 9 step 4's requirement that a decision_cancelled event name the then-effective decision, and I reproduced on the shipped code that a request-level cancel of an unanswered two-item request emits zero lineage events and that a later first answer to that cancelled request still returns DECIDED with an effective head. The fix is design-owned and determined by the design's own section 11 and the user-settled fail-closed contract; no user-owned decision is open.",
  "iteration": 6,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "design",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T23:30:00+00:00",
  "responsible_phase": "design",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer verdict for design iteration 6 of Jira OS-30 only, verifying that REVIEW_IMPLEMENTATION iteration-8 N-802 is closed at the design layer by validated-lineage-only head derivation with named predecessor/successor fields, orphan and fork rejection under named error codes, and an explicit cancel-then-redecide transition; that N-801's response_bindings/ artifact type is adopted with a closed schema and identity formula; that R8-002's historical-v1 read regression is permanently fixed by a separated v1-read / v2-write admissibility rule that never rewrites v1 bytes; that the threat model is honest in both directions; and that bounded 1..3 bundles, the FA-002 per-item designator and all iteration-5 corrections are preserved. Excludes R8-001 and R8-003 (implementation-owned), OS-31 resume, and transport-specific UI.",
  "sequence": 25,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_DESIGN_iteration6.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": null
}
```

---

## Summary

I reviewed the corrected `DESIGN.md` against the nine dispatch checks, the seven-point user-settled
contract, the N-802 reproduction, R8-002's isolated cause, N-801, the approved `ANALYSIS.md` /
`PLAN.md` baselines, the shipped `scripts/clarification_protocol.py` surface, both `SKILL.md` files,
`README.md`, `docs/ROADMAP.md` and `docs/COMPATIBILITY.md`. I did not modify any artifact other
than this review file, and I fixed nothing I found.

### What is genuinely SPECIFIED (verified, not merely present)

**Check 1 — head-derivation algorithm and fail-closed conditions: SPECIFIED.** §9 gives a complete,
order-free procedure: build `D` from fully validated decisions per `decision_item_id`; derive edges
only from validated `decision_superseded` events; require exactly one indegree-zero root with every
member reachable from it; then replay validated head-changing events in sequence order. I traced
all three required shapes by hand against that text:

| Shape | Decisions | Events | §9 outcome | Correct? |
| --- | --- | --- | --- | --- |
| N-802 forgery | 2 | **0** | no edges → two indegree-zero roots → step 3 → `ORPHAN_DECISION` | ✅ rejected |
| Changed answer | 2 | 1 × `decision_superseded` | edge `d1→d2`, root `d1`, `d2` reachable; replay names current head → head `d2` | ✅ admitted |
| Cancel-then-redecide | 2 | `decision_cancelled` + `decision_superseded` | cancel resets head to null and records anchor `P`; the superseded edge from `P` is admissible from a null head and consumes the anchor → head `d2` | ✅ admitted via an explicit validated transition |

§9 closes with the unambiguous sentence "This graph algorithm is the only effective-head derivation…
there is no `later` fallback and `normalized_at`, `occurred_at`, directory order, and decision/event
sequence values cannot promote a decision", which is exactly user-contract points 1 and 2.

**Check 2 — non-first-decision linkage with named fields and validation: SPECIFIED.** §9 step 2
names top-level `prior_decision_id` (P) and `next_decision_id` (N), and states five checks: both
non-null, `P ≠ N`, both members of `D`, both records name the event's run/item, and `N.response_id`
equals the event's successful replacement `response_id`; the same values must also appear in
`details` and reconcile with the referenced decision records. "No timestamp comparison is performed"
is stated explicitly. This is implementable as written.

**Check 3 — conflicting fork: SPECIFIED with a named code.** §9 enumerates six disjunctive
conditions (multiple distinct successors for one predecessor; multiple predecessors for one
successor; traversal cycle; two non-identical head-changing events competing for the same replay
state; a transition bypassing the current head or reset anchor; replay otherwise yielding more than
one possible head) and rejects the item as `LINEAGE_FORK`, explicitly forbidding branch selection by
timestamp, sequence, path order, lexical ID or last-write.

**Check 4 — orphan decision: SPECIFIED with a named code.** "A second root or any unreachable
decision is an **orphan decision** and rejects the item as `ORPHAN_DECISION`; it is never treated as
a candidate head." Missing lineage events are routed to `ORPHAN_DECISION`, malformed/missing
referenced targets to `LINEAGE_INVALID`. Each makes the item `invalid`, blocks new authoritative
transitions and preserves every published byte — user-contract point 5.

**Check 5 — v1-read / v2-write split: SPECIFIED and sufficient to prevent an R8-002 recurrence.**
§6 defines two homogeneous shapes. Shape (a), historical request-v1/response-v1 **single-item**:
the absent response item field resolves to the sole request item *in memory*, raw evidence is
verified directly against the response's `raw.sha256`, and "no `response_bindings/` record is
required or synthesized". Shape (b), request-v2/response-v2: exactly one closed, identity-valid
binding whose `response_id` and `raw_sha256` match, else `SCHEMA_MALFORMED`. This is the precise
inverse of R8-002's cause (`_validate_response_evidence` requiring exactly one binding for every
response with no version guard). The mixed-tree rule is also exact: "A tree may contain disjoint
historical-v1 and v2 request lineages; each is admitted independently", while any lineage or logical
item crossing generations, a request with responses from both generations, a v2 response against v1,
a v1 response against v2, and a v1 response under any bundled request are all `SCHEMA_VERSION_MIXED`.
The asymmetric extra-evidence rule is a genuine strengthening over the review's Required Action: "A
binding for a v1 response neither makes it v2 nor makes it invalid by itself; it is
non-authoritative extra evidence and must not be consulted." Immutability is stated twice (§6 and
the Compatibility rules): existing v1 files and directories are never rewritten, migrated or
backfilled — user-contract point 6.

**Check 6 — `response_bindings/` (N-801): ADOPTED, not silent.** It appears in §2's exact layout;
§2 states the closed record (`schema="orca.clarification.response-raw-binding"`, `schema_version=1`,
`binding_id`, `response_id`, `raw_sha256`), its status as a published artifact rather than staging
data, and its "required exactly once per v2 response / prohibited from retrofitting authority into
v1" rule; §3 adds the `binding_id` formula under domain `os30-response-raw-binding-v1`. N-801's
Required Action asked for §2, §3 and the generation rule; all three are present.

**Check 7 — threat model: HONEST in both directions.** §3 claims "structural integrity against
partial/in-place mutation and unlinked append forgery" and explicitly disclaims "cryptographic
authenticity or unforgeability against a writer able to replace or fabricate every response,
binding, decision, and lineage event and recompute all public hashes", naming that folding the raw
digest into `response_id` does not supply it. The Risks section repeats the bound. This satisfies
user-contract point 7 without overclaiming, and — correctly — the design does **not** demand the
unobtainable primitive the previous reviewer ruled out.

**Check 8 — consistency and naming rather than silent editing: CONFIRMED.** `ANALYSIS.md` §F6 and
`PLAN.md` work items 1/4 already require append-only `decision_superseded` / `decision_cancelled` /
`decision_scope_expanded` lineage with single-head derivation and rejection of forks and multiple
heads; §9 tightens that rather than contradicting it. `PLAN.md`'s `MAX_BUNDLE_ITEMS = 3` and
`MAX_RECLARIFICATION_REVISIONS = 2` are preserved verbatim. The shipped
`orca-worker-reviewer-orchestration/SKILL.md:2361-2366` already carries `--decision-item-id` on
answer modes with request-wide `--cancel`, the 1..3 bundle bound, and the v2/immutable-v1 sentence;
`orca-worker-reviewer-loop/SKILL.md:1254-1258` keeps semantic-only parity with an explicit
disclaimer of artifact-runtime parity. `README.md:765`, `docs/ROADMAP.md:313` and
`docs/COMPATIBILITY.md:169` describe OS-30 without any statement the corrected design contradicts;
their missing v1/v2 text is R8-003's implementation-owned obligation and is named again in Expected
Changed Files step 8 and in the "Direct consistency consequences" paragraph. Modification times
confirm nothing was silently edited: `DESIGN.md` 23:08 is the only file newer than the iteration-8
implementation round, with `scripts/clarification_protocol.py` at 22:32, its installed twin at
22:32, `scripts/test_clarification_protocol.py` at 22:31, `orca-worker-reviewer-orchestration/SKILL.md`
at 22:31 and all five docs plus `scripts/validate_skills.py` older still.

**Check 9 — the `later` fallback's legitimate use is REPLACED, not merely deleted: SPECIFIED.**
This was the substance of N-802's "Why this is not blocking" reasoning, and it is discharged. §9
step 4 gives cancel-then-redecide the explicit validated transition (cancelled reset anchor + a
`decision_superseded` edge from that anchor, admissible from a null head only when `P` is the most
recent unmatched anchor, consumed on application), and Expected Changed Files names the paired
implementation obligation: "it must remove the timestamp-based `later` fallback and emit the
specified linkage for cancel-then-redecide" — user-contract point 4.

**Preserved user-settled work: CONFIRMED, no regression.** `MAX_BUNDLE_ITEMS = 3` (§1), items
`1..3` (§4), `decision_item_id` as the stable per-item response designator (§6), per-item
normalization and lineage (§7, §9), required `--decision-item-id` on answer modes with request-wide
`--cancel` (§11), and every iteration-5 F-001…F-005 resolution are intact.

### Why the gate nevertheless fails

One check-1 condition is not specified: **what a `decision_cancelled` event looks like, and what
status a reader derives, for an item that has no effective decision.** §11 asserts that request-level
`--cancel` "appends cancellation lineage for every non-cancelled item" and "makes every item
cancelled, removes every effective head, leaves dependents non-ready", but §9 step 4 requires a
`decision_cancelled` event to carry `prior_decision_id=P` "naming the then-effective decision", and
§9 step 1 says an item with an empty `D` is `unresolved`. The two readings available to an
implementer are mutually exclusive and both are wrong, which is precisely the class of ambiguity the
iteration budget forbids passing through with one IMPLEMENTATION attempt left. Detail in D6-001.

Everything else I found is precision work that does not change what an implementer builds; seven
non-blocking findings are recorded below.

---

## Blocking Findings

### D6-001 — Cancellation of an item with no effective decision has no specified event shape and no derivable status, so §11's request-level cancel contract is unimplementable under §9

- **ID:** D6-001
- **Quality Attribute:** specification completeness / decision-authority integrity (design phase
  contract; profile absent, so blocking rests on the general gate)
- **Severity:** MAJOR
- **Blocking:** YES — **G1** (violates the design's own explicit §11 requirement that request-level
  cancel makes *every* item cancelled and removes every effective head); **G2** (under the
  event-emitting reading the specified `--cancel` path does not work at all); **G5** (the design's
  own Testing Strategy requires "Request-level `--cancel` succeeds for 1, 2, and 3 items… cancels
  every item atomically", and no validation of that claim is derivable from §9 for unanswered items)
- **Responsible Phase:** design
- **Location:** `artifacts/runs/run_db374a3fd83a/DESIGN.md` §9 step 4 (line 486), §9 step 1 (line
  481), §9 closing status paragraph (line 514), §11 (lines 606, 609-611), §1 `ITEM_EFFECTIVE_STATUS`
  (line 118); corroborating implementation surface
  `scripts/clarification_protocol.py:761-766` (`if prior:` guard) and `:873-905`
  (`_effective_decision`).

- **Issue:**
  §9 step 4 states, without exception: *"A `decision_cancelled` event requires `prior_decision_id=P`
  naming the then-effective decision, `next_decision_id=null`, and a validated explicit CANCEL
  response for this run/item/request."* §9 step 5 repeats *"cancellation must name the current
  head."* §9's `details` contract (line 466) likewise says *"`decision_cancelled`: prior decision ID,
  next null."*

  §11 states, without exception: *"`--cancel` … atomically publishes one CANCEL response per item …
  and appends cancellation lineage for every non-cancelled item"* and *"Request-level cancel makes
  every item cancelled, removes every effective head, leaves dependents non-ready."*

  For an item with no effective decision — the ordinary case, since the whole point of `--cancel` is
  abandoning a request nobody has answered — these cannot both hold. The implementer has exactly two
  readings and the design chooses neither:

  1. **Emit the event with `prior_decision_id=null`.** §9 step 4's non-null requirement rejects it;
     §9 routes malformed/invalid transitions to `LINEAGE_INVALID`, and step 1's "unless a malformed
     transition purports to reference it" makes the item `invalid`. Since "any invalid item fails the
     request closed" (§9, line 516), a completely legitimate request-level cancel of an unanswered
     bundle would permanently poison the request. That is a functional break of a shipped, currently
     working path.
  2. **Emit no event for undecided items** (what the code does today at
     `clarification_protocol.py:761-766`, whose `if prior:` guard skips the append). Then `D` is
     empty, no event references the item, and §9 step 1 derives `unresolved` — not `cancelled`.
     §11's "makes every item cancelled" is then false, and §1's `ITEM_EFFECTIVE_STATUS = cancelled`
     is unreachable for these items. Worse, nothing in §9 makes a *later* first decision for such an
     item invalid: it becomes the unique indegree-zero root and is effective. Whole-request
     abandonment — which §11 explicitly calls out as the reason individual-item cancellation is not
     exposed ("cancellation expresses abandonment of the whole human request") — is therefore not
     durable.

- **Reason / Evidence:**
  I reproduced reading 2 on the shipped code rather than inferring it. Two independent items, no
  answers, then a request-level cancel, then a first answer to one item:

  ```text
  cancel status:             CANCELLED
  lineage events:            []                       <- zero events appended
  decisions:                 []
  show heads:                {item_c8f3…: None, item_df6c…: None}

  post-cancel answer status: DECIDED                  <- accepted after cancellation
  show heads after:          {item_c8f3…: None, item_df6c…: 'decision_fe035bfa8d28edad87d66ac7'}
  ```

  The cancelled request produced an effective decision afterwards. Under the corrected §9 this state
  is indistinguishable from a never-cancelled request: `D = {d}` for that item, one indegree-zero
  root, zero events referencing it, head effective. §9's graph derivation has no rule that a
  cancellation blocks a subsequent *first* decision, because the only cancellation semantics it
  defines are "reset the head to null and record `P` as an anchor" — and with no decision there is
  no `P` to record.

  The existing regression net does not cover this. `scripts/test_clarification_protocol.py:141-155`
  (`test_bundle_items_answer_independently_then_request_level_cancel`) answers **all three** items
  before cancelling, so every item has a `prior` and the `if prior:` guard never fires; the
  unanswered-item path has no test at all. The design's Testing Strategy line 842 requires cancel to
  succeed "for 1, 2, and 3 items" but never states whether those items are answered, so it does not
  force the gap open either.

  This is a genuine delta consequence, not a carried-in nit I am re-litigating. Before iteration 6,
  per-item status came from the ingest return value and the `later` fallback made head derivation
  timestamp-driven, so the missing cancellation event was inert. Iteration 6 makes replayed lineage
  the *sole* source of both head and status (§9: "This graph algorithm is the only effective-head
  derivation"), which promotes the omission into a decision-authority hole. It is also squarely
  inside the correction's own subject matter: user-contract point 4 ("the cancel-then-redecide path
  has an explicit validated transition") and point 5 ("orphan decision, missing event, and
  conflicting fork are all FAIL CLOSED") are both satisfied for the decided case and both silent for
  the undecided case.

  I checked the approved baseline before calling this design-owned rather than implementation-owned.
  `ANALYSIS.md:138` says cancellation "appends `decision_cancelled` referencing the effective
  decision"; `PLAN.md:21` says "cancellation requires an explicit response and leaves the item
  unresolved". Neither baseline specifies the no-decision case, so the implementation phase cannot
  derive it — exactly the situation N-802's author described when routing head-derivation semantics
  to the design owner.

- **Required Action:** In §9 (and reconciled in §11), specify the cancellation contract for an item
  with no effective decision. All three sub-points are needed:

  1. **Event shape.** State whether a `decision_cancelled` event for an item with empty `D` is
     emitted with `prior_decision_id=null`, or is not emitted at all. If it is emitted, amend §9 step
     4 and the §9 `details` bullet so `prior_decision_id` is explicitly nullable *only* in this case,
     and state that such an event neither creates nor consumes a reset anchor.
  2. **Derived status.** State how a reader distinguishes `cancelled` from `unresolved` for an item
     with empty `D`, so §1's `ITEM_EFFECTIVE_STATUS = cancelled` is reachable and §11's "makes every
     item cancelled" is true as written.
  3. **Post-cancellation admission.** State explicitly whether a first decision for a cancelled item
     is admissible. If it is not — which is what §11's "cancellation expresses abandonment of the
     whole human request" implies — name the rule (e.g. a cancelled item with no unmatched anchor
     admits no root) and the closed error code (`CANCEL_REQUEST_INVALID`, `LINEAGE_INVALID`, or a
     new code added to the §Error Handling vocabulary). If it *is* admissible, say so and state that
     a cancelled request may be re-answered, so the implementer does not invent a prohibition.

  Then extend the Testing Strategy line 842 bullet to require the unanswered case: request-level
  cancel on a 1-, 2- and 3-item request with **zero** prior answers appends the specified lineage,
  every item derives the specified status, and a subsequent answer reaches the specified outcome.

---

## Non-Blocking Findings

### D6-N01 — `MULTIPLE_EFFECTIVE_HEADS` is in the closed error vocabulary but §9 assigns no condition to it

- **ID:** D6-N01 · **Quality Attribute:** closed error-vocabulary completeness · **Severity:** LOW
  · **Blocking:** NO
- **Location:** DESIGN.md "Error Handling / Compatibility" closed reason list; §9 fork paragraph.
- **Issue:** `MULTIPLE_EFFECTIVE_HEADS` remains in the closed reason set, but §9 routes every
  multi-head condition — including "replay otherwise yields more than one possible head" — to
  `LINEAGE_FORK`. No text assigns a trigger to `MULTIPLE_EFFECTIVE_HEADS`.
- **Reason:** A closed vocabulary with an unreachable member invites the implementer to guess a
  trigger, and R8-001 already shows this run's tests do not pin error codes tightly. Not blocking:
  every condition that *could* raise it already has a named code, so no behavior is undetermined.
- **Required Action:** Either name the condition that produces `MULTIPLE_EFFECTIVE_HEADS` or remove
  it from the closed list and state that `LINEAGE_FORK` subsumes it.

### D6-N02 — §1's closed constant block omits the binding schema-version constant §2 requires

- **ID:** D6-N02 · **Quality Attribute:** specification internal consistency · **Severity:** LOW ·
  **Blocking:** NO
- **Location:** DESIGN.md §1 constants block; §2 `response_bindings/` paragraph.
- **Issue:** §1 declares `REQUEST_SCHEMA_VERSION`, `RESPONSE_SCHEMA_VERSION`,
  `DECISION_SCHEMA_VERSION` and `LINEAGE_SCHEMA_VERSION` as the closed set, but the binding record
  §2 introduces carries `schema_version=1` with no corresponding constant.
- **Reason:** The value is stated exactly in §2, so nothing is ambiguous; only the "public constants
  are closed" claim is now slightly untrue.
- **Required Action:** Add `BINDING_SCHEMA_VERSION = 1` to §1's block.

### D6-N03 — §9 step 2 requires decision records to "name the event's run", but §7's decision record has no run field

- **ID:** D6-N03 · **Quality Attribute:** validation-procedure precision · **Severity:** LOW ·
  **Blocking:** NO
- **Location:** DESIGN.md §9 step 2 ("require both records to name the event's run/item"); §7
  decision record field table.
- **Issue:** The lineage event carries `run_id`; the decision record does not. Run identity is only
  derivable from the decision's `source_ledger_key`, whose §3 grammar puts `<run-id>` in the leading
  component.
- **Reason:** The item half of the check (`decision_item_id`) is direct and unambiguous, and the
  reader is already rooted inside one run's artifact tree, so the run half is defensive rather than
  load-bearing. Not blocking, but with one implementation attempt left it is worth removing the
  guess.
- **Required Action:** State that the run component is taken from the decision's `source_ledger_key`
  under §3's split rule, or drop the run half of the check and rely on the run-rooted read path.

### D6-N04 — §1's `ITEM_EFFECTIVE_STATUS` vocabulary has no carrier in any schema or port return type

- **ID:** D6-N04 · **Quality Attribute:** interface completeness · **Severity:** MEDIUM ·
  **Blocking:** NO
- **Location:** DESIGN.md §1 (`ITEM_EFFECTIVE_STATUS = unresolved | effective | cancelled |
  invalid`); §10 `PublishResult` / `IngestResult` / `HumanApprovalPort.show`; §11 `show` contract.
- **Issue:** No record field and no port return type carries a per-item effective status. `show`
  returns "redacted request/status/effective-decision metadata", which in practice is a
  `decision_item_id -> decision_id | null` mapping; `unresolved` and `cancelled` are both null and
  therefore indistinguishable to any caller.
- **Reason:** Non-blocking on its own because §9 fully determines the status internally and invalid
  items fail closed by raising. It is nonetheless the surface on which D6-001 becomes observable, so
  fixing D6-001 will likely require touching this too.
- **Required Action:** Name the field or return member that carries `ITEM_EFFECTIVE_STATUS` to
  callers, or state explicitly that the vocabulary is internal to head derivation and not exposed.

### D6-N05 — §9 step 3's "every decision after the first" reintroduces ordering language into an intentionally order-free rule

- **ID:** D6-N05 · **Quality Attribute:** specification precision · **Severity:** LOW ·
  **Blocking:** NO
- **Location:** DESIGN.md §9 step 3, first sentence.
- **Issue:** "Every decision after the first must be the `next_decision_id` of exactly one such edge"
  presupposes an ordering over `D`, in a section whose whole purpose is that no ordering exists.
- **Reason:** The very next clause — "exactly one root in `D` (indegree zero), and every member of
  `D` must be reachable from it" — is complete and order-free on its own, so no implementer can
  actually be misled into re-deriving a timestamp order. The phrase is redundant, not contradictory.
- **Required Action:** Delete the phrase or restate it as "every non-root decision".

### D6-N06 — §6's generation split is scoped to raw-evidence admissibility; §9's uniform applicability to v1 trees should be stated outright

- **ID:** D6-N06 · **Quality Attribute:** regression-prevention precision · **Severity:** MEDIUM ·
  **Blocking:** NO
- **Location:** DESIGN.md §6 (read/write admissibility); §9 (head derivation); Compatibility rules.
- **Issue:** §6 splits v1 and v2 for *evidence* admission only. §9's graph derivation is stated as
  universal ("This graph algorithm is the only effective-head derivation") but §6 never says so, and
  §9 never mentions generations. An implementer holding both "do not regress historical v1 reads"
  (R8-002) and "remove the `later` fallback" (N-802) could plausibly guess that v1 trees keep the old
  head derivation for compatibility.
- **Reason:** §9's closing sentence is unambiguous enough that I do not treat this as a real fork in
  the specification, and the user-settled contract point 2 forbids the `later` fallback without
  generation exception. Its practical impact is also nil: R8-002 established there are no OS-30
  artifact trees in the repository or in any release. I record it because a wrong guess here is
  unrecoverable in the last implementation attempt.
- **Required Action:** Add one sentence to §6 or §9 stating that the v1/v2 split governs evidence
  admissibility only and that §9's graph derivation applies identically to both generations —
  including that a hypothetical historical v1 tree whose head depended on the removed `later`
  fallback now derives `ORPHAN_DECISION`, intentionally, with its bytes untouched.

### D6-N07 — §3's structural-integrity sentence is marginally broader than §9 delivers for a forged *first* decision

- **ID:** D6-N07 · **Quality Attribute:** threat-model precision · **Severity:** LOW ·
  **Blocking:** NO
- **Location:** DESIGN.md §3, "…the lineage graph detect records appended without a valid transition".
- **Issue:** Under §9 the first decision for an item is the unique root and requires no transition
  at all, so an append that fabricates a response, binding and first decision for an unanswered item
  is admitted as effective. The sentence as written reads as if every appended record needs a
  transition.
- **Reason:** Explicitly **not** a point-7 overclaim and explicitly **not** a request for a stronger
  primitive. A fabricated first answer is structurally indistinguishable from a legitimate first
  answer bearing its own actor and provenance evidence, so it falls squarely inside the adversary
  §3 already disclaims ("a writer able to replace or fabricate…"). The substantive disclaimer is
  correct and complete; only the one clause is loose.
- **Required Action:** Narrow the clause to "non-first decisions appended without a valid
  transition", leaving the disclaimer paragraph unchanged.

---

## Test Review

Design-phase validation is specification completeness and consistency, so this section assesses
whether the Testing Strategy would actually catch a regression of the corrected behavior — not
execution results. I ran no gate commands; the one Python execution I performed was the D6-001
reproduction against the shipped port, and it mutated nothing outside a temporary directory.

**Adequately covered by the corrected Testing Strategy:**

- The N-802 shape is pinned by name and by mechanism: "Append a second otherwise-valid
  response/binding/decision without a lineage event and assert `ORPHAN_DECISION`, no effective-head
  change, no mutation, and no timestamp/sequence fallback." The paired negative — "Delete the
  required supersession/cancel transition and assert the same fail-closed result" — is a genuine
  mutation-sensitivity test of the kind R8-001 found missing, so the design has learned from that
  finding rather than restating a coverage claim.
- Fork coverage is enumerated per condition (cross-run/item, missing/self target, competing
  successors, multiple predecessors, bypassed reset anchor, cycle, multiple head, malformed and
  out-of-order events) with "fork cases assert `LINEAGE_FORK` exactly" — a code-level assertion,
  which addresses N-806's complaint that only `SOURCE_NOT_OPEN` is currently asserted by code.
- R8-002 is directly pinned: "Historical v1 single-item request/response sets remain readable without
  a binding and without byte changes; v2 requires exactly one digest-matching binding. One tree
  containing disjoint valid v1 and v2 lineages reads both" — this is exactly the end-to-end
  historical-v1 test R8-002's Required Action and R7-N04 both asked for, including the
  bytes-unchanged assertion.
- Cancel-then-redecide is pinned as a linkage requirement, not a timestamp: "cancel-then-redecide
  requires a supersession edge from the most recent cancelled reset anchor."

**Gaps in the test specification:**

1. **The D6-001 case is untested and unspecified.** Line 842 requires request-level `--cancel` to
   succeed "for 1, 2, and 3 items" but never says whether those items are answered. The only
   existing test, `scripts/test_clarification_protocol.py:141-155`, answers every item first, which
   is precisely the branch that avoids the `if prior:` guard at
   `clarification_protocol.py:761-766`. No specified test would notice that cancelling an unanswered
   request appends zero lineage events, and none would notice that a later answer to a cancelled
   request becomes effective. Fixing D6-001 must add both assertions.
2. **No test pins the v1 head-derivation consequence (D6-N06).** The v1 tests assert readability and
   byte-identity but not that §9's graph rules apply to a v1 tree, so an implementation that
   preserved the `later` fallback "for compatibility" on the v1 path would pass every listed test.
3. **The `MULTIPLE_EFFECTIVE_HEADS` code (D6-N01) has no test** because it has no defined trigger.

The remaining implementation-owned obligations R8-001 (mutation sensitivity for the binding's three
sub-checks) and R8-003 (`validate_skills.py` OS-30 anchors plus the v2/immutable-v1 statement across
the seven named documentation files) are correctly retained in the design — R8-001 through the
mutation-style assertions above, R8-003 through Expected Changed Files steps 6 and 8 and the
"Direct consistency consequences" paragraph. I confirmed `scripts/validate_skills.py` is still
unmodified (mtime 13:51:32, predating the OS-30 work) and that the v2/immutable-v1 sentence still
appears only in `orca-worker-reviewer-orchestration/SKILL.md:2366` among the named files. Neither is
design-blocking; both remain the implementation phase's to close.

---

## Final Decision

**RESULT: FAIL · REVIEW_VERDICT: FAIL · DECISION_GATE_STATE: CLEAR**

The correction does the hard part correctly. N-802 is closed at the design layer in the strongest
available form: a validated per-item lineage graph is the sole path to authority, the N-802
reproduction (2 decisions, 0 events, nothing deleted or edited) is rejected as `ORPHAN_DECISION` by
the root/reachability rule, both legitimate shapes remain admissible, forks and orphans have named
error codes and enumerated conditions, and the `later` fallback's one load-bearing use is replaced
by an explicit cancelled-anchor transition rather than deleted. N-801 is adopted with a closed
schema and identity formula. R8-002 is fixed permanently by an admissibility split that is precise
about the mixed-tree case and asymmetric about extra v1 evidence. The threat model is stated
honestly in both directions, claiming what unkeyed content addressing plus a validated graph can
deliver and disclaiming what it cannot, without reaching for the primitive the previous reviewer
correctly ruled out. Bounded 1..3 bundles, the FA-002 per-item designator and every iteration-5
correction survive intact, and no document was silently edited.

I fail the gate on one thing, and I want the Coordinator to see exactly how narrow it is: the design
specifies cancellation completely for an item that *has* a decision and not at all for an item that
does not, while §11 asserts a request-level contract that only the second case can satisfy. I raise
it as blocking rather than as a note for three reasons. It is a direct consequence of this round's
delta — making replayed lineage the sole source of head *and* status is what promoted an inert
omission into a decision-authority hole. It is reproducible on the shipped code today: a cancelled
request accepted a fresh answer and served it as the effective head. And the iteration budget makes
it unrecoverable — IMPLEMENTATION has one attempt for R8-001, R8-002, R8-003 and the N-802 work, and
of the two readings available to that implementer, one permanently poisons a legitimate cancel and
the other leaves whole-request abandonment unenforceable.

The remedy is small and design-owned: three sentences in §9 reconciled with §11, plus one extended
Testing Strategy bullet. Nothing in D6-001 requires a user decision — §11 already states the intended
outcome and the user-settled fail-closed contract already fixes the direction — so the decision gate
is CLEAR and the correct route is a further design correction round, not a question to the user.
Design has used 6 of 8 gate attempts, so the budget accommodates it comfortably; spending one design
attempt here is materially cheaper than spending the last implementation attempt on a guess.

The seven non-blocking findings need not gate iteration 7, but D6-N04 and D6-N06 are cheap to fold
into the same edit and both touch surfaces the D6-001 fix will already open.
