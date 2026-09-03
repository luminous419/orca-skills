"""Durable ``RuntimeStatePort`` implementations for crash-safe idempotency.

The store records a *stable intent claim before the external effect is attempted*, so a
process that dies anywhere after the claim can tell, on restart with a brand new adapter,
that the effect may already exist. Records hold only durable external identifiers; runtime
handles never reach the file, mirroring ``state.FORBIDDEN_KEYS``.
"""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .contracts import ActionIntent, SettlementEvent
from .state import FORBIDDEN_KEYS

RUNTIME_STATE_SCHEMA_VERSION = "os40.runtime_state.v1"
CLAIMED, EFFECTED, SETTLED = "CLAIMED", "EFFECTED", "SETTLED"
STATUSES = (CLAIMED, EFFECTED, SETTLED)


class RuntimeStateConflict(ValueError):
    """The same stable intent identity was claimed with a different payload."""


class IdempotencyPortRequired(ValueError):
    """No durable ``RuntimeStatePort`` is available for a path that creates effects.

    Durable idempotency is not optional: without a ledger that outlives the process, a
    restart cannot tell "never started" from "already created", so the same stable intent
    would create a second external Task/Dispatch.
    """


def resolve_runtime_state(adapter: Any, runtime_state: Any = None) -> Any:
    """Return the one durable port to use, or refuse.

    Resolution order: the explicit argument, else the port the caller wired into the
    adapter.  When both are present they must be the same object -- two ledgers for one
    execution would split the receipts and defeat the guarantee.
    """
    bound = getattr(adapter, "runtime_state", None)
    if runtime_state is not None and bound is not None and runtime_state is not bound:
        raise RuntimeStateConflict(
            "AMBIGUOUS_RUNTIME_STATE: the adapter is bound to a different RuntimeStatePort "
            "than the one supplied; pass one port, or bind the same object to both")
    resolved = runtime_state if runtime_state is not None else bound
    if resolved is None:
        raise IdempotencyPortRequired(
            "IDEMPOTENCY_PORT_REQUIRED: pass runtime_state=..., or bind one to the adapter. "
            "A durable RuntimeStatePort is mandatory on every path that creates external "
            "effects; see deterministic_workflow.runtime_state.FileRuntimeStateStore.")
    return resolved


def _assert_checkpointable(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or FORBIDDEN_KEYS.search(key):
                raise RuntimeStateConflict(f"NON_CHECKPOINTABLE_RUNTIME_STATE:{path}.{key}")
            _assert_checkpointable(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_checkpointable(child, f"{path}[{index}]")
    elif value is not None and type(value) not in (bool, int, float, str):
        raise RuntimeStateConflict(f"NON_CHECKPOINTABLE_RUNTIME_STATE:{path}")


class _RuntimeStateStore:
    """Shared claim/receipt/settlement bookkeeping over a pluggable record map."""

    def _read(self) -> dict[str, Any]:
        raise NotImplementedError

    def _write(self, records: dict[str, Any]) -> None:
        raise NotImplementedError

    def get_receipt(self, intent_id: str) -> dict[str, Any] | None:
        record = self._read().get(intent_id)
        return deepcopy(record) if record is not None else None

    def get_settlement(self, intent_id: str) -> SettlementEvent | None:
        record = self._read().get(intent_id) or {}
        event = record.get("settlement")
        return deepcopy(event) if event else None

    def claim(self, intent: ActionIntent) -> dict[str, Any]:
        records = self._read()
        existing = records.get(intent["intent_id"])
        if existing is not None:
            if existing.get("payload_digest") != intent["payload_digest"]:
                raise RuntimeStateConflict(f"IDEMPOTENCY_CONFLICT:{intent['intent_id']}")
            return deepcopy(existing)
        record = {
            "intent_id": intent["intent_id"], "command_id": intent["command_id"],
            "payload_digest": intent["payload_digest"], "run_id": intent["run_id"],
            "phase": intent["phase"], "role": intent["role"],
            "round_kind": intent["round_kind"], "status": CLAIMED,
            "receipt": None, "settlement": None,
        }
        records[intent["intent_id"]] = record
        self._write(records)
        return deepcopy(record)

    def record_receipt(self, intent_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
        _assert_checkpointable(receipt, "receipt")
        records = self._read()
        record = records.get(intent_id)
        if record is None:
            raise RuntimeStateConflict(f"UNCLAIMED_INTENT:{intent_id}")
        if record["status"] == CLAIMED:
            record["status"] = EFFECTED
        record["receipt"] = deepcopy(receipt)
        self._write(records)
        return deepcopy(record)

    def settle(self, intent_id: str, event: SettlementEvent) -> dict[str, Any]:
        _assert_checkpointable(dict(event), "settlement")
        records = self._read()
        record = records.get(intent_id)
        if record is None:
            raise RuntimeStateConflict(f"UNCLAIMED_INTENT:{intent_id}")
        stored = record.get("settlement")
        if stored is not None and stored != event:
            raise RuntimeStateConflict(f"SETTLEMENT_CONFLICT:{intent_id}")
        record["settlement"] = deepcopy(dict(event))
        record["status"] = SETTLED
        self._write(records)
        return deepcopy(record)


class InMemoryRuntimeStateStore(_RuntimeStateStore):
    """Process-local store; only crash-safe for tests that share the instance."""

    def __init__(self) -> None:
        self._records: dict[str, Any] = {}

    def _read(self) -> dict[str, Any]:
        return self._records

    def _write(self, records: dict[str, Any]) -> None:
        self._records = records


class FileRuntimeStateStore(_RuntimeStateStore):
    """JSON-file store that survives the process, written atomically."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeStateConflict(f"UNREADABLE_RUNTIME_STATE:{exc}") from exc
        records = raw.get("records")
        return records if isinstance(records, dict) else {}

    def _write(self, records: dict[str, Any]) -> None:
        payload = {"schema_version": RUNTIME_STATE_SCHEMA_VERSION, "records": records}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=f".{self.path.name}.", delete=False)
        try:
            with handle:
                json.dump(payload, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
