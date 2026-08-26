# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The TEST Worker correctly reported a blocked baseline rather than claiming a successful §7
capture. Independent review reproduced the mechanism behind F-401 and F-402, confirmed every
wiring and provisioning claim in F-403 from source and the approved DESIGN, and found no
production-code change in the current worktree. The missing B-1′…B-7 execution remains a real
failure of the TEST gate, but the Worker's refusal to consume B-4R dispatch retry budget was
correct because no Final Review Task/Dispatch was ever reached.

## Blocking Findings

### F-401 — confirmed: pass B can block while opening a non-regular entry

ID: F-401  
Quality Attribute: G2  
Severity: MAJOR  
Blocking: YES  
Location: `scripts/review_isolation.py:585-612`; `scripts/review_isolation.py:123`; `scripts/review_isolation.py:499`  
Issue: `scan_readable_set()` pass B reads every non-symlink entry reported in `filenames` without first requiring `stat.S_ISREG`, while `/dev` is in the default IMM candidate set.  
Reason / Evidence: In an independent bounded reproduction, a temporary scan root containing one ordinary file and one FIFO caused the production `scan_readable_set(..., passes=SCAN_PASSES_IMM)` call to remain blocked after two seconds; the child was then killed. The same call over the ordinary file alone returned in about 7 ms and reported one file/content scan. This confirms the Worker's claimed mechanism without repeating the unsafe unbounded `/dev` walk.  
Required Action: IMPLEMENTATION must restrict pass-B content reads to an explicitly supported regular-file policy, add a FIFO regression test, and add a bounded production-entry-point termination test before TEST retries the baseline.

Responsible Phase: implementation

### F-402 — confirmed: generated read and write clauses use different path spellings

ID: F-402  
Quality Attribute: G2  
Severity: MAJOR  
Blocking: YES  
Location: `scripts/review_isolation.py:1862-1864`, `scripts/review_isolation.py:957`, `scripts/review_isolation.py:1066-1119`  
Issue: `compute_readable_set()` admits realpath-resolved session roots, but `isolate()` supplies raw `tempfile` spellings to the writable list. On this host those are `/private/var/...` and `/var/...` respectively.  
Reason / Evidence: An independently generated real profile contained the resolved `(subpath "/private/var/.../review_root")` read clause and the unresolved `(subpath "/var/.../review_root")` write clause. Executing the real `wrap_command()` through Seatbelt and attempting `echo x > write_probe` returned rc=1 with `Operation not permitted`, and no file was created.  
Required Action: IMPLEMENTATION must canonicalize writable roots consistently and add a real generated-profile write probe/regression test.

Responsible Phase: implementation

### F-403 — confirmed: mandatory agent/Orca pre-flight wiring and usable agent state are absent

ID: F-403  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `scripts/review_isolation.py:1880`, `scripts/review_isolation.py:1919-1978`, `scripts/final_review_eval.py:1284-1332`, `artifacts/runs/run_75c5c6046f35/DESIGN.md:1368-1374`  
Issue: Production `isolate()` calls `preflight_probe(session)` without the real agent command; the isolate CLI has no agent-command option; `orca_check_probe()` has no caller; and `build_session()` only creates an empty session HOME, with no pre-scan provisioning contract for required agent state.  
Reason / Evidence: Repository-wide caller search found only the production call `preflight_probe(session)` and the two definitions; there is no call to `orca_check_probe()`. The pre-flight therefore executes only its four hard-coded read-only commands. The parser exposes `--terminal` but no resolved-agent argument. DESIGN O-2 explicitly says session-scoped state changes were not designed there, and later amendments only partly close the shared-state risk by creating a session HOME; they do not define credential/state provisioning into it.  
Required Action: IMPLEMENTATION must wire the actual resolved agent command and Orca check into the fail-closed pre-flight. The credential/state seeding contract must first return to DESIGN because it changes the isolated agent environment and must preserve attestation, key isolation, and secret handling; IMPLEMENTATION can then implement the approved contract.

Responsible Phase: implementation for the missing wiring; DESIGN follow-up for the deferred O-2 provisioning decision.

## Non-Blocking Findings

The Worker's orphan-session teardown note is accurately labeled non-blocking and does not alter the
current gate. No action is required for this TEST correction loop unless separately scoped.

## Test Review

The approved B-1′…B-7 procedure was not completed, so B1…B6 have no passing evidence and the TEST
phase cannot pass. The Worker did not hide that gap, weaken a test, write a replacement baseline,
or supersede the historical baseline. Its reported `validate_skills.py` and package/unit-suite
results are supporting regression evidence only; they do not substitute for the mandatory
isolated capture, and the Worker correctly says so.

B-4R applies after a dispatch-layer failure and requires each retry to use a new Task/Dispatch and
session. Here B-1′ itself could not terminate and B-2′ was never dispatched, so consuming the
dispatch retry counter would not follow the approved procedure. The retry-budget decision is
therefore correct.

## Evidence Checked

- Read `TEST.md` completely, including all three findings, validation results, remaining gaps, and the non-blocking teardown note.
- Read the verbatim OS-22 request from `orca orchestration task-list --run run_804e35d29531 --json`.
- Checked DESIGN's amended B-1′…B-7 procedure, B1…B6 acceptance criteria, O-1/O-2 text, and later O-2 amendments.
- Ran a bounded FIFO/regular-file comparison against the production `scan_readable_set()`.
- Generated a real Seatbelt profile from raw and realpath session spellings and ran a real sandboxed write attempt through `wrap_command()`.
- Searched all Python callers of `preflight_probe()` and `orca_check_probe()` and inspected the isolate CLI parser.
- Checked `git status`, `git diff --name-only`, and production-file diffs. The only tracked worktree modification shown is the run's `ORCHESTRATOR_LOG.md`; no production source or test file is modified. Existing unrelated untracked artifacts were not touched.

## Final Decision

The Worker's BLOCKED result is genuine, accurately described, and correctly scoped. F-401 and
F-402 belong to IMPLEMENTATION; F-403's missing agent/Orca wiring belongs to IMPLEMENTATION, while
the attested credential/state provisioning decision must return to DESIGN as the Worker states.
Because the explicit mandatory post-fix §7 baseline still has no completed validation evidence and
the production isolation mechanism demonstrably does not work, the TEST gate remains `RESULT:
FAIL` with `REVIEW_VERDICT: FAIL`.
