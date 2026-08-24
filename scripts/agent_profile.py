#!/usr/bin/env python3
"""Agent Profile: named Worker/Reviewer routing, resolved once before a Run exists.

OS-4. A profile answers WHO executes each phase. It does not change WHAT runs
(`phases`), HOW STRONGLY it is reviewed (`risk`), or WHAT COUNTS AS PASS (the
project quality profile) -- the one dependency that exists runs in a single
direction: required_roles() READS the settled requested phases and risk to decide
which roles must resolve, and never writes back to either.

Three things in this module are deliberate and easy to undo by accident:

1. Parsing does NOT run the agent-command gate. build_agent_profiles() validates
   YAML shape, the closed key sets, types and the phase vocabulary, and stops
   there. A command's SAFETY (is it even a plausible, allowlisted token) does not
   depend on the requested phases or risk level and is answered at selection time
   instead -- see (2). Its EXECUTABILITY (does this run actually need it, is it
   on PATH) does depend on them, because that is what decides which roles are
   actually required, and a command in a role this run never dispatches must not
   be able to block the run over an environment fact -- see (3).

2. validate_profile_command_safety() asks "is this a safe, allowlisted command
   token" of every command the SELECTED PROFILE DECLARES -- defaults, every
   phase override regardless of whether this invocation requested that phase,
   final_review -- plus any explicit worker=/reviewer= participating in this same
   invocation. It runs once, right after selection, needs no requested-phase or
   risk information at all, and never touches PATH. A profile is a single trust
   document; a `bash` sitting in a phase this invocation did not ask for is not
   made safe by that omission, because the very next invocation of the same
   profile might ask for it.

3. validate_routing_commands() asks "does this command actually exist" of
   routing.required_entries() only, after requested phases and risk have decided
   which roles are required. PATH is an environment fact, not a trust question,
   so an unused-but-syntactically-safe command need not be installed. Splitting
   safety from availability this way means an invalid or disallowed command
   anywhere in the profile's definition is refused before a Run ever exists,
   while an unused-but-valid command that simply is not on this machine's PATH
   does not block anything.

Standard library only, like every other module in scripts/. The restricted-subset
YAML reader is reused from scripts.quality_profile rather than re-implemented:
one parser means two profile formats cannot disagree about what a document says.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

try:  # pragma: no cover - import shim, exercised by both invocation forms
    from scripts.quality_profile import (
        APPLICABLE_PHASES,
        QualityProfileError,
        parse_profile_document,
    )
except ImportError:  # pragma: no cover - same module, flat import path
    from quality_profile import (
        APPLICABLE_PHASES,
        QualityProfileError,
        parse_profile_document,
    )


# ---- locations -------------------------------------------------------------------
# Two sources, and exactly two. The project-local file wins as a WHOLE DEFINITION:
# a profile name found in both is taken from the project file entirely, never
# merged field by field. A field-level merge is what makes "which rule applied?"
# unanswerable, and the audit evidence records which source a profile came from
# precisely so that question keeps a one-line answer.
PROJECT_PROFILE_RELATIVE_PATH = ".orca/agent-profiles.yaml"
USER_PROFILE_RELATIVE_PATH = ".orca/agent-profiles.yaml"

SUPPORTED_SCHEMA_VERSIONS = (1,)

SOURCE_PROJECT_LOCAL = "project_local"
SOURCE_USER_GLOBAL = "user_global"
# Precedence order, highest first. discover_agent_profiles() walks this.
SOURCE_PRECEDENCE = (SOURCE_PROJECT_LOCAL, SOURCE_USER_GLOBAL)

# ---- the schema ------------------------------------------------------------------
# Closed key sets. An unknown key is refused rather than ignored: a typo in
# `reviewer` that silently means "no reviewer configured" is exactly the failure a
# profile exists to prevent.
DOCUMENT_KEYS = ("version", "profiles")
PROFILE_KEYS = ("defaults", "phases", "final_review")
ROLE_KEYS = ("worker", "reviewer")
FINAL_REVIEW_KEYS = ("reviewer",)
# NOT redefined here. The seven phases are already a repository constant shared by
# quality_profile, task_context and both skills' policy contracts; a second list
# would be a second source of truth for the same vocabulary.
PHASE_KEYS = APPLICABLE_PHASES

PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)

# ---- roles and resolution origins --------------------------------------------------
ROLE_WORKER = "worker"
ROLE_REVIEWER = "reviewer"
ROLE_FINAL_REVIEWER = "final_reviewer"
# The slot an entry uses in place of a workflow phase for the Final Adversarial
# Review. It is not a phase (it cannot appear in `phases=` and no quality attribute
# may be authored against it), so it gets a reserved name rather than joining
# PHASE_KEYS.
FINAL_REVIEW_SLOT = "final_review"

ORIGIN_EXPLICIT = "explicit"
ORIGIN_PHASE = "phase"
ORIGIN_DEFAULTS = "defaults"
ORIGIN_UNRESOLVED = ""

# ---- runtimes ----------------------------------------------------------------------
# The two skills consume the same profile file and the same resolution rules; they
# differ only in which routing keys they can use. orca-worker-reviewer-loop has no
# risk axis and no Final Adversarial Review, so every phase Reviewer is required
# there and final_review.reviewer is a known key it ignores.
RUNTIME_ORCHESTRATION = "orchestration"
RUNTIME_LOOP = "loop"
RUNTIMES = (RUNTIME_ORCHESTRATION, RUNTIME_LOOP)

RISK_REVIEWER_REQUIRED = ("medium", "high")

# ---- selection states ---------------------------------------------------------------
SELECTION_OMITTED = "omitted"
SELECTION_SELECTED = "selected"
SELECTION_INVALID = "invalid"

# ---- reason codes --------------------------------------------------------------------
# The three new ones live in the shared policy contract's `errors` map (both
# SKILL.md files, byte-equal). The three reused ones are the repository's existing
# agent-command boundary -- OS-4 reuses that boundary rather than inventing a
# parallel one.
REASON_INVALID_PROFILE = "INVALID_AGENT_PROFILE"
REASON_UNKNOWN_PROFILE = "UNKNOWN_AGENT_PROFILE"
REASON_ROLE_UNRESOLVED = "AGENT_ROLE_UNRESOLVED"
REASON_INVALID_COMMAND = "INVALID_AGENT_COMMAND"
REASON_COMMAND_NOT_ALLOWED = "AGENT_NOT_ALLOWED"
REASON_COMMAND_NOT_FOUND = "AGENT_COMMAND_NOT_FOUND"

# ---- audit evidence event names ------------------------------------------------------
# Defined here, not in run_logging.py. The log writer's `event`, `role`, `result`
# and `detail` columns are free-form strings, so recording agent routing needs no
# schema change there -- which is what keeps scripts/run_logging.py and its
# byte-identical copy at orca-worker-reviewer-orchestration/tools/run_logging.py
# untouched by OS-4.
EVENT_PROFILE_SELECTED = "agent_profile_selected"
EVENT_ROUTING_RESOLVED = "agent_routing_resolved"

RESULT_REQUIRED = "required"
RESULT_OPTIONAL = "optional"
# What an evidence row records for a role the profile supplied nothing for. It is a
# legitimate state (a Worker-only profile is valid at LOW risk), and recording it is
# how a later reader can see that the gap was known rather than missed.
EVIDENCE_NO_COMMAND = "none"


class AgentProfileError(ValueError):
    """Raised when a profile exists but cannot be used as written.

    Carries the reason code the coordinator reports, so the call site does not have
    to re-derive which of the six codes applies from the message text.
    """

    def __init__(self, message: str, *, reason: str = REASON_INVALID_PROFILE) -> None:
        super().__init__(message)
        self.reason = reason


# ---- data structures -----------------------------------------------------------------
# Every field is a tuple, not a dict. `frozen=True` freezes the binding, not the
# object, so a dict field would leave a "run-scoped immutable routing" object whose
# contents any caller could edit -- which is the exact property OS-4 needs to hold
# across corrections, re-reviews and downstream revalidation.


@dataclass(frozen=True)
class AgentProfile:
    """One named profile, as written in one source file."""

    name: str
    source: str
    path: str
    defaults: tuple[tuple[str, str], ...] = ()
    phases: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = ()
    final_review: tuple[tuple[str, str], ...] = ()

    def default_for(self, role: str) -> str:
        for key, command in self.defaults:
            if key == role:
                return command
        return ""

    def phase_for(self, phase: str, role: str) -> str:
        for phase_name, roles in self.phases:
            if phase_name != phase:
                continue
            for key, command in roles:
                if key == role:
                    return command
        return ""

    def final_reviewer(self) -> str:
        for key, command in self.final_review:
            if key == ROLE_REVIEWER:
                return command
        return ""


@dataclass(frozen=True)
class RoleRouting:
    """One resolved role in one run. Immutable for the life of that run."""

    phase: str
    role: str
    command: str = ""
    origin: str = ORIGIN_UNRESOLVED
    required: bool = False

    @property
    def resolved(self) -> bool:
        return bool(self.command)


@dataclass(frozen=True)
class AgentProfileSelection:
    """What `profile=` resolved to: omitted, selected, or invalid.

    Three states rather than two, for the same reason resolve_quality_profile()
    distinguishes absent from invalid: omitted means "run exactly as before" while
    invalid means "no run at all", and a caller that had to tell them apart from an
    exception would lose the distinction at the first try/except.
    """

    status: str
    name: str = ""
    profile: AgentProfile | None = None
    reason: str = ""
    error: str = ""
    searched: tuple[str, ...] = ()

    @property
    def is_omitted(self) -> bool:
        return self.status == SELECTION_OMITTED

    @property
    def is_selected(self) -> bool:
        return self.status == SELECTION_SELECTED

    @property
    def is_invalid(self) -> bool:
        return self.status == SELECTION_INVALID


@dataclass(frozen=True)
class RunRouting:
    """The materialized routing for one run. Built once, before the Run exists.

    `entries` covers every requested phase's Worker and Reviewer plus, for the
    orchestration runtime, the Final Reviewer. Phases outside the request are not
    materialized at all: a profile may declare routing for a phase without that
    meaning the phase runs.

    Two different subsets are read from here, and they are deliberately not the
    same subset:
      required_entries()  -> what the command gate checks, and what must resolve
      entries             -> what the audit evidence records, optional included
    """

    runtime: str
    profile_name: str = ""
    profile_source: str = ""
    requested_phases: tuple[str, ...] = ()
    entries: tuple[RoleRouting, ...] = ()

    @property
    def is_legacy(self) -> bool:
        """True when no profile was selected. Such a routing emits no evidence."""
        return not self.profile_name

    def for_role(self, phase: str, role: str) -> RoleRouting | None:
        for entry in self.entries:
            if entry.phase == phase and entry.role == role:
                return entry
        return None

    def command_for(self, phase: str, role: str) -> str:
        entry = self.for_role(phase, role)
        return entry.command if entry is not None else ""

    def required_entries(self) -> tuple[RoleRouting, ...]:
        return tuple(entry for entry in self.entries if entry.required)

    def unresolved_required(self) -> tuple[RoleRouting, ...]:
        return tuple(
            entry for entry in self.entries if entry.required and not entry.resolved
        )

    def required_commands(self) -> tuple[str, ...]:
        """Distinct required commands, in first-seen order."""
        seen: list[str] = []
        for entry in self.required_entries():
            if entry.resolved and entry.command not in seen:
                seen.append(entry.command)
        return tuple(seen)

    def evidence_rows(self) -> tuple[dict[str, str], ...]:
        """Audit rows for this routing: the selection, then EVERY entry.

        Not required_entries(). An optional role is one this run will not dispatch,
        which is a statement about the lifecycle, not permission to leave it out of
        the record -- the whole point of the evidence is to show what the profile
        resolved to, including the parts that turned out not to be needed.

        A legacy routing produces nothing at all: a run with no profile must leave
        the logs byte-identical to a run from before OS-4 existed.
        """
        if self.is_legacy:
            return ()
        rows: list[dict[str, str]] = [
            {
                "event": EVENT_PROFILE_SELECTED,
                "phase": "",
                "role": "",
                "requested_phases": ",".join(self.requested_phases),
                "result": "",
                "detail": f"profile={self.profile_name} source={self.profile_source}",
            }
        ]
        for entry in self.entries:
            command = entry.command or EVIDENCE_NO_COMMAND
            origin = entry.origin or EVIDENCE_NO_COMMAND
            rows.append(
                {
                    "event": EVENT_ROUTING_RESOLVED,
                    "phase": entry.phase,
                    "role": entry.role,
                    "requested_phases": "",
                    "result": RESULT_REQUIRED if entry.required else RESULT_OPTIONAL,
                    "detail": f"command={command} origin={origin}",
                }
            )
        return tuple(rows)


# ---- parsing and schema validation ---------------------------------------------------
# This whole section answers one question: "is this file an Agent Profile document?"
# It never answers "may this command be executed?" -- see the module docstring.


def parse_agent_profiles_document(source: str) -> dict[str, Any]:
    """Parse the restricted YAML subset, translating the reader's error type."""
    try:
        return parse_profile_document(source)
    except QualityProfileError as exc:
        raise AgentProfileError(str(exc)) from None


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentProfileError(f"{where} must be a mapping")
    if not value:
        raise AgentProfileError(f"{where} must not be empty")
    return value


def _require_known_keys(mapping: dict[str, Any], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise AgentProfileError(
            f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(allowed)}"
        )


def _require_command_value(value: Any, where: str) -> str:
    """A command must be a non-empty string. Its SHAPE is not checked here.

    `bash`, `my agent` and `../claude` all pass this function. They are rejected by
    validate_profile_command_safety() as soon as the profile is selected --
    anywhere in the profile's definition, not only in a role this invocation
    happens to require -- and PATH-missing required commands are separately
    rejected by validate_routing_commands().
    """
    if not isinstance(value, str) or not value:
        raise AgentProfileError(f"{where} must be a non-empty string")
    return value


def _build_role_mapping(
    raw: Any, *, allowed: Iterable[str], where: str
) -> tuple[tuple[str, str], ...]:
    mapping = _require_mapping(raw, where)
    _require_known_keys(mapping, allowed, where)
    return tuple(
        (key, _require_command_value(mapping[key], f"{where}.{key}"))
        for key in mapping
    )


def _build_profile(name: str, raw: Any, *, path: str, source: str) -> AgentProfile:
    where = f"profiles.{name}"
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise AgentProfileError(f"{where}: invalid profile name {name!r}")
    mapping = _require_mapping(raw, where)
    _require_known_keys(mapping, PROFILE_KEYS, where)

    defaults: tuple[tuple[str, str], ...] = ()
    if "defaults" in mapping:
        defaults = _build_role_mapping(
            mapping["defaults"], allowed=ROLE_KEYS, where=f"{where}.defaults"
        )

    phases: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    if "phases" in mapping:
        phase_mapping = _require_mapping(mapping["phases"], f"{where}.phases")
        _require_known_keys(phase_mapping, PHASE_KEYS, f"{where}.phases")
        for phase_name in phase_mapping:
            phases.append(
                (
                    phase_name,
                    _build_role_mapping(
                        phase_mapping[phase_name],
                        allowed=ROLE_KEYS,
                        where=f"{where}.phases.{phase_name}",
                    ),
                )
            )

    final_review: tuple[tuple[str, str], ...] = ()
    if "final_review" in mapping:
        final_review = _build_role_mapping(
            mapping["final_review"],
            allowed=FINAL_REVIEW_KEYS,
            where=f"{where}.final_review",
        )

    return AgentProfile(
        name=name,
        source=source,
        path=path,
        defaults=defaults,
        phases=tuple(phases),
        final_review=final_review,
    )


def build_agent_profiles(
    document: dict[str, Any], *, path: str, source: str
) -> tuple[tuple[str, AgentProfile], ...]:
    """Validate the document's schema and return (name, profile) pairs.

    Schema only: version, the closed key sets, types, the phase vocabulary, and
    that each command value is a non-empty string. This function does not import,
    call or otherwise consult the agent-command gate, and it never touches PATH --
    a fact `test_building_never_calls_which` pins, because reintroducing an eager
    check here is the specific regression OS-4's review caught twice.
    """
    if not isinstance(document, dict):
        raise AgentProfileError("profile document root must be a mapping")
    _require_known_keys(document, DOCUMENT_KEYS, "document")

    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise AgentProfileError("version must be an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise AgentProfileError(
            f"unsupported schema version {version}; supported: "
            f"{', '.join(str(item) for item in SUPPORTED_SCHEMA_VERSIONS)}"
        )

    profiles = _require_mapping(document.get("profiles"), "profiles")
    return tuple(
        (name, _build_profile(name, profiles[name], path=path, source=source))
        for name in profiles
    )


def load_agent_profiles_text(
    text: str, *, path: str, source: str
) -> tuple[tuple[str, AgentProfile], ...]:
    return build_agent_profiles(
        parse_agent_profiles_document(text), path=path, source=source
    )


def _read_source(path: Path, display: str, source: str) -> tuple[tuple[str, AgentProfile], ...]:
    """Read one source file. A missing file is normal and yields nothing."""
    if not path.exists() and not path.is_symlink():
        return ()
    if not path.is_file():
        raise AgentProfileError(
            f"{display} exists but is not a regular file (a directory, symlink loop "
            "or device node cannot be an agent profile document)"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentProfileError(f"{display} cannot be read: {exc}") from None
    return load_agent_profiles_text(text, path=display, source=source)


def discover_agent_profiles(
    *, project_root: Path | str = ".", home: Path | str | None = None
) -> tuple[dict[str, AgentProfile], tuple[str, ...]]:
    """Load and merge BOTH sources eagerly, for enumeration.

    Not used by select_agent_profile() (see there): resolving one requested name
    must stop at the first source that has it, so that a lower-precedence source's
    condition can never affect a selection the higher-precedence source already
    answered. This function answers a different question -- "what profiles exist
    across both sources" -- for which reading both is the correct behaviour, and a
    malformed lower-precedence file is correctly an error here even if the name a
    caller eventually wants lives entirely in the higher-precedence one.

    `home` is a parameter, not a lookup, because a developer's real
    ~/.orca/agent-profiles.yaml must never reach a test run. Production passes
    nothing and gets Path.home(); every test passes a temporary directory.

    Returns (name -> profile, paths consulted). The paths are reported so a caller
    can say WHERE it looked when a name is not found.
    """
    home_path = Path.home() if home is None else Path(home)
    candidates = (
        (SOURCE_PROJECT_LOCAL, Path(project_root) / PROJECT_PROFILE_RELATIVE_PATH,
         PROJECT_PROFILE_RELATIVE_PATH),
        (SOURCE_USER_GLOBAL, home_path / USER_PROFILE_RELATIVE_PATH,
         f"~/{USER_PROFILE_RELATIVE_PATH}"),
    )
    resolved: dict[str, AgentProfile] = {}
    searched: list[str] = []
    for source, path, display in candidates:
        searched.append(display)
        for name, profile in _read_source(path, display, source):
            # First source wins, and it wins WHOLE. The loop order is
            # SOURCE_PRECEDENCE, so a name already present came from the
            # project-local file and the user-global definition is discarded
            # entirely -- not consulted for fields the winner happens to omit.
            if name not in resolved:
                resolved[name] = profile
    return resolved, tuple(searched)


def select_agent_profile(
    name: str | None, *, project_root: Path | str = ".", home: Path | str | None = None
) -> AgentProfileSelection:
    """Resolve `profile=` to one of three states. Never raises.

    `name is None` means the parameter was omitted -- the legacy path, which does
    not read either source file. An empty string means the user wrote `profile=`
    with no value, which is an explicit invalid value rather than an omission.

    Unlike discover_agent_profiles() (which parses BOTH sources unconditionally,
    for enumeration), this walks SOURCE_PRECEDENCE one source at a time and stops
    at the first one that actually contains `name`. That is the whole fix: the
    selected profile is a self-contained resolution domain, so a lower-precedence
    source's condition -- malformed, unreadable, a directory, anything -- must
    never be able to fail a selection the higher-precedence source already
    answered. Only when project-local parses cleanly and does not contain `name`
    is user-global even opened.
    """
    if name is None:
        return AgentProfileSelection(status=SELECTION_OMITTED)
    if not name:
        return AgentProfileSelection(
            status=SELECTION_INVALID,
            name="",
            reason=REASON_UNKNOWN_PROFILE,
            error="profile= was given with no value",
        )
    home_path = Path.home() if home is None else Path(home)
    candidates = (
        (SOURCE_PROJECT_LOCAL, Path(project_root) / PROJECT_PROFILE_RELATIVE_PATH,
         PROJECT_PROFILE_RELATIVE_PATH),
        (SOURCE_USER_GLOBAL, home_path / USER_PROFILE_RELATIVE_PATH,
         f"~/{USER_PROFILE_RELATIVE_PATH}"),
    )
    searched: list[str] = []
    for source, path, display in candidates:
        searched.append(display)
        try:
            profiles = dict(_read_source(path, display, source))
        except AgentProfileError as exc:
            return AgentProfileSelection(
                status=SELECTION_INVALID,
                name=name,
                reason=exc.reason,
                error=str(exc),
                searched=tuple(searched),
            )
        profile = profiles.get(name)
        if profile is not None:
            return AgentProfileSelection(
                status=SELECTION_SELECTED,
                name=name,
                profile=profile,
                searched=tuple(searched),
            )
        # This source parsed cleanly and simply does not have `name`. Fall through
        # to the next (lower-precedence) source rather than treating that as
        # unknown yet -- unknown is only true once every source has been tried.
    return AgentProfileSelection(
        status=SELECTION_INVALID,
        name=name,
        reason=REASON_UNKNOWN_PROFILE,
        error=f"no profile named {name!r} in {', '.join(searched)}",
        searched=tuple(searched),
    )


# ---- resolution ------------------------------------------------------------------------
# Two resolvers, and they stay two. The chains disagree about which source wins
# first, and a single function with a flag would put that difference in the hands of
# every call site.


def resolve_phase_role(
    profile: AgentProfile | None, phase: str, role: str, *, explicit: str = ""
) -> tuple[str, str]:
    """explicit > phases.<phase>.<role> > defaults.<role> > unresolved."""
    if explicit:
        return explicit, ORIGIN_EXPLICIT
    if profile is not None:
        command = profile.phase_for(phase, role)
        if command:
            return command, ORIGIN_PHASE
        command = profile.default_for(role)
        if command:
            return command, ORIGIN_DEFAULTS
    return "", ORIGIN_UNRESOLVED


def resolve_final_reviewer(
    profile: AgentProfile | None, *, explicit_reviewer: str = ""
) -> tuple[str, str]:
    """final_review.reviewer > explicit > defaults.reviewer > unresolved.

    The first two are in the OPPOSITE order to resolve_phase_role(). That is the
    requirement, not an oversight: a profile that names a Final Reviewer means it,
    and an explicit `reviewer=` on the command line is about the phase reviewers.
    """
    if profile is not None:
        command = profile.final_reviewer()
        if command:
            return command, ORIGIN_PHASE
    if explicit_reviewer:
        return explicit_reviewer, ORIGIN_EXPLICIT
    if profile is not None:
        command = profile.default_for(ROLE_REVIEWER)
        if command:
            return command, ORIGIN_DEFAULTS
    return "", ORIGIN_UNRESOLVED


# ---- required roles --------------------------------------------------------------------


def required_roles(
    *, runtime: str, requested_phases: tuple[str, ...], risk: str | None
) -> tuple[tuple[str, str], ...]:
    """Which (phase, role) pairs must resolve for this run to be dispatchable.

    Reads the settled requested phases and risk; changes neither. At LOW risk the
    orchestration runtime creates no Reviewer node at all, so the phase Reviewer is
    optional there -- which is what makes a Worker-plus-Final-Reviewer profile a
    legitimate LOW-risk configuration. The loop runtime has no risk axis and every
    phase ends in "Reviewer PASS", so its phase Reviewers are always required and
    it has no Final Reviewer to require.
    """
    if runtime not in RUNTIMES:
        raise AgentProfileError(f"unknown runtime {runtime!r}")
    pairs: list[tuple[str, str]] = []
    reviewer_required = (
        runtime == RUNTIME_LOOP or (risk or "").casefold() in RISK_REVIEWER_REQUIRED
    )
    for phase in requested_phases:
        pairs.append((phase, ROLE_WORKER))
        if reviewer_required:
            pairs.append((phase, ROLE_REVIEWER))
    if runtime == RUNTIME_ORCHESTRATION:
        pairs.append((FINAL_REVIEW_SLOT, ROLE_FINAL_REVIEWER))
    return tuple(pairs)


# ---- materialization -------------------------------------------------------------------


def materialize_run_routing(
    *,
    runtime: str,
    selection: AgentProfileSelection,
    requested_phases: tuple[str, ...],
    risk: str | None = None,
    explicit_worker: str = "",
    explicit_reviewer: str = "",
) -> RunRouting:
    """Resolve every role this run can use, once, before the Run is created.

    Only requested phases are materialized. A profile may carry routing for phases
    this invocation did not ask for; that routing is not part of this run and is
    neither validated nor recorded.
    """
    if runtime not in RUNTIMES:
        raise AgentProfileError(f"unknown runtime {runtime!r}")
    profile = selection.profile if selection.is_selected else None
    required = set(required_roles(
        runtime=runtime, requested_phases=requested_phases, risk=risk
    ))

    entries: list[RoleRouting] = []
    for phase in requested_phases:
        for role, explicit in (
            (ROLE_WORKER, explicit_worker),
            (ROLE_REVIEWER, explicit_reviewer),
        ):
            command, origin = resolve_phase_role(profile, phase, role, explicit=explicit)
            entries.append(
                RoleRouting(
                    phase=phase,
                    role=role,
                    command=command,
                    origin=origin,
                    required=(phase, role) in required,
                )
            )
    if runtime == RUNTIME_ORCHESTRATION:
        command, origin = resolve_final_reviewer(
            profile, explicit_reviewer=explicit_reviewer
        )
        entries.append(
            RoleRouting(
                phase=FINAL_REVIEW_SLOT,
                role=ROLE_FINAL_REVIEWER,
                command=command,
                origin=origin,
                required=(FINAL_REVIEW_SLOT, ROLE_FINAL_REVIEWER) in required,
            )
        )

    return RunRouting(
        runtime=runtime,
        profile_name=selection.name if selection.is_selected else "",
        profile_source=(
            profile.source if (selection.is_selected and profile is not None) else ""
        ),
        requested_phases=tuple(requested_phases),
        entries=tuple(entries),
    )


# ---- the static safety gate --------------------------------------------------------------


def validate_profile_command_safety(
    profile: AgentProfile,
    *,
    explicit_worker: str = "",
    explicit_reviewer: str = "",
    token_pattern: re.Pattern[str],
    known_commands: Iterable[str],
    custom_command_pattern: re.Pattern[str],
) -> None:
    """Apply the STATIC half of the agent-command trust boundary -- token shape and
    allowlist membership, never PATH -- to every command the SELECTED PROFILE
    DECLARES, whether or not this invocation's requested phases will ever
    materialize or dispatch it, plus any explicit worker=/reviewer= value
    participating in this same selected-profile invocation.

    This deliberately does NOT operate on RunRouting.entries.
    materialize_run_routing() only builds entries for requested phases (plus, for
    orchestration, Final Review) -- an intentional, unrelated decision about WHAT
    RUNS that this function does not touch or widen: it validates the profile
    DEFINITION itself, once, at selection time, before any phase is even known.
    A profile is a single trust document an operator wrote; `bash` sitting in
    `phases.refactoring.worker` is not made safe by this invocation asking only
    for `analysis` -- the very next invocation of the same profile might ask for
    `refactoring`, and evidence_rows() would then record it verbatim. Checking the
    whole definition once here closes that gap without ever creating a Task,
    Dispatch, or routing entry for a phase nobody requested.

    PATH existence is deliberately excluded, for a different run each time: it is
    an environment fact, not a trust question, and applies only to
    routing.required_entries() in validate_routing_commands().
    """
    allowed = set(known_commands)
    commands: list[tuple[str, str]] = []
    for role, command in profile.defaults:
        if command:
            commands.append((f"defaults.{role}", command))
    for phase_name, roles in profile.phases:
        for role, command in roles:
            if command:
                commands.append((f"phases.{phase_name}.{role}", command))
    for role, command in profile.final_review:
        if command:
            commands.append((f"final_review.{role}", command))
    if explicit_worker:
        commands.append(("explicit.worker", explicit_worker))
    if explicit_reviewer:
        commands.append(("explicit.reviewer", explicit_reviewer))

    for location, command in commands:
        if not token_pattern.fullmatch(command):
            raise AgentProfileError(
                f"{location}: {command!r} is not a simple PATH command token",
                reason=REASON_INVALID_COMMAND,
            )
    for location, command in commands:
        if command not in allowed and not custom_command_pattern.fullmatch(command):
            raise AgentProfileError(
                f"{location}: {command!r} is outside the agent trust boundary",
                reason=REASON_COMMAND_NOT_ALLOWED,
            )


# ---- the availability gate -----------------------------------------------------------------


def validate_routing_commands(
    routing: RunRouting,
    *,
    token_pattern: re.Pattern[str],
    known_commands: Iterable[str],
    custom_command_pattern: re.Pattern[str],
    which: Callable[[str], str | None] = shutil.which,
) -> None:
    """Apply the full agent-command boundary -- token, allowlist, AND PATH -- to
    required routing, and only required routing.

    Call this AFTER validate_profile_command_safety() has already cleared the
    whole profile definition's token and allowlist safety; the two token/allowlist
    passes here re-check required entries specifically so the reported error and
    its ordering match exactly what the legacy (no-profile) path would have
    reported for the same command, then add the one check safety-only cannot
    make: PATH existence, which is the deliberately narrower gate.

    The PATH target set is required_entries(). An optional or non-consumed entry
    is never dispatched, so its command cannot reach execution and must not be
    able to block the run over an environment fact -- an unused
    `phases.refactoring.worker` on a run that asked only for `analysis`, a
    LOW-risk phase Reviewer, a loop run's final_review.reviewer. Static safety for
    every entry's command -- required or not, requested phase or not -- already
    happened in validate_profile_command_safety(); this function narrows only the
    availability check, never the trust boundary.

    Unresolved required entries are not this function's business; validate_required_roles()
    reports those, with the reason code that says a role is missing rather than wrong.
    """
    targets = tuple(
        entry for entry in routing.required_entries() if entry.resolved
    )
    allowed = set(known_commands)

    for entry in targets:
        if not token_pattern.fullmatch(entry.command):
            raise AgentProfileError(
                f"{entry.phase}.{entry.role}: {entry.command!r} is not a simple "
                "PATH command token",
                reason=REASON_INVALID_COMMAND,
            )
    for entry in targets:
        if entry.command not in allowed and not custom_command_pattern.fullmatch(
            entry.command
        ):
            raise AgentProfileError(
                f"{entry.phase}.{entry.role}: {entry.command!r} is outside the "
                "agent trust boundary",
                reason=REASON_COMMAND_NOT_ALLOWED,
            )
    for entry in targets:
        if which(entry.command) is None:
            raise AgentProfileError(
                f"{entry.phase}.{entry.role}: {entry.command!r} was not found on PATH",
                reason=REASON_COMMAND_NOT_FOUND,
            )


def validate_required_roles(routing: RunRouting) -> None:
    """Every required role must have resolved to some command."""
    missing = routing.unresolved_required()
    if missing:
        names = ", ".join(f"{entry.phase}.{entry.role}" for entry in missing)
        raise AgentProfileError(
            f"required role(s) unresolved: {names}", reason=REASON_ROLE_UNRESOLVED
        )
