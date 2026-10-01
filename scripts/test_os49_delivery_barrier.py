#!/usr/bin/env python3
"""OS-49: the pre-delivery model-identity barrier.

A Worker or Reviewer task must NEVER be delivered before the requested model has been
positively verified for that attempt. These tests drive the real barrier through the real
`start_worker()` on both delivery rungs and through BOTH centralized dispatch initiators,
and assert on the recorded Orca command stream that nothing was delivered when it refuses.

The ORDERING GROUP near the bottom is the part that cannot be satisfied by observation
alone: every one of those tests fails if a driver is allowed to report a verified model
without having requested a selection for THIS attempt.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from scripts import decision_gate, run_logging
from scripts.agent_profile import (
    MODEL_EVIDENCE_NONE,
    MODEL_EVIDENCE_REQUESTED,
    MODEL_EVIDENCE_STALE,
    MODEL_EVIDENCE_UNVERIFIABLE,
    MODEL_EVIDENCE_VERIFIED,
    MODEL_SELECTION_VERIFIED_CAPABILITY,
    REASON_WORKER_REVIEWER_MUST_DIFFER,
    RUNTIME_ORCHESTRATION,
    SELECTION_SELECTED,
    AgentProfileSelection,
    load_agent_profiles_text,
    materialize_run_routing,
)
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.orca_runtime_harness import (
    MODEL_SELECTION_AMBIGUOUS,
    MODEL_SELECTION_MISMATCH,
    MODEL_SELECTION_PAIR_UNADMITTED,
    MODEL_SELECTION_REQUEST_ABSENT,
    MODEL_SELECTION_REQUEST_METHODS,
    MODEL_SELECTION_REQUEST_STALE,
    MODEL_SELECTION_UNSUPPORTED,
    MODEL_SELECTION_UNVERIFIED,
    ModelEvidence,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
)
from scripts.test_orca_runtime_contract import RecordingExec, SequentialTerminalExec

#: The commands that DELIVER. The barrier must precede every one of them, on both rungs.
DELIVERY_VERBS = {"worker-start", "dispatch", "send"}

SPLIT_PROFILE = (
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
    "      reviewer:\n"
    "        command: claude\n"
    "        model: glm-5.2\n"
)

LEGACY_PROFILE = (
    "version: 1\n"
    "profiles:\n"
    "  plain:\n"
    "    phases:\n"
    "      implementation:\n"
    "        worker: claude\n"
    "        reviewer: codex\n"
    "    final_review:\n"
    "      reviewer: codex\n"
)


def routing_from(text: str, name: str, *, phases=("implementation",), risk="high"):
    profiles = dict(
        load_agent_profiles_text(text, path="t.yaml", source="project_local")
    )
    return materialize_run_routing(
        runtime=RUNTIME_ORCHESTRATION,
        selection=AgentProfileSelection(
            status=SELECTION_SELECTED, name=name, profile=profiles[name]
        ),
        requested_phases=phases,
        risk=risk,
    )


class RecordingDriver:
    """A driver whose every action is recorded, so ORDER is assertable at the driver.

    `evidence` is a callable `(ticket, request_stamp, observe_stamp) -> ModelEvidence`, so
    a test can construct any shape of result -- including ones a conforming driver would
    never produce -- while the recorded call order stays truthful.
    """

    def __init__(self, evidence, *, stamps: int = 2) -> None:
        self.evidence = evidence
        self.stamps = stamps
        self.calls: list[tuple[str, int]] = []

    def select_and_verify(self, ticket):
        drawn: list[int] = []
        for index in range(self.stamps):
            ordinal = ticket.stamp()
            drawn.append(ordinal)
            self.calls.append(("request" if index == 0 else "observe", ordinal))
        request_stamp = drawn[0] if drawn else 0
        observe_stamp = drawn[1] if len(drawn) > 1 else 0
        return self.evidence(ticket, request_stamp, observe_stamp)


def conforming(ticket, request_stamp=0, observe_stamp=0, *,
               state=MODEL_EVIDENCE_VERIFIED, resolved=None, **overrides):
    """Evidence a CONFORMING driver would return, with any field overridable.

    The two ordinals are accepted positionally for readability but are written into the
    same `fields` dict every override lands in, so a test may override them by name
    without colliding with the positional values.
    """
    fields = dict(
        state=state,
        requested_model=ticket.requested_model,
        resolved_model=ticket.requested_model if resolved is None else resolved,
        selection_token=ticket.token,
        request_method=MODEL_SELECTION_REQUEST_METHODS[0],
        request_stamp=request_stamp,
        observation_method="in_process_session_state",
        observe_stamp=observe_stamp,
        capability=MODEL_SELECTION_VERIFIED_CAPABILITY,
        observed_at_run=ticket.run_id,
        observed_at_task=ticket.task_id,
        observed_at_terminal=ticket.terminal,
        observed_at_role=ticket.role,
        observed_at_phase=ticket.phase,
        observed_at_attempt=ticket.attempt,
    )
    fields.update(overrides)
    return ModelEvidence(**fields)


class BarrierTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(
        self,
        recorder: RecordingExec,
        *,
        routing=None,
        model_driver=None,
        phases=("implementation",),
    ) -> OrcaRuntimeHarness:
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(
                self.artifact_dir, agent_routing=routing, model_driver=model_driver
            )
        harness._exec_orca = recorder
        harness.run_owner, harness.run_id = "term_owner", "run_barrier"
        harness.requested_phases = phases
        run_logging.open_decision_ledger(
            harness.run_id,
            base=self.artifact_dir,
            phases=phases,
            risk=harness.risk or "",
            ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
        )
        return harness

    def admit_counterpart(self, harness, role, *, phase="implementation", attempt=1,
                          task_id="task_admit") -> str:
        """Create `role`'s COUNTERPART session and run the OS-49 admission pre-pass on it.

        Required by review F-001 before any SAME-COMMAND model-aware delivery: the pair
        is routable only once BOTH effective identities are positively verified and
        distinct, and that has to happen before the first delivery of either role.
        `verify_model_identity()` delivers nothing, so calling this never makes a
        "nothing was delivered" assertion vacuous.

        Returns the counterpart's handle. Needs a recorder that hands out a fresh handle
        per `terminal create` -- the two sessions are alive at the same time by
        construction, which the base RecordingExec's single pinned handle cannot model.
        """
        counterpart = {"worker": "reviewer", "reviewer": "worker"}[role]
        handle = harness.create_fake_terminal(
            counterpart,
            "pass" if counterpart == "reviewer" else "complete",
            iteration=attempt,
            phase=phase,
        )
        harness.verify_model_identity(
            task_id, handle, role=counterpart, phase=phase, attempt=attempt
        )
        return handle

    def assertNothingDelivered(self, recorder: RecordingExec) -> None:
        delivered = sorted(set(recorder.verbs) & DELIVERY_VERBS)
        self.assertEqual(
            delivered, [],
            f"the barrier refused but these delivery commands still ran: {delivered}",
        )

    def refuse(self, *, driver, role="worker", phase="implementation", attempt=1,
               routing_text=SPLIT_PROFILE, routing_name="split"):
        recorder = RecordingExec()
        harness = self.build(
            recorder,
            routing=routing_from(routing_text, routing_name),
            model_driver=driver,
        )
        handle = harness.create_fake_terminal(role, "complete", iteration=attempt,
                                             phase=phase)
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker("task_g", handle, "spec", role=role, phase=phase,
                                 attempt=attempt)
        self.assertNothingDelivered(recorder)
        return str(caught.exception), harness, recorder

    def assertRefusedWith(self, reason: str, **kwargs) -> str:
        message, _harness, _recorder = self.refuse(**kwargs)
        self.assertTrue(
            message.startswith(reason),
            f"expected refusal {reason!r}, got: {message}",
        )
        return message


class LegacyAndModelLessPathsTests(BarrierTestCase):
    def test_no_routing_at_all_is_byte_identical(self) -> None:
        """The legacy path returns at the barrier's first line: no driver is consulted and
        no new refusal exists."""
        recorder = RecordingExec()
        harness = self.build(recorder)
        handle = harness.create_fake_terminal("worker", "complete", iteration=1)
        self.assertEqual(
            harness.start_worker("task_g", handle, "spec"), ("ctx_1", True)
        )
        self.assertEqual(recorder.verbs, ["create", "wait", "worker-start"])

    def test_a_model_less_profile_never_consults_the_driver(self) -> None:
        recorder = RecordingExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder,
            routing=routing_from(LEGACY_PROFILE, "plain"),
            model_driver=driver,
        )
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        self.assertEqual(
            harness.start_worker("task_g", handle, "spec", role="worker",
                                 phase="implementation", attempt=1),
            ("ctx_1", True),
        )
        self.assertEqual(driver.requests, [])
        self.assertEqual(
            harness.ledger_terminal(handle)["model_state"], MODEL_EVIDENCE_NONE
        )

    def test_a_model_less_role_in_a_model_aware_run_is_not_gated(self) -> None:
        """The barrier is per-ROLE, keyed on that role's own declared model."""
        document = (
            "version: 2\n"
            "profiles:\n"
            "  mixed:\n"
            "    phases:\n"
            "      implementation:\n"
            "        worker:\n"
            "          command: claude\n"
            "          model: glm-5.2\n"
            "        reviewer: codex\n"
            "    final_review:\n"
            "      reviewer: codex\n"
        )
        recorder = RecordingExec()
        harness = self.build(
            recorder, routing=routing_from(document, "mixed"), model_driver=None
        )
        handle = harness.create_fake_terminal("reviewer", "pass", iteration=1,
                                             phase="implementation")
        self.assertEqual(
            harness.start_worker("task_g", handle, "spec", role="reviewer",
                                 phase="implementation", attempt=1),
            ("ctx_1", True),
        )


class PositiveVerificationTests(BarrierTestCase):
    def test_one_executable_two_verified_models_both_deliver(self) -> None:
        """The capability OS-49 exists for, end to end through the real barrier: the SAME
        `claude` executable runs the Worker on one verified model and the Reviewer on
        another, and both dispatches are delivered.

        The lifecycle is the one review F-001 requires, and the order is the point:
        BOTH sessions exist and BOTH models are positively verified and compared BEFORE
        the first delivery of either role. Nothing is delivered during admission.
        """
        recorder = SequentialTerminalExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        handles = {
            role: harness.create_fake_terminal(role, mode, iteration=1,
                                               phase="implementation")
            for role, mode in (("worker", "complete"), ("reviewer", "pass"))
        }
        for role, handle in handles.items():
            harness.verify_model_identity("task_g", handle, role=role,
                                          phase="implementation", attempt=1)
        self.assertNothingDelivered(recorder)
        for role, model in (("worker", "glm-5.2"), ("reviewer", "glm-5.3-flash")):
            with self.subTest(role=role):
                handle = handles[role]
                dispatch_id, supervised = harness.start_worker(
                    "task_g", handle, "spec", role=role, phase="implementation",
                    attempt=1,
                )
                self.assertEqual((dispatch_id, supervised), ("ctx_1", True))
                row = harness.ledger_terminal(handle)
                self.assertEqual(row["requested_model"], model)
                self.assertEqual(row["resolved_model"], model)
                self.assertEqual(row["model_state"], MODEL_EVIDENCE_VERIFIED)
                self.assertEqual(
                    row["model_request_method"], MODEL_SELECTION_REQUEST_METHODS[0]
                )
                self.assertEqual(row["model_observed_at_dispatch"], "ctx_1")

    def test_the_model_never_reaches_the_terminal_create_command(self) -> None:
        """A model is a separate ledger field, never concatenated into argv."""
        recorder = RecordingExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        harness.create_fake_terminal("worker", "complete", iteration=1,
                                     phase="implementation")
        created = next(c for c in recorder.commands if c[1] == "create")
        command = created[created.index("--command") + 1]
        self.assertEqual(command, "claude")
        self.assertNotIn("glm", " ".join(created))

    def test_final_reviewer_model_routing_is_verified_before_delivery(self) -> None:
        recorder = RecordingExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        handle = harness.create_fake_terminal("final_reviewer", "pass", iteration=1,
                                             phase="final_review")
        harness.start_worker("task_g", handle, "spec", role="final_reviewer",
                             phase="final_review", attempt=1)
        self.assertEqual(
            harness.ledger_terminal(handle)["resolved_model"], "glm-5.2"
        )


class FailClosedBarrierTests(BarrierTestCase):
    def test_declared_model_without_a_driver_is_refused(self) -> None:
        message = self.assertRefusedWith(MODEL_SELECTION_UNSUPPORTED, driver=None)
        self.assertIn("a selection cannot be requested", message)

    def test_requested_but_never_resolved_is_refused(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_UNVERIFIED,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(t, rq, ob, state=MODEL_EVIDENCE_REQUESTED),
            ),
        )

    def test_resolved_model_mismatch_is_refused(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_MISMATCH,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(
                    t, rq, ob, state="mismatch", resolved="glm-9.9"
                ),
            ),
        )

    def test_a_driver_that_does_not_echo_the_requested_model_is_refused(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_MISMATCH,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(t, rq, ob, requested_model="something-else"),
            ),
        )

    def test_an_unverifiable_state_is_refused_as_unsupported(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_UNSUPPORTED,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(
                    t, rq, ob, state=MODEL_EVIDENCE_UNVERIFIABLE
                ),
            ),
        )

    def test_a_stale_state_is_refused(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_UNVERIFIED,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(t, rq, ob, state=MODEL_EVIDENCE_STALE),
            ),
        )

    def test_a_synthetic_or_malformed_resolved_value_is_refused(self) -> None:
        for bad in ("<synthetic>", "", "glm 5.2", "../glm"):
            with self.subTest(resolved=bad):
                self.assertRefusedWith(
                    MODEL_SELECTION_UNVERIFIED,
                    driver=RecordingDriver(
                        lambda t, rq, ob, bad=bad: conforming(t, rq, ob, resolved=bad),
                    ),
                )

    def test_a_wrong_evidence_type_is_refused_never_raised(self) -> None:
        """The same rule the reuse gate already applies to a mis-typed observation: a
        wrong type must be REFUSED by name, not produce a TypeError."""
        for wrong in ({"state": "verified"}, "verified", None, 7):
            with self.subTest(evidence=wrong):
                self.assertRefusedWith(
                    MODEL_SELECTION_UNVERIFIED,
                    driver=RecordingDriver(lambda t, rq, ob, w=wrong: w),
                )

    def test_an_unknown_evidence_state_is_refused(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_UNVERIFIED,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(t, rq, ob, state="probably_fine"),
            ),
        )

    def test_evidence_for_a_different_terminal_role_or_attempt_is_refused(self) -> None:
        for field, value in (
            ("observed_at_terminal", "term_other"),
            ("observed_at_role", "reviewer"),
            ("observed_at_phase", "design"),
            ("observed_at_attempt", 7),
            ("observed_at_task", "task_other"),
            ("observed_at_run", "run_other"),
        ):
            with self.subTest(field=field):
                self.assertRefusedWith(
                    MODEL_SELECTION_REQUEST_STALE,
                    driver=RecordingDriver(
                        lambda t, rq, ob, f=field, v=value: conforming(
                            t, rq, ob, **{f: v}
                        ),
                    ),
                )

    def test_the_identity_arguments_cannot_be_silently_omitted(self) -> None:
        """A future caller that forgets to thread (role, phase, attempt) gets a REFUSED
        dispatch, which is observable, rather than an unverified delivery, which is not.
        """
        for kwargs in (
            {},
            {"role": "worker"},
            {"role": "worker", "phase": "implementation"},
            {"phase": "implementation", "attempt": 1},
        ):
            with self.subTest(kwargs=sorted(kwargs)):
                recorder = RecordingExec()
                harness = self.build(
                    recorder,
                    routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=InProcessModelDriver(),
                )
                handle = harness.create_fake_terminal(
                    "worker", "complete", iteration=1, phase="implementation"
                )
                recorder.commands.clear()
                with self.assertRaises(OrcaRuntimeError) as caught:
                    harness.start_worker("task_g", handle, "spec", **kwargs)
                self.assertTrue(
                    str(caught.exception).startswith(MODEL_SELECTION_UNVERIFIED)
                )
                self.assertNothingDelivered(recorder)


class ResolvedValueIndependenceTests(BarrierTestCase):
    def test_two_distinct_declared_tokens_resolving_to_one_model_are_refused(self) -> None:
        """The half the declaration-time gate structurally cannot see. Both sides declare
        DIFFERENT models and pass Gate A; an ALIAS collapses them onto one RESOLVED model,
        and only a resolved-value comparison refuses that.

        The driver here reports `verified` even though resolved != requested, which is the
        realistic alias case rather than a misbehaving driver: `--model opus` has been
        measured resolving to `claude-opus-5`, and only the driver can know that satisfies
        the request. The harness implements no alias table -- which is exactly why the
        resolved-value comparison, not the declared one, has to be the deciding check.

        CORRECTED in iteration 2 (review F-001). This test used to DELIVER the Worker
        first and assert only that the Reviewer, arriving second, was refused -- i.e. it
        documented the defect: the Worker had already run on a pair whose independence
        was never positively established. The refusal now happens during ADMISSION, and
        the assertion is that NOTHING was ever delivered for EITHER role.
        """
        recorder = SequentialTerminalExec()
        driver = RecordingDriver(
            lambda t, rq, ob: conforming(t, rq, ob, resolved="glm-5.2")
        )
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        worker = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        reviewer = harness.create_fake_terminal("reviewer", "pass", iteration=1,
                                               phase="implementation")
        harness.verify_model_identity("task_g", worker, role="worker",
                                      phase="implementation", attempt=1)
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity("task_g", reviewer, role="reviewer",
                                          phase="implementation", attempt=1)
        self.assertTrue(
            str(caught.exception).startswith(REASON_WORKER_REVIEWER_MUST_DIFFER)
        )
        self.assertNothingDelivered(recorder)
        # And the Worker cannot be delivered either: its counterpart holds no verified
        # evidence, because the refused admission recorded nothing.
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker("task_g", worker, "spec", role="worker",
                                 phase="implementation", attempt=1)
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED)
        )
        self.assertNothingDelivered(recorder)

    def test_contradictory_resolved_values_for_one_identity_are_refused(self) -> None:
        """A session CAN report two model ids; the second attempt for the same
        (phase, role) resolving differently is ambiguous, and the dispatch does not
        happen."""
        recorder = SequentialTerminalExec()
        worker_answers = iter(("glm-5.2", "glm-9.9"))
        # The driver judges each resolution satisfying (an alias), so the DRIFT between
        # rounds is the only defect left for the barrier to catch. Keyed by ROLE, because
        # the Reviewer must be admitted before the Worker may be delivered at all and its
        # resolution must not consume one of the Worker's answers.
        drifting = RecordingDriver(
            lambda t, rq, ob: conforming(
                t, rq, ob,
                resolved=(
                    next(worker_answers) if t.role == "worker" else "glm-5.3-flash"
                ),
            )
        )
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=drifting,
        )
        self.admit_counterpart(harness, "worker")
        first = harness.create_fake_terminal("worker", "complete", iteration=1,
                                            phase="implementation")
        harness.start_worker("task_g", first, "spec", role="worker",
                             phase="implementation", attempt=1)
        second = harness.create_fake_terminal("worker", "complete", iteration=2,
                                              phase="implementation")
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker("task_h", second, "spec", role="worker",
                                 phase="implementation", attempt=2)
        self.assertTrue(str(caught.exception).startswith(MODEL_SELECTION_AMBIGUOUS))
        self.assertNothingDelivered(recorder)


class TheOrderingGroupTests(BarrierTestCase):
    """The tests that are UNASSERTABLE unless the request is a separately attested leg.

    Every one of these would pass trivially against a seam that only verified a resolved
    model, and fails against it only because the request leg is absent -- which is the
    point: an ordering requirement no test can fail is not enforced.
    """

    def test_request_precedes_verification_on_the_driver_seam(self) -> None:
        recorder = SequentialTerminalExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        self.admit_counterpart(harness, "worker")
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        driver.requests.clear()
        harness.start_worker("task_g", handle, "spec", role="worker",
                             phase="implementation", attempt=1)
        kinds = [kind for kind, _ordinal in driver.requests]
        self.assertEqual(kinds, ["request", "observe"])
        ordinals = [ordinal for _kind, ordinal in driver.requests]
        self.assertEqual(ordinals, sorted(ordinals))
        self.assertEqual(ordinals[1], ordinals[0] + 1)

    def test_an_observe_only_driver_is_refused(self) -> None:
        """The pre-existing / default model-state case. The driver returns a perfectly
        consistent `verified` with matching requested and resolved models and NO request
        leg -- and nothing is delivered."""
        message = self.assertRefusedWith(
            MODEL_SELECTION_REQUEST_ABSENT,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(
                    t, selection_token="", request_method="",
                    request_stamp=0, observe_stamp=0,
                ),
                stamps=0,
            ),
        )
        self.assertIn("not evidence", message)

    def test_stamp_count_must_be_exactly_two(self) -> None:
        cases = (
            # stamps drawn, evidence shape, expected reason
            (0, dict(selection_token="", request_method="", request_stamp=0,
                     observe_stamp=0), MODEL_SELECTION_REQUEST_ABSENT),
            (1, dict(observe_stamp=0), MODEL_SELECTION_UNVERIFIED),
            (3, {}, MODEL_SELECTION_REQUEST_STALE),
        )
        for stamps, overrides, reason in cases:
            with self.subTest(stamps=stamps, reason=reason):
                self.assertRefusedWith(
                    reason,
                    driver=RecordingDriver(
                        lambda t, rq, ob, o=overrides: conforming(
                            t, **{"request_stamp": rq, "observe_stamp": ob, **o}
                        ),
                        stamps=stamps,
                    ),
                )

    def test_stamping_once_but_reporting_two_ordinals_is_refused(self) -> None:
        """The counter did not advance twice, and the harness knows that arithmetically."""
        self.assertRefusedWith(
            MODEL_SELECTION_REQUEST_STALE,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(t, rq, rq + 1), stamps=1
            ),
        )

    def test_fabricated_ordinals_are_refused(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_REQUEST_STALE,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(t, 1, 2), stamps=0
            ),
        )

    def test_observe_stamp_before_request_stamp_is_refused(self) -> None:
        self.assertRefusedWith(
            MODEL_SELECTION_REQUEST_STALE,
            driver=RecordingDriver(
                lambda t, rq, ob: conforming(t, ob, rq)
            ),
        )

    def test_a_previous_attempts_token_is_refused(self) -> None:
        """Evidence cannot be carried over: attempt 2's driver replaying attempt 1's token
        is refused, because that token did not exist until attempt 1's barrier ran and is
        not the one attempt 2's barrier minted."""
        recorder = SequentialTerminalExec()
        seen: list[str] = []

        class Replaying:
            """Replays only across WORKER attempts, so the Reviewer's up-front admission
            -- which a same-command pair now requires before any delivery -- is not
            itself the thing that gets refused."""

            def select_and_verify(self, ticket):
                request_stamp = ticket.stamp()
                observe_stamp = ticket.stamp()
                token = ticket.token
                if ticket.role == "worker":
                    token = seen[0] if seen else ticket.token
                    seen.append(ticket.token)
                return conforming(
                    ticket, request_stamp, observe_stamp, selection_token=token
                )

        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=Replaying(),
        )
        self.admit_counterpart(harness, "worker")
        first = harness.create_fake_terminal("worker", "complete", iteration=1,
                                            phase="implementation")
        harness.start_worker("task_g", first, "spec", role="worker",
                             phase="implementation", attempt=1)
        second = harness.create_fake_terminal("worker", "complete", iteration=2,
                                              phase="implementation")
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker("task_h", second, "spec", role="worker",
                                 phase="implementation", attempt=2)
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_REQUEST_STALE)
        )
        self.assertNothingDelivered(recorder)

    def test_a_request_method_outside_the_closed_set_is_refused(self) -> None:
        for method in ("launch_argv", "whatever", "/model", "slash_model"):
            with self.subTest(method=method):
                self.assertRefusedWith(
                    MODEL_SELECTION_UNSUPPORTED,
                    driver=RecordingDriver(
                        lambda t, rq, ob, m=method: conforming(
                            t, rq, ob, request_method=m
                        ),
                    ),
                )

    def test_a_wrong_or_missing_capability_token_is_refused(self) -> None:
        for capability in ("", "prompt_delivery_verified", "model_selection"):
            with self.subTest(capability=capability):
                self.assertRefusedWith(
                    MODEL_SELECTION_UNSUPPORTED,
                    driver=RecordingDriver(
                        lambda t, rq, ob, c=capability: conforming(
                            t, rq, ob, capability=c
                        ),
                    ),
                )

    def test_the_ticket_is_single_use_and_revoked_after_the_call(self) -> None:
        """A driver that keeps the ticket and stamps later gets an error rather than a
        usable ordinal out of the NEXT attempt's window."""
        recorder = SequentialTerminalExec()
        kept: list = []

        class Hoarding(InProcessModelDriver):
            def select_and_verify(self, ticket):
                if ticket.role == "worker":
                    kept.append(ticket)
                return super().select_and_verify(ticket)

        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=Hoarding(),
        )
        self.admit_counterpart(harness, "worker")
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        harness.start_worker("task_g", handle, "spec", role="worker",
                             phase="implementation", attempt=1)
        self.assertEqual(len(kept), 1)
        with self.assertRaises(OrcaRuntimeError) as caught:
            kept[0].stamp()
        self.assertIn("revoked", str(caught.exception))

    def test_a_raising_driver_leaves_no_live_stamp_behind(self) -> None:
        recorder = RecordingExec()
        kept: list = []

        class Exploding:
            def select_and_verify(self, ticket):
                kept.append(ticket)
                raise RuntimeError("driver blew up")

        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=Exploding(),
        )
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        recorder.commands.clear()
        with self.assertRaises(RuntimeError):
            harness.start_worker("task_g", handle, "spec", role="worker",
                                 phase="implementation", attempt=1)
        self.assertNothingDelivered(recorder)
        with self.assertRaises(OrcaRuntimeError):
            kept[0].stamp()

    def test_both_legs_precede_both_delivery_acts_on_rung_three(self) -> None:
        recorder = SequentialTerminalExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        self.admit_counterpart(harness, "worker")
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        order: list[str] = []
        original = recorder.__call__

        def tracking(args):
            verb = args[1] if len(args) > 1 else args[0]
            if verb in DELIVERY_VERBS:
                order.append(f"deliver:{verb}")
            return original(args)

        harness._exec_orca = tracking
        driver.requests.clear()
        harness.start_worker("task_g", handle, "spec", role="worker",
                             phase="implementation", attempt=1)
        self.assertEqual([kind for kind, _ in driver.requests],
                         ["request", "observe"])
        self.assertTrue(order, "rung 3 issued no delivery command at all")
        self.assertEqual(order[0], "deliver:worker-start")

    def test_both_legs_precede_both_delivery_acts_on_rung_four(self) -> None:
        """Rung 4 is `dispatch` + `terminal send --text <prompt incl. spec>`; the barrier
        must precede BOTH, not only the rung-3 adoption."""
        recorder = SequentialTerminalExec(
            errors={"worker-start": {"code": "agent_unconfigured"}}
        )
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        self.admit_counterpart(harness, "worker")
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        driver.requests.clear()
        recorder.commands.clear()
        dispatch_id, supervised = harness.start_worker(
            "task_g", handle, "spec", role="worker", phase="implementation", attempt=1
        )
        self.assertFalse(supervised)
        self.assertIn("dispatch", recorder.verbs)
        self.assertIn("send", recorder.verbs)
        self.assertEqual([kind for kind, _ in driver.requests],
                         ["request", "observe"])

    def test_nothing_is_delivered_on_rung_four_when_the_barrier_refuses(self) -> None:
        recorder = RecordingExec(
            errors={"worker-start": {"code": "agent_unconfigured"}}
        )
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"), model_driver=None
        )
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError):
            harness.start_worker("task_g", handle, "spec", role="worker",
                                 phase="implementation", attempt=1)
        self.assertNothingDelivered(recorder)


class BarrierCoversEveryDoorTests(BarrierTestCase):
    def test_the_barrier_covers_initiator_two(self) -> None:
        """`observe_unexpected_exit()` creates a Task, a terminal and a Dispatch exactly
        as the sibling initiator does, so it carries the same obligation. This is the
        bypass class that once let that path reach worker-start without the B1 guard."""
        recorder = RecordingExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"), model_driver=None
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.observe_unexpected_exit("worker", 1, phase="implementation")
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_UNSUPPORTED)
        )
        self.assertNothingDelivered(recorder)

    def test_the_barrier_covers_initiator_one(self) -> None:
        recorder = RecordingExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"), model_driver=None
        )
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.run_existing_task(
                "worker", 1, "complete", "task_g", phase="implementation", spec="spec"
            )
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_UNSUPPORTED)
        )
        self.assertNothingDelivered(recorder)

    def test_all_three_roles_are_gated(self) -> None:
        for role, phase in (
            ("worker", "implementation"),
            ("reviewer", "implementation"),
            ("final_reviewer", "final_review"),
        ):
            with self.subTest(role=role):
                self.assertRefusedWith(
                    MODEL_SELECTION_UNSUPPORTED, driver=None, role=role, phase=phase
                )

    def test_every_round_reruns_the_whole_lifecycle(self) -> None:
        """A correction, a re-review, a downstream revalidation and a Final Review round
        are each a new start_worker() call, so each mints its own ticket and its driver
        must attest its OWN fresh request. A round cannot inherit a verification."""
        recorder = SequentialTerminalExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        # The Reviewer identity is admitted once, up front; it is the ROUNDS that must
        # each re-verify, and the pair requirement does not substitute for that.
        self.admit_counterpart(harness, "worker")
        driver.requests.clear()
        tokens: list[str] = []
        for attempt in (1, 2, 3):
            handle = harness.create_fake_terminal("worker", "complete",
                                                  iteration=attempt,
                                                  phase="implementation")
            harness.start_worker(f"task_{attempt}", handle, "spec", role="worker",
                                 phase="implementation", attempt=attempt)
            tokens.append(driver.tickets[-1].token)
        self.assertEqual(len(set(tokens)), 3, "a round reused another round's token")
        self.assertEqual(len(driver.requests), 6)   # two legs per round, every round


DISTINCT_COMMAND_PROFILE = (
    "version: 2\n"
    "profiles:\n"
    "  distinct:\n"
    "    phases:\n"
    "      implementation:\n"
    "        worker:\n"
    "          command: claude\n"
    "          model: glm-5.2\n"
    "        reviewer:\n"
    "          command: codex\n"
    "          model: gpt-5.6-sol\n"
    "    final_review:\n"
    "      reviewer:\n"
    "        command: codex\n"
    "        model: gpt-5.6-sol\n"
)


class PairAdmissionPrecedesDeliveryTests(BarrierTestCase):
    """The regression group for review finding F-001.

    THE BUG THESE WOULD HAVE CAUGHT. Before iteration 2, a same-command pair's
    independence was decided twice and both decisions were too weak to hold it:

      * at declaration time, two distinct `requested` tokens were accepted as proof of
        independence -- but `requested` is a declaration, and two declared tokens can
        alias onto one resolved model; and
      * at the pre-delivery barrier, the resolved-value pair check ran only `if
        counterpart is not None`, which on the WORKER -- whose dispatch precedes its
        Reviewer session -- was vacuous.

    So the real sequence was: declaration passes, the Worker's barrier finds no
    counterpart and passes, THE WORKER TASK IS DELIVERED, and only then does the
    Reviewer's barrier refuse. A Worker had already run on a pair whose independence was
    never positively established. Every test below fails against that shape: each one
    asserts a refusal on the FIRST delivery of a role whose same-command counterpart
    holds no verified model evidence, and `assertNothingDelivered` fails the moment any
    `worker-start`, `dispatch` or `send` reaches the recorder.
    """

    def test_the_worker_is_not_delivered_before_the_reviewer_model_is_verified(self) -> None:
        """F-001 in one test, on the exact ordering that used to deliver: the Worker's own
        model is positively verified, the Reviewer's is not yet, and the dispatch does not
        happen."""
        message = self.assertRefusedWith(
            MODEL_SELECTION_PAIR_UNADMITTED, driver=InProcessModelDriver(), role="worker"
        )
        self.assertIn("reviewer", message)
        self.assertIn("alias onto one model", message)

    def test_the_reviewer_is_not_delivered_before_the_worker_model_is_verified(self) -> None:
        """Symmetric, and not a courtesy: the requirement is on the FIRST delivery of
        EITHER role, so it cannot be satisfied by always dispatching one of them first."""
        self.assertRefusedWith(
            MODEL_SELECTION_PAIR_UNADMITTED,
            driver=InProcessModelDriver(),
            role="reviewer",
        )

    def test_insufficient_counterpart_evidence_never_admits_a_delivery(self) -> None:
        """The ticket's invariant, driven through the real lifecycle for EVERY
        non-verified evidence state: the counterpart's admission is refused, so it records
        nothing, so the Worker's delivery is refused too. Insufficient model evidence
        never proves independence, at either end of the pair.
        """
        for state in (MODEL_EVIDENCE_REQUESTED, MODEL_EVIDENCE_NONE,
                      MODEL_EVIDENCE_STALE, MODEL_EVIDENCE_UNVERIFIABLE):
            with self.subTest(state=state):
                recorder = SequentialTerminalExec()
                driver = RecordingDriver(
                    lambda t, rq, ob, s=state: conforming(
                        t, rq, ob,
                        state=MODEL_EVIDENCE_VERIFIED if t.role == "worker" else s,
                    )
                )
                harness = self.build(
                    recorder, routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=driver,
                )
                reviewer = harness.create_fake_terminal(
                    "reviewer", "pass", iteration=1, phase="implementation"
                )
                with self.assertRaises(OrcaRuntimeError):
                    harness.verify_model_identity(
                        "task_g", reviewer, role="reviewer",
                        phase="implementation", attempt=1,
                    )
                worker = harness.create_fake_terminal(
                    "worker", "complete", iteration=1, phase="implementation"
                )
                recorder.commands.clear()
                with self.assertRaises(OrcaRuntimeError) as caught:
                    harness.start_worker("task_g", worker, "spec", role="worker",
                                         phase="implementation", attempt=1)
                self.assertTrue(
                    str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED)
                )
                self.assertNothingDelivered(recorder)

    def test_an_alias_to_one_resolved_model_is_refused_before_any_delivery(self) -> None:
        """The case the review named explicitly. Two DISTINCT declared tokens, an alias
        that collapses them onto ONE resolved model: the pair is refused during
        admission, and neither role is ever delivered. Against the old shape the Worker
        delivered here and only the Reviewer was refused.
        """
        recorder = SequentialTerminalExec()
        driver = RecordingDriver(
            lambda t, rq, ob: conforming(t, rq, ob, resolved="glm-5.2")
        )
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        handles = {
            role: harness.create_fake_terminal(role, mode, iteration=1,
                                               phase="implementation")
            for role, mode in (("worker", "complete"), ("reviewer", "pass"))
        }
        harness.verify_model_identity("task_g", handles["worker"], role="worker",
                                      phase="implementation", attempt=1)
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity("task_g", handles["reviewer"],
                                          role="reviewer", phase="implementation",
                                          attempt=1)
        self.assertTrue(
            str(caught.exception).startswith(REASON_WORKER_REVIEWER_MUST_DIFFER)
        )
        for role in ("worker", "reviewer"):
            with self.subTest(role=role):
                recorder.commands.clear()
                with self.assertRaises(OrcaRuntimeError):
                    harness.start_worker("task_g", handles[role], "spec", role=role,
                                         phase="implementation", attempt=1)
                self.assertNothingDelivered(recorder)

    def test_the_requirement_covers_rung_four_as_well(self) -> None:
        """Rung 4 is `dispatch` + `terminal send`. The pair requirement sits at the same
        single point as the rest of the barrier, so it precedes that rung too."""
        recorder = SequentialTerminalExec(
            errors={"worker-start": {"code": "agent_unconfigured"}}
        )
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker("task_g", handle, "spec", role="worker",
                                 phase="implementation", attempt=1)
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED)
        )
        self.assertNothingDelivered(recorder)

    def test_the_requirement_covers_both_centralized_initiators(self) -> None:
        """Both doors, with a CONFORMING driver wired in -- so the refusal is the pair
        requirement itself and not the absent-driver refusal the sibling door tests
        already cover."""
        for door in ("run_existing_task", "observe_unexpected_exit"):
            with self.subTest(door=door):
                recorder = SequentialTerminalExec()
                harness = self.build(
                    recorder, routing=routing_from(SPLIT_PROFILE, "split"),
                    model_driver=InProcessModelDriver(),
                )
                with self.assertRaises(OrcaRuntimeError) as caught:
                    if door == "run_existing_task":
                        harness.run_existing_task(
                            "worker", 1, "complete", "task_g",
                            phase="implementation", spec="spec",
                        )
                    else:
                        harness.observe_unexpected_exit(
                            "worker", 1, phase="implementation"
                        )
                self.assertTrue(
                    str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED)
                )
                self.assertNothingDelivered(recorder)

    def test_a_distinct_command_pair_is_delivered_with_no_counterpart_evidence(self) -> None:
        """The compatibility half, and the reason the requirement is keyed on COMMAND
        EQUALITY rather than applied to every pair: distinct commands are independent on
        row 1 of the rule, so the Worker delivers before any Reviewer session exists --
        exactly as it did before OS-49 and exactly as this repository's own
        `claude-opus` / `codex-sol` wrappers require.
        """
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder, routing=routing_from(DISTINCT_COMMAND_PROFILE, "distinct"),
            model_driver=InProcessModelDriver(),
        )
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        dispatch_id, supervised = harness.start_worker(
            "task_g", handle, "spec", role="worker", phase="implementation", attempt=1
        )
        self.assertEqual((dispatch_id, supervised), ("ctx_1", True))
        self.assertIn("worker-start", recorder.verbs)

    def test_an_optional_counterpart_carries_no_pair_obligation(self) -> None:
        """At LOW risk the Reviewer entry exists but is OPTIONAL and no Reviewer is ever
        dispatched, so there is no pair to admit. Scoped exactly as Gate A's pair check
        is -- a role nobody dispatches must not fail a run, which is the same rule the
        PATH check follows. Without this scoping the requirement would refuse every
        LOW-risk model-aware Worker, which is a behaviour OS-49 must not change.
        """
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder,
            routing=routing_from(SPLIT_PROFILE, "split", risk="low"),
            model_driver=InProcessModelDriver(),
        )
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        self.assertEqual(
            harness.start_worker("task_g", handle, "spec", role="worker",
                                 phase="implementation", attempt=1),
            ("ctx_1", True),
        )
        self.assertIn("worker-start", recorder.verbs)

    def test_a_model_less_pair_is_unaffected(self) -> None:
        """A v1 / model-less run has no model axis at all, so no admission obligation and
        no new refusal: the Worker delivers with no counterpart evidence, as before."""
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder, routing=routing_from(LEGACY_PROFILE, "plain"),
            model_driver=InProcessModelDriver(),
        )
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        self.assertEqual(
            harness.start_worker("task_g", handle, "spec", role="worker",
                                 phase="implementation", attempt=1),
            ("ctx_1", True),
        )

    def test_the_final_reviewer_carries_no_pair_obligation(self) -> None:
        """The Final Adversarial Reviewer has no Worker counterpart in its routing slot,
        so it is outside this rule -- and it must still route on a model."""
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        handle = harness.create_fake_terminal("final_reviewer", "pass", iteration=1,
                                             phase="final_review")
        self.assertEqual(
            harness.start_worker("task_g", handle, "spec", role="final_reviewer",
                                 phase="final_review", attempt=1),
            ("ctx_1", True),
        )

    def test_the_pre_pass_delivers_nothing_and_records_only_on_acceptance(self) -> None:
        """What makes the pre-pass usable as an admission step rather than a second
        delivery: it issues no delivery command, and a refused pre-pass leaves no record
        a later round could read as earned."""
        recorder = SequentialTerminalExec()
        refusing = RecordingDriver(
            lambda t, rq, ob: conforming(t, rq, ob, state=MODEL_EVIDENCE_STALE)
        )
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=refusing,
        )
        handle = harness.create_fake_terminal("reviewer", "pass", iteration=1,
                                             phase="implementation")
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_model_identity("task_g", handle, role="reviewer",
                                          phase="implementation", attempt=1)
        self.assertNothingDelivered(recorder)
        self.assertEqual(harness._model_identity, {})


if __name__ == "__main__":
    unittest.main()
