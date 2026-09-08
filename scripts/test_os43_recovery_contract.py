"""OS-43 U-1 (the wave-0 contract probe) and U-2 (the engine-owned recovery API).

U-1 exists to surface a WRONG CONTRACT before anything depends on it.  Every assertion
below is executed against RUNNING SOURCE -- the real stores, the real classifier, the real
adapter capabilities -- never against a prior run's claim about them, which is the standing
UR-5 rule.  If the outcome mapping the API composes is not the one the engine primitives
actually produce, these tests fail in wave 0 with no dependent code in existence.

Nothing here sleeps.  Every lease, expiry and observation window is driven by
``runtime_state.ManualLeaseClock`` through ``LeaseClockPort``, which exists precisely so
"every lease/observation/lock-timeout test advances time explicitly instead of sleeping"
(``ports.py:178-186``).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts.deterministic_workflow import (pause_policy, pause_store, recovery_runtime,
                                            recovery_store, runtime_state)
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
from scripts.test_deterministic_workflow_pause import record as pause_record
from scripts.test_deterministic_workflow_pause_fixture import (REQUIRES_LANGGRAPH, REVIEW_PASS,
                                                               WORKER, PauseFixture)

RUN = "run_x"


# ======================================================================================
# U-1.  The five outcome families, pinned against the primitives that produce them.
# ======================================================================================
class ClaimOutcomeVocabularyTests(unittest.TestCase):
    """RK-1's detector: what the engine's two run-scoped claims actually return."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = ManualLeaseClock()

    def pause_store_for(self, owner="host:pid1"):
        return pause_store.FilePauseRecordStore(self.root / "pause.json",
                                                clock=self.clock, owner_id=owner)

    def test_runtime_state_claim_outcomes_are_exactly_three_names(self):
        self.assertEqual(runtime_state.CLAIM_OUTCOMES,
                         ("CREATED", "RESUMED", "ALREADY_SETTLED"))
        self.assertEqual(recovery_store.RECOVERY_CLAIM_OUTCOMES,
                         runtime_state.CLAIM_OUTCOMES,
                         "the new run-scoped lease mirrors the engine's own outcome "
                         "vocabulary rather than inventing a third meaning")

    def test_pause_claim_reports_the_three_already_settled_outcomes(self):
        store = self.pause_store_for()
        store.create(pause_record())
        self.assertEqual(store.claim(RUN)["claim_outcome"], pause_store.CREATED)
        token = store.read(RUN)["lease_token"]
        store.mark_resumed(RUN, token)
        self.assertEqual(store.claim(RUN)["claim_outcome"], pause_store.ALREADY_RESUMED)
        for name in ("ALREADY_RESUMED", "ALREADY_CANCELLED", "ALREADY_ABANDONED"):
            self.assertIn(name.replace("ALREADY_", "RUN_ALREADY_"),
                          pause_policy.PAUSE_REFUSAL_CODES)

    def test_a_live_foreign_lease_raises_rather_than_returning_an_outcome(self):
        owner, rival = self.pause_store_for("host:pid1"), self.pause_store_for("host:pid2")
        owner.create(pause_record())
        owner.claim(RUN)
        with self.assertRaises(pause_store.PauseClaimHeld):
            rival.claim(RUN)
        # And once the lease lapses -- advanced explicitly, never slept -- takeover is legal.
        self.clock.advance(pause_store.DEFAULT_LEASE_SECONDS + 1.0)
        self.assertEqual(rival.claim(RUN)["claim_outcome"], pause_store.RESUMED)

    def test_the_claim_mints_and_ROTATES_the_token_which_is_the_fence(self):
        owner, rival = self.pause_store_for("host:pid1"), self.pause_store_for("host:pid2")
        owner.create(pause_record())
        first = owner.claim(RUN)["lease_token"]
        self.clock.advance(pause_store.DEFAULT_LEASE_SECONDS + 1.0)
        second = rival.claim(RUN)["lease_token"]
        self.assertNotEqual(first, second, "a takeover must rotate the token")
        with self.assertRaises(pause_store.PauseClaimLost):
            owner.heartbeat(RUN, first)

    def test_fenced_has_no_no_token_supplied_branch(self):
        store = self.pause_store_for()
        store.create(pause_record())
        store.claim(RUN)
        for absent in ("", None):
            with self.assertRaises(pause_store.PauseClaimRequired):
                store.heartbeat(RUN, absent)          # type: ignore[arg-type]
        # The new recovery lease copies the rule verbatim.
        recovery = recovery_store.FileRecoveryStateStore(self.root / "recovery.json",
                                                         clock=self.clock)
        recovery.claim(RUN)
        for absent in ("", None):
            with self.assertRaises(recovery_store.RecoveryClaimRequired):
                recovery.heartbeat(RUN, absent)       # type: ignore[arg-type]

    def test_the_observation_window_is_bounded_and_never_shorter_than_the_lease(self):
        for lease in (0.25, 12.0, 600.0):
            self.assertEqual(pause_store.observe_timeout_for(lease),
                             lease + pause_store.DEFAULT_OBSERVE_GRACE_SECONDS)


class RecoveryOutcomeCodeTableTests(unittest.TestCase):
    """Every code the API may return comes from a vocabulary that ALREADY exists."""

    def test_the_outcome_set_is_exactly_six_closed_members(self):
        self.assertEqual(recovery_runtime.RECOVERY_OUTCOMES,
                         ("RECOVERED", "NO_EFFECT", "NOT_RECOVERABLE", "CONFLICT",
                          "UNSUPPORTED", "REFUSED"))
        self.assertEqual(set(recovery_runtime.RECOVERY_OUTCOME_CODES),
                         set(recovery_runtime.RECOVERY_OUTCOMES),
                         "every outcome names its own closed code set; a default branch "
                         "is what lets prose escape as a code")

    def test_every_code_belongs_to_an_engine_vocabulary_that_exists_today(self):
        known = (pause_policy.PAUSE_REFUSAL_CODES | pause_policy.PAUSE_RECOVERY_CODES
                 | pause_policy.RECOVERY_REFUSAL_CODES
                 | pause_policy.RECOVERY_PROGRESS_CODES
                 | {"IDEMPOTENCY_RECOVERY_UNSUPPORTED", "LANGGRAPH_DEPENDENCY_MISSING",
                    "LEASE_LOST", "IDEMPOTENCY_LEASE_LOST", "IDEMPOTENCY_LEASE_HELD",
                    "RUNTIME_STATE_ERROR"})
        for outcome, codes in recovery_runtime.RECOVERY_OUTCOME_CODES.items():
            for code in codes:
                self.assertIn(code, known, f"{outcome} carries an invented code {code!r}")

    def test_the_new_recovery_codes_are_additive_and_disjoint(self):
        self.assertFalse(pause_policy.RECOVERY_REFUSAL_CODES
                         & pause_policy.PAUSE_REFUSAL_CODES,
                         "a sibling set, not new members of the OS-31 refusals")
        self.assertFalse(pause_policy.RECOVERY_PROGRESS_CODES
                         & pause_policy.RECOVERY_REFUSAL_CODES)
        # No existing member was removed or reordered.
        for member in ("PAUSE_CLAIM_HELD", "PAUSE_CLAIM_LOST", "PAUSE_OBSERVATION_TIMEOUT",
                       "STALE_CHECKPOINT_HEAD", "PAUSE_CONTINUATION_UNRECOVERABLE",
                       "PAUSE_CHECKPOINT_MISSING", "CHECKPOINT_UNVERIFIED"):
            self.assertIn(member, pause_policy.PAUSE_REFUSAL_CODES)

    def test_a_refusal_outcome_cannot_carry_a_continuation_handle(self):
        """AC-7 mechanism 3, enforced by the TYPE rather than by a caller's discipline."""
        for status in recovery_runtime.RECOVERY_TERMINAL_OUTCOMES:
            code = sorted(recovery_runtime.RECOVERY_OUTCOME_CODES[status])[0]
            with self.assertRaises(ValueError):
                recovery_runtime.RecoveryOutcome(status, code, effect_performed=True)
            with self.assertRaises(ValueError):
                recovery_runtime.RecoveryOutcome(status, code,
                                                 resumed_checkpoint_id="cp_1")

    def test_an_outcome_may_not_carry_a_code_from_another_outcomes_set(self):
        with self.assertRaises(ValueError):
            recovery_runtime.RecoveryOutcome(recovery_runtime.RECOVERED,
                                             "PAUSE_CLAIM_HELD")
        with self.assertRaises(ValueError):
            recovery_runtime.RecoveryOutcome("MADE_UP", "RECOVERY_ADVANCED")

    def test_the_request_is_frozen_and_carries_no_bypass_field(self):
        """AC-7 mechanism 1: a bypass that cannot be EXPRESSED cannot be attempted."""
        import dataclasses
        fields = {field.name for field in
                  dataclasses.fields(recovery_runtime.RecoveryRequest)}
        for forbidden in ("next_node", "route", "verdict", "recoverable", "phase",
                          "responsible_phase", "decision_bundle_id", "lease_token",
                          "token", "force"):
            self.assertNotIn(forbidden, fields)
        request = recovery_runtime.RecoveryRequest(run_id=RUN, artifact_base=".",
                                                   graph_factory=lambda saver: None)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.run_id = "other"          # type: ignore[misc]
        with self.assertRaises(TypeError):
            recovery_runtime.RecoveryRequest(run_id=RUN, artifact_base=".",
                                             graph_factory=lambda saver: None,
                                             next_node="PREPARE_WORKER")  # type: ignore


class RealAdapterCapabilityTests(unittest.TestCase):
    """The UNSUPPORTED family, pinned against the REAL adapter rather than a fake."""

    def test_the_real_orca_adapter_withholds_external_resume_on_purpose(self):
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        adapter = OrcaAdapter.__new__(OrcaAdapter)
        adapter.settlement_journal = None
        adapter.approval_port = None
        declared = adapter.capabilities()
        self.assertNotIn("external_resume", declared,
                         "a settlement delivered to a dead process cannot be re-collected "
                         "through any documented Orca primitive; OS-43 fails CLOSED on "
                         "that rather than working around it")
        self.assertIn("external_lookup", declared)

    def test_the_unsupported_codes_are_the_two_the_engine_actually_raises(self):
        from scripts.deterministic_workflow import executor
        self.assertIn("IDEMPOTENCY_RECOVERY_UNSUPPORTED",
                      recovery_runtime.RECOVERY_OUTCOME_CODES[
                          recovery_runtime.UNSUPPORTED])
        self.assertTrue(hasattr(executor, "IdempotencyRecoveryError"))
        self.assertIn("LANGGRAPH_DEPENDENCY_MISSING",
                      recovery_runtime.RECOVERY_OUTCOME_CODES[
                          recovery_runtime.UNSUPPORTED])


class RecoveryIdentityTests(unittest.TestCase):
    """AC-5's identity: keyed on the COMMITTED head, so "same identity" == "unmoved"."""

    def identity(self, **overrides: Any) -> str:
        base = {"run_id": RUN, "thread_id": "t", "checkpoint_ns": "",
                "head_checkpoint_id": "cp_1",
                "recovery_kind": recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE}
        return recovery_runtime.recovery_identity(**{**base, **overrides})

    def test_the_identity_is_stable_and_content_addressed(self):
        self.assertEqual(self.identity(), self.identity())
        self.assertTrue(self.identity().startswith("recovery_"))

    def test_a_moved_head_yields_a_DIFFERENT_identity(self):
        self.assertNotEqual(self.identity(), self.identity(head_checkpoint_id="cp_2"))

    def test_each_component_of_the_tuple_is_load_bearing(self):
        for field, value in (("run_id", "other"), ("thread_id", "t2"),
                             ("checkpoint_ns", "ns"),
                             ("recovery_kind",
                              recovery_runtime.RECOVERY_KIND_PAUSE_CONTINUATION)):
            self.assertNotEqual(self.identity(), self.identity(**{field: value}), field)

    def test_an_unknown_recovery_kind_is_refused(self):
        with self.assertRaises(ValueError):
            self.identity(recovery_kind="whatever")


# ======================================================================================
# U-2.  The composed API, against real stores.
# ======================================================================================
class RecoveryStoreAttemptLedgerTests(unittest.TestCase):
    """The write-before-effect ordering AC-5 rests on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = ManualLeaseClock()
        self.path = Path(self.tmp.name) / "recovery.json"

    def store(self, owner="host:pid1"):
        return recovery_store.FileRecoveryStateStore(self.path, clock=self.clock,
                                                     owner_id=owner)

    def entry(self, recovery_id="recovery_1", head_before="cp_1"):
        return {"recovery_id": recovery_id, "recovery_kind": "stalled_active",
                "stage": "CLAIMED", "head_before": head_before, "head_after": "",
                "outcome": "", "code": "", "actor_id": "watchdog",
                "opened_at": "2026-09-08T00:00:00Z", "promoted_at": None}

    def test_two_claimants_produce_exactly_one_CREATED(self):
        first, second = self.store("host:pid1"), self.store("host:pid2")
        self.assertEqual(first.claim(RUN)["claim_outcome"], recovery_store.CREATED)
        with self.assertRaises(recovery_store.RecoveryClaimHeld):
            second.claim(RUN)

    def test_the_attempt_entry_is_written_before_the_effect_and_is_idempotent(self):
        store = self.store()
        token = store.claim(RUN)["lease_token"]
        store.open_attempt(RUN, self.entry(), lease_token=token)
        store.open_attempt(RUN, self.entry(head_before="DIFFERENT"), lease_token=token)
        stored = store.read(RUN)["attempts"]["recovery_1"]
        self.assertEqual(stored["stage"], "CLAIMED")
        self.assertEqual(stored["head_before"], "cp_1",
                         "a re-drive of the same crash window replays open_attempt and "
                         "must not disturb what the earlier process recorded")

    def test_promoting_an_attempt_that_was_never_opened_is_refused(self):
        store = self.store()
        token = store.claim(RUN)["lease_token"]
        with self.assertRaises(recovery_store.RecoveryStoreError):
            store.promote_attempt(RUN, "recovery_1", head_after="cp_2",
                                  outcome="RECOVERED", code="RECOVERY_ADVANCED",
                                  promoted_at="t", lease_token=token)

    def test_a_corrupt_record_is_never_read_as_no_prior_attempt(self):
        # The CURRENT schema version, read from the module: this test is about a malformed
        # RECORD, and pinning the version literal would silently turn it into a second
        # copy of the incompatible-version test below the next time the schema is bumped.
        self.path.write_text(
            '{"schema_version": "%s", "record": {"run_id": "run_x"}}'
            % recovery_store.RECOVERY_RECORD_SCHEMA_VERSION, encoding="utf-8")
        with self.assertRaises(recovery_store.RecoveryRecordCorrupt):
            self.store().read(RUN)

    def test_an_incompatible_schema_version_raises_rather_than_reading_empty(self):
        self.path.write_text('{"schema_version": "os43.recovery_state.v0", '
                             '"record": {}}', encoding="utf-8")
        with self.assertRaises(recovery_store.RecoveryRecordCorrupt):
            self.store().read(RUN)


class RecoveryApiRefusalTests(unittest.TestCase):
    """The branches that are reachable with no LangGraph and no checkpoint."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def request(self, run_id=RUN):
        return recovery_runtime.RecoveryRequest(run_id=run_id,
                                                artifact_base=str(self.base),
                                                graph_factory=lambda saver: None)

    def test_a_run_with_no_checkpoint_is_NOT_RECOVERABLE_not_an_invocation(self):
        (self.base / "artifacts" / "runs" / RUN).mkdir(parents=True)
        outcome = recovery_runtime.recover_stalled_run(self.request())
        self.assertIn(outcome.status, (recovery_runtime.NOT_RECOVERABLE,
                                       recovery_runtime.UNSUPPORTED))
        self.assertFalse(outcome.effect_performed)
        self.assertEqual(outcome.resumed_checkpoint_id, "")

    def test_an_unknown_actor_type_is_refused_before_anything_is_claimed(self):
        with self.assertRaises(ValueError):
            recovery_runtime.recover_stalled_run(
                recovery_runtime.RecoveryRequest(run_id=RUN,
                                                 artifact_base=str(self.base),
                                                 graph_factory=lambda saver: None,
                                                 actor_type="root"))

    @REQUIRES_LANGGRAPH
    def test_a_pause_record_AND_a_live_recovery_lease_is_CONFLICT_never_resolved(self):
        """DR-3: two run-scoped leases on one run is refused, not arbitrated."""
        root = self.base / "artifacts" / "runs" / RUN
        root.mkdir(parents=True)
        store = pause_store.store_for(RUN, artifact_base=self.base)
        store.create(pause_record())
        recovery_store.store_for(RUN, artifact_base=self.base).claim(RUN)
        outcome = recovery_runtime.recover_stalled_run(self.request())
        self.assertEqual(outcome.status, recovery_runtime.CONFLICT)
        self.assertEqual(outcome.code, "RECOVERY_CLAIM_HELD")
        self.assertFalse(outcome.effect_performed)


@REQUIRES_LANGGRAPH
class PausedBranchDelegationTests(PauseFixture):
    """CON-2 literally: for a PAUSED run OS-31 remains the single authority."""

    RUN = "run_pause"

    def test_an_unanswered_pause_is_never_auto_resumed(self):
        """NG-5.  Only a continuation a dead process already COMMITTED is recoverable."""
        _final, record, _adapter = self.drive_to_pause()
        self.assertEqual(record["status"], "WAITING_FOR_INPUT")
        outcome = recovery_runtime.recover_stalled_run(
            recovery_runtime.RecoveryRequest(
                run_id=self.RUN, artifact_base=str(self.base),
                graph_factory=lambda saver: None))
        self.assertEqual(outcome.status, recovery_runtime.NOT_RECOVERABLE)
        self.assertEqual(outcome.code, "RECOVERY_NO_RUNNABLE_NODE")
        self.assertFalse(outcome.effect_performed)
        # And the pause is still there, untouched.
        self.assertEqual(self.store().read(self.RUN)["status"], "WAITING_FOR_INPUT")

    def test_a_disposed_run_reports_NO_EFFECT_and_performs_none(self):
        self.drive_to_pause()
        self.answer_all()
        outcome, _adapter = self.fresh_resume(self.store().read(self.RUN),
                                              results=(WORKER, REVIEW_PASS, REVIEW_PASS))
        self.assertEqual(outcome.status, "RESUMED")
        recovered = recovery_runtime.recover_stalled_run(
            recovery_runtime.RecoveryRequest(
                run_id=self.RUN, artifact_base=str(self.base),
                graph_factory=lambda saver: None))
        self.assertIn(recovered.status, (recovery_runtime.NO_EFFECT,
                                         recovery_runtime.NOT_RECOVERABLE))
        self.assertFalse(recovered.effect_performed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
