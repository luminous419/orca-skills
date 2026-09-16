"""OS-37 N4.  The headless POSIX PTY / process supervisor.

**No Terminal.app.  No iTerm.  No window.**  The only pty in this runtime is created by
``pty.openpty()`` inside this process, and the child is ``fork``ed here and never handed to
a terminal emulator.  That is the ticket's first hard constraint, and it is satisfied by
construction rather than by policy: there is no code path in this module that could open a
window, because opening one requires a primitive this module does not call.

Two things happen in the forked child that are worth reading twice:

*The spawn record*, written by the child as the LAST instruction before ``execve``.  It
exists for exactly one reason: so :meth:`lookup` can prove ABSENCE.  Because the write
precedes the exec, a child that died before the write also died before the exec, so no CLI
ran and no external effect exists.  **It is not a claim** -- the durable pre-effect claim is
``runtime_state.claim``, taken by the executor before ``adapter.start`` is even called, and
the spawn record is written *after* it, by the child, excludes nobody, mints nothing, holds
no fence value, and is read by exactly one caller (DESIGN D3.4a).

*Descriptor closure*, ``os.closerange(3, MAXFD)``.  This is half the nested-CLI isolation
story: ``CLAUDE_CODE_MESSAGING_SOCKET`` names a path that :mod:`standalone_env` scrubs, but
an inherited OPEN socket fd would be a second channel with the name gone.

Ownership discovery is tty-scoped (``ps -t <tty>``), never ``ps -ax``, and every signal
re-derives the process group from the table rather than trusting one remembered in memory.
Four refusals gate it, and each one sends NO signal.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import termios
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypedDict

from . import standalone_capture as capture_mod
from . import standalone_identity as identity
from .standalone_profile import CaptureLimits, StandaloneProfile

# ---- the four ownership refusals (AC-37-02) --------------------------------------------
#: Each one results in NO signal being sent, except R-OWN-2 which downgrades a group signal
#: to a root-scoped one (signalling the supervisor's own group would be suicide) and R-OWN-4
#: which re-scans rather than serving a stale snapshot.
OWNERSHIP_REFUSALS = ("unbound_tty", "tty_shared_with_driver", "captured_tty_mismatch",
                      "stale_snapshot")

#: Kill ordering (`docs/ORCA_RUNTIME_PRIMITIVES.md` C4): descendant groups FIRST, the PTY
#: session leader LAST, so the leader cannot reap its children before they are signalled.
#: The spawn topology makes this a real ordering rather than a no-op: the session leader is
#: the exit WATCHER and the agent CLI is its child in a separate, FOREGROUND process group
#: (DESIGN risk DR-1).  The leader must outlive the agent, because the leader is what
#: `waitpid`s it and writes the exit sentinel a stranger process later reads; signalling it
#: first would destroy the exit evidence.  `session_leader` is therefore derived from the
#: record's `sid` -- a session leader's pgid IS its sid -- and never from the agent's pgid.
#:
#: **And when the leader IS the exit watcher it is not signalled at all** (consolidated
#: review finding 12).  "Last" was still "too early": a SIGTERM/SIGKILL delivered to the
#: watcher's group in the same rung as the agent's could kill the watcher BEFORE its
#: `waitpid` returned, so the agent's exit sentinel was never written and proof-of-death
#: was defeated by the very ladder meant to produce it.  The watcher needs no signal: it
#: blocks in `waitpid` and exits by itself the instant the agent is reaped.  A record whose
#: `sid` equals its own `pid` (no separate watcher -- the pre-topology shape) keeps the
#: old two-rung ordering, because there the leader IS the agent.
KILL_ORDER = ("descendant_groups", "session_leader")

#: The ps keywords, in order.  `sess` is the portable spelling of the session
#: id; `sid` is procps-only and darwin refuses it by name.
_PS_FIELDS = ("pid", "ppid", "pgid", "sess", "tty", "stat")
_PS_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*$")


class PtyRefused(RuntimeError):
    """A pty/process action was refused by name.  Never a silent no-op."""


class ProcessTableUnreadable(RuntimeError):
    """The process table could not be read, so liveness is UNKNOWN.

    Distinct from "the process is gone".  A caller that conflates them reports a live agent
    as exited, which is the failure this whole module is shaped to prevent.
    """


class SpawnRecord(TypedDict):
    """The child's own evidence that an ``execve`` happened.  NOT a claim.

    Written by the child, after ``fork``, before ``execve``, and after the durable
    pre-effect claim already exists.  Its four non-authority properties (DESIGN D3.4a) are:
    it is written after the claim; it excludes nobody; it mints nothing; and it is read by
    exactly one caller (``lookup``), never by ``assert_may_act``.
    """

    session_id: str
    process_incarnation: str
    pid: int
    pgid: int
    sid: int
    boot_id: str
    proc_start_ticks: int
    argv_digest: str
    env_digest: str
    started_at: str


class ProcessRow(TypedDict):
    pid: int
    ppid: int
    pgid: int
    sid: int
    tty: str
    stat: str


class ProcessTableSnapshot(TypedDict):
    """A tty-scoped process-table read, stamped BEFORE the scan started.

    Stamped before rather than after on purpose: a snapshot's age must be at least its true
    age.  Stamping it afterwards would make a slow scan look fresh, which is the one
    direction that matters -- "stale PIDs are unsafe to signal"
    (`docs/ORCA_RUNTIME_PRIMITIVES.md` C28).
    """

    tty: str
    captured_at: float
    rows: tuple[ProcessRow, ...]
    readable: bool


# ---- host identity ---------------------------------------------------------------------
def boot_id() -> str:
    """A host boot identity, so a pid recycled ACROSS a reboot is detectable.

    **Called in the PARENT, before the fork.**  It used to run ``sysctl`` through
    ``subprocess`` and was called from inside the forked child -- which is unsafe: between
    ``fork()`` and ``execve()`` a child shares the parent's address space with none of its
    threads, so forking further processes and running Python's subprocess machinery there can
    wedge the child.  It is a host constant, so the parent can read it once and pass it in.

    Best-effort by design: where no boot identity is readable this returns the empty string,
    and :func:`standalone_identity.verify` then simply does not use that axis -- it never
    substitutes a fabricated value, because a fabricated boot id would make a recycled pid
    look like the original.
    """
    for path in ("/proc/sys/kernel/random/boot_id",):
        try:
            return Path(path).read_text().strip()
        except OSError:
            pass
    try:  # darwin
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=5, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def proc_start_ticks(pid: int) -> int:
    """The kernel's own start time for ``pid``, or ``0`` when unreadable.

    ``0`` means "this axis carries no evidence", never "started at the epoch".  Callers
    treat a zero as an axis to skip, which :func:`standalone_identity.verify` does.

    **Safe to call in a forked child**: it reads one plain file and never spawns anything.
    On a host with no ``/proc`` there is no such file, so the answer is ``0`` -- the axis
    carries no evidence there, which is stated rather than faked.  The previous ``ps``
    fallback ran a subprocess on the pre-exec path and is gone for the reason above.
    """
    try:  # linux
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return int(fields[19])
    except (OSError, IndexError, ValueError):
        pass
    # darwin (consolidated review round 4, finding 3).  ``proc_pidinfo(PROC_PIDTBSDINFO)``
    # is the kernel's own start time for the pid -- a libc call, no fork, no subprocess,
    # so it is as safe on the pre-exec path as the ``/proc`` read above.  Before this the
    # MVP platform answered 0 for EVERY process, so the start-identity axis carried no
    # evidence anywhere and a live pid absent from its tty could not be told from a
    # recycled one.
    return _darwin_start_ticks(pid)


#: ``PROC_PIDTBSDINFO`` and the size of ``struct proc_bsdinfo`` (<sys/proc_info.h>).  The
#: two start-time fields are the last two ``uint64_t`` members, at byte offsets 120 and
#: 128; the size is asserted by the call itself, which returns fewer bytes on a mismatch.
_PROC_PIDTBSDINFO = 3
_PROC_PIDTBSDINFO_SIZE = 136


def _darwin_start_ticks(pid: int) -> int:
    lib = _libproc_handle()
    if lib is None or not hasattr(lib, "proc_pidinfo"):
        return 0
    import ctypes
    try:
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                     ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(_PROC_PIDTBSDINFO_SIZE)
        written = lib.proc_pidinfo(int(pid), _PROC_PIDTBSDINFO, 0, buffer,
                                   _PROC_PIDTBSDINFO_SIZE)
    except (OSError, ValueError, AttributeError):
        return 0
    if written != _PROC_PIDTBSDINFO_SIZE:
        return 0
    seconds, micros = struct.unpack_from("<QQ", buffer.raw, 120)
    return int(seconds) * 1_000_000 + int(micros)


def highest_open_fd(*, ceiling: int = 4096) -> int:
    """The highest descriptor this process currently holds, for the child's ``closerange``.

    Computed in the PARENT.  ``os.closerange(3, SC_OPEN_MAX)`` is what this replaces, and on
    this host ``SC_OPEN_MAX`` is 1 048 576 -- a million ``close()`` calls on the pre-exec
    path, per spawn.  Enumerating what is actually open is both correct and bounded.

    ``ceiling`` is a floor on the range, not a cap on safety: the child closes up to
    ``max(highest_open, ceiling)``, so a descriptor the enumeration missed below the ceiling
    is still closed.  A descriptor above it could only exist if this process had opened one,
    which the enumeration would have found.
    """
    highest = 2
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            if entry.isdigit():
                highest = max(highest, int(entry))
        return max(highest, ceiling)
    # No enumeration available: fall back to the soft limit, but bounded, because an
    # unbounded range makes every spawn cost a million syscalls.
    try:
        import resource
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        return max(ceiling, min(int(soft), 65_536))
    except (ImportError, OSError, ValueError):
        return ceiling


# ---- discovery -------------------------------------------------------------------------
def read_process_table(tty: str, *,
                       runner: Any = None) -> ProcessTableSnapshot:
    """A tty-scoped snapshot: ``ps -t <tty> -o pid=,ppid=,pgid=,sess=,tty=,stat=``.

    tty-scoped, never ``ps -ax`` (`docs/ORCA_RUNTIME_PRIMITIVES.md` C2).  A whole-host scan
    is both slower and more dangerous: it invites matching on a command name, and a command
    name is not identity.

    A read failure sets ``readable=False`` rather than returning an empty ``rows`` tuple.
    An empty readable table and an unreadable table are different facts and route
    differently; collapsing them is how "I could not look" becomes "it is gone".
    """
    captured_at = time.time()
    # `sess`, not `sid`.  FACT, verified on this host: darwin's `ps` answers
    # `sid: keyword not found` and returns a SHORT row, which the strict parser below then
    # correctly reports as unreadable -- so the whole supervisor would have failed closed on
    # its own MVP platform, permanently and silently.  `sess` is accepted by both darwin's
    # ps and procps, so it is the portable spelling of the same field.
    argv = ["ps", "-t", tty, "-o", "pid=,ppid=,pgid=,sess=,tty=,stat="]
    try:
        if runner is not None:
            completed = runner(argv)
            code = getattr(completed, "returncode", 0)
            text = getattr(completed, "stdout", "") or ""
        else:
            completed = subprocess.run(argv, capture_output=True, text=True,
                                       timeout=10, check=False)
            code, text = completed.returncode, completed.stdout
    except (OSError, subprocess.SubprocessError, Exception) as exc:  # noqa: BLE001
        if isinstance(exc, (OSError, subprocess.SubprocessError)):
            return {"tty": tty, "captured_at": captured_at, "rows": (), "readable": False}
        raise
    if code not in (0, 1):
        # `ps -t` exits 1 with no output when the tty holds no process -- a legitimate
        # EMPTY answer.  Any other status means the read itself failed.
        return {"tty": tty, "captured_at": captured_at, "rows": (), "readable": False}
    rows: list[ProcessRow] = []
    # Round-8 item 5: `ps` delimits rows with "\n"; split on that alone (the same
    # protocol-delimiter rule every structured reader follows).
    for line in capture_mod.protocol_lines(text):
        if not line.strip():
            continue
        match = _PS_LINE.match(line)
        if match is None:
            # An unparsable line makes the WHOLE snapshot unreadable.  Skipping it would
            # silently drop the row that might be the process we are about to declare dead.
            return {"tty": tty, "captured_at": captured_at, "rows": (), "readable": False}
        rows.append({"pid": int(match.group(1)), "ppid": int(match.group(2)),
                     "pgid": int(match.group(3)), "sid": int(match.group(4)),
                     "tty": match.group(5), "stat": match.group(6)})
    return {"tty": tty, "captured_at": captured_at, "rows": tuple(rows), "readable": True}


def row_for(snapshot: Mapping[str, Any], pid: int) -> ProcessRow | None:
    for row in snapshot.get("rows", ()):
        if row["pid"] == pid:
            return row
    return None


def check_ownership(record: Mapping[str, Any], snapshot: Mapping[str, Any], *,
                    staleness_budget_ms: int, now: float | None = None,
                    supervisor_pid: int | None = None) -> dict[str, Any]:
    """The four refusals, evaluated in order.  Returns a decision; sends nothing.

    ``{"verdict": "owned"|"refused", "refusal": <member or "">,
       "scope": "group"|"root"|"none", "row": ProcessRow|None}``

    ``scope`` is the widest signalling scope this decision permits.  ``none`` means no
    signal at all -- and the caller must honour it, which :func:`signal_target` enforces by
    taking this decision rather than a boolean.
    """
    moment = time.time() if now is None else now
    captured_tty = record.get("captured_tty")
    if captured_tty in ("?", "??", "", None):
        return {"verdict": "refused", "refusal": "unbound_tty", "scope": "none", "row": None}
    if not snapshot.get("readable", False):
        raise ProcessTableUnreadable(
            f"the process table for {captured_tty!r} could not be read; liveness is "
            "unknown, which is not the same as exited")
    age_ms = (moment - float(snapshot["captured_at"])) * 1000.0
    if age_ms > staleness_budget_ms:
        return {"verdict": "refused", "refusal": "stale_snapshot", "scope": "none",
                "row": None, "age_ms": age_ms}
    row = row_for(snapshot, int(record["pid"]))
    if row is None:
        return {"verdict": "refused", "refusal": "captured_tty_mismatch", "scope": "none",
                "row": None, "detail": "pid_absent_from_tty_scoped_table"}
    if row["tty"] != captured_tty:
        # A recycled pid.  The pid exists, on another tty; signalling it signals a stranger.
        return {"verdict": "refused", "refusal": "captured_tty_mismatch", "scope": "none",
                "row": row}
    own_pid = os.getpid() if supervisor_pid is None else supervisor_pid
    if row_for(snapshot, own_pid) is not None:
        # The supervisor shares the tty with the target.  A group signal would reach this
        # process too.  Downgrade to a root-scoped signal -- NEVER killpg.
        return {"verdict": "owned", "refusal": "tty_shared_with_driver", "scope": "root",
                "row": row}
    if row["pgid"] != record.get("pgid"):
        return {"verdict": "refused", "refusal": "captured_tty_mismatch", "scope": "none",
                "row": row, "detail": "pgid_mismatch"}
    return {"verdict": "owned", "refusal": "", "scope": "group", "row": row}


def descendant_groups(snapshot: Mapping[str, Any], *, leader_pgid: int) -> tuple[int, ...]:
    """Process groups on this tty that are NOT the leader's, innermost first.

    These are signalled BEFORE the leader (C4).  With the spawn topology in place the agent
    CLI genuinely is in a descendant group -- its own, which also owns the pty foreground --
    so this ordering does real work, and the leader survives long enough to reap it.
    """
    groups = {row["pgid"] for row in snapshot.get("rows", ())
              if row["pgid"] != leader_pgid}
    return tuple(sorted(groups, reverse=True))


# ---- the spawn record ------------------------------------------------------------------
def spawn_record_dir(artifact_base: str | os.PathLike[str], run_id: str,
                     intent_id: str) -> Path:
    return (Path(artifact_base) / "runs" / run_id / "standalone" / "intents" / intent_id)


def spawn_record_path(artifact_base: str | os.PathLike[str], run_id: str, intent_id: str,
                      incarnation: str) -> Path:
    return spawn_record_dir(artifact_base, run_id, intent_id) / f"spawn.{incarnation}"


def write_spawn_record(path: str | os.PathLike[str], record: Mapping[str, Any]) -> None:
    """``write`` -> ``fsync(file)`` -> ``rename`` -> ``fsync(dir)``.

    Called in the forked child, which is still this runtime's own Python until ``execve``.
    The rename makes a partial file unobservable: a reader either sees a whole record or
    none, never half of one, so "no spawn record" always means what it says.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    payload = json.dumps(dict(record), sort_keys=True).encode()
    handle = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(handle, payload)
        os.fsync(handle)
    finally:
        os.close(handle)
    os.replace(str(tmp), str(target))
    dir_fd = os.open(str(target.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class SpawnRecordLookup(TypedDict):
    """The three-way answer ``lookup`` needs, and no fourth."""

    outcome: str          # `absent` | `present` | `unknown`
    record: dict[str, Any] | None
    detail: str


def read_spawn_records(artifact_base: str | os.PathLike[str], run_id: str,
                       intent_id: str, *, incarnation: str = "") -> SpawnRecordLookup:
    """``absent`` proves no ``execve``; ``present`` means the effect may exist; ``unknown`` raises upstream.

    ``absent`` is returned ONLY when the intent directory was read successfully and holds no
    spawn record.  A missing directory counts as absent -- nothing was ever written for this
    intent, and the write precedes the exec -- but a directory that EXISTS and cannot be
    listed is ``unknown``, because that is exactly the case where a record might be there.

    **``incarnation`` scopes the question to ONE attempt** (consolidated review finding
    15).  Without it the lexicographically LATEST record was returned whatever the caller
    asked about, so a retry's identity bind could read an EARLIER incarnation's record --
    written by a process that is not the one just forked -- and bind the new attempt to it
    before the new child's own exec.  With it, only ``spawn.<incarnation>`` is read, and a
    record whose body names a different incarnation is refused as ``unknown`` rather than
    adopted.  ``lookup`` still asks the unscoped question -- "did ANY execve happen for this
    intent?" -- which is a different question with a different answer.
    """
    directory = spawn_record_dir(artifact_base, run_id, intent_id)
    if not directory.exists():
        parent = directory.parent
        if parent.exists() and not os.access(parent, os.R_OK):
            return {"outcome": "unknown", "record": None,
                    "detail": "intents directory is not readable"}
        return {"outcome": "absent", "record": None,
                "detail": "no intent directory: nothing was ever written before an execve"}
    try:
        names = sorted(p for p in directory.iterdir() if p.name.startswith("spawn."))
    except OSError as exc:
        return {"outcome": "unknown", "record": None,
                "detail": f"intent directory unreadable: {exc}"}
    names = [p for p in names if not p.name.endswith(".tmp")]
    if incarnation:
        names = [p for p in names if p.name == f"spawn.{incarnation}"]
    if not names:
        return {"outcome": "absent", "record": None,
                "detail": ("intent directory readable and holds no spawn record"
                           + (f" for incarnation {incarnation!r}" if incarnation else ""))}
    try:
        payload = json.loads(names[-1].read_text())
    except (OSError, ValueError) as exc:
        return {"outcome": "unknown", "record": None,
                "detail": f"spawn record present but unreadable: {exc}"}
    if incarnation and (not isinstance(payload, dict)
                        or payload.get("process_incarnation") != incarnation):
        return {"outcome": "unknown", "record": None,
                "detail": f"spawn record for {incarnation!r} names another incarnation "
                          f"{(payload or {}).get('process_incarnation')!r}; it is not "
                          "this attempt's evidence"}
    return {"outcome": "present", "record": payload,
            "detail": "an execve was reached; the effect may exist and must be observed"}


# ---- the exec wrapper ------------------------------------------------------------------
def exit_sentinel_path(artifact_base: str | os.PathLike[str], run_id: str,
                       session_id: str, incarnation: str) -> Path:
    return (Path(artifact_base) / "runs" / run_id / "standalone" / session_id
            / f"exit.{incarnation}")


def write_exit_sentinel(path: str | os.PathLike[str], *, code: int, fence: str) -> None:
    """Write the fenced exit status via tmp+rename.  Async-signal-safe-ish, by design.

    Called by the pty SESSION LEADER after it has reaped the agent -- i.e. after ``fork``
    and never after ``execve`` -- so it uses raw ``os`` descriptor calls and no
    :mod:`subprocess`, no :class:`pathlib.Path` and no logging.  A forked child holds the
    parent's address space with none of its threads, and richer machinery can wedge there.

    It gives a STRANGER process an OS-sourced exit status -- ``waitpid``'s own status word,
    not a parsed screen -- which ``waitpid`` cannot give a non-parent.  tmp+rename so a
    partial file is never observed.

    The residual is named rather than hidden: if the leader itself is ``SIGKILL``ed no
    sentinel is written, and a successor then reports ``exit_unproven`` -> ``LOST``, never
    ``COMPLETED``.  That is DESIGN risk DR-2 and it is fail-closed.
    """
    target = os.fsencode(os.fspath(path))
    payload = f"{int(code)}\t{fence}\n".encode()
    tmp = target + b".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, target)


def _wait_status_to_code(status: int) -> int:
    """``waitpid``'s status word as a shell-shaped code: ``128 + signum`` when signalled."""
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return 1


def read_exit_sentinel(path: str | os.PathLike[str], *, fence: str) -> dict[str, Any]:
    """``{"outcome": "exited"|"absent"|"foreign"|"unreadable", "code": int|None}``.

    A sentinel whose fence does not match this incarnation is ``foreign`` -- it belongs to
    another run of the same session id -- and its code is deliberately NOT returned.  A
    foreign exit status harvested as this one's is precisely the replay attack the identity
    fence exists to refuse.
    """
    target = Path(path)
    if not target.exists():
        return {"outcome": "absent", "code": None}
    try:
        raw = target.read_text()
    except OSError as exc:
        return {"outcome": "unreadable", "code": None, "detail": str(exc)}
    parts = raw.strip().split("\t")
    if len(parts) != 2 or not parts[0].lstrip("-").isdigit():
        return {"outcome": "unreadable", "code": None, "detail": "malformed sentinel"}
    if parts[1] != fence:
        return {"outcome": "foreign", "code": None, "observed_fence": parts[1]}
    return {"outcome": "exited", "code": int(parts[0])}


# ---- the spawn -------------------------------------------------------------------------
class PtySession(TypedDict):
    master_fd: int
    slave_name: str
    pid: int
    pgid: int
    sid: int
    leader_pid: int
    pty_id: str
    #: The EXACT argv handed to ``execve`` (finding 14).  The runtime's delivery
    #: verification hands the composed argv to the driver's replay selector, and it used to
    #: read it off this mapping under a key nothing ever wrote -- so the selector always saw
    #: an EMPTY argv even though preflight had rehearsed the real one.
    argv: tuple[str, ...]
    #: The SUPERVISOR's end of the orphan guard (round 4, finding 1).  Its read end lives in
    #: the exit watcher and becomes readable -- EOF -- only when every copy of this end is
    #: closed, i.e. when the supervisor process is gone.  Closed by :func:`release`.
    orphan_guard_fd: int
    #: The SUPERVISOR's end of the DRAIN HANDOFF (round-10 item 1).  The watcher, having
    #: reaped the agent and written the exit sentinel, defers its own exit -- keeping the
    #: session-leader tty alive so an unread tail is not revoke-discarded on darwin -- until
    #: this end is closed (in :meth:`StandaloneSession._reclaim`, after the supervisor's own
    #: finalizing drain) or the supervisor dies.  Closed by :func:`release`.
    drain_handoff_fd: int


class SpawnHandoffFailed(OSError):
    """The session leader never reported an agent pid -- and the pty is RETAINED.

    Round 4, finding 4.  The old shape closed the master and raised a bare ``OSError``, so
    the caller had no handle at all over a leader it had forked and an agent that may have
    reached ``execve`` in the meantime; the runtime then recorded ``spawn_failed`` with
    ``teardown=not_required`` over a process it never proved absent.  This exception keeps
    every piece of authority the parent still holds: the leader pid (its own child), the
    open master, the slave name (so the tty-scoped process table can be read) and the
    guard end -- enough to find, signal through the ownership ladder, reap and PROVE the
    child's exit, or to retain it durably when that proof cannot be made.
    """

    def __init__(self, detail: str, *, leader_pid: int, master_fd: int, slave_name: str,
                 pty_id: str, orphan_guard_fd: int, argv: tuple[str, ...],
                 drain_handoff_fd: int = -1) -> None:
        super().__init__(errno.ECHILD, detail)
        self.leader_pid = leader_pid
        self.master_fd = master_fd
        self.slave_name = slave_name
        self.pty_id = pty_id
        self.orphan_guard_fd = orphan_guard_fd
        self.drain_handoff_fd = drain_handoff_fd
        self.argv = argv

    def retained_session(self) -> "PtySession":
        """The partial session the runtime keeps authority through.  ``pid`` is ``0``:
        no agent identity was reported and none is invented; the runtime binds it from the
        child's own spawn record, or from nothing."""
        return {"master_fd": self.master_fd, "slave_name": self.slave_name, "pid": 0,
                "pgid": 0, "sid": self.leader_pid, "leader_pid": self.leader_pid,
                "pty_id": self.pty_id, "argv": self.argv,
                "orphan_guard_fd": self.orphan_guard_fd,
                "drain_handoff_fd": self.drain_handoff_fd}


def spawn(*, argv: Sequence[str], env: Mapping[str, str], profile: StandaloneProfile,
          session_id: str, incarnation: str, spawn_record_target: str | os.PathLike[str],
          cwd: str | None = None, argv_digest: str = "", env_digest: str = "",
          sentinel: str | os.PathLike[str] | None = None,
          fence: str = "", image: str | None = None,
          capture: str | os.PathLike[str] | None = None) -> PtySession:
    """``openpty`` -> ``fork`` (leader) -> ``fork`` (agent) -> ``setpgid`` -> ``tcsetpgrp``
    -> spawn record -> ``closerange`` -> ``execve``.

    **The agent's process image IS ``realpath(profile.binary)``.**  That is the whole point
    of this topology and it is what makes R-A leg 4 (§D5.3(4)) an executable-IDENTITY proof
    rather than a command-line guess.  The pty's foreground process is the agent itself, so
    ``readlink`` on it answers with the profile's binary and nothing else -- not a shell,
    not an updater helper, and not an interpreter that merely carries the expected path as
    an argument.

    Two children, each with one job:

    *The session leader* (``leader_pid``) is created by the first ``fork``.  It calls
    ``setsid``, acquires the pty as its controlling terminal, and then does exactly one
    thing forever: ``waitpid`` the agent and write the fenced exit sentinel.  It is NOT the
    pty's foreground and it NEVER execs, so it cannot be mistaken for the agent.  It exists
    because an OS-sourced exit status can only be read by the agent's PARENT, and the
    supervisor process that called :func:`spawn` may be gone by the time anybody asks -- a
    Coordinator turn ending is the normal case, not the exceptional one.

    *The agent* is created by the second ``fork``.  It puts itself in its OWN process group
    (``setpgid(0, 0)``, so ``pgid == pid``), makes that group the pty's FOREGROUND group
    (``tcsetpgrp``, with ``SIGTTOU`` ignored because a background group asking to become the
    foreground is exactly what raises it), writes the spawn record, closes inherited
    descriptors and ``execve``s the RESOLVED binary.

    Because the agent is its own group leader, the pgid the record carries is the agent's
    pid, ``os.tcgetpgrp(master_fd)`` equals that pgid EXACTLY -- no descendant-group
    widening -- and the foreground group's leader is the process whose image the readiness
    proof reads.  The three facts are the same fact, which is why leg 3 and leg 4 can both
    be equalities.

    The spawn record is still written by the process that is about to ``execve``, as the
    last instruction before it, so its ABSENCE still proves no ``execve`` happened
    (DESIGN D3.4a).  Nothing about that property moved.

    **The agent and its exit evidence outlive the supervisor** (round 4, finding 1).  The
    watcher ignores ``SIGHUP``, keeps ONE copy of the pty master open so the last close of
    the supervisor's copy can never hang the pty up, and holds the read end of an *orphan
    guard* pipe whose write end only the supervisor holds.  When that end reads EOF the
    supervisor is gone: the watcher then drains the master itself -- into ``capture`` (the
    session's own ``capture.log``, or the sentinel's sibling of that name), verbatim -- so
    the agent is never blocked on a full pty buffer nobody reads, runs to its own end, is
    reaped by the watcher and gets its fenced exit sentinel written.  Before this the
    supervisor's death closed the only master, the kernel hung the pty up, ``SIGHUP`` killed
    the session leader, and the leader's exit ``SIGHUP``ed the foreground agent: both
    vanished and no sentinel was ever written.
    """
    # Read in the PARENT, and passed into the child, because the child may not spawn.
    host_boot_id = boot_id()
    # Resolved in the PARENT so the image the kernel loads is, BY CONSTRUCTION, the same
    # path R-A leg 4 compares against.  Callers that already resolved the profile's binary
    # pass it as `image`; that is the load-bearing wiring, because it makes "what was
    # exec'd" and "what readiness requires" the same string rather than two independent
    # resolutions that could disagree.  `argv[0]` is only ever the fallback, and a bare name
    # in it is resolved against the CHILD's PATH -- never against this process's cwd, which
    # `realpath` would otherwise invent a nonexistent path from.
    candidate = image or argv[0]
    if os.sep not in candidate:
        candidate = shutil.which(candidate, path=env.get("PATH", "")) or candidate
    image_path = os.path.realpath(candidate)
    # ---- finding 3: every fallible pre-exec condition this PARENT can check is checked
    # here, BEFORE the fork, so a child that cannot reach `execve` never gets far enough
    # to write a spawn record.  A record is consumed as "an execve was reached", and the
    # retry that follows is then IDEMPOTENCY-blocked -- for an agent that never ran.
    if not os.path.isfile(image_path) or not os.access(image_path, os.X_OK):
        raise OSError(errno.ENOENT,
                      f"the agent image {image_path!r} is not an executable file; nothing "
                      "was forked and no spawn record exists")
    if cwd and not os.path.isdir(cwd):
        raise OSError(errno.ENOENT,
                      f"the declared worktree {cwd!r} is not a directory; nothing was "
                      "forked and no spawn record exists")
    # ABSOLUTE, resolved in the parent: the child `chdir`s into the worktree BEFORE it
    # writes the spawn record (finding 3), so a relative artifact base would otherwise
    # land the record inside the agent's worktree and the parent would read "no intent
    # directory" for a child that did reach `execve`.
    spawn_record_target = os.path.abspath(os.fspath(spawn_record_target))
    if sentinel is not None:
        sentinel = os.path.abspath(os.fspath(sentinel))
    if capture is None and sentinel is not None:
        capture = os.path.join(os.path.dirname(sentinel), "capture.log")
    elif capture is not None:
        capture = os.path.abspath(os.fspath(capture))
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    fcntl.fcntl(master_fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    _set_raw(slave_fd)
    _set_winsize(slave_fd, profile.rows, profile.cols)
    # The agent's pid is minted two forks down, so it is handed back up a pipe rather than
    # guessed.  Bounded, and a failure to learn it is a spawn failure -- never a fabricated
    # pid, because every ownership refusal in this module keys on the recorded pid.
    handoff_r, handoff_w = os.pipe()
    # The orphan guard (finding 1): the watcher keeps `guard_r`; only the supervisor keeps
    # `guard_w`, so the read end reports EOF exactly when the supervisor is gone.
    guard_r, guard_w = os.pipe()
    fcntl.fcntl(guard_w, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    # The DRAIN HANDOFF (round-10 item 1): when the supervisor is alive at the agent's
    # exit, IT owns the final drain -- but the watcher is the session LEADER, and on darwin
    # a session leader's exit REVOKES the controlling tty and DISCARDS the unread tail still
    # buffered on the master.  So the watcher, having reaped the agent and written the exit
    # sentinel, DEFERS its own exit -- keeping the tty alive -- until the supervisor closes
    # `dh_w` (it does so in `_reclaim`, after its own finalizing drain) or dies.  The
    # watcher keeps `dh_r`; only the supervisor keeps `dh_w`.  This makes the supervisor the
    # SINGLE finalizing owner in that path: watcher exit can no longer manufacture the
    # hangup the supervisor reads as capture finality over a truncated capture.
    dh_r, dh_w = os.pipe()
    fcntl.fcntl(dh_w, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    # Enumerated AFTER the pipes exist, so the child's `closerange` and the watcher's own
    # descriptor sweep both cover them.
    close_up_to = highest_open_fd()
    capture_target = os.fsencode(os.fspath(capture)) if capture is not None else None

    started_at = _now_iso()
    leader_pid = os.fork()
    if leader_pid == 0:  # pragma: no cover - the leader never returns
        try:
            os.close(handoff_r)
            os.close(guard_w)
            os.close(dh_w)            # the watcher must not hold the supervisor's write end
            os.setsid()
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            agent_pid = os.fork()
            if agent_pid == 0:
                try:
                    # A background process group asking to become the foreground raises
                    # SIGTTOU at itself.  Ignoring it is the documented way to perform the
                    # handover, not a way of hiding an error.
                    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
                    os.setpgid(0, 0)
                    os.tcsetpgrp(0, os.getpgrp())
                    child_pid = os.getpid()
                    # ---- finding 3: EVERY fallible pre-exec operation comes BEFORE the
                    # spawn record.  `chdir` used to come after it, so a missing or
                    # unreadable worktree made the child die at 127 WITHOUT reaching
                    # `execve` while a record saying "an execve was reached" already sat on
                    # disk.  The record is the last write before `execve` and nothing
                    # that can fail stands between the two any more.
                    if cwd:
                        os.chdir(cwd)
                    # THE LAST THING THIS PROCESS DOES BEFORE execve.  After the pre-effect
                    # claim, never before it.  Its ABSENCE is what proves no execve
                    # happened.
                    #
                    # Everything in this record is either already known to the parent or
                    # readable from a plain file.  NOTHING here spawns a process: between
                    # fork() and execve() the child holds the parent's address space with
                    # none of its threads, and running Python's subprocess machinery in
                    # that state can wedge it -- which it did, before this was fixed.
                    write_spawn_record(spawn_record_target, {
                        "session_id": session_id, "process_incarnation": incarnation,
                        "pid": child_pid, "pgid": os.getpgid(0), "sid": os.getsid(0),
                        "boot_id": host_boot_id,
                        "proc_start_ticks": proc_start_ticks(child_pid),
                        "argv_digest": argv_digest, "env_digest": env_digest,
                        "started_at": started_at,
                    })
                    # `closerange` cannot fail in a way that stops the exec -- it only
                    # closes -- and it stays after the write because the write needs a
                    # descriptor of its own.
                    os.closerange(3, close_up_to + 1)
                    os.execve(image_path, list(argv), dict(env))
                except BaseException:
                    os._exit(127)
            os.write(handoff_w, b"%d\n" % agent_pid)
            os.close(handoff_w)
            # ---- finding 9 (round 3) / finding 1 (round 4): what the WATCHER holds ------
            # It holds NO slave descriptor: its 0/1/2 go to /dev/null and its inherited
            # slave copy is closed, so the slave's last close is the agent's own.  It
            # DOES keep one copy of the master -- deliberately, and that is the round-4
            # correction of the round-3 shape that closed it: with the supervisor holding
            # the only master, the supervisor's death was the pty's hangup, and the hangup
            # killed first the watcher (the session leader) and then, through the leader's
            # exit, the foreground agent.  The kept copy is a KEEPALIVE, not a reader:
            # while the supervisor lives the watcher never reads it, and once the guard
            # reports the supervisor gone the watcher drains it verbatim into the capture
            # so the agent can finish.  Every other inherited descriptor is closed here,
            # because a later dispatch's guard end inherited by THIS watcher would keep
            # that dispatch's watcher from ever seeing its own supervisor die.
            _watch(agent_pid, master_fd=master_fd, slave_fd=slave_fd, guard_r=guard_r,
                   close_up_to=close_up_to, sentinel=sentinel, fence=fence,
                   capture=capture_target, capture_limits=profile.capture,
                   slave_name=slave_name, dh_r=dh_r,
                   drain_budget_ms=profile.timeouts.post_exit_drain_budget_ms)
        except BaseException:
            os._exit(127)
    os.close(slave_fd)
    os.close(handoff_w)
    os.close(guard_r)
    os.close(dh_r)                    # the supervisor keeps only the write end `dh_w`
    pty_id = f"pty-{uuid.uuid4().hex[:12]}"
    argv_tuple = tuple(str(a) for a in argv)
    try:
        agent_pid = _read_handoff(handoff_r)
    finally:
        os.close(handoff_r)
    if agent_pid <= 0:
        # Finding 4.  The master is NOT closed and nothing is guessed: the parent keeps
        # the leader pid, the open master, the slave name and the guard, and the caller
        # decides -- from the child's own spawn record and the tty-scoped table -- whether
        # an agent exists, terminates it through the ownership ladder, and proves it gone
        # or retains it.  Closing the master here was the old path's only "teardown", and
        # it was a hangup the kernel might or might not act on, never a proof.
        raise SpawnHandoffFailed(
            "the pty session leader never reported an agent pid; no agent process "
            "identity exists, so none is invented -- the pty is retained for teardown",
            leader_pid=leader_pid, master_fd=master_fd, slave_name=slave_name,
            pty_id=pty_id, orphan_guard_fd=guard_w, argv=argv_tuple,
            drain_handoff_fd=dh_w)
    return {"master_fd": master_fd, "slave_name": slave_name, "pid": agent_pid,
            "pgid": agent_pid, "sid": leader_pid, "leader_pid": leader_pid,
            "pty_id": pty_id, "argv": argv_tuple, "orphan_guard_fd": guard_w,
            "drain_handoff_fd": dh_w}


def _watch(agent_pid: int, *, master_fd: int, slave_fd: int, guard_r: int,
           close_up_to: int, sentinel: str | os.PathLike[str] | None, fence: str,
           capture: bytes | None,
           capture_limits: CaptureLimits | None = None,
           slave_name: str = "", drain_budget_ms: int = 2_000, dh_r: int = -1
           ) -> None:  # pragma: no cover - runs in the forked watcher
    """The exit watcher's whole life.  Raw ``os`` calls only: this is a forked child.

    Never returns: it ``_exit``s with the agent's shell-shaped status after writing the
    fenced sentinel.  ``SIGHUP`` is ignored so a hangup of the controlling pty -- which
    the kept master makes impossible while this process lives, but which a stranger could
    still deliver by hand -- can never destroy the exit evidence.

    **The orphan drain is BOUNDED by the profile's own capture limits** (consolidated
    follow-up review, finding 4).  The round-4 shape appended every drained byte verbatim,
    so a 4 KiB limit retained 131 KiB after supervisor death and the capture still
    answered `answerable=True`.  Now every chunk goes through the same `admit_chunk`
    decision `BoundedCapture` applies, the meta is rewritten after every append with the
    running digest under `writer="exit_watcher"`, and a reader that finds the meta and
    the bytes in disagreement fails closed.  The master is still drained past the limit
    -- the agent must never block on a full pty buffer -- but the bytes are DROPPED and
    counted, never written.

    **The sentinel proves the EXIT; the finalized record proves the CAPTURE** (round-9
    consolidated review, item 1).  When this watcher is the one finalizing -- the
    supervisor is gone and it holds the only reader of the master -- it drains the master
    TO THE HANGUP (bounded by the profile's ``post_exit_drain_budget_ms``; silence does
    not end it), saves and fsyncs the capture meta, writes the fenced capture-finalized
    proof (:func:`standalone_capture.write_capture_finalized`, bound to the capture's
    final length + digest and to the sentinel it is about to write) and ONLY THEN writes
    the exit sentinel.  A drain that ends by the bound (a descendant still holds the
    slave) or by an unreadable master writes an ``unproven`` record naming what held the
    slave, then the sentinel; a successor then settles `stream_end_unproven`.  When the
    supervisor is ALIVE at the agent's exit it owns the drain and writes the proof itself;
    the sentinel this watcher writes at once is exit evidence only and no reader may take
    it for capture completion.

    **The supervisor-alive watcher DEFERS its exit** (round-10 item 1).  On darwin a
    session leader's exit REVOKES the controlling tty and discards the unread tail still
    buffered on the master, so a watcher that reaped the agent, wrote the sentinel and
    exited AT ONCE could manufacture the very hangup the supervisor then read as capture
    finality -- over a capture the revoke had just truncated.  So when the supervisor is
    alive this watcher writes the sentinel (the supervisor needs it to start its own drain)
    and then BLOCKS in :func:`_await_drain_handoff`, keeping the session-leader tty alive,
    until the supervisor closes ``dh_r``'s write end (it does so in ``_reclaim``, after its
    own finalizing drain) or the supervisor dies.  It writes NO proof on that path -- the
    supervisor is the single finalizing owner -- so a supervisor killed before its proof
    still leaves no proof and a successor still refuses ``stream_end_unproven``.
    """
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        os.close(slave_fd)
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        if null > 2:
            os.close(null)
    except OSError:
        pass
    # ---- correction iteration 2 (CI-2): the agent's exit wakes the watcher AT ONCE ------
    # The round-4 loop polled `waitpid(WNOHANG)` every 50 ms, so between the agent's exit
    # and the fenced sentinel there was a window of up to 50 ms in which the agent was a
    # zombie -- off its tty, unsentinelled -- and a stranger reading the run in that window
    # (`recover_handle`) saw an orphan.  Measured on the MVP host under load: 18 of 40
    # reads landed in it; the sentinel arrived ~8-10 ms later.  A SIGCHLD wake-up pipe
    # (`signal.set_wakeup_fd`) in every `select` below closes the window to the signal's
    # own latency: the kernel writes the byte the instant the child exits and the very
    # next `waitpid` reaps it.  The handler itself does nothing; PEP 475 would otherwise
    # silently restart the `select` and swallow the signal.
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)
    signal.signal(signal.SIGCHLD, lambda *_args: None)
    keep = {master_fd, guard_r, wake_r, wake_w}
    if dh_r >= 0:
        keep.add(dh_r)
    for fd in range(3, close_up_to + 1):
        if fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass
    orphaned = False
    appender: capture_mod.RawBoundedAppender | None = None
    status = 0
    while True:
        try:
            done, status = os.waitpid(agent_pid, os.WNOHANG)
        except ChildProcessError:
            done, status = agent_pid, 0
        if done == agent_pid:
            break
        if not orphaned:
            try:
                ready, _, _ = select.select([guard_r, wake_r], [], [], 0.05)
            except (OSError, ValueError):
                ready = [guard_r]
            if wake_r in ready:
                _drain_wakeups(wake_r)
                continue                       # a child changed state: reap it above
            if ready:
                # EOF on the guard: the supervisor is gone.  From here the agent's output
                # has no reader but this process, so it becomes the reader -- under the
                # SAME limits the supervisor applied (finding 4).
                orphaned = True
                appender = _orphan_take_over(guard_r, capture, capture_limits)
            continue
        _drain_once(master_fd, appender, budget=0.05, wake_r=wake_r)
    code = _wait_status_to_code(status)
    # ---- round-10 follow-up (a): re-check the guard AFTER reaping -------------------------
    # The agent's exit and the supervisor's death can be simultaneously ready: `waitpid`
    # returns the agent (break) while EOF sits unread on the guard, and the SIGCHLD fast
    # path above can `continue` past the guard check in the same iteration.  Either way the
    # loop could exit with `orphaned` still False and skip orphan finalization -- writing a
    # sentinel with no proof over a supervisor that was gone and whose tail this watcher
    # alone could have drained.  A final non-blocking guard probe closes that race: a
    # supervisor already gone is detected here and the orphan-finalize path below runs.
    if not orphaned:
        try:
            ready, _, _ = select.select([guard_r], [], [], 0)
        except (OSError, ValueError):
            ready = []
        if ready:
            orphaned = True
            appender = _orphan_take_over(guard_r, capture, capture_limits)
    if orphaned:
        if capture is not None:
            # This watcher is the one FINALIZING: drain to the hangup, persist, prove, and
            # only then write the sentinel (item 1).  Nothing here may raise past the
            # sentinel write -- a watcher that dies of bookkeeping loses the exit evidence.
            try:
                _finalize_orphaned_capture(
                    master_fd, appender,
                    budget_s=max(0, int(drain_budget_ms)) / 1000.0,
                    finalized=capture_mod.capture_finalized_path(
                        capture, fence.partition(":")[2]),
                    fence=fence, code=code, slave_name=slave_name)
            except Exception:  # noqa: BLE001 - the sentinel is still written below
                pass
        if sentinel is not None:
            write_exit_sentinel(sentinel, code=code, fence=fence)
    else:
        # Round-10 item 1: the supervisor was ALIVE at the reap and owns the final drain.
        # Write the sentinel so it can start draining, then LINGER -- keeping the tty alive,
        # writing no proof -- until it releases us (dh_r EOF, closed in `_reclaim`) or dies.
        if sentinel is not None:
            write_exit_sentinel(sentinel, code=code, fence=fence)
        if dh_r >= 0:
            _await_drain_handoff(dh_r, guard_r,
                                 budget_s=max(0, int(drain_budget_ms)) / 1000.0)
    os._exit(code)


def _orphan_take_over(guard_r: int, capture: bytes | None,
                      capture_limits: "CaptureLimits | None"
                      ) -> "capture_mod.RawBoundedAppender | None":  # pragma: no cover
    """Close the (now-EOF) orphan guard and build the bounded appender the watcher drains
    into once the supervisor is gone.  A watcher never dies of bookkeeping, so an appender
    that cannot be built is ``None`` (the finalized record then names that nothing vouches
    for the bytes)."""
    try:
        os.close(guard_r)
    except OSError:
        pass
    if capture is None:
        return None
    try:
        return capture_mod.RawBoundedAppender(capture, limits=capture_limits or CaptureLimits())
    except Exception:  # noqa: BLE001 - a watcher never dies of bookkeeping
        return None


def _await_drain_handoff(dh_r: int, guard_r: int, *, budget_s: float
                         ) -> None:  # pragma: no cover - runs in the forked watcher
    """Round-10 item 1.  Block until the supervisor RELEASES this watcher -- which it does
    by closing ``dh_r``'s write end in ``_reclaim``, after its own finalizing drain -- or
    until the supervisor DIES (both the handoff and the orphan guard read EOF on its death),
    or a generous ceiling elapses.  Reads no master byte and writes no proof: its ONLY job
    is to keep the session-leader tty alive so the supervisor's drain sees the whole tail
    rather than a revoke-truncated one.  The ceiling exists solely for a supervisor wedged
    holding both fds without dying; it is a large multiple of the drain budget, so in the
    normal path the supervisor always releases (or dies) first."""
    ceiling = max(30.0, budget_s * 8.0)
    deadline = time.monotonic() + ceiling
    fds = [fd for fd in (dh_r, guard_r) if fd >= 0]
    while fds:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        try:
            ready, _, _ = select.select(fds, [], [], min(1.0, remaining))
        except (OSError, ValueError):
            return
        if ready:
            return  # released (drain done) or the supervisor died -- either way, exit


def _finalize_orphaned_capture(master_fd: int, appender: Any, *, budget_s: float,
                               finalized: bytes, fence: str, code: int | None,
                               slave_name: str = "", clock: Any = time.monotonic,
                               reader: Any = os.read,
                               settle_s: float = 0.15) -> dict[str, Any]:
    """The exit watcher's post-exit finalization (item 1, round 9).  Raw ``os`` calls;
    in its own function so a real-pty test can drive it in-process.

    The agent is ALREADY REAPED by the caller (``waitpid`` returned its exit status --
    the strongest exit proof there is), so no further byte can ORIGINATE from the agent.
    This drains what its output left in the pty and decides whether the capture is
    COMPLETE:

    * a clean EOF (``b""``) or ``errno.EIO`` is the pty HANGUP -- every slave descriptor
      is closed -- and the capture is complete (Linux delivers this to the watcher; it
      closes the flip-buffer lost-tail race the round-8 head existed to close);
    * otherwise the drain runs until the master has been QUIET for ``settle_s`` after the
      reap -- and THIS is the only sound "quiescence" gate, because it stands on the
      REAPED exit (not on a final record + silence, which the review forbids): the writer
      of record is provably gone.  It is ``proven`` only when NOTHING still holds the
      slave (:func:`_slave_holders`: the agent's foreground process group is empty and no
      ``/proc`` fd resolves to the slave).  A descendant that kept the slave open -- a
      background dev server, an inherited MCP / language-server stdio -- keeps the group
      non-empty (a normal fork inherits the agent's pgid) or shows in the ``/proc`` scan,
      so the drain is ``unproven`` and the holder is NAMED.  **darwin caveat:** a
      session-leader reader (this watcher) never sees the master EOF while it lives, and
      darwin offers no subprocess-free fd enumeration, so a descendant that ALSO left the
      agent's process group is not detectable here and the ``proven`` gate on darwin is
      "reaped + quiesced + empty foreground group"; the conformance doc states this bound.

    ``EINTR`` is retried; any other read / poll error is ``master_unreadable``.  Then, in
    order: the appender's meta is saved (fsynced) and closed; the finalized record is
    written -- ``proven`` or ``unproven`` per the above, bound to the capture's final
    ``total_bytes`` / ``sha256`` and to the sentinel identity the caller writes NEXT.  The
    caller writes the exit sentinel AFTER this returns, never before.
    """
    deadline = clock() + budget_s
    ended, errno_name, read = "budget", "", 0
    last_data = clock()
    while True:
        now = clock()
        if now >= deadline:
            ended = "budget"
            break
        try:
            ready, _, _ = select.select([master_fd], [], [], min(0.05, deadline - now))
        except InterruptedError:
            continue
        except (OSError, ValueError) as exc:
            ended, errno_name = "master_unreadable", _errno_name(exc)
            break
        if not ready:
            if clock() - last_data >= settle_s:
                # Quiet for the settle window since the last byte, and the agent is
                # reaped: no more bytes can ORIGINATE.  Whether that is FINAL is the
                # holder probe below, not silence alone.
                ended = "quiesced"
                break
            continue
        try:
            chunk = reader(master_fd, 65_536)
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EIO:
                ended, errno_name = "hangup", "EIO"
            else:
                ended, errno_name = "master_unreadable", _errno_name(exc)
            break
        if not chunk:
            ended = "hangup"
            break
        read += len(chunk)
        last_data = clock()
        if appender is not None:
            try:
                appender.append(chunk)
            except Exception:  # noqa: BLE001 - bookkeeping never stops the drain
                pass
    total, digest, records = 0, "", 0
    if appender is not None:
        appender.save_meta()
        appender.close()
        total, records = int(appender.total), int(appender.records)
        digest = appender.digest.hexdigest()
    holders = None
    if ended == "hangup" and appender is not None:
        finality, detail = capture_mod.FINALITY_PROVEN, ""
    elif ended == "quiesced" and appender is not None:
        # Iteration 4 (option B): silence after the reap is `proven` ONLY on a COMPLETE
        # positive absence proof from the SAME fail-closed libproc slave-descriptor authority
        # the supervisor uses (`slave_device_holders`) -- run here by the watcher before it
        # writes any `proven` record.  A `present` holder (on the tty or setsid'd off it) OR
        # an `unreadable` authority (any inaccessible pid: a denied `proc_pidinfo`, a growth
        # failure, the listing itself -- NEVER a silent skip) is `unproven` with the cause
        # NAMED.  If the authority cannot run at all in this forked context it raises and the
        # `except` below records `unproven` -- never `quiesced`-as-proven.
        try:
            holders = slave_device_holders(slave_name, exclude_pids=(os.getpid(),))
        except Exception as exc:  # noqa: BLE001 - a watcher never dies of bookkeeping
            holders = {"method": "libproc", "state": "unreadable",
                       "unenumerable": [{"pid": 0, "errno": repr(exc), "where": "forked"}]}
        state = str(holders.get("state") or "unreadable")
        if state == "proven_absent":
            finality, holders = capture_mod.FINALITY_PROVEN, None
            detail = ""
        elif state == "present":
            finality = capture_mod.FINALITY_UNPROVEN
            detail = ("the agent is reaped and its output quiesced, but a process still holds "
                      f"the pty slave open (pids {holders.get('holders')}); "
                      "the capture may yet grow")
        else:  # unreadable -- fail closed, name what could not be enumerated
            finality = capture_mod.FINALITY_UNPROVEN
            detail = ("the agent is reaped and its output quiesced, but the complete "
                      "slave-descriptor authority could not be run to completion "
                      f"({holders.get('unenumerable')}); a positive absence is not proven")
    else:
        finality = capture_mod.FINALITY_UNPROVEN
        try:
            holders = slave_device_holders(slave_name, exclude_pids=(os.getpid(),))
        except Exception:  # noqa: BLE001
            holders = {"method": "libproc", "state": "unreadable"}
        detail = ("no bounded appender could be built for the capture; nothing vouches "
                  "for the bytes" if appender is None else
                  "the pty output did not quiesce within the post-exit drain bound; a "
                  "descendant may still hold the slave" if ended == "budget" else
                  f"the master could not be read ({errno_name})")
    capture_mod.write_capture_finalized(
        finalized, fence=fence, finality=finality, writer=capture_mod.WRITER_EXIT_WATCHER,
        ended=ended, errno_name=errno_name, total_bytes=total, sha256=digest,
        records=records, exit_how="exit_sentinel", exit_code=code, holders=holders,
        detail=detail)
    return {"ended": ended, "errno": errno_name, "bytes": read, "finality": finality,
            "total_bytes": total, "sha256": digest}


#: Iteration 4/5 (option B).  darwin ``libproc`` constants for the COMPLETE slave-descriptor
#: authority: enumerate EVERY process, and for every process whose file descriptors the kernel
#: lets us read, match each CURRENT vnode fd's (device, inode) against the pty SLAVE's, so a
#: holder is found however it names the fd and INCLUDING an off-tty ``setsid`` descendant.
#: Iteration-5 reviewer corrections are baked into the helpers below: ``proc_listallpids``
#: returns an ENTRY COUNT (F1); a process's identity is NEVER inferred from a denied read -- we
#: inspect its fds directly and let the kernel's own permission boundary classify it (F2); a
#: per-fd failure is a moving fd table, resolved by a stable double scan over a FRESH listing,
#: never a "raced, not a holder" shortcut (F3); and a fixed-offset decode requires the EXACT
#: structure length (F4).
_PROC_ALL_PIDS = 1
_PROC_PIDLISTFDS = 1
_PROC_PIDFDVNODEPATHINFO = 2
_PROC_PIDTBSDINFO = 3
_PROX_FDTYPE_VNODE = 1
_PROC_FDINFO_SIZE = 8                     # sizeof(struct proc_fdinfo): int32 fd + uint32 type
#: sizeof(struct vnode_fdinfowithpath) as darwin ACTUALLY returns it for a valid vnode fd,
#: verified empirically on this host (proc_fileinfo 24 + vnode_info_path incl. vip_path[MAXPATHLEN]
#: = 1200 bytes; pty slave, regular file and tty all return exactly 1200).  A positive
#: ``proc_pidfdinfo`` return of ANY OTHER length is a short/long read (reviewer iter5 F4) and is
#: rejected -- the fixed-offset decode below is valid ONLY for the exact structure.
_VNODE_FDINFOWITHPATH_SIZE = 1200
_BSDINFO_SIZE = 256
#: Byte offsets, verified empirically on darwin arm64/x86_64 (all little-endian):
#:  proc_bsdinfo: pbi_ppid @16, pbi_uid @20, pbi_ruid @28 (after flags/status/xstatus/pid).
#:  vnode_fdinfowithpath: proc_fileinfo (24 bytes) then vinfo_stat -> vst_dev @+0, vst_ino @+8
#:  (fstat of an open pty slave and this decode agree; verified on a live slave fd).
_BSD_PPID_OFF, _BSD_UID_OFF, _BSD_RUID_OFF = 16, 20, 28
_VNODE_STAT_OFF, _VNODE_DEV_OFF, _VNODE_INO_OFF = 24, 0, 8
#: Bounds for the fail-closed enumeration (reviewer iter5 F1/F3).
_PIDLIST_MAX_ATTEMPTS = 6                 # re-query proc_listallpids until the count is stable
_PIDLIST_SLACK = 4096                     # spare entry capacity; a full buffer means truncation
_FD_SCAN_MAX_ATTEMPTS = 8                 # per-process: retries toward two identical clean scans
_FD_SCAN_RETRY_SLEEP_S = 0.001           # let ordinary fd-table churn settle between retries
_SHORT_READ_ERRNO = "short_read"         # sentinel: a positive but wrong-length libproc return

try:
    _LIBPROC = ctypes.CDLL("/usr/lib/libSystem.dylib", use_errno=True)
    _LIBPROC.proc_listallpids.restype = ctypes.c_int
    _LIBPROC.proc_pidinfo.restype = ctypes.c_int
    _LIBPROC.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                      ctypes.c_void_p, ctypes.c_int]
    _LIBPROC.proc_pidfdinfo.restype = ctypes.c_int
    _LIBPROC.proc_pidfdinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_void_p, ctypes.c_int]
except OSError:  # pragma: no cover - non-darwin
    _LIBPROC = None


def _libproc_pidinfo(pid: int, flavor: int, size: int) -> "tuple[bytes | None, int]":
    """One ``proc_pidinfo`` call into a fixed buffer; ``(bytes, 0)`` or ``(None, errno)``.

    Retained for diagnostics and because the identity flavor is a documented libproc entry
    point, but it is DELIBERATELY NOT on the proof path (reviewer iter5 F2): the authority never
    infers a process's UID from an identity read and never skips a process because that read was
    denied.  Holders are found by inspecting file descriptors directly, which the kernel permits
    for exactly the processes whose slave a mode-0620 owner-uid pty could be open in."""
    buf = (ctypes.c_byte * size)()
    got = _LIBPROC.proc_pidinfo(pid, flavor, 0, buf, size)
    if got <= 0:
        return None, (ctypes.get_errno() or errno.EINVAL)
    return bytes(buf)[:got], 0


def _libproc_fd_devino(pid: int, fd: int) -> "tuple[tuple[int, int] | None, object]":
    """The (vst_dev, vst_ino) of one fd's vnode via ``proc_pidfdinfo`` /
    ``PROC_PIDFDVNODEPATHINFO``.  Requires the return length to be EXACTLY
    ``sizeof(struct vnode_fdinfowithpath)`` before decoding any fixed-offset field (reviewer
    iter5 F4: a positive SHORT read must never be decoded from the zero-filled buffer).
    ``(devino, 0)`` or ``(None, errno-or-sentinel)`` on ANY failure.  A non-vnode fd (pipe,
    socket) and a closed fd both return ``EBADF`` here, which is why the caller queries only fds
    whose CURRENT listing type is a vnode and treats an EBADF as a moving fd table, not absence."""
    buf = (ctypes.c_byte * _VNODE_FDINFOWITHPATH_SIZE)()
    got = _LIBPROC.proc_pidfdinfo(pid, fd, _PROC_PIDFDVNODEPATHINFO, buf,
                                  _VNODE_FDINFOWITHPATH_SIZE)
    if got <= 0:
        return None, (ctypes.get_errno() or errno.EINVAL)
    if got != _VNODE_FDINFOWITHPATH_SIZE:
        return None, _SHORT_READ_ERRNO       # a partial/oversized structure is not authority
    raw = bytes(buf)
    base = _VNODE_STAT_OFF
    dev = struct.unpack_from("<I", raw, base + _VNODE_DEV_OFF)[0]
    ino = struct.unpack_from("<Q", raw, base + _VNODE_INO_OFF)[0]
    return (dev, ino), 0


def _slave_reference_devino(slave_name: str) -> "tuple[tuple[int, int] | None, str]":
    """The pty slave's (device, inode) taken by ``fstat`` of an OPEN slave fd -- NOT
    ``stat`` of the path, whose devfs node reports a DIFFERENT inode than the opened pty
    vnode (measured).  ``O_NOCTTY`` so this transient open never acquires the tty; closed
    before any scan.  ``(devino, "")`` or ``(None, reason)``."""
    if not slave_name:
        return None, "no slave name"
    fd = -1
    try:
        fd = os.open(slave_name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        st = os.fstat(fd)
        return (st.st_dev & 0xFFFFFFFF, st.st_ino), ""
    except OSError as exc:
        return None, f"fstat(slave): {_errno_name(exc)}"
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def _libproc_list_all_pids() -> "tuple[list[int] | None, str | None]":
    """Every pid on the host via ``proc_listallpids``, whose fill return is the ENTRY COUNT --
    the number of pids written, NOT a byte length (reviewer iter5 F1; verified: fill_return
    tracks the number of nonzero entries, and dividing by ``sizeof(int32)`` silently drops three
    quarters of the table).  Re-query into a buffer with slack until the fill is STRICTLY smaller
    than the capacity (a fill that reaches capacity is a truncation/growth and is retried,
    bounded).  ``(pids, None)`` or ``(None, reason)`` when it never stabilises."""
    reason = "listallpids_unstable"
    for _ in range(_PIDLIST_MAX_ATTEMPTS):
        n = _LIBPROC.proc_listallpids(None, 0)             # entries needed
        if n <= 0:
            return None, f"listallpids_count:{_errno_name_n(ctypes.get_errno())}"
        cap = int(n) + _PIDLIST_SLACK                      # entries
        buf = (ctypes.c_int32 * cap)()
        got = _LIBPROC.proc_listallpids(buf, ctypes.sizeof(buf))   # buffersize BYTES; returns ENTRIES
        if got <= 0:
            return None, f"listallpids_fill:{_errno_name_n(ctypes.get_errno())}"
        if got >= cap:                                     # buffer full -> truncated / still growing
            reason = "listallpids_truncated"
            continue
        return [buf[i] for i in range(int(got)) if buf[i] > 0], None
    return None, reason


def _libproc_list_vnode_fds(pid: int) -> "tuple[list[int] | None, object]":
    """A FRESH ``PROC_PIDLISTFDS`` for ``pid``, returning the fds whose CURRENT type is a vnode
    -- never a stale snapshot type (reviewer iter5 F3: a holder can ``dup2`` the slave onto a fd
    that was a pipe in an earlier snapshot, so the type must be re-read every scan).
    ``proc_pidinfo(PROC_PIDLISTFDS)`` returns a BYTE length (a whole number of
    ``sizeof(struct proc_fdinfo)`` entries); a length that is not a whole number of entries, or
    that fills the buffer (growth), is rejected (F4).  ``(fds, 0)`` or ``(None, errno)``."""
    size = _LIBPROC.proc_pidinfo(pid, _PROC_PIDLISTFDS, 0, None, 0)   # bytes needed
    if size <= 0:
        return None, (ctypes.get_errno() or errno.EINVAL)
    cap = int(size) + 16 * _PROC_FDINFO_SIZE               # byte slack; a full buffer => growth
    buf = (ctypes.c_byte * cap)()
    filled = _LIBPROC.proc_pidinfo(pid, _PROC_PIDLISTFDS, 0, buf, cap)   # bytes written
    if filled <= 0:
        return None, (ctypes.get_errno() or errno.EINVAL)
    if filled >= cap or filled % _PROC_FDINFO_SIZE != 0:
        return None, _SHORT_READ_ERRNO                     # growth / malformed listing length
    raw = bytes(buf)
    fds: list[int] = []
    for i in range(filled // _PROC_FDINFO_SIZE):
        fd = struct.unpack_from("<i", raw, i * _PROC_FDINFO_SIZE)[0]
        ftype = struct.unpack_from("<I", raw, i * _PROC_FDINFO_SIZE + 4)[0]
        if ftype == _PROX_FDTYPE_VNODE:
            fds.append(fd)
    return fds, 0


def _scan_process_for_slave(pid: int, ref: "tuple[int, int]",
                            exclude: "set[int]") -> "tuple[str, object]":
    """Inspect ONE process for the slave with a fail-closed, race-resistant protocol
    (reviewer iter5 F3).  Each attempt re-lists the process's fds FRESH and queries every
    CURRENT vnode fd's ``(dev, ino)``; ANY per-fd failure that is not a whole-process exit means
    the fd table moved under us (a holder can relocate the slave onto a former non-vnode fd and
    close the originals -- the closed originals then read ``EBADF``), so the scan is discarded
    and retried.  A process counts as inspected only after TWO consecutive, complete, IDENTICAL
    scans, bounded; otherwise it is ``unstable``.  Returns ``('match', pid)`` |
    ``('clean', None)`` | ``('gone', None)`` | ``('denied', None)`` | ``('unstable', reason)``.

    ``denied`` (the listing itself is ``EPERM``/``EACCES``) is the kernel refusing to let us read
    another uid's fds; since we are not root, that is trustworthy evidence the process is not our
    uid, and a mode-0620 owner-uid pty slave cannot be open in a non-owner, non-root process.  A
    changed-uid (``setuid``) descendant that dropped our uid while holding the slave is out of
    this authority's scope and is NOT claimed as covered (reviewer iter5 F2)."""
    prev: "dict[int, tuple[int, int]] | None" = None
    for _ in range(_FD_SCAN_MAX_ATTEMPTS):
        fds, err = _libproc_list_vnode_fds(pid)
        if fds is None:
            if _errno_is_gone(err):
                return "gone", None
            if err in (errno.EPERM, errno.EACCES):
                return "denied", None
            prev = None                                    # transient listing failure -> retry
            time.sleep(_FD_SCAN_RETRY_SLEEP_S)
            continue
        scan: "dict[int, tuple[int, int]]" = {}
        failed = False
        for fd in fds:
            devino, fe = _libproc_fd_devino(pid, fd)
            if devino is None:
                if _errno_is_gone(fe):
                    return "gone", None
                if fe in (errno.EPERM, errno.EACCES):
                    # This fd's vnode is permission-restricted (a TCC/sandbox-protected resource
                    # that macOS refuses to introspect even for the owner -- common on fd 3 of a
                    # user's launchd agents).  A pty slave vnode is NEVER permission-restricted:
                    # the slave's own vnode info is readable in the reference AND in a real holder
                    # (verified), so a fd we are refused is PROVABLY not the slave.  Skip THIS fd
                    # (never a match here) and keep inspecting the rest; do not fail the scan --
                    # otherwise every host with a restricted-fd agent is permanently `unreadable`.
                    scan[fd] = None                        # record it as inspected-but-restricted
                    continue
                failed = True                              # EBADF (closed/retyped) / short: a RACE / truncation
                break
            scan[fd] = devino
        if failed:
            prev = None
            time.sleep(_FD_SCAN_RETRY_SLEEP_S)
            continue
        if prev is not None and scan == prev:
            for devino in scan.values():
                if devino is not None and devino == ref and pid not in exclude:
                    return "match", pid
            return "clean", None
        prev = scan                                        # first clean scan; confirm it is stable
    return "unstable", "fd_table_never_stabilised"


def slave_device_holders(slave_name: str, *, exclude_pids: "Sequence[int]" = ()
                         ) -> dict[str, Any]:
    """Iteration 4/5 (option B) -- a COMPLETE, fail-closed slave-descriptor authority (darwin
    ``libproc``), the ONE sanctioned exception to "the kernel hangup is the only proof": the
    supervisor's release-of-the-deferring-watcher (whose revoke delivers the real hangup) is
    gated on this proof, and the orphan watcher uses it before writing any ``proven`` record.

    Enumerate EVERY process (``proc_listallpids``, ENTRY-COUNT semantics, truncation/growth
    rejected -- F1).  The gate for inspecting a process is FD INSPECTABILITY, not an identity
    read: for every pid we take a stable double scan of its CURRENT vnode fds
    (:func:`_scan_process_for_slave`) and match each ``(vst_dev, vst_ino)`` against the slave's
    (from ``fstat`` of the slave device).  A match on a pid other than the excluded agent /
    watcher is a HOLDER (a retained slave, on the tty or ``setsid``'d off it).

    The reviewer-iter5 corrections are the substance of this authority:

    * F1 -- ``proc_listallpids`` returns the number of PID ENTRIES; the fill is used directly
      (never divided by ``sizeof(int32)``), and a fill that reaches buffer capacity is a
      truncation/growth that is retried and, if it never settles, ``unreadable``.
    * F2 -- a process's UID is NEVER inferred from a denied identity read.  A same-uid holder
      whose ``PROC_PIDTBSDINFO`` is denied is still caught because its FD LISTING succeeds and is
      inspected.  A process whose fd LISTING the kernel denies (``EPERM``/``EACCES``) is, since we
      are not root, provably not our uid and cannot hold a mode-0620 owner-uid slave; it is the
      diagnostic ``other_uid`` bucket.  Changed-uid (``setuid``) descendants are explicitly OUT
      OF SCOPE and not claimed as covered (no false lineage claim).
    * F3 -- a per-fd failure is a MOVING fd table, not proof of absence: the process's scan is
      re-taken over a FRESH listing (so a slave ``dup2``-relocated onto a former pipe fd is seen
      as a vnode now), and only TWO consecutive, complete, identical scans count as inspected;
      an fd table that never stabilises within the bound is ``unstable`` -> ``unenumerable``.
    * F4 -- a fixed-offset decode requires the EXACT structure length; a positive short/long
      ``proc_pidfdinfo`` or a malformed listing length is ``unenumerable`` for that pid, never
      decoded from the zero-filled buffer.

    ``{"method": "libproc", "state": "present"|"proven_absent"|"unreadable", "holders": [pid],
    "unenumerable": [{"pid", "errno", "where"}], "other_uid": [pid], "gone": [pid],
    "device": ...}``.  ``state`` is ``present`` if any holder, else ``unreadable`` if any process
    was ``unenumerable`` (fail closed -- an incomplete scan is NEVER absence), else
    ``proven_absent`` (processes that merely exited mid-scan, or are other-uid and thus cannot
    hold the slave, do not block the proof)."""
    out: dict[str, Any] = {"method": "libproc", "device": str(slave_name or ""),
                           "state": "unreadable", "holders": [], "unenumerable": [],
                           "other_uid": [], "gone": []}
    if _LIBPROC is None:
        out["unenumerable"].append({"pid": 0, "errno": "no_libproc", "where": "load"})
        return out
    ref, reason = _slave_reference_devino(slave_name)
    if ref is None:
        out["unenumerable"].append({"pid": 0, "errno": reason, "where": "slave_fstat"})
        return out
    exclude = set(int(p) for p in exclude_pids if p)
    pids, list_reason = _libproc_list_all_pids()
    if pids is None:
        out["unenumerable"].append({"pid": 0, "errno": list_reason, "where": "listallpids"})
        return out
    holders: list[int] = []
    for pid in pids:
        if pid in exclude:
            continue
        result, detail = _scan_process_for_slave(pid, ref, exclude)
        if result == "match":
            holders.append(pid)
        elif result == "clean":
            continue
        elif result == "gone":
            out["gone"].append(pid)
        elif result == "denied":
            out["other_uid"].append(pid)
        else:  # unstable -> the fd table never stabilised: fail closed, name the pid
            out["unenumerable"].append({"pid": pid, "errno": str(detail or "unstable"),
                                        "where": "fd_scan"})
    out["holders"] = sorted(set(holders))
    out["gone"] = sorted(set(out["gone"]))
    out["other_uid"] = sorted(set(out["other_uid"]))
    if out["holders"]:
        out["state"] = "present"
    elif out["unenumerable"]:
        out["state"] = "unreadable"          # fail closed: an incomplete scan is never absence
    else:
        out["state"] = "proven_absent"
    return out



def _errno_name_n(code: object) -> str:
    if isinstance(code, str):
        return code
    try:
        return errno.errorcode.get(int(code or 0), str(code))
    except (TypeError, ValueError):
        return str(code)


#: A candidate pid that VANISHES during the scan (``proc_pidinfo`` / ``proc_pidfdinfo`` returns
#: ``ESRCH`` / ``ENOENT``) has EXITED -- the kernel released every descriptor it held at exit --
#: so it provably holds no slave descriptor and is recorded ``gone`` (diagnostic), NEVER
#: ``unenumerable``.  This is categorically distinct from a DENIED (``EPERM`` / ``EACCES``)
#: inspection of a LIVE process (the reviewer's F1 interposer), which stays ``unenumerable`` ->
#: ``unreadable`` -> unproven.  Without this split a busy host -- which churns transient same-uid
#: pids constantly -- would read ``unreadable`` over a genuinely clean slave whenever a bystander
#: process exited between the listing and its inspection.
_PID_GONE_ERRNOS = frozenset((errno.ESRCH, errno.ENOENT))


def _errno_is_gone(code: object) -> bool:
    if not isinstance(code, int):
        return False
    return int(code or 0) in _PID_GONE_ERRNOS


def _slave_holder_state(holders: Mapping[str, Any]) -> str:
    """The tri-state absence verdict over :func:`_slave_holders` evidence (round-10 item 2):

    * ``present`` -- something still holds the slave: the agent's foreground process group
      has members, or a ``/proc`` fd resolves to it;
    * ``proven_absent`` -- a COMPLETE positive absence proof: the foreground-group probe
      COMPLETED and found the group empty and, where ``/proc`` exists, a COMPLETE scan
      found no holder;
    * ``unreadable`` -- an authority the absence proof needs could not be read (a failed
      ``tcgetpgrp`` / ``killpg``, an unreadable ``/proc``, or a skipped ``/proc/<pid>/fd``).

    Only ``proven_absent`` may authorise ``proven``.  ``present`` and ``unreadable`` are
    both ``unproven`` -- an incomplete or unreadable probe is NEVER collapsed into absence,
    which is the exact defect this replaces: failed ``tcgetpgrp`` / unreadable ``/proc`` /
    skipped fd entries used to fall through to ``foreground_group_present=None, rows=[]`` and
    read as `absent`, stamping ``proven`` over a probe that proved nothing."""
    if holders.get("foreground_group_present") is True or holders.get("rows"):
        return "present"
    if holders.get("unreadable"):
        return "unreadable"
    if holders.get("foreground_probe") != "complete":
        return "unreadable"
    if holders.get("proc_scan") == "incomplete":
        return "unreadable"
    return "proven_absent"


def _errno_name(exc: BaseException) -> str:
    code = getattr(exc, "errno", None)
    return errno.errorcode.get(code, str(code)) if code is not None else type(exc).__name__


def _slave_holders(master_fd: int, slave_name: str, *,
                   tcgetpgrp: Any = os.tcgetpgrp, killpg: Any = os.killpg,
                   listdir: Any = os.listdir, readlink: Any = os.readlink,
                   isdir: Any = os.path.isdir) -> dict[str, Any]:
    """What still holds the pty SLAVE after the agent's exit -- the retained-slave cause,
    made observable (round-9 Linux descendant / PTY scope, choice (a)), as a TRI-STATE
    result (round-10 item 2): every probe records whether it COMPLETED, and any authority
    that could not be read is NAMED in ``unreadable`` rather than collapsed into absence.

    Raw ``os`` calls only (a forked watcher runs this; nothing may spawn).  Two probes:
    the slave's FOREGROUND process group (``tcgetpgrp`` on the master) and whether that
    group still has members (``killpg(pgid, 0)``) -- both platforms; and on Linux a
    ``/proc/<pid>/fd`` scan for descriptors that resolve to the slave's path, naming each
    holder's pid and ``comm``.  darwin offers no subprocess-free fd enumeration here, so
    ``proc_scan`` is ``absent`` and the foreground-group probe is the only absence axis;
    the supervisor-side reader adds the tty-scoped process table (``ps -t``) when it reports
    the refusal.  The ``os`` calls are seams so error injection (``tcgetpgrp -> EIO``,
    ``/proc listdir -> EACCES``, a skipped fd entry) is lockable over a real pty without
    patching ``os`` globally.
    """
    out: dict[str, Any] = {"slave": slave_name, "method": "pgid_probe", "rows": [],
                           "foreground_pgid": None, "foreground_group_present": None,
                           "foreground_probe": "unreadable", "proc_scan": "absent",
                           "unreadable": []}
    try:
        pgid = int(tcgetpgrp(master_fd))
        out["foreground_pgid"] = pgid
        if pgid <= 0:
            # Follow-up (c): ``tcgetpgrp() <= 0`` means "no usable foreground group", NOT a
            # live holder -- and ``killpg(0, 0)`` / ``killpg(<negative>, 0)`` would signal
            # the watcher's OWN process group (or, for 0, its group; the round-8 shape's
            # latent suicide).  It is never called; this is a COMPLETE, positive absence on
            # the foreground axis.
            out["foreground_group_present"] = False
            out["foreground_probe"] = "complete"
        else:
            try:
                killpg(pgid, 0)
                out["foreground_group_present"] = True
                out["foreground_probe"] = "complete"
            except ProcessLookupError:
                out["foreground_group_present"] = False
                out["foreground_probe"] = "complete"
            except PermissionError:
                # A group we may not signal still EXISTS -- a present holder, proven.
                out["foreground_group_present"] = True
                out["foreground_probe"] = "complete"
            except OSError as exc:
                out["foreground_group_present"] = None
                out["unreadable"].append(f"killpg:{_errno_name(exc)}")
    except OSError as exc:
        out["foreground_group_present"] = None
        out["unreadable"].append(f"tcgetpgrp:{_errno_name(exc)}")
    if slave_name and isdir("/proc"):
        out["method"] = "proc_fd_scan"
        complete = True
        rows: list[dict[str, Any]] = []
        try:
            entries = listdir("/proc")
        except OSError as exc:
            entries = []
            complete = False
            out["unreadable"].append(f"proc_listdir:{_errno_name(exc)}")
        for entry in entries:
            if not entry.isdigit() or entry == str(os.getpid()):
                continue
            fd_dir = f"/proc/{entry}/fd"
            try:
                fds = listdir(fd_dir)
            except FileNotFoundError:
                continue                       # the process exited: it holds nothing
            except OSError as exc:
                # EACCES etc: this pid cannot be ruled out, so absence is not COMPLETE.
                complete = False
                out["unreadable"].append(f"proc_fd:{entry}:{_errno_name(exc)}")
                continue
            held = []
            for name in fds:
                try:
                    if readlink(f"{fd_dir}/{name}") == slave_name:
                        held.append(int(name))
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    complete = False
                    out["unreadable"].append(f"proc_fd_link:{entry}/{name}:{_errno_name(exc)}")
                    continue
            if held:
                comm = ""
                try:
                    with open(f"/proc/{entry}/comm", "rb") as handle:
                        comm = handle.read().decode("utf-8", "replace").strip()
                except OSError:
                    pass
                rows.append({"pid": int(entry), "comm": comm, "fds": sorted(held)})
        out["rows"] = rows
        out["proc_scan"] = "complete" if complete else "incomplete"
    return out


def _drain_wakeups(wake_r: int) -> None:  # pragma: no cover - runs in the forked watcher
    """Empty the SIGCHLD wake-up pipe so the next ``select`` waits again."""
    try:
        os.set_blocking(wake_r, False)
        while os.read(wake_r, 64):
            pass
    except (BlockingIOError, OSError):
        pass


def _drain_once(master_fd: int, appender: Any, *, budget: float,
                wake_r: int = -1) -> int:  # pragma: no cover
    """Read what is ready on the master within ``budget`` seconds; hand it to the BOUNDED
    appender (finding 4), which decides what reaches the file.  Returns early -- reading
    nothing -- when the SIGCHLD wake-up pipe fires instead."""
    fds = [master_fd] + ([wake_r] if wake_r >= 0 else [])
    try:
        ready, _, _ = select.select(fds, [], [], budget)
    except (OSError, ValueError):
        return 0
    if wake_r >= 0 and wake_r in ready:
        _drain_wakeups(wake_r)
    if master_fd not in ready:
        return 0
    try:
        chunk = os.read(master_fd, 65_536)
    except OSError:
        return 0
    if chunk and appender is not None:
        try:
            appender.append(chunk)
        except Exception:  # noqa: BLE001 - bookkeeping never stops the drain
            pass
    return len(chunk)


def _read_handoff(fd: int, *, budget_ms: int = 10_000) -> int:
    """The agent pid the session leader reports, or ``0``.  Bounded, never blocking forever.

    ``0`` means "no identity was reported" and the caller raises; it is never coerced into
    a plausible pid.  A wrong pid here would make every later ownership check refuse -- or,
    far worse, address a stranger.
    """
    import select as _select
    deadline = time.time() + budget_ms / 1000.0
    buf = b""
    while time.time() < deadline and b"\n" not in buf:
        try:
            ready, _, _ = _select.select([fd], [], [], 0.05)
        except (OSError, ValueError):
            return 0
        if not ready:
            continue
        try:
            chunk = os.read(fd, 64)
        except OSError:
            return 0
        if not chunk:
            break
        buf += chunk
    try:
        return int(buf.split(b"\n", 1)[0])
    except (ValueError, IndexError):
        return 0


def _set_raw(fd: int) -> None:
    """Raw mode, so the paste frame is not echoed back at the reader.

    An echoed frame is not merely noise: it is the exact bytes of the prompt appearing in
    the capture, which a screen-reading completion check would mistake for output.  This
    runtime does not accept readiness on screen text at all, but the capture is still the
    transcript an operator reads, and a doubled prompt makes it unreadable.
    """
    try:
        mode = termios.tcgetattr(fd)
    except termios.error:
        return
    mode[3] = mode[3] & ~(termios.ECHO | termios.ICANON)  # lflag
    try:
        termios.tcsetattr(fd, termios.TCSANOW, mode)
    except termios.error:
        pass


#: The `termios` special characters the line discipline may CONSUME (a signal, a line
#: edit, flow control) instead of echoing -- each gated on the mode flag that arms it.
#: Read from the live `c_cc` array, never from a table of "usual" bindings.
_SPECIAL_CC_BY_FLAG = (
    ("isig", ("VINTR", "VQUIT", "VSUSP", "VDSUSP")),
    ("icanon", ("VEOF", "VEOL", "VEOL2", "VERASE", "VKILL", "VWERASE", "VREPRINT",
                "VERASE2")),
    ("iexten", ("VLNEXT", "VDISCARD", "VSTATUS")),
    ("ixon", ("VSTART", "VSTOP")),
)
_POSIX_VDISABLE = 0xFF


def _cc_byte(cc: Sequence[Any], name: str) -> int | None:
    index = getattr(termios, name, None)
    if index is None or index >= len(cc):
        return None
    value = cc[index]
    if isinstance(value, (bytes, bytearray)):
        return value[0] if value else None
    if isinstance(value, int):
        return value
    return None


def termios_evidence(fd: int) -> dict[str, Any] | None:
    """The line-discipline flags that decide WHAT an echo of a write to ``fd`` looks like.

    F-002 / B1 (iteration-8 review).  Read with ``tcgetattr`` on the pty MASTER at delivery
    time: the master and slave share one termios, so this is the slave's CURRENT mode --
    including whatever the agent itself set after ``execve`` -- and not the mode this
    runtime configured at spawn.  ``None`` when it cannot be read; the lifecycle then
    reports the echo as `echo_unproven`, never guesses a transformation.

    Every value is a plain bool/int so the record can travel in evidence and in the journal.
    ``special_bytes`` are the ``c_cc`` bytes the discipline would CONSUME rather than echo
    under the flags in force; a payload carrying one of them has no derivable echo.
    """
    try:
        mode = termios.tcgetattr(fd)
    except (termios.error, OSError, ValueError):
        return None
    iflag, oflag, _cflag, lflag, _ispeed, _ospeed, cc = mode
    # Tab expansion is `TAB3` on both kernels: Linux spells it `XTABS == TAB3 == TABDLY`,
    # BSD/macOS `OXTABS == TAB3` (a single bit inside `TABDLY`).  Testing the `TAB3` bits
    # for equality is the one check that is exact on both.
    tab3 = getattr(termios, "TAB3", 0)
    sysname = os.uname().sysname
    flags: dict[str, Any] = {
        # Which LINE DISCIPLINE these flags were read from (iteration-3 CI correction): the
        # lifecycle derives the echo per discipline and refuses to derive one for a kernel
        # the evidence does not name.  BSD `ttydisc` on Darwin, `n_tty` on Linux; anything
        # else is recorded as `None` and resolves `transport_unrecorded`.
        "platform": sysname,
        "discipline": {"Darwin": "bsd_ttydisc", "Linux": "linux_n_tty"}.get(sysname),
        "echo": bool(lflag & termios.ECHO),
        "echoctl": bool(lflag & getattr(termios, "ECHOCTL", 0)),
        "echonl": bool(lflag & getattr(termios, "ECHONL", 0)),
        "icanon": bool(lflag & termios.ICANON),
        "isig": bool(lflag & termios.ISIG),
        "iexten": bool(lflag & getattr(termios, "IEXTEN", 0)),
        "ixon": bool(iflag & termios.IXON),
        "opost": bool(oflag & termios.OPOST),
        "onlcr": bool(oflag & termios.ONLCR),
        "ocrnl": bool(oflag & getattr(termios, "OCRNL", 0)),
        "onocr": bool(oflag & getattr(termios, "ONOCR", 0)),
        "onlret": bool(oflag & getattr(termios, "ONLRET", 0)),
        "tab_expand": bool(tab3) and (oflag & tab3) == tab3,
        "icrnl": bool(iflag & termios.ICRNL),
        "inlcr": bool(iflag & termios.INLCR),
        "igncr": bool(iflag & termios.IGNCR),
    }
    special: set[int] = set()
    for flag, names in _SPECIAL_CC_BY_FLAG:
        if not flags[flag]:
            continue
        for name in names:
            value = _cc_byte(cc, name)
            if value is not None and value != _POSIX_VDISABLE:
                special.add(int(value))
    flags["special_bytes"] = sorted(special)
    return flags


def echo_transport(fd: int | None, *, kind: str, framed: bool, cols: int) -> dict[str, Any]:
    """The `EchoTransport` record for ONE delivery, read at the moment of that delivery.

    ``kind`` is ``"argv"`` when the payload left with the ``execve`` (the line discipline
    never saw it, so it cannot echo it) and ``"pty_write"`` when it was written to the pty.
    ``framed`` says whether a bracketed-paste frame was written around it.  The termios
    block is the evidence the lifecycle derives the expected echo FROM; it is ``None``, not
    a default, when it could not be read.
    """
    record: dict[str, Any] = {"kind": kind, "framed": bool(framed), "cols": int(cols),
                              "termios": None, "read_at": _now_iso()}
    if kind == "pty_write" and fd is not None and fd >= 0:
        record["termios"] = termios_evidence(fd)
    return record


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- the R-A half of the readiness quorum ----------------------------------------------
class ProcessLivenessProof(TypedDict):
    """R-A: readiness evidence that reads ZERO bytes of terminal output.

    Every field here comes from the process table, ``waitpid``, ``tcgetpgrp`` or
    ``readlink``.  No frame of any shape -- login, update, permission, setup, or something
    nobody has named -- can influence any of them, which is why the readiness quorum is
    correct without knowing what G-4's update prompt looks like on the wire.
    """

    identity_matches: bool
    not_exited: bool
    foreground_is_child_group: bool
    foreground_executable_matches: bool
    observed: dict[str, Any]


def liveness_proof(record: Mapping[str, Any], *, snapshot: Mapping[str, Any],
                   master_fd: int | None, expected_binary: str,
                   waitpid_status: Any = None,
                   tcgetpgrp: Any = None,
                   resolve_executable: Any = None) -> ProcessLivenessProof:
    """Build R-A.  Reads the OS, never the terminal.

    ``foreground_executable_matches`` compares the resolved executable of the pty's
    foreground process against the preflight-resolved ``realpath(profile.binary)``.  That
    is what rules out a plain shell and an updater helper holding the foreground while the
    agent is not running -- a distinction no amount of output-reading can make.
    """
    row = row_for(snapshot, int(record["pid"])) if snapshot.get("readable") else None
    # `sid` is compared ONLY where the platform reported it.  Darwin's ps answers 0 for the
    # session id, and treating a zero as a conflict would make R-A unsatisfiable on the MVP's
    # own platform.  A zero is "no evidence on this axis", never a fabricated match: the tty
    # and the process group -- the axes that actually establish ownership -- are still
    # required, and `standalone_identity.verify` applies the same rule.
    identity_matches = bool(
        row is not None
        and row["tty"] == record.get("captured_tty")
        and row["pgid"] == record.get("pgid")
        and (not row["sid"] or row["sid"] == record.get("sid")))

    if waitpid_status is None:
        not_exited = _waitpid_not_exited(int(record["pid"]))
    else:
        not_exited = bool(waitpid_status())

    fg_pgid: int | None
    if tcgetpgrp is not None:
        fg_pgid = tcgetpgrp()
    elif master_fd is not None:
        try:
            fg_pgid = os.tcgetpgrp(master_fd)
        except OSError:
            fg_pgid = None
    else:
        fg_pgid = None
    # -- leg 3: the child's group IS the pty's foreground group ------------------------
    # An EQUALITY, not a membership test.  The spawn topology puts the agent in its own
    # process group and hands that group the pty foreground (see :func:`spawn`), so the
    # recorded pgid is exactly what `tcgetpgrp` must answer.  Accepting "some descendant
    # group" instead would let any process the agent forked stand in for the agent.
    foreground_is_child_group = fg_pgid is not None and fg_pgid == int(record["pgid"])

    # -- leg 4: the foreground process IS the profile's binary --------------------------
    # DESIGN §D5.3(4) R-A: "that foreground process's resolved executable equals the
    # preflight-resolved realpath(profile.binary) -- not a shell, not an updater helper."
    # Implemented as literal executable IDENTITY and nothing weaker.
    #
    # The foreground process is the LEADER of the foreground process group -- the row whose
    # pid equals the foreground pgid -- which the spawn topology guarantees is the agent
    # itself, because the agent calls `setpgid(0, 0)` and then `tcsetpgrp`.  Its image is
    # `realpath(profile.binary)` because that is the exact path :func:`spawn` passes to
    # `execve`.
    #
    # Deliberately NOT accepted, and each for a stated reason:
    #   * a match on the foreground COMMAND LINE -- an interpreter or an updater helper can
    #     carry the expected path as an ARGUMENT while the running image is something else,
    #     so a command-line token is no more identity than a terminal title is, and this
    #     ticket forbids resting identity on a title outright;
    #   * a search of OTHER rows in the session or the process group -- "some process
    #     somewhere in this scope is the binary" is not "the foreground process is the
    #     binary", and it is satisfied while a shell holds the foreground and the agent sits
    #     stopped or in the background.
    # Both were tried and both are gone.  If the platform cannot answer, leg 4 stays False
    # and R-A fails closed, which is the correct answer to an unprovable question.
    expected = os.path.realpath(expected_binary) if expected_binary else ""
    resolved = ""
    foreground_executable_matches = False
    if fg_pgid is not None and expected:
        fg_row = row_for(snapshot, fg_pgid)
        if fg_row is not None and fg_row["pgid"] == fg_pgid:
            resolver = resolve_executable or _resolve_executable
            resolved = resolver(fg_pgid) or ""
            foreground_executable_matches = bool(resolved) and resolved == expected

    return {"identity_matches": identity_matches, "not_exited": not_exited,
            "foreground_is_child_group": foreground_is_child_group,
            "foreground_executable_matches": foreground_executable_matches,
            "observed": {"row": row, "foreground_pgid": fg_pgid,
                         "foreground_executable": resolved, "expected_executable": expected}}


def liveness_satisfied(proof: Mapping[str, Any]) -> bool:
    """R-A holds iff ALL four legs hold.  Conjunctive, deliberately."""
    return all(bool(proof.get(key)) for key in
               ("identity_matches", "not_exited", "foreground_is_child_group",
                "foreground_executable_matches"))


def _waitpid_not_exited(pid: int) -> bool:
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        # Not our child (a stranger process asking) -- fall back to a signal-0 probe, which
        # answers "does a process with this pid exist", the most this caller can know.
        return _pid_exists(pid)
    except OSError:
        return False
    return reaped == 0


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM  # exists, but not ours to signal
    return True


def _resolve_executable(pid: int) -> str:
    """The KERNEL's answer for what image ``pid`` is running, resolved, or ``""``.

    This is the single input to R-A leg 4, so what it may and may not read matters more
    than anything else in this module.

    **It must not be derived from anything the process controls.**  MEASURED on the MVP
    platform: a process exec'd from image ``X`` with ``argv[0] = "totally-not-the-image"``
    is reported by ``ps -o comm=`` AND by ``ps -o args=`` as ``totally-not-the-image``,
    while ``proc_pidpath`` still reports ``X``.  ``argv[0]`` and the command line are
    caller-supplied strings; an image path from the kernel is not.  Resting identity on the
    former would be the same class of error as resting it on a terminal title, which this
    ticket forbids outright -- so this function reads:

    * Linux: ``/proc/<pid>/exe``, a kernel-maintained symlink to the image.
    * Darwin: ``proc_pidpath(2)`` from ``libproc``, the kernel's own path for the image.

    and NOTHING else.  An empty string means "this platform did not answer", which makes
    leg 4 false and R-A fail closed -- the correct answer to an unprovable question, and
    never an invitation to consult a weaker source.
    """
    try:  # linux
        return os.path.realpath(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        pass
    path = _proc_pidpath(pid)
    return os.path.realpath(path) if path else ""


#: ``PROC_PIDPATHINFO_MAXSIZE``.  Resolved lazily and cached, including the NEGATIVE answer,
#: so a host without ``libproc`` pays for the lookup once and then fails closed cheaply.
_LIBPROC_MAXPATH = 4096
_libproc: Any = None


def _libproc_handle() -> Any:
    global _libproc
    if _libproc is None:
        import ctypes
        import ctypes.util
        _libproc = False
        try:
            lib = ctypes.CDLL(ctypes.util.find_library("System")
                              or "/usr/lib/libSystem.B.dylib", use_errno=True)
            lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            lib.proc_pidpath.restype = ctypes.c_int
            _libproc = lib
        except (OSError, AttributeError):
            _libproc = False
    return _libproc or None


def _proc_pidpath(pid: int) -> str:
    """Darwin's kernel-sourced image path for ``pid``, or ``""``."""
    lib = _libproc_handle()
    if lib is None:
        return ""
    import ctypes
    buffer = ctypes.create_string_buffer(_LIBPROC_MAXPATH)
    try:
        written = lib.proc_pidpath(int(pid), buffer, _LIBPROC_MAXPATH)
    except (OSError, ValueError):
        return ""
    if written <= 0:
        return ""
    return buffer.value.decode("utf-8", "surrogateescape")


# ---- signalling ------------------------------------------------------------------------
def signal_target(record: Mapping[str, Any], decision: Mapping[str, Any], sig: int, *,
                  permit: Any, snapshot: Mapping[str, Any],
                  killpg: Any = None, kill: Any = None) -> dict[str, Any]:
    """Send ``sig``, honouring the ownership decision.  Takes a :class:`Permit`.

    The permit parameter is not decoration: it is the only way into this function, and only
    :func:`standalone_identity.assert_may_act` can produce one.  A caller cannot signal a
    process without having re-verified ownership, because there is no overload that omits it.

    ``scope == "none"`` sends NOTHING and reports the refusal.  ``scope == "root"`` sends to
    the single pid, never the group -- that is the tty-shared case, where a group signal
    would reach the supervisor itself.
    """
    identity.require_permit(permit, record, "signal")
    if decision.get("verdict") != "owned" or decision.get("scope") == "none":
        return {"sent": (), "refusal": decision.get("refusal") or "not_owned",
                "scope": "none"}
    send_group = killpg or os.killpg
    send_one = kill or os.kill
    sent: list[dict[str, Any]] = []
    if decision["scope"] == "group":
        # C4: descendant groups FIRST, the session leader LAST.
        #
        # The leader's group is the RECORD'S SID, not its pgid: a session leader's pgid is
        # its own pid, which is its sid, and the agent lives in a DIFFERENT group under it
        # (see :func:`spawn`).  Using the agent's pgid here would invert C4 -- it would
        # signal the leader first, killing the process that has to reap the agent and write
        # the exit sentinel, and a proven exit would become an unprovable one.  Falls back
        # to the pgid where no sid was recorded, which is the pre-topology shape.
        leader = int(record.get("sid") or record["pgid"])
        for pgid in descendant_groups(snapshot, leader_pgid=leader):
            _try(sent, "descendant_groups", pgid, lambda: send_group(pgid, sig))
        if leader == int(record["pid"]):
            # No separate exit watcher: the leader is the agent itself, and the old
            # ordering (descendants first, then it) is exactly right.
            _try(sent, "session_leader", leader, lambda: send_group(leader, sig))
        else:
            # Finding 12.  The watcher is NEVER signalled: it must survive to reap the agent
            # and write the exit sentinel, and it exits on its own once it has.
            sent.append({"rung": "session_leader", "target": leader,
                         "result": "withheld:exit_watcher"})
    else:
        pid = int(record["pid"])
        _try(sent, "root", pid, lambda: send_one(pid, sig))
    return {"sent": tuple(sent), "refusal": decision.get("refusal", ""),
            "scope": decision["scope"]}


def _try(log: list[dict[str, Any]], rung: str, target: int, action: Any) -> None:
    try:
        action()
        log.append({"rung": rung, "target": target, "result": "sent"})
    except ProcessLookupError:
        log.append({"rung": rung, "target": target, "result": "esrch"})
    except PermissionError:
        log.append({"rung": rung, "target": target, "result": "eperm"})
    except OSError as exc:
        log.append({"rung": rung, "target": target, "result": f"errno:{exc.errno}"})


def graceful_signal() -> int:
    return signal.SIGTERM


def force_signal() -> int:
    return signal.SIGKILL


def exit_proven(record: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Whether an OS-confirmed exit is PROVEN.  Three answers, never two.

    ``{"proven": bool, "reason": str}``.  ``proven=False`` with reason
    ``process_table_unreadable`` is UNKNOWN, not alive and not dead -- and the interrupt
    ladder maps it to ``exit_unproven`` -> ``LOST``, never to ``terminated_forced``.
    """
    if not snapshot.get("readable", False):
        return {"proven": False, "reason": "process_table_unreadable"}
    pid = int(record["pid"])
    row = row_for(snapshot, pid)
    if row is not None and row["tty"] == record.get("captured_tty"):
        return {"proven": False, "reason": "incarnation_still_present"}
    if not _pid_exists(pid):
        return {"proven": True, "reason": "esrch_and_incarnation_absent"}
    # ---- round 4, finding 3: the pid EXISTS but is not on the captured tty ----------
    # That used to be accepted as proof of exit ("recycled, so ours is gone").  It is not:
    # the recorded pid is the AGENT, not the session leader, and an agent that detached
    # from the pty (setsid, a daemonising helper, a hangup survivor) is the SAME process
    # incarnation, alive, off the tty.  The only same-incarnation identity the OS offers
    # is the kernel's start time for the pid, so that is what decides: an equal start
    # identity is the same live process; a different one is a recycled pid whose
    # original is therefore gone; no identity on either side is UNKNOWN, which is never
    # "exited".
    expected = record.get("proc_start_ticks")
    observed = proc_start_ticks(pid)
    if not expected or not observed:
        return {"proven": False, "reason": "exit_unproven:pid_exists_off_tty_without_start_identity",
                "expected_start": expected, "observed_start": observed}
    if int(observed) == int(expected):
        return {"proven": False, "reason": "incarnation_detached_but_live",
                "expected_start": expected, "observed_start": observed}
    return {"proven": True, "reason": "pid_recycled_start_identity_mismatch",
            "expected_start": expected, "observed_start": observed}


def drain(master_fd: int, *, budget_ms: int = 500) -> int:
    """Read and DISCARD whatever the child has written, bounded.  Returns the byte count.

    **This is a teardown obligation, not a convenience.**  FACT, measured on this host: a
    session leader that exits while the pty slave holds unflushed output and nobody is
    reading the master wedges in the kernel's "trying to exit" state -- ``ps`` reports
    ``E``, the process leaves the tty, and ``waitpid`` reports it as a live child that has
    not changed state, FOREVER.  Draining 38 bytes made the same child reapable
    immediately.

    Without this, every path that has to PROVE an exit is permanently unable to:
    :func:`standalone_identity.prove_teardown` would raise ``StandaloneTeardownUnproven``
    after a failed start, and the interrupt ladder's rung 4 would report ``exit_unproven``
    -> ``LOST`` for a process that really did die.  Both are fail-closed, and both would be
    fail-closed for the wrong reason -- which is worse than a loud error, because it looks
    like the contract working.

    Callers that need the BYTES read them through :mod:`standalone_capture` instead; this
    one exists for the teardown path, which needs the pipe empty rather than the content.
    """
    if not isinstance(master_fd, int) or master_fd < 0:
        return 0
    import select as _select
    drained = 0
    deadline = time.time() + budget_ms / 1000.0
    while time.time() < deadline:
        try:
            ready, _, _ = _select.select([master_fd], [], [], 0.05)
        except (OSError, ValueError):
            break
        if not ready:
            break
        try:
            chunk = os.read(master_fd, 65_536)
        except OSError:
            break
        if not chunk:
            break
        drained += len(chunk)
    return drained


def _signal_drain_handoff(session: Mapping[str, Any]) -> None:
    """Release a watcher deferring its exit in :func:`_await_drain_handoff` (round-10 item
    1): write ONE byte to the handoff's write end, then close it.  The byte -- not the close
    -- is the reliable wake: a ``select`` on the read end returns on the data whether or not
    the last write end has closed, so a lingering descriptor copy can never wedge the
    release.  Idempotent: the fd is set to ``-1`` once released."""
    handoff = session.get("drain_handoff_fd")
    if isinstance(handoff, int) and handoff >= 0:
        try:
            os.write(handoff, b"1")
        except OSError:
            pass
        try:
            os.close(handoff)
        except OSError:
            pass
        if isinstance(session, dict):
            session["drain_handoff_fd"] = -1


def reap_leader(session: Mapping[str, Any], *, timeout_ms: int = 2_000) -> dict[str, Any]:
    """``waitpid`` the exit watcher this process forked, bounded.  Finding 9.

    The watcher is THIS process's child: nobody else can reap it, and an unreaped watcher
    is a zombie for the life of the supervisor -- one per dispatch.  It exits by itself as
    soon as it has reaped the agent and written the sentinel, so after a proven exit this
    returns almost at once; the bound is for the case where it does not, which is reported
    rather than waited on forever.

    ``{"reaped": bool, "status": int|None, "detail": str}``.  ``ChildProcessError`` --
    already reaped, or not our child (a stranger process asking) -- is ``reaped=True``
    with no status: there is nothing left to collect.

    Round-10 item 1: reaping the leader means the supervisor is DONE with the pty, so this
    RELEASES the drain handoff first -- a supervisor-alive watcher defers its exit until
    that write end closes, and reaping it before releasing would otherwise wait out the
    linger ceiling.  Idempotent (``_reclaim`` also releases it explicitly first).
    """
    pid = session.get("leader_pid")
    if not isinstance(pid, int) or pid <= 0:
        return {"reaped": False, "status": None, "detail": "no leader pid recorded"}
    _signal_drain_handoff(session)
    deadline = time.time() + timeout_ms / 1000.0
    while True:
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return {"reaped": True, "status": None,
                    "detail": "already reaped or not this process's child"}
        except OSError as exc:
            return {"reaped": False, "status": None, "detail": f"waitpid: {exc}"}
        if done == pid:
            return {"reaped": True, "status": _wait_status_to_code(status), "detail": ""}
        if time.time() >= deadline:
            return {"reaped": False, "status": None,
                    "detail": "the exit watcher is still running at the deadline"}
        time.sleep(0.02)


def release(session: Mapping[str, Any]) -> None:
    """DRAIN, then close the master fd.  Does NOT delete the capture file (AC-37-05).

    Draining first for the reason :func:`drain` documents: closing the master without
    emptying it can leave the child unreapable, and a runtime that leaks unreapable children
    is exactly what a process supervisor exists not to be.

    The capture must stay readable after release, so a completion question asked by a
    successor process has something to read.  Deleting it here would make AC-37-05 depend on
    U6, which is UNKNOWN.
    """
    fd = session.get("master_fd")
    if isinstance(fd, int) and fd >= 0:
        drain(fd)
        try:
            os.close(fd)
        except OSError:
            pass
        if isinstance(session, dict):
            # Closed ONCE.  A second release over the same mapping would otherwise close
            # whatever descriptor number the kernel has since handed to somebody else.
            session["master_fd"] = -1
    # The orphan guard's supervisor end (finding 1).  Closing it tells the watcher this
    # supervisor no longer reads the master -- after a proven exit there is nothing left
    # to read, and after a RETAINED (unsettled) dispatch it is exactly what lets the
    # watcher take over draining so the live agent is not wedged on a full pty buffer.
    guard = session.get("orphan_guard_fd")
    if isinstance(guard, int) and guard >= 0:
        try:
            os.close(guard)
        except OSError:
            pass
        if isinstance(session, dict):
            session["orphan_guard_fd"] = -1
    # The drain handoff's supervisor end (round-10 item 1).  Signalling it releases a
    # watcher still deferring its exit -- `_reclaim` signals it explicitly before reaping the
    # leader, and `release` signals it once more (idempotent) so a path that reclaims without
    # a full drain never leaves the watcher blocked.
    _signal_drain_handoff(session)
