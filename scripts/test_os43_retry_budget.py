"""OS-43 U-7: the action gate -- budget, backoff, escalation and restart reconstruction.

Nothing sleeps.  Every backoff deadline and every expiry is computed against
:class:`ManualLeaseClock` through ``LeaseClockPort``, so the whole suite is deterministic
by construction rather than by being fast enough.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import lease_keeper
from scripts.deterministic_workflow import watchdog_state as module
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
from scripts.deterministic_workflow.watchdog_classifier import Classification
from scripts.deterministic_workflow.watchdog_state import (GATE_ACT, GATE_DECLINE,
                                                           backoff_delay, gate, react)

RID = "recovery_1"


def actionable(state="STALLED_RECOVERABLE"):
    return Classification(state, 11, True, {}, "digest")


def observing(state="ACTIVE_DISPATCH_WAIT"):
    return Classification(state, 8, False, {}, "digest")


def row(**overrides):
    base = module.empty_row(RID)
    base.update(overrides)
    return base


class GateOrderTests(unittest.TestCase):
    """The order is the contract, and every position is asserted, not assumed."""

    def setUp(self):
        self.clock = ManualLeaseClock()

    def call(self, classification=None, **kwargs):
        options = {"recovery_id": RID, "ledger": {}, "liveness_status": "EXPIRED",
                   "clock": self.clock}
        options.update(kwargs)
        return gate(classification or actionable(), **options)

    def test_a_non_actionable_classification_declines_first(self):
        decision = self.call(observing())
        self.assertEqual((decision.action, decision.reason),
                         (GATE_DECLINE, "not_actionable"))

    def test_shutdown_outranks_every_other_decline(self):
        decision = self.call(shutdown=True, ledger={RID: row(terminal=True, attempts=99)})
        self.assertEqual(decision.reason, "shutdown_requested")

    def test_a_terminal_identity_is_never_reopened(self):
        """AC-7 mechanism 4: refusal is sticky PER IDENTITY, durably."""
        decision = self.call(ledger={RID: row(terminal=True)})
        self.assertEqual(decision.reason, "identity_terminal")

    def test_an_exhausted_budget_declines_before_backoff_is_consulted(self):
        decision = self.call(ledger={RID: row(attempts=5,
                                              backoff_until=self.clock.time() + 999)})
        self.assertEqual(decision.reason, "budget_exhausted")

    def test_a_pending_backoff_declines_and_reports_its_deadline(self):
        deadline = self.clock.time() + 30.0
        decision = self.call(ledger={RID: row(attempts=1, backoff_until=deadline)})
        self.assertEqual(decision.reason, "backoff_pending")
        self.assertEqual(decision.backoff_until, deadline)
        self.clock.advance(31.0)
        self.assertEqual(self.call(
            ledger={RID: row(attempts=1, backoff_until=deadline)}).action, GATE_ACT)

    def test_liveness_sits_BELOW_backoff_so_a_dead_coordinator_never_skips_one(self):
        deadline = self.clock.time() + 30.0
        decision = self.call(ledger={RID: row(attempts=1, backoff_until=deadline)},
                             liveness_status="EXPIRED")
        self.assertEqual(decision.reason, "backoff_pending")

    def test_only_EXPIRED_satisfies_AC1s_premise(self):
        for status in ("LIVE", "ABSENT", "UNREADABLE", "", "anything"):
            with self.subTest(status=status):
                decision = self.call(liveness_status=status)
                self.assertEqual(decision.action, GATE_DECLINE)
                self.assertIn(decision.reason, ("liveness_live", "liveness_absent"))
        self.assertEqual(self.call(liveness_status="EXPIRED").action, GATE_ACT)

    def test_an_unfoldable_ledger_declines_and_NEVER_starts_a_fresh_budget(self):
        decision = self.call(ledger=None)
        self.assertEqual(decision.reason, "audit_unavailable")

    def test_ACT_reports_the_next_attempt_ordinal(self):
        decision = self.call(ledger={RID: row(attempts=2)})
        self.assertEqual((decision.action, decision.attempt_ordinal), (GATE_ACT, 3))

    def test_the_decline_vocabulary_is_closed(self):
        with self.assertRaises(ValueError):
            module.GateDecision(GATE_DECLINE, "because_i_said_so")
        with self.assertRaises(ValueError):
            module.GateDecision(GATE_ACT, "not_actionable")


class BackoffTests(unittest.TestCase):
    """SC-7: deterministic, lease-derived, capped -- and no jitter, on purpose."""

    def test_the_base_is_the_lease_derived_heartbeat_interval(self):
        self.assertEqual(backoff_delay(1, lease_seconds=60.0),
                         lease_keeper.heartbeat_interval_for(60.0))

    def test_the_delay_doubles_and_is_capped_at_sixteen_leases(self):
        delays = [backoff_delay(n, lease_seconds=60.0) for n in range(1, 12)]
        self.assertEqual(delays[:4], [20.0, 40.0, 80.0, 160.0])
        self.assertTrue(all(delay <= 16.0 * 60.0 for delay in delays))
        self.assertEqual(delays[-1], 16.0 * 60.0)

    def test_it_is_a_pure_function_of_the_ordinal_and_the_lease(self):
        self.assertEqual(backoff_delay(3, lease_seconds=30.0),
                         backoff_delay(3, lease_seconds=30.0),
                         "no jitter: jitter would make the fake-clock suite "
                         "non-deterministic and NG-4 puts multi-host herd out of scope")


class OutcomeActionTableTests(unittest.TestCase):
    """TOTAL over the closed outcome set; there is no default branch."""

    def setUp(self):
        self.clock = ManualLeaseClock()

    def react(self, status, **kwargs):
        options = {"row": row(), "attempt_ordinal": 1, "lease_seconds": 60.0,
                   "clock": self.clock}
        options.update(kwargs)
        return react(status, **options)

    def test_every_closed_outcome_has_a_row(self):
        for status in module.RECOVERY_OUTCOMES:
            self.assertIsInstance(self.react(status), module.Reaction)
        with self.assertRaises(ValueError):
            self.react("SOMETHING_ELSE")

    def test_RECOVERED_resets_and_is_not_terminal(self):
        reaction = self.react("RECOVERED")
        self.assertFalse(reaction.budget_consumed)
        self.assertFalse(reaction.terminal)
        self.assertEqual(reaction.escalation, "")

    def test_NO_EFFECT_settles_the_identity_with_nothing_owed(self):
        reaction = self.react("NO_EFFECT")
        self.assertTrue(reaction.terminal)
        self.assertEqual(reaction.escalation, "")

    def test_CONFLICT_does_NOT_consume_the_recovery_budget(self):
        """Another claimant working successfully is not this Watchdog failing."""
        reaction = self.react("CONFLICT")
        self.assertFalse(reaction.budget_consumed)
        self.assertEqual(reaction.conflicts, 1)
        self.assertGreater(reaction.backoff_until, self.clock.time())

    def test_CONFLICT_still_escalates_at_its_own_bounded_cap(self):
        reaction = self.react("CONFLICT", row=row(conflicts=7))
        self.assertEqual(reaction.escalation, "escalation_state_conflict")
        self.assertTrue(reaction.terminal)

    def test_NOT_RECOVERABLE_and_UNSUPPORTED_are_terminal_on_the_FIRST_occurrence(self):
        """Retrying an unchanged head cannot change the answer -- AC-7, mechanically."""
        for status, escalation in (("NOT_RECOVERABLE", "escalation_not_recoverable"),
                                   ("UNSUPPORTED", "escalation_unsupported_capability")):
            reaction = self.react(status)
            self.assertTrue(reaction.terminal, status)
            self.assertEqual(reaction.escalation, escalation)

    def test_REFUSED_retries_within_the_budget_then_escalates(self):
        early = self.react("REFUSED", attempt_ordinal=1)
        self.assertFalse(early.terminal)
        self.assertEqual(early.escalation, "")
        self.assertGreater(early.backoff_until, self.clock.time())
        last = self.react("REFUSED", attempt_ordinal=5)
        self.assertTrue(last.terminal)
        self.assertEqual(last.escalation, "escalation_budget_exhausted")

    def test_the_escalation_vocabulary_is_closed(self):
        for status in module.RECOVERY_OUTCOMES:
            escalation = self.react(status, row=row(conflicts=99),
                                    attempt_ordinal=99).escalation
            if escalation:
                self.assertIn(escalation, module.WATCHDOG_ESCALATIONS)


class RestartReconstructionTests(unittest.TestCase):
    """AC-6: the budget has no in-memory home, so a restart cannot reset it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.clock = ManualLeaseClock()

    def audit(self):
        from scripts.deterministic_workflow.watchdog_audit import FileWatchdogAudit
        return FileWatchdogAudit(self.base)

    def open_attempt(self, audit, ordinal, outcome="REFUSED",
                     code="PAUSE_RECORD_CORRUPT"):
        audit.append("run_r", "watchdog_claim_opened",
                     {"recovery_id": RID, "head_before": "cp_1"})
        audit.append("run_r", "watchdog_resume_outcome",
                     {"recovery_id": RID, "outcome_status": outcome,
                      "outcome_code": code, "head_before": "cp_1"})
        audit.append("run_r", "watchdog_failure",
                     {"recovery_id": RID, "outcome_status": outcome,
                      "outcome_code": code, "attempt_ordinal": ordinal,
                      "backoff_until": self.clock.time() + 40.0})

    def test_a_kill_and_restart_mid_budget_resumes_the_SAME_budget_and_deadline(self):
        writer = self.audit()
        for ordinal in (1, 2, 3):
            self.open_attempt(writer, ordinal)
        # Every in-memory object is dropped; a FRESH reader folds the durable ledger.
        reader = self.audit()
        folded = reader.fold("run_r")
        self.assertEqual(folded[RID]["attempts"], 3)
        self.assertEqual(folded[RID]["backoff_until"], self.clock.time() + 40.0)
        decision = gate(actionable(), recovery_id=RID, ledger=folded,
                        liveness_status="EXPIRED", clock=self.clock)
        self.assertEqual(decision.reason, "backoff_pending")
        self.clock.advance(41.0)
        decision = gate(actionable(), recovery_id=RID, ledger=folded,
                        liveness_status="EXPIRED", clock=self.clock)
        self.assertEqual(decision.attempt_ordinal, 4,
                         "the successor continues the predecessor's budget, it does not "
                         "start a new one")

    def test_a_terminal_identity_survives_the_restart(self):
        writer = self.audit()
        writer.append("run_r", "watchdog_claim_opened",
                      {"recovery_id": RID, "head_before": "cp_1"})
        writer.append("run_r", "watchdog_resume_outcome",
                      {"recovery_id": RID, "outcome_status": "NOT_RECOVERABLE",
                       "outcome_code": "RECOVERY_NO_RUNNABLE_NODE"})
        folded = self.audit().fold("run_r")
        self.assertTrue(folded[RID]["terminal"])
        self.assertEqual(gate(actionable(), recovery_id=RID, ledger=folded,
                              liveness_status="EXPIRED", clock=self.clock).reason,
                         "identity_terminal")

    def test_a_restart_that_can_read_NOTHING_fails_closed_rather_than_starting_fresh(self):
        from scripts.deterministic_workflow.watchdog_audit import WatchdogAuditError
        writer = self.audit()
        writer.append("run_r", "watchdog_claim_opened",
                      {"recovery_id": RID, "head_before": "cp_1"})
        record = (self.base / "artifacts" / "runs" / "run_r" / "watchdog_audit"
                  / "000000" / "record.json")
        record.write_text("{not json", encoding="utf-8")
        with self.assertRaises(WatchdogAuditError):
            self.audit().fold("run_r")
        self.assertEqual(gate(actionable(), recovery_id=RID, ledger=None,
                              liveness_status="EXPIRED", clock=self.clock).reason,
                         "audit_unavailable")

    def test_an_ABSENT_ledger_is_not_damage_and_folds_to_an_empty_budget(self):
        """A pre-OS-43 run correctly concludes "no attempt has been made"."""
        self.assertEqual(self.audit().fold("run_never_seen"), {})
        decision = gate(actionable(), recovery_id=RID, ledger={},
                        liveness_status="EXPIRED", clock=self.clock)
        self.assertEqual((decision.action, decision.attempt_ordinal), (GATE_ACT, 1))

    def test_the_watchdog_has_NOTHING_token_shaped_to_reconstruct(self):
        """It never receives a claim, so ownership recovery is vacuous by construction."""
        import inspect

        from scripts.deterministic_workflow import recovery_runtime
        fields = inspect.signature(recovery_runtime.RecoveryOutcome).parameters
        self.assertNotIn("lease_token", fields)
        source = Path(recovery_runtime.__file__).read_text(encoding="utf-8")
        self.assertNotIn("return.*lease_token", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
