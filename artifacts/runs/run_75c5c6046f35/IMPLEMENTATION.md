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

## IMPLEMENTATION iteration 2 — F-401/F-402/F-403

STATUS: COMPLETE

Scope of this iteration: **DESIGN's approved iteration-3 *Expected Changed Files / Implementation
Steps* table (steps 1-4), plus TEST's F-401 and F-402, and nothing else.** No design decision was
reopened and none was made. Where DESIGN's text could not be implemented as written, the code
implements the smallest correction that preserves the property DESIGN states and the deviation is
filed below as a Finding (**F-501**, **F-502**, **F-503**) rather than folded silently into the
diff — same Mandatory Invariant as iteration 1.

The load-bearing claim of this iteration is not "the three findings were each addressed". It is the
one thing `TEST.md`'s `B-1′` attempt could not close: **a real, seeded, end-to-end
`isolate --enforcement seatbelt` run now terminates and writes a valid `ISOLATION.json`.** The
evidence is in `## End-to-End Proof` below, and it is what makes F-401, F-402 and F-403 closed
*together* rather than three individually plausible patches.

### Summary / Analysis

| finding | what shipped |
|---|---|
| **F-401** — `scan_readable_set()`'s pass B `read_text()`s every non-symlink entry the walk reaches; `/dev` is a default Class IMM candidate and 459 of its 462 entries are character or block devices, which is what SIGKILLed the §7 capture at 17m44s | The gate is **general policy, not a `/dev` special case**, exactly as DESIGN's *Risks* row requires: nothing under the walk is opened unless `lstat()` says `S_ISREG`. It precedes pass B (the finding as filed), pass C (whose size prefilter falls through to a full read when `lstat()` raises) and pass D (which hands the path to `tarfile`/`zipfile`, i.e. opens it too). A non-regular entry is still **counted**, in `files` and in the new `counters["non_regular"]`, so the policy is visible in the attestation's per-root record rather than inferred from an absence. |
| **F-402** — `compute_readable_set()` admits realpath-resolved Class USR roots while `isolate()` built the writable list from the raw `tempfile.mkdtemp()` path, so on darwin one generated profile's read clause said `/private/var/folders/…` and its write clause said `/var/folders/…`; seatbelt matches on the resolved path, so every sandboxed write was denied | Fixed **where the path enters the system**: `build_session()` now returns `_realpath(session)`, which corrects the writable set and `wrap_command()`'s `TMPDIR`/`HOME` spellings in one stroke. `isolate()` additionally resolves each writable entry — a second belt, in code rather than in a comment, because two clauses of one profile disagreeing is precisely the defect and it was invisible in the attestation. |
| **F-403 (1)** — `preflight_probe()` was never called with a real `agent_command` anywhere in the codebase | `isolate()` now calls `preflight_probe(session, agent_command or None, agent_path=agent_path)` with the resolved command threaded from a new `--agent-command` on the `isolate` CLI, writes `control/probes/preflight.log`, and raises (exit `4`) with the log's first 600 bytes when it fails. G.5 always said the pre-flight ran the launch line; no caller ever made that true. |
| **F-403 (2)** — `orca_check_probe()` had no caller anywhere, so O-1 was assumed rather than asserted | `isolate()` calls it **whenever a dispatch terminal handle is supplied** — with no handle there is no dispatch to check and inventing one would prove nothing — writes `control/probes/orca_check.log`, and a non-zero rc is exit `4`. |
| **F-403 (3)** — the session HOME had no attested way to receive agent credentials before the readable-set scan ran | DESIGN's D-6.8/D-6.9 mechanism, built in full: `SeedSource`/`SeededRecord` (both `@dataclass(frozen=True)`), `_open_no_follow()`, `read_seed_sources()`, `place_seed_sources()`, `seed_session_home()`, `inventory_session_home()`, `attest_seeds()`, `assert_home_scanned()`, `assert_agent_path_admitted()`, the S-1…S-8 and D-1…D-4 refusals, the five caps, the `os.supports_dir_fd` guard, `--seed`/`--agent-path` on the CLI, and `ISOLATION_SCHEMA_VERSION = "1.1"`. |

**`shutil.copyfile` and `sha256_path` appear nowhere on the seed path**, which is the whole of
F-001's structural answer and is asserted by a test that reads the module's own source between
`read_seed_sources()` and `inventory_session_home()`.

### Changes

#### Step 1 — `scripts/review_isolation.py` — **HARD**

**F-401.** `scan_readable_set()` gains an `lstat()`-based `S_ISREG` gate ahead of passes B, C and D
and a `counters["non_regular"]` field; the docstring states the policy once, generally, with the
measured reason (`/dev`, 459 devices, the 17m44s SIGKILL) and the forward reason (the pre-flight's
own agent writes into `<SESSION>/home`, the operator's real `$CODEX_HOME` contains a unix socket,
and NEG-5 re-scans that root).

**F-402.** `build_session()` returns `_realpath(session)`; `isolate()`'s `writable` list is built
from `_realpath(session / …)` for all three roots.

**The seed mechanism (D-6.1 … D-6.9).**

* Constants: `MAX_SEEDS = 8`, `MAX_SEED_BYTES = 1 MiB`, `MAX_SEED_TOTAL_BYTES = 4 MiB`,
  `MAX_HOME_INVENTORY = 1024`, `MAX_KEY_INODES = 4096`; `SEED_POLICY_STATEMENT`,
  `SEED_DIGEST_LIMITATION`, `SEED_ARCHIVE_SUFFIXES`, `SEED_REFUSED_COMPONENTS`,
  `_NO_FOLLOW_DIR_FLAGS`, `_NO_FOLLOW_FILE_FLAGS`.
* `IsolationSeedGrammarError(final_review_eval.EvalInputError)` — the argument-grammar failures of
  D-6.1/D-6.8, which are exit `1` because nothing is built and there is nothing to remove. Every
  other refusal is `IsolationError`, i.e. exit `4`.
* `SeedSource(dest, source, data, sha256)` and `SeededRecord(dest, source, seeded_bytes,
  seeded_sha256, seeded_mode)`, both frozen. `SeedSource` carries **no absolute source pathname**:
  `source` is already `_path_field()`-redacted, so `place_seed_sources()` is not handed a value it
  could pass to `open`, `stat`, `copyfile` or `sha256_path`.
* `_assert_dir_fd_support()` — a loud exit-`4` refusal rather than a silent pathname fallback,
  because the fallback is F-001.
* `_open_no_follow(abs_path) -> (fd, parts)` — component-by-component `O_NOFOLLOW` walk from `/`,
  at most two descriptors held, `os.close()` in a `finally:`. Returns the literal component
  sequence S-3 and S-6 are then decided over.
* `_key_bearing_inodes(fixture)` — S-8's `(st_dev, st_ino)` set over `answer_key.json` and every
  regular file under the fixture's `key/` and `adjudications/`; **2 entries measured** on this host,
  capped at `MAX_KEY_INODES` and fail-closed above it. Collected only when at least one `--seed` is
  declared.
* `_read_to_ceiling(fd, ceiling)` — the read that actually enforces S-2, because `st_size` is
  advisory.
* `read_seed_sources()` — phase 1, steps 1-12 of D-6.8's table in that order, every pair completed
  before phase 2 runs at all. No post-read `fstat` identity re-check, deliberately: a file can be
  replaced and restored between two `fstat`s, so that comparison cannot fail closed.
* `place_seed_sources()` — phase 2: `mkdir(0o700, dir_fd=)` + `O_DIRECTORY|O_NOFOLLOW` re-open per
  parent, `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` at `0o600`, `os.write` to completion, `os.fchmod` on
  the **descriptor**.
* `seed_session_home()` — the two-line composition, called from `build_session()` immediately after
  `(session / "home").mkdir()` and inside the existing `try:` whose `except BaseException:` removes
  a half-built session (D-6.3's call site).
* `inventory_session_home(session)` — D-6.9's **single reader**: one `lstat`-only walk, one read per
  regular file through `O_RDONLY|O_NOFOLLOW` from the walk's own directory descriptor, a
  non-regular entry recorded with `kind` and **no digest and no open**.
* `attest_seeds(manifest, inventory)` — replaces the designed-then-superseded
  `assert_seeds_present()`. Fills `observed_*` from the inventory entry, derives `state`, stamps
  `origin: "seed"` and the three counters, and exits `4` on a declared `dest` that is absent or not
  a regular file. It is handed frozen records, so it cannot recompute or overwrite the as-copied
  side.
* `assert_home_scanned(session, readable, probes)` and
  `assert_agent_path_admitted(agent_path, readable)` — D-6.3's and D-6.6's contract assertions.
* `wrap_command(session, command, agent_path=())` adds `PATH` **only** when `--agent-path` was
  given; with none, the launch line is byte-identical to what it was before the parameter existed
  (asserted by a test).
* `build_attestation(..., session_home=None)` emits the `session_home` object when and only when
  something was seeded, and appends `SEED_DIGEST_LIMITATION` to `limitations[]` in that case.
  `ISOLATION_SCHEMA_VERSION = "1.1"`.
* `isolate(..., seed=(), agent_path=(), agent_command="", orca="orca")`, with the call order
  D-6.9 requires: `assert_home_scanned()` → `inventory_session_home()` → `attest_seeds()` →
  `build_attestation()`.

#### Step 2 — `scripts/final_review_eval.py` — **HARD**

`--seed` (repeatable, `metavar="ABS_SOURCE:HOME_RELATIVE_DEST"`, help text carrying the closed
refusal list and the low-entropy-secret caveat in DESIGN iteration 3's wording — *"the seeded and
observed digests"*), `--agent-path` (repeatable) and `--agent-command`, all defaulting to empty and
all threaded into `_dispatch_isolate()`.

#### Step 3 — `scripts/test_review_isolation.py` — **HARD**

33 new tests in seven classes (31 for steps 1-3, plus the 2 `SeedCliExitCodeTests` that
Finding **F-503** added). See `## Unit Tests`.

#### Step 4 — docs

`CHANGELOG.md` gains one `Unreleased → Added` entry stating the `--seed`/`--agent-path`/
`--agent-command` contract, the single-descriptor read contract, the closed refusal list including
S-8's hard-link rule, the two-identity seed record and the `1.0 → 1.1` bump, and the
low-entropy-secret residual. `COMPATIBILITY.md` is untouched, as DESIGN requires.

### Modified Files

| file | step |
|---|---|
| `scripts/review_isolation.py` | 1 (+ F-401, F-402) |
| `scripts/final_review_eval.py` | 2 |
| `scripts/test_review_isolation.py` | 3 |
| `CHANGELOG.md` | 4 |
| `artifacts/runs/run_75c5c6046f35/IMPLEMENTATION.md` | this Worker Result |

`VERSION`, `LICENSE-DECISION.md`, `COMPATIBILITY.md`, `scripts/run_logging.py` and its byte-identical
mirror, the fixture trees, the answer key, the scorer, the adjudication schema, the redaction policy,
`release_manifest.py`, the detection/search policy and the H-1/H-2/H-4/H-5 conclusions are all
untouched. No branch and no PR was created; the work is committed on
`agent/final-review-observability-evaluation`.

### Unit Tests

**Added — `scripts/test_review_isolation.py`**

| DESIGN id | test | class |
|---|---|---|
| T-10.1 | `test_t101_a_valid_pair_lands_with_both_identities_and_the_designed_modes` | `SeedPlacementTests` |
| T-10.1 (`O_EXCL`) | `test_t101b_the_destination_is_created_by_o_excl` | `SeedPlacementTests` |
| T-10.7 | `test_t107_the_per_source_total_and_count_caps_all_bind` | `SeedPlacementTests` |
| T-10.8 | `test_t108_validate_all_then_copy_leaves_neither_pair_behind` | `SeedPlacementTests` |
| T-10.2 | `test_t102_a_directory_a_symlink_and_a_fifo_are_each_refused` | `SeedSourceRefusalTests` |
| T-10.3 | `test_t103_the_repository_the_fixture_and_key_names_are_each_refused` | `SeedSourceRefusalTests` |
| T-10.4 | `test_t104_a_source_carrying_key_vocabulary_is_refused_before_the_copy` | `SeedSourceRefusalTests` |
| T-10.5 | `test_t105_an_executable_an_archive_and_a_non_utf8_source_are_refused` | `SeedSourceRefusalTests` |
| T-10.6 | `test_t106_every_refused_destination_form_is_refused` | `SeedDestinationRefusalTests` |
| T-10.6 (grammar) | `test_t106b_the_argument_grammar_is_exit_one_not_exit_four` | `SeedDestinationRefusalTests` |
| T-10.13 | `test_t1013_a_substitution_between_the_phases_changes_nothing_observable` | `SeedSubstitutionRaceTests` |
| T-10.14 | `test_t1014_a_substitution_can_never_bypass_a_refusal` | `SeedSubstitutionRaceTests` |
| T-10.15 | `test_t1015_the_walk_decides_over_components_not_over_a_pathname` | `SeedSubstitutionRaceTests` |
| T-10.16 | `test_t1016_s8_refuses_a_hard_link_alias_of_key_material` | `SeedSubstitutionRaceTests` |
| T-10.17 | `test_t1017_the_by_construction_guarantees_hold` | `SeedSubstitutionRaceTests` |
| D-6.8 (by inspection) | `test_the_seed_path_never_names_copyfile_or_sha256_path` | `SeedSubstitutionRaceTests` |
| T-10.9 | `test_t109_the_session_home_is_not_exempt_from_the_admission_scan` | `SeedAttestationTests` |
| T-10.10 | `test_t1010_the_record_shape_is_the_two_identity_one` | `SeedAttestationTests` |
| T-10.11 | `test_t1011_the_inventory_is_the_single_reader_and_opens_nothing_it_should_not` | `SeedAttestationTests` |
| T-10.11 (`tree_digest`) | `test_t1011b_the_tree_digest_is_stable_across_two_identical_sessions` | `SeedAttestationTests` |
| T-10.18 | `test_t1018_a_modified_seed_keeps_both_identities` | `SeedAttestationTests` |
| T-10.19 | `test_t1019_the_unmodified_case_and_the_immutability_guard` | `SeedAttestationTests` |
| T-10.12 | `test_t1012_an_unadmitted_entry_is_refused_and_an_admitted_one_leads_path` | `AgentPathTests` |
| T-10.12 (control) | `test_the_launch_line_is_byte_identical_with_no_agent_path` | `AgentPathTests` |
| **F-402** | `test_build_session_returns_a_resolved_path` | `WritableSetSpellingTests` |
| **F-402** | `test_the_readable_and_writable_spellings_of_one_root_agree` | `WritableSetSpellingTests` |
| **F-402** | `test_f402_the_generated_profile_actually_permits_a_write` | `NegativeContractTests` |
| **F-402** | `test_f402b_every_writable_root_is_denied_outside_the_session` | `NegativeContractTests` |
| **F-401** | `test_a_fifo_under_an_admitted_root_is_counted_and_not_opened` | `NonRegularScanTests` |
| **F-401** | `test_a_non_regular_entry_named_like_an_archive_is_not_handed_to_tarfile` | `NonRegularScanTests` |
| **F-401** | `test_the_production_entry_point_terminates_on_a_non_regular_entry` | `NonRegularScanTests` |
| **F-503** | `test_a_grammar_failure_is_exit_one_with_a_message_not_a_traceback` | `SeedCliExitCodeTests` |
| **F-503** | `test_a_refused_source_is_exit_four_with_no_session_left` | `SeedCliExitCodeTests` |

#### Behaviour covered, and why each test is shaped the way it is

* **The race tests are deterministic.** The substitution happens between two ordinary function calls
  in the test body — no threads, no timing, no retries — which is only possible because D-6.8 made
  phases 1 and 2 two public functions. The seam the tests drive is the seam that ships; a test-only
  hook would have been a different code path from the one that runs.
* **T-10.13 asserts the outcome, not the exception.** For each of the four substitutions the
  destination's bytes are byte-identical to the *original* source, its digest equals the returned
  `seeded_sha256`, and **no byte of the answer key and no key token appears anywhere under
  `<SESSION>/home`** — the assertion F-001 is actually about.
* **T-10.16 asserts S-4 and S-8 independently**, each with the other disabled at the unit level
  (`_key_bearing_inodes` stubbed to the empty set for the S-4 half; `_answer_key_digest` and
  `key_leak_tokens` stubbed for the S-8 half), plus the case S-4 alone would pass — a hard link to a
  non-key regular file under `key/` carrying no key vocabulary, with the "S-4 would pass this"
  control asserted in the same method.
* **F-401's production-entry-point test runs in a subprocess with `timeout=180`**, so a regression
  fails loudly instead of hanging the suite forever. `/dev/zero` cannot be reproduced without root;
  a FIFO reproduces the same defect exactly (`read_text()` on one never returns) and the fix is the
  same general policy for both.
* **F-402's integration test runs the real launch line against a real generated profile** and looks
  at the filesystem afterwards, in `NegativeContractTests` where a real session, a real readable set
  and a real profile already exist. A literal assertion over the profile *text* would have passed
  against the defective profile, which is exactly why it is not one — paired, as every negative
  assertion in that class is, with the inverse: a write to the host temp directory outside the
  session is still denied.
* **Seed sources live under the realpath of the temporary directory.** `tempfile.mkdtemp()` hands
  back `/var/folders/…` on darwin and `/var` is a symlink, so an unresolved source path is refused
  by the no-follow walk at component 0. That is D-6.8 working as specified, not a test
  inconvenience, and it is recorded here because an operator will meet it (see **N-4**).

### End-to-End Proof — the loop `TEST.md`'s `B-1′` attempt could not close

This is the load-bearing evidence of the iteration. F-401, F-402 and F-403 are not three
independently plausible patches: the mechanism is only fixed if a real, seeded, kernel-enforced
`isolate` run **terminates** and writes a **valid** `ISOLATION.json`, which had never happened.

**The command, run exactly as an operator would run it** (a synthetic `auth.json`-shaped seed — no
real credential is needed to prove the mechanism, and the pre-flight's real-agent authentication
remains `B-1′`'s own step):

```bash
python3 scripts/final_review_eval.py isolate \
    --run-id run_e2e_seed_proof \
    --seed <SCRATCH>/e2e/auth.json:.codex/auth.json \
    --agent-command "/bin/echo AGENT-STARTED-OK" \
    --enforcement seatbelt \
    --out <SCRATCH>/e2e/isolate_result.json
```

`rc=0`, empty stderr, wall clock **~14 minutes**. The `TEST.md` attempt this replaces was SIGKILLed
at **17m44s** while blocked in `read()` on `/dev/console`, having produced nothing.

**F-401 — the scan terminates, and the record says why.** `probes[NEG-5].roots[]`:

| class | root | `content_scanned` | hits |
|---|---|---|---|
| IMM | `/bin` | 0 | 0 |
| IMM | `/sbin` | 0 | 0 |
| IMM | `/private/etc` | 214 | 0 |
| IMM | **`/dev`** | **0** | 0 |
| IMM | `/private/var/select` | 0 | 0 |
| IMM | `/usr` | 11,027 | 0 |
| IMM | `/System` | 77,256 | 0 |
| IMM | `/Library/Developer/CommandLineTools` | 94,472 | 0 |
| USR | `<SESSION>/review_root` | 16 | 0 |
| USR | `<SESSION>/tmp` | 0 | 0 |
| USR | **`<SESSION>/home`** | 1 | 0 |

**182,986 files opened and content-scanned in total, and `/dev` contributes exactly zero of them** —
every one of its entries is a character or block device and the `S_ISREG` gate skipped all of them
while still counting them. That single `0` is the finding, closed, measured at the production entry
point rather than argued about: the mandatory pass B of D-5.1 ran in full over every admitted root
*including* the one that used to hang it.

**F-402 — the writable set and the profile agree.** `build_session()` returned the resolved session
path, and the emitted launch line spells `TMPDIR`, `HOME` and the profile path with the same
`/private/var/folders/...` prefix the read clauses use. The pre-flight then **wrote inside the
session for real** — the `xcrun` shims created `<SESSION>/home/Library/Caches/com.apple.python/...`,
34 files of it, which the inventory enumerates below. A profile whose write clause disagreed with
its read clause could not have produced a single one of them.

**F-403 (1) — the pre-flight ran the actual agent command under the actual profile.**
`control/probes/preflight.log`, verbatim tail:

```text
$ /bin/ls .
rc=0
artifacts
policy
subject


$ /bin/echo AGENT-STARTED-OK
rc=0
AGENT-STARTED-OK
```

The fifth entry exists only because `--agent-command` was threaded through to
`preflight_probe(session, agent_command, agent_path=…)`. Before this iteration the log had four
entries on every capture and the agent command was never executed at all.

**F-403 (2) — O-1.** No `--terminal` was supplied on this run, so `orca_check_probe()` correctly did
not run: with no dispatch handle there is no dispatch to check, and inventing one would prove
nothing. The caller now exists, is exercised by the `--terminal` path, and O-1 stays open and
undischarged exactly as DESIGN says it does — it is discharged by a capture that supplies a real
handle, not by this proof.

**F-403 (3) — the seed, attested at both ends of the window.** `ISOLATION.json`:

```json
"schema_version": "1.1",
"scope_enforcement": "seatbelt",
"properties": {"S1": "PASS", "S2": "PASS", "S3": "PASS"},
"session_home": {
  "seeded": [{
    "dest": "home/.codex/auth.json",
    "source": "<REDACTED:foreign_absolute_path>",
    "seeded_bytes": 319,
    "seeded_sha256": "sha256:28fe5058751280777ccf9abffd6125597d2803de9925889a74cafb3730498e8f",
    "seeded_mode": "0600",
    "observed_bytes": 319,
    "observed_sha256": "sha256:28fe5058751280777ccf9abffd6125597d2803de9925889a74cafb3730498e8f",
    "observed_mode": "0600",
    "state": "unmodified"
  }],
  "inventory": {
    "files": 35, "bytes": 421463,
    "tree_digest": "sha256:3e1a20e2a5cd65bf6268f5183ac9137f51dc5db232e7963e5810e76e595e742f",
    "seeded_unmodified": 1, "seeded_modified": 0, "unseeded": 34, "truncated": false
  },
  "scanned_by": ["compute_readable_set:USR", "NEG-5"]
}
```

Every claim D-6.4 and D-6.9 make about this document is true of it:

* the seed landed at `home/.codex/auth.json` at mode `0600` under a `0700` parent, and is the one
  entry whose `origin` is `"seed"`;
* the **34 other files** are what the pre-flight's own processes wrote into the session HOME after
  the admission scan and before the attestation — enumerated with digests, each `origin: "session"`,
  which is the honest statement (present at attestation time and not in the seed manifest) rather
  than a guess about who wrote them;
* `inventory.entries["home/.codex/auth.json"].sha256` **is** `seeded[0].observed_sha256`, the same
  string, because there is one walk and one read;
* `assert_home_scanned()` passed: `<SESSION>/home` is a scanned Class USR root in `readable_set[]`
  **and** carries its own NEG-5 per-root record (`passes: [A,B,C,D,S]`, `vocabulary: key_leak`,
  `content_scanned: 1`, `hits: 0`) — the seed was scanned by both gates, with the full key
  vocabulary, in the session it actually runs in;
* every path-bearing field passes P-PATH (`writable_set[]` and `seeded[].source` are the redacted
  placeholder; `dest` and `inventory.entries[].path` are session-relative);
* `assert_no_clock_value()` passed — the document carries no clock value;
* `limitations[]` has **two** entries: G.6's privileged-writer sentence, and the digest-as-verifier
  residual, present because and only because something was seeded;
* **NEG-0 … NEG-8 all PASS**, so S1, S2 and S3 are three independent `PASS` verdicts.

**What this proof does not claim.** It is not the §7 baseline. It used a synthetic seed and a
trivial `--agent-command`, so it does not show the project's real Final Review agent
*authenticating* — that is `B-1′`'s own mandatory step and its `preflight.log` is its own evidence.
It also did not exercise `--agent-path`/`PATH` (unit-covered by T-10.12) or `--terminal`/O-1. What
it does establish, which nothing before it did, is that **the production `isolate` path runs to
completion with a seed in it and emits an attestation that satisfies every contract assertion the
design places on it.**

The attestation and the pre-flight log were kept for inspection at
`<SESSION>/control/ISOLATION.json` and `<SESSION>/control/probes/preflight.log`, where `<SESSION>`
is the path in `isolate_result.json`. The session was **not** torn down, so a reviewer can re-read
both.

### Additional Validation

The full suite as the dispatch names it, plus the three companion gates.

| gate | result |
|---|---|
| `python3 scripts/validate_skills.py` | **PASSED** (463 checks) |
| `python3 -m unittest discover -s scripts -p 'test_*.py'` | **Ran 1167 tests in 742.6s — 2 failures, 6 skipped.** Both failures are `test_run_logging.RetainedReportWhitespaceExemptionTests` (`test_the_whitespace_gate_passes_over_the_whole_os22_range`, `test_the_gate_fails_again_once_the_exemption_is_removed`), which run `git diff --check` over the OS-22 commit range and find trailing whitespace in **committed review artifacts of other Runs** (`run_4d1c47c838db/REVIEW_DESIGN_iteration1.md`, `…/REVIEW_DESIGN_iteration2.md`, `run_75c5c6046f35/REVIEW_DESIGN_iteration2.md`, `…/REVIEW_TEST_iteration1.md`) — no file any step of this iteration touches. See **N-5**. |
| `python3 scripts/verify_package.py` | **PASSED** (109 source files) |
| `cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py` | **identical** — no `run_logging.py` change was needed or made, so the byte-parity mirror is untouched |
| `git diff --check` (working tree) | **exit 0**, clean |

**The baseline this is measured against.** The same suite on the tree as it stood before this
iteration's test file changed: **Ran 1134 tests, 2 failures** — the same two. This iteration adds
**33 tests** (1134 → 1167) and **no new failure**: every test that passed before still passes, and
every test added here passes.

Two intermediate runs are recorded for completeness, because each one is a checkpoint the numbers
should be reconcilable against: 1134 (baseline, before the T-10 block), 1165 (after T-10.1…T-10.19,
the F-401 and F-402 tests), 1167 (after `SeedCliExitCodeTests`, which F-503 added).

**Beyond the required suite**, each of these was executed rather than reasoned about:

* the **seeded end-to-end capture** of `## End-to-End Proof` — `rc=0`, a valid `ISOLATION.json`,
  NEG-0…NEG-8 all `PASS`, 182,986 files content-scanned, `/dev` contributing `0`;
* the CLI's two exit codes at the production entry point — `rc=1` with `input error:` and no
  traceback for a `--seed` grammar failure, `rc=4` with `isolation failure:` and no session left
  behind for a refused source (both now also unit tests);
* the FIFO/socket/character-device open behaviour of F-501's flag set, measured with a 1-second
  alarm before the flag was chosen;
* `assert_retained_path_field()` over the six destination forms of F-502, measured before the rule
  was added.

### Findings

Three findings and two notes. All three findings are places where **DESIGN's text could not be
implemented exactly as written**; in each case the code implements the smallest correction that
preserves the property DESIGN itself states, and the deviation is reported here rather than folded
into the diff. None of them touches a design *decision* — each is about the mechanism DESIGN
specifies to enforce a rule it already decided, and each was found by running the finished code
rather than by reading it.

#### F-501 — D-6.8's flag set makes S-1's FIFO refusal unbounded, contradicting its own T-10.2

* **DESIGN says** (D-6.8, the no-follow walk, step 3): the final component is opened
  `os.open(base, O_RDONLY | O_NOFOLLOW | O_CLOEXEC, dir_fd=dfd)`, and (the per-pair table, step 2)
  **S-1** is then decided from `stat.S_ISREG(os.fstat(fd).st_mode)`, which is what "refuses a
  directory, FIFO, socket, character or block device".
* **Why it cannot work as written.** S-1's refusal cannot happen until the descriptor exists, and
  `os.open(<fifo>, O_RDONLY)` with no writer **blocks forever**. Measured on this host
  (python 3.11, macOS 26.5.2 arm64), with a 1-second alarm:

  ```text
  os.open(<fifo>, O_RDONLY|O_NOFOLLOW|O_CLOEXEC)              -> BLOCKED, still blocked at 1s
  os.open(<fifo>, O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK)   -> ok in 0.000s, fstat -> prw-r--r--
  os.open(<unix socket>, ...|O_NONBLOCK)                      -> OSError EOPNOTSUPP
  os.open("/dev/zero", ...|O_NONBLOCK)                        -> ok, fstat -> crw-rw-rw-
  os.open(<regular file>, ...|O_NONBLOCK)                     -> ok, contents read normally
  ```

  So the flag set DESIGN names refuses a FIFO only in **unbounded** time — which is F-401's defect
  class reproduced at the seed door, and is exactly what the *same iteration's* **T-10.2** requires
  be bounded (*"the FIFO bounded-time case is unchanged"*). D-6.8's measured-primitives block did
  not include a FIFO open, which is why the inconsistency was not visible at design time.
* **What was implemented**, and it is the smallest change that satisfies both DESIGN statements:
  `_NO_FOLLOW_FILE_FLAGS` is `O_RDONLY | O_NOFOLLOW | O_CLOEXEC | **O_NONBLOCK**`. The open returns
  immediately, `fstat()` reports `S_ISFIFO`, and S-1 refuses it — the refusal DESIGN specifies,
  reached in bounded time. `O_NONBLOCK` is a no-op on a regular file, which is the only kind of
  source that survives S-1 and reaches the read, so nothing else about D-6.8 changes. A unix socket
  is refused one step earlier, by `EOPNOTSUPP` at the open, which is still exit `4` and still
  "session removed"; its message was reworded from *"without following a symlink"* to *"as a
  regular file without following a symlink"* so it does not misdescribe that case.
* **Asserted, not asserted-about:** `T-10.2` now wraps the FIFO case in a wall-clock bound, and
  `NonRegularScanTests` carries the same shape at the other end (`inventory_session_home()` and
  `scan_readable_set()`), so a future edit that drops the flag fails a test instead of hanging a
  capture.
* **No design decision is affected.** S-1's rule, its evidence source (`fstat` on the one
  descriptor) and its exit code are all exactly as DESIGN wrote them.

#### F-502 — D-1 does not forbid an absolute `--seed` destination, and phase 2 then raised a raw `OSError`

* **DESIGN says** (D-6.2, rule **D-1**): `run_logging.assert_retained_path_field("home/" + dest)`
  *"already forbids a leading `/`, a drive letter, any `..` component, whitespace, `<`, `>`, `\` and
  any URL form — so D-1 subsumes the entire traversal-escape check"*.
* **Why it is not true for this input.** The field D-1 validates is the **concatenation**, so
  `dest = "/abs"` is validated as `"home//abs"` — which has no leading `/`. Measured directly:

  ```text
  assert_retained_path_field("home//abs")       -> ACCEPTED
  assert_retained_path_field("home/../escape")  -> refused
  assert_retained_path_field("home/a b/x")      -> refused
  assert_retained_path_field("home/x\\y")       -> refused
  assert_retained_path_field("home/<x>")        -> refused
  ```

  Every other form DESIGN names *is* caught. The absolute one is not, and it then reached phase 2,
  where `dest.split("/")` yields an empty first component and
  `os.mkdir("", 0o700, dir_fd=parent_fd)` raised a bare `FileNotFoundError` **out of the seed
  path** — an uncaught `OSError` instead of a refusal, i.e. not fail-closed in the way D-6.2
  requires ("Every violation is exit 4").
* **What was implemented:** `_validate_seed_destination()` refuses a destination with an empty,
  `.` or `..` component, before D-2's component check, as `IsolationError` (exit `4`, the code
  D-6.2 assigns to every D-rule violation). This is the **same grammar D-6.8 already requires of a
  source**, applied to the destination; it adds no rule DESIGN did not already intend and removes
  none.
* **Asserted:** `T-10.6` now includes `/abs` among the refused destination forms, alongside
  `../escape`, `a b/x`, `key/x`, `subject/x`, `adjudications/x`, `answer_key.json`, `x\y`, `<x>` and
  a duplicate destination, and asserts no partial session is left behind.

#### F-503 — the seed grammar's exit 1 arrived as an uncaught traceback, not as a message

* **DESIGN says** (D-6.1, and D-6.8's two grammar additions): an argument-grammar failure is exit
  `1` *"with the offending argument printed"*, consistent with every other grammar failure.
* **What actually happened** on the first CLI run of the finished mechanism:

  ```text
  $ python3 scripts/final_review_eval.py isolate --run-id run_grammar --seed /no/colon/here …
  Traceback (most recent call last):
    …
  review_isolation.IsolationSeedGrammarError: --seed takes exactly one ':' …
  rc=1
  ```

  The **code** was right and the **path** was not: `IsolationSeedGrammarError` subclasses
  `final_review_eval.EvalInputError` as `review_isolation` sees it, but `final_review_eval.py` runs
  as `__main__` and `review_isolation` does its own `import final_review_eval`, so there are two
  `EvalInputError` classes and `main()`'s `except EvalInputError` does not catch this one. Exit `1`
  came from CPython's uncaught-exception default, which is not a contract — any future change to
  the exception's base class would silently move it.
* **What was implemented:** `_dispatch_isolate()` catches `review_isolation.IsolationSeedGrammarError`
  explicitly, ahead of the two `IsolationError` clauses, and returns `EXIT_INPUT_ERROR` after
  printing `input error: <message>` in `main()`'s own format. Verified: `rc=1`, the message on
  stderr, no traceback.
* **Asserted at the production entry point:** `SeedCliExitCodeTests` runs the real CLI and asserts
  exit `1` with `input error:` and **no** `Traceback` for a grammar failure, and exit `4` with
  `isolation failure:` and no session left behind for a refused source. An in-process
  `assertRaises` proves which exception is raised, not which code an operator sees.

#### N-4 — on darwin a `--seed` source must be given in its **resolved** spelling, and that is D-6.8 working

`/var`, `/tmp` and `/etc` are symlinks on darwin, so `--seed /var/folders/…/auth.json:…` is refused
at component 0 with `ELOOP` and `--seed /private/var/folders/…/auth.json:…` is accepted. That is
D-6.8's contract behaving exactly as specified — the walk proves every component is not a symlink,
which is what makes S-3's lexical containment sound — but it is a real operator-facing consequence
that DESIGN does not spell out anywhere, and an operator meeting it for the first time will read it
as a bug. It is recorded here, in the `--seed` refusal list in `CHANGELOG.md`, and in the docstring
of the seed test base class. The measured real credential path
(`/Users/<user>/.codex/auth.json`) is unaffected: `/Users` is a **firmlink**, not a symlink, and the
walk opens it normally.

#### N-5 — the two suite failures are pre-existing and are not this change's

`python3 -m unittest discover -s scripts -p 'test_*.py'` finishes with **2 failures**, both
`RetainedReportWhitespaceExemptionTests` running `git diff --check` over the OS-22 commit range and
finding trailing whitespace in **committed review artifacts of other Runs** — this iteration's
output includes `artifacts/runs/run_75c5c6046f35/REVIEW_TEST_iteration1.md`, a file no step of this
iteration touches. They are the same two failures iteration 1 recorded as **F-302**, they fail
identically on the tree as it stood before this change, and `git diff --check` over the **working
tree** is clean (exit 0). Reported rather than fixed, for F-302's unchanged reason: rewriting
another Run's committed review artifact to satisfy a whitespace gate would edit evidence.

#### N-6 — two small shape choices, named so they are not mistaken for drift

* **`assert_home_scanned(session, readable, probes)`** rather than DESIGN's data-flow spelling
  `assert_home_scanned(readable)`. The requirement D-6.3 states has two halves — the root must be a
  scanned Class USR entry **and** must appear in NEG-5's per-root record — and neither the root's
  path nor the probe list is derivable from `readable` alone. The pseudo-code spelling is
  illustrative; both checks are implemented, and the NEG-5 half is skipped only when NEG-5 did not
  run at all (`--enforcement none`), never when it ran and failed.
* **`counters["non_regular"]` is not surfaced in the NEG-5 probe record.** F-401's fix returns it
  from `scan_readable_set()` and it is asserted in `NonRegularScanTests`, but the attestation's
  per-root record carries the correlated `content_scanned` only, which is the shape DESIGN's
  iteration-2 *Risks* section describes and which this iteration did not reopen. Over `/dev` it
  reads `0`, which is the same fact from the other side (see `## End-to-End Proof`). Adding the
  counter to `probes[NEG-5].roots[]` is a one-line additive change and would be a reasonable next
  increment; it was deliberately **not** made here, because it would have invalidated the
  end-to-end attestation this iteration produced as evidence and required a second 14-minute
  capture for a cosmetic gain.
* **`place_seed_sources()` tolerates `FileExistsError` from `os.mkdir`** on an intermediate
  directory, and D-3's *"refuses to descend through anything it did not create"* still holds: the
  session HOME is created empty by `build_session()` immediately above, seeding is the only thing
  that writes into it before the scan, so the only pre-existing intermediate is one an **earlier
  pair in the same call** created — which is what lets two pairs share `.codex/`. Anything else
  fails the `O_DIRECTORY|O_NOFOLLOW` re-open, which is what actually enforces the rule.
