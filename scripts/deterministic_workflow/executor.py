"""StateGraph node callables; only execute_intent crosses a port boundary."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import (BASE_CAPABILITIES, EVENT_REJECTION_CODES, EventValidationError,
                        make_intent, validate_event)
from .routing import active_correction_phase, downstream_revalidation_set, missing_capabilities, responsible_phases, route
from .runtime_state import resolve_runtime_state
from .state import StateError, normalize_malformed_state, validate_state


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


def _settle_now(adapter: Any, runtime_state: Any, intent: dict[str, Any]) -> dict[str, Any]:
    adapter.start(intent)
    event = adapter.settlement(intent["intent_id"])
    if event is None: raise StateError("OUT_OF_ORDER_EVENT:settlement missing")
    runtime_state.settle(intent["intent_id"], event)
    return event


def _execute_recoverable(adapter: Any, runtime_state: Any, intent: dict[str, Any]) -> dict[str, Any]:
    """Claim the stable intent durably, then never create a second external effect for it."""
    intent_id = intent["intent_id"]
    record = runtime_state.get_receipt(intent_id)
    if record is None:
        # Intent-before-effect: the claim is durable before anything external can happen.
        runtime_state.claim(intent)
        return _settle_now(adapter, runtime_state, intent)
    if record.get("payload_digest") != intent["payload_digest"]:
        raise StateError(f"IDEMPOTENCY_CONFLICT:{intent_id}")
    if record.get("status") == "SETTLED":
        event = runtime_state.get_settlement(intent_id)
        if event is not None: return event
    # A claim survives without a settlement only when an earlier process died somewhere
    # around the external effect.  Recover it by stable identity; never re-run it blindly.
    event = adapter.settlement(intent_id)
    if event is None:
        raise StateError(f"IDEMPOTENCY_RECOVERY_REQUIRED:{record.get('status')}:{intent_id}")
    runtime_state.settle(intent_id, event)
    return event


def execute_intent_node(adapter: Any, runtime_state: Any = None):
    """Build the EXECUTE_INTENT node, refusing to run without a durable ledger.

    The port is resolved once, at construction, so a path that cannot be crash-safe fails
    before any state is processed rather than at the moment it would create the effect.
    There is deliberately no port-less mode: that was how the default execution contract
    stayed able to duplicate a Task/Dispatch across a restart.
    """
    ledger = resolve_runtime_state(adapter, runtime_state)

    def node(state: dict[str, Any]) -> dict[str, Any]:
        intent = state["pending_intent"]
        if not intent or state["intent_status"] != "PREPARED": raise StateError("OUT_OF_ORDER_EVENT:intent")
        event = _execute_recoverable(adapter, ledger, intent)
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


def apply_result_node(state: dict[str, Any]) -> dict[str, Any]:
    new = deepcopy(state); intent, event = new["pending_intent"], new["pending_event"]
    if (new.get("terminal_reason") or {}).get("code") in EVENT_REJECTION_CODES:
        new["processed_command_ids"].append(intent["command_id"])
        new["processed_event_ids"].append(event["event_id"])
        new["pending_intent"] = None; new["pending_event"] = None; new["intent_status"] = "NONE"
        new["logical_trace"] = _trace(new, "APPLY_RESULT", event_id=event["event_id"])
        return new
    result = deepcopy(event["result"]); role = intent["role"]; phase = intent["phase"]
    result.update({"intent_id": intent["intent_id"], "phase": phase,
                   "iteration": intent["phase_iteration"] + (1 if role != "FINAL_REVIEWER" else 0)})
    if role == "WORKER":
        new["worker_result"] = result
        if new["risk"] == "low" and result.get("status") == "COMPLETE":
            new["phase_iterations"][phase] += 1
            new["remaining_phase_budget"][phase] -= 1
            new["phase_passes"][phase] = {"phase": phase, "generation": new["phase_iterations"][phase], "tree_digest": new["repository_binding"]["tree_digest"], "gate_intent_id": intent["intent_id"], "gate_event_id": event["event_id"]}
    elif role == "PHASE_REVIEWER":
        new["reviewer_result"] = result
        new["phase_iterations"][phase] += 1; new["remaining_phase_budget"][phase] -= 1
        if result.get("result") == "PASS":
            new["phase_passes"][phase] = {"phase": phase, "generation": new["phase_iterations"][phase], "tree_digest": new["repository_binding"]["tree_digest"], "gate_intent_id": intent["intent_id"], "gate_event_id": event["event_id"]}
    else:
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
        if new["decision_state"] in ("NEEDS_INPUT", "CONFLICT"): code = new["decision_state"]
        else: code = (new.get("terminal_reason") or {}).get("code") or ("UNIT_TEST_BLOCKED" if (new.get("worker_result") or {}).get("unit_test_status") == "BLOCKED" else "WORKER_BLOCKED")
    reason_phase = new["current_phase"]
    if (token == "ESCALATE" and new["round_kind"] == "FINAL_REVIEW"
            and code == "MAX_ITERATIONS_REACHED" and correction_phase is not None):
        reason_phase = correction_phase
    new["terminal_status"] = status; new["terminal_reason"] = {"code": code, "message": code, "phase": reason_phase}
    new["pending_role"] = None; new["pending_intent"] = None; new["pending_event"] = None; new["intent_status"] = "NONE"
    new["logical_trace"] = _trace(new, "TERMINAL", terminal_status=status, reason_code=code)
    return new
