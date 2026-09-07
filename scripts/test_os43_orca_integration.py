"""OS-43 U-11 / AC-8 half B: the REAL Orca adapter, offline.

``FakeAdapter`` was written to be recovery-safe, so proving anything on it proves nothing
about ``OrcaAdapter`` / ``OrcaRuntimeHarness``.  Every test here drives the REAL classes
through ``OfflineHarnessTestCase``, which stubs only ``_exec_orca`` -- the subprocess
boundary -- so every line of the real adapter, the real terminal ledger and the real
journal writes actually execute.

**The obligation this file exists to discharge.**  The real adapter withholds
``external_resume`` permanently and on purpose, so the O-2-shaped run terminates at
``UNSUPPORTED`` / ``IDEMPOTENCY_RECOVERY_UNSUPPORTED``.  That outcome is ASSERTED here, not
skipped: a green deterministic suite over a path the real adapter refuses is exactly the
failure this test exists to prevent.
"""
from __future__ import annotations

import unittest

from scripts.deterministic_workflow import (recovery_runtime, watchdog_state,
                                            watchdog_supervisor)
from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
from scripts.test_orca_runtime_contract import OfflineHarnessTestCase, RecordingExec
from scripts.test_os43_fixture import (FakeDiscoveryPort, FakeLivenessPort,
                                       FakeObservationPort, RecordingAudit,
                                       ScriptedRecovery, outcome)

RUN = "run_offline"


class RealAdapterCapabilityTests(OfflineHarnessTestCase):
    """The capability boundary, read off the REAL adapter over the REAL harness."""

    def adapter(self) -> OrcaAdapter:
        harness = self.build(RecordingExec())
        return OrcaAdapter(harness)

    def test_the_real_adapter_declares_external_lookup_and_NOT_external_resume(self):
        declared = self.adapter().capabilities()
        self.assertIn("external_lookup", declared)
        self.assertNotIn("external_resume", declared)
        self.assertTrue(BASE_CAPABILITIES <= declared)

    def test_the_O2_SHAPE_sets_F11_against_the_REAL_adapter(self):
        """The observation half: an outstanding reconciliation on a runtime that cannot
        re-collect the settlement is UNCOVERED, not "nothing to worry about"."""
        from scripts.deterministic_workflow import watchdog_observation
        declared = self.adapter().capabilities()
        snapshot = watchdog_observation.snapshot(
            RUN,
            observation=FakeObservationPort(
                orca_state={"active_dispatches": (),
                            "runnable_actions": ("reconcile_dispatch:T1",)},
                declared_capabilities=declared),
            liveness=FakeLivenessPort(), ledger={})
        self.assertTrue(snapshot.facts["F7"], "the O-2 shape")
        self.assertTrue(snapshot.facts["F11"],
                        "the real adapter cannot re-collect a settlement delivered to a "
                        "dead process, so this path is fail-closed")

    def test_a_run_with_NO_outstanding_reconciliation_is_not_blocked_by_the_same_gap(self):
        """DR-8's boundary: fail-closed where it matters, not everywhere."""
        from scripts.deterministic_workflow import watchdog_observation
        snapshot = watchdog_observation.snapshot(
            RUN,
            observation=FakeObservationPort(
                declared_capabilities=self.adapter().capabilities(),
                checkpoint_state={"present": True, "run_status": "ACTIVE",
                                  "next_node": "PREPARE_WORKER", "thread_id": "t",
                                  "checkpoint_ns": "", "head_checkpoint_id": "cp_1",
                                  "status_authority": "workflow_checkpoint"}),
            liveness=FakeLivenessPort(), ledger={})
        self.assertFalse(snapshot.facts["F11"])
        self.assertTrue(snapshot.facts["F5"])


class UnsupportedOutcomeIsAssertedNotSkippedTests(OfflineHarnessTestCase):
    """ANALYSIS R-6, discharged: the refusal is EXERCISED, not skipped."""

    def test_the_UNSUPPORTED_outcome_carries_the_engine_s_OWN_code(self):
        self.assertIn("IDEMPOTENCY_RECOVERY_UNSUPPORTED",
                      recovery_runtime.RECOVERY_OUTCOME_CODES[
                          recovery_runtime.UNSUPPORTED])

    def test_the_executor_raises_exactly_that_code_when_external_resume_is_absent(self):
        """Executed against the REAL adapter's capability set, not a stub of it."""
        from scripts.deterministic_workflow import executor
        harness = self.build(RecordingExec())
        adapter = OrcaAdapter(harness)
        intent = {"intent_id": "intent_x"}
        with self.assertRaises(executor.IdempotencyRecoveryError) as raised:
            executor._collect(adapter, None, intent, {"task_id": "task_1"}, "token")
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_RECOVERY_UNSUPPORTED")

    def test_the_watchdog_ESCALATES_that_outcome_and_never_retries_past_it(self):
        """AC-7 end to end on the O-2 shape: terminal on the FIRST occurrence."""
        audit = RecordingAudit()
        recovery = ScriptedRecovery(
            outcome(recovery_runtime.UNSUPPORTED, "IDEMPOTENCY_RECOVERY_UNSUPPORTED"))
        report = watchdog_supervisor.run_once(
            discovery=FakeDiscoveryPort(RUN),
            observation=FakeObservationPort(
                checkpoint_state={"present": True, "run_status": "ACTIVE",
                                  "next_node": "PREPARE_WORKER", "thread_id": "t",
                                  "checkpoint_ns": "", "head_checkpoint_id": "cp_1",
                                  "status_authority": "workflow_checkpoint"}),
            liveness=FakeLivenessPort("EXPIRED"), recovery=recovery, audit=audit,
            clock=ManualLeaseClock(), max_concurrent_runs=1)
        row = report.runs[0]
        self.assertEqual(row.outcome_status, recovery_runtime.UNSUPPORTED)
        self.assertEqual(row.outcome_code, "IDEMPOTENCY_RECOVERY_UNSUPPORTED")
        self.assertEqual(row.escalation, "escalation_unsupported_capability")
        self.assertIn(row.escalation, watchdog_state.WATCHDOG_ESCALATIONS)
        self.assertEqual(len(recovery.requests), 1,
                         "terminal on the first occurrence: retrying an undeclared "
                         "capability cannot change the answer")

    def test_nothing_in_this_module_is_skipped_to_make_the_lane_pass(self):
        """A guard on the guard: the whole point of U-11 is that it does not skip.

        Read from the FILE rather than from this class's own source, so the assertion
        covers every suite in the module and is not satisfied by its own text.
        """
        import scripts.test_os43_orca_integration as module
        from pathlib import Path
        source = Path(module.__file__).read_text(encoding="utf-8")
        start = source.index("class RealAdapterCapabilityTests")
        # Sliced to exclude THIS method, whose own text necessarily names the escapes it
        # is looking for.
        body = (source[start:source.index("    def test_nothing_in_this_module_is_")]
                + source[source.index("class RealHarnessArtifactTests"):])
        for escape in ("self.skipTest" + "(", "@unittest." + "skip",
                       "skip" + "Unless", "expected" + "Failure"):
            self.assertNotIn(escape, body,
                             "a green suite over a path the real adapter refuses is "
                             "exactly the failure this file exists to prevent")


class RealHarnessArtifactTests(OfflineHarnessTestCase):
    """The new artefacts are new FILENAMES and are invisible to the real harness."""

    def test_the_liveness_producer_runs_under_the_real_harness_and_never_raises(self):
        harness = self.build(RecordingExec())
        harness._begin_turn_boundary_liveness()
        self.addCleanup(harness._end_turn_boundary_liveness)
        from scripts.deterministic_workflow import coordinator_liveness
        status = coordinator_liveness.liveness_status(harness.run_id,
                                                      artifact_base=self.artifact_dir)
        self.assertIn(status, coordinator_liveness.LIVENESS_STATUSES)

    def test_the_real_harness_delivery_ledger_is_unaffected_by_a_watchdog_ledger(self):
        from scripts import run_logging
        from scripts.deterministic_workflow import watchdog_audit
        harness = self.build(RecordingExec())
        before = run_logging.replay_delivery_ledger(harness.run_id,
                                                    base=self.artifact_dir)
        watchdog_audit.append_watchdog_audit_record(
            harness.run_id, "watchdog_detected",
            {"classified_state": "STALLED_RECOVERABLE"}, base=self.artifact_dir)
        self.assertEqual(run_logging.replay_delivery_ledger(harness.run_id,
                                                            base=self.artifact_dir),
                         before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
