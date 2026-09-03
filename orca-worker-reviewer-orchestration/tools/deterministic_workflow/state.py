"""Typed checkpoint state construction and fail-closed validation."""
from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from .contracts import (ALL_PHASES, BASE_CAPABILITIES, CAPABILITIES, DECISION_STATES, RISKS,
                        ROUND_KINDS, SCHEMA_VERSION, WORKFLOW_ID)


class StateError(ValueError):
    pass


class WorkflowState(TypedDict):
    schema_version: str; run_id: str; thread_id: str; workflow_id: str
    requested_phases: list[str]; risk: str; max_iterations: int
    adapter_capabilities: list[str]; current_phase_index: int; current_phase: str
    round_kind: str; pending_role: str | None
    phase_iterations: dict[str, int]; final_review_iterations: int
    remaining_phase_budget: dict[str, int]; remaining_final_budget: int
    correction_queue: list[str]; correction_index: int; corrected_phases: list[str]
    revalidation_queue: list[str]; revalidation_index: int
    phase_passes: dict[str, dict[str, Any] | None]
    worker_result: dict[str, Any] | None; reviewer_result: dict[str, Any] | None
    final_reviewer_result: dict[str, Any] | None; quality_verdict: str | None
    decision_state: str; decision_reason_code: str | None
    blocking_findings: list[dict[str, Any]]; pending_clarification_id: str | None
    artifact_binding: dict[str, Any]; initial_repository_binding: dict[str, Any]
    repository_binding: dict[str, Any]; route_token: str | None
    pending_intent: dict[str, Any] | None; intent_status: str
    pending_event: dict[str, Any] | None; processed_command_ids: list[str]
    processed_event_ids: list[str]; logical_trace: list[dict[str, Any]]
    terminal_status: str | None; terminal_reason: dict[str, Any] | None


FORBIDDEN_KEYS = re.compile(r"(?:process_handle|terminal_handle|session_handle|credential|access_token|client)", re.I)


def initial_state(*, run_id: str, thread_id: str, phases: tuple[str, ...],
                  capabilities: frozenset[str], risk: str = "high", max_iterations: int = 5,
                  head_sha: str = "0" * 40, tree_digest: str = "clean") -> WorkflowState:
    phase_iterations = {p: 0 for p in phases}
    repo = {"head_sha": head_sha, "tree_digest": tree_digest, "dirty": tree_digest != "clean"}
    state: WorkflowState = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "thread_id": thread_id,
        "workflow_id": WORKFLOW_ID, "requested_phases": list(phases), "risk": risk,
        "max_iterations": max_iterations, "adapter_capabilities": sorted(capabilities),
        "current_phase_index": 0, "current_phase": phases[0], "round_kind": "PHASE_GATE",
        "pending_role": "WORKER", "phase_iterations": phase_iterations,
        "final_review_iterations": 0,
        "remaining_phase_budget": {p: max_iterations for p in phases},
        "remaining_final_budget": max_iterations, "correction_queue": [], "correction_index": 0,
        "corrected_phases": [], "revalidation_queue": [], "revalidation_index": 0,
        "phase_passes": {p: None for p in phases}, "worker_result": None,
        "reviewer_result": None, "final_reviewer_result": None, "quality_verdict": None,
        "decision_state": "CLEAR", "decision_reason_code": None, "blocking_findings": [],
        "pending_clarification_id": None,
        "artifact_binding": {"artifact_root_id": run_id, "relative_path": None, "digest": None,
                             "evidence_ids": []},
        "initial_repository_binding": dict(repo), "repository_binding": dict(repo),
        "route_token": None, "pending_intent": None, "intent_status": "NONE",
        "pending_event": None, "processed_command_ids": [], "processed_event_ids": [],
        "logical_trace": [], "terminal_status": None, "terminal_reason": None,
    }
    return validate_state(state, expected_thread_id=thread_id)


def _checkpointable(value: Any, path: str = "state") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or FORBIDDEN_KEYS.search(key):
                raise StateError(f"NON_CHECKPOINTABLE_STATE:{path}.{key}")
            _checkpointable(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value): _checkpointable(child, f"{path}[{index}]")
    elif value is not None and type(value) not in (bool, int, str):
        raise StateError(f"NON_CHECKPOINTABLE_STATE:{path}")


def validate_state(raw: dict[str, Any], *, expected_thread_id: str) -> WorkflowState:
    _checkpointable(raw)
    json.dumps(raw, allow_nan=False)
    required = set(WorkflowState.__required_keys__)
    if set(raw) != required: raise StateError("MALFORMED_STATE:closed fields")
    # Container types are checked before anything indexes them, so a wrong-typed field
    # can never reach the trace/routing code as a silent KeyError or TypeError.
    for key in ("requested_phases", "adapter_capabilities", "correction_queue", "corrected_phases",
                "revalidation_queue", "blocking_findings", "processed_command_ids",
                "processed_event_ids", "logical_trace"):
        if type(raw[key]) is not list: raise StateError(f"MALFORMED_STATE:{key} type")
    for key in ("phase_iterations", "remaining_phase_budget", "phase_passes", "artifact_binding",
                "initial_repository_binding", "repository_binding"):
        if type(raw[key]) is not dict: raise StateError(f"MALFORMED_STATE:{key} type")
    for key in ("current_phase_index", "final_review_iterations", "remaining_final_budget",
                "correction_index", "revalidation_index"):
        if type(raw[key]) is not int: raise StateError(f"MALFORMED_STATE:{key} type")
    for key in ("schema_version", "run_id", "thread_id", "workflow_id", "current_phase",
                "round_kind", "risk"):
        if type(raw[key]) is not str: raise StateError(f"MALFORMED_STATE:{key} type")
    if raw["schema_version"] != SCHEMA_VERSION or raw["workflow_id"] != WORKFLOW_ID:
        raise StateError("MALFORMED_STATE:schema")
    if raw["thread_id"] != expected_thread_id: raise StateError("MALFORMED_STATE:thread")
    if not re.fullmatch(r"run_[a-z0-9]+", raw["run_id"]): raise StateError("MALFORMED_STATE:run")
    phases = raw["requested_phases"]
    if not phases or any(p not in ALL_PHASES for p in phases): raise StateError("MALFORMED_STATE:phases")
    if raw["risk"] not in RISKS or type(raw["max_iterations"]) is not int or not 1 <= raw["max_iterations"] <= 10:
        raise StateError("MALFORMED_STATE:parameters")
    if set(raw["adapter_capabilities"]) - CAPABILITIES: raise StateError("MALFORMED_STATE:capabilities")
    if raw["adapter_capabilities"] != sorted(set(raw["adapter_capabilities"])): raise StateError("MALFORMED_STATE:capabilities")
    if set(raw["phase_iterations"]) != set(phases) or set(raw["phase_passes"]) != set(phases):
        raise StateError("MALFORMED_STATE:phase maps")
    maximum = raw["max_iterations"]
    expected_remaining = {p: maximum - raw["phase_iterations"][p] for p in phases}
    if raw["remaining_phase_budget"] != expected_remaining:
        raise StateError("MALFORMED_STATE:phase budget")
    if raw["remaining_final_budget"] != maximum - raw["final_review_iterations"]:
        raise StateError("MALFORMED_STATE:final budget")
    if raw["decision_state"] not in DECISION_STATES: raise StateError("MALFORMED_STATE:decision")
    if raw["round_kind"] not in ROUND_KINDS: raise StateError("MALFORMED_STATE:round kind")
    # Phase/index coherence: every field the trace indexes must resolve.  A CORRECTION or
    # revalidation round legitimately points at a phase other than requested_phases[index],
    # so the exact-match rule applies only to the forward PHASE_GATE path.
    index = raw["current_phase_index"]
    if not 0 <= index < len(phases): raise StateError("MALFORMED_STATE:phase index")
    if raw["current_phase"] not in phases: raise StateError("MALFORMED_STATE:current phase")
    if raw["round_kind"] == "PHASE_GATE" and raw["current_phase"] != phases[index]:
        raise StateError("MALFORMED_STATE:phase index coherence")
    if len(raw["processed_command_ids"]) != len(set(raw["processed_command_ids"])) or len(raw["processed_event_ids"]) != len(set(raw["processed_event_ids"])):
        raise StateError("MALFORMED_STATE:duplicate identity")
    if raw["terminal_status"] is not None and any((raw["pending_role"], raw["pending_intent"], raw["pending_event"])):
        raise StateError("POST_TERMINAL_EVENT")
    return raw  # type: ignore[return-value]


def normalize_malformed_state(raw: Any, *, code: str, message: str) -> dict[str, Any]:
    """Project any malformed input onto a valid, closed, terminal-bound state.

    ``validate_node`` cannot hand a malformed dictionary onward: the trace and routing code
    index required fields unconditionally.  Instead of guessing, this rebuilds a minimally
    valid state, keeping only the identity fields that survive validation on their own, and
    binds it to the BLOCKED terminal path.
    """
    source = raw if isinstance(raw, dict) else {}
    run_id = source.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"run_[a-z0-9]+", run_id):
        run_id = "run_malformed"
    thread_id = source.get("thread_id") if isinstance(source.get("thread_id"), str) else ""
    phases = source.get("requested_phases")
    if not isinstance(phases, list) or not phases or any(p not in ALL_PHASES for p in phases):
        phases = [ALL_PHASES[0]]
    risk = source.get("risk") if source.get("risk") in RISKS else "high"
    maximum = source.get("max_iterations")
    if type(maximum) is not int or not 1 <= maximum <= 10:
        maximum = 5
    capabilities = source.get("adapter_capabilities")
    if not isinstance(capabilities, list) or set(capabilities) - CAPABILITIES:
        capabilities = sorted(BASE_CAPABILITIES)
    state = initial_state(run_id=run_id, thread_id=thread_id, phases=tuple(phases),
                          capabilities=frozenset(capabilities), risk=risk, max_iterations=maximum)
    state["route_token"] = "BLOCK"
    state["terminal_reason"] = {"code": code, "message": message}
    return dict(state)
