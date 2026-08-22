#!/usr/bin/env python3
"""Regression tests for validate_skills.py using disposable repository copies."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "orca-worker-reviewer-loop",
    "orca-worker-reviewer-orchestration",
)


class ValidatorRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temporary_directory.name) / "repo"
        self.repo_root.mkdir()

        for filename in (
            "README.md",
            "INSTALL.md",
            "VERSION",
            "CHANGELOG.md",
            "COMPATIBILITY.md",
            "RELEASING.md",
            "LICENSE-DECISION.md",
            "STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md",
        ):
            shutil.copy2(SOURCE_ROOT / filename, self.repo_root / filename)
        for skill_name in SKILL_NAMES:
            shutil.copytree(SOURCE_ROOT / skill_name, self.repo_root / skill_name)

        scripts_dir = self.repo_root / "scripts"
        scripts_dir.mkdir()
        for filename in (
            "validate_skills.py",
            "skill_policy.py",
            "workflow_contract.py",
        ):
            shutil.copy2(SOURCE_ROOT / "scripts" / filename, scripts_dir)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_validator(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "scripts/validate_skills.py"],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_repository_passes(self) -> None:
        result = self.run_validator()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Skill validation PASSED", result.stdout)

    def test_missing_required_error_code_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("INVALID_MAX_ITERATIONS", "REMOVED_MAX_ITERATIONS_ERROR"),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing required error code INVALID_MAX_ITERATIONS", result.stdout)

    def test_shared_template_drift_fails(self) -> None:
        template_path = (
            self.repo_root
            / "orca-worker-reviewer-orchestration"
            / "templates"
            / "analysis.md"
        )
        template_path.write_text(
            template_path.read_text(encoding="utf-8") + "\nDrift.\n",
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("templates/analysis.md differs between skills", result.stdout)

    def test_human_readable_default_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("DEFAULT_MAX_ITERATIONS = 5", "DEFAULT_MAX_ITERATIONS = 6", 1),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("human-readable defaults differ from contract", result.stdout)

    def test_human_readable_known_command_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("claude\ncodex\nclaude-glm", "claude\nclaude-glm", 1),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("human-readable known commands differ", result.stdout)

    def test_vendor_specific_agent_argument_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("<agent-command>", "<agent-command> --dangerously-skip-permissions", 1),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("vendor-specific agent launch argument", result.stdout)

    def test_custom_agent_trust_pattern_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace(
                '"custom_agent_command_pattern": "(?:claude|codex)-[A-Za-z0-9._-]+"',
                '"custom_agent_command_pattern": "[A-Za-z0-9._-]+"',
                1,
            ),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("custom agent trust pattern is invalid", result.stdout)

    def test_workflow_output_contract_drift_fails(self) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            text.replace("RESULT: PASS | FAIL", "RESULT: ACCEPT | REJECT"),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing documented choice ['FAIL', 'PASS']", result.stdout)

    def test_invalid_version_fails(self) -> None:
        (self.repo_root / "VERSION").write_text("v1\n", encoding="utf-8")

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("VERSION must contain one SemVer", result.stdout)

    def test_user_specific_path_in_step5_report_fails(self) -> None:
        report = self.repo_root / "STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md"
        report.write_text(
            report.read_text(encoding="utf-8").replace(
                "/Users/<user>/", "/Users/" + "private-user/", 1
            ),
            encoding="utf-8",
        )

        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("user-specific absolute path", result.stdout)

    def mutate_orchestration_skill(self, old: str, new: str) -> None:
        skill_path = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        skill_path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def assert_lifecycle_contract_rejected(self, expected: str) -> None:
        result = self.run_validator()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(expected, result.stdout)

    def test_lifecycle_contract_missing_unsupervised_outcome_fails(self) -> None:
        self.mutate_orchestration_skill(
            "LIFECYCLE_OUTCOMES = reuse, retain, release, unsupervised",
            "LIFECYCLE_OUTCOMES = reuse, retain, release",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_force_ready_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FORCE_READY_USE = recovery_only",
            "FORCE_READY_USE = routine",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_missing_worker_start_terminal_step_fails(self) -> None:
        self.mutate_orchestration_skill(
            "worker_start_agent, "
            "terminal_create_then_tui_idle_then_worker_start_terminal, dispatch_inject",
            "worker_start_agent, dispatch_inject",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_missing_cleanup_authority_axis_fails(self) -> None:
        self.mutate_orchestration_skill(
            "AXIS_C2_CLEANUP_AUTHORITY = launch_provenance_and_ownership\n",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract keys differ from the validator source of truth"
        )

    def test_lifecycle_contract_close_gate_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "CLOSE_ALLOWED_ONLY_WHEN = authorized_and_close_eligible_role",
            "CLOSE_ALLOWED_ONLY_WHEN = connected",
        )

        self.assert_lifecycle_contract_rejected(
            "CLOSE_ALLOWED_ONLY_WHEN must require a close eligible terminal role"
        )

    def test_lifecycle_contract_coordinator_session_removed_from_never_close_fails(
        self,
    ) -> None:
        self.mutate_orchestration_skill(
            "NEVER_CLOSE_TERMINAL_ROLES = coordinator_session, ",
            "NEVER_CLOSE_TERMINAL_ROLES = ",
        )

        self.assert_lifecycle_contract_rejected(
            "NEVER_CLOSE_TERMINAL_ROLES must contain exactly"
        )

    def test_lifecycle_contract_close_gate_without_role_condition_fails(self) -> None:
        self.mutate_orchestration_skill(
            "CLOSE_ALLOWED_ONLY_WHEN = authorized_and_close_eligible_role",
            "CLOSE_ALLOWED_ONLY_WHEN = authorized",
        )

        self.assert_lifecycle_contract_rejected(
            "CLOSE_ALLOWED_ONLY_WHEN must require a close eligible terminal role"
        )

    def test_lifecycle_contract_finalization_gate_order_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINALIZATION_PER_DISPATCH = exactly_once, gate_before_lifecycle_action",
            "FINALIZATION_PER_DISPATCH = exactly_once",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_settlement_verification_drift_fails(self) -> None:
        """Dropping the pre-mutation settlement check from the contract must fail.

        The human review of PR #10 found the harness mutating lifecycle state before
        axis (a) was proven; the anchor block is where that ordering requirement is
        locked, so removing the token has to be rejected here.
        """
        self.mutate_orchestration_skill(
            ", settlement_verified_before_lifecycle_action",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "lifecycle contract values differ from the validator source of truth"
        )

    def test_lifecycle_contract_copied_into_loop_skill_fails(self) -> None:
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index("#### Lifecycle accounting contract")
        fence = orchestration.index("```text", start)
        end = orchestration.index("```\n", fence + 1) + 4
        block = orchestration[start:end]
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8") + "\n" + block,
            encoding="utf-8",
        )

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the orchestration lifecycle "
            "contract"
        )

    # ---- DESIGN section 7.4: the section 17 final review contract ----------------

    def final_review_contract_block(self) -> str:
        """The section 17 anchor block, sliced out of the copied orchestration skill."""
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index("#### Final review contract")
        fence = orchestration.index("```text", start)
        end = orchestration.index("```\n", fence + 1) + 4
        return orchestration[start:end]

    def test_bare_final_review_choice_line_fails(self) -> None:
        """Blocker-1 lock: the result template's FINAL_REVIEW must stay a single value.

        Written as `PASS | FAIL` it reads as a workflow choice line, and the shared
        output-contract extractor then sees two skills declaring different fields.
        """
        self.mutate_orchestration_skill(
            "FINAL_REVIEW: PASS",
            "FINAL_REVIEW: PASS | FAIL",
        )

        self.assert_lifecycle_contract_rejected(
            "inconsistent fields for ['FAIL', 'PASS']"
        )

    def test_final_review_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(self.final_review_contract_block(), "")

        self.assert_lifecycle_contract_rejected(
            "missing or malformed final review contract"
        )

    def test_final_review_contract_allows_reuse_outcome_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = retain, release, unsupervised",
            "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = reuse, retain, release, "
            "unsupervised",
        )

        self.assert_lifecycle_contract_rejected(
            "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES must never contain reuse"
        )

    def test_final_review_contract_iteration_bound_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_ITERATION_BOUND = max_iterations",
            "FINAL_REVIEW_ITERATION_BOUND = three",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_contract_last_attempt_guard_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_LAST_ATTEMPT_FAIL = escalate_before_correction_routing",
            "FINAL_REVIEW_LAST_ATTEMPT_FAIL = correct_then_escalate",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_contract_task_graph_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_TASK_GRAPH = single_node_no_dependencies",
            "FINAL_REVIEW_TASK_GRAPH = depends_on_last_reviewer_task",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_anti_anchoring_sentence_removed_fails(self) -> None:
        self.mutate_orchestration_skill(
            "이전 phase Reviewer의 PASS 판정을 옳다고 가정하지 않는다.\n",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "final review prose is missing the anti-anchoring premise (ko)"
        )

    def test_final_review_contract_copied_into_loop_skill_fails(self) -> None:
        block = self.final_review_contract_block()
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8") + "\n" + block,
            encoding="utf-8",
        )

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the orchestration final "
            "review contract"
        )

    def test_final_review_downstream_revalidation_drift_fails(self) -> None:
        """The permanent lock on the AMENDED DECISION P1 (PR #11 human review, MAJOR 1).

        `delegated_to_next_final_review` is the superseded reading: it says a fresh
        Final Review attempt is a substitute for re-running the downstream phases. That
        reading cannot be re-introduced into section 17 without the validator rejecting
        it, whatever any future prose around the block says.
        """
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_DOWNSTREAM_REVALIDATION = "
            "all_requested_phases_after_earliest_corrected_phase",
            "FINAL_REVIEW_DOWNSTREAM_REVALIDATION = delegated_to_next_final_review",
        )

        self.assert_lifecycle_contract_rejected(
            "final review contract values differ from the validator source of truth"
        )

    def test_final_review_role_outside_close_eligible_roles_fails(self) -> None:
        self.mutate_orchestration_skill(
            "FINAL_REVIEW_ROLE = phase_reviewer",
            "FINAL_REVIEW_ROLE = coordinator_session",
        )

        self.assert_lifecycle_contract_rejected(
            "FINAL_REVIEW_ROLE must be a close eligible terminal role"
        )


if __name__ == "__main__":
    unittest.main()
