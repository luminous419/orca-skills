"""OS-37 N9.  The append-only execution journal.  **A DERIVED EVENT LOG, NOT AN AUTHORITY.**

This module holds no authority and OS-37 adds none.  Four already exist in the engine and
all four are reused unchanged:

===========================================  ==========================================
``runtime_state.claim`` / ``record_receipt`` the pre-effect durable claim on the stable
/ ``settle``                                 ``intent_id``, the lease token that fences
                                             every ownership-sensitive write, and the
                                             intent's idempotent settlement
``recovery_store.claim``                     run-level execution authority and takeover
``pause_runtime.resume_run``                 one-shot resume of a paused run
``lease_keeper.LeaseKeeper``                 lease renewal across a long blocking call
===========================================  ==========================================

So this file **grants no exclusivity, mints no token or lease, implements no takeover,
holds no fence VALUE, and is never consulted to decide whether this process may act.**
Every record names the authority it was derived from, in ``derived_from``, and that field
may never be ``"journal"``.  There is deliberately no ``CLAIMED`` record kind: the claim is
``runtime_state``'s, taken at ``executor.py``'s ``runtime_state.claim(intent)`` *before*
``adapter.start`` is called, and duplicating it here would be a second pre-effect claim on
the stable intent -- forbidden by the approved plan's binding B-1 whatever it were called.

The identity fence is compared here and OWNED elsewhere.  ``admit``'s S-5 reads the fence
value from the runtime-state receipt's ``external_id`` -- written under the lease token by
``record_receipt`` -- and only *compares* the incoming record against it.  Because the value
lives in the ledger, deleting or truncating this journal cannot widen what is admissible;
it can only make the journal unreadable, which RAISES.

``admit`` decides whether an observed EVENT is recorded.  It deliberately does not decide
whether the INTENT is settled: that is ``runtime_state.settle`` and its
``SETTLEMENT_CONFLICT``, and S-0 *reads* that verdict rather than re-deciding it.

The sibling lock file serialises APPENDS TO THIS ONE FILE and is not an authority over
anything.  While it is held, another process can still claim an intent, claim a run, resume
a paused run, signal a process and read every one of those -- only a second appender to
this file waits.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, TypedDict

from .contracts import OwnershipAxes, SettlementEvent, validate_axes

#: The six record kinds.  **There is no CLAIMED.**  Every one of these describes something
#: that was OBSERVED; none of them confers permission.
#: `DELIVERY_INTENT` (iteration 3, USER DIRECTIVE D-D.1) records the spawn request AND the
#: prompt digest as ONE ATOMIC record, BEFORE the process exists.  It is deliberately NOT a
#: claim, NOT a lease and NOT a fence -- AC-37-20's single claim authority stays
#: `runtime_state`, and this record is appended AFTER the existing claim and BEFORE the
#: fork.  `CLAIMED` remains absent from this vocabulary for exactly that reason.
RECORD_KINDS = ("SPAWN_OBSERVED", "RECEIPT_OBSERVED", "EVENT", "SETTLEMENT_OBSERVED",
                "REFUSED", "RELEASED", "DELIVERY_INTENT")

#: The authority a record was derived FROM.  `journal` is deliberately not a member: a
#: record derived from this file would be a record deriving its authority from itself.
#: `driver` is the authority for a `DELIVERY_INTENT`: the argv and the prompt digest are
#: composed by the driver from the profile, and neither exists anywhere else at the instant
#: the record is written -- the process does not exist yet.
DERIVED_FROM = ("runtime_state", "recovery_store", "pty", "process_table", "capture",
                "driver")

#: `admit`'s three outcomes, and its refusal codes.
ADMIT_OUTCOMES = ("admitted", "no_effect", "refused")
SETTLEMENT_CONFLICT = "SETTLEMENT_CONFLICT"
SETTLEMENT_IDENTITY_MISMATCH = "SETTLEMENT_IDENTITY_MISMATCH"
FOREIGN_INCARNATION = "FOREIGN_INCARNATION"

#: What `recover_handle` may answer.  `listing_candidate` MUST NOT be acted on; it exists
#: so an abandon report can name the resource it could not verify.
HANDLE_RECOVERY = ("listing_verified", "listing_candidate", "not_listed")


class JournalUnreadable(RuntimeError):
    """A digest failed or the file could not be read.  Unknown is never empty.

    Raised rather than degrading to "no records".  A caller that receives an empty journal
    where it should receive this exception concludes "no dispatch is open", which is the
    single most dangerous wrong answer this module can give.
    """


class JournalRecord(TypedDict):
    seq: int
    written_at: str
    writer: str
    run_id: str
    intent_id: str
    dispatch_id: str
    task_id: str
    session_id: str
    process_incarnation: str        # with session_id, THE IDENTITY FENCE (compared, not owned)
    kind: str
    derived_from: str
    event: str
    state: str
    lost_reason: str
    axes: OwnershipAxes
    source_vocabulary: dict[str, Any]
    outcome: str
    message_id: str
    reported_by: str
    digest: str


_RECORD_KEYS = tuple(JournalRecord.__annotations__)
#: The fields that are NOT strings, so a missing one must raise rather than default to "".
#: `axes` in particular: defaulting it would let a record through with no ownership axes at
#: all, and `validate_axes` exists precisely to refuse that.
_NON_STRING_KEYS = frozenset({"seq", "axes", "source_vocabulary"})


def journal_path(artifact_base: str | os.PathLike[str], run_id: str) -> Path:
    return Path(artifact_base) / "runs" / run_id / "standalone" / "journal.ndjson"


def record_digest(record: Mapping[str, Any]) -> str:
    """sha256 over the record MINUS its own ``digest`` field."""
    payload = {key: value for key, value in record.items() if key != "digest"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def make_record(**fields: Any) -> JournalRecord:
    """Build and validate one record, or raise.

    ``derived_from`` is required and is validated against a set that excludes ``journal``.
    A record that could not name where its content came from would be a record asserting
    its own authority.
    """
    record: dict[str, Any] = {key: "" for key in _RECORD_KEYS}
    record["seq"] = 0
    record["axes"] = {"settlement": "unknown", "worker_resource": "unsupervised",
                      "process_liveness": "unverifiable", "cleanup_authority": "unknown"}
    record["source_vocabulary"] = {}
    unknown = set(fields) - set(_RECORD_KEYS)
    if unknown:
        raise ValueError(f"unknown journal fields {sorted(unknown)!r}")
    record.update(fields)
    if record["kind"] not in RECORD_KINDS:
        raise ValueError(f"kind {record['kind']!r} is not one of {RECORD_KINDS!r}; "
                         "note that CLAIMED is deliberately absent -- the claim is "
                         "runtime_state's")
    if record["derived_from"] not in DERIVED_FROM:
        raise ValueError(
            f"derived_from {record['derived_from']!r} is not one of {DERIVED_FROM!r}; "
            "a record must name the authority it was derived from, and it may never be "
            "this file")
    if record["state"] == "LOST" and not record["lost_reason"]:
        raise ValueError("a LOST record carries a lost_reason")
    record["axes"] = validate_axes(record["axes"])
    record["digest"] = record_digest(record)
    return record  # type: ignore[return-value]


class ExecutionJournal:
    """One append-only NDJSON file per run, plus a sibling append lock.

    Nothing is ever rewritten, truncated or deleted.
    """

    def __init__(self, artifact_base: str | os.PathLike[str], run_id: str, *,
                 writer: str = "") -> None:
        self.artifact_base = Path(artifact_base)
        self.run_id = run_id
        self.path = journal_path(artifact_base, run_id)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = writer or _default_writer()

    # -- the APPEND lock: for this file, and for nothing else ---------------------------
    @contextmanager
    def _append_lock(self) -> Iterator[None]:
        """Serialise appends to THIS FILE.  Not a run lock and not an intent lock.

        While this is held another process can still take either real claim, resume a run,
        signal a process and read anything; only a second appender to this file waits.  That
        is asserted by a test rather than promised here.
        """
        handle = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                os.close(handle)

    # -- read ----------------------------------------------------------------------------
    def rows(self) -> tuple[JournalRecord, ...]:
        """Every record, in ``seq`` order.  RAISES if any digest fails.

        A record whose digest does not verify makes the WHOLE journal unreadable.  Skipping
        the bad record would silently hide exactly the tampering the digest exists to
        detect.
        """
        if not self.path.exists():
            return ()
        try:
            raw = self.path.read_text()
        except OSError as exc:
            raise JournalUnreadable(f"{self.path}: {exc}") from exc
        out: list[JournalRecord] = []
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise JournalUnreadable(
                    f"{self.path}:{number} is not valid JSON: {exc}") from exc
            if not isinstance(record, dict) or "digest" not in record:
                raise JournalUnreadable(f"{self.path}:{number} has an unexpected shape")
            if record_digest(record) != record["digest"]:
                raise JournalUnreadable(
                    f"{self.path}:{number} digest does not verify; the journal is "
                    "unreadable, which is not the same as empty")
            out.append(record)  # type: ignore[arg-type]
        return tuple(sorted(out, key=lambda r: r["seq"]))

    def rows_for(self, intent_id: str) -> tuple[JournalRecord, ...]:
        return tuple(row for row in self.rows() if row["intent_id"] == intent_id)

    def _next_seq(self) -> int:
        """The next ``seq``, read under the append lock.  Strictly monotone per file.

        Read from the last line rather than counted in memory, so it is monotone across
        processes as well as within one.  Wall-clock time is never the ordering authority:
        cross-authority order is INCOMPARABLE, not older (R-6), and readers order by ``seq``
        and use ``written_at`` only for display.
        """
        rows = self.rows()
        return (rows[-1]["seq"] + 1) if rows else 1

    # -- append --------------------------------------------------------------------------
    def append(self, record: Mapping[str, Any]) -> JournalRecord:
        """Append one record.  ``open(a)`` + one ``write`` + ``flush`` + ``fsync``."""
        with self._append_lock():
            body = dict(record)
            body["seq"] = self._next_seq()
            body["writer"] = body.get("writer") or self.writer
            body["run_id"] = body.get("run_id") or self.run_id
            body["written_at"] = body.get("written_at") or _now_iso()
            fields: dict[str, Any] = {}
            for key in _RECORD_KEYS:
                if key == "digest":
                    continue                      # make_record computes it
                if key in _NON_STRING_KEYS:
                    fields[key] = body[key]       # must be present; make_record validates
                else:
                    fields[key] = body.get(key, "")
            complete = make_record(**fields)
            line = json.dumps(complete, sort_keys=True, ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return complete

    # -- D4.3a the ATOMIC delivery intent: append + fsync BEFORE the fork ----------------
    class IntentNotDurable(RuntimeError):
        """The `DELIVERY_INTENT` did not reach stable storage.  **Spawning is unreachable.**

        Raised, so the structurally subsequent `fork` is never executed.  O-3's guarantee
        rests on this being an exception rather than a returned flag: a caller that ignored
        a boolean would spawn a process whose prompt nobody recorded, and a successor could
        then neither prove the prompt was delivered nor prove it was not.
        """

    def append_delivery_intent(self, intent: Mapping[str, Any], *,
                               axes: Mapping[str, Any] | None = None) -> JournalRecord:
        """Append the spawn request AND the prompt digest as ONE record, then ``fsync``.

        **The ordering guarantee, stated as the code enforces it.**  This method RETURNS
        only after ``os.fsync`` has returned, and :meth:`append` performs that ``fsync``
        inside the append lock.  The spawn is *structurally subsequent* -- it is written
        after this call in `StandaloneSession.start`, and this call raises rather than
        returns on any append failure, so there is no control-flow path on which a `fork`
        follows a failed intent append.

        **What this record is NOT.**  Not a claim, not a lease, not a fence.  It carries no
        exclusivity, grants no permission and settles nothing: `admit`'s S-0..S-7 ladder
        never consults it to decide whether a settlement is authoritative, and
        `runtime_state` remains the only claim authority (AC-37-20).  What it *is* is the
        observation that lets a successor's `lookup` answer PRECISELY -- absence of this
        record proves no `fork` happened (D4.3e row 1), which is the only thing that makes
        an idempotent re-spawn safe.

        **The prompt itself is never written here.**  Only its digest.  A prompt can carry
        anything the engine composed into it, and the journal is a plain file a stranger
        interpreter reads.
        """
        required = ("intent_id", "dispatch_id", "task_id", "session_id", "prompt_digest",
                    "argv_digest", "attempt_incarnation", "delivery_mode")
        missing = [name for name in required if not intent.get(name)]
        if missing:
            raise ValueError(
                f"a DELIVERY_INTENT is incomplete without {missing!r}; a partial intent "
                "would let a successor draw a conclusion the record cannot support")
        if "prompt" in intent or "payload" in intent:
            raise ValueError(
                "a DELIVERY_INTENT carries the prompt DIGEST, never the prompt; the "
                "journal is a plain file read by stranger processes")
        try:
            return self.append({
                "kind": "DELIVERY_INTENT", "derived_from": "driver",
                "event": "", "state": "STARTING",
                "intent_id": str(intent["intent_id"]),
                "dispatch_id": str(intent["dispatch_id"]),
                "task_id": str(intent["task_id"]),
                "session_id": str(intent["session_id"]),
                "process_incarnation": str(intent["attempt_incarnation"]),
                # The axes are honest about the instant this record is written: the
                # process DOES NOT EXIST YET, so liveness is `unverifiable` and settlement
                # is `unknown`.  Nothing here asserts ownership of anything.
                "axes": dict(axes or {"settlement": "unknown",
                                      "worker_resource": "retain",
                                      "process_liveness": "unverifiable",
                                      "cleanup_authority": "unknown"}),
                "source_vocabulary": {
                    "prompt_digest": str(intent["prompt_digest"]),
                    "argv_digest": str(intent["argv_digest"]),
                    "delivery_mode": str(intent["delivery_mode"]),
                    "intended_at": str(intent.get("at") or intent.get("intended_at") or
                                       _now_iso()),
                },
            })
        except OSError as exc:
            raise self.IntentNotDurable(
                f"the delivery intent for {intent.get('intent_id')!r} did not reach stable "
                f"storage ({exc}); the spawn that would have followed is unreachable"
            ) from exc

    def delivery_intent_for(self, intent_id: str,
                            attempt_incarnation: str = "") -> JournalRecord | None:
        """The recorded intent, or ``None``.  ``None`` means ABSENT, proved by reading.

        It never means "unknown": :meth:`rows` RAISES `JournalUnreadable` when the file
        cannot be read or a digest fails, so a caller that receives ``None`` from here has
        actually read the journal through.
        """
        found = None
        for row in self.rows_for(intent_id):
            if row["kind"] != "DELIVERY_INTENT":
                continue
            if attempt_incarnation and row["process_incarnation"] != attempt_incarnation:
                continue
            found = row
        return found

    # -- D9.3 idempotent admission of observed EVENTS -----------------------------------
    def admit(self, record: Mapping[str, Any], *,
              runtime_state: Any = None) -> dict[str, Any]:
        """Decide whether an OBSERVED event is recorded.  S-0..S-7, in this fixed order.

        Every branch is a total function over the closed set.  **There is no
        ``else: return True``.**

        ``admit`` never writes to ``runtime_state``.  The CLAIMED -> EFFECTED -> SETTLED
        triple is written only by the executor and by ``StandaloneAdapter.start``'s single
        ``record_receipt`` call, always under the lease token, so there is exactly one
        writer of that authority and it is not this module.
        """
        intent_id = record.get("intent_id", "")
        existing = self.rows_for(intent_id)          # S-6 half: raises if unreadable

        # -- S-0: the ledger owns the intent's settlement; this file mirrors it ----------
        if runtime_state is not None:
            try:
                settled = runtime_state.get_settlement(intent_id)
            except Exception as exc:  # noqa: BLE001 - S-6: unknown is never empty
                raise JournalUnreadable(
                    f"the settlement authority for {intent_id} could not be read: {exc}"
                ) from exc
            if settled is not None and record.get("kind") == "SETTLEMENT_OBSERVED":
                if record.get("outcome") and _contradicts(settled, record):
                    return {"outcome": "refused", "code": SETTLEMENT_CONFLICT,
                            "detail": "the ledger already settled this intent with a "
                                      "different outcome; this file does not re-decide it"}

        terminals = tuple(row for row in existing if row["kind"] == "SETTLEMENT_OBSERVED")
        if record.get("kind") == "SETTLEMENT_OBSERVED":
            for row in terminals:
                # -- S-1: duplicate delivery of the SAME report ------------------------
                if row["message_id"] and row["message_id"] == record.get("message_id"):
                    return {"outcome": "no_effect", "code": "",
                            "detail": "duplicate delivery of the same report"}
            for row in terminals:
                # -- S-3: a stale report from a RETRIED task racing this dispatch -------
                if row["dispatch_id"] and record.get("dispatch_id") \
                        and row["dispatch_id"] != record.get("dispatch_id"):
                    return {"outcome": "refused", "code": SETTLEMENT_IDENTITY_MISMATCH,
                            "detail": "a settlement whose dispatch identity does not match"}
            for row in terminals:
                # -- S-2: the ACCEPTED idempotent retry.  Refusing it would diverge from
                # Orca, which is a policy divergence under AC-37-20, not extra strictness.
                if (row["message_id"] != record.get("message_id")
                        and row["reported_by"] and row["reported_by"] == record.get("reported_by")
                        and row["outcome"] == record.get("outcome")
                        and row["task_id"] == record.get("task_id")
                        and row["dispatch_id"] == record.get("dispatch_id")
                        and row["state"] == record.get("state")):
                    written = self.append(record)
                    return {"outcome": "admitted", "code": "",
                            "detail": "accepted idempotent retry", "seq": written["seq"]}

        # -- S-4: out-of-order arrival of a superseded event ------------------------------
        incoming_seq = record.get("seq")
        if isinstance(incoming_seq, int) and incoming_seq > 0 and terminals:
            if incoming_seq < max(row["seq"] for row in terminals):
                return {"outcome": "no_effect", "code": "",
                        "detail": "out-of-order arrival of a superseded event"}

        # -- S-5: the identity fence, whose VALUE belongs to the ledger -------------------
        if runtime_state is not None:
            expected = _receipt_external_id(runtime_state, intent_id)
            if expected:
                offered = f"{record.get('session_id','')}:{record.get('process_incarnation','')}"
                if offered != expected:
                    # A foreign report that quotes the right handle is rejected: payload
                    # knowledge alone is not authority.
                    return {"outcome": "refused", "code": FOREIGN_INCARNATION,
                            "detail": "the record's fence does not equal the fence stored "
                                      "in the runtime-state receipt"}

        # -- S-7 --------------------------------------------------------------------------
        written = self.append(record)
        return {"outcome": "admitted", "code": "", "detail": "", "seq": written["seq"]}

    # -- D9.5 the settlement predicate, both branches -----------------------------------
    def settlement_confirmed(self, candidate: Mapping[str, Any],
                             receipt: Mapping[str, Any], *,
                             expected_outcome: str) -> dict[str, Any]:
        """The predicate, WHOLE.  Both identity paths, and the refusal.

            confirmed  <=>  dispatch_id matches AND dispatch_status matches
                            AND task_status matches AND provenance == "worker_report"
                            AND outcome == expected_outcome
                            AND ( message_id == receipt.message_id                # (a)
                                  OR (receipt.from_handle is not None
                                      AND reported_by == receipt.from_handle) )   # (b)

        **Path (b) must exist.**  A message-id-only check would refuse settlements Orca
        accepts, which is a policy divergence under AC-37-20, not extra strictness.
        **The refusal must also exist.**  Neither path matching, or any exact check
        failing, is a NAMED refusal routed to recovery -- never an inferred completion.
        "(b) without (c) is an amnesty; (c) without (b) is a divergence."
        """
        checks = {
            "dispatch_id": candidate.get("dispatch_id") == receipt.get("dispatch_id"),
            "dispatch_status": candidate.get("dispatch_status") == receipt.get("dispatch_status"),
            "task_status": candidate.get("task_status") == receipt.get("task_status"),
            "provenance": candidate.get("provenance") == "worker_report",
            "outcome": candidate.get("outcome") == expected_outcome,
        }
        path_a = bool(receipt.get("message_id")) and \
            candidate.get("message_id") == receipt.get("message_id")
        path_b = receipt.get("from_handle") is not None and \
            candidate.get("reported_by") == receipt.get("from_handle")
        identity_ok = path_a or path_b
        failed = [name for name, ok in checks.items() if not ok]
        if failed or not identity_ok:
            return {"confirmed": False,
                    "refusal": "unconfirmed_is_not_settled",
                    "failed_checks": tuple(failed),
                    "identity_path": "a" if path_a else ("b" if path_b else ""),
                    "route": "recovery"}
        return {"confirmed": True, "refusal": "",
                "identity_path": "a" if path_a else "b", "failed_checks": ()}

    # -- settlement / open dispatches ----------------------------------------------------
    def settlement_of(self, intent_id: str, *,
                      runtime_state: Any = None) -> SettlementEvent | None:
        """The stored settlement, or ``None`` **only to prove absence**.

        An unreadable authority RAISES.  ``None`` here is a positive statement -- the
        authorities were read and none holds a settlement for this intent -- and a caller
        may act on it; an exception says the caller must not.
        """
        rows = self.rows_for(intent_id)          # raises when unreadable
        if runtime_state is not None:
            stored = runtime_state.get_settlement(intent_id)
            if stored is not None:
                return stored
        for row in reversed(rows):
            if row["kind"] == "SETTLEMENT_OBSERVED" and row["source_vocabulary"].get("event"):
                event = row["source_vocabulary"]["event"]
                return event if isinstance(event, dict) else None
        return None

    def open_dispatches(self) -> tuple[str, ...]:
        """Every intent of this run with no terminal record.  RAISES when unreadable.

        Raises rather than returning a short tuple, which is the rule
        :class:`ports.LifecycleSettlementPort` states directly: a source that cannot be read
        is unknown, never empty.
        """
        rows = self.rows()
        seen: dict[str, bool] = {}
        for row in rows:
            if not row["intent_id"]:
                continue
            seen.setdefault(row["intent_id"], False)
            if row["kind"] in ("SETTLEMENT_OBSERVED", "RELEASED"):
                seen[row["intent_id"]] = True
        return tuple(sorted(intent for intent, done in seen.items() if not done))

    def axes_for(self, intent_id: str) -> OwnershipAxes:
        """The last recorded axes for this intent.  All four, always.

        The default is the maximally ignorant one -- unknown / unsupervised / unverifiable /
        unknown -- because a dispatch nobody has observed is not a dispatch that is fine.
        """
        rows = self.rows_for(intent_id)
        for row in reversed(rows):
            if row["axes"]:
                return validate_axes(row["axes"])
        return validate_axes({"settlement": "unknown", "worker_resource": "unsupervised",
                              "process_liveness": "unverifiable",
                              "cleanup_authority": "unknown"})


def _contradicts(settled: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    stored = settled.get("outcome") if isinstance(settled, Mapping) else None
    incoming = record.get("source_vocabulary", {}).get("outcome") or record.get("outcome")
    if not stored or not incoming:
        return False
    return str(stored).upper() not in (str(incoming).upper(),
                                       _NORMALISED.get(str(incoming).lower(), ""))


_NORMALISED = {"succeeded": "SUCCEEDED", "failed": "FAILED"}


def _receipt_external_id(runtime_state: Any, intent_id: str) -> str:
    """The fence VALUE, read from the ledger receipt.  This module never mints it."""
    try:
        stored = runtime_state.get_receipt(intent_id)
    except Exception as exc:  # noqa: BLE001 - S-6
        raise JournalUnreadable(
            f"the runtime-state receipt for {intent_id} could not be read: {exc}") from exc
    if not stored:
        return ""
    receipt = stored.get("receipt") if isinstance(stored, Mapping) else None
    if not isinstance(receipt, Mapping):
        return ""
    return str(receipt.get("external_id") or "")


def _default_writer() -> str:
    try:
        from .runtime_state import default_owner_id
        return default_owner_id()
    except Exception:  # noqa: BLE001 - a writer label is diagnostic, never authority
        return f"pid:{os.getpid()}"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---- D9.6 re-queryability by a STRANGER process (AC-37-12) -----------------------------
class RunSnapshot(TypedDict):
    run_id: str
    intents: dict[str, dict[str, Any]]
    journal_present: bool


def rediscover(run_id: str, artifact_base: str | os.PathLike[str], *,
               runtime_state: Any = None, intent_ids: Sequence[str] = (),
               table_reader: Any = None) -> RunSnapshot:
    """Reconstruct a run's state holding NONE of the creating process's objects.

    **The ordering is the point.**  The runtime-state LEDGER is read first, because it is
    the authority for what was claimed and what was effected; the journal only says what
    was OBSERVED.  A journal that is missing entirely still yields a correct, fail-closed
    answer from the ledger plus the exit sentinel plus the process table.

    On load, every persisted lease is UNRECONCILED: a restart grants no writer on the
    strength of what the previous process wrote.
    """
    from . import standalone_pty as pty_supervisor
    journal = ExecutionJournal(artifact_base, run_id)
    journal_present = journal.path.exists()
    intents: dict[str, dict[str, Any]] = {}
    candidates = list(intent_ids)
    if journal_present:
        for row in journal.rows():
            if row["intent_id"] and row["intent_id"] not in candidates:
                candidates.append(row["intent_id"])

    for intent_id in candidates:
        entry: dict[str, Any] = {"intent_id": intent_id, "lease": "unreconciled"}
        ledger_status = ""
        receipt = None
        if runtime_state is not None:
            settled = runtime_state.get_settlement(intent_id)
            if settled is not None:
                entry.update({"ledger_status": "SETTLED", "state": "settled",
                              "settlement": settled})
                intents[intent_id] = entry
                continue
            stored = runtime_state.get_receipt(intent_id)
            receipt = (stored or {}).get("receipt")
            ledger_status = (stored or {}).get("status", "") if stored else ""
        entry["ledger_status"] = ledger_status or "UNKNOWN"

        if ledger_status == "CLAIMED" and not receipt:
            # NOTHING was recorded as effected.  Read the child-side spawn record: its
            # ABSENCE proves no execve happened, so nothing was created and lookup may
            # honestly say so.
            probe = pty_supervisor.read_spawn_records(artifact_base, run_id, intent_id)
            entry.update({"state": {"absent": "never_started", "present": "may_exist",
                                    "unknown": "unprovable"}[probe["outcome"]],
                          "spawn_record": probe["outcome"], "detail": probe["detail"]})
            intents[intent_id] = entry
            continue

        fence = str((receipt or {}).get("external_id") or "")
        session_id = fence.split(":", 1)[0] if fence else ""
        incarnation = fence.split(":", 1)[1] if ":" in fence else ""
        terminal = None
        if journal_present:
            for row in reversed(journal.rows_for(intent_id)):
                if row["kind"] == "SETTLEMENT_OBSERVED" and (
                        not fence or f"{row['session_id']}:{row['process_incarnation']}" == fence):
                    terminal = row
                    break
        if terminal is not None:
            entry.update({"state": terminal["state"] or "settled",
                          "observed_outcome": terminal["outcome"],
                          "from": "journal"})
            intents[intent_id] = entry
            continue
        if session_id and incarnation:
            sentinel = pty_supervisor.read_exit_sentinel(
                pty_supervisor.exit_sentinel_path(artifact_base, run_id, session_id,
                                                  incarnation), fence=fence)
            if sentinel["outcome"] == "exited":
                entry.update({"state": "exit_observed", "exit_status": sentinel["code"],
                              "from": "exit_sentinel"})
                intents[intent_id] = entry
                continue
            if sentinel["outcome"] in ("unreadable", "foreign"):
                entry.update({"state": "LOST", "lost_reason": "evidence_unreadable",
                              "from": "exit_sentinel", "sentinel": sentinel["outcome"]})
                intents[intent_id] = entry
                continue
        # Nothing observable.  Probe the process table -- and report an unreadable table as
        # LOST/unverifiable rather than as an exit.
        entry.update(_probe_liveness(journal, intent_id, table_reader=table_reader,
                                     fence=fence))
        intents[intent_id] = entry

    return {"run_id": run_id, "intents": intents, "journal_present": journal_present}


def _probe_liveness(journal: ExecutionJournal, intent_id: str, *,
                    table_reader: Any, fence: str) -> dict[str, Any]:
    from . import standalone_pty as pty_supervisor
    tty = ""
    pid = 0
    for row in reversed(journal.rows_for(intent_id)):
        vocab = row.get("source_vocabulary") or {}
        tty = tty or str(vocab.get("captured_tty") or "")
        pid = pid or int(vocab.get("pid") or 0)
        if tty and pid:
            break
    if not tty or not pid:
        return {"state": "LOST", "lost_reason": "evidence_unreadable",
                "from": "process_table",
                "detail": "no durable process address survives for this intent"}
    reader = table_reader or pty_supervisor.read_process_table
    snapshot = reader(tty)
    if not snapshot.get("readable", False):
        return {"state": "LOST", "lost_reason": "process_table_unreadable",
                "from": "process_table"}
    row = pty_supervisor.row_for(snapshot, pid)
    if row is not None and row["tty"] == tty:
        return {"state": "RUNNING", "from": "process_table", "process_liveness": "live"}
    # The wrapper was SIGKILLed, or the process is otherwise gone with no sentinel.  This
    # is DESIGN risk DR-2, and it is fail-closed: LOST, never COMPLETED.
    return {"state": "LOST", "lost_reason": "stop_unverified", "from": "process_table",
            "detail": "the incarnation is absent and no exit sentinel exists"}


def recover_handle(journal: ExecutionJournal, intent_id: str, *,
                   verified_digest: str = "") -> dict[str, Any]:
    """``{"handle": str|None, "handle_recovery": <member>}``.  RAISES when unreadable.

    A handle is returned ONLY for ``listing_verified`` -- only when a durable digest proved
    it.  ``listing_candidate`` reports a match with no verifier and **must not be acted
    on**; it exists so an abandon report can name the resource.
    """
    rows = journal.rows_for(intent_id)          # raises when unreadable
    handle = ""
    digest = ""
    for row in reversed(rows):
        vocab = row.get("source_vocabulary") or {}
        handle = handle or str(vocab.get("pty_id") or "")
        digest = digest or str(vocab.get("session_digest") or "")
        if handle:
            break
    if not handle:
        return {"handle": None, "handle_recovery": "not_listed"}
    if verified_digest and digest and verified_digest == digest:
        return {"handle": handle, "handle_recovery": "listing_verified"}
    return {"handle": None, "handle_recovery": "listing_candidate",
            "candidate": handle,
            "detail": "named for reporting only; no durable digest verified it, so it must "
                      "not be acted on"}
