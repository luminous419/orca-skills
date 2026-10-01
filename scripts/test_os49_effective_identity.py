#!/usr/bin/env python3
"""OS-49: the effective Worker/Reviewer identity rule, and Gate A.

The rule is CATEGORICAL -- it applies to every materialized pair, on both runtimes, at
every schema version. These tests drive the whole decision table through the ONE pure
implementation and through both production doors, and they pin the three invariants the
ticket states as completion criteria:

  * same command + two positively verified DIFFERENT models  -> independent
  * same command + same model                                -> fails closed
  * same command + unknown/unverified model evidence         -> NEVER proves independence
"""
from __future__ import annotations

import re
import textwrap
import unittest
from pathlib import Path

from scripts.agent_profile import (
    FINAL_REVIEW_SLOT,
    MODEL_EVIDENCE_MISMATCH,
    MODEL_EVIDENCE_NONE,
    MODEL_EVIDENCE_REQUESTED,
    MODEL_EVIDENCE_STALE,
    MODEL_EVIDENCE_STATES,
    MODEL_EVIDENCE_UNVERIFIABLE,
    MODEL_EVIDENCE_VERIFIED,
    MODEL_SELECTION_VERIFIED_CAPABILITY,
    REASON_MODEL_NOT_SUPPORTED,
    REASON_WORKER_REVIEWER_MUST_DIFFER,
    RUNTIME_LOOP,
    RUNTIME_ORCHESTRATION,
    SELECTION_SELECTED,
    AgentProfileError,
    AgentProfileSelection,
    ADMISSION_INDEPENDENT,
    ADMISSION_PENDING_VERIFICATION,
    ADMISSION_REFUSED,
    EFFECTIVE_IDENTITY_ADMISSION_STATES,
    declaration_evidence_state,
    effective_identity_admission,
    effective_identity_independent,
    load_agent_profiles_text,
    materialize_run_routing,
    validate_effective_identity,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PROFILE = REPO_ROOT / ".orca" / "agent-profiles.example.yaml"
CAPABLE = frozenset({MODEL_SELECTION_VERIFIED_CAPABILITY})


def load(text: str):
    return dict(load_agent_profiles_text(text, path="t.yaml", source="project_local"))


def routing(text: str, name: str, *, phases=("design",), risk="high",
            runtime=RUNTIME_ORCHESTRATION):
    selection = AgentProfileSelection(
        status=SELECTION_SELECTED, name=name, profile=load(text)[name]
    )
    return materialize_run_routing(
        runtime=runtime, selection=selection, requested_phases=phases, risk=risk
    )


def pair_profile(worker: str, reviewer: str, *, version: int = 2) -> str:
    """One v1-or-v2 document whose `design` phase is exactly the pair under test.

    Assembled by concatenation rather than through textwrap.dedent: interpolation happens
    BEFORE dedent, so a role block indented differently from the template body would
    change dedent's common prefix and silently mis-indent the whole document.
    """
    return "\n".join(
        (
            f"version: {version}",
            "profiles:",
            "  p:",
            "    phases:",
            "      design:",
            worker,
            reviewer,
            "    final_review:",
            "      reviewer: codex",
            "",
        )
    )


def role_block(role: str, command: str, model: str = "") -> str:
    if not model:
        return f"        {role}: {command}"
    return (
        f"        {role}:\n"
        f"          command: {command}\n"
        f"          model: {model}"
    )


class TheRuleAsADecisionTableTests(unittest.TestCase):
    """Every row of the rule, through the ONE pure implementation.

    Rows whose verdict only a resolved-value comparison can reach are also driven
    through the pre-delivery barrier in test_os49_delivery_barrier.py; here the pure
    function is asserted to be total and to name the right outcome for each state pair.
    """

    def test_row_1_different_commands_are_independent(self) -> None:
        for w_state, r_state in (
            (MODEL_EVIDENCE_NONE, MODEL_EVIDENCE_NONE),
            (MODEL_EVIDENCE_VERIFIED, MODEL_EVIDENCE_NONE),
            (MODEL_EVIDENCE_UNVERIFIABLE, MODEL_EVIDENCE_STALE),
        ):
            with self.subTest(w=w_state, r=r_state):
                ok, reason = effective_identity_independent(
                    ("claude-opus", "", w_state), ("codex-sol", "", r_state)
                )
                self.assertTrue(ok)
                self.assertEqual(reason, "")

    def test_row_2_same_command_two_verified_distinct_models_independent(self) -> None:
        ok, reason = effective_identity_independent(
            ("claude", "glm-5.2", MODEL_EVIDENCE_VERIFIED),
            ("claude", "glm-5.3-flash", MODEL_EVIDENCE_VERIFIED),
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_row_3_same_command_same_resolved_model_refused(self) -> None:
        ok, reason = effective_identity_independent(
            ("claude", "glm-5.2", MODEL_EVIDENCE_VERIFIED),
            ("claude", "glm-5.2", MODEL_EVIDENCE_VERIFIED),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_row_3prime_same_command_same_declared_model_refused(self) -> None:
        ok, reason = effective_identity_independent(
            ("claude", "glm-5.2", MODEL_EVIDENCE_REQUESTED),
            ("claude", "glm-5.2", MODEL_EVIDENCE_REQUESTED),
        )
        self.assertFalse(ok)
        self.assertEqual(reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_row_4_same_command_no_models_refused(self) -> None:
        ok, reason = effective_identity_independent(
            ("claude", "", MODEL_EVIDENCE_NONE), ("claude", "", MODEL_EVIDENCE_NONE)
        )
        self.assertFalse(ok)
        self.assertEqual(reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_rows_5_and_6_mixed_requested_and_verified_refused(self) -> None:
        for w_state, r_state in (
            (MODEL_EVIDENCE_REQUESTED, MODEL_EVIDENCE_VERIFIED),
            (MODEL_EVIDENCE_VERIFIED, MODEL_EVIDENCE_REQUESTED),
        ):
            with self.subTest(w=w_state, r=r_state):
                ok, _ = effective_identity_independent(
                    ("claude", "glm-5.2", w_state),
                    ("claude", "glm-5.3-flash", r_state),
                )
                self.assertFalse(ok)

    def test_row_7_two_distinct_declared_models_are_pending_never_independent(self) -> None:
        """CORRECTED in OS-49 iteration 2 (review F-001). This test previously asserted
        `ok is True` here, which is the prohibited positive result: `requested` is a
        DECLARATION, nothing has been observed, and two distinct declared tokens can
        alias onto one resolved model. The old assertion encoded the defect rather than
        detecting it.

        The row is PENDING, not refused and not independent: refusing it outright would
        refuse every model-aware pair and the feature could never route, so the verdict
        is DEFERRED to the pre-delivery barrier -- which must settle it on RESOLVED
        values BEFORE the first delivery of either role.
        """
        declared = (
            ("claude", "glm-5.2", MODEL_EVIDENCE_REQUESTED),
            ("claude", "glm-5.3-flash", MODEL_EVIDENCE_REQUESTED),
        )
        admission, reason = effective_identity_admission(*declared)
        self.assertEqual(admission, ADMISSION_PENDING_VERIFICATION)
        self.assertEqual(reason, "")
        # And the independence question itself answers NO, which is the invariant.
        ok, independence_reason = effective_identity_independent(*declared)
        self.assertFalse(ok)
        self.assertEqual(independence_reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_row_8_one_side_declares_no_model_refused(self) -> None:
        for worker, reviewer in (
            (("claude", "glm-5.2", MODEL_EVIDENCE_VERIFIED),
             ("claude", "", MODEL_EVIDENCE_NONE)),
            (("claude", "", MODEL_EVIDENCE_NONE),
             ("claude", "glm-5.2", MODEL_EVIDENCE_VERIFIED)),
        ):
            with self.subTest(worker=worker, reviewer=reviewer):
                ok, reason = effective_identity_independent(worker, reviewer)
                self.assertFalse(ok)
                self.assertEqual(reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_row_9_unverifiable_is_named_separately(self) -> None:
        for worker, reviewer in (
            (("claude", "glm-5.2", MODEL_EVIDENCE_UNVERIFIABLE),
             ("claude", "glm-5.3-flash", MODEL_EVIDENCE_REQUESTED)),
            (("claude", "glm-5.2", MODEL_EVIDENCE_REQUESTED),
             ("claude", "glm-5.3-flash", MODEL_EVIDENCE_UNVERIFIABLE)),
        ):
            with self.subTest(worker=worker):
                ok, reason = effective_identity_independent(worker, reviewer)
                self.assertFalse(ok)
                self.assertEqual(reason, REASON_MODEL_NOT_SUPPORTED)

    def test_rows_10_and_11_mismatch_and_stale_never_prove_independence(self) -> None:
        for state in (MODEL_EVIDENCE_MISMATCH, MODEL_EVIDENCE_STALE):
            with self.subTest(state=state):
                ok, reason = effective_identity_independent(
                    ("claude", "glm-5.2", state),
                    ("claude", "glm-5.3-flash", MODEL_EVIDENCE_VERIFIED),
                )
                self.assertFalse(ok)
                self.assertEqual(reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_insufficient_evidence_never_proves_independence(self) -> None:
        """The ticket's invariant, as ONE assertion over every non-verified state: two
        DIFFERENT declared models on one command never prove independence unless BOTH
        sides are positively verified."""
        # CORRECTED in iteration 2: MODEL_EVIDENCE_REQUESTED was missing from this
        # tuple, which is why the test could not fail on review finding F-001. It is
        # the FIRST member now -- a declaration is the weakest evidence of all.
        inadmissible = (
            MODEL_EVIDENCE_REQUESTED, MODEL_EVIDENCE_NONE, MODEL_EVIDENCE_MISMATCH,
            MODEL_EVIDENCE_UNVERIFIABLE, MODEL_EVIDENCE_STALE,
        )
        for state in inadmissible:
            for counterpart in (MODEL_EVIDENCE_VERIFIED, state):
                with self.subTest(state=state, counterpart=counterpart):
                    ok, _ = effective_identity_independent(
                        ("claude", "glm-5.2", state),
                        ("claude", "glm-5.3-flash", counterpart),
                    )
                    self.assertFalse(ok)

    def test_only_two_verified_distinct_models_admit_a_same_command_pair(self) -> None:
        """The whole rule as ONE statement about the same-command half: across every
        state pair, ADMISSION_INDEPENDENT is reachable ONLY when both sides are
        `verified` with distinct models, and the classification is total."""
        for worker_state in MODEL_EVIDENCE_STATES:
            for reviewer_state in MODEL_EVIDENCE_STATES:
                with self.subTest(worker=worker_state, reviewer=reviewer_state):
                    admission, reason = effective_identity_admission(
                        ("claude", "glm-5.2", worker_state),
                        ("claude", "glm-5.3-flash", reviewer_state),
                    )
                    self.assertIn(admission, EFFECTIVE_IDENTITY_ADMISSION_STATES)
                    both_verified = (
                        worker_state == reviewer_state == MODEL_EVIDENCE_VERIFIED
                    )
                    self.assertEqual(
                        admission == ADMISSION_INDEPENDENT, both_verified
                    )
                    if admission == ADMISSION_REFUSED:
                        self.assertNotEqual(reason, "")
                    else:
                        self.assertEqual(reason, "")

    def test_an_unknown_evidence_state_is_refused_not_guessed(self) -> None:
        with self.assertRaises(AgentProfileError):
            effective_identity_independent(
                ("claude", "glm-5.2", "probably_fine"),
                ("claude", "glm-5.3-flash", MODEL_EVIDENCE_VERIFIED),
            )

    def test_declaration_evidence_state_is_the_two_declaration_time_values(self) -> None:
        self.assertEqual(declaration_evidence_state(""), MODEL_EVIDENCE_NONE)
        self.assertEqual(declaration_evidence_state("glm-5.2"), MODEL_EVIDENCE_REQUESTED)


class GateATests(unittest.TestCase):
    def test_same_command_no_model_is_refused_before_any_run(self) -> None:
        document = pair_profile(role_block("worker", "claude"),
                               role_block("reviewer", "claude"), version=1)
        with self.assertRaises(AgentProfileError) as caught:
            validate_effective_identity(routing(document, "p"))
        self.assertEqual(caught.exception.reason, REASON_WORKER_REVIEWER_MUST_DIFFER)
        self.assertIn("design", str(caught.exception))

    def test_the_rule_is_categorical_across_schema_versions(self) -> None:
        """A `version: 1` document is judged by the identical rule. O2 buys PARSING
        isolation, never policy isolation."""
        for version in (1, 2):
            with self.subTest(version=version):
                document = pair_profile(role_block("worker", "claude"),
                                       role_block("reviewer", "claude"),
                                       version=version)
                with self.assertRaises(AgentProfileError) as caught:
                    validate_effective_identity(routing(document, "p"))
                self.assertEqual(
                    caught.exception.reason, REASON_WORKER_REVIEWER_MUST_DIFFER
                )

    def test_same_command_same_declared_model_refused_at_gate_a(self) -> None:
        document = pair_profile(
            role_block("worker", "claude", "glm-5.2"),
            role_block("reviewer", "claude", "glm-5.2"),
        )
        with self.assertRaises(AgentProfileError) as caught:
            validate_effective_identity(
                routing(document, "p"), model_capabilities=CAPABLE
            )
        self.assertEqual(caught.exception.reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_same_command_one_model_missing_refused_at_gate_a(self) -> None:
        document = pair_profile(
            role_block("worker", "claude", "glm-5.2"),
            role_block("reviewer", "claude"),
        )
        with self.assertRaises(AgentProfileError) as caught:
            validate_effective_identity(
                routing(document, "p"), model_capabilities=CAPABLE
            )
        self.assertEqual(caught.exception.reason, REASON_WORKER_REVIEWER_MUST_DIFFER)

    def test_two_distinct_declared_models_pass_gate_a_as_PENDING_not_admitted(self) -> None:
        """Gate A does not refuse the pair -- and does not admit it either. The run
        carries the obligation, which `pending_admission_phases()` names and which only
        the runtime's pre-delivery barrier can discharge."""
        document = pair_profile(
            role_block("worker", "claude", "glm-5.2"),
            role_block("reviewer", "claude", "glm-5.3-flash"),
        )
        run_routing = routing(document, "p")
        validate_effective_identity(run_routing, model_capabilities=CAPABLE)
        self.assertEqual(run_routing.pending_admission_phases(), ("design",))

    def test_a_distinct_command_pair_carries_no_pending_obligation(self) -> None:
        """The compatibility half: distinct commands are independent on row 1, so no
        obligation exists and nothing about their lifecycle changes."""
        for worker, reviewer in (
            (role_block("worker", "claude-opus"), role_block("reviewer", "codex-sol")),
            (role_block("worker", "claude", "glm-5.2"),
             role_block("reviewer", "codex", "gpt-5.6-sol")),
        ):
            with self.subTest(worker=worker):
                run_routing = routing(pair_profile(worker, reviewer), "p")
                validate_effective_identity(
                    run_routing, model_capabilities=CAPABLE
                )
                self.assertEqual(run_routing.pending_admission_phases(), ())

    def test_a_declared_model_without_a_capability_is_refused(self) -> None:
        """The DEFAULT, and the reason the real runtime path is honestly fail-closed:
        both production doors offer no capability, so a declared model never routes
        there."""
        document = pair_profile(
            role_block("worker", "claude", "glm-5.2"),
            role_block("reviewer", "claude", "glm-5.3-flash"),
        )
        with self.assertRaises(AgentProfileError) as caught:
            validate_effective_identity(routing(document, "p"))
        self.assertEqual(caught.exception.reason, REASON_MODEL_NOT_SUPPORTED)
        self.assertIn("no model-selection capability", str(caught.exception))

    def test_distinct_model_pinned_wrappers_still_route(self) -> None:
        """The ticket's explicit compatibility clause: the two commands THIS repository's
        own agents use are model-pinned wrappers, declare no model, and must be
        unaffected."""
        document = pair_profile(
            role_block("worker", "claude-opus"),
            role_block("reviewer", "codex-sol"),
        )
        route = routing(document, "p")
        validate_effective_identity(route)
        self.assertFalse(route.is_model_aware)
        self.assertEqual(route.effective_identity("design", "worker"),
                         ("claude-opus", ""))

    def test_a_legacy_routing_is_never_judged(self) -> None:
        legacy = materialize_run_routing(
            runtime=RUNTIME_ORCHESTRATION,
            selection=AgentProfileSelection(status="omitted"),
            requested_phases=("design",),
            risk="high",
            explicit_worker="claude",
            explicit_reviewer="claude",
        )
        self.assertTrue(legacy.is_legacy)
        validate_effective_identity(legacy)       # no profile, nothing to judge here

    def test_low_risk_has_no_pair_to_judge(self) -> None:
        """At LOW risk the orchestration runtime materializes no phase Reviewer, so there
        is no pair. That is pre-OS-49 RISK behaviour about which roles exist, not model
        routing reading risk."""
        document = pair_profile(role_block("worker", "claude"),
                               role_block("reviewer", "claude"), version=1)
        validate_effective_identity(routing(document, "p", risk="low"))
        for risk in ("medium", "high"):
            with self.subTest(risk=risk):
                with self.assertRaises(AgentProfileError):
                    validate_effective_identity(routing(document, "p", risk=risk))

    def test_gate_a_scope_is_required_entries_only(self) -> None:
        """An unrequested phase's same-command pair does not fail the run -- the same
        narrowing the PATH check already uses, and for the same reason."""
        document = textwrap.dedent(
            """\
            version: 1
            profiles:
              p:
                defaults:
                  worker: claude
                  reviewer: codex
                phases:
                  refactoring:
                    worker: claude
                    reviewer: claude
                final_review:
                  reviewer: codex
            """
        )
        validate_effective_identity(routing(document, "p", phases=("design",)))
        with self.assertRaises(AgentProfileError):
            validate_effective_identity(routing(document, "p", phases=("refactoring",)))

    def test_the_final_reviewer_is_outside_the_independence_rule(self) -> None:
        """No contract requires the Final Reviewer to differ from a phase Worker, and the
        ticket states the invariant for Worker/Reviewer only."""
        document = textwrap.dedent(
            """\
            version: 1
            profiles:
              p:
                defaults:
                  worker: claude
                  reviewer: codex
                final_review:
                  reviewer: claude
            """
        )
        route = routing(document, "p")
        validate_effective_identity(route)
        self.assertEqual(route.command_for(FINAL_REVIEW_SLOT, "final_reviewer"),
                         "claude")
        self.assertEqual(route.command_for("design", "worker"), "claude")

    def test_an_unresolved_required_role_is_not_this_gates_business(self) -> None:
        """It must report a MISSING role, not WORKER_REVIEWER_MUST_DIFFER."""
        document = textwrap.dedent(
            """\
            version: 1
            profiles:
              p:
                defaults:
                  worker: claude
            """
        )
        validate_effective_identity(routing(document, "p"))

    def test_model_routing_reads_no_other_axis(self) -> None:
        """The rule reads (command, model, evidence state) and nothing else: the same
        declared pair gets the same verdict at every risk level that materializes it."""
        document = pair_profile(
            role_block("worker", "claude", "glm-5.2"),
            role_block("reviewer", "claude", "glm-5.2"),
        )
        for risk in ("medium", "high"):
            with self.subTest(risk=risk):
                with self.assertRaises(AgentProfileError) as caught:
                    validate_effective_identity(
                        routing(document, "p", risk=risk), model_capabilities=CAPABLE
                    )
                self.assertEqual(
                    caught.exception.reason, REASON_WORKER_REVIEWER_MUST_DIFFER
                )


class ShippedExampleProfileTests(unittest.TestCase):
    """The direct mitigation for shipping an example the shipped gate refuses."""

    def test_shipped_example_profiles_pass_gate_a(self) -> None:
        text = EXAMPLE_PROFILE.read_text(encoding="utf-8")
        profiles = load(text)
        self.assertTrue(profiles, "the shipped example declares no profile")
        phases = ("analysis", "plan", "design", "implementation", "test",
                  "bugfix", "refactoring")
        for name in profiles:
            for runtime in (RUNTIME_ORCHESTRATION, RUNTIME_LOOP):
                for risk in ("low", "medium", "high"):
                    with self.subTest(profile=name, runtime=runtime, risk=risk):
                        route = materialize_run_routing(
                            runtime=runtime,
                            selection=AgentProfileSelection(
                                status=SELECTION_SELECTED,
                                name=name,
                                profile=profiles[name],
                            ),
                            requested_phases=phases,
                            risk=risk,
                        )
                        validate_effective_identity(route)

    def test_the_shipped_example_declares_no_model(self) -> None:
        """A declared model is refused on every real placement in this release, so an
        example that declared one would ship a profile the shipped gate rejects."""
        for name, profile in load(EXAMPLE_PROFILE.read_text(encoding="utf-8")).items():
            with self.subTest(profile=name):
                values = (
                    [v for _k, v in profile.defaults]
                    + [v for _p, roles in profile.phases for _k, v in roles]
                    + [v for _k, v in profile.final_review]
                )
                self.assertEqual([v.model for v in values if v.model], [])


class LegacyPathParityTests(unittest.TestCase):
    """The profile-less path routes through the SAME implementation of the rule, so the
    two cannot drift. Its behaviour must be byte-identical to the comparison it replaced.
    """

    def test_legacy_same_command_refusal_matches_the_pure_rule(self) -> None:
        for worker, reviewer, expected in (
            ("claude", "claude", False),
            ("claude", "codex", True),
            ("claude-glm", "claude-glm", False),
            ("claude-glm", "claude-gemma", True),
        ):
            with self.subTest(worker=worker, reviewer=reviewer):
                ok, _ = effective_identity_independent(
                    (worker, "", MODEL_EVIDENCE_NONE),
                    (reviewer, "", MODEL_EVIDENCE_NONE),
                )
                self.assertEqual(ok, expected)
                # The pre-OS-49 comparison, for reference: it must agree everywhere.
                self.assertEqual(ok, worker != reviewer)


if __name__ == "__main__":
    unittest.main()
