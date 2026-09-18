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
import re
from collections.abc import Mapping, Sequence
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
#: Round-9 consolidated review, item 6.  The CLOSED v2 meta shape every writer produces
#: (:func:`write_meta`) and the ONLY shape the exit watcher may inherit at handoff: exactly
#: these keys, these types, a 64-hex-digit lowercase ``sha256``.  A meta that is not this
#: shape -- absent / empty / malformed digest, a stray or missing key, a wrong type -- is
#: `meta_invalid`, inherited IRREVERSIBLY: the watcher never re-derives the digest from
#: the bytes and never writes a healed one back (it used to accept an empty ``sha256``,
#: skip the prefix check and persist a freshly computed hash, so a capture that was
#: unanswerable before the handoff answered after it).
META_KEYS = frozenset({"schema", "records", "total_bytes", "dropped_bytes", "truncation",
                       "sha256", "writer", "unanswerable"})
INTEGRITY_META_INVALID = "meta_invalid"
TRUNCATION_CAUSES = ("", TRUNCATION_TOTAL_BYTES, TRUNCATION_LINE_BYTES,
                     TRUNCATION_RECORD_COUNT)

#: OS-48.  The LEGACY capture-finalized proof (`os37.capture_finalized.v1`) is no longer evidence
#: of anything: it rested on a negative whole-process-table scan (ANALYSIS F0).  A reader that
#: finds ONLY such a record answers `legacy_finalized_record` by name (DESIGN §5); nothing is
#: upgraded in place.  The OS-48 positive proof is the CAPTURE FENCE below.
LEGACY_FINALIZED_SCHEMA = "os37.capture_finalized.v1"

#: OS-48 DESIGN §1 -- the CAPTURE FENCE (`os48.capture_fence.v1`): written ONLY by the claimed
#: finalizing owner, tmp+fsync+link (exclusive) + dir fsync, after the owner (the exit watcher,
#: which holds one slave fd for the life of the dispatch) reaped the pinned agent incarnation and
#: wrote the FENCE MARKER into its own slave fd.  The marker's offset in `capture.log` is the
#: settlement boundary N: every byte any subtree member wrote before the root's exit is in
#: `[0, N)` (the pty's single output FIFO -- DESIGN probe_d1, macOS + Linux), and nothing written
#: after the marker can be.  The fence binds N, sha256(capture[0:N)), the emitter identity, the exit
#: evidence and the owner generation -- positive facts about fixed objects, never a scan.
CAPTURE_FENCE_SCHEMA = "os48.capture_fence.v1"
#: DESIGN §1.8 -- the RELEASE RECORD (`os48.release_boundary.v1`): the diagnostic retention
#: boundary R (the RELEASE marker's offset), a SEPARATE durable record written by the custodian
#: after the fence, joined to the fence by incarnation + fence-file sha256.
RELEASE_BOUNDARY_SCHEMA = "os48.release_boundary.v1"
#: DESIGN §2.5 -- finalizer OWNER GENERATIONS (`os48.finalizer_owner.v1`, `owner.<inc>.g<n>`) and a
#: live owner's durable RELINQUISHMENT (`os48.relinquish.v1`, `relinquish.<inc>.g<n>`).
FINALIZER_OWNER_SCHEMA = "os48.finalizer_owner.v1"
RELINQUISH_SCHEMA = "os48.relinquish.v1"

#: DESIGN §1.7 -- the closed evidence-state vocabulary.  Only FINAL may authorise a settlement; a
#: negative enumeration can never produce it (it lands in UNKNOWN).
EVIDENCE_PRESENT = "present"
EVIDENCE_FINAL = "final"
EVIDENCE_UNREADABLE = "unreadable"
EVIDENCE_INCONSISTENT = "inconsistent"
EVIDENCE_UNKNOWN = "unknown"
EVIDENCE_STATES = frozenset({EVIDENCE_PRESENT, EVIDENCE_FINAL, EVIDENCE_UNREADABLE,
                             EVIDENCE_INCONSISTENT, EVIDENCE_UNKNOWN})

#: DESIGN §6 -- named non-success outcomes produced by the fence / ownership machinery.  Every one
#: is a member of `standalone_lifecycle.LOST_REASONS` (LOST) or a FAILED verdict reason, or a
#: DIAGNOSTIC (journal-only) name; none can become COMPLETED.
OUTCOME_BOUNDARY_UNPROVEN = "boundary_unproven"
OUTCOME_FENCE_MISSING = "fence_missing"
OUTCOME_FENCE_MISMATCH = "fence_mismatch"
OUTCOME_FENCE_FOREIGN = "fence_foreign"
OUTCOME_LEGACY_FINALIZED = "legacy_finalized_record"
OUTCOME_OWNER_CONFLICT = "owner_conflict"
OUTCOME_FINALIZER_ALIVE = "finalizer_alive"
OUTCOME_FENCE_PUBLISHED_NO_CLAIM = "fence_published_no_claim"
OUTCOME_SUCCESSION_UNWITNESSED = "succession_unwitnessed"
OUTCOME_PROVENANCE_AMBIGUOUS = "provenance_ambiguous"
OUTCOME_RECORD_FRAMING_AMBIGUOUS = "record_framing_ambiguous"   # F-015: a completion-shaped object inside an unparsable line of [baseline, N)
OUTCOME_RECORD_SCAN_INCOMPLETE = "record_scan_incomplete"       # F-015 (i8): the framing scan of [baseline, N) hit its bound -- R1/R2 undecidable
OUTCOME_PROVENANCE_UNBOUND = "provenance_unbound"
OUTCOME_REFUSAL_IN_BOUNDARY = "refusal_in_boundary"
OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED = "diagnostic_tail_unaccounted"
OUTCOME_RELEASE_RECORD_MISSING = "release_record_missing"
OUTCOME_MEMBERSHIP_UNREADABLE = "membership_unreadable"   # the positive set is UNKNOWN (ledger unreadable / incomplete)
OUTCOME_DESCENDANTS_UNKNOWN = "descendants_unknown"       # F-009: discovery could not be read; the set beyond the positive members is UNKNOWN

#: The two finalizing owner roles the fence admits -- the supervisor (holds the master) and the
#: exit watcher (holds the OWNER SLAVE REFERENCE and writes the marker) -- plus the successor,
#: which holds neither and may publish only when the marker is ALREADY in the capture.
OWNER_SUPERVISOR = "supervisor"
OWNER_EXIT_WATCHER = "exit_watcher"
OWNER_SUCCESSOR = "successor"
#: Kept for readers of legacy journal rows (`writer=` values of os37 records).
WRITER_SUPERVISOR = OWNER_SUPERVISOR
WRITER_EXIT_WATCHER = OWNER_EXIT_WATCHER


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
        if not isinstance(meta, dict):
            meta = {}

        def _count(name: str) -> int:
            value = meta.get(name, 0)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0
        # Counters are adopted defensively; whether the meta is the CLOSED shape is
        # `integrity`'s question (item 6), answered by name, never by a crash here.
        self._records = _count("records")
        self._total = _count("total_bytes")
        self._dropped = _count("dropped_bytes")
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

    @staticmethod
    def transcript_of(raw: bytes) -> str:
        r"""The :meth:`transcript` reading of an arbitrary byte interval of the capture (OS-48:
        the authoritative ``[baseline, N)`` slice a settlement is allowed to parse)."""
        return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

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
    def records(self) -> int:
        """How many records the store has appended (per the meta / the verified tail)."""
        return self._records

    @property
    def sha256(self) -> str:
        """The digest of the FILE AS IT IS (recomputed on load, continued by every append
        this store made) -- the value the capture-finalized proof binds (item 1)."""
        return self._digest.hexdigest()

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
        if not isinstance(meta, dict) or meta.get("schema") != META_SCHEMA:
            return {"consistent": False, "reason": "meta_schema_mismatch",
                    "schema": meta.get("schema") if isinstance(meta, dict) else None}
        unanswerable = str(meta.get("unanswerable") or "")
        if not unanswerable:
            # Item 6 (round 9): the reader holds the meta to the SAME closed shape the
            # exit watcher requires at handoff, so an absent / empty / malformed digest is
            # refused by name here too -- never compared, never treated as "no digest".
            problem = _meta_shape_problem(meta)
            if problem:
                return {"consistent": False, "reason": INTEGRITY_META_INVALID,
                        "detail": problem}
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
        #: Item 6: when the inherited meta failed its shape / prefix check, the ``sha256``
        #: string it carried is preserved VERBATIM in everything this writer saves -- a
        #: healed digest is exactly what must never be written back.  ``None`` means the
        #: inherited description was verified and this writer's running digest continues
        #: it.
        self._inherited_sha256: str | None = None
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
        inherited = meta.get("unanswerable")
        if isinstance(inherited, str) and inherited:
            # Irreversible: the supervisor already recorded it, this writer keeps it.
            self.unanswerable = inherited
        # ---- item 6 (round 9): the CLOSED v2 shape, or an inherited failure -----------
        # Nothing below is coerced.  A meta that names another schema, lacks a key,
        # carries a stray one, holds a wrong type or an invalid digest is not this
        # contract's description of the prefix, and the description is what this writer
        # would otherwise VOUCH for by continuing its digest.  The counters it does adopt
        # then describe the FILE as it is (`_digest_existing`), the inherited ``sha256``
        # string is kept VERBATIM in every meta this writer saves (never replaced by a
        # digest it computed itself), and the capture stays unanswerable for good.
        problem = _meta_shape_problem(meta)
        if problem:
            self._meta_sha256 = meta.get("sha256") if isinstance(meta.get("sha256"), str) else ""
            self._inherited_sha256 = self._meta_sha256
            self._mark_unanswerable(UNANSWERABLE_INHERITED_PREFIX + INTEGRITY_META_INVALID
                                    + ":" + problem)
            self.records = meta.get("records") if isinstance(meta.get("records"), int) else 0
            self.total = (meta.get("total_bytes")
                          if isinstance(meta.get("total_bytes"), int) else 0)
            self.dropped = (meta.get("dropped_bytes")
                            if isinstance(meta.get("dropped_bytes"), int) else 0)
            self.truncation = (meta.get("truncation")
                               if isinstance(meta.get("truncation"), str) else "")
            return
        self.records = int(meta["records"])
        self.total = int(meta["total_bytes"])
        self.dropped = int(meta["dropped_bytes"])
        self.truncation = str(meta["truncation"])
        self._meta_sha256 = str(meta["sha256"])

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
        elif self.unanswerable.startswith(UNANSWERABLE_INHERITED_PREFIX + INTEGRITY_META_INVALID):
            pass                                          # item 6: already refused by shape
        elif prefix.hexdigest() != self._meta_sha256:
            # Same length, different bytes: the recorded digest does not describe the
            # prefix on disk.  This used to be recomputed and overwritten, which is
            # exactly how a `sha256_mismatch` was "healed" at handoff.  (Item 6: an EMPTY
            # recorded digest no longer skips this branch -- the shape check above has
            # already refused it -- and the inherited string is what gets written back.)
            self._inherited_sha256 = self._meta_sha256
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
            # Round-8 iteration 2: the SAME intent-before-bytes discipline the supervisor's
            # `BoundedCapture.append` follows.  A stranger collecting this dispatch reads
            # the file between this writer's bytes and its meta; without an intent that
            # in-flight suffix was `unverified_tail` -- indistinguishable from a forged
            # one -- so an answerability-first gate could refuse a capture that was merely
            # not yet fully visible.  With the intent on stable storage first, the
            # in-flight suffix is `verified` and the gate refuses only a real integrity
            # failure.  An intent this writer cannot record makes the chunk a write
            # failure (irreversible, by name), never an undescribed suffix.
            try:
                write_append_intent(self.intent, offset=self.total, payload=payload)
            except OSError:
                self.dropped += len(payload)
                self._mark_unanswerable(UNANSWERABLE_WRITE_FAILED)
                self.save_meta()
                return
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
                       # Item 6: an inherited description that failed its check is
                       # written back AS INHERITED, never as a digest this writer derived.
                       sha256=(self._inherited_sha256 if self._inherited_sha256 is not None
                               else self.digest.hexdigest()),
                       writer=WRITER_EXIT_WATCHER, unanswerable=self.unanswerable)
        except OSError:
            pass

    def close(self) -> None:
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


def _meta_shape_problem(meta: Any) -> str:
    """``""`` when ``meta`` is EXACTLY the closed v2 shape :func:`write_meta` produces;
    otherwise the first problem, named (item 6, round 9).  Shared by the reader side
    (:meth:`BoundedCapture.integrity`) and the exit watcher's handoff
    (:class:`RawBoundedAppender`), so both refuse the same descriptions."""
    if not isinstance(meta, dict):
        return "not_an_object"
    if meta.get("schema") != META_SCHEMA:
        return "schema"
    keys = set(meta)
    if keys != META_KEYS:
        missing = sorted(META_KEYS - keys)
        extra = sorted(keys - META_KEYS)
        return "keys:" + ",".join(["-" + k for k in missing] + ["+" + k for k in extra])
    for name in ("records", "total_bytes", "dropped_bytes"):
        value = meta[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return name
    if meta["truncation"] not in TRUNCATION_CAUSES:
        return "truncation"
    if meta["writer"] not in (WRITER_SUPERVISOR, WRITER_EXIT_WATCHER):
        return "writer"
    if not isinstance(meta["unanswerable"], str):
        return "unanswerable"
    digest = meta["sha256"]
    if (not isinstance(digest, str) or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)):
        return "sha256"
    return ""


def capture_finalized_path(capture: str | os.PathLike[str] | bytes,
                           incarnation: str) -> bytes:
    """``<capture.log>.finalized.<incarnation>.json`` -- the LEGACY os37 record's path, kept only
    so a reader can detect (and refuse by name) a run finalized by the superseded protocol."""
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    return target + b".finalized." + incarnation.encode() + b".json"


# ---- OS-48 fence markers (DESIGN §1.1 / §1.8) -- [FORKED-SAFE] -------------------------------
_NONCE_RE = re.compile(r"[0-9a-f]{32}")


def marker_bytes(nonce: str) -> bytes:
    """[FORKED-SAFE] The in-band FENCE marker the owner writes into ITS OWN slave fd after the
    pinned agent incarnation is reaped.  The pty output queue is a single FIFO, so every byte
    written to any slave descriptor before this call is delivered to the master before it."""
    if not _NONCE_RE.fullmatch(nonce or ""):
        raise ValueError("fence nonce must be 32 lowercase hex chars")
    return b"\n<<OS48-FENCE " + nonce.encode() + b">>\n"


def release_marker_bytes(nonce: str) -> bytes:
    """[FORKED-SAFE] The RELEASE marker (DESIGN §1.8), written on release-1; the owner closes its
    slave reference only on release-2, after the custodian consumed up to it."""
    if not _NONCE_RE.fullmatch(nonce or ""):
        raise ValueError("fence nonce must be 32 lowercase hex chars")
    return b"\n<<OS48-RELEASE " + nonce.encode() + b">>\n"


_FENCE_RE_TEMPLATE = rb"\r?\n<<OS48-FENCE %s>>\r?\n"
_RELEASE_RE_TEMPLATE = rb"\r?\n<<OS48-RELEASE %s>>\r?\n"


def _find_once(pattern: bytes, capture: bytes, after: int) -> tuple[int, int, str]:
    hits = list(re.finditer(pattern, capture[after:]))
    if not hits:
        return -1, 0, EVIDENCE_UNKNOWN
    if len(hits) > 1:
        return -1, 0, EVIDENCE_INCONSISTENT
    return after + hits[0].start(), hits[0].end() - hits[0].start(), EVIDENCE_FINAL


def find_marker(capture: bytes, nonce: str) -> tuple[int, str]:
    """[FORKED-SAFE] ``(N, "final")`` when the fence marker for ``nonce`` occurs EXACTLY once;
    ``(-1, "unknown")`` when absent (`boundary_unproven`); ``(-1, "inconsistent")`` when it occurs
    more than once (a duplicated marker is never evidence).  Tolerant of ONLCR (``\\r\\n``)."""
    marker_bytes(nonce)
    offset, _length, state = _find_once(_FENCE_RE_TEMPLATE % nonce.encode(), capture, 0)
    return offset, state


def marker_span(capture: bytes, nonce: str) -> tuple[int, int, str]:
    """Like :func:`find_marker` but also returns the matched marker length (ONLCR may widen it)."""
    marker_bytes(nonce)
    return _find_once(_FENCE_RE_TEMPLATE % nonce.encode(), capture, 0)


def find_release_marker(capture: bytes, nonce: str, *, after: int) -> tuple[int, int, str]:
    """[FORKED-SAFE] ``(R, length, state)`` for the RELEASE marker searched only AFTER the fence."""
    release_marker_bytes(nonce)
    return _find_once(_RELEASE_RE_TEMPLATE % nonce.encode(), capture, max(0, after))


def prefix_digest(capture: bytes, offset_n: int) -> str:
    # a memoryview: no second copy of the prefix (iteration 3: under address-space pressure
    # the slice copy was the next allocation to fail after the parser was made total)
    return hashlib.sha256(memoryview(capture)[:max(0, int(offset_n))]).hexdigest()


def file_prefix_digest(path: str | os.PathLike[str] | bytes, offset_n: int) -> str:
    """[FORKED-SAFE] sha256 of the first ``offset_n`` bytes of a file, read with raw ``os``."""
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    digest = hashlib.sha256()
    fd = os.open(target, os.O_RDONLY)
    try:
        remaining = int(offset_n)
        while remaining > 0:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _file_size(path: bytes) -> int:
    try:
        return int(os.stat(path).st_size)
    except OSError:
        return -1


# ---- durable, exclusive record publication -- [FORKED-SAFE] ---------------------------------
#: Test-only seam at the tmp-fsynced -> link boundary of :func:`publish_exclusive`
#: (REVIEW_IMPLEMENTATION_iteration2 F-007).  Production never sets it.
_LINK_HOOK: Any = None


def publish_exclusive(path: str | os.PathLike[str] | bytes, payload: bytes) -> bool:
    """[FORKED-SAFE] Write ``payload`` to a private tmp (fsync), then ``os.link(tmp, path)`` --
    atomic and EXCLUSIVE: exactly one publisher of a given path wins, a torn record cannot exist
    (a tmp is never authoritative), and the directory is fsynced after a win.  ``True`` when this
    call published, ``False`` when the path already existed (the caller reads the winner)."""
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    tmp = target + b".tmp." + str(os.getpid()).encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    if _LINK_HOOK is not None:
        # The RC2 crash boundary (DESIGN §1.7): the tmp is fsynced and the target is not yet
        # linked.  Test-only seam (`None` in production); a lock pauses / kills HERE.
        _LINK_HOOK(tmp, target)
    try:
        os.link(tmp, target)
        won = True
    except FileExistsError:
        won = False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if won:
        try:
            dir_fd = os.open(os.path.dirname(target) or b".", os.O_RDONLY)
        except OSError:
            return True
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
    return won


def _read_json_record(path: bytes, schema: str) -> dict[str, Any]:
    """``{"outcome": "present"|"absent"|"unreadable", "record": dict|None, "detail": str}``."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return {"outcome": "absent", "record": None, "detail": ""}
    except OSError as exc:
        return {"outcome": "unreadable", "record": None, "detail": str(exc)}
    try:
        raw = b""
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            raw += chunk
    except OSError as exc:
        return {"outcome": "unreadable", "record": None, "detail": str(exc)}
    finally:
        os.close(fd)
    try:
        record = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return {"outcome": "unreadable", "record": None, "detail": "malformed record"}
    if not isinstance(record, dict) or record.get("schema") != schema:
        return {"outcome": "unreadable", "record": None,
                "detail": f"not a {schema} record"}
    return {"outcome": "present", "record": record, "detail": ""}


# ---- the capture fence (DESIGN §1.5) -----------------------------------------------------------
def capture_fence_path(capture: str | os.PathLike[str] | bytes, incarnation: str) -> bytes:
    """``<capture.log>.fence.<incarnation>.json``."""
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    return target + b".fence." + incarnation.encode() + b".json"


# ---- OS-48 (REVIEW_IMPLEMENTATION_iteration2 F-001): the sidecar snapshot AT the boundary -------
SIDECAR_SNAPSHOT_SCHEMA = "os48.sidecar_snapshot.v1"
SIDECAR_STATE_PRESENT = "present"          # the declared file existed at the owner's reap-step read
SIDECAR_STATE_ABSENT = "absent"            # ENOENT at that read: a POSITIVE absence
SIDECAR_STATE_UNREADABLE = "sidecar_unreadable"  # denied / EIO / short at that read: NOT absence (F-008)
SIDECAR_STATE_UNPROVEN = "sidecar_unproven"  # no snapshot record at all: the presence fact is unproven
SIDECAR_STATE_NONE = "none_declared"       # the profile declares no sidecar
#: Where the owner's read sits relative to the marker (F-001 (b)): the read is taken in the
#: reap step, after `waitpid` and BEFORE the marker is emitted.  It is recorded in the fence
#: as a fact about the read, never as a claim that file content is bound to the stream offset N
#: -- the design defines ONE boundary (the marker's stream offset) and no auxiliary-content
#: boundary, so sidecar CONTENT is never a settlement body source (`sidecar_unproven`); the
#: snapshot serves R3's presence fact only, immutably.
SIDECAR_INSTANT_REAP_STEP = "reap_step_before_marker"


def sidecar_snapshot_path(capture: str | os.PathLike[str] | bytes, incarnation: str) -> bytes:
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    return target + b".sidecar." + incarnation.encode() + b".json"


def sidecar_bytes_path(capture: str | os.PathLike[str] | bytes, incarnation: str) -> bytes:
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    return target + b".sidecar." + incarnation.encode() + b".bytes"


def snapshot_sidecar(capture: bytes, incarnation: str, *, fence: str, sidecar_path: str,
                     captured_at: str) -> dict[str, Any]:
    """[FORKED-SAFE] Read + digest the DECLARED sidecar file and publish (link-exclusive) both
    the bytes and the record -- called by the exit watcher in its reap step (instant
    `reap_step_before_marker`).  What this fixes is the sidecar's PRESENCE FACT for R3
    (`present` / `absent` / `sidecar_unreadable`), pinned in the fence so no later creation,
    removal or resize can move a verdict.  It does NOT bind the file's CONTENT to the stream
    boundary N: a helper may still write between this read and the marker, which is why the
    content is never a settlement body source (REVIEW_IMPLEMENTATION_iteration3 F-001, option
    ii -- `result_body(allow_path=False)`, `sidecar_refused = sidecar_unproven`).  A record
    that could not be published is reported as `sidecar_unproven` by every later reader."""
    record: dict[str, Any] = {"schema": SIDECAR_SNAPSHOT_SCHEMA, "fence": fence,
                              "path": sidecar_path, "captured_at": captured_at,
                              "instant": SIDECAR_INSTANT_REAP_STEP,
                              "state": SIDECAR_STATE_UNPROVEN, "sha256": None, "bytes": 0, "error": ""}
    raw: bytes | None = None
    try:
        fd = os.open(sidecar_path, os.O_RDONLY)
        try:
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(fd)
    except FileNotFoundError:
        record["state"] = SIDECAR_STATE_ABSENT               # ENOENT: positive absence
    except OSError as exc:
        # REVIEW_IMPLEMENTATION_iteration3 F-008: EACCES / EIO / any other failure is NOT
        # absence -- the presence fact is unreadable, named, and can approve nothing.
        record["state"] = SIDECAR_STATE_UNREADABLE
        record["error"] = f"{type(exc).__name__}:{getattr(exc, 'errno', '')}"
    if raw is not None:
        record.update({"state": SIDECAR_STATE_PRESENT, "sha256": hashlib.sha256(raw).hexdigest(),
                       "bytes": len(raw)})
        publish_exclusive(sidecar_bytes_path(capture, incarnation), raw)
    publish_exclusive(sidecar_snapshot_path(capture, incarnation),
                      json.dumps(record, sort_keys=True).encode())
    return record


def read_sidecar_snapshot(capture: str | os.PathLike[str] | bytes, incarnation: str, *,
                          fence: str) -> dict[str, Any]:
    """``{"state": present|absent|sidecar_unproven, "record", "raw"}`` -- the bytes are returned
    ONLY when they re-digest to the record (a torn / substituted bytes file is `sidecar_unproven`)."""
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    got = _read_json_record(sidecar_snapshot_path(target, incarnation), SIDECAR_SNAPSHOT_SCHEMA)
    if got["outcome"] != "present" or got["record"].get("fence") != fence:
        return {"state": SIDECAR_STATE_UNPROVEN, "record": None, "raw": None}
    record = got["record"]
    if record.get("state") in (SIDECAR_STATE_ABSENT, SIDECAR_STATE_UNREADABLE, SIDECAR_STATE_UNPROVEN):
        return {"state": record.get("state"), "record": record, "raw": None}
    try:
        with open(sidecar_bytes_path(target, incarnation), "rb") as handle:
            raw = handle.read()
    except OSError:
        return {"state": SIDECAR_STATE_UNPROVEN, "record": record, "raw": None}
    if hashlib.sha256(raw).hexdigest() != record.get("sha256") or len(raw) != int(record.get("bytes") or -1):
        return {"state": SIDECAR_STATE_UNPROVEN, "record": record, "raw": None}
    return {"state": SIDECAR_STATE_PRESENT, "record": record, "raw": raw}


def make_capture_fence(*, fence: str, emitter: Mapping[str, Any], emitter_pgid: int,
                       offset_n: int, marker_len: int, marker_nonce: str, sha256_prefix: str,
                       tail_bytes_at_publish: int, exit_how: str, exit_code: int | None,
                       reaped_by: Mapping[str, Any] | None, owner: Mapping[str, Any],
                       evidence_source: str, provenance: Sequence[str],
                       published_at: str, sidecar: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The fence record.  ``sidecar`` (OS-48 F-001, iterations 3-4) is the watcher's snapshot of
    the declared ``-o`` file taken in its reap step (instant `reap_step_before_marker`) --
    ``{"state", "path", "sha256", "bytes", "instant", "error"}`` with state `present` /
    `absent` / `sidecar_unreadable` / `sidecar_unproven` -- the immutable PRESENCE fact for R3;
    it never binds the file's content to N and is never a settlement body source; ``None``
    when the profile declares no sidecar.  A state first sampled after N is never a proof and
    is never recorded here."""
    return {"schema": CAPTURE_FENCE_SCHEMA, "fence": fence, "sidecar": dict(sidecar) if sidecar else None,
            "emitter": dict(emitter), "emitter_pgid": int(emitter_pgid),
            "boundary": {"offset_n": int(offset_n), "marker_offset": int(offset_n),
                         "marker_len": int(marker_len), "marker_nonce": marker_nonce,
                         "sha256_prefix": sha256_prefix,
                         "tail_bytes_at_publish": int(tail_bytes_at_publish)},
            "exit": {"how": exit_how, "code": exit_code,
                     "reaped_by": dict(reaped_by) if reaped_by else None},
            "owner": dict(owner), "evidence_source": evidence_source,
            "published_at": published_at, "provenance": list(provenance)}


def write_capture_fence(path: str | os.PathLike[str] | bytes, record: Mapping[str, Any]) -> bool:
    """[FORKED-SAFE] Publish the fence exclusively.  ``False`` = a fence already exists (the
    caller reads and verifies it; it never overwrites -- I-6)."""
    if record.get("schema") != CAPTURE_FENCE_SCHEMA:
        raise ValueError("not a capture fence record")
    return publish_exclusive(path, json.dumps(record, sort_keys=True).encode())


def read_capture_fence(path: str | os.PathLike[str] | bytes, *, fence: str,
                       legacy_path: str | os.PathLike[str] | bytes | None = None) -> dict[str, Any]:
    """``{"outcome": "final"|"absent"|"foreign"|"unreadable"|"legacy_finalized_record", "record"}``.
    A fence of another incarnation is ``foreign`` and its contents are NOT returned.  When no
    fence exists but a legacy os37 finalized record does, the outcome is the NAMED refusal
    ``legacy_finalized_record`` -- never finality (DESIGN §5)."""
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    got = _read_json_record(target, CAPTURE_FENCE_SCHEMA)
    if got["outcome"] == "absent":
        if legacy_path is not None:
            legacy = os.fsencode(os.fspath(legacy_path)) if not isinstance(legacy_path, bytes) else legacy_path
            if _file_size(legacy) >= 0:
                return {"outcome": OUTCOME_LEGACY_FINALIZED, "record": None,
                        "detail": "an os37.capture_finalized.v1 record exists and is not evidence"}
        return {"outcome": "absent", "record": None, "detail": ""}
    if got["outcome"] == "unreadable":
        return got
    record = got["record"]
    if record.get("fence") != fence:
        return {"outcome": "foreign", "record": None,
                "detail": f"fence {record.get('fence')!r} is not {fence!r}"}
    return {"outcome": EVIDENCE_FINAL, "record": record, "detail": ""}


def _identity_complete(candidate: Any) -> bool:
    """[FORKED-SAFE] pid > 0, start_id > 0, non-empty boot id (mirrors
    `standalone_identity.identity_complete`; duplicated so this module stays import-free)."""
    if not isinstance(candidate, Mapping):
        return False
    try:
        return (int(candidate.get("pid") or 0) > 0 and int(candidate.get("start_id") or 0) > 0
                and bool(str(candidate.get("boot_id") or "")))
    except (TypeError, ValueError):
        return False


def fence_matches(record: Mapping[str, Any], *, capture: str | os.PathLike[str] | bytes,
                  sentinel_code: int | None, sentinel_present: bool) -> dict[str, Any]:
    """Bind a fence to the CAPTURE ON DISK and to the exit sentinel: the file must hold at
    least N bytes, sha256(file[0:N)) must equal the fence's digest, and a fence citing the
    sentinel must agree with it.  ``{"matches": bool, "reason": str}``."""
    boundary = record.get("boundary") if isinstance(record.get("boundary"), dict) else {}
    offset_n = boundary.get("offset_n")
    digest = boundary.get("sha256_prefix")
    # REVIEW_IMPLEMENTATION F-005: the REQUIRED typed identities the fence joins -- the
    # emitter, the finalizing owner and (when cited) the reaper -- must be positive and
    # complete (pid > 0, start_id > 0, boot id); a fence carrying an unreadable identity is
    # `identity_unreadable`, never a match.
    for axis, candidate in (("emitter", record.get("emitter")),
                            ("owner", (record.get("owner") or {}).get("owner")
                             if isinstance(record.get("owner"), dict) else None),
                            ("reaped_by", (record.get("exit") or {}).get("reaped_by")
                             if isinstance(record.get("exit"), dict) else None)):
        if axis == "reaped_by" and candidate is None:
            continue
        if not _identity_complete(candidate):
            return {"matches": False, "reason": "identity_unreadable", "axis": axis}
    if not isinstance(offset_n, int) or isinstance(offset_n, bool) or offset_n < 0:
        return {"matches": False, "reason": "offset_n"}
    if not isinstance(digest, str) or len(digest) != 64:
        return {"matches": False, "reason": "sha256_prefix"}
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    size = _file_size(target)
    if size < offset_n:
        return {"matches": False, "reason": "capture_shorter_than_boundary",
                "proof_bytes": offset_n, "file_bytes": size}
    try:
        if file_prefix_digest(target, offset_n) != digest:
            return {"matches": False, "reason": "capture_digest_mismatch"}
    except OSError as exc:
        return {"matches": False, "reason": f"capture_unreadable:{exc.errno}"}
    exit_evidence = record.get("exit") if isinstance(record.get("exit"), dict) else {}
    how = str(exit_evidence.get("how") or "")
    if how == "exit_sentinel":
        if not sentinel_present:
            return {"matches": False, "reason": "sentinel_absent"}
        if exit_evidence.get("code") != sentinel_code:
            return {"matches": False, "reason": "sentinel_code_mismatch"}
    elif not how:
        return {"matches": False, "reason": "exit_evidence_missing"}
    return {"matches": True, "reason": ""}


# ---- the release record (DESIGN §1.8) ------------------------------------------------------------
def release_record_path(capture: str | os.PathLike[str] | bytes, incarnation: str) -> bytes:
    target = os.fsencode(os.fspath(capture)) if not isinstance(capture, bytes) else capture
    return target + b".release." + incarnation.encode() + b".json"


def make_release_record(*, fence: str, fence_file_sha256: str, release_nonce: str, offset_r: int,
                        retained_tail_bytes: int, retained_tail_sha256: str,
                        custodian: Mapping[str, Any], custodian_role: str, state: str,
                        published_at: str) -> dict[str, Any]:
    return {"schema": RELEASE_BOUNDARY_SCHEMA, "fence": fence,
            "fence_file_sha256": fence_file_sha256, "release_nonce": release_nonce,
            "offset_r": int(offset_r), "retained_tail_bytes": int(retained_tail_bytes),
            "retained_tail_sha256": retained_tail_sha256, "custodian": dict(custodian),
            "custodian_role": custodian_role, "state": state, "published_at": published_at}


def write_release_record(path: str | os.PathLike[str] | bytes, record: Mapping[str, Any]) -> bool:
    if record.get("schema") != RELEASE_BOUNDARY_SCHEMA:
        raise ValueError("not a release record")
    return publish_exclusive(path, json.dumps(record, sort_keys=True).encode())


def read_release_record(path: str | os.PathLike[str] | bytes, *, fence: str) -> dict[str, Any]:
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    got = _read_json_record(target, RELEASE_BOUNDARY_SCHEMA)
    if got["outcome"] != "present":
        return got
    if got["record"].get("fence") != fence:
        return {"outcome": "foreign", "record": None, "detail": "release record of another incarnation"}
    return {"outcome": EVIDENCE_FINAL, "record": got["record"], "detail": ""}


def verify_release_record(record: Mapping[str, Any], *, capture: bytes, fence_path: bytes,
                          fence_record: Mapping[str, Any]) -> dict[str, Any]:
    """[FORKED-SAFE] REVIEW_IMPLEMENTATION F-003: join a release record to the SAME proof --
    the fence file digest it cites, the fence's nonce and boundary N, and the retained tail
    ``capture[N+marker_len : R)`` recomputed from the capture bytes.  ``{"matches", "reason"}``."""
    boundary = fence_record.get("boundary") if isinstance(fence_record.get("boundary"), dict) else {}
    offset_n = int(boundary.get("offset_n", -1))
    marker_len = int(boundary.get("marker_len") or 0)
    if record.get("release_nonce") != boundary.get("marker_nonce"):
        return {"matches": False, "reason": "release_nonce_mismatch"}
    try:
        if record.get("fence_file_sha256") != file_digest(fence_path):
            return {"matches": False, "reason": "fence_file_digest_mismatch"}
    except OSError:
        return {"matches": False, "reason": "fence_file_unreadable"}
    r = int(record.get("offset_r", -1))
    if r <= offset_n or r > len(capture):
        return {"matches": False, "reason": "offset_r_outside_capture"}
    found, _length, state = find_release_marker(capture, str(record.get("release_nonce") or ""), after=offset_n)
    if state != EVIDENCE_FINAL or found != r:
        return {"matches": False, "reason": "release_marker_" + ("duplicated" if state == EVIDENCE_INCONSISTENT else "absent_or_moved")}
    tail = capture[offset_n + marker_len:r]
    if len(tail) != int(record.get("retained_tail_bytes", -1)):
        return {"matches": False, "reason": "retained_tail_length_mismatch"}
    if hashlib.sha256(tail).hexdigest() != record.get("retained_tail_sha256"):
        return {"matches": False, "reason": "retained_tail_digest_mismatch"}
    return {"matches": True, "reason": ""}


def file_digest(path: str | os.PathLike[str] | bytes) -> str:
    target = os.fsencode(os.fspath(path)) if not isinstance(path, bytes) else path
    size = _file_size(target)
    return file_prefix_digest(target, size) if size >= 0 else ""


def recover_release_boundary(capture: bytes, nonce: str, offset_n: int, *,
                             release_record_present: bool) -> tuple[int, int, str, str | None]:
    """DESIGN §1.8: ``(R, marker_len, state, outcome)`` -- R from the capture when the RELEASE
    marker is present (the retained tail is then provable exactly as on the live path); absent
    → the retention boundary is UNKNOWN (`diagnostic_tail_unaccounted`, plus
    `release_record_missing` when no record exists either).  `[0,N)` is untouched either way."""
    r, length, state = find_release_marker(capture, nonce, after=offset_n)
    if state == EVIDENCE_FINAL:
        return r, length, state, None
    if state == EVIDENCE_INCONSISTENT:
        return -1, 0, state, OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED
    return -1, 0, EVIDENCE_UNKNOWN, (OUTCOME_DIAGNOSTIC_TAIL_UNACCOUNTED if release_record_present
                                     else OUTCOME_RELEASE_RECORD_MISSING)


# ---- finalizer owner generations (DESIGN §2.5) -----------------------------------------------------
def owner_generation_path(directory: str | os.PathLike[str] | bytes, incarnation: str,
                          generation: int) -> bytes:
    base = os.fsencode(os.fspath(directory)) if not isinstance(directory, bytes) else directory
    return os.path.join(base, b"owner." + incarnation.encode() + b".g" + str(int(generation)).encode())


def relinquish_path(directory: str | os.PathLike[str] | bytes, incarnation: str,
                    generation: int) -> bytes:
    base = os.fsencode(os.fspath(directory)) if not isinstance(directory, bytes) else directory
    return os.path.join(base, b"relinquish." + incarnation.encode() + b".g" + str(int(generation)).encode())


def read_generations(directory: str | os.PathLike[str] | bytes, incarnation: str
                     ) -> tuple[int, dict[str, Any] | None, str]:
    """``(highest_generation, highest_record, state)``: the highest LINKED generation for this
    incarnation (0 when none); ``state`` is ``final`` (readable) or ``unreadable``."""
    base = os.fsencode(os.fspath(directory)) if not isinstance(directory, bytes) else directory
    prefix = b"owner." + incarnation.encode() + b".g"
    try:
        names = os.listdir(base)
    except OSError:
        return 0, None, EVIDENCE_UNREADABLE
    highest, record = 0, None
    for name in names:
        if not name.startswith(prefix) or b".tmp." in name:
            continue
        try:
            generation = int(name[len(prefix):])
        except ValueError:
            continue
        if generation > highest:
            got = _read_json_record(os.path.join(base, name), FINALIZER_OWNER_SCHEMA)
            if got["outcome"] != "present":
                return 0, None, EVIDENCE_UNREADABLE
            highest, record = generation, got["record"]
    return highest, record, EVIDENCE_FINAL


def make_owner_generation(*, fence: str, generation: int, owner_role: str,
                          owner: Mapping[str, Any], claim_reason: str,
                          superseded: Mapping[str, Any] | None, death_evidence: str,
                          claimed_at: str) -> dict[str, Any]:
    return {"schema": FINALIZER_OWNER_SCHEMA, "fence": fence, "generation": int(generation),
            "owner_role": owner_role, "owner": dict(owner), "claimed_at": claimed_at,
            "claim_reason": claim_reason,
            "superseded": dict(superseded) if superseded else None,
            "death_evidence": death_evidence}


def claim_target(evidence: Mapping[str, Any]) -> int:
    """DESIGN §2.5 rule 3: the ONLY generation a claim carrying ``evidence`` may link."""
    return int(evidence["predecessor_generation"]) + 1


def validate_claim(record: Mapping[str, Any], evidence: Mapping[str, Any], *,
                   highest_linked_now: int) -> str | None:
    """Refuse by name (`owner_conflict`) a link that is not exactly predecessor+1 for the pinned
    predecessor, or attempted after another generation superseded that predecessor."""
    if int(record.get("generation", -1)) != claim_target(evidence):
        return OUTCOME_OWNER_CONFLICT
    predecessor = evidence.get("predecessor") or {}
    if int(evidence["predecessor_generation"]) > 0:
        superseded = record.get("superseded") or {}
        if (superseded.get("pid"), superseded.get("start_id")) != (predecessor.get("pid"), predecessor.get("start_id")):
            return OUTCOME_OWNER_CONFLICT
    if int(highest_linked_now) != int(evidence["predecessor_generation"]):
        return OUTCOME_OWNER_CONFLICT
    return None


def may_claim_generation(*, fence_published: bool, highest_owner_alive: bool | None,
                         relinquish_record: bool, death_witness: str) -> tuple[str, str | None]:
    """DESIGN §2.5 rules 1-2.  ``("custodian"|"claim"|"refuse", outcome)``.
    Terminal rule: once a fence is published NO generation may be claimed -- a later actor is a
    CUSTODIAN (release/cleanup only).  Death rule: guard EOF is relinquishment-or-death; a claim
    needs the owner's durable relinquishment record or an incarnation-bound death witness in
    state ``final``; anything else refuses by name."""
    if fence_published:
        if highest_owner_alive and not relinquish_record:
            return "custodian", OUTCOME_FENCE_PUBLISHED_NO_CLAIM
        return "custodian", None
    if relinquish_record:
        return "claim", None
    # REVIEW_IMPLEMENTATION F-002: a POSITIVELY LIVE highest owner is refused BEFORE any death
    # witness is consulted -- a witness can only ever be for the owner it was registered on,
    # and the caller must have re-bound it to the highest owner's identity (`witness_for`);
    # a live owner with a "final" witness is a contradiction, never a claim.
    if highest_owner_alive:
        return "refuse", OUTCOME_FINALIZER_ALIVE
    if death_witness == EVIDENCE_FINAL:
        return "claim", None
    if death_witness in (EVIDENCE_UNREADABLE, EVIDENCE_INCONSISTENT):
        return "refuse", "identity_unreadable"
    return "refuse", OUTCOME_SUCCESSION_UNWITNESSED


def claim_generation(directory: str | os.PathLike[str] | bytes, incarnation: str,
                     record: Mapping[str, Any], evidence: Mapping[str, Any]) -> str | None:
    """[FORKED-SAFE] Validate the claim against the pinned evidence and the generations linked
    NOW, then link it exclusively.  ``None`` = won; else the named refusal."""
    if record.get("schema") != FINALIZER_OWNER_SCHEMA:
        raise ValueError("not an owner generation record")
    highest, _rec, state = read_generations(directory, incarnation)
    if state != EVIDENCE_FINAL:
        return "identity_unreadable"
    why = validate_claim(record, evidence, highest_linked_now=highest)
    if why is not None:
        return why
    target = owner_generation_path(directory, incarnation, int(record["generation"]))
    if publish_exclusive(target, json.dumps(record, sort_keys=True).encode()):
        return None
    return OUTCOME_OWNER_CONFLICT


def write_relinquish(directory: str | os.PathLike[str] | bytes, incarnation: str, *,
                     fence: str, generation: int, owner: Mapping[str, Any], reason: str,
                     written_at: str) -> bool:
    record = {"schema": RELINQUISH_SCHEMA, "fence": fence, "generation": int(generation),
              "owner": dict(owner), "reason": reason, "written_at": written_at}
    return publish_exclusive(relinquish_path(directory, incarnation, generation),
                             json.dumps(record, sort_keys=True).encode())


def read_relinquish(directory: str | os.PathLike[str] | bytes, incarnation: str,
                    generation: int) -> dict[str, Any]:
    return _read_json_record(relinquish_path(directory, incarnation, generation), RELINQUISH_SCHEMA)


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
    ``offset``.  Raw ``os`` calls, like :func:`write_meta`, so BOTH writers call it: the
    supervisor's :class:`BoundedCapture` and, since round-8 iteration 2, the exit
    watcher's :class:`RawBoundedAppender` -- so a reader that lands between either
    writer's bytes and its meta sees a VERIFIED in-flight suffix, never an unverified
    one."""
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


#: REVIEW_IMPLEMENTATION_iteration8 F-015 -- the framing scan's bounds.  A scan that reaches
#: any of them is INCOMPLETE and says so (`EmbeddedScan.complete == False`); the selector turns
#: that into the named outcome `record_scan_incomplete` (LOST), never into "no candidates".
#: Objects: every JSON object EXAMINED (top-level and nested) across the whole fenced range;
#: depth: nesting the traversal will descend; bytes: characters the balanced-object finder may
#: visit in total (the finder re-scans after an unbalanced `{`, so this bounds its worst case
#: independently of the capture's own byte limit).
EMBEDDED_SCAN_OBJECT_LIMIT = 65_536
EMBEDDED_SCAN_DEPTH_LIMIT = 64
EMBEDDED_SCAN_BYTE_BUDGET = 64 * 1024 * 1024
SCAN_INCOMPLETE_OBJECTS = "object_limit"
SCAN_INCOMPLETE_DEPTH = "depth_limit"
SCAN_INCOMPLETE_BYTES = "byte_budget"
#: REVIEW_IMPLEMENTATION_iteration2 (run_5fcd2beac376) F-017 / F-015: the JSON parser gave up on a
#: candidate for a reason that is NOT a syntax rejection -- an allocation failure
#: (`MemoryError`) or a value-conversion failure that is not `JSONDecodeError` -- so the
#: candidate is UNEXAMINED, never "invalid prose"
SCAN_INCOMPLETE_RESOURCE = "resource_limit"
SCAN_INCOMPLETE_CONVERSION = "conversion_limit"
#: REVIEW_IMPLEMENTATION_iteration2 F-015: the reader's integer-conversion budget.  A JSON
#: integer token longer than this is kept as an UNCONVERTED digit string (a `str` subclass,
#: :class:`UnconvertedInteger`) instead of being converted -- the object is still parsed
#: whole, so a refusal it carries is recognised; the interpreter's own `int(...)` string
#: limit (4,300 digits by default) is never reached and never raises.  Python 3.11+ raises
#: `ValueError` (not `JSONDecodeError`) from `json.loads` for a longer token; the i2 parsers
#: read that as "invalid prose" and an earlier success won over a bound refusal.
INTEGER_DIGIT_BUDGET = 4_000


class UnconvertedInteger(str):
    """A JSON integer token the reader did not convert (longer than
    :data:`INTEGER_DIGIT_BUDGET` digits): its digits, as a string, so a record carrying it is
    still examined whole.  Never compared as a number by this runtime."""
    __slots__ = ()


def _bounded_int(token: str) -> Any:
    return int(token) if len(token) <= INTEGER_DIGIT_BUDGET else UnconvertedInteger(token)


class ParseFailure(Exception):
    """The JSON parser could not EXAMINE a candidate for a reason that is not a syntax
    rejection: ``reason`` is one of `resource_limit` (`MemoryError`), `depth_limit`
    (`RecursionError`), `conversion_limit` (a `ValueError` that is not `JSONDecodeError`,
    i.e. a value the parser accepted syntactically but could not convert)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def parse_json(text: str) -> Any:
    """`json.loads` with the reader's bounded integer conversion; raises `json.JSONDecodeError`
    for a SYNTAX rejection and :class:`ParseFailure` for everything else the parser can fail
    with (allocation, depth, conversion) -- never any other exception.  THE one JSON entry
    point of every reader on the settlement / readiness path (F-017: total by construction;
    a seam for the resource-failure locks)."""
    try:
        return json.loads(text, parse_int=_bounded_int)
    except json.JSONDecodeError:
        raise
    except MemoryError as exc:
        raise ParseFailure(SCAN_INCOMPLETE_RESOURCE, type(exc).__name__) from None
    except RecursionError as exc:
        raise ParseFailure(SCAN_INCOMPLETE_DEPTH, type(exc).__name__) from None
    except ValueError as exc:
        raise ParseFailure(SCAN_INCOMPLETE_CONVERSION, f"{type(exc).__name__}: {exc}"[:200]) from None


class ScanBudget:
    """The mutable bound one framing scan shares across every unparsable run of a fenced
    range: `objects` and `chars` count DOWN; `exhausted` names the first bound reached."""

    def __init__(self, *, objects: int | None = None, depth: int | None = None,
                 chars: int | None = None) -> None:
        # the module bounds are read at construction (a lock may lower them by patching)
        self.objects = int(EMBEDDED_SCAN_OBJECT_LIMIT if objects is None else objects)
        self.depth = int(EMBEDDED_SCAN_DEPTH_LIMIT if depth is None else depth)
        self.chars = int(EMBEDDED_SCAN_BYTE_BUDGET if chars is None else chars)
        self.exhausted = ""

    def spend_chars(self, n: int) -> bool:
        self.chars -= int(n)
        if self.chars < 0 and not self.exhausted:
            self.exhausted = SCAN_INCOMPLETE_BYTES
        return self.chars >= 0

    def spend_object(self) -> bool:
        self.objects -= 1
        if self.objects < 0 and not self.exhausted:
            self.exhausted = SCAN_INCOMPLETE_OBJECTS
        return self.objects >= 0


class EmbeddedScan(TypedDict):
    """`embedded_scan`'s answer: every object EXAMINED (top-level and nested, traversal order),
    whether the scan COMPLETED, and -- when it did not -- the bound it hit."""
    objects: list[dict[str, Any]]
    complete: bool
    reason: str
    examined: int


def walk_nested_objects(root: Any, out: list[dict[str, Any]], budget: ScanBudget) -> bool:
    """Every dict reachable from ``root`` through dicts and lists, breadth-first per level,
    appended to ``out`` -- NEVER through a string: a JSON string literal is data, whatever it
    spells (a serialized record inside a string is not a record).  ``False`` when the object or
    depth bound stops the walk before every dict was examined."""
    level: list[Any] = [root]
    depth = 0
    while level:
        if depth > budget.depth:
            if not budget.exhausted:
                budget.exhausted = SCAN_INCOMPLETE_DEPTH
            return False
        nxt: list[Any] = []
        for node in level:
            if isinstance(node, dict):
                if not budget.spend_object():
                    return False
                out.append(node)
                nxt.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                nxt.extend(v for v in node if isinstance(v, (dict, list)))
        level = nxt
        depth += 1
    return True


def embedded_scan(text: str, *, budget: ScanBudget | None = None) -> EmbeddedScan:
    """REVIEW_IMPLEMENTATION_iteration7/8 F-015: every JSON OBJECT embedded anywhere in ``text``
    (a run of lines that do not parse as records) -- a helper prefix (`progress: {...}`), a
    suffix, a record split across lines, a cooperative WRAPPER closed later (`{"progress": `
    + the root's own record + `}`) -- each balanced top-level object (string- and escape-aware,
    `json.loads`-accepted) AND every object nested inside it through dicts and lists, in
    order.  NOT a parser of records: what it yields is evidence that a completion-SHAPED or
    refusing object exists where the record grammar sees prose.

    BOUNDED and HONEST about it (i8): the shared ``budget`` limits the objects examined, the
    nesting descended and the characters the finder visits; reaching any bound ends the scan
    with ``complete == False`` and the bound's name -- the remainder was NOT examined, and a
    caller must never read an incomplete scan as "no candidate".  A string literal's contents
    are never descended or re-parsed."""
    budget = budget or ScanBudget()
    out: list[dict[str, Any]] = []
    complete = True
    i, n = 0, len(text)
    while i < n:
        start = text.find("{", i)
        if start < 0:
            break
        depth, j, in_str, esc = 0, start, False, False
        end = -1
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if not budget.spend_chars(j - start + 1):
            complete = False
            break
        if end < 0:
            i = start + 1
            continue
        try:
            candidate = parse_json(text[start:end + 1])
        except json.JSONDecodeError:
            i = start + 1                                  # a syntax rejection: not an object
            continue
        except ParseFailure as failure:
            # the parser gave up for a NON-syntax reason (allocation / depth / conversion):
            # the candidate is UNEXAMINED by construction, never invalid prose
            budget.exhausted = budget.exhausted or failure.reason
            complete = False
            break
        if isinstance(candidate, dict):
            if not walk_nested_objects(candidate, out, budget):
                complete = False
                break
            i = end + 1
        else:
            i = start + 1
    return {"objects": out, "complete": complete and not budget.exhausted,
            "reason": budget.exhausted if (not complete or budget.exhausted) else "",
            "examined": len(out)}


def embedded_objects(text: str, *, limit: int = EMBEDDED_SCAN_OBJECT_LIMIT) -> list[dict[str, Any]]:
    """The objects of :func:`embedded_scan` over ``text`` alone (a convenience for readers that
    want the list; production selection reads the SCAN, whose completeness it must not drop)."""
    return embedded_scan(text, budget=ScanBudget(objects=limit))["objects"]


def unparsable_runs(text: str) -> list[str]:
    """The maximal runs of consecutive lines of ``text`` that are NOT records (joined with
    the delimiter): what :func:`embedded_scan` scans (F-015).  A record split across two
    lines is one run; a helper prefix on a record line is one run."""
    runs: list[list[str]] = []
    current: list[str] = []
    for parsed, raw in structured_lines(text):
        if parsed is None:
            current.append(raw)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return ["\n".join(run) for run in runs]


def structured_lines(text: str) -> tuple[tuple[dict[str, Any] | None, str], ...]:
    """Split a captured stream into ``(parsed-JSON-or-None, raw-line)`` pairs.

    An unparsable line is kept with ``None`` -- **never dropped**.  A line that is not JSON
    is not evidence of anything, but it is still part of the transcript, and dropping it
    would make the capture disagree with the file it came from.  Split on the protocol
    delimiter only (:func:`protocol_lines`), never on Unicode line separators inside a
    record.

    REVIEW_IMPLEMENTATION (run_5fcd2beac376) F-017: this is the FIRST parser every reader
    of the stream reaches (readiness, refusal evidence, completion selection), so a line the
    JSON parser cannot follow to the end -- a nesting depth past the interpreter's recursion
    limit raises ``RecursionError``, not ``ValueError`` -- must never escape as an untyped
    exception.  Such a line is kept as an UNPARSABLE line (``None``), which routes it into the
    bounded framing scan (:func:`embedded_scan`), where the same depth failure is the named
    ``depth_limit`` and the settlement is `record_scan_incomplete` (LOST), never COMPLETED.
    """
    out: list[tuple[dict[str, Any] | None, str]] = []
    for line in protocol_lines(text):
        stripped = line.strip()
        if not stripped:
            continue
        out.append((parse_record_line(stripped), line))
    return tuple(out)


def parse_record_line(stripped: str) -> dict[str, Any] | None:
    """One stripped line as a JSON OBJECT record, or ``None`` -- for a non-JSON line, a JSON
    value that is not an object, and a line the parser could not examine (F-017 depth /
    allocation, F-015 conversion: see :func:`line_parse_failure`, which names WHY so that no
    caller reads such a line as harmless prose).  Total over its input: no exception leaves
    this function for any text."""
    parsed, _failure = _parse_record_line(stripped)
    return parsed


def _parse_record_line(stripped: str) -> "tuple[dict[str, Any] | None, str]":
    """``(record-or-None, failure reason)``: the reason is ``""`` for a syntax rejection /
    a non-object / a non-JSON line, else the :class:`ParseFailure` reason."""
    if not stripped or stripped[0] not in "{[":
        return None, ""
    try:
        candidate = parse_json(stripped)
    except json.JSONDecodeError:
        return None, ""
    except ParseFailure as failure:
        return None, failure.reason
    return (candidate if isinstance(candidate, dict) else None), ""


def structured_lines_with_failures(text: str) -> "tuple[tuple[dict[str, Any] | None, str, str], ...]":
    """:func:`structured_lines` with a third element per line: ``""`` or the reason the record
    parser could not EXAMINE the line (`resource_limit` / `depth_limit` / `conversion_limit`)
    -- for readers that must stop at such a line instead of reading it as prose."""
    out: list[tuple[dict[str, Any] | None, str, str]] = []
    for line in protocol_lines(text):
        stripped = line.strip()
        if not stripped:
            continue
        parsed, reason = _parse_record_line(stripped)
        out.append((parsed, line, reason))
    return tuple(out)


def line_parse_failures(text: str) -> list[dict[str, Any]]:
    """REVIEW_IMPLEMENTATION_iteration2 F-017 / F-015: every line of ``text`` the record parser
    could not EXAMINE (allocation / depth / conversion), as ``{"index", "reason"}`` -- the
    evidence a scan needs to declare itself incomplete instead of treating the line as prose
    and the range as fully examined."""
    out: list[dict[str, Any]] = []
    for index, line in enumerate(protocol_lines(text)):
        stripped = line.strip()
        if not stripped:
            continue
        _parsed, reason = _parse_record_line(stripped)
        if reason:
            out.append({"index": index, "reason": reason})
    return out


def redacted_summary(store: BoundedCapture, env_names: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A journal-safe summary.  Byte counts and limits; never the bytes."""
    return {"size": store.size, "truncated": store.truncated,
            "truncation": store.truncation, "dropped_bytes": store.dropped_bytes,
            "path": str(store.path),
            "limits": {"max_total_bytes": store.limits.max_total_bytes,
                       "max_line_bytes": store.limits.max_line_bytes,
                       "max_records": store.limits.max_records}}
