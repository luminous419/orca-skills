#!/usr/bin/env python3
"""Project Quality Profile: schema, loader, validation, and phase applicability.

The Reviewer gate used to be a broad generic software-quality checklist. It is now
two layers: a deliberately small general gate, and the project's own quality
attributes read from `.orca/quality-profile.yaml`. This module owns the second layer
and the constants that spell out the first, so that both the dispatched Task spec
(scripts/task_context.py) and the review policy documents describe the same model.

Standard library only, like every other module in scripts/. That is why the YAML
reader below is a deliberately restricted subset parser rather than PyYAML: nothing
in this repository may depend on a third-party package, and a profile that only
parses on machines that happen to have PyYAML installed is a silent behaviour fork.
The parser refuses whatever it does not understand instead of guessing, which is the
same fail-closed rule the validation below follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The one default location. There is deliberately no search path, no inheritance, no
# remote profile and no organization-wide profile: a configuration hierarchy is what
# makes "which rule applied?" unanswerable, and OS-1 scopes all of that out.
DEFAULT_PROFILE_PATH = ".orca/quality-profile.yaml"

# Only version 1 exists. An unsupported version is an explicit error, never a
# best-effort parse of a schema this code has never seen.
SUPPORTED_SCHEMA_VERSIONS = (1,)

# The categories from the schema. A closed set on purpose: the point of a category is
# to say what KIND of project-specific concern an attribute encodes, and a free-form
# string cannot be checked, reported on, or reviewed consistently.
QUALITY_CATEGORIES = (
    "business-domain",
    "platform-infrastructure",
    "team-convention",
    "operational-risk",
)

# The phases an attribute may declare in applies_to. `final_review` is absent on
# purpose: the Final Adversarial Review re-checks every attribute applicable to the
# requested workflow, so declaring it there would be either redundant or a way to
# hide an attribute from the phase that actually produces the work.
APPLICABLE_PHASES = (
    "analysis",
    "plan",
    "design",
    "implementation",
    "test",
    "bugfix",
    "refactoring",
)

ATTRIBUTE_REQUIRED_KEYS = ("id", "category", "name", "blocking")
ATTRIBUTE_OPTIONAL_KEYS = ("description", "applies_to", "verification")
ATTRIBUTE_KEYS = (*ATTRIBUTE_REQUIRED_KEYS, *ATTRIBUTE_OPTIONAL_KEYS)
PROFILE_KEYS = ("version", "quality_attributes")

# ---- layer 1: the Minimal General Gate -------------------------------------------
# Five categories, and it stays five. This is the ONLY blocking criterion that
# applies with no project profile at all, and the thing it exists to replace is the
# open-ended quality checklist that used to promote any generic concern to a FAIL.
# The labels are the machine-checkable anchors; reviews/common.md carries the prose
# for the same five ids and validate_skills.py binds the two together.
MINIMAL_GENERAL_GATE = (
    ("G1", "explicit requirement violation"),
    ("G2", "result does not work"),
    ("G3", "severe regression"),
    ("G4", "data loss security irreversible side effect"),
    ("G5", "missing validation evidence"),
)
GENERAL_GATE_IDS = tuple(gate_id for gate_id, _ in MINIMAL_GENERAL_GATE)

# The explicit non-list. Without it "not in the general gate" is an inference each
# reviewer makes for itself, which is exactly how a style preference becomes a FAIL.
NON_BLOCKING_BY_DEFAULT = (
    "clean architecture preference",
    "SOLID preference",
    "naming taste",
    "minor duplication",
    "documentation polish",
    "speculative future extensibility",
    "generalized best practice",
    "stylistic refactoring suggestion",
    "personal design preference",
)

# The order a Reviewer actually decides in. Anything outside these four tiers is
# never promoted to a blocking finding on the Reviewer's own initiative.
DECISION_PRIORITY = (
    "1 explicit user/project requirements",
    "2 applicable project quality profile attributes",
    "3 current phase contract",
    "4 minimal general gate",
)

# ---- verdicts ---------------------------------------------------------------------
# Four report verdicts, two workflow gate values. PASS WITH NOTES is an annotation on
# the review report, NOT a new lifecycle state: the `RESULT:` line an orchestration
# harness parses stays two-valued, so task settlement, the FAIL loop, downstream
# revalidation and the Final Review trigger are all untouched by it.
VERDICT_PASS = "PASS"
VERDICT_PASS_WITH_NOTES = "PASS WITH NOTES"
VERDICT_FAIL = "FAIL"
VERDICT_BLOCKED = "BLOCKED"
REPORT_VERDICTS = (
    VERDICT_PASS,
    VERDICT_PASS_WITH_NOTES,
    VERDICT_FAIL,
    VERDICT_BLOCKED,
)
WORKFLOW_GATE_VALUES = (VERDICT_PASS, VERDICT_FAIL)
VERDICT_GATE_MAPPING = {
    VERDICT_PASS: VERDICT_PASS,
    VERDICT_PASS_WITH_NOTES: VERDICT_PASS,
    VERDICT_FAIL: VERDICT_FAIL,
    # BLOCKED is a FAIL at the gate whose Required Action is to supply the missing
    # project information or evidence. It is not a third gate value: giving the gate
    # a third value would change every parser and every state transition that reads
    # it, for a case the existing FAIL loop already routes correctly.
    VERDICT_BLOCKED: VERDICT_FAIL,
}

# ---- profile resolution states ----------------------------------------------------
PROFILE_STATUS_LOADED = "loaded"
PROFILE_STATUS_ABSENT = "absent"
PROFILE_STATUS_INVALID = "invalid"
PROFILE_STATUSES = (
    PROFILE_STATUS_LOADED,
    PROFILE_STATUS_ABSENT,
    PROFILE_STATUS_INVALID,
)

# The reason code a Coordinator reports when a profile exists but does not validate.
# It is a pre-dispatch validation failure, in the same shape as INVALID_PHASE.
INVALID_PROFILE_REASON = "INVALID_QUALITY_PROFILE"


class QualityProfileError(ValueError):
    """Raised when a profile exists but cannot be trusted.

    Never raised for a missing profile: absent is a legitimate state with its own
    defined semantics, and conflating the two is what would let a typo in the path
    read as "this project has no quality attributes".
    """


# ---- the YAML subset ---------------------------------------------------------------


def _strip_comment(line: str) -> str:
    """Remove a trailing `#` comment when the line carries no quote character.

    Quoted values are left alone rather than parsed, because a half-correct comment
    stripper that eats a `#` inside a quoted string is worse than one that declines.
    """
    if '"' in line or "'" in line:
        return line
    index = line.find(" #")
    if index != -1:
        return line[:index]
    return line


def _scalar(raw: str) -> Any:
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~", ""):
        return None
    try:
        return int(text)
    except ValueError:
        return text


@dataclass(frozen=True)
class _Line:
    indent: int
    text: str
    number: int


def _tokenize(source: str) -> list[_Line]:
    lines: list[_Line] = []
    for number, raw in enumerate(source.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise QualityProfileError(f"line {number}: tabs are not valid indentation")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        text = _strip_comment(raw).rstrip()
        if not text.strip():
            continue
        lines.append(_Line(len(text) - len(text.lstrip()), text.strip(), number))
    return lines


def _block_scalar(lines: list[_Line], index: int, indent: int, folded: bool) -> tuple[str, int]:
    collected: list[str] = []
    while index < len(lines) and lines[index].indent > indent:
        collected.append(lines[index].text)
        index += 1
    return (" " if folded else "\n").join(collected), index


def _parse_block(lines: list[_Line], index: int, indent: int) -> tuple[Any, int]:
    """Parse one mapping or one sequence at `indent`, returning (value, next index)."""
    if index >= len(lines):
        raise QualityProfileError("unexpected end of profile")
    if lines[index].text.startswith("- "):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_sequence(lines: list[_Line], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if not line.text.startswith("- "):
            raise QualityProfileError(
                f"line {line.number}: expected a '- ' sequence entry"
            )
        entry = line.text[2:].strip()
        if not entry:
            raise QualityProfileError(f"line {line.number}: empty sequence entry")
        if ":" in entry and not entry.startswith(("'", '"')):
            # `- key: value` opens a mapping whose remaining keys sit two columns in.
            nested_indent = indent + 2
            synthetic = [_Line(nested_indent, entry, line.number)]
            index += 1
            while index < len(lines) and lines[index].indent >= nested_indent:
                synthetic.append(lines[index])
                index += 1
            value, consumed = _parse_mapping(synthetic, 0, nested_indent)
            if consumed != len(synthetic):
                raise QualityProfileError(f"line {line.number}: malformed list item")
            items.append(value)
            continue
        items.append(_scalar(entry))
        index += 1
    return items, index


def _parse_mapping(lines: list[_Line], index: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        key, separator, raw_value = line.text.partition(":")
        if not separator:
            raise QualityProfileError(f"line {line.number}: expected 'key: value'")
        key = key.strip()
        if not key:
            raise QualityProfileError(f"line {line.number}: empty key")
        if key in mapping:
            raise QualityProfileError(f"line {line.number}: duplicate key {key!r}")
        raw_value = raw_value.strip()
        index += 1
        if raw_value in (">", "|", ">-", "|-"):
            mapping[key], index = _block_scalar(
                lines, index, indent, folded=raw_value.startswith(">")
            )
            continue
        if raw_value:
            mapping[key] = _scalar(raw_value)
            continue
        if index >= len(lines) or lines[index].indent <= indent:
            raise QualityProfileError(
                f"line {line.number}: key {key!r} has no value and no nested block"
            )
        mapping[key], index = _parse_block(lines, index, lines[index].indent)
    return mapping, index


def parse_profile_document(source: str) -> dict[str, Any]:
    """Parse the restricted YAML subset a quality profile is written in.

    Supports exactly what the schema needs: `key: scalar`, nested mappings, block
    sequences of scalars and of mappings, and `>`/`|` block scalars. Anything else
    raises rather than being skipped, so a profile written with a construct this
    reader does not model reads as invalid instead of as partially applied.
    """
    lines = _tokenize(source)
    if not lines:
        raise QualityProfileError("profile is empty")
    if lines[0].indent != 0:
        raise QualityProfileError("line 1: profile must start at column 0")
    document, consumed = _parse_block(lines, 0, 0)
    if consumed != len(lines):
        raise QualityProfileError(
            f"line {lines[consumed].number}: unexpected indentation"
        )
    if not isinstance(document, dict):
        raise QualityProfileError("profile root must be a mapping")
    return document


# ---- the schema --------------------------------------------------------------------


@dataclass(frozen=True)
class QualityAttribute:
    id: str
    category: str
    name: str
    blocking: bool
    description: str = ""
    # Empty means "all applicable workflow phases", which is the documented default
    # for an omitted applies_to. It is not "no phase": an attribute nobody evaluates
    # would be a silently dead rule.
    applies_to: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()

    def applies_to_phase(self, phase: str) -> bool:
        return not self.applies_to or phase in self.applies_to

    def summary(self) -> str:
        """One agent-visible line. The shape both Worker and Reviewer receive."""
        blocking = "blocking" if self.blocking else "non-blocking"
        line = f"{self.id} [{self.category}] {blocking}: {self.name}"
        if self.description:
            line += f" -- {self.description}"
        if self.verification:
            line += f" (verify: {', '.join(self.verification)})"
        return line


@dataclass(frozen=True)
class QualityProfile:
    version: int
    attributes: tuple[QualityAttribute, ...]
    path: str = DEFAULT_PROFILE_PATH

    def for_phase(self, phase: str) -> tuple[QualityAttribute, ...]:
        return tuple(
            attribute
            for attribute in self.attributes
            if attribute.applies_to_phase(phase)
        )

    def for_phases(self, phases: tuple[str, ...]) -> tuple[QualityAttribute, ...]:
        """Every attribute applicable to ANY of `phases`, in profile order.

        This is what the Final Adversarial Review evaluates against: it re-checks the
        requested workflow as a whole, so an attribute scoped to a single phase is
        still in scope for the final gate as long as that phase was requested.
        """
        selected = [
            attribute
            for attribute in self.attributes
            if any(attribute.applies_to_phase(phase) for phase in phases)
        ]
        return tuple(selected)


def _require_string(value: Any, field: str, attribute_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityProfileError(
            f"quality attribute {attribute_id or '<unknown>'}: {field} must be a "
            "non-empty string"
        )
    return value.strip()


def _require_string_list(value: Any, field: str, attribute_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise QualityProfileError(
            f"quality attribute {attribute_id}: {field} must be a non-empty list"
        )
    items = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise QualityProfileError(
                f"quality attribute {attribute_id}: {field} entries must be non-empty "
                "strings"
            )
        items.append(item.strip())
    return tuple(items)


def _build_attribute(raw: Any, seen: set[str]) -> QualityAttribute:
    if not isinstance(raw, dict):
        raise QualityProfileError("each quality attribute must be a mapping")
    attribute_id = raw.get("id") if isinstance(raw.get("id"), str) else ""
    unknown = sorted(set(raw) - set(ATTRIBUTE_KEYS))
    if unknown:
        raise QualityProfileError(
            f"quality attribute {attribute_id or '<unknown>'}: unknown keys "
            f"{unknown}; supported keys are {list(ATTRIBUTE_KEYS)}"
        )
    missing = [key for key in ATTRIBUTE_REQUIRED_KEYS if key not in raw]
    if missing:
        raise QualityProfileError(
            f"quality attribute {attribute_id or '<unknown>'}: missing required keys "
            f"{missing}"
        )
    attribute_id = _require_string(raw["id"], "id", attribute_id)
    if attribute_id in seen:
        raise QualityProfileError(f"duplicate quality attribute id: {attribute_id}")
    seen.add(attribute_id)

    category = _require_string(raw["category"], "category", attribute_id)
    if category not in QUALITY_CATEGORIES:
        raise QualityProfileError(
            f"quality attribute {attribute_id}: unknown category {category!r}; "
            f"expected one of {list(QUALITY_CATEGORIES)}"
        )
    name = _require_string(raw["name"], "name", attribute_id)

    blocking = raw["blocking"]
    # An explicit boolean, not a truthy value: `blocking: "true"` and `blocking: yes`
    # both read as "this is blocking" to a human and would otherwise be accepted as a
    # string, which is precisely the ambiguity that decides whether a gate fails.
    if not isinstance(blocking, bool):
        raise QualityProfileError(
            f"quality attribute {attribute_id}: blocking must be the boolean true or "
            f"false, got {blocking!r}"
        )

    description = raw.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise QualityProfileError(
            f"quality attribute {attribute_id}: description must be a string"
        )

    applies_to: tuple[str, ...] = ()
    if "applies_to" in raw:
        applies_to = _require_string_list(raw["applies_to"], "applies_to", attribute_id)
        unsupported = [
            phase for phase in applies_to if phase not in APPLICABLE_PHASES
        ]
        if unsupported:
            raise QualityProfileError(
                f"quality attribute {attribute_id}: applies_to names unsupported "
                f"phases {unsupported}; expected values from {list(APPLICABLE_PHASES)}"
            )

    verification: tuple[str, ...] = ()
    if "verification" in raw:
        verification = _require_string_list(
            raw["verification"], "verification", attribute_id
        )

    return QualityAttribute(
        id=attribute_id,
        category=category,
        name=name,
        blocking=blocking,
        description=" ".join(description.split()),
        applies_to=applies_to,
        verification=verification,
    )


def build_profile(document: dict[str, Any], *, path: str = DEFAULT_PROFILE_PATH) -> QualityProfile:
    """Validate a parsed document into a QualityProfile, or raise."""
    unknown = sorted(set(document) - set(PROFILE_KEYS))
    if unknown:
        raise QualityProfileError(
            f"unknown top-level keys {unknown}; supported keys are {list(PROFILE_KEYS)}"
        )
    if "version" not in document:
        raise QualityProfileError("profile is missing the required 'version' key")
    version = document["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise QualityProfileError(f"version must be an integer, got {version!r}")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise QualityProfileError(
            f"unsupported quality profile schema version {version}; this build "
            f"supports {list(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    if "quality_attributes" not in document:
        raise QualityProfileError("profile is missing the 'quality_attributes' key")
    raw_attributes = document["quality_attributes"]
    if raw_attributes is None:
        raw_attributes = []
    if not isinstance(raw_attributes, list):
        raise QualityProfileError("quality_attributes must be a list")

    seen: set[str] = set()
    attributes = tuple(_build_attribute(raw, seen) for raw in raw_attributes)
    return QualityProfile(version=version, attributes=attributes, path=path)


def load_profile_text(text: str, *, path: str = DEFAULT_PROFILE_PATH) -> QualityProfile:
    return build_profile(parse_profile_document(text), path=path)


# ---- resolution: the tri-state the coordinator branches on --------------------------


@dataclass(frozen=True)
class QualityProfileResolution:
    """What a repository actually has, as three distinguishable states.

    `absent` and `invalid` are separate states rather than one "no usable profile"
    answer, because they demand opposite responses: absent is a normal project that
    is reviewed against requirements + phase contract + the general gate, while
    invalid means nobody can produce a trustworthy verdict and no Task may be
    dispatched until it is fixed.
    """

    status: str
    path: str
    profile: QualityProfile | None = None
    error: str = ""

    @property
    def is_loaded(self) -> bool:
        return self.status == PROFILE_STATUS_LOADED

    @property
    def is_absent(self) -> bool:
        return self.status == PROFILE_STATUS_ABSENT

    @property
    def is_invalid(self) -> bool:
        return self.status == PROFILE_STATUS_INVALID

    def attributes_for(self, phases: tuple[str, ...]) -> tuple[QualityAttribute, ...]:
        if self.profile is None:
            return ()
        return self.profile.for_phases(phases)


def resolve_quality_profile(
    root: Path | str = ".", *, relative_path: str = DEFAULT_PROFILE_PATH
) -> QualityProfileResolution:
    """Read the project profile and report which of the three states applies.

    Never raises for a missing or malformed profile: the caller has to be able to see
    the difference, and a raise here would make `absent` and `invalid` indistinguishable
    at every call site that wrapped it in a try/except. The refusal to dispatch on
    `invalid` lives in the context builder that consumes this, which is the point where
    "we cannot judge this project" actually has to stop something.
    """
    path = Path(root) / relative_path
    display = relative_path
    if not path.is_file():
        return QualityProfileResolution(status=PROFILE_STATUS_ABSENT, path=display)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return QualityProfileResolution(
            status=PROFILE_STATUS_INVALID, path=display, error=f"cannot read: {exc}"
        )
    try:
        profile = load_profile_text(text, path=display)
    except QualityProfileError as exc:
        return QualityProfileResolution(
            status=PROFILE_STATUS_INVALID, path=display, error=str(exc)
        )
    return QualityProfileResolution(
        status=PROFILE_STATUS_LOADED, path=display, profile=profile
    )


def blocking_attributes(
    attributes: tuple[QualityAttribute, ...],
) -> tuple[QualityAttribute, ...]:
    return tuple(attribute for attribute in attributes if attribute.blocking)


def workflow_gate_value(verdict: str) -> str:
    """The two-valued gate value a report verdict maps to.

    The mapping, not a synonym table: PASS WITH NOTES and BLOCKED exist so a report
    can say something the gate cannot, and this is where that extra meaning is
    deliberately dropped before it reaches the lifecycle.
    """
    try:
        return VERDICT_GATE_MAPPING[verdict]
    except KeyError:
        raise QualityProfileError(
            f"unknown review verdict {verdict!r}; expected one of {list(REPORT_VERDICTS)}"
        ) from None
