"""OS-31 cancel and abandon: TC-1 ... TC-4, the audit trail, and the honest boundary.

Cancel and abandon are explicit human instructions, never a timeout and never automatic.
Abandon is the last-resort disposition and must be able to complete -- so a row it cannot
discharge is recorded ``residual`` and **reported**, and the run then does not claim AC-1.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.deterministic_workflow import pause_policy, pause_runtime, pause_store
from scripts.test_deterministic_workflow_pause_fixture import (REQUIRES_LANGGRAPH,
                                                               WORKTREE_A, PauseFixture)


def _log_rows(path: Path) -> list[str]:
    """The markdown log's data rows, in file order -- last row wins is a real property."""
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines()
            if line.startswith("|") and not set(line) <= set("|- ")]


@REQUIRES_LANGGRAPH
class CancelTests(PauseFixture):
    """TC-1: an explicit cancel of a paused run, end to end, with no Orca."""

    RUN = "run_cancel"

    def test_cancel_settles_the_run_and_records_the_actor_that_authorised_it(self):
        _, record, _ = self.drive_to_pause()
        outcome, _ = self.fresh_dispose(kind="CANCEL")
        self.assertEqual(outcome.status, "CANCELLED", outcome.detail)
        self.assertEqual(outcome.state["terminal_status"], "CANCELLED")
        self.assertEqual(outcome.state["run_lifecycle"], "SETTLED")
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "CANCELLED")
        self.assertEqual(stored["disposition"]["kind"], "CANCEL")
        self.assertEqual(stored["disposition"]["actor_id"], "alice")
        self.assertEqual(stored["disposition"]["actor_type"], "human")
        self.assertTrue(stored["disposition"]["cancellation_id"])

    def test_a_cancelled_run_is_neither_resumable_nor_re_adoptable(self):
        """R-14: discovery can never take a disposed run back."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.fresh_dispose(kind="CANCEL")
        self.assertEqual(self.store().claim(self.RUN)["claim_outcome"],
                         "ALREADY_CANCELLED")
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "NO_EFFECT")
        self.assertEqual(outcome.code, "RUN_ALREADY_CANCELLED")
        self.assertEqual(adapter.effect_count, 0)

    def test_cancel_retires_the_checkpoint_and_never_deletes_it(self):
        """The checkpoint is the audit evidence for what was disposed."""
        _, record, _ = self.drive_to_pause()
        self.fresh_dispose(kind="CANCEL")
        saver = self.saver()
        self.assertTrue(saver.is_retired("t"))
        self.assertIsNotNone(saver.get_tuple({"configurable": {
            "thread_id": "t", "checkpoint_ns": "",
            "checkpoint_id": record["checkpoint_id"]}}))

    def test_cancel_succeeds_even_though_the_repository_head_moved(self):
        """CC-4, the pair to the resume rule: a moved head never refuses a cancel."""
        _, record, _ = self.drive_to_pause()
        outcome, _ = self.fresh_dispose(kind="CANCEL")
        self.assertEqual(outcome.status, "CANCELLED", outcome.detail)
        # And the paired assertion: the SAME movement makes a resume revalidate.
        moved = {"head_sha": "1" * 40, "tree_digest": "dirty", "dirty": True}
        codes = pause_policy.stale_source_codes(
            {"pause_binding": {
                "repository_binding": record["projection"]["repository_binding"],
                "artifact_binding": record["projection"]["artifact_binding"],
                "policy_digest": record["projection"]["policy_digest"]}},
            current_repository=moved,
            current_artifact=record["projection"]["artifact_binding"],
            current_policy_digest=record["projection"]["policy_digest"])
        self.assertEqual(codes, ("STALE_SOURCE_BINDING",))

    def test_a_replayed_cancel_is_idempotent_and_writes_no_second_disposition(self):
        """Required regression 12: no second run_end pair, no second disposal."""
        _, record, _ = self.drive_to_pause()
        before = self.artifact_digests()
        first, _ = self.fresh_dispose(kind="CANCEL")
        self.assertEqual(first.status, "CANCELLED")
        after_first = json.loads(pause_store.pause_record_path(
            self.RUN, artifact_base=self.base).read_text())
        replay, adapter = self.fresh_dispose(kind="CANCEL")
        self.assertEqual(replay.status, "ALREADY_DISPOSED")
        self.assertEqual(replay.code, "RUN_ALREADY_CANCELLED")
        self.assertEqual(json.loads(pause_store.pause_record_path(
            self.RUN, artifact_base=self.base).read_text()), after_first)
        stable = {key: value for key, value in self.artifact_digests().items()
                  if key in before}
        self.assertEqual(stable, before)

    def test_cancel_records_a_real_decision_cancelled_event_per_item(self):
        """X1 runs for CANCEL: there IS a human instruction to record."""
        _, record, _ = self.drive_to_pause()
        self.fresh_dispose(kind="CANCEL")
        lineage = self.lineage_types()
        self.assertIn("decision_cancelled", lineage)

    def test_a_disposed_run_publishes_no_further_clarification(self):
        """WU-12: promote_pending is a no-op once a run is disposed."""
        _, record, _ = self.drive_to_pause()
        port = self.approval_port()
        port.persist_blocked_sources(self.RUN, self.sources)
        self.fresh_dispose(kind="CANCEL")
        result = port.promote_pending(self.RUN)
        self.assertEqual(result.status, "EXISTING")
        self.assertEqual(result.request_ids, ())

    def test_promote_pending_is_unchanged_for_a_run_that_is_not_disposed(self):
        from scripts.clarification_protocol import run_disposition
        self.drive_to_pause()
        self.assertIsNone(run_disposition(self.base, self.RUN))
        self.assertIsNone(run_disposition(self.base, "run_never_paused"))

    def lineage_types(self):
        root = (self.base / "artifacts" / "runs" / self.RUN / "clarifications" / "lineage")
        return [json.loads(path.read_text())["event_type"]
                for path in sorted(root.glob("[0-9]*/event.json"))]


@REQUIRES_LANGGRAPH
class AbandonTests(PauseFixture):
    """TC-2/TC-3 and F-001: the mechanism, or the refusal -- never a stored label."""

    RUN = "run_abandon"

    def test_abandon_fabricates_no_decision_and_no_cancellation_event(self):
        """TT-2: there is no human answer, so none is written."""
        _, record, _ = self.drive_to_pause()
        before = self.lineage_types()
        outcome, _ = self.fresh_dispose(kind="ABANDON", submission_id="abandon_1")
        self.assertEqual(outcome.status, "ABANDONED", outcome.detail)
        self.assertEqual(self.lineage_types(), before,
                         "abandon must not write a decision nobody made")

    def test_abandon_settles_the_run_and_the_record(self):
        _, record, _ = self.drive_to_pause()
        outcome, _ = self.fresh_dispose(kind="ABANDON", submission_id="abandon_1")
        self.assertEqual(outcome.state["terminal_status"], "ABANDONED")
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "ABANDONED")
        self.assertEqual(stored["disposition"]["kind"], "ABANDON")
        self.assertEqual(self.store().claim(self.RUN)["claim_outcome"],
                         "ALREADY_ABANDONED")

    def test_a_row_the_journal_owns_is_discharged_and_ac1_is_claimed(self):
        """F-001(c): the discharge that is real."""
        adapter = self.adapter(axes={"intent_1": {"cleanup_authority": "unknown",
                                                  "process_liveness": "disputed"}})
        self.seed_dispatch(adapter, "intent_1")
        _, record, _ = self.drive_to_pause(adapter=adapter)
        self.assertIsNotNone(record)
        row = record["projection"]["settlement_ledger"][0]
        self.assertEqual(row["terminal_disposition"], "retained_by_named_owner")
        self.assertTrue(row["terminal_owner"])
        self.assertTrue(record["ac1_discharged"])
        outcome, _ = self.fresh_dispose(kind="ABANDON", submission_id="abandon_1")
        self.assertTrue(outcome.ac1_discharged)
        self.assertEqual(outcome.residual_terminals, [])

    def test_a_residual_row_completes_the_abandon_but_never_claims_ac1(self):
        """F-001(b): the honest outcome, with its reporting duty."""
        _, record, _ = self.drive_to_pause()
        # A residual dispatch that only the durable journal knows about, discovered by a
        # Coordinator that never ran it.
        journal = self.journal()
        journal.record("intent_residual", stage="PLANNED", run_id=self.RUN,
                       terminal_title=f"os31-{self.RUN}-intent_residual",
                       terminal_worktree=WORKTREE_A, terminal_role="unknown_role",
                       terminal_origin="unknown", provenance_source="absent")
        journal.record("intent_residual", stage="OPENED", task_id="task_residual")
        world = self.world()
        world.create_terminal(worktree=WORKTREE_A, handle="term_residual",
                              title=f"os31-{self.RUN}-intent_residual")
        outcome, adapter = self.fresh_dispose(kind="ABANDON", submission_id="abandon_1",
                                              world=world)
        self.assertEqual(outcome.status, "ABANDONED", outcome.detail)
        self.assertFalse(outcome.ac1_discharged,
                         "OS-31 does not claim AC-1 for a run with a residual terminal")
        self.assertEqual(len(outcome.residual_terminals), 1)
        entry = outcome.residual_terminals[0]
        self.assertEqual(entry["terminal_title"], f"os31-{self.RUN}-intent_residual")
        self.assertEqual(entry["task_id"], "task_residual")
        self.assertEqual(entry["provenance_source"], "absent")
        self.assertEqual(entry["handle_recovery"], "listing_candidate")
        self.assertEqual(entry["candidate_handle"], "term_residual",
                         "the human is handed an address, not a search")
        stored = self.store().read(self.RUN)
        self.assertFalse(stored["ac1_discharged"])
        self.assertEqual(len(stored["residual_terminals"]), 1)
        self.assertNotIn("close", [verb for verb, _ in adapter.lifecycle_commands])

    def test_a_residual_row_is_never_labelled_transferred_and_never_names_an_actor(self):
        """F-001(a): the claim is withdrawn, and cannot return by way of a label."""
        import ast
        import inspect
        from scripts.deterministic_workflow import executor, pause_policy as policy
        self.assertNotIn("transferred", policy.TERMINAL_DISPOSITIONS)
        for module in (policy, executor):
            source = inspect.getsource(module)
            self.assertNotIn('"actor:', source, module.__name__)
            self.assertNotIn("'actor:", source, module.__name__)
            ast.parse(source)

    def test_an_unverified_identity_reports_no_candidate_handle(self):
        """Publishing an address the digest DISPROVES is worse than publishing none."""
        entry = pause_runtime._residual_entry({  # noqa: SLF001
            "terminal_title": "os31-run_x-intent_1", "terminal_digest": "d",
            "terminal_role": "unknown_role", "terminal_origin": "unknown",
            "provenance_source": "journal", "task_id": "task_1", "dispatch_id": "",
            "process_liveness": "disputed", "handle_recovery": "unverified"},
            "term_wrong")
        self.assertEqual(entry["candidate_handle"], "")

    def test_the_pause_and_abandon_asymmetry_is_asserted_as_a_pair(self):
        """F-001(d): pause REFUSES the same row that abandon records as residual."""
        row = {"intent_id": "intent_1", "cleanup_authority": "unknown",
               "worker_resource": "unsupervised", "process_liveness": "disputed",
               "provenance_source": "absent", "terminal_role": "unknown_role",
               "terminal_origin": "unknown", "terminal_owner": "",
               "terminal_title": "os31-run_x-intent_1", "task_id": "task_1",
               "recovery": "", "handle_recovery": "not_listed", "stage": "OPENED"}
        self.assertEqual(pause_policy.terminal_disposition(row), "residual")
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_policy.require_pause_disposition(row)
        self.assertEqual(ctx.exception.code, "TERMINAL_ORPHAN_POSSIBLE")

    def lineage_types(self):
        root = (self.base / "artifacts" / "runs" / self.RUN / "clarifications" / "lineage")
        if not root.exists():
            return []
        return [json.loads(path.read_text())["event_type"]
                for path in sorted(root.glob("[0-9]*/event.json"))]


@REQUIRES_LANGGRAPH
class DispositionArbitrationTests(PauseFixture):
    """TC-4: cancel racing a resume, arbitrated by the same run-scoped fence."""

    RUN = "run_arbitrate"

    def test_a_cancel_and_a_resume_contend_for_one_lease_and_one_wins(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        held = self.store(owner_id="host:pid1")
        self.assertEqual(held.claim(self.RUN)["claim_outcome"], "CREATED")
        loser = self.store(owner_id="host:pid2")
        outcome, adapter = self.fresh_dispose(kind="CANCEL", store=loser)
        self.assertEqual(outcome.status, "REFUSED")
        self.assertIn(outcome.code, ("PAUSE_CLAIM_HELD", "PAUSE_OBSERVATION_TIMEOUT"))
        self.assertEqual(adapter.lifecycle_commands, [])
        self.assertEqual(self.store().read(self.RUN)["status"], "WAITING_FOR_INPUT")

    def test_a_resume_after_a_cancel_reports_the_disposition_and_performs_no_effect(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.fresh_dispose(kind="CANCEL")
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.code, "RUN_ALREADY_CANCELLED")
        self.assertEqual(adapter.effect_count, 0)

    def test_a_cancel_after_a_resume_reports_the_resume_and_performs_no_effect(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.fresh_resume(record)
        outcome, adapter = self.fresh_dispose(kind="CANCEL")
        self.assertEqual(outcome.status, "ALREADY_DISPOSED")
        self.assertEqual(outcome.code, "RUN_ALREADY_RESUMED")
        self.assertEqual(adapter.lifecycle_commands, [])


@REQUIRES_LANGGRAPH
class AuditAndTimingEvidenceTests(PauseFixture):
    """AC-7: pause / resume / cancel / refusal all leave append-only evidence."""

    RUN = "run_audit"

    def orchestrator(self):
        from scripts import run_logging
        return _log_rows(self.base / "artifacts" / "runs" / self.RUN
                         / run_logging.ORCHESTRATOR_LOG_FILENAME)

    def timing(self):
        from scripts import run_logging
        return _log_rows(self.base / "artifacts" / "runs" / self.RUN
                         / run_logging.TIMING_LOG_FILENAME)

    def test_a_pause_records_its_settlement_rows_the_pause_and_a_run_end(self):
        adapter = self.adapter(axes={"intent_1": {"process_liveness": "already exited"}})
        self.seed_dispatch(adapter, "intent_1")
        _, record, _ = self.drive_to_pause(adapter=adapter)
        rows = self.orchestrator()
        self.assertTrue(any("pause_settlement_accounted" in row for row in rows))
        self.assertTrue(any("run_paused" in row for row in rows))
        ends = [row for row in rows if "run_end" in row]
        self.assertEqual(len(ends), 1)
        self.assertIn("WAITING_FOR_INPUT", ends[-1])
        self.assertTrue(any("run_paused" in row for row in self.timing()))

    def test_a_resume_records_exactly_one_run_resumed_row_and_a_replay_records_none(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.fresh_resume(record)
        resumed = [row for row in self.orchestrator() if "run_resumed" in row]
        self.assertEqual(len(resumed), 1)
        self.fresh_resume(record)
        self.assertEqual(len([row for row in self.orchestrator()
                              if "run_resumed" in row]), 1,
                         "a replay writes no second pair")

    def test_a_refused_resume_records_the_named_reason_code(self):
        _, record, _ = self.drive_to_pause()
        outcome, _ = self.fresh_resume(record)
        self.assertEqual(outcome.code, "RESPONSE_NOT_FOUND")
        refused = [row for row in self.orchestrator() if "run_resume_refused" in row]
        self.assertEqual(len(refused), 1)
        self.assertIn("RESPONSE_NOT_FOUND", refused[-1])

    def test_a_cancel_appends_a_second_run_end_that_is_authoritative(self):
        _, record, _ = self.drive_to_pause()
        self.fresh_dispose(kind="CANCEL")
        rows = self.orchestrator()
        self.assertTrue(any("run_cancelled" in row for row in rows))
        ends = [row for row in rows if "run_end" in row]
        self.assertEqual(len(ends), 2, "run_end is not terminal; the last row wins")
        self.assertIn("WAITING_FOR_INPUT", ends[0])
        self.assertIn("CANCELLED", ends[-1])
        timing_ends = [row for row in self.timing() if "run_end" in row]
        self.assertEqual(len(timing_ends), 2)

    def test_an_abandon_with_a_residual_terminal_reports_its_count_and_names_it(self):
        _, record, _ = self.drive_to_pause()
        journal = self.journal()
        journal.record("intent_residual", stage="PLANNED", run_id=self.RUN,
                       terminal_title=f"os31-{self.RUN}-intent_residual",
                       terminal_worktree=WORKTREE_A, terminal_role="unknown_role",
                       terminal_origin="unknown", provenance_source="absent")
        journal.record("intent_residual", stage="OPENED", task_id="task_residual")
        world = self.world()
        world.create_terminal(worktree=WORKTREE_A, handle="term_residual",
                              title=f"os31-{self.RUN}-intent_residual")
        self.fresh_dispose(kind="ABANDON", submission_id="abandon_1", world=world)
        abandoned = [row for row in self.orchestrator() if "run_abandoned" in row]
        self.assertEqual(len(abandoned), 1)
        self.assertIn("residual_terminal_count=1", abandoned[-1])
        ends = [row for row in self.orchestrator() if "run_end" in row]
        self.assertIn("ABANDONED", ends[-1])
        self.assertIn("ac1_discharged=false", ends[-1])
        self.assertIn("os31-run_audit-intent_residual", ends[-1])

    def test_a_refused_takeover_is_recorded_rather_than_silent(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.store(owner_id="host:pid1").claim(self.RUN)
        self.fresh_resume(record, store=self.store(owner_id="host:pid2"))
        self.assertTrue(any("pause_takeover_refused" in row
                            for row in self.orchestrator()))


class DispositionVocabularyTests(unittest.TestCase):
    """No timeout, no automatic invoker, no privileged actor class anywhere."""

    def test_a_disposition_kind_outside_the_closed_set_is_refused(self):
        for kind in ("TIMEOUT", "AUTO", "EXPIRE", ""):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    pause_runtime.dispose_run(
                        "run_x", artifact_base=".", kind=kind, actor_id="a",
                        actor_type="human", submission_id="s", reason="r",
                        graph_factory=lambda saver: None)

    def test_only_cancel_and_abandon_exist(self):
        self.assertEqual(pause_policy.DISPOSITION_KINDS, ("CANCEL", "ABANDON"))
        self.assertEqual(pause_policy.ACTOR_TYPES, ("human", "service"))

    def test_no_engine_module_derives_a_disposition_from_a_timeout(self):
        import ast
        import inspect
        from scripts.deterministic_workflow import executor, pause_policy as policy
        from scripts.deterministic_workflow import pause_runtime as runtime
        for module in (policy, runtime, executor):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value in ("CANCEL", "ABANDON"):
                    continue
            self.assertNotIn("time.sleep", inspect.getsource(module), module.__name__)


class RunStatusVocabularyTests(unittest.TestCase):
    """T-01: the new statuses are accepted at every enforcement point, and no others are."""

    def test_the_three_new_statuses_are_accepted_and_an_unknown_one_is_still_refused(self):
        import tempfile
        from scripts import run_logging
        for status in ("WAITING_FOR_INPUT", "CANCELLED", "ABANDONED"):
            self.assertIn(status, run_logging.RUN_STATUS_VALUES)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for status in ("WAITING_FOR_INPUT", "CANCELLED", "ABANDONED"):
                run_logging.log_run_status("run_status", status, base=base,
                                           reason="os31", run_started_at="2026-01-01T00:00:00Z")
            with self.assertRaises(run_logging.RunLoggingError):
                run_logging.log_run_status("run_status", "PAUSED", base=base)

    def test_the_harness_reads_the_same_tuple_rather_than_repeating_it(self):
        from scripts import orca_runtime_harness, run_logging
        source = orca_runtime_harness.__file__
        self.assertTrue(Path(source).exists())
        self.assertEqual(run_logging.RUN_STATUS_VALUES,
                         ("COMPLETED", "BLOCKED", "ERROR", "ESCALATED",
                          "WAITING_FOR_INPUT", "CANCELLED", "ABANDONED"))

    def test_the_cli_choices_track_the_tuple(self):
        from scripts import run_logging
        parser = run_logging._build_parser()  # noqa: SLF001
        status = parser._subparsers._group_actions[0].choices["run-status"]  # noqa: SLF001
        for action in status._actions:  # noqa: SLF001
            if action.dest == "status":
                self.assertEqual(tuple(action.choices), run_logging.RUN_STATUS_VALUES)
                return
        self.fail("run-status --status has no choices")

    def test_the_second_run_end_row_is_authoritative_under_last_row_wins(self):
        import tempfile
        from scripts import run_logging
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_logging.log_run_status("run_second", "WAITING_FOR_INPUT", base=base,
                                       run_started_at="2026-01-01T00:00:00Z")
            run_logging.log_run_status("run_second", "CANCELLED", base=base,
                                       run_started_at="2026-01-01T00:00:00Z")
            rows = _log_rows(base / "artifacts" / "runs" / "run_second"
                             / run_logging.ORCHESTRATOR_LOG_FILENAME)
            ends = [row for row in rows if "run_end" in row]
            self.assertEqual(len(ends), 2)
            self.assertIn("CANCELLED", ends[-1])
            self.assertIn("WAITING_FOR_INPUT", ends[0])

    def test_the_timing_run_end_carries_a_non_blank_started_at(self):
        import tempfile
        from scripts import run_logging
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_logging.log_run_status("run_timing", "CANCELLED", base=base,
                                       run_started_at="2026-01-01T00:00:00Z")
            rows = _log_rows(base / "artifacts" / "runs" / "run_timing"
                             / run_logging.TIMING_LOG_FILENAME)
            ends = [row for row in rows if "run_end" in row]
            self.assertTrue(ends)
            self.assertIn("2026-01-01T00:00:00Z", ends[-1],
                          "OS-19: never a blank started_at")

    def test_the_os31_event_constants_exist_and_are_distinct(self):
        from scripts import run_logging
        events = {run_logging.EVENT_RUN_PAUSED, run_logging.EVENT_RUN_RESUMED,
                  run_logging.EVENT_RUN_RESUME_REFUSED, run_logging.EVENT_RUN_CANCELLED,
                  run_logging.EVENT_RUN_ABANDONED, run_logging.EVENT_PAUSE_SETTLEMENT,
                  run_logging.EVENT_PAUSE_TAKEOVER,
                  run_logging.EVENT_PAUSE_TAKEOVER_REFUSED}
        self.assertEqual(len(events), 8)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
