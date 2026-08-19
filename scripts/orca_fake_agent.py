#!/usr/bin/env python3
"""Thin fake-agent CLI that consumes an Orca injected Dispatch preamble."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts.workflow_contract import load_workflow_output_contract
except ModuleNotFoundError:
    from workflow_contract import load_workflow_output_contract


SCRIPT_DIR = Path(__file__).resolve().parent
TASK_ID = re.compile(
    r"(?:taskId|task_id|--task-id)[`'\" :=]+(?P<id>task_[a-z0-9]+)", re.I
)
DISPATCH_ID = re.compile(
    r"(?:dispatchId|dispatch_id|--dispatch-id)[`'\" :=]+(?P<id>ctx_[a-z0-9]+)",
    re.I,
)
CAPABILITY = re.compile(r"--dispatch-capability\s+(?P<value>dcap_[A-Za-z0-9_-]+)")


def extract_lifecycle(prompt: str) -> tuple[str, str, str | None]:
    task = TASK_ID.search(prompt)
    dispatch = DISPATCH_ID.search(prompt)
    capability = CAPABILITY.search(prompt)
    if not task or not dispatch:
        raise ValueError("Dispatch prompt did not contain taskId and dispatchId")
    return (
        task.group("id"),
        dispatch.group("id"),
        capability.group("value") if capability else None,
    )


def fake_command(args: argparse.Namespace) -> list[str]:
    contract = load_workflow_output_contract(
        SCRIPT_DIR.parent / "orca-worker-reviewer-orchestration" / "SKILL.md"
    )
    if args.role == "worker":
        command = [
            sys.executable,
            str(SCRIPT_DIR / "fake_worker.py"),
            "--mode",
            args.mode,
            "--field",
            contract.worker_field,
            "--complete-value",
            contract.worker_complete,
            "--blocked-value",
            contract.worker_blocked,
            "--iteration",
            str(args.iteration),
            "--resolutions-json",
            args.resolutions_json,
        ]
    else:
        command = [
            sys.executable,
            str(SCRIPT_DIR / "fake_reviewer.py"),
            "--mode",
            args.mode,
            "--field",
            contract.reviewer_field,
            "--pass-value",
            contract.reviewer_pass,
            "--fail-value",
            contract.reviewer_fail,
            "--iteration",
            str(args.iteration),
            "--findings-json",
            args.findings_json,
        ]
    return command


def send_done(
    task_id: str,
    dispatch_id: str,
    capability: str | None,
    outcome: str,
    body: str,
    orca_command: str,
) -> None:
    command = [
        orca_command,
        "orchestration",
        "send",
        "--type",
        "worker_done",
        "--subject",
        f"fake agent {outcome}",
        "--body",
        body,
        "--task-id",
        task_id,
        "--dispatch-id",
        dispatch_id,
        "--outcome",
        outcome,
        "--json",
    ]
    if capability:
        command[3:3] = ["--dispatch-capability", capability]
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, end="", flush=True)
        raise SystemExit(result.returncode)


def confirm_dispatch_ready(dispatch_id: str, orca_command: str) -> None:
    result = subprocess.run(
        [
            orca_command,
            "orchestration",
            "worker-show",
            "--dispatch",
            dispatch_id,
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout, end="", flush=True)
        print(result.stderr, file=sys.stderr, end="", flush=True)
        raise SystemExit(result.returncode)
    payload = json.loads(result.stdout)
    status = payload["result"]["dispatch"]["status"]
    if status != "dispatched":
        raise SystemExit(f"dispatch is not active: {status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("worker", "reviewer"), required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--max-dispatches", type=int, default=1)
    parser.add_argument("--ask-before", action="store_true")
    parser.add_argument("--iteration", type=int, default=1)
    parser.add_argument("--findings-json", default="[]")
    parser.add_argument("--resolutions-json", default="{}")
    parser.add_argument("--orca-command", required=True)
    args = parser.parse_args()

    modes = args.mode.split(",")
    if len(modes) not in (1, args.max_dispatches):
        parser.error("--mode must contain one value or one per Dispatch")
    prompt = ""
    task_block_seen = False
    completed_dispatches = 0
    for chunk in sys.stdin:
        prompt += chunk
        print(chunk, end="", flush=True)
        if "=== TASK ===" in chunk:
            task_block_seen = True
            continue
        if not task_block_seen or not chunk.strip():
            continue
        try:
            task_id, dispatch_id, capability = extract_lifecycle(prompt)
        except ValueError:
            continue

        if capability:
            confirm_dispatch_ready(dispatch_id, args.orca_command)
        args.mode = modes[min(completed_dispatches, len(modes) - 1)]
        args.iteration = completed_dispatches + 1
        if args.ask_before and completed_dispatches == 0:
            ask_command = [
                args.orca_command,
                "orchestration",
                "ask",
                "--question",
                "May the deterministic fake continue?",
                "--options",
                "yes,no",
                "--timeout-ms",
                "30000",
                "--json",
            ]
            if capability:
                ask_command[3:3] = ["--dispatch-capability", capability]
            question = subprocess.run(
                ask_command,
                text=True,
                capture_output=True,
                check=False,
            )
            print(question.stdout, end="", flush=True)
            if question.returncode != 0:
                return question.returncode
        fake = subprocess.run(
            fake_command(args), text=True, capture_output=True, check=False
        )
        print(fake.stdout, end="", flush=True)
        print(fake.stderr, file=sys.stderr, end="", flush=True)
        if fake.returncode != 0:
            return fake.returncode

        outcome = (
            "failed"
            if args.role == "worker" and args.mode == "blocked"
            else "succeeded"
        )
        if outcome == "failed":
            escalation_command = [
                args.orca_command,
                "orchestration",
                "send",
                "--type",
                "escalation",
                "--subject",
                "Blocked: deterministic fake",
                "--body",
                "The fake Worker cannot complete this task.",
                "--task-id",
                task_id,
                "--json",
            ]
            if capability:
                escalation_command[3:3] = ["--dispatch-capability", capability]
            escalation = subprocess.run(
                escalation_command,
                text=True,
                capture_output=True,
                check=False,
            )
            print(escalation.stdout, end="", flush=True)
            if escalation.returncode != 0:
                return escalation.returncode
        send_done(
            task_id,
            dispatch_id,
            capability,
            outcome,
            fake.stdout,
            args.orca_command,
        )
        completed_dispatches += 1
        if completed_dispatches >= args.max_dispatches:
            return 0
        prompt = ""
        task_block_seen = False
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
