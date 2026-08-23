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

    # ---- the three anchor contracts added for session reuse ---------------------
    # test_valid_repository_passes above is the positive case for all three: it runs
    # the validator over an unmutated copy, so every new check has to pass there.

    def test_reuse_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(
            "REUSE_SCOPE = same_role_across_phases_and_iterations\n", ""
        )

        self.assert_lifecycle_contract_rejected(
            "session reuse contract keys drifted"
        )

    def test_reuse_eligibility_shorter_than_eight_conditions_fails(self) -> None:
        self.mutate_orchestration_skill(
            ", not_coordinator_or_adopted, not_in_lifecycle_recovery\n",
            "\n",
        )

        self.assert_lifecycle_contract_rejected(
            "REUSE_ELIGIBILITY must list exactly eight conditions"
        )

    def test_reuse_zero_command_sentence_removal_fails(self) -> None:
        self.mutate_orchestration_skill(
            "reuse는 이전 Dispatch에 어떤 lifecycle mutation 명령도 보내지 않는다.",
            "reuse는 이전 Dispatch에 retain 명령을 보낸다.",
        )

        self.assert_lifecycle_contract_rejected(
            "section 6 prose is missing the zero lifecycle command sentence"
        )

    def test_role_table_calling_a_reused_terminal_adopted_fails(self) -> None:
        """PLAN D-1: the drift this change removed must not be reintroduced."""
        self.mutate_orchestration_skill(
            "| `external_or_adopted` | **Coordinator가 만들지 않은** terminal.",
            "| `external_or_adopted` | reused / pre-existing.",
        )

        self.assert_lifecycle_contract_rejected(
            "must not call a reused terminal external_or_adopted"
        )

    def test_task_boundary_keys_carrying_an_id_fails(self) -> None:
        """Layer 1 is assembled before either id exists, so neither may appear."""
        self.mutate_orchestration_skill(
            "TASK_BOUNDARY_KEYS = current_role,",
            "TASK_BOUNDARY_KEYS = task_id, current_role,",
        )

        self.assert_lifecycle_contract_rejected(
            "TASK_BOUNDARY_KEYS must not carry an id"
        )

    def test_dispatch_identity_rule_without_new_value_every_attempt_fails(self) -> None:
        self.mutate_orchestration_skill(
            "new_value_every_attempt, ",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "DISPATCH_IDENTITY_RULE must forbid identity carry-over"
        )

    def test_reviewer_context_losing_drill_down_fails(self) -> None:
        self.mutate_orchestration_skill(
            ", validation, drill_down\n",
            ", validation\n",
        )

        self.assert_lifecycle_contract_rejected(
            "REVIEWER_CONTEXT_KEYS must keep all eight keys including drill_down"
        )

    def test_delta_first_removing_direct_verification_duty_fails(self) -> None:
        """R-4 anti-weakening: delta-first may not shrink section 11's own duty."""
        self.mutate_orchestration_skill(
            "Worker 설명을 사실로 가정하지 않고 실제 repository/artifact/diff/test result를 확인한다.\n",
            "",
        )

        self.assert_lifecycle_contract_rejected(
            "must not remove the reviewer's direct verification duty"
        )

    def test_reviewer_context_carve_out_removal_fails(self) -> None:
        self.mutate_orchestration_skill(
            "REVIEWER_CONTEXT_EXCLUDES = final_adversarial_review",
            "REVIEWER_CONTEXT_EXCLUDES = none",
        )

        self.assert_lifecycle_contract_rejected(
            "must keep the final adversarial review carve-out"
        )

    # ---- DESIGN section 7.1 D: block-level negatives for the three new contracts --
    # Each block gets the same three: removed entirely, one value drifted, and copied
    # into the loop skill. The three failure modes hit three different checks -- the
    # `parsed is not None` guard, the exact-dict comparison, and the containment
    # guard -- so a validator that lost any one of them still fails here.

    def anchor_block(self, heading: str) -> str:
        """The `#### <heading>` subsection down to the end of its ```text fence."""
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index(heading)
        fence = orchestration.index("```text", start)
        end = orchestration.index("```\n", fence + 1) + 4
        return orchestration[start:end]

    def copy_into_loop_skill(self, heading: str) -> None:
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8")
            + "\n"
            + self.anchor_block(heading),
            encoding="utf-8",
        )

    def test_session_reuse_contract_missing_fails(self) -> None:
        """The whole block, not one key: the `parsed is not None` guard."""
        self.mutate_orchestration_skill(self.anchor_block("#### Session reuse contract"), "")

        self.assert_lifecycle_contract_rejected(
            "session reuse contract block is missing or malformed"
        )

    def test_session_reuse_contract_value_drift_fails(self) -> None:
        """The exact-dict comparison itself, independent of the semantic checks."""
        self.mutate_orchestration_skill(
            "REUSE_SCOPE = same_role_across_phases_and_iterations",
            "REUSE_SCOPE = any_role_across_phases_and_iterations",
        )

        self.assert_lifecycle_contract_rejected(
            "session reuse contract values drifted"
        )

    def test_session_reuse_contract_copied_into_loop_skill_fails(self) -> None:
        self.copy_into_loop_skill("#### Session reuse contract")

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the session reuse contract"
        )

    def test_task_boundary_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(self.anchor_block("#### Task boundary contract"), "")

        self.assert_lifecycle_contract_rejected(
            "task boundary contract block is missing or malformed"
        )

    def test_task_boundary_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "DISPATCH_INJECTED_IDENTITY = task_id, dispatch_id, dispatch_capability, "
            "coordinator_handle",
            "DISPATCH_INJECTED_IDENTITY = task_id, dispatch_id, coordinator_handle",
        )

        self.assert_lifecycle_contract_rejected(
            "task boundary contract values drifted"
        )

    def test_task_boundary_contract_copied_into_loop_skill_fails(self) -> None:
        self.copy_into_loop_skill("#### Task boundary contract")

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the task boundary contract"
        )

    def test_reviewer_context_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(
            self.anchor_block("#### Reviewer context contract"), ""
        )

        self.assert_lifecycle_contract_rejected(
            "reviewer context contract block is missing or malformed"
        )

    def test_reviewer_context_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "REVIEWER_CONTEXT_MODE = delta_first",
            "REVIEWER_CONTEXT_MODE = whole_history",
        )

        self.assert_lifecycle_contract_rejected(
            "reviewer context contract values drifted"
        )

    def test_reviewer_context_contract_copied_into_loop_skill_fails(self) -> None:
        self.copy_into_loop_skill("#### Reviewer context contract")

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the reviewer context contract"
        )

    # ---- the quality profile block, and the review policy it only indexes ---------
    # The block gets the same three negatives as the others. The fourth test is the
    # one this contract needs that the others do not: a SKILL.md that still declares
    # the model while reviews/common.md -- the file a phase Reviewer is actually
    # routed to -- has lost it is exactly the documentation-only change OS-1 forbids.

    def quality_profile_block(self) -> str:
        """The machine-readable fence, not the first ```text in the subsection."""
        orchestration = (
            self.repo_root / "orca-worker-reviewer-orchestration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        start = orchestration.index("```text\nQUALITY_PROFILE_STATUS")
        return orchestration[start : orchestration.index("```\n", start + 1) + 4]

    def test_quality_profile_contract_missing_fails(self) -> None:
        self.mutate_orchestration_skill(self.quality_profile_block(), "")

        self.assert_lifecycle_contract_rejected(
            "quality profile contract block is missing or malformed"
        )

    def test_quality_profile_contract_value_drift_fails(self) -> None:
        self.mutate_orchestration_skill(
            "QUALITY_GATE_SEVERITY_RULE = severity_is_not_blocking",
            "QUALITY_GATE_SEVERITY_RULE = severity_is_blocking",
        )

        self.assert_lifecycle_contract_rejected(
            "quality profile contract values drifted"
        )

    def test_quality_gate_gaining_a_third_workflow_value_fails(self) -> None:
        """PASS WITH NOTES becoming a gate value is the regression this guards."""
        self.mutate_orchestration_skill(
            "QUALITY_GATE_WORKFLOW_VALUES = pass, fail",
            "QUALITY_GATE_WORKFLOW_VALUES = pass, pass_with_notes, fail",
        )

        self.assert_lifecycle_contract_rejected(
            "QUALITY_GATE_WORKFLOW_VALUES must stay two-valued"
        )

    def test_general_gate_growing_past_five_ids_fails(self) -> None:
        self.mutate_orchestration_skill(
            "QUALITY_GATE_GENERAL_IDS = g1, g2, g3, g4, g5",
            "QUALITY_GATE_GENERAL_IDS = g1, g2, g3, g4, g5, g6",
        )

        self.assert_lifecycle_contract_rejected(
            "the minimal general gate must stay five ids"
        )

    def test_quality_gate_dropping_the_worker_role_fails(self) -> None:
        self.mutate_orchestration_skill(
            "QUALITY_GATE_CONTEXT_ROLES = worker, reviewer, final_reviewer",
            "QUALITY_GATE_CONTEXT_ROLES = reviewer, final_reviewer",
        )

        self.assert_lifecycle_contract_rejected(
            "QUALITY_GATE_CONTEXT_ROLES must reach the Worker"
        )

    def test_review_policy_losing_the_profile_first_model_fails(self) -> None:
        """A documentation-only SKILL.md change must not pass validation."""
        policy_path = (
            self.repo_root
            / "orca-worker-reviewer-orchestration"
            / "reviews"
            / "common.md"
        )
        text = policy_path.read_text(encoding="utf-8")
        policy_path.write_text(
            text.replace("### Minimal General Gate", "### Removed", 1), encoding="utf-8"
        )

        self.assert_lifecycle_contract_rejected(
            "reviews/common.md is missing the profile-first anchor"
        )

    def test_quality_profile_contract_copied_into_loop_skill_fails(self) -> None:
        loop_path = self.repo_root / "orca-worker-reviewer-loop" / "SKILL.md"
        loop_path.write_text(
            loop_path.read_text(encoding="utf-8")
            + "\n#### Quality profile contract\n\n"
            + self.quality_profile_block(),
            encoding="utf-8",
        )

        self.assert_lifecycle_contract_rejected(
            "orca-worker-reviewer-loop: must not contain the quality profile contract"
        )

    def test_dispatch_settled_example_losing_reuse_fails(self) -> None:
        """PR #15 review finding: --reuse silently dropped from the CLI example."""
        self.mutate_orchestration_skill(
            "--terminal <handle> --action created|reused --reuse <worker-start/dispatch\n"
            "      응답의 effects[].action> --result \"<outcome/settlement/lifecycle 요약>\"",
            "--terminal <handle> --action created|reused --result "
            "\"<outcome/settlement/lifecycle 요약>\"",
        )

        self.assert_lifecycle_contract_rejected(
            "dispatch_settled orchestrator-event example is missing "
            "'--action created|reused --reuse'"
        )

    def test_run_logging_section_missing_fails(self) -> None:
        self.mutate_orchestration_skill(
            "#### Run-scoped orchestration and timing logs (OS-17)",
            "#### Removed",
        )

        self.assert_lifecycle_contract_rejected(
            "run-scoped orchestration/timing log section is missing"
        )

if __name__ == "__main__":
    unittest.main()
