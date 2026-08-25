# OS-22 §7 Seeded-Fixture Final Review Baseline

Executed by the Coordinator after the ANALYSIS/PLAN/DESIGN/IMPLEMENTATION/TEST phase gates of
this run (`run_804e35d29531`) all passed, per PLAN.md's BASELINE work items (B-1 through B-5) and
ordering constraints 6-8. This baseline exercises the *current* Final Adversarial Review mechanism
against the seeded-defect fixture (`scripts/fixtures/final_review_eval/`) built in IMPLEMENTATION.
**No Final Review detection/search policy was changed to produce this result.**

Audit trail lives in a separate, dedicated Orca Run: `run_ff587481a820` (objective: "OS-22 §7
baseline: seeded-fixture Final Review execution (no detection/search policy change); parent
orchestration run=run_804e35d29531"). It is kept separate from `run_804e35d29531`'s own audit
trail because it evaluates a controlled fixture, not this run's real OS-22 diff — mixing the two
attempt sequences would conflate two different subjects under one run's provenance.

## B-1 — Materialize

```
python3 scripts/final_review_eval.py materialize --dest <scratch, outside this repo>
```

Result: 14 files, `fixture_digest = sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d`,
no `.git` created. Verified with `verify-fixture` (PASSED) and `scan-leak` with **no exclusions**
against the full materialized workspace (PASSED, zero hits) before any Reviewer saw it.

## B-2 — Dispatch

One fresh Final Adversarial Review attempt was dispatched against the materialized workspace, in a
newly created terminal (`codex-sol`), following this Skill's own §17/§11 Reviewer contract and
`reviews/common.md`. The dispatched Task spec gave the Reviewer only `CONTRACT.md`, `DIFF.patch`,
`src/`, and `tests/` — no answer key, no seeded-defect identity, no defect location, no expected
finding count.

Attempt 1 (Task `task_0c55cde37456`, Dispatch `ctx_33c8c8414587`) settled cleanly on the first try
— `status: completed`, no dispatch-layer failure, so **B-3R's retry loop was not needed** for this
baseline run. `RESULT: FAIL`, `REVIEW_VERDICT: FAIL`, 5 blocking findings reported in
`REPORT.md`.

## B-3 — Audit capture

```
python3 scripts/run_logging.py final-review-audit-write --run-id run_ff587481a820 --attempt 1 \
  --task-id task_0c55cde37456 --dispatch-id ctx_33c8c8414587 \
  --provenance accepted --settlement settled --report-path <REPORT.md> \
  --terminal <handle> --agent-command codex-sol --agent-origin self_created
```

Wrote an immutable per-dispatch audit record at
`artifacts/runs/run_ff587481a820/final_review_audit/attempt1__task_0c55cde37456__ctx_33c8c8414587/`
(`input.md`, `report.md`, `record.json`). `final-review-audit-provenance --attempt 1` confirms
exactly one `accepted_dispatch_key` with zero `violations`. Grepped the retained `input.md` and
`report.md` for seeded-defect IDs (`SD-\d`), "answer key", and "seeded defect" — zero hits,
confirming no key material reached the Reviewer or the retained artifacts.

Since no dispatch-layer failure occurred on this run, this baseline does not itself demonstrate
the B-3R retry path — that path is exercised separately by this run's own TEST-phase audit/failure
regression tests (T-2), which inject a synthetic dispatch failure and assert retry-under-new-identity
behavior. Real baseline provenance here is a clean `accepted` case.

## B-4 — Scoring (separate step, after Reviewer submission)

```
python3 scripts/final_review_eval.py parse-report --report REPORT.md --workspace <scratch> --out FINDINGS.json
python3 scripts/final_review_eval.py score --findings FINDINGS.json \
  --key scripts/fixtures/final_review_eval/key/answer_key.json --workspace <scratch> \
  --out METRICS.json --run-verdict FAIL
```

Both run only after the Reviewer's dispatch had already settled and `REPORT.md` existed — scoring
was not run concurrently with or before Reviewer execution, per §5's execution/scoring separation
requirement.

Metrics (`METRICS.json`, schema_version 1.0):

| metric | value |
|---|---|
| seeded_defects_total | 5 |
| detected_seeded_defects | 3 |
| seeded_recall | 0.6 (3/5) |
| miss_count / miss_rate | 2 / 0.4 |
| missed_defect_ids | SD-2, SD-4 |
| matched_findings | 3 (F-002→SD-1, F-003→SD-3, F-005→SD-5) |
| unmatched_findings | 2 (F-001, F-004) — both `UNADJUDICATED`, **not** auto-classified as false positives |
| adjudicated_true_positives / false_positives | 0 / 0 (no adjudication was performed) |
| precision / false_positive_rate | `null`, status `REFUSED` — reason: 2 unmatched findings carry no independent adjudication and no closed-world attestation is present |
| evidence_grounding | 1.0 (5/5 findings resolve to a real file, and to a real line where one was given) |
| verdict_reproducibility | `SINGLE_RUN_NOT_ASSERTED` (run_count=1 — a single run correctly does not claim reproducibility) |

This is the concrete proof of the ticket's §6 precision/false-positive rule: the scorer refused to
compute precision or FP-rate from an incompletely-adjudicated result, rather than defaulting
unmatched findings to false positives.

## B-5 — Baseline pass criteria (independent, per DEC-9)

Per PLAN.md's DEC-9, the §7 baseline is PASS only if all five of the following hold — not
inferred from detection quality:

| # | criterion | result |
|---|---|---|
| 1 | evaluation procedure actually ran | **PASS** — `materialize` → dispatch → `parse-report` → `score` all executed and exited 0 |
| 2 | scoring worked | **PASS** — `METRICS.json` produced with the full contracted schema, correct recall denominator, correct refusal behavior |
| 3 | audit artifacts were generated | **PASS** — `final_review_audit/attempt1__.../{input,report,record}` written, `final-review-audit-provenance` confirms one accepted record, `EXPORT_BUNDLE.json` produced |
| 4 | no answer-key leak into Reviewer input | **PASS** — dispatched Task spec contained no key material; `scan-leak` on the materialized workspace and a direct grep of the retained `input.md`/`report.md` both found zero hits |
| 5 | result is reproducible in form | **PASS** — every artifact carries an explicit `schema_version` and content digest (`fixture_digest`, `key_digest`, `findings_source_digest`); re-running `score` against the same `FINDINGS.json`/key is deterministic by construction (no wall-clock field in the byte-compared metrics document, per DESIGN.md D-002's fix) |

**§7 baseline: PASS** (all five criteria met; note this baseline itself did not need to exercise
the B-3R dispatch-failure retry path).

## Explicit non-conclusions

This baseline is a single run against one small fixture. Per the ticket's stated purpose (§7) and
Completion Criteria, the following are **not** concluded from this result, and this document
draws none of them:

- No claim is made about the *general* detection quality of the current Final Review mechanism.
- No conclusion is drawn on hypotheses H-1, H-2, H-4, or H-5.
- No comparison is made here between the current Final Review, a falsification/search-depth
  contract, or reviewer/model assignment variation — that comparison is OS-23 scope.
- Recall of 0.6 on this one attempt is not treated as a target, a regression, or an improvement —
  it is simply this run's number, recorded so OS-23 has a real number to compare against.
