# Reviewer Result — IMPLEMENTATION iteration 7 (FA correction round)

IMPLEMENTATION_REVIEW: FAIL

RESULT: FAIL

REVIEW_VERDICT: FAIL

DECISION_GATE_STATE: CLEAR

Reviewer: B3 (Claude Opus, Reviewer B3, fresh session). Verifies: IMPLEMENTATION Worker B2,
`artifacts/runs/run_db374a3fd83a/IMPLEMENTATION.md` (record `run_db374a3fd83a/implementation/7/B2#18`).
Baseline: `DESIGN.md` as corrected and PASSed at design iteration 5.

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "Implementation iteration 7 FAILs the phase gate on three independently reproduced blocking findings, every one of them re-derived by executing and mutating the shipped code rather than by reading IMPLEMENTATION.md. FA-001 and FA-004 are genuinely closed and I confirm them: through the real E2EHarness and OrcaRuntimeHarness seams with a real ArtifactHumanApprovalPort, 2, 3 and 4 open decision items publish 1, 1 and 2 requests covering 2/2, 3/3 and 4/4 source ledger keys; publication failure is durably recorded as compact JSON carrying exception class, message and affected ledger keys; transitive dependent exclusion holds (a chain a<-b<-c plus independent d publishes exactly one request containing a and d); and the exact attempt-1 command that published against the fabricated key run_ghost/implementation/9/B2#7 with exit 0 now fails closed with SOURCE_NOT_OPEN and exit 2, writing no clarifications directory, while a genuine open B2 ledger record still publishes and cross-run, closed-record and reason-code-mismatch variants each fail closed. FA-002 realization is largely real: a published 3-item bundle is answerable item by item by decision_item_id, partial state is rendered per item, exact replay is idempotent, request-level --cancel cancels every item without an item selector, and a supplied selector on --cancel is rejected as CANCEL_REQUEST_INVALID. FA-003 is NOT closed. Rewriting raw_response.txt to 'production', updating the response record's own raw.sha256 to match, and recomputing the content-derived decision id still makes show() serve 'deploy to production' to a human who chose 'deploy to staging' -- the exact attempt-1 harm -- because response_id = H(request_id, decision_item_id, submission_id) does not bind the raw digest and _validate_decision_record compares the raw file only against the response record's own tamperable field, which is a self-referential check rather than the DESIGN section 7 reconciliation. Separately, editing a persisted decision's option.action in place to 'deploy to production AND delete backups' is accepted with no error, because the decision's authority payload (option, custom, scope) is never compared against the re-derived normalized value. Third, an ambiguous free-text answer to ANY item of a bundle hard-fails with CLARIFICATION_INVALID after the response record has already been written, because _reclarify hardcodes request['items'][0] and republishes the bundle member with its peer independent_with list intact: one of the three declared response modes is unusable on a bundle and a bundled item can never be re-clarified, which is the same defect shape FA-002 named. Fourth, the FA-003 decision-record validator has zero mutation-sensitive coverage: replacing _validate_decision_record with a pass-through, or removing only its decision_id content re-derivation, or removing only its closed-field and schema checks, each leaves all 26 focused tests green, because the tampered-decision subtest is satisfied by a pre-existing 'decision lineage fork' check; this is the third tautological-fixture defect in this run after R-001 and T2-001. Fifth, the shipped orca-worker-reviewer-orchestration/SKILL.md respond example still omits --decision-item-id, which DESIGN explicitly requires for every executable respond answer-mode example, and that documented command now fails with SCHEMA_MALFORMED; no documentation file states the request/response v2 generation or the immutable historical v1 policy the design also names. Byte parity, OS-28/OS-29 preservation, historical-artifact preservation, the OS-31 boundary and the untracked root e2e_harness.py are all clean, and every gate command in IMPLEMENTATION.md reproduces (26 focused, 499 combined, 194 packaging, 697 validator checks, parity, git diff --check). No user-owned choice is open: every required action is determined by the run's own approved DESIGN, so the gate state is CLEAR and the correct route is a further correction round, not a question to the user.",
  "iteration": 7,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "implementation",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T13:40:00Z",
  "responsible_phase": "implementation",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer closure verdict for IMPLEMENTATION iteration 7 of Jira OS-30 against the corrected DESIGN iteration 5 baseline. Covers independent re-derivation by execution of FA-001 (multi-item terminal publication through the real harness seams, diagnosable publication failure, transitive dependent exclusion), FA-002 realization (per-item select/answer/cancel by stable decision_item_id and the partial/duplicate/missing/stale contracts), FA-003 (decision and lineage tamper resistance and fail-closed show()), FA-004 (ledger-backed CLI create), schema generation handling, the section 14 mandatory test gate including mutation sensitivity, and scope (source/installed parity, OS-28/OS-29 preservation, no tracked historical artifact modified, no OS-31 expansion, untracked root e2e_harness.py untouched). Excludes fixing any finding, OS-31 resume and transport work, PR creation, merge, and Jira status changes.",
  "sequence": 22,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_IMPLEMENTATION_iteration7.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": {
    "iteration": 7,
    "phase": "implementation",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/implementation/7/B2#18"
  }
}
```

---

## Summary

I treated `IMPLEMENTATION.md` as a set of claims to test, not as evidence. Every statement below was
produced by running or mutating the code in this worktree.

**Two of the three carried-in blocking findings are genuinely closed, and closed well.**

`FA-001` is fixed at the right layer. `publication_batches()` sorts the ready set, computes the
transitive ancestor closure, partitions it into deterministic antichains of at most
`MAX_BUNDLE_ITEMS`, and returns *every* batch; both harness seams iterate all of them. I did not
trust the shipped seam tests for this, because they assert against a `FakePort` and therefore prove
only that the seam calls *something*. I re-ran the case with a **real `ArtifactHumanApprovalPort`**
through both seams and counted the request directories actually written to disk:

```text
n=2 E2EHarness:          requests=1  items_covered=2/2  errors=[]
n=2 OrcaRuntimeHarness:  requests=1  items_covered=2/2  errors=[]
n=3 E2EHarness:          requests=1  items_covered=3/3  errors=[]
n=3 OrcaRuntimeHarness:  requests=1  items_covered=3/3  errors=[]
n=4 E2EHarness:          requests=2  items_covered=4/4  errors=[]
n=4 OrcaRuntimeHarness:  requests=2  items_covered=4/4  errors=[]
```

Publication failure is now diagnosable, and durably so — I induced a real failure *inside*
`port.publish()` and read the row back through `run_logging.read_clarification_publication_errors`:

```text
{"exception":"ClarificationError","ledger_keys":["run_seam/implementation/1/B2#1",
 "run_seam/implementation/1/B2#2"],"message":"dependency: unknown item"}
```

Transitive dependent exclusion holds. With `a <- b <- c` plus an independent `d`, the seam publishes
exactly one request containing `['a', 'd']`; `b` and `c` are withheld, so a dependent question never
shares a bundle with what it depends on, directly or transitively.

`FA-004` is fixed. I re-ran the attempt-1 command verbatim:

```text
$ python3 scripts/clarification_protocol.py create --artifact-base ghost --run-id run_ghost \
    --ledger-key "run_ghost/implementation/9/B2#7" --input ghost/in.json
{"schema_version":1,"status":"ERROR","code":"SOURCE_NOT_OPEN"}
exit=2
# no clarifications/ directory created
```

`--ledger-key` is now `required=True`, and I confirmed the check is not a blanket refusal: against a
real `decision_ledger` record that is B2, `open_decision_item: true`, `NEEDS_INPUT`, with matching
phase/iteration/state/reason, `create` succeeds with exit 0; flipping `open_decision_item` to false,
changing `reason_code`, or pointing at a cross-run key each fails closed with `SOURCE_NOT_OPEN`.

The `FA-002` **realization** is also largely real, and I confirmed it by publishing a genuine 3-item
bundle through the seam and driving every mode over the CLI: per-item `--option-id` answers advance
only their own head, partial state renders correctly per item, exact replay with a fixed
`--responded-at` is idempotent and returns the same IDs, a conflicting duplicate is
`CLARIFICATION_ID_CONFLICT` exit 4, a missing selector is `SCHEMA_MALFORMED` exit 2, a foreign
selector is `ITEM_NOT_IN_REQUEST` exit 2, request-level `--cancel` cancels all three items and is
replay-safe, and `--cancel` with a selector is `CANCEL_REQUEST_INVALID`. That is real work and I am
not disturbing it.

**The gate fails on three things.**

`FA-003` is **not** closed. The decision and lineage validators added this round do catch the two
tampers the new test exercises, and they catch the literal attempt-1 forgery (a fabricated
`decision_ffff…` directory plus a rewritten `next_decision_id` is now rejected with
`decision_id content mismatch`). But the *harm* FA-003 named is still reproducible in a minimal,
single-item, single-response run. `response_id = H(request_id, decision_item_id, submission_id)`
does not bind the raw evidence digest, and `_validate_decision_record` checks the raw file only
against the response record's own `raw.sha256` — a self-referential comparison, not the DESIGN §7
reconciliation. Rewrite both together and the whole tree is internally consistent:

```text
HUMAN CHOSE:             b'staging'
legit head served:       {'action': 'deploy to staging', 'option_id': 'staging'}
AFTER TAMPER -> show() OK, head: decision_97dc914096f34596d7f34e61
SERVED AUTHORITY:        {'action': 'deploy to production', 'option_id': 'production'}
```

Separately and more cheaply, the decision record's own authority payload is never reconciled at all:
editing `option.action` in place to `deploy to production AND delete backups` is accepted with no
error, because `_validate_decision_record` re-derives `decision_id` from the *recomputed* normalized
value and never compares the record's stored `option`, `custom` or `scope` against it.

The bundle path has a second unanswerable mode. An ambiguous free-text answer to **any** item of a
multi-item bundle raises instead of re-clarifying — after the response artifact has already been
committed. `_reclarify` hardcodes `request["items"][0]` (so it re-clarifies the wrong item) and
republishes that member carrying its peers' `independent_with` list, which `_publish_items` then
rejects. This is the FA-002 defect shape one mode over: a human gives a legitimate free-text answer
to a bundled question, gets `CLARIFICATION_INVALID` exit 2, and the item can never reach an
effective head.

And the section 14 test gate is not satisfied for the FA-003 half of the change. Deleting
`_validate_decision_record` entirely leaves all 26 focused tests green.

### On the settled user decision

Bounded bundles are retained. Nothing in this review asks for their removal or for narrowing to one
item per request; R7-003 asks for the *bundle* re-clarification path to work, which is the opposite
direction.

### On the design baseline

I looked for evidence that corrected DESIGN iteration 5 is unimplementable as written and did not
find any. Every blocking finding below is an implementation shortfall against a design clause that
is implementable; three of them quote a clause the implementation simply did not carry out. I am not
raising a blocking finding against the baseline.

---

## Blocking Findings

### R7-001 — FA-003 is not closed: forged raw evidence still serves an authority the human never gave

- **ID:** R7-001
- **Quality Attribute:** decision authority integrity / artifact tamper-resistance
- **Severity:** CRITICAL
- **Blocking:** YES (G1 — explicit approved-DESIGN §7 requirement; G4 — security/authority integrity)
- **Responsible Phase:** implementation
- **Location:** `scripts/clarification_protocol.py:686` (`response_id` derivation in `_ingest_one`),
  `scripts/clarification_protocol.py:788-795` (`_validate_decision_record`, the `raw digest mismatch`
  check), `scripts/clarification_protocol.py:626-628` (`_validate_response_record`, `response_id`
  re-derivation); and the byte-identical installed twin.
- **Issue:** `response_id = _identifier("response", "os30-response-v2", [request_id,
  decision_item_id, submission_id])`. The raw evidence digest is **not** an input to the response's
  content-derived identity. `_validate_decision_record` then does
  `hashlib.sha256(raw_bytes).hexdigest() != response["raw"]["sha256"]` — it compares the raw file
  against a field *inside the same tamperable record*. An attacker who edits both together passes
  every check: the response id still re-derives, the decision id re-derives from the freshly
  recomputed normalization, and nothing else binds the human's actual bytes. DESIGN §7 requires
  "No decision is valid unless its response, request, **raw digest**, item, and source key
  reconcile"; the raw digest does not reconcile against anything immutable.
- **Reason / Evidence:** Minimal reproduction on a single-item request with one response — no
  deletions of other artifacts required beyond swapping the decision directory the response already
  names. Rewrite `raw_response.txt` to `production`, set `raw.sha256` to that file's digest, set
  `decision_id` to `H(response_id, recomputed normalized)`, and write the matching decision record:

  ```text
  HUMAN CHOSE:             b'staging'
  legit head served:       {'action': 'deploy to staging', 'option_id': 'staging'}
  AFTER TAMPER -> show() OK, head: decision_97dc914096f34596d7f34e61
  SERVED AUTHORITY:        {'action': 'deploy to production', 'option_id': 'production'}
  byte-identical after:    True
  ```

  This is the exact attempt-1 harm — `deploy to production` served for a human who chose
  `deploy to staging` — and `IMPLEMENTATION.md` asserts "FA-003 — CLOSED … response/request/item/
  source bindings and raw digest reconcile." That assertion does not hold.
- **Required Action:** Bind the raw evidence to an identity the tamper cannot recompute. Either
  include the raw digest in the response's content-derived `response_id` (a response schema
  generation concern — coordinate with the design's v2 statement), or add an immutable published
  binding of `(response_id, raw sha256)` that `_validate_response_record` and
  `_validate_decision_record` reconcile against, and reject any decision whose response's raw bytes
  do not match that binding. Add a tamper case that rewrites `raw_response.txt` **and**
  `raw.sha256` **and** the decision id together and asserts `show()` fails closed with the tree
  byte-identical.

### R7-002 — A persisted decision's authority payload (`option` / `custom` / `scope`) is never reconciled

- **ID:** R7-002
- **Quality Attribute:** decision authority integrity
- **Severity:** CRITICAL
- **Blocking:** YES (G1 — approved-DESIGN §7 reconciliation requirement; G4 — authority integrity)
- **Responsible Phase:** implementation
- **Location:** `scripts/clarification_protocol.py:783-797` (`_validate_decision_record`); the
  `outcome!=record["kind"]` and `decision_id` re-derivation checks at lines 794-796.
- **Issue:** `_validate_decision_record` recomputes `normalized` from the request and the raw bytes,
  compares only `outcome` against `record["kind"]`, then re-derives `decision_id` from
  `[response_id, normalized]`. Because both sides of that derivation use the **recomputed** value,
  the record's own stored `option`, `custom` and `scope` fields — the fields that *state what the
  human authorized* — are never compared to anything. `resolves` is checked against the item's
  `source_ledger_key`; `option`, `custom` and `scope` are not checked at all.
- **Reason / Evidence:** In-place edit of one field on the effective decision, no other change:

  ```text
  B in-place decision.option.action tamper: NO ERROR
     head=decision_d2806fee817a9e40d9ac62fc
     served={'action': 'deploy to production AND delete backups', 'option_id': 'production'}
     tree byte-identical after rejection: True   (there was no rejection)
  ```

  DESIGN §7 defines `option` as closed `{option_id, action}` and `scope` as "exact request custom
  subject or option action boundary". A decision that states an action the request never offered is
  precisely a forged authority, and FA-003's Required Action named this reconciliation explicitly.
  This is a strictly cheaper attack than R7-001 — it needs no digest recomputation.
- **Required Action:** In `_validate_decision_record`, compare the stored `option`, `custom`
  (including `bounded_by` and `raw_response_sha256`) and `scope` field-by-field against the values
  re-derived from the validated request and raw bytes, and reject on any divergence. Add a tamper
  case per authority-bearing decision field asserting fail-closed with a byte-identical tree.

### R7-003 — An ambiguous answer to any bundled item hard-fails; bundled items can never be re-clarified

- **ID:** R7-003
- **Quality Attribute:** explicit requirement (Jira OS-30 bounded independent bundles; DESIGN §8
  bounded re-clarification)
- **Severity:** CRITICAL
- **Blocking:** YES (G1 — DESIGN §8 "An ambiguous current response affects only its named item";
  G2 — a declared response mode does not work on a bundle)
- **Responsible Phase:** implementation
- **Location:** `scripts/clarification_protocol.py:878-889` (`_reclarify`), specifically
  `item=request["items"][0]` at line 879 and the `_publish_items` call at line 886;
  call site `scripts/clarification_protocol.py:740` (`_ingest_one`, which passes no
  `decision_item_id`).
- **Issue:** Two defects compound. (1) `_reclarify` re-clarifies `request["items"][0]` regardless of
  which item the ambiguous response named, contradicting DESIGN §8 ("affects only its **named**
  item"). (2) It copies that item verbatim — including the bundle's `independent_with` list naming
  its two peers — into a **single-item** revision, which `_publish_items` immediately rejects with
  `bundle: symmetric independence required`. So the ambiguity path raises for every item of every
  multi-item bundle. `--response-file` is one of the three modes the request itself declares in
  `accepted_response_modes`.
- **Reason / Evidence:** A real 3-item bundle published through the E2EHarness seam, answered over
  the shipped CLI with a legitimate free-text reply:

  ```text
  $ python3 scripts/clarification_protocol.py respond --artifact-base b2 --run-id run_seam \
      --request-id request_675b5d325581770b48303aee \
      --decision-item-id item_522931c2388df9bd75647c4c --submission-id amb \
      --actor-id alice --actor-type human --where-recorded desk \
      --responded-at 2026-09-01T08:00:00Z --response-file b2/ans.txt
  {"schema_version":1,"status":"ERROR","code":"CLARIFICATION_INVALID"}
  exit=2
  # and the response record WAS committed before the failure:
  responses/response_eb229901e5dc884f5af3470e
  ```

  Via the library API the same case raises
  `ClarificationError: bundle: symmetric independence required` out of `_publish_items`. The
  operation is therefore also not all-or-nothing: a response artifact is persisted, the item reaches
  no head, retrying the same submission re-enters the same failure, and the run stays `BLOCKED` with
  no path forward except an exact option id. DESIGN's own test matrix requires "Ambiguity at
  revisions 0 and 1 creates revisions 1 and 2" without restricting that to single-item requests, and
  §8 makes the per-item scoping explicit.
- **Required Action:** Pass the responding `decision_item_id` into `_reclarify` and re-clarify that
  item. When a bundle member is re-clarified alone, publish the revision with an
  `independent_with` list consistent with its new membership (empty for a single-item revision) and
  define, per DESIGN §9's "a revision that silently removes an item is `SCHEMA_MALFORMED`", how the
  revision relates to the bundle's other items — either re-publish the full item set at the new
  revision or state the single-item-revision rule and make `_current_request` / `_current_item_ids`
  recognise it. Ensure no response artifact is committed when the operation cannot complete. Add
  tests that answer a 2- and 3-item bundle ambiguously **on a non-first item** and assert
  `RECLARIFICATION_CREATED`, the correct item re-clarified, and the other items unaffected.

### R7-004 — The FA-003 decision-record validator has zero mutation-sensitive test coverage

- **ID:** R7-004
- **Quality Attribute:** mandatory test gate (SKILL section 14) / validation evidence
- **Severity:** MAJOR
- **Blocking:** YES (G5 — missing validation evidence for production code changed this round)
- **Responsible Phase:** implementation
- **Location:** `scripts/test_clarification_protocol.py:155-170`
  (`test_tampered_decision_and_lineage_fail_show_without_mutation`) against
  `scripts/clarification_protocol.py:783-797` (`_validate_decision_record`).
- **Issue:** The test's `decision` subtest rewrites `decision_item_id` to
  `item_000000000000000000000000` on a superseded decision. With the validator removed, that record
  simply stops matching the item filter in `_effective_decision`, and the **pre-existing**
  `decision lineage fork` check raises instead — so `assertRaises(ClarificationError)` is satisfied
  by code that predates this round's fix. The new validator is not what makes the test pass.
- **Reason / Evidence:** Three independent weakenings applied to a copy of the tree, focused suite
  re-run after each:

  ```text
  M3  _validate_decision_record -> pass-through (return dict(raw))     Ran 26 tests  OK
  M3a drop ONLY the decision_id content re-derivation check            Ran 26 tests  OK
  M3b drop ONLY the closed-field / schema / directory-binding checks   Ran 26 tests  OK
  ```

  Diagnostic under M3, confirming the substitute cause:
  `raised by: ClarificationError decision lineage fork`.

  For contrast, the other fixes in this round *are* mutation-sensitive, which is how I know the
  sandbox is sound: collapsing `publication_batches` to one batch → `FAILED (failures=2)`; reducing
  the durable failure detail to a bare class name → `FAILED (errors=1)`; making
  `_validate_lineage_event` a pass-through → `FAILED (failures=1)`; dropping the CLI ledger check →
  `FAILED (failures=1)`; ignoring `decision_item_id` in `ingest` → `FAILED (failures=1)`.

  Findings R-001 and T2-001 in this run were both tautological-fixture defects and my dispatch
  states explicitly that a third must not pass. A test that stays green with the fix reverted is not
  coverage, and it is the reason R7-001 and R7-002 survived a round that reported FA-003 closed.
- **Required Action:** Add decision-record tamper cases that are rejected **by
  `_validate_decision_record` specifically** and prove it — at minimum: a wrong `schema_version`, an
  extra/missing closed field, a `decision_id` that does not re-derive, a `request_id`/`response_id`
  binding that does not resolve, a `source_ledger_key` that does not match the item, and (after
  R7-001/R7-002) a raw-digest and an `option`/`scope` divergence. Demonstrate mutation sensitivity in
  `TEST.md` by weakening the validator and recording the resulting failure count, as
  `REVIEW_TEST_iteration2.md` did for the request validator.

### R7-005 — The shipped `SKILL.md` respond example omits `--decision-item-id`; the documented command now fails

- **ID:** R7-005
- **Quality Attribute:** explicit requirement (approved DESIGN, "Expected Changed Files" §, named
  documentation obligation) / docs-vs-behavior consistency
- **Severity:** MAJOR
- **Blocking:** YES (G1 — a named, explicit requirement of the approved baseline was not carried
  out; G2 — the shipped, documented command does not work)
- **Responsible Phase:** implementation
- **Location:** `orca-worker-reviewer-orchestration/SKILL.md` (OS-30 section, the `respond` example);
  `orca-worker-reviewer-loop/SKILL.md`, `README.md`, `INSTALL.md`, `docs/COMPATIBILITY.md`,
  `docs/ROADMAP.md`, `CHANGELOG.md` (schema-generation text).
- **Issue:** The corrected DESIGN states, verbatim: *"Documentation must update … : every executable
  respond example must include `--decision-item-id ITEM` for answer modes, while `--cancel` remains
  request-wide and omits it; schema text must say new request/response v2 and immutable historical
  v1."* The shipped example is still
  `respond --artifact-base PATH --run-id RUN --request-id ID --submission-id TOKEN … (--option-id ID | --response-file PATH | --cancel)`
  with no `--decision-item-id`. No documentation file mentions the v2 generation or the historical
  v1 policy (`grep` for `schema_version` / `v2` across all five docs returns nothing in the OS-30
  sections).
- **Reason / Evidence:** DESIGN §11 makes `--decision-item-id` mandatory for `--option-id` and
  `--response-file` "even for a one-item v2 request", and the implementation enforces it. A user
  copying the shipped command therefore gets:

  ```text
  {"schema_version":1,"status":"ERROR","code":"SCHEMA_MALFORMED"}
  exit=2
  ```

  This is the same axis-E contradiction `FINAL_REVIEW.md` raised against FA-002 (SKILL.md documenting
  a `respond` invocation that cannot work), now inverted: the CLI was fixed and the doc was not.
- **Required Action:** Update the orchestration `SKILL.md` respond example to include
  `--decision-item-id ITEM` for the answer modes and keep `--cancel` request-wide without it; add
  the request/response v2 generation and immutable-historical-v1 statement to the schema text in
  `README.md`, `INSTALL.md`, `CHANGELOG.md`, `docs/ROADMAP.md`, `docs/COMPATIBILITY.md` and both
  `SKILL.md` files; and extend the `validate_skills.py` OS-30 anchors so the documented command
  cannot drift from the CLI contract again.

---

## Non-Blocking Findings

### R7-N01 — An unknown response `schema_version` reports `CLARIFICATION_INVALID`, not `SCHEMA_UNSUPPORTED`

- **Quality Attribute:** closed error vocabulary. **Severity:** LOW. **Blocking:** NO.
- **Location:** `scripts/clarification_protocol.py:613-619` (`_validate_response_record`), where
  `fields = common if version==2 else common-{"decision_item_id"}` runs *before* the version check.
- **Issue / Evidence:** A response record with `schema_version: 99` is checked against the v1 field
  set, so it fails on closedness first: `ClarificationError: response: closed schema mismatch`
  (`CLARIFICATION_INVALID`) rather than DESIGN §6's "Any unknown version is `SCHEMA_UNSUPPORTED`".
  It fails closed with the tree byte-identical, so no authority is at risk. Note that
  `test_unsupported_and_mixed_response_versions_fail_without_rewrite` computes an `expected`
  variable holding `"SCHEMA_UNSUPPORTED"` / `"SCHEMA_VERSION_MIXED"` and never asserts on it — the
  assertion is `assertRaisesRegex(ClarificationError, "response|generation")`, which is why the
  wrong code is not caught. Carried-in T2-003 is the same class.
- **Required Action:** Check `schema_version` membership before selecting the closed field set, and
  assert the code (`exc.code`) rather than a message regex.

### R7-N02 — N-503 confirmed by execution: cross-item reuse of a `submission_id` is accepted

- **Quality Attribute:** idempotency contract completeness. **Severity:** MEDIUM. **Blocking:** NO.
- **Evidence:** On a 3-item bundle, `--submission-id tok` answered item A (exit 0), then the same
  token answered item B (exit 0, second decision written). DESIGN §9 says "Reuse of a submission ID
  with a different item or content is rejected as `CLARIFICATION_ID_CONFLICT`, with no mutation."
  The *content* half is enforced (same item, different option → `CLARIFICATION_ID_CONFLICT` exit 4);
  the *item* half is not, because `response_id` now includes `decision_item_id` so the two responses
  never collide.
- **Reason this is not blocking:** `REVIEW_DESIGN_iteration5.md` already adjudicated this as N-503,
  Blocking NO, and identified it as an unspecified detection mechanism in the design rather than an
  implementation defect. Every such response is well-formed and correctly item-addressed. I agree
  with that classification and am not re-litigating it; I record the execution confirmation so the
  next round can close it against the design text rather than by guesswork.

### R7-N03 — One malformed Coordinator declaration suppresses every other question in the run

- **Quality Attribute:** publication robustness. **Severity:** MEDIUM. **Blocking:** NO.
- **Evidence:** With four open items where exactly one declaration carries an unknown `depends_on`:
  `one bad declaration out of 4 -> requests published: 0  errors: 1`. Item validation runs across
  all sources inside `publication_batches()` before any batching, and the seam's `try` wraps the
  whole batch loop, so a single bad declaration re-creates FA-001's user-visible symptom (a
  `BLOCKED` run that asked nothing) for the three well-formed questions.
- **Reason this is not blocking:** It fails closed, the run correctly remains `BLOCKED`, and the
  failure is now fully diagnosable (R7's FA-001 fix) — DESIGN §12's "A missing Coordinator request
  declaration is fail-closed" supports whole-set rejection. FA-001's Required Action was about
  well-formed input, which now works.
- **Required Action (optional):** Consider per-batch isolation so valid independent questions still
  reach the human, or state whole-set atomicity explicitly in DESIGN §12.

### R7-N04 — Named error codes and the historical-v1 read path have no unit coverage

- **Quality Attribute:** test completeness. **Severity:** MEDIUM. **Blocking:** NO.
- **Evidence:** `grep` over `scripts/test_clarification_protocol.py` finds assertions for
  `SOURCE_NOT_OPEN` only. No test asserts `ITEM_NOT_IN_REQUEST`, `STALE_ITEM`, `SCHEMA_MALFORMED`
  (missing selector), `CANCEL_REQUEST_INVALID` or `CLARIFICATION_ID_CONFLICT` by code, and no test
  reads a homogeneous historical v1 request/response set. I verified all of these by hand: the
  codes are emitted correctly over the CLI, and a hand-built v1 lineage reads with an effective head
  and with its bytes unchanged. The behavior is right; the regression net is not there.
- **Required Action:** Add code-level assertions for each named error code, and a
  historical-v1-single-item read test asserting byte-identity, as DESIGN's testing strategy lists.

### R7-N05 — Carried-in T2-001 confirmed again: the first assertion in `test_bundle_bound_and_independence` is inert

- **Quality Attribute:** test completeness. **Severity:** LOW. **Blocking:** NO.
- The three-item case raises on cross-run item IDs, not on the bundle bound. The four-item case does
  genuinely reach the `1..3` bound (the length check is first in `_publish_items`), so the bound is
  covered. Agreed non-blocking, unchanged from `FINAL_REVIEW.md` FA-N02.

### R7-N06 — Dead `decision_recorded` branch remains in `_effective_decision`

- **Quality Attribute:** cross-phase consistency (N-507 residue). **Severity:** LOW. **Blocking:** NO.
- N-507 is substantively discharged: nothing emits `decision_recorded` any more, and
  `_validate_lineage_event` rejects it as an unsupported event type. But
  `scripts/clarification_protocol.py:859` still carries an unreachable `if kind=="decision_recorded"`
  branch and its `saw_recorded` bookkeeping. Harmless; remove it so the closed five-event set is
  obvious from the reader.

### R7-N07 — Stray untracked `/e2e_harness.py` confirmed untouched (FA-N01 carried in)

- `mtime 2026-09-01 03:17`, 110,133 bytes — unchanged, and earlier than this run's first artifact.
  This round did not touch it. It must still not be committed: stage the 15 tracked files plus the
  OS-30 source/fixture paths explicitly, never `git add -A`.

---

## Test Review

**What I re-ran, and what reproduces.** Every gate command in `IMPLEMENTATION.md` reproduces:

| Command | Claimed | Observed |
| --- | --- | --- |
| `python -m unittest scripts.test_clarification_protocol` | PASS, 26 tests | **PASS, 26 tests, 0.29s** |
| `… + test_e2e_harness + test_orca_runtime_contract` | PASS, 497 tests | **PASS, 499 tests, 56.7s** |
| `PYTHONPATH=scripts … test_validate_skills test_release_package` | PASS, 194 | **PASS, 194 tests, 21.0s** |
| `python scripts/validate_skills.py` | PASS, 697 checks | **PASS, 697 checks** |
| `cmp scripts/clarification_protocol.py …/tools/…` | PASS | **identical** |
| `cmp scripts/run_logging.py …/tools/run_logging.py` | PASS | **identical** |
| `git diff --check` | PASS | **clean** |

(The 497→499 difference is test-count drift between environments, not a failure; every test passes.)

**New coverage that is real.** Five of the six weakenings I applied are caught:

| Weakening | Suite result |
| --- | --- |
| `publication_batches` collapses to a single batch | FAILED (failures=2) |
| durable failure detail reduced to a bare class name | FAILED (errors=1) |
| `_validate_lineage_event` → pass-through | FAILED (failures=1) |
| CLI `create` drops the ledger verification | FAILED (failures=1) |
| `ingest` ignores `decision_item_id`, always uses `items[0]` | FAILED (failures=1) |
| **`_validate_decision_record` → pass-through** | **OK — not caught (R7-004)** |

So `test_terminal_block_with_two_three_four_items_covers_every_item`,
`test_publication_failure_is_durable_and_reader_exposes_it`,
`test_cli_create_requires_existing_ledger_identity`,
`test_bundle_items_answer_independently_then_request_level_cancel` and the *lineage* half of
`test_tampered_decision_and_lineage_fail_show_without_mutation` are genuine, mutation-sensitive
regressions. That is the majority of this round's new tests, and it is good work.

**Where the suite is weaker than it looks.**

1. The seam tests assert against a `FakePort`, so they prove the seam *calls* something with the
   right shapes but never that the real `ArtifactHumanApprovalPort` accepts the batches. I supplied
   the missing half by re-running n=2/3/4 through both seams with the real port and counting
   request directories on disk; it does hold. A seam test with the real port would close the gap
   permanently and is cheap.
2. The `decision` half of the tamper test is satisfied by a pre-existing check (R7-004).
3. `expected` in `test_unsupported_and_mixed_response_versions_fail_without_rewrite` is computed and
   never asserted (R7-N01).
4. Five named error codes and the historical-v1 read path have no assertions (R7-N04).

**Section 14 gate.** Production code changed; unit tests were added and modified and executed with
`UNIT_TEST_STATUS: PASS`. That much is satisfied. The gate is nonetheless not met for the FA-003
decision-record change, because its tests are not mutation-sensitive to the code they claim to
cover — which is precisely how R7-001 and R7-002 reached this review inside a change that reported
FA-003 closed.

**Scope.** Clean. Source/installed byte parity holds for both shipped tools. `git diff main` is the
same 15 tracked files (+252/−3 including this round's harness edits) plus the untracked OS-30
source/fixture paths. No tracked historical run artifact was modified — `git status` shows
`artifacts/` entirely untracked and unchanged apart from this round's own reports. No OS-31 surface
entered `clarification_protocol.py`: it imports no Orca client, opens no transport, and takes no
resume token, dispatch function or terminal handle. The untracked root-level `e2e_harness.py` was
not touched.

---

## Final Decision

**RESULT: FAIL — REVIEW_VERDICT: FAIL — DECISION_GATE_STATE: CLEAR**

FA-001 and FA-004 are closed and I verified both by execution, not by report. The FA-002 realization
— per-item selection, answering and request-level cancellation by stable `decision_item_id` — is
real for the option and cancel modes, and the user's retained-bundles decision is respected
throughout. That is substantial, correct progress.

The gate fails because FA-003, one of the three findings this round was required to close, is not
closed: the exact attempt-1 harm still reproduces (R7-001), a cheaper in-place tamper of the
decision's authority payload is not detected at all (R7-002), and the validator that was supposed to
prevent both can be deleted without turning the suite red (R7-004). Independently, the bundle
re-clarification path hard-fails for every bundled item after committing a response record (R7-003),
which leaves one of the three declared response modes unusable on exactly the artifact shape OS-30
exists to serve. And the shipped documentation instructs users to run a command that no longer works
(R7-005), against an explicit, named clause of the approved design.

None of the five blocking findings requires a user decision. Every required action is determined by
the corrected DESIGN iteration 5, so this is a correction round, not a `NEEDS_INPUT`. This is
implementation gate attempt 6 of 8; two attempts remain. R7-001, R7-002 and R7-004 are one coherent
work item (finish the decision-record validation and give it mutation-sensitive tests), R7-003 is a
second, and R7-005 is a small documentation fix — a single correction round can reasonably close all
five.

I did not modify any code or artifact other than this review file, and I fixed nothing I found.
