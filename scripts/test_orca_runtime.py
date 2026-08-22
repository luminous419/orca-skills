#!/usr/bin/env python3
"""Opt-in integration tests against the installed Orca runtime."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from scripts.orca_runtime_harness import (
        CLEANUP_AUTHORITY_STATES,
        CLOSE_ELIGIBLE_ROLES,
        NEVER_CLOSE_ROLES,
        TERMINAL_ROLE_CLASSES,
        UNSETTLED_WORKER_STATES,
        WORKER_RESOURCE_OUTCOMES,
        UnsupportedOrcaContract,
        run_final_review_runtime_scenario,
        run_runtime_scenarios,
    )
except ModuleNotFoundError:
    from orca_runtime_harness import (
        CLEANUP_AUTHORITY_STATES,
        CLOSE_ELIGIBLE_ROLES,
        NEVER_CLOSE_ROLES,
        TERMINAL_ROLE_CLASSES,
        UNSETTLED_WORKER_STATES,
        WORKER_RESOURCE_OUTCOMES,
        UnsupportedOrcaContract,
        run_final_review_runtime_scenario,
        run_runtime_scenarios,
    )


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
        self.assertEqual(set(by_name), set("ABCDEFGHI"))

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

        self.assert_scenario_g(by_name["G"])
        self.assert_scenario_h(by_name["H"])
        self.assert_scenario_i(by_name["I"])
        self.assert_placement_ladder(results)

        for result in results:
            self.assertTrue(result.run_id.startswith("run_"))
            for attempt in result.attempts:
                self.assertTrue(attempt.task_id.startswith("task_"))
                self.assertTrue(attempt.dispatch_id.startswith("ctx_"))
                if attempt.worker_done_count == 0:
                    self.assertIn(attempt.dispatch_status, {"dispatched", "failed"})
                    # "ready" is the adopted-then-already-exited worker; the
                    # "_external" suffix is the unsupervised branch's own label.
                    self.assertTrue(
                        attempt.worker_state in UNSETTLED_WORKER_STATES
                        or attempt.worker_state.startswith("outcome_unknown"),
                        f"unsettled dispatch reported {attempt.worker_state}",
                    )
                else:
                    self.assertIn(attempt.dispatch_status, {"completed", "failed"})
                self.assertIn(attempt.task_status, {"completed", "failed", "blocked"})

                self.assertEqual(attempt.finalizations, 1)
                self.assertIn(attempt.worker_resource, set(WORKER_RESOURCE_OUTCOMES))
                self.assertIn(attempt.cleanup_authority, CLEANUP_AUTHORITY_STATES)
                self.assertIn(attempt.terminal_role, TERMINAL_ROLE_CLASSES)

                # Domain assertions alone would accept a wrong-but-valid "authorized".
                # Pin the actual relation between role and authority instead.
                if attempt.terminal_role in NEVER_CLOSE_ROLES:
                    self.assertNotEqual(attempt.cleanup_authority, "authorized")
                if attempt.cleanup_authority == "authorized":
                    self.assertIn(attempt.terminal_role, CLOSE_ELIGIBLE_ROLES)

    def assert_placement_ladder(self, results) -> None:
        """Rung 3's middle step really ran against the installed runtime.

        The offline tests pin the order (create -> tui-idle -> worker-start); this is
        the evidence that the real `terminal wait --for tui-idle` command was accepted
        by Orca rather than only being asserted against a stub.
        """
        observed = set()
        for result in results:
            worker_rows = [
                row
                for row in result.ledger
                if row["role"] != "run_owner_fixture" and row["created_by"]
            ]
            self.assertTrue(worker_rows, f"scenario {result.scenario} placed no worker")
            for row in worker_rows:
                self.assertIn(row["tui_idle"], {"idle", "timeout", "unobserved"})
                observed.add(row["tui_idle"])
            self.assertIn("terminal wait", result.commands_used)
        # a run in which the wait was never even attempted would leave only the
        # never-observed default behind
        self.assertNotEqual(observed, {"unobserved"})

    def assert_scenario_g(self, scenario_g) -> None:
        """Graph-first promotion used the pre-created Reviewer Task, with no override."""
        self.assertEqual(scenario_g.status, "COMPLETED")
        self.assertEqual(len(scenario_g.attempts), 2)

        # (1) the pre-created Reviewer Task became ready without a manual override
        self.assertTrue(scenario_g.reviewer_task_id.startswith("task_"))
        self.assertEqual(scenario_g.reviewer_task_status, "ready")

        # (2) the very Task that was promoted is the one that was dispatched
        reviewer_attempt = scenario_g.attempts[1]
        self.assertEqual(reviewer_attempt.role, "reviewer")
        self.assertEqual(reviewer_attempt.task_id, scenario_g.reviewer_task_id)
        self.assertEqual(reviewer_attempt.task_status, "completed")
        self.assertEqual(reviewer_attempt.outcome, "succeeded")
        self.assertEqual(reviewer_attempt.finalizations, 1)
        self.assertNotEqual(
            reviewer_attempt.dispatch_id, scenario_g.attempts[0].dispatch_id
        )

        # (3) no force-ready command ran anywhere in Run G (real command log)
        self.assertNotIn("orchestration task-update", scenario_g.commands_used)
        self.assertNotIn("task-update:ready", scenario_g.recovery)

    def assert_scenario_h(self, scenario_h) -> None:
        """A dependent created after settlement stays pending; it is never dispatched."""
        self.assertEqual(scenario_h.late_dependent_status, "pending")
        self.assertEqual(scenario_h.attempts[0].task_status, "completed")

    def assert_scenario_i(self, scenario_i) -> None:
        """Self-created is not the same as closable."""
        rows = {row["handle"]: row for row in scenario_i.ledger}

        # I-1: the run-owner fixture is self-created BUT still not authorized
        owner = rows[scenario_i.run_owner_handle]
        self.assertEqual(owner["role"], "run_owner_fixture")
        self.assertEqual(owner["origin"], "self_created")
        self.assertEqual(owner["cleanup_authority"], "not_authorized")
        self.assertEqual(owner["action"], "retained")
        self.assertNotIn("close", owner["policy_commands"])
        # The ledger snapshot above is taken before teardown, so on its own it cannot
        # say whether the teardown path closed this handle. The receipt can: it lists
        # the lifecycle commands seen for the handle immediately BEFORE the close.
        self.assertEqual(
            scenario_i.fixture_teardown["policyCommandsBeforeTeardown"], []
        )
        self.assertEqual(scenario_i.fixture_teardown["close"], "issued")

        # I-2: a simulated coordinator_session row is never authorized either
        simulated = rows["term_simulated"]
        self.assertEqual(simulated["role"], "coordinator_session")
        self.assertEqual(simulated["cleanup_authority"], "not_authorized")
        self.assertEqual(simulated["action"], "retained")

        # I-3: the self-handle guard refuses rather than closes
        self.assertIn(
            scenario_i.fixture_teardown["selfHandleProbe"], {"refused", "unset"}
        )
        self.assertEqual(scenario_i.fixture_teardown["role"], "run_owner_fixture")
        self.assertIn(
            scenario_i.fixture_teardown["selfHandleGuard"], {"passed", "unset"}
        )


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


class FinalReviewRuntimeIntegrationTests(unittest.TestCase):
    """Opt-in scenario J: Final Adversarial Review terminal freshness, real runtime.

    Skipped unless ORCA_RUNTIME_TEST=1, exactly like OrcaRuntimeIntegrationTests.
    Scenario J is deliberately outside run_runtime_scenarios(), whose A-I result set
    is pinned by an exact-set assertion above.
    """

    FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = frozenset(
        {"retain", "release", "unsupervised"}
    )

    def test_final_review_terminal_freshness(self) -> None:
        if not RUN_ORCA:
            self.skipTest("requires --orca-runtime and a ready Orca runtime")
        if ARTIFACT_DIR:
            artifact_dir = Path(ARTIFACT_DIR)
            try:
                result = run_final_review_runtime_scenario(artifact_dir)
            except UnsupportedOrcaContract as exc:
                self.skipTest(str(exc))
            self.assert_scenario_j(result, artifact_dir)
        else:
            with tempfile.TemporaryDirectory() as directory:
                artifact_dir = Path(directory)
                try:
                    result = run_final_review_runtime_scenario(artifact_dir)
                except UnsupportedOrcaContract as exc:
                    self.skipTest(str(exc))
                self.assert_scenario_j(result, artifact_dir)

    def assert_scenario_j(self, result, artifact_dir: Path) -> None:
        # J-1: the scenario ran to completion
        self.assertEqual(result.scenario, "J")
        self.assertEqual(result.status, "COMPLETED")

        # J-2: one brand-new terminal per Final Review attempt
        self.assertEqual(len(result.final_review_terminals), 2)
        self.assertNotEqual(
            result.final_review_terminals[0], result.final_review_terminals[1]
        )

        # J-3: and none of them is a phase Reviewer's terminal
        self.assertTrue(
            set(result.final_review_terminals).isdisjoint(
                result.phase_reviewer_terminals
            )
        )

        # J-4/J-5: axis (b) is never reuse, and each Dispatch finalizes exactly once
        final_review_attempts = [result.attempts[2], result.attempts[4]]
        for attempt in final_review_attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertIn(
                    attempt.worker_resource,
                    self.FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES,
                )
                self.assertNotEqual(attempt.worker_resource, "reuse")
                self.assertIn(attempt.worker_resource, set(WORKER_RESOURCE_OUTCOMES))
                self.assertEqual(attempt.finalizations, 1)
                self.assertIn(attempt.terminal_role, TERMINAL_ROLE_CLASSES)

        # J-6: the receipt on disk carries the same two distinct handles
        snapshot_path = artifact_dir / "scenario-j.json"
        self.assertTrue(snapshot_path.is_file(), snapshot_path)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        recorded = snapshot["result"]["final_review_terminals"]
        self.assertEqual(recorded, result.final_review_terminals)
        self.assertEqual(len(set(recorded)), 2)


if __name__ == "__main__":
    main()
