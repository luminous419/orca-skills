#!/usr/bin/env python3
"""OS-42: the schema projection, the Worker-facing contract it generates, and the
defect classifier that reads what the agent sent back.

Why this module exists at all, and why it is NOT part of decision_gate.py:
`decision_gate`'s module docstring states its import direction as an invariant -- it
imports `decision_policy` and the standard library and nothing else -- and
`scripts/test_os29_decision_gate.py` enforces that statically by walking its AST. A
generator has to read `decision_gate`'s record constants AND `decision_policy`'s parsed
vocabulary AND be callable from `task_context`'s renderer, so putting it inside
`decision_gate` would either break that test or force `decision_gate` to grow outward
edges. This module imports `decision_gate` instead: the edge points INWARD, and the
static test keeps passing untouched.

The one rule that governs the whole file: **the projection is DERIVED, never
transcribed.** Not one enum token is a string literal here. Every value comes from
`decision_gate`'s constants or from the `decision_policy` block parsed out of SKILL.md,
so a policy edit that is not regenerated is a drift failure rather than a silent lie in
a prompt -- which is exactly the defect OS-42 exists to remove.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # imported as `scripts.decision_contract` and as a top-level module
    from scripts import decision_gate
    from scripts.decision_policy import (
        DecisionPolicy,
        DecisionPolicyError,
        codes_for_state,
        load_decision_policy,
        validate_record,
    )
except ModuleNotFoundError:  # pragma: no cover - flat installed Skill layout
    import decision_gate  # type: ignore[no-redef]
    from decision_policy import (  # type: ignore[no-redef]
        DecisionPolicy,
        DecisionPolicyError,
        codes_for_state,
        load_decision_policy,
        validate_record,
    )

CONTRACT_PROJECTION_VERSION: int = 1

# ---- the settlement gate envelope -------------------------------------------------
# The closed shape `result["gate"]` carries. It is duplicated as a frozen tuple inside
# deterministic_workflow/contracts.py, whose parity with this one is asserted by a test:
# `contracts.py` is the runtime-neutral core of the shipped engine and takes no sibling
# import, and a six-name tuple pinned by an equality test is the smaller risk. It is the
# same trade the repository already makes for decision_gate.FIELD_LINE vs
# e2e_harness.FIELD_LINE.
GATE_ENVELOPE_KEYS: tuple[str, ...] = (
    "declared_state",
    "declaration_count",
    "fence_count",
    "record",
    "record_text",
    "truncated",
)
RECORD_TEXT_MAX_BYTES = 8192

# ---- the generated block's delimiters ---------------------------------------------
# Spelled here and re-exported through task_context so the renderer and the reader
# cannot disagree about where the block starts.
CONTRACT_BLOCK_HEADER = "=== DECISION GATE CONTRACT (generated) ==="
CONTRACT_BLOCK_FOOTER = "=== END DECISION GATE CONTRACT ==="
REPAIR_BLOCK_HEADER = "=== VALIDATION REPAIR ==="
REPAIR_BLOCK_FOOTER = "=== END VALIDATION REPAIR ==="

# The boundary each role records at. Derived from decision_gate's own identity table so
# a role/boundary pair can never be mismatched by a caller.
_BOUNDARY_BY_SOURCE = {
    source: boundary for boundary, source, _ in decision_gate.AGENT_TERMINAL_IDENTITIES
}
_AGENT_ROLE_ALIASES = {
    "WORKER": "worker",
    "worker": "worker",
    "PHASE_REVIEWER": "reviewer",
    "FINAL_REVIEWER": "reviewer",
    "reviewer": "reviewer",
    "final_reviewer": "reviewer",
}
# The whole (boundary, source, role) triple each dispatch role must record at, read from
# `decision_gate`'s own identity table rather than restated. `_BOUNDARY_BY_SOURCE` above
# gives the boundary; this gives the triple `classify_gate` compares against.
_IDENTITY_BY_SOURCE: dict[str, tuple[str, str, str]] = {
    source: (boundary, source, role)
    for boundary, source, role in decision_gate.AGENT_TERMINAL_IDENTITIES
}
# The mechanics fields whose VALUES are checked against their own closed domain before
# the relational identity rule is consulted. Order is the reporting order.
MECHANICS_IDENTITY_FIELDS: tuple[str, ...] = ("boundary", "source", "role")
# The three fields that bind a record to ONE dispatch. `iteration` is the one-based
# `gate_iteration` the contract was rendered with, never `repair_attempt`.
BINDING_FIELDS: tuple[str, ...] = ("run", "phase", "iteration")
# The one-based gate-attempt domain, mirroring `artifact_identity.ATTEMPT_MIN`. Spelled
# here rather than imported: this module may not import the engine package (the edge
# points the other way), and one integer pinned by a test is the smaller risk.
ITERATION_MIN: int = 1


def expected_identity(role: str) -> tuple[str, str, str]:
    """The (boundary, source, role) triple the named dispatch role must record at.

    Raises for a role that names no agent boundary; `classify_gate` never lets that
    reach here -- it turns the same condition into a fail-closed LIFECYCLE defect,
    because a validator must not raise into VALIDATE_SETTLEMENT.
    """
    source = _AGENT_ROLE_ALIASES.get(role)
    if source is None:
        raise ValueError(f"unknown role for the decision gate contract: {role!r}")
    return _IDENTITY_BY_SOURCE[source]


class DecisionPolicyRequired(RuntimeError):
    """The decision policy could not be loaded, so no gate can be validated.

    Raised at graph BUILD time, mirroring DurableCheckpointerRequired and
    IdempotencyPortRequired: a graph that cannot check a gate must not be constructed.
    There is deliberately no default policy and no silent skip -- "we could not read the
    contract" must never read the same as "the contract was satisfied".
    """


# ---- policy resolution --------------------------------------------------------------

SKILL_DIR_ENV = "ORCA_SKILL_DIR"
SKILL_FILENAME = "SKILL.md"
_ORCHESTRATION_SKILL = "orca-worker-reviewer-orchestration"


def candidate_skill_paths(skill_path: str | Path | None = None) -> tuple[Path, ...]:
    """Where a SKILL.md may be, in resolution order. Pure; performs no I/O."""
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    if skill_path is not None:
        given = Path(skill_path)
        candidates.append(given if given.is_file() else given / SKILL_FILENAME)
    override = os.environ.get(SKILL_DIR_ENV)
    if override:
        candidates.append(Path(override) / SKILL_FILENAME)
    # installed layout: this file is <skill root>/tools/decision_contract.py
    candidates.append(here.parent.parent / SKILL_FILENAME)
    # repository layout: this file is <repo>/scripts/decision_contract.py
    candidates.append(here.parent.parent / _ORCHESTRATION_SKILL / SKILL_FILENAME)
    return tuple(candidates)


def resolve_policy(skill_path: str | Path | None = None) -> DecisionPolicy:
    """Load the decision policy, fail-closed.

    Never re-parses SKILL.md itself: it calls the existing
    `decision_policy.load_decision_policy`, so the block has exactly one parser.
    """
    if skill_path is not None:
        # An EXPLICIT path that does not exist is an error, never a reason to fall back:
        # silently resolving a different policy than the caller named is the shape of
        # failure this whole module exists to remove.
        given = Path(skill_path)
        resolved = given if given.is_file() else given / SKILL_FILENAME
        if not resolved.is_file():
            raise DecisionPolicyRequired(
                f"DECISION_POLICY_REQUIRED: {resolved} does not exist")
    tried: list[str] = []
    for candidate in candidate_skill_paths(skill_path):
        tried.append(str(candidate))
        if not candidate.is_file():
            continue
        try:
            return load_decision_policy(candidate)
        except (DecisionPolicyError, OSError) as exc:
            raise DecisionPolicyRequired(
                f"DECISION_POLICY_REQUIRED: {candidate} could not be read as a decision "
                f"policy: {exc}"
            ) from exc
    raise DecisionPolicyRequired(
        "DECISION_POLICY_REQUIRED: no SKILL.md found; tried " + ", ".join(tried)
    )


# ---- the projection -----------------------------------------------------------------


@dataclass(frozen=True)
class MachineControlField:
    """One machine-control field, as the contract declares it."""

    name: str
    kind: str
    values: tuple[str, ...]
    minimum: int | None


@dataclass(frozen=True)
class ContractProjection:
    """Everything a Worker must be told, read out of the schema and nothing else."""

    projection_version: int
    ledger_schema_version: int
    states: tuple[str, ...]
    required_fields: tuple[str, ...]
    mechanics_fields: tuple[str, ...]
    closed_key_set: tuple[str, ...]
    reason_codes_by_state: Mapping[str, tuple[str, ...]]
    required_evidence_by_code: Mapping[str, tuple[str, ...]]
    required_evidence_by_state: Mapping[str, tuple[str, ...]]
    machine_control: tuple[MachineControlField, ...]
    policy_source_kinds: tuple[str, ...]
    policy_source_roles: tuple[str, ...]

    def enum_tokens(self) -> tuple[str, ...]:
        """Every closed-enum token the contract declares, for the drift check."""
        tokens: list[str] = list(self.states)
        for codes in self.reason_codes_by_state.values():
            tokens.extend(codes)
        for field in self.machine_control:
            tokens.extend(field.values)
        tokens.extend(self.policy_source_kinds)
        tokens.extend(self.policy_source_roles)
        return tuple(dict.fromkeys(tokens))


def contract_projection(policy: DecisionPolicy) -> ContractProjection:
    """The single projection. Every field is read; none is written by hand."""
    return ContractProjection(
        projection_version=CONTRACT_PROJECTION_VERSION,
        ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
        states=tuple(decision_gate.DECISION_STATES),
        required_fields=tuple(decision_gate.REQUIRED_LEDGER_RECORD_FIELDS),
        mechanics_fields=tuple(decision_gate.LEDGER_MECHANICS_FIELDS),
        closed_key_set=tuple(sorted(decision_gate.CLOSED_LEDGER_RECORD_FIELDS)),
        reason_codes_by_state={
            state: codes_for_state(policy, state)
            for state in decision_gate.DECISION_STATES
        },
        required_evidence_by_code={
            name: tuple(code.required_evidence)
            for name, code in policy.reason_codes.items()
        },
        required_evidence_by_state={
            state: tuple(values) for state, values in policy.required_evidence.items()
        },
        machine_control=tuple(
            MachineControlField(
                name=name,
                kind=spec.kind,
                values=tuple(spec.values),
                minimum=spec.minimum,
            )
            for name, spec in policy.boundary_elements.items()
        ),
        policy_source_kinds=tuple(policy.policy_source_kinds),
        policy_source_roles=tuple(policy.policy_source_roles),
    )


# ---- the generated Worker contract ---------------------------------------------------


def _skeleton(projection: ContractProjection, *, state: str, run_id: str, phase: str,
              iteration: int, boundary: str, source: str) -> str:
    record: dict[str, Any] = {
        "ledger_schema_version": projection.ledger_schema_version,
        "boundary": boundary,
        "source": source,
        "role": source,
        "run": run_id,
        "phase": phase,
        "iteration": iteration,
        "responsible_phase": phase,
        "state": state,
        "reason_code": None,
        "open_decision_item": False,
        "open_item": None,
        "assumption": None,
        "evidence": {},
        "verdict": "",
        "source_binding": f"artifacts/runs/{run_id}/",
        "recorded_at": "<the real current UTC time, RFC3339>",
        "prior_open_decision_items": [],
    }
    codes = projection.reason_codes_by_state.get(state, ())
    if codes:
        record["reason_code"] = f"<one of: {' | '.join(codes)}>"
        for field in projection.required_evidence_by_code.get(codes[0], ()):  # noqa: B007
            if field == "reason_code":
                continue
            record.setdefault(field, f"<required for this reason_code>")
    return json.dumps(record, indent=2, sort_keys=False)


def render_worker_contract(
    projection: ContractProjection,
    *,
    run_id: str,
    phase: str,
    iteration: int,
    role: str,
) -> str:
    """The block an agent actually reads. A pure function of its arguments.

    `boundary` is DERIVED from `role` rather than passed, so a caller cannot pair a role
    with the wrong boundary. Every enum token below is interpolated from `projection`;
    none is a literal in this function, which is what
    `test_renderer_contains_no_enum_string_literal` asserts.
    """
    source = _AGENT_ROLE_ALIASES.get(role)
    if source is None:
        raise ValueError(f"unknown role for the decision gate contract: {role!r}")
    boundary = _BOUNDARY_BY_SOURCE[source]
    lines: list[str] = [CONTRACT_BLOCK_HEADER, ""]
    lines.append(
        "Generated from the live schema (decision_gate.py + the decision_policy block "
        "of SKILL.md). Do not paraphrase it and do not copy values from any other "
        "document."
    )
    lines.append("")
    lines.append("Your result body MUST contain BOTH of these exactly once, and they")
    lines.append("MUST agree:")
    lines.append(
        f"  1. the declaration line  {decision_gate.GATE_STATE_FIELD}: "
        f"<one of {list(projection.states)}>"
    )
    lines.append(
        f"  2. exactly one fenced {decision_gate.GATE_RECORD_FENCE} JSON record "
        "  <- THIS IS THE AUTHORITY"
    )
    lines.append("")
    lines.append(f"REQUIRED fields ({len(projection.required_fields)}):")
    lines.append(f"  {list(projection.required_fields)}")
    lines.append("Plus the mechanics fields for your boundary:")
    lines.append(
        f"  ledger_schema_version={projection.ledger_schema_version}  "
        f"boundary={boundary!r}  source={source!r}  role={source!r}"
    )
    lines.append("")
    lines.append("CLOSED KEY SET. Any key outside this set is a hard rejection:")
    lines.append(f"  {list(projection.closed_key_set)}")
    lines.append("")
    lines.append(f"`state` : CLOSED ENUM -> exactly one of {list(projection.states)}")
    lines.append("")
    lines.append("`reason_code` is determined by `state`:")
    for state in projection.states:
        codes = projection.reason_codes_by_state.get(state, ())
        if codes:
            lines.append(f"  `{state}` -> `reason_code` MUST be exactly one of {list(codes)}")
        else:
            lines.append(f"  `{state}` -> `reason_code` MUST be JSON null")
    lines.append("")
    lines.append(
        "MACHINE-CONTROL fields. These are TOP-LEVEL record keys, NOT nested inside"
    )
    lines.append("`evidence`. They carry enum tokens, not prose:")
    for field in projection.machine_control:
        if field.kind == "enum":
            lines.append(
                f"  `{field.name}` : CLOSED ENUM -> exactly one of {list(field.values)}"
            )
        elif field.kind in ("boolean", "declared"):
            lines.append(
                f"  `{field.name}` : JSON boolean -> true or false "
                "(never a string, never a sentence)"
            )
        elif field.kind == "citations":
            minimum = field.minimum or 0
            lines.append(
                f"  `{field.name}` : JSON array of non-empty text, at least {minimum}"
            )
        elif field.kind == "policy_source":
            lines.append(
                f"  `policy_source` : OBJECT -> "
                f'{{"kind": <one of {list(projection.policy_source_kinds)}>, '
                f'"locator": "<non-empty text>", '
                f'"role": <one of {list(projection.policy_source_roles)}>}}'
            )
        elif field.kind == "user_decision":
            lines.append(
                f"  `{field.name}` : declared ONLY when it applies; "
                f"admits {list(field.values) or list(_triggering(field))}"
            )
    lines.append("")
    lines.append("Fields each reason_code additionally requires:")
    for state in projection.states:
        for code in projection.reason_codes_by_state.get(state, ()):
            extra = [
                name
                for name in projection.required_evidence_by_code.get(code, ())
                if name != "reason_code"
            ]
            lines.append(f"  reason_code `{code}` -> also set TOP-LEVEL fields {extra}")
    lines.append("")
    lines.append("HARD RULES:")
    lines.append(
        "  * NEVER put a natural-language sentence in a closed-enum field. Legal values"
    )
    lines.append("    are the bare enum tokens above and nothing else.")
    lines.append(
        "  * Prose belongs in the free-text fields the contract names, never in an enum"
    )
    lines.append("    or boolean field.")
    lines.append("  * Booleans are JSON true/false, never a quoted string.")
    lines.append("  * Enum tokens are lower_snake_case exactly as written.")
    lines.append(f"  * Emit exactly ONE {decision_gate.GATE_RECORD_FENCE} fence.")
    lines.append(
        "  * Never downgrade a real "
        f"{'/'.join(decision_gate.BLOCKING_STATES)} to "
        f"{projection.states[0]} to make the gate pass."
    )
    lines.append("")
    for state in projection.states:
        lines.append(f"SKELETON -- state {state}:")
        lines.append(decision_gate.GATE_RECORD_FENCE)
        lines.append(
            _skeleton(projection, state=state, run_id=run_id, phase=phase,
                      iteration=iteration, boundary=boundary, source=source)
        )
        lines.append("```")
        lines.append("")
    lines.append(CONTRACT_BLOCK_FOOTER)
    return "\n".join(lines)


def _triggering(field: MachineControlField) -> tuple[str, ...]:
    return field.values


# ---- the repair instruction ----------------------------------------------------------


def render_repair_instruction(
    instruction: Mapping[str, Any], projection: ContractProjection
) -> str:
    """The block a REPAIR dispatch carries, and nothing else.

    A pure function of the closed `instruction` structure plus the projection. It takes
    no other argument, so there is no input from which a suggested value could enter --
    which, together with the closed key sets of `GateDefect` and the instruction itself,
    is the STRUCTURAL reason the Coordinator cannot infer an enum for the agent.

    `expected` is rendered as the WHOLE allowed set, never a member of it. A rendering
    that named one token would be the semantic laundering the ticket forbids, and
    `test_repair_block_lists_the_entire_allowed_set` fails if it ever does.
    """
    attempt = instruction["attempt"]
    max_attempts = instruction["max_attempts"]
    lines = [
        REPAIR_BLOCK_HEADER,
        "",
        f"attempt: {attempt} of {max_attempts}",
        "Your previous decision-gate record was rejected before any Reviewer saw it.",
        "Fix ONLY the defects listed. Do not change your decision.",
        "",
    ]
    for index, defect in enumerate(instruction["defects"], start=1):
        lines.append(f"defect {index}")
        lines.append(f"  code:           {defect['code']}")
        lines.append(f"  field:          {defect['field_path'] or '<whole record>'}")
        lines.append(f"  you sent:       {defect['actual']}")
        allowed = defect.get("expected") or ()
        lines.append(
            "  allowed values: "
            + (" | ".join(str(value) for value in allowed) if allowed
               else "<see the contract block above>")
        )
        lines.append(f"  detail:         {defect['message']}")
        lines.append("")
    lines.append(
        f"The full contract is in the {CONTRACT_BLOCK_HEADER} block above; this block "
        "adds no value of its own."
    )
    lines.append(REPAIR_BLOCK_FOOTER)
    del projection  # read for its type only; nothing is interpolated from it here
    return "\n".join(lines)


# ---- the settlement parser: it TRANSPORTS, it never classifies -------------------------


def extract_gate_envelope(body: str) -> dict[str, Any]:
    """Read the two halves of a gate declaration out of an agent's Markdown body.

    Uses `decision_gate`'s OWN regexes, never a second copy, so "what counts as a
    declaration line" and "what counts as a fence" have exactly one definition.

    This function makes no judgement whatsoever. A `reversibility` sentence is carried
    verbatim in `record`; no fence yields `fence_count: 0`; an unparseable fence yields
    `record: None` with the text preserved in `record_text`. That is what lets the
    defect reach the classifier instead of dying in the adapter as
    MALFORMED_ORCA_SETTLEMENT_BODY, which is the whole OS-42 failure.
    """
    text = body if isinstance(body, str) else ""
    declarations = [
        match.group("value")
        for match in decision_gate.FIELD_LINE.finditer(text)
        if match.group("field") == decision_gate.GATE_STATE_FIELD
    ]
    fences = [
        match.group("body") for match in decision_gate.GATE_RECORD_BLOCK.finditer(text)
    ]
    record: dict[str, Any] | None = None
    record_text: str | None = None
    truncated = False
    if len(fences) == 1:
        raw = fences[0]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            record = parsed
        else:
            encoded = raw.encode("utf-8")
            truncated = len(encoded) > RECORD_TEXT_MAX_BYTES
            record_text = (
                encoded[:RECORD_TEXT_MAX_BYTES].decode("utf-8", "ignore")
                if truncated else raw
            )
    return {
        "declared_state": declarations[0] if len(declarations) == 1 else None,
        "declaration_count": len(declarations),
        "fence_count": len(fences),
        "record": record,
        "record_text": record_text,
        "truncated": truncated,
    }


def parse_agent_settlement(attempt: Any, intent: Mapping[str, Any]) -> dict[str, Any]:
    """`OrcaAdapter`'s default result parser: build `result`, including `result["gate"]`.

    A JSON body still works exactly as before, so every scripted test and the
    FakeAdapter path is unchanged. A Markdown body -- what a real agent emits -- is read
    for its field lines and its fenced record instead of being refused outright.

    It raises only when the body cannot be READ at all. Everything the classifier is
    meant to judge is transported, not rejected here.
    """
    try:
        body = attempt.body
    except AttributeError as exc:  # pragma: no cover - defensive
        raise ValueError("MALFORMED_ORCA_SETTLEMENT_BODY") from exc
    if not isinstance(body, str):
        raise ValueError("MALFORMED_ORCA_SETTLEMENT_BODY")
    result: dict[str, Any]
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        result = dict(parsed)
    else:
        result = {}
        for match in decision_gate.FIELD_LINE.finditer(body):
            field, value = match.group("field"), match.group("value")
            if field == "STATUS":
                result["status"] = value
            elif field == "RESULT":
                result["result"] = value
    if "gate" not in result:
        result["gate"] = extract_gate_envelope(body)
    del intent  # the signature is the adapter's; the parser needs nothing from it
    return result


# ---- the classifier -------------------------------------------------------------------


def _defect(code: str, kind: str, field_path: str, expected: Sequence[str], actual: Any,
            message: str) -> decision_gate.GateDefect:
    return decision_gate.GateDefect(
        code=code,
        kind=kind,
        field_path=field_path,
        expected=tuple(expected),
        actual=decision_gate.truncate_actual(actual),
        message=message,
    )


# ---- the mechanics identity, and the dispatch it is bound to (OS-42 F-001) -----------
# `classify_gate` used to accept its expected `role` and discard it, and no
# classification path ever called `decision_gate.record_identity_defect()` or
# `validate_ledger_record()`. A Worker settlement could therefore declare the Reviewer's
# identity, an impossible boundary/source/role combination, or an unknown boundary, and
# the production classifier returned no defect at all.
#
# The SPLIT below is the contract, and it is what keeps FORM and LIFECYCLE honest:
#
#   * a mechanics field whose VALUE is outside its own closed domain -- an unknown
#     boundary, a source that is not a source, a schema version this build cannot read --
#     is a correctable format error. It classifies FORM, so the bounded repair path acts
#     on it exactly as it does for a natural-language `reversibility`.
#   * a triple whose three fields are each in-domain but which is NOT a real record
#     identity, or which IS one but not THIS dispatch's, is identity forgery. It
#     classifies LIFECYCLE, and `routing.route` never repairs a LIFECYCLE defect, so it
#     fails closed instead of being repaired into acceptance.
#
# The relational half DELEGATES to `decision_gate.record_identity_defect`, the validator
# every persisted ledger already goes through, rather than growing a second identity
# table beside it.

_MECHANICS_DOMAINS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("boundary", decision_gate.BOUNDARIES),
    ("source", decision_gate.SOURCES),
    ("role", decision_gate.ROLES),
)


def _mechanics_form_defects(
    record: Mapping[str, Any]
) -> tuple[decision_gate.GateDefect, ...]:
    """Per-FIELD domain errors in the mechanics block. All of them, never just the first.

    `boundary`, `source` and `ledger_schema_version` are members of
    `LEDGER_MECHANICS_FIELDS`, not of `REQUIRED_LEDGER_RECORD_FIELDS`, so
    `collect_form_defects` never noticed them missing. A record with no boundary at all
    claims no identity and must not pass.

    Absence is a defect on BOTH paths, and deliberately the same one. The live OS-29
    ingress used to suppress these; round-3 F-001 removed that exemption, because a
    dispatched record is GIVEN these fields by the generated contract and omitting them
    is a correctable format error rather than a division of labour.
    """
    defects: list[decision_gate.GateDefect] = []
    supported = decision_gate.SUPPORTED_LEDGER_RECORD_SCHEMA_VERSIONS
    allowed_versions = tuple(str(version) for version in supported)
    if "ledger_schema_version" not in record:
        defects.append(_defect(
            decision_gate.GATE_INPUT_MALFORMED, "FORM", "ledger_schema_version",
            allowed_versions, None,
            "the mechanics field 'ledger_schema_version' is absent",
        ))
    else:
        version = record["ledger_schema_version"]
        # `bool` first: isinstance(True, int) is True, the same trap the record's own
        # type sweep closes.
        if (type(version) is bool or not isinstance(version, int)
                or version not in supported):
            defects.append(_defect(
                decision_gate.GATE_INPUT_MALFORMED, "FORM", "ledger_schema_version",
                allowed_versions, version,
                "'ledger_schema_version' is outside the set this build supports",
            ))
    for name, domain in _MECHANICS_DOMAINS:
        if name not in record:
            defects.append(_defect(
                decision_gate.GATE_INPUT_MALFORMED, "FORM", name, domain, None,
                f"the mechanics field {name!r} is absent",
            ))
        elif record[name] not in domain:
            defects.append(_defect(
                decision_gate.GATE_INPUT_MALFORMED, "FORM", name, domain, record[name],
                f"{name!r} is outside its closed set",
            ))
    return tuple(defects)


def _binding_domain_defects(
    record: Mapping[str, Any], binding: Mapping[str, Any] | None
) -> tuple[decision_gate.GateDefect, ...]:
    """Binding values outside their DOMAIN. FORM, and therefore repairable.

    Only `iteration` needs an arm of its own: `run` and `phase` are declared `str` and
    `iteration` `int` by `decision_gate.LEDGER_FIELD_TYPES`, so a wrongly-TYPED value is
    already a FORM defect from the record's own type sweep and reporting it twice would
    spend one repair attempt on one mistake. What that sweep cannot see is the RANGE:
    `iteration` is a one-based ordinal (`artifact_identity.ATTEMPT_MIN`), so `0` is not a
    foreign dispatch -- it is no dispatch at all, i.e. a format error.
    """
    if not binding:
        return ()
    defects: list[decision_gate.GateDefect] = []
    # ABSENCE FIRST. A record that does not say which run, phase or gate attempt it
    # belongs to has made no claim this validator can check -- and it must not be given
    # one. That is the round-3 correction: the generated contract hands every dispatched
    # record these three values, so omitting them is a correctable FORM error the bounded
    # repair loop re-asks, never an invitation to fill them in.
    for name in BINDING_FIELDS:
        if binding.get(name) is not None and record.get(name) is None:
            defects.append(_defect(
                decision_gate.GATE_INPUT_MALFORMED, "FORM", name,
                (str(binding[name]),), None,
                f"the record does not declare which {name} it belongs to",
            ))
    value = record.get("iteration")
    if type(value) is int and value < ITERATION_MIN:
        defects.append(_defect(
            decision_gate.GATE_INPUT_MALFORMED, "FORM", "iteration",
            (f">= {ITERATION_MIN}",), value,
            "`iteration` is outside the one-based gate-attempt domain",
        ))
    return tuple(defects)


def _identity_defects(
    record: Mapping[str, Any], *, role: str, binding: Mapping[str, Any] | None = None
) -> tuple[decision_gate.GateDefect, ...]:
    """The record's IDENTITY: who it claims to be, and which dispatch it claims to be of.

    Round-2 F-001 draws the line this function enforces, and both directions are
    load-bearing:

    * an UNKNOWN token -- `boundary: "B9"`, `source: "banana"` -- is outside its closed
      set. It names no agent and no dispatch, so it is a FORMAT error, stays with
      `_mechanics_form_defects`, and stays repairable. This function returns nothing for
      it, which is why every arm below first checks that the value is in its domain.
    * a WELL-FORMED but FOREIGN value -- `B3` on a Worker settlement, `run_foreign`,
      another phase, another gate iteration -- is a CLAIM about which dispatch produced
      the record. `run` and `phase` have no closed enum the schema declares and
      `iteration` is any one-based ordinal, so for those three every well-formed value is
      such a claim. A claim naming another dispatch is forgery: LIFECYCLE, which
      `routing.route` never repairs, so it fails closed instead of being re-asked.

    The boundary/source/role triple is judged as ONE unit and reported as one defect:
    they are not three independent facts but one identity, and three defects would spend
    three repair budget entries -- and would read as three mistakes -- for one forgery.
    Only the DECLARED members are compared, so the ingress reading (where absence is not
    a claim) and the engine reading (where absence is already a FORM defect) share this
    code without either inventing a claim the record never made.
    """
    try:
        expected = expected_identity(role)
    except ValueError:
        # A dispatch role with no decision-gate boundary. Fail closed with a defect
        # rather than raising: this function runs inside VALIDATE_SETTLEMENT, where an
        # exception would crash the run instead of blocking it.
        return (_defect(
            decision_gate.GATE_INPUT_UNBOUND, "LIFECYCLE", "role",
            tuple(sorted(set(_AGENT_ROLE_ALIASES))), role,
            f"no decision-gate identity is defined for dispatch role {role!r}",
        ),)
    defects: list[decision_gate.GateDefect] = []
    domains = dict(_MECHANICS_DOMAINS)
    # `expected_identity` returns the triple in MECHANICS_IDENTITY_FIELDS order; the
    # alignment is pinned by `test_the_identity_triple_and_its_field_names_stay_aligned`.
    declared = [(name, record[name], want)
                for name, want in zip(MECHANICS_IDENTITY_FIELDS, expected)
                if name in record]
    # Every declared member must be inside its own closed set before the triple means
    # anything at all; otherwise `B9` would be reported as another agent's identity.
    in_domain = all(value in domains[name] for name, value, _want in declared)
    if declared and in_domain and any(value != want for _name, value, want in declared):
        claimed = tuple(value for _name, value, _want in declared)
        defects.append(_defect(
            decision_gate.GATE_INPUT_UNBOUND, "LIFECYCLE",
            "/".join(MECHANICS_IDENTITY_FIELDS), ("/".join(expected),), claimed,
            f"this dispatch records at {'/'.join(expected)}; the record declares "
            f"{'/'.join(str(value) for value in claimed)}, which is another agent's "
            "identity and is never repaired into acceptance",
        ))
    defects.extend(_binding_identity_defects(record, binding))
    if defects:
        return tuple(defects)
    if not in_domain:
        # An out-of-domain member makes the triple unreadable as an identity. It is a
        # FORMAT error and `_mechanics_form_defects` already owns it; asking the
        # relational validator here would report `B9` as "not one of RECORD_IDENTITIES",
        # i.e. as forgery -- the exact inversion the round-2 review forbids.
        return ()
    # Nothing above fired, so the triple IS this dispatch's identity. The relational
    # validator the persisted ledger already goes through still has one clause left to
    # apply -- `verifies` is a claim only the Reviewer B3 form may make -- and it is read
    # from `decision_gate` rather than restated here.
    detail = decision_gate.record_identity_defect(record) if all(
        name in record for name in MECHANICS_IDENTITY_FIELDS) else None
    if detail is not None:
        return (_defect(
            decision_gate.GATE_INPUT_UNBOUND, "LIFECYCLE",
            "/".join(MECHANICS_IDENTITY_FIELDS), ("/".join(expected),),
            decision_gate.record_identity(record),
            f"the record claims no real ledger identity: {detail}",
        ),)
    return ()


def _binding_identity_defects(
    record: Mapping[str, Any], binding: Mapping[str, Any] | None
) -> tuple[decision_gate.GateDefect, ...]:
    """`run` / `phase` / `iteration` against the ACTIVE dispatch.

    LIFECYCLE, and this is the round-2 correction: these used to classify FORM, so
    `route_node` PREPARED REPAIR for a record claiming another run, another phase or
    another gate iteration -- an identity claim being re-asked instead of refused.

    `phase` is compared case-insensitively because the engine spells it `ANALYSIS` and
    the rendered contract spells it `analysis`; a case-sensitive check would reject every
    conforming record. A value whose TYPE is wrong is left to the type sweep: it is a
    format error, not a claim.
    """
    if not binding:
        return ()
    defects: list[decision_gate.GateDefect] = []
    expected_run = binding.get("run")
    actual_run = record.get("run")
    if expected_run and isinstance(actual_run, str) and actual_run != expected_run:
        defects.append(_defect(
            decision_gate.GATE_INPUT_UNBOUND, "LIFECYCLE", "run", (expected_run,),
            actual_run, "the record claims to belong to a different run",
        ))
    expected_phase = binding.get("phase")
    actual_phase = record.get("phase")
    if (expected_phase and isinstance(actual_phase, str)
            and actual_phase.strip().lower() != str(expected_phase).strip().lower()):
        defects.append(_defect(
            decision_gate.GATE_INPUT_UNBOUND, "LIFECYCLE", "phase",
            (str(expected_phase),), actual_phase,
            "the record claims to belong to a different phase",
        ))
    expected_iteration = binding.get("iteration")
    actual_iteration = record.get("iteration")
    if (isinstance(expected_iteration, int) and type(actual_iteration) is int
            and actual_iteration >= ITERATION_MIN
            and actual_iteration != expected_iteration):
        defects.append(_defect(
            decision_gate.GATE_INPUT_UNBOUND, "LIFECYCLE", "iteration",
            (str(expected_iteration),), actual_iteration,
            "the record claims to belong to a different gate iteration",
        ))
    # `source_binding` is deliberately NOT checked here. It is not a mechanics field --
    # `decision_gate.LEDGER_MECHANICS_FIELDS` does not contain it -- and the live harness
    # already OWNS it unconditionally: an earlier external review made it harness-written
    # rather than agent-supplied precisely so a record could not claim provenance it was
    # never bound to, and `test_an_agent_cannot_supply_its_own_source_binding` pins that
    # overwrite. Refusing the settlement instead would replace a reviewed guarantee with
    # a different one, for a field the round-2 finding does not name.
    return tuple(defects)


def mechanics_identity_defects(
    record: Mapping[str, Any], *, role: str, binding: Mapping[str, Any] | None
) -> tuple[decision_gate.GateDefect, ...]:
    """The COMPLETE mechanics identity of a raw agent record, for the live OS-29 ingress.

    Round-2 F-001. `OrcaRuntimeHarness._record_decision_from_attempt` is the FIRST live
    consumer of a settlement and runs long before any engine node: it parsed the record
    with `parse_gate_result` -- which binds nothing to the active dispatch -- and then
    overwrote `run`, `phase`, `iteration`, `boundary`, `source` and `role` with the
    Coordinator's own values before appending the result to the decision ledger. A
    foreign identity was therefore not rejected but REWRITTEN into a valid-looking row,
    and no later classifier can withdraw an accepted ledger write.

    Round 3 removes the remaining asymmetry. The first version of this function judged
    only the fields a record DECLARED, on the argument that the Coordinator owns the
    mechanics block and absence is a division of labour. It is not: OS-42's own contract
    GIVES every dispatched record the schema's required mechanics fields, so a record
    that omits them has produced a correctable format error -- precisely what bounded
    repair exists to re-ask. Two validators disagreeing about whether omission is a
    defect, with the permissive one running first and writing the ledger, is the same
    irreversible ordering problem in a different shape.

    So this is now the SAME judgement `classify_gate` makes, reached through the same
    helpers: ABSENT required mechanics and ABSENT binding fields are FORM, an
    OUT-OF-DOMAIN token is FORM, and a WELL-FORMED value naming another dispatch is
    LIFECYCLE. It returns one kind or the other, never both, so the caller's
    repairable/fail-closed decision stays a single equality test.
    """
    domain = (_mechanics_form_defects(record)
              + _binding_domain_defects(record, binding))
    if domain:
        return domain
    return _identity_defects(record, role=role, binding=binding)


def classify_gate(
    policy: DecisionPolicy,
    envelope: Mapping[str, Any] | None,
    *,
    role: str,
    binding: Mapping[str, Any] | None = None,
) -> tuple[decision_gate.GateDefect, ...]:
    """FORM / SEMANTIC / LIFECYCLE, in the one order that keeps them apart.

    Returns () when the gate is acceptable. A defect is a RETURN VALUE, never an
    exception: only a programming error raises here.

    The ordering IS the taxonomy. Every FORM check runs first and every one of them is
    derived from the contract's own declared domains, so once they are clean anything
    `validate_record` still rejects is BY CONSTRUCTION a judgement -- INV-4, undeclared
    safety facts, clause proof, boundary-element binding, empty required evidence,
    grounds, the CONFLICT citation minimum. That is why `decision_policy.py` needs no
    behavioural change and why a judgement can never be labelled FORM and re-asked.

    `role` is the identity THIS dispatch was sent out under, and it is READ, not
    documentation: the record's boundary/source/role triple must be the one that role
    records at. `binding` is the active task/dispatch context -- `{"run", "phase",
    "iteration"}`, the three values the generated contract was rendered with. Omitting it
    checks no binding, which is what the standalone CLI does: it validates a body that
    has no dispatch behind it and must not invent one.
    """
    if envelope is None:
        return (_defect(
            decision_gate.GATE_INPUT_MISSING, "FORM", "", (), None,
            "the settlement carried no decision-gate declaration at all",
        ),)
    declarations = envelope.get("declaration_count")
    fences = envelope.get("fence_count")
    if declarations != 1:
        code = (decision_gate.GATE_INPUT_MISSING if declarations == 0
                else decision_gate.GATE_INPUT_MALFORMED)
        return (_defect(
            code, "FORM", decision_gate.GATE_STATE_FIELD, ("exactly 1",), declarations,
            f"found {declarations} {decision_gate.GATE_STATE_FIELD} declaration lines, "
            "expected exactly one",
        ),)
    if fences != 1:
        code = (decision_gate.GATE_INPUT_MISSING if fences == 0
                else decision_gate.GATE_INPUT_MALFORMED)
        return (_defect(
            code, "FORM", decision_gate.GATE_RECORD_FENCE, ("exactly 1",), fences,
            f"found {fences} {decision_gate.GATE_RECORD_FENCE} fences, expected exactly one",
        ),)
    record = envelope.get("record")
    if record is None:
        return (_defect(
            decision_gate.GATE_INPUT_MALFORMED, "FORM",
            decision_gate.GATE_RECORD_FENCE, ("<a JSON object>",),
            envelope.get("record_text"),
            "the decision-gate fence does not contain a JSON object",
        ),)
    declared_state = envelope.get("declared_state")
    if declared_state not in decision_gate.DECISION_STATES:
        return (_defect(
            decision_gate.GATE_INPUT_MALFORMED, "FORM",
            decision_gate.GATE_STATE_FIELD, decision_gate.DECISION_STATES,
            declared_state,
            "the declared state is outside the closed set",
        ),)
    if record.get("state") != declared_state:
        return (_defect(
            decision_gate.SUMMARY_DISAGREES_WITH_RECORD, "FORM", "state",
            (declared_state,), record.get("state"),
            "the declaration line and the record disagree; the record is the authority",
        ),)
    # ---- the mechanics identity, BEFORE the general form sweep ---------------------
    # IDENTITY FIRST, and returned alone. A well-formed value naming another dispatch is
    # never repaired into acceptance, so it must not be masked by -- or mixed with -- a
    # repairable FORM defect found elsewhere in the record. `_identity_defects` reports
    # nothing for a value that is outside its own domain, which is what keeps an unknown
    # token (`B9`) on the FORM/repairable side of the same line.
    mechanics = (_mechanics_form_defects(record)
                 + _binding_domain_defects(record, binding))
    identity = _identity_defects(record, role=role, binding=binding)
    if identity:
        return identity
    form = mechanics + decision_gate.collect_form_defects(policy, record)
    if form:
        return form
    try:
        validate_record(policy, record)
    except DecisionPolicyError as exc:
        return (_defect(
            decision_gate.GATE_INPUT_MALFORMED, "SEMANTIC", "", (), record.get("state"),
            str(exc),
        ),)
    if record.get("state") in decision_gate.BLOCKING_STATES:
        state = str(record.get("state"))
        reason = record.get("reason_code")
        return (_defect(
            decision_gate.block_reason(state, reason), "SEMANTIC", "state", (), state,
            f"the record declares a legitimate semantic block: {state}/{reason}",
        ),)
    return ()


# ---- CLI ------------------------------------------------------------------------------

VALIDATION_REPAIR_REQUIRES_CHECKPOINT = "VALIDATION_REPAIR_REQUIRES_CHECKPOINT"


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill-path", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="print the generated Worker contract block")
    render.add_argument("--run-id", required=True)
    render.add_argument("--phase", required=True)
    render.add_argument("--iteration", type=int, required=True)
    render.add_argument("--role", required=True, choices=("worker", "reviewer"))

    check = sub.add_parser("validate", help="classify an agent result body, fail-closed")
    check.add_argument("--body-file", required=True)
    check.add_argument("--role", default="WORKER")
    check.add_argument("--json", action="store_true")
    check.add_argument(
        "--repair", action="store_true",
        help="refused: repair requires an OS-40 checkpoint (see PLAN.md U-1)",
    )

    args = parser.parse_args(argv)
    try:
        policy = resolve_policy(args.skill_path)
    except DecisionPolicyRequired as exc:
        print(str(exc), file=sys.stderr)
        return 2
    projection = contract_projection(policy)

    if args.command == "render":
        print(render_worker_contract(projection, run_id=args.run_id, phase=args.phase,
                                     iteration=args.iteration, role=args.role))
        return 0

    if args.repair:
        print(
            f"{VALIDATION_REPAIR_REQUIRES_CHECKPOINT}: bounded validation-repair is a "
            "checkpointed OS-40 workflow decision. This CLI validates and blocks; it "
            "never repairs and never writes a checkpoint.",
            file=sys.stderr,
        )
        return 3
    body = Path(args.body_file).read_text(encoding="utf-8")
    defects = classify_gate(policy, extract_gate_envelope(body), role=args.role)
    if args.json:
        print(json.dumps([defect.as_dict() for defect in defects], indent=2))
    else:
        for defect in defects:
            print(f"{defect.kind} {defect.code} {defect.field_path}: {defect.message}")
            if defect.expected:
                print(f"  allowed: {' | '.join(defect.expected)}")
            print(f"  actual:  {defect.actual}")
    return 0 if not defects else 1


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI shim
    return _cli(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
