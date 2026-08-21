# Installation — Orca Skills

This repository contains multiple Orca skills. Install one or both skill directories into Claude's skill path.

## 1. Prerequisites

- Orca
- Claude Code environment capable of launching the selected Worker/Reviewer commands
- `~/.claude/skills` available
- The selected Worker and Reviewer commands on PATH. Defaults:

```bash
command -v claude-glm
command -v claude-gemma
```

The known commands are `claude`, `codex`, `claude-glm`, and `claude-gemma`. Additional
wrappers must use a trusted `claude-` or `codex-` prefix, be a simple
`[A-Za-z0-9._-]+` token, and resolve to an executable on PATH. This prevents unrelated
PATH commands such as shells and interpreters from being selected as agents.

For a personal model-pinned setup, place wrappers such as `~/bin/claude-opus` and
`~/bin/codex-sol` in a directory already on PATH, then select them by command name:

```text
/orca-worker-reviewer-orchestration \
  worker=claude-opus \
  reviewer=codex-sol \
  phases=analysis,plan,design
```

The repository intentionally does not provide those wrapper scripts. The Skill does
not inspect wrapper internals. Likewise, when generic `claude` and `codex` commands are
selected, model selection is the responsibility of each CLI's current configuration;
the Skill does not choose or guarantee a model.
The Skill also appends no permission or other vendor-specific launch arguments. Configure
those in the CLI itself or include them inside the PATH-resolved wrapper implementation.

Do not put arguments, paths, whitespace, or shell metacharacters in `worker=` or
`reviewer=`. Values such as `claude --model opus`, `../claude`, an absolute path, or
`claude;echo` are rejected rather than executed.

Repository validation and packaging support CPython 3.11, 3.12, and 3.13 and use
only the standard library. Read [`COMPATIBILITY.md`](COMPATIBILITY.md) before treating
a runtime configuration as verified.

The orchestration variant additionally requires:

- Orca built-in `orchestration` support enabled and available in the installed Orca version.
- Orca built-in `orca-cli` guide available for terminal lifecycle control when custom agent commands are launched in Orca terminals.

The skill loads the version-matched runtime guides rather than hard-coding terminal/orchestration CLI grammar.

## 2. Clone or Download

```bash
git clone https://github.com/luminous419/orca-skills.git
cd orca-skills
```

For an offline company laptop, transfer the repository/package using the organization's approved file-transfer process, then continue from the extracted directory.

## 3. Validate the Repository

Before installing or updating either skill, run:

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/verify_package.py
git diff --check
```

The validator checks both `orca-worker-reviewer-loop` and
`orca-worker-reviewer-orchestration`. Its purpose is to catch malformed
`SKILL.md` frontmatter, missing or incorrect phase routing, drift between the
shared templates/review policies, user-specific absolute paths, and missing
workflow policy gates before the skill directories are copied into place.

The validator uses only the Python standard library. A successful run ends with
`Skill validation PASSED` and exits with status `0`. The accompanying regression
tests confirm that representative broken repository states are rejected and that
both skills return the same deterministic policy decisions without starting Orca
or the configured Worker/Reviewer commands. The suite also runs fake-agent E2E
subprocesses in disposable workspaces; it never invokes a real LLM or Orca runtime.

The default suite and CI do not start Orca Desktop or real agents. The Step 5
`claude-glm`/`claude-gemma` smoke test is **VERIFIED in the tested company environment
on Orca 1.4.178-rc.2**. See
[`STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md`](STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md) for the
evidence and [`COMPATIBILITY.md`](COMPATIBILITY.md) for its deliberately narrow scope.
Stable production readiness is not yet claimed because the license decision remains open.

The optional real-Orca integration suite is separate so Orca availability never
breaks installation validation. With this checkout registered in a running Orca
runtime, execute:

```bash
python3 scripts/test_orca_runtime.py --orca-runtime \
  --artifact-dir artifacts/orca-runtime/latest
```

This uses only deterministic fake agents but creates real Orca Runs, Tasks,
Dispatches, lifecycle messages, and terminal/resource observations. A custom
fake executable that is not recognized for supervised attachment follows the
version-matched guide's tracked-Dispatch fallback. The adapter currently supports
Orca `1.4.184`; other versions or changed guide grammar are skipped before runtime
state is created.

## 4. Global Installation — Recommended

```bash
mkdir -p ~/.claude/skills
```

Install the direct-session loop:

```bash
cp -R orca-worker-reviewer-loop ~/.claude/skills/
```

Install the Orca-native orchestration variant:

```bash
cp -R orca-worker-reviewer-orchestration ~/.claude/skills/
```

Install both when comparing behavior:

```bash
cp -R orca-worker-reviewer-loop ~/.claude/skills/
cp -R orca-worker-reviewer-orchestration ~/.claude/skills/
```

## 5. Verify Files

```bash
find ~/.claude/skills/orca-worker-reviewer-loop -maxdepth 3 -type f | sort
find ~/.claude/skills/orca-worker-reviewer-orchestration -maxdepth 3 -type f | sort
```

Each installed skill should contain:

```text
SKILL.md
templates/
reviews/
```

For a source release, build and verify the deterministic archive instead of copying a
working tree with local artifacts:

```bash
python3 scripts/build_release.py
python3 scripts/verify_package.py --archive "dist/orca-skills-$(tr -d '\n' < VERSION).tar.gz"
```

The archive includes repository documentation, validation tooling, CI metadata, and
both complete Skill directories. It excludes `.git`, `artifacts`, `run`, `dist`, Python
bytecode, and `__pycache__` content.

## 6. Verify Orca Built-in Skills for the Orchestration Variant

If `orca-worker-reviewer-orchestration` is installed, verify the current Orca binary exposes both built-in guides:

```text
<ORCA> skills get orchestration
<ORCA> skills get orca-cli
```

`<ORCA>` means the Orca CLI executable resolved for the current environment according to Orca's own skill guidance.

The first guide is used for Run/Task/Dispatch/worker completion state. The second is used when terminal create/send/read/wait operations are required for custom commands such as `claude-glm` or `claude-gemma`.

## 7. Verify Help

```text
/orca-worker-reviewer-loop help
/orca-worker-reviewer-orchestration help
```

Help mode should not start Worker/Reviewer execution.

## 8. Project-local Installation — Optional

```bash
mkdir -p .claude/skills
cp -R orca-worker-reviewer-loop .claude/skills/
cp -R orca-worker-reviewer-orchestration .claude/skills/
```

Use global installation when the same skills should be reused across projects.

## 9. Update

After pulling or receiving a newer repository snapshot, replace the installed skill directory.

Run `python3 scripts/validate_skills.py` before replacing either installed directory.

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop
cp -R orca-worker-reviewer-loop ~/.claude/skills/

rm -rf ~/.claude/skills/orca-worker-reviewer-orchestration
cp -R orca-worker-reviewer-orchestration ~/.claude/skills/
```

Restart the Claude Code session if updated skills are not detected.

## 10. Uninstall

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop
rm -rf ~/.claude/skills/orca-worker-reviewer-orchestration
```

## 11. Runtime Artifacts

Do not copy generated `run/` or other runtime artifact directories as part of a skill installation.
The distributable skill definition is:

```text
SKILL.md
templates/
reviews/
```
