"""Deterministic in-memory adapter for Orca-independent workflow execution."""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .contracts import (BASE_CAPABILITIES, EXTERNAL_LOOKUP, EXTERNAL_RESUME, ActionIntent,
                        RECOVERY_CAPABILITIES, SettlementEvent, make_settlement_event)


class FileExternalWorld:
    """A durable stand-in for the external Task registry, outside every adapter instance.

    This is the *reference implementation* of the two optional recovery capabilities: it is
    what an external runtime would have to offer for a crashed run to be resumed rather than
    duplicated -- an effect discoverable by stable intent identity, and an outcome readable
    by a process that did not create it.  Because it lives in a file, a brand new adapter in
    a brand new process can look up and finish work an earlier process started, which is the
    property ``test_deterministic_workflow_ownership`` proves end to end.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=f".{self.path.name}.", delete=False)
        with handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, self.path)

    def create(self, intent: ActionIntent) -> dict[str, Any]:
        """Register the external effect for a stable intent, idempotently."""
        world = self._read()
        entry = world.get(intent["intent_id"])
        if entry is None:
            entry = {"external_id": f"ext_{len(world) + 1}_{intent['intent_id'][-8:]}",
                     "intent_id": intent["intent_id"], "outcome": None, "occurred_at": None}
            world[intent["intent_id"]] = entry
            self._write(world)
        return deepcopy(entry)

    def find(self, intent_id: str) -> dict[str, Any] | None:
        entry = self._read().get(intent_id)
        return {"external_id": entry["external_id"], "intent_id": intent_id} if entry else None

    def complete(self, intent_id: str, result: dict[str, Any], occurred_at: str) -> None:
        world = self._read()
        entry = world.get(intent_id)
        if entry is None:
            raise KeyError(intent_id)
        entry["outcome"] = deepcopy(result)
        entry["occurred_at"] = occurred_at
        self._write(world)

    def outcome(self, intent_id: str) -> dict[str, Any] | None:
        entry = self._read().get(intent_id)
        if entry is None or entry.get("outcome") is None:
            return None
        return deepcopy(entry)


class IdempotencyConflict(ValueError): pass


class FakeAdapter:
    def __init__(self, results: list[dict[str, Any]], *, capabilities: frozenset[str] = BASE_CAPABILITIES,
                 runtime_state: Any = None, external_world: Any = None):
        self.results = list(results); self._capabilities = capabilities
        self.receipts: dict[str, dict[str, Any]] = {}; self.events: dict[str, SettlementEvent] = {}
        self.runtime_state = runtime_state
        self.external_world = external_world
        self.effect_count = 0

    def capabilities(self) -> frozenset[str]:
        # The recovery capabilities are declared only when a durable external world actually
        # backs them.  Declaring a capability the adapter cannot honour is what would let the
        # recovery ladder believe an effect was proven absent when it was merely unreadable.
        if self.external_world is None:
            return self._capabilities
        return self._capabilities | RECOVERY_CAPABILITIES

    def start(self, intent: ActionIntent, *, lease_token: str | None = None) -> dict[str, Any]:
        """Create the effect once, recording it under the caller's lease.

        ``lease_token`` is the fence the executor obtained from ``claim``.  It is threaded
        through rather than defaulted away: with a ledger wired in, a caller that cannot
        name a live lease is refused by the store instead of writing an effect the current
        owner does not know about.
        """
        existing = self.receipts.get(intent["intent_id"]) or self._durable_receipt(intent)
        if existing:
            if existing["payload_digest"] != intent["payload_digest"]: raise IdempotencyConflict(intent["intent_id"])
            return deepcopy(existing)
        if not self.results: raise RuntimeError("fake result script exhausted")
        result = deepcopy(self.results.pop(0)); self.effect_count += 1
        occurred_at = f"2026-01-01T00:00:{self.effect_count:02d}Z"
        if self.external_world is not None:
            # The effect becomes discoverable before it produces an outcome, so a crash in
            # between leaves something a successor can find instead of re-creating.
            self.external_world.create(intent)
            self.external_world.complete(intent["intent_id"], result, occurred_at)
        event = make_settlement_event(intent, result, occurred_at=occurred_at)
        receipt = {"intent_id": intent["intent_id"], "payload_digest": intent["payload_digest"]}
        self.receipts[intent["intent_id"]] = receipt; self.events[intent["intent_id"]] = event
        if self.runtime_state is not None:
            # Record the durable identity as soon as the effect exists, then its settlement.
            self.runtime_state.record_receipt(intent["intent_id"],
                                              {"external_id": intent["intent_id"]}, lease_token)
            self.runtime_state.settle(intent["intent_id"], event, lease_token)
        return deepcopy(receipt)

    def _durable_receipt(self, intent: ActionIntent) -> dict[str, Any] | None:
        if self.runtime_state is None: return None
        record = self.runtime_state.get_receipt(intent["intent_id"])
        if not record or record.get("status") == "CLAIMED": return None
        return {"intent_id": record["intent_id"], "payload_digest": record["payload_digest"]}

    def lookup(self, intent: ActionIntent) -> dict[str, Any] | None:
        """Find an effect created for this stable intent, or prove that none was."""
        if self.external_world is None:
            raise NotImplementedError(f"{EXTERNAL_LOOKUP} is not declared by this adapter")
        return self.external_world.find(intent["intent_id"])

    def resume(self, intent: ActionIntent, receipt: dict[str, Any]) -> SettlementEvent | None:
        """Collect the outcome of an effect this process did not create, or None if pending."""
        if self.external_world is None:
            raise NotImplementedError(f"{EXTERNAL_RESUME} is not declared by this adapter")
        entry = self.external_world.outcome(intent["intent_id"])
        if entry is None:
            return None
        event = make_settlement_event(intent, entry["outcome"], occurred_at=entry["occurred_at"])
        self.events[intent["intent_id"]] = event
        return deepcopy(event)

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
