#!/usr/bin/env python3
"""Smoke tests for the shared Task boundary / Reviewer context builders."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from scripts.quality_profile import (
    DEFAULT_PROFILE_PATH,
    PROFILE_STATUS_ABSENT,
    PROFILE_STATUS_INVALID,
    PROFILE_STATUS_LOADED,
    QualityProfileResolution,
    load_profile_text,
    resolve_quality_profile,
)
from scripts.task_context import (
    AGENT_ROUTING_KEYS,
    build_agent_routing_context,
    parse_agent_routing,
    parse_agent_routing_keys,
    RISK_CONTEXT_KEYS,
    RISK_SPEC_HEADER,
    build_risk_context,
    parse_risk_profile,
    parse_risk_profile_keys,
    AGENT_MODES,
    CANONICAL_PHASES,
    DISPATCH_INJECTED_IDENTITY,
    QUALITY_GATE_KEYS,
    QUALITY_GATE_RECEIPT_KEY,
    REVIEWER_CONTEXT_KEYS,
    REVIEWER_DRILL_DOWN_MANDATE,
    REVIEWER_PRIOR_PASS_IS_NOT_EVIDENCE,
    TASK_BOUNDARY_KEYS,
    WORKFLOW_PHASES,
    TaskContextError,
    build_quality_gate_context,
    build_reviewer_context,
    build_task_boundary,
    ensure_run_artifact_root,
    parse_quality_gate,
    parse_reviewer_context,
    phase_artifact_contract,
    render_boundary_receipt,
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


class RiskProfileBlockTests(unittest.TestCase):
    """OS-3: the `=== RISK PROFILE ===` block (T-16 structural half, T-24)."""

    def boundary(self, phase: str = "implementation"):
        return build_task_boundary(
            current_role="worker",
            current_phase=phase,
            current_iteration=1,
            artifact_contract=f"artifacts/runs/r/{phase.upper()}.md",
        )

    def test_the_two_axes_share_no_key(self) -> None:
        """T-16. Independence made structural, not conventional."""
        self.assertEqual(set(QUALITY_GATE_KEYS) & set(RISK_CONTEXT_KEYS), set())

    def test_the_two_builders_share_no_argument(self) -> None:
        """T-16. build_risk_context takes no profile; build_quality_gate_context
        takes no risk. Neither can read the other's state even by accident."""
        risk_params = set(inspect.signature(build_risk_context).parameters)
        gate_params = set(inspect.signature(build_quality_gate_context).parameters)
        self.assertNotIn("resolution", risk_params)
        self.assertEqual(risk_params & {"resolution", "profile", "quality_profile"}, set())
        self.assertEqual(gate_params & {"risk", "risk_source"}, set())

    def test_safety_floor_is_phase_specific_and_risk_invariant(self) -> None:
        """T-24. `safety_floor` is identical at every level -- the machine-checkable
        form of "risk changes validation strength, never the safety floor"."""
        expected = {
            "implementation": "unit_test_add_modify_execute_pass",
            "bugfix": "regression_test_required",
            "refactoring": "behavior_preservation_and_relevant_unit_tests",
            "analysis": "not_applicable",
            "plan": "not_applicable",
            "design": "not_applicable",
            "test": "not_applicable",
        }
        for phase, floor in expected.items():
            floors = {
                build_risk_context(
                    risk=risk, risk_source="default", current_phase=phase
                )["safety_floor"]
                for risk in ("low", "medium", "high")
            }
            with self.subTest(phase=phase):
                self.assertEqual(floors, {floor})

    def test_safety_floor_evidence_is_the_one_risk_varying_key(self) -> None:
        """T-24. What receipt THIS dispatch must produce."""
        cases = {
            ("implementation", "low"): "unit_test_status_required",
            ("implementation", "medium"): "phase_reviewer_verifies",
            ("implementation", "high"): "phase_reviewer_verifies",
            ("bugfix", "low"): "unit_test_status_required",
            ("refactoring", "low"): "unit_test_status_required",
            ("analysis", "low"): "not_applicable",
            ("test", "low"): "not_applicable",
        }
        for (phase, risk), expected in cases.items():
            with self.subTest(phase=phase, risk=risk):
                context = build_risk_context(
                    risk=risk, risk_source="default", current_phase=phase
                )
                self.assertEqual(context["safety_floor_evidence"], expected)

    def test_derived_values_track_the_level(self) -> None:
        low = build_risk_context(
            risk="low", risk_source="explicit", current_phase="implementation"
        )
        high = build_risk_context(
            risk="high", risk_source="default", current_phase="implementation"
        )
        self.assertEqual(low["phase_gate"], "worker_only")
        self.assertEqual(high["phase_gate"], "worker_then_phase_reviewer")
        self.assertEqual(low["downstream_revalidation"], "disabled")
        self.assertEqual(high["downstream_revalidation"], "enabled")
        for context in (low, high):
            self.assertEqual(context["final_review"], "mandatory")
            self.assertEqual(context["quality_profile_axis"], "independent")

    def test_an_unknown_level_or_source_fails_closed(self) -> None:
        with self.assertRaises(TaskContextError):
            build_risk_context(
                risk="extreme", risk_source="default", current_phase="implementation"
            )
        with self.assertRaises(TaskContextError):
            build_risk_context(
                risk="low", risk_source="inferred", current_phase="implementation"
            )

    def test_omitting_the_block_renders_a_byte_identical_spec(self) -> None:
        """The compatibility guarantee for every existing caller."""
        boundary = self.boundary()
        self.assertEqual(
            render_task_spec("base", boundary),
            render_task_spec("base", boundary, None, None, None),
        )
        self.assertNotIn(RISK_SPEC_HEADER, render_task_spec("base", boundary))

    def test_the_block_round_trips_through_the_rendered_spec(self) -> None:
        context = build_risk_context(
            risk="low", risk_source="explicit", current_phase="implementation"
        )
        spec = render_task_spec("base", self.boundary(), None, None, context)
        self.assertEqual(parse_risk_profile(spec), context)
        self.assertEqual(parse_risk_profile_keys(spec), RISK_CONTEXT_KEYS)

    def test_parsing_a_spec_without_a_block_is_an_error_but_keys_are_empty(self) -> None:
        spec = render_task_spec("base", self.boundary())
        self.assertEqual(parse_risk_profile_keys(spec), ())
        with self.assertRaises(TaskContextError):
            parse_risk_profile(spec)


class AgentRoutingContextTests(unittest.TestCase):
    """OS-4's third block: disjoint from the other two, and absent on the legacy path."""

    def routing(self, *, risk="low"):
        from scripts.agent_profile import (
            RUNTIME_ORCHESTRATION,
            SELECTION_SELECTED,
            AgentProfileSelection,
            load_agent_profiles_text,
            materialize_run_routing,
        )

        profiles = dict(
            load_agent_profiles_text(
                "version: 1\nprofiles:\n  p:\n"
                "    defaults:\n      worker: claude\n      reviewer: codex\n"
                "    final_review:\n      reviewer: codex\n",
                path="test.yaml",
                source="project_local",
            )
        )
        return materialize_run_routing(
            runtime=RUNTIME_ORCHESTRATION,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name="p", profile=profiles["p"]
            ),
            requested_phases=("analysis",),
            risk=risk,
        )

    def test_the_three_axis_key_sets_are_disjoint(self) -> None:
        """Agent routing, risk and the quality gate are three independent axes."""
        self.assertEqual(set(AGENT_ROUTING_KEYS) & set(RISK_CONTEXT_KEYS), set())
        self.assertEqual(set(AGENT_ROUTING_KEYS) & set(QUALITY_GATE_KEYS), set())

    def test_the_builder_reads_the_routing_without_re_resolving(self) -> None:
        context = build_agent_routing_context(
            routing=self.routing(), current_phase="analysis"
        )

        self.assertEqual(context["agent_profile"], "p")
        self.assertEqual(context["agent_profile_source"], "project_local")
        self.assertEqual(context["phase_worker"], "claude")
        self.assertEqual(context["final_reviewer"], "codex")
        self.assertEqual(context["routing_mutability"], "immutable_for_this_run")

    def test_every_key_is_rendered_and_parses_back(self) -> None:
        context = build_agent_routing_context(
            routing=self.routing(), current_phase="analysis"
        )
        spec = render_task_spec(
            "body", self.boundary(), agent_routing=context
        )

        self.assertEqual(parse_agent_routing_keys(spec), AGENT_ROUTING_KEYS)
        self.assertEqual(parse_agent_routing(spec), context)

    def test_omitting_the_argument_renders_a_byte_identical_spec(self) -> None:
        """The legacy path passes nothing, so its spec cannot grow a routing block."""
        boundary = self.boundary()

        self.assertEqual(
            render_task_spec("body", boundary),
            render_task_spec("body", boundary, agent_routing=None),
        )
        self.assertNotIn("AGENT ROUTING", render_task_spec("body", boundary))
        self.assertEqual(parse_agent_routing_keys(render_task_spec("body", boundary)), ())

    def test_a_legacy_spec_has_no_routing_block_to_parse(self) -> None:
        with self.assertRaises(TaskContextError):
            parse_agent_routing(render_task_spec("body", self.boundary()))

    def test_the_builder_refuses_a_missing_routing(self) -> None:
        with self.assertRaises(TaskContextError):
            build_agent_routing_context(routing=None, current_phase="analysis")

    def boundary(self):
        return build_task_boundary(
            current_role="worker",
            current_phase="analysis",
            current_iteration=1,
            artifact_contract="write: artifacts/runs/run_test/ANALYSIS.md",
        )


if __name__ == "__main__":
    unittest.main()


PROFILE_TEXT = """version: 1

quality_attributes:

  - id: DOMAIN-001
    category: business-domain
    name: Idempotent processing
    blocking: true
    applies_to:
      - design
      - implementation

  - id: DESIGN-001
    category: platform-infrastructure
    name: Design only rule
    blocking: false
    applies_to:
      - design

  - id: TEAM-001
    category: team-convention
    name: Repository convention
    blocking: false
"""


def loaded_resolution() -> QualityProfileResolution:
    return QualityProfileResolution(
        status=PROFILE_STATUS_LOADED,
        path=DEFAULT_PROFILE_PATH,
        profile=load_profile_text(PROFILE_TEXT),
    )


def absent_resolution() -> QualityProfileResolution:
    return QualityProfileResolution(
        status=PROFILE_STATUS_ABSENT, path=DEFAULT_PROFILE_PATH
    )


class QualityGateContextTests(unittest.TestCase):
    """Section 13-E/F: the quality model as the dispatched spec actually carries it."""

    def gate(self, phase: str = "implementation", **kwargs: object) -> dict[str, object]:
        kwargs.setdefault("resolution", loaded_resolution())
        return build_quality_gate_context(current_phase=phase, **kwargs)  # type: ignore[arg-type]

    def test_the_gate_carries_every_documented_key(self) -> None:
        gate = self.gate()

        self.assertEqual(tuple(sorted(gate)), tuple(sorted(QUALITY_GATE_KEYS)))
        self.assertEqual(gate["profile_status"], PROFILE_STATUS_LOADED)
        self.assertEqual(gate["profile_path"], DEFAULT_PROFILE_PATH)

    def test_only_the_phases_own_attributes_reach_it(self) -> None:
        design = self.gate("design")
        implementation = self.gate("implementation")
        analysis = self.gate("analysis")

        self.assertIn(
            "DESIGN-001", " ".join(design["applicable_quality_attributes"])  # type: ignore[arg-type]
        )
        self.assertNotIn(
            "DESIGN-001",
            " ".join(implementation["applicable_quality_attributes"]),  # type: ignore[arg-type]
        )
        self.assertNotIn(
            "DOMAIN-001", " ".join(analysis["applicable_quality_attributes"])  # type: ignore[arg-type]
        )
        # applies_to omitted means every applicable phase, so TEAM-001 is in all three.
        for gate in (design, implementation, analysis):
            self.assertIn(
                "TEAM-001", " ".join(gate["applicable_quality_attributes"])  # type: ignore[arg-type]
            )

    def test_blocking_is_reported_separately_from_applicability(self) -> None:
        gate = self.gate("design")

        self.assertEqual(gate["blocking_quality_attributes"], ("DOMAIN-001",))

    def test_the_final_gate_spans_the_requested_workflow(self) -> None:
        gate = build_quality_gate_context(
            resolution=loaded_resolution(),
            current_phase="final_review",
            requested_phases=("implementation", "test"),
        )

        rendered = " ".join(gate["applicable_quality_attributes"])  # type: ignore[arg-type]
        self.assertIn("DOMAIN-001", rendered)
        self.assertIn("TEAM-001", rendered)
        self.assertNotIn("DESIGN-001", rendered)

    def test_the_final_gate_refuses_an_undeclared_requested_set(self) -> None:
        with self.assertRaisesRegex(TaskContextError, "requested_phases is required"):
            build_quality_gate_context(
                resolution=loaded_resolution(), current_phase="final_review"
            )

    def test_an_absent_profile_does_not_restore_a_generic_checklist(self) -> None:
        """Section 13-F: no profile means three tiers, not the old broad checklist."""
        gate = build_quality_gate_context(
            resolution=absent_resolution(), current_phase="implementation"
        )

        self.assertEqual(gate["profile_status"], PROFILE_STATUS_ABSENT)
        self.assertEqual(gate["applicable_quality_attributes"], ("none",))
        self.assertEqual(gate["blocking_quality_attributes"], ("none",))
        # The general gate is still exactly five, and the suppression list is still
        # carried: absent is a defined state, not a fall back to "review everything".
        self.assertEqual(len(gate["general_gate"]), 5)  # type: ignore[arg-type]
        self.assertIn(
            "generalized best practice", gate["non_blocking_by_default"]  # type: ignore[operator]
        )

    def test_an_invalid_profile_cannot_produce_a_dispatchable_context(self) -> None:
        """Section 13-G: the no-silent-fallback rule, enforced structurally."""
        resolution = QualityProfileResolution(
            status=PROFILE_STATUS_INVALID,
            path=DEFAULT_PROFILE_PATH,
            error="duplicate quality attribute id: DOMAIN-001",
        )

        with self.assertRaisesRegex(TaskContextError, "INVALID_QUALITY_PROFILE"):
            build_quality_gate_context(
                resolution=resolution, current_phase="implementation"
            )

    def test_the_rendered_block_reads_back_as_the_gate_that_built_it(self) -> None:
        spec = render_task_spec(
            "worker implementation iteration 1",
            build_task_boundary(
                current_role="worker",
                current_phase="implementation",
                current_iteration=1,
                artifact_contract="artifacts/runs/run_x/IMPLEMENTATION.md",
            ),
            None,
            self.gate(),
        )

        parsed = parse_quality_gate(spec)
        self.assertEqual(tuple(sorted(parsed)), tuple(sorted(QUALITY_GATE_KEYS)))
        self.assertEqual(parsed["profile_status"], PROFILE_STATUS_LOADED)
        self.assertIn("DOMAIN-001", parsed["applicable_quality_attributes"])
        self.assertIn("G1 explicit requirement violation", parsed["general_gate"])
        self.assertIn("minimal general gate", parsed["decision_priority"])
        self.assertIn("PASS WITH NOTES", parsed["verdict_semantics"])
        with self.assertRaisesRegex(TaskContextError, "no quality gate block"):
            parse_quality_gate("a spec rendered before this wiring")

    def test_the_receipt_proves_the_gate_arrived(self) -> None:
        spec = render_task_spec(
            "worker implementation iteration 1",
            build_task_boundary(
                current_role="worker",
                current_phase="implementation",
                current_iteration=1,
                artifact_contract="artifacts/runs/run_x/IMPLEMENTATION.md",
            ),
            None,
            self.gate(),
        )

        receipt = render_boundary_receipt(spec)
        self.assertIn(QUALITY_GATE_RECEIPT_KEY, receipt)
        self.assertIn("profile_status", receipt)

    def test_this_repository_has_no_active_profile_yet(self) -> None:
        """The example is not the profile: activating it is the project's choice."""
        repo_root = Path(__file__).resolve().parents[1]
        resolution = resolve_quality_profile(repo_root)

        self.assertEqual(resolution.status, PROFILE_STATUS_ABSENT)


class DispatchedSpecQualityGateTests(unittest.TestCase):
    """The same assertion, made against the text the runtime actually dispatches.

    Testing build_quality_gate_context alone would only prove a helper works. What
    the requirement asks for is that the Worker's and the Reviewer's real Task specs
    carry the model, which is why this reaches through dispatch_context -- the one
    function both the supervised (`task-create --spec`) and low-level (`terminal
    send`) paths render their agent-visible text with.
    """

    def dispatched(self, role: str, phase: str = "implementation") -> str:
        from scripts.orca_runtime_harness import dispatch_context

        spec, _, _ = dispatch_context(
            role,
            1,
            "complete" if role == "worker" else "pass",
            phase=phase,
            run_id="run_quality",
            quality_profile=loaded_resolution(),
        )
        return spec

    def test_both_roles_receive_the_same_quality_model(self) -> None:
        worker = parse_quality_gate(self.dispatched("worker"))
        reviewer = parse_quality_gate(self.dispatched("reviewer"))

        # Semantically identical, not merely both present: a Worker judged against a
        # different attribute set than its Reviewer buys correction rounds for rules
        # it was never handed.
        self.assertEqual(worker, reviewer)
        self.assertIn("DOMAIN-001", worker["applicable_quality_attributes"])
        self.assertEqual(worker["blocking_quality_attributes"], "DOMAIN-001")

    def test_the_dispatched_spec_carries_the_decision_priority_and_general_gate(
        self,
    ) -> None:
        for role in ("worker", "reviewer"):
            with self.subTest(role):
                parsed = parse_quality_gate(self.dispatched(role))
                self.assertIn(
                    "1 explicit user/project requirements", parsed["decision_priority"]
                )
                self.assertIn(
                    "2 applicable project quality profile attributes",
                    parsed["decision_priority"],
                )
                for gate_id in ("G1", "G2", "G3", "G4", "G5"):
                    self.assertIn(gate_id, parsed["general_gate"])
                self.assertIn(
                    "never promoted to a blocking finding",
                    parsed["non_blocking_by_default"],
                )

    def test_phase_filtering_survives_into_the_dispatched_spec(self) -> None:
        analysis = parse_quality_gate(self.dispatched("reviewer", "analysis"))
        design = parse_quality_gate(self.dispatched("reviewer", "design"))

        self.assertNotIn("DESIGN-001", analysis["applicable_quality_attributes"])
        self.assertIn("DESIGN-001", design["applicable_quality_attributes"])

    def test_the_final_reviewer_spec_spans_the_requested_workflow(self) -> None:
        from scripts.orca_runtime_harness import dispatch_context

        spec, _, _ = dispatch_context(
            "reviewer",
            1,
            "pass",
            phase="final_review",
            run_id="run_quality",
            quality_profile=loaded_resolution(),
            requested_phases=("implementation", "test"),
        )

        parsed = parse_quality_gate(spec)
        self.assertIn("DOMAIN-001", parsed["applicable_quality_attributes"])
        self.assertNotIn("DESIGN-001", parsed["applicable_quality_attributes"])

    def test_an_invalid_profile_stops_the_dispatch_from_being_rendered(self) -> None:
        from scripts.orca_runtime_harness import dispatch_context

        with self.assertRaisesRegex(TaskContextError, "INVALID_QUALITY_PROFILE"):
            dispatch_context(
                "worker",
                1,
                "complete",
                phase="implementation",
                run_id="run_quality",
                quality_profile=QualityProfileResolution(
                    status=PROFILE_STATUS_INVALID,
                    path=DEFAULT_PROFILE_PATH,
                    error="unsupported quality profile schema version 9",
                ),
            )
