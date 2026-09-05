"""Typed checkpoint state construction and fail-closed validation."""
from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from .contracts import (ACTION_SCHEMA_VERSION, ALL_PHASES, BASE_CAPABILITIES, CAPABILITIES,
                        DECISION_STATES, EVENT_SCHEMA_VERSION, RISKS, ROLES, ROUND_KINDS,
                        ROUTE_TOKENS, RUN_LIFECYCLE_STATES, SCHEMA_VERSION,
                        TERMINAL_STATUSES, WORKFLOW_ID, ActionIntent, SettlementEvent)

INTENT_STATUSES = ("NONE", "PREPARED", "SETTLED")


class StateError(ValueError):
    pass


class WorkflowState(TypedDict):
    schema_version: str; run_id: str; thread_id: str; workflow_id: str
    requested_phases: list[str]; risk: str; max_iterations: int
    adapter_capabilities: list[str]; current_phase_index: int; current_phase: str
    round_kind: str; pending_role: str | None
    phase_iterations: dict[str, int]; final_review_iterations: int
    remaining_phase_budget: dict[str, int]; remaining_final_budget: int
    correction_queue: list[str]; correction_index: int; corrected_phases: list[str]
    revalidation_queue: list[str]; revalidation_index: int
    phase_passes: dict[str, dict[str, Any] | None]
    worker_result: dict[str, Any] | None; reviewer_result: dict[str, Any] | None
    final_reviewer_result: dict[str, Any] | None; quality_verdict: str | None
    decision_state: str; decision_reason_code: str | None
    blocking_findings: list[dict[str, Any]]; pending_clarification_id: str | None
    artifact_binding: dict[str, Any]; initial_repository_binding: dict[str, Any]
    repository_binding: dict[str, Any]; route_token: str | None
    pending_intent: dict[str, Any] | None; intent_status: str
    pending_event: dict[str, Any] | None; processed_command_ids: list[str]
    processed_event_ids: list[str]; logical_trace: list[dict[str, Any]]
    terminal_status: str | None; terminal_reason: dict[str, Any] | None
    # ---- OS-31 durable pause and resume ----
    # ``run_lifecycle`` names the one run state ``terminal_status`` cannot express.  Its
    # overlap with ``terminal_status`` elsewhere is a cross-check, not a second authority:
    # a disagreement is a refusal, never a preference (see _assert_lifecycle_coherence).
    run_lifecycle: str; pause_binding: dict[str, Any] | None
    # ``phase_pass_floor`` exists for the phase-pass currency rule alone (AC-6): a pass
    # recorded before a change the engine has not re-run may not satisfy completion.  Both
    # are inert -- ``{}`` and ``0`` -- in every run that never paused.
    binding_generation: int; phase_pass_floor: dict[str, int]


FORBIDDEN_KEYS = re.compile(r"(?:process_handle|terminal_handle|session_handle|credential|access_token|client)", re.I)


def initial_state(*, run_id: str, thread_id: str, phases: tuple[str, ...],
                  capabilities: frozenset[str], risk: str = "high", max_iterations: int = 5,
                  head_sha: str = "0" * 40, tree_digest: str = "clean") -> WorkflowState:
    phase_iterations = {p: 0 for p in phases}
    repo = {"head_sha": head_sha, "tree_digest": tree_digest, "dirty": tree_digest != "clean"}
    state: WorkflowState = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "thread_id": thread_id,
        "workflow_id": WORKFLOW_ID, "requested_phases": list(phases), "risk": risk,
        "max_iterations": max_iterations, "adapter_capabilities": sorted(capabilities),
        "current_phase_index": 0, "current_phase": phases[0], "round_kind": "PHASE_GATE",
        "pending_role": "WORKER", "phase_iterations": phase_iterations,
        "final_review_iterations": 0,
        "remaining_phase_budget": {p: max_iterations for p in phases},
        "remaining_final_budget": max_iterations, "correction_queue": [], "correction_index": 0,
        "corrected_phases": [], "revalidation_queue": [], "revalidation_index": 0,
        "phase_passes": {p: None for p in phases}, "worker_result": None,
        "reviewer_result": None, "final_reviewer_result": None, "quality_verdict": None,
        "decision_state": "CLEAR", "decision_reason_code": None, "blocking_findings": [],
        "pending_clarification_id": None,
        "artifact_binding": {"artifact_root_id": run_id, "relative_path": None, "digest": None,
                             "evidence_ids": []},
        "initial_repository_binding": dict(repo), "repository_binding": dict(repo),
        "route_token": None, "pending_intent": None, "intent_status": "NONE",
        "pending_event": None, "processed_command_ids": [], "processed_event_ids": [],
        "logical_trace": [], "terminal_status": None, "terminal_reason": None,
        "run_lifecycle": "ACTIVE", "pause_binding": None,
        "binding_generation": 0, "phase_pass_floor": {},
    }
    return validate_state(state, expected_thread_id=thread_id)


def _checkpointable(value: Any, path: str = "state") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or FORBIDDEN_KEYS.search(key):
                raise StateError(f"NON_CHECKPOINTABLE_STATE:{path}.{key}")
            _checkpointable(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value): _checkpointable(child, f"{path}[{index}]")
    elif value is not None and type(value) not in (bool, int, str):
        raise StateError(f"NON_CHECKPOINTABLE_STATE:{path}")


def _assert_iteration_domain(label: str, consumed: Any, remaining: Any, maximum: int) -> None:
    """Every iteration domain is an exact integer pair inside ``0..max_iterations``.

    The equality ``consumed + remaining == max`` alone is not an invariant: it is satisfied
    by ``(-100, 105)`` for a maximum of 5, which grants 105 further attempts.  ``bool`` is
    rejected explicitly because ``isinstance(True, int)`` is True and ``True + 4 == 5``, so
    a boolean sails through both the type check and the sum.
    """
    for name, value in (("consumed", consumed), ("remaining", remaining)):
        if type(value) is not int:
            raise StateError(f"MALFORMED_STATE:{label} {name} type")
        if not 0 <= value <= maximum:
            raise StateError(f"MALFORMED_STATE:{label} {name} range")
    if consumed + remaining != maximum:
        raise StateError(f"MALFORMED_STATE:{label} sum")


_OPTIONAL_STR_FIELDS = ("decision_reason_code", "quality_verdict", "pending_clarification_id")
_OPTIONAL_DICT_FIELDS = ("worker_result", "reviewer_result", "final_reviewer_result",
                         "terminal_reason")


def _assert_value_domains(raw: dict[str, Any]) -> None:
    """Check the *values* of every remaining known field, not just their names.

    ``update_state`` merges caller-supplied values straight into a checkpoint, so a field
    whose name is known but whose value is nonsense -- an invented decision state, a bogus
    terminal status, a hand-built ``pending_intent`` -- would otherwise be committed and
    then read back by the routing code as if the graph had produced it.
    """
    if raw["route_token"] is not None and raw["route_token"] not in ROUTE_TOKENS:
        raise StateError("MALFORMED_STATE:route token")
    if raw["terminal_status"] is not None and raw["terminal_status"] not in TERMINAL_STATUSES:
        raise StateError("MALFORMED_STATE:terminal status")
    if raw["intent_status"] not in INTENT_STATUSES:
        raise StateError("MALFORMED_STATE:intent status")
    if raw["run_lifecycle"] not in RUN_LIFECYCLE_STATES:
        raise StateError("MALFORMED_STATE:run lifecycle")
    if raw["pending_role"] is not None and raw["pending_role"] not in ROLES:
        raise StateError("MALFORMED_STATE:pending role")
    for key in _OPTIONAL_STR_FIELDS:
        if raw[key] is not None and type(raw[key]) is not str:
            raise StateError(f"MALFORMED_STATE:{key} type")
    for key in _OPTIONAL_DICT_FIELDS:
        if raw[key] is not None and type(raw[key]) is not dict:
            raise StateError(f"MALFORMED_STATE:{key} type")
    for phase, entry in raw["phase_passes"].items():
        if entry is not None and type(entry) is not dict:
            raise StateError(f"MALFORMED_STATE:phase pass {phase} type")
    for finding in raw["blocking_findings"]:
        if type(finding) is not dict:
            raise StateError("MALFORMED_STATE:blocking finding type")
    for key in ("processed_command_ids", "processed_event_ids", "requested_phases",
                "correction_queue", "corrected_phases", "revalidation_queue",
                "adapter_capabilities"):
        if any(type(item) is not str for item in raw[key]):
            raise StateError(f"MALFORMED_STATE:{key} member type")
    for key, queue in (("correction_index", "correction_queue"),
                       ("revalidation_index", "revalidation_queue")):
        if not 0 <= raw[key] <= len(raw[queue]):
            raise StateError(f"MALFORMED_STATE:{key} range")
    if any(p not in ALL_PHASES for p in raw["correction_queue"] + raw["corrected_phases"]
           + raw["revalidation_queue"]):
        raise StateError("MALFORMED_STATE:queue phases")
    _assert_pending_intent(raw)
    _assert_pending_event(raw)
    _assert_lifecycle_coherence(raw)


def _assert_lifecycle_coherence(raw: dict[str, Any]) -> None:
    """One place, fail-closed, for every rule relating lifecycle to terminal status.

    The ``SETTLED <=> terminal_status is not None`` biconditional makes a state that "was
    terminal and now is not" unrepresentable, which is what closes the forged-resume path
    without any code path needing to be careful (OS-31 SS8.1).
    """
    lifecycle = raw["run_lifecycle"]
    terminal = raw["terminal_status"]
    if (lifecycle == "SETTLED") != (terminal is not None):
        raise StateError("MALFORMED_STATE:lifecycle coherence")
    if lifecycle == "WAITING_FOR_INPUT":
        if raw["pause_binding"] is None:
            raise StateError("MALFORMED_STATE:lifecycle coherence")
        if raw["pending_intent"] is not None or raw["pending_event"] is not None:
            raise StateError("MALFORMED_STATE:lifecycle coherence")
        if raw["intent_status"] != "NONE":
            raise StateError("MALFORMED_STATE:lifecycle coherence")
    if lifecycle == "ACTIVE" and raw["pause_binding"] is not None:
        raise StateError("MALFORMED_STATE:lifecycle coherence")
    if raw["pause_binding"] is not None:
        _assert_pause_binding(raw["pause_binding"])
    for phase, floor in raw["phase_pass_floor"].items():
        if phase not in raw["requested_phases"]:
            raise StateError("MALFORMED_STATE:phase pass floor phase")
        if type(floor) is not int or floor < 0:
            raise StateError("MALFORMED_STATE:phase pass floor value")
    if type(raw["binding_generation"]) is not int or raw["binding_generation"] < 0:
        raise StateError("MALFORMED_STATE:binding generation")


def _assert_pause_binding(binding: Any) -> None:
    """Validate the closed pause binding, delegating to the pure policy module.

    Imported lazily so ``state`` keeps the tiny import surface every other module relies
    on; ``pause_policy`` is pure and carries no LangGraph or Orca dependency either way.
    """
    from .pause_policy import PauseRefused, validate_pause_binding

    try:
        validate_pause_binding(binding)
    except PauseRefused as exc:
        raise StateError(f"MALFORMED_STATE:pause binding:{exc.detail}") from exc


def _assert_pending_intent(raw: dict[str, Any]) -> None:
    intent = raw["pending_intent"]
    if intent is None:
        if raw["intent_status"] != "NONE":
            raise StateError("MALFORMED_STATE:intent status without intent")
        return
    if type(intent) is not dict or set(intent) != set(ActionIntent.__required_keys__):
        raise StateError("MALFORMED_STATE:pending intent shape")
    if intent["schema_version"] != ACTION_SCHEMA_VERSION:
        raise StateError("MALFORMED_STATE:pending intent schema")
    if intent["role"] not in ROLES or intent["round_kind"] not in ROUND_KINDS:
        raise StateError("MALFORMED_STATE:pending intent vocabulary")
    if intent["phase"] not in ALL_PHASES or intent["run_id"] != raw["run_id"]:
        raise StateError("MALFORMED_STATE:pending intent binding")
    for key in ("intent_id", "command_id", "payload_digest", "action_kind"):
        if type(intent[key]) is not str or not intent[key]:
            raise StateError(f"MALFORMED_STATE:pending intent {key}")
    for key in ("artifact_binding", "repository_binding"):
        if type(intent[key]) is not dict:
            raise StateError(f"MALFORMED_STATE:pending intent {key}")
    for key in ("phase_iteration", "final_review_iteration"):
        if type(intent[key]) is not int or intent[key] < 0:
            raise StateError(f"MALFORMED_STATE:pending intent {key}")


def _assert_pending_event(raw: dict[str, Any]) -> None:
    event = raw["pending_event"]
    if event is None:
        return
    if type(event) is not dict or set(event) != set(SettlementEvent.__required_keys__):
        raise StateError("MALFORMED_STATE:pending event shape")
    if event["schema_version"] != EVENT_SCHEMA_VERSION or type(event["result"]) is not dict:
        raise StateError("MALFORMED_STATE:pending event schema")
    for key in ("event_id", "intent_id", "command_id", "event_kind", "outcome",
                "occurred_at", "payload_digest"):
        if type(event[key]) is not str or not event[key]:
            raise StateError(f"MALFORMED_STATE:pending event {key}")
    if raw["pending_intent"] is not None and (
            event["intent_id"] != raw["pending_intent"]["intent_id"]
            or event["command_id"] != raw["pending_intent"]["command_id"]):
        raise StateError("MALFORMED_STATE:pending event binding")


def validate_state(raw: dict[str, Any], *, expected_thread_id: str) -> WorkflowState:
    _checkpointable(raw)
    json.dumps(raw, allow_nan=False)
    required = set(WorkflowState.__required_keys__)
    if set(raw) != required: raise StateError("MALFORMED_STATE:closed fields")
    # Container types are checked before anything indexes them, so a wrong-typed field
    # can never reach the trace/routing code as a silent KeyError or TypeError.
    for key in ("requested_phases", "adapter_capabilities", "correction_queue", "corrected_phases",
                "revalidation_queue", "blocking_findings", "processed_command_ids",
                "processed_event_ids", "logical_trace"):
        if type(raw[key]) is not list: raise StateError(f"MALFORMED_STATE:{key} type")
    for key in ("phase_iterations", "remaining_phase_budget", "phase_passes", "artifact_binding",
                "initial_repository_binding", "repository_binding", "phase_pass_floor"):
        if type(raw[key]) is not dict: raise StateError(f"MALFORMED_STATE:{key} type")
    if raw["pause_binding"] is not None and type(raw["pause_binding"]) is not dict:
        raise StateError("MALFORMED_STATE:pause_binding type")
    for key in ("current_phase_index", "final_review_iterations", "remaining_final_budget",
                "correction_index", "revalidation_index", "binding_generation"):
        if type(raw[key]) is not int: raise StateError(f"MALFORMED_STATE:{key} type")
    for key in ("schema_version", "run_id", "thread_id", "workflow_id", "current_phase",
                "round_kind", "risk", "run_lifecycle"):
        if type(raw[key]) is not str: raise StateError(f"MALFORMED_STATE:{key} type")
    if raw["schema_version"] != SCHEMA_VERSION or raw["workflow_id"] != WORKFLOW_ID:
        raise StateError("MALFORMED_STATE:schema")
    if raw["thread_id"] != expected_thread_id: raise StateError("MALFORMED_STATE:thread")
    if not re.fullmatch(r"run_[a-z0-9]+", raw["run_id"]): raise StateError("MALFORMED_STATE:run")
    phases = raw["requested_phases"]
    if not phases or any(p not in ALL_PHASES for p in phases): raise StateError("MALFORMED_STATE:phases")
    if raw["risk"] not in RISKS or type(raw["max_iterations"]) is not int or not 1 <= raw["max_iterations"] <= 10:
        raise StateError("MALFORMED_STATE:parameters")
    if set(raw["adapter_capabilities"]) - CAPABILITIES: raise StateError("MALFORMED_STATE:capabilities")
    if raw["adapter_capabilities"] != sorted(set(raw["adapter_capabilities"])): raise StateError("MALFORMED_STATE:capabilities")
    if set(raw["phase_iterations"]) != set(phases) or set(raw["phase_passes"]) != set(phases):
        raise StateError("MALFORMED_STATE:phase maps")
    maximum = raw["max_iterations"]
    if set(raw["remaining_phase_budget"]) != set(phases):
        raise StateError("MALFORMED_STATE:phase maps")
    for phase in phases:
        _assert_iteration_domain(f"phase budget:{phase}", raw["phase_iterations"][phase],
                                 raw["remaining_phase_budget"][phase], maximum)
    _assert_iteration_domain("final budget", raw["final_review_iterations"],
                             raw["remaining_final_budget"], maximum)
    if raw["decision_state"] not in DECISION_STATES: raise StateError("MALFORMED_STATE:decision")
    if raw["round_kind"] not in ROUND_KINDS: raise StateError("MALFORMED_STATE:round kind")
    _assert_value_domains(raw)
    # Phase/index coherence: every field the trace indexes must resolve.  A CORRECTION or
    # revalidation round legitimately points at a phase other than requested_phases[index],
    # so the exact-match rule applies only to the forward PHASE_GATE path.
    index = raw["current_phase_index"]
    if not 0 <= index < len(phases): raise StateError("MALFORMED_STATE:phase index")
    if raw["current_phase"] not in phases: raise StateError("MALFORMED_STATE:current phase")
    if raw["round_kind"] == "PHASE_GATE" and raw["current_phase"] != phases[index]:
        raise StateError("MALFORMED_STATE:phase index coherence")
    if len(raw["processed_command_ids"]) != len(set(raw["processed_command_ids"])) or len(raw["processed_event_ids"]) != len(set(raw["processed_event_ids"])):
        raise StateError("MALFORMED_STATE:duplicate identity")
    if raw["terminal_status"] is not None and any((raw["pending_role"], raw["pending_intent"], raw["pending_event"])):
        raise StateError("POST_TERMINAL_EVENT")
    return raw  # type: ignore[return-value]


def normalize_malformed_state(raw: Any, *, code: str, message: str) -> dict[str, Any]:
    """Project any malformed input onto a valid, closed, terminal-bound state.

    ``validate_node`` cannot hand a malformed dictionary onward: the trace and routing code
    index required fields unconditionally.  Instead of guessing, this rebuilds a minimally
    valid state, keeping only the identity fields that survive validation on their own, and
    binds it to the BLOCKED terminal path.
    """
    source = raw if isinstance(raw, dict) else {}
    run_id = source.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"run_[a-z0-9]+", run_id):
        run_id = "run_malformed"
    thread_id = source.get("thread_id") if isinstance(source.get("thread_id"), str) else ""
    phases = source.get("requested_phases")
    if not isinstance(phases, list) or not phases or any(p not in ALL_PHASES for p in phases):
        phases = [ALL_PHASES[0]]
    risk = source.get("risk") if source.get("risk") in RISKS else "high"
    maximum = source.get("max_iterations")
    if type(maximum) is not int or not 1 <= maximum <= 10:
        maximum = 5
    capabilities = source.get("adapter_capabilities")
    if not isinstance(capabilities, list) or set(capabilities) - CAPABILITIES:
        capabilities = sorted(BASE_CAPABILITIES)
    state = initial_state(run_id=run_id, thread_id=thread_id, phases=tuple(phases),
                          capabilities=frozenset(capabilities), risk=risk, max_iterations=maximum)
    state["route_token"] = "BLOCK"
    state["terminal_reason"] = {"code": code, "message": message}
    return dict(state)


# ---- typed checkpoint updates -------------------------------------------------------
# A raw dictionary update is the widest possible ingress: every known field is writable
# with any value.  These commands narrow it to the small set of fields an operator has a
# legitimate reason to set out of band, each with its own field-specific check.  The raw
# dictionary path still exists (LangGraph's own signature) but is validated against the
# complete merged checkpoint -- see ``GuardedWorkflowGraph.update_state``.
UPDATE_COMMANDS: dict[str, tuple[str, ...]] = {
    "SET_DECISION": ("decision_state", "decision_reason_code"),
    "SET_CLARIFICATION": ("pending_clarification_id",),
    "SET_REPOSITORY_BINDING": ("repository_binding",),
    "SET_ARTIFACT_BINDING": ("artifact_binding",),
    "CLEAR_PENDING": ("pending_intent", "pending_event", "intent_status"),
    # ---- OS-31.  Neither command names worker_result, reviewer_result,
    # final_reviewer_result, phase_passes, final_review_iterations or any budget, so no
    # typed resume or disposition can touch a gate input (SS8.3).
    # ``route_token`` and ``terminal_reason`` are cleared, never set: ``route_node``
    # short-circuits to the recorded token whenever a terminal reason is present
    # (executor.py), so a resume that left the pause's reason standing would re-route
    # straight back into PAUSE instead of re-entering the workflow. Clearing them is what
    # makes the re-entry a re-entry; neither is a gate input, and both stay refused on the
    # raw ingress via PROTECTED_STATE_FIELDS.
    "RESUME_PAUSE": ("run_lifecycle", "pause_binding", "decision_state",
                     "decision_reason_code", "pending_clarification_id",
                     "round_kind", "current_phase", "correction_queue",
                     "correction_index", "binding_generation", "phase_pass_floor",
                     "repository_binding", "artifact_binding",
                     "route_token", "terminal_reason"),
    # Same rule as RESUME_PAUSE: the recorded pause reason and token are CLEARED so the
    # re-entry actually re-routes, and are refused on the raw ingress either way.
    "REQUEST_DISPOSITION": ("pause_binding", "route_token", "terminal_reason"),
}


def typed_update(command: str, **fields: Any) -> dict[str, Any]:
    """Build a checkpoint update from the closed command vocabulary.

    The command names the exact field set it may write; an unknown command, a missing
    field or an extra field is refused here, before the update reaches a checkpoint.
    """
    if command not in UPDATE_COMMANDS:
        raise StateError(f"UNKNOWN_UPDATE_COMMAND:{command}")
    allowed = set(UPDATE_COMMANDS[command])
    if set(fields) != allowed:
        raise StateError(
            f"MALFORMED_UPDATE_COMMAND:{command}:expected {sorted(allowed)}, "
            f"got {sorted(fields)}")
    if command == "SET_DECISION" and fields["decision_state"] not in DECISION_STATES:
        raise StateError("MALFORMED_UPDATE_COMMAND:SET_DECISION:decision_state")
    if command == "CLEAR_PENDING" and (fields["pending_intent"] is not None
                                       or fields["pending_event"] is not None
                                       or fields["intent_status"] != "NONE"):
        raise StateError("MALFORMED_UPDATE_COMMAND:CLEAR_PENDING:not a clear")
    if command == "RESUME_PAUSE":
        if fields["run_lifecycle"] != "ACTIVE":
            raise StateError("MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:run_lifecycle")
        if fields["pause_binding"] is not None:
            raise StateError("MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:pause_binding must clear")
        if fields["decision_state"] not in DECISION_STATES:
            raise StateError("MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:decision_state")
        if type(fields["binding_generation"]) is not int or fields["binding_generation"] < 0:
            raise StateError("MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:binding_generation")
        if not isinstance(fields["phase_pass_floor"], dict):
            raise StateError("MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:phase_pass_floor")
        if fields["route_token"] is not None or fields["terminal_reason"] is not None:
            raise StateError(
                "MALFORMED_UPDATE_COMMAND:RESUME_PAUSE:route_token and terminal_reason "
                "may only be cleared")
    if command == "REQUEST_DISPOSITION":
        binding = fields["pause_binding"]
        if not isinstance(binding, dict) or binding.get("disposition") is None:
            raise StateError(
                "MALFORMED_UPDATE_COMMAND:REQUEST_DISPOSITION:no disposition requested")
        if fields["route_token"] is not None or fields["terminal_reason"] is not None:
            raise StateError(
                "MALFORMED_UPDATE_COMMAND:REQUEST_DISPOSITION:route_token and "
                "terminal_reason may only be cleared")
    return dict(fields)
