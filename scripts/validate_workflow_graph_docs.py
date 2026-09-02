#!/usr/bin/env python3
"""Validate the Skill's OS-40 contract against the stdlib graph specification."""
from __future__ import annotations
import json
import re
from pathlib import Path

try:
    from scripts.deterministic_workflow.contracts import PHASES, ROUTE_TOKENS, SCHEMA_VERSION, TERMINAL_STATUSES, WORKFLOW_ID
except ModuleNotFoundError:  # direct ``python3 scripts/...`` execution
    from deterministic_workflow.contracts import PHASES, ROUTE_TOKENS, SCHEMA_VERSION, TERMINAL_STATUSES, WORKFLOW_ID

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/"orca-worker-reviewer-orchestration"/"SKILL.md"
PATTERN=re.compile(r"```workflow-graph-contract\n(?P<body>.*?)\n```",re.S)


def validate(path: Path = SKILL) -> None:
    matches=PATTERN.findall(path.read_text(encoding="utf-8"))
    if len(matches)!=1: raise ValueError("expected exactly one workflow-graph-contract")
    actual=json.loads(matches[0])
    expected={"workflow_id":WORKFLOW_ID,"schema_version":SCHEMA_VERSION,"phases":list(PHASES),
              "route_tokens":list(ROUTE_TOKENS),"terminal_statuses":list(TERMINAL_STATUSES),
              "iteration_domains":["PHASE_ITERATIONS","FINAL_REVIEW_ITERATIONS"],
              "decision_first":True,"final_review_mandatory":True,"downstream_revalidation":"high_only",
              "launcher":"tools/run_workflow.py"}
    if actual!=expected: raise ValueError(f"workflow graph contract mismatch: {actual!r}")


def main() -> int:
    try: validate()
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(f"Workflow graph documentation validation FAILED: {exc}"); return 1
    print("Workflow graph documentation validation PASSED"); return 0


if __name__=="__main__": raise SystemExit(main())
