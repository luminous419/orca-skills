#!/usr/bin/env python3
"""Smoke tests for deterministic policy decisions defined by the Markdown skills."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest import mock

from scripts.skill_policy import (
    evaluate_invocation,
    finalize_routing,
    load_policy_contract,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "orca-worker-reviewer-loop",
    "orca-worker-reviewer-orchestration",
)
SKILL_PATHS = tuple(REPO_ROOT / name / "SKILL.md" for name in SKILL_NAMES)


class PolicySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        bin_dir = Path(self.temp_directory.name)
        for command in (
            "claude",
            "codex",
            "claude-glm",
            "claude-gemma",
            "claude-opus",
            "codex-sol",
            "bash",
            "sh",
            "python3",
        ):
            executable = bin_dir / command
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        self.path_patch = mock.patch.dict(os.environ, {"PATH": str(bin_dir)})
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp_directory.cleanup()

    def evaluate(self, skill_path: Path, suffix: str):
        return evaluate_invocation(skill_path, f"/{skill_path.parent.name}{suffix}")

    def assert_blocked(self, suffix: str, reason: str) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name, invocation=suffix):
                decision = self.evaluate(skill_path, suffix)
                self.assertEqual(decision.status, "BLOCKED")
                self.assertEqual(decision.reason, reason)
                self.assertFalse(decision.should_execute)

    def assert_valid(self, suffix: str, phases: tuple[str, ...]) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name, invocation=suffix):
                decision = self.evaluate(skill_path, suffix)
                self.assertEqual(decision.status, "VALID")
                self.assertIsNone(decision.reason)
                self.assertTrue(decision.should_execute)
                self.assertEqual(decision.phases, phases)

    def test_help_mode_never_executes(self) -> None:
        expected_invocations = {
            "orca-worker-reviewer-loop": "/orca-worker-reviewer-loop help",
            "orca-worker-reviewer-orchestration": (
                "/orca-worker-reviewer-orchestration help"
            ),
        }
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = evaluate_invocation(
                    skill_path, expected_invocations[skill_path.parent.name]
                )
                self.assertEqual(decision.status, "HELP")
                self.assertFalse(decision.should_execute)
                self.assertIsNone(decision.worker)
                self.assertIsNone(decision.reviewer)

    def test_worker_and_reviewer_must_differ(self) -> None:
        self.assert_blocked(
            " worker=claude-glm reviewer=claude-glm phases=analysis 요청",
            "WORKER_REVIEWER_MUST_DIFFER",
        )
        self.assert_blocked(
            " worker=claude-opus reviewer=claude-opus phases=analysis 요청",
            "WORKER_REVIEWER_MUST_DIFFER",
        )

    def test_generic_commands_are_valid(self) -> None:
        self.assert_valid(
            " worker=claude reviewer=codex phases=analysis 요청",
            ("analysis",),
        )

    def test_known_company_commands_are_valid(self) -> None:
        self.assert_valid(
            " worker=claude-glm reviewer=claude-gemma phases=analysis 요청",
            ("analysis",),
        )

    def test_path_resolved_model_pinned_wrappers_are_valid(self) -> None:
        self.assert_valid(
            " worker=claude-opus reviewer=codex-sol phases=analysis 요청",
            ("analysis",),
        )

    def test_missing_agent_command_is_blocked(self) -> None:
        self.assert_blocked(
            " worker=claude-missing reviewer=claude-gemma phases=analysis 요청",
            "AGENT_COMMAND_NOT_FOUND",
        )

    def test_non_agent_path_commands_are_not_allowed(self) -> None:
        for command in ("bash", "sh", "python3"):
            with self.subTest(command=command):
                self.assert_blocked(
                    f" worker={command} reviewer=codex phases=analysis 요청",
                    "AGENT_NOT_ALLOWED",
                )

    def test_unsafe_agent_commands_are_blocked(self) -> None:
        for value in (
            '"claude --model opus"',
            "../claude",
            "/usr/local/bin/claude",
            "claude;echo",
            "claude&&echo",
        ):
            with self.subTest(value=value):
                self.assert_blocked(
                    f" worker={value} reviewer=codex phases=analysis 요청",
                    "INVALID_AGENT_COMMAND",
                )

    def test_invalid_max_iterations(self) -> None:
        for value in ("0", "11", "many"):
            with self.subTest(value=value):
                self.assert_blocked(
                    f" max-iterations={value} phases=analysis 요청",
                    "INVALID_MAX_ITERATIONS",
                )

    def test_invalid_sequential_phase_order(self) -> None:
        for phases in ("implementation,design", "test,implementation"):
            with self.subTest(phases=phases):
                self.assert_blocked(
                    f" phases={phases} 요청",
                    "INVALID_PHASE_ORDER",
                )

    def test_unknown_explicit_phase_is_blocked(self) -> None:
        for phases in ("unknown", "design,unknown"):
            with self.subTest(phases=phases):
                self.assert_blocked(
                    f" phases={phases} 요청",
                    "INVALID_PHASE",
                )

    def test_explicit_and_natural_language_phase_conflict(self) -> None:
        self.assert_blocked(
            " phases=design,implementation 테스트까지 명시적으로 수행해줘",
            "PHASE_CONFLICT",
        )

    def test_phase_conflict_precedes_other_phase_resolution(self) -> None:
        self.assert_blocked(
            " phases=implementation,design 테스트까지 수행해줘",
            "PHASE_CONFLICT",
        )

    def test_unsupported_specialized_phase_combination(self) -> None:
        for phases in ("bugfix,test", "refactoring,implementation"):
            with self.subTest(phases=phases):
                self.assert_blocked(
                    f" phases={phases} 요청",
                    "UNSUPPORTED_PHASE_COMBINATION",
                )

    def test_valid_sequential_combinations(self) -> None:
        combinations = (
            ("analysis,plan,design", ("analysis", "plan", "design")),
            ("design,implementation", ("design", "implementation")),
            ("implementation,test", ("implementation", "test")),
            (
                "analysis,design,implementation,test",
                ("analysis", "design", "implementation", "test"),
            ),
        )
        for value, expected in combinations:
            with self.subTest(phases=value):
                self.assert_valid(f" phases={value} 요청", expected)

    def test_specialized_phases_are_valid_alone(self) -> None:
        self.assert_valid(" phases=bugfix 요청", ("bugfix",))
        self.assert_valid(" phases=refactoring 요청", ("refactoring",))

    def test_parameter_priority_within_deterministic_scope(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name, source="explicit"):
                decision = self.evaluate(
                    skill_path,
                    " worker=claude-gemma reviewer=claude-glm "
                    "max-iterations=3 phases=design,implementation "
                    "설계하고 구현해줘",
                )
                self.assertEqual(decision.status, "VALID")
                self.assertEqual(decision.worker, "claude-gemma")
                self.assertEqual(decision.reviewer, "claude-glm")
                self.assertEqual(decision.max_iterations, 3)
                self.assertEqual(decision.phase_source, "explicit")

            with self.subTest(skill=skill_path.parent.name, source="natural"):
                decision = self.evaluate(skill_path, " 상세 설계를 작성해줘")
                self.assertEqual(decision.phases, ("design",))
                self.assertEqual(decision.phase_source, "natural_language")
                self.assertEqual(decision.worker, "claude-glm")
                self.assertEqual(decision.reviewer, "claude-gemma")
                self.assertEqual(decision.max_iterations, 5)

            with self.subTest(skill=skill_path.parent.name, source="default"):
                decision = self.evaluate(skill_path, " 이 작업을 진행해줘")
                self.assertEqual(decision.phase_source, "llm_classification")
                self.assertTrue(decision.requires_llm_phase_classification)
                self.assertEqual(decision.worker, "claude-glm")
                self.assertEqual(decision.reviewer, "claude-gemma")
                self.assertEqual(decision.max_iterations, 5)

    def test_non_phase_natural_language_parameters_remain_llm_owned(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                contract = load_policy_contract(skill_path)
                scope = contract["natural_language_automation"]
                self.assertEqual(
                    scope["deterministic_representative_terms_for"], ["phases"]
                )
                self.assertEqual(
                    scope["llm_interpretation_required_for"],
                    [
                        "worker",
                        "reviewer",
                        "max-iterations",
                        "free-form phase requests",
                    ],
                )

    def test_agent_launch_is_argument_free_for_generic_and_wrapper_commands(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                contract = load_policy_contract(skill_path)
                self.assertEqual(contract["agent_launch_arguments"], [])

                for worker, reviewer in (
                    ("claude", "codex"),
                    ("claude-opus", "codex-sol"),
                ):
                    decision = self.evaluate(
                        skill_path,
                        f" worker={worker} reviewer={reviewer} phases=analysis 요청",
                    )
                    self.assertEqual(decision.status, "VALID")
                    self.assertEqual([decision.worker], [worker])
                    self.assertEqual([decision.reviewer], [reviewer])

    # ---- OS-3 risk parameter (T-5, T-6, T-6a, T-7, T-8) -------------------------

    ORCHESTRATION = REPO_ROOT / "orca-worker-reviewer-orchestration" / "SKILL.md"
    LOOP = REPO_ROOT / "orca-worker-reviewer-loop" / "SKILL.md"

    def orchestration(self, suffix: str):
        return self.evaluate(self.ORCHESTRATION, suffix)

    def test_risk_defaults_to_high_when_omitted(self) -> None:
        decision = self.orchestration(" phases=implementation 구현해줘")
        self.assertEqual(decision.status, "VALID")
        self.assertEqual(decision.risk, "high")
        self.assertEqual(decision.risk_source, "default")

    def test_explicit_risk_overrides_the_default(self) -> None:
        """T-5."""
        for value, expected in (("low", "low"), ("medium", "medium"), ("high", "high")):
            with self.subTest(value=value):
                decision = self.orchestration(
                    f" risk={value} phases=implementation 구현해줘"
                )
                self.assertEqual(decision.status, "VALID")
                self.assertEqual(decision.risk, expected)
                self.assertEqual(decision.risk_source, "explicit")

    def test_invalid_risk_fails_closed(self) -> None:
        """T-6. Every value here is still not a level AFTER case folding, and the
        empty explicit value is one of them: `\S*` matches `risk=` with nothing
        after it, so the key is recognized and "" simply is not a member."""
        for suffix in (
            " risk=extreme phases=implementation 구현해줘",
            " risk=1 phases=implementation 구현해줘",
            " risk=lo phases=implementation 구현해줘",
            " risk=low,high phases=implementation 구현해줘",
            " risk=none phases=implementation 구현해줘",
            " risk= phases=implementation 구현해줘",
            " phases=implementation 구현해줘 risk=",
        ):
            with self.subTest(invocation=suffix):
                decision = self.orchestration(suffix)
                self.assertEqual(decision.status, "BLOCKED")
                self.assertEqual(decision.reason, "INVALID_RISK")
                self.assertFalse(decision.should_execute)
                self.assertEqual(decision.risk_source, "explicit")

    def test_omission_and_an_empty_explicit_value_are_different(self) -> None:
        """T-6a. The two cases side by side, so they can never be conflated: an
        omitted risk is the default, an explicitly empty one fails closed."""
        omitted = self.orchestration(" phases=implementation 구현해줘")
        empty = self.orchestration(" risk= phases=implementation 구현해줘")
        self.assertEqual((omitted.status, omitted.risk, omitted.risk_source),
                         ("VALID", "high", "default"))
        self.assertEqual((empty.status, empty.reason), ("BLOCKED", "INVALID_RISK"))

    def test_a_risk_token_never_reaches_natural_language_phase_detection(self) -> None:
        """T-6a, second half: the token is stripped from the body before it is
        scanned, so it can never be read as request prose."""
        decision = self.orchestration(" risk=low phases=implementation 구현해줘")
        self.assertEqual(decision.phases, ("implementation",))
        self.assertEqual(decision.phase_source, "explicit")

    def test_risk_values_are_case_folded(self) -> None:
        """T-7. Non-empty values only, deliberately disjoint from T-6. A trailing
        space terminates the token; it does not invalidate the value."""
        for suffix, expected in (
            (" risk=HIGH phases=implementation 구현해줘", "high"),
            (" risk=High phases=implementation 구현해줘", "high"),
            (" risk=Medium phases=implementation 구현해줘", "medium"),
            (" risk=LOW  phases=implementation 구현해줘", "low"),
        ):
            with self.subTest(invocation=suffix):
                decision = self.orchestration(suffix)
                self.assertEqual(decision.status, "VALID")
                self.assertEqual(decision.risk, expected)
                self.assertEqual(decision.risk_source, "explicit")

    def test_the_loop_skill_has_no_risk_axis(self) -> None:
        """T-8. A `risk=` token on the loop skill is not a parameter at all: it is
        left in the request body, exactly as before OS-3."""
        decision = self.evaluate(self.LOOP, " risk=low phases=implementation 구현해줘")
        self.assertEqual(decision.status, "VALID")
        self.assertIsNone(decision.risk)
        self.assertIsNone(decision.risk_source)

    def test_two_skills_have_identical_contracts(self) -> None:
        contracts = [load_policy_contract(path) for path in SKILL_PATHS]
        self.assertEqual(contracts[0], contracts[1])

    def test_two_skills_return_identical_policy_decisions(self) -> None:
        suffixes = (
            " help",
            " worker=claude-glm reviewer=claude-glm phases=analysis 요청",
            " worker=claude-missing phases=analysis 요청",
            " worker=bash reviewer=codex phases=analysis 요청",
            " worker=claude-opus reviewer=codex-sol phases=analysis 요청",
            " worker=../claude phases=analysis 요청",
            " max-iterations=0 phases=analysis 요청",
            " max-iterations=11 phases=analysis 요청",
            " max-iterations=many phases=analysis 요청",
            " phases=unknown 요청",
            " phases=design,unknown 요청",
            " phases=implementation,design 요청",
            " phases=test,implementation 요청",
            " phases=design,implementation 테스트까지 수행해줘",
            " phases=bugfix,test 요청",
            " phases=refactoring,implementation 요청",
            " phases=analysis,plan,design 요청",
            " phases=design,implementation 요청",
            " phases=implementation,test 요청",
            " phases=analysis,design,implementation,test 요청",
            " phases=bugfix 요청",
            " phases=refactoring 요청",
            " 상세 설계를 작성해줘",
            " 일반 작업을 진행해줘",
        )
        for suffix in suffixes:
            with self.subTest(invocation=suffix):
                decisions = [self.evaluate(path, suffix) for path in SKILL_PATHS]
                # OS-3 T-8. Whole-object equality is no longer the right claim: risk
                # is orchestration-only, so the orchestration decision resolves a
                # level and the loop decision resolves None. That asymmetry IS the
                # requirement. The shared-policy equality survives over a projection
                # that drops only the intentional fields, and two new assertions pin
                # the asymmetry itself -- strictly more than the single assertEqual.
                shared = [
                    replace(
                        decision,
                        risk=None,
                        risk_source=None,
                        # OS-4 adds three fields that are None on every legacy
                        # invocation, which is what these suffixes all are. They are
                        # projected out for the same reason risk is: the equality
                        # claim is about SHARED behaviour, not about the two skills
                        # being identical.
                        agent_profile=None,
                        agent_profile_source=None,
                        routing=None,
                    )
                    for decision in decisions
                ]
                self.assertEqual(shared[0], shared[1])
                # The loop skill NEVER resolves a risk, on any path.
                self.assertIsNone(decisions[0].risk)
                self.assertIsNone(decisions[0].risk_source)
                # The orchestration skill resolves one wherever the gate is reached.
                # HELP returns before any parameter is read, and a BLOCKED decision
                # from an earlier gate (agent/max-iterations) also returns first --
                # both legitimately carry None, so the strong assertion is made where
                # the decision actually got that far.
                if decisions[1].status == "VALID":
                    self.assertEqual(decisions[1].risk, "high")
                    self.assertEqual(decisions[1].risk_source, "default")
                else:
                    self.assertIn(decisions[1].risk, (None, "high"))


class AgentProfileInvocationTests(unittest.TestCase):
    """OS-4 at the invocation boundary: the two branches, and what separates them."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        root = Path(self.temp_directory.name)
        self.project = root / "project"
        self.home = root / "home"
        (self.project / ".orca").mkdir(parents=True)
        (self.home / ".orca").mkdir(parents=True)
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            "version: 1\n"
            "profiles:\n"
            "  diverse:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    final_review:\n      reviewer: codex\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def evaluate(self, skill_path: Path, suffix: str, *, which=None):
        def default_which(command: str):
            # Everything the profile names exists; the legacy contract defaults do
            # NOT. A profile run must not care about commands it replaced.
            return f"/usr/bin/{command}" if command in {"claude", "codex"} else None

        return evaluate_invocation(
            skill_path,
            f"/{skill_path.parent.name}{suffix}",
            project_root=self.project,
            home=self.home,
            which=which or default_which,
        )

    def test_an_unused_legacy_default_missing_from_path_does_not_block(self) -> None:
        """The defect the design review caught: a profile run must not be failed by
        `claude-glm`/`claude-gemma` being absent when nothing will run them."""
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(
                    skill_path, " profile=diverse phases=analysis 요청"
                )
                self.assertEqual(decision.status, "VALID", decision.reason)
                self.assertEqual(decision.agent_profile, "diverse")
                self.assertIsNotNone(decision.routing)

    def test_an_unsafe_optional_reviewer_fails_closed_at_the_invocation_boundary(
        self,
    ) -> None:
        """The other half of the same review defect, at the full evaluate_invocation
        boundary rather than the unit level: LOW leaves the orchestration phase
        Reviewer optional, and an unvalidated `bash` sitting there used to reach
        should_execute=True. It must now fail closed before any Run exists, with
        the same AGENT_NOT_ALLOWED reason the legacy path would report."""
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            "version: 1\n"
            "profiles:\n"
            "  unsafe:\n"
            "    defaults:\n      worker: claude\n      reviewer: bash\n"
            "    final_review:\n      reviewer: codex\n",
            encoding="utf-8",
        )
        orchestration = SKILL_PATHS[1]
        decision = self.evaluate(
            orchestration, " profile=unsafe risk=low phases=analysis 요청"
        )

        self.assertEqual(decision.reason, "AGENT_NOT_ALLOWED")
        self.assertFalse(decision.should_execute)

    def test_contract_defaults_are_never_substituted_under_a_profile(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(
                    skill_path, " profile=diverse phases=analysis 요청"
                )
                self.assertEqual(decision.worker, "")
                self.assertEqual(decision.reviewer, "")

    def test_a_legacy_invocation_resolves_no_routing(self) -> None:
        """Branch A leaves every OS-4 field None, which is what stops a routing
        block, an evidence row and a report field from appearing."""
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(
                    skill_path,
                    " phases=analysis 요청",
                    which=lambda command: f"/usr/bin/{command}",
                )
                self.assertEqual(decision.status, "VALID", decision.reason)
                self.assertIsNone(decision.agent_profile)
                self.assertIsNone(decision.routing)
                self.assertEqual(decision.worker, "claude-glm")

    def test_an_unknown_profile_fails_closed(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(skill_path, " profile=nosuch phases=analysis 요청")
                self.assertEqual(decision.reason, "UNKNOWN_AGENT_PROFILE")
                self.assertFalse(decision.should_execute)

    def test_an_empty_profile_value_is_explicit_not_omitted(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(skill_path, " profile= phases=analysis 요청")
                self.assertEqual(decision.reason, "UNKNOWN_AGENT_PROFILE")

    def test_a_profile_name_containing_a_phase_word_is_not_a_phase_request(self) -> None:
        """The token must leave `body` before natural-language phase detection."""
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            "version: 1\n"
            "profiles:\n"
            "  design_first:\n"
            "    defaults:\n      worker: claude\n      reviewer: codex\n"
            "    final_review:\n      reviewer: codex\n",
            encoding="utf-8",
        )
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(
                    skill_path, " profile=design_first phases=analysis 요청"
                )
                self.assertEqual(decision.status, "VALID", decision.reason)
                self.assertEqual(decision.phases, ("analysis",))

    def test_a_required_role_that_cannot_resolve_blocks(self) -> None:
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            "version: 1\nprofiles:\n  thin:\n    defaults:\n      worker: claude\n",
            encoding="utf-8",
        )
        # Orchestration always requires a Final Reviewer; the loop skill always
        # requires a phase Reviewer. Neither resolves here.
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(skill_path, " profile=thin phases=analysis 요청")
                self.assertEqual(decision.reason, "AGENT_ROLE_UNRESOLVED")

    def test_a_natural_language_request_defers_routing_to_finalize(self) -> None:
        """R9's second gate. When an LLM has to classify the phases, the routing
        cannot be materialized at parse time -- so the gate MOVES, it is not skipped."""
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(skill_path, " profile=diverse 작업을 진행해줘")

                self.assertEqual(decision.status, "VALID", decision.reason)
                self.assertTrue(decision.requires_llm_phase_classification)
                self.assertEqual(decision.phases, ())
                # The profile is remembered; the routing is not yet built.
                self.assertEqual(decision.agent_profile, "diverse")
                self.assertIsNone(decision.routing)

    def finalize(self, skill_path: Path, decision, phases, *, which=None):
        def default_which(command: str):
            return f"/usr/bin/{command}" if command in {"claude", "codex"} else None

        return finalize_routing(
            decision,
            phases,
            skill_path=skill_path,
            project_root=self.project,
            home=self.home,
            which=which or default_which,
        )

    def test_finalize_routing_materializes_once_the_phases_are_settled(self) -> None:
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(skill_path, " profile=diverse 작업을 진행해줘")

                final = self.finalize(skill_path, decision, ("analysis",))

                self.assertEqual(final.status, "VALID", final.reason)
                self.assertEqual(final.phases, ("analysis",))
                self.assertIsNotNone(final.routing)
                self.assertEqual(final.routing.requested_phases, ("analysis",))
                self.assertFalse(final.requires_llm_phase_classification)

    def test_finalize_routing_still_fails_closed(self) -> None:
        """Deferring the gate must not weaken it: an unresolvable required role is
        refused here exactly as it would have been at parse time."""
        (self.project / ".orca" / "agent-profiles.yaml").write_text(
            "version: 1\nprofiles:\n  thin:\n    defaults:\n      worker: claude\n",
            encoding="utf-8",
        )
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(skill_path, " profile=thin 작업을 진행해줘")

                final = self.finalize(skill_path, decision, ("analysis",))

                self.assertEqual(final.reason, "AGENT_ROLE_UNRESOLVED")
                self.assertFalse(final.should_execute)

    def test_finalize_routing_is_idempotent(self) -> None:
        """A coordinator may call it unconditionally; a decision that already has a
        routing comes back unchanged."""
        skill_path = SKILL_PATHS[0]
        decision = self.evaluate(skill_path, " profile=diverse phases=analysis 요청")
        self.assertIsNotNone(decision.routing)

        final = self.finalize(skill_path, decision, ("design",))

        self.assertIs(final, decision)

    def test_finalize_routing_leaves_a_legacy_decision_untouched(self) -> None:
        """The legacy path passes through: no profile, no routing, no change."""
        for skill_path in SKILL_PATHS:
            with self.subTest(skill=skill_path.parent.name):
                decision = self.evaluate(
                    skill_path,
                    " 작업을 진행해줘",
                    which=lambda command: f"/usr/bin/{command}",
                )

                final = self.finalize(skill_path, decision, ("analysis",))

                self.assertIs(final, decision)
                self.assertIsNone(final.routing)

    def test_the_loop_skill_also_resolves_a_profile(self) -> None:
        """OS-4 applies to BOTH skills; only the consumed keys differ."""
        decision = self.evaluate(
            SKILL_PATHS[0], " profile=diverse phases=analysis 요청"
        )

        self.assertEqual(SKILL_PATHS[0].parent.name, "orca-worker-reviewer-loop")
        self.assertIsNotNone(decision.routing)
        self.assertEqual(decision.routing.runtime, "loop")


if __name__ == "__main__":
    unittest.main()
