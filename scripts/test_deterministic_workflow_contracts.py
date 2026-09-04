from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
from scripts.deterministic_workflow.graph_spec import GraphSpec, GraphSpecError, validate_graph_spec
from scripts.deterministic_workflow.routing import (downstream_revalidation_set,
                                                     final_gate, phase_gate, route)
from scripts.deterministic_workflow.state import StateError, initial_state, validate_state


class ContractTests(unittest.TestCase):
    def state(self):
        return initial_state(run_id="run_contract", thread_id="t", phases=("ANALYSIS", "PLAN", "DESIGN"), capabilities=BASE_CAPABILITIES)

    def test_same_state_has_same_route_and_stable_intent(self):
        state = self.state()
        self.assertEqual(route(state), route(copy.deepcopy(state)))
        self.assertEqual(make_intent(state, "WORKER", "PHASE_GATE"), make_intent(copy.deepcopy(state), "WORKER", "PHASE_GATE"))

    def test_non_checkpointable_and_unknown_state_fail_closed(self):
        for mutation in (lambda s: s.update(terminal_handle="x"), lambda s: s.update(extra="x"), lambda s: s.update(max_iterations=True)):
            state = self.state(); mutation(state)
            with self.assertRaises(StateError): validate_state(state, expected_thread_id="t")

    def test_nested_runtime_handles_and_credentials_are_not_checkpointable(self):
        for forbidden_key in ("terminal_handle", "process_handle", "credential"):
            state = self.state()
            state["artifact_binding"][forbidden_key] = "must-not-persist"
            with self.assertRaisesRegex(
                StateError,
                rf"NON_CHECKPOINTABLE_STATE:state\.artifact_binding\.{forbidden_key}",
            ):
                validate_state(state, expected_thread_id="t")

    def test_budget_and_duplicate_identity_validation(self):
        state = self.state(); state["remaining_phase_budget"]["PLAN"] = 99
        with self.assertRaisesRegex(StateError, "phase budget"): validate_state(state, expected_thread_id="t")
        state = self.state(); state["processed_event_ids"] = ["e", "e"]
        with self.assertRaisesRegex(StateError, "duplicate identity"): validate_state(state, expected_thread_id="t")

    def test_decision_precedes_quality(self):
        state = self.state(); state["decision_state"] = "NEEDS_INPUT"; state["quality_verdict"] = "PASS"
        self.assertEqual(route(state), "BLOCK")

    def test_checkpoint_unknown_reviewer_verdicts_fail_closed_at_each_routing_layer(self):
        unknown_values = ("UNKNOWN_VERDICT", "", None, "pass", "APPROVED")
        for value in unknown_values:
            with self.subTest(layer="phase_gate", value=value):
                state = self.state()
                state["worker_result"] = {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}
                state["reviewer_result"] = {"result": value}
                self.assertEqual(phase_gate(state), "BLOCK")
                self.assertEqual(route(state), "BLOCK")
            with self.subTest(layer="final_gate", value=value):
                state = self.state()
                state["round_kind"] = "FINAL_REVIEW"
                state["final_reviewer_result"] = {"result": value}
                self.assertEqual(final_gate(state), "BLOCK")
                self.assertEqual(route(state), "BLOCK")
        state = self.state()
        state["worker_result"] = {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}
        state["reviewer_result"] = {"result": "PASS"}
        with patch("scripts.deterministic_workflow.routing.phase_gate", return_value="UNKNOWN_VERDICT"):
            self.assertEqual(route(state), "BLOCK")

    def test_downstream_is_high_only_canonical_suffix(self):
        requested = ("ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST")
        self.assertEqual(downstream_revalidation_set(("DESIGN",), requested, "high"), ("IMPLEMENTATION", "TEST"))
        self.assertEqual(downstream_revalidation_set(("DESIGN",), requested, "medium"), ())


class GraphSpecTests(unittest.TestCase):
    def test_default_spec_is_valid(self): validate_graph_spec()

    def test_unreachable_and_route_coverage_mutations_are_rejected(self):
        with self.assertRaises(GraphSpecError): validate_graph_spec(GraphSpec(nodes=GraphSpec().nodes + ("DEAD",)))
        with self.assertRaises(GraphSpecError): validate_graph_spec(GraphSpec(route_targets=GraphSpec().route_targets[:-1]))

    def test_unknown_target_terminal_edge_and_guard_mutations_are_rejected(self):
        with self.assertRaises(GraphSpecError): validate_graph_spec(GraphSpec(edges=GraphSpec().edges + (("ROUTE", "MISSING"),)))
        with self.assertRaises(GraphSpecError): validate_graph_spec(GraphSpec(edges=GraphSpec().edges + (("TERMINAL", "ROUTE"),)))
        with self.assertRaises(GraphSpecError): validate_graph_spec(GraphSpec(cycle_guards=frozenset({"phase_budget", "final_budget"})))


if __name__ == "__main__": unittest.main()
