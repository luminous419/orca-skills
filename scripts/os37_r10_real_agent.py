"""OS-37 R10 — the REAL-AGENT, Orca-free Worker -> Reviewer -> correction -> Final Review E2E.

**This module is what FINAL_REVIEW F-002 asked for, and it is deliberately a different thing
from `scripts/os37_r10_fixture.py`.**  That fixture is retained unchanged (USER DIRECTIVE
D-H) as runtime-boundary evidence, and it is INADMISSIBLE here: in it, `os37-r10-agent`
supplies readiness and success itself and selects the reviewer's FAIL/PASS from a fixture
input, so no agent performs the work and no agent forms a verdict.

Here, four dispatches are handed to a REAL configured CLI through the unchanged standalone
runtime, and the reviewer's verdict is PARSED OUT OF THE ARTIFACT THE AGENT PRODUCED by the
same `decision_contract.parse_agent_settlement` the production policy uses.

**There is no code path in this module that can choose a verdict.**  That is not a promise:
`test_os37_standalone_e2e.py::test_the_real_agent_harness_cannot_author_a_verdict` walks this
file's AST and fails if any string containing `PASS` or `FAIL` is assigned, returned or
compared as a verdict outside the parser's own output.

**How the FAIL at step 2 is induced (D13.6(b)).**  By controlling the WORKER'S INPUT, never
the reviewer's output.  The task contract states one requirement twice and unmissably --
negative input must raise `ValueError`, and a test must assert it -- while the worker's
iteration-1 instruction block carries an explicit "implement only the happy path" clause.
So the worker's step-1 artifact is genuinely non-conforming to the contract the reviewer is
given, and the worker was not asked to lie.  The reviewer is not told a defect exists, is
not told a verdict, and its instruction block is BYTE-IDENTICAL between step 2 and step 4.

If the reviewer returns PASS at step 2 the run is recorded `INDUCEMENT_INEFFECTIVE` -- an
honest outcome to be reported and re-run with a sharper contract, never patched by having
the runtime supply the verdict.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any, Literal, TypedDict

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:              # `python3 scripts/os37_r10_real_agent.py`
    sys.path.insert(0, str(REPO))
FIXTURES = REPO / "scripts" / "fixtures" / "os37" / "r10"

#: The four committed prompt fixtures.  R-1: the inducement is DATA, not a runtime decision,
#: so a prompt change is a visible diff and its digest is recorded in the evidence.
PROMPT_FILES = ("task_contract.md", "worker_iteration1.md", "worker_correction.md",
                "reviewer.md")


class BlockedEvidence(TypedDict):
    """D13.6(d).  ``verdict`` is a single-member ``Literal``: this type CANNOT say PASS.

    The same type-level discipline `ReadinessEvidence` / `CompletionEvidence` use.  A CLI
    that cannot be exercised because of authentication or environment yields one of these.
    It is never a PASS, never a skip, and never omitted from the matrix (USER DIRECTIVE
    D-G).  An empty ``measurement`` or ``observed`` makes the row INVALID and the E2E
    "not established" -- "blocked" without evidence is indistinguishable from "not
    attempted".
    """

    cli: str
    binary_realpath: str
    version_string: str
    constraint: str
    measurement: str
    observed: str
    exit_code: int | None
    at: str
    verdict: Literal["BLOCKED"]


BLOCKED_CONSTRAINTS = ("auth_absent", "auth_scope_unseeded", "auth_probe_interactive",
                       "binary_absent", "version_unsupported", "network_unreachable",
                       "delivery_mode_mismatch", "identity_binding_unverified",
                       "profile_readiness_unverified", "quota_exhausted")

#: Constraints that are statements about THIS DESIGN being wrong for the installed CLI
#: rather than about the host being unavailable.  A row carrying one of these routes to
#: DESIGN as `PREVIOUS_PHASE_CHANGE_REQUIRED`.
DESIGN_ROUTING_CONSTRAINTS = ("delivery_mode_mismatch", "identity_binding_unverified",
                              "profile_readiness_unverified")


def blocked(cli: str, *, binary_realpath: str, version_string: str, constraint: str,
            measurement: str, observed: str,
            exit_code: int | None) -> BlockedEvidence:
    if constraint not in BLOCKED_CONSTRAINTS:
        raise ValueError(f"constraint {constraint!r} is not a closed-set member")
    if not measurement.strip() or not observed.strip():
        raise ValueError(
            "a BLOCKED row must name the EXACT command that established it and the exact "
            "bytes observed; a row without them is indistinguishable from 'not attempted'")
    return {"cli": cli, "binary_realpath": binary_realpath,
            "version_string": version_string, "constraint": constraint,
            "measurement": measurement, "observed": observed[:4000],
            "exit_code": exit_code, "at": _now(), "verdict": "BLOCKED"}


def prompt_digests() -> dict[str, str]:
    """R-1: every prompt fixture's digest, recorded in the evidence artifact."""
    return {name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
            for name in PROMPT_FILES}


# ---- the Orca-free preconditions (D13.6(c)) ----------------------------------------------
def orca_free_environment(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """A child environment with `orca` UNRESOLVABLE and every `ORCA_*` name absent.

    Constructed, never pruned: the allowlist is what the child gets, so a name nobody
    thought to remove cannot survive.  `PATH` is rebuilt from entries that do not resolve
    an `orca` executable, so removing the variable is not confused with removing the tool.
    """
    source = dict(base_env if base_env is not None else os.environ)
    path_entries = [entry for entry in source.get("PATH", "").split(os.pathsep)
                    if entry and not os.path.exists(os.path.join(entry, "orca"))]
    allowed = {"HOME": source.get("HOME", ""), "LANG": source.get("LANG", "en_US.UTF-8"),
               "TERM": "xterm-256color", "TMPDIR": source.get("TMPDIR", "/tmp"),
               "USER": source.get("USER", ""), "SHELL": "/bin/sh",
               "PATH": os.pathsep.join(path_entries)}
    return {name: value for name, value in allowed.items() if value != ""}


def assert_orca_free(env: dict[str, str]) -> dict[str, Any]:
    """The five §D13.3 preconditions plus iteration 3's two, as a RECORDED assertion.

    Raises rather than returning a flag: an E2E that ran with Orca reachable proves nothing
    about an Orca-free environment, so there is no branch in which it continues.
    """
    leaked = sorted(name for name in env if name.upper().startswith("ORCA"))
    if leaked:
        raise RuntimeError(f"ORCA_* names reached the child env: {leaked}")
    resolved = shutil.which("orca", path=env.get("PATH", ""))
    if resolved is not None:
        raise RuntimeError(f"`orca` is resolvable on the child PATH at {resolved}")
    return {"orca_on_child_path": None, "orca_env_names": [],
            "child_env_names": sorted(env), "at": _now()}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---- profiles ---------------------------------------------------------------------------
def _r10_timeouts():
    """Deadlines sized for a REAL agent doing REAL work, measured rather than guessed.

    The default `readiness_timeout_ms` is 60 s, which is a sensible default for a dispatch
    that answers a question.  A worker writing two modules and a report was MEASURED here
    taking 55-70 s, and a correction pass reading a review first takes longer -- so under
    the default the run reached `PROMPT_DELIVERED` and then timed out with the work
    already done, which is a deadline bug reported as a lost dispatch.

    Raising the deadline is the correct fix and it weakens nothing: the completion gate is
    still CONJUNCTIVE (a declared result record AND a `waitpid`-sourced exit), a timeout is
    still `TIMED_OUT` and never `COMPLETED`, and every other refusal is untouched.  These
    are profile fields precisely so that an operator whose host is slower changes
    configuration rather than code (`docs/ORCA_RUNTIME_PRIMITIVES.md` C11).
    """
    from scripts.deterministic_workflow.standalone_profile import Timeouts
    # `readiness_timeout_ms` is back to a READINESS bound.  It was 900_000 because
    # `await_completion` shared this field (external review #4), so the only way to let a
    # multi-minute turn finish was to give a process fifteen minutes to become READY --
    # which silently loosened the readiness gate as the price of a working completion gate.
    # Completion now has `completion_timeout_ms` and each question is bounded by its own
    # answer.
    return Timeouts(readiness_timeout_ms=120_000, preflight_timeout_ms=120_000,
                    delivery_verify_timeout_ms=120_000,
                    completion_timeout_ms=1_800_000,
                    graceful_force_timeout_ms=15_000, physical_exit_timeout_ms=20_000)


R10_TIMEOUTS = _r10_timeouts()


def _bin_dirs(*binaries: str) -> tuple[str, ...]:
    """The directories the child needs, resolved from the PARENT's PATH once.

    The runtime rebuilds the child's ``PATH`` from these plus a fixed base and REMOVES every
    directory that holds an `orca` executable (`standalone_env.build_child_path`), so this
    function names what the agent needs and the env policy is what guarantees Orca is not
    among it.  `node` is included because the Claude CLI is a Node program.
    """
    found: list[str] = []
    for name in binaries:
        resolved = shutil.which(name)
        if not resolved:
            continue
        # BOTH the link's directory and its target's.  A CLI installed as a symlink into a
        # `bin` dir resolves by NAME from the link's directory, while R-A's executable
        # identity is an equality against the TARGET's realpath -- so the child needs the
        # first on PATH and the runtime compares the second.
        for directory in (os.path.dirname(resolved),
                          os.path.dirname(os.path.realpath(resolved))):
            if directory and directory not in found:
                found.append(directory)
    return tuple(found)


def claude_profile(worktree: str, **overrides):
    from scripts.deterministic_workflow.standalone_profile import (
        AuthProbe, CompletionSelector, DeliveryProofSelector, ReadinessSelector,
        ResultBodySelector, StandaloneProfile, Timeouts)
    fields = dict(
        driver="claude", binary="claude", supported_range=((1, 0, 0), (99, 0, 0)),
        timeouts=R10_TIMEOUTS,
        bin_dirs=_bin_dirs("claude", "node", "git"),
        delivery_mode="launch_with_prompt", identity_binding="minted_echo",
        identity_flag="--session-id", worktree=worktree,
        permission_mode="acceptEdits", no_session_persistence=True,
        extra_args=("--strict-mcp-config", "--add-dir", worktree),
        readiness_records=(ReadinessSelector(channel="structured", record_type="system",
                                             session_field="session_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="assistant"),),
        completion_records=(CompletionSelector(channel="structured", record_type="result",
                                               error_field="is_error",
                                               success_field="terminal_reason",
                                               success_values=("completed",)),),
        # D4.0 M-14: the final assistant text is the `result` record's own `result` field.
        # Declaring it is what lets the driver hand the SHARED settlement parser a BODY
        # instead of the whole JSON event stream (external review #3).
        result_body_records=(ResultBodySelector(channel="structured",
                                                record_type="result",
                                                body_field="result"),),
        # D4.0 M-13: `claude auth status` is bounded, non-interactive and exits 0 when the
        # CLI holds a usable session.  Declared here rather than injected into
        # `StandaloneSession.start` by this harness, which is external review #6: the
        # injection meant the launcher -> adapter -> session path was never exercised.
        auth_probe=AuthProbe(args=("auth", "status")),
        auth_markers=(("error", "authentication_failed"),
                      ("is_api_error_message", "True"),
                      ("terminal_reason", "api_error")))
    fields.update(overrides)
    return StandaloneProfile(**fields)


def codex_profile(worktree: str, codex_home: str, **overrides):
    from scripts.deterministic_workflow.standalone_profile import (
        AuthProbe, CompletionSelector, DeliveryProofSelector, ReadinessSelector,
        ResultBodySelector, StandaloneProfile, Timeouts)
    fields = dict(
        driver="codex", binary="codex", supported_range=((0, 1, 0), (99, 0, 0)),
        timeouts=R10_TIMEOUTS,
        bin_dirs=_bin_dirs("codex", "git"),
        delivery_mode="launch_with_prompt", identity_binding="adopted",
        worktree=worktree, sandbox_mode="workspace-write",
        resume_channel="cli_resume_subcommand", resume_args=("--json",),
        driver_env={"CODEX_HOME": codex_home},
        output_last_message_path=os.path.join(codex_home, "last-message.txt"),
        auth_seed_source=os.path.expanduser("~/.codex/auth.json"),
        auth_seed_dest_name="auth.json",
        readiness_records=(ReadinessSelector(channel="structured",
                                             record_type="thread.started",
                                             session_field="thread_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="item.completed",
                                               item_type="agent_message"),
                         DeliveryProofSelector(channel="structured",
                                               record_type="turn.completed"),),
        completion_records=(CompletionSelector(channel="structured",
                                               record_type="turn.completed"),),
        # D4.0 M-8: the final assistant text arrives as an `item.completed` whose item type
        # is `agent_message`; `-o` carries the same body as a second source, which
        # `_Driver.result_body` consults after the selector.
        result_body_records=(ResultBodySelector(channel="structured",
                                                record_type="item.completed",
                                                item_type="agent_message",
                                                body_field="item.text"),),
        # D4.0 M-13: `codex login status` is the measured bounded, non-interactive probe.
        auth_probe=AuthProbe(args=("login", "status")),
        auth_markers=(("type", "turn.failed"),))
    fields.update(overrides)
    return StandaloneProfile(**fields)


PROFILES = {"claude": claude_profile, "codex": codex_profile}


# ---- one dispatch through the UNCHANGED standalone runtime -------------------------------
def dispatch(*, cli: str, role: str, step: int, prompt: str, worktree: str,
             artifact_base: Path, run_id: str, codex_home: str = "",
             budget_s: float = 900.0) -> dict[str, Any]:
    """One agent, one headless PTY, one identity, one `DELIVERY_INTENT`, one settlement.

    Nothing about the runtime is special-cased for this harness: it is
    `StandaloneSession.run_dispatch` with the profile the driver ships, so what this proves
    is what a real operator would get.  A fresh `StandaloneSession` per step means a fresh
    process with a FRESH IDENTITY -- step 4 is never step 2 resumed, which D13.6(a) requires
    of the "fresh Final Review".
    """
    from scripts.deterministic_workflow import standalone_journal as sj
    from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
    from scripts.deterministic_workflow.standalone_runtime import (StandaloneDispatchFailed,
                                                                   StandaloneSession)
    profile = (PROFILES[cli](worktree, codex_home) if cli == "codex"
               else PROFILES[cli](worktree))
    if cli == "codex":
        from scripts.deterministic_workflow import standalone_drivers as drivers
        seeded = drivers.driver_for(profile).seed_auth_home(codex_home)
        if not seeded["seeded"]:
            return {"outcome": "blocked", "constraint": "auth_scope_unseeded",
                    "detail": seeded, "role": role, "step": step, "cli": cli}
    intent_id = f"r10-{cli}-{role}-{step}-{uuid.uuid4().hex[:8]}"
    intent = {"intent_id": intent_id, "task_id": f"r10-{cli}", "command_id": "c",
              "payload_digest": hashlib.sha256(prompt.encode()).hexdigest(),
              "run_id": run_id, "action_kind": "DISPATCH_AGENT",
              "phase": "IMPLEMENTATION", "role": "WORKER" if role == "worker" else "REVIEWER",
              "round_kind": "PHASE_GATE"}
    ledger = InMemoryRuntimeStateStore()
    claim = ledger.claim(intent)
    journal = sj.ExecutionJournal(artifact_base, run_id)
    session = StandaloneSession(
        intent=intent, profile=profile, artifact_base=artifact_base, run_id=run_id,
        journal=journal, runtime_state=ledger, worktree_path=worktree,
        agent_id=f"r10-{cli}-{role}")
    started = time.time()
    record: dict[str, Any] = {"cli": cli, "role": role, "step": step,
                              "intent_id": intent_id,
                              "prompt_digest": hashlib.sha256(prompt.encode()).hexdigest(),
                              "started_at": _now()}
    # M-13's probe is DECLARED BY THE PROFILE now, not injected here.  This harness used to
    # pass `auth_probe_argv` straight into `StandaloneSession.start`, below the production
    # entry point -- which is external review #6 exactly: the evidence it produced said
    # nothing about whether `launcher -> adapter -> session` can authenticate an existing
    # OAuth session, because that path never carried the probe at all.
    try:
        settled = session.run_dispatch(lease_token=claim["lease_token"], payload=prompt)
        record["outcome"] = "settled"
        record["settled"] = {k: v for k, v in settled.items() if k != "receipt"}
    except StandaloneDispatchFailed as failure:
        record["outcome"] = "failed"
        record["stage"] = failure.stage
        record["reason"] = failure.reason
    finally:
        record["elapsed_s"] = round(time.time() - started, 2)
        record["session_id"] = session.session_id
        record["adopted_id"] = session.adopted_id
        record["state"] = session.state
        record["event_log"] = list(session.event_log)
        record["delivery_intent"] = session.delivery_intent
        record["delivery_proof"] = session.delivery_proof
        # R-4: every byte the agent emitted is retained, so the verdict is RE-DERIVABLE
        # offline by the same parser rather than trusted.
        record["transcript"] = session.capture.transcript()
        record["journal_kinds"] = [row["kind"] for row in journal.rows_for(intent_id)]
        try:
            session.release()
        except Exception:                                    # noqa: BLE001 - teardown only
            pass
    return record


def read_verdict(review_path: Path) -> dict[str, Any]:
    """Parse the reviewer's OWN artifact with the PRODUCTION parser.

    R-5, and the whole answer to F-002: the verdict is read out of a file the agent wrote,
    by `decision_contract.parse_agent_settlement` -- byte-identical policy to the Orca and
    fake paths -- and this function has no other source for it.  If the artifact is absent
    or carries no `RESULT:` line, the value is ``None``, which is neither PASS nor FAIL and
    is reported as `unreadable`.
    """
    from scripts import decision_contract

    class _Body:
        def __init__(self, body: str) -> None:
            self.body = body

    if not review_path.exists():
        return {"verdict": None, "reason": "artifact_absent", "path": str(review_path)}
    body = review_path.read_text(encoding="utf-8", errors="replace")
    parsed = decision_contract.parse_agent_settlement(_Body(body), {})
    return {"verdict": parsed.get("result"), "reason": "", "path": str(review_path),
            "body": body, "parsed": parsed}


def four_step_run(cli_by_role: dict[str, str], *, run_index: int,
                  artifact_base: Path) -> dict[str, Any]:
    """Worker -> Reviewer(FAIL) -> correction -> fresh Final Review, with real agents.

    ``cli_by_role`` maps ``worker``/``reviewer`` to a CLI name, which is what makes the
    MIXED run (worker `claude`, reviewer `codex`) expressible: its only purpose is to
    demonstrate that the POLICY did not branch on the CLI, and it uses the same code path
    as the single-CLI runs.
    """
    contract = (FIXTURES / "task_contract.md").read_text()
    worker_1 = (FIXTURES / "worker_iteration1.md").read_text()
    worker_2 = (FIXTURES / "worker_correction.md").read_text()
    reviewer = (FIXTURES / "reviewer.md").read_text()

    worktree = Path(tempfile.mkdtemp(prefix=f"os37-r10-{run_index}-"))
    subprocess.run(["git", "init", "-q", str(worktree)], check=False,
                   capture_output=True, timeout=60)
    codex_home = tempfile.mkdtemp(prefix=f"os37-r10-codexhome-{run_index}-")
    run_id = f"r10_real_{run_index}_{uuid.uuid4().hex[:6]}"
    steps: list[dict[str, Any]] = []
    result: dict[str, Any] = {"run_index": run_index, "run_id": run_id,
                              "cli_by_role": dict(cli_by_role),
                              "worktree": str(worktree), "steps": steps,
                              "prompt_digests": prompt_digests(), "started_at": _now()}

    def _dispatch(role: str, step: int, prompt: str) -> dict[str, Any]:
        cli = cli_by_role[role]
        row = dispatch(cli=cli, role=role, step=step, prompt=prompt,
                       worktree=str(worktree), artifact_base=artifact_base,
                       run_id=run_id, codex_home=codex_home)
        steps.append(row)
        return row

    # Step 1 -- the WORKER, with the seeded omission in its instruction block.
    _dispatch("worker", 1, f"{contract}\n\n---\n\n{worker_1}")
    result["worker_artifact_present"] = (worktree / "parse_duration.py").exists()

    # Step 2 -- the REVIEWER.  Not told a defect exists, not told a verdict.
    review_path = worktree / "REVIEW.md"
    if review_path.exists():
        review_path.unlink()
    _dispatch("reviewer", 2, f"{contract}\n\n---\n\n{reviewer}")
    step2 = read_verdict(review_path)
    result["step2_verdict"] = step2["verdict"]
    result["step2_review"] = step2.get("body", "")

    # Step 3 -- the CORRECTION, from the reviewer's finding text VERBATIM.
    findings = step2.get("body", "")
    _dispatch("worker", 3, f"{contract}\n\n---\n\n"
                           + worker_2.replace("{findings}", findings))

    # Step 4 -- the FRESH Final Review.  A fresh process with a fresh identity, and a
    # reviewer instruction block BYTE-IDENTICAL to step 2's.
    if review_path.exists():
        review_path.unlink()
    _dispatch("reviewer", 4, f"{contract}\n\n---\n\n{reviewer}")
    step4 = read_verdict(review_path)
    result["step4_verdict"] = step4["verdict"]
    result["step4_review"] = step4.get("body", "")

    result["step2_and_step4_prompts_identical"] = (
        steps[1]["prompt_digest"] == steps[3]["prompt_digest"])
    result["fresh_identity_at_step4"] = (
        steps[1].get("session_id") != steps[3].get("session_id"))
    result["finished_at"] = _now()
    result["outcome"] = classify_run(result)
    try:
        result["final_files"] = sorted(p.name for p in worktree.iterdir()
                                       if p.is_file())
        for name in ("parse_duration.py", "test_parse_duration.py", "WORKER.md",
                     "REVIEW.md"):
            target = worktree / name
            if target.exists():
                result.setdefault("artifacts", {})[name] = target.read_text(
                    encoding="utf-8", errors="replace")
    except OSError:
        pass
    return result


def classify_run(result: dict[str, Any]) -> str:
    """One of FIVE honest outcomes.  Only the first is a pass, and it is not chosen here.

    This function READS the verdicts the production parser already extracted from the
    agents' own artifacts and names the shape of the run.  It cannot produce a verdict: the
    two values it compares were parsed out of files this process did not write.
    """
    two, four = result.get("step2_verdict"), result.get("step4_verdict")
    if two is None or four is None:
        return "UNREADABLE"
    if two.strip().upper() == "PASS":
        # The inducement did not work: an honest outcome to report and re-run with a
        # sharper contract, NEVER patched by having the runtime supply a verdict.
        return "INDUCEMENT_INEFFECTIVE"
    if two.strip().upper() != "FAIL":
        return "UNREADABLE"
    if four.strip().upper() != "PASS":
        return "CORRECTION_INEFFECTIVE"
    return "CONFORMING"


def preflight_cli(cli: str) -> BlockedEvidence | None:
    """A BLOCKED row, or ``None`` when the CLI can actually run.

    Bounded and non-interactive on both legs (D4.0 M-13 measured that both installed CLIs
    offer such a probe).  A CLI that cannot be exercised because of authentication or
    environment is NOT a pass and NOT a skip -- it produces a row naming the exact command
    and the exact bytes.
    """
    binary = shutil.which(cli)
    if binary is None:
        return blocked(cli, binary_realpath="", version_string="",
                       constraint="binary_absent",
                       measurement=f"shutil.which({cli!r})",
                       observed="None -- the binary is not on PATH",
                       exit_code=None)
    version = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=60, check=False)
    probe_argv = ([binary, "auth", "status"] if cli == "claude"
                  else [binary, "login", "status"])
    probe = subprocess.run(probe_argv, capture_output=True, text=True, timeout=120,
                           check=False)
    combined = (probe.stdout + probe.stderr)
    authed = (probe.returncode == 0
              and ('"loggedIn": true' in combined or '"loggedIn":true' in combined
                   or "Logged in" in combined))
    if not authed:
        return blocked(cli, binary_realpath=os.path.realpath(binary),
                       version_string=version.stdout.strip(),
                       constraint="auth_absent",
                       measurement=" ".join(probe_argv),
                       observed=combined.strip() or "<no output>",
                       exit_code=probe.returncode)
    if cli == "codex" and not os.path.exists(os.path.expanduser("~/.codex/auth.json")):
        return blocked(cli, binary_realpath=os.path.realpath(binary),
                       version_string=version.stdout.strip(),
                       constraint="auth_scope_unseeded",
                       measurement="os.path.exists('~/.codex/auth.json')",
                       observed="False -- there is no credential file to seed the "
                                "run-scoped CODEX_HOME with, and D4.0 M-8 measured an "
                                "unseeded root yielding 401 Unauthorized",
                       exit_code=None)
    return None


def roll_up(rows: list[dict[str, Any]],
            blocked_rows: list[BlockedEvidence]) -> dict[str, Any]:
    """D13.6(e).  **The roll-up REFUSES to aggregate.**

    R10 and V-8 PASS only when EVERY declared CLI produced a PASS row.  One BLOCKED row
    means the requirement is NOT MET for that CLI and the overall row reads
    ``PARTIAL — <cli> BLOCKED``, never PASS.  A 2-of-3 result is ``FLAKY`` with all three
    transcripts retained -- reported, not rounded up.
    """
    by_cli: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_cli.setdefault(row["cli_by_role"]["worker"] + "/"
                          + row["cli_by_role"]["reviewer"], []).append(row)
    per_cli: dict[str, Any] = {}
    for label, runs in by_cli.items():
        conforming = [r for r in runs if r["outcome"] == "CONFORMING"]
        # **This is a MATRIX roll-up, not a review verdict, and the two deliberately do not
        # share a name.**  A review verdict is a judgement an agent formed and this process
        # may never author one; a matrix roll-up counts how many runs came out CONFORMING,
        # where `CONFORMING` was itself derived from verdicts the production parser read out
        # of the agents' own artifacts.  `test_os37_standalone_e2e.py::
        # test_the_real_agent_harness_cannot_author_a_verdict` enforces the distinction by
        # failing on any assignment of a PASS/FAIL literal to a verdict-shaped name.
        if len(conforming) == len(runs) and len(runs) >= 1:
            matrix_outcome = "PASS"
        elif conforming:
            matrix_outcome = "FLAKY"
        elif any(r["outcome"] == "INDUCEMENT_INEFFECTIVE" for r in runs):
            matrix_outcome = "INDUCEMENT_INEFFECTIVE"
        else:
            matrix_outcome = "FAIL"
        per_cli[label] = {"matrix_outcome": matrix_outcome, "runs": len(runs),
                          "conforming": len(conforming),
                          "outcomes": [r["outcome"] for r in runs],
                          "step2_verdicts": [r.get("step2_verdict") for r in runs],
                          "step4_verdicts": [r.get("step4_verdict") for r in runs]}
    for row in blocked_rows:
        per_cli[row["cli"]] = {"matrix_outcome": "BLOCKED",
                               "constraint": row["constraint"],
                               "measurement": row["measurement"],
                               "routes_to_design":
                                   row["constraint"] in DESIGN_ROUTING_CONSTRAINTS}
    if blocked_rows:
        overall = "PARTIAL — " + ", ".join(f"{r['cli']} BLOCKED" for r in blocked_rows)
    elif not per_cli:
        overall = "NOT ESTABLISHED"
    elif all(entry["matrix_outcome"] == "PASS" for entry in per_cli.values()):
        overall = "PASS"
    else:
        overall = "PARTIAL — " + ", ".join(
            f"{label} {entry['matrix_outcome']}"
            for label, entry in sorted(per_cli.items())
            if entry["matrix_outcome"] != "PASS")
    return {"overall": overall, "per_cli": per_cli,
            "blocked": [dict(row) for row in blocked_rows]}


def stranger_reread(artifact_base: Path, run_id: str,
                    intent_ids: list[str]) -> dict[str, Any]:
    """D13.6(c) precondition 7: re-read the chain from a SEPARATE INTERPRETER.

    A separate interpreter, not a separate function: this process holds the journal objects,
    the capture buffers and the ledger it just wrote, so re-reading in-process would prove
    that a program can read its own memory.  The subprocess holds NONE of that and starts
    from the files alone, which is exactly the position a Supervisor, an operator or a
    reviewer is in after the Coordinator's turn has ended.

    It re-derives, per intent, that the `DELIVERY_INTENT` exists and carries a prompt digest,
    that a spawn record follows it, and that the run settled -- from the append-only journal
    and nothing else.
    """
    program = textwrap.dedent(
        """
        import json, sys
        sys.path.insert(0, sys.argv[1])
        from scripts.deterministic_workflow import standalone_journal as sj
        base, run_id, intents = sys.argv[2], sys.argv[3], sys.argv[4:]
        journal = sj.ExecutionJournal(base, run_id)
        out = {}
        for intent_id in intents:
            rows = journal.rows_for(intent_id)
            kinds = [r["kind"] for r in rows]
            intent_row = journal.delivery_intent_for(intent_id)
            out[intent_id] = {
                "kinds": kinds,
                "delivery_intent_present": intent_row is not None,
                "prompt_digest": (intent_row or {}).get(
                    "source_vocabulary", {}).get("prompt_digest", ""),
                "intent_precedes_spawn": (
                    "DELIVERY_INTENT" in kinds and "SPAWN_OBSERVED" in kinds
                    and kinds.index("DELIVERY_INTENT") < kinds.index("SPAWN_OBSERVED")),
                "settled": "SETTLEMENT_OBSERVED" in kinds,
                "events": [r["event"] for r in rows if r["event"]],
            }
        print(json.dumps(out))
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", program, str(REPO), str(artifact_base), run_id, *intent_ids],
        capture_output=True, text=True, timeout=180, check=False)
    if completed.returncode != 0:
        return {"ok": False, "error": completed.stderr[-2000:]}
    try:
        return {"ok": True, "per_intent": json.loads(completed.stdout)}
    except ValueError as exc:
        return {"ok": False, "error": f"unparsable: {exc}"}


def verify_retained_evidence(out_dir: Path) -> dict[str, Any]:
    """Re-derive D13.6(e)'s conditions (3) and (5) from the RETAINED evidence alone.

    Run after the matrix so it reads what a reviewer would read, not what this process
    remembers.
    """
    summary_path = out_dir / "R10_REAL_AGENT.json"
    summary = json.loads(summary_path.read_text())
    artifact_base = out_dir / "artifact_base"
    checks: list[dict[str, Any]] = []
    for run in summary["runs"]:
        intent_ids = [step["intent_id"] for step in run["steps"]]
        reread = stranger_reread(artifact_base, run["run_id"], intent_ids)
        every_intent = bool(reread.get("ok")) and all(
            row["delivery_intent_present"] and row["intent_precedes_spawn"]
            and row["prompt_digest"]
            for row in (reread.get("per_intent") or {}).values())
        every_delivery_proved = all(
            step.get("delivery_proof") is not None for step in run["steps"])
        checks.append({
            "run_id": run["run_id"],
            "stranger_reread_ok": bool(reread.get("ok")),
            "every_dispatch_journalled_its_intent_before_its_spawn": every_intent,
            "every_dispatch_constructed_a_typed_delivery_proof": every_delivery_proved,
            "detail": reread.get("per_intent") or reread.get("error"),
        })
    summary["retained_evidence_verification"] = {
        "generated_at": _now(),
        "stranger_interpreter": "a subprocess holding none of the harness's objects, "
                                "reading the append-only journal and nothing else",
        "runs": checks,
        "all_runs_verified": all(
            c["stranger_reread_ok"]
            and c["every_dispatch_journalled_its_intent_before_its_spawn"]
            and c["every_dispatch_constructed_a_typed_delivery_proof"]
            for c in checks) and bool(checks),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary["retained_evidence_verification"]


def main(argv: list[str] | None = None) -> int:
    """Run the matrix and write the evidence.  Never prints or returns a verdict it chose."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    # REQUIRED, with no default (external review #1).  It used to default into
    # `artifacts/runs/run_54d90086bd75/evidence/r10_real_agent` -- one particular untracked
    # run's directory -- so a later invocation silently overwrote another run's evidence and
    # a clean checkout had nowhere for this to mean anything.  Evidence about a real agent
    # run belongs to THAT run, and the operator names it.
    parser.add_argument("--out", required=True,
                        help="the directory this run's real-agent evidence is written to")
    parser.add_argument("--repeats", type=int, default=3,
                        help="R-3: an agent verdict is a distribution, not an event")
    parser.add_argument("--clis", default="claude,codex")
    parser.add_argument("--mixed", action="store_true", default=True)
    parser.add_argument("--no-mixed", dest="mixed", action="store_false")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # The journals live WITH the evidence, not in a temp directory.  R-4 requires the
    # verdict to be re-derivable offline from the retained stream, and D13.6(c) precondition
    # 7 requires the DELIVERY_INTENT -> spawn -> receipt -> settlement chain to be re-read by
    # a stranger interpreter -- neither of which a reviewer can do against a path that was
    # cleaned up when the harness exited.
    artifact_base = out_dir / "artifact_base"
    artifact_base.mkdir(parents=True, exist_ok=True)
    env = orca_free_environment()
    preconditions = assert_orca_free(env)
    # The harness itself runs with the same PATH it hands the children, so `orca` is
    # unreachable to THIS process too for the duration of the matrix.
    os.environ["PATH"] = env["PATH"]
    for name in [n for n in os.environ if n.upper().startswith("ORCA")]:
        os.environ.pop(name, None)

    requested = [name.strip() for name in args.clis.split(",") if name.strip()]
    blocked_rows: list[BlockedEvidence] = []
    runnable: list[str] = []
    for cli in requested:
        row = preflight_cli(cli)
        if row is None:
            runnable.append(cli)
        else:
            blocked_rows.append(row)

    rows: list[dict[str, Any]] = []
    index = 0
    for cli in runnable:
        for _ in range(args.repeats):
            index += 1
            rows.append(four_step_run({"worker": cli, "reviewer": cli},
                                      run_index=index, artifact_base=artifact_base))
            _write(out_dir, rows, blocked_rows, preconditions, env)
    if args.mixed and len(runnable) >= 2:
        index += 1
        rows.append(four_step_run({"worker": runnable[0], "reviewer": runnable[1]},
                                  run_index=index, artifact_base=artifact_base))
        _write(out_dir, rows, blocked_rows, preconditions, env)
    summary = _write(out_dir, rows, blocked_rows, preconditions, env)
    verification = verify_retained_evidence(out_dir) if rows else {}
    print(json.dumps({"overall": summary["roll_up"]["overall"],
                      "per_cli": summary["roll_up"]["per_cli"],
                      "retained_evidence_verified":
                          verification.get("all_runs_verified", False)}, indent=2))
    return 0


def _write(out_dir: Path, rows: list[dict[str, Any]],
           blocked_rows: list[BlockedEvidence], preconditions: dict[str, Any],
           env: dict[str, str]) -> dict[str, Any]:
    """Write the evidence after EVERY run, so a crash mid-matrix loses nothing."""
    summary = {"generated_at": _now(), "preconditions": preconditions,
               "child_env_names": sorted(env), "prompt_digests": prompt_digests(),
               "runs": rows, "roll_up": roll_up(rows, blocked_rows)}
    # Recreated on every write, not just at startup.  A four-step run costs real agent time
    # and real quota, and losing a completed run because the directory went away between
    # runs is exactly the kind of avoidable loss this harness should not have.
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "R10_REAL_AGENT.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    for row in rows:
        run_dir = out_dir / row["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        for step in row["steps"]:
            name = f"step{step['step']}_{step['role']}_{step['cli']}"
            (run_dir / f"{name}.transcript").write_text(
                step.get("transcript", ""), encoding="utf-8", errors="replace")
            (run_dir / f"{name}.json").write_text(json.dumps(
                {k: v for k, v in step.items() if k != "transcript"},
                indent=2, ensure_ascii=False))
        for name, body in (row.get("artifacts") or {}).items():
            (run_dir / f"produced_{name}").write_text(body, encoding="utf-8",
                                                      errors="replace")
    return summary


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
