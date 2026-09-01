#!/usr/bin/env python3
"""Deterministic fake Reviewer process used only by the E2E harness tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


try:
    from scripts.task_context import render_boundary_receipt
    # OS-29: ONE definition of the gate declaration, shared by the two fake agents.
    # A second copy here would be a place for the two fakes' decision semantics to
    # drift, which is the exact failure mode this ticket adds validators against.
    from scripts.fake_worker import add_decision_gate_arguments, render_decision_gate
except ModuleNotFoundError:  # run directly as scripts/fake_*.py
    from task_context import render_boundary_receipt
    from fake_worker import add_decision_gate_arguments, render_decision_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--pass-value", required=True)
    parser.add_argument("--fail-value", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--findings-json", default="[]")
    parser.add_argument("--responsible-phases-json", default="{}")
    # OS-1 Final Review Finding Contract: severity says how much impact a finding
    # has, blocking says whether it fails the gate, and they are separate axes.
    parser.add_argument("--quality-attributes-json", default="{}")
    parser.add_argument("--blocking-json", default="{}")
    parser.add_argument("--artifact")
    # Same contract as fake_worker: the dispatched Task spec is this agent's input,
    # and the receipt is read back out of it.
    parser.add_argument("--task-spec", default="")
    add_decision_gate_arguments(parser)
    # OS-29 B3-V: the ledger key of the Worker B2 record this Reviewer is verifying.
    # Supplied by the harness ONLY in verification mode, so an ordinary round's
    # dispatched command -- and this agent's output -- stay byte-identical. The
    # -raw seam lets a scenario emit a binding that does not resolve, which is P6b
    # row 7 and must fail closed rather than fall back to the Worker's own answer.
    parser.add_argument("--decision-gate-verifies", default="")
    parser.add_argument("--decision-gate-verifies-raw", default=None)
    args = parser.parse_args()

    def gate_text() -> str:
        extra: dict | None = None
        key = (
            args.decision_gate_verifies_raw
            if args.decision_gate_verifies_raw is not None
            else args.decision_gate_verifies
        )
        if key:
            run, phase, iteration = key.split("/")[0:3]
            extra = {
                "verifies": {
                    "run": run,
                    "phase": phase,
                    "iteration": int(iteration),
                    "worker_record_key": key,
                }
            }
        return render_decision_gate(args, extra)

    if args.mode == "exit":
        return 23
    if args.mode == "malformed-missing":
        print("# Review Result\n\n## Summary\nMissing result field")
        return 0
    if args.mode == "malformed-invalid":
        print(f"# Review Result\n\n{args.field}: UNKNOWN")
        return 0
    if args.mode == "fail-modify":
        if not args.artifact:
            return 2
        Path(args.artifact).write_text("reviewer modified production artifact\n")
        mode = "fail"
    else:
        mode = args.mode

    findings = json.loads(args.findings_json)
    responsible_phases = json.loads(args.responsible_phases_json)
    quality_attributes = json.loads(args.quality_attributes_json)
    blocking_flags = json.loads(args.blocking_json)

    def emit(finding_id: str, severity: str, blocking: bool) -> None:
        """One finding in the shape reviews/common.md's Finding Contract defines."""
        print(f"ID: {finding_id}")
        # NONE is the contract's own pairing rule: a finding charged to no quality
        # attribute is never blocking, so a caller that supplied neither gets the
        # consistent pair rather than a half-specified finding.
        print(
            "Quality Attribute: "
            + quality_attributes.get(finding_id, "G1" if blocking else "NONE")
        )
        print(f"Severity: {severity}")
        print("Blocking: " + ("YES" if blocking else "NO"))
        phase = responsible_phases.get(finding_id)
        if phase:
            print(f"Responsible Phase: {phase}")
        print("Issue: deterministic finding")

    def is_blocking(finding_id: str, default: bool) -> bool:
        return bool(blocking_flags.get(finding_id, default))

    if mode in {"pass", "pass-nonblocking", "pass-blocking"}:
        print(f"# Review Result\n\n{args.field}: {args.pass_value}")
        print(f"ITERATION: {args.iteration}")
        if mode == "pass-blocking":
            print("\n## Blocking Findings")
            for finding_id in findings:
                emit(finding_id, "MINOR", True)
        elif mode == "pass-nonblocking":
            print("\n## Blocking Findings\n(none)")
            print("\n## Non-Blocking Findings")
            for finding_id in findings:
                emit(finding_id, "MINOR", False)
        print(gate_text(), end="")
        print(render_boundary_receipt(args.task_spec), end="")
        return 0
    if mode not in {"fail", "fail-mixed"}:
        return 2

    print(f"# Review Result\n\n{args.field}: {args.fail_value}")
    print(f"ITERATION: {args.iteration}")
    if mode == "fail":
        print("\n## Blocking Findings")
        for finding_id in findings:
            emit(finding_id, "MAJOR", is_blocking(finding_id, True))
        print(gate_text(), end="")
        print(render_boundary_receipt(args.task_spec), end="")
        return 0

    # fail-mixed: one report carrying both kinds, which is the only shape that can
    # show routing keying on `Blocking:` rather than on the section or the severity.
    # Every finding here is MAJOR on purpose -- severity is held constant so that any
    # difference in what gets corrected is attributable to the blocking field alone.
    blocking_ids = [
        finding_id for finding_id in findings if is_blocking(finding_id, True)
    ]
    note_ids = [
        finding_id for finding_id in findings if not is_blocking(finding_id, True)
    ]
    print("\n## Blocking Findings")
    for finding_id in blocking_ids:
        emit(finding_id, "MAJOR", True)
    print("\n## Non-Blocking Findings")
    for finding_id in note_ids:
        emit(finding_id, "MAJOR", False)
    print(gate_text(), end="")
    print(render_boundary_receipt(args.task_spec), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
