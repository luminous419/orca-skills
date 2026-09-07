"""StateGraph node callables; only execute_intent crosses a port boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import (ARTIFACT_IDENTITY_DRIFT, BASE_CAPABILITIES, EVENT_REJECTION_CODES,
                        EXTERNAL_LOOKUP, EXTERNAL_RESUME, GATE_DEFECT_CODES,
                        GATE_REPAIR_EXHAUSTED, GATE_TERMINAL_CODES, MAX_REPAIR_ATTEMPTS,
                        EventValidationError, ExternalLookupUnavailable,
                        binding_snapshot, make_intent, validate_event)
from . import artifact_identity
from . import audit
from .lease_keeper import LeaseRenewalFailed, lease_keeper_factory
from . import pause_policy
from .routing import (active_correction_phase, downstream_revalidation_set,
                      final_review_binding_current, missing_capabilities, responsible_phases,
                      route, verify_final_review_binding)
from .runtime_state import (ALREADY_SETTLED, CREATED, DEFAULT_OBSERVE_TIMEOUT_SECONDS,
                            EFFECTED, SETTLED, RuntimeStateLeaseHeld,
                            RuntimeStateObservationTimeout, resolve_runtime_state)
from .state import StateError, normalize_malformed_state, validate_state


class IdempotencyRecoveryError(StateError):
    """A crash window cannot be reconciled, so the workflow stops instead of duplicating.

    ``code`` is the terminal reason a caller projects onto a BLOCKED terminal state; the
    exception exists so the refusal is loud rather than silently re-running the effect.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}:{message}")
        self.code = code
        self.detail = message


def _trace(state: dict[str, Any], node: str, **extra: Any) -> list[dict[str, Any]]:
    # Read defensively: VALIDATE normalizes malformed input, and this stays total anyway so
    # no trace append can turn a contracted BLOCKED terminal into an uncaught KeyError.
    trace = state.get("logical_trace")
    trace = trace if isinstance(trace, list) else []
    phase = state.get("current_phase")
    iterations = state.get("phase_iterations")
    entry = {"sequence": len(trace), "node": node,
             "route": state.get("route_token"), "phase": phase,
             "phase_iteration": iterations.get(phase) if isinstance(iterations, dict) else None,
             "final_review_iteration": state.get("final_review_iterations"),
             "role": state.get("pending_role"), "round_kind": state.get("round_kind"),
             "intent_id": (state.get("pending_intent") or {}).get("intent_id"),
             "event_id": (state.get("pending_event") or {}).get("event_id"),
             "gate": None, "terminal_status": state.get("terminal_status"), "reason_code": None}
    entry.update(extra); return [*trace, entry]


def validate_node(state: dict[str, Any]) -> dict[str, Any]:
    try: validate_state(state, expected_thread_id=state.get("thread_id", ""))
    except (StateError, TypeError, ValueError, KeyError, AttributeError) as exc:
        # Fail closed onto a *valid* state; returning the malformed dictionary made the very
        # next trace append raise KeyError instead of reaching BLOCKED/MALFORMED_STATE.
        return normalize_malformed_state(state, code="MALFORMED_STATE", message=str(exc))
    missing = missing_capabilities(BASE_CAPABILITIES, frozenset(state["adapter_capabilities"]))
    if missing:
        return {**state, "route_token": "BLOCK", "terminal_reason": {"code": "ADAPTER_CAPABILITY_MISSING", "message": ",".join(missing), "missing_capabilities": list(missing)}}
    return {**state, "logical_trace": _trace(state, "VALIDATE")}


def route_node(state: dict[str, Any]) -> dict[str, Any]:
    token = (state.get("route_token") or "BLOCK") if state.get("terminal_reason") else route(state)
    return {**state, "route_token": token, "logical_trace": _trace(state, "ROUTE", route=token)}


def prepare_intent_node(state: dict[str, Any]) -> dict[str, Any]:
    token = state["route_token"]
    new = deepcopy(state)
    if token == "PREPARE_REPAIR":
        # OS-42.  The SAME role, the SAME phase and the SAME round are re-asked; only the
        # representation was wrong.  Nothing about current_phase, phase_iterations,
        # correction_queue or correction_index moves.
        if new["pending_gate_defect"] is None:
            raise StateError("OUT_OF_ORDER_EVENT:repair without a defect")
        role, kind = new["pending_role"], new["round_kind"]
        new["repair_attempts"] += 1
        new["remaining_repair_budget"] -= 1
        # pending_gate_defect is deliberately STILL SET here: make_intent reads it to
        # build the intent's repair_instruction.  Clearing it before make_intent -- which
        # an earlier draft of this design did -- is exactly how a repair dispatch ends up
        # carrying no defect and re-asking for nothing.
    elif token == "PREPARE_FINAL_REVIEWER":
        role, kind = "FINAL_REVIEWER", "FINAL_REVIEW"
        new["final_review_iterations"] += 1
        new["remaining_final_budget"] -= 1
        new["final_reviewer_result"] = None
    elif token == "PREPARE_PHASE_REVIEWER": role, kind = "PHASE_REVIEWER", state["round_kind"]
    else:
        role = "WORKER"
        kind = {"PREPARE_CORRECTION": "CORRECTION", "PREPARE_REVALIDATION": "DOWNSTREAM_REVALIDATION"}.get(token, state["round_kind"])
        new["round_kind"] = kind
        if token == "PREPARE_CORRECTION" and new["correction_queue"]:
            correction_phase = active_correction_phase(new)
            if correction_phase is None: raise StateError("OUT_OF_ORDER_EVENT:correction queue consumed")
            new["current_phase"] = correction_phase
        new["worker_result"] = None; new["reviewer_result"] = None
    if token != "PREPARE_REPAIR":
        # Every ordinary preparation opens a NEW gate round, so the repair domain resets.
        new["repair_attempts"] = 0
        new["remaining_repair_budget"] = MAX_REPAIR_ATTEMPTS
        new["repair_binding"] = None
        new["pending_gate_defect"] = None
    new["pending_role"] = role
    intent = make_intent(new, role, kind)
    if intent["command_id"] in new["processed_command_ids"]:
        raise StateError("OUT_OF_ORDER_EVENT:processed command prepared")
    new["pending_intent"] = intent; new["intent_status"] = "PREPARED"; new["route_token"] = None
    if token == "PREPARE_REPAIR":
        # Cleared only NOW.  The defects have been copied onto the intent, where they are
        # digest-bound and survive a crash with the intent itself.  repair_binding is kept:
        # it says which gate round owns the counter, and clearing it would leave
        # repair_attempts >= 1 with nothing owning it.
        new["pending_gate_defect"] = None
    new["logical_trace"] = _trace(new, "PREPARE_INTENT")
    return new


def _still_owned(keeper: Any) -> None:
    """The ownership checkpoint taken before every write that follows an external call.

    ``keeper`` is optional only so the helpers below stay directly callable; on every path
    the executor takes, one is supplied and renewal is live for the whole blocking call.
    """
    if keeper is not None:
        keeper.raise_if_lost()


def _committed(keeper: Any, write: Any, *args: Any) -> Any:
    """Perform one ownership-sensitive write between two checkpoints, not just one.

    A checkpoint *before* the write is not enough, and the reason is easy to miss: a renewal
    that fails after that checkpoint does not necessarily rotate the lease token.  A
    ``RuntimeStateLockTimeout`` or a transient unreadable ledger leaves the token perfectly
    valid, so the fence -- correctly -- accepts the write that follows, and the recorded
    renewal failure would then be swallowed: the node would return success and advance the
    workflow on a claim it can no longer vouch for.

    So the checkpoint is taken again once the write returns.  Whatever the keeper learned
    while the write was in flight is honoured before this executor writes anything further,
    reports success, or advances any state.  The write that already landed is left standing
    on purpose: it is the durable record of an external effect that really did settle, and a
    successor claiming the intent adopts it as ``ALREADY_SETTLED`` instead of re-running it.
    What fails closed is *this* executor -- it names the loss as ``IDEMPOTENCY_LEASE_LOST``
    rather than pretending the claim was healthy.
    """
    _still_owned(keeper)
    result = write(*args)
    _still_owned(keeper)
    return result


def _settle_now(adapter: Any, runtime_state: Any, intent: dict[str, Any],
                lease_token: str, keeper: Any = None) -> dict[str, Any]:
    # The token travels with the effect: whatever the adapter writes about this intent is
    # fenced by the same lease this executor holds, so a predecessor that lost ownership
    # mid-``start`` cannot land its own external identity here.  ``start`` is the long
    # blocking call -- minutes, not milliseconds -- so the keeper renews the lease
    # throughout it and the checkpoint below refuses to settle if renewal ever failed.
    adapter.start(intent, lease_token=lease_token)
    _still_owned(keeper)
    event = adapter.settlement(intent["intent_id"])
    if event is None: raise StateError("OUT_OF_ORDER_EVENT:settlement missing")
    _committed(keeper, runtime_state.settle, intent["intent_id"], event, lease_token)
    return event


def _adapter_capabilities(adapter: Any) -> frozenset[str]:
    try:
        return frozenset(adapter.capabilities())
    except (AttributeError, TypeError):
        return frozenset()


def _collect(adapter: Any, runtime_state: Any, intent: dict[str, Any],
             receipt: dict[str, Any], lease_token: str, keeper: Any = None) -> dict[str, Any]:
    """Step 3 of the recovery ladder: settle from the effect that already exists."""
    intent_id = intent["intent_id"]
    if EXTERNAL_RESUME not in _adapter_capabilities(adapter):
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_UNSUPPORTED",
            f"{intent_id}: the adapter declares no {EXTERNAL_RESUME} capability, so an "
            "effect created by an earlier process can be neither observed nor collected")
    # ``resume`` blocks on the external runtime exactly like ``start`` does.
    event = adapter.resume(intent, receipt)
    _still_owned(keeper)
    if event is None:
        # The Task exists and is still running (or its outcome is unreadable).  Ownership
        # has been taken over and the effect observed; it is never re-created.
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_BLOCKED",
            f"{intent_id}: the existing external effect has not settled yet")
    _committed(keeper, runtime_state.settle, intent_id, event, lease_token)
    return event


def _recover(adapter: Any, runtime_state: Any, intent: dict[str, Any],
             record: dict[str, Any], lease_token: str, keeper: Any = None) -> dict[str, Any]:
    """Reconcile a claim an earlier owner left behind, without ever duplicating the effect.

    The ladder is fixed and fails closed at every rung:

    1. ask the adapter for a settlement of this stable identity;
    2. an ``EFFECTED`` record already names the external effect -- resume/observe it;
    3. a ``CLAIMED`` record names nothing, so look the effect up by stable intent identity;
    4. re-run **only** when the lookup proves no effect was created;
    5. when existence cannot be established, stop as BLOCKED rather than run it again.
    """
    intent_id = intent["intent_id"]
    event = adapter.settlement(intent_id)
    if event is not None:
        _committed(keeper, runtime_state.settle, intent_id, event, lease_token)
        return event
    if record.get("status") == EFFECTED:
        return _collect(adapter, runtime_state, intent, dict(record.get("receipt") or {}),
                        lease_token, keeper)
    # CLAIMED with no durable external identifier: the previous process died around the
    # creation call.  Only a lookup keyed on the stable intent identity can tell whether
    # the Task exists, and Orca's task-create exposes no idempotency key of its own.
    if EXTERNAL_LOOKUP not in _adapter_capabilities(adapter):
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_UNSUPPORTED",
            f"{intent_id}: the adapter declares no {EXTERNAL_LOOKUP} capability, so it "
            "cannot prove whether an external effect was created for this claim")
    try:
        found = adapter.lookup(intent)
    except ExternalLookupUnavailable as exc:
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_BLOCKED", f"{intent_id}: {exc}") from exc
    _still_owned(keeper)
    if found is None:
        return _settle_now(adapter, runtime_state, intent, lease_token, keeper)
    _committed(keeper, runtime_state.record_receipt, intent_id, dict(found), lease_token)
    return _collect(adapter, runtime_state, intent, dict(found), lease_token, keeper)


def _execute_recoverable(adapter: Any, runtime_state: Any, intent: dict[str, Any],
                         keeper_factory: Any = None) -> dict[str, Any]:
    """Claim the stable intent exclusively, then never create a second effect for it.

    ``claim`` is the whole ``lock -> read -> validate -> claim -> persist`` critical section,
    so two processes racing on one intent produce exactly one ``CREATED`` outcome; the loser
    either sees a live lease (and is refused as a would-be second executor) or, once that
    lease lapses, resumes into the recovery ladder above.

    A claim that is never renewed is only exclusive for one lease period, which is shorter
    than the external work it guards, so every path that blocks on the adapter runs inside a
    :class:`lease_keeper.LeaseKeeper`: it renews for the whole call and fails closed, turning
    a lost lease into a named BLOCKED terminal instead of a write the successor would refuse.
    """
    intent_id = intent["intent_id"]
    record = runtime_state.claim(intent)
    # ``claim`` is the only place a lease token is minted, and every ownership-sensitive
    # write below is fenced by it.  Carrying it explicitly -- instead of letting the ledger
    # treat "no token" as "no check" -- is what keeps a superseded executor out.
    lease_token = record["lease_token"]
    outcome = record.get("claim_outcome")
    if outcome == ALREADY_SETTLED:
        # Nothing external happens here, so there is no blocking window to keep alive.
        event = runtime_state.get_settlement(intent_id)
        if event is not None:
            return event
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_BLOCKED", f"{intent_id}: settled record without settlement")
    factory = keeper_factory or lease_keeper_factory()
    try:
        # ``__exit__`` stops and joins the beat thread on success, exception and cancellation
        # alike, so no keeper outlives the call it was renewing for -- and it *verifies* the
        # shutdown, raising ``LeaseKeeperNotStopped`` rather than leaving a revoked-but-live
        # thread behind that this executor would then be reporting success on top of.
        with factory(runtime_state, intent_id, lease_token) as keeper:
            if outcome == CREATED:
                return _settle_now(adapter, runtime_state, intent, lease_token, keeper)
            return _recover(adapter, runtime_state, intent, record, lease_token, keeper)
    except LeaseRenewalFailed as exc:
        # Fail closed and *stay* closed, for a renewal that failed and for a keeper that
        # could not be retired alike: neither is a reason to re-enter the claim path,
        # because this process may already have created the external effect.
        raise IdempotencyRecoveryError("IDEMPOTENCY_LEASE_LOST", str(exc)) from exc


def _observe_then_take_over(adapter: Any, ledger: Any, intent: dict[str, Any],
                            timeout_seconds: float, keeper_factory: Any = None) -> dict[str, Any]:
    """The observer role: another Coordinator owns this intent, so watch it, never re-run it.

    The wait is explicitly bounded.  When the owner settles, its settlement is adopted; when
    its lease lapses (a silently killed owner), ownership is taken over once and the recovery
    ladder decides what may be done; when neither happens by the deadline the run stops as
    BLOCKED instead of waiting forever or racing the owner.
    """
    intent_id = intent["intent_id"]
    try:
        record = ledger.observe(intent_id, timeout_seconds=timeout_seconds)
    except RuntimeStateObservationTimeout as exc:
        raise IdempotencyRecoveryError("IDEMPOTENCY_OBSERVATION_TIMEOUT", str(exc)) from exc
    if record is not None and record.get("status") == SETTLED:
        event = ledger.get_settlement(intent_id)
        if event is not None:
            return event
    try:
        return _execute_recoverable(adapter, ledger, intent, keeper_factory)
    except RuntimeStateLeaseHeld as exc:
        raise IdempotencyRecoveryError("IDEMPOTENCY_LEASE_HELD", str(exc)) from exc


def execute_intent_node(adapter: Any, runtime_state: Any = None, *,
                        observe_timeout_seconds: float = DEFAULT_OBSERVE_TIMEOUT_SECONDS,
                        heartbeat_interval_seconds: float | None = None,
                        keeper_factory: Any = None):
    """Build the EXECUTE_INTENT node, refusing to run without a durable ledger.

    The port is resolved once, at construction, so a path that cannot be crash-safe fails
    before any state is processed rather than at the moment it would create the effect.
    There is deliberately no port-less mode: that was how the default execution contract
    stayed able to duplicate a Task/Dispatch across a restart.

    ``observe_timeout_seconds`` bounds the observer role taken when another Coordinator
    holds a live lease on the same intent; it is never unbounded.

    ``heartbeat_interval_seconds`` overrides the lease-derived renewal period, and
    ``keeper_factory`` replaces the keeper outright; both exist so a test can drive lease
    renewal with a synchronisation primitive rather than wall-clock time.  Neither is needed
    in production: the period is derived from the ledger's own ``lease_seconds``.
    """
    ledger = resolve_runtime_state(adapter, runtime_state)
    factory = keeper_factory or lease_keeper_factory(
        interval_seconds=heartbeat_interval_seconds)

    def node(state: dict[str, Any]) -> dict[str, Any]:
        intent = state["pending_intent"]
        if not intent or state["intent_status"] != "PREPARED": raise StateError("OUT_OF_ORDER_EVENT:intent")
        try:
            event = _execute_recoverable(adapter, ledger, intent, factory)
        except RuntimeStateLeaseHeld:
            event = _observe_then_take_over(adapter, ledger, intent, observe_timeout_seconds,
                                            factory)
        return {**state, "pending_event": event, "intent_status": "SETTLED",
                "logical_trace": _trace(state, "EXECUTE_INTENT", event_id=event["event_id"])}
    return node


# ---- OS-31: the PAUSE and DISPOSE nodes ---------------------------------------------
# The engine owns pause/resume POLICY and stays runtime-neutral: every decision below is
# taken by ``pause_policy`` (pure) over data an adapter merely *translated*.  The adapter
# performs I/O; it never decides whether a pause may happen.


class _WallClock:
    def now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refuse_pause(state: dict[str, Any], code: str, detail: str) -> dict[str, Any]:
    """A refused pause falls back to exactly where a pre-OS-31 decision block left the run.

    Refusing to pause is always available and always safe; pausing with an unresolved
    terminal is neither.  No pause record is written and no half-paused state exists.
    """
    new = deepcopy(state)
    new["route_token"] = "BLOCK"
    new["terminal_reason"] = {"code": code, "message": detail, "phase": new["current_phase"]}
    new["logical_trace"] = _trace(new, "PAUSE", route="BLOCK", reason_code=code)
    return new


def _settlement_row(port: Any, journal: Any, intent_id: str, *, now: str) -> dict[str, Any]:
    """Account one dispatch on all four axes and finish its journal row.

    Step 0 is the handle: ``account_axes`` and ``register_terminal`` both need a plaintext
    handle, and in a fresh process it exists nowhere but the live runtime.  Any outcome
    other than ``in_process``/``listing_verified`` refuses the row here, before a single
    mutating verb is considered.
    """
    recovered = dict(port.recover_handle(intent_id))
    outcome = recovered.get("handle_recovery") or "not_attempted"
    stored = dict(journal.row(intent_id) or {}) if journal is not None else {}
    if journal is not None:
        journal.record(intent_id, stage=stored.get("stage") or "PLANNED",
                       handle_recovery=outcome)
    # The handle decides FIRST: a row whose terminal cannot be proved is refused before any
    # question about who owns it, and long before any mutating verb is considered.
    pause_policy.refuse_unrecovered_handle({**stored, "intent_id": intent_id}, outcome)
    if outcome == "not_attempted":
        # W-A: no terminal was ever requested, so there is nothing to own and nothing to
        # leak.  The row is finished with no effect rather than refused.
        row = {key: "" for key in pause_policy.SETTLEMENT_ROW_KEYS}
        row.update({key: value for key, value in stored.items()
                    if key in row and isinstance(value, str)})
        row.update({"intent_id": intent_id, "settlement": "recovered",
                    "worker_resource": "unsupervised",
                    "process_liveness": "already exited",
                    "cleanup_authority": "unknown", "recovery": "no_effect",
                    "handle_recovery": outcome, "terminal_owner": "",
                    "accounted_at": now})
        row["terminal_disposition"] = pause_policy.require_pause_disposition(row)
        if journal is not None:
            journal.record(intent_id, stage="DISPOSED", disposed_at=now,
                           recovery=row["recovery"],
                           terminal_disposition=row["terminal_disposition"])
        return pause_policy.validate_settlement_row(row)
    # (a) decided, never assumed.  ``account_dispatch`` is read-only and issues zero
    # commands, so repeating it after a crash is always safe.
    row = dict(port.account_dispatch(intent_id))
    row.setdefault("accounted_at", now)
    row["handle_recovery"] = outcome
    if journal is not None:
        journal.record(intent_id, stage="ACCOUNTED", accounted_at=row["accounted_at"],
                       dispatch_id=row.get("dispatch_id", ""),
                       task_id=row.get("task_id", ""),
                       handle_recovery=outcome or "",
                       provenance_source=row.get("provenance_source", ""))
    if row.get("settlement") == "not_settled":
        recovery = dict(port.recover_dispatch(intent_id, reason="pause"))
        row.update(recovery)
        row["settlement"] = "recovered"          # recovered, never "settled"
    if (row.get("cleanup_authority") == "authorized"
            and row.get("worker_resource") == "release"):
        released = dict(port.release_terminal(intent_id, authority="authorized"))
        row.update(released)
    row["terminal_disposition"] = pause_policy.require_pause_disposition(row)
    if row["terminal_disposition"] in ("released", "exited"):
        # Nobody owns a terminal that is proven ended: the owner column is blanked rather
        # than left naming a party who no longer holds anything.
        row["terminal_owner"] = ""
    row = pause_policy.validate_settlement_row(
        {key: row.get(key, "") for key in pause_policy.SETTLEMENT_ROW_KEYS})
    if journal is not None:
        journal.record(intent_id, stage="DISPOSED", disposed_at=now,
                       recovery=row["recovery"],
                       terminal_disposition=row["terminal_disposition"])
    return row


def pause_node(settlement_port: Any, approval_port: Any, *, clock: Any = None,
               skill_path: Any = None, journal: Any = None,
               sources_provider: Any = None):
    """Build the PAUSE node: the ONE place the engine performs lifecycle settlement.

    ``terminal_node`` still performs no external call.  Both ports are capability-gated
    before this node is reachable at all, because ``routing.pause_admissible`` already
    refused the route when either capability is missing.
    """
    ticker = clock or _WallClock()

    def node(state: dict[str, Any]) -> dict[str, Any]:
        now = ticker.now()
        try:
            intent_ids = tuple(settlement_port.open_dispatches())
        except pause_policy.PauseRefused as exc:
            return _refuse_pause(state, exc.code, exc.detail)
        except Exception as exc:  # noqa: BLE001 - unreadable is unknown, never empty
            return _refuse_pause(state, "DISPATCH_UNACCOUNTED", str(exc))
        rows: list[dict[str, Any]] = []
        for intent_id in intent_ids:
            try:
                rows.append(_settlement_row(settlement_port, journal, intent_id, now=now))
            except pause_policy.PauseRefused as exc:
                return _refuse_pause(state, exc.code, exc.detail)
            except Exception as exc:  # noqa: BLE001
                return _refuse_pause(state, "DISPATCH_UNACCOUNTED", f"{intent_id}: {exc}")
        if any(row["settlement"] == "not_settled" for row in rows):
            return _refuse_pause(state, "DISPATCH_UNACCOUNTED",
                                 "a dispatch is still running; leaving one running is a leak")
        sources = ()
        if sources_provider is not None:
            sources = tuple(sources_provider(state))
        elif hasattr(approval_port, "load_blocked_sources"):
            sources = tuple(approval_port.load_blocked_sources(state["run_id"]))
        if not sources:
            return _refuse_pause(state, "PAUSE_NOT_ADMISSIBLE",
                                 "no clarification source is available to ask")
        published = approval_port.publish(run_id=state["run_id"], sources=sources)
        if not published.request_ids:
            return _refuse_pause(state, "PAUSE_NOT_ADMISSIBLE",
                                 "the approval port published no request")
        request_id = published.request_ids[0]
        item_ids = sorted(published.item_ids)
        ledger_keys = sorted({key for source in sources
                              for key in source.source_ledger_keys})
        responsible = pause_policy.responsible_phase_for(
            [{"phase": source.phase} for source in sources],
            state["requested_phases"], state["current_phase"])
        binding = {
            "pause_record_id": pause_policy.pause_record_id(
                run_id=state["run_id"], thread_id=state["thread_id"],
                request_id=request_id, decision_item_ids=item_ids),
            "paused_at": now, "request_id": request_id,
            "decision_item_ids": item_ids, "source_ledger_keys": ledger_keys,
            "responsible_phase": responsible,
            "repository_binding": deepcopy(state["repository_binding"]),
            "artifact_binding": deepcopy(state["artifact_binding"]),
            "policy_digest": (pause_policy.policy_digest(skill_path) if skill_path
                              else "no_policy_source"),
            "settlement_ledger": rows, "disposition": None,
        }
        new = deepcopy(state)
        new["pause_binding"] = pause_policy.validate_pause_binding(binding)
        new["pending_clarification_id"] = request_id
        new["run_lifecycle"] = "WAITING_FOR_INPUT"
        new["route_token"] = "PAUSE"
        new["pending_role"] = None; new["pending_intent"] = None
        new["pending_event"] = None; new["intent_status"] = "NONE"
        new["logical_trace"] = _trace(new, "PAUSE", route="PAUSE")
        return new

    return node


def dispose_node(settlement_port: Any = None, *, clock: Any = None, journal: Any = None):
    """Build the DISPOSE node: explicit cancel/abandon of an already-paused run.

    Bindings are deliberately **frozen**, not re-validated: a moved head is not a reason to
    refuse a cancel.  That is the exact opposite of the resume rule, and the pair is
    asserted as a pair.
    """
    ticker = clock or _WallClock()

    def node(state: dict[str, Any]) -> dict[str, Any]:
        now = ticker.now()
        new = deepcopy(state)
        binding = deepcopy(new["pause_binding"] or {})
        disposition = binding.get("disposition") or {}
        rows = [dict(row) for row in binding.get("settlement_ledger") or ()]
        if disposition.get("kind") == "ABANDON" and settlement_port is not None:
            # TC-3: residual dispatches are discovered durably, so a Coordinator that never
            # ran the original dispatch still finds them.  Abandon is the last-resort
            # disposition and must be able to complete, so a row that reaches none of the
            # discharging dispositions is recorded ``residual`` -- reported, never claimed.
            accounted = {row["intent_id"] for row in rows}
            try:
                pending = [value for value in settlement_port.open_dispatches()
                           if value not in accounted]
            except Exception:  # noqa: BLE001 - report what is knowable, refuse nothing here
                pending = []
            for intent_id in pending:
                rows.append(_residual_row(settlement_port, journal, intent_id, now=now,
                                          cancellation_id=disposition.get(
                                              "cancellation_id", "")))
        binding["settlement_ledger"] = rows
        new["pause_binding"] = binding
        new["route_token"] = disposition.get("kind") or "CANCEL"
        new["logical_trace"] = _trace(new, "DISPOSE", route=new["route_token"])
        return new

    return node


def _residual_row(port: Any, journal: Any, intent_id: str, *, now: str,
                  cancellation_id: str) -> dict[str, Any]:
    """Account a residual dispatch on the abandon path, refusing nothing and claiming nothing."""
    recovered: dict[str, Any] = {"handle": None, "handle_recovery": "not_attempted"}
    try:
        recovered = dict(port.recover_handle(intent_id))
    except Exception:  # noqa: BLE001 - abandon must complete; the row records what is known
        pass
    row: dict[str, Any] = {key: "" for key in pause_policy.SETTLEMENT_ROW_KEYS}
    stored = journal.row(intent_id) if journal is not None else None
    for key in pause_policy.SETTLEMENT_ROW_KEYS:
        value = (stored or {}).get(key)
        if isinstance(value, str) and value:
            row[key] = value
    try:
        accounted = dict(port.account_dispatch(intent_id))
        for key, value in accounted.items():
            if key in row and isinstance(value, str):
                row[key] = value
    except Exception:  # noqa: BLE001
        pass
    try:
        recovery = dict(port.recover_dispatch(intent_id, reason=f"abandon:{cancellation_id}"))
        for key, value in recovery.items():
            if key in row and isinstance(value, str):
                row[key] = value
        row["settlement"] = "recovered"
    except Exception:  # noqa: BLE001
        row["settlement"] = row.get("settlement") or "not_settled"
    row["intent_id"] = intent_id
    row["accounted_at"] = row.get("accounted_at") or now
    row["handle_recovery"] = recovered.get("handle_recovery") or "not_attempted"
    row["settlement"] = row.get("settlement") or "not_settled"
    row["worker_resource"] = row.get("worker_resource") or "unsupervised"
    row["process_liveness"] = row.get("process_liveness") or "disputed"
    row["cleanup_authority"] = row.get("cleanup_authority") or "unknown"
    row["provenance_source"] = row.get("provenance_source") or "absent"
    disposition = pause_policy.terminal_disposition(row)
    row["terminal_disposition"] = disposition
    if disposition == "residual":
        # Not a transfer, and not called one.  Writing an actor id into a field would be an
        # audit action, not an adoption, so the owner stays empty and the run does not
        # claim AC-1 for this dispatch.
        row["terminal_owner"] = ""
        row["recovery"] = f"residual:{cancellation_id}"
    if journal is not None:
        journal.record(intent_id, stage="DISPOSED", disposed_at=now,
                       recovery=row["recovery"],
                       terminal_disposition=row["terminal_disposition"],
                       handle_recovery=row["handle_recovery"])
    return pause_policy.validate_settlement_row(row)


def validate_settlement_node(policy: Any = None, classifier: Any = None):
    """Build the VALIDATE_SETTLEMENT node, closing over the decision policy.

    A FACTORY, mirroring ``execute_intent_node(adapter, runtime_state)``: the policy is
    resolved once at graph construction so the node performs no I/O, and the classifier
    is injected so ``executor`` needs no module-level import of the ``tools/`` sibling
    that owns it -- a packaging mistake then fails at build time with a named refusal
    instead of making the whole engine package unimportable.

    Called with no arguments it behaves exactly as before OS-42: no policy, no gate
    classification, every existing path unchanged.
    """

    def node(state: dict[str, Any]) -> dict[str, Any]:
        intent, event = state["pending_intent"], state["pending_event"]
        if not intent or not event or event.get("intent_id") != intent["intent_id"] or event.get("command_id") != intent["command_id"]:
            raise StateError("OUT_OF_ORDER_EVENT:settlement binding")
        # Step 2 stays ABOVE the classification below: a replayed settlement must exit
        # before it can be re-classified, or a crash-restart would re-derive a defect for
        # a settlement that was already consumed and would move the repair counter twice.
        if event["event_id"] in state["processed_event_ids"]:
            return {**state, "pending_intent": None, "pending_event": None, "intent_status": "NONE"}
        try:
            validate_event(intent, event)
        except EventValidationError as exc:
            return {**state, "terminal_reason": {"code": exc.code, "message": str(exc)},
                    "logical_trace": _trace(state, "VALIDATE_SETTLEMENT", reason_code=exc.code)}
        if policy is None or classifier is None:
            return {**state, "logical_trace": _trace(state, "VALIDATE_SETTLEMENT")}
        # OS-42 F-001. The classifier is given BOTH halves of the record's identity: the
        # role this dispatch was sent out under, and the run/phase/gate-iteration the
        # contract was rendered with. `artifact_identity.contract_phase` is the SAME
        # derivation `OrcaAdapter.start` renders with, so the record an agent is told to
        # write is the record the validator accepts.
        defects = classifier(
            policy, (event.get("result") or {}).get("gate"), role=intent["role"],
            binding={"run": intent["run_id"],
                     "phase": artifact_identity.contract_phase(intent["role"],
                                                               intent["phase"]),
                     "iteration": intent["gate_iteration"]})
        if not defects:
            return {**state, "logical_trace": _trace(state, "VALIDATE_SETTLEMENT")}
        kinds = {defect.kind for defect in defects}
        # An EQUALITY, not a membership test: a mixed list containing even one SEMANTIC or
        # LIFECYCLE defect is not repairable.  This is the fail-closed default expressed
        # at the classification boundary; `routing.route` expresses it again.
        code = ("DECISION_GATE_FORM_DEFECT" if kinds == {"FORM"}
                else "DECISION_GATE_SEMANTIC_BLOCK" if "SEMANTIC" in kinds
                else "DECISION_GATE_LIFECYCLE_DEFECT")
        # terminal_reason is DELIBERATELY NOT SET, for ANY kind.  `route_node` calls
        # `route(state)` only when terminal_reason is falsy, so writing one here would
        # make the repair branch unreachable and would block every repairable defect
        # immediately.  A gate defect is not terminal until ROUTE says so, because whether
        # it ends the run depends on its kind AND on the repair budget.
        return {**state,
                "pending_gate_defect": {
                    "defects": [defect.as_dict() for defect in defects], "code": code},
                "repair_binding": {"phase": intent["phase"],
                                   "phase_iteration": intent["phase_iteration"],
                                   "role": intent["role"],
                                   "round_kind": intent["round_kind"]},
                "logical_trace": _trace(state, "VALIDATE_SETTLEMENT", reason_code=code)}

    return node


# ---- OS-42: the three audit emitters ------------------------------------------------
# Each is a PURE function of (state_before, state_after) returning the outbox entries the
# transition owes.  They perform no I/O and touch no state: the graph appends what they
# return to the checkpointed `audit_outbox`, so the INTENT to emit survives a crash and is
# retried, while the DELIVERY stays outside the lifecycle path and its failure changes
# nothing.  Purity is what makes that split possible -- and what makes each emitter
# testable without a filesystem.


def audit_gate_transition(before: dict[str, Any],
                          after: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """VALIDATE_SETTLEMENT: a FORM defect was detected, or a repair settled clean."""
    intent, event = before.get("pending_intent"), before.get("pending_event")
    if not intent or not event:
        return ()
    defect = after.get("pending_gate_defect")
    if defect is not None and before.get("pending_gate_defect") is None:
        if defect["code"] != "DECISION_GATE_FORM_DEFECT":
            # SEMANTIC and LIFECYCLE defects are not repair events; their audit is the
            # terminal the run is about to record.  Emitting a "form defect" row for them
            # would put the wrong name on a judgement.
            return ()
        key = audit.gate_defect_key(event["event_id"])
        return (audit.outbox_entry(
            audit.EVENT_GATE_FORM_DEFECT, key,
            phase=intent["phase"], role=_audit_role(intent),
            iteration=intent["gate_iteration"],
            round_kind=intent["round_kind"].lower(),
            decision_state=audit.INPUT_DEFECT_STATE,
            decision_reason_code=defect["defects"][0].get("code", ""),
            detail=audit.defect_detail(defect["defects"], key=key,
                                       repair_attempt=intent["repair_attempt"],
                                       max_attempts=MAX_REPAIR_ATTEMPTS),
        ),)
    if defect is None and intent["repair_attempt"] >= 1:
        # The repaired dispatch came back clean.  This is the ONLY place that fact is
        # knowable: after APPLY_RESULT the intent is gone and the round looks ordinary.
        key = audit.repair_succeeded_key(event["event_id"])
        return (audit.outbox_entry(
            audit.EVENT_REPAIR_SUCCEEDED, key,
            phase=intent["phase"], role=_audit_role(intent),
            iteration=intent["gate_iteration"],
            round_kind=intent["round_kind"].lower(),
            detail=audit.defect_detail((), key=key,
                                       repair_attempt=intent["repair_attempt"],
                                       max_attempts=MAX_REPAIR_ATTEMPTS),
        ),)
    return ()


def audit_repair_request(before: dict[str, Any],
                         after: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """PREPARE_INTENT: a repair dispatch was prepared for this gate round."""
    if before.get("route_token") != "PREPARE_REPAIR":
        return ()
    intent = after.get("pending_intent")
    if not intent:
        return ()
    instruction = intent.get("repair_instruction") or {}
    key = audit.repair_requested_key(intent["command_id"])
    return (audit.outbox_entry(
        audit.EVENT_REPAIR_REQUESTED, key,
        phase=intent["phase"], role=_audit_role(intent),
        iteration=intent["gate_iteration"],
        round_kind=intent["round_kind"].lower(),
        decision_state=audit.INPUT_DEFECT_STATE,
        decision_reason_code=(instruction.get("defects") or [{}])[0].get("code", ""),
        detail=audit.defect_detail(instruction.get("defects") or (), key=key,
                                   repair_attempt=intent["repair_attempt"],
                                   max_attempts=MAX_REPAIR_ATTEMPTS),
    ),)


def audit_terminal(before: dict[str, Any],
                   after: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """TERMINAL: the repair budget was spent and the round failed closed."""
    del before
    reason = after.get("terminal_reason") or {}
    if reason.get("code") != GATE_REPAIR_EXHAUSTED:
        return ()
    binding = after.get("repair_binding") or {}
    defects = reason.get("defects") or ()
    key = audit.repair_exhausted_key(
        after["run_id"], reason.get("phase", ""),
        (after.get("phase_iterations") or {}).get(reason.get("phase", ""), ""),
        reason.get("repair_attempts", ""))
    return (audit.outbox_entry(
        audit.EVENT_REPAIR_EXHAUSTED, key,
        phase=reason.get("phase", ""),
        role=str(binding.get("role", "")).lower(),
        round_kind=str(binding.get("round_kind", "")).lower(),
        decision_state=audit.INPUT_DEFECT_STATE,
        decision_reason_code=GATE_REPAIR_EXHAUSTED,
        result="BLOCKED",
        detail=audit.defect_detail(defects, key=key,
                                   repair_attempt=reason.get("repair_attempts"),
                                   max_attempts=reason.get("max_repair_attempts")),
    ),)


def _audit_role(intent: dict[str, Any]) -> str:
    return "reviewer" if intent["role"].endswith("REVIEWER") else "worker"


def consume_without_apply(state: dict[str, Any]) -> str | None:
    """The reason this settlement must be consumed but never applied, or None.

    Two sources, deliberately.  An EVENT-level rejection is a node deciding the run is
    over and carries ``terminal_reason``; a GATE defect is not terminal until ROUTE says
    so and carries ``pending_gate_defect`` instead.
    """
    code = (state.get("terminal_reason") or {}).get("code")
    if code in EVENT_REJECTION_CODES:
        return code
    defect = state.get("pending_gate_defect")
    return defect["code"] if defect is not None else None


def role_binding_is_stale(state: dict[str, Any], intent: dict[str, Any]) -> bool:
    """True when a Reviewer intent no longer describes the state it is about to be applied to."""
    if intent["role"] == "WORKER":
        return False
    return (intent["repository_binding"] != state["repository_binding"]
            or intent["artifact_binding"] != state["artifact_binding"])


def _pass_record(state: dict[str, Any], phase: str, intent: dict[str, Any],
                 event: dict[str, Any]) -> dict[str, Any]:
    """The gate-pass record, bound to the tree and artifacts the gate actually saw.

    A Worker pass records the binding its own settlement just advanced state to; a Reviewer
    pass records the binding carried on the Reviewer's intent, which the staleness guard has
    already proven identical to current state.  Either way the pass names a real tree rather
    than the run's initial default.
    """
    repository = (state["repository_binding"] if intent["role"] == "WORKER"
                  else intent["repository_binding"])
    artifact = (state["artifact_binding"] if intent["role"] == "WORKER"
                else intent["artifact_binding"])
    return {"phase": phase, "generation": state["phase_iterations"][phase],
            # OS-31 AC-6: the generation the phase-pass currency floor compares against.
            "binding_generation": state.get("binding_generation", 0),
            "tree_digest": repository.get("tree_digest"),
            "head_sha": repository.get("head_sha"),
            "artifact_digest": artifact.get("digest"),
            "reviewed_binding": binding_snapshot(repository, artifact),
            "gate_intent_id": intent["intent_id"], "gate_event_id": event["event_id"]}


def _reject_settlement(new: dict[str, Any], intent: dict[str, Any], event: dict[str, Any],
                       code: str, message: str) -> dict[str, Any]:
    """Consume a settlement without applying its result, and bind the run to BLOCK."""
    new["terminal_reason"] = {"code": code, "message": message}
    new["route_token"] = "BLOCK"
    new["processed_command_ids"].append(intent["command_id"])
    new["processed_event_ids"].append(event["event_id"])
    new["pending_intent"] = None; new["pending_event"] = None; new["intent_status"] = "NONE"
    new["logical_trace"] = _trace(new, "APPLY_RESULT", event_id=event["event_id"],
                                  reason_code=code)
    return new


def apply_result_node(state: dict[str, Any]) -> dict[str, Any]:
    new = deepcopy(state); intent, event = new["pending_intent"], new["pending_event"]
    if intent is None or event is None:
        # VALIDATE_SETTLEMENT short-circuited an already-processed settlement and cleared
        # both.  There is nothing to apply and nothing to consume -- the pass that really
        # processed it already appended both ids, and appending them again would trip
        # state.py's duplicate-identity check.  A pass-through, not a rejection: nothing
        # about a replay is a defect.  Before this guard the node reached
        # `role_binding_is_stale(new, None)` and raised
        # `TypeError: 'NoneType' object is not subscriptable`.
        new["logical_trace"] = _trace(new, "APPLY_RESULT")
        return new
    if consume_without_apply(new) is not None:
        # Consumed, never applied.  worker_result / reviewer_result / final_reviewer_result,
        # phase_passes, the bindings and every budget are untouched, which is what makes a
        # gate defect cost no phase or final-review iteration.  pending_gate_defect and
        # repair_binding are deliberately PRESERVED: ROUTE reads both, and clearing them
        # here would make the repair branch unreachable one node later.
        new["processed_command_ids"].append(intent["command_id"])
        new["processed_event_ids"].append(event["event_id"])
        new["pending_intent"] = None; new["pending_event"] = None; new["intent_status"] = "NONE"
        new["logical_trace"] = _trace(new, "APPLY_RESULT", event_id=event["event_id"])
        return new
    if role_binding_is_stale(new, intent):
        # A Reviewer may only judge the exact repository head and artifact tree its intent
        # was bound to.  If state moved underneath it, the review is stale: refuse it rather
        # than record a pass against a tree nobody reviewed.
        return _reject_settlement(new, intent, event, "STALE_REVIEW_BINDING",
                                  "reviewer intent binding does not match current state")
    reported = ((event["result"].get("binding") or {}).get("artifact") or {}).get("relative_path")
    if reported is not None and reported != intent["artifact_contract_path"]:
        # The path-level counterpart of validate_settlement_binding's root-level
        # SETTLEMENT_BINDING_SCOPE check.  Never repairable: a dispatch writing somewhere
        # other than the file it was contracted to write is not a representation defect.
        return _reject_settlement(
            new, intent, event, ARTIFACT_IDENTITY_DRIFT,
            f"settlement reports {reported!r}, intent contracted "
            f"{intent['artifact_contract_path']!r}")
    result = deepcopy(event["result"]); role = intent["role"]; phase = intent["phase"]
    # OS-42: READ the derived ordinal rather than recomputing `phase_iteration + 1` here.
    # The adapter reads the same field, so the dispatched task context and the applied
    # result can no longer disagree about which gate attempt this was.
    result.update({"intent_id": intent["intent_id"], "phase": phase,
                   "iteration": intent["gate_iteration"]})
    if role == "WORKER":
        # Advance the repository/artifact binding from the settlement *before* anything
        # downstream (the phase Reviewer dispatch above all) reads it.
        if "binding" in result:
            new["repository_binding"] = deepcopy(result["binding"]["repository"])
            new["artifact_binding"] = deepcopy(result["binding"]["artifact"])
        new["worker_result"] = result
        if new["risk"] == "low" and result.get("status") == "COMPLETE":
            new["phase_iterations"][phase] += 1
            new["remaining_phase_budget"][phase] -= 1
            new["phase_passes"][phase] = _pass_record(new, phase, intent, event)
    elif role == "PHASE_REVIEWER":
        result["reviewed_binding"] = binding_snapshot(intent["repository_binding"],
                                                      intent["artifact_binding"])
        new["reviewer_result"] = result
        new["phase_iterations"][phase] += 1; new["remaining_phase_budget"][phase] -= 1
        if result.get("result") == "PASS":
            new["phase_passes"][phase] = _pass_record(new, phase, intent, event)
    else:
        result["reviewed_binding"] = binding_snapshot(intent["repository_binding"],
                                                      intent["artifact_binding"])
        new["final_reviewer_result"] = result; new["blocking_findings"] = result.get("findings", [])
        if result.get("result") == "FAIL" and new["final_review_iterations"] < new["max_iterations"]:
            try: queue = responsible_phases(new["blocking_findings"], tuple(new["requested_phases"]))
            except ValueError:
                new["route_token"] = "ESCALATE"; new["terminal_reason"] = {"code": "OUT_OF_SCOPE_FINAL_REVIEW_FINDING", "message": "responsible phase outside request"}; queue = ()
            new["correction_queue"] = list(queue); new["correction_index"] = 0
    new["processed_command_ids"].append(intent["command_id"]); new["processed_event_ids"].append(event["event_id"])
    new["pending_intent"] = None; new["pending_event"] = None; new["intent_status"] = "NONE"
    new["logical_trace"] = _trace(new, "APPLY_RESULT", event_id=event["event_id"])
    return new


def advance_phase_node(state: dict[str, Any]) -> dict[str, Any]:
    new = deepcopy(state); kind = new["round_kind"]
    new["worker_result"] = None; new["reviewer_result"] = None; new["route_token"] = None
    if kind == "CORRECTION" and new["correction_queue"]:
        new["corrected_phases"].append(new["current_phase"]); new["correction_index"] += 1
        if new["correction_index"] < len(new["correction_queue"]):
            new["current_phase"] = new["correction_queue"][new["correction_index"]]
        else:
            downstream = downstream_revalidation_set(new["corrected_phases"], tuple(new["requested_phases"]), new["risk"])
            new["revalidation_queue"] = list(downstream); new["revalidation_index"] = 0
            if downstream: new["round_kind"] = "DOWNSTREAM_REVALIDATION"; new["current_phase"] = downstream[0]
            else: new["round_kind"] = "FINAL_REVIEW"; new["final_reviewer_result"] = None
    elif kind == "DOWNSTREAM_REVALIDATION":
        new["revalidation_index"] += 1
        if new["revalidation_index"] < len(new["revalidation_queue"]): new["current_phase"] = new["revalidation_queue"][new["revalidation_index"]]
        else: new["round_kind"] = "FINAL_REVIEW"; new["final_reviewer_result"] = None
    elif new["current_phase_index"] + 1 < len(new["requested_phases"]):
        new["current_phase_index"] += 1; new["current_phase"] = new["requested_phases"][new["current_phase_index"]]; new["round_kind"] = "PHASE_GATE"
    else: new["round_kind"] = "FINAL_REVIEW"; new["final_reviewer_result"] = None
    new["logical_trace"] = _trace(new, "ADVANCE_PHASE")
    return new


def _gate_terminal_extras(state: dict[str, Any], code: str) -> dict[str, Any]:
    """The structured payload a GATE terminal carries, and nothing else carries.

    This is where the ticket's "terminal reason records the validation error, field path,
    allowed values and retry count" is actually met: the error and field path and allowed
    set live in each defect, and the retry count is the repair counter beside them.
    """
    defect = state.get("pending_gate_defect")
    if code not in GATE_TERMINAL_CODES or defect is None:
        return {}
    extras: dict[str, Any] = {"defects": deepcopy(defect["defects"])}
    if code == GATE_REPAIR_EXHAUSTED:
        extras["repair_attempts"] = state["repair_attempts"]
        extras["max_repair_attempts"] = MAX_REPAIR_ATTEMPTS
    return extras


def terminal_node(state: dict[str, Any]) -> dict[str, Any]:
    new = deepcopy(state); token = new["route_token"]
    if token == "PAUSE":
        # OS-31.  TERMINAL is the graph *exit* node, not "the run is over".  For a pause it
        # writes NO terminal status: the run is WAITING_FOR_INPUT, which is deliberately
        # absent from TERMINAL_STATUSES, so there is nothing for a resume to clear.
        new["run_lifecycle"] = "WAITING_FOR_INPUT"
        new["terminal_status"] = None
        new["terminal_reason"] = {"code": new["decision_state"],
                                  "message": "WAITING_FOR_INPUT",
                                  "phase": new["current_phase"]}
        new["pending_role"] = None; new["pending_intent"] = None
        new["pending_event"] = None; new["intent_status"] = "NONE"
        new["logical_trace"] = _trace(new, "TERMINAL", terminal_status=None,
                                      reason_code=new["decision_state"])
        return new
    if token in ("CANCEL", "ABANDON"):
        status = "CANCELLED" if token == "CANCEL" else "ABANDONED"
        disposition = (new.get("pause_binding") or {}).get("disposition") or {}
        new["run_lifecycle"] = "SETTLED"
        new["terminal_status"] = status
        new["terminal_reason"] = {"code": f"RUN_{status}",
                                  "message": disposition.get("cancellation_id", status),
                                  "phase": new["current_phase"]}
        new["pending_role"] = None; new["pending_intent"] = None
        new["pending_event"] = None; new["intent_status"] = "NONE"
        new["logical_trace"] = _trace(new, "TERMINAL", terminal_status=status,
                                      reason_code=f"RUN_{status}")
        return new
    if token == "COMPLETE":
        status, code = "COMPLETED", "WORKFLOW_COMPLETED"
        # OS-31 SS7.3.  ``route`` already required final_review_binding_current before it
        # emitted COMPLETE; verifying again at the stamping point is a fail-closed
        # cross-check, and turns a helper that was test-only into production code.
        try:
            verify_final_review_binding(new)
        except ValueError as exc:
            token = "BLOCK"
            new["route_token"] = "BLOCK"
            new["terminal_reason"] = {"code": str(exc), "message": str(exc)}
    if token == "ESCALATE":
        status = "ESCALATED"
        correction_phase = active_correction_phase(new)
        responsible_exhausted = (new["round_kind"] == "FINAL_REVIEW"
                                 and correction_phase is not None
                                 and new["remaining_phase_budget"][correction_phase] <= 0)
        code = (new.get("terminal_reason") or {}).get("code") or (
            "MAX_ITERATIONS_REACHED" if responsible_exhausted
            else ("FINAL_REVIEW_MAX_ITERATIONS_REACHED" if new["round_kind"] == "FINAL_REVIEW"
                  else "MAX_ITERATIONS_REACHED"))
    elif token != "COMPLETE":
        status = "BLOCKED"
        stale_final = (new["round_kind"] == "FINAL_REVIEW"
                       and (new.get("final_reviewer_result") or {}).get("result") == "PASS"
                       and not final_review_binding_current(new))
        refusal = (new.get("terminal_reason") or {}).get("code")
        if refusal in pause_policy.PAUSE_REFUSAL_CODES:
            # A refused pause falls back to exactly where a pre-OS-31 decision block left
            # the run -- BLOCK/BLOCKED -- but it says WHY, so "the pause was refused" is
            # never reported as an ordinary decision block.
            code = refusal
        elif new["decision_state"] in ("NEEDS_INPUT", "CONFLICT"): code = new["decision_state"]
        elif stale_final and not (new.get("terminal_reason") or {}).get("code"):
            code = "STALE_FINAL_REVIEW_BINDING"
        elif not refusal and new.get("pending_gate_defect") is not None:
            # OS-42.  Placed AFTER the pause-refusal and decision_state clauses so the
            # OS-31 and OS-28 blocks keep exactly the precedence they have today.  A FORM
            # defect only reaches TERMINAL when the repair budget is spent, which is what
            # the exhaustion code names.
            gate_code = new["pending_gate_defect"]["code"]
            code = (GATE_REPAIR_EXHAUSTED if gate_code == "DECISION_GATE_FORM_DEFECT"
                    else gate_code)
        else: code = (new.get("terminal_reason") or {}).get("code") or ("UNIT_TEST_BLOCKED" if (new.get("worker_result") or {}).get("unit_test_status") == "BLOCKED" else "WORKER_BLOCKED")
    reason_phase = new["current_phase"]
    if (token == "ESCALATE" and new["round_kind"] == "FINAL_REVIEW"
            and code == "MAX_ITERATIONS_REACHED" and correction_phase is not None):
        reason_phase = correction_phase
    new["terminal_status"] = status; new["run_lifecycle"] = "SETTLED"
    # OS-42.  This assignment REBUILDS terminal_reason, so structured keys written by an
    # earlier node would be discarded -- which is why the gate payload is composed HERE,
    # from pending_gate_defect, rather than upstream.  `extras` is empty for every
    # terminal that is not a gate terminal, so no existing terminal reason changes shape.
    new["terminal_reason"] = {"code": code, "message": code, "phase": reason_phase,
                              **_gate_terminal_extras(new, code)}
    new["pending_role"] = None; new["pending_intent"] = None; new["pending_event"] = None; new["intent_status"] = "NONE"
    new["logical_trace"] = _trace(new, "TERMINAL", terminal_status=status, reason_code=code)
    return new
