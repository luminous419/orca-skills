#!/usr/bin/env python3
"""Tests for scripts/workflow_contract.py's REVIEW_VERDICT parsing.

OS-17 review round 3 MAJOR-2: the two-valued workflow gate and OS-1's separate
four-valued report annotation are parsed by two different regexes
(CHOICE_LINE vs REVIEW_VERDICT_LINE) precisely so a value containing spaces
("PASS WITH NOTES") and a four-way choice don't collide with the existing
two-way CHOICE_LINE matches. These tests exercise that parsing directly,
independent of any real SKILL.md/reviews/common.md content.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.workflow_contract import (
    WorkflowContractError,
    _find_review_verdict_choice,
    load_workflow_output_contract,
)


SOURCE = Path("test.md")


class ReviewVerdictParsingTests(unittest.TestCase):
    def test_the_documented_four_values_parse_in_order(self) -> None:
        field, values = _find_review_verdict_choice(
            "REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED\n", SOURCE
        )
        self.assertEqual(field, "REVIEW_VERDICT")
        self.assertEqual(values, ("PASS", "PASS WITH NOTES", "FAIL", "BLOCKED"))

    def test_a_two_valued_result_line_does_not_match(self) -> None:
        with self.assertRaisesRegex(WorkflowContractError, "missing REVIEW_VERDICT"):
            _find_review_verdict_choice("RESULT: PASS | FAIL\n", SOURCE)

    def test_a_three_valued_line_does_not_match(self) -> None:
        with self.assertRaisesRegex(WorkflowContractError, "missing REVIEW_VERDICT"):
            _find_review_verdict_choice(
                "FINDING_RESOLUTION: RESOLVED | DISPUTED | BLOCKED\n", SOURCE
            )

    def test_inconsistent_field_names_across_matches_fail_closed(self) -> None:
        text = (
            "REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED\n"
            "REPORT_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED\n"
        )
        with self.assertRaisesRegex(WorkflowContractError, "inconsistent fields"):
            _find_review_verdict_choice(text, SOURCE)

    def test_inconsistent_values_across_matches_fail_closed(self) -> None:
        text = (
            "REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED\n"
            "REVIEW_VERDICT: PASS | NOTED | FAIL | BLOCKED\n"
        )
        with self.assertRaisesRegex(WorkflowContractError, "inconsistent REVIEW_VERDICT values"):
            _find_review_verdict_choice(text, SOURCE)

    def test_repeated_identical_lines_are_fine(self) -> None:
        text = (
            "REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED\n"
            "REVIEW_VERDICT: PASS | PASS WITH NOTES | FAIL | BLOCKED\n"
        )
        field, values = _find_review_verdict_choice(text, SOURCE)
        self.assertEqual(field, "REVIEW_VERDICT")
        self.assertEqual(values, ("PASS", "PASS WITH NOTES", "FAIL", "BLOCKED"))


class LoadWorkflowOutputContractReviewVerdictTests(unittest.TestCase):
    """Same real SKILL.md files both skills already ship, read end to end."""

    REPO_ROOT = Path(__file__).resolve().parents[1]

    def test_the_orchestration_skill_carries_the_four_values(self) -> None:
        contract = load_workflow_output_contract(
            self.REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )
        self.assertEqual(contract.review_verdict_field, "REVIEW_VERDICT")
        self.assertEqual(
            contract.review_verdict_values,
            ("PASS", "PASS WITH NOTES", "FAIL", "BLOCKED"),
        )

    def test_the_loop_skill_carries_the_same_four_values(self) -> None:
        """orca-worker-reviewer-loop's own SKILL.md never inlines REVIEW_VERDICT --
        only the shared reviews/common.md does -- so this also proves
        load_workflow_output_contract reads that file, not just skill_path."""
        contract = load_workflow_output_contract(
            self.REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
        )
        self.assertEqual(contract.review_verdict_field, "REVIEW_VERDICT")
        self.assertEqual(
            contract.review_verdict_values,
            ("PASS", "PASS WITH NOTES", "FAIL", "BLOCKED"),
        )


if __name__ == "__main__":
    unittest.main()
