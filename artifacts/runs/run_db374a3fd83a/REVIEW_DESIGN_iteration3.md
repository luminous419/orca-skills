# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

Run: `run_db374a3fd83a` · Phase: design · Iteration: 3 · Role: reviewer
Target: `artifacts/runs/run_db374a3fd83a/DESIGN.md`
Baseline: ANALYSIS/PLAN PASS · Delta under review: F-004 identity fallback + resolution trace

## Summary

F-004 is fixed for the case it was raised on, and I verified the fix by executing the
repository rather than by reading the Resolution Trace.

- **F-004's demonstrated failure is genuinely closed.** §3 now makes identity a tagged
  rule instead of a bare `(run_id, phase, open_item)` tuple, §4 declares source
  `open_item` nullable and names what supplies identity in its place, and the Testing
  Strategy adds the AC1 fixture ("a single valid `NEEDS_INPUT` block with `open_item:
  null` publishes exactly one structured request"). The exact record the iteration-2
  review demonstrated — a valid `NEEDS_INPUT` with `open_item: None` — now publishes a
  request instead of fail-closing to nothing. The identity is deterministic and
  implementable: SHA-256 over a canonical tagged array, no clock, no ordering, no prose.
- **The Worker went further than the required action asked, and in the right
  direction.** The iteration-2 required action said a null `open_item` "must never
  merge". The design instead folds a bound Reviewer B3 onto `verifies.worker_record_key`,
  which preserves the F-002 merge for a null-labelled agreeing pair. I checked that this
  is sound, not a shortcut: `verifies` is validated by OS-29
  (`validate_ledger_record` decision_gate.py:425-431, `record_identity_defect` :359-362,
  `verification_binding_defect` :970-983), only a Reviewer B3 may carry it, exactly one
  B3 may ever verify one B2 (:649-651, `test_only_one_reviewer_may_verify_one_worker_
  classification` at test_decision_gate.py:750), and `unresolved_block_reason`'s own
  terminal shape B pairs the two records by that same field (:887-928). The design is
  reusing the repository's authoritative pairing rather than inventing one.
- **F-001, F-002 and F-003 are unregressed.** `redacted_preview` appears only in the
  Resolution Trace; `redact_text` appears once, as the §13 negation; §3's run-component
  rule still restates `_ensure_run_artifact_root` (run_logging.py:336-364) exactly.

One blocking finding remains, and it is a direct consequence of *how* the F-004 fix was
applied rather than a new topic. The tagged rule is evaluated **per judgement record**.
`open_item` is free text that two independent agent sessions write independently, and
OS-29 never compares the Worker's value with its Reviewer's. I enumerated all five label
combinations of the bound B2/B3 pair by execution: all five are valid ledgers that reach
the same `DECISION_BLOCKED` terminal, and the design assigns one item to only two of
them. The other three publish two requests for one question — the F-002 defect the gate
already blocked on, now reachable through label disagreement instead of through
per-judgement identity.

The quality profile is absent, so this judgement uses only explicit requirements (Jira
OS-30, fetched live this iteration), the design phase contract, and the minimal general
gate G1-G5. No style preference, generic checklist item, or OS-31/transport concern was
promoted to blocking. Architecture settled in iterations 1-2 — module boundary, artifact
layout, port neutrality, numeric bounds, schema v1, OS-31 exclusion — was not
re-litigated.

## Blocking Findings

```text
ID: F-005
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: DESIGN.md §3 ("logical_item_key = canonical([\"named\", run_id, phase, open_item])
          when open_item is a non-empty string = canonical([\"producer\",
          canonical_producer_ledger_key]) when open_item is null or empty"; "When it is null
          or empty, the identity uses `canonical_producer_ledger_key`"); §12 ("The adapter
          groups records by the tagged logical-item rule in §3"; "It publishes once per open
          question or unlabeled producer, never once per judgement record"); Testing
          Strategy, "Request, bundle, and dependency behavior" bullet 3 ("an agreeing
          Reviewer B3 block with the same `(run, phase, open_item)`")
Issue: The tagged rule chooses its branch from EACH record's own `open_item`, so the
       verification fold to `verifies.worker_record_key` applies only when the B3's label
       happens to be null. Nothing in OS-29 or the Skill contract makes a Reviewer's
       `open_item` equal, or equally null to, the Worker's. When the two disagree — B2
       labelled and B3 null, B2 null and B3 labelled, or both labelled with different text
       — the pair derives two different logical keys, so one question becomes two decision
       items and two request artifacts. That is the F-002 defect (asking a human the same
       question twice) reached by a different route, and it contradicts §12's own claim
       that publication happens "once per open question ... never once per judgement
       record".
Reason / Evidence: Demonstrated by execution in the worktree, not by argument. I built the
       bound B2/B3 pair from the real fixtures (`worker_needs_input.json`,
       `run_entry_declaration.json`) against the real policy
       (`decision_policy.load_decision_policy(Path("orca-worker-reviewer-orchestration/
       SKILL.md"))`), varying only the two `open_item` values. Every combination passes
       `validate_ledger_record` for all three records and reaches the same terminal:

         B2 named / B3 null          -> DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope
                                        open: {.../B2#1, .../B3#2}
         B2 null  / B3 named         -> DECISION_BLOCKED:... , open: {B2#1, B3#2}
         both named, different text  -> DECISION_BLOCKED:... , open: {B2#1, B3#2}
         both null                   -> DECISION_BLOCKED:... , open: {B2#1, B3#2}
         both named, identical       -> DECISION_BLOCKED:... , open: {B2#1, B3#2}

       Applying §3 to each record of the pair:
         both null                  -> producer(B2#1)     vs producer(B2#1)      SAME
         both named identical       -> named(run,phase,L) vs named(run,phase,L)  SAME
         B2 named / B3 null         -> named(run,phase,L) vs producer(B2#1)      DIFFERENT
         B2 null  / B3 named        -> producer(B2#1)     vs named(run,phase,L)  DIFFERENT
         both named, different text -> named(...,L1)      vs named(...,L2)       DIFFERENT
       Three of the five valid shapes split. §3's guard "Judgements sharing a key must
       agree on the state/reason and request contract; disagreement fails closed" cannot
       catch any of them, because the keys differ and the judgements never share one.
       Nothing in the repository constrains the two labels:
       - `open_item` is compared nowhere. `grep -n open_item scripts/decision_gate.py`
         returns only REQUIRED_LEDGER_RECORD_FIELDS (:148) — a presence requirement — and
         `open_items()`/`unresolved_block_reason` line comments. `grep -n open_item
         scripts/decision_policy.py` returns no hits. No clause relates a B3's label to the
         B2 it verifies.
       - Both harnesses stamp the field with `setdefault` onto the AGENT-supplied fenced
         record (orca_runtime_harness.py:2544, e2e_harness.py:1029), immediately below the
         block that stamps `verifies`, `boundary`, `source` and `role` as harness-owned. So
         the Worker session and the Reviewer session each choose their own value with no
         coordination and no shared vocabulary.
       - The Skill never asks for it: `grep -rn open_item orca-worker-reviewer-
         orchestration/ --include=*.md` returns exactly one line, SKILL.md:1090, the
         `no_unresolved_open_item` admissibility constant. There is no record template that
         would make two agents converge on the same string.
       This violates an explicit requirement, not a preference. Jira OS-30 `## Scope`
       bullet 1 is "stable decision/request identity", and AC1 is "`NEEDS_INPUT`은 stable ID가
       있는 구조화된 request artifact를 생성한다". An identity that depends on whether two
       independent agents chose the same free-text string is not stable: the same question,
       the same run, the same phase and the same OS-29 judgement pair yield one request or
       two depending on wording.
       This is an incompleteness of the F-004 correction, not a pre-existing defect and not
       a re-statement of F-002. Iteration 2 fail-closed on every mixed-label shape (§4
       required a non-empty `open_item`, so the null side was rejected and nothing was
       published). Iteration 3 correctly stopped rejecting them, but tagged each record
       independently, so the shapes now publish twice instead of publishing nothing. The
       authoritative pairing the design needs is already in its hand and already validated:
       §3 reads `verifies.worker_record_key` — it just does not consult it when the B3
       carries a label.
Required Action: Inside §3, make the verification fold UNCONDITIONAL and evaluate it BEFORE
       the named/producer tag, so the tag is a property of the logical producer rather than
       of the reading record:
         canonical_producer_record(r) = the Worker B2 record named by
             r.verifies.worker_record_key, when r is a Reviewer B3 whose `verifies` has
             passed OS-29 binding validation against that record; otherwise r itself.
         logical_item_key(r) = named/producer tag computed from
             canonical_producer_record(r).open_item and its ledger key — never from r's own
             open_item.
       All five shapes then collapse to one item keyed by the Worker's own label (or by the
       Worker's ledger key when the Worker's label is null), which is exactly the pairing
       `unresolved_block_reason` shape B already performs. The bound B3's own `open_item`
       becomes descriptive evidence and is not identity-bearing. Say in §4 that
       `RequestItem.open_item` is copied from the canonical producer record, and in §12 that
       grouping folds bound verifications before tagging. Add one Testing Strategy fixture
       for a bound B2/B3 pair whose `open_item` values DISAGREE (both directions, plus two
       differing non-empty labels) publishing exactly one request; the current bullet
       presupposes "the same `(run, phase, open_item)`" and so cannot detect this. Do not
       fix this by requiring the two labels to match in OS-29 — the design's own
       Compatibility rules forbid changing `CLOSED_LEDGER_RECORD_FIELDS` or OS-28
       transitions, and the repository's valid fixtures carry both null and non-null values.
```

## Non-Blocking Findings

```text
ID: N-201
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §3 ("two independent null-labelled producers have different producer
          ledger keys and cannot coalesce"); Testing Strategy ("two independent
          null-labelled producer records in one `(run, phase)` derive different item IDs
          and never coalesce")
Issue: The non-coalescence property is correct as a unit property of the identity function,
       but the shape it describes cannot reach the §12 publication seam, so it must not be
       written as an integration fixture.
Reason / Evidence: Two independent null-labelled blocking producers in one `(run, phase)`
       means a B3 that does NOT verify the open B2. I executed that shape: terminal shape A
       is skipped (`recomputed != {head_key}`), and shape B rejects it at
       `verification_binding_defect` because `verifies` is None, so
       `unresolved_block_reason` returns None and the run is classified a producer defect,
       not a decision block. No record set reaches the adapter. This matches the bound the
       iteration-2 review already drew around F-004's second-order claim
       (test_decision_gate.py:1410; test_orca_runtime_contract.py:8595).
Required Action: Optional — keep the fixture at the identity-function level and say so, so
       implementation does not spend effort constructing an unreachable harness scenario.
```

```text
ID: N-202
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §6 (`raw.sensitivity`), §7 (`resolves` | "exact `source_ledger_key`"),
          §12 (e2e seam), Testing Strategy security bullet ("any other JSON")
Issue: Four of the five optional notes recorded in iteration 2 were not taken up, and the
       fifth only implicitly. They remain the Worker's judgement and do not affect this
       gate; recorded so they are carried rather than lost.
Reason / Evidence: N-101 — §7's decision record still binds a singular `source_ledger_key`
       and `resolves` is still "exact `source_ledger_key`"; `source_ledger_keys` appears
       only on the request item (line 237) and the port type (line 416). N-102 — `raw`'s
       `sensitivity` field is still listed with no stated source or default. N-103 — the
       canary assertion still reads "nowhere in ... response `record.json`, any other JSON",
       which still collides with §7's non-sensitive `custom.value` in a decision record.
       N-105 — the `e2e_harness.py` seam is still "final BLOCKED-result assembly, before
       result serialization", a description rather than the single location §12 now gives
       for `orca_runtime_harness.py`. N-104 is arguably addressed in passing: §12 now reads
       "after `decision_gate.unresolved_block_reason(...)` HAS CONVERTED the completed
       attempt to terminal `BLOCKED`", which reads as gating on reclassification, though it
       still does not say so as a rule.
Required Action: Optional — none of these change the gate.
```

## Test Review

No tests were written or changed. The phase is design-only; `git status --short` shows only
the pre-existing untracked artifacts and the untracked root-level `e2e_harness.py`, none of
which this iteration touched, and this review modified no production code, test, or
fixture. I reviewed the Testing Strategy as a design artifact and checked its new claims
against the real suites.

**The F-004 additions are real coverage.** The AC1 bullet — "a single valid `NEEDS_INPUT`
block with `open_item: null` publishes exactly one structured request (AC1)" — is written
against the exact record the iteration-2 finding demonstrated, so it would catch the
regression it exists for. The bound-pair bullet and the independent-null bullet are both
present and distinct.

**Where it is silent, and it is the same blind spot for the third time.** Every fixture
that touches identity presupposes agreement on the label: iteration 2's bullet said "the
same `(run, phase, open_item)`", and iteration 3 added "with null labels" — two points on
a five-point space, both of them the agreeing ones. No case varies the Worker's and the
Reviewer's `open_item` against each other, which is the one axis OS-29 leaves entirely
free and the axis the whole identity scheme now turns on. This is the same pattern that
let F-003 survive iteration 1 and F-004 survive iteration 2: new OS-30 fixtures are
authored by one hand and are naturally self-consistent, so the suite stays green exactly
where the repository is permissive.

**Verified as still feasible.** The two checkable claims carried from iteration 2 remain
true. `imported_names()` (test_os29_decision_gate.py:44-57) still does
`names.add(node.module.split(".")[0])` and `names.add(node.module.replace("scripts.", ""))`,
so implementation step 5's AST claim and its forbidden-import positive control hold. The
runtime seam is still one location: `unresolved_block_reason` at
orca_runtime_harness.py:2703 and `log_run_status("BLOCKED", ...)` at :2724, with the
authoritative records in scope.

## Evidence Checked

Authoritative sources:
- Jira OS-30 fetched live this iteration (`getJiraIssue`, cloud
  `2c6ec14b-0c84-47a5-83e1-9243bfb5bf5f`, status 할 일). `## Scope` bullet 1 ("stable
  decision/request identity") and AC1 are the grounds for F-005; the remaining scope
  bullets and all 9 acceptance criteria were re-checked against the iteration-3 delta and
  no previously satisfied criterion was lost by the F-004 correction.
- `artifacts/runs/run_db374a3fd83a/REVIEW_DESIGN_iteration2.md`, F-004 and N-101..N-105
  read in full and checked one by one against the delta; `REVIEW_DESIGN.md` F-001..F-003
  re-checked for regression.
- Phase policy: `orca-worker-reviewer-orchestration/reviews/common.md` and
  `reviews/design.md`.

Repository evidence produced by direct execution in the worktree:
- The five-way `open_item` combination matrix above. All five bound B2/B3 pairs validate
  and all five return `DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope` with both
  ledger keys open. -> F-005.
- The unbound variant (blocking B3 with `verifies: null` alongside an open B2) returns
  None from `unresolved_block_reason` — a producer defect, never reaching the seam.
  -> N-201.
- `grep -n open_item scripts/decision_gate.py scripts/decision_policy.py` — presence
  requirement at decision_gate.py:148 only; no hits at all in decision_policy.py; no
  comparison anywhere. `grep -rn open_item orca-worker-reviewer-orchestration/
  --include=*.md` -> one line, SKILL.md:1090.
- Agent-supplied null-defaulting confirmed at orca_runtime_harness.py:2544 and
  e2e_harness.py:1029, directly beneath the harness-owned `verifies`/`boundary`/`source`/
  `role` stamping block.

Repository evidence confirming the iteration-3 fix (design claims verified TRUE):
- `record_identity_defect` (decision_gate.py:359-362) permits `verifies` only on a Reviewer
  B3; `validate_ledger_record` (:425-431) requires it to carry exactly
  `VERIFIES_FIELDS` (:207); `verification_binding_defect` (:970-983) checks run/phase/
  iteration/worker_record_key. -> §3's "validated `verifies`" premise is real.
- `verification_admission_defect` conjunct 6 (:649-651) plus
  `test_only_one_reviewer_may_verify_one_worker_classification`
  (test_decision_gate.py:750) confirm exactly one B3 may verify one B2. -> §3's
  one-hop determinism claim is TRUE.
- `unresolved_block_reason` terminal shape B (:887-928) pairs head and blocker through
  `verifies.worker_record_key`. -> the fold the design adopts is the repository's own.
- `_ensure_run_artifact_root` (run_logging.py:336-364) still matches §3's run-component
  wording verbatim. -> F-003 unregressed.
- `redacted_preview` occurs only in the Resolution Trace; `redact_text` occurs once, in
  §13, as a prohibition. -> F-001 unregressed.
- `open_items()` (:516-556) still refuses to let an agreeing blocking record or any
  verification resolve anything, so the merge still belongs in OS-30 where §12 puts it.
  -> F-002's premise still correct.

Not reviewed, deliberately: OS-31 resume/continuation, transports, approval UIs and future
refactors are outside the phase contract and were used as grounds for nothing.

## Final Decision

FAIL, on one blocking finding.

F-004 itself was fixed properly. The design stopped rejecting the record shape the last
review demonstrated, the AC1 fixture is written against exactly that record, and the
fallback the Worker chose is better than the one the required action proposed: instead of
abandoning the F-002 merge for null labels, it folds the bound Reviewer onto
`verifies.worker_record_key` — a field OS-29 actually validates, that only a Reviewer B3
may carry, that exactly one record may ever bind to a given Worker key, and that
`unresolved_block_reason` already uses to pair the same two records. I checked each of
those four properties in the source and they all hold. The identity is deterministic,
standard-library-computable, and implementable as written.

What blocks the gate is that the fold is conditioned on the reading record's own
`open_item` instead of being applied first. `open_item` is free text that the Worker
session and the Reviewer session write independently, that OS-29 never compares between
them, and that no Skill template asks either of them to populate. I did not infer the
consequence — I ran all five label combinations of the bound pair through the real
validator and the real terminal classifier. All five are valid ledgers reaching the same
DECISION_BLOCKED terminal; the design assigns them one decision item in two cases and two
in three. In those three the human is asked the same question twice, which is the exact
user-visible defect this gate blocked F-002 on, and it defeats OS-30's first scope bullet,
"stable decision/request identity". Letting it through after blocking F-002 and F-004 on
the same class of evidence would make those gates arbitrary.

The remedy is smaller than the one F-004 required and touches nothing settled: hoist the
verification fold above the tag in §3, one sentence each in §4 and §12, and one fixture
that varies the two labels against each other. Architecture, module boundary, artifact
layout, port neutrality, numeric bounds, schema v1 and the OS-31 exclusion all stand, and
F-001, F-002 and F-003 remain resolved.

Two non-blocking findings are recorded for the Worker's judgement and do not affect this
gate. The quality profile is absent, so no tier-2 attribute was applied, and no generic
best practice, style preference or speculative extensibility concern was used as grounds
for any finding.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: The verdict is fully determined by the live Jira OS-30 text (Scope bullet 1 and
AC1), the approved ANALYSIS/PLAN baseline, the iteration-2 REVIEW_DESIGN findings, the
design phase contract, the profile-absent minimal general gate (G1-G5), and repository
evidence produced by direct execution in the worktree during this review. The single
blocking finding rests on an executed enumeration showing that three of five valid OS-29
label combinations split one question into two requests, not on a judgement call. No
user-owned choice arose: the required correction is reversible, repository-local, and
determined by existing repository contracts.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "The live Jira OS-30 ticket, the approved analysis and plan baselines, the iteration-2 design review findings, the design phase contract and directly executed repository evidence fully determine this review verdict; the single blocking finding rests on a demonstrated enumeration of valid OS-29 record shapes that the design's identity rule splits, and no user-owned choice is open.",
  "iteration": 3,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "design",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T17:05:00+00:00",
  "responsible_phase": "design",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer verdict for design iteration 3 of Jira OS-30 only, verifying that REVIEW_DESIGN_iteration2 F-004 is root-cause fixed with a deterministic implementable identity and no item conflation, and that F-001 through F-003 did not regress, excluding OS-31, transports and future refactors.",
  "sequence": 11,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_DESIGN_iteration3.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": null
}
```
