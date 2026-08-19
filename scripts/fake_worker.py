#!/usr/bin/env python3
"""Deterministic fake Worker process used only by the E2E harness tests."""

from __future__ import annotations

import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--field", required=True)
    parser.add_argument("--complete-value", required=True)
    parser.add_argument("--blocked-value", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--resolutions-json", default="{}")
    args = parser.parse_args()

    if args.mode == "exit":
        return 17
    if args.mode == "malformed":
        print("# Worker Result\n\n## Summary\nMissing status field")
        return 0
    if args.mode == "blocked":
        print(f"# Worker Result\n\n{args.field}: {args.blocked_value}")
        return 0
    if args.mode not in {"complete", "correction"}:
        return 2

    resolutions = json.loads(args.resolutions_json)
    print(f"# Worker Result\n\n{args.field}: {args.complete_value}")
    print(f"ITERATION: {args.iteration}")
    if resolutions:
        print("\n## Review Feedback Resolution")
        for finding_id, status in sorted(resolutions.items()):
            print(f"FINDING {finding_id}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
