#!/usr/bin/env python3
"""Minimal deterministic Worker/Reviewer loop harness for fake-agent E2E tests."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from scripts import run_logging
from scripts.quality_profile import resolve_quality_profile
from scripts.skill_policy import load_risk_contract
from scripts.agent_profile import (
    RUNTIME_LOOP,
    RUNTIME_ORCHESTRATION,
    AgentProfileError,
    materialize_run_routing,
    select_agent_profile,
)
from scripts.task_context import (
    AGENT_ROUTING_SPEC_HEADER,
    build_agent_routing_context,
    parse_agent_routing,
    RISK_CONTEXT_KEYS,
    RISK_SPEC_HEADER,
    FINAL_REVIEW_PHASE,
    build_quality_gate_context,
    build_risk_context,
    parse_quality_gate,
    parse_risk_profile,
    build_reviewer_context,
    build_task_boundary,
    ensure_run_artifact_root,
    phase_artifact_contract,
    render_task_spec,
    run_artifact_root,
)
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
QUALITY_ATTRIBUTE_LINE = re.compile(
    r"(?m)^Quality Attribute:\s*(?P<attribute>[A-Za-z][A-Za-z0-9_-]*)\s*$"
)
SEVERITY_LINE = re.compile(
    r"(?m)^Severity:\s*(?P<severity>CRITICAL|MAJOR|MINOR)\s*$"
)
BLOCKING_LINE = re.compile(r"(?m)^Blocking:\s*(?P<blocking>YES|NO)\s*$")
# `Quality Attribute: NONE` is the contract's own spelling for "charged to no
# project attribute", and reviews/common.md pairs it with exactly one blocking
# value: NO. A General Gate violation is blocking and is charged to G1-G5, never to
# NONE, so NONE + YES names no criterion at all and cannot be acted on.
UNCHARGED_QUALITY_ATTRIBUTE = "NONE"
RESPONSIBLE_PHASE_LINE = re.compile(
    r"(?m)^Responsible Phase:\s*(?P<phase>[a-z][a-z0-9_]*)\s*$"
)


# The only two session policies. "reuse" hands the same session id to the next
# same-role attempt (S-R4); "fresh" allocates a new one every time (S-R2), which is
# exactly today's one-terminal-per-attempt behaviour and therefore the safe fallback.
SESSION_POLICIES = frozenset({"reuse", "fresh"})

# The subprocess each role actually runs. Recorded on the event so `agent_command` is
# a fact about the run, not decoration -- the runtime harness's eligibility condition
# 2 compares the same kind of value.
SESSION_AGENT_COMMANDS = {
    "worker": "fake_worker.py",
    "reviewer": "fake_reviewer.py",
    "final_review": "fake_reviewer.py",
}


# ---- OS-3 -------------------------------------------------------------------------
RISK_LEVELS = ("low", "medium", "high")
UNIT_TEST_STATUS_FIELD = "UNIT_TEST_STATUS"
UNIT_TEST_STATUS_PASS = "PASS"
UNIT_TEST_STATUS_BLOCKED = "BLOCKED"
UNIT_TEST_STATUS_VALUES = (UNIT_TEST_STATUS_PASS, UNIT_TEST_STATUS_BLOCKED)
# SKILL.md section 14's three headings, and only those. TEST is deliberately absent:
# its obligations live in templates/test.md's Mandatory Invariants (the phase
# contract, tier 3), not in section 14, and templates/ is byte-locked across skills.
UNIT_TEST_GATED_PHASES = frozenset({"implementation", "bugfix", "refactoring"})
UNIT_TEST_EVIDENCE_MISSING_REASON = "UNIT_TEST_EVIDENCE_MISSING"
UNIT_TEST_BLOCKED_REASON = "UNIT_TEST_BLOCKED"


class OutputContractError(ValueError):
    """Raised when fake-agent output violates the documented result contract."""


class RiskNotSupportedError(ValueError):
    """Raised when an explicit risk is given to a skill that has no risk axis.

    A separate class rather than OutputContractError: that one means "fake-agent
    output violates the result contract" and is caught by run()'s output-parsing
    blocks, so a capability error routed through it would be silently converted into
    a MALFORMED_WORKER_OUTPUT result. ValueError keeps it in the same house shape as
    TaskContextError / RunLoggingError / PolicyContractError.
    """


@dataclass(frozen=True)
class FakeScenario:
    worker_modes: tuple[str, ...]
    reviewer_modes: tuple[str, ...]
    reviewer_findings: tuple[tuple[str, ...], ...] = ()
    worker_resolutions: tuple[dict[str, str], ...] = ()
    # OS-3: per-iteration section 14 evidence, indexed exactly like
    # worker_resolutions. Empty tuple -> no flag passed -> byte-identical output.
    worker_unit_test_statuses: tuple[str, ...] = ()
    # The malformed-output seam: per iteration, the RAW UNIT_TEST_STATUS values to
    # emit, one line each and unconstrained, so a scenario can drive the parser's
    # duplicate-line and unknown-value branches through the real subprocess. Separate
    # from the field above rather than loosening it: that one is the well-formed knob
    # every ordinary scenario uses, and it should stay impossible to misuse.
    worker_unit_test_status_lines: tuple[tuple[str, ...], ...] = ()


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
    sessions: tuple[SessionEvent, ...] = ()


@dataclass(frozen=True)
class SessionEvent:
    """One agent invocation, and which session it was handed to.

    task_boundary and reviewer_context_keys are normalized to tuples so this frozen
    record stays hashable and two attempts can be compared for equality. They are
    produced by scripts/task_context.py, the same module the runtime harness uses, so
    the payload shape is defined in exactly one place.

    `quality_gate` is the profile-first block parsed back OUT of the `--task-spec`
    text this invocation was handed. The two fields above are the layer-1 keys and the
    Reviewer key NAMES, neither of which can answer "which quality attributes did this
    phase's Worker actually see", so a workflow test would otherwise have to re-derive
    what it believes the spec should have contained. Parsed rather than stored raw
    because the raw spec carries the Reviewer's drill_down -- an absolute workspace
    path -- which would make two runs of the same scenario compare unequal and break
    the cross-skill determinism assertion in test_e2e_harness.py.
    """

    role: str = ""
    phase: str = ""
    iteration: int = 0
    session_id: str = ""
    created: bool = False
    agent_command: str = ""
    task_boundary: tuple[tuple[str, str], ...] = ()
    reviewer_context_keys: tuple[str, ...] = ()
    quality_gate: tuple[tuple[str, str], ...] = ()
    # OS-3: the risk block parsed back OUT of the dispatched --task-spec text, the
    # same way quality_gate is. () for a skill with no risk axis and for the
    # final_review record. Contains no absolute paths, so it cannot reintroduce the
    # drill_down non-determinism this docstring warns about above.
    risk_profile: tuple[tuple[str, str], ...] = ()
    # OS-4: the routing block parsed back OUT of the dispatched spec, the same way
    # quality_gate and risk_profile are. () for every legacy dispatch -- a run with
    # no `profile=` renders no routing block at all, so an empty tuple here is the
    # assertion a compatibility test binds to rather than a placeholder.
    agent_routing: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class FinalFinding:
    finding_id: str
    responsible_phase: str | None
    severity: str = "MAJOR"
    # OS-1's Final Review Finding Contract. `severity` says how much impact the
    # finding has; `blocking` says whether it fails this gate, and they are different
    # axes -- a MAJOR finding whose quality attribute is not blocking does not route
    # to a correction round. `quality_attribute` is the attribute id or general gate
    # id the finding is charged to; NONE is only ever paired with blocking=False.
    quality_attribute: str = "G1"
    blocking: bool = True


# A scenario may spell a finding either way. The two-value form predates OS-1 and
# means "a blocking general-gate violation", which is what every pre-OS-1 fixture in
# this file already meant by putting a finding under `## Blocking Findings`.
FinalFindingSpec = tuple[str, str] | tuple[str, str, str, bool]


def normalize_final_finding_spec(spec: FinalFindingSpec) -> tuple[str, str, str, bool]:
    """(id, responsible phase, quality attribute, blocking) from either form."""
    if len(spec) == 2:
        finding_id, phase = spec
        return finding_id, phase, "G1", True
    finding_id, phase, attribute, blocking = spec
    return finding_id, phase, attribute, bool(blocking)


@dataclass(frozen=True)
class FinalReviewScenario:
    modes: tuple[str, ...]
    findings: tuple[tuple[FinalFindingSpec, ...], ...] = ()


@dataclass(frozen=True)
class WorkflowScenario:
    phases: tuple[str, ...]
    phase_scenarios: dict[str, FakeScenario]
    final_review: FinalReviewScenario
    run_id: str = "run_e2e_final_adversarial_review"
    correction_scenarios: dict[tuple[str, int], FakeScenario] = field(
        default_factory=dict
    )
    revalidation_scenarios: dict[tuple[str, int], FakeScenario] = field(
        default_factory=dict
    )
    session_policy: str = "reuse"
    # OS-3. None means "not specified", NOT "high": a field defaulted to "high" could
    # not distinguish "explicitly asked for HIGH" from "said nothing", and both the
    # loop-skill fail-closed rule and the precedence rule depend on that difference.
    # There is deliberately no risk_source field -- a scenario that supplies a value
    # IS the explicit case, so a second field could only agree redundantly or lie.
    risk: str | None = None


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
    revalidation_dispatches: list[tuple[str, int]] = field(default_factory=list)
    reason: str | None = None
    sessions: tuple[SessionEvent, ...] = ()
    # OS-3 reporting (never input): None for a skill with no risk axis.
    risk: str | None = None
    risk_source: str | None = None
    reviewer_gates_skipped: list[str] = field(default_factory=list)
    # OS-4 reporting (never input). () for a run that selected no profile -- the
    # final report of such a run must not grow an AGENT_PROFILE line, not even one
    # reading "none", so an empty tuple here is the absence itself rather than a
    # placeholder standing in for it.
    agent_profile_report: tuple[str, ...] = ()


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


def parse_unit_test_status(output: str) -> str:
    """The section 14 gate result a Worker reported, or "" when it reported none.

    A standalone reader rather than a fourth element on parse_worker_output()'s
    return tuple: that function has three call sites and is bound by existing tests,
    and an ABSENT field is a legitimate, common state that must not become an
    OutputContractError -- it is the CALLER that decides whether absence is
    acceptable, which is what makes the LOW/MEDIUM/HIGH split expressible here.

    Raises OutputContractError on more than one line, or on a value outside
    UNIT_TEST_STATUS_VALUES, matching _parse_choice's own strictness.
    """
    values = [
        match.group("value")
        for match in FIELD_LINE.finditer(output)
        if match.group("field") == UNIT_TEST_STATUS_FIELD
    ]
    if not values:
        return ""
    if len(values) > 1:
        raise OutputContractError(
            f"expected at most one {UNIT_TEST_STATUS_FIELD}, got {values}"
        )
    if values[0] not in UNIT_TEST_STATUS_VALUES:
        raise OutputContractError(
            f"invalid {UNIT_TEST_STATUS_FIELD} {values[0]}"
        )
    return values[0]


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



def _parsed_quality_gate(spec: str) -> tuple[tuple[str, str], ...]:
    """The quality-gate block read back out of a rendered spec, as sorted pairs.

    Parsed from the dispatched text rather than from the builder's return value: the
    question these records exist to answer is what the agent was handed, and only the
    payload can answer it.
    """
    return tuple(sorted(parse_quality_gate(spec).items()))


def _parsed_agent_routing(spec: str) -> tuple[tuple[str, str], ...]:
    """The routing block parsed back out of a rendered spec; () when absent.

    Absent is the normal answer: only a run that selected a profile renders one.
    """
    if AGENT_ROUTING_SPEC_HEADER not in spec:
        return ()
    return tuple(sorted(parse_agent_routing(spec).items()))


def _parsed_risk_profile(spec: str) -> tuple[tuple[str, str], ...]:
    """The risk block read back out of a rendered spec, as sorted pairs.

    Returns () when the spec carries no block at all -- the honest answer for a skill
    with no risk axis, and the reason this does not call parse_risk_profile()
    unguarded (that function raises on an absent block, by design).
    """
    if RISK_SPEC_HEADER not in spec:
        return ()
    return tuple(sorted(parse_risk_profile(spec).items()))


def final_review_artifact_path(run_id: str, attempt: int) -> str:
    """W-A1-N: attempt 1 is unsuffixed; attempt N>=2 carries _iteration<N>.

    There is deliberately no `_iteration1` form -- it would break the parallel with
    artifacts/runs/<run_id>/REVIEW_<PHASE>.md, which uses the same rule. The run_id
    prefix (run_artifact_root, task_context's single root builder) is what keeps two
    runs' Final Review artifacts from landing in the same shared artifacts/ root.
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    suffix = "" if attempt == 1 else f"_iteration{attempt}"
    return f"{run_artifact_root(run_id)}FINAL_REVIEW{suffix}.md"


def lower_to_requested_phase(phase: str, requested: tuple[str, ...]) -> str | None:
    """Ladder rule 3: map a responsible phase onto the requested phase set.

    Returns the phase itself when it is requested; otherwise the last requested
    canonical phase at or below it; otherwise None, which the caller turns into
    OUT_OF_SCOPE_FINAL_REVIEW_FINDING.

    There is deliberately NO forward ("above") fallback: SKILL.md section 17's ladder
    has exactly two branches after the nature mapping -- step 3 lowers, step 4
    escalates. (PR #11 human review, MAJOR 2.)
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
    return None


def downstream_revalidation_set(
    corrected: Iterable[str], requested: tuple[str, ...]
) -> tuple[str, ...]:
    """SKILL.md section 17 D: every requested phase strictly after the EARLIEST
    corrected phase, in canonical order.

    Only canonical phases carry an order, so a run whose corrected set is entirely
    specialized (bugfix / refactoring) yields (), which makes T5a a no-op and leaves
    single-phase runs byte-identical to the pre-T5a behaviour.

    The result is ordered by CANONICAL_PHASES, not by `requested`, so it is
    deterministic even if a caller passes `phases=` out of canonical order -- the
    same authority `lower_to_requested_phase` already uses.
    """
    indices = [
        CANONICAL_PHASES.index(p) for p in corrected if p in CANONICAL_PHASES
    ]
    if not indices:
        return ()
    return tuple(p for p in CANONICAL_PHASES[min(indices) + 1:] if p in requested)


def _require_finding_field(
    pattern: re.Pattern[str], block: str, finding_id: str, field: str
) -> re.Match[str]:
    """The match for a REQUIRED finding field, or a contract error naming it.

    One helper for all three so no field can end up with a quiet default while its
    siblings raise -- which is exactly how `Quality Attribute:` and `Severity:` drifted
    apart from `Blocking:`.
    """
    found = pattern.search(block)
    if found is None:
        raise OutputContractError(f"finding {finding_id} has no {field} field")
    return found


def parse_final_review_output(
    output: str, contract: WorkflowOutputContract
) -> tuple[str, tuple[FinalFinding, ...]]:
    """Verdict + every finding the report carries, blocking and non-blocking alike.

    Delegates the verdict and the blocking id set to the EXISTING
    parse_reviewer_output -- its signature is bound by several tests and is not
    changed -- then walks BOTH finding sections pairing each `ID:` with the
    `Quality Attribute:`, `Blocking:` and `Responsible Phase:` lines that follow it
    before the next `ID:`.

    Reading both sections is the point. While only the blocking section was parsed, a
    non-blocking finding was invisible to the caller, so "notes do not start a
    correction loop" was true because the parser never saw them -- not because
    anything honoured `Blocking:`. The caller now sees every finding and has to decide
    on the field, which is what makes the OS-1 severity/blocking split observable.

    Every contract field -- `Quality Attribute:`, `Severity:` and `Blocking:` -- is
    REQUIRED, and none of them is inferred from the section a finding sits in or from
    a default. Inferring would re-derive a field from exactly the signal it exists to
    replace, and defaulting is worse: a report that dropped the line would parse into
    a finding this function invented, and every downstream assertion about that field
    would then be an assertion about the default. `Severity:` was defaulted rather
    than parsed until iteration 3, which silently made an equal-severity control read
    MAJOR == MAJOR no matter what the report said.

    The one forbidden combination is rejected here too: `Quality Attribute: NONE`
    with `Blocking: YES`. A finding charged to no attribute is a generic observation,
    and a blocking one is charged to a project attribute id or a General Gate id --
    so the pair names no criterion the run could act on.
    """
    verdict, _ = parse_reviewer_output(output, contract)
    sections = {
        match.group("title").strip(): match.group("body")
        for match in SECTION.finditer(output)
    }
    findings: list[FinalFinding] = []
    for title in ("Blocking Findings", "Non-Blocking Findings"):
        body = sections.get(title, "")
        matches = list(FINDING_LINE.finditer(body))
        for position, match in enumerate(matches):
            start = match.end()
            end = (
                matches[position + 1].start()
                if position + 1 < len(matches)
                else len(body)
            )
            block = body[start:end]
            finding_id = match.group("id")
            attribute = _require_finding_field(
                QUALITY_ATTRIBUTE_LINE, block, finding_id, "Quality Attribute"
            ).group("attribute")
            severity = _require_finding_field(
                SEVERITY_LINE, block, finding_id, "Severity"
            ).group("severity")
            blocking = (
                _require_finding_field(
                    BLOCKING_LINE, block, finding_id, "Blocking"
                ).group("blocking")
                == "YES"
            )
            if attribute == UNCHARGED_QUALITY_ATTRIBUTE and blocking:
                raise OutputContractError(
                    f"finding {finding_id} is Blocking: YES with "
                    f"Quality Attribute: {UNCHARGED_QUALITY_ATTRIBUTE}; a blocking "
                    "finding is charged to a project attribute id or a General Gate "
                    "id, never to NONE"
                )
            phase_match = RESPONSIBLE_PHASE_LINE.search(block)
            findings.append(
                FinalFinding(
                    finding_id,
                    phase_match.group("phase") if phase_match is not None else None,
                    severity=severity,
                    quality_attribute=attribute,
                    blocking=blocking,
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
        session_policy: str = "reuse",
        run_id: str = "run_e2e",
        risk: str | None = None,
        agent_profile: str | None = None,
    ) -> None:
        self.contract = load_workflow_output_contract(skill_path)
        # OS-3. Resolved ONCE, from the skill this harness was constructed for,
        # exactly the way evaluate_invocation() distinguishes the two skills. None
        # means "this skill has no risk axis", which is what orca-worker-reviewer-loop
        # yields with no edit to that file -- so its dispatched specs stay
        # byte-identical and its SessionEvents keep risk_profile == ().
        self.risk_contract = load_risk_contract(skill_path)
        self.supports_risk = self.risk_contract is not None
        self.risk, self.risk_source = self._resolve_risk(risk, skill_path)
        self.phase = phase
        self.max_iterations = max_iterations
        self.workspace = workspace
        self.protected_artifacts = protected_artifacts
        # The directory identity every artifact_contract this instance builds is
        # scoped under. run_workflow() overwrites this from scenario.run_id before
        # its phase loop starts; a bare .run() call (no run_workflow) keeps this
        # default, which is why it is a real, non-empty run id rather than "".
        self.run_id = run_id
        # Resolved once, from this instance's own workspace rather than the real
        # repository, for the same reason the artifact root is: a deterministic
        # scenario must not change its dispatched Task specs because the checkout it
        # happens to run inside grew a `.orca/quality-profile.yaml`.
        self.quality_profile = resolve_quality_profile(workspace)
        # OS-4: materialized ONCE, from this instance's own workspace, exactly like
        # the quality profile above and for the same reason. None when no profile was
        # selected -- and None is what makes every downstream site skip the routing
        # block, the ledger identity and the evidence rows, so a profile-less run
        # produces the bytes it produced before OS-4.
        self.agent_profile_name = agent_profile
        # Kept so run_workflow() can re-materialize for the scenario's full phase set
        # once it knows what that set is -- the constructor cannot.
        self._skill_path = skill_path
        self.agent_routing = self._resolve_agent_routing(
            skill_path, workspace, (self.phase,)
        )
        # Provisioned immediately, under workspace (this instance's own scratch
        # directory) rather than the real repository's artifacts/ root, before the
        # first Worker/Reviewer subprocess -- run with cwd=workspace -- could be
        # told to write inside a directory nothing has created yet.
        ensure_run_artifact_root(self.run_id, base=self.workspace)
        # The first three MUST be mutable objects. _phase_harness() is a copy.copy(),
        # so a list / dict / count object is shared BY REFERENCE with every clone and
        # the state therefore survives phase, correction and revalidation boundaries.
        # An int counter attribute would be re-bound inside the clone, invisible to
        # the parent, and ids would collide -- hence itertools.count. Putting any of
        # them on the class would share them across instances and poison tests.
        self.sessions: list[SessionEvent] = []
        self._session_ids: dict[str, str] = {}
        self._session_counter = itertools.count(1)
        self.session_policy = session_policy

    def _resolve_risk(
        self, risk: str | None, skill_path: Path
    ) -> tuple[str | None, str | None]:
        """The (level, source) pair this harness is frozen at. Called once, from
        __init__.

        The ONLY place a risk level is resolved for this harness, and the only place
        the contract default is applied. run_workflow() may later re-resolve it, but
        only when a scenario supplies an explicit value -- never by falling back to
        the default again, which is how an explicitly constructed LOW would get
        silently promoted to HIGH.
        """
        if not self.supports_risk:
            if risk is not None:
                raise RiskNotSupportedError(
                    f"RISK_NOT_SUPPORTED: {skill_path.parent.name} has no "
                    f"'#### Risk profile contract' block, so risk={risk!r} cannot be "
                    "honoured; no harness is constructed"
                )
            return None, None
        if risk is None:
            return self.risk_contract["RISK_DEFAULT"][0], "default"
        folded = risk.casefold()
        if folded not in RISK_LEVELS:
            raise ValueError(f"INVALID_RISK: {risk!r}")
        return folded, "explicit"

    def _risk_or_default(self) -> str:
        """The strength this harness actually enforces, as a level name.

        A skill with no risk axis (self.risk is None) behaves exactly as it did
        before OS-3, which is the HIGH-equivalent path -- so every risk conditional
        reads this instead of comparing self.risk to a literal. Comparing the raw
        None against "high" would silently DISABLE T5a for the loop skill, which is a
        behaviour change, not a no-op.
        """
        return self.risk or "high"

    def risk_context(self) -> dict[str, str] | None:
        """The risk model both fake agents receive this phase, or None.

        The same one-call-both-roles shape as quality_gate(). Returns None for a
        skill whose SKILL.md carries no risk block; render_task_spec() skips a None
        block entirely, so that skill's dispatched payload stays byte-identical.
        """
        if not self.supports_risk:
            return None
        return build_risk_context(
            risk=self.risk,
            risk_source=self.risk_source,
            current_phase=self.phase,
        )

    def _resolve_agent_routing(
        self, skill_path: Path, workspace: Path, requested_phases: tuple[str, ...]
    ):
        """Materialize this run's routing once, for EVERY requested phase.

        `requested_phases` is the whole set the run will execute, not the phase this
        instance happens to be pointed at. A single-phase materialization looks right
        for a `.run()` call and is wrong for `run_workflow()`: the phase clones share
        this object by reference, so any phase missing from it would render as
        not_applicable in that phase's own dispatch.

        Reads the profile from `workspace`, never the real repository or the real
        home directory: a deterministic scenario must not change what it dispatches
        because the checkout it happens to run inside grew an agent-profiles.yaml.
        """
        if self.agent_profile_name is None:
            return None
        selection = select_agent_profile(
            self.agent_profile_name, project_root=workspace, home=workspace
        )
        if not selection.is_selected:
            raise AgentProfileError(
                f"agent profile {self.agent_profile_name!r}: {selection.error}",
                reason=selection.reason,
            )
        return materialize_run_routing(
            runtime=(
                RUNTIME_LOOP
                if skill_path.parent.name.endswith("-loop")
                else RUNTIME_ORCHESTRATION
            ),
            selection=selection,
            requested_phases=requested_phases,
            risk=self.risk,
        )

    def agent_routing_context(self) -> dict[str, str] | None:
        """The routing block both fake agents receive this phase, or None.

        Same one-call-both-roles shape as quality_gate() and risk_context(). None on
        the legacy path, and render_task_spec() then omits the block entirely.
        """
        if self.agent_routing is None:
            return None
        return build_agent_routing_context(
            routing=self.agent_routing, current_phase=self.phase
        )

    def final_review_routing_context(self) -> dict[str, str] | None:
        """The routing block a Final Review dispatch receives, or None.

        Built against the final_review slot rather than a workflow phase, so
        `final_reviewer` carries the resolved command and the phase roles read
        not_applicable -- a Final Review attempt has no Worker.
        """
        if self.agent_routing is None:
            return None
        return build_agent_routing_context(
            routing=self.agent_routing, current_phase=FINAL_REVIEW_PHASE
        )

    def agent_routing_report_lines(self) -> tuple[str, ...]:
        """The conditional final-report evidence, or () when no profile was selected.

        () is not a formatting detail: a profile-less run's final report must not grow
        an `AGENT_PROFILE:` line, not even one reading "none".
        """
        if self.agent_routing is None:
            return ()
        lines = [
            f"AGENT_PROFILE: {self.agent_routing.profile_name} "
            f"({self.agent_routing.profile_source})",
            "AGENT_ROUTING:",
        ]
        for entry in self.agent_routing.entries:
            command = entry.command or "unresolved"
            suffix = "" if entry.required else ", optional"
            lines.append(
                f"  {entry.phase} {entry.role}={command} "
                f"({entry.origin or 'none'}{suffix})"
            )
        return tuple(lines)

    def allocate_session(
        self,
        role: str = "",
        phase: str = "",
        iteration: int = 0,
        *,
        policy: str = "reuse",
    ) -> tuple[str, bool]:
        """S-R0..S-R7. Returns (session_id, created).

        `phase` and `iteration` are accepted and deliberately unused: every rule keys
        on role alone, and the caller records those two on the SessionEvent. Keeping
        them in the signature keeps the call site self-describing and leaves room for
        a phase-scoped policy without moving the call sites.
        """
        if policy not in SESSION_POLICIES:
            policy = "fresh"        # defence in depth; run_workflow rejects first
        if role == "final_review":                                      # S-R1
            return f"sess_{next(self._session_counter)}", True
        if policy == "reuse" and role in self._session_ids:             # S-R4
            return self._session_ids[role], False
        session_id = f"sess_{next(self._session_counter)}"              # S-R2 / S-R3
        self._session_ids[role] = session_id
        return session_id, True

    def invalidate_session(self, role: str = "") -> None:               # S-R7
        """Drop this role's chain so the next round starts a fresh session."""
        self._session_ids.pop(role, None)

    def _record_session(
        self,
        role: str,
        iteration: int,
        *,
        task_boundary: tuple[tuple[str, str], ...] = (),
        reviewer_context_keys: tuple[str, ...] = (),
        quality_gate: tuple[tuple[str, str], ...] = (),
        risk_profile: tuple[tuple[str, str], ...] = (),
        agent_routing: tuple[tuple[str, str], ...] = (),
    ) -> SessionEvent:
        """Append-only: allocate (or reuse) this role's session and record the fact."""
        session_id, created = self.allocate_session(
            role, self.phase, iteration, policy=self.session_policy
        )
        event = SessionEvent(
            role=role,
            phase=self.phase,
            iteration=iteration,
            session_id=session_id,
            created=created,
            agent_command=SESSION_AGENT_COMMANDS.get(role, ""),
            task_boundary=task_boundary,
            reviewer_context_keys=reviewer_context_keys,
            quality_gate=quality_gate,
            risk_profile=risk_profile,
            agent_routing=agent_routing,
        )
        self.sessions.append(event)
        return event

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
            sessions=tuple(self.sessions),
        )

    def quality_gate(self) -> dict[str, object]:
        """The profile-first quality model both fake agents receive this phase.

        One call, one resolution, both roles: the Worker and the Reviewer of a phase
        must not be handed two different answers to "which quality attributes apply
        here", and building it in one place is what makes that structural.
        """
        return build_quality_gate_context(
            resolution=self.quality_profile,
            current_phase=self.phase,
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
            # Built BEFORE the command, and rendered INTO it: this harness has no
            # Orca preamble to inject a Task spec, so --task-spec is the agent-visible
            # payload, and the fake echoes a receipt parsed back out of it.
            worker_boundary = build_task_boundary(
                current_role="worker",
                current_phase=self.phase,
                current_iteration=iteration,
                artifact_contract=phase_artifact_contract(
                    role="worker", phase=self.phase, run_id=self.run_id
                ),
                relevant_previous_findings=tuple(sorted(previous_blocking_findings)),
            )
            # One string, bound once: the text handed to the subprocess and the text
            # the session event records are the same object, so a test reading the
            # event is reading what the agent was actually given.
            worker_spec = render_task_spec(
                f"worker {self.phase} iteration {iteration}",
                worker_boundary,
                None,
                self.quality_gate(),
                self.risk_context(),
                self.agent_routing_context(),
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
                "--task-spec",
                worker_spec,
            ]
            unit_test_status = (
                scenario.worker_unit_test_statuses[iteration - 1]
                if iteration <= len(scenario.worker_unit_test_statuses)
                else ""
            )
            if unit_test_status:
                worker_command.extend(["--unit-test-status", unit_test_status])
            raw_statuses = (
                scenario.worker_unit_test_status_lines[iteration - 1]
                if iteration <= len(scenario.worker_unit_test_status_lines)
                else ()
            )
            for raw in raw_statuses:
                worker_command.extend(["--unit-test-status-raw", raw])
            self._record_session(
                "worker",
                iteration,
                task_boundary=tuple(sorted(worker_boundary.items())),
                quality_gate=_parsed_quality_gate(worker_spec),
                risk_profile=_parsed_risk_profile(worker_spec),
                agent_routing=_parsed_agent_routing(worker_spec),
            )
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
                # Parsed inside the SAME try block, so a duplicate or unrecognized
                # value reuses the existing MALFORMED_WORKER_OUTPUT error path
                # rather than inventing a second one.
                reported_unit_test_status = parse_unit_test_status(worker.stdout)
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
                    sessions=tuple(self.sessions),
                )

            # ---- OS-3 section 14 safety floor. LOW requires AFFIRMATIVE evidence:
            # silence is exactly what section 14 forbids, and LOW has no phase
            # Reviewer to notice it. MEDIUM/HIGH are unchanged -- the phase Reviewer
            # stays the documented enforcer there.
            if (
                self.phase in UNIT_TEST_GATED_PHASES
                and self._risk_or_default() == "low"
                and reported_unit_test_status != UNIT_TEST_STATUS_PASS
            ):
                return WorkflowResult(
                    current_phase=self.phase,
                    current_iteration=iteration,
                    max_iterations=self.max_iterations,
                    worker_attempts=worker_attempts,
                    reviewer_attempts=reviewer_attempts,
                    findings=finding_traces,
                    final_status=self.contract.blocked_status,
                    reason=(
                        UNIT_TEST_BLOCKED_REASON
                        if reported_unit_test_status == UNIT_TEST_STATUS_BLOCKED
                        else UNIT_TEST_EVIDENCE_MISSING_REASON
                    ),
                    sessions=tuple(self.sessions),
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

            # ---- OS-3: at LOW the phase gate IS the Worker result. Every
            # Worker-side guard above has already run; the Reviewer half below is
            # skipped entirely, so no Reviewer Task/Dispatch is ever recorded.
            if self._risk_or_default() == "low":
                return WorkflowResult(
                    current_phase=self.phase,
                    current_iteration=iteration,
                    max_iterations=self.max_iterations,
                    worker_attempts=worker_attempts,
                    reviewer_attempts=reviewer_attempts,
                    findings=finding_traces,
                    final_status=self.contract.completed_status,
                    sessions=tuple(self.sessions),
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
            reviewer_boundary = build_task_boundary(
                current_role="reviewer",
                current_phase=self.phase,
                current_iteration=iteration,
                artifact_contract=phase_artifact_contract(
                    role="reviewer", phase=self.phase, run_id=self.run_id
                ),
                relevant_previous_findings=tuple(sorted(previous_blocking_findings)),
            )
            reviewer_context = build_reviewer_context(
                original_objective=f"e2e:{self.phase}",
                current_phase=self.phase,
                approved_baseline=(),
                current_delta=(worker.stdout,),
                new_claims=tuple(sorted(parsed_resolutions)),
                previous_findings=tuple(
                    (finding_id, parsed_resolutions.get(finding_id, ""))
                    for finding_id in sorted(previous_blocking_findings)
                ),
                validation=(worker_status,),
                drill_down=(str(self.workspace),),
            )
            reviewer_spec = render_task_spec(
                f"reviewer {self.phase} iteration {iteration}",
                reviewer_boundary,
                reviewer_context,
                self.quality_gate(),
                self.risk_context(),
                self.agent_routing_context(),
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
                "--task-spec",
                reviewer_spec,
            ]
            if self.protected_artifacts:
                reviewer_command.extend(
                    ["--artifact", str(self.protected_artifacts[0])]
                )
            self._record_session(
                "reviewer",
                iteration,
                task_boundary=tuple(sorted(reviewer_boundary.items())),
                reviewer_context_keys=tuple(sorted(reviewer_context)),
                quality_gate=_parsed_quality_gate(reviewer_spec),
                risk_profile=_parsed_risk_profile(reviewer_spec),
                agent_routing=_parsed_agent_routing(reviewer_spec),
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
                    sessions=tuple(self.sessions),
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
            sessions=tuple(self.sessions),
        )

    def _phase_harness(self, phase: str, budget: int) -> "E2EHarness":
        """A shallow clone that runs `run()` for one phase with a bounded budget.

        run() is the single-phase authority and this clone does not change its
        behaviour; the clone only varies the two attributes it reads (`phase`,
        `max_iterations`). The contract, workspace and protected artifacts are
        shared by reference on purpose -- the protected-artifact guard must see
        the same files the parent protects.
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
        self, attempt: int, mode: str, findings: tuple[FinalFindingSpec, ...]
    ) -> tuple[str | None, tuple[FinalFinding, ...], AgentAttempt | None]:
        """One Final Adversarial Review dispatch: a Reviewer-only invocation.

        There is no Worker in a Final Review attempt, so this does not go through
        run(); it reuses the same fake_reviewer.py subprocess, the same protected-
        artifact hashing guard, and the same contract fields.

        When the returned AgentAttempt is None a guard tripped, and the first slot
        carries the error reason instead of a verdict.
        """
        normalized = [normalize_final_finding_spec(spec) for spec in findings]
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
            json.dumps([spec[0] for spec in normalized]),
            "--responsible-phases-json",
            json.dumps(
                {spec[0]: spec[1] for spec in normalized}, sort_keys=True
            ),
            "--quality-attributes-json",
            json.dumps({spec[0]: spec[2] for spec in normalized}, sort_keys=True),
            "--blocking-json",
            json.dumps({spec[0]: spec[3] for spec in normalized}, sort_keys=True),
        ]
        if self.protected_artifacts:
            command.extend(["--artifact", str(self.protected_artifacts[0])])
        # OS-4: the Final Reviewer's own routing reaches the dispatch and the session
        # record. Rendered ONLY when a profile was selected -- a legacy Final Review
        # attempt keeps the exact argv it had before OS-4, with no --task-spec.
        final_routing = self.final_review_routing_context()
        recorded_routing: tuple[tuple[str, str], ...] = ()
        if final_routing is not None:
            final_spec = render_task_spec(
                f"final_review attempt {attempt}",
                build_task_boundary(
                    current_role="final_reviewer",
                    current_phase=FINAL_REVIEW_PHASE,
                    current_iteration=attempt,
                    artifact_contract=phase_artifact_contract(
                        role="final_reviewer",
                        phase=FINAL_REVIEW_PHASE,
                        run_id=self.run_id,
                    ),
                ),
                None,
                None,
                None,
                final_routing,
            )
            command.extend(["--task-spec", final_spec])
            recorded_routing = _parsed_agent_routing(final_spec)
        self._record_session(
            "final_review", attempt, agent_routing=recorded_routing
        )
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

    def _write_final_review_audit(self, attempt_number: int, attempt) -> None:
        """Materialize this attempt's review record, then audit the dispatch.

        The review record is written to the section 9 laddered path this harness
        already computes -- `final_review_artifact_path()` named it and nothing
        materialized it, so the contracted artifact existed only as a string. A real
        Final Reviewer writes that file; writing it here is what lets the audit
        record snapshot a report rather than an absence.

        Identities are this harness's own deterministic synthetic ones: there is no
        Orca Task or Dispatch behind a fake-agent subprocess, and inventing one that
        looked like a real orca id would put a fabricated identity into an evidence
        record. `capture=False` for the same reason -- there is no run to read back.

        A logging failure never changes a lifecycle judgement, so this swallows.
        """
        try:
            report = (
                ensure_run_artifact_root(self.run_id, base=self.workspace)
                / Path(final_review_artifact_path(self.run_id, attempt_number)).name
            )
            report.write_text(attempt.output, encoding="utf-8")
            capture_status, parse_status = run_logging.probe_final_review_report(
                self.run_id, attempt_number, base=self.workspace
            )
            provenance, void_reason, settlement = (
                run_logging.resolve_final_review_provenance(
                    settled=True,
                    report_capture_status=capture_status,
                    report_parse_status=parse_status,
                )
            )
            run_logging.write_final_review_audit_record(
                self.run_id,
                base=self.workspace,
                final_review_attempt=attempt_number,
                task_id=f"task_e2e_final_review_{attempt_number}",
                dispatch_id=f"ctx_e2e_final_review_{attempt_number}",
                provenance_state=provenance,
                void_reason=void_reason,
                settlement_state=settlement,
                reviewer_agent_origin="e2e_harness",
                capture=False,
            )
        except Exception:  # noqa: BLE001 -- section 9: logging never mutates state
            return

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
        revalidation_dispatches: list[tuple[str, int]] = []
        corrected_findings: list[tuple[int, str, str, str]] = []
        final_review_attempts: list[AgentAttempt] = []
        final_review_artifacts: list[str] = []
        final_review_iterations = 0
        final_review_verdict: str | None = None
        reviewer_gates_skipped: list[str] = []

        def snapshot(
            final_status: str = "", reason: str | None = None
        ) -> WorkflowRunResult:
            return WorkflowRunResult(
                phases=scenario.phases,
                phase_iterations=dict(phase_iterations),
                final_review_iterations=final_review_iterations,
                final_review_attempts=list(final_review_attempts),
                correction_dispatches=list(correction_dispatches),
                revalidation_dispatches=list(revalidation_dispatches),
                final_review_verdict=final_review_verdict,
                final_status=final_status,
                final_review_artifacts=tuple(final_review_artifacts),
                corrected_findings=tuple(corrected_findings),
                reason=reason,
                sessions=tuple(self.sessions),
                risk=self.risk,
                risk_source=self.risk_source,
                reviewer_gates_skipped=list(reviewer_gates_skipped),
                agent_profile_report=self.agent_routing_report_lines(),
            )

        # The only write of session_policy in this run: every _phase_harness clone
        # copies it by value, so the phase gate, correction and revalidation rounds
        # all read the scenario's policy without run() ever seeing it (S-R0).
        policy = scenario.session_policy
        if policy not in SESSION_POLICIES:
            return self._workflow_error(
                "SCENARIO_SESSION_POLICY_INVALID:" + policy, snapshot()
            )
        self.session_policy = policy
        # ---- OS-3 guards, capability first then value ------------------------------
        if scenario.risk is not None and not self.supports_risk:
            return self._workflow_error(
                "SCENARIO_RISK_NOT_SUPPORTED:" + scenario.risk, snapshot()
            )
        if scenario.risk is not None and scenario.risk.casefold() not in RISK_LEVELS:
            return self._workflow_error(
                "SCENARIO_RISK_INVALID:" + scenario.risk, snapshot()
            )
        # PRECEDENCE: an explicit scenario value overrides; an omitted one PRESERVES
        # what __init__ already resolved. There is deliberately no `or RISK_DEFAULT`
        # fallback here -- _resolve_risk() already applied the default, and
        # re-applying it is how an explicitly constructed LOW gets promoted to HIGH.
        if scenario.risk is not None:
            self.risk = scenario.risk.casefold()
            self.risk_source = "explicit"
        risk = self._risk_or_default()
        # The one write of run_id for this workflow: every _phase_harness clone below
        # copies it by value (same mechanism as session_policy), so the phase gates,
        # corrections, revalidations and the Final Review artifacts that follow all
        # land under the SAME artifacts/runs/<run_id>/ directory.
        self.run_id = scenario.run_id
        # Re-provisioned here because run_id may differ from the constructor's
        # default: the phase loop below dispatches into this directory immediately.
        ensure_run_artifact_root(self.run_id, base=self.workspace)
        # OS-4: the routing for THIS workflow's whole requested phase set, resolved
        # once here -- before the first dispatch, which is this harness's equivalent
        # of "before the Run exists". The constructor could only see one phase; the
        # clones below share this object by reference, so every phase gate,
        # correction, revalidation and the Final Review read the same resolution and
        # none of them re-reads the profile file.
        self.agent_routing = self._resolve_agent_routing(
            self._skill_path, self.workspace, scenario.phases
        )

        def gate_attempts(result: WorkflowResult) -> int:
            """SKILL.md section 13: a gate attempt is a Reviewer attempt at
            MEDIUM/HIGH and a Worker attempt at LOW, so the per-phase budget stays
            reachable -- and the dispatch ledgers stay non-empty -- at every level."""
            return len(
                result.worker_attempts if risk == "low" else result.reviewer_attempts
            )

        # ---- sequential phase gates (SKILL.md section 8: PASS before the next phase)
        for phase in scenario.phases:
            if risk == "low":
                # A skipped gate is recorded positively: the absence of a Reviewer
                # row must never be the only evidence that one was skipped.
                reviewer_gates_skipped.append(phase)
            phase_scenario = scenario.phase_scenarios.get(phase)
            if phase_scenario is None:
                return self._workflow_error(
                    "SCENARIO_PHASE_MISSING:" + phase, snapshot()
                )
            result = self._phase_harness(phase, self.max_iterations).run(phase_scenario)
            phase_iterations[phase] += gate_attempts(result)
            if result.final_status != self.contract.completed_status:
                # S-R7: a round that did not PASS leaves both roles in recovery, so
                # neither chain may be carried forward.
                self.invalidate_session("worker")
                self.invalidate_session("reviewer")
                # BLOCKED worker, malformed output, or the phase's own budget exhausted:
                # the gate is never reached and the phase's status/reason is propagated.
                return snapshot(result.final_status, result.reason)

        while True:
            # ---- T0: every requested phase has PASSed. Open a fresh attempt.
            final_review_iterations += 1
            final_review_artifacts.append(
                final_review_artifact_path(self.run_id, final_review_iterations)
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
            # OS-22: the same snapshot -> redact -> write path the live runtime takes,
            # here, immediately after the attempt settles and before the T1/T2/T3
            # branch below reads the verdict. Placed here for the same reason it is
            # placed there: attempt N+1 renders the same unsuffixed FINAL_REVIEW.md
            # on the real dispatch path, so a snapshot deferred to run end is a
            # snapshot of somebody else's report.
            self._write_final_review_audit(final_review_iterations, attempt)

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
            #      Only blocking findings are routed at all.
            # Blocking is the routing axis, not severity and not which section the
            # reviewer printed the finding under. A MAJOR finding charged to a
            # non-blocking quality attribute is a note: it is reported and it is not
            # corrected. A FAIL carrying no blocking finding at all contradicts its
            # own verdict, which is the malformed case the next line still catches.
            blocking_findings = [finding for finding in findings if finding.blocking]
            if not blocking_findings:
                return self._workflow_error(
                    "MALFORMED_FINAL_REVIEW_OUTPUT", snapshot()
                )
            routed: dict[str, list[str]] = {}
            for finding in blocking_findings:
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
                for offset in range(1, gate_attempts(result) + 1):
                    correction_dispatches.append(
                        (phase, phase_iterations[phase] + offset)
                    )
                phase_iterations[phase] += gate_attempts(result)
                # ---- T4a: the finding-resolution bridge, evaluated BEFORE the round's own
                #      status, because run() would have fired it at local iteration 1 -- i.e.
                #      before this round's first Reviewer ever ran.
                if bridge_reason is not None:
                    self.invalidate_session("worker")        # S-R7
                    self.invalidate_session("reviewer")
                    return self._workflow_error(
                        f"{FINAL_REVIEW_RESOLUTION_REASON} ({phase}): {bridge_reason}",
                        snapshot(),
                    )
                if result.final_status != self.contract.completed_status:
                    self.invalidate_session("worker")        # S-R7
                    self.invalidate_session("reviewer")
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

            # ---- T5a: DOWNSTREAM REVALIDATION.
            # Every requested phase strictly after the EARLIEST phase corrected in this
            # attempt is re-run as a full Worker->Reviewer gate, in canonical order,
            # UNCONDITIONALLY -- whether or not the correction is judged to have changed
            # anything. A fresh Final Review attempt is an ADDITIONAL global gate, never a
            # substitute for this. (PR #11 human review, MAJOR 1.)
            #
            # Note the call: _phase_harness(...).run(...), the SAME pattern the initial
            # phase-gate loop above uses -- NOT _run_correction_round(...). A revalidation
            # round has no routed finding ids, so the section-3.2.7 resolution bridge must
            # not fire on it; routing it through _run_correction_round would make every
            # revalidation ERROR with FINAL_REVIEW_RESOLUTION_TRACE_INCOMPLETE. (Risk D-17.)
            # OS-3: D is computed and executed at HIGH only. At LOW and MEDIUM T5a
            # is a no-op. downstream_revalidation_set() itself is unchanged -- the
            # CALL SITE is gated, which is smaller and safer than gating a pure
            # function other callers depend on.
            downstream = (
                downstream_revalidation_set(responsible, scenario.phases)
                if risk == "high"
                else ()
            )
            for phase in downstream:
                if phase_iterations[phase] >= self.max_iterations:
                    return snapshot(
                        self.contract.escalated_status,
                        f"MAX_ITERATIONS_REACHED ({phase})",
                    )
                revalidation = scenario.revalidation_scenarios.get(
                    (phase, final_review_iterations)
                )
                if revalidation is None:
                    return self._workflow_error(
                        "SCENARIO_REVALIDATION_MISSING", snapshot()
                    )
                budget = self.max_iterations - phase_iterations[phase]
                result = self._phase_harness(phase, budget).run(revalidation)
                # Ledger BEFORE verdict, exactly as T4: these Reviewer dispatches
                # physically happened and are never rewound.
                for offset in range(1, gate_attempts(result) + 1):
                    revalidation_dispatches.append(
                        (phase, phase_iterations[phase] + offset)
                    )
                phase_iterations[phase] += gate_attempts(result)
                if result.final_status != self.contract.completed_status:
                    self.invalidate_session("worker")        # S-R7
                    self.invalidate_session("reviewer")
                if result.final_status == self.contract.escalated_status:
                    return snapshot(
                        self.contract.escalated_status,
                        f"MAX_ITERATIONS_REACHED ({phase})",
                    )
                if result.final_status != self.contract.completed_status:
                    return snapshot(result.final_status, result.reason)
                # No corrected_findings row: a revalidation resolves no finding.
            # ---- T5: every corrected and revalidated phase PASSed. Loop to T0 for a fresh attempt.
