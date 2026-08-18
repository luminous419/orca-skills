# Installation — Orca Skills

This repository contains multiple Orca skills. Install one or both skill directories into Claude's skill path.

## 1. Prerequisites

- Orca
- Claude Code environment capable of launching the selected Worker/Reviewer commands
- `~/.claude/skills` available
- Default agent commands on PATH:

```bash
command -v claude-glm
command -v claude-gemma
```

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

## 3. Global Installation — Recommended

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

## 4. Verify Files

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

## 5. Verify Orca Built-in Skills for the Orchestration Variant

If `orca-worker-reviewer-orchestration` is installed, verify the current Orca binary exposes both built-in guides:

```text
<ORCA> skills get orchestration
<ORCA> skills get orca-cli
```

`<ORCA>` means the Orca CLI executable resolved for the current environment according to Orca's own skill guidance.

The first guide is used for Run/Task/Dispatch/worker completion state. The second is used when terminal create/send/read/wait operations are required for custom commands such as `claude-glm` or `claude-gemma`.

## 6. Verify Help

```text
/orca-worker-reviewer-loop help
/orca-worker-reviewer-orchestration help
```

Help mode should not start Worker/Reviewer execution.

## 7. Project-local Installation — Optional

```bash
mkdir -p .claude/skills
cp -R orca-worker-reviewer-loop .claude/skills/
cp -R orca-worker-reviewer-orchestration .claude/skills/
```

Use global installation when the same skills should be reused across projects.

## 8. Update

After pulling or receiving a newer repository snapshot, replace the installed skill directory.

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop
cp -R orca-worker-reviewer-loop ~/.claude/skills/

rm -rf ~/.claude/skills/orca-worker-reviewer-orchestration
cp -R orca-worker-reviewer-orchestration ~/.claude/skills/
```

Restart the Claude Code session if updated skills are not detected.

## 9. Uninstall

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop
rm -rf ~/.claude/skills/orca-worker-reviewer-orchestration
```

## 10. Runtime Artifacts

Do not copy generated `run/` or other runtime artifact directories as part of a skill installation.
The distributable skill definition is:

```text
SKILL.md
templates/
reviews/
```
