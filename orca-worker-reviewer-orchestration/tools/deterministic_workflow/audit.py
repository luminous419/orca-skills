"""OS-42: the validation-repair audit trail.

The ticket requires validation repair to have "its own state AND its own audit events".
The state is the four closed ``WorkflowState`` fields; this module is the other half.

Two constraints have to hold AT ONCE, and holding one at the cost of the other is the
defect this file exists to avoid:

**(i) An audit write failure never changes a lifecycle decision.**  SKILL.md section 9
states the rule for the run logs: logging records what already happened.  So delivery
never raises into the node that produced the transition, and no engine path branches on
whether a row was written.

**(ii) Exactly one COMPLETE durable row per logical transition -- none missing, none
duplicated, after any crash or restart.**

A "swallow every failure" emitter satisfies (i) and violates (ii): the write fails, the
checkpoint advances anyway, and the row is permanently absent.  A "raise on failure"
emitter satisfies (ii) and violates (i).  Neither is acceptable, so the delivery is split
in two:

* **The INTENT rides the checkpoint.**  Each emitter is a pure function that returns
  outbox entries; the graph appends them to ``state["audit_outbox"]``, which is a closed
  checkpointed field.  A delivery that fails leaves its entry in the outbox, so the next
  node -- and any later resume of the same thread -- retries it.  The intent is therefore
  never lost, and its failure never propagates.  This is the same shape as OS-44's
  delivery ack path, which publishes a durable intent record before the external effect
  precisely so a crash between the two is recoverable.
* **The DELIVERY is exactly-once BY CONSTRUCTION, with no liveness judgement anywhere.**
  It publishes a per-key record with ``run_logging.publish_audit_outbox_record``, which
  stages a directory and moves it with ONE ``os.rename`` -- the decision ledger's own
  scheme, where POSIX refuses a rename onto an existing non-empty directory, so at most
  one publication per key can ever win.  The human-readable table is then REGENERATED
  from that published set by ``run_logging.project_audit_outbox``, never appended to.

Why a duplicate row is IMPOSSIBLE rather than unlikely: a row exists in the table if and
only if a record exists in the published set, and the set is keyed by the audit key, so
it cannot hold two entries for one key.  Two racing writers therefore publish once
between them and regenerate byte-identical text.

An earlier design appended the row independently and guarded it with a claim plus a log
scan.  That required deciding whether a competing claimant was alive or crashed, and a
reproduced race showed the cost: a writer that lost the claim treated the live owner as
dead, scanned a log the owner had not appended to yet, and appended a second row.  No
timeout fixes it -- too short declares a slow owner dead, too long wedges a crashed one.
The fix is to remove the independent append, not to arbitrate it.

Crash points, and what each yields:

==========================================  ===============================================
crash point                                 outcome on resume
==========================================  ===============================================
before publish                              entry still in the outbox -> published, projected
after publish, before the projection        record present -> projection regenerated
after the projection, before the outbox     the record is already in the set; publishing
drop is checkpointed                        again is refused and the projection is unchanged
==========================================  ===============================================

Exactly one row in every row of that table, and in every interleaving of them.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

# The four `--event` values.  Duplicated from `run_logging` rather than imported, for the
# same reason `GATE_ENVELOPE_KEYS` is duplicated in `contracts.py`: this package is the
# runtime-neutral engine and takes no tools/ sibling import at module scope.  A parity
# test pins the two sets equal.
EVENT_GATE_FORM_DEFECT = "decision_gate_form_defect"
EVENT_REPAIR_REQUESTED = "validation_repair_requested"
EVENT_REPAIR_SUCCEEDED = "validation_repair_succeeded"
EVENT_REPAIR_EXHAUSTED = "validation_repair_exhausted"
AUDIT_EVENTS: tuple[str, ...] = (
    EVENT_GATE_FORM_DEFECT,
    EVENT_REPAIR_REQUESTED,
    EVENT_REPAIR_SUCCEEDED,
    EVENT_REPAIR_EXHAUSTED,
)

# The `decision_state` column value for a row that reports an INPUT defect rather than a
# decision.  Duplicated from `decision_gate.INPUT_DEFECT_STATE` for the same reason the
# event names are, and pinned equal by the same parity test: "the record was unreadable"
# and "the record said CLEAR" must never look alike in this column.
INPUT_DEFECT_STATE = "INPUT"

# The marker the dedupe reads back out of the row.  It lives in `detail` because
# ORCHESTRATOR_LOG_COLUMNS is the whole schema and every row fills every column -- adding
# a column for a key would change that schema for every event in the repository.
AUDIT_KEY_FIELD = "audit_key"


@runtime_checkable
class AuditSinkPort(Protocol):
    """Durably record ONE audit transition, at most once per key.

    Returns True only when the transition is fully recorded, so a False leaves the entry
    in the checkpointed outbox for a later attempt.  No engine path branches on the value
    to decide anything about the workflow -- it decides only whether to retry the AUDIT.
    """

    def deliver(self, event: str, key: str, fields: Mapping[str, Any]) -> bool: ...


# ---- deterministic keys, one per logical transition ---------------------------------
# Each is a pure function of an identity the checkpoint already carries, so a replayed
# transition produces the same key and the dedupe recognises it.


def gate_defect_key(event_id: str) -> str:
    """The settlement whose gate was rejected.  `event_id` is a pure function of the
    canonical settlement payload, so it survives a restart unchanged."""
    return f"gate_defect:{event_id}"


def repair_requested_key(command_id: str) -> str:
    """The repair dispatch that was prepared.  `command_id` carries `repair_attempt`, so
    attempt 1 and attempt 2 are distinct keys and neither collides with the ordinary
    attempt that preceded them."""
    return f"repair_requested:{command_id}"


def repair_succeeded_key(event_id: str) -> str:
    """The repaired settlement that classified clean."""
    return f"repair_succeeded:{event_id}"


def repair_exhausted_key(run_id: str, phase: str, gate_iteration: Any,
                         repair_attempts: Any) -> str:
    """The one terminal a spent repair budget produces for this gate round."""
    return f"repair_exhausted:{run_id}:{phase}:{gate_iteration}:{repair_attempts}"


# ---- the detail column ----------------------------------------------------------------


def defect_detail(defects: Sequence[Mapping[str, Any]], *, key: str,
                  repair_attempt: Any = None, max_attempts: Any = None) -> str:
    """The `detail` cell: the audit key, the repair ordinal, and the first defect's
    field path and allowed set.

    The ordinal goes here rather than in the `iteration` column on purpose: `iteration`
    keeps meaning the GATE iteration for every row in this table, and a repair attempt is
    not an iteration.  Overloading it would make two different counters share one column.
    """
    parts = [f"{AUDIT_KEY_FIELD}={key}"]
    if repair_attempt is not None:
        ordinal = f"repair_attempt={repair_attempt}"
        if max_attempts is not None:
            ordinal += f"/{max_attempts}"
        parts.append(ordinal)
    first = defects[0] if defects else None
    if first is not None:
        parts.append(f"defect_code={first.get('code', '')}")
        parts.append(f"field={first.get('field_path') or '<whole record>'}")
        allowed = first.get("expected") or ()
        if allowed:
            parts.append("allowed=" + "|".join(str(value) for value in allowed))
        if len(defects) > 1:
            parts.append(f"further_defects={len(defects) - 1}")
    return "; ".join(parts)


# ---- outbox entries ------------------------------------------------------------------
# An entry is the CHECKPOINTED INTENT to emit.  It is a plain JSON-safe mapping so it
# survives `state._checkpointable`, which admits only dict/list/bool/int/str/None.
OUTBOX_ENTRY_KEYS: tuple[str, ...] = ("event", "key", "fields")


def outbox_entry(event: str, key: str, **fields: Any) -> dict[str, Any]:
    """One durable intent to emit, ready to ride the checkpoint."""
    return {"event": event, "key": key, "fields": dict(fields)}


def merge_outbox(state: dict[str, Any],
                 entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Append entries to the state's outbox, skipping keys it already carries.

    Deduplicating on the way in keeps a re-executed node from stacking the same intent
    twice; the delivery side is idempotent regardless, so this is hygiene rather than the
    guarantee.
    """
    outbox = [dict(entry) for entry in state.get("audit_outbox") or ()]
    present = {entry["key"] for entry in outbox}
    for entry in entries:
        if entry["key"] not in present:
            outbox.append(dict(entry))
            present.add(entry["key"])
    return outbox


def flush_outbox(sink: Any, outbox: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deliver what can be delivered; RETURN what could not.

    Total by construction: a sink that raises, or no sink at all, leaves every entry in
    the returned list, and the caller writes that list back into the checkpoint.  Nothing
    here can raise into the node that produced the transition, and nothing here inspects
    an outcome to decide anything about the workflow -- constraint (i).

    An entry is dropped ONLY when delivery reported success, so a failed write is retried
    on the next node and on any later resume of this thread -- constraint (ii)'s "no
    missing row" half.
    """
    if sink is None:
        return [dict(entry) for entry in outbox]
    undelivered: list[dict[str, Any]] = []
    for entry in outbox:
        try:
            delivered = bool(sink.deliver(entry["event"], entry["key"],
                                          dict(entry["fields"])))
        except Exception:  # noqa: BLE001 - deliberate: see the module docstring
            delivered = False
        if not delivered:
            undelivered.append(dict(entry))
    return undelivered


def drain(sink: Any, state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Deliver a checkpoint's outstanding audit intents. Returns what is still undelivered.

    A run that has already terminated runs no further node, so "the next node retries it"
    stops being true at exactly the moment a run ends -- which is also when the last
    transition (exhaustion) is emitted.  This is the retry point for that case, and it is
    deliberately OUTSIDE the graph: it reads a checkpoint, delivers, and writes no state
    back.  It cannot influence a lifecycle decision because it touches none, and it needs
    no state write because delivery is idempotent -- draining twice is a no-op.

    Call it after a run settles and on any resume.  `launcher.execute_state` does.
    """
    return flush_outbox(sink, state.get("audit_outbox") or ())


# ---- the sink: publish atomically, then DERIVE the human-readable view -------------


class RunLoggingAuditSink:
    """Publishes one durable record per key and regenerates the derived audit table.

    There are exactly two steps and neither can produce a duplicate:

    1. **Publish** ``validation_repair_audit/<key>/record.json`` by staging a directory
       and moving it with ONE ``os.rename``.  POSIX refuses a rename onto an existing
       non-empty directory, so at most one publication per key can EVER win -- across
       threads, processes, and restarts alike.  The published set is therefore a SET,
       keyed by the audit key.
    2. **Project** the table by REGENERATING it from that set.  The content is a pure
       function of the set, so two writers racing produce byte-identical text, and the
       whole file is swapped in with one ``os.replace``.

    A duplicate row is impossible rather than unlikely, and the reason is structural: a
    row exists in the table if and only if a record exists in the set, and the set cannot
    hold two entries for one key.  Nothing here observes another writer, waits for one,
    or decides whether one is alive -- the previous design's append-claim needed exactly
    that liveness judgement, and it is the judgement that produced the reproduced race.

    `deliver` returns True when the record is published, which is the whole of the
    durable guarantee; a False leaves the entry in the checkpointed outbox to be retried.
    It raises nothing that ``flush_outbox`` does not already contain.
    """

    def __init__(self, run_id: str, artifact_base: Any = None) -> None:
        self.run_id = run_id
        self.artifact_base = artifact_base

    @staticmethod
    def _run_logging() -> Any:
        try:  # repository layout
            from scripts import run_logging
        except ImportError:  # pragma: no cover - flat installed layout
            import run_logging  # type: ignore[no-redef]
        return run_logging

    def deliver(self, event: str, key: str, fields: Mapping[str, Any]) -> bool:
        run_logging = self._run_logging()
        # 1. Atomic, exactly-once-per-key publication.  "Already published" is a normal
        #    resume or a lost race, not an error: either way the record exists, which is
        #    what the caller needs to know.
        run_logging.publish_audit_outbox_record(
            self.run_id, key, {"event": event, "key": key, "fields": dict(fields)},
            base=self.artifact_base)
        # 2. Regenerate the derived view.  Rendering is a pure function of the published
        #    set, but PUBLISHING the rendering is a read-modify-write, so this converges
        #    it against the authority rather than replacing blind.
        run_logging.project_audit_outbox(self.run_id, base=self.artifact_base)
        # 3. Report success only once THIS entry's row is actually in the published
        #    table.  `flush_outbox` discards an entry on True, so returning True on an
        #    unconfirmed write is how a row gets lost; a False keeps the checkpointed
        #    intent and the next node, resume or drain re-delivers it.  The record is
        #    already durable either way, so a retry republishes nothing.
        return bool(run_logging.audit_projection_covers(
            self.run_id, key, base=self.artifact_base))
