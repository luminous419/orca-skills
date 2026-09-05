"""Orca adapter composed only from real ``OrcaRuntimeHarness`` primitives."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from . import pause_policy
from .contracts import (BASE_CAPABILITIES, EXTERNAL_LOOKUP, LIFECYCLE_SETTLEMENT,
                        ActionIntent, ExternalLookupUnavailable, SettlementEvent,
                        make_settlement_event)

# OS-31 SS4.2.1. `active`/`current` are documented ALIASES that the reading process
# re-resolves, so persisting one persists no worktree at all. Only a stable
# `id:<repo-id>::<path>` selector denotes the same worktree in every process.
WORKTREE_ALIASES = frozenset({"current", "active"})


class OrcaAdapter:
    """Synchronous façade over ``create_task`` and ``run_existing_task``."""

    def __init__(self, harness: Any,
                 result_parser: Callable[[Any, ActionIntent], dict[str, Any]] | None = None,
                 runtime_state: Any = None, settlement_journal: Any = None,
                 approval_port: Any = None):
        self.harness = harness
        self.result_parser = result_parser or self._parse_result
        self.runtime_state = runtime_state
        # OS-31: the durable, run-scoped, append-then-promote journal. Every write lands
        # strictly BEFORE the external effect it describes, because process memory
        # (`_receipts` here, `_terminals` in the harness) is exactly what a successor
        # Coordinator does not have.
        self.settlement_journal = settlement_journal
        self.approval_port = approval_port
        self._receipts: dict[str, dict[str, Any]] = {}
        self._events: dict[str, SettlementEvent] = {}

    def capabilities(self) -> frozenset[str]:
        """The capabilities Orca's primitives actually support -- and no others.

        ``external_lookup`` is declared because ``orca orchestration task-list --run`` returns
        each Task's full spec, and every spec this adapter creates is the canonical intent
        JSON, so an existing Task can be found by stable ``intent_id``.

        ``external_resume`` is deliberately **not** declared.  ``worker_done`` is delivered
        once, to the message stream of the process that owns the run; a settlement delivered
        to a process that has since died cannot be re-collected through any documented Orca
        primitive, and ``task-create`` accepts no idempotency key that would let one be
        reconstructed.  Rather than pretend otherwise, recovery of an already-dispatched
        effect fails closed (``IDEMPOTENCY_RECOVERY_UNSUPPORTED`` -> BLOCKED) and the
        remaining reconciliation is an operator decision.  Closing that window is OS-37's
        production process/PTY ownership work, not OS-40's.
        """
        offered = BASE_CAPABILITIES | frozenset(
            {"dispatch_provenance", "dependency_edges", "runtime_ownership", EXTERNAL_LOOKUP}
        )
        # OS-31: declared only when the durable journal that makes them honourable is
        # actually wired in. An adapter that cannot reconstruct the dispatch set from disk
        # does not satisfy LifecycleSettlementPort and must not claim it -- pause then
        # correctly falls back to BLOCK.
        if self.settlement_journal is not None:
            offered = offered | frozenset({LIFECYCLE_SETTLEMENT})
        if self.approval_port is not None:
            offered = offered | frozenset({"human_approval"})
        return offered

    def lookup(self, intent: ActionIntent) -> dict[str, Any] | None:
        """Find the Task created for this stable intent, or prove that none was.

        Returns ``None`` only when the run's Task listing was read successfully and contains
        no Task whose spec *is* this intent -- the one situation in which re-running the
        effect is safe.  Matching parses each spec and compares the top-level ``intent_id``
        rather than searching the raw text, so an unrelated spec that merely quotes the id is
        not mistaken for this intent's Task.  Anything that leaves existence unknown raises
        :class:`ExternalLookupUnavailable` so the caller stops instead of guessing.
        """
        run_id = getattr(self.harness, "run_id", None)
        if not run_id:
            raise ExternalLookupUnavailable("no run is bound; task existence cannot be read")
        try:
            payload = self.harness.call("orchestration", "task-list", "--run", run_id)
            tasks = payload["result"]["tasks"]
        except Exception as exc:  # noqa: BLE001 - any read failure is "unknown", not "absent"
            raise ExternalLookupUnavailable(f"task listing unreadable: {exc}") from exc
        if not isinstance(tasks, list):
            raise ExternalLookupUnavailable("task listing has an unexpected shape")
        for task in tasks:
            if not isinstance(task, dict) or "spec" not in task:
                raise ExternalLookupUnavailable(
                    "task listing omits specs; intent identity cannot be matched")
            if self._spec_intent_id(task["spec"]) == intent["intent_id"]:
                return {"task_id": task.get("id"), "intent_id": intent["intent_id"]}
        return None

    @staticmethod
    def _spec_intent_id(spec: Any) -> str | None:
        """The top-level ``intent_id`` of a Task spec this adapter wrote, or ``None``.

        Every spec ``start`` creates is the canonical intent JSON, so identity is a parsed
        field comparison.  A substring search over the raw text would also match a spec that
        merely *mentions* the id -- for example one whose payload quotes another intent --
        and a foreign spec must not be mistaken for this intent's effect.  Anything that is
        not a JSON object simply belongs to no intent.
        """
        if not isinstance(spec, str):
            return None
        try:
            parsed = json.loads(spec)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        found = parsed.get("intent_id")
        return found if isinstance(found, str) else None

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

    def start(self, intent: ActionIntent, *, lease_token: str | None = None) -> dict[str, Any]:
        """Create the Task once, recording its identity under the caller's lease.

        Every ledger write below is fenced by ``lease_token``: an executor whose lease was
        taken over while it was blocked in ``create_task`` is refused here rather than
        overwriting the successor's receipt with a second Task's identity.
        """
        existing = self._receipts.get(intent["intent_id"]) or self._durable_receipt(intent)
        if existing is not None:
            if existing["payload_digest"] != intent["payload_digest"]:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return deepcopy(existing)
        spec = json.dumps(intent, sort_keys=True, separators=(",", ":"))
        # ---- E0: the PLANNED row, written BEFORE `task-create` ----
        # Not one field of it is a runtime observation of an effect -- role, origin, the
        # run-unique title and the stable worktree selector are all this caller's own
        # choice -- so not one field of it can be lost with the effect.
        planned = self._journal_planned(intent)
        task_id = self.harness.create_task(spec)
        # The external Task now exists.  Record its durable identity immediately so a crash
        # before the dispatch settles cannot look like "never started" on the next process.
        self._record_receipt(intent, {"task_id": task_id}, lease_token)
        self._journal(intent["intent_id"], stage="OPENED", task_id=task_id,
                      opened_at=_now())
        phase = "final_review" if intent["role"] == "FINAL_REVIEWER" else intent["phase"].lower()
        iteration = (intent["final_review_iteration"] if intent["role"] == "FINAL_REVIEWER"
                     else intent["phase_iteration"] + 1)
        mode = "complete" if intent["role"] == "WORKER" else "pass"
        attempt, terminal = self.harness.run_existing_task(
            self._role(intent), iteration, mode, task_id,
            phase=phase, spec=spec, round_kind=intent["round_kind"].lower(),
            terminal_title=(planned or {}).get("terminal_title"),
            terminal_worktree=(planned or {}).get("terminal_worktree"),
            terminal_observer=(self._journal_intended(intent["intent_id"])
                               if planned else None),
        )
        result = self.result_parser(attempt, intent)
        event = make_settlement_event(intent, result, occurred_at="1970-01-01T00:00:00Z")
        receipt = {"intent_id": intent["intent_id"], "payload_digest": intent["payload_digest"],
                   "task_id": task_id, "dispatch_id": attempt.dispatch_id, "terminal": terminal}
        self._receipts[intent["intent_id"]] = receipt
        self._events[intent["intent_id"]] = event
        # ``terminal`` is a runtime handle and is deliberately never persisted.
        self._record_receipt(intent, {"task_id": task_id, "dispatch_id": attempt.dispatch_id},
                             lease_token)
        if self.runtime_state is not None:
            self.runtime_state.settle(intent["intent_id"], event, lease_token)
        return deepcopy(receipt)

    # ---- OS-31: the durable settlement journal (SS4.2.1) ----
    def _journal(self, intent_id: str, *, stage: str, **fields: Any) -> None:
        if self.settlement_journal is not None:
            self.settlement_journal.record(intent_id, stage=stage, **fields)

    def origin_worktree_selector(self) -> str:
        """The stable `id:<repo-id>::<path>` selector of the worktree this process is in.

        Read through the verb the harness ALREADY executes during contract validation, so
        no new grammar is introduced, and read strictly before E1 -- it is a property of
        where this Coordinator is, not an observation of an effect E1 or E2 produced, which
        is exactly why it can be journalled before the crash window opens.
        """
        try:
            payload = self.harness.call("worktree", "current")
            worktree_id = payload["result"]["worktree"]["id"]
        except Exception as exc:  # noqa: BLE001 - unreadable is unknown, never an alias
            raise ExternalLookupUnavailable(
                f"DISPATCH_UNACCOUNTED: the origin worktree could not be resolved: {exc}"
            ) from exc
        if (not isinstance(worktree_id, str) or "::" not in worktree_id
                or worktree_id in WORKTREE_ALIASES):
            raise ExternalLookupUnavailable(
                "DISPATCH_UNACCOUNTED: `worktree current` returned no stable "
                f"<repo-id>::<path> identity ({worktree_id!r}); substituting the alias "
                "would produce a row that LOOKS recoverable and is not")
        return f"id:{worktree_id}"

    def _journal_planned(self, intent: ActionIntent) -> dict[str, Any] | None:
        """Refuse BEFORE E1 rather than fall back to the alias: nothing is made, nothing leaks."""
        if self.settlement_journal is None:
            return None
        run_id = getattr(self.harness, "run_id", "") or ""
        intent_id = intent["intent_id"]
        role = "phase_reviewer" if intent["role"] != "WORKER" else "phase_worker"
        self._journal(
            intent_id, stage="PLANNED", run_id=run_id,
            payload_digest=intent["payload_digest"],
            terminal_title=f"os31-{run_id}-{intent_id}",
            terminal_worktree=self.origin_worktree_selector(),
            terminal_role="active_worker", terminal_origin="self_created",
            terminal_intended_role=role, terminal_owner=run_id or intent_id,
            created_by=run_id or intent_id, provenance_source="journal",
            planned_at=_now())
        return self.settlement_journal.row(intent_id)

    def _journal_intended(self, intent_id: str) -> Callable[[str], None]:
        def observer(handle: str) -> None:
            # Between `terminal create` and `worker-start`: the ONLY point at which a
            # durable write can sit between the two effects.
            self._journal(intent_id, stage="INTENDED",
                          terminal_digest=pause_policy.terminal_digest(handle),
                          provenance_source="journal", intended_at=_now())
        return observer

    # ---- OS-31 LifecycleSettlementPort ----
    def open_dispatches(self) -> tuple[str, ...]:
        """The three-legged durable reconstruction, never ``self._receipts``.

        (1) journal rows not at DISPOSED, (2) durable runtime-state receipts with no
        journal row, (3) the authoritative `task-list --run` listing, whose Tasks appear in
        neither -- a FOREIGN Task this adapter did not create, reported rather than adopted.
        A source that cannot be read is "unknown", never "empty".
        """
        if self.settlement_journal is None:
            raise ExternalLookupUnavailable(
                "DISPATCH_UNACCOUNTED: no durable settlement journal is wired in")
        rows = self.settlement_journal.rows()
        found = {intent_id for intent_id, row in rows.items() if row["stage"] != "DISPOSED"}
        if self.runtime_state is not None:
            for intent_id in self._durable_intent_ids():
                if intent_id not in rows:
                    found.add(intent_id)
        for intent_id in self._listed_intent_ids():
            if intent_id not in rows:
                found.add(intent_id)
        return tuple(sorted(found))

    def _durable_intent_ids(self) -> tuple[str, ...]:
        reader = getattr(self.runtime_state, "_read", None)
        locked = getattr(self.runtime_state, "_locked", None)
        if reader is None:
            return ()
        records = reader() if locked is None else self._locked_read(locked, reader)
        return tuple(intent_id for intent_id, record in records.items()
                     if record.get("status") in ("EFFECTED", "SETTLED"))

    @staticmethod
    def _locked_read(locked: Any, reader: Any) -> dict[str, Any]:
        with locked():
            return dict(reader())

    def _listed_intent_ids(self) -> tuple[str, ...]:
        run_id = getattr(self.harness, "run_id", None)
        if not run_id:
            return ()
        try:
            tasks = self.harness.call("orchestration", "task-list",
                                      "--run", run_id)["result"]["tasks"]
        except Exception as exc:  # noqa: BLE001 - unreadable is unknown, never empty
            raise ExternalLookupUnavailable(f"task listing unreadable: {exc}") from exc
        found = []
        for task in tasks or ():
            intent_id = self._spec_intent_id((task or {}).get("spec"))
            if intent_id:
                found.append(intent_id)
        return tuple(found)

    def recover_handle(self, intent_id: str) -> dict[str, Any]:
        """Enumerate, narrow by normalised run-unique title, then PROVE with the digest.

        The title narrows; the digest decides.  A handle is returned only for
        ``listing_verified``; every other cell of the SS4.2.1a table fails closed, and the
        decision itself lives in the pure policy module, not here.
        """
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {"intent_id": intent_id, "stage": "PLANNED"}
        selector = row.get("terminal_worktree") or ""
        if not selector or selector in WORKTREE_ALIASES:
            return {"handle": None, "handle_recovery": "scope_unresolved"}
        try:
            listing: Any = list(self.harness.list_terminals(worktree=selector))
        except Exception:  # noqa: BLE001 - unreadable is unknown, never empty
            listing = None
        scope_resolved = True
        if not listing:
            # "Absent" is only meaningful inside a scope that provably resolves: an
            # unresolvable selector returns ok:true with an empty array, which on its own
            # is indistinguishable from a real worktree holding no terminals.
            resolved = self.harness.resolve_worktree(selector)
            expected = selector.split("id:", 1)[-1]
            scope_resolved = bool(resolved) and resolved.get("id") == expected
        return dict(pause_policy.resolve_terminal_handle(
            row, listing, scope_resolved=scope_resolved))

    def account_dispatch(self, intent_id: str) -> dict[str, Any]:
        """Read-only: delegates to ``harness.account_axes``, which issues ZERO commands."""
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {}
        handle = self.recover_handle(intent_id).get("handle") or ""
        task_id = row.get("task_id", "")
        dispatch_id = row.get("dispatch_id", "") or self._dispatch_for_task(task_id)
        observation, task_status, supervised = self._observe(task_id, dispatch_id)
        if handle:
            # Provenance is re-seeded from the JOURNAL, never from worker-show, whose
            # verified response carries no role, no origin and no terminal handle at all.
            self.harness.register_terminal(
                handle, role=row.get("terminal_role") or "unknown_role",
                origin=row.get("terminal_origin") or "unknown",
                intended_role=row.get("terminal_intended_role") or None,
                owner_dispatch_id=dispatch_id or None,
                created_by=row.get("created_by", ""))
        settlement, worker_resource, liveness, authority, role = self.harness.account_axes(
            task_id, dispatch_id, handle, supervised=supervised, observation=observation,
            task_status=task_status, lifecycle="retain")
        accounted = {key: "" for key in pause_policy.SETTLEMENT_ROW_KEYS}
        for key in pause_policy.SETTLEMENT_ROW_KEYS:
            value = row.get(key)
            if isinstance(value, str) and value:
                accounted[key] = value
        accounted.update({
            "intent_id": intent_id, "task_id": task_id, "dispatch_id": dispatch_id,
            "settlement": "settled" if settlement in ("completed", "failed") else "not_settled",
            "worker_resource": worker_resource,
            "process_liveness": liveness, "cleanup_authority": authority,
            "terminal_role": role, "recovery": accounted.get("recovery") or "observed",
            "terminal_disposition": "",
        })
        return accounted

    def _dispatch_for_task(self, task_id: str) -> str:
        if not task_id:
            return ""
        try:
            shown = self.harness.call("orchestration", "dispatch-show",
                                      "--task", task_id)["result"]
        except Exception:  # noqa: BLE001
            return ""
        dispatch = shown.get("dispatch") or {}
        return dispatch.get("id", "") if isinstance(dispatch, dict) else ""

    def _observe(self, task_id: str, dispatch_id: str) -> tuple[dict[str, Any], str, bool]:
        observation: dict[str, Any] = {}
        supervised = bool(dispatch_id)
        if dispatch_id:
            try:
                observation = dict(self.harness.call(
                    "orchestration", "worker-show", "--dispatch", dispatch_id)["result"])
            except Exception:  # noqa: BLE001
                observation = {}
                supervised = False
        task_status = ""
        if task_id:
            try:
                task_status = self.harness.task_status(task_id)
            except Exception:  # noqa: BLE001
                task_status = ""
        return observation, task_status, supervised

    def recover_dispatch(self, intent_id: str, *, reason: str) -> dict[str, Any]:
        """`worker-abandon` -> `worker-release`, or `task-update --status failed`.

        Accounted **recovered**, never "settled": this dispatch produced no accepted
        worker_done, so there is no role promotion here either.
        """
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {}
        dispatch_id = row.get("dispatch_id", "") or self._dispatch_for_task(
            row.get("task_id", ""))
        if not dispatch_id:
            self.harness.call("orchestration", "task-update", "--id", row.get("task_id", ""),
                              "--status", "failed", "--result",
                              json.dumps({"reason": reason}))
            return {"settlement": "recovered", "recovery": "task-update:failed"}
        shown = self.harness.call("orchestration", "worker-show",
                                  "--dispatch", dispatch_id)["result"]
        state = ((shown.get("worker") or {}).get("state") or "")
        recovery = "observed"
        if state in ("outcome_unknown", "ready"):
            abandoned = self.harness.call("orchestration", "worker-abandon",
                                          "--dispatch", dispatch_id)
            recovery = f"abandon:{abandoned['result']['state']}"
        self.harness.call("orchestration", "worker-release", "--dispatch", dispatch_id)
        return {"settlement": "recovered", "recovery": recovery}

    def release_terminal(self, intent_id: str, *, authority: str) -> dict[str, Any]:
        """Called ONLY with proven authority and a `release` lifecycle intent."""
        if authority != "authorized":
            raise ValueError("release_terminal requires proven cleanup authority")
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {}
        dispatch_id = row.get("dispatch_id", "")
        released = self.harness.call("orchestration", "worker-release",
                                     "--dispatch", dispatch_id)["result"]
        action = released.get("processAction", "")
        if action in pause_policy.PROCESS_TERMINATING_ACTIONS:
            return {"recovery": f"released:{action}", "process_liveness": "already exited"}
        # D-6/R8-iii: a release receipt that does not prove a termination means the runtime
        # KEPT the process, whatever cleanup authority said.
        return {"recovery": f"retained:{action or 'none'}"}

    def _record_receipt(self, intent: ActionIntent, receipt: dict[str, Any],
                        lease_token: str | None) -> None:
        if self.runtime_state is not None:
            self.runtime_state.record_receipt(intent["intent_id"], receipt, lease_token)

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


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ORCA_PRIMITIVE_MAP = {
    "start": ("create_task", "run_existing_task"),
    "send": ("call",),
    "status": ("task_status",),
    "interrupt": ("call",),
}
