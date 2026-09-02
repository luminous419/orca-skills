# Review Result

RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES
DECISION_GATE_STATE: CLEAR

## Summary

OS-40 Final Adversarial Review, attempt 3. Fresh session; no inherited verdict context. I
re-derived every gate result rather than trusting the phase Reviewer history, the Coordinator's
baseline numbers, or the two prior Final Review attempts.

Verdict: **0 blocking findings.** Acceptance Criteria 1–16 and the user's 11-item verification
list are met, and I confirmed each of them by direct execution rather than by reading the phase
reports. The engine is 875 lines across 11 core modules, the LangGraph `StateGraph` in `graph.py`
is the only executable transition definition, `routing.py` holds the pure gates behind its single
conditional edge, and no separate loop or parallel transition engine was created.

The most substantive thing I did was construct my own 20-mutation adversarial sweep against the
new suite, independent of the six axes TEST.md reports. **14 of 20 mutations were detected.** All
six survivors are coverage gaps in guards that I then verified, by direct execution, to be
functionally correct — none is a production-code defect. That distinction is what separates this
attempt from attempt 1 (F-002 was a coverage gap in guards central to the fail-closed claim) and
attempt 2 (F-001 was a live `IndexError` on a reachable path). Neither shape is present now.

Both blocking findings from the earlier attempts are genuinely fixed, and I reproduced the fixes
rather than accepting the reports:

- attempt 1 F-001 (unknown reviewer verdict fail-open): `phase_gate`, `final_gate` and `route`
  now normalize every value outside `{PASS, FAIL}` to `BLOCK`. Re-opening either gate
  (`return result` / `return verdict`) is caught by 6 and 5 test failures respectively (M6, M6b).
- attempt 2 F-001 (`correction_queue[correction_index]` unguarded): `active_correction_phase()`
  is the single indexed accessor; the package contains zero direct `correction_queue[` indexing.
  Removing its bounds check reproduces the crash as 4 test errors (M5).

I also re-judged the four CF-6 carry-forwards and CF-7 on their own merits and reached the same
non-blocking conclusion as attempt 2, by a different route — reasoning recorded in N-002 and N-007.

One new finding I did not see raised in any prior artifact: a canonical 5-phase run needs ~68
graph steps, well above LangGraph's default `recursion_limit` of 25, and neither `build_graph`
nor any document sets or mentions it (N-001). It is non-blocking because no shipped component
invokes `build_graph`, but it is the highest-priority follow-up.

## Blocking Findings

None.

To be explicit about the two open items this attempt was asked to adjudicate: **CF-7 / N-009 is
not blocking** (grounds in N-002), and **CF-6 N-002 — the `downstream_revalidation_set`
duplication — is not blocking** (grounds in N-007). I reached both conclusions before reading how
attempt 2 had ruled, and my grounds differ from its.

## Non-Blocking Findings

### N-001

ID: N-001
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
Location: `scripts/deterministic_workflow/graph.py:37`, `docs/DETERMINISTIC_WORKFLOW.md`, `README.md:753`
Issue: A canonical five-phase run needs ~68 graph steps, but LangGraph's default
`recursion_limit` is 25. `build_graph(...).compile(...)` sets no default, and no document
mentions the config key. Every test passes `config={"recursion_limit": 300}` explicitly; nothing
shipped does.
Reason / Evidence: Reproduced directly. With the default config the canonical AC-1 scenario dies:
`GraphRecursionError: Recursion limit of 25 reached without hitting a stop condition`. With
`recursion_limit=300` the same scenario returns `COMPLETED` with 68 trace entries. `grep -rn
"recursion_limit" docs/ README.md INSTALL.md orca-worker-reviewer-orchestration/
scripts/deterministic_workflow/` returns nothing. An integrator following README's "use the
runtime-neutral package in `scripts/deterministic_workflow`" hits this on their first run.
Not blocking: `grep -rn build_graph` outside tests and artifacts finds only the two definition
sites — no shipped code path invokes the graph, so nothing delivered is broken. `run_workflow.py`
performs a dependency check only. The failure is also self-describing (LangGraph's own error names
the fix), and OS-37 owns the standalone runner. This is an ergonomics/documentation gap, which the
review policy lists as non-blocking by default.
Required Action: Either give `build_graph` a documented default recursion budget derived from
`len(requested_phases)` and `max_iterations`, or state the required `recursion_limit` in
`docs/DETERMINISTIC_WORKFLOW.md` alongside the invocation example.

### N-002

ID: N-002
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
Location: `scripts/deterministic_workflow/routing.py:37-38` (SKILL §14 mandatory unit-test gate)
Issue: Confirms CF-7 / TEST N-009. Deleting the mandatory unit-test gate from `phase_gate` leaves
all 36 targeted tests passing.
Reason / Evidence: Reproduced as mutation M1 — `Ran 36 tests ... OK` with the guard removed. I
then verified the guard itself is correct rather than assuming it: for `current_phase =
IMPLEMENTATION`, `unit_test_status` of `BLOCKED`, `NOT_APPLICABLE`, `FAIL` and `None` each yield
`phase_gate=BLOCK` / `route=BLOCK`, and only `PASS` proceeds to `PREPARE_PHASE_REVIEWER`. Through
the compiled graph, an IMPLEMENTATION worker reporting `unit_test_status=BLOCKED` terminates
`BLOCKED` / `UNIT_TEST_BLOCKED` after exactly 1 effect.
Not blocking, and my grounds are independent of attempt 2's: the mandatory unit-test gate is a
repository SKILL policy, not an OS-40 requirement. It appears in none of AC 1–16 and in none of the
user's 11 verification items, so it is not a tier-1 explicit requirement for this run. G5 asks
whether the evidence needed to *judge* is absent; it is not — I judged the guard correct by direct
execution in three lines. This is a coverage gap in a correct guard, which is materially different
from attempt 1's F-002, where the untested guards were the ones the fail-closed claim rested on.
Required Action: Add one test asserting `phase_gate`/`route` return `BLOCK` for an
IMPLEMENTATION worker whose `unit_test_status` is not `PASS`. One test method closes it.

### N-003

ID: N-003
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
Location: `scripts/deterministic_workflow/routing.py:69,22-29`, `graph_spec.py:49`, `contracts.py:87`
Issue: Five further guards survive removal with the suite still green. All five are correct code;
all five are untested.
Reason / Evidence: From my 20-mutation sweep, with each guard's correctness then verified by hand:
- **M14, post-terminal route guard** (`routing.py:69`). The most consequential of the five. I
  resumed a state with `terminal_status` set and `pending_role=None` — the exact shape
  `terminal_node` leaves behind. `validate_state` accepts it, because its `POST_TERMINAL_EVENT`
  check only fires when a pending role/intent/event is present, and `terminal_node` clears all
  three. With the guard, `route` returns `COMPLETE`/`ESCALATE`/`BLOCK` matching the status; without
  it, all three return `PREPARE_WORKER` — a post-terminal dispatch. So the route guard is the only
  real defense on that path, and the tested `POST_TERMINAL_EVENT` assertion never reaches it.
  AC 10's terminal clause still has evidence at the state-validator layer (M13 is detected), which
  is why this is a second-layer coverage gap and not a G5 evidence absence.
- **M12, unreachable-node check** (`graph_spec.py:49`). Survives because the existing DEAD-node
  fixture is also caught by the dead-end check. The two checks are genuinely distinct: I built
  `GraphSpec(nodes=... + ("ORPHAN",), edges=... + (("ORPHAN","TERMINAL"),))`, which reaches TERMINAL
  but is unreachable from VALIDATE, and the check correctly raises `unreachable nodes: ['ORPHAN']`.
- **M16, `validate_event` outcome check** (`contracts.py:87`). No adapter under test emits an
  `outcome` other than `SUCCEEDED`.
- **M17, out-of-scope finding rejection** (`routing.py:26`). Verified correct: a finding whose
  `responsible_phase` is outside `requested` raises `OUT_OF_SCOPE_FINAL_REVIEW_FINDING`. The
  `ESCALATE` branch this feeds in `executor.py:118-119` is entirely untested.
- **M18, blocking-flag filter** (`routing.py:27`). Verified correct: a `blocking: False` finding
  yields an empty correction queue. Every test supplies `blocking: True`.
Required Action: Add targeted tests for the post-terminal route guard and the out-of-scope
finding escalation branch first; the other three are lower value.

### N-004

ID: N-004
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
Location: `artifacts/runs/run_0bcf4e7296c9/DESIGN.md:107,119-130,248,319`; `scripts/deterministic_workflow/executor.py:119,171`; `contracts.py:81-95`
Issue: Four places where DESIGN specifies more than the implementation delivers. None changes an
acceptance criterion; together they are the largest documentation-accuracy debt in the change.
Reason / Evidence:
- DESIGN §6 defines `TerminalReason` as `{code, message, phase, finding_ids,
  missing_capabilities}`. `terminal_node:171` writes only `{code, message, phase}`. Concretely,
  `validate_node:30` computes a sorted `missing_capabilities` list — which §1 explicitly requires
  ("enters BLOCKED `ADAPTER_CAPABILITY_MISSING` with sorted missing set") — and `terminal_node`
  then overwrites the record and discards it. The capability test only asserts the `code`, so this
  is invisible to the suite. AC 12 is still met: the block is explicit and correctly coded.
- DESIGN §1 declares closed `WorkerResult`/`ReviewerResult` TypedDicts. `contracts.py` defines
  neither, and `validate_event` checks only `status` / `result` membership, not the key set. Extra
  adapter-supplied keys are stored verbatim. I confirmed the consequence: a worker result carrying
  `terminal_handle` / `credential` lands in `worker_result` inside the checkpoint, and the
  `FORBIDDEN_KEYS` denylist never catches it because `VALIDATE` runs only at graph entry —
  `APPLY_RESULT → ROUTE` never revisits it. `validate_state` on that snapshot does reject it
  (`NON_CHECKPOINTABLE_STATE:state.worker_result.terminal_handle`), so the guard is right; it is
  simply not on the ingress path. Not blocking: neither shipped adapter produces such a result
  (`OrcaAdapter` keeps task/dispatch/terminal handles in its private `_receipts`, exactly as
  DESIGN §7 requires), the 35-field state schema itself carries no runtime object, and OS-40 uses
  MemorySaver only — durable persistence is OS-31.
- DESIGN §6 lists `OUT_OF_ORDER_EVENT` as a BLOCKED terminal reason code, but the four
  out-of-order sites raise `StateError` instead of producing a terminal state. Raising is
  fail-closed (no transition, no effect) and the tests deliberately assert the raise, so behavior
  is safe; the divergence is in form, and all four sites are unreachable through `route`.
- DESIGN §2 calls `TERMINAL` the "sole terminal-field writer (CF-3)". True for `terminal_status`;
  `terminal_reason` is also written by `validate_node`, `validate_settlement_node` and
  `apply_result_node` as a staged reason carrier. The same table's own VALIDATE rows acknowledge
  this, so CF-3's actual question (ROUTE vs TERMINAL) is answered — the word "sole" is imprecise.
Required Action: Either narrow DESIGN §1/§6 to what the engine enforces, or carry
`missing_capabilities`/`finding_ids` through `terminal_node` and enforce the closed result key set
at `VALIDATE_SETTLEMENT`. Pick one direction and make both sides say it.

### N-005

ID: N-005
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: `README.md:753`, `INSTALL.md:240`, `docs/COMPATIBILITY.md:171`, `docs/ROADMAP.md:309`
Issue: In all four files the new OS-40 section was inserted between an existing heading and that
heading's body, orphaning the original text under the new heading.
Reason / Evidence: In README, `## Execution-layer Difference` is now immediately followed by
`## Deterministic workflow engine (OS-40)`, and its body ("The two skills intentionally share
development policy but differ in execution mechanics:") now reads as part of the OS-40 section.
The same pattern appears under `### OS-30 clarification tool` (INSTALL), `### OS-30 compatibility`
(COMPATIBILITY) and `## Maintaining This Roadmap` (ROADMAP). Content is accurate; placement is not.
Required Action: Move each inserted section to after the body of the heading it now splits.

### N-006

ID: N-006
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: `artifacts/runs/run_0bcf4e7296c9/TEST.md` ("Test Scope", "Failures / Findings")
Issue: Stale counts left over from earlier iterations, consistent with carried N-011 / N-015.
Reason / Evidence: "최종 34개 targeted tests" and "graph-dependent 18 tests만 명시적으로 skip"
against measured 36 targeted tests and 20 absent-lane skips; earlier prose also cites 1759 against
the final 1761. The Execution section's numbers are correct and match my re-runs, so the errors are
confined to narrative text.
Required Action: Reconcile the narrative counts with the Execution section before the PR
description quotes them.

### N-007

ID: N-007
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: CF-6 carry-forwards (`scripts/e2e_harness.py:497`, `orca-worker-reviewer-orchestration/SKILL.md`, `scripts/test_deterministic_workflow_{graph,adapters}.py`)
Issue: The four CF-6 items remain open. I re-adjudicated each rather than inheriting the ruling.
Reason / Evidence:
- **CF-6 N-002 (D-calculation duplication).** The requirement is "LangGraph 와 별도로 동일한 전이
  규칙을 수행하는 독자 event loop 나 병렬 transition engine 을 **만들지 않는다**" — a prohibition on
  *creating* one. `scripts/e2e_harness.py` is unmodified in this run (`git status` clean, last
  touched at 7bc228a), is imported only by test modules (`test_e2e_harness`, `test_review_isolation`,
  `test_os29_decision_gate`, `test_clarification_protocol`), and is not shipped in either Skill
  package. It is the pre-existing parity oracle for the prompt-driven Orca path, which OS-40's Out
  of Scope explicitly preserves ("기존 Orca adapter 제거" is out of scope). No parallel engine was
  created. Not blocking.
- **CF-6 N-003 (SKILL prose reduction).** Deferral is not merely acceptable here, it is the safer
  choice: nothing outside the tests calls `build_graph`, so the SKILL prose is still what actually
  drives live Orca runs. Deleting it now would remove the operating control logic while the graph
  is not yet wired in. The 9 added lines correctly assert graph ownership without cutting the live
  path. Not blocking.
- **CF-6 N-004 (validator strength).** The core AST scan checks only `orca`/`subprocess` in import
  statements, not the wider symbol set DESIGN §1 names, and the cycle-guard check is a constant
  comparison. I verified by direct grep that the core is in fact clean of `orca`, `subprocess`,
  `terminal_handle`, `session`, `credential`, `claude` and `codex` — the only matches repository-wide
  are `fake_adapter.py`'s docstring and the `FORBIDDEN_KEYS` pattern itself. Weak validator, correct
  state. Not blocking.
- **CF-6 N-005 (`_langgraph_ok` duplicated).** Cosmetic. The CF-1 import-based form is used in both
  copies and is itself pinned by `test_guard_is_import_based_for_blocked_import`.
Required Action: None required for this gate; carry into follow-up.

### N-008

ID: N-008
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: `orca-worker-reviewer-orchestration/tools/run_workflow.py`, `INSTALL.md:245`, SKILL `workflow-graph-contract` (`"launcher"` key)
Issue: `run_workflow.py` is described as the launcher and named as `"launcher"` in the machine
contract, but it only checks that langgraph 0.2.76 imports and prints a readiness line. It
constructs no graph and runs no workflow.
Reason / Evidence: Read in full — 18 lines, `dependency_version()` plus a `__main__` print. INSTALL
does not actually overclaim (it says the command fails explicitly when LangGraph is absent, which
is exactly what it does), so this is a naming imprecision, related to N-001's missing invocation
surface.
Required Action: Rename to reflect the dependency-probe role, or grow it into the documented
invocation entry point when N-001 is addressed.

## Test Review

I re-ran everything rather than quoting the phase reports. All figures below are my own.

| Check | Result | Against |
| --- | --- | --- |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | `Ran 1761 tests in 329.190s`, `OK (skipped=6)`, exit 0 | CF-2 baseline `1725 / OK (skipped=6)` — +36, identical 6 skips, no new skip/error/failure |
| targeted 3 modules | `Ran 36 tests`, `OK` | matches claim |
| dependency-absent lane (`MetaPathFinder` raising `ImportError` for `langgraph*`) | `Ran 36`, `OK (skipped=20)`, errors=0 failures=0 | CF-1 satisfied; the guard is import-based and correct |
| `python3 scripts/validate_skills.py` | `Skill validation PASSED (727 checks)` | matches |
| `python3 scripts/verify_package.py` | `Package verification PASSED (226 source files)` | matches |
| `python3 scripts/validate_workflow_graph_docs.py` | `PASSED` | AC 14 |
| `git diff --check` | no output, exit 0 | AC 16 |
| source ↔ installed mirror | 11/11 files byte-identical (`cmp`) | AC 16 |
| installed copy runs standalone | copied `tools/` to a scratch dir, `PYTHONPATH=.` → engine returns `COMPLETED` | `ports.py`'s import fallback works in the installed layout |

**Mutation sensitivity — my own 20-mutation sweep, independent of TEST.md's six axes.** Each
mutation was applied to production source, the targeted suite run, then reverted; all six touched
files were SHA-256-verified identical to their pre-sweep state afterwards (`RESTORE HASH MATCH:
True`), and I re-confirmed `git status` is unchanged from session start.

Detected (14): downstream risk gate (1 failure) · T2/correction-queue ordering (1) · settlement
event dedupe (3) · correction-queue bounds guard (4 errors — reproduces attempt 2's F-001) ·
`phase_gate` open verdict (6) · `final_gate` open verdict (5) · `FORBIDDEN_KEYS` emptied (1) ·
`all_phase_passes_current` forced True (1) · `missing_capabilities` emptied (1 error) ·
FakeAdapter idempotency removed (2 errors) · processed-command replay guard (1) ·
`POST_TERMINAL_EVENT` check (1) · settlement binding check (1) · decision-state guard in `route`
(2) · closed-state-field check (1).

Survived (6): M1, M12, M14, M16, M17, M18 — all analysed in N-002 and N-003, all verified
functionally correct by direct execution.

This is the answer to "do the tests detect wrong transitions, duplicate execution and checkpoint
corruption, or do they just restate the implementation?" Every one of those three named categories
is covered by detected mutations: wrong transitions (M2, M3, M6, M6b, M8, M19, M20), duplicate
execution (M4, M10, M11), checkpoint corruption (M7, M13, M20). The suite is genuinely
mutation-sensitive, not tautological.

**AC verification I performed directly, not by reading reports:**

- AC 1 — five-phase happy path reaches `COMPLETED` with 11 effects and all `phase_passes` set.
- AC 3 — both budget domains escalate without further dispatch, with distinct reason codes
  (`MAX_ITERATIONS_REACHED` with the responsible phase vs `FINAL_REVIEW_MAX_ITERATIONS_REACHED`).
- AC 6 — three independent runs of the same scenario produced byte-identical normalized traces,
  terminal status and effect counts.
- AC 8 — resuming a `MemorySaver` thread yields an identical next-node sequence across two
  independent probes (`EXECUTE_INTENT` ×4 both times) with identical effect counts (4, 4).
- AC 12 — capability shortfall blocks with `ADAPTER_CAPABILITY_MISSING` and `effect_count == 0`.
- AC 15 — no OS-28/29/30 module is modified (`decision_policy.py`, `decision_gate.py`,
  `clarification_protocol.py`, `workflow_contract.py`, `run_logging.py`, `orca_runtime_harness.py`,
  `e2e_harness.py` all clean); `artifacts/archive/` and the five unrelated run directories carry
  mtimes of 2026-08-26/09-01, predating this run.

**Scope and security (checklist G/H/I):** the tracked diff is 7 files / 83 insertions / 0
deletions. No destructive operation, no `subprocess`, no `eval`/`exec`, no secret in any new file
(the only `token` matches are `route_token`). No commit, nothing staged, branch unchanged at
7bc228a. The engine is 875 lines with no speculative abstraction — no over-engineering finding.
The `validate_skills.py` extension is conditional so historical partial mutation fixtures stay
valid, and the full suite (including `test_validate_skills`) confirms no coupling regression.

**Decision provenance (checklist J):** 35 ledger entries under
`artifacts/runs/run_0bcf4e7296c9/decision_ledger/`, all `CLEAR`; zero `NEEDS_INPUT` and zero
`CONFLICT`, so no unresolved decision blocks completion. `ORCHESTRATOR_LOG.md` records every
boundary with its gate result and grounds, including the IMPLEMENTATION budget extension
explicitly marked `user-authorized`. No high-impact assumption was adopted without user authority.

## Final Decision

**RESULT: PASS (PASS WITH NOTES).** Zero blocking findings; eight non-blocking findings recorded.

The gate criteria, applied literally: no G1 (every explicit AC and every user verification item is
satisfied, and I verified the load-bearing ones by execution); no G2 (the engine works — happy
path, correction, revalidation, both budget domains, checkpoint resume and adapter parity all run);
no G3 (1725 → 1761 with the identical six-skip allowlist and no new failure); no G4 (no destructive
operation, no secret, no irreversible side effect, no out-of-scope file touched); no G5 (the
evidence needed to judge is present, and I reproduced it independently rather than relying on it).

The findings that remain are coverage gaps in verified-correct guards, documentation-accuracy debt,
and one ergonomics gap. The review policy is explicit that documentation polish, minor duplication
and generalized best practice are not grounds for FAIL, and that a `Quality Attribute: NONE`
finding is always non-blocking. Promoting any of them would be exactly the "generic quality
checklist" escalation this gate is instructed not to perform — and, given TEST is at 5/5 and
IMPLEMENTATION is on a user-authorized extension, it would terminate the run on grounds the policy
does not support.

I want to be equally clear about the converse: I did not soften this verdict to avoid an
escalation. I built an adversarial mutation sweep specifically to find the shape of defect that
failed attempts 1 and 2, ran it against the current tree, and found no production-code defect. Six
mutants survived, and I checked each surviving guard by hand instead of assuming it was fine —
every one behaves correctly. Had any survivor turned out to be a broken guard rather than an
untested one, this would have been a FAIL.

Recommended follow-up priority: N-001 (recursion budget), then N-003's post-terminal route-guard
test, then N-002 (unit-test gate coverage), then N-004 (DESIGN/implementation reconciliation).

```decision-gate
{
  "state": "CLEAR",
  "reason_code": null,
  "open_decision_item": false,
  "grounds": "This review reached its verdict from directly reproduced evidence — full suite, targeted suite, dependency-absent lane, validators, mirror parity and a 20-mutation adversarial sweep — with no finding requiring user authority; the run's decision ledger holds 35 entries, all CLEAR, with no unresolved NEEDS_INPUT or CONFLICT.",
  "scope": "This phase's own conduct at this iteration."
}
```
