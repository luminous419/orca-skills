# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL
DECISION_GATE_STATE: CLEAR

## Summary

The iteration-4 correction materially improves F-002: intended terminal provenance is journalled at
`PLANNED` before Task creation, the terminal handle digest is observed between terminal creation and
`worker-start`, and a fresh adapter is designed to obtain a live candidate through the real
`terminal list` operation and verify it against that digest. The unrecoverable W-C window is also
explicitly refused and reported without claiming AC-1, and the real-adapter fixture no longer
pre-seeds process-local harness state.

F-002 is nevertheless **NOT RESOLVED** because the candidate enumeration is scoped by the literal
selector `"current"`, while `orca terminal list --help` defines `active/current` as selector aliases
and separately offers durable selectors such as `identity:`, `id:` and `path:`. A replacement
Coordinator can have a different current worktree, so the proposed fresh-process lookup can query
the wrong scope, miss the live W-D terminal, and refuse a window the design claims is fully
recoverable. F-001 and F-003 remain **RESOLVED** with no observed regression; PLAN F-001 checkpoint
authority and the stated scope walls remain intact.

## Blocking Findings

### F-002

ID: F-002  
Quality Attribute: G1  
Severity: CRITICAL  
Blocking: YES  
Location: `DESIGN.md` §4.2.1 `PLANNED` row and W-D table; §4.2.1a candidate enumeration; §4.4 pre-checkpoint crash row; §10.2 `recover_handle`; §13.3 regressions 1 and 17  
Issue: Terminal provenance is now persisted before the crash window, but the worktree scope needed to recover the plaintext handle is not persisted as a stable worktree identity.  
Reason / Evidence: The design says `terminal_worktree` is `"current"` today (§4.2.1) and that a successor later runs `terminal list --worktree <row["terminal_worktree"]>`. The current implementation confirms E2 uses `terminal create --worktree current` (`scripts/orca_runtime_harness.py`, `create_fake_terminal`), and the observed version-matched help says `--worktree` accepts durable selectors such as `identity:<identity>`, `id:<repo-id>::<path>` and `path:<path>`, while `active/current` are aliases. Therefore persisting the literal word `current` does not persist the worktree in which E2 created the terminal: it is re-resolved in the successor's context. This directly contradicts §4.2.1a's statement that the recorded selector is “not whatever `current` happens to mean in the successor process.” If the successor is current in another worktree, leg (4) sees zero candidates and reaches `TERMINAL_ORPHAN_POSSIBLE`; W-D is fail-closed but not “closed” or leak-free as claimed. T-08/T-47 script the listing under the same recorded selector and do not require a fresh Coordinator whose `current` resolves elsewhere, so they cannot detect the defect.  
Required Action: Before E1, resolve the creation worktree to a stable, replayable selector returned by Orca (for example a verified `identity:`, `id:` or `path:` selector), persist that value in `PLANNED`, and use that exact stable selector for both E2 and recovery; alternatively classify the affected fresh-process window as an enforced limitation, refuse it, report the residual, and do not claim W-D/AC-1 completion. Update T-08/T-47 so creation occurs with one `current` binding and the fresh recovery object has a different `current` binding, proving recovery uses the persisted stable origin-worktree identity rather than the alias.

## Non-Blocking Findings

None.

## Test Review

- T-48 now tests an honest residual/refusal rather than a fabricated transfer label, and checks
  `ac1_discharged == false`; this preserves the iteration-3 resolution of F-001.
- T-47 explicitly starts with empty `OrcaRuntimeHarness._terminals` and
  `OrcaAdapter._receipts`, obtains the plaintext handle only from a scripted real-shape terminal
  listing, verifies its digest, and refuses contradictory/ambiguous/unreadable listings. This closes
  the earlier FakeAdapter/real-adapter provenance-source gap, but its fixed listing does not exercise
  re-resolution of `current` in a different replacement-Coordinator context.
- T-08 names W-A through W-E and makes W-C an enforced fail-closed limitation. Its positive W-D
  oracle remains incomplete until it proves stable worktree scoping across the process boundary.
- The multi-item atomic-bundle tests for F-003 and the checkpoint-authority tests remain specified
  unchanged; no regression was found in either contract.

## Evidence Checked

- Approved `ANALYSIS.md` and `PLAN.md`.
- Current `DESIGN.md`, especially §§3, 4.2.1, 4.2.1a, 4.2.2, 4.4, 9.2, 10.2, 12, 13.3 and 13.4.
- `scripts/orca_runtime_harness.py`: process-local terminal ledger, `register_terminal`,
  `ledger_terminal`, `create_fake_terminal`, `run_existing_task`, and settlement operations.
- `scripts/deterministic_workflow/orca_adapter.py`: `_receipts`, durable receipt handling and
  `run_existing_task` ordering.
- `runtime_state.py`, `ports.py`, `fake_adapter.py`, `executor.py`, `state.py`, and the relevant
  `scripts/test_*.py` fixture/response shapes.
- Observed `orca terminal list --help`: `--worktree` accepts stable selectors and separately lists
  `active/current`; observed tracked source state is clean at branch `main`, HEAD
  `c279005d0c2c743cbb6111b802efd7ff3797ac35` (untracked run artifacts only).

## Final Decision

FAIL. F-001: **RESOLVED, no regression**. F-002: **NOT RESOLVED**. F-003: **RESOLVED, no
regression**. PLAN F-001 and the scope limits are not regressed. The remaining F-002 defect is an
explicit G1 violation because the design claims fresh-Coordinator W-D recovery and leak-free
settlement without durably binding the lookup to the terminal's actual origin worktree.

```decision-gate
{
  "run": "run_c2166e75bb02",
  "phase": "DESIGN",
  "iteration": 4,
  "state": "CLEAR",
  "reason_code": null,
  "evidence": "Direct review of the approved ANALYSIS/PLAN, the iteration-4 DESIGN, mandated source and test seams, version-matched Orca orchestration guidance, and observed `orca terminal list --help`; the remaining defect is classifiable under G1 without user authority.",
  "assumption": null,
  "open_item": null,
  "responsible_phase": "DESIGN",
  "role": "reviewer",
  "verdict": "FAIL",
  "source_binding": "branch main, HEAD c279005d0c2c743cbb6111b802efd7ff3797ac35, tracked worktree clean; untracked artifacts present",
  "recorded_at": "2026-09-05T10:05:29Z",
  "boundary": "B3",
  "open_decision_item": false,
  "grounds": "No boundary required user authority; the correction can be judged against the explicit fresh-process recovery and terminal-settlement requirements.",
  "scope": "DESIGN iteration-4 correction re-review of F-002, with regression checks for F-001, F-003, PLAN F-001 checkpoint authority, and scope limits.",
  "classification_attempted": true,
  "reversibility": "reversible_in_run",
  "blast_radius": "current_change",
  "monetary_cost": false,
  "security": false,
  "privacy": false,
  "compliance": false,
  "long_term_lock_in": false,
  "impact": "The DESIGN gate remains failed until terminal recovery is bound to a stable origin-worktree selector or the affected window is explicitly refused without claiming completion."
}
```
