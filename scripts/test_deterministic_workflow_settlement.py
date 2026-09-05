"""OS-31 settlement regressions: the durable journal, orphan recovery and ownership leaks.

The oracle for a terminal ownership leak is the **exit invariant**, not the record: a row
that merely *records* an ambiguity has not discharged it, and neither has a row that
*labels* one.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.deterministic_workflow import pause_policy, pause_store
from scripts.test_deterministic_workflow_pause_fixture import (REQUIRES_LANGGRAPH,
                                                               REVIEW_PASS, WORKER,
                                                               WORKTREE_A, WORKTREE_B,
                                                               PauseFixture)


@REQUIRES_LANGGRAPH
class DurableProvenanceTests(PauseFixture):
    """F-002: every journal write lands BEFORE the effect it describes."""

    RUN = "run_journal"

    def test_the_planned_row_carries_a_stable_worktree_identity_never_an_alias(self):
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        row = adapter.settlement_journal.row("intent_1")
        self.assertEqual(row["terminal_worktree"], WORKTREE_A)
        self.assertTrue(row["terminal_worktree"].startswith("id:"))
        self.assertIn("::", row["terminal_worktree"])
        self.assertNotIn(row["terminal_worktree"], ("current", "active"))

    def test_every_journal_row_written_by_the_adapter_names_a_stable_scope(self):
        """The property, over every row any suite in this module writes."""
        adapter = self.adapter()
        for intent_id in ("intent_1", "intent_2", "intent_3"):
            self.seed_dispatch(adapter, intent_id)
        for row in adapter.settlement_journal.rows().values():
            self.assertRegex(row["terminal_worktree"], r"^id:[^:]+::/")

    def test_provenance_is_journal_authoritative_and_the_runtime_is_observation_only(self):
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        row = adapter.settlement_journal.row("intent_1")
        self.assertEqual(row["provenance_source"], "journal")
        self.assertEqual(row["terminal_role"], "phase_worker")
        self.assertEqual(row["terminal_origin"], "self_created")
        self.assertTrue(row["terminal_owner"])

    def test_the_plaintext_handle_is_never_persisted_only_its_digest(self):
        adapter = self.adapter()
        handle = self.seed_dispatch(adapter, "intent_1")
        row = adapter.settlement_journal.row("intent_1")
        self.assertEqual(row["terminal_digest"], pause_policy.terminal_digest(handle))
        journal_text = pause_store.settlement_journal_path(
            self.RUN, artifact_base=self.base).read_text()
        self.assertNotIn(handle, journal_text)
        self.assertIn(row["terminal_digest"], journal_text)

    def test_the_handle_appears_in_no_durable_store_after_a_committed_pause(self):
        adapter = self.adapter()
        handle = self.seed_dispatch(adapter, "intent_1")
        adapter.axes = {"intent_1": {"process_liveness": "already exited"}}
        _, record, _ = self.drive_to_pause(adapter=adapter)
        self.assertIsNotNone(record)
        for path in (pause_store.settlement_journal_path(self.RUN,
                                                         artifact_base=self.base),
                     pause_store.pause_record_path(self.RUN, artifact_base=self.base),
                     self.checkpoint_path, self.root / "ledger.json"):
            if not path.exists():
                continue
            with self.subTest(path=path.name):
                self.assertNotIn(handle, path.read_text())


@REQUIRES_LANGGRAPH
class HandleRecoveryTests(PauseFixture):
    """SS4.2.1a end to end: enumerate, narrow by normalised title, PROVE with the digest."""

    RUN = "run_recover"

    def test_a_fresh_process_recovers_the_handle_from_the_listing_alone(self):
        creator = self.adapter()
        handle = self.seed_dispatch(creator, "intent_1")
        del creator
        fresh = self.adapter()
        found = fresh.recover_handle("intent_1")
        self.assertEqual(found, {"handle": handle,
                                 "handle_recovery": "listing_verified"})

    def test_a_decorated_title_still_matches_and_a_foreign_run_does_not(self):
        adapter = self.adapter()
        handle = self.seed_dispatch(
            adapter, "intent_1", listed_title=f"✳ os31-{self.RUN}-intent_1")
        adapter.external_world.create_terminal(worktree=WORKTREE_A, handle="term_foreign",
                                               title="os31-run_OTHER-intent_1")
        adapter.external_world.create_terminal(worktree=WORKTREE_A, handle="term_shell",
                                               title="orca-skills")
        found = self.adapter().recover_handle("intent_1")
        self.assertEqual(found["handle"], handle)
        self.assertEqual(found["handle_recovery"], "listing_verified")

    def test_the_recovery_is_the_digest_not_the_listing(self):
        """Mutate the digest by one byte and the recovery must refuse."""
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        row = adapter.settlement_journal.row("intent_1")
        adapter.settlement_journal.record(
            "intent_1", stage="INTENDED",
            terminal_digest=pause_policy.terminal_digest("a-different-handle"))
        found = self.adapter().recover_handle("intent_1")
        self.assertIsNone(found["handle"])
        self.assertEqual(found["handle_recovery"], "unverified")

    def test_a_listing_under_a_different_worktree_finds_nothing(self):
        """The recorded selector is an identity, not a pronoun: scope is load-bearing."""
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        world = adapter.external_world
        world.register_worktree(WORKTREE_B)
        self.assertEqual(world.list_terminals(WORKTREE_B), [])
        self.assertEqual(len(world.list_terminals(WORKTREE_A)), 1)

    def test_an_unresolvable_scope_is_unknown_never_empty(self):
        adapter = self.adapter(worktree="id:repoGONE::/wt/gone")
        self.seed_dispatch(adapter, "intent_1", worktree="id:repoGONE::/wt/gone",
                           handle="term_x")
        adapter.external_world.close_terminal(worktree="id:repoGONE::/wt/gone",
                                              handle="term_x")
        # The selector was registered by create_terminal, so drop it to model
        # `worktree show` -> ok:false / selector_not_found.
        world = adapter.external_world
        document = world._read()  # noqa: SLF001 - modelling the runtime's own refusal
        document["__worktrees__"].pop("id:repoGONE::/wt/gone")
        world._write(document)  # noqa: SLF001
        found = self.adapter(worktree="id:repoGONE::/wt/gone").recover_handle("intent_1")
        self.assertEqual(found["handle_recovery"], "scope_unresolved")

    def test_a_w_c_row_is_addressable_but_unproven_and_therefore_refused(self):
        """The limitation, asserted as behaviour: enumeration works, the verifier does not."""
        adapter = self.adapter()
        adapter.settlement_journal.record(
            "intent_wc", stage="PLANNED", run_id=self.RUN,
            terminal_title=adapter.terminal_title("intent_wc"),
            terminal_worktree=WORKTREE_A, terminal_role="phase_worker",
            terminal_origin="self_created", provenance_source="journal")
        adapter.settlement_journal.record("intent_wc", stage="OPENED",
                                          task_id="task_wc")
        adapter.external_world.create_terminal(
            worktree=WORKTREE_A, handle="term_wc",
            title=adapter.terminal_title("intent_wc"))
        found = self.adapter().recover_handle("intent_wc")
        self.assertEqual(found["handle_recovery"], "listing_candidate")
        self.assertIsNone(found["handle"], "a label is not proof")
        self.assertEqual(found["candidate_handle"], "term_wc")

    def test_an_unreadable_listing_refuses_rather_than_enumerating_an_empty_set(self):
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        adapter.listing_readable = False
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            adapter.recover_handle("intent_1")
        self.assertEqual(ctx.exception.code, "DISPATCH_UNACCOUNTED")


@REQUIRES_LANGGRAPH
class TerminalOwnershipTests(PauseFixture):
    """Required regression 10: the oracle is the exit invariant, not the record."""

    RUN = "run_ownership"

    def pause_with(self, axes, *, stage="INTENDED"):
        adapter = self.adapter(axes={"intent_1": axes})
        self.seed_dispatch(adapter, "intent_1", stage=stage)
        return self.drive_to_pause(adapter=adapter)

    def test_unknown_authority_with_no_nameable_owner_refuses_the_pause(self):
        final, record, adapter = self.pause_with({
            "cleanup_authority": "unknown", "process_liveness": "disputed",
            "terminal_role": "unknown_role", "terminal_origin": "unknown",
            "terminal_owner": "", "provenance_source": "absent"})
        self.assertEqual(final["run_lifecycle"], "SETTLED")
        self.assertEqual(final["terminal_status"], "BLOCKED")
        self.assertIn(final["terminal_reason"]["code"],
                      ("TERMINAL_OWNERSHIP_UNKNOWN", "TERMINAL_ORPHAN_POSSIBLE"))
        self.assertIsNone(record, "no pause record may be written")
        self.assertEqual(adapter.lifecycle_commands, [],
                         "nothing may be released on a refused pause")

    def test_a_named_owner_makes_disputed_liveness_admissible_and_reported(self):
        final, record, _ = self.pause_with({
            "cleanup_authority": "unknown", "process_liveness": "disputed"})
        self.assertEqual(final["run_lifecycle"], "WAITING_FOR_INPUT")
        row = final["pause_binding"]["settlement_ledger"][0]
        self.assertEqual(row["terminal_disposition"], "retained_by_named_owner")
        self.assertTrue(row["terminal_owner"])
        self.assertEqual(row["process_liveness"], "disputed",
                         "the dispute is recorded as reporting evidence, not resolved")
        self.assertTrue(record["ac1_discharged"])

    def test_a_proven_exit_is_admissible_as_exited(self):
        final, record, _ = self.pause_with({"process_liveness": "already exited"})
        row = final["pause_binding"]["settlement_ledger"][0]
        self.assertEqual(row["terminal_disposition"], "exited")
        self.assertEqual(row["terminal_owner"], "",
                         "nobody owns a terminal that is proven gone")

    def test_a_release_receipt_that_proves_a_termination_is_a_release(self):
        final, record, adapter = self.pause_with({
            "cleanup_authority": "authorized", "worker_resource": "release",
            "release_process_action": "killed"})
        row = final["pause_binding"]["settlement_ledger"][0]
        self.assertEqual(row["terminal_disposition"], "released")
        self.assertIn(("worker-release", "intent_1"), adapter.lifecycle_commands)

    def test_the_observed_live_receipt_is_not_a_release(self):
        """D-6/R8-iii: retained/external_terminal/none was 12/12 of the live observations."""
        final, record, _ = self.pause_with({
            "cleanup_authority": "authorized", "worker_resource": "release",
            "release_process_action": "none"})
        row = final["pause_binding"]["settlement_ledger"][0]
        self.assertNotEqual(row["terminal_disposition"], "released")
        self.assertEqual(row["terminal_disposition"], "retained_by_named_owner")

    def test_a_never_close_role_passes_through_a_named_owner_and_is_never_closed(self):
        final, record, adapter = self.pause_with({
            "cleanup_authority": "not_authorized", "terminal_role": "active_worker"})
        row = final["pause_binding"]["settlement_ledger"][0]
        self.assertEqual(row["terminal_disposition"], "retained_by_named_owner")
        self.assertEqual(adapter.lifecycle_commands, [])

    def test_a_w_c_row_refuses_the_pause_as_a_possible_orphan(self):
        final, record, adapter = self.pause_with({}, stage="OPENED")
        self.assertEqual(final["terminal_reason"]["code"], "TERMINAL_ORPHAN_POSSIBLE")
        self.assertIsNone(record)
        self.assertEqual(adapter.lifecycle_commands, [])

    def test_a_contradicted_identity_refuses_the_pause_and_closes_nothing(self):
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        adapter.settlement_journal.record(
            "intent_1", stage="INTENDED",
            terminal_digest=pause_policy.terminal_digest("a-different-handle"))
        final, record, _ = self.drive_to_pause(adapter=adapter)
        self.assertEqual(final["terminal_reason"]["code"], "TERMINAL_IDENTITY_UNVERIFIED")
        self.assertIsNone(record)
        self.assertEqual(adapter.lifecycle_commands, [])

    def test_an_unsettled_dispatch_is_recovered_and_recorded_recovered_never_settled(self):
        """Required regression 9: abandon -> release, worker_done 0, no role promotion."""
        final, record, adapter = self.pause_with({"settlement": "not_settled"})
        self.assertEqual(final["run_lifecycle"], "WAITING_FOR_INPUT")
        row = final["pause_binding"]["settlement_ledger"][0]
        self.assertEqual(row["settlement"], "recovered")
        self.assertTrue(row["recovery"].startswith("abandon:"))
        self.assertIn(("worker-abandon", "intent_1"), adapter.lifecycle_commands)
        self.assertEqual(row["terminal_role"], "phase_worker",
                         "a recovery never promotes a role")

    def test_the_coordinators_own_terminal_never_appears_in_open_dispatches(self):
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        self.assertEqual(adapter.open_dispatches(), ("intent_1",))

    def test_a_finished_row_is_never_re_enumerated(self):
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        adapter.settlement_journal.record("intent_1", stage="DISPOSED",
                                          disposed_at="t9")
        self.assertEqual(adapter.open_dispatches(), ())


@REQUIRES_LANGGRAPH
class ArtifactImmutabilityTests(PauseFixture):
    """Required regression 11: a replay rewrites nothing and duplicates nothing."""

    RUN = "run_artifact"

    def test_a_replayed_pause_publishes_a_byte_identical_request(self):
        first, record, _ = self.drive_to_pause()
        before = self.artifact_digests()
        self.assertTrue(before)
        # A second pause of the same run over the same sources must be content-idempotent.
        adapter = self.adapter()
        graph = self.graph(adapter, self.saver())
        graph.invoke(None, {"configurable": {"thread_id": "t", "checkpoint_ns": ""},
                            "recursion_limit": 200})
        self.assertEqual(self.artifact_digests(), before)

    def test_exactly_one_request_directory_exists_per_published_bundle(self):
        self.drive_to_pause()
        self.assertEqual(len(self.requests()), 1)


class JournalOrderingTests(unittest.TestCase):
    """Every journal write precedes the effect it covers -- asserted on the stage ladder."""

    def test_the_stage_order_is_the_effect_order(self):
        self.assertEqual(pause_store.JOURNAL_STAGES,
                         ("PLANNED", "OPENED", "INTENDED", "ACCOUNTED", "DISPOSED"))

    def test_planned_carries_everything_the_effect_could_destroy(self):
        row = pause_store.new_journal_row(intent_id="intent_1", run_id="run_x",
                                          stage="PLANNED", terminal_role="phase_worker",
                                          terminal_origin="self_created",
                                          terminal_title="os31-run_x-intent_1",
                                          terminal_worktree="id:repo::/wt")
        for key in ("terminal_role", "terminal_origin", "terminal_title",
                    "terminal_worktree"):
            self.assertTrue(row[key], key)
        self.assertEqual(row["terminal_digest"], "",
                         "the digest cannot exist before the terminal does")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
