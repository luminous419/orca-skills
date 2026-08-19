#!/usr/bin/env python3
"""Deterministic fake Reviewer process used only by the E2E harness tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--pass-value", required=True)
    parser.add_argument("--fail-value", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--findings-json", default="[]")
    parser.add_argument("--artifact")
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

    if mode == "pass":
        print(f"# Review Result\n\n{args.field}: {args.pass_value}")
        print(f"ITERATION: {args.iteration}")
        return 0
    if mode != "fail":
        return 2

    findings = json.loads(args.findings_json)
    print(f"# Review Result\n\n{args.field}: {args.fail_value}")
    print(f"ITERATION: {args.iteration}")
    print("\n## Blocking Findings")
    for finding_id in findings:
        print(f"ID: {finding_id}")
        print("Severity: MAJOR")
        print("Issue: deterministic finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
