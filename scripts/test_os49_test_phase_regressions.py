#!/usr/bin/env python3
"""OS-49 TEST phase: the cases the implementation suite does not genuinely cover.

Two groups, deliberately separated.

GROUP 1 -- REQUIRED CASES THAT WERE COVERED IN ONE DIRECTION ONLY. The ticket asks for
aliasing "in both role orders" and for a same-command pair that routes; the shipped suite
exercises exactly one order of each. These pass today and fail if the symmetry is lost.

GROUP 2 -- THE TWO DEFECTS THIS PHASE FOUND, AND THE FIXES FOR THEM. Each one is a named
finding in `artifacts/runs/run_217298d7063b/TEST.md`. In TEST iteration 1 every test in
this group FAILED against the tree as the IMPLEMENTATION phase left it, stating the
required behaviour rather than the current one -- not weakened, skipped or xfailed,
because a required behaviour that does not hold must be visible as a failure. The phase
Reviewer reproduced all six independently (REVIEW_TEST.md, RESULT: FAIL).

In iteration 2 the PRODUCTION code was fixed and these tests now pass unchanged: no
assertion in this group was edited, relaxed or removed to make that happen. The two
descriptions below are kept in the PAST TENSE of the defect, because that is what each
test exists to keep from coming back; `_routing_key()` and `finish()` now carry the
corrections, and each group also gained one test that pins the CONSTRAINT on its fix.

  T-001  (fixed in `_routing_key()`.) A Final Adversarial Review is dispatched with
         role="reviewer" and
         phase="final_review" -- the ONLY spelling any production path produces
         (`run_existing_task` -> `_barrier_phase`, and `_write_final_review_audit_record`
         itself returns early unless `attempt.role == "reviewer"`). `_routing_key()` keys
         the routing ROLE off the role string's prefix, so that spelling resolves to
         ("final_review", "reviewer") -- an entry that does not exist -- and the barrier
         returns at `if not requested`. The declared Final Reviewer model is therefore
         never requested, never verified and never recorded, and the task is delivered.
         The shipped test that covers this required case passes role="final_reviewer",
         which no production caller uses.

  T-002  (fixed in `finish()` and at the counterpart-admission read.) Model-identity
         evidence was not run-scoped. `finish()` cleared `_terminals`,
         `_ledger` and `_deliveries` but not `_model_identity`, and the pair-admission
         read never checks the counterpart evidence's `observed_at_run`. A later run on
         the same harness instance therefore inherits the previous run's counterpart
         evidence and delivers a same-command Worker with no counterpart session in that
         run at all. `run_runtime_scenarios()` runs several start_run/finish cycles on one
         harness instance, so the shape is reachable in shipped code.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from scripts import run_logging
from scripts.agent_profile import (
    EVENT_AGENT_IDENTITY_BOUND,
    MODEL_EVIDENCE_VERIFIED,
    REASON_WORKER_REVIEWER_MUST_DIFFER,
)
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.orca_runtime_harness import (
    MODEL_SELECTION_PAIR_UNADMITTED,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
    RuntimeScenarioResult,
)
from scripts.task_context import FINAL_REVIEW_PHASE
from scripts.test_orca_runtime_contract import RecordingExec, SequentialTerminalExec
from scripts.test_os49_delivery_barrier import (
    DELIVERY_VERBS,
    SPLIT_PROFILE,
    BarrierTestCase,
    RecordingDriver,
    conforming,
    routing_from,
)
from scripts.test_os49_model_provenance import (
    ProvenanceTestCase,
    detail_pairs,
    log_rows,
)


# --------------------------------------------------------------------------------------
# GROUP 1: the required cases the shipped suite covers in one direction only.
# --------------------------------------------------------------------------------------


class BothRoleOrdersTests(BarrierTestCase):
    """`claude + glm-5.2` vs `claude + glm-5.3-flash` must behave the same whichever role
    is verified or delivered first. The shipped suite only ever runs worker-first."""

    def _harness(self, recorder, driver):
        return self.build(
            recorder,
            routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )

    def test_an_alias_is_refused_in_the_reverse_role_order(self) -> None:
        """Two distinct DECLARED tokens collapsing onto one RESOLVED model, with the
        REVIEWER verified first. The shipped alias test verifies the worker first, so the
        refusal it exercises is the one the reviewer's own barrier raises; this one is the
        refusal the WORKER's barrier has to raise, and neither role may be delivered."""
        recorder = SequentialTerminalExec()
        driver = RecordingDriver(
            lambda t, rq, ob: conforming(t, rq, ob, resolved="glm-5.2")
        )
        harness = self._harness(recorder, driver)
        handles = {
            role: harness.create_fake_terminal(role, mode, iteration=1,
                                               phase="implementation")
            for role, mode in (("reviewer", "pass"), ("worker", "complete"))
        }
        harness.verify_model_identity("task_g", handles["reviewer"], role="reviewer",
                                      phase="implementation", attempt=1)
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.verify_model_identity("task_g", handles["worker"], role="worker",
                                          phase="implementation", attempt=1)
        self.assertTrue(
            str(caught.exception).startswith(REASON_WORKER_REVIEWER_MUST_DIFFER),
            str(caught.exception),
        )
        for role in ("worker", "reviewer"):
            with self.subTest(role=role):
                recorder.commands.clear()
                with self.assertRaises(OrcaRuntimeError):
                    harness.start_worker("task_g", handles[role], "spec", role=role,
                                         phase="implementation", attempt=1)
                self.assertNothingDelivered(recorder)

    def test_a_same_command_pair_delivers_reviewer_first_too(self) -> None:
        """The positive capability, with the REVIEWER delivered first. Delivery order must
        not be part of the admission rule -- only verification order is, and both are
        already verified here."""
        recorder = SequentialTerminalExec()
        harness = self._harness(recorder, InProcessModelDriver())
        handles = {
            role: harness.create_fake_terminal(role, mode, iteration=1,
                                               phase="implementation")
            for role, mode in (("worker", "complete"), ("reviewer", "pass"))
        }
        for role in ("worker", "reviewer"):
            harness.verify_model_identity("task_g", handles[role], role=role,
                                          phase="implementation", attempt=1)
        self.assertNothingDelivered(recorder)
        for role, model in (("reviewer", "glm-5.3-flash"), ("worker", "glm-5.2")):
            with self.subTest(role=role):
                harness.start_worker("task_g", handles[role], "spec", role=role,
                                     phase="implementation", attempt=1)
                row = harness.ledger_terminal(handles[role])
                self.assertEqual(row["resolved_model"], model)
                self.assertEqual(row["model_state"], MODEL_EVIDENCE_VERIFIED)

    def test_the_pair_obligation_is_refused_from_either_side_first(self) -> None:
        """Already covered per-role in the shipped suite; asserted here as one symmetry so
        a future change cannot satisfy the rule for one role only."""
        for first in ("worker", "reviewer"):
            with self.subTest(delivered_first=first):
                self.assertRefusedWith(
                    MODEL_SELECTION_PAIR_UNADMITTED,
                    driver=InProcessModelDriver(),
                    role=first,
                )


# --------------------------------------------------------------------------------------
# GROUP 2, T-001: the Final Adversarial Review's model is never verified.
# --------------------------------------------------------------------------------------


class FinalReviewerOnTheDispatchedRoleSpellingTests(BarrierTestCase):
    def test_the_routing_entry_is_found_on_the_spelling_production_dispatches(self) -> None:
        """The root cause, isolated. `_barrier_phase()` already maps a Final Review
        attempt onto the reserved slot and its own docstring says that slot "is also the
        slot its routing entry lives in"; `_routing_key()` does not honour it, so the
        lookup misses and every downstream model step is skipped."""
        harness = self.build(
            RecordingExec(), routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        barrier_phase = harness._barrier_phase("reviewer", FINAL_REVIEW_PHASE)
        self.assertEqual(barrier_phase, FINAL_REVIEW_PHASE)
        entry = harness._routing_entry_for("reviewer", barrier_phase)
        self.assertIsNotNone(
            entry,
            "the Final Reviewer's routing entry must be reachable from the role/phase "
            "pair run_existing_task() actually passes to start_worker()",
        )
        self.assertEqual(entry.model, "glm-5.2")

    def test_the_final_reviewer_model_is_verified_before_its_delivery(self) -> None:
        """ORIGINAL_REQUEST §3: a task must never be delivered before the requested model
        is positively verified. Driven on the dispatched spelling, the driver is never
        asked and the task is delivered anyway."""
        recorder = RecordingExec()
        driver = InProcessModelDriver()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=driver,
        )
        handle = harness.create_fake_terminal("reviewer", "pass", iteration=1,
                                             phase=FINAL_REVIEW_PHASE)
        recorder.commands.clear()
        harness.start_worker("task_f", handle, "spec", role="reviewer",
                             phase=FINAL_REVIEW_PHASE, attempt=1)
        delivered = sorted(set(recorder.verbs) & DELIVERY_VERBS)
        self.assertTrue(
            delivered, "this test is only meaningful if the dispatch was attempted"
        )
        self.assertTrue(
            driver.requests,
            "the Final Reviewer declares a model and the task was DELIVERED without a "
            "single model-selection request: "
            f"delivery commands={delivered} driver requests={driver.requests}",
        )
        self.assertEqual(
            harness.ledger_terminal(handle)["resolved_model"], "glm-5.2"
        )

    def test_a_declared_final_reviewer_model_without_a_driver_is_refused(self) -> None:
        """The fail-closed half. With no driver at all a declared model cannot even be
        REQUESTED, so the Final Review dispatch must be refused -- exactly as a phase role
        is -- and nothing may be delivered."""
        recorder = RecordingExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"), model_driver=None,
        )
        handle = harness.create_fake_terminal("reviewer", "pass", iteration=1,
                                             phase=FINAL_REVIEW_PHASE)
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError):
            harness.start_worker("task_f", handle, "spec", role="reviewer",
                                 phase=FINAL_REVIEW_PHASE, attempt=1)
        self.assertNothingDelivered(recorder)


    def test_the_final_review_delivers_on_its_own_evidence_alone(self) -> None:
        """The CONSTRAINT on the T-001 fix, pinned so a later change cannot overshoot it.

        Making the dispatched spelling resolve the Final Reviewer's entry must widen
        REACHABILITY only: the Final Reviewer stays outside the PAIR-admission rule,
        because `final_reviewer` has no counterpart role to look up. So a Final Review
        is routable with NO other model evidence in the run at all -- nothing from the
        implementation pair, and no second Final Review session -- and the only record
        the barrier leaves behind is its own.
        """
        recorder = RecordingExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.assertEqual(harness._model_identity, {})
        handle = harness.create_fake_terminal("reviewer", "pass", iteration=1,
                                             phase=FINAL_REVIEW_PHASE)
        harness.start_worker("task_f", handle, "spec", role="reviewer",
                             phase=FINAL_REVIEW_PHASE, attempt=1)
        self.assertEqual(
            list(harness._model_identity), [(FINAL_REVIEW_PHASE, "final_reviewer")]
        )


class FinalReviewProvenanceTests(ProvenanceTestCase):
    def _dispatch_final_review(self, run_id: str):
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(
            recorder,
            routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
            run_id=run_id,
        )
        harness.requested_phases = ("implementation",)
        self.dispatch(harness, recorder, role="reviewer", mode="pass",
                      phase=FINAL_REVIEW_PHASE, round_kind="final_review")
        return harness

    def test_the_final_review_identity_row_carries_the_verified_model(self) -> None:
        """ORIGINAL_REQUEST §5: it must be possible to reconstruct which effective agent
        identity produced each review, the Final Adversarial Review included. The shipped
        row-count test passes while this row records `command=` and `model_state=none` for
        a profile that declares `final_review.reviewer.model`."""
        self._dispatch_final_review("run_fr_row")
        rows = [
            row for row in log_rows(self.artifact_dir, "run_fr_row")
            if row["event"] == EVENT_AGENT_IDENTITY_BOUND
        ]
        self.assertEqual(len(rows), 1, rows)
        detail = detail_pairs(rows[0]["detail"])
        self.assertEqual(rows[0]["result"], f"model_state={MODEL_EVIDENCE_VERIFIED}")
        self.assertEqual(detail["command"], "claude")
        self.assertEqual(detail["requested_model"], "glm-5.2")
        self.assertEqual(detail["resolved_model"], "glm-5.2")
        self.assertEqual(detail["selection_verified"], "true")

    def test_the_final_review_audit_record_is_never_internally_contradictory(self) -> None:
        """A record that names a requested model and carries no verification evidence at
        all is exactly the contradiction reuse condition 9 refuses to act on. The Final
        Review audit record currently writes one: `reviewer_requested_model` is read
        straight from the routing while every evidence field stays empty."""
        self._dispatch_final_review("run_fr_audit")
        directory = (
            self.artifact_dir / "artifacts" / "runs" / "run_fr_audit"
            / run_logging.FINAL_REVIEW_AUDIT_DIRNAME
        )
        records = sorted(
            directory.rglob(run_logging.FINAL_REVIEW_AUDIT_RECORD_FILENAME)
        )
        self.assertEqual(len(records), 1, records)
        record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(record["reviewer_requested_model"], "glm-5.2")
        self.assertEqual(
            record["reviewer_model_state"], MODEL_EVIDENCE_VERIFIED,
            "a settled Final Review that names a requested model must also carry the "
            f"evidence that resolved it: {record}",
        )
        self.assertEqual(record["reviewer_resolved_model"], "glm-5.2")
        self.assertTrue(record["reviewer_model_request_method"])
        self.assertTrue(record["reviewer_model_request_evidence"])


# --------------------------------------------------------------------------------------
# GROUP 2, T-002: model-identity evidence is not run-scoped.
# --------------------------------------------------------------------------------------


class ModelEvidenceIsRunScopedTests(BarrierTestCase):
    def test_accepted_evidence_names_the_run_it_was_observed_in(self) -> None:
        """The discriminator already exists on the record -- this test documents that the
        data needed to scope the counterpart read is present, so the sibling test's
        failure is about the check, not about missing evidence."""
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        handle = self.admit_counterpart(harness, "worker")
        self.assertTrue(handle)
        evidence = harness._model_identity[("implementation", "reviewer")]
        self.assertEqual(evidence.observed_at_run, "run_barrier")

    def test_counterpart_evidence_does_not_survive_the_run_it_belongs_to(self) -> None:
        """T-002. `finish()` clears `_terminals`, `_ledger` and `_deliveries`; the
        model-identity maps are left behind, and the pair-admission read never compares
        the counterpart's `observed_at_run` with the current run. A second run on the same
        harness instance then delivers a same-command Worker whose Reviewer does not exist
        in that run.

        The per-run state is cleared here rather than by calling `finish()`, which needs a
        whole scenario (snapshot write, run-show, quiescence check) to reach its clearing
        block; the three attributes below are exactly the ones `finish()` clears, and
        re-pointing `run_id` is exactly what `start_run()` does next.
        """
        recorder = SequentialTerminalExec()
        harness = self.build(
            recorder, routing=routing_from(SPLIT_PROFILE, "split"),
            model_driver=InProcessModelDriver(),
        )
        self.admit_counterpart(harness, "worker")

        harness.run_id = "run_second"
        harness._terminals = {}
        harness._ledger = {}
        harness._deliveries = {}

        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        recorder.commands.clear()
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.start_worker("task_second", handle, "spec", role="worker",
                                 phase="implementation", attempt=1)
        self.assertTrue(
            str(caught.exception).startswith(MODEL_SELECTION_PAIR_UNADMITTED),
            str(caught.exception),
        )
        self.assertNothingDelivered(recorder)


class FinishClearsModelEvidenceTests(unittest.TestCase):
    """T-002's other half: the RUN BOUNDARY itself.

    The sibling test above re-points the three attributes `finish()` clears by hand,
    because reaching `finish()`'s clearing block needs a whole scenario. This one pays
    that cost once and asserts the post-condition on the real method, so the two halves
    together cover both fixes: `finish()` clears the model maps, and the counterpart read
    refuses cross-run evidence even if some future caller re-points `run_id` without
    going through `finish()`.
    """

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_the_model_maps_are_empty_after_the_run_boundary(self) -> None:
        recorder = SequentialTerminalExec()
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(
                self.artifact_dir,
                agent_routing=routing_from(SPLIT_PROFILE, "split"),
                model_driver=InProcessModelDriver(),
            )
        harness._exec_orca = recorder
        recorder.results["run-create"] = {"run": {"id": "run_boundary"}}
        harness.start_run("os49 run boundary", requested_phases=("implementation",))
        run_id = harness.run_id

        # Evidence earned through the real barrier, not poked into the map.
        handle = harness.create_fake_terminal("worker", "complete", iteration=1,
                                             phase="implementation")
        harness.verify_model_identity("task_b", handle, role="worker",
                                      phase="implementation", attempt=1)
        self.assertEqual(
            list(harness._model_identity), [("implementation", "worker")]
        )
        self.assertEqual(list(harness._model_pending_evidence), [handle])

        harness.finish(
            RuntimeScenarioResult("probe", run_id, "COMPLETED", 1, [])
        )

        self.assertEqual(harness._model_identity, {})
        self.assertEqual(harness._model_pending_evidence, {})


if __name__ == "__main__":
    unittest.main()
