#!/usr/bin/env python3
"""Opt-in integration tests against the installed Orca runtime."""

from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from pathlib import Path

try:
    from scripts.orca_runtime_harness import (
        UnsupportedOrcaContract,
        run_runtime_scenarios,
    )
except ModuleNotFoundError:
    from orca_runtime_harness import UnsupportedOrcaContract, run_runtime_scenarios


RUN_ORCA = os.environ.get("ORCA_RUNTIME_TEST") == "1"
ARTIFACT_DIR = os.environ.get("ORCA_RUNTIME_ARTIFACT_DIR")


class OrcaRuntimeIntegrationTests(unittest.TestCase):
    def test_runtime_scenarios(self) -> None:
        if not RUN_ORCA:
            self.skipTest("requires --orca-runtime and a ready Orca runtime")
        if ARTIFACT_DIR:
            artifact_dir = Path(ARTIFACT_DIR)
            try:
                results = run_runtime_scenarios(artifact_dir)
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))
        else:
            with tempfile.TemporaryDirectory() as directory:
                try:
                    results = run_runtime_scenarios(Path(directory))
                except UnsupportedOrcaContract as exc:
                    self.skipTest(str(exc))

        by_name = {result.scenario: result for result in results}
        self.assertEqual(set(by_name), set("ABCDEF"))

        scenario_a = by_name["A"]
        self.assertEqual(scenario_a.status, "COMPLETED")
        self.assertEqual(len(scenario_a.attempts), 2)
        self.assertIn("question", scenario_a.signals)
        self.assertEqual(scenario_a.signals.count("worker_done"), 2)

        scenario_b = by_name["B"]
        self.assertEqual(scenario_b.status, "COMPLETED")
        self.assertEqual(scenario_b.iteration, 2)
        self.assertEqual(len(scenario_b.attempts), 4)
        self.assertIn("R1", scenario_b.attempts[1].body)
        self.assertIn("R1", scenario_b.attempts[2].body)
        self.assertTrue(
            scenario_b.attempts[1].lifecycle_action.startswith(("retain:", "reuse:"))
        )

        scenario_c = by_name["C"]
        self.assertEqual(scenario_c.status, "ESCALATED")
        self.assertEqual(scenario_c.iteration, 3)
        self.assertEqual(len(scenario_c.attempts), 6)

        scenario_d = by_name["D"]
        self.assertEqual(scenario_d.status, "BLOCKED")
        self.assertEqual(len(scenario_d.attempts), 1)
        self.assertEqual(scenario_d.attempts[0].outcome, "failed")
        self.assertEqual(scenario_d.attempts[0].task_status, "failed")
        self.assertIn("escalation", scenario_d.signals)

        scenario_e = by_name["E"]
        self.assertEqual(len(scenario_e.attempts), 1)
        self.assertEqual(scenario_e.attempts[0].worker_done_count, 0)

        scenario_f = by_name["F"]
        self.assertEqual(len(scenario_f.attempts), 2)
        self.assertEqual(scenario_f.attempts[1].worker_done_count, 0)

        for result in results:
            self.assertTrue(result.run_id.startswith("run_"))
            for attempt in result.attempts:
                self.assertTrue(attempt.task_id.startswith("task_"))
                self.assertTrue(attempt.dispatch_id.startswith("ctx_"))
                if attempt.worker_done_count == 0:
                    self.assertIn(attempt.dispatch_status, {"dispatched", "failed"})
                    self.assertIn("outcome_unknown", attempt.worker_state)
                else:
                    self.assertIn(attempt.dispatch_status, {"completed", "failed"})
                self.assertIn(attempt.task_status, {"completed", "failed", "blocked"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orca-runtime", action="store_true")
    parser.add_argument("--artifact-dir")
    args, unittest_args = parser.parse_known_args()
    if args.orca_runtime:
        os.environ["ORCA_RUNTIME_TEST"] = "1"
        global RUN_ORCA
        RUN_ORCA = True
    if args.artifact_dir:
        os.environ["ORCA_RUNTIME_ARTIFACT_DIR"] = args.artifact_dir
        global ARTIFACT_DIR
        ARTIFACT_DIR = args.artifact_dir
    unittest.main(argv=[__file__, *unittest_args])


if __name__ == "__main__":
    main()
