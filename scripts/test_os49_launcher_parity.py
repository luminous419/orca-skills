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
import re
import tempfile
import unittest
from pathlib import Path

from scripts import agent_profile
from scripts.agent_profile import (
    REASON_MODEL_NOT_SUPPORTED,
    REASON_WORKER_REVIEWER_MUST_DIFFER,
)
from scripts.deterministic_workflow import launcher

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

    def test_neither_door_offers_a_model_capability(self) -> None:
        """The default is EMPTY, and that single default is what makes the real-runtime
        path honestly fail closed. A door that passed one would have to be able to both
        request a selection and observe the resolution, which neither can."""
        for path in (POLICY_DOOR, LAUNCHER_DOOR):
            text = path.read_text(encoding="utf-8")
            with self.subTest(door=path.name):
                self.assertNotIn("model_capabilities=", text)
                self.assertNotIn("MODEL_SELECTION_VERIFIED", text)


class LauncherBehaviourTests(unittest.TestCase):
    """The same profile must be refused through the launcher with the SAME reason."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary_directory.name)
        (self.project / ".orca").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write(self, text: str) -> None:
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            text, encoding="utf-8"
        )

    def run_door(self, name: str, *, risk: str = "high"):
        return launcher.orca_run_routing(
            agent_profile_name=name,
            requested_phases=("implementation",),
            risk=risk,
            project_root=self.project,
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
        self.assertIn(REASON_MODEL_NOT_SUPPORTED, str(caught.exception))

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
        self.assertIn(REASON_WORKER_REVIEWER_MUST_DIFFER, str(caught.exception))

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
        try:
            routing = self.run_door("fine")
        except launcher.LauncherError as error:
            # PATH absence is an environment fact, not an OS-49 refusal; the point of this
            # test is that no MODEL reason appears.
            self.assertIn("AGENT_COMMAND_NOT_FOUND", str(error))
            return
        self.assertFalse(routing.is_model_aware)


if __name__ == "__main__":
    unittest.main()
