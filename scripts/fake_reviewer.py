#!/usr/bin/env python3
"""Deterministic fake Reviewer process used only by the E2E harness tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


try:
    from scripts.task_context import render_boundary_receipt
except ModuleNotFoundError:  # run directly as scripts/fake_*.py
    from task_context import render_boundary_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--pass-value", required=True)
    parser.add_argument("--fail-value", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--findings-json", default="[]")
    parser.add_argument("--responsible-phases-json", default="{}")
    parser.add_argument("--artifact")
    # Same contract as fake_worker: the dispatched Task spec is this agent's input,
    # and the receipt is read back out of it.
    parser.add_argument("--task-spec", default="")
    args = parser.parse_args()

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
    if mode in {"pass", "pass-nonblocking", "pass-blocking"}:
        print(f"# Review Result\n\n{args.field}: {args.pass_value}")
        print(f"ITERATION: {args.iteration}")
        if mode == "pass-blocking":
            print("\n## Blocking Findings")
        elif mode == "pass-nonblocking":
            print("\n## Blocking Findings\n(none)")
            print("\n## Non-Blocking Findings")
        for finding_id in findings:
            print(f"ID: {finding_id}")
            print("Severity: MINOR")
            phase = responsible_phases.get(finding_id)
            if phase:
                print(f"Responsible Phase: {phase}")
            print("Issue: deterministic finding")
        print(render_boundary_receipt(args.task_spec), end="")
        return 0
    if mode != "fail":
        return 2

    print(f"# Review Result\n\n{args.field}: {args.fail_value}")
    print(f"ITERATION: {args.iteration}")
    print("\n## Blocking Findings")
    for finding_id in findings:
        print(f"ID: {finding_id}")
        print("Severity: MAJOR")
        phase = responsible_phases.get(finding_id)
        if phase:
            print(f"Responsible Phase: {phase}")
        print("Issue: deterministic finding")
    print(render_boundary_receipt(args.task_spec), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
