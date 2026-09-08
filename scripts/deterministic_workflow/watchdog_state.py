"""OS-43 D-6: the action gate -- retry budget, backoff, liveness, shutdown, escalation.

A SECOND ordered decision, deliberately kept out of the classifier so the classifier stays
a pure function of the observation snapshot.  The classifier answers "what is this run?";
this module answers "what may *this* Watchdog do about it right now?", and the two questions
have different inputs: this one reads a clock, a durable budget and a shutdown flag, none of
which is an observation of the run.

The evaluation order is first-match-wins and the order IS the contract, exactly as it is
one layer up.  Liveness sits BELOW budget and backoff, so a run whose Coordinator has just
died does not skip a pending backoff, and ABOVE ``ACT``, so AC-1's named premise is a hard
precondition rather than a hint.

Nothing here is a workflow decision: the gate can only ever DECLINE.  It cannot make a
non-actionable classification actionable, and it never computes a next node, a phase or a
verdict.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .watchdog_classifier import Classification

# ---- transcribed vocabularies ----------------------------------------------------------
# Kept as literals rather than imports on purpose: CON-1 requires the Watchdog core's
# transitive import closure to hold no routing, graph, executor or claim module, and
# ``recovery_runtime`` reaches all of those.  Contract tests assert each of these equals the
# engine's own constant, so they cannot drift.
OUTCOME_RECOVERED = "RECOVERED"
OUTCOME_NO_EFFECT = "NO_EFFECT"
OUTCOME_NOT_RECOVERABLE = "NOT_RECOVERABLE"
OUTCOME_CONFLICT = "CONFLICT"
OUTCOME_UNSUPPORTED = "UNSUPPORTED"
OUTCOME_REFUSED = "REFUSED"
RECOVERY_OUTCOMES = (OUTCOME_RECOVERED, OUTCOME_NO_EFFECT, OUTCOME_NOT_RECOVERABLE,
                     OUTCOME_CONFLICT, OUTCOME_UNSUPPORTED, OUTCOME_REFUSED)

LIVENESS_LIVE = "LIVE"
LIVENESS_EXPIRED = "EXPIRED"
LIVENESS_ABSENT = "ABSENT"
LIVENESS_UNREADABLE = "UNREADABLE"

# ---- the gate --------------------------------------------------------------------------
GATE_ACT = "ACT"
GATE_DECLINE = "DECLINE"
GATE_DECLINE_REASONS = (
    "not_actionable",        # one of the ten observe-and-report states
    "shutdown_requested",    # SC-12; consulted BETWEEN runs, never mid-call
    "identity_terminal",     # a NOT_RECOVERABLE / UNSUPPORTED verdict already stands (AC-7)
    "budget_exhausted",      # SC-7
    "backoff_pending",       # SC-7
    "liveness_live",         # AC-1's premise is not satisfied: the Coordinator is alive
    "liveness_absent",       # AC-1's premise is not satisfied: no lease was ever published
    "audit_unavailable",     # the ledger could not be written: no record, no attempt
)

WATCHDOG_RETRY_BUDGET_DEFAULT = 5          # attempts per recovery_id
WATCHDOG_CONFLICT_CAP_DEFAULT = 8          # consecutive CONFLICTs before escalating
WATCHDOG_BACKOFF_CAP_MULTIPLIER = 16.0     # x lease_seconds
WATCHDOG_UNDECIDABLE_SWEEPS_DEFAULT = 3    # consecutive R1/R2 sweeps before escalating

WATCHDOG_ESCALATIONS = ("escalation_budget_exhausted", "escalation_state_conflict",
                        "escalation_not_recoverable", "escalation_unsupported_capability",
                        "escalation_observation_undecidable", "escalation_audit_unavailable")


@dataclass(frozen=True)
class GateDecision:
    action: str              # GATE_ACT | GATE_DECLINE
    reason: str              # "" for ACT; a GATE_DECLINE_REASONS member otherwise
    recovery_id: str = ""
    attempt_ordinal: int = 0
    backoff_until: float = 0.0

    def __post_init__(self) -> None:
        if self.action not in (GATE_ACT, GATE_DECLINE):
            raise ValueError(f"unknown gate action: {self.action!r}")
        if self.action == GATE_ACT and self.reason:
            raise ValueError("an ACT decision carries no decline reason")
        if self.action == GATE_DECLINE and self.reason not in GATE_DECLINE_REASONS:
            raise ValueError(f"unknown decline reason: {self.reason!r}")


def backoff_delay(attempt_ordinal: int, *, lease_seconds: float) -> float:
    """Deterministic exponential backoff, DERIVED from the lease and never hard-coded.

    ``base`` is ``lease_keeper.heartbeat_interval_for(lease_seconds)`` -- lease/3 -- for the
    same reason that function derives the renewal period: a constant is wrong the moment
    the lease is reconfigured.

    No jitter, on purpose: jitter would make the fake-clock suite non-deterministic, and
    multi-host thundering-herd is out of scope (NG-4).  Every arithmetic input comes from
    the injected clock, so no test sleeps.
    """
    from .lease_keeper import heartbeat_interval_for
    base = heartbeat_interval_for(lease_seconds)
    ordinal = max(int(attempt_ordinal), 1)
    return min(base * (2.0 ** (ordinal - 1)),
               WATCHDOG_BACKOFF_CAP_MULTIPLIER * float(lease_seconds))


def empty_row(recovery_id: str = "") -> dict[str, Any]:
    """What a run with NO ledger history folds to.  An absent ledger is not damage."""
    return {"recovery_id": recovery_id, "attempts": 0, "last_outcome": "",
            "last_code": "", "conflicts": 0, "backoff_until": 0.0, "escalation": "",
            "terminal": False, "head_before": "", "head_after": ""}


def gate(classification: Classification, *, recovery_id: str,
         ledger: Mapping[str, Mapping[str, Any]] | None,
         liveness_status: str, shutdown: bool = False, clock: Any = None,
         budget: int = WATCHDOG_RETRY_BUDGET_DEFAULT,
         audit_available: bool = True) -> GateDecision:
    """First match wins, and the order is the contract.

    ``ledger`` is ``None`` when the fold could not be completed.  That is NOT an empty
    budget: a restart that can read nothing reports unknown and fails closed rather than
    starting fresh (``ports.py:141-148``), so it declines with ``audit_unavailable``.
    """
    if not classification.actionable:
        return GateDecision(GATE_DECLINE, "not_actionable", recovery_id=recovery_id)
    if shutdown:
        return GateDecision(GATE_DECLINE, "shutdown_requested", recovery_id=recovery_id)
    if ledger is None:
        return GateDecision(GATE_DECLINE, "audit_unavailable", recovery_id=recovery_id)
    row = dict(ledger.get(recovery_id) or empty_row(recovery_id))
    if row.get("terminal"):
        # AC-7 mechanism 4: a refusal is sticky PER IDENTITY, durably.  Since the identity
        # contains the committed head, a run that genuinely advances yields a DIFFERENT
        # identity and is legitimately reconsidered; one that has not moved is not.
        return GateDecision(GATE_DECLINE, "identity_terminal", recovery_id=recovery_id,
                            attempt_ordinal=int(row.get("attempts") or 0))
    attempts = int(row.get("attempts") or 0)
    if attempts >= int(budget):
        return GateDecision(GATE_DECLINE, "budget_exhausted", recovery_id=recovery_id,
                            attempt_ordinal=attempts)
    backoff_until = float(row.get("backoff_until") or 0.0)
    now = float(clock.time()) if clock is not None else 0.0
    if backoff_until > now:
        return GateDecision(GATE_DECLINE, "backoff_pending", recovery_id=recovery_id,
                            attempt_ordinal=attempts, backoff_until=backoff_until)
    if liveness_status == LIVENESS_LIVE:
        return GateDecision(GATE_DECLINE, "liveness_live", recovery_id=recovery_id,
                            attempt_ordinal=attempts)
    if liveness_status != LIVENESS_EXPIRED:
        # ABSENT and UNREADABLE alike: absence of evidence is not evidence of death, and
        # an unreadable lease is unknown.  Neither satisfies AC-1's named premise.
        return GateDecision(GATE_DECLINE, "liveness_absent", recovery_id=recovery_id,
                            attempt_ordinal=attempts)
    if not audit_available:
        return GateDecision(GATE_DECLINE, "audit_unavailable", recovery_id=recovery_id,
                            attempt_ordinal=attempts)
    return GateDecision(GATE_ACT, "", recovery_id=recovery_id,
                        attempt_ordinal=attempts + 1)


@dataclass(frozen=True)
class Reaction:
    """What the outcome does to THIS identity's durable state.  Total; no default branch."""

    budget_consumed: bool
    backoff_until: float
    terminal: bool
    escalation: str
    conflicts: int


def react(outcome_status: str, *, row: Mapping[str, Any], attempt_ordinal: int,
          lease_seconds: float, clock: Any = None,
          budget: int = WATCHDOG_RETRY_BUDGET_DEFAULT,
          conflict_cap: int = WATCHDOG_CONFLICT_CAP_DEFAULT) -> Reaction:
    """The outcome -> action table.  TOTAL over the closed outcome set.

    Three rows are not obvious and each is deliberate:

    * **CONFLICT does not consume the recovery budget.**  Another claimant working
      successfully is not this Watchdog failing, and consuming budget there would exhaust
      it without a single failed recovery.  It gets its own bounded counter instead, so a
      permanently stuck foreign lease still escalates (SC-8's "state conflict").
    * **NOT_RECOVERABLE and UNSUPPORTED are terminal on the FIRST occurrence**, not after
      the budget.  ``classify_head``'s evidence is durable and clock-free
      (``pause_runtime.py:460-461``) and a capability is never inferred from silence
      (``orca_adapter.py:64-71``), so retrying an unchanged head cannot change the answer.
      This is AC-7 made mechanical rather than remembered.
    * **REFUSED retries within the budget**, because a durable-state defect
      (``PAUSE_RECORD_CORRUPT``, ``RECOVERY_RECORD_CORRUPT``) can legitimately be repaired
      by an operator between sweeps.
    """
    if outcome_status not in RECOVERY_OUTCOMES:
        raise ValueError(f"unknown recovery outcome: {outcome_status!r}")
    now = float(clock.time()) if clock is not None else 0.0
    conflicts = int(row.get("conflicts") or 0)
    if outcome_status == OUTCOME_RECOVERED:
        return Reaction(False, 0.0, False, "", 0)
    if outcome_status == OUTCOME_NO_EFFECT:
        return Reaction(False, 0.0, True, "", 0)
    if outcome_status == OUTCOME_CONFLICT:
        conflicts += 1
        delay = backoff_delay(conflicts, lease_seconds=lease_seconds)
        escalation = ("escalation_state_conflict" if conflicts >= int(conflict_cap)
                      else "")
        return Reaction(False, now + delay, bool(escalation), escalation, conflicts)
    if outcome_status == OUTCOME_NOT_RECOVERABLE:
        return Reaction(True, 0.0, True, "escalation_not_recoverable", 0)
    if outcome_status == OUTCOME_UNSUPPORTED:
        return Reaction(True, 0.0, True, "escalation_unsupported_capability", 0)
    # OUTCOME_REFUSED
    delay = backoff_delay(attempt_ordinal, lease_seconds=lease_seconds)
    exhausted = attempt_ordinal >= int(budget)
    return Reaction(True, now + delay, exhausted,
                    "escalation_budget_exhausted" if exhausted else "", 0)
