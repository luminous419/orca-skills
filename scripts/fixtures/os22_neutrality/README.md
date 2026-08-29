# Pre-OS-22 Task-spec neutrality baseline

`pre_os22_task_specs.json` is a golden capture of **every Task spec this repository
renders**, taken at commit `1045815` ("Validate Final Adversarial Review effectiveness
(#19)") — the last commit **before** OS-22 added Final Review observability.

It exists to answer one question, mechanically: did adding per-dispatch audit records,
redaction and evidence export change a single byte of what a Reviewer is handed?
`FinalReviewObservabilityNeutralityTests` in `scripts/test_e2e_harness.py` compares the
current tree's output against this file **as UTF-8 bytes**.

## What it holds

| family | key | what it is |
|---|---|---|
| `workflow_specs` | `<skill>` → `<workflow>\|profile=<none\|multi>` | every spec a real workflow dispatches, captured with a `render_task_spec` recording wrapper |
| `direct_specs` | `<role>\|<phase>\|iter<N>\|<blocks>` | an enumerated `render_task_spec()` matrix: three roles × every phase the role is legal for × iterations 1–2 × the five optional-block combinations |

`profile=multi` is not optional coverage. `scripts/e2e_harness.py` renders a
`final_review` spec only when `final_review_routing_context()` is not `None` — i.e.
only under a selected Agent Profile — so without it family A would carry no Final
Review spec at all, and the section 2 claim would not cover the very dispatch OS-22
observes. `direct_specs` covers `final_reviewer`/`final_review` at attempts 1 and 2,
with and without a routing block, independently of what any workflow happens to
dispatch: a future harness change cannot silently shrink the coverage.

## The comparison is byte-strict, and that is the whole point

The capture uses `canonicalize_task_spec()` (`canonicalization: task_spec/1.0`), **not**
`_normalize_artifact()`. The OS-4 normalizer splits every line on whitespace and rejoins
it, so it silently equates specs that differ in indentation, in repeated interior
spaces, in trailing spaces, or in the presence of a terminal newline — and
`render_task_spec()` emits all four as real reviewer-visible content:
`relevant_previous_findings: ` / `approved_baseline: ` / `new_claims: ` carry a trailing
space when their value is empty, and a worker report quoted into `current_delta` carries
its own interior double spaces. A golden built on that normalizer would be a
whitespace-insensitive comparison, not an identity claim.

`canonicalize_task_spec()` therefore performs exactly **one** substitution — the
temporary workspace path, which reaches exactly one reviewer-visible field
(`drill_down`) in family A — and nothing else: no `splitlines()`, no `split()`, no
`strip()`, no reserialization. Family B passes `workspace=None` and gets the identity
function. A closed tripwire list fails the capture loudly if any other nondeterministic
value (a temp path, an ISO-8601 timestamp, an orca-assigned id) ever appears, rather
than normalizing it away.

`test_a_whitespace_only_change_fails_the_neutrality_golden` proves the strictness
instead of asserting it: it applies four whitespace-only mutations to a worker, a
reviewer and a `final_reviewer` spec, requires each to **fail** the real comparison
helper, and demonstrates in the same test body that `_normalize_artifact()` would have
accepted three of them.

## How it was generated

```bash
git archive 1045815 | tar -x -C <checkout>
cp scripts/test_e2e_harness.py <checkout>/scripts/test_e2e_harness.py   # the capture is new
cd <checkout> && python3 -c "…capture_neutrality_task_specs(Path('.'))…"
```

The capture function is new in OS-22, so the test module is **copied** into the pre-OS-22
checkout — the same file, not a re-typed twin — and applied to that commit's code. This
is the same technique `scripts/fixtures/legacy_baseline/README.md` documents for OS-4.
It was generated **before any other OS-22 change landed**; a golden captured afterwards
would only prove the code agrees with itself.

## Regeneration rule

If `FinalReviewObservabilityNeutralityTests` fails, **the current code changed
reviewer-visible bytes and the code is what needs fixing.** Regenerating this file to
make the test pass destroys the only evidence for OS-22 section 2.

There is exactly one legitimate regeneration: if a later change reverses the deferred
`phase_artifact_contract()` Final Review suffix defect, that fix lands as its own commit
and the golden is regenerated at that commit with the delta documented as a section 9
**conformance correction** — explicitly not as an observability change.

## Not a replacement for the OS-4 baseline

`scripts/fixtures/legacy_baseline/pre_os4_artifacts.json` and `LegacyByteIdentityTests`
are untouched by OS-22, deliberately: extending that capture or that fixture in place
would change the input `LegacyByteIdentityTests` compares against and destroy the OS-4
evidence it exists to hold. Two claims, two capture functions, two fixture files.
