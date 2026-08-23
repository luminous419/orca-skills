#!/usr/bin/env python3
"""Task boundary and Reviewer context builders shared by both harnesses.

Standard library only, and deliberately importing neither harness: e2e_harness and
orca_runtime_harness do not import each other today, and this module must not become
the edge that couples them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # pragma: no cover - import shim, exercised by both invocation forms
    from scripts.quality_profile import (
        DECISION_PRIORITY,
        INVALID_PROFILE_REASON,
        MINIMAL_GENERAL_GATE,
        NON_BLOCKING_BY_DEFAULT,
        QualityProfileResolution,
        blocking_attributes,
    )
except ImportError:  # pragma: no cover - same module, flat import path
    from quality_profile import (
        DECISION_PRIORITY,
        INVALID_PROFILE_REASON,
        MINIMAL_GENERAL_GATE,
        NON_BLOCKING_BY_DEFAULT,
        QualityProfileResolution,
        blocking_attributes,
    )

# ---- layer 1: what the coordinator writes into the Task spec --------------------
# Five keys, and neither id among them. A Task spec body is write-once and is
# assembled BEFORE the Task exists (task-create answers with the id; task-update has
# no --spec), and the dispatch id does not exist until the worker start response.
# Both ids are therefore structurally impossible here, not merely undesirable.
TASK_BOUNDARY_KEYS = (
    "current_role",
    "current_phase",
    "current_iteration",
    "artifact_contract",
    "relevant_previous_findings",
)

# ---- layer 2: what Orca injects at dispatch time --------------------------------
# The coordinator never writes these. It verifies that every attempt carries NEW
# values and that the worker's own reports carry the same new values.
DISPATCH_INJECTED_IDENTITY = (
    "task_id",
    "dispatch_id",
    "dispatch_capability",
    "coordinator_handle",
)

TASK_BOUNDARY_ROLES = frozenset({"worker", "reviewer", "final_reviewer"})

# ---- the phase axis, which is NOT the agent-mode axis -----------------------------
# current_phase names a WORKFLOW STAGE. A fake agent's behaviour script ("complete",
# "pass", "fail", "exit") names something else entirely, and PR #12 MAJOR-1 was
# exactly that confusion reaching the dispatched payload: a boundary whose keys were
# right and whose current_phase said "pass".
#
# The five canonical phases run in order; bugfix / refactoring are the specialized
# phases SKILL.md allows a run to consist of (and that downstream revalidation never
# lowers into); final_review is the adversarial gate that runs over a whole run.
CANONICAL_PHASES = ("analysis", "plan", "design", "implementation", "test")
SPECIALIZED_PHASES = ("bugfix", "refactoring")
FINAL_REVIEW_PHASE = "final_review"
WORKFLOW_PHASES = (*CANONICAL_PHASES, *SPECIALIZED_PHASES, FINAL_REVIEW_PHASE)
# Every phase a Quality Attribute's applies_to may name. final_review is excluded on
# purpose: it is the gate over a requested workflow, not a phase an attribute is
# authored against, and it evaluates whatever applies to the phases that ran.
ALL_APPLICABLE_PHASES = (*CANONICAL_PHASES, *SPECIALIZED_PHASES)

# Named so the failure message can say WHICH axis the caller reached for. Membership
# in WORKFLOW_PHASES already refuses every one of these; this set only buys the
# caller an error that names the mistake instead of one that lists seven phases.
AGENT_MODES = frozenset(
    {"complete", "pass", "fail", "blocked", "exit", "correction", "ask", "done"}
)


REVIEWER_CONTEXT_KEYS = (
    "original_objective",
    "current_phase",
    "approved_baseline",
    "current_delta",
    "new_claims",
    "previous_findings",
    "validation",
    "drill_down",
)

# Carried in the payload itself, not only in SKILL.md prose, so a reviewer that reads
# nothing but the context still reads the rule that keeps delta from becoming a fence.
REVIEWER_DRILL_DOWN_MANDATE = (
    "The delta is the starting point, not the boundary: verify anything in the "
    "repository this review needs."
)

# R-3. A reused Reviewer session remembers its own earlier PASS. That memory is a
# search shortcut, never evidence.
REVIEWER_PRIOR_PASS_IS_NOT_EVIDENCE = (
    "A previous PASS you remember is not evidence; re-verify this delta as if you "
    "were seeing it for the first time."
)


# ---- layer 3: the quality model the gate is actually decided against ---------------
# The same eight keys reach the Worker and the Reviewer. That is the point: a Worker
# that does not know which quality attributes block its phase produces correction
# rounds for rules it was never told, and a Reviewer told something different from the
# Worker is judging against a spec that was never dispatched. One block, one meaning.
QUALITY_GATE_KEYS = (
    "profile_status",
    "profile_path",
    "applicable_quality_attributes",
    "blocking_quality_attributes",
    "general_gate",
    "decision_priority",
    "non_blocking_by_default",
    "verdict_semantics",
)

# Carried in the payload rather than only in SKILL.md, for the same reason the
# drill-down mandate is: an agent that reads nothing but its dispatched spec still
# has to read the rule that stops a generic preference from becoming a FAIL.
QUALITY_GATE_SUPPRESSION_MANDATE = (
    "A concern outside the four decision-priority tiers is never promoted to a "
    "blocking finding; report it as a non-blocking note or not at all."
)

VERDICT_SEMANTICS = (
    "PASS = no blocking violation and no substantive non-blocking note",
    "PASS WITH NOTES = no blocking violation with one or more non-blocking findings; "
    "the workflow gate value stays PASS and this is a report annotation only",
    "FAIL = at least one blocking violation",
    "BLOCKED = required project information or evidence is missing, so no trustworthy "
    "verdict is possible; reported as BLOCKED with the workflow gate value FAIL",
)


class TaskContextError(ValueError):
    """Raised when a context builder is asked to produce an incomplete payload."""


def require_workflow_phase(current_phase: Any, *, field: str = "current_phase") -> str:
    """The one gate every phase value passes through. Fail-closed, no fallback.

    There is deliberately no "if we cannot tell, use X" branch: a missing phase and an
    unknown phase both raise, because the only fallback anyone ever reaches for here
    is the agent mode that happens to be in scope, which is the defect itself.
    """
    if not current_phase:
        raise TaskContextError(
            f"{field} is required and must be one of {list(WORKFLOW_PHASES)}; "
            "there is no fallback -- an agent mode is not a phase"
        )
    if current_phase in AGENT_MODES:
        raise TaskContextError(
            f"{field}={current_phase!r} is an agent mode, not a workflow phase; "
            f"pass one of {list(WORKFLOW_PHASES)}"
        )
    if current_phase not in WORKFLOW_PHASES:
        raise TaskContextError(
            f"unknown {field}: {current_phase!r}; expected one of "
            f"{list(WORKFLOW_PHASES)}"
        )
    return str(current_phase)


def run_artifact_root(run_id: str) -> str:
    """The one string every artifact path in a run is prefixed with.

    Fail-closed like require_workflow_phase: there is no fallback for a missing run
    id, because an artifact written outside a run directory -- back at the artifacts/
    root every prior run also wrote to -- is exactly the cross-run leakage this
    isolates. `run_id` is the current Orca Run's id (section 6, "Run을 생성하거나 현재
    Run에 bind한다"); it is never invented here.

    This function is the isolation boundary itself, not just a formatter: `run_id` is
    interpolated straight into a filesystem path, so a value containing a path
    separator or a `..`/`.` segment could climb out of artifacts/runs/ entirely
    instead of merely colliding with another run inside it. Real Orca run ids never
    contain either, so this refuses rather than sanitizes.
    """
    if not run_id:
        raise TaskContextError(
            "run_id is required and must be the current Orca Run id; there is no "
            "fallback -- an artifact path without one would fall back to the shared "
            "artifacts/ root every other run also writes to"
        )
    if "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        raise TaskContextError(
            f"run_id must be a single path segment, got {run_id!r}; a value "
            "containing a path separator or '.'/'..' could escape artifacts/runs/"
        )
    return f"artifacts/runs/{run_id}/"


def ensure_run_artifact_root(run_id: str, *, base: Path | None = None) -> Path:
    """Create artifacts/runs/<run_id>/ (under `base` if given) and return it.

    run_artifact_root() only names the directory; something has to make it exist
    before the first Task referencing it is dispatched, or the first Worker told to
    write artifacts/runs/<run_id>/ANALYSIS.md is the one that discovers it is
    missing. This is that something -- called once, right after the Coordinator
    creates/binds the Run and obtains run_id (section 6 step 1), never per-attempt
    and never left for an agent to guess at.

    `base` lets a harness provision the directory inside its own scratch space
    (OrcaRuntimeHarness.artifact_dir, E2EHarness.workspace) instead of the real
    repository's artifacts/ root, which is what keeps running the test suite from
    littering the working tree with run directories.
    """
    relative = run_artifact_root(run_id)
    root = Path(base) / relative if base is not None else Path(relative)
    root.mkdir(parents=True, exist_ok=True)
    return root


def phase_artifact_contract(*, role: str, phase: str, run_id: str = "") -> str:
    """The artifact this (role, phase) pair is contracted to produce.

    The same spelling both harnesses already use, in one place instead of two:
    e2e_harness builds "artifacts/runs/<run-id>/{PHASE}.md" for a worker and
    "artifacts/runs/<run-id>/REVIEW_{PHASE}.md" for its reviewer. A path built from
    the phase is what makes a Reviewer's current_delta a reference to the WORKER's
    deliverable rather than to the reviewer's own future output. The run-id prefix is
    what keeps two runs' artifacts -- and their approved_baseline/current_delta
    references -- from ever pointing at each other.

    `run_id` defaults to "" (never used as-is) rather than being required outright, so
    that a caller's role/phase mistake is reported before its run_id mistake: role and
    phase are checked first, exactly as before this parameter existed.
    """
    if role not in TASK_BOUNDARY_ROLES:
        raise TaskContextError(f"unknown role: {role!r}")
    phase = require_workflow_phase(phase, field="phase")
    base = run_artifact_root(run_id)
    if phase == FINAL_REVIEW_PHASE:
        # One artifact for the gate itself, whichever side is asking about it.
        return f"{base}FINAL_REVIEW.md"
    if role == "worker":
        return f"{base}{phase.upper()}.md"
    return f"{base}REVIEW_{phase.upper()}.md"


def build_task_boundary(
    *,
    current_role: str,
    current_phase: str,
    current_iteration: int,
    artifact_contract: str,
    relevant_previous_findings: tuple[str, ...] = (),
) -> dict[str, str]:
    """Layer 1 of the Task boundary, as a flat {key: str} payload.

    There is deliberately no task_id or dispatch_id parameter and no such key in the
    result: see TASK_BOUNDARY_KEYS. Values are all strings so a caller can freeze the
    payload as tuple(sorted(result.items())) and compare two attempts for equality.
    """
    if current_role not in TASK_BOUNDARY_ROLES:
        raise TaskContextError(f"unknown current_role: {current_role!r}")
    current_phase = require_workflow_phase(current_phase)
    if current_iteration < 1:
        raise TaskContextError(
            f"current_iteration must be >= 1, got {current_iteration!r}"
        )
    if not artifact_contract:
        raise TaskContextError("artifact_contract is required")
    return {
        "current_role": current_role,
        "current_phase": current_phase,
        "current_iteration": str(current_iteration),
        "artifact_contract": artifact_contract,
        "relevant_previous_findings": "\n".join(relevant_previous_findings),
    }


def build_reviewer_context(
    *,
    original_objective: str,
    current_phase: str,
    approved_baseline: tuple[str, ...] = (),
    current_delta: tuple[str, ...] = (),
    new_claims: tuple[str, ...] = (),
    previous_findings: tuple[tuple[str, str], ...] = (),
    validation: tuple[str, ...] = (),
    drill_down: tuple[str, ...],
) -> dict[str, Any]:
    """The eight-key delta-first Reviewer context.

    drill_down is keyword-only AND has no default on purpose: omitting it is a
    TypeError at the call site, and passing an empty one is a TaskContextError. That
    is R-4's code-level defence -- delta-first must never be able to ship without the
    escape hatch that keeps direct verification unrestricted.
    """
    if not drill_down:
        raise TaskContextError(
            "drill_down is mandatory and must be non-empty: "
            + REVIEWER_DRILL_DOWN_MANDATE
        )
    if not original_objective:
        raise TaskContextError("original_objective is required")
    current_phase = require_workflow_phase(current_phase)
    mandate: tuple[str, ...] = (REVIEWER_DRILL_DOWN_MANDATE,)
    if previous_findings:
        # Correction re-review: the finding -> resolution map comes first, and the
        # reviewer is told outright that its remembered PASS proves nothing.
        mandate = (REVIEWER_PRIOR_PASS_IS_NOT_EVIDENCE, *mandate)
    return {
        "original_objective": original_objective,
        "current_phase": current_phase,
        "approved_baseline": tuple(approved_baseline),
        "current_delta": tuple(current_delta),
        "new_claims": tuple(new_claims),
        "previous_findings": tuple(
            (finding_id, resolution) for finding_id, resolution in previous_findings
        ),
        "validation": tuple(validation),
        "drill_down": (*mandate, *drill_down),
    }


def build_quality_gate_context(
    *,
    resolution: QualityProfileResolution,
    current_phase: str,
    requested_phases: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The profile-first quality model for ONE dispatch, as a flat payload.

    Phase applicability is resolved here rather than left to the agent: an attribute
    scoped to `design` must not reach an ANALYSIS Reviewer as something to evaluate,
    and the only way to guarantee that is to filter before the spec is rendered.
    `final_review` is the one phase whose scope is not itself -- the Final Adversarial
    Review re-checks the requested workflow as a whole, so it receives every attribute
    applicable to any requested phase.

    An invalid profile raises. That is this function's whole contribution to section
    11 of the requirement: the profile is read before a Task can be rendered, so a
    project whose profile does not validate cannot be dispatched at all, and the
    "silently fall back to the generic checklist" path is structurally absent rather
    than merely discouraged.
    """
    current_phase = require_workflow_phase(current_phase)
    if resolution.is_invalid:
        raise TaskContextError(
            f"{INVALID_PROFILE_REASON}: {resolution.path} exists but is not a valid "
            f"quality profile ({resolution.error}); no Task is dispatched and the "
            "generic checklist is not restored"
        )
    if current_phase == FINAL_REVIEW_PHASE:
        scope = tuple(requested_phases)
        if not scope:
            raise TaskContextError(
                "requested_phases is required for the final_review quality gate: the "
                "final gate re-checks the requested workflow, not a single phase"
            )
        for phase in scope:
            require_workflow_phase(phase, field="requested_phases")
    else:
        scope = (current_phase,)

    attributes = resolution.attributes_for(scope)
    blocking = blocking_attributes(attributes)
    return {
        "profile_status": resolution.status,
        "profile_path": resolution.path,
        "applicable_quality_attributes": tuple(
            attribute.summary() for attribute in attributes
        )
        or ("none",),
        "blocking_quality_attributes": tuple(attribute.id for attribute in blocking)
        or ("none",),
        "general_gate": tuple(
            f"{gate_id} {label}" for gate_id, label in MINIMAL_GENERAL_GATE
        ),
        "decision_priority": DECISION_PRIORITY,
        "non_blocking_by_default": (
            QUALITY_GATE_SUPPRESSION_MANDATE,
            *NON_BLOCKING_BY_DEFAULT,
        ),
        "verdict_semantics": VERDICT_SEMANTICS,
    }


# ---- serialization: the boundary as the agent actually receives it --------------
# A builder whose result only ever reaches the coordinator's own records has not
# established a boundary; it has described one. These renderers put layer 1 (and the
# Reviewer's eight keys) into the Task spec body itself -- the text Orca injects into
# the dispatch preamble, and the text the low-level fallback sends verbatim -- so the
# payload travels WITH the dispatch instead of being rebuilt afterwards for the log.
TASK_BOUNDARY_SPEC_HEADER = "=== TASK BOUNDARY (layer 1) ==="
TASK_BOUNDARY_SPEC_FOOTER = "=== END TASK BOUNDARY ==="
REVIEWER_CONTEXT_SPEC_HEADER = "=== REVIEWER CONTEXT (delta-first) ==="
REVIEWER_CONTEXT_SPEC_FOOTER = "=== END REVIEWER CONTEXT ==="
QUALITY_GATE_SPEC_HEADER = "=== QUALITY GATE (profile-first) ==="
QUALITY_GATE_SPEC_FOOTER = "=== END QUALITY GATE ==="
# The last line of every rendered spec, and the agent's signal that the payload is
# COMPLETE. A dispatch preamble arrives a line at a time; an agent that acts on the
# first line of a multi-line spec races the injection that is still delivering it,
# and the runtime fails the dispatch with "is not starting". One unambiguous
# terminator is what makes "the whole boundary has arrived" checkable.
TASK_SPEC_END_MARKER = "=== END TASK SPEC ==="
# One line per key, so a rendered block stays greppable and a receipt can be parsed
# back with str.split. Multi-value fields are joined with this separator; a value
# that contains it round-trips wrong, which is why nothing here builds one.
SPEC_VALUE_SEPARATOR = " || "


def _render_value(value: Any) -> str:
    """One line for one key, whatever shape the builders put in it."""
    if isinstance(value, str):
        return value.replace("\n", SPEC_VALUE_SEPARATOR)
    if isinstance(value, tuple):
        return SPEC_VALUE_SEPARATOR.join(
            f"{item[0]} -> {item[1]}"
            if isinstance(item, tuple)
            else str(item).replace("\n", " ")
            for item in value
        )
    return str(value)


def strip_task_context(spec: str) -> str:
    """The caller's own text, with any block a previous render appended removed.

    The same base text is rendered twice on the run_attempt path -- once for
    task-create and once inside run_existing_task -- so every consumer of "the
    caller's text" has to agree on where it ends, or the second render quotes the
    first one back into itself.
    """
    return spec.split(TASK_BOUNDARY_SPEC_HEADER)[0].rstrip()


def render_task_spec(
    base_spec: str,
    boundary: dict[str, str],
    reviewer_context: dict[str, Any] | None = None,
    quality_gate: dict[str, Any] | None = None,
) -> str:
    """The Task spec body an agent is handed: the caller's text, then the boundary.

    Idempotent on purpose. The same string has to reach `task-create --spec` (which
    Orca replays into the preamble) and the low-level `terminal send` prompt, and the
    two call sites are in different functions; rendering an already-rendered spec
    therefore trims back to the caller's own text rather than stacking a second block.
    """
    body = strip_task_context(base_spec)
    lines = [body, "", TASK_BOUNDARY_SPEC_HEADER]
    # Fixed key order, and a KeyError rather than a blank line for a missing one: a
    # boundary that cannot be rendered in full is not a boundary.
    lines.extend(f"{key}: {_render_value(boundary[key])}" for key in TASK_BOUNDARY_KEYS)
    lines.append(TASK_BOUNDARY_SPEC_FOOTER)
    if reviewer_context is not None:
        lines.append("")
        lines.append(REVIEWER_CONTEXT_SPEC_HEADER)
        lines.extend(
            f"{key}: {_render_value(reviewer_context[key])}"
            for key in REVIEWER_CONTEXT_KEYS
        )
        lines.append(REVIEWER_CONTEXT_SPEC_FOOTER)
    if quality_gate is not None:
        lines.append("")
        lines.append(QUALITY_GATE_SPEC_HEADER)
        lines.extend(
            f"{key}: {_render_value(quality_gate[key])}" for key in QUALITY_GATE_KEYS
        )
        lines.append(QUALITY_GATE_SPEC_FOOTER)
    lines.append(TASK_SPEC_END_MARKER)
    return "\n".join(lines)


def parse_task_boundary(text: str) -> dict[str, str]:
    """Read a rendered layer-1 block back out of whatever text carries it.

    The inverse of render_task_spec's first block, and the reason an agent can prove
    what it received: it parses the preamble it was actually handed and echoes the
    result. Raises TaskContextError when the block is absent or incomplete, so a
    missing boundary reads as a failure rather than as an empty dict.
    """
    if TASK_BOUNDARY_SPEC_HEADER not in text:
        raise TaskContextError("no task boundary block in the supplied text")
    block = text.split(TASK_BOUNDARY_SPEC_HEADER, 1)[1].split(
        TASK_BOUNDARY_SPEC_FOOTER, 1
    )[0]
    parsed: dict[str, str] = {}
    for line in block.splitlines():
        key, separator, value = line.partition(": ")
        key = key.strip()
        if key in TASK_BOUNDARY_KEYS:
            parsed[key] = (
                value.replace(SPEC_VALUE_SEPARATOR, "\n") if separator else ""
            )
        elif line.strip().rstrip(":") in TASK_BOUNDARY_KEYS:
            parsed[line.strip().rstrip(":")] = ""
    missing = [key for key in TASK_BOUNDARY_KEYS if key not in parsed]
    if missing:
        raise TaskContextError(f"task boundary block is missing {missing}")
    return parsed


# ---- the receipt: what the agent proves it received ------------------------------
# The coordinator can show its command log to prove it SENT a boundary. Only the
# agent can show it ARRIVED, and it can only show that by parsing the text it was
# handed and quoting it back. Rendered here, next to the format it reads, so every
# agent that echoes one echoes the same shape.
BOUNDARY_RECEIPT_HEADING = "## Task Boundary Receipt"
BOUNDARY_RECEIPT_PREFIX = "RECEIVED "
REVIEWER_CONTEXT_RECEIPT_KEY = "reviewer_context_keys"
QUALITY_GATE_RECEIPT_KEY = "quality_gate_keys"


def parse_reviewer_context_keys(text: str) -> tuple[str, ...]:
    """Which of the eight Reviewer keys the supplied text actually carries.

    Empty tuple when there is no Reviewer block at all -- a worker's Task spec has
    none, and that absence is itself an assertion worth being able to make.
    """
    if REVIEWER_CONTEXT_SPEC_HEADER not in text:
        return ()
    block = text.split(REVIEWER_CONTEXT_SPEC_HEADER, 1)[1].split(
        REVIEWER_CONTEXT_SPEC_FOOTER, 1
    )[0]
    present = {
        line.partition(":")[0].strip()
        for line in block.splitlines()
        if line.partition(":")[1]
    }
    return tuple(key for key in REVIEWER_CONTEXT_KEYS if key in present)


def parse_reviewer_context(text: str) -> dict[str, str]:
    """Read a rendered Reviewer block back out of whatever text carries it.

    The counterpart of parse_task_boundary for layer 2, and for the same reason: the
    only way to check what a Reviewer was actually TOLD is to read the payload it was
    handed. Multi-value fields come back as the rendered line, so a caller checks a
    reference with `in` or splits on SPEC_VALUE_SEPARATOR. Raises when the block is
    absent or incomplete -- parse_reviewer_context_keys is the total function for
    "which keys are here", including the worker's answer of none.
    """
    if REVIEWER_CONTEXT_SPEC_HEADER not in text:
        raise TaskContextError("no reviewer context block in the supplied text")
    block = text.split(REVIEWER_CONTEXT_SPEC_HEADER, 1)[1].split(
        REVIEWER_CONTEXT_SPEC_FOOTER, 1
    )[0]
    parsed: dict[str, str] = {}
    for line in block.splitlines():
        key, separator, value = line.partition(": ")
        key = key.strip()
        if key in REVIEWER_CONTEXT_KEYS:
            parsed[key] = value if separator else ""
        elif line.strip().rstrip(":") in REVIEWER_CONTEXT_KEYS:
            parsed[line.strip().rstrip(":")] = ""
    missing = [key for key in REVIEWER_CONTEXT_KEYS if key not in parsed]
    if missing:
        raise TaskContextError(f"reviewer context block is missing {missing}")
    return parsed


def parse_quality_gate_keys(text: str) -> tuple[str, ...]:
    """Which of the eight quality-gate keys the supplied text actually carries.

    Empty tuple when there is no block at all, which is the honest answer for a spec
    rendered before this wiring existed.
    """
    if QUALITY_GATE_SPEC_HEADER not in text:
        return ()
    block = text.split(QUALITY_GATE_SPEC_HEADER, 1)[1].split(
        QUALITY_GATE_SPEC_FOOTER, 1
    )[0]
    present = {
        line.partition(":")[0].strip()
        for line in block.splitlines()
        if line.partition(":")[1]
    }
    return tuple(key for key in QUALITY_GATE_KEYS if key in present)


def parse_quality_gate(text: str) -> dict[str, str]:
    """Read a rendered quality-gate block back out of whatever text carries it.

    The only way to check what an agent was actually TOLD about the quality model is
    to read the payload it was handed, which is the same reason parse_task_boundary
    and parse_reviewer_context exist. Raises when the block is absent or incomplete.
    """
    if QUALITY_GATE_SPEC_HEADER not in text:
        raise TaskContextError("no quality gate block in the supplied text")
    block = text.split(QUALITY_GATE_SPEC_HEADER, 1)[1].split(
        QUALITY_GATE_SPEC_FOOTER, 1
    )[0]
    parsed: dict[str, str] = {}
    for line in block.splitlines():
        key, separator, value = line.partition(": ")
        key = key.strip()
        if key in QUALITY_GATE_KEYS:
            parsed[key] = value if separator else ""
        elif line.strip().rstrip(":") in QUALITY_GATE_KEYS:
            parsed[line.strip().rstrip(":")] = ""
    missing = [key for key in QUALITY_GATE_KEYS if key not in parsed]
    if missing:
        raise TaskContextError(f"quality gate block is missing {missing}")
    return parsed


def render_boundary_receipt(text: str) -> str:
    """The block an agent appends to its own report to prove what it was handed.

    Returns "" when the supplied text carries no boundary, which is the honest answer
    for a dispatch that predates this wiring. It never invents a receipt: every line
    is read back out of the payload the agent received.
    """
    try:
        boundary = parse_task_boundary(text)
    except TaskContextError:
        return ""
    lines = [BOUNDARY_RECEIPT_HEADING]
    lines.extend(
        f"{BOUNDARY_RECEIPT_PREFIX}{key}: "
        + boundary[key].replace("\n", SPEC_VALUE_SEPARATOR)
        for key in TASK_BOUNDARY_KEYS
    )
    reviewer_keys = parse_reviewer_context_keys(text)
    if reviewer_keys:
        lines.append(
            f"{BOUNDARY_RECEIPT_PREFIX}{REVIEWER_CONTEXT_RECEIPT_KEY}: "
            + ", ".join(reviewer_keys)
        )
    quality_keys = parse_quality_gate_keys(text)
    if quality_keys:
        lines.append(
            f"{BOUNDARY_RECEIPT_PREFIX}{QUALITY_GATE_RECEIPT_KEY}: "
            + ", ".join(quality_keys)
        )
    return "\n" + "\n".join(lines) + "\n"
