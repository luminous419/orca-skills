#!/usr/bin/env python3
"""Minimal deterministic Worker/Reviewer loop harness for fake-agent E2E tests."""

from __future__ import annotations

import copy
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
SECTION = re.compile(
    r"(?ms)^##\s+(?P<title>[^\n]+)\s*\n(?P<body>.*?)(?=^##\s+|\Z)"
)
RESOLUTION_LINE = re.compile(
    r"(?m)^FINDING\s+(?P<id>[A-Za-z][A-Za-z0-9_-]*):\s*(?P<status>[A-Z_]+)\s*$"
)
CANONICAL_PHASES = ("analysis", "plan", "design", "implementation", "test")
FINAL_REVIEW_RESOLUTION_REASON = "FINAL_REVIEW_RESOLUTION_TRACE_INCOMPLETE"
# Sentinel recorded in corrected_findings when a routed finding id has no accepted
# resolution. Unreachable while the validate_final_review_resolutions bridge is in
# place -- the bridge already proved accepted.keys() == routed ids -- so it exists
# only so that bypassing the bridge fails on the semantics, not on a raw KeyError.
UNACCOUNTED_RESOLUTION = "UNACCOUNTED"
RESPONSIBLE_PHASE_LINE = re.compile(
    r"(?m)^Responsible Phase:\s*(?P<phase>[a-z][a-z0-9_]*)\s*$"
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



@dataclass(frozen=True)
class FinalFinding:
    finding_id: str
    responsible_phase: str | None
    severity: str = "MAJOR"


@dataclass(frozen=True)
class FinalReviewScenario:
    modes: tuple[str, ...]
    findings: tuple[tuple[tuple[str, str], ...], ...] = ()


@dataclass(frozen=True)
class WorkflowScenario:
    phases: tuple[str, ...]
    phase_scenarios: dict[str, FakeScenario]
    final_review: FinalReviewScenario
    topic: str = "final_adversarial_review"
    correction_scenarios: dict[tuple[str, int], FakeScenario] = field(
        default_factory=dict
    )


@dataclass
class WorkflowRunResult:
    phases: tuple[str, ...]
    phase_iterations: dict[str, int]
    final_review_iterations: int
    final_review_attempts: list[AgentAttempt]
    correction_dispatches: list[tuple[str, int]]
    final_review_verdict: str | None
    final_status: str
    final_review_artifacts: tuple[str, ...] = ()
    corrected_findings: tuple[tuple[int, str, str, str], ...] = ()
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
    sections = {
        match.group("title").strip(): match.group("body")
        for match in SECTION.finditer(output)
    }
    blocking_body = sections.get("Blocking Findings", "")
    findings = tuple(
        match.group("id") for match in FINDING_LINE.finditer(blocking_body)
    )
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



def final_review_artifact_path(topic: str, attempt: int) -> str:
    """W-A1-N: attempt 1 is unsuffixed; attempt N>=2 carries _iteration<N>.

    There is deliberately no `_iteration1` form -- it would break the parallel with
    artifacts/REVIEW_<PHASE>_<topic>.md, which uses the same rule.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    suffix = "" if attempt == 1 else f"_iteration{attempt}"
    return f"artifacts/FINAL_REVIEW_{topic}{suffix}.md"


def lower_to_requested_phase(phase: str, requested: tuple[str, ...]) -> str | None:
    """Ladder rule 3: map a responsible phase onto the requested phase set.

    Returns the phase itself when it is requested; otherwise the last requested
    canonical phase at or below it; otherwise the first requested canonical phase
    above it; otherwise None, which the caller turns into
    OUT_OF_SCOPE_FINAL_REVIEW_FINDING.
    """
    if phase in requested:
        return phase
    if phase not in CANONICAL_PHASES:      # bugfix / refactoring are never lowered into
        return None
    index = CANONICAL_PHASES.index(phase)
    canonical_requested = [p for p in requested if p in CANONICAL_PHASES]
    below = [p for p in canonical_requested if CANONICAL_PHASES.index(p) <= index]
    if below:
        return max(below, key=CANONICAL_PHASES.index)
    above = [p for p in canonical_requested if CANONICAL_PHASES.index(p) > index]
    return min(above, key=CANONICAL_PHASES.index) if above else None


def parse_final_review_output(
    output: str, contract: WorkflowOutputContract
) -> tuple[str, tuple[FinalFinding, ...]]:
    """Verdict + (finding id, responsible phase) pairs.

    Delegates the verdict and the id set to the EXISTING parse_reviewer_output --
    its signature is bound by several tests and is not changed -- then walks the
    `## Blocking Findings` body pairing each `ID:` with the `Responsible Phase:`
    line that follows it before the next `ID:`.
    """
    verdict, _ = parse_reviewer_output(output, contract)
    sections = {
        match.group("title").strip(): match.group("body")
        for match in SECTION.finditer(output)
    }
    blocking_body = sections.get("Blocking Findings", "")
    matches = list(FINDING_LINE.finditer(blocking_body))
    findings: list[FinalFinding] = []
    for position, match in enumerate(matches):
        start = match.end()
        end = (
            matches[position + 1].start()
            if position + 1 < len(matches)
            else len(blocking_body)
        )
        phase_match = RESPONSIBLE_PHASE_LINE.search(blocking_body[start:end])
        findings.append(
            FinalFinding(
                match.group("id"),
                phase_match.group("phase") if phase_match is not None else None,
            )
        )
    return verdict, tuple(findings)


def validate_final_review_resolutions(
    worker_output: str,
    routed_finding_ids: frozenset[str],
    contract: WorkflowOutputContract,
) -> str | None:
    """Accept or reject a correction Worker's Final Review finding-resolution map.

    Mirrors run()'s `set(parsed_resolutions) != previous_blocking_findings` check
    for the one case run() structurally cannot see: findings raised by a Final
    Adversarial Review attempt, outside run().

    Returns None when the round is acceptable; otherwise the detail string appended
    to FINAL_REVIEW_RESOLUTION_TRACE_INCOMPLETE.
    """
    try:
        _, resolutions = parse_worker_output(worker_output, contract)
    except OutputContractError as exc:  # defensive: run() already parsed these bytes
        return f"UNPARSEABLE:{exc}"
    emitted = set(resolutions)
    if emitted == set(routed_finding_ids):
        return None
    missing = sorted(set(routed_finding_ids) - emitted)
    extra = sorted(emitted - set(routed_finding_ids))
    return f"missing={missing} extra={extra}"


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
        previous_blocking_findings: set[str] = set()

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

            if previous_blocking_findings:
                if set(parsed_resolutions) != previous_blocking_findings:
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
            previous_blocking_findings = set(parsed_findings)

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

    def _phase_harness(self, phase: str, budget: int) -> "E2EHarness":
        """A shallow clone that runs `run()` for one phase with a bounded budget.

        run() is the single-phase authority and is byte-unchanged; this clone only
        varies the two attributes it reads (`phase`, `max_iterations`). The contract,
        workspace and protected artifacts are shared by reference on purpose -- the
        protected-artifact guard must see the same files the parent protects.
        """
        child = copy.copy(self)
        child.phase = phase
        child.max_iterations = budget
        return child

    def _workflow_error(
        self, reason: str, result: WorkflowRunResult
    ) -> WorkflowRunResult:
        """Parity with _error(): a result-contract violation is ERROR, not ESCALATED."""
        result.final_status = "ERROR"
        result.reason = reason
        return result

    def _run_final_review_attempt(
        self, attempt: int, mode: str, findings: tuple[tuple[str, str], ...]
    ) -> tuple[str | None, tuple[FinalFinding, ...], AgentAttempt | None]:
        """One Final Adversarial Review dispatch: a Reviewer-only invocation.

        There is no Worker in a Final Review attempt, so this does not go through
        run(); it reuses the same fake_reviewer.py subprocess, the same protected-
        artifact hashing guard, and the same contract fields.

        When the returned AgentAttempt is None a guard tripped, and the first slot
        carries the error reason instead of a verdict.
        """
        command = [
            sys.executable,
            str(SCRIPT_DIR / "fake_reviewer.py"),
            "--mode",
            mode,
            "--field",
            self.contract.reviewer_field,
            "--pass-value",
            self.contract.reviewer_pass,
            "--fail-value",
            self.contract.reviewer_fail,
            "--iteration",
            str(attempt),
            "--findings-json",
            json.dumps([finding_id for finding_id, _ in findings]),
            "--responsible-phases-json",
            json.dumps(
                {finding_id: phase for finding_id, phase in findings}, sort_keys=True
            ),
        ]
        if self.protected_artifacts:
            command.extend(["--artifact", str(self.protected_artifacts[0])])
        hashes_before = _hash_files(self.protected_artifacts)
        completed = subprocess.run(
            command,
            cwd=self.workspace,
            text=True,
            capture_output=True,
            check=False,
        )
        hashes_after = _hash_files(self.protected_artifacts)
        # guards, in this order: artifact mutation -> exit code -> output contract
        if hashes_before != hashes_after:
            return "REVIEWER_MODIFIED_PROTECTED_ARTIFACT", (), None
        if completed.returncode != 0:
            return f"FINAL_REVIEW_UNEXPECTED_EXIT:{completed.returncode}", (), None
        try:
            verdict, parsed_findings = parse_final_review_output(
                completed.stdout, self.contract
            )
        except OutputContractError as exc:
            return f"MALFORMED_FINAL_REVIEW_OUTPUT:{exc}", (), None
        return (
            verdict,
            parsed_findings,
            AgentAttempt(attempt, verdict, completed.stdout),
        )

    def _run_correction_round(
        self,
        phase: str,
        budget: int,
        correction: FakeScenario,
        routed_finding_ids: frozenset[str],
    ) -> tuple[WorkflowResult, dict[str, str], str | None]:
        """Run one correction round through the UNCHANGED run(), then bridge-check it.

        Returns (result, accepted_resolutions, bridge_reason).
        bridge_reason is None when the round is acceptable, and accepted_resolutions
        then holds the {finding_id: resolution} map the caller records in
        corrected_findings. When the bridge rejects, accepted_resolutions is {}.
        """
        result = self._phase_harness(phase, budget).run(correction)
        if not result.worker_attempts:
            return result, {}, None        # run() failed before any Worker ran: propagate
        if (
            result.final_status == self.contract.blocked_status
            and result.current_iteration == 1
        ):
            return result, {}, None        # run() parity: the BLOCKED return precedes
                                           # the resolution check
        detail = validate_final_review_resolutions(
            result.worker_attempts[0].output, routed_finding_ids, self.contract
        )
        if detail is not None:
            return result, {}, detail
        _, accepted = parse_worker_output(
            result.worker_attempts[0].output, self.contract
        )
        return result, accepted, None

    def run_workflow(self, scenario: WorkflowScenario) -> WorkflowRunResult:
        phase_iterations: dict[str, int] = {p: 0 for p in scenario.phases}
        correction_dispatches: list[tuple[str, int]] = []
        corrected_findings: list[tuple[int, str, str, str]] = []
        final_review_attempts: list[AgentAttempt] = []
        final_review_artifacts: list[str] = []
        final_review_iterations = 0
        final_review_verdict: str | None = None

        def snapshot(
            final_status: str = "", reason: str | None = None
        ) -> WorkflowRunResult:
            return WorkflowRunResult(
                phases=scenario.phases,
                phase_iterations=dict(phase_iterations),
                final_review_iterations=final_review_iterations,
                final_review_attempts=list(final_review_attempts),
                correction_dispatches=list(correction_dispatches),
                final_review_verdict=final_review_verdict,
                final_status=final_status,
                final_review_artifacts=tuple(final_review_artifacts),
                corrected_findings=tuple(corrected_findings),
                reason=reason,
            )

        # ---- sequential phase gates (SKILL.md section 8: PASS before the next phase)
        for phase in scenario.phases:
            phase_scenario = scenario.phase_scenarios.get(phase)
            if phase_scenario is None:
                return self._workflow_error(
                    "SCENARIO_PHASE_MISSING:" + phase, snapshot()
                )
            result = self._phase_harness(phase, self.max_iterations).run(phase_scenario)
            phase_iterations[phase] += len(result.reviewer_attempts)
            if result.final_status != self.contract.completed_status:
                # BLOCKED worker, malformed output, or the phase's own budget exhausted:
                # the gate is never reached and the phase's status/reason is propagated.
                return snapshot(result.final_status, result.reason)

        while True:
            # ---- T0: every requested phase has PASSed. Open a fresh attempt.
            final_review_iterations += 1
            final_review_artifacts.append(
                final_review_artifact_path(scenario.topic, final_review_iterations)
            )
            index = final_review_iterations - 1
            if index >= len(scenario.final_review.modes):
                return self._workflow_error(
                    "SCENARIO_FINAL_REVIEW_EXHAUSTED", snapshot()
                )

            verdict, findings, attempt = self._run_final_review_attempt(
                final_review_iterations,
                scenario.final_review.modes[index],
                scenario.final_review.findings[index]
                if index < len(scenario.final_review.findings)
                else (),
            )
            if attempt is None:                       # protected-artifact guard tripped,
                return self._workflow_error(          # non-zero exit, or malformed output
                    verdict or "MALFORMED_FINAL_REVIEW_OUTPUT", snapshot()
                )
            final_review_attempts.append(attempt)
            final_review_verdict = verdict

            # ---- T1
            if verdict == self.contract.reviewer_pass:
                return snapshot(self.contract.completed_status)

            # ---- T2: LAST-ATTEMPT GUARD.
            # This is the FIRST statement on the FAIL edge. Nothing above it reads
            # phase_iterations, maps a finding to a phase, or appends to
            # correction_dispatches. Moving it below the routing block is the exact
            # defect the ANALYSIS reviewer found in iteration 2 (risk P-6).
            if final_review_iterations >= self.max_iterations:
                return snapshot(
                    self.contract.escalated_status,
                    "FINAL_REVIEW_MAX_ITERATIONS_REACHED",
                )

            # ---- T3: the attempt is settled; now, and only now, route the findings.
            #      routed[owner] keeps the finding IDS, not merely the owner set: those ids
            #      are what T4 hands to the correction round and what the bridge checks.
            if not findings:
                return self._workflow_error(
                    "MALFORMED_FINAL_REVIEW_OUTPUT", snapshot()
                )
            routed: dict[str, list[str]] = {}
            for finding in findings:
                if finding.responsible_phase is None:
                    return self._workflow_error(
                        "MALFORMED_FINAL_REVIEW_OUTPUT", snapshot()
                    )
                owner = lower_to_requested_phase(
                    finding.responsible_phase, scenario.phases
                )
                if owner is None:
                    return snapshot(
                        self.contract.escalated_status,
                        "OUT_OF_SCOPE_FINAL_REVIEW_FINDING",
                    )
                routed.setdefault(owner, []).append(finding.finding_id)
            responsible = sorted(routed, key=scenario.phases.index)   # upstream first

            # ---- T4: one correction round per responsible phase, upstream first.
            for phase in responsible:
                if phase_iterations[phase] >= self.max_iterations:
                    return snapshot(
                        self.contract.escalated_status,
                        f"MAX_ITERATIONS_REACHED ({phase})",
                    )
                correction = scenario.correction_scenarios.get(
                    (phase, final_review_iterations)
                )
                if correction is None:
                    return self._workflow_error(
                        "SCENARIO_CORRECTION_MISSING", snapshot()
                    )
                budget = self.max_iterations - phase_iterations[phase]
                result, accepted, bridge_reason = self._run_correction_round(
                    phase, budget, correction, frozenset(routed[phase])
                )
                # The ledger is written BEFORE any verdict is applied: these Reviewer
                # dispatches physically happened and are never rewound, whatever the
                # bridge decides.
                for offset in range(1, len(result.reviewer_attempts) + 1):
                    correction_dispatches.append(
                        (phase, phase_iterations[phase] + offset)
                    )
                phase_iterations[phase] += len(result.reviewer_attempts)
                # ---- T4a: the finding-resolution bridge, evaluated BEFORE the round's own
                #      status, because run() would have fired it at local iteration 1 -- i.e.
                #      before this round's first Reviewer ever ran.
                if bridge_reason is not None:
                    return self._workflow_error(
                        f"{FINAL_REVIEW_RESOLUTION_REASON} ({phase}): {bridge_reason}",
                        snapshot(),
                    )
                if result.final_status == self.contract.escalated_status:
                    return snapshot(
                        self.contract.escalated_status,
                        f"MAX_ITERATIONS_REACHED ({phase})",
                    )
                if result.final_status != self.contract.completed_status:
                    return snapshot(result.final_status, result.reason)
                # accepted round -> the DECISION P1 table for the next attempt's prompt
                for finding_id in routed[phase]:
                    corrected_findings.append(
                        (
                            final_review_iterations,
                            finding_id,
                            phase,
                            # .get, not [...]: the bridge above guarantees the key is
                            # present on every reachable path, so this default is dead
                            # code in production and only fires if the bridge is removed.
                            accepted.get(finding_id, UNACCOUNTED_RESOLUTION),
                        )
                    )
            # ---- T5: every responsible phase PASSed again. Loop to T0 for a fresh attempt.
