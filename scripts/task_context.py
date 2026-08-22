#!/usr/bin/env python3
"""Task boundary and Reviewer context builders shared by both harnesses.

Standard library only, and deliberately importing neither harness: e2e_harness and
orca_runtime_harness do not import each other today, and this module must not become
the edge that couples them.
"""

from __future__ import annotations

from typing import Any

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


class TaskContextError(ValueError):
    """Raised when a context builder is asked to produce an incomplete payload."""


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
    if not current_phase:
        raise TaskContextError("current_phase is required")
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
    if not current_phase:
        raise TaskContextError("current_phase is required")
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
    return "\n" + "\n".join(lines) + "\n"
