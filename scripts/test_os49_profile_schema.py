#!/usr/bin/env python3
"""OS-49: the model-aware Agent Profile schema.

Positive cases prove `version: 1` still means exactly what it meant, and that
`version: 2` can carry a model in each of the five role-value positions. Negative cases
are one per documented rejection, because "unknown schema, unknown fields and malformed
model declarations fail closed" is three separate claims and each needs its own name.
"""
from __future__ import annotations

import re
import textwrap
import unittest
from pathlib import Path

from scripts.agent_profile import (
    MODEL_AWARE_SCHEMA_VERSION,
    MODEL_TOKEN_PATTERN,
    REASON_INVALID_MODEL,
    REASON_INVALID_PROFILE,
    ROLE_VALUE_KEYS,
    SOURCE_PROJECT_LOCAL,
    SOURCE_USER_GLOBAL,
    SUPPORTED_SCHEMA_VERSIONS,
    AgentProfileError,
    AgentProfileSelection,
    RoleValue,
    SELECTION_SELECTED,
    load_agent_profiles_text,
    materialize_run_routing,
    select_agent_profile,
    validate_profile_command_safety,
)
from scripts.skill_policy import AGENT_COMMAND_PATTERN

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWN_COMMANDS = ("claude", "codex", "claude-glm", "claude-gemma")
CUSTOM_PATTERN = re.compile(r"(?:claude|codex)-[A-Za-z0-9._-]+", re.ASCII)

V1 = textwrap.dedent(
    """\
    version: 1
    profiles:
      distinct:
        defaults:
          worker: claude
          reviewer: codex
        phases:
          design:
            worker: claude
            reviewer: codex
        final_review:
          reviewer: codex
    """
)

V2 = textwrap.dedent(
    """\
    version: 2
    profiles:
      glm-split:
        defaults:
          worker:
            command: claude
            model: glm-5.2
          reviewer:
            command: claude
            model: glm-5.3-flash
        phases:
          design:
            worker:
              command: claude
              model: glm-5.3-flash
            reviewer: codex
        final_review:
          reviewer:
            command: claude
            model: glm-5.2
    """
)


def load(text: str, *, source: str = SOURCE_PROJECT_LOCAL):
    return dict(load_agent_profiles_text(text, path="test.yaml", source=source))


def routing_for(text: str, name: str, *, phases=("design",), risk="high",
                runtime="orchestration"):
    profile = load(text)[name]
    selection = AgentProfileSelection(
        status=SELECTION_SELECTED, name=name, profile=profile
    )
    return materialize_run_routing(
        runtime=runtime, selection=selection, requested_phases=phases, risk=risk
    )


class RetainedV1CompatibilityTests(unittest.TestCase):
    def test_version_one_is_still_supported(self) -> None:
        self.assertIn(1, SUPPORTED_SCHEMA_VERSIONS)
        self.assertEqual(SUPPORTED_SCHEMA_VERSIONS, (1, 2))

    def test_v1_distinct_command_profile_parses_unchanged(self) -> None:
        profile = load(V1)["distinct"]
        self.assertEqual(profile.schema_version, 1)
        self.assertEqual(profile.default_for("worker"), "claude")
        self.assertEqual(profile.default_for("reviewer"), "codex")
        self.assertEqual(profile.phase_for("design", "worker"), "claude")
        self.assertEqual(profile.final_reviewer(), "codex")

    def test_v1_carries_no_model_anywhere(self) -> None:
        profile = load(V1)["distinct"]
        for value in (
            profile.default_value_for("worker"),
            profile.default_value_for("reviewer"),
            profile.phase_value_for("design", "worker"),
            profile.final_reviewer_value(),
        ):
            self.assertIsNotNone(value)
            self.assertEqual(value.model, "")

    def test_v1_routing_is_not_model_aware(self) -> None:
        routing = routing_for(V1, "distinct")
        self.assertFalse(routing.is_model_aware)
        self.assertEqual(routing.schema_version, 1)
        for entry in routing.entries:
            self.assertEqual(entry.model, "")

    def test_a_v1_document_cannot_reach_the_mapping_branch(self) -> None:
        """The obligation adding a second schema version incurs, asserted mechanically.

        Every role-value shape the v2 branch accepts, written into a v1 document, must be
        refused BY VERSION -- so v2-only behaviour is unreachable from v1 rather than
        merely undocumented there.
        """
        shapes = (
            "      worker:\n        command: claude\n        model: glm-5.2\n",
            "      worker:\n        command: claude\n",
            "      reviewer:\n        command: codex\n        model: gpt-5.6\n",
        )
        for shape in shapes:
            with self.subTest(shape=shape):
                with self.assertRaises(AgentProfileError) as caught:
                    load(f"version: 1\nprofiles:\n  p:\n    defaults:\n{shape}")
                self.assertEqual(caught.exception.reason, REASON_INVALID_PROFILE)
                self.assertIn(
                    f"requires schema version {MODEL_AWARE_SCHEMA_VERSION}",
                    str(caught.exception),
                )

    def test_a_model_less_v2_document_is_indistinguishable_from_v1(self) -> None:
        v2_plain = V1.replace("version: 1", "version: 2")
        plain = routing_for(v2_plain, "distinct")
        self.assertFalse(plain.is_model_aware)
        self.assertEqual(
            [(e.phase, e.role, e.command, e.model, e.origin, e.required)
             for e in plain.entries],
            [(e.phase, e.role, e.command, e.model, e.origin, e.required)
             for e in routing_for(V1, "distinct").entries],
        )


class ModelAwareParsingTests(unittest.TestCase):
    def test_model_aware_role_value_parses(self) -> None:
        profile = load(V2)["glm-split"]
        self.assertEqual(profile.schema_version, 2)
        self.assertEqual(
            profile.default_value_for("worker"),
            RoleValue(command="claude", model="glm-5.2"),
        )
        self.assertEqual(
            profile.default_value_for("reviewer"),
            RoleValue(command="claude", model="glm-5.3-flash"),
        )

    def test_the_command_accessors_still_return_commands(self) -> None:
        profile = load(V2)["glm-split"]
        self.assertEqual(profile.default_for("worker"), "claude")
        self.assertEqual(profile.phase_for("design", "worker"), "claude")
        self.assertEqual(profile.final_reviewer(), "claude")

    def test_routing_carries_model_per_role(self) -> None:
        routing = routing_for(V2, "glm-split")
        self.assertTrue(routing.is_model_aware)
        self.assertEqual(routing.schema_version, 2)
        self.assertEqual(routing.effective_identity("design", "worker"),
                         ("claude", "glm-5.3-flash"))
        self.assertEqual(routing.model_for("design", "worker"), "glm-5.3-flash")

    def test_phase_specific_model_routing(self) -> None:
        """A phase override's model wins over the default's, for the same role."""
        routing = routing_for(V2, "glm-split")
        self.assertEqual(routing.model_for("design", "worker"), "glm-5.3-flash")
        other = routing_for(V2, "glm-split", phases=("plan",))
        self.assertEqual(other.model_for("plan", "worker"), "glm-5.2")

    def test_final_reviewer_model_routing(self) -> None:
        routing = routing_for(V2, "glm-split")
        self.assertEqual(
            routing.effective_identity("final_review", "final_reviewer"),
            ("claude", "glm-5.2"),
        )

    def test_defaults_fallback_carries_model(self) -> None:
        routing = routing_for(V2, "glm-split", phases=("plan",))
        self.assertEqual(routing.effective_identity("plan", "reviewer"),
                         ("claude", "glm-5.3-flash"))

    def test_mixed_string_and_mapping_role_values_in_one_v2_document(self) -> None:
        routing = routing_for(V2, "glm-split")
        self.assertEqual(routing.effective_identity("design", "reviewer"),
                         ("codex", ""))

    def test_final_reviewer_precedence_is_unchanged(self) -> None:
        """final_review.reviewer > explicit > defaults.reviewer, models included."""
        doc = textwrap.dedent(
            """\
            version: 2
            profiles:
              p:
                defaults:
                  worker: claude
                  reviewer:
                    command: codex
                    model: gpt-5.6
                final_review:
                  reviewer:
                    command: claude
                    model: glm-5.2
            """
        )
        routing = routing_for(doc, "p", phases=("design",))
        self.assertEqual(
            routing.effective_identity("final_review", "final_reviewer"),
            ("claude", "glm-5.2"),
        )
        without_final = textwrap.dedent(
            """\
            version: 2
            profiles:
              p:
                defaults:
                  worker: claude
                  reviewer:
                    command: codex
                    model: gpt-5.6
            """
        )
        no_final = routing_for(without_final, "p", phases=("design",))
        self.assertEqual(
            no_final.effective_identity("final_review", "final_reviewer"),
            ("codex", "gpt-5.6"),
        )

    def test_a_model_never_makes_a_role_resolved(self) -> None:
        """RoleRouting.resolved keeps its pre-OS-49 meaning, so the required-role gate
        reports a missing ROLE rather than a missing model."""
        routing = routing_for(V2, "glm-split")
        for entry in routing.entries:
            self.assertEqual(entry.resolved, bool(entry.command))

    def test_required_identities_dedupes_on_the_pair(self) -> None:
        routing = routing_for(V2, "glm-split")
        identities = routing.required_identities()
        self.assertEqual(len(identities), len(set(identities)))
        self.assertIn(("claude", "glm-5.3-flash"), identities)
        self.assertIn(("codex", ""), identities)


class SelfContainedRoutingDomainTests(unittest.TestCase):
    """OS-49 must not change the two-source precedence or the whole-definition merge."""

    def _write(self, root: Path, text: str) -> None:
        (root / ".orca").mkdir(parents=True, exist_ok=True)
        (root / ".orca" / "agent-profiles.yaml").write_text(text, encoding="utf-8")

    def test_project_local_beats_user_global_for_a_model_bearing_profile(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            home = Path(tmp) / "home"
            self._write(project, V2)
            self._write(home, V2.replace("glm-5.2", "glm-9.9"))
            selection = select_agent_profile(
                "glm-split", project_root=project, home=home
            )
            self.assertTrue(selection.is_selected)
            self.assertEqual(selection.profile.source, SOURCE_PROJECT_LOCAL)
            self.assertEqual(
                selection.profile.default_value_for("worker").model, "glm-5.2"
            )

    def test_user_global_is_used_only_when_project_local_lacks_the_name(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            home = Path(tmp) / "home"
            self._write(project, V1)
            self._write(home, V2)
            selection = select_agent_profile(
                "glm-split", project_root=project, home=home
            )
            self.assertTrue(selection.is_selected)
            self.assertEqual(selection.profile.source, SOURCE_USER_GLOBAL)
            self.assertEqual(selection.profile.schema_version, 2)


class FailClosedSchemaTests(unittest.TestCase):
    def _refused(self, text: str, *, reason: str = REASON_INVALID_PROFILE):
        with self.assertRaises(AgentProfileError) as caught:
            load(text)
        self.assertEqual(caught.exception.reason, reason)
        return str(caught.exception)

    def test_unknown_schema_version_fails_closed(self) -> None:
        message = self._refused(
            "version: 3\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
        )
        self.assertIn("unsupported schema version 3", message)
        self.assertIn("supported: 1, 2", message)
        for bad in ('"2"', "2.0", "true", "0"):
            with self.subTest(version=bad):
                self._refused(
                    f"version: {bad}\nprofiles:\n  p:\n"
                    "    defaults:\n      worker: claude\n"
                )

    def test_unknown_key_at_each_level_fails_closed(self) -> None:
        cases = {
            "document": "version: 2\nmodels:\n  a: b\nprofiles:\n  p:\n"
                        "    defaults:\n      worker: claude\n",
            "profile": "version: 2\nprofiles:\n  p:\n    models:\n      a: b\n",
            "defaults": "version: 2\nprofiles:\n  p:\n    defaults:\n"
                        "      worker: claude\n      models: x\n",
            "phases": "version: 2\nprofiles:\n  p:\n    phases:\n"
                      "      nosuchphase:\n        worker: claude\n",
            "final_review": "version: 2\nprofiles:\n  p:\n    final_review:\n"
                            "      worker: claude\n",
        }
        for level, text in cases.items():
            with self.subTest(level=level):
                self.assertIn("unknown key", self._refused(text))

    def test_unknown_key_inside_role_mapping_fails_closed(self) -> None:
        message = self._refused(
            "version: 2\nprofiles:\n  p:\n    phases:\n      design:\n"
            "        worker:\n          command: claude\n          effort: high\n"
        )
        self.assertIn("unknown key(s) effort", message)
        self.assertIn(", ".join(ROLE_VALUE_KEYS), message)

    def test_mapping_role_value_in_v1_fails_closed(self) -> None:
        message = self._refused(
            "version: 1\nprofiles:\n  p:\n    phases:\n      design:\n"
            "        worker:\n          command: claude\n          model: glm-5.2\n"
        )
        self.assertIn("requires schema version 2", message)
        self.assertIn("declares version 1", message)

    def test_model_without_command_fails_closed(self) -> None:
        message = self._refused(
            "version: 2\nprofiles:\n  p:\n    defaults:\n"
            "      worker:\n        model: glm-5.2\n"
        )
        self.assertIn("must declare command", message)

    def test_malformed_model_type_fails_closed(self) -> None:
        """Every non-string / empty `model` is refused as INVALID_AGENT_PROFILE.

        An EMPTY value is caught one layer earlier, by the restricted YAML reader itself
        ("key has no value and no nested block"), and a numeric or list value by the role
        reader. Both are the same verdict for the caller -- the reason code -- so that is
        what this binds to rather than one layer's wording.
        """
        for bad, expected in (
            ("", "no value"),
            ("7", "must be a non-empty string"),
            ("[]", "must be a non-empty string"),
        ):
            with self.subTest(model=bad):
                message = self._refused(
                    "version: 2\nprofiles:\n  p:\n    defaults:\n"
                    f"      worker:\n        command: claude\n        model: {bad}\n"
                )
                self.assertIn(expected, message)

    def test_inline_flow_mapping_fails_closed(self) -> None:
        """The restricted reader turns an inline mapping into a STRING, so without this
        branch the message would blame the COMMAND for a limitation about MAPPINGS -- and
        an operator's cheapest reading of that is "delete the model", a silently
        model-less run. Fails closed either way; this only names the real cause."""
        message = self._refused(
            "version: 2\nprofiles:\n  p:\n    defaults:\n"
            "      worker: {command: claude, model: glm-5.2}\n"
        )
        self.assertIn("indented block, not inline", message)

    def test_inline_flow_mapping_also_fails_closed_at_v1(self) -> None:
        self.assertIn(
            "indented block, not inline",
            self._refused(
                "version: 1\nprofiles:\n  p:\n    defaults:\n"
                "      worker: {command: claude, model: glm-5.2}\n"
            ),
        )

    def test_malformed_model_token_fails_closed(self) -> None:
        bad_tokens = (
            "../glm", "glm 5.2", "-glm", "glm;rm", "$(x)", "<synthetic>",
            "glm/5.2", "a|b",
        )
        for token in bad_tokens:
            with self.subTest(token=token):
                profile = load(
                    "version: 2\nprofiles:\n  p:\n    defaults:\n"
                    f"      worker:\n        command: claude\n        model: {token}\n"
                )["p"]
                with self.assertRaises(AgentProfileError) as caught:
                    validate_profile_command_safety(
                        profile,
                        token_pattern=AGENT_COMMAND_PATTERN,
                        known_commands=KNOWN_COMMANDS,
                        custom_command_pattern=CUSTOM_PATTERN,
                    )
                self.assertEqual(caught.exception.reason, REASON_INVALID_MODEL)
                self.assertIn("is not a simple model token", str(caught.exception))

    def test_the_model_pattern_is_not_the_command_pattern(self) -> None:
        """A separate pattern, so no code path can match a model with the command
        pattern or substitute one for the other."""
        self.assertIsNot(MODEL_TOKEN_PATTERN, AGENT_COMMAND_PATTERN)
        self.assertNotEqual(MODEL_TOKEN_PATTERN.pattern, AGENT_COMMAND_PATTERN.pattern)
        # The model pattern admits `+`, which the command pattern does not; the command
        # pattern admits a leading `-` and `.`, which the model pattern refuses.
        self.assertTrue(MODEL_TOKEN_PATTERN.fullmatch("glm-5.3-flash"))
        self.assertTrue(MODEL_TOKEN_PATTERN.fullmatch("claude-opus-5"))
        self.assertTrue(MODEL_TOKEN_PATTERN.fullmatch("gpt-5.6-sol"))
        self.assertIsNone(MODEL_TOKEN_PATTERN.fullmatch("-glm"))
        self.assertIsNone(MODEL_TOKEN_PATTERN.fullmatch("<synthetic>"))

    def test_the_whole_definition_is_checked_not_only_required_entries(self) -> None:
        """A `model: $(x)` in a phase this invocation never requested is still a trust
        question -- the same rule the command's safety gate already follows."""
        profile = load(
            "version: 2\nprofiles:\n  p:\n    defaults:\n      worker: claude\n"
            "      reviewer: codex\n    phases:\n      refactoring:\n"
            "        worker:\n          command: claude\n          model: $(x)\n"
        )["p"]
        with self.assertRaises(AgentProfileError) as caught:
            validate_profile_command_safety(
                profile,
                token_pattern=AGENT_COMMAND_PATTERN,
                known_commands=KNOWN_COMMANDS,
                custom_command_pattern=CUSTOM_PATTERN,
            )
        self.assertEqual(caught.exception.reason, REASON_INVALID_MODEL)
        self.assertIn("phases.refactoring.worker.model", str(caught.exception))

    def test_a_valid_model_passes_the_safety_gate(self) -> None:
        validate_profile_command_safety(
            load(V2)["glm-split"],
            token_pattern=AGENT_COMMAND_PATTERN,
            known_commands=KNOWN_COMMANDS,
            custom_command_pattern=CUSTOM_PATTERN,
        )


if __name__ == "__main__":
    unittest.main()
