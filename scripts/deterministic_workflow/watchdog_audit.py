"""OS-43 D-8 / DI-2 (option beta): the Watchdog's own append-only, run-rooted ledger.

``artifacts/runs/<run_id>/watchdog_audit/<NNNNNN>/record.json``, with its own closed event
vocabulary, its own schema version and its own strict fold.

**Why not option alpha -- widening ``run_logging.COORDINATOR_AUDIT_EVENTS``.**  That tuple
is closed *because it is folded strictly*: ``replay_delivery_ledger`` refuses every
published record whose event is not in it (``run_logging.py:2620-2626``) BEFORE folding any
of them, and ``turn_boundary.observe_deliveries`` converts that refusal into
``TurnBoundaryUnavailable`` (``turn_boundary.py:398-405``).  Any reader whose tuple predates
the extension -- a lagging mirror, an installed copy -- would therefore take the whole
turn-end boundary and the whole delivery restart recovery dark for that run, and the cause
would be a component that says nothing about deliveries.  The schema-version escape is
strictly worse: bumping ``COORDINATOR_AUDIT_SCHEMA_VERSION`` makes the refusal fire on
EVERY existing record of EVERY existing run (``run_logging.py:2607-2613``).  Beta needs
neither, and it also gives a high-frequency writer its own sequence space instead of
letting watchdog load exhaust the Coordinator's bounded allocation
(``run_logging.py:2497-2515``).

**This ledger is a LOG, not a deduplicator.**  Deduplication is the engine's head-keyed
recovery identity plus its write-before-effect ordering.  The ledger is never consulted to
decide whether a recovery took effect -- the engine's attempt entry and the head pointer
are -- and nothing but the Watchdog ever writes it.  Those two sentences are the DI-2 beta
authority partition.

It is NOT a re-implementation of the durability scheme: it uses
``run_logging._stage_and_publish_audit_record`` (``run_logging.py:1889-1946``), the shared
publish primitive that already backs both the decision ledger and the Final Review audit,
so it inherits "a published directory IS a complete record" (``run_logging.py:1905-1908``)
and never overwrites a published one.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from scripts import run_logging
except ImportError:  # installed Skill layout exposes sibling tools directly
    import run_logging  # type: ignore[no-redef]

WATCHDOG_AUDIT_SCHEMA_VERSION = "os43.watchdog_audit.v1"
WATCHDOG_AUDIT_DIRNAME = "watchdog_audit"
WATCHDOG_AUDIT_RECORD_FILENAME = "record.json"
WATCHDOG_AUDIT_KEY_WIDTH = 6
WATCHDOG_AUDIT_MAX_ALLOCATION_ATTEMPTS = 8

EVENT_WATCHDOG_SWEEP_STARTED = "watchdog_sweep_started"
EVENT_WATCHDOG_SWEEP_COMPLETED = "watchdog_sweep_completed"
EVENT_WATCHDOG_DETECTED = "watchdog_detected"
EVENT_WATCHDOG_GATE_DECLINED = "watchdog_gate_declined"
EVENT_WATCHDOG_CLAIM_OPENED = "watchdog_claim_opened"      # written BEFORE the invocation
EVENT_WATCHDOG_RESUME_OUTCOME = "watchdog_resume_outcome"
EVENT_WATCHDOG_FAILURE = "watchdog_failure"
EVENT_WATCHDOG_ESCALATED = "watchdog_escalated"
EVENT_WATCHDOG_SHUTDOWN = "watchdog_shutdown"
WATCHDOG_AUDIT_EVENTS = (
    EVENT_WATCHDOG_SWEEP_STARTED, EVENT_WATCHDOG_SWEEP_COMPLETED,
    EVENT_WATCHDOG_DETECTED, EVENT_WATCHDOG_GATE_DECLINED, EVENT_WATCHDOG_CLAIM_OPENED,
    EVENT_WATCHDOG_RESUME_OUTCOME, EVENT_WATCHDOG_FAILURE, EVENT_WATCHDOG_ESCALATED,
    EVENT_WATCHDOG_SHUTDOWN,
)

#: Events whose whole purpose is to say something about ONE recovery attempt.  One of these
#: published without a usable ``recovery_id`` cannot be folded into the budget at all, so it
#: is REFUSED rather than skipped -- the ``DELIVERY_IDENTIFIED_AUDIT_EVENTS`` rule
#: (``run_logging.py:2554-2572``).
IDENTITY_BEARING_WATCHDOG_EVENTS = (EVENT_WATCHDOG_CLAIM_OPENED,
                                    EVENT_WATCHDOG_RESUME_OUTCOME,
                                    EVENT_WATCHDOG_FAILURE,
                                    EVENT_WATCHDOG_ESCALATED)


class WatchdogAuditError(ValueError):
    """A watchdog audit record was refused before anything was published, or the fold
    could not be completed and a truncated history was refused instead of returned."""


def watchdog_audit_dir(run_id: str, *, base: Any = ".") -> Path:
    return Path(base) / "artifacts" / "runs" / run_id / WATCHDOG_AUDIT_DIRNAME


def watchdog_audit_sequence_key(sequence: int) -> str:
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise WatchdogAuditError(
            f"an audit sequence must be a non-negative integer, got {sequence!r}")
    return f"{sequence:0{WATCHDOG_AUDIT_KEY_WIDTH}d}"


def _published_keys(audit_dir: Path) -> list[int]:
    if not audit_dir.is_dir():
        return []
    keys: list[int] = []
    for entry in audit_dir.iterdir():
        if not entry.is_dir() or not entry.name.isdigit():
            continue
        keys.append(int(entry.name))
    return sorted(keys)


def append_watchdog_audit_record(run_id: str, event: str, record: Mapping[str, Any], *,
                                 base: Any = ".") -> tuple[Path, int]:
    """Allocate the next free sequence and publish ONE immutable record.

    Refuses an unknown event BEFORE publishing, exactly as
    ``append_coordinator_audit_record`` does (``run_logging.py:2486-2489``): a typo that
    reaches the artifact is a column that silently stops being queryable.
    """
    if event not in WATCHDOG_AUDIT_EVENTS:
        raise WatchdogAuditError(
            f"unknown watchdog audit event: {event!r}; expected one of "
            f"{list(WATCHDOG_AUDIT_EVENTS)}")
    if not isinstance(record, Mapping):
        raise WatchdogAuditError("a watchdog audit record must be a mapping")
    audit_dir = watchdog_audit_dir(run_id, base=base)
    audit_dir.mkdir(parents=True, exist_ok=True)
    existing = _published_keys(audit_dir)
    sequence = (existing[-1] + 1) if existing else 0
    for _attempt in range(WATCHDOG_AUDIT_MAX_ALLOCATION_ATTEMPTS):
        key = watchdog_audit_sequence_key(sequence)
        payload = dict(record)
        payload["sequence"] = sequence
        payload["event"] = event
        payload["run_id"] = run_id
        payload["audit_schema_version"] = WATCHDOG_AUDIT_SCHEMA_VERSION
        payload.setdefault("recorded_at", run_logging.now_iso())
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False,
                          default=str) + "\n"
        try:
            published = run_logging._stage_and_publish_audit_record(
                audit_dir, key, {WATCHDOG_AUDIT_RECORD_FILENAME: text})
        except run_logging.FinalReviewAuditCollision:
            # Two writers on one run get two sequences and neither overwrites the other.
            sequence = _published_keys(audit_dir)[-1] + 1
            continue
        return published, sequence
    raise WatchdogAuditError(
        f"{run_id}: no free watchdog audit sequence after "
        f"{WATCHDOG_AUDIT_MAX_ALLOCATION_ATTEMPTS} attempts")


def read_watchdog_audit(run_id: str, *, base: Any = ".") -> list[dict[str, Any]]:
    """Every published record, ordered by ``sequence``.  Provisions nothing.

    A record whose JSON cannot be parsed is returned as an ``_unreadable`` sentinel rather
    than dropped, for the same reason the decision ledger's reader keeps one: dropping it
    would turn a corrupt record into an absence, and an absence reads as "nothing
    happened" (``run_logging.py:2518-2551``).
    """
    audit_dir = watchdog_audit_dir(run_id, base=base)
    records: list[dict[str, Any]] = []
    for sequence in _published_keys(audit_dir):
        path = (audit_dir / watchdog_audit_sequence_key(sequence)
                / WATCHDOG_AUDIT_RECORD_FILENAME)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            records.append({"sequence": sequence,
                            "_unreadable": " ".join(str(error).split())})
            continue
        if not isinstance(payload, dict):
            records.append({"sequence": sequence, "_unreadable": "not a JSON object"})
            continue
        records.append(payload)
    records.sort(key=lambda record: record.get("sequence", 0))
    return records


def _refuse_unfoldable_watchdog_record(record: Mapping[str, Any], run_id: str) -> None:
    """Raise unless this published record can be folded VERBATIM.

    Mirrors ``_refuse_unfoldable_audit_record`` (``run_logging.py:2575-2645``) condition
    for condition, and for the same reason one layer along: this ledger is the SOLE durable
    source of the retry budget, so a fold that merely skips what it does not understand
    hands back a silently truncated history -- and a truncated history is
    indistinguishable from "nothing has been attempted", which is how a bounded retry
    becomes an unbounded one.
    """
    sequence = record.get("sequence")
    where = f"watchdog audit record {sequence!r} of run {run_id!r}"
    unreadable = record.get("_unreadable")
    if unreadable is not None:
        raise WatchdogAuditError(
            f"{where} could not be read ({unreadable}); this ledger is the only source of "
            "the retry budget, so it is refused rather than silently truncated")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise WatchdogAuditError(
            f"{where} carries no usable sequence, so the ledger cannot be folded in order")
    if record.get("audit_schema_version") != WATCHDOG_AUDIT_SCHEMA_VERSION:
        raise WatchdogAuditError(
            f"{where} declares audit schema {record.get('audit_schema_version')!r}, not "
            f"{WATCHDOG_AUDIT_SCHEMA_VERSION!r}; its fields cannot be folded safely")
    if record.get("run_id") != run_id:
        raise WatchdogAuditError(
            f"{where} belongs to run {record.get('run_id')!r}; a record filed under "
            "another run cannot describe this one")
    event = record.get("event")
    if event not in WATCHDOG_AUDIT_EVENTS:
        raise WatchdogAuditError(
            f"{where} carries unknown event {event!r}; the writer refuses an unknown "
            "event, so a published one is damage")
    recovery_id = record.get("recovery_id", "")
    if recovery_id is not None and not isinstance(recovery_id, str):
        raise WatchdogAuditError(
            f"{where} carries a non-string recovery id {recovery_id!r}")
    if event in IDENTITY_BEARING_WATCHDOG_EVENTS and not recovery_id:
        raise WatchdogAuditError(
            f"{where} is a {event!r} record with no recovery id; it says something about "
            "an attempt this fold can no longer name")


def _new_row(recovery_id: str) -> dict[str, Any]:
    return {"recovery_id": recovery_id, "attempts": 0, "last_outcome": "",
            "last_code": "", "conflicts": 0, "backoff_until": 0.0, "escalation": "",
            "terminal": False, "head_before": "", "head_after": ""}


def replay_watchdog_ledger(run_id: str, *, base: Any = ".") -> dict[str, dict[str, Any]]:
    """Rebuild the budget, backoff, escalation and terminal state a predecessor left.

    Raises :class:`WatchdogAuditError` when ANY published record cannot be folded
    verbatim, BEFORE returning a ledger.  An ABSENT ledger is not damage and folds to an
    empty state, raising nothing -- ``run_logging.py:2589-2591``'s rule, which is also
    what makes a pre-OS-43 run readable with no migration: a Watchdog meeting one
    correctly concludes "no attempt has been made", not "damage".
    """
    ledger: dict[str, dict[str, Any]] = {}
    for record in read_watchdog_audit(run_id, base=base):
        _refuse_unfoldable_watchdog_record(record, run_id)
        recovery_id = str(record.get("recovery_id") or "")
        if not recovery_id:
            continue
        row = ledger.setdefault(recovery_id, _new_row(recovery_id))
        event = record.get("event")
        if record.get("head_before"):
            row["head_before"] = str(record["head_before"])
        if record.get("head_after"):
            row["head_after"] = str(record["head_after"])
        if event == EVENT_WATCHDOG_CLAIM_OPENED:
            row["attempts"] = int(row["attempts"]) + 1
        elif event == EVENT_WATCHDOG_RESUME_OUTCOME:
            outcome = str(record.get("outcome_status") or "")
            row["last_outcome"] = outcome
            row["last_code"] = str(record.get("outcome_code") or "")
            if outcome == "CONFLICT":
                row["conflicts"] = int(row["conflicts"]) + 1
            else:
                row["conflicts"] = 0
            if outcome in ("NOT_RECOVERABLE", "UNSUPPORTED", "NO_EFFECT"):
                row["terminal"] = True
        elif event == EVENT_WATCHDOG_FAILURE:
            row["last_outcome"] = str(record.get("outcome_status") or "")
            row["last_code"] = str(record.get("outcome_code") or "")
            backoff = record.get("backoff_until")
            if isinstance(backoff, (int, float)) and not isinstance(backoff, bool):
                row["backoff_until"] = float(backoff)
        elif event == EVENT_WATCHDOG_ESCALATED:
            row["escalation"] = str(record.get("escalation") or "")
            row["terminal"] = True
    return ledger


class FileWatchdogAudit:
    """A :class:`ports.SupervisorAuditPort` over the run-rooted ledger.

    ``append`` is a GATE here, not evidence: unlike OS-31's ``_audit`` helper, which
    swallows every exception because there "audit is evidence, never a gate"
    (``pause_runtime.py:101-106``), this ledger is the budget's ONLY source, so a record
    that cannot be published means the attempt it precedes must not be made.  The
    divergence is deliberate and is stated so it is not read as an inconsistency.
    """

    def __init__(self, artifact_base: str | os.PathLike[str] = ".") -> None:
        self.artifact_base = Path(artifact_base)

    def append(self, run_id: str, event: str,
               record: Mapping[str, Any]) -> tuple[str, int]:
        published, sequence = append_watchdog_audit_record(run_id, event, record,
                                                           base=self.artifact_base)
        return str(published), sequence

    def fold(self, run_id: str) -> dict[str, dict[str, Any]]:
        return replay_watchdog_ledger(run_id, base=self.artifact_base)
