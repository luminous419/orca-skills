"""StateGraph node callables; only execute_intent crosses a port boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import (BASE_CAPABILITIES, EVENT_REJECTION_CODES, EXTERNAL_LOOKUP,
                        EXTERNAL_RESUME, EventValidationError, ExternalLookupUnavailable,
                        binding_snapshot, make_intent, validate_event)
from .routing import (active_correction_phase, downstream_revalidation_set,
                      final_review_binding_current, missing_capabilities, responsible_phases,
                      route)
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
    if token == "PREPARE_FINAL_REVIEWER":
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
    new["pending_role"] = role
    intent = make_intent(new, role, kind)
    if intent["command_id"] in new["processed_command_ids"]:
        raise StateError("OUT_OF_ORDER_EVENT:processed command prepared")
    new["pending_intent"] = intent; new["intent_status"] = "PREPARED"; new["route_token"] = None
    new["logical_trace"] = _trace(new, "PREPARE_INTENT")
    return new


def _settle_now(adapter: Any, runtime_state: Any, intent: dict[str, Any],
                lease_token: str) -> dict[str, Any]:
    # The token travels with the effect: whatever the adapter writes about this intent is
    # fenced by the same lease this executor holds, so a predecessor that lost ownership
    # mid-``start`` cannot land its own external identity here.
    adapter.start(intent, lease_token=lease_token)
    event = adapter.settlement(intent["intent_id"])
    if event is None: raise StateError("OUT_OF_ORDER_EVENT:settlement missing")
    runtime_state.settle(intent["intent_id"], event, lease_token)
    return event


def _adapter_capabilities(adapter: Any) -> frozenset[str]:
    try:
        return frozenset(adapter.capabilities())
    except (AttributeError, TypeError):
        return frozenset()


def _collect(adapter: Any, runtime_state: Any, intent: dict[str, Any],
             receipt: dict[str, Any], lease_token: str) -> dict[str, Any]:
    """Step 3 of the recovery ladder: settle from the effect that already exists."""
    intent_id = intent["intent_id"]
    if EXTERNAL_RESUME not in _adapter_capabilities(adapter):
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_UNSUPPORTED",
            f"{intent_id}: the adapter declares no {EXTERNAL_RESUME} capability, so an "
            "effect created by an earlier process can be neither observed nor collected")
    event = adapter.resume(intent, receipt)
    if event is None:
        # The Task exists and is still running (or its outcome is unreadable).  Ownership
        # has been taken over and the effect observed; it is never re-created.
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_BLOCKED",
            f"{intent_id}: the existing external effect has not settled yet")
    runtime_state.settle(intent_id, event, lease_token)
    return event


def _recover(adapter: Any, runtime_state: Any, intent: dict[str, Any],
             record: dict[str, Any], lease_token: str) -> dict[str, Any]:
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
        runtime_state.settle(intent_id, event, lease_token)
        return event
    if record.get("status") == EFFECTED:
        return _collect(adapter, runtime_state, intent, dict(record.get("receipt") or {}),
                        lease_token)
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
    if found is None:
        return _settle_now(adapter, runtime_state, intent, lease_token)
    runtime_state.record_receipt(intent_id, dict(found), lease_token)
    return _collect(adapter, runtime_state, intent, dict(found), lease_token)


def _execute_recoverable(adapter: Any, runtime_state: Any, intent: dict[str, Any]) -> dict[str, Any]:
    """Claim the stable intent exclusively, then never create a second effect for it.

    ``claim`` is the whole ``lock -> read -> validate -> claim -> persist`` critical section,
    so two processes racing on one intent produce exactly one ``CREATED`` outcome; the loser
    either sees a live lease (and is refused as a would-be second executor) or, once that
    lease lapses, resumes into the recovery ladder above.
    """
    intent_id = intent["intent_id"]
    record = runtime_state.claim(intent)
    # ``claim`` is the only place a lease token is minted, and every ownership-sensitive
    # write below is fenced by it.  Carrying it explicitly -- instead of letting the ledger
    # treat "no token" as "no check" -- is what keeps a superseded executor out.
    lease_token = record["lease_token"]
    outcome = record.get("claim_outcome")
    if outcome == ALREADY_SETTLED:
        event = runtime_state.get_settlement(intent_id)
        if event is not None:
            return event
        raise IdempotencyRecoveryError(
            "IDEMPOTENCY_RECOVERY_BLOCKED", f"{intent_id}: settled record without settlement")
    if outcome == CREATED:
        return _settle_now(adapter, runtime_state, intent, lease_token)
    return _recover(adapter, runtime_state, intent, record, lease_token)


def _observe_then_take_over(adapter: Any, ledger: Any, intent: dict[str, Any],
                            timeout_seconds: float) -> dict[str, Any]:
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
        return _execute_recoverable(adapter, ledger, intent)
    except RuntimeStateLeaseHeld as exc:
        raise IdempotencyRecoveryError("IDEMPOTENCY_LEASE_HELD", str(exc)) from exc


def execute_intent_node(adapter: Any, runtime_state: Any = None, *,
                        observe_timeout_seconds: float = DEFAULT_OBSERVE_TIMEOUT_SECONDS):
    """Build the EXECUTE_INTENT node, refusing to run without a durable ledger.

    The port is resolved once, at construction, so a path that cannot be crash-safe fails
    before any state is processed rather than at the moment it would create the effect.
    There is deliberately no port-less mode: that was how the default execution contract
    stayed able to duplicate a Task/Dispatch across a restart.

    ``observe_timeout_seconds`` bounds the observer role taken when another Coordinator
    holds a live lease on the same intent; it is never unbounded.
    """
    ledger = resolve_runtime_state(adapter, runtime_state)

    def node(state: dict[str, Any]) -> dict[str, Any]:
        intent = state["pending_intent"]
        if not intent or state["intent_status"] != "PREPARED": raise StateError("OUT_OF_ORDER_EVENT:intent")
        try:
            event = _execute_recoverable(adapter, ledger, intent)
        except RuntimeStateLeaseHeld:
            event = _observe_then_take_over(adapter, ledger, intent, observe_timeout_seconds)
        return {**state, "pending_event": event, "intent_status": "SETTLED",
                "logical_trace": _trace(state, "EXECUTE_INTENT", event_id=event["event_id"])}
    return node


def validate_settlement_node(state: dict[str, Any]) -> dict[str, Any]:
    intent, event = state["pending_intent"], state["pending_event"]
    if not intent or not event or event.get("intent_id") != intent["intent_id"] or event.get("command_id") != intent["command_id"]:
        raise StateError("OUT_OF_ORDER_EVENT:settlement binding")
    if event["event_id"] in state["processed_event_ids"]:
        return {**state, "pending_intent": None, "pending_event": None, "intent_status": "NONE"}
    try:
        validate_event(intent, event)
    except EventValidationError as exc:
        return {**state, "terminal_reason": {"code": exc.code, "message": str(exc)},
                "logical_trace": _trace(state, "VALIDATE_SETTLEMENT", reason_code=exc.code)}
    return {**state, "logical_trace": _trace(state, "VALIDATE_SETTLEMENT")}


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
    if (new.get("terminal_reason") or {}).get("code") in EVENT_REJECTION_CODES:
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
    result = deepcopy(event["result"]); role = intent["role"]; phase = intent["phase"]
    result.update({"intent_id": intent["intent_id"], "phase": phase,
                   "iteration": intent["phase_iteration"] + (1 if role != "FINAL_REVIEWER" else 0)})
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


def terminal_node(state: dict[str, Any]) -> dict[str, Any]:
    new = deepcopy(state); token = new["route_token"]
    if token == "COMPLETE": status, code = "COMPLETED", "WORKFLOW_COMPLETED"
    elif token == "ESCALATE":
        status = "ESCALATED"
        correction_phase = active_correction_phase(new)
        responsible_exhausted = (new["round_kind"] == "FINAL_REVIEW"
                                 and correction_phase is not None
                                 and new["remaining_phase_budget"][correction_phase] <= 0)
        code = (new.get("terminal_reason") or {}).get("code") or (
            "MAX_ITERATIONS_REACHED" if responsible_exhausted
            else ("FINAL_REVIEW_MAX_ITERATIONS_REACHED" if new["round_kind"] == "FINAL_REVIEW"
                  else "MAX_ITERATIONS_REACHED"))
    else:
        status = "BLOCKED"
        stale_final = (new["round_kind"] == "FINAL_REVIEW"
                       and (new.get("final_reviewer_result") or {}).get("result") == "PASS"
                       and not final_review_binding_current(new))
        if new["decision_state"] in ("NEEDS_INPUT", "CONFLICT"): code = new["decision_state"]
        elif stale_final and not (new.get("terminal_reason") or {}).get("code"):
            code = "STALE_FINAL_REVIEW_BINDING"
        else: code = (new.get("terminal_reason") or {}).get("code") or ("UNIT_TEST_BLOCKED" if (new.get("worker_result") or {}).get("unit_test_status") == "BLOCKED" else "WORKER_BLOCKED")
    reason_phase = new["current_phase"]
    if (token == "ESCALATE" and new["round_kind"] == "FINAL_REVIEW"
            and code == "MAX_ITERATIONS_REACHED" and correction_phase is not None):
        reason_phase = correction_phase
    new["terminal_status"] = status; new["terminal_reason"] = {"code": code, "message": code, "phase": reason_phase}
    new["pending_role"] = None; new["pending_intent"] = None; new["pending_event"] = None; new["intent_status"] = "NONE"
    new["logical_trace"] = _trace(new, "TERMINAL", terminal_status=status, reason_code=code)
    return new
