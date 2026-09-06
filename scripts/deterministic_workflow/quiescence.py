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
#: The turn DECLARED a rest state that the authoritative run state does not support: a
#: run reported COMPLETED while Orca still holds a runnable or dispatched Task, or a
#: run reported ``WAITING_FOR_INPUT`` with no durable wait actually armed.  A rest state
#: a Coordinator merely asserts is a natural-language progress report wearing a status
#: name, and OS-44 exists because that is what ended the turn in ``run_c2166e75bb02``.
QUIESCENCE_UNSUPPORTED_REST_CLAIM = "QUIESCENCE_UNSUPPORTED_REST_CLAIM"

QUIESCENCE_REASON_CODES = (
    QUIESCENCE_OK,
    QUIESCENCE_NEXT_NODE_UNCONSUMED,
    QUIESCENCE_IDLE_NON_TERMINAL,
    QUIESCENCE_UNACKNOWLEDGED_DELIVERY,
    QUIESCENCE_UNKNOWN_STATUS,
    QUIESCENCE_UNSUPPORTED_REST_CLAIM,
)

#: The rest states that are claims about the RUN having ended rather than about
#: something being able to wake it.  Each one is corroborated against authoritative run
#: state before ``turn_end_verdict`` accepts it.
_ENDED_RUN_STATES = (SETTLED, ERROR, *TERMINAL_STATUSES)

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
#: OS-44 (BUGFIX-I3-MAJOR-1).  The wire acknowledgement was ISSUED and its outcome was
#: never recorded.  Published before the ``--ack`` command rather than after it, which
#: is the whole point: Orca accepts ``--ack`` and consumes the delivery before this
#: process can publish anything about it, so an audit that ends at ``delivery_settled``
#: cannot tell "the ack never went out" from "the ack went out and the process died".
#: Without that distinction a successor either re-drives a consumed delivery or, worse,
#: silently drops the acknowledgement outcome it owes.
DELIVERY_STATE_ACK_INTENT = "ack_intent"
#: OS-44 (BUGFIX-I3-MAJOR-1).  A successor process CLOSED an acknowledgement its
#: predecessor left open, by re-issuing the idempotent wire ack and recording the
#: outcome.  Distinct from ``acknowledged`` on purpose: the run's audit must say which
#: process discharged the obligation and that it was discharged by reconciliation rather
#: than in the normal flow.  It is a terminal ack state -- nothing is outstanding.
DELIVERY_STATE_ACK_RECONCILED = "ack_reconciled"

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
    DELIVERY_STATE_ACK_INTENT,
    DELIVERY_STATE_ACK_RECONCILED,
)

#: The two states in which this run owes nothing further for a delivery's
#: acknowledgement.  Everything else is an open obligation of some kind, and which kind
#: is what :func:`delivery_obligation` decides.
ACK_CLOSED_STATES = (DELIVERY_STATE_ACKNOWLEDGED, DELIVERY_STATE_ACK_RECONCILED)

#: The row slot recording that a wire acknowledgement was ISSUED for this delivery.
#: Published (as ``delivery_ack_intent``) strictly before the ``--ack`` command, so the
#: audit carries the intent even when the process dies inside the command.
DELIVERY_ACK_INTENT_FIELD = "ack_intent"

# ---- what a delivery row still owes ------------------------------------------------
# OS-44 (BUGFIX-I3-MAJOR-1).  The previous round collapsed four different situations
# into one boolean -- "is this row recovered?" -- and then EXCLUDED recovered rows from
# the pending check.  That made an incomplete acknowledgement disappear rather than be
# closed.  The exclusion existed for a real reason (counting a predecessor's
# awaiting-redelivery obligation refuses to arm the very waiter the redelivery must
# arrive on), so the fix is to distinguish the states rather than to drop either rule.

#: Nothing outstanding.
OBLIGATION_NONE = "none"
#: THIS process consumed the delivery and has not acknowledged it.  Blocks the next
#: waiter and blocks the turn end -- the original OS-44 rule, unchanged.
OBLIGATION_ACK_PENDING = "ack_pending"
#: A predecessor got far enough that redelivery can no longer be relied on to close the
#: acknowledgement: it recorded the settlement, or it issued the wire ack, or both.  A
#: successor MUST close this deterministically by reconciling; it must not wait for a
#: redelivery that Orca has no obligation to send, and it must take no lifecycle action.
OBLIGATION_ACK_RECONCILE = "ack_reconcile"
#: A predecessor consumed the delivery, mutated nothing and never reached the ack, so
#: Orca still holds it and replays it until acknowledged.  This obligation is discharged
#: BY the redelivery, so it must not block arming the waiter that redelivery arrives on
#: -- but it is a runnable next action, not rest, so it does block ending the turn.
OBLIGATION_AWAITING_REDELIVERY = "awaiting_redelivery"
#: A predecessor claimed the settlement and never recorded finishing it.  A lifecycle
#: command may already have gone out, so this is recovered explicitly and never
#: re-driven; see ``DELIVERY_RECOVER``.
OBLIGATION_RECOVER = "recover"

DELIVERY_OBLIGATIONS = (
    OBLIGATION_NONE,
    OBLIGATION_ACK_PENDING,
    OBLIGATION_ACK_RECONCILE,
    OBLIGATION_AWAITING_REDELIVERY,
    OBLIGATION_RECOVER,
)

#: The two obligations a REDELIVERY discharges, and the only two the waiter gate may
#: stand aside for.  Both belong to a predecessor that never reached the wire ack, so
#: Orca still holds the delivery and replays it until it is acknowledged -- on the very
#: waiter that counting them would refuse to arm.  Standing aside is not hiding them:
#: they remain in :func:`delivery_obligation`'s answer, the turn-end boundary reports
#: them as runnable work rather than rest, and ``OBLIGATION_RECOVER`` additionally fails
#: closed when the redelivery is settled.  Every OTHER obligation blocks, including a
#: predecessor's settled-but-unacknowledged row -- that one cannot be discharged by a
#: redelivery that may never come, so it is closed by reconciliation instead.
REDELIVERY_RESOLVED_OBLIGATIONS = (
    OBLIGATION_AWAITING_REDELIVERY,
    OBLIGATION_RECOVER,
)


def delivery_obligation(row: dict[str, Any]) -> str:
    """What one delivery row still owes.  Pure; the single classifier both readers use.

    ``row`` is a delivery-ledger row: the in-memory one the Coordinator holds, or the
    one :func:`run_logging.replay_delivery_ledger` folds out of the durable audit.  The
    two must never disagree about whether something is outstanding, which is why the
    harness and the turn-end CLI both decide it here instead of each spelling the rule.

    A row this process recovered from a predecessor's audit is judged by HOW FAR the
    predecessor got, not by the fact that it was recovered.
    """
    if row.get(DELIVERY_STATE_FIELD) in ACK_CLOSED_STATES:
        return OBLIGATION_NONE
    recovered = bool(row.get(DELIVERY_RECOVERED_FIELD))
    if not recovered:
        # This process consumed it, so the acknowledgement is its own to discharge.
        return OBLIGATION_ACK_PENDING
    if row.get(DELIVERY_SETTLEMENT_CLAIMED_FIELD) and not row.get(
        DELIVERY_SETTLED_FIELD
    ):
        return OBLIGATION_RECOVER
    if row.get(DELIVERY_SETTLED_FIELD) or row.get(DELIVERY_ACK_INTENT_FIELD):
        return OBLIGATION_ACK_RECONCILE
    return OBLIGATION_AWAITING_REDELIVERY


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
    acknowledged = row.get(DELIVERY_STATE_FIELD) in ACK_CLOSED_STATES
    settled = bool(row.get(DELIVERY_SETTLED_FIELD))
    # OS-44 (BUGFIX-I3-MAJOR-1).  A recorded ack INTENT is as disqualifying as a
    # recorded settlement: the wire ``--ack`` was issued, so Orca may already have
    # consumed the delivery and this cannot be re-driven as a first processing.
    ack_issued = bool(row.get(DELIVERY_ACK_INTENT_FIELD))
    if not acknowledged and not settled and not ack_issued:
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


# ---- the executable turn-end boundary's judgement ----------------------------------


def turn_end_verdict(
    *,
    run_status: str,
    next_node: str = "",
    active_dispatches: int = 0,
    unacknowledged_deliveries: tuple[str, ...] | list[str] = (),
    runnable_actions: tuple[str, ...] | list[str] = (),
    durable_wait_armed: bool = False,
) -> dict[str, Any]:
    """:func:`quiescence_verdict` with the DECLARED rest state corroborated.  Pure.

    OS-44 (BUGFIX-I3-CRITICAL-1).  ``quiescence_verdict`` answers the question a
    Coordinator asks itself, and it takes the reported ``run_status`` at face value
    because inside one process that status is the process's own state.  An executable
    boundary invoked by a *prompt-driven* Coordinator cannot do that: "COMPLETED" is
    then a claim a language model typed, and accepting it unchecked reduces the whole
    gate to the natural-language progress report OS-44 exists to refuse.

    So the two extra inputs here are authoritative observations, not claims:

    ``runnable_actions``
        Work the run's own state says is runnable right now and has not been started --
        Orca Tasks that are unblocked and undispatched, plus any route token the OS-40
        checkpoint produced.  A declared ENDED run with runnable work is refused, and a
        runnable action with no active dispatch is the ``run_c2166e75bb02`` shape.
    ``durable_wait_armed``
        Whether a durable artifact for the human wait actually exists.  A declared
        ``WAITING_FOR_INPUT`` with nothing armed is a stall wearing a rest state's name.

    The check order of :func:`quiescence_verdict` is preserved exactly -- an
    unacknowledged delivery still outranks every status question -- and this function
    can only ever downgrade a quiescent verdict, never upgrade a refusal.
    """
    pending_actions = tuple(str(action) for action in runnable_actions if str(action))
    verdict = quiescence_verdict(
        run_status=run_status,
        next_node=next_node or (pending_actions[0] if pending_actions else ""),
        active_dispatches=active_dispatches,
        unacknowledged_deliveries=unacknowledged_deliveries,
    )
    verdict["runnable_actions"] = list(pending_actions)
    verdict["durable_wait_armed"] = bool(durable_wait_armed)
    if not verdict["quiescent"]:
        return verdict
    if run_status == WAITING_FOR_INPUT and not durable_wait_armed:
        verdict["quiescent"] = False
        verdict["reason_code"] = QUIESCENCE_UNSUPPORTED_REST_CLAIM
        verdict["detail"] = (
            "the turn declares WAITING_FOR_INPUT but no durable wait is armed for this "
            "run; a human decision that exists only in the response text cannot wake "
            "the run, so the turn must arm the durable wait before it ends"
        )
        return verdict
    if run_status in _ENDED_RUN_STATES and (pending_actions or active_dispatches):
        verdict["quiescent"] = False
        verdict["reason_code"] = QUIESCENCE_UNSUPPORTED_REST_CLAIM
        verdict["detail"] = (
            f"the turn declares {run_status} but the run's own state does not support "
            f"it: {active_dispatches} dispatch(es) still active and runnable work "
            f"outstanding ({', '.join(pending_actions) or 'none'}); a run that has "
            "ended has neither"
        )
    return verdict
