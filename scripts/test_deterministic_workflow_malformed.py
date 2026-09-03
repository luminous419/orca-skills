"""M-002 regression: malformed graph state must fail closed, never raise."""
from __future__ import annotations

import importlib.metadata
import unittest


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


def _raw_compiled_graph():
    """A raw ``CompiledStateGraph`` the test builds for itself.

    The pin tests must still observe unguarded LangGraph behaviour, but they must not get
    it by unwrapping a guarded instance -- no such path exists any more.  Compiling a
    trivial graph over the same ``WorkflowState`` schema yields the same
    ``CompiledStateGraph`` class, the same public API surface, and the same input
    channel-filtering behaviour, without touching any production entry point.
    """
    from langgraph.graph import END, START, StateGraph
    from scripts.deterministic_workflow.state import WorkflowState
    graph = StateGraph(WorkflowState)
    graph.add_node("PASSTHROUGH", lambda state: state)
    graph.add_edge(START, "PASSTHROUGH")
    graph.add_edge("PASSTHROUGH", END)
    return graph.compile()


def _ledger():
    """An explicit process-local ledger.

    These tests run inside one process, so an in-memory port is sufficient -- but it is
    *chosen*, never defaulted: the engine has no port-less mode, because that default is
    what allowed a restart to duplicate an external Task/Dispatch.
    """
    from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
    return InMemoryRuntimeStateStore()


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class MalformedCompiledGraphEntryTests(unittest.TestCase):
    """Every malformed entry reaches the contracted BLOCKED/MALFORMED_STATE terminal."""

    def setUp(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.state import initial_state
        self.capabilities = BASE_CAPABILITIES
        self.base = initial_state(run_id="run_malformed", thread_id="thread",
                                  phases=("ANALYSIS", "PLAN"), capabilities=BASE_CAPABILITIES)

    def invoke(self, state):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        adapter = FakeAdapter([])
        out = build_graph(adapter, runtime_state=_ledger()).invoke(state, config={"recursion_limit": 50})
        return out, adapter

    def mutated(self, **changes):
        from copy import deepcopy
        state = deepcopy(self.base)
        for key, value in changes.items():
            if value is _DELETE:
                state.pop(key, None)
            else:
                state[key] = value
        return state

    def assert_blocked(self, state):
        out, adapter = self.invoke(state)
        self.assertEqual(out["terminal_status"], "BLOCKED")
        self.assertEqual(out["terminal_reason"]["code"], "MALFORMED_STATE")
        self.assertEqual(adapter.effect_count, 0)
        return out

    def test_missing_required_fields_block_without_keyerror(self):
        for field in ("logical_trace", "current_phase", "phase_iterations",
                      "final_review_iterations", "round_kind", "terminal_status"):
            with self.subTest(missing=field):
                self.assert_blocked(self.mutated(**{field: _DELETE}))

    def test_invalid_field_types_block(self):
        for field, value in (("max_iterations", "5"), ("logical_trace", "not-a-list"),
                             ("phase_iterations", []), ("requested_phases", "ANALYSIS"),
                             ("adapter_capabilities", "agent_start")):
            with self.subTest(field=field):
                self.assert_blocked(self.mutated(**{field: value}))

    def test_incoherent_phase_index_and_budget_combinations_block(self):
        cases = {
            "phase_outside_request": {"current_phase": "DESIGN"},
            "index_out_of_range": {"current_phase_index": 9},
            "index_phase_mismatch": {"current_phase_index": 1},
            "negative_index": {"current_phase_index": -3},
            "budget_mismatch": {"remaining_phase_budget": {"ANALYSIS": 9, "PLAN": 5}},
            "final_budget_mismatch": {"remaining_final_budget": 99},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                self.assert_blocked(self.mutated(**changes))

    def test_unknown_field_is_rejected_at_the_entry_point(self):
        """The launcher entry fails closed too; both boundaries guard independently."""
        from scripts.deterministic_workflow.launcher import execute_state
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        adapter = FakeAdapter([])
        out = execute_state(self.mutated(surprise_field=1), adapter=adapter,
                            runtime_state=_ledger())
        self.assertEqual(out["terminal_status"], "BLOCKED")
        self.assertEqual(out["terminal_reason"]["code"], "MALFORMED_STATE")
        self.assertEqual(adapter.effect_count, 0)

    def test_unknown_only_field_blocks_at_the_compiled_graph_entry(self):
        """F-001: an otherwise-valid state carrying one unknown field must fail closed.

        Every other field here is valid, so nothing but the unknown key can cause the
        block -- this is the case a known-field error would otherwise mask.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        adapter = FakeAdapter([{"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                               {"result": "PASS", "review_verdict": "PASS", "findings": []},
                               {"result": "PASS", "review_verdict": "PASS", "findings": []}])
        out = build_graph(adapter, runtime_state=_ledger()).invoke(self.mutated(surprise_field=1),
                                          config={"recursion_limit": 100})
        self.assertEqual(out["terminal_status"], "BLOCKED")
        self.assertEqual(out["terminal_reason"]["code"], "MALFORMED_STATE")
        self.assertEqual(adapter.effect_count, 0, "no external effect may run for unknown input")
        self.assertNotIn("surprise_field", out)

    def test_unknown_field_matrix_blocks_at_the_compiled_graph_entry(self):
        for name, extra in (("scalar", {"surprise_field": 1}),
                            ("nested", {"nested_unknown": {"a": 1}}),
                            ("shadowing", {"terminal_status_": "COMPLETED"}),
                            ("several", {"one_unknown": 1, "two_unknown": 2})):
            with self.subTest(case=name):
                out, adapter = self.invoke(self.mutated(**extra))
                self.assertEqual(out["terminal_status"], "BLOCKED")
                self.assertEqual(out["terminal_reason"]["code"], "MALFORMED_STATE")
                self.assertEqual(adapter.effect_count, 0)

    def test_unknown_field_is_rejected_by_stream_and_update_state(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import StateError
        adapter = FakeAdapter([])
        graph = build_graph(adapter, checkpointer=MemorySaver(), runtime_state=_ledger())
        config = {"configurable": {"thread_id": "thread"}, "recursion_limit": 50}
        emitted = list(graph.stream(self.mutated(surprise_field=1), config))
        self.assertEqual(emitted[-1]["terminal_status"], "BLOCKED")
        self.assertEqual(emitted[-1]["terminal_reason"]["code"], "MALFORMED_STATE")
        self.assertEqual(adapter.effect_count, 0)
        with self.assertRaisesRegex(StateError, "MALFORMED_STATE:unknown fields"):
            graph.update_state(config, {"surprise_field": 1})

    def test_raw_langgraph_still_drops_unknown_channels(self):
        """Why the guard exists: the underlying StateGraph filters unknown keys itself.

        Pinned against the *unguarded* compiled graph, so the guard's own behaviour cannot
        make this look true when the LangGraph behaviour it compensates for has changed.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        out = _raw_compiled_graph().invoke(self.mutated(surprise_field=1),
                                           config={"recursion_limit": 50})
        self.assertNotIn("surprise_field", out)

    def test_valid_state_is_not_misread_as_malformed(self):
        """The guard must not block the normal path or a checkpoint resume."""
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state
        adapter = FakeAdapter([{"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
                               {"result": "PASS", "review_verdict": "PASS", "findings": []},
                               {"result": "PASS", "review_verdict": "PASS", "findings": []}])
        graph = build_graph(adapter, checkpointer=MemorySaver(), runtime_state=_ledger(),
                            interrupt_before=["APPLY_RESULT"])
        config = {"configurable": {"thread_id": "resume"}, "recursion_limit": 100}
        state = initial_state(run_id="run_guardok", thread_id="resume", phases=("ANALYSIS",),
                              capabilities=self.capabilities)
        graph.invoke(state, config)
        self.assertEqual(adapter.effect_count, 1)
        while graph.get_state(config).next:           # resume path: input is None
            graph.invoke(None, config)
        out = graph.get_state(config).values
        self.assertEqual(out["terminal_status"], "COMPLETED")

    def test_terminal_state_of_malformed_entry_is_itself_valid(self):
        from scripts.deterministic_workflow.state import validate_state
        out = self.assert_blocked(self.mutated(current_phase=_DELETE))
        validate_state(dict(out), expected_thread_id=out["thread_id"])


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class GuardedIngressSurfaceTests(unittest.TestCase):
    """The compiled-graph façade must guard *every* state-ingress API, not a chosen few.

    Guarding a hand-picked list is what let ``batch``/``ainvoke`` through last time, so the
    structural tests here assert the invariant itself: no state-ingress name on the compiled
    graph may be reachable through the façade unless the façade defines its own guard.
    """

    WORKER = {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}
    REVIEW = {"result": "PASS", "review_verdict": "PASS", "findings": []}

    def setUp(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        self.capabilities = BASE_CAPABILITIES

    def valid_state(self, run_id="run_ingress", thread_id="ingress"):
        from scripts.deterministic_workflow.state import initial_state
        return dict(initial_state(run_id=run_id, thread_id=thread_id, phases=("ANALYSIS",),
                                  capabilities=self.capabilities))

    def unknown_state(self):
        return dict(self.valid_state(), surprise_field=1)

    def fresh(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        adapter = FakeAdapter([self.WORKER, self.REVIEW, self.REVIEW])
        return build_graph(adapter, runtime_state=_ledger()), adapter

    CONFIG = {"recursion_limit": 100}

    @staticmethod
    def ingress_names(compiled) -> set[str]:
        """Public callables that accept graph state: ``input``/``inputs``/``values``."""
        import inspect
        found = set()
        for name in dir(compiled):
            if name.startswith("_"):
                continue
            attribute = getattr(compiled, name, None)
            if not callable(attribute):
                continue
            try:
                parameters = set(inspect.signature(attribute).parameters)
            except (TypeError, ValueError):
                continue
            if parameters & {"input", "inputs", "values"}:
                found.add(name)
        return found

    def assert_blocked(self, out, adapter, api):
        self.assertEqual(out["terminal_status"], "BLOCKED", api)
        self.assertEqual(out["terminal_reason"]["code"], "MALFORMED_STATE", api)
        self.assertEqual(adapter.effect_count, 0, f"{api} ran an external effect")

    # ---- unknown-only input must fail closed through every ingress API ----

    def test_sync_ingress_apis_block_unknown_only_input(self):
        import asyncio  # noqa: F401  (kept adjacent to the async twin below)
        graph, adapter = self.fresh()
        self.assert_blocked(graph.invoke(self.unknown_state(), config=self.CONFIG), adapter, "invoke")

        graph, adapter = self.fresh()
        self.assert_blocked(list(graph.stream(self.unknown_state(), config=self.CONFIG))[-1],
                            adapter, "stream")

        graph, adapter = self.fresh()
        self.assert_blocked(graph.batch([self.unknown_state()], config=self.CONFIG)[0],
                            adapter, "batch")

    def test_async_ingress_apis_block_unknown_only_input(self):
        import asyncio
        graph, adapter = self.fresh()
        self.assert_blocked(asyncio.run(graph.ainvoke(self.unknown_state(), config=self.CONFIG)),
                            adapter, "ainvoke")

        graph, adapter = self.fresh()

        async def drain():
            last = None
            async for chunk in graph.astream(self.unknown_state(), config=self.CONFIG):
                last = chunk
            return last

        self.assert_blocked(asyncio.run(drain()), adapter, "astream")

        graph, adapter = self.fresh()
        self.assert_blocked(asyncio.run(graph.abatch([self.unknown_state()], config=self.CONFIG))[0],
                            adapter, "abatch")

    def test_update_state_apis_reject_unknown_fields(self):
        import asyncio
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import StateError
        graph = build_graph(FakeAdapter([]), checkpointer=MemorySaver(), runtime_state=_ledger())
        config = {"configurable": {"thread_id": "ingress"}, "recursion_limit": 50}
        with self.assertRaisesRegex(StateError, "MALFORMED_STATE:unknown fields"):
            graph.update_state(config, {"surprise_field": 1})
        with self.assertRaisesRegex(StateError, "MALFORMED_STATE:unknown fields"):
            asyncio.run(graph.aupdate_state(config, {"surprise_field": 1}))

    # ---- the guard must not over-block ----

    def test_valid_state_still_completes_through_every_ingress_api(self):
        import asyncio
        graph, adapter = self.fresh()
        self.assertEqual(graph.invoke(self.valid_state(), config=self.CONFIG)["terminal_status"],
                         "COMPLETED")
        self.assertEqual(adapter.effect_count, 3)

        graph, adapter = self.fresh()
        self.assertEqual(list(graph.stream(self.valid_state(), config=self.CONFIG))[-1]
                         ["TERMINAL"]["terminal_status"], "COMPLETED")

        graph, adapter = self.fresh()
        self.assertEqual(graph.batch([self.valid_state()], config=self.CONFIG)[0]["terminal_status"],
                         "COMPLETED")

        graph, adapter = self.fresh()
        self.assertEqual(asyncio.run(graph.ainvoke(self.valid_state(), config=self.CONFIG))
                         ["terminal_status"], "COMPLETED")

        graph, adapter = self.fresh()
        self.assertEqual(asyncio.run(graph.abatch([self.valid_state()], config=self.CONFIG))[0]
                         ["terminal_status"], "COMPLETED")

    def test_batch_blocks_only_the_malformed_entries(self):
        graph, adapter = self.fresh()
        results = graph.batch([self.unknown_state(), self.valid_state()], config=self.CONFIG)
        self.assertEqual(results[0]["terminal_status"], "BLOCKED")
        self.assertEqual(results[0]["terminal_reason"]["code"], "MALFORMED_STATE")
        self.assertEqual(results[1]["terminal_status"], "COMPLETED")
        self.assertEqual(adapter.effect_count, 3, "only the valid entry may run effects")

    def test_resume_passes_through_on_sync_and_async_entry(self):
        import asyncio
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        for label, resume in (("sync", lambda g, c: g.invoke(None, c)),
                              ("async", lambda g, c: asyncio.run(g.ainvoke(None, c)))):
            with self.subTest(entry=label):
                adapter = FakeAdapter([self.WORKER, self.REVIEW, self.REVIEW])
                graph = build_graph(adapter, checkpointer=MemorySaver(), runtime_state=_ledger(),
                                    interrupt_before=["APPLY_RESULT"])
                config = {"configurable": {"thread_id": f"resume-{label}"},
                          "recursion_limit": 100}
                graph.invoke(self.valid_state(thread_id=f"resume-{label}"), config)
                while graph.get_state(config).next:
                    resume(graph, config)
                self.assertEqual(graph.get_state(config).values["terminal_status"], "COMPLETED")


    def test_raw_graph_is_not_reachable_from_the_facade(self):
        """No public handle unwraps the guard: ``.compiled`` was one, and is gone."""
        graph, adapter = self.fresh()
        for name in ("compiled", "_compiled", "graph", "pregel", "raw", "unguarded"):
            with self.subTest(attribute=name):
                with self.assertRaises(AttributeError):
                    getattr(graph, name)
        self.assertEqual(adapter.effect_count, 0)

    def test_no_public_member_of_the_facade_yields_an_unguarded_graph(self):
        """The invariant: nothing readable off the façade is a LangGraph runnable."""
        from langgraph.pregel import Pregel
        graph, _ = self.fresh()
        reachable = {name: getattr(graph, name, None) for name in dir(graph)
                     if not name.startswith("__")}
        for name, value in reachable.items():
            with self.subTest(member=name):
                self.assertNotIsInstance(value, Pregel,
                                         f"{name} hands back an unguarded compiled graph")
        self.assertEqual(list(graph.get_subgraphs()), [],
                         "a subgraph would be an unguarded Pregel reachable through the façade")

    def test_the_facade_is_the_only_runnable_reachable_from_itself(self):
        """No public member hands back something that can be invoked around the guard."""
        graph, adapter = self.fresh()
        for name in dir(graph):
            if name.startswith("__"):
                continue
            member = getattr(graph, name, None)
            with self.subTest(member=name):
                self.assertFalse(hasattr(member, "invoke"),
                                 f"{name} exposes an invokable object around the guard")
        self.assert_blocked(graph.invoke(self.unknown_state(), config=self.CONFIG),
                            adapter, "invoke")

    # ---- structural: the invariant, not a hand-written list ----

    def test_no_state_ingress_api_is_reachable_unguarded(self):
        """Deny-by-default: a new LangGraph ingress API cannot silently reappear."""
        from scripts.deterministic_workflow.graph import GUARDED_INGRESS
        graph, _ = self.fresh()
        raw = _raw_compiled_graph()
        for name in sorted(self.ingress_names(raw)):
            with self.subTest(api=name):
                if name in GUARDED_INGRESS:
                    self.assertIsNot(getattr(type(graph), name, None),
                                     getattr(type(raw), name, None),
                                     f"{name} must be the façade's own guarded override")
                    continue
                with self.assertRaises(AttributeError):
                    getattr(graph, name)

    def test_declared_guard_list_matches_the_installed_runtime(self):
        from scripts.deterministic_workflow.graph import GUARDED_INGRESS
        available = self.ingress_names(_raw_compiled_graph())
        self.assertEqual(set(GUARDED_INGRESS) - available, set(),
                         "GUARDED_INGRESS names a method the runtime does not expose")

    def test_readonly_allowlist_contains_no_state_ingress(self):
        from scripts.deterministic_workflow.graph import READ_ONLY_PASSTHROUGH
        graph, _ = self.fresh()
        raw = _raw_compiled_graph()
        self.assertEqual(set(READ_ONLY_PASSTHROUGH) & self.ingress_names(raw), set())
        for name in READ_ONLY_PASSTHROUGH:
            with self.subTest(api=name):
                self.assertTrue(hasattr(raw, name))
                getattr(graph, name)

    def test_composition_apis_that_would_unwrap_the_guard_are_denied(self):
        """``bind``/``pipe``/``with_config``/``validate`` return an unguarded runnable."""
        graph, _ = self.fresh()
        for name in ("bind", "pipe", "map", "assign", "pick", "with_config", "with_retry",
                     "with_fallbacks", "as_tool", "copy", "validate", "builder"):
            with self.subTest(api=name):
                with self.assertRaises(AttributeError):
                    getattr(graph, name)

    def test_read_only_introspection_still_works(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        graph = build_graph(FakeAdapter([]), checkpointer=MemorySaver(), runtime_state=_ledger())
        config = {"configurable": {"thread_id": "introspect"}, "recursion_limit": 50}
        self.assertIsNotNone(graph.get_state(config))
        self.assertIsNotNone(graph.get_graph())


class _Delete:
    def __repr__(self): return "<delete>"


_DELETE = _Delete()

if __name__ == "__main__":
    unittest.main()
