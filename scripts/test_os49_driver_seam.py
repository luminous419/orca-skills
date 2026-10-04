#!/usr/bin/env python3
"""OS-49: the driver/capability seam, and its honesty.

The seam's whole claim is that a capability token means BOTH legs -- a selection was
REQUESTED for this attempt, and the resolution was THEN observed. These tests assert that
an observation locator alone never earns it, that the real adapter declares nothing, and
that the reference driver really does request before it observes.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import standalone_preflight, standalone_profile
from scripts.deterministic_workflow.contracts import (
    BASE_CAPABILITIES,
    MODEL_SELECTION_VERIFIED,
)
from scripts.deterministic_workflow.fake_adapter import FakeAdapter, InProcessModelDriver
from scripts.deterministic_workflow.standalone_profile import READINESS_CHANNELS
from scripts.orca_runtime_harness import ModelSelectionTicket


def ticket(**overrides) -> ModelSelectionTicket:
    drawn = []

    def stamp() -> int:
        drawn.append(len(drawn) + 1)
        return len(drawn)

    fields = dict(
        run_id="run_t", task_id="task_t", terminal="term_t", role="worker",
        phase="implementation", attempt=1, command="claude",
        requested_model="glm-5.2",
        token="run_t:task_t:term_t:worker:implementation:1:1", stamp=stamp,
    )
    fields.update(overrides)
    return ModelSelectionTicket(**fields)


class DeadModelSelectorSurfaceIsGoneTests(unittest.TestCase):
    """OS-49 BUGFIX (review M4). The `ModelSelector` / `model_selector` / preflight trio is
    REMOVED, and this class is the lock that keeps it from coming back half-wired.

    What it was: a `ModelSelector` dataclass, an optional `StandaloneProfile.model_selector`
    field, and a `check_profile()` branch refusing a profile that carried one with
    `model_selection_unsupported`. What it was NOT: loadable. `model_selector` is absent
    from the profile loader's closed key set, and no loader, archive, digest or round-trip
    path ever set it -- so the only way to populate the field was to construct the
    dataclass directly in a test, and the only thing the preflight branch could refuse was
    a test fixture. The three tests that exercised it (one per behaviour above) therefore
    proved that a surface no user can reach refuses correctly.

    These replace them deliberately, with the inverse claim: the surface is absent. The
    alternative fix -- wiring the field through loader, schema, closed key set, archive,
    digest and round-trip -- would create a real, documented configuration option for an
    observation locator that cannot mean anything until OS-14 supplies the model-REQUEST
    half, and would then have to be either honoured or refused. Removing is the smaller
    coherent change, and OS-14 can reintroduce the field together with the half that makes
    it load.

    The honesty claim the removed tests were ALSO defending -- that an observation locator
    alone never earns `model_selection_verified` -- is unaffected and is asserted by
    `CapabilityHonestyTests.test_an_object_that_can_only_observe_is_not_a_driver` below,
    which tests the live capability derivation rather than a dead dataclass.
    """

    def test_the_selector_dataclass_is_gone(self) -> None:
        self.assertFalse(hasattr(standalone_profile, "ModelSelector"))

    def test_the_profile_carries_no_model_selector_field(self) -> None:
        fields = standalone_profile.StandaloneProfile.__dataclass_fields__
        self.assertNotIn("model_selector", fields)

    def test_the_unreachable_preflight_reason_is_gone(self) -> None:
        """A named reason no reachable configuration can produce is a claim the preflight
        vocabulary cannot keep. The ORCHESTRATION-layer `model_selection_unsupported` is a
        different vocabulary, is reachable, and is asserted still present below."""
        self.assertNotIn("model_selection_unsupported", standalone_preflight.REASONS)
        source = Path(standalone_preflight.__file__).read_text(encoding="utf-8")
        self.assertNotIn("profile.model_selector", source)

    def test_the_orchestration_barriers_own_reason_is_untouched(self) -> None:
        """The removal is scoped to the STANDALONE preflight surface. The barrier's own
        closed vocabulary -- the one a model-aware dispatch is actually refused by -- keeps
        every member, and `model_selection_unsupported` is still what a missing or
        non-callable driver produces (review M3)."""
        from scripts.orca_runtime_harness import (
            MODEL_SELECTION_FAILURE_REASONS,
            MODEL_SELECTION_UNSUPPORTED,
        )

        self.assertEqual(MODEL_SELECTION_UNSUPPORTED, "model_selection_unsupported")
        self.assertIn(MODEL_SELECTION_UNSUPPORTED, MODEL_SELECTION_FAILURE_REASONS)
        self.assertEqual(len(MODEL_SELECTION_FAILURE_REASONS), 7)

    def test_the_structured_only_channel_rule_still_stands_for_live_selectors(self) -> None:
        """`READINESS_CHANNELS` is what the removed selector validated against, and it is
        still the single declarable channel for every selector family that remains -- so
        removing the dead one did not loosen the rule it shared."""
        self.assertEqual(READINESS_CHANNELS, ("structured",))
        with self.assertRaises(standalone_profile.ProfileError):
            standalone_profile.ReadinessSelector(
                channel="screen", record_type="system", session_field="session_id"
            )


class CapabilityHonestyTests(unittest.TestCase):
    def test_the_fake_adapter_declares_the_token_only_with_a_driver(self) -> None:
        self.assertNotIn(MODEL_SELECTION_VERIFIED, FakeAdapter([]).capabilities())
        self.assertIn(
            MODEL_SELECTION_VERIFIED,
            FakeAdapter([], model_driver=InProcessModelDriver()).capabilities(),
        )

    def test_an_object_that_can_only_observe_is_not_a_driver(self) -> None:
        """The condition is a DRIVER, not an evidence source: something that can answer
        "what model is this session on" and nothing else earns nothing, because the token
        means both legs."""
        class ObserverOnly:
            def resolved_model(self, terminal: str) -> str:
                return "glm-5.2"

        self.assertNotIn(
            MODEL_SELECTION_VERIFIED,
            FakeAdapter([], model_driver=ObserverOnly()).capabilities(),
        )

    def test_the_orca_adapter_declares_no_model_capability(self) -> None:
        """This is what makes the real-runtime path fail closed. It can neither request a
        selection (no reachable flag, and no observed in-band syntax) nor observe a
        resolution (no declared locator); either reason alone forbids the token."""
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter

        adapter = OrcaAdapter.__new__(OrcaAdapter)
        adapter.settlement_journal = None
        adapter.approval_port = None
        self.assertNotIn(MODEL_SELECTION_VERIFIED, adapter.capabilities())

    def test_the_token_is_not_in_base_capabilities(self) -> None:
        self.assertNotIn(MODEL_SELECTION_VERIFIED, BASE_CAPABILITIES)

    def test_no_policy_module_reads_the_model_capability(self) -> None:
        """Requirement: model routing stays independent of phases, risk, the quality
        profile and the decision policy. The capability is read in exactly two kinds of
        place -- the declaration gate's precondition and the adapters' own
        `capabilities()` -- and never by a policy module."""
        engine = Path(standalone_profile.__file__).parent
        for name in ("graph.py", "routing.py", "executor.py", "state.py"):
            with self.subTest(module=name):
                self.assertNotIn(
                    "MODEL_SELECTION_VERIFIED", (engine / name).read_text("utf-8")
                )
                self.assertNotIn(
                    "model_selection_verified", (engine / name).read_text("utf-8")
                )


class ReferenceDriverTests(unittest.TestCase):
    def test_the_fake_driver_performs_the_request_before_the_observation(self) -> None:
        """Asserted AT THE DRIVER, so a barrier bug and a driver bug stay
        distinguishable."""
        driver = InProcessModelDriver()
        evidence = driver.select_and_verify(ticket())
        self.assertEqual([kind for kind, _ in driver.requests], ["request", "observe"])
        self.assertLess(evidence.request_stamp, evidence.observe_stamp)

    def test_the_request_leg_really_changes_the_session_state(self) -> None:
        """The observation reads back state the REQUEST put there: there is no path by
        which this driver could report a model nothing asked for."""
        driver = InProcessModelDriver()
        self.assertEqual(driver.sessions, {})
        evidence = driver.select_and_verify(ticket())
        self.assertEqual(driver.sessions["term_t"], "glm-5.2")
        self.assertEqual(evidence.resolved_model, "glm-5.2")

    def test_the_driver_owns_satisfaction_and_reports_mismatch_itself(self) -> None:
        """The harness implements no alias table, because only something that can observe a
        provider's resolution can know whether a value satisfied the request."""
        driver = InProcessModelDriver(resolve=lambda _requested: "something-else")
        evidence = driver.select_and_verify(ticket())
        self.assertEqual(evidence.state, "mismatch")

    def test_the_evidence_carries_the_tickets_own_key(self) -> None:
        issued = ticket()
        evidence = driver_evidence = InProcessModelDriver().select_and_verify(issued)
        self.assertEqual(evidence.selection_token, issued.token)
        self.assertEqual(evidence.observed_at_run, issued.run_id)
        self.assertEqual(evidence.observed_at_task, issued.task_id)
        self.assertEqual(evidence.observed_at_terminal, issued.terminal)
        self.assertEqual(evidence.observed_at_role, issued.role)
        self.assertEqual(evidence.observed_at_phase, issued.phase)
        self.assertEqual(evidence.observed_at_attempt, issued.attempt)

    def test_the_request_evidence_cell_renders_both_ordinals(self) -> None:
        evidence = InProcessModelDriver().select_and_verify(ticket())
        cell = evidence.request_evidence
        self.assertTrue(cell.startswith(evidence.selection_token))
        self.assertIn(f"{evidence.request_stamp}->{evidence.observe_stamp}", cell)


if __name__ == "__main__":
    unittest.main()
