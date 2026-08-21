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
from dataclasses import asdict
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
    NEVER_CLOSE_ROLES,
    SELF_HANDLE_ENV,
    TERMINAL_ROLE_CLASSES,
    REQUIRED_ORCA_CLI_GUIDE_SNIPPETS,
    REQUIRED_ORCHESTRATION_GUIDE_SNIPPETS,
    SUPPORTED_ORCA_APP_VERSION,
    TERMINAL_ORIGINS,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
    RuntimeAttempt,
    RuntimeScenarioResult,
    WORKER_RESOURCE_OUTCOMES,
    UnsupportedOrcaContract,
    cleanup_authority,
    close_allowed,
    validate_orca_contract,
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

        first = harness.observe_unexpected_exit("worker", 1)

        self.assertEqual(first.worker_done_count, 0)
        self.assertEqual(first.lifecycle_action, "abandon:abandoned;release:released")
        self.assertEqual(
            harness.lifecycle_commands("ctx_1"), ["worker-abandon", "worker-release"]
        )
        # no accepted worker_done, so the terminal must NOT become close-eligible
        self.assertEqual(first.terminal_role, "active_worker")
        self.assertEqual(first.cleanup_authority, "not_authorized")
        self.assertEqual(first.finalizations, 1)

        second = harness.observe_unexpected_exit("worker", 1)

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

        attempt = harness.observe_unexpected_exit("worker", 1)

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
            harness.observe_unexpected_exit("worker", 1)


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
        through. The verb mapping matters here: reuse is issued as worker-retain.
        """
        for lifecycle in sorted(orca_runtime_harness.LIFECYCLE_INTENTS):
            with self.subTest(lifecycle=lifecycle):
                recorder = RecordingExec(results=self.SETTLED)
                harness = self.build(recorder)
                self.worker_terminal(harness)

                attempt = self.settle(harness, lifecycle=lifecycle)

                expected = orca_runtime_harness.LIFECYCLE_TO_COMMAND[lifecycle]
                self.assertEqual(attempt.settlement, "completed")
                self.assertEqual(harness.lifecycle_commands("ctx_1"), [expected])
                verbs = recorder.verbs
                self.assertLess(verbs.index("worker-show"), verbs.index("task-list"))
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

        attempt = recovery_harness.observe_unexpected_exit("worker", 1)

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

        # reuse is achieved with the retain command, exactly once, and nothing closes
        self.assertEqual(harness.lifecycle_commands("ctx_1"), ["worker-retain"])
        self.assertNotIn("worker-release", recorder.verbs)
        self.assertNotIn("close", recorder.verbs)
        self.assertEqual(attempt.worker_resource, "reuse")
        self.assertEqual(attempt.lifecycle_action, "reuse:retained")
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
            "reviewer", 1, "pass", "task_g", terminal="term_worker"
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
            "worker", 1, "complete", terminal="term_worker"
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


if __name__ == "__main__":
    unittest.main()
