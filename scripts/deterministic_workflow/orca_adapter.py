"""Orca adapter composed only from real ``OrcaRuntimeHarness`` primitives."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from .contracts import (BASE_CAPABILITIES, EVENT_SCHEMA_VERSION, ActionIntent,
                        SettlementEvent, stable_id)


class OrcaAdapter:
    """Synchronous façade over ``create_task`` and ``run_existing_task``."""

    def __init__(self, harness: Any,
                 result_parser: Callable[[Any, ActionIntent], dict[str, Any]] | None = None):
        self.harness = harness
        self.result_parser = result_parser or self._parse_result
        self._receipts: dict[str, dict[str, Any]] = {}
        self._events: dict[str, SettlementEvent] = {}

    def capabilities(self) -> frozenset[str]:
        return BASE_CAPABILITIES | frozenset(
            {"dispatch_provenance", "dependency_edges", "runtime_ownership"}
        )

    @staticmethod
    def _parse_result(attempt: Any, intent: ActionIntent) -> dict[str, Any]:
        try:
            result = json.loads(attempt.body)
        except (AttributeError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("MALFORMED_ORCA_SETTLEMENT_BODY") from exc
        if not isinstance(result, dict):
            raise ValueError("MALFORMED_ORCA_SETTLEMENT_BODY")
        return result

    @staticmethod
    def _role(intent: ActionIntent) -> str:
        return {"WORKER": "worker", "PHASE_REVIEWER": "reviewer",
                "FINAL_REVIEWER": "final_reviewer"}[intent["role"]]

    def start(self, intent: ActionIntent) -> dict[str, Any]:
        existing = self._receipts.get(intent["intent_id"])
        if existing is not None:
            if existing["payload_digest"] != intent["payload_digest"]:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return deepcopy(existing)
        spec = json.dumps(intent, sort_keys=True, separators=(",", ":"))
        task_id = self.harness.create_task(spec)
        phase = "final_review" if intent["role"] == "FINAL_REVIEWER" else intent["phase"].lower()
        iteration = (intent["final_review_iteration"] if intent["role"] == "FINAL_REVIEWER"
                     else intent["phase_iteration"] + 1)
        mode = "complete" if intent["role"] == "WORKER" else "pass"
        attempt, terminal = self.harness.run_existing_task(
            self._role(intent), iteration, mode, task_id,
            phase=phase, spec=spec, round_kind=intent["round_kind"].lower(),
        )
        result = self.result_parser(attempt, intent)
        event_id = stable_id("event", {"intent_id": intent["intent_id"], "result": result})
        event: SettlementEvent = {
            "schema_version": EVENT_SCHEMA_VERSION, "event_id": event_id,
            "intent_id": intent["intent_id"], "command_id": intent["command_id"],
            "event_kind": "AGENT_SETTLED", "outcome": "SUCCEEDED", "result": result,
            "occurred_at": "1970-01-01T00:00:00Z", "payload_digest": stable_id("digest", result),
        }
        receipt = {"intent_id": intent["intent_id"], "payload_digest": intent["payload_digest"],
                   "task_id": task_id, "dispatch_id": attempt.dispatch_id, "terminal": terminal}
        self._receipts[intent["intent_id"]] = receipt
        self._events[intent["intent_id"]] = event
        return deepcopy(receipt)

    def settlement(self, intent_id: str) -> SettlementEvent | None:
        event = self._events.get(intent_id)
        return deepcopy(event) if event else None

    def send(self, intent_id: str, command: dict[str, Any]) -> dict[str, Any]:
        receipt = self._receipts[intent_id]
        return self.harness.call("terminal", "send", "--terminal", receipt["terminal"],
                                 "--text", json.dumps(command, sort_keys=True), "--enter")

    def status(self, intent_id: str) -> dict[str, Any]:
        receipt = self._receipts[intent_id]
        return {"task_status": self.harness.task_status(receipt["task_id"]),
                "dispatch_id": receipt["dispatch_id"]}

    def interrupt(self, intent_id: str, reason: str) -> dict[str, Any]:
        receipt = self._receipts[intent_id]
        return self.harness.call("orchestration", "worker-interrupt", "--dispatch",
                                 receipt["dispatch_id"], "--reason", reason)


ORCA_PRIMITIVE_MAP = {
    "start": ("create_task", "run_existing_task"),
    "send": ("call",),
    "status": ("task_status",),
    "interrupt": ("call",),
}
