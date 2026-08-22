#!/usr/bin/env python3
"""Smoke tests for the shared Task boundary / Reviewer context builders."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from scripts.task_context import (
    AGENT_MODES,
    CANONICAL_PHASES,
    DISPATCH_INJECTED_IDENTITY,
    REVIEWER_CONTEXT_KEYS,
    REVIEWER_DRILL_DOWN_MANDATE,
    REVIEWER_PRIOR_PASS_IS_NOT_EVIDENCE,
    TASK_BOUNDARY_KEYS,
    WORKFLOW_PHASES,
    TaskContextError,
    build_reviewer_context,
    build_task_boundary,
    ensure_run_artifact_root,
    parse_reviewer_context,
    phase_artifact_contract,
    render_task_spec,
    require_workflow_phase,
    run_artifact_root,
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

    def test_the_rendered_block_reads_back_as_the_context_that_built_it(self) -> None:
        """A payload nobody can read back is a claim, not a boundary."""
        context = self.context(
            approved_baseline=("artifacts/ANALYSIS.md",),
            validation=("worker outcome=succeeded",),
        )
        spec = render_task_spec(
            "worker implementation iteration 1",
            build_task_boundary(
                current_role="reviewer",
                current_phase="implementation",
                current_iteration=1,
                artifact_contract="artifacts/REVIEW_IMPLEMENTATION.md",
            ),
            context,
        )

        parsed = parse_reviewer_context(spec)
        self.assertEqual(tuple(sorted(parsed)), tuple(sorted(REVIEWER_CONTEXT_KEYS)))
        self.assertEqual(parsed["current_phase"], "implementation")
        self.assertEqual(parsed["approved_baseline"], "artifacts/ANALYSIS.md")
        self.assertEqual(parsed["current_delta"], "scripts/task_context.py")
        self.assertEqual(parsed["validation"], "worker outcome=succeeded")
        with self.assertRaisesRegex(TaskContextError, "no reviewer context block"):
            parse_reviewer_context("a worker spec carries none")

    def test_drill_down_is_mandatory_in_two_distinguishable_ways(self) -> None:
        """Omitted is a TypeError; empty is a TaskContextError. R-4's code defence."""
        with self.assertRaises(TypeError):
            build_reviewer_context(
                original_objective="o", current_phase="p"
            )  # type: ignore[call-arg]
        with self.assertRaises(TaskContextError):
            self.context(drill_down=())


class WorkflowPhaseTests(unittest.TestCase):
    """PR #12 MAJOR-1: current_phase is the workflow axis, and only that axis.

    The builders used to accept any non-empty string, which is how an agent's
    behaviour script ("complete" / "pass") ended up in a dispatched Task boundary
    with every key spelled correctly.
    """

    def test_every_canonical_phase_passes_and_every_agent_mode_is_refused(self) -> None:
        for phase in WORKFLOW_PHASES:
            with self.subTest(phase=phase):
                self.assertEqual(require_workflow_phase(phase), phase)
        for mode in sorted(AGENT_MODES):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(TaskContextError, "agent mode"):
                    require_workflow_phase(mode)
        # The two axes do not overlap, so refusing one can never refuse the other.
        self.assertEqual(AGENT_MODES & set(WORKFLOW_PHASES), set())
        self.assertEqual(WORKFLOW_PHASES[: len(CANONICAL_PHASES)], CANONICAL_PHASES)

    def test_a_missing_or_unknown_phase_fails_closed(self) -> None:
        """No silent fallback: the caller is told, not quietly given something else."""
        for absent in (None, "", 0):
            with self.subTest(value=absent):
                with self.assertRaisesRegex(TaskContextError, "is required"):
                    require_workflow_phase(absent)
        with self.assertRaisesRegex(TaskContextError, "unknown current_phase"):
            require_workflow_phase("almost-implementation")

    def test_the_builders_refuse_an_agent_mode_as_a_phase(self) -> None:
        with self.assertRaisesRegex(TaskContextError, "agent mode"):
            build_task_boundary(
                current_role="worker",
                current_phase="complete",
                current_iteration=1,
                artifact_contract="artifacts/IMPLEMENTATION.md",
            )
        with self.assertRaisesRegex(TaskContextError, "agent mode"):
            build_reviewer_context(
                original_objective="make reuse safe",
                current_phase="pass",
                drill_down=("scripts/",),
            )

    def test_the_artifact_contract_is_derived_from_the_phase(self) -> None:
        """A worker's deliverable and its reviewer's report, named by the same phase.

        This is what lets a Reviewer's current_delta point at the WORKER's artifact
        instead of at the reviewer's own output path.
        """
        self.assertEqual(
            phase_artifact_contract(role="worker", phase="design", run_id="run_a"),
            "artifacts/runs/run_a/DESIGN.md",
        )
        self.assertEqual(
            phase_artifact_contract(role="reviewer", phase="design", run_id="run_a"),
            "artifacts/runs/run_a/REVIEW_DESIGN.md",
        )
        self.assertEqual(
            phase_artifact_contract(
                role="reviewer", phase="final_review", run_id="run_a"
            ),
            "artifacts/runs/run_a/FINAL_REVIEW.md",
        )
        with self.assertRaisesRegex(TaskContextError, "agent mode"):
            phase_artifact_contract(role="worker", phase="complete")
        with self.assertRaisesRegex(TaskContextError, "unknown role"):
            phase_artifact_contract(role="coordinator", phase="design")

    def test_the_artifact_contract_requires_a_run_id(self) -> None:
        """Fail-closed like require_workflow_phase: no fallback to the shared root.

        role and phase are still checked first (test above): this is what happens
        once both of those are valid and only run_id is missing.
        """
        with self.assertRaisesRegex(TaskContextError, "run_id is required"):
            phase_artifact_contract(role="worker", phase="design")
        with self.assertRaisesRegex(TaskContextError, "run_id is required"):
            phase_artifact_contract(role="reviewer", phase="final_review")

    def test_different_runs_never_share_an_artifact_path(self) -> None:
        """The whole point of run-level isolation, stated as non-overlap.

        Every (role, phase) pair this module can be asked to name gets a disjoint
        path per run_id, and every path it produces stays inside that run's own
        artifacts/runs/<run_id>/ directory -- including BUGFIX/REFACTORING, which
        use the exact same (role, phase) -> path shape as the canonical phases.
        """
        phases = (*WORKFLOW_PHASES,)
        run_a_paths = {
            phase_artifact_contract(role=role, phase=phase, run_id="run_a")
            for phase in phases
            for role in ("worker", "reviewer")
        }
        run_b_paths = {
            phase_artifact_contract(role=role, phase=phase, run_id="run_b")
            for phase in phases
            for role in ("worker", "reviewer")
        }
        self.assertEqual(run_a_paths & run_b_paths, set())
        for path in run_a_paths:
            self.assertTrue(path.startswith("artifacts/runs/run_a/"))
        for path in run_b_paths:
            self.assertTrue(path.startswith("artifacts/runs/run_b/"))
        for phase in ("bugfix", "refactoring"):
            with self.subTest(phase=phase):
                self.assertEqual(
                    phase_artifact_contract(
                        role="worker", phase=phase, run_id="run_a"
                    ),
                    f"artifacts/runs/run_a/{phase.upper()}.md",
                )

    def test_a_path_like_run_id_is_refused_not_sanitized(self) -> None:
        """run_artifact_root is the isolation boundary, not just a formatter.

        A run_id is interpolated straight into a filesystem path; real Orca run ids
        never contain a separator or a bare '.'/'..' segment, so a value that does is
        refused outright rather than cleaned up and used anyway.
        """
        for unsafe in ("../escaped", "a/b", "a\\b", ".", ".."):
            with self.subTest(run_id=unsafe):
                with self.assertRaisesRegex(TaskContextError, "single path segment"):
                    run_artifact_root(unsafe)
                with self.assertRaisesRegex(TaskContextError, "single path segment"):
                    phase_artifact_contract(
                        role="worker", phase="design", run_id=unsafe
                    )


class RunArtifactRootProvisioningTests(unittest.TestCase):
    """MAJOR 1 (PR #13 review): the directory has to exist before anyone writes to it.

    run_artifact_root() only ever returns a string; something has to turn that into a
    real directory before the first Task naming it is dispatched, or the first Worker
    told to write there is the one that discovers it is missing.
    """

    def test_ensure_creates_the_missing_directory_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            base = Path(workspace)
            target = base / "artifacts" / "runs" / "run_fresh"
            self.assertFalse(target.exists(), "fixture must not pre-create the dir")

            created = ensure_run_artifact_root("run_fresh", base=base)

            self.assertEqual(created, target)
            self.assertTrue(target.is_dir())
            # Calling it again (the second phase of the same run) must not raise.
            self.assertEqual(ensure_run_artifact_root("run_fresh", base=base), target)

    def test_ensure_without_a_base_defaults_to_the_real_artifacts_root(self) -> None:
        """Signature-only: actually calling this would create a real directory.

        `base` defaulting to None (never a value invented here) is what makes the
        real repository's artifacts/runs/<run_id>/ the target when a harness omits
        it -- exercising that path in a test would litter the working tree, which is
        exactly what the other two tests in this class use `base=` to avoid.
        """
        parameter = inspect.signature(ensure_run_artifact_root).parameters["base"]
        self.assertIsNone(parameter.default)

    def test_ensure_still_fails_closed_on_a_path_like_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(TaskContextError, "single path segment"):
                ensure_run_artifact_root("../escaped", base=Path(workspace))
            self.assertEqual(list(Path(workspace).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
