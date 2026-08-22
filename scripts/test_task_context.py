#!/usr/bin/env python3
"""Smoke tests for the shared Task boundary / Reviewer context builders."""

from __future__ import annotations

import inspect
import unittest

from scripts.task_context import (
    DISPATCH_INJECTED_IDENTITY,
    REVIEWER_CONTEXT_KEYS,
    REVIEWER_DRILL_DOWN_MANDATE,
    REVIEWER_PRIOR_PASS_IS_NOT_EVIDENCE,
    TASK_BOUNDARY_KEYS,
    TaskContextError,
    build_reviewer_context,
    build_task_boundary,
)


class TaskBoundaryTests(unittest.TestCase):
    def test_layer_one_carries_five_keys_and_neither_id(self) -> None:
        boundary = build_task_boundary(
            current_role="worker",
            current_phase="implementation",
            current_iteration=2,
            artifact_contract="artifacts/IMPLEMENTATION.md",
            relevant_previous_findings=("F1", "F2"),
        )

        self.assertEqual(tuple(sorted(boundary)), tuple(sorted(TASK_BOUNDARY_KEYS)))
        self.assertNotIn("task_id", boundary)
        self.assertNotIn("dispatch_id", boundary)
        self.assertIn("task_id", DISPATCH_INJECTED_IDENTITY)
        self.assertIn("dispatch_id", DISPATCH_INJECTED_IDENTITY)
        # Every value is a string, so two attempts can be frozen and compared.
        self.assertTrue(all(isinstance(value, str) for value in boundary.values()))
        self.assertEqual(boundary["current_iteration"], "2")
        self.assertEqual(boundary["relevant_previous_findings"], "F1\nF2")

    def test_build_task_boundary_has_no_task_id_or_dispatch_id_parameter(self) -> None:
        """The absence is structural, not a habit the caller has to keep.

        A Task spec body is assembled BEFORE the Task exists (task-create answers with
        the id) and the dispatch id does not exist until the worker start response, so
        a builder that accepted either would be offering a value nobody can supply.
        Checking the signature, not just the output, is what makes "you cannot pass it"
        a fact about the API rather than about this one call.
        """
        parameters = inspect.signature(build_task_boundary).parameters

        self.assertNotIn("task_id", parameters)
        self.assertNotIn("dispatch_id", parameters)
        self.assertEqual(
            tuple(sorted(parameters)), tuple(sorted(TASK_BOUNDARY_KEYS))
        )
        for name, parameter in parameters.items():
            with self.subTest(parameter=name):
                self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)

    def test_no_identity_string_appears_anywhere_in_the_payload(self) -> None:
        """Keys AND values: an id smuggled into a free-text field is still an id."""
        boundary = build_task_boundary(
            current_role="reviewer",
            current_phase="design",
            current_iteration=3,
            artifact_contract="artifacts/DESIGN.md",
            relevant_previous_findings=("D1: the anchor block drifted",),
        )

        flattened = " ".join(list(boundary) + list(boundary.values()))
        for forbidden in ("task_id", "dispatch_id", "task_", "ctx_"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, flattened)
        for injected in DISPATCH_INJECTED_IDENTITY:
            with self.subTest(key=injected):
                self.assertNotIn(injected, boundary)

    def test_an_incomplete_boundary_is_refused(self) -> None:
        for kwargs in (
            {"current_role": "coordinator"},
            {"current_phase": ""},
            {"current_iteration": 0},
            {"artifact_contract": ""},
        ):
            complete = {
                "current_role": "reviewer",
                "current_phase": "design",
                "current_iteration": 1,
                "artifact_contract": "artifacts/DESIGN.md",
            }
            complete.update(kwargs)
            with self.subTest(**kwargs):
                with self.assertRaises(TaskContextError):
                    build_task_boundary(**complete)


class ReviewerContextTests(unittest.TestCase):
    def context(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "original_objective": "make reuse safe",
            "current_phase": "implementation",
            "current_delta": ("scripts/task_context.py",),
            "drill_down": ("scripts/",),
        }
        payload.update(overrides)
        return build_reviewer_context(**payload)  # type: ignore[arg-type]

    def test_the_eight_keys_are_produced_with_the_drill_down_mandate_first(self) -> None:
        context = self.context()

        self.assertEqual(
            tuple(sorted(context)), tuple(sorted(REVIEWER_CONTEXT_KEYS))
        )
        self.assertEqual(context["drill_down"][0], REVIEWER_DRILL_DOWN_MANDATE)
        self.assertIn("scripts/", context["drill_down"])

    def test_a_correction_review_is_told_its_remembered_pass_is_not_evidence(self) -> None:
        context = self.context(previous_findings=(("F1", "RESOLVED"),))

        self.assertEqual(
            context["drill_down"][0], REVIEWER_PRIOR_PASS_IS_NOT_EVIDENCE
        )
        self.assertEqual(context["previous_findings"], (("F1", "RESOLVED"),))

    def test_drill_down_is_mandatory_in_two_distinguishable_ways(self) -> None:
        """Omitted is a TypeError; empty is a TaskContextError. R-4's code defence."""
        with self.assertRaises(TypeError):
            build_reviewer_context(
                original_objective="o", current_phase="p"
            )  # type: ignore[call-arg]
        with self.assertRaises(TaskContextError):
            self.context(drill_down=())


if __name__ == "__main__":
    unittest.main()
