"""External review round 2: M2-001..M2-004.

M2-001  ``update_state``/``aupdate_state`` validate the complete merged checkpoint, from
        every allowed ``as_node``, not just the field names.
M2-002  every iteration domain is an exact integer pair inside ``0..max_iterations``.
M2-003  ``CLAIMED``/``EFFECTED`` crash windows have defined recovery semantics, proven with
        a fresh adapter and a fresh process -- not only by refusing to continue.
M2-004  repository/artifact bindings are advanced by Worker settlements and every review is
        bound to the exact output it approves.

Each guard has a matching ``*_is_load_bearing`` mutation test: disabling the guard must
bring the defect back, so a deleted guard cannot leave this suite green.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import state as state_module
from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES, make_intent,
                                                      make_settlement_event)
from scripts.deterministic_workflow.state import StateError, initial_state, validate_state


def _langgraph_ok() -> bool:
    """The dependency-absent lane blocks the import itself, so the guard must be import-based."""
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


REQUIRES_LANGGRAPH = unittest.skipUnless(_langgraph_ok(), "pinned langgraph runtime is absent")

REPO_ROOT = Path(__file__).resolve().parents[1]
HEAD_A = "a" * 40
HEAD_B = "b" * 40


def base_state(run_id="run_r2", thread_id="t", phases=("ANALYSIS",), **kwargs):
    return dict(initial_state(run_id=run_id, thread_id=thread_id, phases=phases,
                              capabilities=BASE_CAPABILITIES, **kwargs))


def worker_result(head=HEAD_B, digest="tree-b", artifact_root="run_r2", **extra):
    result = {"status": "COMPLETE", "unit_test_status": "PASS",
              "binding": {"repository": {"head_sha": head, "tree_digest": digest,
                                         "dirty": False},
                          "artifact": {"artifact_root_id": artifact_root,
                                       "relative_path": "artifacts/x.md",
                                       "digest": "art-1", "evidence_ids": ["ev-1"]}}}
    result.update(extra)
    return result


REVIEW_PASS = {"result": "PASS", "review_verdict": "PASS", "findings": []}


# =======================================================================================
# M2-002  iteration budget invariants
# =======================================================================================

class BudgetInvariantTests(unittest.TestCase):
    """``consumed + remaining == max`` is not an invariant on its own."""

    def phase_state(self, consumed, remaining, maximum=5):
        state = base_state(max_iterations=maximum)
        state["phase_iterations"]["ANALYSIS"] = consumed
        state["remaining_phase_budget"]["ANALYSIS"] = remaining
        return state

    def final_state(self, consumed, remaining, maximum=5):
        state = base_state(max_iterations=maximum)
        state["final_review_iterations"] = consumed
        state["remaining_final_budget"] = remaining
        return state

    def test_the_reviews_own_counterexample_is_rejected(self):
        with self.assertRaisesRegex(StateError, "phase budget:ANALYSIS consumed range"):
            validate_state(self.phase_state(-100, 105), expected_thread_id="t")

    def test_boolean_iteration_counts_are_rejected_in_every_domain(self):
        for state, label in ((self.phase_state(True, 4), "phase"),
                             (self.final_state(True, 4), "final")):
            with self.subTest(domain=label), self.assertRaisesRegex(StateError, "type"):
                validate_state(state, expected_thread_id="t")

    def test_boundary_values_are_accepted_and_out_of_range_values_are_not(self):
        cases = {(0, 5): True, (5, 0): True, (3, 2): True,
                 (-1, 6): False, (6, -1): False, (2, 2): False, (2, 4): False}
        for (consumed, remaining), valid in cases.items():
            for builder, label in ((self.phase_state, "phase"), (self.final_state, "final")):
                with self.subTest(domain=label, consumed=consumed, remaining=remaining):
                    state = builder(consumed, remaining)
                    if valid:
                        validate_state(state, expected_thread_id="t")
                    else:
                        with self.assertRaises(StateError):
                            validate_state(state, expected_thread_id="t")

    def test_non_integer_iteration_counts_are_rejected(self):
        for bad in (2.0, "2", None, [2]):
            with self.subTest(value=bad), self.assertRaises(StateError):
                validate_state(self.phase_state(bad, 3), expected_thread_id="t")

    def test_a_missing_phase_in_the_remaining_budget_map_is_rejected(self):
        state = base_state(phases=("ANALYSIS", "PLAN"))
        state["remaining_phase_budget"].pop("PLAN")
        with self.assertRaisesRegex(StateError, "phase maps"):
            validate_state(state, expected_thread_id="t")

    def test_a_tampered_budget_cannot_buy_another_dispatch(self):
        """The graph must terminate BLOCKED rather than dispatch on a forged budget."""
        from scripts.deterministic_workflow.executor import validate_node
        out = validate_node(self.phase_state(-100, 105))
        self.assertEqual(out["route_token"], "BLOCK")
        self.assertEqual(out["terminal_reason"]["code"], "MALFORMED_STATE")
        self.assertEqual(out["phase_iterations"]["ANALYSIS"], 0,
                         "the forged counter must not survive normalization")

    def test_budget_invariants_are_load_bearing(self):
        with patch.object(state_module, "_assert_iteration_domain",
                          lambda *args, **kwargs: None):
            validate_state(self.phase_state(-100, 105), expected_thread_id="t")


# =======================================================================================
# M2-001  state-update boundary
# =======================================================================================

def _thread_config(thread_id, limit=200):
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": limit}


@REQUIRES_LANGGRAPH
class StateUpdateBoundaryTests(unittest.TestCase):
    """A known field name is not a licence to write any value into a checkpoint."""

    def build(self, thread_id, results=(), *, phases=("ANALYSIS",)):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        ledger = InMemoryRuntimeStateStore()
        adapter = FakeAdapter(list(results), runtime_state=ledger)
        graph = build_graph(adapter, checkpointer=MemorySaver(), runtime_state=ledger,
                            interrupt_before=["EXECUTE_INTENT"], require_durable_checkpointer=False)
        config = _thread_config(thread_id)
        run_id = "run_" + re.sub(r"[^a-z0-9]", "", thread_id.lower())
        graph.invoke(base_state(run_id=run_id, thread_id=thread_id, phases=phases), config)
        return graph, config

    def injections(self):
        return {
            "invalid decision state": {"decision_state": "TOTALLY_BOGUS"},
            "negative phase budget": {"phase_iterations": {"ANALYSIS": -100},
                                      "remaining_phase_budget": {"ANALYSIS": 105}},
            "boolean phase budget": {"phase_iterations": {"ANALYSIS": True},
                                     "remaining_phase_budget": {"ANALYSIS": 4}},
            "negative final budget": {"final_review_iterations": -3,
                                      "remaining_final_budget": 8},
            "unknown route token": {"route_token": "TELEPORT"},
            "unknown terminal status": {"terminal_status": "WAT"},
            "unknown intent status": {"intent_status": "HALF_WAY"},
            "unknown pending role": {"pending_role": "ARCHITECT"},
            "forged pending intent": {"pending_intent": {"intent_id": "x"},
                                      "intent_status": "PREPARED"},
            "forged pending event": {"pending_event": {"event_id": "x"}},
            "phase index incoherence": {"current_phase_index": 4},
            "post terminal pending role": {"terminal_status": "COMPLETED",
                                           "pending_role": "WORKER"},
            "out of vocabulary round kind": {"round_kind": "COFFEE_BREAK"},
            "queue phase outside vocabulary": {"correction_queue": ["NOT_A_PHASE"]},
        }

    def test_update_state_rejects_invalid_values_for_known_fields(self):
        graph, config = self.build("m2001-sync")
        for label, values in self.injections().items():
            with self.subTest(injection=label):
                with self.assertRaises(StateError):
                    graph.update_state(config, values)
        after = graph.get_state(config).values
        self.assertEqual(after["decision_state"], "CLEAR")
        self.assertEqual(after["phase_iterations"]["ANALYSIS"], 0)

    def test_aupdate_state_rejects_invalid_values_for_known_fields(self):
        graph, config = self.build("m2001-async")
        for label, values in self.injections().items():
            with self.subTest(injection=label):
                with self.assertRaises(StateError):
                    asyncio.run(graph.aupdate_state(config, values))

    def test_every_allowed_as_node_is_validated(self):
        """``as_node`` can resume past VALIDATE, so the boundary validates instead."""
        from scripts.deterministic_workflow.graph import ALLOWED_UPDATE_NODES
        graph, config = self.build("m2001-asnode")
        for node in sorted(ALLOWED_UPDATE_NODES):
            with self.subTest(as_node=node):
                with self.assertRaises(StateError):
                    graph.update_state(config, {"decision_state": "TOTALLY_BOGUS"},
                                       as_node=node)
                with self.assertRaises(StateError):
                    asyncio.run(graph.aupdate_state(
                        config, {"phase_iterations": {"ANALYSIS": -100},
                                 "remaining_phase_budget": {"ANALYSIS": 105}}, as_node=node))
        self.assertEqual(graph.get_state(config).values["decision_state"], "CLEAR")

    def test_an_as_node_outside_the_graph_is_refused(self):
        graph, config = self.build("m2001-badnode")
        with self.assertRaisesRegex(StateError, "unknown as_node"):
            graph.update_state(config, {"decision_state": "CLEAR"}, as_node="NOT_A_NODE")

    def test_unknown_field_names_are_still_refused(self):
        graph, config = self.build("m2001-unknown")
        with self.assertRaisesRegex(StateError, "unknown fields"):
            graph.update_state(config, {"surprise": 1})

    def test_a_non_mapping_update_is_refused(self):
        graph, config = self.build("m2001-nonmapping")
        with self.assertRaisesRegex(StateError, "must be a mapping"):
            graph.update_state(config, ["decision_state"])

    def test_a_valid_update_still_commits(self):
        graph, config = self.build("m2001-valid")
        graph.update_state(config, {"decision_state": "ASSUMPTION_ALLOWED",
                                    "decision_reason_code": "R1"})
        values = graph.get_state(config).values
        self.assertEqual(values["decision_state"], "ASSUMPTION_ALLOWED")
        self.assertEqual(values["decision_reason_code"], "R1")

    def test_typed_update_commands_narrow_the_writable_surface(self):
        from scripts.deterministic_workflow.state import UPDATE_COMMANDS, typed_update
        graph, config = self.build("m2001-typed")
        graph.update_state_command(config, "SET_DECISION", decision_state="CLEAR",
                                   decision_reason_code=None)
        self.assertEqual(graph.get_state(config).values["decision_state"], "CLEAR")
        with self.assertRaisesRegex(StateError, "UNKNOWN_UPDATE_COMMAND"):
            typed_update("DROP_TABLES")
        with self.assertRaisesRegex(StateError, "MALFORMED_UPDATE_COMMAND"):
            typed_update("SET_DECISION", decision_state="CLEAR")
        with self.assertRaisesRegex(StateError, "MALFORMED_UPDATE_COMMAND"):
            typed_update("SET_DECISION", decision_state="NOPE", decision_reason_code=None)
        with self.assertRaisesRegex(StateError, "MALFORMED_UPDATE_COMMAND"):
            typed_update("CLEAR_PENDING", pending_intent={"a": 1}, pending_event=None,
                         intent_status="NONE")
        self.assertNotIn("terminal_status",
                         {field for fields in UPDATE_COMMANDS.values() for field in fields})

    def test_an_update_to_a_thread_that_never_ran_is_refused(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        ledger = InMemoryRuntimeStateStore()
        graph = build_graph(FakeAdapter([], runtime_state=ledger), checkpointer=MemorySaver(),
                            runtime_state=ledger, require_durable_checkpointer=False)
        with self.assertRaisesRegex(StateError, "no checkpoint"):
            graph.update_state(_thread_config("never-ran"), {"decision_state": "CLEAR"})

    def test_merged_state_validation_is_load_bearing(self):
        """Mutation: name-only checking lets every known-field injection through again."""
        from scripts.deterministic_workflow import graph as graph_module
        graph, config = self.build("m2001-mutation")
        with patch.object(graph_module, "validate_state", lambda raw, **kwargs: raw):
            graph.update_state(config, {"decision_state": "TOTALLY_BOGUS"})
        self.assertEqual(graph.get_state(config).values["decision_state"], "TOTALLY_BOGUS")


# =======================================================================================
# M2-003  CLAIMED / EFFECTED recovery
# =======================================================================================

class CrashRecoveryLadderTests(unittest.TestCase):
    """Recovery is lookup -> observe/collect -> re-run only when absence is proven."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def store(self, name="ledger.json", **kwargs):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.root / name, **kwargs)

    def world(self, name="world.json"):
        from scripts.deterministic_workflow.fake_adapter import FileExternalWorld
        return FileExternalWorld(self.root / name)

    def prepared_state(self, run_id="run_recover"):
        state = base_state(run_id=run_id, thread_id="t")
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED")
        return state, intent

    def node(self, adapter, store):
        from scripts.deterministic_workflow.executor import execute_intent_node
        return execute_intent_node(adapter, runtime_state=store)

    def test_claimed_without_lookup_capability_fails_closed_and_creates_nothing(self):
        from scripts.deterministic_workflow.executor import IdempotencyRecoveryError
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        state, intent = self.prepared_state()
        store = self.store()
        store.claim(intent)                       # an earlier process died right here
        adapter = FakeAdapter([worker_result(artifact_root="run_recover")],
                              runtime_state=store)
        with self.assertRaises(IdempotencyRecoveryError) as caught:
            self.node(adapter, store)(deepcopy(state))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_RECOVERY_UNSUPPORTED")
        self.assertEqual(adapter.effect_count, 0)

    def test_claimed_with_a_lookup_proving_absence_reruns_exactly_once(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        state, intent = self.prepared_state()
        store = self.store()
        store.claim(intent)
        world = self.world()                      # the effect was never created
        adapter = FakeAdapter([worker_result(artifact_root="run_recover")],
                              runtime_state=store, external_world=world)
        out = self.node(adapter, store)(deepcopy(state))
        self.assertEqual(adapter.effect_count, 1)
        self.assertEqual(out["pending_event"]["intent_id"], intent["intent_id"])
        self.assertIsNotNone(world.find(intent["intent_id"]))

    def test_a_running_effect_is_observed_not_recreated(self):
        from scripts.deterministic_workflow.executor import IdempotencyRecoveryError
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        state, intent = self.prepared_state()
        store = self.store()
        store.claim(intent)
        world = self.world()
        world.create(intent)                      # created, still running, never settled
        adapter = FakeAdapter([worker_result(artifact_root="run_recover")],
                              runtime_state=store, external_world=world)
        with self.assertRaises(IdempotencyRecoveryError) as caught:
            self.node(adapter, store)(deepcopy(state))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_RECOVERY_BLOCKED")
        self.assertEqual(adapter.effect_count, 0)
        self.assertEqual(store.get_receipt(intent["intent_id"])["status"], "EFFECTED",
                         "the discovered external identity must be recorded durably")

    def test_effected_without_resume_capability_fails_closed(self):
        from scripts.deterministic_workflow.executor import IdempotencyRecoveryError
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        state, intent = self.prepared_state()
        store = self.store()
        claimed = store.claim(intent)
        store.record_receipt(intent["intent_id"], {"task_id": "task_1"}, claimed["lease_token"])
        adapter = FakeAdapter([worker_result(artifact_root="run_recover")],
                              runtime_state=store)
        with self.assertRaises(IdempotencyRecoveryError) as caught:
            self.node(adapter, store)(deepcopy(state))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_RECOVERY_UNSUPPORTED")
        self.assertEqual(adapter.effect_count, 0)

    def test_effected_with_resume_collects_the_existing_settlement(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        state, intent = self.prepared_state()
        store = self.store()
        world = self.world()
        # An earlier process created the effect, it completed, then the process died before
        # recording the settlement.
        claimed = store.claim(intent)
        world.create(intent)
        # Only the durable external identity is recorded: the ledger's receipt contract is
        # closed to identifiers, so the world entry's outcome fields never enter it.
        store.record_receipt(intent["intent_id"], dict(world.find(intent["intent_id"])),
                             claimed["lease_token"])
        result = worker_result(artifact_root="run_recover")
        world.complete(intent["intent_id"], result, "2026-01-01T00:00:01Z")

        fresh_store = self.store()
        fresh_adapter = FakeAdapter([], runtime_state=fresh_store, external_world=self.world())
        out = self.node(fresh_adapter, fresh_store)(deepcopy(state))
        self.assertEqual(fresh_adapter.effect_count, 0, "a settled effect is collected, never re-run")
        self.assertEqual(out["pending_event"]["result"], result)
        self.assertEqual(fresh_store.get_receipt(intent["intent_id"])["status"], "SETTLED")

    def test_an_identity_conflict_on_the_same_intent_is_refused(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.runtime_state import RuntimeStateConflict
        state, intent = self.prepared_state()
        store = self.store()
        store.claim(intent)
        forged = deepcopy(intent)
        forged["payload_digest"] = "0" * 64
        state["pending_intent"] = forged
        adapter = FakeAdapter([], runtime_state=store)
        with self.assertRaisesRegex(RuntimeStateConflict, "IDEMPOTENCY_CONFLICT"):
            self.node(adapter, store)(deepcopy(state))

    @REQUIRES_LANGGRAPH
    def test_a_fresh_process_resumes_the_workflow_to_completion(self):
        """Restart continuation, proven across a real process boundary.

        A child process runs the workflow with a durable ledger and external world and is
        stopped part-way through; this process then finishes the same run with brand new
        adapter, ledger and graph objects and no repeated external effect.
        """
        ledger = self.root / "restart_ledger.json"
        world = self.root / "restart_world.json"
        script = REPO_ROOT / "scripts" / "test_deterministic_workflow_round2.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--child-restart", str(ledger), str(world)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        first = json.loads(completed.stdout)
        self.assertEqual(first["effects"], 1, "the child must have created a real effect")
        self.assertEqual(first["settled"], 1)

        second = _resume_restart_run(str(ledger), str(world))
        self.assertEqual(second["terminal_status"], "COMPLETED",
                         "a brand new process must finish the run, not merely refuse")
        self.assertEqual(second["new_effects"], len(RESTART_RESULTS) - 1,
                         "the successor runs only the work that had not happened yet")
        self.assertEqual(len(second["after"]), len(RESTART_RESULTS))
        for intent_id, event_id in second["before"].items():
            self.assertEqual(second["after"][intent_id], event_id,
                             "an intent settled by the dead process must keep its one "
                             "settlement, not be re-run into a second one")

    def test_the_recovery_ladder_is_load_bearing(self):
        """Mutation: skip the ladder and the crash window recreates the external effect."""
        from scripts.deterministic_workflow import executor as executor_module
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        state, intent = self.prepared_state()
        store = self.store()
        store.claim(intent)
        world = self.world()
        world.create(intent)                      # a real Task exists and is still running
        adapter = FakeAdapter([worker_result(artifact_root="run_recover")],
                              runtime_state=store, external_world=world)
        with patch.object(executor_module, "_recover",
                          lambda adapter, ledger, intent, record, lease_token, keeper=None:
                          executor_module._settle_now(adapter, ledger, intent, lease_token,
                                                      keeper)):
            self.node(adapter, store)(deepcopy(state))
        self.assertEqual(adapter.effect_count, 1,
                         "without the ladder the already-running effect is duplicated")

    def test_the_orca_adapter_declares_only_capabilities_orca_really_has(self):
        from scripts.deterministic_workflow.contracts import EXTERNAL_LOOKUP, EXTERNAL_RESUME
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        capabilities = OrcaAdapter(None).capabilities()
        self.assertIn(EXTERNAL_LOOKUP, capabilities,
                      "task-list --run returns each Task's spec, so intent lookup is real")
        self.assertNotIn(EXTERNAL_RESUME, capabilities,
                         "worker_done is delivered once to the owning process; a settlement "
                         "for a dead process cannot be re-collected, so the capability must "
                         "not be advertised")

    def test_the_orca_lookup_refuses_to_guess_when_existence_is_unknown(self):
        from scripts.deterministic_workflow.contracts import ExternalLookupUnavailable
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        _, intent = self.prepared_state()

        class NoRun:
            run_id = None

        class Unreadable:
            run_id = "run_x"

            def call(self, *args):
                raise OSError("orca is not reachable")

        class SpeclessListing:
            run_id = "run_x"

            def call(self, *args):
                return {"result": {"tasks": [{"id": "task_1", "status": "completed"}]}}

        for harness in (NoRun(), Unreadable(), SpeclessListing()):
            with self.subTest(harness=type(harness).__name__):
                with self.assertRaises(ExternalLookupUnavailable):
                    OrcaAdapter(harness).lookup(intent)

    def test_the_orca_lookup_finds_a_task_by_stable_intent_identity(self):
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        _, intent = self.prepared_state()

        class Listing:
            run_id = "run_x"

            def call(self, *args):
                return {"result": {"tasks": [
                    {"id": "task_other", "spec": json.dumps({"intent_id": "intent_other"})},
                    {"id": "task_mine", "spec": json.dumps(intent, sort_keys=True,
                                                           separators=(",", ":"))}]}}

        adapter = OrcaAdapter(Listing())
        self.assertEqual(adapter.lookup(intent)["task_id"], "task_mine")

    def test_the_orca_lookup_proves_absence_when_no_task_carries_the_intent(self):
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        _, intent = self.prepared_state()

        class Listing:
            run_id = "run_x"

            def call(self, *args):
                return {"result": {"tasks": [
                    {"id": "task_other", "spec": json.dumps({"intent_id": "intent_other"})}]}}

        self.assertIsNone(OrcaAdapter(Listing()).lookup(intent))

    def test_the_orca_lookup_matches_the_parsed_intent_id_not_a_substring(self):
        """A foreign spec that merely quotes this intent's id is not this intent's Task."""
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        _, intent = self.prepared_state()

        class Listing:
            run_id = "run_x"

            def call(self, *args):
                return {"result": {"tasks": [
                    # A different intent whose payload happens to quote ours -- e.g. a
                    # reviewer brief naming the worker intent it reviews.
                    {"id": "task_mentions_us",
                     "spec": json.dumps({"intent_id": "intent_other",
                                         "reviews": intent["intent_id"]})},
                    # Not JSON at all, and not an object: neither belongs to any intent.
                    {"id": "task_opaque", "spec": "not json " + intent["intent_id"]},
                    {"id": "task_scalar", "spec": json.dumps(intent["intent_id"])}]}}

        self.assertIsNone(OrcaAdapter(Listing()).lookup(intent),
                          "a quoted mention must not be mistaken for this intent's Task")

    @REQUIRES_LANGGRAPH
    def test_the_launcher_projects_a_recovery_refusal_onto_a_blocked_terminal(self):
        from scripts.deterministic_workflow import launcher
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        store = self.store("launcher.json")
        spec = {"run_id": "run_blocked", "thread_id": "t", "phases": ["ANALYSIS"]}
        state = launcher.build_state(spec)
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        store.claim(intent)                       # a crashed predecessor's claim
        adapter = FakeAdapter([worker_result(artifact_root="run_blocked")],
                              runtime_state=store)
        final = launcher.execute_state(state, adapter=adapter, runtime_state=store,
                                       checkpoint_store_path=self.root / "cp.json")
        self.assertEqual(final["terminal_status"], "BLOCKED")
        self.assertEqual(final["terminal_reason"]["code"], "IDEMPOTENCY_RECOVERY_UNSUPPORTED")
        self.assertEqual(adapter.effect_count, 0)


# =======================================================================================
# M2-004  repository / artifact binding
# =======================================================================================

class BindingAdvancementTests(unittest.TestCase):
    """A review must be bound to the exact Worker output it approves."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def ledger(self):
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        return InMemoryRuntimeStateStore()

    def applied(self, state, role, result, round_kind="PHASE_GATE"):
        from scripts.deterministic_workflow.executor import apply_result_node
        intent = make_intent(state, role, round_kind)
        event = make_settlement_event(intent, result, occurred_at="2026-01-01T00:00:01Z")
        working = deepcopy(state)
        working.update(pending_intent=intent, pending_event=event, intent_status="SETTLED",
                       pending_role=role)
        return apply_result_node(working), intent, event

    def test_a_worker_settlement_advances_the_repository_and_artifact_binding(self):
        state = base_state()
        self.assertEqual(state["repository_binding"]["head_sha"], "0" * 40)
        out, _, _ = self.applied(state, "WORKER", worker_result())
        self.assertEqual(out["repository_binding"],
                         {"head_sha": HEAD_B, "tree_digest": "tree-b", "dirty": False})
        self.assertEqual(out["artifact_binding"]["digest"], "art-1")
        self.assertEqual(out["initial_repository_binding"]["head_sha"], "0" * 40,
                         "the initial binding is history and must not be rewritten")

    def test_the_reviewer_intent_carries_the_advanced_binding(self):
        state = base_state()
        out, _, _ = self.applied(state, "WORKER", worker_result())
        reviewer_intent = make_intent(out, "PHASE_REVIEWER", "PHASE_GATE")
        self.assertEqual(reviewer_intent["repository_binding"]["head_sha"], HEAD_B)
        self.assertEqual(reviewer_intent["artifact_binding"]["digest"], "art-1")

    def test_a_reviewer_pass_records_the_binding_it_reviewed(self):
        state = base_state()
        worker_state, _, _ = self.applied(state, "WORKER", worker_result())
        out, intent, event = self.applied(worker_state, "PHASE_REVIEWER", REVIEW_PASS)
        record = out["phase_passes"]["ANALYSIS"]
        self.assertEqual(record["head_sha"], HEAD_B)
        self.assertEqual(record["tree_digest"], "tree-b")
        self.assertEqual(record["artifact_digest"], "art-1")
        self.assertEqual(record["reviewed_binding"]["repository"], out["repository_binding"])
        self.assertEqual(record["gate_intent_id"], intent["intent_id"])

    def test_a_stale_repository_head_makes_a_reviewer_pass_fail_closed(self):
        from scripts.deterministic_workflow.executor import apply_result_node
        state = base_state()
        worker_state, _, _ = self.applied(state, "WORKER", worker_result())
        intent = make_intent(worker_state, "PHASE_REVIEWER", "PHASE_GATE")
        event = make_settlement_event(intent, REVIEW_PASS, occurred_at="2026-01-01T00:00:02Z")
        moved = deepcopy(worker_state)
        moved["repository_binding"] = {"head_sha": HEAD_A, "tree_digest": "tree-c",
                                       "dirty": False}
        moved.update(pending_intent=intent, pending_event=event, intent_status="SETTLED")
        out = apply_result_node(moved)
        self.assertEqual(out["terminal_reason"]["code"], "STALE_REVIEW_BINDING")
        self.assertEqual(out["route_token"], "BLOCK")
        self.assertIsNone(out["phase_passes"]["ANALYSIS"])
        self.assertIsNone(out["reviewer_result"])

    def test_a_stale_artifact_binding_makes_a_reviewer_pass_fail_closed(self):
        from scripts.deterministic_workflow.executor import apply_result_node
        state = base_state()
        worker_state, _, _ = self.applied(state, "WORKER", worker_result())
        intent = make_intent(worker_state, "PHASE_REVIEWER", "PHASE_GATE")
        event = make_settlement_event(intent, REVIEW_PASS, occurred_at="2026-01-01T00:00:02Z")
        moved = deepcopy(worker_state)
        moved["artifact_binding"] = dict(moved["artifact_binding"], digest="art-2")
        moved.update(pending_intent=intent, pending_event=event, intent_status="SETTLED")
        out = apply_result_node(moved)
        self.assertEqual(out["terminal_reason"]["code"], "STALE_REVIEW_BINDING")
        self.assertIsNone(out["phase_passes"]["ANALYSIS"])

    def test_a_malformed_binding_is_rejected_before_it_reaches_state(self):
        from scripts.deterministic_workflow.contracts import EventValidationError, validate_event
        state = base_state()
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        for label, binding in {
            "short head": {"repository": {"head_sha": "abc", "tree_digest": "t",
                                          "dirty": False},
                           "artifact": {"artifact_root_id": "run_r2", "relative_path": None,
                                        "digest": None, "evidence_ids": []}},
            "non-bool dirty": {"repository": {"head_sha": HEAD_B, "tree_digest": "t",
                                              "dirty": "yes"},
                               "artifact": {"artifact_root_id": "run_r2",
                                            "relative_path": None, "digest": None,
                                            "evidence_ids": []}},
            "unknown repository key": {"repository": {"head_sha": HEAD_B, "tree_digest": "t",
                                                      "dirty": False, "extra": 1},
                                       "artifact": {"artifact_root_id": "run_r2",
                                                    "relative_path": None, "digest": None,
                                                    "evidence_ids": []}},
            "missing artifact half": {"repository": {"head_sha": HEAD_B, "tree_digest": "t",
                                                     "dirty": False}},
            "bad evidence ids": {"repository": {"head_sha": HEAD_B, "tree_digest": "t",
                                                "dirty": False},
                                 "artifact": {"artifact_root_id": "run_r2",
                                              "relative_path": None, "digest": None,
                                              "evidence_ids": [1]}},
        }.items():
            with self.subTest(binding=label):
                result = {"status": "COMPLETE", "unit_test_status": "PASS", "binding": binding}
                event = make_settlement_event(intent, result, occurred_at="2026-01-01T00:00:01Z")
                with self.assertRaises(EventValidationError):
                    validate_event(intent, event)

    def test_a_binding_scoped_to_another_run_is_rejected(self):
        from scripts.deterministic_workflow.contracts import EventValidationError, validate_event
        state = base_state()
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        result = worker_result(artifact_root="run_someone_else")
        event = make_settlement_event(intent, result, occurred_at="2026-01-01T00:00:01Z")
        with self.assertRaises(EventValidationError):
            validate_event(intent, event)

    def test_only_a_worker_settlement_may_carry_a_binding(self):
        from scripts.deterministic_workflow.contracts import EventValidationError, validate_event
        state = base_state()
        intent = make_intent(state, "PHASE_REVIEWER", "PHASE_GATE")
        result = dict(REVIEW_PASS, binding=worker_result()["binding"])
        event = make_settlement_event(intent, result, occurred_at="2026-01-01T00:00:01Z")
        with self.assertRaises(EventValidationError):
            validate_event(intent, event)

    def test_a_tampered_binding_fails_the_settlement_digest(self):
        from scripts.deterministic_workflow.contracts import EventValidationError, validate_event
        state = base_state()
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        event = make_settlement_event(intent, worker_result(),
                                      occurred_at="2026-01-01T00:00:01Z")
        event["result"]["binding"]["repository"]["head_sha"] = HEAD_A
        with self.assertRaises(EventValidationError) as caught:
            validate_event(intent, event)
        self.assertEqual(caught.exception.code, "SETTLEMENT_INTEGRITY")

    def test_the_final_review_binding_is_verifiable(self):
        from scripts.deterministic_workflow.routing import verify_final_review_binding
        state = base_state()
        worker_state, _, _ = self.applied(state, "WORKER", worker_result())
        reviewed, _, _ = self.applied(worker_state, "PHASE_REVIEWER", REVIEW_PASS)
        reviewed["round_kind"] = "FINAL_REVIEW"
        final, _, _ = self.applied(reviewed, "FINAL_REVIEWER", REVIEW_PASS, "FINAL_REVIEW")
        binding = verify_final_review_binding(final)
        self.assertEqual(binding["repository"]["head_sha"], HEAD_B)
        self.assertEqual(binding["artifact"]["digest"], "art-1")

    def test_a_final_review_pass_against_a_stale_binding_cannot_complete(self):
        from scripts.deterministic_workflow.executor import terminal_node
        from scripts.deterministic_workflow.routing import route, verify_final_review_binding
        state = base_state()
        worker_state, _, _ = self.applied(state, "WORKER", worker_result())
        reviewed, _, _ = self.applied(worker_state, "PHASE_REVIEWER", REVIEW_PASS)
        reviewed["round_kind"] = "FINAL_REVIEW"
        final, _, _ = self.applied(reviewed, "FINAL_REVIEWER", REVIEW_PASS, "FINAL_REVIEW")
        self.assertEqual(route(final), "COMPLETE")

        moved = deepcopy(final)
        moved["repository_binding"] = {"head_sha": HEAD_A, "tree_digest": "tree-z",
                                       "dirty": False}
        self.assertEqual(route(moved), "BLOCK")
        with self.assertRaisesRegex(ValueError, "STALE_FINAL_REVIEW_BINDING"):
            verify_final_review_binding(moved)
        terminal = terminal_node({**moved, "route_token": "BLOCK"})
        self.assertEqual(terminal["terminal_status"], "BLOCKED")
        self.assertEqual(terminal["terminal_reason"]["code"], "STALE_FINAL_REVIEW_BINDING")

    def test_a_forged_final_review_result_without_a_binding_cannot_complete(self):
        from scripts.deterministic_workflow.routing import route
        state = base_state()
        state["round_kind"] = "FINAL_REVIEW"
        state["phase_passes"]["ANALYSIS"] = {"phase": "ANALYSIS"}
        state["final_reviewer_result"] = {"result": "PASS"}
        self.assertEqual(route(state), "BLOCK")

    def test_the_stale_binding_guard_is_load_bearing(self):
        from scripts.deterministic_workflow import executor as executor_module
        state = base_state()
        worker_state, _, _ = self.applied(state, "WORKER", worker_result())
        intent = make_intent(worker_state, "PHASE_REVIEWER", "PHASE_GATE")
        event = make_settlement_event(intent, REVIEW_PASS, occurred_at="2026-01-01T00:00:02Z")
        moved = deepcopy(worker_state)
        moved["repository_binding"] = {"head_sha": HEAD_A, "tree_digest": "tree-c",
                                       "dirty": False}
        moved.update(pending_intent=intent, pending_event=event, intent_status="SETTLED")
        with patch.object(executor_module, "role_binding_is_stale", lambda state, intent: False):
            out = executor_module.apply_result_node(moved)
        self.assertIsNotNone(out["phase_passes"]["ANALYSIS"],
                             "without the guard a stale review is recorded as a pass")

    def test_the_binding_advancement_is_load_bearing(self):
        """Mutation: drop the settlement's binding and the pass records the initial tree."""
        state = base_state()
        out, _, _ = self.applied(state, "WORKER",
                                 {"status": "COMPLETE", "unit_test_status": "PASS"})
        self.assertEqual(out["repository_binding"]["head_sha"], "0" * 40,
                         "a settlement without a binding leaves the default in place -- the "
                         "exact situation the finding describes")

    @REQUIRES_LANGGRAPH
    def test_a_full_run_carries_the_worker_binding_into_every_downstream_review(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.routing import verify_final_review_binding
        ledger = self.ledger()
        results = [worker_result(artifact_root="run_full"), REVIEW_PASS, REVIEW_PASS]
        adapter = FakeAdapter(results, runtime_state=ledger)
        out = build_graph(adapter, runtime_state=ledger, require_durable_checkpointer=False).invoke(
            base_state(run_id="run_full", thread_id="t"), {"recursion_limit": 200})
        self.assertEqual(out["terminal_status"], "COMPLETED")
        self.assertEqual(out["repository_binding"]["head_sha"], HEAD_B)
        self.assertEqual(out["phase_passes"]["ANALYSIS"]["head_sha"], HEAD_B)
        self.assertEqual(verify_final_review_binding(out)["repository"]["head_sha"], HEAD_B)


# =======================================================================================
# child-process helpers for the restart-continuation proof
# =======================================================================================

RESTART_RESULTS = [
    {"status": "COMPLETE", "unit_test_status": "PASS"},
    REVIEW_PASS,
    REVIEW_PASS,
]


def _settled_records(ledger_path):
    raw = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    return {intent_id: record["settlement"]["event_id"]
            for intent_id, record in raw["records"].items() if record["status"] == "SETTLED"}


def _child_restart_run(ledger_path, world_path):
    """Run the first half of a workflow, then exit as if the process had been stopped."""
    from langgraph.checkpoint.memory import MemorySaver
    from scripts.deterministic_workflow.fake_adapter import FakeAdapter, FileExternalWorld
    from scripts.deterministic_workflow.graph import build_graph
    from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
    ledger = FileRuntimeStateStore(ledger_path)
    adapter = FakeAdapter(deepcopy(RESTART_RESULTS), runtime_state=ledger,
                          external_world=FileExternalWorld(world_path))
    graph = build_graph(adapter, checkpointer=MemorySaver(), runtime_state=ledger,
                        interrupt_before=["VALIDATE_SETTLEMENT"], require_durable_checkpointer=False)
    graph.invoke(base_state(run_id="run_restart", thread_id="restart"),
                 _thread_config("restart"))
    print(json.dumps({"effects": adapter.effect_count,
                      "settled": len(_settled_records(ledger_path))}))


def _resume_restart_run(ledger_path, world_path):
    """Finish the same run from scratch: new ledger object, new adapter, new graph.

    The resuming adapter is scripted only with the settlements that have not happened yet,
    which is exactly what a real successor has: it can run the remaining work and nothing
    else.  If it tried to re-run an already-settled intent it would consume the wrong script
    entry and the run would fail, so this is a real check rather than a bookkeeping one.
    """
    from scripts.deterministic_workflow.fake_adapter import FakeAdapter, FileExternalWorld
    from scripts.deterministic_workflow.graph import build_graph
    from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
    before = _settled_records(ledger_path)
    ledger = FileRuntimeStateStore(ledger_path)
    adapter = FakeAdapter(deepcopy(RESTART_RESULTS[len(before):]), runtime_state=ledger,
                          external_world=FileExternalWorld(world_path))
    out = build_graph(adapter, runtime_state=ledger, require_durable_checkpointer=False).invoke(
        base_state(run_id="run_restart", thread_id="restart"), {"recursion_limit": 200})
    after = _settled_records(ledger_path)
    return {"terminal_status": out["terminal_status"], "new_effects": adapter.effect_count,
            "before": before, "after": after}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child-restart":
        sys.path.insert(0, str(REPO_ROOT))
        _child_restart_run(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
