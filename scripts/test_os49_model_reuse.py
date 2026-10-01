#!/usr/bin/env python3
"""OS-49: session reuse is MODEL-BOUND.

Condition 9 is APPENDED to the existing eight. These tests pin both halves of that:
every row of its own truth table, AND that it changed nothing else -- the eight still
refuse on their own names, the condition never short-circuits, no input makes a
previously-refused reuse eligible, and a run with no model anywhere produces exactly the
pre-OS-49 decisions.
"""
from __future__ import annotations

import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from scripts import decision_gate, run_logging
from scripts.agent_profile import (
    MODEL_EVIDENCE_NONE,
    MODEL_EVIDENCE_REQUESTED,
    MODEL_EVIDENCE_VERIFIED,
)
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.orca_runtime_harness import (
    MODEL_CAPABILITY_UNSUPPORTED,
    MODEL_IDENTITY_FAILURE_REASONS,
    MODEL_IDENTITY_MISMATCH,
    MODEL_IDENTITY_STALE,
    MODEL_IDENTITY_UNVERIFIED,
    MODEL_SELECTION_REQUEST_METHODS,
    OrcaRuntimeHarness,
    ReuseObservation,
    RuntimeAttempt,
)
from scripts.test_orca_runtime_contract import RecordingExec

#: The eight pre-existing condition names. Condition 9 must never displace one of them.
PRE_OS49_CONDITION_NAMES = {
    "role_mismatch", "agent_command_mismatch",
    "release_state_missing", "release_state_not_live",
    "worker_state_missing", "worker_state_not_reusable",
    "previous_dispatch_not_finalized",
    "ownership_not_transferable", "ownership_not_held_by_this_dispatch",
    "terminal_effect_unrecorded",
    "explicitly_retained",
    "not_self_created", "role_not_reuse_eligible", "coordinator_self_handle",
    "stale_or_missing_observation", "observation_not_for_this_dispatch",
}

ATTEMPT = RuntimeAttempt(
    role="worker",
    iteration=1,
    task_id="task_g",
    dispatch_id="ctx_1",
    outcome="succeeded",
    task_status="completed",
    dispatch_status="completed",
    worker_state="settled",
    terminal_state="released",
    lifecycle_action="reuse",
    worker_done_count=1,
    execution_path="offline",
)


class ModelBoundReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def eligible_harness(self, *, model_driver=None, **model_fields):
        """A harness whose eight pre-existing conditions ALL hold.

        Every negative below therefore fails on condition 9 alone, which is what makes
        "exactly one name" a meaningful assertion.
        """
        recorder = RecordingExec()
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir, model_driver=model_driver)
        harness._exec_orca = recorder
        harness.run_owner, harness.run_id = "term_owner", "run_reuse"
        harness.requested_phases = ("implementation",)
        run_logging.open_decision_ledger(
            harness.run_id,
            base=self.artifact_dir,
            phases=harness.requested_phases,
            risk=harness.risk or "",
            ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
        )
        harness.register_terminal(
            "term_worker",
            role="phase_worker",
            origin="self_created",
            intended_role="phase_worker",
            owner_dispatch_id="ctx_1",
            agent_command="claude",
        )
        harness.record_terminal_effect("term_worker", "reused")
        harness._ledger["ctx_1"] = {
            "dispatch_id": "ctx_1",
            "task_id": "task_g",
            "handle": "term_worker",
            "role": "worker",
            "iteration": 1,
            "state": "finalized",
            "replays": 0,
            "attempt": ATTEMPT,
        }
        row = harness._terminals["term_worker"]
        row.update(model_fields)
        observation = ReuseObservation(
            observed_at_dispatch="ctx_1",
            handle="term_worker",
            worker_state="settled",
            release_state="not_requested",
            ownership_state="external",
            retained_reason="",
        )
        return harness, observation, recorder

    def gate(self, harness, observation, *, requested_model="", agent_command="claude"):
        return harness.reuse_eligible(
            "term_worker",
            role="phase_worker",
            agent_command=agent_command,
            requested_model=requested_model,
            dispatch_id="ctx_1",
            observation=observation,
        )

    # ---- row 1: the compatibility guarantee ------------------------------------------
    def test_no_model_anywhere_reuse_decisions_unchanged(self) -> None:
        """The previous dispatch recorded none and the next requests none: nothing about
        condition 9 is read, and the answer is exactly today's."""
        harness, observation, recorder = self.eligible_harness()
        eligible, reasons = self.gate(harness, observation)
        self.assertTrue(eligible, reasons)
        self.assertEqual(reasons, ())
        self.assertEqual(recorder.commands, [], "the predicate must stay pure")

    def test_no_model_anywhere_still_refuses_on_the_eight_for_their_own_reasons(self) -> None:
        harness, observation, _ = self.eligible_harness()
        harness._terminals["term_worker"]["retain_requested"] = True
        eligible, reasons = self.gate(harness, observation)
        self.assertFalse(eligible)
        self.assertEqual(reasons, ("explicitly_retained",))

    # ---- row 2: the positive model case ----------------------------------------------
    def verified_row(self, model: str = "glm-5.2") -> dict:
        return dict(
            requested_model=model,
            resolved_model=model,
            model_state=MODEL_EVIDENCE_VERIFIED,
            model_request_method=MODEL_SELECTION_REQUEST_METHODS[0],
            model_request_evidence=f"run_reuse:task_g:term_worker:worker:x:1:1:2->3",
            model_observed_at_dispatch="ctx_1",
        )

    def test_same_verified_model_reuse_allowed(self) -> None:
        harness, observation, _ = self.eligible_harness(
            model_driver=InProcessModelDriver(), **self.verified_row()
        )
        eligible, reasons = self.gate(harness, observation, requested_model="glm-5.2")
        self.assertTrue(eligible, reasons)
        self.assertEqual(reasons, ())

    # ---- rows 3-9: every refusal, each binding to exactly one name --------------------
    def assertRefusedOn(self, name: str, *, next_model: str, **recorded):
        """`recorded` is the PREVIOUS dispatch's ledger row; `next_model` is what the next
        dispatch requests. Asserts EXACTLY one failure name, which is only meaningful
        because eligible_harness() makes all eight pre-existing conditions hold."""
        harness, observation, _ = self.eligible_harness(
            model_driver=recorded.pop("model_driver", InProcessModelDriver()),
            **recorded,
        )
        eligible, reasons = self.gate(harness, observation, requested_model=next_model)
        self.assertFalse(eligible)
        self.assertEqual(reasons, (name,), f"expected exactly {name!r}, got {reasons}")

    def test_reuse_refused_across_different_models(self) -> None:
        self.assertRefusedOn(
            MODEL_IDENTITY_MISMATCH,
            next_model="glm-5.3-flash",
            **self.verified_row("glm-5.2"),
        )

    def test_declared_to_undeclared_transition_refused(self) -> None:
        """Declared -> undeclared is a change of identity like any other."""
        self.assertRefusedOn(
            MODEL_IDENTITY_MISMATCH, next_model="", **self.verified_row("glm-5.2")
        )

    def test_undeclared_to_declared_refused(self) -> None:
        self.assertRefusedOn(MODEL_IDENTITY_UNVERIFIED, next_model="glm-5.2")

    def test_requested_never_resolved_refuses_reuse(self) -> None:
        self.assertRefusedOn(
            MODEL_IDENTITY_UNVERIFIED,
            next_model="glm-5.2",
            requested_model="glm-5.2",
            resolved_model="",
            model_state=MODEL_EVIDENCE_REQUESTED,
            model_observed_at_dispatch="ctx_1",
        )

    def test_evidence_for_another_dispatch_refuses_reuse(self) -> None:
        row = self.verified_row()
        row["model_observed_at_dispatch"] = "ctx_other"
        self.assertRefusedOn(MODEL_IDENTITY_STALE, next_model="glm-5.2", **row)

    def test_contradictory_recorded_models_refuse_reuse(self) -> None:
        contradictions = (
            # verified with no resolved value
            dict(requested_model="glm-5.2", resolved_model="",
                 model_state=MODEL_EVIDENCE_VERIFIED,
                 model_request_method=MODEL_SELECTION_REQUEST_METHODS[0],
                 model_request_evidence="t:1->2",
                 model_observed_at_dispatch="ctx_1"),
            # verified with NO record that anything requested it
            dict(requested_model="glm-5.2", resolved_model="glm-5.2",
                 model_state=MODEL_EVIDENCE_VERIFIED,
                 model_request_method="", model_request_evidence="",
                 model_observed_at_dispatch="ctx_1"),
            # verified, requested, but no request EVIDENCE
            dict(requested_model="glm-5.2", resolved_model="glm-5.2",
                 model_state=MODEL_EVIDENCE_VERIFIED,
                 model_request_method=MODEL_SELECTION_REQUEST_METHODS[0],
                 model_request_evidence="",
                 model_observed_at_dispatch="ctx_1"),
            # a resolved value with a state that does not admit one
            dict(requested_model="glm-5.2", resolved_model="glm-5.2",
                 model_state=MODEL_EVIDENCE_REQUESTED,
                 model_request_method=MODEL_SELECTION_REQUEST_METHODS[0],
                 model_request_evidence="t:1->2",
                 model_observed_at_dispatch="ctx_1"),
            # a request method outside the closed set
            dict(requested_model="glm-5.2", resolved_model="glm-5.2",
                 model_state=MODEL_EVIDENCE_VERIFIED,
                 model_request_method="launch_argv",
                 model_request_evidence="t:1->2",
                 model_observed_at_dispatch="ctx_1"),
        )
        for index, row in enumerate(contradictions):
            with self.subTest(contradiction=index):
                harness, observation, _ = self.eligible_harness(
                    model_driver=InProcessModelDriver(), **row
                )
                eligible, reasons = self.gate(
                    harness, observation, requested_model="glm-5.2"
                )
                self.assertFalse(eligible)
                self.assertIn(MODEL_IDENTITY_STALE, reasons)

    def test_capability_absent_refuses_reuse(self) -> None:
        """No driver means THIS dispatch could not request a selection either, so there is
        no way to re-establish the identity the reuse would carry forward."""
        harness, observation, _ = self.eligible_harness(
            model_driver=None, **self.verified_row()
        )
        eligible, reasons = self.gate(harness, observation, requested_model="glm-5.2")
        self.assertFalse(eligible)
        self.assertEqual(reasons, (MODEL_CAPABILITY_UNSUPPORTED,))

    # ---- the "it changed nothing else" half -------------------------------------------
    def test_condition_nine_can_only_refuse(self) -> None:
        """No model input makes a previously-refused reuse eligible."""
        for model_fields, requested in (
            ({}, ""),
            (self.verified_row(), "glm-5.2"),
            (self.verified_row(), "glm-9.9"),
        ):
            with self.subTest(requested=requested):
                harness, observation, _ = self.eligible_harness(
                    model_driver=InProcessModelDriver(), **model_fields
                )
                # Break a pre-existing condition, then check it still refuses.
                harness._terminals["term_worker"]["origin"] = "adopted"
                eligible, reasons = self.gate(
                    harness, observation, requested_model=requested
                )
                self.assertFalse(eligible)
                self.assertIn("not_self_created", reasons)

    def test_condition_nine_never_short_circuits(self) -> None:
        """A row that fails BOTH a pre-existing condition and condition 9 reports both
        names, so a negative test can bind to exactly one of them."""
        harness, observation, _ = self.eligible_harness(
            model_driver=InProcessModelDriver(), **self.verified_row()
        )
        harness._terminals["term_worker"]["retain_requested"] = True
        eligible, reasons = self.gate(harness, observation, requested_model="glm-9.9")
        self.assertFalse(eligible)
        self.assertIn("explicitly_retained", reasons)
        self.assertIn(MODEL_IDENTITY_MISMATCH, reasons)

    def test_the_model_names_are_disjoint_from_the_eight(self) -> None:
        self.assertEqual(
            set(MODEL_IDENTITY_FAILURE_REASONS) & PRE_OS49_CONDITION_NAMES, set()
        )

    def test_a_command_change_still_reports_the_command_name(self) -> None:
        """Condition 2 keeps its own name: the command axis did not move into condition 9.
        """
        harness, observation, _ = self.eligible_harness(
            model_driver=InProcessModelDriver(), **self.verified_row()
        )
        eligible, reasons = self.gate(
            harness, observation, requested_model="glm-5.2", agent_command="codex"
        )
        self.assertFalse(eligible)
        self.assertEqual(reasons, ("agent_command_mismatch",))

    def test_register_terminal_never_overwrites_a_recorded_model_blank(self) -> None:
        harness, _observation, _ = self.eligible_harness(**self.verified_row())
        harness.register_terminal(
            "term_worker",
            role="phase_worker",
            origin="self_created",
            owner_dispatch_id="ctx_2",
            requested_model="",
            model_state="",
        )
        row = harness.ledger_terminal("term_worker")
        self.assertEqual(row["requested_model"], "glm-5.2")
        self.assertEqual(row["model_state"], MODEL_EVIDENCE_VERIFIED)

    def test_a_row_created_without_the_model_keys_is_brought_up_to_the_key_set(self) -> None:
        """A row built before OS-49 existed in this process is completed without
        overwriting anything it already carries."""
        harness, _observation, _ = self.eligible_harness()
        row = harness._terminals["term_worker"]
        for key in (
            "requested_model", "resolved_model", "model_state",
            "model_request_method", "model_request_evidence",
            "model_observed_at_dispatch",
        ):
            row.pop(key, None)
        harness.register_terminal(
            "term_worker", role="phase_worker", origin="self_created",
            owner_dispatch_id="ctx_2",
        )
        completed = harness.ledger_terminal("term_worker")
        self.assertEqual(completed["model_state"], MODEL_EVIDENCE_NONE)
        self.assertEqual(completed["resolved_model"], "")

    def test_terminal_for_next_dispatch_threads_the_requested_model(self) -> None:
        """The gate's only consumer must pass the next dispatch's model through, or
        condition 9 is unreachable in production and reuse is decided without it.

        The observation this method takes is its OWN fresh `worker-show`, so the recorder
        has to report a live, transferable terminal for the positive half -- the default
        fixture reports a released one, which correctly fails condition 3.
        """
        live = {
            "worker-show": {
                "dispatch": {"status": "completed"},
                "worker": {"state": "settled"},
                "terminalResource": {
                    "releaseState": "not_requested",
                    "ownershipState": "external",
                },
            }
        }
        harness, _observation, recorder = self.eligible_harness(
            model_driver=InProcessModelDriver(), **self.verified_row()
        )
        harness._exec_orca = RecordingExec(results=live)

        self.assertIsNone(
            harness.terminal_for_next_dispatch(
                "term_worker", role="phase_worker", agent_command="claude",
                requested_model="glm-9.9", dispatch_id="ctx_1",
            )
        )
        self.assertEqual(
            harness.last_reuse_decision["reasons"], [MODEL_IDENTITY_MISMATCH]
        )
        self.assertEqual(harness.last_reuse_decision["requested_model"], "glm-9.9")

        self.assertEqual(
            harness.terminal_for_next_dispatch(
                "term_worker", role="phase_worker", agent_command="claude",
                requested_model="glm-5.2", dispatch_id="ctx_1",
            ),
            "term_worker",
        )


if __name__ == "__main__":
    unittest.main()
