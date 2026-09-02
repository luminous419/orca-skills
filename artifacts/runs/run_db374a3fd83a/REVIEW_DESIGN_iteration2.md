# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

Run: `run_db374a3fd83a` · Phase: design · Iteration: 2 · Role: reviewer
Target: `artifacts/runs/run_db374a3fd83a/DESIGN.md`
Baseline: ANALYSIS/PLAN PASS · Delta under review: DESIGN corrections + resolution trace

## Summary

All three iteration-1 blocking findings are genuinely addressed, and I verified each fix
against the repository rather than against the Worker's Resolution Trace.

- **F-001 (G4) — RESOLVED at the root.** `redacted_preview` is gone from the response
  record. §6 now states positively that "no preview, excerpt, normalized secret, or other
  response-derived string exists in response JSON", and §13's closing sentence is inverted
  from a licence into a prohibition: previews and excerpts are "forbidden in all of them".
  §13 also names the actual reason (`redact_text` "does not recognize every bare secret"),
  which is exactly the repository fact the finding rested on. The AC9 canary assertion in
  the Testing Strategy now explicitly enumerates response `record.json`, so the test proves
  the property instead of assuming it. The schema and the acceptance test can now both hold.
- **F-002 (G1) — the duplicate-item defect is fixed, but the fix rests on a field this
  repository does not guarantee.** One agreeing Worker B2 + Reviewer B3 pair now yields one
  item (§3, §12), with the extra judgements retained as `source_ledger_keys` evidence and a
  named fixture asserting exactly one request. That part is correct. What is new is that
  `decision_item_id` is now derived *entirely* from `(run_id, phase, open_item)`, and
  `open_item` is a field OS-29 permits to be `null` and all three harness/logging writers
  default to `null`. That is F-004 below.
- **F-003 (G1) — RESOLVED at the root.** §3 now delegates run identity to the real contract:
  "one non-empty path component, neither `.` nor `..`, containing no `/` or platform path
  separator", with "no `run_` prefix or additional character class". I diffed that sentence
  against `_ensure_run_artifact_root` (run_logging.py:336-364) and it is an exact
  restatement of the two conditions that function fails closed on. The right-anchored
  structural split keeps traversal fail-closed. The Testing Strategy names
  `run_e2e_blocking_attribute`, `run_from_scenario`, `run_mdup` and an externally supplied
  Orca-style segment — the ids that previously failed.

All six non-blocking notes N-001..N-006 were also taken up substantively, and I confirmed
the two that made checkable repository claims (N-004's AST walker, N-006's runtime seam).

One blocking finding remains. It is not a re-statement of F-002 and not a preference: it is
a new, empirically demonstrated contradiction introduced *by* the F-002 correction, of the
same class the iteration-1 review blocked F-003 on — the design assumes a stronger OS-29
contract than the repository actually provides, and fail-closes on a legitimate
`NEEDS_INPUT` block, producing no request artifact. That is AC1.

The quality profile is absent, so this judgement uses only explicit requirements (Jira
OS-30, fetched live), the design phase contract, and the minimal general gate G1-G5. No
generic checklist, style preference, or OS-31/transport concern was promoted to blocking.

## Blocking Findings

```text
ID: F-004
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: DESIGN.md §3 ("logical_item_key = canonical(run_id, phase, open_item)";
          "Logical identity is the exact validated `(run_id, phase, open_item)` tuple
          carried by the source records"); §4 RequestItem `open_item` ("non-empty OS-29
          logical question identity"); §12 ("groups records by validated
          `(run_id, phase, open_item)`")
Issue: The F-002 correction moves decision-item identity onto `open_item`, but OS-29
       neither requires nor documents a non-null `open_item` on a blocking record, and
       every writer in this repository defaults it to `null`. A genuine `NEEDS_INPUT`
       block whose record carries `open_item: null` is a valid, open OS-29 item that this
       design fails closed on, publishing no request artifact — which is AC1.
Reason / Evidence: Demonstrated by execution, not by argument. Loading the real policy
       (`decision_policy.load_decision_policy(Path("orca-worker-reviewer-orchestration/
       SKILL.md"))`) and the real blocking fixture
       `scripts/fixtures/decision_gate/valid/worker_needs_input.json` with `open_item`
       set to `None`:
         decision_gate.validate_gate_record(policy, record)  -> ACCEPTED, no exception
         decision_gate.open_items(policy, [entry, record])
             -> {'run_fixture/implementation/1/B2#1'}
       So a `NEEDS_INPUT` record with a null `open_item` is a fully valid gate record AND
       a real open decision item. Nothing in the repository forbids it:
       - `open_item` is in REQUIRED_LEDGER_RECORD_FIELDS (decision_gate.py:148), which
         requires the key to be PRESENT and says nothing about its value. `grep -n
         open_item scripts/decision_policy.py` returns no hits at all — the OS-28 policy
         validator never inspects it. No clause anywhere ties a non-null `open_item` to
         `open_decision_item is True`.
       - Three writers default it to null:
           scripts/orca_runtime_harness.py:2544  record.setdefault("open_item", None)
           scripts/e2e_harness.py:1029           record.setdefault("open_item", None)
           scripts/run_logging.py:2103           "open_item": None   (run-entry record)
         The `setdefault` at orca_runtime_harness.py:2544 is decisive: it sits in the
         block that stamps harness-owned fields onto an AGENT-SUPPLIED fenced record. An
         agent that declares a valid `NEEDS_INPUT` with grounds, reason code and
         `open_decision_item: true` but omits `open_item` gets `None` written into the
         published ledger. Note the deliberate contrast three lines above, where the
         reviewer-driven fix changed `source_binding` FROM a setdefault precisely because
         a defaulted value was unsafe; `open_item` was left as one.
       - The Skill contract never asks for it. `grep -rn open_item
         orca-worker-reviewer-orchestration/ --include=*.md` returns exactly one line,
         SKILL.md:1090, and it is the admissibility constant `no_unresolved_open_item` —
         not an instruction to populate the field. Three of the repository's own VALID
         fixtures carry `"open_item": null` (`run_entry_declaration.json`,
         `worker_assumption_allowed.json`, `worker_clear.json`).
       Under the iteration-2 design that combination is unhandled: §4 requires the
       RequestItem's `open_item` to be a "non-empty OS-29 logical question identity", so
       validation fails; §12 then fail-closes ("Publication failure is logged as a closed
       OS-30 artifact error and the run remains BLOCKED"; "A missing Coordinator request
       declaration is fail-closed and produces no vague fallback question"). The run is
       correctly BLOCKED but AC1's structured request artifact is never produced. This is
       the identical failure shape the iteration-1 review blocked F-003 on — a fail-closed
       rejection of a legitimate OS-29 source — relocated from the run-id grammar to
       `open_item`.
       This is a REGRESSION of the correction, not a pre-existing defect. In iteration 1
       identity was `H(source_ledger_key)`, and `source_ledger_key` is always well-formed
       because `decision_gate.ledger_key()` composes it from fields the gate does validate.
       Iteration 2 made a never-validated, always-defaulted field load-bearing.
       Second-order, and I state its true reachability rather than overstating it: two
       DISTINCT questions in one `(run, phase)` that both carry `open_item: null` are also
       a shape OS-29 accepts —
         open_items(policy, [entry, blk(seq=1), blk(seq=2, B3)])
             -> {'run_fixture/implementation/1/B2#1', 'run_fixture/implementation/1/B3#2'}
       — and §3's merge rule would coalesce them into ONE item, or, where they differ, hit
       §3's "disagreement fails closed" clause and publish nothing. In practice this is
       narrower than it looks, because a multi-item phase does not reclassify as a decision
       block at all: `unresolved_block_reason` returns None for it
       (scripts/test_decision_gate.py:1410
       `test_multiple_unrelated_open_items_stay_a_producer_defect`, and
       scripts/test_orca_runtime_contract.py:8595
       `test_two_open_items_are_not_laundered_into_a_block`). The single-record null case
       above needs no such shape and is sufficient on its own.
Required Action: State the fallback in §3 and §4, inside the design phase. The minimal
       repository-consistent form, which preserves the F-002 fix intact: the logical key is
       `(run_id, phase, open_item)` when `open_item` is a non-empty string, and falls back
       to the record's own `source_ledger_key` when it is null or empty. Merging of later
       agreeing B2/B3 judgements applies ONLY to the non-empty form — a null `open_item`
       carries no question identity, so it must never merge, and per-judgement identity is
       the safe direction there. Say in §4 that `open_item` is nullable on the source
       record and name which field then supplies `RequestItem.open_item` (the design
       already requires `question`, `context` and `what_is_blocked`, so the item is still
       publishable without it). Add two fixtures to the Testing Strategy: a single
       `NEEDS_INPUT` block with `open_item: null` publishes exactly one request; two such
       blocks in one `(run, phase)` do not coalesce. Do not fix this by adding a non-null
       `open_item` validation to OS-29 — the design's own Compatibility rules forbid
       changing `CLOSED_LEDGER_RECORD_FIELDS` or OS-28 transitions, and existing valid
       fixtures carry null.
```

## Non-Blocking Findings

```text
ID: N-101
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
Location: DESIGN.md §7 (decision record `resolves` | "exact `source_ledger_key`");
          §4 (`source_ledger_key` vs `source_ledger_keys`)
Issue: The decision record binds to only the FIRST ledger key. After the F-002 merge, an
       agreeing Reviewer B3 key is retained on the request but is named by no decision, so
       a reader holding only DECISIONS cannot see that it is covered.
Reason / Evidence: `open_items()` keeps BOTH keys open for an agreeing pair — I re-ran it
       above and got `{...B2#1, ...B3#2}`. §4 correctly carries both in the item's
       `source_ledger_keys`, but §7's decision record has a singular `source_ledger_key`
       and `resolves` is "exact `source_ledger_key`". Consumption is OS-31 and explicitly
       out of scope, and the binding is recoverable through `request_id` -> item ->
       `source_ledger_keys`, so this fails in the safe direction rather than falsely
       resolving anything. It is recorded because it is the one place the merge is not
       carried all the way through to the authority artifact.
Required Action: Optional — carry `source_ledger_keys` onto the decision record too, or
       state in §7 that `resolves` names the item's first binding and that the full covered
       set is read from the request item.
```

```text
ID: N-102
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §6 (`raw` closed object, `sensitivity`); §11 (`respond` CLI flags);
          §7 ("For sensitive custom values, `custom.value` is null")
Issue: `raw.sensitivity` gates whether a normalized value is persisted, but nothing states
       how it is derived or what it defaults to.
Reason / Evidence: §1 declares `SENSITIVITY = normal | sensitive` and §6 requires the field
       inside `raw`, but the `respond` CLI in §11 has no sensitivity flag, so it cannot be
       submitter-supplied. It must therefore be derived from the request — presumably
       `custom_decision.sensitive` — and the design never says so. The iteration-1 review
       recommended defaulting to `sensitive` when undeclared; the design took option (a)
       (drop the preview) instead, which is a complete fix for F-001, but leaves this
       derivation unstated.
Required Action: Optional — one sentence in §6 naming the source field and the default.
```

```text
ID: N-103
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md "Testing Strategy — Security and portability" first bullet
          ("nowhere in ... response `record.json`, any other JSON, ...") vs §7
          (`custom.value` "holds only the bounded normalized scalar" for normal values)
Issue: The canary assertion's scope, "any other JSON", collides with the design's own
       deliberate storage of a non-sensitive normalized custom value in
       `decisions/<id>/record.json`.
Reason / Evidence: This is a test-specification precision issue, not a reopening of F-001:
       §13 forbids response bytes and normalized SENSITIVE values in ordinary channels, and
       the decision record is a 0600 authority artifact, not an ordinary channel. But if
       the canary fixture's request declares `custom_decision.allowed=true, sensitive=false`
       and the canary normalizes as CUSTOM, the bytes legitimately appear in the decision
       record and the assertion as written fails. With `allowed=false` the canary is
       AMBIGUOUS, no decision exists, and it passes.
Required Action: Optional — state the fixture's custom envelope, or scope the assertion to
       "every JSON except a decision record whose request declared a non-sensitive custom
       value".
```

```text
ID: N-104
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §12 ("the final `log_run_status` path after
          `decision_gate.unresolved_block_reason(...)` has converted the completed attempt
          to terminal `BLOCKED`")
Issue: The named seam is also reached when `unresolved_block_reason` returns None and the
       terminal classification is a producer DEFECT rather than a decision block. The
       design does not say whether publication happens there.
Reason / Evidence: The seam is unambiguous as a location — I confirmed it at
       scripts/orca_runtime_harness.py:2703 (`unresolved_block_reason`) and :2724
       (`log_run_status("BLOCKED", ...)`), with the authoritative `records` in scope, so
       N-006 is resolved for this harness. But the code is
       `reason = unresolved_block_reason(...) or refusal.reason`, so the BLOCKED branch
       runs either way, and for a multi-item or overstated ledger the reason is
       `DECLARATION_DISAGREES_WITH_LEDGER` (test_decision_gate.py:1410;
       test_orca_runtime_contract.py:8595). §11's "only a valid open B2/B3
       `NEEDS_INPUT`/`CONFLICT` source is eligible" arguably covers it, since those records
       are individually valid; the design just never decides the case.
Required Action: Optional — one clause in §12 saying whether publication is gated on the
       terminal reason matching `DECISION_BLOCKED:*`.
```

```text
ID: N-105
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §12 ("The analogous single seam in `e2e_harness.py` is final
          BLOCKED-result assembly, before result serialization")
Issue: N-006 asked for ONE named location per harness. It is now exact for
       `orca_runtime_harness.py` but still a description rather than a location for
       `e2e_harness.py`, which has several blocked-result construction sites.
Reason / Evidence: `final_status=self.contract.blocked_status` is constructed at
       scripts/e2e_harness.py:966, :1310 and :1331, and `run()` has multiple BLOCKED
       returns (the comment at :1873 is "run() parity: the BLOCKED return precedes").
       "Final BLOCKED-result assembly" does not by itself pick one of them. Lower impact
       than the runtime harness because this harness takes an injected fake port and is
       asserted by fake-port cardinality tests, which would catch a double publication.
Required Action: Optional — name the single line or function in `e2e_harness.py`, as §12
       now does for `orca_runtime_harness.py`.
```

## Test Review

No tests were written or changed: the phase is design-only and `git status --short` shows
only pre-existing untracked artifacts plus the untracked root-level `e2e_harness.py`, none
of which this iteration touched. I reviewed the Testing Strategy as a design artifact and
checked its new claims against the real suites.

**The three additions that discharge the blocking findings are real coverage, not
restatement.** (1) The F-001 canary bullet now names `response record.json` explicitly,
which is what makes it a proof rather than an assumption. (2) The F-002 bullet is written
against the exact repository shape the finding rested on — "a Worker B2 block followed by an
agreeing Reviewer B3 block with the same `(run, phase, open_item)` produces exactly one
decision item and request, retains both source ledger keys, and cannot be answered as two
independent questions" — and it adds the disagreement case. (3) The F-003 bullet names the
concrete previously-failing ids (`run_e2e_blocking_attribute`, `run_from_scenario`,
`run_mdup`) plus an externally supplied Orca-style segment, and pairs them with the
traversal negatives. The N-001 crash-window replay, the N-002 currency triple, the N-003
accepted/omitted `--cancel` mode pair, and the N-005 publisher parity matrix are all
present as named cases.

**Where it is silent.** The strategy has no case for a blocking record with
`open_item: null` (F-004). This is the same blind spot that let F-003 survive iteration 1:
new OS-30 fixtures naturally populate `open_item`, so the suite would be green while the
field the whole identity scheme now rests on goes untested at its permitted null value.

**Verified as feasible.** I re-confirmed the one claim in this iteration that makes a
checkable assertion about existing test machinery. Implementation step 5 states that
parsing `ImportFrom.module` makes the primary `from scripts.run_logging import ...` yield
both `scripts` and `run_logging`. `imported_names()` at scripts/test_os29_decision_gate.py
:44-57 does exactly `names.add(node.module.split(".")[0])` and
`names.add(node.module.replace("scripts.", ""))`, so the claim is literally true, and §1's
snippet was changed to the submodule form to match — N-004 is resolved precisely rather
than nominally. The `from scripts.decision_gate import ...` positive control would indeed
fail the walker, so it is a real control.

## Evidence Checked

Authoritative sources:
- Jira OS-30 fetched live this iteration (`getJiraIssue`, cloud
  `2c6ec14b-0c84-47a5-83e1-9243bfb5bf5f`, status 할 일). All 7 `## Scope` bullets, the 6
  per-question sub-items and all 9 acceptance criteria re-checked against the corrected
  design. Coverage is retained: nothing an AC required was removed by the corrections —
  the only deleted field, `redacted_preview`, was the F-001 defect, and AC5 ("원문 응답과
  정규화된 결정이 함께 보존") still holds through the raw file plus the decision record.
- Prior review `artifacts/runs/run_db374a3fd83a/REVIEW_DESIGN.md`, findings F-001..F-003
  and N-001..N-006 read in full and checked one by one against the delta.
- Phase policy: `orca-worker-reviewer-orchestration/reviews/common.md` and
  `reviews/design.md`.

Repository evidence produced by direct execution in the worktree:
- `validate_gate_record` ACCEPTS a `NEEDS_INPUT`/`open_decision_item: true` record with
  `open_item: None`, and `open_items()` returns it as a real open item. Two such records
  return two open keys. -> F-004.
- `grep -n open_item scripts/decision_policy.py` -> no hits; `open_item` appears in
  REQUIRED_LEDGER_RECORD_FIELDS (decision_gate.py:148) as a presence requirement only.
- Null-defaulting writers: orca_runtime_harness.py:2544, e2e_harness.py:1029,
  run_logging.py:2103. Valid fixtures carrying `"open_item": null`:
  `run_entry_declaration.json`, `worker_assumption_allowed.json`, `worker_clear.json`.
- `grep -rn open_item orca-worker-reviewer-orchestration/ --include=*.md` -> one line,
  SKILL.md:1090, the `no_unresolved_open_item` constant.

Repository evidence confirming the fixes (design claims verified TRUE):
- `_ensure_run_artifact_root` (run_logging.py:336-364) fails closed on exactly an empty
  run_id and one containing `/`, `\` or a bare `.`/`..` — an exact match for §3's new
  wording, with no prefix and no character class. -> F-003 resolved.
- `decision_gate.ledger_key` (292-302) is still per-judgement and its docstring still
  reserves supersession to OS-30; §3's "OS-29 keys identify judgements, not questions" is
  a correct reading of it. -> F-002's premise correctly adopted.
- `open_items()` (516-556) still refuses to let an agreeing blocking record resolve an
  earlier one, so the merge belongs in OS-30 exactly where §12 now puts it.
- `imported_names()` (test_os29_decision_gate.py:44-57) matches implementation step 5's
  stated parse. -> N-004 resolved.
- The runtime seam exists as one location: unresolved_block_reason at
  orca_runtime_harness.py:2703, `log_run_status("BLOCKED", ...)` at :2724, authoritative
  `records` in scope. -> N-006 resolved for this harness (see N-105 for `e2e_harness.py`).
- `redacted_preview` no longer appears in the design except in the Resolution Trace;
  `redact_text` appears once, in §13, as an explicit negation. -> F-001 resolved.
- Multi-item phases are already a producer defect, not a decision block
  (test_decision_gate.py:1410; test_orca_runtime_contract.py:8595) — used to BOUND F-004's
  second-order claim rather than to inflate it.

Not reviewed, deliberately: OS-31 resume/continuation, transports, approval UIs and future
refactors are outside the phase contract and were not evaluated or used as grounds for any
finding. Architecture settled in iteration 1 — module boundary, artifact layout, port
neutrality, numeric bounds, OS-31 exclusion — was not re-litigated. No production code or
artifact was modified by this review.

## Final Decision

FAIL, on one blocking finding.

The corrections are good work and the direction is right. F-001 is fixed at the root, not
patched: the field is gone, the §13 sentence that licensed the leak is inverted, and the
acceptance test now names the file that would have carried the secret. F-003 is fixed at
the root: the design now restates the repository's actual run-root rule verbatim instead of
inventing a narrower one, and names the ids that previously failed. F-002's user-visible
defect — asking a human the same question twice — is fixed, with the correct repository
premise and a fixture that would catch a regression. All six non-blocking notes were taken
up, two of them (N-004, N-006) with claims I could check and did.

What blocks the gate is that the F-002 fix is not root-cause complete. It relocated decision
identity onto `open_item`, and `open_item` is the one field in the OS-29 record that the
gate validates for presence only, that the Skill contract never asks an agent to populate,
and that all three writers in this repository default to `null`. I did not infer this — I
ran the real validator against the real fixture and it accepted a null-`open_item`
`NEEDS_INPUT` block as a genuine open item. For that block the design publishes no request
artifact, which is AC1, and the failure is the same class the iteration-1 review already
blocked F-003 on: a fail-closed rejection of a legitimate OS-29 source. Applying a different
standard to it now would make the previous gate arbitrary.

The remedy is small and does not touch anything settled: one fallback rule in §3, one
nullability sentence in §4, and two fixtures. The architecture, module boundary, artifact
layout, identity scheme, port, numeric bounds and OS-31 exclusion all stand.

Five non-blocking findings are recorded for the Worker's judgement and do not affect this
gate; N-101 is MAJOR in impact but fails in the safe direction and its binding is
recoverable, so it is not blocking. The quality profile is absent, so no tier-2 attribute
was applied, and no generic best practice, style preference or speculative extensibility
concern was used as grounds for any finding.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: The verdict is fully determined by the live Jira OS-30 text, the approved
ANALYSIS/PLAN baseline and the iteration-1 REVIEW_DESIGN findings, the design phase
contract, the profile-absent minimal general gate (G1-G5), and repository evidence produced
by direct execution in the worktree during this review. The single blocking finding rests on
an executed demonstration that OS-29 accepts the state the design assumes cannot occur, not
on a judgement call. No user-owned choice arose: the required correction is reversible,
repository-local, and determined by existing repository contracts.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "The live Jira OS-30 ticket, the approved analysis and plan baselines, the iteration-1 design review findings, the design phase contract and directly executed repository evidence fully determine this review verdict; the single blocking finding rests on a demonstrated repository state that contradicts a design assumption, and no user-owned choice is open.",
  "iteration": 2,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "design",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T12:30:00+00:00",
  "responsible_phase": "design",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer verdict for design iteration 2 of Jira OS-30 only, verifying that REVIEW_DESIGN F-001 through F-003 are root-cause fixed without regression and that N-001 through N-006 were addressed, excluding OS-31, transports and future refactors.",
  "sequence": 9,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_DESIGN_iteration2.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": null
}
```
