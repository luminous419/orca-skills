# Worker Result

STATUS: COMPLETE

## Test Scope / Existing Test Assessment

Verification-only phase for run `run_028d416e596a`, TEST iteration 1, on branch
`agent/final-review-observability-evaluation` at `104be57`. No test file, gate, predicate, facade
or DESIGN/IMPLEMENTATION document was modified. The task was to **independently re-verify**
IMPLEMENTATION's evidence for INV-ATTEMPT-2 rather than trust it, with the user's explicit emphasis
on **negative cases and regression**.

**What was assessed.** The seven gates commit `467cdc9` added, plus `c642ddd`'s F-901 comment fix:

| gate | boundary | site |
|---|---|---|
| GATE 1 | `review_isolation.repatriate()` | `scripts/review_isolation.py:2631` |
| GATE 2 | `review_isolation.isolate()` | `scripts/review_isolation.py:2699` |
| GATE 3 | `final_review_eval.py isolate --attempt` CLI door | `scripts/final_review_eval.py:1390` |
| GATE 4 | `review_isolation.build_attestation()` | `scripts/review_isolation.py:2491` |
| GATE 5 | `run_logging.final_review_report_ladder_path()` | `scripts/run_logging.py:1497` |
| GATE 6 | `e2e_harness.final_review_artifact_path()` | `scripts/e2e_harness.py:431` |
| GATE 7 | `run_logging.read_final_review_attempt_provenance()` | `scripts/run_logging.py:2448` |

All seven are the **first executable statement** of their function (read directly, and pinned by
T-13.4′'s AST census). GATE 0, the pre-existing `final_review_dispatch_key()` check, is refactored
onto the same predicate at `run_logging.py:1741`. `.gitattributes` names exactly seven boundaries
in its enumerated list and `final_review_dispatch_key()` separately as the pre-existing site —
**F-901 is closed as reported**, and the attribute rule line is byte-identical to the shipped one.

**Assessment of the existing tests: substantively sound, with one inert assertion.** The negative
matrix is genuinely uniform — the same eight values (`0`, `-1`, `-12`, `False`, `True`, `2.0`,
`"2"`, `None`) at all six direct-call boundaries, with the bool/int classification done by
`type(x) is int` rather than membership (`False == 0` would otherwise mis-file the bool into the
wrong half of the message contract). One assertion inside T-13.1 does not test what it claims; see
Finding F-1001.

## Added / Modified Tests

**None.** This phase is verification-only by task contract. The one defect found is reported as a
Finding for the Reviewer/Coordinator to route into a correction, not silently patched.

## Behavior Covered

### Negative-case matrix, verified as genuinely exercised

Verified by **mutation testing**, not by reading: each gate was neutered to
`pass  # MUTANT: gate removed` in a throwaway `git worktree` at the same commit, and the owning
tests were re-run. A gate whose removal does not break its tests is a false-positive test; every
one of the seven broke.

| gate | tests re-run with the gate removed | result |
|---|---|---|
| GATE 1 | `test_review_isolation.py -k AttemptDomain` | **9 failed** — all 3 out-of-range + all 5 wrong-type subtests, plus the T-13.4′ census |
| GATE 2 | `test_review_isolation.py -k "t131_isolate or t133a"` | **did not terminate in 240 s, nor in >20 min** — with the gate gone, `isolate()` runs its full body. See note below |
| GATE 3 | `test_final_review_eval.py -k AttemptDomain` | **4 failed** — `--attempt 0` and `-1` on both the `--repatriate` and `--teardown` forms |
| GATE 4 | `test_review_isolation.py -k t136` | **9 failed** — all 8 values + the ordering test |
| GATE 5 | `test_run_logging.py -k AttemptDomain` | **8 failed** — all 8 values |
| GATE 6 | `test_e2e_harness.py -k FinalReviewArtifactPathAttemptDomain` | **9 failed** — all 8 values + the message assertion |
| GATE 7 | `test_run_logging.py -k AttemptDomain` | **11 failed** — all 8 values, the no-scan assertion, and both CLI-door assertions |

**GATE 2's timeout is a pass, not an inconclusive result, and it is proved rather than assumed.**
`IsolationAttemptDomainError` has exactly **one** raise site in the tree
(`scripts/review_isolation.py:1047`, inside the facade), and the facade has exactly four call sites
(the three `review_isolation` gates and the CLI door). With GATE 2 removed, nothing reachable from
`isolate()`'s body can raise that class, so `assertRaises(IsolationAttemptDomainError)` must fail.
The non-termination is itself the measurement: the gate is what stops an expensive session build on
a nonsense argument.

**Exception types and messages**, confirmed at each boundary: `IsolationAttemptDomainError` (a
`final_review_eval.EvalInputError` subclass) at gates 1/2/3/4; `RunLoggingError` (a `ValueError`
subclass, so the shipped `except ValueError` contract at gate 6 is preserved) at gates 5/6/7. Both
halves of the message text (`must be >= 1` for out-of-range ints, `must be an int >= 1` for wrong
types) are asserted verbatim, and T-13.7 additionally pins `final_review_dispatch_key()`'s five
shipped refusal strings byte-for-byte, which makes the extraction's behaviour-preservation claim
executable.

**Malformed strings at the CLI door** take argparse's own exit 2 with `invalid int value`
(`abc`, `1.5`, `0x2`, `1e3`), pinned deliberately as pre-existing per D-A.7.5; out-of-domain
*integers* take exit 1 with `input error: --attempt must be >= 1, got 0` and no traceback. Lenient
integer spellings (`001`, `+2`, `1_0`, ` 3 `) normalise before the domain check ever sees them.

### "No side effect" — verified by gate *relocation*, which is the only mutation that tests it

A plain `assertRaises` cannot distinguish "the gate ran first" from "the gate ran last". So each
gate was **moved later in its own function body** — the exception is still raised, with the correct
type and message, but the work the gate exists to prevent now happens first. A side-effect
assertion that still passes under this mutation is inert.

| claim | mutation | result |
|---|---|---|
| GATE 1 creates no run directory | gate moved after `root.mkdir(...)` | **3 failed** — `assertNothingCreated()` genuinely catches it |
| GATE 2 builds no session | gate moved after `build_session(...)` | **PASSED — the assertion is inert.** See F-1001 |
| GATE 4 sets no document field | gate moved after the `readable["entries"]` loop | **1 failed** — the domain error no longer wins over `IsolationError` |
| GATE 3 tears nothing down | gate moved after the `--teardown` branch | **2 failed** — the directory is destroyed |
| GATE 7 scans no record directory | gate moved after the record loop | **1 failed** — `iter_final_review_audit_records` is called |

Gates 5 and 6 are pure functions with no side effect to order, so the property does not apply.

**T-13.4′ independently catches every relocation**, including GATE 2's: re-run against the
relocated `isolate()` gate it reports `SUBFAILED(function='isolate')`, because it asserts the gate
is `body[0]` at the AST level. This is why F-1001 is an evidence defect and not a correctness
defect — see the finding.

### Regression side

Valid attempts still produce byte-identical shipped output at every boundary, and the documented
`.gitattributes` edge case **attempt = 100** is covered at all four boundaries where an attempt
actually becomes a name or a document field:

| boundary | valid attempts asserted | shipped result pinned |
|---|---|---|
| GATE 1 `repatriate()` | 1, 2, 3, 9, 10, 42, 99, **100** | report + workspace destinations, and `report_digest == sha256_path(source)` |
| GATE 4 `build_attestation()` | 1, 2, **100** | `final_review_attempt` round-trips through `json.dumps` as a JSON **number**, `type(...) is int` |
| GATE 5 ladder path | 1, 2, 3, 9, 10, 42, 99, **100** | `FINAL_REVIEW.md` / `FINAL_REVIEW_iteration<N>.md` |
| GATE 6 artifact path | 1, 2, 3, 99, **100** | `artifacts/runs/run_t/FINAL_REVIEW{suffix}.md` |
| GATE 3 CLI door | 2 | still reaches `repatriate()`, which refuses for its **own** reason with the contract exit, not the input-error exit |
| GATE 7 provenance | 1, 2 | provenance JSON unchanged; attempt grouping still comes from the record's own field, never the filename |

Attempt 100 is not separately asserted at gates 2, 3 and 7, and does not need to be: at those
boundaries the attempt is passed through rather than turned into a name, and the value it ultimately
lands in — `ISOLATION.json`'s `final_review_attempt` — is asserted at 100 by T-13.6. I confirmed
independently with `git check-attr` that `FINAL_REVIEW_iteration100.md` is `unspecified`
(unexempted), which is RK-19's documented, deliberately-bounded undermatch.

## Execution

Every number below was produced on this host in this phase. None is copied from IMPLEMENTATION.md.

**Command 1 — the four relevant suites:**

```
python3 -m pytest scripts/test_review_isolation.py scripts/test_final_review_eval.py \
  scripts/test_run_logging.py scripts/test_e2e_harness.py -q
  -> 2 failed, 562 passed, 1 warning, 1462 subtests passed in 720.66s (0:12:00), exit 1
```

**Command 2 — the broader suites IMPLEMENTATION touched:**

```
python3 -m pytest scripts/test_os22_required_tests.py scripts/test_orca_runtime_contract.py \
  scripts/test_validate_skills.py -q
  -> 370 passed, 8 warnings, 3590 subtests passed in 16.70s, exit 0
```

**Command 3 — skill validation:**

```
python3 scripts/validate_skills.py
  -> Skill validation PASSED (463 checks), exit 0
```

**Reconciliation against IMPLEMENTATION.md's claimed counts — exact match.** IMPLEMENTATION ran the
same files in two different groupings, so the per-command numbers are not comparable, but the
totals are:

| quantity | IMPLEMENTATION (205 + 727 / 360 + 4692) | this phase (562 + 370 / 1462 + 3590) |
|---|---|---|
| passed | **932** | **932** |
| subtests passed | **5052** | **5052** |
| failed | 2 | 2 |

**The two failures, re-verified as pre-existing on a genuinely unmodified tree.** IMPLEMENTATION
verified this by stashing; its changes are now **committed**, so a stash can no longer reproduce the
baseline. I used the stronger equivalent — a `git worktree` at `8411cce`, the direct parent of the
attempt-domain commit `467cdc9`:

```
git worktree add <scratch>/baseline 8411cce --detach
  # confirmed: 0 occurrences of `assert_attempt_in_domain` in all four production files,
  # and .gitattributes carries the original 4-line preamble with no invariant comment block
python3 -m pytest scripts/test_run_logging.py -q -k RetainedReportWhitespaceExemption
  -> 2 failed, 5 passed, 176 deselected, 1 warning, 38 subtests passed in 1.21s
```

The same two test IDs fail —
`RetainedReportWhitespaceExemptionTests::test_the_whitespace_gate_passes_over_the_whole_os22_range`
and `::test_the_gate_fails_again_once_the_exemption_is_removed`. Both assert `git diff --check`
over the committed range `1045815..HEAD`, so they read **committed history** and cannot be
influenced by a working-tree edit. The offending trailing whitespace is in five older review
artifacts —

```
artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md
artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration2.md
artifacts/runs/run_75c5c6046f35/DESIGN.md
artifacts/runs/run_75c5c6046f35/REVIEW_DESIGN_iteration2.md
artifacts/runs/run_75c5c6046f35/REVIEW_TEST_iteration1.md
```

— all introduced by `959a6b4` ("Confirm TEST BLOCKED independently (F-401/402/403)"), verified to be
an **ancestor of `467cdc9`**. These are environment/history noise in the attempt domain's terms:
pre-existing, attempt-domain-unrelated, and neither caused nor fixed by this implementation.
`test_the_gitattributes_rule_is_exactly_the_one_designed` **passes**, which is direct proof that
`c642ddd`'s fix was comment-only.

**Result: FAIL** — on the strength of Finding F-1001 alone. Every executed suite matches
IMPLEMENTATION's reported evidence exactly, all seven gates are correctly implemented and correctly
ordered, and there is no regression; but one negative-case side-effect assertion that IMPLEMENTATION
explicitly claims as covered is inert, and the task contract requires that be reported as blocking
rather than noted.

## Failures / Findings

### F-1001 — BLOCKING (MINOR severity / G5, missing validation evidence)

**T-13.1's "a session is expensive to build and must not be built on a bad argument" assertion is
inert: it can never observe a session being built.**

*Location:* `scripts/test_review_isolation.py:2269-2292`,
`AttemptDomainTests::test_t131_isolate_refuses_zero_and_negatives_and_builds_no_session`, together
with `assertNothingCreated()` at `:2240-2250`.

*Defect.* The test snapshots `set(Path(tempfile.gettempdir()).glob(f"{SESSION_PREFIX}*"))` before
and after, but calls `isolate(..., session_base=self.base)`. `build_session()`
(`scripts/review_isolation.py:1896-1897`) resolves its directory as
`Path(session_base) if session_base else Path(tempfile.gettempdir())`, so with `session_base` set,
every session lands under `self.base` — a *fresh* `TemporaryDirectory` (`:88-89`) — and never in the
globbed directory. `Path.glob` is not recursive, so `<tmp>/<tmpXXXX>/frv_iso_*` cannot match
`<tmp>/frv_iso_*`. The companion `assertNothingCreated()` is inert here for a second, independent
reason: it asserts `(self.base / "artifacts").exists()` is false, but `isolate()` writes to
`self.base/frv_iso_*/review_root/artifacts/...` and never creates `self.base/artifacts` at all.

*Proof (measured, not argued).* With GATE 2 relocated to immediately after the `build_session(...)`
call — so the same exception is still raised, with the same type and the same message, but a full
session is built first — the test **passes**: `1 passed, 3 subtests passed in 0.26s`. A direct probe
against that same mutated module reports:

```
raised: IsolationAttemptDomainError attempt must be >= 1, got 0
test's tempdir-glob assertion sees a change? -> False
sessions actually created under session_base -> ['frv_iso_yftbiv52']
test's assertNothingCreated target (base/'artifacts') exists? -> False
```

A real session directory was created and **both** side-effect assertions stayed green.

*Why this is an evidence defect and not a correctness defect — stated plainly so it can be routed
proportionally.* The **code is correct**: GATE 2 is the first statement of `isolate()`, and that
ordering is genuinely pinned by a *different* test — T-13.4′
(`test_t134_every_gated_boundary_checks_before_it_does_anything_else`), which asserts the gate is
`body[0]` at the AST level and which I confirmed reports `SUBFAILED(function='isolate')` against the
relocated gate. Outright gate *removal* is also still caught, by the `assertRaises`. What does not
exist is the specific evidence IMPLEMENTATION.md's "Behavior Covered" table claims: T-13.1 is listed
as pinning that `isolate()` creates "no new session directory", and it does not pin that.

*Required Action (one of):*
(a) glob `self.base` instead of `tempfile.gettempdir()` — i.e. snapshot
`set(self.base.glob(f"{review_isolation.SESSION_PREFIX}*"))` before and after, which makes the
assertion live; **or**
(b) if the redundancy with T-13.4′ is judged sufficient, delete the two inert assertions and correct
IMPLEMENTATION.md's coverage claim, so no document asserts evidence that does not exist.

Option (a) is a two-line change to one test file and is the smaller correction.

*Scope note:* this is a test-file defect only. No production gate, predicate, facade or document
needs to change, and nothing in the protected list is touched.

## Remaining Gaps

Non-blocking observations. None of these was treated as a finding, and none blocks the phase.

* **N-1 — the CLI door's wrong-type matrix is covered by equivalence, not literally.** `argparse`
  declares `--attempt` as `type=int`, so `True`, `False` and `2.0` can only arrive at the door as
  *text*, where they take argparse's exit 2. T-13.3b pins that class with `abc`, `1.5`, `0x2` and
  `1e3` — `1.5` and `1e3` are the float spellings, so the float case is covered even though the
  literal string `2.0` is not in the list. Adequate as written; noted only so a later reader does
  not mistake the shorter list for a gap.
* **N-2 — `test_review_isolation.py:2409` cites T-12.3, which does not exist in this tree.** The
  comment pairs T-13.5 ("attempt 100 still works") with "T-12.3 asserts it is still unexempted".
  T-12.3 belongs to DESIGN's T-12 `git check-attr` battery under D-A.6″, which IMPLEMENTATION
  already disclosed is **not implemented here**. The underlying claim nevertheless holds — I
  verified directly that `git check-attr whitespace` reports `FINAL_REVIEW_iteration100.md` as
  `unspecified`. This is a dangling forward reference in a comment, inherited from the unimplemented
  D-A.6″ item, not a defect in the attempt-domain work.
* **N-3 — the same `tempfile.gettempdir()` snapshot in T-13.1 is a latent flakiness source.** It
  reads a directory shared with every other process on the host; if fixing F-1001 via option (a) it
  disappears, and if via option (b) it should be removed rather than left.
* **N-4 — RK-19 and RK-21 remain open**, exactly as IMPLEMENTATION recorded and as the approved
  DESIGN permits. RK-21's fail-closed behaviour (`rc` non-zero, empty stdout, nothing written) is
  asserted at both door-2 subcommands and re-confirmed here; no action is implied for this phase.
* **N-5 — the two `RetainedReportWhitespaceExemptionTests` failures are a live repository-hygiene
  issue** that will keep failing every future run until the trailing whitespace in the five
  committed artifacts listed above is addressed. It is outside this run's scope and outside the
  attempt domain, but it is not self-healing, and it means these suites cannot currently reach a
  green exit 0.
