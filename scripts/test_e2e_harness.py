#!/usr/bin/env python3
"""Deterministic fake-agent E2E scenarios for the shared workflow policy."""

from __future__ import annotations

import ast
import inspect
import json
import re
import shutil
import tempfile
import unittest
from os import environ
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path

from scripts.task_context import RISK_CONTEXT_KEYS
import scripts.e2e_harness as e2e_module
from scripts import decision_gate, run_logging
import scripts.task_context as task_context_module
from scripts.final_report import ORCHESTRATION_SKILL, render_final_report
from scripts.orca_runtime_harness import OrcaRuntimeHarness

# The log-writing runtime's process-boundary stub lives in the runtime contract
# tests; importing it here keeps ONE definition of what a stubbed orca call returns.
from scripts.test_orca_runtime_contract import RecordingExec as _RecordingExec
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
from scripts.quality_profile import (
    DEFAULT_PROFILE_PATH,
    PROFILE_STATUS_ABSENT,
    PROFILE_STATUS_LOADED,
    QualityProfileResolution,
)
from scripts.agent_profile import (
    PROJECT_PROFILE_RELATIVE_PATH,
    RUNTIME_ORCHESTRATION,
    SELECTION_SELECTED,
    SOURCE_PROJECT_LOCAL,
    AgentProfileSelection,
    load_agent_profiles_text,
    materialize_run_routing,
)
from scripts.task_context import (
    BOUNDARY_RECEIPT_HEADING,
    BOUNDARY_RECEIPT_PREFIX,
    CANONICAL_PHASES,
    FINAL_REVIEW_PHASE,
    QUALITY_GATE_KEYS,
    QUALITY_GATE_RECEIPT_KEY,
    REVIEWER_CONTEXT_KEYS,
    REVIEWER_CONTEXT_RECEIPT_KEY,
    SPECIALIZED_PHASES,
    SPEC_VALUE_SEPARATOR,
    TASK_BOUNDARY_KEYS,
    TASK_SPEC_END_MARKER,
    build_agent_routing_context,
    build_quality_gate_context,
    build_reviewer_context,
    build_risk_context,
    build_task_boundary,
    phase_artifact_contract,
    render_task_spec,
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

MULTIPHASE_PROFILE = (
    "version: 1\n"
    "profiles:\n"
    "  diverse:\n"
    "    defaults:\n      worker: claude\n      reviewer: codex\n"
    "    phases:\n"
    "      design:\n        worker: claude\n        reviewer: claude\n"
    "      implementation:\n        worker: codex\n        reviewer: codex\n"
    "      bugfix:\n        worker: codex\n        reviewer: claude\n"
    "      refactoring:\n        worker: claude\n        reviewer: codex\n"
    "    final_review:\n      reviewer: claude-gemma\n"
)


class MultiPhaseRoutingConsumptionTests(unittest.TestCase):
    """I-001-R1: every requested phase, and the Final Review, must consume the routing.

    The earlier tests all ran a single phase, which is exactly why a routing
    materialized for the constructor's one phase looked correct: with one phase the
    wrong set and the right set are the same set.
    """

    def run_workflow(self, skill_path: Path, phases, *, profile="diverse"):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".orca").mkdir(parents=True, exist_ok=True)
            (workspace / ".orca" / "agent-profiles.yaml").write_text(
                MULTIPHASE_PROFILE, encoding="utf-8"
            )
            harness = E2EHarness(
                skill_path,
                phase=phases[0],
                workspace=workspace,
                agent_profile=profile,
            )
            result = harness.run_workflow(
                WorkflowScenario(
                    phases=phases,
                    phase_scenarios={
                        phase: FakeScenario(("complete",), ("pass",))
                        for phase in phases
                    },
                    final_review=FinalReviewScenario(modes=("pass",)),
                )
            )
            return result, harness

    def routing_by_phase(self, result):
        return {
            (event.phase, event.role): dict(event.agent_routing)
            for event in result.sessions
            if event.agent_routing
        }

    def test_every_canonical_phase_gets_its_own_resolved_worker(self) -> None:
        phases = ("design", "implementation")
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                result, _ = self.run_workflow(skill_path, phases)

                self.assertEqual(result.final_status, "COMPLETED", result.reason)
                blocks = self.routing_by_phase(result)
                # The defect this test exists for: with a single-phase
                # materialization the second phase rendered not_applicable.
                self.assertEqual(blocks[("design", "worker")]["phase_worker"], "claude")
                self.assertEqual(
                    blocks[("implementation", "worker")]["phase_worker"], "codex"
                )
                for (phase, role), block in blocks.items():
                    # The final-review record legitimately has no phase Worker; it
                    # is keyed under the last phase because that is the harness's
                    # session phase, so exclude it by ROLE rather than by phase.
                    if phase in phases and role != "final_review":
                        self.assertNotEqual(block["phase_worker"], "not_applicable")

    def test_the_same_role_changes_identity_between_phases(self) -> None:
        """Which is precisely when the reuse gate must refuse to reuse a session."""
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                result, _ = self.run_workflow(
                    skill_path, ("design", "implementation")
                )
                blocks = self.routing_by_phase(result)

                self.assertNotEqual(
                    blocks[("design", "worker")]["phase_worker"],
                    blocks[("implementation", "worker")]["phase_worker"],
                )

    def test_specialized_phases_route_the_same_way(self) -> None:
        for phase, worker in (("bugfix", "codex"), ("refactoring", "claude")):
            for skill_path in SKILL_PATHS:
                with self.subTest(skill=skill_path.parent.name, phase=phase):
                    result, _ = self.run_workflow(skill_path, (phase,))

                    blocks = self.routing_by_phase(result)
                    self.assertEqual(blocks[(phase, "worker")]["phase_worker"], worker)

    def test_the_final_review_dispatch_consumes_the_final_reviewer(self) -> None:
        """Orchestration only: the loop skill has no such gate to route."""
        orchestration = [
            path
            for path in SKILL_PATHS
            if not path.parent.name.endswith("-loop")
        ][0]

        result, _ = self.run_workflow(orchestration, ("implementation",))

        final = [
            dict(event.agent_routing)
            for event in result.sessions
            if event.role == "final_review" and event.agent_routing
        ]

        self.assertTrue(final, "the final review session recorded no routing")
        self.assertEqual(final[0]["final_reviewer"], "claude-gemma")
        # A Final Review attempt has no Worker, and the block says so.
        self.assertEqual(final[0]["phase_worker"], "not_applicable")

    def test_the_final_reviewer_may_differ_from_every_phase_reviewer(self) -> None:
        orchestration = [
            path for path in SKILL_PATHS if not path.parent.name.endswith("-loop")
        ][0]

        result, _ = self.run_workflow(orchestration, ("design", "implementation"))
        blocks = self.routing_by_phase(result)
        final = [
            dict(event.agent_routing)
            for event in result.sessions
            if event.role == "final_review" and event.agent_routing
        ][0]

        phase_reviewers = {
            block["phase_reviewer"]
            for key, block in blocks.items()
            if key[1] == "reviewer"
        }

        self.assertNotIn(final["final_reviewer"], phase_reviewers)

    def test_a_legacy_workflow_records_no_routing_on_any_session(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                result, _ = self.run_workflow(
                    skill_path, ("design", "implementation"), profile=None
                )

                self.assertTrue(result.sessions)
                self.assertTrue(
                    all(event.agent_routing == () for event in result.sessions)
                )


class SameCommandSessionIsolationTests(unittest.TestCase):
    """OS-4 verification item 8, and the safety claim D4 rests on.

    A profile is allowed to route a phase's Worker and Reviewer to the SAME agent
    command -- MULTIPHASE_PROFILE does exactly that for `design` (claude/claude) and
    `implementation` (codex/codex). What must still hold is that they are different
    SESSIONS: the relaxation removed a command-inequality check, and the invariant it
    was mistakenly guarding is owned separately by the role condition.

    Nothing asserted this before. Every existing session-isolation test predates
    profiles and runs with two different commands, so it would pass even if a
    same-command profile collapsed the two roles onto one session.
    """

    def run_phase(self, skill_path: Path, phase: str, *, profile="diverse"):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            if profile is not None:
                (workspace / ".orca").mkdir(parents=True, exist_ok=True)
                (workspace / ".orca" / "agent-profiles.yaml").write_text(
                    MULTIPHASE_PROFILE, encoding="utf-8"
                )
            harness = E2EHarness(
                skill_path,
                phase=phase,
                workspace=workspace,
                agent_profile=profile,
            )
            result = harness.run_workflow(
                WorkflowScenario(
                    phases=(phase,),
                    phase_scenarios={phase: FakeScenario(("complete",), ("pass",))},
                    final_review=FinalReviewScenario(modes=("pass",)),
                )
            )
            return result, harness

    def test_a_same_command_phase_still_uses_two_sessions(self) -> None:
        for phase in ("design", "implementation"):
            for skill_path in SKILL_PATHS:
                with self.subTest(skill=skill_path.parent.name, phase=phase):
                    result, _ = self.run_phase(skill_path, phase)

                    worker = [e for e in result.sessions if e.role == "worker"]
                    reviewer = [e for e in result.sessions if e.role == "reviewer"]
                    self.assertTrue(worker and reviewer)
                    # The profile routes both roles to one command for this phase...
                    block = dict(worker[0].agent_routing)
                    self.assertEqual(block["phase_worker"], block["phase_reviewer"])
                    # ...and the two roles are still separate sessions.
                    self.assertNotEqual(
                        worker[0].session_id, reviewer[0].session_id
                    )

    def test_no_session_is_ever_shared_between_the_two_roles(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                result, _ = self.run_phase(skill_path, "design")

                worker_sessions = {
                    e.session_id for e in result.sessions if e.role == "worker"
                }
                reviewer_sessions = {
                    e.session_id for e in result.sessions if e.role == "reviewer"
                }

                self.assertTrue(worker_sessions)
                self.assertTrue(reviewer_sessions)
                self.assertEqual(worker_sessions & reviewer_sessions, set())


class CorrectionRoutingStabilityTests(unittest.TestCase):
    """OS-4 verification item 10: correction and downstream revalidation must read
    the run's ONE materialized routing, never re-resolve.

    The existing stability tests cover the QUALITY profile through the same rounds;
    the agent routing had no equivalent, so a correction round that re-read the
    profile file would not have been caught.
    """

    PASSING = FakeScenario(("complete",), ("pass",))

    def run_scenario(self, skill_path: Path, scenario: WorkflowScenario):
        """Run a workflow whose rounds go past the phase gate.

        The profile file is NOT mutated here. run_workflow() materializes the routing
        for the scenario's phase set before its first dispatch -- that IS the run's
        resolution point -- so a rewrite before that call would be picked up
        legitimately and would prove nothing. The claim these tests make instead is
        the one that matters after that point: every later round reads the same
        resolution. The file-mutation angle is covered at unit level by
        test_agent_profile.MaterializationTests.
        """
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / ".orca").mkdir(parents=True, exist_ok=True)
            (workspace / ".orca" / "agent-profiles.yaml").write_text(
                MULTIPHASE_PROFILE, encoding="utf-8"
            )
            harness = E2EHarness(
                skill_path,
                phase=scenario.phases[0],
                workspace=workspace,
                agent_profile="diverse",
            )
            return harness.run_workflow(scenario), harness

    def blocks_for(self, result, role: str):
        return [
            dict(event.agent_routing)
            for event in result.sessions
            if event.role == role and event.agent_routing
        ]

    def test_a_correction_round_uses_the_same_routing_as_its_phase_gate(self) -> None:
        orchestration = [
            p for p in SKILL_PATHS if not p.parent.name.endswith("-loop")
        ][0]
        scenario = WorkflowScenario(
            phases=("analysis", "implementation"),
            phase_scenarios={
                "analysis": self.PASSING,
                "implementation": self.PASSING,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"), findings=((("R1", "implementation"),), ())
            ),
            correction_scenarios={
                ("implementation", 1): FakeScenario(
                    worker_modes=("correction", "correction"),
                    reviewer_modes=("fail", "pass"),
                    reviewer_findings=(("X1",), ()),
                    worker_resolutions=({"R1": "RESOLVED"}, {"X1": "RESOLVED"}),
                ),
            },
        )

        result, _ = self.run_scenario(orchestration, scenario)

        self.assertEqual(result.final_status, "COMPLETED", result.reason)
        self.assertTrue(result.correction_dispatches)
        # Every implementation-phase Worker dispatch -- the phase gate AND the
        # correction rounds -- carries the same block, byte for byte.
        implementation = [
            tuple(sorted(dict(event.agent_routing).items()))
            for event in result.sessions
            if event.role == "worker"
            and event.phase == "implementation"
            and event.agent_routing
        ]
        self.assertGreater(len(implementation), 1, "no correction round dispatched")
        self.assertEqual(len(set(implementation)), 1)
        self.assertEqual(dict(implementation[0])["phase_worker"], "codex")

    def test_downstream_revalidation_uses_the_same_routing(self) -> None:
        orchestration = [
            p for p in SKILL_PATHS if not p.parent.name.endswith("-loop")
        ][0]
        scenario = WorkflowScenario(
            phases=("analysis", "implementation"),
            phase_scenarios={
                "analysis": self.PASSING,
                "implementation": self.PASSING,
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"), findings=((("R1", "analysis"),), ())
            ),
            correction_scenarios={
                ("analysis", 1): FakeScenario(
                    worker_modes=("correction",),
                    reviewer_modes=("pass",),
                    worker_resolutions=({"R1": "RESOLVED"},),
                ),
            },
            # The key's iteration is final_review_iterations at the moment the
            # correction ran, i.e. attempt 1 -- see h2_scenario's note.
            revalidation_scenarios={
                ("implementation", 1): FakeScenario(("complete",), ("pass",)),
            },
        )

        result, _ = self.run_scenario(orchestration, scenario)

        self.assertTrue(
            result.revalidation_dispatches, "T5a did not run; the test proves nothing"
        )
        # The revalidation round is an implementation-phase dispatch like any other,
        # and it carries the same block the phase gate did.
        implementation = [
            tuple(sorted(dict(event.agent_routing).items()))
            for event in result.sessions
            if event.role == "worker"
            and event.phase == "implementation"
            and event.agent_routing
        ]
        self.assertGreater(len(implementation), 1, "no revalidation dispatched")
        self.assertEqual(len(set(implementation)), 1)
        # The corrected phase keeps its own routing too.
        analysis = {
            dict(event.agent_routing)["phase_worker"]
            for event in result.sessions
            if event.role == "worker"
            and event.phase == "analysis"
            and event.agent_routing
        }
        self.assertEqual(analysis, {"claude"})

    def test_the_final_review_also_reads_the_pre_run_routing(self) -> None:
        orchestration = [
            p for p in SKILL_PATHS if not p.parent.name.endswith("-loop")
        ][0]
        scenario = WorkflowScenario(
            phases=("implementation",),
            phase_scenarios={"implementation": self.PASSING},
            final_review=FinalReviewScenario(modes=("pass",)),
        )

        result, _ = self.run_scenario(orchestration, scenario)

        final = self.blocks_for(result, "final_review")
        self.assertTrue(final)
        self.assertEqual(final[0]["final_reviewer"], "claude-gemma")


PROFILE_DOCUMENT = (
    "version: 1\n"
    "profiles:\n"
    "  diverse:\n"
    "    defaults:\n      worker: claude\n      reviewer: codex\n"
    "    phases:\n"
    "      implementation:\n        worker: codex\n        reviewer: codex\n"
    "    final_review:\n      reviewer: codex\n"
)


LEGACY_BASELINE = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "fixtures"
    / "legacy_baseline"
    / "pre_os4_artifacts.json"
)

# The three representative workflows the golden fixture covers, for both skills.
GOLDEN_WORKFLOWS = {
    "single_canonical": ("implementation",),
    "multi_canonical": ("design", "implementation"),
    "specialized_bugfix": ("bugfix",),
}


def _normalize_artifact(text: str, workspace: Path) -> str:
    """Strip what legitimately varies between two runs of the same scenario.

    The workspace path, ISO timestamps and orca-assigned ids are per-run facts and
    were never part of the contract; everything else must match exactly.
    """
    out = text.replace(str(workspace), "<WORKSPACE>")
    lines = []
    for line in out.splitlines():
        tokens = []
        for token in line.split():
            if token.count("-") >= 2 and token.count(":") >= 2:
                token = "<TS>"
            tokens.append(token)
        lines.append(" ".join(tokens))
    return "\n".join(lines)


# ---- OS-29: the additions the two PRE-OS-4 / PRE-OS-22 goldens cannot contain ------
# OS-29 is a transition no-op and deliberately NOT an artifact no-op: a fully-CLEAR
# run makes the same dispatches, charges the same iterations and reaches the same
# terminal, but it additionally carries the agent's gate declaration inside the
# Reviewer's current_delta and two sparse columns in ORCHESTRATOR_LOG.md. Those two
# additions are enumerated here and removed before the byte comparison, so the
# pre-OS-4 / pre-OS-22 claim keeps its full strength for EVERYTHING ELSE instead of
# being retired or having its golden quietly regenerated.
#
# Each stripper RAISES when the addition it removes is absent. That is the
# non-vacuity half and it is not optional: a stripper that silently accepted a build
# emitting no declaration at all would turn these byte-identity tests into a
# guarantee that OS-29 never happened.
OS29_LOG_COLUMNS = ("decision_state", "decision_reason_code")
OS29_LOG_EVENTS = (
    run_logging.EVENT_DECISION_RECORD_WRITTEN,
    run_logging.EVENT_DECISION_GATE_REFUSED,
    run_logging.EVENT_DECISION_BLOCK,
)


# One regex rather than a line filter: the Reviewer's `current_delta` carries the
# Worker's whole result, and both canonicalizers collapse it onto ONE spec line, so
# the declaration and its fenced record appear INLINE and a line-oriented stripper
# would silently remove nothing.
OS29_SPEC_DECLARATION = re.compile(
    rf"{decision_gate.GATE_STATE_FIELD}:\s*[A-Z_]+\s*```decision-gate\b.*?```[ \n]?",
    re.DOTALL,
)


def strip_os29_spec_additions(spec: str) -> str:
    """One dispatched Task spec, minus the OS-29 gate declaration it now quotes."""
    return OS29_SPEC_DECLARATION.sub("", spec)


def strip_os29_spec_list(specs: list[str]) -> list[str]:
    """Every spec, with the non-vacuity guard applied to the LIST.

    The guard belongs here rather than on each spec: a Worker's own spec legitimately
    carries no declaration (it has not produced one yet), and only a Reviewer's spec
    quotes one back. So "at least one" is the honest claim, and "every one" would be
    false.
    """
    if not any(f"{decision_gate.GATE_STATE_FIELD}:" in spec for spec in specs):
        raise RuntimeError(
            "no dispatched spec carried an OS-29 gate declaration; the stripper "
            "would make this byte-identity comparison vacuous"
        )
    return [strip_os29_spec_additions(spec) for spec in specs]


def strip_os29_log_additions(log: str) -> str:
    """One ORCHESTRATOR_LOG.md, minus the two sparse OS-29 columns and its own rows."""
    if not log:
        return log
    lines = log.splitlines()
    header = [cell.strip() for cell in lines[0].strip().strip("|").split("|")]
    missing = [column for column in OS29_LOG_COLUMNS if column not in header]
    if missing:
        raise RuntimeError(
            f"ORCHESTRATOR_LOG.md is missing the OS-29 columns {missing}; the "
            "stripper would make this byte-identity comparison vacuous"
        )
    drop = {header.index(column) for column in OS29_LOG_COLUMNS}
    event_index = header.index("event")
    kept: list[str] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != len(header):
            kept.append(line)
            continue
        if cells[event_index] in OS29_LOG_EVENTS:
            continue
        remaining = [cell for index, cell in enumerate(cells) if index not in drop]
        kept.append("| " + " | ".join(remaining) + " |")
    return "\n".join(kept)


def capture_orchestrator_log(skill_name, phases, routing=None) -> str:
    """The real ORCHESTRATOR_LOG.md for THIS fixture's own phase sequence.

    Earlier revisions called a fixed single-phase run and pasted its log onto all six
    fixtures, so a bugfix fixture's "log" recorded phase=implementation. The phases
    are arguments now, and one worker attempt is dispatched per requested phase, so
    each fixture's log is that fixture's workflow.

    "" for the loop skill, and that is the contract rather than a gap: that runtime
    has no run-scoped log at all (DESIGN: its evidence medium is the final report).
    Manufacturing one here would report a medium the skill does not have.
    """
    if skill_name != ORCHESTRATION_SKILL:
        return ""
    with tempfile.TemporaryDirectory() as directory:
        artifacts = Path(directory)
        recorder = _RecordingExec(
            results={
                "run-create": {"run": {"id": "run_golden"}},
                "check": _RecordingExec.ACCEPTED_DONE,
            }
        )
        kwargs = {} if routing is None else {"agent_routing": routing}
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(artifacts, **kwargs)
        harness._exec_orca = recorder
        harness.start_run(f"golden {'+'.join(phases)}", requested_phases=phases)
        for iteration, phase in enumerate(phases, start=1):
            harness.run_attempt("worker", iteration, "complete", phase=phase)
        harness.log_run_status("COMPLETED")
        log = artifacts / "artifacts" / "runs" / "run_golden" / "ORCHESTRATOR_LOG.md"
        return _normalize_artifact(
            strip_os29_log_additions(log.read_text(encoding="utf-8")), artifacts
        )


def capture_legacy_artifacts(
    repo_root: Path, skill_name: str, workflow: str, *, profile: str | None = None
) -> dict:
    """The three artifacts a run leaves behind, each from its own real producer and
    each for THIS fixture's own skill and workflow.

    task_specs        full rendered text, via a render_task_spec wrapper
    orchestrator_log  the real file, replaying this fixture's phase sequence
    final_report      this skill's OWN SKILL.md template, via scripts/final_report.py

    This exact function, run inside a `git archive` checkout of the last pre-OS-4
    commit (with scripts/final_report.py copied in, since the renderer is new), built
    scripts/fixtures/legacy_baseline/pre_os4_artifacts.json.
    """
    phases = GOLDEN_WORKFLOWS[workflow]
    skill_path = repo_root / skill_name / "SKILL.md"
    rendered: list[str] = []
    original = e2e_module.render_task_spec

    def recording(*args, **kwargs):
        spec = original(*args, **kwargs)
        rendered.append(spec)
        return spec

    e2e_module.render_task_spec = recording
    try:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            if profile is not None:
                (workspace / ".orca").mkdir(parents=True, exist_ok=True)
                (workspace / ".orca" / "agent-profiles.yaml").write_text(
                    MULTIPHASE_PROFILE, encoding="utf-8"
                )
            kwargs = {} if profile is None else {"agent_profile": profile}
            harness = E2EHarness(
                skill_path,
                phase=phases[0],
                workspace=workspace,
                run_id="run_golden",
                **kwargs,
            )
            result = harness.run_workflow(
                WorkflowScenario(
                    phases=phases,
                    phase_scenarios={
                        phase: FakeScenario(("complete",), ("pass",))
                        for phase in phases
                    },
                    final_review=FinalReviewScenario(modes=("pass",)),
                    run_id="run_golden",
                )
            )
            routing = getattr(harness, "agent_routing", None)
            captured = {
                "task_specs": strip_os29_spec_list(
                    [_normalize_artifact(spec, workspace) for spec in rendered]
                ),
                "orchestrator_log": capture_orchestrator_log(
                    skill_name, phases, routing
                ),
                "final_report": _normalize_artifact(
                    render_final_report(result, skill_name=skill_name), workspace
                ),
            }
            if profile is not None:
                captured["routing_blocks"] = [
                    [list(pair) for pair in event.agent_routing]
                    for event in result.sessions
                ]
            return captured
    finally:
        e2e_module.render_task_spec = original


def _template_from_skill(skill_path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The Final Result template's header keys and section headings, read from the
    SKILL.md fence itself.

    The contract is the document. Parsing it here means the renderer is checked
    against what the skill actually promises rather than against a second copy of
    someone's reading of it.
    """
    text = skill_path.read_text(encoding="utf-8")
    start = text.index("# Final Result")
    fence = text.index("\n```", start)
    block = text[start:fence]
    keys = []
    sections = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            sections.append(stripped)
        elif stripped and ":" in stripped and stripped.split(":", 1)[0].isupper():
            key = stripped.split(":", 1)[0]
            if key not in keys:
                keys.append(key)
    return tuple(keys), tuple(sections)


class FinalReportContractTests(unittest.TestCase):
    """The renderer must emit each skill's OWN template, parsed from its SKILL.md.

    Not one common format: the loop template carries no risk axis and no Final
    Adversarial Review block, and the two differ on `## Unit Tests / Validation`
    versus separate `## Unit Tests` and `## Validation` sections. A renderer that
    emitted RISK for the loop skill would be reporting a lifecycle it does not run.
    """

    def report_for(self, skill_path: Path) -> str:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            harness = E2EHarness(
                skill_path, phase="implementation", workspace=workspace
            )
            result = harness.run_workflow(
                WorkflowScenario(
                    phases=("implementation",),
                    phase_scenarios={
                        "implementation": FakeScenario(("complete",), ("pass",))
                    },
                    final_review=FinalReviewScenario(modes=("pass",)),
                )
            )
        return render_final_report(result, skill_name=skill_path.parent.name)

    def test_every_template_header_key_is_rendered(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                keys, _ = _template_from_skill(skill_path)
                report = self.report_for(skill_path)

                self.assertTrue(keys)
                for key in keys:
                    with self.subTest(key=key):
                        self.assertIn(f"{key}:", report)

    def test_every_template_section_is_rendered(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                _, sections = _template_from_skill(skill_path)
                report = self.report_for(skill_path)

                self.assertTrue(sections)
                for section in sections:
                    with self.subTest(section=section):
                        self.assertIn(section, report)

    def test_the_renderer_emits_no_key_the_template_does_not_declare(self) -> None:
        """The direction that catches a common format leaking into both skills."""
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                keys, _ = _template_from_skill(skill_path)
                report = self.report_for(skill_path)

                rendered = {
                    line.split(":", 1)[0]
                    for line in report.splitlines()
                    if ":" in line and line.split(":", 1)[0].isupper() and line[:1].isupper()
                }
                self.assertEqual(rendered - set(keys), set())

    def test_the_loop_report_carries_no_risk_axis_or_final_review_gate(self) -> None:
        loop = [p for p in SKILL_PATHS if p.parent.name.endswith("-loop")][0]

        report = self.report_for(loop)

        for absent in ("RISK:", "RISK_SOURCE:", "FINAL_REVIEW_ITERATIONS:",
                       "## Final Adversarial Review"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, report)
        self.assertIn("RESULT:", report)

    def test_the_orchestration_report_carries_the_final_review_block(self) -> None:
        orchestration = [
            p for p in SKILL_PATHS if not p.parent.name.endswith("-loop")
        ][0]

        report = self.report_for(orchestration)

        for key in ("RISK:", "RISK_SOURCE:", "FINAL_REVIEW_ITERATIONS:",
                    "FINAL_REVIEW:", "FINAL_REVIEW_TASKS:", "FINAL_FINDINGS:",
                    "FINAL_REVIEW_REVALIDATIONS:", "## Orca Orchestration State"):
            with self.subTest(key=key):
                self.assertIn(key, report)

    def test_an_unknown_skill_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            render_final_report(object(), skill_name="something-else")


class LegacyByteIdentityTests(unittest.TestCase):
    """DESIGN T14: an omitted-profile run must reproduce the PRE-OS-4 artifacts.

    The baseline is not "what this code produces today" -- that would only prove the
    code agrees with itself. It is a golden capture taken from commit 8f3cfa3, the
    last commit before Agent Profile existed, and it is compared character for
    character. See scripts/fixtures/legacy_baseline/README.md.

    Two skills x three workflows (single canonical phase, two canonical phases, a
    specialized bugfix phase), and the non-vacuity half runs the same fixtures with a
    profile selected so a comparison that could never fail would be caught.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = json.loads(LEGACY_BASELINE.read_text(encoding="utf-8"))
        cls.repo_root = Path(__file__).resolve().parents[1]

    def fixtures(self):
        for skill_path in SKILL_PATHS:
            for workflow in GOLDEN_WORKFLOWS:
                yield skill_path.parent.name, workflow

    def test_the_baseline_covers_both_skills_and_three_workflows(self) -> None:
        """A shrunken fixture would silently shrink the claim."""
        self.assertEqual(len(self.golden), 6)
        for skill, workflow in self.fixtures():
            self.assertIn(f"{skill}::{workflow}", self.golden)

    def test_every_dispatched_spec_matches_the_pre_os4_capture(self) -> None:
        """Full spec text, character for character -- not a structured summary."""
        for skill, workflow in self.fixtures():
            with self.subTest(skill=skill, workflow=workflow):
                current = capture_legacy_artifacts(self.repo_root, skill, workflow)
                golden = self.golden[f"{skill}::{workflow}"]["task_specs"]

                self.assertTrue(current["task_specs"])
                self.assertEqual(len(current["task_specs"]), len(golden))
                for index, (actual, expected) in enumerate(
                    zip(current["task_specs"], golden)
                ):
                    with self.subTest(spec=index):
                        self.assertEqual(actual, expected)

    def test_the_orchestrator_log_matches_the_pre_os4_capture(self) -> None:
        """Real log bytes for the runtime that has a log, and none for the one that
        does not -- an empty-vs-empty comparison would prove nothing on its own."""
        for skill, workflow in self.fixtures():
            with self.subTest(skill=skill, workflow=workflow):
                current = capture_legacy_artifacts(self.repo_root, skill, workflow)
                golden = self.golden[f"{skill}::{workflow}"]["orchestrator_log"]

                if skill == ORCHESTRATION_SKILL:
                    self.assertTrue(golden.strip(), "the golden log must not be empty")
                    self.assertIn("run_start", golden)
                    self.assertIn("dispatch_settled", golden)
                else:
                    # DESIGN: the loop runtime has no run-scoped log at all. Its
                    # evidence medium is the final report, checked below.
                    self.assertEqual(golden, "")
                self.assertEqual(current["orchestrator_log"], golden)

    def test_each_orchestration_log_reflects_its_own_workflow(self) -> None:
        """The defect this replaced: one fixed single-phase log pasted onto all six
        fixtures, so a bugfix fixture's log recorded phase=implementation."""
        logs = {}
        for workflow, phases in GOLDEN_WORKFLOWS.items():
            golden = self.golden[f"{ORCHESTRATION_SKILL}::{workflow}"][
                "orchestrator_log"
            ]
            logs[workflow] = golden
            with self.subTest(workflow=workflow):
                run_start = [
                    line for line in golden.splitlines() if "run_start" in line
                ][0]
                self.assertIn(",".join(phases), run_start)
                settled = [
                    line for line in golden.splitlines() if "dispatch_settled" in line
                ]
                self.assertEqual(len(settled), len(phases))
                for phase in phases:
                    self.assertTrue(
                        any(f"| {phase} |" in line for line in settled),
                        f"{workflow}: no dispatch row for {phase}",
                    )

        # And they are genuinely three different logs, not one reused three times.
        self.assertEqual(len(set(logs.values())), len(logs))

    def test_the_loop_evidence_lives_in_its_report_not_a_log(self) -> None:
        for workflow in GOLDEN_WORKFLOWS:
            with self.subTest(workflow=workflow):
                fixture = self.golden[f"orca-worker-reviewer-loop::{workflow}"]

                self.assertEqual(fixture["orchestrator_log"], "")
                self.assertTrue(fixture["final_report"].startswith("# Final Result"))

    def test_the_final_report_matches_the_pre_os4_capture(self) -> None:
        """The renderer's whole output text, not a dict of remembered fields."""
        for skill, workflow in self.fixtures():
            with self.subTest(skill=skill, workflow=workflow):
                current = capture_legacy_artifacts(self.repo_root, skill, workflow)
                golden = self.golden[f"{skill}::{workflow}"]["final_report"]

                self.assertTrue(golden.startswith("# Final Result"))
                self.assertNotIn("AGENT_PROFILE", golden)
                self.assertEqual(current["final_report"], golden)

    def test_no_dispatched_spec_carries_a_routing_block(self) -> None:
        """Stated directly as well as implied by the golden comparison."""
        for skill, workflow in self.fixtures():
            with self.subTest(skill=skill, workflow=workflow):
                skill_path = self.repo_root / skill / "SKILL.md"
                with tempfile.TemporaryDirectory() as directory:
                    workspace = Path(directory)
                    phases = GOLDEN_WORKFLOWS[workflow]
                    harness = E2EHarness(
                        skill_path, phase=phases[0], workspace=workspace
                    )
                    result = harness.run_workflow(
                        WorkflowScenario(
                            phases=phases,
                            phase_scenarios={
                                phase: FakeScenario(("complete",), ("pass",))
                                for phase in phases
                            },
                            final_review=FinalReviewScenario(modes=("pass",)),
                        )
                    )

                    self.assertTrue(
                        all(event.agent_routing == () for event in result.sessions)
                    )
                    self.assertEqual(result.agent_profile_report, ())

    def test_a_selected_profile_changes_all_three_surfaces(self) -> None:
        """The non-vacuity half: the comparisons above must be able to fail."""
        for skill, workflow in self.fixtures():
            with self.subTest(skill=skill, workflow=workflow):
                baseline = self.golden[f"{skill}::{workflow}"]
                legacy = capture_legacy_artifacts(self.repo_root, skill, workflow)
                selected = capture_legacy_artifacts(
                    self.repo_root, skill, workflow, profile="diverse"
                )

                # 1. every dispatch now carries a routing block; the legacy run of
                #    the same fixture carries none. `sessions` deliberately keeps the
                #    pre-OS-4 shape (that is the golden contract), so the routing
                #    surface is compared on its own.
                self.assertNotIn("routing_blocks", legacy)
                self.assertTrue(any(selected["routing_blocks"]))
                # Every phase Worker/Reviewer dispatch, specifically -- not just
                # "at least one session somewhere".
                phase_blocks = [
                    block
                    for block, session in zip(
                        selected["routing_blocks"],
                        [
                            line
                            for line in selected["final_report"].splitlines()
                            if line.startswith("- worker ")
                            or line.startswith("- reviewer ")
                            or line.startswith("- final_review ")
                        ],
                    )
                    if session.startswith(("- worker ", "- reviewer "))
                ]
                self.assertTrue(phase_blocks)
                self.assertTrue(all(phase_blocks))
                # 3. everything else is unchanged -- routing decides WHO executes,
                #    not WHAT runs or whether it passes.
                self.assertNotEqual(selected["task_specs"], baseline["task_specs"])
                # 4. the audit log gains the agent-routing rows -- for the runtime
                #    that HAS a log. The loop skill has none, and its evidence is the
                #    report checked immediately below.
                if skill == ORCHESTRATION_SKILL:
                    self.assertNotEqual(
                        selected["orchestrator_log"], baseline["orchestrator_log"]
                    )
                    self.assertIn(
                        "agent_profile_selected", selected["orchestrator_log"]
                    )
                    self.assertIn(
                        "agent_routing_resolved", selected["orchestrator_log"]
                    )
                else:
                    self.assertEqual(selected["orchestrator_log"], "")
                # 5. the report gains its conditional lines.
                self.assertNotEqual(
                    selected["final_report"], baseline["final_report"]
                )
                self.assertIn("AGENT_PROFILE:", selected["final_report"])
                self.assertIn("AGENT_ROUTING:", selected["final_report"])
                # 6. and the run still completes: routing changes WHO executes, not
                #    WHAT runs or whether it passes.
                self.assertIn("STATUS: COMPLETED", selected["final_report"])
                self.assertIn("STATUS: COMPLETED", baseline["final_report"])


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

    def fail_then_pass_scenario_on_last_phase(
        self, phases: tuple[str, ...]
    ) -> WorkflowScenario:
        """Final Review FAILs once, charged to the LAST requested phase, then passes.

        Unlike `fail_then_pass_scenario` (charged to `phases[0]`), the corrected
        phase here has no requested phase after it in canonical order, so section
        17's downstream set D is empty and T5a has nothing to revalidate.
        """
        correction = FakeScenario(
            ("correction",),
            ("pass",),
            worker_resolutions=({"R1": "RESOLVED"},),
            worker_unit_test_statuses=("PASS",),
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
                modes=("fail", "pass"), findings=((("R1", phases[-1]),), ())
            ),
            correction_scenarios={(phases[-1], 1): correction},
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

    # ---- T-12: churn is LOW <= MEDIUM <= HIGH, strict MEDIUM<HIGH only when T5a --
    # ---- actually revalidates a non-empty downstream set ------------------------

    def test_churn_ordering_when_t5a_actually_revalidates_something_is_strict(
        self,
    ) -> None:
        """T-12, representative strict case: a Final Review correction is charged
        to a NON-last requested phase, so T5a's downstream set D is non-empty and
        HIGH strictly outdoes MEDIUM. This is the ONLY mechanism that makes HIGH
        churn more than MEDIUM -- see the other T-12 cases below for the (more
        common) MEDIUM == HIGH paths.
        """
        phases = ("plan", "design", "implementation")
        churn = {
            risk: self.churn(
                self.run_workflow(self.fail_then_pass_scenario(phases), risk=risk)
            )
            for risk in ("low", "medium", "high")
        }
        self.assertLess(churn["low"], churn["medium"])
        self.assertLess(churn["medium"], churn["high"])

    def test_churn_on_a_clean_first_pass_is_medium_equals_high(self) -> None:
        """T-12: no Final Review FAIL at all -> T5a never runs -> MEDIUM == HIGH."""
        phases = ("plan", "design", "implementation")
        churn = {
            risk: self.churn(self.run_workflow(self.clean_scenario(phases), risk=risk))
            for risk in ("low", "medium", "high")
        }
        self.assertLess(churn["low"], churn["medium"])
        self.assertEqual(churn["medium"], churn["high"])

    def test_churn_with_only_a_phase_local_correction_is_medium_equals_high(
        self,
    ) -> None:
        """T-12 (external review MAJOR, case 1): a phase-local Reviewer FAIL/
        correction is IDENTICAL machinery at MEDIUM and HIGH -- it is not a
        Final Review correction, so it never triggers T5a. A scenario where one
        requested phase needs a phase-local correction but the Final Review still
        passes cleanly must still show MEDIUM == HIGH; only a *Final Review*
        correction with a non-empty downstream set (the test above) makes HIGH
        strictly outdo MEDIUM.
        """
        phases = ("plan", "design", "implementation")
        phase_local_correction = FakeScenario(
            worker_modes=("complete", "correction"),
            reviewer_modes=("fail", "pass"),
            reviewer_findings=(("R1",), ()),
            worker_resolutions=({}, {"R1": "RESOLVED"}),
        )
        scenario = WorkflowScenario(
            phases=phases,
            phase_scenarios={
                "plan": phase_local_correction,
                "design": self.PASSING,
                "implementation": self.PASSING_GATED,
            },
            final_review=FinalReviewScenario(modes=("pass",)),
        )
        churn = {
            risk: self.churn(self.run_workflow(scenario, risk=risk))
            for risk in ("medium", "high")
        }
        self.assertEqual(churn["medium"], churn["high"])

    def test_churn_with_final_review_correction_on_the_last_phase_is_medium_equals_high(
        self,
    ) -> None:
        """T-12 (external review MAJOR, case 2): a Final Review correction charged
        to the LAST requested phase in canonical order has an empty downstream set
        D, so T5a has nothing left to revalidate even though a correction genuinely
        happened. MEDIUM == HIGH here too -- strict inequality is not "whenever any
        correction occurs," only when D is actually non-empty.
        """
        phases = ("plan", "design", "implementation")
        churn = {
            risk: self.churn(
                self.run_workflow(
                    self.fail_then_pass_scenario_on_last_phase(phases), risk=risk
                )
            )
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


# ---- OS-22 N-1: the Final Review observability neutrality anchor -------------------
# The "before" image for section 2 of OS-22: every Task spec this repository renders,
# captured at commit 1045815 -- the last commit before OS-22 -- and compared BYTE for
# byte against the current tree.
#
# This capture path is deliberately separate from capture_legacy_artifacts() and from
# _normalize_artifact(). _normalize_artifact() splits every line on whitespace and
# rejoins it, so it silently equates specs that differ in indentation, in repeated
# interior spaces, in trailing spaces, or in the presence of a terminal newline -- and
# render_task_spec() emits all four of those as real reviewer-visible content
# (`new_claims: ` carries a trailing space when its value is empty; a worker report
# quoted into `current_delta` carries its own interior double spaces). A golden built
# on that normalizer would be a whitespace-insensitive comparison, not the identity
# claim section 2 requires. OS-4's capture and fixture are therefore left untouched.
NEUTRALITY_BASELINE = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "fixtures"
    / "os22_neutrality"
    / "pre_os22_task_specs.json"
)

# "Validate Final Adversarial Review effectiveness (#19)" -- the last commit before
# OS-22. The golden was generated inside a `git archive` checkout of it.
NEUTRALITY_COMMIT = "1045815"

# The transform between render_task_spec() and the stored bytes, versioned so a
# future change to it is a visible change to the claim rather than a silent one.
NEUTRALITY_CANONICALIZATION = "task_spec/1.0"

NEUTRALITY_RUN_ID = "run_golden"

# Spelled out rather than aliased to GOLDEN_WORKFLOWS: a later edit to the OS-4
# fixture's workflow set must not silently reshape section 2's coverage.
NEUTRALITY_WORKFLOWS = {
    "single_canonical": ("implementation",),
    "multi_canonical": ("design", "implementation"),
    "specialized_bugfix": ("bugfix",),
}

# profile=multi is required, not optional: e2e_harness renders a `final_review` spec
# only when final_review_routing_context() is not None, i.e. only under a selected
# Agent Profile. Without it family A would carry no Final Review spec at all.
NEUTRALITY_PROFILES = (("none", None), ("multi", "diverse"))

# Enumerated, closed, and each entry justified below. Nothing else is substituted.
_TASK_SPEC_SUBSTITUTIONS = (
    ("workspace_path", lambda ws: str(ws), "<WORKSPACE>"),
)

# Fail-closed residue check: if any of these survives canonicalization, the enumeration
# above is INCOMPLETE and the capture must be fixed -- never silently normalized away.
_TASK_SPEC_NONDETERMINISM_TRIPWIRES = (
    re.compile(re.escape(tempfile.gettempdir())),
    re.compile(r"/var/folders/"),
    re.compile(r"/private/(?:tmp|var)/"),
    re.compile(re.escape(str(Path(__file__).resolve().parents[1]))),
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"),   # ISO-8601 timestamp
    re.compile(r"\b(?:task|ctx|dcap|term|run)_[0-9a-f]{8,}"),  # orca-assigned ids
)


def canonicalize_task_spec(spec: str, *, workspace: Path | None) -> str:
    """The ONLY transform between render_task_spec() and the golden comparison.

    One string replacement, nothing else. No splitlines(), no split(), no join(),
    no strip(), no rstrip(), no reserialization -- every space, every run of
    spaces, every trailing space and the presence or absence of a terminal
    newline reaches the comparison exactly as render_task_spec() produced it.

    The single substitution exists because family A runs a workflow inside a
    TemporaryDirectory and exactly one reviewer-visible field carries that absolute
    path: `drill_down=(str(self.workspace),)`. Family B renders directly from
    test-owned literals and passes workspace=None, so the transform is the identity.
    """
    out = spec
    if workspace is not None:
        for _name, source, replacement in _TASK_SPEC_SUBSTITUTIONS:
            out = out.replace(source(workspace), replacement)
    for tripwire in _TASK_SPEC_NONDETERMINISM_TRIPWIRES:
        match = tripwire.search(out)
        if match is not None:
            raise AssertionError(
                f"unenumerated nondeterministic value in a captured Task spec: "
                f"{match.group(0)!r}; extend _TASK_SPEC_SUBSTITUTIONS deliberately, "
                f"never loosen the comparison"
            )
    return out


def capture_neutrality_workflow_specs(
    repo_root: Path, skill_name: str, workflow: str, *, profile: str | None = None
) -> list[str]:
    """Family A: every Task spec ONE workflow actually dispatches.

    The same recording-wrapper technique capture_legacy_artifacts() uses, and
    deliberately a separate function: this one canonicalizes byte-strictly and must
    not drift into -- or be drifted into by -- the OS-4 capture.
    """
    phases = NEUTRALITY_WORKFLOWS[workflow]
    skill_path = repo_root / skill_name / "SKILL.md"
    rendered: list[str] = []
    original = e2e_module.render_task_spec

    def recording(*args, **kwargs):
        spec = original(*args, **kwargs)
        rendered.append(spec)
        return spec

    e2e_module.render_task_spec = recording
    try:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            if profile is not None:
                (workspace / ".orca").mkdir(parents=True, exist_ok=True)
                (workspace / ".orca" / "agent-profiles.yaml").write_text(
                    MULTIPHASE_PROFILE, encoding="utf-8"
                )
            kwargs = {} if profile is None else {"agent_profile": profile}
            harness = E2EHarness(
                skill_path,
                phase=phases[0],
                workspace=workspace,
                run_id=NEUTRALITY_RUN_ID,
                **kwargs,
            )
            harness.run_workflow(
                WorkflowScenario(
                    phases=phases,
                    phase_scenarios={
                        phase: FakeScenario(("complete",), ("pass",))
                        for phase in phases
                    },
                    final_review=FinalReviewScenario(modes=("pass",)),
                    run_id=NEUTRALITY_RUN_ID,
                )
            )
            return strip_os29_spec_list(
                [canonicalize_task_spec(spec, workspace=workspace) for spec in rendered]
            )
    finally:
        e2e_module.render_task_spec = original


# The five optional-block combinations render_task_spec() can be handed. "none" is the
# pre-OS-3/OS-4 legacy shape; "all" is every block a dispatch can carry today.
NEUTRALITY_DIRECT_BLOCKS = (
    "none",
    "reviewer",
    "reviewer+quality",
    "reviewer+quality+risk",
    "all",
)

_NEUTRALITY_ROLE_PHASES = (
    ("worker", (*CANONICAL_PHASES, *SPECIALIZED_PHASES)),
    ("reviewer", (*CANONICAL_PHASES, *SPECIALIZED_PHASES)),
    ("final_reviewer", (FINAL_REVIEW_PHASE,)),
)


def _neutrality_direct_cases() -> tuple[tuple[str, str, int, str], ...]:
    cases = []
    for role, phases in _NEUTRALITY_ROLE_PHASES:
        for phase in phases:
            for iteration in (1, 2):
                for blocks in NEUTRALITY_DIRECT_BLOCKS:
                    cases.append((role, phase, iteration, blocks))
    return tuple(cases)


# Family B: the enumerated (role, phase, iteration, block-combination) matrix. Stronger
# than family A as an anchor, because it does not depend on which specs a workflow
# happens to dispatch -- a future harness change cannot silently shrink the coverage.
NEUTRALITY_DIRECT_CASES = _neutrality_direct_cases()


def _neutrality_routing():
    """One materialized RunRouting for every direct case, built from literals."""
    profiles = dict(
        load_agent_profiles_text(
            MULTIPHASE_PROFILE,
            path=PROJECT_PROFILE_RELATIVE_PATH,
            source=SOURCE_PROJECT_LOCAL,
        )
    )
    selection = AgentProfileSelection(
        status=SELECTION_SELECTED, name="diverse", profile=profiles["diverse"]
    )
    return materialize_run_routing(
        runtime=RUNTIME_ORCHESTRATION,
        selection=selection,
        requested_phases=(*CANONICAL_PHASES, *SPECIALIZED_PHASES),
        risk="high",
    )


def _neutrality_direct_spec(role: str, phase: str, iteration: int, blocks: str) -> str:
    """One render_task_spec() call from test-owned literals only.

    The literals are chosen to carry the bytes _normalize_artifact() would destroy:
    an empty multi-value field (which renders as `<key>: ` with a trailing space) and
    a delta line with an interior double space.
    """
    boundary = build_task_boundary(
        current_role=role,
        current_phase=phase,
        current_iteration=iteration,
        artifact_contract=phase_artifact_contract(
            role=role, phase=phase, run_id=NEUTRALITY_RUN_ID
        ),
        relevant_previous_findings=()
        if iteration == 1
        else ("R1 blocking: precedence inverted", "R2 non-blocking: naming"),
    )
    reviewer_context = None
    quality_gate = None
    risk_context = None
    agent_routing = None
    if blocks != "none":
        reviewer_context = build_reviewer_context(
            original_objective=f"neutrality:{phase}",
            current_phase=phase,
            approved_baseline=()
            if iteration == 1
            else (f"artifacts/runs/{NEUTRALITY_RUN_ID}/{phase.upper()}.md",),
            current_delta=("worker report line one", "worker  report  line  two"),
            new_claims=(),
            previous_findings=() if iteration == 1 else (("R1", "resolved"),),
            validation=("unit tests: PASS",),
            drill_down=("<WORKSPACE>",),
        )
    if blocks in ("reviewer+quality", "reviewer+quality+risk", "all"):
        quality_gate = build_quality_gate_context(
            resolution=QualityProfileResolution(
                status=PROFILE_STATUS_ABSENT, path=DEFAULT_PROFILE_PATH
            ),
            current_phase=phase,
            requested_phases=("implementation",)
            if phase == FINAL_REVIEW_PHASE
            else (),
        )
    if blocks in ("reviewer+quality+risk", "all"):
        risk_context = build_risk_context(
            risk="low" if iteration == 1 else "high",
            risk_source="default" if iteration == 1 else "explicit",
            current_phase=phase,
        )
    if blocks == "all":
        agent_routing = build_agent_routing_context(
            routing=_neutrality_routing(), current_phase=phase
        )
    return render_task_spec(
        f"{role} {phase} iteration {iteration}",
        boundary,
        reviewer_context,
        quality_gate,
        risk_context,
        agent_routing,
    )


def neutrality_direct_key(role: str, phase: str, iteration: int, blocks: str) -> str:
    return f"{role}|{phase}|iter{iteration}|{blocks}"


def capture_neutrality_direct_specs() -> dict:
    """Family B, keyed by neutrality_direct_key(). workspace=None -> identity."""
    return {
        neutrality_direct_key(*case): canonicalize_task_spec(
            _neutrality_direct_spec(*case), workspace=None
        )
        for case in NEUTRALITY_DIRECT_CASES
    }


def capture_neutrality_task_specs(repo_root: Path) -> dict:
    """The whole golden document: both families, plus its own provenance.

    This exact function, run inside a `git archive 1045815` checkout with this test
    module copied in, built scripts/fixtures/os22_neutrality/pre_os22_task_specs.json.
    """
    workflow_specs: dict[str, dict[str, list[str]]] = {}
    for skill_path in SKILL_PATHS:
        skill = skill_path.parent.name
        per_skill: dict[str, list[str]] = {}
        for workflow in NEUTRALITY_WORKFLOWS:
            for label, profile in NEUTRALITY_PROFILES:
                per_skill[f"{workflow}|profile={label}"] = (
                    capture_neutrality_workflow_specs(
                        repo_root, skill, workflow, profile=profile
                    )
                )
        workflow_specs[skill] = per_skill
    return {
        "captured_from_commit": NEUTRALITY_COMMIT,
        "captured_by": "scripts/test_e2e_harness.py::capture_neutrality_task_specs",
        "canonicalization": NEUTRALITY_CANONICALIZATION,
        "workflow_specs": workflow_specs,
        "direct_specs": capture_neutrality_direct_specs(),
    }


# The exact signature render_task_spec() had at 1045815: names, order and defaults.
# A new parameter -- even an unused, defaulted one -- is a change to what a Reviewer
# can be handed, so it fails here rather than passing quietly.
NEUTRALITY_RENDER_TASK_SPEC_SIGNATURE = (
    ("base_spec", inspect.Parameter.empty),
    ("boundary", inspect.Parameter.empty),
    ("reviewer_context", None),
    ("quality_gate", None),
    ("risk_context", None),
    ("agent_routing", None),
)


class FinalReviewObservabilityNeutralityTests(unittest.TestCase):
    """OS-22 section 2: adding Final Review observability changed no dispatched byte.

    The baseline is a golden capture taken at 1045815, the last commit BEFORE OS-22,
    and the comparison is on encoded bytes. See
    scripts/fixtures/os22_neutrality/README.md -- if this fails, the current code
    changed reviewer-visible bytes and the code is what needs fixing.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.golden = json.loads(NEUTRALITY_BASELINE.read_text(encoding="utf-8"))
        cls.repo_root = Path(__file__).resolve().parents[1]

    def assertSpecBytesEqual(self, current: str, stored: str, label: str) -> None:
        """The one comparison helper the real assertions and the mutation test share."""
        self.assertEqual(current.encode("utf-8"), stored.encode("utf-8"), label)

    def test_the_golden_records_its_own_provenance(self) -> None:
        self.assertEqual(self.golden["captured_from_commit"], NEUTRALITY_COMMIT)
        self.assertEqual(
            self.golden["canonicalization"], NEUTRALITY_CANONICALIZATION
        )
        self.assertEqual(
            self.golden["captured_by"],
            "scripts/test_e2e_harness.py::capture_neutrality_task_specs",
        )

    def test_the_golden_covers_both_skills_all_workflows_and_both_profiles(
        self,
    ) -> None:
        """A shrunken fixture would silently shrink the claim."""
        specs = self.golden["workflow_specs"]
        self.assertEqual(len(specs), len(SKILL_PATHS))
        for skill_path in SKILL_PATHS:
            skill = skill_path.parent.name
            self.assertIn(skill, specs)
            for workflow in NEUTRALITY_WORKFLOWS:
                for label, _profile in NEUTRALITY_PROFILES:
                    key = f"{workflow}|profile={label}"
                    with self.subTest(skill=skill, key=key):
                        self.assertIn(key, specs[skill])
                        self.assertTrue(specs[skill][key])

    def test_the_golden_carries_a_final_review_spec(self) -> None:
        """The whole point of covering profile=multi in family A."""
        specs = self.golden["workflow_specs"][ORCHESTRATION_SKILL]
        final_review_specs = [
            spec
            for key, rendered in specs.items()
            if key.endswith("|profile=multi")
            for spec in rendered
            if "current_role: final_reviewer" in spec
        ]
        self.assertTrue(final_review_specs)

        direct = self.golden["direct_specs"]
        self.assertIn(
            neutrality_direct_key("final_reviewer", FINAL_REVIEW_PHASE, 1, "all"),
            direct,
        )
        self.assertIn(
            neutrality_direct_key("final_reviewer", FINAL_REVIEW_PHASE, 2, "all"),
            direct,
        )

    def test_every_workflow_spec_is_byte_identical_to_the_pre_os22_capture(
        self,
    ) -> None:
        for skill_path in SKILL_PATHS:
            skill = skill_path.parent.name
            for workflow in NEUTRALITY_WORKFLOWS:
                for label, profile in NEUTRALITY_PROFILES:
                    key = f"{workflow}|profile={label}"
                    with self.subTest(skill=skill, key=key):
                        current = capture_neutrality_workflow_specs(
                            self.repo_root, skill, workflow, profile=profile
                        )
                        stored = self.golden["workflow_specs"][skill][key]

                        self.assertEqual(len(current), len(stored))
                        for index, (actual, expected) in enumerate(
                            zip(current, stored)
                        ):
                            self.assertSpecBytesEqual(
                                actual, expected, f"{skill}::{key}[{index}]"
                            )

    def test_every_direct_spec_is_byte_identical_to_the_pre_os22_capture(self) -> None:
        current = capture_neutrality_direct_specs()
        stored = self.golden["direct_specs"]

        self.assertEqual(sorted(current), sorted(stored))
        for key in sorted(current):
            with self.subTest(case=key):
                self.assertSpecBytesEqual(current[key], stored[key], key)

    def test_the_direct_matrix_covers_every_role_phase_iteration_and_block_set(
        self,
    ) -> None:
        expected = {neutrality_direct_key(*case) for case in NEUTRALITY_DIRECT_CASES}
        self.assertEqual(set(self.golden["direct_specs"]), expected)
        self.assertEqual(
            len(expected),
            sum(len(phases) for _role, phases in _NEUTRALITY_ROLE_PHASES)
            * 2
            * len(NEUTRALITY_DIRECT_BLOCKS),
        )

    def test_no_captured_spec_ends_with_a_terminal_newline(self) -> None:
        """N.2 1b: pinning render_task_spec()'s current terminator, so adding one
        is a neutrality failure rather than an invisible change."""
        for skill, specs in self.golden["workflow_specs"].items():
            for key, rendered in specs.items():
                for index, spec in enumerate(rendered):
                    with self.subTest(skill=skill, key=key, spec=index):
                        self.assertFalse(spec.endswith("\n"))
                        self.assertTrue(spec.endswith(TASK_SPEC_END_MARKER))
        for key, spec in self.golden["direct_specs"].items():
            with self.subTest(case=key):
                self.assertFalse(spec.endswith("\n"))
                self.assertTrue(spec.endswith(TASK_SPEC_END_MARKER))

    def test_a_whitespace_only_change_fails_the_neutrality_golden(self) -> None:
        """N.2 1a: the golden is PROVEN byte-strict, not assumed to be.

        Four whitespace-only mutations, one at a time, on a worker spec, a reviewer
        spec and a final_reviewer spec. Each must fail the real comparison helper --
        and the same test shows _normalize_artifact() would have accepted three of
        them, which is exactly why this capture does not use it.
        """
        samples = {
            "worker": self.golden["direct_specs"][
                neutrality_direct_key("worker", "implementation", 1, "all")
            ],
            "reviewer": self.golden["direct_specs"][
                neutrality_direct_key("reviewer", "design", 2, "all")
            ],
            "final_reviewer": self.golden["direct_specs"][
                neutrality_direct_key("final_reviewer", FINAL_REVIEW_PHASE, 1, "all")
            ],
        }
        workspace = Path("/nonexistent-neutrality-workspace")
        for label, original in samples.items():
            mutants = {
                "strip a trailing space from an empty-valued key": original.replace(
                    "new_claims: \n", "new_claims:\n", 1
                ),
                "add a trailing space to a line that has none": original.replace(
                    "\n=== END TASK BOUNDARY ===",
                    "\n=== END TASK BOUNDARY === ",
                    1,
                ),
                "collapse an interior double space": original.replace("  ", " ", 1),
                "append a terminal newline": original + "\n",
            }
            for mutation, mutant in mutants.items():
                with self.subTest(spec=label, mutation=mutation):
                    self.assertNotEqual(mutant, original, "the mutation was a no-op")
                    with self.assertRaises(self.failureException):
                        self.assertSpecBytesEqual(mutant, original, label)

            for mutation in (
                "strip a trailing space from an empty-valued key",
                "add a trailing space to a line that has none",
                "collapse an interior double space",
            ):
                with self.subTest(spec=label, normalizer=mutation):
                    self.assertEqual(
                        _normalize_artifact(mutants[mutation], workspace),
                        _normalize_artifact(original, workspace),
                        "the old normalizer was supposed to accept this mutation",
                    )

    def test_the_audit_module_is_not_reachable_from_the_dispatch_path(self) -> None:
        """N.2 assertion 3: DEC-4's ordering invariant, enforced structurally.

        Literal non-importability is not achievable -- the redaction code lives in
        run_logging.py, which the dispatching module already imports for logging --
        so the requirement is implemented as NON-INVOCATION and proved here. That is
        strictly stronger evidence than an import graph: an import proves nothing
        about a call, and this proves the call did not happen.

        The patched entry points raise on ANY call, and a full workflow is then
        driven through spec assembly and the dispatch call. The capture surfaces are
        deliberately left out of the tripwire and re-patched below, because they are
        SUPPOSED to run -- after the dispatch, from the settlement path.
        """
        tripwires = (
            "redact_text",
            "capture_stored_task_spec",
            "capture_delivery_evidence",
            "write_final_review_audit_record",
        )
        rendered: list[str] = []
        original = e2e_module.render_task_spec

        def recording(*args, **kwargs):
            spec = original(*args, **kwargs)
            # The tripwires are armed while THIS runs: a spec that renders without
            # tripping one is a spec no audit code touched.
            rendered.append(spec)
            return spec

        skill_path = [
            path for path in SKILL_PATHS if not path.parent.name.endswith("-loop")
        ][0]
        e2e_module.render_task_spec = recording
        try:
            with tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                harness = E2EHarness(
                    skill_path,
                    phase="implementation",
                    workspace=workspace,
                    run_id="run_tripwire",
                )
                scenario = WorkflowScenario(
                    phases=("implementation",),
                    phase_scenarios={
                        "implementation": FakeScenario(("complete",), ("pass",))
                    },
                    final_review=FinalReviewScenario(modes=("pass",)),
                    run_id="run_tripwire",
                )
                patches = [
                    patch.object(
                        run_logging,
                        name,
                        side_effect=AssertionError(
                            f"run_logging.{name} was reached from the "
                            "spec-assembly -> dispatch path"
                        ),
                    )
                    for name in tripwires
                ]
                # The audit emission point itself is on the settlement path, which
                # this workflow also runs, so it is suppressed here rather than
                # allowed to trip its own tripwire.
                with patch.object(
                    e2e_module.E2EHarness, "_write_final_review_audit", lambda *a: None
                ):
                    for entry in patches:
                        entry.start()
                    try:
                        result = harness.run_workflow(scenario)
                    finally:
                        for entry in reversed(patches):
                            entry.stop()
        finally:
            e2e_module.render_task_spec = original

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertTrue(rendered, "no spec was rendered, so nothing was proved")

    def test_the_audit_surfaces_this_test_arms_all_exist(self) -> None:
        """A tripwire patched onto a name that no longer exists proves nothing, and
        patch() on a missing attribute would be the only thing to say so."""
        for name in (
            "redact_text",
            "capture_stored_task_spec",
            "capture_delivery_evidence",
            "write_final_review_audit_record",
        ):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(run_logging, name)))

    def test_render_task_spec_gained_no_parameter(self) -> None:
        signature = inspect.signature(task_context_module.render_task_spec)
        actual = tuple(
            (name, parameter.default)
            for name, parameter in signature.parameters.items()
        )
        self.assertEqual(actual, NEUTRALITY_RENDER_TASK_SPEC_SIGNATURE)

    def test_the_os4_legacy_evidence_is_untouched(self) -> None:
        """A separate capture function and a separate fixture file, as DEC-1 requires:
        extending capture_legacy_artifacts() or pre_os4_artifacts.json in place would
        change LegacyByteIdentityTests' input and destroy the OS-4 evidence."""
        self.assertNotEqual(NEUTRALITY_BASELINE, LEGACY_BASELINE)
        legacy = json.loads(LEGACY_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(len(legacy), 6)
        self.assertEqual(set(legacy), {
            f"{skill_path.parent.name}::{workflow}"
            for skill_path in SKILL_PATHS
            for workflow in GOLDEN_WORKFLOWS
        })

        # The neutrality capture must never route a Task spec through the OS-4
        # normalizer -- that is the whole reason canonicalize_task_spec() exists.
        # Asserted over the parsed call graph, not over the text, so the explanatory
        # comments that name the normalizer do not satisfy or break the check.
        module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        capture_functions = {
            "canonicalize_task_spec",
            "capture_neutrality_workflow_specs",
            "capture_neutrality_direct_specs",
            "capture_neutrality_task_specs",
            "_neutrality_direct_spec",
            "_neutrality_routing",
        }
        found = set()
        for node in module.body:
            if isinstance(node, ast.FunctionDef) and node.name in capture_functions:
                found.add(node.name)
                called = {
                    call.func.id
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                }
                self.assertNotIn("_normalize_artifact", called, node.name)
        self.assertEqual(found, capture_functions)


class DeterministicFinalReviewAuditTests(unittest.TestCase):
    """OS-22 I-10, harness side: the deterministic runtime takes the same path.

    The point is not to re-test the writer -- test_run_logging.py does that -- but to
    prove the EMISSION POINT exists on this path too, and that the workflow's own
    verdict is unaffected by it.
    """

    def run_workflow(
        self,
        modes,
        workspace: Path,
        run_id: str = "run_audit_e2e",
        *,
        findings=(),
        correction_scenarios=None,
    ):
        skill_path = [
            path for path in SKILL_PATHS if not path.parent.name.endswith("-loop")
        ][0]
        harness = E2EHarness(
            skill_path, phase="implementation", workspace=workspace, run_id=run_id
        )
        return harness.run_workflow(
            WorkflowScenario(
                phases=("implementation",),
                phase_scenarios={
                    "implementation": FakeScenario(("complete",), ("pass",))
                },
                final_review=FinalReviewScenario(modes=modes, findings=findings),
                run_id=run_id,
                correction_scenarios=correction_scenarios or {},
            )
        )

    def records(self, workspace: Path, run_id: str) -> dict[str, dict]:
        return {
            key: json.loads((directory / "record.json").read_text(encoding="utf-8"))
            for key, directory in run_logging.iter_final_review_audit_records(
                run_id, base=workspace
            )
        }

    def test_one_record_per_final_review_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)

            result = self.run_workflow(("pass",), workspace)

            records = self.records(workspace, "run_audit_e2e")
            self.assertEqual(len(records), 1)
            record = records["attempt1__task_e2e_final_review_1__ctx_e2e_final_review_1"]
            self.assertEqual(record["final_review_attempt"], 1)
            self.assertEqual(record["provenance_state"], "accepted")
            self.assertEqual(record["settlement_state"], "settled")
            self.assertEqual(result.final_review_iterations, 1)

    def test_the_contracted_review_artifact_is_materialized_and_snapshotted(
        self,
    ) -> None:
        """final_review_artifact_path() named the path and nothing wrote it, so the
        contracted artifact existed only as a string in a result object."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)

            result = self.run_workflow(("pass",), workspace)

            report = workspace / result.final_review_artifacts[0]
            self.assertTrue(report.is_file())
            record = next(iter(self.records(workspace, "run_audit_e2e").values()))
            self.assertEqual(record["report"]["capture_status"], "captured")
            snapshot = (
                workspace
                / "artifacts"
                / "runs"
                / "run_audit_e2e"
                / "final_review_audit"
                / record["dispatch_key"]
                / "report.md"
            )
            self.assertEqual(
                run_logging.sha256_bytes(snapshot.read_bytes()),
                record["report"]["artifact_digest_post_redaction"],
            )

    def test_a_second_attempt_never_overwrites_the_first_record(self) -> None:
        """The whole point of a per-dispatch key: attempt 2's Reviewer can overwrite
        attempt 1's FINAL_REVIEW.md, but not attempt 1's snapshot of it."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)

            self.run_workflow(
                ("fail", "pass"),
                workspace,
                findings=((("R1", "implementation"),), ()),
                correction_scenarios={
                    ("implementation", 1): FakeScenario(
                        worker_modes=("correction",),
                        reviewer_modes=("pass",),
                        worker_resolutions=({"R1": "RESOLVED"},),
                    )
                },
            )

            records = self.records(workspace, "run_audit_e2e")
            self.assertEqual(len(records), 2)
            attempts = sorted(
                record["final_review_attempt"] for record in records.values()
            )
            self.assertEqual(attempts, [1, 2])
            audit_dir = (
                workspace / "artifacts" / "runs" / "run_audit_e2e"
                / "final_review_audit"
            )
            first = (
                audit_dir
                / "attempt1__task_e2e_final_review_1__ctx_e2e_final_review_1"
                / "report.md"
            ).read_text(encoding="utf-8")
            second = (
                audit_dir
                / "attempt2__task_e2e_final_review_2__ctx_e2e_final_review_2"
                / "report.md"
            ).read_text(encoding="utf-8")
            self.assertNotEqual(first, second)

    def test_an_audit_failure_does_not_change_the_workflow_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)

            with patch(
                "scripts.run_logging.write_final_review_audit_record",
                side_effect=OSError("disk full"),
            ):
                result = self.run_workflow(("pass",), workspace)

            self.assertEqual(result.final_review_verdict, "PASS")
            self.assertEqual(result.final_status, "COMPLETED")


class FinalReviewArtifactPathAttemptDomainTests(unittest.TestCase):
    """T-13.9 -- DESIGN D-A.7.4' GATE 6.

    `final_review_artifact_path()` is the third public producer of the
    FINAL_REVIEW.md / FINAL_REVIEW_iteration<N>.md filename family. It shipped with the
    `< 1` half of the check and not the type half, so `2.0` built
    artifacts/runs/<run>/FINAL_REVIEW_iteration2.0.md (M-24b).
    """

    OUT_OF_DOMAIN = (0, -1, -12, False, True, 2.0, "2", None)

    def test_t139_every_out_of_domain_attempt_is_refused(self) -> None:
        for attempt in self.OUT_OF_DOMAIN:
            with self.subTest(attempt=attempt):
                # assertRaises(ValueError) ON PURPOSE, not assertRaises(RunLoggingError).
                # The gate now raises `run_logging.RunLoggingError`, which is declared
                # `class RunLoggingError(ValueError)`, so the shipped raise contract is
                # preserved: every existing `except ValueError` around this function
                # still catches it. This assertion is what pins that substitution
                # (D-A.7.3') rather than leaving a reader to notice it.
                with self.assertRaises(ValueError):
                    e2e_module.final_review_artifact_path("run_t", attempt)

    def test_t139_the_refusal_is_the_shared_predicates_message(self) -> None:
        with self.assertRaises(run_logging.RunLoggingError) as caught:
            e2e_module.final_review_artifact_path("run_t", 2.0)
        self.assertEqual(str(caught.exception), "attempt must be an int >= 1, got 2.0")

    def test_t139_valid_attempts_return_the_shipped_strings(self) -> None:
        for attempt in (1, 2, 3, 99, 100):
            with self.subTest(attempt=attempt):
                suffix = "" if attempt == 1 else f"_iteration{attempt}"
                self.assertEqual(
                    e2e_module.final_review_artifact_path("run_t", attempt),
                    f"artifacts/runs/run_t/FINAL_REVIEW{suffix}.md",
                )


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------
# OS-29: the decision gate's transition behaviour.
#
# The subject of every case below is a TRANSITION -- did the next phase dispatch, did
# the correction Worker dispatch, did the counter move -- so it lives here, where
# WorkflowRunResult makes each of those a list-length or dict assertion on the
# returned value rather than a log grep. Every non-vacuity control is co-located in
# the same test function as the claim it protects.
# ---------------------------------------------------------------------------------
class DecisionGateTransitionTests(unittest.TestCase):
    ORCHESTRATION_SKILL = (
        Path(__file__).resolve().parents[1]
        / "orca-worker-reviewer-orchestration"
        / "SKILL.md"
    )
    RUN_ID = "run_os29"

    def harness(self, workspace: Path, *, risk: str, phase: str = "implementation",
                max_iterations: int = 5) -> E2EHarness:
        return E2EHarness(
            self.ORCHESTRATION_SKILL,
            phase=phase,
            max_iterations=max_iterations,
            workspace=workspace,
            run_id=self.RUN_ID,
            risk=risk,
        )

    def round_at(self, risk: str, scenario: FakeScenario) -> WorkflowResult:
        with tempfile.TemporaryDirectory() as directory:
            return self.harness(Path(directory), risk=risk).run(scenario)

    def workflow(
        self,
        scenario,
        *,
        risk: str = "high",
        seed=None,
        harness_hook=None,
    ):
        """One run_workflow in its own workspace. `seed` may plant a ledger first."""
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            if seed is not None:
                seed(workspace)
            harness = self.harness(workspace, risk=risk, phase=scenario.phases[0])
            if harness_hook is not None:
                harness = harness_hook(harness)
            result = harness.run_workflow(scenario)
            ledger = run_logging.read_decision_ledger(scenario.run_id, base=workspace)
            return result, ledger

    # ---- fixtures ---------------------------------------------------------------
    @staticmethod
    def passing_phase() -> FakeScenario:
        return FakeScenario(worker_modes=("complete",), reviewer_modes=("pass",))

    @staticmethod
    def blocking_phase(state: str = "NEEDS_INPUT") -> FakeScenario:
        return FakeScenario(
            worker_modes=("complete",),
            reviewer_modes=("pass",),
            worker_decision_states=(state,),
            reviewer_decision_states=(state,),
        )

    def clear_workflow(self, phases=("analysis", "implementation")):
        return WorkflowScenario(
            phases=phases,
            phase_scenarios={phase: self.passing_phase() for phase in phases},
            final_review=FinalReviewScenario(modes=("pass",)),
            run_id=self.RUN_ID,
        )

    # ---- scenario 3 / 7 / NV-2 ---------------------------------------------------
    def test_needs_input_blocks_identically_at_every_risk_level(self) -> None:
        """Scenario 3 + 7: risk selects WHERE the terminal is recorded, never what it
        says -- and the blocked round charges no correction iteration."""
        outcomes = {}
        for risk in ("low", "medium", "high"):
            result = self.round_at(risk, self.blocking_phase())
            outcomes[risk] = (
                result.final_status,
                result.reason,
                result.decision_state,
                result.decision_reason_code,
            )

        self.assertEqual(outcomes["low"], outcomes["medium"])
        self.assertEqual(outcomes["low"], outcomes["high"])
        self.assertEqual(
            outcomes["low"],
            (
                "BLOCKED",
                "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope",
                "NEEDS_INPUT",
                "blast_radius_beyond_scope",
            ),
        )
        # THE NON-VACUITY GUARD: the three runs really do differ elsewhere, so the
        # equality above is not three runs of the same code path.
        self.assertEqual(len(self.round_at("low", self.blocking_phase()).reviewer_attempts), 0)
        self.assertEqual(len(self.round_at("high", self.blocking_phase()).reviewer_attempts), 1)
        # INV-D1: the verification is the ALREADY-SCHEDULED Reviewer, not a second one.
        blocked_high = self.round_at("high", self.blocking_phase())
        self.assertEqual(len(blocked_high.worker_attempts), 1)
        self.assertEqual(len(blocked_high.reviewer_attempts), 1)

    def test_a_decision_block_charges_no_iteration_and_a_quality_fail_still_does(
        self,
    ) -> None:
        """NV-2, with its control in the same function: "unchanged" must not also be
        true of a globally broken counter."""
        blocked, _ = self.workflow(
            WorkflowScenario(
                phases=("implementation",),
                phase_scenarios={"implementation": self.blocking_phase()},
                final_review=FinalReviewScenario(modes=("pass",)),
                run_id=self.RUN_ID,
            )
        )
        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(blocked.phase_iterations["implementation"], 0)
        self.assertEqual(blocked.correction_dispatches, [])
        self.assertEqual(blocked.revalidation_dispatches, [])
        # THE CONTROL: a quality FAIL round DOES charge one.
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            harness = self.harness(workspace, risk="high", max_iterations=2)
            fail_result = harness.run_workflow(
                WorkflowScenario(
                    phases=("implementation",),
                    phase_scenarios={
                        "implementation": FakeScenario(
                            worker_modes=("complete", "correction"),
                            reviewer_modes=("fail", "pass"),
                            reviewer_findings=(("Q1",), ()),
                            worker_resolutions=({}, {"Q1": "RESOLVED"}),
                        )
                    },
                    final_review=FinalReviewScenario(modes=("pass",)),
                    run_id=self.RUN_ID,
                )
            )
        self.assertEqual(fail_result.final_status, "COMPLETED")
        self.assertEqual(fail_result.phase_iterations["implementation"], 2)

    # ---- scenario 5 --------------------------------------------------------------
    def test_a_midwork_block_is_a_decision_terminal_not_a_worker_blocked(self) -> None:
        """Scenario 5: O-2 routes a Worker that discovered a blocking decision onto
        the DECISION axis, so it charges nothing -- under WORKER_BLOCKED at LOW it
        would charge one."""
        blocked = self.round_at(
            "low",
            FakeScenario(
                worker_modes=("blocked",),
                reviewer_modes=(),
                worker_decision_states=("CONFLICT",),
            ),
        )

        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(
            blocked.reason, "DECISION_BLOCKED:CONFLICT:requirement_contradiction"
        )
        self.assertIsNotNone(blocked.decision_block)
        # THE CONTROL: the same Worker mode with no decision block keeps the existing
        # WORKER_BLOCKED reason, so O-2 is a real branch and not a rename.
        plain = self.round_at(
            "low", FakeScenario(worker_modes=("blocked",), reviewer_modes=())
        )
        self.assertEqual(plain.reason, "WORKER_BLOCKED")
        self.assertIsNone(plain.decision_block)

    # ---- scenario 6 / P6b row 8 ---------------------------------------------------
    def test_a_reviewer_discovered_block_records_its_finding_and_charges_nothing(
        self,
    ) -> None:
        result = self.round_at(
            "high",
            FakeScenario(
                worker_modes=("complete",),
                reviewer_modes=("fail",),
                reviewer_findings=(("R1",),),
                reviewer_decision_states=("CONFLICT",),
            ),
        )

        self.assertEqual(result.final_status, "BLOCKED")
        self.assertEqual(
            result.reason, "DECISION_BLOCKED:CONFLICT:requirement_contradiction"
        )
        # Both requirements hold at once: the finding IS recorded ...
        self.assertIn("R1", result.findings)
        self.assertEqual(result.findings["R1"].reviewer_iterations, [1])
        # ... and the round charges no correction iteration.
        self.assertIsNotNone(result.decision_block)
        # THE AXIS-SEPARATION CONTROL: a Reviewer FAIL whose own gate result is CLEAR
        # takes the EXISTING correction routing and charges its iteration.
        with tempfile.TemporaryDirectory() as directory:
            harness = self.harness(Path(directory), risk="high", max_iterations=2)
            routed = harness.run(
                FakeScenario(
                    worker_modes=("complete", "correction"),
                    reviewer_modes=("fail", "pass"),
                    reviewer_findings=(("R1",), ()),
                    worker_resolutions=({}, {"R1": "RESOLVED"}),
                )
            )
        self.assertEqual(routed.final_status, "COMPLETED")
        self.assertIsNone(routed.decision_block)
        self.assertEqual(len(routed.reviewer_attempts), 2)

    # ---- scenario 4 / P6b row 6 ---------------------------------------------------
    def test_a_reviewer_downgrade_is_rejected_and_still_terminal(self) -> None:
        for reviewer_state in ("CLEAR", "ASSUMPTION_ALLOWED"):
            with self.subTest(downgrade_to=reviewer_state):
                result = self.round_at(
                    "high",
                    FakeScenario(
                        worker_modes=("complete",),
                        reviewer_modes=("pass",),
                        worker_decision_states=("CONFLICT",),
                        reviewer_decision_states=(reviewer_state,),
                    ),
                )
                self.assertEqual(result.final_status, "BLOCKED")
                self.assertEqual(result.reason, "DECISION_DOWNGRADE_REJECTED")
                self.assertEqual(result.decision_state, "CONFLICT")
        # THE CONTROL: a CONFIRMING Reviewer produces the block reason instead, so
        # DECISION_DOWNGRADE_REJECTED is a decision and not the only outcome.
        confirmed = self.round_at("high", self.blocking_phase("CONFLICT"))
        self.assertEqual(
            confirmed.reason, "DECISION_BLOCKED:CONFLICT:requirement_contradiction"
        )

    def test_an_unbound_verification_record_fails_closed(self) -> None:
        """P6b row 7: not a silent fall-back to the Worker's own classification."""
        result = self.round_at(
            "high",
            FakeScenario(
                worker_modes=("complete",),
                reviewer_modes=("pass",),
                worker_decision_states=("NEEDS_INPUT",),
                reviewer_decision_states=("NEEDS_INPUT",),
                reviewer_decision_args=(
                    ("--decision-gate-verifies-raw", "run_os29/plan/9/B2#9"),
                ),
            ),
        )

        self.assertEqual(result.final_status, "BLOCKED")
        self.assertEqual(result.reason, "DECISION_GATE_INPUT_UNBOUND")
        # THE CONTROL: the same round with the harness-supplied binding blocks with
        # the DECISION reason instead, so "unbound" names a real defect.
        bound = self.round_at("high", self.blocking_phase())
        self.assertEqual(
            bound.reason, "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope"
        )

    # ---- scenario 13 --------------------------------------------------------------
    def test_a_silent_or_broken_agent_never_presumes_clear(self) -> None:
        """F1-F6 through the REAL subprocess, at every risk level and both boundaries."""
        for risk in ("low", "medium", "high"):
            with self.subTest(risk=risk, boundary="B2"):
                silent = self.round_at(
                    risk,
                    FakeScenario(
                        worker_modes=("complete",),
                        reviewer_modes=("pass",),
                        worker_decision_states=("",),
                    ),
                )
                self.assertEqual(silent.final_status, "BLOCKED")
                self.assertEqual(silent.reason, "DECISION_GATE_INPUT_MISSING")
                # Row 3 is risk-independent AND never spends the Reviewer.
                self.assertEqual(silent.reviewer_attempts, [])
        for risk in ("medium", "high"):
            with self.subTest(risk=risk, boundary="B3"):
                silent = self.round_at(
                    risk,
                    FakeScenario(
                        worker_modes=("complete",),
                        reviewer_modes=("pass",),
                        reviewer_decision_states=("",),
                    ),
                )
                self.assertEqual(silent.final_status, "BLOCKED")
                self.assertEqual(silent.reason, "DECISION_GATE_INPUT_MISSING")
        malformed = self.round_at(
            "high",
            FakeScenario(
                worker_modes=("complete",),
                reviewer_modes=("pass",),
                worker_decision_args=(("--decision-gate-record-raw", "{not json"),),
            ),
        )
        self.assertEqual(malformed.reason, "DECISION_GATE_INPUT_MALFORMED")
        duplicated = self.round_at(
            "high",
            FakeScenario(
                worker_modes=("complete",),
                reviewer_modes=("pass",),
                worker_decision_args=(("--decision-gate-state-line-raw", "CLEAR"),),
            ),
        )
        self.assertEqual(duplicated.reason, "DECISION_GATE_INPUT_MALFORMED")
        # THE CONTROL: the unmodified scenario COMPLETES, so none of the above is a
        # harness that refuses everything.
        self.assertEqual(self.round_at("high", self.passing_phase()).final_status, "COMPLETED")

    # ---- P6a F9 / NV-1 -------------------------------------------------------------
    def test_the_first_phase_needs_the_run_entry_declaration(self) -> None:
        """F9: delete sequence 0 and the FIRST phase must not dispatch at all."""

        def delete_declaration(workspace: Path) -> None:
            # The harness re-opens the ledger in run_workflow, so the record has to be
            # removed by a hook the harness itself calls -- see the subclass below.
            raise AssertionError("unused")

        class LedgerlessHarness(E2EHarness):
            def run_workflow(self, scenario):
                shutil.rmtree(
                    Path(self.workspace)
                    / "artifacts" / "runs" / scenario.run_id / "decision_ledger",
                    ignore_errors=True,
                )
                # Re-enter the loop WITHOUT re-opening the ledger: this is the
                # "producer did not run" state, not a harness that never started.
                return E2EHarness.run_workflow(self, scenario)

        scenario = self.clear_workflow()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            harness = LedgerlessHarness(
                self.ORCHESTRATION_SKILL,
                phase="analysis",
                workspace=workspace,
                run_id=self.RUN_ID,
                risk="high",
            )
            # open_decision_ledger runs again inside run_workflow, so the deletion
            # has to happen after it: patch the writer out for this one call.
            with patch.object(run_logging, "open_decision_ledger", lambda *a, **k: None):
                blocked = harness.run_workflow(scenario)

        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(blocked.reason, "DECISION_GATE_INPUT_MISSING")
        self.assertEqual(blocked.sessions, ())
        self.assertEqual(blocked.correction_dispatches, [])
        self.assertEqual(blocked.revalidation_dispatches, [])
        for phase in scenario.phases:
            self.assertEqual(blocked.phase_iterations[phase], 0)
        # NV-1'S CONTROL: the SAME scenario with the declaration present dispatches.
        # Without it, "nothing dispatched" would also hold for a harness that never
        # dispatches anything.
        admitted, _ = self.workflow(scenario)
        self.assertEqual(admitted.final_status, "COMPLETED")
        self.assertTrue(admitted.sessions)
        self.assertEqual(admitted.phase_iterations["analysis"], 1)

    # ---- P6a F11 -------------------------------------------------------------------
    def test_the_declaration_cannot_stand_in_for_a_deleted_settled_record(self) -> None:
        """F11, the hole proof: A3 refuses to fall back to sequence 0."""
        scenario = self.clear_workflow()

        class DeletingHarness(E2EHarness):
            """Delete every settled agent record between the first phase and the next
            B1, which is exactly the shape a lost or withheld record has."""

            def _phase_harness(self, phase, budget):
                child = E2EHarness._phase_harness(self, phase, budget)
                original = child.run

                def run(phase_scenario):
                    result = original(phase_scenario)
                    ledger = (
                        Path(self.workspace)
                        / "artifacts" / "runs" / self.run_id / "decision_ledger"
                    )
                    for entry in sorted(ledger.iterdir()):
                        if entry.name.isdigit() and int(entry.name) > 0:
                            shutil.rmtree(entry)
                    return result

                child.run = run
                return child

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            harness = DeletingHarness(
                self.ORCHESTRATION_SKILL,
                phase="analysis",
                workspace=workspace,
                run_id=self.RUN_ID,
                risk="high",
            )
            blocked = harness.run_workflow(scenario)

        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(blocked.reason, "DECISION_GATE_INPUT_UNBOUND")
        self.assertEqual(blocked.decision_state, "INPUT")
        # The first phase DID dispatch -- the refusal is at the SECOND boundary, not
        # at the run's start, which is what makes this the hole proof rather than F9.
        self.assertEqual(blocked.phase_iterations["analysis"], 1)
        self.assertTrue(blocked.sessions)
        # THE CONTROL: without the deletion the same run completes.
        admitted, _ = self.workflow(scenario)
        self.assertEqual(admitted.final_status, "COMPLETED")

    # ---- scenario 12 ----------------------------------------------------------------
    def test_an_open_ledger_item_blocks_the_next_phase_dispatch(self) -> None:
        """Scenario 12: after a blocking decision is recorded, the next phase's B1
        refuses and NO dispatch for that phase is created."""
        open_record = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "scripts" / "fixtures" / "decision_gate" / "valid"
                / "worker_needs_input.json"
            ).read_text(encoding="utf-8")
        )
        scenario = self.clear_workflow(phases=("analysis", "implementation"))

        class PlantingHarness(E2EHarness):
            """Record an open blocking item as the FIRST phase settles. Planted rather
            than produced because a block produced in-phase terminates the run there,
            so the "a later dispatch is attempted anyway" state is only reachable by
            constructing it."""

            planted = False

            def _phase_harness(harness_self, phase, budget):
                child = E2EHarness._phase_harness(harness_self, phase, budget)
                original = child.run

                def run(phase_scenario):
                    result = original(phase_scenario)
                    if PlantingHarness.planted:
                        return result
                    PlantingHarness.planted = True
                    planted = dict(
                        open_record, run=harness_self.run_id, phase=phase, iteration=1
                    )
                    _, sequence = run_logging.append_decision_ledger_record(
                        harness_self.run_id,
                        planted,
                        base=harness_self.workspace,
                        ledger_schema_version=(
                            decision_gate.LEDGER_RECORD_SCHEMA_VERSION
                        ),
                    )
                    ledger = (
                        Path(harness_self.workspace)
                        / "artifacts" / "runs" / harness_self.run_id
                        / "decision_ledger"
                    )
                    declaration = json.loads(
                        (ledger / "000000" / "record.json").read_text(encoding="utf-8")
                    )
                    declaration["prior_open_decision_items"] = [
                        decision_gate.ledger_key(dict(planted, sequence=sequence))
                    ]
                    (ledger / "000000" / "record.json").write_text(
                        json.dumps(declaration, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    return result

                child.run = run
                return child

        PlantingHarness.planted = False
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            harness = PlantingHarness(
                self.ORCHESTRATION_SKILL,
                phase="analysis",
                workspace=workspace,
                run_id=self.RUN_ID,
                risk="high",
            )
            blocked = harness.run_workflow(scenario)

        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(
            blocked.reason, "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope"
        )
        self.assertEqual(blocked.decision_state, "NEEDS_INPUT")
        self.assertEqual(blocked.decision_reason_code, "blast_radius_beyond_scope")
        # The FIRST phase dispatched; the second did not, and no correction or
        # revalidation dispatch exists at all.
        self.assertEqual(blocked.phase_iterations["analysis"], 1)
        self.assertEqual(blocked.phase_iterations["implementation"], 0)
        self.assertEqual(blocked.correction_dispatches, [])
        self.assertEqual(blocked.revalidation_dispatches, [])
        self.assertEqual(
            [event.phase for event in blocked.sessions if event.role == "worker"],
            ["analysis"],
        )
        # THE CONTROL: the same scenario with nothing planted dispatches BOTH phases,
        # so "implementation never ran" is attributable to the guard.
        admitted, _ = self.workflow(scenario)
        self.assertEqual(admitted.final_status, "COMPLETED")
        self.assertEqual(
            [event.phase for event in admitted.sessions if event.role == "worker"],
            ["analysis", "implementation"],
        )

    def test_nv1_removing_the_guard_lets_the_same_scenario_dispatch(self) -> None:
        """NV-1's literal construction: with the B1 guard neutralized, the run that
        blocked above proceeds. Without this, "nothing was dispatched" would also be
        true of a harness that never dispatches anything."""
        open_record = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "scripts" / "fixtures" / "decision_gate" / "valid"
                / "worker_needs_input.json"
            ).read_text(encoding="utf-8")
        )
        scenario = self.clear_workflow(phases=("analysis",))

        def seed(workspace: Path) -> None:
            run_logging.open_decision_ledger(
                self.RUN_ID,
                base=workspace,
                phases=scenario.phases,
                risk="high",
                ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
            )
            planted = dict(open_record, run=self.RUN_ID, phase="analysis")
            _, sequence = run_logging.append_decision_ledger_record(
                self.RUN_ID,
                planted,
                base=workspace,
                ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
            )
            ledger = workspace / "artifacts" / "runs" / self.RUN_ID / "decision_ledger"
            declaration = json.loads(
                (ledger / "000000" / "record.json").read_text(encoding="utf-8")
            )
            declaration["prior_open_decision_items"] = [
                decision_gate.ledger_key(dict(planted, sequence=sequence))
            ]
            (ledger / "000000" / "record.json").write_text(
                json.dumps(declaration, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        blocked, _ = self.workflow(scenario, seed=seed)
        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(blocked.sessions, ())

        # THE MUTANT: admit_head always admits, i.e. the guard is gone.
        with patch.object(
            decision_gate, "admit_head", lambda *args, **kwargs: {"state": "CLEAR"}
        ):
            dispatched, _ = self.workflow(scenario, seed=seed)

        self.assertEqual(dispatched.final_status, "COMPLETED")
        self.assertTrue(dispatched.sessions)
        self.assertEqual(dispatched.phase_iterations["analysis"], 1)

    def test_a_pre_seeded_ledger_is_unbound_at_the_runs_first_boundary(self) -> None:
        """A3 before A5: a ledger this process did not settle cannot be bound, so the
        first boundary refuses as UNBOUND rather than reporting somebody else's block.
        That ordering is what makes L7 -- no cross-session resume -- fail closed."""
        open_record = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "scripts" / "fixtures" / "decision_gate" / "valid"
                / "worker_needs_input.json"
            ).read_text(encoding="utf-8")
        )

        def seed(workspace: Path) -> None:
            run_logging.open_decision_ledger(
                self.RUN_ID,
                base=workspace,
                phases=("analysis",),
                risk="high",
                ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
            )
            run_logging.append_decision_ledger_record(
                self.RUN_ID,
                dict(open_record, run=self.RUN_ID, phase="analysis"),
                base=workspace,
                ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
            )

        scenario = self.clear_workflow(phases=("analysis",))
        blocked, _ = self.workflow(scenario, seed=seed)

        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(blocked.reason, "DECISION_GATE_INPUT_UNBOUND")
        self.assertEqual(blocked.sessions, ())
        # THE CONTROL: without the pre-seeded record the same run completes.
        admitted, _ = self.workflow(scenario)
        self.assertEqual(admitted.final_status, "COMPLETED")

    # ---- F13 / F14 end to end ---------------------------------------------------------
    def test_an_unreadable_or_unsupported_ledger_record_blocks_end_to_end(self) -> None:
        scenario = self.clear_workflow(phases=("analysis",))

        def mutate(field_value, workspace: Path) -> None:
            ledger = (
                workspace / "artifacts" / "runs" / self.RUN_ID / "decision_ledger"
            )
            record = json.loads(
                (ledger / "000000" / "record.json").read_text(encoding="utf-8")
            )
            if field_value is None:
                record.pop("ledger_schema_version")
            else:
                record["ledger_schema_version"] = field_value
            (ledger / "000000" / "record.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

        cases = {
            "absent (A4-i)": (None, "DECISION_GATE_INPUT_MALFORMED"),
            "text (A4-i)": ("1", "DECISION_GATE_INPUT_MALFORMED"),
            "bool (A4-i)": (True, "DECISION_GATE_INPUT_MALFORMED"),
            "unsupported (A4-ii)": (
                max(decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS) + 1,
                "DECISION_LEDGER_SCHEMA_UNSUPPORTED",
            ),
        }
        for label, (value, expected) in cases.items():
            with self.subTest(case=label):

                def seed(workspace: Path, value=value) -> None:
                    run_logging.open_decision_ledger(
                        self.RUN_ID,
                        base=workspace,
                        phases=scenario.phases,
                        risk="high",
                        ledger_schema_version=(
                            decision_gate.LEDGER_RECORD_SCHEMA_VERSION
                        ),
                    )
                    mutate(value, workspace)

                blocked, _ = self.workflow(scenario, seed=seed)
                self.assertEqual(blocked.final_status, "BLOCKED")
                self.assertEqual(blocked.reason, expected)
                self.assertEqual(blocked.sessions, ())
        # THE CONTROL: the supported version admits and the run completes.
        admitted, _ = self.workflow(scenario)
        self.assertEqual(admitted.final_status, "COMPLETED")

    # ---- scenario 9 ---------------------------------------------------------------
    def test_an_unresolved_decision_forbids_final_review_completion(self) -> None:
        """The Final Review attempt open is a B1 site, so an unresolved item stops
        the run BEFORE the Final Review dispatch rather than after it."""
        scenario = self.clear_workflow(phases=("implementation",))
        open_record = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "scripts" / "fixtures" / "decision_gate" / "valid"
                / "worker_needs_input.json"
            ).read_text(encoding="utf-8")
        )

        class PlantingHarness(E2EHarness):
            """Plant an open decision item after the phase gate PASSes, which is the
            only reachable shape: a block during the phase terminates the run there."""

            def _phase_harness(harness_self, phase, budget):
                child = E2EHarness._phase_harness(harness_self, phase, budget)
                original = child.run

                def run(phase_scenario):
                    result = original(phase_scenario)
                    ledger = (
                        Path(harness_self.workspace)
                        / "artifacts" / "runs" / harness_self.run_id
                        / "decision_ledger"
                    )
                    planted = dict(
                        open_record, run=harness_self.run_id, phase=phase, iteration=1
                    )
                    _, sequence = run_logging.append_decision_ledger_record(
                        harness_self.run_id,
                        planted,
                        base=harness_self.workspace,
                        ledger_schema_version=(
                            decision_gate.LEDGER_RECORD_SCHEMA_VERSION
                        ),
                    )
                    declaration = json.loads(
                        (ledger / "000000" / "record.json").read_text(encoding="utf-8")
                    )
                    declaration["prior_open_decision_items"] = [
                        decision_gate.ledger_key(dict(planted, sequence=sequence))
                    ]
                    (ledger / "000000" / "record.json").write_text(
                        json.dumps(declaration, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    return result

                child.run = run
                return child

        with tempfile.TemporaryDirectory() as directory:
            harness = PlantingHarness(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                workspace=Path(directory),
                run_id=self.RUN_ID,
                risk="high",
            )
            blocked = harness.run_workflow(scenario)

        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(
            blocked.reason, "DECISION_BLOCKED:NEEDS_INPUT:blast_radius_beyond_scope"
        )
        self.assertEqual(blocked.final_review_iterations, 0)
        self.assertEqual(blocked.final_review_attempts, [])
        self.assertIsNone(blocked.final_review_verdict)
        # THE CONTROL: without the planted item the same scenario COMPLETES through
        # a real Final Review attempt.
        admitted, _ = self.workflow(scenario)
        self.assertEqual(admitted.final_status, "COMPLETED")
        self.assertEqual(admitted.final_review_iterations, 1)

    # ---- scenario 8 -------------------------------------------------------------------
    def test_a_downstream_expansion_is_a_new_decision_event_not_a_lineage_link(
        self,
    ) -> None:
        """Scenario 8: a T5a revalidation that widens an earlier decision must raise a
        NEW decision event; there is no link back to what it widened (L3)."""
        scenario = WorkflowScenario(
            phases=("analysis", "implementation"),
            phase_scenarios={
                "analysis": self.passing_phase(),
                "implementation": self.passing_phase(),
            },
            final_review=FinalReviewScenario(
                modes=("fail", "pass"), findings=((("F1", "analysis"),), ())
            ),
            correction_scenarios={
                ("analysis", 1): FakeScenario(
                    worker_modes=("correction",),
                    reviewer_modes=("pass",),
                    worker_resolutions=({"F1": "RESOLVED"},),
                )
            },
            revalidation_scenarios={
                ("implementation", 1): self.blocking_phase("CONFLICT")
            },
            run_id=self.RUN_ID,
        )

        blocked, ledger = self.workflow(scenario)

        self.assertEqual(blocked.final_status, "BLOCKED")
        self.assertEqual(
            blocked.reason, "DECISION_BLOCKED:CONFLICT:requirement_contradiction"
        )
        # The escalation is a NEW ledger record, and NO record links to another
        # decision: `verifies` references a ledger RECORD and nothing else does.
        escalations = [
            record
            for record in ledger
            if record.get("state") == "CONFLICT" and record.get("phase") == "implementation"
        ]
        self.assertTrue(escalations)
        for record in ledger:
            for reserved in decision_gate.OS30_RESERVED_FIELDS:
                self.assertNotIn(reserved, record)
        # THE CONTROL: a revalidation that stays inside the original decision needs
        # no new event and the run completes.
        inside = replace(
            scenario,
            revalidation_scenarios={("implementation", 1): self.passing_phase()},
        )
        completed, _ = self.workflow(inside)
        self.assertEqual(completed.final_status, "COMPLETED")

    # ---- the ledger's provenance ------------------------------------------------------
    def test_every_settled_boundary_leaves_a_complete_bound_record(self) -> None:
        scenario = self.clear_workflow()

        result, ledger = self.workflow(scenario)

        self.assertEqual(result.final_status, "COMPLETED")
        # sequence 0 is the declaration; then one record per settled boundary.
        self.assertEqual([r["sequence"] for r in ledger], list(range(len(ledger))))
        self.assertEqual(ledger[0]["source"], "coordinator:run_entry")
        settled = ledger[1:]
        self.assertEqual(
            [(r["phase"], r["role"], r["boundary"]) for r in settled],
            [
                ("analysis", "worker", "B2"),
                ("analysis", "reviewer", "B3"),
                ("implementation", "worker", "B2"),
                ("implementation", "reviewer", "B3"),
            ],
        )
        for record in ledger:
            for field in decision_gate.REQUIRED_LEDGER_RECORD_FIELDS:
                self.assertIn(field, record)
            for field in decision_gate.LEDGER_MECHANICS_FIELDS:
                self.assertIn(field, record)
            self.assertEqual(record["run"], self.RUN_ID)
        # The Reviewer's record carries the quality verdict beside -- never as -- the
        # decision state: two axes, two fields.
        self.assertEqual(settled[1]["verdict"], "PASS")
        self.assertEqual(settled[1]["state"], "CLEAR")
        self.assertEqual(settled[0]["verdict"], "")

    def test_a_fully_clear_run_adds_artifacts_without_changing_a_transition(
        self,
    ) -> None:
        """The rollback guarantee, stated as an assertion: a CLEAR run's transitions
        are the pre-OS-29 ones, and what it gains is artifacts."""
        scenario = self.clear_workflow()

        result, ledger = self.workflow(scenario)

        self.assertEqual(result.final_status, "COMPLETED")
        self.assertEqual(result.phase_iterations, {"analysis": 1, "implementation": 1})
        self.assertEqual(result.correction_dispatches, [])
        self.assertEqual(result.revalidation_dispatches, [])
        self.assertEqual(result.final_review_iterations, 1)
        self.assertEqual(result.reason, None)
        self.assertEqual(result.decision_state, "")
        self.assertEqual(result.decision_reason_code, "")
        # ...and the additions really are there, so this is a no-op in transitions
        # and NOT a build in which the gate never ran.
        self.assertEqual(len(ledger), 5)


class DecisionGateNonDuplicationTests(unittest.TestCase):
    """NV-3 / M-DUP: the verification-mode Reviewer is not a second loop.

    One test function, three steps, in this order: (1) prove the mutation is not a
    no-op, (2) prove the round_kind proxy is BLIND to it -- which is what demotes
    that proxy from "the non-duplication proof" to supplementary evidence -- and
    (3) prove the invariants reject it while the unmutated control passes.
    """

    ORCHESTRATION_SKILL = (
        Path(__file__).resolve().parents[1]
        / "orca-worker-reviewer-orchestration"
        / "SKILL.md"
    )

    @staticmethod
    def invariant_violations(result: WorkflowResult) -> list[str]:
        """INV-D1: one Worker event and at most one Reviewer event per iteration."""
        violations: list[str] = []
        by_iteration: dict[int, dict[str, int]] = {}
        for event in result.sessions:
            counts = by_iteration.setdefault(event.iteration, {})
            counts[event.role] = counts.get(event.role, 0) + 1
        for iteration, counts in by_iteration.items():
            if counts.get("worker", 0) != 1:
                violations.append(f"iteration {iteration}: {counts} worker events")
            if counts.get("reviewer", 0) > 1:
                violations.append(f"iteration {iteration}: {counts} reviewer events")
        if len(result.reviewer_attempts) > len(result.worker_attempts):
            violations.append("more reviewer attempts than worker attempts")
        return violations

    def run_round(self, harness_class, state: str) -> WorkflowResult:
        with tempfile.TemporaryDirectory() as directory:
            harness = harness_class(
                self.ORCHESTRATION_SKILL,
                phase="implementation",
                workspace=Path(directory),
                run_id="run_mdup",
                risk="high",
            )
            return harness.run(
                FakeScenario(
                    worker_modes=("complete",),
                    reviewer_modes=("pass", "pass"),
                    worker_decision_states=(state,),
                    reviewer_decision_states=(state, state),
                )
            )

    def test_m_dup_fails_the_invariants_while_the_control_passes(self) -> None:
        class DuplicatingHarness(E2EHarness):
            """The mutation: record a SECOND reviewer event for the same
            (phase, iteration), reusing the existing phase_gate label so the
            round_kind vocabulary check cannot see it."""

            def _record_session(self, role, iteration, **kwargs):
                E2EHarness._record_session(self, role, iteration, **kwargs)
                if role == "reviewer":
                    E2EHarness._record_session(self, role, iteration, **kwargs)

        control = self.run_round(E2EHarness, "NEEDS_INPUT")
        mutant = self.run_round(DuplicatingHarness, "NEEDS_INPUT")

        # (1) ANTI-VACUITY PRECHECK: the mutation is not a no-op.
        self.assertGreater(len(mutant.sessions), len(control.sessions))
        self.assertGreater(
            sum(1 for e in mutant.sessions if e.role == "reviewer"),
            sum(1 for e in control.sessions if e.role == "reviewer"),
        )
        # (2) THE PROXY IS BLIND: the four-value round_kind assertion still passes on
        # the mutant, which is why it is not the non-duplication proof.
        self.assertEqual(len(run_logging.ROUND_KIND_VALUES), 4)
        self.assertIn("phase_gate", run_logging.ROUND_KIND_VALUES)
        # (3) THE INVARIANT REJECTS THE MUTANT ...
        self.assertTrue(self.invariant_violations(mutant))
        # ... AND RETURNS CLEAN ON THE CONTROL, so it is not a checker that rejects
        # everything. Note the control is a decision-BLOCKED round: verification mode
        # runs the already-scheduled Reviewer once, not twice.
        self.assertEqual(self.invariant_violations(control), [])
        self.assertEqual(control.final_status, "BLOCKED")
        self.assertEqual(len(control.reviewer_attempts), 1)
        # And a CLEAR round is equally clean, so the invariant is not specific to a
        # blocked one.
        clear = self.run_round(E2EHarness, "CLEAR")
        self.assertEqual(self.invariant_violations(clear), [])
        self.assertEqual(clear.final_status, "COMPLETED")
