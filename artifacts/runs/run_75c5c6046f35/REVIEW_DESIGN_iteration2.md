# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The iteration-2 design gives F-403 a concrete operator interface and places seeded HOME content
before both the admission scan and NEG-5, but it does not yet make the source validation and copy a
single attested operation. The validate-all-then-`shutil.copyfile()` sequence has a source TOCTOU
gap that can bypass the source-path/non-regular-file refusal rules, and the proposed schema replaces
rather than preserves the copied digest when the pre-flight modifies a seed. Those are blocking
security and attestation defects in the exact claims this review must gate.

## Blocking Findings

### F-001 — source validation and copying are separated by a TOCTOU gap

ID: F-001  
Quality Attribute: G4  
Severity: MAJOR  
Blocking: YES  
Location: `DESIGN.md:3127-3153` (D-6.2), especially the validate-all-before-copy contract and D-5; `DESIGN.md:3404-3412` (T-10.1 through T-10.9)  
Issue: The design validates each source path with `lstat`, path-containment checks, digest/content
checks, mode checks, and UTF-8 decoding, then later reopens the pathname through
`shutil.copyfile()`. It specifies no stable file descriptor, no no-follow open, and no identity
check tying the bytes copied to the inode and bytes validated.  
Reason / Evidence: D-6.2 explicitly requires all pairs to be fully validated before any copy, while
D-5 specifies a later pathname-based `shutil.copyfile()`. Between those operations a source can be
replaced, including by a symlink to a file under the repository/fixture/key-bearing roots. That
bypasses S-1 and S-3 at the copy boundary. The later admission scan catches known answer-key digest
or vocabulary, but it is not equivalent to S-3's unconditional refusal of *all* fixture and
adjudication material; content from those roots that does not match the current key vocabulary can
reach the session. This contradicts the required closed seed list and the claim that the interface
does not open a contamination/exfiltration path. No T-10 test exercises source replacement between
validation and copy.  
Required Action: Specify an atomic source-read contract: open each source once with no-follow
semantics, validate `fstat` identity/type/size/mode and the bytes read from that descriptor, retain
those validated bytes or descriptors through the all-pairs decision, and write exactly those bytes
to exclusive destinations. Add a deterministic race/substitution test proving a replaced path or
symlink cannot change the copied bytes or bypass S-1/S-3/S-4.

### F-002 — modified seeds lose the attested identity of what was actually copied

ID: F-002  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `DESIGN.md:3188-3193` (`assert_seeds_present`), `DESIGN.md:3195-3237` (D-6.4 schema), `DESIGN.md:3413` (T-10.10)  
Issue: For a seed modified by the real-agent pre-flight, the design says `seeded[].sha256` is
replaced with the new digest and marks `state: "modified"`. The schema contains only one digest and
size, so it cannot record both the bytes copied by `seed_session_home()` and the bytes present at
attestation time.  
Reason / Evidence: The review requirement is that `ISOLATION.json` honestly record exactly what was
seeded so B6 cannot silently drift. Lines 3190-3193 explicitly choose the observed new digest and
say the attestation records what is there, not what was put there. The inventory already records
what is there; without an immutable copied digest/size, a `modified` row proves neither which
credential bytes passed S-4 nor which bytes were initially supplied. T-10.10 only says seeded
digests “match” and does not pin initial versus observed values or a mutation case.  
Required Action: Give each seed record distinct immutable copied fields (for example
`seeded_sha256`/`seeded_bytes`) and observed-at-attestation fields (or link the latter explicitly to
the inventory), define `state` from their comparison, and add a test in which pre-flight mutation
proves both identities survive in `ISOLATION.json`.

## Non-Blocking Findings

None.

## Test Review

- T-10.1 through T-10.12 cover the ordinary refusal rules, scan inclusion, inventory, and PATH
  admission, but they omit the two load-bearing transitions above: pathname substitution between
  validation and copy, and a seed modified between copy and attestation.
- The documented measured controls establish that a normal `auth.json` authenticates and that
  obvious key vocabulary is caught by the existing scanner. They do not validate the missing
  source-identity or dual-digest contracts.
- No implementation test execution is required for this DESIGN gate; the failures arise directly
  from the proposed interfaces and data flow.

## Evidence Checked

- Read the complete `DESIGN iteration 2` section in `artifacts/runs/run_75c5c6046f35/DESIGN.md`,
  including D-6.0 through D-6.7, data flow, compatibility, implementation steps, T-10.1 through
  T-10.12, and risks/open issues.
- Compared the delta with F-403 in `TEST.md` and its independent confirmation in
  `REVIEW_TEST_iteration1.md`.
- Inspected commit `c476400` and confirmed the iteration changes are documentary; unrelated dirty
  worktree files were not modified.
- Confirmed the design preserves the previously approved placement of HOME in the Class USR
  readable set and runs the same A/B/C/D/S machinery at admission and NEG-5. The findings do not
  reopen F-401, F-402, D-H.2, RK-7, mandatory pass B, or D-I.

## Final Decision

FAIL. The placement and scan path are implementable, but IMPLEMENTATION cannot safely build the
current source-copy contract or truthfully satisfy B6 until F-001 and F-002 are resolved in DESIGN.
