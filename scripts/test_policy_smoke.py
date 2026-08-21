#!/usr/bin/env python3
"""Smoke tests for deterministic policy decisions defined by the Markdown skills."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.skill_policy import evaluate_invocation, load_policy_contract


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "orca-worker-reviewer-loop",
    "orca-worker-reviewer-orchestration",
)
SKILL_PATHS = tuple(REPO_ROOT / name / "SKILL.md" for name in SKILL_NAMES)


class PolicySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        bin_dir = Path(self.temp_directory.name)
        for command in (
            "claude",
            "codex",
            "claude-glm",
            "claude-gemma",
            "claude-opus",
            "codex-sol",
        ):
            executable = bin_dir / command
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        self.path_patch = mock.patch.dict(os.environ, {"PATH": str(bin_dir)})
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp_directory.cleanup()

    def evaluate(self, skill_path: Path, suffix: str):
        return evaluate_invocation(skill_path, f"/{skill_path.parent.name}{suffix}")

    def assert_blocked(self, suffix: str, reason: str) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name, invocation=suffix):
                decision = self.evaluate(skill_path, suffix)
                self.assertEqual(decision.status, "BLOCKED")
                self.assertEqual(decision.reason, reason)
                self.assertFalse(decision.should_execute)

    def assert_valid(self, suffix: str, phases: tuple[str, ...]) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name, invocation=suffix):
                decision = self.evaluate(skill_path, suffix)
                self.assertEqual(decision.status, "VALID")
                self.assertIsNone(decision.reason)
                self.assertTrue(decision.should_execute)
                self.assertEqual(decision.phases, phases)

    def test_help_mode_never_executes(self) -> None:
        expected_invocations = {
            "orca-worker-reviewer-loop": "/orca-worker-reviewer-loop help",
            "orca-worker-reviewer-orchestration": (
                "/orca-worker-reviewer-orchestration help"
            ),
        }
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = evaluate_invocation(
                    skill_path, expected_invocations[skill_path.parent.name]
                )
                self.assertEqual(decision.status, "HELP")
                self.assertFalse(decision.should_execute)
                self.assertIsNone(decision.worker)
                self.assertIsNone(decision.reviewer)

    def test_worker_and_reviewer_must_differ(self) -> None:
        self.assert_blocked(
            " worker=claude-glm reviewer=claude-glm phases=analysis 요청",
            "WORKER_REVIEWER_MUST_DIFFER",
        )
        self.assert_blocked(
            " worker=claude-opus reviewer=claude-opus phases=analysis 요청",
            "WORKER_REVIEWER_MUST_DIFFER",
        )

    def test_generic_commands_are_valid(self) -> None:
        self.assert_valid(
            " worker=claude reviewer=codex phases=analysis 요청",
            ("analysis",),
        )

    def test_known_company_commands_are_valid(self) -> None:
        self.assert_valid(
            " worker=claude-glm reviewer=claude-gemma phases=analysis 요청",
            ("analysis",),
        )

    def test_path_resolved_model_pinned_wrappers_are_valid(self) -> None:
        self.assert_valid(
            " worker=claude-opus reviewer=codex-sol phases=analysis 요청",
            ("analysis",),
        )

    def test_missing_agent_command_is_blocked(self) -> None:
        self.assert_blocked(
            " worker=missing-agent reviewer=claude-gemma phases=analysis 요청",
            "AGENT_COMMAND_NOT_FOUND",
        )

    def test_unsafe_agent_commands_are_blocked(self) -> None:
        for value in (
            '"claude --model opus"',
            "../claude",
            "/usr/local/bin/claude",
            "claude;echo",
            "claude&&echo",
        ):
            with self.subTest(value=value):
                self.assert_blocked(
                    f" worker={value} reviewer=codex phases=analysis 요청",
                    "INVALID_AGENT_COMMAND",
                )

    def test_invalid_max_iterations(self) -> None:
        for value in ("0", "11", "many"):
            with self.subTest(value=value):
                self.assert_blocked(
                    f" max-iterations={value} phases=analysis 요청",
                    "INVALID_MAX_ITERATIONS",
                )

    def test_invalid_sequential_phase_order(self) -> None:
        for phases in ("implementation,design", "test,implementation"):
            with self.subTest(phases=phases):
                self.assert_blocked(
                    f" phases={phases} 요청",
                    "INVALID_PHASE_ORDER",
                )

    def test_unknown_explicit_phase_is_blocked(self) -> None:
        for phases in ("unknown", "design,unknown"):
            with self.subTest(phases=phases):
                self.assert_blocked(
                    f" phases={phases} 요청",
                    "INVALID_PHASE",
                )

    def test_explicit_and_natural_language_phase_conflict(self) -> None:
        self.assert_blocked(
            " phases=design,implementation 테스트까지 명시적으로 수행해줘",
            "PHASE_CONFLICT",
        )

    def test_phase_conflict_precedes_other_phase_resolution(self) -> None:
        self.assert_blocked(
            " phases=implementation,design 테스트까지 수행해줘",
            "PHASE_CONFLICT",
        )

    def test_unsupported_specialized_phase_combination(self) -> None:
        for phases in ("bugfix,test", "refactoring,implementation"):
            with self.subTest(phases=phases):
                self.assert_blocked(
                    f" phases={phases} 요청",
                    "UNSUPPORTED_PHASE_COMBINATION",
                )

    def test_valid_sequential_combinations(self) -> None:
        combinations = (
            ("analysis,plan,design", ("analysis", "plan", "design")),
            ("design,implementation", ("design", "implementation")),
            ("implementation,test", ("implementation", "test")),
            (
                "analysis,design,implementation,test",
                ("analysis", "design", "implementation", "test"),
            ),
        )
        for value, expected in combinations:
            with self.subTest(phases=value):
                self.assert_valid(f" phases={value} 요청", expected)

    def test_specialized_phases_are_valid_alone(self) -> None:
        self.assert_valid(" phases=bugfix 요청", ("bugfix",))
        self.assert_valid(" phases=refactoring 요청", ("refactoring",))

    def test_parameter_priority_within_deterministic_scope(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name, source="explicit"):
                decision = self.evaluate(
                    skill_path,
                    " worker=claude-gemma reviewer=claude-glm "
                    "max-iterations=3 phases=design,implementation "
                    "설계하고 구현해줘",
                )
                self.assertEqual(decision.status, "VALID")
                self.assertEqual(decision.worker, "claude-gemma")
                self.assertEqual(decision.reviewer, "claude-glm")
                self.assertEqual(decision.max_iterations, 3)
                self.assertEqual(decision.phase_source, "explicit")

            with self.subTest(skill=skill_path.parent.name, source="natural"):
                decision = self.evaluate(skill_path, " 상세 설계를 작성해줘")
                self.assertEqual(decision.phases, ("design",))
                self.assertEqual(decision.phase_source, "natural_language")
                self.assertEqual(decision.worker, "claude-glm")
                self.assertEqual(decision.reviewer, "claude-gemma")
                self.assertEqual(decision.max_iterations, 5)

            with self.subTest(skill=skill_path.parent.name, source="default"):
                decision = self.evaluate(skill_path, " 이 작업을 진행해줘")
                self.assertEqual(decision.phase_source, "llm_classification")
                self.assertTrue(decision.requires_llm_phase_classification)
                self.assertEqual(decision.worker, "claude-glm")
                self.assertEqual(decision.reviewer, "claude-gemma")
                self.assertEqual(decision.max_iterations, 5)

    def test_non_phase_natural_language_parameters_remain_llm_owned(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                contract = load_policy_contract(skill_path)
                scope = contract["natural_language_automation"]
                self.assertEqual(
                    scope["deterministic_representative_terms_for"], ["phases"]
                )
                self.assertEqual(
                    scope["llm_interpretation_required_for"],
                    [
                        "worker",
                        "reviewer",
                        "max-iterations",
                        "free-form phase requests",
                    ],
                )

    def test_two_skills_have_identical_contracts(self) -> None:
        contracts = [load_policy_contract(path) for path in SKILL_PATHS]
        self.assertEqual(contracts[0], contracts[1])

    def test_two_skills_return_identical_policy_decisions(self) -> None:
        suffixes = (
            " help",
            " worker=claude-glm reviewer=claude-glm phases=analysis 요청",
            " worker=missing-agent phases=analysis 요청",
            " worker=claude-opus reviewer=codex-sol phases=analysis 요청",
            " worker=../claude phases=analysis 요청",
            " max-iterations=0 phases=analysis 요청",
            " max-iterations=11 phases=analysis 요청",
            " max-iterations=many phases=analysis 요청",
            " phases=unknown 요청",
            " phases=design,unknown 요청",
            " phases=implementation,design 요청",
            " phases=test,implementation 요청",
            " phases=design,implementation 테스트까지 수행해줘",
            " phases=bugfix,test 요청",
            " phases=refactoring,implementation 요청",
            " phases=analysis,plan,design 요청",
            " phases=design,implementation 요청",
            " phases=implementation,test 요청",
            " phases=analysis,design,implementation,test 요청",
            " phases=bugfix 요청",
            " phases=refactoring 요청",
            " 상세 설계를 작성해줘",
            " 일반 작업을 진행해줘",
        )
        for suffix in suffixes:
            with self.subTest(invocation=suffix):
                decisions = [self.evaluate(path, suffix) for path in SKILL_PATHS]
                self.assertEqual(decisions[0], decisions[1])


if __name__ == "__main__":
    unittest.main()
