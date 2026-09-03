"""Deterministic in-memory adapter for Orca-independent workflow execution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .contracts import BASE_CAPABILITIES, ActionIntent, SettlementEvent, make_settlement_event


class IdempotencyConflict(ValueError): pass


class FakeAdapter:
    def __init__(self, results: list[dict[str, Any]], *, capabilities: frozenset[str] = BASE_CAPABILITIES,
                 runtime_state: Any = None):
        self.results = list(results); self._capabilities = capabilities
        self.receipts: dict[str, dict[str, Any]] = {}; self.events: dict[str, SettlementEvent] = {}
        self.runtime_state = runtime_state
        self.effect_count = 0

    def capabilities(self) -> frozenset[str]: return self._capabilities

    def start(self, intent: ActionIntent) -> dict[str, Any]:
        existing = self.receipts.get(intent["intent_id"]) or self._durable_receipt(intent)
        if existing:
            if existing["payload_digest"] != intent["payload_digest"]: raise IdempotencyConflict(intent["intent_id"])
            return deepcopy(existing)
        if not self.results: raise RuntimeError("fake result script exhausted")
        result = deepcopy(self.results.pop(0)); self.effect_count += 1
        occurred_at = f"2026-01-01T00:00:{self.effect_count:02d}Z"
        event = make_settlement_event(intent, result, occurred_at=occurred_at)
        receipt = {"intent_id": intent["intent_id"], "payload_digest": intent["payload_digest"]}
        self.receipts[intent["intent_id"]] = receipt; self.events[intent["intent_id"]] = event
        if self.runtime_state is not None:
            # Record the durable identity as soon as the effect exists, then its settlement.
            self.runtime_state.record_receipt(intent["intent_id"], {"external_id": intent["intent_id"]})
            self.runtime_state.settle(intent["intent_id"], event)
        return deepcopy(receipt)

    def _durable_receipt(self, intent: ActionIntent) -> dict[str, Any] | None:
        if self.runtime_state is None: return None
        record = self.runtime_state.get_receipt(intent["intent_id"])
        if not record or record.get("status") == "CLAIMED": return None
        return {"intent_id": record["intent_id"], "payload_digest": record["payload_digest"]}

    def settlement(self, intent_id: str) -> SettlementEvent | None:
        event = self.events.get(intent_id)
        if event is None and self.runtime_state is not None:
            event = self.runtime_state.get_settlement(intent_id)
        return deepcopy(event) if event else None

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
