# Worker Result

STATUS: BLOCKED

This Run's TEST phase was dispatched to execute the missing post-fix isolated §7 baseline
(DESIGN.md's amended `B-1′ … B-7`) and, on success, to make that capture the current
authoritative §7 baseline. **The baseline was not captured.** `B-1′` — the very first step, the
production `isolate --enforcement seatbelt` command — does not terminate on this host, and two
further defects in the isolation mechanism would each independently prevent `B-2′` from producing a
usable report even if `B-1′` did terminate.

Per the TEST phase's Mandatory Invariant ("TEST phase에서 발견한 production defect를 임의로
production code에서 수정하지 않고 finding으로 보고한다") and the dispatch's explicit instruction
for exactly this branch, **no production code was written, modified or patched.** The three defects
are reported below as findings, each with a reproduction that was actually run.

`artifacts/runs/run_644c005bc9db/BASELINE_RESULT.md` and `artifacts/runs/run_5967188007ce/` are
**unchanged**. No supersession notice was added and no new `BASELINE_RESULT.md` was written,
because the dispatch conditions both on `B1-B6` all passing, and none of them can be evaluated.

---

## Test Scope / Existing Test Assessment

Scope: the `B-1′ … B-7` procedure at `artifacts/runs/run_75c5c6046f35/DESIGN.md:1298-1337`, driven
through the real shipped commands, against the real default readable set on the real host. Not a
synthetic re-implementation of any of it.

**A new baseline Run namespace was created** rather than reusing a historical one, per ordering
rule 4:

```
orca orchestration run-create --objective "OS-22 section 7 isolated baseline re-capture (R1): ..."
→ run_a45e71e518d4
```

`run_644c005bc9db` and `run_5967188007ce` were read but never written to.

**Existing test assessment — the gap that matters.** `scripts/test_review_isolation.py` is
extensive (T-8.1 … T-8.11, T-9.1 … T-9.9) and every one of its cases passes. It nevertheless does
not catch any of the three findings below, for one structural reason that is worth stating plainly:

* Every isolation test drives `build_session()`, `prove_immutable()`, `scan_readable_set()`,
  `render_seatbelt_profile()` and the probe battery over **synthetic trees and hand-written
  literal path strings** (e.g. `writable=["/session/review_root"]` at
  `scripts/test_review_isolation.py:677,742,790`). No test invokes the production `isolate()`
  entry point over `DEFAULT_IMM_CANDIDATES`, and no test writes a file from **inside** the
  generated sandbox. The suite therefore proves the pieces and never proves the assembly.
* The one mechanism DESIGN put in place to catch exactly that — G.5's mandatory pre-flight probe
  running "the *actual* resolved agent command under the *actual* generated profile" — is not
  wired to the agent command in the shipped code (**F-403**), and its four hard-coded checks are
  all read-only, so it passes on a session no agent can actually work in.

## Added / Modified Tests

**None.** This dispatch is a baseline *capture*, not a test-authoring task, and the defects it
surfaced are production defects that the Mandatory Invariant forbids this phase from fixing. Each
finding below names the regression test that should accompany its fix, so the IMPLEMENTATION phase
that owns the fix also owns the test. Three throw-away reproduction scripts were written outside
the repository (in this session's scratchpad) and are **not committed**; each is reproduced inline
below so the finding does not depend on a file that no longer exists.

## Behavior Covered

Real execution, in this order:

| step | command actually run | outcome |
|---|---|---|
| `B-1′` | `python3 scripts/final_review_eval.py isolate --run-id run_a45e71e518d4 --enforcement seatbelt --attempt 0 --out <scratch>/iso_probe0.json` | **SIGKILL after 17m44.790s** (`rc=137`); no `ISOLATION.json`; see **F-401** |
| launch line | `wrap_command(<SESSION>, 'codex-sol …')` run for real against the orphaned session's `scope.sb` | `rc=126`, `codex-sol: Operation not permitted` — operator-fixable via `--allow-read`, recorded for completeness |
| write probe | the real launch line running `/usr/bin/python3` inside the real profile | **every writable-set path is unwritable**; see **F-402** |
| credential probe | the real launch line, plus unsandboxed controls | session `HOME` is empty and the host credential store is denied; see **F-403** |
| `B-2′ … B-7` | — | **not reached.** No Task was dispatched, so there is no dispatch-layer failure to record under `B-3`, and `B-4R`'s retry budget was never entered: retrying an identical command that cannot terminate is not a retry, it is the same failure again |
| `B1 … B6` | — | **not evaluable.** All six depend on artifacts that `B-1′`/`B-2′` never produced |

## Execution

Command: `python3 scripts/final_review_eval.py isolate --run-id run_a45e71e518d4 --enforcement seatbelt --attempt 0 --out <scratch>/iso_probe0.json`
Result: FAIL — `Killed: 9`, `rc=137`, `real 17m44.790s / user 3m14.988s / sys 0m41.230s`

Command: `python3 scripts/validate_skills.py`
Result: PASS — `Skill validation PASSED (463 checks)`

Command: `python3 scripts/verify_package.py`
Result: PASS — `Package verification PASSED (109 source files)`

Command: `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py`
Result: PASS — byte-identical

Command: `git diff --check 1045815..HEAD`
Result: FAIL (`rc=2`) — **expected and non-blocking.** Every reported line is trailing whitespace in
`artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md` and
`artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration2.md`, both committed before this run. No
other path is reported.

Command: `python3 -m unittest discover -s scripts -p 'test_*.py'`
Result: FAIL (`Ran 1134 tests in 764.461s`, `FAILED (failures=2, skipped=6)`) — **the two expected
pre-existing failures and nothing else.** Both are
`test_run_logging.RetainedReportWhitespaceExemptionTests` cases
(`test_the_gate_fails_again_once_the_exemption_is_removed`,
`test_the_whitespace_gate_passes_over_the_whole_os22_range`), and both name only the two
`run_4d1c47c838db` review files above. Every isolation, key-unreachability, redaction, audit,
evaluation, lifecycle, Risk, Quality and Agent Profile test passed — including the whole
`sandbox-exec` denial battery, which is why F-401/F-402/F-403 are gaps in what the suite covers
rather than failures it reports.

## Failures / Findings

Three findings. All three are **production defects in the isolation mechanism itself**, not
properties of this capture attempt: each reproduces from a clean checkout on this host, and each
blocks the §7 baseline independently of the other two. None was fixed here.

---

### F-401 — `scan_readable_set()` pass B opens non-regular files, so `isolate` cannot terminate over `/dev`

Severity: MAJOR · Blocking: YES · Responsible Phase: implementation
Location: `scripts/review_isolation.py:585-612` (the `for name in filenames:` loop, and
`entry.read_text(encoding="utf-8")` at line 605); `DEFAULT_IMM_CANDIDATES` includes `/dev` at
`scripts/review_isolation.py:123`; `SCAN_PASSES_IMM = ("A","B","C","D")` at line 499; the NEG-5
loop that applies pass B to every admitted Class IMM root.

**Issue.** Pass B's loop skips symlinks and `__pycache__` and then calls `read_text()` on
everything else in `filenames`. It never requires a **regular** file. `/dev` is a default Class IMM
candidate; on this host `os.walk("/dev")` yields 462 names, of which **459 are neither regular
files nor symlinks** — character and block devices. `read_text()` on a device that never reaches
EOF is an unbounded allocation, and `read_text()` on a blocking non-regular entry never returns at
all.

**Reproduction 1 — the production command.** The `B-1′` invocation above was SIGKILLed at
17m44.790s with only 3m14.988s of user time, i.e. it spent the bulk of its life not computing. The
orphaned session pins where it died:

```
<SESSION>/control/scope.sb            written 18:17:10   (readable set + immutability proof: ~4m06s)
<SESSION>/control/probes/preflight.log written 18:17:11   (pre-flight: ~1s, all four checks rc=0)
<SESSION>/control/ISOLATION.json       ABSENT
process                                SIGKILL at ~18:30:49
```

`scope.sb` and `preflight.log` exist and `ISOLATION.json` does not, so the process passed
`compute_readable_set()`, wrote the profile, passed the pre-flight, and then spent **~13.6 minutes
inside `run_probes()`** before the kernel killed it. `run_probes()` is where NEG-5 re-scans every
admitted root with pass B. The `Killed: 9` (rather than a hang) is the unbounded-allocation shape.

**Reproduction 2 — the `/dev` facts, bounded so nothing is allocated unboundedly.**

```python
top = next(os.walk("/dev"))
non_regular = [n for n in top[2]
               if not stat.S_ISREG(os.lstat(os.path.join("/dev", n)).st_mode)
               and not os.path.islink(os.path.join("/dev", n))]
```
```json
{"dev_in_DEFAULT_IMM_CANDIDATES": true, "SCAN_PASSES_IMM": ["A","B","C","D"],
 "dev_filenames": 462, "non_regular_non_symlink_in_filenames": 459,
 "sample": ["aes_0","afsc_type5","apfs-raw-device.2.0","auditpipe","auditsessions",
            "autofs","autofs_control","autofs_homedirmounter"]}
{"dev_zero_bytes_read": 67108864, "hit_eof": false, "seconds": 0.001}
```

A bounded incremental read of `/dev/zero` returns 64 MB in 1 ms and never reaches EOF.
`read_text()` has no such bound; that is the defect, and 64 GB/s explains the SIGKILL rather than a
hang.

**Reproduction 3 — the production function, with a positive and a negative control, memory-safe.**
A FIFO blocks on read instead of allocating, so it demonstrates the same "pass B opens a
non-regular directory entry" defect without risking the host:

```python
root = tempfile.mkdtemp(prefix="frv_fifo_repro_")
open(os.path.join(root, "ordinary.txt"), "w").write("hello")
os.mkfifo(os.path.join(root, "a_fifo"))
review_isolation.scan_readable_set(key, root,
    passes=review_isolation.SCAN_PASSES_IMM, vocabulary="key_material")
```

| control | result |
|---|---|
| root **with** the FIFO | `{"root_entries": ["a_fifo","ordinary.txt"]}` then **no return after 20s**; killed |
| root **without** the FIFO (the same script, one line commented out) | returns immediately: `{"returned": true, "files": 1, "content_scanned": 1}` |

**Consequence.** `B-1′` cannot complete, so `B-2′ … B-7` cannot start and `B1 … B6` cannot be
evaluated. NEG-5 is mandatory and fail-closed by design, so there is no supported way around it.

**Required Action (for the phase that owns the fix, not for TEST).** In pass B, classify each entry
with `lstat()` and read only `S_ISREG` entries, with an explicit and tested policy for special
files; preserve the existing symlink, carve-out, prefilter and pass-C behavior. Add (a) a unit
regression test over a synthetic root containing a FIFO — the reproduction above is directly
reusable and needs no root privilege — and (b) a bounded end-to-end smoke test that proves the
production `isolate` entry point terminates over the real default candidates and records a NEG-5
result. Note that `SCAN_PASSES_IMM` gaining mandatory pass B is the F-201 correction landed in
`75afdae`; before it, Class IMM never opened these entries, which is why this surfaces only now.

---

### F-402 — the generated profile's write-allow clause uses an unresolved path spelling, so the isolated Reviewer can write nowhere

Severity: MAJOR · Blocking: YES · Responsible Phase: implementation
Location: `scripts/review_isolation.py:1862-1864` (`writable = [str(session / "review_root"), …]`
in `isolate()`), against `scripts/review_isolation.py:1066,1119` (`build_session()` returns the
raw `mkdtemp()` path, not its realpath) and `scripts/review_isolation.py:957` (`compute_readable_set()`
*does* apply `_realpath()` to every Class USR root).

**Issue.** `compute_readable_set()` resolves symlinks before admitting a root — its own docstring
says why: *"Symlinks are resolved before anything else — `/tmp` → `/private/tmp` on darwin —
because an unresolved spelling in a seatbelt profile does not match."* `isolate()`'s `writable`
list is built straight from the unresolved session path and never gets that treatment. On darwin
`tempfile.gettempdir()` is `/var/folders/…`, whose realpath is `/private/var/folders/…`, so the two
clauses of the same generated profile disagree:

```
;; clause 3, reads   (from compute_readable_set -> _realpath)
148:    (subpath "/private/var/folders/…/frv_iso_…/review_root")
149:    (subpath "/private/var/folders/…/frv_iso_…/tmp")
150:    (subpath "/private/var/folders/…/frv_iso_…/home"))

;; clause 5, writes  (from isolate()'s `writable`, unresolved)
236:    (subpath "/var/folders/…/frv_iso_…/review_root")
237:    (subpath "/var/folders/…/frv_iso_…/tmp")
238:    (subpath "/var/folders/…/frv_iso_…/home")
```

Seatbelt matches on the resolved path, so clause 5's allow matches nothing and the preceding
`(deny file-write*)` stands unconditionally.

**Reproduction — the real launch line, the real profile, the real session.**

```
/bin/sh -c "$(wrap_command(<SESSION>, '/usr/bin/python3 -c <probe>'))"
```
```json
{"home_access_W_OK": false,        "home_real_write": "Operation not permitted",
 "tmp_access_W_OK": false,         "tmp_real_write": "Operation not permitted",
 "review_root_access_W_OK": false, "review_root_real_write": "Operation not permitted",
 "mkdir_dot_codex": "Operation not permitted"}
```

Reads work in the same process — the earlier probe listed `review_root` and resolved `$HOME` — so
this is specifically the write clause, not a broken session.

**Consequence.** `B-2′` requires the isolated Reviewer to write its Review Result into
`<SESSION>/review_root/artifacts/runs/<run>/`, and `B-5′` repatriates that file. Under the shipped
profile it cannot be written at all. This is independent of F-401: fixing the `/dev` scan yields a
session that attests clean and is still unusable.

**Why nothing caught it.** Two reasons, both worth fixing alongside the defect:

1. **No test writes from inside a generated profile.** T-8.6 asserts clause order, the closed
   metadata traversal set, and that the profile parses (`sandbox-exec -f … /usr/bin/true` exits 0).
   The write clause is only ever checked as *text* (`scripts/test_review_isolation.py:690,717`),
   and every fixture passes an already-resolved-looking literal such as `"/session/review_root"`,
   so the two spellings can never diverge in a test.
2. **The attestation hides it.** `build_attestation()` puts every `writable_set` entry through
   `_path_field()` (`scripts/review_isolation.py:1702`), which correctly redacts a foreign absolute
   path to `<REDACTED:foreign_absolute_path>`. A reviewer checking `B6` against
   `FINAL_REVIEW_ISOLATION.json` therefore sees three placeholders and cannot detect the mismatch.
   The redaction is right; the point is that the attestation is not where this can be caught.
3. **The negative battery does not cover writes.** NEG-0 … NEG-8 are read/discovery denial probes.
   A profile that denies *everything* passes all of them, and `S1`/`S2`/`S3` would all read `PASS`
   on a session in which no review can be performed.

**Required Action.** Apply the same `_realpath()` treatment to the writable set that the readable
set already gets (or have `build_session()` return the resolved session path, which fixes
`wrap_command()`'s `TMPDIR`/`HOME` spellings in the same stroke — they are cosmetic for the
environment variables but are the same latent hazard). Add a probe to the mandatory battery that
**writes** a file to each writable-set root from inside the sandbox and fails closed if any write
is refused, and assert it in `scripts/test_review_isolation.py` against a real generated profile
rather than against literal strings.

---

### F-403 — the mandatory pre-flight never runs the real agent command, `orca_check_probe()` is dead code, and the session `HOME` has no provisioning path

Severity: MAJOR · Blocking: YES · Responsible Phase: implementation (with a DESIGN follow-up for O-2)
Location: `scripts/review_isolation.py:1880` (`preflight = preflight_probe(session)` — the
`agent_command` parameter at line 1919 is never supplied by any caller); `scripts/review_isolation.py:1964`
(`orca_check_probe()` — no caller anywhere: `grep -rn "orca_check_probe" --include=*.py .` returns
only the definition); `scripts/final_review_eval.py:1284-1332` (the `isolate` parser has no way to
pass an agent command); `scripts/review_isolation.py:1081` (`(session / "home").mkdir()`).

**Issue, part 1 — the pre-flight does not do what DESIGN G.5 says it does.** G.5 makes the
pre-flight probe mandatory and specifies that `isolate` "runs the *actual* resolved agent command
under the *actual* generated profile with a trivial non-review prompt and a short timeout." The
shipped `preflight_probe()` accepts an `agent_command` and no caller passes one, so the probe runs
only its four hard-coded checks — `python3 -c 'print(1)'`, `/bin/echo`, `git --version`, `/bin/ls .`.
All four are read-only and none is the agent. The orphaned session's `preflight.log` shows all four
at `rc=0` on a session that is unwritable (F-402) and in which the agent cannot start (below).

**Issue, part 2 — O-1's fail-closed assertion is not wired.** `orca_check_probe()` exists to run
`orca orchestration check --terminal <handle>` inside the sandbox and is documented as the concrete
assertion that O-1 is not merely assumed. It has no caller in production or in tests, so O-1 is in
fact still assumed.

**Issue, part 3 — the session `HOME` cannot be provisioned, and the project's real Final Review
agent cannot authenticate without it.** `build_session()` creates `<SESSION>/home` empty and there
is no supported way to place anything in it before `isolate()` scans it, writes `ISOLATION.json`
and returns. The three prior §7 captures (`run_ff587481a820`, `run_92759e0e1034`,
`run_5967188007ce`) all used the agent command `codex-sol`, which is
`exec codex --model … "$@"`; `codex` reads its credentials from `$CODEX_HOME`, default
`$HOME/.codex`. Measured, on this host:

| probe | result |
|---|---|
| inside the real profile: session `HOME` contents, and host credential file | `{"home_listing": [], "session_auth_exists": false, "host_auth_readable": false, "host_auth_error": "Operation not permitted"}` |
| unsandboxed control: `HOME=<fresh empty dir> codex exec …` | `rc=1` — `401 Unauthorized … Missing bearer or basic authentication in header` |
| unsandboxed control: same, with `auth.json` copied into `<fresh HOME>/.codex/` | `rc=0`, the trivial prompt answers correctly |
| unsandboxed control: `CODEX_HOME=<read-only dir holding auth.json>` | `rc=1` — `failed to initialize in-process app-server client: Permission denied` |

So the agent needs a credential file inside a **writable** state directory, `<SESSION>/home` is the
only in-design candidate, and nothing can put the file there while the attestation still covers it.
Placing it there after `isolate()` returns would post-date the `readable_set` scan and the NEG-5 re-scan
that `S3` rests on, which is precisely the kind of undisclosed drift `B6` exists to prevent — so it
is not a workaround this phase was willing to take.

DESIGN already names this, at **O-2**: *"Mitigation available but **not designed here** because it
changes the agent's environment: give the isolated agent a session-scoped state directory via
environment variables."* The finding is that this is not an optional hardening against a residual
risk — it is a **precondition** for `B-2′` to run at all with the project's real agent.

For completeness, and separable from the above: the launch line also fails earlier, because the
agent's own executable is outside the computed readable set —
`cd <review_root> && … exec /usr/bin/sandbox-exec -f <scope.sb> codex-sol` returns `rc=126`,
`/Users/<REDACTED:absolute_local_path>/bin/codex-sol: Operation not permitted`. That part **is**
operator-fixable through the designed `--allow-read` widening (the directory holding `codex-sol`,
and the `codex` binary's own directory, each then exhaustively scanned), and it is recorded here
only so the next attempt does not rediscover it. It is not itself the finding.

**Required Action.** Pass the resolved agent command to `preflight_probe()` from `isolate()` (and
expose it on the `isolate` parser), call `orca_check_probe()` from the same place with the
dispatch's terminal handle so O-1 is asserted rather than assumed, and make both failures exit 4
with the log printed. Provide an attested way to provision the session-scoped agent state
directory — the natural shape is an explicit, operator-supplied seed list copied into
`<SESSION>/home` **before** the readable-set scan runs, so the scan and NEG-5 cover it and the
attestation stays honest — which is the O-2 mitigation DESIGN deferred and which should come back
through DESIGN rather than be improvised in IMPLEMENTATION.

---

### Non-blocking note — an interrupted `isolate` leaves an orphaned session that `--teardown` refuses

`isolate()`'s `except BaseException:` handler removes a half-built session, but a `SIGKILL` cannot
run it. The orphan then carries `control/scope.sb` and `control/probes/` but no `ISOLATION.json`,
and `--teardown` correctly refuses it:

```
$ python3 scripts/final_review_eval.py isolate --run-id run_a45e71e518d4 --teardown <SESSION>
isolation contract violation: <SESSION> carries no control/ISOLATION.json; refusing to remove a
directory that is not a completed isolation session
rc=2
```

The refusal is the right default — a mistyped argument must not delete an unrelated tree — but it
leaves the only supported cleanup path unable to clean up the one case that most needs it, and the
orphan holds a materialized copy of the fixture subject. The orphan from this attempt was removed
by hand after its evidence was extracted, and `assert_no_stale_plants()`'s two plant sites
(`tempfile.gettempdir()`, `~/Library/Caches`) were confirmed clean. Worth a narrow follow-up
(e.g. `--teardown --force` gated on the `frv_iso_` prefix plus a `control/scope.sb` marker); not
blocking, and not fixed here.

## Remaining Gaps

* **The §7 baseline remains uncaptured, and `R1` remains open.** `artifacts/runs/run_644c005bc9db/BASELINE_RESULT.md`
  is still designated the current §7 baseline and still evaluates only `B1-B5`; no committed run
  contains a `FINAL_REVIEW_ISOLATION*.json`. Nothing in this dispatch changed that, and nothing
  should have: the supersession notice and the new `BASELINE_RESULT.md` are conditioned on
  `B1-B6` passing, and none of the six is evaluable.
* **F-401, F-402 and F-403 must all be fixed before the capture is re-attempted.** They are
  sequential gates, not alternatives: F-401 blocks `B-1′`, F-402 blocks the Reviewer's report,
  F-403 blocks the agent's start. A re-attempt after fixing only one of them will fail at the next.
* **Retry budget deliberately not consumed.** `B-4R`'s `DEFAULT_MAX_ITERATIONS = 5` governs
  *dispatch-layer* failures under a new Task/Dispatch identity and a new isolation session. No
  Task was ever dispatched, so there was no dispatch-layer failure; re-running a command that
  cannot terminate would have burned ~90 minutes of wall clock to produce four more identical
  `rc=137`s and no new information.
* **Orca Run `run_a45e71e518d4` exists and is empty.** It was created for this capture and no
  `artifacts/runs/run_a45e71e518d4/` directory was written, because nothing reached the point of
  producing a retainable artifact. It is available for the re-attempt, or can be abandoned.
* **Not verified, because it was never reached:** every `B2 … B7` behavior — scoring, the
  environment-safety grep over the isolation session path spelling (`B3`), the D.6 key-leak scan of
  the retained reviewer input (`B4`), byte-identical re-score (`B5`), the schema-2.0 audit bundle
  export (`B-7`), and the whole of `B6`.
* **Unchanged by design:** `VERSION`, `LICENSE-DECISION.md`, detection/search policy, prompts,
  reviewer instructions, the fixture, the adjudication key, the scorer, and the `H-1`/`H-2`/`H-4`/`H-5`
  conclusions. No detection-quality conclusion is drawn anywhere in this document, and no
  cross-capture comparison is made.
* **`artifacts/runs/run_75c5c6046f35/FINAL_REVIEW.md` changed on disk during this dispatch.** The
  version this Task was dispatched against carried `R1` as *"the repository still designates a
  pre-isolation B1-B5-only capture as the current §7 baseline"*, Responsible Phase `test`. The file
  now on disk carries a different `R1` — the `/dev` pass-B non-termination — with Responsible Phase
  `implementation`. **F-401 above is an independent reproduction of that finding**, arrived at by
  running the production command before the file was re-read, and it confirms it. The coordinator
  should be aware that this TEST dispatch was issued against the superseded text.
