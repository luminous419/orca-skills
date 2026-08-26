# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

Iteration 5 closes the substantive NEG-5 detection gap: the normative pass-set decision makes B
mandatory by default for every admitted IMM root, removes the proposed flag, and adds a real
reformatted/partial-copy regression case distinct from the byte-identical pass-C case. The
symlink-bounding paragraph is also aligned with the Class-USR-only pass-S rule. DESIGN.md is still
not internally consistent enough to implement, however: its earlier normative classification table
and attestation example still say Class IMM is not content-scanned, and its probe-launch paragraph
still says NEG-5 is sandboxed despite G.9's corrected in-process contract.

## Blocking Findings

ID: F-001
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `artifacts/runs/run_4d1c47c838db/DESIGN.md:349-352`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:465-487`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:696-720`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:2488-2500`
Issue: The claimed in-place reconciliation of the Class IMM content-proof contract is incomplete;
normative and emitted-attestation text still says IMM roots are not content-scanned.
Reason / Evidence: G.3's classification table says IMM has `content-scanned? no` because the proof
makes planting impossible and scanning would only inspect an unchanging set. That is the exact
proof-as-substitute rationale iteration 5 says was deleted, and it conflicts with G.1 S3 and
G.3.3's corrected authority that the proof never opens a file and says nothing about pre-existing
content. The concrete `ISOLATION.json.limitations[]` example likewise still emits “not
content-scanned,” even though G.3.3 says the limitation must contain only the privileged-writer
boundary and “nothing broader.” The `readable_set[].scanned: false` field can consistently mean
“not at session-build time,” but the classification table and limitation sentence do not carry
that scope and directly deny the mandatory NEG-5 scan. An implementer cannot follow the document's
claimed single authority without choosing which contradictory normative text to ignore.
Required Action: Correct the G.3 classification table to distinguish session-build-time scanning
from mandatory NEG-5 scanning, and replace the concrete `limitations[]` sentence with the exact
privileged-writer-only limitation promised by G.3.3. Re-scan the full document for every remaining
unscoped “IMM is not content-scanned” statement.

ID: F-002
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `artifacts/runs/run_4d1c47c838db/DESIGN.md:820`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:847-850`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:2235-2247`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:2537-2544`
Issue: DESIGN still gives mutually exclusive execution contracts for NEG-5: in-process versus a
separate sandboxed process.
Reason / Evidence: The corrected G.9 row and D-5.1 explicitly establish that NEG-5 calls
`scan_readable_set()` in-process inside `run_probes()` and derives independence from scanning the
computed admitted set. Seven paragraphs later, the unsuperseded launch rule says “NEG-2 … NEG-5”
all run as separate processes through `wrap_command()`. The iteration-5 data-flow text also calls
the NEG-5 record “from-inside-the-sandbox.” This is not editorial polish: it changes the process
boundary and therefore the implementation and validation mechanism for the safety gate.
Required Action: Make the probe-launch rule and data-flow wording agree with the authoritative G.9
contract: NEG-2 through NEG-4 are sandboxed command probes, while NEG-5 is the in-process scan over
the computed readable set. Remove all remaining claims that NEG-5 runs inside the sandbox unless
the design instead deliberately changes the mechanism and reconciles G.9/D-5.1 accordingly.

## Non-Blocking Findings

None.

## Test Review

- T-8.4d is a real, distinct regression contract: it plants a reformatted whole copy, a rewrapped
  partial excerpt, and a quoted fragment under unrelated names; it requires B hits and proves the
  old A/C/D set produces none. This is materially different from T-8.4b's byte-identical pass-C
  case.
- T-9.5 pins the default contract at the probe record: IMM uses A/B/C/D with `key_material`, USR
  uses A/B/C/D/S with `key_leak`, and neither a parser option nor an `imm_content_scan` switch may
  reintroduce an opt-in path.
- The test strategy is sufficient for the NEG-5 correction itself, but tests cannot resolve the
  contradictory normative execution and attestation contracts above.

## Evidence Checked

- Full `DESIGN.md`, including all five iteration sections, G.1/G.3/G.3.1/G.3.2/G.3.3, G.9,
  D-4.2, D-5.1/D-5.2, component/data-flow changes, compatibility text, tests, and risks.
- `REVIEW_DESIGN_iteration4.md` F-001 and its required action.
- The verbatim OS-22 objective from `orca orchestration task-list --run run_804e35d29531 --json`.
- D-H.2 and RK-7 were treated as the approved baseline and not reopened.

## Final Decision

FAIL. Mandatory default pass B, flag removal, the per-class pass-S rule, and the distinct
reformatted/partial-copy test are sound, so the original detection gap is substantively closed.
The remaining in-place contradictions are blocking because they leave IMPLEMENTATION without one
unambiguous Class IMM attestation and NEG-5 execution contract.
