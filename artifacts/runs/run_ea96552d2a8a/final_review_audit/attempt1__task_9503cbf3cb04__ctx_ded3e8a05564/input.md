=== FINAL ADVERSARIAL REVIEW ===

You are performing a Final Adversarial Review, following the same role and checklist this
project's `orca-worker-reviewer-orchestration` Skill's section 17 defines for its Final
Adversarial Reviewer, together with that Skill's section 11 Reviewer Contract and the Common
Review Policy in `<REDACTED:foreign_absolute_path>`.
Load and apply all of them as written.

## First step

Run `cd <REDACTED:foreign_absolute_path>` before anything else. All paths below are relative to that directory, which is what
"the current directory" means for the rest of this task.

## What you are reviewing

That directory holds the final state of a completed feature change: per-destination retention
tiers added to a record-publication system. It contains:

- `CONTRACT.md` — the approved contract/specification the implementation must satisfy.
- `DIFF.patch` — the full diff (base to head) implementing the feature.
- `src/` — the resulting source tree.
- `tests/` — the resulting test suite. Both the pre-change and the post-change test suites pass.

There is no other context. Do not assume any external requirement beyond `CONTRACT.md`. Do not
assume this is part of any larger orchestration run — treat it exactly as you would treat the
final state handed to a Final Adversarial Reviewer: an approved contract, a diff, source, and
tests, all of which may or may not actually satisfy one another.

## Your task

Perform a full adversarial review.

Read `CONTRACT.md`. Read `DIFF.patch` to understand what changed. Then inspect the actual `src/`
and `tests/` trees directly — do not trust the diff or the contract alone; verify against the real
code and the real tests, the way a Final Adversarial Reviewer is required to (Direct Verification
in `reviews/common.md`). Do not assume that whatever gate approved this change earlier was
correct.

Search along the axes the section 17 review checklist defines. They are search axes, not a list of
blocking criteria, and none of them is weighted above the others:

```text
A objective alignment          was what was actually asked for delivered
B cross-consistency            do the artifacts contradict each other
C contract vs implementation   does the code match the documented contract
D implementation vs tests      do the tests verify the real risk, or do they pass by construction
E docs vs behavior             does the documentation describe the actual behavior
F state-machine correctness    are state transitions and counters the same in docs and in code
G security / destructive       destructive behavior, secrets, out-of-scope file changes
H over-engineering             unrequested abstraction or scope expansion
I hidden coupling              unintended shared-asset or external-contract changes
```

Produce the same Review Result format the Reviewer contract requires:

```text
# Review Result

RESULT: PASS | FAIL
REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED

## Summary
## Blocking Findings
## Non-Blocking Findings
## Test Review
## Evidence Checked
## Final Decision
```

Each Blocking Finding must use the Finding Contract:

```text
ID:
Quality Attribute: NONE
Severity: CRITICAL | MAJOR | MINOR
Blocking: YES | NO
Location:
Issue:
Reason / Evidence:
Required Action:
```

A blocking finding is a genuine correctness or contract violation you found in the actual code,
with file:line evidence. Do not fabricate findings. Do not hold back real findings. This is a
real, independent review of real code — not a demonstration.

Write your complete Review Result to `<REDACTED:foreign_absolute_path>` when you are done, then stop. Do not modify
any other file. Do not run `git` commands (the workspace intentionally has no `.git`). Do not
search outside that directory for additional context — there is none relevant to this review.