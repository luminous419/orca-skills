# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

F-001 is substantively closed. The corrected design removes the root-only `W_OK` admission rule,
removes `/private/var` and `/Library` wholesale, recursively evaluates every IMM root, explicitly
carves the writable data-volume aliases out of `/usr` and `/System`, scans every USR root, replaces
the global metadata grant with a closed traversal set, and adds a real writable-descendant probe
whose unsandboxed and iteration-1 controls demonstrate the leak before the corrected wrapper
denies it. Direct host inspection confirmed the real temp directory is writable beneath
non-root-writable `/private/var`, the named `/usr` firmlinks and `/System/Volumes/Data` boundary
exist, and the corrected admitted-root list accounts for those host paths.

The iteration nevertheless violates the explicit correction boundary: D-I was required to remain
byte-for-byte untouched, but the delta says both that no sentence of D-I changed and that D-I's
limitation sentence is narrowed to new recursive-IMM wording. That is a real D-I contract change,
not merely an explanation of D-G, so the current phase cannot pass until the iteration-2 delta is
made internally and contractually consistent.

## Blocking Findings

ID: F-002  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `artifacts/runs/run_4d1c47c838db/DESIGN.md`, iteration-2 scope declaration
(lines 1317-1320) and iteration-2 Error Handling / Compatibility (lines 1458-1466)  
Issue: The approved correction boundary says D-H and D-I are resolved and must not be reopened,
and this review was specifically required to confirm both sections are byte-for-byte untouched.
The design claims that condition (“no sentence of either changed”) but later explicitly replaces
D-I's limitation sentence with new recursive-immutability wording and calls it “the only
D-I-adjacent change.” Both statements cannot be true, and the latter is a substantive change to
the exact `COMPATIBILITY.md` replacement text owned by D-I.  
Reason / Evidence: The iteration-2 section says at lines 1317-1320 that D-I is not touched and that
no sentence changed. At lines 1458-1466 it supplies a corrected replacement sentence and states
that this is a D-I-adjacent change. The main D-I block at lines 965 onward still contains the
iteration-1 two-boundary wording, so IMPLEMENTATION is left with two competing D-I specifications.
This is an explicit requirement violation and an implementability ambiguity, satisfying G1.  
Required Action: Keep D-I byte-for-byte at its approved iteration-1 text, as required by the task
boundary, and record the narrower recursive-proof limitation only in D-G/`ISOLATION.json`; or, if
changing the compatibility wording is genuinely necessary, obtain an explicit boundary change and
provide one authoritative D-I replacement rather than claiming D-I is untouched.

## Non-Blocking Findings

None.

## Test Review

The new NEG-7 design is load-bearing rather than assumptive. It resolves
`tempfile.gettempdir()` and `~/Library/Caches`, plants both a byte-identical answer key and the real
seeded-defect identities, runs the same probe unsandboxed first, reproduces the leak under the
iteration-1 profile, and then requires the exact corrected wrapper to deny open, stat, existence,
listing, command-line discovery, symlink, and `/System/Volumes/Data` alias access. The stated
unsandboxed/sandboxed contrast is therefore a genuine positive-control/negative-control test.

T-9.9 also directly guards against the original root-only rule by constructing a non-writable
ancestor with a writable descendant and requiring `assert_no_unscanned_descendant()` to reject it.
Together with NEG-8's alias battery and the generated carve-out/profile-list equality check, the
test plan meaningfully covers the corrected F-001 behavior.

## Evidence Checked

- Read the full corrected `DESIGN.md`, including G.3/G.4/G.9, RK-2, and the iteration-2 delta.
- Read `REVIEW_DESIGN_iteration1.md` and the common and DESIGN-specific review policies.
- Independently resolved the host temp directory as
  `/private/var/folders/nz/y8qy3cqx5m5gcm93pn0d945r0000gn/T`; it is writable while
  `/private/var` is not writable at its root.
- Independently inspected every default admitted root and named exclusion on this host:
  `/bin`, `/sbin`, `/private/etc`, `/usr`, `/System`, `/dev`, `/private/var/select`,
  `/Library/Developer/CommandLineTools`, `/usr/local`, `/usr/libexec/cups`, `/usr/share/snmp`,
  `/System/Volumes`, `/private/var`, `/Library`, `/opt/homebrew`, and `/Users`.
- Read the live mount table and `/usr/share/firmlinks`; confirmed the `/System/Volumes/Data` mount
  and the three `/usr` data-volume firmlinks named by the correction.
- Checked the D-H and D-I text and the iteration-2 statements describing their delta.

## Final Decision

FAIL. F-001 itself is closed by the corrected all-root descendant analysis and the new empirical
negative test, but the iteration violates the explicit approved-baseline boundary by changing D-I
while claiming it is byte-for-byte untouched. Resolve F-002 without reopening D-H or the already
approved parts of D-I, then re-review the corrected delta.
