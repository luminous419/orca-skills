"""Pure workflow gates and routing used directly by StateGraph edges."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .contracts import BASE_CAPABILITIES, PHASES, ROUTE_TOKENS


def missing_capabilities(required: frozenset[str], offered: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(required - offered))


def downstream_revalidation_set(corrected: Iterable[str], requested: tuple[str, ...],
                                risk: str = "high") -> tuple[str, ...]:
    if risk != "high": return ()
    indices = [PHASES.index(p) for p in corrected if p in PHASES]
    if not indices: return ()
    return tuple(p for p in PHASES[min(indices) + 1:] if p in requested)


def responsible_phases(findings: Sequence[dict[str, Any]], requested: tuple[str, ...]) -> tuple[str, ...]:
    phases = []
    for finding in findings:
        phase = finding.get("responsible_phase")
        if phase not in requested: raise ValueError("OUT_OF_SCOPE_FINAL_REVIEW_FINDING")
        if finding.get("blocking") is not True: continue
        if phase not in phases: phases.append(phase)
    return tuple(sorted(phases, key=requested.index))


def phase_gate(state: dict[str, Any]) -> str:
    if state["decision_state"] in ("NEEDS_INPUT", "CONFLICT"): return "BLOCK"
    worker = state.get("worker_result")
    if worker is None: return "PENDING"
    if worker.get("status") != "COMPLETE": return "BLOCK"
    if state["current_phase"] in ("IMPLEMENTATION", "BUGFIX", "REFACTORING") and worker.get("unit_test_status") != "PASS":
        return "BLOCK"
    if state["risk"] == "low": return "PASS"
    reviewer = state.get("reviewer_result")
    if reviewer is None: return "PENDING"
    result = reviewer.get("result")
    return result if result in {"PASS", "FAIL"} else "BLOCK"


def final_gate(state: dict[str, Any]) -> str:
    result = state.get("final_reviewer_result")
    if result is None: return "PENDING"
    verdict = result.get("result")
    return verdict if verdict in {"PASS", "FAIL"} else "BLOCK"


def all_phase_passes_current(state: dict[str, Any]) -> bool:
    return all(state["phase_passes"].get(p) is not None for p in state["requested_phases"])


def active_correction_phase(state: dict[str, Any]) -> str | None:
    """Return the indexed correction phase, or None when the queue is consumed/invalid."""
    queue = state.get("correction_queue") or []
    index = state.get("correction_index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(queue):
        return None
    phase = queue[index]
    return phase if phase in state.get("remaining_phase_budget", {}) else None


def route(state: dict[str, Any]) -> str:
    """The sole workflow routing decision, evaluated in strict fail-closed order."""
    if state.get("terminal_status") is not None: return "COMPLETE" if state["terminal_status"] == "COMPLETED" else ("ESCALATE" if state["terminal_status"] == "ESCALATED" else "BLOCK")
    if state["decision_state"] in ("NEEDS_INPUT", "CONFLICT"): return "BLOCK"
    if missing_capabilities(BASE_CAPABILITIES, frozenset(state["adapter_capabilities"])): return "BLOCK"
    kind = state["round_kind"]
    if kind == "FINAL_REVIEW":
        gate = final_gate(state)
        if gate == "PENDING":
            return "ESCALATE" if state["remaining_final_budget"] <= 0 else "PREPARE_FINAL_REVIEWER"
        if gate == "PASS": return "COMPLETE" if all_phase_passes_current(state) else "BLOCK"
        # T2 is deliberately first on the FAIL edge.
        if state["final_review_iterations"] >= state["max_iterations"]: return "ESCALATE"
        if not state["correction_queue"]: return "BLOCK"
        # T4: guard the responsible phase before preparing a correction intent.
        correction_phase = active_correction_phase(state)
        if correction_phase is None: return "BLOCK"
        if state["remaining_phase_budget"][correction_phase] <= 0: return "ESCALATE"
        return "PREPARE_CORRECTION"
    gate = phase_gate(state)
    if gate == "BLOCK": return "BLOCK"
    if gate == "PENDING":
        if state.get("worker_result") is None:
            return "ESCALATE" if state["remaining_phase_budget"][state["current_phase"]] <= 0 else ("PREPARE_CORRECTION" if kind == "CORRECTION" else ("PREPARE_REVALIDATION" if kind == "DOWNSTREAM_REVALIDATION" else "PREPARE_WORKER"))
        return "PREPARE_PHASE_REVIEWER"
    if gate == "FAIL":
        return "ESCALATE" if state["remaining_phase_budget"][state["current_phase"]] <= 0 else "PREPARE_CORRECTION"
    return "ADVANCE_PHASE" if gate == "PASS" else "BLOCK"


def assert_route_token(token: str) -> str:
    if token not in ROUTE_TOKENS: raise ValueError(f"unknown route token: {token}")
    return token
