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

import errno
import fcntl
import json
import os
import pty
import re
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

from . import standalone_identity as identity
from .standalone_profile import StandaloneProfile

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
        return 0


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
    for line in text.splitlines():
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


def spawn(*, argv: Sequence[str], env: Mapping[str, str], profile: StandaloneProfile,
          session_id: str, incarnation: str, spawn_record_target: str | os.PathLike[str],
          cwd: str | None = None, argv_digest: str = "", env_digest: str = "",
          sentinel: str | os.PathLike[str] | None = None,
          fence: str = "", image: str | None = None) -> PtySession:
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
    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)
    close_up_to = highest_open_fd()
    fcntl.fcntl(master_fd, fcntl.F_SETFD, fcntl.FD_CLOEXEC)
    _set_raw(slave_fd)
    _set_winsize(slave_fd, profile.rows, profile.cols)
    # The agent's pid is minted two forks down, so it is handed back up a pipe rather than
    # guessed.  Bounded, and a failure to learn it is a spawn failure -- never a fabricated
    # pid, because every ownership refusal in this module keys on the recorded pid.
    handoff_r, handoff_w = os.pipe()

    started_at = _now_iso()
    leader_pid = os.fork()
    if leader_pid == 0:  # pragma: no cover - the leader never returns
        try:
            os.close(handoff_r)
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
            # ---- finding 9: the LEADER holds no pty descriptor while it waits ---------
            # It inherited the master (from the parent) and the slave (its own 0/1/2 plus
            # the original), and it never execs, so `FD_CLOEXEC` never closed either.
            # Those copies are what kept the pty alive after the parent released its
            # master: hangup cannot be produced while any copy of the master is open, and
            # the slave's last close -- the EOF the parent's drain reads as "the agent is
            # gone" -- cannot happen while the watcher still holds one.  The agent already
            # has its own 0/1/2 on the slave; the watcher's job is `waitpid`, which needs
            # no terminal at all.
            try:
                os.close(master_fd)
                os.close(slave_fd)
                null = os.open(os.devnull, os.O_RDWR)
                for fd in (0, 1, 2):
                    os.dup2(null, fd)
                if null > 2:
                    os.close(null)
            except OSError:
                pass
            code = _wait_status_to_code(os.waitpid(agent_pid, 0)[1])
            if sentinel is not None:
                write_exit_sentinel(sentinel, code=code, fence=fence)
            os._exit(code)
        except BaseException:
            os._exit(127)
    os.close(slave_fd)
    os.close(handoff_w)
    try:
        agent_pid = _read_handoff(handoff_r)
    finally:
        os.close(handoff_r)
    if agent_pid <= 0:
        os.close(master_fd)
        raise OSError(errno.ECHILD,
                      "the pty session leader never reported an agent pid; no agent "
                      "process identity exists, so none is invented")
    return {"master_fd": master_fd, "slave_name": slave_name, "pid": agent_pid,
            "pgid": agent_pid, "sid": leader_pid, "leader_pid": leader_pid,
            "pty_id": f"pty-{uuid.uuid4().hex[:12]}", "argv": tuple(str(a) for a in argv)}


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
    if _pid_exists(pid):
        # The pid exists but is not on our tty.  Either it was recycled (so ours is gone)
        # or it moved (which cannot happen for a session leader).  Recycling is not proof
        # of OUR exit unless the incarnation is absent, which the row check just showed.
        return {"proven": True, "reason": "incarnation_absent_from_captured_tty"}
    return {"proven": True, "reason": "esrch_and_incarnation_absent"}


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
    """
    pid = session.get("leader_pid")
    if not isinstance(pid, int) or pid <= 0:
        return {"reaped": False, "status": None, "detail": "no leader pid recorded"}
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
