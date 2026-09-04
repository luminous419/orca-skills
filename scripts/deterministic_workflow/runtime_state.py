"""Durable ``RuntimeStatePort`` implementations for crash-safe idempotency.

The store records a *stable intent claim before the external effect is attempted*, so a
process that dies anywhere after the claim can tell, on restart with a brand new adapter,
that the effect may already exist. Records hold only durable external identifiers; runtime
handles never reach the file, mirroring ``state.FORBIDDEN_KEYS``.

Exclusivity
-----------
An atomic *replace* keeps the file from tearing, but it does not make a claim exclusive:
two processes can both read an absent intent and both write ``CLAIMED``.  Every mutation
therefore runs inside a real inter-process critical section --
``lock -> read -> validate -> claim -> persist -> unlock`` -- taken on a sidecar lock file
with :func:`fcntl.flock`.  The ledger is re-read *after* the lock is held, so no decision is
ever made from a pre-lock snapshot.  Lock acquisition has an explicit, injectable timeout
and never waits forever.

Ownership is explicit: a record carries ``owner_id``, ``lease_token``, ``lease_expires_at``
and ``last_heartbeat_at``.  While the owner keeps its lease fresh, another Coordinator that
claims the same intent is refused with :class:`RuntimeStateLeaseHeld` and is expected to act
as an observer (see :meth:`_RuntimeStateStore.observe`) rather than run the intent again.
Lease arithmetic reads an injected clock, so no test needs to sleep.

The lease token is also a *fence*, not merely a renewal ticket.  Exclusivity at claim time
is worthless if the previous executor can still write an effect after a takeover: A blocks
inside a slow ``create_task``, its lease expires, B takes over and starts its own Task, and
A then returns and records *its* external identity into B's record -- two external effects
for one stable intent, arriving through the recovery path instead of the race path.  Every
ownership-sensitive transition (:meth:`_RuntimeStateStore.record_receipt`,
:meth:`_RuntimeStateStore.settle`, :meth:`_RuntimeStateStore.heartbeat`) therefore *requires*
the current lease token and rejects a stale or absent one with
:class:`RuntimeStateLeaseHeld` / :class:`RuntimeStateLeaseRequired`.  Callers carry the token
returned by ``claim`` through to the adapter, so the fence is live in production rather than
an optional argument nobody passes.

Known limitation: ``fcntl.flock`` is POSIX-only.  On Windows this store raises
:class:`RuntimeStateLockUnavailable` at construction rather than degrading to the unlocked
behaviour that made the claim non-exclusive in the first place.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

from .contracts import ActionIntent, SettlementEvent
from .state import FORBIDDEN_KEYS

try:  # POSIX advisory locking; absent on Windows.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

RUNTIME_STATE_SCHEMA_VERSION = "os40.runtime_state.v2"
CLAIMED, EFFECTED, SETTLED = "CLAIMED", "EFFECTED", "SETTLED"
STATUSES = (CLAIMED, EFFECTED, SETTLED)

# Outcomes of ``claim``: reported on the returned copy only, never persisted.
CREATED, RESUMED, ALREADY_SETTLED = "CREATED", "RESUMED", "ALREADY_SETTLED"
CLAIM_OUTCOMES = (CREATED, RESUMED, ALREADY_SETTLED)

# The closed record shape.  Unknown keys, missing keys and wrong types are all rejected:
# a ledger that cannot be validated is never treated as "no prior claim".
RECORD_KEYS = frozenset({
    "intent_id", "command_id", "payload_digest", "run_id", "phase", "role", "round_kind",
    "status", "receipt", "settlement",
    "owner_id", "lease_token", "lease_expires_at", "last_heartbeat_at",
})
_RECORD_STR_KEYS = ("intent_id", "command_id", "payload_digest", "run_id", "phase", "role",
                    "round_kind", "status", "owner_id", "lease_token")
_RECORD_NUM_KEYS = ("lease_expires_at", "last_heartbeat_at")

# The closed *receipt* shape.  A receipt exists for exactly one reason: to name the durable
# external identity of an effect that already exists, so a successor process can find it
# instead of creating a second one.  The key set is therefore closed to the identifiers the
# adapters actually persist -- ``OrcaAdapter`` writes ``task_id``/``dispatch_id``,
# ``FakeAdapter`` and the lookup path write ``external_id``/``intent_id`` -- and anything
# else is a corrupt ledger, not an extension point.
RECEIPT_KEYS = frozenset({"task_id", "dispatch_id", "external_id", "intent_id"})
# At least one of these must be present and non-empty once an effect exists.  An EFFECTED
# record whose receipt names no external identifier is the "crash after create_task left
# nothing to reconcile" strand: it is unrecoverable by construction, so it is rejected on
# read rather than resumed into a dead end.
RECEIPT_IDENTITY_KEYS = ("task_id", "external_id")

# The closed *settlement* shape: exactly the canonical ``SettlementEvent`` vocabulary.  A
# stored settlement is replayed as the phase outcome, so it is held to the same closed
# key set on read as ``contracts.validate_event`` applies on ingress.
SETTLEMENT_KEYS = frozenset(SettlementEvent.__required_keys__)
_SETTLEMENT_STR_KEYS = tuple(sorted(SETTLEMENT_KEYS - {"result"}))

# The stable identity a stored record must still agree with when its intent comes back.
# ``payload_digest`` alone leaves every other identity field forgeable.
IDENTITY_KEYS = ("run_id", "phase", "role", "round_kind", "command_id", "payload_digest")

def default_owner_id() -> str:
    """Ownership identity is the *process*, not the store instance.

    Two stores opened over one ledger inside a single process are one executor resuming its
    own work, and must not lock each other out; two different processes are two Coordinators
    and must.  Host and pid give exactly that boundary with no runtime handle in the file.
    """
    return f"{socket.gethostname()}:pid{os.getpid()}"


DEFAULT_LEASE_SECONDS = 60.0
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_OBSERVE_TIMEOUT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.005
OBSERVE_POLL_SECONDS = 0.05


class RuntimeStateConflict(ValueError):
    """The same stable intent identity was claimed with a different payload."""


class RuntimeStateCorrupt(RuntimeStateConflict):
    """The durable ledger is missing, unknown or malformed at the schema/record level.

    This is deliberately *not* an empty ledger.  Reading a corrupt or incompatible file as
    "no prior claim" is what let every external effect be recreated.
    """


class RuntimeStateLeaseHeld(RuntimeStateConflict):
    """Another owner holds a live lease on this intent; this process must observe, not run."""


class RuntimeStateLeaseRequired(RuntimeStateLeaseHeld):
    """An ownership-sensitive write was attempted without the current lease token.

    It is a subclass of :class:`RuntimeStateLeaseHeld` so every caller that already fails
    closed on a lost lease also fails closed on a missing one; ``lease_token=None`` never
    means "skip the ownership check".
    """


class RuntimeStateLockTimeout(RuntimeStateConflict):
    """The inter-process ledger lock could not be acquired inside the explicit timeout."""


class RuntimeStateObservationTimeout(RuntimeStateConflict):
    """Observing another owner's intent hit its explicit timeout; the wait is never infinite."""


class RuntimeStateLockUnavailable(RuntimeStateConflict):
    """This platform offers no inter-process file lock, so an exclusive claim is impossible."""


class IdempotencyPortRequired(ValueError):
    """No durable ``RuntimeStatePort`` is available for a path that creates effects.

    Durable idempotency is not optional: without a ledger that outlives the process, a
    restart cannot tell "never started" from "already created", so the same stable intent
    would create a second external Task/Dispatch.
    """


def runtime_state_error_code(exc: BaseException) -> str:
    """The stable reason code a ledger failure should terminate under.

    Every message in this module starts with an uppercase code, so a caller can project the
    failure onto a BLOCKED terminal without matching on exception classes.
    """
    head = str(exc).split(":", 1)[0].strip()
    return head if head and head.replace("_", "").isalnum() and head.isupper() else "RUNTIME_STATE_ERROR"


class SystemLeaseClock:
    """The default injectable clock: real wall-clock seconds and a real sleep."""

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ManualLeaseClock:
    """A clock a test advances explicitly, so no lease/observation test ever sleeps."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.current = float(start)

    def time(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += float(seconds)

    def advance(self, seconds: float) -> None:
        self.current += float(seconds)


def resolve_runtime_state(adapter: Any, runtime_state: Any = None) -> Any:
    """Return the one durable port to use, or refuse.

    Resolution order: the explicit argument, else the port the caller wired into the
    adapter.  When both are present they must be the same object -- two ledgers for one
    execution would split the receipts and defeat the guarantee.
    """
    bound = getattr(adapter, "runtime_state", None)
    if runtime_state is not None and bound is not None and runtime_state is not bound:
        raise RuntimeStateConflict(
            "AMBIGUOUS_RUNTIME_STATE: the adapter is bound to a different RuntimeStatePort "
            "than the one supplied; pass one port, or bind the same object to both")
    resolved = runtime_state if runtime_state is not None else bound
    if resolved is None:
        raise IdempotencyPortRequired(
            "IDEMPOTENCY_PORT_REQUIRED: pass runtime_state=..., or bind one to the adapter. "
            "A durable RuntimeStatePort is mandatory on every path that creates external "
            "effects; see deterministic_workflow.runtime_state.FileRuntimeStateStore.")
    return resolved


def _assert_checkpointable(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or FORBIDDEN_KEYS.search(key):
                raise RuntimeStateConflict(f"NON_CHECKPOINTABLE_RUNTIME_STATE:{path}.{key}")
            _assert_checkpointable(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_checkpointable(child, f"{path}[{index}]")
    elif value is not None and type(value) not in (bool, int, float, str):
        raise RuntimeStateConflict(f"NON_CHECKPOINTABLE_RUNTIME_STATE:{path}")


def _validate_receipt(intent_id: str, status: str, receipt: dict[str, Any]) -> None:
    """Validate one closed receipt, or refuse.

    Closed key set, non-empty string values, matching identity, and -- once the effect
    exists -- a durable external identifier.  An empty or identifier-free receipt on an
    ``EFFECTED``/``SETTLED`` record claims "the effect exists" while naming nothing that
    could ever find it, so it is corrupt on read rather than resumable.
    """
    unknown = set(receipt) - RECEIPT_KEYS
    if unknown:
        raise RuntimeStateCorrupt(
            f"MALFORMED_RUNTIME_STATE:{intent_id}:receipt unknown keys {sorted(unknown)}")
    for key in sorted(receipt):
        if type(receipt[key]) is not str or not receipt[key]:
            raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:receipt {key} type")
    if "intent_id" in receipt and receipt["intent_id"] != intent_id:
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:receipt identity")
    if status in (EFFECTED, SETTLED) and not any(receipt.get(key)
                                                 for key in RECEIPT_IDENTITY_KEYS):
        raise RuntimeStateCorrupt(
            f"MALFORMED_RUNTIME_STATE:{intent_id}:receipt external identity missing "
            f"(one of {list(RECEIPT_IDENTITY_KEYS)} is required once the effect exists)")


def _validate_settlement(intent_id: str, settlement: dict[str, Any]) -> None:
    """Validate one closed settlement container, or refuse.

    Identity is checked by :func:`validate_record` against the record; this enforces the
    settlement's own closed vocabulary so a truncated or padded event can never be replayed
    as a phase outcome.
    """
    keys = set(settlement)
    if keys != SETTLEMENT_KEYS:
        raise RuntimeStateCorrupt(
            f"MALFORMED_RUNTIME_STATE:{intent_id}:settlement fields "
            f"unknown={sorted(keys - SETTLEMENT_KEYS)} missing={sorted(SETTLEMENT_KEYS - keys)}")
    for key in _SETTLEMENT_STR_KEYS:
        if type(settlement[key]) is not str or not settlement[key]:
            raise RuntimeStateCorrupt(
                f"MALFORMED_RUNTIME_STATE:{intent_id}:settlement {key} type")
    if not isinstance(settlement["result"], dict):
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:settlement result type")


def validate_record(intent_id: Any, record: Any) -> dict[str, Any]:
    """Validate one closed ledger record, or refuse.

    Every rejection here happens *before* any external effect can be attempted, because the
    ledger is validated on read and the read happens inside the claim's critical section.
    """
    if not isinstance(intent_id, str) or not intent_id:
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:record key {intent_id!r}")
    if not isinstance(record, dict):
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:record container")
    keys = set(record)
    if keys - RECORD_KEYS:
        raise RuntimeStateCorrupt(
            f"MALFORMED_RUNTIME_STATE:{intent_id}:unknown keys {sorted(keys - RECORD_KEYS)}")
    if RECORD_KEYS - keys:
        raise RuntimeStateCorrupt(
            f"MALFORMED_RUNTIME_STATE:{intent_id}:missing keys {sorted(RECORD_KEYS - keys)}")
    for key in _RECORD_STR_KEYS:
        if type(record[key]) is not str:
            raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:{key} type")
    for key in _RECORD_NUM_KEYS:
        if type(record[key]) not in (int, float):
            raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:{key} type")
    if record["intent_id"] != intent_id:
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:identity mismatch")
    if record["status"] not in STATUSES:
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:unknown status")
    receipt, settlement = record["receipt"], record["settlement"]
    if receipt is not None and not isinstance(receipt, dict):
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:receipt shape")
    if settlement is not None and not isinstance(settlement, dict):
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:settlement shape")
    if receipt is not None:
        _validate_receipt(intent_id, record["status"], receipt)
    if settlement is not None:
        _validate_settlement(intent_id, settlement)
    # Status/content coherence: a status that claims more than the record carries is a
    # corrupt ledger, not a recoverable one.
    if record["status"] == CLAIMED and (receipt is not None or settlement is not None):
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:CLAIMED carries content")
    if record["status"] == EFFECTED and (receipt is None or settlement is not None):
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:EFFECTED content")
    if record["status"] == SETTLED and settlement is None:
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:SETTLED without settlement")
    if settlement is not None and settlement.get("intent_id") != intent_id:
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:settlement identity")
    if (settlement is not None and record["command_id"]
            and settlement.get("command_id") != record["command_id"]):
        raise RuntimeStateCorrupt(f"MALFORMED_RUNTIME_STATE:{intent_id}:settlement command")
    if receipt is not None:
        _assert_checkpointable(receipt, "receipt")
    if settlement is not None:
        _assert_checkpointable(settlement, "settlement")
    return record


def validate_ledger(raw: Any) -> dict[str, Any]:
    """Validate the whole durable container, or refuse.  Never returns an empty fallback."""
    if not isinstance(raw, dict):
        raise RuntimeStateCorrupt("MALFORMED_RUNTIME_STATE:top-level container")
    version = raw.get("schema_version")
    if type(version) is not str:
        raise RuntimeStateCorrupt("MALFORMED_RUNTIME_STATE:schema_version missing")
    if version != RUNTIME_STATE_SCHEMA_VERSION:
        raise RuntimeStateCorrupt(
            f"INCOMPATIBLE_RUNTIME_STATE:{version} != {RUNTIME_STATE_SCHEMA_VERSION}")
    if set(raw) - {"schema_version", "records"}:
        raise RuntimeStateCorrupt(
            f"MALFORMED_RUNTIME_STATE:unknown top-level keys "
            f"{sorted(set(raw) - {'schema_version', 'records'})}")
    records = raw.get("records")
    if not isinstance(records, dict):
        raise RuntimeStateCorrupt("MALFORMED_RUNTIME_STATE:records container")
    for intent_id, record in records.items():
        validate_record(intent_id, record)
    return records


class _RuntimeStateStore:
    """Shared claim/receipt/settlement bookkeeping over a pluggable record map.

    Subclasses supply the critical section (:meth:`_locked`) and the raw read/write; every
    public mutation below runs the full ``lock -> read -> validate -> decide -> persist``
    sequence, so no decision is taken from a snapshot read before the lock.
    """

    clock: Any = SystemLeaseClock()
    owner_id: str = "owner"
    lease_seconds: float = DEFAULT_LEASE_SECONDS

    def _read(self) -> dict[str, Any]:
        raise NotImplementedError

    def _write(self, records: dict[str, Any]) -> None:
        raise NotImplementedError

    @contextmanager
    def _locked(self) -> Iterator[None]:
        raise NotImplementedError

    def _new_lease(self, record: dict[str, Any], now: float) -> None:
        record["owner_id"] = self.owner_id
        record["lease_token"] = secrets.token_hex(16)
        record["lease_expires_at"] = now + self.lease_seconds
        record["last_heartbeat_at"] = now

    # ---- reads (validated: a corrupt ledger fails closed before any external effect) ----

    def get_receipt(self, intent_id: str) -> dict[str, Any] | None:
        with self._locked():
            record = self._read().get(intent_id)
            return deepcopy(record) if record is not None else None

    def get_settlement(self, intent_id: str) -> SettlementEvent | None:
        with self._locked():
            record = self._read().get(intent_id) or {}
            event = record.get("settlement")
            return deepcopy(event) if event else None

    # ---- exclusive claim ----

    def claim(self, intent: ActionIntent) -> dict[str, Any]:
        """Take exclusive ownership of a stable intent, or refuse.

        The returned copy carries a non-persisted ``claim_outcome``:

        ``CREATED``          nothing existed; this process owns the first attempt and is the
                             only one permitted to create the external effect.
        ``RESUMED``          a record existed whose lease had expired (or is our own); the
                             external effect may already exist, so the caller must recover it
                             rather than re-run it.
        ``ALREADY_SETTLED``  the settlement is durable; nothing external is needed.

        Raises :class:`RuntimeStateLeaseHeld` while another owner's lease is live.
        """
        intent_id = intent["intent_id"]
        with self._locked():
            records = self._read()
            existing = records.get(intent_id)
            now = self.clock.time()
            if existing is None:
                record = {
                    "intent_id": intent_id, "command_id": intent["command_id"],
                    "payload_digest": intent["payload_digest"], "run_id": intent["run_id"],
                    "phase": intent["phase"], "role": intent["role"],
                    "round_kind": intent["round_kind"], "status": CLAIMED,
                    "receipt": None, "settlement": None,
                    "owner_id": "", "lease_token": "",
                    "lease_expires_at": 0.0, "last_heartbeat_at": 0.0,
                }
                self._new_lease(record, now)
                validate_record(intent_id, record)
                records[intent_id] = record
                self._write(records)
                return {**deepcopy(record), "claim_outcome": CREATED}
            # ``validate_record`` proves a record is internally coherent but knows nothing
            # about the intent now asking for it.  The stored identity must therefore be
            # re-checked in full here: a digest match alone leaves run/phase/role/round/
            # command forgeable, and a forged record would hand this intent someone else's
            # external effect.
            mismatched = [key for key in IDENTITY_KEYS if existing.get(key) != intent[key]]
            if mismatched:
                raise RuntimeStateConflict(f"IDEMPOTENCY_CONFLICT:{intent_id}:{mismatched}")
            if existing["status"] == SETTLED:
                return {**deepcopy(existing), "claim_outcome": ALREADY_SETTLED}
            if existing["owner_id"] != self.owner_id and existing["lease_expires_at"] > now:
                raise RuntimeStateLeaseHeld(
                    f"LEASE_HELD:{intent_id}:owner={existing['owner_id']}:"
                    f"expires_at={existing['lease_expires_at']}")
            self._new_lease(existing, now)
            self._write(records)
            return {**deepcopy(existing), "claim_outcome": RESUMED}

    def heartbeat(self, intent_id: str, lease_token: str) -> dict[str, Any]:
        """Extend this owner's lease.  A stale token is refused, never silently renewed."""
        with self._locked():
            records = self._read()
            record = records.get(intent_id)
            if record is None:
                raise RuntimeStateConflict(f"UNCLAIMED_INTENT:{intent_id}")
            if record["lease_token"] != lease_token or record["owner_id"] != self.owner_id:
                raise RuntimeStateLeaseHeld(f"LEASE_LOST:{intent_id}")
            now = self.clock.time()
            record["last_heartbeat_at"] = now
            record["lease_expires_at"] = now + self.lease_seconds
            self._write(records)
            return deepcopy(record)

    def release(self, intent_id: str, lease_token: str) -> None:
        """Drop this owner's lease so a successor need not wait for it to expire."""
        with self._locked():
            records = self._read()
            record = records.get(intent_id)
            if record is None or record["lease_token"] != lease_token:
                return
            record["lease_expires_at"] = self.clock.time()
            self._write(records)

    def observe(self, intent_id: str, *,
                timeout_seconds: float = DEFAULT_OBSERVE_TIMEOUT_SECONDS,
                poll_seconds: float = OBSERVE_POLL_SECONDS) -> dict[str, Any] | None:
        """Watch an intent another Coordinator owns, with an explicit, finite timeout.

        Returns the SETTLED record when the owner finishes, or ``None`` when the owner's
        lease lapses (the caller may then attempt takeover).  Raises
        :class:`RuntimeStateObservationTimeout` at the deadline: an observer never waits
        forever, so a silently killed owner cannot strand a second Coordinator.
        """
        if timeout_seconds <= 0:
            raise ValueError("observe() requires a positive timeout")
        deadline = self.clock.time() + timeout_seconds
        while True:
            with self._locked():
                record = deepcopy(self._read().get(intent_id))
            if record is None:
                return None
            if record["status"] == SETTLED:
                return record
            if record["lease_expires_at"] <= self.clock.time():
                return None
            if self.clock.time() >= deadline:
                raise RuntimeStateObservationTimeout(
                    f"OBSERVATION_TIMEOUT:{intent_id}:owner={record['owner_id']}")
            self.clock.sleep(min(poll_seconds, max(deadline - self.clock.time(), 0.0)))

    # ---- effect bookkeeping ----

    def _fenced(self, records: dict[str, Any], intent_id: str,
                lease_token: Any) -> dict[str, Any]:
        """Return the record only if the caller still holds the lease that owns it.

        This is the fence.  A lease that has been taken over has had its token rotated by
        :meth:`_new_lease`, so the previous executor -- which may be blocked inside a slow
        external call -- presents a token that no longer matches and is refused *before* it
        can write an effect the current owner knows nothing about.  There is deliberately no
        "no token supplied" branch: an absent token is a missing capability, not permission
        to skip the check, so it fails closed exactly like a stale one.
        """
        record = records.get(intent_id)
        if record is None:
            raise RuntimeStateConflict(f"UNCLAIMED_INTENT:{intent_id}")
        if not isinstance(lease_token, str) or not lease_token:
            raise RuntimeStateLeaseRequired(
                f"LEASE_REQUIRED:{intent_id}: an ownership-sensitive write needs the "
                "lease token returned by claim()/heartbeat()")
        if record["lease_token"] != lease_token or record["owner_id"] != self.owner_id:
            raise RuntimeStateLeaseHeld(
                f"LEASE_LOST:{intent_id}:owner={record['owner_id']}")
        return record

    def record_receipt(self, intent_id: str, receipt: dict[str, Any],
                       lease_token: str) -> dict[str, Any]:
        _assert_checkpointable(receipt, "receipt")
        with self._locked():
            records = self._read()
            record = self._fenced(records, intent_id, lease_token)
            if record["status"] == CLAIMED:
                record["status"] = EFFECTED
            record["receipt"] = deepcopy(receipt)
            validate_record(intent_id, record)
            self._write(records)
            return deepcopy(record)

    def settle(self, intent_id: str, event: SettlementEvent,
               lease_token: str) -> dict[str, Any]:
        _assert_checkpointable(dict(event), "settlement")
        with self._locked():
            records = self._read()
            record = self._fenced(records, intent_id, lease_token)
            stored = record.get("settlement")
            if stored is not None and stored != event:
                raise RuntimeStateConflict(f"SETTLEMENT_CONFLICT:{intent_id}")
            record["settlement"] = deepcopy(dict(event))
            record["status"] = SETTLED
            validate_record(intent_id, record)
            self._write(records)
            return deepcopy(record)


class InMemoryRuntimeStateStore(_RuntimeStateStore):
    """Process-local store; only crash-safe for tests that share the instance.

    It honours the same ownership contract so a single-process test exercises exactly the
    code path the file store does, but its lock is a thread lock: it offers no inter-process
    exclusion and must never stand in for :class:`FileRuntimeStateStore` in production.
    """

    def __init__(self, *, clock: Any | None = None, owner_id: str | None = None,
                 lease_seconds: float = DEFAULT_LEASE_SECONDS) -> None:
        self._records: dict[str, Any] = {}
        self._mutex = threading.RLock()
        self.clock = clock or SystemLeaseClock()
        self.owner_id = owner_id or default_owner_id()
        self.lease_seconds = float(lease_seconds)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._mutex:
            yield

    def _read(self) -> dict[str, Any]:
        for intent_id, record in self._records.items():
            validate_record(intent_id, record)
        return self._records

    def _write(self, records: dict[str, Any]) -> None:
        for intent_id, record in records.items():
            validate_record(intent_id, record)
        self._records = records


class FileRuntimeStateStore(_RuntimeStateStore):
    """JSON-file store that survives the process, written atomically under a real lock."""

    def __init__(self, path: str | os.PathLike[str], *, clock: Any | None = None,
                 owner_id: str | None = None, lease_seconds: float = DEFAULT_LEASE_SECONDS,
                 lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        if fcntl is None:  # pragma: no cover - exercised only on non-POSIX hosts
            raise RuntimeStateLockUnavailable(
                "RUNTIME_STATE_LOCK_UNAVAILABLE: fcntl.flock is required for an exclusive "
                "inter-process claim; this store is POSIX-only by design and refuses to run "
                "unlocked, which is the behaviour that allowed duplicate external effects.")
        self.path = Path(path)
        self.lock_path = Path(f"{self.path}.lock")
        self.clock = clock or SystemLeaseClock()
        self.owner_id = owner_id or default_owner_id()
        self.lease_seconds = float(lease_seconds)
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self._depth = 0

    @contextmanager
    def _locked(self) -> Iterator[None]:
        # Re-entrant within one process so a composed operation does not deadlock on itself;
        # ``flock`` is per open file description, so the outermost frame owns the handle.
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = self.clock.time() + self.lock_timeout_seconds
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if self.clock.time() >= deadline:
                        raise RuntimeStateLockTimeout(
                            f"RUNTIME_STATE_LOCK_TIMEOUT:{self.path} after "
                            f"{self.lock_timeout_seconds}s") from None
                    self.clock.sleep(LOCK_POLL_SECONDS)
            self._depth = 1
            try:
                yield
            finally:
                self._depth = 0
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _read(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise RuntimeStateCorrupt(f"UNREADABLE_RUNTIME_STATE:{exc}") from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeStateCorrupt(f"UNREADABLE_RUNTIME_STATE:{exc}") from exc
        return validate_ledger(raw)

    def _write(self, records: dict[str, Any]) -> None:
        for intent_id, record in records.items():
            validate_record(intent_id, record)
        payload = {"schema_version": RUNTIME_STATE_SCHEMA_VERSION, "records": records}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=f".{self.path.name}.", delete=False)
        try:
            with handle:
                json.dump(payload, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
