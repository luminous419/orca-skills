"""Shared OS-31 end-to-end fixture: a real pause on FakeAdapter with NO Orca runtime.

Everything here runs offline.  ``FakeAdapter`` is the vehicle, ``ArtifactHumanApprovalPort``
is the real OS-30 implementation over a temp artifact base (not a simplified stand-in), and
"crash" is expressed the way ``test_deterministic_workflow_ownership`` already expresses it:
stop driving, **drop every in-memory object**, and build a fresh driver over the same
on-disk stores.

This module is named ``test_...`` so it is importable by the suites that share it under
unittest discovery; it deliberately declares no ``TestCase`` of its own.
"""
from __future__ import annotations

import importlib.metadata
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any


def langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


REQUIRES_LANGGRAPH = unittest.skipUnless(langgraph_ok(),
                                         "requires pinned langgraph 0.2.76")

WORKER = {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}
REVIEW_PASS = {"result": "PASS", "review_verdict": "PASS", "findings": []}
WORKTREE_A = "id:repoA::/wt/a"
WORKTREE_B = "id:repoB::/wt/b"


def clarification_item(run_id: str, *, suffix: str = "1", open_item: str = "target",
                       depends_on: tuple[str, ...] = (),
                       independent_with: tuple[str, ...] = ()) -> dict[str, Any]:
    key = f"{run_id}/analysis/1/B2#{suffix}"
    return {
        "open_item": open_item, "source_ledger_key": key, "source_ledger_keys": [key],
        "source_state": "NEEDS_INPUT", "source_reason_code": "user_choice_required",
        "phase": "analysis", "iteration": 1,
        "question": "Which deployment target should be used?",
        "context": "The analysis is complete.",
        "what_is_blocked": "The phase cannot proceed without an explicit target.",
        "options": [
            {"option_id": "staging", "label": "Staging", "action": "deploy to staging",
             "tradeoff": "No production traffic."},
            {"option_id": "production", "label": "Production",
             "action": "deploy to production", "tradeoff": "Immediate user impact."}],
        "recommended_option_id": "staging",
        "recommendation_rationale": "It limits initial risk.", "deadline_at": None,
        "depends_on": list(depends_on), "independent_with": list(independent_with),
        "custom_decision": {"allowed": False, "subject": "", "value_type": "none",
                            "max_length": 0, "pattern": None, "allowed_values": [],
                            "sensitive": False},
        "narrowing_rationale": "",
    }


class PauseFixture(unittest.TestCase):
    """Drive a run to a durable pause, then resume/dispose it from a FRESH object graph."""

    RUN = "run_pause"
    ITEMS = 1

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "base"
        self.base.mkdir()
        self.sources = self.build_sources()

    # ---- construction ------------------------------------------------------
    def build_sources(self):
        """A bundle of ITEMS independent questions, declared symmetrically as OS-30 requires."""
        from scripts.clarification_protocol import ClarificationSource, decision_item_id
        keys = {index: f"{self.RUN}/analysis/1/B2#{index}"
                for index in range(1, self.ITEMS + 1)}
        ids = {index: decision_item_id(self.RUN, "analysis", f"target_{index}",
                                       keys[index])
               for index in keys}
        sources = []
        for index, key in keys.items():
            suffix = str(index)
            peers = tuple(sorted(value for other, value in ids.items() if other != index))
            sources.append(ClarificationSource(
                open_item=f"target_{suffix}", source_ledger_key=key,
                source_ledger_keys=(key,), state="NEEDS_INPUT",
                reason_code="user_choice_required", phase="analysis", iteration=1,
                request_input=clarification_item(self.RUN, suffix=suffix,
                                                 open_item=f"target_{suffix}",
                                                 independent_with=peers)))
        return tuple(sources)

    def approval_port(self):
        from scripts.clarification_protocol import ArtifactHumanApprovalPort
        return ArtifactHumanApprovalPort(self.base)

    def world(self):
        from scripts.deterministic_workflow.fake_adapter import FileExternalWorld
        world = FileExternalWorld(self.root / "world.json")
        world.register_worktree(WORKTREE_A)
        return world

    def ledger(self):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.root / "ledger.json")

    def journal(self):
        from scripts.deterministic_workflow import pause_store
        return pause_store.journal_for(self.RUN, artifact_base=self.base)

    def saver(self):
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        return FileCheckpointSaver(self.checkpoint_path)

    @property
    def checkpoint_path(self) -> Path:
        return self.root / "cp.json"

    def store(self, owner_id=None):
        from scripts.deterministic_workflow import pause_store
        return pause_store.store_for(self.RUN, artifact_base=self.base, owner_id=owner_id)

    def adapter(self, results=(), *, world=None, axes=None, worktree=WORKTREE_A,
                journal=None, approval_port=None, ledger=None, run_id=None):
        """``run_id`` defaults to this fixture's run; a caller that only knows a run id it
        DISCOVERED passes that one, so nothing about its driver is taken on trust."""
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        return FakeAdapter(list(results), runtime_state=ledger or self.ledger(),
                           external_world=world if world is not None else self.world(),
                           run_id=run_id or self.RUN,
                           settlement_journal=journal or self.journal(),
                           approval_port=approval_port or self.approval_port(),
                           worktree=worktree, axes=axes)

    def graph(self, adapter, saver, *, approval_port=None, journal=None):
        from scripts.deterministic_workflow.graph import build_graph
        return build_graph(adapter, checkpointer=saver, runtime_state=adapter.runtime_state,
                           approval_port=approval_port or adapter.approval_port,
                           journal=journal or adapter.settlement_journal,
                           sources_provider=lambda state: self.sources)

    def initial_state(self, *, decision_state="NEEDS_INPUT", phases=("ANALYSIS",)):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.state import initial_state
        capabilities = BASE_CAPABILITIES | frozenset({"human_approval",
                                                      "lifecycle_settlement"})
        state = dict(initial_state(run_id=self.RUN, thread_id="t", phases=phases,
                                   capabilities=capabilities))
        state["decision_state"] = decision_state
        state["decision_reason_code"] = "user_choice_required"
        return state

    # ---- the pause ---------------------------------------------------------
    def drive_to_pause(self, *, adapter=None, dispatches=(), state=None):
        """Run the graph until it commits the pause checkpoint and the Tier-2 record."""
        from scripts.deterministic_workflow import pause_runtime
        adapter = adapter or self.adapter()
        for intent_id in dispatches:
            self.seed_dispatch(adapter, intent_id)
        saver = self.saver()
        graph = self.graph(adapter, saver)
        config = {"configurable": {"thread_id": "t", "checkpoint_ns": ""},
                  "recursion_limit": 200}
        final = graph.invoke(state or self.initial_state(), config)
        record = pause_runtime.finalize_pause(
            final, saver=saver, store=self.store(),
            checkpoint_store_path=str(self.checkpoint_path), artifact_base=self.base)
        return final, record, adapter

    def seed_dispatch(self, adapter, intent_id, *, stage="INTENDED",
                      worktree=WORKTREE_A, handle=None, title=None, listed_title=None):
        """Write the journal rows a real dispatch would have written, in stage order."""
        handle = handle or f"term_{intent_id}"
        adapter.settlement_journal.record(
            intent_id, stage="PLANNED", run_id=self.RUN, payload_digest="d",
            terminal_title=title or adapter.terminal_title(intent_id),
            terminal_worktree=worktree, terminal_role="phase_worker",
            terminal_origin="self_created", terminal_intended_role="phase_worker",
            terminal_owner=self.RUN, created_by=self.RUN, provenance_source="journal",
            planned_at="t0")
        if stage in ("OPENED", "INTENDED", "ACCOUNTED", "DISPOSED"):
            adapter.settlement_journal.record(intent_id, stage="OPENED",
                                              task_id=f"task_{intent_id}", opened_at="t1")
        if stage in ("INTENDED", "ACCOUNTED", "DISPOSED"):
            adapter.settlement_journal.record(intent_id, stage="INTENDED",
                                              intended_at="t2")
            # ``listed_title`` is what the RUNTIME shows (decorated, as observed live);
            # ``title`` is what the journal recorded before the terminal existed.
            adapter.external_world.create_terminal(
                worktree=worktree, handle=handle,
                title=listed_title or title or adapter.terminal_title(intent_id))
            self.set_digest(adapter, intent_id, handle)
        return handle

    @staticmethod
    def set_digest(adapter, intent_id, handle):
        from scripts.deterministic_workflow import pause_policy
        adapter.settlement_journal.record(
            intent_id, stage="INTENDED",
            terminal_digest=pause_policy.terminal_digest(handle))

    # ---- the answer --------------------------------------------------------
    def requests(self):
        root = self.base / "artifacts" / "runs" / self.RUN / "clarifications" / "requests"
        return [json.loads(path.read_text())
                for path in sorted(root.glob("request_*/record.json"))]

    def answer_all(self, *, option="staging", token="submission_1"):
        from scripts.clarification_protocol import ResponseSubmission
        port = self.approval_port()
        for request in self.requests():
            for index, item in enumerate(request["items"]):
                port.ingest(run_id=self.RUN, request_id=request["request_id"],
                            decision_item_id=item["decision_item_id"],
                            submission=ResponseSubmission(
                                f"{token}_{index}", "alice", "human", "desk",
                                "2026-09-01T08:00:00Z", option, None, False, "normal"))

    # ---- the resume, from a fresh object graph -----------------------------
    def fresh_resume(self, record, *, results=(WORKER, REVIEW_PASS, REVIEW_PASS),
                     repository=None, artifact=None, policy_digest=None,
                     store=None, world=None, observe_timeout_seconds=1.0):
        """Every object below is new: nothing of the paused driver survives."""
        from scripts.deterministic_workflow import pause_runtime
        port = self.approval_port()
        adapter = self.adapter(results, world=world)
        projection = record["projection"]

        def graph_factory(saver):
            return self.graph(adapter, saver, approval_port=port)

        outcome = pause_runtime.resume_run(
            self.RUN, artifact_base=self.base, approval_port=port,
            graph_factory=graph_factory,
            current_repository=repository or projection["repository_binding"],
            current_artifact=artifact or projection["artifact_binding"],
            current_policy_digest=(policy_digest if policy_digest is not None
                                   else projection["policy_digest"]),
            store=store, recursion_limit=300,
            observe_timeout_seconds=observe_timeout_seconds)
        return outcome, adapter

    def fresh_dispose(self, *, kind="CANCEL", results=(), submission_id="cancel_1",
                      store=None, world=None, observe_timeout_seconds=1.0):
        from scripts.deterministic_workflow import pause_runtime
        port = self.approval_port()
        adapter = self.adapter(results, world=world)

        def graph_factory(saver):
            return self.graph(adapter, saver, approval_port=port)

        outcome = pause_runtime.dispose_run(
            self.RUN, artifact_base=self.base, kind=kind, actor_id="alice",
            actor_type="human", submission_id=submission_id,
            reason="the requirement was withdrawn", graph_factory=graph_factory,
            approval_port=port, store=store, settlement_port=adapter, recursion_limit=300,
            observe_timeout_seconds=observe_timeout_seconds)
        return outcome, adapter

    # ---- assertions used by more than one suite ----------------------------
    def artifact_digests(self):
        root = self.base / "artifacts" / "runs" / self.RUN / "clarifications"
        import hashlib
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(root.rglob("*")) if path.is_file()}

    def orchestrator_rows(self):
        path = self.base / "artifacts" / "runs" / self.RUN / "ORCHESTRATOR_LOG.md"
        if not path.exists():
            return []
        return [line for line in path.read_text().splitlines() if line.startswith("|")]
