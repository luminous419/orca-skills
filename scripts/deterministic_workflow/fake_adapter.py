"""Deterministic in-memory adapter for Orca-independent workflow execution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import BASE_CAPABILITIES, EVENT_SCHEMA_VERSION, ActionIntent, SettlementEvent, stable_id


class IdempotencyConflict(ValueError): pass


class FakeAdapter:
    def __init__(self, results: list[dict[str, Any]], *, capabilities: frozenset[str] = BASE_CAPABILITIES):
        self.results = list(results); self._capabilities = capabilities
        self.receipts: dict[str, dict[str, Any]] = {}; self.events: dict[str, SettlementEvent] = {}
        self.effect_count = 0

    def capabilities(self) -> frozenset[str]: return self._capabilities

    def start(self, intent: ActionIntent) -> dict[str, Any]:
        existing = self.receipts.get(intent["intent_id"])
        if existing:
            if existing["payload_digest"] != intent["payload_digest"]: raise IdempotencyConflict(intent["intent_id"])
            return deepcopy(existing)
        if not self.results: raise RuntimeError("fake result script exhausted")
        result = deepcopy(self.results.pop(0)); self.effect_count += 1
        event_id = stable_id("event", {"intent_id": intent["intent_id"], "result": result})
        event: SettlementEvent = {
            "schema_version": EVENT_SCHEMA_VERSION, "event_id": event_id,
            "intent_id": intent["intent_id"], "command_id": intent["command_id"],
            "event_kind": "AGENT_SETTLED", "outcome": "SUCCEEDED", "result": result,
            "occurred_at": f"2026-01-01T00:00:{self.effect_count:02d}Z",
            "payload_digest": stable_id("digest", result),
        }
        receipt = {"intent_id": intent["intent_id"], "payload_digest": intent["payload_digest"]}
        self.receipts[intent["intent_id"]] = receipt; self.events[intent["intent_id"]] = event
        return deepcopy(receipt)

    def settlement(self, intent_id: str) -> SettlementEvent | None:
        event = self.events.get(intent_id); return deepcopy(event) if event else None

    def send(self, intent_id: str, command: dict[str, Any]) -> dict[str, Any]: return {"intent_id": intent_id}
    def status(self, intent_id: str) -> dict[str, Any]: return {"settled": intent_id in self.events}
    def interrupt(self, intent_id: str, reason: str) -> dict[str, Any]: return {"intent_id": intent_id, "reason": reason}


class FakeArtifactStore:
    def __init__(self): self.items: dict[str, bytes] = {}
    def put(self, intent: ActionIntent, content: bytes) -> dict[str, Any]:
        old = self.items.get(intent["intent_id"])
        if old is not None and old != content: raise IdempotencyConflict(intent["intent_id"])
        self.items[intent["intent_id"]] = content
        return {"artifact_id": intent["intent_id"], "size": len(content)}
    def get(self, artifact_id: str) -> bytes: return self.items[artifact_id]
    def evidence(self, evidence_id: str) -> bytes: return self.items[evidence_id]
