"""M-004 regression: one workflow control plane, with automatic drift detection."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.deterministic_workflow.contracts import ROUTE_TOKENS
from scripts.deterministic_workflow.graph_spec import GRAPH_OWNED_DECISIONS
from scripts.validate_workflow_graph_docs import (
    CONTROL_PLANE_PATTERN,
    DELEGATION_CLAUSE,
    NON_AUTHORITATIVE_MARKER,
    ControlPlaneError,
    validate_control_plane,
)

SKILL = Path(__file__).resolve().parents[1] / "orca-worker-reviewer-orchestration" / "SKILL.md"


class ControlPlaneContractTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.block = json.loads(CONTROL_PLANE_PATTERN.findall(self.text)[0])

    def test_shipped_skill_declares_a_single_authoritative_control_plane(self):
        validate_control_plane(self.text)
        self.assertEqual(self.block["authority"], "deterministic_engine")
        self.assertEqual(self.text.count(DELEGATION_CLAUSE), 1)

    def test_declared_decisions_match_the_engine_specification(self):
        declared = tuple(entry["decision"] for entry in self.block["graph_owned_decisions"])
        self.assertEqual(declared, tuple(GRAPH_OWNED_DECISIONS))

    def test_every_route_token_is_owned_by_a_declared_decision(self):
        owned = {token for entry in self.block["graph_owned_decisions"]
                 for token in entry["route_tokens"]}
        self.assertEqual(owned, set(ROUTE_TOKENS))

    def test_graph_owned_sections_are_demoted_to_non_authoritative(self):
        for entry in self.block["graph_owned_decisions"]:
            for heading in entry["sections"]:
                with self.subTest(section=heading):
                    self.assertEqual(self.text.count(f"\n{heading}\n"), 1)
                    body = self.text.split(f"\n{heading}\n", 1)[1]
                    self.assertIn(NON_AUTHORITATIVE_MARKER, body.split("\n## ", 1)[0])

    def test_safety_and_user_guidance_sections_are_preserved_and_still_authoritative(self):
        for heading in self.block["skill_owned_safety"]:
            with self.subTest(section=heading):
                self.assertEqual(self.text.count(f"\n{heading}\n"), 1)
                body = self.text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]
                self.assertNotIn(NON_AUTHORITATIVE_MARKER, body)


class ControlPlaneDriftDetectionTests(unittest.TestCase):
    """The validator must reject both reintroduced routing and deleted safety rules."""

    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.block = json.loads(CONTROL_PLANE_PATTERN.findall(self.text)[0])

    def assert_rejected(self, text, fragment):
        with self.assertRaises(ControlPlaneError) as caught:
            validate_control_plane(text)
        self.assertIn(fragment, str(caught.exception))

    def test_removing_a_non_authoritative_marker_is_detected(self):
        self.assert_rejected(self.text.replace(NON_AUTHORITATIVE_MARKER, "", 1),
                             "not demoted")

    def test_deleting_a_preserved_safety_section_is_detected(self):
        heading = self.block["skill_owned_safety"][0]
        self.assert_rejected(self.text.replace(f"\n{heading}\n", "\n## Removed\n", 1),
                             "safety section")

    def test_demoting_a_safety_section_is_detected(self):
        heading = self.block["skill_owned_safety"][0]
        demoted = self.text.replace(f"\n{heading}\n",
                                    f"\n{heading}\n\n{NON_AUTHORITATIVE_MARKER}\n", 1)
        self.assert_rejected(demoted, "must stay authoritative")

    def test_dropping_the_delegation_clause_is_detected(self):
        self.assert_rejected(self.text.replace(DELEGATION_CLAUSE, "", 1), "delegation clause")

    def test_engine_decision_drift_is_detected(self):
        block = json.loads(CONTROL_PLANE_PATTERN.findall(self.text)[0])
        block["graph_owned_decisions"] = block["graph_owned_decisions"][:-1]
        drifted = CONTROL_PLANE_PATTERN.sub(
            lambda _: "```workflow-control-plane\n" + json.dumps(block) + "\n```", self.text, count=1)
        self.assert_rejected(drifted, "graph-owned decision set")

    def test_unowned_route_token_is_detected(self):
        block = json.loads(CONTROL_PLANE_PATTERN.findall(self.text)[0])
        block["graph_owned_decisions"][0]["route_tokens"] = []
        drifted = CONTROL_PLANE_PATTERN.sub(
            lambda _: "```workflow-control-plane\n" + json.dumps(block) + "\n```", self.text, count=1)
        self.assert_rejected(drifted, "route token")

    def test_duplicate_control_plane_block_is_detected(self):
        extra = self.text + "\n```workflow-control-plane\n{}\n```\n"
        self.assert_rejected(extra, "exactly one")


if __name__ == "__main__":
    unittest.main()
