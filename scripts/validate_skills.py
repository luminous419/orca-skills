#!/usr/bin/env python3
"""Validate the structure and shared policy of the Orca skills in this repo."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIRS = (
    REPO_ROOT / "orca-worker-reviewer-loop",
    REPO_ROOT / "orca-worker-reviewer-orchestration",
)

PHASE_ROUTES = {
    "ANALYSIS": (
        "templates/analysis.md",
        "reviews/common.md",
        "reviews/analysis.md",
    ),
    "PLAN": ("templates/plan.md", "reviews/common.md", "reviews/plan.md"),
    "DESIGN": ("templates/design.md", "reviews/common.md", "reviews/design.md"),
    "IMPLEMENTATION": (
        "templates/implementation.md",
        "reviews/common.md",
        "reviews/implementation.md",
    ),
    "TEST": ("templates/test.md", "reviews/common.md", "reviews/test.md"),
    "BUGFIX": (
        "templates/bugfix.md",
        "reviews/common.md",
        "reviews/implementation.md",
    ),
    "REFACTORING": (
        "templates/refactoring.md",
        "reviews/common.md",
        "reviews/refactoring.md",
    ),
}

REQUIRED_ERROR_CODES = (
    "AGENT_NOT_ALLOWED",
    "WORKER_REVIEWER_MUST_DIFFER",
    "AGENT_COMMAND_NOT_FOUND",
    "INVALID_PHASE_ORDER",
    "UNSUPPORTED_PHASE_COMBINATION",
    "PHASE_CONFLICT",
    "PREVIOUS_PHASE_CHANGE_REQUIRED",
    "INVALID_MAX_ITERATIONS",
)

USER_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"/Users/(?!<|\{)[^/\s`]+"),
    re.compile(r"/home/(?!<|\{)[^/\s`]+"),
    re.compile(r"[A-Za-z]:\\Users\\(?!<|\{)[^\\\s`]+"),
)


class Validation:
    def __init__(self) -> None:
        self.checks = 0
        self.errors: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.errors.append(message)


def parse_frontmatter(path: Path) -> dict[str, str]:
    """Parse the flat YAML subset used by SKILL.md frontmatter.

    The repository intentionally has no third-party runtime dependency. This
    parser supports scalar keys and YAML folded/literal block scalars, rejects
    duplicate keys, and fails on unsupported nested YAML instead of accepting it
    ambiguously.
    """

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing opening '---'")

    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("missing closing '---'") from exc

    data: dict[str, str] = {}
    frontmatter = lines[1:closing]
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace() or ":" not in line:
            raise ValueError(f"unsupported YAML at frontmatter line {index + 2}")

        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
            raise ValueError(f"invalid YAML key {key!r}")
        if key in data:
            raise ValueError(f"duplicate YAML key {key!r}")

        if raw_value in {">", ">-", ">+", "|", "|-", "|+"}:
            block: list[str] = []
            index += 1
            while index < len(frontmatter):
                block_line = frontmatter[index]
                if block_line and not block_line[:1].isspace():
                    break
                block.append(block_line.strip())
                index += 1
            separator = " " if raw_value.startswith(">") else "\n"
            value = separator.join(block).strip()
        else:
            if not raw_value:
                raise ValueError(f"empty or nested YAML value for {key!r}")
            if (raw_value.startswith("[") or raw_value.startswith("{")):
                raise ValueError(f"unsupported collection value for {key!r}")
            if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in "\"'":
                raw_value = raw_value[1:-1]
            value = raw_value
            index += 1

        data[key] = value

    return data


def extract_phase_routes(skill_text: str) -> dict[str, tuple[str, ...]]:
    lines = skill_text.splitlines()
    routes: dict[str, tuple[str, ...]] = {}
    phase_pattern = re.compile(
        rf"^({'|'.join(PHASE_ROUTES)})(?::|\s+→)", re.ASCII
    )
    path_pattern = re.compile(r"(?:templates|reviews)/[a-z-]+\.md")

    for index, line in enumerate(lines):
        match = phase_pattern.match(line.strip())
        if not match:
            continue
        phase = match.group(1)
        context = line if "→" in line else "\n".join(lines[index : index + 4])
        paths = tuple(dict.fromkeys(path_pattern.findall(context)))
        if paths:
            routes[phase] = paths
    return routes


def validate_frontmatter(validation: Validation, skill_dir: Path) -> None:
    path = skill_dir / "SKILL.md"
    try:
        metadata = parse_frontmatter(path)
    except (OSError, ValueError) as exc:
        validation.check(False, f"{path.relative_to(REPO_ROOT)}: invalid YAML frontmatter: {exc}")
        return

    validation.check(bool(metadata.get("name")), f"{path}: frontmatter name is required")
    validation.check(
        metadata.get("name") == skill_dir.name,
        f"{path}: frontmatter name must match directory name",
    )
    validation.check(
        bool(metadata.get("description")), f"{path}: frontmatter description is required"
    )


def validate_routes_and_files(validation: Validation, skill_dir: Path) -> None:
    skill_path = skill_dir / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    routes = extract_phase_routes(text)

    for phase, expected_paths in PHASE_ROUTES.items():
        for relative_path in expected_paths:
            validation.check(
                (skill_dir / relative_path).is_file(),
                f"{skill_dir.name}: missing {relative_path} required by {phase}",
            )
        validation.check(
            routes.get(phase) == expected_paths,
            f"{skill_dir.name}: {phase} routing is {routes.get(phase)!r}, expected {expected_paths!r}",
        )


def validate_shared_directories(validation: Validation) -> None:
    left, right = SKILL_DIRS
    for subdir in ("templates", "reviews"):
        left_files = {
            path.relative_to(left / subdir): path
            for path in (left / subdir).rglob("*")
            if path.is_file()
        }
        right_files = {
            path.relative_to(right / subdir): path
            for path in (right / subdir).rglob("*")
            if path.is_file()
        }
        validation.check(
            left_files.keys() == right_files.keys(),
            f"{subdir}/ file sets differ between skills",
        )
        for relative_path in sorted(left_files.keys() & right_files.keys()):
            validation.check(
                left_files[relative_path].read_bytes() == right_files[relative_path].read_bytes(),
                f"{subdir}/{relative_path} differs between skills",
            )


def validate_no_user_absolute_paths(validation: Validation) -> None:
    paths = [REPO_ROOT / "README.md", REPO_ROOT / "INSTALL.md"]
    for skill_dir in SKILL_DIRS:
        paths.extend(skill_dir.rglob("*.md"))

    for path in sorted(paths):
        text = path.read_text(encoding="utf-8")
        for pattern in USER_ABSOLUTE_PATH_PATTERNS:
            match = pattern.search(text)
            validation.check(
                match is None,
                f"{path.relative_to(REPO_ROOT)}: user-specific absolute path {match.group(0)!r}"
                if match
                else "",
            )


def validate_policy_contracts(validation: Validation, skill_dir: Path) -> None:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    for error_code in REQUIRED_ERROR_CODES:
        validation.check(
            error_code in text,
            f"{skill_dir.name}: missing required error code {error_code}",
        )

    validation.check(
        bool(
            re.search(
                r"IMPLEMENTATION[\s\S]{0,600}(?:Unit Test|Unit Tests)[\s\S]{0,300}(?:PASS|required|필수)",
                text,
                re.IGNORECASE,
            )
        ),
        f"{skill_dir.name}: missing IMPLEMENTATION Unit Test gate",
    )
    validation.check(
        bool(
            re.search(
                r"BUGFIX[\s\S]{0,400}(?:Regression Test|regression test)[\s\S]{0,200}(?:PASS|required|필수)",
                text,
                re.IGNORECASE,
            )
        ),
        f"{skill_dir.name}: missing BUGFIX Regression Test gate",
    )
    validation.check(
        bool(re.search(r"1\s*<=\s*max-iterations\s*<=\s*10", text)),
        f"{skill_dir.name}: missing max-iterations range 1 <= max-iterations <= 10",
    )


def main() -> int:
    validation = Validation()

    discovered_skill_dirs = tuple(
        sorted(path.parent for path in REPO_ROOT.glob("*/SKILL.md"))
    )
    validation.check(bool(discovered_skill_dirs), "no SKILL.md files found")
    for skill_dir in discovered_skill_dirs:
        validate_frontmatter(validation, skill_dir)

    for skill_dir in SKILL_DIRS:
        validation.check(skill_dir.is_dir(), f"missing skill directory: {skill_dir}")
        validation.check(
            (skill_dir / "SKILL.md").is_file(),
            f"{skill_dir.name}: missing SKILL.md",
        )
        if not (skill_dir / "SKILL.md").is_file():
            continue
        validate_routes_and_files(validation, skill_dir)
        validate_policy_contracts(validation, skill_dir)

    validate_shared_directories(validation)
    validate_no_user_absolute_paths(validation)

    if validation.errors:
        print(f"Skill validation FAILED ({len(validation.errors)} errors, {validation.checks} checks)")
        for error in validation.errors:
            print(f"- {error}")
        return 1

    print(f"Skill validation PASSED ({validation.checks} checks)")
    print("Validated both skills, shared templates/reviews, routing, and policy gates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
