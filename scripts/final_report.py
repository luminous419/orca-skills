#!/usr/bin/env python3
"""The Final Report each skill specifies, rendered exactly as its SKILL.md states it.

There are TWO templates, not one, and the difference is not cosmetic:

  orca-worker-reviewer-orchestration  SKILL.md section 16
      STATUS PHASES RISK RISK_SOURCE COMPLETED_PHASES WORKER REVIEWER
      ITERATIONS_BY_PHASE FINAL_REVIEW_ITERATIONS
      ## Summary / ## Changed Files / Artifacts / ## Unit Tests / Validation
      ## Orca Orchestration State
      ## Final Adversarial Review  (FINAL_REVIEW, FINAL_REVIEW_TASKS,
                                    FINAL_FINDINGS, FINAL_REVIEW_REVALIDATIONS)
      ## Non-Blocking Recommendations

  orca-worker-reviewer-loop           SKILL.md section 28
      STATUS PHASES COMPLETED_PHASES WORKER REVIEWER ITERATIONS_BY_PHASE
      ## Summary / ## Changed Files / Artifacts / ## Unit Tests / ## Validation
      ## Final Review  (RESULT)
      ## Non-Blocking Recommendations

The loop template has no RISK, no RISK_SOURCE, no FINAL_REVIEW_ITERATIONS and no
Final Adversarial Review block, because that skill has no risk axis and no such gate.
Emitting those fields there would report a lifecycle the skill does not run, and
splitting `## Unit Tests / Validation` (orchestration) from the two separate
`## Unit Tests` and `## Validation` sections (loop) is a real difference between the
two contracts rather than a formatting choice.

scripts/test_e2e_harness.py's FinalReportContractTests parses both templates out of
the SKILL.md files themselves and checks this renderer field by field, so the
contract is the documents -- not this module's opinion of them.

OS-4 adds exactly two CONDITIONAL lines after the header block, and only when a
profile was selected. A run without one renders no `AGENT_PROFILE:` line at all --
not one reading "none" -- because an absent line and a line saying "none" are
different reports.
"""

from __future__ import annotations

from typing import Any

ORCHESTRATION_SKILL = "orca-worker-reviewer-orchestration"
LOOP_SKILL = "orca-worker-reviewer-loop"

# The header keys each template declares, in the order the template declares them.
# The tests read these back out of the SKILL.md fences and compare.
ORCHESTRATION_HEADER_KEYS = (
    "STATUS",
    "PHASES",
    "RISK",
    "RISK_SOURCE",
    "COMPLETED_PHASES",
    "WORKER",
    "REVIEWER",
    "ITERATIONS_BY_PHASE",
    "FINAL_REVIEW_ITERATIONS",
)
LOOP_HEADER_KEYS = (
    "STATUS",
    "PHASES",
    "COMPLETED_PHASES",
    "WORKER",
    "REVIEWER",
    "ITERATIONS_BY_PHASE",
)
ORCHESTRATION_SECTIONS = (
    "## Summary",
    "## Changed Files / Artifacts",
    "## Unit Tests / Validation",
    "## Orca Orchestration State",
    "## Final Adversarial Review",
    "## Non-Blocking Recommendations",
)
LOOP_SECTIONS = (
    "## Summary",
    "## Changed Files / Artifacts",
    "## Unit Tests",
    "## Validation",
    "## Final Review",
    "## Non-Blocking Recommendations",
)
FINAL_REVIEW_KEYS = (
    "FINAL_REVIEW",
    "FINAL_REVIEW_TASKS",
    "FINAL_FINDINGS",
    "FINAL_REVIEW_REVALIDATIONS",
)

# The two OS-4 lines. Conditional: present only for a selected profile.
AGENT_PROFILE_KEY = "AGENT_PROFILE"
AGENT_ROUTING_KEY = "AGENT_ROUTING"


def _agent_for(result: Any, role: str) -> str:
    """What the WORKER:/REVIEWER: line says.

    A single command for a run with no profile, exactly as before OS-4. Under a
    profile the command can differ per phase, so the line points at the routing block
    rather than claiming one of them is the answer.
    """
    if getattr(result, "agent_profile_report", ()):
        return "(per phase - see AGENT_ROUTING)"
    commands = [
        event.agent_command
        for event in result.sessions
        if event.role == role and event.agent_command
    ]
    return commands[0] if commands else ""


def _header(result: Any, *, skill_name: str) -> list[str]:
    completed = ",".join(
        phase for phase in result.phases if result.phase_iterations.get(phase)
    )
    iterations = ",".join(
        f"{phase}={result.phase_iterations[phase]}"
        for phase in result.phases
        if phase in result.phase_iterations
    )
    lines = [f"STATUS: {result.final_status}"]
    if skill_name == LOOP_SKILL:
        # The loop template puts a blank line after STATUS and carries no risk axis.
        lines.append("")
        lines.append(f"PHASES: {','.join(result.phases)}")
    else:
        lines.append(f"PHASES: {','.join(result.phases)}")
        lines.append(f"RISK: {result.risk or ''}")
        lines.append(f"RISK_SOURCE: {result.risk_source or ''}")
    lines.append(f"COMPLETED_PHASES: {completed}")
    lines.append(f"WORKER: {_agent_for(result, 'worker')}")
    lines.append(f"REVIEWER: {_agent_for(result, 'reviewer')}")
    lines.append(f"ITERATIONS_BY_PHASE: {iterations}")
    if skill_name == ORCHESTRATION_SKILL:
        lines.append(f"FINAL_REVIEW_ITERATIONS: {result.final_review_iterations}")
    return lines


def _artifacts(result: Any) -> list[str]:
    return [f"- {path}" for path in result.final_review_artifacts]


def render_final_report(result: Any, *, skill_name: str) -> str:
    """The Final Report for one finished workflow, in that skill's own template."""
    if skill_name not in (ORCHESTRATION_SKILL, LOOP_SKILL):
        raise ValueError(f"unknown skill {skill_name!r}")

    lines = ["# Final Result", ""]
    lines.extend(_header(result, skill_name=skill_name))
    # OS-4's conditional pair, immediately after the header block.
    lines.extend(getattr(result, "agent_profile_report", ()))

    if skill_name == ORCHESTRATION_SKILL:
        lines.extend(
            [
                "",
                "## Summary",
                f"{len(result.phases)} requested phase(s) reached their phase gate.",
                "",
                "## Changed Files / Artifacts",
                *_artifacts(result),
                "",
                "## Unit Tests / Validation",
                # Prose, not a KEY: line -- the template declares no key in this
                # section, and inventing one would be this renderer adding a field
                # the skill never promised.
                "reviewer gates skipped: "
                + (", ".join(result.reviewer_gates_skipped) or "none"),
                "",
                "## Orca Orchestration State",
                *(
                    f"- {event.role} {event.phase} iteration={event.iteration} "
                    f"session={event.session_id} created={event.created} "
                    f"agent={event.agent_command}"
                    for event in result.sessions
                ),
                "",
                "## Final Adversarial Review",
                f"FINAL_REVIEW: {result.final_review_verdict or ''}",
                "FINAL_REVIEW_TASKS: "
                + (
                    ",".join(
                        f"attempt {index + 1}"
                        for index in range(result.final_review_iterations)
                    )
                    or "none"
                ),
                "FINAL_FINDINGS: "
                + (
                    ",".join(
                        f"{finding_id}->{phase}"
                        for _, finding_id, phase, _ in result.corrected_findings
                    )
                    or "none"
                ),
                "FINAL_REVIEW_REVALIDATIONS: "
                + (
                    ",".join(
                        f"{phase}:{iteration}"
                        for phase, iteration in result.revalidation_dispatches
                    )
                    or "none"
                ),
                "",
                "## Non-Blocking Recommendations",
                f"reason: {result.reason or 'none'}",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Summary",
                f"{len(result.phases)} requested phase(s) reached their phase gate.",
                "",
                "## Changed Files / Artifacts",
                *_artifacts(result),
                "",
                "## Unit Tests",
                *(
                    f"- {event.role} {event.phase} iteration={event.iteration} "
                    f"agent={event.agent_command}"
                    for event in result.sessions
                ),
                "",
                "## Validation",
                "correction dispatches: "
                + (
                    ", ".join(
                        f"{phase}:{iteration}"
                        for phase, iteration in result.correction_dispatches
                    )
                    or "none"
                ),
                "",
                "## Final Review",
                "",
                f"RESULT: {result.final_review_verdict or ''}",
                "",
                "## Non-Blocking Recommendations",
                f"reason: {result.reason or 'none'}",
                "",
            ]
        )
    return "\n".join(lines)
