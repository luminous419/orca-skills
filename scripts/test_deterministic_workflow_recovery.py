"""C-001 / M-003 regressions: crash-safe idempotency and settlement integrity.

Every recovery assertion in this module resumes with a **fresh adapter instance**
built on a **file-backed** runtime state store, so nothing is proved by a live
in-process cache.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import io
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


WORKER = {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}
REVIEW_PASS = {"result": "PASS", "review_verdict": "PASS", "findings": []}
REVIEW_FAIL = {"result": "FAIL", "review_verdict": "FAIL",
               "findings": [{"finding_id": "F", "blocking": True, "responsible_phase": "ANALYSIS",
                             "quality_attribute": "G1", "severity": "MAJOR"}]}


class RuntimeStateStoreTests(unittest.TestCase):
    """The persistent store is a checkpoint-safe RuntimeStatePort implementation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "runtime_state.json"

    def store(self):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.path)

    def intent(self, run_id="run_store"):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
        from scripts.deterministic_workflow.state import initial_state
        state = initial_state(run_id=run_id, thread_id="t", phases=("ANALYSIS",),
                              capabilities=BASE_CAPABILITIES)
        return make_intent(state, "WORKER", "PHASE_GATE")

    def test_store_satisfies_the_runtime_state_port(self):
        from scripts.deterministic_workflow.ports import RuntimeStatePort
        self.assertIsInstance(self.store(), RuntimeStatePort)

    def test_claim_is_visible_to_a_separate_store_instance(self):
        intent = self.intent()
        self.store().claim(intent)
        recovered = self.store().get_receipt(intent["intent_id"])
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered["status"], "CLAIMED")
        self.assertEqual(recovered["payload_digest"], intent["payload_digest"])

    def test_persisted_records_hold_no_forbidden_handles(self):
        from scripts.deterministic_workflow.state import FORBIDDEN_KEYS
        intent = self.intent()
        store = self.store()
        claimed = store.claim(intent)
        store.record_receipt(intent["intent_id"], {"task_id": "task_1", "dispatch_id": "ctx_1"},
                             claimed["lease_token"])
        raw = json.loads(self.path.read_text(encoding="utf-8"))

        def walk(node, path="root"):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertIsNone(FORBIDDEN_KEYS.search(key), f"{path}.{key}")
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(raw)

    def test_conflicting_payload_for_the_same_identity_is_rejected(self):
        from scripts.deterministic_workflow.runtime_state import RuntimeStateConflict
        intent = self.intent()
        self.store().claim(intent)
        changed = dict(intent, payload_digest="tampered")
        with self.assertRaises(RuntimeStateConflict):
            self.store().claim(changed)


def _ledger():
    """An explicit process-local ledger.

    These tests run inside one process, so an in-memory port is sufficient -- but it is
    *chosen*, never defaulted: the engine has no port-less mode, because that default is
    what allowed a restart to duplicate an external Task/Dispatch.
    """
    from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
    return InMemoryRuntimeStateStore()


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class CrashWindowIdempotencyTests(unittest.TestCase):
    """C-001: a restart with a brand new adapter must not duplicate an effect."""

    def setUp(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        self.capabilities = BASE_CAPABILITIES
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "runtime_state.json"

    def store(self):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.path)

    def state(self, run_id="run_crash", thread_id="crash", phases=("ANALYSIS",), **kwargs):
        from scripts.deterministic_workflow.state import initial_state
        return initial_state(run_id=run_id, thread_id=thread_id, phases=phases,
                             capabilities=self.capabilities, **kwargs)

    def test_runtime_state_port_is_wired_into_the_graph_execution_path(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        store = self.store()
        adapter = FakeAdapter([WORKER, REVIEW_PASS, REVIEW_PASS], runtime_state=store)
        out = build_graph(adapter, runtime_state=store, require_durable_checkpointer=False).invoke(
            self.state(), config={"recursion_limit": 100})
        self.assertEqual(out["terminal_status"], "COMPLETED")
        self.assertTrue(self.path.exists(), "graph execution must persist runtime state")
        records = json.loads(self.path.read_text(encoding="utf-8"))["records"]
        self.assertEqual(len(records), 3)
        self.assertTrue(all(record["status"] == "SETTLED" for record in records.values()))

    def test_fresh_adapter_recovers_settlement_without_a_second_effect(self):
        """The process dies after settlement is persisted; a new process resumes it."""
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        saver = MemorySaver()
        config = {"configurable": {"thread_id": "crash"}, "recursion_limit": 100}
        results = [WORKER, REVIEW_PASS, REVIEW_PASS]

        first_store = self.store()
        first_adapter = FakeAdapter(deepcopy(results), runtime_state=first_store)
        graph = build_graph(first_adapter, checkpointer=saver, runtime_state=first_store,
                            interrupt_before=["APPLY_RESULT"], require_durable_checkpointer=False)
        graph.invoke(self.state(), config)
        self.assertEqual(first_adapter.effect_count, 1)

        # Process boundary: brand new store instance, brand new adapter instance.  The new
        # process only scripts the work that has not settled yet -- the first intent must be
        # recovered from the durable store, not re-driven through the adapter.
        second_store = self.store()
        second_adapter = FakeAdapter(deepcopy(results[1:]), runtime_state=second_store)
        resumed = build_graph(second_adapter, checkpointer=saver, runtime_state=second_store, require_durable_checkpointer=False)
        out = resumed.invoke(None, config)

        self.assertEqual(out["terminal_status"], "COMPLETED")
        # Only the two intents that were never executed before hit the new adapter.
        self.assertEqual(second_adapter.effect_count, 2)
        self.assertIsNot(first_adapter, second_adapter)

    def test_effect_completed_before_receipt_storage_is_never_recreated(self):
        """The true crash window: external effect made, nothing recorded, then death."""
        from scripts.deterministic_workflow.executor import execute_intent_node
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.contracts import make_intent
        from scripts.deterministic_workflow.state import StateError

        class DyingAdapter(FakeAdapter):
            """Creates the external effect, then dies before anything is recorded."""

            def start(self, intent, *, lease_token=None):
                self.effect_count += 1
                raise KeyboardInterrupt("process killed after external effect")

        state = self.state(run_id="run_window")
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED")

        first_store = self.store()
        dying = DyingAdapter([WORKER], runtime_state=first_store)
        with self.assertRaises(KeyboardInterrupt):
            execute_intent_node(dying, runtime_state=first_store)(deepcopy(state))
        self.assertEqual(dying.effect_count, 1)

        second_store = self.store()
        second_adapter = FakeAdapter([WORKER], runtime_state=second_store)
        with self.assertRaisesRegex(StateError, "IDEMPOTENCY_RECOVERY_UNSUPPORTED"):
            execute_intent_node(second_adapter, runtime_state=second_store)(deepcopy(state))
        self.assertEqual(second_adapter.effect_count, 0, "crash window must not re-create the effect")

    def test_fresh_orca_adapter_does_not_recreate_task_or_dispatch(self):
        from scripts.deterministic_workflow.contracts import make_intent
        from scripts.deterministic_workflow.executor import execute_intent_node
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter

        class OfflineHarness:
            def __init__(self, results):
                self.results = list(results)
                self.calls = []

            def create_task(self, spec, *, deps=()):
                self.calls.append(("create_task", spec))
                return f"task_{len(self.calls)}"

            def run_existing_task(self, role, iteration, mode, task_id, **kwargs):
                self.calls.append(("run_existing_task", task_id))
                return (SimpleNamespace(body=json.dumps(self.results.pop(0)),
                                        dispatch_id=f"ctx_{len(self.calls)}"), "term_offline")

            def task_status(self, task_id): return "completed"
            def call(self, *args, **kwargs): return {"ok": True}

        state = self.state(run_id="run_orcacrash")
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED")

        first_harness = OfflineHarness([WORKER])
        first_store = self.store()
        first = execute_intent_node(OrcaAdapter(first_harness, runtime_state=first_store),
                                    runtime_state=first_store)(deepcopy(state))
        self.assertEqual(sum(1 for call in first_harness.calls if call[0] == "create_task"), 1)

        second_harness = OfflineHarness([WORKER])
        second_store = self.store()
        second = execute_intent_node(OrcaAdapter(second_harness, runtime_state=second_store),
                                     runtime_state=second_store)(deepcopy(state))
        self.assertEqual(second_harness.calls, [], "restart must not create a second Task/Dispatch")
        self.assertEqual(first["pending_event"], second["pending_event"])


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class DefaultPathIdempotencyTests(unittest.TestCase):
    """F-001: the *default* execution contract must be crash-safe, not opt-in.

    Every assertion here uses the public API with no ``runtime_state`` argument, because the
    gap was precisely that the shipped default path skipped the durable claim.
    """

    def setUp(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        self.capabilities = BASE_CAPABILITIES
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def state(self, run_id="run_default", thread_id="t"):
        from scripts.deterministic_workflow.state import initial_state
        return initial_state(run_id=run_id, thread_id=thread_id, phases=("ANALYSIS",),
                             capabilities=self.capabilities)

    def prepared(self, run_id="run_default"):
        from scripts.deterministic_workflow.contracts import make_intent
        state = self.state(run_id)
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED")
        return dict(state), intent

    @staticmethod
    def counting_harness():
        class Harness:
            def __init__(self):
                self.creates = 0
                self.dispatches = 0

            def create_task(self, spec, *, deps=()):
                self.creates += 1
                return f"task_{self.creates}"

            def run_existing_task(self, role, iteration, mode, task_id, **kwargs):
                self.dispatches += 1
                return (SimpleNamespace(body=json.dumps(WORKER),
                                        dispatch_id=f"ctx_{self.dispatches}"), "term_x")

            def task_status(self, task_id): return "completed"
            def call(self, *args, **kwargs): return {}
        return Harness()

    # ---- (a) no durable port anywhere => refuse, before any external effect ----

    def test_default_execute_intent_node_refuses_without_a_durable_port(self):
        from scripts.deterministic_workflow.executor import execute_intent_node
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        from scripts.deterministic_workflow.runtime_state import IdempotencyPortRequired
        state, _ = self.prepared()
        harness = self.counting_harness()
        with self.assertRaises(IdempotencyPortRequired):
            execute_intent_node(OrcaAdapter(harness))(state)
        self.assertEqual(harness.creates, 0, "refusal must precede adapter.start")

    def test_default_build_graph_refuses_without_a_durable_port(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import IdempotencyPortRequired
        adapter = FakeAdapter([WORKER, REVIEW_PASS, REVIEW_PASS])
        with self.assertRaises(IdempotencyPortRequired):
            build_graph(adapter, require_durable_checkpointer=False)
        self.assertEqual(adapter.effect_count, 0)

    def test_default_execute_state_refuses_without_a_durable_port(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.launcher import execute_state
        from scripts.deterministic_workflow.runtime_state import IdempotencyPortRequired
        adapter = FakeAdapter([WORKER, REVIEW_PASS, REVIEW_PASS])
        with self.assertRaises(IdempotencyPortRequired):
            execute_state(self.state(), adapter=adapter)
        self.assertEqual(adapter.effect_count, 0)

    # ---- (b) a port wired into the adapter alone is derived and used ----

    def test_adapter_bound_port_is_derived_without_an_explicit_argument(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        store = FileRuntimeStateStore(self.root / "adapter_bound.json")
        adapter = FakeAdapter([WORKER, REVIEW_PASS, REVIEW_PASS], runtime_state=store)
        out = build_graph(adapter, require_durable_checkpointer=False).invoke(self.state(), config={"recursion_limit": 100})
        self.assertEqual(out["terminal_status"], "COMPLETED")
        self.assertTrue((self.root / "adapter_bound.json").exists())

    def test_two_ports_that_disagree_are_rejected(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.runtime_state import (FileRuntimeStateStore,
                                                                  RuntimeStateConflict)
        adapter = FakeAdapter([], runtime_state=FileRuntimeStateStore(self.root / "a.json"))
        with self.assertRaises(RuntimeStateConflict):
            build_graph(adapter, runtime_state=FileRuntimeStateStore(self.root / "b.json"), require_durable_checkpointer=False)

    # ---- the Final Reviewer's scenario, through the public default path ----

    def test_one_stable_intent_creates_one_external_task_across_fresh_processes(self):
        from scripts.deterministic_workflow.executor import execute_intent_node
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        ledger = self.root / "ledger.json"
        state, intent = self.prepared(run_id="run_freshproc")
        creates = 0
        for _ in range(2):
            # Each pass is a separate "process": fresh harness, fresh adapter, fresh store
            # object over the same durable file, and no explicit runtime_state argument.
            harness = self.counting_harness()
            adapter = OrcaAdapter(harness, runtime_state=FileRuntimeStateStore(ledger))
            execute_intent_node(adapter)(dict(state))
            creates += harness.creates
        self.assertEqual(creates, 1, "one stable intent must create exactly one external Task")

    # ---- the launcher's default must itself be durable ----

    def test_launcher_default_runtime_state_is_a_durable_file(self):
        from scripts.deterministic_workflow import launcher
        with patch.dict(os.environ, {launcher.RUNTIME_STATE_DIR_ENV: str(self.root)}):
            path = launcher.default_runtime_state_path("run_demo", "demo")
        self.assertEqual(path.parent, self.root)
        self.assertTrue(str(path).endswith(".json"))

    def test_cli_without_runtime_state_flag_still_persists_a_ledger(self):
        from scripts.deterministic_workflow import launcher
        # OS-31: the shipped command line is checkpoint-durable by default, so the store
        # is isolated per test rather than written into the working tree.
        with patch.dict(os.environ, {launcher.RUNTIME_STATE_DIR_ENV: str(self.root),
                                     launcher.CHECKPOINT_DIR_ENV: str(self.root)}), \
                contextlib.redirect_stdout(io.StringIO()):
            exit_code = launcher.run_cli(["--demo", "--json"])
            path = launcher.default_runtime_state_path("run_demo", "demo")
        self.assertEqual(exit_code, 0)
        self.assertTrue(path.exists(), "the default run must leave a durable ledger")
        records = json.loads(path.read_text(encoding="utf-8"))["records"]
        self.assertEqual(len(records), 11)
        self.assertTrue(all(record["status"] == "SETTLED" for record in records.values()))

    def test_cli_rerun_recovers_from_the_default_ledger_without_new_effects(self):
        """A second default run over the same ledger repeats no external effect."""
        from scripts.deterministic_workflow import launcher
        with patch.dict(os.environ, {launcher.RUNTIME_STATE_DIR_ENV: str(self.root),
                                     launcher.CHECKPOINT_DIR_ENV: str(self.root)}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.run_cli(["--demo", "--json"]), 0)
            path = launcher.default_runtime_state_path("run_demo", "demo")
            first = json.loads(path.read_text(encoding="utf-8"))["records"]
            self.assertEqual(launcher.run_cli(["--demo", "--json"]), 0)
            second = json.loads(path.read_text(encoding="utf-8"))["records"]
        self.assertEqual(set(first), set(second), "rerun must reuse the same stable intents")


class SettlementIntegrityTests(unittest.TestCase):
    """M-003: identity and digest are recomputed from the canonical payload."""

    def intent(self, role="PHASE_REVIEWER"):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
        from scripts.deterministic_workflow.state import initial_state
        state = initial_state(run_id="run_integrity", thread_id="t", phases=("ANALYSIS",),
                              capabilities=BASE_CAPABILITIES)
        return make_intent(state, role, "PHASE_GATE")

    def event(self, intent, result):
        from scripts.deterministic_workflow.contracts import make_settlement_event
        return make_settlement_event(intent, result, occurred_at="2026-01-01T00:00:00Z")

    def test_canonical_event_validates(self):
        from scripts.deterministic_workflow.contracts import validate_event
        intent = self.intent()
        self.assertIsNotNone(validate_event(intent, self.event(intent, REVIEW_FAIL)))

    def test_fail_to_pass_mutation_is_rejected(self):
        from scripts.deterministic_workflow.contracts import EventValidationError, validate_event
        intent = self.intent()
        event = self.event(intent, REVIEW_FAIL)
        tampered = deepcopy(event)
        tampered["result"]["result"] = "PASS"
        tampered["result"]["review_verdict"] = "PASS"
        tampered["result"]["findings"] = []
        with self.assertRaises(EventValidationError) as caught:
            validate_event(intent, tampered)
        self.assertEqual(caught.exception.code, "SETTLEMENT_INTEGRITY")

    def test_digest_event_id_and_binding_mutations_are_rejected(self):
        from scripts.deterministic_workflow.contracts import EventValidationError, validate_event
        intent = self.intent()
        other = self.intent(role="WORKER")
        event = self.event(intent, REVIEW_PASS)
        mutations = {
            "payload_digest": {"payload_digest": "0" * 64},
            "event_id": {"event_id": "event_" + "0" * 24},
            "intent_binding": {"intent_id": other["intent_id"]},
            "command_binding": {"command_id": other["command_id"]},
        }
        for name, change in mutations.items():
            with self.subTest(mutation=name):
                tampered = dict(deepcopy(event), **change)
                with self.assertRaises(EventValidationError) as caught:
                    validate_event(intent, tampered)
                self.assertEqual(caught.exception.code, "SETTLEMENT_INTEGRITY")

    def test_event_identity_is_reproducible_and_clock_independent(self):
        """Identity must survive a restart that re-derives the settlement on another clock."""
        from scripts.deterministic_workflow.contracts import make_settlement_event
        intent = self.intent()
        early = make_settlement_event(intent, REVIEW_PASS, occurred_at="1970-01-01T00:00:00Z")
        late = make_settlement_event(intent, REVIEW_PASS, occurred_at="2026-09-04T12:00:00Z")
        self.assertEqual(early["event_id"], late["event_id"])
        self.assertEqual(early["payload_digest"], late["payload_digest"])

    def test_malformed_timestamp_is_rejected(self):
        from scripts.deterministic_workflow.contracts import EventValidationError, validate_event
        intent = self.intent()
        for bad in ("", "yesterday", 17, None):
            with self.subTest(occurred_at=bad):
                tampered = dict(deepcopy(self.event(intent, REVIEW_PASS)), occurred_at=bad)
                with self.assertRaises(EventValidationError) as caught:
                    validate_event(intent, tampered)
                self.assertEqual(caught.exception.code, "MALFORMED_EVENT")

    def test_role_bound_payload_differs_across_roles(self):
        from scripts.deterministic_workflow.contracts import settlement_digest
        worker, reviewer = self.intent(role="WORKER"), self.intent(role="PHASE_REVIEWER")
        self.assertNotEqual(settlement_digest(worker, REVIEW_PASS),
                            settlement_digest(reviewer, REVIEW_PASS))


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class CheckpointedSettlementMutationTests(unittest.TestCase):
    """A tampered checkpointed settlement must fail closed before it is applied."""

    def test_checkpointed_fail_to_pass_mutation_blocks_before_apply(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state

        adapter = FakeAdapter([WORKER, REVIEW_FAIL])
        saver = MemorySaver()
        # One ledger for both graph objects: this is a single logical process resuming.
        ledger = _ledger()
        graph = build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                            interrupt_before=["APPLY_RESULT"], require_durable_checkpointer=False)
        config = {"configurable": {"thread_id": "tamper"}, "recursion_limit": 100}
        state = initial_state(run_id="run_tamper", thread_id="tamper", phases=("ANALYSIS",),
                              capabilities=BASE_CAPABILITIES)
        graph.invoke(state, config)      # stops before applying the worker settlement
        graph.invoke(None, config)       # applies worker, stops before reviewer settlement

        pending = graph.get_state(config).values["pending_event"]
        self.assertEqual(pending["result"]["result"], "FAIL")
        tampered = deepcopy(pending)
        tampered["result"]["result"] = "PASS"
        tampered["result"]["review_verdict"] = "PASS"
        tampered["result"]["findings"] = []
        graph.update_state(config, {"pending_event": tampered}, as_node="EXECUTE_INTENT")

        # Resume without the setup interrupt so the run reaches its terminal node.
        out = build_graph(adapter, checkpointer=saver, runtime_state=ledger, require_durable_checkpointer=False).invoke(None, config)
        self.assertEqual(out["terminal_status"], "BLOCKED")
        self.assertEqual(out["terminal_reason"]["code"], "SETTLEMENT_INTEGRITY")
        self.assertIsNone(out["phase_passes"]["ANALYSIS"])
        self.assertNotEqual((out.get("reviewer_result") or {}).get("result"), "PASS")


if __name__ == "__main__":
    unittest.main()
