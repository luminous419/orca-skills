#!/usr/bin/env python3
"""OS-49: the closed vocabularies, and the two locks that keep the real runtime refused.

Four vocabularies live at three layers with two casings. These tests pin each tuple's
members AND its order, assert the two layers' spellings cannot drift, and -- the two
load-bearing ones -- assert that `launch_argv` is not a request method and that no
model-selection command literal or acknowledgement parser appears anywhere in the diff.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts import agent_profile
from scripts.deterministic_workflow import (
    contracts,
    fake_adapter,
    standalone_preflight,
    standalone_profile,
)
from scripts.orca_runtime_harness import (
    MODEL_IDENTITY_FAILURE_REASONS,
    MODEL_SELECTION_FAILURE_REASONS,
    MODEL_SELECTION_REQUEST_METHODS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
TOOLS = REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools"


class ModelEvidenceVocabularyTests(unittest.TestCase):
    def test_the_six_states_in_order(self) -> None:
        self.assertEqual(
            agent_profile.MODEL_EVIDENCE_STATES,
            ("none", "requested", "verified", "mismatch", "unverifiable", "stale"),
        )

    def test_verified_is_the_only_admissible_evidence(self) -> None:
        """`unknown` is not `pass` -- the rule this vocabulary is modelled on."""
        self.assertEqual(agent_profile.MODEL_EVIDENCE_VERIFIED, "verified")
        admissible = {agent_profile.MODEL_EVIDENCE_VERIFIED}
        self.assertEqual(
            set(agent_profile.MODEL_EVIDENCE_STATES) - admissible,
            {"none", "requested", "mismatch", "unverifiable", "stale"},
        )

    def test_each_member_constant_matches_its_tuple_entry(self) -> None:
        for name in agent_profile.MODEL_EVIDENCE_STATES:
            with self.subTest(state=name):
                constant = getattr(agent_profile, f"MODEL_EVIDENCE_{name.upper()}")
                self.assertEqual(constant, name)


class RuntimeVocabularyTests(unittest.TestCase):
    def test_the_seven_selection_failure_reasons_in_lifecycle_order(self) -> None:
        """The tuple's ORDER documents the lifecycle: request, then observation, then the
        Worker/Reviewer PAIR.

        `model_selection_pair_unadmitted` was ADDED in OS-49 iteration 2 for review
        finding F-001, which is why this lock moved from six names to seven. The lock was
        not weakened to let the change through -- it is still an exact, ordered equality
        over the whole closed set, and the new name is the one the ticket asks for under
        "named failure states ... for cases such as: ... unverified model selection": a
        same-command counterpart with no positively verified model is a distinct,
        separately named refusal from this role's own selection having failed.
        """
        self.assertEqual(
            MODEL_SELECTION_FAILURE_REASONS,
            (
                "model_selection_unsupported",
                "model_selection_request_absent",
                "model_selection_request_stale",
                "model_selection_unverified",
                "model_selection_mismatch",
                "model_selection_ambiguous",
                "model_selection_pair_unadmitted",
            ),
        )

    def test_the_three_admission_states(self) -> None:
        """OS-49 iteration 2: declaration time has THREE answers, not two. `pending` is
        the one that is neither a pass nor a refusal -- an obligation the pre-delivery
        barrier must discharge -- and it exists so that `requested` never has to be
        treated as proof of independence in order to keep the feature routable."""
        self.assertEqual(
            agent_profile.EFFECTIVE_IDENTITY_ADMISSION_STATES,
            ("independent", "refused", "pending_model_verification"),
        )
        for name in agent_profile.EFFECTIVE_IDENTITY_ADMISSION_STATES:
            with self.subTest(state=name):
                suffix = "PENDING_VERIFICATION" if name.startswith("pending") else name.upper()
                self.assertEqual(getattr(agent_profile, f"ADMISSION_{suffix}"), name)

    def test_the_four_reuse_identity_reasons(self) -> None:
        self.assertEqual(
            MODEL_IDENTITY_FAILURE_REASONS,
            (
                "model_identity_mismatch",
                "model_identity_unverified",
                "model_identity_stale",
                "model_capability_unsupported",
            ),
        )

    def test_request_leg_members_precede_observation_leg_members(self) -> None:
        request_leg = [
            index for index, name in enumerate(MODEL_SELECTION_FAILURE_REASONS)
            if "request" in name or name.endswith("unsupported")
        ]
        observation_leg = [
            index for index, name in enumerate(MODEL_SELECTION_FAILURE_REASONS)
            if index not in request_leg
        ]
        self.assertTrue(request_leg and observation_leg)
        self.assertLess(max(request_leg), min(observation_leg))

    def test_the_two_layers_vocabularies_are_disjoint_and_differently_cased(self) -> None:
        screaming = {
            agent_profile.REASON_INVALID_MODEL,
            agent_profile.REASON_MODEL_NOT_SUPPORTED,
            agent_profile.REASON_WORKER_REVIEWER_MUST_DIFFER,
        }
        snake = set(MODEL_SELECTION_FAILURE_REASONS) | set(MODEL_IDENTITY_FAILURE_REASONS)
        self.assertEqual(screaming & snake, set())
        for name in screaming:
            self.assertEqual(name, name.upper())
        for name in snake:
            self.assertEqual(name, name.lower())


class TheTwoLocksThatKeepTheRealRuntimeRefusedTests(unittest.TestCase):
    def test_launch_argv_is_not_a_request_method(self) -> None:
        """A model-pinned wrapper's argv was composed before this session existed, so it
        is not a request attributable to THIS attempt. Keeping it out of the closed set is
        a second, independent mechanism keeping the real runtime fail-closed: no real
        adapter can name a member."""
        self.assertEqual(MODEL_SELECTION_REQUEST_METHODS, ("driver_select_and_verify",))
        self.assertNotIn("launch_argv", MODEL_SELECTION_REQUEST_METHODS)
        for method in MODEL_SELECTION_REQUEST_METHODS:
            with self.subTest(method=method):
                self.assertNotIn("model", method.replace("model_", ""))
                self.assertNotIn("/", method)

    def test_no_model_command_literal_or_acknowledgement_parser_in_the_diff(self) -> None:
        """OS-49 invents no model-selection command syntax, acknowledgement format or
        output semantics, and ships no parser for any of them.

        The assertion is over CODE, not over prose: a module may EXPLAIN in a comment why
        it refuses to assume an interactive syntax -- several do, and that explanation is
        the point -- but no module may carry the literal in a string it could send, match,
        or build an argv from. So comments are tokenized away and every remaining token is
        checked, which is strictly stronger than a line grep that a reworded comment could
        have satisfied.
        """
        import io
        import tokenize

        forbidden = re.compile(r"/model\b")
        for directory in (SCRIPTS, TOOLS):
            for path in sorted(directory.rglob("*.py")):
                if path.name.startswith("test_"):
                    continue
                source = path.read_text(encoding="utf-8")
                hits: list[str] = []
                for token in tokenize.generate_tokens(io.StringIO(source).readline):
                    if token.type == tokenize.COMMENT:
                        continue
                    if forbidden.search(token.string):
                        hits.append(f"line {token.start[0]}: {token.string[:80]!r}")
                with self.subTest(path=path.relative_to(REPO_ROOT)):
                    self.assertEqual(hits, [], f"{path}: {hits}")

    def test_the_reference_driver_composes_no_command_and_parses_no_output(self) -> None:
        source = (
            SCRIPTS / "deterministic_workflow" / "fake_adapter.py"
        ).read_text(encoding="utf-8")
        start = source.index("class InProcessModelDriver")
        body = source[start:source.index("class FakeAdapter")]
        for forbidden in ("/model", "re.compile", "subprocess", "json.loads"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)


class CapabilityTokenTests(unittest.TestCase):
    def test_the_token_is_in_capabilities_and_not_in_base(self) -> None:
        self.assertIn(contracts.MODEL_SELECTION_VERIFIED, contracts.CAPABILITIES)
        self.assertNotIn(
            contracts.MODEL_SELECTION_VERIFIED, contracts.BASE_CAPABILITIES
        )
        self.assertEqual(
            contracts.MODEL_CAPABILITIES,
            frozenset({contracts.MODEL_SELECTION_VERIFIED}),
        )

    def test_base_capabilities_is_byte_unchanged(self) -> None:
        """Adding a member there would change what EVERY existing adapter is required to
        declare, and a graph test builds `BASE_CAPABILITIES - {"agent_interrupt"}`."""
        self.assertEqual(
            contracts.BASE_CAPABILITIES,
            frozenset({
                "agent_start", "agent_command", "agent_status", "agent_interrupt",
                "settlement", "idempotent_intent", "artifact_immutable", "checkpoint",
            }),
        )

    def test_the_two_spellings_of_the_token_are_identical(self) -> None:
        """`agent_profile` names the capability as a plain string rather than importing it
        from the engine package, so this is the lock that keeps the duplication honest."""
        self.assertEqual(
            agent_profile.MODEL_SELECTION_VERIFIED_CAPABILITY,
            contracts.MODEL_SELECTION_VERIFIED,
        )

    def test_the_reference_driver_spells_the_request_method_identically(self) -> None:
        self.assertEqual(
            fake_adapter.InProcessModelDriver.REQUEST_METHOD,
            MODEL_SELECTION_REQUEST_METHODS[0],
        )


class PreflightVocabularyTests(unittest.TestCase):
    def test_reasons_gained_exactly_one_member(self) -> None:
        self.assertIn("model_selection_unsupported", standalone_preflight.REASONS)
        self.assertEqual(
            len([r for r in standalone_preflight.REASONS if "model" in r]), 1
        )

    def test_verdicts_are_unchanged(self) -> None:
        self.assertEqual(standalone_preflight.VERDICTS, ("pass", "fail", "unknown"))

    def test_checks_are_unchanged(self) -> None:
        self.assertEqual(
            standalone_preflight.CHECKS,
            ("binary", "version", "auth", "profile", "delivery_mode"),
        )


class PatternSeparationTests(unittest.TestCase):
    def test_the_model_pattern_is_separate_from_the_command_pattern(self) -> None:
        from scripts.skill_policy import AGENT_COMMAND_PATTERN

        self.assertNotEqual(
            agent_profile.MODEL_TOKEN_PATTERN.pattern, AGENT_COMMAND_PATTERN.pattern
        )

    def test_no_module_matches_a_model_with_the_command_pattern(self) -> None:
        """A grep-level assertion that the two patterns are never substituted: the model
        token pattern is the only thing any module uses on a model value."""
        for directory in (SCRIPTS, TOOLS):
            for path in sorted(directory.rglob("*.py")):
                if path.name.startswith("test_"):
                    continue
                text = path.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if "AGENT_COMMAND_PATTERN" not in line:
                        continue
                    with self.subTest(path=path.name, line=line.strip()[:70]):
                        self.assertNotIn(".model", line)
                        self.assertNotIn("model=", line)


class StandaloneSchemaTests(unittest.TestCase):
    def test_the_selector_is_profile_data_with_no_default(self) -> None:
        profile_fields = standalone_profile.StandaloneProfile.__dataclass_fields__
        self.assertIn("model_selector", profile_fields)
        self.assertIsNone(profile_fields["model_selector"].default)

    def test_extra_args_is_not_a_model_carrier(self) -> None:
        """OS-49 closes today's unverified hole by REFUSING a model routed that way, never
        by blessing it: nothing in the schema or the preflight reads a model out of
        extra_args."""
        source = (
            SCRIPTS / "deterministic_workflow" / "standalone_profile.py"
        ).read_text(encoding="utf-8")
        for line in source.splitlines():
            if "extra_args" in line:
                with self.subTest(line=line.strip()[:70]):
                    self.assertNotIn("model", line.lower().replace("model_selector", ""))


if __name__ == "__main__":
    unittest.main()
