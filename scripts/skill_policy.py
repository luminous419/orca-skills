#!/usr/bin/env python3
"""Deterministic smoke-test evaluator for Markdown skill policy contracts."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTRACT_BLOCK_PATTERN = re.compile(
    r"```policy-contract\s*\n(?P<contract>\{.*?\})\s*\n```",
    re.DOTALL,
)
PARAMETER_PATTERN = re.compile(
    r"(?<!\S)(?P<key>worker|reviewer|max-iterations|phases)=(?P<value>[^\s]+)"
)
AGENT_COMMAND_PATTERN = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)

# ---- OS-3: the orchestration-only risk axis ------------------------------------
# Deliberately NOT part of the ```policy-contract JSON above. That block is asserted
# byte-equal across both skills (scripts/validate_skills.py), and orca-worker-
# reviewer-loop has no risk axis; adding a key there would either break that
# equality or require editing a skill that is out of scope. The risk contract is
# therefore its own `#### Risk profile contract` anchor block, in the same
# `KEY = value, value` grammar the six existing orchestration-only anchor contracts
# already use, and load_risk_contract() below is the ONE parser for it --
# validate_skills.py imports this function rather than re-implementing the parse, so
# the runtime evaluator and the validator cannot disagree about what the block says.
RISK_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Risk profile contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)
RISK_CONTRACT_LINE_PATTERN = re.compile(r"([A-Z][A-Z0-9_]*) = (.+)")
RISK_CONTRACT_TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
# `\S*`, NOT `[^\s]+`: the zero-length value must still MATCH, so that an explicit
# `risk=` with no value is recognized as an explicit parameter and fails the
# RISK_LEVELS membership test as INVALID_RISK, instead of falling through to the
# default as if the user had never written it. Separate from PARAMETER_PATTERN so
# widening that shared pattern cannot change what the loop skill strips from `body`.
RISK_PARAMETER_PATTERN = re.compile(r"(?<!\S)(?P<key>risk)=(?P<value>\S*)")


class PolicyContractError(ValueError):
    """Raised when a Markdown policy contract is missing or malformed."""


@dataclass(frozen=True)
class PolicyDecision:
    status: str
    reason: str | None
    should_execute: bool
    worker: str | None = None
    reviewer: str | None = None
    max_iterations: int | None = None
    phases: tuple[str, ...] = ()
    phase_source: str | None = None
    requires_llm_phase_classification: bool = False
    # OS-3. None means "this skill has no risk axis", which is what
    # orca-worker-reviewer-loop yields -- the same way `risk` is absent from its
    # SKILL.md -- so its decisions stay comparable to today except for these fields.
    risk: str | None = None
    risk_source: str | None = None


def load_policy_contract(skill_path: Path) -> dict[str, Any]:
    """Load the machine-readable JSON contract embedded in a SKILL.md file."""

    text = skill_path.read_text(encoding="utf-8")
    match = CONTRACT_BLOCK_PATTERN.search(text)
    if not match:
        raise PolicyContractError(f"{skill_path}: missing policy-contract block")

    try:
        contract = json.loads(match.group("contract"))
    except json.JSONDecodeError as exc:
        raise PolicyContractError(
            f"{skill_path}: invalid policy-contract JSON: {exc}"
        ) from exc

    if not isinstance(contract, dict):
        raise PolicyContractError(f"{skill_path}: policy contract must be an object")
    return contract


def load_risk_contract(skill_path: Path) -> dict[str, tuple[str, ...]] | None:
    """The orchestration-only `#### Risk profile contract` block, or None.

    None means "this skill has no risk axis" -- which is exactly what
    orca-worker-reviewer-loop/SKILL.md yields, with no edit to that file. None is
    also the answer for a block that violates the grammar, following
    parse_lifecycle_contract's one-condition-one-diagnostic convention rather than
    raising: the caller that cares about malformation is the repository validator,
    and it reports it as one named failure.
    """

    match = RISK_CONTRACT_BLOCK_PATTERN.search(
        skill_path.read_text(encoding="utf-8")
    )
    if not match:
        return None
    parsed: dict[str, tuple[str, ...]] = {}
    for line in match.group("values").splitlines():
        line_match = RISK_CONTRACT_LINE_PATTERN.fullmatch(line)
        if line_match is None:
            return None
        key, raw = line_match.group(1), line_match.group(2)
        if key in parsed:
            return None
        values = tuple(value.strip() for value in raw.split(","))
        if not all(RISK_CONTRACT_TOKEN_PATTERN.fullmatch(value) for value in values):
            return None
        parsed[key] = values
    return parsed


def _blocked(
    reason: str,
    *,
    worker: str | None,
    reviewer: str | None,
    max_iterations: int | None,
    phases: tuple[str, ...] = (),
    phase_source: str | None = None,
    risk: str | None = None,
    risk_source: str | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        status="BLOCKED",
        reason=reason,
        should_execute=False,
        worker=worker,
        reviewer=reviewer,
        max_iterations=max_iterations,
        phases=phases,
        phase_source=phase_source,
        risk=risk,
        risk_source=risk_source,
    )


def _natural_language_phases(body: str, contract: dict[str, Any]) -> tuple[str, ...]:
    lowered = body.casefold()
    detected: set[str] = set()
    terms_by_phase = contract["natural_language_phase_terms"]
    for phase, terms in terms_by_phase.items():
        if any(_contains_term(lowered, str(term).casefold()) for term in terms):
            detected.add(phase)

    canonical_order = [
        *contract["sequential_phases"],
        *contract["specialized_phases"],
    ]
    return tuple(phase for phase in canonical_order if phase in detected)


def _contains_term(text: str, term: str) -> bool:
    if term.isascii():
        return bool(
            re.search(
                rf"(?<![a-z0-9_-]){re.escape(term)}(?![a-z0-9_-])",
                text,
            )
        )
    return term in text


def evaluate_invocation(skill_path: Path, invocation: str) -> PolicyDecision:
    """Evaluate deterministic policy gates without starting Orca or an agent."""

    contract = load_policy_contract(skill_path)
    # None for a skill with no `#### Risk profile contract` block (the loop skill),
    # which is what keeps that skill's decisions byte-identical to today.
    risk_contract = load_risk_contract(skill_path)
    skill_name = skill_path.parent.name
    stripped = invocation.strip()
    command_pattern = re.compile(rf"^/{re.escape(skill_name)}(?:\s+|$)")
    command_match = command_pattern.match(stripped)
    if not command_match:
        raise ValueError(f"invocation must start with /{skill_name}")

    payload = stripped[command_match.end() :].strip()
    help_contract = contract["help"]
    if (
        (not payload and help_contract["empty_request"])
        or payload in help_contract["tokens"]
    ):
        return PolicyDecision(status="HELP", reason=None, should_execute=False)

    explicit = {
        match.group("key"): match.group("value")
        for match in PARAMETER_PATTERN.finditer(payload)
    }
    body = PARAMETER_PATTERN.sub(" ", payload)
    if risk_contract is not None:
        # Last occurrence wins, matching the dict-comprehension semantics every other
        # parameter already has. Stripped from `body` BEFORE _natural_language_phases
        # reads it, so a risk token can never be scanned as request prose.
        for match in RISK_PARAMETER_PATTERN.finditer(payload):
            explicit["risk"] = match.group("value")
        body = RISK_PARAMETER_PATTERN.sub(" ", body)
    body = body.strip()
    defaults = contract["defaults"]
    errors = contract["errors"]

    worker = explicit.get("worker", defaults["worker"])
    reviewer = explicit.get("reviewer", defaults["reviewer"])

    if not AGENT_COMMAND_PATTERN.fullmatch(worker) or not AGENT_COMMAND_PATTERN.fullmatch(
        reviewer
    ):
        return _blocked(
            errors["invalid_agent_command"],
            worker=worker,
            reviewer=reviewer,
            max_iterations=None,
        )
    known_commands = set(contract["known_agent_commands"])
    custom_command_pattern = re.compile(
        str(contract["custom_agent_command_pattern"]), re.ASCII
    )
    if any(
        command not in known_commands
        and not custom_command_pattern.fullmatch(command)
        for command in (worker, reviewer)
    ):
        return _blocked(
            errors["agent_not_allowed"],
            worker=worker,
            reviewer=reviewer,
            max_iterations=None,
        )
    if worker == reviewer:
        return _blocked(
            errors["worker_reviewer_must_differ"],
            worker=worker,
            reviewer=reviewer,
            max_iterations=None,
        )
    if shutil.which(worker) is None or shutil.which(reviewer) is None:
        return _blocked(
            errors["agent_command_not_found"],
            worker=worker,
            reviewer=reviewer,
            max_iterations=None,
        )

    raw_max_iterations = explicit.get(
        "max-iterations", str(defaults["max_iterations"])
    )
    try:
        max_iterations = int(raw_max_iterations)
    except ValueError:
        return _blocked(
            errors["invalid_max_iterations"],
            worker=worker,
            reviewer=reviewer,
            max_iterations=None,
        )

    iteration_range = contract["max_iterations"]
    if not iteration_range["min"] <= max_iterations <= iteration_range["max"]:
        return _blocked(
            errors["invalid_max_iterations"],
            worker=worker,
            reviewer=reviewer,
            max_iterations=max_iterations,
        )

    # OS-3 risk gate. Placed with the other scalar parameters and BEFORE phase
    # resolution, so an invalid risk is reported as INVALID_RISK rather than being
    # masked by a phase error. The RISK_LEVELS membership test below is the ONLY
    # place validity is decided: uppercase and mixed-case pass because casefold
    # normalizes them, trailing whitespace is irrelevant because `\S*` never
    # captured it, and an explicitly empty value fails because "" is not a level.
    risk = risk_source = None
    if risk_contract is not None:
        levels = risk_contract["RISK_LEVELS"]
        if "risk" in explicit:
            risk, risk_source = explicit["risk"].casefold(), "explicit"
            if risk not in levels:
                return _blocked(
                    risk_contract["RISK_INVALID_ERROR"][0].upper(),
                    worker=worker,
                    reviewer=reviewer,
                    max_iterations=max_iterations,
                    risk=explicit["risk"],
                    risk_source="explicit",
                )
        else:
            risk, risk_source = risk_contract["RISK_DEFAULT"][0], "default"

    natural_phases = _natural_language_phases(body, contract)
    if "phases" in explicit:
        phases = tuple(
            phase.strip().casefold()
            for phase in explicit["phases"].split(",")
            if phase.strip()
        )
        phase_source = "explicit"
        known_phases = {
            *contract["sequential_phases"],
            *contract["specialized_phases"],
        }
        if any(phase not in known_phases for phase in phases):
            return _blocked(
                errors["invalid_phase"],
                worker=worker,
                reviewer=reviewer,
                max_iterations=max_iterations,
                phases=phases,
                phase_source=phase_source,
                risk=risk,
                risk_source=risk_source,
            )
        if any(phase not in phases for phase in natural_phases):
            return _blocked(
                errors["phase_conflict"],
                worker=worker,
                reviewer=reviewer,
                max_iterations=max_iterations,
                phases=phases,
                phase_source=phase_source,
                risk=risk,
                risk_source=risk_source,
            )
    elif natural_phases:
        phases = natural_phases
        phase_source = "natural_language"
    else:
        phases = ()
        phase_source = "llm_classification"

    specialized = set(contract["specialized_phases"])
    if any(phase in specialized for phase in phases):
        supported = {
            tuple(combination)
            for combination in contract["supported_specialized_combinations"]
        }
        if phases not in supported:
            return _blocked(
                errors["unsupported_phase_combination"],
                worker=worker,
                reviewer=reviewer,
                max_iterations=max_iterations,
                phases=phases,
                phase_source=phase_source,
                risk=risk,
                risk_source=risk_source,
            )

    sequential_order = {
        phase: index for index, phase in enumerate(contract["sequential_phases"])
    }
    if phases and all(phase in sequential_order for phase in phases):
        positions = [sequential_order[phase] for phase in phases]
        if positions != sorted(positions) or len(set(phases)) != len(phases):
            return _blocked(
                errors["invalid_phase_order"],
                worker=worker,
                reviewer=reviewer,
                max_iterations=max_iterations,
                phases=phases,
                phase_source=phase_source,
                risk=risk,
                risk_source=risk_source,
            )

    return PolicyDecision(
        status="VALID",
        reason=None,
        should_execute=True,
        worker=worker,
        reviewer=reviewer,
        max_iterations=max_iterations,
        phases=phases,
        phase_source=phase_source,
        requires_llm_phase_classification=phase_source == "llm_classification",
        risk=risk,
        risk_source=risk_source,
    )
