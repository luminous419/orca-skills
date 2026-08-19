#!/usr/bin/env python3
"""Small real-Orca integration harness using deterministic fake agents."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_CODEX = REPO_ROOT / "scripts" / "fake_bin" / "codex"
WAIT_TYPES = "worker_done,escalation,question"
SUPPORTED_ORCA_APP_VERSION = "1.4.184"
REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS = (
    "orca orchestration run-create --objective <text> --json",
    "orca orchestration task-create --spec <text>",
    "orca orchestration dispatch --task <task_id> --to <handle>",
    "orca orchestration worker-start --task <task_id>",
    "orca orchestration check --wait --types worker_done,escalation,question",
    "orca orchestration worker-release --dispatch <dispatch_id> --json",
    "orca orchestration worker-retain --dispatch <dispatch_id> --json",
    "--type worker_done --subject \"<status>\"",
    "--task-id <task_id> --dispatch-id <dispatch_id> --outcome succeeded",
)
REQUIRED_ORCA_CLI_GUIDE_SNIPPETS = (
    "orca terminal create",
    "orca terminal send",
    "ORCA terminal wait",
)


class OrcaRuntimeError(RuntimeError):
    pass


class UnsupportedOrcaContract(OrcaRuntimeError):
    pass


def validate_orca_contract(
    app_version: str, orchestration_guide: str, cli_guide: str
) -> None:
    if app_version != SUPPORTED_ORCA_APP_VERSION:
        raise UnsupportedOrcaContract(
            f"runtime harness supports Orca {SUPPORTED_ORCA_APP_VERSION}; "
            f"installed runtime is {app_version}"
        )
    missing = [
        snippet
        for snippet in REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS
        if snippet not in orchestration_guide
    ]
    missing.extend(
        snippet
        for snippet in REQUIRED_ORCA_CLI_GUIDE_SNIPPETS
        if snippet not in cli_guide
    )
    if missing:
        raise UnsupportedOrcaContract(
            "installed version-matched guide does not match the pinned grammar: "
            + ", ".join(missing)
        )


@dataclass
class RuntimeAttempt:
    role: str
    iteration: int
    task_id: str
    dispatch_id: str
    outcome: str
    task_status: str
    dispatch_status: str
    worker_state: str
    terminal_state: str
    lifecycle_action: str
    worker_done_count: int
    execution_path: str
    body: str = ""


@dataclass
class RuntimeScenarioResult:
    scenario: str
    run_id: str
    status: str
    iteration: int
    attempts: list[RuntimeAttempt] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    recovery: list[str] = field(default_factory=list)


class OrcaRuntimeHarness:
    def __init__(self, artifact_dir: Path, *, wait_timeout_ms: int = 10000) -> None:
        self.orca = self._resolve_orca()
        self.artifact_dir = artifact_dir
        self.wait_timeout_ms = wait_timeout_ms
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.coordinator: str | None = None
        self.run_id: str | None = None
        self._raw: list[dict[str, Any]] = []
        self._signals: list[str] = []

    @staticmethod
    def _resolve_orca() -> str:
        configured = os.environ.get("ORCA_CLI_COMMAND")
        executable = configured or shutil.which("orca")
        if not executable:
            raise OrcaRuntimeError("Orca CLI executable was not found")
        return executable

    def call(self, *args: str, allow_error: bool = False) -> dict[str, Any]:
        completed = subprocess.run(
            [self.orca, *args, "--json"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise OrcaRuntimeError(
                f"non-JSON Orca response for {' '.join(args)}: {completed.stdout!r}"
            ) from exc
        self._raw.append({"command": list(args), "response": payload})
        if (completed.returncode != 0 or not payload.get("ok")) and not allow_error:
            raise OrcaRuntimeError(
                f"Orca command failed ({' '.join(args)}): {payload.get('error')}"
            )
        return payload

    def preflight(self) -> dict[str, Any]:
        status = self.call("status")["result"]
        if status["runtime"]["state"] != "ready":
            raise OrcaRuntimeError("Orca runtime is not ready")
        orchestration = subprocess.run(
            [self.orca, "skills", "get", "orchestration"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        cli = subprocess.run(
            [self.orca, "skills", "get", "orca-cli"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        validate_orca_contract(status["runtime"]["appVersion"], orchestration, cli)
        current = self.call("worktree", "current")
        return {
            "executable": self.orca,
            "appVersion": status["runtime"]["appVersion"],
            "runtimeId": status["runtime"]["runtimeId"],
            "worktreeId": current["result"]["worktree"]["id"],
            "guides": {
                "orchestration": "orca skills get orchestration",
                "orca-cli": "orca skills get orca-cli",
                "orcaCliGuideLoaded": "terminal create" in cli,
            },
        }

    def start_run(self, objective: str) -> str:
        terminal = self.call(
            "terminal", "create", "--worktree", "current", "--title", objective, "--command", "bash"
        )
        self.coordinator = terminal["result"]["terminal"]["handle"]
        created = self.call(
            "orchestration", "run-create", "--objective", objective, "--from", self.coordinator
        )
        self.run_id = created["result"]["run"]["id"]
        self._signals = []
        return self.run_id

    def create_task(self, spec: str) -> str:
        assert self.coordinator
        created = self.call(
            "orchestration", "task-create", "--spec", spec, "--from", self.coordinator
        )
        return created["result"]["task"]["id"]

    def create_fake_terminal(
        self,
        role: str,
        mode: str,
        *,
        iteration: int,
        findings: tuple[str, ...] = (),
        resolutions: dict[str, str] | None = None,
        max_dispatches: int = 1,
        ask_before: bool = False,
    ) -> str:
        command = [
            "exec",
            str(FAKE_CODEX),
            "--role",
            role,
            "--mode",
            mode,
            "--iteration",
            str(iteration),
            "--findings-json",
            json.dumps(findings),
            "--resolutions-json",
            json.dumps(resolutions or {}, sort_keys=True),
            "--max-dispatches",
            str(max_dispatches),
            "--orca-command",
            self.orca,
        ]
        if ask_before:
            command.append("--ask-before")
        created = self.call(
            "terminal",
            "create",
            "--worktree",
            "current",
            "--title",
            f"fake-{role}-{iteration}",
            "--command",
            shlex.join(command),
        )
        return created["result"]["terminal"]["handle"]

    def start_worker(self, task_id: str, terminal: str, spec: str) -> tuple[str, bool]:
        assert self.coordinator
        started = self.call(
            "orchestration",
            "worker-start",
            "--task",
            task_id,
            "--terminal",
            terminal,
            "--from",
            self.coordinator,
            allow_error=True,
        )
        if started.get("ok"):
            return started["result"]["dispatchId"], True
        error = started.get("error", {})
        if error.get("code") != "agent_unconfigured":
            raise OrcaRuntimeError(f"worker-start failed: {error}")
        dispatched = self.call(
            "orchestration",
            "dispatch",
            "--task",
            task_id,
            "--to",
            terminal,
            "--from",
            self.coordinator,
        )
        dispatch_id = dispatched["result"]["dispatch"]["id"]
        prompt = (
            f"taskId: {task_id}\n"
            f"dispatchId: {dispatch_id}\n"
            "Use worker_done exactly once with an explicit outcome.\n"
            "=== TASK ===\n"
            f"{spec}"
        )
        self.call(
            "terminal", "send", "--terminal", terminal, "--text", prompt, "--enter"
        )
        return dispatch_id, False

    def _check(self) -> dict[str, Any]:
        assert self.coordinator
        return self.call(
            "orchestration",
            "check",
            "--terminal",
            self.coordinator,
            "--wait",
            "--types",
            WAIT_TYPES,
            "--timeout-ms",
            str(self.wait_timeout_ms),
        )["result"]

    def _ack(self, delivery_id: str) -> None:
        assert self.coordinator
        self.call(
            "orchestration", "check", "--terminal", self.coordinator, "--ack", delivery_id
        )

    def confirm_terminal_exit(self, terminal: str) -> str:
        waited = self.call(
            "terminal",
            "wait",
            "--terminal",
            terminal,
            "--for",
            "exit",
            "--timeout-ms",
            str(self.wait_timeout_ms),
            allow_error=True,
        )
        if not waited.get("ok"):
            message = (waited.get("error") or {}).get("message")
            if message == "tab_not_found":
                return "exited"
            raise OrcaRuntimeError(f"terminal exit observation failed: {waited.get('error')}")
        if not waited["result"]["wait"]["satisfied"]:
            raise OrcaRuntimeError("fake terminal did not exit after settlement")
        return "exited"

    def wait_for_done(self, dispatch_id: str) -> tuple[dict[str, Any], str]:
        while True:
            delivery = self._check()
            if delivery.get("timedOut") or not delivery.get("messages"):
                raise OrcaRuntimeError(f"timed out waiting for Dispatch {dispatch_id}")
            done = None
            for message in delivery["messages"]:
                message_type = message["type"]
                self._signals.append(message_type)
                if message_type == "question":
                    self.call(
                        "orchestration",
                        "reply",
                        "--id",
                        message["id"],
                        "--body",
                        "yes",
                        "--from",
                        self.coordinator,
                    )
                elif message_type == "escalation":
                    pass
                elif message_type == "worker_done":
                    payload = json.loads(message["payload"])
                    if payload.get("dispatchId") == dispatch_id:
                        if done is not None:
                            raise OrcaRuntimeError("worker_done was delivered more than once")
                        if payload.get("_orcaLifecycleRejection"):
                            raise OrcaRuntimeError("worker_done was rejected by Orca")
                        done = message
            if done is not None:
                return done, delivery["deliveryId"]
            self._ack(delivery["deliveryId"])

    def settle_attempt(
        self,
        role: str,
        iteration: int,
        task_id: str,
        dispatch_id: str,
        done: dict[str, Any],
        delivery_id: str,
        *,
        lifecycle: str = "release",
        supervised: bool = True,
        terminal: str,
    ) -> RuntimeAttempt:
        if supervised:
            before = self.call("orchestration", "worker-show", "--dispatch", dispatch_id)["result"]
            if lifecycle == "retain":
                action = self.call("orchestration", "worker-retain", "--dispatch", dispatch_id)
            else:
                action = self.call("orchestration", "worker-release", "--dispatch", dispatch_id)
            dispatch_status = before["dispatch"]["status"]
            worker_state = before["worker"]["state"]
            terminal_resource = before.get("terminalResource") or {}
            terminal_state = terminal_resource.get("releaseState", "none")
            lifecycle_action = f"{lifecycle}:{action['result']['state']}"
        else:
            shown = self.call("orchestration", "dispatch-show", "--task", task_id)["result"]
            dispatch = shown.get("dispatch") or shown
            dispatch_status = dispatch["status"]
            worker_state = "settled_external"
            terminal_state = (
                "reused"
                if lifecycle == "retain"
                else self.confirm_terminal_exit(terminal)
            )
            lifecycle_action = "reuse:tracked-external" if lifecycle == "retain" else "release:natural-exit"
        self._ack(delivery_id)
        tasks = self.call("orchestration", "task-list", "--run", self.run_id)["result"]["tasks"]
        task = next(item for item in tasks if item["id"] == task_id)
        payload = json.loads(done["payload"])
        return RuntimeAttempt(
            role=role,
            iteration=iteration,
            task_id=task_id,
            dispatch_id=dispatch_id,
            outcome=payload["outcome"],
            task_status=task["status"],
            dispatch_status=dispatch_status,
            worker_state=worker_state,
            terminal_state=terminal_state,
            lifecycle_action=lifecycle_action,
            worker_done_count=1,
            execution_path="supervised" if supervised else "tracked_external",
            body=done["body"],
        )

    def run_attempt(
        self,
        role: str,
        iteration: int,
        mode: str,
        *,
        findings: tuple[str, ...] = (),
        resolutions: dict[str, str] | None = None,
        ask_before: bool = False,
        lifecycle: str = "release",
        terminal: str | None = None,
        max_dispatches: int = 1,
    ) -> tuple[RuntimeAttempt, str]:
        spec = f"{role} iteration {iteration}: {mode}"
        task_id = self.create_task(spec)
        handle = terminal or self.create_fake_terminal(
            role,
            mode,
            iteration=iteration,
            findings=findings,
            resolutions=resolutions,
            max_dispatches=max_dispatches,
            ask_before=ask_before,
        )
        dispatch_id, supervised = self.start_worker(task_id, handle, spec)
        done, delivery_id = self.wait_for_done(dispatch_id)
        return (
            self.settle_attempt(
                role,
                iteration,
                task_id,
                dispatch_id,
                done,
                delivery_id,
                lifecycle=lifecycle,
                supervised=supervised,
                terminal=handle,
            ),
            handle,
        )

    def observe_unexpected_exit(
        self, role: str, iteration: int
    ) -> RuntimeAttempt:
        task_id = self.create_task(f"{role} iteration {iteration}: unexpected exit")
        handle = self.create_fake_terminal(role, "exit", iteration=iteration)
        dispatch_id, supervised = self.start_worker(
            task_id, handle, f"{role} iteration {iteration}: unexpected exit"
        )
        assert self.coordinator
        self.confirm_terminal_exit(handle)
        checkpoint = self._check()
        if checkpoint.get("messages"):
            raise OrcaRuntimeError("unexpected exit produced a lifecycle message")
        if supervised:
            shown = self.call("orchestration", "worker-show", "--dispatch", dispatch_id)["result"]
            state = shown["worker"]["state"]
        else:
            self.call(
                "orchestration",
                "task-update",
                "--id",
                task_id,
                "--status",
                "failed",
                "--result",
                json.dumps({"reason": "process_exited_without_worker_done"}),
                "--from",
                self.coordinator,
            )
            shown_result = self.call("orchestration", "dispatch-show", "--task", task_id)["result"]
            shown = {"dispatch": shown_result.get("dispatch") or shown_result}
            state = "outcome_unknown_external"
        recovery = "task-update:failed" if not supervised else "observed"
        if supervised and state == "outcome_unknown":
            recovery_result = self.call(
                "orchestration", "worker-abandon", "--dispatch", dispatch_id
            )
            recovery = f"abandon:{recovery_result['result']['state']}"
            shown = self.call("orchestration", "worker-show", "--dispatch", dispatch_id)["result"]
        elif supervised and state not in {"failed", "stopped"}:
            raise OrcaRuntimeError(f"unexpected exit left worker in {state}")
        release_state = "natural-exit"
        if supervised:
            release = self.call("orchestration", "worker-release", "--dispatch", dispatch_id)
            release_state = release["result"]["state"]
        tasks = self.call("orchestration", "task-list", "--run", self.run_id)["result"]["tasks"]
        task = next(item for item in tasks if item["id"] == task_id)
        return RuntimeAttempt(
            role=role,
            iteration=iteration,
            task_id=task_id,
            dispatch_id=dispatch_id,
            outcome="unknown",
            task_status=task["status"],
            dispatch_status=shown["dispatch"]["status"],
            worker_state=state,
            terminal_state=(shown.get("terminalResource") or {}).get("releaseState", "natural_exit"),
            lifecycle_action=f"{recovery};release:{release_state}",
            worker_done_count=0,
            execution_path="supervised" if supervised else "tracked_external",
        )

    def finish(self, result: RuntimeScenarioResult) -> RuntimeScenarioResult:
        assert self.run_id and self.coordinator
        result.signals = list(self._signals)
        snapshot = {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "result": asdict(result),
            "run": self.call("orchestration", "run-show", "--id", self.run_id)["result"],
            "tasks": self.call("orchestration", "task-list", "--run", self.run_id)["result"],
            "commands": self._raw,
        }
        path = self.artifact_dir / f"scenario-{result.scenario.lower()}.json"
        path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.call("terminal", "close", "--terminal", self.coordinator, allow_error=True)
        self.coordinator = None
        self.run_id = None
        self._raw = []
        self._signals = []
        return result


def run_runtime_scenarios(artifact_dir: Path) -> list[RuntimeScenarioResult]:
    harness = OrcaRuntimeHarness(artifact_dir)
    preflight = harness.preflight()
    (artifact_dir / "environment.json").write_text(
        json.dumps(preflight, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    results: list[RuntimeScenarioResult] = []

    run_id = harness.start_run("Step 4 Scenario A first-pass PASS")
    worker, _ = harness.run_attempt("worker", 1, "complete", ask_before=True)
    reviewer, _ = harness.run_attempt("reviewer", 1, "pass")
    results.append(harness.finish(RuntimeScenarioResult("A", run_id, "COMPLETED", 1, [worker, reviewer])))

    run_id = harness.start_run("Step 4 Scenario B FAIL then PASS")
    worker, _ = harness.run_attempt("worker", 1, "complete")
    reviewer1, reviewer_terminal = harness.run_attempt(
        "reviewer", 1, "fail,pass", findings=("R1",), lifecycle="retain", max_dispatches=2
    )
    correction, _ = harness.run_attempt(
        "worker", 2, "correction", resolutions={"R1": "RESOLVED"}
    )
    reviewer2, _ = harness.run_attempt("reviewer", 2, "pass", terminal=reviewer_terminal)
    results.append(harness.finish(RuntimeScenarioResult("B", run_id, "COMPLETED", 2, [worker, reviewer1, correction, reviewer2])))

    run_id = harness.start_run("Step 4 Scenario C max iterations")
    attempts = []
    for iteration in range(1, 4):
        worker, _ = harness.run_attempt(
            "worker", iteration, "complete" if iteration == 1 else "correction",
            resolutions={} if iteration == 1 else {"R1": "DISPUTED"},
        )
        reviewer, _ = harness.run_attempt("reviewer", iteration, "fail", findings=("R1",))
        attempts.extend((worker, reviewer))
    results.append(harness.finish(RuntimeScenarioResult("C", run_id, "ESCALATED", 3, attempts)))

    run_id = harness.start_run("Step 4 Scenario D Worker BLOCKED")
    worker, _ = harness.run_attempt("worker", 1, "blocked")
    results.append(harness.finish(RuntimeScenarioResult("D", run_id, "BLOCKED", 1, [worker])))

    run_id = harness.start_run("Step 4 Scenario E Worker unexpected exit")
    worker = harness.observe_unexpected_exit("worker", 1)
    results.append(harness.finish(RuntimeScenarioResult("E", run_id, "ERROR", 1, [worker], recovery=[worker.lifecycle_action])))

    run_id = harness.start_run("Step 4 Scenario F Reviewer unexpected exit")
    worker, _ = harness.run_attempt("worker", 1, "complete")
    reviewer = harness.observe_unexpected_exit("reviewer", 1)
    results.append(harness.finish(RuntimeScenarioResult("F", run_id, "ERROR", 1, [worker, reviewer], recovery=[reviewer.lifecycle_action])))

    return results
