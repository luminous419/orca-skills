"""Deterministic in-memory adapter for Orca-independent workflow execution."""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import pause_policy
from .contracts import (BASE_CAPABILITIES, EXTERNAL_LOOKUP, EXTERNAL_RESUME,
                        LIFECYCLE_SETTLEMENT, ActionIntent, RECOVERY_CAPABILITIES,
                        SettlementEvent, make_settlement_event)

# Reserved top-level keys in the external world document.  An intent id is always
# ``intent_<hex>``, so these cannot collide with one.
_TERMINALS_KEY = "__terminals__"
_WORKTREES_KEY = "__worktrees__"


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

    @staticmethod
    def _effects(world: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in world.items()
                if key not in (_TERMINALS_KEY, _WORKTREES_KEY)}

    # ---- OS-31: the terminal listing a fresh process enumerates (SS4.2.1a) ----
    def register_worktree(self, selector: str, worktree_id: str = "") -> None:
        """Make a worktree selector resolvable, mirroring ``orca worktree show``."""
        world = self._read()
        world.setdefault(_WORKTREES_KEY, {})[selector] = (
            worktree_id or selector.split(":", 1)[-1])
        self._write(world)

    def resolve_worktree(self, selector: str) -> dict[str, Any] | None:
        """``ok:false``/``selector_not_found`` is modelled as ``None``.

        A zero-length listing under an unresolvable selector is *unknown*, never *empty*,
        which is exactly why this guard exists separately from the listing itself.
        """
        found = (self._read().get(_WORKTREES_KEY) or {}).get(selector)
        return {"id": found} if found else None

    def create_terminal(self, *, worktree: str, handle: str, title: str) -> dict[str, Any]:
        world = self._read()
        element = {"handle": handle, "title": title, "worktreeId": worktree,
                   "orphaned": False, "connected": True}
        world.setdefault(_TERMINALS_KEY, {}).setdefault(worktree, []).append(element)
        world.setdefault(_WORKTREES_KEY, {}).setdefault(worktree,
                                                        worktree.split(":", 1)[-1])
        self._write(world)
        return dict(element)

    def close_terminal(self, *, worktree: str, handle: str) -> None:
        world = self._read()
        listing = (world.get(_TERMINALS_KEY) or {}).get(worktree) or []
        world.setdefault(_TERMINALS_KEY, {})[worktree] = [
            element for element in listing if element["handle"] != handle]
        self._write(world)

    def list_terminals(self, worktree: str) -> list[dict[str, Any]]:
        """Per-selector, so a query under a *different* selector returns an empty array."""
        return [dict(element)
                for element in (self._read().get(_TERMINALS_KEY) or {}).get(worktree) or []]

    def create(self, intent: ActionIntent) -> dict[str, Any]:
        """Register the external effect for a stable intent, idempotently."""
        world = self._read()
        entry = world.get(intent["intent_id"])
        if entry is None:
            entry = {"external_id": f"ext_{len(self._effects(world)) + 1}_{intent['intent_id'][-8:]}",
                     "intent_id": intent["intent_id"], "outcome": None, "occurred_at": None}
            world[intent["intent_id"]] = entry
            self._write(world)
        return deepcopy(entry)

    def find(self, intent_id: str) -> dict[str, Any] | None:
        entry = self._effects(self._read()).get(intent_id)
        return {"external_id": entry["external_id"], "intent_id": intent_id} if entry else None

    def complete(self, intent_id: str, result: dict[str, Any], occurred_at: str) -> None:
        world = self._read()
        entry = world.get(intent_id)
        if entry is None:
            raise KeyError(intent_id)
        entry["outcome"] = deepcopy(result)
        entry["occurred_at"] = occurred_at
        self._write(world)

    def intent_ids(self) -> tuple[str, ...]:
        """Leg (3) analogue: every effect this world knows about, however it was created."""
        return tuple(sorted(self._effects(self._read())))

    def outcome(self, intent_id: str) -> dict[str, Any] | None:
        entry = self._effects(self._read()).get(intent_id)
        if entry is None or entry.get("outcome") is None:
            return None
        return deepcopy(entry)


class IdempotencyConflict(ValueError): pass


class FakeAdapter:
    """The Orca-independent adapter, and OS-31's ``LifecycleSettlementPort`` reference impl.

    ``open_dispatches`` reads the same durable journal and the same durable runtime-state
    file the Orca implementation reads, plus its ``FileExternalWorld`` listing as the
    leg-(3) analogue -- so the fake exercises the *reconstruction*, not a memory read.
    """

    def __init__(self, results: list[dict[str, Any]], *, capabilities: frozenset[str] = BASE_CAPABILITIES,
                 runtime_state: Any = None, external_world: Any = None,
                 run_id: str = "", settlement_journal: Any = None,
                 approval_port: Any = None, worktree: str = "id:fakerepo::/fake/wt",
                 axes: dict[str, dict[str, Any]] | None = None):
        self.results = list(results); self._capabilities = capabilities
        self.receipts: dict[str, dict[str, Any]] = {}; self.events: dict[str, SettlementEvent] = {}
        self.runtime_state = runtime_state
        self.external_world = external_world
        self.effect_count = 0
        self.run_id = run_id
        self.settlement_journal = settlement_journal
        self.approval_port = approval_port
        self.worktree = worktree
        # Injectable scripted axis outcome per dispatch, so a test can construct
        # not_settled, unknown authority, disputed liveness, an unnamed terminal owner and
        # a W-C orphan deliberately rather than by accident.
        self.axes: dict[str, dict[str, Any]] = dict(axes or {})
        self.lifecycle_commands: list[tuple[str, str]] = []
        self.listing_readable = True

    def capabilities(self) -> frozenset[str]:
        # The recovery capabilities are declared only when a durable external world actually
        # backs them.  Declaring a capability the adapter cannot honour is what would let the
        # recovery ladder believe an effect was proven absent when it was merely unreadable.
        offered = self._capabilities
        if self.settlement_journal is not None:
            offered = offered | frozenset({LIFECYCLE_SETTLEMENT})
        if self.approval_port is not None:
            offered = offered | frozenset({"human_approval"})
        if self.external_world is None:
            return offered
        return offered | RECOVERY_CAPABILITIES

    # ---- OS-31 LifecycleSettlementPort -------------------------------------------
    def terminal_title(self, intent_id: str) -> str:
        return f"os31-{self.run_id}-{intent_id}"

    def plan_dispatch(self, intent: ActionIntent, *, role: str = "phase_worker",
                      origin: str = "self_created", created_by: str = "os31",
                      now: str = "") -> dict[str, Any]:
        """Write the PLANNED row BEFORE the first external effect, then create the terminal.

        Nothing written here is a *runtime observation of an effect*, so nothing written
        here can be lost with the effect.
        """
        intent_id = intent["intent_id"]
        self.settlement_journal.record(
            intent_id, stage="PLANNED", run_id=self.run_id,
            payload_digest=intent["payload_digest"],
            terminal_title=self.terminal_title(intent_id),
            terminal_worktree=self.worktree, terminal_role=role,
            terminal_origin=origin, terminal_intended_role=role,
            terminal_owner=created_by, created_by=created_by, planned_at=now or "planned",
            provenance_source="journal")
        return self.settlement_journal.row(intent_id)

    def open_terminal(self, intent_id: str, *, task_id: str, now: str = "") -> None:
        self.settlement_journal.record(intent_id, stage="OPENED", task_id=task_id,
                                       opened_at=now or "opened")

    def intend_terminal(self, intent_id: str, handle: str, *, now: str = "") -> None:
        """The handle is now bound to provenance that was durable before it existed."""
        self.settlement_journal.record(
            intent_id, stage="INTENDED",
            terminal_digest=pause_policy.terminal_digest(handle),
            provenance_source="journal", intended_at=now or "intended")

    def open_dispatches(self) -> tuple[str, ...]:
        if self.settlement_journal is None:
            raise RuntimeError("DISPATCH_UNACCOUNTED: no durable settlement journal")
        found = {row["intent_id"] for row in self.settlement_journal.open_rows()}
        if self.runtime_state is not None:
            for intent_id, record in _durable_records(self.runtime_state).items():
                if record.get("status") in ("EFFECTED", "SETTLED"):
                    found.add(intent_id)
        if self.external_world is not None:
            found.update(self.external_world.intent_ids())
        finished = {row["intent_id"] for row in self.settlement_journal.rows().values()
                    if row["stage"] == "DISPOSED"}
        return tuple(sorted(found - finished))

    def recover_handle(self, intent_id: str) -> dict[str, Any]:
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {"intent_id": intent_id, "stage": "PLANNED"}
        if not self.listing_readable:
            # Unreadable is unknown, never empty -- the same rule ``lookup`` already states.
            return dict(pause_policy.resolve_terminal_handle(row, None))
        selector = row.get("terminal_worktree") or ""
        listing = (self.external_world.list_terminals(selector)
                   if self.external_world is not None else [])
        scope_resolved = (self.external_world is not None
                          and self.external_world.resolve_worktree(selector) is not None)
        return dict(pause_policy.resolve_terminal_handle(
            row, listing, scope_resolved=scope_resolved))

    def account_dispatch(self, intent_id: str) -> dict[str, Any]:
        """Read-only: issues no mutation, so repeating it after a crash is always safe."""
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {}
        scripted = dict(self.axes.get(intent_id) or {})
        accounted = {key: "" for key in pause_policy.SETTLEMENT_ROW_KEYS}
        for key in pause_policy.SETTLEMENT_ROW_KEYS:
            value = row.get(key)
            if isinstance(value, str) and value:
                accounted[key] = value
        accounted.update({"intent_id": intent_id,
                          "settlement": "settled", "worker_resource": "retain",
                          "process_liveness": "live", "cleanup_authority": "not_authorized",
                          "terminal_disposition": "", "recovery": "observed"})
        accounted.update(scripted)
        return accounted

    def recover_dispatch(self, intent_id: str, *, reason: str) -> dict[str, Any]:
        self.lifecycle_commands.append(("worker-abandon", intent_id))
        self.lifecycle_commands.append(("worker-release", intent_id))
        return {"settlement": "recovered", "recovery": f"abandon:outcome_unknown:{reason}"}

    def release_terminal(self, intent_id: str, *, authority: str) -> dict[str, Any]:
        if authority != "authorized":
            raise ValueError("release_terminal is only ever called with proven authority")
        self.lifecycle_commands.append(("worker-release", intent_id))
        action = (self.axes.get(intent_id) or {}).get("release_process_action", "none")
        if action in pause_policy.PROCESS_TERMINATING_ACTIONS:
            return {"recovery": f"released:{action}", "process_liveness": "already exited"}
        # The empirically observed live receipt: retained/external_terminal/none.  D-6/R8-iii
        # says that is NOT a release, whatever cleanup authority said.
        return {"recovery": f"retained:{action}"}

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


def _durable_records(runtime_state: Any) -> dict[str, Any]:
    """Leg (2): durable receipts written by a process older than this journal schema."""
    reader = getattr(runtime_state, "_read", None)
    if reader is None:
        return {}
    locked = getattr(runtime_state, "_locked", None)
    if locked is None:
        return dict(reader())
    with locked():
        return dict(reader())


class FakeArtifactStore:
    def __init__(self): self.items: dict[str, bytes] = {}
    def put(self, intent: ActionIntent, content: bytes) -> dict[str, Any]:
        old = self.items.get(intent["intent_id"])
        if old is not None and old != content: raise IdempotencyConflict(intent["intent_id"])
        self.items[intent["intent_id"]] = content
        return {"artifact_id": intent["intent_id"], "size": len(content)}
    def get(self, artifact_id: str) -> bytes: return self.items[artifact_id]
    def evidence(self, evidence_id: str) -> bytes: return self.items[evidence_id]
