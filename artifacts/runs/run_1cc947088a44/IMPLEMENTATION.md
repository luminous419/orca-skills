# IMPLEMENTATION — run_1cc947088a44

Phase: IMPLEMENTATION · Iteration 1 · Risk: high
Branch: `agent/final-review-observability-evaluation` (Draft PR #20)
Baseline HEAD: `fc4f4a8`

---

## Summary

The pre-flight's `git --version` was removed from the launch line and replaced with a
resolved real git. `/usr/bin/git` and `/usr/bin/python3` are the **same inode** on this
host, so the previous run's Python-only fix left the identical shim reachable through
git's own call site — the door the user actually walked through in another project.

The fix is a **generalization, not a second special case**. `resolve_probe_interpreter()`
was refactored into a tool-agnostic `resolve_developer_tool(system_path, unshimmed)`;
`resolve_probe_interpreter()` and the new `resolve_probe_git()` are thin specializations
over it, and `_probe_git()` is wired into `preflight_probe()` the way `_probe_python()`
already was. Off the shimmed path — every Linux, every unshimmed darwin host — the
resolver hands the caller's spelling straight back, so the git check string stays the
byte-identical `git --version` and Linux behaviour does not move.

13 tests were added, four of which fail if the raw `git --version` spelling is put back at
the call site. That non-vacuity was demonstrated by actually reverting the fix and
recording the failures (§7).

`STATUS: COMPLETE`
`UNIT_TEST_STATUS: PASS`

---

## Analysis

### A1. The previous run's residual argument, and why it did not bound the reported failure

Run `run_a29ac78075a9` resolved `/usr/bin/python3` past the shim and deliberately retained
`git --version`. Its IMPLEMENTATION Reviewer accepted the residual as bounded (`N-003`,
MINOR / non-blocking) on this reasoning:

> `_probe_python()` resolves before any check is launched, and an unresolvable Python
> raises before `git` can execute.

That statement is **true and it bounds the wrong case**. It proves the *python* trigger
cannot recur *through* the git door. It says nothing about git triggering the installer on
its own: in the ordinary case — python resolves fine, which is what happens on this host —
`git --version` still execs the shim, from its own call site, with no ordering guarantee
standing anywhere near it. An ordering guarantee about one tool is not a statement about
another tool's call site. That is the class of error this run had to not repeat, and it is
recorded in the source at `TOOL_SHIM_MARKER` so the next reader meets it there.

### A2. Grounding evidence, re-verified on this host

E1 and E2 from the dispatch were re-measured rather than taken on trust, and extended.
Nothing below executed a shim — every classification is a **byte scan for
`TOOL_SHIM_MARKER` plus `os.stat`**, performed by the running (Anaconda, non-shim)
interpreter:

```
    SHIM  inode=1152921500312571585 links=78 size=118928  /usr/bin/git
    SHIM  inode=1152921500312571585 links=78 size=118928  /usr/bin/python3
    SHIM  inode=1152921500312571585 links=78 size=118928  /usr/bin/cc
    SHIM  inode=1152921500312571585 links=78 size=118928  /usr/bin/clang
    SHIM  inode=1152921500312571585 links=78 size=118928  /usr/bin/make
    SHIM  inode=1152921500312571585 links=78 size=118928  /usr/bin/dyld_info
    real  inode=1152921500312571396 links=1  size=101136  /bin/echo
    real  inode=1152921500312571414 links=1  size=154624  /bin/ls
    real  inode=1152921500312571432 links=1  size=101232  /bin/sh
    real  inode=1152921500312572720 links=1  size=102560  /usr/bin/sandbox-exec
    real  inode=1152921500312573134 links=1  size=117392  /usr/bin/xcrun
    real  inode=1152921500312573131 links=1  size=119104  /usr/bin/xcode-select
    real  inode=53154037            links=1  size=7604272 /Library/Developer/CommandLineTools/usr/bin/git
  ABSENT                                                  /usr/local/bin/git
```

Three things follow, and they are the whole design:

1. **E1 confirmed.** `git` and `python3` are one hardlinked file with 78 links. "We fixed
   python" was never a statement about a python-specific problem.
2. **E2 confirmed.** The real git lives at `/Library/Developer/CommandLineTools/usr/bin/git`
   — already inside `DEFAULT_IMM_CANDIDATES`, already subject to the recursive immutability
   proof. Resolving to it admits nothing new (G4).
3. **The general case is not hypothetical.** `cc`, `clang`, `make` and `dyld_info` are the
   same inode. A python-shaped resolver would have to be re-generalized for whichever of
   those appears on a launch line next, which is why `resolve_developer_tool()` decides
   from the FILE, never from the tool's name.

`/usr/local/bin/git` does not exist on this host, so a bare `git` inside the sandbox
resolves through `PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`, per `wrap_command()`) to
`/usr/bin/git` — the shim. The defect path is confirmed end to end.

### A3. Independent confirmation that the OLD launch line really did exec the shim

The previous run's own recorded `control/probes/preflight.log` is the proof, and it was
sitting in its artifact the whole time:

```
$ git --version
rc=0
git version 2.50.1 (Apple Git-155)
git: error: couldn't create cache file '/var/folders/nz/.../T/xcrun_db-4NI6K8pQ' (errno=Operation not permitted)
git: error: couldn't create cache file '/var/folders/nz/.../T/xcrun_db-e8ymLu31' (errno=Operation not permitted)
```

`xcrun_db` is the **shim's** cache. Only `xcselect_invoke_xcrun` writes it. Those two
stderr lines are a receipt that the pre-flight was exec'ing the tool shim on every isolated
dispatch. §6 shows the same log after the fix, with the `xcrun_db` lines gone — which is
positive evidence the shim is no longer on the launch path, not merely an assertion that it
should not be.

### A4. Why substituting the resolved git does not weaken what the check proves (G2)

The retained-shim argument said substituting the binary "would make the check prove
something else". It does not, because the shim's only job **is** to exec
`<developer dir>/usr/bin/git`: `dyld_info -imports` shows the shim importing exactly one
libxcselect symbol, `_xcselect_invoke_xcrun`. So the resolved path is the same real git the
agent's own `git` would have ended up running — reached without the exec that can put an
installer dialog on the operator's screen. The check still runs a real git, in the sandbox,
through the real `wrap_command()`, and §6 shows it returning
`git version 2.50.1 (Apple Git-155)` from inside a real seatbelt session.

### A5. Scope statement — which launch paths this covers, honestly

Required by the dispatch, and enforced by a test rather than left as prose
(`test_no_fixed_preflight_check_execs_a_tool_shim`):

| Launch path | Reaches a shim? | Covered by this change |
| --- | --- | --- |
| `preflight_probe()` python check | was YES (fixed in run_a29ac78075a9) | yes — `_probe_python()` |
| `preflight_probe()` git check | **YES — this run's finding** | **yes — `_probe_git()`** |
| `preflight_probe()` `/bin/echo preflight` | no — absolute path, measured non-shim | n/a |
| `preflight_probe()` `/bin/ls .` | no — absolute path, measured non-shim | n/a |
| `_run_probe()` interpreter | was YES (fixed in run_a29ac78075a9) | yes — `_probe_python()` |
| `wrap_command()` itself (`/bin/sh`, `/usr/bin/sandbox-exec`) | no — both measured non-shim | n/a |
| `orca_check_probe()` (`orca`) | not an Apple developer tool; carries no `TOOL_SHIM_MARKER` path | out of scope, unchanged |
| `preflight_probe(agent_command=...)` | **possible — NOT covered** | no, deliberately |

**`agent_command` is not covered and cannot be.** It is an arbitrary operator-supplied
string; if an operator passes `cc ...` or `make ...` — both the same shim inode on this host
— that exec is theirs, and rewriting an opaque command string is not something this layer
can do safely. What the change does provide is the tool-agnostic seam
(`resolve_developer_tool()`) to route it through if that ever becomes a real launch path.
This is stated in `preflight_probe()`'s docstring, not only here.

So: **the fix covers the general case at the mechanism level and both known tools at the
call-site level; it does not cover operator-supplied `agent_command`.**

### A6. What I did NOT verify

Stated explicitly, because accuracy is part of the deliverable.

- I did **not** re-run `dyld_info -imports` on the shim or on `/usr/bin/xcode-select` this
  run. `/usr/bin/dyld_info` is itself the shim inode (measured above), so running it would
  have risked the exact dialog the dispatch forbids. The import-table claims in the source
  commentary are the previous run's measurement, carried forward unchanged and labelled as
  such; my own claims rest on marker scans and inode identity, which I did measure.
- I did **not** reproduce the Command Line Tools installer dialog. The dispatch permitted at
  most one deliberate reproduction; it was not necessary, because §A3's `xcrun_db` stderr in
  the previous run's own log is direct evidence the shim was executing, obtained without
  provoking any GUI.
- I did **not** verify behaviour on an Xcode-only host (no `/Library/Developer/CommandLineTools`).
  The `DEVELOPER_DIR` escape hatch is tested with synthetic developer directories only.
- I did **not** push. The Coordinator pushes.

---

## Changes

### `scripts/review_isolation.py`

1. **`SYSTEM_GIT` / `GIT_COMMAND` constants.** `GIT_COMMAND = "git"` is a bare name on
   purpose: the pre-flight proves the git the *agent* reaches, and the agent reaches it
   through `PATH`.
2. **`resolve_developer_tool(system_path, unshimmed, *, developer_dirs=None)`** — the
   generalized resolver, carrying the whole of the previous `resolve_probe_interpreter()`
   body with every python-specific assumption removed. `unshimmed` is the spelling the call
   site would launch if shims did not exist, and it is returned unchanged whenever
   `system_path` is absent or is a real tool. Same fail-closed raise; the message now names
   the tool (`the real git behind it`) instead of hard-coding "interpreter", and still
   carries the `tool shim` / `Command Line Tools installer` wording existing tests pin.
3. **`resolve_probe_interpreter()`** — now a thin specialization. The only python-specific
   thing left is its unshimmed spelling (`/usr/bin/python3` when real, `sys.executable` when
   absent), which is exactly the pre-existing behaviour.
4. **`resolve_probe_git()`** — the git specialization, unshimmed spelling `GIT_COMMAND`.
5. **`_probe_git()`** — mirrors `_probe_python()`; one statement, pinned by test.
6. **`preflight_probe()`** — `"git --version"` → `f"{shlex.quote(_probe_git())} --version"`.
   This is the one-line change that closes the finding; everything above exists to make it
   correct, fail-closed and general.
7. **Commentary.** The `TOOL_SHIM_MARKER` block's "Deliberately NOT extended to the
   pre-flight's `git --version`" paragraph is replaced by the measurement that refutes it
   and by a statement of the reasoning error, so the next reader cannot re-derive the narrow
   argument from the source. `preflight_probe()`'s docstring carries the §A5 scope statement.

### `scripts/test_review_isolation.py`

13 new tests. See §"Unit Tests / Testing Strategy".

### Not changed

No sandbox profile, readable-set, answer-key-isolation, bundle-sanitization,
attempt-domain, provenance or observability logic was touched (G4, G7). No admission list
moved — `test_resolving_an_interpreter_admits_no_new_immutable_root` pins both lists by
value and still passes. No OS-23 / risk / profile / lifecycle semantics changed. No VERSION
or LICENSE change. No new PR, no merge, no force-push. No prior run's artifacts modified.

---

## Modified Files / Artifacts

| Path | Change |
| --- | --- |
| `scripts/review_isolation.py` | generalized resolver + `resolve_probe_git()` + `_probe_git()` + call-site fix + commentary |
| `scripts/test_review_isolation.py` | 13 new tests; 2 existing N-003 tests retargeted |
| `artifacts/runs/run_1cc947088a44/IMPLEMENTATION.md` | this artifact |

---

## Validation

### 1. Full suite, macOS

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1238 tests in 290.850s
FAILED (failures=2, skipped=6)

$ python3 -m unittest discover -s scripts -p 'test_*.py' 2>&1 | grep -E "^(FAIL|ERROR):"
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
```

1225 baseline + 13 new = **1238**. The only two failures are the **expected, pre-existing**
`RetainedReportWhitespaceExemptionTests` pair, which skip on CI because
`actions/checkout@v4` fetches `--depth=1`. Not fixed: every offending file belongs to
another run and is digest-bound.

### 2. `validate_skills.py`

```
$ python3 scripts/validate_skills.py
Skill validation PASSED (463 checks)
```

### 3. `verify_package.py`

```
$ python3 scripts/verify_package.py
Package verification PASSED (109 source files)
```

### 4. `git diff --check`

```
$ git diff --check
(no output)
```

This artifact has no trailing blank line at EOF.

### 5. Linux 3.11 / 3.12 / 3.13 via Docker — FULL suite, real recorded output

**These are full-suite numbers, not a filtered subset.** The `grep` below filters only the
*display* of an already-complete run; the `Ran N tests` line is that run's own total.

```
$ for v in 3.11 3.12 3.13; do
    docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/w -w /w -e HOME=/tmp \
      python:$v python3 -m unittest discover -s scripts -p 'test_*.py'
  done

########## python:3.11 (non-root, CI-like) ##########
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1238 tests in 179.556s
FAILED (failures=2, skipped=28)

########## python:3.12 (non-root, CI-like) ##########
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1238 tests in 211.044s
FAILED (failures=2, skipped=28)

########## python:3.13 (non-root, CI-like) ##########
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1238 tests in 214.757s
FAILED (failures=2, skipped=28)
```

All three: **1238 run, 2 failures**, and those two are the same
`RetainedReportWhitespaceExemptionTests` pair that fails locally on macOS and skips on CI
under `--depth=1`. `skipped=28` vs macOS's `6` is the 22 `@DARWIN_ONLY` tests, two of which
are mine (§"Portability").

#### 5a. A correction to my own first Docker run, recorded because it matters

My first Docker invocation omitted `--user` and therefore ran as **root inside the
container**, which produced `failures=8, errors=1` — seven of them `ImmutabilityProofTests`
(root can write into a tree the proof needs to be immutable) plus the whitespace pair. I did
not assume those were pre-existing. I **measured** it, by stashing my two files and re-running
the identical root invocation against the baseline:

```
$ git stash push -- scripts/review_isolation.py scripts/test_review_isolation.py
$ docker run --rm -v "$PWD":/w -w /w python:3.11 python3 -m unittest discover -s scripts -p 'test_*.py'
ERROR: test_a_never_admitted_candidate_that_exists_is_refused_outright (ImmutabilityProofTests...)
FAIL:  test_a_genuinely_immutable_tree_passes (ImmutabilityProofTests...)
FAIL:  test_narrowing_carves_out_what_it_cannot_certify_and_never_what_is_mutable (ImmutabilityProofTests...)
FAIL:  test_only_the_derived_descriptor_directory_is_exempted_from_i3 (ImmutabilityProofTests...)
FAIL:  test_t85_a_writable_descendant_at_any_depth_rejects_the_root (ImmutabilityProofTests...)
FAIL:  test_t85b_a_writable_directory_at_any_depth_rejects_the_root (ImmutabilityProofTests...)
FAIL:  test_t99_the_superseded_root_only_rule_is_rejected (ImmutabilityProofTests...)
FAIL:  test_the_gate_fails_again_once_the_exemption_is_removed (RetainedReportWhitespaceExemptionTests...)
FAIL:  test_the_whitespace_gate_passes_over_the_whole_os22_range (RetainedReportWhitespaceExemptionTests...)
Ran 1225 tests in 57.583s
FAILED (failures=8, errors=1, skipped=26)
$ git stash pop
```

**Identical nine failures at baseline (1225 tests), so all nine are pre-existing and
root-caused, none of them mine** — and the delta my change contributes is `+13 tests, +0
failures`. Re-running non-root (which is what GitHub Actions does) leaves only the expected
whitespace pair, as shown above. The numbers reported in §5 are the non-root ones.

### 6. A real macOS seatbelt `isolate()` session

```
$ isolate("run_1cc947088a44_step6", fixture=scripts/fixtures/final_review_eval,
          enforcement="seatbelt")

resolved probe git    : /Library/Developer/CommandLineTools/usr/bin/git
resolved probe python : /Library/Developer/CommandLineTools/Library/Frameworks/
                        Python3.framework/Versions/3.9/bin/python3.9

scope_enforcement: seatbelt
PROPERTIES: {"S1": "PASS", "S2": "PASS", "S3": "PASS"}
SESSION:    /private/var/folders/nz/.../T/frv_iso_vd3poa2s
```

Read back out of the session's own `control/ISOLATION.json`:

```
NEG-0 PASS  NEG-1 PASS  NEG-2 PASS  NEG-3 PASS  NEG-4 PASS
NEG-5 PASS  NEG-6 PASS  NEG-7 PASS  NEG-8 PASS
```

The session built, the recursive immutability proof passed, the profile rendered, the
pre-flight ran the real launch line, all nine negative probes passed and the attestation was
written.

**`control/probes/preflight.log` — the deliverable:**

```
$ /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9 -c 'print(1)'
rc=0
1

$ /bin/echo preflight
rc=0
preflight

$ /Library/Developer/CommandLineTools/usr/bin/git --version
rc=0
git version 2.50.1 (Apple Git-155)

$ /bin/ls .
rc=0
artifacts
policy
subject
```

Two things to read off it, side by side with the previous run's log quoted in §A3:

1. The git check now execs **`/Library/Developer/CommandLineTools/usr/bin/git`**, not the
   shim, and it still returns `git version 2.50.1 (Apple Git-155)` — the same real git,
   rc=0, from inside the sandbox, through the real `wrap_command()`. **G2 preserved.**
2. **The two `git: error: couldn't create cache file '.../xcrun_db-...'` lines are gone.**
   `xcrun_db` is the shim's cache and only `xcselect_invoke_xcrun` writes it. Their
   disappearance is positive, observed evidence that the shim is no longer being exec'd —
   not merely an assertion that it should not be. **G1 met.**

**No widening (G4).** The session's `readable_set` admits exactly the eight
`DEFAULT_IMM_CANDIDATES` roots and no others:

```
/Library/Developer/CommandLineTools   /System   /bin   /dev
/private/etc   /private/var/select   /sbin   /usr
```

`/Library/Developer/CommandLineTools` was already on that list before this change and still
had to pass the recursive immutability proof to be admitted. Resolving a tool inside it
admits nothing; the proof admits it, exactly as before.

The session was torn down after the evidence above was read.

### 7. The regression test is NON-VACUOUS — demonstrated by reverting the fix

The fix was reverted at the call site (`f"{shlex.quote(_probe_git())} --version"` → the raw
`"git --version"`), the tests were run, and the fix was restored. **Actual recorded output:**

```
>>> FIX REVERTED at the call site
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' -k ProbeLaunchWiring

FAIL: test_an_unresolvable_git_raises_before_any_check_can_run
AssertionError: IsolationError not raised

FAIL: test_no_fixed_preflight_check_execs_a_tool_shim
AssertionError: False is not true : 'git' is a PATH lookup, which on darwin can land on the shim

FAIL: test_the_preflight_git_check_execs_the_resolved_git
AssertionError: False is not true : the resolved git is not on the launch line:
  [... 'exec /usr/bin/sandbox-exec -f .../scope.sb git --version', ...]

FAIL: test_the_python_check_is_launched_before_the_git_check
AssertionError: /nonexistent/resolved/scm-tool is on no recorded launch line:
  [... 'exec /usr/bin/sandbox-exec -f .../scope.sb git --version', ...]

Ran 8 tests in 0.005s
FAILED (failures=4)

>>> FIX RESTORED
Ran 8 tests in 0.005s
OK
```

**Four tests fail, and the recorded launch line in the failure message literally shows
`scope.sb git --version` — the raw shim spelling back on the launch path.** This is the
guarantee G6 asked for: the previous run's defect (17 green resolver tests while a re-spelled
constant at the call site restored the bug) cannot recur for git.

---

## Unit Tests / Testing Strategy

`UNIT_TEST_STATUS: PASS`

**13 tests added.** `ProbeInterpreterShimTests` 17 → 27, `ProbeLaunchWiringTests` 5 → 8.

```
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' -k Shim
Ran 27 tests in 0.059s
OK

$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' -k ProbeLaunchWiring
Ran 8 tests in 0.007s
OK
```

### What each new test buys

**The resolver is tool-agnostic (6 tests, all portable):**

| Test | Guarantee |
| --- | --- |
| `test_the_resolver_decides_from_the_file_not_from_the_tool_name` | a fake shim NAMED `git` resolves; nothing keys off `python3` |
| `test_the_unshimmed_spelling_is_returned_untouched_for_a_real_tool` | **the Linux invariant at the generic seam** |
| `test_the_unshimmed_spelling_is_returned_when_the_tool_is_absent` | absent ≠ shim |
| `test_git_resolution_fails_closed_and_never_falls_back_to_the_shim` | **G3** — loud error, never `git --version` |
| `test_a_developer_dir_that_only_offers_another_git_shim_is_refused` | a shim in a developer dir is not an escape |
| `test_an_unshimmed_git_keeps_the_bare_path_lookup_the_agent_uses` | **G2** — the bare `git` the agent reaches survives |

**The wiring (2 portable + 3 launch-line):**

| Test | Guarantee |
| --- | --- |
| `test_the_preflight_git_goes_through_the_resolver` | `_probe_git()` is exactly `return resolve_probe_git()`, by AST |
| `test_the_resolved_preflight_git_is_never_a_tool_shim` | the property on whatever host runs it |
| **`test_the_preflight_git_check_execs_the_resolved_git`** | **G6 — the CALL SITE, by reading the command handed to `/bin/sh`** |
| `test_an_unresolvable_git_raises_before_any_check_can_run` | fail-closed *before* anything execs |
| `test_no_fixed_preflight_check_execs_a_tool_shim` | **the §A5 scope statement, enforced over the real check list** |

**The real host (2, `@DARWIN_ONLY`):**

| Test | Guarantee |
| --- | --- |
| `test_on_darwin_the_shimmed_system_git_is_actually_resolved_away` | against the REAL `/usr/bin/git`; resolved path is not a shim AND prints `git version` |
| `test_on_darwin_the_two_shimmed_tools_are_literally_the_same_file` | E1 asserted by `st_ino`, not quoted from an artifact |

### Portability

**11 of 13 are portable** and run in full on Linux CI: the shim, the developer directories
and the real tool behind them are synthesised in a temporary directory, and the launch-line
tests replace `subprocess.run` with a recorder so no shim, no sandbox and no real exec is
involved. Only the 2 `@DARWIN_ONLY` tests skip off darwin, and they are not smoke tests —
one execs the resolved git and asserts its version string.

### Two existing tests retargeted, none deleted

- `test_the_python_check_is_launched_before_the_git_check` — kept, but it now locates the
  git check by the resolved sentinel instead of the raw `git --version` string, and its
  docstring no longer presents ordering as the *argument* for a retained shim.
- `test_an_unresolvable_interpreter_raises_before_git_version_can_run` → renamed
  `..._before_any_check_can_run`; the guarantee is unchanged, the N-003 framing is dropped.
- `test_the_preflight_launch_line_execs_the_resolved_interpreter` — routed through the same
  helper so **both** resolvers are substituted; previously it left `_probe_git()` real,
  which would have made it host-dependent.

### Also verified green (G7 regressions, by name)

```
Provenance        Ran 19 tests   OK
AttemptDomain     Ran 26 tests   OK      (F-602)
Neutrality        Ran 15 tests   OK      (observability-neutrality)
Sanitiz           Ran 20 tests   OK      (bundle sanitization)
NegativeContract  Ran 11 tests   OK      (answer-key isolation, real sandboxed processes)
Observab          Ran 12 tests   OK
```

---

## Review Feedback Resolution

| Requirement | Status | Evidence |
| --- | --- | --- |
| **G1** remove the path by which the git shim can raise the CLT installer | **MET** | §6 — the resolved git on the real launch line, and the `xcrun_db` shim stderr gone |
| **G2** preserve the pre-flight's purpose; do not delete the git check | **MET** | §A4, §6 — the check still runs, still returns `git version 2.50.1 (Apple Git-155)`; the resolved path is the same binary the shim would have exec'd |
| **G3** resolve the real git or fail closed; no silent fallback, no silent widening | **MET** | `resolve_developer_tool()` raises; §7 shows the raise is load-bearing; §6 shows the admission list unmoved |
| **G4** do not weaken readable-set or answer-key isolation | **MET** | §6 — same 8 IMM roots; NEG-0..NEG-8 PASS; `NegativeContract` 11 tests OK; `test_resolving_an_interpreter_admits_no_new_immutable_root` pins both lists by value |
| **G5** do not reinstall the Command Line Tools | **MET** | nothing was installed; resolution is a byte scan plus a path join |
| **G6** regression test at the ACTUAL EXECUTING CALL SITE | **MET** | §7 — reverting the call site fails 4 tests, with the raw `git --version` visible in the recorded launch line |
| **G7** Linux CI green; all isolation/security regressions preserved | **MET** | §5 (3.11/3.12/3.13, 1238 each, only the known local-only pair), §"Also verified green" |
| Non-darwin behaviour unchanged | **MET** | `unshimmed` is returned untouched when the tool is real or absent; the Linux git check string stays the byte-identical `git --version` |
| Never exec an xcselect-linked binary to resolve | **MET** | unchanged `developer_dir_candidates()`; `test_no_xcselect_linked_binary_is_executed_to_resolve_the_developer_dir` still passes |
| Consider ALL pre-flight checks; honest scope statement | **MET** | §A5 table + `preflight_probe()` docstring + `test_no_fixed_preflight_check_execs_a_tool_shim` |
| Do not trigger the CLT installer GUI | **HELD** | no shim was executed at any point; every classification is a marker scan + `os.stat`. `/usr/bin/dyld_info` is itself the shim inode, so `dyld_info` was deliberately NOT run (§A6) |
| No prior run's artifacts modified | **MET** | `git status` shows only my two source files and this new artifact |

### On N-003 specifically

The previous IMPLEMENTATION Reviewer's N-003 (`MINOR`, `Blocking: NO`) is **superseded, not
merely resolved**. Its reasoning is now recorded in the source as an example of the error —
an ordering guarantee about one tool proves nothing about another tool's call site — so the
next reader who is tempted to re-derive it meets the refutation at
`TOOL_SHIM_MARKER` and in `ProbeLaunchWiringTests`' section comment.

---

## Quality Gate

```
profile_status: absent
applicable_quality_attributes: none
blocking: none
decision_priority: explicit requirements > current phase contract > minimal general gate
```

| Gate | Verdict |
| --- | --- |
| G1 explicit requirement violation | none — every G1..G7 met, see table above |
| G2 result does not work | no — real seatbelt session, S1/S2/S3 + NEG-0..NEG-8 PASS |
| G3 severe regression | none — 1238/1238 minus the 2 known pre-existing; Linux identical on 3.11/3.12/3.13 |
| G4 data loss / security / irreversible | none — admission lists unmoved, no profile widening, nothing installed, nothing pushed |
| G5 missing evidence | none — every claim above has its command output; what I did not verify is named in §A6 |

---

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
