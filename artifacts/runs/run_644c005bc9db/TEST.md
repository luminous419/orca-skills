# Worker Result

STATUS: COMPLETE

Run: `run_644c005bc9db` · phase TEST · iteration 1 (fresh follow-up Run, fresh budget)
Branch: `agent/final-review-observability-evaluation` (Draft PR #20) — no new branch, no new PR
Predecessor Run `run_804e35d29531` remains ESCALATED and was **not** modified, with the single
exception the task explicitly directs: a forward-pointing supersession notice prepended to its
`BASELINE_RESULT.md`. No file under `artifacts/runs/run_804e35d29531/` other than that one was
touched.

**What this Run exists to do:** resolve finding **R6**
(`artifacts/runs/run_804e35d29531/FINAL_REVIEW_iteration3.md`) by re-executing DESIGN's B-1…B-5
§7 baseline procedure under the `redaction/1.1` writer already merged on this branch, and making
the new capture the authoritative current baseline.

**New baseline capture:** Orca Run `run_5967188007ce`, Task `task_94d64c838578`,
Dispatch `ctx_2b0c56074844`, agent `codex-sol`, provenance `accepted`, settlement `settled`.

---

## Test Scope / Existing Test Assessment

This is a **verification** round, not a test-authoring round. DESIGN's B3 acceptance condition is
stated over a *concrete captured artifact family*, not over code, so no unit test can discharge it:
`scripts/test_run_logging.py` already proves the `redaction/1.1` policy, the P-PATH ladder and its
fail-closed postcondition on synthetic inputs (T-1/T-3), and those tests passed both before and
after this round. What they cannot prove is that the **committed baseline evidence** was produced by
that policy — which is exactly the gap R6 identified: `1.0`-era bytes on disk under a `1.1`-capable
writer.

So the scope here is:

| # | what had to be verified | how |
|---|---|---|
| 1 | a fresh neutral baseline can be captured end-to-end under `redaction/1.1` | execute B-1…B-5 in a new Run against a scratch workspace outside the repository |
| 2 | the retained family is environment-safe | B3's exact grep condition, plus an **independently re-implemented** P-PATH classifier and digest recomputation |
| 3 | the prompt is still neutral | byte-comparison against the R2-corrected prompt, plus both leak scanners at the `prompt` profile, run **before** dispatch and again on the retained `input.md` |
| 4 | fixture / answer-key isolation is unaffected | `verify-fixture`, literal `scan-leak`, `semantic_leak_scan` at both profiles, and the `metric_inference` disclosure check over the union of the commit set |
| 5 | metric correctness and the refusal contract still hold | `parse-report` + `score`, the `UNADJUDICATED` default, the `REFUSED` statuses, and the `--require-precision` exit code |
| 6 | the whole repository is still green | the full validation suite named in the task |

Existing tests were assessed and **not** modified. No test gap in the code-level suite was found by
this round; the gap was in the artifact, and the artifact is what was regenerated.

## Added / Modified Tests

**None.** No test file was added or modified, and no production file was touched.

This is deliberate and is the correct outcome for this Run:

* The task's requested phase is TEST only. Its Mandatory Invariant forbids fixing production code
  here, and nothing needed fixing — the `redaction/1.1` writer behaved exactly as DESIGN C.3/C.7
  specify on a real capture (see `## Behavior Covered`).
* The code-level behavior was already covered by T-1/T-3 and by T-5a's whitespace-exemption test.
  Adding a duplicate unit test would not have closed R6, because R6 is a statement about committed
  bytes, not about a function.

What this round produces instead is **executed evidence**: a new immutable audit record family, an
evidence bundle, and the two write-ups that make the new capture authoritative.

Files written by this round:

```
artifacts/runs/run_5967188007ce/ORCHESTRATOR_LOG.md                     (new)
artifacts/runs/run_5967188007ce/TIMING_LOG.md                           (new)
artifacts/runs/run_5967188007ce/FINAL_REVIEW_EVIDENCE_BUNDLE.json       (new)
artifacts/runs/run_5967188007ce/final_review_audit/attempt1__task_94d64c838578__ctx_2b0c56074844/
    input.md · report.md · record.json                                  (new, immutable)
artifacts/runs/run_644c005bc9db/BASELINE_RESULT.md                      (new — the CURRENT baseline)
artifacts/runs/run_644c005bc9db/TEST.md                                 (new — this file)
artifacts/runs/run_804e35d29531/BASELINE_RESULT.md                      (supersession notice prepended)
```

`artifacts/runs/run_5967188007ce/.timing_state.json` is git-ignored (`.gitignore:6`) and is not part
of the commit. No scorer output is committed: `FINDINGS.json`, `METRICS.json` and
`SCORING_PROVENANCE.json` were written to a scratch directory outside the repository, per the R4
discipline.

## Behavior Covered

### 1. `redaction/1.1` applied to a real capture (R6, the reason this Run exists)

The superseded capture's `record.json` reported `redaction_policy_version: "redaction/1.0"` and
`redactions: []` while its `input.md` and `report.contract_path` carried a raw scratch path. The new
record reports:

| field | value |
|---|---|
| `stored_task_spec.redaction_policy_version` | `redaction/1.1` |
| `stored_task_spec.redactions` | `[{"category": "foreign_absolute_path", "count": 3}]` |
| `stored_task_spec.byte_length_pre_redaction` → `_post_redaction` | 4104 → 3846 (the three paths were actually removed, not merely relabelled) |
| `report.redaction_policy_version` | `redaction/1.1` |
| `report.contract_path` | `"<REDACTED:foreign_absolute_path>"` — category P4, the whole value and nothing else |
| `metadata_redaction.redaction_policy_version` | `redaction/1.1` |
| bundle `component_versions.redaction_policy` | `redaction/1.1` |

The byte-length delta is the load-bearing part: a policy stamp is a label, but 258 bytes disappearing
is the pipeline having actually done the work.

### 2. Neutrality, preserved byte-for-byte (R2 discipline)

The dispatched prompt is the R2-corrected neutral prompt with **only** the two occurrences of the
scratch workspace path changed. Verified mechanically rather than by reading: substituting the old
path back into the new prompt reproduces the superseded capture's stored spec **exactly**, and both
are 4,104 bytes pre-redaction. So no vocabulary, no weighting, no contract-section pointer, no
expected-count statement and no fixture framing was introduced or removed relative to the prompt that
was already reviewed and accepted for neutrality.

### 3. The UNADJUDICATED / closed-world-refusal contract

| assertion | observed |
|---|---|
| unmatched findings default to `UNADJUDICATED` | every unmatched finding carries `classification: "UNADJUDICATED"`; none was auto-classified as a false positive |
| `adjudication_status` | `none` |
| `closed_world` | `false`; `attested_false_positives: 0` |
| `precision`, `false_positive_rate` | `null`, both `_status` fields `REFUSED`, both `_refusal_reason` fields `adjudication_incomplete: …no independent adjudication verdict, and no closed_world exhaustive attestation is present` |
| forced computation | `score --require-precision` exits **3** and prints the refusal instead of producing a number |

Because neither of E.5 point 2's two computation paths was recorded, B2's
`precision + false_positive_rate == 1` / `unadjudicated_count == 0` conditions correctly do **not**
apply. Refusal is the pass, not a shortfall.

### 4. Determinism (B5), unqualified

`score` re-run on the identical stored findings document produced a file that is **byte-identical for
its entire content, with no excepted field** — confirmed by `cmp` and by identical SHA-256
(`b4afc7f7…`). `parse-report` re-run is likewise byte-identical. The `--provenance-out` sidecar is the
only clock-reading surface and is, by DESIGN D-002, a different artifact outside this comparison; it
was written outside the repository and is not committed.

### 5. Whitespace gate (A.6 / T-5a) on the new artifact class

This capture's `report.md` contains **zero** trailing-whitespace lines, so it does not exercise the
A.6 conflict. It is nonetheless covered: `git check-attr whitespace` reports `unset` for it, because
`.gitattributes`' glob `artifacts/runs/*/final_review_audit/**/report.md` matches the new run
directory without any change. `input.md` and `record.json` correctly report `unspecified` — they stay
fully under the default rules, exactly as A.6's scoping requires.

## Execution

Every command below was run from the repository root. `<scratch>` and `<out>` are directories outside
the repository; `<key>` is the evaluation key, whose path is deliberately not written here (R2-T1).

### B-1 — Materialize

```
Command: python3 scripts/final_review_eval.py materialize --dest <scratch>
Result: PASS — 14 files, no .git created,
        fixture_digest = sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d
        (identical to both prior captures — provably the same subject tree)

Command: python3 scripts/final_review_eval.py verify-fixture --fixture scripts/fixtures/final_review_eval
Result: PASS — "fixture verification PASSED", exit 0

Command: python3 scripts/final_review_eval.py scan-leak --key <key> --target <scratch>
Result: PASS — "leak scan PASSED", exit 0, no exclusions, before any Reviewer saw the workspace
```

### Pre-dispatch neutrality gate

```
Command: python3 scripts/final_review_eval.py scan-leak --key <key> --target <prompt>
Result: PASS — 0 hits, exit 0

Command: python3 artifacts/runs/run_92759e0e1034/tools/semantic_leak_scan.py \
             --key <key> --target <prompt> --profile prompt
Result: PASS — "semantic leak scan [prompt] PASSED (1 files scanned, 0 hits)", exit 0
```

### B-2 — Dispatch

```
Command: orca orchestration run-create --objective "<neutral objective>"
Result: PASS — run_5967188007ce

Command: orca orchestration task-create --run run_5967188007ce --spec "<neutral prompt>"
Result: PASS — task_94d64c838578

Command: orca terminal create --worktree current --command "codex-sol"
Result: PASS — reviewer terminal created (agent_origin = self_created)

Command: python3 scripts/run_logging.py timing-dispatch-start --run-id run_5967188007ce \
             --phase final_review --iteration 1
Result: PASS

Command: orca orchestration dispatch --run run_5967188007ce --task task_94d64c838578 \
             --to <reviewer terminal> --inject
Result: PASS — ctx_2b0c56074844, injected

Settlement: status=completed, failure_count=0, termination_reason=null,
            dispatched 23:22:57Z → completed 23:24:48Z.
            No dispatch-layer failure ⇒ B-3R retry loop NOT needed (third capture running).
Reviewer outcome (an observation, not a criterion): RESULT: FAIL / REVIEW_VERDICT: FAIL,
            a non-empty set of blocking findings, all MAJOR or above. The count is withheld under P-1.
```

### B-3 — Audit capture

```
Command: python3 scripts/run_logging.py final-review-audit-write --run-id run_5967188007ce --attempt 1 \
             --task-id task_94d64c838578 --dispatch-id ctx_2b0c56074844 \
             --provenance accepted --settlement settled --report-path <scratch>/REPORT.md \
             --terminal <handle> --agent-command codex-sol --agent-origin self_created --notes "<...>"
Result: PASS — wrote final_review_audit/attempt1__task_94d64c838578__ctx_2b0c56074844/
               {input.md, report.md, record.json}

Command: python3 scripts/run_logging.py final-review-audit-provenance --run-id run_5967188007ce --attempt 1
Result: PASS — accepted_dispatch_key = attempt1__task_94d64c838578__ctx_2b0c56074844,
               violations = [], unreadable = []

Command: python3 scripts/run_logging.py final-review-audit-export --run-id run_5967188007ce \
             --out artifacts/runs/run_5967188007ce/FINAL_REVIEW_EVIDENCE_BUNDLE.json
Result: PASS — integrity: records_found 1, records_ok 1, digest_mismatches [], unreadable [],
               missing_artifacts [], incomplete_publications []

Command: python3 scripts/run_logging.py run-status --run-id run_5967188007ce --status COMPLETED --reason "<...>"
Result: PASS — run_end row appended to both logs; bundle re-exported afterwards so it embeds the
               closed log
```

### Independent environment-safety verification (B3, the R6 gate)

```
Command: grep -a -c -E '<local-username>|-Users-|/private/tmp/' <each retained + exported file>
Result: PASS — 0 hits in every file:
          input.md 0 · report.md 0 · record.json 0 ·
          FINAL_REVIEW_EVIDENCE_BUNDLE.json 0 · ORCHESTRATOR_LOG.md 0 · TIMING_LOG.md 0
        Also 0 for the broader shapes: claude-501, the session UUID, the workspace basename,
        and the literal "scratchpad".

Command: <independent P-PATH classifier: DESIGN C.7's P1..P4 table re-implemented for this
         verification rather than imported from run_logging.py, applied to every string in
         record.json and in the bundle>
Result: PASS — 24 path-bearing strings examined; no value falls outside P1..P4.
        The only surviving "/Users/" prefixes are in delivery_evidence.process_incarnation, where
        category 4's readability-preserving substitution leaves
        "/Users/<REDACTED:absolute_local_path>/…". That is unchanged, DESIGN-sanctioned
        redaction/1.0 behavior that 1.1 deliberately keeps, discloses no username, and is not one
        of B3's three patterns.

Command: <recompute sha256 of the retained bytes and compare to record.json>
Result: PASS —
  input.md  recorded sha256:62ae5df5f270db0b02caba77fb2a1293e1f9637354fbeda5d2c220c29f2fcd6c
            actual   identical · byte_length 3846 recorded = 3846 actual
  report.md recorded sha256:b7d321da01c8a5e9f3b8cea00bf096f9814c8fb4ebf3e646ecd6b24fcc62dbcc
            actual   identical · byte_length 6130 recorded = 6130 actual
```

### B-4 — Scoring (separate step, after settlement)

```
Command: python3 scripts/final_review_eval.py parse-report --report <scratch>/REPORT.md \
             --workspace <scratch> --out <out>/FINDINGS.json
Result: PASS — exit 0

Command: python3 scripts/final_review_eval.py score --findings <out>/FINDINGS.json --key <key> \
             --workspace <scratch> --out <out>/METRICS.json --run-verdict FAIL \
             --provenance-out <out>/SCORING_PROVENANCE.json
Result: PASS — exit 0, full metric block emitted, precision/false_positive_rate both REFUSED

Command: python3 scripts/final_review_eval.py score … --require-precision
Result: PASS (refusal is the expected behavior) — exit 3,
        "precision refused: adjudication_incomplete: …"
```

Both ran strictly after the Dispatch had settled and `REPORT.md` existed. Both outputs went outside
the repository and are not committed.

### B-5 — Determinism

```
Command: python3 scripts/final_review_eval.py score … --out <out>/METRICS_rerun.json ; cmp METRICS.json METRICS_rerun.json
Result: PASS — byte-identical, whole file, no excepted field; both sha256 b4afc7f7…4374

Command: python3 scripts/final_review_eval.py parse-report … --out <out>/FINDINGS_rerun.json ; cmp
Result: PASS — byte-identical
```

### Answer-key isolation over the new capture

```
Command: python3 scripts/final_review_eval.py verify-fixture --fixture scripts/fixtures/final_review_eval
Result: PASS

Command: python3 scripts/final_review_eval.py scan-leak --key <key> --target <retained input.md>
Result: PASS — 0 hits (this is DESIGN B4's exact criterion)

Command: python3 …/semantic_leak_scan.py --key <key> --target <retained input.md> --profile prompt
Result: PASS — 0 hits

Command: literal scan-leak, file by file, over every committed file of this capture
Result: PASS — 0 hits everywhere

Command: semantic_leak_scan --profile evidence, file by file
Result: PASS on input.md, record.json, ORCHESTRATOR_LOG.md, TIMING_LOG.md, and on both write-ups
        this round produces (BASELINE_RESULT.md, this file).
        ONE hit on report.md, inherited by the bundle that inlines it — see OBS-2 below.

Command: semantic_leak_scan --profile evidence over the UNION of the whole committed set
         (cross-file, so the set cannot jointly disclose what no single file discloses)
Result: metric_inference — PASS, 0 hits. No combination of published numbers pins the key
        population. The only hits in the union are the two archetype_vocabulary hits of OBS-2.
```

### Full validation suite (task step 8)

```
Command: python3 scripts/validate_skills.py
Result: PASS — "Skill validation PASSED (463 checks)"

Command: python3 -m unittest discover -s scripts -p 'test_*.py'
Result: PASS — Ran 1026 tests, OK (skipped=6)

Command: python3 scripts/verify_package.py
Result: PASS — "Package verification PASSED (107 source files)"

Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
Result: PASS — byte-identical

Command: git diff --check 1045815..HEAD
Result: PASS — exit 0
```

(The suite was re-run after the commit that lands this round's artifacts; see
`## Suite re-run after the commit`.)

## Failures / Findings

**No blocking failure. No production code defect was found, and no production file was modified.**

Two observations are recorded so a reviewer does not have to re-derive them. Per the TEST template's
Mandatory Invariant and this task's item 6, neither was fixed here.

### OBS-1 — `redaction/1.1` category 5 also consumes the path after a `~` prefix

**Classification: not a defect — designed fail-closed behavior. Flagged `OUT_OF_SCOPE` for this Run.**

The neutral prompt cites the Common Review Policy at a tilde-relative location. In the retained
`input.md` that citation renders as `` `~<REDACTED:foreign_absolute_path>` ``: category 5's left
boundary is `(?<![\w>/])`, and `~` is none of those, so the absolute remainder after the tilde
matches and is replaced whole. One of the three counted `foreign_absolute_path` redactions is this
one.

This is exactly what DESIGN specifies. C.3.1's comment states that readability "is preserved only
where it is provably safe — the three home spellings above and repo-relative paths … and surrendered
everywhere else", and C.7's P-PATH rule is explicit that a shape nobody anticipated must fail
**closed**. Nothing leaks; the citation's meaning is still recoverable from the surrounding sentence.

Recorded because it is a *readability* regression a future reader of a retained input will notice,
and because restoring it would mean editing `scripts/run_logging.py` — production code, and out of
scope for a TEST-only Run. If the project wants `~`-rooted paths treated like the three home
spellings (redact the user segment, keep the readable tail), that is a MINOR-bump candidate for a
future implementation Run, alongside the Windows `C:\Users\<name>` gap the code already documents as
one. **This Run does not make that change and does not recommend making it under this budget.**

### OBS-2 — one `archetype_vocabulary` hit in the Reviewer's own `report.md`

**Classification: not a leak, and not fixable by construction. No action.**

`semantic_leak_scan --profile evidence` reports one hit on the retained `report.md` at word ~700,
inherited by the evidence bundle that inlines it. The check fires when partial key vocabulary
co-occurs within an eight-word window; here, one sentence of the Reviewer's `## Test Review`
paragraph — its summary of which contract boundaries its focused probes exercised — uses two ordinary
English words that fall inside that window for one archetype name.

Why this is not a finding:

1. **Direction.** It is in the Reviewer's *output*, written after the review, not in its input.
   The retained input is at zero hits under every check, and B4's criterion is stated over the input.
   Nothing narrowed the search — the words describe a defect the Reviewer had already found in `src/`.
2. **Content.** The archetype names are already published, unredacted, in the `ARCHETYPES` tuple in
   `scripts/final_review_eval.py` — shipped, tracked source. The report carries no entry id, no
   finding-to-entry mapping, no total, no missed-entry list and no key path. `metric_inference` over
   the union of the whole set is at zero, so nothing in the commit set solves for the population.
3. **Immutability.** `report.md` is a byte-exact snapshot of Reviewer-authored bytes, digest-bound by
   `record.json` and immutable under DESIGN A.3. Editing it to satisfy a heuristic would falsify the
   published digest and destroy the artifact's evidentiary value — the same reasoning that made
   hand-editing the `1.0` capture unacceptable in R6.

The superseded capture's `report.md` happened to return zero on this check. That was the wording an
independent agent chose, not a guarantee the contract ever made; the guarantee is over the input, and
that guarantee holds.

## Remaining Gaps

1. **Single run, single subject.** This is one dispatch against one small fixture. No detection-quality
   conclusion is drawn, and no H-1/H-2/H-4/H-5 comparison appears in any artifact this round produces.
   The Reviewer's verdict is recorded as an observation, never as a criterion.
2. **B-3R is still unexercised by a real baseline.** Three captures in a row settled cleanly, so the
   dispatch-failure retry path has never run for real. It remains covered only by T-2's synthetic
   injection. Not a regression — worth stating so nobody reads three clean captures as evidence the
   retry path works in production.
3. **The two superseded captures remain on disk with their known disclosures.**
   `run_92759e0e1034/` still holds the `redaction/1.0` scratch-path bytes R6 named, and
   `run_ff587481a820/` still holds the hinted prompt R2 named. Both are immutable and both are now
   explicitly labelled non-current at their entry points. Physically removing them to an out-of-band
   forensic archive is a Coordinator decision this Run does not take.
4. **The two Coordinator-owned arithmetic disclosures in `run_804e35d29531/` are unchanged.**
   Reported in `run_804e35d29531/TEST.md` `### Out-of-scope disclosures found by the new check`;
   still an append-only Coordinator log and three immutable digest-bound audit inputs, still not this
   phase's to rewrite.
5. **`semantic_leak_scan.py` still lives under `artifacts/runs/run_92759e0e1034/tools/`.** This round
   deliberately reused it in place rather than copying it into the new run directory: a second copy of
   a scanner is the drift R1 punished. The consequence is that a superseded run directory holds a tool
   the current baseline depends on. Relocating it is an implementation change, and so lies outside this Run's remit.

## Suite re-run after the commit

The full suite was re-run after the commit landing this round's artifacts, so `git diff --check`
covers the new files as committed:

```
Command: python3 scripts/validate_skills.py                                   Result: PASS (463 checks)
Command: python3 -m unittest discover -s scripts -p 'test_*.py'               Result: PASS (1026 tests, 6 skipped)
Command: python3 scripts/verify_package.py                                    Result: PASS (107 source files)
Command: cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py
                                                                              Result: PASS (byte-identical)
Command: git diff --check 1045815..HEAD                                       Result: PASS (exit 0)
```
