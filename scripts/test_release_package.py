#!/usr/bin/env python3
"""Tests for release manifest and reproducible archive generation."""

from __future__ import annotations

import hashlib
import shutil
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


class ReleasePackageTests(unittest.TestCase):
    def test_source_tree_is_complete(self) -> None:
        files = verify_source_tree()
        relative = {path.relative_to(REPO_ROOT).as_posix() for path in files}
        self.assertIn("orca-worker-reviewer-loop/SKILL.md", relative)
        self.assertIn("orca-worker-reviewer-orchestration/reviews/bugfix.md", relative)
        self.assertIn("STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md", relative)
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
