"""OS-43 D-4: the run-scoped Coordinator liveness lease (AC-1's premise).

Merged code has no such signal, and that absence is the whole reason AC-1 could not be
implemented honestly.  Every ``last_heartbeat_at`` in the tree is a lease over one durable
record renewed only by ``lease_keeper._run`` (``lease_keeper.py:265``) from *inside* a
claimed section (``pause_runtime.py:737``, ``executor.py:277-280``), so a healthy
Coordinator parked on a human decision or waiting on ``orca orchestration check`` renews
nothing -- and "heartbeat expired" is a false positive BY CONSTRUCTION.  The OS-44 session
binding writes ``bound_at``/``released_at`` and no liveness timestamp at all
(``turn_boundary.py:1063-1069``).

This module adds the missing PRODUCER: one new file per run,
``artifacts/runs/<run_id>/.coordinator_liveness.json``, refreshed on a cadence that is
**independent of claimed sections** -- started when the Coordinator binds the run, stopped
when it releases it, beating for the whole interval in between regardless of what the
Coordinator is doing.

``lease_keeper.LeaseKeeper`` is reused **unmodified**: ``heartbeat(id, token)`` is the only
method it calls, so :class:`FileCoordinatorLivenessStore` exposes exactly that shape and
inherits the keeper's fail-closed renewal, its revoke-before-join shutdown verification and
its injectable ``waiter`` (which is how the tests pace renewal without sleeping).

The read is deliberately FOUR-VALUED and never boolean.  ``ABSENT`` is not ``EXPIRED``: a
run whose Coordinator never published a lease has no liveness evidence at all, and reading
that as "expired" would reinstate exactly the false positive this record removes.
``UNREADABLE`` is not ``EXPIRED`` either -- it routes to the observation snapshot's F1 and
therefore to the classifier's R1 -- following ``runtime_state.py:125-130``: reading a
corrupt or incompatible file as "no prior claim" is what let every external effect be
recreated.
"""
from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .durable_store import (DEFAULT_LOCK_TIMEOUT_SECONDS, FileCriticalSection,
                            LockUnavailable, read_json_document, write_json_document)
from .lease_keeper import LeaseKeeper, heartbeat_interval_for
from .runtime_state import DEFAULT_LEASE_SECONDS, SystemLeaseClock, default_owner_id

COORDINATOR_LIVENESS_SCHEMA_VERSION = "os43.coordinator_liveness.v1"
COORDINATOR_LIVENESS_FILENAME = ".coordinator_liveness.json"

#: Closed, and validated on every read.  Unknown keys, missing keys and wrong types are
#: damage, not an extension point.
COORDINATOR_LIVENESS_RECORD_KEYS = (
    "run_id", "owner_id", "session_id", "lease_token", "lease_seconds",
    "lease_expires_at", "last_heartbeat_at", "started_at", "released_at",
)

#: The four answers, and the reason this is not a boolean.
LIVENESS_LIVE = "LIVE"
LIVENESS_EXPIRED = "EXPIRED"
LIVENESS_ABSENT = "ABSENT"
LIVENESS_UNREADABLE = "UNREADABLE"
LIVENESS_STATUSES = (LIVENESS_LIVE, LIVENESS_EXPIRED, LIVENESS_ABSENT,
                     LIVENESS_UNREADABLE)


class CoordinatorLivenessError(ValueError):
    """The liveness store refused an operation under its own closed contract."""


class CoordinatorLivenessCorrupt(CoordinatorLivenessError):
    """The liveness record fails its closed schema.  Never read as "no prior lease"."""


class CoordinatorLivenessClaimLost(CoordinatorLivenessError):
    """A fenced write presented a lease token this record no longer recognises."""


class CoordinatorLivenessLockUnavailable(CoordinatorLivenessError, LockUnavailable):
    """This platform offers no inter-process file lock, so an exclusive claim is impossible."""


def liveness_record_path(run_id: str, *,
                         artifact_base: str | os.PathLike[str]) -> Path:
    return (Path(artifact_base) / "artifacts" / "runs" / run_id
            / COORDINATOR_LIVENESS_FILENAME)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_liveness_record(run_id: str, record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise CoordinatorLivenessCorrupt(
            "COORDINATOR_LIVENESS_CORRUPT:record is not an object")
    if set(record) != set(COORDINATOR_LIVENESS_RECORD_KEYS):
        raise CoordinatorLivenessCorrupt(
            f"COORDINATOR_LIVENESS_CORRUPT:{run_id}: keys {sorted(record)} != "
            f"{sorted(COORDINATOR_LIVENESS_RECORD_KEYS)}")
    if record["run_id"] != run_id:
        raise CoordinatorLivenessCorrupt(
            f"COORDINATOR_LIVENESS_CORRUPT:{run_id}: record names {record['run_id']!r}")
    for key in ("owner_id", "session_id", "lease_token", "started_at"):
        if not isinstance(record[key], str):
            raise CoordinatorLivenessCorrupt(
                f"COORDINATOR_LIVENESS_CORRUPT:{run_id}: {key}")
    for key in ("lease_seconds", "lease_expires_at", "last_heartbeat_at"):
        value = record[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CoordinatorLivenessCorrupt(
                f"COORDINATOR_LIVENESS_CORRUPT:{run_id}: {key}")
    if record["released_at"] is not None and not isinstance(record["released_at"], str):
        raise CoordinatorLivenessCorrupt(
            f"COORDINATOR_LIVENESS_CORRUPT:{run_id}: released_at")
    return dict(record)


class FileCoordinatorLivenessStore:
    """The liveness lease over one JSON document per run.

    The surface is exactly the ``RunPauseStatePort``-shaped subset ``LeaseKeeper`` needs
    (``ports.py:107-109``): ``claim`` / ``heartbeat`` / ``release`` / ``read``.  Nothing
    else is offered, because nothing else is a liveness question.
    """

    def __init__(self, path: str | os.PathLike[str], *, clock: Any | None = None,
                 owner_id: str | None = None,
                 lease_seconds: float = DEFAULT_LEASE_SECONDS,
                 lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        try:
            self._section = FileCriticalSection(path, clock=clock,
                                                lock_timeout_seconds=lock_timeout_seconds)
        except LockUnavailable as exc:  # pragma: no cover - non-POSIX hosts only
            raise CoordinatorLivenessLockUnavailable(str(exc)) from exc
        self.path = Path(path)
        self.clock = clock or SystemLeaseClock()
        self.owner_id = owner_id or default_owner_id()
        self.lease_seconds = float(lease_seconds)

    def _read(self, run_id: str) -> dict[str, Any] | None:
        document = read_json_document(
            self.path, schema_version=COORDINATOR_LIVENESS_SCHEMA_VERSION,
            corrupt_exc=CoordinatorLivenessCorrupt)
        if not document:
            return None
        if set(document) - {"schema_version", "record"}:
            raise CoordinatorLivenessCorrupt(
                "COORDINATOR_LIVENESS_CORRUPT:top-level keys")
        record = document.get("record")
        if record is None:
            return None
        return validate_liveness_record(run_id, record)

    def _persist(self, record: Mapping[str, Any]) -> None:
        validated = validate_liveness_record(record["run_id"], dict(record))
        write_json_document(
            self.path, {"schema_version": COORDINATOR_LIVENESS_SCHEMA_VERSION,
                        "record": validated})

    def read(self, run_id: str) -> dict[str, Any] | None:
        with self._section.locked():
            record = self._read(run_id)
            return deepcopy(record) if record is not None else None

    def claim(self, run_id: str, *, session_id: str = "") -> dict[str, Any]:
        """Publish this Coordinator's liveness lease for the run.

        A liveness lease is not an exclusivity claim and deliberately does NOT refuse a
        live foreign owner: two Coordinator processes on one run is an OS-31 ownership
        question the pause and intent leases already answer, and answering it a second
        time here would give one run two disagreeing authorities.  What this record says
        is only "a Coordinator was alive at this instant".
        """
        with self._section.locked():
            now = self.clock.time()
            record = {
                "run_id": run_id, "owner_id": self.owner_id, "session_id": session_id,
                "lease_token": secrets.token_hex(16),
                "lease_seconds": self.lease_seconds,
                "lease_expires_at": now + self.lease_seconds,
                "last_heartbeat_at": now, "started_at": _now(), "released_at": None,
            }
            self._persist(record)
            return deepcopy(record)

    def heartbeat(self, run_id: str, lease_token: str) -> dict[str, Any]:
        """The ONE method ``LeaseKeeper`` calls (``lease_keeper.py:265``)."""
        with self._section.locked():
            record = self._read(run_id)
            if record is None:
                raise CoordinatorLivenessClaimLost(
                    f"COORDINATOR_LIVENESS_LOST:{run_id}: no liveness record")
            if not isinstance(lease_token, str) or not lease_token:
                raise CoordinatorLivenessClaimLost(
                    f"COORDINATOR_LIVENESS_LOST:{run_id}: a fenced write needs the lease "
                    "token claim() returned")
            if record["lease_token"] != lease_token:
                raise CoordinatorLivenessClaimLost(
                    f"COORDINATOR_LIVENESS_LOST:{run_id}:owner={record['owner_id']}")
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
            record["released_at"] = _now()
            self._persist(record)


def store_for(run_id: str, *, artifact_base: str | os.PathLike[str],
              clock: Any | None = None, owner_id: str | None = None,
              lease_seconds: float = DEFAULT_LEASE_SECONDS
              ) -> FileCoordinatorLivenessStore:
    return FileCoordinatorLivenessStore(
        liveness_record_path(run_id, artifact_base=artifact_base), clock=clock,
        owner_id=owner_id, lease_seconds=lease_seconds)


# ---- the producer helpers the Coordinator loop calls ----------------------------------
def begin_coordinator_liveness(run_id: str, *, artifact_base: Any = ".",
                               session_id: str = "",
                               lease_seconds: float = DEFAULT_LEASE_SECONDS,
                               clock: Any = None, owner_id: str | None = None,
                               keeper_factory: Any = None,
                               waiter: Any = None) -> Any | None:
    """Publish the lease and start renewing it.  Returns the keeper, or ``None``.

    The cadence is ``lease_keeper.heartbeat_interval_for(lease_seconds)`` -- lease/3,
    floored (``lease_keeper.py:62-74``) -- so ``interval < lease`` stays true by
    construction if the lease is ever reconfigured.

    Returns ``None`` and changes nothing when the record cannot be written, under the
    same discipline ``turn_boundary.bind_session_run`` already applies: a Coordinator's
    run must not fail because a liveness record could not be published.  The consequence
    of a missing record is never a Watchdog that guesses -- it is the ``ABSENT`` read
    below, which DECLINES.
    """
    try:
        store = store_for(run_id, artifact_base=artifact_base, clock=clock,
                          owner_id=owner_id, lease_seconds=lease_seconds)
        claimed = store.claim(run_id, session_id=session_id)
    except (CoordinatorLivenessError, OSError):
        return None
    if keeper_factory is not None:
        return keeper_factory(store, run_id, claimed["lease_token"])
    keeper = LeaseKeeper(store, run_id, claimed["lease_token"],
                         interval_seconds=heartbeat_interval_for(lease_seconds),
                         waiter=waiter)
    return keeper.start()


def end_coordinator_liveness(keeper: Any, run_id: str, *, artifact_base: Any = ".",
                             clock: Any = None, owner_id: str | None = None) -> bool:
    """Retire the beat thread and mark the lease released.  True on a clean shutdown.

    Releasing is a WRITTEN record rather than a deleted file, exactly as
    ``turn_boundary.release_session_run`` is: "this Coordinator let go of that run at
    12:04" is a fact a reader of the run's artifacts can see, and an absent file is not.
    """
    stopped = True
    token = ""
    if keeper is not None:
        token = getattr(keeper, "_lease_token", "")
        stopped = bool(keeper.stop())
    try:
        store = store_for(run_id, artifact_base=artifact_base, clock=clock,
                          owner_id=owner_id)
        if not token:
            record = store.read(run_id)
            token = (record or {}).get("lease_token", "")
        if token:
            store.release(run_id, token)
    except (CoordinatorLivenessError, OSError):
        return False
    return stopped


def liveness_status(run_id: str, *, artifact_base: Any = ".", clock: Any = None) -> str:
    """One member of :data:`LIVENESS_STATUSES`.  Four-valued, never boolean.

    ``ABSENT`` is deliberately not ``EXPIRED`` -- absence of evidence is not evidence of
    death, and every pre-OS-43 run would otherwise look dead.  ``UNREADABLE`` is
    deliberately not ``EXPIRED`` either: it is unknown, and unknown fails closed through
    the snapshot's F1 rather than authorising a recovery.
    """
    lease_clock = clock or SystemLeaseClock()
    try:
        store = store_for(run_id, artifact_base=artifact_base, clock=lease_clock)
        record = store.read(run_id)
    except CoordinatorLivenessError:
        return LIVENESS_UNREADABLE
    except OSError:
        return LIVENESS_UNREADABLE
    if record is None:
        return LIVENESS_ABSENT
    if record["released_at"]:
        # A Coordinator that let the run go is not "alive on it"; it is also not evidence
        # of death.  Released reads as ABSENT, which DECLINES, never as EXPIRED.
        return LIVENESS_ABSENT
    return (LIVENESS_LIVE if record["lease_expires_at"] > lease_clock.time()
            else LIVENESS_EXPIRED)


def liveness_record(run_id: str, *,
                    artifact_base: Any = ".") -> dict[str, Any] | None:
    """The raw record, for an audit row.  Raises on damage; ``None`` when there is none."""
    return store_for(run_id, artifact_base=artifact_base).read(run_id)
