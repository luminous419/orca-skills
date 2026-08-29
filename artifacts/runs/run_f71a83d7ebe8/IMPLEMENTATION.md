# IMPLEMENTATION — run_f71a83d7ebe8 (iteration 2, corrected)

Phase: IMPLEMENTATION
Role: WORKER
Branch: `agent/final-review-observability-evaluation` (Draft PR #20)
Iteration 1 task: `task_fdff71d8b2ac` / Dispatch: `ctx_64de550407b3`
Iteration 2 task: `task_60ca59ce0dcc` / Dispatch: `ctx_d1a82ec198b2`

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
CI_STATUS: GREEN — GitHub Actions run 33080957741 on `f0c9275`, all three matrix jobs success

> **Iteration 2 is a report correction only. It changes no production code.** The
> iteration-1 fix was correct and is proven green on real CI. Iteration 1's *report*,
> however, asserted three things that are false (a red-CI prediction, a fabricated causal
> story, and a conflation of two different `git diff --check` invocations), and the Reviewer
> relied on them to raise blocking finding F-IMPL-001. Those claims are corrected in place below — marked
> **[CORRECTION]** and left visible rather than deleted, so the record shows both what was
> wrong and what is true. See §"Correction of iteration 1's false claims (F-IMPL-001)".

---

## Summary

The three CI failures on PR #20 were one defect and two host-topology assumptions, and
they are fixed in that order of importance.

1. **Production defect (C4).** `isolate()` ran the recursive immutability proof on the
   Class IMM candidate roots *unconditionally*, including under `--enforcement none`.
   That proof exists only to justify naming a root in a generated Seatbelt profile's read
   clause without content-scanning it, and `--enforcement none` renders no profile — so a
   Seatbelt-only precondition was gating the one documented path that has no Seatbelt. On
   any host whose `/dev` contains a writable directory (`/dev/mqueue`, `/dev/shm` on an
   ordinary Linux box) the unenforced capture exited 4 and was **unreachable**. The fix is
   in production: `imm_candidates_for_enforcement()` makes the candidate list a Seatbelt
   input, and `--enforcement none` proves nothing because it admits nothing.
2. **Host-topology assumption in production (C5).** `OWN_DESCRIPTOR_DIR = "/dev/fd"` wrote
   one host's `/dev` topology down as a constant. It is now **derived and proven per host**
   by `derive_own_descriptor_dir()`, and a host on which nothing proves gets **no**
   exemption — strictly stricter than the constant.
3. **Host-topology assumption in a test (C3).**
   `test_private_var_and_library_are_not_admissible_by_habit` asserted the never-admitted
   *rule* by naming the real `/private/var`. Off darwin that path does not exist,
   `compute_readable_set()` skips a candidate that is not there, and the test reached the
   branch it claimed to cover on exactly one operating system. It is split into a portable
   fixture-controlled assertion of the rule and a `DARWIN_ONLY` assertion of the host fact.

No skip replaces an assertion anywhere. Every behaviour the three failing tests were
supposed to establish is now **actually executed on Linux**, verified in Docker on CPython
3.11.16, 3.12.14 and 3.13.15. Exactly **one** new skip was added, on **macOS: zero**.

**[CORRECTION]** Two pre-existing failures remain **on a full local checkout only** — not
on CI. They are the retained-report whitespace gate tests, which compare `git diff --check`
over the OS-22 range and trip on coordinator-committed `artifacts/runs/*/REVIEW_*.md`
snapshots carrying Markdown hard breaks. On GitHub Actions these two tests **skip**, because
`actions/checkout@v4` checks out shallow and the pinned base commit is unreachable; CI is
therefore green. Iteration 1 wrongly reported them as failing on every platform and
concluded PR #20 would stay red. They are proven below to exist at committed `HEAD` without
this change, and they are **deliberately not acted on** — fixing them would require editing
other runs' digest-bound artifacts, which the artifact contract forbids.

---

## Analysis

### The failure, read precisely

`compute_readable_set()` walks `imm_candidates`, and for each candidate that exists it runs
`enumerate_boundaries()` + `prove_immutable_narrowing()`; a failing proof is a fatal
`IsolationError` → exit 4. `isolate()` called it the same way for both enforcement backends.

| CI failure | Real cause |
| --- | --- |
| `IsolateCliWiringTests.test_a_leak_in_an_allowed_root_is_exit_four` | The CLI ran `--enforcement none`, but session construction aborted at `/dev` (`I-2` on `/dev/mqueue`, `/dev/shm`) before it ever reached the planted-key assertion. The test's own subject was never exercised. |
| `ImmutabilityProofTests.test_private_var_and_library_are_not_admissible_by_habit` | The test asserted the never-admitted rule through a path that only exists on darwin. On Linux `compute_readable_set()` hit `if not root.exists(): continue`, so no `IsolationError` was raised. |
| `UnenforcedTests.test_t88_enforcement_none_records_unenforced_and_fails_s2` | Same `/dev` proof failure. The documented `--enforcement none` path exited 4 instead of producing an unenforced attestation. |

Failures 1 and 3 are the **same** production defect, observed through two tests. Failure 2 is
an independent test defect.

### C4 — where the fix belongs: production, not the test

The task asks for the reasoning either way. **The current production behaviour is genuinely
wrong**, so the fix is in `review_isolation.py`.

The immutability proof answers exactly one question: *may this root be named in an
`(allow file-read* (subpath …))` clause of a generated profile **without** being
content-scanned at session-build time?* Under `--enforcement none`:

- no profile is rendered (`render_seatbelt_profile()` is inside `if enforcement == ENFORCEMENT_SEATBELT`);
- nothing is admitted and nothing is denied — the reviewer process is unconfined;
- `assert_carve_outs_denied()`, the pre-flight probe, the O-1 probe and NEG-2…NEG-8 all do
  not run, and the probes are recorded `NOT_APPLICABLE_UNENFORCED`.

So the proof's question has no subject, and asking it anyway bought nothing while making the
documented unenforced capture impossible on an ordinary Linux host. Fixing this only in the
test (by passing fixture-controlled candidates from the test) would have left the shipped
`--enforcement none` CLI **still broken on Linux** and would have hidden that fact behind a
green test. That is exactly the shape the reviewer's MAJOR-1 objects to.

What the unenforced path actually rests on is untouched, because none of it was ever the IMM
proof's job — and this is asserted, not asserted-in-prose:

- every Class USR root (`review_root`, `tmp`, `home`, and every `--allow-read`) is still
  realpath-resolved, still refused if it is or contains a `NEVER_ADMITTED` path, and still
  content-scanned with admission refused on a hit;
- NEG-0 (positive control) and NEG-1 (review_root walk) still run and still raise;
- `assert_home_scanned()` and `assert_no_unscanned_descendant()` still run;
- `scope_enforcement` is still `unenforced`, S2 and S3 are still `FAIL`, the CLI still prints
  its `WARNING`, and the capture still FAILS B6.

### C5 — `/dev` derived rather than hard-coded

Two places encoded `/dev`:

- **`DEFAULT_IMM_CANDIDATES`** contains `/dev`. This is now explicitly documented as what it
  always was — a **Seatbelt admission input**, i.e. a darwin host list — and after the C4 fix
  it is read on the `--enforcement seatbelt` path only. It is fixture-controllable end-to-end
  through the `imm_candidates` parameter and the repeatable `--imm-candidate` CLI flag.
- **`OWN_DESCRIPTOR_DIR = "/dev/fd"`** was a bare claim about one host. It is now derived:
  `derive_own_descriptor_dir()` opens a throwaway descriptor `N` and admits a candidate only
  if `<candidate>/N` is the **same device and inode** as this process's own fd `N`. That is
  the definition of "this is my own descriptor table", so the derivation cannot be talked
  into exempting an ordinary writable directory. On darwin it returns `/dev/fd` — byte-for-byte
  the previous value, so macOS behaviour is unchanged. `None` (nothing provable) means **no
  exemption at all**, which is the fail-closed direction.

### C1 / C2 — what was deliberately *not* done

- `prove_immutable()`'s I-1…I-6 logic, `NEVER_ADMITTED`, `MANDATORY_CARVE_OUTS`,
  `NARROWABLE_CHECKS`, the NEG-5 mandatory pass B, `SCAN_PASSES_IMM`/`SCAN_PASSES_ALL`, the
  profile renderer and every fail-closed `raise` are untouched.
- No security test became a skip or a no-op. The one new skip is a **host fact** (`/private/var`
  exists and is never-admitted), and the **rule** it used to stand in for is now asserted
  portably on a fixture root, with a positive control.

---

## Changes

### `scripts/review_isolation.py` (production)

1. **`derive_own_descriptor_dir(candidates=OWN_DESCRIPTOR_DIR_CANDIDATES) -> str | None`** (new).
   Proves the process's own descriptor directory by `(st_dev, st_ino)` identity against a
   freshly opened descriptor. `OWN_DESCRIPTOR_DIR_CANDIDATES = ("/dev/fd", "/proc/self/fd")`.
   `OWN_DESCRIPTOR_DIR` is now `derive_own_descriptor_dir()` rather than a literal.
2. **`prove_immutable()`** — the I-6 exemption branch is now
   `if OWN_DESCRIPTOR_DIR is not None and str(here) == OWN_DESCRIPTOR_DIR:`. No other change;
   the counters, the failure vocabulary and the verdict are identical.
3. **`imm_candidates_for_enforcement(enforcement, imm_candidates=DEFAULT_IMM_CANDIDATES)`** (new).
   Returns the list unchanged for `ENFORCEMENT_SEATBELT` and `()` for everything else. One
   place decides, and the docstring records why.
4. **`isolate()`** — gains the `imm_candidates` parameter (the seam already present in the
   worktree, preserved) and now calls
   `compute_readable_set(..., imm_candidates=imm_candidates_for_enforcement(enforcement, imm_candidates), ...)`.
5. **Comments** on `DEFAULT_IMM_CANDIDATES` state that it is a Seatbelt admission input and a
   darwin host list, and name `imm_candidates_for_enforcement()` as the one place that decides.

### `scripts/final_review_eval.py` (production)

6. **`--imm-candidate ABS_DIR`** (repeatable) on the `isolate` subcommand, wired to
   `isolate(imm_candidates=…)` and falling back to `review_isolation.DEFAULT_IMM_CANDIDATES`
   when absent. *(Present in the worktree on entry; preserved and now covered by a test.)*

### `scripts/test_review_isolation.py` (tests)

7. `test_private_var_and_library_are_not_admissible_by_habit` → **constants only** (portable).
8. `test_a_never_admitted_candidate_that_exists_is_refused_outright` (new, portable) — asserts
   the never-admitted **rule** on a fixture root, with a **positive control**: the same root
   is admitted when it is not on the list, so the refusal is caused by the list and not by the
   root. Patches `NEVER_ADMITTED` rather than the host.
9. `test_the_real_private_var_is_refused_on_the_supported_host` (new, `@DARWIN_ONLY`) — the
   host-topology half, gated because it *is* host topology. **This is the one new skip.**
10. `test_the_own_descriptor_exemption_is_derived_and_never_assumed` (new, portable) — the
    derivation is correct, and a non-descriptor directory is refused (`None`).
11. `test_only_the_derived_descriptor_directory_is_exempted_from_i3` (new, portable) — the
    exemption applied to a fixture tree **both ways round**: unexempted → `I-2`+`I-3` failure,
    `own_descriptors: 0`; exempted → passes with `own_descriptors: 1`, `writable_files: 0`.
12. `test_no_descriptor_directory_means_no_exemption_rather_than_a_wider_proof` (new, portable) —
    `None` is fail-closed.
13. `test_t88_enforcement_none_records_unenforced_and_fails_s2` — **extended**: the attestation's
    `readable_set` carries **no Class IMM entry**, and the three Class USR entries are all
    `scanned: true`. (`path` fields are P-PATH placeholders, so the assertion is on class and
    count; `assert_home_scanned()` is what names `home` and it ran inside this `isolate()`.)
14. `test_the_imm_proof_is_a_seatbelt_input_and_only_a_seatbelt_input` (new, portable) — C4 as a unit.
15. `test_unenforced_still_refuses_a_usr_root_that_carries_key_material` (new, portable) — the
    C2 guard: dropping the IMM proof from `--enforcement none` must not read as "unenforced
    stopped checking". A planted key in an `--allow-read` root is still fatal, and the
    half-built session is still removed.

### `scripts/test_final_review_eval.py` (tests)

16. `test_the_imm_candidate_flag_replaces_the_default_list_and_defaults_to_it` (new, portable) —
    `--imm-candidate` wiring asserted in-process (repeatable, **replaces** rather than extends,
    falls back to the built-in default when absent), so the new flag is covered on every host
    rather than only where Seatbelt exists.

---

## Modified Files / Artifacts

| File | Kind |
| --- | --- |
| `scripts/review_isolation.py` | production |
| `scripts/final_review_eval.py` | production (CLI flag, pre-existing in worktree) |
| `scripts/test_review_isolation.py` | tests |
| `scripts/test_final_review_eval.py` | tests |
| `artifacts/runs/run_f71a83d7ebe8/IMPLEMENTATION.md` | this Worker Result |

Nothing else was touched. `.idea/`, the untracked `artifacts/` files and every other
worktree state were left alone. No `VERSION`, `LICENSE`, `.gitattributes`, `COMPATIBILITY.md`,
`CHANGELOG.md`, workflow or skill-package change. No PR created, no merge, no push, no
force-push.

---

## Validation

### 0. AUTHORITATIVE: GitHub Actions CI run 33080957741 (commit `f0c9275`) — GREEN

This is the result that settles the objective. It post-dates iteration 1's report.

```
$ gh run list --branch agent/final-review-observability-evaluation --limit 3
completed  success  CI  ...  33080957741  2m13s  2026-08-27T14:12:31Z
completed  failure  CI  ...  32994487855  2m8s   2026-08-26T17:29:53Z
completed  failure  CI  ...  32974118219  2m6s   2026-08-26T13:25:32Z

$ gh run view 33080957741 --json jobs --jq '.jobs[] | "\(.name)\t\(.conclusion)"'
validate (3.12)  success
validate (3.13)  success
validate (3.11)  success
```

Per-job test totals, from the run log:

| Job | Result |
| --- | --- |
| `validate (3.11)` | `Ran 1201 tests in 110.361s` — `OK (skipped=29)` |
| `validate (3.12)` | `Ran 1201 tests in 120.345s` — `OK (skipped=29)` |
| `validate (3.13)` | `Ran 1201 tests in 114.924s` — `OK (skipped=29)` |

Delta against the last red run, 32994487855 on `c059dc0` (`Ran 1193 tests` /
`FAILED (failures=3, skipped=28)`, identical on all three jobs):

| Metric | 32994487855 (`c059dc0`) | 33080957741 (`f0c9275`) | Delta |
| --- | --- | --- | --- |
| Tests run | 1193 | 1201 | **+8** |
| Failures | 3 | **0** | **-3** |
| Skips | 28 | 29 | +1 |

Every step of every job succeeded, including `Check whitespace`:

```
$ gh run view 33080957741 --json jobs --jq '.jobs[] | select(.name=="validate (3.11)") | .steps[] | "\(.name)\t\(.conclusion)"'
Set up job                        success
Check out repository              success
Set up Python                     success
Validate Skill packages           success
Run deterministic tests           success
Verify release package inputs     success
Build and verify release archive  success
Check whitespace                  success
```

### 1. Full suite — macOS (darwin 25.5.0, CPython 3.11)

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1201 tests in 311.147s
FAILED (failures=2, skipped=6)
```

Baseline at committed `HEAD` (`6cd2567`), same host, clean clone:

```
Ran 1193 tests in 759.732s
FAILED (failures=2, skipped=6)
```

**+8 tests, 0 new failures, 0 new skips.** Both failures are the same pre-existing pair
(section 6 below).

**Iteration-2 re-run**, same host, no production change since `f0c9275`:

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1201 tests in 297.350s
FAILED (failures=2, skipped=6)

$ python3 -m unittest discover -s scripts -p 'test_*.py' 2>&1 | grep -E '^(FAIL|ERROR):'
FAIL: test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_gate_fails_again_once_the_exemption_is_removed
FAIL: test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_whitespace_gate_passes_over_the_whole_os22_range
```

These two failures are **local-only**, for the mechanism in §7: a full local checkout can
reach the pinned base commit `1045815`, so the gate actually evaluates; CI's shallow
checkout cannot, so the same two tests skip there. The 1201 test count matches CI exactly.

### 2. Full suite — Linux (MANDATORY LINUX PROOF)

Docker was available. Each run copies the checkout into the container, runs `git clean -xdff`
on the copy so it is a fresh-checkout equivalent plus the working diff, and executes the suite
as a **non-root** user (as root `os.access(…, W_OK)` is true for every path, which would fail
the proof for a reason CI never hits).

```
$ docker run --rm -v "$PWD":/src:ro -v .../linux_run.sh:/linux_run.sh:ro python:<V> /bin/sh /linux_run.sh
```

| Interpreter | Before (HEAD + worktree diff) | After |
| --- | --- | --- |
| Python 3.11.16 | `Ran 1193 tests` — `FAILED (failures=5, skipped=24)` | `Ran 1201 tests in 182.536s` — `FAILED (failures=2, skipped=25)` |
| Python 3.12.14 | — | `Ran 1201 tests in 208.586s` — `FAILED (failures=2, skipped=25)` |
| Python 3.13.15 | — | `Ran 1201 tests in 209.011s` — `FAILED (failures=2, skipped=25)` |

**[CORRECTION] These Docker runs are not equivalent to a GitHub Actions job, and iteration 1
wrongly treated them as such.** The container receives a *full* copy of the local checkout,
so the pinned base commit `1045815` is reachable and the two whitespace gate tests **run and
fail**. On GitHub Actions the checkout is shallow, the base commit is unreachable, and the
same two tests **skip**. That difference — not any property of Linux — is the entire reason
these runs show `failures=2` while the real Linux matrix jobs show `OK`. The residual
`failures=2` above is therefore an artifact of the local harness, and the correct Linux
matrix result is §0's `OK (skipped=29)` on all three interpreters.

The baseline's five failures were the three CI failures plus the two whitespace failures. The
three CI failures are **gone on all three interpreters**. Verbatim baseline
evidence for the primary one:

```
FAIL: test_a_leak_in_an_allowed_root_is_exit_four
AssertionError: 'key material is reachable' not found in "isolation failure: /dev: the
recursive immutability proof FAILED -- 2 writable directories, 0 writable regular files,
first failures [{'check': 'I-2', 'path': '/dev/shm'}, {'check': 'I-2', 'path': '/dev/mqueue'}].
```

**The fixed tests RUN on Linux — they are not skipped.** Targeted verbose run, python:3.11:

```
test_a_leak_in_an_allowed_root_is_exit_four ... ok
test_t88_enforcement_none_records_unenforced_and_fails_s2 ... ok
The CONSTANTS half. Portable, because it is about this module's own lists. ... ok
The REFUSAL half, on a FIXTURE root, so it is asserted on every host. ... ok
The exemption applied to a FIXTURE tree, both ways round. ... ok
I-6's second exception: proven per host, and it exempts nothing else. ... ok
`None` is fail-closed. A host that cannot prove one gets no exception. ... ok
C4, as a unit: which enforcement backend asks the proof's question at all. ... ok
The half of the readable set the unenforced path DOES rest on, asserted here. ... ok
T-10: `--imm-candidate` wiring, asserted in-process so it runs on every host. ... ok
The host-topology half of the same rule, gated because it IS host topology. ... skipped
  'the seatbelt backend is darwin-only; T-8.9 carries the fail-closed guarantee on every
   other platform'
```

### 3. `python3 scripts/validate_skills.py`

```
Skill validation PASSED (463 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.
```

### 4. `python3 scripts/verify_package.py` (and the CI archive step)

```
Package verification PASSED (109 source files)
Built reproducible release archive: dist/orca-skills-0.9.0.tar.gz
Package verification PASSED (109 source files)
Verified archive: dist/orca-skills-0.9.0.tar.gz
```

### 5. `git diff --check`

Exit 0 over this change's diff.

### 6. Skip counts, and every newly added skip justified

| Platform | Skips before | Skips after | Delta |
| --- | --- | --- | --- |
| macOS (darwin, 3.11) | 6 | 6 | **0** |
| Linux (3.11 / 3.12 / 3.13) | 24 | 25 | **+1** |

**The one new skip**, and why it is legitimate under C3:

- `ImmutabilityProofTests.test_the_real_private_var_is_refused_on_the_supported_host`
  (`@DARWIN_ONLY`). It asserts a **host-topology fact** — that the real `/private/var` exists
  and is refused — which has no subject off darwin. It runs on macOS, where the Seatbelt
  backend actually runs. The **rule** it used to be the only carrier of (a never-admitted
  candidate that exists is refused outright, before any proof) is now asserted on **every**
  host by `test_a_never_admitted_candidate_that_exists_is_refused_outright`, with a positive
  control. Net portable coverage of the rule went **up**, not down.

No other skip was added, and no existing assertion was converted into a skip.

### 7. MUST-NOT-REGRESS invariants

All pass on macOS and on Linux 3.11/3.12/3.13 (they are inside the 1199 passing tests):

| Invariant | Where it is asserted | Status |
| --- | --- | --- |
| Answer-key isolation (kernel-enforced; key not readable from the reviewer filesystem) | `Neg5ContractTests`, `NegativeContractTests` (`@DARWIN_ONLY @NEEDS_SANDBOX`) on macOS; `T-8.9` fail-closed guarantee elsewhere; Class USR scan + NEG-0/NEG-1 everywhere | PASS |
| Evidence-bundle sanitization / redaction policy | `test_run_logging` redaction suites, P-PATH assertions | PASS |
| Attempt-domain validation (F-602) | `AttemptDomainCliTests`, `assert_attempt_in_domain` suites | PASS |
| Task/Dispatch provenance | `test_run_logging` provenance suites | PASS |
| Observability neutrality | `test_run_logging` / `test_final_review_eval` neutrality suites | PASS |

---

## Unit Tests / Testing Strategy

**UNIT_TEST_STATUS: PASS**

Production code changed → unit tests added/modified, executed, and passing. **+8 tests**
(1193 → 1201) on both platforms.

Strategy, and the rule that shaped every one of them: *gating is for host-topology facts
only, never for portable contract logic.* For each behaviour I asked which of the two it is.

| Behaviour | Portable or gated | Test |
| --- | --- | --- |
| The IMM proof is a Seatbelt-only input | portable | `test_the_imm_proof_is_a_seatbelt_input_and_only_a_seatbelt_input` |
| `--enforcement none` produces an unenforced attestation with **no** IMM entry and three scanned USR roots | portable | `test_t88_enforcement_none_records_unenforced_and_fails_s2` |
| `--enforcement none` still refuses key material in a USR root, and still removes the session | portable | `test_unenforced_still_refuses_a_usr_root_that_carries_key_material`, `test_a_leak_in_an_allowed_root_is_exit_four` |
| A never-admitted candidate that exists is refused outright | portable (fixture-controlled `NEVER_ADMITTED`, with positive control) | `test_a_never_admitted_candidate_that_exists_is_refused_outright` |
| `/private/var` and `/Library` are on the lists / off the candidate list | portable (module constants) | `test_private_var_and_library_are_not_admissible_by_habit` |
| The real `/private/var` is refused on this host | **gated** `@DARWIN_ONLY` — host topology | `test_the_real_private_var_is_refused_on_the_supported_host` |
| The own-descriptor directory is derived, not named | portable | `test_the_own_descriptor_exemption_is_derived_and_never_assumed` |
| I-6 exempts **only** the derived descriptor directory | portable (fixture tree, both directions) | `test_only_the_derived_descriptor_directory_is_exempted_from_i3` |
| No descriptor directory → no exemption (fail-closed) | portable | `test_no_descriptor_directory_means_no_exemption_rather_than_a_wider_proof` |
| `--imm-candidate` is repeatable, replaces, and defaults | portable (in-process wiring) | `test_the_imm_candidate_flag_replaces_the_default_list_and_defaults_to_it` |
| Seatbelt end-to-end behaviour of every one of the above | **gated** `@DARWIN_ONLY @NEEDS_SANDBOX` — pre-existing classes, unchanged | `Neg5ContractTests`, `NegativeContractTests`, `BoundaryEnumerationTests`, … |

Two of the new portable tests carry an explicit **positive control** in the same method
(`…never_admitted_candidate…` admits the root before the list refuses it;
`…only_the_derived_descriptor_directory…` runs the proof with and without the exemption),
following the file's stated T-9 convention that a denial assertion without a control proves
nothing.

---

## Review Feedback Resolution

### PR #20 external review 2026-08-27, MAJOR-1

> *"make the repository-side isolation tests and unenforced path portable to the supported CI
> hosts, or explicitly gate macOS/Seatbelt-only assertions while preserving meaningful Linux
> coverage. In particular, an enforcement=none test must not be blocked by an immutability
> proof that exists only to support Seatbelt admission, and /dev assumptions must be
> derived/fixture-controlled rather than hard-coded to one host topology."*

| Clause | Resolution | Evidence |
| --- | --- | --- |
| Unenforced path must not be blocked by a Seatbelt-only proof | **Fixed in production.** `imm_candidates_for_enforcement()`; `--enforcement none` proves nothing because it admits nothing. | `test_the_imm_proof_is_a_seatbelt_input…`, `test_t88_…` (now passing on Linux) |
| `/dev` derived or fixture-controlled | **Both.** `OWN_DESCRIPTOR_DIR` derived by `(st_dev, st_ino)` identity; the candidate list fixture-controlled via `imm_candidates` / `--imm-candidate`. | `test_the_own_descriptor_exemption_is_derived…`, `test_only_the_derived_descriptor_directory…`, `test_the_imm_candidate_flag…` |
| Gate macOS/Seatbelt-only assertions explicitly | One new `@DARWIN_ONLY` gate, on a host fact only. | §6 above |
| Preserve meaningful Linux coverage | Linux coverage **increased**: +8 executed tests, +1 skip. The rule the gated test used to carry is now asserted portably with a control. | §2 and §6 above |

### Hard constraints

| # | Constraint | Held? |
| --- | --- | --- |
| C1 | Do not weaken macOS Seatbelt semantics | **Yes.** Under `--enforcement seatbelt` `imm_candidates_for_enforcement()` returns the list unchanged; `derive_own_descriptor_dir()` returns `/dev/fd` on darwin, the previous literal. Proof, `NEVER_ADMITTED`, `MANDATORY_CARVE_OUTS`, NEG-5 pass B and every fail-closed raise are untouched. macOS suite: 0 new failures, 0 new skips. |
| C2 | No skips or no-ops standing in for security tests | **Yes.** Portable behaviour is verified with real assertions on Linux (§2 verbose run). The one new gate is a host fact whose *rule* gained portable coverage. |
| C3 | Host-specific facts behind the existing decorators | **Yes.** The existing `DARWIN_ONLY` decorator, one use, host topology only. |
| C4 | `enforcement=none` directly verified on Linux; decide and justify production vs test | **Yes — production fix**, justified in Analysis §"C4". Verified on Linux 3.11/3.12/3.13. |
| C5 | `/dev` derived or fixture-controlled | **Yes**, both mechanisms. |

### Scope prohibitions

OS-23 scope, H-1/H-2/H-4/H-5, Risk / Quality Profile / Agent Profile / Final Review lifecycle
semantics, `VERSION` and `LICENSE` are all untouched. No new PR, no merge, no force-push, no
deletion or reset of worktree state.

### Quality gate (profile absent → minimal general gate)

| Gate | Assessment |
| --- | --- |
| G1 explicit requirement violation | None. C1–C5 all met. |
| G2 result does not work | No. CI run 33080957741 on `f0c9275` is green on all three matrix jobs (`Ran 1201 tests` / `OK (skipped=29)` each). The three target failures pass on 3.11/3.12/3.13. |
| G3 severe regression | None. macOS is byte-identical in pass/fail/skip; `validate_skills`, `verify_package` and the archive build all pass. |
| G4 data loss / security / irreversible side effect | None. Every change to the security path is strictly *stricter* or *inert* on the enforced backend. |
| G5 missing validation evidence | None. Real GitHub Actions matrix result (§0) plus local runs on all three supported interpreters, with before/after counts and verbatim output. |

---

## Correction of iteration 1's false claims (F-IMPL-001)

Reviewer finding **F-IMPL-001** (G2, MAJOR, blocking) concluded that "PR #20 CI is not
fixed" and that the change leaves "every matrix job failing." That conclusion was reasonable
**given what iteration 1's report said** — and what it said was wrong in three places. The
premise is contradicted by CI run 33080957741. Each error is stated below rather than quietly
removed.

### C-1 — FALSE: "PR #20's CI will still be red after this change"

**What iteration 1 claimed:** both the "Run deterministic tests" step and the "Check
whitespace" step would fail, leaving the PR red.

**Truth:** CI run 33080957741 on `f0c9275` is **green on all three matrix jobs**, and every
step succeeded, `Check whitespace` included. Each job reports `Ran 1201 tests` /
`OK (skipped=29)`. Evidence in §0.

The "Check whitespace" step runs bare `git diff --check` — which compares the **working tree
against the index**, and is empty on a fresh checkout. It never evaluates the OS-22 commit
range, so retained-artifact history cannot make it fail. Iteration 1 conflated that step with
the test's ranged `git diff --check 1045815..HEAD`. They are different commands.

### C-2 — FALSE: the two whitespace failures were introduced after CI run 32994487855

**What iteration 1 claimed:** the offending files were introduced by coordinator artifact
commits `c059dc0` and `289d00f`, *after* run 32994487855 — offered as the explanation for why
that run listed three failures rather than five.

**Truth:** both files were already in the tree that run 32994487855 tested. Verified:

```
$ git log --oneline --diff-filter=A -- artifacts/runs/run_75c5c6046f35/REVIEW_TEST_iteration1.md
959a6b4 Confirm TEST BLOCKED independently (F-401/402/403)

$ git merge-base --is-ancestor 959a6b4 c059dc0 && echo YES
YES

$ git cat-file -e c059dc0:artifacts/runs/run_75c5c6046f35/REVIEW_TEST_iteration1.md && echo PRESENT
PRESENT

$ git log --oneline --diff-filter=A -- artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md
289d00f Record DESIGN phase gate PASS for PR #20 remediation run

$ git merge-base --is-ancestor 289d00f c059dc0 && echo YES
YES

$ git cat-file -e c059dc0:artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md && echo PRESENT
PRESENT
```

`c059dc0` is the very commit run 32994487855 tested, and both files were present in it. The
causal story was fabricated from an assumption about commit ordering that was never checked.
The three-versus-five discrepancy has a different cause entirely — C-3.

### C-3 — The real mechanism: shallow checkout makes the gate skip on CI

`scripts/test_run_logging.py:3932` pins the comparison base:

```python
WHITESPACE_GATE_BASE_COMMIT = "1045815"
```

`_require_git_range()` (`scripts/test_run_logging.py:3970`) refuses to evaluate a gate it
cannot evaluate, and **skips rather than silently passing**:

```python
probe = self._git("rev-parse", "--verify", "--quiet",
                  f"{WHITESPACE_GATE_BASE_COMMIT}^{{commit}}", cwd=REPO_ROOT)
if probe.returncode != 0:
    self.skipTest(f"base commit {WHITESPACE_GATE_BASE_COMMIT} is unreachable "
                  "(shallow or grafted checkout)")
```

`.github/workflows/ci.yml:21-22` checks out with `actions/checkout@v4` and sets **no**
`fetch-depth`, so it takes the action's default shallow (`--depth=1`) fetch. The pinned base
commit is therefore unreachable on the runner and the gate tests skip.

Verified directly, not inferred — a `--depth=1` clone reproduces the runner's condition:

```
$ git clone --depth=1 file:///Users/luminous/aiAssistedProjects/orca-skills shallow
$ git -C shallow rev-parse --verify --quiet '1045815^{commit}'   # -> rc=1, UNREACHABLE
$ (cd shallow && python3 -m unittest discover -s scripts -p 'test_run_logging.py' \
     -k RetainedReportWhitespaceExemption -v)
test_the_whitespace_gate_passes_over_the_whole_os22_range ... skipped 'base commit 1045815 is unreachable (shallow or grafted checkout)'
test_the_gate_fails_again_once_the_exemption_is_removed ... skipped 'base commit 1045815 is unreachable (shallow or grafted checkout)'
test_only_retained_reports_are_exempt ... skipped 'base commit 1045815 is unreachable (shallow or grafted checkout)'
test_the_pattern_does_not_leak_outside_the_audit_directories ... skipped 'base commit 1045815 is unreachable (shallow or grafted checkout)'
test_every_retained_artifact_still_matches_its_recorded_digest ... ok
test_the_gitattributes_rule_is_exactly_the_one_designed ... ok
test_the_hard_break_report_keeps_its_forty_trailing_space_lines ... ok
Ran 7 tests in 0.053s
OK (skipped=4)
```

Locally, where `1045815` **is** reachable (`git rev-parse --verify 1045815^{commit}` ->
`104581524c1d64165124269afb75048f935c15af`), the gate evaluates and two of those four fail.
This also explains the skip asymmetry: CI reports `skipped=29` while a full macOS checkout
reports `skipped=6`. The two directions have separate causes — CI skips the range-dependent
gate tests that macOS runs, and macOS runs the Seatbelt/darwin tests that CI skips.

*One refinement I am recording rather than glossing:* the shallow checkout causes **four**
skips in this test class, not two. Two of them (`test_only_retained_reports_are_exempt`,
`test_the_pattern_does_not_leak_outside_the_audit_directories`) pass locally, so only two
show up as local failures. The skip-count arithmetic across the whole suite involves both
this class and the darwin-gated tests; I have not attempted to reconcile the totals
test-by-test, and I am not asserting a precise decomposition of CI's 29.

---

## OUT OF SCOPE — deliberately not acted on

Two tests fail on a **full local checkout** (and skip on CI, per C-3), including at committed
`HEAD` **without** this change:

```
FAIL: test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_whitespace_gate_passes_over_the_whole_os22_range
FAIL: test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_gate_fails_again_once_the_exemption_is_removed
AssertionError: 2 != 0 : git diff --check must exit 0 over the OS-22 range
```

**Proof they pre-exist:** a clean `git clone` of `agent/final-review-observability-evaluation`
at `6cd2567` (no working diff at all) reproduces exactly these two.

**Cause:** tracked `artifacts/runs/*/REVIEW_*.md` snapshots carry Markdown hard breaks (two
trailing spaces). `.gitattributes` exempts only
`artifacts/runs/*/final_review_audit/**/report.md` — the retained Final Review report
snapshots — and nothing else. The files that trip the ranged gate are:

```
$ git diff --check 1045815..HEAD | sed 's/:.*//' | sort -u
artifacts/runs/run_028d416e596a/REVIEW_TEST_iteration1.md
artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md
artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration2.md
artifacts/runs/run_75c5c6046f35/REVIEW_DESIGN_iteration2.md
artifacts/runs/run_75c5c6046f35/REVIEW_TEST_iteration1.md
```

**Why leaving them alone is correct, not an evasion.** Every offending file belongs to a
**different run** — `run_028d416e596a`, `run_4d1c47c838db`, `run_75c5c6046f35`. The two
candidate remedies are both out of bounds:

1. **Strip the trailing whitespace.** This edits tracked, digest-bound retained review
   artifacts owned by other runs. The orchestration artifact contract forbids migrating,
   rewriting, or deleting another run's artifacts, and this dispatch repeats that
   prohibition verbatim. It is also self-defeating on the test's own terms: the class
   docstring at `scripts/test_run_logging.py:3952` states that "passing the gate is worthless
   if it was bought by editing a digest-bound file," and the sibling assertion
   `test_every_retained_artifact_still_matches_its_recorded_digest` would fail the moment
   those bytes changed. The gate is deliberately built so this remedy cannot succeed.
2. **Widen the `.gitattributes` exemption** to cover `REVIEW_*.md`. This is an OS-22
   artifact-governance decision about which artifact classes are byte-exact evidence — out of
   scope for this run, and untouchable under the standing "no OS-23 / no lifecycle-semantics"
   prohibitions.

**Consequence: none for PR #20.** CI is green (§0). This is a local-checkout-only condition
with no effect on the merge gate. Iteration 1's decision to leave these alone was right; its
stated reason was wrong, and is corrected above.

**Still a Coordinator call, but not a blocker.** If the repository wants the ranged gate to
be meaningful on CI, the fix is to give the checkout enough history (`fetch-depth: 0`) — at
which point the exemption question in (2) becomes live and must be decided deliberately.
Flagged for the Coordinator as follow-up; **not** proposed for this run.
