"""Orca adapter composed only from real ``OrcaRuntimeHarness`` primitives."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from .contracts import (BASE_CAPABILITIES, ActionIntent, SettlementEvent,
                        make_settlement_event)


class OrcaAdapter:
    """Synchronous façade over ``create_task`` and ``run_existing_task``."""

    def __init__(self, harness: Any,
                 result_parser: Callable[[Any, ActionIntent], dict[str, Any]] | None = None,
                 runtime_state: Any = None):
        self.harness = harness
        self.result_parser = result_parser or self._parse_result
        self.runtime_state = runtime_state
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
        existing = self._receipts.get(intent["intent_id"]) or self._durable_receipt(intent)
        if existing is not None:
            if existing["payload_digest"] != intent["payload_digest"]:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return deepcopy(existing)
        spec = json.dumps(intent, sort_keys=True, separators=(",", ":"))
        task_id = self.harness.create_task(spec)
        # The external Task now exists.  Record its durable identity immediately so a crash
        # before the dispatch settles cannot look like "never started" on the next process.
        self._record_receipt(intent, {"task_id": task_id})
        phase = "final_review" if intent["role"] == "FINAL_REVIEWER" else intent["phase"].lower()
        iteration = (intent["final_review_iteration"] if intent["role"] == "FINAL_REVIEWER"
                     else intent["phase_iteration"] + 1)
        mode = "complete" if intent["role"] == "WORKER" else "pass"
        attempt, terminal = self.harness.run_existing_task(
            self._role(intent), iteration, mode, task_id,
            phase=phase, spec=spec, round_kind=intent["round_kind"].lower(),
        )
        result = self.result_parser(attempt, intent)
        event = make_settlement_event(intent, result, occurred_at="1970-01-01T00:00:00Z")
        receipt = {"intent_id": intent["intent_id"], "payload_digest": intent["payload_digest"],
                   "task_id": task_id, "dispatch_id": attempt.dispatch_id, "terminal": terminal}
        self._receipts[intent["intent_id"]] = receipt
        self._events[intent["intent_id"]] = event
        # ``terminal`` is a runtime handle and is deliberately never persisted.
        self._record_receipt(intent, {"task_id": task_id, "dispatch_id": attempt.dispatch_id})
        if self.runtime_state is not None:
            self.runtime_state.settle(intent["intent_id"], event)
        return deepcopy(receipt)

    def _record_receipt(self, intent: ActionIntent, receipt: dict[str, Any]) -> None:
        if self.runtime_state is not None:
            self.runtime_state.record_receipt(intent["intent_id"], receipt)

    def _durable_receipt(self, intent: ActionIntent) -> dict[str, Any] | None:
        """Recover an external effect created by an earlier process, by stable identity."""
        if self.runtime_state is None: return None
        record = self.runtime_state.get_receipt(intent["intent_id"])
        if not record or record.get("status") == "CLAIMED": return None
        stored = record.get("receipt") or {}
        return {"intent_id": record["intent_id"], "payload_digest": record["payload_digest"],
                "task_id": stored.get("task_id"), "dispatch_id": stored.get("dispatch_id"),
                "terminal": None}

    def settlement(self, intent_id: str) -> SettlementEvent | None:
        event = self._events.get(intent_id)
        if event is None and self.runtime_state is not None:
            event = self.runtime_state.get_settlement(intent_id)
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
