"""Shared durable-file discipline: flock critical section + atomic replace.

This module carries no LangGraph and no Orca dependency by design (OS-31 §10.1): the
Tier-2 pause record, the settlement journal and the Tier-1 checkpoint store all need the
same ``lock -> read -> validate -> mutate -> persist -> unlock`` sequence, and only one of
them may import LangGraph.

It is a faithful re-implementation of the discipline ``runtime_state.py`` proves out
(``runtime_state.py`` module docstring, ``_locked``/``_flocked``, ``_write``): ``flock`` on
a sidecar ``<path>.lock`` with a finite, injectable timeout, a per-thread re-entrant depth
counter guarded by an ``RLock``, the document read *after* the lock is held, and
``fsync`` + ``os.replace`` on write.

``runtime_state.py`` is deliberately **not** refactored onto this module: its critical
section is entangled with the ``LeaseKeeper`` background renewal thread and the depth
counter that thread depends on, and extracting it would be a concurrency refactor of the
most safety-critical existing module for no behavioural gain.  The cost is a few duplicated
lines; the benefit is zero regression risk on the lease/keeper tests.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:  # pragma: no cover - POSIX in this repository's supported environments
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.005


class DurableStoreError(ValueError):
    """A durable store could not be read, locked or written under its own contract."""


class LockUnavailable(DurableStoreError):
    """This platform offers no inter-process file lock, so exclusivity is impossible."""


class LockTimeout(DurableStoreError):
    """The inter-process lock could not be acquired inside the explicit timeout."""


class _SystemClock:
    def time(self) -> float:
        import time

        return time.time()

    def sleep(self, seconds: float) -> None:
        import time

        time.sleep(seconds)


class FileCriticalSection:
    """``lock -> read -> validate -> mutate -> persist -> unlock`` on a sidecar lock file.

    Re-entrant within one thread so a composed operation does not deadlock on itself;
    ``flock`` is per open file description, so the outermost frame owns the handle.  The
    acquisition loop has an explicit, injectable timeout and never waits forever.
    """

    def __init__(self, path: str | os.PathLike[str], *, clock: Any | None = None,
                 lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        if fcntl is None:  # pragma: no cover - exercised only on non-POSIX hosts
            raise LockUnavailable(
                "DURABLE_STORE_LOCK_UNAVAILABLE: fcntl.flock is required for an exclusive "
                "inter-process critical section; this store is POSIX-only by design and "
                "refuses to run unlocked.")
        self.path = Path(path)
        self.lock_path = Path(f"{self.path}.lock")
        self.clock = clock or _SystemClock()
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self._mutex = threading.RLock()
        self._depth = 0

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._mutex:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            with self._flocked():
                yield

    @contextmanager
    def _flocked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = self.clock.time() + self.lock_timeout_seconds
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if self.clock.time() >= deadline:
                        raise LockTimeout(
                            f"DURABLE_STORE_LOCK_TIMEOUT:{self.path} after "
                            f"{self.lock_timeout_seconds}s") from None
                    self.clock.sleep(LOCK_POLL_SECONDS)
            self._depth = 1
            try:
                yield
            finally:
                self._depth = 0
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def read_json_document(path: str | os.PathLike[str], *, schema_version: str,
                       corrupt_exc: type[Exception]) -> dict[str, Any]:
    """Read a closed JSON document, or refuse.  An absent file is ``{}``; a broken one raises.

    "Unreadable" is never "empty": a corrupt or version-incompatible document raises
    ``corrupt_exc`` rather than being read as an empty store, which is the failure mode that
    would let a durable record silently disappear.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise corrupt_exc(f"UNREADABLE_DURABLE_STORE:{target}:{exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise corrupt_exc(f"UNREADABLE_DURABLE_STORE:{target}:{exc}") from exc
    if not isinstance(raw, dict):
        raise corrupt_exc(f"MALFORMED_DURABLE_STORE:{target}:top-level container")
    version = raw.get("schema_version")
    if type(version) is not str:
        raise corrupt_exc(f"MALFORMED_DURABLE_STORE:{target}:schema_version missing")
    if version != schema_version:
        raise corrupt_exc(
            f"INCOMPATIBLE_DURABLE_STORE:{target}:{version} != {schema_version}")
    return raw


def write_json_document(path: str | os.PathLike[str], payload: dict[str, Any]) -> None:
    """Persist one whole JSON document atomically: temp file + fsync + ``os.replace``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                         prefix=f".{target.name}.", delete=False)
    try:
        with handle:
            json.dump(payload, handle, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
