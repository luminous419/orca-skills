# IMPLEMENTATION — run_f71a83d7ebe8 (iteration 1)

Phase: IMPLEMENTATION
Role: WORKER
Branch: `agent/final-review-observability-evaluation` (Draft PR #20)
Task: `task_fdff71d8b2ac` / Dispatch: `ctx_64de550407b3`

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS

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

Two **pre-existing, out-of-scope** failures remain on every platform (`git diff --check`
over the OS-22 range, caused by coordinator-committed `artifacts/runs/*/REVIEW_*.md`
snapshots carrying Markdown hard breaks). They are proven below to exist at committed
`HEAD` without this change and are **escalated to the Coordinator, not acted on**.

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

The baseline's five failures were the three CI failures plus the two pre-existing whitespace
failures. The three CI failures are **gone on all three interpreters**. Verbatim baseline
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
| G2 result does not work | No. The three target failures pass on 3.11/3.12/3.13. |
| G3 severe regression | None. macOS is byte-identical in pass/fail/skip; `validate_skills`, `verify_package` and the archive build all pass. |
| G4 data loss / security / irreversible side effect | None. Every change to the security path is strictly *stricter* or *inert* on the enforced backend. |
| G5 missing validation evidence | None. Real Linux runs on all three supported interpreters, with before/after counts and verbatim output. |

---

## OUT OF SCOPE — escalated, not acted on

Two further tests fail on **every** platform, including at committed `HEAD` **without** this
change:

```
FAIL: test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_whitespace_gate_passes_over_the_whole_os22_range
FAIL: test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_gate_fails_again_once_the_exemption_is_removed
AssertionError: 2 != 0 : git diff --check must exit 0 over the OS-22 range
```

**Proof they pre-exist:** a clean `git clone` of `agent/final-review-observability-evaluation`
at `6cd2567` (no working diff at all) reproduces exactly these two, on macOS and in the
container.

**Cause:** tracked `artifacts/runs/*/REVIEW_*.md` snapshots carry Markdown hard breaks (two
trailing spaces). `.gitattributes` exempts only
`artifacts/runs/*/final_review_audit/**/report.md`. Offending files were introduced by
coordinator artifact commits — `c059dc0` (`artifacts/runs/run_028d416e596a/REVIEW_TEST_iteration1.md`)
and `289d00f` (`artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md`) — both after CI
run 32994487855, which is why the authoritative failure list in this dispatch names three
failures rather than five.

**Consequence:** PR #20's CI will still be red after this change — both the "Run deterministic
tests" step (these two tests) and the separate "Check whitespace" step (`git diff --check`).

**Not acted on, deliberately.** The two candidate remedies are (a) stripping trailing
whitespace from Reviewer-authored report snapshots, which the repository treats as byte-exact
immutable evidence under DESIGN A.3, or (b) widening the `.gitattributes` exemption, which is
an OS-22 scope decision. Both are artifact-governance calls that belong to the Coordinator,
and neither is within this dispatch's stated scope (which names the three isolation failures
as authoritative and forbids scope changes). **Coordinator decision requested.**
