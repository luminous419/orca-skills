#!/usr/bin/env python3
"""Deterministic fake-agent E2E scenarios for the shared workflow policy."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.task_context import RISK_CONTEXT_KEYS
from scripts.e2e_harness import (
    UNIT_TEST_GATED_PHASES,
    RiskNotSupportedError,
    parse_unit_test_status,
)
from scripts.e2e_harness import E2EHarness, FakeScenario, WorkflowResult
from scripts.e2e_harness import (
    FinalFinding,
    FinalReviewScenario,
    SESSION_AGENT_COMMANDS,
    SessionEvent,
    WorkflowRunResult,
    WorkflowScenario,
    downstream_revalidation_set,
    normalize_final_finding_spec,
    parse_final_review_output,
)
from scripts.e2e_harness import OutputContractError
from scripts.workflow_contract import load_workflow_output_contract
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


def _without_risk_profile(result):
    """The same result with the orchestration-only risk field cleared everywhere.

    Used only by the cross-skill comparison, so the equality it asserts is about
    behaviour the two skills genuinely share.
    """
    return replace(
        result,
        sessions=tuple(
            replace(event, risk_profile=())
            for event in result.sessions
        ),
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
                # T-26. Whole-object equality is no longer the right claim: risk is
                # orchestration-only, so the orchestration side carries a populated
                # SessionEvent.risk_profile and the loop side carries (). That
                # asymmetry IS the requirement -- the loop skill must have no risk
                # axis at all. The equality claim survives as a claim about SHARED
                # behaviour, made over a projection that drops only the intentional
                # field, plus two explicit assertions about the asymmetry itself.
                shared = [_without_risk_profile(result) for result in results]
                self.assertEqual(shared[0], shared[1])
                # (b) the loop skill is untouched: no event carries a risk block.
                self.assertTrue(
                    all(event.risk_profile == () for event in results[0].sessions)
                )
                # (c) the orchestration skill reflects the resolved risk. Only
                # Worker/Reviewer dispatches render a spec, so final_review events
                # legitimately carry () here too.
                dispatched = [
                    event
                    for event in results[1].sessions
                    if event.role in ("worker", "reviewer")
                ]
                for event in dispatched:
                    self.assertEqual(
                        tuple(key for key, _ in event.risk_profile),
                        tuple(sorted(RISK_CONTEXT_KEYS)),
                    )
                    self.assertEqual(dict(event.risk_profile)["risk_level"], "high")
                    self.assertEqual(
                        dict(event.risk_profile)["risk_source"], "default"
                    )

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


class QualityProfileWorkflowTests(unittest.TestCase):
    """ORIGINAL_REQUEST section 13-B and 13-H at the full-workflow level.

    The IMPLEMENTATION phase proved phase filtering and the run-scoped resolution one
    dispatch at a time. What no test asked was whether a WHOLE run holds together:
    five phases, a Final Review that fails, a correction round routed by Responsible
    Phase, and a downstream revalidation round -- all of them reading the same
    profile, each phase seeing only its own attributes. Every assertion below reads
    `SessionEvent.quality_gate`, which is parsed out of the `--task-spec` text the
    fake agent subprocess was actually handed, so a harness that built the right
    payload and dispatched a different one would fail here.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    PASSING_PHASE = FakeScenario(("complete",), ("pass",))

    # One attribute per interesting applies_to shape: a single canonical phase, a
    # multi-phase set, each specialized phase, and an omitted applies_to.
    PROFILE = """version: 1

quality_attributes:

  - id: DESIGN-001
    category: platform-infrastructure
    name: Design only rule
    blocking: false
    applies_to:
      - design

  - id: DOMAIN-001
    category: business-domain
    name: Idempotent processing
    blocking: true
    applies_to:
      - implementation
      - test

  - id: BUG-001
    category: operational-risk
    name: Bugfix only rule
    blocking: true
    applies_to:
      - bugfix

  - id: REFACTOR-001
    category: team-convention
    name: Refactoring only rule
    blocking: false
    applies_to:
      - refactoring

  - id: TEAM-001
    category: team-convention
    name: Repository convention
    blocking: false
"""

    # The keys whose value is a property of the RUN, not of the phase. Phase filtering
    # is allowed to vary the two attribute keys and nothing else; if a second
    # resolution ever leaked in, it would show up in one of these.
    RUN_SCOPED_KEYS = (
        "profile_status",
        "profile_path",
        "general_gate",
        "decision_priority",
        "non_blocking_by_default",
        "verdict_semantics",
    )

    def run_workflow_with_profile(
        self, scenario: WorkflowScenario, *, max_iterations: int = 5
    ) -> WorkflowRunResult:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            path = workspace / DEFAULT_PROFILE_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.PROFILE, encoding="utf-8")
            harness = E2EHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=max_iterations,
                workspace=workspace,
            )
            return harness.run_workflow(scenario)

    @staticmethod
    def gates(result: WorkflowRunResult) -> list[tuple[str, str, dict[str, str]]]:
        """(phase, role, gate) for every dispatch of the run that carried one."""
        return [
            (event.phase, event.role, dict(event.quality_gate))
            for event in result.sessions
            if event.quality_gate
        ]

    def attributes_by_phase(self, result: WorkflowRunResult) -> dict[str, set[str]]:
        seen: dict[str, set[str]] = {}
        for phase, _role, gate in self.gates(result):
            seen.setdefault(phase, set()).update(
                identifier
                for identifier in (
                    "DESIGN-001",
                    "DOMAIN-001",
                    "BUG-001",
                    "REFACTOR-001",
                    "TEAM-001",
                )
                if identifier in gate["applicable_quality_attributes"]
            )
        return seen

    def canonical_scenario(self, **overrides: object) -> WorkflowScenario:
        phases = ("analysis", "plan", "design", "implementation", "test")
        defaults: dict[str, object] = {
            "phases": phases,
            "phase_scenarios": {phase: self.PASSING_PHASE for phase in phases},
            "final_review": FinalReviewScenario(modes=("pass",)),
        }
        defaults.update(overrides)
        return WorkflowScenario(**defaults)  # type: ignore[arg-type]

    # ---- section 13-B: phase filtering, end to end -------------------------------

    def test_phase_scoped_attributes_are_filtered_across_a_full_five_phase_run(
        self,
    ) -> None:
        result = self.run_workflow_with_profile(self.canonical_scenario())

        self.assertEqual(result.final_status, "COMPLETED")
        seen = self.attributes_by_phase(result)
        self.assertEqual(
            set(seen), {"analysis", "plan", "design", "implementation", "test"}
        )
        self.assertEqual(seen["analysis"], {"TEAM-001"})
        self.assertEqual(seen["plan"], {"TEAM-001"})
        self.assertEqual(seen["design"], {"DESIGN-001", "TEAM-001"})
        self.assertEqual(seen["implementation"], {"DOMAIN-001", "TEAM-001"})
        self.assertEqual(seen["test"], {"DOMAIN-001", "TEAM-001"})

    def test_a_phases_worker_and_reviewer_are_handed_the_same_attributes(self) -> None:
        """Section 10: the two roles of one phase must not diverge."""
        result = self.run_workflow_with_profile(self.canonical_scenario())

        by_phase_role: dict[tuple[str, str], dict[str, str]] = {}
        for phase, role, gate in self.gates(result):
            by_phase_role[(phase, role)] = gate
        for phase in ("analysis", "plan", "design", "implementation", "test"):
            with self.subTest(phase):
                self.assertEqual(
                    by_phase_role[(phase, "worker")],
                    by_phase_role[(phase, "reviewer")],
                )

    def test_blocking_attributes_are_reported_per_phase_not_per_profile(self) -> None:
        """DOMAIN-001 is the only blocking attribute, and only where it applies."""
        result = self.run_workflow_with_profile(self.canonical_scenario())

        blocking = {
            phase: gate["blocking_quality_attributes"]
            for phase, _role, gate in self.gates(result)
        }
        self.assertEqual(blocking["analysis"], "none")
        self.assertEqual(blocking["design"], "none")
        self.assertEqual(blocking["implementation"], "DOMAIN-001")
        self.assertEqual(blocking["test"], "DOMAIN-001")

    def test_specialized_bugfix_and_refactoring_runs_see_only_their_own_rules(
        self,
    ) -> None:
        """Specialized phases are phases like any other -- including for filtering."""
        for phase, expected in (
            ("bugfix", {"BUG-001", "TEAM-001"}),
            ("refactoring", {"REFACTOR-001", "TEAM-001"}),
        ):
            with self.subTest(phase):
                result = self.run_workflow_with_profile(
                    WorkflowScenario(
                        phases=(phase,),
                        phase_scenarios={phase: self.PASSING_PHASE},
                        final_review=FinalReviewScenario(modes=("pass",)),
                        run_id=f"run_e2e_quality_{phase}",
                    )
                )

                self.assertEqual(result.final_status, "COMPLETED")
                self.assertEqual(self.attributes_by_phase(result), {phase: expected})

    def test_a_specialized_run_never_sees_a_canonical_phase_attribute(self) -> None:
        """The negative half: DOMAIN-001 is implementation/test scoped, so a BUGFIX
        run must not inherit it just because bugfix is 'the code phase'."""
        result = self.run_workflow_with_profile(
            WorkflowScenario(
                phases=("bugfix",),
                phase_scenarios={"bugfix": self.PASSING_PHASE},
                final_review=FinalReviewScenario(modes=("pass",)),
                run_id="run_e2e_quality_bugfix_negative",
            )
        )

        for _phase, _role, gate in self.gates(result):
            self.assertNotIn("DOMAIN-001", gate["applicable_quality_attributes"])
            self.assertNotIn("DESIGN-001", gate["applicable_quality_attributes"])
            self.assertEqual(gate["blocking_quality_attributes"], "BUG-001")

    # ---- section 13-H: the Final Review gate with a profile installed -------------

    def test_a_blocking_finding_still_routes_to_its_responsible_phase(self) -> None:
        """13-H b. The correction round runs, and its dispatches carry the profile."""
        result = self.run_workflow_with_profile(
            self.canonical_scenario(
                phases=("design", "implementation"),
                phase_scenarios={
                    "design": self.PASSING_PHASE,
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
                    )
                },
            )
        )

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.correction_dispatches, [("implementation", 2)])
        # The correction round is iteration 2 of implementation, and it must have been
        # told the same implementation-scoped attributes as iteration 1.
        implementation_gates = [
            gate
            for phase, _role, gate in self.gates(result)
            if phase == "implementation"
        ]
        self.assertGreaterEqual(len(implementation_gates), 4)
        for gate in implementation_gates:
            self.assertEqual(gate, implementation_gates[0])
            self.assertEqual(gate["blocking_quality_attributes"], "DOMAIN-001")

    def test_non_blocking_final_findings_do_not_start_a_correction_loop(self) -> None:
        """13-H c. Notes alone are not a correction trigger, profile or no profile."""
        result = self.run_workflow_with_profile(
            self.canonical_scenario(
                phases=("design", "implementation"),
                phase_scenarios={
                    "design": self.PASSING_PHASE,
                    "implementation": self.PASSING_PHASE,
                },
                final_review=FinalReviewScenario(
                    modes=("pass-nonblocking",),
                    findings=((("N1", "implementation"),),),
                ),
            )
        )

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.final_review_iterations, 1)
        self.assertEqual(result.correction_dispatches, [])
        self.assertEqual(result.revalidation_dispatches, [])
        self.assertEqual(result.corrected_findings, ())

    def test_downstream_revalidation_carries_the_same_profile(self) -> None:
        """13-H d. T5a still runs, and the revalidated phase reads the same model."""
        result = self.run_workflow_with_profile(
            self.canonical_scenario(
                phases=("design", "implementation", "test"),
                phase_scenarios={
                    "design": self.PASSING_PHASE,
                    "implementation": self.PASSING_PHASE,
                    "test": self.PASSING_PHASE,
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
                    )
                },
                revalidation_scenarios={
                    ("implementation", 1): self.PASSING_PHASE,
                    ("test", 1): self.PASSING_PHASE,
                },
            )
        )

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.correction_dispatches, [("design", 2)])
        self.assertEqual(
            result.revalidation_dispatches, [("implementation", 2), ("test", 2)]
        )
        # Every revalidation dispatch read the same phase-scoped model as the original
        # round: correcting an upstream phase must not change what downstream is
        # judged against.
        per_phase = self.attributes_by_phase(result)
        self.assertEqual(per_phase["implementation"], {"DOMAIN-001", "TEAM-001"})
        self.assertEqual(per_phase["test"], {"DOMAIN-001", "TEAM-001"})
        self.assertEqual(per_phase["design"], {"DESIGN-001", "TEAM-001"})

    def test_one_resolution_spans_every_dispatch_of_a_correcting_run(self) -> None:
        """13-H a at workflow level: phases, correction and revalidation agree.

        Phase filtering is allowed to vary the two attribute keys. Everything else in
        the block is a property of the run's single resolution, so any difference
        across dispatches means a second resolution reached one of them.
        """
        result = self.run_workflow_with_profile(
            self.canonical_scenario(
                phases=("design", "implementation", "test"),
                phase_scenarios={
                    "design": self.PASSING_PHASE,
                    "implementation": self.PASSING_PHASE,
                    "test": self.PASSING_PHASE,
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
                    )
                },
                revalidation_scenarios={
                    ("implementation", 1): self.PASSING_PHASE,
                    ("test", 1): self.PASSING_PHASE,
                },
            )
        )

        gates = self.gates(result)
        self.assertGreater(len(gates), 8, "the run must actually have dispatched")
        first = gates[0][2]
        for phase, role, gate in gates:
            with self.subTest(phase=phase, role=role):
                for key in self.RUN_SCOPED_KEYS:
                    self.assertEqual(gate[key], first[key])
                self.assertEqual(gate["profile_status"], PROFILE_STATUS_LOADED)


class FinalReviewFindingContractTests(unittest.TestCase):
    """TEST-I1 F-001: the finding MODEL must encode what the workflow claims to honour.

    Iteration 1 asserted that non-blocking findings start no correction loop, but the
    fixture had no way to say "non-blocking" -- the fake reviewer emitted only ID,
    Severity, Responsible Phase and Issue, and the parser read only the
    `## Blocking Findings` section. The test therefore passed because of which
    SECTION a finding was printed under, and would have passed identically with the
    OS-1 Severity-vs-Blocking split absent. These tests bind to the fields instead.
    """

    def report(self, *findings: str, verdict: str = "FAIL") -> str:
        body = "\n".join(findings)
        return f"# Review Result\n\nRESULT: {verdict}\n\n{body}\n"

    def contract(self):
        return load_workflow_output_contract(
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )

    def test_both_sections_are_parsed_with_their_contract_fields(self) -> None:
        """A note the parser cannot see cannot be a note it decided to ignore."""
        output = self.report(
            "## Blocking Findings",
            "ID: R1",
            "Quality Attribute: DOMAIN-001",
            "Severity: MAJOR",
            "Blocking: YES",
            "Responsible Phase: implementation",
            "",
            "## Non-Blocking Findings",
            "ID: N1",
            "Quality Attribute: NONE",
            "Severity: MAJOR",
            "Blocking: NO",
            "Responsible Phase: design",
        )

        verdict, findings = parse_final_review_output(output, self.contract())

        self.assertEqual(verdict, "FAIL")
        self.assertEqual(
            findings,
            (
                FinalFinding("R1", "implementation", "MAJOR", "DOMAIN-001", True),
                FinalFinding("N1", "design", "MAJOR", "NONE", False),
            ),
        )

    def test_a_finding_without_a_blocking_field_is_malformed(self) -> None:
        """Inferring it from the section would re-derive it from the signal it replaces."""
        output = self.report(
            "## Blocking Findings",
            "ID: R1",
            "Quality Attribute: DOMAIN-001",
            "Severity: MAJOR",
            "Responsible Phase: implementation",
        )

        with self.assertRaisesRegex(OutputContractError, "no Blocking field"):
            parse_final_review_output(output, self.contract())

    def test_a_finding_without_a_quality_attribute_field_is_malformed(self) -> None:
        """TEST-I2 F-001: the field was optional and silently invented as NONE.

        A real report can omit it, and the parser used to answer with a finding it
        made up -- so every downstream assertion about the attribute was an assertion
        about the fallback. That is also why the iteration-2 dropped-field mutation
        appeared to be caught: a happy-path test greps the fake reviewer's generated
        text, while this boundary accepted the malformed report.
        """
        output = self.report(
            "## Blocking Findings",
            "ID: R1",
            "Severity: MAJOR",
            "Blocking: YES",
            "Responsible Phase: implementation",
        )

        with self.assertRaisesRegex(
            OutputContractError, "R1 has no Quality Attribute field"
        ):
            parse_final_review_output(output, self.contract())

    def test_a_finding_without_a_severity_field_is_malformed(self) -> None:
        """The sibling gap: severity was never parsed at all, only defaulted."""
        output = self.report(
            "## Blocking Findings",
            "ID: R1",
            "Quality Attribute: DOMAIN-001",
            "Blocking: YES",
            "Responsible Phase: implementation",
        )

        with self.assertRaisesRegex(OutputContractError, "R1 has no Severity field"):
            parse_final_review_output(output, self.contract())

    def test_severity_is_read_from_the_report_not_defaulted(self) -> None:
        """Until iteration 3 every parsed finding was MAJOR whatever the report said.

        That made an equal-severity control assert MAJOR == MAJOR by construction.
        Parsing a report whose severities DIFFER is what proves the field is read.
        """
        output = self.report(
            "## Blocking Findings",
            "ID: R1",
            "Quality Attribute: DOMAIN-001",
            "Severity: CRITICAL",
            "Blocking: YES",
            "Responsible Phase: implementation",
            "",
            "## Non-Blocking Findings",
            "ID: N1",
            "Quality Attribute: TEAM-001",
            "Severity: MINOR",
            "Blocking: NO",
        )

        _verdict, findings = parse_final_review_output(output, self.contract())

        by_id = {finding.finding_id: finding for finding in findings}
        self.assertEqual(by_id["R1"].severity, "CRITICAL")
        self.assertEqual(by_id["N1"].severity, "MINOR")

    def test_an_uncharged_blocking_finding_is_malformed(self) -> None:
        """`Quality Attribute: NONE` with `Blocking: YES` names no criterion.

        reviews/common.md pairs NONE with exactly one blocking value, NO. A blocking
        General Gate violation is charged to G1-G5, so NONE + YES is a finding that
        claims to fail the gate under nothing at all.
        """
        output = self.report(
            "## Blocking Findings",
            "ID: R1",
            "Quality Attribute: NONE",
            "Severity: MAJOR",
            "Blocking: YES",
            "Responsible Phase: implementation",
        )

        with self.assertRaisesRegex(OutputContractError, "R1 is Blocking: YES with"):
            parse_final_review_output(output, self.contract())

    def test_the_legitimate_pairings_are_still_accepted(self) -> None:
        """The rejection must be exactly one combination, not a blunt instrument.

        NONE + NO is a generic observation, a General Gate id + YES is a gate
        violation, and a non-blocking PROFILE attribute + NO is the ordinary case for
        an attribute whose `blocking:` is false. All three are valid.
        """
        output = self.report(
            "## Blocking Findings",
            "ID: G1F",
            "Quality Attribute: G1",
            "Severity: MAJOR",
            "Blocking: YES",
            "Responsible Phase: implementation",
            "",
            "## Non-Blocking Findings",
            "ID: N1",
            "Quality Attribute: NONE",
            "Severity: MINOR",
            "Blocking: NO",
            "",
            "ID: N2",
            "Quality Attribute: TEAM-001",
            "Severity: MAJOR",
            "Blocking: NO",
        )

        _verdict, findings = parse_final_review_output(output, self.contract())

        by_id = {finding.finding_id: finding for finding in findings}
        self.assertEqual(set(by_id), {"G1F", "N1", "N2"})
        self.assertTrue(by_id["G1F"].blocking)
        self.assertFalse(by_id["N1"].blocking)
        self.assertFalse(by_id["N2"].blocking)
        self.assertEqual(by_id["N2"].quality_attribute, "TEAM-001")

    def test_the_emitted_fields_are_the_ones_SKILL_md_documents(self) -> None:
        """Anti-drift: the deterministic reviewer must speak the documented contract.

        The fixture is only evidence about OS-1 if it emits the same field names
        section 17's Final Review Finding Contract defines. Two spellings of the same
        contract would let the harness keep passing while the skill said otherwise.
        """
        skill = (
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        section = skill.split("## 17. Final Adversarial Review", 1)[1].split(
            "\n## 18.", 1
        )[0]
        fake = (REPO_ROOT / "scripts" / "fake_reviewer.py").read_text(encoding="utf-8")

        for field in ("Quality Attribute:", "Severity:", "Blocking:", "Responsible Phase:"):
            with self.subTest(field):
                self.assertIn(field, section)
                self.assertIn(field, fake)

    def test_the_two_value_spec_form_still_means_a_blocking_finding(self) -> None:
        """Every pre-OS-1 fixture in this file spells findings the short way."""
        self.assertEqual(
            normalize_final_finding_spec(("R1", "implementation")),
            ("R1", "implementation", "G1", True),
        )
        self.assertEqual(
            normalize_final_finding_spec(("N1", "design", "NONE", False)),
            ("N1", "design", "NONE", False),
        )


class BlockingAttributeCorrectionTests(unittest.TestCase):
    """TEST-I1 F-001: blocking routes, non-blocking does not, at full workflow level.

    Both scenarios below hold SEVERITY CONSTANT at MAJOR across every finding, so the
    only thing that can explain a difference in what gets corrected is the
    `Blocking:` field and the quality attribute behind it. That is the whole content
    of "Severity != Blocking", and it is not provable while severity and section are
    the only things a fixture can vary.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    PASSING_PHASE = FakeScenario(("complete",), ("pass",))
    PROFILE = QualityProfileWorkflowTests.PROFILE
    RUN_SCOPED_KEYS = QualityProfileWorkflowTests.RUN_SCOPED_KEYS

    def run_workflow_with_profile(self, scenario: WorkflowScenario):
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
            return harness.run_workflow(scenario)

    def blocking_scenario(self) -> WorkflowScenario:
        """R1 is charged to DOMAIN-001, the profile's one blocking attribute."""
        return WorkflowScenario(
            phases=("implementation", "test"),
            phase_scenarios={
                "implementation": self.PASSING_PHASE,
                "test": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"),
                findings=((("R1", "implementation", "DOMAIN-001", True),), ()),
            ),
            correction_scenarios={
                ("implementation", 1): FakeScenario(
                    ("correction",),
                    ("pass",),
                    worker_resolutions=({"R1": "RESOLVED"},),
                )
            },
            revalidation_scenarios={("test", 1): self.PASSING_PHASE},
            run_id="run_e2e_blocking_attribute",
        )

    def test_a_blocking_quality_attribute_violation_drives_correction(self) -> None:
        result = self.run_workflow_with_profile(self.blocking_scenario())

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.correction_dispatches, [("implementation", 2)])
        # T5a: TEST is downstream of the corrected IMPLEMENTATION and is revalidated.
        self.assertEqual(result.revalidation_dispatches, [("test", 2)])
        self.assertEqual(
            [entry[1:3] for entry in result.corrected_findings],
            [("R1", "implementation")],
        )

    def test_the_dispatched_report_carries_the_attribute_and_blocking_fields(
        self,
    ) -> None:
        """The finding the workflow acted on really said DOMAIN-001 / Blocking: YES."""
        result = self.run_workflow_with_profile(self.blocking_scenario())

        report = result.final_review_attempts[0].output
        self.assertIn("ID: R1", report)
        self.assertIn("Quality Attribute: DOMAIN-001", report)
        self.assertIn("Blocking: YES", report)
        self.assertIn("Responsible Phase: implementation", report)

    def test_the_correction_round_shares_the_runs_profile_resolution(self) -> None:
        """The correction and revalidation dispatches read the same resolution."""
        result = self.run_workflow_with_profile(self.blocking_scenario())

        gates = [
            (event.phase, dict(event.quality_gate))
            for event in result.sessions
            if event.quality_gate
        ]
        self.assertGreater(len(gates), 6)
        first = gates[0][1]
        for phase, gate in gates:
            with self.subTest(phase):
                for key in self.RUN_SCOPED_KEYS:
                    self.assertEqual(gate[key], first[key])
                # Both requested phases are inside DOMAIN-001's applies_to.
                self.assertEqual(gate["blocking_quality_attributes"], "DOMAIN-001")

    def mixed_scenario(self) -> WorkflowScenario:
        """One report, two MAJOR findings, different only in Blocking and attribute.

        N1 names `design` as its Responsible Phase and design IS a requested phase, so
        a router that ignored `Blocking:` would have a real phase to correct and the
        run would demand a correction fixture that deliberately does not exist.
        """
        return WorkflowScenario(
            phases=("design", "implementation"),
            phase_scenarios={
                "design": self.PASSING_PHASE,
                "implementation": self.PASSING_PHASE,
            },
            final_review=FinalReviewScenario(
                modes=("fail-mixed", "pass"),
                findings=(
                    (
                        ("R1", "implementation", "DOMAIN-001", True),
                        ("N1", "design", "NONE", False),
                    ),
                    (),
                ),
            ),
            correction_scenarios={
                ("implementation", 1): FakeScenario(
                    ("correction",),
                    ("pass",),
                    worker_resolutions=({"R1": "RESOLVED"},),
                )
            },
            run_id="run_e2e_mixed_findings",
        )

    def test_a_non_blocking_finding_is_reported_and_never_corrected(self) -> None:
        result = self.run_workflow_with_profile(self.mixed_scenario())

        self.assertEqual(result.final_status, "COMPLETED")
        # Only the blocking finding routed. `design` was named by N1 and is a
        # requested phase, so its absence here is a decision, not an impossibility.
        self.assertEqual(result.correction_dispatches, [("implementation", 2)])
        self.assertNotIn(
            "design", [phase for phase, _iteration in result.correction_dispatches]
        )
        self.assertEqual(
            [entry[1:3] for entry in result.corrected_findings],
            [("R1", "implementation")],
        )

    def test_severity_is_held_constant_so_only_blocking_can_explain_the_split(
        self,
    ) -> None:
        """The control: both findings are MAJOR, and only one was corrected."""
        result = self.run_workflow_with_profile(self.mixed_scenario())

        report = result.final_review_attempts[0].output
        verdict, findings = parse_final_review_output(
            report,
            load_workflow_output_contract(self.ORCHESTRATION_SKILL),
        )

        self.assertEqual(verdict, "FAIL")
        by_id = {finding.finding_id: finding for finding in findings}
        self.assertEqual(set(by_id), {"R1", "N1"})
        self.assertEqual(by_id["R1"].severity, by_id["N1"].severity)
        self.assertEqual(by_id["R1"].severity, "MAJOR")
        self.assertTrue(by_id["R1"].blocking)
        self.assertFalse(by_id["N1"].blocking)
        self.assertEqual(by_id["R1"].quality_attribute, "DOMAIN-001")
        self.assertEqual(by_id["N1"].quality_attribute, "NONE")
        # And the non-blocking one really did name a correctable phase.
        self.assertEqual(by_id["N1"].responsible_phase, "design")


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

    def test_correction_and_revalidation_clones_share_the_run_resolution(self) -> None:
        """IMPL-I1 F-001, E2E side: a phase clone must not re-read the profile.

        _phase_harness() is the seam every correction and downstream-revalidation
        round goes through. A shallow copy shares the resolution by reference; a
        clone that re-resolved would give a correction Worker a different model from
        the Reviewer that failed it.
        """
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
            resolved = harness.quality_profile
            path.write_text("version: 1\nquality_attributes: []\n", encoding="utf-8")
            clone = harness._phase_harness("implementation", 2)

        self.assertIs(clone.quality_profile, resolved)
        self.assertIn(
            "DOMAIN-001", " ".join(clone.quality_gate()["applicable_quality_attributes"])
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


class RiskWorkflowTests(unittest.TestCase):
    """OS-3: the risk axis, driven through the real run_workflow state machine.

    Orchestration skill only. The loop skill has no risk axis, and asserting the
    boundary is T-26/T-27's job, not this class's.
    """

    ORCHESTRATION_SKILL = (
        REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    LOOP_SKILL = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    PASSING = FakeScenario(("complete",), ("pass",))
    # A LOW run on a section-14 gated phase must carry affirmative evidence.
    PASSING_GATED = FakeScenario(("complete",), ("pass",), worker_unit_test_statuses=("PASS",))

    # ---- helpers ----------------------------------------------------------------

    def run_workflow(
        self,
        scenario: WorkflowScenario,
        *,
        risk: str | None = None,
        skill_path: Path | None = None,
        max_iterations: int = 5,
    ) -> WorkflowRunResult:
        with tempfile.TemporaryDirectory() as directory:
            harness = E2EHarness(
                skill_path or self.ORCHESTRATION_SKILL,
                phase="implementation",
                max_iterations=max_iterations,
                workspace=Path(directory),
                risk=risk,
            )
            return harness.run_workflow(scenario)

    def clean_scenario(self, phases: tuple[str, ...], **kwargs) -> WorkflowScenario:
        """Every requested phase passes first try, and the Final Review passes."""
        return WorkflowScenario(
            phases=phases,
            phase_scenarios={
                phase: (
                    self.PASSING_GATED
                    if phase in UNIT_TEST_GATED_PHASES
                    else self.PASSING
                )
                for phase in phases
            },
            final_review=FinalReviewScenario(modes=("pass",)),
            **kwargs,
        )

    @staticmethod
    def reviewer_events(result: WorkflowRunResult) -> list:
        return [event for event in result.sessions if event.role == "reviewer"]

    @staticmethod
    def churn(result: WorkflowRunResult) -> int:
        return (
            len(RiskWorkflowTests.reviewer_events(result))
            + len(result.correction_dispatches)
            + len(result.revalidation_dispatches)
        )

    # ---- T-1 / T-2 / T-3: the phase-set matrix ----------------------------------

    def test_analysis_plan_matrix(self) -> None:
        """T-1."""
        phases = ("analysis", "plan")
        expected = {"low": 0, "medium": 2, "high": 2}
        for risk, reviewers in expected.items():
            with self.subTest(risk=risk):
                result = self.run_workflow(self.clean_scenario(phases), risk=risk)
                self.assertEqual(result.final_status, "COMPLETED")
                self.assertEqual(len(self.reviewer_events(result)), reviewers)

    def test_plan_design_implementation_matrix(self) -> None:
        """T-2."""
        phases = ("plan", "design", "implementation")
        expected = {"low": 0, "medium": 3, "high": 3}
        for risk, reviewers in expected.items():
            with self.subTest(risk=risk):
                result = self.run_workflow(self.clean_scenario(phases), risk=risk)
                self.assertEqual(result.final_status, "COMPLETED")
                self.assertEqual(len(self.reviewer_events(result)), reviewers)

    def test_the_executed_phase_set_is_identical_at_every_risk(self) -> None:
        """T-3. Risk changes HOW STRONGLY, never WHAT."""
        phases = ("plan", "design", "implementation")
        dispatched = {}
        for risk in ("low", "medium", "high"):
            result = self.run_workflow(self.clean_scenario(phases), risk=risk)
            self.assertEqual(result.phases, phases)
            dispatched[risk] = [
                event.phase for event in result.sessions if event.role == "worker"
            ]
        self.assertEqual(dispatched["low"], dispatched["medium"])
        self.assertEqual(dispatched["medium"], dispatched["high"])
        self.assertEqual(dispatched["low"], list(phases))

    def test_risk_omitted_matches_explicit_high(self) -> None:
        """T-4. The backward-compatibility guarantee, stated directly."""
        phases = ("plan", "design")
        omitted = self.run_workflow(self.clean_scenario(phases))
        explicit = self.run_workflow(self.clean_scenario(phases), risk="high")
        self.assertEqual(omitted.risk, "high")
        self.assertEqual(omitted.risk_source, "default")
        self.assertEqual(explicit.risk_source, "explicit")
        self.assertEqual(omitted.phase_iterations, explicit.phase_iterations)
        self.assertEqual(
            len(self.reviewer_events(omitted)), len(self.reviewer_events(explicit))
        )
        self.assertEqual(
            omitted.final_review_iterations, explicit.final_review_iterations
        )

    # ---- TEST phase: the section 13 counter, which nothing asserted at LOW -------

    def test_phase_iterations_counts_gate_attempts_at_every_risk(self) -> None:
        """SKILL.md section 13 redefines PHASE_ITERATIONS as *gate* attempts -- a
        Reviewer attempt at MEDIUM/HIGH, a Worker attempt at LOW.

        Nothing asserted this at LOW. If gate_attempts() regressed to counting
        reviewer attempts, a LOW run would silently report all-zeros -- exactly the
        "technically true and practically useless" ITERATIONS_BY_PHASE the analysis
        phase identified -- and every other test in this file would still pass.
        """
        phases = ("plan", "design", "implementation")
        counters = {}
        for risk in ("low", "medium", "high"):
            result = self.run_workflow(self.clean_scenario(phases), risk=risk)
            self.assertEqual(result.final_status, "COMPLETED")
            counters[risk] = result.phase_iterations
        expected = {phase: 1 for phase in phases}
        for risk, counter in counters.items():
            with self.subTest(risk=risk):
                # Not zero, and not phase-dependent: one gate attempt per phase.
                self.assertEqual(counter, expected)

    def test_low_correction_rounds_are_counted_and_ledgered(self) -> None:
        """The same counter on the T4 path. A LOW correction round dispatches a
        Worker and no Reviewer, so a reviewer-attempt-based counter would leave both
        the counter and the correction ledger untouched."""
        result = self.run_workflow(
            self.fail_then_pass_scenario(("plan", "design")), risk="low"
        )
        self.assertEqual(result.final_status, "COMPLETED")
        # phase gate (1) + one correction round (1)
        self.assertEqual(result.phase_iterations["plan"], 2)
        self.assertEqual(result.phase_iterations["design"], 1)
        # and the ledger records the round at the right iteration number
        self.assertEqual(result.correction_dispatches, [("plan", 2)])

    # ---- TEST phase: reviewer_gates_skipped beyond a single phase ----------------

    def test_every_requested_phase_is_recorded_as_a_skipped_gate_at_low(self) -> None:
        """The log-facing record of "which phases got a Reviewer gate". Previously
        asserted only for a one-phase run, where a bug that recorded just the first
        phase would be invisible."""
        phases = ("analysis", "plan", "design")
        result = self.run_workflow(self.clean_scenario(phases), risk="low")
        self.assertEqual(result.reviewer_gates_skipped, list(phases))

    def test_no_gate_is_recorded_as_skipped_at_medium_or_high(self) -> None:
        for risk in ("medium", "high"):
            with self.subTest(risk=risk):
                result = self.run_workflow(
                    self.clean_scenario(("analysis", "plan")), risk=risk
                )
                self.assertEqual(result.reviewer_gates_skipped, [])

    def test_final_review_eligibility_at_low_needs_no_phase_reviewer(self) -> None:
        """T-29 (Final Review R1). Section 17's gate is mandatory at every risk
        level, and LOW produces no phase Reviewer verdict at all -- so eligibility
        must rest on the phase gate, never on a Reviewer PASS.

        `test_low_final_fail_routes_worker_only` covers the FAIL path; this is the
        clean path, which is the one a literal reading of the old section 17 trigger
        sentence would have blocked.
        """
        result = self.run_workflow(
            self.clean_scenario(("analysis", "plan", "design")), risk="low"
        )
        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(self.reviewer_events(result), [])       # no phase Reviewer ran
        self.assertGreaterEqual(result.final_review_iterations, 1)  # the gate still fired
        self.assertEqual(result.final_review_verdict, "PASS")

    # ---- T-9 / T-10 / T-11: Final-Review FAIL routing ----------------------------

    def fail_then_pass_scenario(self, phases: tuple[str, ...]) -> WorkflowScenario:
        """Final Review FAILs once, charged to the first phase, then passes."""
        correction = FakeScenario(
            ("correction",),
            ("pass",),
            worker_resolutions=({"R1": "RESOLVED"},),
            worker_unit_test_statuses=("PASS",),
        )
        revalidation = FakeScenario(
            ("complete",), ("pass",), worker_unit_test_statuses=("PASS",)
        )
        return WorkflowScenario(
            phases=phases,
            phase_scenarios={
                phase: (
                    self.PASSING_GATED
                    if phase in UNIT_TEST_GATED_PHASES
                    else self.PASSING
                )
                for phase in phases
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"), findings=((("R1", phases[0]),), ())
            ),
            correction_scenarios={(phases[0], 1): correction},
            revalidation_scenarios={
                (phase, 1): revalidation for phase in phases[1:]
            },
        )

    def test_low_final_fail_routes_worker_only(self) -> None:
        """T-9. Correction runs with ZERO phase-Reviewer dispatches."""
        result = self.run_workflow(
            self.fail_then_pass_scenario(("plan", "design")), risk="low"
        )
        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(self.reviewer_events(result), [])
        self.assertEqual(result.revalidation_dispatches, [])
        self.assertEqual(result.final_review_iterations, 2)
        self.assertEqual([phase for phase, _ in result.correction_dispatches], ["plan"])

    def test_medium_final_fail_routes_through_the_phase_reviewer(self) -> None:
        """T-10. Correction is reviewed; T5a still does not run."""
        result = self.run_workflow(
            self.fail_then_pass_scenario(("plan", "design")), risk="medium"
        )
        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual([phase for phase, _ in result.correction_dispatches], ["plan"])
        self.assertEqual(result.revalidation_dispatches, [])

    def test_high_final_fail_runs_downstream_revalidation(self) -> None:
        """T-11. T5a covers every requested phase after the corrected one."""
        result = self.run_workflow(
            self.fail_then_pass_scenario(("plan", "design", "implementation")),
            risk="high",
        )
        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual([phase for phase, _ in result.correction_dispatches], ["plan"])
        self.assertEqual(
            [phase for phase, _ in result.revalidation_dispatches],
            ["design", "implementation"],
        )

    # ---- T-12: the authorized DQ-1 churn table -----------------------------------

    def test_churn_ordering_on_the_correction_path_is_strict(self) -> None:
        """T-12, row 2: LOW < MEDIUM < HIGH, strictly."""
        phases = ("plan", "design", "implementation")
        churn = {
            risk: self.churn(
                self.run_workflow(self.fail_then_pass_scenario(phases), risk=risk)
            )
            for risk in ("low", "medium", "high")
        }
        self.assertLess(churn["low"], churn["medium"])
        self.assertLess(churn["medium"], churn["high"])

    def test_churn_on_a_clean_first_pass_is_the_documented_exception(self) -> None:
        """T-12, row 1: MEDIUM == HIGH, asserted as intended behaviour."""
        phases = ("plan", "design", "implementation")
        churn = {
            risk: self.churn(self.run_workflow(self.clean_scenario(phases), risk=risk))
            for risk in ("low", "medium", "high")
        }
        self.assertLess(churn["low"], churn["medium"])
        self.assertEqual(churn["medium"], churn["high"])

    # ---- T-13: HIGH inspects out of scope without creating phase Tasks -----------

    def test_an_out_of_scope_finding_is_lowered_never_widened(self) -> None:
        """T-13. The dispatched phase set never grows, at any risk level."""
        correction = FakeScenario(
            ("correction",), ("pass",), worker_resolutions=({"R1": "RESOLVED"},)
        )
        scenario = WorkflowScenario(
            phases=("plan",),
            phase_scenarios={"plan": self.PASSING},
            # `design` is NOT requested: the ladder must lower it onto `plan`.
            final_review=FinalReviewScenario(
                modes=("fail", "pass"), findings=((("R1", "design"),), ())
            ),
            correction_scenarios={("plan", 1): correction},
        )
        for risk in ("low", "medium", "high"):
            with self.subTest(risk=risk):
                result = self.run_workflow(scenario, risk=risk)
                self.assertEqual(result.final_status, "COMPLETED")
                self.assertEqual(
                    {event.phase for event in result.sessions if event.role == "worker"},
                    {"plan"},
                )
                self.assertEqual(
                    [phase for phase, _ in result.correction_dispatches], ["plan"]
                )

    # ---- T-14 / T-15: specialized phases -----------------------------------------

    def assert_specialized(self, phase: str, expected_floor: str) -> None:
        churn = {}
        for risk in ("low", "medium", "high"):
            result = self.run_workflow(self.clean_scenario((phase,)), risk=risk)
            self.assertEqual(result.final_status, "COMPLETED")
            self.assertEqual(result.revalidation_dispatches, [])
            churn[risk] = self.churn(result)
            worker_events = [
                event for event in result.sessions
                if event.role == "worker" and event.phase == phase
            ]
            self.assertTrue(worker_events)
            for event in worker_events:
                self.assertEqual(
                    dict(event.risk_profile)["safety_floor"], expected_floor
                )
        self.assertLess(churn["low"], churn["medium"])
        # The DQ-1 documented exception: D is always empty for specialized runs.
        self.assertEqual(churn["medium"], churn["high"])

    def test_bugfix_across_risk_levels(self) -> None:
        """T-14."""
        self.assert_specialized("bugfix", "regression_test_required")

    def test_refactoring_across_risk_levels(self) -> None:
        """T-15."""
        self.assert_specialized(
            "refactoring", "behavior_preservation_and_relevant_unit_tests"
        )

    # ---- T-16: profile independence, on the dispatched payload -------------------

    def test_the_quality_gate_payload_does_not_vary_with_risk(self) -> None:
        """T-16. Read off result.sessions, so it is about what an agent received."""
        phases = ("plan", "design")
        gates = {}
        for risk in ("low", "medium", "high"):
            result = self.run_workflow(self.clean_scenario(phases), risk=risk)
            gates[risk] = sorted(
                (event.phase, event.role, event.quality_gate)
                for event in result.sessions
                if event.role == "worker"
            )
        self.assertEqual(gates["low"], gates["medium"])
        self.assertEqual(gates["medium"], gates["high"])

    # ---- T-22 / T-22a / T-23: the section 14 safety floor -------------------------

    def gated_scenario(self, phase: str, status: str) -> WorkflowScenario:
        return WorkflowScenario(
            phases=(phase,),
            phase_scenarios={
                phase: FakeScenario(
                    ("complete",),
                    ("pass",),
                    worker_unit_test_statuses=(status,) if status else (),
                )
            },
            final_review=FinalReviewScenario(modes=("pass",)),
        )

    def test_low_requires_affirmative_unit_test_evidence(self) -> None:
        """T-22. Only an explicit PASS satisfies the gate; every other input is a
        defined non-PASS, and none of them dispatches a Reviewer."""
        cases = {
            "PASS": ("COMPLETED", None),
            "": ("BLOCKED", "UNIT_TEST_EVIDENCE_MISSING"),
            "BLOCKED": ("BLOCKED", "UNIT_TEST_BLOCKED"),
        }
        for phase in sorted(UNIT_TEST_GATED_PHASES):
            for status, (expected_status, expected_reason) in cases.items():
                with self.subTest(phase=phase, status=status or "<absent>"):
                    result = self.run_workflow(
                        self.gated_scenario(phase, status), risk="low"
                    )
                    self.assertEqual(result.final_status, expected_status)
                    if expected_reason is not None:
                        self.assertEqual(result.reason, expected_reason)
                        self.assertEqual(self.reviewer_events(result), [])

    def malformed_scenario(
        self, phase: str, raw: tuple[str, ...]
    ) -> WorkflowScenario:
        """A scenario whose Worker emits RAW UNIT_TEST_STATUS lines.

        The --unit-test-status knob is constrained to the well-formed values on
        purpose, so it cannot reach the parser's duplicate-line or unknown-value
        branches. This drives them through the real subprocess instead.
        """
        return WorkflowScenario(
            phases=(phase,),
            phase_scenarios={
                phase: FakeScenario(
                    ("complete",), ("pass",), worker_unit_test_status_lines=(raw,)
                )
            },
            final_review=FinalReviewScenario(modes=("pass",)),
        )

    def assert_malformed(self, result: WorkflowRunResult) -> None:
        self.assertEqual(result.final_status, "ERROR")
        self.assertIsNotNone(result.reason)
        self.assertTrue(
            result.reason.startswith("MALFORMED_WORKER_OUTPUT:"),
            f"unexpected reason: {result.reason!r}",
        )
        # The error returns before the Reviewer half, at every risk level.
        self.assertEqual(self.reviewer_events(result), [])

    def test_duplicate_unit_test_status_lines_are_malformed_output(self) -> None:
        """T-22, the duplicate branch, through the real run() parse path.

        Two lines is a contract violation whatever the values are: the gate asks
        what the Worker reported, and two answers is not an answer.
        """
        for raw in (("PASS", "PASS"), ("PASS", "BLOCKED"), ("BLOCKED", "BLOCKED")):
            for phase in sorted(UNIT_TEST_GATED_PHASES):
                with self.subTest(raw=raw, phase=phase):
                    result = self.run_workflow(
                        self.malformed_scenario(phase, raw), risk="low"
                    )
                    self.assert_malformed(result)
                    self.assertIn("at most one", result.reason)

    def test_an_unknown_unit_test_status_value_is_malformed_output(self) -> None:
        """T-22, the unknown-value branch, through the real run() parse path."""
        for value in ("MAYBE", "SKIPPED", "FAILED", "OK"):
            with self.subTest(value=value):
                result = self.run_workflow(
                    self.malformed_scenario("implementation", (value,)), risk="low"
                )
                self.assert_malformed(result)
                self.assertIn(f"invalid UNIT_TEST_STATUS {value}", result.reason)

    def test_malformed_evidence_is_an_error_at_every_risk_level(self) -> None:
        """The parse runs before any risk branch, so a contract violation is an
        ERROR at MEDIUM and HIGH too -- not something only LOW notices."""
        for risk in ("low", "medium", "high"):
            for raw in (("PASS", "PASS"), ("MAYBE",)):
                with self.subTest(risk=risk, raw=raw):
                    result = self.run_workflow(
                        self.malformed_scenario("implementation", raw), risk=risk
                    )
                    self.assert_malformed(result)

    def test_a_lowercase_value_is_not_a_recognized_field_line(self) -> None:
        """A boundary worth pinning: FIELD_LINE requires an uppercase value, so
        `UNIT_TEST_STATUS: pass` is not a malformed VALUE -- it is not a recognized
        line at all, which at LOW is the missing-evidence case, not an ERROR."""
        result = self.run_workflow(
            self.malformed_scenario("implementation", ("pass",)), risk="low"
        )
        self.assertEqual(result.final_status, "BLOCKED")
        self.assertEqual(result.reason, "UNIT_TEST_EVIDENCE_MISSING")
        self.assertEqual(self.reviewer_events(result), [])

    def test_malformed_evidence_on_an_ungated_phase_is_still_an_error(self) -> None:
        """The phase gate is risk-and-phase conditional; the output CONTRACT is not.
        A duplicate line is malformed output even where section 14 names no gate."""
        result = self.run_workflow(
            self.malformed_scenario("plan", ("PASS", "PASS")), risk="low"
        )
        self.assert_malformed(result)

    def test_the_raw_seam_still_reaches_the_ordinary_gate(self) -> None:
        """The seam is not a bypass: a single well-formed raw line behaves exactly
        like the constrained knob, which is what makes the tests above meaningful."""
        passing = self.run_workflow(
            self.malformed_scenario("implementation", ("PASS",)), risk="low"
        )
        self.assertEqual(passing.final_status, "COMPLETED")
        blocked = self.run_workflow(
            self.malformed_scenario("implementation", ("BLOCKED",)), risk="low"
        )
        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(blocked.reason, "UNIT_TEST_BLOCKED")

    def test_an_ungated_phase_is_unaffected_at_low(self) -> None:
        """T-22a. TEST in particular is asserted NOT gated -- section 14 names three
        phases, and TEST is not one of them."""
        for phase in ("analysis", "plan", "design", "test"):
            with self.subTest(phase=phase):
                self.assertNotIn(phase, UNIT_TEST_GATED_PHASES)
                result = self.run_workflow(
                    self.gated_scenario(phase, ""), risk="low"
                )
                self.assertEqual(result.final_status, "COMPLETED")

    def test_medium_and_high_still_dispatch_the_reviewer(self) -> None:
        """T-23. The section 14 enforcer at MEDIUM/HIGH is the phase Reviewer, and
        that is unchanged: no input short-circuits before it."""
        for risk in ("medium", "high"):
            for status in ("PASS", "", "BLOCKED"):
                with self.subTest(risk=risk, status=status or "<absent>"):
                    result = self.run_workflow(
                        self.gated_scenario("implementation", status), risk=risk
                    )
                    self.assertEqual(result.final_status, "COMPLETED")
                    self.assertEqual(len(self.reviewer_events(result)), 1)

    def test_the_parser_itself_rejects_duplicate_and_unknown_values(self) -> None:
        """The unit-level complement to the run()-path tests above: the same two
        branches, asserted directly, so a future refactor that moves the gate cannot
        quietly lose them."""
        with self.assertRaisesRegex(OutputContractError, "at most one"):
            parse_unit_test_status(
                "# Worker Result\n\nSTATUS: COMPLETE\n"
                "UNIT_TEST_STATUS: PASS\nUNIT_TEST_STATUS: BLOCKED\n"
            )
        with self.assertRaisesRegex(OutputContractError, "invalid UNIT_TEST_STATUS"):
            parse_unit_test_status(
                "# Worker Result\n\nSTATUS: COMPLETE\nUNIT_TEST_STATUS: MAYBE\n"
            )
        # And the two well-formed answers still parse.
        self.assertEqual(
            parse_unit_test_status("STATUS: COMPLETE\nUNIT_TEST_STATUS: PASS\n"),
            "PASS",
        )
        self.assertEqual(
            parse_unit_test_status("STATUS: COMPLETE\nUNIT_TEST_STATUS: BLOCKED\n"),
            "BLOCKED",
        )

    def test_the_fake_worker_emits_nothing_when_the_flag_is_absent(self) -> None:
        """T-23, the untouched-fixture guard: this is what keeps every pre-existing
        FakeScenario byte-identical."""
        result = self.run_workflow(self.gated_scenario("implementation", ""))
        worker_output = result  # the run reached the reviewer, i.e. no gate fired
        self.assertEqual(worker_output.final_status, "COMPLETED")
        self.assertEqual(parse_unit_test_status("# Worker Result\n\nSTATUS: COMPLETE\n"), "")

    # ---- T-24 / T-25: the dispatched risk payload --------------------------------

    def test_safety_floor_evidence_reaches_the_worker(self) -> None:
        """T-24, read off the dispatched payload rather than the builder."""
        expected = {
            "low": "unit_test_status_required",
            "medium": "phase_reviewer_verifies",
            "high": "phase_reviewer_verifies",
        }
        for risk, evidence in expected.items():
            with self.subTest(risk=risk):
                result = self.run_workflow(
                    self.clean_scenario(("implementation",)), risk=risk
                )
                worker = next(
                    event for event in result.sessions if event.role == "worker"
                )
                self.assertEqual(
                    dict(worker.risk_profile)["safety_floor_evidence"], evidence
                )
                self.assertEqual(
                    dict(worker.risk_profile)["safety_floor"],
                    "unit_test_add_modify_execute_pass",
                )

    def test_every_dispatched_spec_carries_the_full_risk_block(self) -> None:
        """T-25. The wiring itself: one risk_context() call, both roles."""
        result = self.run_workflow(self.clean_scenario(("plan",)), risk="medium")
        dispatched = [
            event for event in result.sessions if event.role in ("worker", "reviewer")
        ]
        self.assertEqual(len(dispatched), 2)
        for event in dispatched:
            self.assertEqual(
                tuple(key for key, _ in event.risk_profile),
                tuple(sorted(RISK_CONTEXT_KEYS)),
            )
        self.assertEqual(dispatched[0].risk_profile, dispatched[1].risk_profile)
        for event in result.sessions:
            if event.role == "final_review":
                self.assertEqual(event.risk_profile, ())

    # ---- T-27: the capability boundary --------------------------------------------

    def test_explicit_risk_on_a_non_risk_skill_fails_closed(self) -> None:
        """T-27. Two layers, each in its own idiom."""
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RiskNotSupportedError) as caught:
                E2EHarness(
                    self.LOOP_SKILL,
                    phase="implementation",
                    workspace=Path(directory),
                    risk="low",
                )
            self.assertIn("RISK_NOT_SUPPORTED", str(caught.exception))

        result = self.run_workflow(
            WorkflowScenario(
                phases=("plan",),
                phase_scenarios={"plan": self.PASSING},
                final_review=FinalReviewScenario(modes=("pass",)),
                risk="low",
            ),
            skill_path=self.LOOP_SKILL,
        )
        self.assertEqual(result.final_status, "ERROR")
        self.assertEqual(result.reason, "SCENARIO_RISK_NOT_SUPPORTED:low")

    def test_a_non_risk_skill_without_an_explicit_risk_is_unchanged(self) -> None:
        """T-27, the must-not-fail half."""
        result = self.run_workflow(
            self.clean_scenario(("plan",)), skill_path=self.LOOP_SKILL
        )
        self.assertEqual(result.final_status, "COMPLETED")
        self.assertIsNone(result.risk)
        self.assertIsNone(result.risk_source)
        self.assertTrue(all(event.risk_profile == () for event in result.sessions))

    def test_an_invalid_scenario_risk_is_refused(self) -> None:
        result = self.run_workflow(
            WorkflowScenario(
                phases=("plan",),
                phase_scenarios={"plan": self.PASSING},
                final_review=FinalReviewScenario(modes=("pass",)),
                risk="extreme",
            )
        )
        self.assertEqual(result.final_status, "ERROR")
        self.assertEqual(result.reason, "SCENARIO_RISK_INVALID:extreme")

    def test_an_invalid_constructor_risk_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "INVALID_RISK"):
                E2EHarness(
                    self.ORCHESTRATION_SKILL,
                    phase="implementation",
                    workspace=Path(directory),
                    risk="extreme",
                )

    # ---- T-28: the precedence rule -------------------------------------------------

    def test_constructor_explicit_risk_survives_a_scenario_that_omits_it(self) -> None:
        """T-28. The precedence regression, pinned in all three directions."""
        phases = ("implementation",)
        # (a) preserve: constructor LOW + scenario omitted
        result = self.run_workflow(self.clean_scenario(phases), risk="low")
        self.assertEqual(result.risk, "low")
        self.assertEqual(result.risk_source, "explicit")
        # (b) the pair survived into the DISPATCHED payload, not just the result
        for event in result.sessions:
            if event.role == "worker":
                self.assertEqual(dict(event.risk_profile)["risk_level"], "low")
                self.assertEqual(dict(event.risk_profile)["risk_source"], "explicit")
        # (c) LOW behaviour actually occurred
        self.assertEqual(self.reviewer_events(result), [])
        self.assertEqual(result.reviewer_gates_skipped, ["implementation"])
        self.assertEqual(result.revalidation_dispatches, [])

    def test_the_contract_default_applies_only_when_neither_layer_supplied_one(
        self,
    ) -> None:
        """T-28 (d), first half."""
        result = self.run_workflow(self.clean_scenario(("implementation",)))
        self.assertEqual((result.risk, result.risk_source), ("high", "default"))

    def test_an_explicit_scenario_value_overrides_the_constructor(self) -> None:
        """T-28 (d), second half: the override direction still works."""
        scenario = self.clean_scenario(("implementation",))
        scenario = replace(scenario, risk="medium")
        result = self.run_workflow(scenario, risk="low")
        self.assertEqual((result.risk, result.risk_source), ("medium", "explicit"))
        self.assertEqual(len(self.reviewer_events(result)), 1)
