#!/usr/bin/env python3
"""Deterministic fake-agent E2E scenarios for the shared workflow policy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.e2e_harness import E2EHarness, FakeScenario, WorkflowResult


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATHS = (
    REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md",
    REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md",
)


class FakeAgentE2ETests(unittest.TestCase):
    def run_scenario(
        self,
        skill_path: Path,
        scenario: FakeScenario,
        *,
        max_iterations: int = 5,
        protect_artifact: bool = False,
    ) -> tuple[WorkflowResult, str | None]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            artifact = workspace / "production.txt"
            protected = ()
            original = None
            if protect_artifact:
                original = "production content\n"
                artifact.write_text(original, encoding="utf-8")
                protected = (artifact,)
            harness = E2EHarness(
                skill_path,
                phase="implementation",
                max_iterations=max_iterations,
                workspace=workspace,
                protected_artifacts=protected,
            )
            result = harness.run(scenario)
            final_artifact = (
                artifact.read_text(encoding="utf-8") if artifact.exists() else None
            )
            return result, final_artifact if protect_artifact else original

    def assert_for_both(self, scenario: FakeScenario, **kwargs) -> list[WorkflowResult]:
        results = []
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                result, _ = self.run_scenario(skill_path, scenario, **kwargs)
                results.append(result)
        return results

    def test_scenario_a_first_pass_pass(self) -> None:
        scenario = FakeScenario(("complete",), ("pass",))
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "COMPLETED")
            self.assertEqual(result.current_iteration, 1)
            self.assertEqual(len(result.worker_attempts), 1)
            self.assertEqual(len(result.reviewer_attempts), 1)

    def test_scenario_b_fail_then_pass_with_resolution_trace(self) -> None:
        scenario = FakeScenario(
            worker_modes=("complete", "correction"),
            reviewer_modes=("fail", "pass"),
            reviewer_findings=(("R1",), ()),
            worker_resolutions=({}, {"R1": "RESOLVED"}),
        )
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "COMPLETED")
            self.assertEqual(result.current_iteration, 2)
            self.assertEqual(result.findings["R1"].introduced_iteration, 1)
            self.assertEqual(result.findings["R1"].resolutions, [(2, "RESOLVED")])

    def test_scenario_c_max_iteration_escalation_has_no_extra_attempt(self) -> None:
        scenario = FakeScenario(
            worker_modes=("complete", "correction", "correction"),
            reviewer_modes=("fail", "fail", "fail"),
            reviewer_findings=(("R1",), ("R1",), ("R1",)),
            worker_resolutions=(
                {},
                {"R1": "DISPUTED"},
                {"R1": "DISPUTED"},
            ),
        )
        for result in self.assert_for_both(scenario, max_iterations=3):
            self.assertEqual(result.final_status, "ESCALATED")
            self.assertEqual(result.current_iteration, 3)
            self.assertEqual(len(result.worker_attempts), 3)
            self.assertEqual(len(result.reviewer_attempts), 3)
            self.assertEqual(result.reason, "MAX_ITERATIONS_REACHED")

    def test_scenario_d_worker_blocked_skips_reviewer(self) -> None:
        scenario = FakeScenario(("blocked",), ())
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "BLOCKED")
            self.assertEqual(result.reason, "WORKER_BLOCKED")
            self.assertEqual(len(result.worker_attempts), 1)
            self.assertEqual(result.reviewer_attempts, [])

    def test_scenario_e_malformed_worker_is_not_complete(self) -> None:
        scenario = FakeScenario(("malformed",), ())
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "ERROR")
            self.assertTrue(result.reason.startswith("MALFORMED_WORKER_OUTPUT:"))
            self.assertEqual(result.reviewer_attempts, [])

    def test_scenario_f_malformed_reviewer_never_passes(self) -> None:
        for reviewer_mode in ("malformed-missing", "malformed-invalid"):
            scenario = FakeScenario(("complete",), (reviewer_mode,))
            with self.subTest(mode=reviewer_mode):
                for result in self.assert_for_both(scenario):
                    self.assertEqual(result.final_status, "ERROR")
                    self.assertTrue(
                        result.reason.startswith("MALFORMED_REVIEWER_OUTPUT:")
                    )

    def test_scenario_g_worker_unexpected_exit_skips_reviewer(self) -> None:
        scenario = FakeScenario(("exit",), ())
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "ERROR")
            self.assertEqual(result.reason, "WORKER_UNEXPECTED_EXIT:17")
            self.assertEqual(result.worker_attempts, [])
            self.assertEqual(result.reviewer_attempts, [])

    def test_scenario_h_reviewer_unexpected_exit_never_passes(self) -> None:
        scenario = FakeScenario(("complete",), ("exit",))
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "ERROR")
            self.assertEqual(result.reason, "REVIEWER_UNEXPECTED_EXIT:23")
            self.assertEqual(len(result.worker_attempts), 1)
            self.assertEqual(result.reviewer_attempts, [])

    def test_scenario_i_reviewer_fail_does_not_modify_artifact(self) -> None:
        scenario = FakeScenario(
            ("complete",),
            ("fail",),
            reviewer_findings=(("R1",),),
        )
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                result, artifact = self.run_scenario(
                    skill_path,
                    scenario,
                    max_iterations=1,
                    protect_artifact=True,
                )
                self.assertEqual(result.final_status, "ESCALATED")
                self.assertEqual(artifact, "production content\n")
                self.assertEqual(tuple(result.findings), ("R1",))

    def test_scenario_i_reviewer_modification_is_rejected(self) -> None:
        scenario = FakeScenario(
            ("complete",),
            ("fail-modify",),
            reviewer_findings=(("R1",),),
        )
        for result in self.assert_for_both(
            scenario, max_iterations=1, protect_artifact=True
        ):
            self.assertEqual(result.final_status, "ERROR")
            self.assertEqual(result.reason, "REVIEWER_MODIFIED_PROTECTED_ARTIFACT")
            self.assertEqual(result.reviewer_attempts, [])

    def test_scenario_j_finding_identity_continuity(self) -> None:
        scenario = FakeScenario(
            worker_modes=("complete", "correction", "correction"),
            reviewer_modes=("fail", "fail", "pass"),
            reviewer_findings=(("R1", "R2"), ("R1", "R2"), ()),
            worker_resolutions=(
                {},
                {"R1": "RESOLVED", "R2": "DISPUTED"},
                {"R1": "RESOLVED", "R2": "RESOLVED"},
            ),
        )
        for result in self.assert_for_both(scenario, max_iterations=3):
            self.assertEqual(result.final_status, "COMPLETED")
            self.assertEqual(result.current_iteration, 3)
            self.assertEqual(set(result.findings), {"R1", "R2"})
            self.assertEqual(result.findings["R1"].reviewer_iterations, [1, 2])
            self.assertEqual(result.findings["R2"].reviewer_iterations, [1, 2])
            self.assertEqual(
                result.findings["R1"].resolutions,
                [(2, "RESOLVED"), (3, "RESOLVED")],
            )
            self.assertEqual(
                result.findings["R2"].resolutions,
                [(2, "DISPUTED"), (3, "RESOLVED")],
            )

    def test_incomplete_finding_resolution_trace_is_rejected(self) -> None:
        scenario = FakeScenario(
            worker_modes=("complete", "correction"),
            reviewer_modes=("fail", "pass"),
            reviewer_findings=(("R1", "R2"), ()),
            worker_resolutions=({}, {"R1": "RESOLVED"}),
        )
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "ERROR")
            self.assertEqual(result.reason, "FINDING_RESOLUTION_TRACE_INCOMPLETE")
            self.assertEqual(len(result.reviewer_attempts), 1)

    def test_two_skills_have_identical_results_for_shared_scenarios(self) -> None:
        scenarios = (
            (FakeScenario(("complete",), ("pass",)), 5),
            (
                FakeScenario(
                    ("complete", "correction"),
                    ("fail", "pass"),
                    (("R1",), ()),
                    ({}, {"R1": "RESOLVED"}),
                ),
                5,
            ),
            (FakeScenario(("blocked",), ()), 5),
            (FakeScenario(("malformed",), ()), 5),
            (FakeScenario(("exit",), ()), 5),
            (FakeScenario(("complete",), ("malformed-invalid",)), 5),
            (FakeScenario(("complete",), ("exit",)), 5),
        )
        for scenario, max_iterations in scenarios:
            with self.subTest(scenario=scenario):
                results = [
                    self.run_scenario(
                        skill_path, scenario, max_iterations=max_iterations
                    )[0]
                    for skill_path in SKILL_PATHS
                ]
                self.assertEqual(results[0], results[1])


if __name__ == "__main__":
    unittest.main()
