# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

The design preserves the approved two-tier model: the LangGraph checkpoint is the sole execution-state reconstruction input, while the pause record is limited to discovery, fencing, and a checked projection. It also gives concrete state transitions, C1-C4 checks, stale-source revalidation, gate-preserving re-entry, cancel/abandon status changes, parity edits, and broad test traceability. Three implementation-blocking gaps remain in the pause settlement crash contract, terminal-ownership exit condition, and multi-item response transaction; implementing the document as written would not yet prove OS-31's no-orphan/no-ambiguity and exactly-once requirements.

## Blocking Findings

### F-001

ID: F-001  
Quality Attribute: G1  
Severity: CRITICAL  
Blocking: YES  
Location: `DESIGN.md` §4.2 steps 3-4 and final paragraph; §9.2 step 6; §13.3 regressions 9-10  
Issue: The design permits entry into `WAITING_FOR_INPUT` while terminal ownership is still explicitly `unknown` and process liveness is `disputed`.  
Reason / Evidence: OS-31 requires pause to leave no ambiguous terminal ownership and to release ownership safely. The design says `cleanup_authority == "unknown"` is retained-and-reported, forbids `release_terminal`, and makes `DISPATCH_UNACCOUNTED` the only pause refusal after recovery; its proposed T-12 then treats an `unknown`/`disputed` row as a successful recorded pause. Recording the ambiguity does not resolve it, and the otherwise-declared `TERMINAL_OWNERSHIP_UNKNOWN` refusal code is never assigned an enforcement condition. The same contradiction appears in abandon step 6, which calls retained `active_worker` terminals "no ambiguous ownership" without identifying a definite owner or proving a safe handoff.  
Required Action: Define a fail-closed pause/disposition exit invariant for every terminal: ownership must be definitively released, transferred to an identified owner, or proven already exited. Bind `TERMINAL_OWNERSHIP_UNKNOWN` (and disputed liveness where ownership cannot be established) to refusal/recovery, and change the test oracle so merely persisting `unknown` cannot satisfy AC-1.

### F-002

ID: F-002  
Quality Attribute: G1  
Severity: CRITICAL  
Blocking: YES  
Location: `DESIGN.md` §4.2 settlement-ledger idempotency; §4.4 pre-checkpoint crash row; §10.2 `OrcaAdapter.open_dispatches`; §13.3 regression 1  
Issue: The pre-pause-checkpoint crash recovery described by the design cannot reconstruct the dispatch set or terminal ownership data after the Coordinator process dies.  
Reason / Evidence: The PAUSE node performs external settlement before the checkpoint commits, but stores completed settlement rows only in `pause_binding`, so those rows disappear in the specified crash window. The proposed Orca implementation defines `open_dispatches` from the adapter's own `_receipts`, while the current `OrcaAdapter._receipts` is process memory and the durable `FileRuntimeStateStore` receipt deliberately excludes the terminal handle. A fresh Coordinator therefore cannot enumerate the old adapter's receipt set or recover the terminal provenance/handle needed by `account_dispatch` and `release_terminal`. The claim that the harness claim gate makes re-accounting idempotent addresses repeated mutation, not discovery of the lost settlement/ownership set, and the proposed FakeAdapter test would not prove process-restart compatibility with the real adapter.  
Required Action: Specify a durable-before-effect settlement journal or a complete reconstruction algorithm from durable intent receipts plus authoritative runtime queries, including how task ID, dispatch ID, terminal identity/provenance, four-axis outcome, and per-row completion survive process death. Make the pre-commit crash test drop all adapter/harness objects and verify a fresh adapter can enumerate and finish every prior dispatch without leaks.

### F-003

ID: F-003  
Quality Attribute: G5  
Severity: MAJOR  
Blocking: YES  
Location: `DESIGN.md` §6.1 `resume_id`; §6.2 application sequence; §6.3 all-items rule; §6.4 applied set  
Issue: The exactly-once transaction is not defined for an OS-30 request containing two or three decision items.  
Reason / Evidence: Resume is one graph re-entry effect and §6.3 requires an effective decision for every item, but `resume_id` and each applied entry are defined per `decision_item_id`/`decision_id`; the application sequence then refers to one singular `resume_id` and one `record_applied` before the single graph effect. The document does not say whether all per-item entries are written atomically, which entry owns the effect, or how replay reconciles a crash after only a subset is `RECORDED`/`RESUMED`. `RunPauseStatePort.record_applied` accepts one entry, so IMPLEMENTATION would have to invent the atomicity and recovery contract at the exact boundary responsible for duplicate/conflicting response prevention.  
Required Action: Define one atomic bundle-level application identity over the complete sorted item/decision set, or define an atomic batch/CAS transition for all item entries with an unambiguous single effect owner and recovery rules for every partial-write window. Add a multi-item crash/replay/concurrent-resume test that proves one graph re-entry and no partial decision application.

## Non-Blocking Findings

None.

## Test Review

The proposed test matrix covers the ticket's named regressions and correctly separates LangGraph-dependent checkpoint tests from the LangGraph-free pause index/policy tests. However, T-12 currently asserts the wrong terminal-ownership acceptance condition, T-08 does not prove reconstruction by a fresh real-adapter-shaped process, and no test covers atomic application of a multi-item decision bundle; these missing/incorrect oracles correspond directly to F-001 through F-003.

Independent baseline validation at branch `main`, HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35` completed successfully: `python3 scripts/validate_skills.py` reported 732 checks passed, and `python3 -m unittest discover -s scripts -p 'test_*.py'` reported `Ran 2014 tests in 338.045s`, `OK (skipped=6)`.

## Evidence Checked

Reviewed the approved `ANALYSIS.md` and `PLAN.md`, the complete `DESIGN.md`, and the mandated implementation seams in `scripts/deterministic_workflow/`, `clarification_protocol.py`, `run_logging.py`, `orca_runtime_harness.py`, validators, tests, requirements, and `INSTALL.md`. Direct source inspection confirmed that `OrcaAdapter._receipts` and terminal handles are process-local, durable runtime receipts omit terminal handles, and `HumanApprovalPort.show` exposes request/current/effective-decision/item-status data for a runtime-neutral consumer. No production code or artifact under review was modified.

## Final Decision

FAIL with 3 blocking findings. The checkpoint-authority regression from the prior PLAN review is resolved and the design does not bypass phase or final-review gates, but DESIGN must close the three transaction/ownership gaps before IMPLEMENTATION can proceed without inventing safety-critical behavior.

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "DESIGN",
  "iteration": 1,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "Reviewed the complete DESIGN against the approved ANALYSIS and PLAN and directly inspected the mandated repository seams at branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35. Independent validation observed `python3 scripts/validate_skills.py` pass 732 checks and `python3 -m unittest discover -s scripts -p 'test_*.py'` finish with Ran 2014 tests in 338.045s, OK (skipped=6). Three blocking design gaps were identified: unresolved unknown terminal ownership is accepted as paused, the pre-checkpoint crash path loses the process-local dispatch/terminal discovery set, and multi-item response application lacks an atomic exactly-once contract.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "DESIGN",
  "role": "reviewer",
  "verdict": "FAIL",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, tracked worktree clean; untracked artifacts directories present",
  "recorded_at": "2026-09-05T08:59:34Z",
  "boundary": "B3",
  "open_decision_item": false,
  "grounds": "No user-authority boundary was encountered; the verdict follows from explicit OS-31 acceptance criteria, the approved baselines, and directly observed repository contracts.",
  "scope": "DESIGN phase gate review of artifacts/runs/run_c2166e75bb02/DESIGN.md for Jira OS-31; only REVIEW_DESIGN.md was written.",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "Stops implementation until the design makes terminal ownership determinate, makes pre-checkpoint settlement recoverable after full process loss, and specifies an atomic exactly-once contract for multi-item decisions."
}
```
