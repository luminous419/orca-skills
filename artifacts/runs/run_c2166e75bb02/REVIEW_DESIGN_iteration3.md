# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

Iteration 3 resolves F-001 by removing the fictitious transfer, enforcing the pause refusal, and
withholding AC-1 when abandon records a residual terminal. F-002 remains unresolved: provenance is
now durable before terminal creation, but the proposed fresh-process recovery persists only a digest
of the terminal handle and then requires the unavailable plaintext handle to re-register and account
the terminal. F-003, PLAN F-001 checkpoint authority, and the ticket's scope limits were not
regressed.

Previous finding status:

- F-001: **RESOLVED** — abandon records an explicit residual, does not synthesize an owner or claim
  AC-1, and T-48 proves both the limitation and the pause-side refusal.
- F-002: **NOT RESOLVED** — write ordering preserves role/origin provenance, but W-D recovery still
  cannot obtain the live terminal handle required by its own algorithm.
- F-003: **RESOLVED (no regression)** — bundle identity, single-effect ownership, and its test
  obligations remain intact.

## Blocking Findings

### F-002

ID: F-002  
Quality Attribute: G1  
Severity: CRITICAL  
Blocking: YES  
Location: `DESIGN.md` §3 (`terminal_digest`); §4.2.1 provenance recovery and W-D; §4.4
pre-checkpoint recovery; §10.2; §13.3 regressions 1 and 17  
Issue: The fresh-process recovery algorithm still requires the actual terminal handle, but the
design deliberately persists only `sha256(handle)` and identifies no authoritative source that can
return the plaintext handle after the creating process dies.  
Reason / Evidence: Iteration 3 correctly writes caller-chosen role/origin provenance in `PLANNED`
before `task-create`, and adds the `terminal_observer` callback before `worker-start`. However, that
callback stores only `terminal_digest`. The design later calls
`harness.register_terminal(handle, ...)` and `harness.account_axes(..., terminal, ...)`; both require
the actual handle, not its digest. Current `OrcaAdapter._durable_receipt` reconstructs `terminal=None`,
and `runtime_state.RECEIPT_KEYS` permits only `task_id`, `dispatch_id`, `external_id`, and
`intent_id`. The design itself correctly establishes that `worker-show` exposes no handle and that
`dispatch-show` supplies only the dispatch. Consequently the phrase “handle from the journal digest
match” has no enumerable candidate set to match against. W-D is therefore not closed: after
`worker-start` but before `ACCOUNTED`, a fresh object may recover task/dispatch identity and stored
provenance yet cannot seed the harness ledger or run the specified accounting operation. T-47's
required empty `_terminals`/`_receipts` fixture cannot execute the asserted positive recovery without
inventing or pre-seeding precisely this missing handle. This violates the required fresh-Coordinator
recovery and orphan/terminal-leak validation evidence (G1).  
Required Action: Either durably persist a recoverable terminal identifier outside checkpointed
`WorkflowState` before the relevant crash window, or use a concrete verified Orca operation that
returns it from task/dispatch identity. If neither is permitted or available, classify W-D alongside
W-C as unrecoverable, fail closed, withhold AC-1, report the residual, and make T-08/T-47 prove that
refusal using a genuinely empty fresh adapter/harness rather than claiming positive recovery.

## Non-Blocking Findings

None.

## Test Review

T-48 is now adequate for F-001: it excludes `transferred` and synthesized `actor:` owners, asserts
`residual` is non-discharging, checks `ac1_discharged == false` and residual enumeration, and pairs
abandon completion with pause refusal. The exhaustive pause property also prevents a residual row
from entering a committed pause.

T-08/T-47 remain insufficient for F-002. Their no-preseed fixture is the right requirement, and the
response-shape guard correctly prevents role/origin invention, but the positive W-D oracle cannot be
implemented from the stated inputs because no input contains the terminal handle. The proposed
ordering assertions prove that provenance and a digest were written before effects; they do not prove
that a fresh process can address the terminal.

No implementation tests were run because this is a design-only correction. The Worker records the
unchanged baseline as 732 skill-validation checks and 2014 unit tests passing with 6 skips; the
decisive evidence here is direct inspection of the specified interfaces and test surfaces.

## Evidence Checked

- Approved `ANALYSIS.md` and `PLAN.md`, plus iteration-2 review findings.
- Iteration-3 `DESIGN.md`, especially §§3, 4.2.1, 4.2.2, 4.4, 9.2, 10.2, 13.3, and 13.4.
- `scripts/deterministic_workflow/orca_adapter.py`: `_receipts`, `_durable_receipt`, receipt writes,
  and `run_existing_task` ordering.
- `scripts/deterministic_workflow/runtime_state.py`: closed receipt fields.
- `scripts/deterministic_workflow/ports.py`, `fake_adapter.py`, `executor.py`, and `state.py`.
- `scripts/orca_runtime_harness.py`: `register_terminal`, `ledger_terminal`, `account_axes`, and
  `run_existing_task`.
- Relevant `scripts/test_*.py` response fixtures and the design's T-08/T-47/T-48 oracles.
- Repository binding: branch `main`, HEAD `c279005d0c2c743cbb6111b802efd7ff3797ac35`, tracked
  worktree clean with untracked artifacts.

## Final Decision

FAIL. F-001 is resolved and F-003 remains resolved, but F-002 remains a blocking G1 violation because
the claimed fresh-process recovery cannot address the terminal without the unpersisted handle. The
next correction should preserve the honest abandon limitation and checkpoint authority while either
supplying a concrete durable handle source or extending the explicit fail-closed limitation and test
oracles to W-D.

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "DESIGN",
  "iteration": 3,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "Direct inspection confirmed F-001's enforced residual/refusal design, but found that F-002's fresh-process recovery persists only sha256(handle) while register_terminal and account_axes require the unavailable plaintext terminal handle.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "DESIGN",
  "role": "reviewer",
  "verdict": "FAIL",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, tracked worktree clean with untracked artifacts",
  "recorded_at": "2026-09-05T09:38:50Z",
  "boundary": "B3",
  "open_decision_item": false,
  "grounds": "No user-authority boundary was encountered; the verdict follows from the explicit OS-31 recovery requirements and directly inspected repository interfaces.",
  "scope": "DESIGN iteration 3 correction re-review of F-001 and F-002, with regression checks for F-003, checkpoint authority, and scope limits",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "Blocks implementation until W-D recovery has a concrete terminal-handle source or is honestly enforced and tested as an unrecoverable residual window."
}
```
