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

import contextlib
import ctypes
import errno
import socket
import sys
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
    # darwin (OS-48): `kern.bootsessionuuid` -- a UUID minted once per boot.  `kern.boottime`
    # was used before, but its `usec` field DRIFTS between reads (NTP slewing adjusts the
    # kernel's boottime: MEASURED in run_f820764749d6 -- 229493 vs 168681 usec, hours
    # apart on one boot), so a supervisor and a later successor could read two different
    # "boot ids" for one boot and refuse every ownership check as `identity_changed`.  The
    # boottime SECONDS are the fallback where the UUID sysctl is absent.
    try:
        out = subprocess.run(["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True,
                             text=True, timeout=5, check=False)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=5, check=False)
        if out.returncode == 0 and out.stdout.strip():
            match = re.search(r"sec = (\d+)", out.stdout)
            return f"boottime_sec={match.group(1)}" if match else out.stdout.strip()
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
    #: OS-48 DESIGN §2.3: the SUPERVISOR's end of the control socket over which it asks the
    #: watcher (the agent's parent) to deliver a signal -- delivery bound to the pinned
    #: incarnation by the watcher's single reap-and-deliver thread.  Closed by :func:`release`.
    control_fd: int
    #: OS-48 DESIGN §1.5: the fence nonce minted by the supervisor before the spawn; the
    #: watcher writes `marker_bytes(fence_nonce)` after the reap and the settlement boundary N
    #: is where that marker lands in the capture.
    fence_nonce: str


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
                 drain_handoff_fd: int = -1, control_fd: int = -1,
                 fence_nonce: str = "") -> None:
        super().__init__(errno.ECHILD, detail)
        self.control_fd = control_fd
        self.fence_nonce = fence_nonce
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
                "drain_handoff_fd": self.drain_handoff_fd, "control_fd": self.control_fd,
                "fence_nonce": self.fence_nonce}


def spawn(*, argv: Sequence[str], env: Mapping[str, str], profile: StandaloneProfile,
          session_id: str, incarnation: str, spawn_record_target: str | os.PathLike[str],
          cwd: str | None = None, argv_digest: str = "", env_digest: str = "",
          sentinel: str | os.PathLike[str] | None = None,
          fence: str = "", image: str | None = None,
          capture: str | os.PathLike[str] | None = None,
          fence_nonce: str = "",
          supervisor_identity: Mapping[str, Any] | None = None,
          sidecar_path: str = "") -> PtySession:
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
    host_boot = host_boot_id()
    # OS-48: the fence nonce (DESIGN §1.5) is minted BEFORE the fork and carried by the spawn
    # record, so a successor can search the capture for the marker with nobody alive.
    if not fence_nonce:
        fence_nonce = uuid.uuid4().hex
    if supervisor_identity is None:
        supervisor_identity = identity.process_identity(
            pid=os.getpid(), start_id=proc_start_ticks(os.getpid()), boot_id=host_boot,
            incarnation=fence, source=evidence_source_id())
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
    # OS-48 DESIGN §2.3: the CONTROL socket for watcher-mediated signal delivery.  The
    # supervisor keeps `ctl_sup` (CLOEXEC); the watcher keeps `ctl_w`.
    ctl_sup_sock, ctl_w_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    ctl_sup, ctl_w = ctl_sup_sock.detach(), ctl_w_sock.detach()
    fcntl.fcntl(ctl_sup, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    # OS-48 iteration 7 (F-010): the fork-watch GATE.  The agent child blocks on `gate_r`
    # until the watcher has registered its NOTE_FORK|NOTE_EXIT watch on the child's pid --
    # BEFORE the child can `execve`, and the pre-exec child never forks -- so every fork
    # the root ever performs is observed (coalesced, never counted) and the root has no
    # pre-registration window.  A registration that fails is named `fork_watch_gap`.
    gate_r, gate_w = os.pipe()
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
            os.close(ctl_sup)
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
                    # F-010: wait for the watcher's fork watch (one byte, or EOF if the
                    # watcher is gone -- then there is no watcher to account anything).
                    # Before `chdir`: nothing fallible stands after the spawn record.
                    try:
                        os.close(gate_w)
                        os.read(gate_r, 1)
                        os.close(gate_r)
                    except OSError:
                        pass
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
                        "boot_id": host_boot,
                        "proc_start_ticks": proc_start_ticks(child_pid),
                        "argv_digest": argv_digest, "env_digest": env_digest,
                        "started_at": started_at,
                        # OS-48: the fence nonce, the evidence source and the SUPERVISOR's
                        # pinned identity (the watcher's parent-death witness target).
                        "fence_nonce": fence_nonce,
                        "evidence_source": evidence_source_id(),
                        "supervisor_identity": dict(supervisor_identity),
                    })
                    # `closerange` cannot fail in a way that stops the exec -- it only
                    # closes -- and it stays after the write because the write needs a
                    # descriptor of its own.
                    os.closerange(3, close_up_to + 1)
                    os.execve(image_path, list(argv), dict(env))
                except BaseException:
                    os._exit(127)
            # ---- F-010: register the ROOT's fork/exit watch BEFORE releasing it to exec ----
            members_kq, root_watch = _register_root_watch(agent_pid)
            try:
                os.close(gate_r)
                os.write(gate_w, b"1")
                os.close(gate_w)
            except OSError:
                pass
            os.write(handoff_w, b"%d\n" % agent_pid)
            os.close(handoff_w)
            # ---- what the WATCHER holds (OS-48 DESIGN §1.3) ---------------------------
            # It KEEPS ONE slave descriptor -- the owner-held slave reference: its 0/1/2 go
            # to /dev/null, but the retained slave fd is what it writes the fence / RELEASE
            # markers through and what keeps the kernel from discarding the unread tail;
            # descendants that inherited the slave keep writing (after N: diagnostic tail)
            # until the two-phase release closes this reference.  It ALSO keeps one copy
            # of the master -- deliberately, and that is the round-4
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
                   drain_budget_ms=profile.timeouts.post_exit_drain_budget_ms,
                   control_fd=ctl_w, fence_nonce=fence_nonce,
                   supervisor_identity=supervisor_identity, host_boot_id=host_boot,
                   agent_start_id=proc_start_ticks(agent_pid), sidecar_path=sidecar_path,
                   members_kq=members_kq, root_watch=root_watch)
        except BaseException:
            os._exit(127)
    os.close(slave_fd)
    os.close(handoff_w)
    os.close(gate_r)
    os.close(gate_w)
    os.close(guard_r)
    os.close(dh_r)                    # the supervisor keeps only the write end `dh_w`
    os.close(ctl_w)                   # the supervisor keeps only its own control end
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
            drain_handoff_fd=dh_w, control_fd=ctl_sup, fence_nonce=fence_nonce)
    # F-005: the watcher's start identity is read NOW, while it is positively alive as our
    # child (the handoff just proved it), and pinned on the session -- the fence's
    # `reaped_by` is this pinned identity, never a later read of a possibly-dead pid.
    return {"master_fd": master_fd, "slave_name": slave_name, "pid": agent_pid,
            "pgid": agent_pid, "sid": leader_pid, "leader_pid": leader_pid,
            "pty_id": pty_id, "argv": argv_tuple, "orphan_guard_fd": guard_w,
            "drain_handoff_fd": dh_w, "control_fd": ctl_sup, "fence_nonce": fence_nonce,
            "watcher_start_id": proc_start_ticks(leader_pid), "boot_id": host_boot}


def _register_root_watch(agent_pid: int) -> "tuple[Any, str]":
    """[FORKED-SAFE] Establish the platform's ownership mechanism on the not-yet-exec'd root
    and return its RECEIPT ``(kqueue | None, status)``: darwin -- the membership kqueue with
    NOTE_FORK|NOTE_EXIT registered on the root (``"registered"``, positive: every later fork
    of the root is observed; ``"unavailable:*"`` / ``"<Error>:<errno>"`` -- the named gap);
    Linux -- ``PR_SET_CHILD_SUBREAPER`` installed AND read back (``"subreaper"``, positive;
    ``"ownership_setup_unverified:*"`` -- the named failure, F-014)."""
    if sys.platform != "darwin":
        # F-014: Linux's positive ownership mechanism is the SUBREAPER; it is installed and
        # verified HERE, before the gate releases the root, so no fork of the root can
        # precede it.  The receipt (or the named failure) travels with the membership.
        return None, _set_subreaper()
    try:
        kq = select.kqueue()
    except OSError as exc:
        return None, f"unavailable:{type(exc).__name__}:{getattr(exc, 'errno', '')}"
    try:
        kq.control([select.kevent(agent_pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD,
                                  fflags=select.KQ_NOTE_FORK | select.KQ_NOTE_EXIT)], 0, 0)
    except OSError as exc:
        return kq, f"{type(exc).__name__}:{getattr(exc, 'errno', '')}"
    return kq, "registered"


def _watch(agent_pid: int, *, master_fd: int, slave_fd: int, guard_r: int,
           close_up_to: int, sentinel: str | os.PathLike[str] | None, fence: str,
           capture: bytes | None,
           capture_limits: CaptureLimits | None = None,
           slave_name: str = "", drain_budget_ms: int = 2_000, dh_r: int = -1,
           control_fd: int = -1, fence_nonce: str = "",
           supervisor_identity: Mapping[str, Any] | None = None,
           host_boot_id: str = "", agent_start_id: int = 0, sidecar_path: str = "",
           members_kq: Any = None, root_watch: str = ""
           ) -> None:  # pragma: no cover - runs in the forked watcher
    """The exit watcher's whole life (OS-48 DESIGN §1.6 / §2.3 / §2.5).  Raw ``os`` calls only.

    **It KEEPS one slave descriptor** (the owner-held slave reference, DESIGN §1.3): while it
    is open the kernel never reclaims the unread tail (probe_d1: 3 s / 10 s late reads intact
    on darwin and Linux, ctty or not) and no EOF can be manufactured by anyone else.  It is
    closed only on release-2 (the supervisor confirmed it consumed the RELEASE marker) or at the
    end of the orphan finalize.

    **It is the agent's PARENT and the SAME single thread both reaps and delivers signals**
    (DESIGN §2.3, F-002): a ``SIG`` request served before its own ``waitpid`` reaps the child
    addresses a pid the kernel cannot reuse (a zombie holds its pid until reaped -- probe_d7);
    after the reap a request is refused ``signal_target_reaped``.  It never sends ``killpg``.

    **After the reap it writes the FENCE MARKER into its slave fd, then the exit sentinel**
    (DESIGN §1.6).  The marker's offset in the capture is the settlement boundary N.  Then it
    DEFERS (supervisor alive): serves ``R`` (release-1: write the RELEASE marker) and ``C``
    (release-2: close the slave) on the drain handoff, or -- on guard EOF -- runs the ORPHAN
    finalize: drain to the marker, fence-first, claim a generation only with a durable
    relinquishment record or its incarnation-bound parent-death witness (never guard EOF
    alone -- probe_d11), publish the fence, RELEASE marker, drain to R, release record, close.
    """
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            if fd != slave_fd:
                os.dup2(null, fd)
        if null > 2 and null != slave_fd:
            os.close(null)
    except OSError:
        pass
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)
    signal.signal(signal.SIGCHLD, lambda *_args: None)
    # ---- the incarnation-bound PARENT-DEATH WITNESS (DESIGN §2.5 rule 2, probe_d11) ----------
    witness = _ParentWitness(supervisor_identity or {})
    # ---- the POSITIVE membership set (DESIGN §2.2, REVIEW_IMPLEMENTATION F-006) ---------------
    members = _Membership(members_path(capture, fence.partition(":")[2]) if capture is not None else None,
                          fence=fence, boot_id=host_boot_id, agent_pid=agent_pid,
                          agent_start_id=agent_start_id, kq=members_kq, root_watch=root_watch)
    keep = {master_fd, guard_r, wake_r, wake_w, slave_fd}
    if dh_r >= 0:
        keep.add(dh_r)
    if control_fd >= 0:
        keep.add(control_fd)
    keep.update(witness.fds())
    keep.update(members.fds())
    for fd in range(3, close_up_to + 1):
        if fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass
    orphaned = False
    appender: capture_mod.RawBoundedAppender | None = None
    status = 0
    ctl = _ControlServer(control_fd, agent_pid=agent_pid, fence=fence)
    # A child forked BEFORE the NOTE_FORK registration is found by the first walk; a later
    # periodic walk (bounded, ~1 s) covers an event the kqueue coalesced or dropped.
    members.discover("watch_start")
    next_walk = time.monotonic() + 1.0
    while True:
        try:
            done, status = os.waitpid(agent_pid, os.WNOHANG)
        except ChildProcessError:
            done, status = agent_pid, 0
        if done == agent_pid:
            break
        if sys.platform == "linux":
            _reap_reparented(agent_pid, members)
        if time.monotonic() >= next_walk:
            members.discover("periodic")
            next_walk = time.monotonic() + 1.0
        if not orphaned:
            fds = [guard_r, wake_r] + ([control_fd] if control_fd >= 0 else []) + sorted(members.fds())
            try:
                ready, _, _ = select.select(fds, [], [], 0.05)
            except (OSError, ValueError):
                ready = [guard_r]
            if control_fd >= 0 and control_fd in ready:
                ctl.serve(reaped=False)
                continue
            if any(fd in ready for fd in members.fds()):
                members.serve()                       # NOTE_FORK -> bounded discovery
                continue
            if wake_r in ready:
                _drain_wakeups(wake_r)
                if sys.platform == "linux":
                    members.discover("sigchld")       # a reparented / forked descendant
                continue
            if guard_r in ready:
                orphaned = True
                appender = _orphan_take_over(guard_r, capture, capture_limits)
            continue
        if control_fd >= 0:
            try:
                ready, _, _ = select.select([control_fd], [], [], 0)
            except (OSError, ValueError):
                ready = []
            if ready:
                ctl.serve(reaped=False)
        members.serve()
        _drain_once(master_fd, appender, budget=0.05, wake_r=wake_r)
    code = _wait_status_to_code(status)
    # ---- F-001 (iterations 3-4): the DECLARED sidecar is read + digested + snapshotted HERE,
    # in the same step that reaped the root and before the marker is emitted -- the PRESENCE
    # fact for R3 (instant `reap_step_before_marker`).  This does NOT bind the file's content
    # to N (a helper may still write before the marker lands), which is why the content is
    # never a settlement body source.
    if capture is not None and sidecar_path:
        try:
            capture_mod.snapshot_sidecar(capture, fence.partition(":")[2], fence=fence,
                                         sidecar_path=sidecar_path, captured_at=_now_iso())
        except Exception:  # noqa: BLE001 - no record => readers say `sidecar_unproven`
            pass
    # the root is reaped: one last bounded discovery (children forked at its very end), and
    # its own exit is a positive ledger fact
    members.serve()
    members.discover("agent_reaped")
    members.note_reaped(agent_pid)
    # ---- the FENCE MARKER, written by the OWNER into ITS slave fd AFTER the reap -------------
    marker_written = _write_marker_bounded(slave_fd, capture_mod.marker_bytes(fence_nonce)
                                           if fence_nonce else b"", master_fd, appender,
                                           slave_name=slave_name)
    if sentinel is not None:
        write_exit_sentinel(sentinel, code=code, fence=fence)
    if not orphaned:
        try:
            ready, _, _ = select.select([guard_r], [], [], 0)
        except (OSError, ValueError):
            ready = []
        if ready:
            orphaned = True
            appender = _orphan_take_over(guard_r, capture, capture_limits)
    release_written = False
    if not orphaned and dh_r >= 0:
        next_release_walk = [time.monotonic() + 1.0]

        def _tick() -> None:
            members.serve()                           # darwin NOTE_EXIT / NOTE_FORK
            if sys.platform == "linux":
                _reap_reparented(agent_pid, members)  # reparented descendants: reaped, recorded
            if time.monotonic() >= next_release_walk[0]:
                # F-010 adversarial pass: a descendant reparented (Linux: to this subreaper)
                # or forked while the watcher waits for the release is still discoverable
                members.discover("release_wait")
                next_release_walk[0] = time.monotonic() + 1.0
        orphaned, release_written = _defer_for_release(
            dh_r, guard_r, control_fd, ctl, slave_fd, fence_nonce, master_fd,
            budget_s=max(0, int(drain_budget_ms)) / 1000.0, on_tick=_tick,
            slave_name=slave_name)
        if orphaned:
            appender = _orphan_take_over(guard_r, capture, capture_limits)
    if orphaned and capture is not None:
        try:
            _orphan_finalize(master_fd, slave_fd, appender, capture=capture, fence=fence,
                             fence_nonce=fence_nonce, code=code, marker_written=marker_written,
                             sentinel=sentinel, witness=witness,
                             budget_s=max(0, int(drain_budget_ms)) / 1000.0,
                             host_boot_id=host_boot_id, agent_pid=agent_pid,
                             agent_start_id=agent_start_id, release_written=release_written,
                             sidecar_path=sidecar_path, slave_name=slave_name)
        except Exception:  # noqa: BLE001 - a watcher never dies of bookkeeping
            pass
    members.close()                                    # final ledger accounting
    try:
        os.close(slave_fd)
    except OSError:
        pass
    os._exit(code)


def _orphan_take_over(guard_r: int, capture: bytes | None,
                      capture_limits: "CaptureLimits | None"
                      ) -> "capture_mod.RawBoundedAppender | None":  # pragma: no cover
    """Close the (now-EOF) orphan guard and build the bounded appender the watcher drains
    into once the supervisor is gone.  A watcher never dies of bookkeeping, so an appender
    that cannot be built is ``None`` (the orphan finalize then publishes nothing for bytes
    nothing vouches for)."""
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



# ---- OS-48 DESIGN §2.2: the POSITIVE membership set `members.<inc>.jsonl` ----------------------
MEMBER_SCHEMA = "os48.member.v1"
MEMBER_ROLE_AGENT = "agent"
MEMBER_ROLE_DESCENDANT = "descendant"
MEMBER_EVENT_OBSERVED = "observed"
MEMBER_EVENT_EXITED = "exited"
#: REVIEW_IMPLEMENTATION_iteration4 F-009: a discovery pass whose evidence could NOT be read
#: (the process listing failed / kept changing, or a live candidate's identity was
#: unreadable by every source) is a durable ledger fact of its own -- the set is UNKNOWN
#: beyond what was positively observed, and a reader must say so.
MEMBER_EVENT_DISCOVERY_UNREADABLE = "discovery_unreadable"
DISCOVERY_LISTING_UNREADABLE = "listing_unreadable"
DISCOVERY_LISTING_UNSTABLE = "listing_unstable"
DISCOVERY_CANDIDATE_UNREADABLE = "candidate_identity_unreadable"
#: REVIEW_IMPLEMENTATION_iteration6 (F-010 / F-012 / F-013, coordinator direction): the
#: descendant accounting is modelled CONSERVATIVELY -- the residual is UNKNOWN by default and
#: only an enumerated set of POSITIVE facts clears a piece of it.  Every discovery kind below
#: is a durable `discovery_unreadable` ledger record; none is ever discharged by a later
#: observation (a kqueue NOTE_FORK is COALESCED: it proves "at least one fork", never the set
#: of children, and no kernel interface enumerates the children of a fork event).
#:  * `fork_coalesced`          -- a NOTE_FORK observed on a member: its children beyond the
#:                                 ones positively attributed are UNKNOWN (never cleared);
#:  * `fork_watch_gap`          -- a window in which a member's forks were unobservable: the
#:                                 root when its watch could not be registered before exec
#:                                 (the gate in `spawn` normally closes that window), and
#:                                 EVERY descendant (its watch is registered after its birth);
#:  * `parent_identity_unreadable` -- a candidate names a member pid as its parent but the
#:                                 member's CURRENT start identity cannot be read by any
#:                                 source while the kernel still holds the pid: the
#:                                 attribution can be neither made nor refused (F-012: a held
#:                                 zombie pid is NOT a witness of the cached incarnation);
#:  * `candidate_identity_unreadable` -- a live candidate no source will read, or a candidate
#:                                 positively parented to a member / the subreaper whose start
#:                                 identity is unreadable (zero) -- named, never omitted (F-013);
#:  * `fork_watch_unregistered` -- a member's fork/exit watch could not be registered;
#:  * `fork_watch_unavailable`  -- no kqueue at all (darwin);
#:  * `fork_events_unreadable`  -- the kqueue read failed: pending events (forks) were lost;
#:  * `member_ceiling_exceeded` -- a positively attributed descendant the ledger will not hold;
#:  * `watch_ended`             -- members still alive when the watcher stopped observing:
#:                                 their later forks are unobservable;
#:  * `discovery_pass_ceiling`  -- a walk was still attributing when its bounded passes ran
#:                                 out: deeper generations may exist (worker's adversarial pass);
#:  * `reaped_unattributed`     -- Linux: the subreaper reaped a descendant no walk had
#:                                 attributed -- it existed, forked unobserved, and is gone.
DISCOVERY_FORK_COALESCED = "fork_coalesced"
DISCOVERY_OWNERSHIP_UNVERIFIED = "ownership_setup_unverified"
DISCOVERY_PIDFD_UNAVAILABLE = "pidfd_unavailable"          # F-016: no fixed object for a Linux member
#: REVIEW_IMPLEMENTATION_iteration8 F-016: a Linux candidate whose identity / parentage, re-read
#: AFTER its fixed object (pidfd) was acquired, did not prove the SAME incarnation the
#: pre-acquisition reads described (the pidfd died, the start tick or ppid differ, or the
#: parent member's own fixed object no longer binds it) -- named, NOT a positive member.
DISCOVERY_IDENTITY_UNVERIFIED = "candidate_identity_unverified"
DISCOVERY_PASS_CEILING = "discovery_pass_ceiling"
DISCOVERY_REAPED_UNATTRIBUTED = "reaped_unattributed"
DISCOVERY_FORK_WATCH_GAP = "fork_watch_gap"
DISCOVERY_PARENT_UNREADABLE = "parent_identity_unreadable"
DISCOVERY_WATCH_UNREGISTERED = "fork_watch_unregistered"
DISCOVERY_WATCH_UNAVAILABLE = "fork_watch_unavailable"
DISCOVERY_EVENTS_UNREADABLE = "fork_events_unreadable"
DISCOVERY_CEILING_EXCEEDED = "member_ceiling_exceeded"
DISCOVERY_WATCH_ENDED = "watch_ended"
#: state-file counter per kind
DISCOVERY_COUNTERS = {DISCOVERY_LISTING_UNREADABLE: "listing_unreadable",
                      DISCOVERY_LISTING_UNSTABLE: "listing_unstable",
                      DISCOVERY_CANDIDATE_UNREADABLE: "candidates_unreadable",
                      DISCOVERY_FORK_COALESCED: "forks_coalesced",
                      DISCOVERY_FORK_WATCH_GAP: "watch_gaps",
                      DISCOVERY_PARENT_UNREADABLE: "parents_unreadable",
                      DISCOVERY_WATCH_UNREGISTERED: "unobservable",
                      DISCOVERY_WATCH_UNAVAILABLE: "unobservable",
                      DISCOVERY_EVENTS_UNREADABLE: "unobservable",
                      DISCOVERY_CEILING_EXCEEDED: "unobservable",
                      DISCOVERY_WATCH_ENDED: "unobservable",
                      DISCOVERY_PASS_CEILING: "unobservable",
                      DISCOVERY_OWNERSHIP_UNVERIFIED: "unobservable",
                      DISCOVERY_PIDFD_UNAVAILABLE: "unobservable",
                      DISCOVERY_IDENTITY_UNVERIFIED: "candidates_unverified",
                      DISCOVERY_REAPED_UNATTRIBUTED: "unobservable"}
_DISCOVERY_REASON_CEILING = 16
_DISCOVERY_PID_CEILING = 64
#: Bounded discovery: passes per trigger and the ceiling on members (a fork bomb stays named).
_MEMBER_DISCOVERY_PASSES = 3
_MEMBER_CEILING = 512


def members_path(capture: str | os.PathLike[str] | bytes, incarnation: str) -> bytes:
    """``<capture dir>/members.<inc>.jsonl`` -- append-only, one `MemberRecord` per line."""
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    return os.path.join(os.path.dirname(target) or b".", b"members." + incarnation.encode() + b".jsonl")


def read_members(path: str | os.PathLike[str] | bytes) -> list[dict[str, Any]]:
    """Every complete (newline-terminated, well-formed) member line; see :func:`read_ledger`
    for the readability state a decision must consult."""
    return read_ledger(path)["records"]


def read_ledger(path: str | os.PathLike[str] | bytes) -> dict[str, Any]:
    """REVIEW_IMPLEMENTATION_iteration2 F-006: the ledger with its READABILITY named --
    ``{"records", "state": final|absent|unreadable, "torn": n, "error"}``.  A read the kernel
    refuses (EACCES, EIO, ...) is `unreadable`, never an empty set; a torn final fragment is
    counted (it adds no member but it is evidence the ledger was still being written)."""
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    try:
        with open(target, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return {"records": [], "state": "absent", "torn": 0, "error": ""}
    except OSError as exc:
        return {"records": [], "state": capture_mod.EVIDENCE_UNREADABLE, "torn": 0,
                "error": f"{type(exc).__name__}:{getattr(exc, 'errno', '')}"}
    out: list[dict[str, Any]] = []
    torn = 0
    lines = raw.split(b"\n")
    if lines and lines[-1]:
        torn = 1                                       # an unterminated final fragment
    for line in lines[:-1]:
        try:
            record = json.loads(line.decode("utf-8"))
        except ValueError:
            torn += 1
            continue
        if isinstance(record, dict) and record.get("schema") == MEMBER_SCHEMA:
            out.append(record)
        else:
            torn += 1
    return {"records": out, "state": capture_mod.EVIDENCE_FINAL, "torn": torn, "error": ""}


def _append_member(path: bytes, record: Mapping[str, Any]) -> bool:
    """[FORKED-SAFE] Append ONE line (write + fsync); never rewrites.  ``False`` = not appended."""
    payload = json.dumps(dict(record), sort_keys=True).encode() + b"\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError:
        return False
    try:
        os.write(fd, payload)
        os.fsync(fd)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _darwin_bsdinfo(pid: int) -> "tuple[int, int] | None":
    """[FORKED-SAFE] ``(ppid, start_ticks)`` from an EXACT-size ``PROC_PIDTBSDINFO`` read
    (``pbi_ppid`` at byte 16; start time at 120/128); ``None`` when unreadable / partial."""
    lib = _libproc_handle()
    if lib is None or not hasattr(lib, "proc_pidinfo"):
        return None
    try:
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                     ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(_PROC_PIDTBSDINFO_SIZE)
        written = lib.proc_pidinfo(int(pid), _PROC_PIDTBSDINFO, 0, buffer, _PROC_PIDTBSDINFO_SIZE)
    except (OSError, ValueError, AttributeError):
        return None
    if written != _PROC_PIDTBSDINFO_SIZE:
        return None
    ppid = struct.unpack_from("<I", buffer.raw, 16)[0]
    seconds, micros = struct.unpack_from("<QQ", buffer.raw, 120)
    return int(ppid), int(seconds) * 1_000_000 + int(micros)


def _darwin_kinfo(pid: int) -> "tuple[int, int] | None":
    """[FORKED-SAFE] ``(ppid, start_id)`` from the kernel's OTHER per-process read, ``sysctl
    kern.proc.pid`` (a single ``kinfo_proc``: ``p_starttime`` tv_sec at byte 0 / tv_usec at
    byte 8, ``e_ppid`` at byte 560 -- verified against the calling process and ``PROC_PIDTBSDINFO``
    in this run's evidence).  A pid that is gone answers ZERO bytes (``None`` here, like
    ``_darwin_bsdinfo``); anything but one whole struct is unreadable (``None``).  This is the
    independent source consulted when ``PROC_PIDTBSDINFO`` refuses a candidate
    (REVIEW_IMPLEMENTATION_iteration4 F-009) -- the identity it yields is the SAME
    ``start_id`` the ledger keys members by."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 4)(1, 14, 1, int(pid))              # CTL_KERN, KERN_PROC, KERN_PROC_PID
        buf = ctypes.create_string_buffer(_KINFO_PROC_SIZE)
        got = ctypes.c_size_t(_KINFO_PROC_SIZE)
        if libc.sysctl(mib, 4, buf, ctypes.byref(got), None, 0) != 0:
            return None
        if got.value != _KINFO_PROC_SIZE:
            return None
        raw = buf.raw
        if struct.unpack_from("<i", raw, _KINFO_PROC_PID_OFF)[0] != int(pid):
            return None
        seconds = struct.unpack_from("<q", raw, _KINFO_PROC_START_SEC_OFF)[0]
        micros = struct.unpack_from("<i", raw, _KINFO_PROC_START_USEC_OFF)[0]
        ppid = struct.unpack_from("<i", raw, _KINFO_PROC_PPID_OFF)[0]
        return int(ppid), int(seconds) * 1_000_000 + int(micros)
    except (OSError, ValueError, AttributeError):
        return None


def _process_info_fallback(pid: int) -> "tuple[int, int] | None":
    """[FORKED-SAFE] The independent per-process identity read consulted only when
    :func:`_process_info` answered ``None`` for a candidate (F-009): darwin ``kern.proc.pid``;
    Linux has no second source (``None``)."""
    return _darwin_kinfo(pid) if sys.platform == "darwin" else None


def _linux_stat(pid: int) -> "tuple[int, int] | None":
    """[FORKED-SAFE] ``(ppid, start_ticks)`` from ``/proc/<pid>/stat``; ``None`` when gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            text = handle.read().decode("utf-8", "replace")
        fields = text.rsplit(") ", 1)[1].split()
        return int(fields[1]), int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def _process_info(pid: int) -> "tuple[int, int] | None":
    """[FORKED-SAFE] ``(ppid, start_ticks)`` from the platform evidence source, or ``None``."""
    return _darwin_bsdinfo(pid) if sys.platform == "darwin" else _linux_stat(pid)


def _pidfd_inode(fd: int) -> int:
    """[FORKED-SAFE] The inode of a pidfd, ``0`` when unreadable.  An inode NUMBER alone proves
    nothing about lifetimes: whether it is a non-recyclable identity of the process incarnation
    depends on the kernel's pidfd inode model (:func:`pidfs_lifetime_model`), and on a kernel
    without pidfs every pidfd shares one anonymous inode (:func:`_self_pidfd_inode` tells)."""
    try:
        return int(os.fstat(fd).st_ino)
    except OSError:
        return 0


#: REVIEW_IMPLEMENTATION (run_5fcd2beac376) F-016 -- the ONE pidfd inode model this runtime
#: accepts as a non-recyclable lifetime binding, and why.  Inspected primary sources:
#: linux v6.12 `fs/pidfs.c` + `kernel/pid.c` and v6.16 `fs/pidfs.c`.  On a 64-BIT kernel a
#: pidfs inode number is `struct pid.ino`, assigned from a monotonic 64-bit counter at pid
#: allocation and never reassigned or reused for the life of the boot (v6.12: `pidfs_ino`
#: under `pidmap_lock`; v6.16: `pidfs_add_pid`, `pidfs_ino(ino) == ino`).  The 32-BIT branches
#: are NOT such a binding: v6.12 allocates the number with `ida_alloc_range` and frees it on
#: inode eviction (a later different birth may receive it); v6.16 exposes only the lower 32
#: bits plus a generation the reader cannot see through `st_ino`.  Kernels before 6.9 have no
#: pidfs at all (one shared anonymous inode).  So the positive recovery binding is enabled
#: ONLY when the running kernel is positively a 64-bit Linux at or after 6.9; every other
#: model is UNPROVEN and a reader reports the lifetime as `unknown` by name.
PIDFS_MODEL_STRUCT_PID_64 = "pidfs_struct_pid_64"
_SIXTY_FOUR_BIT_MACHINES = frozenset({"x86_64", "amd64", "aarch64", "arm64", "ppc64", "ppc64le",
                                      "s390x", "riscv64", "loongarch64", "mips64", "sparc64"})


def _kernel_release_tuple(release: str) -> "tuple[int, int]":
    """``(major, minor)`` of a `uname -r` string, ``(0, 0)`` when unparsable."""
    try:
        head = release.split("-", 1)[0].split("+", 1)[0]
        major, minor = head.split(".")[:2]
        return int(major), int(minor)
    except (ValueError, AttributeError):
        return 0, 0


def pidfs_lifetime_model() -> str:
    """[FORKED-SAFE] The pidfd inode lifetime model of the RUNNING kernel that this runtime
    can positively vouch for: :data:`PIDFS_MODEL_STRUCT_PID_64` when this is a 64-bit
    interpreter (which only a 64-bit kernel can run) AND `os.uname()` agrees (a 64-bit
    machine, Linux at or after 6.9 -- the inspected sources' monotonic struct-pid axis);
    ``""`` -- UNPROVEN -- for everything else (32-bit kernels, a 32-bit interpreter or
    personality, kernels before pidfs, an unparsable release, any other platform).  A
    watcher records a member's inode as `fixed_object_id` only under a proven model, and a
    reader accepts an inode equality as "the same incarnation" only under the same proven
    model; unproven means `unknown`, never alive."""
    if sys.platform != "linux":
        return ""
    if sys.maxsize <= 2 ** 32:
        # a 32-bit interpreter: the kernel may be 32-bit (a 32-bit userland can run on either),
        # so the width is not positively established -- unproven
        return ""
    try:
        uname = os.uname()
    except OSError:
        return ""
    # a 64-bit interpreter can only run on a 64-bit kernel; `uname -m` must agree (a 32-bit
    # personality reports a 32-bit machine and is refused)
    if uname.sysname != "Linux" or uname.machine not in _SIXTY_FOUR_BIT_MACHINES:
        return ""
    if _kernel_release_tuple(uname.release) < (6, 9):
        return ""
    return PIDFS_MODEL_STRUCT_PID_64


def _self_pidfd_inode() -> int:
    """[FORKED-SAFE] The calling process's own pidfd inode: the reference that tells a unique
    (pidfs) inode from the shared anonymous one.  ``0`` when no pidfd can be opened."""
    if not hasattr(os, "pidfd_open"):
        return 0
    try:
        fd = os.pidfd_open(os.getpid())
    except OSError:
        return 0
    try:
        return _pidfd_inode(fd)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _pidfd_alive(fd: int) -> str:
    """``alive`` (the pidfd's process has not exited -- so the pid is still THAT process),
    ``exited`` (readable: it ended), ``unavailable`` (the fd cannot be polled)."""
    try:
        ready, _, _ = select.select([fd], [], [], 0)
    except (OSError, ValueError):
        return "unavailable"
    return "exited" if ready else "alive"


def pidfd_binding(pid: int) -> dict[str, Any]:
    """REVIEW_IMPLEMENTATION_iteration8 F-016 -- the INDEPENDENT lifetime binding a reader with
    no held pidfd can still obtain for the process holding ``pid`` NOW (a supervisor or a
    recovery reader after the watcher died): ``{"state", "fixed_object_id"}``.

    ``final`` + the pidfs inode of a fresh pidfd ONLY under a proven non-recyclable inode
    model (:func:`pidfs_lifetime_model`, reported as ``model``) -- compared to the recorded
    member's ``fixed_object_id``: equal -> the SAME incarnation; different -> that incarnation
    is positively gone, the pid is another process's; ``absent`` when no process holds the
    pid; ``unavailable`` when the kernel offers no binding this runtime can vouch for (no
    `pidfd_open`, shared anonymous pidfd inodes, a 32-bit or otherwise unproven inode model
    -- ``model`` says which) -- and then (pid, start tick) equality alone must NOT be read as
    the recorded lifetime: ticks are not injective."""
    model = pidfs_lifetime_model()
    if sys.platform != "linux" or not hasattr(os, "pidfd_open") or int(pid) <= 0:
        return {"state": "unavailable", "fixed_object_id": 0, "model": model}
    try:
        fd = os.pidfd_open(int(pid))
    except ProcessLookupError:
        return {"state": "absent", "fixed_object_id": 0, "model": model}
    except OSError:
        return {"state": "unavailable", "fixed_object_id": 0, "model": model}
    try:
        state = _pidfd_alive(fd)
        if state == "exited":
            # exited but not yet reaped: no live incarnation holds the pid
            return {"state": "absent", "fixed_object_id": 0, "model": model}
        if state != "alive" or not model:
            return {"state": "unavailable", "fixed_object_id": 0, "model": model}
        ino, ref = _pidfd_inode(fd), _self_pidfd_inode()
        if not ino or not ref or ino == ref:
            return {"state": "unavailable", "fixed_object_id": 0, "model": model}
        return {"state": capture_mod.EVIDENCE_FINAL, "fixed_object_id": ino, "model": model}
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _member_identity(pid: int, start_id: int, boot_id: str, fence: str) -> dict[str, Any]:
    return identity.process_identity(pid=int(pid), start_id=int(start_id), boot_id=boot_id,
                                     incarnation=fence, source=evidence_source_id())


class _Membership:
    """[FORKED-SAFE] The watcher's positive membership set (DESIGN §2.2): the agent from the
    spawn record; descendants discovered -- darwin: kqueue ``NOTE_FORK`` on every member
    *triggers* a bounded ``ppid == member`` walk of ``proc_listallpids`` + ``PROC_PIDTBSDINFO``
    with the child's start identity captured at first sight; Linux: a ``/proc`` ppid walk on
    ``SIGCHLD`` (the watcher is a subreaper, so orphaned descendants reparent to it and are
    reaped by ``waitpid``).  Discovery may MISS (a child that forked and exited between the
    event and the read) -- which only ever makes the set smaller; absence from the set means
    ``unknown``, never "not ours".  Membership never widens the signal authority: members are
    for ownership / teardown accounting only (the residual `descendants_unreaped`).

    REVIEW_IMPLEMENTATION_iteration2 F-006: members are keyed by INCARNATION ``(pid, start_id)``,
    never by pid alone; a child is attributed to a parent only when the parent member is a
    LIVE, CURRENT incarnation (its start identity re-read NOW equals the recorded one and it
    has not exited) -- an exited, stale or reused parent attributes nothing; every ledger
    append is accounted (`appended` / `failed`) in ``members.<inc>.state.json`` so a reader can
    tell a short ledger from a complete one.

    REVIEW_IMPLEMENTATION_iteration4 F-009: discovery that could NOT be read is accounted
    SEPARATELY from successful appends -- a failed / changing process listing, or a live
    candidate whose identity no source can read, is (a) a `discovery_unreadable` ledger
    record with its reason and (b) a `discovery` block in the state file (`passes`,
    `listing_unreadable`, `listing_unstable`, `candidates_unreadable`, `reasons`, `pids`).
    A reader (`membership_residual`) turns any of those into the named outcome
    `descendants_unknown`: the positive set stays what it is, and the rest is UNKNOWN --
    never "no descendants".  A candidate refused by ``PROC_PIDTBSDINFO`` is re-read from the
    independent ``kern.proc.pid`` source first; only a candidate unreadable by every source
    while the kernel still holds its pid (not a zombie, not gone) is unknown.

    REVIEW_IMPLEMENTATION_iteration6 (the conservative model).  POSITIVE facts, and only
    these, contribute to the answer:
      P1 the spawn record -- the root's pid + start identity (the watcher's own child);
      P2 the root's fork/exit watch registered BEFORE its exec (`spawn`'s gate);
      P3 a candidate whose ppid is a member whose CURRENT start identity re-read now
         equals the recorded one (alive; a held zombie / unreadable pid is NOT a witness);
      P4 Linux: a candidate whose ppid is the watcher itself (the subreaper's adopted
         child) -- a positive member candidate regardless of its own start readability;
      P5 a member's exit observed by the watcher (NOTE_EXIT / its own waitpid / waitid).
    Everything else is a named UNKNOWN that no later observation discharges."""

    def __init__(self, path: bytes | None, *, fence: str, boot_id: str,
                 agent_pid: int, agent_start_id: int, kq: Any = None, root_watch: str = "") -> None:
        self.path = path
        self.fence = fence
        self.boot_id = boot_id
        self.members: dict[tuple[int, int], dict[str, Any]] = {}
        self.appended = 0
        self.failed = 0
        self.discovery: dict[str, Any] = {"passes": 0, "listing_unreadable": 0, "listing_unstable": 0,
                                          "candidates_unreadable": 0, "forks_coalesced": 0,
                                          "watch_gaps": 0, "parents_unreadable": 0, "unobservable": 0,
                                          "candidates_unverified": 0,
                                          "root_watch": root_watch or "unregistered",
                                          "reasons": [], "pids": []}
        self._unreadable_seen: set[int] = set()
        self._once: set[str] = set()
        self._forked: dict[int, int] = {}
        #: F-016: the FIXED object per Linux member -- a pidfd held from attribution until
        #: the member is observed exiting; identity of a cached member is asked of the pidfd,
        #: never of (pid, tick) equality.  Keyed like `members`.
        self._pidfds: dict[tuple[int, int, int], int] = {}
        #: i8 F-016: this watcher's own pidfd inode -- the reference that tells a distinct
        #: member inode from the shared anonymous inode of a kernel without pidfs (0: none) --
        #: and the kernel's inode lifetime MODEL: a member's inode is recorded as its
        #: `fixed_object_id` (a reader's binding) ONLY under the proven non-recyclable model
        #: (`pidfs_lifetime_model`, run_5fcd2beac376 F-016); distinct values alone prove
        #: nothing about reuse after this watcher's handle is gone
        self._pidfs_ref = _self_pidfd_inode() if sys.platform == "linux" else 0
        self._pidfs_model = pidfs_lifetime_model()
        self._root_watch = root_watch
        self._kq: Any = kq
        if sys.platform == "darwin" and self._kq is None:
            try:
                self._kq = select.kqueue()
            except OSError:
                self._kq = None
        # P1 + P2: the root's watch was registered by `spawn` before the exec (positive) --
        # or it was not, and that is the root's named gap
        if not self._add(agent_pid, agent_start_id, MEMBER_ROLE_AGENT, "spawn_record"):
            # the root's own start identity is unreadable: it is OURS (P1's pid) and
            # unrecordable by incarnation -- named, never a silent empty set
            self._discovery_unreadable(DISCOVERY_CANDIDATE_UNREADABLE, "root_start_unreadable",
                                       "watch_start", [int(agent_pid)])
        if sys.platform == "darwin":
            if self._kq is None:
                self._discovery_unreadable(DISCOVERY_WATCH_UNAVAILABLE, "kqueue_unavailable", "watch_start")
            elif root_watch != "registered":
                self._discovery_unreadable(DISCOVERY_FORK_WATCH_GAP, f"root:{root_watch or 'unregistered'}",
                                           "watch_start", [int(agent_pid)])
        elif root_watch != "subreaper":
            # F-014: the subreaper was not positively established before the root could
            # fork: every reparenting the walks rely on (P4) is unproven for this dispatch
            self._discovery_unreadable(DISCOVERY_OWNERSHIP_UNVERIFIED,
                                       root_watch or "ownership_setup_unverified:unset",
                                       "watch_start", [int(agent_pid)])

    def fds(self) -> set[int]:
        out = {self._kq.fileno()} if self._kq is not None else set()
        out.update(self._pidfds.values())
        return out

    def _lifetimes(self, pid: int, start_id: int) -> "list[tuple[tuple[int, int, int], dict[str, Any]]]":
        return [(k, r) for k, r in self.members.items() if k[0] == int(pid) and k[1] == int(start_id)]

    def _pidfd_state(self, key: "tuple[int, int, int]") -> str:
        """The fixed object's answer for a Linux member: ``alive`` (the pidfd's process has not
        exited -- so the pid is still THAT process), ``exited`` (readable: the process ended),
        ``unavailable`` (no pidfd was obtained)."""
        fd = self._pidfds.get(key)
        if fd is None:
            return "unavailable"
        return _pidfd_alive(fd)

    def _pgid(self, pid: int) -> int:
        try:
            return int(os.getpgid(pid))
        except OSError:
            return 0

    def _append(self, record: Mapping[str, Any]) -> None:
        if self.path is None:
            return
        if _append_member(self.path, record):
            self.appended += 1
        else:
            self.failed += 1
        self._write_state()

    def _write_state(self) -> None:
        """``members.<inc>.state.json``: how many lines this watcher appended and how many it
        could not -- a reader treats `failed > 0` (or a missing state) as unknown accounting."""
        if self.path is None:
            return
        state = {"schema": MEMBER_SCHEMA + ".state", "appended": self.appended, "failed": self.failed,
                 "members": len(self.members), "discovery": json.loads(json.dumps(self.discovery)),
                 "written_at": _now_iso()}
        tmp = self.path + b".state.json.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(fd, json.dumps(state, sort_keys=True).encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmp, self.path + b".state.json")
        except OSError:
            pass

    def _add(self, pid: int, start_id: int, role: str, observed_via: str, *,
             ppid: int | None = None) -> bool:
        """Admit ``(pid, start_id)`` as a positive member -- on Linux ONLY once its fixed object
        binds the same incarnation the caller read (REVIEW_IMPLEMENTATION_iteration8 F-016).

        The caller's ``start_id`` / ``ppid`` are PRE-acquisition reads of `/proc`; between them
        and `pidfd_open` the process may be reaped and its pid (and, at tick granularity, its
        start id) reborn as a FOREIGN process -- the reviewer's same-tick admission cut.  So a
        descendant is admitted only when, AFTER the pidfd is held: (a) the pidfd is alive
        (the pid has been that one process since the open), (b) `/proc` re-read through that
        window reports the SAME start id and the SAME ppid the caller read (the read is bound
        to the pidfd's process because that process was alive after it), and (c) the parent it
        names is this subreaper or a member whose OWN fixed object is still alive after (b).
        Anything else is the named `candidate_identity_unverified` and NOT a member; a pidfd
        that cannot be obtained at all is `pidfd_unavailable` and NOT a member.  The agent
        (P1) is this watcher's own child: its pid is held until our own `waitpid`, which is
        the binding, so its pidfd needs no re-verification."""
        watch_registered = role == MEMBER_ROLE_AGENT and self._root_watch == "registered"
        if pid <= 0 or not start_id:
            return False
        lifetimes = self._lifetimes(pid, start_id)
        if any(not r.get("exited") for _k, r in lifetimes):
            return False                                   # that lifetime is already a member
        # F-016: a (pid, start) key whose recorded lifetimes ALL exited names a NEW lifetime --
        # Linux start ticks are not injective, so a cached exited key never suppresses a live
        # candidate; the ledger carries the lifetime ordinal
        lifetime = len(lifetimes) + 1
        key = (int(pid), int(start_id), lifetime)
        if len(self.members) >= _MEMBER_CEILING:
            # a POSITIVELY attributed descendant that the ledger will not hold is not "not
            # ours" -- it is unknown from here on, and said so (once per pid)
            if pid not in self._unreadable_seen:
                self._unreadable_seen.add(pid)
                self._discovery_unreadable(DISCOVERY_CEILING_EXCEEDED, f"ceiling:{_MEMBER_CEILING}",
                                           observed_via, [int(pid)])
            return False
        record = {"schema": MEMBER_SCHEMA, "event": MEMBER_EVENT_OBSERVED,
                  "identity": _member_identity(pid, start_id, self.boot_id, self.fence),
                  "role": role, "observed_via": observed_via, "pgid": self._pgid(pid),
                  "lifetime": lifetime, "fixed_object": "none", "observed_at": _now_iso()}
        if sys.platform == "linux":
            fd = -1
            try:
                fd = os.pidfd_open(int(pid))                 # the FIXED object (F-016)
            except (OSError, AttributeError) as exc:
                self._discovery_unreadable(DISCOVERY_PIDFD_UNAVAILABLE,
                                           f"{type(exc).__name__}:{getattr(exc, 'errno', '')}",
                                           observed_via, [int(pid)])
                if role != MEMBER_ROLE_AGENT:
                    return False                           # i8: no fixed object, no positive member
            exited_at_admission = False
            if fd >= 0 and role != MEMBER_ROLE_AGENT:
                unverified, exited_at_admission = self._unverified_after_acquisition(pid, start_id, ppid, fd)
                if unverified:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                    if unverified != "fixed_object:gone":
                        self._discovery_unreadable(DISCOVERY_IDENTITY_UNVERIFIED, unverified,
                                                   observed_via, [int(pid)])
                    # else: the bound process ended AND was reaped between the listing and
                    # the acquisition -- ordinary churn (as a pid gone between the listing and
                    # the read is); nothing alive is admitted and nothing is claimed about it
                    return False
            if fd >= 0:
                self._pidfds[key] = fd
                record["fixed_object"] = "pidfd"
                ino = _pidfd_inode(fd)
                if self._pidfs_model and ino and self._pidfs_ref and ino != self._pidfs_ref:
                    # a reader's binding, valid only under the recorded (proven) model
                    record["fixed_object_id"] = ino
                    record["fixed_object_model"] = self._pidfs_model
        elif sys.platform == "darwin":
            record["fixed_object"] = "kqueue_note_exit" if self._kq is not None else "none"
        self.members[key] = record
        self._append(record)
        if sys.platform == "linux" and exited_at_admission:
            # a ZOMBIE bound and verified through its fixed object: positively ours, and P5 at
            # once (the subreaper's reap will follow); never a live member
            self._exited(int(pid), int(start_id), via="pidfd_exit")
            return True
        if watch_registered or self._kq is None:
            return True
        # a DESCENDANT's watch is registered after its birth: whatever it forked before this
        # instant is unobservable -- the named gap (never cleared)
        self._discovery_unreadable(DISCOVERY_FORK_WATCH_GAP, "registered_after_birth", observed_via, [int(pid)])
        try:
            self._kq.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                            flags=select.KQ_EV_ADD,
                                            fflags=select.KQ_NOTE_FORK | select.KQ_NOTE_EXIT)],
                             0, 0)
        except OSError as exc:
            # the member was gone before its watch existed (ESRCH: it already exited --
            # possibly after forking) or the kernel refused the watch: ITS forks are
            # unobservable.
            self._discovery_unreadable(DISCOVERY_WATCH_UNREGISTERED,
                                       f"{type(exc).__name__}:{getattr(exc, 'errno', '')}",
                                       observed_via, [int(pid)])
        return True

    def _unverified_after_acquisition(self, pid: int, start_id: int, ppid: "int | None",
                                      fd: int) -> "tuple[str, bool]":
        """i8 F-016: ``(reason, exited)`` -- the reason the held ``fd`` does NOT prove the
        incarnation the caller read (``""`` when it does), and whether the bound process has
        already exited (a zombie: still holding its pid, still readable, positively ours when
        the re-read matches -- P5 through the fixed object at once).  Order matters: `/proc` is
        re-read FIRST and the pidfd polled AFTER, so an alive answer proves the process the
        read described held the pid through the read; a zombie holds its pid until reaped, so
        its re-read is bound the same way; the parent member's fixed object is polled after
        that, for the same reason."""
        again = _process_info(int(pid))
        state = _pidfd_alive(fd)
        if state == "unavailable":
            return "fixed_object:unavailable", False
        if again is None:
            # exited AND reaped (or unreadable): nothing binds the pre-acquisition read
            return ("fixed_object:gone" if state == "exited" else "reread:unreadable"), False
        if int(again[1]) != int(start_id):
            return f"start_id:{start_id}->{again[1]}", False
        if ppid is not None and int(again[0]) != int(ppid):
            return f"ppid:{ppid}->{again[0]}", False
        if ppid is not None and int(ppid) != os.getpid():
            parent = self._live_member_for(int(ppid))
            if not isinstance(parent, dict):
                return "parent:" + ("unreadable" if parent == "unreadable" else "unbound"), False
        return "", state == "exited"

    def _live_member_for(self, ppid: int) -> "dict[str, Any] | str | None":
        """P3: the member record that a child with parent ``ppid`` may be attributed to -- a
        member with that pid whose CURRENT start identity, re-read now by a positive source,
        equals the recorded one and which has not been observed exiting.  ``None`` refuses
        POSITIVELY (the kernel says no process holds the pid -- a live child naming it was
        forked by a reused pid -- or the pid holds a different, readable incarnation).
        ``"unreadable"`` (F-012): the identity can be read by no source while the kernel still
        holds the pid (live and refused, or a zombie): holding a numeric pid does not bind
        the zombie to the cached incarnation -- another parent may have reaped that one --
        so the attribution can be neither made nor refused and the caller records the
        child as UNKNOWN.  The old held-zombie exception is gone."""
        unreadable = False
        for key, record in list(self.members.items()):
            pid, start_id = key[0], key[1]
            if pid != ppid or record.get("exited"):
                continue
            if sys.platform == "linux":
                # F-016: the FIXED object decides -- an alive pidfd means the pid is still
                # exactly that process (a pid is held until reaped); an exited one is P5 and
                # the pid may already belong to someone else; no pidfd = unreadable
                state = self._pidfd_state(key)
                if state == "alive":
                    return record
                if state == "exited":
                    self._exited(pid, start_id, via="pidfd_exit")
                    continue
                unreadable = True
                continue
            info = _process_info(pid) or _process_info_fallback(pid)
            if info is not None:
                if int(info[1]) == int(start_id):
                    return record
                continue                                   # a different incarnation holds the pid
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue                                   # positively gone: the pid was reused
            except OSError:
                pass
            unreadable = True
        return "unreadable" if unreadable else None

    def _exited(self, pid: int, start_id: int | None = None, via: str = "") -> None:
        for key, record in list(self.members.items()):
            mpid, mstart = key[0], key[1]
            if mpid != pid or record.get("exited"):
                continue
            if start_id is not None and mstart != start_id:
                continue
            record["exited"] = True
            fd = self._pidfds.pop(key, None)
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            self._append({"schema": MEMBER_SCHEMA, "event": MEMBER_EVENT_EXITED,
                          "identity": record["identity"], "role": record["role"], "lifetime": record.get("lifetime", 1),
                          "observed_via": via or ("note_exit" if sys.platform == "darwin" else "subreaper_reap"),
                          "pgid": record.get("pgid", 0), "observed_at": _now_iso()})

    def _discovery_unreadable(self, kind: str, reason: str, trigger: str,
                              pids: "list[int] | None" = None,
                              parent: "Mapping[str, Any] | None" = None,
                              evidence: "Mapping[str, Any] | None" = None) -> None:
        """F-009 / F-010: account discovery uncertainty DURABLY and separately from the appends
        -- the state file's `discovery` block (counted even when the ledger line cannot be
        written) and one `discovery_unreadable` ledger record naming the kind, the reason, the
        trigger, the pids concerned and, for a fork, the PARENT identity + the kernel event."""
        counter = DISCOVERY_COUNTERS[kind]
        self.discovery[counter] += len(pids) if pids else 1
        tag = f"{kind}:{reason}"
        if tag not in self.discovery["reasons"] and len(self.discovery["reasons"]) < _DISCOVERY_REASON_CEILING:
            self.discovery["reasons"].append(tag)
        for pid in pids or ():
            if len(self.discovery["pids"]) < _DISCOVERY_PID_CEILING and pid not in self.discovery["pids"]:
                self.discovery["pids"].append(int(pid))
        self._append({"schema": MEMBER_SCHEMA, "event": MEMBER_EVENT_DISCOVERY_UNREADABLE,
                      "kind": kind, "reason": reason, "trigger": trigger,
                      "pids": [int(p) for p in (pids or ())][:_DISCOVERY_PID_CEILING],
                      "parent": dict(parent) if parent else None,
                      "evidence": dict(evidence) if evidence else None,
                      "pass": self.discovery["passes"], "fence": self.fence,
                      "observed_at": _now_iso()})

    def _record_for_pid(self, pid: int) -> "dict[str, Any] | None":
        """The latest member record (any incarnation, live first) for ``pid``."""
        live = [r for k, r in self.members.items() if k[0] == pid and not r.get("exited")]
        if live:
            return live[-1]
        gone = [r for k, r in self.members.items() if k[0] == pid]
        return gone[-1] if gone else None

    def _list_candidates(self) -> "tuple[list[int] | None, str]":
        """The process table for one pass, or ``(None, reason)`` when it cannot be read --
        darwin: the cross-checked libproc listing (`listallpids_*` reasons); Linux: `/proc`."""
        if sys.platform == "darwin":
            pids, why = _libproc_list_all_pids()
            if pids is None:
                return None, str(why or "listallpids_unreadable")
            return list(pids), ""
        try:
            return [int(name) for name in os.listdir("/proc") if name.isdigit()], ""
        except OSError as exc:
            return None, f"proc_listdir:{type(exc).__name__}:{getattr(exc, 'errno', '')}"

    def discover(self, reason: str) -> int:
        """One bounded discovery pass set: add every process whose ppid is a LIVE, CURRENT member
        incarnation (P3) or, on Linux, this subreaper itself (P4), with its start identity
        captured now.  Returns the number of members added.  Every non-positive observation
        is a named UNKNOWN (iteration 6 model): a listing that cannot be read / settle
        (`listing_*`); a live candidate no source will read, or one positively parented to
        a member / the subreaper whose start identity is unreadable
        (`candidate_identity_unreadable`, F-013); a candidate whose parent member cannot be
        re-read (`parent_identity_unreadable`, F-012); and ``self._forked`` -- the member
        pids whose NOTE_FORK `serve` just observed -- each a `fork_coalesced` record with the
        parent identity and the kernel event, whether or not this walk attributed children
        to it (a coalesced notification never proves the set of children; F-010)."""
        added = 0
        listing_failed = False
        forked, self._forked = self._forked, {}
        for parent_pid, fflags in forked.items():
            record = self._record_for_pid(parent_pid) or {}
            identity = record.get("identity") or {"pid": parent_pid}
            presence = _pid_presence(parent_pid)
            self._discovery_unreadable(
                DISCOVERY_FORK_COALESCED, f"parent:{parent_pid}", reason, [int(parent_pid)],
                parent=identity,
                evidence={"fflags": int(fflags), "note_fork": bool(fflags & select.KQ_NOTE_FORK),
                          "note_exit": bool(fflags & select.KQ_NOTE_EXIT),
                          "parent_exited": bool(record.get("exited")) or presence == "absent",
                          "parent_presence": presence})
        for _ in range(_MEMBER_DISCOVERY_PASSES):
            found = 0
            self.discovery["passes"] += 1
            candidates, why = self._list_candidates()
            if candidates is None:
                kind = (DISCOVERY_LISTING_UNSTABLE if why == "listallpids_unstable"
                        else DISCOVERY_LISTING_UNREADABLE)
                self._discovery_unreadable(kind, why, reason)
                listing_failed = True
                break
            known_pids = {k[0] for k in self.members}
            unreadable: list[int] = []
            parent_unreadable: list[int] = []
            me = os.getpid()
            for pid in candidates:
                if pid == me:
                    continue
                info = _process_info(pid) or _process_info_fallback(pid)
                if info is None:
                    # gone between the listing and the read is ordinary (ESRCH / a zombie);
                    # a pid the kernel still HOLDS whose identity no source will read is not
                    if _pid_presence(pid) == "absent":
                        continue
                    info = _process_info(pid) or _process_info_fallback(pid)   # a reused pid reads now
                    if info is None:
                        if pid not in self._unreadable_seen:
                            unreadable.append(int(pid))
                        continue
                ppid, start = info
                current = [(k, r) for k, r in self._lifetimes(pid, start) if not r.get("exited")]
                if current:
                    if sys.platform == "linux" and self._pidfd_state(current[-1][0]) == "exited":
                        # F-016: the recorded lifetime ended (its fixed object says so) and
                        # a live process holds the same (pid, tick): a NEW lifetime -- fall
                        # through and attribute it on its own parentage
                        self._exited(pid, start, via="pidfd_exit")
                    else:
                        continue
                ours_by_parent = ppid in known_pids or (sys.platform == "linux" and ppid == me)
                if not start:
                    # F-013: a readable ppid with NO start identity -- positively parented to
                    # a member or to this subreaper, it is OURS and unrecordable by
                    # incarnation: unknown, by pid, never omitted
                    if ours_by_parent and pid not in self._unreadable_seen:
                        unreadable.append(int(pid))
                    continue
                via = ""
                if ppid in known_pids:
                    parent = self._live_member_for(ppid)
                    if parent == "unreadable":
                        parent_unreadable.append(int(pid))
                        continue
                    if parent is not None:
                        via = ("note_fork_ppid_scan" if sys.platform == "darwin" else "proc_ppid_walk") + f":{reason}"
                if not via and sys.platform == "linux" and ppid == me:
                    via = f"subreaper_reparent:{reason}"                       # P4
                if via and self._lifetimes(pid, start) and _pid_presence(pid) == "absent":
                    # a ZOMBIE (exited, not yet reaped -- e.g. the root itself before the
                    # watcher's own waitpid) whose key is ALREADY a recorded lifetime IS that
                    # lifetime: never a NEW one (found by the i8 non-root docker run).  A zombie
                    # with no recorded lifetime is still attributed below -- it was positively
                    # ours and its fixed object records P5 at once.
                    continue
                if via and self._add(pid, start, MEMBER_ROLE_DESCENDANT, via, ppid=int(ppid)):
                    found += 1
            if unreadable:
                self._unreadable_seen.update(unreadable)
                self._discovery_unreadable(DISCOVERY_CANDIDATE_UNREADABLE,
                                           "process_info_unreadable", reason, unreadable)
            if parent_unreadable:
                self._discovery_unreadable(DISCOVERY_PARENT_UNREADABLE,
                                           "parent_member_unreadable", reason, parent_unreadable)
            added += found
            if not found:
                break
        else:
            # every bounded pass attributed something: a deeper generation may still exist
            self._discovery_unreadable(DISCOVERY_PASS_CEILING, f"passes:{_MEMBER_DISCOVERY_PASSES}", reason)
        self._write_state()                          # the pass count is durable evidence too
        return added

    def serve(self) -> None:
        """darwin: consume kqueue events -- NOTE_FORK triggers discovery, NOTE_EXIT marks the
        member exited (its pid is now free to be reused; the ledger keeps its identity).
        Linux: poll the held pidfds -- a readable one is P5 for that member (F-016)."""
        if sys.platform == "linux":
            for key in list(self._pidfds):
                if self._pidfd_state(key) == "exited":
                    self._exited(key[0], key[1], via="pidfd_exit")
            return
        if self._kq is None:
            return
        try:
            events = self._kq.control(None, 64, 0)
        except OSError as exc:
            # the events (forks among them) that were pending are LOST evidence: said once
            tag = f"{type(exc).__name__}:{getattr(exc, 'errno', '')}"
            if tag not in self._once:
                self._once.add(tag)
                self._discovery_unreadable(DISCOVERY_EVENTS_UNREADABLE, tag, "serve")
            return
        forked: dict[int, int] = {}
        exited: list[int] = []
        for event in events:
            if event.fflags & select.KQ_NOTE_FORK:
                forked[int(event.ident)] = int(event.fflags)
            if event.fflags & select.KQ_NOTE_EXIT:
                exited.append(int(event.ident))
        # F-010: the fork-triggered walk runs BEFORE the exits are marked; every observed
        # fork is a `fork_coalesced` unknown whatever the walk attributes
        if forked:
            self._forked = forked
            self.discover("note_fork")
        for pid in exited:
            self._exited(pid)

    def note_reaped(self, pid: int) -> None:
        """P5 for a Linux subreaper reap.  A reaped pid that no walk ever attributed is a
        POSITIVE fact that an unaccounted descendant existed (and forked unobserved): named."""
        if not any(k[0] == pid for k in self.members):
            self._discovery_unreadable(DISCOVERY_REAPED_UNATTRIBUTED, "subreaper_reap", "reap", [int(pid)])
            return
        self._exited(pid)

    def close(self) -> None:
        """The watcher stops observing: one last walk (a descendant reparented / forked since
        the previous one), then every member not observed exiting (P5) may still fork --
        their later descendants are unobservable, a named UNKNOWN (`watch_ended`)."""
        try:
            self.discover("close")
        except Exception:  # noqa: BLE001 - the ledger must still be closed
            pass
        alive = sorted({k[0] for k, r in self.members.items() if not r.get("exited")})
        if alive:
            self._discovery_unreadable(DISCOVERY_WATCH_ENDED, "members_alive_at_watcher_exit", "close", alive)
        self._write_state()
        for fd in self._pidfds.values():
            with contextlib.suppress(OSError):
                os.close(fd)
        self._pidfds.clear()
        if self._kq is not None:
            try:
                self._kq.close()
            except OSError:
                pass
            self._kq = None


class _ParentWitness:
    """[FORKED-SAFE] The watcher's incarnation-bound death witness for the SUPERVISOR (its
    parent), registered at start -- darwin: kqueue ``NOTE_EXIT`` on the pinned pid; Linux:
    ``pidfd_open``.  Guard EOF is relinquishment-or-death; only this witness (or a durable
    relinquishment record) may authorise succession (DESIGN §2.5, probe_d11)."""

    def __init__(self, identity: Mapping[str, Any]) -> None:
        self.pid = int(identity.get("pid") or 0)
        self.start_id = int(identity.get("start_id") or 0)
        self.state = "unreadable"
        self._kq: Any = None
        self._pidfd = -1
        if self.pid <= 0:
            return
        try:
            observed = proc_start_ticks(self.pid)
        except Exception:  # noqa: BLE001
            observed = 0
        if not self.start_id:
            self.state = "unreadable"
            return
        if not observed:
            # The pinned pid may already be GONE (the supervisor died between the fork and
            # this registration): ESRCH is the kernel's positive answer that no process has
            # the pid, so the pinned incarnation is dead -- witnessed.  Anything else is
            # unreadable.
            self.state = "final" if _pid_presence(self.pid) == "absent" else "unreadable"
            return
        if observed != self.start_id:
            self.state = "inconsistent"
            return
        try:
            if sys.platform == "darwin":
                self._kq = select.kqueue()
                self._kq.control([select.kevent(self.pid, filter=select.KQ_FILTER_PROC,
                                                flags=select.KQ_EV_ADD,
                                                fflags=select.KQ_NOTE_EXIT)], 0, 0)
            elif hasattr(os, "pidfd_open"):
                self._pidfd = os.pidfd_open(self.pid)
            else:
                return
            self.state = "present"
        except OSError as exc:
            # ESRCH at registration: the kernel has no LIVE process for the pid whose start
            # identity matched a moment ago -- the pinned incarnation is already dead (a
            # zombie awaiting its parent, or reaped between the two reads): witnessed.
            self.state = "final" if exc.errno == errno.ESRCH else "unreadable"

    def covers(self, pid: int, start_id: int) -> bool:
        """Whether this witness was registered on EXACTLY the incarnation ``(pid, start_id)``
        (F-002: a witness is evidence about the process it was pinned to and no other)."""
        return int(pid or 0) == self.pid and int(start_id or 0) == self.start_id and self.pid > 0

    def fds(self) -> set[int]:
        out: set[int] = set()
        if self._kq is not None:
            out.add(self._kq.fileno())
        if self._pidfd >= 0:
            out.add(self._pidfd)
        return out

    def fired(self, timeout: float = 0.0) -> str:
        """``final`` once the pinned incarnation's exit was witnessed; else the current state."""
        if self.state == "final":
            return "final"
        try:
            if self._kq is not None:
                if self._kq.control(None, 1, timeout):
                    self.state = "final"
            elif self._pidfd >= 0:
                ready, _, _ = select.select([self._pidfd], [], [], timeout)
                if ready:
                    self.state = "final"
        except OSError:
            self.state = "unreadable"
        return self.state


class _ControlServer:
    """[FORKED-SAFE] Serves ``SIG <signum> <fence>\\n`` requests on the control socket.  The
    caller (the watcher's single loop) tells it whether the child is already reaped."""

    def __init__(self, fd: int, *, agent_pid: int, fence: str) -> None:
        self.fd = fd
        self.agent_pid = agent_pid
        self.fence = fence
        self.reaped = False

    def serve(self, *, reaped: bool) -> None:
        if self.fd < 0:
            return
        try:
            request = os.read(self.fd, 256)
        except OSError:
            return
        if not request:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
            return
        for line in request.split(b"\n"):
            if not line.strip():
                continue
            parts = line.split()
            reply = b"refused:malformed"
            if len(parts) == 3 and parts[0] == b"SIG":
                try:
                    signum = int(parts[1])
                except ValueError:
                    signum = -1
                if parts[2].decode("utf-8", "replace") != self.fence:
                    reply = b"refused:signal_unbound"
                elif reaped or self.reaped:
                    reply = b"refused:signal_target_reaped"
                elif signum <= 0:
                    reply = b"refused:malformed"
                else:
                    try:
                        os.kill(self.agent_pid, signum)       # bound: same thread, child not yet reaped
                        reply = b"sent"
                    except ProcessLookupError:
                        reply = b"refused:esrch"
                    except OSError as exc:
                        reply = f"refused:errno_{exc.errno}".encode()
            try:
                os.write(self.fd, reply + b"\n")
            except OSError:
                pass


_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37


def _set_subreaper() -> str:  # pragma: no cover - linux only
    """Linux: ``prctl(PR_SET_CHILD_SUBREAPER)`` via ctypes so orphaned descendants reparent to
    the watcher and are reaped with a positive exit status (DESIGN probe_d3).

    REVIEW_IMPLEMENTATION_iteration7 F-014: the setup is VERIFIED -- the set call's return is
    checked and ``PR_GET_CHILD_SUBREAPER`` must read back 1 -- and the RECEIPT is returned:
    ``"subreaper"`` (positive) or the named failure ``"ownership_setup_unverified:<why>"``.
    A platform name is never a receipt."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            return f"ownership_setup_unverified:set:{_errno_name_n(ctypes.get_errno())}"
        flag = ctypes.c_int(-1)
        if libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(flag), 0, 0, 0) != 0:
            return f"ownership_setup_unverified:get:{_errno_name_n(ctypes.get_errno())}"
        if flag.value != 1:
            return f"ownership_setup_unverified:readback:{flag.value}"
    except (OSError, AttributeError, ValueError) as exc:
        return f"ownership_setup_unverified:{type(exc).__name__}"
    return "subreaper"


def _reap_reparented(agent_pid: int, members: "_Membership | None" = None) -> None:  # pragma: no cover - linux only
    """Linux subreaper: collect any reparented descendant that exited -- NEVER the agent,
    whose exit status is the pinned root's proof and must be collected by the watcher's own
    `waitpid(agent_pid)` (a `waitpid(-1)` here could consume it and forge code 0).  The
    candidate is PEEKED with ``WNOWAIT`` first and reaped only by its own pid."""
    if not hasattr(os, "waitid"):
        return
    while True:
        try:
            info = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return
        if info is None or int(info.si_pid) <= 0 or int(info.si_pid) == agent_pid:
            return
        try:
            os.waitpid(int(info.si_pid), os.WNOHANG)
        except ChildProcessError:
            return
        if members is not None:
            members.note_reaped(int(info.si_pid))


def _open_marker_descriptor(slave_fd: int, slave_name: str = "") -> int:
    """[FORKED-SAFE] A NEW open file description on the slave device, opened by PATH
    (``slave_name``, or the device name of ``slave_fd`` read now), non-blocking from the open:
    its file-status flags are its own.  ``-1`` when it cannot be opened (the caller then
    writes nothing and the boundary stays `boundary_unproven` by name -- it never falls back
    to mutating a shared description)."""
    path = slave_name
    if not path:
        try:
            path = os.ttyname(slave_fd)
        except OSError:
            return -1
    try:
        return os.open(path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK
                       | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return -1


def _write_marker_bounded(slave_fd: int, marker: bytes, master_fd: int,
                          appender: Any, attempts: int = 200, *, slave_name: str = "") -> bool:
    """[FORKED-SAFE] Write the marker into the slave's output FIFO WITHOUT blocking forever
    (DESIGN O-3) and WITHOUT touching the file-status flags of any descriptor the agent
    subtree shares (OS-48 PR #36 finding 3).

    The owner-held ``slave_fd`` is the SAME open file description as the agent's 0/1/2 (the
    leader ``dup2``'d it before forking the agent) and as every descendant's inherited stdio;
    an ``F_SETFL(O_NONBLOCK)`` on it -- what this function did before -- turned the whole
    subtree's blocking reads/writes into ``EAGAIN`` / short writes for the duration of the
    marker write (MEASURED from inside a descendant: `test_os48_pr36_locks` L-5).  The marker
    is therefore written through a descriptor opened SEPARATELY by the slave's device path
    (`_open_marker_descriptor`): a new open file description, non-blocking from its open, on
    the same tty -- the same single output FIFO (DESIGN §1.1), so the in-band ordering fact is
    unchanged (re-measured over 256 KiB, L-5) -- and closed right after.  ``slave_fd`` is
    never written through and never has a flag set or cleared; it remains the owner-held
    KEEPALIVE reference only (DESIGN §1.3).  On ``EAGAIN`` the watcher drains the master it
    holds (so the FIFO can make room) and retries, bounded.  ``False`` = the marker could not
    be written (no device path, the path could not be opened, or the bound elapsed): a
    successor then reads `boundary_unproven` by name."""
    if not marker or slave_fd < 0:
        return False
    marker_fd = _open_marker_descriptor(slave_fd, slave_name)
    if marker_fd < 0:
        return False
    written = 0
    ok = False
    try:
        for _ in range(attempts):
            try:
                written += os.write(marker_fd, marker[written:])
            except BlockingIOError:
                if appender is not None:
                    _drain_once(master_fd, appender, budget=0.01)
                else:
                    time.sleep(0.005)
                continue
            except OSError:
                break
            if written >= len(marker):
                ok = True
                break
    finally:
        try:
            os.close(marker_fd)
        except OSError:
            pass
    return ok


def _defer_for_release(dh_r: int, guard_r: int, control_fd: int, ctl: "_ControlServer",
                       slave_fd: int, fence_nonce: str, master_fd: int, *,
                       budget_s: float, on_tick: Any = None,
                       slave_name: str = "") -> tuple[bool, bool]:  # pragma: no cover - runs in the forked watcher
    """DESIGN §1.6 two-phase release, watcher side.  Blocks after the sentinel, keeping the
    slave reference, serving: ``R`` (release-1: write the RELEASE marker, close NOTHING),
    ``C`` (release-2: the supervisor consumed up to R -- close is done by the caller), control
    requests (all refused `signal_target_reaped` now), and guard EOF (supervisor gone ->
    returns ``True`` = orphan).  A generous ceiling covers a supervisor wedged holding both
    ends; expiry returns ``False`` (the slave is then closed without a release record and a
    successor names `diagnostic_tail_unaccounted`)."""
    ceiling = max(30.0, budget_s * 8.0)
    deadline = time.monotonic() + ceiling
    fds = [fd for fd in (dh_r, guard_r, control_fd) if fd >= 0]
    # REVIEW_IMPLEMENTATION F-003: the RELEASE marker is written AT MOST ONCE per nonce.  The
    # flag travels back to the caller so an orphan finalize that follows a served release-1
    # REUSES the marker already in the stream instead of emitting a duplicate (which would
    # make the boundary `inconsistent`).
    release_written = False
    while fds:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, release_written
        if on_tick is not None:
            on_tick()                                 # membership bookkeeping while deferring
        try:
            ready, _, _ = select.select(fds, [], [], min(0.25, remaining))
        except (OSError, ValueError):
            return False, release_written
        if control_fd >= 0 and control_fd in ready:
            ctl.serve(reaped=True)
            if ctl.fd < 0:
                fds = [fd for fd in fds if fd != control_fd]
            continue
        if dh_r in ready:
            try:
                byte = os.read(dh_r, 1)
            except OSError:
                byte = b""
            if byte == b"R":
                if not release_written:
                    _write_marker_bounded(slave_fd, capture_mod.release_marker_bytes(fence_nonce)
                                          if fence_nonce else b"", master_fd, None,
                                          slave_name=slave_name)
                    release_written = True
                continue
            if byte == b"C":
                return False, release_written         # release-2: the caller closes the slave
            if byte == b"":
                # the supervisor's write end closed (its `release`): relinquishment-or-death;
                # the guard decides below
                fds = [fd for fd in fds if fd != dh_r]
                continue
            continue                                  # legacy "1" byte: keep waiting for C/guard
        if guard_r in ready:
            return True, release_written
    return False, release_written


def _orphan_finalize(master_fd: int, slave_fd: int, appender: Any, *, capture: bytes,
                     fence: str, fence_nonce: str, code: int, marker_written: bool,
                     sentinel: str | os.PathLike[str] | None, witness: "_ParentWitness",
                     budget_s: float, host_boot_id: str, agent_pid: int, agent_start_id: int,
                     clock: Any = time.monotonic, reader: Any = os.read,
                     release_written: bool = False, sidecar_path: str = "",
                     slave_name: str = "") -> dict[str, Any]:
    """[FORKED-SAFE] The exit watcher's ORPHAN finalize (DESIGN §1.6 orphan path).  The agent
    is reaped and the marker written; the supervisor is gone (guard EOF).  In order:
    1. drain the master into the capture until the FENCE MARKER is in the capture FILE (bounded);
    2. FENCE FIRST: a published fence makes this watcher a CUSTODIAN (no claim, verify only);
    3. else decide succession by `may_claim_generation` -- a durable relinquishment record or the
       incarnation-bound parent-death witness authorises a claim of exactly predecessor+1;
    4. publish the fence (link-exclusive);
    5. release: RELEASE marker -> drain to R -> release record -> close the slave -> drain to EOF.
    Never raises past the caller; every non-success leaves a NAMED state for a successor."""
    out: dict[str, Any] = {"role": "", "outcome": None}
    incarnation = fence.partition(":")[2]
    directory = os.path.dirname(capture) or b"."
    deadline = clock() + max(budget_s, 0.5)
    marker_len = 0
    offset_n = -1
    while clock() < deadline:
        if appender is not None:
            _drain_once(master_fd, appender, budget=0.05)
        try:
            data = _read_file(capture)
        except OSError:
            data = b""
        offset_n, marker_len, state = capture_mod.marker_span(data, fence_nonce) if fence_nonce else (-1, 0, "unknown")
        if state == capture_mod.EVIDENCE_FINAL:
            break
        if state == capture_mod.EVIDENCE_INCONSISTENT:
            offset_n = -1
            break
        if not marker_written and appender is None:
            break
    if appender is not None:
        try:
            appender.save_meta()
        except Exception:  # noqa: BLE001
            pass
    if offset_n < 0:
        out["outcome"] = capture_mod.OUTCOME_BOUNDARY_UNPROVEN
        return out
    fence_path = capture_mod.capture_fence_path(capture, incarnation)
    existing = capture_mod.read_capture_fence(fence_path, fence=fence)
    owner_identity = identity.process_identity(pid=os.getpid(), start_id=proc_start_ticks(os.getpid()),
                                               boot_id=host_boot_id, incarnation=fence,
                                               source=evidence_source_id())
    emitter_identity = identity.process_identity(pid=agent_pid, start_id=agent_start_id,
                                                 boot_id=host_boot_id, incarnation=fence,
                                                 source=evidence_source_id())
    generation = None
    if existing["outcome"] == capture_mod.EVIDENCE_FINAL:
        out["role"] = "custodian"
    elif not (identity.identity_complete(owner_identity) and identity.identity_complete(emitter_identity)):
        # REVIEW_IMPLEMENTATION F-005: no claim, no publication with an unreadable identity.
        out["role"] = "custodian_no_claim"
        out["outcome"] = identity.IDENTITY_UNREADABLE
    else:
        highest, highest_rec, gstate = capture_mod.read_generations(directory, incarnation)
        relinquish = capture_mod.read_relinquish(directory, incarnation, highest)["outcome"] == "present" if highest else False
        # DESIGN §2.5 rule 2 / cut C4: guard EOF ALONE never authorises a claim -- not even
        # g1.  The pinned supervisor's death must be WITNESSED (kqueue NOTE_EXIT / pidfd on the
        # incarnation pinned at spawn) or durably relinquished; the fds close a moment before
        # the exit is notified, so the witness is given a short bounded wait, never assumed.
        witness_state = witness.fired(1.0 if witness.state == "present" else 0.0)
        out["highest_generation"] = highest
        alive: bool | None = None
        death_evidence = "note_exit_pinned" if sys.platform == "darwin" else "pidfd_readable"
        if highest and highest_rec:
            # REVIEW_IMPLEMENTATION F-002: the parent-death witness was registered on the
            # SUPERVISOR incarnation pinned at spawn.  It is evidence about that process only.
            # If the highest generation is owned by a DIFFERENT incarnation (a successor that
            # claimed over the dead supervisor), the witness does not cover it: obtain exact,
            # per-pid evidence for THAT owner (`read_identity`: absent = positively gone,
            # final+equal start = positively alive, unreadable = no claim) -- never reuse g1's
            # death for g2.
            owner = highest_rec.get("owner") or {}
            pinned_pid, pinned_start = int(owner.get("pid") or 0), int(owner.get("start_id") or 0)
            if witness.covers(pinned_pid, pinned_start):
                alive = witness_state != "final" and _pid_presence(pinned_pid) != "absent"
                if alive:
                    witness_state = "present" if witness_state == "present" else witness_state
            else:
                observed = read_identity(pinned_pid) if pinned_pid > 0 else {"start_state": "unreadable", "start_id": 0}
                if observed["start_state"] == "absent":
                    alive, witness_state, death_evidence = False, "final", "esrch_or_zombie_pinned_pid"
                elif observed["start_state"] != capture_mod.EVIDENCE_FINAL or not pinned_start:
                    alive, witness_state = None, capture_mod.EVIDENCE_UNREADABLE
                elif int(observed["start_id"]) == pinned_start:
                    alive, witness_state = True, capture_mod.EVIDENCE_UNKNOWN
                else:
                    alive, witness_state, death_evidence = False, "final", "start_identity_mismatch_pinned_pid"
        elif highest == 0 and witness_state == "final":
            # No generation at all: the supervisor that could have claimed g1 is the pinned
            # parent and its death is what the witness attests.
            pass
        out["witness"] = witness_state
        out["highest_owner_alive"] = alive
        action, outcome = capture_mod.may_claim_generation(
            fence_published=False, highest_owner_alive=alive if highest else None,
            relinquish_record=relinquish, death_witness=witness_state)
        if action != "claim":
            # No claim -- but this watcher still HOLDS the only slave reference, so the
            # release protocol below is its obligation regardless (RC1 shape): whoever
            # publishes the fence needs the RELEASE marker in the stream to bound the tail.
            out["outcome"] = outcome or capture_mod.OUTCOME_SUCCESSION_UNWITNESSED
            out["role"] = "custodian_no_claim"
            generation = None
        predecessor = (highest_rec or {}).get("owner") if highest else None
        evidence = {"predecessor_generation": highest, "predecessor": predecessor or {},
                    "relinquish_record": relinquish, "death_witness": witness_state,
                    "highest_owner_alive": alive}
        generation = None if action != "claim" else capture_mod.make_owner_generation(
            fence=fence, generation=highest + 1, owner_role=capture_mod.OWNER_EXIT_WATCHER,
            owner=owner_identity, claim_reason="orphan_guard_eof",
            superseded=predecessor,
            death_evidence=("relinquish_record" if relinquish else death_evidence),
            claimed_at=_now_iso())
        refused = (capture_mod.claim_generation(directory, incarnation, generation, evidence)
                   if generation is not None else out["outcome"])
        if refused is not None:
            out["outcome"] = refused
            out["role"] = out["role"] or "custodian_no_claim"
            generation = None
        data = _read_file(capture)
        snapshot = capture_mod.read_sidecar_snapshot(capture, incarnation, fence=fence)
        sidecar_field = (None if not sidecar_path else
                         {k: snapshot["record"].get(k) for k in ("state", "path", "sha256", "bytes", "instant", "error")}
                         if snapshot["record"] is not None else {"state": capture_mod.SIDECAR_STATE_UNPROVEN})
        # PR #36 finding 1: the capture's answerability AT PUBLISH, from this writer's own
        # appender state (raw, forked-safe): a limit drop / line cut (`truncation`) or an
        # irreversible unanswerable cause (`unanswerable`) recorded up to now means the range
        # is not vouched for; otherwise [0, N) is complete and later tail events are diagnostic.
        publish_state = None
        if appender is not None:
            lost = ""
            integrity = ""
            if getattr(appender, "unanswerable", ""):
                lost, integrity = capture_mod.CAPTURE_INTEGRITY_LOST_REASON, str(appender.unanswerable)
            elif getattr(appender, "truncation", ""):
                lost = capture_mod.CAPTURE_TRUNCATED_LOST_REASON
            publish_state = capture_mod.capture_state_at_publish(
                answerable={"answerable": not lost, "lost_reason": lost, "integrity": integrity},
                truncation=getattr(appender, "truncation", ""),
                dropped_bytes=int(getattr(appender, "dropped", 0) or 0),
                total_bytes=int(getattr(appender, "total", 0) or 0))
        record = None if generation is None else capture_mod.make_capture_fence(
            fence=fence,
            emitter=emitter_identity,
            emitter_pgid=agent_pid, offset_n=offset_n, marker_len=marker_len,
            marker_nonce=fence_nonce, sha256_prefix=capture_mod.prefix_digest(data, offset_n),
            tail_bytes_at_publish=max(0, len(data) - offset_n - marker_len),
            exit_how="exit_sentinel" if sentinel is not None else "waitpid_by_parent",
            exit_code=code, reaped_by=owner_identity, owner=generation,
            evidence_source=evidence_source_id(),
            provenance=[capture_mod_PROVENANCE_REAPED, capture_mod_PROVENANCE_MARKER_WRITTEN,
                        capture_mod_PROVENANCE_MARKER_OBSERVED, capture_mod_PROVENANCE_OWNER_CLAIMED,
                        capture_mod_PROVENANCE_SENTINEL]
                       + ([capture_mod_PROVENANCE_ANSWERABLE_AT_PUBLISH]
                          if publish_state and publish_state["answerable"] else []),
            published_at=_now_iso(), sidecar=sidecar_field, capture_state=publish_state)
        if record is None:
            pass                                       # no claim: release only, publish nothing
        elif not capture_mod.write_capture_fence(fence_path, record):
            out["role"] = "custodian"                  # lost the link race: verify, never overwrite
        else:
            out["role"] = "finalizer"
    # ---- release: RELEASE marker -> drain to R -> release record -> close -> drain to EOF ---
    # F-003: a RELEASE marker already written for a served release-1 (the supervisor died
    # between release-1 and its record) is REUSED -- the boundary R is whatever the stream
    # already holds; a second marker with the same nonce would make it `inconsistent`.
    if not release_written:
        _write_marker_bounded(slave_fd, capture_mod.release_marker_bytes(fence_nonce), master_fd, appender,
                              slave_name=slave_name)
    out["release_marker_reused"] = bool(release_written)
    r_deadline = clock() + max(budget_s, 0.5)
    offset_r, r_state = -1, capture_mod.EVIDENCE_UNKNOWN
    while clock() < r_deadline:
        if appender is not None:
            _drain_once(master_fd, appender, budget=0.05)
        data = _read_file(capture)
        offset_r, r_len, r_state = capture_mod.find_release_marker(data, fence_nonce, after=offset_n)
        if r_state != capture_mod.EVIDENCE_UNKNOWN:
            break
    if appender is not None:
        try:
            appender.save_meta()
        except Exception:  # noqa: BLE001
            pass
    out["release_state"] = r_state if offset_r >= 0 or r_state == capture_mod.EVIDENCE_INCONSISTENT else capture_mod.EVIDENCE_UNKNOWN
    if offset_r < 0:
        out["release_outcome"] = (capture_mod.OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
                                  if r_state == capture_mod.EVIDENCE_INCONSISTENT
                                  else capture_mod.OUTCOME_RELEASE_RECORD_MISSING)
    if offset_r >= 0 and capture_mod.read_capture_fence(fence_path, fence=fence)["outcome"] == capture_mod.EVIDENCE_FINAL:
        data = _read_file(capture)
        tail = data[offset_n + marker_len:offset_r]
        out["release_outcome"] = None
        capture_mod.write_release_record(
            capture_mod.release_record_path(capture, incarnation),
            capture_mod.make_release_record(
                fence=fence, fence_file_sha256=capture_mod.file_digest(fence_path),
                release_nonce=fence_nonce, offset_r=offset_r, retained_tail_bytes=len(tail),
                retained_tail_sha256=capture_mod.prefix_digest(tail, len(tail)),
                custodian=owner_identity, custodian_role=capture_mod.OWNER_EXIT_WATCHER,
                state=capture_mod.EVIDENCE_FINAL, published_at=_now_iso()))
    try:
        os.close(slave_fd)
    except OSError:
        pass
    # Closing the owner-held reference releases only THIS holder's slave: a descendant that
    # still holds an inherited slave can keep writing (post-release bytes -- darwin may
    # discard them on its last close, residual O-8, named), so what the master still holds
    # is read out, bounded, and everything here is DIAGNOSTIC -- nothing after N is proof.
    eof_deadline = clock() + 0.25
    while clock() < eof_deadline:
        try:
            ready, _, _ = select.select([master_fd], [], [], 0.05)
        except (OSError, ValueError):
            break
        if not ready:
            continue
        try:
            chunk = reader(master_fd, 65_536)
        except OSError:
            break
        if not chunk:
            break
        if appender is not None:
            try:
                appender.append(chunk)
            except Exception:  # noqa: BLE001
                pass
    if appender is not None:
        try:
            appender.save_meta()
            appender.close()
        except Exception:  # noqa: BLE001
            pass
    _write_orphan_note(capture, incarnation, out)
    return out


def orphan_note_path(capture: str | os.PathLike[str] | bytes, incarnation: str) -> bytes:
    """``<capture>.orphan.<inc>.json``: the orphan watcher's NAMED outcome (DESIGN §2.6: every
    non-success leaves a named state for a successor).  Diagnostic; never a proof."""
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    return target + b".orphan." + incarnation.encode() + b".json"


def _write_orphan_note(capture: bytes, incarnation: str, note: Mapping[str, Any]) -> None:
    """[FORKED-SAFE] Best-effort durable note of the orphan finalize's outcome."""
    path = orphan_note_path(capture, incarnation)
    tmp = path + b".tmp"
    try:
        payload = json.dumps(dict(note), sort_keys=True).encode()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, path)
    except (OSError, TypeError, ValueError):
        pass


def _read_file(path: bytes) -> bytes:
    """[FORKED-SAFE] Whole-file read with raw ``os`` (the capture is bounded by its limits)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        out = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                return out
            out += chunk
    finally:
        os.close(fd)


#: DESIGN §1.5 provenance facts (mirrored from the design stub).
capture_mod_PROVENANCE_REAPED = "emitter_reaped_by_parent_waitpid"
capture_mod_PROVENANCE_MARKER_WRITTEN = "marker_written_by_owner_into_owner_held_slave_after_reap"
capture_mod_PROVENANCE_MARKER_OBSERVED = "marker_observed_in_capture_at_offset_n"
capture_mod_PROVENANCE_OWNER_CLAIMED = "owner_record_claimed_exclusive_link"
capture_mod_PROVENANCE_SENTINEL = "exit_sentinel_present_same_fence"
#: PR #36 finding 1: listed ONLY when the publisher MEASURED the capture answerable at publish
#: (the fence's `capture_at_publish` field carries the fact itself).
capture_mod_PROVENANCE_ANSWERABLE_AT_PUBLISH = "capture_integrity_answerable_at_publish"


def evidence_source_id() -> str:
    return (identity.EVIDENCE_SOURCE_DARWIN if sys.platform == "darwin"
            else identity.EVIDENCE_SOURCE_LINUX)


_HOST_BOOT_ID: str | None = None


def host_boot_id() -> str:
    """The host boot identity, read once per process (parent-only: darwin uses a subprocess)."""
    global _HOST_BOOT_ID
    if _HOST_BOOT_ID is None:
        _HOST_BOOT_ID = boot_id()
    return _HOST_BOOT_ID


def read_identity(pid: int) -> dict[str, Any]:
    """The platform evidence source's identity read for ``pid`` AT DECISION TIME (DESIGN §2.1):
    ``{"start_id", "start_state", "boot_id"}``.  ``start_state`` is ``final`` when the kernel
    answered, ``unreadable`` otherwise; the boot id is the host's."""
    try:
        start = proc_start_ticks(int(pid)) if int(pid) > 0 else 0
    except Exception:  # noqa: BLE001
        start = 0
    if start:
        state = capture_mod.EVIDENCE_FINAL
    else:
        state = _pid_presence(int(pid))
    return {"start_id": int(start or 0), "start_state": state, "boot_id": host_boot_id()}


def _pid_presence(pid: int) -> str:
    """``absent`` when the kernel POSITIVELY answers that no process has ``pid`` right now
    (``kill(pid, 0)`` -> ESRCH -- pids are unique among live processes, so the pinned
    incarnation cannot be alive); ``unreadable`` for every other answer (EPERM: a process
    exists but is not ours; any other error).  Never inferred from a table scan."""
    if pid <= 0:
        return capture_mod.EVIDENCE_UNREADABLE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "absent"
    except OSError:
        return capture_mod.EVIDENCE_UNREADABLE
    # A pid the kernel still holds but whose identity is unreadable is a ZOMBIE candidate.
    # darwin: `kevent(EVFILT_PROC)` answers ESRCH for a zombie (no LIVE process has the pid --
    # measured in this run's evidence); a zombie has exited, so the pinned incarnation is not
    # alive.  Linux: `/proc/<pid>/stat` state `Z`.  Anything else stays unreadable.
    if sys.platform == "darwin":
        try:
            kq = select.kqueue()
            try:
                kq.control([select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                          flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                          fflags=select.KQ_NOTE_EXIT)], 0, 0)
            finally:
                kq.close()
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return "absent"
        return capture_mod.EVIDENCE_UNREADABLE
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
    except (OSError, IndexError):
        return capture_mod.EVIDENCE_UNREADABLE
    return "absent" if state == "Z" else capture_mod.EVIDENCE_UNREADABLE


def request_watcher_signal(control_fd: int, sig: int, fence: str, *,
                           timeout_s: float = 5.0) -> str:
    """Ask the exit watcher (the agent's parent) to deliver ``sig`` (DESIGN §2.3).  Returns
    ``"sent"`` or a named refusal (``refused:signal_target_reaped`` / ``refused:signal_unbound``
    / ``refused:esrch`` / ``refused:no_watcher``)."""
    if not isinstance(control_fd, int) or control_fd < 0:
        return "refused:no_watcher"
    try:
        os.write(control_fd, f"SIG {int(sig)} {fence}\n".encode())
    except OSError:
        return "refused:no_watcher"
    deadline = time.monotonic() + timeout_s
    buf = b""
    while time.monotonic() < deadline and b"\n" not in buf:
        try:
            ready, _, _ = select.select([control_fd], [], [], 0.05)
        except (OSError, ValueError):
            return "refused:no_watcher"
        if not ready:
            continue
        try:
            chunk = os.read(control_fd, 256)
        except OSError:
            return "refused:no_watcher"
        if not chunk:
            return "refused:no_watcher"
        buf += chunk
    return buf.split(b"\n", 1)[0].decode("utf-8", "replace") or "refused:no_watcher"


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
_PIDLIST_MAX_ATTEMPTS = 12                # re-query proc_listallpids until the three enumerations agree
_PIDLIST_RETRY_SLEEP_S = 0.001            # let ordinary process churn settle between attempts (i5 flake: 6 back-to-back ~0.5 ms attempts all disagreed under host churn)
_PIDLIST_SLACK = 4096                     # spare entry capacity; a full buffer means truncation
_FD_SCAN_MAX_ATTEMPTS = 8                 # per-process: retries toward two identical clean scans
_FD_SCAN_RETRY_SLEEP_S = 0.001           # let ordinary fd-table churn settle between retries
_SHORT_READ_ERRNO = "short_read"         # sentinel: a positive but wrong-length libproc return
_PARTIAL_FILL_ERRNO = "partial_fill"     # sentinel: a whole-entry PREFIX shorter than the count (F-004)
_KINFO_PROC_SIZE = 648                   # sizeof(struct kinfo_proc), macOS 64-bit
_KINFO_PROC_PID_OFF = 40                 # kp_proc.p_pid (verified: own pid / parent / pid 1 all found there)
_KINFO_PROC_START_SEC_OFF = 0            # kp_proc.p_starttime.tv_sec  (int64; == PROC_PIDTBSDINFO pbi_start_tvsec)
_KINFO_PROC_START_USEC_OFF = 8           # kp_proc.p_starttime.tv_usec (int32; == pbi_start_tvusec)
_KINFO_PROC_PPID_OFF = 560               # kp_eproc.e_ppid (verified: own parent found there, 0 mismatches over the table)
_FD_WALK_CEILING = 2048                  # fd-table indices the F-004 cross-check will walk per process

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
        before = _sysctl_all_pids()                        # the independent walk, BEFORE the fill
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
            time.sleep(_PIDLIST_RETRY_SLEEP_S)
            continue
        pids = [buf[i] for i in range(int(got)) if buf[i] > 0]
        # REVIEW_IMPLEMENTATION_iteration2 F-004: a POSITIVE fill is never trusted on its own
        # -- the count query is the table CAPACITY plus kernel slack, so no deficit rule can
        # tell a missed entry from churn.  Completeness is a CROSS-CHECK against an INDEPENDENT
        # kernel enumeration (`sysctl kern.proc.all`): every pid the sysctl walk reports that
        # the libproc fill omitted is a missed entry -> `listallpids_partial`; a walk that cannot
        # be read is `listallpids_crosscheck_unreadable`; disagreement that never settles under
        # churn is `listallpids_unstable`.  The scanner's own pid is an additional anchor only.
        after = _sysctl_all_pids()
        if before is None or after is None:
            return None, "listallpids_crosscheck_unreadable"
        filled = set(pids)
        # REVIEW_IMPLEMENTATION_iteration3 F-004: completeness is claimed ONLY when the three
        # enumerations AGREE EXACTLY (independent walk before == libproc fill == independent
        # walk after).  A pid in both walks and absent from the fill is a MISSED entry
        # (`listallpids_partial`); any other disagreement -- a process born or exited between
        # the reads, which is indistinguishable from a partial fill from user space -- is
        # changing evidence: retried, and named `listallpids_unstable` if it never settles.
        # An intersection is never treated as completeness.
        if (before & after) - filled or os.getpid() not in filled:
            return None, "listallpids_partial"
        if before == filled == after:
            return pids, None
        reason = "listallpids_unstable"
        time.sleep(_PIDLIST_RETRY_SLEEP_S)
    return None, reason


def _sysctl_all_pids() -> "set[int] | None":
    """[darwin] The pids of `sysctl kern.proc.all` -- the kernel's OTHER enumeration of the
    process table (a `kinfo_proc` array, pid at byte 40 -- verified against the calling
    process, its parent and pid 1).  ``None`` when the walk cannot be read."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 14, 0)                       # CTL_KERN, KERN_PROC, KERN_PROC_ALL
        for _ in range(4):
            size = ctypes.c_size_t(0)
            if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value <= 0:
                return None
            cap = size.value + _KINFO_PROC_SIZE * 64
            buf = ctypes.create_string_buffer(cap)
            got = ctypes.c_size_t(cap)
            if libc.sysctl(mib, 3, buf, ctypes.byref(got), None, 0) != 0:
                return None
            if got.value >= cap or got.value % _KINFO_PROC_SIZE != 0:
                continue                                          # grew past the buffer: retry
            raw = buf.raw[:got.value]
            pids = {struct.unpack_from("<i", raw, i * _KINFO_PROC_SIZE + _KINFO_PROC_PID_OFF)[0]
                    for i in range(got.value // _KINFO_PROC_SIZE)}
            pids.discard(0)
            if os.getpid() in pids:
                return pids
        return None
    except (OSError, ValueError, AttributeError):
        return None


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


def _fd_walk_omissions(pid: int, listed: "list[int]") -> "list[int] | None":
    """[F-004 cross-check] The vnode fds answered by a per-index walk of ``pid``'s fd table that
    the listing ``listed`` omitted; ``None`` when the table is too large to walk within the
    bound (then the scan is `unreadable`, never assumed complete).  Uses a complete BSDINFO
    read for the table size; an unreadable size is treated as the ceiling."""
    info_size = _darwin_bsdinfo_nfiles(pid)
    if info_size is None:
        return None                                   # F-004 (i2): unreadable size = unreadable walk
    if info_size > _FD_WALK_CEILING:
        return None
    known = set(listed)
    omitted: list[int] = []
    for fd in range(int(info_size)):
        if fd in known:
            continue
        devino, err = _libproc_fd_devino(pid, fd)
        if devino is not None or err == _SHORT_READ_ERRNO:
            omitted.append(fd)
    return omitted


def _darwin_bsdinfo_nfiles(pid: int) -> "int | None":
    """[FORKED-SAFE] ``pbi_nfiles`` (the fd TABLE size, byte 96 of an exact-136 BSDINFO)."""
    lib = _libproc_handle()
    if lib is None or not hasattr(lib, "proc_pidinfo"):
        return None
    try:
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                     ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(_PROC_PIDTBSDINFO_SIZE)
        written = lib.proc_pidinfo(int(pid), _PROC_PIDTBSDINFO, 0, buffer, _PROC_PIDTBSDINFO_SIZE)
    except (OSError, ValueError, AttributeError):
        return None
    if written != _PROC_PIDTBSDINFO_SIZE:
        return None
    return int(struct.unpack_from("<I", buffer.raw, 96)[0])


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
                # OS-48 (I-2): a DENIED listing is unreadable evidence, never "not our uid".
                return "unstable", "listing_denied"
            if err in (_SHORT_READ_ERRNO, _PARTIAL_FILL_ERRNO):
                # F-004: a positive partial / malformed listing is NAMED, never retried into
                # a clean scan (a retry that happened to agree would still omit the fd).
                return "unstable", f"listing_{err}"
            prev = None                                    # transient listing failure -> retry
            time.sleep(_FD_SCAN_RETRY_SLEEP_S)
            continue
        # REVIEW_IMPLEMENTATION F-004: the listing is a SINGLE positive read whose fill length
        # cannot be validated by the count query (that query returns the fd-table CAPACITY plus
        # slack, measured 45 entries for 4 open fds).  A whole-entry prefix that omits a live
        # vnode fd is therefore caught by an INDEPENDENT read: walk every fd index of the table
        # (`pbi_nfiles`, bounded) with `PROC_PIDFDVNODEPATHINFO`; a vnode fd the walk answers
        # that the listing did not name means the listing was partial -> named, never clean.
        omitted = _fd_walk_omissions(pid, fds)
        if omitted is None:
            # the independent walk could not be taken (table size unreadable / beyond the
            # bound): the listing is UNVERIFIED -- named, never clean
            return "unstable", ("fd_walk_unbounded" if _darwin_bsdinfo_nfiles(pid) is not None
                                else "fd_table_size_unreadable")
        if omitted:
            return "unstable", "listing_partial_fill"
        scan: "dict[int, tuple[int, int]]" = {}
        failed = False
        for fd in fds:
            devino, fe = _libproc_fd_devino(pid, fd)
            if devino is None:
                if _errno_is_gone(fe):
                    # OS-48 (I-2, ANALYSIS F8): a per-fd ENOENT is a REVOKED (stale) vnode on a
                    # LIVE process, never proof the process is gone -- unreadable by name.
                    return "unstable", "stale_revoked_fd"
                if fe in (errno.EPERM, errno.EACCES):
                    # This fd's vnode is permission-restricted (a TCC/sandbox-protected resource
                    # that macOS refuses to introspect even for the owner -- common on fd 3 of a
                    # user's launchd agents).  A pty slave vnode is NEVER permission-restricted:
                    # the slave's own vnode info is readable in the reference AND in a real holder
                    # (verified), so a fd we are refused is PROVABLY not the slave.  Skip THIS fd
                    # (never a match here) and keep inspecting the rest; do not fail the scan --
                    # otherwise every host with a restricted-fd agent is permanently `unreadable`.
                    # OS-48 (I-2): a denied per-fd read is unreadable evidence, not "not the slave".
                    return "unstable", "fd_denied"
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
    """**OS-48: DIAGNOSTIC ONLY.**  A negative whole-process-table / descriptor enumeration is
    never evidence of finality (ANALYSIS F0: `proc_listallpids` / `PROC_PIDLISTFDS` /
    `PROC_PIDFDVNODEPATHINFO` are independent non-atomic reads, and ordinary fork / SCM_RIGHTS
    / close between them defeat any negative).  No decision function calls this; the runtime
    journals it as `holders_diagnostic` at most.  Its states are `present` / `unreadable` /
    `none_observed` -- the word "proven" does not occur.  Denied, short and stale (revoked
    ENOENT) reads are `unreadable` by name (I-2).

    Historical: iteration 4/5 (option B) -- a COMPLETE, fail-closed slave-descriptor authority (darwin
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

    ``{"method": "libproc", "state": "present"|"none_observed"|"unreadable", "holders": [pid],
    "unenumerable": [{"pid", "errno", "where"}], "other_uid": [pid], "gone": [pid],
    "device": ...}``.  ``state`` is ``present`` if any holder, else ``unreadable`` if any process
    was ``unenumerable`` (fail closed -- an incomplete scan is NEVER absence), else
    ``none_observed`` (DIAGNOSTIC; processes that merely exited mid-scan, or are other-uid and thus cannot
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
        # OS-48: a scan that found nothing is `none_observed` -- DIAGNOSTIC ONLY.  It is not
        # "proven_absent": no negative enumeration can be (ANALYSIS F0, DESIGN I-1).
        out["state"] = "none_observed"
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
    * ``none_observed`` -- (DIAGNOSTIC ONLY under OS-48) the probe completed and found nothing: the foreground-group probe
      COMPLETED and found the group empty and, where ``/proc`` exists, a COMPLETE scan
      found no holder;
    * ``unreadable`` -- an authority the absence proof needs could not be read (a failed
      ``tcgetpgrp`` / ``killpg``, an unreadable ``/proc``, or a skipped ``/proc/<pid>/fd``).

    Under OS-48 NONE of these states authorises anything; they are journal diagnostics.  ``present`` and ``unreadable`` are
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
    return "none_observed"


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
                  killpg: Any = None, kill: Any = None, watcher: Any = None,
                  pidfd_send: Any = None) -> dict[str, Any]:
    """Send ``sig`` to the AGENT through an incarnation-bound path, honouring the decision.
    Takes a :class:`Permit` (the only way in: :func:`standalone_identity.assert_may_act`).

    OS-48 DESIGN §2.3 (F-002 / F-003):

    * the agent is signalled **by the exit watcher** -- its parent, whose single thread both
      reaps and delivers, so a request served before the reap addresses a pid the kernel
      cannot reuse (probe_d7) -- through ``watcher(sig)`` (the control-socket request);
      ``refused:signal_target_reaped`` after the reap, ``refused:signal_unbound`` when no
      watcher exists (a darwin non-child has no atomic signal primitive; on Linux a held
      pidfd may be supplied as ``pidfd_send``);
    * **no user-space ``killpg`` is ever sent** (`may_killpg` → `group_signal_refused`): a
      leader's identity does not cover the recipients at delivery.  Group teardown is the
      kernel's own SIGHUP to the foreground process group at controlling-tty revoke
      (probe_d8).  The ``killpg`` / ``kill`` seams remain only so a lock can PROVE nothing
      reaches them.
    """
    identity.require_permit(permit, record, "signal")
    if decision.get("verdict") != "owned" or decision.get("scope") == "none":
        return {"sent": (), "refusal": decision.get("refusal") or "not_owned",
                "scope": "none"}
    sent: list[dict[str, Any]] = []
    _allowed, why = identity.may_killpg(int(record.get("pgid") or 0))
    sent.append({"rung": "descendant_groups", "target": int(record.get("pgid") or 0),
                 "result": f"withheld:{why}"})
    leader = int(record.get("sid") or record["pgid"])
    if leader != int(record["pid"]):
        sent.append({"rung": "session_leader", "target": leader,
                     "result": "withheld:exit_watcher"})
    pid = int(record["pid"])
    if watcher is not None:
        try:
            result = str(watcher(sig))
        except Exception as exc:  # noqa: BLE001
            result = f"refused:{type(exc).__name__}"
        sent.append({"rung": "agent_via_watcher", "target": pid, "result": result})
    elif pidfd_send is not None:
        _try(sent, "agent_via_pidfd", pid, lambda: pidfd_send(sig))
    else:
        sent.append({"rung": "agent_via_watcher", "target": pid,
                     "result": "refused:" + identity.SIGNAL_UNBOUND})
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


def request_release_1(session: Mapping[str, Any]) -> bool:
    """OS-48 release-1 (DESIGN §1.8): ask the deferring watcher to write the RELEASE marker
    into its slave fd.  It closes NOTHING yet.  ``False`` when no handoff end exists."""
    handoff = session.get("drain_handoff_fd")
    if isinstance(handoff, int) and handoff >= 0:
        try:
            os.write(handoff, b"R")
            return True
        except OSError:
            return False
    return False


def _signal_drain_handoff(session: Mapping[str, Any]) -> None:
    """OS-48 release-2: tell a watcher deferring in :func:`_defer_for_release` that the
    custodian has consumed the stream up to the RELEASE marker -- write the ``C`` byte to the
    handoff's write end, then close it (the watcher then closes its owner-held slave
    reference).  The byte -- not the close -- is the reliable wake: a ``select`` on the read
    end returns on the data whether or not
    the last write end has closed, so a lingering descriptor copy can never wedge the
    release.  Idempotent: the fd is set to ``-1`` once released."""
    handoff = session.get("drain_handoff_fd")
    if isinstance(handoff, int) and handoff >= 0:
        try:
            os.write(handoff, b"C")       # OS-48 release-2: the custodian consumed up to R
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
    # OS-48: the watcher HOLDS a slave reference and wrote the fence marker through it, so
    # its exit closes a slave with output pending on the master -- the exact wedge
    # :func:`drain` documents ("trying to exit" until the master is read).  A caller that
    # already drained (the runtime's two-phase release) loses nothing here; a raw caller
    # gets the teardown obligation honoured (bytes DISCARDED, counted, never claimed).
    master = session.get("master_fd")
    master = master if isinstance(master, int) and master >= 0 else -1
    drained = 0
    deadline = time.time() + timeout_ms / 1000.0
    while True:
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return {"reaped": True, "status": None, "drained_bytes": drained,
                    "detail": "already reaped or not this process's child"}
        except OSError as exc:
            return {"reaped": False, "status": None, "drained_bytes": drained,
                    "detail": f"waitpid: {exc}"}
        if done == pid:
            return {"reaped": True, "status": _wait_status_to_code(status),
                    "drained_bytes": drained, "detail": ""}
        if time.time() >= deadline:
            return {"reaped": False, "status": None, "drained_bytes": drained,
                    "detail": "the exit watcher is still running at the deadline"}
        if master >= 0:
            drained += drain(master, budget_ms=20)
        else:
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
    # OS-48: the control socket's supervisor end.
    control = session.get("control_fd")
    if isinstance(control, int) and control >= 0:
        try:
            os.close(control)
        except OSError:
            pass
        if isinstance(session, dict):
            session["control_fd"] = -1
