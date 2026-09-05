#!/usr/bin/env python3
"""OS-29: the cross-cutting residue -- the claims no single module owns.

Placement follows the repository's own rule (scripts/test_os22_required_tests.py:1-15):
each case goes in the module that owns its subject, and only what belongs to no single
subject lands here. What is here:

  * the import DIRECTION decision_gate.py must keep, asserted over its AST;
  * the zero-scripts/-imports invariant run_logging.py must keep, asserted over ITS
    AST -- without this the caller-supplied `ledger_schema_version` can silently
    regress into an import and break the INSTALLED skill in a target project, a
    failure that is invisible in this repository's own CI;
  * INV-D3, the dispatch-site cardinality OS-29 must not change, as a static count;
  * the byte-parity between scripts/run_logging.py and the skill's tools/ copy, which
    OS-29 makes newly load-bearing because it edits that file.

Every negative assertion here is paired with a control that proves the walker found
the module's REAL imports, on the rule test_os22_required_tests.py:549-557 records: a
negative assertion over a walker that finds nothing proves nothing.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from scripts import decision_gate
from scripts import e2e_harness as e2e_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
SKILL_TOOLS_RUN_LOGGING = (
    REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools" / "run_logging.py"
)
# Every module that lives in scripts/, so "imports nothing from scripts/" is a
# question about a real, enumerated set rather than about a hand-written list that
# quietly stops covering new modules.
SCRIPTS_MODULES = frozenset(
    path.stem for path in SCRIPTS.glob("*.py") if path.stem != "__init__"
)


def imported_names(path: Path) -> set[str]:
    """Every module name `path` imports, in both `import x` and `from x import y`
    form, and with the `scripts.` package prefix stripped."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
                names.add(alias.name.replace("scripts.", ""))
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
            names.add(node.module.replace("scripts.", ""))
    return names


class ImportDirectionTests(unittest.TestCase):
    def test_decision_gate_imports_only_the_contract_and_the_standard_library(
        self,
    ) -> None:
        imported = imported_names(SCRIPTS / "decision_gate.py")

        forbidden = imported & (SCRIPTS_MODULES - {"decision_policy", "decision_gate"})
        self.assertEqual(
            forbidden,
            set(),
            "decision_gate must import nothing from scripts/ but decision_policy: "
            f"found {sorted(forbidden)}",
        )
        # The control: the walker really does see this module's imports, so the
        # emptiness above is a fact about decision_gate and not about the walker.
        self.assertIn("decision_policy", imported)
        self.assertIn("json", imported)
        self.assertIn("re", imported)

    def test_run_logging_imports_nothing_from_scripts_at_all(self) -> None:
        """The invariant OS-29's own design depends on, asserted rather than trusted.

        run_logging.py is byte-duplicated into the installed Skill's tools/, and
        INSTALL.md's documented global install never copies scripts/. An import of
        decision_gate here would make a live Coordinator's logging CLI crash with
        ModuleNotFoundError in any target project -- and this repository's CI would
        stay green, because here scripts/ IS importable. That is why the version
        constant is a required keyword argument instead.
        """
        imported = imported_names(SCRIPTS / "run_logging.py")

        forbidden = imported & SCRIPTS_MODULES
        self.assertEqual(
            forbidden,
            set(),
            f"run_logging must import nothing from scripts/: found {sorted(forbidden)}",
        )
        self.assertNotIn("decision_gate", imported)
        # The control, same shape as above.
        for stdlib in ("json", "os", "re", "argparse"):
            with self.subTest(module=stdlib):
                self.assertIn(stdlib, imported)

    def test_the_skill_tools_copy_is_byte_identical(self) -> None:
        """C3b: every run_logging edit is mirrored, or the validator fails."""
        self.assertTrue(SKILL_TOOLS_RUN_LOGGING.is_file())
        self.assertEqual(
            (SCRIPTS / "run_logging.py").read_bytes(),
            SKILL_TOOLS_RUN_LOGGING.read_bytes(),
        )
        # The control: the file really carries the OS-29 additions, so byte-identity
        # is identity of the NEW content and not of two stale copies.
        text = SKILL_TOOLS_RUN_LOGGING.read_text(encoding="utf-8")
        for marker in (
            "open_decision_ledger",
            "append_decision_ledger_record",
            "read_decision_ledger",
            "decision_state",
            "decision_reason_code",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_the_gate_and_the_harness_share_one_field_line_grammar(self) -> None:
        """decision_gate spells FIELD_LINE itself because it may not import the
        harness. The two must not drift, so the equality is asserted."""
        self.assertEqual(
            decision_gate.FIELD_LINE.pattern, e2e_module.FIELD_LINE.pattern
        )


class DispatchSiteCardinalityTests(unittest.TestCase):
    """INV-D3: OS-29 adds no dispatch site, no subprocess site and no round.

    A static count over the module's AST rather than a claim in prose. The numbers
    are the pre-OS-29 ones and this ticket does not move them; a future change that
    adds a Reviewer dispatch has to change this test on purpose.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse((SCRIPTS / "e2e_harness.py").read_text(encoding="utf-8"))

    def function(self, name: str) -> ast.FunctionDef:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found")

    def test_run_has_exactly_two_agent_invoking_subprocess_sites(self) -> None:
        run = self.function("run")

        calls = [
            node
            for node in ast.walk(run)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ]

        self.assertEqual(len(calls), 2, "run() must invoke exactly two agents")

    def test_run_workflow_enters_run_through_exactly_the_existing_sites(self) -> None:
        source = (SCRIPTS / "e2e_harness.py").read_text(encoding="utf-8")

        # Two in run_workflow (the phase gate and the T5a revalidation) plus the one
        # inside _run_correction_round, which the T4 path funnels through.
        self.assertEqual(source.count("_phase_harness(phase, self.max_iterations).run("), 1)
        self.assertEqual(source.count("_phase_harness(phase, budget).run("), 2)
        # The control: the counted spelling really occurs, so the equalities above
        # are not two zeroes agreeing with each other.
        self.assertIn("_phase_harness(phase, budget).run(revalidation)", source)

    def test_the_gate_adds_no_round_kind_and_no_run_status(self) -> None:
        from scripts import run_logging

        self.assertEqual(len(run_logging.ROUND_KIND_VALUES), 4)
        # OS-29 itself still adds nothing: the first four values are exactly the ones it
        # inherited, and the terminal OS-29 uses is one that already existed. The three
        # after them are OS-31's, and are named here so this assertion keeps attributing
        # every value to the ticket that introduced it.
        self.assertEqual(
            run_logging.RUN_STATUS_VALUES[:4],
            ("COMPLETED", "BLOCKED", "ERROR", "ESCALATED")
        )
        self.assertEqual(
            run_logging.RUN_STATUS_VALUES[4:],
            ("WAITING_FOR_INPUT", "CANCELLED", "ABANDONED")
        )
        self.assertIn("BLOCKED", run_logging.RUN_STATUS_VALUES)


class WorkerVocabularyTests(unittest.TestCase):
    def test_the_worker_and_reviewer_value_sets_are_unchanged(self) -> None:
        from scripts.workflow_contract import load_workflow_output_contract

        for name in ("orca-worker-reviewer-loop", "orca-worker-reviewer-orchestration"):
            with self.subTest(skill=name):
                contract = load_workflow_output_contract(REPO_ROOT / name / "SKILL.md")
                self.assertEqual(
                    {contract.worker_complete, contract.worker_blocked},
                    {"COMPLETE", "BLOCKED"},
                )
                self.assertEqual(
                    {contract.reviewer_pass, contract.reviewer_fail}, {"PASS", "FAIL"}
                )

    def test_adding_the_gate_field_left_the_two_contracts_identical(self) -> None:
        from scripts.workflow_contract import load_workflow_output_contract

        left = load_workflow_output_contract(
            REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"
        )
        right = load_workflow_output_contract(
            REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
        )

        self.assertEqual(left, right)
        # The control: the field really is documented in both, so the equality is
        # not two files that both lack it.
        for name in ("orca-worker-reviewer-loop", "orca-worker-reviewer-orchestration"):
            text = (REPO_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertGreaterEqual(
                    text.count(f"{decision_gate.GATE_STATE_FIELD}: CLEAR"), 2
                )


if __name__ == "__main__":
    unittest.main()
