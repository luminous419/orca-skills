# Reviewer Result — DESIGN, iteration 7

RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES
DECISION_GATE_STATE: CLEAR

Run: `run_db374a3fd83a` · Phase: design · Iteration: 7 · Role: reviewer (B3, Claude Opus)
Artifact under review: `artifacts/runs/run_db374a3fd83a/DESIGN.md`
Delta reviewed: closure of my own iteration-6 blocking finding D6-001 plus D6-N01..D6-N07, with a
non-regression check on the iteration-6 head-derivation correction (N-802), the v1/v2 split (R8-002),
`response_bindings/` (N-801), the threat model, bounded bundles, and the FA-002 designator.

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {
    "d6_001_subpoint_1_event_shape": "DESIGN.md:467-468, :495-501 — decision_cancelled details bullet now reads 'prior decision ID, nullable only when the item has no decisions and no effective head, next null'; step 4 emits exactly one null-predecessor marker for the empty-D case and states it 'neither creates nor consumes a reset anchor'.",
    "d6_001_subpoint_2_derived_status": "DESIGN.md:478-483 — empty D derives cancelled with exactly one valid null-predecessor marker, unresolved with none, invalid on a malformed/competing transition, as a terminal classification that skips the root replay; DESIGN.md:599-602 exposes it through show.item_statuses so cancelled and unresolved are distinguishable despite both having a null effective decision.",
    "d6_001_subpoint_3_post_cancellation_admission": "DESIGN.md:508-513 — a cancelled item with the marker admits no first-decision root; any later decision makes the item invalid with LINEAGE_INVALID. Write side reconciled at DESIGN.md:643-646: validation detects the marker before authority publication and returns LINEAGE_INVALID creating no response, binding, decision, or lineage event.",
    "d6_001_testing_bullet": "DESIGN.md:876-881 — for sizes 1, 2 and 3 a zero-prior-answer request-level cancel appends exactly one null-predecessor marker per item, every item derives cancelled, no reset anchor exists, and a subsequent first answer fails LINEAGE_INVALID with no effective head and no mutation.",
    "n802_reproduction_still_rejected": "DESIGN.md:489-492 — 2 decisions and 0 events yields two indegree-zero roots; the second root is an orphan decision and rejects the item as ORPHAN_DECISION. DESIGN.md:530-536 restates that no later fallback, normalized_at, occurred_at, directory order or sequence can promote a decision, and applies the same rule to a hypothetical v1 tree.",
    "legitimate_shapes_admitted": "Changed answer: DESIGN.md:485-488 validated decision_superseded edge P->N with named prior_decision_id/next_decision_id, distinct P,N in D, item match, run match via the source_ledger_key split, and N.response_id equal to the event's replacement response. Cancel-then-redecide: DESIGN.md:495-507 cancel with prior_decision_id=P records P as reset anchor, and the redecision is admitted only through a decision_superseded edge from the most recent unmatched anchor, which consuming establishes as head.",
    "d6_n01_closed": "MULTIPLE_EFFECTIVE_HEADS is gone from the closed reason list at DESIGN.md:760-765 and DESIGN.md:527-529 states LINEAGE_FORK subsumes every multi-head construction or replay state.",
    "d6_n02_n07_closed": "BINDING_SCHEMA_VERSION = 1 at DESIGN.md:101; run half of the step-2 check bound to the source_ledger_key split at DESIGN.md:487; 'every non-root decision' at DESIGN.md:489; show.item_statuses at DESIGN.md:598-602; generation-uniform §9 plus its v1 test at DESIGN.md:534-536 and :886-889; threat-model clause narrowed to 'non-first decisions appended without a valid transition' at DESIGN.md:200-206.",
    "r8_002_not_regressed": "DESIGN.md:379-398 — shape (a) historical v1 single-item lineage verifies raw evidence directly against response raw.sha256 with no binding required or synthesized; shape (b) v2 requires exactly one identity-valid binding; a tree may hold disjoint v1 and v2 lineages, each admitted independently; a binding attached to a v1 response is non-authoritative and must not be consulted; v1 bytes are never rewritten, migrated or backfilled.",
    "threat_model_honest": "DESIGN.md:200-210 claims structural integrity against partial/in-place mutation and unlinked append forgery only, and explicitly disclaims cryptographic authenticity against a writer able to fabricate every response, binding, decision and event and recompute all public hashes, naming the raw-digest-in-response_id variant as equally defeated.",
    "settled_requirements_preserved": "MAX_BUNDLE_ITEMS = 3 at DESIGN.md:99 with the 1..3 antichain contract at DESIGN.md:321-347; the FA-002 per-item decision_item_id designator at DESIGN.md:348-361 and §11; no production or baseline file was edited by this design round (only DESIGN.md carries a fresh mtime in the run directory)."
  },
  "grounds": "All three sub-points of my iteration-6 Required Action for D6-001 are now specified rather than mentioned, with reconciled read-side and write-side rules, a named closed error code, and the extended zero-prior-answer testing bullet; D6-N01..D6-N07 are all closed; and the iteration-6 contracts already verified (N-802 head-derivation graph, prior/next linkage validation, LINEAGE_FORK and ORPHAN_DECISION contracts, the v1-read/v2-write split closing R8-002, response_bindings adoption, the honest threat model, bounded bundles and the FA-002 designator) are intact. The remaining findings are LOW/MEDIUM precision and baseline-alignment notes that determine no behavior an implementer must guess.",
  "iteration": 7,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "design",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T23:58:00+09:00",
  "responsible_phase": "design",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Design gate review of the iteration-7 correction to DESIGN.md for Jira OS-30, covering closure of D6-001 and D6-N01..D6-N07 and non-regression of the iteration-6 head-derivation, generation-split, binding, threat-model, bundle and per-item-designator contracts.",
  "sequence": 27,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_DESIGN_iteration7.md",
  "state": "CLEAR",
  "verdict": "PASS",
  "verifies": {
    "worker_record_key": "run_db374a3fd83a/design/7/B2#26"
  }
}
```

---

## Summary

The correction closes my own iteration-6 blocking finding in full, and it closes it in the shape the
Required Action asked for rather than by adding prose around the hole.

**D6-001 — closed, all three sub-points plus the testing bullet.**

1. *Event shape.* §9 step 4 (DESIGN.md:495-501) now says that when `D` is empty and the item has no
   effective head, request-level cancellation emits exactly one `decision_cancelled` event with
   `prior_decision_id=null`, that this shape is valid *only* for that case, and that it "neither
   creates nor consumes a reset anchor." The §9 `details` bullet (line 467) was amended to match
   ("nullable only when the item has no decisions and no effective head"), so the closed tagged-union
   contract and the step-4 procedure no longer disagree — which was the exact contradiction I
   reported.
2. *Derived status.* §9 step 1 (lines 478-483) makes empty-`D` classification explicit and terminal:
   `cancelled` with exactly one valid null-predecessor marker, `unresolved` with none, `invalid` on a
   malformed or competing transition, returned "without attempting the root replay in steps 2-5."
   §1's `ITEM_EFFECTIVE_STATUS = cancelled` is therefore reachable and §11's "makes every item
   cancelled" is now true as written. The distinction is observable: §10 (lines 599-602) adds
   `show.item_statuses`, a `decision_item_id -> ITEM_EFFECTIVE_STATUS` mapping alongside the
   effective-decision mapping, explicitly so that `unresolved` and `cancelled` remain distinguishable
   when both carry a null effective decision. That also closes D6-N04, which I had flagged as the
   surface on which D6-001 becomes observable.
3. *Post-cancellation admission.* The design chose prohibition and named it. §9 step 5 (lines
   508-513): "the cancelled item admits no first-decision root: any later decision for the item makes
   it `invalid` with `LINEAGE_INVALID`; request abandonment cannot be reversed by answering an item
   that had no prior decision." §11 (lines 643-646) reconciles the write side — validation detects the
   marker *before* authority publication and returns `LINEAGE_INVALID` without creating a response,
   binding, decision or lineage event — and distinguishes it from the already-decided
   cancel-then-redecide transition. Both sides use the same closed code, which already exists in the
   §Error Handling vocabulary, so no new code was invented.
4. *Testing.* Line 842's bullet was extended exactly as asked (lines 876-881): for each size 1, 2 and
   3, a zero-prior-answer request-level cancel appends exactly one null-predecessor marker per item,
   every item derives `cancelled`, no reset anchor exists, and a subsequent first answer fails
   `LINEAGE_INVALID` with no effective head and no mutation. This is precisely the state I
   reproduced on the shipped code at iteration 6 (`cancel status: CANCELLED`, zero events, then
   `post-cancel answer status: DECIDED`), and the extended bullet now forces the regression net over
   it.

**Task checklist 1-9 — verified specified, not merely mentioned.**

1. *Head derivation.* §9's five steps are an executable procedure over `D` (validated decisions) and
   validated events, with every fail-closed condition enumerated. It rejects the N-802 reproduction:
   two decisions and zero events give two indegree-zero roots, and step 3 rejects the item as
   `ORPHAN_DECISION` (lines 489-492). It admits the changed-answer shape (2 decisions + one validated
   `decision_superseded`) and the cancel-then-redecide path. Worth stating precisely for the
   implementer: the design's cancel-then-redecide shape is *2 decisions + `decision_cancelled` +
   `decision_superseded`*, not 2 decisions + a bare cancellation. The cancellation resets the head to
   null and records `P` as an anchor; the redecision is admissible only through a `decision_superseded`
   edge with `prior_decision_id=P` when `P` is the most recent unmatched anchor, and applying it
   consumes the anchor. That is stricter than a bare-cancellation reading and is exactly what
   user-contract points 3 and 4 require — a validated transition, never an implicit promotion — so I
   record it as a correct reading, not a defect. It does mean the last implementation attempt must
   emit that supersession event even though the current head is null at that moment; the design says
   so directly (§9 step 4, line 500, and the Expected Changed Files paragraph at lines 824-826), so it is
   specified rather than left to inference.
2. *Non-first-decision linkage.* Named fields (`prior_decision_id`, `next_decision_id`, both
   top-level and mirrored in `details`), with a complete validation list: non-null, distinct, both in
   `D`, both records naming the event's item, run reconciled through the §3 structural split of
   `source_ledger_key` (closing D6-N03's guess), and `N.response_id` equal to the event's successful
   replacement response. "No timestamp comparison is performed" is stated inline.
3. *Conflicting fork.* Six enumerated conditions (multiple successors, multiple predecessors, cycle,
   two competing non-identical head-changing events, a transition bypassing the head/anchor, or any
   replay yielding more than one possible head) → `LINEAGE_FORK`, with explicit refusal to select a
   branch by timestamp, sequence, path order, lexical ID, or last write.
4. *Orphan decision.* A second root or any decision unreachable from the unique root →
   `ORPHAN_DECISION`, "never treated as a candidate head."
5. *v1-read / v2-write split.* §6 (lines 381-399) states two admissible homogeneous shapes; shape (a)
   verifies raw evidence directly against the response's `raw.sha256` with no binding required *or
   synthesized*, which is the exact inverse of the R8-002 regression; shape (b) requires exactly one
   identity-valid binding. The both-generations tree rule is explicit — disjoint v1 and v2 lineages
   coexist and are admitted independently, while any lineage or item that *crosses* generations is
   `SCHEMA_VERSION_MIXED`. A binding found beside a v1 response is non-authoritative extra evidence
   that "must not be consulted," so the v2 artifact cannot leak back into v1 admission. Existing v1
   files are never rewritten, migrated or backfilled.
6. *`response_bindings/`.* Adopted, not silent: present in the §2 layout, with a closed five-field
   schema, an identity formula in §3, and the v2-only authority rule in §6. N-801 is closed.
7. *Threat model.* §3 claims structural integrity against partial/in-place mutation and unlinked
   append forgery, and disclaims cryptographic authenticity against a full-tree fabricator, naming
   the raw-digest-in-`response_id` variant as equally defeated. It neither overclaims point 7 nor
   demands the unobtainable. D6-N07's loose clause is narrowed to "non-first decisions appended
   without a valid transition," which is what §9 actually delivers.
8. *Consistency and naming.* Both SKILL.md files, README.md, INSTALL.md, CHANGELOG.md,
   docs/ROADMAP.md and docs/COMPATIBILITY.md are named as implementation/documentation-owned updates
   with the exact content required (`--decision-item-id` on answer modes, `--cancel` request-wide, v2
   writes / immutable historical v1). No file outside DESIGN.md carries a fresh mtime in the run
   directory, and nothing in the shipped docs contradicts the corrected design. One baseline-alignment
   gap is recorded below as D7-N02.
9. *`later` fallback.* Replaced, not deleted-and-abandoned. The load-bearing legitimate use I was
   asked to protect — cancel-then-redecide — now runs through the validated reset-anchor +
   `decision_superseded` transition, and the design instructs implementation to remove the
   timestamp-based fallback and emit that linkage.

**Iteration-6 contracts confirmed not regressed.** The N-802 head-derivation graph, the
prior/next linkage validation, the `LINEAGE_FORK` / `ORPHAN_DECISION` contracts, the v1-read/v2-write
split closing R8-002, `response_bindings` adoption, the honest threat model, `MAX_BUNDLE_ITEMS = 3`
with the genuine-antichain rule, and the FA-002 per-item `decision_item_id` designator are all intact
and, where touched, only tightened.

Given the budget — one design attempt and one implementation attempt remain — I applied the strict
specification-completeness standard the boundary asks for and looked specifically for places where an
implementer would have to guess. I found none that determine behavior. The four notes below are
precision and baseline-alignment items; none of them leaves an outcome undetermined, so under the
profile-absent gate (no blocking quality attribute; G1-G5 only) none is blocking.

---

## Blocking Findings

None. D6-001 is closed in full and no new blocking defect was found.

---

## Non-Blocking Findings

### D7-N01 — §9 step 1's "exactly one" marker count and step 5's byte-identical-replay idempotency are not reconciled for the empty-`D` case

- **ID:** D7-N01
- **Quality Attribute:** specification precision (derivation determinism)
- **Severity:** LOW
- **Blocking:** NO — no G1-G5 violation; both readings produce the same status on every path the
  design's own write side can actually produce, and no user-settled point is affected.
- **Location:** `artifacts/runs/run_db374a3fd83a/DESIGN.md:478-483` (step 1) and `:512` (step 5,
  "A repeated byte-identical marker is idempotent").
- **Issue:** Step 1 derives `cancelled` for empty `D` when "exactly one valid null-predecessor
  cancellation marker from step 4 exists," and then instructs the reader to "return that status
  without attempting the root replay in steps 2-5." The idempotency rule that collapses a repeated
  byte-identical marker lives in step 5, which step 1 has just told the reader to skip. If a replayed
  request-level `--cancel` ever lands a second byte-identical marker at a new `lineage/<sequence>/`
  directory, a literal reader counts two markers, fails the "exactly one" test, and derives `invalid`
  — while step 5 and §11's "exact replay is idempotent" both say the item stays `cancelled`.
- **Reason:** Low impact and probably unreachable: lineage sequence allocation plus the closing
  §9 sentence ("Exact replay of an already applied event is idempotent only when every byte and
  semantic field matches") and §11's all-or-nothing replay contract together imply the writer does
  not append a duplicate. But the empty-`D` path is the one D6-001 just created, it is the path the
  new 1/2/3-item zero-answer tests exercise, and the count rule and the idempotency rule are on
  opposite sides of a "do not read steps 2-5" instruction. With one implementation attempt left this
  is worth one clause rather than an inference.
- **Required Action:** In §9 step 1, state that byte-identical duplicate markers collapse to one
  before the "exactly one" count is applied (or, equivalently, that the count is over distinct
  `event_id` values), so the empty-`D` classification does not depend on a rule stated only in a step
  that step 1 skips.

### D7-N02 — the derived `cancelled` status and the post-cancellation prohibition diverge from ANALYSIS.md and PLAN.md, which the design asserts need no change

- **ID:** D7-N02
- **Quality Attribute:** baseline consistency / named-not-edited discipline
- **Severity:** MEDIUM
- **Blocking:** NO — the divergence is a refinement the approved baseline does not forbid, DESIGN.md
  itself is unambiguous about the behavior, and no implementation decision is left open. Design-taste
  and document-alignment items are non-blocking by default under the active gate.
- **Location:** `artifacts/runs/run_db374a3fd83a/DESIGN.md:830-831` ("ANALYSIS.md and PLAN.md need no
  change"); `ANALYSIS.md:138` and `:140`; `PLAN.md:21`; corresponding DESIGN.md `:478-483` and
  `:508-513`.
- **Issue:** `ANALYSIS.md:138` says cancellation "appends `decision_cancelled` referencing the
  effective decision … and returns the item to an unresolved state," and `PLAN.md:21` says
  "cancellation requires an explicit response and leaves the item unresolved." The corrected design
  does two things those sentences do not describe: it emits a marker that references *no* effective
  decision (the null-predecessor case), and it derives `cancelled` — a status distinct from
  `unresolved` — with the additional consequence that a zero-answer cancelled item can never be
  answered. The design's Expected Changed Files section nevertheless states that ANALYSIS.md and
  PLAN.md need no change, addressing only the retained 1..3 bundle requirement.
- **Reason:** Substantively the baseline intent is preserved: no replacement decision, no authority,
  no dependency satisfaction, run stays blocked. `unresolved` in the baseline is coarse prose written
  before the four-value `ITEM_EFFECTIVE_STATUS` vocabulary existed, and D6-001's Required Action
  explicitly permitted either admission choice provided it was named — which it is. So this is
  alignment bookkeeping, not a dropped requirement. It is worth recording because the phase contract
  asks that documents needing change be *named* rather than silently diverged from, and because a
  later reader comparing PLAN.md against the shipped behavior will otherwise see a contradiction with
  no trace of who decided it.
- **Required Action:** Either add one line to the Expected Changed Files consistency paragraph naming
  `ANALYSIS.md:138`/`:140` and `PLAN.md:21` as prose superseded by §9's status vocabulary (without
  editing them in this phase), or add one sentence to §9 stating that the baseline's "unresolved"
  wording means "no effective decision, no authority, run remains blocked" and that §9 refines it
  into the two distinguishable statuses `unresolved` and `cancelled`.

### D7-N03 — the closed error list names `ID_CONFLICT` while the body text and the shipped module use `CLARIFICATION_ID_CONFLICT`

- **ID:** D7-N03
- **Quality Attribute:** closed error-vocabulary consistency
- **Severity:** LOW
- **Blocking:** NO — body text (three occurrences) and the shipped implementation already agree, so
  the effective name is determined; only the list entry is short.
- **Location:** `artifacts/runs/run_db374a3fd83a/DESIGN.md:761` (closed reason list, `ID_CONFLICT`)
  versus `:151`, `:550`, `:715`, `:874` (`CLARIFICATION_ID_CONFLICT`); shipped
  `scripts/clarification_protocol.py:59-60` (`class ClarificationConflict … code =
  "CLARIFICATION_ID_CONFLICT"`).
- **Issue:** The design's single normative list of closed error reasons uses a different token from
  everywhere else in the same document and from the code the last implementation attempt must keep.
- **Reason:** Pre-existing since the iteration-5 baseline, not introduced by this correction, and the
  weight of evidence points one way. I raise it only because R8-001 requires the final implementation
  attempt to pin error codes tightly in tests, and a closed vocabulary that disagrees with itself is
  the wrong input to that specific task.
- **Required Action:** Make the list entry read `CLARIFICATION_ID_CONFLICT` so the closed vocabulary
  matches the body text and the shipped code.

### D7-N04 — the "Current Architecture" statement about `release_manifest.py` is stale

- **ID:** D7-N04
- **Quality Attribute:** documentation accuracy
- **Severity:** LOW
- **Blocking:** NO — the implementation step derived from it is idempotent and already satisfied.
- **Location:** `artifacts/runs/run_db374a3fd83a/DESIGN.md:71-73`; actual
  `scripts/release_manifest.py:86-87`.
- **Issue:** The design says "`scripts/release_manifest.py` currently permits only
  `tools/run_logging.py` in the orchestration Skill." The working tree already enumerates both
  `tools/run_logging.py` and `tools/clarification_protocol.py`.
- **Reason:** The sentence describes the pre-implementation baseline and was accurate when written;
  implementation step 7 asks for exactly the state that now exists, so nothing is misdirected. Purely
  an accuracy note for a document the final implementation attempt will read.
- **Required Action:** Restate as an already-satisfied condition, or drop the "currently" claim and
  keep only the requirement that both installed tools be enumerated.

---

## Test Review

Design-phase validation here is specification completeness and consistency, not test execution, so I
assessed whether the Testing Strategy forces the corrected behavior open and whether every new rule
has a named verification anchor.

- **The D6-001 gap is now pinned.** The extended cancellation bullet (lines 876-881) covers exactly
  the case that had no test: request-level cancel on 1-, 2- and 3-item requests with **zero** prior
  answers, asserting marker count per item, derived `cancelled` status, absence of a reset anchor,
  and `LINEAGE_INVALID` with no mutation on a subsequent first answer. At iteration 6 the only
  existing cancellation test
  (`scripts/test_clarification_protocol.py:141-155`) answered all three items first, so the `if
  prior:` guard at `scripts/clarification_protocol.py:761-766` never fired; this bullet is what forces
  that guard to be rewritten rather than left in place.
- **Head derivation has adversarial coverage, not just happy-path coverage.** The strategy requires
  appending a second otherwise-valid response/binding/decision *without* a lineage event and
  asserting `ORPHAN_DECISION` with no head change and no mutation — the direct N-802 reproduction —
  and separately requires deleting the supersession/cancel transition and asserting the same
  fail-closed result. Fork cases must assert `LINEAGE_FORK` "exactly," which is the right shape given
  R8-001's finding that this run's tests do not pin codes tightly.
- **Generation coverage matches §6's rules one-for-one.** Historical v1 single-item sets readable
  without a binding and byte-unchanged; v2 requiring exactly one digest-matching binding; one tree
  with disjoint valid v1 and v2 lineages reading both; and mixed-generation lineages, mixed response
  versions, v2-against-v1, v1-against-v2, bundled v1 responses, and unknown versions all failing
  closed. The added v1 bullet (lines 886-889) pins D6-N06: a hypothetical homogeneous v1 tree with two
  decisions and no supersession event must fail `ORPHAN_DECISION`, never use `later`, and preserve
  every historical byte.
- **Cancel-then-redecide has its own anchor assertion** ("cancel-then-redecide requires a supersession
  edge from the most recent cancelled reset anchor"), so the extra event the design requires is
  test-visible rather than something the implementer might substitute with a bare cancellation.
- **The zero-delta OS-31 boundary and the secret-canary matrix are unchanged** and still name every
  channel (stdout/stderr, response `record.json`, any other JSON, exception text, OS-29 ledger,
  orchestration/timing logs, task specs, lineage summaries, exports, package archives).
- **Residual risk I record without blocking:** the strategy still specifies coverage by behavior
  rather than by mutation sensitivity. R8-001 failed the last implementation round precisely because
  removable sub-checks left the suite green. Nothing in the design forbids the implementation phase
  from adding mutation-sensitivity assertions — R8-001 is implementation-owned and its Required
  Action already names the cases — so this is not a design defect, but with one implementation
  attempt left it is the likeliest way this run still fails at final review.

---

## Final Decision

RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES
DECISION_GATE_STATE: CLEAR

D6-001, the sole blocking finding carried into this iteration, is closed on all three sub-points with
reconciled read-side and write-side rules, a named closed error code drawn from the existing
vocabulary, an observable per-item status carrier, and the extended zero-prior-answer testing bullet.
D6-N01 through D6-N07 are closed. Every contract verified at iteration 6 — the validated-lineage-only
head derivation, the named prior/next linkage and its validation procedure, the `ORPHAN_DECISION` and
`LINEAGE_FORK` contracts, the v1-read/v2-write split that closes R8-002 without rewriting v1 bytes,
`response_bindings/` adoption, the honest structural-integrity threat model, bounded 1..3 bundles, and
the FA-002 per-item `decision_item_id` designator — is intact.

All seven user-settled contract points are specified: head reachable only through validated lineage;
no timestamp/sequence/`later` promotion; supersession requiring valid `decision_superseded` linkage;
an explicit validated cancel-then-redecide transition through the reset anchor; orphan, missing-event
and fork all fail closed with named codes; separated v1-read and v2-write admissibility with v1 never
rewritten; and a threat model that claims structural integrity without claiming cryptographic
authenticity. The design does not overclaim point 7, and I am not asking for a stronger primitive.

The four non-blocking notes (D7-N01 duplicate-marker count reconciliation, D7-N02 ANALYSIS/PLAN
wording alignment, D7-N03 `ID_CONFLICT` vs `CLARIFICATION_ID_CONFLICT` in the closed list, D7-N04
stale `release_manifest.py` statement) determine no behavior and leave no implementer guess; they are
one-clause edits that may be folded into the implementation round's documentation work or left as-is
without risk to the gate. The design phase should not spend its remaining attempt on them.

The design is implementable as written. Proceed to the final implementation attempt.
