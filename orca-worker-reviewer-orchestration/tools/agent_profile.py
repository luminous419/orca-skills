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

import importlib
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

SUPPORTED_SCHEMA_VERSIONS = (1, 2)
# OS-49. The first version in which a role value may be a {command, model} block
# mapping. `version: 1` keeps its pre-OS-49 meaning FROZEN: a role value is a
# command string and a model is not representable at all. A v1 document therefore
# cannot reach the mapping branch, which is why "unknown schema version fails
# closed" is a statement about THIS feature rather than one OS-49 merely inherits.
MODEL_AWARE_SCHEMA_VERSION = 2

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
# OS-49. The role VALUE's own closed key set, used only when a v2 role value is
# written as a block mapping. `command` is required; `model` is optional. There is
# no document-level, profile-level or phase-level `model` key, no `models:` block
# and no `worker_model` sibling -- a model may be written in exactly the five
# role-value positions and nowhere else.
ROLE_VALUE_KEYS = ("command", "model")
# NOT redefined here. The seven phases are already a repository constant shared by
# quality_profile, task_context and both skills' policy contracts; a second list
# would be a second source of truth for the same vocabulary.
PHASE_KEYS = APPLICABLE_PHASES

PROFILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*", re.ASCII)

# OS-49. A model token's SHAPE. Deliberately a SEPARATE pattern from the agent
# command pattern: a model is never matched by, concatenated into, or substituted
# for a command token. Same shape standalone_profile._SAFE_NAME already uses for a
# plain executable name, which admits every model id observed in this environment
# (`opus`, `claude-opus-5`, `gpt-5.6-sol`) and rejects `<synthetic>`, `../x`,
# `a b`, `a;b` and a leading `-`.
MODEL_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*", re.ASCII)

# OS-49. The model-evidence vocabulary: closed, ordered, six members. `verified` is
# the ONLY admissible evidence of model identity, exactly as
# standalone_preflight.VERDICTS makes `unknown` not `pass`. An unknown or
# unverified model never proves Worker/Reviewer independence.
MODEL_EVIDENCE_NONE = "none"
MODEL_EVIDENCE_REQUESTED = "requested"
MODEL_EVIDENCE_VERIFIED = "verified"
MODEL_EVIDENCE_MISMATCH = "mismatch"
MODEL_EVIDENCE_UNVERIFIABLE = "unverifiable"
MODEL_EVIDENCE_STALE = "stale"
MODEL_EVIDENCE_STATES = (
    MODEL_EVIDENCE_NONE,
    MODEL_EVIDENCE_REQUESTED,
    MODEL_EVIDENCE_VERIFIED,
    MODEL_EVIDENCE_MISMATCH,
    MODEL_EVIDENCE_UNVERIFIABLE,
    MODEL_EVIDENCE_STALE,
)

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
# OS-49's two additions, the exact model analogues of the two command codes above:
# INVALID_AGENT_MODEL is the shape question (validate_profile_command_safety, whole
# definition) and AGENT_MODEL_NOT_SUPPORTED is the availability question
# (validate_effective_identity, required entries only).
REASON_INVALID_MODEL = "INVALID_AGENT_MODEL"
REASON_MODEL_NOT_SUPPORTED = "AGENT_MODEL_NOT_SUPPORTED"
# REUSED, never duplicated. The Worker/Reviewer independence invariant already has
# a name in the shared policy contract's `errors` map and a REASON: line in both
# SKILL.md texts. OS-49 changes only its KEY -- from a command string to an
# effective (command, resolved model) identity -- so there is deliberately no
# WORKER_REVIEWER_MODEL_MUST_DIFFER.
REASON_WORKER_REVIEWER_MUST_DIFFER = "WORKER_REVIEWER_MUST_DIFFER"
# The model-selection CAPABILITY token, mirrored from
# deterministic_workflow.contracts.MODEL_SELECTION_VERIFIED. Named here as a plain
# string rather than imported: this module must not gain an import from the
# deterministic-workflow package, and scripts/test_os49_vocabulary_locks.py asserts
# the two spellings are identical so the duplication cannot drift.
MODEL_SELECTION_VERIFIED_CAPABILITY = "model_selection_verified"

# ---- audit evidence event names ------------------------------------------------------
# Defined here, not in run_logging.py. The log writer's `event`, `role`, `result`
# and `detail` columns are free-form strings, so recording agent routing needs no
# schema change there -- which is what keeps scripts/run_logging.py and its
# byte-identical copy at orca-worker-reviewer-orchestration/tools/run_logging.py
# untouched by OS-4.
EVENT_PROFILE_SELECTED = "agent_profile_selected"
EVENT_ROUTING_RESOLVED = "agent_routing_resolved"
# OS-49's durable model provenance row, emitted per settled dispatch from
# _log_attempt()'s existing funnel. A new EVENT NAME rather than a new column:
# every ORCHESTRATOR_LOG reader skips a row whose cell count differs, so adding a
# column would leave every historical row on disk and make all of them invisible.
EVENT_AGENT_IDENTITY_BOUND = "agent_identity_bound"

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
class RoleValue:
    """One role value AS WRITTEN: a command, and optionally the model it must run on.

    OS-49. A `version: 1` document always produces `model == ""` -- that version's
    meaning is frozen and a model is not representable in it. A `version: 2`
    document produces `model != ""` only where the role value is a block mapping
    that declares one; a plain string role value in a v2 document is exactly a v1
    role value, command only.
    """

    command: str
    model: str = ""


@dataclass(frozen=True)
class AgentProfile:
    """One named profile, as written in one source file."""

    name: str
    source: str
    path: str
    # OS-49 provenance: which schema version the document that produced this
    # profile declared. 1 is the pre-OS-49 default, so a profile built by older
    # code or by a test that omits it reads as v1 -- the frozen, model-less meaning.
    schema_version: int = 1
    defaults: tuple[tuple[str, RoleValue], ...] = ()
    phases: tuple[tuple[str, tuple[tuple[str, RoleValue], ...]], ...] = ()
    final_review: tuple[tuple[str, RoleValue], ...] = ()

    # The three COMMAND accessors keep their pre-OS-49 signatures and return
    # values exactly -- every existing caller is source-compatible and keeps
    # getting a command string. The three *_value siblings return the whole role
    # value, and `None` where the command accessors return "".
    def default_for(self, role: str) -> str:
        value = self.default_value_for(role)
        return value.command if value is not None else ""

    def phase_for(self, phase: str, role: str) -> str:
        value = self.phase_value_for(phase, role)
        return value.command if value is not None else ""

    def final_reviewer(self) -> str:
        value = self.final_reviewer_value()
        return value.command if value is not None else ""

    def default_value_for(self, role: str) -> RoleValue | None:
        for key, value in self.defaults:
            if key == role:
                return value
        return None

    def phase_value_for(self, phase: str, role: str) -> RoleValue | None:
        for phase_name, roles in self.phases:
            if phase_name != phase:
                continue
            for key, value in roles:
                if key == role:
                    return value
        return None

    def final_reviewer_value(self) -> RoleValue | None:
        for key, value in self.final_review:
            if key == ROLE_REVIEWER:
                return value
        return None


@dataclass(frozen=True)
class RoleRouting:
    """One resolved role in one run. Immutable for the life of that run."""

    phase: str
    role: str
    command: str = ""
    # OS-49. Inserted after `command` and before `origin`: every construction site
    # uses keyword arguments, and positional construction of this class exists
    # nowhere in the repository.
    model: str = ""
    origin: str = ORIGIN_UNRESOLVED
    required: bool = False

    @property
    def resolved(self) -> bool:
        # UNCHANGED, deliberately: a model never makes a role "resolved", so
        # validate_required_roles() keeps exactly its pre-OS-49 meaning.
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
    # OS-49. 0 means "legacy / no profile"; otherwise the schema version of the
    # document that produced the selected profile.
    schema_version: int = 0
    requested_phases: tuple[str, ...] = ()
    entries: tuple[RoleRouting, ...] = ()

    @property
    def is_legacy(self) -> bool:
        """True when no profile was selected. Such a routing emits no evidence."""
        return not self.profile_name

    @property
    def is_model_aware(self) -> bool:
        """True when ANY materialized entry declares a model.

        OS-49's single guard for every additive behaviour: the identity provenance
        row, the ledger's model fields and Gate B's fail-closed argument check all
        hang off this one predicate, so a run that declares no model anywhere is
        byte-identical to a pre-OS-49 run.
        """
        return any(entry.model for entry in self.entries)

    def model_for(self, phase: str, role: str) -> str:
        entry = self.for_role(phase, role)
        return entry.model if entry is not None else ""

    def effective_identity(self, phase: str, role: str) -> tuple[str, str]:
        """(command, model) for one role. ("", "") when there is no such entry.

        The ONE lookup both halves of an effective identity come from, so a command
        and a model can never be read out of two different entries.
        """
        entry = self.for_role(phase, role)
        if entry is None:
            return "", ""
        return entry.command, entry.model

    def required_identities(self) -> tuple[tuple[str, str], ...]:
        """Distinct REQUIRED (command, model) pairs, first-seen order, resolved only.

        The model-aware sibling of required_commands(), which is left untouched
        because its unit is the executable -- which is exactly what the PATH check
        needs. This accessor's consumer is validate_effective_identity()'s
        capability precondition.
        """
        seen: list[tuple[str, str]] = []
        for entry in self.required_entries():
            if not entry.resolved:
                continue
            identity = (entry.command, entry.model)
            if identity not in seen:
                seen.append(identity)
        return tuple(seen)

    def pending_admission_phases(self) -> tuple[str, ...]:
        """Phases whose Worker/Reviewer pair is declared but NOT yet admitted.

        A pair lands here when Gate A classified it ADMISSION_PENDING_VERIFICATION:
        one command, two distinct DECLARED models, nothing observed yet. That is an
        obligation, not a permission -- these are exactly the phases on which the
        orchestration runtime refuses to deliver EITHER role until both effective
        identities have been positively verified and compared on RESOLVED values.

        Empty for a legacy run, for a model-less v2 document and for every
        distinct-command pair, which is why no existing routing decision changes.
        """
        pending: list[str] = []
        for phase in self.requested_phases:
            worker_entry = self.for_role(phase, ROLE_WORKER)
            reviewer_entry = self.for_role(phase, ROLE_REVIEWER)
            if worker_entry is None or reviewer_entry is None:
                continue
            if not (worker_entry.required and reviewer_entry.required):
                continue
            if not (worker_entry.resolved and reviewer_entry.resolved):
                continue
            admission, _reason = effective_identity_admission(
                (
                    worker_entry.command,
                    worker_entry.model,
                    declaration_evidence_state(worker_entry.model),
                ),
                (
                    reviewer_entry.command,
                    reviewer_entry.model,
                    declaration_evidence_state(reviewer_entry.model),
                ),
            )
            if admission == ADMISSION_PENDING_VERIFICATION and phase not in pending:
                pending.append(phase)
        return tuple(pending)

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
                # OS-49 adds `schema=` to the free-form detail cell. No column
                # changes, so historical rows keep parsing with identical meanings.
                "detail": (
                    f"profile={self.profile_name} source={self.profile_source} "
                    f"schema={self.schema_version}"
                ),
            }
        ]
        for entry in self.entries:
            command = entry.command or EVIDENCE_NO_COMMAND
            origin = entry.origin or EVIDENCE_NO_COMMAND
            model = entry.model or EVIDENCE_NO_COMMAND
            rows.append(
                {
                    "event": EVENT_ROUTING_RESOLVED,
                    "phase": entry.phase,
                    "role": entry.role,
                    "requested_phases": "",
                    "result": RESULT_REQUIRED if entry.required else RESULT_OPTIONAL,
                    "detail": f"command={command} origin={origin} model={model}",
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


def _read_role_value(value: Any, where: str, *, version: int) -> RoleValue:
    """The SINGLE reader for every role value, in every position, at every version.

    OS-49's whole parsing change is this one function. A string role value means
    exactly what it has always meant -- a command, no model -- at every version. A
    block mapping is the model-aware form and requires `version: 2`.

    Shape only. No PATH, no allowlist, no capability: a model's token SHAPE is
    judged by validate_profile_command_safety() over the whole profile definition,
    and its SUPPORTABILITY by validate_effective_identity() over required entries
    only -- exactly the split the command already uses.
    """
    if isinstance(value, dict):
        if version < MODEL_AWARE_SCHEMA_VERSION:
            raise AgentProfileError(
                f"{where}: a model-aware role value requires schema version "
                f"{MODEL_AWARE_SCHEMA_VERSION}; this document declares version "
                f"{version}"
            )
        _require_known_keys(value, ROLE_VALUE_KEYS, where)
        if "command" not in value:
            raise AgentProfileError(f"{where}: a role mapping must declare command")
        command = _require_command_value(value["command"], f"{where}.command")
        model = ""
        if "model" in value:
            model = _require_command_value(value["model"], f"{where}.model")
        return RoleValue(command=command, model=model)
    # Diagnostic branch, verdict-neutral. The restricted YAML reader supports flow
    # SEQUENCES but not flow MAPPINGS, so `worker: {command: claude, model: x}`
    # written inline arrives here as a STRING. Without this branch the next gate
    # refuses it as a bad PATH command token -- which blames the command for a
    # reader limitation about mappings, and the operator's cheapest reading of that
    # message is "delete the mapping", i.e. a silently model-less run. Still fails
    # closed either way; this only makes the message name the real cause.
    if isinstance(value, str) and value.startswith("{"):
        raise AgentProfileError(
            f"{where}: a role mapping must be written as an indented block, "
            "not inline"
        )
    return RoleValue(command=_require_command_value(value, where))


def _build_role_mapping(
    raw: Any, *, allowed: Iterable[str], where: str, version: int
) -> tuple[tuple[str, RoleValue], ...]:
    mapping = _require_mapping(raw, where)
    _require_known_keys(mapping, allowed, where)
    return tuple(
        (key, _read_role_value(mapping[key], f"{where}.{key}", version=version))
        for key in mapping
    )


def _build_profile(
    name: str, raw: Any, *, path: str, source: str, version: int
) -> AgentProfile:
    where = f"profiles.{name}"
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise AgentProfileError(f"{where}: invalid profile name {name!r}")
    mapping = _require_mapping(raw, where)
    _require_known_keys(mapping, PROFILE_KEYS, where)

    defaults: tuple[tuple[str, RoleValue], ...] = ()
    if "defaults" in mapping:
        defaults = _build_role_mapping(
            mapping["defaults"],
            allowed=ROLE_KEYS,
            where=f"{where}.defaults",
            version=version,
        )

    phases: list[tuple[str, tuple[tuple[str, RoleValue], ...]]] = []
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
                        version=version,
                    ),
                )
            )

    final_review: tuple[tuple[str, RoleValue], ...] = ()
    if "final_review" in mapping:
        final_review = _build_role_mapping(
            mapping["final_review"],
            allowed=FINAL_REVIEW_KEYS,
            where=f"{where}.final_review",
            version=version,
        )

    return AgentProfile(
        name=name,
        source=source,
        path=path,
        schema_version=version,
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
        (
            name,
            _build_profile(
                name, profiles[name], path=path, source=source, version=version
            ),
        )
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


def _resolve_phase_role_value(
    profile: AgentProfile | None, phase: str, role: str, *, explicit: str = ""
) -> tuple[RoleValue | None, str]:
    """The resolution CHAIN, once, over whole role values rather than commands.

    An explicit `worker=`/`reviewer=` is a bare command string on the command line
    and can carry no model, so it resolves to a model-less RoleValue -- which is
    exactly today's behaviour expressed in the new type.
    """
    if explicit:
        return RoleValue(command=explicit), ORIGIN_EXPLICIT
    if profile is not None:
        value = profile.phase_value_for(phase, role)
        if value is not None and value.command:
            return value, ORIGIN_PHASE
        value = profile.default_value_for(role)
        if value is not None and value.command:
            return value, ORIGIN_DEFAULTS
    return None, ORIGIN_UNRESOLVED


def _resolve_final_reviewer_value(
    profile: AgentProfile | None, *, explicit_reviewer: str = ""
) -> tuple[RoleValue | None, str]:
    if profile is not None:
        value = profile.final_reviewer_value()
        if value is not None and value.command:
            return value, ORIGIN_PHASE
    if explicit_reviewer:
        return RoleValue(command=explicit_reviewer), ORIGIN_EXPLICIT
    if profile is not None:
        value = profile.default_value_for(ROLE_REVIEWER)
        if value is not None and value.command:
            return value, ORIGIN_DEFAULTS
    return None, ORIGIN_UNRESOLVED


def resolve_phase_role(
    profile: AgentProfile | None, phase: str, role: str, *, explicit: str = ""
) -> tuple[str, str]:
    """explicit > phases.<phase>.<role> > defaults.<role> > unresolved.

    Signature and return value UNCHANGED by OS-49: still (command, origin).
    """
    value, origin = _resolve_phase_role_value(profile, phase, role, explicit=explicit)
    return (value.command if value is not None else ""), origin


def resolve_final_reviewer(
    profile: AgentProfile | None, *, explicit_reviewer: str = ""
) -> tuple[str, str]:
    """final_review.reviewer > explicit > defaults.reviewer > unresolved.

    The first two are in the OPPOSITE order to resolve_phase_role(). That is the
    requirement, not an oversight: a profile that names a Final Reviewer means it,
    and an explicit `reviewer=` on the command line is about the phase reviewers.

    Signature and return value UNCHANGED by OS-49: still (command, origin).
    """
    value, origin = _resolve_final_reviewer_value(
        profile, explicit_reviewer=explicit_reviewer
    )
    return (value.command if value is not None else ""), origin


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
            value, origin = _resolve_phase_role_value(
                profile, phase, role, explicit=explicit
            )
            entries.append(
                RoleRouting(
                    phase=phase,
                    role=role,
                    command=value.command if value is not None else "",
                    model=value.model if value is not None else "",
                    origin=origin,
                    required=(phase, role) in required,
                )
            )
    if runtime == RUNTIME_ORCHESTRATION:
        value, origin = _resolve_final_reviewer_value(
            profile, explicit_reviewer=explicit_reviewer
        )
        entries.append(
            RoleRouting(
                phase=FINAL_REVIEW_SLOT,
                role=ROLE_FINAL_REVIEWER,
                command=value.command if value is not None else "",
                model=value.model if value is not None else "",
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
        schema_version=(
            profile.schema_version
            if (selection.is_selected and profile is not None)
            else 0
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
    # OS-49: the declared MODELS of the same whole definition, collected in the same
    # walk and judged by the same "is this a safe token" question. A `model: $(x)`
    # in a phase this invocation did not request is still a trust question, for
    # exactly the reason the command's is.
    models: list[tuple[str, str]] = []
    for role, value in profile.defaults:
        if value.command:
            commands.append((f"defaults.{role}", value.command))
        if value.model:
            models.append((f"defaults.{role}.model", value.model))
    for phase_name, roles in profile.phases:
        for role, value in roles:
            if value.command:
                commands.append((f"phases.{phase_name}.{role}", value.command))
            if value.model:
                models.append((f"phases.{phase_name}.{role}.model", value.model))
    for role, value in profile.final_review:
        if value.command:
            commands.append((f"final_review.{role}", value.command))
        if value.model:
            models.append((f"final_review.{role}.model", value.model))
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
    # A SEPARATE pattern, deliberately: a model is never matched by the command
    # pattern, never concatenated into a command and never substituted for one.
    # There is no allowlist for models -- which models exist is an environment
    # fact, and the question "can this run select and positively observe one at
    # all" is validate_effective_identity()'s, over required entries only.
    for location, model in models:
        if not MODEL_TOKEN_PATTERN.fullmatch(model):
            raise AgentProfileError(
                f"{location}: {model!r} is not a simple model token",
                reason=REASON_INVALID_MODEL,
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


# ---- the effective-identity rule (OS-49) -----------------------------------------------
# One executable can now select models, so a command string is no longer an agent's
# identity. The rule below is CATEGORICAL: it applies to every materialized
# Worker/Reviewer pair, on both runtimes, at every schema version including v1 --
# O2 buys PARSING isolation, never policy isolation.
#
# It reads (command, model, model-evidence-state) and NOTHING else. It never reads
# the requested phase set as a policy input, the risk level, the quality profile or
# the decision policy; model identity lives INSIDE the existing `agent_profile`
# axis, so decision_policy.CANONICAL_INDEPENDENT_AXES stays a three-tuple.


ADMISSION_INDEPENDENT = "independent"
ADMISSION_REFUSED = "refused"
ADMISSION_PENDING_VERIFICATION = "pending_model_verification"
EFFECTIVE_IDENTITY_ADMISSION_STATES = (
    ADMISSION_INDEPENDENT,
    ADMISSION_REFUSED,
    ADMISSION_PENDING_VERIFICATION,
)


def effective_identity_admission(
    worker: tuple[str, str, str],
    reviewer: tuple[str, str, str],
) -> tuple[str, str]:
    """The categorical rule, implemented exactly once, with THREE answers.

    Each argument is (command, model, model_evidence_state). Returns
    (admission_state, reason_code); reason_code is "" unless the state is
    ADMISSION_REFUSED.

    Two answers are not enough, and that shortage was the OS-49 iteration-1 defect.
    `requested` is a DECLARATION, never an observation, so it can never be evidence
    that two same-command placements are two agents -- but refusing a declared
    same-command pair outright at declaration time would refuse every model-aware
    pair and the feature could never route. So declaration time has a third answer:

      * ADMISSION_INDEPENDENT -- positively established. Either the two commands
        differ, or BOTH models are positively RESOLVED and differ.
      * ADMISSION_REFUSED -- positively excluded, with a reason code. Cheap, and
        reached before any Run exists whenever the declaration alone settles it.
      * ADMISSION_PENDING_VERIFICATION -- NOT a pass. Two distinct DECLARED models on
        one command: an OBLIGATION that something able to observe resolved models must
        discharge, on RESOLVED values, BEFORE the first delivery of either role. The
        orchestration runtime's pre-delivery barrier is that something; an
        undischarged obligation is a refused dispatch, not a permitted one.

    Pure: no routing, no I/O, no capability set. It reads (command, model,
    model-evidence-state) and NOTHING else -- never the requested phase set as a
    policy input, never the risk level, the quality profile or the decision policy;
    model identity lives INSIDE the existing `agent_profile` axis, so
    decision_policy.CANONICAL_INDEPENDENT_AXES stays a three-tuple.

    Two reason codes, both pre-existing contract values:
      * AGENT_MODEL_NOT_SUPPORTED when a side's evidence state is `unverifiable`,
        i.e. the placement cannot select or observe a model at all;
      * WORKER_REVIEWER_MUST_DIFFER for every other refused same-command pair. That
        is the INDEPENDENCE-LEVEL outcome and it is deliberately one name. WHICH leg
        of the model-selection lifecycle failed is a finer fact that only the
        orchestration runtime's pre-delivery barrier can know, and it refuses first,
        with its own closed snake_case vocabulary, before ever reaching here.
    """
    worker_command, worker_model, worker_state = worker
    reviewer_command, reviewer_model, reviewer_state = reviewer
    for state in (worker_state, reviewer_state):
        if state not in MODEL_EVIDENCE_STATES:
            raise AgentProfileError(
                f"unknown model evidence state {state!r}; supported: "
                f"{', '.join(MODEL_EVIDENCE_STATES)}"
            )

    # Row 1. Two different executables are two different agents, exactly as before
    # OS-49 -- this is the clause that keeps distinct model-pinned wrapper commands
    # (`claude-opus` vs `codex-sol`) routing unchanged, and the clause that keeps the
    # legacy (profile-omitted) pair's decision byte-identical to the pre-OS-49 one.
    if worker_command != reviewer_command:
        return ADMISSION_INDEPENDENT, ""

    # Row 9. A declared model on a placement that cannot select or observe one.
    if MODEL_EVIDENCE_UNVERIFIABLE in (worker_state, reviewer_state):
        return ADMISSION_REFUSED, REASON_MODEL_NOT_SUPPORTED

    # Rows 4 and 8. One side carries no model at all, so there is no second axis to
    # distinguish the two: same command, no distinguishing model, refused.
    if MODEL_EVIDENCE_NONE in (worker_state, reviewer_state):
        return ADMISSION_REFUSED, REASON_WORKER_REVIEWER_MUST_DIFFER

    distinct_models = bool(worker_model) and bool(reviewer_model) and (
        worker_model != reviewer_model
    )
    # Rows 2 and 3. The only positive same-command outcome in the whole rule: two
    # models BOTH positively resolved, and different as resolved values.
    if worker_state == reviewer_state == MODEL_EVIDENCE_VERIFIED:
        return (
            (ADMISSION_INDEPENDENT, "")
            if distinct_models
            else (ADMISSION_REFUSED, REASON_WORKER_REVIEWER_MUST_DIFFER)
        )

    # Rows 3' and 7, i.e. declaration time, where nothing has been observed yet and
    # both sides are necessarily `requested`. Equal declared models are REFUSED here,
    # the cheap half of the invariant, caught before any Run exists. Two distinct
    # declared models are PENDING -- an obligation, never a pass.
    if worker_state == reviewer_state == MODEL_EVIDENCE_REQUESTED:
        return (
            (ADMISSION_PENDING_VERIFICATION, "")
            if distinct_models
            else (ADMISSION_REFUSED, REASON_WORKER_REVIEWER_MUST_DIFFER)
        )

    # Rows 5, 6, 10, 11: at least one side has no positive resolution, and the other
    # side's positive resolution cannot stand in for it.
    return ADMISSION_REFUSED, REASON_WORKER_REVIEWER_MUST_DIFFER


def effective_identity_independent(
    worker: tuple[str, str, str],
    reviewer: tuple[str, str, str],
) -> tuple[bool, str]:
    """POSITIVE independence only. Returns (independent, reason_code).

    A thin reading of `effective_identity_admission()` -- so there is ONE
    implementation of the rule rather than two that can drift -- that collapses the
    three admission states onto the single question a caller asks when it is about to
    rely on independence: is independence POSITIVELY established right now?

    ADMISSION_PENDING_VERIFICATION answers no, with WORKER_REVIEWER_MUST_DIFFER: at
    the independence level `requested`, `mismatch` and `stale` are one thing -- no
    positive resolution, therefore no proof of independence. Unknown or unverified
    model evidence NEVER proves independence through this function. A caller that
    must distinguish "not yet" from "never" asks
    `effective_identity_admission()` instead; only Gate A does.
    """
    admission, reason = effective_identity_admission(worker, reviewer)
    if admission == ADMISSION_INDEPENDENT:
        return True, ""
    return False, reason or REASON_WORKER_REVIEWER_MUST_DIFFER


def declaration_evidence_state(model: str) -> str:
    """The evidence state a DECLARED role value has before anything was observed."""
    return MODEL_EVIDENCE_REQUESTED if model else MODEL_EVIDENCE_NONE


def model_selection_capabilities(driver: Any) -> frozenset[str]:
    """The ONE derivation of a model-selection capability from a driver object.

    OS-49 BUGFIX (review M3/M6). Gate A took a `model_capabilities` set and Gate B asked
    `driver is None`, which are two different questions: an object that merely EXISTS
    answered the second one yes. A driver that cannot be CALLED is not a capability, and
    a declaration gate that admits a model the delivery barrier will then refuse -- or,
    worse, that leaks an `AttributeError` out of the barrier instead of a named refusal --
    is the two gates disagreeing. So both gates now read this function, over the same
    driver object, and there is exactly one rule:

        a model-selection capability exists IFF `select_and_verify` is CALLABLE on it.

    The token means BOTH legs (a selection was REQUESTED for this attempt and the
    resolution was THEN observed), and `select_and_verify` is the single method that can
    honour both -- which is why its callability is the whole test and why
    `deterministic_workflow.fake_adapter.FakeAdapter.capabilities()` reads this same
    function rather than re-spelling the predicate.

    `None` -- the production default on every real door -- yields the EMPTY set, so the
    real runtime stays fail-closed by default rather than by remembering to pass nothing.
    """
    if callable(getattr(driver, "select_and_verify", None)):
        return frozenset({MODEL_SELECTION_VERIFIED_CAPABILITY})
    return frozenset()


#: The ONE normalisation that makes a class's import path identical in the two layouts
#: this tree actually ships: the repository layout
#: (`scripts.deterministic_workflow.fake_adapter`, `scripts.orca_runtime_harness`) and
#: the installed FLAT Skill layout (`deterministic_workflow.fake_adapter`,
#: `orca_runtime_harness`) -- the same two layouts every
#: `try: from scripts import X / except ImportError: import X` pair in this tree spans,
#: and the same pair `validate_skills.py` byte-mirrors. EXACTLY ONE leading "scripts."
#: component is stripped, and `resolve_driver_type` tries BOTH spellings -- so the SAME
#: class in the two layouts binds EQUAL, while a genuine third-party package that really
#: is named `scripts.*` still resolves, by the second attempt.
DRIVER_REPO_PACKAGE_PREFIX = "scripts."
DRIVER_TYPE_UNIDENTIFIABLE = "AGENT_MODEL_DRIVER_UNIDENTIFIABLE"


def driver_type_id(driver: Any) -> str:
    """The driver's STABLE, NON-SECRET, CROSS-PROCESS *type* identifier -- or REFUSE.

    `""` for `driver is None`: a POSITIVE "no driver" statement, not an absence.

        "<module, at most one leading 'scripts.' stripped>:<type(driver).__qualname__>"

    accepted ONLY if it ROUND-TRIPS -- importing that module and walking that qualname in
    THIS interpreter yields `type(driver)` ITSELF. The round trip is what makes the
    string INJECTIVE rather than merely descriptive, and it is what rejects every way two
    different classes could otherwise share one spelling:

      * `type("Driver", (), {...})` built at run time is bound to NO module attribute (or
        to a DIFFERENT object) -> the walk fails or resolves elsewhere -> REFUSED;
      * a `<locals>` qualname (a class defined inside a function) cannot be walked ->
        REFUSED;
      * a class defined in `__main__` resolves HERE, but in a successor whose `__main__`
        is a different entry point it resolves to a different object or not at all ->
        that successor REFUSES rather than matching, which is the fail-closed direction.

    `type(...).__name__` is deliberately NOT used: Python does not make `__name__`
    unique, so `module_a.Driver` and `module_b.Driver` would persist as one string and a
    successor could inject a DIFFERENT capable class under the same launch identity.

    Refusing is a REFUSAL, never a fallback: a driver whose class cannot be named durably
    may not open a model-aware run, because the launch record would otherwise bind a
    string a DIFFERENT class could reproduce. Nothing here is secret -- an import path
    and a class name, i.e. source-tree structure, never a token, endpoint or environment
    value.
    """
    if driver is None:
        return ""
    cls = type(driver)
    module = str(getattr(cls, "__module__", "") or "")
    qualname = str(getattr(cls, "__qualname__", "") or "")
    if not module or not qualname or "<" in qualname:
        raise AgentProfileError(
            f"{DRIVER_TYPE_UNIDENTIFIABLE}: {module}:{qualname} is not a durable class "
            "identity; a model-selection driver must be a module-level class",
            reason=DRIVER_TYPE_UNIDENTIFIABLE,
        )
    normalised = (
        module[len(DRIVER_REPO_PACKAGE_PREFIX):]
        if module.startswith(DRIVER_REPO_PACKAGE_PREFIX)
        else module
    )
    identifier = f"{normalised}:{qualname}"
    if cls not in _resolved_driver_types(identifier):
        raise AgentProfileError(
            f"{DRIVER_TYPE_UNIDENTIFIABLE}: {identifier!r} does not resolve back to this "
            "class in this process; a dynamically created or function-local driver class "
            "has no identity a durable record can bind",
            reason=DRIVER_TYPE_UNIDENTIFIABLE,
        )
    return identifier


def _resolved_driver_types(identifier: str) -> tuple[Any, ...]:
    """Every class `identifier` resolves to, across BOTH layout spellings, in order.

    BOTH spellings are reported rather than only the first, and that is load-bearing for
    the round trip rather than tidiness. In this repository one source file is reachable
    under TWO module names at once -- `deterministic_workflow.fake_adapter` and
    `scripts.deterministic_workflow.fake_adapter` are the same `__file__` imported twice,
    because the test lanes put both the repository root and `scripts/` on `sys.path` -- and
    Python makes those two module objects hold two DISTINCT class objects. Accepting only
    the first spelling would therefore refuse a driver imported under the other one, as
    `AGENT_MODEL_DRIVER_UNIDENTIFIABLE`, even though both incarnations ARE the class the
    identifier names. "The SAME class in the two layouts binds EQUAL" is the property the
    identifier exists to have, so the round trip asks whether `type(driver)` is reachable
    under EITHER spelling, not whether it is the first one tried.

    Nothing about injectivity is given up: two different module-level classes still differ
    in module or qualname and so still produce different identifiers, and a dynamically
    created or function-local class is bound to no module attribute at all and is reachable
    under NEITHER spelling. The residual limitation, stated rather than hidden: a top-level
    package genuinely named the same as a `scripts.`-relative module would share one
    identifier with it -- a consequence of the one-component stripping itself, not of this
    function.
    """
    module_path, _, qualname = str(identifier or "").partition(":")
    if not module_path or not qualname:
        return ()
    found: list[Any] = []
    for candidate in (module_path, DRIVER_REPO_PACKAGE_PREFIX + module_path):
        try:
            obj: Any = importlib.import_module(candidate)
        except Exception:  # noqa: BLE001 - absent in THIS layout; try the other one
            continue
        for part in qualname.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if isinstance(obj, type) and obj not in found:
            found.append(obj)
    return tuple(found)


def resolve_driver_type(identifier: str) -> Any:
    """The inverse of `driver_type_id`, or `None`. IMPORTS a module; NEVER instantiates.

    Answers the FIRST class the identifier resolves to; :func:`_resolved_driver_types`
    answers all of them, which is what the round-trip check needs.
    """
    resolved = _resolved_driver_types(identifier)
    return resolved[0] if resolved else None


def validate_effective_identity(
    routing: RunRouting,
    *,
    model_capabilities: frozenset[str] = frozenset(),
) -> None:
    """GATE A: the declaration-time half of the effective-identity rule.

    Runs before any Run, Task, Dispatch or terminal exists, so a refusal here costs
    nothing. Scoped to required_entries() for the same reason the PATH check is: an
    environment fact must not fail a run over a role nobody dispatches.

    `model_capabilities` defaults to EMPTY, and both real production doors
    (skill_policy._resolve_agent_routing and
    deterministic_workflow.launcher.orca_run_routing) pass nothing. That single
    default is what makes the real-runtime path honestly fail closed: a declared
    model is refused with AGENT_MODEL_NOT_SUPPORTED before anything is created.
    Only a caller that can HONESTLY both request a model selection and positively
    observe the resolution -- in OS-49, the deterministic/fake driver seam -- passes
    the capability.
    """
    if routing.is_legacy:
        return

    # 1. Capability precondition. A model nobody can select and verify is not a
    #    routing instruction, it is an unmet requirement.
    if any(model for _, model in routing.required_identities()):
        if MODEL_SELECTION_VERIFIED_CAPABILITY not in model_capabilities:
            for entry in routing.required_entries():
                if entry.resolved and entry.model:
                    raise AgentProfileError(
                        f"{entry.phase}.{entry.role}: model {entry.model!r} is "
                        "declared but no model-selection capability is available "
                        "for this run",
                        reason=REASON_MODEL_NOT_SUPPORTED,
                    )

    # 2. The independence rule, on declared values, for every materialized pair
    #    where BOTH sides are required and resolved. An unresolved required role is
    #    validate_required_roles()' business, with the reason that says a role is
    #    missing rather than wrong.
    for phase in routing.requested_phases:
        worker_entry = routing.for_role(phase, ROLE_WORKER)
        reviewer_entry = routing.for_role(phase, ROLE_REVIEWER)
        if worker_entry is None or reviewer_entry is None:
            continue
        if not (worker_entry.required and reviewer_entry.required):
            continue
        if not (worker_entry.resolved and reviewer_entry.resolved):
            continue
        admission, reason = effective_identity_admission(
            (
                worker_entry.command,
                worker_entry.model,
                declaration_evidence_state(worker_entry.model),
            ),
            (
                reviewer_entry.command,
                reviewer_entry.model,
                declaration_evidence_state(reviewer_entry.model),
            ),
        )
        if admission == ADMISSION_INDEPENDENT:
            continue
        if admission == ADMISSION_PENDING_VERIFICATION:
            # NOT a pass, and deliberately not phrased as one. Two distinct DECLARED
            # models on one command carry an OBLIGATION that this gate structurally
            # cannot discharge -- it has observed no resolved model, and two distinct
            # declared tokens can alias onto one. `pending_admission_phases()` names
            # the pairs that carry it; the orchestration runtime's pre-delivery
            # barrier refuses any delivery, of EITHER role, until both effective
            # identities are positively verified and distinct. Nothing is routed on
            # the strength of a declaration alone.
            continue
        worker_identity = _identity_text(worker_entry.command, worker_entry.model)
        reviewer_identity = _identity_text(reviewer_entry.command, reviewer_entry.model)
        raise AgentProfileError(
            f"{phase}: worker {worker_identity} and reviewer {reviewer_identity} "
            "are not independent effective agent identities",
            reason=reason,
        )


def _identity_text(command: str, model: str) -> str:
    """One-line rendering of an effective identity, for a refusal message."""
    return f"{command!r}" if not model else f"{command!r}+{model!r}"
