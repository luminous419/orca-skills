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

---

# TEST iteration 2 -- R1 correction retry

STATUS: BLOCKED

The §7 baseline capture was **re-attempted for real** now that F-401, F-402 and F-403 are closed,
and it got much further than iteration 1: `B-1′` **succeeded**, the project's real Final Review
agent **authenticated and ran inside the kernel-enforced session**, one real Final Adversarial
Review was **dispatched and completed**, and `B-5′`, `B-6′` and `B-7` all ran. **The capture is
still not a baseline.** Two of the six criteria fail, for two new defects that iteration 1 could
not have reached because it never got past `B-1′`:

* **B1 fails** — no dispatch settled. The isolated Reviewer finished its review and wrote a usable
  report, then could not deliver `worker_done`, because the `orca` CLI **cannot be executed inside
  the sandbox** and cannot be made reachable by any widening the shipped scanner will admit.
  DESIGN's **O-1** is not merely undischarged; it is **false on this host**. See **F-501**.
* **B3 fails** — the retained `FINAL_REVIEW_ISOLATION.json`, which is the artifact `B6` rests on,
  carries the **local username** and the **isolation session path spelling** verbatim in
  `traversal_set[]` and in the NEG-5 per-root records, while every neighbouring path field is
  correctly redacted. See **F-502**. This is the same defect class as **R6**, the finding that
  superseded the `run_92759e0e1034` capture, in a field that did not exist when R6 was written.

A third defect does not fail a criterion but blocks *retaining* a capture: `B-5′`'s two repatriated
outputs land at paths A.6's single `.gitattributes` exemption does not cover, so committing a
completed capture fails the repository's own whitespace gate. See **F-503**.

Per the TEST phase's Mandatory Invariant and the dispatch's explicit instruction for this branch,
**no production code was written, modified or patched.** Both defects are reported below as
findings with reproductions that were actually run, one of them reproduced against attestations
built by a *different* phase.

`artifacts/runs/run_644c005bc9db/BASELINE_RESULT.md` and `artifacts/runs/run_5967188007ce/` are
**unchanged**: no supersession notice was added and no new `BASELINE_RESULT.md` was written,
because both are conditioned on `B1-B6` all passing. The new capture is retained under
`artifacts/runs/run_ea96552d2a8a/` and is labelled, in its own `EXPLORATORY_RESULT.md`, as an
**exploratory run and not a baseline** — which is what `B6`'s own text requires of a capture that
misses a criterion, and which is why there are not two documents claiming to be "current".

## Test Scope / Existing Test Assessment

Scope: the full `B-1′ … B-7` procedure at `artifacts/runs/run_75c5c6046f35/DESIGN.md:1308-1337`,
as amended by this Run's D-6.8/D-6.9 seed contract, driven through the real shipped commands
against the real default readable set, the real fixture and the **real** `codex-sol` agent with the
**real** credential. Not a synthetic re-implementation of any of it, and not a repeat of
IMPLEMENTATION's own end-to-end proof — that proof used a synthetic seed and `/bin/echo` as the
agent command and says so; this is the formal capture with the real agent authenticating.

**A new baseline Run namespace was created**, per ordering rule 4:

```
orca orchestration run-create --objective "OS-22 section 7 isolated baseline capture (TEST
  iteration 2, R1 correction retry): B-1prime..B-7 under seatbelt with the D-6.8/D-6.9 seed
  contract"
→ run_ea96552d2a8a
```

`run_644c005bc9db`, `run_5967188007ce` and `run_a45e71e518d4` were not written to.
**`run_a45e71e518d4` is abandoned** — it is iteration 1's empty, never-populated namespace, no
`artifacts/runs/run_a45e71e518d4/` directory exists, and none will be created. It is recorded here
so it is not mistaken for a third live capture.

**Existing test assessment.** `scripts/test_review_isolation.py` (T-8.1 … T-8.11, T-9.1 … T-9.9)
and `scripts/test_final_review_eval.py` (T-10.1 … T-10.19) all pass, including every case
IMPLEMENTATION added for F-401/F-402/F-403. They still do not catch either finding below, for two
structural reasons worth stating because each names the test that should accompany the fix:

* **F-501 is invisible to the suite because no test executes `orca` — or any host-installed
  tool that is not already in the readable set — from inside a generated profile.** `orca_check_probe()`
  now has a caller (that was F-403's fix) but no test drives it against a real profile; T-10.12
  covers `--agent-path`/`PATH` construction as text. The probe is only exercised when an operator
  passes `--terminal`, and no capture had ever done so.
* **F-502 is invisible because `assert_retained_path_field` is applied field-by-field at the call
  sites in `build_attestation()`, and T-8.7 asserts P-PATH over the fields that *are* wired
  (`readable_set[]`, `writable_set[]`, `denied_roots[]`, `session_root`, `review_root`) rather than
  over the document as a whole.** A whole-document assertion — every string in the serialized
  attestation that looks like an absolute path must pass `assert_retained_path_field` — would have
  failed on `traversal_set[]` the day it was added, and is the test the fix needs.

## Added / Modified Tests

**None.** This dispatch is a baseline *capture*, not a test-authoring task, and both defects it
surfaced are production defects that the Mandatory Invariant forbids this phase from fixing. Each
finding names the regression test that should accompany its fix, so the phase that owns the fix
also owns the test. Every measurement below was run from the repository or from this session's
scratchpad; nothing was committed except the capture artifacts named in `## Execution`.

## Behavior Covered

Real execution, in this order.

| step | what actually ran | outcome |
|---|---|---|
| `B-1′` | `isolate --run-id run_ea96552d2a8a --enforcement seatbelt --attempt 1 --seed <USER_HOME>/.codex/auth.json:.codex/auth.json --allow-read <USER_HOME>/bin --allow-read /opt/homebrew/Caskroom/codex/<v>/bin --agent-path <USER_HOME>/bin --agent-path /opt/homebrew/Caskroom/codex/<v>/bin --agent-command "codex-sol exec 'reply with the single word ok'"` | **rc=0** in `real 14m2.870s / user 11m55.022s / sys 1m21.445s`; `control/ISOLATION.json` written |
| pre-flight | the five checks, the fifth being the **real agent** | all `rc=0`; **`codex-sol` authenticated from the seeded credential and answered** |
| `B-2′` | one Final Adversarial Review dispatched into a **new** terminal running the G.5 launch line, cwd `<SESSION>/review_root`, no terminal reuse | the review **completed in 2m05s** and wrote `FINAL_REVIEW.md`; the dispatch **did not settle** — see **F-501** |
| `B-3` | `final-review-audit-write … --provenance voided --void-reason settlement_failure --settlement not_settled` | immutable record written; `final-review-audit-provenance` reports `accepted_dispatch_key: null` and one `no_accepted_dispatch` violation — the honest record |
| `B-4R` | one attempt made; retries 2…5 **not** consumed | the failure is structural and deterministic, proved rather than assumed — see **F-501**, *"why the retry budget was not consumed"* |
| `B-5′` | `isolate --repatriate <SESSION> --run-id run_ea96552d2a8a --attempt 1` | rc=0; report, attestation and subject tree repatriated, digest-verified |
| `B-6′` | `parse-report` then `score --workspace artifacts/runs/run_ea96552d2a8a/final_review_workspace`, as a separate step after the reviewer stopped | rc=0 both; metric block produced; outputs written **outside** the repository and not committed |
| `B-7` | `final-review-audit-export --run-id run_ea96552d2a8a` | rc=0; `FINAL_REVIEW_EVIDENCE_BUNDLE.json`, `schema_version 2.0`, `integrity.records_found=1 records_ok=1`, every other integrity list empty |
| teardown | `isolate --teardown <SESSION>` | rc=0; the session — which held a copy of the operator's **real** credential — is gone; `assert_no_stale_plants()` clean |

**What `B-1′` proves that nothing before it did.** The three iteration-1 findings are closed at the
production entry point, with the real agent rather than a stand-in:

* **F-401 closed.** `probes[NEG-5]` re-scanned all thirteen admitted roots and returned; the run
  terminated in 14 minutes instead of being SIGKILLed at 17m44s. `/dev` reports
  `content_scanned: 0` while **183,136** files were content-scanned across the other roots
  (`/System` 77,256, `/Library/Developer/CommandLineTools` 94,472, `/usr` 11,027, `/private/etc`
  214, `<SESSION>/home` 149, `<SESSION>/review_root` 16, `<USER_HOME>/bin` 2, the rest 0). The
  `S_ISREG` gate skipped every character and block device and still counted them.
* **F-402 closed.** The pre-flight's own processes wrote inside the session for real — the session
  `HOME` held **246** files totalling **32,377,555** bytes at attestation time, of which **245**
  were written after the admission scan. A profile whose write clause disagreed with its read
  clause could not have produced one of them.
* **F-403 closed, all three parts.** (1) `control/probes/preflight.log` has a **fifth** entry and it
  is the agent: `$ codex-sol exec 'reply with the single word ok'` → `rc=0`, `ok`, `OpenAI Codex
  v0.149.1`, `model: gpt-5.6-sol`, `directory: <SESSION>/review_root`. (2) `orca_check_probe()` has
  a caller and it **runs** — which is how F-501 was found. (3) The credential reached the session
  through the D-6.8/D-6.9 seed path and the attestation records both identities:

```json
"schema_version": "1.1",
"session_home": {
  "seeded": [{
    "dest": "home/.codex/auth.json",
    "source": "<REDACTED:foreign_absolute_path>",
    "seeded_bytes": 4048,  "seeded_sha256": "sha256:a51f66e9…276a",  "seeded_mode": "0600",
    "observed_bytes": 4048, "observed_sha256": "sha256:a51f66e9…276a", "observed_mode": "0600",
    "state": "unmodified"
  }],
  "inventory": { "files": 246, "bytes": 32377555, "seeded_unmodified": 1, "seeded_modified": 0,
                 "unseeded": 245, "truncated": false },
  "scanned_by": ["compute_readable_set:USR", "NEG-5"]
}
```

  `<SESSION>/home` appears in `readable_set[]` as a scanned Class USR root **and** carries its own
  NEG-5 record (`passes [A,B,C,D,S]`, `vocabulary key_leak`, `content_scanned 149`, `hits 0`), so
  the real credential was scanned by both gates with the full key vocabulary and produced no hit.
  `state: "unmodified"` is derived, not asserted: the agent read the credential and did not rewrite
  it.

**The subject is provably the same tree the three prior captures used.** The repatriated
workspace's `MANIFEST.json` carries
`fixture_digest = sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d`,
identical to `run_ff587481a820`, `run_92759e0e1034` and `run_5967188007ce`.

**Prompt discipline.** The dispatched prompt is the R2-corrected neutral prompt reused
byte-for-byte from the current baseline's retained `input.md`, with exactly three substitutions —
the policy path, the subject path and the report path — all of which had to change because the
review now happens inside the session. 4,085 bytes. It names no defect class, weights no search
axis, discloses no fixture or evaluation framing, and states no count. Verified mechanically
**before** dispatch: shipped `scan-leak --key … --target <prompt>` → `leak scan PASSED`;
`semantic_leak_scan --profile prompt` → `PASSED (1 files scanned, 0 hits)`. No detection or search
policy was changed for this capture.

## Execution

Command: `python3 scripts/final_review_eval.py isolate --run-id run_ea96552d2a8a --enforcement seatbelt … --agent-command "codex-sol exec 'reply with the single word ok'"`
Result: PASS — `rc=0`, `real 14m2.870s / user 11m55.022s / sys 1m21.445s`

Command: `orca terminal create --worktree current --title final-review-isolated-1 --command "<G.5 launch line>"` then `orca orchestration task-create` + `orca orchestration dispatch` + `orca terminal send` (8,728 bytes of preamble+TASK delivered, `accepted: true`)
Result: FAIL — the review completed and wrote a usable report; the **dispatch did not settle**. See **F-501**.

Command: `python3 scripts/final_review_eval.py isolate --repatriate <SESSION> --run-id run_ea96552d2a8a --attempt 1`
Result: PASS — `report_digest sha256:53c2481456cc09c042fbafa408b720dbe07671f0ad280d987142bd5dc266c271`

Command: `python3 scripts/final_review_eval.py parse-report …` then `score … --workspace artifacts/runs/run_ea96552d2a8a/final_review_workspace --run-verdict FAIL`
Result: PASS — both `rc=0`; re-scoring the same findings document produced a **byte-identical** metrics file (`cmp` clean), with no excepted field

Command: `python3 scripts/run_logging.py final-review-audit-export --run-id run_ea96552d2a8a`
Result: PASS — `schema_version 2.0`, `component_versions.export_schema 2.0`, `integrity.records_found 1`, `records_ok 1`, `digest_mismatches/unreadable/missing_artifacts/omitted_content/incomplete_publications` all empty

Command: `python3 scripts/final_review_eval.py scan-leak --key <key> --target artifacts/runs/run_ea96552d2a8a/final_review_audit/…/input.md`
Result: PASS — `leak scan PASSED` (this is `B4`'s criterion, and it passes)

Command: `grep -rF` for the local username, the `-Users-` shape, `/private/tmp/`, `frv_iso_` and `/var/folders/` over the retained family
Result: FAIL initially — **all hits in `FINAL_REVIEW_ISOLATION.json` and nowhere else** (see **F-502**); PASS with zero hits for every pattern once that one file was withheld from the commit (see `## Remaining Gaps`)

Command: `python3 scripts/validate_skills.py`
Result: PASS — `Skill validation PASSED (463 checks)`

Command: `python3 scripts/verify_package.py`
Result: PASS — `Package verification PASSED (109 source files)`

Command: `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py`
Result: PASS — byte-identical

Command: `git diff --check`
Result: PASS — `rc=0`, no output

Command: `python3 -m unittest discover -s scripts -p 'test_*.py'`
Result: FAIL (`Ran 1167 tests in 734.741s`, `FAILED (failures=2, skipped=6)`, `real 12m14.858s`) —
**the two expected pre-existing whitespace failures and nothing else.** Both are
`test_run_logging.RetainedReportWhitespaceExemptionTests` cases
(`test_the_gate_fails_again_once_the_exemption_is_removed`,
`test_the_whitespace_gate_passes_over_the_whole_os22_range`). One correction to the dispatch's own
description of them, stated because it would otherwise look like a regression: they now name **four**
files, not two — `run_4d1c47c838db/REVIEW_DESIGN_iteration1.md` and `…_iteration2.md` as expected,
plus `run_75c5c6046f35/REVIEW_DESIGN_iteration2.md` (committed in `0510cc2`) and
`run_75c5c6046f35/REVIEW_TEST_iteration1.md` (committed in `959a6b4`). All four are Reviewer
artifacts committed **at or before HEAD** by earlier dispatches, confirmed with `git log -1 HEAD --
<path>` for each; none is this dispatch's. The failure count is unchanged at 2 and the failing set
is entirely pre-existing. Everything else passed, including the whole isolation suite (T-8, T-9,
T-10) and the `sandbox-exec` denial battery — which is why F-501, F-502 and F-503 are gaps in what
the suite covers rather than failures it reports.

Command: `git diff --check --cached` over the staged capture, before and after the three withholdings
Result: FAIL then PASS — `rc=2` with ~30 lines in `FINAL_REVIEW.md` and ~40 in `final_review_workspace/DIFF.patch` (**F-503**); `rc=0`, no output, once those two paths are not staged

## Failures / Findings

Three findings. All are **production defects**, none is a property of this capture attempt, and
none is covered by F-401/F-402/F-403 — F-401 and F-402 are closed and reproduced above, and
F-403's part 2 was *"`orca_check_probe()` has no caller"*, which is also closed. F-501 is what that
now-wired probe **reports**, which is a different fact and a worse one. F-502 and F-503 are both
defects in *retaining* a capture, and neither could be reached before this dispatch because no
capture had ever produced artifacts to retain. None was fixed here.

---

### F-501 — the isolated agent cannot execute the `orca` CLI, so no isolated dispatch can ever settle; DESIGN's O-1 is false

Severity: MAJOR · Blocking: YES · Responsible Phase: design (with an IMPLEMENTATION follow-up)
Location: `artifacts/runs/run_75c5c6046f35/DESIGN.md:694-699` (G.5's O-1 paragraph) and its
`## Risks / Open Issues` **O-1**; `scripts/review_isolation.py:2855-2880` (`orca_check_probe()`);
`scripts/review_isolation.py:906-948` (`wrap_command()`'s `PATH`);
`scripts/review_isolation.py:857-903` (`render_seatbelt_profile()` clause 1, `(deny file-read*)`).

**Issue.** G.5 asserts, and O-1 records as an assumption to be probed rather than proven, that
*"`orca orchestration send/check/ask` must keep working from inside the sandbox: the `orca`
executable lives outside the repository, `(allow default)` leaves network and process rights
untouched…"*. The first clause is exactly backwards. Clause 1 of the generated profile is
`(deny file-read*)` — an **allowlist**, so *living outside the repository is not sufficient, it is
the problem*: a path that is not in the computed readable set cannot be read or executed. `orca` is
not in it, cannot be put in it, and the launch line's `PATH` cannot even name its directory.

**Reproduction 1 — the production probe, against the profile this capture actually generated.**
`orca_check_probe(session, "<coordinator handle>", agent_path=[…])`, the function `isolate()` now
calls when `--terminal` is supplied:

```json
{"rc": 71, "stderr": "sandbox-exec: execvp() of 'orca' failed: No such file or directory\n"}
```

Three spellings, same session, same profile, plus the unsandboxed control:

| invocation, through the real `wrap_command()` launch line | result |
|---|---|
| `orca orchestration check --terminal <handle>` | `rc=71` — `execvp() of 'orca' failed: No such file or directory` |
| `/usr/local/bin/orca orchestration check --terminal <handle>` | `rc=71` — `execvp() … failed: **Operation not permitted**` |
| `/Applications/Orca.app/Contents/Resources/bin/orca orchestration check …` | `rc=126` — `Operation not permitted` |
| `/bin/ls -l /Applications/Orca.app/Contents/MacOS/Orca` | `rc=1` — `Operation not permitted` |
| the same command, **unsandboxed** | `rc=0` |

The first row is a *second* effect worth separating from the first: with `--agent-path` supplied,
`wrap_command()` sets `PATH=<agent dirs>:/usr/bin:/bin:/usr/sbin:/sbin`, which by construction
**cannot contain `/usr/local/bin`**, because `assert_agent_path_admitted()` requires every
`--agent-path` entry to be an admitted readable-set root. So `orca` is unreachable by name *and*
denied by path.

**Reproduction 2 — the widening that would be needed is refused by the shipped scanner, correctly.**
`orca` is a `bash` script that `exec`s the Electron binary with the CLI entry point
`Contents/Resources/app.asar.unpacked/out/cli/index.js`, so admitting it means admitting the
application bundle. Measured with the shipped `scan_readable_set(key, root, passes=SCAN_PASSES_ALL,
vocabulary="key_leak")`, unmodified:

| candidate `--allow-read` root | files | content_scanned | hits |
|---|---|---|---|
| `/Applications/Orca.app` | 3,157 | 3,005 | **37 → exit 4** |
| `…/app.asar.unpacked/out/cli` (the entry point alone) | 113 | 113 | 0 |
| `…/app.asar.unpacked/out/shared` (which the entry point requires) | 879 | 879 | **6** |
| `…/app.asar.unpacked/out/main` | 56 | 56 | **3** |
| `<USER_HOME>/bin` (`codex-sol`) | 2 | 2 | 0 — admitted, and used |
| `/opt/homebrew/Caskroom/codex/<v>/bin` (the real `codex`) | 2 | 0 | 0 — admitted, and used |

All 37 hits are pass-B token matches on ordinary English in the application's own bundled
JavaScript — 22 on the generic token the vocabulary shares with D-6.9's schema field names, 14
expected-count heuristics, and 1 on a short key-derived identifier that happens to occur inside a
`package.json` (the identifier itself is not reproduced here).
They are false positives, and that does not help: the scanner is fail-closed **by design**, D-6.1
records that *"there is deliberately no `--ignore`"*, and the hits are in `out/shared` and
`out/main`, which the CLI cannot run without. There is no narrower admissible subtree.

**Reproduction 3 — the real dispatch, which is the one that matters.** The isolated Reviewer
received the 8,728-byte preamble+TASK, performed the review, wrote its Review Result, and then ran
the preamble's own reporting command. Terminal transcript, verbatim:

```text
• Ran orca orchestration send --from term_<isolated> --type worker_done --subject "Final
  │ adversarial review: FAIL" --body "I completed the independent adversarial review and wrote
  │ the required Review Result. …
  └ /bin/bash: orca: command not found

• Review completed with a FAIL verdict and <count elided under P-1> blocking findings.
  Report: artifacts/runs/run_ea96552d2a8a/FINAL_REVIEW.md

  The required worker_done notification could not be delivered because the orca CLI is
  unavailable (command not found).

─ Worked for 2m 05s ─
```

`orca orchestration dispatch-show --task <task>` immediately afterwards:
`{"status": "dispatched", "failure_count": 0, "last_failure": null, "completed_at": null,
"last_heartbeat_at": null, "termination_reason": null}`, and the coordinator inbox held
`No messages.` The Dispatch was subsequently fenced with `worker-abandon`
(`state: "abandoned"`, `alreadySettled: false`) and the Task set to `failed`, which is the honest
settlement and is **not** a satisfied `B1`.

**Consequence.** `B1` requires *"at least one dispatch that settled with a usable report"*. A
usable report exists; a settlement cannot. Every isolated §7 baseline is blocked on this, and it
blocks nothing else — `B2`, `B4`, `B5` and `B6` all pass on this very capture.

**Why the `B-4R` retry budget was not consumed past attempt 1.** `B-4R` retries a dispatch-layer
failure under a new Task/Dispatch identity **and a new isolation session**, up to
`DEFAULT_MAX_ITERATIONS = 5`. It exists for *transient* failures. This one is a deterministic
function of the host and the flags, and that determinism was **measured rather than assumed**: the
readable set is computed from `DEFAULT_IMM_CANDIDATES` plus the operator's `--allow-read` list, the
`PATH` from `--agent-path`, and both were evaluated directly (Reproduction 2) — a new session
computes the same set and generates a profile that denies `orca` identically. Attempts 2…5 would
have cost roughly 56 minutes of wall clock to reproduce one `command not found` four more times and
would have produced no new information. This is the same discipline iteration 1 applied and
`REVIEW_TEST_iteration1.md` endorsed, applied here to a failure that *is* a dispatch-layer failure
but is provably not a transient one.

**Required Action (for the phase that owns the fix, not for TEST).** This needs a DESIGN decision,
because every available shape changes something DESIGN fixed. Named without choosing between them:
(a) let the reporting channel out of the sandbox — e.g. the launch line becomes
`cd … && sandbox-exec … <agent>; <unsandboxed settlement>`, which keeps the *review* sandboxed but
gives up "`exec`, so the agent process is the only process"; (b) admit the CLI through a mechanism
that survives a fail-closed content scan of a third-party application bundle, which today does not
exist and which D-6.1 deliberately refuses to add; (c) give the isolated agent a session-local,
attested shim whose only capability is delivering `worker_done`, seeded the way the credential now
is; (d) redefine `B1`'s settlement for isolated captures and say so in `B-3`. Whichever is chosen,
`orca_check_probe()` must gain a **test** that runs it against a real generated profile and asserts
the expected outcome, so this is caught by the suite and not by a capture; and `isolate` should
run the O-1 probe **before** the ~13-minute negative battery rather than after the pre-flight only
when `--terminal` happens to be supplied.

---

### F-502 — the retained attestation leaks the local username and the isolation session path; `traversal_set[]` and the NEG-5 root records skip the P-PATH treatment every neighbouring field gets

Severity: MAJOR · Blocking: YES · Responsible Phase: implementation
Location: `scripts/review_isolation.py:2526` (`"traversal_set": sorted(traversal)`), against
`scripts/review_isolation.py:2489, 2520, 2521, 2527, 2528` (`readable_set[]`, `session_root`,
`review_root`, `writable_set[]`, `denied_roots[]`, every one of which is wrapped in `_path_field()`);
`scripts/review_isolation.py:2436-2445` (`_path_field()` → `run_logging.assert_retained_path_field()`);
the NEG-5 `roots[]` records assembled in `run_negative_probes()`.

**Issue.** `_path_field()`'s own docstring says *"Every path-bearing field goes through the existing
P-PATH treatment"*, and T-8.7 asserts *"every path field is P1/P2/P3/P4 under
`assert_retained_path_field`"*. Two path-bearing structures in the same document skip it:
`traversal_set[]`, and each NEG-5 per-root record's `path`. `B3` — as amended by this Run —
requires the P-PATH grep over the retained family to return zero hits *"for the isolation session
path spelling"* as well as for the local username and the `-Users-` shape. The retained attestation
returns hits for both.

**Reproduction — `grep -rF` over `artifacts/runs/run_ea96552d2a8a/` immediately after `B-5′`.**

| pattern | hits | where |
|---|---|---|
| the local username | 2 | `FINAL_REVIEW_ISOLATION.json` only |
| `frv_iso_` | 4 | `FINAL_REVIEW_ISOLATION.json` only |
| `/var/folders/` | 7 | `FINAL_REVIEW_ISOLATION.json` only |
| `/private/var/folders` | 8 | `FINAL_REVIEW_ISOLATION.json` only |
| `-Users-`, `/private/tmp/` | 0 | — |

The values themselves, abbreviated:

```json
"traversal_set": [ …, "/Users", "/Users/<USER>", …,
                   "/private/var/folders/<hash>/T/frv_iso_<id>", … ],
"probes": [{ "id": "NEG-5", "roots": [ …,
    {"path": "/private/var/folders/<hash>/T/frv_iso_<id>/home",  "class": "USR", …},
    {"path": "/Users/<USER>/bin",                                "class": "USR", …} ]}]
"readable_set": [ …, {"path": "<REDACTED:foreign_absolute_path>", "class": "USR", …} ]
```

The last line is the point: **the same root is redacted in `readable_set[]` and raw two keys later.**

**The values are ones the project's own gate rejects**, checked directly against the shipped
predicate rather than argued:

```
assert_retained_path_field("/Users/<USER>")                       -> P-PATH violation
assert_retained_path_field("/Users/<USER>/bin")                   -> P-PATH violation
assert_retained_path_field("/private/var/folders/<hash>/T/frv_iso_<id>")       -> P-PATH violation
assert_retained_path_field("/private/var/folders/<hash>/T/frv_iso_<id>/home")  -> P-PATH violation
```

**It is not an artefact of this capture's `--allow-read` widenings.** The username hits are, but the
session-path hits are unconditional. Independently reproduced against the two attestations
**IMPLEMENTATION** built for its own end-to-end proof, neither of which passed any `--allow-read`:
both carry the raw session path in `traversal_set[]` **and** in all three NEG-5 `USR` root records,
while their `readable_set[]` entries are correctly redacted. So every attestation the shipped code
has ever produced carries this, and it would have been committed by any capture that reached `B-5′`.

**Why this is the same defect class as R6.** `R6` superseded the `run_92759e0e1034` capture because
its retained family carried *"a raw non-home absolute scratch path containing the local username,
the `-Users-`-encoded workspace shape and a session UUID"*, and `redactions` truthfully reported
`[]` because `redaction/1.0` had no category owning that shape. Here the policy **does** own the
shape — `_path_field()` renders exactly these values as `<REDACTED:foreign_absolute_path>` when it
is called — and two fields simply do not call it. `redaction/1.1` is not at fault; the attestation
writer is.

**Consequence.** `B3` fails. Because an immutable attestation cannot be hand-edited into compliance
without ceasing to be evidence of what the pipeline produces, the file was **withheld from the
commit** rather than edited (see `## Remaining Gaps`), which is why the committed family now greps
clean. Note that `B6` itself is *satisfiable* — the attestation's content is correct and complete;
it is the spelling of two of its fields that is not retainable.

**Required Action.** Wrap `traversal_set[]` and each NEG-5 `roots[].path` in `_path_field()`, the
way their neighbours already are. Then replace T-8.7's per-field assertion with a **whole-document**
one: walk the serialized attestation and require every string that parses as an absolute path to
satisfy `assert_retained_path_field`, so a future field cannot be added without the treatment. That
test fails today on `traversal_set[]` and is the regression guard the fix needs.

---

### F-503 — `B-5′` writes two path classes into the run artifact root that A.6's `.gitattributes` exemption does not cover, so committing a completed capture fails the repository's own whitespace gate

Severity: MAJOR · Blocking: YES · Responsible Phase: design (A.6 scope) with the IMPLEMENTATION edit
Location: `.gitattributes` (the single scoped rule `artifacts/runs/*/final_review_audit/**/report.md -whitespace`);
`scripts/test_run_logging.py:3946` (`GITATTRIBUTES_RULE`) and
`test_the_gitattributes_rule_is_exactly_the_one_designed`, which asserts there is **exactly one**
rule; `scripts/review_isolation.py:2621` and `:2642` (`repatriate()`'s two destinations).

**Issue.** A.6 exempted the retained Final Review snapshot from git's whitespace rules, because it
is Reviewer-authored Markdown whose hard breaks are two trailing spaces and which is digest-bound
and immutable. The rule names exactly one path shape: `final_review_audit/**/report.md`. `B-5′` did
not exist when that rule was written, and it writes **two more** things into the run artifact root
that carry the same bytes or the same problem:

* `artifacts/runs/<run>/FINAL_REVIEW.md` — the repatriated report. **Byte-identical** to the
  exempted `report.md` (both `sha256:53c2481456cc…c271`, verified with `shasum`), at a path the
  rule does not match.
* `artifacts/runs/<run>/final_review_workspace/DIFF.patch` — part of the repatriated subject tree.
  A unified diff's context lines are a single space, which `git diff --check` reports as trailing
  whitespace on every one of them.

**Reproduction — staging the completed capture.** With every `B-5′` output staged,
`git diff --check --cached` exits `2` and reports, in order, ~30 lines in `FINAL_REVIEW.md`
(`+ID: F-001  `, `+Severity: MAJOR  `, …) and ~40 lines in `final_review_workspace/DIFF.patch`
(`+ `). Unstaging those two paths and leaving everything else — `EXPLORATORY_RESULT.md`,
`ORCHESTRATOR_LOG.md`, `FINAL_REVIEW_EVIDENCE_BUNDLE.json` and the whole
`final_review_audit/…/{input.md,record.json,report.md}` record — makes it exit `0` with no output.
`report.md` is silent throughout, which is the exemption working exactly as designed on the one
path it names.

Neither file can be trimmed: `FINAL_REVIEW.md` is digest-bound by `record.json` and is what
`B-5′` verifies byte-for-byte in transit, and `DIFF.patch` is part of a tree whose
`fixture_digest` must stay `sha256:b63f5a9f…70f1d`. Trimming either is the "hand-edited artifact is
no longer evidence" failure the `R6` write-up already names.

**Consequence.** Any capture that reaches `B-5′` and commits its result fails
`test_run_logging.RetainedReportWhitespaceExemptionTests.test_the_whitespace_gate_passes_over_the_whole_os22_range`
from that commit onward. This dispatch avoided introducing that regression by not committing the
two paths (see `## Remaining Gaps`); nothing is lost, because `report.md` is the same bytes and
**is** committed, and the workspace is reproducible from the committed fixture by `materialize`
with the same `fixture_digest`.

**Required Action.** Extend A.6's exemption to the paths `B-5′` creates —
`artifacts/runs/*/FINAL_REVIEW*.md` and `artifacts/runs/*/final_review_workspace*/**` are the two
shapes — and update `GITATTRIBUTES_RULE` and
`test_the_gitattributes_rule_is_exactly_the_one_designed` in the same commit, since that test
asserts a single rule and is the mechanism that stops the exemption being quietly broadened. It is a
DESIGN change because A.6 chose the narrow scope deliberately; the alternative — deciding that
`B-5′`'s repatriated copies are working files rather than retained evidence and are not committed at
all — is also a DESIGN choice and is what this dispatch did provisionally.

---

### N-501 — non-blocking: schema 1.1's own field names make `scan-leak` fail over a seeded run directory

Severity: MINOR · Blocking: NO · Responsible Phase: implementation

`scan-leak --key <key> --target artifacts/runs/run_ea96552d2a8a` returns `rc=4` with exactly one
hit: `FINAL_REVIEW_ISOLATION.json`, token `seeded`. `seeded` is a member of `key_leak_tokens(key)`
(and deliberately not of `key_material_tokens(key)`), and D-6.9's record schema names its fields
`seeded`, `seeded_bytes`, `seeded_sha256`, `seeded_mode`, `seeded_unmodified`, `seeded_modified`.
So a retained attestation from a **seeded** capture always trips the shipped literal scan on its own
schema. The same command over the current baseline's directory `run_5967188007ce` returns
`leak scan PASSED`, so this is new with schema 1.1.

**This document trips the same collision.** `scan-leak --target artifacts/runs/run_75c5c6046f35/TEST.md`
returns `rc=4` on the token `seeded`, because N-501 cannot be described without naming the schema
fields it is about. That is the collision, not a disclosure; for scale, the same command over this
Run's `IMPLEMENTATION.md` — already committed — returns thirteen hits including every seeded-defect
identifier. No answer-key identifier is reproduced in this document.

It is non-blocking because `B4`'s criterion is the scan of the **retained reviewer input**, which
passes with zero hits, and because it is a naming collision rather than a disclosure. It matters
anyway: an operator who runs the obvious whole-directory scan gets a fail that means nothing, which
is how real leak scans stop being read. Worth either renaming the schema fields (a schema break) or
giving `scan_leak()` a documented, tested exemption for the attestation's own key names — a DESIGN
question, not one to improvise.

### N-502 — non-blocking, recorded so it is not rediscovered: the semantic evidence profile fails on the fixture itself

`semantic_leak_scan.py --profile evidence` over `artifacts/runs/run_ea96552d2a8a/` reports 5
`archetype_vocabulary` hits. Controls run in the same session: the **current, accepted** baseline
`artifacts/runs/run_5967188007ce/` reports 2 hits of the same class, and the committed fixture
source `scripts/fixtures/final_review_eval/subject/` reports 2. The hits land on the reviewer's own
description of a defect it found and on the fixture's `CONTRACT.md`/`DIFF.patch`, which is inherent:
a Review Result necessarily quotes what it found. This is pre-existing and tolerated, it is not a
`B3` or `B4` criterion, and no conclusion is drawn from it. It is recorded only because `B-5′` newly
retains a copy of the subject tree in the run directory, which raises the hit count without changing
what is disclosed.

## Remaining Gaps

* **The §7 baseline remains uncaptured, and `R1` remains open.**
  `artifacts/runs/run_644c005bc9db/BASELINE_RESULT.md` is still the designated current §7 baseline
  and still evaluates only `B1-B5`. Nothing in this dispatch changed that, and nothing should have.
  `artifacts/runs/run_ea96552d2a8a/` is labelled an exploratory run in its own
  `EXPLORATORY_RESULT.md`; there is exactly one document in the repository claiming to be the
  current baseline.
* **`B1 … B6`, independently verified, one line each.**

  | criterion | verdict | basis |
  |---|---|---|
  | **B1** procedure ran | **FAIL** | every step executed as documented, but **no dispatch settled** (F-501) |
  | **B2** scoring worked | PASS | `parse-report` and `score` both `rc=0` as a separate post-settlement step; metric block produced; `precision`/`false_positive_rate` correctly `REFUSED` with `adjudication_incomplete`; `verdict_reproducibility` correctly `SINGLE_RUN_NOT_ASSERTED` |
  | **B3** artifacts produced | **FAIL** | the exported bundle greps clean as a whole, and every retained file greps clean **except** `FINAL_REVIEW_ISOLATION.json` (F-502) |
  | **B4** no answer-key leak | PASS | shipped `scan-leak` over the retained reviewer input: `leak scan PASSED`, zero hits (see also N-501) |
  | **B5** reproducible | PASS | re-scoring produced a **byte-identical** metrics file, `cmp` clean, no excepted field; `--workspace` points at the repatriated live tree rather than a deleted session |
  | **B6** scope enforced | PASS | `scope_enforcement == "seatbelt"`; `properties.S1/S2/S3` all `PASS`; `NEG-0 … NEG-8` **all nine** `PASS`; `profile_digest` = `sha256:acce3e5f…ba321` and `shasum -a 256 <SESSION>/control/scope.sb` recomputes the same value; `schema_version "1.1"` with the seed record's `seeded_*`/`observed_*` groups both populated and `state` derived from them; `no_unscanned_descendant: PASS`; `assert_no_clock_value()` passed; two `limitations[]` entries, the second present because and only because something was seeded |

  `B6` passes **as a property of the session**, not of an accepted dispatch: `B6`'s text conditions
  on "the accepted dispatch", and there is none. It is recorded as PASS because every substantive
  clause it names is satisfied and verified, and stating otherwise would hide that the isolation
  mechanism itself now works.
* **Three capture outputs are deliberately NOT committed, and each is reversible in one command.**
  All three are retained **verbatim and unedited** outside the repository, under
  `<SCRATCHPAD>/withheld/`, and every fact this document states was read out of them. **These are
  Worker judgment calls and the coordinator can reverse any of them** — the files are intact and one
  `cp` away.

  | withheld | digest | why |
  |---|---|---|
  | `FINAL_REVIEW_ISOLATION.json` | `sha256:ccd60ccc4226ca253c5ba1086c639f37ebe7f62a4d7d636dbfaed4c8bdaa5a58` | **F-502.** Committing it puts the `R6` disclosure — the local username and the session path spelling — into git history, where it cannot be withdrawn; hand-editing it would destroy its value as evidence of what the pipeline produces |
  | `FINAL_REVIEW.md` (the repatriated report) | `sha256:53c2481456cc09c042fbafa408b720dbe07671f0ad280d987142bd5dc266c271` | **F-503.** Committing it fails the repository's whitespace gate, and the identical bytes are committed at the exempted `final_review_audit/…/report.md` — verified with `shasum`, the two digests are the same string, so nothing is lost |
  | `final_review_workspace/` (the repatriated subject) | `MANIFEST.json` `fixture_digest sha256:b63f5a9f…70f1d` | **F-503.** Same gate, via `DIFF.patch`'s context lines; the tree is reproducible from the committed fixture by `materialize` with the same `fixture_digest` |

  Also kept beside them, purely so the `B6` verification is re-checkable: the session's `scope.sb`
  (`sha256:acce3e5f504f8375d590688db18276cd17b26a3d32fb84589f61d0af274ba321` — which **is** the
  recorded `profile_digest`) and `control/probes/preflight.log`
  (`sha256:84fd86664b61f964a29bcaff5efdfe6adb9c4e4e0e536f602ede59a5032e1c0b`).

  After the three withholdings the committed family was re-verified end to end: `scan-leak` →
  `leak scan PASSED`; the P-PATH grep → **zero** hits for all five patterns; `git diff --check` and
  `git diff --check --cached` → `rc=0`, no output; and `final-review-audit-export` re-run over the
  reduced directory still reports `records_found 1`, `records_ok 1` with every integrity list
  empty, so the withholdings did not break the bundle.
* **The isolation session was torn down**, with `isolate --teardown`, `rc=0`. It held a copy of the
  operator's **real** credential, so leaving it on disk was not acceptable. `assert_no_stale_plants()`
  is clean and the two `frv_iso_` sessions still present are IMPLEMENTATION's own synthetic-seed
  proofs, untouched.
* **`run_a45e71e518d4` is abandoned**, explicitly, and no directory was ever created for it.
* **Not verified, because it was out of scope or unreachable:** O-1 remains open and is now answered
  in the negative rather than discharged; `--terminal` was deliberately **not** passed to the
  capture invocation, because `isolate()` raises and **deletes the session** when the O-1 probe
  fails, which would have destroyed the 14-minute attestation the rest of this document rests on —
  the same probe function was therefore run against the finished session instead, which is the
  identical code path; the `B-4R` retry path is not demonstrated end to end for the reason argued in
  F-501; no adjudication was performed, so precision is refused rather than computed.
* **No metric magnitude is published in this document.** Under P-1, and because this is an
  exploratory run rather than a baseline, **nothing** from
  `{key population total, detected/matched count, missed count, unmatched-finding count, reviewer
  finding count, recall}` is published here — not even the single coarse bucket P-1 would permit —
  and the one exact count that appeared in the isolated agent's own terminal transcript is elided
  above. Scorer outputs were written outside the repository and are not committed. One inherited
  exposure is stated rather than hidden: the shipped `final-review-audit-write` records the parsed
  `blocking_finding_ids` in `record.json`, from which the reviewer finding count is readable. That
  is a property of the tool and is identical in the **current, accepted** baseline's own
  `run_5967188007ce/.../record.json`, so it is pre-existing rather than introduced here; it is
  named so a reader does not take "nothing is published" more broadly than it is meant. The Reviewer's verdict is an
  observation and not a criterion, and no detection-quality conclusion and no `H-1`/`H-2`/`H-4`/`H-5`
  comparison appears here or in any artifact this dispatch wrote.
* **Unchanged by design:** `VERSION`, `LICENSE-DECISION.md`, detection/search policy, prompts,
  reviewer instructions, the fixture, the adjudication key, the scorer, the `H-1`/`H-2`/`H-4`/`H-5`
  conclusions, and every retained record in `run_644c005bc9db` and `run_5967188007ce`. No branch and
  no PR was created. No production code was written, modified or patched.
