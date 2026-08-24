#!/usr/bin/env python3
"""Tests for the Agent Profile: schema, precedence, required roles, and the gate.

The two halves of this file are deliberately opposed, and that opposition is the
point. NonRequiredEntryTests proves that a malformed, disallowed or PATH-missing
command in a role this run never dispatches does NOT block the run;
RequiredPromotionTests proves the same value DOES block once a requested phase or a
risk level makes that role required. Either half alone would be satisfiable by a
wrong implementation -- the first by a gate that checks nothing, the second by a
gate that checks everything.
"""

from __future__ import annotations

import re
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts.agent_profile import (
    ORIGIN_DEFAULTS,
    ORIGIN_EXPLICIT,
    ORIGIN_PHASE,
    ORIGIN_UNRESOLVED,
    PHASE_KEYS,
    PROJECT_PROFILE_RELATIVE_PATH,
    REASON_COMMAND_NOT_ALLOWED,
    REASON_COMMAND_NOT_FOUND,
    REASON_INVALID_COMMAND,
    REASON_INVALID_PROFILE,
    REASON_ROLE_UNRESOLVED,
    REASON_UNKNOWN_PROFILE,
    RESULT_OPTIONAL,
    RESULT_REQUIRED,
    ROLE_FINAL_REVIEWER,
    ROLE_REVIEWER,
    ROLE_WORKER,
    RUNTIME_LOOP,
    RUNTIME_ORCHESTRATION,
    SELECTION_INVALID,
    SELECTION_OMITTED,
    SELECTION_SELECTED,
    SOURCE_PROJECT_LOCAL,
    SOURCE_USER_GLOBAL,
    USER_PROFILE_RELATIVE_PATH,
    AgentProfileError,
    build_agent_profiles,
    discover_agent_profiles,
    load_agent_profiles_text,
    materialize_run_routing,
    parse_agent_profiles_document,
    required_roles,
    resolve_final_reviewer,
    resolve_phase_role,
    select_agent_profile,
    validate_required_roles,
    validate_profile_command_safety,
    validate_routing_commands,
)
from scripts.quality_profile import APPLICABLE_PHASES
from scripts.skill_policy import AGENT_COMMAND_PATTERN

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PROFILE = REPO_ROOT / ".orca" / "agent-profiles.example.yaml"

KNOWN_COMMANDS = ("claude", "codex", "claude-glm", "claude-gemma")
CUSTOM_PATTERN = re.compile(r"(?:claude|codex)-[A-Za-z0-9._-]+", re.ASCII)

VALID = textwrap.dedent(
    """\
    version: 1
    profiles:
      diverse:
        defaults:
          worker: claude
          reviewer: codex
        phases:
          design:
            worker: claude
            reviewer: claude
          implementation:
            worker: codex
            reviewer: codex
        final_review:
          reviewer: codex
    """
)


def load(text: str, *, source: str = SOURCE_PROJECT_LOCAL):
    return dict(load_agent_profiles_text(text, path="test.yaml", source=source))


def always_found(command: str) -> str | None:
    return f"/usr/bin/{command}"


def never_found(command: str) -> str | None:
    return None


def found_except(*missing: str):
    def which(command: str) -> str | None:
        return None if command in missing else f"/usr/bin/{command}"

    return which


def gate(routing, *, which=always_found) -> None:
    validate_routing_commands(
        routing,
        token_pattern=AGENT_COMMAND_PATTERN,
        known_commands=KNOWN_COMMANDS,
        custom_command_pattern=CUSTOM_PATTERN,
        which=which,
    )


def gate_profile_safety(profile, *, explicit_worker="", explicit_reviewer="") -> None:
    """The static half only -- no `which`, because the function under test never
    takes one. Its absence from the signature is itself part of what these tests
    pin: PATH cannot leak into this gate even by accident. Operates on the whole
    profile DEFINITION, not on materialized routing -- see
    validate_profile_command_safety()."""
    validate_profile_command_safety(
        profile,
        explicit_worker=explicit_worker,
        explicit_reviewer=explicit_reviewer,
        token_pattern=AGENT_COMMAND_PATTERN,
        known_commands=KNOWN_COMMANDS,
        custom_command_pattern=CUSTOM_PATTERN,
    )


class SchemaOnlyParsingTests(unittest.TestCase):
    """Building validates the DOCUMENT. It does not judge commands (D-002-R1)."""

    def test_a_valid_profile_loads_every_documented_field(self) -> None:
        profile = load(VALID)["diverse"]

        self.assertEqual(profile.name, "diverse")
        self.assertEqual(profile.default_for(ROLE_WORKER), "claude")
        self.assertEqual(profile.default_for(ROLE_REVIEWER), "codex")
        self.assertEqual(profile.phase_for("design", ROLE_REVIEWER), "claude")
        self.assertEqual(profile.phase_for("implementation", ROLE_WORKER), "codex")
        self.assertEqual(profile.final_reviewer(), "codex")

    def test_multiple_named_profiles_coexist_in_one_file(self) -> None:
        profiles = load(
            "version: 1\n"
            "profiles:\n"
            "  a:\n    defaults:\n      worker: claude\n"
            "  b:\n    defaults:\n      worker: codex\n"
        )

        self.assertEqual(sorted(profiles), ["a", "b"])

    def test_the_shipped_example_profile_validates(self) -> None:
        profiles = load(EXAMPLE_PROFILE.read_text(encoding="utf-8"))

        self.assertIn("diverse", profiles)
        self.assertEqual(profiles["diverse"].final_reviewer(), "codex")

    def test_building_accepts_bash_as_a_command_value(self) -> None:
        """Not a security hole: an unused command is never executed, and a used one
        is refused by the gate. Refusing it here would block runs that never touch
        it."""
        profiles = load(
            "version: 1\nprofiles:\n  p:\n    phases:\n      test:\n        worker: bash\n"
        )

        self.assertEqual(profiles["p"].phase_for("test", ROLE_WORKER), "bash")

    def test_building_accepts_a_space_containing_command_value(self) -> None:
        profiles = load(
            'version: 1\nprofiles:\n  p:\n    defaults:\n      worker: "my agent"\n'
        )

        self.assertEqual(profiles["p"].default_for(ROLE_WORKER), "my agent")

    def test_building_never_calls_which(self) -> None:
        """The regression guard for the eager-check mistake this design made twice."""
        calls: list[str] = []

        def spy(command: str) -> str | None:
            calls.append(command)
            return None

        import scripts.agent_profile as module

        original = module.shutil.which
        module.shutil.which = spy
        try:
            load(VALID)
        finally:
            module.shutil.which = original

        self.assertEqual(calls, [])

    def test_building_rejects_a_non_string_command_value(self) -> None:
        with self.assertRaises(AgentProfileError):
            load("version: 1\nprofiles:\n  p:\n    defaults:\n      worker: 7\n")

    def test_building_rejects_an_empty_role_mapping(self) -> None:
        with self.assertRaises(AgentProfileError):
            load("version: 1\nprofiles:\n  p:\n    defaults:\n")

    def test_a_string_version_is_rejected_not_coerced(self) -> None:
        with self.assertRaises(AgentProfileError):
            load('version: "1"\nprofiles:\n  p:\n    defaults:\n      worker: claude\n')

    def test_an_unsupported_version_is_explicit(self) -> None:
        with self.assertRaises(AgentProfileError):
            load("version: 2\nprofiles:\n  p:\n    defaults:\n      worker: claude\n")

    def test_malformed_yaml_raises_instead_of_parsing_partially(self) -> None:
        with self.assertRaises(AgentProfileError):
            parse_agent_profiles_document("version: 1\n\tprofiles:\n")

    def test_an_unknown_top_level_key_is_rejected(self) -> None:
        with self.assertRaises(AgentProfileError):
            load("version: 1\nprofile:\n  p:\n    defaults:\n      worker: claude\n")

    def test_an_unknown_profile_key_is_rejected(self) -> None:
        with self.assertRaises(AgentProfileError):
            load("version: 1\nprofiles:\n  p:\n    reviewers:\n      worker: claude\n")

    def test_an_unknown_role_key_is_rejected(self) -> None:
        with self.assertRaises(AgentProfileError):
            load("version: 1\nprofiles:\n  p:\n    defaults:\n      wroker: claude\n")

    def test_an_unknown_phase_key_is_rejected(self) -> None:
        with self.assertRaises(AgentProfileError):
            load(
                "version: 1\nprofiles:\n  p:\n    phases:\n"
                "      deploy:\n        worker: claude\n"
            )

    def test_final_review_accepts_only_reviewer(self) -> None:
        with self.assertRaises(AgentProfileError):
            load(
                "version: 1\nprofiles:\n  p:\n    final_review:\n        worker: claude\n"
            )

    def test_the_phase_key_set_is_shared_with_quality_profile(self) -> None:
        """One vocabulary for the seven phases, not two that can drift apart."""
        self.assertEqual(PHASE_KEYS, APPLICABLE_PHASES)


class SourcePrecedenceTests(unittest.TestCase):
    """Whole-definition precedence, and a home directory that is never the real one."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.project = root / "project"
        self.home = root / "home"
        (self.project / ".orca").mkdir(parents=True)
        (self.home / ".orca").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_project(self, text: str) -> None:
        (self.project / PROJECT_PROFILE_RELATIVE_PATH).write_text(text, encoding="utf-8")

    def write_user(self, text: str) -> None:
        (self.home / USER_PROFILE_RELATIVE_PATH).write_text(text, encoding="utf-8")

    def discover(self):
        return discover_agent_profiles(project_root=self.project, home=self.home)[0]

    def test_project_local_wins_over_user_global_for_the_same_name(self) -> None:
        self.write_project(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
        )
        self.write_user(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: codex\n"
        )

        profile = self.discover()["p"]

        self.assertEqual(profile.default_for(ROLE_WORKER), "claude")
        self.assertEqual(profile.source, SOURCE_PROJECT_LOCAL)

    def test_the_losing_definition_contributes_no_field(self) -> None:
        """No field-level merge: the project definition is taken whole."""
        self.write_project(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
        )
        self.write_user(
            "version: 1\nprofiles:\n  p:\n    defaults:\n"
            "      worker: codex\n      reviewer: codex\n"
        )

        profile = self.discover()["p"]

        self.assertEqual(profile.default_for(ROLE_REVIEWER), "")

    def test_a_name_only_in_user_global_is_available(self) -> None:
        self.write_project(
            "version: 1\nprofiles:\n  a:\n    defaults:\n      worker: claude\n"
        )
        self.write_user(
            "version: 1\nprofiles:\n  b:\n    defaults:\n      worker: codex\n"
        )

        profiles = self.discover()

        self.assertEqual(sorted(profiles), ["a", "b"])
        self.assertEqual(profiles["b"].source, SOURCE_USER_GLOBAL)

    def test_neither_source_present_finds_nothing(self) -> None:
        self.assertEqual(self.discover(), {})

    def test_a_malformed_user_global_file_is_invalid_not_ignored(self) -> None:
        self.write_user("version: 9\nprofiles:\n  p:\n    defaults:\n      worker: c\n")

        with self.assertRaises(AgentProfileError):
            self.discover()

    def test_a_directory_at_the_profile_path_is_invalid(self) -> None:
        (self.project / PROJECT_PROFILE_RELATIVE_PATH).mkdir()

        with self.assertRaises(AgentProfileError):
            self.discover()

    def test_home_is_injectable_and_never_the_real_home(self) -> None:
        """A developer's own ~/.orca must not decide what a test run sees."""
        self.write_user(
            "version: 1\nprofiles:\n  only_here:\n    defaults:\n      worker: claude\n"
        )

        self.assertIn("only_here", self.discover())
        self.assertNotIn(
            "only_here",
            discover_agent_profiles(project_root=self.project, home=self.project)[0],
        )


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.project = root / "project"
        self.home = root / "home"
        (self.project / ".orca").mkdir(parents=True)
        (self.home / ".orca").mkdir(parents=True)
        (self.project / PROJECT_PROFILE_RELATIVE_PATH).write_text(VALID, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def select(self, name):
        return select_agent_profile(name, project_root=self.project, home=self.home)

    def test_an_omitted_profile_reads_no_file_at_all(self) -> None:
        selection = select_agent_profile(None, project_root="/nonexistent", home="/nope")

        self.assertEqual(selection.status, SELECTION_OMITTED)
        self.assertIsNone(selection.profile)

    def test_a_selected_name_resolves(self) -> None:
        selection = self.select("diverse")

        self.assertEqual(selection.status, SELECTION_SELECTED)
        self.assertEqual(selection.profile.source, SOURCE_PROJECT_LOCAL)

    def test_a_malformed_user_global_file_does_not_block_a_valid_project_local_selection(
        self,
    ) -> None:
        """The review fix, verbatim: project-local `diverse` is valid (setUp), and
        a broken user-global file must not be able to fail that selection -- the
        selected profile is a self-contained resolution domain, and a
        lower-precedence source's condition is not part of that domain."""
        (self.home / USER_PROFILE_RELATIVE_PATH).write_text(
            "version: 9\nprofiles:\n  diverse:\n    defaults:\n      worker: c\n",
            encoding="utf-8",
        )

        selection = self.select("diverse")

        self.assertEqual(selection.status, SELECTION_SELECTED)
        self.assertEqual(selection.profile.source, SOURCE_PROJECT_LOCAL)
        self.assertEqual(selection.profile.default_for(ROLE_WORKER), "claude")

    def test_user_global_is_not_even_opened_when_project_local_has_the_name(
        self,
    ) -> None:
        """Stronger than the malformed-file case: a directory at the user-global
        path raises the instant anything tries to read it as a document (see
        SourcePrecedenceTests.test_a_directory_at_the_profile_path_is_invalid).
        Selecting a project-local name must not trip that at all."""
        (self.home / USER_PROFILE_RELATIVE_PATH).mkdir()

        selection = self.select("diverse")

        self.assertEqual(selection.status, SELECTION_SELECTED)
        self.assertEqual(selection.profile.source, SOURCE_PROJECT_LOCAL)

    def test_a_name_only_in_user_global_still_resolves_when_project_local_lacks_it(
        self,
    ) -> None:
        """The fallback half of the same precedence rule: project-local parses
        cleanly and simply does not have this name, so user-global is consulted
        and wins -- short-circuiting to project-local must not mean skipping
        user-global outright."""
        (self.home / USER_PROFILE_RELATIVE_PATH).write_text(
            "version: 1\nprofiles:\n  only_global:\n    defaults:\n      worker: codex\n",
            encoding="utf-8",
        )

        selection = self.select("only_global")

        self.assertEqual(selection.status, SELECTION_SELECTED)
        self.assertEqual(selection.profile.source, SOURCE_USER_GLOBAL)

    def test_a_malformed_user_global_file_is_reported_only_when_actually_needed(
        self,
    ) -> None:
        """The mirror image of the first regression above: once project-local is
        confirmed not to have the name, a malformed user-global file IS an error
        -- it is genuinely needed now, not irrelevant."""
        (self.home / USER_PROFILE_RELATIVE_PATH).write_text(
            "version: 9\nprofiles:\n  p:\n    defaults:\n      worker: c\n",
            encoding="utf-8",
        )

        selection = self.select("nosuch")

        self.assertEqual(selection.status, SELECTION_INVALID)
        self.assertEqual(selection.reason, REASON_INVALID_PROFILE)

    def test_an_empty_profile_value_is_unknown_not_omitted(self) -> None:
        selection = self.select("")

        self.assertEqual(selection.status, SELECTION_INVALID)
        self.assertEqual(selection.reason, REASON_UNKNOWN_PROFILE)

    def test_an_unknown_name_is_unknown_agent_profile(self) -> None:
        selection = self.select("nosuch")

        self.assertEqual(selection.status, SELECTION_INVALID)
        self.assertEqual(selection.reason, REASON_UNKNOWN_PROFILE)

    def test_a_malformed_document_is_invalid_agent_profile(self) -> None:
        (self.project / PROJECT_PROFILE_RELATIVE_PATH).write_text(
            "version: 3\nprofiles:\n  p:\n    defaults:\n      worker: claude\n",
            encoding="utf-8",
        )

        selection = self.select("p")

        self.assertEqual(selection.status, SELECTION_INVALID)
        self.assertEqual(selection.reason, REASON_INVALID_PROFILE)

    def test_selection_never_raises(self) -> None:
        (self.project / PROJECT_PROFILE_RELATIVE_PATH).write_text(
            "\tnot yaml\n", encoding="utf-8"
        )

        self.assertEqual(self.select("anything").status, SELECTION_INVALID)


class PhaseRolePrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load(VALID)["diverse"]

    def test_explicit_beats_phase_and_defaults(self) -> None:
        command, origin = resolve_phase_role(
            self.profile, "design", ROLE_WORKER, explicit="claude-glm"
        )

        self.assertEqual((command, origin), ("claude-glm", ORIGIN_EXPLICIT))

    def test_phase_beats_defaults(self) -> None:
        self.assertEqual(
            resolve_phase_role(self.profile, "implementation", ROLE_WORKER),
            ("codex", ORIGIN_PHASE),
        )

    def test_defaults_are_the_last_resort(self) -> None:
        self.assertEqual(
            resolve_phase_role(self.profile, "analysis", ROLE_WORKER),
            ("claude", ORIGIN_DEFAULTS),
        )

    def test_nothing_anywhere_is_unresolved_with_empty_origin(self) -> None:
        bare = load("version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n")["p"]

        self.assertEqual(
            resolve_phase_role(bare, "analysis", ROLE_REVIEWER),
            ("", ORIGIN_UNRESOLVED),
        )

    def test_a_selected_profile_never_borrows_from_another_profile(self) -> None:
        profiles = load(
            "version: 1\nprofiles:\n"
            "  rich:\n    defaults:\n      worker: claude\n      reviewer: codex\n"
            "  thin:\n    defaults:\n      worker: claude\n"
        )

        self.assertEqual(
            resolve_phase_role(profiles["thin"], "analysis", ROLE_REVIEWER),
            ("", ORIGIN_UNRESOLVED),
        )


class FinalReviewerPrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load(VALID)["diverse"]

    def test_final_review_reviewer_beats_explicit(self) -> None:
        command, _ = resolve_final_reviewer(self.profile, explicit_reviewer="claude-glm")

        self.assertEqual(command, "codex")

    def test_explicit_beats_defaults_when_final_review_is_absent(self) -> None:
        profile = load(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      reviewer: codex\n"
        )["p"]

        self.assertEqual(
            resolve_final_reviewer(profile, explicit_reviewer="claude-glm"),
            ("claude-glm", ORIGIN_EXPLICIT),
        )

    def test_defaults_are_the_last_resort(self) -> None:
        profile = load(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      reviewer: codex\n"
        )["p"]

        self.assertEqual(resolve_final_reviewer(profile), ("codex", ORIGIN_DEFAULTS))

    def test_the_two_chains_disagree_on_the_same_inputs(self) -> None:
        """The asymmetry is the requirement, so it gets a test of its own."""
        phase, _ = resolve_phase_role(
            self.profile, "design", ROLE_REVIEWER, explicit="claude-glm"
        )
        final, _ = resolve_final_reviewer(self.profile, explicit_reviewer="claude-glm")

        self.assertEqual(phase, "claude-glm")
        self.assertEqual(final, "codex")
        self.assertNotEqual(phase, final)


class RequiredRoleTests(unittest.TestCase):
    def test_orchestration_low_makes_phase_reviewer_optional(self) -> None:
        pairs = required_roles(
            runtime=RUNTIME_ORCHESTRATION, requested_phases=("analysis",), risk="low"
        )

        self.assertIn(("analysis", ROLE_WORKER), pairs)
        self.assertNotIn(("analysis", ROLE_REVIEWER), pairs)

    def test_orchestration_medium_and_high_require_phase_reviewer(self) -> None:
        for risk in ("medium", "high"):
            with self.subTest(risk=risk):
                pairs = required_roles(
                    runtime=RUNTIME_ORCHESTRATION,
                    requested_phases=("analysis",),
                    risk=risk,
                )
                self.assertIn(("analysis", ROLE_REVIEWER), pairs)

    def test_final_reviewer_is_required_at_every_risk_level(self) -> None:
        for risk in ("low", "medium", "high"):
            with self.subTest(risk=risk):
                pairs = required_roles(
                    runtime=RUNTIME_ORCHESTRATION,
                    requested_phases=("analysis",),
                    risk=risk,
                )
                self.assertIn(("final_review", ROLE_FINAL_REVIEWER), pairs)

    def test_loop_requires_phase_reviewer_and_has_no_final_reviewer(self) -> None:
        pairs = required_roles(
            runtime=RUNTIME_LOOP, requested_phases=("analysis",), risk=None
        )

        self.assertIn(("analysis", ROLE_REVIEWER), pairs)
        self.assertNotIn(("final_review", ROLE_FINAL_REVIEWER), pairs)


class MaterializationTests(unittest.TestCase):
    def routing(self, *, runtime=RUNTIME_ORCHESTRATION, phases=("analysis",), risk="high"):
        selection = select_agent_profile(
            "diverse", project_root=self.project, home=self.home
        )
        return materialize_run_routing(
            runtime=runtime,
            selection=selection,
            requested_phases=phases,
            risk=risk,
        )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.project = root / "project"
        self.home = root / "home"
        (self.project / ".orca").mkdir(parents=True)
        (self.home / ".orca").mkdir(parents=True)
        (self.project / PROJECT_PROFILE_RELATIVE_PATH).write_text(VALID, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_only_requested_phases_are_materialized(self) -> None:
        routing = self.routing(phases=("analysis",))

        phases = {entry.phase for entry in routing.entries}

        self.assertEqual(phases, {"analysis", "final_review"})

    def test_specialized_phases_materialize_the_same_way(self) -> None:
        routing = self.routing(phases=("bugfix",))

        self.assertEqual(routing.command_for("bugfix", ROLE_WORKER), "claude")

    def test_routing_is_frozen(self) -> None:
        routing = self.routing()

        with self.assertRaises(Exception):
            routing.profile_name = "other"  # type: ignore[misc]

    def test_editing_the_file_after_materialization_changes_nothing(self) -> None:
        routing = self.routing(phases=("implementation",))
        (self.project / PROJECT_PROFILE_RELATIVE_PATH).write_text(
            "version: 1\nprofiles:\n  diverse:\n    defaults:\n      worker: claude-glm\n",
            encoding="utf-8",
        )

        self.assertEqual(routing.command_for("implementation", ROLE_WORKER), "codex")

    def test_a_legacy_selection_produces_a_legacy_routing(self) -> None:
        routing = materialize_run_routing(
            runtime=RUNTIME_ORCHESTRATION,
            selection=select_agent_profile(None),
            requested_phases=("analysis",),
            risk="high",
            explicit_worker="claude",
            explicit_reviewer="codex",
        )

        self.assertTrue(routing.is_legacy)
        self.assertEqual(routing.evidence_rows(), ())


class RequiredCommandGateTests(unittest.TestCase):
    """The three gates, over required entries, in the legacy order."""

    def routing(self, text, *, risk="high", phases=("analysis",), runtime=RUNTIME_ORCHESTRATION):
        from scripts.agent_profile import AgentProfileSelection

        profiles = load(text)
        name = next(iter(profiles))
        return materialize_run_routing(
            runtime=runtime,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name=name, profile=profiles[name]
            ),
            requested_phases=phases,
            risk=risk,
        )

    def assert_blocked(self, routing, reason, *, which=always_found):
        with self.assertRaises(AgentProfileError) as caught:
            gate(routing, which=which)
        self.assertEqual(caught.exception.reason, reason)

    def test_a_required_command_with_a_space_is_invalid_agent_command(self) -> None:
        routing = self.routing(
            'version: 1\nprofiles:\n  p:\n    defaults:\n      worker: "my agent"\n'
            "      reviewer: codex\n"
        )

        self.assert_blocked(routing, REASON_INVALID_COMMAND)

    def test_a_required_bash_is_agent_not_allowed(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: bash\n"
            "      reviewer: codex\n"
        )

        self.assert_blocked(routing, REASON_COMMAND_NOT_ALLOWED)

    def test_a_required_command_missing_from_path_is_not_found(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
            "      reviewer: codex\n"
        )

        self.assert_blocked(
            routing, REASON_COMMAND_NOT_FOUND, which=found_except("claude")
        )

    def test_a_required_model_pinned_wrapper_is_allowed(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude-opus\n"
            "      reviewer: codex-sol\n"
        )

        gate(routing)

    def test_token_is_reported_before_allowlist_before_path(self) -> None:
        """Gate-major order, so a mixed failure reports what the legacy path would."""
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      reviewer: bash\n"
            "    phases:\n      analysis:\n"
            '        worker: "my agent"\n',
        )

        self.assert_blocked(routing, REASON_INVALID_COMMAND, which=never_found)

    def test_allowlist_is_reported_before_path(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: bash\n"
            "      reviewer: codex\n"
        )

        self.assert_blocked(routing, REASON_COMMAND_NOT_ALLOWED, which=never_found)

    def test_the_gate_target_set_is_exactly_required_entries(self) -> None:
        routing = self.routing(VALID, risk="low", phases=("design",))

        required = {(entry.phase, entry.role) for entry in routing.required_entries()}

        self.assertEqual(
            required, {("design", ROLE_WORKER), ("final_review", ROLE_FINAL_REVIEWER)}
        )


class WholeProfileCommandSafetyTests(unittest.TestCase):
    """validate_profile_command_safety(): token + allowlist over EVERY command the
    SELECTED PROFILE DECLARES -- defaults, every phase (requested or not),
    final_review -- plus any explicit worker=/reviewer= participating in this
    invocation. This is the review's corrected scope: the first cut of this fix
    only checked materialize_run_routing()'s output, which never contains a phase
    this invocation did not request, so `phases.refactoring.worker: bash` still
    slipped through whenever the request was `phases=analysis`. This gate
    validates the PROFILE DEFINITION directly and needs no requested-phase or
    risk information at all. PATH is deliberately absent from this gate;
    NonRequiredEntryTests below still proves an unused-but-safe command missing
    from PATH does not block anything once routing IS materialized, and that
    stays validate_routing_commands()'s job alone.
    """

    def profile(self, text):
        profiles = load(text)
        name = next(iter(profiles))
        return profiles[name]

    def assert_blocked(self, profile, reason, **kwargs) -> None:
        with self.assertRaises(AgentProfileError) as caught:
            gate_profile_safety(profile, **kwargs)
        self.assertEqual(caught.exception.reason, reason)

    def test_an_unused_optional_reviewer_of_bash_is_agent_not_allowed(self) -> None:
        """The review's own example: LOW leaves the phase Reviewer optional, and
        the required-only gate alone let `bash` sit there unvalidated."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: bash\n"
            "    final_review:\n      reviewer: codex\n"
        )

        self.assert_blocked(profile, REASON_COMMAND_NOT_ALLOWED)

    def test_an_unused_optional_reviewer_with_a_shell_fragment_is_invalid_agent_command(
        self,
    ) -> None:
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n"
            '      reviewer: "claude && rm -rf /"\n'
            "    final_review:\n      reviewer: codex\n"
        )

        self.assert_blocked(profile, REASON_INVALID_COMMAND)

    def test_an_unused_optional_reviewer_with_a_space_containing_value_is_invalid_agent_command(
        self,
    ) -> None:
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n"
            '      reviewer: "my agent"\n'
            "    final_review:\n      reviewer: codex\n"
        )

        self.assert_blocked(profile, REASON_INVALID_COMMAND)

    def test_an_unused_optional_reviewer_shaped_like_a_credential_is_not_allowed(
        self,
    ) -> None:
        """A raw secret-shaped string passes the token pattern (it is just
        alphanumeric-and-dashes) but is not on the allowlist and does not match
        the claude-/codex- wrapper pattern, so it is refused before it can ever
        reach `detail=command=...` in an evidence row."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n"
            "      reviewer: sk-ant-api03-not-a-real-key-0000000000\n"
            "    final_review:\n      reviewer: codex\n"
        )

        self.assert_blocked(profile, REASON_COMMAND_NOT_ALLOWED)

    def test_an_out_of_request_phase_declaration_is_now_checked_and_disallowed(
        self,
    ) -> None:
        """The exact re-review defect: `phases.refactoring.worker: bash` used to
        pass because materialize_run_routing() never materializes a phase this
        invocation did not request. This gate validates the profile DEFINITION,
        not materialized routing, so an out-of-request phase's command is checked
        regardless of what this particular invocation asked for."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    phases:\n      refactoring:\n        worker: bash\n"
        )

        self.assert_blocked(profile, REASON_COMMAND_NOT_ALLOWED)

    def test_an_out_of_request_phase_declaration_with_a_malformed_token_is_disallowed(
        self,
    ) -> None:
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            '    phases:\n      refactoring:\n        worker: "not a token"\n'
        )

        self.assert_blocked(profile, REASON_INVALID_COMMAND)

    def test_an_out_of_request_phase_declaration_that_is_valid_still_passes(
        self,
    ) -> None:
        """Positive control paired with the two negatives above: a genuinely
        allowlisted, well-formed command in an out-of-request phase is not
        refused merely for being declared there."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    phases:\n      refactoring:\n        worker: claude-glm\n"
        )

        gate_profile_safety(profile)  # must not raise

    def test_a_disallowed_final_review_reviewer_is_not_allowed(self) -> None:
        """final_review.reviewer is a known key for BOTH skills (loop just does
        not consume it for dispatch) -- it is still part of the profile
        definition and still owes static safety, independent of runtime."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    final_review:\n      reviewer: bash\n"
        )

        self.assert_blocked(profile, REASON_COMMAND_NOT_ALLOWED)

    def test_a_participating_explicit_reviewer_of_bash_is_not_allowed(self) -> None:
        """The other half of the re-review instruction: explicit worker=/
        reviewer= values that participate in a selected-profile invocation are
        in scope too, even though they are not part of the profile file itself."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
        )

        self.assert_blocked(
            profile, REASON_COMMAND_NOT_ALLOWED, explicit_reviewer="bash"
        )

    def test_a_non_participating_explicit_value_is_not_checked(self) -> None:
        """An explicit value that was never supplied for this invocation
        (empty string, the "not given" sentinel used throughout this module)
        contributes nothing to check and must not be treated as a declared bash
        by accident."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
        )

        gate_profile_safety(profile, explicit_reviewer="")  # must not raise

    def test_an_unused_but_safe_declaration_passes_with_no_which_call(self) -> None:
        """Positive control: a resolved, allowlisted, unused command anywhere in
        the profile is never blocked by this gate, and the gate needs no `which`
        to decide that -- proving PATH truly plays no part."""
        profile = self.profile(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: claude-glm\n"
            "    final_review:\n      reviewer: codex\n"
        )

        gate_profile_safety(profile)  # must not raise

    def test_the_safety_gate_target_set_is_the_whole_profile_not_any_routing(
        self,
    ) -> None:
        """Contrast with test_the_gate_target_set_is_exactly_required_entries in
        RequiredCommandGateTests: this gate never materializes routing at all, so
        its target set is every command VALID declares -- including
        `implementation`, a phase no test in this class ever requests."""
        profile = self.profile(VALID)

        gate_profile_safety(profile)  # design + implementation + final_review,
        # all allowlisted -- must not raise regardless of any requested phase.
        self.assertEqual(profile.phase_for("implementation", ROLE_WORKER), "codex")


class NonRequiredEntryTests(unittest.TestCase):
    """Negative half: a role this run will not dispatch cannot block the run."""

    def routing(self, text, *, risk="low", phases=("analysis",), runtime=RUNTIME_ORCHESTRATION):
        from scripts.agent_profile import AgentProfileSelection

        profiles = load(text)
        name = next(iter(profiles))
        return materialize_run_routing(
            runtime=runtime,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name=name, profile=profiles[name]
            ),
            requested_phases=phases,
            risk=risk,
        )

    def test_an_out_of_request_phase_command_may_be_malformed(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    phases:\n      refactoring:\n"
            '        worker: "not a token"\n'
        )

        gate(routing)

    def test_an_out_of_request_phase_command_may_be_disallowed(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    phases:\n      refactoring:\n        worker: bash\n"
        )

        gate(routing)

    def test_an_out_of_request_phase_command_may_be_missing_from_path(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    phases:\n      refactoring:\n        worker: claude-glm\n"
        )

        gate(routing, which=found_except("claude-glm"))

    def test_a_low_risk_optional_reviewer_may_be_disallowed(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: bash\n"
            "    final_review:\n      reviewer: codex\n"
        )

        gate(routing)

    def test_a_low_risk_optional_reviewer_may_be_missing_from_path(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: claude-gemma\n"
            "    final_review:\n      reviewer: codex\n"
        )

        gate(routing, which=found_except("claude-gemma"))

    def test_loop_ignores_a_disallowed_final_review_reviewer(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    final_review:\n      reviewer: bash\n",
            runtime=RUNTIME_LOOP,
            risk=None,
        )

        gate(routing)

    def test_loop_ignores_a_path_missing_final_review_reviewer(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    final_review:\n      reviewer: claude-glm\n",
            runtime=RUNTIME_LOOP,
            risk=None,
        )

        gate(routing, which=found_except("claude-glm"))

    def test_a_worker_only_profile_is_valid_at_low(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n"
            "    final_review:\n      reviewer: codex\n"
        )

        gate(routing)
        validate_required_roles(routing)


class RequiredPromotionTests(unittest.TestCase):
    """Positive half: the SAME value blocks once the role becomes required."""

    def routing(self, text, *, risk, phases, runtime=RUNTIME_ORCHESTRATION):
        from scripts.agent_profile import AgentProfileSelection

        profiles = load(text)
        name = next(iter(profiles))
        return materialize_run_routing(
            runtime=runtime,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name=name, profile=profiles[name]
            ),
            requested_phases=phases,
            risk=risk,
        )

    DISALLOWED_REVIEWER = (
        "version: 1\nprofiles:\n  p:\n"
        "    defaults:\n      worker: claude\n      reviewer: bash\n"
        "    final_review:\n      reviewer: codex\n"
    )
    MALFORMED_REFACTORING = (
        "version: 1\nprofiles:\n  p:\n"
        "    defaults:\n      worker: claude\n      reviewer: codex\n"
        "    phases:\n      refactoring:\n"
        '        worker: "not a token"\n'
    )

    def test_the_same_malformed_value_blocks_when_the_phase_is_requested(self) -> None:
        routing = self.routing(
            self.MALFORMED_REFACTORING, risk="low", phases=("refactoring",)
        )

        with self.assertRaises(AgentProfileError) as caught:
            gate(routing)
        self.assertEqual(caught.exception.reason, REASON_INVALID_COMMAND)

    def test_the_same_disallowed_reviewer_blocks_at_medium(self) -> None:
        routing = self.routing(
            self.DISALLOWED_REVIEWER, risk="medium", phases=("analysis",)
        )

        with self.assertRaises(AgentProfileError) as caught:
            gate(routing)
        self.assertEqual(caught.exception.reason, REASON_COMMAND_NOT_ALLOWED)

    def test_the_same_path_missing_reviewer_blocks_at_medium(self) -> None:
        text = (
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: claude-gemma\n"
            "    final_review:\n      reviewer: codex\n"
        )
        gate(
            self.routing(text, risk="low", phases=("analysis",)),
            which=found_except("claude-gemma"),
        )

        with self.assertRaises(AgentProfileError) as caught:
            gate(
                self.routing(text, risk="medium", phases=("analysis",)),
                which=found_except("claude-gemma"),
            )
        self.assertEqual(caught.exception.reason, REASON_COMMAND_NOT_FOUND)

    def test_the_same_disallowed_reviewer_blocks_for_loop_at_every_risk(self) -> None:
        routing = self.routing(
            self.DISALLOWED_REVIEWER, risk=None, phases=("analysis",), runtime=RUNTIME_LOOP
        )

        with self.assertRaises(AgentProfileError) as caught:
            gate(routing)
        self.assertEqual(caught.exception.reason, REASON_COMMAND_NOT_ALLOWED)

    def test_a_worker_only_profile_is_unresolved_at_medium(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n"
            "    final_review:\n      reviewer: codex\n",
            risk="medium",
            phases=("analysis",),
        )

        with self.assertRaises(AgentProfileError) as caught:
            validate_required_roles(routing)
        self.assertEqual(caught.exception.reason, REASON_ROLE_UNRESOLVED)

    def test_a_worker_only_profile_is_never_valid_for_loop(self) -> None:
        routing = self.routing(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n",
            risk=None,
            phases=("analysis",),
            runtime=RUNTIME_LOOP,
        )

        with self.assertRaises(AgentProfileError) as caught:
            validate_required_roles(routing)
        self.assertEqual(caught.exception.reason, REASON_ROLE_UNRESOLVED)


class EvidenceTests(unittest.TestCase):
    def routing(self, *, risk="low", runtime=RUNTIME_ORCHESTRATION):
        from scripts.agent_profile import AgentProfileSelection

        profiles = load(
            "version: 1\nprofiles:\n  p:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    final_review:\n      reviewer: codex\n"
        )
        return materialize_run_routing(
            runtime=runtime,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name="p", profile=profiles["p"]
            ),
            requested_phases=("analysis",),
            risk=risk,
        )

    def test_evidence_rows_carry_name_source_phases_commands_and_origins(self) -> None:
        rows = self.routing().evidence_rows()

        header = rows[0]
        self.assertEqual(header["event"], "agent_profile_selected")
        self.assertIn("profile=p", header["detail"])
        self.assertEqual(header["requested_phases"], "analysis")
        self.assertTrue(
            any("command=claude origin=defaults" in row["detail"] for row in rows[1:])
        )

    def test_evidence_covers_every_entry_not_only_required(self) -> None:
        routing = self.routing(risk="low")
        rows = routing.evidence_rows()

        recorded = {(row["phase"], row["role"]) for row in rows[1:]}

        self.assertEqual(recorded, {(e.phase, e.role) for e in routing.entries})

    def test_a_low_risk_optional_reviewer_is_recorded_with_command_and_origin(self) -> None:
        rows = self.routing(risk="low").evidence_rows()

        reviewer = next(row for row in rows if row["role"] == ROLE_REVIEWER)

        self.assertEqual(reviewer["result"], RESULT_OPTIONAL)
        self.assertIn("command=codex", reviewer["detail"])
        self.assertIn("origin=defaults", reviewer["detail"])

    def test_each_row_marks_required_or_optional(self) -> None:
        rows = self.routing(risk="low").evidence_rows()[1:]

        self.assertTrue(
            all(row["result"] in (RESULT_REQUIRED, RESULT_OPTIONAL) for row in rows)
        )

    def test_an_unresolved_optional_entry_still_leaves_a_row(self) -> None:
        from scripts.agent_profile import AgentProfileSelection

        profiles = load(
            "version: 1\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
            "    final_review:\n      reviewer: codex\n"
        )
        routing = materialize_run_routing(
            runtime=RUNTIME_ORCHESTRATION,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name="p", profile=profiles["p"]
            ),
            requested_phases=("analysis",),
            risk="low",
        )

        reviewer = next(
            row for row in routing.evidence_rows() if row["role"] == ROLE_REVIEWER
        )

        self.assertEqual(reviewer["result"], RESULT_OPTIONAL)
        self.assertIn("command=none", reviewer["detail"])

    def test_evidence_uses_only_existing_log_columns(self) -> None:
        """No ORCHESTRATOR_LOG schema change, which is what keeps tools/ untouched."""
        from scripts.run_logging import ORCHESTRATOR_LOG_COLUMNS

        for row in self.routing().evidence_rows():
            self.assertTrue(set(row).issubset(set(ORCHESTRATOR_LOG_COLUMNS)))

    def test_evidence_contains_no_environment_value(self) -> None:
        import os

        rendered = " ".join(
            value for row in self.routing().evidence_rows() for value in row.values()
        )

        for name in ("PATH", "HOME"):
            value = os.environ.get(name)
            if value:
                self.assertNotIn(value, rendered)


class ReuseIdentityTests(unittest.TestCase):
    """OS-4's addition to the session-reuse gate: the RESOLVED command is compared.

    Exercises the harness accessor that decides what condition 2 of the eight-condition
    reuse gate sees. No new condition is added -- `same_agent_command` already existed;
    what changes is which string it is given.
    """

    def harness(self):
        from scripts.orca_runtime_harness import OrcaRuntimeHarness

        return OrcaRuntimeHarness.__new__(OrcaRuntimeHarness)

    def routing(self):
        from scripts.agent_profile import AgentProfileSelection

        profiles = load(VALID)
        return materialize_run_routing(
            runtime=RUNTIME_ORCHESTRATION,
            selection=AgentProfileSelection(
                status=SELECTION_SELECTED, name="diverse", profile=profiles["diverse"]
            ),
            requested_phases=("design", "implementation"),
            risk="high",
        )

    def test_without_a_routing_the_accessor_is_empty_so_callers_fall_back(self) -> None:
        harness = self.harness()
        harness.agent_routing = None

        self.assertEqual(harness.resolved_agent_command("worker", "design"), "")

    def test_the_resolved_command_is_per_phase(self) -> None:
        harness = self.harness()
        harness.agent_routing = self.routing()

        self.assertEqual(harness.resolved_agent_command("worker", "design"), "claude")
        self.assertEqual(
            harness.resolved_agent_command("worker", "implementation"), "codex"
        )

    def test_the_same_role_across_phases_can_change_identity(self) -> None:
        """Which is exactly when reuse must stop: same role, different agent."""
        harness = self.harness()
        harness.agent_routing = self.routing()

        self.assertNotEqual(
            harness.resolved_agent_command("reviewer", "design"),
            harness.resolved_agent_command("reviewer", "implementation"),
        )

    def test_the_final_reviewer_reads_its_own_slot(self) -> None:
        harness = self.harness()
        harness.agent_routing = self.routing()

        self.assertEqual(harness.resolved_agent_command("final_reviewer", ""), "codex")


if __name__ == "__main__":
    unittest.main()
