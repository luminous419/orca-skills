"""OS-43 Tier-2 run-scoped recovery lease and attempt ledger for a NON-paused run.

One durable file per run, ``.recovery_state.json``, over the same ``durable_store``
discipline (flock on a sidecar + ``fsync`` + ``os.replace``) every other Tier-2 record in
this package uses.  It exists for exactly one reason: a run that is ACTIVE and stalled has
**no** pause record, so ``pause_store``'s run-scoped lease -- the thing that makes two
concurrent recoveries produce exactly one winner -- has nothing to key on.  This record is
that lease for that branch, and nothing else.

Two rules are carried verbatim from the stores this one mirrors, because they are what the
whole guarantee rests on:

* ``lock -> read -> validate -> claim -> persist`` is ONE critical section, so two
  claimants produce exactly one ``CREATED`` (``pause_store.claim``, ``pause_store.py:504``;
  ``runtime_state.claim``, ``runtime_state.py:402``).
* every ownership-sensitive write is fenced by the token ``claim`` minted, and there is
  deliberately no "no token supplied" branch: an absent token is a missing capability, not
  permission to skip the check (``runtime_state.py:515-517``, ``ports.py:100-103``).

A record that fails its closed schema raises :class:`RecoveryRecordCorrupt` and is NEVER
read as "no prior attempt" -- ``runtime_state.py:125-130``'s rule, for the same reason:
reading a corrupt record as an empty one is what lets an external effect be recreated.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .durable_store import (DEFAULT_LOCK_TIMEOUT_SECONDS, FileCriticalSection,
                            LockUnavailable, read_json_document, write_json_document)
from .runtime_state import SystemLeaseClock, default_owner_id

RECOVERY_RECORD_SCHEMA_VERSION = "os43.recovery_state.v1"
RECOVERY_RECORD_FILENAME = ".recovery_state.json"

RECOVERY_STATUSES = ("ACTIVE", "SETTLED")
#: Mirrors :data:`runtime_state.CLAIM_OUTCOMES` exactly; a third name would be a third
#: meaning a caller has to learn, and there is no third thing that can happen.
CREATED, RESUMED, ALREADY_SETTLED = "CREATED", "RESUMED", "ALREADY_SETTLED"
RECOVERY_CLAIM_OUTCOMES = (CREATED, RESUMED, ALREADY_SETTLED)

#: The attempt's two stages.  ``CLAIMED`` is written BEFORE the re-entry and ``PROMOTED``
#: after it, so the stage may be ahead of the checkpoint -- harmless, the attempt is then
#: re-driven byte-identically -- but the checkpoint can never be ahead of the stage.
RECOVERY_ATTEMPT_STAGES = ("CLAIMED", "PROMOTED")

RECOVERY_RECORD_KEYS = (
    "run_id", "status", "owner_id", "lease_token", "lease_seconds", "lease_expires_at",
    "last_heartbeat_at", "created_at", "updated_at", "thread_id", "checkpoint_ns",
    "attempts",
)
RECOVERY_ATTEMPT_KEYS = (
    "recovery_id", "recovery_kind", "stage", "head_before", "head_after", "outcome",
    "code", "actor_id", "opened_at", "promoted_at",
)

DEFAULT_LEASE_SECONDS = 60.0


class RecoveryStoreError(ValueError):
    """The recovery store refused an operation under its own closed contract."""


class RecoveryRecordCorrupt(RecoveryStoreError):
    """The recovery record is missing a field, carries an unknown one, or fails its schema.

    Never read as "no prior attempt": an unreadable record is unknown, not empty.
    """


class RecoveryClaimHeld(RecoveryStoreError):
    """Another owner holds a live recovery lease on this run; this process must observe."""


class RecoveryClaimLost(RecoveryStoreError):
    """A fenced write presented a lease token this record no longer recognises."""


class RecoveryClaimRequired(RecoveryClaimLost):
    """A fenced write supplied no lease token.  Absent is never "skip the check"."""


class RecoveryStoreLockUnavailable(RecoveryStoreError, LockUnavailable):
    """This platform offers no inter-process file lock, so an exclusive claim is impossible."""


def recovery_record_path(run_id: str, *,
                         artifact_base: str | os.PathLike[str]) -> Path:
    return (Path(artifact_base) / "artifacts" / "runs" / run_id
            / RECOVERY_RECORD_FILENAME)


def validate_attempt(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise RecoveryRecordCorrupt("RECOVERY_RECORD_CORRUPT:attempt is not an object")
    if set(entry) != set(RECOVERY_ATTEMPT_KEYS):
        raise RecoveryRecordCorrupt(
            f"RECOVERY_RECORD_CORRUPT:attempt keys {sorted(entry)} != "
            f"{sorted(RECOVERY_ATTEMPT_KEYS)}")
    if entry["stage"] not in RECOVERY_ATTEMPT_STAGES:
        raise RecoveryRecordCorrupt(
            f"RECOVERY_RECORD_CORRUPT:attempt stage {entry['stage']!r}")
    for key in ("recovery_id", "recovery_kind", "head_before", "head_after", "outcome",
                "code", "actor_id", "opened_at"):
        if not isinstance(entry[key], str):
            raise RecoveryRecordCorrupt(f"RECOVERY_RECORD_CORRUPT:attempt {key}")
    if entry["promoted_at"] is not None and not isinstance(entry["promoted_at"], str):
        raise RecoveryRecordCorrupt("RECOVERY_RECORD_CORRUPT:attempt promoted_at")
    return dict(entry)


def validate_recovery_record(run_id: str, record: Any) -> dict[str, Any]:
    """The closed schema, checked on EVERY read.  Unknown keys are damage, not extension."""
    if not isinstance(record, dict):
        raise RecoveryRecordCorrupt("RECOVERY_RECORD_CORRUPT:record is not an object")
    if set(record) != set(RECOVERY_RECORD_KEYS):
        raise RecoveryRecordCorrupt(
            f"RECOVERY_RECORD_CORRUPT:{run_id}: keys {sorted(record)} != "
            f"{sorted(RECOVERY_RECORD_KEYS)}")
    if record["run_id"] != run_id:
        raise RecoveryRecordCorrupt(
            f"RECOVERY_RECORD_CORRUPT:{run_id}: record names {record['run_id']!r}")
    if record["status"] not in RECOVERY_STATUSES:
        raise RecoveryRecordCorrupt(
            f"RECOVERY_RECORD_CORRUPT:{run_id}: status {record['status']!r}")
    for key in ("owner_id", "lease_token", "created_at", "updated_at", "thread_id",
                "checkpoint_ns"):
        if not isinstance(record[key], str):
            raise RecoveryRecordCorrupt(f"RECOVERY_RECORD_CORRUPT:{run_id}: {key}")
    for key in ("lease_seconds", "lease_expires_at", "last_heartbeat_at"):
        value = record[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecoveryRecordCorrupt(f"RECOVERY_RECORD_CORRUPT:{run_id}: {key}")
    attempts = record["attempts"]
    if not isinstance(attempts, dict):
        raise RecoveryRecordCorrupt(f"RECOVERY_RECORD_CORRUPT:{run_id}: attempts")
    validated = {}
    for recovery_id, entry in attempts.items():
        row = validate_attempt(entry)
        if row["recovery_id"] != recovery_id:
            raise RecoveryRecordCorrupt(
                f"RECOVERY_RECORD_CORRUPT:{run_id}: attempt filed under {recovery_id!r} "
                f"names {row['recovery_id']!r}")
        validated[recovery_id] = row
    return {**record, "attempts": validated}


def new_recovery_record(run_id: str, *, thread_id: str = "", checkpoint_ns: str = "",
                        created_at: str = "", lease_seconds: float = DEFAULT_LEASE_SECONDS
                        ) -> dict[str, Any]:
    return {
        "run_id": run_id, "status": "ACTIVE", "owner_id": "", "lease_token": "",
        "lease_seconds": float(lease_seconds), "lease_expires_at": 0.0,
        "last_heartbeat_at": 0.0, "created_at": created_at, "updated_at": created_at,
        "thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "attempts": {},
    }


class FileRecoveryStateStore:
    """The run-scoped recovery lease over one JSON document per run.

    The surface is deliberately the ``RunPauseStatePort``-shaped subset
    (``ports.py:107-109``) plus the two attempt writes, so ``lease_keeper.LeaseKeeper``
    -- which calls exactly ``heartbeat(id, token)`` (``lease_keeper.py:265``) -- renews
    this lease unmodified.
    """

    def __init__(self, path: str | os.PathLike[str], *, clock: Any | None = None,
                 owner_id: str | None = None,
                 lease_seconds: float = DEFAULT_LEASE_SECONDS,
                 lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        try:
            self._section = FileCriticalSection(path, clock=clock,
                                                lock_timeout_seconds=lock_timeout_seconds)
        except LockUnavailable as exc:  # pragma: no cover - non-POSIX hosts only
            raise RecoveryStoreLockUnavailable(str(exc)) from exc
        self.path = Path(path)
        self.clock = clock or SystemLeaseClock()
        self.owner_id = owner_id or default_owner_id()
        self.lease_seconds = float(lease_seconds)

    # -- raw document ------------------------------------------------------------
    def _read(self, run_id: str) -> dict[str, Any] | None:
        document = read_json_document(self.path,
                                      schema_version=RECOVERY_RECORD_SCHEMA_VERSION,
                                      corrupt_exc=RecoveryRecordCorrupt)
        if not document:
            return None
        if set(document) - {"schema_version", "record"}:
            raise RecoveryRecordCorrupt("RECOVERY_RECORD_CORRUPT:top-level keys")
        record = document.get("record")
        if record is None:
            return None
        return validate_recovery_record(run_id, record)

    def _persist(self, record: Mapping[str, Any]) -> None:
        validated = validate_recovery_record(record["run_id"], dict(record))
        write_json_document(self.path,
                            {"schema_version": RECOVERY_RECORD_SCHEMA_VERSION,
                             "record": validated})

    def _fenced(self, record: dict[str, Any] | None, run_id: str,
                lease_token: Any) -> dict[str, Any]:
        if record is None:
            raise RecoveryClaimLost(f"RECOVERY_CLAIM_LOST:{run_id}: no recovery record")
        if not isinstance(lease_token, str) or not lease_token:
            raise RecoveryClaimRequired(
                f"RECOVERY_CLAIM_LOST:{run_id}: an ownership-sensitive write needs the "
                "lease token returned by claim()")
        if record["lease_token"] != lease_token or record["owner_id"] != self.owner_id:
            raise RecoveryClaimLost(
                f"RECOVERY_CLAIM_LOST:{run_id}:owner={record['owner_id']}")
        return record

    # -- port surface ------------------------------------------------------------
    def read(self, run_id: str) -> dict[str, Any] | None:
        with self._section.locked():
            record = self._read(run_id)
            return deepcopy(record) if record is not None else None

    def claim(self, run_id: str, *, thread_id: str = "", checkpoint_ns: str = "",
              now_iso: str = "") -> dict[str, Any]:
        """Take the run-scoped recovery lease, or report why nothing is to be done.

        One atomic ``lock -> read -> validate -> claim -> persist`` section, so two
        claimants produce exactly one ``CREATED`` and the claim precedes every effect.
        """
        with self._section.locked():
            record = self._read(run_id)
            if record is None:
                record = new_recovery_record(run_id, thread_id=thread_id,
                                             checkpoint_ns=checkpoint_ns,
                                             created_at=now_iso,
                                             lease_seconds=self.lease_seconds)
            now = self.clock.time()
            if record["status"] == "SETTLED":
                return {**deepcopy(record), "claim_outcome": ALREADY_SETTLED}
            if record["owner_id"] and record["owner_id"] != self.owner_id \
                    and record["lease_expires_at"] > now:
                raise RecoveryClaimHeld(
                    f"RECOVERY_CLAIM_HELD:{run_id}:owner={record['owner_id']}:"
                    f"expires_at={record['lease_expires_at']}")
            outcome = CREATED if not record["owner_id"] else RESUMED
            record["owner_id"] = self.owner_id
            # Rotated on every claim: that rotation IS the fence, so a superseded owner's
            # token no longer matches and its writes are refused.
            record["lease_token"] = secrets.token_hex(16)
            record["lease_expires_at"] = now + self.lease_seconds
            record["last_heartbeat_at"] = now
            if thread_id:
                record["thread_id"] = thread_id
            record["checkpoint_ns"] = checkpoint_ns or record["checkpoint_ns"]
            if now_iso:
                record["updated_at"] = now_iso
            self._persist(record)
            return {**deepcopy(record), "claim_outcome": outcome}

    def heartbeat(self, run_id: str, lease_token: str) -> dict[str, Any]:
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            now = self.clock.time()
            record["last_heartbeat_at"] = now
            record["lease_expires_at"] = now + self.lease_seconds
            self._persist(record)
            return deepcopy(record)

    def release(self, run_id: str, lease_token: str) -> None:
        with self._section.locked():
            record = self._read(run_id)
            if record is None or record["lease_token"] != lease_token:
                return
            record["lease_expires_at"] = self.clock.time()
            self._persist(record)

    def observe(self, run_id: str, *, timeout_seconds: float,
                poll_seconds: float = 0.05) -> dict[str, Any] | None:
        """Watch a run another owner holds, with an explicit, finite timeout.

        Returns the settled record when the owner finishes, ``None`` when its lease
        lapses (the caller may then attempt takeover exactly once).  Raises when this
        observer's own bounded window closes while a live lease is still held -- a
        retryable outcome that claims nothing, never a verdict about the run.
        """
        if timeout_seconds <= 0:
            raise ValueError("observe() requires a positive timeout")
        deadline = self.clock.time() + timeout_seconds
        while True:
            with self._section.locked():
                record = deepcopy(self._read(run_id))
            if record is None:
                return None
            if record["status"] == "SETTLED":
                return record
            if record["lease_expires_at"] <= self.clock.time():
                return None
            if self.clock.time() >= deadline:
                raise RecoveryClaimHeld(
                    f"RECOVERY_CLAIM_HELD:{run_id}:owner={record['owner_id']}:"
                    "observation window closed while the lease was still live")
            self.clock.sleep(min(poll_seconds, max(deadline - self.clock.time(), 0.0)))

    # -- the attempt ledger ------------------------------------------------------
    def open_attempt(self, run_id: str, entry: Mapping[str, Any], *,
                     lease_token: str) -> dict[str, Any]:
        """Write the ``CLAIMED`` attempt entry BEFORE the re-entry, under the same lease.

        Idempotent for an entry that already exists: a re-drive of the same crash window
        replays this call and must not disturb what the earlier process recorded.
        """
        row = validate_attempt(entry)
        if row["stage"] != "CLAIMED":
            raise RecoveryStoreError("open_attempt writes a CLAIMED entry")
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            record["attempts"].setdefault(row["recovery_id"], row)
            self._persist(record)
            return deepcopy(record)

    def promote_attempt(self, run_id: str, recovery_id: str, *, head_after: str,
                        outcome: str, code: str, promoted_at: str,
                        lease_token: str) -> dict[str, Any]:
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            row = record["attempts"].get(recovery_id)
            if row is None:
                raise RecoveryStoreError(
                    f"RECOVERY_RECORD_CORRUPT:{run_id}: no attempt {recovery_id!r} to "
                    "promote; the entry is written before the effect, never after")
            row["stage"] = "PROMOTED"
            row["head_after"] = head_after
            row["outcome"] = outcome
            row["code"] = code
            row["promoted_at"] = promoted_at
            record["updated_at"] = promoted_at
            self._persist(record)
            return deepcopy(record)

    def settle(self, run_id: str, *, lease_token: str,
               updated_at: str = "") -> dict[str, Any]:
        with self._section.locked():
            record = self._fenced(self._read(run_id), run_id, lease_token)
            record["status"] = "SETTLED"
            if updated_at:
                record["updated_at"] = updated_at
            self._persist(record)
            return deepcopy(record)


def store_for(run_id: str, *, artifact_base: str | os.PathLike[str],
              clock: Any | None = None, owner_id: str | None = None,
              lease_seconds: float = DEFAULT_LEASE_SECONDS) -> FileRecoveryStateStore:
    return FileRecoveryStateStore(recovery_record_path(run_id,
                                                       artifact_base=artifact_base),
                                  clock=clock, owner_id=owner_id,
                                  lease_seconds=lease_seconds)
