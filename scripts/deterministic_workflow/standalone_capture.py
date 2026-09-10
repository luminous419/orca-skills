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
        self._load_meta()

    # -- meta ----------------------------------------------------------------------------
    def _load_meta(self) -> None:
        try:
            meta = json.loads(self._meta_path.read_text())
        except (OSError, ValueError):
            # Rebuild from the log itself, which is the authority for the bytes.
            try:
                self._total = self.path.stat().st_size
            except OSError:
                self._total = 0
            self._records = 0
            return
        self._records = int(meta.get("records", 0))
        self._total = int(meta.get("total_bytes", 0))
        self._dropped = int(meta.get("dropped_bytes", 0))
        truncation = meta.get("truncation")
        self._truncation = truncation if isinstance(truncation, str) and truncation else None

    def _save_meta(self) -> None:
        payload = {"records": self._records, "total_bytes": self._total,
                   "dropped_bytes": self._dropped, "truncation": self._truncation or ""}
        tmp = self._meta_path.with_name(self._meta_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True))
        os.replace(str(tmp), str(self._meta_path))

    # -- append --------------------------------------------------------------------------
    def append(self, chunk: bytes, *, at: str) -> CaptureRecord | None:
        """Append ``chunk``, honouring every limit.  Returns the record, or ``None``.

        ``None`` means the limit was already reached and this chunk was DROPPED -- counted
        in ``dropped_bytes``, never silently discarded and never wrapped over the beginning.
        """
        if not isinstance(chunk, bytes):
            raise TypeError("capture appends bytes; a str would already have lost encoding")
        record_truncation = ""
        payload = chunk
        if len(payload) > self.limits.max_line_bytes:
            # Only THIS record is stamped; the store as a whole is not truncated by one
            # over-long line, so a later completion question is still answerable.
            record_truncation = TRUNCATION_LINE_BYTES
            self._dropped += len(payload) - self.limits.max_line_bytes
            payload = payload[:self.limits.max_line_bytes]
        if self._records >= self.limits.max_records:
            self._truncation = TRUNCATION_RECORD_COUNT
            self._dropped += len(chunk)
            self._save_meta()
            return None
        if self._total + len(payload) > self.limits.max_total_bytes:
            self._truncation = TRUNCATION_TOTAL_BYTES
            self._dropped += len(chunk)
            self._save_meta()
            return None
        offset = self._total
        with open(self.path, "ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self._total += len(payload)
        self._records += 1
        if record_truncation:
            self._truncation = self._truncation or None
        self._save_meta()
        return {"offset": offset, "at": at,
                "data": payload.decode("utf-8", errors="replace"),
                "raw_len": len(chunk), "truncation": record_truncation, "stream": "pty"}

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

    def completion_is_answerable(self) -> dict[str, Any]:
        """Whether a completion question may be answered FROM this capture.

        R-7.  A truncated transcript cannot answer a completion question, and the honest
        result is ``LOST`` with a named reason rather than a guess taken from whatever
        survived the truncation.
        """
        if self._truncation is None:
            return {"answerable": True, "lost_reason": ""}
        return {"answerable": False, "lost_reason": CAPTURE_TRUNCATED_LOST_REASON,
                "truncation": self._truncation, "dropped_bytes": self.dropped_bytes}


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
