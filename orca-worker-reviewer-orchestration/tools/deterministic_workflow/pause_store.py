"""Tier-2 durable pause index, coordination fence, projection and settlement journal.

Two durable files live here, both over the :mod:`durable_store` discipline (flock on a
sidecar + ``fsync`` + ``os.replace``), and neither imports LangGraph or Orca (OS-31 §10.1):

``.pause_state.json``          one record per **run** -- discovery identity, claim/lease
                               fence, checkpoint pointer, disposition, applied set and the
                               subordinate projection of the checkpoint.
``.settlement_journal.json``   one row per dispatch, written **before** every external
                               effect it describes, so a fresh process can reconstruct the
                               dispatch set and terminal provenance a dead process held
                               only in memory.

The pause record is **never** the authority for execution state.  That is the OS-40
checkpoint (PLAN D2/F-001); ``projection`` is subordinate and is documented as such so no
later reader mistakes it for authority.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Iterator, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import pause_policy
from .durable_store import (DEFAULT_LOCK_TIMEOUT_SECONDS, FileCriticalSection,
                            LockUnavailable, read_json_document, write_json_document)
from .runtime_state import SystemLeaseClock, default_owner_id

PAUSE_RECORD_SCHEMA_VERSION = "os31.pause_record.v2"
PAUSE_RECORD_STATUSES = ("WAITING_FOR_INPUT", "RESUMED", "CANCELLED", "ABANDONED")
PAUSE_CLAIM_OUTCOMES = ("CREATED", "RESUMED", "ALREADY_RESUMED",
                        "ALREADY_CANCELLED", "ALREADY_ABANDONED")
CREATED, RESUMED = "CREATED", "RESUMED"
ALREADY_RESUMED, ALREADY_CANCELLED, ALREADY_ABANDONED = (
    "ALREADY_RESUMED", "ALREADY_CANCELLED", "ALREADY_ABANDONED")

PAUSE_RECORD_KEYS = (
    "schema_version", "run_id", "workflow_id", "pause_record_id", "status",
    "created_at", "updated_at", "owner_id", "lease_token", "lease_expires_at",
    "last_heartbeat_at", "checkpoint_store_path", "thread_id", "checkpoint_ns",
    "checkpoint_id", "checkpoint_digest", "disposition", "ac1_discharged",
    "residual_terminals", "applied", "projection",
)
RESIDUAL_TERMINAL_KEYS = (
    "terminal_title", "terminal_digest", "terminal_role", "terminal_origin",
    "provenance_source", "task_id", "dispatch_id", "last_observation",
    "handle_recovery", "candidate_handle",
)

SETTLEMENT_JOURNAL_SCHEMA_VERSION = "os31.settlement_journal.v3"
JOURNAL_STAGES = ("PLANNED", "OPENED", "INTENDED", "ACCOUNTED", "DISPOSED")
JOURNAL_ROW_KEYS = (
    "intent_id", "run_id", "payload_digest",
    "task_id", "dispatch_id", "supervised",
    "terminal_title", "terminal_worktree",
    "terminal_digest",
    "terminal_role", "terminal_origin", "terminal_intended_role",
    "terminal_owner", "owner_dispatch_ids", "created_by",
    "provenance_source",
    "stage",
    "settlement", "worker_resource", "process_liveness",
    "cleanup_authority", "terminal_disposition",
    "recovery", "handle_recovery",
    "planned_at", "opened_at", "intended_at",
    "accounted_at", "disposed_at",
)

PAUSE_RECORD_FILENAME = ".pause_state.json"
SETTLEMENT_JOURNAL_FILENAME = ".settlement_journal.json"

DEFAULT_LEASE_SECONDS = 60.0
#: Added on top of the lease an observer is waiting out.  An observation window SHORTER
#: than the incumbent's lease can never legally reach takeover -- it expires while the
#: owner is still (nominally) alive -- so the documented single-call
#: observe-then-takeover path of :func:`pause_runtime.takeover` requires a window that
#: covers the whole remaining lease plus a margin for the poll granularity and clock skew.
DEFAULT_OBSERVE_GRACE_SECONDS = 5.0
#: Bounded by construction: lease + grace, never unbounded and never shorter than the lease.
DEFAULT_OBSERVE_TIMEOUT_SECONDS = DEFAULT_LEASE_SECONDS + DEFAULT_OBSERVE_GRACE_SECONDS
OBSERVE_POLL_SECONDS = 0.05


def observe_timeout_for(lease_seconds: Any) -> float:
    """The bounded observation window that covers one whole lease of ``lease_seconds``.

    Derived from the lease rather than hard-coded, for the same reason
    :func:`lease_keeper.heartbeat_interval_for` derives the renewal period: a constant is
    wrong the moment the lease is reconfigured, and "shorter than the lease" is exactly the
    defect (an observer that gives up before takeover is even legal).
    """
    try:
        lease = float(lease_seconds)
    except (TypeError, ValueError):
        lease = DEFAULT_LEASE_SECONDS
    if not lease > 0.0:
        lease = DEFAULT_LEASE_SECONDS
    return lease + DEFAULT_OBSERVE_GRACE_SECONDS


class PauseStoreError(ValueError):
    """A Tier-2 durable store refused an operation under its own closed contract."""


class PauseRecordCorrupt(PauseStoreError):
    """The pause record is missing a field, carries an unknown one, or fails its schema.

    Never read as "no pause": an invisible paused run is worse than a visible broken one.
    """


class SettlementJournalCorrupt(PauseStoreError):
    """A settlement journal row fails the closed :data:`JOURNAL_ROW_KEYS` schema."""


class PauseClaimHeld(PauseStoreError):
    """Another Coordinator holds a live lease on this run; this process must observe."""


class PauseClaimLost(PauseStoreError):
    """A fenced write presented a lease token this record no longer recognises."""


class PauseClaimRequired(PauseClaimLost):
    """A fenced write supplied no lease token.  Absent is never "skip the check"."""


class PauseObservationTimeout(PauseStoreError):
    """Observing another owner's paused run hit its explicit, finite timeout."""


class PauseStoreLockUnavailable(PauseStoreError, LockUnavailable):
    """This platform offers no inter-process file lock, so an exclusive claim is impossible."""


def run_root(artifact_base: str | os.PathLike[str], run_id: str) -> Path:
    return Path(artifact_base) / "artifacts" / "runs" / run_id


def pause_record_path(run_id: str, *, artifact_base: str | os.PathLike[str]) -> Path:
    return run_root(artifact_base, run_id) / PAUSE_RECORD_FILENAME


def settlement_journal_path(run_id: str, *,
                            artifact_base: str | os.PathLike[str]) -> Path:
    return run_root(artifact_base, run_id) / SETTLEMENT_JOURNAL_FILENAME


# ---- validation ----------------------------------------------------------------------
def validate_journal_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping) or set(row) != set(JOURNAL_ROW_KEYS):
        raise SettlementJournalCorrupt(
            "SETTLEMENT_JOURNAL_CORRUPT:row closed fields: "
            f"{sorted(row) if isinstance(row, Mapping) else type(row).__name__}")
    if row["stage"] not in JOURNAL_STAGES:
        raise SettlementJournalCorrupt(f"SETTLEMENT_JOURNAL_CORRUPT:unknown stage {row['stage']!r}")
    if type(row["supervised"]) is not bool:
        raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:supervised type")
    if not isinstance(row["owner_dispatch_ids"], list) or any(
            not isinstance(item, str) for item in row["owner_dispatch_ids"]):
        raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:owner_dispatch_ids type")
    for key in JOURNAL_ROW_KEYS:
        if key in ("supervised", "owner_dispatch_ids"):
            continue
        if not isinstance(row[key], str):
            raise SettlementJournalCorrupt(f"SETTLEMENT_JOURNAL_CORRUPT:{key} type")
    if not row["intent_id"] or not row["run_id"]:
        raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:identity")
    if row["provenance_source"] not in ("", *pause_policy.PROVENANCE_SOURCES):
        raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:provenance_source")
    if row["handle_recovery"] not in ("", *pause_policy.HANDLE_RECOVERY_OUTCOMES):
        raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:handle_recovery")
    return dict(row)


def new_journal_row(**fields: Any) -> dict[str, Any]:
    """A whole row with every closed key present.  A row is written whole or not at all."""
    row: dict[str, Any] = {key: "" for key in JOURNAL_ROW_KEYS}
    row["supervised"] = False
    row["owner_dispatch_ids"] = []
    row["stage"] = "PLANNED"
    row.update(fields)
    return validate_journal_row(row)


def validate_pause_record(run_id: str, record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) != set(PAUSE_RECORD_KEYS):
        raise PauseRecordCorrupt(
            "PAUSE_RECORD_CORRUPT:closed fields: "
            f"{sorted(record) if isinstance(record, Mapping) else type(record).__name__}")
    if record["schema_version"] != PAUSE_RECORD_SCHEMA_VERSION:
        raise PauseRecordCorrupt(
            f"PAUSE_RECORD_CORRUPT:schema {record['schema_version']!r}")
    if record["run_id"] != run_id:
        raise PauseRecordCorrupt(
            f"PAUSE_RECORD_CORRUPT:identity {record['run_id']!r} != {run_id!r}")
    if record["status"] not in PAUSE_RECORD_STATUSES:
        raise PauseRecordCorrupt(f"PAUSE_RECORD_CORRUPT:status {record['status']!r}")
    for key in ("workflow_id", "pause_record_id", "created_at", "updated_at", "owner_id",
                "lease_token", "checkpoint_store_path", "thread_id", "checkpoint_ns",
                "checkpoint_id", "checkpoint_digest"):
        if not isinstance(record[key], str):
            raise PauseRecordCorrupt(f"PAUSE_RECORD_CORRUPT:{key} type")
    for key in ("lease_expires_at", "last_heartbeat_at"):
        if type(record[key]) not in (int, float):
            raise PauseRecordCorrupt(f"PAUSE_RECORD_CORRUPT:{key} type")
    if type(record["ac1_discharged"]) is not bool:
        raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:ac1_discharged type")
    if not isinstance(record["residual_terminals"], list):
        raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:residual_terminals type")
    for entry in record["residual_terminals"]:
        if not isinstance(entry, Mapping) or set(entry) != set(RESIDUAL_TERMINAL_KEYS):
            raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:residual terminal closed fields")
    if record["disposition"] is not None:
        pause_policy.validate_disposition(record["disposition"])
    if not isinstance(record["applied"], dict):
        raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:applied type")
    for bundle_id, entry in record["applied"].items():
        if not isinstance(entry, Mapping) or set(entry) != set(pause_policy.APPLIED_ENTRY_KEYS):
            raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:applied entry closed fields")
        if entry["resume_bundle_id"] != bundle_id:
            raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:applied entry identity")
        if entry["stage"] not in pause_policy.APPLIED_STAGES:
            raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:applied entry stage")
        if not isinstance(entry["items"], list):
            raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:applied entry items")
    if not isinstance(record["projection"], dict):
        raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:projection type")
    if set(record["projection"]) != set(pause_policy.PAUSE_PROJECTION_KEYS):
        raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:projection closed fields")
    return dict(record)


def _binding_generation(record: Mapping[str, Any]) -> int:
    """The record's re-entry generation, or 0 for a projection that predates the field."""
    value = (record.get("projection") or {}).get("binding_generation")
    return value if type(value) is int else 0


def applied_items(decisions: Mapping[str, str]) -> list[dict[str, str]]:
    """The bundle's items, sorted by ``decision_item_id`` -- the whole answer, never one."""
    return [{"decision_item_id": item, "decision_id": decisions[item]}
            for item in sorted(decisions)]


# ---- the durable settlement journal (Tier 2b) ---------------------------------------
class FileSettlementJournal:
    """Append-then-promote journal of dispatch rows, durable BEFORE each external effect.

    ``OrcaAdapter._receipts`` and ``OrcaRuntimeHarness._terminals`` are process memory, and
    the durable ``FileRuntimeStateStore`` receipt deliberately carries no terminal handle,
    role or origin.  A successor Coordinator therefore cannot *discover* the work a dead
    process started, however idempotent re-mutation is.  This journal is what makes it
    discoverable, and every write lands strictly before the effect it covers.
    """

    def __init__(self, path: str | os.PathLike[str], *, clock: Any | None = None,
                 lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        try:
            self._section = FileCriticalSection(path, clock=clock,
                                                lock_timeout_seconds=lock_timeout_seconds)
        except LockUnavailable as exc:  # pragma: no cover - non-POSIX hosts only
            raise PauseStoreLockUnavailable(str(exc)) from exc
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        document = read_json_document(self.path,
                                      schema_version=SETTLEMENT_JOURNAL_SCHEMA_VERSION,
                                      corrupt_exc=SettlementJournalCorrupt)
        if not document:
            return {}
        if set(document) - {"schema_version", "rows"}:
            raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:top-level keys")
        rows = document.get("rows")
        if not isinstance(rows, dict):
            raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:rows container")
        for intent_id, row in rows.items():
            validated = validate_journal_row(row)
            if validated["intent_id"] != intent_id:
                raise SettlementJournalCorrupt("SETTLEMENT_JOURNAL_CORRUPT:row identity")
        return rows

    def _write(self, rows: dict[str, Any]) -> None:
        for row in rows.values():
            validate_journal_row(row)
        write_json_document(self.path, {
            "schema_version": SETTLEMENT_JOURNAL_SCHEMA_VERSION, "rows": rows})

    def rows(self) -> dict[str, dict[str, Any]]:
        with self._section.locked():
            return deepcopy(self._read())

    def row(self, intent_id: str) -> dict[str, Any] | None:
        with self._section.locked():
            found = self._read().get(intent_id)
            return deepcopy(found) if found is not None else None

    def record(self, intent_id: str, *, stage: str, **fields: Any) -> dict[str, Any]:
        """Write or promote one whole row.  Stage never moves backwards.

        A row already at ``ACCOUNTED`` or ``DISPOSED`` is not re-accounted, so a crash
        between dispatch *n* and *n+1* leaves the completed rows standing on disk and only
        the remainder is processed on retry.
        """
        if stage not in JOURNAL_STAGES:
            raise SettlementJournalCorrupt(f"SETTLEMENT_JOURNAL_CORRUPT:unknown stage {stage!r}")
        with self._section.locked():
            rows = self._read()
            existing = rows.get(intent_id)
            if existing is None:
                row = new_journal_row(intent_id=intent_id, stage=stage, **fields)
            else:
                row = dict(existing)
                row.update(fields)
                if JOURNAL_STAGES.index(stage) >= JOURNAL_STAGES.index(row["stage"]):
                    row["stage"] = stage
                row = validate_journal_row(row)
            rows[intent_id] = row
            self._write(rows)
            return deepcopy(row)

    def open_rows(self) -> tuple[dict[str, Any], ...]:
        """Every row of this journal that is not finished -- leg (1) of ``open_dispatches``."""
        return tuple(deepcopy(row) for _, row in sorted(self.rows().items())
                     if row["stage"] != "DISPOSED")


# ---- the run-scoped pause record (Tier 2) -------------------------------------------
class FilePauseRecordStore:
    """``RunPauseStatePort`` over one JSON document per run.

    Deliberately separate from ``RuntimeStatePort``, which is keyed on ``intent_id`` and
    cannot answer a run-scoped question.  Every mutating call is fenced by the lease token
    ``claim`` minted; there is no "no token supplied" branch.
    """

    def __init__(self, path: str | os.PathLike[str], *, clock: Any | None = None,
                 owner_id: str | None = None, lease_seconds: float = DEFAULT_LEASE_SECONDS,
                 lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        try:
            self._section = FileCriticalSection(path, clock=clock,
                                                lock_timeout_seconds=lock_timeout_seconds)
        except LockUnavailable as exc:  # pragma: no cover - non-POSIX hosts only
            raise PauseStoreLockUnavailable(str(exc)) from exc
        self.path = Path(path)
        self.clock = clock or SystemLeaseClock()
        self.owner_id = owner_id or default_owner_id()
        self.lease_seconds = float(lease_seconds)

    # -- raw document ------------------------------------------------------------
    def _document(self) -> dict[str, Any]:
        document = read_json_document(self.path,
                                      schema_version=PAUSE_RECORD_SCHEMA_VERSION,
                                      corrupt_exc=PauseRecordCorrupt)
        if not document:
            return {}
        if set(document) - {"schema_version", "record", "superseded"}:
            raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:top-level keys")
        return document

    def _read(self, run_id: str) -> dict[str, Any] | None:
        """The ACTIVE generation.  Superseded generations are read by :meth:`superseded`."""
        record = self._document().get("record")
        if record is None:
            return None
        return validate_pause_record(run_id, record)

    def _read_superseded(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._document().get("superseded") or []
        if not isinstance(rows, list):
            raise PauseRecordCorrupt("PAUSE_RECORD_CORRUPT:superseded container")
        return [validate_pause_record(run_id, row) for row in rows]

    def _persist(self, record: dict[str, Any],
                 superseded: list[dict[str, Any]] | None = None) -> None:
        """Write the whole document: the active generation plus the retained history.

        ``superseded`` defaults to whatever is already on disk, so every existing caller
        keeps writing exactly the record it holds and no retained generation is dropped by
        an ordinary fenced update.  The key is omitted entirely while the history is empty,
        so a single-generation run's document stays byte-identical to the pre-OS-31.1 one
        (which is what keeps the C4 "a second reindex is a byte-identical no-op" property).
        """
        validate_pause_record(record["run_id"], record)
        rows = (self._read_superseded(record["run_id"]) if superseded is None
                else [validate_pause_record(record["run_id"], row) for row in superseded])
        document: dict[str, Any] = {"schema_version": PAUSE_RECORD_SCHEMA_VERSION,
                                    "record": record}
        if rows:
            document["superseded"] = rows
        write_json_document(self.path, document)

    def _now(self) -> float:
        return self.clock.time()

    def _fenced(self, record: dict[str, Any] | None, run_id: str,
                lease_token: Any) -> dict[str, Any]:
        if record is None:
            raise PauseClaimLost(f"PAUSE_CLAIM_LOST:{run_id}: no pause record")
        if not isinstance(lease_token, str) or not lease_token:
            raise PauseClaimRequired(
                f"PAUSE_CLAIM_LOST:{run_id}: an ownership-sensitive write needs the lease "
                "token returned by claim()")
        if record["lease_token"] != lease_token or record["owner_id"] != self.owner_id:
            raise PauseClaimLost(f"PAUSE_CLAIM_LOST:{run_id}:owner={record['owner_id']}")
        return record

    # -- port surface ------------------------------------------------------------
    def read(self, run_id: str) -> dict[str, Any] | None:
        with self._section.locked():
            record = self._read(run_id)
            return deepcopy(record) if record is not None else None

    def superseded(self, run_id: str) -> tuple[dict[str, Any], ...]:
        """Every retained earlier generation of this run, oldest first."""
        with self._section.locked():
            return tuple(deepcopy(row) for row in self._read_superseded(run_id))

    def create(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Write the record AFTER the checkpoint committed (C1), per PAUSE GENERATION.

        A run may pause more than once: pause -> resume -> pause -> resume is a normal
        life, and each pause is its own **generation**, identified by ``pause_record_id``
        (a pure function of run, thread, request and decision items), pinned to its own
        ``checkpoint_id`` and carrying its own ``projection['binding_generation']``.
        Returning the run's existing record unconditionally -- which is what this did --
        silently discarded every generation after the first: the new checkpoint pointer and
        request never reached disk and the new pause became invisible to discovery.

        The policy, in order, and every branch of it explicit:

        * **no record**            -> persist it.  This is generation 1.
        * **same generation**      -> idempotent: return the stored record unchanged, so a
          re-finalise (a retry, a duplicate CLI call) is still a no-op and the live
          ownership columns of a generation somebody has already claimed are preserved.
        * **an ACTIVE generation** (``WAITING_FOR_INPUT``) that is NOT this one -> refuse
          with ``PAUSE_GENERATION_ACTIVE``.  A waiting question is a human's open decision;
          overwriting it would strand that decision with no record of what was asked, so
          it fails closed and the incumbent must be answered, cancelled or abandoned first.
        * **a DISPOSED run** (``CANCELLED`` / ``ABANDONED``) -> refuse with
          ``RUN_ALREADY_CANCELLED`` / ``RUN_ALREADY_ABANDONED``.  A disposed run is over;
          its thread is retired and it takes no further pause.
        * **a RESUMED generation** -> supersede it: the answered generation is retained,
          whole, in the document's ``superseded`` history (it is the evidence that its
          bundle was applied exactly once) and the new generation becomes the active
          record.  Retention is chosen over deletion deliberately: the applied set of a
          resumed generation is the OS-30 consumption lineage and must not evaporate when
          the run pauses again.

        Superseding is lineage-checked, never positional: the successor must name a
        DIFFERENT checkpoint than the generation it replaces (a generation is pinned to the
        checkpoint that committed it, so an identical pointer means the "new" pause is the
        old one under another name) and must not move ``binding_generation`` backwards.
        Either violation is ``PAUSE_GENERATION_LINEAGE``.
        """
        candidate = validate_pause_record(record["run_id"], record)
        run_id = candidate["run_id"]
        with self._section.locked():
            existing = self._read(run_id)
            if existing is None:
                self._persist(candidate)
                return deepcopy(candidate)
            same_request = existing["pause_record_id"] == candidate["pause_record_id"]
            if existing["status"] == "WAITING_FOR_INPUT":
                if same_request:
                    return deepcopy(existing)
                raise pause_policy.PauseRefused(
                    "PAUSE_GENERATION_ACTIVE",
                    f"{run_id}: generation {existing['pause_record_id']} is still "
                    f"WAITING_FOR_INPUT; it must be answered, cancelled or abandoned "
                    f"before {candidate['pause_record_id']} can become the active pause")
            if existing["status"] in ("CANCELLED", "ABANDONED"):
                raise pause_policy.PauseRefused(
                    f"RUN_ALREADY_{existing['status']}",
                    f"{run_id}: the run is {existing['status'].lower()}; a disposed run "
                    "takes no further pause generation")
            # existing["status"] == "RESUMED": the one supersedable generation.
            if existing["checkpoint_id"] == candidate["checkpoint_id"]:
                if same_request:
                    return deepcopy(existing)
                raise pause_policy.PauseRefused(
                    "PAUSE_GENERATION_LINEAGE",
                    f"{run_id}: {candidate['pause_record_id']} names the same checkpoint "
                    f"{candidate['checkpoint_id']!r} as the generation it claims to "
                    "follow; a new generation is pinned to its own checkpoint")
            if _binding_generation(candidate) < _binding_generation(existing):
                raise pause_policy.PauseRefused(
                    "PAUSE_GENERATION_LINEAGE",
                    f"{run_id}: binding_generation moved backwards, "
                    f"{_binding_generation(existing)} -> {_binding_generation(candidate)}")
            history = [*self._read_superseded(run_id), existing]
            self._persist(candidate, superseded=history)
            return deepcopy(candidate)

    def replace(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Re-derive the whole record from the checkpoint (C4 reindex), idempotently."""
        candidate = validate_pause_record(record["run_id"], record)
        with self._section.locked():
            self._persist(candidate)
            return deepcopy(candidate)

    def claim(self, run_id: str) -> dict[str, Any]:
        """Take the run-scoped lease, or report why nothing is to be done.

        ``lock -> read -> validate -> claim -> persist`` is one atomic critical section, so
        two claimants produce exactly one winner and the claim precedes every effect.
        """
        with self._section.locked():
            record = self._read(run_id)
            if record is None:
                raise PauseRecordCorrupt(f"PAUSE_RECORD_MISSING:{run_id}")
            now = self._now()
            if record["status"] == "RESUMED":
                return {**deepcopy(record), "claim_outcome": ALREADY_RESUMED}
            if record["status"] == "CANCELLED":
                return {**deepcopy(record), "claim_outcome": ALREADY_CANCELLED}
            if record["status"] == "ABANDONED":
                return {**deepcopy(record), "claim_outcome": ALREADY_ABANDONED}
            if record["owner_id"] and record["owner_id"] != self.owner_id \
                    and record["lease_expires_at"] > now:
                raise PauseClaimHeld(
                    f"PAUSE_CLAIM_HELD:{run_id}:owner={record['owner_id']}:"
                    f"expires_at={record['lease_expires_at']}")
            outcome = CREATED if not record["owner_id"] else RESUMED
            record["owner_id"] = self.owner_id
            record["lease_token"] = secrets.token_hex(16)
            record["lease_expires_at"] = now + self.lease_seconds
            record["last_heartbeat_at"] = now
            record["updated_at"] = record["updated_at"]
            self._persist(record)
            return {**deepcopy(record), "claim_outcome": outcome}

    def heartbeat(self, run_id: str, lease_token: str) -> dict[str, Any]:
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            now = self._now()
            record["last_heartbeat_at"] = now
            record["lease_expires_at"] = now + self.lease_seconds
            self._persist(record)
            return deepcopy(record)

    def release(self, run_id: str, lease_token: str) -> None:
        with self._section.locked():
            record = self._read(run_id)
            if record is None or record["lease_token"] != lease_token:
                return
            record["lease_expires_at"] = self._now()
            self._persist(record)

    def observe(self, run_id: str, *, timeout_seconds: float | None = None,
                poll_seconds: float = OBSERVE_POLL_SECONDS) -> dict[str, Any] | None:
        """Watch a run another Coordinator owns, with an explicit, finite timeout.

        Returns the settled record when the owner finishes, or ``None`` when the owner's
        lease lapses (the caller may then attempt takeover exactly once).

        **Observation/lease coherence.**  ``timeout_seconds`` defaults to
        :func:`observe_timeout_for` over *this store's* lease, i.e. one whole lease plus
        :data:`DEFAULT_OBSERVE_GRACE_SECONDS` -- bounded, finite, and long enough that a
        single observe-then-takeover call can actually reach the takeover when the
        incumbent stops heartbeating.  A window shorter than the lease could not: it
        expired while the owner was still nominally alive, which is what made
        ``takeover()``'s documented single-call path unreachable without an undocumented
        manual retry.

        **The retry contract, when a caller passes its own shorter window.**  An explicit
        ``timeout_seconds`` is honoured exactly as given and still bounds the wait, so a
        caller that deliberately wants to give up early keeps that behaviour.  Giving up is
        reported as :class:`PauseObservationTimeout` (reason code
        ``PAUSE_OBSERVATION_TIMEOUT``), which is a *retryable* outcome and never a verdict
        about the run: it says only that this observer's window closed while a live lease
        was still held.  A caller that sees it may call again -- nothing was claimed and no
        effect was performed -- and a caller that does not want to retry should observe
        with the default window instead.
        """
        if timeout_seconds is None:
            timeout_seconds = observe_timeout_for(self.lease_seconds)
        if timeout_seconds <= 0:
            raise ValueError("observe() requires a positive timeout")
        deadline = self.clock.time() + timeout_seconds
        while True:
            with self._section.locked():
                record = deepcopy(self._read(run_id))
            if record is None:
                return None
            if record["status"] in ("RESUMED", "CANCELLED", "ABANDONED"):
                return record
            if record["lease_expires_at"] <= self.clock.time():
                return None
            if self.clock.time() >= deadline:
                raise PauseObservationTimeout(
                    f"PAUSE_OBSERVATION_TIMEOUT:{run_id}:owner={record['owner_id']}")
            self.clock.sleep(min(poll_seconds, max(deadline - self.clock.time(), 0.0)))

    def update_pointer(self, run_id: str, *, checkpoint_id: str, checkpoint_digest: str,
                       projection: Mapping[str, Any], lease_token: str,
                       updated_at: str = "") -> dict[str, Any]:
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            record["checkpoint_id"] = checkpoint_id
            record["checkpoint_digest"] = checkpoint_digest
            record["projection"] = dict(projection)
            if updated_at:
                record["updated_at"] = updated_at
            self._persist(record)
            return deepcopy(record)

    def record_applied(self, run_id: str, entry: Mapping[str, Any],
                       lease_token: str) -> dict[str, Any]:
        """Write the ONE bundle-level applied entry, atomically.

        ``entry`` is a whole :data:`pause_policy.APPLIED_ENTRY_KEYS` bundle covering every
        decision item of the request -- never one item.  One call, one whole-record write
        under the flock critical section, so no window exists in which a subset of a
        bundle's items is recorded and the rest is not: per-item entries do not exist.
        """
        if set(entry) != set(pause_policy.APPLIED_ENTRY_KEYS):
            raise pause_policy.PauseRefused("PAUSE_LIFECYCLE_INCOHERENT",
                                            "applied entry closed fields")
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            expected = sorted(record["projection"]["decision_item_ids"])
            supplied = [item.get("decision_item_id") for item in entry["items"]]
            if supplied != expected or any(not item.get("decision_id")
                                           for item in entry["items"]):
                raise pause_policy.PauseRefused(
                    "PAUSE_LIFECYCLE_INCOHERENT",
                    f"{run_id}: the bundle must cover exactly {expected}, got {supplied}")
            bundle_id = entry["resume_bundle_id"]
            stored = record["applied"].get(bundle_id)
            if stored is not None:
                return deepcopy(record)
            other = [value for key, value in record["applied"].items() if key != bundle_id]
            if any(value["stage"] in pause_policy.APPLIED_STAGES for value in other):
                raise pause_policy.PauseRefused(
                    "RESPONSE_CONFLICT",
                    f"{run_id}: a different answer is already applied to this request")
            record["applied"][bundle_id] = dict(entry)
            self._persist(record)
            return deepcopy(record)

    def promote_applied(self, run_id: str, resume_bundle_id: str, *,
                        resumed_at: str, resumed_checkpoint_id: str,
                        lease_token: str) -> dict[str, Any]:
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            entry = record["applied"].get(resume_bundle_id)
            if entry is None:
                raise pause_policy.PauseRefused(
                    "PAUSE_LIFECYCLE_INCOHERENT", f"{run_id}: no applied entry to promote")
            entry["stage"] = "RESUMED"
            entry["resumed_at"] = resumed_at
            entry["resumed_checkpoint_id"] = resumed_checkpoint_id
            self._persist(record)
            return deepcopy(record)

    def mark_resumed(self, run_id: str, lease_token: str, *,
                     updated_at: str = "") -> dict[str, Any]:
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            record["status"] = "RESUMED"
            if updated_at:
                record["updated_at"] = updated_at
            self._persist(record)
            return deepcopy(record)

    def settle_disposition(self, run_id: str, disposition: Mapping[str, Any],
                           lease_token: str, *, updated_at: str = "",
                           residual_terminals: Any = None,
                           ac1_discharged: bool | None = None) -> dict[str, Any]:
        """Mark the record terminal.  Idempotent for the same ``cancellation_id``."""
        validated = pause_policy.validate_disposition(disposition)
        status = "CANCELLED" if validated["kind"] == "CANCEL" else "ABANDONED"
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            stored = record["disposition"]
            if stored is not None:
                if stored["cancellation_id"] != validated["cancellation_id"]:
                    raise pause_policy.PauseRefused(
                        f"RUN_ALREADY_{record['status']}",
                        f"{run_id}: already disposed as {stored['cancellation_id']}")
                return {**deepcopy(record), "claim_outcome": f"ALREADY_{record['status']}"}
            record["disposition"] = validated
            record["status"] = status
            if updated_at:
                record["updated_at"] = updated_at
            if residual_terminals is not None:
                record["residual_terminals"] = [dict(entry) for entry in residual_terminals]
            if ac1_discharged is not None:
                record["ac1_discharged"] = bool(ac1_discharged)
            self._persist(record)
            return {**deepcopy(record), "claim_outcome": status}


# ---- discovery -----------------------------------------------------------------------
def discover_paused_runs(artifact_base: str | os.PathLike[str]) -> tuple[dict[str, Any], ...]:
    """Every run root under ``<artifact_base>/artifacts/runs/`` that holds a pause record.

    Read-only, takes no claim, performs no effect, and is safe to run concurrently from any
    number of processes.  A record that fails closed-schema validation is reported with
    verdict ``PAUSE_RECORD_CORRUPT`` rather than skipped: an invisible paused run is worse
    than a visible broken one.
    """
    root = Path(artifact_base) / "artifacts" / "runs"
    listings: list[dict[str, Any]] = []
    if not root.is_dir():
        return ()
    for run_dir in sorted(root.iterdir()):
        record_path = run_dir / PAUSE_RECORD_FILENAME
        if not record_path.is_file():
            continue
        run_id = run_dir.name
        try:
            store = FilePauseRecordStore(record_path)
            record = store.read(run_id)
        except PauseStoreError as exc:
            listings.append(_corrupt_listing(run_id, str(exc)))
            continue
        if record is None:
            continue
        listings.append({
            "run_id": run_id, "status": record["status"],
            "pause_record_id": record["pause_record_id"],
            "current_phase": record["projection"]["current_phase"],
            "round_kind": record["projection"]["round_kind"],
            "request_id": record["projection"]["request_id"],
            "decision_item_ids": list(record["projection"]["decision_item_ids"]),
            "owner_id": record["owner_id"],
            "lease_expires_at": record["lease_expires_at"],
            "checkpoint_id": record["checkpoint_id"],
            "checkpoint_store_path": record["checkpoint_store_path"],
            "verdict": "", "detail": "",
        })
    return tuple(listings)


def _corrupt_listing(run_id: str, detail: str) -> dict[str, Any]:
    return {"run_id": run_id, "status": "", "pause_record_id": "", "current_phase": "",
            "round_kind": "", "request_id": "", "decision_item_ids": [], "owner_id": "",
            "lease_expires_at": 0.0, "checkpoint_id": "", "checkpoint_store_path": "",
            "verdict": "PAUSE_RECORD_CORRUPT", "detail": detail}


def run_disposition(artifact_base: str | os.PathLike[str],
                    run_id: str) -> dict[str, Any] | None:
    """The run's terminal disposition, or ``None`` for a run that has none.

    Used by the engine; ``clarification_protocol`` carries its own dependency-free twin,
    because that module deliberately imports nothing from ``scripts/``.
    """
    path = pause_record_path(run_id, artifact_base=artifact_base)
    if not path.is_file():
        return None
    record = FilePauseRecordStore(path).read(run_id)
    if record is None or record["status"] not in ("CANCELLED", "ABANDONED"):
        return None
    return dict(record["disposition"] or {})


def journal_for(run_id: str, *, artifact_base: str | os.PathLike[str],
                clock: Any | None = None) -> FileSettlementJournal:
    return FileSettlementJournal(settlement_journal_path(run_id, artifact_base=artifact_base),
                                 clock=clock)


def store_for(run_id: str, *, artifact_base: str | os.PathLike[str],
              clock: Any | None = None, owner_id: str | None = None,
              lease_seconds: float = DEFAULT_LEASE_SECONDS) -> FilePauseRecordStore:
    return FilePauseRecordStore(pause_record_path(run_id, artifact_base=artifact_base),
                                clock=clock, owner_id=owner_id, lease_seconds=lease_seconds)


def iter_journal_stages() -> Iterator[str]:
    yield from JOURNAL_STAGES
