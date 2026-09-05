"""OS-44.  The Coordinator run-to-quiescence invariant and delivery provenance rules.

Runtime-neutral and pure, for the same reason every other module in this package is:
the rules below have to be checkable by the deterministic engine's own tests, by the
Orca runtime harness that drives real dispatches, and by a live prompt-driven
Coordinator through the shipped tooling -- three callers that share no runtime.
Nothing here performs I/O, issues a command, or imports anything outside this package.

Two questions live here, and they are separate on purpose.

**May this Coordinator turn end?**  A turn may only end once the run has actually
reached rest.  ``run_c2166e75bb02`` ended a turn after settling an ANALYSIS Reviewer
PASS and before creating the PLAN Task: the run was neither terminal nor waiting for a
human, and no dispatch was active, so nothing existed that could wake it.  It stalled
for ~33 minutes until a user typed.  ``quiescence_verdict`` names that state a
violation instead of a report.

**Whose result is this delivery?**  A delivery carries messages, and a message names
the Task and Dispatch it settles.  ``worker_done_provenance`` refuses -- rather than
adopts -- a ``worker_done`` that names a different Task or Dispatch than the waiter is
waiting for, and ``delivery_disposition`` separates a first processing from a replay of
a delivery this run already consumed.  In the recorded run, ``delivery_5c541e7fe1bd``
was processed but never acknowledged, so the next ``check --wait`` replayed it and the
newly armed PLAN waiter woke on the previous phase's ANALYSIS result.
"""
from __future__ import annotations

from typing import Any

from .contracts import RUN_LIFECYCLE_STATES, TERMINAL_STATUSES

# ---- turn-end (quiescence) vocabulary ---------------------------------------------

#: A turn may end while a dispatch is genuinely running and being waited on.  This is
#: not a run status -- the run is ACTIVE -- so it is named separately from the statuses
#: the engine already owns.
ACTIVE_DISPATCH_WAIT = "ACTIVE_DISPATCH_WAIT"
#: The OS-31 lifecycle state for a run parked on a human decision.  Taken from
#: ``RUN_LIFECYCLE_STATES`` rather than re-spelled, so the two cannot drift.
WAITING_FOR_INPUT = "WAITING_FOR_INPUT"

#: A run that ended on an unrecovered error.  Not a ``TERMINAL_STATUSES`` member -- the
#: engine has no such route token -- but it IS a run-status value a Coordinator reports,
#: and a run that ended in error has ended: there is no next node and nothing to wake.
#: Naming it here is what keeps the self-check from calling an honestly-failed turn a
#: violation.
ERROR = "ERROR"

#: The OS-31 lifecycle state for a run whose work is over.  A run reports EITHER a
#: terminal status or this lifecycle value depending on which vocabulary the caller
#: holds, and both mean the same thing to this contract: there is nothing left to run.
SETTLED = "SETTLED"

#: Every state in which ending the Coordinator turn is legitimate.  OS-44 enumerates
#: five (active dispatch wait, ``WAITING_FOR_INPUT``, ``BLOCKED``, ``ESCALATED``,
#: ``COMPLETED``); ``CANCELLED`` and ``ABANDONED`` are the ``TERMINAL_STATUSES``
#: members OS-31 added and a run that reached one of those has ended just as honestly,
#: and ``SETTLED`` is the lifecycle spelling of the same fact.  Derived from the
#: engine's own tuples so a status added there is covered here without a second list to
#: maintain.
QUIESCENT_STATES = (
    ACTIVE_DISPATCH_WAIT,
    WAITING_FOR_INPUT,
    SETTLED,
    ERROR,
    *TERMINAL_STATUSES,
)

#: The turn ended somewhere it was allowed to.
QUIESCENCE_OK = "QUIESCENCE_OK"
#: A runnable next node exists and no dispatch is active: the exact ``run_c2166e75bb02``
#: shape.  The Coordinator owed the run either the next action or an active wait.
QUIESCENCE_NEXT_NODE_UNCONSUMED = "QUIESCENCE_NEXT_NODE_UNCONSUMED"
#: No next node, no active dispatch, and the run is neither terminal nor waiting.  The
#: run cannot progress and nothing can wake it, which is a stall with no route token to
#: blame -- still a violation, and reported under its own code so the two are
#: distinguishable in the audit.
QUIESCENCE_IDLE_NON_TERMINAL = "QUIESCENCE_IDLE_NON_TERMINAL"
#: A delivery was processed and never acknowledged.  Ending the turn here is what
#: leaves the stale delivery that the next waiter wakes on.
QUIESCENCE_UNACKNOWLEDGED_DELIVERY = "QUIESCENCE_UNACKNOWLEDGED_DELIVERY"
#: The caller handed a status this contract does not define.  Fail closed: an
#: unrecognised status is not evidence of rest.
QUIESCENCE_UNKNOWN_STATUS = "QUIESCENCE_UNKNOWN_STATUS"

QUIESCENCE_REASON_CODES = (
    QUIESCENCE_OK,
    QUIESCENCE_NEXT_NODE_UNCONSUMED,
    QUIESCENCE_IDLE_NON_TERMINAL,
    QUIESCENCE_UNACKNOWLEDGED_DELIVERY,
    QUIESCENCE_UNKNOWN_STATUS,
)

#: Statuses a caller may legitimately report for a run that has NOT ended.  ``ACTIVE``
#: is the OS-31 lifecycle value; the empty string is "the caller did not say", which is
#: treated the same way -- not as rest.  Every other ``RUN_LIFECYCLE_STATES`` member is
#: a rest state and appears in ``QUIESCENT_STATES`` instead.
_NON_TERMINAL_STATUSES = frozenset({"", "ACTIVE"})
assert not (frozenset(RUN_LIFECYCLE_STATES) - _NON_TERMINAL_STATUSES
             - frozenset(QUIESCENT_STATES)), (
    "every run lifecycle state must be classified as rest or not-rest")


class QuiescenceViolation(ValueError):
    """A Coordinator turn was about to end somewhere ``QUIESCENT_STATES`` forbids."""

    def __init__(self, verdict: dict[str, Any]) -> None:
        self.verdict = dict(verdict)
        self.reason_code = str(verdict.get("reason_code", ""))
        super().__init__(str(verdict.get("detail", "")) or self.reason_code)


def quiescence_verdict(
    *,
    run_status: str,
    next_node: str = "",
    active_dispatches: int = 0,
    unacknowledged_deliveries: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Decide whether the Coordinator turn may end.  Pure; raises nothing.

    ``run_status`` is the run's own status (a ``TERMINAL_STATUSES`` member,
    ``WAITING_FOR_INPUT``, ``ACTIVE`` or ``""``).  ``next_node`` is the runnable next
    node or route token the engine has produced, ``""`` when there is none.
    ``active_dispatches`` counts dispatches the Coordinator is (or should be) waiting
    on.  ``unacknowledged_deliveries`` lists deliveries this Coordinator has processed
    and not yet acknowledged.

    The order of the checks is the contract.  An unacknowledged delivery is reported
    FIRST and regardless of status, because a delivery left unacknowledged on a
    COMPLETED run is still the replay hazard OS-44 exists to remove; a Coordinator that
    read the status check first would call that turn quiescent and leave the stale
    delivery behind.
    """
    pending = tuple(str(delivery) for delivery in unacknowledged_deliveries)
    state = _turn_end_state(run_status, active_dispatches)
    verdict: dict[str, Any] = {
        "quiescent": False,
        "state": state,
        "run_status": run_status,
        "next_node": next_node,
        "active_dispatches": int(active_dispatches),
        "unacknowledged_deliveries": list(pending),
        "reason_code": QUIESCENCE_OK,
        "detail": "",
    }
    if pending:
        verdict["reason_code"] = QUIESCENCE_UNACKNOWLEDGED_DELIVERY
        verdict["detail"] = (
            "the Coordinator turn cannot end with processed but unacknowledged "
            f"deliveries: {', '.join(pending)}; acknowledge each one before the turn "
            "ends or the next waiter wakes on the replay"
        )
        return verdict
    if state in QUIESCENT_STATES:
        verdict["quiescent"] = True
        return verdict
    if state == QUIESCENCE_UNKNOWN_STATUS:
        verdict["reason_code"] = QUIESCENCE_UNKNOWN_STATUS
        verdict["detail"] = (
            f"run status {run_status!r} is not a state this contract can call rest; "
            f"expected one of {sorted(QUIESCENT_STATES)} or a non-terminal status"
        )
        return verdict
    if next_node:
        verdict["reason_code"] = QUIESCENCE_NEXT_NODE_UNCONSUMED
        verdict["detail"] = (
            f"next node {next_node!r} is runnable and no dispatch is active, so the "
            "turn owes the run either that next action or an active wait; run status "
            f"is {run_status or 'ACTIVE'}, which is neither terminal nor waiting"
        )
        return verdict
    verdict["reason_code"] = QUIESCENCE_IDLE_NON_TERMINAL
    verdict["detail"] = (
        f"run status is {run_status or 'ACTIVE'} with no active dispatch and no "
        "runnable next node, so nothing can wake this run; a non-terminal, "
        "non-waiting run may not be left idle"
    )
    return verdict


def _turn_end_state(run_status: str, active_dispatches: int) -> str:
    """The state the turn would end in, or ``QUIESCENCE_UNKNOWN_STATUS``.

    A terminal status wins over an active dispatch: a run that has genuinely ended is
    reported by its own status, and reporting it as a dispatch wait would hide the
    ending.  ``ACTIVE_DISPATCH_WAIT`` is therefore only reached from a non-terminal,
    non-waiting status.
    """
    if run_status in QUIESCENT_STATES and run_status != ACTIVE_DISPATCH_WAIT:
        return run_status
    if run_status not in _NON_TERMINAL_STATUSES:
        return QUIESCENCE_UNKNOWN_STATUS
    return ACTIVE_DISPATCH_WAIT if active_dispatches > 0 else run_status or "ACTIVE"


def assert_quiescent(
    *,
    run_status: str,
    next_node: str = "",
    active_dispatches: int = 0,
    unacknowledged_deliveries: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """``quiescence_verdict`` with the fail-closed edge attached.

    Returns the verdict when the turn may end and raises ``QuiescenceViolation``
    carrying that same verdict when it may not.
    """
    verdict = quiescence_verdict(
        run_status=run_status,
        next_node=next_node,
        active_dispatches=active_dispatches,
        unacknowledged_deliveries=unacknowledged_deliveries,
    )
    if not verdict["quiescent"]:
        raise QuiescenceViolation(verdict)
    return verdict


# ---- delivery provenance vocabulary ------------------------------------------------

#: The message names this waiter's Task and Dispatch: adopt it as the waiter's result.
PROVENANCE_ADOPT = "adopt"
#: The message names a different Task or Dispatch, or names none at all.  It is
#: recorded and discarded, never adopted -- this is the filter that keeps a stale
#: ANALYSIS ``worker_done`` from being read as a PLAN result.
PROVENANCE_MISMATCH = "mismatch"

#: The two identity fields every accepted ``worker_done`` carries.  Order matters: a
#: mismatched Dispatch is reported before a mismatched Task, so a stale delivery
#: reports itself as a stale delivery rather than as a Task-routing error.
IDENTITY_FIELDS = ("dispatchId", "taskId")

#: First time this run has seen this delivery: process it.
DELIVERY_PROCESS = "process"
#: A delivery this run already processed and acknowledged has been redelivered.  It is
#: acknowledged again and produces NO lifecycle action of any kind -- no settlement, no
#: release, no artifact, no dispatch, no iteration consumption.
DELIVERY_REPLAY = "replay"
#: The same delivery has been replayed more times than the contract tolerates.  Fail
#: closed rather than spin: something upstream is not consuming the acknowledgement.
DELIVERY_REPLAY_EXHAUSTED = "replay_exhausted"
#: A previous PROCESS consumed this delivery, never settled anything for it, and never
#: acknowledged it -- so the runtime redelivered it.  Nothing was mutated for it, so the
#: redelivery is the recovery path: process it again and let the settlement ledger keep
#: exactly-once.  Discarding it as a replay here is how the delivery would be LOST.
#: Unreachable inside one process, because a processed-and-unacknowledged delivery
#: already forbids arming another waiter; it exists for the restart boundary.
DELIVERY_RESUME = "resume"
#: A previous PROCESS claimed the settlement for this delivery and never recorded that
#: it finished.  A claim carries no proof of how many lifecycle commands already went
#: out, so re-processing could DUPLICATE one.  Fail closed and recover explicitly --
#: the same rule the in-process finalization gate applies to a claimed row.
DELIVERY_RECOVER = "recover"

DELIVERY_DISPOSITIONS = (
    DELIVERY_PROCESS,
    DELIVERY_REPLAY,
    DELIVERY_REPLAY_EXHAUSTED,
    DELIVERY_RESUME,
    DELIVERY_RECOVER,
)

#: How many redeliveries of one already-acknowledged delivery are absorbed before the
#: Coordinator fails closed.
DELIVERY_REPLAY_LIMIT = 3

#: The delivery-ledger row key these three values live under.  Deliberately NOT
#: ``state``: the Coordinator keeps a second, unrelated ledger whose rows carry a
#: ``state`` slot (the per-Dispatch finalize-once ledger), and one structural guard over
#: that slot must not be diluted into covering two different ledgers.
DELIVERY_STATE_FIELD = "delivery_state"

#: A delivery this Coordinator consumed and has not yet acknowledged.
DELIVERY_STATE_PROCESSED = "processed"
#: A delivery whose acknowledgement the runtime accepted.
DELIVERY_STATE_ACKNOWLEDGED = "acknowledged"
#: Bounded ack retry ran out.  The row stays in this state; it never silently becomes
#: acknowledged.
DELIVERY_STATE_ACK_FAILED = "ack_failed"

#: The delivery-ledger row slot recording that the settlement for this delivery was
#: CLAIMED -- written before the settlement path's first command, so a successor can
#: tell "no lifecycle command can have gone out" from "one may already have".
DELIVERY_SETTLEMENT_CLAIMED_FIELD = "settlement_claimed"
#: The row slot recording that state and settlement were fully reflected for this
#: delivery.  Written after finalization and BEFORE the acknowledgement, which is the
#: ordering that makes a crash between them recoverable rather than lossy.
DELIVERY_SETTLED_FIELD = "settled"
#: The row slot marking a row this process RECOVERED from a predecessor's audit rather
#: than consumed itself.  It matters because an outstanding acknowledgement is an
#: obligation of the process that consumed the delivery: the successor cannot discharge
#: a predecessor's, and can only resolve it when the runtime redelivers.  Treating a
#: recovered row as this process's own obligation would block the very waiter the
#: redelivery has to arrive on -- fail-closed into a permanent stall.
DELIVERY_RECOVERED_FIELD = "recovered"

DELIVERY_STATES = (
    DELIVERY_STATE_PROCESSED,
    DELIVERY_STATE_ACKNOWLEDGED,
    DELIVERY_STATE_ACK_FAILED,
)


def worker_done_provenance(
    payload: Any, *, expected_task_id: str, expected_dispatch_id: str
) -> tuple[str, str]:
    """Does this ``worker_done`` payload belong to the waiter that is waiting?

    Returns ``(PROVENANCE_ADOPT, "")`` or ``(PROVENANCE_MISMATCH, reason)``.  Both
    identities are required: a payload that names only one of them proves nothing about
    the other, and a payload that is not even a mapping proves nothing at all.

    This is the *waiter-side* filter and it deliberately duplicates the identity clause
    of the pre-mutation settlement gate.  They answer different questions at different
    moments -- "is this my result?" before adoption, and "may I mutate lifecycle state
    for this dispatch?" before the first command -- and the recorded failure is
    precisely that only the second one existed.
    """
    if not isinstance(payload, dict):
        return PROVENANCE_MISMATCH, (
            f"worker_done payload is {type(payload).__name__}, not an object, so it "
            "carries no provable identity"
        )
    expected = {"dispatchId": expected_dispatch_id, "taskId": expected_task_id}
    for field_name in IDENTITY_FIELDS:
        reported = payload.get(field_name)
        if reported is None:
            return PROVENANCE_MISMATCH, (
                f"worker_done carries no {field_name}; expected "
                f"{expected[field_name]!r}"
            )
        if reported != expected[field_name]:
            return PROVENANCE_MISMATCH, (
                f"worker_done {field_name} is {reported!r}, not "
                f"{expected[field_name]!r}"
            )
    return PROVENANCE_ADOPT, ""


def delivery_disposition(
    delivery_id: str, ledger: dict[str, dict[str, Any]]
) -> tuple[str, str]:
    """What must happen to this delivery: process, resume, recover, or replay?

    ``ledger`` maps delivery id to the row this run recorded for it
    (``DELIVERY_STATE_FIELD``, a ``replays`` count, and the two settlement flags).

    The five answers are decided by how far the previous handling of this delivery
    got, which is exactly what makes a crash at ANY boundary recoverable:

    * never seen              -> ``DELIVERY_PROCESS``
    * consumed, nothing claimed, nothing settled, never acknowledged
                              -> ``DELIVERY_RESUME``: nothing was mutated for it, so
                                 the redelivery is the recovery path.  Treating it as a
                                 replay here would LOSE the result.
    * settlement claimed, never recorded settled
                              -> ``DELIVERY_RECOVER``: a lifecycle command may already
                                 have gone out, so re-processing could DUPLICATE it.
    * settled or acknowledged -> ``DELIVERY_REPLAY``: acknowledge again, zero lifecycle
                                 action, until ``DELIVERY_REPLAY_LIMIT`` is exhausted
                                 and the caller fails closed.
    """
    row = ledger.get(delivery_id)
    if row is None:
        return DELIVERY_PROCESS, ""
    acknowledged = row.get(DELIVERY_STATE_FIELD) == DELIVERY_STATE_ACKNOWLEDGED
    settled = bool(row.get(DELIVERY_SETTLED_FIELD))
    if not acknowledged and not settled:
        if row.get(DELIVERY_SETTLEMENT_CLAIMED_FIELD):
            return DELIVERY_RECOVER, (
                f"delivery {delivery_id} claimed a settlement that was never recorded "
                "as finished; a claim carries no proof of how many lifecycle commands "
                "already went out, so the Coordinator recovers explicitly instead of "
                "repeating one"
            )
        return DELIVERY_RESUME, (
            f"delivery {delivery_id} was consumed by an earlier process, settled "
            "nothing and was never acknowledged; nothing was mutated for it, so it is "
            "processed again rather than discarded as a replay"
        )
    replays = int(row.get("replays") or 0) + 1
    if replays > DELIVERY_REPLAY_LIMIT:
        return DELIVERY_REPLAY_EXHAUSTED, (
            f"delivery {delivery_id} has been replayed {replays} times, past the "
            f"limit of {DELIVERY_REPLAY_LIMIT}; the acknowledgement is not being "
            "consumed, so the Coordinator fails closed instead of looping"
        )
    return DELIVERY_REPLAY, (
        f"delivery {delivery_id} was already processed by this run (state "
        f"{row.get(DELIVERY_STATE_FIELD)!r}, replay {replays}); acknowledging it "
        "again and taking no lifecycle action"
    )
