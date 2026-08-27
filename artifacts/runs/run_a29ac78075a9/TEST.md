# TEST -- run_a29ac78075a9, iteration 1

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS

profile_status: absent | applicable_quality_attributes: none | blocking: none
general_gate: G1 (correctness of the guarantee under test)

---

## Summary

The IMPLEMENTATION delta takes Apple's `com.apple.dt.xcode_select.tool-shim-public` off the
isolation launch line. My question was not whether it works -- that was settled -- but
whether the suite would **fail if it broke**, on a host where the original symptom can no
longer be reproduced. I confirmed the existing 17-test shim suite myself by reverting the
fix in two shapes, and found the coverage genuinely strong on the resolver but **silent on
the two places that actually exec**: the interpreter string is built inside `_run_probe()`
and inside `preflight_probe()`, and an edit that spelled `SYSTEM_PYTHON` in either of them
would put the shim back on the launch line with every existing shim test still green.

I added 5 tests (`ProbeLaunchWiringTests`) closing that gap, pinning the N-003 ordering
invariant that was previously load-bearing prose, and pinning the two admission lists by
value. Every one of the 5 is portable -- Linux CI runs the mechanism rather than skipping
it -- and every one was shown non-vacuous against a purpose-built regression in a throwaway
clone. **The N-003 ordering invariant is TRUE as written**: `_probe_python()` is evaluated
while the check *list* is built, so an unresolvable interpreter propagates out of
`preflight_probe()` with zero processes launched and `git --version` never reaches the shim.

For T3 and T5 I report **adequate, with named tests**, plus one genuine gap in T3 that I
closed (`NEVER_ADMITTED` had only 2 of its 7 members asserted, and `DEFAULT_IMM_CANDIDATES`
was not pinned by value anywhere).

---

## Analysis

### T1 -- THE CORE GUARANTEE: would the suite fail if the shim came back?

**Verdict: it would, but only through ONE assertion, and that assertion did not reach the
exec sites. Gap found and closed.**

I confirmed the Worker's and Reviewer's non-vacuity claim myself rather than taking it on
report. Method: a throwaway copy of `scripts/` under the scratchpad -- production code at
HEAD `5f7c1f0`, plus my new tests -- with a pristine snapshot restored before each run;
mutate only `scripts/review_isolation.py`; clear `__pycache__`; re-run. The real worktree
was never mutated. (An early pass of this gave misleading results from stale bytecode
between back-to-back runs; every number below is from the pycache-cleared re-run. The
separate before/after Linux comparison used `git archive HEAD` for a true pre-delta tree.)

| revert shape | tests that fail |
|---|---|
| R1: `_probe_python()` back to `SYSTEM_PYTHON if exists else sys.executable` | 3 of `ProbeInterpreterShimTests` -- `test_the_probe_interpreter_goes_through_the_resolver`, `test_the_resolved_probe_interpreter_is_never_a_tool_shim`, `test_on_darwin_the_shimmed_system_python_is_actually_resolved_away` |
| R2: `resolve_probe_interpreter()` returns the shim instead of raising | 2 -- `test_resolution_fails_closed_and_never_falls_back_to_the_shim`, `test_a_developer_dir_that_only_offers_another_shim_is_refused` |

That reproduces the reported 3-and-2 exactly. So **yes**, there is a direct assertion that
the resolved probe interpreter is not the marked shim:
`test_the_resolved_probe_interpreter_is_never_a_tool_shim` asserts
`is_tool_shim(_probe_python())` is False, and on darwin
`test_on_darwin_the_shimmed_system_python_is_actually_resolved_away` asserts it against the
real host binary and then proves the result is an interpreter that runs.

One honest qualification the reports do not make: **on Linux, two of those three R1
failures are unreachable.** Off darwin `/usr/bin/python3` is a real interpreter, so under
R1 the pre-fix body returns a real interpreter and both shim assertions still pass. I did
not reason this out -- I ran it, in `python:3.12-slim`, `--user 1000:1000`, over the R1
tree:

```
=== R1 on LINUX 3.12 ===
FAIL: test_the_probe_interpreter_goes_through_the_resolver
Ran 22 tests   FAILED (failures=1, skipped=1)
```

One failure, not three. What catches the core regression on Linux is the AST body pin
`test_the_probe_interpreter_goes_through_the_resolver` alone -- a genuine catch, but a
source-shape assertion rather than a behavioural one.

**The gap.** `_probe_python()` is not what execs. The interpreter string is interpolated at
`review_isolation.py:2225` (`_run_probe`) and `review_isolation.py:3099` (`preflight_probe`).
Nothing asserted that either call site goes through `_probe_python()`:
`ProbeSourceTests.test_the_probe_uses_the_real_launch_line` pins `wrap_command(...)` in
`_run_probe`, not the interpreter, and `preflight_probe`'s launch line had no coverage at
all. An edit spelling `SYSTEM_PYTHON` at either site restores the exact pre-fix defect --
the shim back on the launch line of every isolated dispatch -- with all 17 shim tests green.

I verified that is not hypothetical: mutation **M1** (`_run_probe` spells `SYSTEM_PYTHON`)
and **M2** (`preflight_probe` spells `SYSTEM_PYTHON`) both leave `ProbeInterpreterShimTests`
17/17 green at HEAD. On Linux 3.12, M1 is caught by exactly one test in the repository, and
it is one of mine:

```
=== M1 on LINUX 3.12 ===
FAIL: test_the_probe_launch_line_execs_the_resolved_interpreter
Ran 22 tests   FAILED (failures=1, skipped=1)
```

Closed by `test_the_probe_launch_line_execs_the_resolved_interpreter` and
`test_the_preflight_launch_line_execs_the_resolved_interpreter`, which substitute the
resolver's answer with a sentinel and read the command string actually handed to `/bin/sh`.
Behavioural, portable, and no real exec involved.

### T2 -- FAIL-CLOSED

**Verdict: adequate. Named tests below. No new test needed for the resolver itself.**

- `test_resolution_fails_closed_and_never_falls_back_to_the_shim` -- an unresolvable
  developer directory raises `IsolationError`, and the message is asserted to name both
  "tool shim" and "Command Line Tools installer". Non-vacuous: revert shape R2 fails it.
- `test_a_developer_dir_that_only_offers_another_shim_is_refused` -- a developer directory
  that supplies a *second* shim is not accepted as a resolution. Also fails under R2.
- `test_an_unreadable_candidate_is_a_hard_failure_not_a_false` -- `is_tool_shim()` on an
  unreadable path raises rather than returning `False`. This is the subtle fail-open: a
  `False` there means "not a shim" means the shim goes straight back on the launch line.

"Silently widening the profile" is covered structurally rather than by a resolver test:
the resolver returns a path and touches no admission list, and T3 below now pins both lists
by value, so a widening committed to make resolution work fails the suite.

My added `test_an_unresolvable_interpreter_raises_before_git_version_can_run` extends the
fail-closed assertion one layer out -- to `preflight_probe()`, where the raise has to beat
the shim to the exec. See T4.

### T3 -- NO SANDBOX WEAKENING

**Verdict: strong, with one real gap, now closed.**

Already covered, asserted rather than assumed:

| claim | test |
|---|---|
| profile clause ORDER is the semantics (seatbelt is last-match-wins) | `ProfileRenderingTests.test_t86_the_clauses_appear_in_the_designed_order` |
| metadata surface is a closed set, never a bare `(allow file-read-metadata)` | `test_t86b_the_metadata_surface_is_a_closed_set_not_a_global_allow` |
| clause 6 denies metadata as well as data on key-bearing roots | `test_t86d_clause_six_denies_metadata_as_well_as_data` |
| every carve-out is denied | `test_t86e_every_carve_out_is_denied`; `test_t85c...asserted_by_name` |
| a generated profile actually parses under `sandbox-exec` | `test_t86f_a_generated_profile_actually_parses` |
| recursive immutability proof rejects the F-001 shape at any depth | `test_t85_a_writable_descendant_at_any_depth_rejects_the_root`, `test_t85b...`, `test_t99_the_superseded_root_only_rule_is_rejected` |
| **proof failure is FATAL through `isolate()` AND the half-built session is removed** | `UnenforcedTests.test_an_unprovable_candidate_is_fatal_through_isolate_under_seatbelt` -- asserts `IsolationError`, that the failure names the caller's root, and that `session_base.glob(SESSION_PREFIX*)` is empty |
| never-admitted refusal happens before any proof is attempted | `test_a_never_admitted_candidate_that_exists_is_refused_outright` (fixture-controlled, portable) + `test_the_real_private_var_is_refused_on_the_supported_host` (`@DARWIN_ONLY`, real topology) |
| NEG-5 mandatory content scan runs by DEFAULT and cannot be made opt-in | `Neg5ContractTests.test_t95_every_admitted_root_carries_its_class_pass_set_and_vocabulary` and `test_t95_there_is_no_opt_in_imm_content_scan_anywhere` |
| the `imm_candidates` seam is narrowing-only and never widens | `test_a_supplied_candidate_list_replaces_the_default_and_never_widens_it` |

**The gap.** The coordinator's brief names seven never-admitted roots. Only two of them
(`/private/var`, `/Library`) were asserted, by
`test_private_var_and_library_are_not_admissible_by_habit`. `/Applications`, `/Users`,
`/opt/homebrew`, `/private/tmp` and `/System/Volumes/Data` were **not** asserted anywhere,
and `DEFAULT_IMM_CANDIDATES` was not pinned by value in any test in the repository -- so
"the delta admits no new root" was not something the suite could have told you.

Closed by `test_resolving_an_interpreter_admits_no_new_immutable_root`, which pins both
tuples by value and asserts no never-admitted member appears in the candidate list. Verified
non-vacuous both ways: mutation **M5** (add `/Library/Developer` to `DEFAULT_IMM_CANDIDATES`)
and **M6** (drop `/private/tmp` from `NEVER_ADMITTED`) each fail it.

The test also pins the fact that matters for this delta specifically: the resolver's default
target `DEFAULT_DEVELOPER_DIR` is *inside* an already-listed candidate. It is not a new root
and it is not exempt from the proof that admits that root.

### T4 -- THE GIT SHIM RESIDUAL (N-003)

**The invariant IS true as written.** I did not have to report otherwise.

`preflight_probe()` builds its check list as a literal:

```python
checks = [
    f"{shlex.quote(_probe_python())} -c {shlex.quote('print(1)')}",
    "/bin/echo preflight",
    "git --version",
    "/bin/ls .",
]
```

`_probe_python()` is evaluated while the list is constructed -- before the `for command in
checks:` loop runs `subprocess.run` even once. So both halves of the Reviewer's argument
hold: python resolution happens before any check is launched, and an unresolvable python
raises out of `preflight_probe()` with zero processes launched, so `git --version` cannot
reach the shim on the host that could not resolve python.

As the brief says, that was load-bearing prose. It is now two tests:

- `test_the_python_check_is_launched_before_the_git_check` -- records the commands actually
  handed to `/bin/sh` and asserts the resolved-interpreter command's index is lower than
  `git --version`'s.
- `test_an_unresolvable_interpreter_raises_before_git_version_can_run` -- makes
  `_probe_python()` raise and asserts `IsolationError` propagates **and** that the recorded
  command list is empty.

Non-vacuity, against the two edits that would actually falsify the argument:

| mutation | result |
|---|---|
| **M3**: `git --version` moved to the front of `checks` | both tests fail |
| **M4b**: resolution deferred *into* the loop (`if "__PY__" in command: ...`) with git first -- git runs, *then* resolution raises | 3 tests fail, including `test_an_unresolvable_interpreter_raises_before_git_version_can_run` |

M4b is the one that matters: it is the shape where the argument silently becomes false, and
before this change nothing in the repository would have noticed. I also tried a weaker
lazy-resolution shape (M4) where `_probe_python()` is still called unconditionally on the
first iteration -- that one still raises before git, correctly, and my dedicated test
correctly does *not* fire on it. The test discriminates the real regression from a benign
refactor.

I did **not** replace the git shim, per instruction.

### T5 -- REGRESSION AREAS PRESERVED

**Verdict: adequate everywhere. Nothing added. All executed live on macOS, not skipped.**

| area | coverage | executed |
|---|---|---|
| answer-key isolation | `NegativeContractTests` -- NEG-0..NEG-8, each denial paired with its own positive control, real sandboxed processes through the real `wrap_command()` against a synthetic fixture copy | 11 tests, 222.3s, OK |
| NEG-5 mandatory content scan | `Neg5ContractTests` | 2 tests, 0.7s, OK |
| evidence-bundle sanitization | `DeterministicFinalReviewAuditTests` (`artifact_digest_post_redaction` / `byte_length_post_redaction` identity) + `LogInputReportIdentityJoinTests` and `ForeignAbsolutePathAcrossThePublishedUnitTests` in `test_os22_required_tests.py` | 4 + 27 tests, OK |
| attempt-domain (F-602 area) | `AttemptDomainTests` (D-A.7 / INV-ATTEMPT-2: `0`, `-1`, `-12`, `False`, `True`, `2.0`, `"2"`, `None` all refused) + `FinalReviewArtifactPathAttemptDomainTests` | 12 + 3 tests, OK |
| provenance | `test_the_provenance_sidecar_is_a_separate_file` (sidecar separateness, `generated_at`, `metrics_digest`, and that no sidecar is written when not requested) | 1 test, OK |
| observability-neutrality | `FinalReviewObservabilityNeutralityTests` (audit module unreachable from the `e2e_harness` dispatch path) + `OrcaRuntimeDispatchPathNeutralityTests` (same tripwires on the live orchestration runtime, which is the path a real Final Review takes) | 12 + 27 tests, OK |

I did not pad any of these. They assert what the brief asks and adding to them would not
raise the chance of catching a regression.

---

## Changes

One file, tests only. **No production code was changed in this phase.**

`scripts/test_review_isolation.py` -- added `ProbeLaunchWiringTests`, 5 tests, inserted
after `ProbeInterpreterShimTests`:

1. `test_the_probe_launch_line_execs_the_resolved_interpreter` (T1)
2. `test_the_preflight_launch_line_execs_the_resolved_interpreter` (T1)
3. `test_the_python_check_is_launched_before_the_git_check` (T4 / N-003)
4. `test_an_unresolvable_interpreter_raises_before_git_version_can_run` (T4 / N-003)
5. `test_resolving_an_interpreter_admits_no_new_immutable_root` (T3)

All 5 are portable -- `subprocess.run` is replaced by a recorder, so what is under test is
the command *string*, which is the thing that decides which binary runs. No shim, no
sandbox, no real exec, no `@DARWIN_ONLY` gate.

## Modified Files / Artifacts

- `scripts/test_review_isolation.py` (+5 tests)
- `artifacts/runs/run_a29ac78075a9/TEST.md` (this file)

No production file, no other run's artifacts, no VERSION, no LICENSE, no skill/policy file.

## Validation

Every number below is one I ran and read. Where I did not verify something, I say so.

### 1. Full suite, macOS

| | tests | failures | skips | time |
|---|---|---|---|---|
| BEFORE (HEAD `5f7c1f0`) | **1220** | 2 | 6 | 305.6s |
| AFTER (clean serial run) | **1225** (+5) | **2** | **6** | 298.6s |

```
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed
      (test_run_logging.RetainedReportWhitespaceExemptionTests)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range
      (test_run_logging.RetainedReportWhitespaceExemptionTests)
Ran 1225 tests in 298.592s
FAILED (failures=2, skipped=6)
```

+5 tests, **failure set and skip count unchanged**. My delta introduces no failure and
gates nothing that was previously running.

The 2 failures are the documented pre-existing `RetainedReportWhitespaceExemptionTests`
(`test_the_gate_fails_again_once_the_exemption_is_removed`,
`test_the_whitespace_gate_passes_over_the_whole_os22_range`). They fail on a full-depth
local checkout because every offending file belongs to another run and is digest-bound;
they skip on CI, where `actions/checkout@v4` fetches `--depth=1`. Not fixed, per instruction.

**A note on my own first AFTER run, because the number was wrong and I am not hiding it.**
My first full AFTER run reported 1225 tests with 2 failures *and one error*:
`NegativeContractTests.test_t97_a_planted_key_copy_in_the_real_temp_dir_is_unreachable`,
`FileExistsError: /private/var/folders/.../T/frv_neg7_probe`. That is not a regression and
not caused by my change. `NEG7_PLANT_DIRNAME` is a fixed name in the *real* host temp
directory, and I had a real `isolate(..., plant=True)` seatbelt session running
concurrently in the background, which owned that directory at the moment the test tried to
create it. Two independent confirmations: `NegativeContractTests` run on its own passed
11/11 in 222.3s on the same tree, and the re-run below was performed strictly serially
after the seatbelt session exited.

### 2-4. Gates

- `python3 scripts/validate_skills.py` -> **PASS, 463 checks** (expected 463).
- `python3 scripts/verify_package.py` -> **PASS, 109 source files** (expected 109).
- `git diff --check` -> **clean**, rc=0.

### 5. Linux 3.11 / 3.12 / 3.13 via Docker, real recorded output

Images `python:3.11-slim`, `python:3.12-slim`, `python:3.13-slim`, repo bind-mounted.

New + existing shim coverage, all three versions, `--user 1000:1000`:

```
### py3.11 new+shim tests ###   Ran 22 tests in 0.010s   OK (skipped=1)
### py3.12 new+shim tests ###   Ran 22 tests in 0.006s   OK (skipped=1)
### py3.13 new+shim tests ###   Ran 22 tests in 0.006s   OK (skipped=1)
```

22 = 17 `ProbeInterpreterShimTests` + 5 new `ProbeLaunchWiringTests`. The single skip is
the one `@DARWIN_ONLY` real-host-topology test. **All 5 of my tests execute on Linux.**

Whole isolation module, before vs after, same container, `--user 1000:1000`, python 3.12:

```
BEFORE (git archive HEAD)   Ran 149 tests   FAILED (failures=1, errors=1, skipped=20)
AFTER  (working tree)       Ran 154 tests   FAILED (failures=1, errors=1, skipped=20)
```

149 -> 154 is exactly my +5, and **the failure set is byte-identical before and after**:
`UnenforcedTests.test_an_unprovable_candidate_is_fatal_through_isolate_under_seatbelt` and
`UnenforcedTests.test_t88_enforcement_none_records_unenforced_and_fails_s2`. My delta adds
zero failures on Linux.

Those two are artifacts of *my ad-hoc container invocation*, not of the repository: they
are present at HEAD, and real CI is green on this exact SHA (item 7). Running the same
container as root instead makes it much worse (7 failures + 3 errors, all chmod/`W_OK`
immutability-proof tests) for the obvious reason that root ignores `0o555`. I report this
so the numbers are reproducible, not as a claim about CI. 154 tests also ran on 3.11 and
3.13 with the identical 1-failure/1-error set.

### 6. A real macOS Seatbelt `isolate()` session

Real session, `enforcement='seatbelt'`, `plant=True`, against the real fixture. Built,
proved, pre-flighted, probed and attested in **853.4s**.

```
probe interpreter = /Library/Developer/CommandLineTools/Library/Frameworks/
                    Python3.framework/Versions/3.9/bin/python3.9
is_tool_shim(/usr/bin/python3) = True
is_tool_shim(resolved)         = False

scope_enforcement = seatbelt
properties        = {'S1': 'PASS', 'S2': 'PASS', 'S3': 'PASS'}

NEG-0 PASS   NEG-1 PASS   NEG-2 PASS   NEG-3 PASS   NEG-4 PASS
NEG-5 PASS   roots=11  content_scanned=182986  hits=0
NEG-6 PASS   NEG-7 PASS   NEG-8 PASS
```

**The pre-flight log is the direct evidence for this whole delta**, so it is reproduced
verbatim:

```
$ /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9 -c 'print(1)'
rc=0
1

$ /bin/echo preflight
rc=0
preflight

$ git --version
rc=0
git version 2.50.1 (Apple Git-155)
git: error: couldn't create cache file '/var/folders/.../T/xcrun_db-b8Ac5NB1' (errno=Operation not permitted)
git: error: couldn't create cache file '/var/folders/.../T/xcrun_db-VWMNzbMa' (errno=Operation not permitted)

$ /bin/ls .
rc=0
artifacts
policy
subject
```

The launch line execs the resolved CLT interpreter, not `/usr/bin/python3`. `git --version`
still runs the shim -- the retained, instructed N-003 residual -- and still exits 0; its
`couldn't create cache file` stderr is the documented BENIGN classification and must not be
"fixed" by granting write access to the host per-user temp directory.

**No sandbox weakening, measured on the generated profile rather than assumed.** Admitted
Class IMM roots were exactly the eight `DEFAULT_IMM_CANDIDATES`, no more:

```
IMM: /bin /sbin /private/etc /dev /private/var/select /usr /System
     /Library/Developer/CommandLineTools
```

For each of the seven never-admitted roots I grepped the generated `scope.sb` for an
`(allow ... (subpath "<root>"))` admission. **All seven: zero.** The grep is valid rather
than vacuous -- the same pattern finds `/usr`, `/System`, `/bin` and
`/Library/Developer/CommandLineTools` at count 1 each.

Three of the seven appear in the profile at all, and in every case as a single-node
`(literal ...)` in the metadata clause, never a `(subpath ...)`:

| root | appearance |
|---|---|
| `/Library` | `(literal "/Library")` in the `allow file-read-metadata` clause |
| `/private/var` | `(literal "/private/var")` in the same clause |
| `/private/tmp` | `(literal "/private/tmp")` in the same clause |

That is G.4 clause 2 working as designed: an ancestor needs traversal metadata so the deny
beneath it can be reached, and `literal` grants metadata on that one directory node only,
never on its contents. It is the behaviour `TraversalSetTests` covers. `/Applications`,
`/Users`, `/opt/homebrew` and `/System/Volumes/Data` do not appear in the profile at all.

### 7. GitHub Actions on the pushed commit `5f7c1f0`

I looked, rather than asserting.

```
$ gh run list --branch agent/final-review-observability-evaluation --limit 5
completed  success  ...  CI  agent/final-review-observability-evaluation  pull_request  33094956725  2m7s   2026-08-27T16:46:21Z
completed  success  ...  33088619105 / 33087226248 / 33080957741
completed  failure  ...  32994487855  (2026-08-26, superseded)

$ gh run view 33094956725 --json headSha,status,conclusion,jobs
headSha 5f7c1f0cb7016dfef54b91a05d3b1e8ad1a14366  completed  success
  validate (3.12) success
  validate (3.11) success
  validate (3.13) success
```

**Remote CI is GREEN on `5f7c1f0` across 3.11/3.12/3.13.** When I first checked, this run
was still `in_progress` with 3.11 outstanding; it completed successfully during this phase.
This closes Reviewer note **N-002**, which was written while `5f7c1f0` was still local-only
and the newest green run was on baseline `90e2071`. `origin/agent/final-review-observability-evaluation`
is now `5f7c1f0`.

My TEST-phase commit is **not** pushed (per instruction, the Coordinator pushes), so CI has
**not** run on it. I did not verify remote CI for my own commit.

## Unit Tests / Testing Strategy

### MANDATORY TEST GATE (SKILL section 14)

`UNIT_TEST_STATUS: PASS`. This phase changed **no** production code, so the gate applies to
the tests themselves: 5 tests added, all executed, all PASS on macOS and on Linux
3.11/3.12/3.13.

### Strategy: behavioural, portable, and proven non-vacuous

Three principles, each chosen against a specific way this suite could have lied.

**1. Assert the launch line, not the helper.** The defect was never "`_probe_python()`
returns the wrong thing" -- it was "the isolation path execs a shim". Those are only the
same statement while the call sites route through the helper, and nothing asserted that.
So the two wiring tests substitute the resolver's answer with a sentinel path and read the
command string actually handed to `/bin/sh`, via a `subprocess.run` recorder. That makes the
subject of the test the string that decides which binary runs.

**2. Portable by construction, not by gate.** No `@DARWIN_ONLY` on any of the 5. No shim is
needed, because the resolver is patched; no sandbox is needed, because nothing is executed.
This matters because on Linux the pre-existing shim suite loses two of its three catches
for the core regression (`/usr/bin/python3` is a real interpreter there, so the pre-fix body
returns something that is genuinely not a shim). The 5 new tests catch their regressions on
every platform.

**3. Every test earns its place by failing.** I did not accept any new test until I had
watched it fail against a purpose-built regression, in a throwaway scratchpad copy of
`scripts/`, with `__pycache__` cleared between runs. The full matrix:

| mutation to `review_isolation.py` | tests that FAIL |
|---|---|
| **M1** `_run_probe` interpolates `SYSTEM_PYTHON` | `test_the_probe_launch_line_execs_the_resolved_interpreter` |
| **M2** `preflight_probe` interpolates `SYSTEM_PYTHON` | `test_the_preflight_launch_line_execs_the_resolved_interpreter`, `test_the_python_check_is_launched_before_the_git_check`, `test_an_unresolvable_interpreter_raises_before_git_version_can_run` |
| **M3** `git --version` moved to the front of `checks` | `test_the_preflight_launch_line_execs_the_resolved_interpreter`, `test_the_python_check_is_launched_before_the_git_check` |
| **M4b** resolution deferred into the loop, git first | all three preflight tests |
| **M5** `/Library/Developer` added to `DEFAULT_IMM_CANDIDATES` | `test_resolving_an_interpreter_admits_no_new_immutable_root` |
| **M6** `/private/tmp` dropped from `NEVER_ADMITTED` | `test_resolving_an_interpreter_admits_no_new_immutable_root` |

M1 and M2 are the important rows: **both leave all 17 pre-existing shim tests green.** That
is the gap, measured rather than argued.

I also ran a deliberate negative control. **M4**, a *benign* lazy-resolution refactor where
`_probe_python()` is still called unconditionally on the first loop iteration, still raises
before `git --version` -- and `test_an_unresolvable_interpreter_raises_before_git_version_can_run`
correctly does **not** fire on it. The test discriminates the real regression (M4b) from a
refactor that preserves the invariant, rather than pinning incidental source shape.

### What I deliberately did NOT add

Per the brief -- redundant tests that assert nothing new are worse than an honest
"adequate":

- **No new answer-key / NEG / evidence-bundle / provenance / attempt-domain / neutrality
  tests.** All five T5 areas have live, executed, meaningful coverage (table in T5 above).
  `NegativeContractTests` alone runs 11 real sandboxed processes through the real
  `wrap_command()` in 222s. Adding to it would not raise the chance of catching anything.
- **No second fail-closed test for the resolver.** T2 is covered by three existing tests,
  two of which I watched fail under revert shape R2.
- **No source-text assertion duplicating `test_the_probe_interpreter_goes_through_the_resolver`.**
  The wiring tests supersede it behaviourally; the AST pin stays because it is the one
  assertion that catches R1 on Linux.

## Review Feedback Resolution

### N-001 -- historical mechanism is INFERENCE, not proof

**Preserved, deliberately and without dilution.** The reported Command Line Tools dialog
does **not** reproduce on this host: `/var/log/install.log` records the Command Line Tools
being reinstalled 2026-08-27 21:02-21:08, inside the OS-22 window, so the failing host
state is gone. I did not reproduce it and I did not try.

What is proven and what is not, stated plainly:

- **PROVEN**: `/usr/bin/python3` carries the `com.apple.dt.xcode_select.tool-shim-public`
  marker; the resolved interpreter does not; after the fix the probe and pre-flight launch
  lines exec the resolved interpreter and not the marked shim.
- **NOT PROVEN, and labelled INFERENCE**: that the shim's `_xcselect_invoke_xcrun` import
  is what produced the operator's dialog on the pre-21:02 host state. The import-table
  observation constrains where such a prompt *could* come from in that binary; it does not
  reconstruct the lost host state, and no artifact of mine says otherwise.
- **The Coordinator's original hypothesis (denied `/private/var` + `/Applications` broke
  the shim's developer-dir resolution) was FALSIFIED** and the fix does not rest on it. It
  rests on removing the shim from the launch line, which eliminates the condition regardless
  of which explanation was right.

I have not upgraded any inference into a claim anywhere in this artifact.

### N-002 -- remote CI had not tested the implementation

**RESOLVED during this phase.** `5f7c1f0` is now pushed and CI run **33094956725** on
`headSha 5f7c1f0` completed **success** on 3.11, 3.12 and 3.13. Verified with
`gh run view`, not assumed. See Validation item 7. My own TEST commit is unpushed by
instruction and has no CI result.

### N-003 -- the git shim residual

**Retained, as instructed, and now bounded in test form rather than in prose.**

I did not replace `git --version`. I accept the reasoning: the check exists to prove the
*agent's* real git works inside the sandbox, and substituting the resolved binary would make
it prove something else.

What I changed is that the argument is no longer unguarded. Both of its load-bearing
premises are now assertions:

1. *python resolution happens before any check is launched* --
   `test_the_python_check_is_launched_before_the_git_check`
2. *an unresolvable python raises before `git --version` runs* --
   `test_an_unresolvable_interpreter_raises_before_git_version_can_run`

**The invariant is TRUE as written** -- I checked rather than assuming, and found no reason
to report otherwise. `_probe_python()` is evaluated during construction of the `checks` list
literal at `review_isolation.py:3099`, before the `for command in checks:` loop reaches
`subprocess.run` even once. Under a raising `_probe_python()`, the recorded command list is
empty: `git --version` never executes.

The residual the Reviewer named is unchanged and still real -- *a contrived developer
directory that supplies a usable Python but not Git could still reach shim behaviour during
preflight*. It is now bounded: it can be reached only through a developer directory that
resolves Python, never through the reported unresolvable-Python path, and an edit that
breaks that boundary fails M3/M4b above.

## Observations (not acted on -- outside this phase's objective)

**O-1. A failing pre-flight is fatal in production but that fatality is untested.**
`isolate()` raises `IsolationError` when `preflight["ok"]` is false
(`review_isolation.py:3008-3012`), which is the correct fail-closed behaviour and the
opposite of a silently widened profile. I found **no test** asserting it -- neither that
`isolate()` raises on a failing pre-flight, nor that the half-built session is removed when
it does (the sibling assertion *does* exist for the immutability-proof path, in
`test_an_unprovable_candidate_is_fatal_through_isolate_under_seatbelt`).

I did not add it. It sits in F-403's area rather than in this delta, and the brief's T2 is
about `resolve_probe_interpreter()`, which is adequately covered. It would be cheap and
portable to close (patch `preflight_probe` to return `ok=False`, assert `isolate()` raises
and the session base is empty). Flagging it for the Coordinator to schedule or decline
rather than widening scope unasked.

**O-2. `NEG7_PLANT_DIRNAME` is a fixed name in the real host temp directory**, which makes
`NegativeContractTests` non-parallel-safe against any concurrent real `isolate(plant=True)`.
This bit me once (Validation item 1) and cost a full suite re-run. It is a pre-existing
property, unrelated to this delta, and I did not change it.

## Constraint Compliance

- CLT **not** reinstalled. Sandbox **not** weakened -- both admission lists are now pinned
  by value, which is strictly stronger than before.
- No prior run's artifacts touched. `run_f71a83d7ebe8`, `run_028d416e596a`,
  `run_75c5c6046f35` and every other run directory are unmodified; `git status` shows only
  `scripts/test_review_isolation.py` modified and this run's own artifacts.
- Linux CI coverage **increased** (+5 tests running, 0 gated). The only `@DARWIN_ONLY` test
  in the shim area is the pre-existing real-host-topology one, which is a genuine host
  necessity.
- No OS-23 scope change, no H-1/H-2/H-4/H-5 conclusion, no Risk / Quality Profile / Agent
  Profile / Final Review lifecycle semantics change.
- No VERSION change, no LICENSE change, no new PR, no merge, no force-push, no push.
- **The macOS CLT installer GUI was never triggered.** No shim was executed at any point:
  shim detection is a byte scan (`is_tool_shim`), the mutation experiments patched Python
  objects rather than invoking binaries, and `developer_dir_candidates()` deliberately reads
  `/var/db/xcode_select_link` instead of asking `xcode-select`.

