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
