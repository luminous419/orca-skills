#!/usr/bin/env python3
"""Deterministic fake Worker process used only by the E2E harness tests."""

from __future__ import annotations

import argparse
import json


try:
    from scripts.task_context import render_boundary_receipt
except ModuleNotFoundError:  # run directly as scripts/fake_*.py
    from task_context import render_boundary_receipt


# ---- OS-29: the decision-gate declaration both fake agents emit --------------------
# Deliberately INVERTS the OS-3 opt-in precedent recorded below for
# --unit-test-status. That one was right for an EVIDENCE field whose absence is a
# legitimate state. It is wrong here: under a fail-closed gate a default of silence
# would either break every existing scenario or force an arming flag -- and an arming
# flag IS the fail-open default the ticket forbids. So the agents ASSERT `CLEAR`,
# which is an agent declaring a state (what the contract requires of it), never the
# engine presuming one.
DECISION_GATE_STATE_FIELD = "DECISION_GATE_STATE"
DECISION_GATE_STATES = ("CLEAR", "ASSUMPTION_ALLOWED", "NEEDS_INPUT", "CONFLICT")

# One record per state, each the minimum the shipped OS-28 contract accepts for that
# state. They are the DECISION half only: run/phase/iteration/boundary/sequence and
# the rest of the ledger mechanics are stamped by the harness, which is what makes
# the ledger bind to the round that actually settled rather than to what an agent
# claimed about itself.
_DECISION_GATE_RECORDS = {
    "CLEAR": {
        "state": "CLEAR",
        "reason_code": None,
        "open_decision_item": False,
        "grounds": (
            "No boundary element declared by this phase is triggering and no two "
            "explicit requirements contradict, so no decision item is open at this "
            "boundary."
        ),
        "scope": "This phase's own conduct at this iteration.",
    },
    "ASSUMPTION_ALLOWED": {
        "state": "ASSUMPTION_ALLOWED",
        "reason_code": "repository_policy",
        "open_decision_item": False,
        "policy_source": {
            "role": "supports",
            "kind": "file_path",
            "locator": "CLAUDE.md",
        },
        "reversibility": "reversible_in_run",
        "impact": "one module",
        "retraction_condition": "if the phase Reviewer rejects the assumption",
        "blast_radius": "module",
        "monetary_cost": False,
        "security": False,
        "privacy": False,
        "compliance": False,
        "long_term_lock_in": False,
        "assumption": "the module-local default applies",
        "grounds": (
            "A supporting policy source exists and all six safety facts are declared "
            "false, so the assumption is retractable within this run."
        ),
        "scope": "This phase's own conduct at this iteration.",
    },
    "NEEDS_INPUT": {
        "state": "NEEDS_INPUT",
        "reason_code": "blast_radius_beyond_scope",
        "boundary_element": "blast_radius",
        "blast_radius": "external_system",
        "what_is_missing": (
            "whether the user authorizes touching a system outside the declared scope"
        ),
        "why_policy_cannot_decide": (
            "no repository policy or phase contract fixes the scope boundary"
        ),
        "open_decision_item": True,
        "open_item": "external-system scope authorization",
    },
    "CONFLICT": {
        "state": "CONFLICT",
        "reason_code": "requirement_contradiction",
        "citations": [
            "ORIGINAL_REQUEST.md#fail-closed",
            "ORIGINAL_REQUEST.md#out-of-scope",
        ],
        "why_they_cannot_both_hold": (
            "one requirement demands the record, the other forbids the field it needs"
        ),
        "open_decision_item": True,
        "open_item": "which requirement governs",
    },
}


def add_decision_gate_arguments(parser: argparse.ArgumentParser) -> None:
    """The gate flags both fake agents share. Armed by default, never opt-in."""
    parser.add_argument(
        "--decision-gate-state",
        default="CLEAR",
        choices=("", *DECISION_GATE_STATES),
    )
    # The malformed-output seams, deliberately UNCONSTRAINED and repeatable -- the
    # same role --unit-test-status-raw plays for OS-3, so a fail-closed branch can be
    # driven through the REAL subprocess rather than by calling the parser directly.
    parser.add_argument("--decision-gate-state-line-raw", action="append", default=None)
    parser.add_argument("--decision-gate-record-raw", default=None)
    parser.add_argument("--decision-gate-omit-field", action="store_true")
    parser.add_argument("--decision-gate-omit-block", action="store_true")


def render_decision_gate(args: argparse.Namespace, extra: dict | None = None) -> str:
    """The agent's own gate declaration: one field line and one fenced record.

    The record is the AUTHORITY and the field line is its projection. They are
    emitted from the same source here, so a scenario that wants them to disagree has
    to say so explicitly through --decision-gate-record-raw -- which is exactly the
    drift case the gate must refuse.
    """
    lines: list[str] = []
    state = args.decision_gate_state
    if state and not args.decision_gate_omit_field:
        lines.append(f"{DECISION_GATE_STATE_FIELD}: {state}")
    for raw in args.decision_gate_state_line_raw or ():
        lines.append(f"{DECISION_GATE_STATE_FIELD}: {raw}")
    if args.decision_gate_record_raw is not None:
        lines.extend(["```decision-gate", args.decision_gate_record_raw, "```"])
    elif state and not args.decision_gate_omit_block:
        record = dict(_DECISION_GATE_RECORDS[state])
        if extra:
            record.update(extra)
        lines.extend(
            ["```decision-gate", json.dumps(record, indent=2, sort_keys=True), "```"]
        )
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--complete-value", required=True)
    parser.add_argument("--blocked-value", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--resolutions-json", default="{}")
    # The dispatched Task spec, verbatim. This fake has no Orca preamble to read, so
    # the spec is handed to it directly -- but it is still the agent's INPUT, and the
    # receipt below is still parsed out of it rather than reconstructed.
    parser.add_argument("--task-spec", default="")
    # OS-3 section 14 evidence. Opt-in: the default emits NOTHING, so every existing
    # scenario's output stays byte-identical. UNIT_TEST_STATUS matches the harness's
    # FIELD_LINE regex but not its `STATUS` field filter, so the existing status
    # parse is untouched.
    parser.add_argument(
        "--unit-test-status", default="", choices=("", "PASS", "BLOCKED")
    )
    # The malformed-output seam. --unit-test-status above is the well-formed,
    # scenario-facing knob and stays constrained on purpose; this one is deliberately
    # UNCONSTRAINED and repeatable, so a test can drive the parser's duplicate-line
    # and unknown-value branches through the real subprocess rather than by calling
    # parse_unit_test_status() in isolation. Same role --mode malformed already plays
    # for the STATUS field.
    parser.add_argument("--unit-test-status-raw", action="append", default=None)
    add_decision_gate_arguments(parser)
    args = parser.parse_args()

    if args.mode == "exit":
        return 17
    if args.mode == "malformed":
        # No gate declaration: this output never reaches B2, because the STATUS parse
        # rejects it first and MALFORMED_WORKER_OUTPUT is a result-contract error,
        # not a decision one. Emitting one here would claim a boundary this result
        # never reaches.
        print("# Worker Result\n\n## Summary\nMissing status field")
        return 0
    if args.mode == "blocked":
        # A Worker-declared BLOCKED still crosses B2, so it declares like any other
        # settled result. O-2 then decides which axis owns the terminal: a blocking
        # decision is accounted on the decision axis, and only a result with no
        # decision block keeps the generic WORKER_BLOCKED reason.
        print(f"# Worker Result\n\n{args.field}: {args.blocked_value}")
        print(render_decision_gate(args), end="")
        return 0
    if args.mode not in {"complete", "correction"}:
        return 2

    resolutions = json.loads(args.resolutions_json)
    print(f"# Worker Result\n\n{args.field}: {args.complete_value}")
    if args.unit_test_status:
        print(f"UNIT_TEST_STATUS: {args.unit_test_status}")
    for raw in args.unit_test_status_raw or ():
        print(f"UNIT_TEST_STATUS: {raw}")
    print(f"ITERATION: {args.iteration}")
    print(render_decision_gate(args), end="")
    if resolutions:
        print("\n## Review Feedback Resolution")
        for finding_id, status in sorted(resolutions.items()):
            print(f"FINDING {finding_id}: {status}")
    print(render_boundary_receipt(args.task_spec), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
