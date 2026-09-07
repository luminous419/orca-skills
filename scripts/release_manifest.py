#!/usr/bin/env python3
"""Shared release-package manifest rules."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATION_SKILL_NAME = "orca-worker-reviewer-orchestration"
SKILL_NAMES = (
    "orca-worker-reviewer-loop",
    ORCHESTRATION_SKILL_NAME,
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
ROOT_FILES = (
    "README.md",
    "INSTALL.md",
    "VERSION",
    "CHANGELOG.md",
    "requirements-langgraph.txt",
)
REQUIRED_DOCS = (
    "docs/ROADMAP.md",
    "docs/COMPATIBILITY.md",
    "docs/RELEASING.md",
    "docs/LICENSE-DECISION.md",
    "docs/DETERMINISTIC_WORKFLOW.md",
    "docs/LANGGRAPH_DEPENDENCIES.md",
    "docs/examples/DETERMINISTIC_WORKFLOW_TRACE.md",
    "docs/examples/FULL_WORKFLOW_FAIL_CORRECTION.md",
    "docs/examples/FULL_WORKFLOW_FAIL_CORRECTION.ko.md",
    "docs/validation/GLM_GEMMA_SMOKE_PROCEDURE.md",
    "docs/validation/historical/GLM_GEMMA_SMOKE_REPORT_2026-08-20.md",
)
INCLUDED_ROOTS = (".github", "docs", "scripts", *SKILL_NAMES)
EXECUTABLE_FILES = frozenset({"scripts/fake_bin/fake-agent"})
# OS-42: the full local-import closure of the decision-gate contract, in dependency
# order. Computed once by AST walk and pinned here; the test that recomputes it is what
# keeps this honest as the closure evolves.
DECISION_CONTRACT_CLOSURE = (
    "decision_contract",
    "decision_gate",
    "decision_policy",
    "skill_policy",
    "agent_profile",
    "quality_profile",
)
# OS-42 F-002: the local-import closure of the PRODUCTION Orca execution path, in
# dependency order. `OrcaAdapter` ships inside the engine package and takes its runtime by
# injection, but the runtime it is given -- `orca_runtime_harness` -- lived only in this
# repository, so an installed launcher could offer nothing but the fake adapter and the
# bounded validation-repair loop could never reach a real dispatch. These three modules
# are the difference between the two closures; every other module the harness imports is
# already installed by DECISION_CONTRACT_CLOSURE or ships beside it.
# `test_installed_orca_runtime_is_self_contained` recomputes the closure by walking ASTs,
# so a future import fails the test until this list is updated.
ORCA_RUNTIME_CLOSURE = (
    "orca_runtime_harness",
    "task_context",
    "workflow_contract",
)
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
    if skill_name == ORCHESTRATION_SKILL_NAME:
        # OS-17 review round 3 MAJOR-1: the run-scoped logging CLI must ship inside
        # the installed Skill itself (INSTALL.md's `cp -R` never copies this
        # repository's scripts/), so this one extra file is part of this skill's
        # distributable definition. The loop skill has no such tool.
        paths.add(f"{skill_name}/tools/run_logging.py")
        paths.add(f"{skill_name}/tools/clarification_protocol.py")
        paths.add(f"{skill_name}/tools/run_workflow.py")
        # OS-42. The decision-gate contract is the single source of truth for the
        # Worker's machine-control output, so an installed Coordinator must be able to
        # GENERATE the instructions from it and VALIDATE what comes back. That needs the
        # whole import closure, not just the entry point: decision_contract imports
        # decision_gate and decision_policy, decision_policy imports skill_policy,
        # skill_policy imports agent_profile, and agent_profile imports quality_profile.
        # Shipping a prefix of that chain yields an ImportError on the first
        # load_decision_policy call in exactly the environment the packaging exists for.
        # `test_installed_contract_modules_are_self_contained` recomputes this closure by
        # walking ASTs, so a future import fails the test until this list is updated.
        paths.update(
            f"{skill_name}/tools/{module}.py"
            for module in DECISION_CONTRACT_CLOSURE
        )
        # OS-42 F-002. Without these the installed `--adapter orca` path would import a
        # module that is not in the package, which is an ImportError in exactly the
        # environment the packaging exists for.
        paths.update(
            f"{skill_name}/tools/{module}.py"
            for module in ORCA_RUNTIME_CLOSURE
        )
        engine = REPO_ROOT / skill_name / "tools" / "deterministic_workflow"
        paths.update(
            f"{skill_name}/tools/deterministic_workflow/{path.relative_to(engine).as_posix()}"
            for path in engine.rglob("*.py")
        )
    return paths


def archive_mode(relative_path: str) -> int:
    return 0o755 if relative_path in EXECUTABLE_FILES else 0o644


def release_files(root: Path = REPO_ROOT) -> tuple[Path, ...]:
    files = [root / name for name in ROOT_FILES]
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
    for name in (*ROOT_FILES, *REQUIRED_DOCS):
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
