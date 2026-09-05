"""Tier-1 durable ``BaseCheckpointSaver`` -- the authority for a paused run's state.

OS-31 makes the OS-40 checkpoint the authoritative state of a durable pause (PLAN F-001),
and a production durable checkpointer is therefore required rather than deferred (OD-4).
``langgraph-checkpoint==2.1.1`` ships only ``base``, ``memory`` and ``serde``, so this is an
in-repository saver over the already-pinned serializer: **no new pinned dependency**.

The store is one JSON document per file, with closed key sets validated on every read.
Serialized payloads are base64-encoded so the file stays valid, diffable JSON.  ``head`` is
an explicit pointer written inside the same critical section as the ``put`` -- not
``max(checkpoint_ids)`` -- which is what makes C2 answerable without trusting id ordering.

Retirement, never deletion: a disposed run's checkpoint is the audit evidence for what was
disposed.  ``delete_thread`` exists because the base class declares it and is called by no
OS-31 path.
"""
from __future__ import annotations

import base64
import os
from collections.abc import Iterator, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver, ChannelVersions, CheckpointTuple
from langgraph.checkpoint.base import get_checkpoint_id, get_checkpoint_metadata
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from . import contracts
from .durable_store import (DEFAULT_LOCK_TIMEOUT_SECONDS, FileCriticalSection,
                            LockTimeout, LockUnavailable, read_json_document,
                            write_json_document)

CHECKPOINT_STORE_SCHEMA_VERSION = "os31.checkpoint_store.v1"


class CheckpointStoreError(ValueError):
    """The Tier-1 store refused an operation under its own closed contract."""


class CheckpointStoreCorrupt(CheckpointStoreError):
    """The store is unreadable, version-incompatible or internally inconsistent.

    Never silently treated as an empty store -- that is how a durable checkpoint would
    disappear and a pause record would end up naming nothing.
    """


class CheckpointStoreLockUnavailable(CheckpointStoreError):
    """Non-POSIX host: an exclusive inter-process critical section is impossible."""


class CheckpointStoreLockTimeout(CheckpointStoreError):
    """The store lock could not be acquired inside the explicit timeout."""


class CheckpointThreadRetired(CheckpointStoreError):
    """A disposed run's thread may not be written to again."""


def _b64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _unb64(payload: str) -> bytes:
    return base64.b64decode(payload.encode("ascii"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FileCheckpointSaver(BaseCheckpointSaver[int]):
    """Durable JSON-file checkpointer with an explicit head pointer and thread retirement.

    ``get_next_version`` is inherited from the base class (integer, monotonic, +1) rather
    than overridden, which is what the ``channel_versions`` bookkeeping below assumes.
    """

    def __init__(self, path: str | os.PathLike[str], *, serde: Any = None,
                 clock: Any = None,
                 lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        super().__init__(serde=serde or JsonPlusSerializer())
        try:
            self._section = FileCriticalSection(path, clock=clock,
                                                lock_timeout_seconds=lock_timeout_seconds)
        except LockUnavailable as exc:  # pragma: no cover - non-POSIX hosts only
            raise CheckpointStoreLockUnavailable(str(exc)) from exc
        self.path = Path(path)

    # ---- document ---------------------------------------------------------------
    def _read(self) -> dict[str, Any]:
        try:
            document = read_json_document(self.path,
                                          schema_version=CHECKPOINT_STORE_SCHEMA_VERSION,
                                          corrupt_exc=CheckpointStoreCorrupt)
        except LockTimeout as exc:  # pragma: no cover - defensive
            raise CheckpointStoreLockTimeout(str(exc)) from exc
        if not document:
            return {"schema_version": CHECKPOINT_STORE_SCHEMA_VERSION, "threads": {}}
        if set(document) - {"schema_version", "threads"}:
            raise CheckpointStoreCorrupt("CHECKPOINT_STORE_CORRUPT:top-level keys")
        threads = document.get("threads")
        if not isinstance(threads, dict):
            raise CheckpointStoreCorrupt("CHECKPOINT_STORE_CORRUPT:threads container")
        for thread_id, thread in threads.items():
            self._validate_thread(thread_id, thread)
        return document

    @staticmethod
    def _validate_thread(thread_id: str, thread: Any) -> None:
        if not isinstance(thread, dict) or set(thread) != {"retired", "retired_reason",
                                                           "namespaces"}:
            raise CheckpointStoreCorrupt(f"CHECKPOINT_STORE_CORRUPT:{thread_id}:closed fields")
        if type(thread["retired"]) is not bool:
            raise CheckpointStoreCorrupt(f"CHECKPOINT_STORE_CORRUPT:{thread_id}:retired type")
        namespaces = thread["namespaces"]
        if not isinstance(namespaces, dict):
            raise CheckpointStoreCorrupt(f"CHECKPOINT_STORE_CORRUPT:{thread_id}:namespaces")
        for namespace, entry in namespaces.items():
            if not isinstance(entry, dict) or set(entry) != {"head", "next_sequence",
                                                             "checkpoints", "blobs", "writes"}:
                raise CheckpointStoreCorrupt(
                    f"CHECKPOINT_STORE_CORRUPT:{thread_id}/{namespace}:closed fields")
            checkpoints = entry["checkpoints"]
            if not isinstance(checkpoints, dict):
                raise CheckpointStoreCorrupt(
                    f"CHECKPOINT_STORE_CORRUPT:{thread_id}/{namespace}:checkpoints")
            head = entry["head"]
            if head and head not in checkpoints:
                raise CheckpointStoreCorrupt(
                    f"CHECKPOINT_STORE_CORRUPT:{thread_id}/{namespace}:head names no checkpoint")
            for checkpoint_id, saved in checkpoints.items():
                if not isinstance(saved, dict) or set(saved) != {
                        "sequence", "parent_checkpoint_id", "checkpoint", "metadata",
                        "channel_versions", "schema_version", "written_at"}:
                    raise CheckpointStoreCorrupt(
                        f"CHECKPOINT_STORE_CORRUPT:{thread_id}/{namespace}/{checkpoint_id}")
                if saved["schema_version"] != contracts.SCHEMA_VERSION:
                    raise CheckpointStoreCorrupt(
                        f"CHECKPOINT_STORE_CORRUPT:{thread_id}/{namespace}/{checkpoint_id}:"
                        f"state schema {saved['schema_version']!r}")
                # A ``channel_versions`` entry with no blob is NOT corruption: LangGraph
                # records versions for trigger/managed channels that carry no value, and
                # the reference ``InMemorySaver._load_blobs`` skips exactly those. Treating
                # it as corruption refused every real checkpoint the graph writes.

    def _write(self, document: dict[str, Any]) -> None:
        write_json_document(self.path, document)

    @staticmethod
    def _namespace(document: dict[str, Any], thread_id: str,
                   checkpoint_ns: str) -> dict[str, Any]:
        thread = document["threads"].setdefault(
            thread_id, {"retired": False, "retired_reason": None, "namespaces": {}})
        return thread["namespaces"].setdefault(
            checkpoint_ns, {"head": "", "next_sequence": 0, "checkpoints": {},
                            "blobs": {}, "writes": {}})

    # ---- BaseCheckpointSaver surface --------------------------------------------
    def put(self, config: Any, checkpoint: Any, metadata: Any,
            new_versions: ChannelVersions) -> Any:
        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        copy = dict(checkpoint)
        values: dict[str, Any] = dict(copy.pop("channel_values", {}) or {})
        with self._section.locked():
            document = self._read()
            thread = document["threads"].get(thread_id)
            if thread is not None and thread["retired"]:
                raise CheckpointThreadRetired(
                    f"CHECKPOINT_STORE_RETIRED:{thread_id}:{thread['retired_reason']}")
            entry = self._namespace(document, thread_id, checkpoint_ns)
            for channel, version in (new_versions or {}).items():
                payload = (self.serde.dumps_typed(values[channel]) if channel in values
                           else ("empty", b""))
                entry["blobs"].setdefault(channel, {})[str(version)] = {
                    "type": payload[0], "payload_b64": _b64(payload[1])}
            checkpoint_type, checkpoint_bytes = self.serde.dumps_typed(copy)
            metadata_type, metadata_bytes = self.serde.dumps_typed(
                get_checkpoint_metadata(config, metadata))
            state_schema = values.get("schema_version")
            entry["checkpoints"][checkpoint["id"]] = {
                "sequence": entry["next_sequence"],
                "parent_checkpoint_id": configurable.get("checkpoint_id"),
                "checkpoint": {"type": checkpoint_type, "payload_b64": _b64(checkpoint_bytes)},
                "metadata": {"type": metadata_type, "payload_b64": _b64(metadata_bytes)},
                "channel_versions": {key: value for key, value
                                     in (copy.get("channel_versions") or {}).items()},
                "schema_version": (state_schema if isinstance(state_schema, str)
                                   else contracts.SCHEMA_VERSION),
                "written_at": _now(),
            }
            entry["next_sequence"] += 1
            entry["head"] = checkpoint["id"]
            self._write(document)
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
                                 "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config: Any, writes: Sequence[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = configurable["checkpoint_id"]
        with self._section.locked():
            document = self._read()
            entry = self._namespace(document, thread_id, checkpoint_ns)
            bucket = entry["writes"].setdefault(checkpoint_id, {}).setdefault(task_id, {})
            for index, (channel, value) in enumerate(writes):
                payload_type, payload = self.serde.dumps_typed(value)
                bucket[str(index)] = {"channel": channel, "task_path": task_path,
                                      "value": {"type": payload_type,
                                                "payload_b64": _b64(payload)}}
            self._write(document)

    def _load_blobs(self, entry: dict[str, Any],
                    channel_versions: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for channel, version in channel_versions.items():
            saved = (entry["blobs"].get(channel) or {}).get(str(version))
            if saved is None or saved["type"] == "empty":
                continue
            values[channel] = self.serde.loads_typed(
                (saved["type"], _unb64(saved["payload_b64"])))
        return values

    def _tuple(self, thread_id: str, checkpoint_ns: str, entry: dict[str, Any],
               checkpoint_id: str) -> CheckpointTuple:
        saved = entry["checkpoints"][checkpoint_id]
        checkpoint = self.serde.loads_typed(
            (saved["checkpoint"]["type"], _unb64(saved["checkpoint"]["payload_b64"])))
        metadata = self.serde.loads_typed(
            (saved["metadata"]["type"], _unb64(saved["metadata"]["payload_b64"])))
        writes = []
        for task_id, bucket in (entry["writes"].get(checkpoint_id) or {}).items():
            for _, item in sorted(bucket.items(), key=lambda pair: int(pair[0])):
                writes.append((task_id, item["channel"], self.serde.loads_typed(
                    (item["value"]["type"], _unb64(item["value"]["payload_b64"])))))
        parent = saved["parent_checkpoint_id"]
        return CheckpointTuple(
            config={"configurable": {"thread_id": thread_id,
                                     "checkpoint_ns": checkpoint_ns,
                                     "checkpoint_id": checkpoint_id}},
            checkpoint={**checkpoint,
                        "channel_values": self._load_blobs(
                            entry, dict(checkpoint.get("channel_versions") or {}))},
            metadata=metadata,
            parent_config=({"configurable": {"thread_id": thread_id,
                                             "checkpoint_ns": checkpoint_ns,
                                             "checkpoint_id": parent}} if parent else None),
            pending_writes=writes,
        )

    def get_tuple(self, config: Any) -> CheckpointTuple | None:
        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        with self._section.locked():
            document = self._read()
            thread = document["threads"].get(thread_id)
            if thread is None:
                return None
            entry = thread["namespaces"].get(checkpoint_ns)
            if entry is None or not entry["checkpoints"]:
                return None
            checkpoint_id = get_checkpoint_id(config) or entry["head"]
            if not checkpoint_id or checkpoint_id not in entry["checkpoints"]:
                return None
            return self._tuple(thread_id, checkpoint_ns, deepcopy(entry), checkpoint_id)

    def list(self, config: Any, *, filter: Any = None, before: Any = None,
             limit: int | None = None) -> Iterator[CheckpointTuple]:
        configurable = (config or {}).get("configurable") or {}
        thread_id = configurable.get("thread_id")
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        with self._section.locked():
            document = self._read()
        threads = ([thread_id] if thread_id else list(document["threads"]))
        before_id = get_checkpoint_id(before) if before else None
        produced = 0
        for name in threads:
            thread = document["threads"].get(name)
            if thread is None:
                continue
            entry = thread["namespaces"].get(checkpoint_ns)
            if entry is None:
                continue
            ordered = sorted(entry["checkpoints"].items(),
                             key=lambda pair: pair[1]["sequence"], reverse=True)
            for checkpoint_id, saved in ordered:
                if before_id is not None and saved["sequence"] >= entry["checkpoints"][
                        before_id]["sequence"]:
                    continue
                candidate = self._tuple(name, checkpoint_ns, entry, checkpoint_id)
                if filter and any(candidate.metadata.get(key) != value
                                  for key, value in filter.items()):
                    continue
                yield candidate
                produced += 1
                if limit is not None and produced >= limit:
                    return

    def delete_thread(self, thread_id: str) -> None:
        """Declared by the base class.  **No OS-31 path calls it** -- see ``retire_thread``."""
        with self._section.locked():
            document = self._read()
            document["threads"].pop(thread_id, None)
            self._write(document)

    # ---- async twins -------------------------------------------------------------
    async def aget_tuple(self, config: Any) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(self, config: Any, *, filter: Any = None, before: Any = None,
                    limit: int | None = None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(self, config: Any, checkpoint: Any, metadata: Any,
                   new_versions: ChannelVersions) -> Any:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: Any, writes: Sequence[tuple[str, Any]],
                          task_id: str, task_path: str = "") -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)

    # ---- OS-31 additions ---------------------------------------------------------
    def head(self, thread_id: str, *, checkpoint_ns: str = "") -> str | None:
        """The explicit head pointer, written inside the same critical section as ``put``."""
        with self._section.locked():
            document = self._read()
            thread = document["threads"].get(thread_id)
            if thread is None:
                return None
            entry = thread["namespaces"].get(checkpoint_ns)
            if entry is None or not entry["head"]:
                return None
            return entry["head"]

    def checkpoint_digest(self, thread_id: str, checkpoint_id: str, *,
                          checkpoint_ns: str = "") -> str:
        """``sha256`` over the stored payloads, so a rewritten checkpoint is evident."""
        import hashlib

        with self._section.locked():
            document = self._read()
            thread = document["threads"].get(thread_id)
            entry = (thread or {}).get("namespaces", {}).get(checkpoint_ns)
            saved = (entry or {}).get("checkpoints", {}).get(checkpoint_id)
            if saved is None:
                raise CheckpointStoreError(
                    f"PAUSE_CHECKPOINT_MISSING:{thread_id}/{checkpoint_ns}/{checkpoint_id}")
            payload = {
                "checkpoint_type": saved["checkpoint"]["type"],
                "checkpoint_b64": saved["checkpoint"]["payload_b64"],
                "metadata_type": saved["metadata"]["type"],
                "metadata_b64": saved["metadata"]["payload_b64"],
                "channel_versions": saved["channel_versions"],
            }
        return hashlib.sha256(contracts.canonical_bytes(payload)).hexdigest()

    def retire_thread(self, thread_id: str, *, reason: str) -> None:
        """Retire, never delete: the checkpoint is the audit evidence for a disposal."""
        with self._section.locked():
            document = self._read()
            thread = document["threads"].setdefault(
                thread_id, {"retired": False, "retired_reason": None, "namespaces": {}})
            thread["retired"] = True
            thread["retired_reason"] = reason
            self._write(document)

    def is_retired(self, thread_id: str) -> bool:
        with self._section.locked():
            thread = self._read()["threads"].get(thread_id)
            return bool(thread and thread["retired"])
