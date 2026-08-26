# Worker Result

STATUS: COMPLETE

## Summary / Analysis

D-G, D-H and D-I of `artifacts/runs/run_4d1c47c838db/DESIGN.md` are implemented, tested and
committed on `agent/final-review-observability-evaluation`. All three DESIGN iterations were read
and treated as authoritative: iteration 2's recursive Class IMM proof supersedes iteration 1's
root-only `W_OK` rule, and iteration 3's ruling that **D-I stands exactly as approved** was followed
— `COMPATIBILITY.md` carries the two replacement blocks from the D-I section verbatim and nothing
else, including RK-10's "scanned" imprecision, which was deliberately **not** "improved".

Two DESIGN defects and one host divergence were found. Per the task's instruction, they are
reported as Findings below rather than silently absorbed; **F-101 required a stated deviation**
because DESIGN's literal text is self-contradictory and unimplementable.

Not done, and out of this dispatch's scope: **Step 6, the §7 baseline re-run under isolation**
(B-1′ … B-7). That step dispatches a live Final Reviewer through Orca and is a Coordinator
procedure, not an IMPLEMENTATION deliverable. The tooling it needs is complete and was exercised
end to end against the real fixture (evidence below).

## Changes

### D-H — evidence-bundle sanitization (`scripts/run_logging.py`)

* `EMBED_OMISSION_REASONS` — the closed reason vocabulary (`redaction_residue`,
  `table_structure_changed`, `unreadable`), the same discipline `REDACTION_CATEGORIES` uses.
* `safe_embedded_text(raw, *, redact)` — the one gate every embedded string passes. `redact=True`
  sanitizes `ORCHESTRATOR_LOG.md` (not a post-redaction artifact); `redact=False` verifies
  `input.md` / `report.md` without transforming them, because re-redacting would make
  `digest_verified` false for a clean artifact.
* `_embedded_artifact()` takes `redact` and emits `content_omitted_reason` /
  `redaction_residue_detected`.
* The `orchestrator_log` block is H.3's shape: `content` → **`content_redacted`**, `digest` →
  `digest_pre_redaction` + `digest_post_redaction`, plus `redaction_policy_version`, `redactions`
  and `content_omitted_reason`. `assert_retained_path_field()` still runs on `["path"]`.
* `integrity["omitted_content"]` added and populated. `missing_artifacts` keeps its original
  meaning (absent/unreadable file); content withheld for residue is a different fact and is
  reported as one.
* `FINAL_REVIEW_EXPORT_SCHEMA_VERSION` `"1.0"` → **`"2.0"`**; the exporter docstring carries the
  H.1 rule verbatim.
* The authoritative local log is opened read-only and is never rewritten (T-7.11 asserts it).

### D-G — reviewer execution isolation (`scripts/review_isolation.py`, new, ~1,700 lines)

`prove_immutable()` (I-1…I-6 over the whole subtree), `prove_immutable_narrowing()`,
`enumerate_boundaries()`, `read_mount_table()` / `read_firmlinks()`, `scan_readable_set()` (passes
A–D plus S), `compute_readable_set()`, `assert_no_unscanned_descendant()`,
`assert_carve_outs_denied()`, `compute_traversal_set()`, `render_seatbelt_profile()`,
`wrap_command()`, `build_session()`, `preflight_probe()`, `orca_check_probe()`, `run_probes()`
(NEG-0…NEG-8), `build_attestation()` / `write_attestation()`, `repatriate()`, `teardown()`.

`/private/var` and `/Library` are absent from the candidate list by construction and are on
`NEVER_ADMITTED`; `/System/Volumes` is a mandatory carve-out. The profile's metadata surface is a
closed traversal set, never a global `(allow file-read-metadata)`.

### D-G CLI (`scripts/final_review_eval.py`)

The `isolate` subcommand with `--run-id / --fixture / --session-base / --policy-file / --allow-read
/ --enforcement / --attempt / --terminal / --base / --out / --no-plant` and the mutually exclusive
`--repatriate` / `--teardown` forms. Exit codes reuse G.7's mapping (2 contract, 4 leak/fixture, 1
input). The module docstring says "Six subcommands", asserted by T-10.

`review_isolation` imports `final_review_eval` at module scope (one `materialize`/`scan_leak`, not
two); `final_review_eval` imports `review_isolation` **inside the one handler**, so there is no
cycle.

### D-I — `COMPATIBILITY.md`

Both replacement blocks applied verbatim from the D-I section at DESIGN.md line 965. No other
`COMPATIBILITY.md` text touched.

### Docs

`CHANGELOG.md` (the `2.0` break with its reader rule, and the new isolation tooling),
`orca-worker-reviewer-orchestration/SKILL.md` §9 (one paragraph, outside the
`#### Final review contract` block — `validate_skills.py`'s `FINAL_REVIEW_CONTRACT_MAX_LINES` is
unaffected and validation passes), `scripts/fixtures/final_review_eval/README.md` (the reproduction
pointer).

Untouched, as instructed: `VERSION`, `LICENSE-DECISION.md`, `release_manifest.py`, the fixture
trees, the answer key, the scorer, detection/search policy, H-1/H-2/H-4/H-5.

## Modified Files

| file | change |
|---|---|
| `scripts/run_logging.py` | D-H |
| `orca-worker-reviewer-orchestration/tools/run_logging.py` | byte-parity twin, synced |
| `scripts/review_isolation.py` | **new** — D-G |
| `scripts/final_review_eval.py` | `isolate` family, docstring count |
| `scripts/test_run_logging.py` | T-7.1…T-7.12 + the `:3181` `["content"]` → `["content_redacted"]` update |
| `scripts/test_review_isolation.py` | **new** — T-8, T-9 (NEG-0…NEG-8), T-9.9 |
| `scripts/test_final_review_eval.py` | T-10 |
| `COMPATIBILITY.md` | D-I, verbatim |
| `CHANGELOG.md`, `SKILL.md`, `scripts/fixtures/final_review_eval/README.md` | Step 7 |

## Unit Tests

### Added / Modified Tests

**T-7 (18 tests, `scripts/test_run_logging.py::BundleSanitizationTests`).** T-7.1…T-7.12 as
specified, plus the `safe_embedded_text()` units and the closed-vocabulary assertion. Every
poisoned case is a **positive control plus a negative control in the same method**: `poison()`
asserts the value actually reached the raw local log before anything asserts it cannot reach the
bundle, and every negative assertion is made against the **whole serialized bundle**, not the log
object — a leak that moved to another key is still a leak.

**T-8 / T-9 (70 tests, `scripts/test_review_isolation.py`).** Session layout and the six G.2 rules;
scan passes A/B/C/D/S each catching a distinct planted copy; the immutability proof rejecting the
F-001 shape; boundary enumeration and the unexplained-boundary hard failure; profile clause order
and the closed metadata surface; attestation legality; `--enforcement none`; `--repatriate` /
`--teardown` refusals; the launch line; and NEG-0…NEG-8 against a real sandbox.

* **NEG-0** runs inside each of NEG-2/NEG-3/NEG-4's methods, never as a separate test.
* **T-9.7 / NEG-7** plants a byte-identical answer key in the host's *resolved*
  `tempfile.gettempdir()` and `~/Library/Caches`, asserts the unsandboxed control reads both, then
  asserts every sandboxed `open`/`exists`/`stat`/`listdir`/`cat`/`ls`/`find`/`grep` is denied,
  including through a `review_root` symlink and the `/System/Volumes/Data` alias, and asserts the
  plants are gone afterwards.
* **T-9.9** is the F-001 regression guard: it builds the shape the superseded root-only `W_OK` rule
  admitted and asserts `prove_immutable()` rejects it and `assert_no_unscanned_descendant()` raises,
  with no dependence on a plant surviving on disk.

**T-10 (8 tests, `scripts/test_final_review_eval.py`).** Exit codes per form, the mutual exclusion,
the subcommand count, and a real leak in an `--allow-read` root mapping to exit 4.

### Behavior Covered

The load-bearing pairs, each a positive control proving the unfixed case genuinely leaks and a
negative control proving the fix blocks it:

| mechanism | positive control | negative control |
|---|---|---|
| bundle sanitization | the poisoned value **is** in the raw local log | it is absent from the whole serialized bundle |
| execution isolation | NEG-0: the unsandboxed probe reads the key and `git show` succeeds | NEG-2/3/4: `open`/`stat`/`exists`/`listdir`/`git` all denied |
| writable-descendant admission | the plant is readable unsandboxed, sha256 == the key's | every read denied, `exists` False, `find`/`grep` empty stdout |
| the F-001 rule itself | the root's own `W_OK` is False, so the old rule admitted it | `prove_immutable()` rejects it; the invariant assertion raises |
| a profile that is too tight | — | `/bin/ls subject` still works inside the sandbox (RK-4) |

### Execution

```text
Command: python3 -m unittest discover -s scripts -p 'test_review_isolation.py'
Result: PASS  (70 tests, OK)

Command: python3 -m unittest discover -s scripts -p 'test_run_logging.py'
Result: PASS  (181 tests; 2 PRE-EXISTING failures, see F-103)

Command: python3 -m unittest discover -s scripts -p 'test_final_review_eval.py'
Result: PASS  (77 tests, OK)

Command: python3 scripts/validate_skills.py
Result: PASS  (Skill validation PASSED, 463 checks)

Command: python3 scripts/verify_package.py
Result: PASS  (Package verification PASSED, 109 source files)

Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result: PASS  (identical)

Command: git diff --check
Result: PASS  (clean)
```

## Additional Validation

**The mechanism was exercised end to end against the real fixture**, not only against synthetic
unit inputs:

```text
python3 scripts/final_review_eval.py isolate --run-id run_smoke --enforcement seatbelt
  scope_enforcement: seatbelt        properties: S1 PASS  S2 PASS  S3 PASS
  probes: NEG-0..NEG-8 all PASS      no_unscanned_descendant: PASS
  carve_outs_denied: 75              key_bearing_roots_discovered: 1
```

The derived proof counters reproduce DESIGN G.3.2's measured table **exactly** on this host, which
is independent evidence that the recursive proof is the one the design specified:

| root | this implementation | DESIGN G.3.2 |
|---|---|---|
| `/bin` | 1 dir, 37 files, 0 writable | 1 dir, 37 files, 0 writable |
| `/sbin` | 1 dir, 74 files, 0 writable | 1 dir, 74 files, 0 writable |
| `/private/etc` | 31 dirs, 230 files, 0 writable | 31 dirs, 230 files, 0 writable |
| `/usr` | 1,173 dirs, 21,796 files, 0 writable | 1,173 dirs, 21,796 files, 0 writable |
| `/System` | 169,297 dirs, 286,743 files, 0 writable | 169,297 dirs, 286,743 files, 0 writable |

The derived carve-out set for `/usr` is exactly `{/usr/local, /usr/libexec/cups, /usr/share/snmp}`
and for `/System` exactly the seven `/System/Library/*` firmlinks plus the mandatory
`/System/Volumes` — i.e. the design's list, **derived from `/usr/share/firmlinks` and the mount
table at session-build time rather than hard-coded**, as `## Risks / Open Issues` requires.

The pre-flight probe did its job on the first real run: it refused the session because
`sys.executable` on this host is an Anaconda interpreter under `/Users`, which is never admitted.
That is the designed outcome — a widening must be an explicit `--allow-read`, never a silent one.

**O-1 is not yet discharged.** `orca_check_probe()` is implemented per G.5, but running
`orca orchestration check` inside the sandbox needs a live terminal handle and so belongs to the
Step 6 capture. The Coordinator must treat a failure there as a blocking finding, per O-1.

## Findings

### F-101 — DESIGN H.2 step 2's residue check is not satisfiable, and contradicts T-7.3/T-7.4/T-7.8

**Severity: MAJOR (blocking as written). Resolution: deviated, with the deviation stated here and
in the code.**

H.2 step 2 requires `again == candidate` **and** `extra == ()`. The second clause cannot hold for
`redaction/1.1`, because two of its categories deliberately preserve a readable anchor and
therefore re-match their own output while rewriting it to **identical bytes**:

```text
'GITHUB_TOKEN=ghp_deadbeef1234'  -> 'GITHUB_TOKEN=<REDACTED:env_secret_pattern>'
                                    text_fixed=True  extra=(env_secret_pattern, 1)
'https://u:p@h/p'                -> 'https://<REDACTED:url_credential>@h/p'
                                    text_fixed=True  extra=(url_credential, 1)
```

Two of H.2's own seven "verified fixed point" cases (`AWS_SECRET_ACCESS_KEY=…`, `https://u:p@h/p`)
fail the clause. Enforcing it literally would omit the log from **every** bundle that logged a
secret-named assignment or a credentialed URL — exactly the bundles sanitization exists for — and
would make DESIGN's own T-7.3, T-7.4 and T-7.8 unsatisfiable, since those require the content to be
*embedded* with a non-zero redaction count.

Implemented: the **text** fixed point (`again == candidate`), which is the complete security
statement — a second pass that matched and rewrote to different bytes breaks text equality, and one
that rewrote to identical bytes removed nothing, because the only region a category can rewrite to
itself is a region that is already its own placeholder. A placeholder-masking variant was built and
rejected: `env_secret_pattern`'s `\s*` lets a masked `KEY=` swallow the *next* token, producing
false positives. The docstring records all of this at the point of edit.

RK-7 says the response to a real non-fixed-point input is a redaction-policy fix (a MINOR bump),
but `## Risks / Open Issues` → *Explicitly not designed* forbids changing the policy's five
categories. That contradiction is itself part of this finding and is left for the boundary owner.

### F-102 — G.3.2's `<20 s` proof cost does not hold on this host

**Severity: MINOR. Resolution: implemented as designed; the measurement is reported.**

DESIGN measures the full proof at "under 20 s in total, once per session". Measured here:
`/System` **210 s**, `/Library/Developer/CommandLineTools` 3.6 s, `/usr` 1.4 s — about **3.5 min**
per session. The cost is `os.access()` per file over ~420,000 files, plus the narrowing loop's
re-walks. The mechanism is unchanged and correct; only the constant differs. Two bounded decisions
were needed and are documented in code rather than hidden:

* narrowable failures (I-1 unenumerable directory, I-4 boundary) are recorded uncapped so narrowing
  converges in two walks rather than one walk per offending directory;
* **NEG-5's rescan runs all five passes over Class USR roots and the two *walk* passes (A, D) over
  Class IMM roots.** Passes B and C open and hash every regular file; over the admitted IMM roots
  that is ~420,000 files and minutes per capture, and what they would re-derive is already
  established more strongly by the proof. Passes A and D still find a stray `answer_key.json` or a
  packaged copy, at walk cost. The per-root pass list is recorded in the probe record.

If the boundary owner considers the reduced IMM pass set a weakening of NEG-5, the remedy is a
DESIGN decision, not an implementation one.

### F-103 — two pre-existing test failures, from a coordinator commit, unrelated to this work

**Severity: MINOR. Resolution: not fixed — out of authorized scope.**

`RetainedReportWhitespaceExemptionTests` (2 tests) fails on `git diff --check` over the OS-22
commit range. Cause: `artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md` and
`REVIEW_DESIGN_iteration2.md`, committed in `289d00f` ("Record DESIGN phase gate PASS"), carry
Markdown hard-break trailing whitespace and are not covered by the `.gitattributes` exemption,
which is scoped to `artifacts/runs/*/final_review_audit/**/report.md`.

**Verified pre-existing**: `git stash -u` to a clean tree reproduces both failures identically.
`git diff --check` on my own working tree is **clean**. Not fixed because both remedies are
forbidden here — editing a settled Reviewer record violates DESIGN ordering rule 4, and widening
the `.gitattributes` rule is unauthorized and would break
`test_the_gitattributes_rule_is_exactly_the_one_designed`, which pins it to exactly one rule.

### F-104 — two DESIGN rules conflict on escaping symlinks; resolved per class

**Severity: MINOR. Resolution: implemented both, split by class.**

G.3.1 says escaping symlinks "are **recorded but are not hits**" because seatbelt evaluates the
resolved target. G.3.3's bounding paragraph says "every symlink encountered whose realpath escapes
the root **is itself a hit**". Applied uniformly, the second rule fails every capture on this host:
`/System` and `/Library/Developer/CommandLineTools` ship hundreds of vendor symlinks out of
themselves.

Resolved by class, which is what each rule is actually about: for **Class USR** the scan *is* the
evidence, so an uncovered path is a gap and a hit (pass S); for **Class IMM** the *profile* is the
evidence, so the link grants nothing the profile does not already grant on the target's own terms.
Both branches are asserted by tests.

### Two implementation-side defects found by the tests and fixed

Recorded because each would have made a negative test assert nothing:

1. **`_all_denied()` treated a probe that never ran as a full denial.** The first real sandboxed
   run failed to exec (F-101's Anaconda interpreter), the probe returned no JSON, and NEG-7
   reported **PASS** on an empty result. It now fails closed on an empty or failed probe, and
   `ProbeFailClosedTests` asserts it.
2. **NEG-5 scanned paths the profile denies.** The rescan walked `/System` without the carve-outs,
   so it reported every answer-key copy anywhere on the data volume — reachable only through the
   `/System/Volumes/Data` alias the profile denies — as a hit. The carve-outs are part of the
   readable set's definition and are now passed to the scan.

One narrow, named exemption was required and is documented: `exists`/`stat` on the session's
*ancestors* succeed by construction, because G.4 clause 2 must keep every ancestor traversable or
the kernel cannot resolve `review_root` at all — and they reveal nothing a process standing in that
tree does not already know from its own cwd. **Enumeration** of the temp root is the discovery
channel, it is denied, and it is the operation DESIGN names for that target and the one asserted.

## Review Feedback Resolution

Not applicable — first IMPLEMENTATION iteration, `relevant_previous_findings: none`.
