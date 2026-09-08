"""OS-43 Tier-2 run-scoped recovery lease and attempt ledger for a NON-paused run.

One durable file per run, ``.recovery_state.json``, over the same ``durable_store``
discipline (flock on a sidecar + ``fsync`` + ``os.replace``) every other Tier-2 record in
this package uses.  It exists for exactly one reason: a run that is ACTIVE and stalled has
**no** pause record, so ``pause_store``'s run-scoped lease -- the thing that makes two
concurrent recoveries produce exactly one winner -- has nothing to key on.  This record is
that lease for that branch.

It is also the run's single **execution authority**.  A recovery lease that only recovery
takes serialises Watchdog-vs-Watchdog and nothing else, and the Coordinator's ordinary
execution path took no run-scoped authority at all -- so a Coordinator that revived after
a Watchdog had observed it stale could drive the same checkpoint concurrently, and the
liveness record cannot close that window because it is an OBSERVATION
(``coordinator_liveness.py:164-172``), not a mutual-exclusion primitive.  Both parties now
pass through :meth:`FileRecoveryStateStore.claim`, and ``owner_kind`` records which one
won, so a challenger can tell an owned-and-running run from a recovery attempt it may
legitimately take over -- atomically, inside the claim, never by looking again afterwards.

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

RECOVERY_RECORD_SCHEMA_VERSION = "os43.recovery_state.v3"
RECOVERY_RECORD_FILENAME = ".recovery_state.json"

#: WHICH KIND of claimant holds the run-scoped EXECUTION AUTHORITY.  It is a ROLE, never
#: an identity: WHO the holder is, is :data:`FileRecoveryStateStore.claimant_id`, and the
#: two are deliberately separate.  Identifying a claimant by anything a peer can also be
#: -- the process (``runtime_state.default_owner_id``), or the process and the role
#: together -- reads a DISTINCT concurrent actor as the holder resuming its own work: the
#: claim returns ``RESUMED``, rotates the holder's token out from under it, and lets BOTH
#: parties past.  That is true of a Coordinator and a Watchdog in one process, and it is
#: equally true of two Watchdogs in one process, because ``watchdog_supervisor`` is
#: runtime-neutral and callable in-process (CON-5).
#:
#: The kind is recorded because the two roles are not symmetric and a challenger must be
#: able to tell them apart ATOMICALLY, inside the claim, rather than by looking at the
#: world a second time:
#:
#: * ``coordinator`` -- an ordinary Coordinator execution (``launcher.execute_state``).  A
#:   live one is not a crashed peer waiting to be taken over; it is the run's owner, so a
#:   challenger fails closed immediately instead of observing.
#: * ``recovery``    -- an engine recovery attempt (``recovery_runtime._recover_active``).
#:   A live one may still be a process that dies, so the Watchdog-vs-Watchdog
#:   observe-then-take-over ladder is preserved exactly as delivered.
#:
#: ``""`` is a record that has never been claimed.
OWNER_KIND_COORDINATOR = "coordinator"
OWNER_KIND_RECOVERY = "recovery"
OWNER_KINDS = ("", OWNER_KIND_COORDINATOR, OWNER_KIND_RECOVERY)

#: The ONE stable code a refused claimant carries, whichever party it is.  It is a
#: refusal, never a retry hint and never a wait: the holder is alive and owns the run.
EXECUTION_AUTHORITY_HELD = "EXECUTION_AUTHORITY_HELD"

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
    "run_id", "status", "owner_id", "claimant_id", "owner_kind", "lease_token",
    "lease_seconds",
    "lease_expires_at", "last_heartbeat_at", "created_at", "updated_at", "thread_id",
    "checkpoint_ns", "attempts",
)
RECOVERY_ATTEMPT_KEYS = (
    "recovery_id", "recovery_kind", "stage", "head_before", "head_after", "outcome",
    "code", "actor_id", "opened_at", "promoted_at",
)

DEFAULT_LEASE_SECONDS = 60.0


def new_claimant_id(owner_id: str) -> str:
    """A fresh identity for ONE live execution attempt.

    The claimant is the ATTEMPT, not the process and not the role.  ``owner_id`` names
    the process (``runtime_state.default_owner_id``) and ``owner_kind`` names the role;
    both are things a concurrent peer can equally be, so neither -- nor the two together
    -- can answer "is the holder ME?".  Two Watchdog recoveries in one process share both
    and are still two actors that must serialise (CON-5: ``watchdog_supervisor`` is
    runtime-neutral and callable in-process).

    Minted per store instance, and each live execution attempt constructs its own store:
    ``launcher._execution_authority`` builds one per ``execute_state`` and
    ``recovery_runtime._recover_active`` builds one per ``recover_stalled_run``.  The
    random half makes it unique WITHOUT a registry or a second lock; the ``owner_id``
    prefix keeps the durable record and every refusal message legible about WHERE the
    holder lives.  It is written into the record, so it outlives the process that minted
    it and a successor can name the claimant it took the run from.
    """
    return f"{owner_id}/{secrets.token_hex(8)}"


class RecoveryStoreError(ValueError):
    """The recovery store refused an operation under its own closed contract."""


class RecoveryRecordCorrupt(RecoveryStoreError):
    """The recovery record is missing a field, carries an unknown one, or fails its schema.

    Never read as "no prior attempt": an unreadable record is unknown, not empty.
    """


class RecoveryClaimHeld(RecoveryStoreError):
    """Another owner holds a live recovery lease on this run; this process must observe."""


class RecoveryAuthorityHeld(RecoveryClaimHeld):
    """A LIVE holder owns this run's execution authority, and there is nothing to observe.

    Distinct from :class:`RecoveryClaimHeld` because the two demand opposite behaviour and
    a caller must not have to guess which it got:

    * ``RecoveryClaimHeld`` says "another *recovery* attempt is in flight".  It may be a
      process that dies, so the delivered observe-then-take-over ladder still applies and
      Watchdog-vs-Watchdog is unchanged.
    * ``RecoveryAuthorityHeld`` says "this run is OWNED and running".  The loser must
      NEITHER wait NOR proceed: waiting is the same second look that opened the window in
      the first place, and proceeding is the duplicate execution.  It fails closed with
      :data:`EXECUTION_AUTHORITY_HELD` and performs nothing.

    It subclasses ``RecoveryClaimHeld`` so no existing ``except`` clause stops seeing a
    held claim; every caller that must tell them apart catches this one FIRST.
    """


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


#: The canonical run-rooted checkpoint store's file name, transcribed from
#: ``launcher.CHECKPOINT_STORE_FILENAME`` / ``recovery_runtime.WORKFLOW_CHECKPOINT_FILENAME``
#: rather than imported, because both of those import this module.
WORKFLOW_CHECKPOINT_FILENAME = ".workflow_checkpoints.json"


def authority_path_for_checkpoint(checkpoint_path: str | os.PathLike[str]) -> Path:
    """The execution authority that guards ONE checkpoint store.

    The authority and the store it guards must be addressable from each other, because
    the thing being serialised is "who may advance THIS checkpoint".  For the canonical
    run-rooted layout -- ``<base>/artifacts/runs/<run_id>/.workflow_checkpoints.json``,
    the one ``recovery_runtime.checkpoint_path`` and ``turn_boundary`` both use -- this
    returns exactly :func:`recovery_record_path` for that run, so the Coordinator and the
    Watchdog meet on ONE record without either of them having to be told where it is.

    A store somewhere else (an explicit ``--checkpoint-store``, or ``CHECKPOINT_DIR_ENV``)
    is not discoverable by the Watchdog at all, so its authority is local to that store
    and is named after it -- never a directory-wide file two unrelated runs would share.
    """
    path = Path(checkpoint_path)
    if path.name == WORKFLOW_CHECKPOINT_FILENAME:
        return path.with_name(RECOVERY_RECORD_FILENAME)
    return path.with_name(f"{path.name}{RECOVERY_RECORD_FILENAME}")


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
    for key in ("owner_id", "claimant_id", "lease_token", "created_at", "updated_at",
                "thread_id", "checkpoint_ns"):
        if not isinstance(record[key], str):
            raise RecoveryRecordCorrupt(f"RECOVERY_RECORD_CORRUPT:{run_id}: {key}")
    if record["owner_kind"] not in OWNER_KINDS:
        raise RecoveryRecordCorrupt(
            f"RECOVERY_RECORD_CORRUPT:{run_id}: owner_kind {record['owner_kind']!r}")
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
        "run_id": run_id, "status": "ACTIVE", "owner_id": "", "claimant_id": "",
        "owner_kind": "", "lease_token": "",
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
                 owner_id: str | None = None, claimant_id: str | None = None,
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
        #: WHO this claimant is -- one live execution attempt.  Never derived from
        #: ``owner_id`` or ``owner_kind``: see :func:`new_claimant_id`.  Supplying it
        #: explicitly is how a SUCCESSOR PROCESS re-presents an identity it already had;
        #: it never makes two concurrent attempts one claimant unless a caller says so.
        self.claimant_id = claimant_id or new_claimant_id(self.owner_id)
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
        # BOTH halves, and neither on its own.  The TOKEN is the capability -- minted per
        # claim, rotated by every later one, so a superseded claimant cannot match it
        # however it identifies itself.  The CLAIMANT is who the record says holds the
        # run; checking it means a token that reached some other attempt is still not
        # permission to write, which is the whole reason the claimant is per-attempt
        # rather than per-process or per-role.
        if record["lease_token"] != lease_token \
                or record["claimant_id"] != self.claimant_id:
            raise RecoveryClaimLost(
                f"RECOVERY_CLAIM_LOST:{run_id}:owner={record['owner_id']}:"
                f"claimant={record['claimant_id'] or 'none'}")
        return record

    def _continues(self, record: Mapping[str, Any], continuation_token: str) -> bool:
        """Is this claim an EXPLICIT continuation of the claim in this record?

        The question the old code asked here was "does the holder LOOK like me?", and the
        answer was inferred from identity: same process (then same process and same kind)
        therefore same actor.  That inference is what admitted a second concurrent actor
        twice over, because every identity a claimant can be COMPARED on is one a peer can
        also HOLD.  It is gone.  Resumption is now a capability the resumer must PRESENT:
        the lease token the earlier claim minted and returned to it.  Only the actor that
        holds it can continue that claim, and holding it is a fact about the caller rather
        than a resemblance the store guesses at.

        A token that this record does not recognise is never "not a continuation, carry
        on": it is a claim this caller believed it held and no longer does, so it is
        refused as :class:`RecoveryClaimLost` by the caller of this predicate.
        """
        return bool(continuation_token) and record["lease_token"] == continuation_token

    # -- port surface ------------------------------------------------------------
    def read(self, run_id: str) -> dict[str, Any] | None:
        with self._section.locked():
            record = self._read(run_id)
            return deepcopy(record) if record is not None else None

    def claim(self, run_id: str, *, thread_id: str = "", checkpoint_ns: str = "",
              now_iso: str = "", owner_kind: str = OWNER_KIND_RECOVERY,
              takeover: bool = True, continuation_token: str = "") -> dict[str, Any]:
        """Take the run-scoped EXECUTION AUTHORITY, or report why nothing is to be done.

        One atomic ``lock -> read -> validate -> claim -> persist`` section, so two
        claimants produce exactly one ``CREATED`` and the claim precedes every effect.
        Both a Coordinator advancing the run in the ordinary way and an engine recovery
        pass through THIS section, which is what serialises them against each other:
        neither can reach ``graph.invoke`` without having won it.

        The CLAIMANT IS THE ATTEMPT (:attr:`claimant_id`), not the process and not the
        role.  A live holder is therefore refused to EVERY other attempt -- a Coordinator
        against a Watchdog, and equally a Watchdog against another Watchdog in the same
        process -- because no two concurrent attempts can carry one identity.

        Resuming a claim is a separate thing, and it is EXPLICIT: the resumer presents in
        ``continuation_token`` the lease token that claim minted and returned to it.  A
        presented token that this record no longer recognises raises
        :class:`RecoveryClaimLost` rather than falling through to an ordinary claim: the
        caller believed it held this run, and being wrong about that is a refusal, never a
        licence to take it afresh.  There is deliberately no way to resume by RESEMBLING
        the holder; "same identity therefore same actor" is the inference this replaces.

        ``owner_kind`` records WHICH ROLE won, and ``takeover`` declares whether
        this claimant is entitled to the observe-then-take-over ladder at all.  A
        Coordinator sets ``takeover=False``: it has a run in hand and cannot wait, so a
        live holder is an immediate, final refusal.  A refusal that must not be waited on
        is raised as :class:`RecoveryAuthorityHeld`; every other live holder keeps raising
        :class:`RecoveryClaimHeld` exactly as delivered.
        """
        if owner_kind not in OWNER_KINDS or not owner_kind:
            raise RecoveryStoreError(
                f"unknown execution-authority owner kind: {owner_kind!r}")
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
            continuing = self._continues(record, continuation_token)
            if continuation_token and not continuing:
                # Presented, and unrecognised.  The claim this caller meant to continue is
                # gone; taking a NEW one here is exactly the supersession this record
                # exists to prevent, so it fails closed on the same code every other lost
                # capability reports.
                raise RecoveryClaimLost(
                    f"RECOVERY_CLAIM_LOST:{run_id}:owner={record['owner_id']}:"
                    f"claimant={record['claimant_id'] or 'none'}: the continuation token "
                    "presented is not this record's")
            if continuing and record["owner_kind"] != owner_kind:
                # A continuation carries the claim FORWARD; it cannot re-cast the role the
                # record already published, because a challenger decides whether it may
                # observe from that role.
                raise RecoveryStoreError(
                    f"a continuation may not change owner_kind: record holds "
                    f"{record['owner_kind']!r}, claim asks for {owner_kind!r}")
            if not continuing and record["claimant_id"] \
                    and record["lease_expires_at"] > now:
                # The holder is ALIVE and it is NOT this claimant.  Whether that is
                # something to observe or something to fail closed on is decided HERE,
                # inside the same critical section that read it -- never by a second look
                # afterwards.
                if not takeover or record["owner_kind"] == OWNER_KIND_COORDINATOR:
                    raise RecoveryAuthorityHeld(
                        f"{EXECUTION_AUTHORITY_HELD}:{run_id}:"
                        f"owner={record['owner_id']}:"
                        f"claimant={record['claimant_id']}:"
                        f"owner_kind={record['owner_kind'] or 'unknown'}:"
                        f"expires_at={record['lease_expires_at']}")
                raise RecoveryClaimHeld(
                    f"RECOVERY_CLAIM_HELD:{run_id}:owner={record['owner_id']}:"
                    f"claimant={record['claimant_id']}:"
                    f"expires_at={record['lease_expires_at']}")
            outcome = CREATED if not record["claimant_id"] else RESUMED
            record["owner_id"] = self.owner_id
            record["claimant_id"] = self.claimant_id
            record["owner_kind"] = owner_kind
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

    def fence(self, run_id: str, lease_token: str) -> dict[str, Any]:
        """Validate the fencing token ATOMICALLY, immediately before an irreversible step.

        This is deliberately not "re-read the state and decide again".  It reads nothing
        about the world: it compares the token this owner was handed by ``claim`` against
        the token the record currently carries, inside the record's own critical section,
        and raises the moment they differ.  Because ``claim`` ROTATES the token, a
        superseded owner can never pass it -- which is what makes it a fence rather than a
        second look.  The record's ``claimant_id`` is compared beside the token, so a token
        that reached some OTHER live execution attempt is not permission to write either.

        Called before the graph transition and before every external side effect, so a
        lease lost mid-flight stops this owner at the next irreversible step instead of
        letting it finish work a successor already owns.
        """
        with self._section.locked():
            return deepcopy(self._fenced(self._read(run_id), run_id, lease_token))

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
              claimant_id: str | None = None,
              lease_seconds: float = DEFAULT_LEASE_SECONDS) -> FileRecoveryStateStore:
    return FileRecoveryStateStore(recovery_record_path(run_id,
                                                       artifact_base=artifact_base),
                                  clock=clock, owner_id=owner_id,
                                  claimant_id=claimant_id,
                                  lease_seconds=lease_seconds)
