"""M-001 regression: the shipped launcher must actually execute the graph."""
from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_TOOLS = REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools"
LAUNCHER = SKILL_TOOLS / "run_workflow.py"
LAUNCHER_MODULE = SKILL_TOOLS / "deterministic_workflow" / "launcher.py"
CANONICAL_PHASES = ("ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST")


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


def canonical_results():
    results = []
    for phase in CANONICAL_PHASES:
        unit = "PASS" if phase == "IMPLEMENTATION" else "NOT_APPLICABLE"
        results.append({"status": "COMPLETE", "unit_test_status": unit})
        results.append({"result": "PASS", "review_verdict": "PASS", "findings": []})
    results.append({"result": "PASS", "review_verdict": "PASS", "findings": []})
    return results


class LauncherStaticContractTests(unittest.TestCase):
    """The launcher is more than a dependency probe, even without LangGraph."""

    def test_launcher_exposes_an_execution_entry_point(self):
        """The shipped entry point must execute the graph, not just probe the runtime."""
        shim = LAUNCHER.read_text(encoding="utf-8")
        for token in ("run_cli", "def main"):
            self.assertIn(token, shim, f"launcher shim must reference {token!r}")
        engine = LAUNCHER_MODULE.read_text(encoding="utf-8")
        for token in ("build_graph", "argparse", "recursion_limit", "EXIT_CODES"):
            self.assertIn(token, engine, f"shipped launcher must reference {token!r}")

    def test_dependency_probe_is_still_available(self):
        completed = subprocess.run([sys.executable, str(LAUNCHER), "--check-runtime"],
                                   capture_output=True, text=True, timeout=120)
        self.assertIn(completed.returncode, (0, 3))


def _ledger():
    """An explicit process-local ledger.

    These tests run inside one process, so an in-memory port is sufficient -- but it is
    *chosen*, never defaulted: the engine has no port-less mode, because that default is
    what allowed a restart to duplicate an external Task/Dispatch.
    """
    from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
    return InMemoryRuntimeStateStore()


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class LauncherExecutionTests(unittest.TestCase):
    def setUp(self):
        self._ledger_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._ledger_dir.cleanup)

    def launch(self, *args, ledger_dir=None):
        """Run the shipped CLI with an isolated durable ledger.

        The launcher always writes one; pointing it at a per-test directory keeps runs that
        reuse a run_id from recovering each other's settlements.
        """
        env = dict(os.environ)
        env["ORCA_OS40_RUNTIME_STATE_DIR"] = ledger_dir or self._ledger_dir.name
        # OS-31: the checkpoint store is durable by default too, so it is isolated the same
        # way -- otherwise the suite would write artifacts/runs/ into the working tree.
        env["ORCA_OS40_CHECKPOINT_DIR"] = ledger_dir or self._ledger_dir.name
        return subprocess.run([sys.executable, str(LAUNCHER), *args],
                              capture_output=True, text=True, timeout=300, env=env)

    def test_default_recursion_limit_exceeds_the_langgraph_default(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.launcher import default_recursion_limit
        from scripts.deterministic_workflow.state import initial_state
        state = initial_state(run_id="run_limit", thread_id="t", phases=CANONICAL_PHASES,
                              capabilities=BASE_CAPABILITIES)
        limit = default_recursion_limit(state)
        self.assertGreater(limit, 25, "LangGraph's default recursion limit is 25")
        self.assertGreaterEqual(limit, 68, "the canonical 5-phase workflow needs ~68 steps")

    def test_canonical_five_phase_workflow_completes_with_default_limit(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.launcher import execute_state
        from scripts.deterministic_workflow.state import initial_state
        state = initial_state(run_id="run_canonical", thread_id="t", phases=CANONICAL_PHASES,
                              capabilities=BASE_CAPABILITIES)
        adapter = FakeAdapter(canonical_results())
        # OS-31: execute_state is checkpoint-durable by default, so the store is pointed at
        # a per-test directory rather than at artifacts/runs/ in the working tree.
        out = execute_state(state, adapter=adapter, runtime_state=_ledger(),
                            checkpoint_store_path=Path(self._ledger_dir.name) / "cp.json")
        self.assertEqual(out["terminal_status"], "COMPLETED")
        self.assertEqual(adapter.effect_count, 11)

    def test_default_limit_is_used_when_the_caller_supplies_none(self):
        """Without explicit handling LangGraph's 25-step default aborts the run."""
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state
        from langgraph.errors import GraphRecursionError
        state = initial_state(run_id="run_nolimit", thread_id="t", phases=CANONICAL_PHASES,
                              capabilities=BASE_CAPABILITIES)
        with self.assertRaises(GraphRecursionError):
            build_graph(FakeAdapter(canonical_results()), runtime_state=_ledger(), require_durable_checkpointer=False).invoke(state)

    def test_terminal_exit_codes_cover_every_terminal_status(self):
        from scripts.deterministic_workflow.launcher import EXIT_CODES
        self.assertEqual(EXIT_CODES["COMPLETED"], 0)
        # OS-31 adds three: a paused run is not a failure, and cancel/abandon are its two
        # explicit dispositions. Every code stays distinct.
        self.assertEqual(sorted(EXIT_CODES),
                         ["ABANDONED", "BLOCKED", "CANCELLED", "COMPLETED", "ESCALATED",
                          "WAITING_FOR_INPUT"])
        self.assertEqual(len(set(EXIT_CODES.values())), 6)

    def test_demo_scenario_runs_without_orca_and_exits_zero(self):
        completed = self.launch("--demo", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["terminal_status"], "COMPLETED")
        self.assertEqual(payload["requested_phases"], list(CANONICAL_PHASES))
        self.assertEqual(payload["exit_code"], 0)

    def test_cli_runs_a_supplied_state_and_result_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state_path, results_path = tmp / "state.json", tmp / "results.json"
            state_path.write_text(json.dumps({
                "run_id": "run_cli", "thread_id": "cli", "phases": ["ANALYSIS"],
                "risk": "high", "max_iterations": 5}), encoding="utf-8")
            results_path.write_text(json.dumps([
                {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                {"result": "PASS", "review_verdict": "PASS", "findings": []},
                {"result": "PASS", "review_verdict": "PASS", "findings": []}]), encoding="utf-8")
            completed = self.launch("--state", str(state_path), "--results", str(results_path),
                                    "--runtime-state", str(tmp / "rt.json"), "--json")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["terminal_status"], "COMPLETED")
            self.assertTrue((tmp / "rt.json").exists(), "runtime state must be persisted")

    def test_blocked_and_escalated_runs_return_distinct_nonzero_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state_path = tmp / "state.json"
            state_path.write_text(json.dumps({
                "run_id": "run_block", "thread_id": "block", "phases": ["ANALYSIS"],
                "risk": "high", "max_iterations": 1}), encoding="utf-8")
            blocked = tmp / "blocked.json"
            blocked.write_text(json.dumps([
                {"status": "BLOCKED", "unit_test_status": "BLOCKED"}]), encoding="utf-8")
            escalated = tmp / "escalated.json"
            escalated.write_text(json.dumps([
                {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                {"result": "FAIL", "review_verdict": "FAIL",
                 "findings": [{"finding_id": "F", "blocking": True,
                               "responsible_phase": "ANALYSIS", "quality_attribute": "G1",
                               "severity": "MAJOR"}]}]), encoding="utf-8")
            for results_path, expected in ((blocked, "BLOCKED"), (escalated, "ESCALATED")):
                with self.subTest(expected=expected):
                    # A separate ledger per scenario: the two share a run_id, and a shared
                    # ledger would (correctly) replay the first scenario's settlement.
                    scenario_ledger = tmp / f"ledger_{expected.lower()}"
                    scenario_ledger.mkdir()
                    completed = self.launch("--state", str(state_path), "--results",
                                            str(results_path), "--json",
                                            ledger_dir=str(scenario_ledger))
                    payload = json.loads(completed.stdout)
                    self.assertEqual(payload["terminal_status"], expected)
                    self.assertEqual(completed.returncode, payload["exit_code"])
                    self.assertNotEqual(completed.returncode, 0)

    def test_malformed_input_state_exits_blocked_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state_path = tmp / "state.json"
            state_path.write_text(json.dumps({"run_id": "run_bad", "thread_id": "bad",
                                              "phases": ["NOT_A_PHASE"]}), encoding="utf-8")
            completed = self.launch("--state", str(state_path), "--results", str(state_path),
                                    "--json")
            self.assertNotIn("Traceback", completed.stderr)
            self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
