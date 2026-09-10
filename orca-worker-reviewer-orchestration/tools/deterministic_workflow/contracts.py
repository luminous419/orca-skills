"""Runtime-neutral closed contracts for the OS-40 workflow graph."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from copy import deepcopy
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

SCHEMA_VERSION = "os40.workflow.v2"
WORKFLOW_ID = "os40.standard.v1"
ACTION_SCHEMA_VERSION = "os40.action.v2"
EVENT_SCHEMA_VERSION = "os40.event.v1"

PHASES = ("ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST")
SPECIALIZED_PHASES = ("BUGFIX", "REFACTORING")
ALL_PHASES = PHASES + SPECIALIZED_PHASES
RISKS = ("low", "medium", "high")
ROLES = ("WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER")
ROUND_KINDS = ("PHASE_GATE", "CORRECTION", "DOWNSTREAM_REVALIDATION", "FINAL_REVIEW")
ROUTE_TOKENS = (
    "BLOCK", "ESCALATE", "PREPARE_WORKER", "PREPARE_PHASE_REVIEWER",
    "ADVANCE_PHASE", "PREPARE_FINAL_REVIEWER", "PREPARE_CORRECTION",
    "PREPARE_REVALIDATION", "COMPLETE", "PAUSE", "CANCEL", "ABANDON",
    # OS-42.  A bounded validation-repair re-asks the SAME role in the SAME phase and
    # iteration for a well-formed decision-gate record.  It maps to the EXISTING
    # PREPARE_INTENT node, so no graph node is added and validate_graph_spec's
    # reachability and dead-end proofs are untouched.
    "PREPARE_REPAIR",
)
TERMINAL_STATUSES = ("COMPLETED", "BLOCKED", "ESCALATED", "CANCELLED", "ABANDONED")
# OS-31.  ``WAITING_FOR_INPUT`` is deliberately ABSENT from ``TERMINAL_STATUSES``: a run
# waiting for a human decision has not ended, and naming it terminal is exactly the defect
# OS-31 exists to remove.  It is a run *lifecycle* value, not a terminal status.
RUN_LIFECYCLE_STATES = ("ACTIVE", "WAITING_FOR_INPUT", "SETTLED")
DECISION_STATES = ("CLEAR", "ASSUMPTION_ALLOWED", "NEEDS_INPUT", "CONFLICT")

# ---- OS-42 validation repair --------------------------------------------------------
# How many times ONE gate round may re-ask an agent for a well-formed record.  It is a
# module constant and NOT a user-facing parameter (SKILL.md section 13 says the ticket
# adds none), and it is deliberately NOT derived from max_iterations: a generous
# correction budget must not buy unbounded re-asks, because the two counters answer
# different questions.
MAX_REPAIR_ATTEMPTS: int = 2
# The closed shape `result["gate"]` carries.  Duplicated from
# `decision_contract.GATE_ENVELOPE_KEYS` and pinned equal by a parity test: this module
# is the runtime-neutral core of the shipped engine and takes no tools/ sibling import.
GATE_ENVELOPE_KEYS: tuple[str, ...] = (
    "declared_state", "declaration_count", "fence_count",
    "record", "record_text", "truncated",
)
# The closed shape a REPAIR dispatch carries to the agent.  There is deliberately no
# `suggestion`, `candidate` or `recommended` key: the Coordinator never chooses a value
# for the agent, and a closed tuple is where that is enforced rather than promised.
REPAIR_INSTRUCTION_KEYS: tuple[str, ...] = ("attempt", "max_attempts", "defects")
# The three terminal codes a gate defect can produce.  They are NOT event-rejection
# codes: an event rejection is a node deciding the run is over, while a gate defect is
# not terminal until ROUTE says so.
GATE_DEFECT_CODES = frozenset({
    "DECISION_GATE_FORM_DEFECT",
    "DECISION_GATE_SEMANTIC_BLOCK",
    "DECISION_GATE_LIFECYCLE_DEFECT",
})
GATE_REPAIR_EXHAUSTED = "DECISION_GATE_REPAIR_EXHAUSTED"
# What TERMINAL may stamp for a gate outcome, so the structured payload is attached to
# exactly these and to nothing else.
GATE_TERMINAL_CODES = frozenset({GATE_REPAIR_EXHAUSTED}) | (
    GATE_DEFECT_CODES - {"DECISION_GATE_FORM_DEFECT"}
)
ARTIFACT_IDENTITY_DRIFT = "ARTIFACT_IDENTITY_DRIFT"
BASE_CAPABILITIES = frozenset({
    "agent_start", "agent_command", "agent_status", "agent_interrupt",
    "settlement", "idempotent_intent", "artifact_immutable", "checkpoint",
})
# Optional recovery capabilities.  They are NOT part of ``BASE_CAPABILITIES``: an adapter
# that cannot honestly implement one must not declare it, and the recovery path that needs
# it then fails closed instead of pretending the primitive exists.
EXTERNAL_LOOKUP = "external_lookup"      # find an existing effect by stable intent identity
EXTERNAL_RESUME = "external_resume"      # observe/collect an effect an earlier process created
RECOVERY_CAPABILITIES = frozenset({EXTERNAL_LOOKUP, EXTERNAL_RESUME})
# OS-31.  A decision block may become a durable pause only when the adapter can BOTH ask
# the question and settle what is running; an adapter that cannot honour either must not
# declare it, and pause then correctly falls back to the pre-OS-31 BLOCK behaviour.
LIFECYCLE_SETTLEMENT = "lifecycle_settlement"
PAUSE_CAPABILITIES = frozenset({"human_approval", LIFECYCLE_SETTLEMENT})
# ---- OS-37 standalone CLI execution adapter (ADDITIVE ONLY) -------------------------
# The five tokens `docs/AGENT_EXECUTION_CONTRACT.md:403-405` proposes for a runtime that
# owns local processes itself.  They are ADDITIVE: `CAPABILITIES` is only ever read as the
# allowed SUPERSET (`state.py`'s subset validation), so widening it forbids nothing that
# was previously allowed and changes no existing declaration.  `BASE_CAPABILITIES` above
# is deliberately NOT touched -- `test_deterministic_workflow_graph.py` builds
# `BASE_CAPABILITIES - {"agent_interrupt"}` to prove the validate gate fires, and adding a
# member there would change what every existing adapter is required to declare.
PTY_SESSION = "pty_session"                          # the runtime owns a real pty session
PROMPT_DELIVERY_VERIFIED = "prompt_delivery_verified"  # delivery is PROVEN, not assumed
INTERRUPT_LADDER = "interrupt_ladder"                # graceful -> bounded wait -> force
PROCESS_GROUP_OWNERSHIP = "process_group_ownership"  # signals are ownership-scoped
SESSION_REDISCOVERY = "session_rediscovery"          # a stranger process can re-query
STANDALONE_CAPABILITIES = frozenset({
    PTY_SESSION, PROMPT_DELIVERY_VERIFIED, INTERRUPT_LADDER, PROCESS_GROUP_OWNERSHIP,
    SESSION_REDISCOVERY,
})
CAPABILITIES = BASE_CAPABILITIES | RECOVERY_CAPABILITIES | STANDALONE_CAPABILITIES | frozenset({
    "human_approval", "dispatch_provenance", "dependency_edges", "runtime_ownership",
    LIFECYCLE_SETTLEMENT,
})

# ---- OS-37 W-2 / AC-37-13: the four ownership axes, as four CLOSED vocabularies -------
# `docs/AGENT_EXECUTION_CONTRACT.md` keeps these four questions apart on purpose: a
# settled dispatch says nothing about whether its worker resource may be reused, which
# says nothing about whether the process is alive, which says nothing about whether anyone
# is authorized to clean it up.  Collapsing any pair is how a live process gets reported
# as released.  The vocabularies mirror `pause_policy`'s (which owns the pause half) and
# are pinned equal to it by a parity test rather than by a cross-package import: this
# module is the runtime-neutral core and takes no sibling import.
SETTLEMENT_AXIS = ("settled", "recovered", "not_settled", "unknown")
WORKER_RESOURCE_AXIS = ("reuse", "retain", "release", "unsupervised")
PROCESS_LIVENESS_AXIS = ("live", "already exited", "disputed", "unverifiable")
CLEANUP_AUTHORITY_AXIS = ("authorized", "not_authorized", "unknown")
OWNERSHIP_AXIS_VOCABULARIES: dict[str, tuple[str, ...]] = {
    "settlement": SETTLEMENT_AXIS,
    "worker_resource": WORKER_RESOURCE_AXIS,
    "process_liveness": PROCESS_LIVENESS_AXIS,
    "cleanup_authority": CLEANUP_AUTHORITY_AXIS,
}
OWNERSHIP_AXIS_KEYS = tuple(OWNERSHIP_AXIS_VOCABULARIES)


class OwnershipAxes(TypedDict):
    """All four axes, ALWAYS all four.  There is deliberately no default and no Optional.

    A caller that can only answer three axes has an unknown on the fourth, and `unknown`
    is a member of every vocabulary that admits one -- it is never expressed by omitting
    the key, because an omitted key reads as "irrelevant" at every call site.
    """

    settlement: str
    worker_resource: str
    process_liveness: str
    cleanup_authority: str


class VocabularyError(ValueError):
    """A value outside a closed vocabulary was offered where a member is required."""


def validate_vocabulary_member(vocabulary: Any, value: Any, *, name: str = "value") -> str:
    """Return ``value`` iff it is a member of ``vocabulary``; otherwise RAISE.

    AC-37-07's mechanism.  It raises rather than substituting a default because a value
    outside a closed set is an unknown, and reducing an unknown to a default -- or to a
    boolean -- is exactly what the ticket forbids.
    """
    members = tuple(vocabulary)
    if not isinstance(value, str) or value not in members:
        raise VocabularyError(
            f"{name}={value!r} is not a member of the closed vocabulary {members!r}")
    return value


def validate_axes(value: Any) -> OwnershipAxes:
    """Validate all four axes together, or raise.

    Validating them together is the point: three valid axes and one missing key is not a
    partial answer, it is an invalid one.
    """
    if not isinstance(value, Mapping) or set(value) != set(OWNERSHIP_AXIS_KEYS):
        raise VocabularyError(
            f"ownership axes must carry exactly {OWNERSHIP_AXIS_KEYS!r}, got "
            f"{sorted(value) if isinstance(value, Mapping) else type(value).__name__!r}")
    return {  # type: ignore[return-value]
        key: validate_vocabulary_member(OWNERSHIP_AXIS_VOCABULARIES[key], value[key],
                                        name=key)
        for key in OWNERSHIP_AXIS_KEYS
    }


# ---- OS-37 AC-37-14: host scope, as a CLOSED tagged union -----------------------------
# The MVP owns local processes only.  A remote or GUI scope is not "not yet implemented
# and therefore local"; it is unparsable, and `parse_host_scope` returns None for it.
# None is a reportable absence, never a default that silently localises a remote handle.
HOST_SCOPE_KINDS = ("local",)
HostScope = Literal["local"]


def parse_host_scope(value: Any) -> str | None:
    """The parsed host scope, or ``None`` when the value names no scope this build knows.

    Deliberately NOT ``value or "local"``.  Defaulting an unparsable scope to ``local``
    would let a handle minted for another host be signalled here, which is the single
    worst thing a process supervisor can get wrong.
    """
    if isinstance(value, str) and value in HOST_SCOPE_KINDS:
        return value
    return None

Phase = Literal["ANALYSIS", "PLAN", "DESIGN", "IMPLEMENTATION", "TEST", "BUGFIX", "REFACTORING"]
Role = Literal["WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"]
RouteToken = Literal["BLOCK", "ESCALATE", "PREPARE_WORKER", "PREPARE_PHASE_REVIEWER", "ADVANCE_PHASE", "PREPARE_FINAL_REVIEWER", "PREPARE_CORRECTION", "PREPARE_REVALIDATION", "COMPLETE", "PAUSE", "CANCEL", "ABANDON", "PREPARE_REPAIR"]


class Finding(TypedDict):
    finding_id: str
    blocking: bool
    responsible_phase: Phase
    quality_attribute: str
    severity: str


class ActionIntent(TypedDict):
    schema_version: str
    intent_id: str
    command_id: str
    action_kind: str
    run_id: str
    phase: Phase
    phase_iteration: int
    final_review_iteration: int
    role: Role
    round_kind: str
    artifact_binding: dict[str, Any]
    repository_binding: dict[str, Any]
    payload_digest: str
    # ---- OS-42 ----
    repair_attempt: int                          # 0 ordinary, n>=1 the n-th repair
    gate_iteration: int                          # >= 1, artifact_identity's derivation
    artifact_contract_path: str                  # the ONE file this dispatch may write
    repair_instruction: dict[str, Any] | None    # REPAIR_INSTRUCTION_KEYS, or None


class SettlementEvent(TypedDict):
    schema_version: str
    event_id: str
    intent_id: str
    command_id: str
    event_kind: str
    outcome: str
    result: dict[str, Any]
    occurred_at: str
    payload_digest: str


class BindingError(ValueError):
    """A repository/artifact binding is missing, malformed or bound to the wrong run."""


class ExternalLookupUnavailable(RuntimeError):
    """The adapter cannot answer whether an external effect exists for this intent.

    This is deliberately distinct from "no effect exists": absence must be *proven* before
    an effect may be recreated, so an unanswerable lookup fails closed instead.
    """


class EventValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# A settlement rejected for any of these reasons must never have its result applied.
EVENT_REJECTION_CODES = frozenset({"MALFORMED_EVENT", "UNKNOWN_EVENT", "SETTLEMENT_INTEGRITY"})
# OS-42: a gate DEFECT is consumed without being applied too, but it is signalled through
# `pending_gate_defect` rather than `terminal_reason`, because whether it ends the run
# depends on its kind AND on the repair budget -- and that is a routing decision.  See
# `executor.consume_without_apply`.


# ---- repository / artifact bindings -------------------------------------------------
# A settlement may report the repository head and artifact tree its work produced.  The
# normalized shapes below are the only ones the workflow will read back into state, so a
# Reviewer intent is always bound to an exact, validated description of the Worker output
# it is judging rather than to the run's initial defaults.
REPOSITORY_BINDING_KEYS = ("head_sha", "tree_digest", "dirty")
ARTIFACT_BINDING_KEYS = ("artifact_root_id", "relative_path", "digest", "evidence_ids")
_HEAD_SHA = re.compile(r"[0-9a-f]{40}")


def normalize_repository_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(REPOSITORY_BINDING_KEYS):
        raise BindingError("MALFORMED_REPOSITORY_BINDING:closed fields")
    if type(value["head_sha"]) is not str or not _HEAD_SHA.fullmatch(value["head_sha"]):
        raise BindingError("MALFORMED_REPOSITORY_BINDING:head_sha")
    if type(value["tree_digest"]) is not str or not value["tree_digest"]:
        raise BindingError("MALFORMED_REPOSITORY_BINDING:tree_digest")
    if type(value["dirty"]) is not bool:
        raise BindingError("MALFORMED_REPOSITORY_BINDING:dirty")
    return {key: value[key] for key in REPOSITORY_BINDING_KEYS}


def normalize_artifact_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(ARTIFACT_BINDING_KEYS):
        raise BindingError("MALFORMED_ARTIFACT_BINDING:closed fields")
    if type(value["artifact_root_id"]) is not str or not value["artifact_root_id"]:
        raise BindingError("MALFORMED_ARTIFACT_BINDING:artifact_root_id")
    for key in ("relative_path", "digest"):
        if value[key] is not None and (type(value[key]) is not str or not value[key]):
            raise BindingError(f"MALFORMED_ARTIFACT_BINDING:{key}")
    evidence = value["evidence_ids"]
    if type(evidence) is not list or any(type(item) is not str or not item for item in evidence):
        raise BindingError("MALFORMED_ARTIFACT_BINDING:evidence_ids")
    return {"artifact_root_id": value["artifact_root_id"],
            "relative_path": value["relative_path"], "digest": value["digest"],
            "evidence_ids": list(evidence)}


def validate_settlement_binding(intent: ActionIntent, binding: Any) -> dict[str, Any]:
    """Validate the binding a Worker settlement advertises against the intent it answers.

    The artifact root is pinned to the intent's own root: a settlement may report new work
    inside this run's artifact tree, never rebind the workflow to another run's artifacts.
    Because the binding lives inside ``result``, it is already covered by the settlement
    digest, so a tampered binding fails the integrity check before it is read here.
    """
    if not isinstance(binding, dict) or set(binding) != {"repository", "artifact"}:
        raise BindingError("MALFORMED_SETTLEMENT_BINDING:closed fields")
    repository = normalize_repository_binding(binding["repository"])
    artifact = normalize_artifact_binding(binding["artifact"])
    expected_root = (intent.get("artifact_binding") or {}).get("artifact_root_id")
    if artifact["artifact_root_id"] != expected_root:
        raise BindingError(
            f"SETTLEMENT_BINDING_SCOPE:{artifact['artifact_root_id']} != {expected_root}")
    return {"repository": repository, "artifact": artifact}


def binding_snapshot(repository: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    """The normalized pair recorded on a gate pass, so a review is traceable to a tree."""
    return {"repository": dict(repository), "artifact": dict(artifact)}


def _validate_gate_envelope(envelope: Any) -> None:
    """Shape only. Nothing here looks at the record's CONTENTS -- that is the
    classifier's job, and doing it here would turn a repairable defect into an
    unrepairable event-integrity failure."""
    if not isinstance(envelope, dict) or set(envelope) != set(GATE_ENVELOPE_KEYS):
        raise EventValidationError(
            "MALFORMED_EVENT", f"result['gate'] must carry exactly {list(GATE_ENVELOPE_KEYS)}")
    if envelope["declared_state"] is not None and type(envelope["declared_state"]) is not str:
        raise EventValidationError("MALFORMED_EVENT", "gate declared_state must be text or null")
    for key in ("declaration_count", "fence_count"):
        value = envelope[key]
        if type(value) is not int or value < 0:
            raise EventValidationError("MALFORMED_EVENT", f"gate {key} must be a count >= 0")
    if envelope["record"] is not None and type(envelope["record"]) is not dict:
        raise EventValidationError("MALFORMED_EVENT", "gate record must be an object or null")
    if envelope["record_text"] is not None and type(envelope["record_text"]) is not str:
        raise EventValidationError("MALFORMED_EVENT", "gate record_text must be text or null")
    if type(envelope["truncated"]) is not bool:
        raise EventValidationError("MALFORMED_EVENT", "gate truncated must be a boolean")


def validate_event(intent: ActionIntent, event: dict[str, Any]) -> SettlementEvent:
    """Validate the closed settlement vocabulary and identity before its result is applied."""
    if set(event) != set(SettlementEvent.__required_keys__) or not isinstance(event.get("result"), dict):
        raise EventValidationError("MALFORMED_EVENT", "closed settlement fields/result required")
    if (event.get("schema_version") != EVENT_SCHEMA_VERSION
            or event.get("event_kind") != "AGENT_SETTLED"
            or event.get("outcome") != "SUCCEEDED"):
        raise EventValidationError("UNKNOWN_EVENT", "unsupported settlement vocabulary")
    result = event["result"]
    if intent["role"] == "WORKER":
        if result.get("status") not in {"COMPLETE", "BLOCKED"}:
            raise EventValidationError("UNKNOWN_EVENT", "unknown worker status")
    elif result.get("result") not in {"PASS", "FAIL"}:
        raise EventValidationError("UNKNOWN_EVENT", "unknown reviewer result")
    # OS-42.  The gate envelope's SHAPE, and only its shape.  A malformed envelope is the
    # ADAPTER's product, not the agent's, so it is an event-integrity failure and is never
    # repairable.  An ABSENT envelope is legal here and becomes the FORM defect
    # DECISION_GATE_INPUT_MISSING downstream -- "the agent said nothing" is a repairable
    # defect and must not be disguised as an integrity failure.
    if "gate" in result:
        _validate_gate_envelope(result["gate"])
    # Only a Worker settlement may advance the repository/artifact binding, and only with a
    # fully normalized one; a malformed or out-of-scope binding is never applied.
    if "binding" in result:
        if intent["role"] != "WORKER":
            raise EventValidationError("UNKNOWN_EVENT", "only a Worker settlement carries a binding")
        try:
            validate_settlement_binding(intent, result["binding"])
        except BindingError as exc:
            raise EventValidationError("MALFORMED_EVENT", str(exc)) from exc
    # Identity is closed: a checkpointed settlement whose result, intent/command binding,
    # digest, timestamp or event ID was altered no longer matches its canonical payload.
    if event["intent_id"] != intent["intent_id"] or event["command_id"] != intent["command_id"]:
        raise EventValidationError("SETTLEMENT_INTEGRITY", "settlement is bound to another intent/command")
    if not _well_formed_timestamp(event["occurred_at"]):
        raise EventValidationError("MALFORMED_EVENT", "settlement timestamp is malformed")
    digest = settlement_digest(intent, result)
    if not _constant_time_equal(event["payload_digest"], digest):
        raise EventValidationError("SETTLEMENT_INTEGRITY", "settlement payload digest mismatch")
    if not _constant_time_equal(event["event_id"], _event_id(digest)):
        raise EventValidationError("SETTLEMENT_INTEGRITY", "settlement event identity mismatch")
    return event  # type: ignore[return-value]


def _constant_time_equal(left: Any, right: str) -> bool:
    return isinstance(left, str) and hmac.compare_digest(left, right)


_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _well_formed_timestamp(value: Any) -> bool:
    return isinstance(value, str) and bool(_TIMESTAMP.fullmatch(value))


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def stable_id(namespace: str, value: Any) -> str:
    return f"{namespace}_{hashlib.sha256(canonical_bytes(value)).hexdigest()[:24]}"


def make_intent(state: dict[str, Any], role: Role, round_kind: str) -> ActionIntent:
    """Build the one intent this dispatch is, including everything derived from state.

    OS-42 adds four fields, and WHERE each one goes is load-bearing.

    ``repair_attempt`` is in ``identity``, so it changes ``command_id``.  It has to be:
    ``apply_result_node`` appends ``command_id`` to ``processed_command_ids`` even on the
    consume-without-apply path, and ``prepare_intent_node`` refuses a prepared intent
    whose ``command_id`` is already there.  Without the identity slot a repair -- same
    phase, same iteration, same role, same round kind -- would be refused outright, and
    if that guard were bypassed ``OrcaAdapter.start`` would return the FIRST attempt's
    cached receipt and repair would appear to succeed while doing nothing.

    ``gate_iteration``, ``artifact_contract_path`` and ``repair_instruction`` are in the
    ``payload``, not the identity.  All three are functions of fields already in
    ``identity``, so adding them there would be redundant; putting them in the payload
    makes them digest-bound, and therefore re-checked by ``runtime_state.claim`` -- so a
    successor process that re-derives this intent and computes a different artifact path,
    or a different defect list, is refused rather than silently writing elsewhere.
    """
    from . import artifact_identity      # local: keeps the module import graph acyclic

    phase = state["current_phase"]
    gate_iteration = artifact_identity.gate_iteration(state, role, phase)
    artifact_contract_path = artifact_identity.artifact_relative_path(
        run_id=state["run_id"], phase=phase, role=role, gate_iteration=gate_iteration)
    repair_attempt = state.get("repair_attempts", 0)
    defect = state.get("pending_gate_defect")
    repair_instruction = None if defect is None else {
        "attempt": repair_attempt,
        "max_attempts": MAX_REPAIR_ATTEMPTS,
        "defects": deepcopy(defect["defects"]),
    }
    # The biconditional makes "a repair dispatch with no defect payload" -- and its
    # mirror, "a defect payload on an ordinary dispatch" -- UNREPRESENTABLE rather than
    # merely discouraged.
    if (repair_attempt >= 1) != (repair_instruction is not None):
        raise ValueError("MALFORMED_STATE:repair instruction coherence")
    identity = {
        "workflow_id": state["workflow_id"], "run_id": state["run_id"],
        "phase": phase,
        "phase_iteration": state["phase_iterations"][phase],
        "final_review_iteration": state["final_review_iterations"],
        "role": role, "round_kind": round_kind, "action_kind": "RUN_AGENT",
        "repair_attempt": repair_attempt,
    }
    command_id = stable_id("cmd", identity)
    payload = {
        "command_id": command_id, "artifact_binding": state["artifact_binding"],
        "repository_binding": state["repository_binding"],
        "gate_iteration": gate_iteration,
        "artifact_contract_path": artifact_contract_path,
        "repair_instruction": repair_instruction,
    }
    payload_digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return {
        "schema_version": ACTION_SCHEMA_VERSION,
        "intent_id": stable_id("intent", {**payload, "payload_digest": payload_digest}),
        "command_id": command_id, "action_kind": "RUN_AGENT", "run_id": state["run_id"],
        "phase": phase, "phase_iteration": identity["phase_iteration"],
        "final_review_iteration": identity["final_review_iteration"], "role": role,
        "round_kind": round_kind, "artifact_binding": state["artifact_binding"],
        "repository_binding": state["repository_binding"], "payload_digest": payload_digest,
        "repair_attempt": repair_attempt, "gate_iteration": gate_iteration,
        "artifact_contract_path": artifact_contract_path,
        "repair_instruction": repair_instruction,
    }


def settlement_payload(intent: ActionIntent, result: dict[str, Any]) -> dict[str, Any]:
    """The canonical settlement payload: the closed input to digest and event identity.

    Every field an applied settlement can influence is bound in here, so a mutation of
    the result, of the role, or of the intent/command binding changes the digest.
    """
    return {
        "schema_version": EVENT_SCHEMA_VERSION, "event_kind": "AGENT_SETTLED",
        "outcome": "SUCCEEDED", "intent_id": intent["intent_id"],
        "command_id": intent["command_id"], "role": intent["role"],
        "intent_payload_digest": intent["payload_digest"], "result": result,
    }


def settlement_digest(intent: ActionIntent, result: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(settlement_payload(intent, result))).hexdigest()


def _event_id(payload_digest: str) -> str:
    return stable_id("event", {"payload_digest": payload_digest})


def settlement_event_id(intent: ActionIntent, result: dict[str, Any]) -> str:
    """Event identity is a pure function of the canonical payload.

    ``occurred_at`` is deliberately excluded: identity must be reproducible when a restarted
    process re-derives the same settlement, and it must be identical across adapters whose
    clocks differ.  The timestamp is still validated for shape and, being read by no gate,
    can influence no applied decision.
    """
    return _event_id(settlement_digest(intent, result))


def make_settlement_event(intent: ActionIntent, result: dict[str, Any], *,
                          occurred_at: str) -> SettlementEvent:
    """Build the only settlement shape ``validate_event`` accepts for this intent."""
    digest = settlement_digest(intent, result)
    return {
        "schema_version": EVENT_SCHEMA_VERSION, "event_id": _event_id(digest),
        "intent_id": intent["intent_id"], "command_id": intent["command_id"],
        "event_kind": "AGENT_SETTLED", "outcome": "SUCCEEDED", "result": result,
        "occurred_at": occurred_at, "payload_digest": digest,
    }
