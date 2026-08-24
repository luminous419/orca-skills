#!/usr/bin/env python3
"""Tests for scripts/skill_policy.py's `#### Risk profile contract` parser (OS-3).

A dedicated file, following scripts/test_workflow_contract.py's precedent: one
module's parser, exercised first against synthetic text that no real SKILL.md has to
carry, and then against both real skills end to end. The point of the second half is
that `load_risk_contract(loop) is None` is not an assumption -- it is a property of
the shipped files, checked here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.skill_policy import load_risk_contract


REPO_ROOT = Path(__file__).resolve().parents[1]
WELL_FORMED = """#### Risk profile contract

prose the block is an index into.

```text
RISK_PARAMETER = risk
RISK_LEVELS = low, medium, high
RISK_DEFAULT = high
```
"""


def _parse(text: str):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "SKILL.md"
        path.write_text(text, encoding="utf-8")
        return load_risk_contract(path)


class RiskContractParsingTests(unittest.TestCase):
    """T-17: every way the block can be well-formed or not."""

    def test_a_well_formed_block_parses(self) -> None:
        self.assertEqual(
            _parse(WELL_FORMED),
            {
                "RISK_PARAMETER": ("risk",),
                "RISK_LEVELS": ("low", "medium", "high"),
                "RISK_DEFAULT": ("high",),
            },
        )

    def test_an_absent_block_is_none_not_an_error(self) -> None:
        """None is the capability answer, not a failure: it is what a skill with no
        risk axis yields, and evaluate_invocation() branches on exactly that."""
        self.assertIsNone(_parse("# A skill with no risk axis\n\nprose only.\n"))

    def test_a_duplicate_key_is_rejected(self) -> None:
        self.assertIsNone(
            _parse(WELL_FORMED.replace("RISK_DEFAULT = high", "RISK_LEVELS = low"))
        )

    def test_an_uppercase_value_token_is_rejected(self) -> None:
        self.assertIsNone(_parse(WELL_FORMED.replace("= high", "= HIGH")))

    def test_a_non_key_value_line_is_rejected(self) -> None:
        self.assertIsNone(
            _parse(WELL_FORMED.replace("RISK_DEFAULT = high", "not a contract line"))
        )

    def test_a_lowercase_key_is_rejected(self) -> None:
        self.assertIsNone(
            _parse(WELL_FORMED.replace("RISK_DEFAULT = high", "risk_default = high"))
        )

    def test_a_value_with_a_hyphen_is_rejected(self) -> None:
        """The token grammar is [a-z][a-z0-9_]* -- the same one the six existing
        orchestration-only anchor contracts use."""
        self.assertIsNone(_parse(WELL_FORMED.replace("= high", "= high-risk")))


class RealSkillFilesTests(unittest.TestCase):
    """T-8/T-17: the shipped files, read end to end."""

    ORCHESTRATION = REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    LOOP = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"

    def test_the_orchestration_skill_carries_the_contract(self) -> None:
        contract = load_risk_contract(self.ORCHESTRATION)
        self.assertIsNotNone(contract)
        self.assertEqual(contract["RISK_LEVELS"], ("low", "medium", "high"))
        self.assertEqual(contract["RISK_DEFAULT"], ("high",))
        self.assertEqual(contract["RISK_INVALID_ERROR"], ("invalid_risk",))
        self.assertEqual(contract["RISK_SELECTION_SOURCES"], ("explicit", "default"))
        self.assertIn(contract["RISK_DEFAULT"][0], contract["RISK_LEVELS"])

    def test_the_loop_skill_has_no_risk_axis(self) -> None:
        """The whole out-of-scope guarantee, in one assertion."""
        self.assertIsNone(load_risk_contract(self.LOOP))

    def test_the_loop_skill_never_names_the_orchestration_error_code(self) -> None:
        self.assertNotIn("INVALID_RISK", self.LOOP.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
