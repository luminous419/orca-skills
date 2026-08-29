# TEST — run_1cc947088a44

Phase: TEST (iteration 1) · Risk: high · Branch `agent/final-review-observability-evaluation`,
HEAD `338fbac` at start.

The question this phase asks is not "does the mechanism work" — IMPLEMENTATION settled that
across four gates and a green CI matrix. It is **would the suite fail if it broke**, for a
mechanism that took four rounds to get right and whose failure mode is a silent security
regression.

## Summary

I attacked the three guards by **breaking them one at a time in the real module and running
the whole isolation suite against each break**, restoring from a SHA-256-verified pristine
copy after every run. Eight mutations, eight recorded transcripts — seven of them in the
table below, plus one that turned out to be a no-op and is disclosed with its correction
under Testing Strategy. Two of the three guards
are strong and behavioural. The third — the F-002 admission gate, the security one — is
strong *at the gate itself* but was resting on two weaker anchors that I found by trying to
falsify them rather than by reading them: an `open()` observer that watched exactly one of
the three doors a Python read can go through, and a `inspect.getsource()` string match that
was the **sole** guard on `isolate()` handing the pre-flight its own readable set.

Both gaps are demonstrated, not asserted. Inserting `Path(candidate).read_bytes()`
immediately before the admission gate left **both** F-002 tests green on the suite as it
stood. Widening `paths` in `isolate()` to `[…] + ["/"]` — which hands the pre-flight a set
admitting the entire filesystem — left the source pin green because the pinned substring is
still a prefix of the widened line. I closed both: the observer now records `builtins.open`,
`io.open` and `os.open`, and a new portable test drives `isolate()` for real and compares the
`admitted_roots` the pre-flight was **actually called with** against the roots `isolate()`
computed. Each is shown to fail on the mutation it exists for, and to pass on HEAD.

Everything else in the brief is **adequate, and I say so with named tests rather than
padding**. T2's refusal paths are both reachable and asserted, and I report which is a live
user-facing failure mode (F-002's) and which is defensive for every `--agent-path` capture
(F-003's), having verified the "every entry is realpath'd" justification directly rather than
taking it. T4 is answered by measurement: every constant and function of the sandbox surface
is **byte-identical** across `fc4f4a8..HEAD`. T6's skip reconciliation is **upgraded from a
derivation to a measurement** — I reproduced CI's `OK (skipped=32)` on all three Python
versions from a real `--depth=1` clone and named the four extra skips from the transcript.

The one place I have to report a genuine coverage limit rather than a fix is T3: the
*resolver* is tool-agnostic and tested as such, but the *wiring* is not generic, and I
measured that a third shimmed tool added to the pre-flight as an absolute path would be
caught on macOS and **would land green on Linux CI**. Per the task boundary I did not wire
additional tools; the coverage fact is reported.

## Analysis

### T0 — the two stale prose fixes (carried from the iteration-4 review)

Both verified against reality, not pattern-matched.

**N-004 — the stale `12`.** `artifacts/runs/run_1cc947088a44/IMPLEMENTATION.md` §A6 called
`PreflightGitPathSelectionTests` a 12-test set. Measured by parsing the class rather than
counting by eye:

```
$ python3 -c "<ast walk over scripts/test_review_isolation.py>"
PreflightGitPathSelectionTests 22        # 22 methods named test_*
class decorators: []                     # no DARWIN_ONLY / NEEDS_SANDBOX on the class
DECORATED methods: (none)
skipTest inside the class body: 0
```

So "22 portable tests" is exact: no method in the class can skip. (The class body's single
`sys.platform` reference guards an *extra assertion* inside
`test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing`, not a `skipTest`; the
test itself executes on every platform, and the Linux transcripts in §V.5 confirm it is not
among the skips.) Corrected to `22`.

**N-005 — the false "nothing pushed".** Measured:

```
$ git ls-remote origin agent/final-review-observability-evaluation
a02b1226774233984dc8520c3720959c74c955d9  refs/heads/agent/final-review-observability-evaluation
```

The branch is pushed, and the same report's G7 row *depends* on the Actions run that push
produced. The G4 row now reads `… nothing installed; the Worker did not push (the
Coordinator pushed a02b122, §5b)`, which is the narrowing the review asked for and is true.

**One thing I deliberately did not change.** The string "12 portable tests" also occurs once
more, in the `### F-001 … RESOLVED` response table. That table records what iteration 2
delivered, where 12 was the correct count, and the iteration-4 review named only the §A6
occurrence. The dispatch says *fix exactly those two and do not otherwise edit that file*, so
I left it. Stating it here so the next reader does not read the survivor as an oversight.

### T1 — the three guards, each independently

**Method.** For each guard: substitute one literal in `scripts/review_isolation.py`, run
`python3 -m unittest discover -s scripts -p test_review_isolation.py -v` (188 tests) in full,
record the transcript, restore the file from an in-memory pristine copy and assert its
SHA-256 matches. `git status --short scripts/` was clean after every batch. Baseline:

```
M0_baseline                                  Ran 188 tests in 235.679s   OK
```

| # | The guard, broken | Result | Tests that caught it |
|---|---|---|---|
| **M1** | the raw `git --version` spelling back on the pre-flight check list (**G1**) | `FAILED (failures=7)` | `test_the_preflight_git_check_execs_the_resolved_git`, `test_no_fixed_preflight_check_execs_a_tool_shim`, `test_the_python_check_is_launched_before_the_git_check`, `test_an_unresolvable_git_raises_before_any_check_can_run`, `test_the_preflight_launch_line_checks_the_agent_path_git`, `test_the_preflight_refuses_an_unadmitted_git_before_anything_launches`, `test_an_ungettable_git_stops_the_preflight_before_anything_launches` |
| **M1b** | `SYSTEM_PYTHON` back on the probe launch line (**G1**, python half) | `FAILED (failures=1)` | `test_the_probe_launch_line_execs_the_resolved_interpreter` |
| **M2** | selection reverts to a fixed `/usr/bin/git`, ignoring PATH (**F-001**) | `FAILED (failures=9)` | 8 behavioural + `test_the_preflight_git_goes_through_the_resolver` (source pin) |
| **M3** | the admission check moved to **after** the byte read (**F-002**) | `FAILED (failures=2, errors=2)` | `test_an_unadmitted_inherited_candidate_is_refused_without_being_opened`, `test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing`, + 2 errors |
| **M4b** | an empty admitted set means "admit everything" (**F-002**) | `FAILED (failures=2)` | `test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing`, `test_the_preflight_refuses_an_unadmitted_git_before_anything_launches` |
| **M6** | `_probe_git()` stops threading `admitted_roots` through (**F-002**, mirror) | `FAILED (failures=1, errors=6)` | 6 behavioural + `test_the_preflight_git_goes_through_the_resolver` |
| **M5** | the relative/empty PATH component is resolved instead of refused (**F-003**) | `FAILED (failures=2)` | `test_a_relative_component_that_could_change_the_selection_is_refused`, `test_an_empty_path_component_is_refused_the_same_way` |

**(a) G1 — a raw shim spelling on the launch line: caught, behaviourally.** All seven M1
failures read the command string actually handed to `/bin/sh`; none inspects source. The
python half (M1b) has exactly one guard per exec site — `_run_probe()` and
`preflight_probe()` build the interpreter string separately, and each has its own launch-line
test. One precise behavioural test per site is adequate; I record the number so nobody reads
"1 failure" as weakness by comparison with git's 7.

**(b) F-001 — a fixed `/usr/bin/git`: caught, behaviourally.** Eight of M2's nine failures are
behavioural, including the end-to-end `test_the_preflight_launch_line_checks_the_agent_path_git`,
which runs the **real** resolver and reads the recorded launch line. The regression cannot
survive by re-spelling.

**(c) F-002 — "rejects without opening": the decisive property, examined closely.**

The brief asks whether the test *actually observes opens* rather than only the return value,
and whether it would fail if the order were swapped. Both halves, answered separately:

*Order-sensitivity: yes, and robustly.* `test_an_unadmitted_inherited_candidate_is_refused_without_being_opened`
patches `review_isolation.is_tool_shim` with `side_effect=AssertionError("the candidate was
opened")`. Every classification read in the current call graph goes through that function, so
any reordering that classifies before admitting converts the expected `IsolationError` into an
`AssertionError` and the test fails. M3 confirms this empirically — it is one of the four
M3 casualties.

*Observing opens: it did, but through one door of three.* The helper recorded only
`builtins.open`. Measured, in isolation:

```
builtins.open is io.open   -> True
builtins open() recorded   : True
Path.read_bytes recorded   : False        # pathlib calls io.open, a separate name binding
os.open recorded           : False
```

So a read spelled any way other than a bare `open()` was invisible to the assertion. This is
not hypothetical. Inserting one line into `select_launch_path_tool()` immediately **before**
the admission gate:

```python
target = _realpath(candidate)
Path(candidate).read_bytes()          # the out-of-gate read, in another spelling
if not any(_is_within(target, root) for root in roots):
```

leaves **both** F-002 tests green on the suite as it stood — the exact defect class F-002 was
raised for, undetected. Closed by recording all three doors (`builtins.open`, `io.open`,
`os.open`). With the strengthened observer the same mutation fails both tests
(§V.6, N2). Both continue to pass unmutated.

*The residual limit, stated.* The strengthened observer covers every read that reaches a
file through Python's own open paths. A read performed by a subprocess, or through a C
extension that bypasses `os.open`, would still be unobserved. Nothing in the current call
graph does that, and the `is_tool_shim` net catches anything routed through classification;
this is the boundary of the claim, not a known hole.

**Guards resting on a source/AST pin rather than behaviour.** Four exist in this area. Three
are backed by behavioural tests and are therefore belt-and-braces:

- `test_the_probe_interpreter_goes_through_the_resolver` and
  `test_the_preflight_git_goes_through_the_resolver` pin `_probe_python()` / `_probe_git()`
  body statements — but M1b and M2/M6 show the behavioural launch-line tests fail on the same
  defects anyway.
- `test_no_xcselect_linked_binary_is_executed_to_resolve_the_developer_dir` scans
  `developer_dir_candidates()` for `subprocess`/`xcrun`/`popen`. This one is *inherently* a
  source assertion — the property is "this code never does X", which has no observable
  behaviour to assert on a host where X would succeed. Appropriate as written.

The fourth was a real problem:

> `test_isolate_passes_the_readable_set_it_computed_to_the_preflight` asserts two substrings
> in `inspect.getsource(review_isolation.isolate)`. **It was the only guard** on the wiring
> that F-002's fix depends on — no test ran `isolate()` far enough to reach `preflight_probe()`
> (the two seatbelt `isolate()` tests in the suite both expect failure *before* the
> pre-flight). And the pin admits the defect: changing
> `paths = [entry["path"] for entry in readable["entries"]]` to
> `… + ["/"]` keeps the pinned substring as a prefix, so **the pin passes while the pre-flight
> is handed a set that admits the whole filesystem**. Measured: that mutation produces exactly
> one failure, and it is the new behavioural test, not the pin.

This is precisely the shape that bit an earlier run on this branch — green source pins over an
unasserted runtime value — so I closed it rather than only reporting it. The new
`test_isolate_really_hands_the_preflight_the_set_it_computed` substitutes `preflight_probe()`
with a recorder that captures the keyword it was actually called with and raises before any
process launches, then compares that value with the roots `isolate()` computed. It is portable
(`imm_candidates=()` proves no host root, so the readable set is the session's own three Class
USR roots on every platform) and it launches nothing (`sandbox-exec` need only exist). The
existing source pin is kept — it catches deletion and rename cheaply — but it is no longer
carrying the property alone.

### T2 — the refusal paths are real code paths

Both refusals are reachable and asserted. They differ in kind, and the brief is right that the
difference matters.

**F-002's refusal is a LIVE failure mode, not a defensive branch.** It fires for a real,
common operator configuration: any inherited PATH that offers a `git` whose realpath is
outside the admitted readable set, ahead of `/usr/bin`. On macOS the obvious instance is a
Homebrew git — `/opt/homebrew` is in `NEVER_ADMITTED`, and `wrap_command()`'s own docstring
records that every entry in `/opt/homebrew/bin` symlinks out to `Cellar`/`Caskroom`. The
consequence is that `isolate()` raises and no session is built.

Bounded honestly: **this host does not currently trigger it.** Its PATH begins
`/opt/homebrew/bin`, but there is no `git` there today (`os.path.exists("/opt/homebrew/bin/git")`
→ `False`), so the walk falls through to `/usr/bin`, which `/usr` admits. What I *did* exercise
live is the same gate's two answers on this host's real PATH, by removing one root from the
admitted list rather than by planting anything (§V.4):

```
(1) with the 8 admitted IMM roots
    SELECTED   : /usr/bin/git -> realpath /usr/bin/git       admitted by ['/usr']
    resolved to: /Library/Developer/CommandLineTools/usr/bin/git
(2) the same host, the same PATH, /usr removed from the admitted list
    IsolationError: the effective launch PATH selects /usr/bin/git (realpath /usr/bin/git)
    for 'git', and that file is outside every admitted readable-set root ([...]) ...
```

Asserted by `test_an_unadmitted_inherited_candidate_is_refused_without_being_opened` (refusal
**and** no read) and `test_the_preflight_refuses_an_unadmitted_git_before_anything_launches`
(end to end, no check launched). The failure is loud, fail-closed, and its message names the
remedy (`--allow-read` plus `--agent-path`). It is still a real user-visible failure, and an
operator on Homebrew git will meet it.

**F-003's refusal is defensive for every `--agent-path` capture and live only through an
inherited PATH.** The claim that "no `--agent-path` configuration can reach it because every
entry is realpath'd" is **true**, and I verified it directly rather than reading it:

```
$ cd <tmp>; launch_path(["relbin"])   -> /private/var/.../tmp.../relbin:/usr/bin:/bin:/usr/sbin:/sbin
$ launch_path(["./relbin"])           -> (identical — realpath'd)
$ launch_path([""])                   -> /private/var/.../tmp...:/usr/bin:/bin:/usr/sbin:/sbin
  every component absolute?           -> True
```

`launch_path()` realpaths each `--agent-path` entry and appends only the four absolute
`LAUNCH_PATH_TAIL` directories, and the inherited PATH is not included at all in that branch.
So the refusal cannot fire. Through an inherited PATH it can, and only when the relative or
empty component precedes the directory that supplies git — measured:

```
PATH=':/usr/bin'    -> REFUSED  (empty component, reached first)
PATH='bin:/usr/bin' -> REFUSED  (relative component, reached first)
PATH='/usr/bin:'    -> /usr/bin/git   (the trailing empty component is never reached)
```

Asserted by `test_a_relative_component_that_could_change_the_selection_is_refused`,
`test_an_empty_path_component_is_refused_the_same_way`,
`test_a_relative_component_after_the_match_is_never_reached` (the scoping limit) and
`test_the_selection_does_not_depend_on_this_processs_directory` (the positive half). A leading
`:` in `PATH` is an ordinary shell mistake, so this is reachable — but it is far rarer than
the F-002 case, and it cannot be reached at all by the configuration the dispatch actually
uses (`--agent-path`).

### T3 — cross-tool generality

**The measurement the generalization rests on, re-verified on this host.** All six named tools
are one inode, and the link count says the blast radius is far larger than six:

```
/usr/bin/git      ino=1152921500312571585  nlink=78  size=118928
/usr/bin/python3  ino=1152921500312571585  nlink=78  size=118928
/usr/bin/cc       ino=1152921500312571585  nlink=78  size=118928
/usr/bin/clang    ino=1152921500312571585  nlink=78  size=118928
/usr/bin/make     ino=1152921500312571585  nlink=78  size=118928
/usr/bin/dyld_info ino=1152921500312571585 nlink=78  size=118928
/bin/echo         ino=1152921500312571396      (distinct — a real binary)
/bin/ls           ino=1152921500312571414      (distinct — a real binary)
```

Enumerating `/usr/bin`, `/bin`, `/usr/sbin`, `/sbin` for that inode returns **78 names**:

```
DeRez GetFileInfo ResMerger Rez SetFile SplitForks ar as asa bison bm4 c++ c++filt c89 c99
cc clang clang++ clangd cmpdylib codesign_allocate cpp ctags ctf_insert dsymutil dwarfdump
dyld_info flex flex++ g++ gatherheaderdoc gcc gcov git git-receive-pack git-shell
git-upload-archive git-upload-pack gm4 gnumake gperf hdxml2manxml headerdoc2html indent
install_name_tool ld lex libtool lipo lldb llvm-g++ llvm-gcc lorder m4 make mig nm nmedit
objdump otool pagestuff pip3 python3 ranlib resolveLinks rpcgen segedit size sourcekit-lsp
strings strip swift swiftc unifdef unifdefall vtool xml2man yacc
```

`is_tool_shim()` classifies all six probed names as the shim and `/bin/echo` / `/bin/ls` as
real, which is exactly what `preflight_probe()`'s scope statement asserts in prose and
`test_no_fixed_preflight_check_execs_a_tool_shim` asserts over the real list.

**The resolver's generality IS tested.** `test_the_resolver_decides_from_the_file_not_from_the_tool_name`
drives `resolve_developer_tool()` with a fake shim **named `git`** through a developer
directory offering a real `git`, and asserts the answer is neither the shim nor name-derived.
`test_the_unshimmed_spelling_is_returned_untouched_for_a_real_tool` and
`…_when_the_tool_is_absent` pin the pass-through in both directions. Nothing in
`resolve_developer_tool()` keys off a tool name, and a test proves it.

**The wiring's generality is NOT tested, and here is the measured consequence.** I added a
third shimmed tool to `preflight_probe()`'s fixed check list two ways and ran the three
relevant classes (57 tests):

| how the third tool is added | result | which assertion caught it |
|---|---|---|
| appended bare: `"cc --version"` | `FAILED (failures=1)` | `test_no_fixed_preflight_check_execs_a_tool_shim` — the check **count** (4 → 5) and the "no PATH lookup" assertion. Both are **portable**. |
| replacing `/bin/ls .` with `"/usr/bin/cc --version"` | `FAILED (failures=1)` | the same test, but at `self.assertFalse(review_isolation.is_tool_shim(executable))` — classification of the **real host binary** |

The second row is the honest coverage fact: the count is unchanged, so the only thing that
fires is a classification of `/usr/bin/cc` **as it exists on the running host**. On Linux
`/usr/bin/cc` is a real binary, that assertion is vacuous, and the same defect lands green on
CI. Nothing routes a newly added tool through `select_launch_path_tool()` /
`resolve_developer_tool()` automatically; the seam exists and `preflight_probe()`'s docstring
points at it, but it is opt-in.

**So:** a third shimmed tool added later is protected **on macOS** (by count if spelled bare,
by real-host classification if spelled absolutely) and only partially on Linux. Per the task
boundary I did **not** wire additional tools — that would be scope expansion. The fact is
reported.

### T4 — no sandbox weakening, asserted not assumed

**The strongest available assertion first: the surface did not move, by digest.** I extracted
each sandbox-surface constant and function at `fc4f4a8` (the last commit before this delta)
and at `HEAD` and compared SHA-256 of the exact source span:

```
IDENTICAL fc4f4a8..HEAD (26 of 26 compared):
  const ADMISSION_CLASSES        const DEFAULT_IMM_CANDIDATES   const ISOLATION_SCHEMA_VERSION
  const MANDATORY_CARVE_OUTS     const NARROWABLE_CHECKS        const NEVER_ADMITTED
  const SCAN_PASSES_ALL          const SCAN_PASSES_IMM          const SCAN_VOCABULARIES
  def _clause                    def _run_neg7                  def _run_neg8
  def assert_agent_path_admitted def assert_attempt_in_domain   def assert_carve_outs_denied
  def assert_no_unscanned_descendant                            def attest_seeds
  def compute_readable_set       def compute_traversal_set      def enumerate_boundaries
  def inventory_session_home     def prove_immutable            def prove_immutable_narrowing
  def render_seatbelt_profile    def run_probes                 def scan_readable_set
CHANGED (1):
  const LAUNCH_PATH_TAIL   (absent -> new; it is the four absolute system PATH directories)
```

The admission lists, the scan, the recursive proof, the profile renderer, the probe battery
and the attestation are **byte-identical**. The delta added a constant and touched only the
resolution/selection path.

Per item:

- **`NEVER_ADMITTED` intact and pinned.** Pinned by value in
  `test_resolving_an_interpreter_admits_no_new_immutable_root`, together with
  `DEFAULT_IMM_CANDIDATES`, plus an explicit disjointness assertion over both. Behaviourally
  enforced by `test_a_never_admitted_candidate_that_exists_is_refused_outright`,
  `test_private_var_and_library_are_not_admissible_by_habit`,
  `test_the_real_private_var_is_refused_on_the_supported_host` (darwin) and
  `test_a_supplied_candidate_list_replaces_the_default_and_never_widens_it`. **Adequate.**
- **`DEFAULT_IMM_CANDIDATES` pinned.** Same test, by value, in order, with the assertion that
  `DEFAULT_DEVELOPER_DIR` is inside an already-listed candidate — i.e. that resolving the
  interpreter admits nothing new. The live session confirms the same 8 IMM roots (§V.4).
  **Adequate.**
- **Generated profile unchanged.** `render_seatbelt_profile()` is byte-identical (above), and
  its output is pinned structurally by `ProfileRenderingTests`: `t86` (clause order — "seatbelt
  is last-match-wins, so the order IS the semantics"), `t86b` (no bare
  `(allow file-read-metadata)`), `t86c` (root as `literal`, never `subpath`), `t86d` (clause 6
  denies metadata as well as data), `t86e` (every carve-out denied) and `t86f` (a generated
  profile really parses under `sandbox-exec`). NEG-6 re-checks the digest of the profile the
  session actually wrote. **Limit, stated:** there is no byte-level golden of a rendered
  profile, so "unchanged" rests on the renderer's own digest plus the structural pins, not on
  a stored expected string. Adding a golden would pin host-specific root lists and is not
  worth its brittleness; I am recording the shape of the guarantee, not proposing a change.
- **Immutability proof still fatal, with the session removed.**
  `test_an_unprovable_candidate_is_fatal_through_isolate_under_seatbelt` pushes an unprovable
  root through `isolate()` itself, asserts `"immutability proof FAILED"`, asserts the failure
  **names the caller's own root**, and asserts
  `list(base.glob("frv_iso_*")) == []` — the half-built session is gone. It then pushes the
  same root through `--enforcement none` and asserts no IMM entry is admitted. Paired with
  `test_a_failing_root_is_never_admitted_as_imm` and
  `test_unenforced_still_refuses_a_usr_root_that_carries_key_material`. **Adequate, and it is
  the exact property named in the brief.**
- **NEG-5 mandatory content scan still runs.**
  `test_t95_every_admitted_root_carries_its_class_pass_set_and_vocabulary` asserts, over a real
  probe record, that every IMM root carries passes `["A","B","C","D"]` with the `key_material`
  vocabulary and every USR root `["A","B","C","D","S"]` with `key_leak`, and that
  `content_scanned` is an integer count.
  `test_t95_there_is_no_opt_in_imm_content_scan_anywhere` asserts there is no
  `--scan-imm-content` flag, no `SCAN_PASSES_IMM_CONTENT` attribute and no `imm_content_scan`
  key — i.e. the scan cannot be turned into an opt-in. **Adequate.** Both are darwin-gated;
  they appear by name in the Linux skip listing (§V.5), which is the seatbelt dependency the
  hard constraints explicitly permit. The live session (§V.4) records `NEG-5 PASS`.

### T5 — regression areas preserved

Re-run **against this HEAD**, not carried over from the iteration-2 measurements the report
labels as such. Counts and outcomes in §V.3.

- **Answer-key isolation** — `NegativeContractTests.test_neg2_the_sandboxed_process_cannot_open_the_key`
  (open denied, with its own positive control), `…test_neg3_discovery_is_blocked_not_merely_reading`
  (the directory cannot even be enumerated), `…test_neg4_git_cannot_reach_the_key_either`,
  `…test_t97_a_planted_key_copy_in_the_real_temp_dir_is_unreachable`,
  `…test_t98_every_alias_spelling_is_denied_too`,
  `…test_the_data_volume_alias_of_the_repository_is_denied`,
  `…test_a_symlink_out_of_review_root_does_not_bypass_the_profile`, plus
  `DiscoveryOracleTests.test_the_content_marker_lives_only_inside_the_key` and
  `…test_a_path_on_stderr_is_not_a_discovery` (the oracle itself is not fooled by an echoed
  path). Every denial is paired with a positive control in the same method, so a probe that
  used the wrong path cannot pass silently. **Live and meaningful.** Darwin-gated — Linux
  keeps the portable `LeakScanTests` / `FixtureIntegrityTests` halves.
- **Evidence-bundle sanitization** — `BundleSanitizationTests`, 20 tests: `t71` foreign
  absolute path, `t73` secret-named assignment, `t74` URL credential, `t75` dispatch
  capability — each asserted to **never reach the bundle**; `t76` a clean log embedded
  verbatim with zero redactions (the anti-vacuity control); `t78` structure preserved under
  every poisoned case; `t79`/`t79b`/`t710` residue omitted **with a reason** rather than
  silently shipped; `t711` the raw local log byte-identical after export;
  `test_the_omission_reason_vocabulary_is_closed`;
  `test_a_missing_log_is_reported_as_unreadable_not_as_clean`. **Adequate — this is the
  strongest-covered area of the five.**
- **Attempt domain (F-602)** — `AttemptDomainTests`: `t131` `isolate()` and `repatriate()`
  refuse `0`, `-1`, `-12` and build/create nothing; `t133a` non-integer objects refused at the
  function boundary, including `False`/`True`/`2.0`/`"2"`/`None` (the bool exclusion exists
  because `attempt=True` silently aliased attempt 1 and `attempt=False` wrote
  `FINAL_REVIEW_iterationFalse.md`); `t133b` the CLI keeps argparse's own exit 2; `t134` the
  gate is the **first** statement of every gated boundary — asserted by parsing each function
  body, not by substring search, so a gate deleted in one function and left in a sibling still
  fails. **Adequate.**
- **Provenance** — `AuditProvenanceTests`: default is `unknown` and never `accepted`; every
  void reason round-trips; the writer is fail-closed in both directions; one accepted dispatch
  is returned, two are *reported not resolved*, none produces no verdict; a voided record is
  never returned as a verdict; grouping comes from the field, not the filename. Plus
  `ProvenanceLadderTests` and `AttemptDomainProvenanceTests`. **Adequate.**
- **Observability neutrality** — `FinalReviewObservabilityNeutralityTests`: the golden capture
  is compared **on encoded bytes** against the pre-OS-22 commit `1045815`;
  `test_the_golden_records_its_own_provenance` prevents a re-captured baseline from being
  passed off as the original, and `test_the_golden_covers_both_skills_all_workflows_and_both_profiles`
  prevents a shrunken fixture from silently shrinking the claim. **Adequate — the anti-shrink
  test is what makes this meaningful rather than decorative.**

### T6 — the skip reconciliation, upgraded from derivation to measurement

The report reconciles CI's `skipped=32` against local Docker's `28` as four
`_require_git_range()`-gated tests and **labels it a source-level derivation rather than a
measured per-skip listing**. I measured it.

The premise first: `.github/workflows` uses `actions/checkout@v4` with no `fetch-depth`, which
defaults to a shallow `--depth=1` checkout. Confirmed by reading the workflow. And:

```
deep clone   at 338fbac : git rev-parse --verify 1045815^{commit}  -> 104581524c1d…  (reachable)
shallow --depth=1 clone : git rev-parse --verify 1045815^{commit}  -> (not reachable)
```

Then two real clones, six full Docker runs, `-v` so the transcript names every skip:

| clone | 3.11 | 3.12 | 3.13 |
|---|---|---|---|
| deep | `Ran 1259 tests` `FAILED (failures=2, skipped=28)` | `FAILED (failures=2, skipped=28)` | `FAILED (failures=2, skipped=28)` |
| **shallow (`--depth=1`, what CI does)** | `Ran 1259 tests` **`OK (skipped=32)`** | **`OK (skipped=32)`** | **`OK (skipped=32)`** |

Measured skip composition (from the transcripts, not arithmetic):

```
deep    28 = 21 'the seatbelt backend is darwin-only; ...'
             + 6 'requires --orca-runtime and a ready Orca runtime'
             + 1 '/usr/bin/sandbox-exec is not present on this host'
shallow 32 = the same 28
             + 4 'base commit 1045815 is unreachable (shallow or grafted checkout)'
```

and the four extra, by name, from `comm -13 deep shallow`:

```
test_run_logging.RetainedReportWhitespaceExemptionTests.test_only_retained_reports_are_exempt
test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_gate_fails_again_once_the_exemption_is_removed
test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_pattern_does_not_leak_outside_the_audit_directories
test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_whitespace_gate_passes_over_the_whole_os22_range
```

**The derivation was correct in every particular**, and the arithmetic `28 + 4 = 32` is now a
measurement on three Python versions. It also explains the *failure* count the report never
had to explain: two of those four are the pair that fails on a full checkout, so the shallow
run has zero failures and ends `OK` — reproducing CI's exact `Ran 1259 tests` / `OK
(skipped=32)` locally.

**One phrasing correction to the report's §5b, offered rather than applied.** §5b says the 22
difference between macOS's 6 and Linux's 28 "is the 22 `@DARWIN_ONLY` tests". The measured
breakdown is 22 platform-gated tests in `test_review_isolation` — **21** carrying the
`DARWIN_ONLY` reason and **1** carrying `'/usr/bin/sandbox-exec is not present on this host'`
(`ProfileRenderingTests.test_t86f_a_generated_profile_actually_parses`, which is
`@NEEDS_SANDBOX` rather than `@DARWIN_ONLY`). An AST count of the two decorators over the
module gives `DARWIN_ONLY 21 · NEEDS_SANDBOX 14 · union 22`, which agrees. The report's number
is right; only the label on one of the 22 is slightly off. **I did not edit
`IMPLEMENTATION.md` for this** — the artifact contract permits exactly the two T0 fixes — and
I have deliberately **not** promoted its "source-level derivation" label either. That label
was honest when written and remains an accurate description of what §5b did; the measurement
lives here.

## Changes

Two test-file changes. **No production code was changed.**

1. `PreflightGitPathSelectionTests.opened_paths()` — the F-002 read observer now records
   `builtins.open`, `io.open` **and** `os.open` instead of `builtins.open` alone, so
   "the candidate was not opened" is a statement about the read rather than about one spelling
   of it. The docstring records the measurement behind the change.
2. `PreflightGitPathSelectionTests.test_isolate_really_hands_the_preflight_the_set_it_computed`
   — new, portable: substitutes `preflight_probe()` with a recorder, runs `isolate()` under
   `enforcement="seatbelt"` with `imm_candidates=()` and a stand-in `SANDBOX_EXEC` (nothing is
   executed — the recorder raises first), and asserts the `admitted_roots` the pre-flight was
   actually called with equals the roots `isolate()` computed, and is not empty.

Imports `contextlib` and `io` added.

No sandbox was weakened; no admission list, profile, proof or probe was touched; no Apple shim
was executed; the git check was neither deleted, stubbed nor made conditional; no other run's
artifacts were modified; no OS-23, H-1/H-2/H-4/H-5, Risk, Quality Profile, Agent Profile or
Final Review lifecycle change; no VERSION or LICENSE change; no new PR, no merge, no
force-push, no push.

## Modified Files / Artifacts

| path | change |
|---|---|
| `scripts/test_review_isolation.py` | +78 / −3: three-door read observer; one new portable wiring test; `contextlib`/`io` imports |
| `artifacts/runs/run_1cc947088a44/IMPLEMENTATION.md` | 2 lines, the two T0 fixes only (N-004 `12`→`22`; N-005 `nothing pushed` narrowed) |
| `artifacts/runs/run_1cc947088a44/TEST.md` | this artifact (new) |

## Validation

### V.1 — full macOS suite

```
$ python3 -m unittest discover -s scripts -p 'test_*.py' -v
Ran 1260 tests in 306.060s
FAILED (failures=2, skipped=6)

FAIL: test_the_gate_fails_again_once_the_exemption_is_removed
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range
      (both test_run_logging.RetainedReportWhitespaceExemptionTests)
```

1259 → **1260**: the one test added here. The two failures are exactly the expected
pre-existing `RetainedReportWhitespaceExemptionTests` pair — they belong to another run, they
are digest-bound, and per the task boundary they are not fixed here. Nothing else fails.

The 6 skips, **measured from the transcript** rather than assumed, are all one reason and none
of them mine:

```
6  'requires --orca-runtime and a ready Orca runtime'
   test_orca_runtime.FinalReviewRuntimeIntegrationTests.test_final_review_terminal_freshness
   test_orca_runtime.OrcaRuntimeIntegrationTests.test_runtime_scenarios
   test_orca_runtime.QualityProfileRuntimeIntegrationTests.test_every_dispatch_of_the_run_carries_the_quality_gate
   test_orca_runtime.QualityProfileRuntimeIntegrationTests.test_quality_profile_phase_filtering
   test_orca_runtime.RiskRuntimeIntegrationTests.test_risk_conditional_phase_graph
   test_orca_runtime.SessionReuseRuntimeIntegrationTests.test_session_reuse_terminal_accounting
```

This closes the last arithmetic in §T6 as a measurement too: Linux's 28 = these same **6** plus
the **22** platform-gated tests in `test_review_isolation`, with nothing unaccounted for on
either side.

### V.2 — package validators and whitespace

```
$ python3 scripts/validate_skills.py
Skill validation PASSED (463 checks)
Validated both skills, shared templates/reviews, routing, and policy gates.       # exit 0

$ python3 scripts/verify_package.py
Package verification PASSED (109 source files)                                     # exit 0

$ git diff --check
(no output)
```

463 and 109, as expected. `TEST.md` and the two edited lines carry no trailing whitespace and
no trailing blank line at EOF.

### V.3 — named regression suites, re-run against this HEAD

```
Provenance         Ran 19 tests in   0.239s  OK
AttemptDomain      Ran 26 tests in   1.076s  OK     (F-602)
Neutrality         Ran 15 tests in   2.717s  OK     (observability-neutrality)
Sanitiz            Ran 20 tests in   0.028s  OK     (evidence-bundle sanitization)
Observab           Ran 12 tests in   1.510s  OK
ImmutabilityProof  Ran 14 tests in   0.511s  OK     (the recursive proof)
Neg5Contract       Ran  2 tests in   0.689s  OK     (the mandatory content scan)
NegativeContract   Ran 11 tests in 228.627s  OK     (answer-key isolation, real sandboxed
                                                     processes with paired positive controls)
LeakScan           Ran 11 tests in   0.220s  OK     (+ DiscoveryOracle, FixtureIntegrity)
EvidenceBundle     Ran  5 tests in   0.023s  OK
```

All green against **this** HEAD, replacing the report's iteration-2 measurements of the same
suites.

### V.4 — a real macOS seatbelt `isolate()` session

```
$ python3 scripts/final_review_eval.py isolate --run-id run_1cc947088a44_test \
      --enforcement seatbelt --out <scratch>/live.json
rc=0
stderr: (empty — zero `xcrun` "couldn't create cache file" lines, no installer dialog)

scope_enforcement : seatbelt
properties        : {"S1": "PASS", "S2": "PASS", "S3": "PASS"}
session           : /private/var/folders/nz/.../T/frv_iso_13rqq7ao
profile_digest    : sha256:f14907740de3dafee7f1446fc691939cd9939f52901684e3b57628fca2cf3150

admitted roots (from the session's own ISOLATION.json):
  IMM /bin        IMM /private/var/select   IMM /Library/Developer/CommandLineTools
  IMM /sbin       IMM /usr                  USR <session>/review_root
  IMM /private/etc IMM /System              USR <session>/tmp
  IMM /dev                                  USR <session>/home

probes: NEG-0 PASS  NEG-1 PASS  NEG-2 PASS  NEG-3 PASS  NEG-4 PASS
        NEG-5 PASS  NEG-6 PASS  NEG-7 PASS  NEG-8 PASS
no_unscanned_descendant: PASS
```

**The same 8 IMM roots as every previous capture** — nothing was admitted to make anything
pass. `control/probes/preflight.log`, which is the deliverable:

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

No bare `git`, no `/usr/bin/git`, no `/usr/bin/python3` on any launch line; the PATH-correct
real git ran and answered. The session was torn down afterwards
(`isolate --teardown <session>`); the other `frv_iso_*` directories under the host temp
directory belong to earlier runs and were left untouched.

### V.5 — Linux 3.11 / 3.12 / 3.13 via Docker, full suite

Two clone shapes, three interpreter versions each, non-root and CI-like, `-v` so every skip
is named. The working tree's `test_review_isolation.py` was copied into both clones, so these
runs include the two changes made here.

```
$ docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v <clone>:/w -w /w python:<v> \
      python3 -m unittest discover -s scripts -p 'test_*.py' -v

deep2_3.11     Ran 1260 tests in 184.461s   FAILED (failures=2, skipped=28)   exit 1
deep2_3.12     Ran 1260 tests in 210.002s   FAILED (failures=2, skipped=28)   exit 1
deep2_3.13     Ran 1260 tests in 210.540s   FAILED (failures=2, skipped=28)   exit 1
shallow2_3.11  Ran 1260 tests in 184.748s   OK (skipped=32)                   exit 0
shallow2_3.12  Ran 1260 tests in 211.347s   OK (skipped=32)                   exit 0
shallow2_3.13  Ran 1260 tests in 210.594s   OK (skipped=32)                   exit 0
```

**1260 on Linux too, and the skip counts did not move** (28 deep / 32 shallow, identical to the
pre-change runs in §T6). So the added test is not a Linux skip, and neither changed test became
one. Confirmed positively rather than inferred from the totals — all three appear as `ok` in
the transcripts:

```
The same wiring, BEHAVIOURALLY -- because the test above is a source pin. ... ok
F-002. The inherited PATH is not an admission decision, and classifying a ... ok
The default is the refusing one, which is what makes forgetting to thread the ... ok
```

The three `shallow2` runs are the CI-equivalent shape (`actions/checkout@v4` default
`--depth=1`) and reproduce CI's exact `Ran <n> tests` / `OK (skipped=32)` with zero failures
and zero errors. The three `deep2` runs carry the same two pre-existing whitespace failures as
macOS, for the reason §T6 measures.

No anomaly appeared in any of the twelve Docker runs recorded for this phase (six pre-change in
§T6, six here), including on 3.12 — I note this because the report's §5 records a single
non-reproducing 3.12 `SessionLayoutTests` error, and I did not reproduce it.

### V.6 — non-vacuity of everything added

Every claim above that a test catches a defect was produced by causing the defect and watching
the test fail. The seven production-code mutations in T1's table, each run against the full 188-test
isolation module and each restored with a SHA-256 check; plus the two mutations that justify
the two additions, run in the repository itself:

```
### N1  isolate() widens what it hands the pre-flight:  paths = [...] + ["/"]
FAIL: test_isolate_really_hands_the_preflight_the_set_it_computed
Ran 31 tests   FAILED (failures=1)
      -> the pre-existing source pin test_isolate_passes_the_readable_set_it_computed_to_the_preflight
         PASSED on this defect. That is the false negative the new test exists to close.

### N2  an out-of-gate read of the candidate, spelled Path(candidate).read_bytes()
FAIL: test_an_unadmitted_inherited_candidate_is_refused_without_being_opened
FAIL: test_the_default_admits_nothing_so_an_unthreaded_call_reads_nothing
Ran 31 tests   FAILED (failures=2)
      -> the SAME mutation against the pre-change (builtins-only) observer:
         Ran 2 tests   OK      -- i.e. it was invisible before this change.
```

`git status --short scripts/` was clean after each restore.

## Unit Tests / Testing Strategy

`UNIT_TEST_STATUS: PASS`

No production code changed, so the mandatory gate's trigger did not fire; it is satisfied
anyway, because the two test changes were added, executed, shown to pass on HEAD and shown to
**fail on the defect each exists for** (§V.6). The strategy for this phase was falsification
rather than enumeration: for every claim in the brief I asked what would have to be true for
it to be false, then built that case and ran it. That is what produced the two findings — both
were areas the previous phase reasonably believed were covered, and both were only visible from
the failing side.

One methodological correction I made mid-phase, recorded because it is the same error class
this branch keeps producing: my first F-002 "admitted_roots defaults to admitting everything"
mutation changed `select_launch_path_tool()`'s default parameter value. `resolve_probe_git()`
always passes `admitted_roots` **positionally**, so the default is never used and the mutation
was a no-op. It reported `OK`, and reporting that `OK` as "the guard is weak" would have been a
claim wider than its evidence in the other direction. I re-ran it against the value that
actually flows (`M4b`: an empty admitted set meaning `[/]` inside the function body) and it
fails 2 tests. Both the mistake and the corrected result are in T1's table.

## Review Feedback Resolution

| ID | From | Resolution |
|---|---|---|
| **N-004** | iteration-4 review, MINOR, non-blocking | RESOLVED. §A6's "12 portable tests" → "22 portable tests", verified by parsing the class (22 `test_*` methods, no class or method skip decorator, no `skipTest` in the body) rather than by trusting the corrected count elsewhere in the report. |
| **N-005** | iteration-4 review, MINOR, non-blocking | RESOLVED. The G4 row's "nothing pushed" is narrowed to "the Worker did not push (the Coordinator pushed `a02b122`, §5b)", verified against `git ls-remote origin`, which resolves the branch to `a02b1226774233984dc8520c3720959c74c955d9`. |

No blocking findings were outstanding at entry to this phase, and this phase raises none
against IMPLEMENTATION. The two coverage gaps it found are in the **test** file and are fixed
here; the one it could not fix without scope expansion (T3's Linux-side wiring generality) is
reported as a coverage fact with its measurement, not as a defect in the approved
implementation.

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
