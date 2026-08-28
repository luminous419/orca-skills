# IMPLEMENTATION — run_1cc947088a44

Phase: IMPLEMENTATION · Iteration 4 (report-only correction over the iteration-3 code) · Risk: high
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

**Iteration 3 corrects F-002 and F-003, both introduced by iteration 2's own correction.**
Following a PATH is a *read*, and iteration 2 followed it into directories nothing had
admitted: with no `--agent-path`, the inherited `PATH` was searched and the selected
candidate was **opened** by `is_tool_shim()` — in the host process, before Seatbelt exists,
with no readable-set check anywhere on that branch. My own inherited-PATH test asserted that
bypass as correct behaviour, over a `TemporaryDirectory` that realpaths under
`/private/var`, i.e. `NEVER_ADMITTED` (§A7). Separately, `shutil.which(..., path=<whole
PATH>)` resolves a relative or empty component against **this** process's directory while
the agent resolves it against `<session>/review_root`, so the "exact effective-PATH
fidelity" claim was wider than the behaviour (§A8).

Selection is now one function, `select_launch_path_tool()`, which walks the PATH component
by component and **refuses** — before any byte is read — a candidate whose realpath is
outside the admitted readable set, and a relative or empty component that could still change
which file is selected. `isolate()` threads its own `readable["entries"]` in; the parameter
**defaults to admitting nothing**, so a caller that forgets it fails closed instead of
searching ungated. What is *not* claimed is stated in the same places (§A8, §A9).

**Iteration 4 corrects F-004, and it is a REPORT correction only — no production code and
no test changed.** Two claims in this artifact were wider than their evidence. "Linux is
unchanged in behaviour" was false as stated: the F-002 admission gate has no platform
predicate, so an unadmitted PATH candidate is now refused on Linux too, where the bare `git`
would previously have been used. §A4 now separates what is genuinely unchanged (no shim, so
no substitution; an **admitted** candidate is executed exactly as selected) from what
changed (unadmitted candidates fail closed, everywhere). And G7 "Linux CI green" was marked
`MET` while this HEAD was unpushed; the branch has since been pushed, and G7 is now met by
citing **Actions run 33187763926 on `headSha a02b1226774233984dc8520c3720959c74c955d9`** —
3.11/3.12/3.13 all `success`, `Ran 1259 tests` / `OK (skipped=32)` each (§5b). The
non-reproducing 3.12 error in §5 is updated with that matrix as *consistent-with* evidence
and is **not** upgraded to a diagnosis; the disclosure stays.

34 tests are added net across the first three iterations. Every property is pinned non-vacuously:
putting the raw `git --version` back on the launch line fails 6 tests, reverting to
fixed-path resolution fails 4, deleting the admission gate fails 4, and deleting the
relative-component refusal fails 2 — demonstrated by actually breaking each and recording
the output (§7).

`STATUS: COMPLETE`
`UNIT_TEST_STATUS: PASS` — iteration 4 changed no production code and no test, so the gate
is the iteration-3 evidence plus a re-confirmation that nothing drifted (§1–§4, §5b).

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

**What is unchanged on Linux — and what changed there too.** Those are two different
statements, and iteration 3's report collapsed them into one sentence that was wider than
the code. Separated:

*Unchanged.* Nothing on a Linux PATH is a shim, so step 3 performs **no resolution and no
substitution**: it returns the candidate step 2 selected. For a candidate that **is
admitted**, the file executed is byte-for-byte the one the bare name always reached. Only
the check *string* differs — that candidate's absolute path instead of the bare word —
which makes the selection explicit and auditable while exec'ing the identical file.

*Changed, on Linux exactly as on darwin.* Step 2 now **refuses** a candidate whose realpath
falls outside the admitted readable set, and refuses a relative or empty PATH component
before it. Where the bare `git` would previously just have been used, such a run now raises
`IsolationError`. That is the F-002/F-003 correction behaving as designed, it fails closed,
and it is **not darwin-only**: `select_launch_path_tool()` has no platform predicate, and
the 22 `PreflightGitPathSelectionTests` that pin it carry no `@DARWIN_ONLY` marker and run
on Linux CI. So the claim "Linux behaviour is unchanged" is **false as stated**; the true
claim is the narrow one above. What green CI on 3.11/3.12/3.13 (§5b) shows is not that
nothing changed, but that no test in this repository exercises an unadmitted candidate and
expects it to be followed.

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
| `--agent-path A B` given | `A:B:/usr/bin:/bin:/usr/sbin:/sbin` (from `launch_path()`) | first executable `git` on that exact string **whose realpath is inside the admitted readable set**; a shim among them is resolved, never exec'd |
| no `--agent-path` | inherited from this process (no `PATH=` on the launch line) | same rule, on the inherited PATH — and the inherited PATH is where the admission gate does real work, because nothing has vetted it |
| candidate outside the admitted readable set | — | **hard failure, raised before the file is opened** (F-002) |
| relative or empty PATH component reached before the tool | — | **hard failure** — this process cannot resolve it the way the agent will (F-003) |
| no `git` on the effective PATH | — | **hard failure before any check launches**; never a bare `git` fallback |

**Iteration 2 claimed here that "only already-admitted `--agent-path` entries are searched".
That was false, and it is F-002.** `assert_agent_path_admitted()` validates only the
explicit `--agent-path` sequence; the inherited-PATH branch went through no admission
decision at all, and `is_tool_shim()` opened whatever it selected. The gate that makes the
sentence true is now written down rather than assumed: `select_launch_path_tool()` refuses a
candidate whose realpath is outside `isolate()`'s own `readable["entries"]`, and refuses it
*before* the read. Nothing new is admitted, scanned or made readable to buy that;
`NEVER_ADMITTED`, `DEFAULT_IMM_CANDIDATES`, the immutability proof and the NEG-5 mandatory
scan are untouched (§6, and `test_resolving_an_interpreter_admits_no_new_immutable_root`
pins both lists by value). The gate only ever refuses **more**.

### A7. F-002 — following a PATH is a read, and iteration 2 read what nothing admitted

The correction I shipped in iteration 2 is right about *which* file the pre-flight must
verify and wrong about *what it may do to find out*. Classification is
`is_tool_shim(candidate)`, and that function `open()`s the candidate. It runs in the host
process, at session-build time, **before** `sandbox-exec` is anywhere near the picture. So
"select from the inherited PATH, then classify by bytes" is, on any host whose `PATH` names
a directory the capture never scanned, an out-of-sandbox read of an unadmitted file.

The gate that was supposed to prevent this — `assert_agent_path_admitted()` — only ever saw
the explicit `--agent-path` sequence. The `launch_path()` branch that returns
`os.environ["PATH"]` bypassed it completely, and on this host that PATH begins
`/opt/homebrew/bin:/opt/homebrew/sbin:/Users/luminous/bin:…` — three directories the
readable set refuses, two of them under `NEVER_ADMITTED` roots (`/opt/homebrew`, `/Users`).

**My own test asserted the bypass as correct.** `test_an_inherited_path_git_is_followed_too`
built its candidate under `TemporaryDirectory`, called `_probe_git()` with no admission
argument, and asserted the file was read and returned. On this host that directory realpaths
to `/private/var/folders/...`, and `/private/var` is the first entry of `NEVER_ADMITTED`.
The test did not fail to cover the hole; it *documented the hole as the specification*. A
later sandbox denial does not undo a read that already happened, which is why "the profile
would have denied it anyway" is not an answer.

The fix is the smallest thing that makes the sentence in §A5 true: thread the readable set
`isolate()` already computed into the one place that turns a PATH into a filename, and
refuse there. `admitted_roots` defaults to `()`, so the refusing behaviour is what a caller
gets by forgetting — `test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing`
pins that, and asserts (on darwin) that the temporary directory really is under a
`NEVER_ADMITTED` root, so the test still describes the reported bypass and not something
weaker.

Admission is decided on the candidate's **realpath**, and that is deliberate rather than
lax: an `open()` returns the realpath's bytes, and the readable set is stated in realpaths
for the same reason the seatbelt profile is. A different *spelling* of an admitted file
(`/var/...` for `/private/var/...`) exposes nothing extra and is followed
(`test_a_differently_spelled_but_admitted_candidate_is_still_followed`); a candidate whose
realpath leaves the set is refused however innocent its spelling
(`test_a_candidate_whose_realpath_escapes_the_admitted_set_is_refused`) — which is exactly
the `/opt/homebrew/bin` → `Cellar` shape `wrap_command()` already documents.

### A8. F-003 — `shutil.which()` resolves relative components in the WRONG directory

`wrap_command()` is `cd <session>/review_root && … exec …`. The agent therefore resolves a
PATH component spelled `bin`, or an empty component, against `<session>/review_root`. The
pre-flight resolved the same string against **the review process's own current directory**,
because that is what `shutil.which(name, path=value)` does. Same PATH string, two different
files.

Measured on the pre-fix code, with a `bin/git` in each directory:

```
resolver cwd : /private/var/folders/…/T/tmphfdsjep6/resolver_cwd
agent cwd    : /var/folders/…/T/tmphfdsjep6/launch_cwd   (what wrap_command cds to)
PICKED       : /private/var/folders/…/T/tmphfdsjep6/resolver_cwd/bin/git
agent would  : /var/folders/…/T/tmphfdsjep6/launch_cwd/bin/git
```

**I chose refusal over reproduction**, and the reason is the same one F-002 turns on:
resolving the component the agent's way would mean `os.access()`-ing and then opening files
under a directory chosen by a relative string, inside a session the agent has not been
launched in — buying exactness by widening what the pre-flight touches. Refusing costs
nothing real: an absolute `--agent-path` entry is already the documented way to put a
directory on the agent's PATH, and `launch_path()` realpaths every one of them, so **no
`--agent-path` configuration can hit this refusal at all**. It is reachable only from an
inherited PATH that carries a relative or empty component.

The refusal is scoped to components that could actually change the answer. The walk is
left-to-right and stops at the first match, so a relative component *after* the directory
that supplies `git` is never reached — and cannot be reached by the agent either, whose own
left-to-right lookup has already matched. That limit is a stated behaviour with a test
(`test_a_relative_component_after_the_match_is_never_reached`), not an accident.

What this buys is the positive claim, now true as stated: every component the selection
consults is absolute, so the answer does not depend on the process's current directory —
`test_the_selection_does_not_depend_on_this_processs_directory` runs the same resolution
from the resolver's directory and from the launch directory, with a *different* `bin/git`
planted in each, and requires the same absolute answer from both.

### A9. What "PATH fidelity" now claims, exactly

Three times on this branch the defect has been a claim wider than its evidence. So, narrowly:

| Claim | True? | Bounded by |
| --- | --- | --- |
| The pre-flight verifies the git the agent's own effective PATH selects | **yes**, for every PATH the selection accepts | `PreflightGitPathSelectionTests`, incl. the end-to-end launch-line test |
| …for an admitted `--agent-path` directory that outranks `/usr/bin` | **yes** | `test_an_admitted_agent_path_git_outranks_the_system_git` |
| …for an inherited PATH | **only when the candidate's realpath is inside the admitted readable set**; otherwise the run fails closed | F-002 gate + 4 tests |
| …for a PATH with a relative or empty component before the tool | **no — refused**, deliberately, because this process cannot resolve it as the agent will | F-003 gate + 2 tests |
| The candidate is never executed to classify it | **yes** | byte scan only; `ProbeLaunchWiringTests` |
| Nothing outside the admitted readable set is opened by *this* resolution | **yes for the PATH-selected candidate**; the developer-directory target read by `resolve_developer_tool()` is out of this gate's scope and unchanged | stated in `resolve_probe_git()`'s docstring and §A6 |
| Non-darwin behaviour is *unconditionally* unchanged | **no.** Unchanged for an **admitted** candidate — no substitution, same file, absolute spelling. An **unadmitted** candidate, or a relative/empty component, that previously would have been used now raises | §A4; F-002 gate + 4 tests; F-003 gate + 2 tests |
| The behaviour change is confined to darwin | **no.** `select_launch_path_tool()` has no platform predicate; the admission refusal and the component refusal apply identically on Linux. Only the *shim substitution* in step 3 is darwin-specific **in effect**, because only darwin has a shim to substitute | §A4; the 22 `PreflightGitPathSelectionTests` carry no `@DARWIN_ONLY` marker and run on Linux CI (§5b) |
| A caller that omits `admitted_roots` still resolves | **no — it admits nothing and refuses everything** | `test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing` |

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
- I did **not** exercise the F-002 **refusal** in a real seatbelt session. The live session
  in §6 exercises the *accepting* side: its inherited PATH selects `/usr/bin/git`, whose
  realpath is inside the proven Class IMM root `/usr`, so the gate passes it. Driving the
  refusal live would mean planting a git in an unscanned directory ahead of `/usr/bin` on my
  own PATH and watching the capture abort — which proves nothing the portable tests do not,
  and which I will not do by widening admission. The refusing side is therefore covered by
  four portable tests plus the end-to-end `preflight_probe()` one, and I say so here rather
  than implying the live session covered both sides. This is the same reasoning the
  Reviewer endorsed for `--agent-path` above.
- I did **not** exercise the F-003 refusal in a real session either, for the same reason:
  it is unreachable from any `--agent-path` configuration (every entry is realpath'd and
  absolute), so provoking it live would mean editing my own shell `PATH` to carry a relative
  component. Two portable tests plus the recorded pre-fix divergence measurement (§A8) carry
  it.
- I did **not** extend the admission gate to the developer-directory read inside
  `resolve_developer_tool()` — the `<developer dir>/usr/bin/<tool>` file it opens when the
  candidate IS the shim. That path comes from `developer_dir_candidates()`, is shared
  byte-for-byte with the probe interpreter, and the dispatch freezes both
  (`resolve_developer_tool()` and the python path stay exactly as they are). It is stated as
  an explicit limit in `resolve_probe_git()`'s docstring and in §A9 rather than left for a
  reader to discover. On this host that directory is
  `/Library/Developer/CommandLineTools`, which **is** a `DEFAULT_IMM_CANDIDATES` root and
  is admitted only after passing the recursive immutability proof.
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
5. **`select_launch_path_tool(name, path_value, admitted_roots=())`** *(iteration 3, new)*
   — the one place a PATH becomes a filename, and therefore the one place both new gates
   belong. It walks the components left to right and, per component: refuses a **relative or
   empty** one (F-003 — this process cannot resolve it in the agent's directory), otherwise
   looks the name up in that single directory, and on the first match refuses the candidate
   unless its **realpath is inside an admitted root** (F-002) — a metadata-only decision,
   taken *before* `is_tool_shim()` can open anything. `admitted_roots` defaults to `()`,
   which admits nothing, so forgetting to thread it fails closed. Falling off the end raises
   the same "no git on the effective launch PATH" error as before, with the same wording.
6. **`resolve_probe_git(path_value=None, *, agent_path=(), admitted_roots=(), developer_dirs=None)`**
   *(iteration 2, rewritten; iteration 3, gated)* — **selects** the candidate through
   `select_launch_path_tool()`, then classifies it by its bytes: a real binary is returned
   as-is, a shim goes through `resolve_developer_tool(candidate, candidate)`. Iteration 1's
   signature took a fixed `system_git` and is gone; that fixed address was the defect. Its
   docstring now states the *limits* of the PATH-fidelity claim, and names the one read that
   is out of the gate's scope.
7. **`_probe_git(agent_path=(), *, admitted_roots=())`** — mirrors `_probe_python()`; one
   statement, pinned by AST test *including both keyword arguments*: dropping `agent_path`
   is the fixed-path regression, dropping `admitted_roots` is F-002.
8. **`preflight_probe()`** — `"git --version"` →
   `f"{shlex.quote(_probe_git(agent_path, admitted_roots=admitted_roots))} --version"`, with
   `admitted_roots` a new keyword-only parameter defaulting to `()`. This is the one-line
   change that closes the original finding; everything above exists to make it correct,
   PATH-faithful, fail-closed, general — and, since iteration 3, incapable of reading
   outside the readable set to get there.
9. **`isolate()`** — passes `admitted_roots=paths` to `preflight_probe()`, where `paths` is
   the list it already derived from `readable["entries"]` for the profile itself. One
   expression, no new computation, no second source of truth for what is admitted; pinned by
   `test_isolate_passes_the_readable_set_it_computed_to_the_preflight`.
10. **Commentary.** The `TOOL_SHIM_MARKER` block's "Deliberately NOT extended to the
   pre-flight's `git --version`" paragraph is replaced by the measurement that refutes it
   and by a statement of the reasoning error, so the next reader cannot re-derive the narrow
   argument from the source. `preflight_probe()`'s docstring carries the §A5 scope statement,
   and now also states that the git spelling is PATH-*selected* before it is shim-resolved.
   `resolve_probe_git()`'s docstring no longer claims the resolved path is unconditionally
   the git the agent would run; it states the two properties and the condition under which
   each holds. `launch_path()`'s docstring no longer reads as "the inherited PATH is
   followed wherever it leads" — the sentence that produced F-002 — and says instead that it
   *reports* a PATH it does not vouch for.

### `scripts/test_review_isolation.py`

+34 net across the three iterations (13 in iteration 1, +12 in the new
`PreflightGitPathSelectionTests`, −1 iteration-1 test replaced, +10 in iteration 3). One
iteration-2 test — `test_an_inherited_path_git_is_followed_too` — is **rewritten**, because
what it asserted was the F-002 bypass. See §"Unit Tests / Testing Strategy".

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
| `scripts/review_isolation.py` | generalized resolver + `launch_path()` + PATH-selecting `resolve_probe_git()` + **`select_launch_path_tool()` (admission-before-bytes and cwd-safe component walk)** + `_probe_git(agent_path, admitted_roots=…)` + `preflight_probe(admitted_roots=…)` + `isolate()` threading its own readable set + call-site fix + commentary |
| `scripts/test_review_isolation.py` | +34 net (13 iteration-1, +12 `PreflightGitPathSelectionTests`, −1 replaced, +10 iteration-3); 1 iteration-2 test rewritten because it encoded F-002; existing calls now state their admitted roots |
| `artifacts/runs/run_1cc947088a44/IMPLEMENTATION.md` | this artifact |

---

## Validation

### 1. Full suite, macOS

**Iteration 3 (this delta):**

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1259 tests in 301.176s
FAILED (failures=2, skipped=6)
```

1225 baseline + 34 net new = **1259** (iteration 1 recorded 1238 at +13; iteration 2, 1249
at +24). The only two failures are the **expected, pre-existing**
`RetainedReportWhitespaceExemptionTests` pair, which skip on CI because
`actions/checkout@v4` fetches `--depth=1`. Not fixed: every offending file belongs to
another run and is digest-bound. `skipped=6` is unchanged from iteration 2 — none of the ten
new tests is a skip on darwin either.

**Iteration 4 re-confirmation (no-drift check).** Because iteration 4 changes no code, its
gate is the evidence above plus a re-run proving nothing moved. Re-run on this tree:

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1259 tests in 294.067s
FAILED (failures=2, skipped=6)

$ python3 scripts/validate_skills.py
Skill validation PASSED (463 checks)

$ python3 scripts/verify_package.py
Package verification PASSED (109 source files)

$ git diff --check
(no output)

$ git diff --stat HEAD -- scripts/
(no output — the production and test files are byte-identical to the commit the
 iteration-3 Reviewer inspected, a02b1226774233984dc8520c3720959c74c955d9)
```

Same 1259, same two failures, same `skipped=6`, same 463 and 109. The two failures are the
whitespace pair: their output is the trailing-whitespace listing over `run_a29ac78075a9`'s
retained review artifact, which belongs to another run and is digest-bound (above).

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
Ran 1259 tests in 185.554s
FAILED (failures=2, skipped=28)

########## python:3.12 (non-root, CI-like) ##########
ERROR: test_t83_a_symlink_in_the_policy_copy_list_is_refused (test_review_isolation.SessionLayoutTests...)
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1259 tests in 209.211s
FAILED (failures=2, errors=1, skipped=28)

########## python:3.13 (non-root, CI-like) ##########
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1259 tests in 211.960s
FAILED (failures=2, skipped=28)
```

All three: **1259 run**, and the constant two failures are the same
`RetainedReportWhitespaceExemptionTests` pair that fails locally on macOS and skips on CI
under `--depth=1`. `skipped=28` vs macOS's `6` is the 22 `@DARWIN_ONLY` tests, two of which
are mine (§"Portability"). **`skipped=28` is unchanged from iterations 1 and 2**, which is
the mechanical proof that all 22 `PreflightGitPathSelectionTests` — the 12 from iteration 2
and the 10 new ones — execute on Linux rather than skipping. The docker `git version 2.47.3`
is the real, non-shim git those runs select.

**The one-off `ERROR` on 3.12, reported rather than dropped.** That loop ran while the macOS
full suite was running against the *same* working tree (docker mounts it live), and
`test_t83_a_symlink_in_the_policy_copy_list_is_refused` reads repository policy files. It did
not reproduce:

```
$ docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/w -w /w -e HOME=/tmp \
    python:3.12 python3 -m unittest discover -s scripts -p 'test_*.py'      # alone
FAIL: …RetainedReportWhitespaceExemptionTests… (×2 only)
Ran 1259 tests in 217.509s
FAILED (failures=2, skipped=28)

$ docker run … python:3.12 python3 -m unittest test_review_isolation.SessionLayoutTests -v
test_t83_a_symlink_in_the_policy_copy_list_is_refused … ok
Ran 6 tests in 0.376s
OK
```

Stated honestly: I **did not capture that error's traceback** — the loop piped through `grep`
— so "concurrent access to the shared mount" is the most likely cause and not a measured
one. What I did measure is that a clean 3.12 full run and the class in isolation both pass,
and that the test touches nothing this delta changes.

**Update (iteration 4), with what is now known and no more than that.** The full CI matrix
has since run on this exact commit (§5b): `validate (3.11)`, `validate (3.12)` and
`validate (3.13)` all succeeded, each reporting `Ran 1259 tests` / `OK (skipped=32)` — no
failures, no errors, and in particular no error in `SessionLayoutTests` on 3.12. Together
with the clean solo re-runs above, that is evidence **consistent with** concurrency-induced
interference on the live-mounted working tree rather than a defect in this delta. It is
**not** a proof of cause, and I am not upgrading it to one: I never captured the traceback,
a green re-run cannot diagnose a non-reproducing error, and **I still cannot explain what
that error was**. The disclosure stays for the next reader.

The default-**root** docker invocation was also run for continuity with §5a: all three
versions reported `Ran 1249 tests … FAILED (failures=8, errors=1, skipped=28)` — the same
**nine** pre-existing results §5a root-caused. Their names were re-listed on 3.11 and match
exactly: seven `ImmutabilityProofTests` (root ignores the mode bits the proof relies on)
plus the whitespace pair. So the delta is `+24 tests, +0 failures` under both invocations.

**Narrowed in iteration 4.** Those figures — `1249` and `+24` — are **iteration-2**
arithmetic (`1225 + 24`), and I cannot attest that the root invocation was re-measured after
iteration 3 added its 10 tests; on this tree the count is `1259` / `+34`. So read this
paragraph as: under the root invocation *as last measured*, the failure set was exactly the
nine pre-existing root-caused results and none of them was mine. The number this report
relies on for the iteration-3 tree is the **non-root** `1259` above, and remote CI (§5b),
which is also non-root, agrees at `1259`.

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

#### 5b. Remote Linux CI, on this exact commit — the evidence G7 previously lacked

Iteration 3 marked G7 `MET` while this HEAD was unpushed, so no Actions run existed for it;
the Reviewer was right to call that a claim without evidence (F-004). The branch has since
been pushed, and CI has now run **on `a02b122` itself**. Verified here rather than assumed:

```
$ gh run list --branch agent/final-review-observability-evaluation --limit 3
completed  success  Build Final Review observability and evaluation foundation  CI  \
    agent/final-review-observability-evaluation  pull_request  33187763926  2m4s  2026-08-28T15:59:02Z
completed  success  …                                                        33099895008  …
completed  success  …                                                        33099629674  …

$ gh run view 33187763926 --json headSha,jobs \
    --jq '{sha: .headSha, jobs: [.jobs[] | {name, conclusion}]}'
{"jobs":[{"conclusion":"success","name":"validate (3.12)"},
         {"conclusion":"success","name":"validate (3.11)"},
         {"conclusion":"success","name":"validate (3.13)"}],
 "sha":"a02b1226774233984dc8520c3720959c74c955d9"}

$ gh run view 33187763926 --log | grep -E "Ran [0-9]+ tests|OK \(|FAILED \("
validate (3.12)  Run deterministic tests  Ran 1259 tests in 108.920s
validate (3.12)  Run deterministic tests  OK (skipped=32)
validate (3.11)  Run deterministic tests  Ran 1259 tests in 110.378s
validate (3.11)  Run deterministic tests  OK (skipped=32)
validate (3.13)  Run deterministic tests  Ran 1259 tests in 111.298s
validate (3.13)  Run deterministic tests  OK (skipped=32)
```

**What this does and does not establish.**

- It **does** establish, for the reviewed commit and no other: `headSha` is
  `a02b1226774233984dc8520c3720959c74c955d9`, which is this HEAD; all three matrix jobs
  concluded `success`; each ran the same **1259** tests as the local macOS run; and each
  ended `OK` — zero failures, zero errors. `OK (skipped=32)`, not `FAILED (…)`.
- It **does not** establish that Linux behaviour is unchanged by this delta. It cannot:
  green CI means no existing test exercises the newly-refused case, not that the case does
  not exist. §A4 and §A9 state what actually changed on Linux.
- The `skipped=32` here versus `skipped=28` in §5's local Docker runs is **fully accounted
  for by four tests, none of them mine**: the four
  `RetainedReportWhitespaceExemptionTests` methods gated by `_require_git_range()`, which
  calls `skipTest` when the whitespace gate's base commit is unreachable — exactly the case
  under `actions/checkout@v4`'s `--depth=1` checkout. `28 + 4 = 32`. Two of those four are
  the pair that *fails* on a full local checkout (§1, §5); the other two pass locally. I
  reached this by reading the skip conditions in `scripts/test_run_logging.py` and checking
  the arithmetic — **not** by reading a per-skip listing, which `unittest` does not emit
  without `-v`, so treat it as a source-level derivation rather than a direct measurement.
  What is directly measured either way is that the count is a skip count on a green run.

### 6. A real macOS seatbelt `isolate()` session

Re-run against the **iteration-3** code, not carried over from iteration 2.

```
$ isolate("run_1cc947088a44_f002", fixture=scripts/fixtures/final_review_eval,
          enforcement="seatbelt")

effective launch PATH (no --agent-path, inherited):
  /opt/homebrew/bin:/opt/homebrew/sbin:/Users/luminous/bin:/opt/homebrew/opt/node@24/bin: ...
resolved probe python : /Library/Developer/CommandLineTools/Library/Frameworks/
                        Python3.framework/Versions/3.9/bin/python3.9

scope_enforcement: seatbelt
PROPERTIES: {"S1": "PASS", "S2": "PASS", "S3": "PASS"}
SESSION:    /private/var/folders/nz/.../T/frv_iso_usos7308
profile_digest: sha256:57128c27dbfc92ab6eacb186ab2d568f73911f5faa23844160554464e4b6f7c4

IMM/USR admitted roots (from the session's own attestation):
  IMM  /bin          IMM  /private/var/select   IMM  /Library/Developer/CommandLineTools
  IMM  /sbin         IMM  /usr                  USR  <session>/review_root
  IMM  /private/etc  IMM  /System               USR  <session>/tmp
  IMM  /dev                                     USR  <session>/home
```

**The same 8 IMM roots as every previous capture.** Nothing was admitted to make the new
gate pass; the gate passes because the git the inherited PATH selects was already inside an
admitted root.

**The PATH selection AND the new admission decision are both visible in that trace.** The
inherited PATH begins `/opt/homebrew/bin`; there is no `/opt/homebrew/bin/git`, so the walk
falls through — component by component — to `/usr/bin`, whose `git` realpaths to
`/usr/bin/git`, **inside the admitted Class IMM root `/usr`**, so the gate admits it. Only
then is it read: it carries `TOOL_SHIM_MARKER`, so it is resolved — never exec'd — to
`/Library/Developer/CommandLineTools/usr/bin/git`. This is the **inherited-PATH** branch;
the `--agent-path` branch and the *refusing* side of the gate cannot be exercised in a real
session without widening the profile, and are proven by `PreflightGitPathSelectionTests`
instead (§A6 states this).

The gate's two answers, on this host's real PATH, side by side:

```
$ select_launch_path_tool("git", launch_path(), <the 8 admitted IMM roots>)
SELECTED  : /usr/bin/git -> realpath /usr/bin/git
admitted by: ['/usr']

$ select_launch_path_tool("git", launch_path(), <the same list minus /usr>)
IsolationError: the effective launch PATH selects /usr/bin/git (realpath /usr/bin/git)
for 'git', and that file is outside every admitted readable-set root ([…]) …
```

The second call is the fail-closed behaviour on the very same host and the very same PATH,
reached by removing one root from the admitted list rather than by planting anything.

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
   `launch_path()` → `select_launch_path_tool()` → shim resolution, rather than through a
   hard-coded `/usr/bin/git`. **F-001 corrected in the live path, not only in unit tests.**
4. The file that selection opened, `/usr/bin/git`, is inside the admitted Class IMM root
   `/usr` — the admission was checked *before* the open, by the readable set this very
   session computed. **F-002 corrected in the live path**, on its accepting side; §A6 is
   explicit that the refusing side is covered portably rather than here.

**No widening (G4).** The session's `readable_set` admits exactly the eight
`DEFAULT_IMM_CANDIDATES` roots and no others (plus the three session-local Class USR roots,
which are the fixture's own and are `scanned: true`):

```
/bin  /sbin  /private/etc  /dev  /private/var/select  /usr  /System
/Library/Developer/CommandLineTools
```

`/Library/Developer/CommandLineTools` was already on that list before this change and still
had to pass the recursive immutability proof to be admitted. Resolving a tool inside it
admits nothing; the proof admits it, exactly as before. `launch_path()` adds nothing to the
readable set — it only joins directory strings — and `select_launch_path_tool()` adds
nothing either: it *consumes* the readable set and can only ever refuse more than before.

`grep -c xcrun_db control/probes/preflight.log` → **0**. No stale NEG-7 plant was left
behind (`_plant_sites()` checked afterwards: none).


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

**Mutation D — delete the F-002 admission gate** (`if not any(_is_within(target, root) …)`
→ `if False:`), i.e. iteration 2's behaviour: select from the PATH, then open whatever was
found.

```
$ python3 -m unittest test_review_isolation.PreflightGitPathSelectionTests

FAIL: test_a_candidate_whose_realpath_escapes_the_admitted_set_is_refused
FAIL: test_an_unadmitted_inherited_candidate_is_refused_without_being_opened
      AssertionError: the candidate was opened
FAIL: test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing
      AssertionError: IsolationError not raised
FAIL: test_the_preflight_refuses_an_unadmitted_git_before_anything_launches
      AssertionError: IsolationError not raised
Ran 22 tests in 0.019s
FAILED (failures=4)
```

`AssertionError: the candidate was opened` is the finding itself: with the gate removed, the
resolver reaches `is_tool_shim()` on a file under a directory the stated readable set does
not contain. That test also records every path `open()` is called with, so a future edit
that reads the candidate by some *other* spelling fails it too.

**Mutation E — delete the F-003 relative/empty refusal** (`if not component or not
os.path.isabs(component)` → `if False:`).

```
$ python3 -m unittest test_review_isolation.PreflightGitPathSelectionTests

FAIL: test_a_relative_component_that_could_change_the_selection_is_refused
      AssertionError: IsolationError not raised
FAIL: test_an_empty_path_component_is_refused_the_same_way
      AssertionError: IsolationError not raised
Ran 22 tests in 0.019s
FAILED (failures=2)
```

And on that same broken tree, the divergence measured directly — this is the output quoted
in §A8, produced by the mutated code, not predicted:

```
resolver cwd : …/tmphfdsjep6/resolver_cwd
agent cwd    : …/tmphfdsjep6/launch_cwd   (what wrap_command cds to)
PICKED       : …/tmphfdsjep6/resolver_cwd/bin/git
agent would  : …/tmphfdsjep6/launch_cwd/bin/git
```

**Mutation F — `isolate()` stops threading its readable set in** (drop
`admitted_roots=paths`).

```
FAIL: test_isolate_passes_the_readable_set_it_computed_to_the_preflight
      AssertionError: 'admitted_roots=paths' not found in 'def isolate(…'
Ran 22 tests in 0.020s
FAILED (failures=1)
```

**Mutation G — `_probe_git()` stops forwarding it** (`resolve_probe_git(agent_path=…)` with
`admitted_roots` dropped), the subtlest form of F-002: the gate exists, the call site starves
it.

```
ERROR: PreflightGitPathSelectionTests.test_an_admitted_agent_path_git_outranks_the_system_git
ERROR: PreflightGitPathSelectionTests.test_an_admitted_inherited_path_git_is_followed_too
ERROR: PreflightGitPathSelectionTests.test_the_first_admitted_agent_path_entry_wins_as_on_the_launch_line
ERROR: PreflightGitPathSelectionTests.test_the_preflight_launch_line_checks_the_agent_path_git
ERROR: ProbeInterpreterShimTests.test_on_darwin_the_shimmed_system_git_is_actually_resolved_away
ERROR: ProbeInterpreterShimTests.test_the_resolved_preflight_git_is_never_a_tool_shim
FAIL:  ProbeInterpreterShimTests.test_the_preflight_git_goes_through_the_resolver
       Lists differ: ['…resolve_probe_git(agent_path=agent_path)']
                  != ['…resolve_probe_git(agent_path=agent_path, admitted_roots=admitted_roots)']
Ran 48 tests in 0.065s
FAILED (failures=1, errors=6)
```

Starving the gate fails **closed**, loudly, in seven tests — which is the point: the failure
mode of a forgotten argument is refusal, not a silent ungated search.

**Restored, unmutated:**

```
$ python3 -m unittest test_review_isolation.PreflightGitPathSelectionTests \
      test_review_isolation.ProbeInterpreterShimTests
Ran 48 tests in 0.067s
OK

$ diff -q <throwaway copy>/scripts/review_isolation.py <working tree>/scripts/review_isolation.py
(identical — the working tree was never mutated)
```

So: a raw shim spelling back on the launch line fails 6 tests; fixed-path resolution fails 4
to 8; a deleted admission gate fails 4; a deleted relative-component refusal fails 2; an
unthreaded readable set fails 1 at the wiring and 7 at the behaviour. Every mutation was run
in a **throwaway copy of the repository** under the scratchpad and restored; the working
tree was never edited to produce these numbers.

---

## Unit Tests / Testing Strategy

`UNIT_TEST_STATUS: PASS`

**+34 net.** `ProbeInterpreterShimTests` 17 → 26 (iteration 2 replaced one),
`ProbeLaunchWiringTests` 5 → 8, and `PreflightGitPathSelectionTests` 0 → 12 → **22**.

```
$ python3 -m unittest test_review_isolation.PreflightGitPathSelectionTests \
      test_review_isolation.ProbeInterpreterShimTests \
      test_review_isolation.ProbeLaunchWiringTests
Ran 56 tests in 0.067s
OK

$ python3 -m unittest test_review_isolation.PreflightGitPathSelectionTests
Ran 22 tests in 0.018s
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
| `test_an_admitted_inherited_path_git_is_followed_too` | the no-`--agent-path` half of the same mismatch — **rewritten in iteration 3**, see below |
| `test_a_real_path_selected_git_is_used_exactly_as_selected` | **the Linux invariant** — a real candidate is verified as-is, never "improved" |
| `test_a_path_selected_shim_is_resolved_rather_than_executed` | both properties at once: right candidate found, shim resolved by bytes not exec'd |
| `test_an_unresolvable_path_selected_shim_fails_closed` | fail closed, with the installer wording |
| `test_no_git_on_the_effective_path_is_a_loud_failure` | fail closed, naming the PATH; no bare-`git` fallback |
| `test_a_non_executable_git_on_path_is_not_selected` | selection uses the shell's `X_OK` rule, so it cannot bless a file the agent cannot run |
| **`test_the_preflight_launch_line_checks_the_agent_path_git`** | **end-to-end with the REAL resolver: the agent's git is on the recorded launch line and `CommandLineTools` is not** |
| `test_an_ungettable_git_stops_the_preflight_before_anything_launches` | zero processes launched when git cannot be produced |

**F-002 — admission before bytes (6 new, all portable):**

| Test | Guarantee |
| --- | --- |
| **`test_an_unadmitted_inherited_candidate_is_refused_without_being_opened`** | **THE F-002 regression** — an inherited-PATH candidate outside the stated readable set is refused, and *nothing opened it*: `is_tool_shim` is patched to fail the test if called, AND every `open()` path is recorded and asserted not to contain the candidate |
| `test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing` | the default refuses; and on darwin it asserts the fixture directory really is under a `NEVER_ADMITTED` root, so the test still describes the reported bypass |
| `test_a_candidate_whose_realpath_escapes_the_admitted_set_is_refused` | a symlink out of the admitted set is refused — admission is a realpath decision |
| `test_a_differently_spelled_but_admitted_candidate_is_still_followed` | …and only a realpath decision: an alternate spelling of an admitted file is *not* refused, so the gate is not accidentally stricter than its reason |
| `test_the_preflight_refuses_an_unadmitted_git_before_anything_launches` | end-to-end: zero processes launched |
| `test_isolate_passes_the_readable_set_it_computed_to_the_preflight` | the wiring, at the one place it is decided — `admitted_roots=paths`, `isolate()`'s own entries |

**F-003 — the lookup does not depend on this process's directory (4 new, all portable):**

| Test | Guarantee |
| --- | --- |
| **`test_a_relative_component_that_could_change_the_selection_is_refused`** | **THE F-003 regression** — a *different* `bin/git` in the resolver's cwd and in the launch cwd; the old behaviour returns the resolver's copy, and returning **either** fails the test |
| `test_an_empty_path_component_is_refused_the_same_way` | an empty component is `.` for the shell and carries the same divergence |
| `test_the_selection_does_not_depend_on_this_processs_directory` | the positive claim: the same absolute PATH resolved from both directories gives the same answer |
| `test_a_relative_component_after_the_match_is_never_reached` | the stated limit of the refusal, tested rather than implied |

**The real host (2, `@DARWIN_ONLY`):**

| Test | Guarantee |
| --- | --- |
| `test_on_darwin_the_shimmed_system_git_is_actually_resolved_away` | against the REAL `/usr/bin/git`; resolved path is not a shim AND prints `git version` |
| `test_on_darwin_the_two_shimmed_tools_are_literally_the_same_file` | E1 asserted by `st_ino`, not quoted from an artifact |

### Portability

**34 of the 36 added tests are portable** and run in full on Linux CI (36 added, 2 removed,
`+34` net — `git diff fc4f4a8..HEAD -- scripts/test_review_isolation.py` counts them): the
shim, the developer directories, the PATH candidate gits and the real tool behind them are
all synthesised in a temporary directory, and the launch-line tests replace `subprocess.run`
with a recorder so no shim, no sandbox and no real exec is involved. **All 22
`PreflightGitPathSelectionTests` are portable** — the class contains no `@DARWIN_ONLY`
marker — and that is corroborated mechanically rather than asserted: §5a's baseline root run
reports `skipped=26` and §5's delta runs report `skipped=28`, so **exactly 2** of the 36
added tests skip on Linux, which are exactly the 2 that carry the marker. Neither is a smoke
test — one execs the resolved git and asserts its version string.

(The earlier figures "22 of 24" and "all 12" in this paragraph were iteration-2 counts left
un-updated when iteration 3 added 10 more tests. Corrected above; no test changed.)

Remote CI on this commit reports `skipped=32` rather than 28 (§5b). Those 4 extra skips are
`RetainedReportWhitespaceExemptionTests` methods that skip on a `--depth=1` checkout, not
tests of this delta; the reconciliation, and its status as a source-level derivation rather
than a measured per-skip listing, is in §5b.

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
  12 `PreflightGitPathSelectionTests` as of iteration 2 (22 after iteration 3), including the
  end-to-end launch-line assertion.
- `test_git_resolution_fails_closed_and_never_falls_back_to_the_shim` and
  `test_a_developer_dir_that_only_offers_another_git_shim_is_refused` — same guarantees,
  now driven through a PATH whose only `git` is the shim, because selection is how the
  resolution is reached.
- `test_the_resolved_preflight_git_is_never_a_tool_shim` — the "bare name on an unshimmed
  host" branch is gone; the answer is always an absolute path now, and the test skips (with
  a reason) on a host that has no git at all, which is the fail-closed case.
- `test_the_preflight_git_goes_through_the_resolver` — now pins
  `resolve_probe_git(agent_path=agent_path)` including the argument.

Iteration 3:

- **`test_an_inherited_path_git_is_followed_too` → `test_an_admitted_inherited_path_git_is_followed_too`, rewritten.**
  The old version built a git under `TemporaryDirectory` and asserted `_probe_git()` **read
  and returned it** with no admission argument at all. On this host that directory realpaths
  under `/private/var`, the first `NEVER_ADMITTED` entry — so what it pinned as correct was
  F-002 itself. The rewritten test keeps the same synthetic directory and the same
  inherited-PATH mechanism, and states the readable set that makes the read legitimate. The
  scenario it used to bless is now asserted to *fail*, by
  `test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing`.
- `test_the_preflight_git_goes_through_the_resolver` — the AST pin now covers **both**
  keywords, because `admitted_roots` is exactly as load-bearing as `agent_path`.
- Every `resolve_probe_git()` / `_probe_git()` / `preflight_probe()` call in the existing
  tests now states its admitted roots explicitly (`self.admitted()`, or for the two real-host
  tests `host_git_roots()` — the candidate's own parent directory and its realpath's parent,
  which is the narrowest admission that covers this host's PATH-selected git). That is a
  test *parameter*, not a widening: no real capture admits a temporary directory, and
  `compute_readable_set()` is untouched.

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
| **G2** preserve the pre-flight's purpose; verify the git the agent will actually use; do not delete the check | **MET (iteration 3)** | §A4 — the candidate is selected from `launch_path(agent_path)`, the same string `wrap_command()` puts on the launch line, then shim-resolved by bytes; §6 — the check still runs and still returns `git version 2.50.1 (Apple Git-155)`; `PreflightGitPathSelectionTests` (22) pins it; §7 mutations A/C show the fixed-path form failing and E the cwd divergence. **Limits stated in §A9**: a candidate outside the readable set, or a relative/empty PATH component that could change the selection, is refused rather than followed |
| **G3** resolve the real git or fail closed; no silent fallback, no silent widening | **MET** | `resolve_developer_tool()` raises; §7 shows the raise is load-bearing; §6 shows the admission list unmoved |
| **G4** do not weaken readable-set or answer-key isolation | **MET (iteration 3)** | §6 — same 8 IMM roots, same 3 session USR roots, NEG-0..NEG-8 PASS; `test_resolving_an_interpreter_admits_no_new_immutable_root` pins both lists by value. Iteration 2 *weakened* it in one direction the profile could not catch — an out-of-sandbox read of an unadmitted PATH candidate (F-002) — and that is now gated **before** the read, with the gate defaulting to admitting nothing |
| **G5** do not reinstall the Command Line Tools | **MET** | nothing was installed; resolution is a byte scan plus a path join |
| **G6** regression test at the ACTUAL EXECUTING CALL SITE | **MET** | §7 — three separate mutations, 6 / 4 / 8 failures, with the offending launch line quoted in each failure message |
| **G7** Linux CI green; all isolation/security regressions preserved | **MET — by remote CI on this exact commit** | GitHub Actions run **33187763926**, `headSha a02b1226774233984dc8520c3720959c74c955d9` — this HEAD. `validate (3.11)` success, `validate (3.12)` success, `validate (3.13)` success; each job's log records `Ran 1259 tests` then `OK (skipped=32)`: zero failures, zero errors. Commands, raw output and the skip-count reconciliation in **§5b**; local Docker corroboration (1259 each) in §5; named regression re-runs in §"Also verified green" — those were recorded against the **iteration-2** delta and are labelled as such there; the iteration-3/4 coverage of the same suites is the 1259-test run itself, locally and on CI. *Bound:* green CI shows no existing test exercises the newly-refused unadmitted-candidate case — it is not evidence that Linux behaviour is unchanged (§A4) |
| Non-darwin behaviour unchanged | **MET ONLY AS NARROWED — see §A4** | **Unchanged part:** nothing on a Linux PATH is a shim, so no resolution or substitution occurs; for an **admitted** candidate, selection alone is the whole answer and the **same binary** the bare `git` always reached is executed — `test_a_real_path_selected_git_is_used_exactly_as_selected`. Only the check *string* becomes that binary's absolute path instead of the bare word. **Changed part, stated plainly:** the F-002 admission gate and the F-003 relative/empty-component refusal have **no platform predicate**, so on Linux too a candidate whose realpath is outside the admitted readable set now raises where the bare `git` would previously have been used. That is intended and fail-closed, but it means this requirement is **not** met unconditionally, and iteration 3's unqualified "unchanged" was wrong. §5b's green CI on 3.11/3.12/3.13 shows only that no existing test exercises the refused case |
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
| **Linux** | For an **admitted** candidate: unchanged binary, no substitution — `test_a_real_path_selected_git_is_used_exactly_as_selected`, plus §5's three Docker versions and §5b's three CI jobs. For an **unadmitted** candidate: now refused, on Linux as everywhere (§A4, F-002). Not an unconditional "Linux is unchanged". |
| **Security** | ~~Only already-admitted `agent_path` entries are searched~~ — **this sentence was false when I wrote it, and is F-002.** `assert_agent_path_admitted()` gates only the explicit `--agent-path` sequence; the inherited-PATH branch was ungated and opened what it found. It is true now, and true because of a gate rather than because of an assumption: see F-002 below. `NEVER_ADMITTED`, `DEFAULT_IMM_CANDIDATES`, the immutability proof and the NEG-5 scan remain byte-for-byte untouched (§6, §A5). |
| **Regression coverage** | The Reviewer's explicit ask: `PreflightGitPathSelectionTests` — 12 portable tests, including an admitted `agent_path` directory holding a **distinct** git candidate asserted to be the one checked, the inherited-PATH half, and an end-to-end `preflight_probe()` run with the real resolver. Non-vacuity in §7. |

### F-002 (G4, MAJOR, BLOCKING) — RESOLVED

| | |
| --- | --- |
| **Finding** | With no explicit `agent_path`, `launch_path()` returns the raw inherited `PATH`; `_probe_git()` selects from it and `is_tool_shim()` **opens** the candidate — outside Seatbelt, with no readable-set admission. My own new test encoded the bypass as expected behaviour. |
| **Accepted** | Yes, entirely, including the part about my test. I re-derived it in the source: `assert_agent_path_admitted()` sees only the explicit sequence, the `os.environ["PATH"]` branch of `launch_path()` reaches `resolve_developer_tool(candidate, candidate)` directly, and that calls `is_tool_shim()`, which `open()`s the file. On this host the inherited PATH begins with three directories the readable set refuses, two of them under `NEVER_ADMITTED` roots. |
| **Fix** | `select_launch_path_tool(name, path_value, admitted_roots)` — the single place a PATH becomes a filename. The candidate's **realpath** must be inside an admitted root; otherwise `IsolationError`, raised on a metadata-only decision, *before* any byte is read. `isolate()` threads its own `readable["entries"]` through `preflight_probe()` → `_probe_git()` → `resolve_probe_git()`. The parameter defaults to `()` — admitting nothing — so a caller that forgets it refuses rather than searches. |
| **Inherited PATH preserved for admitted candidates** | Yes, and that is the live path: §6's real session selects `/usr/bin/git`, inside the proven Class IMM root `/usr`, and the capture runs exactly as before. |
| **No widening** | Nothing was admitted to make this pass. §6 shows the same 8 IMM roots and the same 3 session USR roots; `NEVER_ADMITTED`, `DEFAULT_IMM_CANDIDATES`, `prove_immutable*`, the NEG-5 scan and the generated profile are untouched. The gate can only refuse **more** than before. |
| **Regression proving no open** | `test_an_unadmitted_inherited_candidate_is_refused_without_being_opened` — patches `is_tool_shim` to fail the test if it is called, **and** records every path passed to `open()` and asserts the candidate is not among them, so the guarantee survives a re-spelling of the read. Mutation D (§7) shows it failing with `AssertionError: the candidate was opened`. |
| **The bypass test rewritten** | `test_an_inherited_path_git_is_followed_too` → `test_an_admitted_inherited_path_git_is_followed_too`: same mechanism, same synthetic directory, but the readable set is now stated, and the old no-argument scenario is asserted to **fail** by `test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing` (which also asserts, on darwin, that the fixture really is under a `NEVER_ADMITTED` root). |
| **Stated limit** | The developer-directory file that `resolve_developer_tool()` reads when the admitted candidate *is* the shim is **not** covered by this gate: it is shared with the frozen python path and the dispatch freezes both. Named in `resolve_probe_git()`'s docstring, §A6 and §A9. |

### F-003 (G2, MAJOR, BLOCKING) — RESOLVED, by refusal

| | |
| --- | --- |
| **Finding** | `shutil.which(..., path=value)` resolves relative and empty PATH components against the **review process's** cwd, while the launched check first `cd`s to `<session>/review_root`. So `PATH=bin:/usr/bin`, or an empty component, can select a different file than the agent will, and the "exact effective-path fidelity" claim was again wider than the behaviour. |
| **Accepted** | Yes. Measured, not reasoned: with a distinct `bin/git` in each directory the pre-fix code returns the **resolver's** copy while `wrap_command()` sends the agent to the launch directory (output in §A8 and §7 mutation E). |
| **Choice** | **Refuse**, not reproduce — the Reviewer allowed either. Justification: resolving the component the agent's way means `access()`-ing and opening files under a directory named by a relative string inside a session the agent has not been launched in, which buys exactness by widening what the pre-flight touches — the same trade F-002 just rejected. Refusal costs nothing real: `launch_path()` realpaths every `--agent-path` entry, so **no `--agent-path` configuration can reach this refusal**; only an inherited PATH carrying a relative or empty component can. |
| **Scope of the refusal** | Components that could still change the selection. The walk is left-to-right and stops at the first match, so a relative component *after* the directory that supplies `git` is never reached — and cannot be reached by the agent either. Stated as behaviour and tested (`test_a_relative_component_after_the_match_is_never_reached`), not left implicit. |
| **Regression with distinct candidates** | `test_a_relative_component_that_could_change_the_selection_is_refused` plants a *different* `bin/git` in the resolver cwd and in the launch cwd, stands in the resolver cwd, and fails if **either** is selected. `test_an_empty_path_component_is_refused_the_same_way` covers the empty form. |
| **The positive claim, now true** | Every component the selection consults is absolute, so the answer is cwd-independent — `test_the_selection_does_not_depend_on_this_processs_directory` runs the same resolution from both directories and requires the same answer. |
| **Claim narrowed** | §A9 states, line by line, what "PATH fidelity" now claims and where it stops. `resolve_probe_git()`'s docstring carries the same limits in the source. |

### On N-001 (non-blocking, remote CI) — now CLOSED

When iteration 3 was written this was correct and outstanding: HEAD was local-only, so the
branch's green Actions runs covered `fc4f4a8`, not this delta, and the Linux evidence on
offer was Docker reproduction rather than CI. Marking G7 `MET` anyway was the overclaim the
Reviewer raised as F-004(b).

It is closed now, and by evidence rather than by narrowing: the Coordinator pushed the
branch, and **GitHub Actions run 33187763926 ran on `a02b1226774233984dc8520c3720959c74c955d9`
— this commit** — with `validate (3.11)`, `validate (3.12)` and `validate (3.13)` all
`success`, each `Ran 1259 tests` / `OK (skipped=32)`. Confirmed from `gh` in §5b rather than
taken on report. I still do not push; the Coordinator does.

### On F-004 (G2, MAJOR, BLOCKING) — RESOLVED, in the report only

| | |
| --- | --- |
| **Finding** | The report's Linux and CI claims were wider than their evidence: "Linux is unchanged in behaviour" contradicted the deliberately cross-platform admission rule, and G7 "Linux CI green" was marked `MET` while HEAD was unpushed and no Actions run existed for it. |
| **Accepted** | Yes, both parts, without qualification. The admission gate has no platform predicate — I wrote it that way on purpose for F-002 — so "Linux unchanged" was false as stated, and my own N-001 disposition three sections later said the CI evidence did not exist, which made the report internally inconsistent. |
| **Fix (a)** | §A4 now separates *unchanged* (no shim, so no resolution or substitution; an **admitted** candidate is executed exactly as selected, same file, absolute spelling) from *changed* (an unadmitted candidate, or a relative/empty component, now raises — on Linux as on darwin). §A9 gains two rows answering "is non-darwin behaviour unconditionally unchanged?" and "is the change confined to darwin?" with **no**. The G7 and "Non-darwin behaviour unchanged" rows carry the same split, the latter re-marked **MET ONLY AS NARROWED**. |
| **Fix (b)** | G7 is now `MET` by citation, not assertion: run **33187763926**, `headSha a02b122…`, three green matrix jobs, `Ran 1259 tests` / `OK (skipped=32)` each, verified with the three `gh` commands recorded verbatim in §5b. |
| **Also** | §5's non-reproducing 3.12 `SessionLayoutTests` error is updated with the green matrix on this commit as evidence *consistent with* concurrency-induced interference — explicitly **not** upgraded to a diagnosis, and the disclosure that I never captured the traceback is kept. Stale iteration-2 test counts in §"Portability" ("22 of 24", "all 12") are corrected to 36 added / 34 portable / 22 in `PreflightGitPathSelectionTests`, and the stale `1249` figures in the G7 and G3 rows to `1259`. |
| **Scope** | **No production code and no test changed in this iteration.** `git diff fc4f4a8..HEAD -- scripts/` is byte-identical to what the iteration-3 Reviewer inspected and passed; the only file this iteration touches is this artifact. |

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
| G3 severe regression | none — locally 1259 with only the 2 known pre-existing macOS-only `RetainedReportWhitespaceExemptionTests` failures; remote CI on this exact commit is `Ran 1259 tests` / `OK (skipped=32)` on 3.11, 3.12 and 3.13 with zero failures and zero errors (run 33187763926, §5b) |
| G4 data loss / security / irreversible | none — admission lists unmoved, no profile widening, nothing installed, nothing pushed. The one G4 defect this branch did have (F-002, my own) is fixed by refusing more, never by admitting more; the PATH-selected candidate is now admission-checked before it is opened, and the one read outside that gate's scope is named in §A6/§A9 |
| G5 missing evidence | none — every claim above has its command output; what I did not verify is named in §A6; what the PATH-fidelity claim does and does not cover is enumerated in §A9; the one-off 3.12 `ERROR` and the fact that I did not capture its traceback are recorded in §5 rather than dropped, and its §5 update adds the green matrix on this commit as *consistent-with* evidence without claiming a diagnosis |

---

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
