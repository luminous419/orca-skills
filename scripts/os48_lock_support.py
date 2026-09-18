"""OS-48 lock support: the shared fixture the `test_os48_*` regression / adversarial locks use.

Every lock here drives the PRODUCTION spawn (`standalone_pty.spawn`) and the PRODUCTION
supervisor (`StandaloneSession`) -- the same code path the real CLIs take -- over a real pty,
and orders adversarial events with SEAMS (pipes, hooks, injected returns), never with a sleep
that has to hit a window.  Nothing in this module is production code; it is test-only and
lives in `scripts/` (not mirrored).
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from scripts.deterministic_workflow import standalone_capture as capture_mod
from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow import standalone_pty as pty_supervisor
from scripts.deterministic_workflow import standalone_runtime as rt
from scripts.deterministic_workflow.standalone_profile import (StandaloneProfile,
                                                               profile_from_mapping)

IS_DARWIN = sys.platform == "darwin"
IS_LINUX = sys.platform == "linux"
PYTHON = sys.executable


def sh_profile(room: str, *, drain_ms: int = 3000, binding_mode: str = "single_record_optin",
               binding_field: str = "", carrier_type: str = "") -> StandaloneProfile:
    """A `/bin/sh` fixture profile (the production loader's shape) with the OS-48 completion
    binding declared explicitly."""
    completion = {"channel": "structured", "record_type": "result", "error_field": "is_error",
                  "binding_mode": binding_mode}
    if binding_field:
        completion["binding_field"] = binding_field
    if carrier_type:
        completion["carrier_type"] = carrier_type
    return profile_from_mapping({
        "driver": "claude", "binary": "sh", "supported_range": [[1, 0, 0], [9, 0, 0]],
        "bin_dirs": ["/bin"], "worktree": room,
        "readiness_records": [{"channel": "structured", "record_type": "system",
                               "session_field": "session_id"}],
        "completion_records": [completion],
        "delivery_mode": "post_ready_delivery", "identity_binding": "minted_echo",
        "identity_flag": "--session-id",
        "timeouts": {"post_exit_drain_budget_ms": drain_ms, "physical_exit_timeout_ms": 4000,
                     "completion_timeout_ms": 20000}})


class Room:
    """A temp directory + the sessions/processes it spawned, torn down in `close`."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="os48-lock-")).resolve()
        self.sessions: list[dict[str, Any]] = []
        self.pids: list[int] = []

    def close(self) -> None:
        for s in self.sessions:
            with contextlib.suppress(Exception):
                pty_supervisor.reap_leader(s, timeout_ms=2000)
                pty_supervisor.release(s)
            for pid in (s.get("leader_pid"), s.get("pid")):
                with contextlib.suppress(OSError, TypeError):
                    os.kill(int(pid), signal.SIGKILL)
        for pid in self.pids:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        shutil.rmtree(self.path, True)


def spawn_session(room: Room, agent: str, *, run_id: str, budget_ms: int = 3000,
                  image: str | None = None, argv: list[str] | None = None,
                  binding_mode: str = "single_record_optin", binding_field: str = "",
                  carrier_type: str = "", extra_env: dict[str, str] | None = None,
                  pump_until_sentinel: bool = True, sidecar_path: str = ""):
    """A real `StandaloneSession` wired to a PRODUCTION-spawned pty.  ``agent`` is an `sh`
    script body (or ``argv`` overrides it).  The session's fence nonce is the one the
    watcher will write, exactly as `StandaloneSession.start` does.  Returns
    ``(session, sentinel_path)``; when ``pump_until_sentinel`` the supervisor pumps until the
    watcher's sentinel exists (the exit-proven precondition of `drain_after_exit`)."""
    profile = sh_profile(str(room.path), drain_ms=budget_ms, binding_mode=binding_mode,
                         binding_field=binding_field, carrier_type=carrier_type)
    session = rt.StandaloneSession(
        intent={"intent_id": f"i-{run_id}", "run_id": run_id, "role": "WORKER"},
        profile=profile, artifact_base=room.path / "art", run_id=run_id,
        journal=journal_mod.ExecutionJournal(room.path / "art", run_id))
    cap = str(session.capture.path)
    os.makedirs(os.path.dirname(cap), exist_ok=True)
    base = room.path / "art"
    sentinel = pty_supervisor.exit_sentinel_path(base, run_id, session.session_id,
                                                 session.incarnation)
    os.makedirs(os.path.dirname(sentinel), exist_ok=True)
    env = {"PATH": "/bin:/usr/bin:/usr/local/bin", "OS48_TEST_SID": session.session_id,
           "OS48_ROOM": str(room.path)}
    env.update(extra_env or {})
    if argv is None:
        script = room.path / f"agent-{run_id}.sh"
        script.write_text("#!/bin/sh\n" + agent)
        script.chmod(0o755)
        argv = ["/bin/sh", str(script)]
        image = image or "/bin/sh"
    spawn = pty_supervisor.spawn(
        argv=argv, env=env, profile=profile, session_id=session.session_id,
        incarnation=session.incarnation,
        spawn_record_target=str(pty_supervisor.spawn_record_path(base, run_id, "i",
                                                                 session.incarnation)),
        cwd=str(room.path), sentinel=str(sentinel), fence=session.fence,
        image=image or argv[0], capture=cap, fence_nonce=session.fence_nonce,
        supervisor_identity=session._self_identity(capture_mod.OWNER_SUPERVISOR),
        sidecar_path=sidecar_path)
    if sidecar_path:
        session.last_message_path = sidecar_path
    room.sessions.append(spawn)
    session.pty = spawn
    session.record = {"pid": spawn["pid"], "pgid": spawn["pid"], "sid": spawn["sid"],
                      "captured_tty": os.path.basename(spawn["slave_name"]) if IS_DARWIN
                      else spawn["slave_name"].replace("/dev/", ""),
                      "proc_start_ticks": pty_supervisor.proc_start_ticks(spawn["pid"]),
                      "boot_id": pty_supervisor.host_boot_id(),
                      "session_id": session.session_id,
                      "process_incarnation": session.incarnation}
    if pump_until_sentinel:
        deadline = time.time() + 20
        while not Path(str(sentinel)).exists() and time.time() < deadline:
            session.pump(timeout_ms=20)
        assert Path(str(sentinel)).exists(), "the watcher never wrote the sentinel"
        session.pump(timeout_ms=50)
    return session, Path(str(sentinel))


def sid_record(kind: str, *, is_error: bool, extra: str = "") -> str:
    """An `sh` line writing a `result` record that carries the dispatch's session binding."""
    err = "true" if is_error else "false"
    return ('printf \'{"type":"result","is_error":%s,"session_id":"\'"$OS48_TEST_SID"\'"%s}\\n\'\n'
            % (err, extra))


def fence_of(session) -> dict[str, Any]:
    return capture_mod.read_capture_fence(session._fence_path(), fence=session.fence)


def release_of(session) -> dict[str, Any]:
    return capture_mod.read_release_record(session._release_path(), fence=session.fence)


def settle_rows(session) -> list[dict[str, Any]]:
    return [r for r in session.journal.rows_for(session.intent_id)
            if r.get("kind") == "SETTLEMENT_OBSERVED"]


class KillSpy:
    """Records every user-space signal the code under test tried to send; sends NOTHING."""

    def __init__(self) -> None:
        self.kill: list[tuple[int, int]] = []
        self.killpg: list[tuple[int, int]] = []

    def send_one(self, pid: int, sig: int) -> None:
        self.kill.append((pid, sig))

    def send_group(self, pgid: int, sig: int) -> None:
        self.killpg.append((pgid, sig))


def json_lines(raw: bytes) -> list[dict[str, Any]]:
    out = []
    for line in raw.split(b"\n"):
        seg = line.strip()
        if seg.startswith(b"{"):
            with contextlib.suppress(ValueError):
                out.append(json.loads(seg))
    return out
