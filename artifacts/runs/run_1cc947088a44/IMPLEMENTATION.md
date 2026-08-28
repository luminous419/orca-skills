# IMPLEMENTATION — run_1cc947088a44

Phase: IMPLEMENTATION · Iteration 2 · Risk: high
Branch: `agent/final-review-observability-evaluation` (Draft PR #20)
Baseline HEAD: `fc4f4a8`

---

## Summary

The pre-flight's `git --version` was removed from the launch line and replaced with a
resolved real git. `/usr/bin/git` and `/usr/bin/python3` are the **same inode** on this
host, so the previous run's Python-only fix left the identical shim reachable through
git's own call site — the door the user actually walked through in another project.

The fix is a **generalization, not a second special case**. `resolve_probe_interpreter()`
was refactored into a tool-agnostic `resolve_developer_tool(system_path, unshimmed)`, and
`resolve_probe_interpreter()` is a thin specialization over it.

**Iteration 2 corrects F-001.** Iteration 1 bought shim avoidance by resolving a *fixed*
`/usr/bin/git`, and in doing so lost the property the bare `git` had for free: PATH
fidelity. `wrap_command()` prepends every admitted `--agent-path` directory **before**
`/usr/bin`, so the pre-flight could bless Apple Git while the agent went on to resolve a
different git entirely. `resolve_probe_git()` now **selects** the candidate from
`launch_path(agent_path)` — the very PATH string the launch line carries — and only then
classifies it by its bytes: a real binary is verified as-is, a shim is resolved to the real
tool behind it, and neither "no git on PATH" nor "unresolvable shim" has a fallback.

24 tests are added net across the two iterations. Both properties are pinned non-vacuously:
putting the raw `git --version` back on the launch line fails 6 tests, and reverting to
fixed-path resolution fails 4 — demonstrated by actually breaking each and recording the
output (§7).

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

### A4. What the check must prove, and the claim iteration 1 got wrong (G2, F-001)

**G2 needs two properties at once, and iteration 1 held only one.**

| | PATH fidelity — the git the agent resolves | No shim execution |
| --- | --- | --- |
| before this run (`git --version`) | **yes** — same shell, same PATH | no |
| iteration 1 (fixed `/usr/bin/git`) | **no** | yes |
| iteration 2 (this) | **yes** | **yes** |

Iteration 1's A4 asserted that "the resolved path is the same real git the agent's own
`git` would have ended up running." **That claim is true only when PATH resolution lands on
`/usr/bin/git`, and it was stated without that condition — which is the defect the Reviewer
raised as F-001, and I have verified the mechanism myself.** `wrap_command()` builds

```
PATH=<admitted agent_path entries…>:/usr/bin:/bin:/usr/sbin:/sbin
```

and the launched agent inherits it, so an admitted `--agent-path` directory **outranks
`/usr/bin`**. A pre-flight that looks at the fixed `/usr/bin/git` therefore reports success
for Apple Git while the agent resolves something else. The same mismatch exists with no
`--agent-path` at all, whenever the inherited PATH names a git before `/usr/bin`. On *this*
host it does not — the inherited PATH begins `/opt/homebrew/bin`, but there is no
`/opt/homebrew/bin/git`, so selection falls through to `/usr/bin/git` and iteration 1
happened to be right here. It is right by accident of this machine's PATH, which is exactly
why it cannot be the mechanism.

**The corrected behaviour, stated precisely.** `resolve_probe_git()` now:

1. computes the effective PATH with `launch_path(agent_path)` — the *same function whose
   output `wrap_command()` puts on the launch line*, so the two can never drift;
2. selects the first executable `git` on it with `shutil.which()`, which is the shell's own
   first-match-plus-`X_OK` rule;
3. classifies **that candidate** by its bytes. A real binary is returned unchanged and is
   what gets verified — it *is* the agent's git. A candidate carrying `TOOL_SHIM_MARKER` is
   handed to `resolve_developer_tool()`, which returns `<developer dir>/usr/bin/git`: the
   same file the shim's single libxcselect import, `_xcselect_invoke_xcrun`, would have
   exec'd, reached without the exec that can put an installer dialog on screen;
4. fails closed on both ends — no `git` anywhere on the effective PATH raises, and an
   unresolvable shim raises out of `resolve_developer_tool()`. Neither falls back to a bare
   `git`, to the shim, or to a widened profile.

**Linux is unchanged in behaviour.** Nothing on a Linux PATH is a shim, so step 3 returns
the candidate step 2 selected — the same binary the bare name always reached. The check
*string* is now the absolute path rather than the bare word; that is a spelling change that
makes the selection explicit and auditable, and it executes the identical file.

§6 shows the check still returning `git version 2.50.1 (Apple Git-155)` from inside a real
seatbelt session.

### A5. Scope statement — which launch paths this covers, honestly

Required by the dispatch, and enforced by a test rather than left as prose
(`test_no_fixed_preflight_check_execs_a_tool_shim`):

| Launch path | Reaches a shim? | Covered by this change |
| --- | --- | --- |
| `preflight_probe()` python check | was YES (fixed in run_a29ac78075a9) | yes — `_probe_python()` |
| `preflight_probe()` git check | **YES — this run's finding** | **yes — `_probe_git(agent_path)`, PATH-selected** |
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

**Which PATH the git check follows — the F-001 dimension, also stated honestly.**

| Launch shape | PATH the agent sees | What the pre-flight now checks |
| --- | --- | --- |
| `--agent-path A B` given | `A:B:/usr/bin:/bin:/usr/sbin:/sbin` (from `launch_path()`) | first executable `git` on that exact string; a shim among them is resolved, never exec'd |
| no `--agent-path` | inherited from this process (no `PATH=` on the launch line) | first executable `git` on the inherited PATH, same treatment |
| no `git` on the effective PATH | — | **hard failure before any check launches**; never a bare `git` fallback |

Only already-admitted `--agent-path` entries are searched: `isolate()` runs
`assert_agent_path_admitted(agent_path, readable)` *before* `preflight_probe()`, and
`launch_path()` reads nothing beyond joining those directory strings. Nothing new is
admitted, scanned or made readable; `NEVER_ADMITTED`, `DEFAULT_IMM_CANDIDATES`, the
immutability proof and the NEG-5 mandatory scan are untouched (§6, and
`test_resolving_an_interpreter_admits_no_new_immutable_root` pins both lists by value).

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
- I did **not** run a real `isolate()` session with a synthetic `--agent-path` directory.
  `assert_agent_path_admitted()` gates `--agent-path` against the readable set, and a
  temporary directory lives under `/private/var`, which is in `NEVER_ADMITTED`. Admitting it
  would mean widening the profile — the one thing the dispatch forbids and the whole point
  of the gate. The `--agent-path` selection behaviour is therefore proven by
  `PreflightGitPathSelectionTests` (12 portable tests, including an end-to-end
  `preflight_probe()` run against the recorded launch line), and the real seatbelt session
  in §6 exercises the **inherited-PATH** branch of the same code. I state this rather than
  implying the real session covered both branches.
- I did **not** push. The Coordinator pushes.

---

## Changes

### `scripts/review_isolation.py`

1. **`SYSTEM_GIT` / `GIT_COMMAND` / `LAUNCH_PATH_TAIL` constants.** `GIT_COMMAND = "git"`
   is a bare name on purpose — but as the *name looked up on the agent's own effective
   PATH*, not as a check string. `SYSTEM_GIT` is now explicitly documented as test material
   and the shim's usual address, and explicitly **not** what resolution keys off; keying off
   it is F-001. `LAUNCH_PATH_TAIL` names the `/usr/bin:/bin:/usr/sbin:/sbin` tail once.
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
4. **`launch_path(agent_path, environ=None)`** *(iteration 2, new)* — the single
   definition of "what PATH will the launched agent see": the realpath'd admitted entries
   plus `LAUNCH_PATH_TAIL` when `--agent-path` was given, otherwise this process's inherited
   `PATH`. `wrap_command()` was rewired to build its `PATH=` assignment from it, so the
   string on the launch line and the string the pre-flight searches are *the same string
   from the same function*. `wrap_command()`'s output is byte-identical to before.
5. **`resolve_probe_git(path_value=None, *, agent_path=(), developer_dirs=None)`**
   *(iteration 2, rewritten)* — **selects** the candidate from `launch_path(agent_path)` via
   `shutil.which()`, then classifies it by its bytes: a real binary is returned as-is, a shim
   goes through `resolve_developer_tool(candidate, candidate)`. Raises `IsolationError` when
   no `git` is on the effective PATH, naming that PATH. Iteration 1's signature took a fixed
   `system_git` and is gone; that fixed address was the defect.
6. **`_probe_git(agent_path=())`** — mirrors `_probe_python()`; one statement, pinned by AST
   test *including the `agent_path=agent_path` argument*, because dropping it is exactly the
   fixed-path regression.
7. **`preflight_probe()`** — `"git --version"` →
   `f"{shlex.quote(_probe_git(agent_path))} --version"`. This is the one-line change that
   closes the finding; everything above exists to make it correct, PATH-faithful,
   fail-closed and general.
8. **Commentary.** The `TOOL_SHIM_MARKER` block's "Deliberately NOT extended to the
   pre-flight's `git --version`" paragraph is replaced by the measurement that refutes it
   and by a statement of the reasoning error, so the next reader cannot re-derive the narrow
   argument from the source. `preflight_probe()`'s docstring carries the §A5 scope statement,
   and now also states that the git spelling is PATH-*selected* before it is shim-resolved.
   `resolve_probe_git()`'s docstring no longer claims the resolved path is unconditionally
   the git the agent would run; it states the two properties and the condition under which
   each holds.

### `scripts/test_review_isolation.py`

+24 net across the two iterations (13 in iteration 1, +12 in the new
`PreflightGitPathSelectionTests`, −1 iteration-1 test replaced). See
§"Unit Tests / Testing Strategy".

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
| `scripts/review_isolation.py` | generalized resolver + `launch_path()` + PATH-selecting `resolve_probe_git()` + `_probe_git(agent_path)` + call-site fix + commentary |
| `scripts/test_review_isolation.py` | +24 net (13 iteration-1, +12 `PreflightGitPathSelectionTests`, −1 replaced); 2 existing N-003 tests retargeted |
| `artifacts/runs/run_1cc947088a44/IMPLEMENTATION.md` | this artifact |

---

## Validation

### 1. Full suite, macOS

**Iteration 2 (this delta):**

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1249 tests in 290.342s
FAILED (failures=2, skipped=6)

$ python3 -m unittest discover -s scripts -p 'test_*.py' 2>&1 | grep -E "^(FAIL|ERROR):"
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
```

1225 baseline + 24 net new = **1249** (iteration 1 recorded 1238 at +13). The only two
failures are the **expected, pre-existing** `RetainedReportWhitespaceExemptionTests` pair,
which skip on CI because `actions/checkout@v4` fetches `--depth=1`. Not fixed: every
offending file belongs to another run and is digest-bound.

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
Ran 1249 tests in 197.506s
FAILED (failures=2, skipped=28)

########## python:3.12 (non-root, CI-like) ##########
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1249 tests in 226.220s
FAILED (failures=2, skipped=28)

########## python:3.13 (non-root, CI-like) ##########
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1249 tests in 216.770s
FAILED (failures=2, skipped=28)
```

All three: **1249 run, 2 failures**, and those two are the same
`RetainedReportWhitespaceExemptionTests` pair that fails locally on macOS and skips on CI
under `--depth=1`. `skipped=28` vs macOS's `6` is the 22 `@DARWIN_ONLY` tests, two of which
are mine (§"Portability"). **`skipped=28` is unchanged from iteration 1**, which is the
mechanical proof that all 12 new `PreflightGitPathSelectionTests` execute on Linux rather
than skipping — the docker `git version 2.47.3` is the real, non-shim git those runs select.

The default-**root** docker invocation was also re-run for continuity with §5a: all three
versions reported `Ran 1249 tests … FAILED (failures=8, errors=1, skipped=28)` — the same
**nine** pre-existing results §5a root-caused. Their names were re-listed on 3.11 and match
exactly: seven `ImmutabilityProofTests` (root ignores the mode bits the proof relies on)
plus the whitespace pair. So the delta is `+24 tests, +0 failures` under both invocations.

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

Re-run against the **iteration-2** code, not carried over from iteration 1.

```
$ isolate("run_1cc947088a44_f001", fixture=scripts/fixtures/final_review_eval,
          enforcement="seatbelt")

effective launch PATH (no --agent-path, inherited):
  /opt/homebrew/bin:/opt/homebrew/sbin:/Users/luminous/bin:… ...
resolved probe git    : /Library/Developer/CommandLineTools/usr/bin/git
resolved probe python : /Library/Developer/CommandLineTools/Library/Frameworks/
                        Python3.framework/Versions/3.9/bin/python3.9

scope_enforcement: seatbelt
PROPERTIES: {"S1": "PASS", "S2": "PASS", "S3": "PASS"}
SESSION:    /private/var/folders/nz/.../T/frv_iso_bn6v0ji3
profile_digest: sha256:513061d94a10e42c43702def997b46958aa608116a670343876f72418eb444e0
```

**The PATH selection is visible in that trace.** The inherited PATH begins
`/opt/homebrew/bin`; there is no `/opt/homebrew/bin/git`, so `shutil.which()` falls through
to `/usr/bin/git`; that file carries `TOOL_SHIM_MARKER`, so it is resolved — never exec'd —
to `/Library/Developer/CommandLineTools/usr/bin/git`. This is the **inherited-PATH** branch;
the `--agent-path` branch cannot be exercised in a real session without widening the profile
and is proven by `PreflightGitPathSelectionTests` instead (§A6 states this).

Read back out of the session's own `control/ISOLATION.json`:

```
NEG-0 positive_control            PASS
NEG-1 review_root_walk            PASS
NEG-2 sandboxed_open              PASS
NEG-3 sandboxed_discovery         PASS
NEG-4 sandboxed_git_and_archive   PASS
NEG-5 readable_set_rescan         PASS
NEG-6 profile_integrity           PASS
NEG-7 writable_descendant_plant   PASS
NEG-8 alias_battery               PASS

no_unscanned_descendant: PASS
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

Three things to read off it, side by side with the previous run's log quoted in §A3:

1. The git check execs **`/Library/Developer/CommandLineTools/usr/bin/git`**, not the shim,
   and it returns `git version 2.50.1 (Apple Git-155)` — rc=0, from inside the sandbox,
   through the real `wrap_command()`. **G2 preserved.**
2. **The two `git: error: couldn't create cache file '.../xcrun_db-...'` lines are gone**
   (`grep -c xcrun_db preflight.log` → `0`). `xcrun_db` is the shim's cache and only
   `xcselect_invoke_xcrun` writes it. Their disappearance is positive, observed evidence
   that the shim is no longer being exec'd — not merely an assertion that it should not be.
   **G1 met.**
3. That binary is the one **this host's own PATH selects**, reached through
   `launch_path()` → `shutil.which()` → shim resolution, rather than through a hard-coded
   `/usr/bin/git`. **F-001 corrected in the live path, not only in unit tests.**

**No widening (G4).** The session's `readable_set` admits exactly the eight
`DEFAULT_IMM_CANDIDATES` roots and no others (plus the three session-local Class USR roots,
which are the fixture's own and are `scanned: true`):

```
/bin  /sbin  /private/etc  /dev  /private/var/select  /usr  /System
/Library/Developer/CommandLineTools
```

`/Library/Developer/CommandLineTools` was already on that list before this change and still
had to pass the recursive immutability proof to be admitted (`/System`: 169,297 dirs /
286,743 files, `writable_files: 0`, `passed: true`). Resolving a tool inside it admits
nothing; the proof admits it, exactly as before. `launch_path()` adds nothing to the
readable set — it only joins directory strings that `assert_agent_path_admitted()` has
already cleared.

The session was torn down after the evidence above was read.


### 7. Both properties are pinned NON-VACUOUSLY — demonstrated by breaking each

F-001 exists because iteration 1 fixed one property and silently traded away the other, so
each is broken separately here, in a **throwaway local clone**, and restored. Actual
recorded output.

**Mutation A — revert to fixed-path resolution (the F-001 defect itself).** The candidate is
taken from `SYSTEM_GIT` instead of from the effective PATH; PATH selection is bypassed.

```
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' \
      -k PreflightGitPathSelection

FAIL: test_the_first_admitted_agent_path_entry_wins_as_on_the_launch_line
AssertionError: '/Library/Developer/CommandLineTools/usr/bin/git'
            != '/private/var/folders/.../tmpmfwdjapg/first/git'

FAIL: test_the_preflight_launch_line_checks_the_agent_path_git
AssertionError: False is not true : the agent's own git is on no launch line:
  [... PATH=/private/var/folders/.../agentbin:/usr/bin:/bin:/usr/sbin:/sbin
       exec /usr/bin/sandbox-exec -f .../scope.sb
       /Library/Developer/CommandLineTools/usr/bin/git --version', ...]

Ran 12 tests in 0.017s
FAILED (failures=8)
```

**That failure message is F-001 in one line:** the launch line carries
`PATH=<agentbin>:/usr/bin:…`, and the check runs
`/Library/Developer/CommandLineTools/usr/bin/git`. The agent's git and the verified git are
different files. Eight tests refuse it.

**Mutation B — put the raw shim spelling back on the launch line** (`_probe_git(agent_path)`
→ the literal `"git --version"`), which is the G1 defect iteration 1 closed.

```
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' \
      -k PreflightGitPathSelection -k ProbeLaunchWiring

FAIL: PreflightGitPathSelectionTests.test_an_ungettable_git_stops_the_preflight_before_anything_launches
FAIL: PreflightGitPathSelectionTests.test_the_preflight_launch_line_checks_the_agent_path_git
FAIL: ProbeLaunchWiringTests.test_an_unresolvable_git_raises_before_any_check_can_run
FAIL: ProbeLaunchWiringTests.test_no_fixed_preflight_check_execs_a_tool_shim
FAIL: ProbeLaunchWiringTests.test_the_preflight_git_check_execs_the_resolved_git
FAIL: ProbeLaunchWiringTests.test_the_python_check_is_launched_before_the_git_check
Ran 20 tests in 0.011s
FAILED (failures=6)
```

**Mutation C — drop `agent_path` from the `_probe_git()` call** (`resolve_probe_git()` with
no argument), the subtlest form of the regression: shim avoidance intact, wrong git checked.

```
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' \
      -k PreflightGitPathSelection -k ProbeLaunchWiring -k Shim

FAIL: PreflightGitPathSelectionTests.test_an_admitted_agent_path_git_outranks_the_system_git
FAIL: PreflightGitPathSelectionTests.test_the_first_admitted_agent_path_entry_wins_as_on_the_launch_line
FAIL: PreflightGitPathSelectionTests.test_the_preflight_launch_line_checks_the_agent_path_git
FAIL: ProbeInterpreterShimTests.test_the_preflight_git_goes_through_the_resolver
Ran 46 tests in 0.062s
FAILED (failures=4)
```

**Restored, unmutated:**

```
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' \
      -k PreflightGitPathSelection -k ProbeLaunchWiring -k Shim
Ran 46 tests in 0.056s
OK
```

So a future edit that puts a raw shim spelling back on the launch line fails 6 tests, and
one that reverts to fixed-path resolution fails 4 to 8 — and
`test_the_preflight_launch_line_checks_the_agent_path_git` catches **all three** mutations
behaviourally, by reading the command actually handed to `/bin/sh`.

---

## Unit Tests / Testing Strategy

`UNIT_TEST_STATUS: PASS`

**+24 net.** `ProbeInterpreterShimTests` 17 → 26 (iteration 2 replaced one),
`ProbeLaunchWiringTests` 5 → 8, and the new `PreflightGitPathSelectionTests` 0 → 12.

```
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' -k Shim
Ran 26 tests in 0.091s
OK

$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' -k ProbeLaunchWiring
Ran 8 tests in 0.005s
OK

$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' \
      -k PreflightGitPathSelection
Ran 12 tests in 0.017s
OK
```

### What each new test buys

**The resolver is tool-agnostic (6 tests, all portable):**

| Test | Guarantee |
| --- | --- |
| `test_the_resolver_decides_from_the_file_not_from_the_tool_name` | a fake shim NAMED `git` resolves; nothing keys off `python3` |
| `test_the_unshimmed_spelling_is_returned_untouched_for_a_real_tool` | **the Linux invariant at the generic seam** |
| `test_the_unshimmed_spelling_is_returned_when_the_tool_is_absent` | absent ≠ shim |
| `test_git_resolution_fails_closed_and_never_falls_back_to_the_shim` | **G3** — loud error, never `git --version` (iteration 2: now driven through a PATH whose only `git` is the shim) |
| `test_a_developer_dir_that_only_offers_another_git_shim_is_refused` | a shim in a developer dir is not an escape |

`test_an_unshimmed_git_keeps_the_bare_path_lookup_the_agent_uses` was **replaced** in
iteration 2: it asserted the check string stays the bare `git`, which is the spelling the
F-001 fix must not emit. The property it stood for — PATH fidelity — is now covered
behaviourally by `PreflightGitPathSelectionTests` below, which is strictly stronger.

**The wiring (2 portable + 3 launch-line):**

| Test | Guarantee |
| --- | --- |
| `test_the_preflight_git_goes_through_the_resolver` | `_probe_git()` is exactly `return resolve_probe_git(agent_path=agent_path)`, by AST — the argument is pinned, because dropping it *is* the F-001 regression |
| `test_the_resolved_preflight_git_is_never_a_tool_shim` | the property on whatever host runs it |
| **`test_the_preflight_git_check_execs_the_resolved_git`** | **G6 — the CALL SITE, by reading the command handed to `/bin/sh`** |
| `test_an_unresolvable_git_raises_before_any_check_can_run` | fail-closed *before* anything execs |
| `test_no_fixed_preflight_check_execs_a_tool_shim` | **the §A5 scope statement, enforced over the real check list** |

**PATH selection — `PreflightGitPathSelectionTests`, 12 tests, all portable (iteration 2):**

| Test | Guarantee |
| --- | --- |
| `test_the_searched_path_is_literally_the_launch_line_path` | the anti-drift pin: `launch_path()`'s output is the string on `wrap_command()`'s launch line |
| `test_no_agent_path_means_the_inherited_path_is_what_the_agent_gets` | no `PATH=` on the launch line ⇒ inherited PATH is the right thing to search |
| **`test_an_admitted_agent_path_git_outranks_the_system_git`** | **THE F-001 regression** — an admitted `--agent-path` git is what gets checked, not the Command Line Tools git |
| `test_the_first_admitted_agent_path_entry_wins_as_on_the_launch_line` | selection order matches the launch line's order |
| `test_an_inherited_path_git_is_followed_too` | the no-`--agent-path` half of the same mismatch |
| `test_a_real_path_selected_git_is_used_exactly_as_selected` | **the Linux invariant** — a real candidate is verified as-is, never "improved" |
| `test_a_path_selected_shim_is_resolved_rather_than_executed` | both properties at once: right candidate found, shim resolved by bytes not exec'd |
| `test_an_unresolvable_path_selected_shim_fails_closed` | fail closed, with the installer wording |
| `test_no_git_on_the_effective_path_is_a_loud_failure` | fail closed, naming the PATH; no bare-`git` fallback |
| `test_a_non_executable_git_on_path_is_not_selected` | selection uses the shell's `X_OK` rule, so it cannot bless a file the agent cannot run |
| **`test_the_preflight_launch_line_checks_the_agent_path_git`** | **end-to-end with the REAL resolver: the agent's git is on the recorded launch line and `CommandLineTools` is not** |
| `test_an_ungettable_git_stops_the_preflight_before_anything_launches` | zero processes launched when git cannot be produced |

**The real host (2, `@DARWIN_ONLY`):**

| Test | Guarantee |
| --- | --- |
| `test_on_darwin_the_shimmed_system_git_is_actually_resolved_away` | against the REAL `/usr/bin/git`; resolved path is not a shim AND prints `git version` |
| `test_on_darwin_the_two_shimmed_tools_are_literally_the_same_file` | E1 asserted by `st_ino`, not quoted from an artifact |

### Portability

**22 of 24 are portable** and run in full on Linux CI: the shim, the developer
directories, the PATH candidate gits and the real tool behind them are all synthesised in a
temporary directory, and the launch-line tests replace `subprocess.run` with a recorder so
no shim, no sandbox and no real exec is involved. **All 12 `PreflightGitPathSelectionTests`
are portable**, which is not merely asserted: §5's Linux runs report `skipped=28`, exactly
the iteration-1 number, so none of the 12 turned into a skip. Only the 2 `@DARWIN_ONLY`
tests skip off darwin, and they are not smoke tests — one execs the resolved git and asserts
its version string.

### Existing tests retargeted; one replaced, with the reason

Iteration 1:

- `test_the_python_check_is_launched_before_the_git_check` — kept, but it now locates the
  git check by the resolved sentinel instead of the raw `git --version` string, and its
  docstring no longer presents ordering as the *argument* for a retained shim.
- `test_an_unresolvable_interpreter_raises_before_git_version_can_run` → renamed
  `..._before_any_check_can_run`; the guarantee is unchanged, the N-003 framing is dropped.
- `test_the_preflight_launch_line_execs_the_resolved_interpreter` — routed through the same
  helper so **both** resolvers are substituted; previously it left `_probe_git()` real,
  which would have made it host-dependent.

Iteration 2:

- `test_an_unshimmed_git_keeps_the_bare_path_lookup_the_agent_uses` — **replaced.** It
  asserted `resolve_probe_git()` returns the bare `git`, which is precisely the spelling the
  F-001 fix must not emit; keeping it would have pinned the defect. The property it stood
  for is PATH fidelity, and that is now covered behaviourally and much more strongly by the
  12 `PreflightGitPathSelectionTests`, including the end-to-end launch-line assertion.
- `test_git_resolution_fails_closed_and_never_falls_back_to_the_shim` and
  `test_a_developer_dir_that_only_offers_another_git_shim_is_refused` — same guarantees,
  now driven through a PATH whose only `git` is the shim, because selection is how the
  resolution is reached.
- `test_the_resolved_preflight_git_is_never_a_tool_shim` — the "bare name on an unshimmed
  host" branch is gone; the answer is always an absolute path now, and the test skips (with
  a reason) on a host that has no git at all, which is the fail-closed case.
- `test_the_preflight_git_goes_through_the_resolver` — now pins
  `resolve_probe_git(agent_path=agent_path)` including the argument.

### Also verified green (G7 regressions, by name)

Re-run against the iteration-2 delta:

```
Provenance         Ran 19 tests in   0.271s OK
AttemptDomain      Ran 26 tests in   1.196s OK   (F-602)
Neutrality         Ran 15 tests in   2.974s OK   (observability-neutrality)
Sanitiz            Ran 20 tests in   0.030s OK   (bundle sanitization)
NegativeContract   Ran 11 tests in 229.711s OK   (answer-key isolation, real sandboxed processes)
Observab           Ran 12 tests in   1.557s OK
ImmutabilityProof  Ran 14 tests in   0.520s OK   (the recursive proof, untouched)
```

---

## Review Feedback Resolution

| Requirement | Status | Evidence |
| --- | --- | --- |
| **G1** remove the path by which the git shim can raise the CLT installer | **MET** | §6 — the resolved git on the real launch line, and the `xcrun_db` shim stderr gone |
| **G2** preserve the pre-flight's purpose; verify the git the agent will actually use; do not delete the check | **MET (iteration 2)** | §A4 — the candidate is now selected from `launch_path(agent_path)`, the same string `wrap_command()` puts on the launch line, then shim-resolved by bytes; §6 — the check still runs and still returns `git version 2.50.1 (Apple Git-155)`; `PreflightGitPathSelectionTests` (12) pins it, and §7 mutation A/C shows the fixed-path form failing |
| **G3** resolve the real git or fail closed; no silent fallback, no silent widening | **MET** | `resolve_developer_tool()` raises; §7 shows the raise is load-bearing; §6 shows the admission list unmoved |
| **G4** do not weaken readable-set or answer-key isolation | **MET** | §6 — same 8 IMM roots; NEG-0..NEG-8 PASS; `NegativeContract` 11 tests OK; `test_resolving_an_interpreter_admits_no_new_immutable_root` pins both lists by value |
| **G5** do not reinstall the Command Line Tools | **MET** | nothing was installed; resolution is a byte scan plus a path join |
| **G6** regression test at the ACTUAL EXECUTING CALL SITE | **MET** | §7 — three separate mutations, 6 / 4 / 8 failures, with the offending launch line quoted in each failure message |
| **G7** Linux CI green; all isolation/security regressions preserved | **MET** | §5 (3.11/3.12/3.13, 1249 each, only the known local-only pair), §"Also verified green" |
| Non-darwin behaviour unchanged | **MET** | nothing on a Linux PATH is a shim, so selection alone is the whole answer and the **same binary** the bare `git` always reached is executed — `test_a_real_path_selected_git_is_used_exactly_as_selected`. The check *string* is now that binary's absolute path rather than the bare word; that is a spelling change, and §5 shows all three Linux versions unchanged in outcome |
| Never exec an xcselect-linked binary to resolve | **MET** | unchanged `developer_dir_candidates()`; `test_no_xcselect_linked_binary_is_executed_to_resolve_the_developer_dir` still passes |
| Consider ALL pre-flight checks; honest scope statement | **MET** | §A5 table + `preflight_probe()` docstring + `test_no_fixed_preflight_check_execs_a_tool_shim` |
| Do not trigger the CLT installer GUI | **HELD** | no shim was executed at any point; every classification is a marker scan + `os.stat`. `/usr/bin/dyld_info` is itself the shim inode, so `dyld_info` was deliberately NOT run (§A6) |
| No prior run's artifacts modified | **MET** | `git status` shows only my two source files and this new artifact |

### F-001 (G2, MAJOR, BLOCKING) — RESOLVED

| | |
| --- | --- |
| **Finding** | On a shimmed macOS host, preflight verified the fixed Command Line Tools git behind `/usr/bin/git`, not necessarily the git the agent's effective `PATH` selects. |
| **Accepted** | Yes, without qualification. I reproduced the mechanism: `wrap_command()` prepends admitted `agent_path` entries **before** `/usr/bin`, `resolve_probe_git()` inspected only `SYSTEM_GIT`, and the absolute substitute was placed **after** a differing `PATH=` assignment. |
| **My own overclaim** | `resolve_probe_git()`'s iteration-1 docstring, and §A4, asserted the resolved path "is the same real git the agent's own `git` would have ended up running." That holds **only when PATH resolution lands on `/usr/bin/git`** and was written without the condition. Both the docstring and §A4 now state the two properties and when each holds. |
| **Fix** | `launch_path()` becomes the single definition of the agent's effective PATH and feeds *both* `wrap_command()`'s `PATH=` assignment and `resolve_probe_git()`'s search. Selection by `shutil.which()` (the shell's own rule), then byte-classification: real → verified as-is, shim → `resolve_developer_tool()`, otherwise raise. |
| **Fail closed** | No candidate on PATH raises and names the PATH; an unresolvable shim raises out of `resolve_developer_tool()`. No bare-`git` fallback, no shim fallback, no profile widening. |
| **Linux** | Unchanged binary; `test_a_real_path_selected_git_is_used_exactly_as_selected` and §5's three versions. |
| **Security** | Only already-admitted `agent_path` entries are searched — `isolate()` runs `assert_agent_path_admitted()` before `preflight_probe()`. `NEVER_ADMITTED`, `DEFAULT_IMM_CANDIDATES`, the immutability proof and the NEG-5 scan are byte-for-byte untouched (§6, §A5). |
| **Regression coverage** | The Reviewer's explicit ask: `PreflightGitPathSelectionTests` — 12 portable tests, including an admitted `agent_path` directory holding a **distinct** git candidate asserted to be the one checked, the inherited-PATH half, and an end-to-end `preflight_probe()` run with the real resolver. Non-vacuity in §7. |

### On N-001 (non-blocking, remote CI)

Correct and still outstanding: HEAD is local-only, so the branch's green Actions runs do not
cover this delta. I do not push — the Coordinator does. Linux evidence here is reproduced
locally in Docker on 3.11/3.12/3.13 and is offered as exactly that, not as CI.

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
| G2 result does not work | no — real seatbelt session, S1/S2/S3 + NEG-0..NEG-8 PASS, pre-flight exec'ing the PATH-correct real git |
| G3 severe regression | none — 1249 minus the 2 known pre-existing; Linux identical on 3.11/3.12/3.13 |
| G4 data loss / security / irreversible | none — admission lists unmoved, no profile widening, only already-admitted `agent_path` entries searched, nothing installed, nothing pushed |
| G5 missing evidence | none — every claim above has its command output; what I did not verify is named in §A6 |

---

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
