# Review Result

RESULT: PASS
REVIEW_VERDICT: PASS WITH NOTES

## Summary

F-401, F-402, and F-403 are closed at the code level. I reviewed commit `c06f8b5`, the complete
`IMPLEMENTATION.md`, the approved DESIGN D-6.8/D-6.9 contract, and the production source rather
than accepting the Worker summary. Independent reproductions confirmed bounded non-regular-file
handling, real seatbelt writes with realpath-consistent clauses, production probe wiring, the
single-open buffered seed contract, dual seeded/observed identities, and a successful real seeded
`isolate()` capture.

## Blocking Findings

None.

## Non-Blocking Findings

ID: N-401
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: `scripts/test_run_logging.py` (`RetainedReportWhitespaceExemptionTests`) and retained review artifacts named in the failure output
Issue: The repository-wide unit command is not fully green: 2 of 1167 tests fail.
Reason / Evidence: Both failures are the already-recorded whitespace-range failures over committed review artifacts outside this implementation delta. The same two failures were documented before this iteration; all 36 targeted closure tests passed, `validate_skills.py` passed 463 checks, and `verify_package.py` passed 109 source files.
Required Action: Optional follow-up in the owning artifact/history scope; no implementation-gate action for F-401/F-402/F-403.

## Test Review

- Targeted command: `PYTHONPATH=.:scripts python3 -m unittest -v` over SeedPlacement,
  SeedSourceRefusal, SeedDestinationRefusal, SeedSubstitutionRace, SeedAttestation, AgentPath,
  WritableSetSpelling, NonRegularScan, ProbeSource, and ProbeFailClosed test classes: **36 passed**.
- Full command: `python3 -m unittest discover -s scripts -p 'test_*.py'`: **1167 run, 2 failed,
  6 skipped**. The two failures are N-401 and do not exercise modified production behavior.
- `python3 scripts/validate_skills.py`: **PASS, 463 checks**.
- `python3 scripts/verify_package.py`: **PASS, 109 source files**.
- `git diff --check`: **PASS** for the working tree.

The T-10 tests are load-bearing. Independently, outside their test bodies, I called
`read_seed_sources()`, replaced the source before `place_seed_sources()`, and observed that the
destination and `seeded_sha256` still represented the original buffer (T-10.13). I then rewrote the
session destination, ran `inventory_session_home()` plus `attest_seeds()`, and observed the original
`seeded_sha256`, the new `observed_sha256`, `state == "modified"`, and
`seeded_modified == 1`; assigning to the seeded record raised `FrozenInstanceError` (T-10.18/19).

## Evidence Checked

- **F-401:** An independent scan root containing one regular file and one FIFO completed in
  approximately 0.002 seconds with `files=2`, `non_regular=1`, `content_scanned=1`, and no hits.
  Source inspection confirms the `S_ISREG(entry.lstat().st_mode)` gate precedes passes B, C, and D.
- **F-402:** I built a fresh session and real seatbelt profile, then ran the real wrapped command
  to write `payload` into session HOME. It returned `rc=0`, created the file, and the session,
  readable-set, writable-set, TMPDIR, HOME, and profile paths all used the resolved
  `/private/var/...` spelling.
- **F-403 probe wiring:** `isolate()` passes `agent_command or None` and the admitted `agent_path`
  to `preflight_probe()`. When `terminal` is present it passes the real `session`, `terminal`,
  `orca`, and `agent_path` to `orca_check_probe()` and fails closed on nonzero status. The CLI
  threads `--agent-command`, `--agent-path`, `--terminal`, and `--seed` into this production path.
- **Seed contract:** `SeedSource` and `SeededRecord` are frozen dataclasses. Phase 1 opens each
  component with no-follow descriptor operations, adds `O_NONBLOCK` so FIFO refusal is bounded,
  applies S-1 through S-8 to the one descriptor/buffer, and retains no usable source pathname.
  Phase 2 receives bytes, writes with `O_CREAT|O_EXCL|O_NOFOLLOW`, and emits the seeded identity.
  Attestation obtains observed identity from the inventory's single read. `shutil.copyfile()` does
  not occur in executable seed code, and `sha256_path()` is not called by the seed path.
- **Independent end-to-end run:** `final_review_eval.py isolate` with a synthetic external seed,
  `--agent-command "/bin/echo REVIEWER-E2E-OK"`, and `--enforcement seatbelt` returned successfully.
  `/private/tmp/orca-reviewer-iteration2-e2e/result.json` points to a retained, valid schema-1.1
  attestation. It records S1/S2/S3 PASS, NEG-0 through NEG-8 PASS, the seed at mode `0600` with
  matching seeded/observed 41-byte digests and `state: unmodified`, and a complete 35-file HOME
  inventory. The retained preflight log contains the real agent command and `rc=0`.

## Final Decision

PASS WITH NOTES maps to `RESULT: PASS`. The three prior blocking findings are resolved:
F-401 terminates without opening non-regular entries, F-402 permits real sandboxed writes using one
resolved path spelling, and F-403's production probes plus the complete D-6.8/D-6.9 seed mechanism
are implemented and independently exercised through a successful real `isolate()` run. N-401 is
pre-existing, unrelated to the implementation delta, and non-blocking under the supplied minimal
general gate.
