"""Runtime-neutral closed contracts for the OS-40 workflow graph."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
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


# A settlement rejected for any of these reasons must never have its result applied.
EVENT_REJECTION_CODES = frozenset({"MALFORMED_EVENT", "UNKNOWN_EVENT", "SETTLEMENT_INTEGRITY"})


def validate_event(intent: ActionIntent, event: dict[str, Any]) -> SettlementEvent:
    """Validate the closed settlement vocabulary and identity before its result is applied."""
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
    # Identity is closed: a checkpointed settlement whose result, intent/command binding,
    # digest, timestamp or event ID was altered no longer matches its canonical payload.
    if event["intent_id"] != intent["intent_id"] or event["command_id"] != intent["command_id"]:
        raise EventValidationError("SETTLEMENT_INTEGRITY", "settlement is bound to another intent/command")
    if not _well_formed_timestamp(event["occurred_at"]):
        raise EventValidationError("MALFORMED_EVENT", "settlement timestamp is malformed")
    digest = settlement_digest(intent, result)
    if not _constant_time_equal(event["payload_digest"], digest):
        raise EventValidationError("SETTLEMENT_INTEGRITY", "settlement payload digest mismatch")
    if not _constant_time_equal(event["event_id"], _event_id(digest)):
        raise EventValidationError("SETTLEMENT_INTEGRITY", "settlement event identity mismatch")
    return event  # type: ignore[return-value]


def _constant_time_equal(left: Any, right: str) -> bool:
    return isinstance(left, str) and hmac.compare_digest(left, right)


_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _well_formed_timestamp(value: Any) -> bool:
    return isinstance(value, str) and bool(_TIMESTAMP.fullmatch(value))


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


def settlement_payload(intent: ActionIntent, result: dict[str, Any]) -> dict[str, Any]:
    """The canonical settlement payload: the closed input to digest and event identity.

    Every field an applied settlement can influence is bound in here, so a mutation of
    the result, of the role, or of the intent/command binding changes the digest.
    """
    return {
        "schema_version": EVENT_SCHEMA_VERSION, "event_kind": "AGENT_SETTLED",
        "outcome": "SUCCEEDED", "intent_id": intent["intent_id"],
        "command_id": intent["command_id"], "role": intent["role"],
        "intent_payload_digest": intent["payload_digest"], "result": result,
    }


def settlement_digest(intent: ActionIntent, result: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(settlement_payload(intent, result))).hexdigest()


def _event_id(payload_digest: str) -> str:
    return stable_id("event", {"payload_digest": payload_digest})


def settlement_event_id(intent: ActionIntent, result: dict[str, Any]) -> str:
    """Event identity is a pure function of the canonical payload.

    ``occurred_at`` is deliberately excluded: identity must be reproducible when a restarted
    process re-derives the same settlement, and it must be identical across adapters whose
    clocks differ.  The timestamp is still validated for shape and, being read by no gate,
    can influence no applied decision.
    """
    return _event_id(settlement_digest(intent, result))


def make_settlement_event(intent: ActionIntent, result: dict[str, Any], *,
                          occurred_at: str) -> SettlementEvent:
    """Build the only settlement shape ``validate_event`` accepts for this intent."""
    digest = settlement_digest(intent, result)
    return {
        "schema_version": EVENT_SCHEMA_VERSION, "event_id": _event_id(digest),
        "intent_id": intent["intent_id"], "command_id": intent["command_id"],
        "event_kind": "AGENT_SETTLED", "outcome": "SUCCEEDED", "result": result,
        "occurred_at": occurred_at, "payload_digest": digest,
    }
