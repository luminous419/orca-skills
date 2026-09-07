"""OS-43 U-3: the discovery surface that also reaches runs holding NO pause record.

``pause_store.discover_paused_runs`` ``continue``s past every directory with no pause
record, so a stalled ACTIVE run is invisible to it -- that invisibility is the gap this
unit closes, and the first test below pins it against running source rather than asserting
it from the design.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import pause_store, recovery_runtime
from scripts.test_deterministic_workflow_pause import record as pause_record
from scripts.test_deterministic_workflow_pause_fixture import REQUIRES_LANGGRAPH


def langgraph_ok() -> bool:
    from scripts.test_deterministic_workflow_pause_fixture import langgraph_ok as probe
    return probe()


class DiscoveryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runs = self.base / "artifacts" / "runs"
        self.runs.mkdir(parents=True)

    def make_run(self, run_id: str) -> Path:
        root = self.runs / run_id
        root.mkdir(exist_ok=True)
        return root

    def make_paused(self, run_id: str) -> None:
        self.make_run(run_id)
        pause_store.store_for(run_id, artifact_base=self.base).create(
            pause_record(run_id=run_id))

    def make_checkpointed(self, run_id: str, *, terminal: str | None = None) -> None:
        """A run with a real OS-40 checkpoint and NO pause record."""
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.state import initial_state
        root = self.make_run(run_id)
        saver = FileCheckpointSaver(root / ".workflow_checkpoints.json")
        state = dict(initial_state(run_id=run_id, thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        if terminal:
            # ``SETTLED <=> terminal_status is not None`` is a biconditional the closed
            # state enforces, and a terminal state may carry no pending role, intent or
            # event (``POST_TERMINAL_EVENT``).  A terminal fixture has to satisfy both.
            state["terminal_status"] = terminal
            state["run_lifecycle"] = "SETTLED"
            state["pending_role"] = None
            state["pending_intent"] = None
            state["pending_event"] = None
        checkpoint = {"v": 1, "id": f"chk_{run_id}", "ts": "2026-01-01T00:00:00Z",
                      "channel_values": dict(state),
                      "channel_versions": {key: 1 for key in state},
                      "versions_seen": {}, "pending_sends": []}
        saver.put({"configurable": {"thread_id": "t", "checkpoint_ns": ""}}, checkpoint,
                  {"source": "loop", "step": 0}, {key: 1 for key in state})

    def rows(self, **kwargs):
        listings = recovery_runtime.discover_recoverable_runs(self.base, **kwargs)
        return {row["run_id"]: row for row in listings}


@REQUIRES_LANGGRAPH
class DiscoveryReachTests(DiscoveryFixture):
    def test_the_OS31_surface_is_BLIND_to_a_run_with_no_pause_record(self):
        """The gap, pinned against running source before anything is built on it."""
        self.make_checkpointed("run_active")
        self.assertEqual(pause_store.discover_paused_runs(self.base), ())

    def test_the_new_surface_reaches_a_stalled_ACTIVE_run(self):
        self.make_checkpointed("run_active")
        row = self.rows()["run_active"]
        self.assertEqual(row["verdict"], recovery_runtime.RECOVERY_STALLED_RECOVERABLE)
        self.assertEqual(row["recovery_kind"],
                         recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)
        self.assertEqual(row["next_node"], "PREPARE_WORKER")
        self.assertEqual(row["thread_id"], "t")

    def test_a_paused_run_keeps_OS31s_OWN_verdict_rather_than_a_renamed_one(self):
        self.make_paused("run_paused")
        row = self.rows()["run_paused"]
        self.assertEqual(row["recovery_kind"],
                         recovery_runtime.RECOVERY_KIND_PAUSE_CONTINUATION)
        self.assertIn(row["verdict"], recovery_runtime.DISCOVERY_VERDICTS)

    def test_a_TERMINAL_run_is_reported_with_no_runnable_node(self):
        self.make_checkpointed("run_done", terminal="COMPLETED")
        self.assertEqual(self.rows()["run_done"]["verdict"], "RECOVERY_NO_RUNNABLE_NODE")

    def test_a_run_with_no_checkpoint_authority_at_all_is_named_not_omitted(self):
        self.make_run("run_bare")
        self.assertEqual(self.rows()["run_bare"]["verdict"], "NO_CHECKPOINT_AUTHORITY")

    def test_all_four_dispositions_are_enumerated_in_one_listing(self):
        self.make_paused("run_paused")
        self.make_checkpointed("run_active")
        self.make_checkpointed("run_done", terminal="COMPLETED")
        self.make_run("run_bare")
        rows = self.rows()
        self.assertEqual(set(rows),
                         {"run_paused", "run_active", "run_done", "run_bare"})
        for run_id, row in rows.items():
            self.assertIn(row["verdict"], recovery_runtime.DISCOVERY_VERDICTS, run_id)

    def test_a_corrupt_checkpoint_is_REPORTED_with_a_named_verdict_never_omitted(self):
        self.make_checkpointed("run_broken")
        (self.runs / "run_broken" / ".workflow_checkpoints.json").write_text(
            "{not json", encoding="utf-8")
        row = self.rows()["run_broken"]
        self.assertEqual(row["verdict"], "PAUSE_RECORD_CORRUPT")
        self.assertTrue(row["detail"])


class DiscoveryDisciplineTests(DiscoveryFixture):
    """Read-only, and "unknown" is never "empty"."""

    def test_an_unreadable_RUNS_ROOT_raises_rather_than_reporting_an_empty_fleet(self):
        import os
        if os.geteuid() == 0:                 # pragma: no cover - root ignores the mode
            self.skipTest("root can read a 0o000 directory")
        self.make_run("run_a")
        self.runs.chmod(0o000)
        self.addCleanup(lambda: self.runs.chmod(0o755))
        with self.assertRaises(recovery_runtime.RunsRootUnreadable):
            recovery_runtime.discover_recoverable_runs(self.base)

    def test_an_absent_runs_root_is_an_empty_listing_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(recovery_runtime.discover_recoverable_runs(tmp), ())

    def test_discovery_takes_NO_claim_and_performs_no_effect(self):
        self.make_run("run_a")
        if langgraph_ok():
            self.make_checkpointed("run_active")
        before = sorted(path.name for path in self.runs.rglob("*"))
        recovery_runtime.discover_recoverable_runs(
            self.base, langgraph_available=langgraph_ok())
        self.assertEqual(sorted(path.name for path in self.runs.rglob("*")), before,
                         "a discovery that wrote something is not a discovery")

    def test_a_degraded_runtime_is_NAMED_never_reported_as_recoverable(self):
        self.make_run("run_active")
        (self.runs / "run_active" / ".workflow_checkpoints.json").write_text(
            "{}", encoding="utf-8")
        row = {entry["run_id"]: entry for entry in
               recovery_runtime.discover_recoverable_runs(
                   self.base, langgraph_available=False)}["run_active"]
        self.assertEqual(row["verdict"], "CHECKPOINT_UNVERIFIED")
        self.assertNotIn(row["verdict"], recovery_runtime.DISCOVERY_ACTIONABLE_VERDICTS)

    def test_the_verdict_vocabulary_is_CLOSED(self):
        with self.assertRaises(ValueError):
            recovery_runtime._row("run_a", kind="unknown", verdict="MADE_UP")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
