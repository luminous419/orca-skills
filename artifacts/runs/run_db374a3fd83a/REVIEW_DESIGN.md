# Review Result

RESULT: FAIL
REVIEW_VERDICT: FAIL

## Summary

`artifacts/runs/run_db374a3fd83a/DESIGN.md` is a strong, largely implementable design for
Jira OS-30. It carries every `## Scope` bullet and all nine acceptance criteria of the
authoritative ticket, it holds the OS-31 line as a type/API boundary rather than as prose,
and almost every repository fact it asserts is true — I re-verified each one directly in
the worktree instead of accepting the Worker's summary. It also discharges REVIEW_PLAN
N-001 through N-004 substantively.

Three defects nevertheless block the design gate. Each is a concrete, verifiable
contradiction between the design and either the repository it must run in or the design's
own acceptance evidence — not a style preference or a generic best practice:

1. **F-001 (G4).** The response record's mandatory `redacted_preview` re-copies up to 200
   scalars of the user's answer after passing it through `run_logging.redact_text`, and
   §13 explicitly authorizes "redacted previews" into CLI output, ordinary logs, task
   specs and exported summaries. `redact_text` under `redaction/1.1` removes only five
   categories (dispatch capabilities, URL credentials, `NAME=`/`NAME:` secret
   assignments, and absolute paths); a bare secret in prose survives it verbatim. The
   design's own AC9 canary test — "exact bytes occur exactly once in the published raw
   file … and nowhere in stdout/stderr, JSON, … orchestration/timing logs, task specs,
   lineage summaries, exports" — therefore cannot pass against the design's own schema.
2. **F-002 (G1).** `decision_item_id = H(source_ledger_key)` combined with "publishes once
   per open source record" mints two decision items, two requests and two independently
   answerable decisions for ONE underlying question whenever a Reviewer agrees with a
   Worker's `NEEDS_INPUT`/`CONFLICT`. That ledger shape is not hypothetical: it is a
   supported, tested OS-29 state. This breaks the ticket's first Scope bullet, "stable
   decision/request identity."
3. **F-003 (G1).** `source_ledger_key` is validated against `^run_[a-z0-9]+/…`, which is
   strictly narrower than the run-id contract the design claims to inherit and rejects
   run ids this repository already uses. A fail-closed rejection of a legitimate OS-29 key
   produces no request artifact for a real `NEEDS_INPUT` block, which is AC1.

All three are fixable inside the design phase without changing the architecture, the
module boundary, the artifact layout, the port, or any numeric bound. Six non-blocking
notes follow. The quality profile is absent, so the judgement below rests only on explicit
requirements (Jira OS-30), the design phase contract, and the minimal general gate G1-G5;
no broad generic checklist was applied and no OS-31/transport/future-refactor concern was
promoted to blocking.

## Blocking Findings

```text
ID: F-001
Quality Attribute: G4
Severity: MAJOR
Blocking: YES
Location: DESIGN.md §6 (response record `redacted_preview`); §13 final paragraph;
          "Testing Strategy — Security and portability" first bullet
Issue: The mandatory `redacted_preview` field carries unredacted secret material, and
       §13 authorizes propagating previews into ordinary logs, CLI output, task specs
       and exported summaries. This is the exact channel AC9 exists to close, and it
       makes the design's own AC9 canary test unpassable.
Reason / Evidence: §6 requires, with no nullability marker and no sensitivity condition,
       `redacted_preview | run_logging.redact_text output, maximum 200 scalars`. I read
       the actual policy the design delegates to. scripts/run_logging.py:1094-1140
       defines REDACTION_CATEGORIES as exactly five entries:
         orca_dispatch_capability  \bdcap_[A-Za-z0-9_\-]{8,}
         url_credential            scheme://user:pass@
         env_secret_pattern        [A-Z0-9_]*(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|
                                   PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]* [=:] value
         absolute_local_path       /Users//home//root/<segment>
         foreign_absolute_path     every other absolute POSIX path
       and scripts/run_logging.py:1185-1219 confirms redact_text is exactly a pass over
       that tuple. A user answer such as `use sk-proj-AbCdEf0123456789` or a unique test
       canary `CANARY-9f3a...` matches NO category — there is no `NAME=`/`NAME:` form and
       no path — so it is returned byte-for-byte. `redacted_preview` is then a SECOND
       verbatim copy of up to 200 scalars of the answer, inside response `record.json`.
       That alone contradicts the design's own stated invariant of "one authoritative raw
       response copy" only in spirit, since record.json is 0600. The blocking part is
       §13's closing sentence: "Error construction, CLI output, ordinary logs, OS-29
       ledger, task specs, lineage details, and exported summaries receive
       identifiers/digests/redacted previews only." A "redacted preview" is, per the
       evidence above, not redacted for the class of content AC9 names. §13 therefore
       licenses copying secret bytes into ordinary logs, task specs, exports and stdout —
       and §11's guard ("`show` … never prints raw response bytes or sensitive custom
       values") does not exclude `redacted_preview`, which is literally neither.
       This is falsifiable against the design's own gate: the Testing Strategy asserts the
       canary appears "nowhere in stdout/stderr, JSON, exception text, OS-29 ledger,
       orchestration/timing logs, task specs, lineage summaries, exports, or package
       archives". A plain canary submitted via `--response-file` WILL appear in the
       response record.json. Schema and acceptance test cannot both hold.
       The design already owns the machinery to fix this and simply does not wire it:
       `raw.sensitivity` (normal|sensitive), request `sensitivity_guidance`, and
       `custom_decision.sensitive` all exist.
Required Action: Resolve the contradiction in DESIGN.md before implementation. Either
       (a) drop `redacted_preview` from the response record and let the digest plus
       identifiers carry the evidence, or (b) make it structural rather than textual
       (byte count, line count, matched-category counts from redact_text's second return
       value), or (c) define it as null/empty whenever `raw.sensitivity = sensitive` AND
       state that sensitivity defaults to `sensitive` when the request did not declare
       otherwise. Whichever is chosen, amend §13's final sentence so "redacted preview" is
       not on the list of things ordinary logs, CLI output, task specs and exported
       summaries may receive, and amend §11 so `show` is explicitly excluded from printing
       it. State in the Testing Strategy that the canary assertion covers response
       record.json, so the test proves the property rather than assuming it.
```

```text
ID: F-002
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: DESIGN.md §3 (`decision_item_id = "item_" + H("os30-item-v1\0" + source_ledger_key)`);
          §12 ("It publishes once per open source record"); §5 (dependency DAG rules)
Issue: One underlying question becomes TWO decision items, two requests and two
       independently answerable decisions whenever a Reviewer agrees with a Worker's
       blocking record. The ticket's first Scope bullet is "stable decision/request
       identity"; this identity is not stable across the OS-29 producer contract the
       ticket names as a Dependency.
Reason / Evidence: OS-29's ledger key is per-JUDGEMENT, not per-QUESTION.
       scripts/decision_gate.py:292-302 says so in its own docstring — "An identity for
       ONE judgement." scripts/decision_gate.py:516-556 `open_items()` keys the open set
       by `ledger_key(record)` for EVERY record that is blocking with
       `open_decision_item is True`, and its inner loop explicitly refuses to let a
       later blocking record resolve an earlier one: "A record that is itself blocking
       resolves nothing. Without this a Reviewer CONFIRMING a Worker's NEEDS_INPUT would
       read as having closed it."
       So a Worker B2 `NEEDS_INPUT` and an agreeing Reviewer B3 `NEEDS_INPUT` about the
       same `open_item` yield TWO open keys. That is not a hypothetical shape: it is a
       named, passing regression —
       scripts/test_decision_gate.py:397-419
       `test_a_worker_reviewer_agreement_never_resolves_an_open_item`, which builds
       `agreeing_reviewer = dict(self.worker_open, sequence=2, role="reviewer",
       boundary="B3", ...)` and asserts BOTH keys sit in `prior_open_decision_items`.
       DESIGN.md's own "Current Architecture" section concedes the traversal that reaches
       it: "an unresolved decision is terminal even after the one already scheduled
       verification Reviewer."
       Under §3 the two keys hash to two distinct `decision_item_id`s, and §12 publishes
       "once per open source record" — so the human is asked the same question twice.
       §5 makes it worse rather than better: the two items carry no declared dependency,
       so they are eligible to be co-bundled as INDEPENDENT (rules 4-5 are satisfied
       vacuously), the user may answer them differently, and the result is two effective
       decisions for one question with NO supersession link between them — §9's
       `decision_superseded` is same-item only ("prior and next decision IDs, both same
       item"), so the lineage reader cannot even detect the conflict, and its
       MULTIPLE_EFFECTIVE_HEADS check is per-item and will not fire.
       The design never mentions Worker/Reviewer agreement, deduplication, or a
       question-level identity distinct from the judgement-level key.
Required Action: Add an explicit rule to §3 and §12. The minimal repository-consistent
       form: derive `decision_item_id` from the (run, phase, `open_item`) tuple that
       OS-29 already carries as the question's own identity, and keep the ledger key as a
       one-way `source_ledger_key` binding — retaining the FIRST key that opened the item
       and recording later agreeing keys as additional evidence on the same item. State
       the deduplication rule in §12 as "publishes once per open QUESTION, not once per
       open record", and add a fixture to the Testing Strategy covering Worker B2 +
       agreeing Reviewer B3 asserting exactly one published request. If instead one item
       per record is intended, the design must say why asking the same question twice is
       correct and how two divergent answers are reconciled.
```

```text
ID: F-003
Quality Attribute: G1
Severity: MAJOR
Blocking: YES
Location: DESIGN.md §3 ("`source_ledger_key` is validated against
          `^run_[a-z0-9]+/[a-z][a-z0-9_]*/[0-9]+/(B2|B3)#[0-9]+$`"); §2
          ("`<run-id>` continues through `run_logging`'s existing run-root validation")
Issue: The run-id half of the pattern is strictly narrower than the run-id contract the
       design claims in §2 to inherit, and it rejects run ids this repository uses today.
       A fail-closed rejection of a valid OS-29 key means no request artifact is published
       for a genuine `NEEDS_INPUT` block, which is AC1.
Reason / Evidence: The repository's run-id contract is scripts/run_logging.py:336-364
       `_ensure_run_artifact_root`, which fails closed on exactly two things: an empty
       run_id, and one "containing a path separator or a bare `.`/`..` segment". It
       imposes no `run_` prefix and no character class. The design's §2 says OS-30 run
       ids "continue through" that validation, then §3 imposes `^run_[a-z0-9]+`, which
       forbids the underscore inside the id. The design is internally inconsistent, and
       the narrower half is the one that gates authority.
       Verified against real ids rather than argued in the abstract. `ledger_key` is
       `f"{run}/{phase}/{iteration}/{boundary}#{sequence}"` (decision_gate.py:292-302), so
       the run id appears verbatim. e2e_harness.py calls `run_logging.open_decision_ledger`
       (lines 703, 1959) and `append_decision_ledger_record` (line 1030) for every run, and
       scripts/test_e2e_harness.py already drives it with:
         run_e2e_blocking_attribute      -> FAILS ^run_[a-z0-9]+
         run_e2e_mixed_findings          -> FAILS
         run_e2e_quality_bugfix_negative -> FAILS
         run_from_scenario               -> FAILS
         run_mdup, run_tripwire          -> FAILS
         run_golden, run_a, run_b, run_fixture -> pass
       At runtime the id is not even this code's to choose: orca_runtime_harness.py:1577
       takes it from Orca — `self.run_id = created["result"]["run"]["id"]` — so the design
       is fail-closing on the format of an externally supplied identifier that no
       repository contract constrains.
Required Action: Widen the run-id component to match the contract §2 already cites — a
       single non-empty path segment that is neither `.` nor `..` and contains no path
       separator — or, if a tighter grammar is genuinely wanted, state it as a NEW
       repository run-id contract, place it next to `_ensure_run_artifact_root`'s rule,
       and account for the existing run ids above. The traversal-safety goal the pattern
       serves is fully met by the separator/dot-segment rule; the `[a-z0-9]` class adds no
       safety and removes valid inputs.
```

## Non-Blocking Findings

```text
ID: N-001
Quality Attribute: NONE
Severity: MAJOR
Blocking: NO
Location: DESIGN.md §13 (safe order steps 4-6) vs §6 (`decision_id`,
          `normalization_outcome`, `normalization_reason`) vs §3 (replay sentence)
Issue: The response record is published before the decision object, yet carries
       `decision_id`. The crash window between the two publications is unhandled, and
       §3's idempotency promise cannot hold inside it.
Reason / Evidence: §13 publishes "raw plus response JSON together" at step 4 and only at
       step 6 publishes "a decision/lineage event". Published objects are immutable — §2:
       "No published file is edited." So `decision_id` must be computed in memory before
       step 4; that is possible, since §3 derives it deterministically from `response_id`
       plus the canonical normalized payload. But if the process dies between step 4 and
       step 6, a published, immutable response names a `decision_id` whose directory does
       not exist. §3 then promises something unachievable: "Repeating the same submission
       ID with byte-identical raw content and identical actor/provenance returns the
       existing response/decision" — there is no existing decision to return. The wording
       also reads two ways: step 2 says "decode only for normalization" (normalization in
       memory, early) while step 6 says "only then normalize and publish a decision"
       (normalization last). Only the first reading is consistent with §6.
       This is non-blocking because effective state is replayed from DECISIONS, not from
       response pointers (§9), so a dangling pointer leaves the item unresolved rather
       than falsely resolved — the fail-closed direction. The gap is recovery, not safety.
Required Action: State in §13 that normalization is computed in memory before step 4 and
       that step 6 commits authority; and state the replay rule for a published response
       whose named decision is absent — the recommended form is that replaying the same
       submission ID with identical bytes/actor/provenance resumes at step 6 and publishes
       the (deterministic, therefore identical) decision. Add a crash-between-publications
       case to the Testing Strategy's idempotency bullet.
```

```text
ID: N-002
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §1 (`REQUEST_STATUS = current | stale`); §3 ("a non-current request");
          §6 (`stale` "derived at ingestion"); "Error Handling" (`STALE_REQUEST`)
Issue: `stale` gates whether a response may create a decision, but the rule that makes a
       request current or non-current is never stated.
Reason / Evidence: `REQUEST_STATUS` is declared as a closed enum in §1, yet no field in
       the §4 request record carries it, so it must be derived — and no derivation rule
       appears anywhere in the design. Two readings are available and they differ where it
       matters: (a) current = the highest revision in the item's reclarification chain, or
       (b) current = the request with no successor `reclarifies_request_id`. They coincide
       normally but not under partial publication. A third question is unanswered and
       matters for AC7: does a request stay current after it has produced an effective
       decision? It must, or the changed-answer path in §8 ("Supersession occurs only
       after a later response successfully produces a new decision") has no request to
       submit against — the `respond` CLI takes only `--request-id`.
Required Action: Add one sentence to §3 or §8 defining currency, and say explicitly that
       an effective decision does not make its request stale.
```

```text
ID: N-003
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §4 (`accepted_response_modes` "exact non-empty subset of `option_id`,
          `response_file`"); §11 CLI (`--cancel`); §1 (`RESPONSE_KINDS` includes `CANCEL`)
Issue: `CANCEL` is a first-class response kind with its own CLI flag, its own raw token
       and its own lineage event, but no corresponding member of the closed
       `accepted_response_modes` set.
Reason / Evidence: An implementer who validates the incoming response against the
       request's declared `accepted_response_modes` — the natural reading of a field whose
       purpose is to declare what the request accepts — rejects every `--cancel`
       submission, because no request can declare it. Conversely, if cancellation is
       deliberately outside that gate, nothing says so, and §9's `decision_cancelled`
       ("explicit CANCEL response required") depends on the path working.
Required Action: Either add `cancel` to the closed set, or state in §4 that
       `accepted_response_modes` constrains only answer submission and that cancellation
       is always available as a lifecycle instruction.
```

```text
ID: N-004
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §1 (import shim snippet); "Expected Changed Files" step 5
Issue: The shim snippet is not the pattern it claims to match, and the difference changes
       what the planned AST guard sees.
Reason / Evidence: §1 says the module imports run_logging "through the same dual-location
       pattern as `decision_gate.py`" and shows `from scripts import run_logging`.
       decision_gate.py:38-51 actually uses `from scripts.decision_policy import (...)`
       with a `from decision_policy import (...)` fallback — a submodule import, not a
       package-member import. The distinction is load-bearing for step 5's guard: the
       existing walker, scripts/test_os29_decision_gate.py:44-57 `imported_names()`,
       records `node.module.split(".")[0]` and `node.module.replace("scripts.", "")`. For
       `from scripts.decision_policy import ...` that yields `{"scripts",
       "decision_policy"}`; for `from scripts import run_logging` it yields only
       `{"scripts"}` — the name `run_logging` never appears from the primary import. Only
       the `except` branch's bare `import run_logging` puts `run_logging` in the set, and
       that name IS in SCRIPTS_MODULES (built at lines 39-41 from `SCRIPTS.glob("*.py")`),
       so the naive `imported & SCRIPTS_MODULES` check flags it. Step 5's allowlist
       handles that, but the "positive control proving it detects a forbidden `scripts.*`
       import" must be written against whichever form is actually used.
       Both forms work at runtime, so this is precision, not a defect.
Required Action: Optional — use decision_gate's submodule form
       (`from scripts.run_logging import ...` / `from run_logging import ...`) so the
       claim of pattern parity is literally true and the walker sees the sibling name, or
       keep the package-member form and say in step 5 which names the allowlist covers.
```

```text
ID: N-005
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §2 ("a new small generic helper in `clarification_protocol.py`,
          deliberately matching the existing directory-publication behavior")
Issue: A second implementation of this repository's atomic-publication scheme is
       introduced without the parity test the repository's own precedent attaches to
       exactly this kind of deliberate duplication.
Reason / Evidence: The decision to duplicate is correct and I verified why: the existing
       publisher `_stage_and_publish_audit_record` (run_logging.py:1835-1902) takes
       `files: dict[str, str]` and writes through `_write_staged_file` (line ~1827) with
       `open(path, "x", encoding="utf-8")` — text only, and it sets no modes. OS-30 needs
       byte-exact raw preservation and 0600/0700, so reuse is genuinely not available.
       What is missing is the repository's answer to duplication. run_logging.py:336-350
       duplicates `ensure_run_artifact_root` for the same install-boundary reason and its
       docstring names the guard: "`ArtifactRootParityTests` in test_run_logging.py checks
       the two stay behaviourally identical so this duplication cannot silently drift."
       The design's Testing Strategy has no equivalent for the two publishers, so a later
       fix to one (a new fsync boundary, a changed collision rule) can silently miss the
       other. Also minor: §6 describes `redacted_preview` as "`run_logging.redact_text`
       output", but that function returns `tuple[str, tuple[dict[str, int], ...]]`
       (run_logging.py:1185-1187), not a string.
Required Action: Optional — add a parity assertion between the two publication helpers to
       the Testing Strategy, in the shape ArtifactRootParityTests already establishes.
```

```text
ID: N-006
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Location: DESIGN.md §12 ("called only after a valid OS-29 B2/B3 blocking record has been
          durably appended and the terminal block reason is known"; "Integration occurs
          after B2/B3 settlement and before final `BLOCKED` logging/return")
Issue: The two clauses name two different code locations in
       scripts/orca_runtime_harness.py, and the design does not say which is the seam.
Reason / Evidence: The blocking record is durably appended and `_last_settled` advanced at
       the settlement sites (orca_runtime_harness.py:2469, 2475, 2510, 2559). The terminal
       block reason is not known there — it is computed later, inside `log_run_status`,
       at line 2703 via `decision_gate.unresolved_block_reason(...)`, on the path that
       converts a COMPLETED attempt into `log_run_status("BLOCKED", ...)` at line 2724.
       "After settlement" and "when the terminal block reason is known" are therefore not
       the same instant. Either seam is workable — the design's own architecture note
       ("an unresolved decision is terminal even after the one already scheduled
       verification Reviewer") establishes that publishing at settlement cannot be
       invalidated later — but the design phase owes the implementation one location.
       This finding interacts with F-002: at the settlement seam BOTH the Worker B2 and
       the agreeing Reviewer B3 records pass through, which is how the duplicate-item
       shape arises.
Required Action: Optional — name the single call site in §12 by its role (settlement, or
       the `log_run_status` block path), so step 3 of "Expected Changed Files" edits one
       seam rather than a choice of two.
```

## Test Review

No tests were written or changed in this phase; the phase is design-only and the tracked
tree is clean (`git status --short` shows only pre-existing untracked artifacts and the
untracked root-level `e2e_harness.py`, none of which this phase touched). I reviewed the
Testing Strategy as a design artifact and checked its claims against the real suites.

**What the strategy gets right.** It is unusually strong on the properties that matter
here. The AC-to-fixture mapping is concrete rather than aspirational: both producer states
(`NEEDS_INPUT` and `CONFLICT`), bundle sizes 1/3/4, the exact request fields the ticket
enumerates (`what_is_blocked`, literal `default_applicable: false`, literal timeout text,
recommendation plus rationale), the five normalization outcomes, revision exhaustion at 2,
and every lineage failure class (cross-run, self/missing target, fork, cycle, multiple
head, out-of-order, scope-reuse) each have a named case. The OS-31 exclusion is expressed
as a measurable zero-delta over an enumerated tuple rather than as an intention, which is
the right shape. The repository gate command list matches this repository's documented
gates, including `python3 -m unittest discover -s scripts -p 'test_*.py'`,
`validate_skills.py`, `verify_package.py` and `git diff --check`.

**Where it fails.** The security bullet asserts a property the schema contradicts. Its
canary claim covers "JSON" and "exports"; §6's mandatory `redacted_preview` puts up to 200
scalars of the canary into response `record.json`, and §13 authorizes previews into
ordinary logs, task specs and exported summaries. Since `redact_text` provably does not
match a bare canary (F-001 evidence), this test cannot pass as written against this
schema. A test that cannot pass is not coverage.

**Where it is silent.** Three gaps, each tied to a finding above:
- No case covers a Worker B2 blocking record followed by an agreeing Reviewer B3 record
  (F-002). This is the one shape that breaks item identity, and OS-29 already has a named
  regression proving the shape occurs
  (`test_a_worker_reviewer_agreement_never_resolves_an_open_item`).
- No case covers run ids outside `^run_[a-z0-9]+$` (F-003), which is why the narrowing
  would survive the suite: new OS-30 fixtures would naturally pick conforming ids.
- No case covers a crash between response publication and decision publication (N-001),
  the one window where the idempotency promise in §3 does not hold.

**Verified as feasible.** I confirmed the assertions the strategy depends on do not
collide with existing locks. Adding `scripts/clarification_protocol.py` widens
`SCRIPTS_MODULES` (test_os29_decision_gate.py:39-41) but breaks neither import-direction
assertion, since neither `decision_gate` nor `run_logging` will import it. The
`DispatchSiteCardinalityTests` source-substring counts (lines ~163-172) are over spellings
OS-30 does not introduce. `release_manifest.required_skill_paths()` (lines 76-88) is
indeed the single declaration point for a second installed tool, and `EXECUTABLE_FILES`
(line 42) is `{"scripts/fake_bin/codex"}` only, so the new tool ships 0644 — consistent
with the design's `python clarification_protocol.py …` invocation form.

## Evidence Checked

Authoritative sources:
- Jira OS-30 fetched live (`getJiraIssue`, cloud `luminous419`, status 할 일): its
  `## Scope` (7 bullets, 6 per-question sub-items), 9 acceptance criteria, `Dependencies`
  (OS-28 and the OS-29 `NEEDS_INPUT`/`CONFLICT` producer contract), and `Out of Scope`
  (durable resume engine, approval-UI integrations, org-specific option catalogs).
- Approved baseline: `ANALYSIS.md`, `REVIEW_ANALYSIS.md`, `REVIEW_ANALYSIS_iteration2.md`,
  `PLAN.md`, `REVIEW_PLAN.md` (N-001..N-004 re-read and checked against DESIGN.md).
- Phase policy: `orca-worker-reviewer-orchestration/reviews/common.md` and
  `reviews/design.md`.

Repository facts re-verified directly (design claims confirmed TRUE):
- `decision_gate.ledger_key()` exists and is public (decision_gate.py:292-302); its
  docstring already reserves supersession to OS-30.
- `CLOSED_LEDGER_RECORD_FIELDS` (line 191) and `OS30_RESERVED_FIELDS` (lines 196-205,
  exactly eight names) exist as described; `LEDGER_RECORD_SCHEMA_VERSION = 1` (line 61).
- `run_logging.redact_text` exists (line 1185); `FINAL_REVIEW_REDACTION_POLICY_VERSION =
  "redaction/1.1"` (line 896); `read_decision_ledger` (line 1955) provisions nothing and
  returns `_unreadable` sentinels rather than dropping records — the design's "typed
  unreadable sentinel" posture matches the existing reader.
- `scripts/run_logging.py` and `orca-worker-reviewer-orchestration/tools/run_logging.py`
  are byte-identical (`diff` clean).
- `release_manifest.required_skill_paths()` permits exactly one tool,
  `tools/run_logging.py` (lines 76-88); the loop skill has no `tools/` directory (`ls`).
- `scripts/e2e_harness.py` has exactly four B1 sites, annotated "B1 site 1".."B1 site 4"
  (lines 2041, 2067, 2253, 2347) and two `subprocess.run` agent sites — the design's
  topology claim is exact.
- `OrcaRuntimeHarness.__init__` (line 688) with `self._last_settled` (756) and
  `self._pending_verification` (763) in process memory only; `self.artifact_dir` is the
  existing artifact base the design's port would receive.
- `scripts/fixtures/decision_gate/invalid/record_carries_os30_supersession.json` exists
  and must stay invalid; `valid/worker_needs_input.json` is a B2/iteration-1/`run_fixture`
  record, so the design's "strict positive integer" iteration holds for B2/B3 (only the
  B1 run-entry record carries iteration 0).
- Python 3.11-3.13 support is real (INSTALL.md:43, README.md:728).
- `validate_skills.MIRRORED_DECISION_SEMANTICS_ANCHORS` (lines 796-801) is the home
  REVIEW_PLAN N-001 named, and the design's step 6 now names it explicitly.

Repository facts that CONTRADICT design claims (basis of the blocking findings):
- `REDACTION_CATEGORIES` (run_logging.py:1094-1140) covers five categories; a bare secret
  in prose is not among them. -> F-001.
- `decision_gate.open_items` (516-556) keys by per-judgement ledger key and explicitly
  refuses to let an agreeing blocking record resolve an earlier one;
  `test_decision_gate.py:397-419` proves the two-key shape. -> F-002.
- `_ensure_run_artifact_root` (run_logging.py:336-364) imposes no `run_` prefix and no
  character class; `test_e2e_harness.py` uses `run_e2e_blocking_attribute`,
  `run_e2e_mixed_findings`, `run_e2e_quality_bugfix_negative`, `run_from_scenario`,
  `run_mdup`, `run_tripwire`; `orca_runtime_harness.py:1577` takes the run id from Orca.
  -> F-003.

Not reviewed, deliberately: OS-31 resume/continuation, transports, and future refactors
are out of the phase contract and were not evaluated, promoted, or used as grounds for any
finding. No production code or artifact was modified by this review.

## Final Decision

FAIL. The design is close, and none of the three blocking findings touches its
architecture: the module boundary, the artifact layout, the deterministic-identity scheme,
the runtime-neutral port, the numeric bounds, and the OS-31 exclusion are all sound and
were verified against the real repository rather than accepted from the summary. But each
blocking finding is a checkable contradiction, not a preference.

F-001 is G4: the design mandates a field that carries unredacted secret bytes and, in §13,
authorizes copying it into ordinary logs, task specs, CLI output and exports — the precise
channel AC9 exists to close — and the delegated `redact_text` policy provably does not
remove that class of content. The design's own AC9 canary test cannot pass against its own
schema, so this is not a hypothetical exposure but one its acceptance evidence would have
caught in implementation at the cost of a rework of the response record.

F-002 is G1: "stable decision/request identity" is the ticket's first Scope bullet, and
under the specified identity rule a Worker B2 block plus an agreeing Reviewer B3 block —
a shape OS-29 supports and this repository already regression-tests — asks the human the
same question twice and can produce two effective decisions with no supersession link the
lineage reader can see.

F-003 is G1: a run-id grammar narrower than the contract §2 claims to inherit fail-closes
on legitimate OS-29 keys, including run ids in this repository's current tests and ids
supplied by Orca at runtime, so AC1's "`NEEDS_INPUT` produces a structured request
artifact" would not hold for those runs.

The six non-blocking findings are recorded for the Worker's judgement and do not affect
this gate; N-001 is MAJOR in impact but fails closed in the safe direction and so is not
blocking. The quality profile is absent, so no tier-2 attribute was applied, and no
generic best practice, style preference, or speculative extensibility concern was used as
grounds. Fixing F-001 through F-003 requires edits to §3, §4, §6, §12 and §13 plus three
added test cases; it requires no change to the design's structure.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: The verdict is fully determined by the authoritative Jira OS-30 text, the
approved ANALYSIS/PLAN baseline and their reviews, the design phase contract, the
profile-absent minimal general gate (G1-G5), and repository source evidence read directly
in the worktree during this review. Every blocking finding rests on a repository fact that
contradicts a design statement, not on a judgement call. No user-owned choice arose: the
required corrections are reversible, repository-local, and already determined by existing
repository contracts.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B3",
  "evidence": {},
  "grounds": "The authoritative Jira OS-30 ticket, the approved analysis and plan baselines, the design phase contract and directly re-verified repository source evidence fully determine this review verdict; the three blocking findings each rest on a repository fact that contradicts a design statement, and no user-owned choice is open.",
  "iteration": 1,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "design",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T09:40:00+00:00",
  "responsible_phase": "design",
  "role": "reviewer",
  "run": "run_db374a3fd83a",
  "scope": "Reviewer verdict for design iteration 1 of Jira OS-30 only, verifying requirement coverage, exact implementability against the real repository, safety, completeness, compatibility and testing strategy, excluding OS-31, transports and future refactors.",
  "sequence": 7,
  "source": "reviewer",
  "source_binding": "artifacts/runs/run_db374a3fd83a/REVIEW_DESIGN.md",
  "state": "CLEAR",
  "verdict": "FAIL",
  "verifies": null
}
```
