# Worker Result

STATUS: COMPLETE

> **Stated up front, because the Mandatory Invariant turns on it.** The suite finishes with **two
> failures**, and neither is caused by this change: both are
> `RetainedReportWhitespaceExemptionTests` failing `git diff --check` over the OS-22 commit range
> because of trailing spaces in a *different* Run's committed review artifacts, and both fail
> identically on the clean tree at HEAD (`d0afdfe`) with everything here stashed. Every test this
> change added or corrected passes, and no test that passed at HEAD fails now. Full evidence and
> the reason it was not fixed here are in `## Findings` -> **F-302**. If the phase gate requires a
> fully green suite regardless of provenance, F-302 is the one thing to decide.

## Summary / Analysis

This iteration closes the two code-vs-DESIGN divergences the DESIGN phase of this Run reported as
Findings **F-201** and **F-202**, by implementing `DESIGN.md`'s Implementation Steps **1, 2, 3, 4,
5 and 6** exactly as written. No design decision was reopened, no new one was made, and nothing
outside those steps was changed except where a step's own correctness required it (each such place
is named explicitly in `## Findings` below rather than folded into the diff).

* **F-202** (`scripts/run_logging.py`) — `safe_embedded_text()`'s residue check was the bare
  whole-string `again != candidate` rule D-4.1 replaced. It is now the per-match rule:
  `_residual_matches_are_self_output()` requires, for every `(name, pattern, replacement)` in
  `REDACTION_CATEGORIES` and every match `m` of `pattern` in the candidate, that
  `m.expand(replacement) == m.group(0)`. The second `redact_text()` call is removed, not kept
  alongside — one authority for one property. Mirrored byte-identically into
  `orca-worker-reviewer-orchestration/tools/run_logging.py` (Step 6); `cmp` is clean.
* **F-201** (`scripts/review_isolation.py`, `scripts/final_review_eval.py`) —
  `SCAN_PASSES_NAME_ONLY = ("A", "D")` is **removed** (not aliased) and replaced by
  `SCAN_PASSES_IMM = ("A", "B", "C", "D")`. Pass B is now mandatory over every admitted root, is
  driven by `scan_readable_set()`'s own carve-out-pruned walk rather than delegated to
  `final_review_eval.scan_leak()`'s carve-out-blind `rglob`, and takes its vocabulary from the new
  `vocabulary` parameter (`key_leak` for Class USR, `key_material` for Class IMM). The scan reports
  a `content_scanned` counter and returns hits sorted by `(pass, path)`. `final_review_eval.py`
  gains `_is_identifier_form()`, `_key_tokens()`, `key_material_tokens()` and `scan_leak_text()`,
  with `key_leak_tokens()` and `scan_leak()` re-expressed over them and behaviour unchanged.

Both were verified empirically on this host rather than asserted — see `## Additional Validation`.
The measured facts match DESIGN's: 723 vs 712 tokens with the difference exactly the eleven
DESIGN names, all three of the review's counterexample plants caught by the mandatory pass B and
**zero** of them caught by the shipped `("A", "D")` set or by iteration 4's `("A", "C", "D")` set,
and all seven canonical redaction shapes satisfying the per-match rule including the two whose
second pass legitimately reports a non-empty count.

## Changes

### Step 1 — `scripts/run_logging.py` (D-H.2 / D-4.1) — **HARD**

* **New** `_residual_matches_are_self_output(text) -> bool`, immediately above
  `safe_embedded_text()`, with the docstring D-4.1 specifies: it states why the rule is neither
  `redact_text(text)[0] == text` (a whole-string proxy for a per-match property) nor
  `redact_text(text)[1] == ()` (a different property `redaction/1.1` cannot satisfy on the very
  inputs the gate exists for).
* `safe_embedded_text()` step 2 becomes
  `if not _residual_matches_are_self_output(candidate): return None, redactions,
  "redaction_residue"`. The `again, _second_pass_counts = redact_text(candidate)` call is deleted.
* The docstring's two stale paragraphs are replaced: the fixed point is now described as decided
  per match, with text equality named as an immediate consequence that is deliberately not
  asserted separately and explicitly **not** used as a proxy, because a whole-string comparison
  lets a match that removed something be masked by matches elsewhere in the text.
* Signature, the `redact=True` / `redact=False` split, step 1, step 3 and the closed
  `EMBED_OMISSION_REASONS` vocabulary are untouched. `"redaction_residue"` remains the one reason
  this check can produce.

### Step 3 — `scripts/review_isolation.py` (D-5.1 / D-5.2) — **HARD**

* `SCAN_PASSES_NAME_ONLY = ("A", "D")` **deleted**, together with its comment block asserting the
  passes are "a WALK rather than a READ". `SCAN_PASSES_IMM = ("A", "B", "C", "D")` takes its place,
  with a comment stating that pass B is mandatory, that the only per-class difference is its
  vocabulary, and that the immutability proof is not a substitute for the scan.
* **New** `SCAN_VOCABULARIES = ("key_leak", "key_material")`; `scan_readable_set()` gains
  `vocabulary: str = "key_leak"` and raises `IsolationError` on an unknown value rather than
  silently defaulting.
* Pass B **moved into the existing pruned walk**: `b_tokens` is computed once before the walk from
  the selected vocabulary; inside the file loop, after the symlink `continue` and before pass C's
  prefilter, a file whose path contains `__pycache__` is skipped (matching `scan_leak`), the file
  is read under `except (OSError, UnicodeDecodeError)` (matching `scan_leak`), and each
  `scan_leak_text(entry, text, b_tokens, count_heuristics=(vocabulary == "key_leak"))` record is
  appended as `{"pass": "B", **hit}`. The trailing
  `if "B" in passes: for hit in final_review_eval.scan_leak(key, [root])` block is deleted, and so
  is the comment that justified delegating to it ("…which is the only class that runs pass B…").
* `counters` gains `"content_scanned"` — files pass B actually opened and decoded — and the
  returned document gains `"vocabulary"`.
* `hits` is sorted by `(pass, path)` before return, so the record stays stable now that B's hits
  interleave with A/C/D's rather than being appended after them.
* Pass C gains the size prefilter D-4.2 keeps (`## DESIGN iteration 4` Step 3; iteration 5's
  Components table states it is unchanged, which presupposes it): **new** `_answer_key_size(key)`
  reads the size from the same `key["__source_path__"]` `_answer_key_digest()` reads and returns
  `None` on a missing path or `OSError`; the walk `continue`s before `sha256_path(entry)` when the
  sizes differ, and an `OSError` from `lstat()` falls through to hashing so the prefilter can never
  turn an unreadable file into a silent pass.
* `run_negative_probes()`'s NEG-5 loop selects `SCAN_PASSES_IMM` + `vocabulary="key_material"` for
  Class IMM and `SCAN_PASSES_ALL` + `vocabulary="key_leak"` for Class USR; each `rescan_detail[]`
  entry gains `"vocabulary"` and `"content_scanned"` beside its existing fields.
* The NEG-5 comment block is rewritten wholesale: the duplicated sentence is gone, and the claim
  "What passes B and C would be re-deriving is already established more strongly by the recursive
  proof" is deleted rather than edited.
* Two stale claims that the mandatory pass B makes false were corrected in the same commit (see
  `## Findings`, note N-1): `compute_readable_set()`'s docstring and the module docstring's S3 row.
* `ISOLATION.json.limitations[]` now carries G.6's exact privileged-writer-only sentence; the "not
  content-scanned" clause is gone (see `## Findings`, note N-2).
* `build_parser()` is unchanged. `--scan-imm-content` is not added, and `SCAN_PASSES_IMM_CONTENT`
  is not created.

### Step 4 — `scripts/final_review_eval.py` (D-5.1) — **HARD**

* **New** `_is_identifier_form(marker)` — `return "_" in marker` — with the docstring naming the
  three measured collisions (`/usr/share/dict/web2`, `/usr/share/tokenizer/ko/dicrc`,
  `AVContentKeySession.h`).
* **New** `_key_tokens(key, *, include_labels)` — the single construction both vocabularies derive
  from, so `key_material_tokens(key) ⊆ key_leak_tokens(key)` holds structurally.
* `key_leak_tokens()` re-expressed as `_key_tokens(key, include_labels=True)`, docstring kept
  verbatim; **new** `key_material_tokens()` as `_key_tokens(key, include_labels=False)`.
* **New** `scan_leak_text(path, text, tokens, *, count_heuristics=True)` — `scan_leak()`'s per-file
  body lifted out unchanged. `scan_leak()` keeps its `rglob`, its `__pycache__` skip, its
  `(OSError, UnicodeDecodeError)` skip and its deliberate absence of an exclusion parameter; its
  per-file body is now a call to `scan_leak_text()`. No CLI change; every existing caller —
  `materialize()`'s workspace check included — is untouched.

### Step 6 — `orca-worker-reviewer-orchestration/tools/run_logging.py`

Byte-identical mirror of Step 1. `cmp` against `scripts/run_logging.py` is clean.

## Modified Files

| file | step |
|---|---|
| `scripts/run_logging.py` | 1 |
| `scripts/test_run_logging.py` | 2 |
| `scripts/review_isolation.py` | 3 |
| `scripts/final_review_eval.py` | 4 |
| `scripts/test_review_isolation.py` | 5 |
| `scripts/test_final_review_eval.py` | 5 (T-8.4g) |
| `orca-worker-reviewer-orchestration/tools/run_logging.py` | 6 |
| `artifacts/runs/run_75c5c6046f35/IMPLEMENTATION.md` | this Worker Result |

`VERSION`, `LICENSE-DECISION.md`, `CHANGELOG.md`, `SKILL.md`, `COMPATIBILITY.md`, the detection and
search policy, and the H-1/H-2/H-4/H-5 conclusions are untouched. No branch and no PR was created;
the work is committed on `agent/final-review-observability-evaluation`.

## Unit Tests

### Added / Modified Tests

**Added — `scripts/test_review_isolation.py`**

| DESIGN id | test |
|---|---|
| T-8.4b | `test_t84b_pass_c_survives_the_imm_pass_set` |
| T-8.4c | `test_t84c_the_pass_c_size_prefilter_is_an_equivalence` |
| T-8.4d | `test_t84d_mandatory_pass_b_catches_what_a_c_d_cannot_see` |
| T-8.4e | `test_t84e_the_imm_vocabulary_is_specific_not_merely_smaller` |
| T-8.4f | `test_t84f_the_two_vocabularies_cannot_drift_apart` |
| T-9.5 | `Neg5ContractTests.test_t95_every_admitted_root_carries_its_class_pass_set_and_vocabulary` and `…test_t95_there_is_no_opt_in_imm_content_scan_anywhere` |
| — | `test_the_unknown_vocabulary_is_refused_rather_than_defaulted` (the new parameter's closed enum) |

**Added — `scripts/test_final_review_eval.py`**

| DESIGN id | test |
|---|---|
| T-8.4g | `ScanLeakRefactorTests.test_t84g_the_records_are_identical_over_every_hit_shape` and `…test_t84g_the_shipped_fixture_is_unchanged_too` |

**Added — `scripts/test_run_logging.py`**

| DESIGN id | test |
|---|---|
| T-7.13 | `test_t713_two_anchored_categories_are_recognised_again_and_still_embedded` |
| T-7.14 | `test_t714_residue_is_decided_per_match_not_by_whole_string_equality` |

**Corrected in place**

| test | correction |
|---|---|
| `test_t73_a_secret_named_assignment_in_result_never_reaches_the_bundle` (T-7.3) | now also asserts `content_redacted is not None` and `content_omitted_reason == ""` — the log is EMBEDDED, which the deleted `extra == ()` clause made unsatisfiable |
| `test_t74_a_url_credential_never_reaches_the_bundle` (T-7.4) | same two assertions |
| `test_t79_residual_text_is_omitted_with_a_reason_and_the_export_ships` (T-7.9) | monkeypatch retargeted from `redact_text` to `REDACTION_CATEGORIES`, because the corrected step 2 iterates the category tuple directly and a stubbed `redact_text` no longer drives it |
| `test_t79b_a_structure_changing_policy_is_omitted_not_silently_shipped` | the stub now runs the REAL policy before eating pipes, so the test still exercises the structure gate rather than tripping the residue gate first (see `## Findings`, note N-3) |
| `test_an_escaping_symlink_is_not_a_hit_for_a_proven_immutable_root` | `SCAN_PASSES_NAME_ONLY` → `SCAN_PASSES_IMM`; `assertNotIn("S", SCAN_PASSES_IMM)`; **new** assertion that the escaping symlink yields no pass-**B** hit either, which is DESIGN §D's fact 2 asserted as behaviour |
| `test_a_carved_out_subtree_is_not_scanned_because_it_is_not_readable` | switched to `SCAN_PASSES_IMM`; the inline comment claiming pass B "is therefore never run over a root that has one" **deleted**; the carved subtree now also holds the key's **prose**, so the test proves the carve-out prunes pass B and not merely pass A |
| `test_t78_every_poisoned_case_keeps_the_table_structure` (T-7.8) | unchanged — it already asserted every poisoned case is embedded, which the corrected step 2 makes true |
| `test_t84_pass_b_catches_a_key_shingle` | unchanged assertion; it now exercises the walk-driven pass B |

### Behavior Covered

* The per-match residue rule fires on a category that still has something to **remove**, and does
  **not** fire on the two `redaction/1.1` categories that re-match their own placeholder output and
  rewrite it to identical bytes (T-7.13/T-7.14). T-7.13 asserts out loud that a non-empty
  second-pass count is expected and safe, so re-adding `extra == ()` fails there with the reason
  in front of the contributor.
* Mandatory pass B catches a reformatted copy, a partial excerpt and a quoted fragment under
  unrelated basenames, and iteration 4's `("A", "C", "D")` set catches none of the three (T-8.4d).
* The Class IMM vocabulary is specific rather than merely smaller: a file holding only the eleven
  excluded tokens plus the `AVContentKeySession.h` sentence plus an expected-count-shaped sentence
  is clean under `key_material` and dirty under `key_leak`, while a file holding one archetype is
  dirty under both (T-8.4e).
* The two vocabularies cannot drift apart: proper subset, with the difference computed from
  `FIXED_LEAK_MARKERS` and the key rather than hard-coded, and `count_heuristics` off for
  `key_material` / on for `key_leak` asserted by calling `scan_leak_text()` directly (T-8.4f).
* Pass C survives the IMM pass set and is corroborated — not contradicted — by pass B on the same
  path (T-8.4b); the size prefilter is an equivalence (T-8.4c).
* The NEG-5 probe record carries `passes == ["A","B","C","D"]` + `vocabulary == "key_material"` for
  every IMM root, `["A","B","C","D","S"]` + `"key_leak"` for every USR root, and an integer
  `content_scanned` for every entry; no `imm_content_scan` field, no `SCAN_PASSES_IMM_CONTENT`, and
  no `--scan-imm-content` option exists (T-9.5).
* The `scan_leak()` refactor changed no record it returns, over the shipped fixture and over a
  synthetic tree with one file per hit shape — token hit, expected-count hit, prose hit, clean,
  undecodable, `__pycache__` (T-8.4g).
* A carve-out prunes pass B, not only pass A — the regression guard for DESIGN iteration 5 §B.

### Execution

```text
Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result:  FAIL   Ran 1134 tests, 2 failures, 6 skipped.
                BOTH failures are PRE-EXISTING and unrelated to this Task -- see
                Finding F-302. Every new and every corrected test in this change
                PASSES; no test that passed at HEAD fails now.

Command: python3 scripts/validate_skills.py
Result:  PASS   Skill validation PASSED (463 checks)

Command: python3 scripts/verify_package.py
Result:  PASS   (exit 0)

Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result:  PASS   (byte-identical)

Command: git diff --check
Result:  PASS   (no whitespace errors)
```

## Additional Validation

Everything below was **run on this host** against the shipped fixture and the real
`redaction/1.1` categories, not reasoned about.

```text
== E-1  the shipped A/D-only set over the three plants ==
  hits: []
== E-2  iteration 4's A/C/D set over the same three plants ==
  hits: []
== E-3  the corrected mandatory pass B (SCAN_PASSES_IMM, key_material) ==
  content_scanned: 3  vocabulary: key_material
  pass-B hits per file: {'chat.txt': 8, 'notes.log': 127, 'vendor.dat': 712}
  hits sorted by (pass, path): True
== E-4  vocabularies ==
  key_leak=723 key_material=712 proper_subset=True
  difference: ['answer key', 'expected finding', 'sd-1', 'sd-2', 'sd-3', 'sd-4', 'sd-5', 'seeded', 'seeded defect', '시드', '정답']
== E-5  per-match residue rule over real redaction/1.1 ==
  self_output=True  second_pass_counts=[]                        '/Users/alice/x/y' -> '/Users/<REDACTED:absolute_local_path>/x/y'
  self_output=True  second_pass_counts=[]                        '/private/tmp/claude-501/foo' -> '<REDACTED:foreign_absolute_path>'
  self_output=True  second_pass_counts=[]                        '/luminous' -> '<REDACTED:foreign_absolute_path>'
  self_output=True  second_pass_counts=[]                        'file:///Users/alice/a' -> 'file:///Users/<REDACTED:absolute_local_path>/a'
  self_output=True  second_pass_counts=['env_secret_pattern']    'AWS_SECRET_ACCESS_KEY=hunter2' -> 'AWS_SECRET_ACCESS_KEY=<REDACTED:env_secret_pattern>'
  self_output=True  second_pass_counts=['url_credential']        'https://u:p@h/p' -> 'https://<REDACTED:url_credential>@h/p'
  self_output=True  second_pass_counts=[]                        'dcap_AAAABBBBCCCCDDDD' -> '<REDACTED:orca_dispatch_capability>'
  self_output=True  second_pass_counts=[]                        '<REPO>/scripts/x.py' -> '<REPO>/scripts/x.py'
  a category that still has something to REMOVE -> False
== E-6  cost of the mandatory pass over a real IMM root (/bin, /sbin) ==
  /bin: files=37 content_scanned=0 hits=0 0.03s
  /sbin: files=74 content_scanned=0 hits=0 0.03s
```

Reading of the six probes:

* **E-1 / E-2 / E-3** are F-201's consequence and its closure, measured. The shipped `("A", "D")`
  set and iteration 4's `("A", "C", "D")` set both return **zero** hits over a reformatted copy, a
  40-column-rewrapped `negative_space_argument` and a `> `-quoted `summary` under unrelated
  basenames. The corrected mandatory pass B flags all three, by 712 / 127 / 8 token hits — margins
  of orders of magnitude rather than one lucky token. `content_scanned` reports the three files it
  opened, and the returned hits are `(pass, path)`-sorted.
* **E-4** confirms D-5.1's arithmetic against the running code: 723 vs 712, proper subset, and the
  difference is exactly the six natural-language markers plus the five defect ids.
* **E-5** confirms D-4.1 against the real policy: all eight shapes satisfy the per-match rule after
  one pass, including `AWS_SECRET_ACCESS_KEY=…` and `https://u:p@h/p`, whose second pass reports a
  non-empty count while removing nothing — which is precisely why the deleted `extra == ()` clause
  was unsatisfiable rather than merely strict. `<REPO>/scripts/x.py` is untouched. A synthetic
  category that still has something to remove is correctly rejected.
* **E-6** is the cost check on real Class IMM roots. `content_scanned == 0` for `/bin` and `/sbin`
  because every file there fails UTF-8 strict decode at once and is skipped exactly as
  `scan_leak()` already skips it — the shape RK-15 describes, confirmed rather than assumed.

**The §7 baseline re-capture (Step 7) is not part of this Task.** DESIGN sequences it *after*
Steps 1-6 are green, and this dispatch's scope is Steps 1-6. RK-15's measured ~46-minute capture
duration applies to it.

## Review Feedback Resolution

| finding | status | evidence |
|---|---|---|
| **F-201** — `SCAN_PASSES_NAME_ONLY = ("A", "D")` is the pass set D-5.1 superseded; pass B not mandatory in the running code | **RESOLVED** | Step 3 + Step 4; constant removed rather than aliased (`grep -rn SCAN_PASSES_NAME_ONLY scripts/ orca-worker-reviewer-orchestration/` returns nothing); pass B relocated into the pruned walk; `vocabulary` parameter, `content_scanned` counter and `(pass, path)` sort in place; the duplicated comment at the old `1316-1319` and the "already established more strongly by the recursive proof" block deleted; T-8.4b…f, T-8.4g, T-9.5; empirical E-1…E-4 |
| **F-202** — `safe_embedded_text()`'s residue check is still the text-equality rule D-4.1 replaced | **RESOLVED** | Step 1 + Step 6; `_residual_matches_are_self_output()` present in both copies and `cmp`-identical; the `again != candidate` check deleted; T-7.3/T-7.4/T-7.9 corrected, T-7.13/T-7.14 added; empirical E-5 |

## Findings

One finding, and three in-scope notes recorded so the reviewer does not have to infer them from the
diff.

### F-301 — DESIGN's illustrative synthetic redaction category for T-7.9 / T-7.14 is unreachable as written

* **DESIGN says** (T-7.9's row, and T-7.14's): inject one synthetic category whose replacement does
  not expand to its own span — *"e.g. `("t", re.compile(r"ZZ_[A-Z]+"), "<REDACTED:t>")`"* — over a
  log containing `ZZ_LEAK`, and assert the log is omitted with reason `redaction_residue`.
* **Why it cannot fire.** `safe_embedded_text(raw, redact=True)` runs `redact_text()` first, and
  `redact_text()` iterates the *same* `REDACTION_CATEGORIES` tuple the test patches, one
  `pattern.subn(replacement, out)` per category. So the first pass rewrites `ZZ_LEAK` to
  `<REDACTED:t>`, which `ZZ_[A-Z]+` no longer matches — there is no residual match left for step 2
  to judge, and the log is embedded. Verified: with that exact triple the export returns
  `content_redacted` non-`None`. The example is a fixed point of its own policy, which is the one
  shape the residue gate is designed to accept.
* **What was implemented instead**, chosen to be the smallest change that keeps DESIGN's stated
  property: `("t", re.compile(r"ZZ_[A-Z]+"), "ZZ_REDACTED_X")`. It satisfies D-4.1's predicate
  literally — `ZZ_[A-Z]+` matches `ZZ_REDACTED` inside the first pass's own output, and that match
  expands to `ZZ_REDACTED_X` ≠ `ZZ_REDACTED`, i.e. the policy still has something to **remove** —
  and it is reachable through the export path. Both tests carry a comment pointing here.
* **This is a defect in one illustrative example inside DESIGN's Testing Strategy, not in D-4.1.**
  The rule itself is exactly right and is implemented verbatim; only the example DESIGN offers to
  exercise it does not exercise it. No design decision is affected, and nothing was silently
  deviated from.

### N-1 — two stale in-code claims that the mandatory pass B makes false, corrected in the same commit

`compute_readable_set()`'s docstring said Class IMM "is NOT content-scanned: the proof makes
planting impossible, so a scan would be scanning a set that cannot change", and the module
docstring's **S3** row said readable paths are covered by "exhaustively content-scanned (Class USR),
or exhaustively proven immutable so that planting is impossible (Class IMM)". Both are the claim
D-5.2 §1 deletes by name, and after Step 3 both are false about the shipped code. Corrected in
place: `scanned: false` is now scoped to session-build time with a pointer to
`probes[NEG-5].roots[].content_scanned`, and S3 now says both classes are content-scanned with the
proof making the IMM scan *durable* rather than replacing it. Raised here because DESIGN's Step 3
row names the constant, the walk and the `1315-1330` comment block explicitly but not these two.

### N-2 — `ISOLATION.json.limitations[]` now carries G.6's exact sentence

The emitted string was *"Class IMM roots are proven immutable recursively at session-build time
against the run user's own privileges, **not content-scanned**; …"*. That is the sentence this Run's
DESIGN F-001 disposition and sweep-table row 1 require to be replaced with G.6's privileged-writer-
only wording, and after Step 3 the "not content-scanned" clause is simply untrue of the code. It now
reads exactly: *"The recursive immutability proof is evaluated at session-build time against the run
user's own privileges, so it does not bind a privileged (root) writer, who is outside the stated
threat model"*. Field, cardinality and position are unchanged; `test_t87e…` still passes unmodified.
Raised because DESIGN records this as a documentary change with no implementation step attached,
while it is in fact an emitted-artifact change.

### N-3 — `test_t79b`'s stub had to run the real policy

`test_t79b_a_structure_changing_policy_is_omitted_not_silently_shipped` stubbed `redact_text` with
a pipe-eating function that performed **no** redaction. Under the old whole-string rule that was
fine; under the per-match rule the un-redacted `/Volumes/ext/a` poison in the candidate is genuine
residue, so the test would have returned `"redaction_residue"` and stopped exercising the structure
gate it exists for. The stub now runs the real `redact_text()` first and eats pipes afterwards,
which preserves both the poison's positive control and the test's subject. DESIGN's Step 2 names
T-7.9 but not T-7.9b; this is the same retargeting, applied to the test next to it.

### F-302 - two PRE-EXISTING whitespace-gate failures in the suite, not caused by this change

* **Symptom.** `test_run_logging.RetainedReportWhitespaceExemptionTests`'s
  `test_the_whitespace_gate_passes_over_the_whole_os22_range` and
  `test_the_gate_fails_again_once_the_exemption_is_removed` both fail with
  `git diff --check` exit 2 over the OS-22 commit range.
* **Cause.** Markdown hard breaks (two trailing spaces) inside
  `artifacts/runs/run_4d1c47c838db/REVIEW_DESIGN_iteration1.md` and `..._iteration2.md`, committed
  by `289d00f` ("Record DESIGN phase gate PASS for PR #20 remediation run"). `.gitattributes`
  exempts exactly `artifacts/runs/*/final_review_audit/**/report.md` and nothing else, so these two
  Reviewer-authored artifacts are inside the gate's scope.
* **Proof it predates this change.** With the whole working tree stashed (`git stash -u`), the same
  two tests fail identically on the clean tree at HEAD (`d0afdfe`), with the same paths. Nothing in
  this change touches `.gitattributes`, that test class, or those artifacts.
* **Not fixed here, deliberately.** The two remedies are (a) trimming the trailing spaces out of
  another Run's committed review artifacts and (b) widening the `.gitattributes` exemption - and
  (b) is pinned shut by `test_the_gitattributes_rule_is_exactly_the_one_designed` and
  `test_only_retained_reports_are_exempt`, which assert the rule is exactly the one designed.
  Neither is authorized by `DESIGN.md` for this correction and neither is inside F-201/F-202, so it
  is reported rather than silently fixed. **`git diff --check` over the working tree is clean**;
  what fails is the historical-range gate.

### No other gap found

`--scan-imm-content`, `scan_imm_content`, `imm_content_scan` and `SCAN_PASSES_IMM_CONTENT` appear
nowhere under `scripts/` or `orca-worker-reviewer-orchestration/` outside the T-9.5 assertions that
now pin their absence. Pass S remains Class USR only via `SCAN_PASSES_ALL`. NEG-5 remains
in-process, fail-closed through `IsolationError` at `review_isolation.py`'s probe collection, and
mapped to `EXIT_LEAK_OR_FIXTURE == 4`.
