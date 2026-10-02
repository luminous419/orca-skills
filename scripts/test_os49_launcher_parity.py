#!/usr/bin/env python3
"""OS-49: the launcher door must apply every validation the policy door applies.

The shipped launcher is a second, independent entrance into the SAME runtime. If a Gate A
validation lands on the Coordinator's door only, the launcher becomes a weaker door -- and
a model nobody could verify would route through it.

The subset assertion here is MECHANICAL (an AST walk), not a hand-maintained list, so a
validation added in a future ticket fails this test until it lands on both doors.
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_profile
from scripts.agent_profile import (
    MODEL_SELECTION_VERIFIED_CAPABILITY,
    REASON_MODEL_NOT_SUPPORTED,
    REASON_WORKER_REVIEWER_MUST_DIFFER,
)
from scripts.deterministic_workflow import launcher

#: The agent commands the profiles below route to. The door's PATH check (`validate_
#: routing_commands`, gate 4) runs BEFORE Gate A, so on a host without these executables
#: every behavioural test below stopped at `AGENT_COMMAND_NOT_FOUND` and never reached the
#: gate it exists to test -- which is exactly how all six CI lanes failed on PR head
#: c3d7484 while passing on a developer machine that happens to have a Claude CLI.
#:
#: The fix is to OWN the PATH rather than to hope: `LauncherBehaviourTests.setUp` writes an
#: executable shim per command into a temporary directory and makes that directory the
#: ENTIRE PATH. So the host's real `claude`/`codex` are invisible too, and the test behaves
#: identically on a machine that has them and on one that does not.
ROUTED_COMMANDS = ("claude", "codex")

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_DOOR = REPO_ROOT / "scripts" / "skill_policy.py"
LAUNCHER_DOOR = REPO_ROOT / "scripts" / "deterministic_workflow" / "launcher.py"

#: Every public validation entry point `agent_profile` offers. The subset assertion is
#: computed against THIS set, derived from the module, so a new validator joins it
#: automatically rather than needing to be remembered here.
VALIDATION_ENTRY_POINTS = frozenset(
    name for name in dir(agent_profile)
    if name.startswith("validate_") and callable(getattr(agent_profile, name))
)


#: Validations the launcher door enforces with its OWN expression instead of by calling
#: the shared validator, mapped to the expression that does it. Pre-existing and NOT
#: introduced by OS-49: `orca_run_routing` has refused unresolved required roles through
#: `routing.unresolved_required()` since it was written, with its own message naming each
#: `<phase>/<role>`. Changing that message is unrelated to OS-49, so the exception is
#: declared here with its evidence and verified present by its own test.
OPEN_CODED_ON_THE_LAUNCHER_DOOR = {
    "validate_required_roles": "routing.unresolved_required()",
}


def called_validators(path: Path, function: str) -> set[str]:
    """Which agent_profile validators one function calls, by walking its AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function
    )
    found: set[str] = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if name in VALIDATION_ENTRY_POINTS:
            found.add(name)
    return found


class LauncherParityTests(unittest.TestCase):
    def test_the_entry_point_set_is_not_empty(self) -> None:
        """A vacuous subset assertion would pass against a typo in either name."""
        self.assertIn("validate_effective_identity", VALIDATION_ENTRY_POINTS)
        self.assertIn("validate_routing_commands", VALIDATION_ENTRY_POINTS)
        self.assertIn("validate_required_roles", VALIDATION_ENTRY_POINTS)
        self.assertIn("validate_profile_command_safety", VALIDATION_ENTRY_POINTS)

    def test_the_policy_doors_validations_are_a_subset_of_the_launchers(self) -> None:
        """Mechanical, so a FUTURE validation cannot land on one door only.

        One PRE-EXISTING exception is declared below with its evidence rather than hidden:
        the launcher enforces the required-roles invariant with its own open-coded check
        instead of calling the validator. The invariant IS enforced on both doors -- only
        the message differs -- and the equivalence is asserted separately, so subtracting
        it here cannot mask a door that stopped enforcing something.
        """
        policy = called_validators(POLICY_DOOR, "_resolve_agent_routing")
        door = called_validators(LAUNCHER_DOOR, "orca_run_routing")
        self.assertTrue(policy, "the policy door calls no validator at all")
        missing = policy - door - set(OPEN_CODED_ON_THE_LAUNCHER_DOOR)
        self.assertEqual(
            missing, set(),
            "these validations land on the Coordinator's door but not on the shipped "
            f"launcher's, which makes the launcher a weaker entrance: {sorted(missing)}",
        )

    def test_every_declared_open_coded_equivalent_is_really_present(self) -> None:
        """The exception above pays for itself: if the launcher ever drops its open-coded
        check, subtracting the validator from the subset assertion stops being honest and
        this test is what fails."""
        text = LAUNCHER_DOOR.read_text(encoding="utf-8")
        for validator, expression in OPEN_CODED_ON_THE_LAUNCHER_DOOR.items():
            with self.subTest(validator=validator):
                self.assertIn(expression, text)

    def test_no_model_validation_is_open_coded(self) -> None:
        """OS-49's own gate is CALLED on both doors, never re-implemented on one."""
        self.assertNotIn(
            "validate_effective_identity", OPEN_CODED_ON_THE_LAUNCHER_DOOR
        )

    def test_both_doors_call_the_effective_identity_gate(self) -> None:
        for path, function in (
            (POLICY_DOOR, "_resolve_agent_routing"),
            (LAUNCHER_DOOR, "orca_run_routing"),
        ):
            with self.subTest(door=path.name):
                self.assertIn(
                    "validate_effective_identity", called_validators(path, function)
                )

    def test_the_policy_door_offers_no_model_capability_at_all(self) -> None:
        """The Coordinator's door has no driver to be given and never names a capability.

        Unchanged by the M6 seam: this door produces a PolicyDecision, not a harness, so
        there is no second gate for it to agree with and nothing for a driver to be
        threaded to. A capability spelled here would be a capability nothing could honour.
        """
        text = POLICY_DOOR.read_text(encoding="utf-8")
        self.assertNotIn("model_capabilities=", text)
        self.assertNotIn("MODEL_SELECTION_VERIFIED", text)

    def test_the_launcher_doors_capability_comes_only_from_an_injected_driver(self) -> None:
        """OS-49 BUGFIX (review M6). The launcher door GAINED a `model_driver` seam, and the
        previous version of this test asserted the string `model_capabilities=` never
        appeared in launcher.py -- which encoded the old behaviour exactly: that NO
        construction path could carry a driver, which is the defect M6 names.

        Replacing a grep with three stronger, behavioural facts rather than deleting it:

          1. the parameter's default is `None`, so a default construction offers nothing;
          2. the capability is DERIVED from that parameter through the one shared
             derivation, never spelled as a literal, so Gate A and Gate B cannot disagree;
          3. no literal capability token is written on this door at all.

        The behavioural half -- that a declared model is still refused when no driver is
        passed -- is `test_a_declared_model_is_refused_through_the_launcher` below, which
        calls the door for real.
        """
        for function in (launcher.orca_run_routing, launcher.build_orca_adapter):
            with self.subTest(function=function.__name__):
                parameter = inspect.signature(function).parameters["model_driver"]
                self.assertIs(parameter.default, None)
        text = LAUNCHER_DOOR.read_text(encoding="utf-8")
        self.assertIn(
            "model_capabilities=agent_profile.model_selection_capabilities(model_driver)",
            text,
            "Gate A's capability must be derived from the injected driver through the one "
            "shared derivation, not spelled on this door",
        )
        self.assertNotIn("MODEL_SELECTION_VERIFIED", text)
        self.assertNotIn(MODEL_SELECTION_VERIFIED_CAPABILITY, text)

    def test_no_launcher_cli_flag_exposes_the_driver_seam(self) -> None:
        """The seam is a CODE parameter, never configuration -- the M4 lesson applied to
        M6. A `--model-driver` flag would be a user-loadable way to claim a capability
        nothing in this release can honour."""
        text = LAUNCHER_DOOR.read_text(encoding="utf-8")
        for flag in ("--model-driver", "--model_driver", "--model-capability"):
            with self.subTest(flag=flag):
                self.assertNotIn(flag, text)

    def test_both_gates_read_one_capability_derivation(self) -> None:
        """Gate A (this door) and Gate B (the harness barrier) must not have two rules.

        `model_selection_capabilities` is that one rule; both call sites are asserted by
        name, and the harness is asserted to no longer ask the weaker `is None` question
        that let a driver without a callable `select_and_verify` through Gate B.
        """
        harness = REPO_ROOT / "scripts" / "orca_runtime_harness.py"
        door = LAUNCHER_DOOR.read_text(encoding="utf-8")
        runtime = harness.read_text(encoding="utf-8")
        self.assertIn("agent_profile.model_selection_capabilities(model_driver)", door)
        self.assertIn("model_selection_capabilities(\n            self.model_driver\n        )",
                      runtime)
        self.assertNotIn("if self.model_driver is None:", runtime)


class LauncherBehaviourTests(unittest.TestCase):
    """The same profile must be refused through the launcher with the SAME reason.

    HOST-INDEPENDENT by construction: `setUp` replaces the whole PATH with a directory
    holding one shim per routed command, so the PATH gate that precedes Gate A resolves on
    every machine and every CI lane, and the assertions below are about the gate under test
    rather than about what the developer happens to have installed. Nothing is weakened --
    the shims only make the commands RESOLVABLE; no validator, reason code or assertion is
    relaxed, and a profile that should be refused is still refused for the same reason.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary_directory.name)
        (self.project / ".orca").mkdir(parents=True)
        binaries = self.project / "bin"
        binaries.mkdir()
        for command in ROUTED_COMMANDS:
            shim = binaries / command
            shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            shim.chmod(0o755)
        # The ENTIRE PATH, not a prefix: a host `claude` must not be able to satisfy the
        # gate either, or the test would still be measuring the host on half the machines.
        patcher = patch.dict(os.environ, {"PATH": str(binaries)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write(self, text: str) -> None:
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            text, encoding="utf-8"
        )

    def run_door(self, name: str, *, risk: str = "high", model_driver=None):
        return launcher.orca_run_routing(
            agent_profile_name=name,
            requested_phases=("implementation",),
            risk=risk,
            project_root=self.project,
            **({} if model_driver is None else {"model_driver": model_driver}),
        )

    def test_every_routed_command_resolves_on_the_sandbox_path(self) -> None:
        """The sandbox itself, asserted -- otherwise a broken shim would silently turn
        every refusal test below back into an AGENT_COMMAND_NOT_FOUND test that passes for
        the wrong reason."""
        import shutil

        for command in ROUTED_COMMANDS:
            with self.subTest(command=command):
                resolved = shutil.which(command)
                self.assertIsNotNone(resolved, f"{command} does not resolve")
                self.assertTrue(
                    str(resolved).startswith(str(self.project)),
                    f"{command} resolved to {resolved!r}, outside the sandbox",
                )

    def test_a_declared_model_is_refused_through_the_launcher(self) -> None:
        self.write(
            "version: 2\n"
            "profiles:\n"
            "  split:\n"
            "    phases:\n"
            "      implementation:\n"
            "        worker:\n"
            "          command: claude\n"
            "          model: glm-5.2\n"
            "        reviewer:\n"
            "          command: claude\n"
            "          model: glm-5.3-flash\n"
            "    final_review:\n"
            "      reviewer: codex\n"
        )
        with self.assertRaises(launcher.LauncherError) as caught:
            self.run_door("split")
        message = str(caught.exception)
        self.assertIn(REASON_MODEL_NOT_SUPPORTED, message)
        # The PATH gate is BEHIND us, not merely absent from the message: that is the whole
        # point of the sandbox, and asserting it keeps this test from silently degrading
        # into "something failed" if the shims ever stop working.
        self.assertNotIn("AGENT_COMMAND_NOT_FOUND", message)

    def test_a_same_command_pair_is_refused_through_the_launcher(self) -> None:
        self.write(
            "version: 1\n"
            "profiles:\n"
            "  same:\n"
            "    phases:\n"
            "      implementation:\n"
            "        worker: claude\n"
            "        reviewer: claude\n"
            "    final_review:\n"
            "      reviewer: codex\n"
        )
        with self.assertRaises(launcher.LauncherError) as caught:
            self.run_door("same")
        message = str(caught.exception)
        self.assertIn(REASON_WORKER_REVIEWER_MUST_DIFFER, message)
        self.assertNotIn("AGENT_COMMAND_NOT_FOUND", message)

    def test_a_malformed_model_token_is_refused_through_the_launcher(self) -> None:
        self.write(
            "version: 2\n"
            "profiles:\n"
            "  bad:\n"
            "    phases:\n"
            "      implementation:\n"
            "        worker:\n"
            "          command: claude\n"
            "          model: $(x)\n"
            "        reviewer: codex\n"
            "    final_review:\n"
            "      reviewer: codex\n"
        )
        with self.assertRaises(launcher.LauncherError) as caught:
            self.run_door("bad")
        self.assertIn("INVALID_AGENT_MODEL", str(caught.exception))

    def test_a_distinct_command_v1_profile_still_routes_through_the_launcher(self) -> None:
        self.write(
            "version: 1\n"
            "profiles:\n"
            "  fine:\n"
            "    phases:\n"
            "      implementation:\n"
            "        worker: claude\n"
            "        reviewer: codex\n"
            "    final_review:\n"
            "      reviewer: codex\n"
        )
        # No `except` fallback any more: PATH absence used to be tolerated here as an
        # environment fact, which made the real assertion skippable on exactly the hosts
        # that matter. The sandbox removes the environment fact, so the door must ROUTE.
        routing = self.run_door("fine")
        self.assertFalse(routing.is_model_aware)


if __name__ == "__main__":
    unittest.main()
