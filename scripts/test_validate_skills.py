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


if __name__ == "__main__":
    unittest.main()
