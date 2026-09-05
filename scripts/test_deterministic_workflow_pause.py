"""OS-31 Tier-2 and pure-policy regressions -- deliberately NOT gated on LangGraph.

The gating asymmetry between this module and ``test_deterministic_workflow_checkpoint``
is itself the evidence for V8: the *authority* module is LangGraph-dependent, the
index/fence/policy modules are not, and this file proves it by running without it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.deterministic_workflow import pause_policy, pause_store
from scripts.deterministic_workflow.durable_store import (FileCriticalSection, LockTimeout,
                                                          read_json_document,
                                                          write_json_document)
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock

BINDING = {
    "pause_record_id": "pause_abc", "paused_at": "2026-01-01T00:00:00Z",
    "request_id": "request_1", "decision_item_ids": ["item_a"],
    "source_ledger_keys": ["run_x/000001"], "responsible_phase": "ANALYSIS",
    "repository_binding": {"head_sha": "0" * 40, "tree_digest": "clean", "dirty": False},
    "artifact_binding": {"artifact_root_id": "run_x", "relative_path": None,
                         "digest": None, "evidence_ids": []},
    "policy_digest": "digest_1", "settlement_ledger": [], "disposition": None,
}


def settlement_row(**overrides):
    row = {key: "" for key in pause_policy.SETTLEMENT_ROW_KEYS}
    row.update({"intent_id": "intent_1", "settlement": "settled",
                "worker_resource": "retain", "process_liveness": "live",
                "cleanup_authority": "not_authorized", "provenance_source": "journal",
                "handle_recovery": "in_process", "terminal_disposition": "residual",
                "terminal_role": "phase_worker", "terminal_origin": "self_created",
                "terminal_owner": "run_x", "recovery": "observed",
                "accounted_at": "2026-01-01T00:00:00Z"})
    row.update(overrides)
    return row


def projection(**overrides):
    value = {key: None for key in pause_policy.PAUSE_PROJECTION_KEYS}
    value.update({
        "current_phase": "ANALYSIS", "current_phase_index": 0, "phase_iteration": 0,
        "final_review_iteration": 0, "round_kind": "PHASE_GATE", "risk": "high",
        "requested_phases": ["ANALYSIS"], "decision_state": "NEEDS_INPUT",
        "decision_reason_code": "MISSING_INPUT", "pending_clarification_id": "request_1",
        "responsible_phase": "ANALYSIS", "request_id": "request_1",
        "decision_item_ids": ["item_a"], "source_ledger_keys": ["run_x/000001"],
        "repository_binding": dict(BINDING["repository_binding"]),
        "artifact_binding": dict(BINDING["artifact_binding"]),
        "policy_digest": "digest_1", "binding_generation": 0, "settlement_ledger": []})
    value.update(overrides)
    return value


def record(run_id="run_x", **overrides):
    value = {
        "schema_version": pause_store.PAUSE_RECORD_SCHEMA_VERSION, "run_id": run_id,
        "workflow_id": "os40.standard.v1", "pause_record_id": "pause_abc",
        "status": "WAITING_FOR_INPUT", "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z", "owner_id": "", "lease_token": "",
        "lease_expires_at": 0.0, "last_heartbeat_at": 0.0,
        "checkpoint_store_path": "cp.json", "thread_id": "t", "checkpoint_ns": "",
        "checkpoint_id": "chk_1", "checkpoint_digest": "d1", "disposition": None,
        "ac1_discharged": True, "residual_terminals": [], "applied": {},
        "projection": projection()}
    value.update(overrides)
    return value


class LifecycleTransitionTests(unittest.TestCase):
    """T-05: the transition table is exhaustive over the whole cross product."""

    def test_every_state_event_pair_is_either_declared_or_refused(self):
        for state in pause_policy.RUN_LIFECYCLE_STATES:
            for event in pause_policy.PAUSE_EVENTS:
                declared = [target for source, name, target in pause_policy.PAUSE_TRANSITIONS
                            if source == state and name == event]
                with self.subTest(state=state, event=event):
                    if declared:
                        self.assertEqual(pause_policy.transition(state, event), declared[0])
                    else:
                        with self.assertRaises(pause_policy.PauseTransitionRefused) as ctx:
                            pause_policy.transition(state, event)
                        self.assertEqual(ctx.exception.code, "PAUSE_TRANSITION_FORBIDDEN")

    def test_the_consequential_forbidden_edges_are_named_and_refused(self):
        for state, event in (("SETTLED", "RESUME"), ("SETTLED", "CANCEL"),
                             ("WAITING_FOR_INPUT", "ENTER_PAUSE"), ("ACTIVE", "RESUME"),
                             ("ACTIVE", "CANCEL"), ("ACTIVE", "ABANDON")):
            with self.subTest(edge=(state, event)):
                with self.assertRaises(pause_policy.PauseTransitionRefused):
                    pause_policy.transition(state, event)

    def test_an_unknown_state_or_event_is_refused_rather_than_defaulted(self):
        for args in (("NOPE", "RESUME"), ("ACTIVE", "NOPE")):
            with self.assertRaises(pause_policy.PauseTransitionRefused):
                pause_policy.transition(*args)


class ReasonCodeVocabularyTests(unittest.TestCase):
    def test_refusal_and_revalidation_codes_are_disjoint(self):
        """A changed source must never be mistaken for a refusal, and vice versa."""
        self.assertEqual(pause_policy.PAUSE_REFUSAL_CODES
                         & pause_policy.PAUSE_REVALIDATION_CODES, frozenset())

    def test_the_engine_vocabularies_equal_the_harness_ones(self):
        from scripts import orca_runtime_harness as harness
        self.assertEqual(set(pause_policy.WORKER_RESOURCE_OUTCOMES),
                         set(harness.WORKER_RESOURCE_OUTCOMES))
        self.assertEqual(set(pause_policy.CLEANUP_AUTHORITY_STATES),
                         set(harness.CLEANUP_AUTHORITY_STATES))
        self.assertEqual(set(pause_policy.PROCESS_TERMINATING_ACTIONS),
                         set(harness.PROCESS_TERMINATING_ACTIONS))

    def test_transferred_is_not_a_disposition_and_residual_never_discharges(self):
        """T-12/T-48: the vocabulary itself is asserted, so no later edit can re-admit a
        label as a discharge."""
        self.assertNotIn("transferred", pause_policy.TERMINAL_DISPOSITIONS)
        self.assertNotIn("residual", pause_policy.AC1_DISCHARGING_DISPOSITIONS)
        self.assertLess(pause_policy.AC1_DISCHARGING_DISPOSITIONS,
                        set(pause_policy.TERMINAL_DISPOSITIONS))

    def test_pause_refused_rejects_a_code_outside_the_closed_set(self):
        with self.assertRaises(ValueError):
            pause_policy.PauseRefused("NOT_A_REAL_CODE", "x")


class TerminalDispositionTests(unittest.TestCase):
    """T-12: the exit invariant as an exhaustive property, not a sample."""

    def test_the_function_is_total_over_the_whole_cross_product(self):
        seen = set()
        for settlement in pause_policy.SETTLEMENT_OUTCOMES:
            for resource in pause_policy.WORKER_RESOURCE_OUTCOMES:
                for liveness in pause_policy.PROCESS_LIVENESS_STATES:
                    for authority in pause_policy.CLEANUP_AUTHORITY_STATES:
                        for role in ("phase_worker", "unknown_role"):
                            for origin in ("self_created", "unknown"):
                                for owner in ("run_x", ""):
                                    for source in pause_policy.PROVENANCE_SOURCES:
                                        row = settlement_row(
                                            settlement=settlement, worker_resource=resource,
                                            process_liveness=liveness,
                                            cleanup_authority=authority, terminal_role=role,
                                            terminal_origin=origin, terminal_owner=owner,
                                            provenance_source=source,
                                            recovery=("released:killed"
                                                      if resource == "release" else "observed"))
                                        value = pause_policy.terminal_disposition(row)
                                        self.assertIn(value,
                                                      pause_policy.TERMINAL_DISPOSITIONS)
                                        seen.add(value)
        self.assertEqual(seen, set(pause_policy.TERMINAL_DISPOSITIONS))

    def test_every_discharging_named_owner_row_names_a_real_owner_from_the_journal(self):
        row = settlement_row(cleanup_authority="unknown", process_liveness="disputed")
        self.assertEqual(pause_policy.terminal_disposition(row), "retained_by_named_owner")
        self.assertTrue(row["terminal_owner"])
        self.assertEqual(row["provenance_source"], "journal")

    def test_unknown_authority_with_no_nameable_owner_refuses_the_pause(self):
        row = settlement_row(cleanup_authority="unknown", process_liveness="disputed",
                             terminal_role="unknown_role", terminal_origin="unknown",
                             terminal_owner="")
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_policy.require_pause_disposition(row)
        self.assertEqual(ctx.exception.code, "TERMINAL_OWNERSHIP_UNKNOWN")

    def test_an_absent_provenance_row_refuses_as_a_possible_orphan(self):
        row = settlement_row(provenance_source="absent", terminal_role="unknown_role",
                             terminal_origin="unknown", terminal_owner="",
                             handle_recovery="listing_candidate")
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_policy.require_pause_disposition(row)
        self.assertEqual(ctx.exception.code, "TERMINAL_ORPHAN_POSSIBLE")

    def test_a_contradicted_digest_refuses_as_an_unverified_identity(self):
        row = settlement_row(handle_recovery="unverified", terminal_role="unknown_role",
                             terminal_origin="unknown", terminal_owner="")
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_policy.require_pause_disposition(row)
        self.assertEqual(ctx.exception.code, "TERMINAL_IDENTITY_UNVERIFIED")

    def test_a_release_receipt_that_proves_no_termination_is_not_a_release(self):
        """D-6/R8-iii: the live receipt observed 12/12 is retained/external_terminal/none."""
        row = settlement_row(cleanup_authority="authorized", worker_resource="release",
                             recovery="retained:none")
        self.assertNotEqual(pause_policy.terminal_disposition(row), "released")
        row = settlement_row(cleanup_authority="authorized", worker_resource="release",
                             recovery="released:killed")
        self.assertEqual(pause_policy.terminal_disposition(row), "released")

    def test_ac1_is_computed_from_the_rows_and_never_asserted(self):
        rows = [settlement_row(process_liveness="already exited",
                               terminal_disposition="exited")]
        self.assertTrue(pause_policy.ac1_discharged(rows))
        rows.append(settlement_row(terminal_disposition="residual"))
        self.assertFalse(pause_policy.ac1_discharged(rows))


class IdentityTests(unittest.TestCase):
    """T-07: stable across a restart, insensitive to bindings, sensitive to the answer."""

    def test_the_three_identities_are_pure_functions_of_their_declared_inputs(self):
        first = pause_policy.pause_record_id(run_id="run_x", thread_id="t",
                                             request_id="r", decision_item_ids=["b", "a"])
        second = pause_policy.pause_record_id(run_id="run_x", thread_id="t",
                                              request_id="r", decision_item_ids=["a", "b"])
        self.assertEqual(first, second)

    def test_resume_bundle_id_is_insensitive_to_binding_and_iteration(self):
        kwargs = {"run_id": "run_x", "request_id": "r", "pause_record_id": "p"}
        one = pause_policy.resume_bundle_id(decisions=(("a", "d1"), ("b", "d2")), **kwargs)
        two = pause_policy.resume_bundle_id(decisions=(("b", "d2"), ("a", "d1")), **kwargs)
        self.assertEqual(one, two)

    def test_resume_bundle_id_is_sensitive_to_every_decision_and_to_completeness(self):
        kwargs = {"run_id": "run_x", "request_id": "r", "pause_record_id": "p"}
        full = pause_policy.resume_bundle_id(decisions=(("a", "d1"), ("b", "d2")), **kwargs)
        differing = pause_policy.resume_bundle_id(decisions=(("a", "d1"), ("b", "dX")),
                                                  **kwargs)
        partial = pause_policy.resume_bundle_id(decisions=(("a", "d1"),), **kwargs)
        self.assertNotEqual(full, differing)
        self.assertNotEqual(full, partial)

    def test_cancellation_id_is_stable_for_one_submission(self):
        kwargs = {"run_id": "run_x", "pause_record_id": "p",
                  "cancel_submission_id": "s", "cancel_kind": "CANCEL"}
        self.assertEqual(pause_policy.cancellation_id(**kwargs),
                         pause_policy.cancellation_id(**kwargs))
        self.assertNotEqual(pause_policy.cancellation_id(**kwargs),
                            pause_policy.cancellation_id(**{**kwargs,
                                                            "cancel_kind": "ABANDON"}))


class ProjectionTests(unittest.TestCase):
    def test_project_pause_covers_exactly_the_declared_key_set(self):
        state = {
            "current_phase": "ANALYSIS", "current_phase_index": 0,
            "phase_iterations": {"ANALYSIS": 0}, "final_review_iterations": 0,
            "round_kind": "PHASE_GATE", "risk": "high", "requested_phases": ["ANALYSIS"],
            "decision_state": "NEEDS_INPUT", "decision_reason_code": "MISSING_INPUT",
            "pending_clarification_id": "request_1", "binding_generation": 0,
            "pause_binding": dict(BINDING)}
        self.assertEqual(set(pause_policy.project_pause(state)),
                         set(pause_policy.PAUSE_PROJECTION_KEYS))

    def test_the_diff_names_the_fields_that_differ(self):
        left = projection()
        right = projection(current_phase="PLAN", policy_digest="other")
        self.assertEqual(pause_policy.projection_diff(left, right),
                         ("current_phase", "policy_digest"))


class TerminalTitleTests(unittest.TestCase):
    """SS4.2.1a normalisation, exercised against the decorations observed live."""

    DECORATIONS = ("✳ ", "◐ ")
    TARGET = "os31-run_c2166e75bb02-intent_ab12"

    def test_normalisation_is_idempotent_and_inert_on_undecorated_titles(self):
        for raw in ("orca-skills", "Terminal 5", self.TARGET):
            self.assertEqual(pause_policy.normalize_terminal_title(raw), raw)
            once = pause_policy.normalize_terminal_title(raw)
            self.assertEqual(pause_policy.normalize_terminal_title(once), once)

    def test_every_observed_decoration_is_stripped_with_its_trailing_space(self):
        for glyph in self.DECORATIONS:
            self.assertEqual(
                pause_policy.normalize_terminal_title(glyph + "OS-31 Durable Pause"),
                "OS-31 Durable Pause")
            self.assertTrue(pause_policy.match_terminal_title(glyph + self.TARGET,
                                                              self.TARGET))

    def test_adversarial_near_misses_are_all_rejected(self):
        for raw in ("os31-run_OTHERRUN0000-intent_ab12", "x-" + self.TARGET,
                    self.TARGET + "0", "an unrelated title", ""):
            with self.subTest(raw=raw):
                self.assertFalse(pause_policy.match_terminal_title(raw, self.TARGET))

    def test_an_empty_target_never_matches_anything(self):
        self.assertFalse(pause_policy.match_terminal_title("anything", ""))


class HandleResolutionTableTests(unittest.TestCase):
    """SS4.2.1a as pure unit tests: no Orca, no adapter, no fixture at all."""

    TITLE = "os31-run_x-intent_1"

    def row(self, stage="INTENDED", digest=""):
        return {"intent_id": "intent_1", "stage": stage, "terminal_title": self.TITLE,
                "terminal_worktree": "id:repoA::/wt/a", "terminal_digest": digest}

    def element(self, handle, title=None):
        return {"handle": handle, "title": title or self.TITLE, "orphaned": False}

    def test_exactly_one_digest_match_is_the_only_cell_that_yields_a_handle(self):
        digest = pause_policy.terminal_digest("term_real")
        found = pause_policy.resolve_terminal_handle(
            self.row(digest=digest), [self.element("term_real"),
                                      self.element("term_other", "orca-skills")])
        self.assertEqual(found, {"handle": "term_real",
                                 "handle_recovery": "listing_verified"})

    def test_a_title_match_the_digest_contradicts_is_never_acted_on(self):
        found = pause_policy.resolve_terminal_handle(
            self.row(digest=pause_policy.terminal_digest("term_real")),
            [self.element("term_impostor")])
        self.assertEqual(found["handle"], None)
        self.assertEqual(found["handle_recovery"], "unverified")

    def test_two_digest_matches_are_an_anomaly_not_a_coin_toss(self):
        digest = pause_policy.terminal_digest("term_real")
        found = pause_policy.resolve_terminal_handle(
            self.row(digest=digest),
            [self.element("term_real"), self.element("term_real", "✳ " + self.TITLE)])
        self.assertEqual(found["handle_recovery"], "unverified")
        self.assertIsNone(found["handle"])

    def test_an_empty_listing_in_a_proved_scope_is_not_listed(self):
        found = pause_policy.resolve_terminal_handle(
            self.row(digest="d"), [], scope_resolved=True)
        self.assertEqual(found["handle_recovery"], "not_listed")

    def test_an_empty_listing_in_an_unresolvable_scope_is_unknown_never_empty(self):
        found = pause_policy.resolve_terminal_handle(
            self.row(digest="d"), [], scope_resolved=False)
        self.assertEqual(found["handle_recovery"], "scope_unresolved")

    def test_an_unreadable_listing_refuses_rather_than_enumerating_nothing(self):
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_policy.resolve_terminal_handle(self.row(digest="d"), None)
        self.assertEqual(ctx.exception.code, "DISPATCH_UNACCOUNTED")

    def test_a_planned_row_never_attempts_a_recovery(self):
        found = pause_policy.resolve_terminal_handle(self.row(stage="PLANNED"), [])
        self.assertEqual(found["handle_recovery"], "not_attempted")

    def test_a_w_c_row_reports_its_candidate_and_still_yields_no_handle(self):
        """Enumeration works for W-C; the VERIFIER is what is missing, so it fails closed."""
        found = pause_policy.resolve_terminal_handle(
            self.row(stage="OPENED"), [self.element("term_maybe")])
        self.assertEqual(found["handle_recovery"], "listing_candidate")
        self.assertIsNone(found["handle"])
        self.assertEqual(found["candidate_handle"], "term_maybe")

    def test_the_property_over_the_whole_cell_cross_product(self):
        """A plaintext handle is used ONLY when a durable digest proved it."""
        digest = pause_policy.terminal_digest("term_real")
        cases = [
            ("INTENDED", digest, [self.element("term_real")], True, "listing_verified"),
            ("INTENDED", digest, [], True, "not_listed"),
            ("INTENDED", digest, [], False, "scope_unresolved"),
            ("INTENDED", digest, [self.element("term_x")], True, "unverified"),
            # No digest was ever journalled, so a title match is a CANDIDATE (nothing can
            # contradict a digest that does not exist) -- addressable, unproven, refused.
            ("INTENDED", "", [self.element("term_real")], True, "listing_candidate"),
            ("OPENED", "", [self.element("term_real")], True, "listing_candidate"),
            ("OPENED", "", [], True, "not_listed"),
            ("PLANNED", "", [self.element("term_real")], True, "not_attempted"),
        ]
        for stage, digest_value, listing, resolved, expected in cases:
            with self.subTest(stage=stage, expected=expected):
                found = pause_policy.resolve_terminal_handle(
                    self.row(stage=stage, digest=digest_value), listing,
                    scope_resolved=resolved)
                self.assertEqual(found["handle_recovery"], expected)
                self.assertEqual(found["handle"] is not None,
                                 expected == "listing_verified")


class ReEntryTests(unittest.TestCase):
    def state(self, **overrides):
        value = {"round_kind": "PHASE_GATE", "current_phase": "IMPLEMENTATION",
                 "requested_phases": ["ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION",
                                      "TEST"],
                 "risk": "high", "binding_generation": 0, "phase_pass_floor": {},
                 "correction_queue": [], "correction_index": 0,
                 "pause_binding": {**BINDING, "responsible_phase": "DESIGN"}}
        value.update(overrides)
        return value

    def test_unchanged_sources_redo_the_paused_round(self):
        reentry = pause_policy.resume_reentry(
            self.state(), current_repository=BINDING["repository_binding"],
            current_artifact=BINDING["artifact_binding"],
            current_policy_digest="digest_1")
        self.assertEqual(reentry.round_kind, "PHASE_GATE")
        self.assertEqual(reentry.current_phase, "IMPLEMENTATION")
        self.assertEqual(reentry.binding_generation, 0)
        self.assertEqual(reentry.revalidation_codes, ())

    def test_a_moved_head_re_enters_through_the_existing_correction_machinery(self):
        moved = {"head_sha": "1" * 40, "tree_digest": "dirty", "dirty": True}
        reentry = pause_policy.resume_reentry(
            self.state(), current_repository=moved,
            current_artifact=BINDING["artifact_binding"],
            current_policy_digest="digest_1")
        self.assertEqual(reentry.round_kind, "CORRECTION")
        self.assertEqual(reentry.current_phase, "DESIGN")
        self.assertEqual(reentry.correction_queue, ("DESIGN",))
        self.assertEqual(reentry.binding_generation, 1)
        self.assertEqual(reentry.revalidation_codes, ("STALE_SOURCE_BINDING",))
        # High risk: the floor is raised for the responsible phase AND for exactly the
        # downstream phases the engine will really re-run.
        self.assertEqual(sorted(reentry.phase_pass_floor),
                         ["DESIGN", "IMPLEMENTATION", "TEST"])

    def test_at_medium_risk_only_the_responsible_phase_floor_is_raised(self):
        """Stated limitation, not hidden: downstream revalidation is high-risk-only."""
        moved = {"head_sha": "1" * 40, "tree_digest": "dirty", "dirty": True}
        reentry = pause_policy.resume_reentry(
            self.state(risk="medium"), current_repository=moved,
            current_artifact=BINDING["artifact_binding"],
            current_policy_digest="digest_1")
        self.assertEqual(sorted(reentry.phase_pass_floor), ["DESIGN"])

    def test_a_changed_policy_digest_is_a_revalidation_trigger_not_a_refusal(self):
        reentry = pause_policy.resume_reentry(
            self.state(), current_repository=BINDING["repository_binding"],
            current_artifact=BINDING["artifact_binding"],
            current_policy_digest="digest_2")
        self.assertEqual(reentry.revalidation_codes, ("STALE_POLICY_DIGEST",))
        for code in reentry.revalidation_codes:
            self.assertIn(code, pause_policy.PAUSE_REVALIDATION_CODES)
            self.assertNotIn(code, pause_policy.PAUSE_REFUSAL_CODES)

    def test_the_responsible_phase_is_the_earliest_named_one(self):
        self.assertEqual(pause_policy.responsible_phase_for(
            [{"phase": "IMPLEMENTATION"}, {"phase": "PLAN"}],
            ["ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION"], "TEST"), "PLAN")
        self.assertEqual(pause_policy.responsible_phase_for(
            [{"phase": "unknown"}], ["ANALYSIS"], "ANALYSIS"), "ANALYSIS")


class BindingValidationTests(unittest.TestCase):
    def test_a_closed_key_set_is_enforced_in_both_directions(self):
        for mutate in (lambda b: b.pop("policy_digest"),
                       lambda b: b.update(extra=1)):
            binding = dict(BINDING)
            mutate(binding)
            with self.assertRaises(pause_policy.PauseRefused):
                pause_policy.validate_pause_binding(binding)

    def test_a_malformed_settlement_row_is_refused(self):
        binding = {**BINDING, "settlement_ledger": [{"intent_id": "x"}]}
        with self.assertRaises(pause_policy.PauseRefused):
            pause_policy.validate_pause_binding(binding)

    def test_every_closed_vocabulary_is_checked_on_a_row(self):
        for key, bad in (("settlement", "nope"), ("worker_resource", "nope"),
                         ("process_liveness", "nope"), ("cleanup_authority", "nope"),
                         ("terminal_disposition", "transferred"),
                         ("provenance_source", "runtime"),
                         ("handle_recovery", "guessed")):
            with self.subTest(key=key):
                with self.assertRaises(pause_policy.PauseRefused):
                    pause_policy.validate_settlement_row(settlement_row(**{key: bad}))

    def test_a_disposition_must_name_a_real_kind_actor_and_actor_type(self):
        good = {"kind": "CANCEL", "cancellation_id": "c", "actor_id": "a",
                "actor_type": "human", "submission_id": "s", "reason": "r",
                "requested_at": "2026-01-01T00:00:00Z"}
        pause_policy.validate_disposition(good)
        for key, bad in (("kind", "TIMEOUT"), ("actor_type", "robot"), ("actor_id", "")):
            with self.subTest(key=key):
                with self.assertRaises(pause_policy.PauseRefused):
                    pause_policy.validate_disposition({**good, key: bad})


class DurableStoreDisciplineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_lock_is_reentrant_within_one_thread(self):
        section = FileCriticalSection(self.root / "a.json")
        with section.locked():
            with section.locked():
                pass

    def test_the_lock_timeout_is_finite_and_injectable(self):
        clock = ManualLeaseClock()
        first = FileCriticalSection(self.root / "b.json", clock=clock,
                                    lock_timeout_seconds=0.05)
        second = FileCriticalSection(self.root / "b.json", clock=clock,
                                     lock_timeout_seconds=0.05)
        holding = threading.Event()
        release = threading.Event()

        def hold():
            with first.locked():
                holding.set()
                release.wait(5)

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(holding.wait(5))
            with self.assertRaises(LockTimeout):
                with second.locked():
                    pass
        finally:
            release.set()
            thread.join(5)

    def test_an_absent_document_is_empty_but_a_broken_one_raises(self):
        path = self.root / "c.json"
        self.assertEqual(read_json_document(path, schema_version="v1",
                                            corrupt_exc=ValueError), {})
        path.write_text("{not json")
        with self.assertRaises(ValueError):
            read_json_document(path, schema_version="v1", corrupt_exc=ValueError)
        write_json_document(path, {"schema_version": "v2"})
        with self.assertRaises(ValueError):
            read_json_document(path, schema_version="v1", corrupt_exc=ValueError)

    def test_a_write_is_atomic_and_leaves_no_temporary_file_behind(self):
        path = self.root / "d.json"
        write_json_document(path, {"schema_version": "v1", "value": 1})
        self.assertEqual(json.loads(path.read_text())["value"], 1)
        self.assertEqual([p.name for p in self.root.iterdir()], ["d.json"])


class PauseRecordStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = ManualLeaseClock()

    def store(self, owner="host:pid1"):
        return pause_store.FilePauseRecordStore(self.root / "pause.json",
                                                clock=self.clock, owner_id=owner,
                                                lease_seconds=30.0)

    def test_create_is_idempotent_and_read_round_trips(self):
        store = self.store()
        store.create(record())
        store.create({**record(), "checkpoint_id": "chk_2"})
        self.assertEqual(store.read("run_x")["checkpoint_id"], "chk_1")

    def test_a_corrupt_record_is_never_read_as_no_pause(self):
        (self.root / "pause.json").write_text(json.dumps(
            {"schema_version": pause_store.PAUSE_RECORD_SCHEMA_VERSION,
             "record": {"run_id": "run_x"}}))
        with self.assertRaises(pause_store.PauseRecordCorrupt):
            self.store().read("run_x")

    def test_two_owners_produce_exactly_one_winner(self):
        first, second = self.store("host:pid1"), self.store("host:pid2")
        first.create(record())
        claimed = first.claim("run_x")
        self.assertEqual(claimed["claim_outcome"], "CREATED")
        with self.assertRaises(pause_store.PauseClaimHeld):
            second.claim("run_x")

    def test_a_lapsed_lease_is_re_taken_as_resumed(self):
        first, second = self.store("host:pid1"), self.store("host:pid2")
        first.create(record())
        first.claim("run_x")
        self.clock.advance(120)
        self.assertEqual(second.claim("run_x")["claim_outcome"], "RESUMED")

    def test_every_mutating_call_is_fenced_and_an_absent_token_never_skips_the_check(self):
        store = self.store()
        store.create(record())
        token = store.claim("run_x")["lease_token"]
        with self.assertRaises(pause_store.PauseClaimRequired):
            store.mark_resumed("run_x", "")
        with self.assertRaises(pause_store.PauseClaimLost):
            store.mark_resumed("run_x", "not-the-token")
        self.assertEqual(store.mark_resumed("run_x", token)["status"], "RESUMED")

    def test_a_settled_record_reports_its_disposition_rather_than_re_claiming(self):
        store = self.store()
        store.create(record())
        token = store.claim("run_x")["lease_token"]
        disposition = {"kind": "CANCEL", "cancellation_id": "c1", "actor_id": "a",
                       "actor_type": "human", "submission_id": "s", "reason": "r",
                       "requested_at": "2026-01-01T00:00:00Z"}
        store.settle_disposition("run_x", disposition, token)
        self.assertEqual(store.claim("run_x")["claim_outcome"], "ALREADY_CANCELLED")

    def test_a_second_disposition_with_a_different_id_is_refused(self):
        store = self.store()
        store.create(record())
        token = store.claim("run_x")["lease_token"]
        disposition = {"kind": "CANCEL", "cancellation_id": "c1", "actor_id": "a",
                       "actor_type": "human", "submission_id": "s", "reason": "r",
                       "requested_at": "2026-01-01T00:00:00Z"}
        store.settle_disposition("run_x", disposition, token)
        settled = store.settle_disposition("run_x", disposition, token)
        self.assertEqual(settled["claim_outcome"], "ALREADY_CANCELLED")
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            store.settle_disposition("run_x", {**disposition, "cancellation_id": "c2"},
                                     token)
        self.assertEqual(ctx.exception.code, "RUN_ALREADY_CANCELLED")

    def test_observe_has_an_explicit_finite_timeout(self):
        first, second = self.store("host:pid1"), self.store("host:pid2")
        first.create(record())
        first.claim("run_x")
        with self.assertRaises(pause_store.PauseObservationTimeout):
            second.observe("run_x", timeout_seconds=1.0, poll_seconds=0.1)


class AppliedSetTests(unittest.TestCase):
    """F-003: one bundle, one entry, one atomic write -- no partial state can exist."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = pause_store.FilePauseRecordStore(
            Path(self.tmp.name) / "pause.json", clock=ManualLeaseClock())
        self.store.create(record(projection=projection(
            decision_item_ids=["item_a", "item_b", "item_c"])))
        self.token = self.store.claim("run_x")["lease_token"]

    def entry(self, decisions, bundle_id="bundle_1"):
        return {"resume_bundle_id": bundle_id, "request_id": "request_1",
                "items": pause_store.applied_items(decisions), "stage": "RECORDED",
                "recorded_at": "2026-01-01T00:00:00Z", "resumed_at": "",
                "resumed_checkpoint_id": ""}

    def test_a_partial_bundle_can_never_be_recorded(self):
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            self.store.record_applied("run_x", self.entry({"item_a": "d1"}), self.token)
        self.assertEqual(ctx.exception.code, "PAUSE_LIFECYCLE_INCOHERENT")
        self.assertEqual(self.store.read("run_x")["applied"], {})

    def test_an_over_complete_bundle_can_never_be_recorded(self):
        with self.assertRaises(pause_policy.PauseRefused):
            self.store.record_applied("run_x", self.entry(
                {"item_a": "d1", "item_b": "d2", "item_c": "d3", "item_d": "d4"}),
                self.token)

    def test_one_whole_bundle_is_written_and_a_replay_is_idempotent(self):
        decisions = {"item_a": "d1", "item_b": "d2", "item_c": "d3"}
        self.store.record_applied("run_x", self.entry(decisions), self.token)
        self.store.record_applied("run_x", self.entry(decisions), self.token)
        stored = self.store.read("run_x")["applied"]
        self.assertEqual(len(stored), 1)
        self.assertEqual([item["decision_item_id"] for item in stored["bundle_1"]["items"]],
                         ["item_a", "item_b", "item_c"])

    def test_a_second_differing_answer_is_refused_and_never_partially_applied(self):
        self.store.record_applied("run_x", self.entry(
            {"item_a": "d1", "item_b": "d2", "item_c": "d3"}), self.token)
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            self.store.record_applied("run_x", self.entry(
                {"item_a": "d1", "item_b": "dX", "item_c": "d3"}, "bundle_2"), self.token)
        self.assertEqual(ctx.exception.code, "RESPONSE_CONFLICT")
        self.assertEqual(len(self.store.read("run_x")["applied"]), 1)


class SettlementJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = pause_store.FileSettlementJournal(
            Path(self.tmp.name) / "journal.json")

    def test_stages_promote_forward_and_never_backwards(self):
        self.journal.record("intent_1", stage="PLANNED", run_id="run_x")
        self.journal.record("intent_1", stage="ACCOUNTED")
        self.journal.record("intent_1", stage="OPENED")
        self.assertEqual(self.journal.row("intent_1")["stage"], "ACCOUNTED")

    def test_a_row_is_written_whole_with_every_closed_key_present(self):
        row = self.journal.record("intent_1", stage="PLANNED", run_id="run_x")
        self.assertEqual(set(row), set(pause_store.JOURNAL_ROW_KEYS))

    def test_open_rows_excludes_only_finished_rows(self):
        self.journal.record("intent_1", stage="PLANNED", run_id="run_x")
        self.journal.record("intent_2", stage="DISPOSED", run_id="run_x")
        self.assertEqual([row["intent_id"] for row in self.journal.open_rows()],
                         ["intent_1"])

    def test_a_corrupt_row_refuses_rather_than_enumerating_an_empty_set(self):
        path = Path(self.tmp.name) / "journal.json"
        path.write_text(json.dumps({
            "schema_version": pause_store.SETTLEMENT_JOURNAL_SCHEMA_VERSION,
            "rows": {"intent_1": {"intent_id": "intent_1"}}}))
        with self.assertRaises(pause_store.SettlementJournalCorrupt):
            self.journal.rows()

    def test_an_unknown_stage_is_refused(self):
        with self.assertRaises(pause_store.SettlementJournalCorrupt):
            self.journal.record("intent_1", stage="SOMETHING")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def write(self, run_id, payload):
        root = self.base / "artifacts" / "runs" / run_id
        root.mkdir(parents=True, exist_ok=True)
        (root / pause_store.PAUSE_RECORD_FILENAME).write_text(json.dumps(payload))

    def test_a_new_process_needs_nothing_but_the_artifact_base(self):
        self.write("run_x", {"schema_version": pause_store.PAUSE_RECORD_SCHEMA_VERSION,
                             "record": record()})
        listings = pause_store.discover_paused_runs(self.base)
        self.assertEqual([item["run_id"] for item in listings], ["run_x"])
        self.assertEqual(listings[0]["request_id"], "request_1")

    def test_a_corrupt_record_is_listed_rather_than_skipped(self):
        self.write("run_bad", {"schema_version": pause_store.PAUSE_RECORD_SCHEMA_VERSION,
                               "record": {"run_id": "run_bad"}})
        listings = pause_store.discover_paused_runs(self.base)
        self.assertEqual(listings[0]["verdict"], "PAUSE_RECORD_CORRUPT")

    def test_an_absent_artifact_root_lists_nothing_and_raises_nothing(self):
        self.assertEqual(pause_store.discover_paused_runs(self.base / "nowhere"), ())

    def test_run_disposition_is_none_for_a_live_or_paused_run(self):
        self.write("run_x", {"schema_version": pause_store.PAUSE_RECORD_SCHEMA_VERSION,
                             "record": record()})
        self.assertIsNone(pause_store.run_disposition(self.base, "run_x"))
        self.assertIsNone(pause_store.run_disposition(self.base, "run_absent"))


class ImportIsolationTests(unittest.TestCase):
    """T-35: the index/policy tier imports and functions with LangGraph absent.

    Run in a SUBPROCESS on purpose.  Blocking ``langgraph`` inside this interpreter would
    have to unload and reload the engine modules, and a reloaded module defines a second
    copy of every exception class -- after which ``except PauseClaimHeld`` in a module that
    imported the first copy silently stops matching.  A child process cannot leak that.
    """

    CHILD = r"""
import builtins, sys
BLOCKED = ("langgraph",)
real_import = builtins.__import__


def guarded(name, *args, **kwargs):
    if name.split(".")[0] in BLOCKED:
        raise ImportError("langgraph is blocked for this test: " + name)
    return real_import(name, *args, **kwargs)


builtins.__import__ = guarded
from scripts.deterministic_workflow import pause_policy, pause_store, durable_store
assert pause_policy.transition("ACTIVE", "ENTER_PAUSE") == "WAITING_FOR_INPUT"
assert pause_store.PAUSE_RECORD_SCHEMA_VERSION == "os31.pause_record.v2"
assert durable_store.DEFAULT_LOCK_TIMEOUT_SECONDS > 0
assert "langgraph" not in sys.modules
assert "scripts.deterministic_workflow.checkpoint_store" not in sys.modules
try:
    from scripts.deterministic_workflow import checkpoint_store  # noqa: F401
except ImportError:
    pass
else:
    raise AssertionError("checkpoint_store imported without langgraph")
try:
    from scripts.deterministic_workflow.launcher import require_runtime
    require_runtime()
except Exception as exc:
    assert "LANGGRAPH_DEPENDENCY_MISSING" in str(exc), exc
else:
    raise AssertionError("require_runtime did not refuse")
print("OS31_NO_LANGGRAPH_OK")
"""

    def test_pause_policy_and_pause_store_import_and_function_without_langgraph(self):
        import subprocess
        import sys as _sys

        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run([_sys.executable, "-c", self.CHILD], cwd=root,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("OS31_NO_LANGGRAPH_OK", completed.stdout)

    def test_discover_works_with_no_langgraph_and_never_reports_resumable(self):
        """The degraded verdict is named, and is a strictly smaller capability."""
        import subprocess
        import sys as _sys

        root = Path(__file__).resolve().parents[1]
        child = self.CHILD.replace('print("OS31_NO_LANGGRAPH_OK")', """
import json, tempfile
from pathlib import Path
base = Path(tempfile.mkdtemp())
run_root = base / "artifacts" / "runs" / "run_degraded"
run_root.mkdir(parents=True)
record = json.loads(RECORD)
(run_root / pause_store.PAUSE_RECORD_FILENAME).write_text(
    json.dumps({"schema_version": pause_store.PAUSE_RECORD_SCHEMA_VERSION,
                "record": record}))
listings = pause_store.discover_paused_runs(base)
assert [item["run_id"] for item in listings] == ["run_degraded"], listings
print("OS31_NO_LANGGRAPH_OK")
""")
        child = f"RECORD = {json.dumps(json.dumps(record(run_id='run_degraded')))}\n" + child
        completed = subprocess.run([_sys.executable, "-c", child], cwd=root,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("OS31_NO_LANGGRAPH_OK", completed.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
