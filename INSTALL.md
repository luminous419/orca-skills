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
only the standard library. Read [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) before treating
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
workflow policy gates before the skill directories are copied into place. It
also rejects stale relative links in the maintained repository documentation.

The validator uses only the Python standard library. A successful run ends with
`Skill validation PASSED` and exits with status `0`. The accompanying regression
tests confirm that representative broken repository states are rejected and that
both skills return the same deterministic policy decisions without starting Orca
or the configured Worker/Reviewer commands. The suite also runs fake-agent E2E
subprocesses in disposable workspaces; it never invokes a real LLM or Orca runtime.

The default suite and CI do not start Orca Desktop or real agents. The Step 5
`claude-glm`/`claude-gemma` smoke test is **VERIFIED in the tested company environment
on Orca 1.4.178-rc.2**. See
[`historical GLM/Gemma smoke report`](docs/validation/historical/GLM_GEMMA_SMOKE_REPORT_2026-08-20.md)
for the evidence and [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) for its deliberately narrow scope.
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
version-matched guide's tracked-Dispatch fallback. This revision is point-verified on
Orca `1.4.196` only; the check is exact set membership and not a range, so any other
version — including the earlier `1.4.184` observation, which was made against an older
revision of this repository — or changed guide grammar is skipped before runtime state
is created. The `1.4.184` and `1.4.178-rc.2` records are preserved as historical
observations and grant no support. See
[`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md) for what each observation covers and
which of them applies to the current head.

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

`orca-worker-reviewer-orchestration` additionally contains `tools/run_logging.py` — the
run-scoped `ORCHESTRATOR_LOG.md`/`TIMING_LOG.md` writer's CLI, needed because a live
Coordinator invokes it from the installed Skill directory, not from this repository's
own `scripts/` (which the commands above never copy). It is byte-identical to this
repository's own `scripts/run_logging.py`; `scripts/validate_skills.py` enforces that
the two never drift.

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

`orca-worker-reviewer-orchestration` additionally distributes `tools/run_logging.py` (see
section 5).
### OS-30 clarification tool

### OS-40 deterministic workflow engine

The engine runtime is optional for legacy validation and required to execute the graph:

```bash
python3 -m pip install -r requirements-langgraph.txt
python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --check-runtime
```

Offline installations must obtain these pinned wheels through the organization's approved
transfer/cache process. The command fails explicitly when LangGraph is absent or not version
0.2.76; it does not use the prompt loop as a fallback.

`tools/run_workflow.py` is the engine's execution entry point, not only a runtime probe. It
builds state, selects an adapter, invokes or resumes the compiled graph, prints the terminal
result and exits with that result's code:

| exit code | meaning |
| --- | --- |
| 0 | `COMPLETED` |
| 1 | `BLOCKED` (decision block, quality block, or malformed input state) |
| 2 | `ESCALATED` (phase or final-review iteration budget exhausted) |
| 3 | unusable arguments, unreadable input, or missing/wrong LangGraph runtime |
| 4 | `WAITING_FOR_INPUT` (OS-31: a durable pause awaiting a human decision) |
| 5 | `CANCELLED` (OS-31: an explicit human cancel of a paused run) |
| 6 | `ABANDONED` (OS-31: an explicit human abandon of a paused run) |

**The shipped command line is checkpoint-durable by default.** OS-31 installs a durable
`FileCheckpointSaver` with no extra flags, beside the durable ledger, so a paused run can
survive the process; `--checkpoint-store` and `$ORCA_OS40_CHECKPOINT_DIR` move it. This does
not change the sentence above: LangGraph is still required to execute the graph, the runtime
check still fails explicitly when it is absent, and there is still no prompt-loop fallback.

OS-31 adds exactly two verbs, and no more:

```bash
python3 orca-worker-reviewer-orchestration/tools/run_workflow.py discover --artifact-base . --json
python3 orca-worker-reviewer-orchestration/tools/run_workflow.py resume --run-id run_x --artifact-base .
```

`discover` lists every paused run under an artifact base and is read-only; without LangGraph
it still works but reports every verdict as `CHECKPOINT_UNVERIFIED`, never `RESUMABLE`.
`resume` applies the recorded human decision and re-enters the same run exactly once, or
disposes it with `--cancel` / `--abandon`; without LangGraph it refuses with
`LANGGRAPH_DEPENDENCY_MISSING` before taking any claim.

Run the canonical five-phase workflow end to end with no Orca runtime present:

```bash
python3 orca-worker-reviewer-orchestration/tools/run_workflow.py --demo --json
```

Supply your own scenario with a state specification and a scripted settlement list:

```bash
python3 orca-worker-reviewer-orchestration/tools/run_workflow.py \
  --state state.json --results results.json --runtime-state runtime_state.json --json
```

`state.json` accepts `run_id`, `thread_id`, `phases`, `risk` and `max_iterations`;
`results.json` is a JSON list of Worker/Reviewer result objects settled in order.

**Durable idempotency is required, not optional.** Every path that can create an external
Task/Dispatch needs a `RuntimeStatePort`: the engine claims each stable intent in that
ledger *before* the external effect, so a process that dies and restarts recovers the
existing receipt instead of creating a second Task/Dispatch. There is deliberately no
port-less mode.

- `build_graph(adapter)`, `execute_intent_node(adapter)` and `execute_state(...)` resolve
  the port from their `runtime_state` argument, or from one bound to the adapter. If
  neither is present they raise `IdempotencyPortRequired` — at build time, before any
  state is processed, so a graph that could duplicate an effect cannot be constructed.
  Supplying two different ports raises `RuntimeStateConflict`, because two ledgers for one
  execution would split the receipts.
- `run_workflow.py` always supplies one, so the shipped command line is crash-safe with no
  extra flags. `--runtime-state PATH` chooses the file; without it the launcher writes
  `<run_id>__<thread_id>.json` under `$ORCA_OS40_RUNTIME_STATE_DIR`, defaulting to a
  `orca-os40-runtime-state` directory in the system temp location. That default is a real
  file — it survives the process, which is what the crash window requires — but temp
  directories are cleared periodically, so set `ORCA_OS40_RUNTIME_STATE_DIR` or pass
  `--runtime-state` to keep the ledger somewhere you control.

**Recursion limit.** LangGraph's default `recursion_limit` is 25 graph steps. One settled
intent costs 5 steps and each phase advance costs 2, so the canonical five-phase workflow
needs about 68 steps and aborts with `GraphRecursionError` under the default. The launcher
therefore always sets the limit explicitly, computed from the requested phases and the
iteration budget (`deterministic_workflow.launcher.default_recursion_limit`); `--recursion-limit`
overrides it. Callers that embed `build_graph` directly must pass their own
`config={"recursion_limit": ...}`.

New clarification requests and responses use schema generation v2; homogeneous historical v1 single-item artifacts remain immutable and are never migrated or rewritten.

Installing `orca-worker-reviewer-orchestration` with the documented directory copy also installs `tools/clarification_protocol.py` and its adjacent `run_logging.py` dependency. Run `python ~/.claude/skills/orca-worker-reviewer-orchestration/tools/clarification_protocol.py --help` to verify the self-contained CLI. The loop Skill intentionally has no artifact CLI.
