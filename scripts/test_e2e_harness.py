#!/usr/bin/env python3
"""Deterministic fake-agent E2E scenarios for the shared workflow policy."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.e2e_harness import E2EHarness, FakeScenario, WorkflowResult
from scripts.e2e_harness import (
    FinalReviewScenario,
    SESSION_AGENT_COMMANDS,
    SessionEvent,
    WorkflowRunResult,
    WorkflowScenario,
    downstream_revalidation_set,
)
from scripts.quality_profile import DEFAULT_PROFILE_PATH, PROFILE_STATUS_LOADED
from scripts.task_context import (
    BOUNDARY_RECEIPT_HEADING,
    BOUNDARY_RECEIPT_PREFIX,
    QUALITY_GATE_KEYS,
    QUALITY_GATE_RECEIPT_KEY,
    REVIEWER_CONTEXT_KEYS,
    REVIEWER_CONTEXT_RECEIPT_KEY,
    SPEC_VALUE_SEPARATOR,
    TASK_BOUNDARY_KEYS,
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

class SessionStateMachineTests(unittest.TestCase):
    """DESIGN section 7.1 C-1: S-R0..S-R7, called directly.

    allocate_session()/invalidate_session() are the whole policy, so they get unit
    tests that do not go through run(): a rule that only ever runs inside a workflow
    is a rule whose boundaries nobody has looked at.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )

    def harness(self, *, session_policy: str = "reuse") -> E2EHarness:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        return E2EHarness(
            self.ORCHESTRATION_SKILL,
            phase="implementation",
            max_iterations=5,
            workspace=Path(temporary_directory.name),
            session_policy=session_policy,
        )

    def test_final_review_always_gets_a_fresh_session_and_leaves_no_chain(self) -> None:
        """S-R1: section 17's freshness rule, above the policy rather than inside it."""
        harness = self.harness()

        first_id, first_created = harness.allocate_session(
            "final_review", "final_review", 1, policy="reuse"
        )
        second_id, second_created = harness.allocate_session(
            "final_review", "final_review", 2, policy="reuse"
        )

        self.assertTrue(first_created)
        self.assertTrue(second_created)
        self.assertNotEqual(first_id, second_id)
        # ... and nothing was remembered, so no later round can pick the chain up
        self.assertNotIn("final_review", harness._session_ids)

    def test_a_fresh_policy_allocates_a_new_session_every_round(self) -> None:
        """S-R2: the fallback is today's one-terminal-per-attempt behaviour."""
        harness = self.harness(session_policy="fresh")

        allocations = [
            harness.allocate_session("worker", "implementation", round_, policy="fresh")
            for round_ in (1, 2, 3)
        ]

        self.assertTrue(all(created for _, created in allocations))
        self.assertEqual(len({session_id for session_id, _ in allocations}), 3)

    def test_the_first_allocation_of_a_role_is_always_a_creation(self) -> None:
        """S-R3: the boundary case -- there is no chain to continue yet."""
        harness = self.harness()

        for role in ("worker", "reviewer"):
            with self.subTest(role=role):
                self.assertNotIn(role, harness._session_ids)
                session_id, created = harness.allocate_session(
                    role, "implementation", 1, policy="reuse"
                )
                self.assertTrue(created)
                self.assertEqual(harness._session_ids[role], session_id)

    def test_a_reuse_policy_hands_the_same_session_to_the_next_same_role_round(
        self,
    ) -> None:
        """S-R4: the second attempt is where reuse first actually happens."""
        harness = self.harness()

        first_id, first_created = harness.allocate_session(
            "worker", "implementation", 1, policy="reuse"
        )
        second_id, second_created = harness.allocate_session(
            "worker", "implementation", 2, policy="reuse"
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first_id, second_id)

    def test_worker_and_reviewer_session_ids_can_never_collide(self) -> None:
        """S-R5: the chains are keyed by role, so a swap cannot be a reuse."""
        harness = self.harness()

        worker_ids = {
            harness.allocate_session("worker", "implementation", round_, policy="reuse")[0]
            for round_ in (1, 2, 3)
        }
        reviewer_ids = {
            harness.allocate_session("reviewer", "implementation", round_, policy="reuse")[0]
            for round_ in (1, 2, 3)
        }

        self.assertEqual(len(worker_ids), 1)
        self.assertEqual(len(reviewer_ids), 1)
        self.assertTrue(worker_ids.isdisjoint(reviewer_ids))

    def test_correction_and_revalidation_rounds_continue_the_same_chain(self) -> None:
        """S-R6: the chain keys on role alone, so a phase change does not break it."""
        harness = self.harness()

        initial, _ = harness.allocate_session("worker", "design", 1, policy="reuse")
        correction, correction_created = harness.allocate_session(
            "worker", "design", 2, policy="reuse"
        )
        revalidation, revalidation_created = harness.allocate_session(
            "worker", "implementation", 1, policy="reuse"
        )

        self.assertEqual({initial, correction, revalidation}, {initial})
        self.assertFalse(correction_created)
        self.assertFalse(revalidation_created)

    def test_a_failed_round_invalidates_that_roles_session(self) -> None:
        """S-R7: a round that did not PASS leaves that role in recovery."""
        harness = self.harness()
        worker_id, _ = harness.allocate_session("worker", "design", 1, policy="reuse")
        reviewer_id, _ = harness.allocate_session("reviewer", "design", 1, policy="reuse")

        harness.invalidate_session("worker")

        next_worker_id, next_created = harness.allocate_session(
            "worker", "design", 2, policy="reuse"
        )
        next_reviewer_id, next_reviewer_created = harness.allocate_session(
            "reviewer", "design", 2, policy="reuse"
        )

        self.assertTrue(next_created)
        self.assertNotEqual(next_worker_id, worker_id)
        # the other role's chain is untouched: invalidation is per role
        self.assertFalse(next_reviewer_created)
        self.assertEqual(next_reviewer_id, reviewer_id)


class SessionLedgerTests(unittest.TestCase):
    """DESIGN section 7.1 C-2: where the recorded events end up.

    The three mutable session attributes are shared BY REFERENCE with every
    _phase_harness() clone; if any of them were rebound per clone the events a phase
    recorded would vanish when that phase returned.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    PASSING_PHASE = FakeScenario(("complete",), ("pass",))

    def harness(self, *, session_policy: str = "reuse") -> E2EHarness:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        return E2EHarness(
            self.ORCHESTRATION_SKILL,
            phase="design",
            max_iterations=5,
            workspace=Path(temporary_directory.name),
            session_policy=session_policy,
        )

    def test_sessions_accumulate_across_phase_clones(self) -> None:
        parent = self.harness()

        parent._record_session("worker", 1)
        clone = parent._phase_harness("implementation", 3)
        clone._record_session("worker", 1)

        self.assertIs(parent.sessions, clone.sessions)
        self.assertEqual(len(parent.sessions), 2)
        self.assertEqual([event.phase for event in parent.sessions], ["design", "implementation"])
        # the id chain survives the clone boundary too, which is the point of S-R6
        self.assertEqual(len({event.session_id for event in parent.sessions}), 1)
        self.assertEqual([event.created for event in parent.sessions], [True, False])

    def test_workflow_run_result_carries_role_events_in_order(self) -> None:
        scenario = WorkflowScenario(
            phases=("design", "implementation"),
            phase_scenarios={
                "design": self.PASSING_PHASE,
                "implementation": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(modes=("pass",)),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            harness = E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=5,
                workspace=Path(temporary_directory),
            )
            result = harness.run_workflow(scenario)

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(
            [event.role for event in result.sessions],
            ["worker", "reviewer", "worker", "reviewer", "final_review"],
        )
        self.assertEqual(
            [event.phase for event in result.sessions],
            ["design", "design", "implementation", "implementation", "implementation"],
        )
        self.assertEqual(
            [event.agent_command for event in result.sessions],
            [
                SESSION_AGENT_COMMANDS["worker"],
                SESSION_AGENT_COMMANDS["reviewer"],
                SESSION_AGENT_COMMANDS["worker"],
                SESSION_AGENT_COMMANDS["reviewer"],
                SESSION_AGENT_COMMANDS["final_review"],
            ],
        )


class WorkflowSessionPolicyTests(unittest.TestCase):
    """DESIGN section 7.1 C-3: the policy actually reaches allocation.

    These drive `run_workflow` and never call allocate_session() themselves -- that
    is the whole evidence that scenario -> E2EHarness.session_policy -> every
    _phase_harness clone -> _record_session is really wired, and not just declared.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    PASSING_PHASE = FakeScenario(("complete",), ("pass",))

    def correction_and_revalidation_scenario(
        self, *, session_policy: str = "reuse"
    ) -> WorkflowScenario:
        """DESIGN -> FAIL at final review -> DESIGN correction -> IMPLEMENTATION revalidation."""
        return WorkflowScenario(
            phases=("design", "implementation"),
            phase_scenarios={
                "design": self.PASSING_PHASE,
                "implementation": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"),
                findings=((("R1", "design"),), ()),
            ),
            correction_scenarios={
                ("design", 1): FakeScenario(
                    ("correction",),
                    ("pass",),
                    worker_resolutions=({"R1": "RESOLVED"},),
                ),
            },
            revalidation_scenarios={("implementation", 1): self.PASSING_PHASE},
            session_policy=session_policy,
        )

    def run_workflow_scenario(self, scenario: WorkflowScenario) -> WorkflowRunResult:
        with tempfile.TemporaryDirectory() as temporary_directory:
            harness = E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=5,
                workspace=Path(temporary_directory),
            )
            return harness.run_workflow(scenario)

    def test_a_reuse_workflow_keeps_one_session_per_role_across_correction_and_revalidation(
        self,
    ) -> None:
        result = self.run_workflow_scenario(self.correction_and_revalidation_scenario())

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.correction_dispatches, [("design", 2)])
        self.assertEqual(result.revalidation_dispatches, [("implementation", 2)])

        for role in ("worker", "reviewer"):
            events = [event for event in result.sessions if event.role == role]
            with self.subTest(role=role):
                self.assertGreater(len(events), 2)
                self.assertEqual(len({event.session_id for event in events}), 1)
                self.assertEqual(
                    sum(1 for event in events if event.created), 1
                )

        final_review_events = [
            event for event in result.sessions if event.role == "final_review"
        ]
        self.assertEqual(len(final_review_events), 2)
        self.assertTrue(all(event.created for event in final_review_events))
        chained_ids = {
            event.session_id
            for event in result.sessions
            if event.role in {"worker", "reviewer"}
        }
        self.assertTrue(
            {event.session_id for event in final_review_events}.isdisjoint(chained_ids)
        )

    def test_the_same_workflow_with_a_fresh_policy_allocates_a_session_per_attempt(
        self,
    ) -> None:
        result = self.run_workflow_scenario(
            self.correction_and_revalidation_scenario(session_policy="fresh")
        )

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertTrue(all(event.created for event in result.sessions))
        self.assertEqual(
            len({event.session_id for event in result.sessions}),
            len(result.sessions),
        )

    def test_an_invalid_session_policy_is_refused_before_the_first_phase_clone(
        self,
    ) -> None:
        result = self.run_workflow_scenario(
            self.correction_and_revalidation_scenario(session_policy="bogus")
        )

        self.assertEqual(result.final_status, "ERROR")
        self.assertEqual(result.reason, "SCENARIO_SESSION_POLICY_INVALID:bogus")
        # nothing ran, so nothing was recorded: the counters are still at their
        # pre-seeded zeros and not one session was allocated.
        self.assertEqual(result.sessions, ())
        self.assertEqual(set(result.phase_iterations.values()), {0})

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
                # T5a: correcting ANALYSIS during Final Review attempt 1 puts the
                # requested IMPLEMENTATION phase downstream, so the revalidation round
                # is mandatory. The key's iteration is the `final_review_iterations`
                # value at the moment the correction ran, i.e. attempt 1. Supplying it
                # keeps H2's witness intact: ANALYSIS still reaches the phase bound at
                # the same moment the Final Review bound is reached.
                revalidation_scenarios={("implementation", 1): self.PASSING_PHASE},
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
        """H4: the last-attempt guard fires while another phase still has budget.

        T5a rewrite (DESIGN section 4.3): the corrected phase is now `implementation`,
        the LAST requested phase, so `D == ()` and the revalidation loop never spends
        the fresh `analysis` budget. Correcting `analysis` here -- the pre-T5a setup --
        would revalidate `implementation` and destroy the very "fresh budget" this
        guard is supposed to beat.
        """
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
                        (("R1", "implementation"),),
                        (("R2", "implementation"),),
                        (("R3", "analysis"),),
                    ),
                ),
                correction_scenarios={
                    ("implementation", 1): FakeScenario(
                        ("correction",),
                        ("pass",),
                        worker_resolutions=({"R1": "RESOLVED"},),
                    ),
                    ("implementation", 2): FakeScenario(
                        ("correction",),
                        ("pass",),
                        worker_resolutions=({"R2": "RESOLVED"},),
                    ),
                },
            ),
            3,
        )

    def v1_scenario(self) -> tuple[WorkflowScenario, int]:
        """V1: the PR #11 human reviewer's own example -- DESIGN section 4.3.

        Five requested phases, the Final Review corrects DESIGN, and the two requested
        phases after DESIGN must be re-run before a fresh attempt may open.
        """
        phases = ("analysis", "plan", "design", "implementation", "test")
        return (
            WorkflowScenario(
                phases=phases,
                phase_scenarios={phase: self.PASSING_PHASE for phase in phases},
                final_review=FinalReviewScenario(
                    modes=("fail", "pass"),
                    findings=((("R1", "design"),), ()),
                ),
                correction_scenarios={
                    ("design", 1): FakeScenario(
                        ("correction",),
                        ("pass",),
                        worker_resolutions=({"R1": "RESOLVED"},),
                    ),
                },
                revalidation_scenarios={
                    ("implementation", 1): self.PASSING_PHASE,
                    ("test", 1): self.PASSING_PHASE,
                },
            ),
            5,
        )

    def v4_scenario(self) -> tuple[WorkflowScenario, int]:
        """V4: the revalidation round exhausts the downstream phase's own budget."""
        return (
            WorkflowScenario(
                phases=("design", "implementation"),
                phase_scenarios={
                    "design": self.PASSING_PHASE,
                    "implementation": self.PASSING_PHASE,
                },
                final_review=FinalReviewScenario(
                    modes=("fail", "pass"),
                    findings=((("R1", "design"),), ()),
                ),
                correction_scenarios={
                    ("design", 1): FakeScenario(
                        ("correction",),
                        ("pass",),
                        worker_resolutions=({"R1": "RESOLVED"},),
                    ),
                },
                revalidation_scenarios={
                    # budget is max_iterations - 1 == 1, and this Reviewer FAILs, so the
                    # revalidation round exhausts the phase without ever PASSing.
                    ("implementation", 1): FakeScenario(
                        ("complete",),
                        ("fail",),
                        reviewer_findings=(("Q1",),),
                    ),
                },
            ),
            2,
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
            ("artifacts/runs/run_e2e_final_adversarial_review/FINAL_REVIEW.md",),
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
        self.assertEqual(result.phase_iterations["implementation"], 3)
        self.assertEqual(result.phase_iterations["analysis"], 1)  # 2 unspent
        # the guard, stated as a negative: the third FAIL named analysis, whose
        # budget was NOT exhausted, and no correction for it was ever dispatched.
        self.assertNotIn(
            "analysis", {phase for phase, _ in result.correction_dispatches}
        )
        self.assertEqual(
            result.correction_dispatches,
            [("implementation", 2), ("implementation", 3)],
        )
        # and T5a genuinely had nothing to do: implementation is the last requested
        # phase, so D == () and the fresh analysis budget was never touched.
        self.assertEqual(result.revalidation_dispatches, [])

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
        # T5a ran once, for the one requested phase downstream of ANALYSIS, and the
        # escalation still reports the Final Review reason rather than that phase's.
        self.assertEqual(result.revalidation_dispatches, [("implementation", 2)])

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
            # T5a's own escalation edge: a downstream revalidation that exhausts the
            # phase budget must be just as unable to reach COMPLETED as the five above.
            "V4": self.v4_scenario(),
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
                "artifacts/runs/run_e2e_final_adversarial_review/FINAL_REVIEW.md",
                "artifacts/runs/run_e2e_final_adversarial_review/"
                "FINAL_REVIEW_iteration2.md",
                "artifacts/runs/run_e2e_final_adversarial_review/"
                "FINAL_REVIEW_iteration3.md",
            ),
        )
        self.assertEqual(
            len(result.final_review_artifacts), result.final_review_iterations
        )
        for path in result.final_review_artifacts:
            self.assertNotIn("_iteration1", path)
            self.assertTrue(
                path.startswith("artifacts/runs/run_e2e_final_adversarial_review/")
            )

    def test_two_runs_of_the_same_scenario_never_share_an_artifact_path(self) -> None:
        """Run-level isolation, end to end: same scenario, two run_ids, no overlap.

        h4_scenario is the richest fixture already in this suite (3 Final Review
        attempts, a correction round, a downstream revalidation): running it twice
        under different run_ids and diffing every artifact path either run's sessions
        reference is a stronger witness than comparing final_review_artifacts alone.
        """
        scenario, max_iterations = self.h4_scenario()

        result_a = self.run_workflow_scenario(
            replace(scenario, run_id="run_a"), max_iterations=max_iterations
        )
        result_b = self.run_workflow_scenario(
            replace(scenario, run_id="run_b"), max_iterations=max_iterations
        )

        self.assertEqual(
            set(result_a.final_review_artifacts) & set(result_b.final_review_artifacts),
            set(),
        )
        for path in result_a.final_review_artifacts:
            self.assertTrue(path.startswith("artifacts/runs/run_a/"))
        for path in result_b.final_review_artifacts:
            self.assertTrue(path.startswith("artifacts/runs/run_b/"))

        def artifact_contracts(result: WorkflowRunResult) -> set[str]:
            return {
                value
                for event in result.sessions
                for key, value in event.task_boundary
                if key == "artifact_contract"
            }

        contracts_a, contracts_b = artifact_contracts(result_a), artifact_contracts(result_b)
        self.assertTrue(contracts_a)
        self.assertEqual(contracts_a & contracts_b, set())
        for path in contracts_a:
            self.assertTrue(path.startswith("artifacts/runs/run_a/"))
        for path in contracts_b:
            self.assertTrue(path.startswith("artifacts/runs/run_b/"))

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

    # ---- 17-22: T5a downstream revalidation (PR #11 human review, MAJOR 1) --------

    def test_downstream_phases_are_revalidated_after_an_upstream_correction(
        self,
    ) -> None:
        """V1 -- THE witness for MAJOR 1.

        Against the pre-T5a harness `revalidation_dispatches` does not exist at all,
        and even after a "declare the field but never populate it" patch this method
        still fails twice over: on `revalidation_dispatches == [...]` and on
        `phase_iterations`, whose implementation/test entries would remain 1 -- the
        literal stale-PASS evidence the human reviewer described.
        """
        scenario, max_iterations = self.v1_scenario()

        result = self.run_workflow_scenario(scenario, max_iterations=max_iterations)

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.final_review_iterations, 2)
        self.assertEqual(result.correction_dispatches, [("design", 2)])
        # THE assertion MAJOR 1 asks for: the two requested phases after DESIGN were
        # actually re-dispatched, in canonical order, before the fresh Final Review.
        self.assertEqual(
            result.revalidation_dispatches, [("implementation", 2), ("test", 2)]
        )
        self.assertEqual(
            result.phase_iterations,
            {"analysis": 1, "plan": 1, "design": 2, "implementation": 2, "test": 2},
        )
        # upstream phases are untouched
        self.assertNotIn(
            "analysis", {phase for phase, _ in result.revalidation_dispatches}
        )
        self.assertNotIn(
            "plan", {phase for phase, _ in result.revalidation_dispatches}
        )

    def test_downstream_revalidation_is_empty_when_no_requested_phase_follows(
        self,
    ) -> None:
        """V2: the specialized-phase and last-requested-phase carve-outs."""
        for phase in ("bugfix", "refactoring", "implementation"):
            with self.subTest(phase=phase):
                scenario = WorkflowScenario(
                    phases=(phase,),
                    phase_scenarios={phase: self.PASSING_PHASE},
                    final_review=FinalReviewScenario(
                        modes=("fail", "pass"),
                        findings=((("R1", phase),), ()),
                    ),
                    correction_scenarios={
                        (phase, 1): FakeScenario(
                            ("correction",),
                            ("pass",),
                            worker_resolutions=({"R1": "RESOLVED"},),
                        ),
                    },
                )

                result = self.run_workflow_scenario(scenario, max_iterations=5)

                self.assertEqual(result.final_status, "COMPLETED")
                self.assertEqual(result.final_review_iterations, 2)
                # no revalidation scenario is supplied at all, so a non-empty D would
                # additionally surface as ERROR/SCENARIO_REVALIDATION_MISSING here.
                self.assertEqual(result.revalidation_dispatches, [])

    def test_downstream_revalidation_fail_loop_then_pass(self) -> None:
        """V3: section 12's FAIL Loop applies inside a revalidation round unchanged."""
        scenario = WorkflowScenario(
            phases=("design", "implementation"),
            phase_scenarios={
                "design": self.PASSING_PHASE,
                "implementation": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"),
                findings=((("R1", "design"),), ()),
            ),
            correction_scenarios={
                ("design", 1): FakeScenario(
                    ("correction",),
                    ("pass",),
                    worker_resolutions=({"R1": "RESOLVED"},),
                ),
            },
            revalidation_scenarios={
                ("implementation", 1): FakeScenario(
                    worker_modes=("complete", "correction"),
                    reviewer_modes=("fail", "pass"),
                    reviewer_findings=(("Q1",), ()),
                    worker_resolutions=({}, {"Q1": "RESOLVED"}),
                ),
            },
        )

        result = self.run_workflow_scenario(scenario, max_iterations=5)

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(
            result.revalidation_dispatches,
            [("implementation", 2), ("implementation", 3)],
        )
        self.assertEqual(result.phase_iterations["implementation"], 3)
        # not escalated merely because the revalidation needed two rounds
        self.assertNotEqual(result.final_status, "ESCALATED")
        self.assertEqual(result.final_review_iterations, 2)

    def test_downstream_revalidation_budget_exhaustion_escalates(self) -> None:
        """V4: T5a's escalation edge reuses T4's reason literal, verbatim."""
        scenario, max_iterations = self.v4_scenario()

        result = self.run_workflow_scenario(scenario, max_iterations=max_iterations)

        self.assertEqual(result.final_status, "ESCALATED")
        # the SAME literal T4 escalates with -- T5a introduces no new REASON
        self.assertEqual(result.reason, "MAX_ITERATIONS_REACHED (implementation)")
        self.assertEqual(result.revalidation_dispatches, [("implementation", 2)])
        # no fresh Final Review attempt was opened after a failed revalidation
        self.assertEqual(result.final_review_iterations, 1)
        self.assertEqual(len(result.final_review_attempts), 1)

    def test_downstream_revalidation_set_is_the_suffix_after_the_earliest_corrected_phase(
        self,
    ) -> None:
        """Unit test of `downstream_revalidation_set` itself -- DESIGN section 3.2.2."""
        cases = {
            "single canonical mid-run": (
                ("design",),
                ("analysis", "plan", "design", "implementation", "test"),
                ("implementation", "test"),
            ),
            # V5: the later corrected phase is downstream of the earlier one, so the
            # EARLIEST wins and `test` is corrected AND then revalidated again.
            "V5 two corrected, later is downstream": (
                ("design", "test"),
                ("analysis", "design", "implementation", "test"),
                ("implementation", "test"),
            ),
            "specialized-only corrected set": (
                ("bugfix", "refactoring"),
                ("bugfix", "refactoring"),
                (),
            ),
            "corrected phase is the last requested": (
                ("implementation",),
                ("analysis", "implementation"),
                (),
            ),
            "non-contiguous requested set": (
                ("analysis",),
                ("analysis", "test"),
                ("test",),
            ),
        }
        for label, (corrected, requested, expected) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(
                    downstream_revalidation_set(corrected, requested), expected
                )

        # negative: the result is ordered by CANONICAL_PHASES, never by `requested`,
        # so a caller that passes `phases=` out of canonical order gets the SAME tuple.
        self.assertEqual(
            downstream_revalidation_set(
                ("design",), ("test", "implementation", "design", "analysis")
            ),
            ("implementation", "test"),
        )

    def test_downstream_revalidation_adds_no_corrected_findings_row(self) -> None:
        """V1 again, at the T5a / DECISION-P1 boundary (risk D-17's witness).

        A revalidation round is not a correction: it resolves no finding, so it must
        contribute no row to the table that feeds the next attempt's prompt -- and so
        T5a must call `_phase_harness(...).run(...)`, never `_run_correction_round`.

        Two revalidation Workers are exercised against the same V1 shape. The silent
        one proves the row set stays at T4's single row. The one that VOLUNTEERS a
        resolution trace is the behavioural detector for the risk D-17 variant the
        silent case cannot see: DESIGN section 3.2.4a Q2 says a revalidation Worker is
        handed UPSTREAM_CORRECTION, not PREVIOUS_REVIEW_FINDINGS, and "no resolution
        trace is demanded of its first Worker". Routing T5a through
        `_run_correction_round(phase, budget, revalidation, frozenset())` passes
        `emitted == set()` silently for a Worker that emits nothing, but a Worker that
        emits ANY resolution then trips the bridge with
        `FINAL_REVIEW_RESOLUTION_TRACE_INCOMPLETE (implementation): missing=[]
        extra=['R1']`. No mock, no spy -- only observable harness behaviour.
        """
        reporting_revalidation = FakeScenario(
            ("complete",), ("pass",), worker_resolutions=({"R1": "RESOLVED"},)
        )
        for label, revalidation in (
            ("silent revalidation Worker", self.PASSING_PHASE),
            ("revalidation Worker volunteers a resolution", reporting_revalidation),
        ):
            with self.subTest(revalidation=label):
                base, max_iterations = self.v1_scenario()
                # WorkflowScenario is frozen: swap only the revalidation fixtures,
                # so both cases share V1's phases, gates, and DESIGN correction.
                scenario = replace(
                    base,
                    revalidation_scenarios={
                        ("implementation", 1): revalidation,
                        ("test", 1): revalidation,
                    },
                )

                result = self.run_workflow_scenario(
                    scenario, max_iterations=max_iterations
                )

                self.assertNotEqual(result.final_status, "ERROR")
                self.assertEqual(result.final_status, "COMPLETED")
                self.assertIsNone(result.reason)
                # only T4's own row survives: the resolutions a revalidation Worker
                # volunteers are never promoted into the DECISION P1 table.
                self.assertEqual(
                    result.corrected_findings, ((1, "R1", "design", "RESOLVED"),)
                )
                self.assertNotIn(
                    "implementation",
                    {phase for _, _, phase, _ in result.corrected_findings},
                )
                self.assertNotIn(
                    "test", {phase for _, _, phase, _ in result.corrected_findings}
                )
                # the revalidations demonstrably DID happen -- so the empty row set
                # above is the T5a/T4 boundary and not merely a run in which T5a
                # never fired.
                self.assertEqual(
                    result.revalidation_dispatches,
                    [("implementation", 2), ("test", 2)],
                )


class SessionRecordingTests(unittest.TestCase):
    """W-30..W-35: every agent invocation is recorded with the session it ran in.

    The minimum this IMPLEMENTATION owes: one positive that the events are recorded
    with the layer-1 boundary and the delta-first Reviewer keys, and the two structural
    properties the state machine exists for (Worker session != Reviewer session, and a
    reused chain creates its session exactly once).
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )

    def run_phase(
        self, scenario: FakeScenario, *, session_policy: str = "reuse"
    ) -> WorkflowResult:
        with tempfile.TemporaryDirectory() as temporary_directory:
            harness = E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=5,
                workspace=Path(temporary_directory),
                session_policy=session_policy,
            )
            return harness.run(scenario)

    def test_one_pass_round_records_a_worker_and_a_reviewer_session(self) -> None:
        result = self.run_phase(FakeScenario(("complete",), ("pass",)))

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual([event.role for event in result.sessions], ["worker", "reviewer"])
        worker_event, reviewer_event = result.sessions
        self.assertIsInstance(worker_event, SessionEvent)
        self.assertTrue(worker_event.created and reviewer_event.created)
        # S-R5: the two chains are keyed by role, so they can never collide.
        self.assertNotEqual(worker_event.session_id, reviewer_event.session_id)
        self.assertEqual(
            worker_event.agent_command, SESSION_AGENT_COMMANDS["worker"]
        )
        self.assertEqual(
            reviewer_event.agent_command, SESSION_AGENT_COMMANDS["reviewer"]
        )
        # Layer 1 is rebuilt per attempt, and neither id is in it.
        self.assertEqual(
            tuple(key for key, _ in worker_event.task_boundary),
            tuple(sorted(TASK_BOUNDARY_KEYS)),
        )
        self.assertNotIn("task_id", dict(worker_event.task_boundary))
        self.assertNotIn("dispatch_id", dict(worker_event.task_boundary))
        self.assertEqual(dict(worker_event.task_boundary)["current_role"], "worker")
        self.assertEqual(dict(worker_event.task_boundary)["current_iteration"], "1")
        # The Reviewer, and only the Reviewer, carries the eight delta-first keys.
        self.assertEqual(
            reviewer_event.reviewer_context_keys, tuple(sorted(REVIEWER_CONTEXT_KEYS))
        )
        self.assertEqual(worker_event.reviewer_context_keys, ())

    def test_a_reused_chain_creates_each_role_session_once(self) -> None:
        result = self.run_phase(
            FakeScenario(
                ("complete", "complete"),
                ("fail", "pass"),
                reviewer_findings=(("R1",), ()),
                worker_resolutions=({}, {"R1": "RESOLVED"}),
            )
        )

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(len(result.sessions), 4)
        for role in ("worker", "reviewer"):
            events = [event for event in result.sessions if event.role == role]
            with self.subTest(role=role):
                self.assertEqual([event.created for event in events], [True, False])
                self.assertEqual(len({event.session_id for event in events}), 1)
                # A new boundary every attempt, even inside one session (S-R6).
                self.assertNotEqual(
                    events[0].task_boundary, events[1].task_boundary
                )

    def test_each_agent_echoes_the_boundary_it_was_actually_handed(self) -> None:
        """FINAL-I1-MAJOR-1, on this harness's own agent-visible channel.

        E2EHarness has no Orca Task and no dispatch preamble, so the fake agents'
        input is their argv: --task-spec carries the rendered boundary, and each fake
        parses it back and prints a receipt. Asserting on the receipt in the agent's
        stdout -- not on the SessionEvent the harness wrote for itself -- is what
        distinguishes a boundary that was delivered from one that was only recorded.
        """
        result = self.run_phase(
            FakeScenario(
                ("complete", "complete"),
                ("fail", "pass"),
                reviewer_findings=(("R1",), ()),
                worker_resolutions=({}, {"R1": "RESOLVED"}),
            )
        )

        self.assertEqual(result.final_status, "COMPLETED")
        for role, attempts in (
            ("worker", result.worker_attempts),
            ("reviewer", result.reviewer_attempts),
        ):
            events = [event for event in result.sessions if event.role == role]
            # The session is reused, so attempt 2 is the interesting one: same agent,
            # new Task, and it has to report the NEW iteration back.
            self.assertEqual([event.created for event in events], [True, False])
            for index, (event, attempt) in enumerate(zip(events, attempts), start=1):
                with self.subTest(role=role, iteration=index):
                    self.assertIn(BOUNDARY_RECEIPT_HEADING, attempt.output)
                    for key, value in event.task_boundary:
                        self.assertIn(
                            f"{BOUNDARY_RECEIPT_PREFIX}{key}: "
                            + value.replace("\n", SPEC_VALUE_SEPARATOR),
                            attempt.output,
                        )
                    self.assertIn(
                        f"{BOUNDARY_RECEIPT_PREFIX}current_iteration: {index}",
                        attempt.output,
                    )
        # The eight delta-first keys reach the Reviewer and only the Reviewer.
        self.assertIn(
            f"{BOUNDARY_RECEIPT_PREFIX}{REVIEWER_CONTEXT_RECEIPT_KEY}",
            result.reviewer_attempts[0].output,
        )
        self.assertNotIn(
            REVIEWER_CONTEXT_RECEIPT_KEY, result.worker_attempts[0].output
        )

    def test_the_fresh_policy_allocates_a_new_session_per_attempt(self) -> None:
        result = self.run_phase(
            FakeScenario(
                ("complete", "complete"),
                ("fail", "pass"),
                reviewer_findings=(("R1",), ()),
                worker_resolutions=({}, {"R1": "RESOLVED"}),
            ),
            session_policy="fresh",
        )

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertTrue(all(event.created for event in result.sessions))
        self.assertEqual(
            len({event.session_id for event in result.sessions}),
            len(result.sessions),
        )


class QualityGateE2ETests(unittest.TestCase):
    """The quality model, checked where the fake agents actually receive it.

    The E2E harness has no Orca preamble, so `--task-spec` IS the dispatched input,
    and each fake echoes a receipt parsed back out of it. Asserting on that receipt is
    therefore an assertion about the agent-visible payload, not about a helper.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    PROFILE = """version: 1

quality_attributes:

  - id: DOMAIN-001
    category: business-domain
    name: Idempotent processing
    blocking: true
    applies_to:
      - implementation

  - id: DESIGN-001
    category: platform-infrastructure
    name: Design only rule
    blocking: false
    applies_to:
      - design
"""

    def run_phase(self, *, profile: str | None) -> WorkflowResult:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            if profile is not None:
                path = workspace / DEFAULT_PROFILE_PATH
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(profile, encoding="utf-8")
            harness = E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=5,
                workspace=workspace,
            )
            return harness.run(FakeScenario(("complete",), ("pass",)))

    def test_both_agents_receive_the_quality_gate_block(self) -> None:
        result = self.run_phase(profile=self.PROFILE)

        self.assertEqual(result.final_status, "COMPLETED")
        for role, attempts in (
            ("worker", result.worker_attempts),
            ("reviewer", result.reviewer_attempts),
        ):
            with self.subTest(role):
                output = attempts[0].output
                self.assertIn(
                    f"{BOUNDARY_RECEIPT_PREFIX}{QUALITY_GATE_RECEIPT_KEY}", output
                )
                for key in QUALITY_GATE_KEYS:
                    self.assertIn(key, output)

    def test_the_absent_profile_run_still_carries_the_minimal_gate(self) -> None:
        """No profile is a defined state, not a reason to send nothing."""
        result = self.run_phase(profile=None)

        self.assertEqual(result.final_status, "COMPLETED")
        for attempts in (result.worker_attempts, result.reviewer_attempts):
            self.assertIn(
                f"{BOUNDARY_RECEIPT_PREFIX}{QUALITY_GATE_RECEIPT_KEY}",
                attempts[0].output,
            )

    def test_one_resolution_feeds_both_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / DEFAULT_PROFILE_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.PROFILE, encoding="utf-8")
            harness = E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=5,
                workspace=workspace,
            )
            gate = harness.quality_gate()

        self.assertEqual(gate["profile_status"], PROFILE_STATUS_LOADED)
        # implementation-scoped only: the design attribute is filtered out before the
        # spec is rendered, so neither role is asked to evaluate it here.
        rendered = " ".join(gate["applicable_quality_attributes"])
        self.assertIn("DOMAIN-001", rendered)
        self.assertNotIn("DESIGN-001", rendered)
        self.assertEqual(gate["blocking_quality_attributes"], ("DOMAIN-001",))


class RunArtifactRootProvisioningTests(unittest.TestCase):
    """MAJOR 1 (PR #13 review): the run directory must exist before a Worker runs.

    workspace is a fresh tempdir per test and this class never pre-creates
    artifacts/runs/ inside it -- the point is that E2EHarness does that itself.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )

    def test_constructing_the_harness_provisions_its_default_run_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            target = workspace / "artifacts" / "runs" / "run_e2e"
            self.assertFalse(target.exists())

            E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=5,
                workspace=workspace,
            )

            self.assertTrue(target.is_dir())

    def test_run_workflow_provisions_the_scenarios_own_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            target = workspace / "artifacts" / "runs" / "run_from_scenario"
            self.assertFalse(target.exists())

            harness = E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=5,
                workspace=workspace,
            )
            scenario = WorkflowScenario(
                phases=("implementation",),
                phase_scenarios={
                    "implementation": FakeScenario(("complete",), ("pass",))
                },
                final_review=FinalReviewScenario(modes=("pass",)),
                run_id="run_from_scenario",
            )

            result = harness.run_workflow(scenario)

            self.assertEqual(result.final_status, "COMPLETED")
            self.assertTrue(target.is_dir())
