"""OS-37 F5 — the REAL-CLI, Orca-free Worker -> Reviewer FAIL -> correction -> fresh
Reviewer PASS workflow, driven through the PRODUCTION GRAPH/ADAPTER PROMPT BOUNDARY.

This is what the consolidated follow-up review of ``87f6179`` (finding 5) asked for, and
it is deliberately a DIFFERENT thing from ``scripts/os37_r10_real_agent.py``.  That harness
hands its prompt straight to ``StandaloneSession.run_dispatch(payload=...)`` and states in
its own docstring that it does NOT traverse ``graph.EXECUTE_INTENT ->
AgentExecutionPort.start`` -- so the prompt it proves is the HARNESS's, not the production
Graph's.

Here the ONLY thing this module composes is a launch specification: an OBJECTIVE (the task
contract) and, as DATA, the per-round instruction that scopes a first iteration.  It then
runs the shipped ``run_workflow.py --adapter standalone`` in a process whose ``PATH`` cannot
resolve ``orca``.  Every prompt the agent sees is rendered by
``launcher.build_standalone_prompt_composer`` at the ``EXECUTE_INTENT ->
StandaloneAdapter.start`` boundary -- role, phase, task contract, correction instruction and
the ``STATUS:`` / ``RESULT:`` + ``decision-gate`` review-output contract -- and delivered to
the real ``claude`` / ``codex`` CLI.

**There is no code path in this module that can choose a verdict.**  The Worker's FAIL is
induced ONLY by the launch data (a first-iteration instruction that legitimately defers one
required section of the objective, framed as the intended first step of the correction
loop, never as a contradiction); the reviewer reaches PASS/FAIL from its OWN reading of the
files, and the workflow gate reads that verdict out of the settlement the GRAPH consumed,
through the same ``decision_contract.parse_agent_settlement`` the Orca and fake paths use.
``test_os37_r10_graph_prompt_e2e.py`` walks this file's AST and fails if any ``PASS``/``FAIL``
string is assigned, returned or compared as a verdict here.

If the CLI is unauthenticated, quota-blocked or unavailable, this reports BLOCKED with the
exact command and the raw bytes observed -- never a substituted verdict and never a fake
adapter run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.os37_r10_real_agent import (  # noqa: E402 - the reusable Orca-free helpers
    assert_orca_free, claude_profile, codex_profile, orca_free_environment,
    profile_document)

#: The DESIGN-phase task contract.  DESIGN, not a code phase, so the phase gate reads the
#: reviewer's PASS/FAIL directly and no `unit_test_status` field is required.  The contract
#: is strict on purpose: all three sections with real content, or the reviewer must FAIL.
OBJECTIVE = (
    "Write a design document DESIGN.md in the current working directory for a small "
    "string-utilities module.  For THIS phase gate the design is complete only if it "
    "contains all three sections, with these exact titles and real content in each: "
    "'## Parsing', '## Error handling', and '## Examples'.  If any of the three sections "
    "is absent or contains only a placeholder / deferral note rather than real content, "
    "the design does NOT satisfy this phase's contract and a reviewer must return "
    "RESULT: FAIL for it."
)

#: The inducement, as DATA on the launch spec (not a runtime decision).  It scopes the
#: first pass and frames the FAIL as the intended first step of the correction loop, so the
#: Worker does not read it as a contradiction and does not pre-empt the reviewer.
ROLE_INSTRUCTIONS = {
    "WORKER:PHASE_GATE": (
        "Submit a FIRST draft now containing '## Parsing' and '## Examples' with real "
        "content, and an '## Error handling' heading whose body is only the note "
        "'Deferred to the next pass.'.  This is the intended first step of the correction "
        "loop: it is EXPECTED that the reviewer returns FAIL asking you to fill in error "
        "handling, and you will do that in the correction round.  Do NOT pre-empt it by "
        "writing the error-handling content now; report STATUS: COMPLETE for this first "
        "draft."
    ),
    "WORKER:CORRECTION": (
        "Address the reviewer's findings: replace the deferral note under "
        "'## Error handling' with real content specifying the ValueError behaviour for "
        "invalid inputs, so all three sections have real content."
    ),
}

#: Ordinal labels for the settlements the graph produced, for the evidence summary.
_SEQUENCE_LABELS = (
    "Worker (iteration 1)", "Phase Reviewer (iteration 1)", "Worker (correction)",
    "Phase Reviewer (iteration 2)", "Final Reviewer",
)


def _write_profile(cli: str, worktree: str, path: Path, *, codex_home: str = "") -> None:
    profile = (codex_profile(worktree, codex_home) if cli == "codex"
               else claude_profile(worktree))
    document = profile_document(profile)
    document["worktree"] = worktree
    # Per CLI: `--strict-mcp-config` is a Claude Code flag; codex-cli (measured 0.153.2)
    # rejects it with `error: unexpected argument '--strict-mcp-config'` (exit 2) before any
    # record is emitted, which fails the preflight rehearsal as `profile_readiness_unverified`.
    # Both CLIs accept `--add-dir`.
    document["extra_args"] = (["--add-dir", worktree] if cli == "codex"
                              else ["--strict-mcp-config", "--add-dir", worktree])
    document["add_dirs"] = []

    def _plain(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        raise TypeError(type(value).__name__)
    path.write_text(json.dumps(document, default=_plain))


def _settlement_verdicts(journal_path: Path) -> list[dict[str, Any]]:
    """The (status, result) the GRAPH consumed for each settlement, read from the journal.

    The values are transcribed from the settlement event's own ``result`` -- the object the
    production ``decision_contract.parse_agent_settlement`` produced and the graph routed
    on -- never chosen here.
    """
    out: list[dict[str, Any]] = []
    for line in journal_path.read_text().splitlines():
        row = json.loads(line)
        if row["kind"] != "SETTLEMENT_OBSERVED":
            continue
        event = (row.get("source_vocabulary") or {}).get("event") or {}
        result = event.get("result") or {}
        out.append({"status": result.get("status"), "result": result.get("result"),
                    "gate_state": (result.get("gate") or {}).get("declared_state")})
    return out


def run_e2e(cli: str, out_dir: Path, *, timeout_s: float = 1800.0) -> dict[str, Any]:
    """Drive ONE real ``run_workflow.py --adapter standalone`` loop and read its evidence."""
    out_dir.mkdir(parents=True, exist_ok=True)
    worktree = out_dir / "wt"
    worktree.mkdir(exist_ok=True)
    artifact_base = out_dir / "artifact_base"
    artifact_base.mkdir(exist_ok=True)
    codex_home = str(out_dir / "codex_home")
    if cli == "codex":
        os.makedirs(codex_home, exist_ok=True)
    _write_profile(cli, str(worktree), out_dir / "profile.json", codex_home=codex_home)
    # `state.validate_state` requires run ids to match `run_[a-z0-9]+` -- no separator
    # after the prefix -- so the CLI name is appended without an underscore.
    run_id = f"run_f5graph{cli}"
    state = {"run_id": run_id, "thread_id": "t", "phases": ["DESIGN"], "risk": "high",
             "max_iterations": 5, "objective": OBJECTIVE,
             "role_instructions": ROLE_INSTRUCTIONS}
    (out_dir / "state.json").write_text(json.dumps(state))

    env = orca_free_environment()
    precondition = assert_orca_free(env)      # raises if orca is reachable
    (out_dir / "preconditions.json").write_text(json.dumps(precondition, indent=2))
    env["ORCA_OS40_RUNTIME_STATE_DIR"] = str(out_dir / "runtime_state")
    env["ORCA_OS40_CHECKPOINT_DIR"] = str(out_dir / "checkpoints")
    os.makedirs(env["ORCA_OS40_RUNTIME_STATE_DIR"], exist_ok=True)
    os.makedirs(env["ORCA_OS40_CHECKPOINT_DIR"], exist_ok=True)

    cmd = [sys.executable,
           str(REPO / "orca-worker-reviewer-orchestration" / "tools" / "run_workflow.py"),
           "--adapter", "standalone", "--state", str(out_dir / "state.json"),
           "--standalone-profile", str(out_dir / "profile.json"),
           "--artifact-base", str(artifact_base), "--project-root", str(REPO), "--json"]
    started = time.time()
    try:
        completed = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                   timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return {"cli": cli, "outcome": "blocked", "constraint": "cli_timeout",
                "measurement": " ".join(cmd),
                "observed": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str)
                else "", "elapsed_s": round(time.time() - started, 1)}
    (out_dir / "run_stdout.json").write_text(completed.stdout)
    (out_dir / "run_stderr.txt").write_text(completed.stderr)
    record: dict[str, Any] = {"cli": cli, "command": " ".join(cmd),
                              "exit_code": completed.returncode,
                              "elapsed_s": round(time.time() - started, 1)}
    try:
        summary = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        record.update({"outcome": "blocked", "constraint": "no_summary",
                       "measurement": " ".join(cmd),
                       "observed": (completed.stdout + completed.stderr)[-2000:]})
        return record
    record["run_summary"] = summary
    journal = artifact_base / "runs" / run_id / "standalone" / "journal.ndjson"
    if journal.exists():
        shutil.copy(journal, out_dir / "execution_journal.ndjson")
        record["settlements"] = _settlement_verdicts(journal)
    record["outcome"] = "ran"
    return record


def loop_is_established(record: dict[str, Any]) -> bool:
    """Whether THIS run showed Worker -> Reviewer FAIL -> correction -> fresh Reviewer PASS.

    The judgement is read from the settlement verdicts the GRAPH consumed, in order: a
    worker COMPLETE, a reviewer whose result is the fail token, a later worker COMPLETE, a
    later reviewer whose result is the pass token, and a COMPLETED terminal.  The tokens
    come from the workflow output contract, not from a literal in this module.
    """
    from scripts.workflow_contract import load_workflow_output_contract
    skill = REPO / "orca-worker-reviewer-orchestration" / "SKILL.md"
    contract = load_workflow_output_contract(skill)
    fail_token, pass_token = contract.reviewer_fail, contract.reviewer_pass
    if record.get("outcome") != "ran":
        return False
    if (record.get("run_summary") or {}).get("terminal_status") != "COMPLETED":
        return False
    verdicts = [row.get("result") for row in record.get("settlements") or []]
    fails = [i for i, v in enumerate(verdicts) if v == fail_token]
    if not fails:
        return False
    later_pass = [i for i, v in enumerate(verdicts) if v == pass_token and i > fails[0]]
    return bool(later_pass)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", help="directory the F5 graph-boundary evidence is written to")
    parser.add_argument("--cli", default="claude", choices=("claude", "codex"))
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    args = parser.parse_args(argv)
    out_dir = Path(args.out_dir)
    record = run_e2e(args.cli, out_dir, timeout_s=args.timeout_s)
    record["loop_established"] = loop_is_established(record)
    labelled = []
    for label, row in zip(_SEQUENCE_LABELS, record.get("settlements") or []):
        labelled.append({"dispatch": label, **row})
    record["loop_shape"] = labelled
    (out_dir / "F5_EVIDENCE.json").write_text(json.dumps(record, indent=2, default=str))
    print(json.dumps(record, indent=2, default=str))
    return 0 if record.get("loop_established") else 1


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
