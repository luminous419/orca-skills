# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

The correction fully resolves F-003 and fixes the ordinary pause-side predicate and oracle identified
by F-001. However, F-001 remains unresolved on the explicit abandon/disposition path, and F-002
remains unresolved in the crash window before the terminal-bearing `INTENDED` journal update: the
proposed runtime-query reconstruction assumes terminal provenance that the inspected harness says the
runtime does not retain. The approved PLAN checkpoint-authority rule remains intact and the delta does
not expand the ticket scope.

Previous finding status:

- F-001: **NOT RESOLVED** — pause now fails closed, but abandon converts unknown ownership into a
  nominal actor handoff without a transfer/acceptance mechanism.
- F-002: **NOT RESOLVED** — fresh-process dispatch discovery is specified, but terminal provenance is
  not reconstructible for every required crash window.
- F-003: **RESOLVED** — one atomic bundle identity, one entry, one effect owner, complete recovery
  ladder, and multi-item crash/replay/concurrency coverage are defined.

## Blocking Findings

### F-001

ID: F-001  
Quality Attribute: G1  
Severity: CRITICAL  
Blocking: YES  
Location: `DESIGN.md` §4.2.2; §9.2 step 6; §13.3 regressions 10 and 18  
Issue: The pause exit predicate is now fail-closed, but the abandon/disposition path still permits an
unknown terminal to be called `transferred` merely by writing `actor:<actor_id>` and mentioning it in
a report.  
Reason / Evidence: §4.2.2 correctly binds `TERMINAL_OWNERSHIP_UNKNOWN` and prevents an ordinary pause
when no owner is nameable; regression 10 also correctly makes persisted `unknown` insufficient for
AC-1. But §9.2's abandon-only discharge performs no terminal adoption, registration, capability
transfer, acknowledgement, or other operation that establishes that the actor can control or has
accepted ownership of the terminal. Naming the human who requested abandon and telling them about an
unknown terminal is an audit/reporting action, not the required definite release, proven exit, or safe
ownership transfer. Regression 18 asserts the label rather than a real transfer invariant, recreating
the original “recording ambiguity resolves ambiguity” defect on the disposition path.  
Required Action: Keep the corrected pause predicate, but make abandon fail closed unless the terminal
is released, proven exited, or transferred through a concrete and verifiable ownership/adoption
operation to an identified owner. If the available Orca contract cannot perform such a transfer,
record/report the residual terminal but do not claim AC-1 or a completed safe disposition; update T-48
to prove the transfer mechanism or refusal rather than only the stored owner string.

### F-002

ID: F-002  
Quality Attribute: G1  
Severity: CRITICAL  
Blocking: YES  
Location: `DESIGN.md` §4.2.1 provenance recovery and survival table; §4.4 pre-checkpoint crash row;
§10.2; §13.3 regressions 1 and 17  
Issue: The three-legged algorithm can rediscover Tasks/Dispatches, but it cannot reconstruct terminal
role/origin/ownership for every crash point claimed by the design.  
Reason / Evidence: The terminal-bearing `INTENDED` update occurs only after
`run_existing_task(...)` returns `(attempt, terminal)`. A process may die after Task/Dispatch creation
or while that call is in flight, leaving only a runtime-state receipt or `task-list` row. The proposed
fallback says `dispatch-show` plus `worker-show` yields the terminal handle **and its
role/origin/owner**, then calls `register_terminal`. Direct inspection contradicts that premise:
`OrcaRuntimeHarness.register_terminal` documents that role and origin are evidence “the runtime keeps
neither, so they are recorded here or lost forever,” and `ledger_terminal` returns
`unknown_role`/`unknown` for an unregistered handle. `worker-show` supplies runtime observation and
terminal resource state, but the design identifies no durable or authoritative source for the lost
OS-31 ownership provenance. Consequently the fresh process can discover a row yet must fail F-001's
predicate, so it cannot “finish every prior dispatch without leaks” as the required action and T-08/
T-47 claim. The offline test would only prove the claim if its stub invents role/origin data unavailable
from the real runtime, which leaves the FakeAdapter/real-adapter compatibility gap open.  
Required Action: Persist the terminal provenance before the crash window can open, or define an
authoritative reconstruction source that demonstrably returns each required provenance field in the
supported Orca contract. Revise the fresh-object real-adapter test fixture to mirror actual
`worker-show`/`dispatch-show` response fields and prove recovery without pre-seeded harness terminal
state; otherwise fail closed and acknowledge that the required leak-free pre-checkpoint recovery is
not implemented.

## Non-Blocking Findings

None.

## Test Review

The proposed T-12 oracle now correctly refuses `unknown`/`disputed` ownership with no owner and thus
addresses the pause half of F-001. T-46 is adequate for F-003: it covers a three-item bundle, crashes
on both sides of the graph commit, incomplete answers, conflicting replay, and concurrent resumers,
while asserting one bundle entry and one graph effect. T-08/T-47 and T-48 remain insufficient because
their expected outcomes embed the two unsupported design premises above rather than proving actual
provenance reconstruction or ownership transfer.

No implementation tests were run for this design-only correction. The Worker reports the unchanged
baseline as 732 skill-validation checks and 2014 unit tests passing with 6 skips; the decisive evidence
for this re-review was direct inspection of the design and the specified repository interfaces.

## Evidence Checked

- Approved `ANALYSIS.md` and `PLAN.md`, including checkpoint Tier-1 authority and C1-C4.
- Iteration-1 `REVIEW_DESIGN.md` and the iteration-2 `DESIGN.md` correction sections.
- `scripts/deterministic_workflow/orca_adapter.py`, especially `_receipts`, both
  `_record_receipt` sites, and terminal-handle exclusion.
- `runtime_state.py`, `state.py`, `executor.py`, `ports.py`, `fake_adapter.py`, `lease_keeper.py`,
  `graph.py`, and `launcher.py`.
- `scripts/clarification_protocol.py`, `scripts/run_logging.py`,
  `scripts/orca_runtime_harness.py`, and relevant `scripts/test_*.py` surfaces.
- Repository binding: branch `main`, HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35`, tracked
  worktree clean (untracked artifact directories present).

## Final Decision

FAIL. F-003 is resolved, but F-001 and F-002 still violate the explicit leak-free terminal ownership
and fresh-Coordinator recovery requirements. The next correction should preserve the bundle design and
checkpoint-authority model while replacing nominal provenance/ownership assertions with durable facts
or fail-closed outcomes.

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "DESIGN",
  "iteration": 2,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "Direct inspection found F-003 resolved, while DESIGN sections 4.2.1 and 9.2 rely on provenance reconstruction and ownership transfer not supported by the inspected OrcaRuntimeHarness contracts.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "DESIGN",
  "role": "reviewer",
  "verdict": "FAIL",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, tracked worktree clean with untracked artifacts",
  "recorded_at": "2026-09-05T09:16:00Z",
  "boundary": "B3",
  "open_decision_item": false,
  "grounds": "No user-authority boundary was encountered; the verdict follows from repository evidence and explicit OS-31 acceptance criteria.",
  "scope": "DESIGN iteration 2 correction re-review of F-001, F-002, and F-003",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "Blocks implementation until terminal provenance recovery and disposition ownership are made mechanically valid."
}
```
