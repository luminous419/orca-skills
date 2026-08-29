# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

The D-H.2 correction is sound: `extra == ()` is unsatisfiable for real
`env_secret_pattern` and `url_credential` inputs because their placeholders intentionally
re-match, while the proposed per-match expansion rule accepts those self-outputs and rejects any
residual match that would still change bytes. RK-7 now consistently separates the fail-safe
runtime response (omit only the affected value and still write the bundle) from a future policy
change. The NEG-5 Class IMM decision is not ready for implementation, however: omitting pass B
leaves admitted readable roots unproved clean of non-byte-identical key material and contradicts
the design's own isolation premise.

## Blocking Findings

ID: F-001
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: `artifacts/runs/run_4d1c47c838db/DESIGN.md:255-261`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:465-488`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:1758-1791`,
`artifacts/runs/run_4d1c47c838db/DESIGN.md:1852-1898`
Issue: The default Class IMM NEG-5 pass set A/C/D does not establish that every readable root is
clean of answer-key material.
Reason / Evidence: The design correctly re-derives that recursive immutability proves only current
write capability and says nothing about pre-existing content. Pass C closes the single
byte-identical-copy example, but the design explicitly concedes that A/C/D misses a pretty-printed
copy, excerpt, or quoted fragment under an unrelated filename. Such a pre-existing readable file
does not require a malicious operator during the review; it is an initial-state condition, and
immutability does not establish its cleanliness. This conflicts with the original answer-key
non-exposure requirement and with DESIGN's own principles that S2 without readable-set cleanliness
is defeated by a stray copy and that NEG-5 must not trust classification. Citing I-3 to omit B is
also inconsistent with lines 1785-1791's correct conclusion that reducing NEG-5 by citing the
classification proof is circular. The optional, default-off `--scan-imm-content` flag does not
make the default baseline satisfy the guarantee. In-place text remains contradictory as well:
lines 465-468 claim there is nothing a scan could find after the proof, immediately before lines
481-482 say the proof says nothing about what is already there; lines 486-488 say every escaping
symlink is a hit although the corrected contract makes pass S Class-USR-only.
Required Action: Require pass B for every admitted IMM root in a qualifying baseline, or replace
the broad roots with a narrower mechanism that independently proves absence of all D.6 key
material at capture time. Then reconcile G.3.3's proof rationale and symlink-bounding text with the
chosen per-class pass contract and update the NEG-5 tests accordingly.

## Non-Blocking Findings

None.

## Test Review

- Independently ran the actual `redaction/1.1` categories over
  `GITHUB_TOKEN=ghp_deadbeef1234` and `https://user:hunter2@example.test/x`. In both cases the
  second-pass category count is non-empty, the text is unchanged, and every residual match expands
  exactly to its own span. This confirms `extra == ()` was unsatisfiable for the intended inputs.
- Checked all five category patterns and replacements. Categories 1, 4, and 5 cannot self-output;
  categories 2 and 3 can self-output only through their literal placeholders. Therefore the
  per-match rule cannot produce a text-equal-but-unsafe acceptance under the current policy.
- Independently evaluated the IMM counterexample from the stated pass semantics. A renamed,
  byte-identical copy is caught by C with a sound size prefilter, but any reformatted/partial copy
  with an unrelated name is missed by A/C/D and caught only by B, exactly as DESIGN acknowledges.

## Evidence Checked

- Full corrected `DESIGN.md`, including D-H.2, G.3/G.3.3, G.9, RK-7, iteration 4 decisions,
  component changes, compatibility notes, and testing strategy.
- Actual category definitions and `redact_text()` behavior in `scripts/run_logging.py`.
- `IMPLEMENTATION.md` F-101/F-102 and `REVIEW_IMPLEMENTATION_iteration1.md` F-001/F-002.
- The verbatim OS-22 objective from `orca orchestration task-list --run run_804e35d29531 --json`.

## Final Decision

FAIL. D-H.2 and RK-7 are corrected and implementation-ready, but the default Class IMM NEG-5
contract still permits readable answer-key material that only pass B detects. The design must
resolve that security-contract gap and its related internal contradictions before IMPLEMENTATION.
