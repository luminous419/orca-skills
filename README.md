# Orca Skills

Reusable Orca skills for structured software-development workflows.

The current pre-1.0 version is defined only in [`VERSION`](VERSION). Compatibility
and verification status are maintained in [`COMPATIBILITY.md`](COMPATIBILITY.md).

## Available Skills

### `orca-worker-reviewer-loop`

Direct-session implementation of the 2-agent Worker → Reviewer → PASS/FAIL loop.

- Creates/uses separate Orca sessions for Worker and Reviewer.
- Does **not require** Orca built-in orchestration state.
- Good baseline when simple direct terminal/session control is preferred.

### `orca-worker-reviewer-orchestration`

Orca-native implementation of the same 2-agent development policy.

- Uses Orca built-in `orchestration` as the execution/state-tracking layer.
- Uses built-in `orca-cli` for Orca terminal lifecycle control when custom agent commands must be launched or prompted directly.
- Requires real Run / Task / Dispatch provenance.
- Loads version-matched guides with `skills get orchestration` and, when terminal control is needed, `skills get orca-cli`.
- Uses supervised completion (`worker_done`/escalation/wait) according to the current Orca runtime contract.
- Creates the phase Task graph before dispatching the Worker, so the Reviewer Task becomes ready through normal dependency promotion instead of a manual status override.
- Accounts for every settled Dispatch on four separate axes — settlement, supervised worker-resource registration, residual process liveness, and cleanup authority — and finalizes each Dispatch exactly once.
- Runs an implicit Final Adversarial Review gate after every requested phase set: a fresh Reviewer session per attempt reviews the whole final tree, and `STATUS: COMPLETED` is reachable only through its PASS.
- Intended for comparison/PoC before deciding whether it should replace the direct-session loop.

## Shared Development Model

Both skills support the same phase vocabulary:

```text
Sequential:
ANALYSIS → PLAN → DESIGN → IMPLEMENTATION → TEST

Specialized:
BUGFIX
REFACTORING
```

Both enforce the same core policy:

```text
Worker
  ↓
Reviewer
  ↓
PASS → next phase / complete
FAIL → Worker correction → Reviewer re-review
```

Important gates include:

- Worker and Reviewer must differ.
- Agent values must be simple PATH-resolved command tokens; paths, arguments, and shell syntax are rejected.
- Sequential phase order is validated.
- Specialized phases (`BUGFIX`, `REFACTORING`) are not silently mixed into arbitrary sequential combinations.
- Unsupported specialized phase combinations are blocked with `UNSUPPORTED_PHASE_COMBINATION`.
- Explicit `phases=` conflicting with natural-language phase requests is blocked.
- IMPLEMENTATION requires Unit Tests.
- BUGFIX requires a Regression Test.
- Reviewer never edits/fixes its own findings.
- `max-iterations` limits repeated review loops.
- `risk=low|medium|high` (orchestration skill only) selects validation strength; an invalid or explicitly empty value is blocked with `INVALID_RISK`.

## Risk-Based Workflow

`orca-worker-reviewer-orchestration` takes a second, independent axis beside `phases`:

```text
phases   WHAT to execute            (unchanged semantics)
risk     HOW STRONGLY to validate   (low | medium | high, default high)
```

`risk` never expands or contracts the requested phase set, and the Final Adversarial
Review is mandatory and identical at every level.

| | **LOW** | **MEDIUM** | **HIGH** (default) |
|---|---|---|---|
| per requested phase | Worker only | Worker → Reviewer, bounded correction loop | Worker → Reviewer, full existing strength |
| phase Task graph | Worker node only | Worker + dependent Reviewer | Worker + dependent Reviewer |
| phase gate | the Worker's own result | the Reviewer verdict | the Reviewer verdict |
| downstream revalidation (T5a) | no-op | no-op | runs |
| Final Adversarial Review | mandatory | mandatory | mandatory |
| Final Review FAIL routing | Worker correction → fresh Final Review | correction → phase Reviewer → fresh Final Review | correction → phase Reviewer → T5a → fresh Final Review |

```text
/orca-worker-reviewer-orchestration risk=low phases=implementation <request>
```

Omitting `risk` resolves to `high`, so every pre-existing invocation behaves exactly as
before. An explicit value always overrides the default; there is no natural-language
inference, so the selection source is only ever `explicit` or `default`. A value that is
not a level — **including an explicitly empty `risk=`**, which is an explicit parameter
with no valid value rather than an omission — fails closed before any Run, Task or
Dispatch is created:

```text
STATUS: BLOCKED
REASON: INVALID_RISK
```

**Churn.** For identical requested phases, `LOW < MEDIUM` always strictly, and churn is
monotonic: `LOW <= MEDIUM <= HIGH`. `MEDIUM < HIGH` holds strictly in exactly one
situation: a Final Adversarial Review FAIL routes a correction to some requested phase
`p`, and the set of requested phases after `p` in canonical order is non-empty, so
section 17's T5a downstream revalidation actually produces extra dispatches — that is
HIGH's only churn-producing mechanism beyond MEDIUM. Everywhere else `MEDIUM == HIGH`,
which is documented behavior, not a defect:

- Final Review passes cleanly, whether or not a phase-local Reviewer FAIL/correction
  happened along the way — that loop is identical at MEDIUM and HIGH and by itself
  produces no extra HIGH churn.
- A Final Review correction is routed to the last requested phase in canonical order,
  so the downstream set is empty.
- Every BUGFIX/REFACTORING run — specialized phases have no canonical order, so their
  downstream set is always empty.

**Safety floor.** Risk changes validation strength, never the safety floor. The section 14
gates (IMPLEMENTATION unit tests, BUGFIX regression test, REFACTORING behavior
preservation) hold at every level. At MEDIUM/HIGH the phase Reviewer enforces them; at LOW,
where none exists, the Worker must carry an affirmative `UNIT_TEST_STATUS: PASS` — a
missing or `BLOCKED` value does not pass the gate.

**Independence.** The Risk Profile and the Project Quality Profile are two separate,
non-interacting resolutions. Neither reads the other's configuration or gates on the
other's state, and they reach agents as two separate blocks in the dispatched Task spec.

`risk` is orchestration-only. `orca-worker-reviewer-loop` has no risk axis and is
unaffected.

## Run-Scoped Artifacts and Logs

Every orchestration run writes everything it produces under one directory:

```text
artifacts/runs/<run-id>/
```

Phase and review artifacts (`IMPLEMENTATION.md`, `REVIEW_TEST.md`, `FINAL_REVIEW.md`, ...) live there,
and so do two Coordinator-owned logs:

| file | contents |
| --- | --- |
| `ORCHESTRATOR_LOG.md` | one row per lifecycle event: run start/end, every Worker/Reviewer/Final-Review dispatch settlement (phase, role, iteration, Task ID, Dispatch ID, terminal, created-vs-reused, the reviewer's own PASS/FAIL gate result and separate four-valued PASS/PASS WITH NOTES/FAIL/BLOCKED review verdict, outcome, lifecycle/cleanup result), the run's selected `risk` and `risk_source`, its `requested_phases`, each dispatch's `round_kind` (`phase_gate`/`correction`/`downstream_revalidation`/`final_review`), `reviewer_gate_skipped` rows for a LOW run's skipped phase gates, unexpected exits, and pre-dispatch failures |
| `TIMING_LOG.md` | one row per timed event: run start/end with wall-clock duration, phase/iteration start/end boundaries, and each dispatch's start/end/duration, each carrying the `risk` it was produced under |
| `final_review_audit/<dispatch_key>/` | one immutable directory per Final Adversarial Review **dispatch**: `record.json` (identity, provenance, digests, failure metadata, explicit `schema_version`), `input.md` (the retained redacted Task spec Orca stored for that dispatch) and `report.md` (the retained redacted review snapshot). `dispatch_key` is `attempt<N>__<task_id>__<dispatch_id or nodispatch>` |
| `FINAL_REVIEW_EVIDENCE_BUNDLE.json` | an **opt-in** self-contained export of the above plus this run's `ORCHESTRATOR_LOG.md`, content inlined, every artifact carrying both its recorded and its recomputed digest |

Both are append-only markdown tables, auto-created on first write (`scripts/run_logging.py` owns the
format) — a run that never reaches a given event simply never gets that row, and neither file requires
a separate opt-in. `OrcaRuntimeHarness` (the Python path that drives real Orca) calls
`scripts/run_logging.py`'s functions directly; a Coordinator driving `orca` by hand from the shell calls
the same logic through `python3 <SKILL_DIR>/tools/run_logging.py orchestrator-event|timing-event|run-status
...`, where `<SKILL_DIR>` is wherever `orca-worker-reviewer-orchestration` is actually installed (global,
project-local, or this repository's own checkout) — the installed Skill ships its own byte-identical copy
of this module at `orca-worker-reviewer-orchestration/tools/run_logging.py` (parity enforced by
`scripts/validate_skills.py`) precisely because a global/project-local install never copies this
repository's `scripts/` directory. See the orchestration skill's "Run-scoped orchestration and timing
logs" section for the exact call points. The final `run-status` row records one of `COMPLETED`,
`BLOCKED`, `ERROR`, or `ESCALATED`.

### Final Review audit records

`final_review_audit/` answers a question the two logs cannot: **what was the Final Adversarial
Reviewer actually handed, and what did it actually produce?** The logs are authoritative for run
lifecycle provenance; these records are authoritative for an attempt's *content*, and
`FINAL_RESULT.md` is a summary that references both. Where a summary and a record disagree, the
record wins and a reader has to say so rather than reconcile silently.

Four properties are worth knowing before reading one:

- **A published directory is a complete record.** Three files are staged and fsynced, then
  published with a single `os.rename()`, so the name only appears once everything is on disk.
  There is no "is it finished yet?" heuristic, and readers ignore `final_review_audit/.staging/`
  entirely. A publication that never completed is *retained* as the only evidence of that attempt
  and reported, never silently dropped.
- **A record is written once and never edited.** A retry has a new Task/Dispatch identity and
  therefore a new `dispatch_key`; the writer refuses to overwrite, and there is no force flag,
  no update function and no code path that writes into a published record.
- **Provenance is fail-closed.** `provenance_state` is `accepted | voided | unknown`, and no
  default anywhere is `accepted`. A missing file, an unparseable one, a missing required field and
  an unknown schema MAJOR all read `unknown`. A `voided` record — with one of six `void_reason`
  values — is never returned as a verdict, and two `accepted` records in one attempt is reported
  as a contract violation rather than resolved by picking a winner.
- **Retained artifacts are secret-safe.** Both `.md` files pass through a versioned redaction
  policy (dispatch capability tokens, URL credentials, secret-named environment values, and the
  user-name segment of an absolute path). The raw bytes never touch disk; only the digests survive,
  and the record carries the pre-redaction digest, the policy version, the post-redaction artifact
  digest and per-category substitution counts — never a substituted value or its offset.

Writing and reading these is the same shared writer the logs use:

```text
python3 <SKILL_DIR>/tools/run_logging.py final-review-audit-write --run-id <run-id> --attempt <n> \
    --task-id <task_id> [--dispatch-id <id>] [--provenance accepted|voided|unknown] ...
python3 <SKILL_DIR>/tools/run_logging.py final-review-audit-provenance --run-id <run-id> --attempt <n>
python3 <SKILL_DIR>/tools/run_logging.py final-review-audit-export --run-id <run-id> [--out <path>]
```

Adding these records changed **no** byte of what a Reviewer is dispatched, and that is checked
rather than asserted: `scripts/fixtures/os22_neutrality/pre_os22_task_specs.json` is a golden
capture of every Task spec this repository renders, taken before the feature existed, and the
comparison is on encoded bytes.

`artifacts/runs/` is deliberately not gitignored — these records *are* the evidence — and nothing
here stages or commits them for you. Nothing is ever deleted, compacted or garbage-collected.

### Evaluating a Final Review

`scripts/fixtures/final_review_eval/` holds a small subject project in two states with a written
contract on both sides, and `scripts/final_review_eval.py` materializes a reviewable workspace
from it, verifies the fixture against its own key, scans for key material leaking into a
reviewer's scope, parses a review into normalized findings, and scores it.

The metric contract refuses to overclaim. Recall over the key's entries is always computable and
always carries its numerator, denominator and population. A finding that matches nothing is
`UNADJUDICATED` — never, by any flag or path, an automatic false positive — and precision and
false-positive rate are `REFUSED` with a machine-readable reason until a human adjudicates the
unmatched findings or signs a closed-world attestation. The metrics document contains no
clock-derived value, so identical inputs produce byte-identical output.

Out of scope here: retention/archival of old runs' logs (OS-8) and richer analysis — bottleneck
detection, aggregate metrics, dashboards (OS-7). This is raw evidence, not a report.

## Agent Profile

A named Agent Profile decides **who** executes each phase. It does not decide what runs
(`phases`), how strongly the work is reviewed (`risk`), or what counts as PASS (the project
quality profile). Selecting a profile cannot turn a review off, add a phase, or make a
failing gate pass.

```text
/orca-worker-reviewer-orchestration profile=diverse phases=design,implementation <request>
```

Both skills support it. They read the same file and the same resolution rules, and differ
only in which routing keys they can consume.

### Location and schema

Profiles are read from two places, highest precedence first:

```text
<project>/.orca/agent-profiles.yaml
~/.orca/agent-profiles.yaml
```

A name present in both is taken from the project-local file **as a whole definition**.
Fields are never merged across the two files, so the profile you can read in one place is
the profile that ran. `.orca/agent-profiles.example.yaml` in this repository is a starting
point.

```yaml
version: 1

profiles:
  diverse:
    defaults:
      worker: claude
      reviewer: codex
    phases:
      implementation:
        worker: codex
        reviewer: codex
    final_review:
      reviewer: codex
```

`phases` may name any of the seven workflow phases — the same seven the skills already
support. Declaring routing for a phase does not run that phase; `phases=` alone decides
that.

### Resolution

Three chains, and the phase roles disagree with the final reviewer about which source wins
first. That asymmetry is intended: a profile that names a final reviewer means it, while an
explicit `reviewer=` on the command line is about the phase reviewers.

```text
phase worker    explicit worker=      > phases.<phase>.worker   > defaults.worker
phase reviewer  explicit reviewer=    > phases.<phase>.reviewer > defaults.reviewer
final reviewer  final_review.reviewer > explicit reviewer=      > defaults.reviewer
```

A selected profile is a self-contained resolution domain. It never borrows a missing field
from another profile or from the defaults a profile-less run would have used.

### Which roles must resolve

Risk decides, and the answer differs per skill:

| role | orchestration | loop |
|---|---|---|
| phase Worker | required at every risk | required |
| phase Reviewer | required at MEDIUM/HIGH, optional at LOW | always required |
| final reviewer | required at every risk | not applicable |

So a profile defining only a Worker and a final reviewer is valid for a LOW-risk
orchestration run, and never valid for the loop skill. This computation *reads* the settled
phases and risk; it changes neither.

### What is checked, and when

Everything happens before a Run, a Task or a Dispatch exists:

```text
1. read the profile sources and validate the schema   (no command is judged here)
2. materialize routing for the requested phases and the final review
3. token -> allowlist -> PATH, over the REQUIRED roles only
4. check that every required role resolved
5. only then create the Run
```

Step 3 deliberately checks required roles and nothing else. A command in a role this run
will never dispatch — a phase outside the request, a LOW-risk phase Reviewer, the loop
skill's `final_review.reviewer` — is not checked and cannot block the run. That leaves no
gap in the trust boundary, because the required set is exactly the set of commands the run
can execute.

### Missing vs. malformed

```text
omitted            legacy behaviour, unchanged. The profile files are not read at all.
profile=<unknown>  UNKNOWN_AGENT_PROFILE. `profile=` with no value is here too --
                   an empty value is an explicit wrong answer, not an omission.
malformed schema   INVALID_AGENT_PROFILE. Unknown or duplicate keys, an unknown phase,
                   an unsupported version and a non-string command all land here.
unresolved role    AGENT_ROLE_UNRESOLVED.
```

Every one of these blocks before any Run exists, in the same shape as `INVALID_PHASE` and
`INVALID_RISK`. None of them is a correction-loop input.

### Run-scoped and audited

Routing is materialized once and is immutable for that run. Editing the profile file
mid-run changes nothing: corrections, re-reviews and downstream revalidation all read the
same resolution.

Both skills record what they resolved — the profile name and source, the requested phases,
each resolved command and where it came from. Optional roles are recorded too, because
"not dispatched at this risk level" is a statement about the lifecycle, not permission to
leave it out of the record. Nothing but command tokens and enumerations is written, so no
secret, credential or environment value can reach the evidence.

A run without `profile=` records none of this and renders no routing block, because its
output must stay identical to a run from before the feature existed.

## Project Quality Profile

The Reviewer gate is not a broad generic software-quality checklist. A verdict is
decided profile-first, in four tiers:

```text
1 explicit user/project requirements
2 applicable project quality profile attributes
3 current phase contract
4 minimal general gate
```

A concern outside those four tiers is never promoted to a blocking finding. Generic
best practice, naming taste, minor duplication, documentation polish, speculative
extensibility and stylistic refactoring suggestions are not grounds for `FAIL` unless
the project's own profile declares them blocking.

### Location and schema

The profile lives at `.orca/quality-profile.yaml` in the repository being worked on.
`.orca/quality-profile.example.yaml` in this repository is a generic starting point;
copy it and replace every attribute with your project's own.

```yaml
version: 1

quality_attributes:

  - id: DOMAIN-001
    category: business-domain
    name: Idempotent processing
    blocking: true
    applies_to:
      - design
      - implementation
      - test
    description: >
      Reprocessing the same input must not produce a duplicate side effect.
    verification:
      - code
      - tests

  - id: TEAM-001
    category: team-convention
    name: Repository convention
    blocking: false
    description: >
      Keep the existing repository structure and naming convention.
```

| field | meaning |
| --- | --- |
| `version` | schema version; only `1` exists, and any other value is an error |
| `id` | unique within the profile |
| `category` | `business-domain`, `platform-infrastructure`, `team-convention`, or `operational-risk` |
| `name` | short label |
| `blocking` | a real boolean; a violation of a `true` attribute fails the gate |
| `description` | optional prose |
| `applies_to` | optional list of phases; omitted means **all applicable workflow phases** |
| `verification` | optional list of where the evidence comes from |

`applies_to` accepts `analysis`, `plan`, `design`, `implementation`, `test`, `bugfix`
and `refactoring`. A `design`-only attribute is not handed to the ANALYSIS Reviewer at
all, so nobody is asked to evaluate a rule that does not apply yet. `bugfix` and
`refactoring` follow the same rule as every other phase. The Final Adversarial Review
receives every attribute applicable to any requested phase, because it re-checks the
requested workflow as a whole.

Deliberately **not** in scope: profile inheritance, remote profiles, organization-wide
profiles, merge hierarchies, dynamic rule engines, and LLM-generated profiles.

#### YAML subset

`quality-profile.yaml` is parsed by a small, stdlib-only, restricted-subset parser
(`scripts/quality_profile.py`), not a general-purpose YAML library — this repository
depends on nothing outside the standard library. It supports `key: value` mappings,
block sequences (`- item` and `- key: value` list-of-mappings), inline flow sequences
(`applies_to: [design, implementation, test]`), quoted scalars, and `>`/`|` block
scalars. It refuses anything it does not understand — including YAML features it does
not implement, such as nested `[...]`/`{...}` inside an inline list, anchors/aliases,
or multi-document files — rather than guessing at a partial parse.

### Minimal General Gate

The general layer is intentionally small and stays five categories:

```text
G1 explicit requirement violation
G2 result does not work
G3 severe regression
G4 data loss / security / irreversible side effect
G5 missing validation evidence
```

### Severity is not blocking

```text
Severity  how much impact a finding has          CRITICAL | MAJOR | MINOR
Blocking  whether it must fail this gate         YES | NO
```

Severity alone never fails a gate. `Blocking: YES` holds only when the violated
quality attribute is `blocking: true`, or when the finding violates G1-G5. A finding
with `Quality Attribute: NONE` is always `Blocking: NO`.

```text
ID: F-001
Quality Attribute: DOMAIN-001
Severity: MAJOR
Blocking: YES

ID: N-001
Quality Attribute: NONE
Severity: MINOR
Blocking: NO
Required Action: Optional improvement
```

### Verdicts

A review report has four verdicts; the workflow gate has two values.

| report verdict | meaning | gate |
| --- | --- | --- |
| `PASS` | no blocking violation, no substantive note | `RESULT: PASS` |
| `PASS WITH NOTES` | no blocking violation, one or more non-blocking findings | `RESULT: PASS` |
| `FAIL` | at least one blocking violation | `RESULT: FAIL` |
| `BLOCKED` | required project information or evidence is missing | `RESULT: FAIL` |

`PASS WITH NOTES` and `BLOCKED` are report annotations, not new orchestration
lifecycle states. Task settlement, the FAIL correction loop, downstream revalidation
and the Final Adversarial Review trigger are unchanged by them. `BLOCKED` is a `FAIL`
whose Required Action is to supply the missing information or evidence.

Non-blocking findings alone never start a correction loop, in a phase review or in the
Final Adversarial Review.

### Missing vs. malformed

These two are never conflated:

- **No profile** — meaning the path does not exist — is a normal state. The review
  then uses explicit requirements, the current phase contract and the Minimal General
  Gate, and does *not* restore the old broad generic checklist.
- **A profile that exists but does not validate** is not a normal state. It is a
  validation failure *before dispatch*: the Coordinator resolves the profile right
  after binding the Run and before creating the first Task, and reports

  ```text
  STATUS: BLOCKED
  REASON: INVALID_QUALITY_PROFILE
  ```

  No Task is dispatched, so there is no path back to the generic checklist to fall
  onto. Duplicate attribute ids, a non-boolean `blocking`, an unknown `applies_to`
  phase, an unknown category, unknown keys, an unsupported `version` and unparseable
  YAML all resolve to this state — and so does a path that exists but is not a
  readable regular file, such as a directory or a broken symlink sitting at
  `.orca/quality-profile.yaml`. Only genuine nonexistence is `absent`.

### What the agents actually receive

The applicable attributes are filtered per phase and rendered into the dispatched Task
spec itself, for the Worker as well as the Reviewer, from one resolution:

```text
=== QUALITY GATE (profile-first) ===
profile_status: loaded
profile_path: .orca/quality-profile.yaml
applicable_quality_attributes: DOMAIN-001 [business-domain] blocking: Idempotent processing
blocking_quality_attributes: DOMAIN-001
general_gate: G1 explicit requirement violation || ...
decision_priority: 1 explicit user/project requirements || ...
non_blocking_by_default: ...
verdict_semantics: ...
=== END QUALITY GATE ===
```

Telling only the Reviewer would buy correction rounds for rules the Worker was never
given; building both blocks from one resolution is what keeps the two roles from being
judged against different specs.

That resolution is read **once per run**, at the run boundary, and the same immutable
object is threaded through every Worker, phase Reviewer, correction, downstream
revalidation and Final Reviewer spec of that run. Re-reading the file per attempt
would mean a profile edited while a Worker was running handed that Worker's Reviewer a
different quality model — the same divergence, arriving by a slower route.

## Quick Examples

Direct-session version:

```text
/orca-worker-reviewer-loop phases=design,implementation

아래 기능을 설계하고 구현해줘.
```

Orca-native orchestration version:

```text
/orca-worker-reviewer-orchestration phases=design,implementation

아래 기능을 설계하고 구현해줘.
```

Company environment:

```text
/orca-worker-reviewer-orchestration \
worker=claude-glm \
reviewer=claude-gemma \
phases=analysis,plan,design

<request>
```

Personal model-pinned wrappers:

```text
/orca-worker-reviewer-orchestration \
worker=claude-opus \
reviewer=codex-sol \
phases=analysis,plan,design

<request>
```

Generic CLIs:

```text
/orca-worker-reviewer-orchestration \
worker=claude \
reviewer=codex \
phases=analysis,plan,design

<request>
```

`claude` and `codex` are generic entry points. The Skill does not select or guarantee
their models; model selection belongs to each CLI's current configuration. For a stable
model choice, place a model-pinned wrapper such as `claude-opus` or `codex-sol` in a
directory on PATH and pass its command name. The Skill treats the wrapper as an opaque
executable and does not inspect its implementation or vendor-specific model syntax.
The Skill launches only that executable token and appends no model, permission, or
vendor-specific arguments. Put any required flags in CLI configuration or the wrapper.

Agent values must match `[A-Za-z0-9._-]+` and resolve on PATH. Custom wrappers must use
a trusted `claude-` or `codex-` prefix; PATH-resolved shells and interpreters such as
`bash`, `sh`, and `python3` are not agent commands. Do not pass arguments, shell
fragments, absolute paths, or relative paths in `worker=` or `reviewer=`.

Defaults:

```text
worker=claude-glm
reviewer=claude-gemma
max-iterations=5
```

## Which Skill Should I Use?

Use `orca-worker-reviewer-loop` when you want the simplest direct-session behavior or need a baseline for comparison.

Use `orca-worker-reviewer-orchestration` when you want Orca-native task/dispatch provenance, supervised worker completion, explicit orchestration state, and stronger lifecycle tracking.

The orchestration variant intentionally does **not** silently fall back to the loop variant. If Orca orchestration is unavailable, it reports BLOCKED so the execution model remains observable.

## Installation

See [`INSTALL.md`](INSTALL.md).

## Validation

Run the repository validator from the repository root:

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/verify_package.py
git diff --check
```

The validator uses only the Python standard library and validates both
`orca-worker-reviewer-loop` and `orca-worker-reviewer-orchestration`. It checks:

- `SKILL.md` YAML frontmatter
- required phase templates and review policies
- phase routing against the files on disk
- identical shared `templates/` and `reviews/` content across both skills
- absence of user-specific absolute paths
- required error codes, test gates, and the `max-iterations` range
- the orchestration-only lifecycle accounting contract block, including the never-close and close-eligible terminal role sets
- the orchestration-only final adversarial review contract block, its section 17 prose anchors, and the rule that its worker-resource outcomes never include `reuse`

The command exits with status `0` when all checks pass and a non-zero status with
actionable error messages when an inconsistency is found.

The regression tests run the validator against disposable repository copies and
verify that a valid repository passes while policy/prose drift, a missing required
error code, and shared-template drift are rejected. They also reject drift in the
lifecycle accounting contract: a dropped `unsupervised` outcome, a routine
force-ready policy, a missing custom-command placement step, a missing cleanup
authority axis, a close gate that no longer requires a close-eligible terminal
role, `coordinator_session` removed from the never-close roles, a finalization
contract that no longer requires the gate to run before the lifecycle action, and
the orchestration-only contract copied into the loop skill.

The policy smoke tests load the identical `policy-contract` JSON embedded in both
`SKILL.md` files and evaluate representative invocations without launching Orca
or an agent. They cover help mode, agent gates, iteration bounds, phase ordering,
unknown phases, phase conflicts, specialized combinations, explicit/default
parameter priority, representative natural-language phase terms, valid paths,
and cross-skill decision parity. Natural-language Worker, Reviewer, and
max-iterations instructions—as well as free-form phase requests outside declared
terms—remain Coordinator/LLM responsibilities rather than deterministic parser
behavior.

The fake-agent E2E tests start deterministic Worker and Reviewer subprocesses,
not Orca or an LLM. A minimal single-phase harness verifies bounded
Worker → Reviewer iteration, PASS/FAIL transitions, escalation, blocked and
malformed/exit handling, finding continuity, Reviewer artifact immutability,
and equivalent shared-policy results for both skills. A multi-phase workflow
harness additionally drives the orchestration-only Final Adversarial Review
gate, its responsible-phase routing, and its two independent iteration
counters. All workspaces and
protected production fixtures are disposable temporary directories.

### Orca runtime integration

The default suite remains offline and skips the real-runtime integration test.
To exercise actual Orca Run/Task/Dispatch lifecycle state with the deterministic
fake agents, first register this checkout with Orca if needed, then run:

```bash
orca repo add --path "$PWD" --json
python3 scripts/test_orca_runtime.py --orca-runtime \
  --artifact-dir artifacts/orca-runtime/latest
```

The integration command resolves the installed Orca CLI, loads its
version-matched `orchestration` and `orca-cli` guides, and requires a ready Orca
runtime. It never launches `claude-glm`, `claude-gemma`, Codex, or another LLM.
The current adapter is compatibility-gated to Orca `1.4.184` and exact required
guide grammar; a different version or changed command contract is skipped before
any Run or terminal is created. The resolved executable is passed into every
fake-agent lifecycle call, including `ORCA_CLI_COMMAND` overrides.
It tries supervised attachment first and, when the runtime classifies the custom
fake executable as unrecognized, uses the guide's tracked-Dispatch plus terminal
prompt fallback. One reviewer terminal is deliberately reused; all other fake
processes exit after their settled attempt.
Alongside the first-pass, FAIL loop, max-iteration, blocked, and unexpected-exit
scenarios, the suite covers graph-first dependency promotion, a late-created
dependent that stays pending, and the never-close terminal roles. The harness never
closes a terminal from its lifecycle policy path; the only terminal it reclaims is
the run-owner fixture it created itself, behind three guards that refuse loudly
rather than close.
Runtime-issued IDs and command receipts are written as diagnostic JSON artifacts;
tests assert their structure and invariants rather than fixed identifier values.

### CI and releases

GitHub Actions runs the validator, deterministic unit/policy/fake-agent tests,
package verification, reproducible archive build/verification, and whitespace check
on pull requests and `main` pushes using Python 3.11, 3.12, and 3.13. It installs no
third-party Python dependencies.

CI intentionally does not run Orca Desktop/runtime integration or real
`claude-glm`/`claude-gemma` agents. Orca integration remains the opt-in local command
above. Real GLM/Gemma smoke testing is **VERIFIED in the tested company environment on
Orca 1.4.178-rc.2**; see the
[`Step 5 report`](STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md). This is a point verification,
not a claim of compatibility across an Orca version range. A stable production-ready
release is not yet claimed because the license decision remains open.

Build and verify the deterministic release archive locally with:

```bash
python3 scripts/build_release.py
python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"
```

See [`RELEASING.md`](RELEASING.md) for SemVer rules and the release checklist.
No license has been selected; [`LICENSE-DECISION.md`](LICENSE-DECISION.md) records the
required owner decision rather than inventing a grant.

## Execution-layer Difference

The two skills intentionally share development policy but differ in execution mechanics:

```text
orca-worker-reviewer-loop
  → direct Orca session / terminal control

orca-worker-reviewer-orchestration
  → Orca built-in orchestration for Run/Task/Dispatch state
  → Orca built-in orca-cli when terminal lifecycle control is required
```

The orchestration variant never claims a worker was orchestrated unless real Orca orchestration state exists.
