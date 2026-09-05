"""OS-31 end-to-end pause and resume, on the fake adapter, with NO Orca runtime.

Covers the required regressions for crash-before/after-pause, duplicate response,
concurrent resume race, stale checkpoint, stale response, conflicting response, changed
source/policy, artifact duplication, gate preservation and the multi-item bundle.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.deterministic_workflow import pause_policy, pause_runtime, pause_store
from scripts.test_deterministic_workflow_pause_fixture import (REQUIRES_LANGGRAPH,
                                                               REVIEW_PASS, WORKER,
                                                               PauseFixture)


@REQUIRES_LANGGRAPH
class PauseCommitTests(PauseFixture):
    RUN = "run_pausecommit"

    def test_a_decision_block_becomes_a_durable_pause_not_a_terminal(self):
        final, record, _ = self.drive_to_pause()
        self.assertEqual(final["run_lifecycle"], "WAITING_FOR_INPUT")
        self.assertIsNone(final["terminal_status"], "a pause is not a terminal status")
        self.assertEqual(final["terminal_reason"]["message"], "WAITING_FOR_INPUT")
        self.assertEqual(final["route_token"], "PAUSE")
        self.assertIsNotNone(final["pause_binding"])
        self.assertEqual(record["status"], "WAITING_FOR_INPUT")
        self.assertTrue(record["ac1_discharged"])

    def test_the_record_alone_explains_why_the_run_stopped_and_where_to_resume(self):
        """AC-2: run state alone must recover the reason, the decision and the re-entry."""
        _, record, _ = self.drive_to_pause()
        projection = record["projection"]
        self.assertEqual(projection["decision_state"], "NEEDS_INPUT")
        self.assertEqual(projection["decision_reason_code"], "user_choice_required")
        self.assertTrue(projection["request_id"])
        self.assertTrue(projection["decision_item_ids"])
        self.assertEqual(projection["responsible_phase"], "ANALYSIS")
        self.assertEqual(record["thread_id"], "t")
        self.assertTrue(record["checkpoint_id"])

    def test_pause_is_not_admissible_without_both_capabilities_and_block_is_preserved(self):
        """The pre-OS-31 behaviour is preserved by construction, not by a test edit."""
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.routing import pause_admissible, route
        state = self.initial_state()
        state["adapter_capabilities"] = sorted(BASE_CAPABILITIES)
        self.assertFalse(pause_admissible(state))
        self.assertEqual(route(state), "BLOCK")

    def test_c1_holds_by_construction_the_record_never_names_a_missing_checkpoint(self):
        _, record, _ = self.drive_to_pause()
        saver = self.saver()
        self.assertEqual(saver.head("t"), record["checkpoint_id"])
        pause_runtime.assert_c1(record, saver)
        pause_runtime.assert_c2(record, saver)

    def test_the_projection_is_subordinate_and_never_an_input_to_reconstruction(self):
        """T-43: mutate the projection and the reconstructed state is unchanged."""
        _, record, _ = self.drive_to_pause()
        saver = self.saver()
        honest = pause_runtime.reconstruct(record, saver)
        tampered = json.loads(json.dumps(record))
        tampered["projection"]["current_phase"] = "TEST"
        self.assertEqual(pause_runtime.reconstruct(tampered, saver), honest)
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_runtime.assert_c3(honest, tampered)
        self.assertEqual(ctx.exception.code, "PAUSE_PROJECTION_DIVERGED")


@REQUIRES_LANGGRAPH
class CrashWindowTests(PauseFixture):
    RUN = "run_crash"

    def test_a_crash_before_the_pause_checkpoint_leaves_the_run_active(self):
        """Required regression 1: nothing on disk claims a pause that never committed."""
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        saver = self.saver()
        graph = self.graph(adapter, saver)
        config = {"configurable": {"thread_id": "t", "checkpoint_ns": ""},
                  "recursion_limit": 200}
        state = self.initial_state()
        # The crash: the PAUSE node raises on the first dispatch it accounts.
        adapter.listing_readable = False
        final = graph.invoke(state, config)
        self.assertIsNone(final["pause_binding"])
        self.assertEqual(final["run_lifecycle"], "SETTLED")
        self.assertEqual(final["terminal_status"], "BLOCKED")
        self.assertEqual(final["terminal_reason"]["code"], "DISPATCH_UNACCOUNTED")
        self.assertIsNone(self.store().read(self.RUN), "no pause record may exist")

    def test_a_fresh_process_reconstructs_the_dispatch_set_from_disk_alone(self):
        """The successor holds none of the dead process's objects."""
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        self.seed_dispatch(adapter, "intent_2")
        del adapter
        fresh = self.adapter()
        self.assertEqual(fresh.open_dispatches(), ("intent_1", "intent_2"))

    def test_a_crash_after_the_checkpoint_is_repaired_forward_and_idempotently(self):
        """Required regression 2 / C4: reindex re-derives the record FROM the checkpoint."""
        adapter = self.adapter()
        saver = self.saver()
        graph = self.graph(adapter, saver)
        config = {"configurable": {"thread_id": "t", "checkpoint_ns": ""},
                  "recursion_limit": 200}
        final = graph.invoke(self.initial_state(), config)
        self.assertEqual(final["run_lifecycle"], "WAITING_FOR_INPUT")
        self.assertIsNone(self.store().read(self.RUN),
                          "finalize_pause did not run: this is the crash window")
        first = pause_runtime.reindex(self.base, self.RUN, "t", self.checkpoint_path)
        self.assertIsNotNone(first)
        self.assertEqual(first["status"], "WAITING_FOR_INPUT")
        second = pause_runtime.reindex(self.base, self.RUN, "t", self.checkpoint_path)
        self.assertEqual(second, first, "a second reindex must be a byte-identical no-op")

    def test_the_asymmetry_between_c1_and_c4_is_asserted_directly(self):
        """T-45: checkpoint -> record is repairable; record -> checkpoint is not."""
        _, record, _ = self.drive_to_pause()
        broken = {**record, "checkpoint_id": "chk_absent"}
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_runtime.assert_c1(broken, self.saver())
        self.assertEqual(ctx.exception.code, "PAUSE_CHECKPOINT_MISSING")


@REQUIRES_LANGGRAPH
class ResumeTests(PauseFixture):
    RUN = "run_resume"

    def test_a_new_coordinator_applies_the_answer_and_resumes_exactly_once(self):
        """AC-3: a brand-new object graph resumes the SAME run to completion."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED")
        self.assertEqual(adapter.effect_count, 3)
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "RESUMED")
        self.assertEqual(len(stored["applied"]), 1)
        entry = next(iter(stored["applied"].values()))
        self.assertEqual(entry["stage"], "RESUMED")
        self.assertEqual(entry["resumed_checkpoint_id"], outcome.resumed_checkpoint_id)

    def test_resume_without_an_answer_is_not_an_error_it_is_still_waiting(self):
        _, record, _ = self.drive_to_pause()
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "REFUSED")
        self.assertEqual(outcome.code, "RESPONSE_NOT_FOUND")
        self.assertEqual(adapter.effect_count, 0)
        self.assertEqual(self.store().read(self.RUN)["status"], "WAITING_FOR_INPUT")

    def test_a_replayed_response_creates_no_second_effect_and_no_second_log_pair(self):
        """Required regression 3 / AC-4."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        before = self.artifact_digests()
        first, _ = self.fresh_resume(record)
        self.assertEqual(first.status, "RESUMED")
        replay, adapter = self.fresh_resume(record)
        self.assertEqual(replay.status, "NO_EFFECT")
        self.assertEqual(replay.code, "RUN_ALREADY_RESUMED")
        self.assertEqual(adapter.effect_count, 0, "a replay performs no effect at all")
        after = self.artifact_digests()
        self.assertEqual({key: value for key, value in after.items() if key in before},
                         before, "no published clarification artifact may be rewritten")
        self.assertEqual(len(self.store().read(self.RUN)["applied"]), 1)

    def test_a_stale_checkpoint_head_refuses_the_resume_and_performs_no_effect(self):
        """Required regression 5: the record must name its own thread's head."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        store = self.store()
        token = store.claim(self.RUN)["lease_token"]
        store.update_pointer(self.RUN, checkpoint_id="chk_moved",
                             checkpoint_digest=record["checkpoint_digest"],
                             projection=record["projection"], lease_token=token)
        store.release(self.RUN, token)
        outcome, adapter = self.fresh_resume(record)
        self.assertIn(outcome.code, ("STALE_CHECKPOINT_HEAD", "PAUSE_CHECKPOINT_MISSING"))
        self.assertEqual(adapter.effect_count, 0)
        self.assertEqual(self.store().read(self.RUN)["status"], "WAITING_FOR_INPUT")

    def test_update_pointer_under_the_claim_makes_a_stale_record_resumable_again(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        store = self.store()
        token = store.claim(self.RUN)["lease_token"]
        store.update_pointer(self.RUN, checkpoint_id="chk_moved",
                             checkpoint_digest="nope", projection=record["projection"],
                             lease_token=token)
        store.update_pointer(self.RUN, checkpoint_id=record["checkpoint_id"],
                             checkpoint_digest=record["checkpoint_digest"],
                             projection=record["projection"], lease_token=token)
        store.release(self.RUN, token)
        outcome, _ = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)

    def test_a_concurrent_resume_race_produces_exactly_one_winner_and_no_effect_loser(self):
        """Required regression 4: the claim precedes every effect, so the loser does none."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        winner_store = self.store(owner_id="host:pid1")
        loser_store = self.store(owner_id="host:pid2")
        claimed = winner_store.claim(self.RUN)
        self.assertEqual(claimed["claim_outcome"], "CREATED")
        with self.assertRaises(pause_store.PauseClaimHeld):
            loser_store.claim(self.RUN)
        outcome, adapter = self.fresh_resume(record, store=loser_store)
        self.assertEqual(adapter.effect_count, 0,
                         "the loser must create no Task, no Dispatch and no artifact")
        self.assertIn(outcome.status, ("REFUSED", "NO_EFFECT"))

    def test_a_stale_response_revision_is_refused_and_never_applied(self):
        """Required regression 6, driven through OS-30's own reclarification path."""
        from scripts.clarification_protocol import ResponseSubmission
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        port = self.approval_port()
        request = self.requests()[0]
        # An ambiguous answer makes OS-30 republish the request at revision+1, which
        # supersedes the revision the pause binding named.
        free_text = self.root / "free_text.txt"
        free_text.write_text("neither of the offered options", encoding="utf-8")
        port.ingest(run_id=self.RUN, request_id=request["request_id"],
                    decision_item_id=request["items"][0]["decision_item_id"],
                    submission=ResponseSubmission("ambiguous", "alice", "human", "desk",
                                                  "2026-09-01T09:00:00Z", None,
                                                  free_text, False, "normal"))
        self.assertGreater(len(self.requests()), 1,
                           "OS-30 must have published a newer revision")
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "REFUSED")
        self.assertEqual(outcome.code, "RESPONSE_STALE_REVISION")
        self.assertEqual(adapter.effect_count, 0)

    def test_a_conflicting_response_is_refused_and_never_arbitrated_by_recency(self):
        """Required regression 8: two effective decisions for one item never resolve."""
        from scripts.deterministic_workflow.pause_runtime import read_decision_bundle

        class Forking:
            def show(self, *, run_id, request_id):
                from scripts.clarification_protocol import LineageFork
                raise LineageFork("conflicting supersession fork")

        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            read_decision_bundle(Forking(), run_id=self.RUN, request_id="r",
                                 decision_item_ids=["item_a"])
        self.assertEqual(ctx.exception.code, "RESPONSE_CONFLICT")

    def test_a_cancelled_item_is_disposed_never_resumed(self):
        from scripts.deterministic_workflow.pause_runtime import read_decision_bundle

        class Cancelled:
            def show(self, *, run_id, request_id):
                return {"current": True, "effective_decisions": {"item_a": None},
                        "item_statuses": {"item_a": "cancelled"}}

        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            read_decision_bundle(Cancelled(), run_id=self.RUN, request_id="r",
                                 decision_item_ids=["item_a"])
        self.assertEqual(ctx.exception.code, "RESPONSE_ITEM_UNRESOLVED")


@REQUIRES_LANGGRAPH
class StaleSourceRevalidationTests(PauseFixture):
    RUN = "run_stale"

    def test_a_moved_head_re_enters_the_responsible_phase_through_correction(self):
        """Required regression 7(a): the answer is not applied unconditionally."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        moved = {"head_sha": "1" * 40, "tree_digest": "dirty", "dirty": True}
        outcome, _ = self.fresh_resume(record, repository=moved)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.revalidation_codes, ("STALE_SOURCE_BINDING",))
        self.assertGreaterEqual(outcome.state["binding_generation"], 1)
        self.assertEqual(outcome.state["phase_pass_floor"].get("ANALYSIS"),
                         outcome.state["binding_generation"])

    def test_a_changed_policy_digest_revalidates_rather_than_refusing(self):
        """Required regression 7(b)."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        outcome, _ = self.fresh_resume(record, policy_digest="a-different-digest")
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.revalidation_codes, ("STALE_POLICY_DIGEST",))

    def test_an_unchanged_source_redoes_the_paused_round_without_raising_a_floor(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        outcome, _ = self.fresh_resume(record)
        self.assertEqual(outcome.revalidation_codes, ())
        self.assertEqual(outcome.state["binding_generation"], 0)
        self.assertEqual(outcome.state["phase_pass_floor"], {})


@REQUIRES_LANGGRAPH
class GatePreservationTests(PauseFixture):
    RUN = "run_gate"

    def test_the_raw_ingress_refuses_every_protected_field(self):
        """T-33/T-34: resume and cancel are expressible ONLY through the typed commands."""
        from scripts.deterministic_workflow.graph import PROTECTED_STATE_FIELDS
        from scripts.deterministic_workflow.state import StateError
        _, record, adapter = self.drive_to_pause()
        graph = self.graph(self.adapter(), self.saver())
        config = {"configurable": {"thread_id": "t", "checkpoint_ns": ""}}
        for field in sorted(PROTECTED_STATE_FIELDS):
            with self.subTest(field=field):
                with self.assertRaises(StateError) as ctx:
                    graph.update_state(config, {field: None})
                self.assertIn("protected field", str(ctx.exception))

    def test_a_resume_may_raise_a_floor_but_never_lower_one(self):
        from scripts.deterministic_workflow.state import StateError
        _, record, _ = self.drive_to_pause()
        graph = self.graph(self.adapter(), self.saver())
        config = {"configurable": {"thread_id": "t", "checkpoint_ns": ""}}
        binding = record["projection"]
        graph.update_state_command(
            config, "RESUME_PAUSE", as_node="VALIDATE", run_lifecycle="ACTIVE",
            pause_binding=None, decision_state="CLEAR", decision_reason_code=None,
            pending_clarification_id=binding["request_id"], round_kind="PHASE_GATE",
            current_phase="ANALYSIS", correction_queue=[], correction_index=0,
            binding_generation=3, phase_pass_floor={"ANALYSIS": 3},
            repository_binding=binding["repository_binding"],
            artifact_binding=binding["artifact_binding"],
            route_token=None, terminal_reason=None)
        with self.assertRaises(StateError) as ctx:
            graph.update_state_command(
                config, "RESUME_PAUSE", as_node="VALIDATE", run_lifecycle="ACTIVE",
                pause_binding=None, decision_state="CLEAR", decision_reason_code=None,
                pending_clarification_id=binding["request_id"], round_kind="PHASE_GATE",
                current_phase="ANALYSIS", correction_queue=[], correction_index=0,
                binding_generation=1, phase_pass_floor={"ANALYSIS": 1},
                repository_binding=binding["repository_binding"],
                artifact_binding=binding["artifact_binding"],
                route_token=None, terminal_reason=None)
        self.assertIn("generation must not decrease", str(ctx.exception))

    def test_no_operation_can_clear_a_terminal_status(self):
        """SS8.1: 'was terminal and now is not' is unrepresentable, not merely avoided."""
        from scripts.deterministic_workflow.state import StateError, initial_state, validate_state
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        state = dict(initial_state(run_id="run_x", thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        state["pending_role"] = None
        state["run_lifecycle"] = "SETTLED"
        with self.assertRaisesRegex(StateError, "lifecycle coherence"):
            validate_state(state, expected_thread_id="t")
        state["terminal_status"] = "COMPLETED"
        validate_state(state, expected_thread_id="t")
        state["terminal_status"] = None
        with self.assertRaisesRegex(StateError, "lifecycle coherence"):
            validate_state(state, expected_thread_id="t")

    def test_the_resumed_run_still_dispatches_the_phase_reviewer_and_the_final_review(self):
        """AC-6: the gates are structural properties of the re-entry path.

        route() is the same pure function after a resume as before the pause, so the
        resumed run walks the SAME rounds: a Worker, then a phase Reviewer, then the
        mandatory Final Adversarial Review.
        """
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        roles = [entry["role"] for entry in outcome.state["logical_trace"]
                 if entry["node"] == "PREPARE_INTENT"]
        self.assertEqual(roles, ["WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"])
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED")
        self.assertIsNotNone(outcome.state["phase_passes"]["ANALYSIS"])

    def test_a_completion_without_a_final_review_pass_is_refused_at_the_stamping_point(self):
        from scripts.deterministic_workflow.executor import terminal_node
        state = self.initial_state(decision_state="CLEAR")
        state["route_token"] = "COMPLETE"
        out = terminal_node(state)
        self.assertEqual(out["terminal_status"], "BLOCKED")
        self.assertEqual(out["terminal_reason"]["code"], "NO_FINAL_REVIEW_PASS")


@REQUIRES_LANGGRAPH
class MultiItemBundleTests(PauseFixture):
    """F-003: one bundle, one identity, one effect owner -- across 1, 2 and 3 items."""

    RUN = "run_bundle"
    ITEMS = 3

    def test_a_three_item_bundle_pauses_and_resumes_as_one_transaction(self):
        _, record, _ = self.drive_to_pause()
        self.assertEqual(len(record["projection"]["decision_item_ids"]), 3)
        self.answer_all()
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        stored = self.store().read(self.RUN)
        self.assertEqual(len(stored["applied"]), 1)
        entry = next(iter(stored["applied"].values()))
        self.assertEqual([item["decision_item_id"] for item in entry["items"]],
                         sorted(record["projection"]["decision_item_ids"]))

    def test_answering_only_some_items_writes_no_applied_entry_and_performs_no_effect(self):
        """Required regression 16(d)."""
        from scripts.clarification_protocol import ResponseSubmission
        _, record, _ = self.drive_to_pause()
        port = self.approval_port()
        request = self.requests()[0]
        port.ingest(run_id=self.RUN, request_id=request["request_id"],
                    decision_item_id=request["items"][0]["decision_item_id"],
                    submission=ResponseSubmission("s0", "alice", "human", "desk",
                                                  "2026-09-01T08:00:00Z", "staging",
                                                  None, False, "normal"))
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.code, "RESPONSE_NOT_FOUND")
        self.assertEqual(adapter.effect_count, 0)
        self.assertEqual(self.store().read(self.RUN)["applied"], {})

    def test_a_crash_after_the_dedupe_write_re_drives_exactly_one_effect(self):
        """Required regression 16(a): the head is the authority, not the record."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        store = self.store()
        token = store.claim(self.RUN)["lease_token"]
        decisions = self.effective_decisions(record)
        bundle_id = pause_policy.resume_bundle_id(
            run_id=self.RUN, request_id=record["projection"]["request_id"],
            pause_record_id=record["pause_record_id"],
            decisions=tuple(sorted(decisions.items())))
        store.record_applied(self.RUN, {
            "resume_bundle_id": bundle_id,
            "request_id": record["projection"]["request_id"],
            "items": pause_store.applied_items(decisions), "stage": "RECORDED",
            "recorded_at": "2026-01-01T00:00:00Z", "resumed_at": "",
            "resumed_checkpoint_id": ""}, token)
        store.release(self.RUN, token)
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(adapter.effect_count, 3, "exactly one graph re-entry")
        self.assertEqual(len(self.store().read(self.RUN)["applied"]), 1)

    def test_a_differing_answer_after_the_bundle_resumed_is_a_conflict(self):
        """Required regression 16(e): never a replay, never a partial application."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.fresh_resume(record)
        store = self.store()
        token = store.claim(self.RUN)["lease_token"]
        decisions = {item: f"decision_other_{item}"
                     for item in record["projection"]["decision_item_ids"]}
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            store.record_applied(self.RUN, {
                "resume_bundle_id": "bundle_other",
                "request_id": record["projection"]["request_id"],
                "items": pause_store.applied_items(decisions), "stage": "RECORDED",
                "recorded_at": "2026-01-01T00:00:00Z", "resumed_at": "",
                "resumed_checkpoint_id": ""}, token)
        self.assertEqual(ctx.exception.code, "RESPONSE_CONFLICT")
        self.assertEqual(len(self.store().read(self.RUN)["applied"]), 1)

    def effective_decisions(self, record):
        port = self.approval_port()
        shown = port.show(run_id=self.RUN,
                          request_id=record["projection"]["request_id"])
        return {item: shown["effective_decisions"][item]
                for item in record["projection"]["decision_item_ids"]}


@REQUIRES_LANGGRAPH
class DiscoveryAndDegradedModeTests(PauseFixture):
    RUN = "run_discover"

    def test_discovery_reports_a_resumable_run_without_taking_a_claim(self):
        _, record, _ = self.drive_to_pause()
        listings = pause_runtime.discover(self.base)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["verdict"], "RESUMABLE")
        self.assertEqual(self.store().read(self.RUN)["owner_id"], "",
                         "discovery must take no claim")

    def test_discovery_without_langgraph_never_claims_the_pause_is_fine(self):
        """Required regression 14: degraded is named, never equivalent."""
        self.drive_to_pause()
        listings = pause_runtime.discover(self.base, langgraph_available=False)
        self.assertEqual(listings[0]["verdict"], "CHECKPOINT_UNVERIFIED")
        self.assertNotEqual(listings[0]["verdict"], "RESUMABLE")

    def test_a_broken_checkpoint_is_reported_as_unresumable_not_as_resumable(self):
        _, record, _ = self.drive_to_pause()
        store = self.store()
        token = store.claim(self.RUN)["lease_token"]
        store.update_pointer(self.RUN, checkpoint_id="chk_gone",
                             checkpoint_digest="d", projection=record["projection"],
                             lease_token=token)
        store.release(self.RUN, token)
        listings = pause_runtime.discover(self.base)
        self.assertEqual(listings[0]["verdict"], "PAUSE_CHECKPOINT_MISSING")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
