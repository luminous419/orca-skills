"""OS-43 U-5: the observation port and the atomic fact snapshot (layer (a)).

Every test here is about HOW an authority answered, never about what the answer means:
that separation is the whole of UR-1's two layers, and it is what keeps CON-1 checkable.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import watchdog_observation as module
from scripts.deterministic_workflow.watchdog_observation import (SUPPORT_ABSENT_DECLARED,
                                                                 SUPPORT_SUPPORTED,
                                                                 SUPPORT_UNREADABLE,
                                                                 SUPPORT_UNSUPPORTED,
                                                                 fold_support,
                                                                 observation_unsupported,
                                                                 snapshot)
from scripts.test_os43_fixture import FakeLivenessPort, FakeObservationPort

RUN = "run_w"


class SupportFoldTests(unittest.TestCase):
    """The four-branch fold, and the branch that makes it TOTAL."""

    def test_any_unreadable_contributor_wins(self):
        self.assertEqual(fold_support((SUPPORT_SUPPORTED, SUPPORT_UNREADABLE)),
                         SUPPORT_UNREADABLE)

    def test_any_supported_contributor_beats_an_absent_one(self):
        self.assertEqual(fold_support((SUPPORT_ABSENT_DECLARED, SUPPORT_SUPPORTED)),
                         SUPPORT_SUPPORTED)

    def test_all_absent_is_ABSENT_DECLARED_and_therefore_COVERED(self):
        self.assertEqual(fold_support((SUPPORT_ABSENT_DECLARED, SUPPORT_ABSENT_DECLARED)),
                         SUPPORT_ABSENT_DECLARED)

    def test_the_EMPTY_contributor_case_is_UNSUPPORTED_never_ABSENT_DECLARED(self):
        """Nothing answered, so nothing is known.  This is the unconditional else."""
        self.assertEqual(fold_support(()), SUPPORT_UNSUPPORTED)
        self.assertEqual(fold_support((SUPPORT_UNSUPPORTED,)), SUPPORT_UNSUPPORTED)


class F11PredicateTests(unittest.TestCase):
    """CON-4: an uncovered VETO is fail-closed exactly as an uncovered ENABLER is."""

    def base(self):
        return {fact: SUPPORT_SUPPORTED for fact in module.FACT_IDS}

    def test_every_safety_relevant_fact_sets_F11_when_uncovered(self):
        self.assertEqual(len(module.SAFETY_RELEVANT_FACTS), 9)
        for fact in module.SAFETY_RELEVANT_FACTS:
            support = {**self.base(), fact: SUPPORT_UNSUPPORTED}
            self.assertTrue(observation_unsupported(support), fact)

    def test_ABSENT_DECLARED_never_sets_F11(self):
        for fact in module.SAFETY_RELEVANT_FACTS:
            support = {**self.base(), fact: SUPPORT_ABSENT_DECLARED}
            self.assertFalse(observation_unsupported(support), fact)

    def test_the_domain_is_NOT_the_enabling_facts(self):
        self.assertEqual(module.ENABLING_FACTS, ("F4", "F5"))
        self.assertNotEqual(set(module.SAFETY_RELEVANT_FACTS), set(module.ENABLING_FACTS))
        for veto in ("F2", "F3", "F6", "F7", "F8", "F9", "F10"):
            self.assertIn(veto, module.SAFETY_RELEVANT_FACTS)

    def test_F1_and_F11_are_excluded_each_for_its_own_reason(self):
        self.assertNotIn("F1", module.SAFETY_RELEVANT_FACTS)
        self.assertNotIn("F11", module.SAFETY_RELEVANT_FACTS)


class SnapshotConstructionTests(unittest.TestCase):
    def build(self, **answers):
        return snapshot(RUN, observation=FakeObservationPort(**answers),
                        liveness=FakeLivenessPort(), ledger={})

    def test_every_port_method_is_called_at_MOST_once(self):
        port = FakeObservationPort()
        snapshot(RUN, observation=port, liveness=FakeLivenessPort(), ledger={})
        self.assertEqual(sorted(port.calls), sorted(set(port.calls)),
                         "one snapshot, no re-read: the determinism argument's "
                         "operational precondition")

    def test_the_snapshot_is_frozen_and_total(self):
        result = self.build()
        self.assertEqual(set(result.facts), set(module.FACT_IDS))
        self.assertEqual(set(result.support), set(module.FACT_IDS))
        with self.assertRaises(Exception):
            result.facts = {}                 # type: ignore[misc]

    def test_an_authority_that_RAISES_sets_F1_and_marks_that_fact_UNREADABLE(self):
        result = self.build(orca_state=FakeObservationPort.RAISE_UNAVAILABLE)
        self.assertTrue(result.facts["F1"])
        self.assertEqual(result.support["F6"], SUPPORT_UNREADABLE)

    def test_an_UNSUPPORTED_authority_sets_F11_and_NOT_F1(self):
        result = self.build(orca_state=FakeObservationPort.RAISE_UNSUPPORTED)
        self.assertFalse(result.facts["F1"])
        self.assertTrue(result.facts["F11"])
        self.assertEqual(result.support["F6"], SUPPORT_UNSUPPORTED)
        self.assertIn("F6", result.evidence["F11"])

    def test_a_legitimately_ABSENT_checkpoint_is_covered_and_never_sets_F11(self):
        """"A run with NO checkpoint store is a real and normal case" -- and stays one."""
        result = self.build(orca_state={"active_dispatches": (),
                                        "runnable_actions": ("dispatch_task:T1",)})
        self.assertEqual(result.support["F5"], SUPPORT_SUPPORTED)
        self.assertFalse(result.facts["F11"])
        self.assertTrue(result.facts["F5"])
        self.assertEqual(result.status_authority, "declared_only")

    def test_F3_is_TRI_VALUED_an_unreadable_witness_is_F1_not_no_wait_armed(self):
        """AC-2's defence, and the reason F3 does not reuse observe_durable_wait."""
        result = self.build(durable_wait={"evidence": (), "unreadable": ("os31_pause_record",)})
        self.assertTrue(result.facts["F1"],
                        "an unreadable wait witness routes to F1/R1, never to 'no human "
                        "wait is armed'")
        self.assertFalse(result.facts["F3"])

    def test_F6_requires_BOTH_authorities_to_agree(self):
        """AC-3: a `dispatched` Task with no live worker row is F7, not F6."""
        reconcile = self.build(orca_state={"active_dispatches": (),
                                           "runnable_actions": ("reconcile_dispatch:T1",)})
        self.assertFalse(reconcile.facts["F6"])
        self.assertTrue(reconcile.facts["F7"])
        self.assertFalse(reconcile.facts["F5"],
                         "a reconcile action is recovery work, not a runnable next node")
        live = self.build(orca_state={"active_dispatches": ("d1",),
                                      "runnable_actions": ()})
        self.assertTrue(live.facts["F6"])

    def test_an_UNREADABLE_liveness_lease_is_F1_and_never_EXPIRED(self):
        result = snapshot(RUN, observation=FakeObservationPort(),
                          liveness=FakeLivenessPort(raises=True), ledger={})
        self.assertTrue(result.facts["F1"])
        self.assertEqual(result.liveness_status, "")

    def test_the_liveness_status_is_carried_VERBATIM_for_the_gate(self):
        for status in ("LIVE", "EXPIRED", "ABSENT", "UNREADABLE"):
            result = snapshot(RUN, observation=FakeObservationPort(),
                              liveness=FakeLivenessPort(status), ledger={})
            self.assertEqual(result.liveness_status, status)

    def test_F10_is_head_keyed_so_an_advanced_run_is_reconsidered(self):
        ledger = {"rid": {"last_outcome": "CONFLICT", "head_before": "cp_1"}}
        stalled = snapshot(RUN, observation=FakeObservationPort(
            checkpoint_state={"present": True, "run_status": "ACTIVE",
                              "next_node": "PREPARE_WORKER", "thread_id": "t",
                              "checkpoint_ns": "", "head_checkpoint_id": "cp_1",
                              "status_authority": "workflow_checkpoint"}),
            liveness=FakeLivenessPort(), ledger=ledger)
        self.assertTrue(stalled.facts["F10"])
        advanced = snapshot(RUN, observation=FakeObservationPort(
            checkpoint_state={"present": True, "run_status": "ACTIVE",
                              "next_node": "PREPARE_WORKER", "thread_id": "t",
                              "checkpoint_ns": "", "head_checkpoint_id": "cp_2",
                              "status_authority": "workflow_checkpoint"}),
            liveness=FakeLivenessPort(), ledger=ledger)
        self.assertFalse(advanced.facts["F10"],
                         "a run that genuinely advanced is legitimately reconsidered")

    def test_a_ledger_that_was_never_consulted_is_UNSUPPORTED_for_F10(self):
        result = snapshot(RUN, observation=FakeObservationPort(),
                          liveness=FakeLivenessPort(), ledger=None)
        self.assertEqual(result.support["F10"], SUPPORT_UNSUPPORTED)
        self.assertTrue(result.facts["F11"])

    def test_the_undeclared_capability_clause_is_scoped_to_the_path_that_needs_it(self):
        """DR-8: the real adapter withholds external_resume permanently and on purpose."""
        idle = self.build(declared_capabilities=frozenset({"external_lookup"}))
        self.assertFalse(idle.facts["F11"],
                         "withholding external_resume must not make the Watchdog inert "
                         "on every real run")
        needing = self.build(declared_capabilities=frozenset({"external_lookup"}),
                             orca_state={"active_dispatches": (),
                                         "runnable_actions": ("reconcile_dispatch:T1",)})
        self.assertTrue(needing.facts["F11"],
                        "on the O-2 shape the capability IS needed, so its absence is "
                        "fail-closed")

    def test_a_snapshot_digest_covers_the_whole_vector(self):
        first = self.build()
        second = self.build(orca_state={"active_dispatches": ("d1",),
                                        "runnable_actions": ()})
        self.assertNotEqual(first.snapshot_digest, second.snapshot_digest)


class DeclaredInputNegativeTests(unittest.TestCase):
    """CON-3: no declared or prompt-level input can set any fact."""

    def test_the_port_protocol_takes_only_a_run_id(self):
        import inspect

        from scripts.deterministic_workflow.ports import RunObservationPort
        for name in ("orca_state", "checkpoint_state", "pause_state", "durable_wait",
                     "delivery_obligations", "foreign_lease", "declared_capabilities"):
            parameters = list(inspect.signature(
                getattr(RunObservationPort, name)).parameters)
            self.assertEqual(parameters, ["self", "run_id"],
                             f"{name} accepts a declared value")

    def test_snapshot_accepts_no_declared_status_or_next_node(self):
        import inspect
        parameters = set(inspect.signature(snapshot).parameters)
        for forbidden in ("declared_status", "declared_next_node", "next_node",
                          "run_status", "verdict"):
            self.assertNotIn(forbidden, parameters)

    def test_the_engine_refuses_this_class_at_the_same_boundary(self):
        """The rule is the repository's own, not one invented here.

        ``turn_boundary.observe`` already states that a declaration is ADDED to the
        derived work and "can never win" against authoritative state; the Watchdog goes
        one step further and accepts no declaration at all.
        """
        from scripts.deterministic_workflow import turn_boundary
        source = Path(turn_boundary.__file__).read_text(encoding="utf-8")
        self.assertIn("can never win", source)
        self.assertIn("ADDED to the runnable work", source)


class RealObservationAdapterTests(unittest.TestCase):
    """The concrete adapter's three-way answer, over a real (empty) artifact tree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "artifacts" / "runs" / RUN).mkdir(parents=True)

    def adapter(self, **kwargs):
        from scripts.deterministic_workflow.recovery_runtime import RunObservationAdapter
        return RunObservationAdapter(self.base, **kwargs)

    def test_no_orca_listing_authority_is_UNSUPPORTED_not_no_dispatch_running(self):
        with self.assertRaises(module.ObservationUnsupported):
            self.adapter().orca_state(RUN)

    def test_no_capability_authority_is_UNSUPPORTED(self):
        with self.assertRaises(module.ObservationUnsupported):
            self.adapter().declared_capabilities(RUN)

    def test_an_absent_pause_record_is_an_ABSENCE_not_a_refusal(self):
        self.assertIsNone(self.adapter().pause_state(RUN))

    def test_a_CORRUPT_pause_record_RAISES_and_is_never_read_as_absent(self):
        (self.base / "artifacts" / "runs" / RUN / ".pause_state.json").write_text(
            "{not json", encoding="utf-8")
        with self.assertRaises(module.ObservationUnavailable):
            self.adapter().pause_state(RUN)

    def test_an_unreadable_pause_record_is_reported_by_the_TRI_VALUED_wait_read(self):
        """The OS-44 function swallows this; the Watchdog's own read must not."""
        (self.base / "artifacts" / "runs" / RUN / ".pause_state.json").write_text(
            "{not json", encoding="utf-8")
        answer = self.adapter().durable_wait(RUN)
        self.assertEqual(answer["evidence"], ())
        self.assertEqual(answer["unreadable"], ("os31_pause_record",))
        from scripts.deterministic_workflow import turn_boundary
        self.assertEqual(turn_boundary.pause_record_status(RUN, artifact_base=self.base),
                         "", "the OS-44 read still fails open, unchanged")

    def test_an_armed_pause_is_reported_as_wait_evidence(self):
        import json
        (self.base / "artifacts" / "runs" / RUN / ".pause_state.json").write_text(
            json.dumps({"schema_version": "os31.pause_record.v2",
                        "record": {"run_id": RUN, "status": "WAITING_FOR_INPUT"}}),
            encoding="utf-8")
        answer = self.adapter().durable_wait(RUN)
        self.assertEqual(answer["evidence"], ("os31_pause_record",))
        self.assertEqual(answer["unreadable"], ())

    def test_an_absent_checkpoint_is_reported_present_False(self):
        answer = self.adapter().checkpoint_state(RUN)
        self.assertFalse(answer["present"])

    def test_no_foreign_lease_is_None_rather_than_a_guess(self):
        self.assertIsNone(self.adapter().foreign_lease(RUN))

    def test_a_live_FOREIGN_lease_is_reported(self):
        from scripts.deterministic_workflow import recovery_store
        from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
        clock = ManualLeaseClock()
        recovery_store.store_for(RUN, artifact_base=self.base, clock=clock,
                                 owner_id="host:pid999").claim(RUN)
        answer = self.adapter(clock=clock, owner_id="host:pid1").foreign_lease(RUN)
        self.assertEqual(answer["owner_id"], "host:pid999")
        clock.advance(120.0)
        self.assertIsNone(self.adapter(clock=clock, owner_id="host:pid1").foreign_lease(RUN))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# ======================================================================================
# TEST phase, gap G-6.  CON-4 PER AUTHORITY at the BUILDER, not only per fact at the
# classifier.
#
# ``test_os43_classifier.ConCorrectionWitnessTests.test_T10b_*`` runs per member of
# ``SAFETY_RELEVANT_FACTS``, but it builds its witnesses by hand with
# ``make_snapshot(support=...)``: it therefore proves the ORDERING is right and proves
# nothing about whether an authority that answers "nothing covers this" actually reaches
# that support value.  The seam between what a test constructs and what the product
# composes is exactly where this run's three defects lived, so it is closed here: every
# one of the seven port methods is made UNSUPPORTED in turn, through the REAL
# ``snapshot`` builder, and the run is required to stop at R2 before any actionable rule.
# ======================================================================================
class UnsupportedAuthorityPerPortTests(unittest.TestCase):
    """CON-4 / SAFE-6: each authority in turn, through the real builder and classifier."""

    PORTS = ("orca_state", "checkpoint_state", "pause_state", "durable_wait",
             "delivery_obligations", "foreign_lease", "declared_capabilities")

    def build(self, **answers):
        return snapshot(RUN, observation=FakeObservationPort(**answers),
                        liveness=FakeLivenessPort(), ledger={})

    def test_the_port_set_is_exactly_the_protocol_s_own(self):
        """So a NEW authority cannot be added without being covered here."""
        from scripts.deterministic_workflow.ports import RunObservationPort
        declared = tuple(name for name in vars(RunObservationPort)
                         if not name.startswith("_"))
        self.assertEqual(sorted(declared), sorted(self.PORTS))

    def test_EVERY_authority_that_answers_UNSUPPORTED_stops_the_run_at_R2(self):
        from scripts.deterministic_workflow.watchdog_classifier import classify
        for name in self.PORTS:
            with self.subTest(authority=name):
                # The R11 shape in every other respect: a runnable next node, an expired
                # Coordinator, nothing in flight.  Only this one authority is uncovered.
                result = self.build(**{
                    name: FakeObservationPort.RAISE_UNSUPPORTED,
                    **({} if name == "checkpoint_state" else {
                        "checkpoint_state": {"present": True, "run_status": "ACTIVE",
                                             "next_node": "PREPARE_WORKER",
                                             "thread_id": "t", "checkpoint_ns": "",
                                             "head_checkpoint_id": "cp_1",
                                             "status_authority": "workflow_checkpoint"}}),
                })
                self.assertFalse(result.facts["F1"],
                                 f"{name}: nothing raised, so this is not R1's business")
                self.assertTrue(result.facts["F11"],
                                f"{name}: an authority that covers nothing must not read "
                                "as a KNOWN false")
                classification = classify(result)
                self.assertEqual(classification.rule_index, 2, name)
                self.assertEqual(classification.state, "UNSUPPORTED_FAIL_CLOSED", name)
                self.assertFalse(classification.actionable, name)

    def test_the_SAME_seven_authorities_answering_normally_reach_R11(self):
        """The paired positive.  Without it the test above would also pass on a builder
        that set F11 unconditionally, which would make the Watchdog inert."""
        from scripts.deterministic_workflow.watchdog_classifier import classify
        result = self.build(checkpoint_state={"present": True, "run_status": "ACTIVE",
                                              "next_node": "PREPARE_WORKER",
                                              "thread_id": "t", "checkpoint_ns": "",
                                              "head_checkpoint_id": "cp_1",
                                              "status_authority": "workflow_checkpoint"})
        self.assertFalse(result.facts["F11"])
        classification = classify(result)
        self.assertEqual((classification.state, classification.rule_index),
                         ("STALLED_RECOVERABLE", 11))
