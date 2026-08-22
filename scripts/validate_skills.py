#!/usr/bin/env python3
"""Validate the structure and shared policy of the Orca skills in this repo."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from skill_policy import PolicyContractError, load_policy_contract
from workflow_contract import WorkflowContractError, load_workflow_output_contract


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
        "reviews/bugfix.md",
    ),
    "REFACTORING": (
        "templates/refactoring.md",
        "reviews/common.md",
        "reviews/refactoring.md",
    ),
}

REQUIRED_ERROR_CODES = (
    "AGENT_NOT_ALLOWED",
    "INVALID_AGENT_COMMAND",
    "WORKER_REVIEWER_MUST_DIFFER",
    "AGENT_COMMAND_NOT_FOUND",
    "INVALID_PHASE",
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
REPOSITORY_DOCS = (
    "README.md",
    "INSTALL.md",
    "CHANGELOG.md",
    "COMPATIBILITY.md",
    "RELEASING.md",
    "LICENSE-DECISION.md",
    "STEP5_REAL_GLM_GEMMA_SMOKE_REPORT.md",
)
SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)

LIFECYCLE_SKILL_DIR = REPO_ROOT / "orca-worker-reviewer-orchestration"

LIFECYCLE_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Lifecycle accounting contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
LIFECYCLE_CONTRACT_LINE_PATTERN = re.compile(r"([A-Z][A-Z0-9_]*) = (.+)")
LIFECYCLE_CONTRACT_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_]*")

LIFECYCLE_CONTRACT: dict[str, tuple[str, ...]] = {
    "AXIS_A_SETTLEMENT": ("dispatch_and_task_provenance",),
    "AXIS_B_WORKER_RESOURCE": ("supervised_worker_registry",),
    "AXIS_C1_PROCESS_LIVENESS": ("terminal_inspection",),
    "AXIS_C2_CLEANUP_AUTHORITY": ("launch_provenance_and_ownership",),
    "LIFECYCLE_OUTCOMES": ("reuse", "retain", "release", "unsupervised"),
    "CLEANUP_AUTHORITY_STATES": ("authorized", "not_authorized", "unknown"),
    "TERMINAL_ROLE_CLASSES": (
        "coordinator_session",
        "setup_terminal",
        "active_worker",
        "external_or_adopted",
        "phase_worker",
        "phase_reviewer",
        "unknown_role",
    ),
    "NEVER_CLOSE_TERMINAL_ROLES": (
        "coordinator_session",
        "setup_terminal",
        "active_worker",
        "external_or_adopted",
        "unknown_role",
    ),
    "CLOSE_ELIGIBLE_TERMINAL_ROLES": ("phase_worker", "phase_reviewer"),
    "CLOSE_ALLOWED_ONLY_WHEN": ("authorized_and_close_eligible_role",),
    "DEFAULT_WHEN_NOT_AUTHORIZED": ("retain_and_report",),
    "FINALIZATION_PER_DISPATCH": (
        "exactly_once",
        "gate_before_lifecycle_action",
        "settlement_verified_before_lifecycle_action",
    ),
    "TASK_GRAPH_ORDERING": ("create_graph_before_worker_dispatch",),
    "FORCE_READY_USE": ("recovery_only",),
    "CUSTOM_COMMAND_PLACEMENT_ORDER": (
        "worker_start_agent",
        "terminal_create_then_tui_idle_then_worker_start_terminal",
        "dispatch_inject",
    ),
}

LIFECYCLE_AXIS_LABELS = ("(a)", "(b)", "(c1)", "(c2)")
LIFECYCLE_CONTRACT_MAX_LINES = 15

FINAL_REVIEW_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Final review contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)

FINAL_REVIEW_CONTRACT: dict[str, tuple[str, ...]] = {
    "FINAL_REVIEW_TRIGGER": ("after_every_requested_phase_set",),
    "FINAL_REVIEW_STRUCTURE": ("orchestration_only_implicit_gate",),
    "FINAL_REVIEW_ROLE": ("phase_reviewer",),
    "FINAL_REVIEW_TERMINAL_FRESHNESS": ("new_terminal_per_attempt",),
    "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES": ("retain", "release", "unsupervised"),
    "FINAL_REVIEW_TASK_GRAPH": ("single_node_no_dependencies",),
    "FINAL_REVIEW_COUNTER_DOMAINS": ("phase_iterations", "final_review_iterations"),
    "FINAL_REVIEW_ITERATION_BOUND": ("max_iterations",),
    "FINAL_REVIEW_LAST_ATTEMPT_FAIL": ("escalate_before_correction_routing",),
    "FINAL_REVIEW_EXHAUSTION_REASON": ("final_review_max_iterations_reached",),
    "FINAL_REVIEW_OUT_OF_SCOPE_REASON": ("out_of_scope_final_review_finding",),
    "FINAL_REVIEW_COMPLETION_GATE": ("requested_phases_pass_and_final_review_pass",),
}

FINAL_REVIEW_CONTRACT_MAX_LINES = 12
FINAL_REVIEW_SECTION_HEADING = "## 17. Final Adversarial Review"
FINAL_REVIEW_SECTION_END = "\n## 18."
FINAL_REVIEW_ANTI_ANCHORING_SENTENCES = (
    "이전 phase Reviewer의 PASS 판정을 옳다고 가정하지 않는다.",
    "Do NOT assume any previous Reviewer PASS decision is correct.",
)
FINAL_REVIEW_CHECKLIST_ANCHORS = (
    "A objective alignment",
    "B cross-phase consistency",
    "C contract vs implementation",
    "D implementation vs tests",
    "E docs vs behavior",
    "F lifecycle state machine",
    "G security destructive",
    "H over-engineering",
    "I hidden coupling",
)
FINAL_REVIEW_BARE_CHOICE_LINE = re.compile(
    r"(?m)^FINAL_REVIEW:\s*[A-Z_]+\s*\|\s*[A-Z_]+\s*$"
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
    paths = [REPO_ROOT / name for name in REPOSITORY_DOCS]
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


def validate_version(validation: Validation) -> None:
    version_path = REPO_ROOT / "VERSION"
    validation.check(version_path.is_file(), "missing VERSION source of truth")
    if not version_path.is_file():
        return
    raw = version_path.read_text(encoding="utf-8")
    version = raw.strip()
    validation.check(
        raw == f"{version}\n" and bool(SEMVER_PATTERN.fullmatch(version)),
        "VERSION must contain one SemVer MAJOR.MINOR.PATCH line",
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


def validate_machine_readable_contracts(validation: Validation) -> None:
    contracts: list[tuple[Path, dict[str, object]]] = []
    for skill_dir in SKILL_DIRS:
        skill_path = skill_dir / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")
        try:
            contract = load_policy_contract(skill_path)
        except (OSError, PolicyContractError) as exc:
            validation.check(False, str(exc))
            continue
        contracts.append((skill_dir, contract))

        validation.check(
            contract.get("schema_version") == 1,
            f"{skill_dir.name}: unsupported policy contract schema",
        )
        validation.check(
            contract.get("sequential_phases")
            == [phase.casefold() for phase in PHASE_ROUTES if phase not in {"BUGFIX", "REFACTORING"}],
            f"{skill_dir.name}: contract sequential phases do not match phase routing",
        )
        validation.check(
            contract.get("specialized_phases") == ["bugfix", "refactoring"],
            f"{skill_dir.name}: contract specialized phases are invalid",
        )
        validation.check(
            contract.get("supported_specialized_combinations")
            == [["bugfix"], ["refactoring"]],
            f"{skill_dir.name}: contract specialized combinations are invalid",
        )
        validation.check(
            contract.get("natural_language_automation")
            == {
                "deterministic_representative_terms_for": ["phases"],
                "llm_interpretation_required_for": [
                    "worker",
                    "reviewer",
                    "max-iterations",
                    "free-form phase requests",
                ],
            },
            f"{skill_dir.name}: natural-language automation scope is invalid",
        )

        raw_defaults = contract.get("defaults", {})
        defaults = raw_defaults if isinstance(raw_defaults, dict) else {}
        raw_known_commands = contract.get("known_agent_commands", [])
        known_commands = (
            raw_known_commands if isinstance(raw_known_commands, list) else []
        )
        validation.check(
            defaults.get("worker") in known_commands
            and defaults.get("reviewer") in known_commands
            and defaults.get("worker") != defaults.get("reviewer"),
            f"{skill_dir.name}: contract agent defaults/known commands are inconsistent",
        )
        validation.check(
            contract.get("agent_command_pattern") == "[A-Za-z0-9._-]+",
            f"{skill_dir.name}: contract agent command pattern is invalid",
        )
        validation.check(
            contract.get("custom_agent_command_pattern")
            == "(?:claude|codex)-[A-Za-z0-9._-]+",
            f"{skill_dir.name}: contract custom agent trust pattern is invalid",
        )
        validation.check(
            contract.get("agent_launch_arguments") == [],
            f"{skill_dir.name}: agent launch arguments must be empty",
        )
        validation.check(
            "--dangerously-skip-permissions" not in skill_text,
            f"{skill_dir.name}: contains a vendor-specific agent launch argument",
        )
        validation.check(
            f"DEFAULT_WORKER = {defaults.get('worker')}" in skill_text
            and f"DEFAULT_REVIEWER = {defaults.get('reviewer')}" in skill_text
            and f"DEFAULT_MAX_ITERATIONS = {defaults.get('max_iterations')}"
            in skill_text,
            f"{skill_dir.name}: human-readable defaults differ from contract",
        )

        known_commands_match = re.search(
            r"기본 known commands:\s*```text\s*(?P<values>.*?)\s*```",
            skill_text,
            re.DOTALL,
        )
        documented_known_commands = (
            [
                line.strip()
                for line in known_commands_match.group("values").splitlines()
                if line.strip()
            ]
            if known_commands_match
            else []
        )
        validation.check(
            documented_known_commands == known_commands,
            f"{skill_dir.name}: human-readable known commands differ from contract",
        )
        raw_iteration_range = contract.get("max_iterations", {})
        iteration_range = (
            raw_iteration_range if isinstance(raw_iteration_range, dict) else {}
        )
        validation.check(
            iteration_range.get("min") == 1
            and iteration_range.get("max") == 10
            and iteration_range["min"]
            <= defaults.get("max_iterations", 0)
            <= iteration_range["max"],
            f"{skill_dir.name}: contract max-iterations values are inconsistent",
        )
        validation.check(
            bool(
                re.search(
                    rf"{iteration_range.get('min')}\s*<=\s*max-iterations\s*<=\s*{iteration_range.get('max')}",
                    skill_text,
                )
            ),
            f"{skill_dir.name}: human-readable max-iterations range differs from contract",
        )

        raw_errors = contract.get("errors", {})
        errors = raw_errors if isinstance(raw_errors, dict) else {}
        required_error_keys = {
            "agent_not_allowed",
            "invalid_agent_command",
            "agent_command_not_found",
            "worker_reviewer_must_differ",
            "invalid_max_iterations",
            "invalid_phase",
            "invalid_phase_order",
            "phase_conflict",
            "unsupported_phase_combination",
        }
        validation.check(
            set(errors) == required_error_keys
            and set(errors.values()).issubset(REQUIRED_ERROR_CODES),
            f"{skill_dir.name}: contract error mapping is incomplete or invalid",
        )
        validation.check(
            all(f"REASON: {error_code}" in skill_text for error_code in errors.values()),
            f"{skill_dir.name}: human-readable error mapping differs from contract",
        )

    if len(contracts) == len(SKILL_DIRS):
        validation.check(
            contracts[0][1] == contracts[1][1],
            "machine-readable policy contracts differ between skills",
        )


def parse_lifecycle_contract(skill_text: str) -> dict[str, tuple[str, ...]] | None:
    """Parse the section 6 anchor block into {KEY: (value, ...)}.

    Returns None when the block is absent or violates the format rules; the caller
    turns that single condition into one diagnostic instead of many derived ones.
    """
    match = LIFECYCLE_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return None
    parsed: dict[str, tuple[str, ...]] = {}
    for line in match.group("values").splitlines():
        line_match = LIFECYCLE_CONTRACT_LINE_PATTERN.fullmatch(line)
        if line_match is None:
            return None
        key, raw = line_match.group(1), line_match.group(2)
        if key in parsed:
            return None
        values = tuple(value.strip() for value in raw.split(","))
        if not all(
            LIFECYCLE_CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values
        ):
            return None
        parsed[key] = values
    return parsed


def lifecycle_contract_block_lines(skill_text: str) -> int:
    match = LIFECYCLE_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return 0
    return len(match.group("values").splitlines())


def validate_lifecycle_accounting_contract(validation: Validation) -> None:
    """Orchestration-only section 6 lifecycle contract. Not shared with the loop skill."""
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        validation.check(False, f"{LIFECYCLE_SKILL_DIR.name}: {exc}")
        skill_text = ""

    parsed = parse_lifecycle_contract(skill_text)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: missing or malformed lifecycle accounting "
        "contract block",
    )
    parsed = parsed or {}

    validation.check(
        set(parsed) == set(LIFECYCLE_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle contract keys differ from the "
        "validator source of truth",
    )
    validation.check(
        parsed == LIFECYCLE_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle contract values differ from the "
        "validator source of truth",
    )
    validation.check(
        0 < lifecycle_contract_block_lines(skill_text) <= LIFECYCLE_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle contract block exceeds "
        f"{LIFECYCLE_CONTRACT_MAX_LINES} lines",
    )

    section = extract_lifecycle_section(skill_text)
    for label in LIFECYCLE_AXIS_LABELS:
        validation.check(
            label in section,
            f"{LIFECYCLE_SKILL_DIR.name}: lifecycle prose is missing axis label "
            f"{label}",
        )
    missing_outcomes = [
        outcome
        for outcome in LIFECYCLE_CONTRACT["LIFECYCLE_OUTCOMES"]
        if outcome not in section
    ]
    validation.check(
        not missing_outcomes,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle prose is missing outcome "
        + ", ".join(missing_outcomes or ["-"]),
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_lifecycle_contract(loop_text) is None,
        "orca-worker-reviewer-loop: must not contain the orchestration lifecycle "
        "contract",
    )

    never_close = set(parsed.get("NEVER_CLOSE_TERMINAL_ROLES", ()))
    close_eligible = set(parsed.get("CLOSE_ELIGIBLE_TERMINAL_ROLES", ()))
    all_roles = set(parsed.get("TERMINAL_ROLE_CLASSES", ()))
    expected_never_close = set(LIFECYCLE_CONTRACT["NEVER_CLOSE_TERMINAL_ROLES"])
    validation.check(
        never_close == expected_never_close,
        "NEVER_CLOSE_TERMINAL_ROLES must contain exactly "
        + ", ".join(sorted(expected_never_close)),
    )
    validation.check(
        close_eligible == set(LIFECYCLE_CONTRACT["CLOSE_ELIGIBLE_TERMINAL_ROLES"]),
        "CLOSE_ELIGIBLE_TERMINAL_ROLES must be exactly phase_worker, phase_reviewer",
    )
    validation.check(
        bool(all_roles)
        and not (never_close & close_eligible)
        and (never_close | close_eligible) == all_roles,
        "terminal role classes must partition into never-close and close-eligible",
    )
    validation.check(
        parsed.get("CLOSE_ALLOWED_ONLY_WHEN")
        == LIFECYCLE_CONTRACT["CLOSE_ALLOWED_ONLY_WHEN"],
        "CLOSE_ALLOWED_ONLY_WHEN must require a close eligible terminal role",
    )
    validation.check(
        "coordinator_session" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: lifecycle prose must name coordinator_session",
    )


def extract_lifecycle_section(skill_text: str) -> str:
    """Return the body of section 6, where the lifecycle prose must live."""
    start = skill_text.find("## 6. Orca-native Worker Placement")
    if start == -1:
        return ""
    end = skill_text.find("\n## 7.", start)
    return skill_text[start:] if end == -1 else skill_text[start:end]



def parse_final_review_contract(skill_text: str) -> dict[str, tuple[str, ...]] | None:
    """Parse the section 17 anchor block into {KEY: (value, ...)}.

    Deliberately a separate implementation from parse_lifecycle_contract rather than a
    shared extraction: the two blocks have different key prefixes, different line caps
    and different structural cross-checks, and twelve existing regression tests bind to
    the lifecycle parser. Only the two shared regexes are reused.
    """
    match = FINAL_REVIEW_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return None
    parsed: dict[str, tuple[str, ...]] = {}
    for line in match.group("values").splitlines():
        line_match = LIFECYCLE_CONTRACT_LINE_PATTERN.fullmatch(line)
        if line_match is None:
            return None
        key, raw = line_match.group(1), line_match.group(2)
        if key in parsed:
            return None
        values = tuple(value.strip() for value in raw.split(","))
        if not all(
            LIFECYCLE_CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values
        ):
            return None
        parsed[key] = values
    return parsed


def final_review_contract_block_lines(skill_text: str) -> int:
    """0 when the block is absent, else its line count."""
    match = FINAL_REVIEW_CONTRACT_BLOCK_PATTERN.search(skill_text)
    if match is None:
        return 0
    return len(match.group("values").splitlines())


def extract_final_review_section(skill_text: str) -> str:
    """Return the body of section 17, where the final-review prose must live.

    Unlike extract_lifecycle_section this DOES return "" on a missing anchor, but the
    caller turns that into a dedicated diagnostic rather than letting the content
    checks fail with derived messages.
    """
    start = skill_text.find(FINAL_REVIEW_SECTION_HEADING)
    if start == -1:
        return ""
    end = skill_text.find(FINAL_REVIEW_SECTION_END, start)
    return skill_text[start:] if end == -1 else skill_text[start:end]


def validate_final_review_contract(validation: Validation) -> None:
    """Orchestration-only section 17 final adversarial review gate contract."""
    skill_path = LIFECYCLE_SKILL_DIR / "SKILL.md"
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        validation.check(False, f"{LIFECYCLE_SKILL_DIR.name}: {exc}")
        skill_text = ""

    parsed = parse_final_review_contract(skill_text)
    validation.check(
        parsed is not None,
        f"{LIFECYCLE_SKILL_DIR.name}: missing or malformed final review contract "
        "block",
    )
    parsed = parsed or {}

    validation.check(
        set(parsed) == set(FINAL_REVIEW_CONTRACT),
        f"{LIFECYCLE_SKILL_DIR.name}: final review contract keys differ from the "
        "validator source of truth",
    )
    validation.check(
        parsed == FINAL_REVIEW_CONTRACT,
        f"{LIFECYCLE_SKILL_DIR.name}: final review contract values differ from the "
        "validator source of truth",
    )
    validation.check(
        0 < final_review_contract_block_lines(skill_text)
        <= FINAL_REVIEW_CONTRACT_MAX_LINES,
        f"{LIFECYCLE_SKILL_DIR.name}: final review contract block exceeds "
        f"{FINAL_REVIEW_CONTRACT_MAX_LINES} lines",
    )

    section = extract_final_review_section(skill_text)
    validation.check(
        section != "",
        f"{LIFECYCLE_SKILL_DIR.name}: section 17 Final Adversarial Review is missing "
        "or renumbered",
    )
    validation.check(
        FINAL_REVIEW_ANTI_ANCHORING_SENTENCES[0] in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the "
        "anti-anchoring premise (ko)",
    )
    validation.check(
        FINAL_REVIEW_ANTI_ANCHORING_SENTENCES[1] in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the "
        "anti-anchoring premise (en)",
    )
    validation.check(
        "Responsible Phase" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the Responsible "
        "Phase field",
    )
    validation.check(
        "FINAL_REVIEW_MAX_ITERATIONS_REACHED" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the exhaustion "
        "reason",
    )
    validation.check(
        "OUT_OF_SCOPE_FINAL_REVIEW_FINDING" in section,
        f"{LIFECYCLE_SKILL_DIR.name}: final review prose is missing the out-of-scope "
        "reason",
    )
    missing_anchors = [
        anchor for anchor in FINAL_REVIEW_CHECKLIST_ANCHORS if anchor not in section
    ]
    validation.check(
        not missing_anchors,
        f"{LIFECYCLE_SKILL_DIR.name}: final review checklist is missing "
        + ", ".join(missing_anchors or ["-"]),
    )

    outcomes = set(parsed.get("FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES", ()))
    lifecycle_outcomes = set(LIFECYCLE_CONTRACT["LIFECYCLE_OUTCOMES"])
    validation.check(
        "reuse" not in outcomes,
        "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES must never contain reuse",
    )
    validation.check(
        bool(outcomes) and outcomes < lifecycle_outcomes,
        "FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES must be a strict subset of "
        "LIFECYCLE_OUTCOMES",
    )
    role = parsed.get("FINAL_REVIEW_ROLE", ())
    validation.check(
        bool(role) and role[0] in LIFECYCLE_CONTRACT["CLOSE_ELIGIBLE_TERMINAL_ROLES"],
        "FINAL_REVIEW_ROLE must be a close eligible terminal role",
    )

    loop_skill = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
    loop_text = loop_skill.read_text(encoding="utf-8") if loop_skill.is_file() else ""
    validation.check(
        parse_final_review_contract(loop_text) is None,
        "orca-worker-reviewer-loop: must not contain the orchestration final review "
        "contract",
    )
    validation.check(
        "Final Adversarial Review" not in loop_text,
        "orca-worker-reviewer-loop: must not describe the final adversarial review "
        "gate",
    )

    validation.check(
        FINAL_REVIEW_BARE_CHOICE_LINE.search(skill_text) is None,
        f"{LIFECYCLE_SKILL_DIR.name}: FINAL_REVIEW must not be written as a "
        "PASS | FAIL choice line",
    )


def validate_workflow_output_contracts(validation: Validation) -> None:
    contracts = []
    for skill_dir in SKILL_DIRS:
        skill_path = skill_dir / "SKILL.md"
        try:
            contract = load_workflow_output_contract(skill_path)
        except (OSError, WorkflowContractError) as exc:
            validation.check(False, str(exc))
            continue
        contracts.append(contract)
        validation.check(
            contract.finding_resolution_values
            == ("RESOLVED", "DISPUTED", "BLOCKED"),
            f"{skill_dir.name}: invalid finding resolution contract",
        )

    if len(contracts) == len(SKILL_DIRS):
        validation.check(
            contracts[0] == contracts[1],
            "Worker/Reviewer output contracts differ between skills",
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
    validate_machine_readable_contracts(validation)
    validate_workflow_output_contracts(validation)
    validate_lifecycle_accounting_contract(validation)
    validate_final_review_contract(validation)
    validate_version(validation)
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
