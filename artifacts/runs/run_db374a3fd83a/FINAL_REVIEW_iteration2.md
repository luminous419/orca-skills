# FINAL ADVERSARIAL REVIEW — Jira OS-30 Structured Human Clarification and Decision Protocol

RUN: run_db374a3fd83a
REVIEWER: claude-opus (fresh session, Final Adversarial Review iteration 2 of max 5)
DELTA REVIEWED: working tree vs `main`. The branch has ZERO commits ahead of `main`, so `git diff main..HEAD` is empty and was not used. Reviewed: `git diff main --stat` = 16 tracked files, +351/-13, plus the untracked new sources `scripts/clarification_protocol.py`, `scripts/test_clarification_protocol.py`, `orca-worker-reviewer-orchestration/tools/clarification_protocol.py`, and `scripts/fixtures/clarification_protocol/**`.

RESULT: PASS

REVIEW_VERDICT: PASS WITH NOTES

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {
    "attempt1_closure_verification": "FA-001 re-run through BOTH real harness seams with the REAL ArtifactHumanApprovalPort at n=1,2,3,4,5,7 open items: ceil(n/3) requests published, every ledger key covered, zero swallowed errors. FA-002: 2-item bundle answered per item with divergent actions and then cancelled per item to status 'cancelled'. FA-003: three forgery vectors (in-place option mutation, unlinked appended production decision, appended decision plus recomputed decision_superseded event) all rejected with named codes. FA-004: create against run_ghost/implementation/9/B2#7 -> SOURCE_NOT_OPEN, exit 2, zero artifacts, identical in the installed twin.",
    "acceptance": "AC1-AC9 independently re-derived by executing the documented installed CLI end to end: request creation from a real open ledger record, non-recommended option accepted, no decision without an explicit response, ambiguous free text -> RECLARIFICATION_CREATED then AMBIGUITY_LIMIT_REACHED at revision 2, changed answer on the current revision -> decision_superseded lineage, sensitive custom value redacted in the decision with exactly one 0600 raw copy and no leak to stdout or logs.",
    "mutation": "16 targeted guard mutations against the 44-test focused suite: 10 killed, 6 survived. Every authority-critical re-derivation (request_id, response_id, decision_id, event_id, raw-binding uniqueness, SOURCE_NOT_OPEN, MAX_BUNDLE_ITEMS, default_applicable, publication_batches batch completeness) was killed.",
    "security": "Symlink and FIFO response files refused with CLARIFICATION_SECURITY_FAILURE (exit 4); run_id and request_id path traversal refused (exit 2); no secrets in the new sources or fixtures; no out-of-scope tracked file touched.",
    "validation": "Every claimed figure reproduced independently: 44 focused tests OK; 1706 discovery tests OK with 6 skips (331s); validate_skills.py PASSED 714 checks; verify_package.py PASSED 195 source files; build_release.py plus archive verification PASSED; scripts/clarification_protocol.py and orca-worker-reviewer-orchestration/tools/clarification_protocol.py byte-identical (sha256 2dc472e9...), same for run_logging.py (sha256 d45e4038...); compileall clean; git diff --check clean."
  },
  "grounds": "Final Adversarial Review of Jira OS-30 PASSES WITH NOTES. All four attempt-1 blocking findings are independently verified closed by execution, not accepted on report. FA-001 was re-run with the REAL ArtifactHumanApprovalPort through both the E2EHarness and OrcaRuntimeHarness seams rather than the FakePort the shipped tests use, at 1, 2, 3, 4, 5 and 7 open decision items: publication_batches forms ceil(n/3) deterministic antichain bundles, every ledger key is published exactly once, and clarification_errors is empty in all twelve runs. FA-002 is closed: a 2-item bundle is answerable per item through decision_item_id, the two items receive different actions with no cross-item authority transfer, and whole-request cancel drives both items to status cancelled with null heads; per-item cancellation is refused with CANCEL_REQUEST_INVALID, which matches DESIGN section 11's explicit 'Individual-item cancellation is intentionally not exposed'. FA-003 is closed: in-place option mutation fails 'decision authority mismatch', an unlinked appended production decision fails 'decision_id content mismatch', and an appended decision with a fully recomputed decision_superseded lineage event also fails 'decision_id content mismatch', because _validate_decision_record re-normalizes from the persisted raw response bytes. The only surviving attack rewrites the published immutable raw_response.txt bytes and recomputes the response digest, the raw binding and the decision identity; that is a rewrite rather than an unlinked append and is precisely the bound the approved DESIGN declares (structural integrity, not cryptographic authenticity against a writer who can recompute every identity), so it is recorded as a boundary confirmation and NOT as a defect. FA-004 is closed: --ledger-key is required by argparse, read_decision_ledger is consulted read-only, the fabricated key run_ghost/implementation/9/B2#7 yields SOURCE_NOT_OPEN with exit 2 and zero artifacts written in both the repository and installed copies, a record with open_decision_item false is refused the same way, and a genuinely open B2 NEEDS_INPUT record is accepted. AC1-AC9 were re-derived by executing the documented CLI end to end rather than by reading TEST.md. Every claimed validation figure reproduced exactly. NO BLOCKING FINDING EXISTS. Ten non-blocking findings are recorded: the sharpest are a second '## Unreleased' heading appended below the released 0.9.0 section in CHANGELOG.md (a filing defect, not a false statement), an uncaught AttributeError in show() when a published response record is valid JSON but not an object (fails closed, but escapes the declared error-code contract), and six surviving guard mutations of which the two LINEAGE_FORK 'bypasses current head' guards are the only non-redundant pair. None of these is a G1-G5 violation: none is an explicit requirement violation, none makes the result not work, none is a regression, none causes data loss, security exposure or an irreversible side effect, and the validation evidence is present and reproduces. Under the profile-first gate with no quality profile and no blocking quality attributes, generic best practice, design taste and minor improvement are never blocking, so the correct verdict is PASS WITH NOTES and no correction loop starts. No user-owned decision is open: every note has an objective, optional required action and none requires user authority, so the decision gate is CLEAR.",
  "iteration": 2,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "final_review",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-02T12:20:00+09:00",
  "responsible_phase": null,
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Final Adversarial Review iteration 2 for Jira OS-30 across axes A-J: independent re-derivation of objective alignment, cross-phase consistency, contract vs implementation, implementation vs tests, docs vs behavior, lifecycle state machine, security/destructive change, over-engineering, hidden coupling, and decision provenance, plus execution-level re-verification of the four attempt-1 blocking findings. Excludes fixing any finding, OS-31 resume and transport work, PR creation, merge, and Jira status changes.",
  "sequence": 39,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/FINAL_REVIEW_iteration2.md",
  "state": "CLEAR",
  "verdict": "PASS",
  "verifies": {
    "iteration": 5,
    "phase": "test",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/test/5/B2#37"
  }
}
```

---

## Summary

I re-derived OS-30 from the working tree without accepting any phase gate PASS, and I verified the four attempt-1 findings by running the exact attacks that succeeded in attempt 1. **All four are genuinely closed.** I found **no blocking finding**.

### What I executed rather than read

| Check | Result |
| --- | --- |
| FA-001 through **both** harness seams with the **real** `ArtifactHumanApprovalPort` (not the suite's `FakePort`), n = 1, 2, 3, 4, 5, 7 open items | `ceil(n/3)` requests, **every** ledger key published exactly once, `clarification_errors == []` in all 12 runs |
| FA-002 2-item bundle: answer each item, then cancel | Both items answered independently with **different** actions; `show()` reports two distinct effective decisions; whole-request cancel drives both to `cancelled` with null heads |
| FA-003 forgery: in-place mutation / unlinked append / append + recomputed lineage event | Rejected `SCHEMA_MALFORMED` — `decision authority mismatch`, `decision_id content mismatch`, `decision_id content mismatch` |
| FA-004 `create --ledger-key run_ghost/implementation/9/B2#7` | `SOURCE_NOT_OPEN`, exit 2, **zero** artifacts written; identical in the installed twin; `--ledger-key` required by argparse; a real open record → `CREATED`; `open_decision_item:false` → `SOURCE_NOT_OPEN` |
| AC1–AC9 end to end via the **documented** installed CLI | All nine satisfied (see Test Review) |
| 16 targeted guard mutations vs the 44-test suite | 10 killed, 6 survived — **every** authority-critical re-derivation killed |
| Security probes: symlink / FIFO response file, `run_id` and `request_id` traversal | `CLARIFICATION_SECURITY_FAILURE` (exit 4), `CLARIFICATION_INVALID` (exit 2) |

### Claimed validation evidence — all reproduced

| Claim | My result |
| --- | --- |
| 44 focused tests | `Ran 44 tests ... OK` |
| 1,706 discovery tests, 6 skips | `Ran 1706 tests in 331.485s ... OK (skipped=6)` |
| `validate_skills.py` 714 checks | `Skill validation PASSED (714 checks)` |
| `verify_package.py` 195 source files | `Package verification PASSED (195 source files)` |
| Reproducible release archive build + verify | Built `dist/orca-skills-0.9.0.tar.gz`; `Verified archive` PASSED (`dist/` is gitignored — no debris) |
| Source/installed byte parity | `clarification_protocol.py` both `2dc472e9…`; `run_logging.py` both `d45e4038…`; the two `run_logging.py` diffs are line-for-line identical |
| `compileall` clean, `git diff --check` clean | Both clean |

### Axis-by-axis position

- **A objective alignment** — satisfied end to end. Every AC has an executable path through the shipped installed tool.
- **B cross-phase consistency** — no contradiction that changes behavior. One internal tension inside DESIGN itself (F2-N06).
- **C contract vs implementation** — the documented CLI verbs, flags, bundle cap (3), re-clarification bound (2) and `on_timeout`/`default_applicable` constants all match the code and were executed. `expand_scope()` exists in code but in no CLI contract (F2-N05).
- **D implementation vs tests** — tests were not weakened to pass; they kill every guard that FA-001…FA-004 turned on. Six lower-value guards remain unpinned (F2-N03).
- **E docs vs behavior** — attempt 1's successors are genuinely fixed: neither `SKILL.md` claims OS-30 is unimplemented, and `docs/ROADMAP.md:177` separates OS-30 (implemented) from OS-31 (not). One filing defect in `CHANGELOG.md` (F2-N01) and one imprecise Korean sentence in the loop Skill (F2-N10).
- **F lifecycle state machine** — `MAX_BUNDLE_ITEMS = 3` and `MAX_RECLARIFICATION_REVISIONS = 2` match the docs and were confirmed by execution (revision 0 → 1 → 2 → `AMBIGUITY_LIMIT_REACHED`). `invalid` is a fail-closed error rather than a returned status (F2-N06).
- **G security / destructive** — no secrets, no destructive behavior, no out-of-scope tracked file touched. Root `e2e_harness.py` (mtime 2026-09-01 03:17) is untouched pre-existing debris.
- **H over-engineering** — nothing unrequested. `expand_scope()` implements an explicit Jira Scope element.
- **I hidden coupling** — the only shared-asset edits (`release_manifest.py`, `validate_skills.py`, `run_logging.py`) are additive and required to ship the second installed tool; `run_logging.py` keeps its zero-`scripts/`-imports invariant and both copies stay byte-identical.
- **J decision provenance** — no unresolved `NEEDS_INPUT`/`CONFLICT` anywhere; the one high-impact design change (retaining bundles) carries an explicit USER DECISION; every gate boundary left a fenced decision-gate result and a responsible phase in the run-scoped artifacts. The ledger/sequence irregularities are real, were surfaced and correctly refused rather than papered over (F2-N07).

### Note for the coordinator (not a review finding)

The delta is entirely uncommitted. When creating the PR, stage **explicitly** — never `git add -A` — so that root-level `e2e_harness.py` (pre-existing debris) stays out. Whether `artifacts/runs/run_db374a3fd83a/` is committed is a human decision.

---

## Blocking Findings

**None.**

I state this deliberately, having held both halves of the budget instruction. Attempt 1 was right to fail this run, and I re-ran its four attacks rather than trusting the closure reports; each is closed at the level of executed behavior. The one attack that still succeeds — rewriting published `raw_response.txt` bytes and recomputing the response digest, the raw binding and the decision identity so `show()` serves "deploy to production" — is a **rewrite of immutable published bytes**, not an unlinked append, and is exactly the limit the approved DESIGN declares. Holding the implementation to cryptographic authenticity against a writer who can recompute every identity would be a false FAIL that ends the run for the wrong reason, so I have not raised it as blocking. It is recorded as F2-N09.

Every finding below is classified explicitly as a **production defect** or a **coverage/documentation gap**, and none reaches G1–G5.

---

## Non-Blocking Findings

### F2-N01 — CHANGELOG.md gains a second `## Unreleased` heading below a released section
- **Quality Attribute:** documentation accuracy · **Severity:** HIGH · **Blocking:** No
- **Class:** documentation gap (not a production defect)
- **Location:** `CHANGELOG.md:109`
- **Issue:** The file already has a live `## Unreleased` section at line 6 holding the OS-28/OS-29 entries. The OS-30 entry was appended at end of file, creating a **second** `## Unreleased` heading *below* `## 0.9.0 - 2026-08-20` (line 89).
- **Reason / Evidence:** `grep -n '^## ' CHANGELOG.md` → lines 6, 89, 109. The file's own preamble declares a "Keep a Changelog-inspired format", in which unreleased work belongs in the single top section. A reader who opens the Unreleased section does not see OS-30.
- **Why not blocking:** the heading is correctly labelled `Unreleased`, so the text makes no false claim about behavior — unlike attempt 1's N-903/N-1001, which asserted OS-30 was unimplemented. This is misfiling, not misstatement, and misfiling is a documentation-structure improvement, which the profile-first gate never treats as blocking.
- **Recommended (non-gating) Action:** move the OS-30 bullet into the existing `## Unreleased` → `### Added` list at the top and delete the trailing duplicate heading.

### F2-N02 — `show()` dereferences a published response record before the object-type guard
- **Quality Attribute:** error-contract integrity · **Severity:** MEDIUM · **Blocking:** No
- **Class:** production defect (fail-closed; no authority served)
- **Location:** `scripts/clarification_protocol.py:1019` (and the byte-identical installed copy)
- **Issue:** `show()` calls `response_raw.get("request_id","")` *before* handing the value to `_validate_response_record`, whose first line is `if not isinstance(raw,dict): raise SchemaMalformed("response object")`. A published response record that is valid JSON but not an object bypasses the existing guard.
- **Reason / Evidence:** replacing one `responses/<id>/record.json` with `[]` produces an uncaught `AttributeError: 'list' object has no attribute 'get'`, a Python traceback and **exit 1**, instead of `{"code":"SCHEMA_MALFORMED"}` and exit 2. The equivalent decision-record corruption is handled correctly (exit 2, `CLARIFICATION_INVALID`), and `_lineage_events` wraps its parse in `try/except Exception -> LineageInvalid`; only this one line is unguarded.
- **Why not blocking:** it fails closed. No decision, request or lineage is served, no record is written, and no authority the human did not give is exposed. It violates the declared 13-code CLI contract cosmetically, not the safety property. Not G1–G5.
- **Recommended (non-gating) Action:** validate the parsed object before reading `request_id` — e.g. resolve the bound request from the record only after an `isinstance(..., dict)` check, or wrap the sweep the way `_lineage_events` already does.

### F2-N03 — Six guard mutations survive the focused suite; two of them are not redundant
- **Quality Attribute:** test effectiveness · **Severity:** HIGH · **Blocking:** No
- **Class:** coverage gap (not a production defect — every guard is present and correct in production)
- **Location:** `scripts/test_clarification_protocol.py` (44 tests) vs `scripts/clarification_protocol.py:641-644, 692-697, 731-733, 869-871, 962-963, 973-978`
- **Issue:** I mutated 16 guards one at a time and re-ran the focused suite. Ten died. Six survived:

  | Mutated guard | Survives? | Redundant with another live guard? |
  | --- | --- | --- |
  | `_validate_response_evidence` raw digest + byte-count check | survived | **No for schema v1** — the v1 path `return`s before the binding check, so a mutated build verifies no v1 raw bytes at all |
  | `len(roots)>1 -> OrphanDecision` | survived | Largely — the reachability check usually re-raises `OrphanDecision` |
  | `visit()` `dependency: cycle` | survived | No, but removal yields `RecursionError`, not silent acceptance (confirms carried-in N4-103) |
  | write-side `cancelled item cannot receive a first decision` | survived | **Yes** — an identical guard runs before the response write |
  | post-write `raw verification failed` (0600 + content) | survived | Yes — `_write_directory` already creates the file `0600` |
  | `LINEAGE_FORK` supersession **and** cancellation "bypasses current head" | survived | **No** — see below |

- **Reason / Evidence:** the "bypasses current head" pair is the only non-redundant survivor. With both removed, a `decision_cancelled` event naming a superseded (non-head) decision no longer raises `LINEAGE_FORK`; it sets `head=None` and derives item status `cancelled`, so a stale cancellation silently cancels an item that had already moved on. The fork checks (`len(set(values))>1`) and the reachability walk do not cover this, because the graph itself stays well-formed — only the *replay order* is wrong. This is exactly the class of property DESIGN sections 9 and 10 make load-bearing.
- **Why not blocking:** all six guards exist and behave correctly in the shipped code; I verified the head-bypass property directly against unmutated source. Every guard that carries FA-001…FA-004's authority — `request_id`, `response_id`, `decision_id`, `event_id`, raw-binding uniqueness, `SOURCE_NOT_OPEN`, `MAX_BUNDLE_ITEMS`, `default_applicable`/`on_timeout`, and `publication_batches` batch completeness — was **killed**, so the security-critical re-derivation chain is genuinely pinned. Declared validation evidence is present and reproduces, so this is not G5.
- **Recommended (non-gating) Action:** if a TEST iteration is spent, prioritise exactly one test — a valid multi-transition history where a `decision_cancelled` names a superseded decision — which kills both head-bypass mutants at once. The v1 raw-digest case is the natural second.

### F2-N04 — TEST.md carries no AC1–AC9 acceptance matrix (carried-in N5-101, confirmed)
- **Quality Attribute:** validation traceability · **Severity:** HIGH · **Blocking:** No
- **Class:** documentation gap (not a production defect)
- **Location:** `artifacts/runs/run_db374a3fd83a/TEST.md`
- **Issue:** TEST.md is a delta-only report ("Corrected Acceptance Evidence" covers T4-001/T4-002 only). The run's consolidated AC1–AC9 matrix lives in review artifacts, re-derived by the TEST reviewer rather than supplied by the TEST worker.
- **Reason / Evidence:** confirmed by reading TEST.md in full. I did not resolve this by reading a review file either — I re-derived AC1–AC9 by execution (see Test Review), and **all nine pass**.
- **Why not blocking:** the validation evidence itself exists, is executable, and reproduces; only its consolidated presentation in the phase-owning artifact is missing. G5 is about missing evidence, not about where a correct summary is filed. TEST has 4 attempts left, so I could have made this blocking cheaply — I have not, because doing so would be manufacturing a finding rather than reporting one.
- **Recommended (non-gating) Action:** if a TEST iteration is spent anyway, restore the AC1–AC9 matrix in TEST.md with one named executable per row.

### F2-N05 — Jira Scope's "scope expansion" has no CLI verb and appears in no CLI contract
- **Quality Attribute:** operability · **Severity:** MEDIUM · **Blocking:** No
- **Class:** coverage gap (the capability exists and is tested)
- **Location:** `scripts/clarification_protocol.py:571` (`expand_scope`), `_parser()` at line 1033, `orca-worker-reviewer-orchestration/SKILL.md` OS-30 section, DESIGN section 11
- **Issue:** `ArtifactHumanApprovalPort.expand_scope()` implements the Jira Scope element "the lifecycle handles … scope expansion without erasing history" — it validates new child identities, refuses reuse of an existing identity, requires the parent to have an effective decision, and appends `decision_scope_expanded`. But it is not in the `HumanApprovalPort` Protocol, has no `clarification` subcommand, and is named nowhere in SKILL.md or DESIGN's CLI contract. A Coordinator following the shipped Skill has no operator-facing path to it.
- **Reason / Evidence:** `_parser()` defines exactly `create`, `respond`, `show`. `grep expand_scope` finds one caller — a single unit test at `scripts/test_clarification_protocol.py:605`. DESIGN mentions `decision_scope_expanded` only in the lineage-event catalogue (lines 115, 469), never in section 11.
- **Why not blocking:** the Jira requirement is that the *lifecycle* handle scope expansion without erasing history, and it does; no acceptance criterion (AC1–AC9) names scope expansion, and AC8's CLI requirement is about the **response** path, which is fully exposed. Critically, no shipped document claims a CLI verb that does not exist, so there is no docs-vs-behavior falsehood. Not G1.
- **Recommended (non-gating) Action:** either add an `expand` subcommand, or state in SKILL.md that scope expansion is a library-level operation in this release.

### F2-N06 — DESIGN promises an `invalid` value in `show.item_statuses` that the code can never emit
- **Quality Attribute:** contract precision · **Severity:** LOW · **Blocking:** No
- **Class:** documentation gap, internal to a run artifact
- **Location:** `artifacts/runs/run_db374a3fd83a/DESIGN.md:119, 538-540, 599-601` vs `scripts/clarification_protocol.py:1029-1030`
- **Issue:** DESIGN declares `ITEM_EFFECTIVE_STATUS = unresolved | effective | cancelled | invalid` and says `show` maps every `decision_item_id` "to that closed status". `_lineage_state` returns only `unresolved`, `effective` or `cancelled`; an invalid item raises and fails the whole `show()` closed.
- **Reason / Evidence:** confirmed by reading both. DESIGN's own line 539 — "any invalid item fails the request closed" — describes what the code actually does, so the tension is *inside DESIGN*, and the code implements the safer of the two statements.
- **Why not blocking:** no shipped document makes this claim (DESIGN.md is a run artifact, not distributed), behavior is the safe branch, and nothing depends on the unreachable value.
- **Recommended (non-gating) Action:** none required; if DESIGN is edited for another reason, reconcile section 10's sentence with section 9's fail-closed rule.

### F2-N07 — Run-scoped decision ledger absent; duplicate top-level `sequence` values
- **Quality Attribute:** decision provenance · **Severity:** HIGH · **Blocking:** No
- **Class:** process/meta-artifact gap — **outside the OS-30 delta**; not attributable to any of the five phases
- **Location:** `artifacts/runs/run_db374a3fd83a/` (no `decision_ledger/` directory); duplicate sequences reported at 14 and 18
- **Issue:** the orchestration Skill's own decision-gate contract names `artifacts/runs/<run-id>/decision_ledger/<NNNNNN>/record.json` as the authority, and no such directory exists for this run. Because the fenced blocks are hand-authored into worker/reviewer artifacts and one worker artifact was replaced in place, `sequence` is not a unique key across the run.
- **Reason / Evidence:** `ls artifacts/runs/run_db374a3fd83a/` shows no `decision_ledger`. The maximum `sequence` anywhere in the run is **38** (`REVIEW_TEST_iteration5.md`), which is why this report uses 39. `ORCHESTRATOR_LOG.md:45` records the run's own analysis: *"Sequence integrity: duplicates 14/18 confirmed real; root defect identified as the absent run-scoped ledger; ledger NOT fabricated; this record uses sequence 23."*
- **Why not blocking:** three reasons, all of which I checked. (1) Every gate boundary **did** leave its result and responsible phase in the run-scoped artifacts and in `ORCHESTRATOR_LOG.md`, so the decision evidence exists — only its canonical index does not, which is not G5. (2) The run surfaced the irregularity itself and **refused to fabricate a ledger**, which is the correct fail-closed choice; retro-fitting one would have been the actual provenance defect. (3) The OS-30 delta does not touch the ledger writer, so this is a property of how the run was operated, not of the code under review. Raising it as blocking would attribute an orchestration-process gap to a phase that did not cause it.
- **Recommended (non-gating) Action:** for future runs, drive gate records through `run_logging.append_decision_ledger_record` so `sequence` is allocated by the writer and in-place artifact replacement cannot collide.

### F2-N08 — Sweep-listing completeness and unpinned ROADMAP prose (carried-in N-1101, N-1102, confirmed)
- **Quality Attribute:** documentation durability · **Severity:** LOW · **Blocking:** No
- **Class:** coverage/documentation gap
- **Location:** `scripts/validate_skills.py:117-120` (`OS30_SCHEMA_DOC_TEXT`, `OS30_SCHEMA_DOCS`), `docs/ROADMAP.md:177`
- **Issue:** `validate_os30_contract` pins the v2/historical-v1 sentence in all five shipped docs and pins the six OS-30 semantic anchors in both Skills, but nothing anchors the corrected ROADMAP sentence that separates "OS-30 implemented" from "OS-31 not implemented". A future edit could silently regress the exact statement attempt 1's successor N-1001 was raised to fix.
- **Reason / Evidence:** read the validator delta; the five-document check covers only `OS30_SCHEMA_DOC_TEXT`.
- **Why not blocking:** the current prose is correct today, and the anchor is a durability improvement.
- **Recommended (non-gating) Action:** add one value-pinned anchor for the ROADMAP OS-30/OS-31 sentence.

### F2-N09 — Threat-model boundary re-confirmed by execution (informational, **not a defect**)
- **Quality Attribute:** security posture clarity · **Severity:** INFO · **Blocking:** No
- **Location:** `scripts/clarification_protocol.py` `_validate_response_evidence` / `_validate_decision_record`; DESIGN sections 6–10
- **Issue:** a writer with filesystem access who rewrites the published `raw_response.txt` bytes and then recomputes the response record digest, the `binding_id` and the `decision_id` makes `show()` serve `deploy to production` for a human who chose `deploy to staging`.
- **Reason / Evidence:** I executed this. It requires **rewriting immutable published bytes**, not appending. All three append-only forgeries (FA-003's original vector plus two stronger variants) are rejected.
- **Why this is not a finding:** the approved DESIGN bounds the guarantee to structural integrity against unlinked append forgery and explicitly disclaims cryptographic authenticity; a previous reviewer already proved that folding the raw digest into `response_id` falls to the same rewrite. No unkeyed content-addressing scheme can do better. Recorded so the boundary is stated in this report rather than rediscovered.

### F2-N10 — Loop `SKILL.md` sentence reads as a capability claim in isolation
- **Quality Attribute:** documentation precision · **Severity:** INFO · **Blocking:** No
- **Location:** `orca-worker-reviewer-loop/SKILL.md:364-367`
- **Issue:** "OS-30의 구조화된 질문, item별 응답, decision과 append-only lineage 계약은 이 Skill에 구현되어 있다" ("…is implemented in this Skill") could be read as claiming a runtime the direct-session loop does not have.
- **Reason / Evidence:** the same sentence's second clause disclaims the OS-29 execution path and the run-scoped artifact store/CLI, the anchor block states `OS30_EXECUTABLE_ARTIFACT_STORE = unavailable_in_direct_loop`, and the English paragraph repeats the disclaimer. `validate_skills.py` asserts the loop Skill must **not** carry the orchestration-only executable anchor.
- **Why not blocking:** the boundary is stated three times in the same section, so no reader reaches a false conclusion. This is not the N-903 class (which asserted OS-30 was *un*implemented and was flatly false).
- **Recommended (non-gating) Action:** narrow the subject to "계약(semantics)" if the section is edited.

---

## Test Review

### Are the tests verifying real risk, or were they weakened to pass?

**Verifying real risk.** I checked this three ways.

**1. Mutation campaign (the direct test).** 16 guards mutated one at a time, focused suite re-run each time. Result: 10 killed / 6 survived. The kills land exactly where attempt 1's findings landed:

| Killed guard | Which attempt-1 finding it protects |
| --- | --- |
| `publication_batches` returning all remaining batches | FA-001 |
| `MAX_BUNDLE_ITEMS = 3` | FA-001 / FA-002 |
| `response_id` content re-derivation | **N-901** — the carried-in HIGH is genuinely closed; TEST's added coverage kills this mutant |
| `decision_id` content re-derivation | FA-003 |
| `event_id` content re-derivation | FA-003 |
| `request_id` content re-derivation | FA-003 (T4-002 closure confirmed) |
| raw-binding uniqueness | FA-003 |
| `SOURCE_NOT_OPEN` in CLI `create` | FA-004 |
| `default_applicable` / `on_timeout` guard | AC3 / AC4 |

The six survivors are recorded as F2-N03; only the `LINEAGE_FORK` head-bypass pair is non-redundant.

**2. Real-port re-execution of FA-001.** The shipped suite's `HarnessClarificationSeamTests` uses a `FakePort`, so the real `ArtifactHumanApprovalPort.publish()` is never exercised through the harness seam by any shipped test. I substituted the real port and re-ran at n = 1, 2, 3, 4, 5, 7 across both harnesses: `ceil(n/3)` requests, complete item coverage, no errors. The claim survives the stronger test.

**3. Independent AC1–AC9 derivation by execution** (because TEST.md no longer carries the matrix — F2-N04). All via the documented installed CLI at `orca-worker-reviewer-orchestration/tools/clarification_protocol.py`:

| AC | Evidence | Result |
| --- | --- | --- |
| AC1 `NEEDS_INPUT` creates a structured request with a stable ID | `create` against a real open B2 record → `CREATED`, `request_10baa757…`; re-`create` is idempotent; `CONFLICT` covered by `test_needs_input_and_conflict_create_complete_non_default_request`; the harness seam publishes automatically at terminal `BLOCKED` | PASS |
| AC2 ≥1 actionable option + explicit recommendation | `_validate_item` enforces 1–8 options each with `option_id`/`label`/`action`/`tradeoff`, plus `recommended_option_id` ∈ options and non-empty `recommendation_rationale`; a request lacking them is refused | PASS |
| AC3 a recommendation is not approval | the non-recommended option `production` was accepted normally; no decision exists until an explicit `respond`; `recommended_default.json` is an invalid fixture | PASS |
| AC4 timeout / no response is not implicit approval | `default_applicable:false` and `on_timeout:"no selection; run remains blocked"` are pinned in `_validate_request_record`; with no response, `show` reports `effective_decisions: {item: null}`, `item_statuses: unresolved` | PASS |
| AC5 original response **and** normalized decision both retained | `raw_response.txt` (0600) alongside `record.json` for every response; the decision record carries `option`/`custom`, `resolves`, actor, provenance, timestamps | PASS |
| AC6 ambiguous response causes bounded re-clarification | free text `"maybe do whatever you think best"` → `RECLARIFICATION_CREATED` (exit 3), then a second → `RECLARIFICATION_CREATED`, then a third → `AMBIGUITY_LIMIT_REACHED` at revision 2. **No decision was created at any step.** In a 2-item bundle, an ambiguous answer on item B opened a new revision while item A's decision stayed `effective` | PASS |
| AC7 changed response supersedes and preserves lineage | answering the current revision with `staging` after `production` → `DECIDED` plus lineage `1 decision_superseded decision_4efa6d… -> decision_60ed66…`; both decision records remain byte-present; a change sent to a **stale** revision correctly returns `STALE_REQUEST` with no supersession | PASS |
| AC8 artifacts + explicit CLI, not a terminal UI | the tool never reads a TTY, never prompts and never calls Orca `ask`; every documented invocation in SKILL.md executes verbatim; symlink and FIFO response files are refused (`CLARIFICATION_SECURITY_FAILURE`, exit 4) | PASS |
| AC9 sensitive responses not copied without limit | with `--sensitivity sensitive` and a `sensitive:true` envelope, the decision record shows `value:null`, `redacted:true`, `raw_response_sha256:…`; a repository-wide grep found the secret in **exactly one** artifact — `raw_response.txt`, mode `-rw-------`; `show` stdout is clean | PASS |

### Fixtures and negative coverage
`scripts/fixtures/clarification_protocol/` holds `valid/needs_input_request.json` and the negatives `invalid/oversized_bundle.json` and `invalid/recommended_default.json` — small, but each pins a rule I confirmed live in the validator (`bundle: requires 1..3 items`; `request: implicit authority forbidden`).

### Suite health
44 focused tests OK; 1,706 discovery tests OK with 6 skips in 331s. No test was skipped, `expectedFailure`-marked or narrowed to accommodate the delta.

---

## Final Decision

**RESULT: PASS · REVIEW_VERDICT: PASS WITH NOTES · DECISION_GATE_STATE: CLEAR**

The four attempt-1 blocking findings are closed at the level of executed behavior, verified by re-running the original attacks plus stronger variants — including FA-001 through the real port that no shipped test exercises. All nine acceptance criteria have an executable path through the shipped installed CLI, and every claimed validation figure reproduced exactly. There is no G1 requirement violation, no G2 failure to work, no G3 regression, no G4 data-loss/security/irreversible side effect, and no G5 missing validation evidence.

Ten non-blocking findings are recorded. Three deserve attention if a phase iteration is spent for another reason — the duplicate `## Unreleased` heading (F2-N01), the unguarded `show()` dereference (F2-N02), and the two non-redundant `LINEAGE_FORK` head-bypass guards left unpinned (F2-N03) — but none of them is a production defect that changes what a human authorized, and none is grounds to consume the exhausted implementation budget. **No correction loop starts. OS-30 is ready for the PR step.**
