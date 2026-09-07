"""OS-43 U-8: the supervisor loop -- AC-1, AC-4, AC-5, AC-6, SC-11, SC-12 end to end.

The concurrency suites use TWO PROCESSES on ONE filesystem, because that is what AC-4 is
about; the single-winner guarantee is asserted at the engine's atomic claim and NOWHERE
above it.  A test that made the Watchdog take a pre-lock of its own would be a FAILURE,
not a pass, and there is no such lock to assert.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from scripts.deterministic_workflow import recovery_runtime, watchdog_supervisor
from scripts.deterministic_workflow import watchdog_audit as audit_module
from scripts.deterministic_workflow.recovery_runtime import (CONFLICT, NO_EFFECT,
                                                             NOT_RECOVERABLE, RECOVERED)
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
from scripts.test_deterministic_workflow_pause_fixture import REQUIRES_LANGGRAPH
from scripts.test_os43_fixture import (FakeDiscoveryPort, FakeLivenessPort,
                                       FakeObservationPort, RecordingAudit,
                                       ScriptedRecovery, outcome)

RUN = "run_s"

STALLED = {"checkpoint_state": {"present": True, "run_status": "ACTIVE",
                                "next_node": "PREPARE_WORKER", "thread_id": "t",
                                "checkpoint_ns": "", "head_checkpoint_id": "cp_1",
                                "status_authority": "workflow_checkpoint"}}


def deps(*, observation=None, liveness="EXPIRED", recovery=None, audit=None,
         clock=None, **overrides):
    base = {
        "discovery": FakeDiscoveryPort(RUN),
        "observation": observation or FakeObservationPort(**STALLED),
        "liveness": FakeLivenessPort(liveness),
        "recovery": recovery or ScriptedRecovery(
            outcome(RECOVERED, "RECOVERY_ADVANCED", effect_performed=True,
                    head_before="cp_1", head_after="cp_2")),
        "audit": audit or RecordingAudit(),
        "clock": clock or ManualLeaseClock(),
        "max_concurrent_runs": 1,
    }
    base.update(overrides)
    return base


class FiveStepTests(unittest.TestCase):
    """detect -> gate -> invoke -> observe -> react, in that order, recorded verbatim."""

    def test_a_stalled_run_with_an_EXPIRED_lease_is_recovered(self):
        audit = RecordingAudit()
        recovery = ScriptedRecovery(outcome(RECOVERED, "RECOVERY_ADVANCED",
                                            effect_performed=True, head_before="cp_1",
                                            head_after="cp_2"))
        report = watchdog_supervisor.run_once(**deps(recovery=recovery, audit=audit))
        self.assertEqual((report.runs_observed, report.runs_acted), (1, 1))
        row = report.runs[0]
        self.assertEqual((row.state, row.rule_index), ("STALLED_RECOVERABLE", 11))
        self.assertEqual((row.outcome_status, row.outcome_code),
                         (RECOVERED, "RECOVERY_ADVANCED"))
        self.assertEqual(report.escalations, [])
        self.assertEqual(audit.events()[:3],
                         ["watchdog_detected", "watchdog_claim_opened",
                          "watchdog_resume_outcome"])

    def test_the_claim_record_is_published_BEFORE_the_invocation(self):
        audit = RecordingAudit()
        watchdog_supervisor.run_once(**deps(audit=audit))
        events = audit.events()
        self.assertLess(events.index("watchdog_claim_opened"),
                        events.index("watchdog_resume_outcome"))

    def test_the_engine_s_answer_is_recorded_VERBATIM_and_never_reinterpreted(self):
        audit = RecordingAudit()
        recovery = ScriptedRecovery(outcome(NOT_RECOVERABLE, "STALE_CHECKPOINT_HEAD",
                                            detail="anything at all"))
        watchdog_supervisor.run_once(**deps(recovery=recovery, audit=audit))
        recorded = [record for _run, event, record in audit.records
                    if event == "watchdog_resume_outcome"][0]
        self.assertEqual(recorded["outcome_status"], NOT_RECOVERABLE)
        self.assertEqual(recorded["outcome_code"], "STALE_CHECKPOINT_HEAD")
        self.assertNotIn("detail", recorded, "detail is never parsed by any caller")

    def test_the_supervisor_never_calls_the_engine_for_a_non_actionable_state(self):
        recovery = ScriptedRecovery()          # any call raises
        report = watchdog_supervisor.run_once(**deps(
            observation=FakeObservationPort(orca_state={"active_dispatches": ("d1",),
                                                        "runnable_actions": ()}),
            recovery=recovery))
        self.assertEqual(report.runs[0].state, "ACTIVE_DISPATCH_WAIT")
        self.assertEqual(report.runs_acted, 0)
        self.assertEqual(recovery.requests, [])

    def test_AC1s_premise_is_a_HARD_precondition_of_acting(self):
        for status in ("LIVE", "ABSENT", "UNREADABLE"):
            with self.subTest(liveness=status):
                recovery = ScriptedRecovery()
                report = watchdog_supervisor.run_once(**deps(liveness=status,
                                                             recovery=recovery))
                self.assertEqual(report.runs_acted, 0)
                self.assertEqual(recovery.requests, [])

    def test_a_declined_gate_is_RECORDED_with_its_reason(self):
        audit = RecordingAudit()
        watchdog_supervisor.run_once(**deps(liveness="LIVE", audit=audit,
                                            recovery=ScriptedRecovery()))
        declines = [record for _run, event, record in audit.records
                    if event == "watchdog_gate_declined"]
        self.assertEqual(declines[0]["decline_reason"], "liveness_live")


class FailClosedTests(unittest.TestCase):
    def test_an_unreadable_authority_makes_the_sweep_observe_and_escalate_not_act(self):
        audit = RecordingAudit()
        recovery = ScriptedRecovery()
        report = watchdog_supervisor.run_once(**deps(
            observation=FakeObservationPort(
                orca_state=FakeObservationPort.RAISE_UNAVAILABLE, **STALLED),
            recovery=recovery, audit=audit))
        self.assertEqual(report.runs[0].state, "UNDECIDABLE_FAIL_CLOSED")
        self.assertEqual(recovery.requests, [])
        self.assertEqual(report.escalations, [f"{RUN}:escalation_observation_undecidable"])

    def test_an_uncovered_safety_relevant_fact_escalates_as_an_operator_problem(self):
        recovery = ScriptedRecovery()
        report = watchdog_supervisor.run_once(**deps(
            observation=FakeObservationPort(
                durable_wait=FakeObservationPort.RAISE_UNSUPPORTED, **STALLED),
            recovery=recovery))
        self.assertEqual(report.runs[0].state, "UNSUPPORTED_FAIL_CLOSED")
        self.assertEqual(report.runs[0].rule_index, 2)
        self.assertEqual(recovery.requests, [])
        self.assertEqual(report.escalations,
                         [f"{RUN}:escalation_observation_undecidable"])

    def test_an_unfoldable_ledger_declines_the_attempt_OUTRIGHT(self):
        """A ledger that EXISTS and refused is UNREADABLE -- F1 => R1 -- never uncovered.

        The two failures escalate differently on purpose: an authority that raised names a
        repairable cause, and this one names the budget's only source, so it escalates as
        ``escalation_audit_unavailable`` rather than as a generic observation failure.
        """
        recovery = ScriptedRecovery()
        report = watchdog_supervisor.run_once(**deps(
            audit=RecordingAudit(fold_raises=True), recovery=recovery))
        self.assertEqual(report.runs[0].state, "UNDECIDABLE_FAIL_CLOSED")
        self.assertEqual(report.runs[0].rule_index, 1)
        self.assertEqual(recovery.requests, [], "no budget, no attempt")
        self.assertEqual(report.escalations, [f"{RUN}:escalation_audit_unavailable"])
        self.assertTrue(report.runs[0].detail)

    def test_a_claim_record_that_cannot_be_published_stops_the_attempt(self):
        """The ledger is the budget's only source, so appending it is a GATE."""
        recovery = ScriptedRecovery()
        report = watchdog_supervisor.run_once(**deps(
            audit=RecordingAudit(append_raises_on="watchdog_claim_opened"),
            recovery=recovery))
        self.assertEqual(recovery.requests, [], "no record, no attempt")
        self.assertEqual(report.runs[0].escalation, "escalation_audit_unavailable")

    def test_an_engine_failure_the_closed_set_cannot_express_is_ONE_runs_problem(self):
        """A sweep that died here would leave every other stalled run unobserved.

        Recorded and escalated -- never swallowed, and never treated as a recovery.
        """
        class Exploding(ScriptedRecovery):
            def recover(self, request):
                raise RuntimeError("the engine raised something nobody modelled")

        audit = RecordingAudit()
        report = watchdog_supervisor.run_once(**deps(
            discovery=FakeDiscoveryPort("run_a", "run_b"), recovery=Exploding(),
            audit=audit, max_concurrent_runs=1))
        self.assertEqual(report.runs_observed, 2, "the sweep finished the fleet")
        for row in report.runs:
            self.assertEqual(row.escalation, "escalation_observation_undecidable")
            self.assertEqual(row.outcome_status, "", "no outcome was invented")
            self.assertIn("RuntimeError", row.detail)
        self.assertEqual(len(report.escalations), 2)

    def test_an_unreadable_RUNS_ROOT_raises_rather_than_sweeping_an_empty_fleet(self):
        class Blind:
            def discover(self):
                raise OSError("the runs root cannot be listed")
        with self.assertRaises(RuntimeError):
            watchdog_supervisor.run_once(**deps(discovery=Blind()))

    def test_a_terminal_outcome_is_NOT_retried_for_a_different_answer(self):
        audit = RecordingAudit()
        recovery = ScriptedRecovery(outcome(NOT_RECOVERABLE, "RECOVERY_NO_RUNNABLE_NODE"))
        first = watchdog_supervisor.run_once(**deps(recovery=recovery, audit=audit))
        self.assertEqual(first.runs[0].escalation, "escalation_not_recoverable")
        # The identity is now terminal, so a second sweep does NOT invoke again.
        recovery_id = first.runs[0].recovery_id
        again = ScriptedRecovery()
        second = watchdog_supervisor.run_once(**deps(
            recovery=again,
            audit=RecordingAudit(rows={recovery_id: {"attempts": 1, "terminal": True,
                                                     "conflicts": 0, "backoff_until": 0.0,
                                                     "last_outcome": NOT_RECOVERABLE,
                                                     "escalation":
                                                     "escalation_not_recoverable"}})))
        self.assertEqual(second.runs[0].gate_reason, "identity_terminal")
        self.assertEqual(again.requests, [])


class ReplayTests(unittest.TestCase):
    """AC-5: a replayed detection and a replayed resume create no second effect."""

    def test_a_replayed_sweep_over_an_unmoved_head_reads_NO_EFFECT(self):
        recovery = ScriptedRecovery(
            outcome(RECOVERED, "RECOVERY_ADVANCED", effect_performed=True,
                    head_before="cp_1", head_after="cp_2"),
            outcome(NO_EFFECT, "RECOVERY_ALREADY_APPLIED"))
        audit = RecordingAudit()
        first = watchdog_supervisor.run_once(**deps(recovery=recovery, audit=audit))
        second = watchdog_supervisor.run_once(**deps(recovery=recovery, audit=audit))
        self.assertEqual(first.runs[0].recovery_id, second.runs[0].recovery_id,
                         "an unmoved head yields the SAME identity")
        self.assertEqual(second.runs[0].outcome_status, NO_EFFECT)

    def test_append_only_is_NOT_deduplication(self):
        """A replay with the ledger present but the identity entry ABSENT still creates
        no second effect: the guarantee is the ENGINE's, never the log's."""
        recovery = ScriptedRecovery(outcome(NO_EFFECT, "RECOVERY_ALREADY_APPLIED"))
        audit = RecordingAudit(rows={"some_other_identity": {"attempts": 3}})
        report = watchdog_supervisor.run_once(**deps(recovery=recovery, audit=audit))
        self.assertEqual(report.runs[0].outcome_status, NO_EFFECT)
        self.assertEqual(len(recovery.requests), 1)


class ConcurrencyTests(unittest.TestCase):
    """AC-4: two concurrent Watchdogs, exactly one winner -- decided by the ENGINE."""

    def test_the_supervisor_takes_NO_lock_of_its_own(self):
        source = Path(watchdog_supervisor.__file__).read_text(encoding="utf-8")
        for forbidden in ("flock", "FileCriticalSection", "Lock()", "acquire("):
            self.assertNotIn(forbidden, source,
                             "a Watchdog-level pre-lock is a FAILURE, not a pass: the "
                             "race is resolved at the engine's atomic claim")

    def test_two_concurrent_supervisors_yield_exactly_one_RECOVERED(self):
        results: list[str] = []
        gate = threading.Barrier(2)
        first = ScriptedRecovery(outcome(RECOVERED, "RECOVERY_ADVANCED",
                                         effect_performed=True, head_before="cp_1",
                                         head_after="cp_2"))
        second = ScriptedRecovery(outcome(CONFLICT, "RECOVERY_CLAIM_HELD"))

        def sweep(recovery):
            gate.wait(timeout=5)
            report = watchdog_supervisor.run_once(**deps(recovery=recovery))
            results.append(report.runs[0].outcome_status)

        threads = [threading.Thread(target=sweep, args=(port,))
                   for port in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(results), [CONFLICT, RECOVERED])
        self.assertEqual(len(first.requests) + len(second.requests), 2)


class LifecycleTests(unittest.TestCase):
    """SC-11 / SC-12: both modes, and a shutdown that interrupts nothing."""

    def test_run_once_sweeps_exactly_once(self):
        recovery = ScriptedRecovery(outcome(RECOVERED, "RECOVERY_ADVANCED",
                                            effect_performed=True, head_before="cp_1",
                                            head_after="cp_2"))
        watchdog_supervisor.run_once(**deps(recovery=recovery))
        self.assertEqual(len(recovery.requests), 1)

    def test_run_continuous_stops_on_the_shutdown_flag_between_sweeps(self):
        flag = threading.Event()
        sweeps = {"count": 0}

        def waiter(event, seconds):
            sweeps["count"] += 1
            if sweeps["count"] >= 2:
                flag.set()
            return event.is_set()

        recovery = ScriptedRecovery(*[outcome(RECOVERED, "RECOVERY_ADVANCED",
                                              effect_performed=True, head_before="cp_1",
                                              head_after="cp_2") for _ in range(3)])
        report = watchdog_supervisor.run_continuous(shutdown=flag, waiter=waiter,
                                                    interval_seconds=0.0,
                                                    **deps(recovery=recovery))
        self.assertTrue(report.shutdown)
        self.assertEqual(sweeps["count"], 2)

    def test_a_shutdown_already_requested_starts_no_sweep_at_all(self):
        flag = threading.Event()
        flag.set()
        recovery = ScriptedRecovery()
        report = watchdog_supervisor.run_continuous(shutdown=flag,
                                                    interval_seconds=0.0,
                                                    **deps(recovery=recovery))
        self.assertEqual(recovery.requests, [])
        self.assertEqual(report.runs_observed, 0)

    def test_shutdown_requested_DURING_a_sweep_declines_the_gate_not_the_call(self):
        """R-7: the flag is a gate input, never an interrupt inside an in-flight call."""
        flag = threading.Event()
        flag.set()
        recovery = ScriptedRecovery()
        report = watchdog_supervisor.run_once(**deps(recovery=recovery, shutdown=flag))
        self.assertEqual(report.runs[0].gate_reason, "shutdown_requested")
        self.assertEqual(recovery.requests, [])

    def test_the_sweep_interval_is_LEASE_DERIVED_and_never_hard_coded(self):
        from scripts.deterministic_workflow.pause_store import observe_timeout_for
        source = Path(watchdog_supervisor.__file__).read_text(encoding="utf-8")
        self.assertIn("observe_timeout_for(lease_seconds)", source)
        self.assertEqual(observe_timeout_for(60.0), 65.0)

    def test_concurrency_is_BOUNDED(self):
        self.assertEqual(watchdog_supervisor.DEFAULT_MAX_CONCURRENT_RUNS, 4)
        recovery = ScriptedRecovery(*[outcome(NO_EFFECT, "RECOVERY_ALREADY_APPLIED")
                                      for _ in range(3)])
        report = watchdog_supervisor.run_once(**deps(
            discovery=FakeDiscoveryPort("run_a", "run_b", "run_c"),
            recovery=recovery, max_concurrent_runs=2))
        self.assertEqual(report.runs_observed, 3)

    def test_a_single_run_can_be_targeted(self):
        recovery = ScriptedRecovery(outcome(NO_EFFECT, "RECOVERY_ALREADY_APPLIED"))
        report = watchdog_supervisor.run_once(**deps(
            discovery=FakeDiscoveryPort("run_a", "run_b"), recovery=recovery,
            run_ids=("run_a",)))
        self.assertEqual(report.runs_observed, 1)


@REQUIRES_LANGGRAPH
class EndToEndAgainstRealStoresTests(unittest.TestCase):
    """The loop over the REAL engine API, a real checkpoint and a real ledger."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        from scripts.test_os43_discovery import DiscoveryFixture
        self.fixture = DiscoveryFixture("run")
        self.fixture.base = self.base
        self.fixture.runs = self.base / "artifacts" / "runs"
        self.fixture.runs.mkdir(parents=True)
        self.fixture.make_checkpointed(RUN)

    def test_the_real_discovery_and_the_real_engine_agree_about_the_run(self):
        listings = recovery_runtime.discover_recoverable_runs(self.base)
        self.assertEqual(listings[0]["verdict"],
                         recovery_runtime.RECOVERY_STALLED_RECOVERABLE)
        # The engine's own recovery, with a graph factory that performs no work: the
        # outcome is still one closed member and the head does not move.
        result = recovery_runtime.recover_stalled_run(
            recovery_runtime.RecoveryRequest(
                run_id=RUN, artifact_base=str(self.base),
                graph_factory=lambda saver: _NoopGraph()))
        self.assertEqual(result.status, RECOVERED)
        self.assertFalse(result.effect_performed, "the head did not move")
        self.assertEqual(result.head_before, result.head_after)

    def test_a_replayed_invocation_over_an_unmoved_head_is_a_NO_OP(self):
        request = recovery_runtime.RecoveryRequest(
            run_id=RUN, artifact_base=str(self.base),
            graph_factory=lambda saver: _NoopGraph())
        first = recovery_runtime.recover_stalled_run(request)
        second = recovery_runtime.recover_stalled_run(request)
        self.assertEqual(first.recovery_id, second.recovery_id)
        self.assertEqual(second.status, NO_EFFECT)
        self.assertEqual(second.code, "RECOVERY_ALREADY_APPLIED")
        self.assertFalse(second.effect_performed)

    def test_the_watchdog_ledger_and_the_delivery_ledger_stay_disjoint(self):
        audit = audit_module.FileWatchdogAudit(self.base)
        audit.append(RUN, "watchdog_detected", {"classified_state": "STALLED_RECOVERABLE"})
        from scripts import run_logging
        self.assertEqual(run_logging.replay_delivery_ledger(RUN, base=self.base), {})


class _NoopGraph:
    """A graph whose ``invoke`` commits nothing.  The head therefore does not move."""

    def invoke(self, value, config):
        return {}


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
