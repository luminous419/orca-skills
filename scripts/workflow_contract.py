#!/usr/bin/env python3
"""Parse Worker/Reviewer workflow result contracts from Markdown skills."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


CHOICE_LINE = re.compile(
    r"(?m)^(?P<field>[A-Z_]+):\s*(?P<left>[A-Z_]+)\s*\|\s*(?P<right>[A-Z_]+)\s*$"
)


class WorkflowContractError(ValueError):
    """Raised when documented workflow output contracts cannot be resolved."""


@dataclass(frozen=True)
class WorkflowOutputContract:
    worker_field: str
    worker_complete: str
    worker_blocked: str
    reviewer_field: str
    reviewer_pass: str
    reviewer_fail: str
    finding_resolution_values: tuple[str, ...]
    completed_status: str
    blocked_status: str
    escalated_status: str


def _find_choice(
    text: str, expected_values: frozenset[str], source: Path
) -> tuple[str, tuple[str, str]]:
    matches = [
        match
        for match in CHOICE_LINE.finditer(text)
        if frozenset((match.group("left"), match.group("right"))) == expected_values
    ]
    if not matches:
        raise WorkflowContractError(
            f"{source}: missing documented choice {sorted(expected_values)}"
        )
    fields = {match.group("field") for match in matches}
    if len(fields) != 1:
        raise WorkflowContractError(
            f"{source}: inconsistent fields for {sorted(expected_values)}"
        )
    first = matches[0]
    return first.group("field"), (first.group("left"), first.group("right"))


def load_workflow_output_contract(skill_path: Path) -> WorkflowOutputContract:
    """Load shared workflow fields and values from SKILL.md and its template."""

    skill_text = skill_path.read_text(encoding="utf-8")
    worker_field, worker_values = _find_choice(
        skill_text, frozenset(("COMPLETE", "BLOCKED")), skill_path
    )
    reviewer_field, reviewer_values = _find_choice(
        skill_text, frozenset(("PASS", "FAIL")), skill_path
    )

    implementation_path = skill_path.parent / "templates" / "implementation.md"
    implementation_text = implementation_path.read_text(encoding="utf-8")
    resolution_match = re.search(
        r"`(RESOLVED\s*\|\s*DISPUTED\s*\|\s*BLOCKED)`",
        implementation_text,
    )
    if not resolution_match:
        raise WorkflowContractError(
            f"{implementation_path}: missing finding resolution vocabulary"
        )
    resolution_values = tuple(
        value.strip() for value in resolution_match.group(1).split("|")
    )

    for status in ("COMPLETED", "BLOCKED", "ESCALATED"):
        if f"STATUS: {status}" not in skill_text:
            raise WorkflowContractError(
                f"{skill_path}: missing terminal workflow status {status}"
            )

    return WorkflowOutputContract(
        worker_field=worker_field,
        worker_complete="COMPLETE",
        worker_blocked="BLOCKED",
        reviewer_field=reviewer_field,
        reviewer_pass="PASS",
        reviewer_fail="FAIL",
        finding_resolution_values=resolution_values,
        completed_status="COMPLETED",
        blocked_status="BLOCKED",
        escalated_status="ESCALATED",
    )
