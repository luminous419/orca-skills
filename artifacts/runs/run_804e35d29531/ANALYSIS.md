# Worker Result

STATUS: COMPLETE

## Request Summary

Jira **OS-22 — Final Review Observability & Evaluation Foundation**, ANALYSIS phase only.

OS-22 asks for an audit/evaluation foundation that makes every Final Adversarial Review
attempt reconstructible after the fact (reviewer-visible semantic input, report/findings/verdict,
accepted-vs-voided provenance, Task/Dispatch/repo identity), plus a seeded-defect evaluation
fixture with an isolated answer key and a repeatable metric contract, without changing Final
Review detection/search behaviour and without concluding H-1/H-2/H-4/H-5.

This phase produces analysis only. No code or documentation was written or modified. The
subsequent PLAN / DESIGN / IMPLEMENTATION / TEST phases are separate Tasks.

**One correction to the Task statement, carried through this document.** The Task text refers to
`tools/run_logging.py`. In this repository the canonical module is
`scripts/run_logging.py`; `orca-worker-reviewer-orchestration/tools/run_logging.py` is a
**byte-identical** copy shipped inside the installed Skill, and the two are compared
byte-for-byte by `scripts/validate_skills.py::validate_run_logging_tool_parity`
(`scripts/validate_skills.py:1944-1971`). Verified this run:
`cmp scripts/run_logging.py orca-worker-reviewer-orchestration/tools/run_logging.py` → identical.
Every change to the logging surface must therefore land in **both** files, or validation fails.

Scope boundary of the repository under analysis: `orca-worker-reviewer-orchestration/SKILL.md`
(1945 lines), its sister `orca-worker-reviewer-loop/SKILL.md`, `scripts/run_logging.py` (1064
lines), and the surrounding execution/validation tooling in `scripts/`.

---

## Current State

### CS-1. What a Final Adversarial Review attempt is contracted to leave behind

§17 (`SKILL.md:1698-1889`) defines the gate. Its artifact obligations are exactly two sentences,
at `SKILL.md:1865-1867`:

```text
review 기록은 attempt 1이 `<ARTIFACT_ROOT>FINAL_REVIEW.md`, attempt N>=2가
`<ARTIFACT_ROOT>FINAL_REVIEW_iteration<N>.md`다.
`_iteration1` 형태는 존재하지 않으며 `<N>`은 그 attempt의 `FINAL_REVIEW_ITERATIONS` 값이다.
```

§9's Artifact path contract (`SKILL.md:981-1016`) states the same rule as rule 1 of a three-rule
ladder (`SKILL.md:999-1003`), under `ARTIFACT_ROOT = artifacts/runs/<run-id>/`
(`SKILL.md:988`).

**The attempt's *input* has no artifact contract at all.** §17's input paragraph
(`SKILL.md:1733-1737`) describes what a Final Reviewer is handed, but only as prose describing
what the Coordinator should assemble — inline items (ORIGINAL_REQUEST, PHASES, provenance+ledger
summary, `FINAL_REVIEW_ITERATIONS`/max-iterations, the previous attempt's finding/resolution
table) and path-passed items (per-phase approved and REVIEW artifacts, the full `base..HEAD`
diff, changed production files, test/validation results). Nothing in §9, §16 or §17 says that
assembled spec is written to any file. Grepping `SKILL.md` for artifact paths returns four hits
(`:988`, `:1035`, `:1101`, `:1617`) and none of them is an input artifact.

### CS-2. Actual content structure of a `FINAL_REVIEW*.md`

It is a §11 Reviewer report, unchanged: §17 explicitly says the Final Reviewer "§11 Reviewer
Contract를 그대로 따르는 Reviewer instance이며 출력도 §11과 같다" (`SKILL.md:1709-1710`), with one
added field. The observed on-disk shape (e.g.
`artifacts/runs/run_c854db299e7a/FINAL_REVIEW.md`) is:

```text
# Review Result
RESULT: PASS | FAIL                                     (workflow gate, 2 values)
REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED  (report annotation, 4 values)
## Summary
## Blocking Findings      (ID / Quality Attribute / Severity / Blocking /
                           Responsible Phase / Location / Issue /
                           Reason / Evidence / Required Action)   -- §17 finding contract, SKILL.md:1775-1783
## Non-Blocking Findings
## Test Review
## Evidence Checked       (in reviews/common.md:178, NOT in §11 template SKILL.md:1240-1249)
## Final Decision
```

Two properties matter for OS-22:

- The report carries **no identity of its own**. There is no run id, attempt number, Task id,
  Dispatch id, terminal handle, reviewer command, or repository head in the document. Its only
  binding to an attempt is its **filename**, and its only binding to a dispatch is a
  human-written prose row in `ORCHESTRATOR_LOG.md`.
- `## Evidence Checked` is contract-ambiguous. It **is** in `reviews/common.md`'s Review Result
  Contract (`reviews/common.md:178`) but is **absent** from §11's own template
  (`SKILL.md:1240-1249`) and from §17's, which says the output is "§11과 같다". The two authorities
  disagree by one section, so the richest evidence-bearing part of a Final Review report is
  present in some reports and absent from others. Anything OS-22 wants to reconstruct from the
  report body cannot assume this section exists.

### CS-3. Actual schema of `ORCHESTRATOR_LOG.md` and `TIMING_LOG.md`

Both are append-only Markdown tables owned by `scripts/run_logging.py`. The columns *are* the
schema — `run_logging.py:53-97`:

```python
ORCHESTRATOR_LOG_COLUMNS = (
    "timestamp", "event", "phase", "role", "iteration",
    "task_id", "dispatch_id", "terminal", "action", "reuse",
    "gate_result", "review_verdict",
    "risk", "risk_source", "requested_phases", "round_kind",
    "result", "detail",
)                                                  # run_logging.py:56-80
TIMING_LOG_COLUMNS = (
    "timestamp", "event", "phase", "role", "iteration",
    "started_at", "ended_at", "duration_s", "risk", "detail",
)                                                  # run_logging.py:81-92
```

Constrained vocabularies (`run_logging.py:99-117`):

```python
RUN_STATUS_VALUES = ("COMPLETED", "BLOCKED", "ERROR", "ESCALATED")
RISK_VALUES        = ("low", "medium", "high")
RISK_SOURCE_VALUES = ("explicit", "default")
ROUND_KIND_VALUES  = ("phase_gate", "correction", "downstream_revalidation", "final_review")
```

**There is no `schema_version` field, and no version marker of any kind, in either table.** Both
files begin directly with the header row (verified: `head -1` of every
`artifacts/runs/*/ORCHESTRATOR_LOG.md`).

Notably `--event` is **unconstrained** — `orchestrator.add_argument("--event", required=True)`
with no `choices` (`run_logging.py:872`), while `--action`, `--risk`, `--risk-source` and
`--round-kind` all carry `choices` (`run_logging.py:876-887`). Real runs have exploited that:
`artifacts/runs/run_2c614077e685/ORCHESTRATOR_LOG.md` contains
`external_review_correction_triggered` and `artifacts/runs/run_ec18ea04bc22/ORCHESTRATOR_LOG.md`
contains `pr_created`, neither of which is documented anywhere in `SKILL.md`. Any OS-22 consumer
that switches on `event` must therefore be written against an **open** vocabulary.

### CS-4. Actual CLI and function surface of `run_logging.py`

Four subcommands (`run_logging.py:857-921`):

| subcommand | writes | key flags |
|---|---|---|
| `orchestrator-event` | `ORCHESTRATOR_LOG.md` | `--event` (free-form), `--task-id`, `--dispatch-id`, `--terminal`, `--action{,created,reused}`, `--reuse`, `--gate-result`, `--review-verdict`, `--risk`, `--risk-source`, `--requested-phases`, `--round-kind`, `--result`, `--detail` |
| `timing-event` | `TIMING_LOG.md` | `--event`, `--started-at`, `--ended-at`, `--duration-seconds`, `--risk`, `--detail` |
| `timing-dispatch-start` | `.timing_state.json` (+ opens boundary) | `--phase --role --iteration --risk`; reads the clock itself and prints the captured ISO instant |
| `run-status` | both | `--status` (4 values), `--reason`, `--run-started-at`, `--risk`, `--risk-source` |

Public functions: `log_orchestrator_event()` (`:340`), `log_timing_event()` (`:407`),
`log_run_status()` (`:508`), `class RunTimingTracker` (`:558`), plus path helpers
`orchestrator_log_path()` (`:330`) / `timing_log_path()` (`:335`) and
`_ensure_run_artifact_root()` (`:300`). Row writing is `_ensure_table()` (`:281`) +
`_append_row()` (`:294`) — every row fills every column, blank where inapplicable, so a parser
can split on `|`.

Deliberate design constraints that bind OS-22:

- **Zero imports from `scripts/`** — stated at `run_logging.py:17-27`, because an installed
  Skill copy has no `scripts/` package next to it. Any new audit module reachable from the
  installed Skill inherits this constraint.
- **Standard library only** (`run_logging.py:29`), consistent with the whole repository
  (`COMPATIBILITY.md`: "The project uses only the Python standard library", CPython 3.11+).
- **Logging failure never mutates a settled lifecycle decision** (`SKILL.md:1199-1201`).

### CS-5. Who consumes these artifacts today

- `scripts/orca_runtime_harness.py` — the Python execution path; imports `run_logging` and calls
  the same functions the CLI calls (`orca_runtime_harness.py:38`, `:1530-1560`, `:2065-2245`).
- `scripts/e2e_harness.py::final_review_artifact_path(run_id, attempt)`
  (`e2e_harness.py:422-433`) — the deterministic E2E harness's own copy of the attempt-suffix
  rule, asserted by `scripts/test_e2e_harness.py:2138-2151`.
- `scripts/task_context.py::phase_artifact_contract()` (`task_context.py:284-308`) — builds the
  `artifact_contract` value that goes into a dispatched Task spec.
- `scripts/validate_skills.py` — 19 `validate_*` functions, of which
  `validate_final_review_contract()` (`:1262`), `validate_run_logging_contract()` (`:1899`) and
  `validate_run_logging_tool_parity()` (`:1944`) are the ones OS-22 will trip.
- `scripts/release_manifest.py::required_skill_paths()` (`:70-82`) — the packaging allow-list;
  it explicitly names `<skill>/tools/run_logging.py` as the one extra shipped file.
- `scripts/fixtures/legacy_baseline/pre_os4_artifacts.json` — a golden capture of `task_specs`
  (full text of every dispatched spec), `orchestrator_log` and `final_report` across six
  skill×workflow combinations.

### CS-6. Where the Reviewer-visible semantic input is actually produced

One function: `scripts/task_context.py::render_task_spec()` (`task_context.py:590-646`). It
takes the caller's base spec, `strip_task_context()`s it (idempotent), then appends fixed-order
blocks with fixed header/footer markers (`task_context.py:543-558`):

```text
=== TASK BOUNDARY (layer 1) ===        TASK_BOUNDARY_KEYS
=== REVIEWER CONTEXT (delta-first) === REVIEWER_CONTEXT_KEYS   (optional)
=== QUALITY GATE (profile-first) ===   QUALITY_GATE_KEYS       (optional)
=== RISK PROFILE ===                   RISK_CONTEXT_KEYS       (optional)
=== AGENT ROUTING (who executes) ===   AGENT_ROUTING_KEYS      (optional)
=== END TASK SPEC ===
```

The comments at `:627-628` and `:636-638` record the existing house rule for extending this
function: a new block is appended **after** the existing ones and **only when its argument is
supplied**, so that a caller that omits it "renders a byte-identical spec to before this argument
existed." That is the precedent OS-22 must follow — or, better, avoid needing (see R-1).

### CS-7. Existing security/secret posture

There is **no redaction machinery of any kind** in the repository. Grepping `scripts/*.py`
(excluding tests) and `SKILL.md` for `redact|REDACT|secret` returns exactly two hits, both
policy prose: `SKILL.md:1597` ("secret 출력/기록/외부 전송" forbidden, §15) and `SKILL.md:1758`
(checklist axis G). The only machine-checked secret rule is the contract line
`AGENT_PROFILE_SECRETS = never_recorded` (`SKILL.md:1433`), enforced by
`validate_agent_profile_contract()`. No digest, hashing, or redaction-policy versioning exists to
build on.

### CS-8. Existing retention posture

`.gitignore` excludes `artifacts/orca-runtime/`, `artifacts/orca-agent-smoke/`, and
`artifacts/**/.timing_state.json` — but **not** `artifacts/runs/`. Retention is therefore
manual and ad hoc: of six run directories on disk, exactly one (`run_2c614077e685`, the OS-21
run) is tracked in git (`git ls-files artifacts` → 26 files, all under that one run).
`SKILL.md:1204-1205` explicitly defers retention: "Retention/archive 정책은 OS-8 범위이며 이
파일들은 여기서 삭제되거나 압축되지 않는다."

There is no evaluation fixture, answer key, scorer, or recall/precision machinery anywhere in
the repository. `scripts/fixtures/` contains one unrelated fixture (`legacy_baseline`).

---

## Findings

Each sub-question from the Task, answered against evidence.

### F1 — Reviewer-visible semantic input: **not held by any repository artifact; the Coordinator-authored Task spec IS retrievable from live Orca state; the delivered bytes are not.**

This finding was wrong in iteration 1 (it said "not reconstructible — not at all") and is
corrected here. Three layers have to be separated, because they carry different guarantees.

**(a) The persisted Task spec is retrievable from live Orca orchestration state.** The
version-matched CLI exposes each Task's full stored spec:

```bash
orca orchestration task-list --run <run_id> --json     # omit --brief; --brief caps each spec at 160 chars
```

Verified this run. `orca orchestration task-list --run run_804e35d29531 --json` returns
`task_c862feea878c.spec` in full (12 334 chars) including the complete verbatim
`=== ORIGINAL_REQUEST ===` block and every dispatched context block. It also works for
**historical** Final Review Tasks in other runs, not only the live one — verified against
`run_2c614077e685` (`task_c1c4fe5b4310`, Final Review attempt 1, 9 523 chars),
`run_e0cdf1afae58` (`task_1374a851c17d`), `run_ec18ea04bc22` (`task_917f1a2fc5d7`) and
`run_c854db299e7a`. That last run is the sharpest check, because it is this finding's own worked
example: all three specs from its three dispatches of attempt 1 are present in Orca state, and
their byte sizes corroborate `FINAL_RESULT.md:139-145` almost exactly.

| Task | status | stored spec (UTF-8 bytes) | `FINAL_RESULT.md` says |
|---|---|---|---|
| `task_2d0a6f4fc5a4` | failed (`agent_prompt_blocked`) | 14 805 B (14.5 KB) | ~14.8 KB |
| `task_6b7d7a0cdd95` | failed (`dispatch_input`) | 5 553 B (5.4 KB) | ~5.5 KB |
| `task_d3f49c042d5a` | completed — the **accepted PASS** | 2 269 B (2.2 KB) | ~2.3 KB |

What (a) proves: the Coordinator-authored spec text of a Final Review attempt is recoverable from
an authoritative source, per Task, for past runs as well as the live one, and the accepted PASS
above *can* now be read against the 2.2 KB spec its reviewer was actually assigned.

What (a) does **not** prove — these are the real limits, and each is a separate PLAN input:

1. **It is not evidence of delivery.** The spec is what Orca stored, not what the agent received.
   `run_c854db299e7a`'s `agent_prompt_blocked` failure is precisely a case where the stored spec
   exists and delivery did not complete, so "stored spec" and "reviewer-visible input" are
   provably not the same thing.
2. **It excludes the dispatch-injected preamble** — see (b).
3. **It establishes no retention horizon and no exportability** — see (c).

**(b) The dispatch-injected preamble is re-rendered on read, not retained — and the re-render is
demonstrably not the delivered bytes.** `orca orchestration dispatch-show --task <task_id>
--preamble --json` does return a `preamble` field (preamble + spec concatenated), for historical
Tasks as well as live ones. But it is rendered against *reader* state at call time, and it
diverges from what was delivered in at least two verified ways:

- **Wrong coordinator identity.** The preamble delivered to this Task says
  `Your coordinator's terminal handle is: term_4677bca4-…`, which `run-list` confirms is
  `run_804e35d29531`'s real coordinator. `dispatch-show --preamble` instead renders
  `term_a79429c1-…` — the *calling* terminal's own handle. The same wrong handle is rendered for
  `task_c1c4fe5b4310`, `task_9be1dd68dc2c` (run_2c614077e685, coordinator `term_5b1942be-…`),
  `task_1374a851c17d` (run_e0cdf1afae58, coordinator `None`) and `task_917f1a2fc5d7`
  (run_ec18ea04bc22, coordinator `term_14caa2ae-…`). Four Tasks, three runs, three different real
  coordinators, one rendered value — the field is filled from the reader, not from the record.
- **Missing capability material.** Every delivered command line in this Task's preamble carries
  `--dispatch-capability dcap_…`. The re-rendered preamble contains no `dispatch-capability`
  token at all.

So the preamble layer is reconstructible in **shape** but not in **content**. An audit artifact
that captured `dispatch-show --preamble` output and labelled it "what the reviewer received"
would be recording a partly fabricated record. (One incidental benefit: the re-render omits the
live `dcap_` token, which is relevant to R-3 — but the delivered text does carry it, so a
snapshot taken from the delivered side still needs redaction.)

**(c) Nothing durable holds any of this, and Orca guarantees no retention policy.** No file
anywhere in the repository holds a dispatched Final Review spec. §17's input paragraph
(`SKILL.md:1733-1737`) describes the intended content but assigns it no path; §9's ladder
(`SKILL.md:999-1003`) has no rule for an input artifact; §16's verification checklist
(`SKILL.md:1603-1625`) never asks for one. `artifacts/runs/run_c854db299e7a/` on disk contains
none of the three specs tabulated above.

Orca's *observable* history is in fact deeper than a single page, and this ANALYSIS previously
mis-stated that. `orca orchestration run-list` documents `--cursor <cursor>` in `--help`, and
`--json --limit 100` returns 100 rows plus a non-empty `result.nextCursor`. Feeding that cursor
back paginates: page 1 = 100 rows (newest `2026-08-25T14:16:04Z`, oldest `2026-08-21T19:54:17Z`),
page 2 = 100 rows (to `2026-08-21T15:40:02Z`), page 3 = 48 rows and no further cursor, ending at
`run_legacy_local` (`2026-08-01T12:20:24Z`); the oldest non-legacy Run reachable is
`run_4d0d517ab731` (`2026-08-05T14:00:05Z`). That is **248 unique Runs currently retrievable**.
`--limit 150` and `--limit 200` are indeed rejected `invalid_argument`, but that is a per-page
cap, not a visibility ceiling — cursor pagination traverses past it. Historical Task state is
retrievable that far back too: `orca orchestration task-list --run run_4d0d517ab731 --json`
returns `ok: true` with full `spec` text for a Run from 2026-08-05.

The real gap is narrower, and it survives that correction: **nothing establishes a retention
contract.** Neither the CLI result nor `SKILL.md` nor anything else in the repository states a
retention policy, a minimum guaranteed horizon, a deletion or compaction behavior, or an export
guarantee. What 248 observable Runs demonstrate is the *current* state of one database, not a
commitment that any of it is still there next month. Orca state is also not exported with the
repository, not reviewable in a diff, and unavailable to anyone who has the repo but not this
Orca database — which is the audience OS-22's provenance/evaluation requirement is written for.
So OS-22's audit artifact still has to carry its own durability independently of Orca's runtime
retention, not because Orca's history is shallow, but because Orca promises nothing about it and
it does not travel with the repository.

**Consequence, restated correctly.** `artifacts/runs/run_c854db299e7a/ORCHESTRATOR_LOG.md`
rows 35-37 record three dispatches of Final Review attempt 1 against one head:

```text
14:22:25Z  task_2d0a6f4fc5a4 / ctx_8251971fb59e  term_33295587…  failed, agent_prompt_blocked
14:24:25Z  task_6b7d7a0cdd95 / ctx_a2ed3c36e1b9  term_113d023d…  failed at dispatch_input
14:32:36Z  task_d3f49c042d5a / ctx_71f59c521292  term_c164994d…  PASS
```

The accepted PASS was produced from a spec roughly one sixth the size of the first, which
retrieval from Orca state now **confirms** rather than merely leaves open. What retrieval does
not fix is that the confirmation required live Orca state, out-of-band CLI access, and an
operator who already knew which Task ids to ask for: the durable record itself
(`FINAL_REVIEW.md` plus a prose `ORCHESTRATOR_LOG.md` row) contains no run id, no Task id, no
Dispatch id and no pointer to any of it. OS-21's F5 ("the accepted PASS is unauditable")
therefore **stands as a statement about the durable, self-contained record** and is **withdrawn
as a statement about absolute recoverability while Orca state survives**. OS-22's input artifact
should be a snapshot *of* source (a), explicitly labelled as the stored spec rather than the
delivered input, carrying (b)'s delivery evidence separately and never as verbatim preamble text.

### F2 — Reviewer report / findings / verdict: **reconstructible only by filename convention, and demonstrably lost in practice.**

Three distinct failure modes, all observed:

**F2a — a whole run's Final Review reports are simply missing.**
`artifacts/runs/run_ec18ea04bc22/` (OS-19, PR #17) contains `ORCHESTRATOR_LOG.md` and
`TIMING_LOG.md` and **nothing else**. Its log records two Final Review attempts:

```text
09:02:59Z  final_review reviewer 1  task_7ba7f5616d19  FAIL  (R1 G1 blocking, NaN duration)
09:17:51Z  final_review reviewer 2  task_917f1a2fc5d7  PASS
```

Neither `FINAL_REVIEW.md` nor `FINAL_REVIEW_iteration2.md` exists. The run ended
`COMPLETED` and the PR shipped. §16 step 8 (`SKILL.md:1616-1618`) *requires* "attempt마다
artifacts/FINAL_REVIEW_*가 있는지 확인한다" — the check is contract prose with no enforcement in
any validator, harness, or tool, and it was not performed.

**F2b — the file that §9 names for attempt 1 held a *voided* report while the accepted verdict
had none.** In `run_c854db299e7a`, the log says attempt 1's accepted outcome is PASS
(`task_d3f49c042d5a`). But `artifacts/runs/run_c854db299e7a/FINAL_REVIEW.md` reads
`RESULT: FAIL` with `R1 ... Responsible Phase: implementation` about
`scripts/e2e_harness.py:805-828` — that is the *second failed dispatch's* report. A third report
from the *first* failed dispatch survives at repository root as
`artifacts/FINAL_REVIEW_agent_profile_separation.md` (also `RESULT: FAIL`, a different R1 about
`WORKER_REVIEWER_MUST_DIFFER`). So for one attempt number there are two retained FAIL reports and
zero retained report for the accepted PASS, and a consumer following §9's ladder reads the
canonical attempt-1 path and gets a **voided FAIL**.

**F2c — a run can have reports and no lifecycle log at all.** `artifacts/runs/run_bf55f06dd7fc/`
contains `FINAL_REVIEW.md`, `IMPLEMENTATION.md`, `TEST.md` and four `REVIEW_*` files but **no**
`ORCHESTRATOR_LOG.md` and no `TIMING_LOG.md`. Its Final Review verdict has no dispatch identity
anywhere.

The one healthy case is `run_2c614077e685`: 8 `final_review` rows in the log and 8
`FINAL_REVIEW*.md` files whose `RESULT:` lines match the log's `gate_result` column
attempt-for-attempt (1 FAIL, 2 PASS, 3-7 FAIL, 8 PASS). That correspondence is real but
**entirely conventional** — nothing computes or checks it.

### F3 — accepted vs voided provenance: **no representation exists.**

`ORCHESTRATOR_LOG_COLUMNS` (`run_logging.py:56-80`) has no provenance, acceptance, or void
column. In `run_c854db299e7a` the two voided dispatches are recorded as `dispatch_settled` rows
with **empty** `gate_result`/`review_verdict` and the failure explanation buried in the free-text
`result` column:

```text
| dispatch_settled | final_review | reviewer | 1 | task_2d0a6f4fc5a4 | … |  |  | … |
  failed; last_failure=agent_prompt_blocked (prompt likely too long/complex …) |
```

So "this attempt was voided" is inferable only by a human reading English prose in `result`, and
"why" only from the same prose. A machine consumer sees a `dispatch_settled` row with a blank
verdict — indistinguishable from a Worker row, or from a reviewer whose response was malformed
(which `SKILL.md:1126-1128` explicitly instructs the Coordinator to leave blank: "빈 값이 추측한
값보다 정직하다"). **Blank currently means at least three different things.**

Correspondingly, no retained report says whether it was accepted. `FINAL_REVIEW.md` in
`run_c854db299e7a` is a voided report that presents itself as an ordinary review result, and OS-21
found that `FINAL_RESULT.md` then asserts the *opposite* of one of those voided findings
(`run_2c614077e685/ANALYSIS.md:401-406`).

### F4 — overwrite across retry/correction: **structurally guaranteed for retries; already occurring.**

The attempt-suffix rule keys on `FINAL_REVIEW_ITERATIONS` (`SKILL.md:1865-1867`), and §17's
Coordinator procedure increments that counter **once per attempt** (`SKILL.md:1722`, step 2),
not once per dispatch. Consequently *n* dispatch retries of one attempt all share one path.
Confirmed in code: `task_context.py:304-306` returns `f"{base}FINAL_REVIEW.md"` for
`phase == "final_review"` **with no attempt/iteration parameter at all** — the function signature
is `phase_artifact_contract(*, role, phase, run_id="")`, so the `artifact_contract` that reaches
a dispatched Final Reviewer spec is `FINAL_REVIEW.md` for *every* attempt. This is locked in by
`scripts/test_task_context.py:254-259`. Meanwhile `e2e_harness.py:422-433` implements the
suffixed rule correctly. **The two in-repo implementations of the same §9 rule disagree**, and
the one that feeds real dispatched specs is the one that never suffixes.

For phase Workers, in-place overwrite is deliberate and contractual — §9 rule 2 says
`<PHASE>.md` is updated "in-place 갱신, suffix 없음" (`SKILL.md:1001`) — so correction history for
a phase artifact is intentionally *not* preserved, only its reviews are.

### F5 — self-referential stale provenance (§4 of the ticket): **present, and structurally invited.**

Two mechanisms, both observed:

**F5a — the summary artifact duplicates dispatch history and then contradicts the primary source.**
§16 (`SKILL.md:1627-1648`) requires `FINAL_RESULT.md` to serialize the whole lifecycle ledger:
per-Dispatch four-axis rows, reuse chains, efficiency counts, plus a `## Final Adversarial Review`
block with `FINAL_REVIEW_TASKS` / `FINAL_FINDINGS` / `FINAL_REVIEW_REVALIDATIONS`. That is a
full second copy of what `ORCHESTRATOR_LOG.md` already holds, written by hand from Coordinator
memory. In `run_c854db299e7a/FINAL_RESULT.md` that copy became the *only* narrative of the
Final Review (there being no report for the accepted attempt) and it states `FINAL_FINDINGS:
none` plus a claim contradicting a voided finding, with no reviewer artifact behind it
(`FINAL_RESULT.md:156-170`). OS-21 recorded this at `run_2c614077e685/ANALYSIS.md:399-406`.

**F5b — `ORCHESTRATOR_LOG.md` is not actually terminal at `run_end`.** In
`run_2c614077e685/ORCHESTRATOR_LOG.md` a `run_end | COMPLETED` row appears at 17:50:28Z and is
then followed by six hours of further rows (`external_review_correction_triggered`, correction
dispatches, final_review attempts 3-8). Append-only is preserved; "the run ended" is not. A
consumer that treats the first `run_end` row as the end of the record silently truncates six of
the eight Final Review attempts. Since §16's `FINAL_RESULT.md` is written at what the Coordinator
believes is the end, that is precisely how a summary goes stale while still looking authoritative.

### F6 — the "undocumented size threshold" constraint (§3): **no existing code is affected.**

Searched `scripts/`, `orca-worker-reviewer-orchestration/` and `orca-worker-reviewer-loop/` for
`agent_prompt_blocked`, `prompt_too_long`, `MAX_PROMPT`, `SPEC_MAX`, `spec_bytes`, `len(spec)`.
The only hit is `scripts/e2e_harness.py:234` (`if len(spec) == 2:` — an unrelated tuple-length
check). The ~14.8 KB / ~5.5 KB / ~2.3 KB numbers exist **only** as prose observations in
`artifacts/runs/run_c854db299e7a/FINAL_RESULT.md:139-145`, and `agent_prompt_blocked` appears
nowhere in the repository's code or Skill text.

The §3 constraint is therefore **forward-looking, not remedial**: nothing must be un-hardcoded.
It forbids a future DESIGN/IMPLEMENTATION from lifting those observed numbers into a constant.
It is worth noting explicitly because the temptation is concrete — a naive "keep the spec under
the limit" implementation would encode 2.3 KB, and that number is a single-build observation on
one Orca version (`COMPATIBILITY.md` pins observations to 1.4.184 / 1.4.178-rc.2), not a product
constant.

### F7 — asymmetry that shapes where OS-22 can put things

Every phase has a paired policy file: `templates/<phase>.md` (Worker) and `reviews/<phase>.md`
(Reviewer), for all seven phases, plus `reviews/common.md`. **`final_review` has neither.**
Confirmed by `ls orca-worker-reviewer-orchestration/{templates,reviews}/` and by
`release_manifest.py:70-82`, whose `required_skill_paths()` enumerates `PHASES` and adds only
`tools/run_logging.py` beyond that. The Final Review's entire policy lives in `SKILL.md` §17.
Any OS-22 artifact contract for the Final Review therefore has no existing per-phase file to
extend — it goes into §9/§17 text, into `run_logging.py`, or into a genuinely new file that must
also be added to `required_skill_paths()`.

---

## Impact Scope

### Must change

| Component | Why | Notes |
|---|---|---|
| `orca-worker-reviewer-orchestration/SKILL.md` §9 Artifact path contract (`:981-1016`) | needs rules for a per-attempt **input** artifact and a per-attempt **report** artifact keyed by something that survives dispatch retry (F1, F4) | the three-rule ladder at `:999-1003` is the exact insertion point; per F1(a) the input artifact's *content* can be sourced by reading the stored Task spec back out of Orca after dispatch, so this row is about the durable path/naming rule, not about a spec-assembly hook |
| `SKILL.md` §9 Run-scoped logs (`:1018-1131`) | the ORCHESTRATOR_LOG columns and the "call exactly once at these points" list are here; provenance (accepted/voided) and audit-artifact identity have to be reachable from a log row (F3) | this subsection is asserted by `validate_run_logging_contract()` against literal anchor strings |
| `SKILL.md` §16 Final Verification step 8 (`:1616-1618`) | still says `artifacts/FINAL_REVIEW_*` — the **pre-run-scoping root path**, contradicting §9's `<ARTIFACT_ROOT>`; and the per-attempt check it mandates is unenforced (F2a) | a genuine existing defect in scope for OS-22 |
| `SKILL.md` §17 (`:1698-1889`) | the input paragraph (`:1733-1737`) and review-record paragraph (`:1865-1867`) are the two places that define what an attempt leaves behind; the `#### Final review contract` block (`:1871-1889`) is the machine-readable lock | contract block is length-capped at 15 lines by `FINAL_REVIEW_CONTRACT_MAX_LINES` (`validate_skills.py:275`) — **adding a key requires raising that cap and updating `FINAL_REVIEW_CONTRACT`** (`validate_skills.py:248-273`) |
| `scripts/run_logging.py` **and** `orca-worker-reviewer-orchestration/tools/run_logging.py` | the only writer of run-scoped provenance; needs the provenance/void vocabulary and probably the audit-artifact writer | byte-parity enforced (`validate_skills.py:1944-1971`); stdlib-only; **zero `scripts/` imports** |
| `scripts/task_context.py::phase_artifact_contract()` (`:284-308`) | returns `FINAL_REVIEW.md` for every attempt (F4) — the live dispatched-spec path | changing its signature touches `orca_runtime_harness.py:513,532,3081` and `e2e_harness.py:925,1100,1302` |
| `scripts/validate_skills.py` | 3 of its 19 validators lock the surfaces above | any new contract block wants a matching validator, per house style |

### Likely to change

- `scripts/orca_runtime_harness.py` — the Python execution path; would emit the new audit
  artifacts at the same points it emits log rows (`:2065-2245`).
- `scripts/e2e_harness.py` — `final_review_artifact_path()` (`:422-433`) and the attempt loop
  (`:1486-1500`); also the deterministic place to test audit-artifact emission without a live agent.
- `scripts/release_manifest.py::required_skill_paths()` (`:70-82`) — if any new file ships inside
  the Skill.
- `README.md` "Run-Scoped Artifacts and Logs" (`:136-160`) — documents the two logs and the
  artifact table; a new artifact class belongs here.
- `CHANGELOG.md`, `COMPATIBILITY.md` — house convention on every prior OS-* ticket.

### New surfaces OS-22 introduces

1. **Per-attempt Final Review input artifact** (secret-safe, digest-bearing) — new path, new
   schema, new writer.
2. **Per-attempt Final Review report artifact with explicit provenance** — either a new sidecar
   next to `FINAL_REVIEW*.md` or a versioned envelope; DESIGN decides.
3. **A versioned machine-readable audit contract** — the repository has no precedent for a
   versioned artifact; the closest analogues are `.orca/quality-profile.yaml`'s `version` field
   (`SKILL.md:1335-1337`) and `VERSION`.
4. **Seeded-defect evaluation fixture + isolated answer key** — new directory; `scripts/fixtures/`
   is the established location, but co-locating the answer key there is exactly the leak risk
   (R-4).
5. **Scorer / evaluator** — new module implementing the §6 metric contract, including the
   `UNADJUDICATED` rule and the refusal-to-compute-precision rule.
6. **Redaction** — entirely new; nothing to extend (CS-7).

### Explicitly out of scope (Task §"다음을 이번 PR에 포함하지 않는다")

OS-23 detection/search improvements; a falsification policy for the Final Review (i.e. **do not**
create `reviews/final_review.md` as a policy artifact in this ticket, even though F7 shows the
slot is empty); reviewer/model optimization; any conclusion about H-1/H-2/H-4/H-5; unrelated
lifecycle changes. `VERSION` and `LICENSE-DECISION.md` must not change; no merge.

---

## Dependencies / Constraints

### D-1. Existing semantics §8 of the ticket requires preserving — what each actually means here

| Invariant | Where it lives | What OS-22 must not disturb |
|---|---|---|
| Fresh Final Reviewer terminal per attempt | `SKILL.md:1713-1715`; `FINAL_REVIEW_TERMINAL_FRESHNESS = new_terminal_per_attempt` (`:1877`); `INVARIANTS` (`:1931`) | An audit artifact must not become a reason to reuse a terminal, and must not be *read into* the next attempt's context (that would re-introduce inherited verdict context, which freshness exists to prevent) |
| Worker / Reviewer separation | `SKILL.md:427-430`; `INVARIANTS:1894-1896`; §17 axis (b) never `reuse` (`:1878`, `:1932`) | The audit writer is a Coordinator-side step, not a third role. It must not be dispatched as a Worker or a Reviewer |
| Phase lifecycle | §7/§8/§12/§13; `PASS WITH NOTES`/`BLOCKED` are annotations, never lifecycle states (`:1377-1380`, `INVARIANTS:1943`) | `accepted`/`voided` must be recorded **provenance**, not a new lifecycle state or a new gate value. `RESULT:` stays two-valued, `REVIEW_VERDICT:` stays four-valued |
| Risk semantics | §8 Risk Axis (`:821-925`); `FINAL_REVIEW_RISK_INDEPENDENCE = mandatory_and_identical_at_every_risk_level` (`:1887`) | Audit emission must be identical at LOW/MEDIUM/HIGH. Making audit depend on risk would break the one property §17 asserts most strongly |
| Quality Profile semantics | §11 `#### Quality profile contract` (`:1311-1420`); resolved once per run (`:1355-1359`) | The audit artifact must not become a second quality input, and must not be re-resolved per attempt |
| Agent Profile immutable routing | §11 `#### Agent profile contract` (`:1413-1449`); `AGENT_PROFILE_SECRETS = never_recorded` (`:1433`) | Recording the resolved reviewer command per attempt (which OS-21 item 2 says is *necessary* to separate H-4 from H-5) is compatible — `AGENT_PROFILE_EVIDENCE` (`:1432`) already lists `resolved_commands`. But `never_recorded` for secrets is a hard rule the input artifact must honour |
| correction / downstream revalidation semantics | §12 (`:1439-1484`); §17 T3/T4/T5a (`:1807-1830`) and `#### Downstream revalidation` (`:1834-1869`) | `D` computation, HIGH-only execution, and the `PHASE_ITERATIONS[q]` reuse must be untouched |
| Responsible Phase correction semantics | §17 finding contract (`:1775-1783`) and ladder (`:1789-1801`) | OS-21 item 3 wants a new ladder rung — **that is OS-23/backlog, not OS-22** |

### D-2. Hard technical constraints

1. **Byte-parity**: `scripts/run_logging.py` ≡ `orca-worker-reviewer-orchestration/tools/run_logging.py`.
2. **No `scripts/` imports** from anything reachable by the installed Skill (`run_logging.py:17-27`).
   An audit writer callable by a live Coordinator CLI must be self-contained.
3. **Standard library only**, CPython 3.11-3.13 (`COMPATIBILITY.md`). Rules out YAML libraries;
   JSON is the only structured format with stdlib support (and the existing precedent —
   `.timing_state.json`, `pre_os4_artifacts.json`).
4. **Machine-readable contract blocks are length- and content-locked.**
   `FINAL_REVIEW_CONTRACT` (`validate_skills.py:248-273`) is compared for **exact equality**
   (`:1285`) against the block parsed from §17, and capped at
   `FINAL_REVIEW_CONTRACT_MAX_LINES = 15` (`:275`). Adding one key means editing the validator,
   the cap, and the SKILL.md block together.
5. **Append-only, every-row-fills-every-column** (`run_logging.py:53-55`) — adding a column to
   `ORCHESTRATOR_LOG_COLUMNS` changes the header of **every future** table while leaving existing
   files at the old width. There is no reader in-repo today, so nothing breaks now, but any OS-22
   reader must tolerate both widths. This is the strongest argument for a **separate** audit file
   over a widened log.
6. **`--event` is an open vocabulary** (CS-3) — a reader must not assume a closed set.
7. **Logging must never mutate settled lifecycle state** (`SKILL.md:1199-1201`) — so must audit
   writing. An audit-write failure cannot re-dispatch or re-settle anything.

### D-3. Where additive is possible, and where it is not

**Cleanly additive:**

- A new per-attempt input artifact and a new per-attempt report/provenance sidecar under
  `<ARTIFACT_ROOT>` — new paths, no existing consumer.
- New `orchestrator-event --event` values — the vocabulary is already open and already extended
  in the field (`pr_created`, `external_review_correction_triggered`).
- New `run_logging.py` subcommands — `argparse` subparsers, purely additive.
- The seeded fixture, answer key and scorer — entirely new files.
- A new `SKILL.md` subsection under §9 — precedent: OS-17 added `#### Run-scoped orchestration and
  timing logs` the same way.

**Not additive — requires a migration judgement (§8 of the ticket demands this be stated):**

1. **`ORCHESTRATOR_LOG_COLUMNS`.** Adding a provenance column changes the table width. Existing
   run logs on disk keep the old width. Either the reader tolerates both, or the provenance goes
   in a separate file. *Recommendation for PLAN: separate file; leave the log schema alone.*
2. **`phase_artifact_contract()` signature** (`task_context.py:284-308`). Making the final_review
   path attempt-aware means a new parameter, six call sites, and an assertion change at
   `test_task_context.py:254-259`. Unavoidable if the dispatched `artifact_contract` is to name
   a per-attempt path. *This is a real behaviour change to the dispatched spec — see R-1.*
3. **`SKILL.md:1617`'s `artifacts/FINAL_REVIEW_*`.** Fixing the stale root path to
   `<ARTIFACT_ROOT>FINAL_REVIEW*` is a correction, not an addition; it makes §16 agree with §9.
   Low risk, but it *is* a change to documented meaning and should be called out.
4. **`FINAL_REVIEW_CONTRACT` block.** Any new key is a coordinated three-place edit (D-2.4).

---

## Risks

### R-1 (HIGH) — Observability Neutrality (§2): the input artifact's own path can change the input

This is the sharpest risk in the ticket, and it is not hypothetical.

The Final Reviewer's Task spec contains `artifact_contract` as a **layer-1 boundary key**
(`TASK_BOUNDARY_KEYS`, `task_context.py:38-48`), rendered inline into the spec by
`render_task_spec()` (`task_context.py:606-610`). If OS-22 changes the Final Review's
`artifact_contract` from `FINAL_REVIEW.md` to a per-attempt path (which F4 says it should), then
**the Reviewer-visible bytes change** — for the same logical Final Review, before and after
OS-22. That directly contradicts the ticket's requirement that "동일 logical Final Review에 대해
OS-22 전후 Reviewer-visible semantic content가 동일해야 한다."

The tension is real and PLAN must resolve it explicitly rather than discover it in TEST. Three
shapes exist, and they are genuinely different tickets' worth of risk:

- (a) Leave `artifact_contract` alone; the Coordinator writes the per-attempt copy **outside**
  the reviewer's context. Neutrality is perfect. Cost: the reviewer still writes to a colliding
  path, so the Coordinator must copy/rename after settlement, and a report written by a *voided*
  dispatch can still land on the canonical path first (exactly F2b).
- (b) Change `artifact_contract` to the per-attempt path and argue the change is *semantically*
  neutral (a path is not review content). Cost: "semantic content" then needs a definition, and
  the byte-level neutrality test the ticket implies becomes impossible.
- (c) Two-layer: reviewer keeps writing the contract path, Coordinator immediately snapshots it
  to an immutable per-attempt path with provenance. Neutrality preserved; overwrite window
  shrinks to the interval between two dispatches of the same attempt.

**PLAN must pick one and state the neutrality claim it is making.** DESIGN must not decide this
implicitly.

Related neutrality traps, all concrete:

- **`render_task_spec()` is the single choke point** (`task_context.py:590-646`). Any audit
  metadata (attempt id, digest, redaction version, correlation id) appended there is by
  construction inside the reviewer's context. The existing house rule at `:627-628` and `:636-638`
  ("a caller that omits it renders a byte-identical spec") is the pattern to follow — but for
  OS-22 the correct answer is usually to add **no** argument at all.
- **A redaction pass that rewrites the input before dispatch** would change what the reviewer
  sees. Redaction must apply to the **retained copy**, after the spec is dispatched — never
  between assembly and dispatch. If both must be redacted, then OS-22 *has* changed reviewer
  input and must say so.
- **Size/budget**: the ticket forbids OS-22 from changing search behaviour or context budget.
  Since the spec is what hit the `agent_prompt_blocked` limit (F1), any implementation that adds
  bytes to the spec is a behaviour change even if the added bytes are "just metadata."
- **The input snapshot need not touch the spec at all (F1(a)).** Because
  `orca orchestration task-list --run <run_id> --json` returns the stored spec after dispatch,
  the Coordinator can capture the retained input entirely out-of-band. That makes shape (a) below
  byte-neutral *by construction* rather than by argument, and it is the strongest reason to
  prefer (a) or (c) over (b).
- **Neutrality is testable with existing machinery.**
  `scripts/fixtures/legacy_baseline/pre_os4_artifacts.json` already captures `task_specs` as
  "full text of every dispatched spec" for six skill×workflow combinations
  (`scripts/fixtures/legacy_baseline/README.md`). The same capture, run before and after OS-22,
  is a byte-level neutrality proof. PLAN should adopt this rather than invent one.

### R-2 (HIGH) — voided reports keep leaking into the accepted record

F2b is not a past accident; it is what the current path rule produces whenever a dispatch retry
happens. Any OS-22 design that keys the report path on attempt number **alone** reproduces it.
Worse, a partially-implemented OS-22 could make it less visible: adding a provenance field that
defaults to `accepted` would silently bless voided reports. The safe default is fail-closed —
absent or unparseable provenance reads as `UNKNOWN`, never as `accepted`. The ticket's §9
"malformed/incomplete audit artifact의 fail-closed 또는 explicit UNKNOWN 처리" requirement is
pointed at exactly this.

Note also that a voided dispatch's agent **keeps running** and can write its report *after* a
later attempt has written to the same path (`FINAL_RESULT.md:147-155`: "Both agent processes
actually continued working on the blocked prompt … and later attempted `worker_done`"). So the
overwrite is a genuine race, not just a naming collision.

### R-3 (MEDIUM) — secret/credential exposure in a retained input artifact (§4)

The Final Review spec is the largest and least-structured thing this system assembles. It
contains, by §17's own description (`SKILL.md:1733-1737`), a full `base..HEAD` diff reference,
changed production file lists, and test/validation output. Concrete exposure surfaces:

- **Absolute local paths.** `validate_skills.py::validate_no_user_absolute_paths()` (`:810`)
  already polices this for Skill files; a retained run artifact is a **new** surface it does not
  cover. Real Coordinator context routinely contains `/Users/<name>/...`.
- **Orca capability tokens.** Every dispatched worker preamble carries a
  `dcap_...` dispatch capability and terminal handles. These are short-lived, but they are
  credentials, and they appear in exactly the material a "reviewer-visible input" snapshot would
  capture. Note the asymmetry established in F1(b): the **delivered** preamble contains the
  `dcap_` token, while `dispatch-show --preamble` omits it. A snapshot sourced from Orca state
  therefore carries fewer credentials than one captured at delivery — which is a reason to prefer
  the Orca-state source, not a reason to skip redaction (absolute paths, PR URLs and diff content
  are in the Task spec regardless).
- **Environment-specific identity.** `git remote`, PR URLs, machine hostnames, agent command
  paths resolved via `PATH`.
- **Diff content.** If a project's diff contains a secret, an artifact that preserves
  "Reviewer-visible semantic content … 재현 가능하게" preserves the secret too.

There is nothing to build on (CS-7): no digest helper, no redaction policy, no test precedent.
The ticket's identity-metadata requirement (pre-redaction digest / policy version /
post-redaction digest / occurrence category) is therefore **all new** and needs to be designed as
a unit, or the digests will not actually be verifiable against each other.

Retention interacts: `artifacts/runs/` is not gitignored (CS-8), so the *default* is that a
retained input artifact becomes a committed, permanently public repository object. The ticket's
"모든 run artifact를 무조건 Git에 commit하는 방식을 기본값으로 두지 않는다" is directly aimed at this,
and the current state is that ad-hoc manual committing is already the practice (1 of 6 runs).

### R-4 (MEDIUM) — answer-key leakage (§5)

Leak paths, ranked by how easy they are to hit:

1. **Co-location.** If the fixture lives at `scripts/fixtures/<name>/` and the answer key lives
   beside it, a reviewer told to inspect the fixture directory finds the key by `ls`. The
   reviewer's `drill_down` mandate is explicitly *unrestricted* (`REVIEWER_DRILL_DOWN =
   mandatory_and_unrestricted`, `SKILL.md:1307`) — the contract *tells* it to look around.
2. **Git history.** A seeded defect introduced as a commit whose message says what it seeds is a
   leak to any reviewer that runs `git log`. Every observed Final Review artifact re-runs
   `git diff`/`git show` (OS-21 F7: "directly verifies the final repository state — HOLDS").
3. **Expected-count leakage.** Telling the reviewer "there are N issues" — or implying it via
   a fixture whose structure makes N obvious — invalidates the recall measurement.
4. **The audit artifact itself.** If the retained input snapshot of attempt *k* is readable and
   the reviewer of attempt *k+1* is pointed at `<ARTIFACT_ROOT>`, prior findings leak. This
   collides with the freshness invariant (D-1) as well.
5. **Scorer output in the run directory.** A scoring report written under `<ARTIFACT_ROOT>`
   during a baseline run is readable by the next attempt's reviewer.

The ticket's "Reviewer execution과 scoring을 분리한다" requirement is the structural mitigation,
but it only works if the *storage* is separated too, not just the *timing*.

### R-5 (MEDIUM) — the fixture being solvable by string search

The ticket requires the seeded defects be findable only through source/diff/test/contract
evidence. The five archetypes named (value-vs-presence, omitted call-site/propagation,
equality/boundary, losing precedence/fallback, validation-scope gap) are all **negative-space**
defects — the absence of something. That is what makes them string-search-resistant, and it is
also what makes writing them hard: it is easy to accidentally seed a defect that a grep for a
suspicious token finds. The ticket's own instruction that the Reviewer must verify the intended
defects actually exist implies DESIGN must produce a per-defect argument for *why* each is
negative-space, not just a list.

A second-order risk: the fixture must be small and reproducible, but the archetypes came from
defects in **this** repository's real code (OS-21's M1-M5). A fixture that is literally this
repository's code risks the reviewer recognizing it; a fixture that is too synthetic risks not
representing the archetype at all.

### R-6 (LOW-MEDIUM) — the baseline (§7) can fail for reasons unrelated to detection quality

The single required baseline run is a Final Review dispatch, and the observed base rate of
dispatch-layer failure on this exact gate is non-trivial: `run_c854db299e7a` needed three
dispatches for one attempt. A baseline that fails at `dispatch_input` proves nothing about
scoring. PLAN should treat "the baseline executed" and "the baseline's verdict" as separate
outcomes, and should ensure the audit machinery captures the failure evidence (which is, after
all, requirement §3).

### R-7 (LOW) — validator/packaging breakage

`validate_skills.py` compares `FINAL_REVIEW_CONTRACT` for exact equality and caps the block at 15
lines (D-2.4); `release_manifest.py::required_skill_paths()` (`:70-82`) enumerates required
packaged files and `verify_source_tree()` raises on any missing one. A new shipped file that is
added to one and not the other fails `python3 scripts/verify_package.py`. Mechanical, but it will
bite.

---

## Assumptions / Unknowns

### A-1. Provenance enum (§3) is explicitly an **example**, not a decision

The ticket lists `accepted`, `voided: dispatch_input`, `voided: capability_invalid`,
`voided: settlement_failure` and says "정확한 enum/schema는 DESIGN 단계에서 결정한다." Carrying
that forward, DESIGN must decide — and PLAN must list as decisions to be made:

1. Is provenance one field or two (`state` × `reason`)? The observed evidence has *causes* that
   compose: `run_c854db299e7a` attempt 1a failed at `dispatch_input` with `agent_prompt_blocked`,
   then its `worker_done` was separately rejected with `dispatch_capability_invalid`. One flat
   enum would have to pick one.
2. Is there a value for "the dispatch settled and the reviewer produced a report, but the report
   was malformed"? `SKILL.md:1126-1128` already contemplates malformed responses.
3. Is `UNKNOWN` a member of the enum (fail-closed default) or a separate absence-state?
4. Does provenance attach to the **report**, the **attempt**, or the **dispatch**? F2b shows one
   attempt with three dispatches, two reports and one accepted verdict — so at minimum the
   dispatch is the natural key, with the attempt as a grouping.
5. Are voided reports retained at the same path family as accepted ones, or quarantined?
6. What is the schema-version format and where does it live (in-file field vs filename vs
   sidecar)? The repository has no precedent (CS-3, item 3 of "New surfaces").

### A-2. Unknowns this ANALYSIS could not resolve from the repository

- **~~Whether Orca exposes the assembled spec back to the Coordinator after dispatch.~~
  RESOLVED — it exposes the stored Task spec, but not the delivered bytes (F1).** Iteration 1
  listed this as unknown; it is not. `orca orchestration task-list --run <run_id> --json` returns
  each Task's full persisted spec, for historical runs as well as the live one, so the retained
  input can be captured from an authoritative source rather than re-assembled, and §2's "가능한 한
  Reviewer context 밖에서 기록한다" is satisfiable for the spec layer without instrumenting
  `render_task_spec()` at all. Three narrower unknowns replace it, and DESIGN must keep them
  apart:
  1. **Delivered-byte equivalence — still unknown.** Nothing observable confirms the agent
     received the stored spec bytes, and `agent_prompt_blocked` is a counter-example where it
     did not (F1(a)). A Task-spec snapshot proves what Orca stored: stronger than "what the
     Coordinator intended to send," weaker than "what the reviewer saw." The artifact must say
     which of the three it is claiming.
  2. **The dispatch-injected preamble is not retrievable as delivered.**
     `dispatch-show --task <id> --preamble` re-renders it with the *reader's* terminal handle
     substituted for the coordinator's and with the `dcap_` capability omitted — verified across
     four Tasks in three runs (F1(b)). OS-22 may record preamble *metadata* (contract version,
     dispatch id, capability hash, dispatched_at — all present in `dispatch-show`), but must not
     present re-rendered preamble text as a delivered-input record.
  3. **Orca's retention *contract* — still unknown, though its current history is measurable.**
     `run-list` does support `--cursor`, and cursor pagination enumerated 248 Runs back to
     `2026-08-01T12:20:24Z`, with `task-list` still returning full Task specs for a Run from
     2026-08-05 (F1(c)). So historical Task/Dispatch retrieval reaches the oldest presently
     retained Run. What remains unknown is the *contract*: no CLI result, no `SKILL.md` clause,
     and nothing else in the repository states a retention policy, a minimum guaranteed horizon,
     a deletion/compaction behavior, or an export guarantee. Orca state is also not exported with
     the repository. So OS-22 still needs its own durable run-artifact/export copy; Orca state is
     a better *source* for that copy, not a substitute for it. The one thing DESIGN must **not**
     conclude is that "Orca already retains it" removes the artifact requirement.
  The §11/§17 rule that Orca CLI grammar is never guessed (`SKILL.md:1892-1898`) still applies:
  the three commands relied on above (`task-list --run … --json`, `dispatch-show --task …
  [--preamble] --json`, `run-list --json`) were each executed against this build during this
  phase, and their `--help` output is the version-matched authority.
- **The real nature of the `agent_prompt_blocked` limit.** `FINAL_RESULT.md:139-145` calls it "an
  apparent size threshold" on "this Orca build's `codex-sol` terminal-injection path," bracketed
  between 5.5 KB and 2.3 KB by bisection, and separately hypothesizes a "dispatch-layer race"
  (`run_c854db299e7a/ORCHESTRATOR_LOG.md` row 36). Two incompatible explanations from the same
  run. OS-22 must **record** observed input size and failure metadata (which §3 asks for) without
  asserting either explanation.
- **Whether any external tooling parses `ORCHESTRATOR_LOG.md`.** In-repo the answer is no
  (CS-5 — nothing reads the tables back). Outside the repo, unknown. This bounds how confidently
  PLAN can call a column addition "safe."
- **The `.timing_state.json` precedent.** It is a JSON sidecar next to the two logs, gitignored
  (`.gitignore`: `artifacts/**/.timing_state.json`), and owned by `RunTimingTracker`
  (`run_logging.py:44-51`). Whether an audit sidecar should follow the same *ignored* pattern is
  a retention decision, and it is the opposite of what OS-22 wants (audit artifacts are the
  evidence). Flagging it so DESIGN does not copy the gitignore line by reflex.

### A-3. Assumptions made in this analysis

1. **`.orca/quality-profile.yaml` is absent** for this run — confirmed: only
   `.orca/quality-profile.example.yaml` exists. Consistent with the Task's
   `profile_status: absent`. So no project quality attribute is blocking here, and the gate is
   Explicit Requirements → phase contract → G1-G5.
2. **OS-21 == PR #19 == `run_2c614077e685`.** Established from the commit message of `1045815`
   ("Jira: OS-21 / Run: run_2c614077e685") and confirmed by that run's ORCHESTRATOR_LOG
   `run_start` detail ("OS-21 Final Adversarial Review Effectiveness Validation"). Its findings
   are cited above by file and line rather than paraphrased.
3. **Run→ticket mapping used for evidence**: `run_c854db299e7a` = OS-4 / PR #18 (agent profile
   separation); `run_ec18ea04bc22` = OS-19 / PR #17 (TIMING_LOG correctness); `run_e0cdf1afae58`
   and `run_bf55f06dd7fc` = earlier tickets. Each is taken from that run's own `run_start`
   `detail` cell, not inferred.
4. **This analysis makes no claim about H-1/H-2/H-4/H-5.** Where OS-21's evidence tiers are
   cited, the tier (DEMONSTRATED / HYPOTHESIS) is carried through unchanged. F1-F5 above are
   independently re-verified structural facts about the current repository, not restatements of
   OS-21's causal claims.

---

## Recommended Next Step

PLAN should scope the following, in this order. The ordering is by **what blocks what**, not by
importance.

**Priority 1 — decide the neutrality shape before anything else (R-1).**
Everything else depends on it. PLAN must choose between (a) leave `artifact_contract` untouched
and snapshot outside reviewer context, (b) change it and redefine "semantic content," or (c) the
two-layer snapshot. It must state the neutrality claim explicitly and name the test that proves
it. F1(a) materially strengthens (a) and (c): the input snapshot does **not** require a
spec-assembly hook, because the Coordinator can read the stored spec back with
`orca orchestration task-list --run <run_id> --json` after dispatch. PLAN should adopt that as
the default input-capture mechanism, label the captured object as the *stored Task spec* (not
"what the reviewer saw" — F1(a) limit 1), and carry `dispatch-show` **metadata** (dispatch id,
capability hash, contract version, dispatched/completed timestamps) as separate delivery
evidence rather than re-rendered preamble text (F1(b)).
Recommend adopting the existing `pre_os4_artifacts.json` capture technique
(`scripts/fixtures/legacy_baseline/README.md`) as the neutrality test, since it already captures
full dispatched spec text before/after a change.

**Priority 1 — define the audit identity key and the provenance model (A-1, F3, F4).**
The key must survive dispatch retry: F2b proves attempt number alone is insufficient, and
`run_c854db299e7a` gives a worked example of one attempt / three dispatches / two reports / one
accepted verdict. PLAN should settle: dispatch as the primary key, attempt as the grouping,
provenance fail-closed to `UNKNOWN`. It should also decide **separate audit file vs widened
`ORCHESTRATOR_LOG.md`** — D-2.5 argues for separate, and PLAN should say so or say why not.

**Priority 1 — decide the schema-versioning mechanism (§1).**
No precedent exists (CS-3). This is small but blocks the artifact format, and retrofitting a
version marker onto an unversioned artifact is exactly the migration OS-22 exists to avoid
repeating.

**Priority 2 — the secret-safe representation and its identity metadata (R-3, §4).**
Four pieces that only work as a set: pre-redaction digest, redaction policy version,
post-redaction digest, occurrence/category record. PLAN must also fix the **ordering**
constraint: redaction applies to the retained copy *after* dispatch, never between assembly and
dispatch (R-1). And it must decide retention/commit policy explicitly, since the current default
(`artifacts/runs/` not gitignored, CS-8) commits everything. Retention is not discharged by Orca:
per F1(c) the runtime currently exposes 248 Runs via cursor pagination but commits to **no**
retention policy, minimum horizon, deletion behavior, or export guarantee, and Orca state does
not travel with the repository — so the durable artifact requirement stands independently of what
Orca happens to still hold.

**Priority 2 — artifact authority and the self-referential structure (F5, §4).**
Three decisions: (i) `ORCHESTRATOR_LOG.md` is authoritative for lifecycle provenance — including
what a post-`run_end` append means (F5b); (ii) the per-attempt input/report artifacts are
authoritative for content; (iii) `FINAL_RESULT.md` **references** rather than duplicates. Item
(iii) touches §16's required `## Orca Orchestration State` serialization
(`SKILL.md:1627-1648`) and PLAN must judge whether trimming it is in scope or is an OS-23 item.

**Priority 2 — the two concrete existing defects this ticket sits on top of.**
Both are small, both are squarely inside OS-22's stated goals, and both should be
explicitly accepted or explicitly deferred rather than left ambiguous:
- `SKILL.md:1617` still names the pre-run-scoping `artifacts/FINAL_REVIEW_*` path, contradicting
  §9's `<ARTIFACT_ROOT>` (Impact Scope).
- `task_context.py:304-306` returns `FINAL_REVIEW.md` for every attempt while
  `e2e_harness.py:422-433` implements the suffix rule — two in-repo implementations of one §9
  rule that disagree (F4). Note the fix is entangled with R-1's choice.

**Priority 3 — fixture and answer-key architecture (R-4, R-5).**
PLAN should fix the **storage separation** (key outside anything a reviewer is pointed at,
including git history and `<ARTIFACT_ROOT>`) before designing defect content, because the leak
paths are structural. It should also require DESIGN to justify, per seeded defect, why it is
negative-space rather than string-findable.

**Priority 3 — the metric contract and the `UNADJUDICATED` rule (§6).**
Mostly self-contained. The one rule that must not be softened: unmatched findings default to
`UNADJUDICATED`, and precision/FP-rate is *refused* (not estimated, not defaulted) unless the
closed-world or independent-adjudication precondition is met. OS-21's own external review (EXT-1,
commit `1045815`) was a MAJOR finding for exactly this class of overclaim — the scorer should make
the overclaim impossible rather than rely on the operator.

**Priority 3 — baseline execution plan (§7, R-6).**
Treat "procedure ran / scoring worked / artifacts produced / no answer-key leak / reproducible"
as five separate pass criteria, and treat dispatch-layer failure as a distinct outcome that the
new audit machinery is itself expected to capture.

**Guardrails PLAN must carry through, restated:** do not introduce a falsification or search-depth
obligation for the Final Review (OS-23); do not create `reviews/final_review.md` as a policy
artifact in this ticket even though F7 shows the slot is empty; do not rank or conclude
H-1/H-2/H-4/H-5; do not hard-code any observed `agent_prompt_blocked` size number as a product
constant (F6); do not change `VERSION` or `LICENSE-DECISION.md`; do not merge.

---

## Review Feedback Resolution

### A-001 (G1, MAJOR, blocking) — "reviewer-visible semantic input is not reconstructible, not at all" / "whether Orca exposes the assembled spec after dispatch is unknown"

**Accepted.** The Reviewer was right on the substance: the ORIGINAL_REQUEST and every dispatched
context block of `task_c862feea878c` are returned in full by
`orca orchestration task-list --run run_804e35d29531 --json`, so both the "not at all" phrasing
in F1 and the "unknown" framing in A-2 were factually wrong as written.

**Independent verification performed before revising** (not taken on the Reviewer's word):

| Check | Command run this iteration | Result |
|---|---|---|
| Live-run Task spec | `orca orchestration task-list --run run_804e35d29531 --json` | 4 Tasks returned; `task_c862feea878c.spec` complete, 12 334 chars, `=== ORIGINAL_REQUEST ===` present in full |
| `task-show` availability | `orca orchestration --help` | No `task-show` verb in this build; `task-list [--brief] [--json] [--run]` is the version-matched command (its `--help` states `--brief` caps each spec at 160 chars) |
| Historical Final Review Tasks | `task-list --run` for `run_2c614077e685`, `run_e0cdf1afae58`, `run_ec18ea04bc22`, `run_c854db299e7a`, `run_bf55f06dd7fc` | All still retrievable, `legacyReadOnly=false`; Final Review specs `task_c1c4fe5b4310` (9 523), `task_9be1dd68dc2c`, `task_1374a851c17d`, `task_917f1a2fc5d7` all returned in full |
| F1's own worked example | `task-list --run run_c854db299e7a --json` | All three attempt-1 specs present: 14 805 B / 5 553 B / 2 269 B, corroborating `FINAL_RESULT.md:139-145` (~14.8 / 5.5 / 2.3 KB) |
| Preamble retrievability | `orca orchestration dispatch-show --task <id> --preamble --json` | Returns a `preamble` field, but re-rendered: coordinator handle is the **reader's** handle (`term_a79429c1-…`) for all four Tasks tested across three runs whose real coordinators are `term_5b1942be-…`, `None`, `term_14caa2ae-…`; `--dispatch-capability dcap_…` present in the delivered preamble is **absent** from the re-render |
| Retention horizon | `orca orchestration run-list --json --limit {100,150,200}` | ~~Caps at 100 rows; 150 and 200 rejected `invalid_argument`; no run cursor. Visible window reaches back only to 2026-08-21 — horizon indeterminate~~ **Superseded by A-002 below.** The per-page cap is real (150/200 rejected `invalid_argument`), but `--cursor` exists and paginates past it: 248 Runs are currently retrievable back to `2026-08-01T12:20:24Z`. What is indeterminate is the retention *contract*, not the observable history |

The verification also produced one fact the Reviewer did not have: the preamble **is** retrievable
via `dispatch-show --preamble`, but as a re-render that is provably not the delivered bytes. That
is recorded as F1(b) rather than left as an open "not shown to be retrievable," because "returns
something wrong" is a materially different design constraint from "returns nothing."

**Changes made** (`artifacts/runs/run_804e35d29531/ANALYSIS.md`, line numbers post-edit):

1. **`:226-325` — F1 rewritten.** Heading changed from "**NOT reconstructible.** Not partially —
   not at all." to "not held by any repository artifact; the Coordinator-authored Task spec IS
   retrievable from live Orca state; the delivered bytes are not." Body restructured into the
   three layers the correction instruction requires:
   - **(a) `:231-265`** the persisted Task spec retrievable via
     `orca orchestration task-list --run <run_id> --json`, with the verified evidence table, and
     an explicit statement of the three things it does **not** prove (not evidence of delivery;
     excludes the preamble; establishes no retention horizon).
   - **(b) `:267-289`** the dispatch-injected preamble: retrievable in shape via
     `dispatch-show --preamble`, but re-rendered against reader state and divergent from the
     delivered bytes in two verified ways (wrong coordinator handle, missing `dcap_` token).
   - **(c) `:291-325`** durable/export requirements: no repository artifact holds any of it, and
     Orca commits to no retention policy or export guarantee. (The retention wording in this
     sub-item was itself corrected in the A-002 pass below — see that entry for the accurate
     statement.)
   The `ORCHESTRATOR_LOG.md` rows 35-37 evidence block is preserved verbatim. OS-21's F5 is
   explicitly re-scoped rather than deleted: it stands for the durable self-contained record and
   is withdrawn as a claim about absolute recoverability while Orca state survives.
2. **`:758-785` — A-2 first bullet rewritten** from an open unknown to "RESOLVED — it exposes the
   stored Task spec, but not the delivered bytes," replaced by three narrower unknowns
   (delivered-byte equivalence, preamble non-retrievability as delivered, retention horizon +
   non-exportability), with an explicit instruction that DESIGN must not conclude "Orca already
   retains it" removes the artifact requirement.

**Propagated to dependent references** (step 3 of the correction instruction):

3. **`:463` Impact Scope**, §9 artifact-path row — noted that per F1(a) the input artifact's
   *content* can be sourced from Orca state after dispatch, so the row concerns the durable
   path/naming rule rather than a spec-assembly hook.
4. **`:619-623` Risks / R-1**, new bullet "The input snapshot need not touch the spec at all
   (F1(a))" — out-of-band capture makes neutrality shape (a) byte-neutral by construction.
5. **`:654-661` Risks / R-3** — recorded the credential asymmetry F1(b) exposed (delivered
   preamble carries `dcap_`, the re-render does not), while keeping the redaction requirement
   intact for absolute paths, PR URLs and diff content.
6. **`:827-840` Recommended Next Step, Priority 1 (R-1)** — PLAN directed to adopt
   `task-list --run … --json` as the default input-capture mechanism, to label the captured
   object as the *stored Task spec*, and to carry `dispatch-show` metadata as separate delivery
   evidence rather than re-rendered preamble text.
7. **`:854-862` Recommended Next Step, Priority 2 (retention/commit policy)** — added that
   retention is not discharged by Orca, since it guarantees no retention policy and its state does
   not travel with the repository. (The "unmeasurable horizon" justification originally written
   here was corrected in the A-002 pass below.)

**Not changed.** No other finding, risk, assumption or recommendation was touched. F2-F7, R-2 and
R-4-R-7, A-1, A-3 and every other Recommended Next Step item are as the Reviewer approved them.

### A-002 (G1, MAJOR, blocking) — the iteration-2 correction wrongly claimed `run-list` has no cursor and a 100-row visibility ceiling

**Accepted.** This error was introduced *by* the previous correction, not present in the original
ANALYSIS. It asserted that `orca orchestration run-list` caps visible history at 100 rows,
"offers no run cursor," and that Orca's retention horizon is therefore "unmeasurable from here" —
and recorded that as independently verified. The CLI does support cursor pagination, so the
evidence was wrong and the conclusion drawn from it was unsupported.

**Independent verification performed before revising** (the Reviewer's numbers were re-derived
from scratch, not taken on trust):

| Check | Command run this iteration | Result |
|---|---|---|
| Cursor flag exists | `orca orchestration run-list --help` | Usage line reads `run-list [--limit <n>] [--cursor <cursor>] [--json]`; `--cursor <n>` is a documented option |
| Page 1 + cursor emitted | `orca orchestration run-list --json --limit 100` | 100 rows; `result.nextCursor` non-empty (`eyJjcmVhdGVkQXQiOiIyMDI2LTA4LTIxIDE5OjU0OjE3Iiwi…`); newest `2026-08-25T14:16:04Z`, oldest on page `2026-08-21T19:54:17Z` |
| Cursor traversal | same command with `--cursor <nextCursor>`, repeated to exhaustion | page 2 = 100 rows (`2026-08-21T19:54:00Z` → `2026-08-21T15:40:02Z`, cursor emitted); page 3 = 48 rows (`2026-08-21T13:19:10Z` → `2026-08-01T12:20:24Z`, **no** further cursor) |
| Total observable history | union of the three pages | **248 unique Run ids**, no duplicates; oldest row is `run_legacy_local` (`2026-08-01T12:20:24Z`); oldest non-legacy Run is `run_4d0d517ab731` (`2026-08-05T14:00:05Z`) |
| Per-page cap still real | `run-list --json --limit 150`, `--limit 200` | Both rejected `invalid_argument` — confirms a per-page cap, refutes a visibility ceiling |
| Historical Task retrieval at that depth | `orca orchestration task-list --run run_4d0d517ab731 --json` | `ok: true`, `legacyReadOnly: false`, Tasks returned with full `spec` text for a Run created 2026-08-05 |

**Changes made** (`artifacts/runs/run_804e35d29531/ANALYSIS.md`, in place):

1. **F1(c)** — heading changed from "Orca's retention horizon is unknown" to "Orca guarantees no
   retention policy." The "caps at 100 rows / no run cursor / visible window reaches back only to
   2026-08-21 / not measurable from here" paragraph was removed and replaced with the verified
   pagination result above (248 Runs, three pages, oldest `2026-08-01T12:20:24Z`, `task-list`
   still returning full specs at that depth), plus an explicit note that this ANALYSIS previously
   mis-stated it. The `--limit 150/200` rejection is retained and correctly characterised as a
   per-page cap.
2. **A-2, third unknown** — retitled from "retention horizon — still unknown, and unmeasurable
   from here" to "retention *contract* — still unknown, though its current history is
   measurable," and restated: historical Task/Dispatch retrieval reaches the oldest presently
   retained Run, but nothing establishes a policy, minimum horizon, deletion behavior, or export
   guarantee.
3. **Recommended Next Step, Priority 2 (retention/commit policy)** — the dependent justification
   was re-grounded on the accurate premise: the runtime currently exposes 248 Runs via cursor
   pagination but commits to no retention policy, minimum horizon, deletion behavior, or export
   guarantee, and Orca state does not travel with the repository.
4. **A-001 evidence table above** — the "Retention horizon" row is struck through and marked
   superseded, with the corrected finding inline; the two A-001 change-log items that repeated
   the "unmeasurable" wording (items 1(c) and 7) are corrected and cross-referenced here rather
   than rewritten silently.

**Why the OS-22 conclusion survives the correction.** The requirement for a durable, repository-
resident audit artifact never depended on Orca's history being shallow. It rests on two facts
that this verification did not disturb: (i) no retention *contract* exists — 248 observable Runs
describe the present state of one database, not a commitment about next month; and (ii) Orca
state is not exported with the repository, not reviewable in a diff, and unavailable to anyone
holding the repo without this Orca database. Both remain true, so DESIGN's obligation is
unchanged: OS-22's artifact must carry its own durability independently of Orca's runtime
retention.

**Not changed in this pass.** The A-001 correction (persisted Task spec vs. preamble/delivery vs.
durable export) is untouched — the Reviewer confirmed it accurate. F2-F7, Impact Scope (beyond
the F1 propagation already recorded under A-001), Dependencies/Constraints, R-2 and R-4-R-7, A-1,
A-3, and every unaffected Recommended Next Step item are as previously approved.
