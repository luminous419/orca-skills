"""Runtime-neutral closed contracts for the OS-40 workflow graph."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, TypedDict

SCHEMA_VERSION = "os40.workflow.v1"
WORKFLOW_ID = "os40.standard.v1"
ACTION_SCHEMA_VERSION = "os40.action.v1"
EVENT_SCHEMA_VERSION = "os40.event.v1"

PHASES = ("ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST")
SPECIALIZED_PHASES = ("BUGFIX", "REFACTORING")
ALL_PHASES = PHASES + SPECIALIZED_PHASES
RISKS = ("low", "medium", "high")
ROLES = ("WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER")
ROUND_KINDS = ("PHASE_GATE", "CORRECTION", "DOWNSTREAM_REVALIDATION", "FINAL_REVIEW")
ROUTE_TOKENS = (
    "BLOCK", "ESCALATE", "PREPARE_WORKER", "PREPARE_PHASE_REVIEWER",
    "ADVANCE_PHASE", "PREPARE_FINAL_REVIEWER", "PREPARE_CORRECTION",
    "PREPARE_REVALIDATION", "COMPLETE",
)
TERMINAL_STATUSES = ("COMPLETED", "BLOCKED", "ESCALATED")
DECISION_STATES = ("CLEAR", "ASSUMPTION_ALLOWED", "NEEDS_INPUT", "CONFLICT")
BASE_CAPABILITIES = frozenset({
    "agent_start", "agent_command", "agent_status", "agent_interrupt",
    "settlement", "idempotent_intent", "artifact_immutable", "checkpoint",
})
CAPABILITIES = BASE_CAPABILITIES | frozenset({
    "human_approval", "dispatch_provenance", "dependency_edges", "runtime_ownership",
})

Phase = Literal["ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST", "BUGFIX", "REFACTORING"]
Role = Literal["WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"]
RouteToken = Literal["BLOCK", "ESCALATE", "PREPARE_WORKER", "PREPARE_PHASE_REVIEWER", "ADVANCE_PHASE", "PREPARE_FINAL_REVIEWER", "PREPARE_CORRECTION", "PREPARE_REVALIDATION", "COMPLETE"]


class Finding(TypedDict):
    finding_id: str
    blocking: bool
    responsible_phase: Phase
    quality_attribute: str
    severity: str


class ActionIntent(TypedDict):
    schema_version: str
    intent_id: str
    command_id: str
    action_kind: str
    run_id: str
    phase: Phase
    phase_iteration: int
    final_review_iteration: int
    role: Role
    round_kind: str
    artifact_binding: dict[str, Any]
    repository_binding: dict[str, Any]
    payload_digest: str


class SettlementEvent(TypedDict):
    schema_version: str
    event_id: str
    intent_id: str
    command_id: str
    event_kind: str
    outcome: str
    result: dict[str, Any]
    occurred_at: str
    payload_digest: str


class EventValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_event(intent: ActionIntent, event: dict[str, Any]) -> SettlementEvent:
    """Validate the closed settlement vocabulary before its result is applied."""
    if set(event) != set(SettlementEvent.__required_keys__) or not isinstance(event.get("result"), dict):
        raise EventValidationError("MALFORMED_EVENT", "closed settlement fields/result required")
    if (event.get("schema_version") != EVENT_SCHEMA_VERSION
            or event.get("event_kind") != "AGENT_SETTLED"
            or event.get("outcome") != "SUCCEEDED"):
        raise EventValidationError("UNKNOWN_EVENT", "unsupported settlement vocabulary")
    result = event["result"]
    if intent["role"] == "WORKER":
        if result.get("status") not in {"COMPLETE", "BLOCKED"}:
            raise EventValidationError("UNKNOWN_EVENT", "unknown worker status")
    elif result.get("result") not in {"PASS", "FAIL"}:
        raise EventValidationError("UNKNOWN_EVENT", "unknown reviewer result")
    return event  # type: ignore[return-value]


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def stable_id(namespace: str, value: Any) -> str:
    return f"{namespace}_{hashlib.sha256(canonical_bytes(value)).hexdigest()[:24]}"


def make_intent(state: dict[str, Any], role: Role, round_kind: str) -> ActionIntent:
    identity = {
        "workflow_id": state["workflow_id"], "run_id": state["run_id"],
        "phase": state["current_phase"],
        "phase_iteration": state["phase_iterations"][state["current_phase"]],
        "final_review_iteration": state["final_review_iterations"],
        "role": role, "round_kind": round_kind, "action_kind": "RUN_AGENT",
    }
    command_id = stable_id("cmd", identity)
    payload = {
        "command_id": command_id, "artifact_binding": state["artifact_binding"],
        "repository_binding": state["repository_binding"],
    }
    payload_digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return {
        "schema_version": ACTION_SCHEMA_VERSION,
        "intent_id": stable_id("intent", {**payload, "payload_digest": payload_digest}),
        "command_id": command_id, "action_kind": "RUN_AGENT", "run_id": state["run_id"],
        "phase": state["current_phase"], "phase_iteration": identity["phase_iteration"],
        "final_review_iteration": identity["final_review_iteration"], "role": role,
        "round_kind": round_kind, "artifact_binding": state["artifact_binding"],
        "repository_binding": state["repository_binding"], "payload_digest": payload_digest,
    }
