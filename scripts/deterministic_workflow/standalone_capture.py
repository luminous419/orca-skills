"""OS-37 N5.  Bounded, cursored output capture with NAMED truncation (AC-37-05).

Three properties are the whole point:

*Bounded, with the bound reported.*  Every limit is explicit and every limit has a named
cause.  Nothing wraps.  A reader that gets ``truncated=True`` and asks a completion
question is answered ``LOST`` with ``lost_reason="capture_truncated"`` rather than a guess
-- refusal R-7 applied to this surface.

*Cursored by BYTE OFFSET into a file on disk*, so the cursor is stable across processes.  A
successor process reading this capture after the creating process is gone gets the same
records from the same cursor; an in-memory ring buffer could not offer that, and AC-37-12's
re-queryability is not a nice-to-have here.

*Readable after ``release()``.*  The file is deliberately not deleted when the session ends.
AC-37-05 is written so that this holds independently of U6 -- whether Orca's own capture
survives terminal release -- and U6 stays UNKNOWN.

The file is written VERBATIM: it is the agent's own transcript, and rewriting it would
destroy the evidence it exists to be.  What is *not* written anywhere is any environment
value or auth material -- that discipline lives in :mod:`standalone_env` and the journal.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

from .standalone_profile import CaptureLimits

#: Named truncation causes, in the order they are checked.  A caller never sees `True` on
#: its own: truncation always arrives with the reason it happened.
TRUNCATION_TOTAL_BYTES = "total_bytes"
TRUNCATION_LINE_BYTES = "line_bytes"
TRUNCATION_RECORD_COUNT = "record_count"

#: The lost_reason a completion question gets when the transcript it would have to read is
#: incomplete.  R-7: an unreadable or partial authority is unknown, never empty.
CAPTURE_TRUNCATED_LOST_REASON = "capture_truncated"

#: The lost_reason when the capture's INTEGRITY METADATA disagrees with the bytes on disk
#: (consolidated follow-up review, finding 4).  A member of `lifecycle.LOST_REASONS`.
CAPTURE_INTEGRITY_LOST_REASON = "evidence_unreadable"

#: The two writers a capture file admits.  Recorded in the meta so a reader can tell
#: which process wrote the tail -- the live supervisor, or the exit watcher after the
#: supervisor died.  Any other value is not this contract's.
WRITER_SUPERVISOR = "supervisor"
WRITER_EXIT_WATCHER = "exit_watcher"

#: The meta file's schema.  A meta that names another schema is a disagreement.
META_SCHEMA = "os37.capture_meta.v2"


class CaptureRecord(TypedDict):
    offset: int
    at: str
    data: str            # decoded with errors="replace"; `raw_len` keeps the true length
    raw_len: int
    truncation: str      # "" unless THIS record was cut
    stream: str          # "pty"


class CaptureRead(TypedDict):
    records: tuple[CaptureRecord, ...]
    next_cursor: int
    truncated: bool
    truncation: str | None
    dropped_bytes: int


class BoundedCapture:
    """One session's capture store.  Append-only, cursored, limit-reporting.

    The state file beside the log holds the counters.  It is a derived index and is
    rebuilt from the log if it is missing -- it is never the authority for the bytes.
    """

    def __init__(self, path: str | os.PathLike[str], *,
                 limits: CaptureLimits | None = None) -> None:
        self.path = Path(path)
        self.limits = limits or CaptureLimits()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.path.with_name(self.path.name + ".meta.json")
        self._records = 0
        self._total = 0
        self._dropped = 0
        self._truncation: str | None = None
        #: Running digest of every byte this store has WRITTEN, persisted in the meta as
        #: `sha256` (finding 4).  A reader recomputes it from the file and refuses a
        #: capture whose bytes and meta disagree -- whichever process wrote the tail.
        self._digest = hashlib.sha256()
        self._writer = WRITER_SUPERVISOR
        self._meta_present = False
        self._load_meta()

    # -- meta ----------------------------------------------------------------------------
    def _load_meta(self) -> None:
        """Adopt the counters the meta names, and the digest OF THE FILE AS IT IS.

        The digest is recomputed from the bytes on disk (bounded by the store's own
        limit, so the read is bounded too) rather than trusted from the meta: the meta's
        `sha256` is what :meth:`integrity` compares AGAINST, and seeding the running digest
        from it would make the comparison a tautology.
        """
        self._digest = hashlib.sha256()
        self._meta_present = False
        try:
            meta = json.loads(self._meta_path.read_text())
        except (OSError, ValueError):
            # No meta: the counters are rebuilt from the log itself, which is the authority
            # for the bytes.  Whether that is CONSISTENT is `integrity`'s question.
            try:
                self._total = self.path.stat().st_size
            except OSError:
                self._total = 0
            self._records = 0
            self._digest = _digest_of(self.path)
            return
        self._meta_present = True
        self._records = int(meta.get("records", 0))
        self._total = int(meta.get("total_bytes", 0))
        self._dropped = int(meta.get("dropped_bytes", 0))
        truncation = meta.get("truncation")
        self._truncation = truncation if isinstance(truncation, str) and truncation else None
        self._writer = str(meta.get("writer") or WRITER_SUPERVISOR)
        self._digest = _digest_of(self.path)

    def refresh(self) -> None:
        """Re-read the meta and the file.  For a reader whose file another process --
        the exit watcher after the supervisor died -- may still be appending to."""
        self._load_meta()

    def _save_meta(self) -> None:
        write_meta(self._meta_path, records=self._records, total_bytes=self._total,
                   dropped_bytes=self._dropped, truncation=self._truncation or "",
                   sha256=self._digest.hexdigest(), writer=self._writer)
        self._meta_present = True

    # -- append --------------------------------------------------------------------------
    def append(self, chunk: bytes, *, at: str) -> CaptureRecord | None:
        """Append ``chunk``, honouring every limit.  Returns the record, or ``None``.

        ``None`` means the limit was already reached and this chunk was DROPPED -- counted
        in ``dropped_bytes``, never silently discarded and never wrapped over the beginning.
        """
        if not isinstance(chunk, bytes):
            raise TypeError("capture appends bytes; a str would already have lost encoding")
        # ---- consolidated review finding 13 -----------------------------------------
        # A chunk the store could not keep WHOLE is counted exactly once, as the number of
        # bytes that did not reach the file, and it makes the store `truncated` with the
        # cause named.  It used to do neither: an over-long chunk was cut, its excess added
        # to `dropped_bytes`, and the store was left `truncated=False` -- so a completion
        # question was answered from a transcript whose result record may have been in
        # the bytes that were cut.  And a cut chunk that THEN hit the record or total limit
        # had its whole length added a second time.  R-7 applies to the cut as much as to
        # the drop: bytes this store does not hold are bytes a reader cannot reason from.
        #
        # The decision itself lives in `admit_chunk`, shared with the exit watcher's
        # orphan path (follow-up review finding 4), so the two writers of one capture file
        # cannot disagree about what the limits mean.
        decision = admit_chunk(chunk, records=self._records, total=self._total,
                               limits=self.limits)
        self._dropped += decision["dropped"]
        payload = decision["payload"]
        if payload is None:
            # A whole-chunk drop names ITS cause: the store is now bounded by that limit.
            self._truncation = decision["truncation"]
            self._save_meta()
            return None
        if decision["truncation"]:
            self._truncation = self._truncation or decision["truncation"]
        offset = self._total
        with open(self.path, "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._total += len(payload)
        self._records += 1
        self._digest.update(payload)
        self._save_meta()
        return {"offset": offset, "at": at,
                "data": payload.decode("utf-8", errors="replace"),
                "raw_len": len(chunk), "truncation": decision["record_truncation"],
                "stream": "pty"}

    # -- read ----------------------------------------------------------------------------
    def read(self, cursor: int = 0, limit: int = 65_536) -> CaptureRead:
        """Read from a byte ``cursor``.  Stable across processes by construction."""
        if cursor < 0:
            raise ValueError("a capture cursor is a byte offset and cannot be negative")
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        records: tuple[CaptureRecord, ...] = ()
        if cursor < size:
            with open(self.path, "rb") as handle:
                handle.seek(cursor)
                data = handle.read(max(0, limit))
            records = ({"offset": cursor, "at": "",
                        "data": data.decode("utf-8", errors="replace"),
                        "raw_len": len(data), "truncation": "", "stream": "pty"},)
            cursor += len(data)
        return {"records": records, "next_cursor": cursor,
                "truncated": self._truncation is not None,
                "truncation": self._truncation, "dropped_bytes": self._dropped}

    def transcript(self, cursor: int = 0) -> str:
        r"""The capture with the TRANSPORT's line-ending translation undone.  For PARSERS.

        A pty translates ``\n`` to ``\r\n`` on output (termios ``ONLCR``), so a line-anchored
        parser reading the raw capture sees every line ending in ``\r``.  That is an artefact
        of the transport, not of the agent's output, and it silently breaks any pattern
        anchored to a line boundary.

        MEASURED, and it broke a real gate: ``decision_gate.GATE_RECORD_BLOCK`` is
        ``^```decision-gate\n``, which never matches ``` ```decision-gate\r\n ```, while
        ``FIELD_LINE`` ends in ``\s*$`` and tolerates the ``\r``.  So a standalone agent's
        ``DECISION_GATE_STATE`` line was read and its fenced record was not -- the run
        repaired twice and terminated ``DECISION_GATE_REPAIR_EXHAUSTED`` for output that was
        in fact well formed.  A parser fed this view sees what the agent wrote.

        :meth:`text` stays VERBATIM and the file on disk is never rewritten: the capture is
        the agent's own transcript and it is evidence.  This is a reading of it, not a
        replacement for it.
        """
        return self.text(cursor).replace("\r\n", "\n").replace("\r", "\n")

    def text(self, cursor: int = 0) -> str:
        r"""The whole capture from ``cursor``, decoded VERBATIM.  For evidence.

        Verbatim, including the pty's ``\r\n``.  Parsers want :meth:`transcript`; anything
        that reports what the agent actually emitted wants this.
        """
        try:
            with open(self.path, "rb") as handle:
                handle.seek(cursor)
                return handle.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    @property
    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    @property
    def truncated(self) -> bool:
        return self._truncation is not None

    @property
    def truncation(self) -> str | None:
        return self._truncation

    @property
    def dropped_bytes(self) -> int:
        """How many bytes were dropped.  A reported fact, never a silent one."""
        return self._dropped

    @property
    def writer(self) -> str:
        """Who wrote the tail of this capture, per the meta: supervisor or exit watcher."""
        return self._writer

    def integrity(self) -> dict[str, Any]:
        """Whether the META and the BYTES agree.  Finding 4 (follow-up review).

        Three facts are compared, and any disagreement is reported by name rather than
        resolved: the meta's `total_bytes` must equal the file's size, the meta's `sha256`
        must equal the digest of the file, and a file that holds bytes must have a meta at
        all.  A capture whose tail was appended outside this contract -- an exit watcher
        that wrote raw bytes past the limit, a hand edit, a torn write -- disagrees on at
        least one of them, and a completion question over it is unanswerable.
        """
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        try:
            meta = json.loads(self._meta_path.read_text())
        except OSError:
            meta = None
        except ValueError:
            return {"consistent": False, "reason": "meta_unparsable"}
        if meta is None:
            if size == 0:
                return {"consistent": True, "reason": ""}
            return {"consistent": False, "reason": "meta_missing",
                    "file_bytes": size}
        if meta.get("schema") != META_SCHEMA:
            return {"consistent": False, "reason": "meta_schema_mismatch",
                    "schema": meta.get("schema")}
        if int(meta.get("total_bytes", -1)) != size:
            return {"consistent": False, "reason": "total_bytes_mismatch",
                    "meta_bytes": int(meta.get("total_bytes", -1)), "file_bytes": size}
        recorded = str(meta.get("sha256") or "")
        actual = _digest_of(self.path).hexdigest()
        if recorded != actual:
            return {"consistent": False, "reason": "sha256_mismatch",
                    "meta_sha256": recorded, "file_sha256": actual}
        if meta.get("writer") not in (WRITER_SUPERVISOR, WRITER_EXIT_WATCHER):
            return {"consistent": False, "reason": "writer_unknown",
                    "writer": meta.get("writer")}
        return {"consistent": True, "reason": ""}

    def completion_is_answerable(self) -> dict[str, Any]:
        """Whether a completion question may be answered FROM this capture.

        R-7.  A truncated transcript cannot answer a completion question, and the honest
        result is ``LOST`` with a named reason rather than a guess taken from whatever
        survived the truncation.  Neither can a transcript whose integrity metadata
        disagrees with its bytes (finding 4): that is ``evidence_unreadable``, fail-closed.
        """
        integrity = self.integrity()
        if not integrity["consistent"]:
            return {"answerable": False, "lost_reason": CAPTURE_INTEGRITY_LOST_REASON,
                    "integrity": integrity["reason"], "truncation": self._truncation,
                    "dropped_bytes": self.dropped_bytes}
        if self._truncation is None:
            return {"answerable": True, "lost_reason": ""}
        return {"answerable": False, "lost_reason": CAPTURE_TRUNCATED_LOST_REASON,
                "truncation": self._truncation, "dropped_bytes": self.dropped_bytes}


def admit_chunk(chunk: bytes, *, records: int, total: int,
                limits: CaptureLimits) -> dict[str, Any]:
    """THE limit decision, for every writer of a capture file.

    ``{"payload": bytes | None, "record_truncation": str, "truncation": str | None,
    "dropped": int}``.  ``payload`` is what reaches the file -- ``None`` when the chunk is
    dropped whole -- ``record_truncation`` names a cut applied to THIS chunk,
    ``truncation`` the store-level cause this decision establishes (or ``None``), and
    ``dropped`` the bytes that did not reach the file, counted exactly once.

    A pure function of the counters, so the supervisor's :class:`BoundedCapture` and the
    exit watcher's raw appender (`standalone_pty._watch`, after the supervisor died) make
    the same decision for the same state.  Finding 4 of the follow-up review was exactly
    that they did not: the watcher appended verbatim, unbounded.
    """
    record_truncation = ""
    payload = chunk
    if len(payload) > limits.max_line_bytes:
        record_truncation = TRUNCATION_LINE_BYTES
        payload = payload[:limits.max_line_bytes]
    if records >= limits.max_records:
        return {"payload": None, "record_truncation": "",
                "truncation": TRUNCATION_RECORD_COUNT, "dropped": len(chunk)}
    if total + len(payload) > limits.max_total_bytes:
        return {"payload": None, "record_truncation": "",
                "truncation": TRUNCATION_TOTAL_BYTES, "dropped": len(chunk)}
    return {"payload": payload, "record_truncation": record_truncation,
            "truncation": TRUNCATION_LINE_BYTES if record_truncation else None,
            "dropped": len(chunk) - len(payload)}


class RawBoundedAppender:
    """The exit watcher's end of the capture contract.  Raw ``os`` calls only.

    Built in the FORKED watcher the moment the supervisor is proven gone, from whatever
    meta the supervisor last saved (or from nothing, for a capture nobody had opened), and
    from then on every drained chunk goes through :func:`admit_chunk` -- the same limits,
    the same truncation causes, the same drop accounting -- and every append rewrites the
    meta with the running digest under ``writer="exit_watcher"``.  A reader holds the
    result to the same integrity check as a supervisor-written capture.

    Deliberately not a :class:`BoundedCapture`: that class uses buffered file objects and
    ``pathlib``, and this runs after ``fork`` without ``exec``, where the rule is raw
    descriptor calls and nothing that could wedge on a parent's lock.
    """

    def __init__(self, capture: bytes, *, limits: CaptureLimits) -> None:
        self.capture = capture
        self.meta = capture + b".meta.json"
        self.limits = limits
        self.records = 0
        self.total = 0
        self.dropped = 0
        self.truncation = ""
        self.digest = hashlib.sha256()
        self.fd = -1
        self._adopt_meta()
        self._digest_existing()
        try:
            self.fd = os.open(capture, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError:
            self.fd = -1

    def _adopt_meta(self) -> None:
        try:
            fd = os.open(self.meta, os.O_RDONLY)
        except OSError:
            return
        try:
            raw = b""
            while True:
                chunk = os.read(fd, 65_536)
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(fd)
        try:
            meta = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return
        if not isinstance(meta, dict):
            return
        self.records = int(meta.get("records", 0) or 0)
        self.total = int(meta.get("total_bytes", 0) or 0)
        self.dropped = int(meta.get("dropped_bytes", 0) or 0)
        self.truncation = str(meta.get("truncation") or "")

    def _digest_existing(self) -> None:
        """The digest of the bytes ALREADY on disk, so the running digest continues the
        supervisor's rather than restarting.  Also the authority for `total` when the
        supervisor's meta lagged its last write (a crash between the two): the FILE is the
        authority for the bytes, and the meta this writer produces describes the file."""
        size = 0
        try:
            fd = os.open(self.capture, os.O_RDONLY)
        except OSError:
            return
        try:
            while True:
                chunk = os.read(fd, 65_536)
                if not chunk:
                    break
                self.digest.update(chunk)
                size += len(chunk)
        finally:
            os.close(fd)
        if size != self.total:
            # One supervisor append landed without its meta.  Counted as one record; the
            # limit logic below then sees the true total.
            if size > self.total:
                self.records += 1
            self.total = size

    def append(self, chunk: bytes) -> None:
        decision = admit_chunk(chunk, records=self.records, total=self.total,
                               limits=self.limits)
        self.dropped += decision["dropped"]
        payload = decision["payload"]
        if payload is None:
            self.truncation = decision["truncation"] or self.truncation
        elif decision["truncation"] and not self.truncation:
            self.truncation = decision["truncation"]
        if payload is not None and self.fd >= 0:
            written = 0
            while written < len(payload):
                try:
                    written += os.write(self.fd, payload[written:])
                except OSError:
                    break
            if written:
                try:
                    os.fsync(self.fd)
                except OSError:
                    pass
            self.total += written
            self.records += 1
            self.digest.update(payload[:written])
        self.save_meta()

    def save_meta(self) -> None:
        try:
            write_meta(self.meta, records=self.records, total_bytes=self.total,
                       dropped_bytes=self.dropped, truncation=self.truncation,
                       sha256=self.digest.hexdigest(), writer=WRITER_EXIT_WATCHER)
        except OSError:
            pass

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


def meta_path_for(path: str | os.PathLike[str]) -> Path:
    """``<capture.log>.meta.json`` -- the one meta file for one capture file."""
    target = Path(path)
    return target.with_name(target.name + ".meta.json")


def write_meta(path: str | os.PathLike[str], *, records: int, total_bytes: int,
               dropped_bytes: int, truncation: str, sha256: str, writer: str) -> None:
    """The ONE writer of a capture meta file, for both processes that may hold the pen.

    Raw ``os`` calls only (tmp + fsync + rename), because the exit watcher calls this from
    a forked child that must not run richer machinery.  `BoundedCapture._save_meta` calls
    it too, so the supervisor's meta and the watcher's meta are the same bytes for the
    same state -- which is what lets a reader hold both to one integrity check.
    """
    payload = json.dumps({"schema": META_SCHEMA, "records": int(records),
                          "total_bytes": int(total_bytes),
                          "dropped_bytes": int(dropped_bytes),
                          "truncation": truncation or "", "sha256": sha256,
                          "writer": writer}, sort_keys=True).encode()
    target = os.fsencode(os.fspath(path))
    tmp = target + b".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, target)


def _digest_of(path: Path) -> Any:
    """``sha256`` over the whole file, or over nothing when it does not exist."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65_536), b""):
                digest.update(chunk)
    except OSError:
        pass
    return digest


def capture_path(artifact_base: str | os.PathLike[str], run_id: str,
                 session_id: str) -> Path:
    """``<artifact_base>/runs/<run_id>/standalone/<session_id>/capture.log``.

    A plain file under the run root, which is what makes it readable by a stranger process
    after the creating process is gone.
    """
    return (Path(artifact_base) / "runs" / run_id / "standalone" / session_id
            / "capture.log")


def structured_lines(text: str) -> tuple[tuple[dict[str, Any] | None, str], ...]:
    """Split a captured stream into ``(parsed-JSON-or-None, raw-line)`` pairs.

    An unparsable line is kept with ``None`` -- **never dropped**.  A line that is not JSON
    is not evidence of anything, but it is still part of the transcript, and dropping it
    would make the capture disagree with the file it came from.
    """
    out: list[tuple[dict[str, Any] | None, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parsed: dict[str, Any] | None = None
        if stripped[0] in "{[":
            try:
                candidate = json.loads(stripped)
                parsed = candidate if isinstance(candidate, dict) else None
            except ValueError:
                parsed = None
        out.append((parsed, line))
    return tuple(out)


def redacted_summary(store: BoundedCapture, env_names: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A journal-safe summary.  Byte counts and limits; never the bytes."""
    return {"size": store.size, "truncated": store.truncated,
            "truncation": store.truncation, "dropped_bytes": store.dropped_bytes,
            "path": str(store.path),
            "limits": {"max_total_bytes": store.limits.max_total_bytes,
                       "max_line_bytes": store.limits.max_line_bytes,
                       "max_records": store.limits.max_records}}
