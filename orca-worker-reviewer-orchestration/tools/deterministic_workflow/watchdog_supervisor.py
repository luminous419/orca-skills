"""OS-43 D-7: the Supervisor loop -- detect, gate, invoke, observe, react.

Runtime-neutral by construction: every dependency arrives as an injected port and this
module names no concrete implementation, which is the whole of CON-5.  It reaches the
engine ONLY through :class:`ports.RecoveryInvocationPort`, so recoverability, the next
node, the claim, the fence and the resume semantics are unreachable from here -- not by
convention, but because there is no other route.

The loop records the returned ``status`` and ``code`` VERBATIM and interprets nothing
beyond the closed set.  It never reads a ``WorkflowState`` field, never calls
``routing.route``, never opens the checkpoint store, and never touches an approval port.

**Sweep amplification is contained here, not tuned later.**  Each observed run costs two
``orca`` CLI calls per sweep (``turn_boundary.py:369-380``), so an unbounded sweep over
many runs invites rate limiting, which raises ``TurnBoundaryUnavailable`` and turns the
whole fleet into R1.  ``interval_seconds`` therefore defaults to
``pause_store.observe_timeout_for(lease_seconds)`` -- lease-derived, never hard-coded, for
the same reason ``heartbeat_interval_for`` is -- and ``max_concurrent_runs`` bounds the
per-sweep pool.

**Graceful shutdown never interrupts an in-flight invocation.**  The flag is consulted at
gate step 2 -- between sweeps and between runs within a sweep -- and never inside step 3,
because the claimed section is inside the ENGINE: a Watchdog that tore down an in-flight
``recover`` would leave the engine mid-``graph.invoke()``.  The Watchdog itself holds no
claim, ever, so a clean shutdown owes nothing.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from . import ports
from . import watchdog_audit as audit_module
from . import watchdog_observation, watchdog_state
from .watchdog_classifier import (ACTIONABLE_STATES, PAUSE_CONTINUATION_RECOVERABLE,
                                  STALLED_RECOVERABLE, classify)
from .watchdog_state import GATE_ACT, gate, react

DEFAULT_MAX_CONCURRENT_RUNS = 4
DEFAULT_LEASE_SECONDS = 60.0

#: Transcribed verbatim from ``recovery_runtime``; see the note in ``watchdog_state``.
RECOVERY_KIND_STALLED_ACTIVE = "stalled_active"
RECOVERY_KIND_PAUSE_CONTINUATION = "pause_continuation"

_STATE_TO_KIND = {
    STALLED_RECOVERABLE: RECOVERY_KIND_STALLED_ACTIVE,
    PAUSE_CONTINUATION_RECOVERABLE: RECOVERY_KIND_PAUSE_CONTINUATION,
}


@dataclass
class RunReport:
    run_id: str
    state: str = ""
    rule_index: int = 0
    gate_action: str = ""
    gate_reason: str = ""
    outcome_status: str = ""
    outcome_code: str = ""
    escalation: str = ""
    recovery_id: str = ""
    detail: str = ""
    #: The engine was invoked FOR THIS RUN and returned one closed outcome.  A gate that
    #: said ACT is not enough: an invocation that raised before producing an outcome
    #: performed nothing, and counting it as work done is how a wiring defect reads as a
    #: recovered fleet.
    acted: bool = False


@dataclass
class SweepReport:
    runs_observed: int = 0
    runs_acted: int = 0
    runs: list[RunReport] = field(default_factory=list)
    escalations: list[str] = field(default_factory=list)
    shutdown: bool = False

    @property
    def exit_code(self) -> int:
        """``0`` nothing to do / all handled; ``1`` at least one escalation."""
        return 1 if self.escalations else 0


def sweep(*, discovery: Any, observation: Any, liveness: Any, recovery: Any,
          audit: Any, clock: Any = None, shutdown: Any = None,
          max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
          budget: int = watchdog_state.WATCHDOG_RETRY_BUDGET_DEFAULT,
          conflict_cap: int = watchdog_state.WATCHDOG_CONFLICT_CAP_DEFAULT,
          lease_seconds: float = DEFAULT_LEASE_SECONDS,
          run_ids: tuple[str, ...] = ()) -> SweepReport:
    """One sweep: the five steps of the approved model, over the discovered run set."""
    report = SweepReport()
    try:
        listings = tuple(discovery.discover())
    except OSError as exc:
        # An unreadable RUNS ROOT is "unknown", never "empty": a sweep that silently saw
        # no runs is exactly the truncated history this design refuses everywhere else.
        raise RuntimeError(
            f"the watchdog could not enumerate the runs root ({exc}); a sweep that cannot "
            "see the run set reports nothing rather than an empty fleet") from exc
    targets = [str(row.get("run_id") or "") for row in listings
               if row.get("run_id") and (not run_ids or row.get("run_id") in run_ids)]
    report.runs_observed = len(targets)
    if not targets:
        return report

    def one(run_id: str) -> RunReport:
        return _sweep_run(run_id, observation=observation, liveness=liveness,
                          recovery=recovery, audit=audit, clock=clock,
                          shutdown=shutdown, budget=budget, conflict_cap=conflict_cap,
                          lease_seconds=lease_seconds)

    workers = max(1, min(int(max_concurrent_runs), len(targets)))
    if workers == 1:
        results = [one(run_id) for run_id in targets]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, targets))
    for row in results:
        report.runs.append(row)
        # ``acted``, not ``gate_action``: the gate is a DECISION to act, and a run whose
        # invocation never produced an outcome was not acted upon however the gate ruled.
        if row.acted:
            report.runs_acted += 1
        if row.escalation:
            report.escalations.append(f"{row.run_id}:{row.escalation}")
    report.shutdown = bool(shutdown is not None and _is_set(shutdown))
    return report


def _is_set(flag: Any) -> bool:
    is_set = getattr(flag, "is_set", None)
    return bool(is_set()) if callable(is_set) else bool(flag)


def _sweep_run(run_id: str, *, observation: Any, liveness: Any, recovery: Any,
               audit: Any, clock: Any, shutdown: Any, budget: int, conflict_cap: int,
               lease_seconds: float) -> RunReport:
    row = RunReport(run_id=run_id)
    # ---- 1. DETECT -------------------------------------------------------------------
    ledger_unreadable = False
    try:
        ledger: Mapping[str, Mapping[str, Any]] | None = audit.fold(run_id)
    except audit_module.WatchdogAuditError as exc:
        # A restart that can read NOTHING reports unknown and fails closed.  It does not
        # start with a fresh budget.  The ledger EXISTS and refused, so it is UNREADABLE
        # (F1 => R1), not uncovered.
        ledger, ledger_unreadable = None, True
        row.detail = str(exc)
    snapshot = watchdog_observation.snapshot(run_id, observation=observation,
                                             liveness=liveness, clock=clock,
                                             ledger=ledger,
                                             ledger_unreadable=ledger_unreadable)
    classification = classify(snapshot)
    row.state, row.rule_index = classification.state, classification.rule_index
    recovery_id = ""
    if classification.state in ACTIONABLE_STATES:
        recovery_id = recovery.identity(
            run_id=run_id, thread_id=snapshot.thread_id,
            checkpoint_ns=snapshot.checkpoint_ns,
            head_checkpoint_id=snapshot.head_checkpoint_id,
            recovery_kind=_STATE_TO_KIND[classification.state])
    row.recovery_id = recovery_id
    _append(audit, run_id, audit_module.EVENT_WATCHDOG_DETECTED, {
        "classified_state": classification.state, "rule_index": classification.rule_index,
        "facts": dict(snapshot.facts), "support": dict(snapshot.support),
        "snapshot_digest": snapshot.snapshot_digest,
        "status_authority": snapshot.status_authority,
        "liveness_status": snapshot.liveness_status})

    # ---- 2. GATE ---------------------------------------------------------------------
    decision = gate(classification, recovery_id=recovery_id, ledger=ledger,
                    liveness_status=snapshot.liveness_status,
                    shutdown=_is_set(shutdown) if shutdown is not None else False,
                    clock=clock, budget=budget)
    row.gate_action, row.gate_reason = decision.action, decision.reason
    if decision.action != GATE_ACT:
        _append(audit, run_id, audit_module.EVENT_WATCHDOG_GATE_DECLINED,
                {"decline_reason": decision.reason, "recovery_id": recovery_id,
                 "classified_state": classification.state})
        row.escalation = _escalate_undecidable(audit, run_id, classification, snapshot,
                                               decision,
                                               ledger_unreadable=ledger_unreadable)
        return row

    # ---- the claim record is published BEFORE the invocation, and it is a GATE --------
    try:
        _append(audit, run_id, audit_module.EVENT_WATCHDOG_CLAIM_OPENED, {
            "recovery_id": recovery_id,
            "recovery_kind": _STATE_TO_KIND[classification.state],
            "thread_id": snapshot.thread_id, "checkpoint_ns": snapshot.checkpoint_ns,
            "head_before": snapshot.head_checkpoint_id}, gating=True)
    except audit_module.WatchdogAuditError as exc:
        row.gate_action, row.gate_reason = "DECLINE", "audit_unavailable"
        row.escalation = "escalation_audit_unavailable"
        row.detail = str(exc)
        return row

    # ---- 3. INVOKE -- the ONLY action, and it is the engine's ------------------------
    try:
        request = _request(recovery, run_id, snapshot, classification)
        outcome = recovery.recover(request)
    except ports.RecoveryPreconditionUnavailable as exc:
        # A NAMED refusal from the wiring: this run has nothing to invoke, so nothing was
        # claimed, nothing ran and nothing was performed.  It is reported by its own code
        # and -- unlike the generic branch below -- it is unambiguously not an action.
        row.detail = f"{exc.code}: {exc.detail}"
        row.escalation = "escalation_unsupported_capability"
        _append(audit, run_id, audit_module.EVENT_WATCHDOG_ESCALATED, {
            "recovery_id": recovery_id, "escalation": row.escalation,
            "outcome_code": exc.code, "outcome_status": "", "detail": row.detail,
            "attempt_ordinal": decision.attempt_ordinal,
            "rule_index": classification.rule_index})
        return row
    except Exception as exc:                              # noqa: BLE001 - see below
        # An engine failure the closed outcome set cannot express is ONE run's problem,
        # not the fleet's: a sweep that died here would leave every other stalled run
        # unobserved, which is a worse failure than the one it is reporting.  It is
        # recorded and escalated -- never swallowed, and never treated as a recovery.
        row.detail = f"{type(exc).__name__}: {exc}"
        row.escalation = "escalation_observation_undecidable"
        _append(audit, run_id, audit_module.EVENT_WATCHDOG_ESCALATED, {
            "recovery_id": recovery_id, "escalation": row.escalation,
            "outcome_code": "", "outcome_status": "", "detail": row.detail,
            "attempt_ordinal": decision.attempt_ordinal,
            "rule_index": classification.rule_index})
        return row
    row.acted = True
    row.outcome_status = str(outcome.status)
    row.outcome_code = str(outcome.code)

    # ---- 4. OBSERVE -- recorded verbatim, interpreted only against the closed set -----
    _append(audit, run_id, audit_module.EVENT_WATCHDOG_RESUME_OUTCOME, {
        "recovery_id": recovery_id, "outcome_status": outcome.status,
        "outcome_code": outcome.code, "head_before": outcome.head_before,
        "head_after": outcome.head_after,
        "effect_performed": bool(outcome.effect_performed),
        "resumed_checkpoint_id": outcome.resumed_checkpoint_id,
        "revalidation_codes": list(outcome.revalidation_codes)})

    # ---- 5. REACT -- the total outcome -> action table -------------------------------
    stored = dict((ledger or {}).get(recovery_id) or watchdog_state.empty_row(recovery_id))
    reaction = react(str(outcome.status), row=stored,
                     attempt_ordinal=decision.attempt_ordinal,
                     lease_seconds=lease_seconds, clock=clock, budget=budget,
                     conflict_cap=conflict_cap)
    if reaction.backoff_until or reaction.budget_consumed:
        _append(audit, run_id, audit_module.EVENT_WATCHDOG_FAILURE, {
            "recovery_id": recovery_id, "outcome_status": outcome.status,
            "outcome_code": outcome.code,
            "attempt_ordinal": decision.attempt_ordinal,
            "budget_remaining": max(int(budget) - decision.attempt_ordinal, 0),
            "backoff_until": reaction.backoff_until})
    if reaction.escalation:
        _append(audit, run_id, audit_module.EVENT_WATCHDOG_ESCALATED, {
            "recovery_id": recovery_id, "escalation": reaction.escalation,
            "outcome_code": outcome.code, "outcome_status": outcome.status,
            "attempt_ordinal": decision.attempt_ordinal,
            "rule_index": classification.rule_index})
        row.escalation = reaction.escalation
    return row


def _request(recovery: Any, run_id: str, snapshot: Any, classification: Any) -> Any:
    """Build the closed request through the ENGINE's own factory on the port.

    The supervisor never constructs engine types itself: it hands over observations and
    the adapter -- which is outside the core -- assembles them.  There is no argument here
    that could carry a verdict, a node, a phase or a token.
    """
    return recovery.build_request(run_id=run_id, recovery_kind=_STATE_TO_KIND[
        classification.state])


def _append(audit: Any, run_id: str, event: str, record: Mapping[str, Any], *,
            gating: bool = False) -> None:
    """Publish one ledger row.

    ``gating=True`` is the ``watchdog_claim_opened`` record: the ledger is the budget's
    ONLY source, so a claim record that cannot be published means the attempt it precedes
    must NOT be made, and the failure is re-raised.  Every other row is evidence and is
    best effort, which is OS-31's ``_audit`` discipline (``pause_runtime.py:101-106``).
    """
    try:
        audit.append(run_id, event, dict(record))
    except Exception:                                    # noqa: BLE001 - see docstring
        if gating:
            raise


def _escalate_undecidable(audit: Any, run_id: str, classification: Any, snapshot: Any,
                          decision: Any, *, ledger_unreadable: bool = False) -> str:
    """R1/R2 and ``audit_unavailable`` never degrade into silence.

    An observation this Watchdog can never make is an operator's problem, not a reason to
    keep looking, so the two fail-closed states escalate with the cause NAMED: an
    undeclared runtime capability is ``escalation_unsupported_capability``, while a
    safety-relevant fact no declared authority covers is
    ``escalation_observation_undecidable`` and names the uncovered fact ids -- the repair
    is an operator's declaration, not a retry.
    """
    from .watchdog_classifier import UNDECIDABLE_FAIL_CLOSED, UNSUPPORTED_FAIL_CLOSED
    if ledger_unreadable or decision.reason == "audit_unavailable":
        # The ledger is the budget's only source, so its own damage is named as such
        # rather than folded into the generic observation escalation.
        escalation = "escalation_audit_unavailable"
    elif classification.state == UNDECIDABLE_FAIL_CLOSED:
        escalation = "escalation_observation_undecidable"
    elif classification.state == UNSUPPORTED_FAIL_CLOSED:
        uncovered = snapshot.evidence.get("F11") or ()
        escalation = ("escalation_observation_undecidable" if uncovered
                      else "escalation_unsupported_capability")
    else:
        return ""
    _append(audit, run_id, audit_module.EVENT_WATCHDOG_ESCALATED, {
        "recovery_id": decision.recovery_id or "", "escalation": escalation,
        "outcome_code": "", "outcome_status": "",
        "uncovered_facts": list(snapshot.evidence.get("F11") or ()),
        "rule_index": classification.rule_index})
    return escalation


def run_once(**deps: Any) -> SweepReport:
    """Exactly one sweep, then return.  SC-11's one-shot half."""
    return sweep(**deps)


def run_continuous(*, interval_seconds: float | None = None,
                   max_sweeps: int | None = None, shutdown: Any = None,
                   waiter: Any = None, **deps: Any) -> SweepReport:
    """Sweep, wait on the shutdown event, repeat.  SC-11 / SC-12.

    ``interval_seconds`` defaults to the store's own lease-derived observation window, so
    the cadence is never a hard-coded constant.  ``waiter`` is the injection point that
    makes the loop testable without sleeping, exactly as ``LeaseKeeper``'s is.
    """
    from .pause_store import observe_timeout_for
    flag = shutdown if shutdown is not None else threading.Event()
    lease_seconds = float(deps.get("lease_seconds", DEFAULT_LEASE_SECONDS))
    interval = (float(interval_seconds) if interval_seconds is not None
                else observe_timeout_for(lease_seconds))
    wait = waiter or (lambda event, seconds: event.wait(seconds))
    last = SweepReport()
    sweeps = 0
    while True:
        if _is_set(flag):
            break
        last = sweep(shutdown=flag, **deps)
        sweeps += 1
        if max_sweeps is not None and sweeps >= int(max_sweeps):
            break
        if wait(flag, interval):
            break
    audit = deps.get("audit")
    if audit is not None:
        for row in last.runs:
            _append(audit, row.run_id, audit_module.EVENT_WATCHDOG_SHUTDOWN,
                    {"sweeps": sweeps, "escalations": list(last.escalations)})
    last.shutdown = True
    return last
