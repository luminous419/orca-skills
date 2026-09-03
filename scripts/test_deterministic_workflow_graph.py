from __future__ import annotations

import importlib.metadata
import unittest


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try: return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError: return False


def _ledger():
    """An explicit process-local ledger.

    These tests run inside one process, so an in-memory port is sufficient -- but it is
    *chosen*, never defaulted: the engine has no port-less mode, because that default is
    what allowed a restart to duplicate an external Task/Dispatch.
    """
    from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
    return InMemoryRuntimeStateStore()


@unittest.skipUnless(_langgraph_ok(), "requires pinned langgraph 0.2.76")
class WorkflowGraphTests(unittest.TestCase):
    def setUp(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        self.capabilities = BASE_CAPABILITIES

    def run_graph(self, results, phases=("ANALYSIS",), **kwargs):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state
        adapter = FakeAdapter(results)
        state = initial_state(run_id="run_graph", thread_id="thread", phases=phases,
                              capabilities=self.capabilities, **kwargs)
        return build_graph(adapter, runtime_state=_ledger()).invoke(state, config={"recursion_limit": 300}), adapter

    @staticmethod
    def worker(unit="NOT_APPLICABLE"): return {"status": "COMPLETE", "unit_test_status": unit}
    @staticmethod
    def review(result="PASS", findings=()): return {"result": result, "review_verdict": result, "findings": list(findings)}

    def test_full_happy_path_reaches_completed_through_final_review(self):
        phases = ("ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST")
        results = []
        for p in phases: results += [self.worker("PASS" if p == "IMPLEMENTATION" else "NOT_APPLICABLE"), self.review()]
        results += [self.review()]
        out, adapter = self.run_graph(results, phases)
        self.assertEqual(out["terminal_status"], "COMPLETED"); self.assertEqual(adapter.effect_count, 11)
        self.assertTrue(all(out["phase_passes"].values())); self.assertEqual(out["final_review_iterations"], 1)

    def test_reviewer_fail_routes_same_phase_correction_and_fresh_review(self):
        out, adapter = self.run_graph([self.worker(), self.review("FAIL", ({"finding_id":"F","blocking":True,"responsible_phase":"ANALYSIS","quality_attribute":"G1","severity":"MAJOR"},)), self.worker(), self.review(), self.review()])
        self.assertEqual(out["terminal_status"], "COMPLETED"); self.assertEqual(out["phase_iterations"]["ANALYSIS"], 2)
        ids = [e["intent_id"] for e in out["logical_trace"] if e["node"] == "PREPARE_INTENT"]
        self.assertEqual(len(ids), len(set(ids))); self.assertEqual(adapter.effect_count, 5)

    def test_unknown_phase_reviewer_verdict_blocks_before_next_phase_effect(self):
        out, adapter=self.run_graph(
            [self.worker(),{"result":"UNKNOWN_VERDICT","review_verdict":"UNKNOWN_VERDICT","findings":[]}],
            phases=("ANALYSIS","PLAN"),
        )
        self.assertEqual(out["terminal_status"],"BLOCKED")
        self.assertEqual(out["terminal_reason"]["code"],"UNKNOWN_EVENT")
        self.assertEqual(adapter.effect_count,2)
        self.assertEqual(out["phase_iterations"],{"ANALYSIS":0,"PLAN":0})
        self.assertEqual(out["phase_passes"],{"ANALYSIS":None,"PLAN":None})

    def test_unknown_phase_reviewer_verdict_matrix_stops_compiled_graph_effects(self):
        for verdict in ("UNKNOWN", "", None, "pass"):
            with self.subTest(verdict=verdict):
                out, adapter = self.run_graph(
                    [self.worker(), {"result": verdict, "review_verdict": verdict, "findings": []}],
                    phases=("ANALYSIS", "PLAN"),
                )
                self.assertEqual(out["terminal_status"], "BLOCKED")
                self.assertEqual(out["terminal_reason"]["code"], "UNKNOWN_EVENT")
                self.assertEqual(adapter.effect_count, 2)
                self.assertEqual(out["phase_iterations"], {"ANALYSIS": 0, "PLAN": 0})

    def test_processed_command_cannot_be_prepared_again(self):
        from scripts.deterministic_workflow.contracts import make_intent
        from scripts.deterministic_workflow.executor import prepare_intent_node
        from scripts.deterministic_workflow.state import StateError, initial_state
        state = initial_state(run_id="run_commandreplay", thread_id="t", phases=("ANALYSIS",),
                              capabilities=self.capabilities)
        state["route_token"] = "PREPARE_WORKER"
        command_id = make_intent(state, "WORKER", "PHASE_GATE")["command_id"]
        state["processed_command_ids"].append(command_id)
        with self.assertRaisesRegex(StateError, "OUT_OF_ORDER_EVENT:processed command prepared"):
            prepare_intent_node(state)

    def test_final_decision_and_incomplete_worker_route_fail_closed(self):
        from scripts.deterministic_workflow.routing import route
        from scripts.deterministic_workflow.state import initial_state
        for decision in ("NEEDS_INPUT", "CONFLICT"):
            with self.subTest(decision=decision):
                state = initial_state(run_id=f"run_final{decision.lower().replace('_', '')}", thread_id="t",
                                      phases=("ANALYSIS",), capabilities=self.capabilities)
                state["round_kind"] = "FINAL_REVIEW"
                state["decision_state"] = decision
                state["final_reviewer_result"] = {"result": "PASS"}
                state["phase_passes"]["ANALYSIS"] = {}
                self.assertEqual(route(state), "BLOCK")
        state = initial_state(run_id="run_incompleteworker", thread_id="t", phases=("ANALYSIS",),
                              capabilities=self.capabilities)
        state["worker_result"] = {"status": "BLOCKED", "unit_test_status": "NOT_APPLICABLE"}
        self.assertEqual(route(state), "BLOCK")

    def test_missing_reviewer_result_key_routes_block(self):
        from scripts.deterministic_workflow.routing import phase_gate, route
        from scripts.deterministic_workflow.state import initial_state
        state = initial_state(run_id="run_missingreviewresult", thread_id="t", phases=("ANALYSIS",),
                              capabilities=self.capabilities)
        state["worker_result"] = self.worker()
        state["reviewer_result"] = {"review_verdict": "PASS", "findings": []}
        self.assertEqual(phase_gate(state), "BLOCK")
        self.assertEqual(route(state), "BLOCK")

    def test_phase_budget_exhaustion_escalates_without_new_intent(self):
        out, adapter = self.run_graph([self.worker(), self.review("FAIL", ({"finding_id":"F","blocking":True,"responsible_phase":"ANALYSIS","quality_attribute":"G1","severity":"MAJOR"},))], max_iterations=1)
        self.assertEqual(out["terminal_status"], "ESCALATED"); self.assertEqual(out["terminal_reason"]["code"], "MAX_ITERATIONS_REACHED"); self.assertEqual(adapter.effect_count, 2)

    def test_decision_block_states_override_quality_without_budget_consumption(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state
        for decision in ("NEEDS_INPUT","CONFLICT"):
            adapter=FakeAdapter([]); state=initial_state(run_id=f"run_{decision.lower().replace('_','')}",thread_id="t",phases=("ANALYSIS",),capabilities=self.capabilities)
            state["decision_state"]=decision; state["quality_verdict"]="PASS"; state["pending_clarification_id"]="clarification_x"
            before=(dict(state["phase_iterations"]),state["final_review_iterations"],dict(state["remaining_phase_budget"]))
            out=build_graph(adapter, runtime_state=_ledger()).invoke(state)
            after=(out["phase_iterations"],out["final_review_iterations"],out["remaining_phase_budget"])
            self.assertEqual(out["terminal_status"],"BLOCKED"); self.assertEqual(out["terminal_reason"]["code"],decision)
            self.assertEqual(adapter.effect_count,0); self.assertEqual(after,before)

    def test_phase_pass_does_not_replace_final_pass(self):
        out, adapter = self.run_graph([self.worker(), self.review(), self.review("FAIL")], max_iterations=2)
        self.assertIsNotNone(out["phase_passes"]["ANALYSIS"])
        self.assertNotEqual(out["terminal_status"], "COMPLETED")
        from scripts.deterministic_workflow.routing import route
        state=self.run_graph([self.worker(), self.review(), self.review()], max_iterations=2)[0]
        state["terminal_status"]=None; state["terminal_reason"]=None
        state["phase_passes"]["ANALYSIS"]=None; state["round_kind"]="FINAL_REVIEW"
        self.assertEqual(route(state), "BLOCK")

    def test_final_fail_exhausted_responsible_phase_escalates_before_dispatch(self):
        finding={"finding_id":"F","blocking":True,"responsible_phase":"ANALYSIS","quality_attribute":"G1","severity":"MAJOR"}
        results=[self.worker(),self.review("FAIL",(finding,)),self.worker(),self.review(),self.review("FAIL",(finding,))]
        out, adapter=self.run_graph(results,max_iterations=2)
        self.assertEqual(out["terminal_status"],"ESCALATED")
        self.assertEqual(out["terminal_reason"]["code"],"MAX_ITERATIONS_REACHED")
        self.assertEqual(out["terminal_reason"]["phase"],"ANALYSIS")
        self.assertEqual(out["phase_iterations"]["ANALYSIS"],2)
        self.assertEqual(out["remaining_phase_budget"]["ANALYSIS"],0)
        self.assertEqual(adapter.effect_count,5)

    def test_final_budget_guard_precedes_responsible_phase_mapping(self):
        finding={"finding_id":"F","blocking":True,"responsible_phase":"ANALYSIS","quality_attribute":"G1","severity":"MAJOR"}
        out, adapter=self.run_graph([self.worker(),self.review(),self.review("FAIL",(finding,))],max_iterations=1)
        self.assertEqual(out["terminal_status"],"ESCALATED")
        self.assertEqual(out["terminal_reason"]["code"],"FINAL_REVIEW_MAX_ITERATIONS_REACHED")
        self.assertEqual(adapter.effect_count,3)

    def test_consumed_correction_queue_final_budget_exhaustion_escalates(self):
        finding={"finding_id":"F","blocking":True,"responsible_phase":"ANALYSIS","quality_attribute":"G1","severity":"MAJOR"}
        results=[self.worker(),self.review(),self.review("FAIL",(finding,)),
                 self.worker(),self.review(),self.review("FAIL",(finding,))]
        out, adapter=self.run_graph(results,max_iterations=2)
        self.assertEqual(out["terminal_status"],"ESCALATED")
        self.assertEqual(out["terminal_reason"]["code"],"FINAL_REVIEW_MAX_ITERATIONS_REACHED")
        self.assertEqual(out["terminal_reason"]["phase"],"ANALYSIS")
        self.assertEqual(out["correction_index"],len(out["correction_queue"]))
        self.assertEqual(adapter.effect_count,6)

    def test_consumed_correction_queue_after_revalidation_escalates(self):
        finding={"finding_id":"F","blocking":True,"responsible_phase":"ANALYSIS","quality_attribute":"G1","severity":"MAJOR"}
        results=[self.worker(),self.review(),self.worker(),self.review(),self.review("FAIL",(finding,)),
                 self.worker(),self.review(),self.worker(),self.review(),self.review("FAIL",(finding,))]
        out, adapter=self.run_graph(results,phases=("ANALYSIS","PLAN"),max_iterations=2)
        self.assertEqual(out["terminal_status"],"ESCALATED")
        self.assertEqual(out["terminal_reason"]["code"],"FINAL_REVIEW_MAX_ITERATIONS_REACHED")
        self.assertEqual(out["correction_index"],len(out["correction_queue"]))
        self.assertIn("PLAN",out["revalidation_queue"])
        self.assertEqual(adapter.effect_count,10)

    def test_missing_capability_blocks_without_effect(self):
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state
        capabilities=BASE_CAPABILITIES-frozenset({"agent_interrupt"})
        adapter=FakeAdapter([],capabilities=capabilities)
        state=initial_state(run_id="run_cap",thread_id="t",phases=("ANALYSIS",),capabilities=capabilities)
        out=build_graph(adapter, runtime_state=_ledger()).invoke(state)
        self.assertEqual(out["terminal_status"],"BLOCKED")
        self.assertEqual(out["terminal_reason"]["code"],"ADAPTER_CAPABILITY_MISSING")
        self.assertEqual(adapter.effect_count,0)

    def test_high_final_correction_runs_downstream_revalidation(self):
        finding={"finding_id":"F","blocking":True,"responsible_phase":"ANALYSIS","quality_attribute":"G1","severity":"MAJOR"}
        results=[self.worker(),self.review(),self.worker(),self.review(),self.review("FAIL",(finding,)),
                 self.worker(),self.review(),self.worker(),self.review(),self.review()]
        out, adapter=self.run_graph(results,phases=("ANALYSIS","PLAN"),max_iterations=3)
        rounds=[(e["phase"],e["role"],e["round_kind"]) for e in out["logical_trace"]
                if e["node"]=="PREPARE_INTENT"]
        self.assertIn(("PLAN","WORKER","DOWNSTREAM_REVALIDATION"),rounds)
        self.assertIn(("PLAN","PHASE_REVIEWER","DOWNSTREAM_REVALIDATION"),rounds)
        self.assertEqual(out["terminal_status"],"COMPLETED")
        self.assertEqual(adapter.effect_count,10)

    def test_replayed_and_malformed_events_fail_closed_at_graph_node(self):
        from copy import deepcopy
        from scripts.deterministic_workflow.contracts import make_intent
        from scripts.deterministic_workflow.executor import validate_settlement_node
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.state import StateError, initial_state, validate_state
        state=initial_state(run_id="run_event",thread_id="t",phases=("ANALYSIS",),capabilities=self.capabilities)
        intent=make_intent(state,"WORKER","PHASE_GATE"); adapter=FakeAdapter([self.worker()])
        adapter.start(intent); event=adapter.settlement(intent["intent_id"])
        state.update(pending_intent=intent,pending_event=event,intent_status="SETTLED")
        state["processed_event_ids"].append(event["event_id"])
        replayed=validate_settlement_node(state)
        self.assertIsNone(replayed["pending_event"]); self.assertEqual(replayed["intent_status"],"NONE")
        malformed=deepcopy(state); malformed["processed_event_ids"]=[]
        malformed["pending_event"]["command_id"]="wrong"
        with self.assertRaisesRegex(StateError,"settlement binding"): validate_settlement_node(malformed)
        terminal=initial_state(run_id="run_terminal",thread_id="t",phases=("ANALYSIS",),capabilities=self.capabilities)
        terminal["terminal_status"]="COMPLETED"; terminal["pending_event"]=event
        with self.assertRaisesRegex(StateError,"POST_TERMINAL_EVENT"): validate_state(terminal,expected_thread_id="t")

    def test_compiled_graph_dedupes_replayed_settlement_event(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state
        adapter=FakeAdapter([self.worker(),self.review()]); saver=MemorySaver()
        graph=build_graph(adapter,checkpointer=saver,runtime_state=_ledger(),interrupt_before=["APPLY_RESULT"])
        config={"configurable":{"thread_id":"event-replay"},"recursion_limit":100}
        state=initial_state(run_id="run_eventgraph",thread_id="event-replay",phases=("ANALYSIS",),
                            capabilities=self.capabilities,risk="low")
        graph.invoke(state,config)
        first=graph.get_state(config); intent=first.values["pending_intent"]; event=first.values["pending_event"]
        graph.invoke(None,config)  # apply once, then stop at the next settlement
        self.assertIn(event["event_id"],graph.get_state(config).values["processed_event_ids"])
        graph.update_state(config,{"pending_intent":intent,"pending_event":event,
                                   "intent_status":"SETTLED"},as_node="EXECUTE_INTENT")
        graph.invoke(None,config)
        replay=graph.get_state(config).values
        self.assertIsNone(replay.get("pending_event")); self.assertEqual(replay["intent_status"],"NONE")

    def test_memory_checkpoint_resumes_prepared_intent_without_duplicate(self):
        from langgraph.checkpoint.memory import MemorySaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        from scripts.deterministic_workflow.state import initial_state
        adapter=FakeAdapter([self.worker(),self.review(),self.review()]); saver=MemorySaver()
        graph=build_graph(adapter,checkpointer=saver,runtime_state=_ledger(),interrupt_after=["PREPARE_INTENT"]); config={"configurable":{"thread_id":"resume"},"recursion_limit":100}
        state=initial_state(run_id="run_resume",thread_id="resume",phases=("ANALYSIS",),capabilities=self.capabilities)
        graph.invoke(state,config); self.assertEqual(adapter.effect_count,0); self.assertEqual(graph.get_state(config).next,("EXECUTE_INTENT",))
        graph.invoke(None,config)
        # It advanced through the persisted EXECUTE edge and stopped at the next
        # prepared intent; the first external effect was not duplicated.
        self.assertEqual(adapter.effect_count,1)
        self.assertEqual(graph.get_state(config).next,("EXECUTE_INTENT",))


if __name__ == "__main__": unittest.main()
