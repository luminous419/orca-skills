# IMPLEMENTATION — run_a29ac78075a9

Branch `agent/final-review-observability-evaluation` (Draft PR #20). Risk **high**.
Phase IMPLEMENTATION, iteration 1.

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS

---

## Summary

`_probe_python()` handed `/usr/bin/python3` to `_run_probe()` and to `preflight_probe()`.
On darwin that path is not an interpreter — it is Apple's xcode-select **tool shim**, and
the shim's only entry into `libxcselect` is `xcselect_invoke_xcrun`, which is the code path
that asks macOS to present the Command Line Tools installer. The isolation launch path
therefore exec'd an installer-triggering binary on every dispatch. That is now resolved
away: `resolve_probe_interpreter()` detects the shim from its own embedded identifier and
returns the real interpreter behind it inside the already-proven, already-admitted
`/Library/Developer/CommandLineTools` root, or **fails closed** if it cannot.

I could **not** reproduce the reported dialog, and I did not manufacture a diagnosis to
cover that. Section *Analysis* separates what I proved from what remains inference, and it
records that the Coordinator's hypothesis **E4 is falsified by measurement**.

Nothing is admitted that was not admitted before. No profile clause changed. No sandbox
policy changed. 17 new tests, 16 of them portable, all executed on macOS and on Linux
3.11/3.12/3.13.

---

## Analysis

### What I PROVED (commands run, output behind every claim)

**P1 — `/usr/bin/python3` and `/usr/bin/git` are literally the same file.**

```
$ stat -f "%i %N" /usr/bin/python3 /usr/bin/git /usr/bin/xcrun
1152921500312571585 /usr/bin/python3
1152921500312571585 /usr/bin/git          <- same inode
1152921500312573134 /usr/bin/xcrun
$ ls -la /usr/bin/python3 /usr/bin/git
-rwxr-xr-x  78 root  wheel  118928  6월 25 11:29 /usr/bin/git
-rwxr-xr-x  78 root  wheel  118928  6월 25 11:29 /usr/bin/python3
```

One 118,928-byte Mach-O with 78 hard links. Both contain
`com.apple.dt.xcode_select.tool-shim-public`; `/usr/bin/xcode-select`, `/usr/bin/xcrun` and
the real interpreter do not:

```
$ python3 -c "..."   # substring test over the raw bytes
shim marker in /usr/bin/python3: True
shim marker in /usr/bin/xcode-select: False
shim marker in real interp: False
```

This confirms **E1** and is the discriminator the fix is built on.

**P2 — the shim's ONLY way to raise the installer prompt is `xcselect_invoke_xcrun`.**

```
$ dyld_info -imports /usr/bin/python3 | grep -i xcselect
      0x0000  _xcselect_invoke_xcrun  (from libxcselect)
$ dyld_info -imports /usr/bin/xcrun | grep -i xcselect
      0x0000  _xcselect_invoke_xcrun  (from libxcselect)
$ dyld_info -imports /usr/bin/xcode-select | grep -i xcselect
      ... _xcselect_get_developer_dir_path ...
      0x0008  _xcselect_trigger_install_request  (from libxcselect)
```

The shim imports exactly one libxcselect symbol. `xcode-select` is the binary that imports
`_xcselect_trigger_install_request` directly (that is its `--install` subcommand). So:
a process that never execs a shim and never execs `xcrun`/`xcode-select` has **no reachable
call site** for the prompt. That is what makes the chosen fix structural rather than
situational, and it is why `developer_dir_candidates()` deliberately does not shell out to
`xcode-select` to ask where the developer directory is.

**P3 — E4 is FALSIFIED. Developer-directory resolution demonstrably WORKS inside the real
generated profile.**

I built a real session and its real `scope.sb`, then ran, through the real
`wrap_command()` launch line:

```
### /usr/bin/xcode-select -p
/Library/Developer/CommandLineTools
rc=0

### /usr/bin/git --version                       (the SAME shim binary as python3)
git: error: couldn't create cache file '/var/folders/.../xcrun_db-CP7uu77p' (errno=Operation not permitted)
git version 2.50.1 (Apple Git-155)
rc=0

### /usr/bin/xcrun --find python3
/Library/Developer/CommandLineTools/usr/bin/python3
rc=0

### /usr/bin/python3 -c 'print(1)'
rc=0  stdout='1\n'
```

`/private/var` and `/Applications` are still never-admitted in that profile. The shim still
resolves. E4's specific claim — that the denial of `/private/var`/`/Applications` is what
makes the shim conclude the tools are missing — is **contradicted by this measurement**, and
I did not act on it.

**P4 — the real interpreter is inside the already-admitted CLT root and needs nothing new.**

```
$ /usr/bin/xcrun --find python3
/Library/Developer/CommandLineTools/usr/bin/python3
-> realpath: /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9
### under the real profile: rc=0  stdout='1\n'  stderr=''   (no shim noise at all)
```

This confirms **E5**, including the incidental benefit that the resolved interpreter emits
none of the `couldn't create cache file` stderr the shim emits.

**P5 — the host's Command Line Tools were REINSTALLED during the OS-22 work window.**

```
$ grep -a "Command Line Tools" /var/log/install.log
2026-08-27 21:08:04+09 ... Installed "Command Line Tools for Xcode 26.5" (26.5)
2026-08-27 21:08:04+09 ... Installed "Command Line Tools for Xcode 26.6" (26.6)
$ ls -la /Library/Developer/CommandLineTools/usr/bin/python3
lrwxr-xr-x 1 root wheel 67  8월 27 21:03 ... -> ../../Library/Frameworks/Python3.framework/Versions/3.9/bin/python3
```

The extraction/receipt window is 2026-08-27 21:02:41 → 21:08:04. The `python3` symlink and
the whole `Python3.framework` under the developer directory carry timestamps inside that
window. The host state that produced the dialog **no longer exists**, which is why the
symptom does not reproduce today.

For completeness I checked the obvious confounder and it did **not** happen: the OS was not
updated. `sw_vers` reports 26.5.2 / 25F84 and the 26.6.2 update is still only queued
(`SUOSUInstallTonightManager: Queued ...`), and the machine has been up since Aug 26 19:39.

### What I did NOT prove

**I cannot confirm the mechanism by reproduction.** One deliberate reproduction attempt was
made — `/usr/bin/python3 -c 'print(1)'` under the real profile — and it exited 0 and printed
`1`. No installer dialog appeared, then or at any point in this run. The operator was not
interrupted once. Every other observation above was chosen because it cannot raise the
prompt (P2 explains why `xcode-select -p` was safe to use as an observation and why I still
refused to build the FIX on it).

So the following is **inference, clearly labelled as such**: the most economical explanation
consistent with P1–P5 is that before 21:02 on 2026-08-27 the active developer directory did
not provide a usable `python3` (or `xcselect` judged it unusable), `xcselect_invoke_xcrun`
failed, and the shim asked for the installer — and the reason the operator saw it *over and
over* is that `_probe_python()` put that shim on the launch line of every probe and every
pre-flight. I did not verify the pre-21:02 contents of the developer directory; that state
is gone. I also did not establish whether the same command would have failed *outside* the
sandbox at that time.

**This matters for how the fix is justified, and it is justified either way.** Whether the
shim failed for a sandbox reason or a host reason, P2 says the fix removes the only call
site through which the isolation path could raise that prompt at all. It is not a bet on
which of the two it was.

### Why this layer, and what was rejected

* **Rejected: reinstalling / requiring reinstall of the CLT.** Explicitly out of bounds, and
  it fixes one host rather than the code.
* **Rejected: admitting `/private/var`, `/Applications` or `/Library`.** P3 shows the denial
  is not the cause, so this would have been a security regression bought for nothing — it is
  exactly the F-001 reintroduction the task forbids.
* **Rejected: shelling out to `/usr/bin/xcode-select --print-path` to find the developer
  directory.** It is the obvious way to ask, but P2 shows it is the binary that imports
  `_xcselect_trigger_install_request`, so using it is not *provably* free of the prompt this
  resolution exists to avoid. `developer_dir_candidates()` reads the recorded state instead
  and executes nothing; a test asserts that.
* **Rejected: also substituting the pre-flight's `git --version`.** `git` is a shim too, so
  this was a real decision, not an oversight. That check exists to prove *the agent's own
  `git`* works inside the sandbox, and the agent will invoke `git`; substituting the
  resolved binary would make the check prove something else. If git's shim cannot resolve,
  the agent's git is genuinely broken and a loud pre-flight failure is the correct outcome.
  **Named risk:** the reported dialog could in principle recur via `git`. I judged a
  pre-flight that lies to be worse. The reasoning is recorded in the `TOOL_SHIM_MARKER`
  comment so a later reader sees a decision, not a gap.

### Security invariants — unchanged, by construction

The change touches exactly one thing: *which executable path the probes exec*. It does not
touch `render_seatbelt_profile()`, `compute_readable_set()`, `prove_immutable_narrowing()`,
`NEVER_ADMITTED`, `DEFAULT_IMM_CANDIDATES`, `MANDATORY_CARVE_OUTS`, `wrap_command()` or any
probe oracle.

* `/Library/Developer/CommandLineTools` is admitted **exactly as before** — as a
  `DEFAULT_IMM_CANDIDATES` entry that must still survive `enumerate_boundaries()` and
  `prove_immutable_narrowing()`. Resolving an interpreter inside it admits nothing; the
  proof admits it, and it is not special-cased past the proof.
* `/var/db/xcode_select_link` is read in the **host process at session-build time**, never
  from inside the sandbox. `/private/var` stays never-admitted.
* Fail-closed: unresolvable ⇒ `IsolationError`; unreadable candidate ⇒ `IsolationError`.
  Never a silent fallback to the shim, never a widened profile. Raised from `_probe_python()`
  it propagates through `isolate()`, whose `except BaseException` removes the session.
* Verified live on a real session below: recursive immutability proof, NEG-0…NEG-8 including
  the mandatory NEG-5 content scan, and S1/S2/S3 all still PASS.

---

## Changes

### `scripts/review_isolation.py`

| What | Detail |
| --- | --- |
| `TOOL_SHIM_MARKER` | New constant, `b"com.apple.dt.xcode_select.tool-shim-public"`, with the P1/P2 measurement and the `git --version` scope decision recorded as comment. |
| `XCODE_SELECT_LINK`, `DEFAULT_DEVELOPER_DIR` | New constants for developer-directory resolution. |
| `is_tool_shim()` | New. Chunked scan of the file's bytes for the marker — decides from the file, not from the path or the platform. An unreadable candidate raises `IsolationError` rather than returning `False`. |
| `developer_dir_candidates()` | New. `DEVELOPER_DIR` → `/var/db/xcode_select_link` → the documented default, deduplicated, order-preserving. Executes nothing. |
| `resolve_probe_interpreter()` | New. Non-existent system python → `sys.executable` (unchanged). Real interpreter → returned unchanged (every Linux). Shim → `<developer dir>/usr/bin/<name>`, realpathed, re-checked for the marker. Otherwise raises. |
| `_probe_python()` | Now `return resolve_probe_interpreter()`. This is the single line that takes the shim off the launch line of `_run_probe()` and `preflight_probe()`. |
| `SYSTEM_PYTHON` comment | Amended: it names the system python, it is not what gets exec'd on darwin. |
| `typing` import | `Mapping` added. |

`preflight_probe()`'s existing docstring about the `xcrun` shim's `couldn't create cache
file` stderr being BENIGN is untouched and still correct — `git --version` still produces it
(see the live pre-flight log below), and it must still not be "fixed" by granting write
access to the host per-user temp directory.

### `scripts/test_review_isolation.py`

New `ProbeInterpreterShimTests`, 17 tests. See *Unit Tests* below.

---

## Modified Files / Artifacts

```
scripts/review_isolation.py        (+151 / -4)
scripts/test_review_isolation.py   (+178)
artifacts/runs/run_a29ac78075a9/IMPLEMENTATION.md   (this file)
```

No prior run's artifacts were touched. No VERSION, LICENSE, skill, policy, profile or
lifecycle change. No new PR, no merge, no push, no force-push.

---

## Validation

All commands were run on this macOS host from the repository root unless stated.

### 1. Full suite, macOS

```
$ python3 -m unittest discover -s scripts -p 'test_*.py'
Ran 1220 tests in 309.041s
FAILED (failures=2, skipped=6)

FAIL: test_the_gate_fails_again_once_the_exemption_is_removed
      (test_run_logging.RetainedReportWhitespaceExemptionTests)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range
      (test_run_logging.RetainedReportWhitespaceExemptionTests)
```

1220 = the documented 1203 baseline + the 17 new tests. The two failures are **exactly** the
pre-existing, expected `RetainedReportWhitespaceExemptionTests` pair (they skip on CI because
`actions/checkout@v4` fetches `--depth=1`). Every offending file belongs to another run and
is digest-bound; not touched.

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

Also the CI release step, since CI runs it:

```
$ python3 scripts/build_release.py
Built reproducible release archive: .../dist/orca-skills-0.9.0.tar.gz
$ python3 scripts/verify_package.py --archive "dist/orca-skills-0.9.0.tar.gz"
Package verification PASSED (109 source files)
Verified archive: dist/orca-skills-0.9.0.tar.gz
```

### 4. `git diff --check`

```
$ git diff --check ; echo rc=$?
rc=0
```

### 5. Linux 3.11 / 3.12 / 3.13 via Docker — real output

Run as a non-root user (`--user $(id -u):$(id -g)`), which is the runner model GitHub
Actions uses:

```
=== python:3.11 (non-root) ===
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1220 tests in 193.583s
FAILED (failures=2, skipped=26)

=== python:3.12 (non-root) ===
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1220 tests in 208.732s
FAILED (failures=2, skipped=26)

=== python:3.13 (non-root) ===
FAIL: test_the_gate_fails_again_once_the_exemption_is_removed (test_run_logging.RetainedReportWhitespaceExemptionTests...)
FAIL: test_the_whitespace_gate_passes_over_the_whole_os22_range (test_run_logging.RetainedReportWhitespaceExemptionTests...)
Ran 1220 tests in 213.614s
FAILED (failures=2, skipped=26)
```

Same two known failures, nothing else. Those two are the `--depth=1` pair that CI skips, so
**Linux CI stays green on 3.11/3.12/3.13**.

Two honest notes about this step:

* Running the container **as root** (the docker default) produces 7 extra
  `ImmutabilityProofTests` failures, because UID 0 can write into read-only trees and the
  proof's negative assertions stop holding. I verified these are **pre-existing and unrelated**
  by stashing my two files and re-running the identical command: baseline `Ran 1203 tests
  ... FAILED (failures=8, errors=1, skipped=25)` with byte-identical failure names to
  `Ran 1220 tests ... FAILED (failures=8, errors=1, skipped=26)` with the fix. Zero new
  failures either way; the non-root numbers above are the CI-representative ones.
* One 3.12 pass showed a transient
  `ERROR: test_t83_a_symlink_in_the_policy_copy_list_is_refused` while three containers and
  a release build shared the same bind mount. It did **not** reproduce when 3.12 was re-run
  alone (output above).

### 6. A real macOS seatbelt `isolate()` session — STEP 4

```
$ isolate("run_a29ac78075a9_step4", fixture=scripts/fixtures/final_review_eval,
          enforcement="seatbelt")

scope_enforcement: seatbelt
PROPERTIES: {"S1": "PASS", "S2": "PASS", "S3": "PASS"}
probes: NEG-0 PASS, NEG-1 PASS, NEG-2 PASS, NEG-3 PASS, NEG-4 PASS,
        NEG-5 PASS, NEG-6 PASS, NEG-7 PASS, NEG-8 PASS
```

The session built, the immutability proof passed, the profile rendered, the pre-flight ran
the real launch line, all nine negative probes passed and the attestation was written. Its
`control/probes/preflight.log`:

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
git: error: couldn't create cache file '/var/folders/nz/.../T/xcrun_db-4NI6K8pQ' (errno=Operation not permitted)
git: error: couldn't create cache file '/var/folders/nz/.../T/xcrun_db-e8ymLu31' (errno=Operation not permitted)

$ /bin/ls .
rc=0
artifacts
policy
subject
```

The first line is the deliverable: the pre-flight of a real session now execs the resolved
interpreter, not `/usr/bin/python3`. `git` still emits the BENIGN `couldn't create cache
file` stderr, as designed and as `preflight_probe()` documents.

---

## Unit Tests / Testing Strategy

`UNIT_TEST_STATUS: PASS` — 17 new tests, `ProbeInterpreterShimTests`, all passing on macOS
and on Linux 3.11/3.12/3.13.

```
$ python3 -m unittest discover -s scripts -p 'test_review_isolation.py' -k ProbeInterpreterShimTests
Ran 17 tests in 0.032s
OK
```

**Portability.** 16 of the 17 synthesise the shim, the developer directory and the real
interpreter inside a temporary directory, so **Linux CI runs the whole mechanism rather than
skipping it** — no coverage is lost off darwin. Only
`test_on_darwin_the_shimmed_system_python_is_actually_resolved_away` is `@DARWIN_ONLY`,
because it asserts the property against the real `/usr/bin/python3`; it is not a smoke test
— it asserts the resolved path is not the shim, is not itself a shim, and actually executes
and prints `1`.

What they cover: the marker discriminator (including a marker deliberately straddling the
1 MiB read boundary, the case a naive chunked scan misses); unreadable candidate ⇒ hard
failure, not `False`; non-shim system python returned unchanged; missing system python ⇒
`sys.executable`; shim ⇒ resolved to the real interpreter; **fail-closed with no fallback to
the shim**; a developer directory offering another shim refused; first usable candidate
wins; `DEVELOPER_DIR` precedence, blank rejected, documented default last, no duplicates;
no xcselect-linked binary executed during resolution; and an AST assertion that
`_probe_python()`'s body is exactly `return resolve_probe_interpreter()` — a body-level
check, so the gate cannot be deleted from this function and matched in a sibling.

### How I convinced myself they are NOT vacuous

By reverting the fix locally, in two different shapes, and watching them fail — then
restoring.

**Revert A — put the shim back on the launch line** (`_probe_python()` restored to
`return SYSTEM_PYTHON if Path(SYSTEM_PYTHON).exists() else sys.executable`):

```
Ran 17 tests ... FAILED (failures=3)

FAIL: test_the_probe_interpreter_goes_through_the_resolver
- ['return SYSTEM_PYTHON if Path(SYSTEM_PYTHON).exists() else sys.executable']
+ ['return resolve_probe_interpreter()']

FAIL: test_the_resolved_probe_interpreter_is_never_a_tool_shim
    self.assertFalse(review_isolation.is_tool_shim(review_isolation._probe_python()))
AssertionError: True is not false

FAIL: test_on_darwin_the_shimmed_system_python_is_actually_resolved_away
```

**Revert B — make resolution fall back to the shim instead of failing closed** (a `return
system_python` inserted ahead of the `raise`):

```
Ran 17 tests ... FAILED (failures=2)
FAIL: test_resolution_fails_closed_and_never_falls_back_to_the_shim
FAIL: test_a_developer_dir_that_only_offers_another_shim_is_refused
```

**Restored:**

```
Ran 17 tests in 0.030s
OK
```

Both the *what* (never exec a shim) and the *how* (fail closed, never fall back) are held by
assertions that fail when removed.

---

## Review Feedback Resolution

No open review findings were carried into this run; `relevant_previous_findings: none open`.
The Coordinator's grounding evidence was re-verified rather than assumed, as instructed:

| | Coordinator's claim | Outcome |
| --- | --- | --- |
| E1 | `/usr/bin/python3` is Apple's tool shim | **Confirmed** — P1, plus the same-inode result for `git` |
| E2 | the sandboxed pre-flight runs that shim | **Confirmed** by reading `_probe_python`/`_run_probe`/`preflight_probe` |
| E3 | the CLT root is admissible and not the problem | **Consistent** — not independently re-proven here; the live `isolate()` in §6 exercises the proof end to end and passes |
| E4 | the denied `/private/var` + `/Applications` resolution machinery is what fails | **FALSIFIED** — P3. `xcode-select -p`, `xcrun --find python3`, `git --version` and `/usr/bin/python3` all succeed inside the real profile with both paths still denied. Not acted on. |
| E5 | resolving to the real interpreter inside the CLT root is a clean fix admitting nothing new | **Confirmed** — P4, and it is the fix that shipped |
| E6 | the `couldn't create cache file` stderr is benign; do not "fix" it with a temp-dir grant | **Preserved** — docstring untouched, `git` still emits it, still classified benign |

Two things the reviewer should weigh explicitly rather than take on trust:

1. **The mechanism is not confirmed by reproduction** (see *What I did NOT prove*). The host
   state that produced the dialog was overwritten by a CLT reinstall at 2026-08-27
   21:02–21:08. I am asserting that the fix removes the only reachable call site for the
   prompt (P2), not that I watched the old failure happen.
2. **`git --version` still runs a shim on purpose.** Reasoned above and recorded in the
   source. If the reviewer disagrees, that is a scope decision to overturn deliberately, not
   an omission to patch.

Operator impact during this run: the installer dialog was **not** raised once. One deliberate
reproduction attempt was made and it exited 0.
