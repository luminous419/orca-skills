# OS-22 §7 Fixture-Based Final Review Baseline — current, authoritative

**This is the CURRENT §7 baseline.** It supersedes `artifacts/runs/run_804e35d29531/BASELINE_RESULT.md`,
which described the capture in `artifacts/runs/run_92759e0e1034/` — which had itself superseded the
first attempt in `artifacts/runs/run_ff587481a820/`. Both earlier captures remain on disk, intact and
still tracked, as historical forensic evidence. Neither is the accepted baseline, and **no downstream
document may cite either as one**.

The audit trail for this capture lives in its own Orca Run, `run_5967188007ce`.

## Why the previous capture was superseded

| finding | what was wrong |
|---|---|
| R6 (`artifacts/runs/run_804e35d29531/FINAL_REVIEW_iteration3.md`) | The accepted capture in `run_92759e0e1034` was taken under the **superseded `redaction/1.0`** policy. Its retained `input.md`, its `record.json` (`report.contract_path`), and the evidence bundle that embeds them all carried a raw non-home absolute scratch path containing the local username, the `-Users-`-encoded workspace shape and a session UUID — while `redactions` truthfully reported `[]`, because `1.0` had no category that owned that shape. DESIGN's B3 criterion requires the retained family to return **zero** hits for the local username, the `-Users-` shape and `/private/tmp/`, with a non-zero `foreign_absolute_path` redaction count. The `1.0` capture could not meet it, and an immutable record cannot be hand-edited into compliance — a hand-edited artifact is no longer evidence of what the pipeline produces. |

The chain of supersession, stated once so a reader lands in the right place:

```text
run_ff587481a820   attempt 1   superseded (R2 hinted prompt, R4 key-derived evidence)
run_92759e0e1034   attempt 2   superseded (R6: captured under redaction/1.0, leaks the scratch path)
run_5967188007ce   attempt 3   CURRENT — captured under redaction/1.1, retained family environment-safe
```

## What this re-capture changed, and what it deliberately did not

**Changed: the redaction policy in force at capture time.** Nothing else about the procedure moved.
The writer is the `redaction/1.1` implementation already merged on this branch
(`scripts/run_logging.py`, and its byte-identical installed twin). No production code was written,
modified or patched for this capture; this Run's requested phase is TEST only.

**Unchanged, deliberately:**

* **The Reviewer prompt.** It is the R2-corrected neutral prompt, reused byte-for-byte except for the
  two occurrences of the scratch workspace path. Verified mechanically: substituting the old path
  back into the new prompt reproduces the previous capture's stored spec exactly, and both are 4,104
  bytes pre-redaction. It carries only the ordinary §17/§11 framing — role, Direct Verification duty,
  the full undifferentiated A–I search-axis list, the Review Result format, the Finding Contract —
  plus the materialized subject and the report path. It names no defect class, weights no contract
  section above another, does not disclose that the subject is a fixture or part of an evaluation,
  and never says how many findings there are to find.
* **Detection and search policy.** No change of any kind. Same agent command as the two prior
  captures (`codex-sol`), same fixture, same `fixture_digest`.
* **The publication rule.** P-1 below is carried over verbatim from the superseded write-up.
* **Scorer output is not committed.** It was written outside the repository, as before.

## Procedure

Per DESIGN.md `### Baseline procedure (B-1 … B-5, §7)` and PLAN.md's BASELINE work items. It
exercises the *current* Final Adversarial Review mechanism against the planted-defect fixture.
**No Final Review detection or search policy was changed to produce this result.**

### B-1 — Materialize

```
python3 scripts/final_review_eval.py materialize --dest <scratch, outside this repo>
```

14 files, `fixture_digest = sha256:b63f5a9f4280549ea3a05407b4b5fff28e054b75ee674f419413b5c69cf70f1d`
— identical to both prior captures, so the subject is provably the same tree — and no `.git` created.
`verify-fixture` PASSED. The shipped literal `scan-leak`, with no exclusions, PASSED with zero hits
over the whole materialized workspace before any Reviewer saw it.

### B-2 — Dispatch

One fresh Final Adversarial Review attempt, in a newly created terminal running `codex-sol`, adopted
into Run `run_5967188007ce` and dispatched as Task `task_94d64c838578` / Dispatch `ctx_2b0c56074844`.
The Reviewer received the neutral input described above and nothing else. Before dispatch, that exact
prompt text passed both the shipped literal `scan-leak` and `semantic_leak_scan --profile prompt`
with zero hits.

The dispatch settled cleanly on the first try — `status: completed`, `failure_count: 0`, no
dispatch-layer failure — so **B-3R's retry loop was not needed**, for the third capture running. The
Reviewer returned `RESULT: FAIL`, `REVIEW_VERDICT: FAIL`, with a non-empty set of blocking findings,
all MAJOR or above. How many is not published here — see the publication rule below.

### B-3 — Audit capture

```
python3 scripts/run_logging.py final-review-audit-write --run-id run_5967188007ce --attempt 1 \
  --task-id task_94d64c838578 --dispatch-id ctx_2b0c56074844 \
  --provenance accepted --settlement settled --report-path <REPORT.md> \
  --terminal <handle> --agent-command codex-sol --agent-origin self_created
```

Wrote the immutable per-dispatch record at
`artifacts/runs/run_5967188007ce/final_review_audit/attempt1__task_94d64c838578__ctx_2b0c56074844/`
(`input.md`, `report.md`, `record.json`). `final-review-audit-provenance --attempt 1` reports exactly
one `accepted_dispatch_key` and zero `violations`. `final-review-audit-export` wrote
`FINAL_REVIEW_EVIDENCE_BUNDLE.json`, whose `integrity` block reports `records_found: 1`,
`records_ok: 1`, and empty `digest_mismatches`, `unreadable`, `missing_artifacts` and
`incomplete_publications`.

Because no dispatch-layer failure occurred, this baseline does not itself demonstrate the B-3R retry
path; that path is exercised by the TEST-phase audit/failure regression tests (T-2), which inject a
synthetic dispatch failure and assert retry-under-new-identity behavior. Real baseline provenance
here is a clean `accepted` case.

### B-4 — Scoring (separate step, after the Reviewer settled)

```
python3 scripts/final_review_eval.py parse-report --report REPORT.md --workspace <scratch> --out <scratch>/FINDINGS.json
python3 scripts/final_review_eval.py score --findings <scratch>/FINDINGS.json --key <key, not named here> \
  --workspace <scratch> --out <scratch>/METRICS.json --run-verdict FAIL \
  --provenance-out <scratch>/SCORING_PROVENANCE.json
```

Both ran only after the Dispatch had already settled and `REPORT.md` existed — scoring was not run
concurrently with, or before, Reviewer execution, per §5's execution/scoring separation requirement.
**Both output documents were written outside the repository and are not committed.**

## Publication rule (P-1)

Carried over unchanged from the superseded write-up, where it was introduced by finding R4-T2:

> **P-1.** Committed evidence publishes **at most one** quantity from
> `{key population total, detected/matched count, missed count, unmatched-finding count,
> reviewer finding count, recall}`, and publishes it only as a **coarse bucket**, never as an
> exact value. Everything else about the baseline is reported qualitatively — PASS/FAIL,
> present/absent, computed/refused.

The one quantity published is recall, as a bucket. Every other number in that set is withheld, so no
pair of published values determines the population size by any of the relationships the scorer
computes. This is enforced mechanically, not by inspection — see `## Leak validation`.

## Sanitized result

Deliberately reported as an aggregate under P-1. Which specific entries were matched or missed, how
many there are in total, the identifier of the subject, and the location of the key are all withheld
from committed evidence — that withholding is the point of R4, not an omission.

| metric | value |
|---|---|
| recall against the key | **between 50% and 75%** (coarse bucket — the exact value, its numerator and its denominator are all withheld under P-1) |
| findings reported by the Reviewer | withheld under P-1 |
| unmatched findings | present; each carries `UNADJUDICATED` — **not** auto-classified as a false positive. The count is withheld under P-1 |
| adjudicated true / false positives | none — no adjudication was performed, which is why precision is refused below |
| precision, false-positive rate | `null`, status `REFUSED`, reason `adjudication_incomplete` — the unmatched findings carry no independent adjudication and no closed-world attestation is present |
| evidence grounding | perfect — every reported finding resolves to a real file, and to a real line wherever a line was given; none was ungrounded |
| verdict reproducibility | `SINGLE_RUN_NOT_ASSERTED` — this was a single run, and a single run correctly does not claim reproducibility |

The refusal row is the concrete proof of the ticket's §6 rule: the scorer declined to compute
precision or a false-positive rate from an incompletely adjudicated result rather than defaulting
unmatched findings to false positives. Asked to compute one anyway with `--require-precision`, it
exits **3** and prints the refusal rather than producing a number.

Recorded plainly, because it is the only cross-capture comparison this document makes: this
re-capture landed in the **same** recall bucket as the two prior captures. No inference is drawn from
that beyond what it says — three single runs against one subject, and a bucket wide enough that
landing in it is weak evidence of anything.

The exact metrics document still exists; it was written outside this repository and is not committed.
Anyone who needs the precise numbers reproduces them by re-running `score` against the retained
`report.md` and the key, which is the point: the numbers are *reproducible* from the evidence plus
the key, and are not *published* by the evidence alone.

## B-5 — Baseline pass criteria (independent, per DEC-9)

| # | criterion | result |
|---|---|---|
| B1 | procedure ran, including at least one dispatch that settled with a usable report | **PASS** — `materialize` → dispatch (`completed`, `failure_count: 0`) → `parse-report` → `score` all executed and exited 0 |
| B2 | scoring worked | **PASS** — the full metric block emitted with the contracted schema. `precision_status` and `false_positive_rate_status` are **both `REFUSED`**, which is the correct outcome: neither of E.5 point 2's two computation paths was recorded (no adjudication document, no closed-world attestation), so the `precision + false_positive_rate == 1` and `unadjudicated_count == 0` conditions do not apply and must not be forced |
| B3 | artifacts produced **and the retained family is environment-safe (C.7 P-PATH)** | **PASS** — `input.md`, `report.md`, `record.json` exist; both `artifact_digest_post_redaction` values re-verify against the bytes on disk; the log ↔ input ↔ report join succeeds on `task_id`/`dispatch_id`; and a grep of all three plus the exported bundle for the local username, the `-Users-`-encoded workspace shape and `/private/tmp/` returns **zero** hits, with `stored_task_spec.redactions` reporting `foreign_absolute_path` count **3**. This is the criterion the superseded capture failed |
| B4 | no answer-key leak into the Reviewer input | **PASS** — D.6's literal scan of the retained reviewer input returns zero hits, and the semantic scanner's full `prompt` profile also returns zero |
| B5 | reproducible | **PASS** — re-running `score` on the same stored findings document produces a **byte-identical** metrics file for the entire document, with no excepted field (verified by `cmp`, and by identical SHA-256). `parse-report` is likewise byte-identical on re-run. The dispatch inputs are recorded well enough to re-issue: the retained `input.md`, the Task/Dispatch ids, the agent command and the fixture digest are all in the record |

**§7 baseline: PASS** (all five criteria met; this capture again did not need to exercise the B-3R
dispatch-failure retry path).

## Leak validation

Two independent concerns, kept separate because they fail for different reasons.

### 1. Environment safety (the R6 concern, DESIGN B3)

Every retained and exported file of this capture, scanned for each of B3's three patterns:

| file | `<local-username>` | `-Users-` | `/private/tmp/` |
|---|---|---|---|
| `final_review_audit/attempt1__…/input.md` | 0 | 0 | 0 |
| `final_review_audit/attempt1__…/report.md` | 0 | 0 | 0 |
| `final_review_audit/attempt1__…/record.json` | 0 | 0 | 0 |
| `FINAL_REVIEW_EVIDENCE_BUNDLE.json` | 0 | 0 | 0 |
| `ORCHESTRATOR_LOG.md` | 0 | 0 | 0 |
| `TIMING_LOG.md` | 0 | 0 | 0 |

with, in the same records, `stored_task_spec.redaction_policy_version = redaction/1.1`,
`stored_task_spec.redactions = [{foreign_absolute_path, 3}]`, and
`report.contract_path = "<REDACTED:foreign_absolute_path>"` — the P4 category, the whole value and
nothing else. The bundle's `component_versions.redaction_policy` is `redaction/1.1`.

An independent P-PATH classifier — a re-implementation of DESIGN C.7's P1–P4 table, written for this
verification rather than imported from the writer, so it cannot agree with the writer by construction
— was run over every string in `record.json` and in the bundle. No value falls outside P1–P4. The
only surviving `/Users/` prefixes are inside `delivery_evidence.process_incarnation`, where category
4's **readability-preserving** substitution replaces the user-name segment and leaves the rest
(`/Users/<REDACTED:absolute_local_path>/…`). That is unchanged, sanctioned `redaction/1.0` behavior
that `1.1` deliberately keeps, and it discloses no username; it is not one of B3's three patterns.

### 2. Answer-key isolation (the R2/R4/R4-T2 concern)

| scan | scope | result |
|---|---|---|
| `scan-leak` (shipped, literal) | materialized workspace, no exclusions, before any Reviewer saw it | PASSED, 0 hits |
| `scan-leak` + `semantic_leak_scan --profile prompt` | the dispatched prompt text, before dispatch | PASSED, 0 hits each |
| `scan-leak` + `semantic_leak_scan --profile prompt` | the **retained** `input.md`, after capture | PASSED, 0 hits each |
| `scan-leak` (literal) | every committed file of this capture | PASSED, 0 hits, file by file |
| `semantic_leak_scan --profile evidence` (identity checks + `metric_inference`) | every committed file of this capture | 0 hits on `input.md`, `record.json`, both logs; **1 hit** on `report.md`, inherited by the bundle that inlines it — see below |
| `semantic_leak_scan --profile evidence` | this file and `artifacts/runs/run_644c005bc9db/TEST.md` | PASSED, 0 hits |
| `metric_inference` | the **union** of every metric value appearing anywhere in this capture's committed set | PASSED, 0 hits — no combination of published numbers pins the key population |

**The one `report.md` hit, stated plainly rather than hidden.** The scanner's `archetype_vocabulary`
check fires when partial key vocabulary co-occurs inside an eight-word window. In one sentence of the
Reviewer's own `## Test Review` paragraph — its summary of which contract boundaries its focused
probes exercised — two ordinary English words the Reviewer chose independently land inside that
window for one archetype name. Three things make this not a leak:

1. **Direction.** It is in the Reviewer's *output*, produced after the review, not in its input. The
   input is at zero hits under every check, which is what B4 actually governs. Nothing narrowed the
   search; the words are the Reviewer describing a defect it had already found in `src/`.
2. **Content.** The words are generic vocabulary from the subject domain, and the archetype names
   themselves are **already published** in the `ARCHETYPES` tuple in `scripts/final_review_eval.py`
   — shipped, tracked source. The report carries no entry id, no finding-to-entry mapping, no total,
   no missed-entry list and no key path.
3. **Immutability.** `report.md` is a byte-exact snapshot of Reviewer-authored bytes, digest-bound by
   `record.json` and immutable under DESIGN A.3. Editing it to satisfy a heuristic would falsify the
   record and destroy the thing's evidentiary value. The superseded capture's `report.md` happened to
   return zero on this check; that was luck of wording, not a guarantee the contract ever made.

This is the same class of residual the superseded write-up already recorded under *"The retained
Reviewer reports contain their own findings"*: P-1 governs what a write-up **publishes**; it cannot
govern what an audit artifact **is**.

## Known residuals, stated rather than hidden

**The two earlier captures stay on disk, unmodified.** `run_92759e0e1034/` still contains the
`redaction/1.0` scratch-path disclosure R6 identified, and `run_ff587481a820/` still contains the
hinted prompt R2 identified. Both are immutable, digest-bound audit records; both are explicitly
**superseded** and **non-current**; neither may be cited as the accepted baseline. They are retained
because they are the forensic record of the two defects — an artifact whose evidentiary purpose is to
contain a defect cannot be scanned clean. If the project would rather the repository hold no such
material, the follow-up is to move those directories into an out-of-band forensic archive. That is a
separate decision and is not taken here.

**Two Coordinator-owned files in `run_804e35d29531/` still disclose the population by arithmetic.**
Recorded in the superseded write-up and in `run_804e35d29531/TEST.md`
(`### Out-of-scope disclosures found by the new check`); unchanged by this capture, still not this
document's to rewrite, still awaiting a Coordinator decision.

## Explicit non-conclusions

This baseline is a single run against one small subject. Per the ticket's stated purpose (§7) and
Completion Criteria, the following are **not** concluded here, and this document draws none of them:

- No claim is made about the *general* detection quality of the current Final Review mechanism.
- No conclusion is drawn on hypotheses H-1, H-2, H-4, or H-5.
- No comparison is made between the current Final Review, a falsification/search-depth contract, or
  reviewer/model assignment variation — that comparison is OS-23 scope.
- The recorded recall is not a target, a regression, or an improvement. It is this run's number,
  recorded so OS-23 has a real number to compare against. OS-23 gets that number the same way anyone
  else does — by re-running `score` against the retained `report.md` and the key, not by reading it
  off a committed artifact.
