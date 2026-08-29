# Worker Result

STATUS: COMPLETE

## Goal

Turn the approved ANALYSIS (`artifacts/runs/run_804e35d29531/ANALYSIS.md`) into an executable
plan for OS-22's DESIGN → IMPLEMENTATION → TEST phases, and settle — now, with rationale — every
product-level decision the ticket left open, so that DESIGN specifies schemas rather than
re-litigating choices.

The thing being built: a **per-dispatch audit record family** for the Final Adversarial Review,
plus a **seeded-defect evaluation fixture with an isolated answer key** and a **scorer whose
metric contract refuses to overclaim**, and **one baseline execution** of the current (unchanged)
Final Review against that fixture.

The thing being *preserved*: the Reviewer. OS-22 observes it; OS-22 does not touch it. Every
decision below is subordinate to that.

---

## Scope / Out of Scope

### In scope

| # | Deliverable | Ticket ref |
|---|---|---|
| S1 | Per-dispatch Final Review audit record (identity, provenance, digests, failure metadata), immutable once written | §1, §3 |
| S2 | Per-dispatch retained **input** artifact (secret-safe, captured out-of-band after dispatch) | §1, §4 |
| S3 | Per-dispatch retained **report** artifact with explicit accepted/voided provenance | §1, §3 |
| S4 | Explicit schema version on every new audit artifact + a reader compatibility rule | §1 |
| S5 | Deterministic redaction with four identity-metadata fields | §4 |
| S6 | Artifact authority statement (`ORCHESTRATOR_LOG.md` = lifecycle; audit records = content; `FINAL_RESULT.md` = references, not duplicates) | §4 |
| S7 | Retention / export / commit policy, explicitly not "commit everything" | §4 |
| S8 | Seeded-defect fixture covering the five archetypes + isolated answer key | §5 |
| S9 | Scorer + metric contract with `UNADJUDICATED` default and precision refusal | §6 |
| S10 | One baseline execution scored against five independent pass criteria | §7 |
| S11 | Full test suite per §9's five groups, including a **byte-level** neutrality proof | §9 |
| S12 | Fix `SKILL.md:1617`'s stale `artifacts/FINAL_REVIEW_*` path and give §16 step 8 enforcement | ANALYSIS Impact Scope |
| S13 | Draft PR `Build Final Review observability and evaluation foundation` referencing Jira OS-22 | Git/PR |

### Out of scope (do not plan, do not drift into)

- OS-23 detection/search-quality improvements of any kind.
- A Final Review falsification or search-depth policy. **Specifically: do not create
  `reviews/final_review.md`**, even though ANALYSIS F7 shows the slot is empty.
- Reviewer/model optimization or agent-profile routing changes.
- Any conclusion, ranking, or partial verdict on H-1 / H-2 / H-4 / H-5.
- Unrelated lifecycle changes: `RESULT:` stays two-valued, `REVIEW_VERDICT:` stays four-valued,
  `PASS WITH NOTES` / `BLOCKED` stay annotations, correction and downstream-revalidation
  semantics are untouched, Risk/Quality-Profile/Agent-Profile semantics are untouched.
- Hard-coding any observed `agent_prompt_blocked` size number (14 805 / 5 553 / 2 269 bytes, or
  their KB roundings) as a product constant. ANALYSIS F6 confirms nothing today does; OS-22 must
  not be the first.
- Deleting, compacting, or garbage-collecting existing run artifacts (`SKILL.md:1204-1205`
  defers retention/archive to OS-8; OS-22 adds no deletion path).
- `VERSION`, `LICENSE-DECISION.md`. No merge.
- Trimming §16's existing four-axis `## Orca Orchestration State` serialization — see DEC-5.

---

## Decisions

Ten decisions, one per open question the Task named. Each states the choice, the rationale, and
what DESIGN is left to specify. DESIGN may refine spelling and structure; it may not reopen the
choice.

### DEC-1 — Observability Neutrality shape: **out-of-band capture, zero mutation of the dispatched spec** (ANALYSIS R-1 shape (a), extended by (c) on the report side)

**Choice.**

1. `phase_artifact_contract()` keeps returning `<ARTIFACT_ROOT>FINAL_REVIEW.md` for
   `final_review`. **No argument is added to `render_task_spec()`. No block is added to the
   spec. No byte of the Task spec changes.**
2. The retained **input** is captured *after* the dispatch call returns, by reading the stored
   Task spec back out of Orca:
   `orca orchestration task-list --run <run_id> --json` (ANALYSIS F1(a), verified against five
   historical runs). Nothing in the capture path touches assembly.
3. The retained **report** uses the two-layer form: the Reviewer writes to its contract path as
   it always has; the Coordinator, immediately after settlement, snapshots that file to an
   immutable per-dispatch audit path together with its provenance. The Reviewer never learns the
   audit path exists.

**Neutrality claim being made — stated precisely, because §2 requires it to be verifiable:**

> For every dispatched Task spec in every workflow this repository can replay, the bytes produced
> by `scripts/task_context.py::render_task_spec()` at OS-22's merge commit are **character-for-
> character identical** to the bytes produced at `1045815` (the pre-OS-22 commit). This is a
> byte-identity claim, not a "semantic content" claim, and therefore needs no definition of
> "semantic".

**The named check that proves it.** `FinalReviewObservabilityNeutralityTests` in
`scripts/test_e2e_harness.py`, comparing current output against a new golden fixture
`scripts/fixtures/os22_neutrality/pre_os22_task_specs.json`, generated with the existing
`capture_legacy_artifacts()` technique (`scripts/test_e2e_harness.py:1139-1160`) run inside a
`git archive 1045815` checkout — the same procedure that produced
`scripts/fixtures/legacy_baseline/pre_os4_artifacts.json`
(`scripts/fixtures/legacy_baseline/README.md`). Two constraints on it:

- It must be a **new, separate** capture function and a **new, separate** fixture file. Extending
  `capture_legacy_artifacts()` or `pre_os4_artifacts.json` in place would change the input to
  `LegacyByteIdentityTests` and destroy the OS-4 evidence it exists to hold.
- Its coverage must include a **`final_review` Task spec**, which `GOLDEN_WORKFLOWS` does not
  currently emit. Adding that is a test-side capture change, not a product change.

Two supporting assertions in the same test class, because a golden file only catches what it
captures:

- `render_task_spec()`'s signature has no parameter that did not exist at `1045815`.
- The audit/redaction module is not importable from — and not called on — any code path between
  spec assembly and the dispatch call. (Enforced structurally: see DEC-4's ordering invariant.)

**Rationale.** Shape (b) — changing `artifact_contract` and arguing a path is not "semantic
content" — was rejected because it makes the strongest available proof impossible and replaces it
with an argument. Shape (a) is byte-neutral *by construction* rather than by claim, and F1(a)
removed the only reason it was ever hard: the input no longer has to be intercepted at assembly,
because Orca returns the stored spec on request afterwards. The residual cost of (a) — the
Reviewer still writes to a colliding path — is exactly what layer 3 absorbs, and it absorbs it
better than a path change would (see DEC-10).

**Honest limit, which must be carried into the artifact itself.** What is captured is the
**stored Task spec**, not the delivered bytes. `agent_prompt_blocked` is a proven counter-example
where the two differ (ANALYSIS F1(a) limit 1). The audit record must therefore label this field
as the stored spec, and must carry `dispatch-show` **metadata** (dispatch id, dispatched_at,
capability digest, contract version) as *separate delivery evidence* — never re-rendered preamble
text, which ANALYSIS F1(b) proved is partly fabricated (wrong coordinator handle, missing
`dcap_`).

**DESIGN specifies:** the capture command invocation and its failure handling; the field names
that distinguish stored-spec from delivery-evidence; the exact golden-fixture generation script.

### DEC-2 — Audit identity key and provenance model

**Identity key: the Dispatch. Attempt is a grouping, not a key.**

The audit record's identity is the tuple `(run_id, final_review_attempt, task_id, dispatch_id)`,
and the record's filename must be derived from a component that is unique per dispatch. Attempt
number alone is provably insufficient: `run_c854db299e7a` attempt 1 had three dispatches, two
reports and one accepted verdict (ANALYSIS F2b). A retry never reuses a record path; the writer
**refuses to overwrite an existing record** and reports the collision rather than clobbering it.

**Provenance: two fields, not one flat enum.**

```text
provenance_state : accepted | voided | unknown        (fail-closed default: unknown)
void_reason      : required and non-empty iff state == voided; empty otherwise
```

- `unknown` is a **member of `provenance_state`**, not a separate absence-state. Absence of the
  field, an unparseable record, and an explicit `unknown` all read as `unknown`. One state
  machine, not two. There is no code path that defaults to `accepted` (ANALYSIS R-2: a
  provenance field defaulting to `accepted` would silently bless voided reports — worse than no
  field at all).
- Two fields rather than one because the observed causes **compose**: `run_c854db299e7a`
  attempt 1a failed at `dispatch_input` carrying `agent_prompt_blocked`, and then its
  `worker_done` was separately rejected as `dispatch_capability_invalid` (ANALYSIS A-1.1). A flat
  enum has to discard one of those.

**Proposed `void_reason` set** — the ticket's three examples are illustrative; this is the set the
observed dispatch-failure shapes actually require, and DESIGN formalizes the spelling:

| value | meaning | evidence it is needed |
|---|---|---|
| `dispatch_input_rejected` | the dispatch was refused at input | ANALYSIS F1 / `run_c854db299e7a` row 35 |
| `dispatch_capability_invalid` | the dispatch capability was refused | `run_c854db299e7a`, `worker_done` rejection |
| `settlement_failure` | dispatched, never reached a settled outcome | ticket §3 example |
| `superseded_by_retry` | a later dispatch of the same attempt produced the accepted verdict; this output is forensic only | ANALYSIS F2b — the ticket's example list omits it, and it is the one that actually describes the observed accident |
| `report_malformed` | settled with a report that does not satisfy the §11/§17 report contract | ANALYSIS A-1.2; `SKILL.md:1126-1128` already contemplates malformed responses |
| `report_missing` | settled, no report artifact produced | ANALYSIS F2a — `run_ec18ea04bc22` shipped with two attempts and zero reports |

The runtime's own failure label (e.g. the literal string `agent_prompt_blocked`) goes in a
free-text `failure_detail` field alongside `observed_input_bytes`, **not** into the enum. That
keeps §3's "observed input size / failure metadata" requirement satisfied while keeping a
build-specific runtime label out of product vocabulary (ANALYSIS F6).

**Attachment.** Provenance attaches to the **dispatch**. An attempt's accepted verdict is derived:
exactly zero or one dispatch in an attempt group may carry `accepted`; a second is a contract
violation the reader reports rather than resolves.

**Separate audit file, not a widened `ORCHESTRATOR_LOG.md`.**

`ORCHESTRATOR_LOG_COLUMNS` gains **no column**. Adding one changes the header width of every
future table while every existing file on disk keeps the old width, and there is no in-repo reader
today that could be taught to tolerate both (ANALYSIS D-2.5). Instead the log gains **rows**, using
the already-open `--event` vocabulary (`run_logging.py:872` has no `choices`; the field has
already been extended in production with `pr_created` and
`external_review_correction_triggered`). The join is free: the log's existing `task_id` and
`dispatch_id` columns are the audit record's own key, so the §9 test "log ↔ input ↔ report
identity consistency" is a join on columns that already exist.

**DESIGN specifies:** field names, filename derivation, the new event value spellings, and the
reader's collision/violation reporting shape.

### DEC-3 — Schema versioning

**Choice:** an explicit `schema_version` field, **first key of every audit record**, in JSON,
of the form `"<MAJOR>.<MINOR>"`, starting at `1.0`.

- **JSON**, because it is the only structured format with stdlib support under the repository's
  standard-library-only constraint (`COMPATIBILITY.md`), and it has precedent
  (`.timing_state.json`, `pre_os4_artifacts.json`).
- **In-file field, not filename and not sidecar.** §1 explicitly requires that consumers not
  depend on Markdown prose or filename convention; a filename-encoded version dies on the first
  rename, and a sidecar version file is a second thing that can go stale against the thing it
  describes.
- **Independent of `VERSION`.** The repository `VERSION` must not change in this ticket, and
  release cadence is not audit-schema cadence. The constant lives in `run_logging.py` as
  `FINAL_REVIEW_AUDIT_SCHEMA_VERSION`, is mirrored into
  `orca-worker-reviewer-orchestration/tools/run_logging.py` by the existing byte-parity rule, and
  is asserted equal to the value stated in `SKILL.md` §9 by a validator.

**Reader compatibility rule (this *is* the version contract, so it is a decision, not a detail):**

```text
unknown MAJOR  -> refuse to interpret; report UNKNOWN; never infer provenance
unknown MINOR  -> read the fields you know, ignore fields you do not
missing field  -> the record is malformed; provenance reads unknown (DEC-2)
```

MINOR is bumped for additive fields, MAJOR for any change to the meaning of an existing field.
Every new field must be additive-with-default so MINOR suffices.

**DESIGN specifies:** the full v1.0 field list and which fields are required vs optional.

### DEC-4 — Secret-safe redaction

**Where in the pipeline — this is an invariant, not a preference:**

```text
assemble spec -> DISPATCH -> (dispatch returns) -> capture stored spec -> redact -> digest -> write
                     ^                                                      ^
                     |                                                      |
        redaction MUST NOT appear anywhere left of here      redaction applies only here
```

Redaction applies **only to the retained copy, only after the dispatch call has returned**. It is
never in the path between assembly and dispatch. This is what makes DEC-1's byte-identity claim
true rather than argued (ANALYSIS R-1). It is enforced two ways: the capture source is a separate
CLI read that cannot run before dispatch, and a test asserts the redaction module is not reachable
from the dispatch path.

**Deterministic:** the redactor is a pure function of `(input_bytes, policy_version)`. No
randomness, no salt, no clock, no path-dependent state. The same input under the same policy
yields byte-identical output and identical digests, on any machine.

**Raw bytes are never written to disk.** The captured pre-redaction bytes exist in memory only;
only their digest is persisted. This deletes an entire class of leak (a staging file, a temp file,
a crash dump of one) rather than mitigating it.

**The four identity-metadata fields — required as a set, because none of them is verifiable alone:**

| field | what it is | why the set needs it |
|---|---|---|
| `input_digest_pre_redaction` | SHA-256 of the exact captured bytes before redaction | the only surviving identity of the unredacted input; lets two captures of the same spec be proven equal without retaining either |
| `redaction_policy_version` | e.g. `"redaction/1.0"`, versioned independently of `schema_version` | a digest is only comparable against a digest produced by the *same* policy; without this, digest equality is meaningless across policy changes |
| `artifact_digest_post_redaction` | SHA-256 of the retained bytes exactly as written | the only field that is re-derivable from the artifact on disk, so it is the tamper/corruption check |
| `redactions` | ordered `{category, count}` occurrence records | says *that* and *what kind of* substitution happened, so a reader knows the retained text is not verbatim, and how it differs in kind |

`redactions` must not contain the redacted value or any reversible encoding of it. A per-occurrence
digest is permitted; the value is not.

**Initial categories** (small on purpose; DESIGN formalizes replacement tokens):
`orca_dispatch_capability` (the `dcap_` token — a real credential present in every delivered
preamble, ANALYSIS R-3), `absolute_local_path` (`/Users/<name>/…` — `validate_skills.py:810`
already polices this for Skill files but not for run artifacts), `url_credential`,
`env_secret_pattern`. Terminal handles and Task/Dispatch ids are **not** redacted: §1 explicitly
requires reviewer terminal identity, and they are identifiers, not credentials.

**What "redaction 전후 identity 검증 가능" means operationally, since raw bytes are not retained:**
given the same source input, re-running the pipeline reproduces both digests and the same
`redactions` record. That is the testable property, and DEC-4's determinism requirement is what
makes it hold.

**DESIGN specifies:** the pattern set per category, replacement token spellings, and whether
per-occurrence offsets are recorded.

### DEC-5 — Artifact authority boundaries

Three authorities, stated in §9 and enforced by a validator anchor:

1. **`ORCHESTRATOR_LOG.md` — authoritative, append-only, for run lifecycle provenance.**
   Plus the reader rule ANALYSIS F5b showed is missing: **`run_end` is not terminal.**
   `run_2c614077e685` has a `COMPLETED` row followed by six hours of further rows, so a consumer
   that stops at the first `run_end` silently drops six of eight Final Review attempts. The rule
   OS-22 documents: *a reader reads the whole file; the authoritative status is the last
   `run_status` row; rows after a `run_end` are valid and mean the run continued; a later
   `run_end` supersedes an earlier one.* This is a reader contract in prose — **no writer change,
   no schema change, no behaviour change.**

2. **The per-dispatch audit records — authoritative for attempt content** (input, report,
   findings, verdict, provenance). Where a summary and an audit record disagree, the audit record
   wins, and a reader must say so rather than reconcile silently.

3. **`FINAL_RESULT.md` (§16) — a summary that references.** OS-22 adds a *requirement* to §16's
   `## Final Adversarial Review` block: per attempt, cite `task_id`, `dispatch_id`,
   `provenance_state`, and the audit record path; and **do not assert a finding-level claim that
   no retained reviewer artifact supports.** That is the direct fix for ANALYSIS F5a, where
   `run_c854db299e7a/FINAL_RESULT.md:156-170` wrote `FINAL_FINDINGS: none` plus a claim
   contradicting a voided finding, with no reviewer artifact behind it.

**Scope judgement the ticket demands (ANALYSIS Priority 2 item iii): do NOT trim §16's existing
four-axis `## Orca Orchestration State` serialization in OS-22.** Rationale: that ledger is
required by §8's preserved invariants (per-Dispatch four-axis outcomes are checked at
`SKILL.md:1614`), and removing it is a reporting-semantics change — squarely inside "unrelated
lifecycle 변경", which the ticket excludes. §4 asks that the summary *reference rather than
exhaustively duplicate*, and adding the reference requirement achieves that **additively**.
Trimming the duplication is recorded as a follow-up (OS-23 or backlog), not silently skipped.

### DEC-6 — Retention, export, versioning of run artifacts

**Does every run artifact get committed to git by default? No.** The ticket forbids it and this
plan does not introduce it.

**The actual default, stated positively:**

1. **Write-to-disk only.** Audit records are written under `<ARTIFACT_ROOT>` and **nothing in the
   workflow runs `git add`.** No IMPLEMENTATION work item may add an automatic `git add`, commit,
   or push of run artifacts. (This is already true of the current code; OS-22's contribution is
   to make it a *stated policy* instead of an accident, so DESIGN cannot add one by reflex.)
2. **`artifacts/runs/` is not gitignored, and OS-22 does not gitignore it.** ANALYSIS A-2 flags
   the temptation to copy `.gitignore`'s `artifacts/**/.timing_state.json` line by reflex — that
   would be exactly backwards, since audit artifacts *are* the evidence. Committing stays a
   deliberate per-run human decision, which is what it already is in practice (1 of 6 runs on
   disk is tracked, ANALYSIS CS-8).
3. **Minimum evidence subset**, defined per §4 as what effectiveness validation actually needs —
   per Final Review attempt: the per-dispatch audit records, the retained redacted input, the
   retained report, and the run's `ORCHESTRATOR_LOG.md`.
4. **Export is an explicit, opt-in command**, emitting that subset as a single versioned JSON
   bundle a human can attach to a PR or commit. Durability and committing are separated: durable
   means written, self-describing, versioned, secret-safe and exportable — it does not mean
   auto-committed.
5. **No deletion, no compaction, no GC.** `SKILL.md:1204-1205` defers retention/archive to OS-8;
   OS-22 adds no deletion path and no horizon. Stated so DESIGN does not invent one.
6. **Durability does not rest on Orca.** ANALYSIS F1(c): the runtime currently exposes 248 Runs
   via cursor pagination but commits to no retention policy, no minimum horizon, no deletion
   behaviour and no export guarantee, and Orca state does not travel with the repository. Orca is
   the best *source* for the capture; it is not a substitute for the artifact. DESIGN must not
   conclude "Orca already retains it."

**DESIGN specifies:** the export bundle schema and the export subcommand's surface.

### DEC-7 — Fixture and answer-key architecture

**Storage separation, decided before any defect content is designed** (ANALYSIS Priority 3: the
leak paths are structural):

```text
scripts/fixtures/final_review_eval/
    subject/        <- the tree a Reviewer is pointed at. Source, diff, tests, contract docs.
                       No key. No README describing defects. No marker comments. No defect ids.
    key/            <- answer key. Never inside subject/. Never referenced from subject/.
    adjudications/  <- independent adjudication inputs (DEC-8). Also never inside subject/.
```

**Four mitigations, of which two are mechanically enforced:**

1. *Path separation.* The key is never inside `subject/`, and `subject/` contains no reference to
   the key path, the key filename, or any answer-key token. **Enforced by test:** scan `subject/`
   for every token in the key and for the key's path; fail on any hit.
2. *Materialized subject workspace.* The baseline Reviewer is pointed at a materialized copy of
   `subject/` in a scratch workspace containing no `.git` and no key. This closes ANALYSIS R-4.2
   (git history) and R-4.1 (`ls` next door) for the dispatched scope.
3. *Leak detection on the retained input.* **Enforced by test:** scan the OS-22 retained input
   artifact for the reviewer's dispatch for any answer-key token and for any expected-count
   statement; fail on any hit. This is §9's "Reviewer input에 answer key/expected count가 노출되지
   않음" made checkable — and note that it is checkable *only because* OS-22 now retains the
   input. The audit machinery is the leak detector for its own evaluation.
4. *Commit hygiene.* The `subject/` tree and the `key/` tree land in **separate commits**, and the
   `subject/` commit message names only the fixture — never what it seeds.

**Accepted, documented limitation.** `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted`
(`SKILL.md:1307`) means no in-repo storage layout can make the key *unreachable* by a reviewer
that decides to go looking outside its scope. The claim OS-22 makes is therefore the one it can
prove: **no key material appears in the reviewer's input**, verified mechanically per baseline
run. It is not "the key was unreachable." Stating this honestly is a requirement of the plan, not
a caveat to be dropped in DESIGN.

**Per-defect justification DESIGN must produce.** For each of the five archetypes —
value-vs-presence, omitted call-site/propagation, equality/boundary, losing precedence/fallback,
validation-scope gap — DESIGN records four things:

- (a) the archetype it instantiates;
- (b) the exact seeded change, as a diff;
- (c) **the negative-space argument**: why no single grep of `subject/` for a suspicious token
  localizes it, naming the evidence path a reviewer must actually cross-read (which source, which
  call site, which test, which contract doc). ANALYSIS R-5: these archetypes are defects of
  *absence*, which is what makes them search-resistant and also what makes them easy to seed
  wrongly;
- (d) the **matching criterion** the scorer uses to decide a finding matched it — location plus
  claim, never string equality against the key's own wording.

**Second-order risk to hold** (ANALYSIS R-5): the archetypes came from this repository's real
defects. A fixture that *is* this repository's code risks reviewer recognition; a fully synthetic
one risks not representing the archetype. DESIGN must state where on that line each defect sits.

### DEC-8 — Metric contract: `UNADJUDICATED` default and the precision refusal rule

**These are hard constraints. DESIGN and IMPLEMENTATION must not soften them, and a Reviewer
should treat any softening as a §9 test failure, not a judgement call.**

1. Any finding not matched to an answer-key entry is classified **`UNADJUDICATED`**. There is no
   code path, flag, or configuration that maps unmatched → false positive.
2. `precision` and `false_positive_rate` are **not computed** unless one of two preconditions is
   explicitly recorded in the scorer's input:
   - (i) `closed_world: true` with an exhaustive-adjudication attestation covering the whole
     evaluation scope; or
   - (ii) every unmatched finding carries an independent adjudication verdict.
   Otherwise the scorer emits `precision: null` with `precision_status: REFUSED` and a
   machine-readable reason, and **exits non-zero if precision was explicitly requested**. Not
   estimated. Not defaulted to zero. Not silently omitted.
3. **Recall is always computable** — the seeded-defect denominator is known from the key — and is
   always reported with that denominator explicit, so recall can never be mistaken for a
   whole-population metric.
4. **No historical-corpus heuristic.** The adjudication input schema accepts only an explicit
   adjudicator verdict plus rationale. There is no field for "was corrected" or "was not
   disputed", so §6's forbidden inference is not merely discouraged — it is unrepresentable.
5. **`UNADJUDICATED` counts accompany every metric block**, so a partial adjudication can never
   read as a complete one.
6. **Verdict reproducibility** is reported as observed agreement across repeated runs, with the
   run count stated; it is never asserted from a single run.

Metrics emitted (§6): `seeded_defects_total`, `detected_seeded_defects`, `seeded_recall`,
`miss_count`, `miss_rate`, `matched_findings`, `unmatched_findings`,
`adjudicated_true_positives`, `adjudicated_false_positives`, `evidence_grounding`,
`verdict_reproducibility`, plus `precision` / `false_positive_rate` gated by rule 2.

`evidence_grounding` is defined concretely so it is mechanical: the fraction of a report's
findings whose `Location` / `Evidence` fields resolve to a real file and line in `subject/`.

**Rationale for the strictness.** OS-21's own external review (EXT-1, commit `1045815`) was a
MAJOR finding for exactly this class of overclaim. The scorer must make the overclaim
*impossible*, not rely on the operator to avoid it.

**DESIGN specifies:** the findings/key/adjudication input schemas, the matching algorithm, the
metric output schema, and exit codes.

### DEC-9 — Baseline execution plan (§7): five independent pass criteria, satisfied only by a settled Reviewer execution

One baseline run of the **current, unchanged** Final Review against `subject/`. Its purpose is
that the machinery works, not that the Reviewer is good.

**The precondition all five criteria sit on top of:** §7 asks for the current Final Review baseline
to be *executed*, and §5 asks for the evaluation procedure to actually run and produce scoring
work. Both require a Reviewer execution that **settles with a usable report** — a report the scorer
can consume against the fixture's answer key. A dispatch rejected before the Reviewer ever ran
produces no such report, so it cannot satisfy §7 no matter how well its failure was captured.
Baseline success is therefore evaluated only over dispatches that settled with a usable report.

| # | Criterion | Passes when | Fails when |
|---|---|---|---|
| B1 | **Procedure ran** | the documented steps executed end to end, **including at least one Final Review dispatch that settled with a usable report** | a step could not be executed as documented, or no dispatch settled with a usable report |
| B2 | **Scoring worked** | the scorer consumed the report + key and emitted the full metric block, with `precision_status: REFUSED` unless a precondition was met | the scorer crashed, or silently produced a precision it was not entitled to |
| B3 | **Artifacts produced** | per-dispatch input + report + provenance records exist; `artifact_digest_post_redaction` re-verifies against the file on disk; the log ↔ input ↔ report join succeeds on `task_id`/`dispatch_id` | any record missing, any digest mismatch, any join gap |
| B4 | **No answer-key leak** | the mechanical scan of the retained reviewer input finds zero key tokens and zero expected-count statements | any hit |
| B5 | **Reproducible** | re-running the scorer on the same stored reviewer output reproduces byte-identical metrics, and the run's inputs are recorded well enough to re-issue the dispatch | metrics differ across identical inputs |

**Recorded separately and explicitly:**

- The Reviewer's **verdict** is an *observation*, not a pass criterion. A baseline where the
  Reviewer returns FAIL, or misses every seeded defect, still passes B1-B5.
- A **dispatch-layer failure** (`agent_prompt_blocked`, `dispatch_capability_invalid`,
  `settlement_failure`, …) is **always retained as forensic §3 evidence** — pre-failure input
  evidence, the retry input, a **separate Task/Dispatch identity per retry** (DEC-2's identity
  model: provenance attaches to the dispatch, retries are never merged into the original), and the
  `observed_input_bytes` / `failure_detail` metadata. Capturing it is precisely what §3 asked for,
  and that capture is valid §3 evidence whatever the baseline outcome turns out to be.
- **A captured dispatch-layer failure never, by itself, satisfies the §7 baseline.** §3
  (failure evidence preserved) and §7 (baseline executed) are separate requirements and are scored
  separately. On a dispatch-layer failure the baseline **continues**: retry under a new
  Task/Dispatch identity, preserving the failed dispatch's records untouched. The §7 baseline is
  satisfied only once **at least one retry settles with a scoreable Reviewer report**, the scorer
  has run on that report, and **all five criteria B1-B5 pass**.
- **Retries are budget-bounded**, consistent with this Skill's max-iterations semantics
  (`DEFAULT_MAX_ITERATIONS = 5`, terminal reason `FINAL_REVIEW_MAX_ITERATIONS_REACHED`,
  `SKILL.md:140`, `:1511-1527`). If the budget is exhausted — or the baseline otherwise ends with
  no dispatch that settled with a usable report — **the §7 baseline FAILS** and must be reported as
  a FAIL, naming the exhausted budget and the retained failed dispatches. It must not be written up
  as a pass, a partial pass, or a "captured outcome". The failed-dispatch evidence is still
  preserved and still discharges §3; §3 passing does not carry §7.
- ANALYSIS R-6's observed base rate is non-trivial (`run_c854db299e7a` needed three dispatches for
  one attempt), which is the reason the retry path is planned in advance rather than improvised.
- **No detection-quality conclusion is drawn from this run**, and no comparison of H-4 vs H-5.
  The baseline's recorded output is a reference point for OS-23, nothing more. The written
  baseline report must say so in those terms.

### DEC-10 — The two existing defects ANALYSIS flags

**(i) `SKILL.md:1617`'s stale `artifacts/FINAL_REVIEW_*` — FIX IT, and give it enforcement.**

§16 step 8 still names the pre-run-scoping repository-root path, contradicting §9's
`<ARTIFACT_ROOT>` ladder. It is corrected to `<ARTIFACT_ROOT>FINAL_REVIEW*`. It is byte-neutral
with respect to DEC-1's claim: §16 is Coordinator-side final verification and its text is not
rendered into any Task spec, so `render_task_spec()` output is unaffected — the neutrality golden
proves this rather than assuming it.

Beyond the text fix: step 8's per-attempt artifact check is **contract prose with no enforcement
anywhere**, and ANALYSIS F2a is a run (`run_ec18ea04bc22`, OS-19, PR #17) that shipped
`COMPLETED` with two Final Review attempts and zero report files because nobody performed it.
OS-22 adds a validator anchor asserting §16 step 8 names `<ARTIFACT_ROOT>`, so the stale path
cannot silently return.

**(ii) `task_context.py:304-306` vs `e2e_harness.py:422-433` disagreeing about per-attempt
suffixing — DEFER, deliberately and on the record.**

`phase_artifact_contract()` returns `FINAL_REVIEW.md` for every attempt while `e2e_harness.py`
implements §9's suffix rule. Two in-repo implementations of one rule, and the one feeding real
dispatched specs is the one that never suffixes. It is a real conformance defect. It is **not
fixed in OS-22**, for three reasons in descending weight:

1. **It does not fix the problem OS-22 is here for.** The suffix keys on *attempt*; the observed
   overwrite (ANALYSIS F2b) came from three *dispatches within one attempt*, which all write the
   same path with or without suffixing. Fixing the suffix would leave F2b exactly as it is.
2. **It is the single change that would break the byte-identity neutrality proof.**
   `artifact_contract` is a layer-1 `TASK_BOUNDARY_KEYS` value rendered inline into the spec
   (`task_context.py:38-48`, `:606-610`); changing it changes reviewer-visible bytes for attempt
   N≥2, and DEC-1's proof would have to be downgraded to an argument about "semantic content".
3. **DEC-2 makes the canonical path non-authoritative for content anyway.** Once the immutable
   per-dispatch audit record exists, `FINAL_REVIEW.md` holding a voided report is a stale
   convenience copy, not a lost verdict — which is the actual harm F2b caused.

Recorded as a follow-up (OS-23 or backlog) with this rationale, not left ambiguous and not
silently dropped. **If a later phase or Reviewer overturns this deferral**, the fix must land as
its own commit and the neutrality golden must be regenerated at that commit with the delta
documented as a §9-conformance correction, explicitly not as an observability change.

---

## Work Items

Prefixes: **D** = DESIGN phase, **I** = IMPLEMENTATION, **T** = TEST, **B** = baseline.
`run_logging.py` appears once but always means *both* copies — `scripts/run_logging.py` and
`orca-worker-reviewer-orchestration/tools/run_logging.py` are byte-parity-enforced
(`validate_skills.py:1944-1971`).

### DESIGN

| id | item | output |
|---|---|---|
| D-A | Audit record schema v1.0: full field list, required vs optional, filename derivation from the dispatch key, immutability/collision rule, `schema_version` placement, reader compatibility rule (DEC-2, DEC-3) | schema spec + example record |
| D-B | Provenance state machine: which lifecycle event sets which `provenance_state`/`void_reason`, final enum spelling, the "at most one `accepted` per attempt" reader rule (DEC-2) | state table |
| D-C | Redaction policy v1.0: categories, patterns, replacement tokens, digest algorithm, `redactions` record shape, in-memory-only raw handling, ordering invariant (DEC-4) | policy spec |
| D-D | Fixture design: `subject/` tree, five seeded defects each with (a) archetype (b) diff (c) negative-space argument (d) matching criterion; answer-key schema; materialized-workspace protocol (DEC-7) | fixture spec + key schema |
| D-E | Scorer contract: findings / key / adjudication input schemas, matching algorithm, metric output schema, refusal semantics, exit codes (DEC-8) | contract spec |
| D-F | Export bundle schema + minimum evidence subset serialization (DEC-6) | bundle spec |

### IMPLEMENTATION

| id | item | touches | notes |
|---|---|---|---|
| **I-0** | **Generate the pre-OS-22 neutrality golden** `scripts/fixtures/os22_neutrality/pre_os22_task_specs.json` from a `git archive 1045815` checkout, using a **new** capture function covering a `final_review` spec | `scripts/test_e2e_harness.py`, new fixture dir | **Must land before any product change.** A golden generated after implementation is worthless |
| I-1 | Audit record writer/reader in `run_logging.py`: `FINAL_REVIEW_AUDIT_SCHEMA_VERSION`, digest helpers, immutable write that refuses overwrite, fail-closed read | `run_logging.py` ×2 | stdlib only; **zero `scripts/` imports** (`run_logging.py:17-27`) |
| I-2 | Redaction module, same file: deterministic, policy-versioned, four identity fields, raw bytes never written | `run_logging.py` ×2 | keeping it here means **no new shipped file**, so `release_manifest.py` needs no change |
| I-3 | Input capture: post-dispatch `task-list --run … --json` read + `dispatch-show` metadata as separate delivery evidence | `run_logging.py` ×2 | never re-rendered preamble text (ANALYSIS F1(b)) |
| I-4 | New open-vocabulary `ORCHESTRATOR_LOG.md` **rows** at the audit points, reusing existing `task_id`/`dispatch_id`/`detail` columns | `run_logging.py` ×2 | **no column added** |
| I-5 | Export subcommand emitting the minimum evidence subset (D-F) | `run_logging.py` ×2 | no `git add`, ever |
| I-6 | `SKILL.md` §9: new `#### Final Review audit artifacts` subsection — path rule, three authorities, schema-version rule, `run_end`-is-not-terminal reader rule, retention/commit policy | orchestration `SKILL.md` | OS-17 precedent: §9 was extended this way before. Orchestration skill only — the loop skill has no run-scoped log |
| I-7 | `SKILL.md` §17: input/report paragraphs gain the audit obligation; `#### Final review contract` gains 2 keys (`FINAL_REVIEW_AUDIT_RECORD`, `FINAL_REVIEW_PROVENANCE_DEFAULT`) | orchestration `SKILL.md` | **three-place coordinated edit** — see I-9 |
| I-8 | `SKILL.md` §16: fix step 8's stale path to `<ARTIFACT_ROOT>FINAL_REVIEW*`; add the audit-record reference requirement to `## Final Adversarial Review` (DEC-5, DEC-10i) | orchestration `SKILL.md` | four-axis ledger untouched |
| I-9 | `validate_skills.py`: update `FINAL_REVIEW_CONTRACT` dict, raise `FINAL_REVIEW_CONTRACT_MAX_LINES` 15→17, add `validate_final_review_audit_contract()`, add the §16-step-8 `<ARTIFACT_ROOT>` anchor | `scripts/validate_skills.py` | must land in the **same commit** as I-7/I-8 or validation fails |
| I-10 | Emit audit records at the existing final-review dispatch/settlement points | `scripts/orca_runtime_harness.py` (`:2065-2245`), `scripts/e2e_harness.py` (`:1486-1500`) | audit-write failure must never mutate settled lifecycle state (`SKILL.md:1199-1201`) |
| I-11 | Fixture `subject/` tree + five seeded defects (commit 1), answer key + adjudications (commit 2) | `scripts/fixtures/final_review_eval/` | separate commits, DEC-7.4 |
| I-12 | Scorer `scripts/final_review_eval.py` implementing D-E | new file in `scripts/` | repo-side, not shipped in the Skill |
| I-13 | Docs: `README.md` "Run-Scoped Artifacts and Logs" (`:136-160`), `CHANGELOG.md`, `COMPATIBILITY.md` | — | house convention on every prior OS-* ticket |
| I-14 | Draft PR `Build Final Review observability and evaluation foundation`, referencing Jira OS-22, on `agent/final-review-observability-evaluation`. **No merge. `VERSION`/`LICENSE-DECISION.md` untouched** | — | |

### TEST — mapped one-to-one onto §9's five groups

| id | group | cases |
|---|---|---|
| T-1 | **Audit / provenance** | per-dispatch input artifact created; per-dispatch report artifact created; retry/correction produces a *new* record and the writer **refuses** to overwrite; `accepted` provenance path; each `void_reason` path; a voided report is never returned as an accepted verdict; log ↔ input ↔ report identity join on `task_id`/`dispatch_id` |
| T-2 | **Failure handling** | a dispatch-input failure still preserves the pre-failure input evidence; a retry is recorded under a **separate** task/dispatch identity and is not merged with the original; the retained failure record satisfies §3 while leaving the §7 baseline unsatisfied until a dispatch settles with a usable report; a malformed/incomplete record reads `unknown`, never `accepted`; `observed_input_bytes` + `failure_detail` recorded; **guard test: the implementation contains none of `14805`/`5553`/`2269`/`14.8`/`5.5`/`2.3` as a threshold constant** (ANALYSIS F6) |
| T-3 | **Security** | redaction is deterministic (same input twice → identical bytes and digests); `artifact_digest_post_redaction` re-hashes the file on disk; a synthetic `dcap_…` token and a `/Users/<name>/…` path do not survive into the retained artifact; `redactions` carries no redacted value; pre/post identity re-derivable by re-running the pipeline on the same source |
| T-4 | **Evaluation** | each intended seeded defect actually exists in `subject/` (demonstrated, not asserted); answer-key correctness; `subject/` contains no key token or key path; **the retained reviewer input contains no key token and no expected-count statement**; recall computed with an explicit denominator; an unmatched finding is `UNADJUDICATED` and never auto-FP; precision is **refused** (with non-zero exit when requested) under insufficient adjudication; **the baseline is recorded PASS only when a dispatch settled with a usable report, the scorer ran on it, and B1-B5 all pass — a captured dispatch-layer failure with no settled report is recorded as a §7 baseline FAIL, never as a pass** |
| T-5 | **Regression** | full `python3 -m unittest discover -s scripts -p 'test_*.py'` green; `python3 scripts/validate_skills.py` green; `python3 scripts/verify_package.py` green; existing lifecycle / Risk / Quality Profile / Agent Profile tests untouched and passing |
| T-6 | **Neutrality (DEC-1)** | `FinalReviewObservabilityNeutralityTests`: current `render_task_spec()` output byte-identical to `pre_os22_task_specs.json` across all captured workflows **including a `final_review` spec**; `render_task_spec()` gained no parameter; the redaction/audit module is not reachable from the spec-assembly→dispatch path; `LegacyByteIdentityTests` and `pre_os4_artifacts.json` untouched and still passing |

### BASELINE

| id | item |
|---|---|
| B-1 | Materialize `subject/` into a scratch workspace with no `.git` and no key (DEC-7.2) |
| B-2 | Dispatch one Final Review attempt against it with **no** change to detection/search policy |
| B-3 | Capture audit records for the dispatch — including, on a dispatch-layer failure, the pre-failure input evidence, `observed_input_bytes`, and `failure_detail`; never swallow the failure. A captured failure is §3 evidence and is **not** a satisfied baseline |
| **B-3R** | **On a dispatch-layer failure: retry B-2/B-3 under a new Task/Dispatch identity (DEC-2), leaving the failed dispatch's records untouched. Loop until one dispatch settles with a usable Reviewer report or the max-iterations budget is exhausted. If exhausted, record the §7 baseline as FAIL (DEC-9) and stop — do not proceed to B-4 with no report** |
| B-4 | Run the scorer as a **separate** step, on the settled Reviewer report, after the reviewer has submitted (§5 requires execution and scoring be separated) |
| B-5 | Record B1-B5 of DEC-9 independently — the baseline is PASS only if all five pass over a settled, scored report; otherwise it is recorded as FAIL with the retained failed-dispatch evidence cited. Plus the explicit statement that no detection-quality or H-4/H-5 conclusion is drawn |

---

## Dependencies / Execution Order

```text
                     I-0  (neutrality golden, from 1045815)     <-- HARD FIRST
                      |
   D-A -> D-B ----+   |
   D-C -----------+---+--> I-1 -> I-2 -> I-3 -> I-4 -> I-5 --+
   D-F -----------+                                          |
                                                             +--> I-10 --+
   I-6 -> I-7 -> I-8 -+-> I-9   (I-7/I-8/I-9 = ONE commit)   |           |
                                                             |           +--> T-1, T-2, T-3
   D-D --> I-11 --+                                          |           |
   D-E --> I-12 --+------------------------------------------+           +--> T-6
                  |                                                      |
                  +--> T-4                                               +--> T-5
                  |
                  +--> B-1 -> B-2 -> B-3 -+-> B-4 -> B-5  (after T-1/T-3 pass)
                  |             ^         |
                  |             +- B-3R --+  (dispatch-layer failure: retry under a
                  |                new identity, budget-bounded; budget exhausted
                  |                with no settled report => §7 baseline FAIL)
                                                       |
                                                       +--> I-13 -> I-14 (Draft PR)
```

**Ordering constraints that are not negotiable:**

1. **I-0 before every product change.** The neutrality "before" image must be taken at `1045815`.
   Generated later, it proves nothing.
2. **I-7, I-8, I-9 in one commit.** `FINAL_REVIEW_CONTRACT` is compared for *exact equality*
   against the block parsed from §17 (`validate_skills.py:1285`) and capped at
   `FINAL_REVIEW_CONTRACT_MAX_LINES` (`:275`). Editing the SKILL block, the dict, or the cap
   alone leaves the tree red.
3. **Every `run_logging.py` edit copies to `tools/run_logging.py` in the same commit.**
   Byte-parity is enforced (`validate_skills.py:1944-1971`); drift fails validation, not tests.
4. **I-1/I-2 before I-10.** The writer exists before anything emits.
5. **I-11 before I-12.** The scorer is written against a fixture that exists, not an imagined one.
6. **T-1 and T-3 before B-2.** Do not spend a live dispatch discovering that the audit writer or
   the redactor is broken.
7. **B-4 strictly after B-3.** Reviewer execution and scoring are separated in *time*, not only
   in code — §5's requirement.
8. **B-4 never runs without a settled, usable Reviewer report.** If B-3R exhausts its budget, the
   §7 baseline is recorded FAIL (DEC-9); the scorer is not run on an absent report, and a captured
   dispatch failure is not substituted for one.

---

## Validation / Test Plan

**Commands** (the repository's own documented set, `README.md:564-571`):

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/verify_package.py
```

All three must be green at the end of TEST, and `verify_package.py` specifically because
`release_manifest.py::required_skill_paths()` and byte-parity are the two things a partial edit
breaks silently (ANALYSIS R-7).

**Evidence each requirement is discharged:**

| Ticket requirement | Proven by |
|---|---|
| §1 attempt input reconstructible | T-1 input-artifact cases + B-3 |
| §1 attempt report/findings/verdict reconstructible | T-1 report-artifact cases + B-3 |
| §1 explicit schema version | T-1 + `validate_final_review_audit_contract()` (I-9) |
| §2 observability neutrality | **T-6**, byte-identity against `pre_os22_task_specs.json` |
| §3 accepted vs voided provenance | T-1 accepted/voided cases |
| §3 failure evidence preserved | T-2 |
| §3 no hard-coded size threshold | T-2 guard test |
| §4 authority boundaries | I-6 text + `validate_final_review_audit_contract()` anchors |
| §4 secret-safe representation | T-3 |
| §4 retention/commit default | DEC-6 stated in §9; T-5 asserts no workflow path runs `git add` |
| §5 fixture + isolated key | T-4 existence/correctness cases |
| §5 no answer-key leak | T-4 leak-scan cases + B-4 |
| §6 metric contract | T-4 recall / `UNADJUDICATED` / precision-refusal cases |
| §7 baseline | B-1…B-5 (with the B-3R retry loop) against DEC-9's five criteria, over a dispatch that **settled with a usable report**; budget exhausted with no such report ⇒ recorded FAIL |
| §8 compatibility | T-5 regression + T-6 |
| §9 all five groups | T-1…T-6 |

**Completion definition for TEST:** every case above executed with its output recorded — not a
test count. §9 explicitly instructs the Reviewer to inspect the fixture and answer key directly
rather than count tests, so the TEST artifact must show the seeded defects and the key, not
summarize them.

---

## Risks

Carried from ANALYSIS with the mitigation this plan commits to, plus the risks the plan itself
introduces.

| id | risk | severity | mitigation in this plan |
|---|---|---|---|
| R-1 | Observability changes reviewer-visible input (§2) | HIGH | DEC-1: zero spec mutation, out-of-band capture, byte-identity golden (T-6) generated **before** any change (I-0) |
| R-2 | A voided report is read as the accepted verdict | HIGH | DEC-2: dispatch-keyed records, immutable writes, fail-closed `unknown`, no `accepted` default; T-1 |
| R-3 | Secret/credential in a retained artifact | MEDIUM | DEC-4: post-dispatch redaction, raw bytes never on disk, four identity fields, `dcap_`/absolute-path categories; T-3 |
| R-4 | Answer-key leak | MEDIUM | DEC-7: path separation + materialized workspace + **mechanical scan of the retained input** + split commits; T-4, B-4. Residual limitation stated, not hidden |
| R-5 | Fixture solvable by string search | MEDIUM | DEC-7: per-defect negative-space argument required as a DESIGN deliverable (D-D), not an afterthought |
| R-6 | Baseline cannot complete for dispatch reasons unrelated to detection | LOW-MED | DEC-9 + B-3R: the failed dispatch is retained as §3 evidence; the baseline **retries under a new Task/Dispatch identity** (DEC-2) and is satisfied only once one retry settles with a scoreable report and B1-B5 all pass. Retries are budget-bounded by max-iterations; exhausted with no settled report ⇒ **§7 baseline FAILS and is reported as FAIL**, with the failed-dispatch evidence preserved and still valid per §3. A captured failure is the audit machinery working — it is not a satisfied baseline |
| R-7 | Validator/packaging breakage | LOW | Ordering constraints 2 and 3; `verify_package.py` in the validation set |
| **P-1** | The I-7/I-8/I-9 three-place edit is done partially | MEDIUM | One commit, and `validate_skills.py` run immediately after it |
| **P-2** | `run_logging.py` byte-parity drift | MEDIUM | Copy step in the same commit; the validator is the compiler here |
| **P-3** | Scope creep into OS-23 (falsification policy, search depth, `reviews/final_review.md`) | MEDIUM | Named in Out of Scope; the empty `reviews/final_review.md` slot is a **trap**, not an invitation (ANALYSIS F7) |
| **P-4** | The deferred `task_context.py` suffix defect is forgotten | LOW | DEC-10(ii) records it with rationale and a reversal protocol; carried into the PR description and follow-up |
| **P-5** | Audit artifact volume | LOW | One small JSON per dispatch; §17 passes the diff by path reference, not inline, so specs are KB-scale (ANALYSIS F1(a): 2-15 KB observed) |
| **P-6** | An audit-write failure mutates settled lifecycle state | MEDIUM | `SKILL.md:1199-1201` extended to audit writing; I-10 must fail soft. T-2 covers the malformed/incomplete path |

---

## Completion Criteria

OS-22 is complete when all of the following hold. C1-C16 map to the ticket's own sixteen; C17-C20
are this plan's additional checkable conditions.

1. **C1** Each Final Review attempt's reviewer-visible semantic input is reconstructible from a
   retained artifact — labelled as the *stored Task spec*, with delivery evidence carried
   separately (DEC-1).
2. **C2** Each attempt's report / findings / verdict is reconstructible from a retained artifact.
3. **C3** `accepted` and `voided` provenance are distinguishable per dispatch, with a non-empty
   `void_reason` on every voided record.
4. **C4** An input/dispatch failure preserves the pre-failure input evidence, the retry input, and
   both identities separately.
5. **C5** `ORCHESTRATOR_LOG.md` is stated as the authoritative append-only lifecycle source,
   including the `run_end`-is-not-terminal reader rule.
6. **C6** No self-referential stale provenance: `FINAL_RESULT.md` references audit records and
   asserts no finding-level claim unsupported by a retained reviewer artifact.
7. **C7** Every new audit artifact carries an explicit `schema_version` and a stated reader
   compatibility rule.
8. **C8** Retained artifacts are secret-safe: deterministic redaction, four identity-metadata
   fields, raw bytes never persisted.
9. **C9** **T-6 passes**: `render_task_spec()` output is byte-identical to the pre-OS-22 golden
   across all captured workflows including `final_review`.
10. **C10** The seeded fixture and its isolated answer key exist, with per-defect negative-space
    justification.
11. **C11** A repeatable evaluation/scoring procedure exists and is documented.
12. **C12** Unmatched findings are safely adjudicable: `UNADJUDICATED` default, precision refused
    without a recorded precondition.
13. **C13** One baseline execution completed **on a Final Review dispatch that settled with a
    usable Reviewer report and was scored**, with DEC-9's five criteria recorded independently and
    all five passing. Dispatch-layer failures along the way are retained as §3 evidence and retried
    under separate identities (B-3R); if the retry budget is exhausted with no settled report, C13
    is **not** met and the §7 baseline is reported as FAIL.
14. **C14** Existing lifecycle / Risk / Quality Profile / Agent Profile semantics unchanged, with
    their tests passing.
15. **C15** `validate_skills.py`, the full unittest suite, and `verify_package.py` all green.
16. **C16** A fresh Final Adversarial Review passes the final repository state.
17. **C17** `VERSION` and `LICENSE-DECISION.md` are unmodified; nothing is merged; the Draft PR
    exists with the required title and a Jira OS-22 reference.
18. **C18** No `reviews/final_review.md` was created; no falsification or search-depth obligation
    was introduced; no H-1/H-2/H-4/H-5 conclusion appears in any artifact.
19. **C19** No observed `agent_prompt_blocked` size number appears as a constant anywhere in the
    implementation (T-2 guard test).
20. **C20** No workflow path performs an automatic `git add`/commit/push of run artifacts, and
    the retention/commit default is stated in `SKILL.md` §9 (DEC-6).

---

## Review Feedback Resolution

### P-001 (MAJOR, blocking) — "captured dispatch failure = successful baseline" — RESOLVED

**Finding.** The baseline pass model let a dispatch-layer failure count as a satisfied §7 baseline
whenever its failure evidence was captured, collapsing §3 (failure evidence preserved) into §7
(baseline executed). A dispatch rejected before the Reviewer ran produces no report, so it cannot
produce the scoring work §5/§7 require.

**What changed (all edits confined to the baseline success model):**

| Location | Change |
|---|---|
| DEC-9 heading + preamble | States the precondition explicitly: baseline success is evaluated only over dispatches that **settled with a usable report** |
| DEC-9 criterion B1 | Passes only when at least one dispatch settled with a usable report; fails otherwise |
| DEC-9 bullets | Split into (a) dispatch-layer failure is **always** retained §3 forensic evidence — retained unchanged from the previous version; (b) such a failure **never by itself** satisfies §7 — the baseline retries under a new Task/Dispatch identity (DEC-2) until one settles with a scoreable report and B1-B5 all pass; (c) retries are budget-bounded by this Skill's max-iterations semantics and exhaustion ⇒ **§7 baseline FAILS**, reported as FAIL, evidence still valid per §3 |
| Risk R-6 | Restated: retry-under-new-identity, satisfied only on a settled scoreable report, budget-bounded, exhaustion ⇒ reported FAIL |
| BASELINE B-3 | Reworded: a captured failure is §3 evidence, **not** a satisfied baseline |
| BASELINE **B-3R** (new) | The retry loop and its FAIL exit |
| BASELINE B-4 / B-5 | B-4 runs on the settled report; B-5 records PASS only when all five criteria pass over a settled, scored report, otherwise FAIL |
| Ordering constraint 8 (new) + dependency diagram | B-4 never runs without a settled usable report; the diagram shows the B-3R retry edge and the FAIL exit |
| T-2, T-4 | Cases restated so §3 evidence retention and §7 baseline success are asserted separately; a captured failure with no settled report is a recorded baseline FAIL |
| Evidence mapping (§7 row) | Requires a settled, scored report; exhaustion ⇒ FAIL |
| Completion criterion C13 | Requires a settled, scored report with all five criteria passing; exhaustion ⇒ C13 not met, §7 reported FAIL |

**Not changed**, per the correction instructions: DEC-1 through DEC-8, DEC-10, Scope/Out of Scope,
work ordering, and every other section the Reviewer confirmed.
