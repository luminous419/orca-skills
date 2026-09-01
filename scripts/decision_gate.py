#!/usr/bin/env python3
"""OS-29 decision gate: the fail-closed evaluator every phase boundary consumes.

OS-28 (scripts/decision_policy.py) owns the decision VOCABULARY -- the four states,
their entry conditions, the closed reason codes, the required evidence and the
transition rules -- and nothing else. This module owns the GATE: it turns an agent
result or an on-disk ledger into one answer to one question, "may this boundary
dispatch?", and it answers it fail-closed.

Why a new module rather than an edit to decision_policy.py: that module's docstring
makes contract-only isolation a stated invariant, and a gate-input contract is not
part of the decision contract. Keeping them apart is also what lets this module own
LEDGER_RECORD_SCHEMA_VERSION -- the version of ONE LEDGER RECORD -- without coupling
it to decision_policy.SUPPORTED_SCHEMA_VERSIONS, which versions the ```policy-contract
`decision_policy` block a Skill ships. Two different objects, two different constants,
two independent evolutions.

Import direction, enforced statically by scripts/test_os29_decision_gate.py: this
module imports `decision_policy` and the standard library, and NOTHING from
e2e_harness, orca_runtime_harness, run_logging or task_context. run_logging in
particular may import nothing from scripts/ at all (see its own docstring), so the
edge can only point this way -- which is why open_decision_ledger() takes the schema
version as a required keyword argument supplied by its callers rather than importing
it from here.

Risk independence is structural, not documented: no function in this module takes a
risk, quality-profile or agent-profile parameter. Risk selects WHERE a harness
records a terminal block; it never selects WHAT the block says.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

try:  # imported as `scripts.decision_gate` and as a top-level module
    from scripts.decision_policy import (
        DecisionPolicy,
        DecisionPolicyError,
        validate_record,
        validate_transition,
    )
except ModuleNotFoundError:  # pragma: no cover - direct `python3 scripts/...` use
    from decision_policy import (  # type: ignore[no-redef]
        DecisionPolicy,
        DecisionPolicyError,
        validate_record,
        validate_transition,
    )


# ---- the ledger RECORD's own schema version --------------------------------------
# Deliberately NOT decision_policy.SUPPORTED_SCHEMA_VERSIONS. That constant versions
# the policy contract BLOCK and is read only by parse_decision_policy(); this one
# versions one decision ledger record and is read only by A4-i/A4-ii below. A record
# gains a field without the contract changing, and the contract gains a reason code
# without any existing record becoming unreadable, so one shared integer would couple
# two release cadences that have no reason to be coupled.
LEDGER_RECORD_SCHEMA_VERSION: int = 1
SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

DECISION_STATES: tuple[str, ...] = (
    "CLEAR",
    "ASSUMPTION_ALLOWED",
    "NEEDS_INPUT",
    "CONFLICT",
)
BLOCKING_STATES: tuple[str, ...] = ("NEEDS_INPUT", "CONFLICT")
BOUNDARIES: tuple[str, ...] = ("B1", "B2", "B3")
RUN_ENTRY_SOURCE = "coordinator:run_entry"
AGENT_SOURCES: tuple[str, ...] = ("worker", "reviewer")
SOURCES: tuple[str, ...] = (RUN_ENTRY_SOURCE,) + AGENT_SOURCES
ROLES: tuple[str, ...] = ("coordinator", "worker", "reviewer")

# The gate's own field name. NOT `DECISION_STATE`: reviews/common.md already emits
# that inside the OPTIONAL narrative `## Decision Record` section, and reusing the
# name would make the gate read the optional narrative -- collapsing two objects that
# must stay separate. The narrative section stays optional; the gate result does not.
GATE_STATE_FIELD = "DECISION_GATE_STATE"
# Spelled here rather than imported from e2e_harness.FIELD_LINE: this module may not
# import the harness (see the module docstring). The two patterns are identical and
# scripts/test_os29_decision_gate.py asserts that they stay so.
FIELD_LINE = re.compile(r"(?m)^(?P<field>[A-Z_]+):\s*(?P<value>[A-Z_]+)\s*$")
GATE_RECORD_BLOCK = re.compile(r"(?ms)^```decision-gate\n(?P<body>.*?)\n```\s*$")
GATE_RECORD_FENCE = "```decision-gate"


# ---- the closed OS-29 refusal reason set ------------------------------------------
# Every member is a `reason` string carried on the EXISTING RUN_STATUS: BLOCKED --
# the same shape MAX_ITERATIONS_REACHED and UNIT_TEST_BLOCKED already have. No
# RUN_STATUS, round_kind, Worker STATUS or REVIEW_VERDICT value is added by OS-29.
GATE_INPUT_MISSING = "DECISION_GATE_INPUT_MISSING"
GATE_INPUT_MALFORMED = "DECISION_GATE_INPUT_MALFORMED"
GATE_INPUT_UNBOUND = "DECISION_GATE_INPUT_UNBOUND"
LEDGER_INCONSISTENT = "DECISION_LEDGER_INCONSISTENT"
LEDGER_SCHEMA_UNSUPPORTED = "DECISION_LEDGER_SCHEMA_UNSUPPORTED"
DECLARATION_DISAGREES_WITH_LEDGER = "DECISION_GATE_DECLARATION_DISAGREES_WITH_LEDGER"
SUMMARY_DISAGREES_WITH_RECORD = "DECISION_GATE_SUMMARY_DISAGREES_WITH_RECORD"
DOWNGRADE_REJECTED = "DECISION_DOWNGRADE_REJECTED"
GATE_REFUSAL_REASONS: tuple[str, ...] = (
    GATE_INPUT_MISSING,
    GATE_INPUT_MALFORMED,
    GATE_INPUT_UNBOUND,
    LEDGER_INCONSISTENT,
    LEDGER_SCHEMA_UNSUPPORTED,
    DECLARATION_DISAGREES_WITH_LEDGER,
    SUMMARY_DISAGREES_WITH_RECORD,
    DOWNGRADE_REJECTED,
)
# The ninth shape is a grammar rather than a constant: a decision BLOCK names the
# state and the reason code that produced it, so the terminal row is readable without
# opening the ledger.
BLOCK_REASON_PREFIX = "DECISION_BLOCKED"
BLOCK_REASON_PATTERN = re.compile(
    r"^DECISION_BLOCKED:(?P<state>NEEDS_INPUT|CONFLICT):(?P<code>[a-z][a-z0-9_]*)$"
)
# The `decision_state` value written into the sparse ORCHESTRATOR_LOG column when the
# round was terminated by an INPUT defect rather than by a decision. It is not an
# OS-28 state and is deliberately not a member of DECISION_STATES.
INPUT_DEFECT_STATE = "INPUT"


# ---- the record schema -------------------------------------------------------------
# A. the thirteen fields the ticket requires of every recorded judgement.
REQUIRED_LEDGER_RECORD_FIELDS: tuple[str, ...] = (
    "run",
    "phase",
    "iteration",
    "state",
    "reason_code",
    "evidence",
    "assumption",
    "open_item",
    "responsible_phase",
    "role",
    "verdict",
    "source_binding",
    "recorded_at",
)
# B. the six ledger-mechanics fields. ADDITIONAL to the thirteen, never a substitute.
LEDGER_MECHANICS_FIELDS: tuple[str, ...] = (
    "ledger_schema_version",
    "boundary",
    "sequence",
    "source",
    "prior_open_decision_items",
    "verifies",
)
# C. the OS-28 contract's own evidence and grounds keys, validated by validate_record.
CONTRACT_EVIDENCE_FIELDS: tuple[str, ...] = (
    "boundary_element",
    "blast_radius",
    "monetary_cost",
    "security",
    "privacy",
    "compliance",
    "long_term_lock_in",
    "reversibility",
    "impact",
    "policy_source",
    "retraction_condition",
    "retraction",
    "what_is_missing",
    "why_policy_cannot_decide",
    "classification_attempted",
    "citations",
    "why_they_cannot_both_hold",
    "open_decision_item",
    "grounds",
    "scope",
    "user_decision",
)
# D. the field set is CLOSED. This is what makes D5's lineage boundary a CHECK rather
# than a promise: OS-30's supersession and request/response protocol fields are simply
# outside the set, so a record carrying one is malformed and never silently accepted.
CLOSED_LEDGER_RECORD_FIELDS: frozenset[str] = frozenset(
    REQUIRED_LEDGER_RECORD_FIELDS + LEDGER_MECHANICS_FIELDS + CONTRACT_EVIDENCE_FIELDS
)
# Named only so the boundary is greppable and testable. They are NOT a second closed
# set the code consults -- membership of CLOSED_LEDGER_RECORD_FIELDS is the only rule.
OS30_RESERVED_FIELDS: tuple[str, ...] = (
    "supersedes",
    "superseded_by",
    "request_id",
    "response_id",
    "options",
    "recommendation",
    "answered_at",
    "answered_by",
)

VERIFIES_FIELDS: tuple[str, ...] = ("run", "phase", "iteration", "worker_record_key")


class DecisionGateError(ValueError):
    """Base class for every gate-input defect. Never raised for I/O."""


class GateRefusal(DecisionGateError):
    """A boundary refused. `reason` is closed; `detail` is free text for the log."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}{(' -- ' + detail) if detail else ''}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class GateResult:
    """One agent's declaration plus the record that is its authority."""

    declared_state: str
    record: Mapping[str, Any]

    @property
    def state(self) -> str:
        return str(self.record.get("state", ""))

    @property
    def reason_code(self) -> str | None:
        code = self.record.get("reason_code")
        return None if code is None else str(code)


@dataclass(frozen=True)
class GateOutcome:
    """The terminal a B3-V verification produced. Always terminal in OS-29 (L6)."""

    reason: str
    block: tuple[str, str | None]


@dataclass(frozen=True)
class VerificationDispatch:
    """The ONE dispatch an open blocking head may admit, described on every axis.

    `DECISION_GATE_REVIEWER_PARTICIPATION = already_scheduled_reviewer_in_verification_mode`
    is a permission with five bindings, not a role check. This record carries all of
    them so verification_admission_defect() can refuse on each one separately and a
    caller cannot obtain the exception by asserting a role:

    * `role`      -- exactly `"reviewer"`. Not `"final_reviewer"`, not a Worker.
    * `phase`     -- the SAME phase as the head Worker record.
    * `iteration` -- the SAME iteration as the head Worker record.
    * `verifies`  -- the head Worker record's own ledger key, required and matched.

    It is NOT an admission by itself: admit_head() still evaluates A1-A4, A3 and A6
    unchanged, and the exception replaces only A5's refusal, only when the head is
    that Worker's own open B2 blocking record and nothing else in the ledger is open.
    """

    role: str
    phase: str
    iteration: int
    verifies: str | None


def block_reason(state: str, reason_code: str | None) -> str:
    """`DECISION_BLOCKED:<STATE>:<reason_code>` -- the ninth refusal shape."""
    return f"{BLOCK_REASON_PREFIX}:{state}:{reason_code}"


def decision_columns(reason: str) -> tuple[str, str]:
    """The two sparse ORCHESTRATOR_LOG values a terminal `reason` implies.

    A DECISION_BLOCKED:<STATE>:<code> reason names both directly. Every other closed
    OS-29 reason describes an INPUT defect, which is not an OS-28 state -- so it is
    reported under the non-state marker rather than coerced into one, because "the
    record was unreadable" and "the record said CLEAR" must never look alike.
    """
    match = BLOCK_REASON_PATTERN.match(reason)
    if match is not None:
        return match.group("state"), match.group("code")
    return INPUT_DEFECT_STATE, reason


def ledger_key(record: Mapping[str, Any]) -> str:
    """`run/phase/iteration/boundary#sequence`. An identity for ONE judgement.

    Deliberately not a request/response identity and never a link between two
    decisions: OS-30 owns supersession, and D5 stops the lineage here.
    """
    return (
        f"{record.get('run')}/{record.get('phase')}/{record.get('iteration')}"
        f"/{record.get('boundary')}#{record.get('sequence')}"
    )


def _closed_field_defect(record: Mapping[str, Any]) -> str | None:
    extra = sorted(set(record) - CLOSED_LEDGER_RECORD_FIELDS)
    if not extra:
        return None
    return f"fields outside the closed record set: {extra}"


def _is_int(value: Any) -> bool:
    """A bool is NOT an int here. `True` reading as 1 would let a truthy flag pass
    for a schema version, which is exactly the A4-i shape variant F13 pins."""
    return isinstance(value, int) and not isinstance(value, bool)


def validate_gate_record(policy: DecisionPolicy, record: Any) -> None:
    """The half of the schema an AGENT is responsible for: the closed field set and
    the OS-28 contract. The ledger-mechanics half is stamped by the writer and is
    checked by validate_ledger_record() when the record is read back.

    Raises GateRefusal with a closed reason; never DecisionPolicyError.
    """
    if not isinstance(record, dict):
        raise GateRefusal(GATE_INPUT_MALFORMED, "the decision-gate block is not an object")
    defect = _closed_field_defect(record)
    if defect is not None:
        raise GateRefusal(GATE_INPUT_MALFORMED, defect)
    try:
        validate_record(policy, record)
    except DecisionPolicyError as exc:
        raise GateRefusal(GATE_INPUT_MALFORMED, str(exc)) from exc


def validate_ledger_record(policy: DecisionPolicy, record: Any) -> None:
    """A4-i / A4-ii / A4-iii plus the closed field set, for ONE ledger record.

    Every clause has its own closed reason so "this build is too old for this ledger"
    is never confused with "this record is broken".
    """
    if not isinstance(record, dict):
        raise GateRefusal(GATE_INPUT_MALFORMED, "a ledger record must be an object")
    key = ledger_key(record)
    defect = _closed_field_defect(record)
    if defect is not None:
        raise GateRefusal(GATE_INPUT_MALFORMED, f"{key}: {defect}")

    if "ledger_schema_version" not in record:
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"{key} has no ledger_schema_version (A4-i)"
        )
    version = record["ledger_schema_version"]
    if not _is_int(version):
        raise GateRefusal(
            GATE_INPUT_MALFORMED,
            f"{key} ledger_schema_version must be an integer, got {version!r} (A4-i)",
        )
    if version not in SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS:
        raise GateRefusal(
            LEDGER_SCHEMA_UNSUPPORTED,
            f"{key} declares ledger_schema_version {version}; this build supports "
            f"{list(SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS)} (A4-ii)",
        )

    missing = [
        name
        for name in REQUIRED_LEDGER_RECORD_FIELDS + LEDGER_MECHANICS_FIELDS
        if name not in record
    ]
    if missing:
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"{key} is missing required fields {missing} (A4-i)"
        )
    if not _is_int(record["sequence"]) or record["sequence"] < 0:
        raise GateRefusal(
            GATE_INPUT_MALFORMED,
            f"{key} sequence must be a non-negative integer, got "
            f"{record['sequence']!r} (A4-i)",
        )
    if record["boundary"] not in BOUNDARIES:
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"{key} boundary {record['boundary']!r} (A4-i)"
        )
    if record["source"] not in SOURCES:
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"{key} source {record['source']!r} (A4-i)"
        )
    if record["role"] not in ROLES:
        raise GateRefusal(GATE_INPUT_MALFORMED, f"{key} role {record['role']!r} (A4-i)")
    if not isinstance(record["prior_open_decision_items"], list):
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"{key} prior_open_decision_items is not a list (A4-i)"
        )
    verifies = record["verifies"]
    if verifies is not None:
        if not isinstance(verifies, dict) or set(verifies) != set(VERIFIES_FIELDS):
            raise GateRefusal(
                GATE_INPUT_MALFORMED,
                f"{key} verifies must be null or carry exactly {list(VERIFIES_FIELDS)} "
                "(A4-i)",
            )
    try:
        validate_record(policy, record)
    except DecisionPolicyError as exc:
        raise GateRefusal(GATE_INPUT_MALFORMED, f"{key}: {exc} (A4-iii)") from exc


def parse_declared_state(text: str) -> str:
    """The DECISION_GATE_STATE field line, with _parse_choice's exact strictness.

    Raises GateRefusal on absence, on more than one occurrence, and on a value
    outside the closed set. There is deliberately no "" return: an absent gate
    declaration is a refusal, never a state.
    """
    values = [
        match.group("value")
        for match in FIELD_LINE.finditer(text)
        if match.group("field") == GATE_STATE_FIELD
    ]
    if not values:
        raise GateRefusal(GATE_INPUT_MISSING, f"no {GATE_STATE_FIELD} line")
    if len(values) != 1:
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"{len(values)} {GATE_STATE_FIELD} lines: {values}"
        )
    if values[0] not in DECISION_STATES:
        raise GateRefusal(GATE_INPUT_MALFORMED, f"unknown decision state {values[0]!r}")
    return values[0]


def declares_gate_result(text: str) -> bool:
    """True when `text` carries either half of a gate declaration.

    Used only by the live runtime path to tell a legacy body (which declares
    nothing and is therefore not a ledger participant) from a body that declared
    something and got it wrong -- which is a defect and must fail closed.
    """
    if GATE_RECORD_FENCE in text:
        return True
    return any(
        match.group("field") == GATE_STATE_FIELD for match in FIELD_LINE.finditer(text)
    )


def parse_gate_result(text: str, policy: DecisionPolicy) -> GateResult:
    """The B2/B3 reader. Fail-closed at every step; never returns a presumed CLEAR.

    Both halves must be present in the agent's own result body: the
    DECISION_GATE_STATE field line (the declaration) and one fenced
    ```decision-gate block holding the record (the AUTHORITY). They are reconciled
    against each other before the record is validated, so a Markdown summary that
    reads as correct while its record says something else is its own terminal
    reason rather than a silent acceptance of either half.
    """
    declared = parse_declared_state(text)
    blocks = [match.group("body") for match in GATE_RECORD_BLOCK.finditer(text)]
    if not blocks:
        raise GateRefusal(GATE_INPUT_MISSING, f"no {GATE_RECORD_FENCE} block")
    if len(blocks) != 1:
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"{len(blocks)} {GATE_RECORD_FENCE} blocks"
        )
    try:
        record = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise GateRefusal(
            GATE_INPUT_MALFORMED, f"unparseable decision-gate JSON: {exc}"
        ) from exc
    if not isinstance(record, dict):
        raise GateRefusal(GATE_INPUT_MALFORMED, "the decision-gate block is not an object")
    # THE RECORD IS THE AUTHORITY. The field line is its projection, and a
    # disagreement is a defect of the result -- not a tie broken in favour of either.
    if record.get("state") != declared:
        raise GateRefusal(
            SUMMARY_DISAGREES_WITH_RECORD,
            f"{GATE_STATE_FIELD}={declared} but record.state={record.get('state')!r}",
        )
    validate_gate_record(policy, record)
    return GateResult(declared_state=declared, record=record)


def open_items(policy: DecisionPolicy, records: Sequence[Mapping[str, Any]]) -> set[str]:
    """A5's recomputation: blocking items no LATER record has validly resolved.

    A "resolution" is only a record whose transition passes
    decision_policy.validate_transition(). OS-29 writes no resolution rule of its
    own, which is why an agreement between a Worker and a Reviewer can never become
    one.
    """
    opened: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if record.get("state") in BLOCKING_STATES and record.get("open_decision_item") is True:
            opened[ledger_key(record)] = record
    resolved: set[str] = set()
    for key, item in opened.items():
        for later in records:
            if later.get("sequence", -1) <= item.get("sequence", -1):
                continue
            if (later.get("run"), later.get("phase")) != (item.get("run"), item.get("phase")):
                continue
            if later.get("state") in BLOCKING_STATES:
                # A record that is itself blocking resolves nothing. Without this a
                # Reviewer CONFIRMING a Worker's NEEDS_INPUT would read as having
                # closed it, because the contract permits that same-state transition
                # -- agreement would become resolution, which is exactly what the
                # decision contract forbids.
                continue
            if later.get("verifies") is not None:
                # A B3-V VERIFICATION record resolves nothing either, whatever state
                # it carries. L6 makes every verification terminal: an accepted
                # downgrade is RECORDED and still blocks, because acting on it would
                # be resume -- which is OS-31, not OS-29. Without this clause a
                # Reviewer whose downgrade passes validate_transition() would close
                # the Worker's item and re-admit the correction Worker and the next
                # phase, which is the illegal-dispatch hole this ticket closes.
                continue
            try:
                validate_transition(policy, str(item["state"]), str(later["state"]), later)
            except DecisionPolicyError:
                continue
            resolved.add(key)
            break
    return set(opened) - resolved


def declaration_understatement_defect(
    declared: set[str], recomputed: set[str], head: Mapping[str, Any]
) -> str | None:
    """Is this A6 mismatch the ONE permitted shape? None when it is.

    A6 fires whenever the run-entry declaration disagrees with the recomputed open
    set. Exactly one disagreement is ordinary rather than a producer defect: the
    declaration is written once at run open, so it UNDERSTATES the single item this
    round opened. Everything else is the defect A6 exists to name -- a declaration
    that OVERSTATES the ledger by claiming an item that is not open, a phantom or
    stale entry alongside a real block, or several unrelated open items.

    Two conjuncts, and both are needed (external re-review MAJOR):

    1. `declared <= recomputed` -- nothing is claimed that is not actually open;
    2. the open set is EXACTLY the head -- one item, and it is the record this
       round settled.

    Conjunct 2 is the same condition `verification_admission_defect` states as its
    own conjunct 2, which is why A6's admission and the terminal reclassification
    can share this predicate instead of drifting into two readings of "understates".
    """
    overstated = declared - recomputed
    if overstated:
        return (
            f"the declaration claims {sorted(overstated)}, which is not open"
        )
    key = ledger_key(head)
    if recomputed != {key}:
        return f"the open items {sorted(recomputed)} are not exactly the head {key!r}"
    return None


def verification_admission_defect(
    records: Sequence[Mapping[str, Any]],
    head: Mapping[str, Any],
    still_open: set[str],
    dispatch: VerificationDispatch,
    *,
    run_id: str,
) -> str | None:
    """Is THIS dispatch the one Reviewer an open blocking head may admit? P6b row 2.

    Returns None when it is, and a detail string naming the FIRST unmet binding when
    it is not. Every clause is a conjunct: the exception exists only when all of them
    hold at once, which is what keeps it from re-opening the illegal-dispatch hole.

    Conjuncts, in the order they are reported:

    1. the caller is the `reviewer` role -- a Worker (correction or otherwise) and a
       `final_reviewer` are both excluded by exact match, not by prefix or suffix;
    2. the head Worker record is the ONLY thing still open, so a second, older
       unresolved item can never be dispatched past;
    3. the head is a Worker's own B2 record, in an open blocking state;
    4. that record binds this same run, phase and iteration -- a Reviewer of another
       phase or another iteration is not the already-scheduled one;
    5. the dispatch carries a `verifies` binding, and it is that record's key. An
       UNBOUND verification is refused here, before dispatch, exactly as an unbound
       verification RECORD is refused at B3-V afterwards;
    6. no record in the ledger already verifies that key, so exactly ONE such
       dispatch can ever be admitted for one Worker classification.
    """
    if dispatch.role != "reviewer":
        return (
            f"role {dispatch.role!r} is not the already-scheduled current-phase "
            "Reviewer"
        )
    key = ledger_key(head)
    if still_open != {key}:
        return (
            f"the open items {sorted(still_open)} are not exactly the head {key!r}"
        )
    if (
        head.get("source") != "worker"
        or head.get("role") != "worker"
        or head.get("boundary") != "B2"
    ):
        return f"head {key} is not a Worker B2 record"
    if head.get("state") not in BLOCKING_STATES or head.get("open_decision_item") is not True:
        return f"head {key} is not an open blocking classification"
    bound = (head.get("run"), head.get("phase"), head.get("iteration"))
    expected = (run_id, dispatch.phase, dispatch.iteration)
    if bound != expected:
        return f"head binds {bound}, this dispatch is {expected}"
    if dispatch.verifies is None:
        return "the verification dispatch carries no `verifies` binding"
    if dispatch.verifies != key:
        return f"the dispatch verifies {dispatch.verifies!r}, expected {key!r}"
    for record in records:
        verifies = record.get("verifies")
        if isinstance(verifies, dict) and verifies.get("worker_record_key") == key:
            return f"{ledger_key(record)} already verified {key}"
    return None


def admit_head(
    policy: DecisionPolicy,
    records: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    expected_settled_round: tuple[str, str, int] | None,
    verification: VerificationDispatch | None = None,
) -> Mapping[str, Any]:
    """A1-A6. Returns the admitted head record, or raises GateRefusal.

    `expected_settled_round` is None at the run's FIRST B1 and otherwise the
    (run, phase, iteration) of the round that just settled. It is supplied by the
    caller from its own in-memory round state and is NEVER read back off the ledger:
    deriving the expectation from the file being validated would make A3 a
    restatement of the file rather than a binding check.

    Evaluation order is fixed at A1 -> A2 -> A4 -> A3 -> A6 -> A5 so the reported
    reason is deterministic when more than one clause fails. A4 precedes A3 because
    an unreadable ledger cannot be head-selected from; A6 precedes A5 so a
    declaration that disagrees with the ledger is named as a producer defect rather
    than hidden behind the block it failed to declare. No ordering changes any
    OUTCOME: all six refusals are terminal and none can yield a CLEAR.

    `verification` is the caller's description of the dispatch it is about to make,
    and it is OMITTED by every caller that is not offering one. It can relax A5 and
    NOTHING else: A1, A2, A4, A3 and A6 are evaluated identically either way, and
    even at A5 it admits only the single Reviewer dispatch
    verification_admission_defect() accepts on all six of its conjuncts. When it is
    None -- the default, and therefore the behaviour of every pre-existing caller --
    A5 refuses every open item exactly as before.
    """
    ordered = sorted(records, key=lambda record: record.get("sequence", 0))

    # ---- A1: the ledger is non-empty. Absence is a refusal, never a CLEAR.
    if not ordered:
        raise GateRefusal(GATE_INPUT_MISSING, "the decision ledger is empty")

    # ---- A2: exactly one sequence-0 record, and it IS this run's entry declaration.
    zeros = [record for record in ordered if record.get("sequence") == 0]
    if len(zeros) != 1:
        raise GateRefusal(
            LEDGER_INCONSISTENT, f"{len(zeros)} records carry sequence 0"
        )
    run_entry = zeros[0]
    if not (
        run_entry.get("source") == RUN_ENTRY_SOURCE
        and run_entry.get("boundary") == "B1"
        and run_entry.get("run") == run_id
    ):
        raise GateRefusal(
            LEDGER_INCONSISTENT,
            f"sequence 0 is not run {run_id}'s run-entry declaration",
        )

    # ---- A4: every record, in four clauses.
    for record in ordered:
        validate_ledger_record(policy, record)
    sequences = [record.get("sequence") for record in ordered]
    if sequences != list(range(len(ordered))):
        raise GateRefusal(
            GATE_INPUT_MALFORMED,
            f"sequences {sequences} are not a gapless 0..n-1 (A4-iv)",
        )

    # ---- A3: head selection, and the binding that stops it becoming a hole.
    head = ordered[-1]
    if len(ordered) == 1:
        if expected_settled_round is not None:
            raise GateRefusal(
                GATE_INPUT_UNBOUND,
                "the run-entry declaration is the head but this is not the run's "
                f"first boundary; the settled record for {expected_settled_round} "
                "is absent",
            )
    else:
        if head.get("source") not in AGENT_SOURCES or head.get("boundary") not in ("B2", "B3"):
            raise GateRefusal(
                GATE_INPUT_UNBOUND,
                f"head {ledger_key(head)} is not a settled agent record",
            )
        if expected_settled_round is None:
            raise GateRefusal(
                GATE_INPUT_UNBOUND,
                "this is the run's first boundary but the head is an agent record",
            )
        bound = (head.get("run"), head.get("phase"), head.get("iteration"))
        if bound != tuple(expected_settled_round):
            raise GateRefusal(
                GATE_INPUT_UNBOUND,
                f"head binds {bound}, expected {tuple(expected_settled_round)}",
            )

    # ---- A6: the declaration is RECOMPUTED, never trusted.
    declared = set(run_entry.get("prior_open_decision_items") or ())
    recomputed = open_items(
        policy, [record for record in ordered if record is not run_entry]
    )
    if declared != recomputed:
        # The run-entry declaration is written once, at run open, and states what was
        # open AT RUN ENTRY; the ledger is append-only, so an item THIS RUN opened
        # makes it disagree from then on. Every ordinary caller is refused for that,
        # which is correct -- the round is terminal. The one permitted exception has
        # to survive here as well as at A5, or the current-phase verification review
        # would be unreachable in a real run: the Worker's own B2 record is always
        # the disagreement at the moment the Reviewer is dispatched (Final
        # Adversarial Review F-001).
        #
        # It is the SAME predicate on the SAME head, and it narrows A6 by one more
        # conjunct than A5: the ENTIRE disagreement must be that head record, and the
        # declaration may only UNDERSTATE it. A declaration claiming an item that is
        # not open, or a second open item alongside the head, is still the producer
        # defect A6 exists to name.
        admissible = (
            verification is not None
            and declaration_understatement_defect(declared, recomputed, head) is None
            and verification_admission_defect(
                ordered, head, recomputed, verification, run_id=run_id
            )
            is None
        )
        if not admissible:
            raise GateRefusal(
                DECLARATION_DISAGREES_WITH_LEDGER,
                f"declared={sorted(declared)} recomputed={sorted(recomputed)}",
            )

    # ---- A5: no unresolved open blocking item anywhere in the ledger, with the ONE
    # bound exception P6b row 2 requires -- the already-scheduled current-phase
    # Reviewer, in verification mode, bound to THIS head Worker record. The exception
    # is evaluated here and only here: it changes which dispatch A5 admits, never
    # what the block says, never whether the round is terminal, and never whether the
    # item is resolved (open_items() refuses to let a verification resolve anything).
    still_open = open_items(policy, ordered)
    if still_open:
        blocker = next(
            record for record in ordered if ledger_key(record) in still_open
        )
        detail = f"open at {ledger_key(blocker)}"
        if verification is not None:
            defect = verification_admission_defect(
                ordered, head, still_open, verification, run_id=run_id
            )
            if defect is None:
                return head
            detail = f"{detail}; verification refused: {defect}"
        raise GateRefusal(
            block_reason(str(blocker.get("state")), blocker.get("reason_code")),
            detail,
        )

    return head


def unresolved_block_reason(
    policy: DecisionPolicy,
    records: Sequence[Mapping[str, Any]],
    *,
    refused_with: str,
) -> str | None:
    """`DECISION_BLOCKED:<STATE>:<code>` when this ledger holds a VALID unresolved
    blocking item, else None.

    A5's own classification, factored out so a TERMINAL boundary can name the block
    rather than whatever clause happened to fire first. admit_head() evaluates
    A6 before A5, and A6 disagrees whenever an item THIS RUN opened is absent from
    the immutable run-entry declaration -- which is every real block. On a dispatch
    boundary that ordering is harmless: the caller is refused either way and the
    round is terminal. At the run's LAST boundary it is not, because the refusal
    reason is the run's recorded terminal classification, and reporting a valid,
    well-formed NEEDS_INPUT as a producer defect loses the state and reason code
    exactly where the evidence matters most (external re-review MAJOR).

    `refused_with` is the reason admit_head() ACTUALLY raised, and reclassification
    happens for exactly one value: DECLARATION_DISAGREES_WITH_LEDGER. That is not a
    convention, it is a proof. admit_head() evaluates A1 -> A2 -> A4 -> A3 -> A6, so
    reaching A6 is itself the certificate that the ledger is non-empty, has exactly
    one valid sequence-0 run entry, has gapless unique 0..n-1 sequences, and has a
    head bound to the expected settled round. Any OTHER refusal means one of those
    LEDGER-LEVEL clauses failed, and a ledger that is invalid AS A LEDGER can still
    contain individually valid blocking records -- duplicate or gapped sequences, an
    agent record from another run, a head bound to the wrong phase or iteration.
    Reclassifying those would replace a genuine structural defect with a valid-looking
    decision block, which is precisely the laundering this function must not do
    (external re-review MAJOR). Every other reason is therefore preserved unchanged.

    The per-record validate_ledger_record() sweep below is kept as well. It is
    redundant while `refused_with` is A6 -- A4 already passed -- and it is deliberate
    defence in depth, so that a future caller who widens the precondition cannot turn
    an unreadable record into a block without also deleting this loop.

    This function only ever RECLASSIFIES an existing refusal; it never admits
    anything, never returns a non-block reason, and is never a substitute for
    admit_head().
    """
    if refused_with != DECLARATION_DISAGREES_WITH_LEDGER:
        return None
    ordered = sorted(records, key=lambda record: record.get("sequence", 0))
    if not ordered:
        return None
    for record in ordered:
        try:
            validate_ledger_record(policy, record)
        except GateRefusal:
            return None
    run_entry = next((r for r in ordered if r.get("sequence") == 0), None)
    if run_entry is None:
        return None
    head = ordered[-1]
    declared = set(run_entry.get("prior_open_decision_items") or ())
    recomputed = open_items(
        policy, [record for record in ordered if record is not run_entry]
    )
    # Shape, in the order the defects were reported by review. An OVERSTATED or
    # phantom declaration and several unrelated open items each stay the producer
    # defect A6 named; only a single genuinely open Worker item may be reclassified.
    if declared - recomputed:
        return None
    by_key = {ledger_key(record): record for record in ordered}
    head_key = ledger_key(head)

    # ---- Terminal shape A: the head IS the sole open block.
    # Two real cases share it. A Worker B2 classification that ended the round at
    # B2 (LOW, and MEDIUM/HIGH without the permitted verification), and a Reviewer
    # B3 that discovered the block itself -- the Final Review's own decision. Both
    # are A6's admission shape: the declaration understates exactly this record.
    if recomputed == {head_key}:
        return block_reason(str(head.get("state")), head.get("reason_code"))

    # ---- Terminal shape B: the head is the ONE bound B3 verification of the sole
    # open Worker item. After the permitted MEDIUM/HIGH verification settles the head
    # is the Reviewer record, and open_items() deliberately keeps the WORKER item
    # open because a verification never resolves anything. A confirming or stricter
    # Reviewer is itself blocking, so the open set legitimately holds BOTH -- which
    # is why shape A's "open set equals the head" cannot be reused here, and why
    # reusing it alone made MEDIUM/HIGH lose the terminal block LOW kept
    # (external re-review MAJOR).
    candidates = recomputed - {head_key}
    if len(candidates) != 1:
        return None
    (open_key,) = tuple(candidates)
    if recomputed - {open_key, head_key}:
        return None
    blocker = by_key.get(open_key)
    if blocker is None:
        return None
    if (
        blocker.get("boundary") != "B2"
        or blocker.get("source") != "worker"
        or blocker.get("role") != "worker"
    ):
        return None
    if verification_binding_defect(
        GateResult(str(head.get("state", "")), head),
        worker_key=open_key,
        run_id=str(blocker.get("run", "")),
        phase=str(blocker.get("phase", "")),
        iteration=int(blocker.get("iteration", 0)),
    ) is not None:
        return None
    # The SAME evaluator the B3-V settlement used, so confirmation, stricter
    # classification and a rejected downgrade report what they reported then.
    return evaluate_verification(
        policy,
        GateResult(str(blocker.get("state", "")), blocker),
        GateResult(str(head.get("state", "")), head),
    ).reason


def verification_binding_defect(
    reviewer: GateResult, *, worker_key: str, run_id: str, phase: str, iteration: int
) -> str | None:
    """P6b row 7: is the Reviewer's verification record BOUND to the Worker's B2 record?

    Returns a detail string when it is not. A deliberately explicit check rather than
    a silent fall-back to the Worker's classification: both outcomes block, but only
    this one makes the defect visible.
    """
    verifies = reviewer.record.get("verifies")
    if not isinstance(verifies, dict):
        return "the verification record carries no `verifies` reference"
    if set(verifies) != set(VERIFIES_FIELDS):
        return f"`verifies` must carry exactly {list(VERIFIES_FIELDS)}"
    actual = (
        verifies.get("run"),
        verifies.get("phase"),
        verifies.get("iteration"),
        verifies.get("worker_record_key"),
    )
    expected = (run_id, phase, iteration, worker_key)
    if actual != expected:
        return f"`verifies` binds {actual}, expected {expected}"
    return None


def evaluate_verification(
    policy: DecisionPolicy, worker: GateResult, reviewer: GateResult
) -> GateOutcome:
    """P6b rows 4-6. The round is TERMINAL on every one of them (L6).

    Row 4 -- the Reviewer CONFIRMS the Worker's classification: the terminal carries
    the Worker's own state and code, byte-identical to the LOW terminal row 2
    produces at B2. That equality is the whole content of "risk never expands
    decision authority".

    Row 5 -- the Reviewer is STRICTER: the terminal carries the Reviewer's state and
    code. A verification may move toward more blocking, never away.

    Row 6 -- the Reviewer proposes a DOWNGRADE: decided solely by
    decision_policy.validate_transition(). OS-29 writes no downgrade rule of its own,
    and an accepted downgrade is recorded and STILL terminal, because acting on it
    would be resume -- which is OS-31.
    """
    if reviewer.state in BLOCKING_STATES:
        if (reviewer.state, reviewer.reason_code) == (worker.state, worker.reason_code):
            return GateOutcome(
                reason=block_reason(worker.state, worker.reason_code),
                block=(worker.state, worker.reason_code),
            )
        return GateOutcome(
            reason=block_reason(reviewer.state, reviewer.reason_code),
            block=(reviewer.state, reviewer.reason_code),
        )
    try:
        validate_transition(policy, worker.state, reviewer.state, reviewer.record)
    except DecisionPolicyError:
        return GateOutcome(
            reason=DOWNGRADE_REJECTED, block=(worker.state, worker.reason_code)
        )
    return GateOutcome(
        reason=block_reason(worker.state, worker.reason_code),
        block=(worker.state, worker.reason_code),
    )
