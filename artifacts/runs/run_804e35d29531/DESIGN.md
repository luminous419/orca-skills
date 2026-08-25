# Worker Result

STATUS: COMPLETE

## Summary / Requirements

This is the implementable design for OS-22, built on the approved
`artifacts/runs/run_804e35d29531/ANALYSIS.md` and `artifacts/runs/run_804e35d29531/PLAN.md`.
PLAN's ten decisions (DEC-1 … DEC-10) are treated as settled; this artifact turns them into exact
field names, exact paths, exact algorithms and exact exit codes. Nothing here reopens a DEC. Two
places where a DEC met a real repository constraint and needed a *mechanism* rather than a
reversal are called out in **Risks / Open Issues** (O-1, O-2); neither is a blocking
infeasibility, and both are reported rather than designed around silently.

**What the IMPLEMENTATION phase is being handed.** Six deliverables, each a labelled subsection of
`## Components / Interfaces / Data Flow`:

| id | deliverable | PLAN decision it discharges |
|---|---|---|
| **D-A** | Audit record schema v1.0 — field list, required/optional, dispatch-key derivation, the crash-safe stage-then-atomic-publish protocol and its immutability/collision rule, `schema_version` placement, reader compatibility rule | DEC-2, DEC-3 |
| **D-B** | Provenance state machine — event → `provenance_state`/`void_reason`, final enum spelling, "at most one `accepted` per attempt" reader rule | DEC-2 |
| **D-C** | Redaction policy `redaction/1.1` — five ordered categories (including the general non-home absolute-path rule), patterns, tokens, idempotence, digest algorithm, `redactions` shape, in-memory-only raw handling, ordering invariant, and **P-PATH**: the safety property every retained path-bearing field must satisfy whatever code path wrote it | DEC-4 |
| **D-D** | Fixture design — `subject/` tree, five seeded defects (archetype / diff / negative-space argument / matching criterion), answer-key schema, materialized-workspace protocol | DEC-7 |
| **D-E** | Scorer contract — findings/key/adjudication schemas, matching algorithm, metric output schema, the two mutually exclusive precision computation paths and their refusal semantics, exit codes | DEC-8 |
| **D-F** | Export bundle schema + minimum evidence subset serialization | DEC-6 |

Plus **N-1**, the neutrality anchor, which is not one of the six but is where PLAN's
non-negotiable ordering rule #1 (I-0 before every product change) lands: it states exactly what the
golden fixture holds and what the test asserts byte-for-byte.

### Requirements this design must satisfy, and where each is satisfied

| ticket § | requirement | designed in |
|---|---|---|
| §1 | attempt identity + state, independently retained input and report, no overwrite on retry, explicit schema version, machine-readable contract | D-A, D-B |
| §2 | observability neutrality, verified | N-1 |
| §3 | accepted vs voided, void reason, failure evidence (pre-failure input, retry input, separate identities, observed size / failure metadata), no hard-coded threshold | D-A, D-B |
| §4 | three authorities, minimum evidence subset, retention/export/commit policy, secret-safe representation with four identity fields | D-C, D-F, and the `SKILL.md` §9 text in `## Expected Changed Files` |
| §5 | seeded fixture, five archetypes, isolated answer key, execution/scoring separation | D-D |
| §6 | eleven metrics, `UNADJUDICATED` default, precision refusal | D-E |
| §7 | one baseline execution | `## Testing Strategy` → Baseline procedure |
| §8 | additive, existing semantics preserved | `## Error Handling / Compatibility` |
| §9 | five test groups | `## Testing Strategy` |

### Non-negotiable ordering rules this design anchors

1. **I-0 before any product change.** N-1 defines the golden fixture's exact content and the
   capture function that produces it. It is generated from a `git archive 1045815` checkout, and
   the design deliberately makes the fixture *independent of which specs a workflow happens to
   dispatch* (see N-1 family **B**), because `capture_legacy_artifacts()` with `profile=None`
   provably renders **no** `final_review` spec today: `scripts/e2e_harness.py:1295` calls
   `render_task_spec()` for a Final Review attempt only when `final_review_routing_context()` is
   not `None`, i.e. only under a selected Agent Profile.
2. **I-7 / I-8 / I-9 in one commit.** D-A fixes the two `#### Final review contract` keys and
   their exact values, and `## Expected Changed Files / Implementation Steps` states the three
   edit sites and the `FINAL_REVIEW_CONTRACT_MAX_LINES` 15 → 17 bump that must ride with them.
3. **Report snapshot ordering.** The report snapshot is taken inside the settlement sequence,
   after four-axis finalization and *before* the §17 T1/T2/T3 branch, because the deferred
   `phase_artifact_contract()` suffix defect (DEC-10 ii) means attempt N≥2 can overwrite
   attempt 1's `FINAL_REVIEW.md` on the real dispatch path. Stated as an invariant in D-A.

---

## Current Architecture

Read directly from the tree at `1045815`; every path and line reference below was verified in this
working copy.

### Where run-scoped artifacts already live

`scripts/run_logging.py` (1064 lines) owns everything a run writes under its own root. It
re-implements `_ensure_run_artifact_root()` (`:300`) rather than importing
`scripts/task_context.py`, because `INSTALL.md`'s documented install copies only
`SKILL.md`/`templates/`/`reviews/`/`tools/` — never `scripts/` — so this module has **zero
imports from `scripts/`** (module docstring, `:17-27`). The file is mirrored byte-for-byte to
`orca-worker-reviewer-orchestration/tools/run_logging.py`, enforced by
`validate_skills.py::validate_run_logging_tool_parity` (`:1944`).

```text
artifacts/runs/<run_id>/          <- ARTIFACT_ROOT, built by _ensure_run_artifact_root()
    ORCHESTRATOR_LOG.md           <- 18-column append-only table, ORCHESTRATOR_LOG_COLUMNS (:56)
    TIMING_LOG.md                 <- 10-column append-only table, TIMING_LOG_COLUMNS (:81)
    .timing_state.json            <- gitignored (.gitignore: artifacts/**/.timing_state.json)
    <PHASE>.md / REVIEW_<PHASE>.md / FINAL_REVIEW*.md   <- SKILL.md §9 artifact path ladder
```

Writers: `log_orchestrator_event()` (`:340`), `log_timing_event()` (`:407`), `log_run_status()`
(`:508`), `RunTimingTracker` (`:558`), and a four-subcommand CLI (`:857`) whose `--event` argument
has **no `choices`** (`:872`) — the event vocabulary is deliberately open, and has already been
extended in production with `pr_created` and `external_review_correction_triggered`.

### Where the reviewer-visible input is produced

`scripts/task_context.py::render_task_spec()` (`:590`) assembles the spec: caller body, then
layer-1 `TASK_BOUNDARY_KEYS`, then four optional blocks (reviewer context, quality gate, risk,
agent routing), each appended only when its argument is not `None` so that omitting one renders a
byte-identical spec to before that argument existed. `phase_artifact_contract()` (`:284`) returns
`<ARTIFACT_ROOT>FINAL_REVIEW.md` for the `final_review` phase — **unsuffixed for every attempt**,
which is the conformance defect DEC-10(ii) deferred; `scripts/e2e_harness.py::final_review_artifact_path()`
(`:422`) implements §9's suffix rule instead.

### What Orca returns after a dispatch (the capture sources, verified live)

`orca orchestration task-list --run <run_id> --json` returns per task:
`id`, `run_id`, `parent_id`, `created_by_terminal_handle`, `task_title`, `display_name`, **`spec`
in full**, `status`, `deps`, `result`, `created_at`, `completed_at`, `created_by_pane_key`,
`created_by_process_incarnation`, `created_by_run_generation`.

`orca orchestration dispatch-show --task <task_id> --json` returns
`result.dispatch`: `id`, `run_id`, `task_id`, `contract_version`, `launch_token_hash`,
`assignee_handle`, `assignee_pane_key`, `capability_hash`, `process_incarnation`,
`capability_revoked_at`, `status`, `failure_count`, `last_failure`, `dispatched_at`,
`completed_at`, `created_at`, `last_heartbeat_at`, `termination_reason`.

This is the delivery-evidence surface DEC-1 requires. Note what is **not** used: the
`--preamble` field, which ANALYSIS F1(b) proved is re-rendered against reader state (wrong
coordinator handle across four tasks in three runs; missing `dcap_` material entirely).

### Existing conventions this design must not fork

| convention | source | consequence for this design |
|---|---|---|
| stdlib only, CPython ≥ 3.11 | `COMPATIBILITY.md:13-21` | JSON via `json`, digests via `hashlib`, capture via `subprocess` — all stdlib |
| `run_logging.py` imports nothing from `scripts/` | `run_logging.py:17-27` | the audit/redaction/export code lives in `run_logging.py` and duplicates any constant it needs |
| byte-parity with `tools/run_logging.py` | `validate_skills.py:1944` | every edit copies in the same commit |
| `scripts/` ships in the release archive | `release_manifest.py:35` `INCLUDED_ROOTS` | the fixture and answer key are packaged; see O-3 |
| no user-specific absolute paths in `*.md` under skill dirs or `REPOSITORY_DOCS` | `validate_skills.py:66-70`, `:810` | `SKILL.md` examples must write `/Users/<name>/…` (the pattern's `(?!<\|\{)` lookahead exempts it) |
| `--event` has no `choices` | `run_logging.py:872` | new log rows need **no** schema change and **no** new column |
| `FINAL_REVIEW_CONTRACT` compared for exact equality, capped at `FINAL_REVIEW_CONTRACT_MAX_LINES` | `validate_skills.py:1280-1293`, `:275` | the SKILL block, the dict and the cap move together or the tree is red |

---

## Proposed Design

### The shape in one picture

```text
   §17 step 4/5: create terminal, dispatch, wait for worker_done
        |
        |  (nothing on this edge is touched -- N-1's byte-identity claim lives here)
        v
   settlement: four-axis lifecycle finalization (§6)
        |
        +--> [1] snapshot the report file at its resolved path      <- BEFORE the next attempt
        +--> [2] capture the stored Task spec   (task-list --json)     can overwrite it
        +--> [3] capture delivery evidence      (dispatch-show --json)
        +--> [4] redact [1] and [2] in memory, digest pre and post
        +--> [5] stage   <ARTIFACT_ROOT>final_review_audit/.staging/<key>.<nonce>/
                             input.md, report.md, record.json   (fsync each, then the dir)
                 publish <ARTIFACT_ROOT>final_review_audit/<key>/   (ONE os.rename;
                             EEXIST/ENOTEMPTY == a genuine collision, never an overwrite)
        +--> [6] one ORCHESTRATOR_LOG.md row (existing columns, new event value)
        |
        v
   §17 step 6: evaluate verdict, branch T1/T2/T3
```

Raw pre-redaction bytes exist only in step [4]'s local variables. They are never written, never
staged, never passed to a subprocess. Only their digests survive.

### Three principles the whole design is derived from

1. **Nothing left of the dispatch call changes.** The capture sources are CLI reads that cannot
   run before the dispatch exists. This is what makes DEC-1's neutrality claim a *property of the
   construction* rather than an argument (N-1).
2. **A record is written once, complete, and never edited.** Immutability and provenance are
   compatible because provenance is fully determined at settlement — see D-B's precedence ladder.
   No code path anywhere reopens a written record.
3. **Every derived claim names its denominator and refuses when it cannot.** D-E's metric block
   carries `population` and `denominator` on every rate, and `precision_status: REFUSED` with a
   machine-readable reason is a first-class output, not an error path.

---

## Components / Interfaces / Data Flow

### D-A — Audit record schema v1.0 (DEC-2, DEC-3)

#### A.1 Paths

```text
<ARTIFACT_ROOT> = artifacts/runs/<run_id>/                (unchanged, run_logging.py:300)

<ARTIFACT_ROOT>final_review_audit/                        <- NEW, one directory per run
    <dispatch_key>/                 PUBLISHED record unit -- exists only when COMPLETE
        record.json                 audit record (this schema)
        input.md                    retained redacted stored Task spec
        report.md                   retained redacted report snapshot
    .staging/                       NEVER a record; readers ignore it entirely (A.3)
        <dispatch_key>.<nonce>/     one in-progress write; renamed away on success

<ARTIFACT_ROOT>FINAL_REVIEW_EVIDENCE_BUNDLE.json          <- NEW, D-F export, one per run
```

**The record unit is a directory, not three sibling files.** A.3 publishes it with one
`os.rename()` of a fully-written staging directory, and a directory rename is the only single
atomic step available that can carry three files across together. The three files therefore live
*inside* `<dispatch_key>/` rather than being named `<dispatch_key>.record.json` and friends: the
name that must become visible atomically is the one that has to be a rename target.

A subdirectory, not flat files under `<ARTIFACT_ROOT>`, for one reason: §9's artifact-path ladder
assigns meaning to `<ARTIFACT_ROOT>`'s flat namespace (`<PHASE>.md`, `REVIEW_<PHASE>.md`,
`FINAL_REVIEW*.md`) and a glob like `FINAL_REVIEW*` — which §16 step 8 will name after I-8 — must
not start matching audit files. `final_review_audit/` is outside every existing glob. The export
bundle is a run-level summary artifact and sits flat next to `ORCHESTRATOR_LOG.md`, which is the
existing convention for run-level files.

#### A.2 `dispatch_key` derivation — the filename rule

```text
dispatch_key = "attempt" + str(final_review_attempt)
             + "__" + task_id
             + "__" + (dispatch_id or "nodispatch")
```

Example: `attempt1__task_2d0a6f4fc5a4__ctx_ab12cd34ef56`.

* The attempt number leads so `ls` sorts by attempt and the attempt grouping (DEC-2: attempt is a
  *grouping*, not a key) is visible without opening a file.
* `task_id` **and** `dispatch_id` both appear because DEC-2's identity tuple is
  `(run_id, final_review_attempt, task_id, dispatch_id)` and `run_id` is already the directory.
* `nodispatch` covers a pre-dispatch failure, where §9's existing `pre_dispatch_failure` event
  fires and no dispatch id exists. A second dispatchless failure on the *same* Task cannot happen
  under DEC-2's separate-identity-per-retry rule; if it somehow does, the collision rule below
  catches it rather than clobbering.
* **Validation, fail-closed.** Each component must match `^[A-Za-z0-9][A-Za-z0-9_-]*$` and be
  non-empty; `final_review_attempt` must be an `int >= 1`. Anything else raises
  `RunLoggingError` before any file is touched — the same posture `_ensure_run_artifact_root()`
  already takes against a `run_id` with a path separator (`run_logging.py:319-323`).

#### A.3 Immutability, crash-safe publication, and collision

```python
def write_final_review_audit_record(...) -> Path:
    """Stages three files, then publishes them with ONE atomic rename.

    Never overwrites. Never mutates a published record. A failure at any write
    boundary leaves NO published record and never blocks a later attempt.
    """
```

An earlier revision of this section prechecked the three final paths and then created them
sequentially with `open(path, "x")`. That is not recoverable: an `OSError` after `input.md` exists
but before `record.json` does leaves a surviving file that every later attempt reads as a
permanent collision, so that dispatch could never acquire a complete record — and three separate
creates are not atomic with respect to each other anyway. The protocol below replaces it.

**P1 — Stage.** `final_review_audit/.staging/` is created with `os.makedirs(..., exist_ok=True)`.
Inside it, the writer creates its own attempt directory with `os.mkdir()` (which is exclusive by
definition, so two concurrent writers cannot share one):

```text
final_review_audit/.staging/<dispatch_key>.<nonce>/
    nonce = f"{os.getpid()}-{secrets.token_hex(4)}"
```

`.staging/` is a **sibling of the published directories, inside `final_review_audit/`**, so
staging and the rename target are on the same filesystem by construction and `os.rename()` cannot
degrade into a copy or an `EXDEV`.

**P2 — Write and flush.** `input.md`, `report.md` and `record.json` are written into the staging
directory, each with `open(path, "x", encoding="utf-8")`, and each is `flush()`ed and
`os.fsync()`ed before close. The staging directory itself is then fsynced (`os.open(dir,
os.O_RDONLY)` → `os.fsync` → `os.close`), so the three names are durable before publication.
Directory fsync is wrapped in `try/except (OSError, AttributeError)` and degrades to best-effort on
platforms that refuse it; durability is a strengthening, never a precondition for correctness here.

**P3 — Publish, atomically.**

```python
os.rename(staging_dir, audit_dir / dispatch_key)   # NOT os.replace
```

One step, one name. `os.rename()` on a directory whose target already exists fails with
`OSError` (`ENOTEMPTY`/`EEXIST`/`ENOTDIR`, and `FileExistsError` on Windows) instead of
clobbering — which is exactly the immutability guarantee, obtained atomically rather than by a
precheck that races. `os.replace()` is deliberately **not** used: it would overwrite. The parent
`final_review_audit/` directory is fsynced after a successful rename, same best-effort wrapper.

**P4 — Failure at any boundary.** `mkdir`, any of the three writes, any fsync, or the rename
itself raising `OSError` → the writer removes its own staging directory with
`shutil.rmtree(staging_dir, ignore_errors=True)` and re-raises `FinalReviewAuditWriteFailed`. If
the process dies before that cleanup runs, the staging directory is simply left behind — see P6.
In **no** failure case does a published `<dispatch_key>/` directory come into existence, so:

> **The reader rule: a published directory *is* a complete record.**
> A complete record is exactly a directory `final_review_audit/<X>/` where `X` matches A.2's
> `dispatch_key` grammar. Because that name only ever appears via P3's rename of a fully written,
> fsynced staging directory, existence and completeness are the same fact and no reader needs a
> "is it finished yet?" heuristic. Readers **MUST** skip `.staging/` outright — it is excluded
> twice over, by an explicit name check and by A.2's grammar (`^[A-Za-z0-9][A-Za-z0-9_-]*$`
> rejects a leading dot). Nothing under `.staging/` is ever parsed, digested, exported, counted in
> `integrity.records_found`, or allowed to answer a provenance question.

**P5 — Retry for the same dispatch.** A retry stages under a **new** nonce, so it never touches
the abandoned staging state and never has to repair it. The rename then decides:

| state on disk | rename outcome | writer behaviour |
|---|---|---|
| no `<dispatch_key>/` (first attempt, or a previous attempt died mid-write) | succeeds | the record is published; the dispatch is recorded normally — a prior crash costs nothing |
| `<dispatch_key>/` already published | `OSError` | genuine collision: raise `FinalReviewAuditCollision(dispatch_key, published_path)`, `rmtree` the new staging dir, leave the published record byte-for-byte untouched |

So the D-003 failure mode is closed in both directions: a mid-write failure never orphans a
dispatch (the next attempt publishes cleanly), and a completed record is never overwritten.

**P6 — Abandoned staging state is reported, never silently swallowed and never garbage.**
On every `write_final_review_audit_record()` call, and again at export time (D-F), the writer
scans `.staging/` and classifies each entry by its `<dispatch_key>` prefix:

* a published `<dispatch_key>/` exists → the staged copy is redundant; `shutil.rmtree(...,
  ignore_errors=True)`;
* no published directory exists → the staging directory is **retained**, because it is the only
  surviving evidence of that attempt, and one `final_review_audit_incomplete_publication`
  ORCHESTRATOR_LOG row is emitted per entry per run (`detail=staging=<name>`). D-F's bundle
  carries the same list under `integrity.incomplete_publications`, each with its `dispatch_key`,
  the staging directory name, and which of the three files it did and did not contain.

That is the "explicit recoverable incomplete state" rather than a silent permanent refusal: an
operator sees exactly which dispatch failed to publish and what partial evidence survived, and the
next attempt for that dispatch key still publishes normally.

**P7 — Still no mutation surface.** The caller is `_safe_log`-wrapped (see `## Error Handling`), so
both `FinalReviewAuditCollision` and `FinalReviewAuditWriteFailed` land in `self._logging_errors`
as one ORCHESTRATOR_LOG row and the run continues. **Neither ever mutates settled lifecycle state**
(`SKILL.md:1199-1201`). There is no `force`, no `--overwrite`, no update function, and no code path
that writes into an already-published `<dispatch_key>/`. Correcting a record means writing a *new*
record under a new dispatch key, which is what a retry already produces.

#### A.4 The v1.0 field list

`schema_version` is the **first key of the file**, emitted with
`json.dump(record, fh, indent=2, sort_keys=False, ensure_ascii=False)` over an insertion-ordered
`dict`, so the first key is stable and a human `head`-ing the file sees the version first. Files
end with a single trailing newline.

```jsonc
{
  "schema_version": "1.0",                       // REQUIRED. "<MAJOR>.<MINOR>". First key.
  "record_kind": "final_review_dispatch_audit",  // REQUIRED. Literal; lets one reader
                                                 //   discriminate future record families.
  "run_id": "run_804e35d29531",                  // REQUIRED
  "final_review_attempt": 1,                     // REQUIRED, int >= 1 (FINAL_REVIEW_ITERATIONS)
  "task_id": "task_2d0a6f4fc5a4",                // REQUIRED
  "dispatch_id": "ctx_ab12cd34ef56",             // REQUIRED key, "" allowed (pre-dispatch failure)
  "dispatch_key": "attempt1__task_2d0a6f4fc5a4__ctx_ab12cd34ef56",  // REQUIRED, == filename stem
  "recorded_at": "2026-08-26T09:12:33.412870+00:00",                // REQUIRED, now_iso()

  "reviewer_terminal": "term_6ac06c14-6bb5-4e56-ac30-4ecb313371f3", // optional ("" if unknown)
  "reviewer_agent_command": "claude",                               // optional
  "reviewer_agent_origin": "agent_profile_final_review",            // optional; one of
      // agent_profile_final_review | explicit_reviewer | defaults | unknown   (§17 ladder)

  "repository": {                                // optional object; omit only if git is absent
    "head_commit": "1045815d1f1c0a9e...",        //   `git rev-parse HEAD`
    "branch": "agent/final-review-observability-evaluation",
    "dirty": true                                //   `git status --porcelain` non-empty
  },

  "stored_task_spec": {                          // REQUIRED object
    "source": "orca orchestration task-list --run <run_id> --json",  // REQUIRED, literal
    "capture_status": "captured",                // REQUIRED: captured | unavailable
    "capture_error": "",                         // REQUIRED, non-empty iff unavailable
    "captured_at": "2026-08-26T09:12:33.401118+00:00",
    "is_stored_spec_not_delivered_bytes": true,  // REQUIRED literal true -- DEC-1's honest limit,
                                                 //   carried in the artifact, not only in prose
    "byte_length_pre_redaction": 14805,          // int, null when unavailable
    "input_digest_pre_redaction": "sha256:9f2c...",   // one of DEC-4's four identity fields
    "redaction_policy_version": "redaction/1.1",      // two
    "artifact_path": "final_review_audit/attempt1__task_2d0a6f4fc5a4__ctx_ab12cd34ef56/input.md",
                                                 // relative to <ARTIFACT_ROOT>, POSIX separators
    "artifact_digest_post_redaction": "sha256:41ab...",  // three -- re-derivable from disk
    "byte_length_post_redaction": 14790,
    "redactions": [                              // four
      {"category": "orca_dispatch_capability", "count": 1}
    ]
  },

  "delivery_evidence": {                         // REQUIRED object -- DEC-1: separate from the
    "source": "orca orchestration dispatch-show --task <task_id> --json",  //  stored spec, and
    "capture_status": "captured",                //  NEVER re-rendered preamble text
    "capture_error": "",
    "captured_at": "2026-08-26T09:12:33.406902+00:00",
    "preamble_captured": false,                  // REQUIRED literal false -- ANALYSIS F1(b)
    "dispatch_status": "failed",                 // verbatim from the CLI
    "contract_version": 1,
    "dispatched_at": "2026-08-25 15:11:05",
    "completed_at": null,
    "capability_hash": "a5f41c33c097c51c...",    // a hash, not the dcap_ token
    "assignee_handle": "term_6ac06c14-...",
    "failure_count": 1,
    "last_failure": "agent_prompt_blocked",
    "termination_reason": null
  },

  "report": {                                    // REQUIRED object
    "contract_path": "artifacts/runs/run_804.../FINAL_REVIEW.md",   // the path actually read,
                                                 //   normalized per C.7 P-PATH: <ARTIFACT_ROOT>-
                                                 //   relative, else "<REPO>/...", else redacted.
                                                 //   NEVER a raw absolute path -- see C.7.
    "resolution": "ladder",                      // REQUIRED: explicit | ladder | fallback_unsuffixed
    "capture_status": "captured",                // REQUIRED: captured | absent | unreadable
    "capture_error": "",
    "captured_at": "2026-08-26T09:12:33.398440+00:00",
    "byte_length_pre_redaction": 4210,
    "report_digest_pre_redaction": "sha256:7b04...",
    "redaction_policy_version": "redaction/1.1",
    "artifact_path": "final_review_audit/attempt1__task_...__ctx_.../report.md",
    "artifact_digest_post_redaction": "sha256:c31e...",
    "byte_length_post_redaction": 4210,
    "redactions": [],
    "parsed": {                                  // REQUIRED object; all-empty when parse fails
      "parse_status": "ok",                      // ok | malformed | not_attempted
      "parse_error": "",
      "result": "FAIL",                          // RESULT: PASS|FAIL, verbatim; "" if absent
      "review_verdict": "FAIL",                  // REVIEW_VERDICT: 4 values, verbatim; "" if absent
      "blocking_finding_ids": ["R1", "R2"],
      "non_blocking_finding_ids": ["R3"]
    }
  },

  "provenance_state": "voided",                  // REQUIRED -- D-B
  "void_reason": "dispatch_input_rejected",      // REQUIRED, non-empty iff state == voided
  "settlement_state": "not_settled",             // REQUIRED: settled | not_settled | unknown

  "failure_detail": "agent_prompt_blocked",      // optional free text -- the runtime's OWN label.
                                                 //   Never enters an enum (ANALYSIS F6).
  "observed_input_bytes": 14805,                 // optional int -- §3 failure metadata. Distinct
                                                 //   from stored_task_spec.byte_length_pre_redaction
                                                 //   because it can be filled from the runtime's own
                                                 //   failure report when the spec capture failed.
  "input_altered_across_retry": "unknown",       // optional: yes | no | unknown -- §3's "input이
                                                 //   축약/변경되었는지". Derived by a READER comparing
                                                 //   input_digest_pre_redaction across an attempt's
                                                 //   records; the writer never guesses, so the
                                                 //   default written value is "unknown".
  "notes": ""                                    // optional free text
}
```

**Required vs optional, stated once as a rule.** Required: `schema_version`, `record_kind`,
`run_id`, `final_review_attempt`, `task_id`, `dispatch_id`, `dispatch_key`, `recorded_at`,
`stored_task_spec` (with its own required `source`/`capture_status`/`capture_error`/
`is_stored_spec_not_delivered_bytes`), `delivery_evidence` (required `source`/`capture_status`/
`capture_error`/`preamble_captured`), `report` (required `contract_path`/`resolution`/
`capture_status`/`capture_error`/`parsed.parse_status`), `provenance_state`, `void_reason`,
`settlement_state`. Everything else is optional and **additive-with-default**, which is what makes
DEC-3's "MINOR suffices for new fields" rule hold.

**Path-bearing fields obey C.7's P-PATH regardless of which code path writes them.** In this
record that is `report.contract_path`, `stored_task_spec.artifact_path`, `report.artifact_path` and
`delivery_evidence.process_incarnation`; C.7 carries the closed list and the enforcement rule. It
is called out here too because `report.contract_path` is assembled outside the redaction pipeline
and was, in `redaction/1.0`, the one required field that could carry a raw absolute path into a
retained artifact.

**`report.resolution` — why it exists.** DEC-10(ii) deferred the `task_context.py` /
`e2e_harness.py` suffix disagreement. The writer therefore resolves the report path itself:
`explicit` when the caller passed `--report-path`; otherwise `ladder` — §9's rule, attempt 1
unsuffixed and attempt N≥2 `FINAL_REVIEW_iteration<N>.md`; and `fallback_unsuffixed` when the
laddered path does not exist but `FINAL_REVIEW.md` does. Recording which of the three happened
makes the deferred conformance defect **visible as data** in every real run instead of silently
absorbed, which is the honest treatment of a deliberate deferral.

**Ordering invariant (report snapshot).** Because `phase_artifact_contract()` returns the
unsuffixed path for every attempt on the real dispatch path, attempt N+1's Reviewer can overwrite
attempt N's `FINAL_REVIEW.md`. The snapshot is therefore taken **inside the settlement sequence**
— after four-axis finalization (§17 step 5) and before the verdict branch (§17 step 6) — never
deferred to run end. `report.captured_at` is written so a reader can confirm the ordering held.

#### A.5 `schema_version` placement and the reader compatibility rule

The constant lives in `run_logging.py`:

```python
FINAL_REVIEW_AUDIT_SCHEMA_VERSION = "1.0"
FINAL_REVIEW_REDACTION_POLICY_VERSION = "redaction/1.1"
FINAL_REVIEW_EXPORT_SCHEMA_VERSION = "1.0"
```

The three version constants are **independent of each other**, which is why the redaction policy
moving to `redaction/1.1` (C.2) leaves `FINAL_REVIEW_AUDIT_SCHEMA_VERSION` at `"1.0"`: no field of
the record schema changed shape or meaning. `redaction_policy_version` is a *value* the record
carries, and a record that carries a different value is still a MAJOR-1 record a MINOR-0 reader
reads correctly. The metrics schema of D-E is likewise independent, and it *does* take a MINOR bump
for the `attested_false_positives` field and the fourth `adjudication_status` value (E.5 point 2a).

It is independent of the repository `VERSION` (which OS-22 does not touch), mirrored into
`tools/run_logging.py` by the byte-parity rule, and asserted equal to the value stated in
`SKILL.md` §9 by `validate_skills.py::validate_final_review_audit_contract()` (I-9).

Reader entry point:

```python
def read_final_review_audit_record(path: Path) -> tuple[dict | None, str]:
    """Returns (record, status). status: ok | unknown_major | malformed | missing."""
```

```text
missing file            -> (None, "missing")     -> provenance reads unknown
unreadable / not JSON   -> (None, "malformed")   -> provenance reads unknown
missing required field  -> (None, "malformed")   -> provenance reads unknown
schema_version absent
  or not "<int>.<int>"  -> (None, "malformed")   -> provenance reads unknown
MAJOR != 1              -> (None, "unknown_major") -> REFUSE to interpret; report UNKNOWN;
                                                       never infer provenance from any field
MAJOR == 1, MINOR > 0   -> (record, "ok")        -> read known fields, ignore unknown ones
```

MINOR is bumped for additive fields; MAJOR for any change to the meaning of an existing field.
There is no code path anywhere that yields `accepted` from a record that did not read `ok` and did
not carry `"provenance_state": "accepted"` literally.

#### A.6 Retained audit Markdown vs. the repository whitespace gate (R5)

A.3 makes a published `<dispatch_key>/` byte-for-byte immutable, and A.4/C.5 bind each retained
file to a digest recorded inside `record.json`. The repository's required final validation is
`git diff --check <base>..HEAD`. Those two rules collided on real committed evidence, and this
subsection is the mechanism that lets both hold at once. It changes **no** other part of D-A.

**The observed conflict.** `git diff --check 1045815..HEAD` exits **2** with **40** errors, all of
kind `trailing whitespace.`, all in one file:

```text
artifacts/runs/run_92759e0e1034/final_review_audit/attempt1__task_936f73b5d2eb__ctx_1f82fd26c92b/report.md
    lines 12-19, 22-29, 32-39, 42-49, 52-59   (40 lines)
```

**Root cause, stated exactly.** Those 40 lines end in two spaces. That is the Markdown *hard line
break* — the standard, correct way for the Reviewer to force a line break inside a paragraph, and
what makes the `ID:` / `Severity:` / `Blocking:` finding blocks render as separate lines. Git's
default whitespace rule set includes `blank-at-eol`, and `git diff --check` reports every **added**
line matching an active rule and exits 2 when any is found. Neither side is wrong: the report is
valid Markdown, and the gate is doing exactly what its default configuration asks. The defect is
that the OS-22 contract never said how byte-exact retained audit Markdown participates in that gate.

**Why the bytes cannot simply be trimmed.** `record.json` for that dispatch records

```text
report.artifact_digest_post_redaction = sha256:6f91033e4e2f644ab64eb4e61292734671b588d51ff0eb1649c626f8ae748e18
report.byte_length_post_redaction     = 6028
```

and that digest recomputes over the committed bytes exactly as they stand (verified: `shasum -a
256` of the file on disk equals the recorded value, and `git diff --quiet` confirms the working
copy is byte-identical to `HEAD`). Trimming the 40 lines changes both the digest and the byte
length, which would either falsify the published record or require mutating it — and A.3/P7 give
the writer no mutation surface at all. Re-deriving the report is equally excluded: it is a
*retained snapshot of Reviewer-produced bytes*, not a regenerable artifact. So the fix must make
the gate pass **without touching a single byte of any published record**.

**Scope, measured rather than assumed.** Every file under every `final_review_audit/` directory in
the repository was scanned for trailing whitespace in its committed bytes:

| path class | files | files with trailing whitespace |
|---|---|---|
| `artifacts/runs/*/final_review_audit/**/report.md` | 5 | 1 (40 lines) |
| `artifacts/runs/*/final_review_audit/**/input.md` | 5 | 0 |
| `artifacts/runs/*/final_review_audit/**/record.json` | 5 | 0 |

Only `report.md` is affected, and only `report.md` *can* be affected as a class, because it is the
only member of the record unit whose bytes are free-form prose authored by an external Reviewer
agent that legitimately uses Markdown hard breaks. `input.md` is a canonicalized capture of the
stored Task spec and `record.json` is writer-serialized JSON — both are produced by this codebase
under its own formatting control, so both stay fully covered by the gate and are **not** exempted.
The exemption is therefore scoped to `report.md` alone, not to the audit directory.

**The mechanism — a path-scoped Git `-whitespace` attribute.** One line, one new repository-root
file:

```gitattributes
# .gitattributes
# Retained Final Review reports are byte-exact snapshots of Reviewer-authored Markdown, digest-
# bound by record.json and immutable under DESIGN A.3. Markdown hard breaks (two trailing spaces)
# are legitimate there and must not be trimmed, so this path is exempt from git's whitespace
# rules. Scope is exactly these snapshots; every other path keeps the default rules.
artifacts/runs/*/final_review_audit/**/report.md -whitespace
```

`-whitespace` (attribute *unset*) tells `git diff --check`, `git apply --whitespace`, and
`git am` to apply **no** whitespace rules to matching paths. It is Git-native, it is the mechanism
Git documents for exactly this case, and it is honoured for already-committed content because
`git diff` resolves attributes from the working-tree `.gitattributes`, not from the commits being
compared — so a range that ends at `HEAD` is covered the moment the file exists in the checkout.

**Verified empirically before being written down** (this was tested against the real failing file,
not reasoned about):

| check | result |
|---|---|
| `git diff --check 1045815..HEAD` with the attribute present | **exit 0**, no output (was exit 2 / 40 errors) |
| same, in a fresh `git clone` with `.gitattributes` **committed** | **exit 0**, no output |
| `report.md` digest after the change | `sha256:6f91033e…e748e18` — **unchanged**; `git diff --quiet` reports the file unmodified |
| `git check-attr whitespace` on the affected `report.md` | `unset` |
| `git check-attr whitespace` on sibling `input.md` / `record.json` | `unspecified` (still gated) |
| trailing whitespace introduced into a `src/*.py` file | still flagged, **exit 2** |
| trailing whitespace introduced into a `final_review_audit/**/input.md` | still flagged, **exit 2** |
| `verify_package.py` / `validate_skills.py` with the file present | PASSED (107 source files) / PASSED (463 checks) |

Glob boundaries were checked the same way: `report.md`, `artifacts/report.md` and
`artifacts/runs/<run>/report.md` all resolve `unspecified`; only paths of the form
`artifacts/runs/<run>/final_review_audit/**/report.md` resolve `unset`.

**What this does not do.** The exemption is a single path pattern. It is **not** repo-wide, does
not use `* -whitespace`, does not touch `core.whitespace`, and does not alter the gate for
production code, skills, scripts, tests, docs, or any other artifact — including the other two
files of the very same record unit. `git diff --check` keeps its full default coverage everywhere
else, which the negative controls above demonstrate rather than assert. The rule is also
future-proof in the right direction: it covers any *future* retained report whose Reviewer uses
hard breaks, so this class of failure cannot recur, while a whitespace defect in real code is
still caught.

**Regression requirement.** T-5 gains a two-part test that pins both halves of this invariant —
that the gate passes, and that it passes *because of the attribute* rather than because someone
trimmed the file. See `## Testing Strategy` → T-5.

**Implementation note.** `.gitattributes` is a new repository-root file and is added to the Files
summary in `## Expected Changed Files / Implementation Steps`. It is created in Step 0 (before any
product change), so every subsequent step's own `git diff --check` run is already clean.

---

### D-B — Provenance state machine (DEC-2)

#### B.1 The two fields and their final enum spelling

```text
provenance_state : "accepted" | "voided" | "unknown"          # fail-closed default: "unknown"
void_reason      : "" | one of the six below                  # non-empty iff state == "voided"
```

```text
VOID_REASONS = (
    "dispatch_input_rejected",       # the dispatch was refused at input
    "dispatch_capability_invalid",   # the dispatch capability was refused
    "settlement_failure",            # dispatched, never reached a settled outcome
    "report_missing",                # settled, no report artifact at the resolved path
    "report_malformed",              # settled with a report that fails the §11/§17 contract parse
    "superseded_by_retry",           # a later dispatch of the SAME attempt produced the accepted
                                     #   verdict; this output is forensic only
)
```

`unknown` is a **member of** `provenance_state`, not a separate absence-state: an absent field, an
unparseable record, an unknown MAJOR and a literal `"unknown"` all read as `unknown`. One state
machine. Writer-side validation is fail-closed: `provenance_state` not in the three values →
`RunLoggingError`; `voided` with empty `void_reason` → `RunLoggingError`; `void_reason` non-empty
while state is not `voided` → `RunLoggingError`; `void_reason` not in `VOID_REASONS` →
`RunLoggingError`. The default value of the writer's `provenance_state` parameter is
`"unknown"`, so a caller that forgets it produces a fail-closed record, never an `accepted` one.

#### B.2 Lifecycle event → state, as a deterministic first-match ladder

Evaluated in this order at the single write point (settlement, §17 step 5→6). The first matching
condition wins, which is what makes the assignment reproducible rather than a judgement call.

| # | observed at settlement | `provenance_state` | `void_reason` | `settlement_state` | typical `failure_detail` |
|---|---|---|---|---|---|
| 1 | dispatch refused at input (`orca` reports a dispatch-input failure; `dispatch.status == "failed"` with an input-side reason) | `voided` | `dispatch_input_rejected` | `not_settled` | `agent_prompt_blocked` (verbatim runtime label) |
| 2 | the dispatch or its `worker_done` was rejected as an invalid/revoked capability (`capability_revoked_at` set, or a `dispatch_capability_invalid` rejection) | `voided` | `dispatch_capability_invalid` | `not_settled` | verbatim runtime label |
| 3 | dispatched, but the run moved on without a settled outcome (unexpected exit, abandonment, `termination_reason` set with no `worker_done`) | `voided` | `settlement_failure` | `not_settled` | verbatim runtime label |
| 4 | settled, but no report file exists at the resolved path (`report.capture_status != "captured"`) | `voided` | `report_missing` | `settled` | `""` |
| 5 | settled with a report that fails the §11/§17 parse (`report.parsed.parse_status == "malformed"`) | `voided` | `report_malformed` | `settled` | parse error text |
| 6 | settled with a usable report, but the Coordinator does not act on this dispatch's verdict and retries within the same attempt for a reason not covered by 1–5 | `voided` | `superseded_by_retry` | `settled` | `""` |
| 7 | settled with a usable report **and** this is the dispatch whose verdict the Coordinator acts on at §17 step 6 (T1/T2/T3) | `accepted` | `""` | `settled` | `""` |
| 8 | anything else, or the caller cannot determine which of 1–7 applies | `unknown` | `""` | `unknown` | `""` |

**Why immutability and provenance are compatible — the point that makes DEC-2 and DEC-3 work
together.** Every row above is determinable *at settlement time*, so a record is complete when it
is written and never needs editing. Row 7 is not a retroactive blessing: §17 step 6 acts on the
verdict of a cleanly settled dispatch immediately, so "will the Coordinator act on this?" is known
at the moment of settlement. Row 6 covers the only case where a settled dispatch is knowingly not
acted on, and it is chosen by the Coordinator at that same moment. No code path anywhere reopens a
written record, and `superseded_by_retry` never requires downgrading an already-written
`accepted`.

**Where each row lands in the real observed history** (ANALYSIS A-1.1 / F2a / F2b):

* `run_c854db299e7a` attempt 1 → three records: `task_2d0a6f4fc5a4` row 1
  (`dispatch_input_rejected`, `failure_detail: agent_prompt_blocked`,
  `observed_input_bytes: 14805`), `task_6b7d7a0cdd95` row 1 (`dispatch_input_rejected`,
  `observed_input_bytes: 5553`), `task_d3f49c042d5a` row 7 (`accepted`). The composition ANALYSIS
  A-1.1 describes — an input failure whose `worker_done` was *separately* rejected as
  `dispatch_capability_invalid` — is preserved because the ladder picks the earliest cause for
  `void_reason` and the second cause survives verbatim in `failure_detail`, which is exactly the
  reason DEC-2 chose two fields over one flat enum.
* `run_ec18ea04bc22` (two attempts, zero report files) → two records, both row 4
  (`report_missing`). Under this design that run could not have reported `COMPLETED` with
  `FINAL_FINDINGS: none` unchallenged: the §16 audit-record reference requirement (I-8) forces
  `FINAL_RESULT.md` to cite a `provenance_state`, and `voided/report_missing` is not a verdict.

#### B.3 The attempt-level reader rule: at most one `accepted`

```python
def read_final_review_attempt_provenance(
    run_id: str, attempt: int, *, base: Path | None = None
) -> dict:
    """Derive the attempt's accepted verdict from its dispatch records. Never resolves
    a contract violation -- it reports one."""
```

Returns:

```jsonc
{
  "run_id": "run_804e35d29531",
  "final_review_attempt": 1,
  "records": ["attempt1__task_a__ctx_1", "attempt1__task_b__ctx_2"],  // sorted dispatch_keys
  "unreadable": [{"dispatch_key": "...", "status": "malformed"}],
  "accepted_dispatch_key": "attempt1__task_b__ctx_2",   // or null
  "violations": []      // machine-readable; see below
}
```

Rules, all fail-closed:

* Exactly one record with `provenance_state == "accepted"` → that key is returned.
* **Two or more** → `accepted_dispatch_key: null` and
  `violations: [{"code": "multiple_accepted_dispatches", "dispatch_keys": [...]}]`. The reader
  reports; it does not pick a winner.
* **Zero** → `accepted_dispatch_key: null` and
  `violations: [{"code": "no_accepted_dispatch", "attempt": N}]`. This is the §7-relevant
  condition: an attempt with no accepted dispatch produced no usable verdict (DEC-9 B1).
* Any record that did not read `ok` contributes to `unreadable` and to
  `violations: [{"code": "unreadable_record", ...}]`, and **can never** be the accepted one.
* A `voided` record is never returned as a verdict by any function in this module. There is no
  parameter, flag, or fallback that makes it one.

Attempt grouping is derived from the `final_review_attempt` field inside each record, not from the
filename — the filename is a convenience for humans, and §1 explicitly forbids consumers depending
on filename convention.

#### B.4 New `ORCHESTRATOR_LOG.md` rows — no new column

`ORCHESTRATOR_LOG_COLUMNS` is unchanged (DEC-2: adding a column changes the header width of every
future table while every file on disk keeps the old width). Three new `--event` values, using the
already-open vocabulary (`run_logging.py:872` has no `choices`):

| event | when | columns populated |
|---|---|---|
| `final_review_audit_written` | a record was written | `phase=final_review`, `role=reviewer`, `iteration=<attempt>`, `task_id`, `dispatch_id`, `terminal`, `round_kind=final_review`, `result=provenance=<state>[ void_reason=<r>]`, `detail=record=final_review_audit/<key>/record.json` |
| `final_review_audit_incomplete` | the record was written but a component could not be captured | same, plus `detail=...; input=<capture_status>; report=<capture_status>; error=<capture_error>` |
| `final_review_audit_collision` | the writer refused to overwrite an already-published record | same, `result=collision`, `detail=existing=final_review_audit/<key>/` |
| `final_review_audit_write_failed` | staging or publication raised `OSError`; nothing was published | same, `result=write_failed`, `detail=stage=<boundary>; error=<message>` |
| `final_review_audit_incomplete_publication` | an abandoned `.staging/` entry with no published record was found (A.3 P6) | same, `result=incomplete_publication`, `detail=staging=<name>; files=<present list>` |

The §9 join test (`log ↔ input ↔ report identity consistency`) is then a join on `task_id` and
`dispatch_id`, columns that already exist — no schema change, exactly as DEC-2 required.

---

### D-C — Redaction policy v1.0 (DEC-4)

#### C.1 The ordering invariant, restated as code structure

```text
assemble spec -> DISPATCH -> (returns) -> capture stored spec -> redact -> digest -> write
                    ^                                              ^
        NOTHING in the redaction/audit module is reachable          redaction applies here,
        from any function on this edge                              and only here
```

Enforced two ways, both mechanical:

1. **By construction.** The only capture source is a `subprocess` call to
   `orca orchestration task-list` / `dispatch-show`, which cannot return a dispatch that does not
   yet exist. There is no in-process hook at assembly time to add.
2. **By tripwire test.** `FinalReviewObservabilityNeutralityTests` patches
   `run_logging.redact_text` (and `capture_stored_task_spec`, `write_final_review_audit_record`)
   with `side_effect=AssertionError("reached from the dispatch path")`, then drives a full
   workflow through spec assembly and the dispatch call. Any call from that edge fails the test.

#### C.2 The pure function

```python
def redact_text(text: str, *, policy_version: str = FINAL_REVIEW_REDACTION_POLICY_VERSION
               ) -> tuple[str, tuple[dict[str, int], ...]]:
    """Deterministic. Pure function of (text, policy_version). No randomness, no salt,
    no clock, no environment, no filesystem, no path-dependent state."""
```

Same input under the same policy → byte-identical output and identical `redactions`, on any
machine, in any process, at any time. An unknown `policy_version` raises `RunLoggingError` rather
than silently falling back — a digest is only comparable against a digest produced by the *same*
policy.

**Policy version.** `FINAL_REVIEW_REDACTION_POLICY_VERSION = "redaction/1.1"`. C.3 category 5 is a
**behaviour change** to the substituted output for the same input, so it cannot ship under the
`redaction/1.0` label: a digest recorded under `1.0` and a digest recorded under `1.1` are not
comparable, and the whole point of stamping the version into every record is that a reader can tell
them apart. Exactly one policy version is *executable* at a time — `redact_text()` keeps raising
`RunLoggingError` for any `policy_version` that is not the current constant, including
`"redaction/1.0"`. Older records keep their `"redaction/1.0"` stamp as **data**; readers read that
string, they never re-run the policy from it. Retained baseline evidence produced under `1.0` is
regenerated under `1.1` rather than relabelled (see C.7's regeneration rule).

**Idempotence (new required property).** For every input `t`:

```python
once = redact_text(t)[0]
assert redact_text(once)[0] == once          # the redacted text is a FIXED POINT of the policy
```

This is not decoration: it is what makes the policy safe to run twice — over text that has already
been through it, and over an artifact re-redacted under the same version — without the output
drifting. Each category's pattern is written so that its own replacement token cannot re-trigger a
different substitution: category 4's `(?!<|\{)` lookahead, and category 5's leading `(?!<)` plus
the fact that `<REDACTED:foreign_absolute_path>` contains no `/`, are what make the fixed point
hold. (Category 3 may *re-count* a `NAME=<REDACTED:env_secret_pattern>` pair on a second pass, but
it rewrites it to the identical bytes, so the text is still a fixed point; the fixed point is
defined over the **text**, not over the counts.)

**What idempotence is NOT.** It is a property of the *policy*, never a certificate about a *value*.
`redact_text(v)[0] == v` says only that another pass changes nothing; a dangerous string the policy
does not recognise satisfies it trivially, which makes "it is a fixed point" worthless as a safety
proof and circular as a validator. C.7's **P-PATH** therefore does not use it: P-PATH is stated as a
**closed set of allowed output categories** a retained path field must *land in*, so safety is a
property of what the value **is**, not of whether some regex happened to match it.

#### C.3 Categories, patterns, replacement tokens — applied in this exact order

Order is part of the policy, not an implementation detail: a `dcap_` token appearing inside an
environment assignment is redacted and counted by `orca_dispatch_capability`, not by
`env_secret_pattern`, because the first pass has already replaced it. Declaring the order is what
makes the counts reproducible.

| # | category | pattern (Python `re`, `re.MULTILINE` where noted) | replacement |
|---|---|---|---|
| 1 | `orca_dispatch_capability` | `r"\bdcap_[A-Za-z0-9_\-]{8,}"` | `<REDACTED:orca_dispatch_capability>` |
| 2 | `url_credential` | `r"\b([A-Za-z][A-Za-z0-9+.\-]*)://[^\s/@:]+:[^\s/@]+@"` | `\1://<REDACTED:url_credential>@` |
| 3 | `env_secret_pattern` | `r"(?i)\b([A-Z0-9_]*(?:SECRET\|TOKEN\|PASSWORD\|PASSWD\|API_?KEY\|PRIVATE_KEY\|ACCESS_KEY)[A-Z0-9_]*)\s*([=:])\s*(\"[^\"\n]*\"\|'[^'\n]*'\|[^\s\n]+)"` | `\1\2<REDACTED:env_secret_pattern>` (the **name** is kept, only the value is replaced) |
| 4 | `absolute_local_path` — **home-rooted** paths only | see C.3.1 (`_HOME_ABSOLUTE_PATH`) | `\1<REDACTED:absolute_local_path>` — **only the user-name segment** is replaced |
| 5 | `foreign_absolute_path` — every **other** absolute path | see C.3.1 (`_FOREIGN_ABSOLUTE_PATH`) | `<REDACTED:foreign_absolute_path>` — the **whole** matched path is replaced |

#### C.3.1 The two path categories, verbatim

The two path patterns are given here rather than in a table cell because escaping them into one
would make them unreadable and therefore unimplementable. These are the authoritative literals;
IMPLEMENTATION copies them, it does not re-derive them.

```python
# A path-segment character. Excludes the separator, whitespace, the quoting/bracketing
# punctuation a path is normally embedded in, and the angle brackets a placeholder uses,
# so a match stops at prose punctuation and never swallows a `<PLACEHOLDER>`.
_PATH_SEGMENT = r"[^/\s\"'`,;:<>|)\]}]+"

# Category 4 -- home-rooted. UNCHANGED from redaction/1.0. Replaces the user-name segment
# only, so the remainder of the path stays semantically readable.
_HOME_ABSOLUTE_PATH = re.compile(r"(/Users/|/home/|/root/)(?!<|\{)[^/\s\"'`,;:)\]}]+")

# Category 5 -- NEW in redaction/1.1. Every absolute POSIX path that category 4 does not
# already own, with NO minimum segment count: a leading "/" at a safe left boundary
# followed by ONE OR MORE segments. Replaces the ENTIRE match.
_FOREIGN_ABSOLUTE_PATH = re.compile(
    r"(?:(?<=file://)|(?<![\w>/]))"      # left boundary: start-of-text, or a delimiter that is
                                        #   not a word char, not '>' (so `<ROOT>/a/b` survives),
                                        #   and not '/' (so `https://h/a/b` never matches);
                                        #   `file://` is the one scheme that IS a local path.
    r"/(?!/)"                           # a single leading separator, never the '//' of a URL
    r"(?!Users/|home/|root/)"           # category 4 owns these -- do not double-redact, and do
                                        #   not swallow the readable tail it just preserved
    r"(?!<)"                            # not an already-placeheld '/<...'
    rf"{_PATH_SEGMENT}(?:/{_PATH_SEGMENT})*"   # >= 1 segment -- NO floor. '/luminous',
                                        #   '/workspace-501' and '/session-<uuid>' are
                                        #   one-segment and environment-identifying; a
                                        #   segment-count carve-out is a fail-open hole
                                        #   (D3-001), not a safety property.
)
```

Notes that matter for implementation:

* **Why category 5 exists, and why a prefix list can never be the rule.** In `redaction/1.0`,
  category 4 *was* the whole absolute-path rule, and it recognised exactly three prefixes. A real
  retained baseline therefore carried
  `/private/tmp/claude-501/-Users-luminous-aiAssistedProjects-orca-skills/<session-uuid>/scratchpad/…`
  through untouched: the username, the numeric uid, the session UUID and the encoded workspace were
  all still there, and the record reported **zero** redactions. The defect is not a missing prefix —
  it is that a redaction rule stated as an *allowlist of dangerous roots* fails open on every root
  nobody thought of. `redaction/1.1` inverts the posture: category 4 keeps its narrow,
  readability-preserving job for the three home spellings, and **category 5 catches everything
  else, by default**. A new scratch root, a new sandbox layout, a new CI runner path is covered on
  the day it appears, with no policy edit.
* **Category 4 (unchanged) replaces only the user-name segment**, keeping the `/Users/` prefix and
  everything after the segment, so `/Users/luminous/aiAssistedProjects/orca-skills/scripts/x.py`
  becomes `/Users/<REDACTED:absolute_local_path>/aiAssistedProjects/orca-skills/scripts/x.py`. The
  path stays *semantically* readable — which is what "Reviewer-visible semantic content는 재현
  가능하게 유지하되" asks for — while the identifying segment is gone. The `(?!<|\{)` lookahead is
  copied from `validate_skills.py:67-69` so an already-placeheld `/Users/<name>/` is not
  double-redacted. Windows `C:\Users\<name>` is deliberately **not** in v1.1: `COMPATIBILITY.md`
  does not claim Windows support for the runtime path, and an untested pattern is worse than a
  stated gap. It is a MINOR-bump candidate.
* **Category 5 replaces the whole path, and keeps no fragment of it** — not the basename, not the
  first segment, not a length hint, not a hash. `/private/tmp/claude-501/-Users-luminous-…/<uuid>/
  scratchpad/os22_baseline_workspace/REPORT.md` becomes exactly
  `<REDACTED:foreign_absolute_path>` — a fixed literal that borrows nothing from the input.
  Partial preservation was considered and **rejected**: every candidate fragment is itself
  environment-identifying in some real layout (the basename can be a session UUID, the first
  segment can be a username-derived mount point), so a "keep the safe part" rule would be the same
  allowlist mistake one level down. Readability is preserved where it is *provably* safe — the
  three home spellings (category 4) and repository-relative paths, which have no leading `/` and
  are never touched by either category — and surrendered everywhere else. What is lost is only the
  environment-specific spelling of a location; **which** artifact is meant stays recoverable from
  the retained `artifact_path` and the `*_digest_pre_redaction` next to it.
* **No segment-count floor — the one-segment case is covered, not excused (D3-001).**
  `redaction/1.1`'s first draft required two or more segments, on the reasoning that "a bare `/tmp`
  identifies nobody". That reasoning generalises wrongly: `/luminous`, `/workspace-501` and
  `/session-<uuid>` are also one segment and each identifies a user, a uid-derived workspace or a
  session. The floor was therefore an *exclusion carved out of the default-deny rule*, which is the
  same fail-open shape as `redaction/1.0`'s prefix allowlist, one level down. It is removed:
  category 5 matches one or more segments. The price is that a standalone `/tmp`, `/etc` or a
  slash-command-looking token such as `/gsd-plan-phase` in prose is also replaced. That cost is
  accepted deliberately — over-redaction is the fail-closed direction, it is bounded (the token is
  replaced, the surrounding sentence is not), and none of the four legitimate cases below is
  affected. **The policy label stays `redaction/1.1`**, and C.2's versioning rule is not weakened
  by that: `1.1` has never shipped — it is introduced by this same design and no artifact has been
  produced under it — so removing the floor edits an *unreleased* policy rather than changing the
  output of a released one. Every retained artifact on disk today carries the `1.0` stamp and is
  regenerated once, under the final `1.1`, per C.7's regeneration rule.
* **Boundary behaviour, stated so it is testable rather than discovered.** Category 5 matches:
  `/private/tmp/a/b`, `/var/folders/x/y`, `/tmp/a/b`, `/opt/x/y`, `/etc/passwd`,
  `file:///private/tmp/a/b` (→ `file://<REDACTED:foreign_absolute_path>`), **and every one-segment
  absolute path — `/tmp`, `/luminous`, `/workspace-501`, `/session-<uuid>`**. Category 5 does
  **not** match, and these four exemptions are the ones the design guarantees: `https://host/a/b`
  and any other non-`file` `scheme://` URL (the `(?!/)` and the `/`-excluding lookbehind together
  make both slashes unreachable); `<ARTIFACT_ROOT>/final_review_audit/x` and any other
  placeholder-rooted path (leading `/` preceded by `>`); `artifacts/runs/run_x/y` and every other
  repo-relative path (no leading `/` at a boundary); and `PASS/FAIL`, `and/or`, `redaction/1.1`
  (leading `/` preceded by a word character). `/Users/<REDACTED:absolute_local_path>/…` — category
  4's output — is excluded by name, and its tail is protected by the same `>` lookbehind.
* Terminal handles (`term_…`), Task ids (`task_…`), Dispatch ids (`ctx_…`), `capability_hash`
  and `process_incarnation` are **not** redacted. §1 explicitly requires reviewer terminal
  identity; the rest are identifiers or already-hashed values, not credentials.
  `process_incarnation` embeds a workspace path, so it passes through category 4 like any other
  text and is redacted there.
* Redaction is applied to **both** retained artifacts — the input and the report — under the same
  policy version, because a report can quote a path or a command line. It is applied to the
  free-form string fields of `record.json` at the same choke point. And, per **C.7**, every
  retained *path* field is required to satisfy the same safety property even though several of them
  are produced by a different code path that never calls `redact_text()` on the whole record.

#### C.4 The `redactions` record shape

```jsonc
"redactions": [ {"category": "orca_dispatch_capability", "count": 1},
                {"category": "absolute_local_path",      "count": 12} ]
```

* An ordered list, in the **policy order above**, containing an entry only for a category that
  matched at least once. A category that matched zero times is absent, not present with `0` —
  so `redactions: []` unambiguously means "nothing was substituted".
* **No offsets and no per-occurrence digests in v1.0.** DEC-4 permits a per-occurrence digest and
  leaves the offsets question to DESIGN; the decision is to record neither. Offsets plus the
  retained text localize the removed value and reveal its length, which is a leak channel the
  category+count form does not have, and nothing in §4 or §9 needs them. `redactions` therefore
  contains no redacted value and no reversible encoding of one, by construction rather than by
  review. Adding either later is an additive MINOR bump.

#### C.5 Digests and the four identity-metadata fields

```python
def sha256_text(text: str) -> str:   # returns "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
def sha256_bytes(data: bytes) -> str
```

SHA-256, lowercase hex, prefixed `sha256:` so the algorithm travels with the value and a future
algorithm change is not a silent reinterpretation.

| field | computed over |
|---|---|
| `input_digest_pre_redaction` | the exact captured spec string, UTF-8 encoded, **no normalization** — no newline translation, no strip, no unicode normalization |
| `redaction_policy_version` | the literal `FINAL_REVIEW_REDACTION_POLICY_VERSION` in force for that write |
| `artifact_digest_post_redaction` | the exact bytes written to `<key>/input.md` |
| `redactions` | as C.4 |

**`<key>/input.md` and `<key>/report.md` contain the redacted text and nothing else** — no
header, no front-matter, no provenance comment. That is deliberate: it makes
`artifact_digest_post_redaction` re-verifiable by the trivial
`sha256_bytes(path.read_bytes()) == record["stored_task_spec"]["artifact_digest_post_redaction"]`,
with no need to know how to skip a header. All metadata lives in `<key>/record.json`.

#### C.6 In-memory-only raw handling

```python
raw = capture_stored_task_spec(run_id, task_id)      # str, in memory
pre_digest = sha256_text(raw)                        # the only thing that survives
redacted, counts = redact_text(raw)                  # in memory
path.write_text(redacted, encoding="utf-8")          # only the redacted form touches disk
del raw
```

No temporary file, no staging path, no `NamedTemporaryFile`, no writing raw text into a subprocess
argument or environment variable. The captured bytes arrive on the capture subprocess's **stdout
pipe** (never a file), are parsed in memory, redacted in memory, and only the redacted form is
written. This deletes the leak class rather than mitigating it.

**What "redaction 전후 identity 검증 가능" means operationally**, since raw bytes are not retained:
given the same source input, re-running the pipeline reproduces `input_digest_pre_redaction`,
`artifact_digest_post_redaction` and the same `redactions` list. That is the testable property
(T-3), and C.2's determinism is what makes it hold.

#### C.7 Retained **path** fields — the scope rule, and why it is stated separately

C.2–C.6 describe one pipeline: capture → `redact_text()` → write. But a retained artifact can carry
a filesystem path in a field that **never goes through that pipeline**. `report.contract_path` is
the concrete case: it is written at record-construction time by `_relative_artifact_path()`
(`run_logging.py:1598-1608`), a different code path that is not in
`FINAL_REVIEW_REDACTED_METADATA_FIELDS` and is not covered by the redaction choke point. That is
exactly how a raw
`/private/tmp/claude-501/-Users-<user>-…/<session-uuid>/scratchpad/…/REPORT.md` reached a shipped
`record.json` while the record truthfully reported `"redactions": []`. So the policy is stated once
more, as a **field-level property that holds regardless of which code path wrote the value**:

> **P-PATH (closed output-category rule).** Every string field of a retained artifact that can hold
> a filesystem path lands, on disk, in exactly one of these four **output categories**. They are
> mutually exclusive by construction — the P4 literal and a URL both carry characters
> `_SAFE_RELATIVE_PATH` forbids — so no test order can change the answer; the normalizer below
> tests P4/P3 → P2 → P1 only because that reads most directly:
>
> | | category | mechanical test on the final value `v` |
> |---|---|---|
> | **P1** | `<ARTIFACT_ROOT>`-relative | `_SAFE_RELATIVE_PATH.fullmatch(v)` |
> | **P2** | repository-relative | `v.startswith("<REPO>/")` **and** `_SAFE_RELATIVE_PATH.fullmatch(v[len("<REPO>/"):])` |
> | **P3** | not a filesystem path at all | `v == ""` **or** `_NON_FILE_URL.fullmatch(v)` |
> | **P4** | fully replaced | `v == "<REDACTED:foreign_absolute_path>"`, the whole value and nothing else |
>
> There is no fifth category, **no segment-count condition, and no "the policy left it unchanged"
> case**. Any value outside P1–P4 — a leading-`/` path of any length, a partially-substituted path
> such as `/Users/<REDACTED:absolute_local_path>/a/b`, a Windows path, a path with a `..` segment,
> a string with embedded whitespace or prose — is **replaced in full** by the P4 literal. Safety is
> a property of the category the output lands in, never of whether a pattern happened to match the
> input, so a shape nobody anticipated fails **closed** instead of passing as a fixed point.

**The three literals, authoritative** (IMPLEMENTATION copies them, next to C.3.1's):

```python
FOREIGN_PATH_PLACEHOLDER = "<REDACTED:foreign_absolute_path>"

# A plain relative POSIX path: at least one character, no leading '/', no '..' segment,
# no whitespace, no angle bracket (so a placeholder can never masquerade as P1/P2), no
# backslash and no drive letter (so a Windows path is never mistaken for a relative one),
# and no '://' anywhere (so a URL -- 'file://...' in particular -- is decided by the URL
# test above and can never fall through to here as a "relative path").
_SAFE_RELATIVE_PATH = re.compile(r"(?!/)(?![A-Za-z]:)(?!(?:.*/)?\.\.(?:/|$))(?!.*://)[^\s<>\\]+")

# A URL that is not a local file: any scheme except 'file', and no whitespace.
_NON_FILE_URL = re.compile(r"(?!(?i:file):)[A-Za-z][A-Za-z0-9+.\-]*://\S*")
```

**The normalizer is total — it cannot fall through.** One function decides the category and, for
anything outside it, substitutes; it is applied to every field in the closed table below at
record-assembly time, whatever produced the value:

```python
def normalize_retained_path_field(value: str) -> str:
    """Total: every input maps into exactly one of P1..P4. Never calls redact_text()."""
    if value == "" or value == FOREIGN_PATH_PLACEHOLDER:          # P3 (empty) / P4
        return value
    if _NON_FILE_URL.fullmatch(value):                            # P3
        return value
    if value.startswith("<REPO>/") and _SAFE_RELATIVE_PATH.fullmatch(value[len("<REPO>/"):]):
        return value                                              # P2
    if _SAFE_RELATIVE_PATH.fullmatch(value):                      # P1
        return value
    return FOREIGN_PATH_PLACEHOLDER                               # everything else, whole value

def assert_retained_path_field(value: str) -> None:
    """Fail-closed postcondition. Raises rather than write a value outside P1..P4."""
    if normalize_retained_path_field(value) != value:
        raise RunLoggingError(f"P-PATH violation in a retained path field: {value!r}")
```

`normalize_retained_path_field("/luminous")`, `("/tmp")`, `("/session-1f2e…")` and
`("/Users/<REDACTED:absolute_local_path>/repo/x.py")` all return `FOREIGN_PATH_PLACEHOLDER`: the
one-segment case is covered by the *absence* of any relative-path match, not by a pattern that has
to recognise it, which is precisely why D3-001's loophole cannot reappear here. Category 4's
readability-preserving substitution is a **free-text** behaviour (C.3) and deliberately does not
apply to a whole-value path field — a path field pointing outside both owned roots is by definition
environment-specific, and there is no reviewer-visible semantics to preserve in its spelling.

**The resolution ladder** — `_relative_artifact_path(path, root)` gains one rung and one
postcondition, and keeps its name and signature:

| # | test | result |
|---|---|---|
| 1 | `path.resolve()` is under `root.resolve()` (`<ARTIFACT_ROOT>`) | `path.relative_to(root).as_posix()` — **unchanged behaviour** |
| 2 | else `path.resolve()` is under the **project root** (see below) | `"<REPO>/" + path.relative_to(project_root).as_posix()` |
| 3 | else | the literal `FOREIGN_PATH_PLACEHOLDER` — the **whole** value. `redact_text()` is **not** called here: whether the free-text policy recognises the path is irrelevant to whether the field is safe |
| — | **postcondition, always checked** | `assert_retained_path_field(returned_value)` — the value must be in P1–P4, else `RunLoggingError`. The check is a re-run of the total classifier, not a fixed-point test, so it cannot pass a value merely because no pattern matched it |

Rung 2 is why the answer to "redact it, or make it logical?" is **both, in that order**: a report
that genuinely lives in the repository keeps a readable, environment-independent identity
(`<REPO>/artifacts/x/FINAL_REVIEW.md`), and only a path that is outside every root this project
owns — which is, by definition, environment-specific — falls through to redaction.

**The project root is derived from `root`, not discovered.** `_relative_artifact_path(path, root)`
keeps its exact name and signature; `root` is always `<ARTIFACT_ROOT>`, which
`_ensure_run_artifact_root()` (`run_logging.py:306`) builds as
`<base>/artifacts/runs/<run_id>` — anchored at the caller's `base`, defaulting to the current
working directory and *never* to this file's own location, which the module docstring forbids and
OS-22 does not change. Rung 2 therefore computes:

```python
resolved = root.resolve()
project_root = (resolved.parents[2]
                if resolved.parent.name == "runs" and resolved.parents[1].name == "artifacts"
                else None)      # shape unrecognised -> rung 2 is skipped, rung 3 applies
```

No `.git` discovery walk, no `__file__` inspection, no environment variable, no second notion of
"the repository" — introducing one is exactly how two functions come to disagree about where a path
lives, and it would also make the ladder depend on state the record cannot reproduce. The shape
guard is what keeps rung 2 honest when a caller passes an unusual `base`, and skipping to rung 3 is
the fail-closed direction. Because `<ARTIFACT_ROOT>` is nested inside `project_root` by
construction, rungs 1 and 2 are ordered and non-overlapping.

**The postcondition is the actual guarantee.** Rungs 1–3 are how the value is normally produced;
the check is what makes the field safe even if a future contributor adds a fourth production path.
It is deliberately **not** a fixed-point test. `redact_text(v)[0] == v` proves only that a second
pass changes nothing, so every value the policy fails to recognise passes it — the writer would
have accepted a raw `/luminous` on exactly that reasoning (D3-001). Re-running the total classifier
instead asks the only question that matters — *which of the four allowed categories is this value
in?* — and has no "unrecognised" answer to give.

**Scope — the closed list of path-bearing fields P-PATH governs.** Every field below is passed
through `normalize_retained_path_field()` and then through `assert_retained_path_field()` at
record-assembly time, **whatever produced its value**; the "how P-PATH is met" column says how the
value normally arrives in an allowed category, not which fields are exempt from the check — none
are:

| field | producer | how P-PATH is met |
|---|---|---|
| `report.contract_path` | `_relative_artifact_path()` | the ladder above |
| `stored_task_spec.artifact_path`, `report.artifact_path` | constructed from `final_review_audit/<key>/…` | **P1** by construction — always `<ARTIFACT_ROOT>`-relative — and still re-checked |
| `delivery_evidence.process_incarnation` | runtime string embedding a workspace path | it is a free-form string that *contains* a path rather than being one, so it stays in `FINAL_REVIEW_REDACTED_METADATA_FIELDS` and is covered by the C.3 choke point, where category 5 — now with no segment floor — replaces every absolute path it embeds, one-segment included |
| the free-form fields in `FINAL_REVIEW_REDACTED_METADATA_FIELDS` (`capture_error`s, `failure_detail`, `notes`, `parse_error`, …) | various | the redaction choke point, now under `redaction/1.1` |
| `<key>/input.md`, `<key>/report.md` contents | the C.2 pipeline | the redaction choke point |
| D-F bundle: `orchestrator_log.path`, `attempts[].dispatches[].input.path` / `.report.path` | export, from record fields | **P1** by construction; the inlined `record` inherits the record's own compliance; the exporter runs the same assertion before serialising |
| D-E scorer: `findings_source` (E.5), `source_report` (E.2) | the scorer, from a CLI argument | the scorer applies the same ladder and the same `normalize_retained_path_field()` / `assert_retained_path_field()` pair before serialising, so a metrics document produced from a scratch workspace cannot embed that workspace's absolute path. `scripts/final_review_eval.py` imports `redact_text` and the ladder from its sibling `scripts/run_logging.py` rather than re-implementing them — "standard library only" (D-E preamble) forbids third-party dependencies, not reuse of this repository's own module, and a second copy of a redaction policy is precisely the drift R1 punished |

**Adding a path-bearing field later is not free.** A new field that can hold a path must be added
to this table and to the validator's check, in the same commit — the same closed-list discipline
`FINAL_REVIEW_REDACTED_METADATA_FIELDS` already uses, and for the same reason: a field that is
absent from the list is not covered, and that must be visible here rather than inferred from an
assembly expression.

**Regeneration of already-retained evidence.** The baseline audit artifacts under
`artifacts/runs/run_ff587481a820/final_review_audit/` were produced under `redaction/1.0` and
contain a raw non-home absolute path in `input.md`, in `record.json.report.contract_path` and in
the exported bundle. They are **regenerated** under `redaction/1.1` as part of the IMPLEMENTATION
work item that lands category 5 — not hand-edited, because a hand-edited artifact is no longer
evidence of what the pipeline produces, and not left in place with a `1.0` stamp, because §4's
retention requirement is about the bytes on disk, not about the label on them. D-A's immutability
rule is not violated: a *republished* baseline is a new capture under a new policy version, and the
superseded directory is removed in the same commit rather than silently overwritten in place.

---

### D-D — Fixture design (DEC-7)

#### D.1 Tree layout — storage separation decided before any defect content

```text
scripts/fixtures/final_review_eval/
    README.md                 <- describes the FIXTURE. Says nothing about what is seeded,
                                 how many defects exist, or where. Names no key path.
    subject/
        base/                 <- "v1.0" of the subject project (pre-feature)
            CONTRACT.md
            src/__init__.py  src/config.py  src/policy.py  src/quota.py
            src/validation.py  src/pipeline.py
            tests/__init__.py  tests/test_config.py  tests/test_policy.py
            tests/test_quota.py  tests/test_pipeline.py  tests/test_validation.py
        head/                 <- the same project after the feature under review.
                                 Same file list. The five seeded defects live in this diff.
    key/
        answer_key.json       <- NEVER inside subject/. NEVER referenced from subject/.
    adjudications/
        README.md             <- how to author an adjudication input (D-E). No verdicts shipped.
```

`subject/` holds **no** key, no README describing defects, no marker comment, no defect id, no
`# SEEDED` / `# BUG` / `# FIXME` annotation, and no reference to `key/` or `adjudications/`.

**Why two trees rather than one tree plus a stored patch.** §17's input paragraph
(`SKILL.md:1733-1737`) hands the Final Reviewer "전체 diff(base..HEAD)" by path. A reviewable diff
is therefore part of the fixture's realism, not an extra. Storing a *patch file* that goes from a
defect-free state to a defective state would be a direct leak — the patch would literally be the
seeded change list. Storing two ordinary project states and **deriving** the diff at
materialization time gives the reviewer exactly what a real review gets: a feature diff that
happens to contain defects, with no provenance saying so. The diff is generated with
`difflib.unified_diff` (stdlib) at materialize time, so it cannot drift from the trees.

#### D.2 The subject project

A miniature record-publication library: `resolve_settings` (config ladder), `resolve_tier` /
`tier_limits` (retention tiers), `enforce_quota`, `validate_record`, and three publication entry
points. ~230 lines of Python across six modules plus a written `CONTRACT.md` and a green test
suite.

**The feature under review (`base` → `head`): per-destination retention tiers.** `head` adds a
`TIERS` table, a `retention_tier` resolution step, a `destination` source in the config ladder, a
`tier=` parameter on `enforce_quota`, and a `republish` retry entry point. All five seeded defects
are introduced *by that feature work* — none is a gratuitous edit to unrelated code.

`subject/head/CONTRACT.md` (the contract evidence a reviewer must cross-read; written in English
so the leak scanner and the matcher have one language to normalize):

```markdown
# Record Publication Contract v2

## 1. Settings resolution
Effective settings are resolved by first match wins over four sources, highest first:
  1. explicit override (call argument)   2. destination config
  3. project defaults                    4. built-in defaults
A higher source overrides a lower one. A lower source never overrides a higher one.

## 2. Retention tiers
A destination's `retention_tier` replaces the default tier only when its value names a
tier that exists in `TIERS`. An unknown value, a typo, or an empty string is not a tier:
resolution falls back to `default`.

## 3. Quota
A publication is rejected when it would **exceed** the tier's `max_items`.
A publication of exactly `max_items` records is accepted.

## 4. Tier applies to every publication path
Every path that publishes records -- `publish_one`, `publish_batch`, `republish` --
evaluates quota against that destination's resolved tier.

## 5. Validation scope
Every path that writes a record to the store validates it first with `validate_record()`.
There is no exempt path.
```

#### D.3 The five seeded defects

Each entry gives (a) archetype, (b) the diff that introduces it, (c) the negative-space argument,
(d) the scorer's matching criterion. A sixth line records where the defect sits on DEC-7's
recognition-vs-representativeness line.

---

**SD-1 — value-vs-presence** · `subject/head/src/policy.py::resolve_tier`

*(b) diff*

```diff
--- a/src/policy.py
+++ b/src/policy.py
@@
-def resolve_tier(settings):
-    """v1 had no per-destination tier: everything used the built-in default."""
-    return "default"
+def resolve_tier(destination, settings):
+    """CONTRACT.md 2: a destination's retention_tier replaces the default tier."""
+    if "retention_tier" in destination:
+        return destination["retention_tier"]
+    return settings.get("retention_tier", "default")
+
+
+def tier_limits(tier):
+    """The limits for a tier. A tier with no entry has no configured limit."""
+    return TIERS.get(tier) or {"max_items": None, "require_signature": False}
```

*(c) negative-space argument.* `grep retention_tier subject/` returns six hits — the `TIERS`
table, `BUILTIN_DEFAULTS`, this function's two lines, a test, and `CONTRACT.md` — and **every one
of them looks correct in isolation.** There is no suspicious token to find, because the defect is
the *absence* of a membership test against `TIERS`. Localizing it requires cross-reading four
places that are not adjacent: `CONTRACT.md` §2's "names a tier that exists in `TIERS`" sentence,
`src/config.py`'s `TIERS` keys, this function's presence-only `in` check, and
`tier_limits()`'s `or {...}` fallback — which is the line that converts the missed validation into
a real consequence (an unknown tier yields `max_items: None`, and D.2's `enforce_quota` treats a
`None` limit as unlimited, silently dropping both the quota and `require_signature: True` for the
`archival` tier). No single grep spans that chain.

*(d) matching criterion.* `location.file == "src/policy.py"`, `symbol == "resolve_tier"`,
`line_range` the function body; claim requires **two** groups — a presence-vs-value group and an
unknown/invalid-tier-accepted group (exact surface forms in D.4's key excerpt).

*Recognition line.* Archetype-faithful, domain-synthetic. The `if "x" in cfg` shape is the generic
form of the archetype; nothing in the module resembles this repository's vocabulary.

---

**SD-2 — omitted call-site / propagation** · `subject/head/src/pipeline.py::publish_batch`

*(b) diff*

```diff
--- a/src/quota.py
+++ b/src/quota.py
@@
-def enforce_quota(store, settings):
-    return len(store) <= settings["max_items"]
+def enforce_quota(store, settings, tier="default"):
+    limit = tier_limits(tier).get("max_items")
+    if limit is None:
+        return True
+    return len(store) < limit
--- a/src/pipeline.py
+++ b/src/pipeline.py
@@ def publish_one(store, record, settings, destination):
     validate_record(record)
-    if not enforce_quota(store, settings):
+    tier = resolve_tier(destination, settings)
+    if not enforce_quota(store, settings, tier=tier):
         raise QuotaExceeded(record["id"])
     return _write_record(store, record)
@@ def publish_batch(store, records, settings, destination):
     for record in records:
         validate_record(record)
     if not enforce_quota(store + list(records), settings):
         raise QuotaExceeded("batch")
```

The `publish_batch` signature gained `destination` (so it type-checks and reads as updated) but its
`enforce_quota` call was left at the pre-feature two-argument form, silently taking `tier="default"`.

*(c) negative-space argument.* `grep enforce_quota subject/` returns three hits — one definition
and two call sites — and **both call sites are syntactically valid calls to the new signature**,
because the new parameter has a default. There is no error, no warning, no odd token. The defect
is a missing *argument*, so it has no textual footprint at all. Localizing it requires reading the
new signature in `src/quota.py`, then both call sites in `src/pipeline.py` **side by side** (the
asymmetry is only visible in comparison, not in either line alone), then `CONTRACT.md` §4's "Every
path ... `publish_one`, `publish_batch`, `republish`", and then noticing `tests/test_pipeline.py`
exercises `publish_batch` only against a destination whose tier *is* `default`, so the wrong
default is indistinguishable from the right answer in every existing test.

*(d) matching criterion.* `location.file == "src/pipeline.py"`, `symbol == "publish_batch"`, with
a **line-range disambiguator** — SD-2 and SD-5 are both in this file, which is exactly why D-E's
matcher needs symbol/line disambiguation and why a claim-only matcher would be unsound here.
Claim requires a "tier/argument not passed/propagated" group and a "batch path / default tier"
group.

*Recognition line.* Archetype-faithful, domain-synthetic. The default-parameter-absorbs-the-
omission mechanism is the archetype's essence and is reproduced exactly.

---

**SD-3 — equality / boundary** · `subject/head/src/quota.py::enforce_quota`

*(b) diff* — the second hunk of SD-2's `src/quota.py` diff above: `len(store) <= settings["max_items"]`
became `len(store) < limit`. `CONTRACT.md` §3 says a publication is rejected when it would
**exceed** `max_items`, and one of exactly `max_items` is accepted; `<` rejects at exactly the
limit.

*(c) negative-space argument.* `<` and `<=` appear elsewhere in the tree legitimately, so no
comparison operator is inherently suspicious, and the changed line *is* in the diff — but the diff
shows a line that was rewritten for a different, legitimate reason (the tier lookup), so the
comparison change reads as incidental. Localizing it requires reading `CONTRACT.md` §3's
"exceed" / "exactly `max_items` is accepted" wording against the operator, and then checking
`tests/test_quota.py`, which exercises 50 and 150 against a limit of 100 and **never** 100 — the
boundary is the one value the suite does not test. This is the "D implementation vs tests" axis of
§17's checklist, and no string search reaches it.

*(d) matching criterion.* `location.file == "src/quota.py"`, `symbol == "enforce_quota"`; claim
requires a boundary/off-by-one group and an "exactly at the limit is rejected / should be
accepted" group.

*Recognition line.* Archetype-faithful, domain-synthetic — with the honest note that this is the
most generic of the five and therefore the one most likely to be found by a reviewer who is simply
careful, independent of search strategy. That is a property of the archetype, not a flaw in the
seeding.

---

**SD-4 — losing precedence / fallback** · `subject/head/src/config.py::resolve_settings`

*(b) diff*

```diff
--- a/src/config.py
+++ b/src/config.py
@@
-def resolve_settings(explicit, project):
-    merged = dict(BUILTIN_DEFAULTS)
-    merged.update(project)
-    merged.update(explicit)
-    return merged
+def resolve_settings(explicit, destination, project):
+    """CONTRACT.md 1: explicit > destination > project > builtin."""
+    return {**explicit, **destination, **project, **BUILTIN_DEFAULTS}
```

*(c) negative-space argument.* This is the sharpest negative space of the five, because the code
**contains all four sources in the textually correct order** — `explicit, destination, project,
BUILTIN_DEFAULTS`, reading left to right exactly as `CONTRACT.md` §1 lists them — and carries a
docstring that restates the correct ladder. Any grep for the source names, for `explicit`, or for
`resolve_settings` lands on a line that looks like the contract. The defect is that a dict-splat
literal resolves **later keys last-wins**, so the textual order is precisely inverted from the
precedence order: `BUILTIN_DEFAULTS` overrides everything and an explicit override never takes
effect. Localizing it requires knowing Python's `{**a, **b}` semantics *and* cross-reading the
ladder in `CONTRACT.md`; and `tests/test_config.py` populates exactly one source per case, so
precedence — the only thing that could expose it — is never exercised by any existing test.

*(d) matching criterion.* `location.file == "src/config.py"`, `symbol == "resolve_settings"`;
claim requires a precedence/override group and an "order inverted / lower source wins / builtin
overrides explicit" group.

*Recognition line.* Archetype-faithful, domain-synthetic, and **language-idiomatic**: the defect
depends on a real Python semantic rather than on invented domain rules, which is what keeps it
from being a puzzle. This is the one where the fixture leans furthest toward "realistic" and
furthest from "this repository", so recognition risk is lowest.

---

**SD-5 — validation-scope gap** · `subject/head/src/pipeline.py::republish`

*(b) diff*

```diff
--- a/src/pipeline.py
+++ b/src/pipeline.py
@@
+def republish(store, record, settings, destination):
+    """Retry path for a publication that already failed downstream of validation."""
+    tier = resolve_tier(destination, settings)
+    if not enforce_quota(store, settings, tier=tier):
+        raise QuotaExceeded(record["id"])
+    return _write_record(store, record)
```

*(c) negative-space argument.* `grep validate_record subject/` finds the definition, two call
sites, a dedicated test file, and `CONTRACT.md` §5 — the function looks thoroughly used, which is
what makes the gap invisible to a coverage-by-grep reading. The defect is a *third* writer that
does not call it. Localizing it requires enumerating call sites of the **sink** (`_write_record`,
three of them) rather than of the validator, noticing that one reaches the store without passing
through the validator, and reading `CONTRACT.md` §5's "There is no exempt path" against the
docstring's plausible-sounding excuse ("already failed downstream of validation") — an excuse that
is *false* for the retry path, since a retried record can be re-supplied by a caller and is never
re-checked. `tests/test_pipeline.py` calls `republish` only with a record that is already valid,
so the suite is green and silent.

*(d) matching criterion.* `location.file == "src/pipeline.py"`, `symbol == "republish"`, with the
line-range disambiguator that separates it from SD-2; claim requires a validation-missing group
and a "write path / retry path / bypasses" group.

*Recognition line.* Archetype-faithful, domain-synthetic. The "new entry point reaches the shared
sink without the shared guard" mechanism is the archetype exactly; the plausible-but-false
docstring is deliberate, because a validation-scope gap that announces itself is not the archetype.

---

**Second-order risk, held explicitly (DEC-7, ANALYSIS R-5).** All five archetypes were abstracted
from defects observed in *this* repository, but no defect is transplanted: the domain (record
publication and retention) shares no vocabulary with orchestration — no run, dispatch, reviewer,
attempt, phase, verdict, or artifact-root concept appears anywhere in `subject/`. Each defect is
labelled above as **archetype-faithful, domain-synthetic**, with SD-3 flagged as the most generic
and SD-4 as the most idiomatic. That is the line DEC-7 asked DESIGN to state, stated per defect.

#### D.4 Answer-key schema

`scripts/fixtures/final_review_eval/key/answer_key.json`:

```jsonc
{
  "schema_version": "1.0",
  "fixture_id": "final_review_eval/v1",
  "fixture_digest": "sha256:...",        // over the materialized head/ manifest -- see D.5
  "expected_finding_count_is_not_a_contract": true,  // literal: the key states seeded defects,
                                                     //   NOT how many findings a reviewer "should"
                                                     //   report. Nothing consumes a target count.
  "seeded_defects": [
    {
      "id": "SD-1",
      "archetype": "value_vs_presence",              // one of the five, exact spellings below
      "location": {
        "file": "src/policy.py",                     // relative to the materialized workspace root
        "symbol": "resolve_tier",
        "line_range": [24, 31]                       // inclusive, 1-based, in head/
      },
      "contract_reference": "CONTRACT.md 2",
      "summary": "resolve_tier tests for the PRESENCE of retention_tier instead of validating its VALUE against TIERS, so an unknown tier is accepted and tier_limits falls back to an unlimited quota.",
      "introduced_by": "src/policy.py resolve_tier/tier_limits hunk (base -> head)",
      "negative_space_argument": "...as D.3(c)...",
      "match_criterion": {
        "location_tolerance_lines": 6,
        "claim_requirements": {
          "all_of": [
            {"any_of": ["presence", "present", "\"in\" check", "key exists", "membership",
                        "존재 여부", "존재만"]},
            {"any_of": ["unknown tier", "invalid tier", "unrecognized tier", "not in tiers",
                        "arbitrary value", "validate the value", "값을 검증", "알 수 없는 tier"]}
          ]
        }
      }
    }
    // SD-2 ... SD-5, same shape
  ]
}
```

* `archetype` values, exact spellings, one per ticket §5 archetype:
  `value_vs_presence`, `omitted_call_site_propagation`, `equality_boundary`,
  `losing_precedence_fallback`, `validation_scope_gap`.
* `claim_requirements.all_of` is a list of groups; a group is satisfied when **any** of its
  surface forms occurs in the normalized finding text. Both English and Korean forms are listed
  because this repository's reviewers write bilingual reports. This is deliberately **not** string
  equality against the key's own wording (DEC-7 (d)): the key's `summary` text never participates
  in matching, and each group offers alternative phrasings of one concept.
* `expected_finding_count_is_not_a_contract` is a literal guard: nothing in D-E reads a target
  finding count, so §5's "expected finding count" cannot leak through the key even by accident.

#### D.5 Materialized-workspace protocol

```bash
python3 scripts/final_review_eval.py materialize --dest <dir> [--fixture <path>]
```

Produces:

```text
<dir>/
    CONTRACT.md
    src/...                      copied verbatim from subject/head/
    tests/...
    DIFF.patch                   generated: unified diff subject/base -> subject/head,
                                 a/<path> vs b/<path>, 3 lines of context, LF endings,
                                 files in sorted order  (difflib.unified_diff, stdlib)
    MANIFEST.json                {"fixture_id": ..., "fixture_digest": "sha256:...",
                                  "files": {"<relpath>": "sha256:...", ...}}
```

Rules:

1. **Refuses a non-empty `--dest`** (exit 4). No overwrite, no merge, no partial reuse.
2. **No `.git` is created and none is copied** — closing ANALYSIS R-4.2 (git history) for the
   dispatched scope. The reviewer gets `DIFF.patch`, which is what §17 hands it anyway.
3. **`key/` and `adjudications/` are never read by `materialize`** — the code path does not open
   them, so a mistake cannot copy them.
4. **Post-copy assertions, all before the command exits 0:**
   * no path component in `<dir>` is named `key` or `adjudications`;
   * the leak scan of D.6 over every file in `<dir>` returns zero hits;
   * `MANIFEST.json`'s `fixture_digest` equals the answer key's `fixture_digest` (read from the
     key **only for this comparison**, never copied) — a mismatch exits 2 and prints the computed
     digest, so a human updates the key deliberately. There is no `--update-digest`; auto-updating
     the expected value would destroy the check.
5. `fixture_digest` = `sha256` over the manifest text built as sorted
   `"<relpath>\0<sha256-hex>\n"` lines, so it is stable across filesystems and orderings.

#### D.6 Answer-key leak scan

```bash
python3 scripts/final_review_eval.py scan-leak --key <key.json> --target <path>...
```

`key_leak_tokens(key)` is defined precisely, because "every string in the key" is wrong — the key
names real symbols (`resolve_tier`) that **must** appear in `subject/`:

* every `seeded_defects[].id` (`SD-1` … `SD-5`);
* every `archetype` value (`value_vs_presence`, …);
* the `fixture_id` literal;
* every whitespace-normalized 6-word shingle of every `summary` and `negative_space_argument`;
* the fixed marker set: `answer key`, `answer_key`, `seeded defect`, `seeded_defect`,
  `expected finding`, `expected_finding_count`, `SEEDED`, `ANSWER KEY`, `정답`, `시드`.

Plus an independent **expected-count** detector: a regex for a cardinal number (digits or
`one`…`ten` or `하나`…`다섯`) within 40 characters of `finding\|defect\|issue\|bug\|결함\|발견`
combined with `expect\|should find\|총\|개의\|must find`.

Matching is case-insensitive over whitespace-normalized text. Exit 0 on zero hits, **exit 4** with
the hit list on any hit.

**Scan targets, and what each proves:**

| target | test | proves |
|---|---|---|
| `subject/` (both trees) | T-4 | no key material is stored where the reviewer's scope points (DEC-7 mitigation 1, mechanically enforced) |
| the materialized `<dir>` | D.5 rule 4 | the workspace the reviewer actually reads is clean |
| `<ARTIFACT_ROOT>final_review_audit/<key>/input.md` for the baseline dispatch | T-4, B-4 | **no key material reached the reviewer's input** — checkable only because OS-22 now retains the input |

**Documented limitation, carried into the artifact rather than dropped (DEC-7).** The scan cannot
detect a leak that consists only of naming a real symbol or file, because those must appear in
`subject/`. And `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted` (`SKILL.md:1307`) means no
storage layout makes the key *unreachable* by a reviewer that goes looking outside its scope. The
claim OS-22 makes is therefore the provable one: **no key material appears in the reviewer's
retained input**, verified mechanically per baseline run. It is not "the key was unreachable."

#### D.7 Commit hygiene (DEC-7 mitigation 4)

`subject/` lands in one commit whose message names only the fixture
(`Add final review evaluation fixture subject tree`) and says nothing about what it seeds.
`key/` and `adjudications/` land in a **separate, later** commit. This is an ordering constraint on
I-11, restated in `## Expected Changed Files / Implementation Steps`.

---

### D-E — Scorer contract (DEC-8)

New file `scripts/final_review_eval.py` (repo-side, **not** shipped in either Skill — it is not in
`release_manifest.py::required_skill_paths()` and does not need to be; it is packaged only because
`INCLUDED_ROOTS` carries all of `scripts/`). Standard library only.

#### E.1 Subcommands

```bash
python3 scripts/final_review_eval.py materialize   --dest <dir> [--fixture <dir>]
python3 scripts/final_review_eval.py verify-fixture [--fixture <dir>] [--key <path>]
python3 scripts/final_review_eval.py scan-leak     --key <path> --target <path>...
python3 scripts/final_review_eval.py parse-report  --report <path> [--out <path>]
python3 scripts/final_review_eval.py score --findings <path> --key <path>
                                           [--adjudications <path>] [--workspace <dir>]
                                           [--out <path>] [--require-precision]
                                           [--run-verdict <path> ...]
                                           [--provenance-out <path>]
```

`--provenance-out` is the **only** place `score` is allowed to read a clock, and it writes a
separate file that is not the metrics document — see E.5.

`parse-report` and `score` are separate commands on purpose: §5 requires Reviewer execution and
scoring to be separated, and separating *parsing* from *scoring* additionally makes the parse
auditable — the normalized findings JSON is an artifact a human can read before any metric is
computed.

#### E.2 Findings input schema (`--findings`)

```jsonc
{
  "schema_version": "1.0",
  "source_report": "artifacts/runs/run_x/final_review_audit/attempt1__task_a__ctx_b/report.md",
  "source_report_digest": "sha256:...",     // ties the metrics to a specific retained report
  "findings": [
    {
      "id": "R1",                            // §17 Finding Contract `ID:`
      "location_raw": "src/policy.py:27",    // §17 `Location:` verbatim
      "location_file": "src/policy.py",      // parsed; null when unresolvable
      "location_line": 27,                   // parsed; null when the finding names no line
      "severity": "MAJOR",
      "blocking": true,
      "quality_attribute": "G1",
      "responsible_phase": "implementation",
      "issue": "...",                        // §17 `Issue:`
      "reason": "...",                       // §17 `Reason / Evidence:`
      "required_action": "...",
      "raw": "...entire finding block verbatim..."
    }
  ]
}
```

`parse-report` produces this from a §11/§17-shaped report. Location parsing, in order, first match
wins: `<path>:<line>`; `<path>:<start>-<end>` (uses `start`); `<path>` followed by
`line\|L\|:` `<n>`; a bare `<path>` (line `null`). A path is accepted when it resolves to an
existing file under `--workspace` (when given) or matches `^[\w./-]+\.(py\|md)$` otherwise.
Anything else leaves `location_file: null`.

#### E.3 Adjudication input schema (`--adjudications`)

```jsonc
{
  "schema_version": "1.0",
  "adjudicator": "<identity string>",
  "adjudicated_at": "2026-08-26T10:00:00+00:00",
  "closed_world": false,
  "exhaustive_attestation": null,
  "verdicts": [
    {"finding_id": "R3", "verdict": "true_positive", "rationale": "<non-empty>"}
  ]
}
```

* `verdict` accepts exactly two values: `true_positive`, `false_positive`.
* `rationale` is required and must be non-empty after stripping.
* **Any key not in `{finding_id, verdict, rationale}` inside a verdict object is a hard error
  (exit 2).** This is DEC-8 rule 4 made structural: there is no field for "was corrected", "was
  not disputed", or any historical-corpus signal, so §6's forbidden inference is *unrepresentable*
  rather than merely discouraged. The same strictness applies at the top level: unknown top-level
  keys are an error.
* `exhaustive_attestation`, when present, is
  `{"scope": str, "statement": str, "attested_by": str, "attested_at": str}` with all four
  non-empty.
* **`closed_world` and `exhaustive_attestation` are coupled, and the coupling is validated as an
  input contract, not silently tolerated.** `closed_world == true` **requires** a complete
  `exhaustive_attestation` (object present, all four keys, all non-empty); `closed_world == false`
  **requires** `exhaustive_attestation` to be `null`. Either mismatch is a hard error (**exit 2**).
  This deletes the two half-states the file could previously express — a closed-world claim with
  nothing signed behind it, and an attestation that no computation path reads — so E.5's precision
  gate never has to decide what an unsigned closed world means.

#### E.4 Matching algorithm — deterministic, one-to-one

```text
0. Verify key.fixture_digest against --workspace's MANIFEST.json when --workspace is given.
   Mismatch -> exit 2. (Metrics computed against a different tree than the key describes
   are not metrics.)
1. Normalize each finding's claim text T = lower(collapse_ws(strip_md(issue + " " + reason
   + " " + required_action))).  Normalize key surface forms the same way.
2. For each (finding f, seeded defect k):
     claim_ok(f,k)    := every group in k.match_criterion.claim_requirements.all_of has at
                         least one member occurring as a substring of T(f)
     location_ok(f,k) := f.location_file == k.location.file
                         AND ( f.location_line is None
                               OR k.location.line_range[0] - tol <= f.location_line
                                                                 <= k.location.line_range[1] + tol )
                         where tol = k.match_criterion.location_tolerance_lines
     symbol_hit(f,k)  := k.location.symbol occurs in T(f) or in f.location_raw
3. Candidate pairs = {(f,k) : claim_ok and location_ok}.
4. Ambiguity guard: if a finding f has candidates against two or more k that share the SAME
   location.file, resolve by, in order:
       (i)  symbol_hit -- if exactly one candidate has it, that one wins;
       (ii) line proximity -- if f.location_line is not None, the candidate whose line_range
            midpoint is nearest wins, ties broken by lexicographic key id;
       (iii) otherwise f matches NOTHING and is reported in unmatched_findings with
            reason "ambiguous_match" (still UNADJUDICATED -- never an auto-FP).
   This guard is why SD-2 and SD-5, both in src/pipeline.py, are separable.
5. Assignment is one-to-one and greedy over a deterministic sort key, descending:
       (claim groups satisfied, symbol_hit, -line_distance, -key_id_lex, -finding_id_lex)
   Each seeded defect takes at most one finding; each finding matches at most one defect.
6. Every remaining finding is unmatched with reason
       no_key_match | unresolvable_location | ambiguous_match
   and classification UNADJUDICATED unless (a) an adjudication verdict names it, or (b) E.5
   point 2's closed-world rule applies to it. The matcher itself NEVER classifies; it only
   records the reason. Which of the three reasons was recorded is what E.5 point 2 keys on,
   which is why the reason is part of the contract and not a diagnostic string.
```

The algorithm reads no clock, no environment, no filesystem beyond the three input files and the
optional workspace, and does not iterate over unordered sets. E.5 additionally keeps every
clock-derived value **out of the serialized metrics document**, so B5's byte-identical-metrics
requirement holds for the whole file, by construction and with no carve-out.

#### E.5 Metric output schema

```jsonc
{
  "schema_version": "1.0",
  "fixture_id": "final_review_eval/v1",
  "fixture_digest": "sha256:...",
  "key_digest": "sha256:...",
  "findings_source": "artifacts/runs/run_x/final_review_audit/attempt1__..../report.md",
  "findings_source_digest": "sha256:...",
  "findings_total": 4,

  "adjudication_status": "none",              // none | partial | complete | complete_by_attestation
  "closed_world": false,

  "seeded_defects_total": 5,
  "detected_seeded_defects": 2,
  "seeded_recall": {"value": 0.4, "numerator": 2, "denominator": 5,
                    "population": "seeded_defects_only"},
  "miss_count": 3,
  "miss_rate": {"value": 0.6, "numerator": 3, "denominator": 5,
                "population": "seeded_defects_only"},
  "missed_defect_ids": ["SD-2", "SD-4", "SD-5"],

  "matched_findings": [
    {"finding_id": "R1", "seeded_defect_id": "SD-1",
     "claim_groups_satisfied": 2, "symbol_hit": true, "line_distance": 0}
  ],
  "unmatched_findings": [
    // classification: UNADJUDICATED | ADJUDICATED_TRUE_POSITIVE | ADJUDICATED_FALSE_POSITIVE
    //               | ATTESTED_FALSE_POSITIVE   (closed world only -- see point 2)
    {"finding_id": "R3", "reason": "no_key_match", "classification": "UNADJUDICATED"},
    {"finding_id": "R4", "reason": "unresolvable_location", "classification": "UNADJUDICATED"}
  ],
  "unadjudicated_count": 2,

  "adjudicated_true_positives": 0,
  "adjudicated_false_positives": 0,
  "attested_false_positives": 0,              // always present; non-zero only under closed world

  "precision": null,
  "precision_status": "REFUSED",              // COMPUTED | REFUSED
  "precision_refusal_reason": "adjudication_incomplete: 2 unmatched findings carry no independent adjudication verdict, and no closed_world exhaustive attestation is present",
  "false_positive_rate": null,
  "false_positive_rate_status": "REFUSED",
  "false_positive_rate_refusal_reason": "<same machine-readable reason>",

  "evidence_grounding": {
    "value": 0.75, "numerator": 3, "denominator": 4,
    "definition": "fraction of findings whose Location resolves to a file that exists in the materialized subject workspace and, when a line is given, to a line within that file",
    "ungrounded_finding_ids": ["R4"]
  },

  "verdict_reproducibility": {
    "status": "SINGLE_RUN_NOT_ASSERTED",      // OBSERVED | SINGLE_RUN_NOT_ASSERTED
    "run_count": 1,
    "verdicts": ["FAIL"],
    "agreement": null
  }
}
```

**The metrics document contains no clock-derived value — B5 holds for the whole file.**
An earlier revision emitted a top-level `generated_at` and then weakened T-4's rerun assertion to
"byte-identical apart from `generated_at`". A test-level exception is not the contracted behaviour,
and PLAN DEC-9 B5's failure condition is *any* metric difference across identical inputs, so the
field is removed rather than excepted:

* **No key of the metrics document is derived from the clock, the environment, the process, or the
  argv.** Every value above is a function of `--findings`, `--key`, `--adjudications`,
  `--workspace` and `--run-verdict` alone. `score` therefore satisfies B5 for the complete file:
  identical inputs → byte-identical metrics output, no carve-outs, no excepted keys.
* **"When was this scored?" is answered by a different file.** When — and only when —
  `--provenance-out <path>` is passed, `score` writes a **separate** provenance sidecar:

  ```jsonc
  {"schema_version": "1.0",
   "generated_at": "2026-08-26T10:11:12.131415+00:00",  // the ONLY clock read in the scorer
   "scorer_source_digest": "sha256:...",                // sha256 of final_review_eval.py
   "argv": ["score", "--findings", "f.json", "--key", "..."],
   "metrics_digest": "sha256:..."}                      // ties the sidecar to the metrics bytes
  ```

  The sidecar is **by definition outside the B5 comparison**: B5 is stated over "the scoring
  metrics output", which is the document written to `--out`, and the sidecar is a different
  artifact at a different path that the default invocation does not produce at all. It is never
  embedded in, appended to, or merged into the metrics document, and D-F's bundle references it by
  digest rather than inlining it. Its `metrics_digest` is what lets an auditor prove *which*
  metrics bytes a given timestamp describes without putting the timestamp inside them.
* **Structurally enforced, not reviewed.** T-4 asserts the property by patching every clock source
  reachable from `scripts/final_review_eval.py` (`time.time`, `time.monotonic`,
  `datetime.datetime.now`, `datetime.datetime.utcnow`) to raise, and running `score` without
  `--provenance-out` to a successful exit 0. A future contributor cannot reintroduce a timestamp
  into the metrics document without that test failing.

**The refusal/honesty properties, each mapped to a field:**

1. *Unmatched is never auto-FP — **outside closed world**.* On the default path
   (`closed_world == false`, which is every ordinary scoring run), `unmatched_findings[].
   classification` can only be `UNADJUDICATED` or, when an adjudication verdict names the finding,
   `ADJUDICATED_TRUE_POSITIVE` / `ADJUDICATED_FALSE_POSITIVE`. There is no flag, config or
   heuristic that maps unmatched → false positive on this path. **The scope of this rule is the
   default path, and point 2's closed-world rule is a narrower, explicitly-signed exception to it,
   not a contradiction of it.** The exception is stated here as well as there so that a reader of
   either point cannot come away with the wrong absolute. What makes the exception legitimate and
   the default rule necessary is the same fact: absent a signed exhaustive attestation, the answer
   key is a *sample* of the true defects, so "did not match the key" carries no information about
   whether a finding is true — and inventing an FP from it would be exactly the fabricated metric
   §6 forbids.
2. *Precision gating, and the closed-world classification rule.* The two computation paths are
   **mutually exclusive**, selected by `closed_world`, and each is complete on its own — the
   earlier "either … or" framing left both branches under-specified about the same findings, which
   is the defect this revision closes.

   **Path A — closed world (`closed_world == true`).** E.3 has already guaranteed a complete
   `exhaustive_attestation` (otherwise the run exited 2). The attestation's meaning is fixed here,
   in one sentence: *the answer key enumerates every true defect within the attested scope.* Each
   unmatched finding is then classified by its E.4 step-6 **reason**:

   | unmatched reason | explicit verdict present? | classification |
   |---|---|---|
   | any | yes | `ADJUDICATED_TRUE_POSITIVE` / `ADJUDICATED_FALSE_POSITIVE` — **an explicit per-item verdict always wins over the attestation** |
   | `no_key_match` | no | **`ATTESTED_FALSE_POSITIVE`** — the matcher compared this finding against the whole key and it satisfied no entry; under the attestation that is a positive statement that it names no true defect |
   | `unresolvable_location` | no | `UNADJUDICATED` → **precision and FP-rate are REFUSED** |
   | `ambiguous_match` | no | `UNADJUDICATED` → **precision and FP-rate are REFUSED** |

   The last two rows are the honest half of the rule and are **not** negotiable down to "attest and
   move on". `unresolvable_location` and `ambiguous_match` mean the matcher could not *finish*
   evaluating the finding against the key — in the ambiguous case the finding actually satisfied
   two entries' criteria — so the attestation, which speaks only about the key's coverage of true
   defects, says nothing about them. Auto-FP-ing a finding the matcher never managed to test would
   manufacture precision out of a matcher limitation. The refusal reason is
   `closed_world_incomplete_match_evaluation: <n> unmatched findings have reason
   unresolvable_location|ambiguous_match and carry no explicit verdict`, and the fix available to
   the adjudicator is to supply verdicts for exactly those ids.

   **Path B — open world (`closed_world == false`).** `precision_status: COMPUTED` requires every
   id in `unmatched_findings` to carry a verdict. Otherwise REFUSED with
   `adjudication_incomplete: …`.

   **Both paths, when COMPUTED:**

   ```text
   precision           = (len(matched_findings) + adjudicated_true_positives) / findings_total
   false_positive_rate = (adjudicated_false_positives + attested_false_positives) / findings_total
   ```

   `attested_false_positives` is `0` on path B by construction, so the second formula reduces to
   the original open-world one and path B's numbers are unchanged by this revision. A matched
   finding counts as a true positive **by construction of the key** (it identified a seeded
   defect); this is stated in the output's own `definition` fields so no reader has to infer it.

   **Three invariants that make the two metrics consistent with each other**, each asserted by
   T-4 rather than left to inspection:

   * `precision_status == "COMPUTED"` **iff** `false_positive_rate_status == "COMPUTED"`. The two
     are gated by one decision and can never disagree.
   * `precision_status == "COMPUTED"` **implies** `unadjudicated_count == 0`. Every finding is
     accounted for by exactly one of: matched, adjudicated TP, adjudicated FP, attested FP.
     Therefore `len(matched_findings) + adjudicated_true_positives + adjudicated_false_positives
     + attested_false_positives == findings_total`, and **`precision + false_positive_rate == 1`
     exactly**. The R3 failure mode — a numerator that penalises unmatched findings in `precision`
     while `false_positive_rate` ignores them and reports a false `0` — is arithmetically
     impossible once this holds, in either direction.
   * `findings_total == 0` → both metrics are `null` / `REFUSED` with reason `no_findings`, on
     **both** paths. A zero denominator is refused, never divided by and never reported as a
     perfect score.

   **2a. `adjudication_status`, stated so it can never read as `partial` while precision is
   computed.** The enum gains a fourth value, and the mapping is total:

   | condition | `adjudication_status` |
   |---|---|
   | no adjudication file, or a file with zero verdicts and `closed_world == false` | `none` |
   | some but not all unmatched findings classified, i.e. `unadjudicated_count > 0` | `partial` |
   | `unadjudicated_count == 0` and `attested_false_positives == 0` (every classification came from an explicit per-item verdict) | `complete` |
   | `unadjudicated_count == 0` and `attested_false_positives > 0` (at least one classification came from the attestation) | `complete_by_attestation` |

   `complete_by_attestation` exists so the *provenance* of the completeness stays visible: a reader
   can tell at a glance that some false positives were derived from a signed scope claim rather
   than judged one by one, which is a materially weaker warrant and must not be laundered into
   plain `complete`. Adding the value is an additive MINOR bump to the metrics schema.
3. *Recall always computable, denominator explicit.* `seeded_recall` and `miss_rate` carry
   `numerator`, `denominator` and `population: "seeded_defects_only"`, so neither can be read as a
   whole-population metric.
4. *No historical-corpus heuristic.* Enforced by E.3's closed key set (exit 2 on any extra key).
5. *`UNADJUDICATED` counts accompany every metric block.* `unadjudicated_count`,
   `adjudication_status` and `attested_false_positives` are unconditional fields, so a partial
   adjudication can never read as a complete one, and a completeness that rests on an attestation
   can never read as one that rests on per-item verdicts (point 2a).
6. *Verdict reproducibility is observed, never asserted from one run.* With fewer than two
   `--run-verdict` inputs the status is `SINGLE_RUN_NOT_ASSERTED` and `agreement` is `null`. With
   two or more, `agreement = (count of the modal verdict) / run_count`, `run_count` explicit.

#### E.6 Exit codes

```text
0  scored successfully -- INCLUDING a REFUSED precision when --require-precision was not passed
1  input error: a file is missing/unreadable/not JSON, or its schema_version MAJOR is unknown
2  contract violation in the inputs: unknown key in an adjudication verdict object, empty
   rationale, duplicate finding id, duplicate seeded defect id, key/fixture digest mismatch,
   malformed match_criterion, `closed_world == true` without a complete `exhaustive_attestation`,
   or `exhaustive_attestation` present while `closed_world == false` (E.3)
3  precision was REFUSED while --require-precision was passed
4  leak detected (scan-leak), fixture verification failed (verify-fixture), or a non-empty
   --dest (materialize)
```

`verify-fixture` additionally checks, and exits 4 on any failure: every seeded defect's
`location.file` exists in `head/`; its `symbol` is defined within `line_range`; the `base` → `head`
diff actually touches that range (so a key entry cannot describe a defect the diff never
introduced); the `base` tree's own test suite passes; the `head` tree's test suite **also** passes
(that is the point — a green suite is what makes the defects search-resistant); and D.6's leak scan
over `subject/` is clean.

---

### D-F — Export bundle schema and the minimum evidence subset (DEC-6)

#### F.1 The minimum evidence subset, stated as a closed list

Per §4, defined as what effectiveness validation actually needs, and nothing else. Per Final
Review **attempt**:

1. every per-dispatch audit record in that attempt group (`<key>/record.json`);
2. the retained redacted input for each (`<key>/input.md`);
3. the retained redacted report for each (`<key>/report.md`);

plus, once per run:

4. the run's `ORCHESTRATOR_LOG.md`.

`TIMING_LOG.md`, `.timing_state.json`, phase artifacts and `FINAL_RESULT.md` are **not** in the
subset: none of them is required to reconstruct an attempt's input, report, verdict or provenance,
and DEC-6 defines the subset by that need rather than by convenience.

#### F.2 Bundle schema

```jsonc
{
  "schema_version": "1.0",
  "bundle_kind": "final_review_evidence_bundle",
  "run_id": "run_804e35d29531",
  "exported_at": "2026-08-26T10:22:00.000000+00:00",
  "component_versions": {
    "audit_schema": "1.0",
    "redaction_policy": "redaction/1.1",
    "export_schema": "1.0"
  },
  "orchestrator_log": {
    "path": "artifacts/runs/run_804e35d29531/ORCHESTRATOR_LOG.md",
    "digest": "sha256:...",
    "content": "| timestamp | event | ... |\n..."      // verbatim, inlined
  },
  "attempts": [
    {
      "final_review_attempt": 1,
      "accepted_dispatch_key": "attempt1__task_d3f49c042d5a__ctx_...",   // or null
      "violations": [],                                                  // D-B B.3 codes
      "dispatches": [
        {
          "dispatch_key": "attempt1__task_2d0a6f4fc5a4__ctx_...",
          "record_status": "ok",                       // ok | unknown_major | malformed | missing
          "record": { /* the full record json, verbatim */ },
          "input":  {"path": "final_review_audit/..../input.md",
                     "digest_recorded": "sha256:...", "digest_recomputed": "sha256:...",
                     "digest_verified": true, "content": "<redacted text>"},
          "report": {"path": "final_review_audit/..../report.md",
                     "digest_recorded": "sha256:...", "digest_recomputed": "sha256:...",
                     "digest_verified": true, "content": "<redacted text>"}
        }
      ]
    }
  ],
  "integrity": {
    "records_found": 3, "records_ok": 3,
    "digest_mismatches": [], "unreadable": [], "missing_artifacts": [],
    "incomplete_publications": [                 // A.3 P6 -- abandoned .staging/ entries with
      {"dispatch_key": "attempt2__task_c__ctx_d",//   no published record. NEVER counted as a
       "staging_dir": "attempt2__task_c__ctx_d.81422-9f3ac10e",  //  record, and never parsed.
       "files_present": ["input.md"], "files_absent": ["report.md", "record.json"]}
    ]
  }
}
```

* **Content is inlined**, not referenced. DEC-6.6 is explicit that durability must not rest on
  Orca; it must equally not rest on the bundle's neighbours still being on disk. A bundle a human
  attaches to a PR is self-contained or it is not evidence.
* Every embedded artifact carries both the digest **recorded** in the record and the digest
  **recomputed** from disk at export time, plus the boolean. A mismatch does not abort the export
  — it is reported in `integrity.digest_mismatches` and the bundle still ships, because a
  tamper/corruption event is exactly the thing an auditor needs to receive rather than be denied.
* Attempts are ordered by `final_review_attempt`; dispatches within an attempt by `dispatch_key`.
  Everything else follows insertion order, so two exports of the same run are byte-identical apart
  from `exported_at`.

#### F.3 Export surface, and the retention/commit policy it implements

```bash
python3 <SKILL_DIR>/tools/run_logging.py final-review-audit-export --run-id <run-id> \
    [--base <dir>] [--out <path>]
```

Default `--out`: `<ARTIFACT_ROOT>FINAL_REVIEW_EVIDENCE_BUNDLE.json`.

* **Opt-in.** No workflow step runs it automatically. Nothing calls it from the settlement path.
* **Overwriting a bundle is permitted**, in deliberate contrast to D-A's record immutability: a
  bundle is fully re-derivable from records that are themselves immutable, so a stale bundle is a
  hazard while a rewritten one is not. This asymmetry is stated in `SKILL.md` §9 so it cannot be
  read as an inconsistency.
* **No `git add`, ever.** No IMPLEMENTATION work item may add an automatic `git add`, commit, or
  push of run artifacts, in this command or anywhere else. T-5 asserts it (grep the workflow paths
  for `git add`).
* **`artifacts/runs/` is not gitignored and OS-22 does not gitignore it** (ANALYSIS A-2: copying
  `.gitignore`'s `artifacts/**/.timing_state.json` line by reflex would be exactly backwards —
  audit artifacts *are* the evidence). Committing stays a deliberate per-run human decision.
* **No deletion, no compaction, no GC, no horizon.** `SKILL.md:1204-1205` defers retention/archive
  to OS-8; OS-22 adds no deletion path for any artifact. A.3 P6's sweep is **not** an exception and
  must not be implemented as one: it removes only `final_review_audit/.staging/` scratch state, only
  when the corresponding `<dispatch_key>/` has already been published (so the bytes survive at the
  final path), and it **retains** any staging entry whose record never published, because that
  entry is the only evidence of the failed attempt. No published record, no log, no bundle and no
  run artifact is ever removed.
* **The export reports incomplete publications rather than hiding them.** `integrity.
  incomplete_publications` (F.2) lists every retained `.staging/` entry with the files it holds.
  An auditor is told which dispatches failed to publish; the bundle never silently omits them and
  never counts them as records.

#### F.4 Three authorities (DEC-5), as the §9 text will state them

1. **`ORCHESTRATOR_LOG.md` — authoritative, append-only, for run lifecycle provenance**, plus the
   reader rule ANALYSIS F5b showed is missing: **`run_end` is not terminal.** A reader reads the
   whole file; the authoritative status is the **last** `run_status` row; rows after a `run_end`
   are valid and mean the run continued; a later `run_end` supersedes an earlier one. Prose
   reader contract only — no writer change, no schema change, no behaviour change.
2. **The per-dispatch audit records — authoritative for attempt content** (input, report,
   findings, verdict, provenance). Where a summary and a record disagree, the record wins and a
   reader must say so rather than reconcile silently.
3. **`FINAL_RESULT.md` (§16) — a summary that references.** Its `## Final Adversarial Review`
   block must, per attempt, cite `task_id`, `dispatch_id`, `provenance_state` and the audit record
   path, and **must not assert a finding-level claim that no retained reviewer artifact supports.**
   The existing four-axis `## Orca Orchestration State` serialization is **not trimmed** (DEC-5).

---

### N-1 — The neutrality anchor (DEC-1, PLAN ordering rule 1)

#### N.1 What the golden holds

`scripts/fixtures/os22_neutrality/pre_os22_task_specs.json`, generated from a
`git archive 1045815` checkout — `1045815` being "Validate Final Adversarial Review effectiveness
(#19)", the last commit before OS-22.

```jsonc
{
  "captured_from_commit": "1045815",
  "captured_by": "scripts/test_e2e_harness.py::capture_neutrality_task_specs",
  "canonicalization": "task_spec/1.0",   // N.1.1 -- the ONLY transform applied, versioned
  "workflow_specs": {          // family A: specs a real workflow actually dispatches
    "orca-worker-reviewer-orchestration": {
      "single_canonical|profile=none":     ["<verbatim spec text>", "..."],
      "single_canonical|profile=multi":    ["..."],
      "multi_canonical|profile=none":      ["..."],
      "multi_canonical|profile=multi":     ["..."],
      "specialized_bugfix|profile=none":   ["..."],
      "specialized_bugfix|profile=multi":  ["..."]
    },
    "orca-worker-reviewer-loop": { /* same six keys */ }
  },
  "direct_specs": {            // family B: a direct render_task_spec() matrix
    "final_reviewer|final_review|iter1|routing=none":  "<verbatim spec text>",
    "final_reviewer|final_review|iter2|routing=none":  "...",
    "final_reviewer|final_review|iter1|routing=multi": "...",
    "worker|design|iter1|no_optional_blocks":          "...",
    "reviewer|design|iter1|reviewer+quality":          "...",
    "reviewer|design|iter2|reviewer+quality+risk":     "...",
    "worker|implementation|iter1|all_four_blocks":     "...",
    "worker|bugfix|iter1|all_four_blocks":             "..."
    /* the full matrix is enumerated by NEUTRALITY_DIRECT_CASES, below */
  }
}
```

**Why two families.** Family A is the existing recording-wrapper technique
(`capture_legacy_artifacts`'s `e2e_module.render_task_spec = recording`, `:1126-1133`) and proves
neutrality for the specs a workflow really dispatches. It must include `profile=multi` variants,
because `scripts/e2e_harness.py:1295` renders a `final_review` spec **only** when
`final_review_routing_context()` is not `None` — i.e. only under a selected Agent Profile. Family B
calls `render_task_spec()` directly over an enumerated matrix of `(role, phase, iteration,
optional-block combination)`, including `final_reviewer`/`final_review` at attempts 1 and 2 with
and without routing. Family B is the stronger anchor: it does not depend on which specs a workflow
*happens* to dispatch, so a future harness change cannot silently shrink the neutrality coverage.

#### N.1.1 `canonicalize_task_spec()` — the capture contract, and why it is not `_normalize_artifact()`

**`_normalize_artifact()` (`scripts/test_e2e_harness.py:1087-1101`) must not be used on Task
specs.** Read it: after the workspace substitution it calls `splitlines()`, then `line.split()`,
then `" ".join(tokens)`, then `"\n".join(lines)`. It therefore silently equates any two specs that
differ only in indentation, in repeated interior spaces, in trailing spaces, or in the presence of
a final newline — and it drops a terminal newline outright. A golden built on it is a
whitespace-insensitive comparison, not the character-for-character identity DEC-1 requires.

This is not hypothetical for *these* specs. Running today's `capture_legacy_artifacts()` with the
normalizer replaced by the identity function shows every dispatched spec losing bytes to
`_normalize_artifact()` — 1 byte for each worker spec, 8 for each reviewer spec, across all twelve
skill×workflow fixtures. The lines responsible are real reviewer-visible content:
`relevant_previous_findings: ` and `approved_baseline: ` and `new_claims: ` carry a **trailing
space** when their value is empty (`render_task_spec` emits `f"{key}: {value}"` unconditionally,
`task_context.py:609`), and `current_delta:` embeds the worker's report text with its own interior
double spaces. A byte-strict golden must hold those bytes; `_normalize_artifact()` throws them away.

The neutrality capture therefore uses its own transform, in the same module, applied **only** to
Task specs:

```python
NEUTRALITY_CANONICALIZATION = "task_spec/1.0"

# Enumerated, closed, and each entry justified below. Nothing else is substituted.
_TASK_SPEC_SUBSTITUTIONS = (
    ("workspace_path", lambda ws: str(ws), "<WORKSPACE>"),
)

# Fail-closed residue check: if any of these survives canonicalization, the enumeration
# above is INCOMPLETE and the capture must be fixed -- never silently normalized away.
_TASK_SPEC_NONDETERMINISM_TRIPWIRES = (
    re.compile(re.escape(tempfile.gettempdir())),
    re.compile(r"/var/folders/"),
    re.compile(r"/private/(?:tmp|var)/"),
    re.compile(re.escape(str(Path(__file__).resolve().parents[1]))),
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"),   # ISO-8601 timestamp
    re.compile(r"\b(?:task|ctx|dcap|term|run)_[0-9a-f]{8,}"),      # orca-assigned ids
)


def canonicalize_task_spec(spec: str, *, workspace: Path | None) -> str:
    """The ONLY transform between render_task_spec() and the golden comparison.

    One string replacement, nothing else. No splitlines(), no split(), no join(),
    no strip(), no rstrip(), no reserialization -- every space, every run of
    spaces, every trailing space and the presence or absence of a terminal
    newline reaches the comparison exactly as render_task_spec() produced it.
    """
    out = spec
    if workspace is not None:
        for _name, source, replacement in _TASK_SPEC_SUBSTITUTIONS:
            out = out.replace(source(workspace), replacement)
    for tripwire in _TASK_SPEC_NONDETERMINISM_TRIPWIRES:
        match = tripwire.search(out)
        if match is not None:
            raise AssertionError(
                f"unenumerated nondeterministic value in a captured Task spec: "
                f"{match.group(0)!r}; extend _TASK_SPEC_SUBSTITUTIONS deliberately, "
                f"never loosen the comparison"
            )
    return out
```

**The single enumerated substitution, and why the list is exactly one entry long.** Family A runs
the workflow inside a `tempfile.TemporaryDirectory()`, and exactly one reviewer-visible field
carries that absolute path: `drill_down=(str(self.workspace),)` (`scripts/e2e_harness.py:1116`),
which lands on the reviewer spec's `drill_down:` line. Verified empirically against the current
tree — the workspace string occurs **0** times in a worker spec and **1** time in a reviewer spec.
Nothing else varies: `run_id` is the literal `"run_golden"` fixed by the capture, and every path
`phase_artifact_contract()` builds is repo-relative (`artifacts/runs/run_golden/DESIGN.md`), never
absolute. There is **no timestamp to strip**: `scripts/task_context.py` never imports `datetime` or
`time`, `render_task_spec()` reads no clock, and a scan of all twelve captured specs for
`_normalize_artifact()`'s own `<TS>` token shape (`count("-") >= 2 and count(":") >= 2`) finds zero
hits. The `<TS>` half of `_normalize_artifact()` is therefore not merely lossy for Task specs, it
is dead — which is why the transform above does not carry a timestamp rule at all, and why the
tripwire list carries an ISO-8601 pattern instead: if a timestamp ever *does* appear, the capture
fails loudly rather than quietly erasing it.

Family B calls `render_task_spec()` directly with test-owned literals and no workspace, so it
passes `workspace=None` and the transform is the **identity function** — raw bytes, start to
finish.

**Storage and comparison, stated as bytes.** The golden is written with
`json.dump(..., ensure_ascii=False, indent=2, sort_keys=True)`; JSON string encoding is lossless
over trailing spaces and over the absence of a terminal newline, so the round trip
`canonicalize → json → json → compare` preserves every byte. `render_task_spec()` produces **no
terminal newline** (its last line is `=== END TASK SPEC ===`, joined with `"\n"`), and that fact
is itself part of the contract: the test asserts it directly, so a future `+ "\n"` fails T-6.
The comparison is performed on encoded bytes rather than on `str`:

```python
self.assertEqual(current.encode("utf-8"), stored.encode("utf-8"))
```

**Scope separation, so the two normalizations cannot drift into each other.**
`canonicalize_task_spec()` is used **only** for `pre_os22_task_specs.json`.
`_normalize_artifact()` keeps its existing job unchanged — `ORCHESTRATOR_LOG.md` and the final
report inside `capture_legacy_artifacts()`, where real per-run timestamps and orca-assigned ids do
appear and where OS-4's `LegacyByteIdentityTests` depends on today's exact behaviour. Neither
function is modified, neither calls the other, and N.2 item 4 already forbids touching the OS-4
capture path. The neutrality golden's proof is thereby independent of log/artifact normalization
entirely.

`NEUTRALITY_DIRECT_CASES` is a module-level tuple in `scripts/test_e2e_harness.py` enumerating,
for each of `worker`/`reviewer`/`final_reviewer` × each `WORKFLOW_PHASES` value the role is legal
for × iterations `{1, 2}` × the five block combinations `{none, reviewer, reviewer+quality,
reviewer+quality+risk, all four}`, exactly the boundary/reviewer-context payloads
`build_reviewer_context`/`build_quality_gate_context`/`build_risk_context`/
`build_agent_routing_context` already build in the existing tests. Every case is deterministic and
timestamp-free.

#### N.2 What the test asserts, byte-for-byte

`FinalReviewObservabilityNeutralityTests` in `scripts/test_e2e_harness.py`:

1. **Byte identity.** For every key in `workflow_specs` and `direct_specs`, the current tree's
   captured spec is passed through `canonicalize_task_spec()` (N.1.1) — one enumerated
   substitution for family A, the identity function for family B — and the result is compared to
   the stored string **as UTF-8 bytes**: `assertEqual(current.encode("utf-8"),
   stored.encode("utf-8"))`. Indentation, repeated interior spaces, trailing spaces and the
   absence of a terminal newline all participate in the comparison. `_normalize_artifact()` is
   **not** used here and must not be: it tokenizes and reserializes every line and would make all
   four of those differences compare equal (N.1.1). This is a byte-identity claim, not a "semantic
   content" claim, so it needs no definition of "semantic".

   1a. **Mutation test — the golden is proven byte-strict, not assumed to be.**
   `test_a_whitespace_only_change_fails_the_neutrality_golden` takes each of one worker spec, one
   reviewer spec and one `final_reviewer` spec from the stored golden and applies, one at a time,
   four whitespace-only mutations: (i) strip the trailing space from a `<key>: ` line with an
   empty value; (ii) add a trailing space to a line that has none; (iii) collapse one run of two
   interior spaces to one; (iv) append a terminal `"\n"`. Each mutant is fed through the same
   comparison helper the real assertion uses, and the test asserts each one **fails**
   (`assertRaises(self.failureException)`). The same test additionally asserts that
   `_normalize_artifact(mutant, ws) == _normalize_artifact(original, ws)` for mutations (i), (ii)
   and (iii) — i.e. it demonstrates in the test body that the old normalizer would have accepted
   exactly the changes the new comparison rejects. Without 1a, "byte-strict" is a claim about the
   test rather than a property of it.

   1b. **No terminal newline.** The test asserts every captured spec satisfies
   `not spec.endswith("\n")`, pinning `render_task_spec()`'s current terminator so that adding one
   is a T-6 failure rather than an invisible change.
2. **Signature stability.** `inspect.signature(task_context.render_task_spec)` has exactly the
   parameter names, order and defaults it had at `1045815`, asserted against a literal tuple in
   the test. A new parameter — even an unused, defaulted one — fails.
3. **Unreachability tripwire.** With `run_logging.redact_text`,
   `run_logging.capture_stored_task_spec` and `run_logging.write_final_review_audit_record`
   patched to `side_effect=AssertionError`, a full workflow runs through spec assembly and the
   dispatch call. Any call from that edge fails the test. This is DEC-4's ordering invariant
   enforced structurally rather than by review.
4. **OS-4 evidence untouched.** `LegacyByteIdentityTests` and
   `scripts/fixtures/legacy_baseline/pre_os4_artifacts.json` are asserted unmodified — a separate
   capture function and a separate fixture file, exactly as DEC-1 requires, because extending
   `capture_legacy_artifacts()` or `pre_os4_artifacts.json` in place would change the input to
   `LegacyByteIdentityTests` and destroy the OS-4 evidence it exists to hold.

**Regeneration rule, stated in the fixture's own README** (mirroring
`scripts/fixtures/legacy_baseline/README.md`'s wording): if this test fails, the current code
changed reviewer-visible bytes and **the code is what needs fixing**. Regenerating the fixture to
make it pass destroys the only evidence for §2. The one legitimate regeneration is DEC-10(ii)'s
reversal protocol: if a later phase overturns the deferred suffix fix, that fix lands as its own
commit and the golden is regenerated at that commit with the delta documented as a §9-conformance
correction, explicitly not as an observability change.

---

## Error Handling / Compatibility

### Failure posture, by surface

| surface | failure | behaviour | rationale |
|---|---|---|---|
| audit write | disk full, unwritable path, `OSError` | wrapped in the existing `_safe_log()` (`orca_runtime_harness.py:2076`); the error lands in `self._logging_errors`; **settled lifecycle state is never mutated** | `SKILL.md:1199-1201`; PLAN P-6 |
| audit write | the record for this dispatch key is **already published** | the publishing `os.rename()` fails; the new staging directory is removed; `FinalReviewAuditCollision` → one `final_review_audit_collision` row; the published record is untouched; run continues | D-A A.3 P5; a retry must never clobber the record of the dispatch it replaced |
| audit write | `OSError` at any staging boundary (`mkdir`, any of the three writes, any fsync, the rename) | **nothing is published** — a published `<key>/` only ever appears via A.3 P3's rename; the staging dir is `rmtree`d; `FinalReviewAuditWriteFailed` → one `final_review_audit_write_failed` row; **the same dispatch key can still publish on a later attempt** | D-A A.3 P4/P5; the old precheck-then-sequential-create protocol could orphan a dispatch permanently, this one cannot |
| audit write | a process died mid-write, leaving `.staging/<key>.<nonce>/` behind | readers ignore `.staging/` entirely (A.3 P4 reader rule); the entry is retained as partial evidence, reported once per run as `final_review_audit_incomplete_publication`, and surfaced in the bundle's `integrity.incomplete_publications` | D-A A.3 P6; an explicit recoverable incomplete state, not a silent permanent refusal |
| spec capture | `orca` not on `PATH`, non-zero exit, timeout, unparseable JSON, task id absent from the result | `stored_task_spec.capture_status = "unavailable"` with a non-empty `capture_error`; the record is **still written**, with the input artifact absent and its digest fields `null`; one `final_review_audit_incomplete` row | a record that says "the input could not be captured, here is why" is evidence; a missing record is not |
| delivery-evidence capture | same | `delivery_evidence.capture_status = "unavailable"` + `capture_error`; record still written | as above |
| report snapshot | file absent at the resolved path | `report.capture_status = "absent"`; provenance ladder row 4 → `voided`/`report_missing` | ANALYSIS F2a is exactly this case |
| report snapshot | file present but undecodable | `capture_status = "unreadable"` + `capture_error`; treated as row 4 | never guess |
| report parse | present but fails the §11/§17 shape | `parsed.parse_status = "malformed"` + `parse_error`; ladder row 5 → `voided`/`report_malformed`; **the raw report is still snapshotted** | the malformed bytes are the evidence |
| redaction | unknown `policy_version` (now including the superseded `"redaction/1.0"`) | `RunLoggingError` before anything is written | a digest under an unknown policy is not comparable to anything |
| record assembly | a path-bearing field fails C.7's **P-PATH** postcondition — the value is in none of the four allowed output categories P1–P4 | `RunLoggingError` before staging; **nothing is written or published** | R1: the leak this closes reached disk *because* the field had no postcondition at all. D3-001: a postcondition phrased as "the policy leaves it unchanged" would have passed a raw one-segment `/luminous`, so the check asks which allowed category the value is in and has no "unrecognised" answer |
| record assembly | the repository root cannot be resolved, so C.7 rung 2 is unavailable | rung 2 is skipped and rung 3 applies — the whole value becomes `<REDACTED:foreign_absolute_path>` | fail closed: an unresolvable root costs readability, never safety |
| reader | missing / malformed / unknown-MAJOR record | provenance reads `unknown`; never `accepted` | D-A A.5, DEC-2 |
| reader | two `accepted` in one attempt | `accepted_dispatch_key: null` + `multiple_accepted_dispatches` violation | the reader reports a contract violation, it does not resolve one |
| scorer | any input error / contract violation | exit 1 / 2 with a message on stderr; **no partial metric file is written** | a half-written metric block is worse than none |
| scorer | precision refused | exit 0 with `precision_status: REFUSED`, or exit 3 when `--require-precision` | DEC-8 rule 2 |
| scorer | `closed_world` and `exhaustive_attestation` disagree in either direction | exit **2**; no metrics file written | E.3: an unsigned closed world and an unread attestation are both half-states the precision gate would otherwise have to guess about |
| scorer | closed world, but an unmatched finding has reason `unresolvable_location` / `ambiguous_match` and no verdict | exit 0 with both metrics `REFUSED`, reason `closed_world_incomplete_match_evaluation` (exit 3 under `--require-precision`) | E.5 point 2: the attestation covers the key's completeness, not a match the matcher could not finish evaluating |
| scorer | an internal violation of E.5's three consistency invariants | `RuntimeError`, no metrics file | R3 was exactly a silent inconsistency between two metrics; it must abort, not serialize |
| materialize | non-empty `--dest`, leak hit, digest mismatch | exit 4 / 4 / 2; nothing left behind in `--dest` on failure | fail closed before a reviewer can be pointed at it |

**Capture invocation, concretely.** `run_logging.py` cannot import `scripts/`
(`run_logging.py:17-27`), so it duplicates the CLI-command resolution the same way it already
duplicates `_ensure_run_artifact_root()`: `os.environ.get("ORCA_CLI_COMMAND", "orca")` — the same
env var `orca_runtime_harness.py:732` reads — and calls
`subprocess.run([...], capture_output=True, text=True, timeout=CAPTURE_TIMEOUT_SECONDS,
check=False)` with `CAPTURE_TIMEOUT_SECONDS = 30`. `FileNotFoundError`, a non-zero return code, a
`TimeoutExpired` and a `json.JSONDecodeError` all funnel into `capture_status: "unavailable"` with
the reason in `capture_error`. Captured text arrives on the stdout **pipe** — never via a file — so
D-C C.6's in-memory-only guarantee holds through the capture layer too.

### Compatibility — §8's preserved invariants, each with why it is untouched

| invariant | status | why |
|---|---|---|
| fresh Final Reviewer session/terminal per attempt | untouched | nothing in this design creates, reuses or closes a terminal |
| Worker / Reviewer separation | untouched | no new role; the Coordinator writes the records, the Reviewer never learns the audit path exists |
| phase lifecycle | untouched | no new phase, no new transition, no counter change |
| Risk semantics | untouched | no risk field is read or written by any new code |
| Quality Profile semantics | untouched | no quality-gate block is read or written |
| Agent Profile immutable routing | untouched | routing is *recorded* in the record (`reviewer_agent_command`, `reviewer_agent_origin`) and never consulted for a decision |
| correction / downstream revalidation semantics | untouched | `round_kind` values are unchanged; correction rounds are not audited by this design |
| Responsible Phase correction semantics | untouched | the §17 Finding Contract is unchanged |
| `RESULT:` two-valued, `REVIEW_VERDICT:` four-valued | untouched | both are *copied verbatim* into `report.parsed`, never re-derived or collapsed |

### Additive-ness, and the two places it is not purely additive

**Purely additive:** a new directory under `<ARTIFACT_ROOT>`; three new `--event` values in an
open vocabulary; new functions and new CLI subcommands in `run_logging.py`; a new
`scripts/final_review_eval.py`; new fixtures; a new `SKILL.md` §9 subsection. No existing column,
no existing path, no existing schema, no existing function signature changes.

**Not purely additive, both deliberate and both scoped by PLAN:**

1. **`SKILL.md` §16 step 8's path text** changes from `artifacts/FINAL_REVIEW_*` to
   `<ARTIFACT_ROOT>FINAL_REVIEW*` (DEC-10 i). It is a correction of a stale path that contradicts
   §9's ladder, not a semantic change, and it is byte-neutral with respect to N-1: §16 is
   Coordinator-side final verification and its text is never rendered into a Task spec — which N-1
   *proves* rather than assumes.
2. **`FINAL_REVIEW_CONTRACT` gains two keys** and `FINAL_REVIEW_CONTRACT_MAX_LINES` goes 15 → 17.
   The dict is compared for **exact equality** against the block parsed from §17
   (`validate_skills.py:1285`) and the block is length-capped (`:1291`), so the SKILL block, the
   validator dict and the cap must move in **one commit** — PLAN ordering constraint 2.

```text
FINAL_REVIEW_AUDIT_RECORD = artifact_root_final_review_audit_per_dispatch
FINAL_REVIEW_PROVENANCE_DEFAULT = unknown
```

Spellings chosen to match the existing block's style (lowercase snake, no paths, no punctuation)
so the block stays parseable by `FINAL_REVIEW_CONTRACT_BLOCK_PATTERN` (`validate_skills.py:243`)
and readable as a policy statement rather than a path.

3. **The redaction policy ships as `redaction/1.1`, not `redaction/1.0`** (C.2, C.3 category 5).
   Nothing outside OS-22 reads the policy, and OS-22 has not been released, so no external consumer
   is affected; the version is bumped anyway because the substituted output for a given input
   changes, and a version whose meaning drifts is worse than no version. The record schema stays
   `1.0` — `redaction_policy_version` is a value the record carries, not a shape it has (A.5).
   The metrics schema of D-E takes a **MINOR** bump for `attested_false_positives` and the fourth
   `adjudication_status` value (E.5 point 2a): both are additive, and no existing metrics key
   changes meaning on the open-world path, whose numbers are byte-identical before and after.

### Migration judgement (§8 requires this to be explicit)

**No migration is performed and none is needed.** No existing artifact changes meaning, so no
existing consumer can misread one. Runs that completed before OS-22 simply have no
`final_review_audit/` directory; every reader in this design treats an absent record as
`unknown` (D-A A.5), which is the correct reading for a run that never wrote one. Existing
`artifacts/runs/*/` trees are not touched, not migrated, not backfilled and not deleted —
consistent with §9's existing rule that a new run never adopts another run's artifacts and with
DEC-6.5's no-deletion policy. Backfilling historical runs from Orca state would be *possible*
(ANALYSIS F1(a) verified `task-list` works for historical runs) and is deliberately **not** done:
a backfilled record would carry a `recorded_at` that is not the settlement time and a report
snapshot taken long after any overwrite, which is precisely the "self-referential stale
provenance" §4 forbids.

**C.7's baseline regeneration is not an exception to this, and the distinction is load-bearing.**
`artifacts/runs/run_ff587481a820/final_review_audit/` is not a historical third-party run — it is
**OS-22's own §7 baseline evidence, produced by this work item while it was being built**, and it
is regenerated by re-running this work item's pipeline against the same source, under the corrected
policy, before the work item is finished. That is republishing a deliverable that is not yet
shipped, not migrating someone else's settled artifact. The rules that make backfilling wrong do
not apply: nothing is reconstructed from Orca state, `recorded_at` is the time of the real
re-capture, and no run that OS-22 did not itself produce is touched. Any run outside this work
item's own baseline keeps the "not touched, not migrated, not backfilled, not deleted" treatment
above, `redaction/1.0` stamp included.

### Volume

One published directory per dispatch, holding a record and two text artifacts. Observed spec sizes
are 2–15 KB (ANALYSIS F1(a)); reports are single-digit KB. A three-dispatch attempt costs well
under 60 KB. §17 passes the diff by path reference rather than inline, so this does not grow with
repository size. A.3's staging directories add no steady-state cost — each is renamed away on
success and `rmtree`d on a handled failure; only a hard process kill leaves one behind, it is
bounded by the same per-dispatch size, and A.3 P6 sweeps it as soon as that key publishes.

---

## Expected Changed Files / Implementation Steps

Ordering below **is** the implementation order. Constraints marked **HARD** fail the tree or
destroy evidence if violated.

### Step 0 — I-0, before any product change (**HARD**)

| file | change |
|---|---|
| `scripts/test_e2e_harness.py` | add `NEUTRALITY_WORKFLOWS`, `NEUTRALITY_DIRECT_CASES`, `NEUTRALITY_CANONICALIZATION`, `_TASK_SPEC_NONDETERMINISM_TRIPWIRES`, `canonicalize_task_spec()` and `capture_neutrality_task_specs()` — **new** functions, not an extension of `capture_legacy_artifacts()` and **never calling `_normalize_artifact()` on a Task spec** (**HARD**, N.1.1); `_normalize_artifact()` itself is left byte-for-byte unmodified so OS-4's `LegacyByteIdentityTests` keeps its exact input |
| `scripts/fixtures/os22_neutrality/pre_os22_task_specs.json` | **new**, generated by running that function inside a `git archive 1045815` checkout |
| `scripts/fixtures/os22_neutrality/README.md` | **new**: what it holds, how it was generated, and the regeneration rule (N.2) |
| `.gitattributes` | **new**, one commented rule: `artifacts/runs/*/final_review_audit/**/report.md -whitespace` (A.6). Created first so every later step's `git diff --check` run is already clean, and so the fix can never be mistaken for a retroactive patch over a failing gate |

A golden generated after implementation proves nothing. Verify by diffing the fixture's own
`captured_from_commit` against `git rev-parse --short 1045815` in the test.

### Step 1 — `run_logging.py` (I-1 → I-5)

Every edit is copied to `orca-worker-reviewer-orchestration/tools/run_logging.py` **in the same
commit** (**HARD**, `validate_skills.py:1944`). Standard library only; **zero `scripts/` imports**
(**HARD**, `run_logging.py:17-27`).

| id | addition |
|---|---|
| I-1 | `FINAL_REVIEW_AUDIT_SCHEMA_VERSION`, `FINAL_REVIEW_AUDIT_DIRNAME = "final_review_audit"`, `PROVENANCE_STATES`, `VOID_REASONS`, `SETTLEMENT_STATES`, `FinalReviewAuditError`/`FinalReviewAuditCollision`/`FinalReviewAuditWriteFailed`, `FINAL_REVIEW_AUDIT_STAGING_DIRNAME = ".staging"`, `_stage_and_publish_audit_record()` (A.3 P1–P3), `sweep_final_review_audit_staging()` (A.3 P6), `sha256_text()`, `sha256_bytes()`, `final_review_audit_dir()`, `final_review_dispatch_key()`, `write_final_review_audit_record()`, `read_final_review_audit_record()`, `read_final_review_attempt_provenance()` |
| I-2 | `FINAL_REVIEW_REDACTION_POLICY_VERSION = "redaction/1.1"`, `_PATH_SEGMENT`, `_HOME_ABSOLUTE_PATH`, `_FOREIGN_ABSOLUTE_PATH` (C.3.1 verbatim), `REDACTION_CATEGORIES` (the ordered **5**-tuple of `(name, compiled_pattern, replacement)`), `redact_text()`; and C.7's `FOREIGN_PATH_PLACEHOLDER`, `_SAFE_RELATIVE_PATH`, `_NON_FILE_URL`, the total `normalize_retained_path_field()` / `assert_retained_path_field()` pair, and the `_relative_artifact_path()` ladder (`<ARTIFACT_ROOT>`-relative → `<REPO>/…` → the placeholder, whole value, **without** calling `redact_text()`), with the P-PATH postcondition applied to every field in C.7's closed table |
| I-3 | `capture_stored_task_spec()`, `capture_delivery_evidence()`, `CAPTURE_TIMEOUT_SECONDS`, `_orca_command()` |
| I-4 | the three new `--event` spellings used at the audit points (no column added, **HARD**) |
| I-5 | `FINAL_REVIEW_EXPORT_SCHEMA_VERSION`, `export_final_review_evidence()`, and the three CLI subcommands `final-review-audit-write`, `final-review-audit-provenance`, `final-review-audit-export` |

CLI surface (matching the existing `_add_common_arguments` style):

```text
final-review-audit-write --run-id <id> --attempt <n> --task-id <id> [--dispatch-id <id>]
    [--base <dir>] [--provenance accepted|voided|unknown] [--void-reason <one of six>]
    [--settlement settled|not_settled|unknown] [--report-path <path>] [--terminal <handle>]
    [--agent-command <cmd>] [--agent-origin <origin>] [--failure-detail <text>]
    [--observed-input-bytes <n>] [--no-capture] [--notes <text>]
final-review-audit-provenance --run-id <id> --attempt <n> [--base <dir>]
final-review-audit-export --run-id <id> [--base <dir>] [--out <path>]
```

`--provenance` defaults to `unknown` (**HARD**: no default anywhere may be `accepted`).

**Baseline regeneration (C.7, R1).** In the **same commit** that lands category 5, the retained
baseline audit artifacts under `artifacts/runs/run_ff587481a820/final_review_audit/` and any
exported bundle derived from them are regenerated by re-running the pipeline under
`redaction/1.1` — never hand-edited, and never left in place carrying a `redaction/1.0` stamp over
an unredacted absolute path. The commit is verified by grepping the whole of `artifacts/` for the
local username, the `-Users-` encoded-workspace shape and `/private/tmp/`: zero hits is the
acceptance condition.

### Step 2 — SKILL.md + validator, **one commit** (I-6, I-7, I-8, I-9) (**HARD**)

| file | change |
|---|---|
| `orca-worker-reviewer-orchestration/SKILL.md` §9 | **new** `#### Final Review audit artifacts (OS-22)` subsection after the OS-17 run-logging subsection: the `final_review_audit/` path rule **including A.3's reader rule (a published `<dispatch_key>/` directory is a complete record; `.staging/` is never a record)**, the D-A schema-version + reader compatibility rule, D-B's provenance enum, D-C's secret-safe requirement **stated as C.7's P-PATH property — no retained field carries a raw absolute path, whichever code path wrote it — together with the `redaction/1.1` policy version**, F.4's three authorities including the `run_end`-is-not-terminal reader rule, F.3's retention/commit policy, and the three CLI call points. Orchestration skill only — the loop skill has no run-scoped log. Use `/Users/<name>/…` in any path example (`validate_skills.py:67`). |
| `SKILL.md` §17 | the input and report paragraphs gain the audit obligation; `#### Final review contract` gains the two keys above |
| `SKILL.md` §16 | step 8: `artifacts/FINAL_REVIEW_*` → `<ARTIFACT_ROOT>FINAL_REVIEW*`; `## Final Adversarial Review` gains the per-attempt `task_id` / `dispatch_id` / `provenance_state` / audit-record-path reference requirement and the no-unsupported-claim rule. **The four-axis `## Orca Orchestration State` ledger is not trimmed** (DEC-5). |
| `scripts/validate_skills.py` | `FINAL_REVIEW_CONTRACT` += 2 keys; `FINAL_REVIEW_CONTRACT_MAX_LINES` 15 → 17; **new** `validate_final_review_audit_contract()` asserting (a) the §9 subsection exists, (b) it states the literal `FINAL_REVIEW_AUDIT_SCHEMA_VERSION` value, (c) it names `final_review_audit/` and states the `.staging/`-is-never-a-record reader rule, (d) it carries the three-authority statement and the `run_end`-is-not-terminal rule, (e) §16 step 8 names `<ARTIFACT_ROOT>` and no longer names `artifacts/FINAL_REVIEW_`, **(f) it states the literal `FINAL_REVIEW_REDACTION_POLICY_VERSION` value and C.7's P-PATH property (no retained field carries a raw absolute path)**; registered in the validator's run list |

Run `python3 scripts/validate_skills.py` immediately after this commit (PLAN P-1).

### Step 3 — emission points (I-10)

| file | change |
|---|---|
| `scripts/orca_runtime_harness.py` | in the final-review settlement path (`:2065-2245` region, the `_log_attempt` call site for `round_kind="final_review"`): snapshot → capture → redact → write, all through `_safe_log` (**HARD**: audit-write failure never mutates settled lifecycle state), placed **after** four-axis finalization and **before** the verdict branch |
| `scripts/e2e_harness.py` | the same at `:1486-1500`, so the deterministic harness exercises the same path |

### Step 4 — fixture, in **two separate commits** (I-11) (**HARD**, DEC-7.4)

| commit | files |
|---|---|
| 1 — message names only the fixture | `scripts/fixtures/final_review_eval/README.md`, `subject/base/**` (8 files), `subject/head/**` (8 files) |
| 2 — later | `scripts/fixtures/final_review_eval/key/answer_key.json`, `adjudications/README.md` |

### Step 5 — scorer (I-12), after the fixture exists (**HARD**, PLAN ordering 5)

`scripts/final_review_eval.py` — new, stdlib only, five subcommands per D-E. E.5 point 2's two
computation paths are implemented as **one** classification function over `unmatched_findings`
whose only inputs are `closed_world`, the per-finding unmatched `reason` and the verdict map, so
the open-world and closed-world numerators cannot drift apart the way R3 found them; the three
invariants at the end of E.5 point 2 are asserted in the scorer itself (a `RuntimeError` on
violation, caught by T-4), not only in the tests.

### Step 6 — tests (T-1 … T-6)

| file | change |
|---|---|
| `scripts/test_run_logging.py` | T-1, T-2, T-3 cases, **including the A.3 write-boundary fault-injection suite** (staging `mkdir`, each `open("x")`, each `write`, each `fsync`, the publishing `rename`), the abandoned-`.staging/` cases, and **T-3's category-5 / idempotence / P-PATH suites (R1)** |
| `scripts/test_e2e_harness.py` | `FinalReviewObservabilityNeutralityTests` (T-6), including the N.2 1a whitespace-mutation test that proves the golden is byte-strict |
| `scripts/test_final_review_eval.py` | **new**: T-4 cases, including the unqualified B5 rerun assertion, the patched-clock no-timestamp-in-metrics test, and **the exact-value closed-world precision/false-positive-rate suite (R3)** |

### Step 7 — docs and PR (I-13, I-14)

`README.md` "Run-Scoped Artifacts and Logs" (`:136-160`) gains the `final_review_audit/` row and
the bundle; `CHANGELOG.md`; `COMPATIBILITY.md`. Draft PR
`Build Final Review observability and evaluation foundation` on
`agent/final-review-observability-evaluation`, referencing Jira OS-22. **No merge. `VERSION` and
`LICENSE-DECISION.md` untouched.**

### Files summary

```text
NEW   .gitattributes                                             (one line, A.6/R5)
NEW   scripts/final_review_eval.py
NEW   scripts/test_final_review_eval.py
NEW   scripts/fixtures/os22_neutrality/{pre_os22_task_specs.json,README.md}
NEW   scripts/fixtures/final_review_eval/README.md
NEW   scripts/fixtures/final_review_eval/subject/{base,head}/**            (16 files)
NEW   scripts/fixtures/final_review_eval/key/answer_key.json
NEW   scripts/fixtures/final_review_eval/adjudications/README.md
EDIT  scripts/run_logging.py                                   (+ byte-parity copy)
EDIT  orca-worker-reviewer-orchestration/tools/run_logging.py
EDIT  orca-worker-reviewer-orchestration/SKILL.md              (sections 9, 16, 17)
EDIT  scripts/validate_skills.py
EDIT  scripts/orca_runtime_harness.py
EDIT  scripts/e2e_harness.py
EDIT  scripts/test_run_logging.py
EDIT  scripts/test_e2e_harness.py
EDIT  README.md, CHANGELOG.md, COMPATIBILITY.md
```

---

## Testing Strategy

Mapped one-to-one onto §9's five groups, plus T-6. Each row names the behaviour, not a count —
§9 explicitly instructs the Reviewer to inspect the fixture and answer key directly rather than
count tests.

### T-1 Audit / provenance — `scripts/test_run_logging.py`

* per-dispatch **input** artifact created at `final_review_audit/<key>/input.md`, with the record's
  `artifact_path` matching and `artifact_digest_post_redaction` re-verifying against the bytes on disk;
* per-dispatch **report** artifact created, same two checks;
* three dispatches in one attempt produce three distinct records; a second write for an
  already-published key raises `FinalReviewAuditCollision`, leaves the published
  `<key>/` directory unchanged byte-for-byte (all three files re-hashed), leaves **no** new
  `.staging/` entry behind, and emits one `final_review_audit_collision` row;
* **fault injection at every write boundary (A.3 P1–P3)** — a parametrized test that injects
  `OSError` at each of: `os.mkdir` of the staging directory; the `open("x")` of `input.md`, of
  `report.md`, and of `record.json`; the `write` of each of those three; each `os.fsync`; and the
  publishing `os.rename`. For **every** injection point it asserts (a) no
  `final_review_audit/<key>/` directory exists — a partial write can never masquerade as a
  published record; (b) `read_final_review_attempt_provenance()` still reports the attempt, and
  never `accepted` for that dispatch; (c) one `final_review_audit_write_failed` row was emitted and
  **settled lifecycle state was not mutated**; and (d) — the D-003 property — a **subsequent**
  `write_final_review_audit_record()` call for the **same** dispatch key **succeeds**, publishing a
  complete `<key>/`, i.e. a mid-write failure never orphans a dispatch permanently. The old
  precheck-then-sequential-create protocol fails (d) at three of those injection points, so this
  test is a real regression guard rather than a formality;
* **abandoned staging state (A.3 P6)** — with a staging directory left behind by a killed writer:
  it is never counted by any reader (`integrity.records_found` unchanged, provenance readers skip
  it, no bundle inlining), it is **retained** while no published record for that key exists and
  reported once as `final_review_audit_incomplete_publication` with its `files_present` list, and
  it is swept once a record for that key *is* published;
* the `accepted` path: `read_final_review_attempt_provenance()` returns that dispatch key;
* **each of the six `void_reason` values** through its own ladder condition (D-B B.2 rows 1–6);
* a voided report is never returned as an accepted verdict — asserted against every public reader
  function, including with an attempt group whose only settled dispatch is `voided`;
* `log ↔ input ↔ report` identity join: the `final_review_audit_written` row's `task_id`/
  `dispatch_id` equal the record's, and the record's `artifact_path`s resolve to existing files;
* the §9 report-path ladder: `resolution` is `ladder` for attempt 2 when the suffixed file exists
  and `fallback_unsuffixed` when only `FINAL_REVIEW.md` does.

### T-2 Failure handling — `scripts/test_run_logging.py`

* a dispatch-input failure still writes a record with `capture_status` and `observed_input_bytes`
  populated and pre-failure input evidence retained where the capture succeeded;
* a retry is recorded under a **separate** task/dispatch identity and is not merged with the
  original — asserted by the two published `<key>/` directories existing side by side with
  distinct keys and no file under either mutated;
* **fault injection on the retry path**: a dispatch whose audit write failed mid-staging, followed
  by a retry under a new identity, produces one complete published record for the retry and one
  retained `.staging/` entry for the failed original; the retained entry is reported as
  `final_review_audit_incomplete_publication` and is never read as a record, and
  `read_final_review_attempt_provenance()` is unaffected by its presence;
* the retained failure record satisfies §3 while `read_final_review_attempt_provenance()` still
  reports `no_accepted_dispatch`, i.e. the §7 baseline is **not** satisfied by it;
* a malformed record, a missing record, and an unknown-MAJOR record each read `unknown`, never
  `accepted`;
* `observed_input_bytes` + `failure_detail` recorded verbatim;
* **guard test**: `scripts/run_logging.py`, `tools/run_logging.py`,
  `scripts/orca_runtime_harness.py` and `scripts/e2e_harness.py` contain none of the literals
  `14805`, `5553`, `2269`, `14.8`, `5.5`, `2.3` as a threshold constant (ANALYSIS F6, PLAN C19).

### T-3 Security — `scripts/test_run_logging.py`

* `redact_text()` is deterministic: the same input twice yields identical bytes **and** identical
  `redactions`, across two processes;
* `artifact_digest_post_redaction` re-hashes the file on disk and matches;
* a synthetic `dcap_AAAAAAAAAAAA…` token and a `/Users/<name>/…` path do not survive into either
  retained artifact, and the replacement tokens appear instead;
* **non-home absolute paths (C.3 category 5, the R1 regression guard)** — a table-driven test whose
  inputs include, at minimum,
  `/private/tmp/claude-501/-Users-<user>-aiAssistedProjects-orca-skills/<uuid>/scratchpad/x/REPORT.md`
  (the exact shape of the shipped-baseline leak), `/var/folders/ab/cd/T/tmpxyz`, `/tmp/a/b`,
  `/opt/homebrew/bin/python3`, `/etc/passwd`, `file:///private/tmp/a/b`, **and the one-segment
  cases `/tmp`, `/luminous`, `/workspace-501` and `/session-<uuid>` (the D3-001 guard: these were
  the loophole the ≥2-segment floor left open, and they are now positives, not negatives)**. Each asserts the whole
  path is replaced by `<REDACTED:foreign_absolute_path>`, that the literal username, the uid
  fragment and the session UUID appear **nowhere** in the retained bytes, and that `redactions`
  reports a non-zero `foreign_absolute_path` count — the shipped baseline reported `[]`, so the
  count assertion is itself a regression guard;
* **category 5 negatives, asserted individually so a future tightening cannot silently over-redact**:
  `https://host/a/b` and `postgres://u:p@h/db` (URLs survive category 5), `<ARTIFACT_ROOT>/final_
  review_audit/k/input.md` (placeholder-rooted path survives), `artifacts/runs/run_x/report.md`
  (repo-relative survives), `PASS/FAIL`, `and/or` and `redaction/1.1` (prose survives), and
  `/Users/<name>/repo/x.py` — which must come out as category 4's readable
  `/Users/<REDACTED:absolute_local_path>/repo/x.py`, **not** swallowed whole by category 5. These
  four exemption classes are exactly the ones C.3 guarantees; the list is closed, and a bare `/tmp`
  is deliberately **not** on it any more;
* **policy idempotence (C.2)**: for every input in both tables above,
  `redact_text(redact_text(t)[0])[0] == redact_text(t)[0]`, byte for byte;
* **P-PATH on every path-bearing record field (C.7)** — with a `contract_path` pointing at
  (i) a file under `<ARTIFACT_ROOT>`, (ii) a file in the repository but outside `<ARTIFACT_ROOT>`,
  and (iii) a file under a scratch root outside both, the written `record.json` field is
  respectively an `<ARTIFACT_ROOT>`-relative path, a `<REPO>/…` path, and
  `<REDACTED:foreign_absolute_path>`;
* **`normalize_retained_path_field()` is total, and the D3-001 loophole is closed by name** — a
  table-driven test over the classifier itself. **Mapped to `<REDACTED:foreign_absolute_path>` in
  full**: `/luminous`, `/tmp`, `/workspace-501`, `/session-1f2e3d4c` (**every one of these is one
  segment — the exact shape that the ≥2-segment floor plus the fixed-point test let through**),
  `/private/tmp/a/b`, `file:///private/tmp/a/b`, `/Users/<REDACTED:absolute_local_path>/repo/x.py`
  (a *partially* substituted home path is still an absolute path, so the field form replaces it
  whole), `C:\Users\bob\x`, `C:/Users/bob`, `a/../../etc/passwd`, `..`, and a value carrying
  prose around a path (`"built under /luminous"`). **Returned unchanged**:
  `final_review_audit/k/input.md` and `artifacts/runs/run_x/report.md` (P1),
  `<REPO>/artifacts/x/FINAL_REVIEW.md` (P2), `https://example.com/a` and `HTTPS://EX.COM/a` (P3),
  `""` (P3), and the placeholder itself (P4). The test additionally asserts **totality and the
  postcondition's agreement with the normalizer**: for every input `v` above,
  `normalize_retained_path_field(normalize_retained_path_field(v)) == normalize_retained_path_field(v)`
  and `assert_retained_path_field(normalize_retained_path_field(v))` does not raise;
* **the sweep does not ask whether the policy recognised the value** — a **generic sweep** over the
  serialized record asserts that every string value of every path-bearing field is in P1–P4, and
  that **no** string value anywhere in the record begins with `/`. It is stated this way on
  purpose: the earlier "every `/`-leading string must be a fixed point of the policy" formulation
  was circular — a value the policy does not recognise is a fixed point by definition and would
  have passed — and a dedicated case pins that down, asserting the sweep **rejects** a record whose
  `contract_path` was forced to the raw one-segment `/luminous` even though
  `redact_text("/luminous")[0] == "/luminous"` would hold under a hypothetical policy that did not
  match it. The sweep is also what catches a *newly added* path field that its author forgot to
  route through the ladder;
* **the postcondition raises rather than writes**: with `_relative_artifact_path()`'s ladder
  stubbed to return a raw absolute path — once multi-segment, once the one-segment `/luminous` —
  `write_final_review_audit_record()` raises `RunLoggingError` and publishes nothing in **both**
  cases; the staging directory is left with no published record;
* **an unknown policy version still raises**, including the now-superseded `"redaction/1.0"`;
* the category order of D-C C.3 is asserted: a `dcap_` inside `ORCA_TOKEN=dcap_…` is counted under
  `orca_dispatch_capability`, not under `env_secret_pattern`;
* `redactions` carries no redacted value, no offset and no per-occurrence digest — asserted by
  checking the serialized record contains neither the synthetic secret nor its length;
* pre/post identity is re-derivable: re-running the pipeline on the same source reproduces both
  digests and the same `redactions`;
* **raw bytes never on disk**: with a `tmp_path`-scoped write tripwire, no file other than the two
  redacted artifacts and the record is created during a write.

### T-4 Evaluation — `scripts/test_final_review_eval.py`

* each of the five intended seeded defects **actually exists** — demonstrated, not asserted:
  for each, a behavioural test executes the `head` code and shows the contract-violating outcome
  (e.g. SD-3: a batch of exactly `max_items` is rejected; SD-4: an explicit override does not take
  effect; SD-5: an invalid record reaches the store through `republish`), and the corresponding
  `base` behaviour is correct;
* `head`'s own test suite passes (that is what makes the defects search-resistant) and `base`'s
  does too;
* answer-key correctness: `verify-fixture` green — every `location.file`/`symbol`/`line_range`
  resolves, the `base`→`head` diff touches each range, and `fixture_digest` matches;
* `subject/` contains no key token and no key path (D.6 scan, exit 0);
* **the retained reviewer input contains no key token and no expected-count statement** (D.6 scan
  against `final_review_audit/<key>/input.md`, exit 0);
* recall computed with an explicit denominator (`seeded_recall.denominator == 5`,
  `population == "seeded_defects_only"`);
* on the **open-world** path an unmatched finding is `UNADJUDICATED` and never auto-FP, including
  the `ambiguous_match` and `unresolvable_location` reasons;
* precision is **refused** under insufficient adjudication (`precision: null`,
  `precision_status: REFUSED`, non-empty reason) and exits **3** when `--require-precision` was
  passed;
* **the closed-world path, asserted by exact value rather than by "computation occurred" (R3).**
  A perfect report plus exactly one noise finding whose location resolves but matches no key entry,
  a complete `exhaustive_attestation`, `closed_world: true`, and **no** verdicts. Asserted:
  `findings_total == 6`, `len(matched_findings) == 5`, the noise finding's `unmatched_findings`
  entry has `reason == "no_key_match"` and `classification == "ATTESTED_FALSE_POSITIVE"`,
  `attested_false_positives == 1`, `adjudicated_false_positives == 0`,
  `unadjudicated_count == 0`, `adjudication_status == "complete_by_attestation"`,
  `precision == 5/6` and `false_positive_rate == 1/6` — **not `0`**, which is what the previous
  spec produced — and `precision + false_positive_rate == 1`. The pre-fix behaviour fails four of
  these assertions, so the test is a real regression guard rather than a formality;
* **an explicit verdict beats the attestation**: the same closed-world input plus a
  `{"finding_id": "<noise>", "verdict": "true_positive"}` verdict yields
  `classification == "ADJUDICATED_TRUE_POSITIVE"`, `attested_false_positives == 0`,
  `adjudication_status == "complete"`, `false_positive_rate == 0` and `precision == 1`;
* **closed world still REFUSES on an incompletely evaluated match**: with the noise finding's
  reason forced to `unresolvable_location` (and, in a second case, `ambiguous_match`) and no
  verdict for it, both metrics are `null`/`REFUSED` with reason
  `closed_world_incomplete_match_evaluation`, `classification == "UNADJUDICATED"`,
  `adjudication_status == "partial"`, and `--require-precision` exits **3**; supplying a verdict
  for exactly that id flips the same input to COMPUTED;
* **the gate is a single decision**: across every case above,
  `precision_status == false_positive_rate_status`, and `precision_status == "COMPUTED"` implies
  `unadjudicated_count == 0`;
* `findings_total == 0` refuses both metrics with reason `no_findings` and never divides;
* **E.3's coupling**: `closed_world: true` with `exhaustive_attestation: null` exits **2**, and
  an `exhaustive_attestation` supplied while `closed_world: false` exits **2**;
* an adjudication file carrying an extra key (e.g. `was_corrected`) exits **2**;
* **the scorer's own path fields obey C.7 P-PATH**: scoring a findings file whose `source_report`
  is an absolute path under a scratch root outside the repository writes `findings_source` as
  `<REDACTED:foreign_absolute_path>` — asserted for both a deep scratch path and the one-segment
  `/luminous`, so the scorer cannot carry D3-001's loophole either — and every string value in the
  metrics document is in P1–P4, with none beginning with `/`;
* the SD-2/SD-5 disambiguation: two findings in `src/pipeline.py`, one naming `publish_batch` and
  one naming `republish`, match their own defects and not each other's;
* determinism, **unqualified (B5)**: `score` run twice on identical inputs produces a
  byte-identical metrics file — the whole file, compared as bytes, with no excepted key. Asserted
  both within one process and across two subprocess invocations;
* **no clock in the metrics document (E.5)**: with `time.time`, `time.monotonic`,
  `datetime.datetime.now` and `datetime.datetime.utcnow` patched to raise inside
  `scripts/final_review_eval.py`, `score` without `--provenance-out` still exits **0** and writes
  the full metric block; and the serialized metrics document contains no `generated_at` key and no
  value matching an ISO-8601 timestamp;
* `--provenance-out` writes its sidecar to a **separate path**, the sidecar's `metrics_digest`
  matches `sha256` of the metrics bytes, and passing `--provenance-out` does **not** change the
  metrics bytes by so much as one byte versus the same run without it.

### T-5 Regression

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/verify_package.py
git diff --check 1045815..HEAD
```

All four green (`git diff --check` exits 0 with no output). Plus: existing lifecycle / Risk /
Quality Profile / Agent Profile tests untouched and passing; and a grep assertion that **no
workflow path performs an automatic `git add`, commit or push of run artifacts** (PLAN C20).

#### T-5a Retained-report whitespace exemption — `scripts/test_run_logging.py` (A.6, R5)

`RetainedReportWhitespaceExemptionTests`. The point of the test is that the two halves of A.6 must
hold **together**: passing the gate is worthless if it was bought by editing a digest-bound file,
and an intact digest is worthless if the gate still fails. Both are asserted in one test class, and
neither assertion may be satisfied by the other's fix.

* **(a) The gate passes.** Run `git diff --check 1045815..HEAD` as a subprocess from the repository
  root and assert **exit 0 and empty stdout**. Skipped — not passed — when the repository has no
  `.git` directory or commit `1045815` is unreachable, so the test never silently degrades into a
  vacuous success.
* **(b) The exempted bytes are untouched.** For **every** published record unit found under
  `artifacts/runs/*/final_review_audit/*/`, load `record.json` and re-hash the file named by
  `report.artifact_path`; assert the SHA-256 equals `report.artifact_digest_post_redaction` and the
  size equals `report.byte_length_post_redaction`. The same check is applied to
  `stored_task_spec.artifact_path`. This is what proves the exemption did not require trimming the
  file:
  `artifacts/runs/run_92759e0e1034/final_review_audit/attempt1__task_936f73b5d2eb__ctx_1f82fd26c92b/report.md`
  must still hash to `sha256:6f91033e4e2f644ab64eb4e61292734671b588d51ff0eb1649c626f8ae748e18` at
  6028 bytes, with its 40 hard-break lines intact.
* **(c) The exemption is narrow, asserted rather than assumed.** `git check-attr whitespace --
  <path>` returns `unset` for each retained `report.md`, and `unspecified` for each sibling
  `input.md` and `record.json`, for `scripts/run_logging.py`, and for the repository-root
  `README.md`. A repo-wide `* -whitespace` line or a `core.whitespace` change would fail this
  assertion.
* **(d) Mutation check.** With `.gitattributes` temporarily moved aside in a scratch clone, (a)
  must **fail** with exit 2. That is what makes T-5a a real regression rather than a test that
  passes because the underlying condition happens not to occur.

### T-6 Neutrality — `scripts/test_e2e_harness.py`

`FinalReviewObservabilityNeutralityTests`, exactly the assertions of N.2:

* **byte identity** against `pre_os22_task_specs.json` across both families including
  `final_review` specs, compared as UTF-8 bytes after `canonicalize_task_spec()` — never after
  `_normalize_artifact()` (N.1.1);
* **the mutation test (N.2 item 1a)**: four whitespace-only mutations of a worker, a reviewer and
  a `final_reviewer` spec each make the golden **fail**, and the test body shows
  `_normalize_artifact()` would have accepted three of them. This is what makes T-6 a byte-strict
  test rather than a test that merely says it is;
* **no terminal newline** on any captured spec (N.2 item 1b);
* **capture-time tripwire**: `canonicalize_task_spec()` raises on any unenumerated nondeterministic
  residue (temp dir, repo root, ISO-8601 timestamp, orca-assigned id), so a future spec change that
  introduces one fails the capture instead of being silently normalized away;
* no new `render_task_spec()` parameter; the redaction/audit unreachability tripwire;
  `LegacyByteIdentityTests` and `pre_os4_artifacts.json` untouched and still passing.

### Baseline procedure (B-1 … B-5, §7)

```text
B-1  python3 scripts/final_review_eval.py materialize --dest <scratch>
     (no .git, no key; D.5 rule 4 assertions must pass)
B-2  Dispatch ONE Final Review attempt against <scratch> with NO change to detection/search policy
B-3  Capture the audit records for that dispatch -- including, on a dispatch-layer failure, the
     pre-failure input evidence, observed_input_bytes and failure_detail. Never swallow the
     failure. A captured failure is §3 evidence and is NOT a satisfied baseline.
B-3R On a dispatch-layer failure: retry B-2/B-3 under a NEW Task/Dispatch identity, leaving the
     failed dispatch's records untouched. Loop until one dispatch settles with a usable report or
     the max-iterations budget (DEFAULT_MAX_ITERATIONS = 5) is exhausted. If exhausted, record
     the §7 baseline as FAIL and STOP -- do not proceed to B-4 with no report.
B-4  Run the scorer as a SEPARATE step, after the reviewer submitted (§5 requires the separation
     in time, not only in code):
       python3 scripts/final_review_eval.py parse-report --report <retained report> --out f.json
       python3 scripts/final_review_eval.py score --findings f.json \
           --key scripts/fixtures/final_review_eval/key/answer_key.json \
           --workspace <scratch> --out metrics.json
B-5  Record DEC-9's B1-B5 independently.
```

| criterion | passes when |
|---|---|
| B1 procedure ran | every step executed as documented **including at least one dispatch that settled with a usable report** |
| B2 scoring worked | the full metric block emitted, with `precision_status` and `false_positive_rate_status` both `REFUSED` unless one of E.5 point 2's two computation paths was recorded; when computed, `precision + false_positive_rate == 1` and `unadjudicated_count == 0` |
| B3 artifacts produced | input + report + record exist; `artifact_digest_post_redaction` re-verifies; the log ↔ input ↔ report join succeeds on `task_id`/`dispatch_id`; **and the retained family is environment-safe (C.7 P-PATH)** — `<scratch>` is by construction an absolute path outside the repository, so it appears in the reviewer's input and in `report.contract_path`, and the acceptance check is a grep of `<key>/input.md`, `<key>/report.md`, `<key>/record.json` and any exported bundle for the local username, the `-Users-`-encoded workspace shape and `/private/tmp/` returning **zero** hits, with `redactions` reporting a non-zero `foreign_absolute_path` count. Recording a baseline whose own evidence leaks the environment it ran in is not a satisfied B3 |
| B4 no answer-key leak | D.6's scan of the retained reviewer input returns zero hits |
| B5 reproducible | re-running `score` on the same stored output reproduces **byte-identical metrics for the entire file, with no excepted field** (E.5 keeps every clock-derived value out of the metrics document; any `--provenance-out` sidecar is a different artifact and is not part of this comparison), and the dispatch inputs are recorded well enough to re-issue |

Recorded separately and explicitly: the Reviewer's **verdict is an observation, not a criterion** —
a baseline where the Reviewer returns FAIL, or misses all five seeded defects, still passes B1-B5.
**No detection-quality conclusion is drawn**, and no H-1/H-2/H-4/H-5 comparison appears in any
artifact. The baseline's output is a reference point for OS-23, and the written baseline report
must say so in those terms.

---

## Risks / Open Issues

### Risks carried from PLAN, with the concrete mechanism this design gives each

| id | risk | mitigated by |
|---|---|---|
| R-1 | observability changes reviewer-visible input (§2) | **N-1**: no spec mutation by construction; byte-identity golden from `1045815` with two capture families and a signature assertion; unreachability tripwire |
| R-2 | a voided report is read as the accepted verdict | **D-A A.3** immutable dispatch-keyed records, published by one atomic rename so a partial write is never readable as a record + **D-B B.1/B.3**: no default is `accepted`, fail-closed `unknown`, and the attempt reader refuses to pick a winner on a violation |
| R-3 | secret/credential **or environment-identifying path** in a retained artifact | **D-C**: post-dispatch redaction, five ordered categories — `dcap_`, URL credentials, env secrets, home-rooted paths, and (C.3 category 5) **every other absolute path, replaced whole** — plus C.7's **P-PATH**, a closed set of four allowed output categories every path-bearing field must land in regardless of which code path wrote it (anything else is replaced whole, and nothing is ever accepted merely because a pattern failed to match it), raw bytes only in memory and only from a stdout pipe, and four identity fields |
| R-4 | answer-key leak | **D-D D.1/D.5/D.6**: path separation, materialized workspace with no `.git`, mechanical scan of `subject/`, of the workspace, and of the retained input; split commits. Residual limitation stated, not hidden |
| R-5 | fixture solvable by string search | **D-D D.3**: per-defect negative-space argument naming the exact cross-read path; all five are defects of absence; the `head` suite is green, so no failing test localizes any of them |
| R-6 | baseline cannot complete for dispatch reasons | **Baseline procedure B-3R**: retry under a new identity, budget-bounded; exhaustion ⇒ §7 recorded FAIL, never a pass or a "captured outcome" |
| R-7 | validator/packaging breakage | Step 2 is one commit + `validate_skills.py` immediately after; byte-parity copy in every `run_logging.py` commit; `verify_package.py` in the validation set |
| P-6 | an audit-write failure mutates settled lifecycle state | every write goes through `_safe_log` (`orca_runtime_harness.py:2076`); T-2 covers the malformed/incomplete path |

### Open issues — raised, not designed around

**O-1 (non-blocking, mechanism note on DEC-4's "not reachable" requirement).** PLAN requires that
"the audit/redaction module is not importable from — and not called on — any code path between
spec assembly and the dispatch call." Literal non-importability is not achievable: I-2 places the
redaction code inside `run_logging.py`, which `orca_runtime_harness.py` — the module that performs
the dispatch — already imports for logging. The requirement is therefore implemented as
**non-invocation**, verified by N.2 assertion 3's tripwire (patched entry points raising on any
call from the assembly→dispatch edge), which is strictly stronger evidence than an import graph:
an import proves nothing about a call, and the tripwire proves the call did not happen. Flagged
here rather than silently reinterpreted. Should a Reviewer judge non-importability to be
load-bearing, the alternative is a separate `scripts/final_review_audit.py` module — which would
break I-2's "no new shipped file, so `release_manifest.py` needs no change" rationale and require
a `required_skill_paths()` change, so it is not taken unilaterally.

**O-2 (non-blocking, consequence of DEC-10 ii).** Because the suffix defect is deferred,
`phase_artifact_contract()` hands every attempt the same `FINAL_REVIEW.md` on the real dispatch
path, so attempt N+1's Reviewer can overwrite attempt N's report **before** run end. This design
handles it by snapshotting inside the settlement sequence (D-A A.4's ordering invariant) and by
recording `report.resolution`, which makes the deferred defect visible as data. The residual
exposure is a window between settlement and snapshot; it is bounded by the snapshot being the
first action after four-axis finalization, and there is no concurrent Final Review dispatch (§17's
Task graph is a single node with no dependencies), so nothing can write that file during the
window. Raised so the deferral's real cost is on the record rather than absorbed silently.

**O-3 (non-blocking, packaging consequence of DEC-7's storage layout).**
`release_manifest.py:35`'s `INCLUDED_ROOTS` contains `scripts/`, so
`scripts/fixtures/final_review_eval/key/answer_key.json` **will be included in the release
archive**. This does not weaken the claim OS-22 makes — that claim is about the reviewer's
retained *input*, verified per run (D.6) — and the key was always going to be in the repository
under DEC-7's layout. It is surfaced because it is a real, non-obvious consequence: a downloaded
release tarball contains the answer key. Excluding it would require an exclusion rule in
`release_manifest.py`, which is a packaging-semantics change outside OS-22's scope and is
therefore **not** taken here; recommendation is to accept and note it in `CHANGELOG.md`.
Constraint for IMPLEMENTATION either way: `verify_package.py`'s `USER_PATH_PATTERNS` scan runs
over packaged files, so no fixture file may contain a real `/Users/<realname>/…` path.

**O-4 (non-blocking, maintainability).** `run_logging.py` grows from 1064 lines to roughly 1500
with I-1 … I-5, and every line is duplicated into `tools/run_logging.py`. This is the direct cost
of I-2's decision to avoid a new shipped file, and of the module's no-`scripts`-imports rule. It
is a maintainability observation, not a defect, and no split is proposed inside OS-22 because a
split would change `release_manifest.py::required_skill_paths()`.

**O-5 (non-blocking, fixture maintenance).** `answer_key.json`'s `fixture_digest` and each
defect's `line_range` must be updated by hand whenever `subject/head/` changes, and D.5 rule 4
deliberately offers no `--update-digest`. A stale digest fails `materialize` (exit 2) and
`verify-fixture` (exit 4) loudly rather than silently mis-scoring, which is the intended trade;
the maintenance burden is stated so it is not discovered later as a surprise.

### Explicitly not designed (PLAN's Out of Scope, restated so IMPLEMENTATION cannot drift)

* No OS-23 detection/search-quality change of any kind.
* **No `reviews/final_review.md` is created** — ANALYSIS F7's empty slot is a trap, not an
  invitation. No falsification policy, no search-depth obligation.
* No reviewer/model optimization, no agent-profile routing change.
* No conclusion, ranking or partial verdict on H-1 / H-2 / H-4 / H-5.
* No lifecycle-semantics change: `RESULT:` stays two-valued, `REVIEW_VERDICT:` stays four-valued,
  correction and downstream-revalidation semantics untouched.
* No observed `agent_prompt_blocked` size number as a product constant (T-2 guard).
* No deletion, compaction or GC of run artifacts; no `git add` of them.
* §16's four-axis `## Orca Orchestration State` ledger is **not** trimmed; the duplication-trim is
  recorded as an OS-23/backlog follow-up.
* `VERSION` and `LICENSE-DECISION.md` unmodified; nothing merged.
* DEC-10(ii)'s `task_context.py` suffix defect remains deferred, with its reversal protocol
  recorded in N.2.

## Review Feedback Resolution

### DESIGN iteration 5 (correction) — Final Adversarial Review R5

Source: `artifacts/runs/run_804e35d29531/FINAL_REVIEW_iteration2.md` (RESULT: FAIL). One
MAJOR/blocking finding, R5. The same review confirmed R1, R2, R3 and R4 **resolved** and found the
two documented residual disclosures (the append-only `ORCHESTRATOR_LOG.md` row and the three
immutable `final_review_audit/**/input.md` files) **acceptable** under the audit-immutability
invariants. None of those is reopened here. This iteration adds **one new subsection, A.6**, and
the test and file-list entries that A.6 requires; D-B, D-C, D-D, D-E, D-F and N-1 are untouched, no
PLAN DEC is reopened, and no already-committed byte is modified.

#### R5 — the mandatory whitespace gate failed on an immutable retained report (G5, MAJOR, blocking)

**Finding.** `git diff --check 1045815..HEAD` exits 2 with 40 `trailing whitespace.` errors in
`artifacts/runs/run_92759e0e1034/final_review_audit/attempt1__task_936f73b5d2eb__ctx_1f82fd26c92b/report.md`.
The bytes cannot be trimmed: `record.json` binds them to
`artifact_digest_post_redaction = sha256:6f91033e...e748e18` at 6028 bytes, and A.3/P7 make a
published record immutable with no mutation surface. The repository state could therefore satisfy
the required full validation **or** its own immutable-evidence rule, never both.

**Root cause.** The 40 lines end in two spaces — the Markdown hard line break, which is what makes
the Reviewer's `ID:` / `Severity:` / `Blocking:` blocks render as separate lines. Git's default
whitespace rules include `blank-at-eol`, so `git diff --check` flags every such added line. Both
sides are individually correct; the design was silent on how byte-exact retained audit Markdown
participates in the gate. This is a **design** omission, not an implementation defect — the
implementation correctly retained and digested the Reviewer-produced bytes.

**Resolution — new A.6.** A path-scoped Git `-whitespace` attribute in a new repository-root
`.gitattributes`, matching `artifacts/runs/*/final_review_audit/**/report.md` and nothing else.
This suppresses the gate for that artifact class without altering one byte of any published record.

**Scope was measured, not assumed.** All 15 files under all three `final_review_audit/` directories
were scanned: only `report.md` carries trailing whitespace (1 of 5 files, 40 lines), and only
`report.md` *can*, because it is the sole member of the record unit whose bytes are free-form prose
authored by an external Reviewer. `input.md` and `record.json` are produced by this codebase under
its own formatting control and stay fully gated. The exemption is scoped to `report.md`, not to the
audit directory.

**The mechanism was verified before being written down**, against the real failing file rather than
by reasoning: the gate goes 2 -> 0 with the attribute present, again in a fresh clone with
`.gitattributes` committed; the report's digest is unchanged and `git diff --quiet` reports the
file unmodified; `git check-attr` returns `unset` only for retained `report.md` paths and
`unspecified` for siblings, source and docs; trailing whitespace injected into `src/*.py` and into
a sibling `input.md` is still flagged with exit 2; and `verify_package.py` / `validate_skills.py`
still pass with the new root file present. The full evidence table is in A.6.

**Regression.** New T-5a asserts the gate passes **and** that every retained artifact still matches
its recorded digest and byte length, plus a narrowness assertion over `git check-attr` and a
mutation check that fails when `.gitattributes` is moved aside. Either half alone would be
satisfiable by the wrong fix; only the conjunction pins the invariant. `git diff --check
1045815..HEAD` is also added to T-5's standing command list so the gate is a first-class regression
rather than an ad-hoc manual step.

#### Not reopened in iteration 5

R1, R2, R3, R4 and their D-C / D-E fixes are confirmed resolved and are untouched. The two
Coordinator-owned residual disclosures are settled and untouched. Nothing in D-A other than the new
A.6 changed, and A.3's immutability rule is strengthened by this iteration rather than relaxed —
the whole point of the mechanism chosen is that it required zero edits to published bytes.

### DESIGN iteration 4 (correction) — Final Adversarial Review D3-001

Source: `artifacts/runs/run_804e35d29531/REVIEW_DESIGN_iteration3.md` (RESULT: FAIL). One
MAJOR/blocking finding, a residual gap in iteration 3's R1 fix. This iteration changes **D-C only**.
D-E is untouched — R3 was confirmed resolved by the same review — as are D-A (except the reference
noted below), D-B, D-D, D-F and N-1. No PLAN DEC is reopened and no production code is edited here.

#### D3-001 — the absolute-path guarantee failed open, and its validator was circular (G1, MAJOR, blocking)

**Finding.** Two coupled defects. (i) Category 5 required **two or more** segments, so a
one-segment environment-identifying absolute path — `/luminous`, `/workspace-501`,
`/session-<uuid>` — matched neither category 4 nor category 5 and passed through unchanged.
(ii) P-PATH's third case accepted any value for which `redact_text(value)[0] == value`. That is
circular: a value the policy does **not** recognise is a fixed point by construction, so "it is a
fixed point" proves only that a second pass changes nothing, never that the value is safe. Together
they meant `_relative_artifact_path("/luminous")` returned and serialized `/luminous`, and the
promised generic validator would have accepted it — every absolute path the policy failed to
recognise passed the check precisely *because* it failed to recognise it.

**Root cause, named so it is not repeated.** Both defects are the same mistake in different
clothes, and it is the same one R1 punished: safety was made to depend on **recognising the
dangerous shapes in advance** — first as an allowlist of dangerous roots (`redaction/1.0`), then as
a segment-count carve-out plus a "did any pattern match?" test (`redaction/1.1` draft). The fix
inverts it a second time: safety is now a property of the **output category the value lands in**.

**Resolved by** — the correction's option **(a)**, the positive/closed rule, with the fail-closed
postcondition of option (b) as its enforcement:

| where | change |
|---|---|
| **C.3.1** `_FOREIGN_ABSOLUTE_PATH` | The `(?:/{_PATH_SEGMENT})+` quantifier becomes `*` — **one or more segments, no floor**. `/luminous`, `/tmp`, `/workspace-501` and `/session-<uuid>` are now matched and replaced whole. The four lookarounds that keep the legitimate cases intact (`file://` branch, `>`-excluding lookbehind, `/`-excluding lookbehind, category-4 exclusion) are unchanged. |
| **C.3 notes** (new note, first in the list) | States why the floor was itself a fail-open carve-out — "a bare `/tmp` identifies nobody" does not generalise to `/luminous` — and records the accepted cost: a standalone `/tmp`, `/etc` or `/gsd-plan-phase` in prose is now also replaced. Over-redaction is the fail-closed direction, it is bounded to the token, and none of the four exemption classes is affected. |
| **C.3 boundary note** | Rewritten: the one-segment paths move into the **positives** list, and the negatives are restated as the **four exemption classes the design guarantees** — non-`file` URLs, placeholder-rooted paths (`<ARTIFACT_ROOT>/…`), repo-relative paths, and a `/` preceded by a word character (`PASS/FAIL`, `and/or`, `redaction/1.1`). |
| **C.2** (new paragraph *What idempotence is NOT*) | Idempotence stays a required property of the **policy**, but the design now states explicitly that it is never a certificate about a **value**, and that C.7 does not use it. The sentence that made C.7 depend on it is removed. |
| **C.7 P-PATH** — **rewritten** | Now a **closed set of four output categories** with a mechanical test each: **P1** `<ARTIFACT_ROOT>`-relative, **P2** `<REPO>/…`, **P3** not a filesystem path (empty, or a non-`file` URL), **P4** the fixed literal `<REDACTED:foreign_absolute_path>`. There is no fifth category, no segment-count condition and **no fixed-point case**. Anything else — a leading-`/` path of *any* length, a partially-substituted `/Users/<REDACTED:absolute_local_path>/a/b`, a Windows path, a `..` segment, embedded prose — is replaced **in full** by the P4 literal. |
| **C.7** (new literals + functions) | `FOREIGN_PATH_PLACEHOLDER`, `_SAFE_RELATIVE_PATH` and `_NON_FILE_URL` given verbatim, plus the **total** `normalize_retained_path_field()` (every input maps into P1–P4; it never calls `redact_text()`) and `assert_retained_path_field()` (raises `RunLoggingError` when a value is outside P1–P4). IMPLEMENTATION applies them mechanically — there is no remaining design judgement in the rule. |
| **C.7 ladder** | Rung 3 becomes "the whole value → `FOREIGN_PATH_PLACEHOLDER`", with `redact_text()` no longer called on the path field at all; the postcondition row becomes a re-run of the total classifier instead of a fixed-point test. The "postcondition is the actual guarantee" paragraph now says why a fixed-point test would have accepted `/luminous`. |
| **C.7 scope table** | Restated: every listed field goes through `normalize_retained_path_field()` **and** `assert_retained_path_field()` at record-assembly time whatever produced it — the column says how a value normally *arrives* in an allowed category, not which fields skip the check; none do. `process_incarnation` is clarified as a free-form field that *contains* a path (C.3 choke point, now floor-free) rather than a whole-value path field. |
| **Failure posture table** | The P-PATH row's condition is restated as "in none of P1–P4", with D3-001's reason recorded: a postcondition phrased as "the policy leaves it unchanged" would have passed a raw `/luminous`. The unresolvable-repo-root row now says rung 3 replaces the whole value. |
| **Step 1 (I-2)** | The constant/function list names `FOREIGN_PATH_PLACEHOLDER`, `_SAFE_RELATIVE_PATH`, `_NON_FILE_URL`, `normalize_retained_path_field()`, `assert_retained_path_field()`, and the ladder's rung 3 **without** `redact_text()`. |
| **T-3** | The one-segment paths move from the negatives list into the **category-5 positives**, labelled as the D3-001 guard. A new bullet drives `normalize_retained_path_field()` directly with a replaced-in-full table (including all four one-segment shapes, a partially-substituted home path, Windows paths, `..`, and prose-around-a-path) and an unchanged table (P1/P2/P3/P4), asserting totality and postcondition agreement. The generic sweep is re-specified as "every path-bearing value is in P1–P4 **and no record string begins with `/`**", with an explicit case asserting the sweep **rejects** a forced raw `/luminous` — the circular fixed-point formulation is gone. The postcondition-raises case is run twice, once multi-segment and once one-segment. |
| **T-4** | The scorer's `findings_source` case is asserted for the one-segment `/luminous` as well as a deep scratch path, so the scorer cannot carry the same loophole. |
| **Risks R-3** | The mitigation text no longer cites idempotence as the decidability mechanism; it cites P-PATH's closed category set and "nothing is accepted merely because a pattern failed to match it". |

#### Not reopened in iteration 4

**D-E is untouched** — R3's resolution stands exactly as iteration 3 left it, including E.3's
coupling, E.5's two paths, `attested_false_positives`, `complete_by_attestation` and the three
invariants; the only D-E edit is T-4's one-segment assertion, which adds a case and changes no
metric semantics. D-A (A.4's `contract_path` comment already says "NEVER a raw absolute path" and
still holds), D-B, D-D, D-F, N-1, the Open Issues list and the Out-of-Scope list are unmodified.
D-C's C.1, C.4, C.5 and C.6 are unmodified, as is category 4. The policy label stays
`redaction/1.1` because `1.1` is unreleased — it is introduced by iteration 3 and no artifact has
ever been produced under it — so this is an edit to an unreleased policy, not a behaviour change
within a released one; C.2's versioning rule and C.7's one-time regeneration of the `1.0` baseline
are both unaffected.

---

### DESIGN iteration 3 (correction) — Final Adversarial Review R1, R3

Source: `artifacts/runs/run_804e35d29531/FINAL_REVIEW.md`. Two MAJOR/blocking findings were routed
to DESIGN because in both cases the implementation follows the specification faithfully and the
**specification** is what is wrong or incomplete (§17's Responsible Phase ladder: 코드는 명세를
따르는데 명세가 틀림 → DESIGN). This iteration changes the spec only. No production code is edited
here; the corresponding code changes belong to the IMPLEMENTATION phase's downstream revalidation.
No PLAN DEC is reopened, and D-A, D-B, D-D, D-F and N-1 keep their iteration-2 content except for
the cross-references listed below.

> **Historical record — partly superseded.** The R1 table below describes iteration 3 as written.
> Three of its clauses were later found insufficient and were replaced in **iteration 4** (D3-001,
> above): C.3.1's ≥2-segment floor on category 5, P-PATH's case **(c)** "a fixed point of the
> policy", and C.2's claim that C.7 depends on idempotence. The current rule is the closed P1–P4
> output-category set in C.7; read iteration 4's entry for what is in force. R3's table is
> unaffected and remains current.

#### R1 — the retained audit family is not local-path safe (G4, MAJOR, blocking)

**Finding.** D-C category 4 defined the *entire* absolute-path rule as
`r"(/Users/|/home/|/root/)…"`. A path beginning with any other absolute prefix passed through
untouched, so the shipped baseline retained
`/private/tmp/claude-501/-Users-luminous-aiAssistedProjects-orca-skills/<session-uuid>/scratchpad/…`
verbatim in `input.md`, in `record.json.report.contract_path` and in the export bundle — username,
uid and session UUID included — while the record truthfully reported `"redactions": []`. Two
distinct spec gaps: the pattern was an allowlist of dangerous roots (fails open on every root not
enumerated), and `contract_path` is written by `_relative_artifact_path()`, a code path the
redaction spec never claimed to cover.

**Resolved by.**

| where | change |
|---|---|
| **C.2** (new paragraphs) | The policy ships as **`redaction/1.1`**, because the substituted output for the same input changes and a digest is only comparable within one policy version. Exactly one version stays executable; `redact_text()` raises for `"redaction/1.0"` too. Older records keep their `1.0` stamp as *data*. **Idempotence is added as a required property**: `redact_text(redact_text(t)[0])[0] == redact_text(t)[0]`, with the per-category reason each replacement token cannot re-trigger a substitution. The fixed point is what makes "already redacted" a decidable property rather than a comment — C.7 depends on it. |
| **C.3 table** | Category 4 is **narrowed in name and scope** to `absolute_local_path` — *home-rooted paths only*, behaviour unchanged. **New category 5, `foreign_absolute_path`**: every other absolute path, **whole match replaced** by the fixed literal `<REDACTED:foreign_absolute_path>`. |
| **C.3.1** (new subsection) | The two path patterns given **verbatim as authoritative Python literals** rather than escaped into a table cell, with `_PATH_SEGMENT`, `_HOME_ABSOLUTE_PATH` and `_FOREIGN_ABSOLUTE_PATH` and an inline comment on every assertion in the new pattern (the `file://` branch, the `>`-excluding lookbehind that keeps `<ARTIFACT_ROOT>/…` readable, the `/`-excluding lookbehind that makes URLs unreachable, the category-4 exclusion, the ≥2-segment floor). IMPLEMENTATION copies them; it does not re-derive them. |
| **C.3 notes** | Three notes replace one: *why an allowlist of roots can never be the rule* (with the concrete leak as the worked example); category 4's unchanged readability-preserving behaviour; and **why partial preservation was considered and rejected** for category 5 — every candidate fragment (basename, first segment, length, hash) is environment-identifying in some real layout, so "keep the safe part" would be the same allowlist mistake one level down. A fourth note states the **boundary behaviour as a list of positives and negatives**, so it is testable rather than discovered. |
| **C.7** (new subsection) | **P-PATH**, the field-level property that answers the second half of R1: *every* retained string field that can hold a path is, on disk, exactly one of (a) `<ARTIFACT_ROOT>`-relative, (b) `<REPO>/…`, (c) a fixed point of the policy — **regardless of which code path wrote it**; there is no fourth case and the writer raises rather than write one. `_relative_artifact_path()` gains rung 2 (`<REPO>/…`) and an always-checked postcondition. The answer to "redact `contract_path`, or make it logical?" is stated as **both, in that order**: a report inside the repository keeps a readable environment-independent identity; only a path outside every root this project owns falls through to redaction. A closed table lists every path-bearing field (record, bundle, and the scorer's `findings_source`/`source_report`) with how each meets P-PATH, plus the rule that adding a path-bearing field later requires adding it to that table in the same commit. |
| **C.7** (regeneration rule) | The affected baseline evidence under `artifacts/runs/run_ff587481a820/final_review_audit/` is **regenerated** under `redaction/1.1` — never hand-edited (a hand-edited artifact is no longer evidence of what the pipeline produces) and never relabelled. |
| **C.3 "both artifacts" bullet** | Extended to name the record's free-form fields and to point at C.7 for path fields written outside the pipeline. |
| **A.4** | `contract_path`'s comment states the P-PATH normalization; a new paragraph names the four path-bearing fields of the record and why `contract_path` in particular is called out here. |
| **A.5** | New paragraph: the three version constants are independent, so `redaction/1.1` leaves the record schema at `1.0` (`redaction_policy_version` is a value the record carries, not a shape it has), while the D-E metrics schema takes a MINOR bump for R3's additions. |
| **F.2** | `component_versions.redaction_policy` → `"redaction/1.1"`. |
| **Failure posture table** | Two new rows: a P-PATH postcondition failure raises before staging and publishes nothing; an unresolvable repository root skips rung 2 and falls to redaction (fail closed — costs readability, never safety). The unknown-policy row now names `"redaction/1.0"` explicitly. |
| **Additive-ness** | New item 3: the policy bump is deliberate and scoped, the record schema is unaffected, the metrics schema takes a MINOR. |
| **Migration judgement** | New paragraph reconciling C.7's regeneration with "existing runs are not touched": `run_ff587481a820` is **OS-22's own unshipped baseline evidence**, re-captured by re-running this work item's pipeline — not a historical third-party run being migrated or backfilled from Orca state. Every other run keeps the untouched treatment, `redaction/1.0` stamp included. |
| **Step 1 (I-2)** | The constant list names `redaction/1.1`, the three pattern literals, the ordered **5**-tuple, and `_relative_artifact_path()`'s ladder + postcondition. A new **Baseline regeneration** paragraph makes the regeneration a same-commit obligation with a concrete acceptance condition (grep all of `artifacts/` for the username, the `-Users-` encoded-workspace shape and `/private/tmp/`; zero hits). |
| **Step 2 (SKILL.md §9, validator)** | §9 must state P-PATH and the policy version; `validate_final_review_audit_contract()` gains check (f) asserting both. |
| **T-3** | Six new bullet groups: the category-5 positives (led by the exact shape of the shipped leak, asserting the username/uid/UUID appear nowhere **and** that `redactions` reports a non-zero count — the baseline reported `[]`, so the count assertion is itself the regression guard); the category-5 negatives asserted individually so a future tightening cannot silently over-redact; idempotence over both tables; P-PATH across all three `contract_path` cases plus a **generic sweep** of every `/`-leading string in the serialized record (which is what catches a newly added path field); the postcondition raising rather than writing; and `"redaction/1.0"` still raising. |

#### R3 — closed-world false-positive rate was internally inconsistent (G2, MAJOR, blocking)

**Finding.** D-E E.5 point 2 offered two sufficient conditions for computing precision, but defined
what happens to an unmatched finding under only one of them. On the `closed_world` +
`exhaustive_attestation` path with no per-item verdicts, unmatched findings stayed `UNADJUDICATED`,
so `precision`'s denominator penalised them while `false_positive_rate =
adjudicated_false_positives / findings_total` reported a **false `0`** — and `adjudication_status`
read `partial` while the metrics claimed to be computed.

**Resolved by** — the Required Action's first option is adopted, scoped, and made arithmetically
self-checking:

| where | change |
|---|---|
| **E.5 point 1** | Rewritten with its scope stated in the rule itself: *"Unmatched is never auto-FP — **outside closed world**."* The default path is named, the closed-world rule is named as a **narrower, explicitly-signed exception rather than a contradiction**, and the shared reason both halves follow from is given (absent a signed attestation the key is a *sample*, so "did not match" carries no information). Stated in both points so a reader of either cannot leave with the wrong absolute — which is how the original contradiction survived review. |
| **E.5 point 2** | Rebuilt as **two mutually exclusive paths selected by `closed_world`**, each complete on its own; the previous "either … or" framing is gone. **Path A (closed world)**: the attestation's meaning is fixed in one sentence — *the key enumerates every true defect within the attested scope* — and a table classifies each unmatched finding by its E.4 step-6 **reason**: an explicit verdict always wins; `no_key_match` with no verdict → **`ATTESTED_FALSE_POSITIVE`**; `unresolvable_location` and `ambiguous_match` with no verdict → stay `UNADJUDICATED` and **REFUSE both metrics** with reason `closed_world_incomplete_match_evaluation`. The last two rows are argued, not asserted: those reasons mean the matcher could not *finish* evaluating the finding against the key, so the attestation — which speaks only about the key's coverage — says nothing about them, and auto-FP-ing them would manufacture precision out of a matcher limitation. **Path B (open world)** is unchanged. |
| **E.5 point 2, formulas** | `false_positive_rate = (adjudicated_false_positives + attested_false_positives) / findings_total`. `attested_false_positives` is `0` on path B by construction, so **path B's numbers are byte-identical before and after this revision**. |
| **E.5 point 2, invariants** | Three, asserted in the scorer itself and by T-4: `precision_status == false_positive_rate_status` always (one gate, one decision); `COMPUTED` **implies** `unadjudicated_count == 0`, hence every finding is accounted for exactly once and **`precision + false_positive_rate == 1` exactly** — which makes R3's failure mode *arithmetically impossible* in either direction rather than merely fixed in one place; and `findings_total == 0` refuses both metrics with reason `no_findings` instead of dividing by zero. |
| **E.5 point 2a** (new) | `adjudication_status` gains a fourth value and a **total** mapping: `none` / `partial` / `complete` / **`complete_by_attestation`**. The new value exists so the *provenance* of completeness stays visible — false positives derived from a signed scope claim are a materially weaker warrant than ones judged item by item, and must not be laundered into plain `complete`. This is what closes the finding's "it also calls adjudication status `partial`" half. |
| **E.5 schema** | `attested_false_positives` added (always present, non-zero only under closed world); `adjudication_status` comment lists four values; `unmatched_findings[].classification` enum spelled out with `ATTESTED_FALSE_POSITIVE` marked closed-world-only. |
| **E.3** | `closed_world` and `exhaustive_attestation` are now **coupled and validated**: `true` requires a complete attestation, `false` requires `null`, either mismatch is **exit 2**. This deletes the two half-states the input file could previously express, so the precision gate never has to guess what an unsigned closed world means. |
| **E.4 step 6** | Now says the matcher **never classifies** — it only records the reason — and points at E.5 point 2 as the sole classifier. Which reason was recorded is therefore part of the contract, not a diagnostic string. |
| **E.6** | Exit 2 gains the two E.3 coupling violations. |
| **Failure posture table** | Three new scorer rows: the coupling violation; the closed-world incomplete-match-evaluation refusal; and a `RuntimeError` on any internal violation of the three invariants — R3 was a silent inconsistency between two metrics, so it must abort rather than serialize. |
| **Step 5** | The two paths are implemented as **one** classification function over `unmatched_findings` whose only inputs are `closed_world`, the unmatched `reason` and the verdict map, so the two numerators cannot drift apart again; the three invariants are asserted in the scorer, not only in the tests. |
| **T-4** | The vague "computation occurred" case is replaced by **exact-value assertions**: the perfect-report-plus-one-noise-finding closed-world scenario now asserts `findings_total == 6`, `len(matched_findings) == 5`, the noise finding's `reason == "no_key_match"` and `classification == "ATTESTED_FALSE_POSITIVE"`, `attested_false_positives == 1`, `adjudicated_false_positives == 0`, `unadjudicated_count == 0`, `adjudication_status == "complete_by_attestation"`, `precision == 5/6`, `false_positive_rate == 1/6` (**not `0`**) and `precision + false_positive_rate == 1` — the pre-fix behaviour fails four of these. Plus: an explicit verdict overriding the attestation; closed world still refusing on `unresolvable_location` / `ambiguous_match` and flipping to COMPUTED when a verdict for exactly that id is supplied; the single-gate invariant across every case; the zero-findings refusal; E.3's two exit-2 coupling cases; and the scorer's own `findings_source` obeying C.7 P-PATH. |
| **A.5 / Additive-ness** | The metrics schema MINOR bump for `attested_false_positives` and the fourth `adjudication_status` value is recorded in both places, with the note that no existing metrics key changes meaning on the open-world path. |

#### Not reopened in iteration 3

D-A (except A.4's `contract_path` comment and A.5's version-independence paragraph), D-B, D-D,
D-F (except F.2's `component_versions`), N-1, the Risks/Open Issues list and the Out-of-Scope list
are unmodified. D-C's C.1, C.4, C.5 and C.6 are unmodified. D-E's E.1, E.2 and E.4's matching
algorithm (other than step 6's "the matcher never classifies" clarification) are unmodified.

---

### DESIGN iteration 2 (correction) — REVIEW_DESIGN.md D-001, D-002, D-003

Three MAJOR/blocking findings from `REVIEW_DESIGN.md` are
resolved below. D-A through D-F were confirmed substantially sound by the Reviewer and were not
rewritten; no PLAN DEC decision is reopened. Every edit is listed with its section.

#### D-001 — the neutrality golden was not a byte-identity test (G1, MAJOR, blocking)

**Finding.** N.2 applied the existing `_normalize_artifact()` to Task specs and then called the
result "character for character". `_normalize_artifact()` tokenizes each line with `split()` and
rejoins with a single space, so indentation, repeated interior spaces, trailing spaces and the
terminal newline all compared equal — weakening approved DEC-1 to a semantic argument.

**Resolved by.**

| where | change |
|---|---|
| **N.1.1** (new subsection) | `canonicalize_task_spec(spec, *, workspace)` — a Task-spec-specific transform that performs **one enumerated substitution** (the absolute workspace path → `<WORKSPACE>`) and nothing else: no `splitlines()`, no `split()`, no `join()`, no `strip()`, no reserialization. Family B passes `workspace=None`, making it the identity function. A closed `_TASK_SPEC_NONDETERMINISM_TRIPWIRES` list (temp dir, `/var/folders/`, `/private/tmp|var/`, repo root, ISO-8601, orca-assigned ids) makes the capture **raise** on any unenumerated residue rather than normalize it away, so the enumeration is honest or the test fails. |
| **N.1.1** | The enumeration is justified against the real code, read not assumed: the workspace path reaches a spec through exactly one field, `drill_down=(str(self.workspace),)` (`scripts/e2e_harness.py:1116`), verified as 0 occurrences in a worker spec and 1 in a reviewer spec; `run_id` is the fixed literal `"run_golden"` and `phase_artifact_contract()` (`task_context.py:284-308`) returns repo-relative paths; `scripts/task_context.py` imports neither `datetime` nor `time`, and a scan of all twelve captured specs for `_normalize_artifact()`'s own `<TS>` token shape finds **zero** hits — so there is no timestamp to strip and the `<TS>` rule is dead weight for Task specs. |
| **N.1.1** | Evidence that the old approach really was lossy: with the normalizer replaced by the identity function, every dispatched spec loses bytes (1 per worker spec, 8 per reviewer spec, across all twelve skill×workflow fixtures), on lines such as `relevant_previous_findings: ` / `approved_baseline: ` / `new_claims: ` whose empty values leave a **trailing space** (`task_context.py:609`), and on `current_delta:`'s embedded interior double spaces. |
| **N.1.1** | Storage/comparison stated as bytes: `json.dump(..., ensure_ascii=False)` round-trips trailing spaces and the missing terminal newline losslessly; the assertion is `assertEqual(current.encode("utf-8"), stored.encode("utf-8"))`. `render_task_spec()`'s absence of a terminal newline is pinned as part of the contract. |
| **N.1.1** | Scope separation: `canonicalize_task_spec()` is used only for `pre_os22_task_specs.json`; `_normalize_artifact()` keeps its unchanged job on `ORCHESTRATOR_LOG.md` / the final report, is not modified, and neither function calls the other. Log/artifact normalization is now fully separate from the neutrality proof. |
| **N.2 item 1** | Rewritten: comparison is over UTF-8 bytes after `canonicalize_task_spec()`, and `_normalize_artifact()` is explicitly forbidden here with the reason. |
| **N.2 items 1a, 1b** (new) | **Mutation test**, as required: four whitespace-only mutations — remove a trailing space, add a trailing space, collapse an interior double space, append a terminal `"\n"` — applied to a worker, a reviewer and a `final_reviewer` spec, each asserted to **fail** the golden; plus an assertion in the same test that `_normalize_artifact()` would have *accepted* three of them, demonstrating the new comparison is strictly stronger. 1b pins the no-terminal-newline fact. |
| **T-6** | Expanded from four bullets to name the byte comparison, the mutation test, the no-terminal-newline assertion and the capture-time tripwire. |
| **N.1 fixture block** | `"<normalized spec text>"` → `"<verbatim spec text>"`; new `"canonicalization": "task_spec/1.0"` key. |
| **Step 0** | Adds `NEUTRALITY_CANONICALIZATION`, `_TASK_SPEC_NONDETERMINISM_TRIPWIRES`, `canonicalize_task_spec()`; marks **HARD** that `_normalize_artifact()` is never called on a Task spec and is itself left unmodified. |

#### D-002 — clock-derived `generated_at` broke B5's byte-identity (G1, MAJOR, blocking)

**Finding.** E.5 emitted a top-level `generated_at` while T-4 weakened B5's rerun assertion to
"byte-identical apart from `generated_at`" — a test-level exception overriding an approved
criterion whose failure condition is *any* metric difference across identical inputs.

**Resolved by.**

| where | change |
|---|---|
| **E.5 schema** | `generated_at` **removed** from the metrics document. |
| **E.5** (new paragraphs) | The contract is stated positively: no key of the metrics document is derived from the clock, the environment, the process or argv; every value is a function of `--findings`, `--key`, `--adjudications`, `--workspace`, `--run-verdict` alone. Wall-clock provenance moves to a **separate sidecar** written only under the new `--provenance-out <path>` flag, carrying `generated_at`, `scorer_source_digest`, `argv` and a `metrics_digest` that ties it to the metrics bytes. The sidecar is outside B5 **by definition** — B5 is stated over the document written to `--out`, and the default invocation does not produce a sidecar at all — not by a test carve-out. |
| **E.1** | `score` gains `[--provenance-out <path>]`, documented as the only place the scorer may read a clock. |
| **E.4** | Closing paragraph no longer implies the claim covers only the algorithm; it now says E.5 keeps clock-derived values out of the serialized document so B5 holds **for the whole file, with no carve-out**. |
| **T-4** | The determinism bullet is **unqualified**: byte-identical metrics file, whole file, no excepted key, asserted in-process and across two subprocess invocations. Two new bullets: a structural no-clock test (patch `time.time`, `time.monotonic`, `datetime.now`, `datetime.utcnow` to raise; `score` must still exit 0 and emit the full block; the document must contain no `generated_at` and no ISO-8601-shaped value), and a sidecar test (separate path, matching `metrics_digest`, and passing `--provenance-out` changes the metrics bytes by zero bytes). |
| **B5 criterion row** (Baseline procedure) | Restated as byte-identical for the entire file with no excepted field, with the sidecar explicitly named as a different artifact outside the comparison. |

#### D-003 — the three-file writer could permanently orphan a dispatch (G2, MAJOR, blocking)

**Finding.** A.3 prechecked the three final paths then created them sequentially with
`open(..., "x")`. An `OSError` after `input.md` existed left a survivor that every later attempt
read as a permanent collision, so that dispatch could never acquire a complete record; and three
separate creates are not atomic with respect to each other.

**Resolved by.**

| where | change |
|---|---|
| **A.1** | The record unit is now a **directory** `final_review_audit/<dispatch_key>/` holding `record.json`, `input.md`, `report.md`, plus a sibling `final_review_audit/.staging/`. Rationale stated: the name that must become visible atomically has to be the rename target. |
| **A.3** (rewritten as P1–P7) | **P1 Stage** — `os.mkdir()` an exclusive `.staging/<dispatch_key>.<pid>-<nonce>/`, a sibling inside `final_review_audit/` so staging and target are on the same filesystem by construction (no `EXDEV`). **P2 Write/flush** — three `open(..., "x")` writes, each `flush()` + `os.fsync()`, then an fsync of the staging directory (best-effort wrapper). **P3 Publish** — one `os.rename(staging_dir, audit_dir / dispatch_key)`; `os.replace()` is deliberately *not* used, because rename's failure on an existing target **is** the immutability guarantee, obtained atomically instead of by a racing precheck; parent fsync after. **P4 Failure at any boundary** — `rmtree` the staging dir, raise `FinalReviewAuditWriteFailed`; no published directory can ever come into existence, which yields the **reader rule**: a published `<dispatch_key>/` directory *is* a complete record, existence and completeness are the same fact, and readers **MUST** skip `.staging/` (excluded twice: explicit name check, and A.2's grammar rejects a leading dot). Nothing under `.staging/` is parsed, digested, exported, counted, or allowed to answer a provenance question. |
| **A.3 P5** | **Retry for the same dispatch**, tabulated: no published directory (first attempt, or a previous attempt died mid-write) → the rename **succeeds** and the record publishes, so a prior crash costs nothing; already published → genuine `FinalReviewAuditCollision`, new staging removed, published record untouched. The D-003 failure mode is closed in both directions. |
| **A.3 P6** | **Abandoned staging state is explicit, not silent**: swept only when a published record for that key exists; otherwise **retained** as the sole surviving evidence and reported once per run as `final_review_audit_incomplete_publication`, and in the bundle's `integrity.incomplete_publications` with `files_present`/`files_absent`. That is the required "explicit recoverable incomplete state" rather than a permanent refusal. |
| **A.3 P7** | Unchanged posture restated: `_safe_log`-wrapped, never mutates settled lifecycle state, no `force`, no `--overwrite`, no update function, no write into a published directory. |
| **The shape in one picture** | Step [5] now reads *stage → publish*, naming the single rename and the EEXIST-is-a-collision rule. |
| **B.4 log rows** | `detail=record=final_review_audit/<key>/record.json`; collision row retargeted at an already-**published** record; two new events: `final_review_audit_write_failed` and `final_review_audit_incomplete_publication`. |
| **Error Handling table** | The single collision row is replaced by three: already-published collision; `OSError` at any staging boundary (nothing published, **the same key can still publish later**); and a killed-writer staging leftover (ignored by readers, retained, reported). |
| **T-1** (fault injection, as required) | A parametrized fault-injection test at **every** write boundary — staging `mkdir`, each of the three `open("x")`, each `write`, each `fsync`, and the publishing `rename` — asserting at each point: (a) no `final_review_audit/<key>/` exists; (b) provenance never reads `accepted` for it; (c) one `final_review_audit_write_failed` row and no lifecycle mutation; (d) **a subsequent write for the same dispatch key succeeds**. Noted explicitly that the old protocol fails (d) at three of those points, so the test is a real regression guard. Plus an abandoned-staging test covering "never counted / retained / reported / swept once published". Collision bullet updated to the published-directory semantics. |
| **T-2** (fault injection on the retry path) | A mid-staging failure followed by a retry under a new identity yields one complete published record for the retry, one retained `.staging/` entry for the original, reported and never read as a record, with provenance unaffected. Retry-independence bullet restated over published directories. |
| **Path ripple** | `<key>.record.json` / `<key>.input.md` / `<key>.report.md` → `<key>/record.json` etc. everywhere they appear: A.4 (`artifact_path` for input and report), C.5's digest table and the "redacted text and nothing else" paragraph, F.1's minimum evidence subset, F.2's bundle `input`/`report` paths, E.2's `source_report`, E.5's `findings_source`, D.6's scan-target table, T-1 and T-4's scan path. |
| **Other downstream reconciliation** | F.2 `integrity` gains `incomplete_publications`; F.3's "no deletion" policy is reconciled with P6 (the sweep touches only redundant `.staging/` scratch whose bytes already survive at the final path, and never a published record, log, bundle or run artifact) and gains a bullet on reporting incomplete publications; R-2's mitigation now cites the atomic publication; Step 1's I-1 constant list gains `FinalReviewAuditWriteFailed`, `FINAL_REVIEW_AUDIT_STAGING_DIRNAME`, `_stage_and_publish_audit_record()` and `sweep_final_review_audit_staging()`; Step 2's §9 SKILL.md row and the `validate_skills.py` row require the `.staging/`-is-never-a-record reader rule to be stated and validated; **Volume** notes staging has no steady-state cost. |

#### Not changed in iteration 2

D-A's other subsections, D-B, D-C, D-D, D-E's E.1–E.3/E.6, D-F's F.1/F.4, the Compatibility and
Migration sections, the Risks/Open Issues list and the Out-of-Scope list are unmodified except for
the path spellings and cross-references enumerated above. No PLAN DEC is reopened.
