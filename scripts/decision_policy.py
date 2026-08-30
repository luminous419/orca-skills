#!/usr/bin/env python3
"""Loader and evaluator for the OS-28 bounded-autonomy decision policy contract.

The contract itself lives in the shared ```policy-contract JSON block of both
SKILL.md files, under the `decision_policy` key. This module is its ONE parser:
scripts/validate_skills.py imports from here rather than re-implementing the
parse, the same dependency direction validate_risk_profile_contract() already
has toward skill_policy.load_risk_contract.

---- Why every failure here RAISES, and never returns None -------------------
This module deliberately does NOT copy skill_policy.load_risk_contract's
convention. That function returns None for a malformed block, and its caller
(evaluate_invocation) reads None as "this Skill has no risk axis" -- so a
malformed block silently removes the axis at runtime. That is a fail-OPEN, and
OS-28 validation requirement 9 requires the opposite.

The precedent this module follows instead is quality_profile.py's
_parse_document() and agent_profile.py's document validation: an unsupported or
unparseable schema raises. Do not "fix" this toward load_risk_contract; the
one-condition-one-diagnostic convention there exists because the repository
validator is that block's only consumer, which is not true here.

---- What this module deliberately does NOT do -------------------------------
No import of orca_runtime_harness, run_logging, review_isolation, e2e_harness
or task_context. No dispatch, gate, phase, pause, wait or question logic. This
is the OS-28 contract; running the check at a phase gate is OS-29, asking the
question is OS-30, and waiting for the answer is OS-31.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # pragma: no cover - import shim, exercised by both invocation forms
    from scripts.skill_policy import load_policy_contract
except ImportError:  # pragma: no cover - same module, flat import path
    from skill_policy import load_policy_contract


SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)

DECISION_STATES: tuple[str, ...] = (
    "CLEAR",
    "ASSUMPTION_ALLOWED",
    "NEEDS_INPUT",
    "CONFLICT",
)

# ---- DESIGN D3-2: the axis-independence partition ---------------------------
# One definition, three readers: this loader, validate_skills.py, and the tests.
# Iteration 1 of DESIGN had a single rule "no key or value names a risk level or
# a profile", which FAILS THE CORRECT CONTRACT because `independent_axes`
# intentionally names all three axes (review finding RD-1). The rule is therefore
# split by POSITION: axis tokens are forbidden inside a state-selection input and
# permitted in a declarative one, where a positive equality check guards them.
STATE_SELECTION_INPUTS: frozenset[str] = frozenset(
    {
        "states",
        "transitions",
        "entry_clauses",
        "reason_codes",
        "authority_precedence",
        "boundary_elements",
        "entry_conditions",
        "required_evidence",
        "assumption_allowed_requires",
        "assumption_allowed_forbidden_when",
        "user_decision_fields",
        "user_decision_sources",
        "forbidden_authority_sources",
        "citation_minimum",
        "downstream_rule",
        "aggregate_order",
        "policy_source_roles",
        "policy_source_kinds",
        "state_scope",
    }
)
DECLARATIVE_KEYS: frozenset[str] = frozenset({"schema_version", "independent_axes"})

# Exact-token matching, NOT substring. A substring rule would reject
# `quality_attribute_id` -- a legitimate policy_source_kinds member, because A4-0
# admits a quality attribute as an evidence SOURCE -- and `long_term_lock_in`,
# which contains "lo" but not the token "low". Citing a quality attribute as a
# policy source is an input the Worker supplies; the axis acting as a SELECTOR the
# contract branches on is what requirement 7 forbids. Exact tokens keep them apart.
AXIS_TOKENS: frozenset[str] = frozenset(
    {"risk", "quality_profile", "agent_profile", "profile", "low", "medium", "high"}
)
CANONICAL_INDEPENDENT_AXES: tuple[str, ...] = ("risk", "quality_profile", "agent_profile")

# FR-4: the closed vocabulary of entry predicates. `permitted_states` evaluates a
# state's entry condition rather than assuming a fixed starting set, so an unknown
# predicate name must fail closed at load time -- otherwise a typo would silently
# make a condition unsatisfiable and quietly narrow (or widen) the boundary.
ENTRY_PREDICATES: frozenset[str] = frozenset(
    {
        "no_open_decision_item",
        "determining_policy_source",
        "explicit_user_authorization",
        "reversible_in_run",
        "blast_radius_within_scope",
        "no_high_impact_element",
        "supporting_policy_source",
        "no_reserved_user_authority",
        "undetermined_boundary_element",
        "absent_user_intent",
        "unclassifiable_item",
        "declared_contradiction",
    }
)
ENTRY_COMBINATORS: frozenset[str] = frozenset({"any_of", "all_of"})

TRANSITION_VALUES: frozenset[str] = frozenset(
    {"allowed", "forbidden", "requires_user_decision", "requires_retraction"}
)
WORKFLOW_VALUES: frozenset[str] = frozenset(
    {
        "continue",
        "continue_and_review",
        "pause_and_ask",
        "pause_and_request_resolution",
    }
)

DECISION_POLICY_MAX_LINES = 90


class DecisionPolicyError(ValueError):
    """Raised when the decision-policy contract is missing, malformed, or unsupported."""


@dataclass(frozen=True)
class StateSpec:
    workflow: str
    user_decision_required: bool
    reason_code_required: bool


@dataclass(frozen=True)
class BoundaryElement:
    kind: str
    values: tuple[str, ...] = ()
    minimum: int | None = None
    triggering: Any = None
    """Which value(s) of this element mean it is TRUE in A3-1's sense. `None` for
    repository_project_policy, which A4-0 classifies as a boundary INPUT that resolves
    items rather than a trigger that escalates them."""


@dataclass(frozen=True)
class ReasonCode:
    name: str
    state: str
    clause: str | None
    boundary_element: str | None
    required_evidence: tuple[str, ...]
    """The EFFECTIVE set: the per-code override when the contract declares one,
    else the per-state default. `unclassifiable_decision` is the only code with an
    override today -- it cannot supply a boundary_element by definition, and
    inventing a twelfth boundary element was rejected (DESIGN D1-4)."""


@dataclass(frozen=True)
class DecisionPolicy:
    schema_version: int
    state_scope: str
    aggregate_order: tuple[str, ...]
    states: Mapping[str, StateSpec]
    transitions: Mapping[tuple[str, str], str]
    downstream_rule: str
    entry_clauses: Mapping[str, Mapping[str, str]]
    reason_codes: Mapping[str, ReasonCode]
    boundary_elements: Mapping[str, BoundaryElement]
    entry_conditions: Mapping[str, Mapping[str, tuple[str, ...]]]
    policy_source_cannot_resolve: frozenset[str]
    policy_source_roles: tuple[str, ...]
    policy_source_kinds: tuple[str, ...]
    required_evidence: Mapping[str, tuple[str, ...]]
    assumption_allowed_requires: Mapping[str, Any]
    assumption_allowed_forbidden_when: Mapping[str, Any]
    user_decision_fields: tuple[str, ...]
    user_decision_sources: frozenset[str]
    forbidden_authority_sources: frozenset[str]
    citation_minimum: Mapping[str, int]
    independent_axes: tuple[str, ...]
    raw: Mapping[str, Any]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DecisionPolicyError(message)


def parse_decision_policy(block: Any) -> DecisionPolicy:
    """Validate and structure the `decision_policy` object. Raises, never returns None."""

    _require(isinstance(block, dict), "decision_policy must be an object")

    version = block.get("schema_version")
    _require("schema_version" in block, "decision_policy is missing 'schema_version'")
    _require(
        isinstance(version, int) and not isinstance(version, bool),
        f"decision_policy schema_version must be an integer, got {version!r}",
    )
    _require(
        version in SUPPORTED_SCHEMA_VERSIONS,
        f"unsupported decision policy schema version {version}; this build supports "
        f"{list(SUPPORTED_SCHEMA_VERSIONS)}",
    )

    keys = set(block)
    expected = STATE_SELECTION_INPUTS | DECLARATIVE_KEYS
    _require(
        keys == expected,
        "decision_policy key set is invalid; unexpected="
        f"{sorted(keys - expected)} missing={sorted(expected - keys)}",
    )

    states: dict[str, StateSpec] = {}
    raw_states = block["states"]
    _require(isinstance(raw_states, dict), "decision_policy states must be an object")
    _require(
        set(raw_states) == set(DECISION_STATES),
        f"decision_policy states must be exactly {list(DECISION_STATES)}",
    )
    for name, spec in raw_states.items():
        _require(isinstance(spec, dict), f"state {name} must be an object")
        workflow = spec.get("workflow")
        _require(
            workflow in WORKFLOW_VALUES,
            f"state {name} workflow {workflow!r} is outside the closed set",
        )
        for flag in ("user_decision_required", "reason_code_required"):
            _require(
                isinstance(spec.get(flag), bool),
                f"state {name} {flag} must be a boolean",
            )
        states[name] = StateSpec(
            workflow=str(workflow),
            user_decision_required=bool(spec["user_decision_required"]),
            reason_code_required=bool(spec["reason_code_required"]),
        )

    transitions: dict[tuple[str, str], str] = {}
    raw_transitions = block["transitions"]
    _require(
        isinstance(raw_transitions, dict)
        and set(raw_transitions) == set(DECISION_STATES),
        "decision_policy transitions must cover exactly the four states",
    )
    for source, row in raw_transitions.items():
        _require(
            isinstance(row, dict) and set(row) == set(DECISION_STATES),
            f"transition row {source} must cover exactly the four states",
        )
        for target, rule in row.items():
            _require(
                rule in TRANSITION_VALUES,
                f"transitions[{source}][{target}]={rule!r} is outside the closed set",
            )
            transitions[(source, target)] = str(rule)

    raw_clauses = block["entry_clauses"]
    _require(isinstance(raw_clauses, dict), "entry_clauses must be an object")
    entry_clauses: dict[str, Mapping[str, str]] = {}
    for state, clauses in raw_clauses.items():
        _require(state in states, f"entry_clauses names unknown state {state!r}")
        _require(isinstance(clauses, dict) and clauses, f"entry_clauses[{state}] must be a non-empty object")
        entry_clauses[state] = dict(clauses)

    boundary_elements: dict[str, BoundaryElement] = {}
    raw_elements = block["boundary_elements"]
    _require(isinstance(raw_elements, dict), "boundary_elements must be an object")
    for name, spec in raw_elements.items():
        _require(isinstance(spec, dict), f"boundary element {name} must be an object")
        element = BoundaryElement(
            kind=str(spec.get("kind", "")),
            values=tuple(spec.get("values", ()) or ()),
            minimum=spec.get("minimum"),
            triggering=spec.get("triggering"),
        )
        # Downstream revalidation: `triggering` was checked for PRESENCE but never for
        # CONSISTENCY with the element's own value set, so an enum element could name
        # a triggering value it does not declare. Nothing could then equal it --
        # _validate_declared_facts rejects out-of-enum values -- so the element became
        # a DEAD TRIGGER and stopped escalating silently. C27 catches drift from the
        # pinned value; this catches a contract that is inconsistent on its own terms.
        if element.kind == "enum" and isinstance(element.triggering, (list, tuple)):
            orphans = [v for v in element.triggering if v not in element.values]
            _require(
                not orphans,
                f"boundary element {name!r} names triggering value(s) {orphans} that "
                f"are not in its own value set {list(element.values)}",
            )
        boundary_elements[name] = element

    precedence = block["authority_precedence"]
    _require(isinstance(precedence, dict), "authority_precedence must be an object")
    cannot_resolve = frozenset(precedence.get("policy_source_cannot_resolve", ()))
    unknown_precedence = sorted(cannot_resolve - set(raw_elements))
    _require(
        not unknown_precedence,
        f"authority_precedence names unknown boundary element(s) {unknown_precedence}",
    )

    raw_conditions = block["entry_conditions"]
    _require(
        isinstance(raw_conditions, dict)
        and set(raw_conditions) == set(DECISION_STATES),
        "entry_conditions must cover exactly the four states",
    )
    entry_conditions: dict[str, Mapping[str, tuple[str, ...]]] = {}
    for state, condition in raw_conditions.items():
        _require(
            isinstance(condition, dict) and len(condition) == 1,
            f"entry_conditions[{state}] must name exactly one combinator",
        )
        (combinator, predicates), = condition.items()
        _require(
            combinator in ENTRY_COMBINATORS,
            f"entry_conditions[{state}] combinator {combinator!r} is outside the closed set",
        )
        _require(
            isinstance(predicates, list) and predicates,
            f"entry_conditions[{state}] must list at least one predicate",
        )
        unknown = [name for name in predicates if name not in ENTRY_PREDICATES]
        _require(
            not unknown,
            f"entry_conditions[{state}] names unknown predicate(s) {unknown}",
        )
        entry_conditions[str(state)] = {str(combinator): tuple(predicates)}

    raw_required = block["required_evidence"]
    _require(
        isinstance(raw_required, dict) and set(raw_required) == set(DECISION_STATES),
        "required_evidence must cover exactly the four states",
    )
    required_evidence = {
        state: tuple(fields) for state, fields in raw_required.items()
    }

    reason_codes: dict[str, ReasonCode] = {}
    raw_codes = block["reason_codes"]
    _require(isinstance(raw_codes, dict) and raw_codes, "reason_codes must be a non-empty object")
    for name, spec in raw_codes.items():
        _require(isinstance(spec, dict), f"reason code {name} must be an object")
        state = spec.get("state")
        _require(state in states, f"reason code {name} names unknown state {state!r}")
        clause = spec.get("clause")
        if state in entry_clauses:
            _require(
                clause in entry_clauses[state],
                f"reason code {name} names unknown entry clause {clause!r} for {state}",
            )
        else:
            _require(
                clause is None,
                f"reason code {name} names clause {clause!r} but {state} has no clause set",
            )
        element = spec.get("boundary_element")
        if element is not None:
            _require(
                element in boundary_elements,
                f"reason code {name} names unknown boundary element {element!r}",
            )
        effective = tuple(spec.get("required_evidence") or required_evidence[str(state)])
        _require(
            "boundary_element" not in effective or element is not None,
            f"reason code {name} requires a boundary_element field but declares none",
        )
        reason_codes[name] = ReasonCode(
            name=name,
            state=str(state),
            clause=clause,
            boundary_element=element,
            required_evidence=effective,
        )

    user_decision_sources = frozenset(block["user_decision_sources"])
    _require(bool(user_decision_sources), "user_decision_sources must not be empty")
    forbidden_sources = frozenset(block["forbidden_authority_sources"])
    # FR-2: the denylist no longer enforces anything -- it guards the allowlist. If a
    # forbidden category is ever added to the positive vocabulary, that is the defect
    # this catches, and it catches it at load time in both Skills.
    overlap = user_decision_sources & forbidden_sources
    _require(
        not overlap,
        f"user_decision_sources must not admit a forbidden authority source: {sorted(overlap)}",
    )

    independent_axes = tuple(block["independent_axes"])
    _require(
        independent_axes == CANONICAL_INDEPENDENT_AXES,
        f"independent_axes must be exactly {list(CANONICAL_INDEPENDENT_AXES)}",
    )

    return DecisionPolicy(
        schema_version=version,
        state_scope=str(block["state_scope"]),
        aggregate_order=tuple(block["aggregate_order"]),
        states=states,
        transitions=transitions,
        downstream_rule=str(block["downstream_rule"]),
        entry_clauses=entry_clauses,
        reason_codes=reason_codes,
        boundary_elements=boundary_elements,
        entry_conditions=entry_conditions,
        policy_source_cannot_resolve=cannot_resolve,
        policy_source_roles=tuple(block["policy_source_roles"]),
        policy_source_kinds=tuple(block["policy_source_kinds"]),
        required_evidence=required_evidence,
        assumption_allowed_requires=dict(block["assumption_allowed_requires"]),
        assumption_allowed_forbidden_when=dict(block["assumption_allowed_forbidden_when"]),
        user_decision_fields=tuple(block["user_decision_fields"]),
        user_decision_sources=user_decision_sources,
        forbidden_authority_sources=frozenset(block["forbidden_authority_sources"]),
        citation_minimum={k: int(v) for k, v in block["citation_minimum"].items()},
        independent_axes=independent_axes,
        raw=block,
    )


def load_decision_policy(skill_path: Path) -> DecisionPolicy:
    """Load the decision policy from a SKILL.md. Raises DecisionPolicyError on any defect."""

    contract = load_policy_contract(skill_path)
    _require(
        "decision_policy" in contract,
        f"{skill_path}: missing decision_policy contract",
    )
    return parse_decision_policy(contract["decision_policy"])


def codes_for_state(policy: DecisionPolicy, state: str) -> tuple[str, ...]:
    """The reason codes the contract assigns to `state`, in contract order."""
    return tuple(
        name for name, code in policy.reason_codes.items() if code.state == state
    )


def transition_rule(policy: DecisionPolicy, source: str, target: str) -> str:
    _require(source in policy.states, f"unknown source state {source!r}")
    _require(target in policy.states, f"unknown target state {target!r}")
    return policy.transitions[(source, target)]


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) == 0
    return False


def _assumption_allowed_is_forbidden(
    policy: DecisionPolicy, facts: Mapping[str, Any]
) -> bool:
    """INV-4, with NO exception. A4-0: neither a determining policy_source nor a
    user_decision lifts any of these; they route the item to CLEAR instead."""
    rule = policy.assumption_allowed_forbidden_when
    reversibility = facts.get("reversibility")
    if reversibility in tuple(rule.get("reversibility_in", ())):
        return True
    if reversibility == "irreversible" and facts.get("blast_radius") in tuple(
        rule.get("blast_radius_in_with_irreversible", ())
    ):
        return True
    for element in tuple(rule.get("any_true_of", ())):
        if facts.get(element) is True:
            return True
    if rule.get("explicit_user_authority_reserved") and (
        facts.get("explicit_user_authority") == "reserved"
    ):
        return True
    return False


def _policy_source_role(facts: Mapping[str, Any]) -> str | None:
    """The declared policy-source role, from either accepted shape.

    A flat facts mapping declares `policy_source_role`; a decision record declares
    the nested `policy_source: {"role": ...}` that validate_record checks. Accepting
    both lets requirement 4 and 5's tests pass one record to both functions instead
    of maintaining two parallel fixtures that could drift apart. Neither shape can
    carry an axis value into the computation -- see permitted_states.
    """
    flat = facts.get("policy_source_role")
    if flat is not None:
        return str(flat)
    nested = facts.get("policy_source")
    if isinstance(nested, dict) and nested.get("role") is not None:
        return str(nested["role"])
    return None


def _element_is_triggering(spec: BoundaryElement, value: Any) -> bool:
    """Does this declared value make the element TRUE in A3-1's sense?

    The triggering values come from the contract, not from this code: A4-1 already
    fixed them (irreversible; blast radius in repository/external_system; the five
    booleans true; authority reserved), and C27 pins them by value.
    """
    if spec.triggering is None:
        return False
    if isinstance(spec.triggering, bool):
        return value is spec.triggering
    if spec.triggering == "at_minimum":
        minimum = spec.minimum or 0
        try:
            return len(value) >= minimum
        except TypeError:
            return False
    if isinstance(spec.triggering, (list, tuple)):
        return value in tuple(spec.triggering)
    return value == spec.triggering


def _validate_declared_facts(
    policy: DecisionPolicy, facts: Mapping[str, Any]
) -> None:
    """Declared values must be members of the sets the contract declares.

    Found by the FR-3 axis sweep ("checked for presence, never for consistency").
    An unrecognised enum value did not raise: it simply matched no triggering value,
    so `{"reversibility": "irrevrsible"}` produced an EMPTY permitted set -- a
    degenerate outcome rather than a rejection. Fail-closed means rejecting the input,
    not returning a state set nobody can act on. Only DECLARED keys are checked, so
    omitting an element stays legal.
    """
    for element, spec in policy.boundary_elements.items():
        if element not in facts or spec.kind != "enum":
            continue
        value = facts[element]
        _require(
            value in spec.values,
            f"boundary element {element!r} declares {value!r}, which is outside its "
            f"closed value set {list(spec.values)}",
        )
    source = facts.get("policy_source")
    if isinstance(source, dict) and source.get("kind") is not None:
        _require(
            source["kind"] in policy.policy_source_kinds,
            f"policy_source kind {source['kind']!r} is outside the closed set "
            f"{list(policy.policy_source_kinds)}",
        )


def _evaluate_predicate(
    name: str, policy: DecisionPolicy, facts: Mapping[str, Any]
) -> bool:
    """One entry predicate over the DECLARED facts. Closed vocabulary: a name not
    handled here is rejected at load time by ENTRY_PREDICATES, so this cannot
    silently return False for a typo."""
    role = _policy_source_role(facts)
    authorization = facts.get("user_decision") or {}
    authorized = (
        isinstance(authorization, dict)
        and authorization.get("source") in policy.user_decision_sources
    )
    triggered = [
        element
        for element, spec in policy.boundary_elements.items()
        if element in facts and _element_is_triggering(spec, facts[element])
    ]

    # RI3-1: authority PRECEDENCE. The predicates below were evaluated
    # independently, so each was right alone and wrong in combination. A4-0 names
    # exactly two things a determining policy source cannot resolve: it "cannot
    # un-reserve" explicit user authority (-> NEEDS_INPUT) and "cannot arbitrate two
    # explicit requirements" (-> CONFLICT). The contract carries those two element
    # names in authority_precedence.policy_source_cannot_resolve; this is the one
    # place they are applied.
    contradiction = facts.get("conflict_clause") in tuple(
        policy.entry_clauses.get("CONFLICT", {})
    )
    unresolvable_by_policy = (
        "explicit_user_authority" in policy.policy_source_cannot_resolve
        and facts.get("explicit_user_authority") == "reserved"
    ) or (
        "explicit_requirement_conflict" in policy.policy_source_cannot_resolve
        and contradiction
    )

    if name == "no_open_decision_item":
        # A3-1 clause 1 is "no decision item is open". A triggering element or a
        # declared contradiction IS an open item, so the caller asserting
        # open_decision_item=false alongside one of them is self-contradictory and
        # must not yield CLEAR.
        return (
            facts.get("open_decision_item") is False
            and not triggered
            and not contradiction
        )
    if name == "determining_policy_source":
        # A valid user decision outranks the precedence bar: A4-0's authorization
        # column routes both "cannot" rows to CLEAR.
        return role == "determines" and (authorized or not unresolvable_by_policy)
    if name == "explicit_user_authorization":
        return authorized
    if name == "reversible_in_run":
        return facts.get("reversibility") == "reversible_in_run"
    if name == "blast_radius_within_scope":
        spec = policy.boundary_elements["blast_radius"]
        return facts.get("blast_radius") not in tuple(spec.triggering or ())
    if name == "no_high_impact_element":
        return not _assumption_allowed_is_forbidden(policy, facts)
    if name == "supporting_policy_source":
        return role == "supports"
    if name == "no_reserved_user_authority":
        return facts.get("explicit_user_authority") != "reserved"
    if name == "undetermined_boundary_element":
        # The mirror of the determining_policy_source rule, and it has to be stated
        # here too: a determining policy source resolves an ordinary element, but not
        # one A4-0 says it cannot resolve. Without this the reserved-authority item
        # left CLEAR (correctly) and lost NEEDS_INPUT (incorrectly), yielding an
        # empty set -- the same "each predicate right alone, wrong together" shape
        # RI3-1 reported, one predicate over.
        resolved_by_policy = role == "determines" and not unresolvable_by_policy
        return bool(triggered) and not resolved_by_policy and not authorized
    if name == "absent_user_intent":
        return facts.get("user_intent_absent") is True
    if name == "unclassifiable_item":
        return facts.get("unclassifiable") is True
    if name == "declared_contradiction":
        # Symmetric with undetermined_boundary_element: A4-0 gives ONE destination
        # per cell, so an authorization relocates the item to CLEAR rather than
        # leaving CONFLICT simultaneously permitted.
        return contradiction and not authorized
    raise DecisionPolicyError(f"unhandled entry predicate {name!r}")


def permitted_states(
    policy: DecisionPolicy, facts: Mapping[str, Any]
) -> frozenset[str]:
    """The states the contract permits for these DECLARED boundary facts.

    FR-4: this EVALUATES each state's entry condition. The previous version fixed the
    result set to {CLEAR, NEEDS_INPUT, CONFLICT} and computed only whether to add
    ASSUMPTION_ALLOWED, so an irreversible, external-system, security-relevant item
    with no policy source and no authorization was reported as permitting CLEAR --
    exactly the automatic approval of an irreversible high-impact decision without
    explicit authority that the ticket forbids. Checking only that ASSUMPTION_ALLOWED
    is absent was a narrower property than the one being claimed.

    Pure function of (contract, declared facts): no I/O, no dispatch, no phase, no
    gate, no wait. This is contract evaluation, not the OS-29 runtime check.

    Risk-independence is STRUCTURAL. Every predicate reads only declared boundary
    elements, the policy-source role, the user_decision, and the four explicitly
    named flags -- never a risk, quality-profile or agent-profile key. There is
    deliberately no `risk` parameter, and a test asserts that via inspect.signature.
    """

    role = _policy_source_role(facts)
    if role is not None and role not in policy.policy_source_roles:
        raise DecisionPolicyError(f"unknown policy_source role {role!r}")
    _validate_declared_facts(policy, facts)

    permitted = set()
    for state, condition in policy.entry_conditions.items():
        (combinator, predicates), = condition.items()
        results = [_evaluate_predicate(name, policy, facts) for name in predicates]
        if (any(results) if combinator == "any_of" else all(results)):
            permitted.add(state)
    return frozenset(permitted)


def validate_record(policy: DecisionPolicy, record: Mapping[str, Any]) -> None:
    """Validate one decision record against the contract. Raises on any violation."""

    state = record.get("state")
    _require(state in policy.states, f"unknown decision state {state!r}")
    spec = policy.states[str(state)]

    code: ReasonCode | None = None
    if spec.reason_code_required:
        name = record.get("reason_code")
        _require(
            isinstance(name, str) and name in policy.reason_codes,
            f"state {state} requires a reason_code from the closed set, got {name!r}",
        )
        code = policy.reason_codes[str(name)]
        _require(
            code.state == state,
            f"reason code {name} belongs to {code.state}, not {state}",
        )
    else:
        _require(
            record.get("reason_code") is None,
            f"state {state} must not carry a reason_code",
        )

    if code is not None and code.boundary_element is not None:
        # FR-3: the record must name the SAME element the code binds. Checking only
        # that the field is non-empty let `security_impact` be filed with
        # boundary_element "privacy", which makes misclassification -- the thing a
        # Reviewer is required to be able to judge -- not machine-checkable.
        declared = record.get("boundary_element")
        _require(
            declared == code.boundary_element,
            f"reason code {code.name} binds boundary element "
            f"{code.boundary_element!r}, but the record declares {declared!r}",
        )
    elif code is not None and "boundary_element" not in code.required_evidence:
        # Positive control for the deliberate override: a code with no bound element
        # (today only unclassifiable_decision) must not smuggle one in.
        _require(
            record.get("boundary_element") is None,
            f"reason code {code.name} binds no boundary element, but the record "
            f"declares {record.get('boundary_element')!r}",
        )

    required = code.required_evidence if code else policy.required_evidence[str(state)]
    for field in required:
        if field == "reason_code":
            continue
        _require(
            field in record and not _is_empty(record[field]),
            f"state {state} requires a non-empty {field!r}",
        )

    _validate_declared_facts(policy, record)

    if state == "CONFLICT":
        minimum = policy.citation_minimum.get("CONFLICT", 2)
        citations = record.get("citations") or ()
        _require(
            len(citations) >= minimum,
            f"CONFLICT requires at least {minimum} citations, got {len(citations)}",
        )

    if state == "ASSUMPTION_ALLOWED":
        source = record.get("policy_source") or {}
        _require(isinstance(source, dict), "policy_source must be an object")
        expected_role = policy.assumption_allowed_requires.get("policy_source_role")
        _require(
            source.get("role") == expected_role,
            f"ASSUMPTION_ALLOWED requires policy_source role {expected_role!r}, "
            f"got {source.get('role')!r}",
        )
        _require(
            not _assumption_allowed_is_forbidden(policy, record),
            "ASSUMPTION_ALLOWED is forbidden for this item (INV-4, no exception)",
        )


def validate_transition(
    policy: DecisionPolicy,
    source: str,
    target: str,
    record: Mapping[str, Any] | None = None,
) -> None:
    """Validate a transition of one decision item. Raises on any violation."""

    rule = transition_rule(policy, source, target)
    record = record or {}
    if rule == "forbidden":
        raise DecisionPolicyError(
            f"transition {source} -> {target} is forbidden unconditionally; "
            "a user_decision does not enable it"
        )
    if rule == "requires_user_decision":
        decision = record.get("user_decision")
        _require(
            isinstance(decision, dict) and decision,
            f"transition {source} -> {target} requires a user_decision record",
        )
        for field in policy.user_decision_fields:
            _require(
                field in decision and not _is_empty(decision[field]),
                f"user_decision requires a non-empty {field!r}",
            )
        # FR-2: user authority is an ALLOWLIST, not an open string minus five tokens.
        # A denylist of spellings cannot enforce a categorical rule -- `high_confidence`
        # and `worker_reviewer_consensus` are the same categories as the listed
        # `model_confidence` and `worker_reviewer_agreement` under a different spelling,
        # and a denylist admits every synonym nobody thought of. Membership in the
        # closed positive vocabulary is the gate; an unknown source is REJECTED.
        claimed = decision.get("source")
        if claimed not in policy.user_decision_sources:
            category = (
                " That source names a category the contract explicitly excludes from"
                " user authority."
                if claimed in policy.forbidden_authority_sources
                else " An unrecognised source is rejected rather than assumed valid."
            )
            raise DecisionPolicyError(
                f"{claimed!r} is not evidence of user authority; the contract admits "
                f"only {sorted(policy.user_decision_sources)}.{category}"
            )
    if rule == "requires_retraction":
        _require(
            not _is_empty(record.get("retraction")),
            f"transition {source} -> {target} requires a recorded retraction",
        )
