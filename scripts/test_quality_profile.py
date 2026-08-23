#!/usr/bin/env python3
"""Tests for the Project Quality Profile: loader, applicability, and gate semantics."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.quality_profile import (
    APPLICABLE_PHASES,
    DEFAULT_PROFILE_PATH,
    GENERAL_GATE_IDS,
    INVALID_PROFILE_REASON,
    MINIMAL_GENERAL_GATE,
    NON_BLOCKING_BY_DEFAULT,
    PROFILE_STATUS_ABSENT,
    PROFILE_STATUS_INVALID,
    PROFILE_STATUS_LOADED,
    QUALITY_CATEGORIES,
    VERDICT_BLOCKED,
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_PASS_WITH_NOTES,
    WORKFLOW_GATE_VALUES,
    QualityProfileError,
    blocking_attributes,
    load_profile_text,
    resolve_quality_profile,
    workflow_gate_value,
)
from scripts.task_context import ALL_APPLICABLE_PHASES

VALID_PROFILE = textwrap.dedent(
    """\
    # a project profile
    version: 1

    quality_attributes:

      - id: DOMAIN-001
        category: business-domain
        name: Idempotent processing
        blocking: true
        applies_to:
          - design
          - implementation
          - test
        description: >
          Reprocessing the same input must not produce
          a duplicate side effect.
        verification:
          - code
          - tests

      - id: TEAM-001
        category: team-convention
        name: Repository convention
        blocking: false
        description: >
          Keep the existing repository structure.
    """
)


def write_profile(root: Path, text: str) -> Path:
    path = root / DEFAULT_PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class ProfileLoaderTests(unittest.TestCase):
    """Section 13-A: every way a profile can be right, and every way it can be wrong."""

    def test_a_valid_profile_loads_every_documented_field(self) -> None:
        profile = load_profile_text(VALID_PROFILE)

        self.assertEqual(profile.version, 1)
        self.assertEqual(
            [attribute.id for attribute in profile.attributes],
            ["DOMAIN-001", "TEAM-001"],
        )
        domain, team = profile.attributes
        self.assertEqual(domain.category, "business-domain")
        self.assertEqual(domain.name, "Idempotent processing")
        self.assertIs(domain.blocking, True)
        self.assertEqual(domain.applies_to, ("design", "implementation", "test"))
        self.assertEqual(domain.verification, ("code", "tests"))
        # A folded block scalar arrives as one line, not as the raw indented text.
        self.assertEqual(
            domain.description,
            "Reprocessing the same input must not produce a duplicate side effect.",
        )
        self.assertIs(team.blocking, False)
        self.assertEqual(team.applies_to, ())

    def test_a_missing_profile_is_absent_not_invalid(self) -> None:
        """Absent and invalid demand opposite responses, so they are separate states."""
        with tempfile.TemporaryDirectory() as directory:
            resolution = resolve_quality_profile(Path(directory))

        self.assertEqual(resolution.status, PROFILE_STATUS_ABSENT)
        self.assertTrue(resolution.is_absent)
        self.assertFalse(resolution.is_invalid)
        self.assertIsNone(resolution.profile)
        self.assertEqual(resolution.path, DEFAULT_PROFILE_PATH)
        self.assertEqual(resolution.attributes_for(("implementation",)), ())

    def test_a_present_profile_resolves_to_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_profile(root, VALID_PROFILE)
            resolution = resolve_quality_profile(root)

        self.assertEqual(resolution.status, PROFILE_STATUS_LOADED)
        self.assertIsNotNone(resolution.profile)
        self.assertEqual(
            [attribute.id for attribute in resolution.attributes_for(("design",))],
            ["DOMAIN-001", "TEAM-001"],
        )

    def test_malformed_yaml_raises_instead_of_parsing_partially(self) -> None:
        for label, source in (
            ("tab indentation", "version: 1\n\tquality_attributes: []\n"),
            ("no colon", "version: 1\nquality_attributes\n"),
            ("dangling key", "version: 1\nquality_attributes:\n"),
            ("indented root", "  version: 1\n"),
            ("empty", "\n# only a comment\n"),
        ):
            with self.subTest(label):
                with self.assertRaises(QualityProfileError):
                    load_profile_text(source)

    def test_invalid_schema_shapes_are_rejected(self) -> None:
        cases = {
            "unknown top-level key": "version: 1\nquality_attributes: []\nextra: 1\n",
            "missing version": "quality_attributes: []\n",
            "missing quality_attributes": "version: 1\n",
            "non-integer version": "version: one\nquality_attributes: []\n",
            "attributes not a list": "version: 1\nquality_attributes: nope\n",
        }
        for label, source in cases.items():
            with self.subTest(label):
                with self.assertRaises(QualityProfileError):
                    load_profile_text(source)

    def test_a_missing_required_attribute_key_is_rejected(self) -> None:
        source = textwrap.dedent(
            """\
            version: 1
            quality_attributes:
              - id: DOMAIN-001
                category: business-domain
                name: Missing blocking
            """
        )
        with self.assertRaisesRegex(QualityProfileError, "missing required keys"):
            load_profile_text(source)

    def test_an_unknown_attribute_key_is_rejected_rather_than_ignored(self) -> None:
        """A typo'd `blocked:` that reads as absent would silently disarm a rule."""
        source = textwrap.dedent(
            """\
            version: 1
            quality_attributes:
              - id: DOMAIN-001
                category: business-domain
                name: Typo
                blocking: true
                blockign: true
            """
        )
        with self.assertRaisesRegex(QualityProfileError, "unknown keys"):
            load_profile_text(source)

    def test_a_duplicate_quality_attribute_id_is_rejected(self) -> None:
        source = textwrap.dedent(
            """\
            version: 1
            quality_attributes:
              - id: DOMAIN-001
                category: business-domain
                name: First
                blocking: true
              - id: DOMAIN-001
                category: team-convention
                name: Second
                blocking: false
            """
        )
        with self.assertRaisesRegex(QualityProfileError, "duplicate quality attribute"):
            load_profile_text(source)

    def test_blocking_must_be_an_actual_boolean(self) -> None:
        """`blocking: "true"` reads as blocking to a human; it must not to the gate."""
        for value in ('"true"', "yes", "1", "TRUE"):
            with self.subTest(value):
                source = textwrap.dedent(
                    f"""\
                    version: 1
                    quality_attributes:
                      - id: DOMAIN-001
                        category: business-domain
                        name: Ambiguous
                        blocking: {value}
                    """
                )
                with self.assertRaisesRegex(
                    QualityProfileError, "blocking must be the boolean"
                ):
                    load_profile_text(source)

    def test_applies_to_must_name_supported_phases(self) -> None:
        for value in ("deployment", "final_review"):
            with self.subTest(value):
                source = textwrap.dedent(
                    f"""\
                    version: 1
                    quality_attributes:
                      - id: DOMAIN-001
                        category: business-domain
                        name: Bad phase
                        blocking: true
                        applies_to:
                          - {value}
                    """
                )
                with self.assertRaisesRegex(QualityProfileError, "unsupported"):
                    load_profile_text(source)

    def test_an_unknown_category_is_rejected(self) -> None:
        source = textwrap.dedent(
            """\
            version: 1
            quality_attributes:
              - id: X-001
                category: whatever
                name: Bad category
                blocking: false
            """
        )
        with self.assertRaisesRegex(QualityProfileError, "unknown category"):
            load_profile_text(source)

    def test_an_unsupported_schema_version_is_explicit(self) -> None:
        with self.assertRaisesRegex(QualityProfileError, "unsupported quality profile"):
            load_profile_text("version: 2\nquality_attributes: []\n")

    def test_the_shipped_example_profile_validates(self) -> None:
        """The example is documentation only if it actually loads."""
        example = (
            Path(__file__).resolve().parents[1]
            / ".orca"
            / "quality-profile.example.yaml"
        )
        profile = load_profile_text(example.read_text(encoding="utf-8"))

        self.assertEqual(profile.version, 1)
        self.assertTrue(profile.attributes)
        for attribute in profile.attributes:
            self.assertIn(attribute.category, QUALITY_CATEGORIES)


class PhaseApplicabilityTests(unittest.TestCase):
    """Section 13-B: an attribute is only evaluated where it applies."""

    def profile(self):
        return load_profile_text(VALID_PROFILE)

    def test_a_design_scoped_attribute_is_absent_from_earlier_phases(self) -> None:
        profile = self.profile()

        self.assertEqual(
            [attribute.id for attribute in profile.for_phase("analysis")], ["TEAM-001"]
        )
        self.assertEqual(
            [attribute.id for attribute in profile.for_phase("plan")], ["TEAM-001"]
        )
        self.assertIn(
            "DOMAIN-001", [attribute.id for attribute in profile.for_phase("design")]
        )
        self.assertIn(
            "DOMAIN-001",
            [attribute.id for attribute in profile.for_phase("implementation")],
        )

    def test_an_omitted_applies_to_means_every_applicable_phase(self) -> None:
        profile = self.profile()
        for phase in APPLICABLE_PHASES:
            with self.subTest(phase):
                self.assertIn(
                    "TEAM-001",
                    [attribute.id for attribute in profile.for_phase(phase)],
                )

    def test_specialized_phases_follow_the_same_rule(self) -> None:
        """bugfix / refactoring are phases like any other, not a special case."""
        profile = self.profile()

        for phase in ("bugfix", "refactoring"):
            with self.subTest(phase):
                ids = [attribute.id for attribute in profile.for_phase(phase)]
                self.assertEqual(ids, ["TEAM-001"])

        scoped = load_profile_text(
            textwrap.dedent(
                """\
                version: 1
                quality_attributes:
                  - id: OPS-001
                    category: operational-risk
                    name: Bugfix only
                    blocking: true
                    applies_to:
                      - bugfix
                """
            )
        )
        self.assertEqual(
            [attribute.id for attribute in scoped.for_phase("bugfix")], ["OPS-001"]
        )
        self.assertEqual(scoped.for_phase("refactoring"), ())

    def test_the_final_gate_sees_every_attribute_of_the_requested_workflow(
        self,
    ) -> None:
        profile = self.profile()

        self.assertEqual(
            [attribute.id for attribute in profile.for_phases(("analysis", "plan"))],
            ["TEAM-001"],
        )
        self.assertEqual(
            [
                attribute.id
                for attribute in profile.for_phases(("implementation", "test"))
            ],
            ["DOMAIN-001", "TEAM-001"],
        )

    def test_the_applicable_phase_axis_is_shared_with_task_context(self) -> None:
        """Two copies of this list would let an applies_to value be valid in one place."""
        self.assertEqual(APPLICABLE_PHASES, ALL_APPLICABLE_PHASES)


class VerdictSemanticsTests(unittest.TestCase):
    """Section 13-C and 13-D: what fails a gate, and what deliberately does not."""

    def test_the_workflow_gate_stays_two_valued(self) -> None:
        self.assertEqual(WORKFLOW_GATE_VALUES, ("PASS", "FAIL"))

    def test_pass_with_notes_maps_to_a_pass_gate(self) -> None:
        """The annotation must not reach the lifecycle as a third state."""
        self.assertEqual(workflow_gate_value(VERDICT_PASS), "PASS")
        self.assertEqual(workflow_gate_value(VERDICT_PASS_WITH_NOTES), "PASS")

    def test_a_blocking_violation_fails_and_blocked_maps_to_fail(self) -> None:
        self.assertEqual(workflow_gate_value(VERDICT_FAIL), "FAIL")
        self.assertEqual(workflow_gate_value(VERDICT_BLOCKED), "FAIL")

    def test_an_unknown_verdict_is_refused(self) -> None:
        with self.assertRaisesRegex(QualityProfileError, "unknown review verdict"):
            workflow_gate_value("PASS WITH RESERVATIONS")

    def test_only_blocking_attributes_can_fail_a_gate(self) -> None:
        """Severity is not blocking: a non-blocking attribute never becomes one."""
        profile = load_profile_text(VALID_PROFILE)
        blocking = blocking_attributes(profile.for_phase("implementation"))

        self.assertEqual([attribute.id for attribute in blocking], ["DOMAIN-001"])
        self.assertNotIn("TEAM-001", [attribute.id for attribute in blocking])

    def test_the_general_gate_stays_five_categories(self) -> None:
        self.assertEqual(len(MINIMAL_GENERAL_GATE), 5)
        self.assertEqual(GENERAL_GATE_IDS, ("G1", "G2", "G3", "G4", "G5"))

    def test_generic_concerns_are_named_as_non_blocking_by_default(self) -> None:
        """Section 13-D: the suppression list is data, not an inference per reviewer."""
        for concern in (
            "clean architecture preference",
            "SOLID preference",
            "naming taste",
            "minor duplication",
            "documentation polish",
            "speculative future extensibility",
            "generalized best practice",
            "stylistic refactoring suggestion",
        ):
            with self.subTest(concern):
                self.assertIn(concern, NON_BLOCKING_BY_DEFAULT)
        # And none of them is a general gate id, which is what would re-promote them.
        for _, label in MINIMAL_GENERAL_GATE:
            self.assertNotIn(label, NON_BLOCKING_BY_DEFAULT)


class MalformedProfileTests(unittest.TestCase):
    """Section 13-G: a broken profile is a state, and never a quiet fallback."""

    def test_a_malformed_profile_resolves_to_invalid_with_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_profile(root, "version: 1\nquality_attributes: nope\n")
            resolution = resolve_quality_profile(root)

        self.assertEqual(resolution.status, PROFILE_STATUS_INVALID)
        self.assertTrue(resolution.is_invalid)
        self.assertFalse(resolution.is_absent)
        self.assertIsNone(resolution.profile)
        self.assertIn("quality_attributes must be a list", resolution.error)

    def test_resolution_never_raises_so_absent_and_invalid_stay_distinguishable(
        self,
    ) -> None:
        """A raise here would collapse both states into one except-branch."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_profile(root, "!!! not a profile\n")
            resolution = resolve_quality_profile(root)

        self.assertEqual(resolution.status, PROFILE_STATUS_INVALID)

    def test_a_directory_at_the_profile_path_is_invalid_not_absent(self) -> None:
        """IMPL-I1 F-002. `is_file()` said False here, and False read as 'no profile'.

        A directory at .orca/quality-profile.yaml is a broken setup -- a botched
        checkout, a mkdir -p that ran on the file path, a mount point. Reading it as
        "this project chose to have no quality attributes" silently swaps the whole
        gate for the no-profile fallback, which is the one outcome sections 3 and 11
        say must never happen quietly.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / DEFAULT_PROFILE_PATH).mkdir(parents=True)
            resolution = resolve_quality_profile(root)

        self.assertEqual(resolution.status, PROFILE_STATUS_INVALID)
        self.assertFalse(resolution.is_absent)
        self.assertIsNone(resolution.profile)
        self.assertIn("not a regular file", resolution.error)

    def test_a_broken_symlink_at_the_profile_path_is_invalid_not_absent(self) -> None:
        """`exists()` is False for a broken symlink, so existence alone is not enough."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / DEFAULT_PROFILE_PATH
            path.parent.mkdir(parents=True)
            path.symlink_to(root / ".orca" / "gone.yaml")
            resolution = resolve_quality_profile(root)

        self.assertEqual(resolution.status, PROFILE_STATUS_INVALID)
        self.assertIn("not a regular file", resolution.error)

    def test_only_a_truly_missing_path_is_absent(self) -> None:
        """The other half of F-002: absent must stay reachable for the normal case."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # An .orca directory that simply has no profile in it is still absent.
            (root / ".orca").mkdir()
            self.assertEqual(
                resolve_quality_profile(root).status, PROFILE_STATUS_ABSENT
            )

    def test_the_reason_code_is_the_documented_one(self) -> None:
        self.assertEqual(INVALID_PROFILE_REASON, "INVALID_QUALITY_PROFILE")


if __name__ == "__main__":
    unittest.main()
