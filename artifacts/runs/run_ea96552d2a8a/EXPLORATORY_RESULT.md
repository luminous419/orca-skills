# OS-22 §7 isolated capture — EXPLORATORY RUN, **NOT** a baseline

> **This directory is not a §7 baseline and must not be cited as one.** The current, authoritative
> §7 baseline is and remains `artifacts/runs/run_644c005bc9db/BASELINE_RESULT.md`, describing the
> capture in `artifacts/runs/run_5967188007ce/`. Nothing in either was edited or deleted to produce
> this directory (ordering rule 4), and no supersession notice was added to either, because this
> capture does not satisfy `B1-B6`.

This is the capture DESIGN's amended `B-1′ … B-7` procedure produced on 2026-08-26, during TEST
iteration 2 of Run `run_75c5c6046f35`. It is retained because it is the **first** isolated capture
that ran at all: the kernel-enforced session built, the project's real Final Review agent
authenticated and worked inside it, and a real Final Adversarial Review completed. It is labelled
exploratory because two of the six criteria fail.

| criterion | verdict | one-line basis |
|---|---|---|
| **B1** procedure ran | **FAIL** | every step executed as documented, but **no dispatch settled** — the isolated worker cannot execute the `orca` CLI inside the sandbox, so `worker_done` could not be delivered (finding **F-501**) |
| **B2** scoring worked | PASS | `parse-report` + `score` ran as a separate post-review step; `precision`/`false_positive_rate` correctly `REFUSED` (`adjudication_incomplete`) |
| **B3** artifacts produced | **FAIL** | the retained `FINAL_REVIEW_ISOLATION.json` carries the local username and the isolation session path spelling verbatim (finding **F-502**); everything else in the retained family, and the exported bundle as a whole, grep clean |
| **B4** no answer-key leak | PASS | shipped `scan-leak` over the retained reviewer input: zero hits |
| **B5** reproducible | PASS | re-scoring produced a byte-identical metrics file, no excepted field |
| **B6** scope enforced | PASS | `scope_enforcement "seatbelt"`, `S1/S2/S3` all `PASS`, `NEG-0 … NEG-8` all `PASS`, `profile_digest` recomputes against the session's `scope.sb`, schema `1.1` seed record populated at both ends |

The full account, both findings with their reproductions, and the independent `B1-B6` verification
are in `artifacts/runs/run_75c5c6046f35/TEST.md`, section **TEST iteration 2 — R1 correction retry**.

## What is in this directory

| file | what it is |
|---|---|
| `final_review_audit/attempt1__task_9503cbf3cb04__ctx_ded3e8a05564/` | the immutable per-dispatch record: `input.md`, `report.md`, `record.json`. `report.md` **is** the isolated Reviewer's Review Result, byte-for-byte. `provenance: voided`, `void_reason: settlement_failure`, `settlement: not_settled` — the honest record of a dispatch that produced a report and never settled |
| `FINAL_REVIEW_EVIDENCE_BUNDLE.json` | the schema-`2.0` export. `integrity.records_found 1`, `records_ok 1`, every other integrity list empty |
| `ORCHESTRATOR_LOG.md` | written by `final-review-audit-write` |
| `EXPLORATORY_RESULT.md` | this file |

## Three `B-5′` outputs are deliberately absent

Each was retained verbatim and unedited outside the repository rather than committed, and each is
one `cp` away if the coordinator decides otherwise. Digests and the full reasoning are in `TEST.md`.

| absent | why |
|---|---|
| `FINAL_REVIEW_ISOLATION.json` | the artifact `B6` was verified against, and the one file that fails `B3`: committing it puts the `R6` disclosure into git history, and hand-editing it would destroy its value as evidence of what the pipeline produces (**F-502**) |
| `FINAL_REVIEW.md` | committing it fails the repository's own whitespace gate, and the **identical bytes** are committed at the exempted `final_review_audit/…/report.md` — same `sha256:53c2481456cc…c271` — so no evidence is lost (**F-503**) |
| `final_review_workspace/` | same gate, via `DIFF.patch`'s single-space context lines; the tree is reproducible from the committed fixture by `materialize`, with the same `fixture_digest` (**F-503**) |

## Not published

No quantity from `{key population total, detected/matched count, missed count, unmatched-finding
count, reviewer finding count, recall}` is published in this document. The Reviewer's verdict is an
observation, not a criterion. No detection-quality conclusion is drawn and no `H-1`/`H-2`/`H-4`/`H-5`
comparison appears here. Scorer outputs were written outside the repository and are not committed.
