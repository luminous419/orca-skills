> **SUPERSEDED — this is no longer the current §7 baseline.**
> The capture described below (`artifacts/runs/run_92759e0e1034/`) was taken under the superseded
> `redaction/1.0` policy, and its retained `input.md`, `record.json` and evidence bundle carry a raw
> environment-specific scratch path. That is finding **R6** in
> `artifacts/runs/run_804e35d29531/FINAL_REVIEW_iteration3.md`, and it means this capture does not
> satisfy DESIGN's B3 gate.
>
> **The current, authoritative §7 baseline is
> [`artifacts/runs/run_644c005bc9db/BASELINE_RESULT.md`](../run_644c005bc9db/BASELINE_RESULT.md)**,
> describing the re-capture in `artifacts/runs/run_5967188007ce/` under `redaction/1.1`.
>
> This document and the `run_92759e0e1034/` artifacts are retained unmodified as historical forensic
> evidence of R6 — exactly as `run_ff587481a820/` is retained as the forensic evidence of R2/R4. Do
> **not** cite either as the accepted baseline result. Everything below is preserved as it stood when
> it was written; only this notice was added.

---

# OS-22 §7 Fixture-Based Final Review Baseline

**This document replaces the previous §7 baseline write-up.** The first attempt at this baseline
was rejected by Final Adversarial Review (findings R2 and R4 in
`artifacts/runs/run_804e35d29531/FINAL_REVIEW.md`) and is **superseded**. Its artifacts remain on
disk under `artifacts/runs/run_ff587481a820/` as forensic evidence of exactly the defect this
correction fixes — they must **not** be cited as the accepted baseline result. Everything below
describes a fresh, independent baseline executed in a new Orca Run, `run_92759e0e1034`.

## Why the previous attempt was rejected

| finding | what was wrong |
|---|---|
| R2 | The Reviewer input for the superseded attempt narrowed the search: it told the Reviewer which defect classes to weight and pointed at specific numbered contract sections as the ones that mattered. That is a detection/search-depth alteration of the very mechanism the baseline is supposed to measure unchanged, so the number it produced was not a baseline. The shipped literal leak scan could not see it, because the hint was spelled with hyphens while the key spells the same vocabulary with underscores. |
| R4 | The committed run evidence republished key-derived identities (which specific entries were matched or missed, how many there were, the fixture's identifier, and the path to the key). Holding scoring until after the Reviewer settled protected the *execution*; it did not stop the *committed artifacts* from making the key reachable. |

## What this replacement changed

1. **Neutral Reviewer input.** The dispatched input contains only the ordinary §17/§11 Final
   Adversarial Reviewer framing — role, Direct Verification duty, the full undifferentiated A–I
   search-axis list, the Review Result format, and the Finding Contract — plus the materialized
   subject (`CONTRACT.md`, `DIFF.patch`, `src/`, `tests/`) and the report path. It names no defect
   class, weights no contract section above another, does not say the subject is a fixture or an
   evaluation, and never says how many findings there are to find. The retained `input.md` is the evidence.
2. **Leak validation extended past literal matching, and then past token matching entirely.** A
   new checker, `artifacts/runs/run_92759e0e1034/tools/semantic_leak_scan.py`, normalizes `_`, `-`,
   `/` and whitespace to one form before comparing, and additionally flags partial key vocabulary
   co-occurring inside a short window, contract-section targeting, expected-count statements, and
   framing or emphasis that narrows the search. It derives its vocabulary from the key at runtime
   and embeds none of it, so the checker itself is safe to commit. Run against the superseded
   attempt's input it reports 11 hits — i.e. it reproduces R2, and finds more besides — and
   against this attempt's input, zero. A later round (R4-T2) added a `metric_inference` check that
   looks at *numbers* rather than tokens: it extracts the evaluation's metric fields from a
   document and reports whether any combination of them algebraically determines the key
   population, which is how a write-up with no leaked vocabulary at all can still disclose how many
   entries the key holds. See `## Leak validation over the exact commit set`.
3. **Committed evidence redesigned.** Scorer output is no longer committed. It was written to a
   scratch directory outside the repository and is not part of any commit. What is committed is
   the audit trail plus the sanitized top-line summary below.

## Procedure

Executed after the ANALYSIS/PLAN/DESIGN/IMPLEMENTATION/TEST phase gates of `run_804e35d29531` all
passed, per PLAN.md's BASELINE work items (B-1 … B-5) and ordering constraints 6–8. It exercises
the *current* Final Adversarial Review mechanism against the planted-defect fixture built in
IMPLEMENTATION. **No Final Review detection or search policy was changed to produce this result.**

The audit trail lives in its own Orca Run, `run_92759e0e1034`, whose objective records it as the
§7 baseline replacement run under a neutral Reviewer prompt, made for the R2/R4 correction, with no
detection or search policy change, and names `run_804e35d29531` as its parent. It is kept out of
`run_804e35d29531`'s own audit trail because it evaluates a controlled subject, not this run's real
OS-22 diff; mixing the two attempt sequences would conflate two different subjects under one run's
provenance.

### B-1 — Materialize

```
python3 scripts/final_review_eval.py materialize --dest <scratch, outside this repo>
```

14 files, `fixture_digest = sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d`,
no `.git` created. `verify-fixture` PASSED. The shipped `scan-leak`, with no exclusions, PASSED
with zero hits over the whole materialized workspace before any Reviewer saw it.

### B-2 — Dispatch

One fresh Final Adversarial Review attempt, in a newly created terminal running `codex-sol`,
adopted into the Run and dispatched as Task `task_936f73b5d2eb` / Dispatch `ctx_1f82fd26c92b`.
The Reviewer received the neutral input described above and nothing else.

The dispatch settled cleanly on the first try — `status: completed`, no dispatch-layer failure — so
B-3R's retry loop was again not needed here. The Reviewer returned `RESULT: FAIL`,
`REVIEW_VERDICT: FAIL`, with a non-empty set of blocking findings, all MAJOR or above. How many is
not published here — see the publication rule below.

### B-3 — Audit capture

```
python3 scripts/run_logging.py final-review-audit-write --run-id run_92759e0e1034 --attempt 1 \
  --task-id task_936f73b5d2eb --dispatch-id ctx_1f82fd26c92b \
  --provenance accepted --settlement settled --report-path <REPORT.md> \
  --terminal <handle> --agent-command codex-sol --agent-origin self_created
```

Wrote the immutable per-dispatch record at
`artifacts/runs/run_92759e0e1034/final_review_audit/attempt1__task_936f73b5d2eb__ctx_1f82fd26c92b/`
(`input.md`, `report.md`, `record.json`). `final-review-audit-provenance --attempt 1` reports
exactly one `accepted_dispatch_key` and zero `violations`.
`final-review-audit-export` wrote `FINAL_REVIEW_EVIDENCE_BUNDLE.json`.

Because no dispatch-layer failure occurred, this baseline does not itself demonstrate the B-3R
retry path; that path is exercised by this run's own TEST-phase audit/failure regression tests
(T-2), which inject a synthetic dispatch failure and assert retry-under-new-identity behavior.
Real baseline provenance here is a clean `accepted` case.

### B-4 — Scoring (separate step, after the Reviewer settled)

```
python3 scripts/final_review_eval.py parse-report --report REPORT.md --workspace <scratch> --out <scratch>/FINDINGS.json
python3 scripts/final_review_eval.py score --findings <scratch>/FINDINGS.json --key <key, not named here> \
  --workspace <scratch> --out <scratch>/METRICS.json --run-verdict FAIL \
  --provenance-out <scratch>/SCORING_PROVENANCE.json
```

Both ran only after the Dispatch had already settled and `REPORT.md` existed — scoring was not run
concurrently with, or before, Reviewer execution, per §5's execution/scoring separation
requirement. **Both output documents were written outside the repository and are not committed.**

## Publication rule (P-1)

The first version of this section withheld the denominator as a *field* and was still wrong:
it published the Reviewer's finding count, the unmatched-finding count and an exact recall
decimal. Subtract the second from the first to get the matched count, divide that by recall, and
the withheld population falls out exactly (finding R4-T2). Redacting one field is not a disclosure
control when the remaining fields solve for it — and, for the same reason, that arithmetic is
described here rather than worked through with its actual operands.

So this document now follows a single rule, applied identically here and in
`artifacts/runs/run_804e35d29531/TEST.md`:

> **P-1.** Committed evidence publishes **at most one** quantity from
> `{key population total, detected/matched count, missed count, unmatched-finding count,
> reviewer finding count, recall}`, and publishes it only as a **coarse bucket**, never as an
> exact value. Everything else about the baseline is reported qualitatively — PASS/FAIL,
> present/absent, computed/refused.

The one quantity published is recall, as a bucket. Every other number in that set is withheld,
so no pair of published values determines the population size by any of the relationships the
scorer computes. This is enforced mechanically, not by inspection: see
`metric_inference` under `## Leak validation over the exact commit set`.

## Sanitized result

Deliberately reported as an aggregate under P-1. Which specific entries were matched or missed,
how many there are in total, the identifier of the subject, and the location of the key are all
withheld from committed evidence — that withholding is the point of R4, not an omission.

| metric | value |
|---|---|
| recall against the key | **between 50% and 75%** (coarse bucket — the exact value, its numerator and its denominator are all withheld under P-1) |
| findings reported by the Reviewer | withheld under P-1 |
| unmatched findings | present; each carries `UNADJUDICATED` — **not** auto-classified as a false positive. The count is withheld under P-1 |
| adjudicated true / false positives | none — no adjudication was performed, which is why precision is refused below |
| precision, false-positive rate | `null`, status `REFUSED` — the unmatched findings carry no independent adjudication and no closed-world attestation is present |
| evidence grounding | perfect — every reported finding resolves to a real file, and to a real line wherever a line was given; none was ungrounded |
| verdict reproducibility | `SINGLE_RUN_NOT_ASSERTED` — this was a single run, and a single run correctly does not claim reproducibility |

The refusal row is the concrete proof of the ticket's §6 rule: the scorer declined to compute
precision or a false-positive rate from an incompletely adjudicated result rather than defaulting
unmatched findings to false positives.

One observation worth recording, since it is the whole reason R2 mattered: the neutral input
landed in the **same** recall bucket as the superseded, hint-bearing input, and the underlying
exact value did not move at all. That does not retire R2 — a measurement taken through an altered
mechanism is not a baseline regardless of what number it happens to land on, and one paired attempt
cannot establish that the hints were inert. It does mean the recorded number itself did not move.

The exact metrics document still exists; it was written outside this repository and is not
committed. Anyone who needs the precise numbers reproduces them by re-running `score` against the
retained `report.md` and the key, which is the point: the numbers are *reproducible* from the
evidence plus the key, and are not *published* by the evidence alone.

## B-5 — Baseline pass criteria (independent, per DEC-9)

Per PLAN.md's DEC-9, the §7 baseline is PASS only if all five hold — not inferred from detection
quality:

| # | criterion | result |
|---|---|---|
| 1 | evaluation procedure actually ran | **PASS** — `materialize` → dispatch → `parse-report` → `score` all executed and exited 0 |
| 2 | scoring worked | **PASS** — the metrics document was produced with the full contracted schema, the correct recall denominator, and the correct refusal behavior |
| 3 | audit artifacts were generated | **PASS** — the per-dispatch `input.md` / `report.md` / `record.json` were written, provenance reports one accepted record and zero violations, and the evidence bundle was produced |
| 4 | no key leak into Reviewer input, and none in committed evidence | **PASS** — literal, semantic and `metric_inference` checks are all at zero over the exact commit set; see the leak validation below |
| 5 | result is reproducible in form | **PASS** — every artifact carries an explicit `schema_version` and content digest; re-running `score` against the same findings document and key is deterministic by construction (no wall-clock field in the byte-compared metrics document, per DESIGN.md D-002; the clock lives only in the separate provenance sidecar) |

**§7 baseline: PASS** (all five criteria met; this baseline again did not need to exercise the
B-3R dispatch-failure retry path).

## Leak validation over the exact commit set

All three checks were run over **every file this correction adds or rewrites**, not merely over
the materialized workspace: the shipped literal `scan-leak`, the semantic scanner's vocabulary
checks, and the `metric_inference` disclosure check described below. The set, and each file's
result:

| file | literal `scan-leak` | `semantic_leak_scan --profile evidence` (vocabulary + `metric_inference`) |
|---|---|---|
| `run_92759e0e1034/ORCHESTRATOR_LOG.md` | clean | clean |
| `run_92759e0e1034/TIMING_LOG.md` | clean | clean |
| `run_92759e0e1034/FINAL_REVIEW_EVIDENCE_BUNDLE.json` | clean | clean |
| `run_92759e0e1034/final_review_audit/attempt1__…/input.md` | clean | clean |
| `run_92759e0e1034/final_review_audit/attempt1__…/report.md` | clean | clean |
| `run_92759e0e1034/final_review_audit/attempt1__…/record.json` | clean | clean |
| `run_92759e0e1034/tools/semantic_leak_scan.py` | clean | clean |
| `run_804e35d29531/BASELINE_RESULT.md` (this file) | clean | clean |
| `run_804e35d29531/TEST.md` | clean | clean |
| `run_ff587481a820/ORCHESTRATOR_LOG.md` (redacted) | clean | clean |
| `run_ff587481a820/TIMING_LOG.md` | clean | clean |
| `run_ff587481a820/attempt1_scoring/FINDINGS.json` (quarantined) | clean | clean |
| `run_ff587481a820/attempt1_scoring/METRICS.json` (quarantined) | clean | clean |
| `run_ff587481a820/attempt1_scoring/REPORT.md` | clean | **retained, see below** |
| `run_ff587481a820/final_review_audit/attempt1__…/record.json` (redacted) | clean | clean |
| `run_ff587481a820/final_review_audit/attempt1__…/report.md` | clean | **retained, see below** |
| `run_ff587481a820/final_review_audit/attempt1__…/input.md` | **retained** | **retained** |
| `run_ff587481a820/EXPORT_BUNDLE.json` (embeds the above input) | **retained** | **retained** |

Plus, before any Reviewer saw it: the shipped `scan-leak` over the whole materialized workspace with
no exclusions — PASSED, zero hits — and `semantic_leak_scan --profile prompt` over the retained
Reviewer input — PASSED, zero hits. And, separately from the per-file sweep, one `metric_inference`
pass over the **union** of every metric value appearing anywhere in the set, because a commit set
can jointly disclose what no single file in it discloses alone.

### The `metric_inference` disclosure check (R4-T2)

Token matching cannot catch R4-T2, because R4-T2 leaked no token. `metric_inference` extracts the
evaluation's numeric metric fields from a document — as JSON-ish `field: value` pairs and as prose,
in digits or number words — and then asks whether any combination of them determines the key
population size under the relationships `scripts/final_review_eval.py` actually computes:

| id | relationship | solves for the population when the document publishes |
|---|---|---|
| REL-1 | the denominator / population total is itself a field | that field, in any spelling |
| REL-2 | `recall = detected / total` | recall **and** the detected/matched/numerator count |
| REL-3 | `recall = 1 − missed / total` | recall **and** the missed count |
| REL-4 | `total = detected + missed` | the detected count **and** the missed count |
| REL-5 | `detected = reported findings − unmatched` , then REL-2 | the Reviewer's finding count, the unmatched count **and** recall |
| REL-6 | recall written as the fraction `detected/total` | that fraction, in any spelling |

A range or bucket (`50-75%`, `between 50% and 75%`) is not an exact value and is stripped before
extraction, so P-1's coarse recall is deliberately not a hit. A hit is raised whenever a
relationship is *satisfiable* — the published numbers pin a positive integral total — regardless of
whether the solved value happens to be the real one, since a reader does the same arithmetic
without knowing the answer in advance.

**Regression evidence that it detects R4-T2.** Run against the pre-correction versions of the two
documents, it fires exactly as the finding described: on this file's previous `## Sanitized result`
it reports one REL-5 hit and names the correct solved value, and on the previous `TEST.md` it
reports a REL-1 hit on the explicit denominator plus REL-2/REL-5 hits. Run against the corrected
versions, both are at zero.

**Every file this correction produces is at zero hits under both scanners.** The four rows marked
*retained* are the superseded attempt's own forensic evidence, kept deliberately and with explicit
Coordinator authorization: they are the record of the hinted prompt, the Reviewer's answer to it,
and the bundle that embeds them. A scan cannot be clean over the artifact whose whole evidentiary
purpose is to contain the defect. What those four rows still carry, after quarantine, is archetype
*vocabulary* — already published in the `ARCHETYPES` tuple in `scripts/final_review_eval.py` — and
the fact that four of those categories were pointed at during that attempt. They no longer carry any
entry id, any finding-to-entry mapping, any total, any missed-entry list, or the key's path.

The `prompt` profile runs every check and is what a reviewer-visible input must pass. The `evidence`
profile runs only the identity checks and is what a committed run artifact must pass — a write-up of
the procedure may legitimately name the key or the fixture; it may not reproduce what the key
contains. The replacement attempt's own Reviewer report is included in the `evidence` sweep and
passes: the Reviewer's wording, arrived at independently, does not reproduce key vocabulary.

### What was quarantined or redacted from the superseded run

Done during this correction round on the Coordinator's explicit instruction, without deleting the
directory or its audit trail:

| artifact | action |
|---|---|
| `attempt1_scoring/METRICS.json` | contents replaced with a placeholder recording what it held, why it was quarantined, and the original SHA-256; the original was moved out of the repository |
| `attempt1_scoring/FINDINGS.json` | same treatment — the paired output of the same scorer step. It carried no key-derived content and is fully reproducible from the retained `report.md` with `parse-report`, so nothing forensic is lost |
| `ORCHESTRATOR_LOG.md` run-end row | detail text replaced; it had named key-derived quantities. The row itself, its timestamp and its result are untouched |
| `final_review_audit/attempt1__…/record.json` `notes` | replaced with a supersession note; every other field, digest and provenance value is untouched |
| `EXPORT_BUNDLE.json` | regenerated from the redacted log and record |

The audit trail proper — `record.json`, `input.md`, `report.md` — is intact. It is the evidence.

## Known residual, stated rather than hidden

**Two Coordinator-owned files disclose the population by the same arithmetic.** Running
`metric_inference` over the wider artifact tree found the disclosure R4-T2 describes in
`artifacts/runs/run_804e35d29531/ORCHESTRATOR_LOG.md` (a settled row whose `detail` text quotes the
finding verbatim, operands included) and in the three
`artifacts/runs/run_804e35d29531/final_review_audit/attempt1__task_75d5e97d1679__ctx_*/input.md`
records (each summarises this baseline as an exact `detected/total` fraction with its decimal).
Neither is this document's to rewrite — one is an append-only Coordinator log, the others are
immutable audit records whose bytes are digest-bound in their paired `record.json` — so they are
reported for a Coordinator decision rather than edited. Details in `TEST.md`,
`### Out-of-scope disclosures found by the new check`.

**The retained Reviewer reports contain their own findings.** P-1 governs what this write-up
*publishes*; it cannot govern what an audit artifact *is*. `report.md` in each run's
`final_review_audit/` directory is the Reviewer's verbatim output, so counting its findings is
always possible — that is what makes it evidence. This is not a P-1 violation and does not
reconstruct the population: a finding count alone determines nothing, and P-1 guarantees no second
quantity is published anywhere in the set for it to be combined with. The `metric_inference` pass
over the union of the whole commit set is what actually enforces that guarantee, rather than
leaving it as an assertion.

`artifacts/runs/run_ff587481a820/` is retained and still tracked in git. After the quarantine above
it no longer makes the key's contents reachable, but its `input.md` still shows which categories that
Reviewer was pointed at, and its `EXPORT_BUNDLE.json` embeds that text. That is deliberate — it is
the forensic record of R2 — and this correction does not launder it: the run is **superseded**, it is
not the accepted baseline, and no downstream document may cite it as one. If the project would rather
the repository hold no such material at all, the follow-up is to move that directory into an
out-of-band forensic archive. That is a separate decision and is not taken here.

## Explicit non-conclusions

This baseline is a single run against one small subject. Per the ticket's stated purpose (§7) and
Completion Criteria, the following are **not** concluded here, and this document draws none of
them:

- No claim is made about the *general* detection quality of the current Final Review mechanism.
- No conclusion is drawn on hypotheses H-1, H-2, H-4, or H-5.
- No comparison is made between the current Final Review, a falsification/search-depth contract,
  or reviewer/model assignment variation — that comparison is OS-23 scope.
- The recorded recall is not a target, a regression, or an improvement. It is this run's number,
  recorded so OS-23 has a real number to compare against. OS-23 gets that number the same way
  anyone else does — by re-running `score` against the retained `report.md` and the key — not by
  reading it off a committed artifact, because a committed artifact that carried it would also
  carry the key's population size.
