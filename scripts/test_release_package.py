#!/usr/bin/env python3
"""Tests for release manifest and reproducible archive generation."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from build_release import build_archive
from release_manifest import (
    EXECUTABLE_FILES,
    PackageError,
    REPO_ROOT,
    read_version,
    verify_source_tree,
)
from verify_package import verify_archive


SKILL_NAMES = ("orca-worker-reviewer-loop", "orca-worker-reviewer-orchestration")


class InstalledSkillPortabilityTests(unittest.TestCase):
    """OS-4 verification item 15: the Agent Profile contract must survive the
    documented install.

    INSTALL.md section 4 installs a skill with `cp -R <skill-dir> ~/.claude/skills/`.
    That copies SKILL.md, templates/, reviews/ and (orchestration only)
    tools/run_logging.py -- and NOT the repository's scripts/. DESIGN D3 decided the
    profile loader does not need to run inside an installed skill: the Coordinator
    follows the SKILL.md prose contract, the same way it already does for the quality
    profile, whose loader is likewise repository-only.

    That decision is only sound if the installed SKILL.md is self-contained. These
    tests hold it to that: whatever the Agent Profile section tells a Coordinator to
    do must be doable with the files `cp -R` actually delivered.
    """

    def install(self, root: Path, skill_name: str) -> Path:
        """Reproduce INSTALL.md section 4 exactly: one `cp -R` of the skill dir."""
        target = root / "skills"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO_ROOT / skill_name, target / skill_name)
        return target / skill_name

    def agent_profile_section(self, skill_md: Path) -> str:
        text = skill_md.read_text(encoding="utf-8")
        start = text.index("## Agent Profile")
        end = text.index("\n## ", start + 1)
        return text[start:end]

    def test_the_install_delivers_what_each_skill_declares(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for skill_name in SKILL_NAMES:
                with self.subTest(skill=skill_name):
                    installed = self.install(root, skill_name)

                    self.assertTrue((installed / "SKILL.md").is_file())
                    self.assertTrue((installed / "templates").is_dir())
                    self.assertTrue((installed / "reviews").is_dir())
                    if skill_name.endswith("orchestration"):
                        self.assertTrue(
                            (installed / "tools" / "run_logging.py").is_file()
                        )
                        self.assertTrue(
                            (installed / "tools" / "clarification_protocol.py").is_file()
                        )

    def test_os30_installed_tool_is_byte_identical_and_self_contained(self) -> None:
        source = REPO_ROOT / "scripts" / "clarification_protocol.py"
        installed = REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools" / "clarification_protocol.py"
        self.assertEqual(source.read_bytes(), installed.read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            skill = self.install(Path(directory), "orca-worker-reviewer-orchestration")
            completed = subprocess.run(
                [sys.executable, str(skill / "tools" / "clarification_protocol.py"), "--help"],
                cwd=skill, text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_the_agent_profile_contract_survives_the_install(self) -> None:
        """The section exists in the installed copy, with both source paths."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for skill_name in SKILL_NAMES:
                with self.subTest(skill=skill_name):
                    installed = self.install(root, skill_name)
                    section = self.agent_profile_section(installed / "SKILL.md")

                    self.assertIn(".orca/agent-profiles.yaml", section)
                    self.assertIn("~/.orca/agent-profiles.yaml", section)
                    self.assertIn("profile=<name>", installed.joinpath("SKILL.md").read_text(encoding="utf-8"))

    def test_the_agent_profile_contract_needs_no_uninstalled_file(self) -> None:
        """D3's condition. If this section ever tells a Coordinator to run a
        repository script, the documented install stops being sufficient and either
        the file must ship inside the skill (as tools/run_logging.py does) or the
        prose must stop depending on it."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for skill_name in SKILL_NAMES:
                with self.subTest(skill=skill_name):
                    installed = self.install(root, skill_name)
                    section = self.agent_profile_section(installed / "SKILL.md")

                    self.assertNotIn("scripts/", section)
                    for module in ("agent_profile.py", "final_report.py"):
                        self.assertNotIn(module, section)

    def test_every_script_the_installed_skill_names_is_installed(self) -> None:
        """Whole-document version of the check above: any `tools/<file>` the SKILL.md
        references must exist in the installed tree."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for skill_name in SKILL_NAMES:
                with self.subTest(skill=skill_name):
                    installed = self.install(root, skill_name)
                    text = (installed / "SKILL.md").read_text(encoding="utf-8")

                    for match in re.findall(r"tools/[A-Za-z0-9_.-]+\.py", text):
                        self.assertTrue(
                            (installed / match).is_file(),
                            f"{skill_name}: SKILL.md names {match}, "
                            "which the documented install does not deliver",
                        )

    def test_the_release_archive_carries_the_new_os4_modules(self) -> None:
        """The source release is the other distribution path, and it DOES carry
        scripts/. A repository consumer must get the loader and the renderer."""
        files = {
            path.relative_to(REPO_ROOT).as_posix() for path in verify_source_tree()
        }

        for expected in (
            "scripts/agent_profile.py",
            "scripts/final_report.py",
            "scripts/test_agent_profile.py",
            "scripts/fixtures/legacy_baseline/pre_os4_artifacts.json",
        ):
            with self.subTest(path=expected):
                self.assertIn(expected, files)

    def test_the_example_profile_is_repository_only(self) -> None:
        """`.orca/` is deliberately outside the package: shipping it would package a
        user's real profile. The example stays a repository reference."""
        files = {
            path.relative_to(REPO_ROOT).as_posix() for path in verify_source_tree()
        }

        self.assertTrue((REPO_ROOT / ".orca" / "agent-profiles.example.yaml").is_file())
        self.assertFalse(any(path.startswith(".orca/") for path in files))


class ReleasePackageTests(unittest.TestCase):
    def test_source_tree_is_complete(self) -> None:
        files = verify_source_tree()
        relative = {path.relative_to(REPO_ROOT).as_posix() for path in files}
        self.assertIn("orca-worker-reviewer-loop/SKILL.md", relative)
        self.assertIn("orca-worker-reviewer-orchestration/reviews/bugfix.md", relative)
        self.assertIn("docs/ROADMAP.md", relative)
        self.assertIn("docs/examples/FULL_WORKFLOW_FAIL_CORRECTION.md", relative)
        self.assertIn("docs/examples/FULL_WORKFLOW_FAIL_CORRECTION.ko.md", relative)
        self.assertIn("docs/validation/GLM_GEMMA_SMOKE_PROCEDURE.md", relative)
        self.assertIn(
            "docs/validation/historical/GLM_GEMMA_SMOKE_REPORT_2026-08-20.md",
            relative,
        )
        self.assertFalse(any("__pycache__" in path for path in relative))

    def test_archive_is_valid_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.tar.gz"
            second = Path(directory) / "second.tar.gz"
            build_archive(first)
            build_archive(second)
            verify_archive(first)
            with tarfile.open(first, mode="r:gz") as archive:
                shim = archive.getmember(
                    f"orca-skills-{read_version()}/scripts/fake_bin/codex"
                )
            self.assertEqual(shim.mode, 0o755)
            self.assertEqual(
                hashlib.sha256(first.read_bytes()).digest(),
                hashlib.sha256(second.read_bytes()).digest(),
            )

    def test_executable_manifest_matches_runtime_shim(self) -> None:
        self.assertEqual(EXECUTABLE_FILES, {"scripts/fake_bin/codex"})
        self.assertTrue((REPO_ROOT / "scripts/fake_bin/codex").stat().st_mode & 0o111)

    def test_missing_required_skill_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(REPO_ROOT, root, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
            (root / "orca-worker-reviewer-loop" / "templates" / "analysis.md").unlink()
            with self.assertRaisesRegex(PackageError, "missing required Skill package files"):
                verify_source_tree(root)

    def test_invalid_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(REPO_ROOT, root, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
            (root / "VERSION").write_text("version-1\n", encoding="utf-8")
            with self.assertRaisesRegex(PackageError, "SemVer"):
                verify_source_tree(root)

    def test_unexpected_skill_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            shutil.copytree(REPO_ROOT, root, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
            artifact = root / "orca-worker-reviewer-loop" / "local-output.txt"
            artifact.write_text("not distributable\n", encoding="utf-8")
            with self.assertRaisesRegex(PackageError, "unexpected Skill package files"):
                verify_source_tree(root)


if __name__ == "__main__":
    unittest.main()
