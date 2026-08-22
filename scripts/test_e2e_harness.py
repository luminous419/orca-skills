#!/usr/bin/env python3
"""Deterministic fake-agent E2E scenarios for the shared workflow policy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.e2e_harness import E2EHarness, FakeScenario, WorkflowResult
from scripts.e2e_harness import (
    FinalReviewScenario,
    WorkflowRunResult,
    WorkflowScenario,
)


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

    def test_pass_with_non_blocking_finding_is_valid(self) -> None:
        scenario = FakeScenario(
            ("complete",),
            ("pass-nonblocking",),
            reviewer_findings=(("R1",),),
        )
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "COMPLETED")
            self.assertEqual(result.findings, {})

    def test_pass_with_blocking_finding_is_malformed(self) -> None:
        scenario = FakeScenario(
            ("complete",),
            ("pass-blocking",),
            reviewer_findings=(("R1",),),
        )
        for result in self.assert_for_both(scenario):
            self.assertEqual(result.final_status, "ERROR")
            self.assertTrue(result.reason.startswith("MALFORMED_REVIEWER_OUTPUT:"))

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

    def test_only_previous_blocking_findings_require_resolution(self) -> None:
        scenario = FakeScenario(
            worker_modes=("complete", "correction", "correction"),
            reviewer_modes=("fail", "fail", "pass"),
            reviewer_findings=(("R1", "R2"), ("R2",), ()),
            worker_resolutions=(
                {},
                {"R1": "RESOLVED", "R2": "DISPUTED"},
                {"R2": "RESOLVED"},
            ),
        )
        for result in self.assert_for_both(scenario, max_iterations=3):
            self.assertEqual(result.final_status, "COMPLETED")
            self.assertEqual(set(result.findings), {"R1", "R2"})
            self.assertEqual(result.findings["R1"].reviewer_iterations, [1])
            self.assertEqual(result.findings["R2"].reviewer_iterations, [1, 2])
            self.assertEqual(result.findings["R1"].resolutions, [(2, "RESOLVED")])
            self.assertEqual(
                result.findings["R2"].resolutions,
                [(2, "DISPUTED"), (3, "RESOLVED")],
            )

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


class FinalAdversarialReviewTests(unittest.TestCase):
    """DESIGN section 7.2: the Final Adversarial Review gate, offline and deterministic.

    Every method drives the real `run_workflow` through the same fake Worker/Reviewer
    subprocesses the phase tests use. `run()` itself is byte-unchanged, so the phase
    gates below are the production single-phase authority, not a re-implementation.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    PASSING_PHASE = FakeScenario(("complete",), ("pass",))
    RESOLUTION_CASES = {
        "missing": {},
        "extra": {"R1": "RESOLVED", "R9": "RESOLVED"},
        "mismatched": {"R9": "RESOLVED"},
    }

    def run_workflow_scenario_with_artifact(
        self,
        scenario: WorkflowScenario,
        *,
        max_iterations: int = 5,
        protect_artifact: bool = False,
        skill_path: Path | None = None,
    ) -> tuple[WorkflowRunResult, str | None]:
        """Mirror of run_scenario for the workflow gate: temp workspace, optional
        protected artifact, one E2EHarness, `run_workflow` instead of `run`."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            artifact = workspace / "production.txt"
            protected: tuple[Path, ...] = ()
            if protect_artifact:
                artifact.write_text("production content\n", encoding="utf-8")
                protected = (artifact,)
            harness = E2EHarness(
                skill_path or self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=max_iterations,
                workspace=workspace,
                protected_artifacts=protected,
            )
            result = harness.run_workflow(scenario)
            final_artifact = (
                artifact.read_text(encoding="utf-8") if protect_artifact else None
            )
            return result, final_artifact

    def run_workflow_scenario(
        self,
        scenario: WorkflowScenario,
        *,
        max_iterations: int = 5,
        protect_artifact: bool = False,
        skill_path: Path | None = None,
    ) -> WorkflowRunResult:
        result, _ = self.run_workflow_scenario_with_artifact(
            scenario,
            max_iterations=max_iterations,
            protect_artifact=protect_artifact,
            skill_path=skill_path,
        )
        return result

    # ---- scenario builders shared by a dedicated method and the H-sweep ----------

    def h1_scenario(self) -> tuple[WorkflowScenario, int]:
        """H1: the responsible phase has already spent its whole budget."""
        return (
            WorkflowScenario(
                phases=("analysis", "implementation"),
                phase_scenarios={
                    "analysis": self.PASSING_PHASE,
                    "implementation": FakeScenario(
                        worker_modes=("complete", "correction"),
                        reviewer_modes=("fail", "pass"),
                        reviewer_findings=(("P1",), ()),
                        worker_resolutions=({}, {"P1": "RESOLVED"}),
                    ),
                },
                final_review=FinalReviewScenario(
                    modes=("fail",),
                    findings=((("R1", "implementation"),),),
                ),
            ),
            2,
        )

    def h2_scenario(self) -> tuple[WorkflowScenario, int]:
        """H2: the Final Review budget runs out while a phase is exhausted too."""
        return (
            WorkflowScenario(
                phases=("analysis", "implementation"),
                phase_scenarios={
                    "analysis": self.PASSING_PHASE,
                    "implementation": self.PASSING_PHASE,
                },
                final_review=FinalReviewScenario(
                    modes=("fail", "fail"),
                    findings=((("R1", "analysis"),), (("R2", "analysis"),)),
                ),
                correction_scenarios={
                    ("analysis", 1): FakeScenario(
                        ("correction",),
                        ("pass",),
                        worker_resolutions=({"R1": "RESOLVED"},),
                    ),
                },
            ),
            2,
        )

    def h3_scenario(self) -> tuple[WorkflowScenario, int]:
        """H3: max-iterations 1, so the very first FAIL is also the last attempt."""
        return (
            WorkflowScenario(
                phases=("analysis", "implementation"),
                phase_scenarios={
                    "analysis": self.PASSING_PHASE,
                    "implementation": self.PASSING_PHASE,
                },
                final_review=FinalReviewScenario(
                    modes=("fail",),
                    findings=((("R1", "implementation"),),),
                ),
            ),
            1,
        )

    def h4_scenario(self) -> tuple[WorkflowScenario, int]:
        """H4: the last-attempt guard fires while another phase still has budget."""
        return (
            WorkflowScenario(
                phases=("analysis", "implementation"),
                phase_scenarios={
                    "analysis": FakeScenario(("complete",), ("pass",)),
                    "implementation": FakeScenario(("complete",), ("pass",)),
                },
                final_review=FinalReviewScenario(
                    modes=("fail", "fail", "fail"),
                    findings=(
                        (("R1", "analysis"),),
                        (("R2", "analysis"),),
                        (("R3", "implementation"),),
                    ),
                ),
                correction_scenarios={
                    ("analysis", 1): FakeScenario(
                        ("correction",),
                        ("pass",),
                        worker_resolutions=({"R1": "RESOLVED"},),
                    ),
                    ("analysis", 2): FakeScenario(
                        ("correction",),
                        ("pass",),
                        worker_resolutions=({"R2": "RESOLVED"},),
                    ),
                },
            ),
            3,
        )

    def unaccounted_resolution_scenario(
        self, emitted: dict[str, str]
    ) -> tuple[WorkflowScenario, int]:
        """R-N: every other guard is satisfied, so only the bridge can stop this run."""
        return (
            WorkflowScenario(
                phases=("analysis", "implementation"),
                phase_scenarios={
                    "analysis": FakeScenario(("complete",), ("pass",)),
                    "implementation": FakeScenario(("complete",), ("pass",)),
                },
                # attempt 2 would PASS -- so ONLY the bridge can stop this run.
                final_review=FinalReviewScenario(
                    modes=("fail", "pass"),
                    findings=((("R1", "implementation"),), ()),
                ),
                correction_scenarios={
                    ("implementation", 1): FakeScenario(
                        ("correction",), ("pass",), worker_resolutions=(emitted,)
                    ),
                },
            ),
            5,
        )

    # ---- 1-2: the gate runs at all -----------------------------------------------

    def test_scenario_a_final_review_runs_after_all_phases_pass(self) -> None:
        phases = ("analysis", "plan", "design", "implementation", "test")
        scenario = WorkflowScenario(
            phases=phases,
            phase_scenarios={phase: self.PASSING_PHASE for phase in phases},
            final_review=FinalReviewScenario(modes=("pass",)),
        )

        result = self.run_workflow_scenario(scenario)

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.final_review_verdict, "PASS")
        self.assertEqual(result.final_review_iterations, 1)
        self.assertEqual(result.phase_iterations, {phase: 1 for phase in phases})
        self.assertEqual(len(result.final_review_attempts), 1)
        self.assertEqual(result.correction_dispatches, [])
        self.assertEqual(
            result.final_review_artifacts,
            ("artifacts/FINAL_REVIEW_final_adversarial_review.md",),
        )

    def test_scenario_a_final_review_runs_after_specialized_single_phase(self) -> None:
        for phase in ("bugfix", "refactoring", "implementation"):
            with self.subTest(phase=phase):
                scenario = WorkflowScenario(
                    phases=(phase,),
                    phase_scenarios={phase: self.PASSING_PHASE},
                    final_review=FinalReviewScenario(modes=("pass",)),
                )

                result = self.run_workflow_scenario(scenario)

                # the gate is not skipped just because a single phase was requested
                self.assertEqual(result.final_status, "COMPLETED")
                self.assertEqual(result.final_review_iterations, 1)
                self.assertEqual(result.final_review_verdict, "PASS")
                self.assertEqual(result.phase_iterations, {phase: 1})

    # ---- 3-5: FAIL, routing, and the correction loop ------------------------------

    def test_scenario_c_final_review_fail_is_not_completed(self) -> None:
        scenario = WorkflowScenario(
            phases=("analysis", "implementation"),
            phase_scenarios={
                "analysis": self.PASSING_PHASE,
                "implementation": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "fail"),
                findings=((("R1", "implementation"),), (("R2", "implementation"),)),
            ),
            correction_scenarios={
                ("implementation", 1): FakeScenario(
                    ("correction",),
                    ("pass",),
                    worker_resolutions=({"R1": "RESOLVED"},),
                ),
            },
        )

        result = self.run_workflow_scenario(scenario, max_iterations=2)

        self.assertNotEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.final_status, "ESCALATED")
        self.assertEqual(result.final_review_verdict, "FAIL")

    def test_scenario_d_finding_maps_to_responsible_phase_only(self) -> None:
        scenario = WorkflowScenario(
            phases=("analysis", "implementation"),
            phase_scenarios={
                "analysis": self.PASSING_PHASE,
                "implementation": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"),
                findings=((("R1", "implementation"),), ()),
            ),
            correction_scenarios={
                ("implementation", 1): FakeScenario(
                    ("correction",),
                    ("pass",),
                    worker_resolutions=({"R1": "RESOLVED"},),
                ),
            },
        )

        result = self.run_workflow_scenario(scenario)

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(
            result.phase_iterations, {"analysis": 1, "implementation": 2}
        )
        self.assertEqual(result.final_review_iterations, 2)
        # the exact DESIGN section 4.6 prompt-input row for attempt 2
        self.assertEqual(
            result.corrected_findings,
            ((1, "R1", "implementation", "RESOLVED"),),
        )
        # the phase that was not responsible was never re-dispatched
        self.assertEqual(result.correction_dispatches, [("implementation", 2)])
        self.assertNotIn(
            "analysis", {phase for phase, _ in result.correction_dispatches}
        )

    def test_scenario_e_multi_round_correction_then_final_review_pass(self) -> None:
        scenario = WorkflowScenario(
            phases=("analysis", "implementation"),
            phase_scenarios={
                "analysis": self.PASSING_PHASE,
                "implementation": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"),
                findings=((("R1", "implementation"),), ()),
            ),
            correction_scenarios={
                # the round itself needs two Reviewer attempts: R1 is the Final Review
                # finding the bridge checks, X1 is the correction Reviewer's own finding
                # that run()'s previous_blocking_findings check enforces.
                ("implementation", 1): FakeScenario(
                    worker_modes=("correction", "correction"),
                    reviewer_modes=("fail", "pass"),
                    reviewer_findings=(("X1",), ()),
                    worker_resolutions=({"R1": "RESOLVED"}, {"X1": "RESOLVED"}),
                ),
            },
        )

        result = self.run_workflow_scenario(scenario)

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.phase_iterations["implementation"], 3)
        self.assertEqual(result.final_review_iterations, 2)
        self.assertEqual(
            result.correction_dispatches,
            [("implementation", 2), ("implementation", 3)],
        )

    # ---- 6-9: the four escalation branches ---------------------------------------

    def test_h1_phase_budget_exhausted_during_final_review_correction(self) -> None:
        scenario, max_iterations = self.h1_scenario()

        result = self.run_workflow_scenario(scenario, max_iterations=max_iterations)

        self.assertEqual(result.final_status, "ESCALATED")
        self.assertEqual(result.reason, "MAX_ITERATIONS_REACHED (implementation)")
        self.assertEqual(result.final_review_iterations, 1)
        # no third IMPLEMENTATION Reviewer, and no second Final Review attempt
        self.assertEqual(result.phase_iterations["implementation"], 2)
        self.assertEqual(result.correction_dispatches, [])
        self.assertEqual(len(result.final_review_attempts), 1)

    def test_h4_last_attempt_guard_fires_while_phase_budget_remains(self) -> None:
        scenario, max_iterations = self.h4_scenario()

        result = self.run_workflow_scenario(scenario, max_iterations=max_iterations)

        self.assertEqual(result.final_status, "ESCALATED")
        self.assertEqual(result.reason, "FINAL_REVIEW_MAX_ITERATIONS_REACHED")
        self.assertEqual(result.final_review_iterations, 3)
        self.assertEqual(result.phase_iterations["analysis"], 3)
        self.assertEqual(result.phase_iterations["implementation"], 1)  # 2 unspent
        # the guard, stated as a negative: the third FAIL named implementation, whose
        # budget was NOT exhausted, and no correction for it was ever dispatched.
        self.assertNotIn(
            "implementation", {phase for phase, _ in result.correction_dispatches}
        )
        self.assertEqual(
            result.correction_dispatches, [("analysis", 2), ("analysis", 3)]
        )

    def test_h2_final_review_budget_exhausted_escalates_without_correction(
        self,
    ) -> None:
        scenario, max_iterations = self.h2_scenario()

        result = self.run_workflow_scenario(scenario, max_iterations=max_iterations)

        self.assertEqual(result.final_status, "ESCALATED")
        self.assertEqual(result.reason, "FINAL_REVIEW_MAX_ITERATIONS_REACHED")
        # the responsible phase is exhausted at the same moment, and the reason is
        # still never the phase one: T2 is evaluated before any phase counter is read.
        self.assertEqual(result.phase_iterations["analysis"], max_iterations)
        self.assertNotEqual(result.reason, "MAX_ITERATIONS_REACHED (analysis)")
        # nothing was dispatched after Final Review attempt 2 failed
        self.assertEqual(result.correction_dispatches, [("analysis", 2)])
        self.assertEqual(result.final_review_iterations, 2)

    def test_h3_max_iterations_one_escalates_on_first_final_review_fail(self) -> None:
        scenario, max_iterations = self.h3_scenario()

        result = self.run_workflow_scenario(scenario, max_iterations=max_iterations)

        self.assertEqual(result.final_status, "ESCALATED")
        self.assertEqual(result.reason, "FINAL_REVIEW_MAX_ITERATIONS_REACHED")
        self.assertEqual(result.final_review_iterations, 1)
        self.assertEqual(result.correction_dispatches, [])

    def test_completed_is_unreachable_from_every_escalation_branch(self) -> None:
        branches = {
            "H1": self.h1_scenario(),
            "H2": self.h2_scenario(),
            "H3": self.h3_scenario(),
            "H4": self.h4_scenario(),
            "R-N": self.unaccounted_resolution_scenario({}),
        }
        for label, (scenario, max_iterations) in branches.items():
            with self.subTest(branch=label):
                result = self.run_workflow_scenario(
                    scenario, max_iterations=max_iterations
                )

                self.assertNotEqual(result.final_status, "COMPLETED")

    # ---- 11-15: routing, verdicts, artifacts, and run() parity --------------------

    def test_out_of_scope_responsible_phase_escalates(self) -> None:
        scenario = WorkflowScenario(
            phases=("implementation",),
            phase_scenarios={"implementation": self.PASSING_PHASE},
            final_review=FinalReviewScenario(
                modes=("fail",),
                findings=((("R1", "refactoring"),),),
            ),
        )

        result = self.run_workflow_scenario(scenario)

        self.assertEqual(result.final_status, "ESCALATED")
        self.assertEqual(result.reason, "OUT_OF_SCOPE_FINAL_REVIEW_FINDING")
        # the requested phase set is never silently widened to absorb the finding
        self.assertEqual(result.correction_dispatches, [])
        self.assertEqual(result.phase_iterations, {"implementation": 1})
        self.assertEqual(result.corrected_findings, ())

    def test_minor_only_final_review_findings_are_a_pass(self) -> None:
        scenario = WorkflowScenario(
            phases=("implementation",),
            phase_scenarios={"implementation": self.PASSING_PHASE},
            final_review=FinalReviewScenario(
                modes=("pass-nonblocking",),
                findings=((("R1", "implementation"),),),
            ),
        )

        result = self.run_workflow_scenario(scenario)

        self.assertEqual(result.final_review_verdict, "PASS")
        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.correction_dispatches, [])
        self.assertEqual(result.corrected_findings, ())

    def test_final_review_does_not_modify_protected_artifacts(self) -> None:
        modifying = WorkflowScenario(
            phases=("implementation",),
            phase_scenarios={"implementation": self.PASSING_PHASE},
            final_review=FinalReviewScenario(
                modes=("fail-modify",),
                findings=((("R1", "implementation"),),),
            ),
        )

        result, _ = self.run_workflow_scenario_with_artifact(
            modifying, max_iterations=1, protect_artifact=True
        )

        self.assertEqual(result.final_status, "ERROR")
        self.assertEqual(result.reason, "REVIEWER_MODIFIED_PROTECTED_ARTIFACT")
        self.assertEqual(result.final_review_attempts, [])

        # and a Final Reviewer that behaves leaves the protected bytes untouched
        behaving = WorkflowScenario(
            phases=("implementation",),
            phase_scenarios={"implementation": self.PASSING_PHASE},
            final_review=FinalReviewScenario(
                modes=("fail",),
                findings=((("R1", "implementation"),),),
            ),
        )

        behaved, artifact = self.run_workflow_scenario_with_artifact(
            behaving, max_iterations=1, protect_artifact=True
        )

        self.assertEqual(artifact, "production content\n")
        self.assertEqual(behaved.final_status, "ESCALATED")

    def test_run_is_unchanged_by_run_workflow(self) -> None:
        phase_scenario = FakeScenario(
            worker_modes=("complete", "correction"),
            reviewer_modes=("fail", "pass"),
            reviewer_findings=(("P1",), ()),
            worker_resolutions=({}, {"P1": "RESOLVED"}),
        )
        escalating = FakeScenario(
            worker_modes=("complete", "correction"),
            reviewer_modes=("fail", "fail"),
            reviewer_findings=(("P1",), ("P1",)),
            worker_resolutions=({}, {"P1": "DISPUTED"}),
        )
        direct = FakeAgentE2ETests()
        for scenario, max_iterations in ((phase_scenario, 5), (escalating, 2)):
            with self.subTest(scenario=scenario):
                expected, _ = direct.run_scenario(
                    self.ORCHESTRATION_SKILL, scenario, max_iterations=max_iterations
                )
                workflow = WorkflowScenario(
                    phases=("implementation",),
                    phase_scenarios={"implementation": scenario},
                    final_review=FinalReviewScenario(modes=("pass",)),
                )

                result = self.run_workflow_scenario(
                    workflow, max_iterations=max_iterations
                )

                self.assertEqual(
                    result.phase_iterations["implementation"],
                    len(expected.reviewer_attempts),
                )
                if expected.final_status == "COMPLETED":
                    self.assertEqual(result.final_status, "COMPLETED")
                    self.assertEqual(result.final_review_iterations, 1)
                else:
                    # a phase that never PASSes propagates run()'s own status and
                    # reason verbatim; the gate is never even opened.
                    self.assertEqual(result.final_status, expected.final_status)
                    self.assertEqual(result.reason, expected.reason)
                    self.assertEqual(result.final_review_iterations, 0)
                    self.assertEqual(result.final_review_attempts, [])

    def test_final_review_artifact_paths_follow_the_attempt_suffix_rule(self) -> None:
        scenario, max_iterations = self.h4_scenario()

        result = self.run_workflow_scenario(scenario, max_iterations=max_iterations)

        self.assertEqual(
            result.final_review_artifacts,
            (
                "artifacts/FINAL_REVIEW_final_adversarial_review.md",
                "artifacts/FINAL_REVIEW_final_adversarial_review_iteration2.md",
                "artifacts/FINAL_REVIEW_final_adversarial_review_iteration3.md",
            ),
        )
        self.assertEqual(
            len(result.final_review_artifacts), result.final_review_iterations
        )
        for path in result.final_review_artifacts:
            self.assertNotIn("_iteration1", path)

    # ---- 16: the R1 bridge witness ------------------------------------------------

    def test_unaccounted_final_review_finding_resolution_cannot_complete(self) -> None:
        for label, emitted in self.RESOLUTION_CASES.items():
            with self.subTest(case=label):
                scenario, max_iterations = self.unaccounted_resolution_scenario(
                    emitted
                )

                result = self.run_workflow_scenario(
                    scenario, max_iterations=max_iterations
                )

                self.assertNotEqual(result.final_status, "COMPLETED")
                self.assertEqual(result.final_status, "ERROR")
                self.assertTrue(
                    result.reason.startswith(
                        "FINAL_REVIEW_RESOLUTION_TRACE_INCOMPLETE (implementation)"
                    ),
                    result.reason,
                )
                # counters: exactly what actually ran is charged, and no more
                self.assertEqual(result.phase_iterations["implementation"], 2)
                self.assertEqual(
                    result.correction_dispatches, [("implementation", 2)]
                )
                # the OTHER counter domain is untouched: no attempt 2 was ever opened,
                # even though the scenario supplies a PASSing one.
                self.assertEqual(result.final_review_iterations, 1)
                self.assertEqual(result.final_review_verdict, "FAIL")
                # and the DECISION P1 table carries no unverified row
                self.assertEqual(result.corrected_findings, ())
