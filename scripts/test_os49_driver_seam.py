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
from scripts.deterministic_workflow.standalone_profile import (
    ModelSelector,
    ProfileError,
    READINESS_CHANNELS,
)
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


class ModelSelectorTests(unittest.TestCase):
    def test_only_the_structured_channel_is_declarable(self) -> None:
        self.assertEqual(READINESS_CHANNELS, ("structured",))
        ModelSelector(channel="structured", record_type="system", model_field="m")
        for channel in ("screen", "title", "stdout", ""):
            with self.subTest(channel=channel):
                with self.assertRaises(ProfileError):
                    ModelSelector(
                        channel=channel, record_type="system", model_field="m"
                    )

    def test_an_empty_record_type_or_field_is_refused(self) -> None:
        for field in ("record_type", "model_field"):
            with self.subTest(field=field):
                with self.assertRaises(ProfileError):
                    ModelSelector(
                        channel="structured",
                        **{"record_type": "system", "model_field": "m", field: ""},
                    )

    def test_the_record_type_is_matched_by_equality_not_as_a_pattern(self) -> None:
        """A pattern would match a frame that merely QUOTES the value."""
        source = Path(
            standalone_profile.__file__
        ).read_text(encoding="utf-8")
        start = source.index("class ModelSelector")
        body = source[start:source.index("class CaptureLimits")]
        self.assertIn("EQUALITY", body)
        self.assertNotIn("re.compile", body)
        self.assertNotIn(".match(", body)


class SelectorAloneEarnsNothingTests(unittest.TestCase):
    """The load-bearing honesty assertion: the OBSERVATION half never licenses delivery."""

    def base_profile(self, **overrides) -> standalone_profile.StandaloneProfile:
        fields = dict(
            driver="claude",
            binary="claude",
            supported_range=((0, 0, 0), (99, 0, 0)),
            readiness_records=(
                standalone_profile.ReadinessSelector(
                    channel="structured", record_type="system",
                    session_field="session_id",
                ),
            ),
            delivery_mode=standalone_profile.DELIVERY_MODES[0],
            # `adopted` rather than `minted_echo`: the latter additionally requires an
            # identity flag, and which binding this fixture uses is irrelevant to the
            # model-selector question under test.
            identity_binding="adopted",
            delivery_proofs=(
                standalone_profile.DeliveryProofSelector(
                    channel="structured", record_type="assistant"
                ),
            ),
        )
        fields.update(overrides)
        return standalone_profile.StandaloneProfile(**fields)

    def test_a_profile_with_a_selector_is_refused_at_preflight(self) -> None:
        profile = self.base_profile(
            model_selector=ModelSelector(
                channel="structured", record_type="system", model_field="message.model"
            )
        )
        outcome = standalone_preflight.check_profile(profile, {})
        self.assertEqual(outcome["verdict"], "fail")
        self.assertEqual(outcome["reason"], "model_selection_unsupported")

    def test_the_refusal_names_the_missing_request_half(self) -> None:
        profile = self.base_profile(
            model_selector=ModelSelector(
                channel="structured", record_type="system", model_field="m"
            )
        )
        outcome = standalone_preflight.check_profile(profile, {})
        self.assertIn("REQUESTED", outcome["evidence"]["detail"])

    def test_a_profile_with_no_selector_is_unaffected(self) -> None:
        """Both shipping profiles declare none, so their behaviour is byte-identical."""
        outcome = standalone_preflight.check_profile(self.base_profile(), {})
        self.assertNotEqual(outcome["reason"], "model_selection_unsupported")

    def test_unknown_is_never_pass(self) -> None:
        self.assertEqual(standalone_preflight.VERDICTS, ("pass", "fail", "unknown"))
        self.assertNotEqual("unknown", "pass")


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
