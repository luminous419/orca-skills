# Orca Skills

Reusable Orca skills for structured software-development workflows.

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
- Sequential phase order is validated.
- Specialized phases (`BUGFIX`, `REFACTORING`) are not silently mixed into arbitrary sequential combinations.
- Unsupported specialized phase combinations are blocked with `UNSUPPORTED_PHASE_COMBINATION`.
- Explicit `phases=` conflicting with natural-language phase requests is blocked.
- IMPLEMENTATION requires Unit Tests.
- BUGFIX requires a Regression Test.
- Reviewer never edits/fixes its own findings.
- `max-iterations` limits repeated review loops.

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

Override agents:

```text
/orca-worker-reviewer-orchestration \
worker=claude-glm \
reviewer=claude-gemma \
phases=design,implementation

<request>
```

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
python3 -m unittest scripts/test_validate_skills.py
```

The validator uses only the Python standard library and validates both
`orca-worker-reviewer-loop` and `orca-worker-reviewer-orchestration`. It checks:

- `SKILL.md` YAML frontmatter
- required phase templates and review policies
- phase routing against the files on disk
- identical shared `templates/` and `reviews/` content across both skills
- absence of user-specific absolute paths
- required error codes, test gates, and the `max-iterations` range

The command exits with status `0` when all checks pass and a non-zero status with
actionable error messages when an inconsistency is found.

The regression tests run the validator against disposable repository copies and
verify that a valid repository passes while a missing required error code and
shared-template drift are rejected.

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
