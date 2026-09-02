# Reviewer Result — IMPLEMENTATION iteration 9 (final attempt)

IMPLEMENTATION_REVIEW: PASS

RESULT: PASS

REVIEW_VERDICT: PASS WITH NOTES

DECISION_GATE_STATE: CLEAR

Reviewer: B3 (Claude Opus, Reviewer B3, fresh session). Verifies: IMPLEMENTATION Worker B2,
`artifacts/runs/run_db374a3fd83a/IMPLEMENTATION.md` (record `run_db374a3fd83a/implementation/9/B2#28`).
Baseline: `DESIGN.md` as corrected at design iterations 6 and 7 and PASSed — the baseline that
changed since iteration 8, and the one everything below is measured against.

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {
    "combined_gate": "719 PASS",
    "discovery_gate": "1701 PASS, 6 skipped",
    "focused_tests": "39 PASS",
    "mutations_reviewer_added": 20,
    "mutations_worker_enumerated": 13,
    "mutation_survivors_worker_set": 0,
    "validator_checks": 714
  },
  "grounds": "Implementation iteration 9 PASSes the phase gate. Every carried-in finding is closed and I re-derived each one by executing or mutating the shipped code, never by reading IMPLEMENTATION.md. N-802: the exact unlinked append forgery that succeeded at iteration 8 -- a well-formed second response, its content-addressed binding and its decision copied in with nothing deleted and nothing edited -- now fails closed as ORPHAN_DECISION 'unlinked decision', and every pre-existing file is byte-identical after the rejected read; both legitimate shapes still work, with a changed answer emitting 2 decisions and 1 decision_superseded and cancel-then-redecide emitting 2 decisions, 1 decision_cancelled and 1 anchor-consuming decision_superseded, and the timestamp-based later fallback is genuinely absent from the source rather than guarded, with no occurrence of normalized_at, occurred_at, sort, max or min anywhere in the head-derivation body. D6-001: request-level cancel with zero prior answers on 1-, 2- and 3-item requests emits exactly one null-predecessor marker per item, derives cancelled for every item through show.item_statuses, creates no reset anchor, and rejects a subsequent first answer with LINEAGE_INVALID on the write side before any byte is written and on the read side when a decision is planted beside the marker; the mixed case is correct too, an item that had been answered keeps a consumable anchor and can be redecided while an item that never had one is irreversibly abandoned. R8-002: a hand-built homogeneous historical v1 request/response/decision set carrying no response_bindings entry now reads an effective head with its bytes unchanged, which is precisely what failed at iteration 8; v2 still requires exactly one binding, with a deleted binding and an appended second binding both failing SCHEMA_MALFORMED; disjoint v1 and v2 lineages coexist in one tree and each is admitted independently; a crossed generation is SCHEMA_VERSION_MIXED; and a binding published against a v1 response, whether truthful or lying, is correctly ignored rather than consulted. R8-001: I re-ran the worker's full 13-mutation set in a sandbox copy with the installed twin re-synced and confirmed zero survivors, including M5 and M6, the two R7-004 named by name, and M11, M12 and M13, the three internal checks of the binding. R8-003: the OS-30 anchors exist and fire -- all nine anchor deletions across both Skills, all five documentation-statement deletions, the false-executable-parity case and the simultaneous-value-drift case each turn the validator red, the four targeted regressions all fail when validate_os30_contract is unregistered, and the required v2/immutable-historical-v1 statement reaches all seven named files, five as prose and two as the value-pinned anchor. Nothing regressed: FA-001 publishes 1, 1 and 2 requests covering 2/2, 3/3 and 4/4 items through both real harness seams with a real ArtifactHumanApprovalPort; the full FA-002 CLI matrix behaves exactly as the corrected design specifies over a real ledger-backed 3-item bundle; FA-004 rejects the fabricated key with SOURCE_NOT_OPEN exit 2 writing no clarifications directory; and R7-001, R7-002 and R7-003 all still fail closed or behave correctly on the same attacks and drives I used to raise them. Scope is clean: source and installed copies are byte-identical, no tracked historical artifact was modified, no OS-31 surface was added, the untracked root e2e_harness.py is untouched at its original 03:17 mtime, bundles remain bounded at 1..3, and the FA-002 --decision-item-id designator is present in every answer-mode example and absent from --cancel. I went past the worker's enumerated set as this dispatch requires and added twenty mutations of my own against load-bearing checks it did not enumerate; six died, and for thirteen of the fourteen survivors I confirmed by running real attacks against each mutant that the tree still fails closed through a redundant guard, so they are depth of coverage rather than defects. Exactly one survivor is materially load-bearing: removing the response_id content re-derivation lets a decision be transferred from one bundle item to another by consistent in-place edits, and that mutant survives both the 39-test focused suite and the 719-test combined gate. I record it as N-901 at HIGH severity and non-blocking, and I have written out my reasoning so the Coordinator can overrule me: the guard is present and effective in the shipped code, which I proved by running the attack against the real build, so nothing defective ships; the coverage state is unchanged from iteration 8 rather than a regression; and no prior Required Action in this run named this case, so making it blocking on the final attempt would apply an unannounced standard rather than the phase contract. Three further non-blocking notes are recorded: N-902 for the redundantly-covered replay guards, N-903 for stale in-Skill text that still says OS-30 is not implemented, and N-904 for DESIGN step 5's AST invariant living in test_release_package.py rather than test_os29_decision_gate.py -- I supplied the synthetic forbidden import myself and confirmed the guarantee is genuinely enforced there, so only the placement deviates. I did not find the corrected DESIGN unimplementable and I raise no blocking finding against the baseline. No user-owned choice is open: every remaining item is a determinate follow-up against the run's own approved design, so the gate state is CLEAR.",
  "iteration": 9,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "implementation",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-02T02:40:00Z",
  "responsible_phase": "implementation",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer closure verdict for IMPLEMENTATION iteration 9 of Jira OS-30 against the DESIGN baseline as corrected at iterations 6 and 7. Covers independent re-derivation by execution and mutation of N-802, R8-001, R8-002 and R8-003, the D6-001 zero-answer cancellation realization, the v1-read/v2-write admissibility split, validated-lineage-only head derivation with the later fallback removed, non-regression of FA-001, FA-002, FA-004 and R7-001/R7-002/R7-003, an independently constructed twenty-mutation extension beyond the worker's enumerated set, and scope (source/installed byte parity, OS-28/OS-29 preservation, no tracked historical artifact modified, no OS-31 expansion, untracked root e2e_harness.py untouched, bounded bundles and the FA-002 designator). Excludes fixing any finding, OS-31 resume and transport work, PR creation, merge, and Jira status changes.",
  "sequence": 29,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_IMPLEMENTATION_iteration9.md",
  "state": "CLEAR",
  "verdict": "PASS",
  "verifies": {
    "iteration": 9,
    "phase": "implementation",
    "run": "run_db374a3fd83a",
    "worker_record_key": "run_db374a3fd83a/implementation/9/B2#28"
  }
}
```

---

## Summary

I treated `IMPLEMENTATION.md` as a set of claims to test. Everything below was produced by running
or mutating the code in this worktree, in a sandbox copy where mutation was required. I modified no
production file, no test, and no artifact other than this review.

### N-802 is closed — the forgery that worked last round now fails closed

I ran the exact attack from iteration 8. One item answered `staging`; I then copied in a
well-formed second response, its content-addressed binding and its decision, produced by a real
second `ingest` in a donor tree, and copied in **no lineage event**. Nothing was deleted and
nothing was edited.

```text
legit head:        decision_880ba0a2321ba8fe51122982  {'action': 'deploy to staging', 'option_id': 'staging'}
donor (legit)  ->  changed answer emits 1 new lineage dir; head becomes decision_e43a7d51647c62deb57ad661

--- FORGERY APPLIED (nothing deleted or edited) ---
decisions present: ['decision_880ba0a2321ba8fe51122982', 'decision_e43a7d51647c62deb57ad661']
lineage present  : []
pre-existing files missing: [] | modified: []
show() FAILED CLOSED: OrphanDecision ORPHAN_DECISION | unlinked decision
tree byte-identical for pre-existing files after rejection: True
```

At iteration 8 this same shape served `deploy to production` from a `staging` answer. It is now
rejected, with every published byte preserved.

**Both legitimate shapes still work**, and they emit exactly the transitions §9 specifies:

```text
A changed answer:        2 decisions, events=[('000000','decision_superseded', d1, d2)]
                         head==successor: True | action: deploy to production | status: effective
B after cancel:          heads={item: None} statuses={item: 'cancelled'}
                         events=[('000000','decision_cancelled', d1, None)]
B cancel-then-redecide:  2 decisions, events=[('000000','decision_cancelled', d1, None),
                                              ('000001','decision_superseded', d1, d2)]
                         head==successor: True | action: deploy to production | status: effective
```

The `later` fallback is **removed from the source, not guarded**. The only remaining occurrence of
the word in the file is an unrelated prose comment at line 190, there is no `normalized_at >`
comparison anywhere, and the entire head-derivation body (`_lineage_state`,
`scripts/clarification_protocol.py:906-981`) contains no `occurred_at`, `normalized_at`, `sort`,
`max(` or `min(`.

### D6-001 is realized exactly, and the asymmetry is right

Request-level cancel with **zero** prior answers, at all three bundle sizes:

| n | markers | one per item | decisions dir | item_statuses | reset anchor | later first answer | bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | yes | `[]` | all `cancelled` | `None` | `LINEAGE_INVALID` | unchanged |
| 2 | 2 | yes | `[]` | all `cancelled` | `None` | `LINEAGE_INVALID` | unchanged |
| 3 | 3 | yes | `[]` | all `cancelled` | `None` | `LINEAGE_INVALID` | unchanged |

The write-side rejection happens **before any write** (`_ingest_one`,
`scripts/clarification_protocol.py:721-723`, ahead of `response_id` derivation), and the tree is
byte-identical afterwards. The **read side** is independently correct: planting a decision beside
the marker — again by copying a legitimately produced response, binding and decision, deleting and
editing nothing — gives, at n=1, 2 and 3:

```text
read side: LineageInvalid LINEAGE_INVALID | decision appended after null-predecessor cancellation
pre-existing bytes unchanged: True
```

The mixed case is the sharpest test of §9 step 4 and it behaves as designed. On a 3-item bundle
where only item A was answered before a request-wide cancel:

```text
statuses after mixed cancel: {A: 'cancelled', B: 'cancelled', C: 'cancelled'}
  A (was answered):   anchor=decision_2457132d65cb6446  -> redecide DECIDED
  B (never answered): anchor=None                       -> redecide REJECTED[LINEAGE_INVALID]
```

An item that had a decision keeps a consumable reset anchor; an item that never had one is
irreversibly abandoned. That is precisely the distinction the corrected design draws.

### R8-002 is closed — the v1 read path works again and v2 is unchanged

I hand-built a genuine homogeneous v1 set — request v1 under `os30-request-v1`, response v1 with no
`decision_item_id` under `os30-response-v1`, `raw_response.txt`, matching decision, **no**
`response_bindings` directory at all:

```text
A pure v1 tree: response_bindings dir exists: False
  show OK -> heads: {'item_3970a23c…': 'decision_f386a08ce7181065e4c92252'}  statuses: {'…': 'effective'}
  head==built decision: True
  bytes unchanged after read: True
```

That is the exact set that failed `SCHEMA_MALFORMED response raw binding mismatch` at iteration 8.
The mechanism is the generation guard at `scripts/clarification_protocol.py:646-649`, which returns
before any binding lookup for `schema_version==1`.

The v2 side is not weakened, and the mixed/disjoint rules hold:

| Case | Observed |
| --- | --- |
| v2 with its binding | effective head |
| v2, binding **deleted** | `SCHEMA_MALFORMED` response raw binding mismatch |
| v2, **second** binding appended | `SCHEMA_MALFORMED` response raw binding mismatch |
| **Disjoint** v1 + v2 lineages in one tree | both read independently, both effective |
| v1 response against a v2 request | `SCHEMA_VERSION_MIXED` request/response generation |
| v1 + stray **matching** binding | ignored; still effective |
| v1 + **lying** binding | ignored; neither retrofits authority nor invalidates |

The last two are the design's "a binding for a v1 response … is non-authoritative extra evidence and
must not be consulted", and the implementation honours both halves.

### R8-001 is closed on its own terms — zero survivors in the worker's set

Re-run independently: sandbox copy of the tree, installed twin re-synced, one mutation at a time,
source restored after each. Baseline 39 PASS.

| ID | Disabled/weakened check | My result |
| --- | --- | --- |
| M1 | response-evidence validator no-op | KILLED (failures=5) |
| M2 | decision authority field tuple empty | KILLED (failures=1) |
| M3 | reclarification forced to item 0 | KILLED (failures=2) |
| M4 | decision validator pass-through | KILLED (failures=6) |
| **M5** | **decision-ID content derivation removed** | **KILLED (failures=1)** |
| **M6** | **decision closed fields/schema/version/directory checks removed** | **KILLED (failures=2, errors=1)** |
| M7 | lineage validator pass-through | KILLED (failures=1) |
| M8 | publication collapsed to one batch | KILLED (failures=2) |
| M9 | CLI ledger verification removed | KILLED (failures=1) |
| M10 | item-membership guard disabled | KILLED (errors=1) |
| **M11** | **binding identity drops digest** | **KILLED (failures=1)** |
| **M12** | **exactly-one binding weakened to at-least-one** | **KILLED (failures=1)** |
| **M13** | **raw-bytes/digest rechecks removed** | **KILLED (failures=1)** |

Survivors: **0/13**. M5 and M6 are the two R7-004 named by name and that survived at iteration 8;
M11, M12 and M13 are the three internal checks of the binding I said had no coverage. All five are
now dead. My M6 patch is shaped slightly differently from the worker's, so my failure count differs
from the reported 18 errors; the material claim — that it is caught — reproduces exactly.

### I extended the set, and one genuine gap remains

This dispatch asks me to verify the enumerated set is not a convenient subset, so I wrote twenty
further mutations of my own against load-bearing checks the worker did not enumerate. Six died
outright (`X3` conflicting-fork, `X6` write-side irreversibility, `X8` supersession linkage, `X10`
generation match, `X13` binding directory-name binding, `X17` decision→item/`source_ledger_key`
binding). Fourteen survived the suite — but a surviving mutant is only interesting if it changes
behavior, so I ran five real attacks against **each** mutant build rather than reasoning about it.
Thirteen of the fourteen still fail closed through a redundant guard; the details are in N-902.

Exactly one is materially load-bearing and uncovered: removing the `response_id` content
re-derivation lets a decision be moved from one bundle item to another. That is N-901, recorded
non-blocking with my reasoning written out in full so the Coordinator can overrule me.

### R8-003 is closed, and the anchors actually fire

`scripts/validate_skills.py` gained `validate_os30_contract` (+35 lines), registered in `main()`.
I did not take its existence as evidence; I deleted each anchor in turn and re-ran the validator.

```text
orchestration DELETE OS30_SCHEMA_GENERATIONS…   -> FAILS: OS-30 shared semantic anchor missing or duplicated
orchestration DELETE OS30_AUTHORITY…            -> FAILS
orchestration DELETE OS30_NO_IMPLICIT_APPROVAL… -> FAILS
orchestration DELETE OS30_RESUME_BOUNDARY…      -> FAILS
loop          DELETE (same four)                -> FAILS (all four)
README.md / INSTALL.md / CHANGELOG.md / docs/ROADMAP.md / docs/COMPATIBILITY.md
              DELETE schema statement           -> FAILS: "<file>: OS-30 v2/historical-v1 schema statement missing"
loop claims executable parity                   -> FAILS: OS-30 non-executable boundary missing or duplicated
both Skills drift to the SAME wrong value       -> FAILS (value-pinned, so equality-only drift is caught)
```

That last case is the one that matters most: the anchors are value-pinned rather than compared for
equality, so simultaneous drift in both Skills is caught rather than passing as "still in parity".

The required statement reaches **all seven** named files — five as the prose sentence, both
`SKILL.md` files as the `OS30_SCHEMA_GENERATIONS = new_request_response_v2; immutable_historical_v1`
anchor, which says the same thing and is what the validator pins:

```text
README.md                                   prose=1 anchor=0
INSTALL.md                                  prose=1 anchor=0
CHANGELOG.md                                prose=1 anchor=0
docs/ROADMAP.md                             prose=1 anchor=0
docs/COMPATIBILITY.md                       prose=1 anchor=0
orca-worker-reviewer-orchestration/SKILL.md prose=0 anchor=1
orca-worker-reviewer-loop/SKILL.md          prose=0 anchor=1
```

Both Skill variants carry the four shared anchors and exactly one distinct executable-store anchor
(`orchestration_only` vs `unavailable_in_direct_loop`), with each forbidden from carrying the
other's — so parity is asserted where it is real and false parity is rejected. The four targeted
regressions in `scripts/test_validate_skills.py` are load-bearing: unregistering
`validate_os30_contract` turns all four red (`FAILED (failures=4)`).

### Nothing regressed

FA-001, through the **real** `E2EHarness` and `OrcaRuntimeHarness` seams with a **real**
`ArtifactHumanApprovalPort`, counting request directories actually written to disk:

```text
n=2 E2EHarness           requests=1 items_covered=2/2 errors=[]
n=2 OrcaRuntimeHarness   requests=1 items_covered=2/2 errors=[]
n=3 E2EHarness           requests=1 items_covered=3/3 errors=[]
n=3 OrcaRuntimeHarness   requests=1 items_covered=3/3 errors=[]
n=4 E2EHarness           requests=2 items_covered=4/4 errors=[]
n=4 OrcaRuntimeHarness   requests=2 items_covered=4/4 errors=[]
```

FA-002, over the shipped CLI against a real `decision_ledger`-backed 3-item bundle (sequence 0
correctly reserved as the OS-29 run-entry declaration):

| Case | Observed |
| --- | --- |
| Answer A by `--decision-item-id` | `DECIDED`, exit 0 |
| Partial state after A | `{A: decision_00adc4…, B: None, C: None}` |
| Answer C independently | `DECIDED`, exit 0, only C advances |
| Exact replay of A | identical response/decision IDs, exit 0 |
| Same token, different content | `CLARIFICATION_ID_CONFLICT`, exit 4 |
| Answer mode with no selector | `SCHEMA_MALFORMED`, exit 2 |
| Selector not in request | `ITEM_NOT_IN_REQUEST`, exit 2 |
| `--cancel` **with** a selector | `CANCEL_REQUEST_INVALID`, exit 2 |
| Request-wide `--cancel` | `CANCELLED`, all heads cleared, all items `cancelled` |

FA-004, verbatim: `create` with the fabricated `run_ghost/implementation/9/B2#7` returns
`{"schema_version":1,"status":"ERROR","code":"SOURCE_NOT_OPEN"}` exit 2 and creates no
`clarifications/` directory, while the same command against three real ledger records publishes a
3-item bundle with exit 0.

R7-001, R7-002 and R7-003, re-attacked with the same drives I used to raise them:

```text
R7-001 full raw rewrite + recomputed decision + swapped directory
       -> SCHEMA_MALFORMED | response raw binding mismatch
R7-002 in-place edit of decision.option.action
       -> SCHEMA_MALFORMED | decision authority mismatch
R7-003 n=2 idx=0/1 and n=3 idx=0/1/2 -> RECLARIFICATION_CREATED revision=1
       members_same=True narrowed_on_target=True others_byte_identical=True  (all five)
```

### Scope is clean

Source and installed protocol copies are byte-identical (`cmp` silent). No tracked artifact under
`artifacts/` is modified — the modified set is exactly the sixteen intended OS-30 files. The
untracked root `e2e_harness.py` is untouched at its original `Sep 1 03:17:19` mtime. `MAX_BUNDLE_ITEMS`
is still 3 and requests still accept `1..3`. Every executable `respond` answer-mode example carries
`--decision-item-id ITEM` and the `--cancel` example omits it. No OS-31 surface was added; the
shipped text explicitly keeps resume and transport out of scope. `git diff --check` is clean,
`compileall` is clean, and `verify_package.py` passes at 195 source files.

### On the design baseline

I did not find the corrected DESIGN unimplementable and I raise **no blocking finding against the
baseline**. The two changes design made at iterations 6 and 7 that I pushed for — removing the
`later` fallback in favour of validated-lineage-only head derivation, and splitting v1 read
admissibility from v2 write admissibility — are both implementable as written and are implemented
as written. N-801 from last round is closed by design: `response_bindings/` and the `binding_id`
formula are now in §2 and §3, and §3 states the bounded structural-integrity claim explicitly.

---

## Blocking Findings

**None.**

I want to be explicit about this, because I have failed this gate four times in this run and the
consequence of a false PASS here is worse than the consequence of an escalation. I looked for a
blocking finding and did not find one. Concretely:

- **No G1.** Every named clause of the corrected DESIGN that I could execute, I executed, and each
  behaves as specified. The only literal step-text deviation I found is N-904, and I confirmed by
  supplying the synthetic forbidden import myself that the guarantee that step protects is genuinely
  enforced — in a different file, by a stronger runtime test.
- **No G2.** No designed read or write path fails. The v1 read path that I failed this round's
  predecessor on now works; the graph derivation, the cancellation semantics, the CLI contract and
  both harness seams all work.
- **No G3.** Nothing verified working at iteration 7 or 8 is broken now. I re-checked every one of
  the six carried-in non-regression items by execution.
- **No G4.** Every rejected attack left the tree byte-identical. I verified byte-identity explicitly
  after each of the forgeries above, not just the success/failure code.
- **No G5.** The evidence the worker offers reproduces. Its 13-mutation set has zero survivors when
  I re-derive it independently, its gate commands reproduce, and where its counts differ from mine
  the worker had already disclosed why (later additions), and my counts are strictly higher.

N-901 is the one finding I weighed seriously as blocking, and the reasoning for keeping it
non-blocking is written out inside it rather than summarised, so the Coordinator can overrule me on
the record.

---

## Non-Blocking Findings

### N-901 — The `response_id` content re-derivation is load-bearing against a cross-item authority transfer and no test kills it

- **ID:** N-901
- **Quality Attribute:** decision authority integrity / mandatory test gate (mutation sensitivity)
- **Severity:** HIGH
- **Blocking:** NO — reasoning below, written out so the Coordinator can overrule me
- **Responsible Phase:** implementation (follow-up), not this gate
- **Location:** `scripts/clarification_protocol.py:631-632` (`_validate_response_record`, the
  `expected = _identifier("response", f"os30-response-v{version}", identity)` check) and the
  byte-identical installed twin; missing coverage in `scripts/test_clarification_protocol.py`.
- **Issue:** Removing only that one check — leaving every other validator, the whole lineage graph,
  and the binding machinery intact — permits a decision answered for bundle item A to be transferred
  to item B by consistent in-place edits of two records. All 39 focused tests and the full 719-test
  combined gate stay green with the check removed.
- **Reason / Evidence:** On a 2-item bundle I answered item A `production`, then edited the
  response's `decision_item_id` to B and the decision's `decision_item_id`, `source_ledger_key` and
  `resolves` to B's. On the **shipped** build this is rejected, which is why nothing defective
  ships:

  ```text
  shipped build:  D_cross_item -> REJECTED[SCHEMA_MALFORMED]  response_id content mismatch
  ```

  On the mutant with only that check removed, the same tree is served:

  ```text
  X14 mutant:     D_cross_item -> ACCEPTED  TRANSFERRED to B: {A: None, B: 'decision_1d55ccd4b…'}
  ```

  And the mutant is not caught anywhere:

  ```text
  X14 vs focused suite (39 tests)   -> SURVIVED (suite green)
  X14 vs COMBINED gate (719 tests)  -> SURVIVED, "OK"
  ```

  This works because both items in a bundle can share option shapes, so `normalized` — and therefore
  `decision_id = H(response_id, normalized)` — is unchanged by the move; the `response_id`
  re-derivation is the only thing that notices the response no longer belongs to the item it claims.
- **Why this is not blocking.** Three reasons, and I hold all three:
  1. **The guard works.** I proved by executing the attack against the real build that the transfer
     is rejected. This is a coverage gap, not a defect — nothing broken ships, and G2 does not fire.
  2. **It is not a regression.** The check and its lack of coverage both predate this round; the
     v1 branch of the identity is new but the check itself is not, and my iteration-8 matrix did not
     probe it either. G3 does not fire.
  3. **No prior Required Action named it.** R7-004 enumerated the *decision* record's non-re-deriving
     ID and the worker delivered it; R8-001 enumerated five cases and the worker killed all five.
     Neither I nor anyone else in this run named the *response* analogue until this review. Failing
     the final attempt on a standard announced for the first time in the verdict that ends the run
     would be exactly the false FAIL my dispatch warns against, and G5 is about evidence for the
     claims under review — all of which reproduce.

  What would change my mind: evidence that a writer other than the protocol itself can reach the
  artifact tree in normal operation, which would move this from "an unguarded regression risk" toward
  a live exposure. Nothing in this repository or in any release ships an OS-30 artifact tree today.
- **Required Action:** In a follow-up round, add a test asserting that a response whose
  `response_id` does not re-derive from `[request_id, decision_item_id, submission_id]` (v2) or
  `[request_id, submission_id]` (v1) fails closed, together with the end-to-end cross-item transfer
  above asserting that item B's head stays `None` and that no byte changes. Record it in the
  `TEST.md` mutation matrix alongside M1–M13.

### N-902 — Several §9 replay and admission guards are individually removable with the suite green, though each is redundantly covered

- **ID:** N-902
- **Quality Attribute:** mandatory test gate (mutation sensitivity) / depth of coverage
- **Severity:** MEDIUM
- **Blocking:** NO
- **Responsible Phase:** implementation (follow-up)
- **Location:** `scripts/clarification_protocol.py:951-980` (multi-root, unreachability and replay
  head/anchor bypass guards), `:942-946` (empty-`D` competing-marker guard), `:936-938`
  (cancellation linkage), `:625-626` (v1 bundled-response rejection), `:653-654` (binding schema),
  `:1016-1021` (whole-tree response validation in `show`), `:880-881` (`event_id` re-derivation),
  `:928-929` (lineage response-item cross-check), `:676` (cancel-forbids-selector guard).
- **Issue:** Each of these can be removed individually with all 39 focused tests green.
- **Reason / Evidence:** I did not report these as gaps on the strength of a green suite, because a
  green suite after a mutation can mean either "no test notices" or "the mutant is equivalent". I
  ran five real attacks against **each** mutant build. In every one of these thirteen cases the tree
  still fails closed, because a second guard catches the same shape:

  ```text
  X1  multiple roots accepted        -> N-802 forgery still REJECTED[ORPHAN_DECISION] (unreachability catches it)
  X2  unreachability check removed   -> still REJECTED[ORPHAN_DECISION] (multi-root catches it)
  X4  replay head-bypass removed     -> head-bypass attack still REJECTED[LINEAGE_FORK] (graph fork catches it)
  X5  post-cancel read-side removed  -> still fails closed, as LINEAGE_FORK instead of LINEAGE_INVALID
  X7, X9, X11, X12, X16, X18, X19, X20 -> no behavioural difference on any of the five attacks
  ```

  The one that is worth naming precisely is X5: removing the read-side post-cancellation guard does
  not reverse an abandonment, but it changes the reported code from the `LINEAGE_INVALID` the design
  names in §9 step 5 to `LINEAGE_FORK`. Defense in depth is a virtue, and I am not asking for it to
  be removed; I am recording that the suite would not tell a future maintainer which guard they
  broke.
- **Required Action:** In a follow-up round, add per-guard tests asserting the **named code** the
  design specifies for each condition — `ORPHAN_DECISION` for a second root and for an unreachable
  decision, `LINEAGE_FORK` for each of the six fork conditions §9 enumerates, `LINEAGE_INVALID` for
  a post-cancellation decision — so that each guard is individually pinned to its own contract rather
  than collectively to "something rejected this". No production change is required.

### N-903 — Shipped `SKILL.md` now contradicts itself about whether OS-30 is implemented

- **ID:** N-903
- **Quality Attribute:** documentation consistency (user-facing)
- **Severity:** MEDIUM
- **Blocking:** NO
- **Responsible Phase:** implementation (follow-up)
- **Location:** `orca-worker-reviewer-orchestration/SKILL.md:369-370`, and the section heading and
  lead paragraph at `:2273-2276`, against the new `## Structured Human Clarification (OS-30)` section
  at `:2353` of the same file.
- **Issue:** Line 370 still reads "질문을 구성하는 것(OS-30), 응답을 기다렸다 재개하는 것(OS-31)은 이
  Skill에 아직 구현되어 있지 않다" — OS-30 is *not yet implemented in this Skill*. The same file now
  ships an executable `tools/clarification_protocol.py`, four documented CLI forms, and the
  `OS30_EXECUTABLE_ARTIFACT_STORE = orchestration_only` anchor. Section `#### Decision gate
  limitations (OS-30 / OS-31 부재의 귀결)` and its "그 이후는 구현되어 있지 않다" lead are stale in the
  same way, although its individual limitation lines L1 and L2 remain accurate about resume and UX.
- **Reason / Evidence:** Both statements are in the same shipped file and cannot both be true. The
  equivalent line in `orca-worker-reviewer-loop/SKILL.md:364-365` is **correct** and should stay:
  that Skill genuinely has no artifact store, which is exactly what its
  `OS30_EXECUTABLE_ARTIFACT_STORE = unavailable_in_direct_loop` anchor pins. This is not a G1
  violation: DESIGN implementation step 6 and the documentation clause specify what must be *added*
  to both `SKILL.md` files, and that was done; neither names this removal. It is a real
  correctness problem in prose a user reads, which is why I record it rather than pass over it.
- **Required Action:** In a follow-up round, narrow line 370 to OS-31 only in the orchestration
  Skill, and re-title/re-lead the limitations section so it describes the OS-31 boundary rather than
  the absence of OS-30. Consider adding a validator anchor for the corrected phrasing, since this is
  exactly the class of drift the new `validate_os30_contract` exists to catch.

### N-904 — DESIGN step 5's installed-tool AST import invariant lives in a different file than the step names

- **ID:** N-904
- **Quality Attribute:** explicit requirement (DESIGN implementation step 5) / placement
- **Severity:** LOW
- **Blocking:** NO
- **Responsible Phase:** implementation (follow-up) or design (record the actual placement)
- **Location:** `scripts/test_os29_decision_gate.py` (byte-identical to `main`; its
  `imported_names` walker covers `decision_gate.py` and `run_logging.py` only) versus
  `scripts/test_release_package.py:76-87`
  (`test_os30_installed_tool_is_byte_identical_and_self_contained`).
- **Issue:** Step 5 says "Extend `scripts/test_os29_decision_gate.py` with unchanged-schema/
  reserved-field locks and the installed-tool AST import invariant", and elaborates it in terms of
  the primary `from scripts.run_logging import …` and the fallback `from run_logging import …` —
  which is exactly `clarification_protocol.py`'s import shape at lines 23-26. That file has a zero
  diff against `main`.
- **Reason / Evidence:** I checked whether the *guarantee* exists elsewhere before deciding, because
  the parallel to R8-003 — where I made an unmodified `validate_skills.py` blocking — is close and I
  did not want to resolve it by assertion. The schema and reserved-field locks step 5 names already
  exist in that file from OS-29 and are untouched
  (`test_the_gate_adds_no_round_kind_and_no_run_status`,
  `test_the_worker_and_reviewer_value_sets_are_unchanged`,
  `test_adding_the_gate_field_left_the_two_contracts_identical`). For the import invariant I supplied
  step 5's own positive control myself — I added `import scripts.decision_gate` to both copies of the
  protocol and re-ran the gate:

  ```text
  sanity: repo import OK (so the mutant is not trivially broken in-repo)
  FAIL: test_os30_installed_tool_is_byte_identical_and_self_contained
        AssertionError: 0 != 1 : ModuleNotFoundError from the installed copy
  ```

  It is caught, and caught under `PYTHONPATH=.` — the subprocess runs with `cwd=skill`, so the
  inherited relative path does not reach the repository. I confirmed the same property directly by
  copying the Skill outside the repo and running it with `env -u PYTHONPATH`: the installed tool runs
  standalone against its sibling `tools/run_logging.py`. So the difference from R8-003 is material:
  there the guarantee existed nowhere; here it exists, is stronger than an AST walk because it is a
  real runtime import, and has a working positive control. Only the file placement deviates.
- **Required Action:** Either add the AST invariant for `clarification_protocol.py` to
  `scripts/test_os29_decision_gate.py` as step 5 words it, or record in DESIGN that the invariant is
  discharged by `test_release_package.py`'s runtime portability test. Prefer the second if the
  runtime test is considered the stronger of the two; do not add both without saying which is
  authoritative.

### N-905 — The validator does not anchor the `respond` example's `--decision-item-id` rule

- **ID:** N-905
- **Quality Attribute:** drift protection
- **Severity:** LOW
- **Blocking:** NO
- **Responsible Phase:** implementation (follow-up)
- **Location:** `scripts/validate_skills.py:109-119` (`OS30_SHARED_ANCHORS` and friends);
  `orca-worker-reviewer-orchestration/SKILL.md` respond examples.
- **Issue:** My iteration-8 R8-003 Required Action listed, "at minimum", three things to anchor: the
  answer-mode `--decision-item-id`, its absence from `--cancel`, and the schema statement. The third
  is anchored and fires; the first two are not.
- **Reason / Evidence:** The examples themselves are **correct** today — I executed both forms and
  both work, and there is exactly one executable `respond` example in the repository. But R7-005
  happened precisely because that example drifted from the CLI contract for a whole round with the
  validator green. DESIGN implementation step 6 requires "explicit shared OS-30 semantic anchors …
  with deletion/drift/false-feature-parity cases", all of which exist and all of which fire, and it
  does not name the respond-example rule specifically — so this is a gap against my own elaboration,
  not against the baseline, which is why it is LOW and not blocking.
- **Required Action:** In a follow-up round, add a validator check that every executable `respond`
  answer-mode example carries `--decision-item-id` and that the `--cancel` example does not, with a
  drift case in `scripts/test_validate_skills.py`.

---

## Test Review

Every gate command in `IMPLEMENTATION.md` reproduces. Where my counts differ from the worker's, they
are **higher**, and the worker had already disclosed the reason (its runs 2 and 3 predate the last
three validator tests).

| Gate command | Worker reported | My result |
| --- | --- | --- |
| `python3 -m unittest scripts.test_clarification_protocol` | 39 PASS | **39 PASS**, 0.547s |
| Combined six-module gate | 716 PASS | **719 PASS**, 84.8s |
| `discover -s scripts -p 'test_*.py'` | 1698 PASS, 6 skipped | **1701 PASS, 6 skipped**, 330.5s |
| `python3 scripts/validate_skills.py` | 714 checks | **714 checks PASS** |
| Four targeted OS-30 validator regressions | 4 PASS | **4 PASS**, and load-bearing |
| `cmp` source vs installed | PASS | **byte-identical** |
| `python3 -m compileall -q` | PASS | **PASS** |
| `git diff --check` | PASS | **clean** |
| `python3 scripts/verify_package.py` | (not listed) | **PASSED, 195 source files** |

**Test quality.** The tests added this round are real coverage, not decoration. I confirmed each of
the following is load-bearing by reverting the thing it tests and watching it go red, rather than by
reading it:

- `test_unlinked_second_decision_is_orphan_and_read_is_non_mutating` and
  `test_wrong_linkage_and_conflicting_fork_fail_with_named_codes` pin the N-802 graph. `X3`
  (conflicting successor/predecessor) and `X8` (supersession event-to-response linkage) both die.
- `test_zero_answer_request_cancel_is_irreversible_for_bundle_sizes` pins D6-001 across all three
  bundle sizes. `X6` (write-side irreversibility) dies with three failures.
- `test_historical_v1_reads_without_binding_and_without_rewrite` and
  `test_disjoint_v1_and_v2_lineages_coexist_but_cross_generation_fails` pin R8-002. `X10`
  (generation match) dies.
- `test_decision_validator_load_bearing_checks_each_reject` kills M5 and M6 — the two that survived
  at iteration 8.
- `test_response_binding_identity_uniqueness_and_raw_recheck_are_load_bearing` and
  `test_binding_content_address_changes_when_bound_digest_changes` kill M11, M12, M13 and `X13`.
- The four `test_os30_*` validator regressions all fail when `validate_os30_contract` is
  unregistered.

**Test gaps.** N-901 and N-902, above. The honest headline is that the worker's enumerated
13-mutation set covers everything this round set out to fix, and my twenty-mutation extension found
exactly one check whose removal produces a real forgery — a check that predates this round and that
no Required Action in this run had named.

**Fixtures.** `scripts/fixtures/clarification_protocol/` carries `valid/needs_input_request.json`
and `invalid/{oversized_bundle,recommended_default}.json`, exercised by
`test_published_fixture_files_are_exercised` and
`test_persisted_request_negative_fixture_matrix_fails_closed_without_side_effects`. DESIGN's testing
strategy asks for a broader invalid-fixture matrix ("every missing/extra field, wrong enum, wrong
version, bool-as-int, malformed timestamp/ID/path, source/run mismatch, unsorted/duplicate
membership, and oversized value"); those conditions are covered in code by the closed validators and
their tests rather than as files on disk, which `REVIEW_TEST_iteration2.md` already adjudicated as
acceptable for this run. I am not reopening it.

---

## Final Decision

**RESULT: PASS. REVIEW_VERDICT: PASS WITH NOTES. DECISION_GATE_STATE: CLEAR.**

The corrected DESIGN is implemented as written. The four carried-in findings — N-802, R8-001,
R8-002 and R8-003 — are each closed, and I closed each one by running the same attack or the same
mutation I used to raise it, not by reading the worker's report. The forgery that hijacked the
effective head at iteration 8 now fails `ORPHAN_DECISION` with the tree byte-identical; the historical
v1 read path I failed the last round for breaking works again with its bytes untouched; the two
mutation cases I named by name two rounds running are dead, along with all three of the binding's
internal checks; and the validator anchors I said did not exist now exist, fire on every deletion,
and are value-pinned so that simultaneous drift in both Skills is caught. D6-001's irreversible
abandonment holds on both the read and the write side at all three bundle sizes, and its asymmetry —
a cancelled item that had an answer can be redecided through its anchor, one that never had an answer
cannot — is exactly right. Nothing regressed.

I extended the mutation set well past what the worker enumerated, because this dispatch asked me to
test whether that set was a convenient subset. It was not: all thirteen die, and of my twenty
additions, six die outright and thirteen of the fourteen survivors are provably redundant guards that
still fail the attack closed. One is a genuine gap, N-901, and I have recorded it at HIGH severity
with the full argument for why it does not fail this gate — the guard is present and effective in the
shipped code, the coverage state is unchanged rather than regressed, and no Required Action in this
run named the case before now. If the Coordinator judges that a load-bearing authority check with no
mutation coverage must fail the gate regardless of whether it was previously named, that judgement is
available on the record above and I will not have hidden the evidence behind a PASS.

I did not find the corrected DESIGN unimplementable, and I raise no blocking finding against the
baseline. The gate state is CLEAR: every open item in this review is a determinate follow-up derived
from the run's own approved design, and none of them is a choice that belongs to the user.

The remaining OS-30 work is unchanged and out of my scope: open the PR on the named branch without
merging, without publishing a release, and without touching Jira status.
