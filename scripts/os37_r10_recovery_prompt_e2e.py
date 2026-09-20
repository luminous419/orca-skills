"""OS-37 round-7 blocker 2 -- the RECOVERY-PROMPT E2E, through the production launcher +
Graph, with `orca` unresolvable on PATH, for a REAL crash AFTER the first dispatch settles.

    python3 -m scripts.os37_r10_recovery_prompt_e2e <out_dir> --cli {fixture,claude,codex}

What it proves (iteration 2, addressing REVIEW_BUGFIX findings 3/10):

  1. A REAL supervisor process launches the run through the production
     `build_standalone_adapter` -> `execute_state` -> LangGraph graph path and runs
     **Worker iteration 1 to a committed settlement** against the selected driver -- the
     native `os37-graph-agent` fixture, or the REAL `claude` / `codex` CLI.  It then stalls
     deterministically (`interrupt_after=["APPLY_RESULT"]`) and the PARENT **SIGKILLs it** --
     a real crash of a real supervisor after the first dispatch settled.
  2. The PARENT recovers the stalled run through the production **watchdog** wiring
     (`recover`), in-process, and the recovered graph dispatches the NEXT intents (Phase
     Reviewer i1 -> correction Worker -> Phase Reviewer i2 -> Final Reviewer) to the SAME
     real driver, to a COMPLETED terminal.
  3. Every delivered prompt of every real turn is captured -- the composer's rendering,
     verified against the runtime's OWN structured delivery record (the `DELIVERY_INTENT`
     journal row's `prompt_digest`), so the captured text is provably the delivered text
     and NOT a fixture dump.  The recovered Phase-Reviewer prompt is **byte-equal** to the
     pre-crash rendering the launch composer produced, and the correction Worker prompt
     carries the objective, the output contract AND the correction instruction.

Iteration 5 (B1, durable worktree resolution): the profile declares the RELATIVE worktree
``wt``; the launch supervisor is started from ``<out>/launch_cwd`` (where ``wt`` lives) and
the recovery is a SEPARATE watchdog process started from ``<out>/recovery_cwd`` (which holds
no ``wt``).  Both cwds, the archived worktree the launch bound and the worktree / cwd flag the
recovered runtime composed are recorded in the evidence, so the recovery is proven to rebuild
the LAUNCH-time absolute worktree and never ``<recovery_cwd>/wt``.

Round-8 item 3 (omitted worktree): with ``--worktree omitted`` the profile names NO
worktree at all; the launch runs from ``<out>/launch_cwd`` (which the frozen profile then
binds as the absolute worktree) and the recovery from ``<out>/recovery_cwd``.  The evidence
records that the archived, launch-time and recovered worktrees are all ``launch_cwd`` and
that the recovery process never ran an agent in its own cwd.

`orca` is removed from PATH and every `ORCA_*` name stripped, asserted with a raise, in both
the launch subprocess and the recovery process.  For `--cli codex` the run-scoped
`CODEX_HOME` is seeded 0600 from `~/.codex/auth.json` by the production preflight; the seed
lives OUTSIDE the evidence tree and is never retained.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.deterministic_workflow import launcher  # noqa: E402
from scripts.deterministic_workflow import standalone_drivers as drivers  # noqa: E402
from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore  # noqa: E402
from scripts.os37_r10_graph_prompt_e2e import OBJECTIVE, ROLE_INSTRUCTIONS  # noqa: E402
from scripts.os37_r10_real_agent import (  # noqa: E402
    assert_orca_free, claude_profile, codex_profile, orca_free_environment,
    profile_document)


# ---- profiles per driver ----------------------------------------------------------------
def _graph_agent_bin() -> str:
    from scripts import os37_graph_agent_fixture as graph_fixture
    built = graph_fixture.native_agent_dir()
    if built is None:
        raise SystemExit("BLOCKED: os37-graph-agent could not be built (no C compiler)")
    return str(built)


def build_profile(cli: str, worktree: str, *, codex_home: str = "",
                  launch_cwd: str = ".") -> dict[str, Any]:
    """``worktree=""`` builds a profile that OMITS the worktree key entirely (round-8
    item 3); the launch composition then freezes it to the launch cwd."""
    profile = _build_profile(cli, worktree, codex_home=codex_home, launch_cwd=launch_cwd)
    if not worktree:
        profile.pop("worktree", None)
    return profile


def _build_profile(cli: str, worktree: str, *, codex_home: str = "",
                   launch_cwd: str = ".") -> dict[str, Any]:
    if cli == "fixture":
        return {
            "driver": "claude", "binary": "os37-graph-agent",
            "supported_range": [[1, 0, 0], [3, 0, 0]], "bin_dirs": [_graph_agent_bin()],
            "worktree": worktree,
            "readiness_records": [{"channel": "structured", "record_type": "system",
                                   "session_field": "session_id"}],
            "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
            "identity_flag": "--session-id",
            "delivery_proofs": [{"channel": "structured", "record_type": "assistant"}],
            "completion_records": [{"channel": "structured", "record_type": "result",
                                    "error_field": "is_error",
                                    "success_field": "terminal_reason",
                                    "success_values": ["completed"],
                                    "binding_mode": "single_record_optin"}],
            "result_body_records": [{"channel": "structured", "record_type": "result",
                                     "body_field": "result"}],
            "driver_env": {"OS37_GA_BODY_IN_RESULT": "1", "OS37_GA_FAIL_PHASE": "DESIGN"},
            "auth_probe": {"args": ["auth", "status"]},
            "timeouts": {"preflight_timeout_ms": 4000, "readiness_timeout_ms": 20000,
                         "delivery_verify_timeout_ms": 10000, "completion_timeout_ms": 20000},
        }
    if cli == "claude":
        # `--add-dir` rides `extra_args`, which the runtime composes VERBATIM (it is not a
        # worktree-derived flag), so the harness gives it the absolute launch-time path;
        # the profile's own `worktree` stays the relative spec under test.
        return profile_document(claude_profile(
            worktree, extra_args=("--strict-mcp-config", "--add-dir",
                                  os.path.abspath(os.path.join(launch_cwd, worktree or ".")))))
    if cli == "codex":
        return profile_document(codex_profile(worktree, codex_home))
    raise SystemExit(f"unknown --cli {cli!r}")


# ---- the composer tee (harness instrumentation; touches no production file) --------------
def _install_composer_tee(dump_dir: Path) -> None:
    """Wrap `standalone_recovery_composition` so the recovered adapter's prompt composer
    tees each rendered prompt to ``dump_dir/<n>_<role>.txt`` -- the composer's OWN output,
    later verified against the journal's `DELIVERY_INTENT` digest."""
    dump_dir.mkdir(parents=True, exist_ok=True)
    real = launcher.standalone_recovery_composition
    counter = {"n": 0}

    def wrapped(base, run_id, *, thread_id, ledger, pause_row_journal, profile_override=None):
        adapter, journal, port = real(base, run_id, thread_id=thread_id, ledger=ledger,
                                      pause_row_journal=pause_row_journal,
                                      profile_override=profile_override)
        _tee_adapter_composer(adapter, dump_dir, "recover", counter)
        return adapter, journal, port

    launcher.standalone_recovery_composition = wrapped


def _tee_adapter_composer(adapter: Any, dump_dir: Path, prefix: str,
                          counter: dict[str, int] | None = None) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    composer = getattr(adapter, "_prompt_composer", None)
    if composer is None:
        return
    counter = counter if counter is not None else {"n": 0}

    def teed(intent, _c=composer):
        text = _c(intent)
        counter["n"] += 1
        role = str(intent.get("role") or "?")
        (dump_dir / f"{prefix}_{counter['n']:02d}_{role}.txt").write_text(text)
        return text
    adapter._prompt_composer = teed


# ---- the launch subprocess (a REAL supervisor that runs Worker i1 then stalls) -----------
def _run_launch_child(cfg: dict[str, Any]) -> int:
    base = Path(cfg["base"])
    run_id = cfg["run_id"]
    # This subprocess must itself be orca-free.
    assert_orca_free({k: v for k, v in os.environ.items()})
    ledger = FileRuntimeStateStore(Path(cfg["ledger"]))
    profile = json.loads(Path(cfg["profile"]).read_text())
    composition = launcher.prompt_composition_record(
        OBJECTIVE, requested_phases=("DESIGN",), risk="high", project_root=REPO,
        role_instructions=ROLE_INSTRUCTIONS)
    adapter, state = launcher.build_standalone_adapter(
        {"run_id": run_id, "thread_id": cfg["thread_id"], "phases": ["DESIGN"],
         "max_iterations": 4}, artifact_base=base, run_id=run_id,
        runtime_state=ledger, profile_spec=profile, prompt_composition=composition)
    _tee_adapter_composer(adapter, Path(cfg["launch_dump"]), "launch")
    # Iteration 5 (B1): the launch process's own account of where it ran and what it froze.
    Path(cfg["launch_facts"]).write_text(json.dumps({
        "launch_cwd": os.getcwd(),
        "profile_worktree_spec": profile.get("worktree", "<omitted>"),
        "profile_worktree_omitted": "worktree" not in profile,
        "launch_worktree": adapter.runtime.profile.worktree,
    }, indent=2))
    final = launcher.execute_state(
        state, adapter=adapter, runtime_state=ledger,
        journal=launcher._standalone_pause_row_journal(base, run_id),
        artifact_base=base, interrupt_after=["APPLY_RESULT"],
        recursion_limit=200, audit_sink=None)
    Path(cfg["marker"]).write_text(json.dumps({
        "terminal_status": final.get("terminal_status"),
        "pending_intent": final.get("pending_intent"),
    }, default=str))
    time.sleep(3600)                                     # alive-but-stalled; parent SIGKILLs
    return 0


# ---- the recovery subprocess (a REAL, separate watchdog process from its OWN cwd) -------
def _run_recover_child(cfg: dict[str, Any]) -> int:
    """Iteration 5 (B1).  The recovery used to run in the parent's process (and cwd); it
    is now a separate process started from ``recovery_cwd``, so a worktree the archive held
    relative would be re-interpreted HERE, against a cwd that holds no ``wt``."""
    assert_orca_free({k: v for k, v in os.environ.items()})
    base = Path(cfg["base"])
    run_id = cfg["run_id"]
    facts: dict[str, Any] = {"recovery_cwd": os.getcwd()}
    _install_composer_tee(Path(cfg["recover_dump"]))
    real = launcher.standalone_recovery_composition

    def observed(*args: Any, **kwargs: Any):
        adapter, journal, port = real(*args, **kwargs)
        profile = adapter.runtime.profile
        facts["recovered_worktree"] = profile.worktree
        facts["recovered_add_dirs"] = list(profile.add_dirs)
        argv = list(drivers.driver_for(profile).argv(session_id="probe", prompt="probe"))
        facts["recovered_argv_cwd_flags"] = {
            flag: argv[argv.index(flag) + 1] for flag in ("-C", "--add-dir") if flag in argv}
        # The flag a driver composes from the WORKTREE itself: codex's `-C`; claude runs in
        # the cwd and carries `--add-dir` verbatim from `extra_args`.
        facts["recovered_cwd_flag"] = facts["recovered_argv_cwd_flags"].get(
            "-C", profile.worktree)
        return adapter, journal, port
    launcher.standalone_recovery_composition = observed
    out, err = _sio(), _sio()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = launcher.run_watchdog_cli(
            ["recover", "--run-id", run_id, "--artifact-base", str(base),
             "--adapter", "standalone", "--recursion-limit", "200", "--json"])
    Path(cfg["recover_stdout"]).write_text(out.getvalue())
    Path(cfg["recover_stderr"]).write_text(err.getvalue())
    facts["recover_exit"] = code
    Path(cfg["recover_facts"]).write_text(json.dumps(facts, indent=2))
    return 0


# ---- evidence readers -------------------------------------------------------------------
def _journal_delivery_digests(base: Path, run_id: str) -> dict[str, str]:
    from scripts.deterministic_workflow.standalone_journal import journal_path
    path = journal_path(base, run_id)
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().split("\n"):          # the protocol delimiter only
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("kind") != "DELIVERY_INTENT":
            continue
        digest = (row.get("source_vocabulary") or {}).get("prompt_digest")
        if digest:
            out[str(row.get("intent_id"))] = str(digest)
    return out


def _settlement_shape(base: Path, run_id: str) -> list[dict[str, Any]]:
    from scripts.deterministic_workflow.standalone_journal import journal_path
    path = journal_path(base, run_id)
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text().split("\n"):          # the protocol delimiter only
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("kind") != "SETTLEMENT_OBSERVED":
            continue
        result = ((row.get("source_vocabulary") or {}).get("event") or {}).get("result") or {}
        out.append({"status": result.get("status"), "result": result.get("result")})
    return out


def _pending_reviewer_intent(base: Path, run_id: str) -> dict[str, Any] | None:
    """The intent the recovered graph dispatches next, reconstructed off the committed head:
    after APPLY_RESULT of Worker i1, the router dispatches the PHASE_REVIEWER for the design
    phase at the current gate iteration."""
    from scripts.deterministic_workflow import recovery_runtime
    head = recovery_runtime.resolve_head(run_id, artifact_base=base)
    if head is None:
        return None
    state = head.state
    return {"intent_id": "pending-reviewer", "run_id": run_id, "task_id": "t",
            "role": "PHASE_REVIEWER", "phase": "DESIGN",
            "gate_iteration": int(state.get("gate_iteration") or 1),
            "round_kind": "PHASE_GATE", "repair_instruction": None}


def _digest(text: str) -> str:
    return drivers.prompt_digest(text)


def _head_terminal(base: Path, run_id: str) -> Any:
    from scripts.deterministic_workflow import recovery_runtime
    try:
        head = recovery_runtime.resolve_head(run_id, artifact_base=base)
    except Exception as exc:  # noqa: BLE001
        return f"unreadable: {type(exc).__name__}"
    return None if head is None else head.state.get("terminal_status")


def _loop_completed(shape: list[dict[str, Any]]) -> bool:
    verdicts = [row.get("result") for row in shape]
    fails = [i for i, v in enumerate(verdicts) if v == "FAIL"]
    if not fails:
        return False
    return any(v == "PASS" and i > fails[0] for i, v in enumerate(verdicts))


def _sio():
    import io
    return io.StringIO()


# ---- the orchestration ------------------------------------------------------------------
def run(out_dir: Path, cli: str, *, timeout_s: float = 2400.0,
        worktree_mode: str = "relative") -> dict[str, Any]:
    # ABSOLUTE, so the worktree and every derived path name one directory regardless of the
    # process cwd -- the launch subprocess and the in-process recovery resolve identically.
    # (The production runtime also resolves a relative worktree now; this keeps the harness's
    # own evidence paths absolute.)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / "artifact_base"
    # Iteration 5 (B1): the profile names the RELATIVE worktree `wt`; the launch runs from
    # `launch_cwd` (which holds it) and the recovery from `recovery_cwd` (which does not).
    launch_cwd = out_dir / "launch_cwd"
    recovery_cwd = out_dir / "recovery_cwd"
    # Round-8 item 3: `omitted` names NO worktree; the launch cwd IS the worktree.
    worktree_spec = "wt" if worktree_mode == "relative" else ""
    worktree = launch_cwd / worktree_spec if worktree_spec else launch_cwd
    launch_dump = out_dir / "launch_delivered"
    recover_dump = out_dir / "recover_delivered"
    for path in (base, worktree, recovery_cwd, launch_dump, recover_dump):
        path.mkdir(parents=True, exist_ok=True)
    codex_home = tempfile.mkdtemp(prefix="os37-codexhome-") if cli == "codex" else ""
    run_id = f"run_r7rec{cli}"
    thread_id = "t"

    env = orca_free_environment()
    precondition = assert_orca_free(env)
    (out_dir / "preconditions.json").write_text(json.dumps(precondition, indent=2))
    profile = build_profile(cli, worktree_spec, codex_home=codex_home,
                            launch_cwd=str(launch_cwd))

    def _plain(value: Any) -> Any:                        # a real CLI profile carries bytes
        if isinstance(value, bytes):
            return value.decode("utf-8", "surrogateescape")
        raise TypeError(type(value).__name__)
    (out_dir / "profile.json").write_text(json.dumps(profile, indent=2, default=_plain))
    ledger_path = out_dir / "ledger.json"
    marker = out_dir / "stalled.marker"

    child_cfg = {"base": str(base), "run_id": run_id, "thread_id": thread_id,
                 "ledger": str(ledger_path), "profile": str(out_dir / "profile.json"),
                 "launch_dump": str(launch_dump), "marker": str(marker),
                 "launch_facts": str(out_dir / "launch_facts.json")}
    cfg_path = out_dir / "child_cfg.json"
    cfg_path.write_text(json.dumps(child_cfg))
    # Both subprocesses run this file BY PATH (it puts the repository on `sys.path`
    # itself), so neither needs the repository as its cwd -- the cwd is the variable.
    this_file = str(Path(__file__).resolve())

    # No ORCA_OS40_* env: those names are ORCA-namespaced and the orca-free precondition
    # forbids them.  The launch child and the recovery both use the artifact-base default
    # checkpoint store (`resolve_checkpoint_path(..., artifact_base=base)`) and the ledger
    # the launch authority records, so both halves address the SAME durable files.
    child_env = dict(env)

    # (1) the REAL launch supervisor: Worker i1 -> committed settlement -> stall.
    # The child's stderr is written to a file (never a PIPE that fills and is discarded on
    # SIGKILL), so a launch that settles anything other than COMPLETE is diagnosable.
    child_log = open(out_dir / "launch_child_stderr.txt", "wb")
    child = subprocess.Popen(
        [sys.executable, this_file, "--child", str(cfg_path)],
        env=child_env, cwd=str(launch_cwd), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=child_log)
    started = time.time()
    stalled: dict[str, Any] = {}
    try:
        while not marker.exists():
            if child.poll() is not None:
                child_log.flush()
                se = (out_dir / "launch_child_stderr.txt").read_text(
                    encoding="utf-8", errors="replace")
                return {"cli": cli, "outcome": "blocked", "stage": "launch",
                        "reason": "the launch supervisor exited before stalling",
                        "child_rc": child.returncode, "stderr": se[-3000:]}
            if time.time() - started > timeout_s:
                child.kill()
                return {"cli": cli, "outcome": "blocked", "stage": "launch",
                        "reason": "the launch supervisor did not stall in time"}
            time.sleep(0.5)
        stalled = json.loads(marker.read_text())
        # (2) the CRASH: SIGKILL the real supervisor after Worker i1 settled.
        os.kill(child.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()

    launch_settlements = _settlement_shape(base, run_id)
    if not launch_settlements:
        return {"cli": cli, "outcome": "blocked", "stage": "launch",
                "reason": "Worker i1 did not settle before the crash", "stalled": stalled}

    # The pre-crash rendering of the next (Phase Reviewer) dispatch, from the launch composer.
    pre_crash_intent = _pending_reviewer_intent(base, run_id)
    pre_crash_prompt = None
    if pre_crash_intent is not None:
        authority = launcher.load_standalone_authority(base, run_id, thread_id) or {}
        composition = launcher.load_standalone_prompt_composition(
            base, run_id, digest=authority.get("prompt_composition_digest", ""))
        pre_crash_prompt = launcher.composer_from_composition(composition)(pre_crash_intent)
        (out_dir / "pre_crash_reviewer_prompt.txt").write_text(pre_crash_prompt)

    launch_facts: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        launch_facts = json.loads((out_dir / "launch_facts.json").read_text())
    authority = launcher.load_standalone_authority(base, run_id, thread_id) or {}
    archived_worktree = ""
    with contextlib.suppress(Exception):
        archived_worktree = str(launcher.load_standalone_profile(
            base, run_id, digest=authority.get("profile_digest", "")).get("worktree", ""))

    # (3) RECOVERY through the production watchdog wiring in a SEPARATE process started
    # from `recovery_cwd`, same real driver.
    recover_cfg = {"base": str(base), "run_id": run_id,
                   "recover_dump": str(recover_dump),
                   "recover_stdout": str(out_dir / "recover_stdout.json"),
                   "recover_stderr": str(out_dir / "recover_stderr.txt"),
                   "recover_facts": str(out_dir / "recover_facts.json")}
    recover_cfg_path = out_dir / "recover_cfg.json"
    recover_cfg_path.write_text(json.dumps(recover_cfg))
    recover_proc = subprocess.run(
        [sys.executable, this_file, "--recover", str(recover_cfg_path)],
        env=child_env, cwd=str(recovery_cwd), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s)
    (out_dir / "recover_child_stderr.txt").write_bytes(recover_proc.stderr or b"")
    recover_facts: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        recover_facts = json.loads((out_dir / "recover_facts.json").read_text())
    code = recover_facts.get("recover_exit", recover_proc.returncode or -1)
    summary: dict[str, Any] = {}
    stdout_text = ""
    with contextlib.suppress(Exception):
        stdout_text = (out_dir / "recover_stdout.json").read_text()
    if stdout_text.strip():
        with contextlib.suppress(Exception):
            summary = json.loads(stdout_text.strip().splitlines()[-1])

    # (4) assertions, from the runtime's OWN structured delivery records.
    digests = _journal_delivery_digests(base, run_id)
    recovered_prompts = {p.name: p.read_text() for p in sorted(recover_dump.glob("*.txt"))}
    launch_prompts = {p.name: p.read_text() for p in sorted(launch_dump.glob("*.txt"))}
    all_prompts = list(launch_prompts.values()) + list(recovered_prompts.values())
    journalled = set(digests.values())
    captured_verified = [t for t in all_prompts if _digest(t) in journalled]
    reviewer_recovered = [t for t in recovered_prompts.values()
                          if "You are the PHASE_REVIEWER" in t]
    correction_recovered = [t for t in recovered_prompts.values()
                            if "A reviewer read your previous submission" in t]
    byte_equal = bool(pre_crash_prompt and any(
        t.strip() == pre_crash_prompt.strip() for t in reviewer_recovered))
    correction_carries = any(
        OBJECTIVE.split(".")[0] in t
        and ROLE_INSTRUCTIONS["WORKER:CORRECTION"].split(":")[0] in t
        and '"payload_digest"' not in t
        for t in correction_recovered)
    final_shape = _settlement_shape(base, run_id)

    launch_worktree = str(launch_facts.get("launch_worktree") or "")
    record: dict[str, Any] = {
        "cli": cli,
        "outcome": "ran",
        "worktree_mode": worktree_mode,
        "profile_worktree_omitted": bool(launch_facts.get("profile_worktree_omitted")),
        "orca_free": precondition,
        # Iteration 5 (B1): the two cwds and what each process made of the worktree.
        "launch_cwd": str(launch_facts.get("launch_cwd") or ""),
        "recovery_cwd": str(recover_facts.get("recovery_cwd") or ""),
        "profile_worktree_spec": str(launch_facts.get("profile_worktree_spec") or ""),
        "launch_worktree": launch_worktree,
        "archived_worktree": archived_worktree,
        "recovered_worktree": str(recover_facts.get("recovered_worktree") or ""),
        "recovered_cwd_flag": str(recover_facts.get("recovered_cwd_flag") or ""),
        "recovered_argv_cwd_flags": recover_facts.get("recovered_argv_cwd_flags"),
        "recovery_cwd_worktree_exists": ((recovery_cwd / worktree_spec).exists()
                                         if worktree_spec else None),
        "recovery_process": "separate watchdog subprocess (recover verb)",
        "launch_worker_settled": bool(launch_settlements),
        "crash": "SIGKILL after APPLY_RESULT of Worker i1 (real supervisor subprocess)",
        "recover_exit": code,
        "recover_status": summary.get("status"),
        "terminal_status_after_recover": _head_terminal(base, run_id),
        "delivered_intents_journalled": len(digests),
        "captured_prompts": len(all_prompts),
        "captured_prompts_verified_against_journal_digest": len(captured_verified),
        "all_captured_verified": bool(all_prompts) and len(captured_verified) == len(all_prompts),
        "recovered_reviewer_prompt_byte_equal_to_pre_crash": byte_equal,
        "correction_prompt_carries_objective_and_instruction": correction_carries,
        "loop_shape": final_shape,
    }
    expected_worktree = (os.path.join(record["launch_cwd"], worktree_spec)
                         if worktree_spec else record["launch_cwd"])
    record["expected_worktree"] = expected_worktree
    record["worktree_durable_across_cwds"] = bool(
        launch_worktree and os.path.isabs(launch_worktree)
        and record["launch_cwd"] and record["recovery_cwd"]
        and record["launch_cwd"] != record["recovery_cwd"]
        and launch_worktree == expected_worktree
        and record["archived_worktree"] == launch_worktree
        and record["recovered_worktree"] == launch_worktree
        and record["recovered_cwd_flag"] == launch_worktree
        and not record["recovery_cwd_worktree_exists"]
        and (worktree_mode == "relative" or record["profile_worktree_omitted"]))
    record["recovery_prompt_established"] = bool(
        code == 0 and summary.get("status") == "RECOVERED"
        and record["terminal_status_after_recover"] == "COMPLETED"
        and record["all_captured_verified"] and byte_equal and correction_carries
        and _loop_completed(final_shape) and record["worktree_durable_across_cwds"])
    if codex_home:
        shutil.rmtree(codex_home, ignore_errors=True)     # never retain the seed
    return record


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--child":
        return _run_launch_child(json.loads(Path(args[1]).read_text()))
    if args and args[0] == "--recover":
        return _run_recover_child(json.loads(Path(args[1]).read_text()))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir")
    parser.add_argument("--cli", default="fixture", choices=("fixture", "claude", "codex"))
    parser.add_argument("--timeout-s", type=float, default=2400.0)
    parser.add_argument("--worktree", default="relative", choices=("relative", "omitted"),
                        help="relative: the profile names the relative worktree `wt`; "
                             "omitted: the profile names NO worktree (round-8 item 3)")
    ns = parser.parse_args(args)
    record = run(Path(ns.out_dir), ns.cli, timeout_s=ns.timeout_s,
                 worktree_mode=ns.worktree)
    (Path(ns.out_dir) / "RECOVERY_PROMPT_EVIDENCE.json").write_text(
        json.dumps(record, indent=2, default=str))
    print(json.dumps(record, indent=2, default=str))
    return 0 if record.get("recovery_prompt_established") else 1


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
