# Reviewer Result — IMPLEMENTATION iteration 8 (R7 correction round)

IMPLEMENTATION_REVIEW: FAIL

RESULT: FAIL

REVIEW_VERDICT: FAIL

DECISION_GATE_STATE: CLEAR

Reviewer: B3 (Claude Opus, Reviewer B3, fresh session). Verifies: IMPLEMENTATION Worker B2,
`artifacts/runs/run_db374a3fd83a/IMPLEMENTATION.md` (record `run_db374a3fd83a/implementation/8/B2#23`).
Baseline: `DESIGN.md` as corrected and PASSed at design iteration 5.

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "Implementation iteration 8 FAILs the phase gate on three blocking findings, every one re-derived by executing or mutating the shipped code rather than by reading IMPLEMENTATION.md. Four of the five carried-in findings are genuinely closed and I confirm them by execution: R7-001's exact forgery (rewrite raw_response.txt to 'production', its raw.sha256, the response's decision_id, and swap the decision directory) now fails show() closed with SCHEMA_MALFORMED 'response raw binding mismatch' and leaves the tree byte-identical; R7-002's in-place edit of a persisted decision's option.action to 'deploy to production AND delete backups' now fails closed with 'decision authority mismatch'; R7-003 is fully closed, with ambiguous free-text on every item position of 2- and 3-item bundles returning RECLARIFICATION_CREATED, revision 1 retaining full membership and symmetric independence, narrowing rationale changed only on the named item and every other item byte-identical at the item-object level, verified through both the library API and the shipped CLI; and R7-005's shipped respond example now carries --decision-item-id for answer modes with --cancel request-wide, and the documented commands execute correctly. FA-001 and FA-004 have not regressed: through the real E2EHarness and OrcaRuntimeHarness seams with a real ArtifactHumanApprovalPort, 2, 3 and 4 open items publish 1, 1 and 2 requests covering 2/2, 3/3 and 4/4 source ledger keys; a chain a<-b<-c plus independent d publishes exactly one request containing a and d; an induced failure inside port.publish() is durably recorded with exception class, message and affected ledger keys; and the fabricated key run_ghost/implementation/9/B2#7 fails closed with SOURCE_NOT_OPEN exit 2 writing no clarifications directory. The FA-002 realization is fully exercised over the CLI on a real ledger-backed 3-item bundle: per-item answer, partial state, idempotent replay, CLARIFICATION_ID_CONFLICT/4, SCHEMA_MALFORMED/2, ITEM_NOT_IN_REQUEST/2, CANCEL_REQUEST_INVALID/2, request-wide cancel and STALE_REQUEST/3 all behave as the corrected design specifies. The gate nonetheless fails on three things. First, R8-002: this round's new mandatory response_bindings/ artifact makes the DESIGN section 6 historical-v1 read path impossible; a genuine homogeneous v1 request/response/decision set fails show() closed with 'response raw binding mismatch', and I isolated the cause by disabling only _validate_response_evidence in a sandbox copy, where the same tree reads an effective head -- so a read path that DESIGN requires to remain effective, and that I verified working at iteration 7, was regressed by this round. Second, R8-001: the mutation-sensitivity gaps R7-004 named by name still survive. The coarse probe is fixed, but dropping only the decision_id content re-derivation, or only the closed-field/schema/version/directory-binding checks, still leaves all 29 focused tests green, and the new binding's three load-bearing sub-checks -- content-addressed binding_id, the exactly-one-match uniqueness rule, and the raw-bytes-versus-binding digest recheck -- are each removable with the suite still green. Third, R8-003: DESIGN implementation step 6 explicitly requires OS-30 semantic anchors in scripts/validate_skills.py, and that file is unmodified against main, while the required request/response v2 plus immutable-historical-v1 schema statement appears in only one of the seven documentation files DESIGN names. I also record, non-blocking, that content-addressed identities in DESIGN section 3 are recomputable by any writer, so an append-only forged supersession still hijacks the effective head; that is a threat-model gap in the baseline rather than an implementation shortfall, and my own iteration-7 required action asked for an anchor the design's unkeyed primitives cannot provide. Bounded bundles are retained and nothing here asks for their removal. Every gate command in IMPLEMENTATION.md reproduces exactly (29 focused, 502 combined, 194 packaging, 697 validator checks, source/installed byte parity, git diff --check clean), and scope is clean: no tracked historical artifact modified, no OS-31 surface, untracked root e2e_harness.py untouched at its original 03:17 mtime. No user-owned choice is open: every required action is determined by the run's own approved DESIGN, so the gate state is CLEAR and the correct route is a further correction round, not a question to the user.",
  "iteration": 8,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "implementation",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T14:05:00Z",
  "responsible_phase": "implementation",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer closure verdict for IMPLEMENTATION iteration 8 of Jira OS-30 against the corrected DESIGN iteration 5 baseline. Covers independent re-derivation by execution of carried-in findings R7-001 through R7-005, non-regression of FA-001 and FA-004, the FA-002 realization contracts, decision and lineage tamper resistance, schema generation handling, the section 14 mandatory test gate including mutation sensitivity, the Coordinator-raised decision-record sequence adjudication, and scope (source/installed parity, OS-28/OS-29 preservation, no tracked historical artifact modified, no OS-31 expansion, untracked root e2e_harness.py untouched). Excludes fixing any finding, OS-31 resume and transport work, PR creation, merge, and Jira status changes.",
  "sequence": 24,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_IMPLEMENTATION_iteration8.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": {
    "iteration": 8,
    "phase": "implementation",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/implementation/8/B2#23"
  }
}
```

---

## Summary

I treated `IMPLEMENTATION.md` as a set of claims to test. Everything below was produced by running
or mutating the code in this worktree, in a sandbox copy where mutation was required. I modified no
production file, no test, and no artifact other than this review.

### Four of the five carried-in findings are genuinely closed

**R7-001 — CLOSED.** The exact attack I used last round now fails closed. On a single-item request
answered `staging`, I rewrote `raw_response.txt` to `production`, set `raw.sha256` and `byte_count`
to match, recomputed `decision_id = H(response_id, recomputed normalized)`, rewrote the response's
`decision_id`, and swapped the decision directory:

```text
HUMAN CHOSE:        b'staging'
legit head served:  ('decision_85cd…', {'action': 'deploy to staging', 'option_id': 'staging'})
AFTER TAMPER:       show() FAILED CLOSED  SCHEMA_MALFORMED  response raw binding mismatch
tree byte-identical after read: True
```

The mechanism is the new content-addressed `(response_id, raw sha256)` binding published under
`clarifications/response_bindings/`, which `_validate_response_evidence`
(`scripts/clarification_protocol.py:638`) reconciles against on every response and decision read.
Attempt-1's original FA-003 attack — a fabricated `decision_ffffffffffffffffffffffff` directory plus
a rewritten lineage `next_decision_id` — also fails closed (`SCHEMA_MALFORMED decision_id content
mismatch`, tree byte-identical), as does appending a second binding for the same response
(`response raw binding mismatch`).

**R7-002 — CLOSED.** The cheaper attack is now rejected. In-place edit of one field on the effective
decision, nothing else changed:

```text
B in-place decision.option.action -> "deploy to production AND delete backups"
show() FAILED CLOSED:  SCHEMA_MALFORMED  decision authority mismatch
tree byte-identical after rejection: True
```

`_validate_decision_record` now reconstructs the expected record via `_decision_record` and compares
`option`, `custom`, `scope`, `resolves`, `actor`, `provenance` and `responded_at` field by field
(`scripts/clarification_protocol.py:836`).

**R7-003 — CLOSED, and closed well.** `_reclarification_items` preflights full membership,
per-item validation and the known DAG before any write, and `_reclarify` re-clarifies the item the
response actually named. I drove **every item position** of 2- and 3-item bundles:

```text
n=2 target_idx=0 -> RECLARIFICATION_CREATED  members_same=True narrowed_on_target=True others_untouched=True indep_ok=True
n=2 target_idx=1 -> RECLARIFICATION_CREATED  members_same=True narrowed_on_target=True others_untouched=True indep_ok=True
n=3 target_idx=0 -> RECLARIFICATION_CREATED  members_same=True narrowed_on_target=True others_untouched=True indep_ok=True
n=3 target_idx=1 -> RECLARIFICATION_CREATED  members_same=True narrowed_on_target=True others_untouched=True indep_ok=True
n=3 target_idx=2 -> RECLARIFICATION_CREATED  members_same=True narrowed_on_target=True others_untouched=True indep_ok=True
```

and confirmed the same over the shipped CLI against a real decision ledger, on the **third** item of
a 3-item bundle: `RECLARIFICATION_CREATED` exit 3, revision 1 carrying all three items with the
narrowing rationale on the correct one, and the superseded revision 0 subsequently returning
`STALE_REQUEST` exit 3.

**R7-005 — the behavioral half is CLOSED.** The shipped orchestration `SKILL.md` respond example now
reads `… --request-id ID --decision-item-id ITEM --submission-id TOKEN … (--option-id ID |
--response-file PATH)` with `--cancel` on a separate request-wide line that correctly omits the
selector, and it is the only executable `respond` example in the repository. I executed both forms
and both work. The documentation obligation is not fully discharged — see R8-003.

### FA-001 and FA-004 have not regressed

Through the **real** `E2EHarness` and `OrcaRuntimeHarness` seams with a **real**
`ArtifactHumanApprovalPort`, counting request directories actually written to disk:

```text
n=2 E2EHarness           requests=1 items_covered=2/2 errors=[]
n=2 OrcaRuntimeHarness   requests=1 items_covered=2/2 errors=[]
n=3 E2EHarness           requests=1 items_covered=3/3 errors=[]
n=3 OrcaRuntimeHarness   requests=1 items_covered=3/3 errors=[]
n=4 E2EHarness           requests=2 items_covered=4/4 errors=[]
n=4 OrcaRuntimeHarness   requests=2 items_covered=4/4 errors=[]
```

Transitive dependent exclusion holds — with `a <- b <- c` plus independent `d`, the seam publishes
exactly one request containing `['choice_1', 'choice_4']`. A failure induced **inside**
`port.publish()` is durably readable through `run_logging.read_clarification_publication_errors`:

```text
{"exception":"ClarificationError","ledger_keys":["run_seam/implementation/1/B2#1",
 "run_seam/implementation/1/B2#2"],"message":"dependency: unknown item"}
```

FA-004: the attempt-1 command, verbatim.

```text
$ python3 scripts/clarification_protocol.py create --artifact-base ghost --run-id run_ghost \
    --ledger-key "run_ghost/implementation/9/B2#7" --input ghost/in.json
{"schema_version":1,"status":"ERROR","code":"SOURCE_NOT_OPEN"}
exit=2
# no clarifications/ directory created
```

`--ledger-key` is `required=True`, and the check is not a blanket refusal: against three real
`decision_ledger` records (with sequence 0 correctly reserved as the OS-29 run-entry declaration),
`create` publishes a 3-item bundle with exit 0.

### The FA-002 realization is fully exercised

On that real ledger-backed 3-item bundle, over the shipped CLI:

| Case | Observed |
| --- | --- |
| Answer item A by `--decision-item-id` | `DECIDED`, exit 0 |
| Partial state after A | `{A: decision_dfba…, B: None, C: None}` |
| Answer item B independently | `DECIDED`, exit 0, only B's head advances |
| Exact replay of A | identical response/decision IDs, exit 0 |
| Same token, different content, same item | `CLARIFICATION_ID_CONFLICT`, exit 4 |
| Answer-mode with no `--decision-item-id` | `SCHEMA_MALFORMED`, exit 2 |
| Selector not in request | `ITEM_NOT_IN_REQUEST`, exit 2 |
| `--cancel` **with** a selector | `CANCEL_REQUEST_INVALID`, exit 2 |
| Request-wide `--cancel` | `CANCELLED`, all three heads cleared, replay-safe |
| Answer to a superseded revision | `STALE_REQUEST`, exit 3 |

### The gate fails on three things

1. **R8-002** — the new binding makes DESIGN §6's historical-v1 read path impossible; a read path I
   verified working at iteration 7 now fails closed.
2. **R8-001** — the specific mutation-sensitivity cases R7-004's Required Action enumerated by name
   still survive, and so do the three sub-checks that make this round's binding load-bearing.
3. **R8-003** — DESIGN implementation step 6's `validate_skills.py` OS-30 anchors were not added at
   all, and the required schema-generation statement reached 1 of the 7 named documentation files.

### On the settled user decision

Bounded bundles are retained. Nothing in this review asks for their removal or for narrowing to one
item per request. R8-001 asks for tests, R8-002 for a version-aware read, R8-003 for anchors and
documentation text; none touches bundle capability.

### On the design baseline

I did not find DESIGN iteration 5 unimplementable, and I raise no blocking finding against the
baseline. I do record two baseline gaps as non-blocking (N-802 threat model, N-801 artifact layout),
including the fact that my own iteration-7 R7-001 Required Action asked for "an identity the tamper
cannot recompute", which DESIGN §3's unkeyed content-addressing cannot provide by either of the two
routes I named. The worker chose the second route I offered and implemented it faithfully; the
residual is the design's, not the worker's.

---

## Blocking Findings

### R8-002 — The new `response_bindings` requirement breaks DESIGN §6's historical-v1 read path

- **ID:** R8-002
- **Quality Attribute:** explicit requirement (DESIGN §6 schema generations) / backward compatibility
- **Severity:** MAJOR
- **Blocking:** YES (G1 — DESIGN §6 "A historical v1 response remains effective on read under shape
  (a)"; G2 — a designed read path does not work; G3 — regression against iteration 7, which I
  verified working)
- **Responsible Phase:** implementation
- **Location:** `scripts/clarification_protocol.py:638-654` (`_validate_response_evidence`, called
  unconditionally from `show()` at line 946 and from `_validate_decision_record` at line 827);
  `scripts/clarification_protocol.py:633-637` (`_response_binding`); and the byte-identical
  installed twin.
- **Issue:** `_validate_response_evidence` requires **exactly one** published
  `response_bindings/binding_*/record.json` naming the response, for **every** response record,
  with no version guard. The `response_bindings` directory is new in this round. Any artifact set
  written before it existed — which by definition is every historical v1 set, the shape DESIGN §6
  explicitly admits — has no binding, so `len(matches) != 1` and the read fails. DESIGN §6 states
  that readers accept "historical request v1 plus response v1" as a valid homogeneous shape and
  that "A historical v1 response remains effective on read only under shape (a)", and that "Schema
  generations are non-destructive". A shape that can never be read is not accepted.
- **Reason / Evidence:** I hand-built a genuine homogeneous v1 set — request v1 with its identity
  derived under the `os30-request-v1` domain, single item, response v1 with no `decision_item_id`
  and its identity under `os30-response-v1`, `raw_response.txt`, and a matching decision record —
  containing no `response_bindings` entry, exactly as a pre-v2 tree would look:

  ```text
  historical v1 set WITHOUT response_bindings (genuine pre-v2 shape)
    show -> SCHEMA_MALFORMED  response raw binding mismatch
    bytes unchanged: True
  ```

  I isolated the cause rather than inferring it. In a sandbox copy of the tree with **only**
  `_validate_response_evidence` disabled — which is precisely the iteration-7 state, since that
  method is the entire R7-001 fix — the same v1 set reads correctly:

  ```text
  with _validate_response_evidence disabled (= iteration-7 state):
    show -> {'item_3f05521d439756c435468b83': 'decision_4666348eda0978748f47c04d'}
    bytes unchanged: True
  restored:
    show -> SCHEMA_MALFORMED  response raw binding mismatch
  ```

  `REVIEW_IMPLEMENTATION_iteration7.md` R7-N04 recorded that "a hand-built v1 lineage reads with an
  effective head and with its bytes unchanged", so this is a regression introduced this round, not a
  pre-existing gap. The same reasoning applies to any v2 tree published by iterations 1-7. The
  failure is fail-**closed** — no false authority is served and no bytes are rewritten — which is
  why this is MAJOR rather than CRITICAL, and there are no OS-30 artifact trees in the repository or
  in any release today, so field impact is currently nil. It is nonetheless a named contract of the
  approved baseline, it is the subject of this dispatch's check 8, and the shipped `SKILL.md` now
  advertises to users that "historical homogeneous v1 single-item artifacts remain immutable and are
  never migrated or rewritten", which reads as a promise that they remain usable.
- **Required Action:** Make the binding requirement generation-aware, consistent with DESIGN §6's
  non-destructive rule: require the binding for response `schema_version` 2 and skip it for a
  validated historical v1 response, or state in DESIGN §2/§6 that a v1 set additionally requires a
  binding and specify how an unmigratable historical set is to be treated (`invalid` versus
  effective) — but do not silently make the admitted shape unreadable. Add a test that reads a
  homogeneous historical v1 single-item set end to end, asserts an effective head, and asserts its
  bytes are unchanged, as DESIGN's testing strategy already lists and as R7-N04 requested.

### R8-001 — R7-004 is only partially closed: the enumerated mutation cases still survive, and the new binding's load-bearing sub-checks have no coverage

- **ID:** R8-001
- **Quality Attribute:** mandatory test gate (SKILL section 14) / validation evidence
- **Severity:** MAJOR
- **Blocking:** YES (G5 — missing validation evidence for production code changed this round)
- **Responsible Phase:** implementation
- **Location:** `scripts/test_clarification_protocol.py:179-204`
  (`test_raw_record_digest_and_decision_rewrite_fails_closed_without_mutation`,
  `test_tampered_decision_authority_payload_fails_closed`) against
  `scripts/clarification_protocol.py:633-654` (`_response_binding`,
  `_validate_response_evidence`) and `scripts/clarification_protocol.py:815-838`
  (`_validate_decision_record`).
- **Issue:** The coarse probe from R7-004 is fixed — replacing `_validate_decision_record` with a
  pass-through now fails the suite. But R7-004's Required Action enumerated the minimum cases:
  *"a wrong `schema_version`, an extra/missing closed field, a `decision_id` that does not re-derive,
  a `request_id`/`response_id` binding that does not resolve, a `source_ledger_key` that does not
  match the item, and (after R7-001/R7-002) a raw-digest and an `option`/`scope` divergence."* Only
  the `option`/`scope` divergence gained a test. The two sub-mutations I named verbatim last round
  (M3a, M3b) still leave the suite green. Separately, `_validate_response_evidence` — this round's
  entire R7-001 fix — is covered only at the "does the method exist" level: its three internal
  checks are each individually removable with all 29 tests passing.
- **Reason / Evidence:** Full mutation matrix, applied one at a time to a sandbox copy of the tree
  (baseline: 29 tests OK), the focused suite re-run after each and the source restored:

  | Mutation | Suite result |
  | --- | --- |
  | M1 `_validate_response_evidence` → no-op | FAILED (failures=1) |
  | M2 decision authority field tuple → empty | FAILED (failures=1) |
  | M3 `_reclarification_items` forces `items[0]` | FAILED (failures=2) |
  | M4 `_validate_decision_record` → pass-through | FAILED (failures=1) |
  | **M5 drop ONLY the `decision_id` content re-derivation** | **OK — not caught** |
  | **M6 drop ONLY the closed-field / schema / version / directory-binding checks** | **OK — not caught** |
  | M7 `_validate_lineage_event` → pass-through | FAILED (failures=1) |
  | M8 `publication_batches` → single batch | FAILED (failures=2) |
  | M9 CLI `create` drops the ledger verification | FAILED (failures=1) |
  | M10 `ingest` ignores `decision_item_id` | FAILED (failures=3) |
  | **M11 `binding_id` no longer content-addressed (drop `digest` from the identity)** | **OK — not caught** |
  | **M12 drop the binding uniqueness rule (`len(matches)!=1` → `not matches`)** | **OK — not caught** |
  | **M13 drop the raw-bytes-versus-binding digest recheck** | **OK — not caught** |

  M1, M2 and M3 are the three mutations `IMPLEMENTATION.md` reports, and all three reproduce exactly
  as claimed — that work is real and I confirm it. M5 and M6 are the two I reported under R7-004 as
  surviving, and they still survive. M11 and M12 matter beyond bookkeeping: under M11 the binding
  record becomes editable **in place** (its directory name no longer depends on the digest), and
  under M12 an attacker can simply *append* a second binding carrying the tampered digest instead of
  having to delete the original. Both weaken the very property R7-001 was raised to obtain, and no
  test notices. Findings R-001, T2-001 and R7-004 in this run were all fixture-strength defects; the
  standard my dispatch sets is that a test which passes with the fix reverted is not coverage.
- **Required Action:** Add tests that fail when each of M5, M6, M11, M12 and M13 is applied. Concretely:
  a decision record with a wrong `schema_version`; one with an extra and one with a missing closed
  field; one whose `decision_id` does not re-derive from `[response_id, normalized]` while every
  other field is consistent; one whose `source_ledger_key` does not match its item; a response whose
  binding directory name does not equal `H(response_id, raw_sha256)`; a response with two published
  bindings; and a response whose `raw_response.txt` bytes disagree with its otherwise-consistent
  binding. Record the mutation matrix and resulting failure counts in `TEST.md`, as
  `REVIEW_TEST_iteration2.md` did for the request validator.

### R8-003 — DESIGN step 6's `validate_skills.py` OS-30 anchors were never added, and the required schema-generation text reached 1 of 7 named files

- **ID:** R8-003
- **Quality Attribute:** explicit requirement (approved DESIGN "Expected Changed Files /
  Implementation Steps" 6 and 8, and the named documentation consequence) / drift protection
- **Severity:** MAJOR
- **Blocking:** YES (G1 — two explicit, named requirements of the approved baseline were not carried
  out, in a round that reports R7-005 CLOSED)
- **Responsible Phase:** implementation
- **Location:** `scripts/validate_skills.py` (unmodified against `main`);
  `scripts/test_validate_skills.py` (+1 line against `main`); `README.md`, `INSTALL.md`,
  `CHANGELOG.md`, `docs/ROADMAP.md`, `docs/COMPATIBILITY.md`, `orca-worker-reviewer-loop/SKILL.md`.
- **Issue:** DESIGN implementation step 6 states: *"Update both `SKILL.md` files. Add explicit shared
  OS-30 semantic anchors to `scripts/validate_skills.py`, with deletion/drift/false-feature-parity
  cases in `scripts/test_validate_skills.py` (REVIEW_PLAN N-001)."* No anchors exist. The DESIGN
  documentation clause states: *"Documentation must update `README.md`, `INSTALL.md`, `CHANGELOG.md`,
  `docs/ROADMAP.md`, `docs/COMPATIBILITY.md`, `orca-worker-reviewer-orchestration/SKILL.md`, and
  `orca-worker-reviewer-loop/SKILL.md`: … schema text must say new request/response v2 and immutable
  historical v1."* Only `orca-worker-reviewer-orchestration/SKILL.md` says it.
- **Reason / Evidence:**

  ```text
  $ git diff --stat main -- scripts/validate_skills.py
  (no output — the file is unmodified)

  $ grep -ni "clarif\|OS-30\|os30\|decision-item" scripts/validate_skills.py
  (no matches)

  $ git diff --stat main -- scripts/test_validate_skills.py
   scripts/test_validate_skills.py | 1 +
  ```

  `scripts/validate_skills.py` passes 697 checks, exactly the same count as `main`, so the
  green validator result in `IMPLEMENTATION.md` proves nothing about OS-30. The absence of anchors
  is the direct reason R7-005 was possible in the first place: the shipped `respond` example drifted
  away from the CLI contract for a whole round with the validator green throughout, and nothing
  today prevents the same drift recurring. On the documentation side,
  `docs/COMPATIBILITY.md` is the file whose stated purpose is exactly this policy and its OS-30
  section describes legacy behavior without mentioning v1/v2 generations at all — and per R8-002 the
  v1 policy it would have documented does not currently hold. `IMPLEMENTATION.md` reports R7-005
  CLOSED with the justification that no other Markdown contains an executable `respond` example;
  that is true and I verified it, but it addresses only the first of the two clauses.
- **Required Action:** Add the OS-30 semantic anchors to `scripts/validate_skills.py` — at minimum
  that every executable `respond` answer-mode example carries `--decision-item-id`, that the
  `--cancel` example does not, and that the request/response v2 plus immutable-historical-v1
  statement is present — with the deletion, drift and false-feature-parity cases DESIGN step 6 names
  in `scripts/test_validate_skills.py`. Add the schema-generation statement to the remaining six
  named documentation files, in a form consistent with whatever R8-002 settles for the v1 read path.

---

## Non-Blocking Findings

### N-801 — `response_bindings/` is an artifact type DESIGN §2's "exact artifact layout" does not contain

- **Quality Attribute:** design/implementation consistency. **Severity:** MEDIUM. **Blocking:** NO.
- **Location:** `scripts/clarification_protocol.py:633-654`; `DESIGN.md` §2 and §3.
- **Issue / Evidence:** DESIGN §2 enumerates an *exact* layout — `requests/`, `responses/`,
  `decisions/`, `lineage/`, `.staging/` — and §3 enumerates the exact identity formulas, none of
  which is `binding_id`. The implementation publishes a sixth directory with a seventh identity
  domain (`os30-response-raw-binding-v1`). I am **not** ruling this blocking: my own iteration-7
  R7-001 Required Action offered "add an immutable published binding of `(response_id, raw sha256)`"
  as one of two acceptable routes, and the alternative route (adding the raw digest to `response_id`)
  would have deviated from §3's stated `response_id` formula instead. The worker chose the route I
  named and said so explicitly. But the deviation is undocumented in the baseline, and R8-002 is its
  direct consequence.
- **Required Action:** Record the `response_bindings/` directory and the `binding_id` formula in
  DESIGN §2 and §3, together with the generation rule R8-002 requires.

### N-802 — Content-addressed identities are recomputable, so an append-only forged supersession still hijacks the effective head

- **Quality Attribute:** decision authority integrity (baseline threat model). **Severity:** HIGH.
  **Blocking:** NO — see the reasoning below, which I have written out so the Coordinator can overrule me.
- **Location:** `scripts/clarification_protocol.py:903` (`_effective_decision`, the
  `later=[rec for rec in decisions if rec["normalized_at"] > last_event_at]` fallback);
  `DESIGN.md` §3 (identity formulas) and §9 (head derivation, "readers reject … forks, more than one
  current head").
- **Issue / Evidence:** Every OS-30 identity is an unkeyed SHA-256 over a public domain string and
  public inputs, so any principal who can write into the artifact tree can recompute all of them.
  Two consequences reproduce on the current code.

  *Append-only forged supersession.* Adding a well-formed second response, its binding and a decision
  — deleting and editing nothing — makes that decision the head, because no `decision_superseded`
  event is required for the `later` fallback to fire:

  ```text
  before:  ('decision_85cd…', {'action': 'deploy to staging',    'option_id': 'staging'})
  after:   ('decision_2b5a…', {'action': 'deploy to production', 'option_id': 'production'})
  tree byte-identical (nothing deleted or edited): True
  ```

  The writer never produces this shape: a legitimate changed answer emits `decision_superseded`
  (2 decisions, 1 event), and a legitimate cancel-then-redecide emits `decision_cancelled`
  (2 decisions, 1 event) — I confirmed both. The forged case has 2 decisions and **zero** events.

  *Full rewrite.* Rewriting `raw_response.txt`, the response record, and then deleting and
  re-creating both the binding and the decision directory under their recomputed names also serves
  `deploy to production` from a `staging` answer, with `show()` returning success.

- **Why this is not blocking.** First, no unkeyed content-addressing scheme can resist a writer who
  recomputes every identity; the alternative route my own R7-001 Required Action named (folding the
  raw digest into `response_id`) falls to exactly the same rewrite, so "an identity the tamper cannot
  recompute" is not obtainable from DESIGN §3's primitives and asking for it again would be asking
  for something the baseline cannot supply. Second, the append-only variant is not a validation
  omission but an under-specification: §9 names forks among what readers reject, yet the `later`
  fallback is load-bearing for the legitimate cancel-then-redecide path, and DESIGN does not say how
  a post-cancellation decision is meant to be linked. Closing it requires a design decision about
  head derivation that the implementation phase cannot make unilaterally, and the artifacts a forger
  produces are otherwise indistinguishable from a legitimate second answer bearing its own actor and
  provenance evidence. I am recording it in full rather than blocking on it. If the design owner
  reads §9's fork clause as covering unlinked decisions, this becomes blocking next round.
- **Required Action (design-owned):** State the threat model in DESIGN §3 — tamper-evidence against
  partial and in-place edits, not unforgeability against an arbitrary writer — and specify in §9
  whether a non-first decision for an item must be reachable from a `decision_superseded` event or
  must follow a `decision_cancelled` head reset, so that an unlinked second decision can be rejected
  as the fork §9 already names.

### N-803 — R7-N01 carried unchanged: an unknown response `schema_version` still reports `CLARIFICATION_INVALID`

- **Quality Attribute:** closed error vocabulary. **Severity:** LOW. **Blocking:** NO.
- **Location:** `scripts/clarification_protocol.py:615` and `:619`;
  `scripts/test_clarification_protocol.py:242-250`.
- **Evidence:** `fields = common if version==2 else common-{"decision_item_id"}` still runs before
  the version check, so a response with `schema_version: 99` fails on closedness first. Measured
  across all four record types:

  ```text
  response schema_version=99  -> CLARIFICATION_INVALID  response: closed schema mismatch   (DESIGN §6 says SCHEMA_UNSUPPORTED)
  request  schema_version=99  -> SCHEMA_UNSUPPORTED     request: unsupported schema
  decision schema_version=99  -> SCHEMA_UNSUPPORTED     decision version
  lineage  schema_version=99  -> SCHEMA_UNSUPPORTED     lineage version
  v1 response against v2 req  -> SCHEMA_VERSION_MIXED   request/response generation
  ```

  All fail closed with the tree byte-identical, so no authority is at risk. The response case is the
  only outlier. `test_unsupported_and_mixed_response_versions_fail_without_rewrite` still computes
  an `expected` variable holding `"SCHEMA_UNSUPPORTED"`/`"SCHEMA_VERSION_MIXED"` and never asserts
  on it — the assertion remains `assertRaisesRegex(ClarificationError, "response|generation")`.
- **Required Action:** Check `schema_version` membership before selecting the closed field set, and
  assert `exc.code` rather than a message regex.

### N-804 — R7-N02 / N-503 carried: cross-item reuse of a `submission_id` is still accepted

- **Quality Attribute:** idempotency contract completeness. **Severity:** MEDIUM. **Blocking:** NO.
- Unchanged from last round and already adjudicated as N-503 (Blocking NO) in
  `REVIEW_DESIGN_iteration5.md` as a design-level unspecified detection mechanism. The *content*
  half of DESIGN §9's duplicate rule is enforced (`CLARIFICATION_ID_CONFLICT` exit 4, confirmed
  again this round); the *item* half is not, because `response_id` includes `decision_item_id` so
  two same-token responses to different items never collide. I am not re-litigating it.

### N-805 — R7-N03 carried: one malformed Coordinator declaration still suppresses every other question

- **Quality Attribute:** publication robustness. **Severity:** MEDIUM. **Blocking:** NO.
- Item validation still runs across all sources inside `publication_batches()` before any batching,
  and the seam's `try` still wraps the whole batch loop, so one bad declaration among four yields
  0 requests and 1 durable error. It fails closed, the run correctly remains `BLOCKED`, and the
  failure is fully diagnosable. Unchanged reasoning from R7-N03.

### N-806 — R7-N04 partially carried: named error codes are correct but still have no unit assertions

- **Quality Attribute:** test completeness. **Severity:** MEDIUM. **Blocking:** NO.
- `grep` over `scripts/test_clarification_protocol.py` still finds a code-level assertion only for
  `SOURCE_NOT_OPEN`. `ITEM_NOT_IN_REQUEST`, `STALE_ITEM`, `SCHEMA_MALFORMED` (missing selector),
  `CANCEL_REQUEST_INVALID` and `CLARIFICATION_ID_CONFLICT` have none. I verified every one of them
  by hand over the CLI this round (see the FA-002 table in the Summary) — the behavior is right, the
  regression net is not there. The historical-v1 read test named in R7-N04 is now required by
  R8-002 instead.

### N-807 — R7-N06 carried: the dead `decision_recorded` branch remains

- **Quality Attribute:** cross-phase consistency (N-507 residue). **Severity:** LOW. **Blocking:** NO.
- `scripts/clarification_protocol.py:890` still carries an unreachable
  `if kind=="decision_recorded"` branch plus its `saw_recorded` bookkeeping at lines 881, 891, 896
  and 900, although `_validate_lineage_event` rejects that event type as unsupported. Harmless;
  removing it would make the closed five-event set obvious to a reader.

### N-808 — R7-N07 confirmed: the stray untracked `/e2e_harness.py` was not touched

- `mtime 2026-09-01 03:17`, 110,133 bytes — unchanged, and earlier than this run's first artifact.
  It must still not be committed: stage the 15 tracked files plus the OS-30 source/fixture paths
  explicitly, never `git add -A`.

### N-809 — Seam tests still assert against a `FakePort` (verified sound, gap remains)

- **Quality Attribute:** test completeness. **Severity:** LOW. **Blocking:** NO.
- `HarnessClarificationSeamTests` still uses `FakePort`, so the suite proves the seams *call*
  something with the right shapes but never that the real `ArtifactHumanApprovalPort` accepts the
  batches. I supplied the missing half by hand for the third round running (the n=2/3/4 table above)
  and it holds. A seam subtest parameterised over the real port would close this permanently and is
  cheap. Also note that `test_publication_failure_is_durable_and_reader_exposes_it` asserts
  `"ledger_keys" in detail` but not that it is non-empty; in that fixture it is `[]`.

### Verified and explicitly NOT a defect

While tracing FA-001 I found that `_ledger_parts` rejects `…/B2#0` (`#[1-9][0-9]*`), so a
sequence-0 record can never become a clarification source. I confirmed this is correct rather than a
bug: `scripts/decision_gate.py:692-706` reserves sequence 0 for the run-entry declaration
(`boundary == "B1"`, `source == RUN_ENTRY_SOURCE`), which is never an open decision item. I record
it so a later reviewer does not spend the same time on it.

---

## Test Review

**Every gate command in `IMPLEMENTATION.md` reproduces.**

| Command | Claimed | Observed |
| --- | --- | --- |
| `python3 -m unittest scripts.test_clarification_protocol` | PASS, 29 tests, 0.346s | **PASS, 29 tests, 0.426s** |
| `… + test_e2e_harness + test_orca_runtime_contract` | PASS, 502 tests, 58.696s | **PASS, 502 tests, 58.253s** |
| `PYTHONPATH=scripts … test_validate_skills test_release_package` | PASS, 194 tests | **PASS, 194 tests, 22.7s** |
| `python3 scripts/validate_skills.py` | PASS, 697 checks | **PASS, 697 checks** |
| `python3 -m py_compile …` | PASS | **PASS** |
| `cmp scripts/clarification_protocol.py …/tools/…` | PASS | **identical** |
| `cmp scripts/run_logging.py …/tools/run_logging.py` | PASS | **identical** |
| `git diff --check` | PASS | **clean** |

**The three claimed mutation results are real.** M1, M2 and M3 in the R8-001 table are exactly the
three weakenings `IMPLEMENTATION.md` reports, and each produces the reported failure. I also
confirmed that last round's five genuine regressions are still genuine (M7-M10 plus M4), so the
suite has not been weakened anywhere I measured. The R7-004 headline probe — `_validate_decision_record`
as a pass-through — is now caught.

**Where the suite is still weaker than it looks.**

1. Five of thirteen mutations survive, including the two R7-004 named by name and the three internal
   checks of this round's binding (R8-001).
2. No test exercises the historical-v1 read path, which is how R8-002 reached this review inside a
   round that changed the response read path.
3. Five named error codes still have no code-level assertion (N-806), and `expected` in the version
   test is still computed and unasserted (N-803).
4. The seam tests still use a `FakePort` (N-809).

**New coverage that is genuinely good.** `test_non_first_bundle_item_ambiguous_reclarifies_same_complete_bundle`
is a well-built test: it subtests 2- and 3-item bundles, targets `items[-1]`, asserts full membership
of the revision, asserts the narrowing rationale on the target, and asserts every other item is
equal object-for-object. `test_tampered_decision_authority_payload_fails_closed` is minimal and
mutation-sensitive. `test_raw_record_digest_and_decision_rewrite_fails_closed_without_mutation`
reproduces the full R7-001 forgery and asserts byte-identity afterwards. That is the majority of
this round's new work and it is sound.

**Section 14 gate.** Production code changed; unit tests were added and modified and executed with
`UNIT_TEST_STATUS: PASS`, which I reproduce. The gate is nonetheless not met, because the tests for
this round's binding are not sensitive to the sub-checks that make it work (R8-001).

**Decision-record sequence adjudication.** I assessed the worker's conclusion and **concur**. Across
the 23 fenced records in this run, sequence 14 is genuinely duplicated
(`REVIEW_IMPLEMENTATION_iteration5.md` reviewer and `DESIGN.md` worker), sequence 17 is absent, and
sequence 18 appears once among the surviving records. There is no `decision_ledger/` directory under
`artifacts/runs/run_db374a3fd83a/` — the only one in the repository is `artifacts/runs/run_os29/` —
so the worker's statement that the run-scoped ledger the contract names does not exist is verifiable,
and its explanation (in-place worker-report replacement destroys the allocation history, producing
both the collision and the gap) matches the evidence. The worker correctly declined to fabricate the
missing ledger. Its own record uses sequence 23, which is greater than the prior maximum 22 and
collides with nothing; this review uses 24 on the same basis. Note that these Markdown-embedded
records are not an OS-29 ledger by OS-29's own rules in any case — that contract requires a
sequence-0 run-entry declaration and gapless `0..n-1` sequences, and this set starts at 2.

**Scope.** Clean. Source and installed `clarification_protocol.py` and `run_logging.py` are
byte-identical. `git diff --stat main` is the same 15 tracked files (+253/−3) plus the untracked
OS-30 source and fixture paths. No tracked historical run artifact was modified — `git status` shows
`artifacts/` entirely untracked. No OS-31 surface entered `clarification_protocol.py`: it imports no
Orca client, opens no transport, and takes no resume token, dispatch function or terminal handle.
OS-28/OS-29 behavior is preserved — the 502-test combined run and the 194-test packaging run both
pass, and `scripts/decision_gate.py` is untouched. The untracked root-level `e2e_harness.py` was not
touched.

---

## Final Decision

**RESULT: FAIL — REVIEW_VERDICT: FAIL — DECISION_GATE_STATE: CLEAR**

This is a substantially better round than iteration 7. Four of the five findings I raised are closed,
and three of them are closed properly, with mechanisms rather than assertions: the content-addressed
raw-evidence binding, the field-by-field authority reconciliation, and the preflighted
bundle-preserving targeted re-clarification are all real, and I verified each by the same execution
and mutation I used to raise the original finding. FA-001, FA-002 and FA-004 are intact.

The gate fails on three findings, all MAJOR and all narrow:

| ID | Severity | Responsible phase | Gate |
| --- | --- | --- | --- |
| R8-002 | MAJOR | implementation | G1, G2, G3 |
| R8-001 | MAJOR | implementation | G5 |
| R8-003 | MAJOR | implementation | G1 |

R8-002 is the one that matters most, because it is a regression this round introduced: the fix for
R7-001 made a read path the approved DESIGN requires — and that I measured working one round ago —
fail closed for every artifact set that predates the new binding. It fails safe rather than open, and
no such artifact set exists today, which is why it is MAJOR and not CRITICAL, but DESIGN §6 states
the contract plainly and a generation guard is a small change. R8-001 and R8-003 are both cases where
a required action was reported CLOSED while the specific, enumerated part of it was not carried out;
neither is a matter of taste, and both have short, mechanical required actions.

I considered and rejected two candidate blocking findings. The `response_bindings` directory's
absence from DESIGN §2 (N-801) is a route I myself authorised last round, so blocking on it would be
unfair. The append-only forged supersession (N-802) reproduces the FA-003 harm and I have documented
it in full with its evidence, but closing it requires a head-derivation decision that DESIGN §9 does
not currently make, and the alternative fix my own R7-001 Required Action named would not have
prevented it either; that is a baseline gap to route to the design owner, not an implementation
shortfall to spend an iteration on. I state this explicitly so the Coordinator can overrule me if it
reads §9's fork clause more strictly than I have.

The decision gate is **CLEAR**. No user-owned choice is open: every required action above is
determined by the run's own approved DESIGN, and the settled user decision on bounded bundles is
untouched by all three. The correct route is one further correction round, not a question to the
user. This is implementation gate attempt 6 of 8, leaving two attempts; the remaining work is a
version guard on one method, roughly seven focused tests, one validator anchor set, and six
documentation sentences.
