"""OS-43 U-9: the DI-2 (beta) run-rooted watchdog ledger and its strict fold.

The four T-9 conformance assertions live here, because beta's one real risk is two ledgers
disagreeing about one run and the answer to it is a DISJOINT AUTHORITY PARTITION rather
than a convention.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import run_logging
from scripts.deterministic_workflow import watchdog_audit as module

RUN = "run_a"


class VocabularyTests(unittest.TestCase):
    def test_the_event_vocabulary_is_closed_at_nine(self):
        self.assertEqual(len(module.WATCHDOG_AUDIT_EVENTS), 9)

    def test_an_unknown_event_is_refused_BEFORE_anything_is_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(module.WatchdogAuditError):
                module.append_watchdog_audit_record(RUN, "not_an_event", {}, base=tmp)
            self.assertFalse(module.watchdog_audit_dir(RUN, base=tmp).exists()
                             and list(module.watchdog_audit_dir(RUN, base=tmp).iterdir()))

    def test_the_identity_bearing_subset_is_the_four_that_name_ONE_attempt(self):
        self.assertEqual(set(module.IDENTITY_BEARING_WATCHDOG_EVENTS),
                         {"watchdog_claim_opened", "watchdog_resume_outcome",
                          "watchdog_failure", "watchdog_escalated"})


class AuthorityPartitionTests(unittest.TestCase):
    """T-9.  Beta costs OS-44 nothing, and this is what makes that checkable."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_the_two_event_vocabularies_are_DISJOINT(self):
        self.assertFalse(set(module.WATCHDOG_AUDIT_EVENTS)
                         & set(run_logging.COORDINATOR_AUDIT_EVENTS))

    def test_COORDINATOR_AUDIT_EVENTS_and_its_schema_version_are_UNCHANGED(self):
        """DI-2's decisive consequence: alpha would have had to widen exactly this."""
        self.assertEqual(run_logging.COORDINATOR_AUDIT_SCHEMA_VERSION, "1.0")
        self.assertEqual(len(run_logging.COORDINATOR_AUDIT_EVENTS), 13)
        for name in ("delivery_processed", "delivery_acknowledged", "delivery_ack_retry",
                     "delivery_ack_failed", "delivery_ack_intent",
                     "delivery_ack_reconciled", "delivery_replayed", "delivery_mismatch",
                     "delivery_settlement_claimed", "delivery_settled",
                     "delivery_recovery", "quiescence_verified", "quiescence_violation"):
            self.assertIn(name, run_logging.COORDINATOR_AUDIT_EVENTS)

    def test_no_watchdog_record_carries_a_delivery_id(self):
        module.append_watchdog_audit_record(RUN, "watchdog_detected",
                                            {"classified_state": "STALLED_RECOVERABLE"},
                                            base=self.base)
        for record in module.read_watchdog_audit(RUN, base=self.base):
            self.assertNotIn("delivery_id", record)

    def test_replay_delivery_ledger_is_IDENTICAL_with_and_without_a_watchdog_ledger(self):
        run_logging.append_coordinator_audit_record(
            RUN, "delivery_processed", {"delivery_id": "d1"}, base=self.base)
        before = run_logging.replay_delivery_ledger(RUN, base=self.base)
        for event in module.WATCHDOG_AUDIT_EVENTS:
            payload = {"recovery_id": "rid"} if event in \
                module.IDENTITY_BEARING_WATCHDOG_EVENTS else {}
            module.append_watchdog_audit_record(RUN, event, payload, base=self.base)
        after = run_logging.replay_delivery_ledger(RUN, base=self.base)
        self.assertEqual(before, after,
                         "nothing the Watchdog writes is ever seen by the OS-44 fold")

    def test_the_open_orchestrator_log_vocabulary_is_where_the_new_names_went(self):
        for name in ("EVENT_RUN_RECOVERY_STARTED", "EVENT_RUN_RECOVERED",
                     "EVENT_RUN_RECOVERY_REFUSED", "EVENT_WATCHDOG_ESCALATION"):
            self.assertTrue(hasattr(run_logging, name))
            self.assertNotIn(getattr(run_logging, name),
                             run_logging.COORDINATOR_AUDIT_EVENTS)


class AppendAndFoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def append(self, event, **record):
        return module.append_watchdog_audit_record(RUN, event, record, base=self.base)

    def test_sequences_are_allocated_and_never_overwritten(self):
        first = self.append("watchdog_sweep_started")[1]
        second = self.append("watchdog_sweep_completed")[1]
        self.assertEqual((first, second), (0, 1))
        published = sorted(path.name for path in
                           module.watchdog_audit_dir(RUN, base=self.base).iterdir()
                           if path.is_dir() and path.name.isdigit())
        self.assertEqual(published, ["000000", "000001"])

    def test_two_writers_on_one_run_get_two_sequences(self):
        keys = {self.append("watchdog_detected")[1] for _ in range(4)}
        self.assertEqual(len(keys), 4)

    def test_a_published_record_is_never_edited(self):
        path, _sequence = self.append("watchdog_detected")
        before = Path(path).joinpath("record.json").read_bytes()
        self.append("watchdog_detected")
        self.assertEqual(Path(path).joinpath("record.json").read_bytes(), before)

    def test_an_ABSENT_ledger_folds_to_an_empty_state_and_raises_NOTHING(self):
        self.assertEqual(module.replay_watchdog_ledger("run_never_seen",
                                                       base=self.base), {})

    def test_a_TRUNCATED_history_raises_rather_than_being_returned(self):
        self.append("watchdog_claim_opened", recovery_id="rid", head_before="cp_1")
        record = (module.watchdog_audit_dir(RUN, base=self.base) / "000000"
                  / "record.json")
        record.write_text("{not json", encoding="utf-8")
        with self.assertRaises(module.WatchdogAuditError):
            module.replay_watchdog_ledger(RUN, base=self.base)

    def test_a_wrong_schema_version_is_refused(self):
        self.append("watchdog_detected")
        record = (module.watchdog_audit_dir(RUN, base=self.base) / "000000"
                  / "record.json")
        payload = json.loads(record.read_text(encoding="utf-8"))
        payload["audit_schema_version"] = "os43.watchdog_audit.v0"
        record.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(module.WatchdogAuditError):
            module.replay_watchdog_ledger(RUN, base=self.base)

    def test_a_record_filed_under_another_run_is_refused(self):
        self.append("watchdog_detected")
        record = (module.watchdog_audit_dir(RUN, base=self.base) / "000000"
                  / "record.json")
        payload = json.loads(record.read_text(encoding="utf-8"))
        payload["run_id"] = "somebody_else"
        record.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(module.WatchdogAuditError):
            module.replay_watchdog_ledger(RUN, base=self.base)

    def test_an_identity_bearing_record_with_no_recovery_id_is_refused(self):
        self.append("watchdog_detected")
        record = (module.watchdog_audit_dir(RUN, base=self.base) / "000000"
                  / "record.json")
        payload = json.loads(record.read_text(encoding="utf-8"))
        payload["event"] = "watchdog_claim_opened"
        record.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(module.WatchdogAuditError):
            module.replay_watchdog_ledger(RUN, base=self.base)

    def test_the_fold_reconstructs_exactly_what_the_gate_consumes(self):
        self.append("watchdog_claim_opened", recovery_id="rid", head_before="cp_1")
        self.append("watchdog_resume_outcome", recovery_id="rid",
                    outcome_status="CONFLICT", outcome_code="RECOVERY_CLAIM_HELD")
        self.append("watchdog_failure", recovery_id="rid", outcome_status="CONFLICT",
                    outcome_code="RECOVERY_CLAIM_HELD", attempt_ordinal=1,
                    backoff_until=1234.0)
        folded = module.replay_watchdog_ledger(RUN, base=self.base)["rid"]
        self.assertEqual(folded["attempts"], 1)
        self.assertEqual(folded["last_outcome"], "CONFLICT")
        self.assertEqual(folded["conflicts"], 1)
        self.assertEqual(folded["backoff_until"], 1234.0)
        self.assertEqual(folded["head_before"], "cp_1")
        self.assertFalse(folded["terminal"])

    def test_an_escalation_marks_the_identity_terminal(self):
        self.append("watchdog_claim_opened", recovery_id="rid", head_before="cp_1")
        self.append("watchdog_escalated", recovery_id="rid",
                    escalation="escalation_not_recoverable")
        folded = module.replay_watchdog_ledger(RUN, base=self.base)["rid"]
        self.assertTrue(folded["terminal"])
        self.assertEqual(folded["escalation"], "escalation_not_recoverable")


class AppendOnlyIsNotDeduplicationTests(unittest.TestCase):
    """The required negative assertion: a LOG, not a deduplicator."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_the_ledger_is_never_consulted_to_decide_whether_an_effect_occurred(self):
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertIn("a LOG, not a deduplicator", source)
        # And the fold reports what HAPPENED, never what MAY happen: it carries no
        # "already applied" verdict of its own.
        module.append_watchdog_audit_record(
            RUN, "watchdog_claim_opened", {"recovery_id": "rid", "head_before": "cp_1"},
            base=self.base)
        folded = module.replay_watchdog_ledger(RUN, base=self.base)["rid"]
        self.assertNotIn("already_applied", folded)
        self.assertNotIn("effect_performed", folded)

    def test_two_identical_claim_records_are_TWO_rows_not_one(self):
        for _ in range(2):
            module.append_watchdog_audit_record(
                RUN, "watchdog_claim_opened",
                {"recovery_id": "rid", "head_before": "cp_1"}, base=self.base)
        self.assertEqual(len(module.read_watchdog_audit(RUN, base=self.base)), 2)
        self.assertEqual(module.replay_watchdog_ledger(RUN,
                                                       base=self.base)["rid"]["attempts"],
                         2, "the ledger COUNTS attempts; it does not collapse them, and "
                            "deduplication is the engine's head-keyed identity")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
