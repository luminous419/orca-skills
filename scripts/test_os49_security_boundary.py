#!/usr/bin/env python3
"""OS-49: the command trust boundary did not move.

Six properties, one test group each. Each is a REGRESSION assertion: the point is not that
OS-49 added a protection but that it added a second identity axis without weakening the
one that already existed.
"""
from __future__ import annotations

import re
import shlex
import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from scripts import decision_gate, run_logging
from scripts.agent_profile import (
    MODEL_TOKEN_PATTERN,
    REASON_COMMAND_NOT_ALLOWED,
    REASON_INVALID_COMMAND,
    REASON_INVALID_MODEL,
    AgentProfileError,
    load_agent_profiles_text,
    validate_profile_command_safety,
)
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.orca_runtime_harness import OrcaRuntimeHarness
from scripts.skill_policy import AGENT_COMMAND_PATTERN
from scripts.test_orca_runtime_contract import RecordingExec
from scripts.test_os49_delivery_barrier import SPLIT_PROFILE, routing_from

REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWN = ("claude", "codex", "claude-glm", "claude-gemma")
CUSTOM = re.compile(r"(?:claude|codex)-[A-Za-z0-9._-]+", re.ASCII)


def load(text: str):
    return dict(load_agent_profiles_text(text, path="t.yaml", source="project_local"))


def profile_with(command: str, model: str = "") -> str:
    lines = [
        "version: 2", "profiles:", "  p:", "    defaults:",
    ]
    if model:
        lines += [f"      worker:", f"        command: {command}",
                  f"        model: {model}"]
    else:
        lines += [f"      worker: {command}"]
    lines += ["      reviewer: codex", ""]
    return "\n".join(lines)


class SimplePathResolvedTokensOnlyTests(unittest.TestCase):
    """Property 1: a command is still a simple PATH-resolved executable token."""

    def test_the_command_pattern_is_unchanged(self) -> None:
        self.assertEqual(AGENT_COMMAND_PATTERN.pattern, "[A-Za-z0-9._-]+")

    def test_a_non_token_command_is_still_refused_with_its_own_reason(self) -> None:
        for command in ("../claude", "my agent", "claude;rm", "$(x)", "/usr/bin/claude"):
            with self.subTest(command=command):
                with self.assertRaises(AgentProfileError) as caught:
                    validate_profile_command_safety(
                        load(profile_with(command))["p"],
                        token_pattern=AGENT_COMMAND_PATTERN,
                        known_commands=KNOWN,
                        custom_command_pattern=CUSTOM,
                    )
                self.assertEqual(caught.exception.reason, REASON_INVALID_COMMAND)

    def test_an_unallowlisted_command_is_still_refused(self) -> None:
        with self.assertRaises(AgentProfileError) as caught:
            validate_profile_command_safety(
                load(profile_with("bash"))["p"],
                token_pattern=AGENT_COMMAND_PATTERN,
                known_commands=KNOWN,
                custom_command_pattern=CUSTOM,
            )
        self.assertEqual(caught.exception.reason, REASON_COMMAND_NOT_ALLOWED)

    def test_a_declared_model_does_not_let_an_unsafe_command_through(self) -> None:
        """The command gate runs FIRST and independently: adding a model to a bad command
        does not change its verdict."""
        with self.assertRaises(AgentProfileError) as caught:
            validate_profile_command_safety(
                load(profile_with("bash", "glm-5.2"))["p"],
                token_pattern=AGENT_COMMAND_PATTERN,
                known_commands=KNOWN,
                custom_command_pattern=CUSTOM,
            )
        self.assertEqual(caught.exception.reason, REASON_COMMAND_NOT_ALLOWED)


class NoShellFragmentOrInterpreterFallbackTests(unittest.TestCase):
    """Property 2: nothing new is executed, spawned, or composed into a command line."""

    def test_the_model_never_reaches_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RecordingExec()
            with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
                harness = OrcaRuntimeHarness(
                    Path(tmp),
                    agent_routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=InProcessModelDriver(),
                )
            harness._exec_orca = recorder
            harness.run_owner, harness.run_id = "term_owner", "run_sec"
            harness.create_fake_terminal(
                "worker", "complete", iteration=1, phase="implementation"
            )
            for command in recorder.commands:
                with self.subTest(command=command[:3]):
                    self.assertNotIn("glm-5.2", " ".join(command))
                    self.assertNotIn("--model", command)

    def test_the_terminal_create_command_is_the_bare_resolved_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RecordingExec()
            with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
                harness = OrcaRuntimeHarness(
                    Path(tmp),
                    agent_routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=InProcessModelDriver(),
                )
            harness._exec_orca = recorder
            harness.run_owner, harness.run_id = "term_owner", "run_sec"
            harness.create_fake_terminal(
                "worker", "complete", iteration=1, phase="implementation"
            )
            created = next(c for c in recorder.commands if c[1] == "create")
            value = created[created.index("--command") + 1]
            self.assertEqual(value, "claude")
            self.assertEqual(shlex.split(value), ["claude"])


class NoUnsafeConstructionFromModelNamesTests(unittest.TestCase):
    """Property 3: the model is a separate ledger field, never concatenated."""

    def test_the_model_is_a_separate_ledger_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = RecordingExec()
            with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
                harness = OrcaRuntimeHarness(
                    Path(tmp),
                    agent_routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=InProcessModelDriver(),
                )
            harness._exec_orca = recorder
            harness.run_owner, harness.run_id = "term_owner", "run_sec"
            handle = harness.create_fake_terminal(
                "worker", "complete", iteration=1, phase="implementation"
            )
            row = harness.ledger_terminal(handle)
            self.assertEqual(row["agent_command"], "claude")
            self.assertEqual(row["requested_model"], "glm-5.2")
            self.assertNotIn("glm", row["agent_command"])

    def test_the_reuse_gate_key_still_compares_the_command_alone(self) -> None:
        """Smuggling a model into `agent_command` would silently change condition 2's key,
        so one executable's two models would look like two executables to a gate whose job
        is to tell them apart on a SECOND axis."""
        source = (REPO_ROOT / "scripts" / "orca_runtime_harness.py").read_text("utf-8")
        start = source.index("# ---- 2. same agent command")
        block = source[start:source.index("# ---- 3.", start)]
        self.assertIn('row["agent_command"] != agent_command', block)
        self.assertNotIn("model", block)

    def test_the_two_patterns_are_never_interchanged(self) -> None:
        self.assertNotEqual(
            MODEL_TOKEN_PATTERN.pattern, AGENT_COMMAND_PATTERN.pattern
        )
        # A command token that is NOT a valid model token, and vice versa: the two
        # vocabularies genuinely differ, so substituting one for the other is detectable.
        self.assertTrue(AGENT_COMMAND_PATTERN.fullmatch("-claude"))
        self.assertIsNone(MODEL_TOKEN_PATTERN.fullmatch("-claude"))
        self.assertTrue(MODEL_TOKEN_PATTERN.fullmatch("a+b"))
        self.assertIsNone(AGENT_COMMAND_PATTERN.fullmatch("a+b"))


class MalformedModelValuesFailClosedTests(unittest.TestCase):
    """Property 4: before any process exists."""

    def test_every_unsafe_model_shape_is_refused_at_declaration(self) -> None:
        for model in (
            "../glm", "glm 5.2", "-glm", "glm;rm -rf /", "$(x)", "`id`",
            "<synthetic>", "glm|cat", "glm&", "glm\\n", "glm/5.2",
        ):
            with self.subTest(model=model):
                try:
                    profile = load(profile_with("claude", model))["p"]
                except AgentProfileError:
                    continue            # refused one layer earlier, still fail-closed
                with self.assertRaises(AgentProfileError) as caught:
                    validate_profile_command_safety(
                        profile,
                        token_pattern=AGENT_COMMAND_PATTERN,
                        known_commands=KNOWN,
                        custom_command_pattern=CUSTOM,
                    )
                self.assertEqual(caught.exception.reason, REASON_INVALID_MODEL)

    def test_an_overlong_model_token_is_still_only_a_token(self) -> None:
        """Length is not the boundary -- SHAPE is -- so a long but well-formed token passes
        the shape gate and is then refused for lacking a capability, not for its length."""
        self.assertTrue(MODEL_TOKEN_PATTERN.fullmatch("a" * 500))

    def test_a_model_cannot_carry_a_newline_into_the_evidence_table(self) -> None:
        self.assertIsNone(MODEL_TOKEN_PATTERN.fullmatch("glm\n5.2"))
        self.assertIsNone(MODEL_TOKEN_PATTERN.fullmatch("glm|5.2"))


class SelectionOnlyThroughADeclaredCapabilityTests(unittest.TestCase):
    """Property 5: the default state of the system is refusal."""

    def test_the_capability_parameter_defaults_to_empty(self) -> None:
        import inspect

        from scripts.agent_profile import validate_effective_identity

        signature = inspect.signature(validate_effective_identity)
        self.assertEqual(
            signature.parameters["model_capabilities"].default, frozenset()
        )

    def test_the_driver_defaults_to_none(self) -> None:
        import inspect

        signature = inspect.signature(OrcaRuntimeHarness.__init__)
        self.assertIsNone(signature.parameters["model_driver"].default)


class NoVendorLaunchArgumentTests(unittest.TestCase):
    """Property 6: `agent_launch_arguments` stays empty in both skills."""

    def test_no_model_flag_was_added_to_any_shipped_module(self) -> None:
        for directory in (
            REPO_ROOT / "scripts",
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools",
        ):
            for path in sorted(directory.rglob("*.py")):
                if path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8")
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or '"--model"' not in line:
                        continue
                    with self.subTest(path=path.name, line=stripped[:70]):
                        self.fail(f"{path.name} composes a --model flag: {stripped}")


if __name__ == "__main__":
    unittest.main()
