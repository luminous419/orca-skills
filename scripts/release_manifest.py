#!/usr/bin/env python3
"""Shared release-package manifest rules."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "orca-worker-reviewer-loop",
    "orca-worker-reviewer-orchestration",
)
PHASES = (
    "analysis",
    "plan",
    "design",
    "implementation",
    "test",
    "bugfix",
    "refactoring",
)
TOP_LEVEL_FILES = (
    "README.md",
    "INSTALL.md",
    "VERSION",
    "CHANGELOG.md",
    "COMPATIBILITY.md",
    "RELEASING.md",
    "LICENSE-DECISION.md",
)
INCLUDED_ROOTS = (".github", "scripts", *SKILL_NAMES)
EXECUTABLE_FILES = frozenset({"scripts/fake_bin/codex"})
FORBIDDEN_PARTS = {
    ".git",
    "artifacts",
    "dist",
    "run",
    "__pycache__",
}
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)
USER_PATH_PATTERNS = (
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9._-]+\\"),
)


class PackageError(ValueError):
    """Raised when release package inputs violate the manifest."""


def read_version(root: Path = REPO_ROOT) -> str:
    version_path = root / "VERSION"
    try:
        raw = version_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageError(f"cannot read VERSION: {exc}") from exc
    version = raw.strip()
    if raw != f"{version}\n" or not VERSION_PATTERN.fullmatch(version):
        raise PackageError("VERSION must contain one SemVer MAJOR.MINOR.PATCH line")
    return version


def required_skill_paths(skill_name: str) -> set[str]:
    paths = {f"{skill_name}/SKILL.md", f"{skill_name}/reviews/common.md"}
    for phase in PHASES:
        paths.add(f"{skill_name}/templates/{phase}.md")
        paths.add(f"{skill_name}/reviews/{phase}.md")
    return paths


def archive_mode(relative_path: str) -> int:
    return 0o755 if relative_path in EXECUTABLE_FILES else 0o644


def release_files(root: Path = REPO_ROOT) -> tuple[Path, ...]:
    files = [root / name for name in TOP_LEVEL_FILES]
    for relative_root in INCLUDED_ROOTS:
        base = root / relative_root
        if not base.is_dir():
            raise PackageError(f"missing release directory: {relative_root}")
        for path in base.rglob("*"):
            if path.is_symlink():
                raise PackageError(f"symlinks are not allowed: {path.relative_to(root)}")
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if FORBIDDEN_PARTS.intersection(relative.parts) or path.suffix in {".pyc", ".pyo"}:
                continue
            files.append(path)
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def verify_source_tree(root: Path = REPO_ROOT) -> tuple[Path, ...]:
    version = read_version(root)
    del version
    for name in TOP_LEVEL_FILES:
        if not (root / name).is_file():
            raise PackageError(f"missing release file: {name}")

    files = release_files(root)
    relative_files = {path.relative_to(root).as_posix() for path in files}
    required = set().union(*(required_skill_paths(name) for name in SKILL_NAMES))
    missing = sorted(required - relative_files)
    if missing:
        raise PackageError(f"missing required Skill package files: {', '.join(missing)}")
    packaged_skill_files = {
        relative
        for relative in relative_files
        if relative.split("/", 1)[0] in SKILL_NAMES
    }
    unexpected = sorted(packaged_skill_files - required)
    if unexpected:
        raise PackageError(f"unexpected Skill package files: {', '.join(unexpected)}")

    missing_executables = sorted(EXECUTABLE_FILES - relative_files)
    if missing_executables:
        raise PackageError(
            f"missing executable release files: {', '.join(missing_executables)}"
        )
    for relative in EXECUTABLE_FILES:
        if not ((root / relative).stat().st_mode & 0o111):
            raise PackageError(f"release executable lacks execute permission: {relative}")

    for relative in relative_files:
        parts = Path(relative).parts
        forbidden = FORBIDDEN_PARTS.intersection(parts)
        if forbidden:
            raise PackageError(f"forbidden release path: {relative}")

    for path in files:
        if path.suffix not in {".md", ".yml", ".yaml", ".py", ""}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in USER_PATH_PATTERNS:
            match = pattern.search(text)
            if match:
                raise PackageError(
                    f"user-specific absolute path in {path.relative_to(root)}: {match.group(0)}"
                )
    return files
