"""OS-31 Tier-1 regressions: the durable checkpointer is the AUTHORITY for a paused run.

Gated on the pinned LangGraph, unlike ``test_deterministic_workflow_pause``.  The gating
asymmetry is itself the V8 evidence: the authority module is LangGraph-dependent, the
index/policy modules are not.
"""
from __future__ import annotations

import importlib.metadata
import json
import tempfile
import threading
import unittest
from pathlib import Path


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


WORKER = {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}
REVIEW_PASS = {"result": "PASS", "review_verdict": "PASS", "findings": []}


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class FileCheckpointSaverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def saver(self, name="cp.json"):
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        return FileCheckpointSaver(self.root / name)

    def state(self, run_id="run_cp", thread_id="t"):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.state import initial_state
        return dict(initial_state(run_id=run_id, thread_id=thread_id,
                                  phases=("ANALYSIS",), capabilities=BASE_CAPABILITIES))

    def put(self, saver, thread_id, values, checkpoint_id, parent=None):
        checkpoint = {"v": 1, "id": checkpoint_id, "ts": "2026-01-01T00:00:00Z",
                      "channel_values": dict(values),
                      "channel_versions": {key: 1 for key in values},
                      "versions_seen": {}, "pending_sends": []}
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        if parent:
            config["configurable"]["checkpoint_id"] = parent
        return saver.put(config, checkpoint, {"source": "loop", "step": 0},
                         {key: 1 for key in values})

    def test_every_closed_state_field_survives_a_round_trip(self):
        from scripts.deterministic_workflow.state import WorkflowState
        saver = self.saver()
        state = self.state()
        self.put(saver, "t", state, "chk_1")
        tuple_ = saver.get_tuple({"configurable": {"thread_id": "t", "checkpoint_ns": ""}})
        restored = dict(tuple_.checkpoint["channel_values"])
        self.assertEqual(set(restored), set(WorkflowState.__required_keys__))
        for key, value in state.items():
            self.assertEqual(restored[key], value, key)

    def test_the_head_is_an_explicit_pointer_and_is_monotonic_across_writes(self):
        saver = self.saver()
        self.put(saver, "t", self.state(), "chk_1")
        self.assertEqual(saver.head("t"), "chk_1")
        self.put(saver, "t", self.state(), "chk_2", parent="chk_1")
        self.assertEqual(saver.head("t"), "chk_2")
        document = json.loads((self.root / "cp.json").read_text())
        entry = document["threads"]["t"]["namespaces"][""]
        self.assertEqual(entry["head"], "chk_2")
        self.assertEqual(entry["checkpoints"]["chk_1"]["sequence"], 0)
        self.assertEqual(entry["checkpoints"]["chk_2"]["sequence"], 1)

    def test_the_digest_changes_when_the_stored_checkpoint_changes(self):
        saver = self.saver()
        self.put(saver, "t", self.state(), "chk_1")
        first = saver.checkpoint_digest("t", "chk_1")
        self.assertEqual(first, saver.checkpoint_digest("t", "chk_1"))
        document = json.loads((self.root / "cp.json").read_text())
        entry = document["threads"]["t"]["namespaces"][""]["checkpoints"]["chk_1"]
        entry["checkpoint"]["payload_b64"] = "AAAA"
        (self.root / "cp.json").write_text(json.dumps(document))
        self.assertNotEqual(first, saver.checkpoint_digest("t", "chk_1"))

    def test_a_missing_checkpoint_digest_is_a_named_refusal(self):
        from scripts.deterministic_workflow.checkpoint_store import CheckpointStoreError
        saver = self.saver()
        self.put(saver, "t", self.state(), "chk_1")
        with self.assertRaises(CheckpointStoreError):
            saver.checkpoint_digest("t", "chk_absent")

    def test_retire_blocks_writes_but_keeps_the_checkpoint_readable_as_evidence(self):
        from scripts.deterministic_workflow.checkpoint_store import CheckpointThreadRetired
        saver = self.saver()
        self.put(saver, "t", self.state(), "chk_1")
        saver.retire_thread("t", reason="cancel_run_1")
        self.assertTrue(saver.is_retired("t"))
        with self.assertRaises(CheckpointThreadRetired):
            self.put(saver, "t", self.state(), "chk_2")
        tuple_ = saver.get_tuple({"configurable": {"thread_id": "t", "checkpoint_ns": ""}})
        self.assertIsNotNone(tuple_, "a disposed run's checkpoint is the audit evidence")

    def test_a_corrupt_store_is_refused_and_never_read_as_empty(self):
        from scripts.deterministic_workflow.checkpoint_store import CheckpointStoreCorrupt
        saver = self.saver()
        self.put(saver, "t", self.state(), "chk_1")
        document = json.loads((self.root / "cp.json").read_text())
        document["threads"]["t"]["namespaces"][""]["head"] = "chk_absent"
        (self.root / "cp.json").write_text(json.dumps(document))
        with self.assertRaises(CheckpointStoreCorrupt):
            saver.head("t")

    def test_an_incompatible_schema_version_is_refused(self):
        from scripts.deterministic_workflow.checkpoint_store import CheckpointStoreCorrupt
        (self.root / "cp.json").write_text(json.dumps({"schema_version": "other",
                                                       "threads": {}}))
        with self.assertRaises(CheckpointStoreCorrupt):
            self.saver().head("t")

    def test_a_checkpoint_written_by_an_older_state_schema_is_refused(self):
        from scripts.deterministic_workflow.checkpoint_store import CheckpointStoreCorrupt
        saver = self.saver()
        self.put(saver, "t", self.state(), "chk_1")
        document = json.loads((self.root / "cp.json").read_text())
        document["threads"]["t"]["namespaces"][""]["checkpoints"]["chk_1"][
            "schema_version"] = "os40.workflow.v0"
        (self.root / "cp.json").write_text(json.dumps(document))
        with self.assertRaises(CheckpointStoreCorrupt):
            saver.get_tuple({"configurable": {"thread_id": "t", "checkpoint_ns": ""}})

    def test_two_concurrent_writers_are_serialised_and_both_land(self):
        saver_a, saver_b = self.saver(), self.saver()
        errors: list[BaseException] = []

        def write(saver, checkpoint_id):
            try:
                for index in range(10):
                    self.put(saver, "t", self.state(), f"{checkpoint_id}_{index}")
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(saver_a, "a")),
                   threading.Thread(target=write, args=(saver_b, "b"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        document = json.loads((self.root / "cp.json").read_text())
        self.assertEqual(len(document["threads"]["t"]["namespaces"][""]["checkpoints"]), 20)

    def test_a_non_default_namespace_is_stored_and_read_back(self):
        """OI-2: only "" is exercised end to end, but the store is written for the general case."""
        saver = self.saver()
        checkpoint = {"v": 1, "id": "chk_ns", "ts": "2026-01-01T00:00:00Z",
                      "channel_values": self.state(), "channel_versions": {},
                      "versions_seen": {}, "pending_sends": []}
        saver.put({"configurable": {"thread_id": "t", "checkpoint_ns": "sub"}},
                  checkpoint, {"source": "loop", "step": 0}, {})
        self.assertEqual(saver.head("t", checkpoint_ns="sub"), "chk_ns")
        self.assertIsNone(saver.head("t"))

    def test_pending_writes_round_trip(self):
        saver = self.saver()
        self.put(saver, "t", self.state(), "chk_1")
        saver.put_writes({"configurable": {"thread_id": "t", "checkpoint_ns": "",
                                           "checkpoint_id": "chk_1"}},
                         [("channel_a", {"value": 1})], "task_1")
        tuple_ = saver.get_tuple({"configurable": {"thread_id": "t", "checkpoint_ns": "",
                                                   "checkpoint_id": "chk_1"}})
        self.assertEqual(tuple_.pending_writes,
                         [("task_1", "channel_a", {"value": 1})])

    def test_delete_thread_exists_but_no_os31_path_calls_it(self):
        """The base class declares it; OS-31 retires, and a grep-style assertion says so."""
        import ast
        import inspect
        from scripts.deterministic_workflow import pause_runtime
        from scripts.deterministic_workflow import executor
        for module in (pause_runtime, executor):
            tree = ast.parse(inspect.getsource(module))
            called = {node.func.attr for node in ast.walk(tree)
                      if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute)}
            self.assertNotIn("delete_thread", called, module.__name__)


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class DefaultWiringTests(unittest.TestCase):
    """REQ-1: the shipped command line is checkpoint-durable with no extra flags."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_build_graph_refuses_without_a_durable_checkpointer(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import (DurableCheckpointerRequired,
                                                          build_graph)
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        store = FileRuntimeStateStore(self.root / "ledger.json")
        adapter = FakeAdapter([], runtime_state=store)
        with self.assertRaises(DurableCheckpointerRequired):
            build_graph(adapter, runtime_state=store)
        with self.assertRaises(DurableCheckpointerRequired):
            build_graph(adapter, checkpointer=MemorySaver(), runtime_state=store)

    def test_the_named_test_only_escape_hatch_is_what_keeps_memorysaver_valid(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        store = FileRuntimeStateStore(self.root / "ledger.json")
        graph = build_graph(FakeAdapter([], runtime_state=store), runtime_state=store,
                            checkpointer=MemorySaver(),
                            require_durable_checkpointer=False)
        self.assertIsNotNone(graph)

    def test_execute_state_installs_a_durable_saver_and_sets_the_thread_unconditionally(self):
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.launcher import execute_state
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        from scripts.deterministic_workflow.state import initial_state
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        store = FileRuntimeStateStore(self.root / "ledger.json")
        state = dict(initial_state(run_id="run_default", thread_id="t",
                                   phases=("ANALYSIS",), capabilities=BASE_CAPABILITIES))
        path = self.root / "checkpoints.json"
        out = execute_state(state, adapter=FakeAdapter([WORKER, REVIEW_PASS, REVIEW_PASS],
                                                       runtime_state=store),
                            runtime_state=store, checkpoint_store_path=path)
        self.assertEqual(out["terminal_status"], "COMPLETED")
        self.assertTrue(path.exists(), "the default path must leave a durable checkpoint")
        self.assertIsNotNone(FileCheckpointSaver(path).head("t"))

    def test_resolve_checkpoint_path_follows_its_declared_resolution_order(self):
        import os
        from unittest.mock import patch
        from scripts.deterministic_workflow import launcher
        explicit = launcher.resolve_checkpoint_path("run_a", "t",
                                                    explicit=self.root / "x.json")
        self.assertEqual(explicit, self.root / "x.json")
        with patch.dict(os.environ, {launcher.CHECKPOINT_DIR_ENV: str(self.root)}):
            env = launcher.resolve_checkpoint_path("run_a", "t")
        self.assertEqual(env.parent, self.root)
        default = launcher.resolve_checkpoint_path("run_a", "t", artifact_base=self.root)
        self.assertEqual(default,
                         self.root / "artifacts" / "runs" / "run_a"
                         / launcher.CHECKPOINT_STORE_FILENAME)

    def test_a_brand_new_process_rebuilds_the_state_field_for_field(self):
        """T-41: reconstruction reads the checkpoint and nothing else."""
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.launcher import execute_state
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        from scripts.deterministic_workflow.state import initial_state, validate_state
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        store = FileRuntimeStateStore(self.root / "ledger.json")
        state = dict(initial_state(run_id="run_rebuild", thread_id="t",
                                   phases=("ANALYSIS",), capabilities=BASE_CAPABILITIES))
        path = self.root / "checkpoints.json"
        final = execute_state(state, adapter=FakeAdapter([WORKER, REVIEW_PASS, REVIEW_PASS],
                                                         runtime_state=store),
                              runtime_state=store, checkpoint_store_path=path)
        fresh = FileCheckpointSaver(path)          # a new object over the same file
        head = fresh.head("t")
        tuple_ = fresh.get_tuple({"configurable": {"thread_id": "t", "checkpoint_ns": "",
                                                   "checkpoint_id": head}})
        from scripts.deterministic_workflow.pause_runtime import restore_closed_state
        # LangGraph omits a channel from a snapshot when its value is None, so the closed
        # field set is restored explicitly -- a checkpoint is never a partial state.
        rebuilt = validate_state(restore_closed_state(tuple_.checkpoint["channel_values"]),
                                 expected_thread_id="t")
        for key, value in final.items():
            if value is None:
                continue
            self.assertEqual(rebuilt[key], value, key)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
