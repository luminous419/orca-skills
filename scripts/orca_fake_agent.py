#!/usr/bin/env python3
"""Thin fake-agent CLI that consumes an Orca injected Dispatch preamble."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    from scripts.task_context import TASK_SPEC_END_MARKER, render_boundary_receipt
    from scripts.workflow_contract import load_workflow_output_contract
except ModuleNotFoundError:
    from task_context import TASK_SPEC_END_MARKER, render_boundary_receipt
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


# OS-41. The only Dispatch status that means "this Dispatch is live and this agent
# may report against it". Unchanged from the value this file has always required --
# what changed is WHEN a 1.4.196 runtime reaches it.
ACTIVE_DISPATCH_STATUS = "dispatched"
# Statuses that mean "the runtime is still starting this Dispatch; look again".
# Orca 1.4.184 promoted the Dispatch to `dispatched` before the injected prompt
# reached the agent, so one look was always enough. Orca 1.4.196 delivers the prompt
# during `worker-start`'s own start composition, while the Dispatch row is still
# `pending` (worker.state "starting", worker.stage "authority_attached"), and
# promotes it only when that composition finishes. A deterministic fake is faster
# than the composition, so it used to observe `pending`, treat it as fatal and exit
# -- which killed the agent process mid-start and made `worker-start` itself fail
# with `dispatch_inactive`. Waiting is therefore the fix, and acting on `pending` is
# NOT: a `worker_done` sent against a still-pending Dispatch is rejected by the
# runtime as an inactive dispatch, so the agent must reach `dispatched` first.
STARTING_DISPATCH_STATUSES = frozenset({"pending"})
# Bounded, so a Dispatch that never becomes active fails closed instead of hanging.
# Comfortably above the ~3s promotion observed on 1.4.196 and below the 60s
# `worker-start` timeoutMs the same runtime reports in `worker.start_options`.
DISPATCH_READY_TIMEOUT_S = 45.0
DISPATCH_READY_POLL_S = 0.5


def confirm_dispatch_ready(dispatch_id: str, orca_command: str) -> None:
    """Block until this Dispatch is actually active, or fail closed.

    Returns only for ACTIVE_DISPATCH_STATUS. A status the runtime is still working
    through (STARTING_DISPATCH_STATUSES) is re-read until the deadline; every other
    value -- settled, abandoned, or simply unrecognized -- exits immediately, which
    is the same fail-closed answer this function has always given.
    """
    deadline = time.monotonic() + DISPATCH_READY_TIMEOUT_S
    while True:
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
        if status == ACTIVE_DISPATCH_STATUS:
            return
        if status not in STARTING_DISPATCH_STATUSES:
            raise SystemExit(f"dispatch is not active: {status}")
        if time.monotonic() >= deadline:
            raise SystemExit(
                f"dispatch stayed {status} for {DISPATCH_READY_TIMEOUT_S:.0f}s "
                "without becoming active"
            )
        time.sleep(DISPATCH_READY_POLL_S)


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
        if TASK_SPEC_END_MARKER not in prompt:
            # The Task spec is multi-line and is still being injected. Acting on its
            # first line would run worker-show while the runtime is still starting
            # the dispatch -- which fails it outright -- and would echo a receipt for
            # a boundary only half of which had arrived.
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
            fake.stdout + render_boundary_receipt(prompt),
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
