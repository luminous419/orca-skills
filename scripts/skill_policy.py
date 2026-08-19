#!/usr/bin/env python3
"""Deterministic smoke-test evaluator for Markdown skill policy contracts."""

from __future__ import annotations

import json
import re
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


def _blocked(
    reason: str,
    *,
    worker: str | None,
    reviewer: str | None,
    max_iterations: int | None,
    phases: tuple[str, ...] = (),
    phase_source: str | None = None,
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
    body = PARAMETER_PATTERN.sub(" ", payload).strip()
    defaults = contract["defaults"]
    errors = contract["errors"]

    worker = explicit.get("worker", defaults["worker"])
    reviewer = explicit.get("reviewer", defaults["reviewer"])

    allowlist = set(contract["agent_allowlist"])
    if worker not in allowlist or reviewer not in allowlist:
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
            )
        if any(phase not in phases for phase in natural_phases):
            return _blocked(
                errors["phase_conflict"],
                worker=worker,
                reviewer=reviewer,
                max_iterations=max_iterations,
                phases=phases,
                phase_source=phase_source,
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
    )
