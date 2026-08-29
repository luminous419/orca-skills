# TEST — run_f71a83d7ebe8

Phase: TEST
Role: WORKER
Iteration: 1
Task: `task_08cf1b49ef54` / Dispatch: `ctx_922fcb45df0a`
Branch: `agent/final-review-observability-evaluation` (Draft PR #20)
Baseline HEAD at dispatch: `2b00e80`

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS

---

## Summary

I assessed the TEST coverage of the IMPLEMENTATION delta against T1–T5 and found it
largely sound, with **two real gaps in T2 — the security seam — which I closed**. I made
no production change; the two additions are tests.

The delta's own account of itself checks out. I did not take the IMPLEMENTATION report's
word for the C4 premise: I reproduced it directly on a real Linux host, where `/dev` fails
the recursive immutability proof on `/dev/shm` and `/dev/mqueue` exactly as claimed, while
`/bin`, `/sbin` and `/usr` pass. That is the defect the fix removes, observed rather than
inferred.

The gaps I closed were both at the `isolate()` boundary. `imm_candidates_for_enforcement()`
was tested as a pure function, and the `--imm-candidate` CLI flag was tested with
`isolate` patched out — so the layer between them, *that `isolate()` actually hands its
`imm_candidates` to `compute_readable_set()`*, was asserted by nothing. An `isolate()`
that dropped the argument, or that passed `DEFAULT_IMM_CANDIDATES` no matter what it was
given, kept every existing test in the file green. Separately, the "narrowing only"
property that the flag's help text promises had no direct assertion at all.

T5's loose end is resolved exactly: a `--depth=1` checkout skips **four**
`RetainedReportWhitespaceExemptionTests` (not two), and `18 darwin + 6 orca-runtime +
1 sandbox-exec + 4 shallow = 29`, which is CI's `skipped=29` reconciled test-by-test.

---

## Analysis

### T1 — enforcement=none is directly and portably asserted. Coverage is ADEQUATE.

`UnenforcedTests` carries **no** `@DARWIN_ONLY` decorator (`scripts/test_review_isolation.py:1032`),
so the whole class executes on Linux. `test_t88_enforcement_none_records_unenforced_and_fails_s2`
runs the real CLI end-to-end and asserts, with real assertions rather than smoke:

- `scope_enforcement == "unenforced"` and `properties["S2"] == "FAIL"`;
- every probe other than NEG-0/NEG-1 is `NOT_APPLICABLE_UNENFORCED`, with an explicit
  `assertNotEqual(probe["result"], "SKIP")` — the "absence of enforcement, not absence of
  evidence" distinction the objective asks for;
- **zero** Class IMM entries in the attestation's readable set — which is precisely
  "not gated behind the Seatbelt-only proof";
- the three Class USR session roots are all present with `scanned: true`.

Confirmed executed and passing on Linux 3.11/3.12/3.13 (verbose run output, `... ok`).

I judged this adequate on its own terms, and did **not** add a duplicate. What I did add
(below) strengthens it in a direction `test_t88` cannot reach: `test_t88` only demonstrates
the ungating on a host whose `/dev` happens to be writable. My addition proves it on any
host, by construction.

### T2 — the security contract did not weaken. TWO GAPS FOUND AND CLOSED.

| T2 requirement | Status | Evidence |
|---|---|---|
| a candidate on `NEVER_ADMITTED` is still refused | **adequate, no change** | `test_a_never_admitted_candidate_that_exists_is_refused_outright` — portable, fixture-controlled root, and it carries its own **positive control** (the same candidate *is* admitted when the list entry is absent), so the refusal is attributed to the list and not to the fixture. `test_the_real_private_var_is_refused_on_the_supported_host` is the darwin host-fact twin. |
| a failing immutability proof is still fatal | **adequate at `compute_readable_set`; GAP at `isolate()`** | `test_a_failing_root_is_never_admitted_as_imm` asserts `IsolationError` with `"immutability proof FAILED"`. But nothing asserted this through `isolate()`, nor that the half-built session is removed on that path. **Closed** — see GAP-1. |
| NEG-5 content scanning still runs (mandatory pass B) | **adequate, no change** | `Neg5ContractTests` builds its readable set with `imm_candidates=FAST_IMM` — i.e. it exercises the override seam itself — and asserts every Class IMM root gets `passes == ["A","B","C","D"]` with `vocabulary == "key_material"`, plus `test_t95_there_is_no_opt_in_imm_content_scan_anywhere` as the regression guard against a default-off gate. Both **passed on macOS** in this run. This is darwin-gated for a real reason: NEG-5 runs only under `--enforcement seatbelt`, so off darwin there is no NEG-5 record to assert against. The portable half of the same guarantee is `test_t84d_mandatory_pass_b_catches_what_a_c_d_cannot_see`, which runs on Linux. |
| narrowing admits FEWER roots, never more | **GAP — nothing asserted it** | The flag's help text promises "narrowing it only ever admits fewer roots", and the reviewer's iteration-1 note repeats the claim, but no test distinguished *replace* from *extend*. **Closed** — see GAP-2. |

#### GAP-1 — the `isolate()` seam was asserted by nothing

`test_an_unprovable_candidate_is_fatal_through_isolate_under_seatbelt`
(`scripts/test_review_isolation.py`, in `UnenforcedTests`).

One unprovable root is pushed through `isolate()` **twice**, and the two outcomes are the
contract:

- under `enforcement="seatbelt"` the proof runs, FAILS, the error names *the caller's own
  supplied root*, and `self.base` is left with no `frv_iso_*` session — the half-built
  session was removed;
- under `enforcement="none"` the same root is inert: the capture succeeds, records
  `unenforced` / S2 FAIL, and the attestation carries zero Class IMM entries.

Two properties make it worth having. First, it is the only test that would fail if
`isolate()` stopped forwarding `imm_candidates`. Second, it proves the ungating **without
depending on the host's `/dev`**: the fixture root is unprovable by construction (an
ordinary mode-0755 directory is writable by the run user everywhere, which is I-2, the
non-narrowable half of the proof), so the assertion holds on a host whose `/dev` is
perfectly immutable.

It is portable because `isolate()` checks only that `SANDBOX_EXEC` *exists* before
computing the readable set, and the readable set is refused before any profile is
rendered — so a stand-in file is enough and `sandbox-exec` is never executed. Patching
the module global is the same pattern `test_t89_seatbelt_on_a_host_without_the_backend_exits_four`
already uses.

#### GAP-2 — "narrowing only" had no direct assertion

`test_a_supplied_candidate_list_replaces_the_default_and_never_widens_it`
(`scripts/test_review_isolation.py`, in `ImmutabilityProofTests`).

Two halves. With **nothing stubbed**, `imm_candidates=()` admits zero Class IMM roots —
the narrowest end of the seam, and proof that an empty list is not silently backfilled
with the default. Then, with the *proof* stubbed to `passed`, a supplied two-root list
produces exactly those two roots in the order supplied, and no member of
`DEFAULT_IMM_CANDIDATES` appears.

The stub is deliberate and scoped: what is under test here is **which roots are
considered**, not whether they prove, so stubbing removes a dependence on the host's file
modes that would otherwise make the test mean different things on different machines.
Keeping the proof *fatal* is a separate, unstubbed assertion — `test_a_failing_root_is_never_admitted_as_imm`
and GAP-1's test — and the docstring says so, so the two cannot be mistaken for each other.

The failure mode this pins is the quiet one: a seam that EXTENDS the built-in default
instead of replacing it, so that naming one root still admits every real host root behind
the caller's back. As the objective puts it, a security seam introduced to fix CI is
exactly where a silent weakening would hide.

### T3 — the portable/gated split is correct. Coverage is ADEQUATE.

Exactly one new `@DARWIN_ONLY` was added by the delta:
`test_the_real_private_var_is_refused_on_the_supported_host`. It is a **genuine
host-topology fact** — `/private/var` is a real, existing, never-admitted path only on
darwin — and its *rule* is separately asserted portably on a fixture root with a positive
control. That is the correct split.

Every other test the delta added is portable and I confirmed each **actually runs and
asserts on Linux**, from the verbose run output:

| Test | Linux |
|---|---|
| `test_a_never_admitted_candidate_that_exists_is_refused_outright` | ok |
| `test_the_own_descriptor_exemption_is_derived_and_never_assumed` | ok |
| `test_only_the_derived_descriptor_directory_is_exempted_from_i3` | ok |
| `test_no_descriptor_directory_means_no_exemption_rather_than_a_wider_proof` | ok |
| `test_the_imm_proof_is_a_seatbelt_input_and_only_a_seatbelt_input` | ok |
| `test_unenforced_still_refuses_a_usr_root_that_carries_key_material` | ok |
| `test_t88_enforcement_none_records_unenforced_and_fails_s2` | ok |
| `test_the_imm_candidate_flag_replaces_the_default_list_and_defaults_to_it` | ok |

**Positive controls where a negative assertion could pass vacuously** — present in every
case I checked:

- never-admitted refusal: the control admits the same root when the list entry is absent;
- descriptor derivation: `derive_own_descriptor_dir((str(self.base),))` must return `None`,
  so the derivation cannot be talked into exempting an ordinary writable directory;
- I-6 exemption: `test_only_the_derived_descriptor_directory_is_exempted_from_i3` asserts
  both directions on one fixture tree (unexempted → I-2/I-3 failure; exempted →
  `own_descriptors == 1`, `writable_files == 0`);
- fail-closed: `test_no_descriptor_directory_means_no_exemption_rather_than_a_wider_proof`
  pins `None` → no exemption.

One test I specifically checked for vacuity and cleared:
`test_a_usr_root_with_a_hit_is_never_admitted` asserts only `assertRaises(IsolationError)`
while passing `imm_candidates=FAST_IMM`, so on a host where `/bin` or `/sbin` failed the
proof it would pass for the wrong reason. I measured it rather than assuming — on Linux
non-root, `/bin` → `/usr/bin` and `/sbin` → `/usr/sbin` both prove immutable
(`passed=True, wdirs=0, wfiles=0`), so the `IsolationError` genuinely comes from the Class
USR key-material hit. Not vacuous. No change needed.

### T4 — no regression in the named areas. Coverage is ADEQUATE.

Each area retains live, meaningful coverage. Counts are distinct test methods observed in
the verbose Linux 3.11 run; **none of them skip on Linux**, and the only non-passing tests
in any run were the two known whitespace-gate failures under a full-depth checkout.

| Area | Tests | Where |
|---|---|---|
| answer-key isolation (kernel-enforced) | 11 (`NegativeContractTests`: NEG-2/3/4/6, alias spellings, data-volume alias, symlink escape, F-402 writable-root denial) + 2 (`Neg5ContractTests`) | `test_review_isolation.py`; darwin+sandbox gated by necessity, **passed on macOS** in this run |
| evidence-bundle sanitization / redaction | 20 `BundleSanitizationTests`, 12 `RedactionPolicyTests`, 5 `ForeignAbsolutePathRedactionTests`, 12 `RecordMetadataRedactionTests`, 5 `EvidenceBundleTests` | `test_run_logging.py` — all portable, all green on Linux |
| attempt-domain validation (F-602) | 12 `AttemptDomainTests` + 5 `AttemptDomainProvenanceTests` | `test_review_isolation.py`, `test_run_logging.py` — portable, green |
| provenance | 8 `AuditProvenanceTests` + 6 `ProvenanceLadderTests` | `test_run_logging.py` — portable, green |
| observability-neutrality | 12 `FinalReviewObservabilityNeutralityTests` | `test_e2e_harness.py` — portable, green |

### T5 — the shallow-checkout skip inventory, reconciled. LOOSE END RESOLVED.

The correction Worker was right to flag this and right about the number: the shallow
checkout skips **four** tests in that class, not two. `_require_git_range()` has exactly
four call sites (`scripts/test_run_logging.py:3992, 4088, 4118, 4139`), and I confirmed
the runtime behaviour rather than inferring it from the call sites — by cloning the repo
at `--depth=1` (`git rev-list --count HEAD` → `1`, base commit `1045815` **unreachable**)
and running the suite against that clone in Docker.

**The four tests that skip under `--depth=1`:**

1. `RetainedReportWhitespaceExemptionTests.test_the_whitespace_gate_passes_over_the_whole_os22_range`
2. `RetainedReportWhitespaceExemptionTests.test_the_gate_fails_again_once_the_exemption_is_removed`
3. `RetainedReportWhitespaceExemptionTests.test_only_retained_reports_are_exempt`
4. `RetainedReportWhitespaceExemptionTests.test_the_pattern_does_not_leak_outside_the_audit_directories`

all with `skipped 'base commit 1045815 is unreachable (shallow or grafted checkout)'`.

The remaining three tests in that class do **not** call `_require_git_range()` and run
normally on CI: `test_the_gitattributes_rule_is_exactly_the_one_designed`,
`test_the_hard_break_report_keeps_its_forty_trailing_space_lines`, and
`test_every_retained_artifact_still_matches_its_recorded_digest`.

**The full reconciliation against CI's `skipped=29`:**

| Reason | Count | Gate |
|---|---:|---|
| `the seatbelt backend is darwin-only; T-8.9 carries the fail-closed guarantee on every other platform` | 18 | `@DARWIN_ONLY` |
| `requires --orca-runtime and a ready Orca runtime` | 6 | `test_orca_runtime.py` integration gate |
| `base commit 1045815 is unreachable (shallow or grafted checkout)` | 4 | `actions/checkout@v4` default `--depth=1` |
| `/usr/bin/sandbox-exec is not present on this host` | 1 | `@NEEDS_SANDBOX` (`test_t86f_a_generated_profile_actually_parses`) |
| **total** | **29** | = CI `skipped=29` |

The six orca-runtime skips are
`FinalReviewRuntimeIntegrationTests.test_final_review_terminal_freshness`,
`OrcaRuntimeIntegrationTests.test_runtime_scenarios`,
`QualityProfileRuntimeIntegrationTests.test_every_dispatch_of_the_run_carries_the_quality_gate`,
`QualityProfileRuntimeIntegrationTests.test_quality_profile_phase_filtering`,
`RiskRuntimeIntegrationTests.test_risk_conditional_phase_graph`, and
`SessionReuseRuntimeIntegrationTests.test_session_reuse_terminal_accounting`.

**The cross-check that closes it:** the same tree at **full depth** on the same
interpreter skips `18 + 6 + 1 = 25` and runs the four gate tests instead, of which two
fail — which is exactly the two known local-only failures, and exactly the `29 − 25 = 4`
difference. The shallow and full-depth numbers reconcile in both directions.

I did **not** modify those tests or the artifacts they gate. Every offending file belongs
to another run and is digest-bound, and the artifact contract forbids it.

---

## Changes

Two tests added. **No production code was changed in this phase** —
`git diff --stat 2b00e80..HEAD` touches one file.

| File | Change |
|---|---|
| `scripts/test_review_isolation.py` | `+122` lines, `-0`: two new tests in `ImmutabilityProofTests` and `UnenforcedTests`. No existing test modified, weakened, deleted, or converted to a skip. |

1. **`UnenforcedTests.test_an_unprovable_candidate_is_fatal_through_isolate_under_seatbelt`**
   — closes GAP-1. Proves `isolate()` forwards `imm_candidates` to the proof under
   `seatbelt` (fatal, root named, session removed) and does not under `none` (reachable,
   zero IMM entries).

2. **`ImmutabilityProofTests.test_a_supplied_candidate_list_replaces_the_default_and_never_widens_it`**
   — closes GAP-2. Proves the supplied list REPLACES the default rather than extending it,
   and that an empty list admits zero Class IMM roots.

Both are portable and were confirmed executing and passing on Linux 3.11, 3.12 and 3.13
as well as macOS. Neither introduces a platform gate: **no new `@DARWIN_ONLY` or
`@NEEDS_SANDBOX` decorator was added by this phase.**

## Modified Files / Artifacts

- `scripts/test_review_isolation.py` — the two tests above.
- `artifacts/runs/run_f71a83d7ebe8/TEST.md` — this report (in-place, per contract).

Commit: `10a4cbc` on `agent/final-review-observability-evaluation`. **Not pushed** — the
Coordinator pushes. No PR, no merge, no force-push. No other run's artifacts were touched;
no `VERSION`, `LICENSE`, workflow, or skill file was touched.

## Validation

Every number below is from a command I ran in this phase and read the output of.

### 1. Full suite — before and after, per platform

"Before" = `2b00e80` (the dispatch baseline). "After" = `10a4cbc`.

| Platform / interpreter | Checkout | Ran | Result |
|---|---|---:|---|
| **macOS 26.5.2, Python 3.11.8 — before** | full depth | 1201 | `FAILED (failures=2, skipped=6)` |
| **macOS 26.5.2, Python 3.11.8 — after** | full depth | **1203** | `FAILED (failures=2, skipped=6)` |
| **Linux `python:3.11` non-root — after** | `--depth=1` | **1203** | **`OK (skipped=29)`** |
| **Linux `python:3.12` non-root — after** | `--depth=1` | **1203** | **`OK (skipped=29)`** |
| **Linux `python:3.13` non-root — after** | `--depth=1` | **1203** | **`OK (skipped=29)`** |
| Linux `python:3.11` non-root — after (cross-check) | full depth | 1203 | `FAILED (failures=2, skipped=25)` |
| Linux `python:3.11` **root** (mandated command) | `--depth=1` | 1203 | `FAILED (failures=6, errors=1, skipped=29)` |

`1201 → 1203` is exactly the two tests I added. Skip counts are **unchanged** on every
platform, which is the point: nothing was made to pass by becoming a skip.

The two macOS failures are the two known local-only whitespace-gate failures, unchanged
from the before-run, and they are the same two that fail in the Linux full-depth
cross-check. They skip under CI's shallow checkout — see T5.

**Linux baseline provenance.** For the "before" state on Linux I rely on GitHub Actions run
`33080957741`, and I verified it myself in this phase rather than taking the previous
report's word for it: `gh run view 33080957741` reports `headSha
f0c92753f3ba716a9895f30034bc0fa1d53e48a1`, `conclusion success`, with `validate (3.11)`,
`validate (3.12)` and `validate (3.13)` each `success` — 1201 tests, `OK (skipped=29)`.
`1201 → 1203` with `skipped=29` held constant is therefore a like-for-like comparison.

### 2. `python3 scripts/validate_skills.py`

`Skill validation PASSED (463 checks)` — exit 0.

### 3. `python3 scripts/verify_package.py`

`Package verification PASSED (109 source files)` — exit 0.

### 4. `git diff --check`

`git diff --check` (worktree) → exit 0, no output.
`git diff --check 6cd2567..HEAD` (the full delta range) → exit 0, no output.

### 5. Real Linux evidence — Docker

Docker **was** available (server `29.6.2`) and every Linux number above is observed output,
not inference.

Two deliberate, disclosed deviations from the literal command in the task:

**(a) I ran the suite as a non-root user** (`-u $(id -u):$(id -g) -e HOME=/tmp/h`) for the
CI-faithful results, in addition to running the mandated root command. This is not
cosmetic. `prove_immutable()` decides writability with `os.access(path, os.W_OK)`, and for
uid 0 that returns `True` regardless of mode — I verified this directly:

```
uid 0
W_OK on 0555 dir : True
W_OK on 0444 file: True
```

So under root every test that builds an immutable fixture with `chmod 0555` fails by
construction. That is exactly what the mandated command produces: all 7 non-passing tests
are in `ImmutabilityProofTests` (`test_a_genuinely_immutable_tree_passes`,
`test_t85...`, `test_t85b...`, `test_t99...`, `test_narrowing_carves_out...`,
`test_only_the_derived_descriptor_directory_is_exempted_from_i3`, and
`test_a_never_admitted_candidate_that_exists_is_refused_outright`), and **six of the seven
are pre-existing tests untouched by this run**. GitHub Actions runs as the non-root
`runner` user, so the non-root runs are the ones that correspond to CI — and they are
green on all three interpreters. **Neither of the two tests I added is among the root-only
failures.**

**(b) I ran against clean clones rather than bind-mounting the live worktree.** Mounting
`$PWD` into a root container would leave root-owned files in the user's repository if a
test aborted mid-cleanup, and a clean clone is in any case closer to CI's fresh checkout.

### 6. A methodology error I made, and corrected

My first Linux run reported an extra `ERROR:
test_t83_a_symlink_in_the_policy_copy_list_is_refused —
FileExistsError: '/w/artifacts/_isolation_policy_link.md'`. That was **my** fault, not a
defect: I had the macOS suite and the Docker suite running concurrently against the same
working tree, and they raced on a repo-relative path that both create and delete. Every
run reported above uses its own tree. I am recording this rather than quietly dropping it,
because an unexplained error in a test log is exactly the kind of thing that costs a later
phase a gate cycle.

## Unit Tests / Testing Strategy

**UNIT_TEST_STATUS: PASS**

Section 14's gate is about production change, and **this phase changed no production
code** — so the gate is not triggered in its strict form. It is satisfied anyway: I added
tests, executed them, and they pass on four platforms.

The strategy was to attack the delta where a silent weakening could actually hide, and to
say "adequate" everywhere else rather than pad the count:

- **Where I added:** the `imm_candidates` seam, at the two boundaries that had no
  assertion — `isolate()`'s forwarding of the parameter, and replace-vs-extend semantics.
  These are the two places where a regression would be invisible to the existing suite.
- **Where I did not add, and why:** T1's `enforcement=none` contract, the never-admitted
  refusal, NEG-5's mandatory pass B, the descriptor-directory derivation, and all five T4
  regression areas already have direct, non-vacuous, currently-passing tests. They are
  named with what they assert in the Analysis section above. Adding more there would have
  been noise.
- **Non-vacuity** was checked, not assumed. Where a negative assertion could pass for the
  wrong reason I looked for the positive control, and where none was written into the test
  I measured the host instead — `/bin` and `/sbin` do prove immutable on Linux non-root,
  so `test_a_usr_root_with_a_hit_is_never_admitted` fails for the reason it claims.

**Independent confirmation of the fix's premise.** Rather than restate the IMPLEMENTATION
report's causal story, I reproduced it on a real Linux host:

```
DEFAULT_IMM_CANDIDATES: ('/bin', '/sbin', '/private/etc', '/dev',
                         '/private/var/select', '/usr', '/System',
                         '/Library/Developer/CommandLineTools')
  /bin:  passed=True   wdirs=0 wfiles=0
  /sbin: passed=True   wdirs=0 wfiles=0
  /dev:  passed=False  wdirs=2 wfiles=0  first=['/dev/shm', '/dev/mqueue']
  /usr:  passed=True   wdirs=0 wfiles=0
  /private/etc, /private/var/select, /System,
  /Library/Developer/CommandLineTools: ABSENT on this host
```

`/dev` fails on `/dev/shm` and `/dev/mqueue`, exactly as C4 claimed. Before the fix that
made `isolate(..., enforcement="none")` exit 4 on an ordinary Linux box. This is observed,
not inferred.

I also noted, without treating it as a defect, that `derive_own_descriptor_dir()` resolves
to `/dev/fd` on **both** macOS and Debian-based Linux, so the `/proc/self/fd` candidate is
never the one selected on either platform in this matrix. The derivation itself is
correct and fail-closed, and its negative control is asserted; I simply did not observe
the second candidate being exercised, and I am not claiming that I did.

## Review Feedback Resolution

**F-IMPL-001 (G2, MAJOR, blocking) — WITHDRAWN in iteration 2; nothing outstanding.**

The finding required "resolve the retained-artifact whitespace failures … then rerun the
complete CI command set". Its premise — that those failures leave the CI matrix red — was
disproved in iteration 2, and I re-verified the disproof independently in this phase from
two directions:

1. `gh run view 33080957741` → `conclusion success` on `f0c9275` for 3.11/3.12/3.13.
2. The mechanism, reproduced locally: a `--depth=1` clone cannot reach base commit
   `1045815`, so `_require_git_range()` calls `skipTest()` and the four range-dependent
   tests skip. `18 + 6 + 1 + 4 = 29` = CI's `skipped=29`, and the same tree at full depth
   skips 25 and fails exactly those two tests instead.

So the two local failures and the green CI matrix are the same fact seen at two checkout
depths, not a contradiction. I took the required action's *intent* — "rerun the complete CI
command set on macOS and Linux 3.11/3.12/3.13 and provide passing output" — and did
exactly that: three green Linux matrix runs at CI's own checkout depth are in the
Validation table.

Per the task's explicit instruction I did **not** "fix" those tests: every offending file
belongs to another run and is digest-bound, and the artifact contract forbids modifying
another run's artifacts.

**Iteration 2 review (PASS) — no findings.** Its Test Review made four claims I was asked
to check rather than inherit. All four hold, and I have now attached direct evidence to
each: the one new `@DARWIN_ONLY` is genuine host topology (T3); `enforcement=none` is
directly tested and runs on Linux (T1); the named regressions remain live (T4); and
narrowing the candidate list can only admit fewer IMM roots — which was *asserted* in that
review but tested by nothing, and is now pinned by
`test_a_supplied_candidate_list_replaces_the_default_and_never_widens_it` (T2/GAP-2).

## Constraint Compliance

- macOS sandbox-exec isolation semantics: **unchanged**. No production code touched.
- Nothing made to pass by converting an assertion into a skip or no-op: **confirmed** —
  skip counts identical before and after on every platform, and no new gate decorator.
- Other runs' artifacts: **untouched**.
- OS-23 scope, H-1/H-2/H-4/H-5 conclusions, Risk / Quality Profile / Agent Profile / Final
  Review lifecycle semantics: **unchanged**.
- `VERSION`, `LICENSE`: **unchanged**. No new PR, no merge, no force-push, no push.

## Quality Gate

profile_status: absent — general gate applies.

| Gate | Verdict |
|---|---|
| G1 explicit requirement violation | none — T1–T5 all addressed; additions scoped to real gaps |
| G2 result does not work | none — 3/3 Linux matrix green at CI checkout depth |
| G3 severe regression | none — skip counts and failure set unchanged |
| G4 data loss / security / irreversible | none — no production change; the seam is now *more* tightly pinned |
| G5 missing evidence | none — every claim above has command output behind it |

STATUS: COMPLETE
UNIT_TEST_STATUS: PASS
