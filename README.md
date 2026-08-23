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

- **No profile** is a normal state. The review then uses explicit requirements, the
  current phase contract and the Minimal General Gate — and does *not* restore the
  old broad generic checklist.
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
  YAML all resolve to this state.

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
