"""Executable LangGraph StateGraph for OS-40."""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .executor import (advance_phase_node, apply_result_node, execute_intent_node,
                       prepare_intent_node, route_node, terminal_node,
                       validate_node, validate_settlement_node)
from .graph_spec import ROUTE_TARGETS, validate_graph_spec
from .state import WorkflowState


def build_graph(adapter: Any, *, checkpointer: Any = None,
                interrupt_before: list[str] | None = None,
                interrupt_after: list[str] | None = None):
    validate_graph_spec()
    graph = StateGraph(WorkflowState)
    graph.add_node("VALIDATE", validate_node)
    graph.add_node("ROUTE", route_node)
    graph.add_node("ADVANCE_PHASE", advance_phase_node)
    graph.add_node("PREPARE_INTENT", prepare_intent_node)
    graph.add_node("EXECUTE_INTENT", execute_intent_node(adapter))
    graph.add_node("VALIDATE_SETTLEMENT", validate_settlement_node)
    graph.add_node("APPLY_RESULT", apply_result_node)
    graph.add_node("TERMINAL", terminal_node)
    graph.add_edge(START, "VALIDATE")
    graph.add_edge("VALIDATE", "ROUTE")
    graph.add_conditional_edges("ROUTE", lambda state: state["route_token"], ROUTE_TARGETS)
    graph.add_edge("ADVANCE_PHASE", "ROUTE")
    graph.add_edge("PREPARE_INTENT", "EXECUTE_INTENT")
    graph.add_edge("EXECUTE_INTENT", "VALIDATE_SETTLEMENT")
    graph.add_edge("VALIDATE_SETTLEMENT", "APPLY_RESULT")
    graph.add_edge("APPLY_RESULT", "ROUTE")
    graph.add_edge("TERMINAL", END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before,
                         interrupt_after=interrupt_after)
