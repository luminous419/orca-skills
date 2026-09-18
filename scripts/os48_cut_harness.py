"""OS-48 crash-cut harness (test-only, not mirrored): REAL process kills at DETERMINISTIC seams.

A CUT is a named point in the supervisor's or the watcher's code path.  The harness installs a
PAUSE seam there (a monkeypatched production function that writes ``<room>/cut.<name>`` and then
blocks until it is SIGKILLed), runs the real supervisor in a forked child (or the real watcher
through the production spawn), waits for the cut file -- an acknowledgment, never a sleep --
and kills the process that is paused.  A SUCCESSOR is then built over the same artifact base
through the production masterless path (`drain_after_exit` -> `_fence_from_disk` ->
release recovery -> `completion` -> `_settle`) and the lock asserts the DESIGN §1.7 outcome:
the same N / digest, or the named non-success.  Nothing here forges a record.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Callable

from scripts.deterministic_workflow import standalone_capture as capture_mod
from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow import standalone_pty as pty_supervisor
from scripts.deterministic_workflow import standalone_runtime as rt
from scripts.os48_lock_support import Room, sh_profile, sid_record, spawn_session

SUCCESS = sid_record("result", is_error=False)


def wait_for(predicate: Callable[[], bool], *, seconds: float, what: str) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def wait_dead(pid: int, *, seconds: float = 10.0) -> None:
    """Wait until ``pid`` is gone or a zombie (reaped by whoever is its parent)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                return
        except ChildProcessError:
            pass
        if pty_supervisor._pid_presence(pid) == "absent" or pty_supervisor.proc_start_ticks(pid) == 0:
            return
        time.sleep(0.02)


class Pause:
    """A pause seam: ``mark(tag)`` writes the cut file and blocks the CALLING process forever
    (it is SIGKILLed by the parent).  ``only_pid`` restricts the seam to one process (the
    supervisor child or the watcher); other processes call through to the original."""

    def __init__(self, room: Path) -> None:
        self.room = room

    def file(self, tag: str) -> Path:
        return self.room / f"cut.{tag}"

    def mark_and_block(self, tag: str) -> None:
        self.file(tag).write_text(str(os.getpid()))
        while True:                                  # killed here
            time.sleep(0.05)

    def wrap(self, module: Any, name: str, tag: str, *, when: str = "before",
             in_watcher: bool = False, supervisor_pid: int | None = None,
             predicate: Callable[..., bool] | None = None) -> None:
        real = getattr(module, name)
        sup = supervisor_pid if supervisor_pid is not None else os.getpid()

        def seam(*args, **kwargs):
            me = os.getpid()
            here = (me != sup) if in_watcher else (me == sup)
            if here and (predicate is None or predicate(*args, **kwargs)):
                if when == "before":
                    self.mark_and_block(tag)
                out = real(*args, **kwargs)
                self.mark_and_block(tag)
                return out
            return real(*args, **kwargs)
        setattr(module, name, seam)


def supervisor_child(room: Room, *, run_id: str, cut: str, install: Callable[[Pause, int], None],
                     steps: Callable[[Any], None] | None = None, agent: str = SUCCESS + "exit 0\n",
                     budget_ms: int = 3000, sidecar_path: str = "") -> dict[str, Any]:
    """Fork a REAL supervisor process: it installs the seams (``install(pause, sup_pid)``),
    spawns through the production spawn, writes ``<room>/info.json`` and runs ``steps``
    (default: drain -> release) until a seam blocks it.  Returns the info dict; the child is
    left BLOCKED at its cut for the caller to SIGKILL (`kill_child`)."""
    info_path = room.path / f"info.{run_id}.json"
    pid = os.fork()
    if pid == 0:                                       # pragma: no cover - the child
        try:
            pause = Pause(room.path)
            install(pause, os.getpid())
            session, sentinel = spawn_session(room, agent, run_id=run_id, budget_ms=budget_ms,
                                              binding_mode="session_field", binding_field="session_id",
                                              pump_until_sentinel=False, sidecar_path=sidecar_path)
            info = {"supervisor_pid": os.getpid(), "leader_pid": session.pty["leader_pid"],
                    "agent_pid": session.pty["pid"], "session_id": session.session_id,
                    "incarnation": session.incarnation, "fence": session.fence,
                    "fence_nonce": session.fence_nonce, "capture": str(session.capture.path),
                    "sentinel": str(sentinel), "art": str(room.path / "art"), "run_id": run_id,
                    "control_fd": session.pty["control_fd"]}
            info_path.write_text(json.dumps(info))
            if steps is None:
                deadline = time.time() + 20
                while not Path(str(sentinel)).exists() and time.time() < deadline:
                    session.pump(timeout_ms=20)
                session.pump(timeout_ms=50)
                session.drain_after_exit(budget_ms=budget_ms)
                session._release_two_phase()
            else:
                steps(session)
            info_path.with_suffix(".done").write_text("1")
            while True:
                time.sleep(0.1)                       # keep the fds alive until killed
        except BaseException as exc:  # noqa: BLE001
            with contextlib.suppress(OSError):
                (room.path / f"child_error.{run_id}.txt").write_text(repr(exc))
            os._exit(3)
    room.pids.append(pid)                              # never leaked past the room
    wait_for(lambda: info_path.exists() or (room.path / f"child_error.{run_id}.txt").exists(),
             seconds=20, what="the supervisor child's info")
    if not info_path.exists():
        raise AssertionError("the supervisor child failed: " + (room.path / f"child_error.{run_id}.txt").read_text())
    info = json.loads(info_path.read_text())
    info["child_pid"] = pid
    return info


def kill_child(info: dict[str, Any]) -> None:
    pid = int(info["child_pid"])
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)


def successor(room: Room, info: dict[str, Any], *, budget_ms: int = 3000) -> rt.StandaloneSession:
    """The production MASTERLESS session over the crashed dispatch's artifact base: identity
    from the child-written spawn record (the pinned start identity), no master, the same
    fence nonce.  This is `adopt()`'s outcome without its journal join (the join itself is
    locked by the OS-37 recovery suites)."""
    base = Path(info["art"])
    session = rt.StandaloneSession(
        intent={"intent_id": f"i-{info['run_id']}", "run_id": info["run_id"], "role": "WORKER"},
        profile=sh_profile(str(room.path), drain_ms=budget_ms, binding_mode="session_field",
                           binding_field="session_id"),
        artifact_base=base, run_id=info["run_id"],
        journal=journal_mod.ExecutionJournal(base, info["run_id"]))
    session.session_id = info["session_id"]
    session.incarnation = info["incarnation"]
    session.fence_nonce = info["fence_nonce"]
    session.pty = None
    session.adopted = True
    session._writes_ledger = False
    session.capture = capture_mod.BoundedCapture(Path(info["capture"]), limits=session.profile.capture)
    probe = pty_supervisor.read_spawn_records(base, info["run_id"], "i", incarnation=info["incarnation"])
    record = dict(probe.get("record") or {})
    session.record = {"pid": int(info["agent_pid"]), "pgid": int(info["agent_pid"]),
                      "sid": int(info["leader_pid"]),
                      "proc_start_ticks": int(record.get("proc_start_ticks") or 0),
                      "boot_id": str(record.get("boot_id") or ""),
                      "session_id": info["session_id"], "process_incarnation": info["incarnation"]}
    session.state = "RUNNING"
    return session


def settle(session: rt.StandaloneSession, completion: dict[str, Any] | None = None) -> dict[str, Any]:
    """`await_completion` (or the given completion) + the real `_settle` (reclaim suppressed:
    a masterless session has nothing to reclaim) -> ``{"state", "completion", "event"}``."""
    completion = completion if completion is not None else session.await_completion()
    out = {"state": completion["state"], "completion": completion, "event": None}
    if completion["state"] == "COMPLETED":
        session.intent["command_id"] = "c"
        session.intent["payload_digest"] = "0" * 64
        real = session._reclaim
        session._reclaim = lambda **_kw: {"reaped": True}
        try:
            out["event"] = session._settle(completion["evidence"], lease_token=None,
                                           result_parser=lambda attempt, intent: {"status": "COMPLETE", "body": attempt.body},
                                           verdict=completion["verdict"])
        finally:
            session._reclaim = real
    return out


def orphan_note(info: dict[str, Any]) -> Path:
    return Path(os.fsdecode(pty_supervisor.orphan_note_path(os.fsencode(info["capture"]), info["incarnation"])))


def fence_path(info: dict[str, Any]) -> Path:
    return Path(os.fsdecode(capture_mod.capture_fence_path(os.fsencode(info["capture"]), info["incarnation"])))


def release_path(info: dict[str, Any]) -> Path:
    return Path(os.fsdecode(capture_mod.release_record_path(os.fsencode(info["capture"]), info["incarnation"])))


def owner_dir(info: dict[str, Any]) -> Path:
    return Path(info["capture"]).parent


def read_fence(info: dict[str, Any]) -> dict[str, Any]:
    return capture_mod.read_capture_fence(os.fsencode(str(fence_path(info))), fence=info["fence"])


def read_release(info: dict[str, Any]) -> dict[str, Any]:
    return capture_mod.read_release_record(os.fsencode(str(release_path(info))), fence=info["fence"])


def racing_successors(room: Room, info: dict[str, Any], n: int, action: str = "drain") -> list[dict[str, Any]]:
    """``n`` CONCURRENT successor processes over the same crashed dispatch, released together by
    a pipe barrier; each reports its drain outcome / release recovery.  Exactly-one is the
    caller's assertion."""
    go_r, go_w = os.pipe()
    results: list[tuple[int, int]] = []
    for i in range(n):
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:                                   # pragma: no cover - racer
            os.close(r)
            os.close(go_w)
            os.read(go_r, 1)
            try:
                s = successor(room, info)
                drained = s.drain_after_exit(budget_ms=3000)
                report = {"i": i, "finality": drained.get("finality"), "outcome": drained.get("outcome"),
                          "offset_n": drained.get("offset_n"), "release": (s._release or {}).get("state"),
                          "release_by": (s._release or {}).get("recovered_by"),
                          "release_r": (s._release or {}).get("offset_r"),
                          "owner": ((drained.get("fence") or {}).get("owner") or {}).get("generation")}
                if action == "settle":
                    report["settled"] = settle(s)["state"]
            except BaseException as exc:  # noqa: BLE001
                report = {"i": i, "error": repr(exc)}
            os.write(w, json.dumps(report, default=str).encode())
            os._exit(0)
        os.close(w)
        results.append((pid, r))
    os.close(go_r)
    for _ in range(n):
        os.write(go_w, b"g")
    os.close(go_w)
    out = []
    for pid, r in results:
        chunks = b""
        while True:
            chunk = os.read(r, 65536)
            if not chunk:
                break
            chunks += chunk
        os.close(r)
        os.waitpid(pid, 0)
        out.append(json.loads(chunks or b"{}"))
    return out
