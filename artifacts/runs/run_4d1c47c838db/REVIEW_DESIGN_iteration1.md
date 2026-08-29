# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The DESIGN substantially closes MAJOR-2: it keeps the authoritative `ORCHESTRATOR_LOG.md`
read-only, sanitizes only the exported representation, records pre- and post-redaction digests and
the policy version, omits unsafe content without falsifying digest identity, and specifies
synthetic credential/path leak coverage over the serialized bundle. It also supplies concrete
replacement wording for MINOR-1 and does not reopen the scorer, detection/search policy,
H-1/H-2/H-4/H-5, fixture contents, VERSION, or LICENSE.

MAJOR-1 is not yet closed. The proposed Seatbelt profile grants read access to whole Class SYS
subtrees while deciding that a subtree is safe solely from `os.access(root, os.W_OK) == False` and
then explicitly excludes those subtrees from the key-material scan. On the actual target host,
`/private/var` is not writable at its root but contains the current user's writable temporary
directory, so a key copy placed there would be readable by the Reviewer and would evade every
readable-set scan; this contradicts both the external requirement and the design's S3 claim.

## Blocking Findings

ID: F-001  
Quality Attribute: G1  
Severity: MAJOR  
Blocking: YES  
Location: `artifacts/runs/run_4d1c47c838db/DESIGN.md`, G.3 Class SYS classification and scan rules
(lines 311-320 and 346-352), G.4 readable-set profile (lines 355-405), G.9 NEG-5 (line 587), and
RK-2 (line 1013)  
Issue: The allowlist admits entire supposedly system-owned trees, notably `/private/var`, based
only on whether the root directory itself is writable. Class SYS roots are then not scanned.
Root non-writability does not imply descendant non-writability: on this host,
`os.access('/private/var', os.W_OK)` is false while the resolved `tempfile.gettempdir()` is
`/private/var/folders/nz/y8qy3cqx5m5gcm93pn0d945r0000gn/T` and is writable by the current user.
Because the generated profile includes `(subpath "/private/var")`, the Reviewer can read and
discover a key/adjudication copy planted in that writable descendant even though G.3 never scans
it. The statement that `$TMPDIR` is not added wholesale is therefore ineffective: its ancestor is
already granted wholesale.  
Reason / Evidence: I directly evaluated the paths on the target Darwin host. The observed values
were `/private/var: W_OK=False` and the current user's real temp directory under
`/private/var/folders/.../T: W_OK=True`. G.3 pass A-D runs only for Class USR, and NEG-5 likewise
re-scans only Class USR. Thus the proposed negative tests can all pass while an ordinary sandboxed
Reviewer reads a seeded-answer-key copy from a readable, user-writable Class SYS descendant. This
violates the explicit execution/filesystem isolation requirement (G1), the design's own
"allowlist the readable set and scan it exhaustively" claim, and creates a security exposure
within the stated non-malicious-reviewer threat model.  
Required Action: Redesign the system allowlist so no broad admitted subtree can contain an
unscanned user-writable descendant. In particular, remove the wholesale `/private/var` grant and
allow only narrowly justified immutable paths plus the exact session paths needed at runtime;
either exhaustively scan every admitted mutable subtree or prove immutability recursively rather
than checking only its root. Add a load-bearing negative test that plants an answer-key-identical
file and seeded-defect identity in the current user's temp/cache descendant, proves the unsandboxed
positive control can read it, and proves the exact Reviewer wrapper cannot stat, list, open, grep,
or otherwise discover it. Apply the same descendant analysis to every Class SYS root before
claiming S3.

## Non-Blocking Findings

None.

## Test Review

The MAJOR-2 test design is adequate for this phase. T-7 poisons real log cells with foreign paths,
home paths, environment secrets, URL credentials, and dispatch capabilities; checks the entire
serialized export; independently recomputes both digests; exercises residue omission; and verifies
the raw local log is byte-identical before and after export. Those tests support the proposed
sanitized-vs-original audit relationship without changing the raw log's lifecycle authority.

The MAJOR-1 test design is not sufficient because NEG-5 inherits the Class SYS exclusion. NEG-0
through NEG-4 prove denial of the known repository/key paths, but none plants a key copy in a
user-writable descendant of a broadly allowed Class SYS root. The missing case is precisely the
counterexample to the claimed allowlist property, so the current test plan cannot independently
verify unreachability.

## Evidence Checked

- Read the complete current delta: `artifacts/runs/run_4d1c47c838db/DESIGN.md`.
- Drilled into the approved baseline's D.5 materialization limitation, D-F export contract,
  redaction/1.1 and P-PATH rules, and B-1 through B-5 procedure in
  `artifacts/runs/run_804e35d29531/DESIGN.md`.
- Read the common and DESIGN-specific review policies.
- Inspected current repository status and the relevant compatibility wording.
- Direct host check: `/private/var` reported non-writable at its root, while the current user's
  real temporary directory beneath `/private/var/folders` reported writable.
- Verified the delta explicitly leaves detection/search policy, scorer semantics,
  H-1/H-2/H-4/H-5, fixture contents, VERSION, and LICENSE untouched.

## Final Decision

FAIL. MAJOR-2 and MINOR-1 are designed concretely and consistently, but MAJOR-1 remains an explicit
requirement violation because the proposed readable-set classification admits an unscanned,
user-writable path through `/private/var`. The DESIGN must narrow or recursively validate the
Class SYS allowlist and add the synthetic writable-descendant negative test before implementation.
