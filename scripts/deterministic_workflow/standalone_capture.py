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

#: The IRREVERSIBLE unanswerable causes a capture can record about itself (consolidated
#: follow-up review of 87f6179, findings 6 and 7).  Once a meta names one, no later
#: append, handoff or meta rewrite can clear it: the bytes a reader would reason from
#: are known to be incomplete (a write failed) or known to disagree with what an
#: earlier writer recorded (the inherited prefix failed its check), and neither fact is
#: undone by writing more.  ``integrity()`` reports the cause by name and
#: ``completion_is_answerable`` answers ``evidence_unreadable``.
UNANSWERABLE_WRITE_FAILED = "write_failed"
UNANSWERABLE_INHERITED_PREFIX = "inherited_"          # prefix + the integrity reason

#: Round-7 consolidated review, blocker 4.  The APPEND INTENT the supervisor writes
#: durably BEFORE every data append: the offset the bytes will land at, their length and
#: their digest.  It is the ONLY thing that can vouch for a suffix beyond the length the
#: meta declares -- a crash between the data write and the meta write leaves exactly such a
#: suffix, and the intent proves it is the supervisor's own bytes and not a forged or
#: stale tail.  File-length inference (`size > total` => "one in-flight append") is gone:
#: a suffix no intent describes is `unverified_tail`, irreversibly.
APPEND_INTENT_SCHEMA = "os37.capture_append_intent.v1"
#: The integrity reasons blocker 4 names.  `meta_missing` / `meta_unreadable` /
#: `total_bytes_mismatch` are the reader-side reasons (a longer-than-declared file is
#: `total_bytes_mismatch` with ``tail: unverified_tail`` when no append intent proves the
#: suffix); `unverified_tail` is the name the exit watcher inherits IRREVERSIBLY for that
#: same suffix at handoff.
INTEGRITY_META_MISSING = "meta_missing"
INTEGRITY_META_UNREADABLE = "meta_unreadable"
INTEGRITY_TOTAL_BYTES_MISMATCH = "total_bytes_mismatch"
INTEGRITY_UNVERIFIED_TAIL = "unverified_tail"


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
        self._intent_path = intent_path_for(self.path)
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
        #: The irreversible unanswerable cause this capture recorded, or "".
        self._unanswerable = ""
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
        self._unanswerable = str(meta.get("unanswerable") or "")
        self._digest = _digest_of(self.path)
        # Blocker 4: a suffix beyond the declared length is adopted into the counters
        # ONLY when the durable append intent proves it (offset, length and digest); an
        # unproven one leaves the counters describing the declared prefix, and
        # `integrity()` names it `unverified_tail`.
        verified = verified_tail(self.path, self._intent_path, declared_total=self._total)
        if verified["state"] == "verified":
            self._records += 1
            self._total = verified["size"]

    def refresh(self) -> None:
        """Re-read the meta and the file.  For a reader whose file another process --
        the exit watcher after the supervisor died -- may still be appending to."""
        self._load_meta()

    def _save_meta(self) -> None:
        write_meta(self._meta_path, records=self._records, total_bytes=self._total,
                   dropped_bytes=self._dropped, truncation=self._truncation or "",
                   sha256=self._digest.hexdigest(), writer=self._writer,
                   unanswerable=self._unanswerable)
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
        try:
            # Blocker 4: the APPEND INTENT reaches stable storage BEFORE the bytes do.
            # A crash after the data write and before the meta write then leaves a
            # suffix the intent describes exactly -- the one recoverable case -- and any
            # other suffix is provably not this writer's.
            write_append_intent(self._intent_path, offset=offset, payload=payload)
            with open(self.path, "ab") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            # Finding 6.  Bytes this store could not put on disk are bytes a reader
            # cannot reason from, and -- unlike a limit drop -- nothing about the file
            # says so.  The failure is therefore recorded IRREVERSIBLY in the meta
            # (best effort: the meta may fail for the same reason, and then the file
            # and its absent/stale meta disagree, which `integrity()` also refuses)
            # before the error propagates to the supervisor.
            self._mark_unanswerable(UNANSWERABLE_WRITE_FAILED, detail=str(exc))
            raise
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

    def raw(self, cursor: int = 0) -> bytes:
        r"""The whole capture from the RAW byte offset ``cursor``, UNDECODED.

        Delivery provenance is byte-addressable: the delivery event records a byte offset
        (``size`` at the write), so the echo must be matched and excluded on THESE bytes,
        before any UTF-8 decode or ``\r\n`` translation shifts positions (F-002 coordinate
        integrity).  ``cursor`` is a byte offset into the same space as :attr:`size`.
        """
        try:
            with open(self.path, "rb") as handle:
                handle.seek(cursor)
                return handle.read()
        except OSError:
            return b""

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

    @property
    def unanswerable(self) -> str:
        """The irreversible unanswerable cause the meta records, or ``""``."""
        return self._unanswerable

    def _mark_unanswerable(self, cause: str, *, detail: str = "") -> None:
        """Record ``cause`` in the meta, ONCE.  The first cause is kept; a later one does
        not replace it, because the first is the one that made the capture unanswerable
        and every later write happened over an already-unanswerable file."""
        if not self._unanswerable:
            self._unanswerable = cause
        try:
            # `total`/digest describe the FILE as it is, so the meta stays a true
            # description of the bytes even while it names the failure.
            self._total = self.size
            self._digest = _digest_of(self.path)
            self._save_meta()
        except OSError:
            pass
        del detail

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
        except FileNotFoundError:
            meta = None
        except OSError as exc:
            # Blocker 4: a meta that EXISTS and cannot be opened is not an absent one.
            return {"consistent": False, "reason": INTEGRITY_META_UNREADABLE,
                    "detail": f"{type(exc).__name__}: {exc}"}
        except ValueError:
            return {"consistent": False, "reason": "meta_unparsable"}
        if meta is None:
            if size == 0:
                return {"consistent": True, "reason": ""}
            return {"consistent": False, "reason": INTEGRITY_META_MISSING,
                    "file_bytes": size}
        if meta.get("schema") != META_SCHEMA:
            return {"consistent": False, "reason": "meta_schema_mismatch",
                    "schema": meta.get("schema")}
        unanswerable = str(meta.get("unanswerable") or "")
        if unanswerable:
            # Findings 6 / 7.  Irreversible by construction: whatever the bytes and the
            # counters say NOW, a writer recorded that the capture is not evidence.
            return {"consistent": False, "reason": unanswerable}
        declared = int(meta.get("total_bytes", -1))
        recorded = str(meta.get("sha256") or "")
        if declared < size and declared >= 0:
            # Blocker 4.  A suffix beyond the declared length is evidence ONLY when the
            # durable append intent describes it exactly AND the declared prefix still
            # hashes to what the meta recorded; otherwise it is `unverified_tail` -- a
            # forged or stale completion record in that region can never become
            # settlement evidence, and no later meta write can clear this (a writer that
            # adopts the file marks it irreversibly).
            verified = verified_tail(self.path, self._intent_path, declared_total=declared)
            prefix = _digest_of(self.path, limit=declared).hexdigest()
            if verified["state"] != "verified":
                return {"consistent": False, "reason": INTEGRITY_TOTAL_BYTES_MISMATCH,
                        "meta_bytes": declared, "file_bytes": size,
                        "tail": INTEGRITY_UNVERIFIED_TAIL, "detail": verified["reason"]}
            if recorded != prefix:
                return {"consistent": False, "reason": "sha256_mismatch",
                        "meta_sha256": recorded, "file_sha256": prefix}
        else:
            if declared != size:
                return {"consistent": False, "reason": INTEGRITY_TOTAL_BYTES_MISMATCH,
                        "meta_bytes": declared, "file_bytes": size}
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
        self.intent = capture + b".intent.json"
        self.limits = limits
        self.records = 0
        self.total = 0
        self.dropped = 0
        self.truncation = ""
        self.digest = hashlib.sha256()
        self.fd = -1
        #: The irreversible unanswerable cause (findings 6 / 7), inherited from the
        #: supervisor's meta or established by this writer.  Never cleared.
        self.unanswerable = ""
        self._meta_sha256 = ""
        self._meta_present = False
        self._adopt_meta()
        self._digest_existing()
        try:
            self.fd = os.open(capture, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError:
            self.fd = -1
            # Finding 6.  A capture this writer cannot open is a capture whose tail it
            # will lose; that is recorded now, not discovered by a reader later.
            self._mark_unanswerable(UNANSWERABLE_WRITE_FAILED)

    def _adopt_meta(self) -> None:
        try:
            fd = os.open(self.meta, os.O_RDONLY)
        except FileNotFoundError:
            return                                   # genuinely absent; `_digest_existing`
                                                     # decides whether that is a blank slate
        except OSError:
            # Blocker 4.  A meta that EXISTS and cannot be opened (EACCES, EIO, a
            # directory in its place) is NOT an absence: the supervisor described this
            # capture and the description is unreadable, so nothing this writer records
            # can vouch for the prefix.  Irreversible, by name.
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + INTEGRITY_META_UNREADABLE)
            self._meta_present = True
            return
        try:
            raw = b""
            while True:
                chunk = os.read(fd, 65_536)
                if not chunk:
                    break
                raw += chunk
        except OSError:
            # Blocker 4, the other unreadable shape: the meta opened but cannot be READ
            # (a directory in its place, EIO).  Same verdict as an unopenable one -- and
            # the watcher must not die of it, or the tail is lost with nothing recorded.
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + INTEGRITY_META_UNREADABLE)
            self._meta_present = True
            return
        finally:
            os.close(fd)
        try:
            meta = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            # Finding 7.  A meta that EXISTS and cannot be parsed is an inherited
            # integrity failure, not a blank slate: the supervisor described this
            # capture and the description is unreadable, so nothing this writer
            # records can vouch for the prefix.
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + "meta_unparsable")
            return
        if not isinstance(meta, dict):
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + "meta_unparsable")
            return
        self._meta_present = True
        self.records = int(meta.get("records", 0) or 0)
        self.total = int(meta.get("total_bytes", 0) or 0)
        self.dropped = int(meta.get("dropped_bytes", 0) or 0)
        self.truncation = str(meta.get("truncation") or "")
        self._meta_sha256 = str(meta.get("sha256") or "")
        inherited = str(meta.get("unanswerable") or "")
        if inherited:
            # Irreversible: the supervisor already recorded it, this writer keeps it.
            self.unanswerable = inherited
        if meta.get("schema") != META_SCHEMA:
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + "meta_schema_mismatch")

    def _digest_existing(self) -> None:
        """The digest of the bytes ALREADY on disk, so the running digest continues the
        supervisor's rather than restarting.  Also the authority for `total` when the
        supervisor's meta lagged its last write (a crash between the two): the FILE is the
        authority for the bytes, and the meta this writer produces describes the file."""
        size = 0
        prefix = hashlib.sha256()
        try:
            fd = os.open(self.capture, os.O_RDONLY)
        except OSError:
            if self._meta_present and self.total:
                # The meta describes bytes this writer cannot read: inherited failure.
                self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + "capture_unreadable")
            return
        try:
            while True:
                chunk = os.read(fd, 65_536)
                if not chunk:
                    break
                # Finding 7.  The digest of the first `total` bytes -- the PREFIX the
                # supervisor's meta describes -- is computed separately from the running
                # digest, so the inherited description can be VERIFIED before this
                # writer adopts it rather than silently re-derived from the bytes.
                if size < self.total:
                    prefix.update(chunk[:max(0, self.total - size)])
                self.digest.update(chunk)
                size += len(chunk)
        finally:
            os.close(fd)
        if not self._meta_present:
            if size > 0:
                # Blocker 4.  A capture that HOLDS BYTES and has no meta is not a blank
                # slate: somebody wrote bytes this contract never described, and the meta
                # this writer would produce next cannot vouch for them.  It used to hash
                # them and carry on, which "healed" `meta_missing` at handoff.
                self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + INTEGRITY_META_MISSING)
            self.total = size
            return
        if size < self.total:
            # The file is SHORTER than the supervisor said it was: bytes it recorded
            # are gone.  Nothing this writer appends restores them.
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX
                                    + INTEGRITY_TOTAL_BYTES_MISMATCH)
        elif self._meta_sha256 and prefix.hexdigest() != self._meta_sha256:
            # Same length, different bytes: the recorded digest does not describe the
            # prefix on disk.  This used to be recomputed and overwritten, which is
            # exactly how a `sha256_mismatch` was "healed" at handoff.
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + "sha256_mismatch")
        elif size > self.total:
            # Blocker 4.  A suffix beyond the declared length is adopted ONLY when the
            # supervisor's durable APPEND INTENT describes it exactly (offset, length and
            # digest) -- the crash-between-data-and-meta case, proven rather than
            # inferred from the file length.  Anything else in that region is
            # `unverified_tail`: irreversible, so a forged or stale completion record
            # there can never become settlement evidence, whatever this writer appends.
            verified = verified_tail(self.capture, self.intent, declared_total=self.total)
            if verified["state"] == "verified":
                self.records += 1
            else:
                self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX
                                        + INTEGRITY_UNVERIFIED_TAIL)
        if size != self.total:
            self.total = size                          # the meta describes the FILE as it is

    def _mark_unanswerable(self, cause: str) -> None:
        """Record ``cause`` ONCE, irreversibly; the first cause is the one kept."""
        if not self.unanswerable:
            self.unanswerable = cause

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
            failed = False
            while written < len(payload):
                try:
                    written += os.write(self.fd, payload[written:])
                except OSError:
                    failed = True
                    break
            if written:
                try:
                    os.fsync(self.fd)
                except OSError:
                    pass
            self.total += written
            self.records += 1
            self.digest.update(payload[:written])
            if failed or written < len(payload):
                # Finding 6.  The bytes that did not reach the file are DROPPED -- counted,
                # like every other byte this contract loses -- and the loss is a write
                # failure, which no later append undoes: irreversible, by name.
                self.dropped += len(payload) - written
                self._mark_unanswerable(UNANSWERABLE_WRITE_FAILED)
        elif payload is not None:
            # No descriptor (the open failed at construction): the whole chunk is lost.
            self.dropped += len(payload)
            self._mark_unanswerable(UNANSWERABLE_WRITE_FAILED)
        self.save_meta()

    def save_meta(self) -> None:
        try:
            write_meta(self.meta, records=self.records, total_bytes=self.total,
                       dropped_bytes=self.dropped, truncation=self.truncation,
                       sha256=self.digest.hexdigest(), writer=WRITER_EXIT_WATCHER,
                       unanswerable=self.unanswerable)
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


def intent_path_for(path: str | os.PathLike[str]) -> Path:
    """``<capture.log>.intent.json`` -- the durable APPEND INTENT (blocker 4)."""
    target = Path(path)
    return target.with_name(target.name + ".intent.json")


def write_append_intent(path: str | os.PathLike[str] | bytes, *, offset: int,
                        payload: bytes) -> None:
    """Record, durably (tmp + fsync + rename), that ``payload`` is ABOUT to be appended at
    ``offset``.  Raw ``os`` calls, like :func:`write_meta`, so either writer could call it;
    only the supervisor's :class:`BoundedCapture` does, because the exit watcher writes
    its meta after every append and never leaves a described-but-unrecorded suffix."""
    record = json.dumps({"schema": APPEND_INTENT_SCHEMA, "offset": int(offset),
                         "length": len(payload),
                         "sha256": hashlib.sha256(payload).hexdigest()},
                        sort_keys=True).encode()
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    tmp = target + b".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, record)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, target)


def _read_append_intent(path: str | os.PathLike[str] | bytes) -> dict[str, Any] | None:
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    try:
        fd = os.open(target, os.O_RDONLY)
    except OSError:
        return None
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
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("schema") != APPEND_INTENT_SCHEMA:
        return None
    return record


def verified_tail(capture: str | os.PathLike[str] | bytes,
                  intent: str | os.PathLike[str] | bytes, *,
                  declared_total: int) -> dict[str, Any]:
    """Whether the bytes of ``capture`` BEYOND ``declared_total`` are the supervisor's own
    in-flight append, PROVEN by the durable append intent (blocker 4).

    ``{"state": "verified" | "unverified" | "none", "size": <file size>, "reason": ...}``.
    ``none`` when the file is not longer than declared.  ``verified`` requires an intent
    whose ``offset`` equals the declared length and whose ``length`` and ``sha256`` equal
    those of the suffix on disk -- nothing is inferred from the file length alone.  Raw
    ``os`` calls, so the forked exit watcher and the supervisor-side reader make the same
    decision from the same files.
    """
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    try:
        size = os.stat(target).st_size
    except OSError:
        size = 0
    if size <= declared_total:
        return {"state": "none", "size": size, "reason": ""}
    record = _read_append_intent(intent)
    if record is None:
        return {"state": "unverified", "size": size, "reason": "no_append_intent"}
    if int(record.get("offset", -1)) != declared_total:
        return {"state": "unverified", "size": size, "reason": "intent_offset_mismatch"}
    if int(record.get("length", -1)) != size - declared_total:
        return {"state": "unverified", "size": size, "reason": "intent_length_mismatch"}
    digest = hashlib.sha256()
    try:
        fd = os.open(target, os.O_RDONLY)
    except OSError:
        return {"state": "unverified", "size": size, "reason": "capture_unreadable"}
    try:
        os.lseek(fd, declared_total, os.SEEK_SET)
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(fd)
    if digest.hexdigest() != str(record.get("sha256") or ""):
        return {"state": "unverified", "size": size, "reason": "intent_digest_mismatch"}
    return {"state": "verified", "size": size, "reason": ""}


def write_meta(path: str | os.PathLike[str], *, records: int, total_bytes: int,
               dropped_bytes: int, truncation: str, sha256: str, writer: str,
               unanswerable: str = "") -> None:
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
                          "writer": writer,
                          # Findings 6 / 7: the irreversible unanswerable cause, or "".
                          "unanswerable": unanswerable or ""}, sort_keys=True).encode()
    target = os.fsencode(os.fspath(path))
    tmp = target + b".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, target)


def _digest_of(path: Path, *, limit: int | None = None) -> Any:
    """``sha256`` over the whole file (or its first ``limit`` bytes), or over nothing when
    it does not exist."""
    digest = hashlib.sha256()
    remaining = limit
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65_536), b""):
                if remaining is not None:
                    chunk = chunk[:max(0, remaining)]
                    remaining -= len(chunk)
                digest.update(chunk)
                if remaining is not None and remaining <= 0:
                    break
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


def protocol_lines(text: str) -> list[str]:
    r"""Split a line-delimited PROTOCOL stream on its delimiter, ``\n``, and nothing else.

    Round-8 item 5.  Every structured stream this runtime reads -- the CLIs' NDJSON event
    streams, the execution journal, the migration and upgrade audit logs -- is delimited by
    ``\n``, and JSON permits U+2028 / U+2029 / U+0085 RAW inside a string (the journal
    is written with ``ensure_ascii=False``, and an agent's own output carries whatever the
    agent wrote).  ``str.splitlines()`` splits on all of those (and on ``\x0b`` / ``\x0c``
    / ``\x1c``-``\x1e``), so a perfectly valid record whose string held one of them was
    cut in two: the driver saw two unparsable fragments instead of the settlement record,
    and the journal reader raised ``JournalUnreadable`` over a record it had itself
    written.  Splitting ONLY on the protocol delimiter keeps such a record whole.

    ``\r\n``: a pty in canonical mode translates ``\n`` to ``\r\n`` on output.  The
    capture's :meth:`BoundedCapture.transcript` already undoes that for parsers; a caller
    handing raw ``\r\n`` text here gets lines ending in ``\r``, which every JSON reader
    below strips before parsing -- so ``\r\n`` delimits exactly like ``\n``.  A trailing
    delimiter yields no empty last piece (the same shape ``splitlines`` gave).
    """
    pieces = text.split("\n")
    if pieces and pieces[-1] == "":
        pieces.pop()
    return pieces


def structured_lines(text: str) -> tuple[tuple[dict[str, Any] | None, str], ...]:
    """Split a captured stream into ``(parsed-JSON-or-None, raw-line)`` pairs.

    An unparsable line is kept with ``None`` -- **never dropped**.  A line that is not JSON
    is not evidence of anything, but it is still part of the transcript, and dropping it
    would make the capture disagree with the file it came from.  Split on the protocol
    delimiter only (:func:`protocol_lines`), never on Unicode line separators inside a
    record.
    """
    out: list[tuple[dict[str, Any] | None, str]] = []
    for line in protocol_lines(text):
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
