#!/usr/bin/env python3
"""Parse Worker/Reviewer workflow result contracts from Markdown skills."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


CHOICE_LINE = re.compile(
    r"(?m)^(?P<field>[A-Z_]+):\s*(?P<left>[A-Z_]+)\s*\|\s*(?P<right>[A-Z_]+)\s*$"
)
# OS-1's report-level verdict (`REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL |
# BLOCKED`) is a different shape from CHOICE_LINE above: exactly four values, and a
# value may itself contain spaces ("PASS WITH NOTES"). A dedicated pattern rather
# than generalizing CHOICE_LINE, the same way finding-resolution's three-valued
# vocabulary already gets its own search below instead of reusing CHOICE_LINE.
REVIEW_VERDICT_LINE = re.compile(
    r"(?m)^(?P<field>[A-Z_]+):\s*"
    r"(?P<values>[A-Z]+(?:\s[A-Z]+)*(?:\s*\|\s*[A-Z]+(?:\s[A-Z]+)*){3})\s*$"
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
    review_verdict_field: str
    review_verdict_values: tuple[str, ...]


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


def _find_review_verdict_choice(text: str, source: Path) -> tuple[str, tuple[str, ...]]:
    """The field name and exactly-four values of OS-1's REVIEW_VERDICT line.

    Same shape of check as `_find_choice` above (every match must agree on field
    name and value set; at least one match must exist), just against
    REVIEW_VERDICT_LINE instead of CHOICE_LINE.
    """
    matches = list(REVIEW_VERDICT_LINE.finditer(text))
    if not matches:
        raise WorkflowContractError(f"{source}: missing REVIEW_VERDICT vocabulary")
    fields = {match.group("field") for match in matches}
    if len(fields) != 1:
        raise WorkflowContractError(
            f"{source}: inconsistent fields for the REVIEW_VERDICT line"
        )
    value_tuples = {
        tuple(value.strip() for value in match.group("values").split("|"))
        for match in matches
    }
    if len(value_tuples) != 1:
        raise WorkflowContractError(
            f"{source}: inconsistent REVIEW_VERDICT values"
        )
    return matches[0].group("field"), next(iter(value_tuples))


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

    # REVIEW_VERDICT itself lives in reviews/common.md, not necessarily in SKILL.md
    # directly (orca-worker-reviewer-loop's SKILL.md never inlines it; only the
    # shared reviews/common.md does) -- same reason finding_resolution_values above
    # reads templates/implementation.md instead of skill_text.
    reviews_common_path = skill_path.parent / "reviews" / "common.md"
    reviews_common_text = reviews_common_path.read_text(encoding="utf-8")
    review_verdict_field, review_verdict_values = _find_review_verdict_choice(
        reviews_common_text, reviews_common_path
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
        review_verdict_field=review_verdict_field,
        review_verdict_values=review_verdict_values,
    )
