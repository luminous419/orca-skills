#!/usr/bin/env python3
"""Minimal deterministic Worker/Reviewer loop harness for fake-agent E2E tests."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from scripts.workflow_contract import (
    WorkflowOutputContract,
    load_workflow_output_contract,
)


SCRIPT_DIR = Path(__file__).resolve().parent
FIELD_LINE = re.compile(r"(?m)^(?P<field>[A-Z_]+):\s*(?P<value>[A-Z_]+)\s*$")
FINDING_LINE = re.compile(r"(?m)^ID:\s*(?P<id>[A-Za-z][A-Za-z0-9_-]*)\s*$")
RESOLUTION_LINE = re.compile(
    r"(?m)^FINDING\s+(?P<id>[A-Za-z][A-Za-z0-9_-]*):\s*(?P<status>[A-Z_]+)\s*$"
)


class OutputContractError(ValueError):
    """Raised when fake-agent output violates the documented result contract."""


@dataclass(frozen=True)
class FakeScenario:
    worker_modes: tuple[str, ...]
    reviewer_modes: tuple[str, ...]
    reviewer_findings: tuple[tuple[str, ...], ...] = ()
    worker_resolutions: tuple[dict[str, str], ...] = ()


@dataclass
class FindingTrace:
    finding_id: str
    introduced_iteration: int
    reviewer_iterations: list[int] = field(default_factory=list)
    resolutions: list[tuple[int, str]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentAttempt:
    iteration: int
    outcome: str
    output: str


@dataclass
class WorkflowResult:
    current_phase: str
    current_iteration: int
    max_iterations: int
    worker_attempts: list[AgentAttempt]
    reviewer_attempts: list[AgentAttempt]
    findings: dict[str, FindingTrace]
    final_status: str
    reason: str | None = None


def _parse_choice(output: str, field_name: str, allowed: set[str]) -> str:
    values = [
        match.group("value")
        for match in FIELD_LINE.finditer(output)
        if match.group("field") == field_name
    ]
    if len(values) != 1 or values[0] not in allowed:
        raise OutputContractError(
            f"expected exactly one {field_name} in {sorted(allowed)}, got {values}"
        )
    return values[0]


def parse_worker_output(
    output: str, contract: WorkflowOutputContract
) -> tuple[str, dict[str, str]]:
    status = _parse_choice(
        output,
        contract.worker_field,
        {contract.worker_complete, contract.worker_blocked},
    )
    resolutions: dict[str, str] = {}
    for match in RESOLUTION_LINE.finditer(output):
        finding_id = match.group("id")
        resolution = match.group("status")
        if resolution not in contract.finding_resolution_values:
            raise OutputContractError(f"invalid finding resolution {resolution}")
        if finding_id in resolutions:
            raise OutputContractError(f"duplicate finding resolution {finding_id}")
        resolutions[finding_id] = resolution
    return status, resolutions


def parse_reviewer_output(
    output: str, contract: WorkflowOutputContract
) -> tuple[str, tuple[str, ...]]:
    result = _parse_choice(
        output,
        contract.reviewer_field,
        {contract.reviewer_pass, contract.reviewer_fail},
    )
    findings = tuple(match.group("id") for match in FINDING_LINE.finditer(output))
    if len(findings) != len(set(findings)):
        raise OutputContractError("duplicate finding identity")
    if result == contract.reviewer_fail and not findings:
        raise OutputContractError("FAIL review must contain a finding")
    if result == contract.reviewer_pass and findings:
        raise OutputContractError("PASS review must not contain blocking findings")
    return result, findings


def _hash_files(paths: tuple[Path, ...]) -> dict[Path, str]:
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


class E2EHarness:
    """Run one phase through a bounded fake Worker/Reviewer state machine."""

    def __init__(
        self,
        skill_path: Path,
        *,
        phase: str = "implementation",
        max_iterations: int = 5,
        workspace: Path,
        protected_artifacts: tuple[Path, ...] = (),
    ) -> None:
        self.contract = load_workflow_output_contract(skill_path)
        self.phase = phase
        self.max_iterations = max_iterations
        self.workspace = workspace
        self.protected_artifacts = protected_artifacts

    def _error(
        self,
        iteration: int,
        reason: str,
        worker_attempts: list[AgentAttempt],
        reviewer_attempts: list[AgentAttempt],
        findings: dict[str, FindingTrace],
    ) -> WorkflowResult:
        return WorkflowResult(
            current_phase=self.phase,
            current_iteration=iteration,
            max_iterations=self.max_iterations,
            worker_attempts=worker_attempts,
            reviewer_attempts=reviewer_attempts,
            findings=findings,
            final_status="ERROR",
            reason=reason,
        )

    def run(self, scenario: FakeScenario) -> WorkflowResult:
        worker_attempts: list[AgentAttempt] = []
        reviewer_attempts: list[AgentAttempt] = []
        finding_traces: dict[str, FindingTrace] = {}

        for iteration in range(1, self.max_iterations + 1):
            if iteration > len(scenario.worker_modes):
                return self._error(
                    iteration,
                    "SCENARIO_WORKER_EXHAUSTED",
                    worker_attempts,
                    reviewer_attempts,
                    finding_traces,
                )
            resolutions = (
                scenario.worker_resolutions[iteration - 1]
                if iteration <= len(scenario.worker_resolutions)
                else {}
            )
            worker_command = [
                sys.executable,
                str(SCRIPT_DIR / "fake_worker.py"),
                "--mode",
                scenario.worker_modes[iteration - 1],
                "--field",
                self.contract.worker_field,
                "--complete-value",
                self.contract.worker_complete,
                "--blocked-value",
                self.contract.worker_blocked,
                "--iteration",
                str(iteration),
                "--resolutions-json",
                json.dumps(resolutions, sort_keys=True),
            ]
            worker = subprocess.run(
                worker_command,
                cwd=self.workspace,
                text=True,
                capture_output=True,
                check=False,
            )
            if worker.returncode != 0:
                return self._error(
                    iteration,
                    f"WORKER_UNEXPECTED_EXIT:{worker.returncode}",
                    worker_attempts,
                    reviewer_attempts,
                    finding_traces,
                )
            try:
                worker_status, parsed_resolutions = parse_worker_output(
                    worker.stdout, self.contract
                )
            except OutputContractError as exc:
                return self._error(
                    iteration,
                    f"MALFORMED_WORKER_OUTPUT:{exc}",
                    worker_attempts,
                    reviewer_attempts,
                    finding_traces,
                )
            worker_attempts.append(
                AgentAttempt(iteration, worker_status, worker.stdout)
            )
            if worker_status == self.contract.worker_blocked:
                return WorkflowResult(
                    current_phase=self.phase,
                    current_iteration=iteration,
                    max_iterations=self.max_iterations,
                    worker_attempts=worker_attempts,
                    reviewer_attempts=reviewer_attempts,
                    findings=finding_traces,
                    final_status=self.contract.blocked_status,
                    reason="WORKER_BLOCKED",
                )

            if finding_traces:
                if set(parsed_resolutions) != set(finding_traces):
                    return self._error(
                        iteration,
                        "FINDING_RESOLUTION_TRACE_INCOMPLETE",
                        worker_attempts,
                        reviewer_attempts,
                        finding_traces,
                    )
                for finding_id, resolution in parsed_resolutions.items():
                    finding_traces[finding_id].resolutions.append(
                        (iteration, resolution)
                    )

            reviewer_index = iteration - 1
            if reviewer_index >= len(scenario.reviewer_modes):
                return self._error(
                    iteration,
                    "SCENARIO_REVIEWER_EXHAUSTED",
                    worker_attempts,
                    reviewer_attempts,
                    finding_traces,
                )
            findings = (
                scenario.reviewer_findings[reviewer_index]
                if reviewer_index < len(scenario.reviewer_findings)
                else ()
            )
            reviewer_command = [
                sys.executable,
                str(SCRIPT_DIR / "fake_reviewer.py"),
                "--mode",
                scenario.reviewer_modes[reviewer_index],
                "--field",
                self.contract.reviewer_field,
                "--pass-value",
                self.contract.reviewer_pass,
                "--fail-value",
                self.contract.reviewer_fail,
                "--iteration",
                str(iteration),
                "--findings-json",
                json.dumps(findings),
            ]
            if self.protected_artifacts:
                reviewer_command.extend(
                    ["--artifact", str(self.protected_artifacts[0])]
                )
            hashes_before = _hash_files(self.protected_artifacts)
            reviewer = subprocess.run(
                reviewer_command,
                cwd=self.workspace,
                text=True,
                capture_output=True,
                check=False,
            )
            hashes_after = _hash_files(self.protected_artifacts)
            if hashes_before != hashes_after:
                return self._error(
                    iteration,
                    "REVIEWER_MODIFIED_PROTECTED_ARTIFACT",
                    worker_attempts,
                    reviewer_attempts,
                    finding_traces,
                )
            if reviewer.returncode != 0:
                return self._error(
                    iteration,
                    f"REVIEWER_UNEXPECTED_EXIT:{reviewer.returncode}",
                    worker_attempts,
                    reviewer_attempts,
                    finding_traces,
                )
            try:
                review_result, parsed_findings = parse_reviewer_output(
                    reviewer.stdout, self.contract
                )
            except OutputContractError as exc:
                return self._error(
                    iteration,
                    f"MALFORMED_REVIEWER_OUTPUT:{exc}",
                    worker_attempts,
                    reviewer_attempts,
                    finding_traces,
                )
            reviewer_attempts.append(
                AgentAttempt(iteration, review_result, reviewer.stdout)
            )

            if review_result == self.contract.reviewer_pass:
                return WorkflowResult(
                    current_phase=self.phase,
                    current_iteration=iteration,
                    max_iterations=self.max_iterations,
                    worker_attempts=worker_attempts,
                    reviewer_attempts=reviewer_attempts,
                    findings=finding_traces,
                    final_status=self.contract.completed_status,
                )

            for finding_id in parsed_findings:
                trace = finding_traces.setdefault(
                    finding_id, FindingTrace(finding_id, iteration)
                )
                trace.reviewer_iterations.append(iteration)

        return WorkflowResult(
            current_phase=self.phase,
            current_iteration=self.max_iterations,
            max_iterations=self.max_iterations,
            worker_attempts=worker_attempts,
            reviewer_attempts=reviewer_attempts,
            findings=finding_traces,
            final_status=self.contract.escalated_status,
            reason="MAX_ITERATIONS_REACHED",
        )
