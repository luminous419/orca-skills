#!/usr/bin/env python3
"""Offline regression tests for the pinned Orca runtime contract adapter."""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from dataclasses import asdict, replace
from os import environ
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, NamedTuple
from unittest.mock import patch

from scripts import orca_runtime_harness
from scripts.orca_fake_agent import send_done
from scripts.orca_runtime_harness import (
    CLOSE_ELIGIBLE_ROLES,
    LIFECYCLE_MUTATION_COMMANDS,
    NEVER_CLOSE_ROLES,
    PROCESS_TERMINATING_ACTIONS,
    SELF_HANDLE_ENV,
    TERMINAL_ROLE_CLASSES,
    REQUIRED_ORCA_CLI_GUIDE_SNIPPETS,
    REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS,
    SUPPORTED_ORCA_APP_VERSION,
    TERMINAL_ORIGINS,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
    LIVE_RELEASE_STATES,
    OWNERSHIP_TRANSFERABLE_STATES,
    REUSABLE_WORKER_STATES,
    ReuseObservation,
    RuntimeAttempt,
    RuntimeScenarioResult,
    WORKER_RESOURCE_OUTCOMES,
    UnsupportedOrcaContract,
    SESSION_REUSE_OBJECTIVE,
    WorkflowEvidence,
    cleanup_authority,
    close_allowed,
    dispatch_context,
    run_session_reuse_runtime_scenario,
    validate_orca_contract,
)
from scripts.quality_profile import DEFAULT_PROFILE_PATH, INVALID_PROFILE_REASON
from scripts.task_context import (
    AGENT_MODES,
    BOUNDARY_RECEIPT_PREFIX,
    CANONICAL_PHASES,
    FINAL_REVIEW_PHASE,
    REVIEWER_CONTEXT_KEYS,
    REVIEWER_CONTEXT_SPEC_HEADER,
    REVIEWER_DRILL_DOWN_MANDATE,
    TASK_BOUNDARY_KEYS,
    TASK_BOUNDARY_SPEC_HEADER,
    TaskContextError,
    parse_quality_gate,
    parse_reviewer_context,
    parse_reviewer_context_keys,
    parse_task_boundary,
    phase_artifact_contract,
    render_boundary_receipt,
)

# validate_skills.py imports its siblings by top-level module name, so scripts/ must be
# importable before it can be loaded. `unittest discover -s scripts` already arranges
# that; this keeps the other invocation forms working too.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from scripts.validate_skills import LIFECYCLE_CONTRACT
except ModuleNotFoundError:  # pragma: no cover - direct execution
    from validate_skills import LIFECYCLE_CONTRACT


SKILL_NEVER_CLOSE_TERMINAL_ROLES = frozenset(
    LIFECYCLE_CONTRACT["NEVER_CLOSE_TERMINAL_ROLES"]
)
SKILL_CLOSE_ELIGIBLE_TERMINAL_ROLES = frozenset(
    LIFECYCLE_CONTRACT["CLOSE_ELIGIBLE_TERMINAL_ROLES"]
)

NEW_ORCHESTRATION_SNIPPETS = (
    "orca orchestration task-create --spec <text> [--deps <json_array>]",
    "orca orchestration task-list [--status <status>] [--ready]",
    "orca orchestration dispatch-show --task <task_id>",
    "worker-start --task <next_task_id> --terminal <handle> --json",
    "orca orchestration worker-show --dispatch <dispatch_id> --json",
)

# Shaped exactly like a live `worker_done` payload: the dispatch preamble sends
# --task-id/--dispatch-id/--outcome, so all three identities are present. STEP 1b now
# requires all three, which is what makes the omission fixtures below meaningful.
DONE = {
    "payload": json.dumps(
        {"taskId": "task_g", "dispatchId": "ctx_1", "outcome": "succeeded"}
    ),
    "body": "ok",
}
# The completion timestamp axis (a) requires alongside a settled status. The live
# runtime writes `completed_at` on both the completed and the failed Dispatch row.
COMPLETED_AT = "2026-08-21 20:12:31"


def done_for(
    dispatch_id: str, task_id: str = "task_g", outcome: str = "succeeded"
) -> dict[str, Any]:
    """DONE, but for another dispatch.

    A reuse chain settles more than one Dispatch on one terminal, and STEP 1b refuses
    a payload whose identities are not the ones being settled -- so a second attempt
    needs a second payload, not the module-level one.
    """
    return {
        "payload": json.dumps(
            {"taskId": task_id, "dispatchId": dispatch_id, "outcome": outcome}
        ),
        "body": "ok",
    }


class AxisCase(NamedTuple):
    """One account_axes() row.

    Named fields, not raw indexes: the previous index-based relation test read the
    cleanup-authority column where it meant to read the action, and therefore checked
    nothing. A NamedTuple makes that class of mistake impossible to write.
    """

    name: str
    role: str
    origin: str
    owner_dispatch_id: str
    supervised: bool
    observation: dict[str, Any]
    liveness: str
    authority: str
    action: str
    lifecycle: str = "release"  # the coordinator's choice; only reuse/retain differ


class RecordingExec:
    """Deterministic stand-in for OrcaRuntimeHarness._exec_orca.

    Replaces ONLY the process boundary, so harness.call() still runs for real: JSON
    parsing, self._raw.append({"command": ..., "response": ...}) and the ok/returncode
    check all keep their production behaviour. lifecycle_commands() reads that same
    _raw log, which is what makes the assertions below meaningful.
    """

    RESULTS = {
        "worker-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": {"releaseState": "released"},
        },
        "worker-release": {"state": "released"},
        "worker-retain": {"state": "retained"},
        "check": {},
        "task-list": {"tasks": [{"id": "task_g", "status": "completed"}]},
        "dispatch-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT}
        },
        "dispatch": {"dispatch": {"id": "ctx_1"}},
        "worker-start": {"dispatchId": "ctx_1"},
        "task-create": {"task": {"id": "task_g"}},
        "create": {"terminal": {"handle": "term_created"}},
        "send": {},
        "wait": {"wait": {"satisfied": True}},
        "close": {},
    }

    ACCEPTED_DONE = {
        "deliveryId": "dlv_1",
        "timedOut": False,
        "messages": [
            {
                "id": "msg_1",
                "type": "worker_done",
                "payload": json.dumps(
                    {"taskId": "task_g", "dispatchId": "ctx_1", "outcome": "succeeded"}
                ),
                "body": "ok",
            }
        ],
    }

    def __init__(
        self,
        *,
        fail_on: str | None = None,
        errors: dict[str, dict[str, Any]] | None = None,
        results: dict[str, Any] | None = None,
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.fail_on = fail_on
        # errors: verb -> structured Orca error body, returned with ok=False and
        # returncode 0 (the shape a caller passing allow_error=True must handle).
        self.errors = dict(errors or {})
        self.results = {**self.RESULTS, **(results or {})}
        # An unmodelled verb answers with an empty ok result instead of raising, so a
        # method sweep cannot be silently truncated by the first command nobody pinned.
        self.unmodelled: list[str] = []

    @property
    def verbs(self) -> list[str]:
        return [command[1] if len(command) > 1 else command[0] for command in self.commands]

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        self.commands.append(args)
        verb = args[1] if len(args) > 1 else args[0]
        if verb == self.fail_on:
            return 1, json.dumps(
                {"ok": False, "error": f"simulated failure in {verb}"}
            )
        if verb in self.errors:
            return 0, json.dumps({"ok": False, "error": self.errors[verb]})
        if verb not in self.results:
            self.unmodelled.append(verb)
        return 0, json.dumps({"ok": True, "result": self.results.get(verb, {})})


class SequentialTerminalExec(RecordingExec):
    """RecordingExec whose `terminal create` hands back a fresh handle each call.

    The base recorder pins one handle, which would make every freshness assertion
    below vacuously false; this subclass is the minimum needed to distinguish
    "a new terminal per attempt" from "the same terminal twice".
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.created: list[str] = []

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        if args[:2] == ("terminal", "create"):
            handle = f"term_fr_{len(self.created) + 1}"
            self.created.append(handle)
            self.commands.append(args)
            return 0, json.dumps({"ok": True, "result": {"terminal": {"handle": handle}}})
        return super().__call__(args)


class EchoingTerminalExec(SequentialTerminalExec):
    """SequentialTerminalExec that also plays the agent, and answers with its input.

    It keeps the `--spec` text of every task-create and the `--text` of every
    terminal send, and the worker_done body it hands back is the boundary receipt
    parsed out of the spec that was dispatched FOR THAT TASK. That is the difference
    the finding turned on: a coordinator can always show what it recorded, but only
    an answer derived from the dispatched input can show what the agent received. A
    boundary that never reached the Task spec has no receipt to echo, so the positive
    test below fails at the assertion rather than passing on metadata.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.specs: dict[str, str] = {}  # task id -> the spec it was created with
        self.sent: list[str] = []  # every low-level `terminal send --text` payload

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        verb = args[1] if len(args) > 1 else args[0]
        code, payload = super().__call__(args)
        body = json.loads(payload)
        result = body.get("result") or {}
        if verb == "task-create" and "--spec" in args:
            self.specs[result["task"]["id"]] = args[args.index("--spec") + 1]
        elif verb == "send" and "--text" in args:
            self.sent.append(args[args.index("--text") + 1])
        elif verb == "check" and result.get("messages"):
            for message in result["messages"]:
                task_id = json.loads(message["payload"])["taskId"]
                message["body"] = "ok" + render_boundary_receipt(
                    self.specs.get(task_id, "")
                )
            return code, json.dumps(body)
        return code, payload


class OrcaRuntimeContractTests(unittest.TestCase):
    def test_pinned_version_and_guide_contract_pass(self) -> None:
        validate_orca_contract(
            SUPPORTED_ORCA_APP_VERSION,
            "\n".join(REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS),
            "\n".join(REQUIRED_ORCA_CLI_GUIDE_SNIPPETS),
        )

    def test_different_orca_version_is_blocked(self) -> None:
        with self.assertRaisesRegex(UnsupportedOrcaContract, "installed runtime"):
            validate_orca_contract("1.4.185", "", "")

    def test_guide_grammar_drift_is_blocked(self) -> None:
        with self.assertRaisesRegex(UnsupportedOrcaContract, "pinned grammar"):
            validate_orca_contract(SUPPORTED_ORCA_APP_VERSION, "", "")

    @patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"})
    def test_environment_override_resolves_non_default_executable(self) -> None:
        self.assertEqual(OrcaRuntimeHarness._resolve_orca(), "/opt/orca-dev")

    @patch("scripts.orca_fake_agent.subprocess.run")
    def test_worker_done_uses_resolved_orca_executable(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}\n", stderr=""
        )

        with redirect_stdout(StringIO()):
            send_done(
                "task_example",
                "ctx_example",
                None,
                "succeeded",
                "done",
                "/opt/orca-dev",
            )

        command = run.call_args.args[0]
        self.assertEqual(command[0], "/opt/orca-dev")
        self.assertEqual(command.count("worker_done"), 1)

    # ---- T-4 / T-5: pinned grammar for the new lifecycle paths ------------

    def test_dependency_and_worker_start_terminal_grammar_is_required(self) -> None:
        for snippet in NEW_ORCHESTRATION_SNIPPETS:
            with self.subTest(snippet=snippet):
                guide = "\n".join(
                    entry
                    for entry in REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS
                    if entry != snippet
                )
                with self.assertRaisesRegex(UnsupportedOrcaContract, "pinned grammar"):
                    validate_orca_contract(
                        SUPPORTED_ORCA_APP_VERSION,
                        guide,
                        "\n".join(REQUIRED_ORCA_CLI_GUIDE_SNIPPETS),
                    )

    def test_tui_idle_wait_grammar_is_required(self) -> None:
        cli_guide = "\n".join(
            entry
            for entry in REQUIRED_ORCA_CLI_GUIDE_SNIPPETS
            if entry != "terminal wait --terminal <handle> --for tui-idle"
        )
        with self.assertRaisesRegex(UnsupportedOrcaContract, "pinned grammar"):
            validate_orca_contract(
                SUPPORTED_ORCA_APP_VERSION,
                "\n".join(REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS),
                cli_guide,
            )

    # ---- T-1 / T-2: cleanup authority is a pure, exhaustively tested function ----

    def test_never_close_roles_are_never_authorized(self) -> None:
        self.assertIn("coordinator_session", NEVER_CLOSE_ROLES)
        self.assertIn("run_owner_fixture", NEVER_CLOSE_ROLES)
        self.assertEqual(NEVER_CLOSE_ROLES & CLOSE_ELIGIBLE_ROLES, frozenset())
        for role in sorted(NEVER_CLOSE_ROLES):
            for origin in sorted(TERMINAL_ORIGINS):
                for owned in (True, False):
                    with self.subTest(role=role, origin=origin, owned=owned):
                        # (self_created, owned=True) is the exact combination that was
                        # historically mistaken for "closable".
                        self.assertNotEqual(
                            cleanup_authority(role, origin, owned), "authorized"
                        )
                        self.assertFalse(close_allowed(role, origin, owned))
        # the reporting distinction is preserved, not flattened
        self.assertEqual(
            cleanup_authority("coordinator_session", "self_created", True),
            "not_authorized",
        )
        self.assertEqual(
            cleanup_authority("unknown_role", "self_created", True), "unknown"
        )

    def test_close_eligible_roles_still_require_self_created_ownership(self) -> None:
        for role in sorted(CLOSE_ELIGIBLE_ROLES):
            with self.subTest(role=role):
                self.assertEqual(cleanup_authority(role, "adopted", True), "unknown")
                self.assertEqual(
                    cleanup_authority(role, "pre_existing", True), "unknown"
                )
                self.assertEqual(
                    cleanup_authority(role, "self_created", False), "unknown"
                )
                self.assertEqual(
                    cleanup_authority(role, "self_created", True), "authorized"
                )
                self.assertTrue(close_allowed(role, "self_created", True))
        # the harness widens the skill's never-close list; it never narrows it
        self.assertLessEqual(SKILL_NEVER_CLOSE_TERMINAL_ROLES, NEVER_CLOSE_ROLES)
        self.assertEqual(SKILL_CLOSE_ELIGIBLE_TERMINAL_ROLES, CLOSE_ELIGIBLE_ROLES)


class DuplicateSettlementTests(unittest.TestCase):
    """T-9: a second settlement must not produce a second lifecycle mutation."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_harness(self, recorder: RecordingExec) -> OrcaRuntimeHarness:
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder  # the only process boundary
        harness.run_owner, harness.run_id = "term_owner", "run_offline"
        harness.register_terminal(
            "term_worker",
            role="active_worker",
            origin="self_created",
            intended_role="phase_worker",
            owner_dispatch_id="ctx_1",
        )
        self._assert_command_log_is_wired(harness, recorder)
        return harness

    def _assert_command_log_is_wired(
        self, harness: OrcaRuntimeHarness, recorder: RecordingExec
    ) -> None:
        """Fixture self-check: ONE real call must be visible in ALL THREE views.

        Proves the stub did not bypass harness._raw, i.e. that the log the recorder
        sees and the log lifecycle_commands() reads are the same stream.
        """
        probe = ("orchestration", "worker-retain", "--dispatch", "ctx_probe")
        payload = harness.call(*probe)

        self.assertEqual(payload["result"]["state"], "retained")
        self.assertEqual(recorder.commands[-1], probe)
        self.assertEqual(harness._raw[-1]["command"], list(probe))
        self.assertEqual(harness._raw[-1]["response"]["ok"], True)
        self.assertEqual(harness.lifecycle_commands("ctx_probe"), ["worker-retain"])
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])

        harness._raw.clear()
        recorder.commands.clear()

    def test_duplicate_settlement_replays_and_issues_no_lifecycle_command(self) -> None:
        recorder = RecordingExec()
        harness = self.make_harness(recorder)

        first = harness.settle_attempt(
            "worker", 1, "task_g", "ctx_1", DONE, "dlv_1", terminal="term_worker"
        )
        after_first = list(recorder.commands)
        raw_after_first = [dict(row) for row in harness._raw]

        second = harness.settle_attempt(
            "worker", 1, "task_g", "ctx_1", DONE, "dlv_1", terminal="term_worker"
        )

        self.assertEqual(asdict(second), asdict(first))
        self.assertEqual(recorder.commands, after_first)
        self.assertEqual(harness._raw, raw_after_first)
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])
        self.assertEqual(
            [c for c in recorder.commands if c[1] == "worker-release"],
            [("orchestration", "worker-release", "--dispatch", "ctx_1")],
        )
        self.assertEqual(first.finalizations, 1)
        self.assertEqual(second.finalizations, 1)
        self.assertIsNot(second, first)
        self.assertEqual(harness._ledger["ctx_1"]["replays"], 1)
        self.assertEqual(harness._ledger["ctx_1"]["state"], "finalized")

    def test_finalize_once_is_single_assignment(self) -> None:
        recorder = RecordingExec()
        harness = self.make_harness(recorder)
        first = harness.settle_attempt(
            "worker", 1, "task_g", "ctx_1", DONE, "dlv_1", terminal="term_worker"
        )

        with self.assertRaisesRegex(OrcaRuntimeError, "already finalized"):
            harness.finalize_once(
                "ctx_1",
                attempt=first,
                settlement="completed",
                worker_resource="release",
                process_liveness="already exited",
                cleanup_authority="authorized",
                terminal_role="phase_worker",
            )
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])

    def test_crashed_settlement_is_not_auto_retried(self) -> None:
        # The crash point is the delivery ack (STEP 3), i.e. the first call *after*
        # the lifecycle mutation. Anything earlier now fails in STEP 1b's settlement
        # verification, which by design leaves no mutation to be repeated.
        recorder = RecordingExec(fail_on="check")
        harness = self.make_harness(recorder)

        with self.assertRaises(OrcaRuntimeError):
            harness.settle_attempt(
                "worker", 1, "task_g", "ctx_1", DONE, "dlv_1", terminal="term_worker"
            )
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])
        self.assertEqual(harness._ledger["ctx_1"]["state"], "in_progress")

        with self.assertRaisesRegex(OrcaRuntimeError, "in progress|crashed"):
            harness.settle_attempt(
                "worker", 1, "task_g", "ctx_1", DONE, "dlv_1", terminal="term_worker"
            )
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])

    # Every required parameter of every public harness method must appear here, so a
    # newly added method cannot slip past the sweep below by being unbindable.
    PROBE_ARGUMENTS: dict[str, Any] = {
        "attempt": RuntimeAttempt(
            role="worker",
            iteration=1,
            task_id="task_g",
            dispatch_id="ctx_1",
            outcome="succeeded",
            task_status="completed",
            dispatch_status="completed",
            worker_state="settled",
            terminal_state="released",
            lifecycle_action="release",
            worker_done_count=1,
            execution_path="offline",
        ),
        "delivery_id": "dlv_1",
        "dispatch_id": "ctx_1",
        "done": DONE,
        "handle": "term_worker",
        "iteration": 1,
        "lifecycle": "release",
        "mode": "done",
        "new_role": "external_or_adopted",
        "objective": "probe",
        "observation": {},
        "origin": "self_created",
        "owned_by_this_dispatch": True,
        "agent_command": "exec fake_bin/codex",
        "effect": "reused",
        "release_process_action": "none",
        "retain_reason": "explicit_user_request",
        "result": RuntimeScenarioResult(
            scenario="probe", run_id="run_offline", status="passed", iteration=1
        ),
        "role": "active_worker",
        "settled": True,
        "spec": "probe",
        "supervised": True,
        "task_id": "task_g",
        "task_status": "completed",
        "terminal": "term_worker",
    }

    def test_no_public_api_moves_a_claimed_row_back_to_absent(self) -> None:
        """The settlement ledger is one-way: absent -> in_progress -> finalized.

        Regression guard for the removed release_claim(): a claim carries no proof of
        how many mutations already went out, so any API that reverts it to "absent"
        would let the next settle_attempt() pass the STEP 0 gate and re-issue a
        lifecycle command the runtime already accepted. Sweeps every public method --
        the process boundary is stubbed, so calls either no-op, raise, or mutate only
        this throwaway harness -- and asserts none of them reverts the claimed row.
        Forward movement (finalize_once) is allowed; only "absent" is not.
        """
        # fail_on the delivery ack: the crash must land after the mutation, so the
        # swept row really is "claimed, and one lifecycle command already went out".
        recorder = RecordingExec(fail_on="check")
        harness = self.make_harness(recorder)

        with self.assertRaises(OrcaRuntimeError):
            harness.settle_attempt(
                "worker", 1, "task_g", "ctx_1", DONE, "dlv_1", terminal="term_worker"
            )
        self.assertEqual(harness._ledger["ctx_1"]["state"], "in_progress")
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])

        self.assertFalse(hasattr(harness, "release_claim"))

        swept, unbindable = [], []
        for name in sorted(dir(harness)):
            if name.startswith("_"):
                continue
            member = getattr(harness, name)
            if not callable(member):
                continue
            arguments = {}
            for parameter in inspect.signature(member).parameters.values():
                if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                    continue
                if parameter.name in self.PROBE_ARGUMENTS:
                    arguments[parameter.name] = self.PROBE_ARGUMENTS[parameter.name]
                elif parameter.default is parameter.empty:
                    unbindable.append(f"{name}({parameter.name})")
                    break
            else:
                swept.append(name)
                try:
                    with redirect_stdout(StringIO()):
                        member(**arguments)
                except Exception:  # a raising method still must not revert the row
                    pass
                self.assertNotEqual(
                    harness._ledger["ctx_1"]["state"],
                    "absent",
                    f"{name}() reverted a claimed settlement to absent",
                )

        self.assertEqual(unbindable, [], "extend PROBE_ARGUMENTS to cover these")
        self.assertIn("claim_settlement", swept)
        self.assertIn("finalize_once", swept)
        self.assertIn("settle_attempt", swept)

        # The consequence that actually matters: whatever the sweep did, the gate
        # never hands this dispatch a fresh mutation window again. None is the one
        # return that means "you now own the settlement, go ahead and mutate".
        try:
            regrant: object = harness.claim_settlement(
                "ctx_1",
                task_id="task_g",
                terminal="term_worker",
                role="active_worker",
                iteration=1,
            )
        except OrcaRuntimeError:
            regrant = "refused"
        self.assertIsNotNone(
            regrant, "claim_settlement re-granted a claimed dispatch's mutation window"
        )

    # Only these functions may write a settlement row's "state" slot at all, and only
    # the first of them may mention the literal "absent".
    STATE_WRITERS = frozenset({"claim_settlement", "finalize_once"})
    ABSENT_WRITERS = frozenset({"claim_settlement"})

    @staticmethod
    def _enclosing_scopes(module: ast.Module) -> dict[ast.AST, str]:
        """Map every node to the name of the function that lexically contains it."""
        scopes: dict[ast.AST, str] = {}

        def walk(node: ast.AST, scope: str) -> None:
            for child in ast.iter_child_nodes(node):
                child_scope = (
                    child.name
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else scope
                )
                scopes[child] = child_scope
                walk(child, child_scope)

        walk(module, "<module>")
        return scopes

    @staticmethod
    def _writes_a_state_slot(node: ast.AST) -> bool:
        """True for any expression shape that stores into a row's "state" slot.

        Covers `row["state"] = ...`, `row.state = ...`, augmented and annotated
        assignments, walrus bindings, and a literal `row.update({"state": ...})` --
        the shapes the earlier ast.Assign-only check walked straight past.
        """
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "state"
                ):
                    return True
                if isinstance(target, ast.Attribute) and target.attr == "state":
                    return True
            return False
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "update":
                for argument in node.args:
                    if isinstance(argument, ast.Dict) and any(
                        isinstance(key, ast.Constant) and key.value == "state"
                        for key in argument.keys
                    ):
                        return True
            return False
        return False

    def test_settlement_state_is_never_assigned_absent_after_row_creation(self) -> None:
        """Structural half of the guard above, independent of any method name.

        Two invariants, because either alone is easy to slip past:
          1. the literal "absent" appears only inside claim_settlement() (plus the
             SETTLEMENT_STATES vocabulary), so no other code can name that state; and
          2. nothing outside claim_settlement()/finalize_once() writes a row's "state"
             slot at all, by any assignment shape, so a reversal cannot be smuggled in
             through a variable, a dict update, or an attribute store.
        Non-literal updates of a whole row (`row.update(axes)`) stay covered by the
        behavioural sweep in test_no_public_api_moves_a_claimed_row_back_to_absent.
        """
        module = ast.parse(Path(orca_runtime_harness.__file__).read_text())
        scopes = self._enclosing_scopes(module)

        absent_mentions = sorted(
            {
                (scopes.get(node, "<module>"), node.lineno)
                for node in ast.walk(module)
                if isinstance(node, ast.Constant) and node.value == "absent"
            }
        )
        stray_absent = [
            location
            for location in absent_mentions
            # the module-level SETTLEMENT_STATES vocabulary is the one allowed mention
            if location[0] not in self.ABSENT_WRITERS | {"<module>"}
        ]
        self.assertEqual(stray_absent, [], 'the literal "absent" escaped claim_settlement')
        self.assertTrue(
            any(scope in self.ABSENT_WRITERS for scope, _ in absent_mentions),
            "claim_settlement no longer creates rows in the absent state; "
            "this test is now checking nothing",
        )

        stray_writes = sorted(
            (scopes.get(node, "<module>"), node.lineno)
            for node in ast.walk(module)
            if self._writes_a_state_slot(node)
            and scopes.get(node, "<module>") not in self.STATE_WRITERS
        )
        self.assertEqual(
            stray_writes, [], "a settlement row's state is written outside the gate"
        )

    def test_recording_exec_preserves_the_harness_command_log(self) -> None:
        recorder = RecordingExec()
        harness = self.make_harness(recorder)

        harness.call("orchestration", "worker-release", "--dispatch", "ctx_x")

        self.assertEqual(len(recorder.commands), 1)
        self.assertEqual(len(harness._raw), 1)
        self.assertEqual(
            harness._raw[0]["command"],
            ["orchestration", "worker-release", "--dispatch", "ctx_x"],
        )
        self.assertEqual(harness.lifecycle_commands("ctx_x"), ["worker-release"])
        self.assertEqual(harness.lifecycle_commands(), ["worker-release"])
        self.assertEqual(harness.lifecycle_commands("ctx_other"), [])


class OfflineHarnessTestCase(unittest.TestCase):
    """Shared wiring for tests that drive the harness with only _exec_orca stubbed."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(self, recorder: RecordingExec) -> OrcaRuntimeHarness:
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder  # the only process boundary
        harness.run_owner, harness.run_id = "term_owner", "run_offline"
        return harness

    def worker_terminal(
        self,
        harness: OrcaRuntimeHarness,
        handle: str = "term_worker",
        *,
        role: str = "active_worker",
        origin: str = "self_created",
        intended_role: str = "phase_worker",
        owner_dispatch_id: str | None = "ctx_1",
    ) -> str:
        harness.register_terminal(
            handle,
            role=role,
            origin=origin,
            intended_role=intended_role,
            owner_dispatch_id=owner_dispatch_id,
        )
        return handle


class RunArtifactRootProvisioningTests(OfflineHarnessTestCase):
    """MAJOR 1 (PR #13 review): start_run() must provision the directory it names.

    Every other test in this file calls OfflineHarnessTestCase.build(), which sets
    harness.run_id directly and never calls start_run() at all -- exactly why this
    gap could ship unnoticed. These tests call the real start_run() instead, against
    a workspace setUp() created fresh for this test and never pre-populated.
    """

    def test_start_run_creates_the_directory_before_returning(self) -> None:
        recorder = RecordingExec(results={"run-create": {"run": {"id": "run_fresh"}}})
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder

        target = self.artifact_dir / "artifacts" / "runs" / "run_fresh"
        self.assertFalse(target.exists(), "setUp must not pre-create the run dir")

        run_id = harness.start_run("fresh run objective")

        self.assertEqual(run_id, "run_fresh")
        self.assertTrue(
            target.is_dir(),
            "start_run() must provision artifacts/runs/<run-id>/ before any Task "
            "referencing it can be dispatched",
        )

    def test_a_second_run_in_the_same_workspace_gets_its_own_directory(self) -> None:
        first = RecordingExec(results={"run-create": {"run": {"id": "run_one"}}})
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = first
        harness.start_run("first run")

        second = RecordingExec(results={"run-create": {"run": {"id": "run_two"}}})
        harness._exec_orca = second
        harness.start_run("second run")

        runs_dir = self.artifact_dir / "artifacts" / "runs"
        self.assertEqual(sorted(path.name for path in runs_dir.iterdir()), ["run_one", "run_two"])


class UnsupervisedSettlementTests(OfflineHarnessTestCase):
    """Goal 1: a Dispatch with no supervised worker resource never gets released.

    The supervised branch is covered by DuplicateSettlementTests; this is the branch
    the original defect actually lived on -- the dispatch the runtime auto-settled and
    for which no worker resource was ever registered.
    """

    def settle(self, harness: OrcaRuntimeHarness) -> RuntimeAttempt:
        return harness.settle_attempt(
            "worker",
            1,
            "task_g",
            "ctx_1",
            DONE,
            "dlv_1",
            supervised=False,
            terminal="term_worker",
        )

    def test_unsupervised_settlement_issues_no_worker_resource_command(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)
        self.worker_terminal(harness)

        attempt = self.settle(harness)

        self.assertEqual(attempt.worker_resource, "unsupervised")
        self.assertIn(attempt.worker_resource, set(WORKER_RESOURCE_OUTCOMES))
        self.assertEqual(attempt.execution_path, "tracked_external")
        self.assertEqual(attempt.settlement, "completed")
        # the whole point: no lifecycle mutation exists to repeat
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])
        self.assertEqual(harness.lifecycle_commands(), [])
        self.assertNotIn("worker-release", recorder.verbs)
        self.assertNotIn("worker-retain", recorder.verbs)
        # and the supervised registry is not even consulted on this branch
        self.assertNotIn("worker-show", recorder.verbs)

    def test_unsupervised_settlement_replay_still_issues_nothing(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)
        self.worker_terminal(harness)

        first = self.settle(harness)
        after_first = list(recorder.commands)
        raw_after_first = [dict(row) for row in harness._raw]

        second = self.settle(harness)

        self.assertEqual(asdict(second), asdict(first))
        self.assertEqual(recorder.commands, after_first)
        self.assertEqual(harness._raw, raw_after_first)
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])
        self.assertEqual(harness._ledger["ctx_1"]["replays"], 1)
        self.assertEqual(harness._ledger["ctx_1"]["state"], "finalized")

    def test_crashed_unsupervised_settlement_is_not_auto_retried(self) -> None:
        recorder = RecordingExec(fail_on="task-list")
        harness = self.build(recorder)
        self.worker_terminal(harness)

        with self.assertRaises(OrcaRuntimeError):
            self.settle(harness)
        self.assertEqual(harness._ledger["ctx_1"]["state"], "in_progress")

        with self.assertRaisesRegex(OrcaRuntimeError, "in progress|crashed"):
            self.settle(harness)
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])


class UnexpectedExitSettlementTests(OfflineHarnessTestCase):
    """Goal 1, second mutating path: observe_unexpected_exit shares the STEP 0 gate."""

    RESULTS = {
        "worker-show": {
            "dispatch": {"status": "dispatched"},
            "worker": {"state": "outcome_unknown"},
            "terminalResource": {"releaseState": "released"},
        },
        "worker-abandon": {"state": "abandoned"},
        "task-list": {"tasks": [{"id": "task_g", "status": "failed"}]},
    }

    def test_recovery_path_mutates_once_and_replays_thereafter(self) -> None:
        recorder = RecordingExec(results=self.RESULTS)
        harness = self.build(recorder)

        first = harness.observe_unexpected_exit("worker", 1, phase="implementation")

        self.assertEqual(first.worker_done_count, 0)
        self.assertEqual(first.lifecycle_action, "abandon:abandoned;release:released")
        self.assertEqual(
            harness.lifecycle_commands("ctx_1"), ["worker-abandon", "worker-release"]
        )
        # no accepted worker_done, so the terminal must NOT become close-eligible
        self.assertEqual(first.terminal_role, "active_worker")
        self.assertEqual(first.cleanup_authority, "not_authorized")
        self.assertEqual(first.finalizations, 1)

        second = harness.observe_unexpected_exit("worker", 1, phase="implementation")

        self.assertEqual(asdict(second), asdict(first))
        self.assertEqual(
            harness.lifecycle_commands("ctx_1"), ["worker-abandon", "worker-release"]
        )
        self.assertEqual(harness._ledger["ctx_1"]["replays"], 1)

    def test_a_worker_that_died_before_adoption_takes_the_same_recovery(self) -> None:
        """Ladder rung 3 waits for TUI idle, so an agent can exit before worker-start.

        The runtime then reports the worker resource as "ready" -- registered but
        never observed running. That is an unsettled dispatch exactly like
        outcome_unknown, and it must be abandoned rather than read as a settlement.
        """
        results = {
            **self.RESULTS,
            "worker-show": {
                "dispatch": {"status": "dispatched"},
                "worker": {"state": "ready"},
                "terminalResource": {"releaseState": "released"},
            },
        }
        recorder = RecordingExec(results=results)
        harness = self.build(recorder)

        attempt = harness.observe_unexpected_exit("worker", 1, phase="implementation")

        self.assertEqual(attempt.worker_done_count, 0)
        self.assertEqual(attempt.settlement, "failed")
        self.assertEqual(
            harness.lifecycle_commands("ctx_1"), ["worker-abandon", "worker-release"]
        )
        self.assertEqual(attempt.terminal_role, "active_worker")
        self.assertEqual(attempt.cleanup_authority, "not_authorized")

    def test_an_unrecognised_worker_state_is_still_refused(self) -> None:
        """The widened set is a set, not a hole: unknown states still stop the run."""
        results = {
            **self.RESULTS,
            "worker-show": {
                "dispatch": {"status": "dispatched"},
                "worker": {"state": "running"},
                "terminalResource": {"releaseState": "active"},
            },
        }
        harness = self.build(RecordingExec(results=results))

        with self.assertRaisesRegex(OrcaRuntimeError, "unexpected exit left worker"):
            harness.observe_unexpected_exit("worker", 1, phase="implementation")


class AccountAxesTests(OfflineHarnessTestCase):
    """Goal 2: liveness (c1) and cleanup authority (c2) are decided separately.

    account_axes is the only function that can ever produce a close decision, and the
    supervised/live combinations below are unreachable from the runtime scenarios on a
    machine whose agents are unconfigured. They are pinned here instead.
    """

    # role, origin, owner dispatch, supervised, observation, (c1), (c2), action,
    # and optionally the lifecycle intent (defaults to "release").
    RAW_CASES = (
        (
            "supervised released terminal",
            "phase_worker", "self_created", "ctx_1", True,
            {"terminalResource": {"releaseState": "released"}},
            "already exited", "authorized", "nothing to do",
        ),
        (
            "supervised live terminal",
            "phase_worker", "self_created", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "authorized", "released by runtime",
        ),
        (
            "supervised without a terminal resource",
            "phase_worker", "self_created", "ctx_1", True,
            {},
            "disputed", "authorized", "nothing to do",
        ),
        (
            "live active worker",
            "active_worker", "self_created", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "not_authorized", "retained",
        ),
        (
            "live coordinator session",
            "coordinator_session", "self_created", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "not_authorized", "retained",
        ),
        (
            "live setup terminal",
            "setup_terminal", "self_created", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "not_authorized", "retained",
        ),
        (
            "live adopted terminal",
            "external_or_adopted", "adopted", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "not_authorized", "retained",
        ),
        (
            "live phase worker owned by another dispatch",
            "phase_worker", "self_created", "ctx_other", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "unknown", "retained",
        ),
        (
            "live phase worker that was adopted rather than created",
            "phase_worker", "adopted", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "unknown", "retained",
        ),
        (
            # The row the fix in this iteration is about: unsupervised + live is only
            # reachable for a terminal the caller chose to reuse or retain, so the
            # action must be "retained" even though authority is proven. The intent is
            # supplied per-case below; every other row keeps the default "release".
            "unsupervised reused terminal (reuse intent)",
            "phase_worker", "self_created", "ctx_1", False,
            {"terminalState": "reused"},
            "live", "authorized", "retained", "reuse",
        ),
        (
            "unsupervised reused terminal (retain intent)",
            "phase_worker", "self_created", "ctx_1", False,
            {"terminalState": "reused"},
            "live", "authorized", "retained", "retain",
        ),
        (
            # Same axes, release intent: this is the only unsupervised row that may
            # ever reach a close, and it is unreachable while the process is live
            # unless the caller declined both retain intents.
            "unsupervised live terminal released by the coordinator",
            "phase_worker", "self_created", "ctx_1", False,
            {"terminalState": "reused"},
            "live", "authorized", "closed by coordinator", "release",
        ),
        (
            "supervised live terminal handed to the next dispatch",
            "phase_worker", "self_created", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "authorized", "retained", "reuse",
        ),
        (
            "supervised live terminal held for debugging",
            "phase_worker", "self_created", "ctx_1", True,
            {"terminalResource": {"releaseState": "active"}},
            "live", "authorized", "retained", "retain",
        ),
        (
            "unsupervised exited terminal",
            "phase_worker", "self_created", "ctx_1", False,
            {"terminalState": "exited"},
            "already exited", "authorized", "nothing to do",
        ),
        (
            "unsupervised terminal of unknown state",
            "phase_worker", "self_created", "ctx_1", False,
            {},
            "disputed", "authorized", "nothing to do",
        ),
        (
            "unsupervised live active worker",
            "active_worker", "self_created", "ctx_1", False,
            {"terminalState": "reused"},
            "live", "not_authorized", "retained",
        ),
    )

    CASES = ()  # populated below from RAW_CASES; see setUpClass

    @classmethod
    def setUpClass(cls) -> None:
        cls.CASES = tuple(AxisCase(*row) for row in cls.RAW_CASES)

    def test_axis_matrix(self) -> None:
        for case in self.CASES:
            with self.subTest(case=case.name):
                recorder = RecordingExec()
                harness = self.build(recorder)
                self.worker_terminal(
                    harness,
                    role=case.role,
                    origin=case.origin,
                    owner_dispatch_id=case.owner_dispatch_id,
                )

                axes = harness.account_axes(
                    "task_g",
                    "ctx_1",
                    "term_worker",
                    supervised=case.supervised,
                    observation=case.observation,
                    task_status="completed",
                    lifecycle=case.lifecycle,
                )

                self.assertEqual(axes[0], "completed")
                self.assertEqual(
                    axes[1], case.lifecycle if case.supervised else "unsupervised"
                )
                self.assertEqual(axes[2], case.liveness)
                self.assertEqual(axes[3], case.authority)
                self.assertEqual(axes[4], case.role)
                self.assertEqual(
                    harness.ledger_terminal("term_worker")["action"], case.action
                )
                # accounting reads what was already fetched; it asks the runtime nothing
                self.assertEqual(recorder.commands, [])

    def test_close_is_only_ever_decided_for_a_close_eligible_role(self) -> None:
        """The relation, not just the table rows: nothing else can reach a close."""
        closing = {"released by runtime", "closed by coordinator"}
        for case in self.CASES:
            with self.subTest(case=case.name):
                if case.action in closing:
                    self.assertIn(case.role, CLOSE_ELIGIBLE_ROLES)
                if case.role in NEVER_CLOSE_ROLES:
                    self.assertNotIn(case.action, closing)

    def test_a_retain_intent_is_never_accounted_as_a_close_or_a_release(self) -> None:
        """The relation the previous iteration got wrong, stated as an invariant.

        Independent of the table: for every role/origin/ownership/supervision
        combination the matrix covers, choosing reuse or retain must produce
        "retained" -- including the authorized rows, which are exactly the rows where
        a close would otherwise be possible.
        """
        closing = {"released by runtime", "closed by coordinator"}
        for case in self.CASES:
            for intent in ("reuse", "retain"):
                with self.subTest(case=case.name, intent=intent):
                    harness = self.build(RecordingExec())
                    self.worker_terminal(
                        harness,
                        role=case.role,
                        origin=case.origin,
                        owner_dispatch_id=case.owner_dispatch_id,
                    )

                    axes = harness.account_axes(
                        "task_g",
                        "ctx_1",
                        "term_worker",
                        supervised=case.supervised,
                        observation=case.observation,
                        task_status="completed",
                        lifecycle=intent,
                    )

                    action = harness.ledger_terminal("term_worker")["action"]
                    self.assertNotIn(action, closing)
                    expected = "retained" if case.liveness == "live" else "nothing to do"
                    self.assertEqual(action, expected)
                    # the axes themselves are untouched by the intent
                    self.assertEqual(axes[2], case.liveness)
                    self.assertEqual(axes[3], case.authority)

    def test_an_unknown_lifecycle_intent_is_refused_rather_than_guessed(self) -> None:
        harness = self.build(RecordingExec())
        self.worker_terminal(harness)

        for lifecycle in ("unsupervised", "close", ""):
            with self.subTest(lifecycle=lifecycle):
                with self.assertRaisesRegex(OrcaRuntimeError, "lifecycle intent"):
                    harness.account_axes(
                        "task_g",
                        "ctx_1",
                        "term_worker",
                        supervised=True,
                        observation={"terminalResource": {"releaseState": "active"}},
                        task_status="completed",
                        lifecycle=lifecycle,
                    )

    def test_settlement_axis_reads_only_the_task_status(self) -> None:
        for task_status, expected in (
            ("completed", "completed"),
            ("failed", "failed"),
            ("blocked", "not-settled"),
            ("dispatched", "not-settled"),
            ("", "not-settled"),
        ):
            with self.subTest(task_status=task_status):
                harness = self.build(RecordingExec())
                self.worker_terminal(harness)
                axes = harness.account_axes(
                    "task_g",
                    "ctx_1",
                    "term_worker",
                    supervised=True,
                    observation={"terminalResource": {"releaseState": "released"}},
                    task_status=task_status,
                    lifecycle="release",
                )
                self.assertEqual(axes[0], expected)

    def test_supervised_worker_resource_records_the_chosen_lifecycle(self) -> None:
        for lifecycle in ("reuse", "retain", "release"):
            with self.subTest(lifecycle=lifecycle):
                harness = self.build(RecordingExec())
                self.worker_terminal(harness)
                axes = harness.account_axes(
                    "task_g",
                    "ctx_1",
                    "term_worker",
                    supervised=True,
                    observation={"terminalResource": {"releaseState": "released"}},
                    task_status="completed",
                    lifecycle=lifecycle,
                )
                self.assertEqual(axes[1], lifecycle)
                self.assertIn(axes[1], set(WORKER_RESOURCE_OUTCOMES))

    def test_an_unrecorded_handle_is_unknown_and_stays_out_of_the_ledger(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)

        axes = harness.account_axes(
            "task_g",
            "ctx_1",
            "term_ghost",
            supervised=False,
            observation={"terminalState": "reused"},
            task_status="completed",
            lifecycle="release",
        )

        self.assertEqual(axes[2], "live")
        self.assertEqual(axes[3], "unknown")
        self.assertEqual(axes[4], "unknown_role")
        self.assertNotIn("term_ghost", harness._terminals)
        self.assertEqual(recorder.commands, [])


class SettlementOrderingTests(OfflineHarnessTestCase):
    """Axis (a) is proven from provenance BEFORE the first lifecycle mutation.

    Human review of PR #10 found STEP 2 issuing worker-release / worker-retain on a
    Dispatch that could still be `dispatched`: STEP 0 answers "did I already settle
    this Dispatch?" (idempotency) and, before STEP 1b existed, nothing answered "is
    there a settlement to account at all?" (correctness). These tests pin the
    corrected order -- STEP 0 gate, read-only provenance, verification, then exactly
    one mutation -- on both execution paths.
    """

    SETTLED = {
        "worker-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": {"releaseState": "active"},
        },
        "task-list": {"tasks": [{"id": "task_g", "status": "completed"}]},
        "dispatch-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT}
        },
    }
    NOT_SETTLED = {
        "worker-show": {
            # a worker record that looks perfectly healthy: only the Dispatch and
            # Task rows say this dispatch has not reached an outcome yet
            "dispatch": {"status": "dispatched"},
            "worker": {"state": "settled"},
            "terminalResource": {"releaseState": "active"},
        },
        "task-list": {"tasks": [{"id": "task_g", "status": "dispatched"}]},
        "dispatch-show": {"dispatch": {"status": "dispatched"}},
    }
    LIFECYCLE_VERBS = ("worker-release", "worker-retain", "worker-abandon", "close")

    def settle(
        self,
        harness: OrcaRuntimeHarness,
        *,
        lifecycle: str = "release",
        supervised: bool = True,
        done: dict[str, Any] = DONE,
    ) -> RuntimeAttempt:
        return harness.settle_attempt(
            "worker",
            1,
            "task_g",
            "ctx_1",
            done,
            "dlv_1",
            lifecycle=lifecycle,
            supervised=supervised,
            terminal="term_worker",
        )

    def assert_no_lifecycle_mutation(
        self, harness: OrcaRuntimeHarness, recorder: RecordingExec
    ) -> None:
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])
        for verb in self.LIFECYCLE_VERBS:
            self.assertNotIn(verb, recorder.verbs)

    # ---- 1. a settled dispatch mutates exactly once, and only after the reads ----

    def test_a_settled_dispatch_allows_exactly_one_lifecycle_mutation(self) -> None:
        recorder = RecordingExec(results=self.SETTLED)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        attempt = self.settle(harness)

        self.assertEqual(attempt.settlement, "completed")
        self.assertEqual(attempt.dispatch_status, "completed")
        self.assertEqual(attempt.task_status, "completed")
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])
        # ordering, not just presence: both read-only provenance calls precede the
        # single mutation, and the delivery ack follows it.
        verbs = recorder.verbs
        self.assertLess(verbs.index("worker-show"), verbs.index("task-list"))
        self.assertLess(verbs.index("task-list"), verbs.index("worker-release"))
        self.assertLess(verbs.index("worker-release"), verbs.index("check"))
        self.assertNotIn("unsettled_reason", harness._ledger["ctx_1"])

    def test_a_failed_dispatch_is_settled_too(self) -> None:
        """`failed` is an outcome; only `dispatched` is not-settled."""
        results = {
            **self.SETTLED,
            "worker-show": {
                "dispatch": {"status": "failed", "completed_at": COMPLETED_AT},
                "worker": {"state": "settled"},
                "terminalResource": {"releaseState": "active"},
            },
            "task-list": {"tasks": [{"id": "task_g", "status": "failed"}]},
        }
        recorder = RecordingExec(results=results)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        attempt = self.settle(harness)

        self.assertEqual(attempt.settlement, "failed")
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])

    # ---- 2. a not-settled dispatch mutates zero times, loudly --------------------

    def test_a_not_settled_dispatch_issues_no_lifecycle_mutation(self) -> None:
        for lifecycle in sorted(orca_runtime_harness.LIFECYCLE_INTENTS):
            with self.subTest(lifecycle=lifecycle):
                recorder = RecordingExec(results=self.NOT_SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                with self.assertRaisesRegex(OrcaRuntimeError, "is not settled"):
                    self.settle(harness, lifecycle=lifecycle)

                self.assert_no_lifecycle_mutation(harness, recorder)
                # not silently swallowed: the claim stays, carrying the reason, so
                # the dispatch is recovered explicitly instead of re-mutated.
                row = harness._ledger["ctx_1"]
                self.assertEqual(row["state"], "in_progress")
                self.assertIn("dispatch status 'dispatched'", row["unsettled_reason"])
                self.assertIn("task status 'dispatched'", row["unsettled_reason"])

    def test_either_half_of_the_provenance_alone_is_not_a_settlement(self) -> None:
        """A completed Task with an open Dispatch (or the reverse) still blocks."""
        cases = {
            "dispatch still open": {
                **self.NOT_SETTLED,
                "task-list": {"tasks": [{"id": "task_g", "status": "completed"}]},
            },
            "task still open": {
                **self.NOT_SETTLED,
                "worker-show": self.SETTLED["worker-show"],
                "dispatch-show": self.SETTLED["dispatch-show"],
            },
        }
        for name, results in cases.items():
            with self.subTest(case=name):
                recorder = RecordingExec(results=results)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                with self.assertRaisesRegex(OrcaRuntimeError, "is not settled"):
                    self.settle(harness)

                self.assert_no_lifecycle_mutation(harness, recorder)

    def test_a_worker_without_an_outcome_is_refused_before_any_mutation(self) -> None:
        """The abandon-recovery states never reach release, even if the rows lie."""
        for state in sorted(orca_runtime_harness.UNSETTLED_WORKER_STATES):
            with self.subTest(worker_state=state):
                results = {
                    **self.SETTLED,
                    "worker-show": {
                        **self.SETTLED["worker-show"],
                        "worker": {"state": state},
                    },
                }
                recorder = RecordingExec(results=results)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                with self.assertRaisesRegex(OrcaRuntimeError, "produced no outcome"):
                    self.settle(harness)

                self.assert_no_lifecycle_mutation(harness, recorder)

    def test_a_stale_or_rejected_worker_done_is_refused_before_any_mutation(
        self,
    ) -> None:
        cases = {
            "stale": (
                {
                    "payload": json.dumps(
                        {"outcome": "succeeded", "dispatchId": "ctx_other"}
                    ),
                    "body": "ok",
                },
                "stale worker_done",
            ),
            "rejected": (
                {
                    "payload": json.dumps(
                        {
                            "outcome": "succeeded",
                            "dispatchId": "ctx_1",
                            "_orcaLifecycleRejection": True,
                        }
                    ),
                    "body": "ok",
                },
                "rejected by Orca",
            ),
        }
        for name, (done, expected) in cases.items():
            with self.subTest(case=name):
                recorder = RecordingExec(results=self.SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                with self.assertRaisesRegex(OrcaRuntimeError, expected):
                    self.settle(harness, done=done)

                self.assert_no_lifecycle_mutation(harness, recorder)

    # ---- 3. replay still issues zero additional lifecycle commands ---------------

    def test_replay_adds_no_further_lifecycle_command(self) -> None:
        """STEP 1b did not weaken STEP 0: re-entry is still a pure replay."""
        recorder = RecordingExec(results=self.SETTLED)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        first = self.settle(harness)
        after_first = list(recorder.commands)

        second = self.settle(harness)

        self.assertEqual(asdict(second), asdict(first))
        self.assertEqual(recorder.commands, after_first)
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])
        self.assertEqual(harness._ledger["ctx_1"]["replays"], 1)

    def test_replay_after_a_refused_settlement_never_starts_a_mutation(self) -> None:
        """The refused dispatch is recovered explicitly, never re-driven into STEP 2."""
        recorder = RecordingExec(results=self.NOT_SETTLED)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        with self.assertRaisesRegex(OrcaRuntimeError, "is not settled"):
            self.settle(harness)

        with self.assertRaisesRegex(OrcaRuntimeError, "in progress|crashed"):
            self.settle(harness)

        self.assert_no_lifecycle_mutation(harness, recorder)
        self.assertEqual(harness._ledger["ctx_1"]["state"], "in_progress")

    # ---- 4. the guarantee means the same thing on both execution paths -----------

    def test_both_execution_paths_share_the_ordering_guarantee(self) -> None:
        for supervised in (True, False):
            with self.subTest(supervised=supervised, settled=False):
                recorder = RecordingExec(results=self.NOT_SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                with self.assertRaisesRegex(OrcaRuntimeError, "is not settled"):
                    self.settle(harness, supervised=supervised)

                self.assert_no_lifecycle_mutation(harness, recorder)
                # the unsupervised branch's terminal-exit confirmation is part of
                # STEP 2 as well, so it must not run either
                self.assertNotIn("wait", recorder.verbs)
                self.assertEqual(harness._ledger["ctx_1"]["state"], "in_progress")

            with self.subTest(supervised=supervised, settled=True):
                recorder = RecordingExec(results=self.SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                attempt = self.settle(harness, supervised=supervised)

                # same axis (a) on both paths; the paths differ only in whether a
                # supervised worker resource exists to mutate at all
                self.assertEqual(attempt.settlement, "completed")
                self.assertEqual(attempt.dispatch_status, "completed")
                self.assertEqual(
                    harness.lifecycle_commands("ctx_1"),
                    ["worker-release"] if supervised else [],
                )
                probe = "worker-show" if supervised else "dispatch-show"
                self.assertLess(
                    recorder.verbs.index(probe), recorder.verbs.index("task-list")
                )
                self.assertLess(
                    recorder.verbs.index("task-list"), recorder.verbs.index("check")
                )


    # ---- 5. gaps found while re-testing the human-review correction -------------

    def test_a_settled_dispatch_mutates_once_for_every_lifecycle_intent(self) -> None:
        """Requirement 1 holds for retain/reuse too, not only for release.

        The not-settled direction is already swept over LIFECYCLE_INTENTS; the
        settled direction was pinned for `release` alone, so a regression that let
        STEP 1b run after the mutation on the retain branch could have slipped
        through. The verb mapping matters here: `retain` and `release` each have a
        command, and `reuse` deliberately has none -- ownership transfers on the next
        worker start instead -- so for reuse "mutates once" reads as "mutates zero
        times, and the read-only order still holds".
        """
        for lifecycle in sorted(orca_runtime_harness.LIFECYCLE_INTENTS):
            with self.subTest(lifecycle=lifecycle):
                recorder = RecordingExec(results=self.SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                attempt = self.settle(harness, lifecycle=lifecycle)

                expected = orca_runtime_harness.LIFECYCLE_TO_COMMAND.get(lifecycle)
                self.assertEqual(attempt.settlement, "completed")
                verbs = recorder.verbs
                self.assertLess(verbs.index("worker-show"), verbs.index("task-list"))
                if expected is None:
                    self.assertEqual(lifecycle, "reuse")
                    self.assertEqual(harness.lifecycle_commands("ctx_1"), [])
                    self.assertLess(verbs.index("task-list"), verbs.index("check"))
                    continue
                self.assertEqual(harness.lifecycle_commands("ctx_1"), [expected])
                self.assertLess(verbs.index("task-list"), verbs.index(expected))
                self.assertLess(verbs.index(expected), verbs.index("check"))

    def test_verify_settlement_issues_no_orca_command(self) -> None:
        """The gate is a pure judgement, exactly like account_axes.

        A verification that could itself talk to the runtime would reintroduce the
        very thing it guards -- work happening before axis (a) is proven -- so both
        the accepting and the refusing direction are pinned at zero commands.
        """
        recorder = RecordingExec(results=self.SETTLED)
        harness = self.build(recorder)
        settled = {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
        }

        proven = harness.verify_settlement(
            "ctx_1",
            task_id="task_g",
            observation=settled,
            done=DONE,
            task_status="completed",
            supervised=True,
        )

        self.assertEqual(proven, "completed")
        self.assertEqual(recorder.commands, [])

        with self.assertRaisesRegex(OrcaRuntimeError, "is not settled"):
            harness.verify_settlement(
                "ctx_1",
                task_id="task_g",
                observation={"dispatch": {"status": "dispatched"}, "worker": {}},
                done=DONE,
                task_status="dispatched",
                supervised=True,
            )

        self.assertEqual(recorder.commands, [])

    def test_the_worker_state_gate_is_scoped_to_the_supervised_path(self) -> None:
        """Requirement 4, at the one place the two paths legitimately differ.

        UNSETTLED_WORKER_STATES describes a *supervised* worker record. On the
        unsupervised path no such record was ever registered, so the same state
        string carries no authority and provenance alone decides -- while the
        supervised path must still refuse it. Pinning the asymmetry keeps a future
        edit from either widening the refusal to a path that cannot observe the
        state, or dropping it from the path that can.
        """
        observation = {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "outcome_unknown"},
        }
        harness = self.build(RecordingExec(results=self.SETTLED))

        with self.assertRaisesRegex(OrcaRuntimeError, "produced no outcome"):
            harness.verify_settlement(
                "ctx_1",
                task_id="task_g",
                observation=observation,
                done=DONE,
                task_status="completed",
                supervised=True,
            )

        self.assertEqual(
            harness.verify_settlement(
                "ctx_1",
                task_id="task_g",
                observation=observation,
                done=DONE,
                task_status="completed",
                supervised=False,
            ),
            "completed",
        )

    def test_a_refused_dispatch_is_not_re_driven_once_it_later_settles(self) -> None:
        """STEP 0 and STEP 1b compose one-way: a refusal is terminal for this path.

        The claim STEP 0 takes is deliberately irreversible, so a dispatch refused
        by STEP 1b stays refused even after the runtime rows flip to `completed`.
        That is the intended reading -- a claimed row carries no proof of how many
        mutations went out -- and it is what makes "recover it explicitly" the only
        way forward. Pinned because the alternative (quietly letting the retry
        through) would look like a bug fix and would reopen the duplicate-mutation
        hole STEP 0 exists to close.
        """
        recorder = RecordingExec(results=self.NOT_SETTLED)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        with self.assertRaisesRegex(OrcaRuntimeError, "is not settled"):
            self.settle(harness)

        # the dispatch settles for real a moment later
        recorder.results.update(self.SETTLED)

        with self.assertRaisesRegex(OrcaRuntimeError, "in progress|crashed"):
            self.settle(harness)

        self.assert_no_lifecycle_mutation(harness, recorder)
        row = harness._ledger["ctx_1"]
        self.assertEqual(row["state"], "in_progress")
        self.assertIn("is not settled", row["unsettled_reason"])

    def test_the_ordering_guarantee_is_scoped_to_the_settlement_path(self) -> None:
        """The recovery path is the sanctioned exception, and stays distinguishable.

        observe_unexpected_exit issues worker-abandon and worker-release on a
        Dispatch row that is still `dispatched` -- that is what recovering an
        unsettled dispatch *means*, and it is the path STEP 1b's error messages send
        the caller to. settle_attempt refuses the same rows outright. Pinning both
        halves side by side keeps the guarantee from being read as "no lifecycle
        command may ever touch a not-settled dispatch", which would make the
        documented recovery unreachable.
        """
        results = {
            **self.NOT_SETTLED,
            "worker-show": {
                "dispatch": {"status": "dispatched"},
                "worker": {"state": "outcome_unknown"},
                "terminalResource": {"releaseState": "released"},
            },
            "worker-abandon": {"state": "abandoned"},
            "task-list": {"tasks": [{"id": "task_g", "status": "failed"}]},
        }

        refusing = RecordingExec(results=self.NOT_SETTLED)
        harness = self.build(refusing)
        self.worker_terminal(harness)
        with self.assertRaisesRegex(OrcaRuntimeError, "is not settled"):
            self.settle(harness)
        self.assert_no_lifecycle_mutation(harness, refusing)

        recovering = RecordingExec(results=results)
        recovery_harness = self.build(recovering)

        attempt = recovery_harness.observe_unexpected_exit("worker", 1, phase="implementation")

        self.assertEqual(attempt.worker_done_count, 0)
        self.assertEqual(
            recovery_harness.lifecycle_commands("ctx_1"),
            ["worker-abandon", "worker-release"],
        )
        # and it is still never a settlement, nor a close-eligible terminal
        self.assertEqual(attempt.terminal_role, "active_worker")
        self.assertEqual(attempt.cleanup_authority, "not_authorized")

    def test_a_worker_done_without_an_outcome_is_refused_before_any_mutation(
        self,
    ) -> None:
        """The outcome field is part of axis (a), so it is read before STEP 2.

        The dispatch preamble requires `worker_done` to carry an explicit
        `succeeded`/`failed` outcome, and STEP 4 indexes payload["outcome"]. While
        STEP 1b did not look at the field, an outcome-less payload cleared the gate,
        worker-release went out, and the settlement then died on a bare
        KeyError('outcome') -- after the mutation, with no unsettled_reason recorded
        and the OrcaRuntimeError handler never firing. That is the human review's
        defect in miniature: a check that could be made read-only, made after the
        mutation instead. The refusal now happens above STEP 2, with zero commands.
        """
        cases = {
            "missing": {"taskId": "task_g", "dispatchId": "ctx_1"},
            "null": {"taskId": "task_g", "dispatchId": "ctx_1", "outcome": None},
            "not an outcome": {
                "taskId": "task_g",
                "dispatchId": "ctx_1",
                # a plausible-looking status word that is NOT one of the two the
                # contract defines; "settled" is what the worker *record* says
                "outcome": "settled",
            },
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                recorder = RecordingExec(results=self.SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)
                done = {"payload": json.dumps(payload), "body": "ok"}

                with self.assertRaisesRegex(OrcaRuntimeError, "carries outcome"):
                    self.settle(harness, done=done)

                self.assert_no_lifecycle_mutation(harness, recorder)
                # refused the same way a not-settled dispatch is: the claim stays and
                # carries the reason, so recovery finds a diagnosis, not a KeyError.
                row = harness._ledger["ctx_1"]
                self.assertEqual(row["state"], "in_progress")
                self.assertIn("carries outcome", row["unsettled_reason"])

    def test_both_expected_identities_are_required_before_any_mutation(self) -> None:
        """Axis (a) compares the message against the EXPECTED Task AND Dispatch ID.

        SKILL.md section 6 axis (a) settles a dispatch only on a `worker_done` that
        matches both expected identities. wait_for_done() filters deliveries by
        dispatchId, but verify_settlement is the single pre-mutation correctness gate
        -- and it is reachable directly, from a recovery re-drive or a caller that
        did its own delivery read -- so the gate itself must refuse a message that
        names the wrong Task, or that names no identity at all.
        """
        cases = {
            "no dispatchId": (
                {"taskId": "task_g", "outcome": "succeeded"},
                "carries no dispatchId",
            ),
            "no taskId": (
                {"dispatchId": "ctx_1", "outcome": "succeeded"},
                "carries no taskId",
            ),
            "wrong dispatchId": (
                {"taskId": "task_g", "dispatchId": "ctx_other", "outcome": "succeeded"},
                "payload dispatchId is ctx_other",
            ),
            "wrong taskId": (
                {"taskId": "task_other", "dispatchId": "ctx_1", "outcome": "succeeded"},
                "payload taskId is task_other",
            ),
        }
        for name, (payload, expected) in cases.items():
            with self.subTest(case=name):
                recorder = RecordingExec(results=self.SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)
                done = {"payload": json.dumps(payload), "body": "ok"}

                with self.assertRaisesRegex(OrcaRuntimeError, expected):
                    self.settle(harness, done=done)

                self.assert_no_lifecycle_mutation(harness, recorder)
                self.assertEqual(harness._ledger["ctx_1"]["state"], "in_progress")

    def test_a_settled_status_without_a_completion_timestamp_is_refused(self) -> None:
        """The other half of the axis (a) sentence: outcome AND completion timestamp.

        A Dispatch row that claims `completed`/`failed` but records no moment of
        completion is not the provenance the guide asks for -- it is the shape a
        partially-written or synthesised row has. Refusing it keeps "provenance
        proves the settlement" from degrading into "a status string proves it".
        """
        for status in sorted(orca_runtime_harness.SETTLED_STATUSES):
            with self.subTest(status=status):
                results = {
                    **self.SETTLED,
                    "worker-show": {
                        **self.SETTLED["worker-show"],
                        "dispatch": {"status": status},
                    },
                    "task-list": {"tasks": [{"id": "task_g", "status": status}]},
                }
                recorder = RecordingExec(results=results)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                with self.assertRaisesRegex(
                    OrcaRuntimeError, "no completion timestamp"
                ):
                    self.settle(harness)

                self.assert_no_lifecycle_mutation(harness, recorder)
                self.assertIn(
                    "no completion timestamp",
                    harness._ledger["ctx_1"]["unsettled_reason"],
                )

    def test_the_completion_timestamp_is_read_from_the_real_row_spellings(self) -> None:
        """`completed_at` is what the live runtime writes; the reader is not fussier.

        Pinned against two opposite regressions: a reader hard-coded to one camelCase
        spelling would refuse every real settled dispatch, and a reader that accepts
        an empty string would let a blank timestamp count as provenance.
        """
        row = {"status": "completed"}
        self.assertIsNone(orca_runtime_harness.completion_timestamp(row))
        self.assertIsNone(
            orca_runtime_harness.completion_timestamp({**row, "completed_at": None})
        )
        self.assertIsNone(
            orca_runtime_harness.completion_timestamp({**row, "completed_at": ""})
        )
        for key in orca_runtime_harness.COMPLETION_TIMESTAMP_KEYS:
            with self.subTest(key=key):
                self.assertEqual(
                    orca_runtime_harness.completion_timestamp(
                        {**row, key: COMPLETED_AT}
                    ),
                    COMPLETED_AT,
                )


class ReuseIntentTests(OfflineHarnessTestCase):
    """A live terminal handed to the next Dispatch must not be released or closed."""

    LIVE_WORKER_SHOW = {
        "worker-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": {"releaseState": "active"},
        }
    }

    def test_a_live_reused_terminal_is_retained_and_never_closed(self) -> None:
        recorder = RecordingExec(results=self.LIVE_WORKER_SHOW)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        attempt = harness.settle_attempt(
            "reviewer",
            1,
            "task_g",
            "ctx_1",
            DONE,
            "dlv_1",
            lifecycle="reuse",
            terminal="term_worker",
        )

        # reuse issues zero lifecycle mutations; ownership moves on the next worker start
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])
        self.assertNotIn("worker-release", recorder.verbs)
        self.assertNotIn("close", recorder.verbs)
        self.assertEqual(attempt.worker_resource, "reuse")
        self.assertEqual(attempt.lifecycle_action, "reuse:ownership-transfer-pending")
        self.assertEqual(attempt.process_liveness, "live")

    def test_an_explicit_retain_issues_retain_and_no_release(self) -> None:
        recorder = RecordingExec(results=self.LIVE_WORKER_SHOW)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        attempt = harness.settle_attempt(
            "worker",
            1,
            "task_g",
            "ctx_1",
            DONE,
            "dlv_1",
            lifecycle="retain",
            terminal="term_worker",
        )

        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-retain"])
        self.assertNotIn("close", recorder.verbs)
        self.assertEqual(attempt.worker_resource, "retain")

    def test_reuse_issues_none_of_the_lifecycle_mutation_commands(self) -> None:
        """E': the whole mutation vocabulary, not only the two verbs reuse replaced.

        lifecycle_commands() filters on LIFECYCLE_MUTATION_COMMANDS, but a reuse that
        quietly abandoned its predecessor would still leave the release/retain view
        empty while `worker-abandon` had in fact been sent. The assertion is therefore
        against the intersection of the real command log with the full set.
        """
        recorder = RecordingExec(results=self.LIVE_WORKER_SHOW)
        harness = self.build(recorder)
        self.worker_terminal(harness)

        harness.settle_attempt(
            "worker",
            1,
            "task_g",
            "ctx_1",
            DONE,
            "dlv_1",
            lifecycle="reuse",
            terminal="term_worker",
        )

        self.assertEqual(set(recorder.verbs) & LIFECYCLE_MUTATION_COMMANDS, set())
        self.assertIn("worker-abandon", LIFECYCLE_MUTATION_COMMANDS)
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])

    def test_a_reviewer_re_review_reuse_issues_no_command_either(self) -> None:
        """F: the reviewer half of a chain -- a correction re-review on one terminal.

        Two dispatches settle on the same handle, both with lifecycle="reuse", and the
        second one is the re-review. Neither may send anything, and the ownership
        record must show both dispatches in order.
        """
        recorder = RecordingExec(results=self.LIVE_WORKER_SHOW)
        harness = self.build(recorder)
        self.worker_terminal(
            harness, "term_reviewer", intended_role="phase_reviewer"
        )

        for iteration, dispatch_id in enumerate(("ctx_1", "ctx_2"), start=1):
            harness.register_terminal(
                "term_reviewer",
                role="active_worker",
                origin="self_created",
                intended_role="phase_reviewer",
                owner_dispatch_id=dispatch_id,
            )
            attempt = harness.settle_attempt(
                "reviewer",
                iteration,
                "task_g",
                dispatch_id,
                done_for(dispatch_id),
                f"dlv_{iteration}",
                lifecycle="reuse",
                terminal="term_reviewer",
            )
            with self.subTest(dispatch=dispatch_id):
                self.assertEqual(harness.lifecycle_commands(dispatch_id), [])
                self.assertEqual(
                    attempt.lifecycle_action, "reuse:ownership-transfer-pending"
                )
                self.assertEqual(attempt.worker_resource, "reuse")

        self.assertEqual(set(recorder.verbs) & LIFECYCLE_MUTATION_COMMANDS, set())
        self.assertEqual(harness.reuse_chain("term_reviewer"), ("ctx_1", "ctx_2"))

class WorkerPlacementLadderTests(OfflineHarnessTestCase):
    """Goal 4: the rung transitions of the custom-command placement ladder.

    The pinned-grammar tests above prove the ladder's vocabulary exists in the guide;
    these prove the harness actually moves between rungs the way SKILL.md section 6
    describes -- including rung 3's middle step, the wait for TUI idle, which is a
    real Orca call and not just a token in the pinned grammar.
    """

    def test_rung_three_runs_create_then_tui_idle_then_worker_start(self) -> None:
        """The whole rung, in order, through the real production methods."""
        recorder = RecordingExec()
        harness = self.build(recorder)

        handle = harness.create_fake_terminal("worker", "complete", iteration=1)
        dispatch_id, supervised = harness.start_worker("task_g", handle, "spec")

        self.assertEqual(handle, "term_created")
        self.assertEqual((dispatch_id, supervised), ("ctx_1", True))
        # 1. create -> 2. wait for tui-idle -> 3. adopt with worker-start --terminal
        self.assertEqual(recorder.verbs, ["create", "wait", "worker-start"])

        created, waited, started = recorder.commands
        self.assertEqual(created[:2], ("terminal", "create"))
        self.assertEqual(waited[:2], ("terminal", "wait"))
        self.assertEqual(waited[waited.index("--terminal") + 1], handle)
        self.assertEqual(waited[waited.index("--for") + 1], "tui-idle")
        # the guide requires --timeout-ms on every tui-idle wait
        self.assertEqual(
            waited[waited.index("--timeout-ms") + 1], str(harness.wait_timeout_ms)
        )
        self.assertEqual(started[:2], ("orchestration", "worker-start"))
        self.assertEqual(started[started.index("--terminal") + 1], handle)
        # the adoption is what makes this rung supervised, and it is recorded as such
        row = harness.ledger_terminal(handle)
        self.assertEqual(row["tui_idle"], "idle")
        self.assertEqual(row["created_by"], "supervised_adopted")
        self.assertEqual(row["owner_dispatch_id"], "ctx_1")
        # rung 3 never sends the low-level injected prompt
        self.assertNotIn("send", recorder.verbs)
        self.assertNotIn("dispatch", recorder.verbs)

    def test_the_tui_idle_wait_precedes_the_adoption_it_guards(self) -> None:
        """Order is the point: an idle observation after adoption guards nothing."""
        recorder = RecordingExec()
        harness = self.build(recorder)
        self.worker_terminal(harness, owner_dispatch_id=None)

        harness.start_worker("task_g", "term_worker", "spec")

        self.assertLess(
            recorder.verbs.index("wait"), recorder.verbs.index("worker-start")
        )

    def test_an_inconclusive_tui_idle_wait_still_adopts_rather_than_descending(
        self,
    ) -> None:
        """Rung 3 descends on agent_unconfigured only, never on a weak observation."""
        for label, recorder in (
            ("timeout", RecordingExec(results={"wait": {"wait": {"satisfied": False}}})),
            ("error", RecordingExec(errors={"wait": {"code": "tab_not_found"}})),
        ):
            with self.subTest(wait=label):
                harness = self.build(recorder)
                self.worker_terminal(harness, owner_dispatch_id=None)

                dispatch_id, supervised = harness.start_worker(
                    "task_g", "term_worker", "spec"
                )

                self.assertEqual((dispatch_id, supervised), ("ctx_1", True))
                self.assertEqual(recorder.verbs, ["wait", "worker-start"])
                self.assertEqual(
                    harness.ledger_terminal("term_worker")["tui_idle"],
                    "timeout" if label == "timeout" else "unobserved",
                )

    def test_supervised_worker_start_is_the_preferred_rung(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)
        self.worker_terminal(harness, owner_dispatch_id=None)

        dispatch_id, supervised = harness.start_worker("task_g", "term_worker", "spec")

        self.assertEqual((dispatch_id, supervised), ("ctx_1", True))
        self.assertEqual(recorder.verbs, ["wait", "worker-start"])
        row = harness.ledger_terminal("term_worker")
        self.assertEqual(row["created_by"], "supervised_adopted")
        self.assertEqual(row["owner_dispatch_id"], "ctx_1")

    def test_agent_unconfigured_descends_exactly_one_rung(self) -> None:
        recorder = RecordingExec(errors={"worker-start": {"code": "agent_unconfigured"}})
        harness = self.build(recorder)
        self.worker_terminal(harness, owner_dispatch_id=None)

        dispatch_id, supervised = harness.start_worker(
            "task_g", "term_worker", "review the diff"
        )

        self.assertEqual((dispatch_id, supervised), ("ctx_1", False))
        self.assertEqual(recorder.verbs, ["wait", "worker-start", "dispatch", "send"])
        # rule 1 of the ladder: the same call is not retried
        self.assertEqual(recorder.verbs.count("worker-start"), 1)
        # and the rung is not climbed again either
        self.assertEqual(recorder.verbs.count("wait"), 1)
        sent = recorder.commands[-1]
        prompt = sent[sent.index("--text") + 1]
        self.assertIn("taskId: task_g", prompt)
        self.assertIn("dispatchId: ctx_1", prompt)
        self.assertIn("review the diff", prompt)
        row = harness.ledger_terminal("term_worker")
        self.assertEqual(row["created_by"], "low_level_tracked")
        self.assertEqual(row["owner_dispatch_id"], "ctx_1")

    def test_any_other_worker_start_error_does_not_descend_the_ladder(self) -> None:
        recorder = RecordingExec(errors={"worker-start": {"code": "terminal_busy"}})
        harness = self.build(recorder)
        self.worker_terminal(harness, owner_dispatch_id=None)

        with self.assertRaisesRegex(OrcaRuntimeError, "worker-start failed"):
            harness.start_worker("task_g", "term_worker", "spec")

        self.assertEqual(recorder.verbs, ["wait", "worker-start"])
        self.assertNotIn("dispatch", recorder.verbs)
        self.assertNotIn("send", recorder.verbs)

    @patch.dict(environ, {SELF_HANDLE_ENV: "term_self"})
    def test_the_ladder_refuses_the_callers_own_handle_before_any_command(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)

        with self.assertRaisesRegex(OrcaRuntimeError, "own terminal"):
            harness.start_worker("task_g", "term_self", "spec")

        self.assertEqual(recorder.commands, [])
        self.assertNotIn("term_self", harness._terminals)


class TerminalRoleTransitionTests(OfflineHarnessTestCase):
    """Goal 2 edge cases around the never-close role gate."""

    def test_promotion_to_a_close_eligible_role_requires_a_settled_dispatch(self) -> None:
        harness = self.build(RecordingExec())
        self.worker_terminal(harness)

        harness.demote_or_promote_role("term_worker", "phase_worker", settled=False)
        self.assertEqual(harness.ledger_terminal("term_worker")["role"], "active_worker")

        harness.demote_or_promote_role("term_worker", "phase_worker", settled=True)
        self.assertEqual(harness.ledger_terminal("term_worker")["role"], "phase_worker")

    def test_never_close_roles_are_never_promoted_even_when_settled(self) -> None:
        # active_worker is the one never-close role with a legal upward transition.
        for role in sorted(NEVER_CLOSE_ROLES - {"active_worker"}):
            for target in sorted(CLOSE_ELIGIBLE_ROLES):
                with self.subTest(role=role, target=target):
                    harness = self.build(RecordingExec())
                    self.worker_terminal(harness, role=role, intended_role=target)

                    harness.demote_or_promote_role("term_worker", target, settled=True)

                    row = harness.ledger_terminal("term_worker")
                    self.assertEqual(row["role"], role)
                    self.assertNotEqual(
                        cleanup_authority(row["role"], row["origin"], True), "authorized"
                    )

    def test_a_role_outside_the_vocabulary_is_unknown_not_authorized(self) -> None:
        self.assertNotIn("bogus_role", TERMINAL_ROLE_CLASSES)
        self.assertEqual(cleanup_authority("bogus_role", "self_created", True), "unknown")
        self.assertFalse(close_allowed("bogus_role", "self_created", True))

    def test_register_terminal_rejects_unknown_roles_and_origins(self) -> None:
        harness = self.build(RecordingExec())

        with self.assertRaisesRegex(OrcaRuntimeError, "unknown terminal role"):
            harness.register_terminal("term_x", role="bogus_role", origin="self_created")
        with self.assertRaisesRegex(OrcaRuntimeError, "unknown terminal origin"):
            harness.register_terminal("term_x", role="phase_worker", origin="bogus")
        self.assertEqual(harness._terminals, {})

    def test_reuse_transfers_ownership_without_changing_the_recorded_role(self) -> None:
        harness = self.build(RecordingExec())
        self.worker_terminal(
            harness,
            "term_reviewer",
            role="external_or_adopted",
            origin="adopted",
            intended_role="phase_reviewer",
        )

        harness._attach_terminal("term_reviewer", "ctx_2", "supervised_adopted")

        row = harness.ledger_terminal("term_reviewer")
        self.assertEqual(row["role"], "external_or_adopted")
        self.assertEqual(row["origin"], "adopted")
        self.assertEqual(row["owner_dispatch_id"], "ctx_2")
        self.assertEqual(row["cleanup_authority"], "not_authorized")

    def test_an_unregistered_handle_reads_back_as_unknown_role(self) -> None:
        harness = self.build(RecordingExec())

        row = harness.ledger_terminal("term_ghost")

        self.assertEqual(row["role"], "unknown_role")
        self.assertEqual(row["origin"], "unknown")
        self.assertEqual(row["cleanup_authority"], "unknown")
        self.assertEqual(row["action"], "retained")
        self.assertNotIn("term_ghost", harness._terminals)


class FixtureTeardownGuardTests(OfflineHarnessTestCase):
    """The fixture reclaim path is not the policy path, and says so three times."""

    @patch.dict(environ, {SELF_HANDLE_ENV: "term_unrelated"})
    def test_teardown_refuses_a_terminal_that_is_not_the_run_owner_fixture(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)
        self.worker_terminal(harness)

        with self.assertRaisesRegex(OrcaRuntimeError, "refusing teardown"):
            harness._teardown_fixture_terminal(handle="term_worker")

        self.assertEqual(recorder.commands, [])

    @patch.dict(environ, {SELF_HANDLE_ENV: "term_owner"})
    def test_teardown_refuses_the_callers_own_terminal_first(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)
        # a row that would otherwise pass guard 2
        harness.register_terminal(
            "term_owner", role="run_owner_fixture", origin="self_created"
        )

        with self.assertRaisesRegex(OrcaRuntimeError, "own terminal"):
            harness._teardown_fixture_terminal()

        self.assertEqual(recorder.commands, [])
        self.assertEqual(harness.run_owner, "term_owner")

    @patch.dict(environ, {SELF_HANDLE_ENV: "term_unrelated"})
    def test_teardown_closes_the_fixture_and_nothing_else(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)
        harness.register_terminal(
            "term_owner", role="run_owner_fixture", origin="self_created"
        )

        receipt = harness._teardown_fixture_terminal()

        self.assertEqual(
            recorder.commands, [("terminal", "close", "--terminal", "term_owner")]
        )
        self.assertEqual(receipt["role"], "run_owner_fixture")
        self.assertEqual(receipt["selfHandleGuard"], "passed")
        self.assertIsNone(harness.run_owner)
        # guard 3's premise: the policy path would never have authorized this close
        self.assertFalse(close_allowed("run_owner_fixture", "self_created", True))

    def test_teardown_without_a_fixture_is_a_no_op(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)
        harness.run_owner = None

        self.assertEqual(
            harness._teardown_fixture_terminal(),
            {"handle": None, "selfHandleGuard": "no-fixture"},
        )
        self.assertEqual(recorder.commands, [])


class GraphFirstOrderingTests(OfflineHarnessTestCase):
    """Goal 3, deterministic half: task creation order and the absence of overrides.

    Whether Orca actually promotes a dependent on completion is a runtime property and
    stays in the opt-in scenarios G/H. What the harness itself does -- declare the
    dependency, dispatch the pre-created Task, never override readiness -- is pinned here.
    """

    def test_create_task_declares_its_dependencies(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)

        task_id = harness.create_task("reviewer iteration 1: pass", deps=("task_w",))

        self.assertEqual(task_id, "task_g")
        command = recorder.commands[-1]
        self.assertEqual(command[:2], ("orchestration", "task-create"))
        self.assertIn("--deps", command)
        self.assertEqual(command[command.index("--deps") + 1], '["task_w"]')
        self.assertNotIn("task-update", recorder.verbs)

    def test_create_task_without_dependencies_omits_the_flag(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)

        harness.create_task("worker iteration 1: complete")

        self.assertNotIn("--deps", recorder.commands[-1])

    def test_run_existing_task_dispatches_the_pre_created_task(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(recorder)
        self.worker_terminal(harness)

        attempt, handle = harness.run_existing_task(
            "reviewer", 1, "pass", "task_g", phase="implementation", terminal="term_worker"
        )

        self.assertEqual(handle, "term_worker")
        self.assertEqual(attempt.task_id, "task_g")
        self.assertEqual(attempt.finalizations, 1)
        # the Task came from the graph, not from this dispatch
        self.assertNotIn("task-create", recorder.verbs)
        # and readiness was never overridden anywhere in the flow
        self.assertNotIn("task-update", recorder.verbs)
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-release"])

    def test_run_attempt_still_creates_its_own_task_first(self) -> None:
        recorder = RecordingExec(results={"check": RecordingExec.ACCEPTED_DONE})
        harness = self.build(recorder)
        self.worker_terminal(harness)

        attempt, _ = harness.run_attempt(
            "worker", 1, "complete", phase="implementation", terminal="term_worker"
        )

        self.assertEqual(recorder.verbs.count("task-create"), 1)
        self.assertEqual(recorder.verbs[0], "task-create")
        self.assertEqual(attempt.task_id, "task_g")
        self.assertNotIn("task-update", recorder.verbs)

    def test_no_harness_path_forces_a_task_to_ready(self) -> None:
        """Structural: no call passes both "task-update" and "ready" as literals.

        One-sided on purpose -- removing task-update entirely is not a regression of
        this property. The recovery-only task-update that marks a task failed is fine.
        """
        module = ast.parse(Path(orca_runtime_harness.__file__).read_text())
        overrides = [
            node.lineno
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and {"task-update", "ready"}
            <= {
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant)
            }
        ]
        self.assertEqual(
            overrides, [], "a harness path force-readies a task instead of using deps"
        )


class SameRoleSessionReuseTests(OfflineHarnessTestCase):
    """DESIGN section 7.1 A-1: one terminal per role for a whole run of phases.

    Driven by SequentialTerminalExec, because the base recorder answers every
    `terminal create` with one pinned handle and would make "the chain kept ONE
    terminal" true even for a harness that created five. Its own `arm()` swaps the
    dispatch id, the task id and the delivered worker_done together, so every attempt
    settles a different row with a different identity -- FinalReviewFreshnessTests
    keeps its own copy untouched, it is an unmodified regression class.
    """

    PHASES = ("analysis", "plan", "design", "implementation", "test")

    # settle_attempt's own axis read. The reuse gate adds exactly one more per
    # decision, which is what the count assertion below separates out.
    WORKER_SHOWS_PER_SETTLEMENT = 1

    LIVE_TERMINAL_RESOURCE = {
        "releaseState": "not_requested",
        "ownershipState": "external",
        "retainedReason": "external_terminal",
    }

    def arm(
        self, recorder: SequentialTerminalExec, dispatch_id: str, task_id: str
    ) -> None:
        recorder.results["task-create"] = {"task": {"id": task_id}}
        recorder.results["task-list"] = {
            "tasks": [{"id": task_id, "status": "completed"}]
        }
        recorder.results["worker-start"] = {
            "dispatchId": dispatch_id,
            "effects": [
                {
                    "kind": "terminal",
                    "action": "reused",
                    "id": "term_agent",
                    "role": "agent",
                }
            ],
        }
        recorder.results["worker-show"] = {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": dict(self.LIVE_TERMINAL_RESOURCE),
        }
        recorder.results["worker-release"] = {
            "state": "released",
            "processAction": "none",
        }
        recorder.results["check"] = {
            "deliveryId": f"dlv_{dispatch_id}",
            "timedOut": False,
            "messages": [
                {
                    "id": f"msg_{dispatch_id}",
                    "type": "worker_done",
                    "payload": json.dumps(
                        {
                            "taskId": task_id,
                            "dispatchId": dispatch_id,
                            "outcome": "succeeded",
                        }
                    ),
                    "body": "ok",
                }
            ],
        }

    def chain(
        self,
        recorder: SequentialTerminalExec,
        harness: OrcaRuntimeHarness,
        role: str,
        *,
        phases: tuple[str, ...] | None = None,
        findings: tuple[tuple[str, ...], ...] = (),
        agent_command: str | None = None,
    ) -> list[RuntimeAttempt]:
        """Run one role across `phases` the way scenario K runs it.

        Which terminal an attempt gets is decided by the PRODUCTION gate --
        terminal_for_next_dispatch(), which takes its own fresh observation and runs
        the eight conditions -- never by the loop counter. That is the whole point:
        a chain that handed over the previous handle because `index > 1` would keep
        one terminal even if the gate refused every time (TEST-I1-MAJOR-1), so no
        test written against it could tell a working reuse decision from an absent
        one. Nothing here calls reuse_eligible(): the tests below observe the
        decision, they do not make it.

        `agent_command` is the command the NEXT attempt is said to need. None means
        "the one the ledger recorded for the running session", which is what a real
        same-role chain asks for; passing a different string is condition 2 being
        violated by the caller, with every other condition left satisfied.
        """
        phases = phases or self.PHASES
        mode = "pass" if role.endswith("reviewer") else "complete"
        intended_role = "phase_reviewer" if role.endswith("reviewer") else "phase_worker"
        attempts: list[RuntimeAttempt] = []
        previous: RuntimeAttempt | None = None
        for index, phase in enumerate(phases, start=1):
            task_id = f"task_{role}_{index}"
            self.arm(recorder, f"ctx_{role}_{index}", task_id)
            terminal: str | None = None
            if previous is not None:
                recorded = harness.ledger_terminal(previous.terminal)["agent_command"]
                terminal = harness.terminal_for_next_dispatch(
                    previous.terminal,
                    role=intended_role,
                    agent_command=(
                        recorded if agent_command is None else agent_command
                    ),
                    dispatch_id=previous.dispatch_id,
                )
            attempt_findings = findings[index - 1] if index <= len(findings) else ()
            # Composed once and handed to BOTH task-create and the dispatch, exactly
            # as scenario K does it: the Task spec is the agent-visible payload on
            # the supervised path, so a chain that skipped it here would be testing
            # a wiring the runtime scenario does not have.
            spec, _, _ = dispatch_context(
                role,
                index,
                mode,
                phase=phase,
                base_spec=f"{role} iteration {index}: {phase}",
                findings=attempt_findings,
                run_id=harness.run_id or "",
            )
            attempt, _ = harness.run_existing_task(
                role,
                index,
                mode,
                harness.create_task(spec),
                phase=phase,
                spec=spec,
                lifecycle="release" if index == len(phases) else "reuse",
                terminal=terminal,
                findings=attempt_findings,
            )
            attempts.append(attempt)
            previous = attempt
        return attempts

    # ---- TEST-I1-MAJOR-1: the gate is wired into the path, not just well-formed ----

    def test_the_production_path_reuses_a_terminal_without_the_test_asking(
        self,
    ) -> None:
        """Positive entry-point test: reuse is OBSERVED, never asserted into being.

        Nothing in this test calls reuse_eligible() or observe_for_reuse(). It runs
        the production sequence and reads back what happened: the second attempt ran
        on the handle the first attempt created, and `terminal create` went out once
        for the whole five-phase chain. If terminal_for_next_dispatch() stopped
        consuming the gate -- or the gate started refusing an eligible session --
        this reads five handles and five creations instead of one.
        """
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.chain(recorder, harness, "worker")

        self.assertEqual(attempts[1].terminal, attempts[0].terminal)
        self.assertFalse(attempts[1].terminal_created)
        self.assertEqual(len({attempt.terminal for attempt in attempts}), 1)
        self.assertEqual(len(recorder.created), 1)
        # The decision was taken per attempt against a fresh look, so the four reuse
        # decisions each cost exactly one extra read-only worker-show.
        self.assertEqual(
            recorder.verbs.count("worker-show"),
            self.WORKER_SHOWS_PER_SETTLEMENT * len(self.PHASES)
            + (len(self.PHASES) - 1),
        )

    def test_a_different_agent_command_makes_the_production_path_go_fresh(
        self,
    ) -> None:
        """Negative entry-point test: one violated condition, observed end to end.

        Same production sequence, same fixtures, same live receipts -- only the
        agent command the next attempt needs differs, which is condition 2 and
        condition 2 alone. The observation is again on the runtime side: every
        attempt opens its own terminal, so `terminal create` went out five times.
        A harness that decided reuse by loop position would still report one.
        """
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.chain(
            recorder, harness, "worker", agent_command="exec fake_bin/another-agent"
        )

        self.assertEqual(
            [attempt.terminal_created for attempt in attempts],
            [True] * len(self.PHASES),
        )
        self.assertEqual(
            len({attempt.terminal for attempt in attempts}), len(self.PHASES)
        )
        self.assertEqual(len(recorder.created), len(self.PHASES))

    def test_scenario_k_takes_its_terminal_from_the_gate(self) -> None:
        """Structural lock on the wiring the runtime scenario cannot show offline.

        The offline tests above prove the decision path works; this proves scenario
        K is ON that path. Re-hardcoding `terminal=` to a loop-carried variable
        would leave the two tests above green -- they drive their own sequence --
        so the one thing left to pin is that the runtime scenario asks the gate and
        that the gate reaches both halves of the decision.
        """
        module = ast.parse(Path(orca_runtime_harness.__file__).read_text())
        scopes = self._callers(module)

        self.assertIn(
            "terminal_for_next_dispatch",
            scopes["run_session_reuse_runtime_scenario"] | scopes["next_terminal"],
            "scenario K no longer asks the reuse gate which terminal to dispatch on",
        )
        self.assertLessEqual(
            {"reuse_eligible", "observe_for_reuse"},
            scopes["terminal_for_next_dispatch"],
            "the reuse decision no longer runs the gate against a fresh observation",
        )

    @staticmethod
    def _callers(module: ast.Module) -> dict[str, set[str]]:
        """function name -> the set of method/function names called inside it."""
        called: dict[str, set[str]] = defaultdict(set)

        def walk(node: ast.AST, scope: str) -> None:
            for child in ast.iter_child_nodes(node):
                child_scope = (
                    child.name
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else scope
                )
                if isinstance(child, ast.Call):
                    function = child.func
                    if isinstance(function, ast.Attribute):
                        called[scope].add(function.attr)
                    elif isinstance(function, ast.Name):
                        called[scope].add(function.id)
                walk(child, child_scope)

        walk(module, "<module>")
        return called

    def test_a_worker_chain_across_phases_creates_one_terminal(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.chain(recorder, harness, "worker")

        self.assertEqual(len(attempts), len(self.PHASES))
        self.assertEqual(
            [attempt.terminal_created for attempt in attempts],
            [True, False, False, False, False],
        )
        self.assertEqual(len({attempt.terminal for attempt in attempts}), 1)
        self.assertEqual(len(recorder.created), 1)

    def test_a_reviewer_chain_across_phases_creates_one_terminal(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.chain(recorder, harness, "reviewer")

        self.assertEqual(
            sum(1 for attempt in attempts if attempt.terminal_created), 1
        )
        self.assertEqual(len({attempt.terminal for attempt in attempts}), 1)
        self.assertEqual(len(recorder.created), 1)

    def test_worker_and_reviewer_chains_never_share_a_handle(self) -> None:
        """C: a role swap is never a reuse, so the two chains cannot intersect."""
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        worker_attempts = self.chain(recorder, harness, "worker")
        reviewer_attempts = self.chain(recorder, harness, "reviewer")

        worker_handles = {attempt.terminal for attempt in worker_attempts}
        reviewer_handles = {attempt.terminal for attempt in reviewer_attempts}
        self.assertTrue(worker_handles.isdisjoint(reviewer_handles))
        # The same answer read off the ledger rather than off the attempts.
        self.assertEqual(
            set(harness.handles_with_intended_role("phase_worker")), worker_handles
        )
        self.assertEqual(
            set(harness.handles_with_intended_role("phase_reviewer")),
            reviewer_handles,
        )
        self.assertEqual(len(recorder.created), 2)

    def test_every_attempt_in_a_chain_carries_new_task_and_dispatch_identity(
        self,
    ) -> None:
        """D + I-b: the session persists, the identity does not."""
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.chain(recorder, harness, "worker")

        self.assertEqual(
            len({attempt.task_id for attempt in attempts}), len(attempts)
        )
        self.assertEqual(
            len({attempt.dispatch_id for attempt in attempts}), len(attempts)
        )
        for previous, current in zip(attempts, attempts[1:]):
            with self.subTest(dispatch=current.dispatch_id):
                self.assertNotEqual(previous.task_id, current.task_id)
                self.assertNotEqual(previous.dispatch_id, current.dispatch_id)
                # ... on the one terminal both of them ran on.
                self.assertEqual(previous.terminal, current.terminal)

    def test_a_dispatch_in_lifecycle_recovery_forces_a_fresh_terminal(self) -> None:
        """G: recovery is condition 8, and an ineligible handle is not reused."""
        recorder = SequentialTerminalExec(
            results={
                "worker-show": {
                    "dispatch": {"status": "dispatched"},
                    "worker": {"state": "outcome_unknown"},
                    "terminalResource": {"releaseState": "released"},
                },
                "worker-abandon": {"state": "abandoned"},
                "worker-release": {"state": "released", "processAction": "none"},
                "task-list": {"tasks": [{"id": "task_g", "status": "failed"}]},
            }
        )
        harness = self.build(recorder)

        crashed = harness.observe_unexpected_exit("worker", 1, phase="implementation")
        crashed_handle = crashed.terminal

        self.assertEqual(crashed.outcome, "unknown")
        self.assertEqual(
            harness.lifecycle_recovery_state(crashed.dispatch_id),
            "previous_attempt_in_recovery",
        )
        eligible, reasons = harness.reuse_eligible(
            crashed_handle,
            role="phase_worker",
            agent_command=harness.ledger_terminal(crashed_handle)["agent_command"],
            dispatch_id=crashed.dispatch_id,
            observation=ReuseObservation(
                observed_at_dispatch=crashed.dispatch_id,
                handle=crashed_handle,
                worker_state="settled",
                release_state="not_requested",
                ownership_state="external",
            ),
        )
        self.assertFalse(eligible)
        self.assertIn("previous_attempt_in_recovery", reasons)

        # ... so the next attempt is given no terminal= and creates its own.
        self.arm(recorder, "ctx_worker_2", "task_worker_2")
        recovered, recovered_handle = harness.run_existing_task(
            "worker",
            2,
            "complete",
            harness.create_task("worker iteration 2: complete"),
            phase="implementation",
            lifecycle="release",
        )

        self.assertTrue(recovered.terminal_created)
        self.assertNotEqual(recovered_handle, crashed_handle)

    def test_a_self_created_reuse_chain_keeps_its_phase_role(self) -> None:
        """PLAN MINOR 1 / D-1: a reused terminal is never external_or_adopted.

        _attach_terminal demotes a known handle to active_worker while the new
        dispatch is in flight -- that is the STEP 4-0 "no close before settle" rule --
        and settle_attempt performs the one allowed promotion back. What must never
        happen is the adoption branch, which would relabel the coordinator's own
        terminal as somebody else's and strip its cleanup authority for good.
        """
        recorder = RecordingExec(
            results={
                "worker-show": {
                    "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
                    "worker": {"state": "settled"},
                    "terminalResource": dict(self.LIVE_TERMINAL_RESOURCE),
                },
                "worker-release": {"state": "released", "processAction": "none"},
            }
        )
        harness = self.build(recorder)
        harness.register_terminal(
            "term_worker",
            role="phase_worker",
            origin="self_created",
            intended_role="phase_worker",
            owner_dispatch_id="ctx_1",
            agent_command="exec fake_bin/codex --role worker",
        )

        harness._attach_terminal("term_worker", "ctx_2", "supervised_adopted")

        row = harness.ledger_terminal("term_worker")
        self.assertNotEqual(row["role"], "external_or_adopted")
        self.assertEqual(row["role"], "active_worker")
        self.assertEqual(row["origin"], "self_created")
        self.assertEqual(row["owner_dispatch_ids"], ["ctx_1", "ctx_2"])

        harness.settle_attempt(
            "worker",
            2,
            "task_g",
            "ctx_2",
            done_for("ctx_2"),
            "dlv_2",
            lifecycle="release",
            terminal="term_worker",
        )

        settled = harness.ledger_terminal("term_worker")
        self.assertEqual(settled["role"], "phase_worker")
        self.assertEqual(settled["cleanup_authority"], "authorized")

    def test_reuse_chain_preserves_session_and_refreshes_identity_and_boundary(
        self,
    ) -> None:
        """Test N (DESIGN section 7.1.1): four properties off ONE attempt list.

        (a) the session -- here the terminal -- survives every phase boundary;
        (b) the Task/Dispatch identity is new on every attempt;
        (c) the layer-1 boundary is rebuilt per attempt and carries neither id;
        (d) the Reviewer, and only the Reviewer, is handed the eight delta-first keys.

        If W-27's wiring is removed, (c) and (d) fail at the assertion, not at import:
        that is why N is worth having next to the individual unit tests.
        """
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)
        phases = ("analysis", "plan", "design")

        worker_attempts = self.chain(recorder, harness, "worker", phases=phases)
        reviewer_attempts = self.chain(
            recorder,
            harness,
            "reviewer",
            phases=phases,
            findings=(("R1",), ("R1", "R2"), ()),
        )

        for role, attempts in (
            ("worker", worker_attempts),
            ("reviewer", reviewer_attempts),
        ):
            with self.subTest(role=role):
                # (a)
                self.assertEqual(len({attempt.terminal for attempt in attempts}), 1)
                self.assertEqual(
                    sum(1 for attempt in attempts if attempt.terminal_created), 1
                )
                # (b)
                self.assertEqual(
                    len({attempt.task_id for attempt in attempts}), len(attempts)
                )
                self.assertEqual(
                    len({attempt.dispatch_id for attempt in attempts}), len(attempts)
                )
                # (c)
                boundaries = [attempt.task_boundary for attempt in attempts]
                self.assertEqual(len(set(boundaries)), len(attempts))
                for boundary in boundaries:
                    payload = dict(boundary)
                    self.assertEqual(
                        tuple(sorted(payload)), tuple(sorted(TASK_BOUNDARY_KEYS))
                    )
                    flattened = " ".join(list(payload) + list(payload.values()))
                    self.assertNotIn("task_id", flattened)
                    self.assertNotIn("dispatch_id", flattened)
                self.assertEqual(
                    [dict(boundary)["current_iteration"] for boundary in boundaries],
                    ["1", "2", "3"],
                )

        # (c) the finding list is refreshed too, not only the iteration counter
        self.assertEqual(
            [
                dict(attempt.task_boundary)["relevant_previous_findings"]
                for attempt in reviewer_attempts
            ],
            ["R1", "R1\nR2", ""],
        )
        # (d)
        for attempt in reviewer_attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertEqual(
                    attempt.reviewer_context_keys, tuple(sorted(REVIEWER_CONTEXT_KEYS))
                )
                self.assertIn("drill_down", attempt.reviewer_context_keys)
        for attempt in worker_attempts:
            self.assertEqual(attempt.reviewer_context_keys, ())

    # ---- FINAL-I1-MAJOR-1: the boundary in the DISPATCHED INPUT, not in the log ----

    def test_the_dispatched_task_spec_carries_the_boundary_and_the_agent_echoes_it(
        self,
    ) -> None:
        """The positive half of the correction, observed at the dispatch, not after.

        Every assertion here reads one of two things: the `--spec` argument that
        actually went out on task-create (the text Orca replays into the agent's
        preamble), or the body the agent answered with, which EchoingTerminalExec
        derives from that same spec. Neither is a RuntimeAttempt field. The previous
        wiring built the boundary AFTER settle_attempt and stored it on the attempt,
        which left both of these empty while every attempt-level assertion still
        passed -- that is the gap this test closes.
        """
        recorder = EchoingTerminalExec()
        harness = self.build(recorder)
        phases = ("analysis", "plan", "design")

        attempts = self.chain(recorder, harness, "worker", phases=phases)

        # The session really is reused, so "the second attempt" is a second Task on a
        # terminal that was never restarted -- the case the boundary exists for.
        self.assertEqual(len({attempt.terminal for attempt in attempts}), 1)
        self.assertEqual([a.terminal_created for a in attempts], [True, False, False])

        for index, attempt in enumerate(attempts, start=1):
            with self.subTest(iteration=index):
                spec = recorder.specs[attempt.task_id]
                self.assertIn(TASK_BOUNDARY_SPEC_HEADER, spec)
                dispatched = parse_task_boundary(spec)
                # All five keys, refreshed for THIS attempt, in the text that was sent.
                self.assertEqual(
                    tuple(sorted(dispatched)), tuple(sorted(TASK_BOUNDARY_KEYS))
                )
                self.assertEqual(dispatched["current_iteration"], str(index))
                self.assertEqual(dispatched["current_role"], "worker")
                # The instrumentation field records what was dispatched; it is not a
                # second, independently built payload that could drift from it.
                self.assertEqual(dispatched, dict(attempt.task_boundary))
                # The agent's own receipt: proof of arrival, not of sending.
                self.assertIn(
                    f"{BOUNDARY_RECEIPT_PREFIX}current_iteration: {index}", attempt.body
                )
                self.assertIn(
                    f"{BOUNDARY_RECEIPT_PREFIX}artifact_contract: "
                    + dispatched["artifact_contract"],
                    attempt.body,
                )

    def test_only_the_reviewer_spec_carries_the_delta_first_context(self) -> None:
        """(d) of test N, moved onto the dispatched text.

        The eight keys and the drill-down mandate have to be IN the Reviewer's Task
        spec -- a mandate the reviewer never reads restricts nothing -- and they have
        to be absent from the Worker's.
        """
        recorder = EchoingTerminalExec()
        harness = self.build(recorder)
        phases = ("analysis", "plan")

        reviewer_attempts = self.chain(
            recorder, harness, "reviewer", phases=phases, findings=(("R1",), ())
        )
        worker_attempts = self.chain(recorder, harness, "worker", phases=phases)

        for attempt in reviewer_attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                spec = recorder.specs[attempt.task_id]
                self.assertIn(REVIEWER_CONTEXT_SPEC_HEADER, spec)
                for key in REVIEWER_CONTEXT_KEYS:
                    self.assertIn(f"{key}:", spec)
                self.assertIn(REVIEWER_DRILL_DOWN_MANDATE, spec)
                self.assertIn(
                    f"{BOUNDARY_RECEIPT_PREFIX}reviewer_context_keys", attempt.body
                )
        for attempt in worker_attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                spec = recorder.specs[attempt.task_id]
                self.assertNotIn(REVIEWER_CONTEXT_SPEC_HEADER, spec)
                self.assertNotIn(REVIEWER_DRILL_DOWN_MANDATE, spec)
                self.assertNotIn("reviewer_context_keys", attempt.body)

    def test_no_dispatched_spec_carries_a_previous_identity_or_a_carried_instruction(
        self,
    ) -> None:
        """TASK_BOUNDARY_NEVER_CARRIED, proved at the string level on the real input.

        The three forbidden values are previous_task_id, previous_dispatch_id and
        unfinished_instruction. The first two are checked against the ids of every
        OTHER attempt in the same reused session -- the only place a stale id could
        realistically come from -- and then against the id prefixes outright, because
        `task_`/`ctx_` cannot appear in a spec that was assembled before either id
        existed. The third is checked as the "carry on where you left off" phrasing
        SKILL.md section 9 forbids, which is the form an unfinished instruction takes.
        """
        recorder = EchoingTerminalExec()
        harness = self.build(recorder)
        phases = ("analysis", "plan", "design")

        attempts = self.chain(
            recorder,
            harness,
            "reviewer",
            phases=phases,
            findings=(("R1",), ("R1", "R2"), ()),
        )

        identities = {attempt.task_id for attempt in attempts} | {
            attempt.dispatch_id for attempt in attempts
        }
        self.assertEqual(len(identities), 2 * len(attempts))  # all six are distinct
        for attempt in attempts:
            spec = recorder.specs[attempt.task_id]
            with self.subTest(dispatch=attempt.dispatch_id):
                # previous_task_id / previous_dispatch_id: not this attempt's either.
                for identity in identities:
                    self.assertNotIn(identity, spec)
                    self.assertNotIn(identity, attempt.body)
                for prefix in ("task_", "ctx_", "dcap_"):
                    self.assertNotIn(prefix, spec)
                # unfinished_instruction, in the shapes section 9 names.
                for carried in (
                    "continue",
                    "where you left off",
                    "still open",
                    "unfinished",
                    "remaining from",
                    "as before",
                ):
                    self.assertNotIn(carried, spec.lower())

    def test_the_low_level_fallback_prompt_carries_the_same_boundary(self) -> None:
        """The other agent-visible channel: `terminal send`, not the Task spec.

        On the supervised path Orca replays the Task spec into the preamble; on rung
        4 the harness writes the prompt itself. Both have to carry the boundary, and
        they have to carry the SAME one -- a fallback that dropped it would leave an
        unconfigured agent working without a boundary and nothing would say so.
        """
        recorder = EchoingTerminalExec(
            errors={"worker-start": {"code": "agent_unconfigured"}}
        )
        harness = self.build(recorder)
        # The fallback takes its dispatch id from the `dispatch` verb, which the base
        # recorder pins to ctx_1, so the delivery has to be armed with that same id.
        self.arm(recorder, "ctx_1", "task_g")

        attempt, _ = harness.run_attempt("worker", 1, "complete", phase="implementation")

        self.assertEqual(len(recorder.sent), 1)
        prompt = recorder.sent[0]
        self.assertIn(TASK_BOUNDARY_SPEC_HEADER, prompt)
        self.assertEqual(
            parse_task_boundary(prompt), parse_task_boundary(recorder.specs["task_g"])
        )
        self.assertEqual(parse_task_boundary(prompt), dict(attempt.task_boundary))

    def test_rendering_a_spec_twice_produces_the_same_dispatched_text(self) -> None:
        """run_attempt renders once for task-create and hands the result back in.

        run_existing_task renders again -- it has to, because a caller that created
        the Task itself passes only its own text -- so the two renders have to agree
        exactly. If they did not, task-create and the dispatch prompt would carry
        different boundaries, and the Reviewer's original_objective would quote a
        whole rendered block back into itself.
        """
        once, boundary, context = dispatch_context(
            "reviewer", 2, "pass", phase="design", findings=("R1",), run_id="run_x"
        )
        twice, boundary_again, context_again = dispatch_context(
            "reviewer",
            2,
            "pass",
            phase="design",
            base_spec=once,
            findings=("R1",),
            run_id="run_x",
        )

        self.assertEqual(once, twice)
        self.assertEqual(boundary, boundary_again)
        self.assertIsNotNone(context)
        self.assertEqual(context, context_again)
        self.assertNotIn(
            TASK_BOUNDARY_SPEC_HEADER, str(context_again["original_objective"])
        )

    def test_the_boundary_is_rendered_before_the_task_and_the_dispatch_exist(
        self,
    ) -> None:
        """Ordering, which is the defect itself: rendered first, or not at all.

        FINAL-I1-MAJOR-1 was an ordering bug -- the builders ran after start_worker,
        wait_for_done and settle_attempt had all returned, so nothing they produced
        could possibly have been dispatched. Reading the command log is the direct
        way to pin the order: the spec has to be complete by the time task-create
        goes out, which is before any dispatch verb runs.
        """
        recorder = EchoingTerminalExec()
        harness = self.build(recorder)
        self.arm(recorder, "ctx_worker_1", "task_worker_1")

        harness.run_attempt("worker", 1, "complete", phase="implementation")

        verbs = recorder.verbs
        self.assertLess(verbs.index("task-create"), verbs.index("worker-start"))
        self.assertIn(TASK_BOUNDARY_SPEC_HEADER, recorder.specs["task_worker_1"])
        # And the ordering is structural, not incidental: dispatch_context is a pure
        # function of the attempt's own arguments, so it cannot read a dispatch that
        # does not exist yet.
        parameters = inspect.signature(dispatch_context).parameters
        self.assertNotIn("task_id", parameters)
        self.assertNotIn("dispatch_id", parameters)


class ReuseEligibilityTests(OfflineHarnessTestCase):
    """DESIGN section 7.1 A-2: the eight conditions, one at a time.

    eligible_fixture() satisfies all eight. Every negative below breaks exactly ONE
    thing and binds to the name that must appear -- which is only a meaningful
    assertion because reuse_eligible() never short-circuits, so an unrelated
    condition silently failing would show up here as an extra name rather than
    hiding behind an early return.
    """

    HANDLE = "term_worker"
    DISPATCH = "ctx_1"
    ROLE = "phase_worker"
    AGENT_COMMAND = "exec fake_bin/codex --role worker --iteration 1"

    def eligible_fixture(
        self, **row_overrides: Any
    ) -> tuple[OrcaRuntimeHarness, RecordingExec, ReuseObservation]:
        recorder = RecordingExec()
        harness = self.build(recorder)
        row: dict[str, Any] = {
            "role": "phase_worker",
            "origin": "self_created",
            "intended_role": "phase_worker",
            "owner_dispatch_id": self.DISPATCH,
            "agent_command": self.AGENT_COMMAND,
        }
        row.update(row_overrides)
        harness.register_terminal(self.HANDLE, **row)
        harness.record_terminal_effect(self.HANDLE, "reused")
        harness._ledger[self.DISPATCH] = {
            "dispatch_id": self.DISPATCH,
            "task_id": "task_g",
            "handle": self.HANDLE,
            "role": "worker",
            "iteration": 1,
            "state": "finalized",
            "replays": 0,
            "attempt": None,
        }
        observation = ReuseObservation(
            observed_at_dispatch=self.DISPATCH,
            handle=self.HANDLE,
            worker_state="settled",
            release_state="not_requested",
            ownership_state="external",
            retained_reason="external_terminal",
        )
        return harness, recorder, observation

    def gate(
        self,
        harness: OrcaRuntimeHarness,
        observation: Any,
        *,
        role: str | None = None,
        agent_command: str | None = None,
    ) -> tuple[bool, tuple[str, ...]]:
        return harness.reuse_eligible(
            self.HANDLE,
            role=self.ROLE if role is None else role,
            agent_command=(
                self.AGENT_COMMAND if agent_command is None else agent_command
            ),
            dispatch_id=self.DISPATCH,
            observation=observation,
        )

    # ---- condition 1: same role --------------------------------------------------

    def test_condition_1_matching_role_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        self.assertEqual(harness.ledger_terminal(self.HANDLE)["intended_role"], self.ROLE)
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_1_role_mismatch_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()

        eligible, reasons = self.gate(harness, observation, role="phase_reviewer")

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("role_mismatch",))

    # ---- condition 2: same agent command -----------------------------------------

    def test_condition_2_matching_agent_command_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        self.assertEqual(
            harness.ledger_terminal(self.HANDLE)["agent_command"], self.AGENT_COMMAND
        )
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_2_agent_command_mismatch_is_refused(self) -> None:
        """F-11's test H, absorbed here: a different agent is a different session."""
        harness, _, observation = self.eligible_fixture()

        eligible, reasons = self.gate(
            harness,
            observation,
            agent_command="exec fake_bin/codex --role reviewer --iteration 1",
        )

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("agent_command_mismatch",))

    # ---- condition 3: positively live --------------------------------------------

    def test_condition_3_a_live_process_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        self.assertIn(observation.release_state, LIVE_RELEASE_STATES)
        self.assertIn(observation.worker_state, REUSABLE_WORKER_STATES)
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_3_a_dead_release_state_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()

        eligible, reasons = self.gate(
            harness, replace(observation, release_state="released")
        )

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("release_state_not_live",))

    # ---- condition 4: previous dispatch settled AND finalized ---------------------

    def test_condition_4_a_finalized_previous_dispatch_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        self.assertEqual(harness._ledger[self.DISPATCH]["state"], "finalized")
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_4_an_unfinalized_previous_dispatch_is_refused(self) -> None:
        """One fact, one name: the absent row is not also reported as a recovery."""
        harness, _, observation = self.eligible_fixture()
        del harness._ledger[self.DISPATCH]

        eligible, reasons = self.gate(harness, observation)

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("previous_dispatch_not_finalized",))

    # ---- condition 5: ownership transferable -------------------------------------

    def test_condition_5_transferable_ownership_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        self.assertIn(observation.ownership_state, OWNERSHIP_TRANSFERABLE_STATES)
        self.assertEqual(
            harness.ledger_terminal(self.HANDLE)["terminal_effect"], "reused"
        )
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_5_ownership_held_by_another_dispatch_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture(owner_dispatch_id="ctx_0")

        eligible, reasons = self.gate(harness, observation)

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("ownership_not_held_by_this_dispatch",))

    # ---- condition 6: not explicitly retained ------------------------------------

    def test_condition_6_a_terminal_with_no_retain_request_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        self.assertIs(harness.ledger_terminal(self.HANDLE)["retain_requested"], False)
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_6_an_explicitly_retained_terminal_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()
        harness.mark_retain_requested(self.HANDLE, retain_reason="user asked")

        eligible, reasons = self.gate(harness, observation)

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("explicitly_retained",))

    # ---- condition 7: self-created, close-eligible, not the coordinator's ---------

    def test_condition_7_a_self_created_phase_terminal_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        row = harness.ledger_terminal(self.HANDLE)
        self.assertEqual(row["origin"], "self_created")
        self.assertIn(row["role"], CLOSE_ELIGIBLE_ROLES)
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_7_an_adopted_terminal_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture(
            role="external_or_adopted", origin="adopted"
        )

        eligible, reasons = self.gate(harness, observation)

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("not_self_created", "role_not_reuse_eligible"))

    # ---- condition 8: not in lifecycle recovery ----------------------------------

    def test_condition_8_a_clean_previous_dispatch_is_eligible(self) -> None:
        harness, _, observation = self.eligible_fixture()

        self.assertEqual(harness.lifecycle_recovery_state(self.DISPATCH), "")
        self.assertEqual(self.gate(harness, observation), (True, ()))

    def test_condition_8_a_dispatch_in_recovery_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()
        harness._ledger[self.DISPATCH]["state"] = "in_progress"

        eligible, reasons = self.gate(harness, observation)

        self.assertFalse(eligible)
        self.assertEqual(
            reasons, ("previous_dispatch_not_finalized", "settlement_in_progress")
        )

    # ---- the observation record itself -------------------------------------------

    def test_an_observation_taken_for_another_handle_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()

        eligible, reasons = self.gate(
            harness, replace(observation, handle="term_other")
        )

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("observation_not_for_this_dispatch",))

    # ---- fail-closed: missing vs unknown, one name each (PLAN-I2-MAJOR-1) --------

    def test_a_missing_release_state_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()

        eligible, reasons = self.gate(harness, replace(observation, release_state=""))

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("release_state_missing",))

    def test_an_unknown_release_state_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()
        self.assertNotIn("retained", LIVE_RELEASE_STATES)

        eligible, reasons = self.gate(
            harness, replace(observation, release_state="retained")
        )

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("release_state_not_live",))

    def test_a_missing_worker_state_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()

        eligible, reasons = self.gate(harness, replace(observation, worker_state=""))

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("worker_state_missing",))

    def test_an_unknown_worker_state_is_refused(self) -> None:
        harness, _, observation = self.eligible_fixture()
        self.assertNotIn("running", REUSABLE_WORKER_STATES)

        eligible, reasons = self.gate(
            harness, replace(observation, worker_state="running")
        )

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("worker_state_not_reusable",))

    # ---- purity and completeness --------------------------------------------------

    def test_reuse_eligible_issues_no_orca_command_and_writes_no_ledger_row(
        self,
    ) -> None:
        """R-6: the gate is a predicate. It reads an argument and answers."""
        harness, recorder, observation = self.eligible_fixture()
        commands_before = list(recorder.commands)
        row_before = dict(harness.ledger_terminal(self.HANDLE))
        settlement_before = dict(harness._ledger[self.DISPATCH])

        self.assertEqual(self.gate(harness, observation), (True, ()))
        self.assertEqual(self.gate(harness, replace(observation, worker_state="")), (
            False,
            ("worker_state_missing",),
        ))

        self.assertEqual(recorder.commands, commands_before)
        self.assertEqual(dict(harness.ledger_terminal(self.HANDLE)), row_before)
        self.assertEqual(dict(harness._ledger[self.DISPATCH]), settlement_before)

    def test_every_failing_condition_is_reported_not_just_the_first(self) -> None:
        """No short-circuit: two broken conditions produce two sorted names."""
        harness, _, observation = self.eligible_fixture()
        harness.mark_retain_requested(self.HANDLE, retain_reason="user asked")

        eligible, reasons = self.gate(
            harness, observation, agent_command="a different agent"
        )

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("agent_command_mismatch", "explicitly_retained"))
        self.assertEqual(list(reasons), sorted(set(reasons)))


class RetainStateTransitionTests(OfflineHarnessTestCase):
    """W-37: the retain record has exactly one writer and exactly one clearer.

    The flag is what condition 6 reads, so "who may set it" is a correctness question
    and not bookkeeping: an explicit user retain must survive until a release, and a
    reuse must be unable to invent or erase one.
    """

    RESULTS = {
        "worker-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": {"releaseState": "active"},
        },
        "worker-release": {"state": "released", "processAction": "none"},
        "worker-retain": {"state": "retained", "processAction": "none"},
    }

    def settle(
        self,
        harness: OrcaRuntimeHarness,
        dispatch_id: str,
        lifecycle: str,
        **kwargs: Any,
    ) -> RuntimeAttempt:
        return harness.settle_attempt(
            "worker",
            1,
            "task_g",
            dispatch_id,
            done_for(dispatch_id),
            f"dlv_{dispatch_id}",
            lifecycle=lifecycle,
            terminal="term_worker",
            **kwargs,
        )

    def test_a_retain_records_the_request_and_its_reason(self) -> None:
        harness = self.build(RecordingExec(results=self.RESULTS))
        self.worker_terminal(harness)

        self.settle(
            harness, "ctx_1", "retain", retain_reason="user asked to keep the tab"
        )

        row = harness.ledger_terminal("term_worker")
        self.assertIs(row["retain_requested"], True)
        self.assertEqual(row["retain_reason"], "user asked to keep the tab")
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-retain"])

    def test_a_release_clears_the_recorded_retain_request(self) -> None:
        """The guide's "worker-release clears the requested retention", as behaviour."""
        harness = self.build(RecordingExec(results=self.RESULTS))
        self.worker_terminal(harness)
        self.settle(harness, "ctx_1", "retain", retain_reason="user asked")
        self.assertIs(harness.ledger_terminal("term_worker")["retain_requested"], True)

        self.settle(harness, "ctx_2", "release")

        row = harness.ledger_terminal("term_worker")
        self.assertIs(row["retain_requested"], False)
        self.assertEqual(row["retain_reason"], "")

    def test_reuse_neither_sets_nor_clears_the_retain_request(self) -> None:
        """reuse sends no command, so it also touches neither half of the record."""
        harness = self.build(RecordingExec(results=self.RESULTS))
        self.worker_terminal(harness)

        self.settle(harness, "ctx_1", "reuse")
        self.assertIs(harness.ledger_terminal("term_worker")["retain_requested"], False)
        self.assertEqual(harness.ledger_terminal("term_worker")["retain_reason"], "")

        harness.mark_retain_requested("term_worker", retain_reason="user asked")
        before = dict(harness.ledger_terminal("term_worker"))

        self.settle(harness, "ctx_2", "reuse")

        after = harness.ledger_terminal("term_worker")
        self.assertIs(after["retain_requested"], before["retain_requested"])
        self.assertEqual(after["retain_reason"], before["retain_reason"])
        self.assertEqual(harness.lifecycle_commands("ctx_2"), [])

    def test_register_terminal_has_no_retain_parameter(self) -> None:
        """Creation may not assert a retention nobody requested (DESIGN 4.3.6)."""
        parameters = inspect.signature(OrcaRuntimeHarness.register_terminal).parameters

        self.assertNotIn("retain_requested", parameters)
        self.assertNotIn("retain_reason", parameters)
        self.assertIn("agent_command", parameters)
        # ... and the one method that may set it does take the reason.
        self.assertIn(
            "retain_reason",
            inspect.signature(OrcaRuntimeHarness.mark_retain_requested).parameters,
        )


class TerminalEffectReceiptTests(OfflineHarnessTestCase):
    """D-6 / R8-iii: cleanup authority does not read the receipt; the label does.

    The two fixtures differ only in the placement rung and the release receipt, and
    the expected matrix (DESIGN section 7.1 A-5) says `cleanup_authority` is
    `authorized` in BOTH -- that identity is R8-iii itself. The only value allowed to
    move between the two rows is the recorded `action`, and it moves entirely because
    of the release receipt's `processAction`.

    The base RecordingExec.RESULTS is deliberately left alone: its
    `terminalResource.releaseState` of "released" sends account_axes down the
    "already exited" branch, where the action is "nothing to do" and neither label is
    observable at all.
    """

    # rung 3 -- the only path this repo has ever observed (25/25 receipts)
    RUNG_3 = {
        "worker-start": {
            "dispatchId": "ctx_1",
            "effects": [
                {"kind": "terminal", "action": "reused",
                 "id": "term_worker", "role": "agent"}
            ],
        },
        "worker-release": {"state": "released", "processAction": "none"},
        "worker-retain": {"state": "retained", "processAction": "none"},
        "worker-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": {
                "releaseState": "not_requested",
                "ownershipState": "external",
                "retainedReason": "external_terminal",
            },
        },
    }
    # rung 1 -- NEVER OBSERVED. This fixture is an unobserved hypothesis (A-7).
    RUNG_1 = {
        **RUNG_3,
        "worker-start": {
            "dispatchId": "ctx_1",
            "effects": [
                {"kind": "terminal", "action": "created",
                 "id": "term_worker", "role": "agent"}
            ],
        },
        "worker-release": {"state": "released", "processAction": "killed"},
    }

    class Rung3Exec(RecordingExec):
        """RecordingExec answering with the observed rung-3 receipt shape."""

        def __init__(self, **kwargs: Any) -> None:
            results = {
                **TerminalEffectReceiptTests.RUNG_3,
                "check": RecordingExec.ACCEPTED_DONE,
                **(kwargs.pop("results", None) or {}),
            }
            super().__init__(results=results, **kwargs)

    class Rung1Exec(RecordingExec):
        """RecordingExec answering with the UNOBSERVED rung-1 hypothesis (A-7)."""

        def __init__(self, **kwargs: Any) -> None:
            results = {
                **TerminalEffectReceiptTests.RUNG_1,
                "check": RecordingExec.ACCEPTED_DONE,
                **(kwargs.pop("results", None) or {}),
            }
            super().__init__(results=results, **kwargs)

    def run_one_release(
        self, harness: OrcaRuntimeHarness
    ) -> tuple[RuntimeAttempt, str]:
        """create -> adopt -> settle with lifecycle="release", the production path."""
        handle = harness.create_fake_terminal("worker", "complete", iteration=1)
        dispatch_id, supervised = harness.start_worker("task_g", handle, "spec")
        self.assertTrue(supervised)
        done, delivery_id = harness.wait_for_done(dispatch_id)
        attempt = harness.settle_attempt(
            "worker",
            1,
            "task_g",
            dispatch_id,
            done,
            delivery_id,
            lifecycle="release",
            terminal=handle,
        )
        return attempt, handle

    def test_rung_3_receipt_keeps_authority_and_records_a_retained_action(self) -> None:
        """The observed rung. `processAction: none` proves nothing was ended."""
        harness = self.build(self.Rung3Exec())

        attempt, handle = self.run_one_release(harness)

        row = harness.ledger_terminal(handle)
        self.assertEqual(row["terminal_effect"], "reused")
        self.assertEqual(attempt.release_process_action, "none")
        self.assertEqual(attempt.terminal_state, "not_requested")
        self.assertEqual(attempt.process_liveness, "live")
        # authority does not read the effect -- identical in both rungs
        self.assertEqual(attempt.cleanup_authority, "authorized")
        self.assertEqual(row["action"], "retained (runtime kept the process)")

    def test_rung_1_receipt_keeps_authority_and_records_a_released_action(self) -> None:
        """rung 1 is an unobserved hypothesis (A-7): this repo has never seen it.

        If the real runtime ever reports a created terminal with a different release
        receipt, this one fixture is what changes -- the authority calculation above
        is untouched, which is exactly what R8-iii bought.
        """
        harness = self.build(self.Rung1Exec())

        attempt, handle = self.run_one_release(harness)

        row = harness.ledger_terminal(handle)
        self.assertEqual(row["terminal_effect"], "created")
        self.assertEqual(attempt.release_process_action, "killed")
        self.assertIn("killed", PROCESS_TERMINATING_ACTIONS)
        self.assertEqual(attempt.process_liveness, "live")
        self.assertEqual(attempt.cleanup_authority, "authorized")
        self.assertEqual(row["action"], "released by runtime")

    def test_a_missing_release_receipt_keeps_the_legacy_released_action(self) -> None:
        """The default that keeps AxisMatrixTests unmodified, pinned as behaviour."""
        harness = self.build(self.Rung3Exec())
        harness.register_terminal(
            "term_worker",
            role="phase_worker",
            origin="self_created",
            intended_role="phase_worker",
            owner_dispatch_id="ctx_1",
        )

        axes = harness.account_axes(
            "task_g",
            "ctx_1",
            "term_worker",
            supervised=True,
            observation={
                "terminalResource": dict(self.RUNG_3["worker-show"]["terminalResource"])
            },
            task_status="completed",
            lifecycle="release",
        )

        self.assertEqual(axes[2], "live")
        self.assertEqual(axes[3], "authorized")
        self.assertEqual(
            harness.ledger_terminal("term_worker")["action"], "released by runtime"
        )

    def test_settle_attempt_propagates_the_rung_3_receipt_into_the_ledger_action(
        self,
    ) -> None:
        """End to end: the label is derived from the receipt the runtime returned.

        The expected value is read back out of the recorded worker-release response
        rather than restated, so this fails if settle_attempt ever stops forwarding
        `processAction` into account_axes even though the literal above still passes.
        """
        harness = self.build(self.Rung3Exec())

        _, handle = self.run_one_release(harness)

        receipts = [
            (row.get("response") or {}).get("result", {}).get("processAction")
            for row in harness._raw
            if len(row["command"]) > 1 and row["command"][1] == "worker-release"
        ]
        self.assertEqual(receipts, ["none"])
        expected = (
            "released by runtime"
            if receipts[0] in PROCESS_TERMINATING_ACTIONS
            else "retained (runtime kept the process)"
        )
        self.assertEqual(harness.ledger_terminal(handle)["action"], expected)


class FinalReviewFreshnessTests(OfflineHarnessTestCase):
    """DESIGN section 7.3: what a Final Adversarial Review Dispatch does to terminals.

    Driven by SequentialTerminalExec, because the base recorder answers every
    `terminal create` with one pinned handle and would make "the two handles differ"
    vacuously false. No real Orca is involved.
    """

    # section 17's FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES, restated here so the test
    # fails if the runtime ever accounts a Final Review Dispatch as a reuse.
    FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES = frozenset(
        {"retain", "release", "unsupervised"}
    )

    def arm(self, recorder: SequentialTerminalExec, dispatch_id: str) -> None:
        """Point the stub at the next Dispatch id.

        RecordingExec pins one dispatch id, so without this every attempt would
        settle the SAME row and the second one would read back as a replay.
        """
        recorder.results["worker-start"] = {"dispatchId": dispatch_id}
        recorder.results["check"] = {
            "deliveryId": f"dlv_{dispatch_id}",
            "timedOut": False,
            "messages": [
                {
                    "id": f"msg_{dispatch_id}",
                    "type": "worker_done",
                    "payload": json.dumps(
                        {
                            "taskId": "task_g",
                            "dispatchId": dispatch_id,
                            "outcome": "succeeded",
                        }
                    ),
                    "body": "ok",
                }
            ],
        }

    def phase_reviewer_attempt(
        self, recorder: SequentialTerminalExec, harness: OrcaRuntimeHarness
    ) -> tuple[RuntimeAttempt, str]:
        self.arm(recorder, "ctx_phase_reviewer")
        return harness.run_attempt("reviewer", 1, "pass", phase="implementation")

    def final_review_attempts(
        self,
        recorder: SequentialTerminalExec,
        harness: OrcaRuntimeHarness,
        count: int = 2,
    ) -> list[tuple[RuntimeAttempt, str]]:
        """`count` Final Review attempts, each with NO terminal= argument.

        Passing no terminal is the whole scenario: run_existing_task then takes the
        create_fake_terminal branch and a new handle is allocated per attempt.
        """
        attempts = []
        for index in range(1, count + 1):
            self.arm(recorder, f"ctx_fr_{index}")
            attempts.append(
                harness.run_attempt(
                    "reviewer",
                    index,
                    "pass" if index == count else "fail",
                    phase=FINAL_REVIEW_PHASE,
                    findings=() if index == count else ("R1",),
                )
            )
        return attempts

    @staticmethod
    def terminal_creates(recorder: SequentialTerminalExec) -> list[tuple[str, ...]]:
        return [
            command
            for command in recorder.commands
            if command[:2] == ("terminal", "create")
        ]

    @staticmethod
    def adopted_handles(recorder: SequentialTerminalExec) -> list[str]:
        return [
            command[command.index("--terminal") + 1]
            for command in recorder.commands
            if len(command) > 1 and command[1] == "worker-start"
        ]

    def test_each_final_review_attempt_creates_a_new_terminal(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.final_review_attempts(recorder, harness)

        handles = [handle for _, handle in attempts]
        self.assertEqual(len(self.terminal_creates(recorder)), 2)
        self.assertEqual(handles, recorder.created)
        self.assertNotEqual(handles[0], handles[1])
        self.assertEqual(len(set(handles)), 2)

    def test_final_review_never_reuses_a_previous_final_review_terminal(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.final_review_attempts(recorder, harness)

        first_handle = attempts[0][1]
        adopted = self.adopted_handles(recorder)
        self.assertEqual(adopted, [handle for _, handle in attempts])
        # attempt 2 never adopts attempt 1's terminal
        self.assertEqual(adopted.count(first_handle), 1)
        self.assertNotIn(
            first_handle,
            adopted[1:],
            "a later Final Review attempt adopted the previous attempt's terminal",
        )

    def test_final_review_never_reuses_a_phase_reviewer_terminal(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        _, phase_handle = self.phase_reviewer_attempt(recorder, harness)
        attempts = self.final_review_attempts(recorder, harness)

        final_handles = {handle for _, handle in attempts}
        reviewer_handles = harness.handles_with_intended_role()
        # every one of them IS classified as a phase reviewer terminal ...
        self.assertLessEqual(final_handles | {phase_handle}, set(reviewer_handles))
        # ... and yet no Final Review attempt ran on the phase Reviewer's terminal
        self.assertTrue(final_handles.isdisjoint({phase_handle}))
        self.assertNotIn(phase_handle, self.adopted_handles(recorder)[1:])

    def test_final_review_axis_b_is_never_reuse(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.final_review_attempts(recorder, harness)

        self.assertNotIn("reuse", self.FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES)
        self.assertLessEqual(
            self.FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES, set(WORKER_RESOURCE_OUTCOMES)
        )
        for attempt, _ in attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertIn(
                    attempt.worker_resource,
                    self.FINAL_REVIEW_WORKER_RESOURCE_OUTCOMES,
                )
                self.assertNotEqual(attempt.worker_resource, "reuse")
                self.assertFalse(attempt.lifecycle_action.startswith("reuse:"))

    def test_final_review_dispatch_finalizes_exactly_once(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.final_review_attempts(recorder, harness)

        dispatch_ids = [attempt.dispatch_id for attempt, _ in attempts]
        self.assertEqual(dispatch_ids, ["ctx_fr_1", "ctx_fr_2"])
        for attempt, _ in attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertEqual(attempt.finalizations, 1)
                row = harness._ledger[attempt.dispatch_id]
                self.assertEqual(row["state"], "finalized")
                self.assertEqual(row["replays"], 0)
                # the row is single-assignment: a second finalization is refused
                with self.assertRaises(OrcaRuntimeError):
                    harness.finalize_once(attempt.dispatch_id, attempt=attempt)

    def test_final_review_task_is_created_before_its_dispatch_with_no_deps(
        self,
    ) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        self.final_review_attempts(recorder, harness)

        verbs = recorder.verbs
        self.assertEqual(verbs.count("task-create"), 2)
        self.assertEqual(verbs.count("worker-start"), 2)
        for index, verb in enumerate(verbs):
            if verb == "worker-start":
                self.assertIn("task-create", verbs[:index])
        # a single-node graph: the Final Review Task depends on nothing, and
        # readiness is never overridden
        for command in recorder.commands:
            if len(command) > 1 and command[1] == "task-create":
                self.assertNotIn("--deps", command)
        self.assertNotIn("task-update", verbs)

    def test_final_review_leaves_no_residual_terminal(self) -> None:
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        attempts = self.final_review_attempts(recorder, harness)

        for attempt, handle in attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertEqual(
                    harness.lifecycle_commands(attempt.dispatch_id),
                    ["worker-release"],
                )
                self.assertEqual(harness._ledger[attempt.dispatch_id]["state"],
                                 "finalized")
                # the worker resource is gone, so there is nothing left to close
                self.assertEqual(attempt.process_liveness, "already exited")
                self.assertEqual(
                    harness.ledger_terminal(handle)["action"], "nothing to do"
                )
        self.assertNotIn("close", recorder.verbs)
        self.assertEqual(
            harness.lifecycle_commands(), ["worker-release", "worker-release"]
        )

    def test_final_reviewer_terminal_is_classified_phase_reviewer(self) -> None:
        """R-H: the role widening, pinned directly rather than inferred."""
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        for role, expected in (
            ("reviewer", "phase_reviewer"),
            ("final-reviewer", "phase_reviewer"),
            ("worker", "phase_worker"),
        ):
            with self.subTest(role=role):
                handle = harness.create_fake_terminal(role, "pass", iteration=1)

                row = harness.ledger_terminal(handle)
                self.assertEqual(row["intended_role"], expected)
                self.assertEqual(row["role"], "active_worker")
                self.assertEqual(row["origin"], "self_created")
                self.assertIn(
                    handle,
                    harness.handles_with_intended_role(expected),
                )

    def test_final_review_terminals_differ_from_every_handle_in_a_live_reuse_chain(
        self,
    ) -> None:
        """L: freshness must hold against a CHAIN, not just against the previous attempt.

        The helpers above never pass terminal=, so every attempt they drive already
        gets its own handle; that proves nothing about a run where a phase role really
        did keep one terminal alive across dispatches. This test builds such a chain
        first -- two dispatches, one handle, the second still reusable -- and only then
        asks whether a Final Review attempt stayed out of it.
        """
        recorder = SequentialTerminalExec()
        harness = self.build(recorder)

        chain_handles: list[str] = []
        terminal: str | None = None
        for index in (1, 2):
            self.arm(recorder, f"ctx_chain_{index}")
            _, terminal = harness.run_attempt(
                "worker",
                index,
                "complete",
                phase="implementation",
                lifecycle="release" if index == 2 else "reuse",
                terminal=terminal,
            )
            chain_handles.append(terminal)

        # the chain really is one live terminal handed onward, not two
        self.assertEqual(len(set(chain_handles)), 1)
        self.assertEqual(
            harness.reuse_chain(chain_handles[0]), ("ctx_chain_1", "ctx_chain_2")
        )

        attempts = self.final_review_attempts(recorder, harness)

        final_review_handles = {handle for _, handle in attempts}
        self.assertEqual(len(final_review_handles), 2)
        self.assertTrue(final_review_handles.isdisjoint(chain_handles))
        for attempt, _ in attempts:
            with self.subTest(dispatch=attempt.dispatch_id):
                self.assertTrue(attempt.terminal_created)
                self.assertNotEqual(attempt.worker_resource, "reuse")

class SessionReuseGateTests(OfflineHarnessTestCase):
    """The eight-condition reuse gate: pure, fail-closed, and never short-circuiting.

    Only the minimum this IMPLEMENTATION owes: one fully eligible positive, the
    fail-closed negatives for a missing/stale observation, and the read-only
    observation builder. The full 45-test placement is the TEST phase's job.
    """

    def eligible_harness(
        self, recorder: RecordingExec
    ) -> tuple[OrcaRuntimeHarness, ReuseObservation]:
        """A harness whose ledger satisfies all eight conditions for term_worker."""
        harness = self.build(recorder)
        harness.register_terminal(
            "term_worker",
            role="phase_worker",
            origin="self_created",
            intended_role="phase_worker",
            owner_dispatch_id="ctx_1",
            agent_command="exec fake_bin/codex",
        )
        harness.record_terminal_effect("term_worker", "reused")
        harness._ledger["ctx_1"] = {
            "dispatch_id": "ctx_1",
            "task_id": "task_g",
            "handle": "term_worker",
            "role": "worker",
            "iteration": 1,
            "state": "finalized",
            "replays": 0,
            "attempt": self.PROBE_ATTEMPT,
        }
        observation = ReuseObservation(
            observed_at_dispatch="ctx_1",
            handle="term_worker",
            worker_state="settled",
            release_state="not_requested",
            ownership_state="external",
            retained_reason="",
        )
        return harness, observation

    PROBE_ATTEMPT = RuntimeAttempt(
        role="worker",
        iteration=1,
        task_id="task_g",
        dispatch_id="ctx_1",
        outcome="succeeded",
        task_status="completed",
        dispatch_status="completed",
        worker_state="settled",
        terminal_state="released",
        lifecycle_action="reuse",
        worker_done_count=1,
        execution_path="offline",
    )

    def gate(
        self, harness: OrcaRuntimeHarness, observation: Any
    ) -> tuple[bool, tuple[str, ...]]:
        return harness.reuse_eligible(
            "term_worker",
            role="phase_worker",
            agent_command="exec fake_bin/codex",
            dispatch_id="ctx_1",
            observation=observation,
        )

    def test_all_eight_conditions_met_is_eligible_and_issues_no_command(self) -> None:
        recorder = RecordingExec()
        harness, observation = self.eligible_harness(recorder)

        eligible, reasons = self.gate(harness, observation)

        self.assertTrue(eligible, reasons)
        self.assertEqual(reasons, ())
        # The predicate is pure: no Orca command at all, lifecycle or otherwise.
        self.assertEqual(recorder.commands, [])
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])

    # rung 3 -- the only placement this repo has ever observed (25/25 receipts).
    # The terminal is live and external, the worker is settled, and the release
    # receipt proves nothing was killed.
    RUNG_3 = {
        "worker-start": {
            "dispatchId": "ctx_1",
            "effects": [
                {
                    "kind": "terminal",
                    "action": "reused",
                    "id": "term_created",
                    "role": "agent",
                }
            ],
        },
        "worker-show": {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": {
                "releaseState": "not_requested",
                "ownershipState": "external",
                "retainedReason": "external_terminal",
            },
        },
        "worker-release": {"state": "released", "processAction": "none"},
        "worker-retain": {"state": "retained", "processAction": "none"},
        "check": RecordingExec.ACCEPTED_DONE,
    }

    def test_a_real_flow_reaches_an_eligible_reuse_decision(self) -> None:
        """The gate is reachable, not merely well-formed.

        Every value the eight conditions read is written by the production path --
        create_fake_terminal records the agent command, start_worker records the
        worker-start terminal effect and the owning dispatch, settle_attempt
        finalizes the row -- so nothing here is hand-placed in the ledger. Without
        W-20 the `agent_command` column stays "" and condition 2 refuses every
        terminal forever, which is exactly the failure this test exists to catch:
        a reuse gate that can never say yes is a reuse feature that never runs.
        """
        recorder = RecordingExec(results=self.RUNG_3)
        harness = self.build(recorder)

        handle = harness.create_fake_terminal("worker", "complete", iteration=1)
        self.assertTrue(harness.ledger_terminal(handle)["agent_command"])  # W-20
        dispatch_id, supervised = harness.start_worker(
            "task_g", handle, "worker iteration 1: complete"
        )
        self.assertTrue(supervised)
        self.assertEqual(harness.ledger_terminal(handle)["terminal_effect"], "reused")

        done, delivery_id = harness.wait_for_done(dispatch_id)
        attempt = harness.settle_attempt(
            "worker",
            1,
            "task_g",
            dispatch_id,
            done,
            delivery_id,
            lifecycle="reuse",
            terminal=handle,
        )
        self.assertEqual(attempt.lifecycle_action, "reuse:ownership-transfer-pending")
        self.assertEqual(harness.lifecycle_commands(dispatch_id), [])

        observation = harness.observe_for_reuse(
            dispatch_id=dispatch_id, handle=handle
        )
        commands_before = list(recorder.commands)
        eligible, reasons = harness.reuse_eligible(
            handle,
            role="phase_worker",
            agent_command=harness.ledger_terminal(handle)["agent_command"],
            dispatch_id=dispatch_id,
            observation=observation,
        )

        self.assertTrue(eligible, reasons)
        self.assertEqual(reasons, ())
        self.assertEqual(recorder.commands, commands_before)

    def test_a_missing_observation_is_refused_and_never_raises(self) -> None:
        """Fail-closed: the public-method sweep binds `observation` to a dict."""
        recorder = RecordingExec()
        harness, _ = self.eligible_harness(recorder)

        for wrong in ({}, None, "term_worker"):
            with self.subTest(observation=wrong):
                eligible, reasons = self.gate(harness, wrong)
                self.assertFalse(eligible)
                self.assertIn("stale_or_missing_observation", reasons)
                # "" is NOT OBSERVED, so the two liveness allowlists fail with it.
                self.assertIn("release_state_missing", reasons)
                self.assertIn("worker_state_missing", reasons)
                self.assertIn("ownership_not_transferable", reasons)

    def test_an_observation_taken_for_another_dispatch_is_refused(self) -> None:
        recorder = RecordingExec()
        harness, observation = self.eligible_harness(recorder)
        stale = replace(observation, observed_at_dispatch="ctx_0")

        eligible, reasons = self.gate(harness, stale)

        self.assertFalse(eligible)
        self.assertEqual(reasons, ("observation_not_for_this_dispatch",))

    def test_an_unrecognized_liveness_value_is_not_live(self) -> None:
        """Positive allowlists only: an unobserved value fails, it does not pass."""
        recorder = RecordingExec()
        harness, observation = self.eligible_harness(recorder)
        self.assertNotIn("released", LIVE_RELEASE_STATES)
        self.assertNotIn("running", REUSABLE_WORKER_STATES)
        self.assertNotIn("runtime_owned", OWNERSHIP_TRANSFERABLE_STATES)

        eligible, reasons = self.gate(
            harness,
            replace(
                observation,
                release_state="released",
                worker_state="running",
                ownership_state="runtime_owned",
            ),
        )

        self.assertFalse(eligible)
        self.assertEqual(
            reasons,
            (
                "ownership_not_transferable",
                "release_state_not_live",
                "worker_state_not_reusable",
            ),
        )

    def test_an_unregistered_handle_is_refused_instead_of_raising(self) -> None:
        recorder = RecordingExec()
        harness = self.build(recorder)

        eligible, reasons = harness.reuse_eligible(
            "term_unknown",
            role="phase_worker",
            agent_command="exec fake_bin/codex",
            dispatch_id="ctx_1",
            observation=ReuseObservation(
                observed_at_dispatch="ctx_1", handle="term_unknown"
            ),
        )

        self.assertFalse(eligible)
        self.assertIn("agent_command_mismatch", reasons)
        self.assertIn("terminal_effect_unrecorded", reasons)
        self.assertIn("not_self_created", reasons)
        self.assertIn("previous_dispatch_not_finalized", reasons)

    def test_lifecycle_recovery_state_names_an_unfinalized_dispatch(self) -> None:
        recorder = RecordingExec()
        harness, observation = self.eligible_harness(recorder)
        harness._ledger["ctx_1"]["state"] = "in_progress"

        self.assertEqual(
            harness.lifecycle_recovery_state("ctx_1"), "settlement_in_progress"
        )
        eligible, reasons = self.gate(harness, observation)
        self.assertFalse(eligible)
        self.assertIn("settlement_in_progress", reasons)
        self.assertIn("previous_dispatch_not_finalized", reasons)

    def test_observe_for_reuse_is_one_read_folded_into_a_record(self) -> None:
        recorder = RecordingExec(
            results={
                "worker-show": {
                    "worker": {"state": "succeeded"},
                    "terminalResource": {
                        "releaseState": "not_requested",
                        "ownershipState": "external",
                        "retainedReason": "external terminal",
                    },
                }
            }
        )
        harness = self.build(recorder)

        observation = harness.observe_for_reuse(
            dispatch_id="ctx_1", handle="term_worker"
        )

        self.assertEqual(
            observation,
            ReuseObservation(
                observed_at_dispatch="ctx_1",
                handle="term_worker",
                worker_state="succeeded",
                release_state="not_requested",
                ownership_state="external",
                retained_reason="external terminal",
            ),
        )
        self.assertEqual(recorder.verbs, ["worker-show"])
        self.assertEqual(harness.lifecycle_commands("ctx_1"), [])

    def test_observe_for_reuse_reads_a_missing_field_as_not_observed(self) -> None:
        recorder = RecordingExec(results={"worker-show": {}})
        harness = self.build(recorder)

        observation = harness.observe_for_reuse(dispatch_id="ctx_1")

        self.assertEqual(observation.worker_state, "")
        self.assertEqual(observation.release_state, "")
        self.assertEqual(observation.ownership_state, "")


class ScenarioKExec(EchoingTerminalExec):
    """Answers a whole scenario K run offline: five phases, two roles, ten dispatches.

    EchoingTerminalExec has to be re-armed per attempt by the test that drives it,
    which is exactly what a test of the SCENARIO cannot do -- re-arming from outside
    would mean the test, not run_session_reuse_runtime_scenario(), decided what each
    dispatch looked like. This recorder arms itself instead: `task-create` mints the
    next Task id, `worker-start` mints the Dispatch id for the task it was given and
    the matching worker_done, so the production function runs its own loop end to end
    and the assertions read what IT dispatched.
    """

    TERMINAL_RESOURCE = {
        "releaseState": "not_requested",
        "ownershipState": "external",
        "retainedReason": "external_terminal",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tasks = 0
        self.dispatches = 0
        self.results["run-create"] = {"run": {"id": "run_offline_k"}}
        self.results["run-show"] = {"run": {"id": "run_offline_k"}}
        self.results["worker-release"] = {"state": "released", "processAction": "none"}
        self.results["worker-show"] = {
            "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
            "worker": {"state": "settled"},
            "terminalResource": dict(self.TERMINAL_RESOURCE),
        }

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        verb = args[1] if len(args) > 1 else args[0]
        if verb == "task-create":
            self.tasks += 1
            task_id = f"task_k_{self.tasks}"
            self.results["task-create"] = {"task": {"id": task_id}}
            self.results["task-list"] = {
                "tasks": [{"id": task_id, "status": "completed"}]
            }
        elif verb == "worker-start":
            self.dispatches += 1
            dispatch_id = f"ctx_k_{self.dispatches}"
            task_id = args[args.index("--task") + 1]
            self.results["worker-start"] = {
                "dispatchId": dispatch_id,
                "effects": [
                    {
                        "kind": "terminal",
                        "action": "reused",
                        "id": "term_agent",
                        "role": "agent",
                    }
                ],
            }
            self.results["check"] = {
                "deliveryId": f"dlv_{dispatch_id}",
                "timedOut": False,
                "messages": [
                    {
                        "id": f"msg_{dispatch_id}",
                        "type": "worker_done",
                        "payload": json.dumps(
                            {
                                "taskId": task_id,
                                "dispatchId": dispatch_id,
                                "outcome": "succeeded",
                            }
                        ),
                        "body": "ok",
                    }
                ],
            }
        return super().__call__(args)


class ScenarioKDispatchedPhaseTests(OfflineHarnessTestCase):
    """PR #12 MAJOR-1: what the ten dispatched specs of scenario K actually SAY.

    The pre-existing reuse tests read RuntimeAttempt -- the coordinator's own record
    -- and the integration test checked that five boundary keys were present and that
    current_iteration moved. Neither could see the defect: keys spelled correctly,
    carrying `current_phase: complete`. These tests run the production scenario
    function itself against a self-arming stub and then read the dispatched Task
    specs back out of the command log, which is the text Orca replays into the
    agent's preamble.
    """

    ROLE_SEQUENCE = ("worker", "reviewer")

    def run_scenario(self) -> tuple[RuntimeScenarioResult, list[str]]:
        """The real scenario K, offline, plus every spec it sent to task-create."""
        recorder = ScenarioKExec()
        # preflight is a runtime capability probe (orca status + two `skills get`
        # subprocesses); it is not on the path under test, and stubbing it is what
        # lets the rest of the function run untouched.
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}), patch.object(
            OrcaRuntimeHarness, "preflight", return_value={"executable": "/opt/orca-dev"}
        ), patch.object(OrcaRuntimeHarness, "_exec_orca", recorder):
            result = run_session_reuse_runtime_scenario(self.artifact_dir)
        specs = [
            command[command.index("--spec") + 1]
            for command in recorder.commands
            if command[:2] == ("orchestration", "task-create")
        ]
        return result, specs

    def test_the_ten_dispatched_specs_carry_the_real_phase_sequence(self) -> None:
        """(worker, reviewer) x (analysis..test), read off the dispatched payload."""
        result, specs = self.run_scenario()

        self.assertEqual(len(result.attempts), 2 * len(CANONICAL_PHASES))
        self.assertEqual(len(specs), 2 * len(CANONICAL_PHASES))
        boundaries = [parse_task_boundary(spec) for spec in specs]
        self.assertEqual(
            [(boundary["current_role"], boundary["current_phase"]) for boundary in boundaries],
            [(role, phase) for phase in CANONICAL_PHASES for role in self.ROLE_SEQUENCE],
        )
        # The iteration axis still moves, and it is NOT what proves the phase axis:
        # both would be satisfied by a boundary that said current_phase=complete.
        self.assertEqual(
            [int(boundary["current_iteration"]) for boundary in boundaries],
            [index for index in range(1, len(CANONICAL_PHASES) + 1) for _ in self.ROLE_SEQUENCE],
        )
        # Each side's artifact contract is the one its phase names.
        self.assertEqual(
            [boundary["artifact_contract"] for boundary in boundaries],
            [
                phase_artifact_contract(role=role, phase=phase, run_id=result.run_id)
                for phase in CANONICAL_PHASES
                for role in self.ROLE_SEQUENCE
            ],
        )
        for boundary in boundaries:
            with self.subTest(artifact_contract=boundary["artifact_contract"]):
                self.assertTrue(
                    boundary["artifact_contract"].startswith(
                        f"artifacts/runs/{result.run_id}/"
                    )
                )

    def test_no_dispatched_spec_ever_carries_an_agent_mode_as_its_phase(self) -> None:
        """The defect itself, stated as a negative over every spec and every mode.

        Scenario K runs its worker with mode "complete" and its reviewer with mode
        "pass"; both strings are still in the specs (the base line says so), so the
        assertion is specifically about the current_phase VALUE, not about the text.
        """
        _, specs = self.run_scenario()

        for spec in specs:
            boundary = parse_task_boundary(spec)
            with self.subTest(phase=boundary["current_phase"]):
                self.assertNotIn(boundary["current_phase"], AGENT_MODES)
                self.assertIn(boundary["current_phase"], CANONICAL_PHASES)
        reviewer_specs = [spec for spec in specs if REVIEWER_CONTEXT_SPEC_HEADER in spec]
        self.assertEqual(len(reviewer_specs), len(CANONICAL_PHASES))
        for spec in reviewer_specs:
            context = parse_reviewer_context(spec)
            with self.subTest(phase=context["current_phase"]):
                self.assertNotIn(context["current_phase"], AGENT_MODES)
                self.assertEqual(
                    context["current_phase"], parse_task_boundary(spec)["current_phase"]
                )

    def test_reviewer_context_references_real_workflow_evidence(self) -> None:
        """baseline / delta / validation, checked against what the run really held.

        The delta is the WORKER's artifact for the same phase (not the reviewer's own
        output path), the baseline is exactly the artifacts whose phases already
        passed, and validation quotes the settled outcome of the worker attempt this
        review is about -- all of it available before the dispatch, none of it
        derived from the fake agent's script.
        """
        result, specs = self.run_scenario()

        reviewer_specs = [spec for spec in specs if REVIEWER_CONTEXT_SPEC_HEADER in spec]
        worker_attempts = [
            attempt for attempt in result.attempts if attempt.role == "worker"
        ]
        for index, (phase, spec) in enumerate(zip(CANONICAL_PHASES, reviewer_specs)):
            context = parse_reviewer_context(spec)
            worker_artifact = phase_artifact_contract(
                role="worker", phase=phase, run_id=result.run_id
            )
            with self.subTest(phase=phase):
                self.assertEqual(context["current_delta"], worker_artifact)
                self.assertEqual(
                    context["approved_baseline"],
                    " || ".join(
                        phase_artifact_contract(
                            role="worker", phase=earlier, run_id=result.run_id
                        )
                        for earlier in CANONICAL_PHASES[:index]
                    ),
                )
                self.assertIn(worker_artifact, context["new_claims"])
                self.assertIn(
                    f"worker outcome={worker_attempts[index].outcome}",
                    context["validation"],
                )
                self.assertEqual(context["original_objective"], SESSION_REUSE_OBJECTIVE)
                self.assertIn(REVIEWER_DRILL_DOWN_MANDATE, context["drill_down"])
        # And the worker specs carry no Reviewer block at all, before or after.
        self.assertEqual(
            [parse_reviewer_context_keys(spec) for spec in specs].count(()),
            len(CANONICAL_PHASES),
        )

    def test_the_reuse_accounting_the_correction_had_to_preserve(self) -> None:
        """Ten dispatches, two terminals, eight reuses -- unchanged by this wiring."""
        result, _ = self.run_scenario()

        self.assertEqual(result.terminal_creations, 2)
        self.assertEqual(len({attempt.terminal for attempt in result.attempts}), 2)
        self.assertEqual(
            sum(1 for attempt in result.attempts if attempt.worker_resource == "reuse"),
            8,
        )
        self.assertEqual(
            len({attempt.dispatch_id for attempt in result.attempts}),
            len(result.attempts),
        )

    def test_a_phase_is_required_and_an_agent_mode_is_not_one(self) -> None:
        """Fail-closed, both ways, at the one function every dispatch goes through."""
        with self.assertRaisesRegex(TaskContextError, "phase is required"):
            dispatch_context("worker", 1, "complete")
        for mode in ("complete", "pass", "fail", "exit"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(TaskContextError, "agent mode"):
                    dispatch_context("worker", 1, mode, phase=mode)
        with self.assertRaisesRegex(TaskContextError, "unknown phase"):
            dispatch_context("worker", 1, "complete", phase="whatever")
        # ... and the same refusal reaches the methods that dispatch.
        harness = self.build(ScenarioKExec())
        with self.assertRaises(TaskContextError):
            harness.run_attempt("worker", 1, "complete")
        with self.assertRaises(TaskContextError):
            harness.run_existing_task("worker", 1, "complete", "task_k_1")
        with self.assertRaises(TaskContextError):
            harness.observe_unexpected_exit("worker", 1)

    def test_evidence_defaults_to_the_phase_artifact_rather_than_to_nothing(self) -> None:
        """A caller with no evidence still gets a real reference, not a placeholder."""
        spec, _, context = dispatch_context(
            "reviewer", 1, "pass", phase="plan", run_id="run_x"
        )

        self.assertIsNotNone(context)
        self.assertEqual(context["current_delta"], ("artifacts/runs/run_x/PLAN.md",))
        self.assertEqual(context["approved_baseline"], ())
        self.assertEqual(
            parse_reviewer_context(spec)["current_delta"], "artifacts/runs/run_x/PLAN.md"
        )

        with_evidence, _, evidenced = dispatch_context(
            "reviewer",
            2,
            "pass",
            phase="plan",
            run_id="run_x",
            evidence=WorkflowEvidence(
                original_objective="the run's own objective",
                approved_baseline=("artifacts/runs/run_x/ANALYSIS.md",),
                current_delta=("artifacts/runs/run_x/PLAN.md",),
                new_claims=("PLAN.md section 4 rewritten",),
                validation=("worker outcome=succeeded",),
            ),
        )
        rendered = parse_reviewer_context(with_evidence)
        self.assertEqual(evidenced["original_objective"], "the run's own objective")
        self.assertEqual(
            rendered["approved_baseline"], "artifacts/runs/run_x/ANALYSIS.md"
        )
        self.assertEqual(rendered["validation"], "worker outcome=succeeded")


class RunScopedQualityProfileTests(OfflineHarnessTestCase):
    """IMPL-I1 F-001: one Quality Profile resolution per run, threaded everywhere.

    The defect this pins was not that the model was missing from the spec -- it was
    there -- but that dispatch_context re-read the profile from disk whenever its
    argument was omitted. Every harness path omitted it, so the Worker's spec and the
    spec of the Reviewer judging that Worker were built from two independent reads.
    A profile edited in between (a teammate's commit, a rebase, an editor save)
    silently gave the two roles different quality models, which is exactly the
    divergence ORIGINAL_REQUEST section 10 forbids.

    `arm` and the terminal-resource fixture are borrowed by assignment rather than by
    inheritance: subclassing SameRoleSessionReuseTests would re-run its whole suite
    under this class's name, and the reuse fixture is an unmodified regression class.
    """

    LIVE_TERMINAL_RESOURCE = SameRoleSessionReuseTests.LIVE_TERMINAL_RESOURCE
    arm = SameRoleSessionReuseTests.arm

    PROFILE_AT_RUN_START = """version: 1

quality_attributes:

  - id: DOMAIN-001
    category: business-domain
    name: Idempotent processing
    blocking: true
    applies_to:
      - implementation
"""
    PROFILE_EDITED_MID_RUN = """version: 1

quality_attributes:

  - id: LATE-001
    category: operational-risk
    name: Edited after the run started
    blocking: true
    applies_to:
      - implementation
"""

    def profile_root(self) -> Path:
        root = Path(self.temporary_directory.name) / "project"
        (root / DEFAULT_PROFILE_PATH).parent.mkdir(parents=True, exist_ok=True)
        return root

    def write_profile(self, root: Path, text: str) -> None:
        (root / DEFAULT_PROFILE_PATH).write_text(text, encoding="utf-8")

    def build_for(self, recorder: Any, root: Path) -> OrcaRuntimeHarness:
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(
                self.artifact_dir, quality_profile_root=root
            )
        harness._exec_orca = recorder
        recorder.results["run-create"] = {"run": {"id": "run_profile"}}
        return harness

    def test_a_profile_edited_mid_run_never_reaches_the_reviewer(self) -> None:
        """The regression itself: edit the file between the two dispatches."""
        root = self.profile_root()
        self.write_profile(root, self.PROFILE_AT_RUN_START)
        recorder = EchoingTerminalExec()
        harness = self.build_for(recorder, root)
        harness.start_run("run scoped quality profile")

        self.arm(recorder, "ctx_worker_1", "task_worker_1")
        harness.run_attempt("worker", 1, "complete", phase="implementation")

        # The edit lands after the Worker was dispatched and before the Reviewer is.
        # Before the fix this alone changed what the Reviewer was told.
        self.write_profile(root, self.PROFILE_EDITED_MID_RUN)

        self.arm(recorder, "ctx_reviewer_1", "task_reviewer_1")
        harness.run_attempt("reviewer", 1, "pass", phase="implementation")

        worker_gate = parse_quality_gate(recorder.specs["task_worker_1"])
        reviewer_gate = parse_quality_gate(recorder.specs["task_reviewer_1"])

        self.assertEqual(
            worker_gate,
            reviewer_gate,
            "the Reviewer must be judging against the model its Worker was given",
        )
        self.assertIn("DOMAIN-001", worker_gate["applicable_quality_attributes"])
        self.assertNotIn("LATE-001", reviewer_gate["applicable_quality_attributes"])
        self.assertEqual(reviewer_gate["blocking_quality_attributes"], "DOMAIN-001")

    def test_the_same_resolution_object_reaches_every_attempt_of_the_run(self) -> None:
        """Identity, not equality: a re-read that happened to agree would still be one."""
        root = self.profile_root()
        self.write_profile(root, self.PROFILE_AT_RUN_START)
        recorder = EchoingTerminalExec()
        harness = self.build_for(recorder, root)
        harness.start_run("run scoped quality profile")
        resolved = harness.quality_profile

        self.arm(recorder, "ctx_worker_1", "task_worker_1")
        harness.run_attempt("worker", 1, "complete", phase="implementation")
        self.write_profile(root, self.PROFILE_EDITED_MID_RUN)
        self.arm(recorder, "ctx_reviewer_1", "task_reviewer_1")
        harness.run_attempt("reviewer", 1, "pass", phase="implementation")
        self.arm(recorder, "ctx_final_1", "task_final_1")
        harness.run_attempt("reviewer", 1, "pass", phase="final_review")

        self.assertIs(harness.quality_profile, resolved)
        final_gate = parse_quality_gate(recorder.specs["task_final_1"])
        self.assertIn("DOMAIN-001", final_gate["applicable_quality_attributes"])
        self.assertNotIn("LATE-001", final_gate["applicable_quality_attributes"])

    def test_start_run_refuses_an_invalid_profile_before_anything_exists(self) -> None:
        """Fail at the run boundary, not at the first spec that needs the model."""
        root = self.profile_root()
        self.write_profile(root, "version: 1\nquality_attributes: nope\n")
        recorder = EchoingTerminalExec()
        harness = self.build_for(recorder, root)

        with self.assertRaisesRegex(OrcaRuntimeError, INVALID_PROFILE_REASON):
            harness.start_run("run with a broken profile")

        self.assertNotIn("run-create", recorder.verbs)
        self.assertNotIn("task-create", recorder.verbs)

    def test_a_directory_at_the_profile_path_stops_the_run(self) -> None:
        """F-002 at the runtime boundary: present-but-unusable is not 'no profile'."""
        root = self.profile_root()
        (root / DEFAULT_PROFILE_PATH).mkdir()
        recorder = EchoingTerminalExec()
        harness = self.build_for(recorder, root)

        with self.assertRaisesRegex(OrcaRuntimeError, INVALID_PROFILE_REASON):
            harness.start_run("run with a directory where the profile should be")

        self.assertNotIn("run-create", recorder.verbs)

    def test_no_harness_path_can_omit_the_run_resolution(self) -> None:
        """The structural half: an omitted argument is how F-001 shipped.

        Behavioural tests catch the paths they exercise. This one refuses the shape
        that made the defect possible at all -- a dispatch_context call inside the
        harness that lets the parameter default, and a resolve call anywhere other
        than the two boundaries (module import and start_run).
        """
        source = Path(orca_runtime_harness.__file__).read_text(encoding="utf-8")
        module = ast.parse(source)

        omitted = [
            node.lineno
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "dispatch_context"
            and "quality_profile" not in {keyword.arg for keyword in node.keywords}
        ]
        self.assertEqual(
            omitted,
            [],
            "a harness path lets quality_profile default instead of passing the "
            "run's own resolution",
        )

        resolves = [
            node.lineno
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "resolve_quality_profile"
        ]
        self.assertEqual(
            len(resolves),
            3,
            "resolve_quality_profile belongs at exactly three boundaries: the "
            "import-time constant, the harness constructor, and start_run",
        )


if __name__ == "__main__":
    unittest.main()
