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

The orchestration variant additionally requires Orca built-in orchestration support to be enabled and available in the installed Orca version.

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

## 5. Verify Help

```text
/orca-worker-reviewer-loop help
/orca-worker-reviewer-orchestration help
```

Help mode should not start Worker/Reviewer execution.

## 6. Project-local Installation — Optional

```bash
mkdir -p .claude/skills
cp -R orca-worker-reviewer-loop .claude/skills/
cp -R orca-worker-reviewer-orchestration .claude/skills/
```

Use global installation when the same skills should be reused across projects.

## 7. Update

After pulling or receiving a newer repository snapshot, replace the installed skill directory.

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop
cp -R orca-worker-reviewer-loop ~/.claude/skills/

rm -rf ~/.claude/skills/orca-worker-reviewer-orchestration
cp -R orca-worker-reviewer-orchestration ~/.claude/skills/
```

Restart the Claude Code session if updated skills are not detected.

## 8. Uninstall

```bash
rm -rf ~/.claude/skills/orca-worker-reviewer-loop
rm -rf ~/.claude/skills/orca-worker-reviewer-orchestration
```

## 9. Runtime Artifacts

Do not copy generated `run/` or other runtime artifact directories as part of a skill installation.
The distributable skill definition is:

```text
SKILL.md
templates/
reviews/
```
