"""Pure workflow gates and routing used directly by StateGraph edges."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .contracts import BASE_CAPABILITIES, PAUSE_CAPABILITIES, PHASES, ROUTE_TOKENS


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


def pause_admissible(state: dict[str, Any]) -> bool:
    """A decision block may become a durable pause only when the adapter can both ask the
    question and settle what is running.

    An adapter that declares neither capability keeps the pre-OS-31 behaviour exactly: the
    decision block routes to BLOCK, as every existing test asserts.
    """
    return (state["decision_state"] in ("NEEDS_INPUT", "CONFLICT")
            and not missing_capabilities(PAUSE_CAPABILITIES,
                                         frozenset(state["adapter_capabilities"])))


def requested_disposition(state: dict[str, Any]) -> str | None:
    """The typed cancel/abandon request carried inside ``pause_binding``, or None."""
    disposition = (state.get("pause_binding") or {}).get("disposition")
    if not isinstance(disposition, dict): return None
    kind = disposition.get("kind")
    return kind if kind in ("CANCEL", "ABANDON") else None


def phase_gate(state: dict[str, Any]) -> str:
    if state["decision_state"] in ("NEEDS_INPUT", "CONFLICT"):
        return "PAUSE" if pause_admissible(state) else "BLOCK"
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
    """Every requested phase has a pass, it names a reviewed binding, and it is current.

    "Current" is expressed as a *floor*, not as equality with the head: a pass is
    legitimately recorded against the tree that existed when the phase passed, and in an
    ordinary forward run the head moves with every Worker settlement, so a naive equality
    check would make COMPLETE unreachable.  The floor states exactly what AC-6 needs -- no
    pass predating a change the engine has not re-run may satisfy completion -- and is a
    no-op for every run that never paused, where the map is empty and every generation is 0.
    """
    floors = state.get("phase_pass_floor") or {}
    for phase in state["requested_phases"]:
        record = (state["phase_passes"] or {}).get(phase)
        if record is None:
            return False
        try:
            phase_pass_binding(state, phase)
        except ValueError:
            return False              # a pass with no reviewed_binding is not a pass
        if record.get("binding_generation", 0) < floors.get(phase, 0):
            return False
    return True


def final_review_binding_current(state: dict[str, Any]) -> bool:
    """True when the recorded Final Review is bound to the state's current head and artifacts.

    A Final Review PASS is only evidence about the tree it actually saw.  Recording which
    binding was reviewed and comparing it here is what makes "the Final Reviewer reviewed the
    final implementation head and artifacts" a checkable fact instead of an assumption.
    """
    reviewed = (state.get("final_reviewer_result") or {}).get("reviewed_binding")
    if not isinstance(reviewed, dict):
        return False
    return (reviewed.get("repository") == state.get("repository_binding")
            and reviewed.get("artifact") == state.get("artifact_binding"))


def verify_final_review_binding(state: dict[str, Any]) -> dict[str, Any]:
    """Return the binding the Final Review approved, or refuse to vouch for the run."""
    result = state.get("final_reviewer_result") or {}
    if result.get("result") != "PASS":
        raise ValueError("NO_FINAL_REVIEW_PASS")
    if not final_review_binding_current(state):
        raise ValueError("STALE_FINAL_REVIEW_BINDING")
    return dict(result["reviewed_binding"])


def phase_pass_binding(state: dict[str, Any], phase: str) -> dict[str, Any]:
    """Return the binding the named phase gate approved, or refuse."""
    record = (state.get("phase_passes") or {}).get(phase)
    if not isinstance(record, dict) or not isinstance(record.get("reviewed_binding"), dict):
        raise ValueError(f"NO_PHASE_PASS_BINDING:{phase}")
    return dict(record["reviewed_binding"])


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
    # OS-31.  Both checks sit ABOVE the decision axis so a disposition can leave a paused
    # run, and re-entering a paused checkpoint re-routes to itself idempotently.
    disposition = requested_disposition(state)
    if disposition is not None: return disposition
    if state.get("run_lifecycle") == "WAITING_FOR_INPUT": return "PAUSE"
    if state["decision_state"] in ("NEEDS_INPUT", "CONFLICT"):
        return "PAUSE" if pause_admissible(state) else "BLOCK"
    if missing_capabilities(BASE_CAPABILITIES, frozenset(state["adapter_capabilities"])): return "BLOCK"
    kind = state["round_kind"]
    if kind == "FINAL_REVIEW":
        gate = final_gate(state)
        if gate == "PENDING":
            return "ESCALATE" if state["remaining_final_budget"] <= 0 else "PREPARE_FINAL_REVIEWER"
        # A PASS completes the run only when every phase gate holds AND the Final Review is
        # demonstrably bound to the current head/artifacts.  A PASS recorded against a stale
        # tree cannot complete the workflow.
        if gate == "PASS":
            return ("COMPLETE" if all_phase_passes_current(state)
                    and final_review_binding_current(state) else "BLOCK")
        # T2 is deliberately first on the FAIL edge.
        if state["final_review_iterations"] >= state["max_iterations"]: return "ESCALATE"
        if not state["correction_queue"]: return "BLOCK"
        # T4: guard the responsible phase before preparing a correction intent.
        correction_phase = active_correction_phase(state)
        if correction_phase is None: return "BLOCK"
        if state["remaining_phase_budget"][correction_phase] <= 0: return "ESCALATE"
        return "PREPARE_CORRECTION"
    gate = phase_gate(state)
    if gate == "PAUSE": return "PAUSE"
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
