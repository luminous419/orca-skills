#!/usr/bin/env python3
"""OS-44 regressions: Coordinator turn gaps and stale delivery replay.

Every class here reproduces one half of what the real OS-31 orchestration run
``run_c2166e75bb02`` actually did, as recorded in that run's own
``artifacts/runs/run_c2166e75bb02/ORCHESTRATOR_LOG.md``:

* the ANALYSIS Reviewer's iteration-2 PASS settled at ``2026-09-05T07:18:53`` on
  dispatch ``ctx_330788032c5f``, and the next row -- the PLAN Worker dispatch -- is
  stamped ``2026-09-05T08:05:00``.  Between them the Coordinator ended its turn.  The
  run was neither terminal nor ``WAITING_FOR_INPUT``, no agent was active, and nothing
  existed that could wake it; it resumed only after a user typed.
* the ANALYSIS re-review delivery ``delivery_5c541e7fe1bd`` had been processed and
  never acknowledged, so the next ``check --wait`` replayed it and the freshly armed
  PLAN waiter woke on the previous phase's ANALYSIS result.  The completed-dispatch
  ledger correctly refused the duplicate settlement -- which is exactly why the defect
  was invisible in the lifecycle accounting -- but the wait cycle was still consumed.

The two are one defect with two faces, so they are tested as one file: a Coordinator
that may not end its turn early, and a delivery loop whose acknowledgement ordering
makes "processed" and "acknowledged" impossible to separate.

Nothing here replaces an existing test.  ``DuplicateSettlementTests`` in
``test_orca_runtime_contract`` already proves the finalize-once gate refuses a second
settlement; these prove the delivery never reaches that gate in the first place.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from os import environ
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import decision_gate, run_logging
from scripts.deterministic_workflow import launcher, quiescence, turn_boundary
from scripts.orca_runtime_harness import (
    ACK_MAX_ATTEMPTS,
    ACK_RECONCILED_NOT_OUTSTANDING,
    ACK_RECONCILE_UNRESOLVED,
    DELIVERY_RECOVERED_FIELD,
    DELIVERY_SETTLED_FIELD,
    DELIVERY_SETTLEMENT_CLAIMED_FIELD,
    DELIVERY_STATE_ACKNOWLEDGED,
    DELIVERY_ACK_INTENT_FIELD,
    DELIVERY_STATE_ACK_FAILED,
    DELIVERY_STATE_ACK_INTENT,
    DELIVERY_STATE_ACK_RECONCILED,
    DELIVERY_STATE_FIELD,
    DELIVERY_STATE_PROCESSED,
    OrcaRuntimeError,
    OrcaRuntimeHarness,
    RuntimeScenarioResult,
)
from scripts.test_orca_runtime_contract import (
    COMPLETED_AT,
    DECLARED_DONE_BODY,
    RecordingExec,
)


# ---------------------------------------------------------------------------------
# The recorded run's own identifiers.  Used verbatim so the reproduction is bound to
# the evidence in the ticket rather than to invented ids that merely rhyme with it.
# ---------------------------------------------------------------------------------
ANALYSIS_DISPATCH = "ctx_330788032c5f"
ANALYSIS_TASK = "task_ea02325198cb"
STALE_DELIVERY = "delivery_5c541e7fe1bd"
PLAN_DISPATCH = "ctx_c66e5ad00e3f"
PLAN_TASK = "task_50305428ba47"
PLAN_DELIVERY = "delivery_plan_first"


def worker_done_message(
    task_id: str, dispatch_id: str, *, message_id: str = "", outcome: str = "succeeded"
) -> dict[str, Any]:
    return {
        "id": message_id or f"msg_{dispatch_id}",
        "type": "worker_done",
        "payload": json.dumps(
            {"taskId": task_id, "dispatchId": dispatch_id, "outcome": outcome}
        ),
        "body": DECLARED_DONE_BODY,
    }


def delivery(delivery_id: str, *messages: dict[str, Any]) -> dict[str, Any]:
    return {"deliveryId": delivery_id, "timedOut": False, "messages": list(messages)}


class ScriptedExec:
    """A stand-in for ``_exec_orca`` that replays a scripted delivery sequence.

    Deliberately NOT a subclass of the contract suite's ``RecordingExec``: that
    recorder answers every ``check`` with one pinned delivery, which is precisely the
    shape that cannot express "the mailbox hands back a different batch each time".
    Everything except ``check`` answers with the same settled-dispatch defaults, so a
    test that is about the delivery loop does not have to model the whole runtime.

    ``ack_failures`` maps a delivery id to how many ``--ack`` attempts fail before one
    succeeds, which is how the bounded-retry and fail-closed paths are exercised
    without patching the harness itself.  ``ack_error_code`` is the code those failures
    carry; it defaults to a generic transient rejection, because that is what an
    unclassified failure IS -- a test that wants the runtime's authoritative
    "no such delivery for this Run" answer has to ask for it by name.
    """

    def __init__(
        self,
        deliveries: list[dict[str, Any]],
        *,
        ack_failures: dict[str, int] | None = None,
        ack_error_code: str = "ack_rejected",
    ) -> None:
        self.deliveries = list(deliveries)
        self.ack_failures = dict(ack_failures or {})
        self.ack_error_code = ack_error_code
        self.commands: list[tuple[str, ...]] = []
        self.acked: list[str] = []
        self.waits = 0
        # OS-44 (BUGFIX-I3-CRITICAL-1). Orca's OWN Task and Dispatch listings, which is
        # what the turn-end boundary reads. Defaulted to "both Tasks completed, no
        # worker rows" so every existing test keeps the state it was written against,
        # and overridden by `dispatch_running()` / `dispatch_finished()` for the tests
        # that need a genuinely live wait rather than a settlement-ledger stand-in.
        self.tasks: list[dict[str, Any]] = [
            {"id": ANALYSIS_TASK, "status": "completed", "deps": "[]"},
            {"id": PLAN_TASK, "status": "completed", "deps": "[]"},
        ]
        self.workers: list[dict[str, Any]] = []

    def dispatch_running(self, dispatch_id: str, task_id: str) -> None:
        """Orca's state while a Worker/Reviewer is genuinely running.

        The Task carries Orca's `dispatched` status because no result has been recorded
        for it, and the Dispatch's worker row still reports a live worker. This is the
        state the Coordinator is in INSIDE `wait_for_done()` -- before any settlement is
        claimed, and therefore before the settlement ledger knows the Dispatch exists.
        """
        self.tasks = [
            task for task in self.tasks if task["id"] != task_id
        ] + [{"id": task_id, "status": "dispatched", "deps": "[]",
              "dispatch_id": dispatch_id}]
        self.workers = [
            worker for worker in self.workers if worker["dispatchId"] != dispatch_id
        ] + [{"dispatchId": dispatch_id, "taskId": task_id,
              "dispatchStatus": "dispatched", "workerState": "ready"}]

    def dispatch_finished(self, dispatch_id: str, task_id: str) -> None:
        """The Worker has stopped but the Coordinator has recorded nothing for it.

        Orca still says the Task is `dispatched` while the Dispatch says the worker is
        done: a `worker_done` waiting to be collected. Not an active wait, and not rest.
        """
        self.dispatch_running(dispatch_id, task_id)
        self.workers[-1] = {**self.workers[-1], "dispatchStatus": "completed",
                            "workerState": "settled"}

    def pending_task(self, task_id: str, *deps: str) -> None:
        """A Task Orca holds as runnable-but-undispatched, with its dependencies."""
        self.tasks = [task for task in self.tasks if task["id"] != task_id] + [
            {"id": task_id, "status": "pending", "deps": json.dumps(list(deps))}
        ]

    @property
    def verbs(self) -> list[str]:
        return [command[1] if len(command) > 1 else command[0] for command in self.commands]

    def _flag(self, args: tuple[str, ...], name: str) -> str:
        return args[args.index(name) + 1] if name in args else ""

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        self.commands.append(args)
        verb = args[1] if len(args) > 1 else args[0]
        if verb == "check" and "--ack" in args:
            delivery_id = self._flag(args, "--ack")
            remaining = self.ack_failures.get(delivery_id, 0)
            if remaining > 0:
                self.ack_failures[delivery_id] = remaining - 1
                return 0, json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": self.ack_error_code,
                            "message": "transient"
                            if self.ack_error_code == "ack_rejected"
                            else f"Delivery {delivery_id} does not belong to this Run.",
                        },
                    }
                )
            self.acked.append(delivery_id)
            return 0, json.dumps({"ok": True, "result": {}})
        if verb == "check":
            self.waits += 1
            if not self.deliveries:
                return 0, json.dumps(
                    {"ok": True, "result": {"deliveryId": "", "timedOut": True, "messages": []}}
                )
            return 0, json.dumps({"ok": True, "result": self.deliveries.pop(0)})
        results = {
            "worker-show": {
                "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
                "worker": {"state": "settled"},
                "terminalResource": {"releaseState": "released"},
            },
            "worker-release": {"state": "released", "processAction": "none"},
            "task-list": {"tasks": list(self.tasks)},
            "worker-list": {"workers": list(self.workers)},
        }
        return 0, json.dumps({"ok": True, "result": results.get(verb, {})})


class MailboxExec(RecordingExec):
    """A full-runtime recorder whose `check --wait` is a real mailbox.

    ``ScriptedExec`` above models the delivery loop and nothing else, which is right for
    the tests that are about the loop. V2 asks a different question -- does a duplicate,
    replayed or out-of-order delivery consume a settlement, a release, an ARTIFACT, a
    DISPATCH or an iteration/budget -- and those side effects only exist on the real
    dispatch path. This recorder therefore keeps ``RecordingExec``'s whole runtime
    (terminal create, task-list, worker-start, worker-show, worker-release) and replaces
    only the mailbox, so a test can drive ``start_run`` + ``run_existing_task`` and then
    count what actually happened.
    """

    def __init__(self, deliveries: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.deliveries = list(deliveries)
        self.acked: list[str] = []
        self.waits = 0
        self.created: list[str] = []

    def arm(self, dispatch_id: str, *task_ids: str) -> None:
        """Point the runtime at the next Dispatch and the Tasks it must know about."""
        self.results["worker-start"] = {"dispatchId": dispatch_id, "state": "ready"}
        self.results["task-list"] = {
            "tasks": [{"id": task_id, "status": "completed"} for task_id in task_ids]
        }

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        verb = args[1] if len(args) > 1 else args[0]
        if args[:2] == ("terminal", "create"):
            handle = f"term_v{len(self.created) + 1}"
            self.created.append(handle)
            self.commands.append(args)
            return 0, json.dumps(
                {"ok": True, "result": {"terminal": {"handle": handle}}}
            )
        if verb == "check" and "--ack" in args:
            self.commands.append(args)
            self.acked.append(args[args.index("--ack") + 1])
            return 0, json.dumps({"ok": True, "result": {}})
        if verb == "check":
            self.commands.append(args)
            self.waits += 1
            if not self.deliveries:
                return 0, json.dumps(
                    {
                        "ok": True,
                        "result": {"deliveryId": "", "timedOut": True, "messages": []},
                    }
                )
            return 0, json.dumps({"ok": True, "result": self.deliveries.pop(0)})
        return super().__call__(args)


def _langgraph_available() -> bool:
    """Whether the durable OS-40 checkpoint store can be opened in this environment.

    The checkpoint-authority tests write and read a real checkpoint through
    ``FileCheckpointSaver``, which needs LangGraph. Everything else in this module is
    deliberately independent of it.
    """
    try:
        import langgraph  # noqa: F401
        import langgraph.checkpoint.base  # noqa: F401
    except ImportError:
        return False
    return True


class OS44TestCase(unittest.TestCase):
    """Offline harness wiring, shaped after ``OfflineHarnessTestCase``."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(self, recorder: Any, *, run_id: str = "run_c2166e75bb02") -> OrcaRuntimeHarness:
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder
        harness.run_owner, harness.run_id = "term_owner", run_id
        # start_run() is bypassed here, so its run-owner ledger row is stubbed too:
        # finish() tears that fixture terminal down and refuses an unrecorded role.
        harness.register_terminal(
            harness.run_owner, role="run_owner_fixture", origin="self_created"
        )
        harness.requested_phases = ("analysis", "plan")
        run_logging.open_decision_ledger(
            harness.run_id,
            base=self.artifact_dir,
            phases=harness.requested_phases,
            risk=harness.risk or "",
            ledger_schema_version=decision_gate.LEDGER_RECORD_SCHEMA_VERSION,
        )
        return harness

    def successor(
        self, recorder: Any, *, run_id: str = "run_c2166e75bb02"
    ) -> OrcaRuntimeHarness:
        """A genuinely new PROCESS over an existing run, bound the production way.

        OS-44 (BUGFIX-I1-G1-2). Deliberately NOT build(): this helper calls the real
        `resume_run()` entry point a restarted Coordinator uses and calls
        `restore_delivery_ledger()` nowhere, so what these tests exercise is the
        production restart path rather than a helper invoked by hand.
        """
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder
        harness.resume_run(
            run_id, run_owner="term_owner", requested_phases=("analysis", "plan")
        )
        return harness

    def refused_successor(
        self, recorder: Any, *, run_id: str = "run_c2166e75bb02"
    ) -> tuple[OrcaRuntimeHarness, OrcaRuntimeError]:
        """A successor whose restart recovery is EXPECTED to fail closed.

        Identical to `successor()` -- the same production `resume_run()` entry point --
        except that it hands back the half-bound harness alongside the refusal, so a
        test can assert what state a fail-closed recovery leaves behind rather than only
        that it raised.
        """
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder
        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.resume_run(
                run_id, run_owner="term_owner", requested_phases=("analysis", "plan")
            )
        return harness, raised.exception

    def audit(self, harness: OrcaRuntimeHarness, run_id: str = "") -> list[dict]:
        return run_logging.read_coordinator_audit(
            run_id or harness.run_id or "run_c2166e75bb02", base=self.artifact_dir
        )

    def events(self, harness: OrcaRuntimeHarness, run_id: str = "") -> list[str]:
        return [record.get("event") for record in self.audit(harness, run_id)]

    def audit_record_path(self, sequence: int, run_id: str = "run_c2166e75bb02") -> Path:
        """The published `record.json` of one audit sequence, as it sits on disk.

        Used to damage a REAL published record. A test that patches the replay
        function instead proves only that the caller catches what it is handed; the
        whole point of this family is what the reader and the fold do with a record
        that is genuinely there and genuinely unreadable.
        """
        return (
            self.artifact_dir
            / "artifacts"
            / "runs"
            / run_id
            / run_logging.COORDINATOR_AUDIT_DIRNAME
            / run_logging.coordinator_audit_sequence_key(sequence)
            / run_logging.COORDINATOR_AUDIT_RECORD_FILENAME
        )

    def sequence_of(self, event: str, run_id: str = "run_c2166e75bb02") -> int:
        """The sequence of the first published record carrying `event`."""
        for record in run_logging.read_coordinator_audit(run_id, base=self.artifact_dir):
            if record.get("event") == event:
                return int(record["sequence"])
        raise AssertionError(f"no {event!r} record was published for {run_id}")

    def settle(
        self,
        harness: OrcaRuntimeHarness,
        *,
        task_id: str,
        dispatch_id: str,
        terminal: str,
        role: str = "worker",
        iteration: int = 1,
    ):
        """create terminal row -> wait -> settle, the production ordering."""
        harness.register_terminal(
            terminal,
            role="active_worker",
            origin="self_created",
            intended_role="phase_worker",
            owner_dispatch_id=dispatch_id,
        )
        done, delivery_id = harness.wait_for_done(dispatch_id, task_id)
        return harness.settle_attempt(
            role,
            iteration,
            task_id,
            dispatch_id,
            done,
            delivery_id,
            terminal=terminal,
        )


class RecordedStallReproductionTests(OS44TestCase):
    """The 33-minute gap: a turn that ended between two phases.

    ``run_c2166e75bb02`` had settled ANALYSIS and had a runnable next node -- the PLAN
    Worker dispatch it created 46 minutes later -- with zero active dispatches.  The
    self-check must refuse that turn end, and must say why in the run's own audit.
    """

    def test_a_turn_may_not_end_with_a_runnable_next_node_and_no_active_dispatch(self) -> None:
        harness = self.build(ScriptedExec([]))

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.verify_quiescence("ACTIVE", next_node="PREPARE_WORKER")

        self.assertIn(quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED, str(raised.exception))
        records = self.audit(harness)
        self.assertEqual(
            [record["event"] for record in records],
            [run_logging.EVENT_QUIESCENCE_VIOLATION],
        )
        self.assertEqual(
            records[0]["reason_code"], quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED
        )
        self.assertEqual(records[0]["next_node"], "PREPARE_WORKER")
        self.assertEqual(records[0]["active_dispatches"], 0)

    def test_a_non_terminal_run_may_not_be_left_idle_even_with_no_next_node(self) -> None:
        """The AC is "no idle non-terminal run", not only "no unconsumed next node".

        A Coordinator that reported no next node at all would otherwise satisfy the
        first check while leaving exactly the same unwakeable run behind.
        """
        harness = self.build(ScriptedExec([]))

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.verify_quiescence("ACTIVE")

        self.assertIn(quiescence.QUIESCENCE_IDLE_NON_TERMINAL, str(raised.exception))

    def test_an_active_dispatch_wait_is_a_legitimate_place_to_end_a_turn(self) -> None:
        """The other half: the invariant must not forbid the normal case.

        OS-44 (BUGFIX-I3-CRITICAL-1). The activity is a GENUINE one: Orca holds the Task
        as `dispatched` and the Dispatch's worker row still reports a live worker, which
        is the state a Coordinator is in inside `wait_for_done()`. Nothing here claims a
        settlement. That is the whole difference from what this test used to do -- the
        settlement ledger only learns a Dispatch exists through `claim_settlement()`,
        which runs AFTER `wait_for_done()` returns, so a ledger row can never stand for
        a Worker that is currently running.
        """
        recorder = ScriptedExec([])
        recorder.dispatch_running(ANALYSIS_DISPATCH, ANALYSIS_TASK)
        harness = self.build(recorder)

        verdict = harness.verify_quiescence("ACTIVE", next_node="PREPARE_PHASE_REVIEWER")

        self.assertTrue(verdict["quiescent"])
        self.assertEqual(verdict["state"], quiescence.ACTIVE_DISPATCH_WAIT)
        self.assertEqual(harness.active_dispatch_count(), 1)
        # The settlement ledger knows nothing about it, and that is the point.
        self.assertEqual(harness.unfinalized_ledger_dispatches(), 0)
        self.assertEqual(self.events(harness), [run_logging.EVENT_QUIESCENCE_VERIFIED])

    def test_a_claimed_settlement_is_not_evidence_that_a_worker_is_running(self) -> None:
        """The PR #31 CRITICAL, stated as a test.

        `claim_settlement()` runs only after `wait_for_done()` has already returned, so
        a row in that ledger describes a Worker that has FINISHED. Reading it as an
        active dispatch is what let a turn end on a run nothing could wake, and the
        authoritative derivation must not reproduce it: Orca reports the Task completed,
        so there is no active dispatch no matter what the ledger holds.
        """
        recorder = ScriptedExec([])
        harness = self.build(recorder)
        harness.register_terminal(
            "term_a", role="active_worker", origin="self_created",
            intended_role="phase_worker", owner_dispatch_id=ANALYSIS_DISPATCH,
        )
        harness.claim_settlement(
            ANALYSIS_DISPATCH, task_id=ANALYSIS_TASK, terminal="term_a",
            role="worker", iteration=1,
        )

        self.assertEqual(harness.unfinalized_ledger_dispatches(), 1)
        self.assertEqual(harness.active_dispatch_count(), 0)
        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.verify_quiescence("ACTIVE", next_node="PREPARE_PHASE_REVIEWER")
        self.assertIn(quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED, str(raised.exception))

    def test_a_finished_worker_whose_result_was_never_collected_is_not_a_wait(self) -> None:
        """Orca's two authorities disagreeing is exactly the `run_c2166e75bb02` moment.

        The Task is still `dispatched` because the Coordinator recorded no result, while
        the Dispatch says the worker has stopped. Nothing will wake the run: the
        `worker_done` is sitting there waiting to be collected. That is runnable WORK,
        not an active wait, and the boundary has to say so.
        """
        recorder = ScriptedExec([])
        recorder.dispatch_finished(ANALYSIS_DISPATCH, ANALYSIS_TASK)
        harness = self.build(recorder)

        self.assertEqual(harness.active_dispatch_count(), 0)
        state = harness.observe_orca_dispatch_state()
        self.assertEqual(
            state["runnable_actions"],
            [f"{turn_boundary.ACTION_COLLECT_WORKER_DONE}:{ANALYSIS_TASK}"],
        )

    def test_every_permitted_turn_end_state_is_accepted(self) -> None:
        """The five OS-44 names the ticket enumerates, each proven individually."""
        for status in ("COMPLETED", "BLOCKED", "ESCALATED", "WAITING_FOR_INPUT"):
            with self.subTest(status=status):
                harness = self.build(ScriptedExec([]), run_id=f"run_{status.lower()}")
                verdict = harness.verify_quiescence(status, next_node="PREPARE_WORKER")
                self.assertTrue(verdict["quiescent"])

    def test_the_self_check_runs_immediately_before_the_turn_ends(self) -> None:
        """finish() is this harness's turn end, and it must verify there.

        The check has to read the ledger BEFORE finish() clears it, or every turn would
        trivially look quiescent -- so this asserts the recorded active-dispatch count,
        not merely that a record exists.
        """
        recorder = ScriptedExec([delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))])
        harness = self.build(recorder)
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        harness.finish(
            RuntimeScenarioResult(
                scenario="os44", run_id="run_c2166e75bb02", status="COMPLETED", iteration=1
            )
        )

        verified = [
            record
            for record in self.audit(harness, "run_c2166e75bb02")
            if record["event"] == run_logging.EVENT_QUIESCENCE_VERIFIED
        ]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[-1]["run_status"], "COMPLETED")
        self.assertEqual(verified[-1]["active_dispatches"], 0)


class StaleDeliveryReplayTests(OS44TestCase):
    """``delivery_5c541e7fe1bd``: the PLAN waiter that woke on an ANALYSIS result."""

    def test_the_plan_waiter_does_not_adopt_the_analysis_delivery(self) -> None:
        """The exact recorded sequence, in order.

        The mailbox hands the freshly armed PLAN waiter the replayed ANALYSIS
        ``worker_done`` first, and the real PLAN result only afterwards.  The waiter
        must discard the first, record why, and keep waiting.
        """
        recorder = ScriptedExec(
            [
                delivery(STALE_DELIVERY, worker_done_message(ANALYSIS_TASK, ANALYSIS_DISPATCH)),
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
            ]
        )
        harness = self.build(recorder)

        done, delivery_id = harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(delivery_id, PLAN_DELIVERY)
        self.assertEqual(json.loads(done["payload"])["dispatchId"], PLAN_DISPATCH)
        mismatches = [
            record
            for record in self.audit(harness)
            if record["event"] == run_logging.EVENT_DELIVERY_MISMATCH
        ]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["dispatch_id"], PLAN_DISPATCH)
        self.assertIn(ANALYSIS_DISPATCH, mismatches[0]["detail"])
        # The stale batch is still acknowledged, so it cannot be replayed a third time.
        self.assertIn(STALE_DELIVERY, recorder.acked)

    def test_a_message_naming_the_right_dispatch_but_the_wrong_task_is_refused(self) -> None:
        """Both identities, not one.

        The pre-fix loop compared ``dispatchId`` alone.  A recycled or mis-addressed
        dispatch id that names another Task therefore reached settlement, where the
        pre-mutation gate caught it -- but only AFTER it had been adopted as this
        waiter's result and consumed the wait cycle.
        """
        recorder = ScriptedExec(
            [
                delivery("dlv_wrong_task", worker_done_message("task_someone_else", PLAN_DISPATCH)),
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
            ]
        )
        harness = self.build(recorder)

        done, delivery_id = harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(delivery_id, PLAN_DELIVERY)
        self.assertEqual(json.loads(done["payload"])["taskId"], PLAN_TASK)
        detail = next(
            record["detail"]
            for record in self.audit(harness)
            if record["event"] == run_logging.EVENT_DELIVERY_MISMATCH
        )
        self.assertIn("taskId", detail)

    def test_a_worker_done_with_no_identity_at_all_is_refused(self) -> None:
        recorder = ScriptedExec(
            [
                delivery("dlv_bare", {"id": "msg_bare", "type": "worker_done",
                                      "payload": json.dumps({"outcome": "succeeded"}),
                                      "body": DECLARED_DONE_BODY}),
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
            ]
        )
        harness = self.build(recorder)

        _done, delivery_id = harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(delivery_id, PLAN_DELIVERY)
        self.assertIn(
            "carries no dispatchId",
            next(
                record["detail"]
                for record in self.audit(harness)
                if record["event"] == run_logging.EVENT_DELIVERY_MISMATCH
            ),
        )

    def test_an_unparsable_payload_is_refused_rather_than_raised(self) -> None:
        """A malformed payload proves nothing about identity, so it is a mismatch.

        It must not crash the waiter either: a Coordinator that dies here ends its turn
        in exactly the state the invariant forbids.
        """
        recorder = ScriptedExec(
            [
                delivery("dlv_bad", {"id": "m", "type": "worker_done", "payload": "{not json",
                                     "body": DECLARED_DONE_BODY}),
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
            ]
        )
        harness = self.build(recorder)

        _done, delivery_id = harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(delivery_id, PLAN_DELIVERY)


class AcknowledgementOrderingTests(OS44TestCase):
    """State and settlement first, then the ack, and only then the next waiter."""

    def test_the_matched_delivery_is_recorded_as_processed_before_the_return(self) -> None:
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)

        _done, delivery_id = harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        row = harness._deliveries[delivery_id]
        self.assertEqual(row[DELIVERY_STATE_FIELD], DELIVERY_STATE_PROCESSED)
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))
        self.assertNotIn(PLAN_DELIVERY, recorder.acked)
        self.assertIn(run_logging.EVENT_DELIVERY_PROCESSED, self.events(harness))

    def test_no_waiter_may_be_armed_while_a_processed_delivery_is_unacknowledged(self) -> None:
        """The ordering acceptance criterion, enforced as an ordering.

        This is the gap the recorded run fell through: ``wait_for_done`` returned the
        matched message and the ack happened much later, so anything in between left
        the delivery outstanding and the NEXT ``check --wait`` replayed it.
        """
        recorder = ScriptedExec(
            [
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
                delivery("dlv_next", worker_done_message("task_next", "ctx_next")),
            ]
        )
        harness = self.build(recorder)
        harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)
        waits_before = recorder.waits

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.wait_for_done("ctx_next", "task_next")

        self.assertIn(PLAN_DELIVERY, str(raised.exception))
        self.assertIn("not acknowledged", str(raised.exception))
        self.assertEqual(recorder.waits, waits_before, "no wait was armed")

    def test_a_turn_may_not_end_with_a_processed_unacknowledged_delivery(self) -> None:
        """Reported first and regardless of status.

        A COMPLETED run that leaves a delivery unacknowledged still leaves the replay
        hazard behind, so status alone must never be enough to call the turn quiescent.
        """
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)
        harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.verify_quiescence("COMPLETED")

        self.assertIn(quiescence.QUIESCENCE_UNACKNOWLEDGED_DELIVERY, str(raised.exception))

    def test_settlement_acknowledges_and_then_the_next_waiter_is_allowed(self) -> None:
        """The whole normal phase transition, end to end.

        ANALYSIS settles, its delivery is acknowledged, and only then does the PLAN
        waiter arm and receive the PLAN result -- which is the sequence the recorded
        run was supposed to perform inside one turn.
        """
        recorder = ScriptedExec(
            [
                delivery(STALE_DELIVERY, worker_done_message(ANALYSIS_TASK, ANALYSIS_DISPATCH)),
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
            ]
        )
        harness = self.build(recorder)

        analysis = self.settle(
            harness, task_id=ANALYSIS_TASK, dispatch_id=ANALYSIS_DISPATCH, terminal="term_a"
        )
        self.assertEqual(recorder.acked, [STALE_DELIVERY])
        self.assertEqual(harness.unacknowledged_deliveries(), ())

        plan = self.settle(
            harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p"
        )

        self.assertEqual(analysis.dispatch_id, ANALYSIS_DISPATCH)
        self.assertEqual(plan.dispatch_id, PLAN_DISPATCH)
        self.assertEqual(recorder.acked, [STALE_DELIVERY, PLAN_DELIVERY])
        self.assertEqual(
            harness._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_ACKNOWLEDGED,
        )
        harness.verify_quiescence("COMPLETED")


class AckFailureTests(OS44TestCase):
    """Bounded retry, then fail closed -- and both are named in the audit."""

    def test_a_transient_ack_failure_is_retried_and_succeeds(self) -> None:
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))],
            ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS - 1},
        )
        harness = self.build(recorder)

        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        self.assertEqual(recorder.acked, [PLAN_DELIVERY])
        self.assertEqual(
            harness._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_ACKNOWLEDGED,
        )
        events = self.events(harness)
        self.assertEqual(events.count(run_logging.EVENT_DELIVERY_ACK_RETRY), ACK_MAX_ATTEMPTS - 1)
        self.assertEqual(events.count(run_logging.EVENT_DELIVERY_ACKNOWLEDGED), 1)

    def test_an_ack_that_never_lands_fails_closed_with_an_explicit_reason(self) -> None:
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))],
            ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS + 5},
        )
        harness = self.build(recorder)

        with self.assertRaises(OrcaRuntimeError) as raised:
            self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        self.assertIn(f"{ACK_MAX_ATTEMPTS} attempts", str(raised.exception))
        self.assertEqual(
            harness._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_ACK_FAILED,
        )
        failures = [
            record
            for record in self.audit(harness)
            if record["event"] == run_logging.EVENT_DELIVERY_ACK_FAILED
        ]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["attempts"], ACK_MAX_ATTEMPTS)
        self.assertIn("fails closed", failures[0]["detail"])

    def test_the_ack_is_attempted_exactly_the_bounded_number_of_times(self) -> None:
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))],
            ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS + 5},
        )
        harness = self.build(recorder)
        with self.assertRaises(OrcaRuntimeError):
            self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        attempts = [command for command in recorder.commands if "--ack" in command]
        self.assertEqual(len(attempts), ACK_MAX_ATTEMPTS)

    def test_a_failed_ack_still_blocks_the_next_waiter(self) -> None:
        """Fail-closed means the obligation survives the failure.

        A row left in ``ack_failed`` is still unacknowledged, so the ordering gate and
        the turn-end self-check must both keep refusing.
        """
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))],
            ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS + 5},
        )
        harness = self.build(recorder)
        with self.assertRaises(OrcaRuntimeError):
            self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_quiescence("COMPLETED")


class DuplicateAndOutOfOrderDeliveryTests(OS44TestCase):
    """A replay consumes nothing: no settlement, release, artifact, dispatch or budget."""

    def test_a_replayed_delivery_is_acknowledged_and_takes_no_lifecycle_action(self) -> None:
        recorder = ScriptedExec(
            [
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
                # The same batch again, exactly as an unconsumed acknowledgement
                # would produce -- and then the next phase's real result.
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
                delivery("dlv_second", worker_done_message("task_second", "ctx_second")),
            ]
        )
        harness = self.build(recorder)
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")
        releases_before = harness.lifecycle_commands(PLAN_DISPATCH)

        done, delivery_id = harness.wait_for_done("ctx_second", "task_second")

        self.assertEqual(delivery_id, "dlv_second")
        self.assertEqual(json.loads(done["payload"])["dispatchId"], "ctx_second")
        # No second lifecycle mutation for the replayed dispatch.
        self.assertEqual(harness.lifecycle_commands(PLAN_DISPATCH), releases_before)
        self.assertEqual(harness.lifecycle_commands(PLAN_DISPATCH), ["worker-release"])
        replays = [
            record
            for record in self.audit(harness)
            if record["event"] == run_logging.EVENT_DELIVERY_REPLAYED
        ]
        self.assertEqual(len(replays), 1)
        self.assertEqual(replays[0]["replays"], 1)
        # Acknowledged a second time so it cannot be replayed forever.
        self.assertEqual(recorder.acked.count(PLAN_DELIVERY), 2)

    def test_a_replay_never_becomes_the_next_waiters_result(self) -> None:
        """Even when the replayed batch would satisfy the waiter's own identity check.

        A redelivered batch for the dispatch we are waiting on is still a replay: the
        run already processed it, and adopting it would settle the same dispatch twice.
        """
        recorder = ScriptedExec(
            [
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
            ]
        )
        harness = self.build(recorder)
        harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)
        harness._ack(PLAN_DELIVERY)

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        # The replay was acknowledged, the loop kept waiting, and the mailbox ran dry.
        self.assertIn("timed out", str(raised.exception))
        self.assertEqual(recorder.acked.count(PLAN_DELIVERY), 2)

    def test_replay_is_bounded_and_then_fails_closed(self) -> None:
        """A runtime that never consumes the acknowledgement must not spin the loop."""
        batches = [
            delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))
            for _ in range(quiescence.DELIVERY_REPLAY_LIMIT + 3)
        ]
        recorder = ScriptedExec(batches)
        harness = self.build(recorder)
        harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)
        harness._ack(PLAN_DELIVERY)

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("replayed", str(raised.exception))
        self.assertIn(str(quiescence.DELIVERY_REPLAY_LIMIT), str(raised.exception))

    def test_a_duplicate_settlement_is_still_refused_by_the_finalize_once_gate(self) -> None:
        """Defence in depth: the pre-existing gate is not weakened by any of this."""
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)
        first = self.settle(
            harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p"
        )
        commands_before = list(recorder.commands)

        replayed = harness.settle_attempt(
            "worker", 1, PLAN_TASK, PLAN_DISPATCH,
            worker_done_message(PLAN_TASK, PLAN_DISPATCH), PLAN_DELIVERY,
            terminal="term_p",
        )

        self.assertEqual(replayed.dispatch_id, first.dispatch_id)
        self.assertEqual(recorder.commands, commands_before, "zero commands on replay")


class DuplicateDeliverySideEffectTests(OS44TestCase):
    """BUGFIX-I1-G5-3 / V2. Every side-effect category, asserted rather than named.

    V2 requires evidence that a duplicate, a replayed and an out-of-order delivery
    consume no second SETTLEMENT, RELEASE, ARTIFACT, DISPATCH or ITERATION/BUDGET.
    Iteration 1 named those categories in a docstring and asserted only the first two,
    so the artifact, dispatch and iteration halves had no evidence at all.

    These drive the real dispatch path -- ``start_run`` then ``run_existing_task`` --
    because that is the only place the last three categories exist: a duplicate that
    consumed an artifact write, a dispatch or an iteration would do it there or nowhere.
    One run, two genuine attempts, and between them a duplicate redelivery of the first
    attempt's batch and an out-of-order batch carrying the first attempt's stale
    ``worker_done`` under a fresh delivery id.
    """

    RUN_ID = "run_v2_side_effects"
    ATTEMPT_ONE = {"task": "task_v1", "dispatch": "ctx_v1", "delivery": "dlv_v1"}
    ATTEMPT_TWO = {"task": "task_v2", "dispatch": "ctx_v2", "delivery": "dlv_v2"}

    def drive_first_attempt(
        self, *extra_deliveries: dict[str, Any]
    ) -> tuple[OrcaRuntimeHarness, MailboxExec]:
        """One real run, one real settled attempt, and whatever arrives after it."""
        one = self.ATTEMPT_ONE
        recorder = MailboxExec(
            [
                delivery(one["delivery"], worker_done_message(one["task"], one["dispatch"])),
                *extra_deliveries,
            ]
        )
        recorder.results["run-create"] = {"run": {"id": self.RUN_ID}}
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder
        harness.start_run("OS-44 V2 side effects", requested_phases=("implementation",))
        recorder.arm(one["dispatch"], one["task"], self.ATTEMPT_TWO["task"])
        harness.run_existing_task(
            "worker", 1, "complete", one["task"], phase="implementation"
        )
        return harness, recorder

    def drive(self) -> tuple[OrcaRuntimeHarness, MailboxExec, dict[str, Any]]:
        """Two real attempts, with a duplicate and an out-of-order batch between them.

        Returns the harness, the recorder and the side-effect snapshot taken after the
        FIRST attempt settled -- everything the replays are forbidden to change.
        """
        one, two = self.ATTEMPT_ONE, self.ATTEMPT_TWO
        stale = worker_done_message(one["task"], one["dispatch"], message_id="msg_stale")
        recorder = MailboxExec(
            [
                # attempt 1's own result
                delivery(one["delivery"], worker_done_message(one["task"], one["dispatch"])),
                # DUPLICATE: the same delivery id again, which is what an unconsumed
                # acknowledgement produces.
                delivery(one["delivery"], worker_done_message(one["task"], one["dispatch"])),
                # OUT OF ORDER: a fresh delivery id carrying the PREVIOUS attempt's
                # worker_done, arriving while attempt 2's waiter is armed.
                delivery("dlv_out_of_order", stale),
                # attempt 2's own result
                delivery(two["delivery"], worker_done_message(two["task"], two["dispatch"])),
            ]
        )
        recorder.results["run-create"] = {"run": {"id": self.RUN_ID}}
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = recorder
        harness.start_run("OS-44 V2 side effects", requested_phases=("implementation",))

        recorder.arm(one["dispatch"], one["task"], two["task"])
        harness.run_existing_task(
            "worker", 1, "complete", one["task"], phase="implementation"
        )
        snapshot = self.side_effects(harness, recorder)

        recorder.arm(two["dispatch"], one["task"], two["task"])
        harness.run_existing_task(
            "worker", 2, "correction", two["task"],
            phase="implementation", round_kind="correction",
        )
        return harness, recorder, snapshot

    # ---- the five V2 categories, each read off something that really happened ------

    def orchestrator_rows(self) -> list[dict[str, str]]:
        path = (
            self.artifact_dir / "artifacts" / "runs" / self.RUN_ID
            / run_logging.ORCHESTRATOR_LOG_FILENAME
        )
        lines = path.read_text(encoding="utf-8").splitlines()
        columns = [cell.strip() for cell in lines[0].strip("|").split("|")]
        return [
            dict(zip(columns, (cell.strip() for cell in line.strip("|").split("|"))))
            for line in lines[2:]
        ]

    def timing_rows(self) -> list[dict[str, str]]:
        path = (
            self.artifact_dir / "artifacts" / "runs" / self.RUN_ID
            / run_logging.TIMING_LOG_FILENAME
        )
        lines = path.read_text(encoding="utf-8").splitlines()
        columns = [cell.strip() for cell in lines[0].strip("|").split("|")]
        return [
            dict(zip(columns, (cell.strip() for cell in line.strip("|").split("|"))))
            for line in lines[2:]
        ]

    def side_effects(
        self, harness: OrcaRuntimeHarness, recorder: MailboxExec
    ) -> dict[str, Any]:
        settled = [row for row in self.orchestrator_rows() if row["event"] == "dispatch_settled"]
        return {
            # settlement
            "finalized": sorted(
                dispatch_id
                for dispatch_id, row in harness._ledger.items()
                if row.get("state") == "finalized"
            ),
            "settlement_replays": {
                dispatch_id: row.get("replays", 0)
                for dispatch_id, row in harness._ledger.items()
            },
            # release
            "releases": [
                verb for verb in recorder.verbs if verb in {"worker-release", "worker-retain"}
            ],
            # artifact
            "settled_rows": [row["dispatch_id"] for row in settled],
            "decision_records": len(
                run_logging.read_decision_ledger(self.RUN_ID, base=self.artifact_dir)
            ),
            # dispatch
            "dispatches_started": recorder.verbs.count("worker-start"),
            "tasks_created": recorder.verbs.count("task-create"),
            "terminals_created": len(recorder.created),
            # iteration / budget
            "iterations": sorted(row["iteration"] for row in settled),
            "iteration_starts": [
                row["iteration"]
                for row in self.timing_rows()
                if row["event"] == "iteration_start"
            ],
        }

    def test_a_duplicate_and_an_out_of_order_delivery_settle_nothing_twice(self) -> None:
        harness, recorder, before = self.drive()
        after = self.side_effects(harness, recorder)

        # SETTLEMENT: exactly one finalized row per real dispatch, and the first
        # attempt's row was never re-entered by the replay.
        self.assertEqual(before["finalized"], ["ctx_v1"])
        self.assertEqual(after["finalized"], ["ctx_v1", "ctx_v2"])
        self.assertEqual(after["settlement_replays"], {"ctx_v1": 0, "ctx_v2": 0})

    def test_a_duplicate_and_an_out_of_order_delivery_release_nothing_twice(self) -> None:
        harness, recorder, before = self.drive()
        after = self.side_effects(harness, recorder)

        # RELEASE: one lifecycle mutation per real dispatch, never two.
        self.assertEqual(before["releases"], ["worker-release"])
        self.assertEqual(after["releases"], ["worker-release", "worker-release"])
        self.assertEqual(harness.lifecycle_commands("ctx_v1"), ["worker-release"])
        self.assertEqual(harness.lifecycle_commands("ctx_v2"), ["worker-release"])

    def test_a_duplicate_and_an_out_of_order_delivery_write_no_extra_artifact(self) -> None:
        harness, recorder, before = self.drive()
        after = self.side_effects(harness, recorder)

        # ARTIFACT: one ORCHESTRATOR_LOG settlement row and one decision-ledger record
        # per real attempt. A replay that reached _log_attempt would show up here as a
        # third row for a dispatch that only settled once.
        self.assertEqual(before["settled_rows"], ["ctx_v1"])
        self.assertEqual(after["settled_rows"], ["ctx_v1", "ctx_v2"])
        self.assertEqual(after["decision_records"], before["decision_records"] + 1)

    def test_a_duplicate_and_an_out_of_order_delivery_create_no_new_dispatch(self) -> None:
        harness, recorder, before = self.drive()
        after = self.side_effects(harness, recorder)

        # DISPATCH: two attempts, two `worker-start` commands, two terminals -- and no
        # Task was created at all, because both came from the graph.
        self.assertEqual(before["dispatches_started"], 1)
        self.assertEqual(after["dispatches_started"], 2)
        self.assertEqual(after["tasks_created"], 0)
        self.assertEqual(after["terminals_created"], before["terminals_created"] + 1)

    def test_a_duplicate_and_an_out_of_order_delivery_consume_no_iteration(self) -> None:
        harness, recorder, before = self.drive()
        after = self.side_effects(harness, recorder)

        # ITERATION / BUDGET: iteration 1 and iteration 2, once each. Three deliveries
        # reached the second waiter and exactly one of them was a real round.
        self.assertEqual(before["iterations"], ["1"])
        self.assertEqual(after["iterations"], ["1", "2"])
        self.assertEqual(after["iteration_starts"], ["1", "2"])

    def test_a_redelivery_for_the_waiters_own_dispatch_consumes_nothing(self) -> None:
        """The recorded shape, where the replay ledger is the ONLY thing in the way.

        The duplicate and out-of-order batches above are refused by the provenance
        filter as well, which is defence in depth but leaves the replay ledger's own
        contribution unmeasured. Here the Coordinator re-arms a waiter for the SAME
        dispatch -- what `run_c2166e75bb02` did across its phase boundary -- so the
        redelivered batch satisfies the waiter's identity check exactly and nothing but
        "this run already handled this delivery" can refuse it.
        """
        one = self.ATTEMPT_ONE
        harness, recorder = self.drive_first_attempt(
            delivery(one["delivery"], worker_done_message(one["task"], one["dispatch"]))
        )
        before = self.side_effects(harness, recorder)

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.wait_for_done(one["dispatch"], one["task"])

        # Acknowledged again, never adopted, and the mailbox then ran dry.
        self.assertIn("timed out", str(raised.exception))
        self.assertEqual(recorder.acked.count(one["delivery"]), 2)
        # SETTLEMENT, RELEASE, ARTIFACT, DISPATCH and ITERATION/BUDGET, all five at
        # once: nothing the first attempt produced changed and nothing was added.
        self.assertEqual(self.side_effects(harness, recorder), before)

    def test_the_replays_really_did_arrive_and_were_acknowledged(self) -> None:
        """The negative assertions above are only worth anything if the replays ran."""
        harness, recorder, _ = self.drive()

        # The duplicate was acknowledged a second time, and the out-of-order batch was
        # acknowledged once -- neither was adopted as the second waiter's result.
        self.assertEqual(recorder.acked.count("dlv_v1"), 2)
        self.assertEqual(recorder.acked.count("dlv_out_of_order"), 1)
        self.assertEqual(recorder.acked.count("dlv_v2"), 1)
        events = self.events(harness, self.RUN_ID)
        self.assertIn(run_logging.EVENT_DELIVERY_REPLAYED, events)
        self.assertIn(run_logging.EVENT_DELIVERY_MISMATCH, events)
        # And the second attempt settled its OWN dispatch, not the replayed one.
        self.assertEqual(harness._ledger["ctx_v2"]["attempt"].task_id, "task_v2")


class ReplayedSettlementDischargeTests(OS44TestCase):
    """The ack obligation must not outlive the only code path that can clear it."""

    def test_a_replayed_settlement_still_discharges_its_delivery_obligation(self) -> None:
        """The obligation cannot outlive the only code that could clear it.

        A delivery consumed for a dispatch the finalize-once gate has ALREADY settled
        never reaches the settlement path's ack, because STEP 0 returns first. Without
        an explicit discharge there, the ordering gate and the turn-end self-check
        would both keep refusing forever on an obligation nothing can clear -- a
        different stall in the same family as the one this ticket removes.
        """
        recorder = ScriptedExec(
            [
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
                # A NEW delivery carrying the same already-settled dispatch's result,
                # which is what a mailbox produces after a redelivery with a fresh id.
                delivery("dlv_resent", worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
            ]
        )
        harness = self.build(recorder)
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")
        lifecycle_before = harness.lifecycle_commands(PLAN_DISPATCH)

        done, delivery_id = harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)
        self.assertEqual(delivery_id, "dlv_resent")
        self.assertEqual(harness.unacknowledged_deliveries(), ("dlv_resent",))

        replayed = harness.settle_attempt(
            "worker", 1, PLAN_TASK, PLAN_DISPATCH, done, delivery_id, terminal="term_p"
        )

        self.assertEqual(replayed.dispatch_id, PLAN_DISPATCH)
        # The obligation is discharged and no SECOND lifecycle mutation was issued.
        self.assertEqual(harness.unacknowledged_deliveries(), ())
        self.assertEqual(harness.lifecycle_commands(PLAN_DISPATCH), lifecycle_before)
        self.assertEqual(harness.lifecycle_commands(PLAN_DISPATCH), ["worker-release"])
        harness.verify_quiescence("COMPLETED")

    def test_an_already_acknowledged_delivery_costs_the_replay_path_zero_commands(self) -> None:
        """The discharge is conditional, so the ordinary replay stays command-free."""
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")
        commands_before = list(recorder.commands)

        harness.settle_attempt(
            "worker", 1, PLAN_TASK, PLAN_DISPATCH,
            worker_done_message(PLAN_TASK, PLAN_DISPATCH), PLAN_DELIVERY,
            terminal="term_p",
        )

        self.assertEqual(recorder.commands, commands_before)


class CrashBetweenReflectionAndAckTests(OS44TestCase):
    """BUGFIX-I1-G1-1. The crash window the acknowledgement used to sit above.

    Iteration 1 acknowledged the delivery in STEP 3 and only then ran ``account_axes``,
    built the ``RuntimeAttempt`` and called ``finalize_once``. A failure anywhere in
    that tail landed AFTER a successful ack: the runtime considered the delivery
    consumed and would never redeliver it, while the settlement ledger was left
    incomplete -- the delivery was simply lost, and nothing recorded that it had been.

    The fix does not move the window, it closes it: state and settlement are reflected
    first, the durable settled record is published, and the ack is the last statement
    of the method. Every test below injects the failure at exactly the point where the
    old ack would already have succeeded.
    """

    def crash_after_the_mutation(self, harness: OrcaRuntimeHarness) -> Any:
        """Fail the axis accounting: the first step after the lifecycle command.

        This is precisely where iteration 1 had already acknowledged the delivery.
        """
        return patch.object(
            harness,
            "account_axes",
            side_effect=OrcaRuntimeError("crash after the lifecycle mutation"),
        )

    def consume(self, harness: OrcaRuntimeHarness) -> tuple[dict[str, Any], str]:
        harness.register_terminal(
            "term_p",
            role="active_worker",
            origin="self_created",
            intended_role="phase_worker",
            owner_dispatch_id=PLAN_DISPATCH,
        )
        return harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

    def crashed_harness(self) -> tuple[OrcaRuntimeHarness, ScriptedExec]:
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)
        done, delivery_id = self.consume(harness)
        with self.crash_after_the_mutation(harness):
            with self.assertRaises(OrcaRuntimeError):
                harness.settle_attempt(
                    "worker", 1, PLAN_TASK, PLAN_DISPATCH, done, delivery_id,
                    terminal="term_p",
                )
        return harness, recorder

    def test_the_delivery_is_not_acknowledged_when_finalization_fails(self) -> None:
        harness, recorder = self.crashed_harness()

        # Pre-fix this list held PLAN_DELIVERY: the ack ran above account_axes.
        self.assertEqual(recorder.acked, [])
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))
        self.assertEqual(harness._ledger[PLAN_DISPATCH]["state"], "in_progress")
        events = self.events(harness)
        # The claim was published before the first settlement command; the settled
        # record and the acknowledgement were not, because neither happened.
        self.assertIn(run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED, events)
        self.assertNotIn(run_logging.EVENT_DELIVERY_SETTLED, events)
        self.assertNotIn(run_logging.EVENT_DELIVERY_ACKNOWLEDGED, events)

    def test_nothing_may_proceed_over_the_unacknowledged_delivery(self) -> None:
        """Not lost, and not silently stepped over either."""
        harness, _ = self.crashed_harness()

        with self.assertRaises(OrcaRuntimeError) as armed:
            harness.wait_for_done("ctx_next", "task_next")
        self.assertIn("was processed and not acknowledged", str(armed.exception))

        with self.assertRaises(OrcaRuntimeError) as ended:
            harness.verify_quiescence("COMPLETED")
        self.assertIn(
            quiescence.QUIESCENCE_UNACKNOWLEDGED_DELIVERY, str(ended.exception)
        )

    def test_the_failed_settlement_is_never_repeated_in_this_process(self) -> None:
        """No duplication: one lifecycle command went out and one is all there is."""
        harness, recorder = self.crashed_harness()

        with self.assertRaisesRegex(OrcaRuntimeError, "in progress|crashed"):
            harness.settle_attempt(
                "worker", 1, PLAN_TASK, PLAN_DISPATCH,
                worker_done_message(PLAN_TASK, PLAN_DISPATCH), PLAN_DELIVERY,
                terminal="term_p",
            )

        self.assertEqual(harness.lifecycle_commands(PLAN_DISPATCH), ["worker-release"])
        self.assertEqual(recorder.acked, [])

    def test_a_successor_recovers_that_delivery_and_issues_no_second_release(self) -> None:
        """And no duplication across the restart either.

        The delivery is still in the runtime's hands -- it was never acknowledged --
        so it is redelivered. The successor finds a settlement CLAIM with no settled
        record, which is "a lifecycle command may already have gone out", and refuses
        to repeat one instead of guessing.
        """
        self.crashed_harness()
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        successor = self.successor(recorder)

        with self.assertRaises(OrcaRuntimeError) as raised:
            successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("claimed a settlement that was never recorded", str(raised.exception))
        self.assertEqual(successor.lifecycle_commands(PLAN_DISPATCH), [])
        recovery = [
            record
            for record in self.audit(successor)
            if record["event"] == run_logging.EVENT_DELIVERY_RECOVERY
        ]
        self.assertEqual([row["reason_code"] for row in recovery], [quiescence.DELIVERY_RECOVER])

    def test_a_crash_before_the_claim_is_resumed_and_settles_exactly_once(self) -> None:
        """The other side of the boundary, and the positive proof of "not lost".

        A process that consumed the delivery and died before claiming any settlement
        mutated nothing. Discarding the redelivery as a replay there is exactly how the
        result would be LOST, so the successor processes it again -- once.
        """
        first = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        self.consume(self.build(first))  # consumed, nothing settled, then the process dies
        self.assertEqual(first.acked, [])

        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        successor = self.successor(recorder)

        done, delivery_id = self.consume(successor)
        attempt = successor.settle_attempt(
            "worker", 1, PLAN_TASK, PLAN_DISPATCH, done, delivery_id, terminal="term_p"
        )

        self.assertEqual(attempt.finalizations, 1)
        self.assertEqual(successor.lifecycle_commands(PLAN_DISPATCH), ["worker-release"])
        self.assertEqual(recorder.acked, [PLAN_DELIVERY])
        self.assertEqual(successor.unacknowledged_deliveries(), ())
        recovery = [
            record
            for record in self.audit(successor)
            if record["event"] == run_logging.EVENT_DELIVERY_RECOVERY
        ]
        self.assertEqual([row["reason_code"] for row in recovery], [quiescence.DELIVERY_RESUME])

    def test_the_acknowledgement_is_the_last_step_of_a_successful_settlement(self) -> None:
        """The ordering itself, read off the audit and off the command log."""
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)

        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        self.assertEqual(
            self.events(harness),
            [
                run_logging.EVENT_DELIVERY_PROCESSED,
                run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED,
                run_logging.EVENT_DELIVERY_SETTLED,
                # OS-44 (BUGFIX-I3-MAJOR-1). The ack intent sits between the settled
                # record and the acknowledgement, because it is published BEFORE the
                # wire command whose outcome the acknowledgement reports.
                run_logging.EVENT_DELIVERY_ACK_INTENT,
                run_logging.EVENT_DELIVERY_ACKNOWLEDGED,
            ],
        )
        # And on the wire: the lifecycle mutation, then the acknowledgement.
        verbs = [
            command[1] if len(command) > 1 else command[0]
            for command in recorder.commands
            if command[1] == "worker-release" or "--ack" in command
        ]
        self.assertEqual(verbs, ["worker-release", "check"])
        self.assertTrue(harness._deliveries[PLAN_DELIVERY][DELIVERY_SETTLED_FIELD])
        self.assertTrue(
            harness._deliveries[PLAN_DELIVERY][DELIVERY_SETTLEMENT_CLAIMED_FIELD]
        )


class DurableBeforeMemoryTransitionTests(OS44TestCase):
    """BUGFIX-I2-G1-1. A transition that cannot be durably recorded has not happened.

    Iteration 2 made the Coordinator audit the only source a successor recovers the
    delivery ledger from, and made an unpublishable record fail closed -- but it still
    flipped the in-memory progress flag BEFORE publishing the record that justifies it.
    So a failed ``delivery_settled`` write left memory saying "settled" over an audit
    that said only "claimed": a later ``settle_attempt()`` took STEP 0's
    already-finalized branch and acknowledged a delivery whose durable settled record
    did not exist, and after a restart the only recovery source classified that row as
    an unfinished claim while the runtime's copy of the delivery was already gone. The
    same ordering let a wire acknowledgement succeed and its ``delivery_acknowledged``
    record fail, permanently omitting R8's required outcome.

    Every progress transition now commits only after its publication succeeds, and the
    STEP 0 discharge refuses to acknowledge a delivery whose durable settled state is
    not proven. Each test below injects a failure into exactly ONE audit event, so what
    is under test is the ordering rather than the fail-closed rule iteration 2 added.
    """

    def audit_write_fails_for(self, event: str) -> Any:
        """Fail one audit event's publication and publish every other one for real."""
        published = run_logging.append_coordinator_audit_record

        def publish(run_id: str, name: str, fields: dict, **kwargs: Any) -> Any:
            if name == event:
                raise OSError(f"full disk writing {name}")
            return published(run_id, name, fields, **kwargs)

        return patch.object(run_logging, "append_coordinator_audit_record", publish)

    def settle_with_failed_record(
        self, event: str
    ) -> tuple[OrcaRuntimeHarness, ScriptedExec]:
        """Drive one real settlement whose `event` record cannot be published."""
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)
        with self.audit_write_fails_for(event):
            with self.assertRaises(OrcaRuntimeError) as raised:
                self.settle(
                    harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p"
                )
        self.assertIn("fails closed", str(raised.exception))
        self.assertTrue(harness._logging_errors)
        return harness, recorder

    def retry(self, harness: OrcaRuntimeHarness) -> Any:
        """Re-enter settle_attempt for the same delivery: STEP 0's finalized branch."""
        return harness.settle_attempt(
            "worker",
            1,
            PLAN_TASK,
            PLAN_DISPATCH,
            worker_done_message(PLAN_TASK, PLAN_DISPATCH),
            PLAN_DELIVERY,
            terminal="term_p",
        )

    # ---- delivery_settled ---------------------------------------------------------

    def test_a_failed_settled_record_leaves_the_row_unsettled(self) -> None:
        """The finding itself: memory may not run ahead of the artifact."""
        harness, recorder = self.settle_with_failed_record(
            run_logging.EVENT_DELIVERY_SETTLED
        )
        row = harness._deliveries[PLAN_DELIVERY]

        self.assertTrue(row[DELIVERY_SETTLEMENT_CLAIMED_FIELD])
        # Pre-fix this flag was True while the audit below ended at the claim.
        self.assertFalse(row[DELIVERY_SETTLED_FIELD])
        self.assertEqual(
            self.events(harness),
            [
                run_logging.EVENT_DELIVERY_PROCESSED,
                run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED,
            ],
        )
        self.assertEqual(recorder.acked, [])
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))

    def test_a_reentry_after_a_failed_settled_record_refuses_to_acknowledge(self) -> None:
        """Re-entry. The exact path the reviewer reproduced an acknowledgement on.

        ``finalize_once`` already ran, so STEP 0 hands the retry the recorded
        settlement and the old code discharged the delivery there unconditionally.
        """
        harness, recorder = self.settle_with_failed_record(
            run_logging.EVENT_DELIVERY_SETTLED
        )

        with self.assertRaises(OrcaRuntimeError) as raised:
            self.retry(harness)

        self.assertIn("claimed a settlement that was never recorded", str(raised.exception))
        # Pre-fix: recorder.acked == [PLAN_DELIVERY] over an audit with no settled row.
        self.assertEqual(recorder.acked, [])
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))
        self.assertEqual(harness.lifecycle_commands(PLAN_DISPATCH), ["worker-release"])
        recovery = [
            record
            for record in self.audit(harness)
            if record["event"] == run_logging.EVENT_DELIVERY_RECOVERY
        ]
        self.assertEqual(
            [row["reason_code"] for row in recovery], [quiescence.DELIVERY_RECOVER]
        )

    def test_a_restart_after_a_failed_settled_record_recovers_it_explicitly(self) -> None:
        """Restart. The delivery is still the runtime's, so it comes back."""
        self.settle_with_failed_record(run_logging.EVENT_DELIVERY_SETTLED)
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        successor = self.successor(recorder)

        self.assertFalse(successor._deliveries[PLAN_DELIVERY][DELIVERY_SETTLED_FIELD])
        with self.assertRaises(OrcaRuntimeError) as raised:
            successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("claimed a settlement that was never recorded", str(raised.exception))
        self.assertEqual(recorder.acked, [])
        self.assertNotIn("worker-release", recorder.verbs)

    # ---- delivery_acknowledged ----------------------------------------------------

    def test_a_failed_acknowledged_record_does_not_commit_the_acknowledgement(self) -> None:
        """R8's required outcome is not omitted: the run stops where it could not record.

        The wire acknowledgement cannot be unsent, so the fail-closed direction is the
        in-memory transition: the delivery stays an outstanding obligation, nothing may
        arm over it or end the turn, and the retry re-publishes the record.
        """
        harness, recorder = self.settle_with_failed_record(
            run_logging.EVENT_DELIVERY_ACKNOWLEDGED
        )
        row = harness._deliveries[PLAN_DELIVERY]

        self.assertEqual(recorder.acked, [PLAN_DELIVERY])
        self.assertTrue(row[DELIVERY_SETTLED_FIELD])
        # Pre-fix this was DELIVERY_STATE_ACKNOWLEDGED over an audit that has no such row.
        # It is now `ack_intent`: the wire ack WAS issued and its outcome was not
        # recorded, which is precisely the state the audit has to be able to express.
        self.assertEqual(row[DELIVERY_STATE_FIELD], DELIVERY_STATE_ACK_INTENT)
        self.assertTrue(row[DELIVERY_ACK_INTENT_FIELD])
        self.assertEqual(
            self.events(harness),
            [
                run_logging.EVENT_DELIVERY_PROCESSED,
                run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED,
                run_logging.EVENT_DELIVERY_SETTLED,
                run_logging.EVENT_DELIVERY_ACK_INTENT,
            ],
        )
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))
        with self.assertRaises(OrcaRuntimeError) as ended:
            harness.verify_quiescence("COMPLETED")
        self.assertIn(
            quiescence.QUIESCENCE_UNACKNOWLEDGED_DELIVERY, str(ended.exception)
        )

    def test_a_reentry_publishes_the_acknowledgement_record_the_first_attempt_lost(self) -> None:
        """Re-entry. Durable settled state IS proven here, so the discharge proceeds."""
        harness, recorder = self.settle_with_failed_record(
            run_logging.EVENT_DELIVERY_ACKNOWLEDGED
        )

        self.retry(harness)

        self.assertEqual(
            self.events(harness),
            [
                run_logging.EVENT_DELIVERY_PROCESSED,
                run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED,
                run_logging.EVENT_DELIVERY_SETTLED,
                # One intent, not two: the re-entry re-issues the same intent, and the
                # record is published once, before the first attempt.
                run_logging.EVENT_DELIVERY_ACK_INTENT,
                run_logging.EVENT_DELIVERY_ACKNOWLEDGED,
            ],
        )
        self.assertEqual(harness.unacknowledged_deliveries(), ())
        self.assertEqual(
            harness._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_ACKNOWLEDGED,
        )
        # One settlement, one release: the re-entry mutated no lifecycle state.
        self.assertEqual(harness.lifecycle_commands(PLAN_DISPATCH), ["worker-release"])
        self.assertEqual(recorder.acked, [PLAN_DELIVERY, PLAN_DELIVERY])

    def test_a_restart_after_a_failed_acknowledged_record_replays_and_never_resettles(self) -> None:
        """Restart. Settled IS durable, so the redelivery is a replay, not a recovery."""
        self.settle_with_failed_record(run_logging.EVENT_DELIVERY_ACKNOWLEDGED)
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        successor = self.successor(recorder)

        self.assertTrue(successor._deliveries[PLAN_DELIVERY][DELIVERY_SETTLED_FIELD])
        # OS-44 (BUGFIX-I3-MAJOR-1). The successor does not wait to find out: binding
        # the run RECONCILED the inherited acknowledgement, so the obligation is closed
        # before anything else happens and the row carries a terminal state.
        self.assertEqual(
            successor._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_ACK_RECONCILED,
        )
        self.assertEqual(recorder.acked, [PLAN_DELIVERY])
        self.assertIn(run_logging.EVENT_DELIVERY_ACK_RECONCILED, self.events(successor))

        with self.assertRaises(OrcaRuntimeError) as raised:
            successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("timed out", str(raised.exception))
        # The redelivery is still a replay, and it still settles nothing.
        self.assertEqual(recorder.acked, [PLAN_DELIVERY, PLAN_DELIVERY])
        self.assertNotIn("worker-release", recorder.verbs)
        self.assertIn(run_logging.EVENT_DELIVERY_REPLAYED, self.events(successor))

    # ---- the same rule, one boundary earlier --------------------------------------

    def test_a_failed_processed_record_does_not_commit_the_consumption(self) -> None:
        """The rule is the family's, not one call site's: publish, then transition.

        The row is still OPENED -- an unopened row would be an invisible obligation --
        but it does not reach ``processed``, and it stays outstanding either way.
        """
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)

        with self.audit_write_fails_for(run_logging.EVENT_DELIVERY_PROCESSED):
            with self.assertRaises(OrcaRuntimeError):
                harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertNotEqual(
            harness._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_PROCESSED,
        )
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))
        self.assertEqual(recorder.acked, [])
        self.assertEqual(self.events(harness), [])


class ProcessRestartTests(OS44TestCase):
    """A fresh Coordinator process over an existing run must not re-adopt a replay."""

    def test_a_restarted_coordinator_recovers_without_being_asked_to(self) -> None:
        """BUGFIX-I1-G1-2. The PRODUCTION restart entry point, not the helper.

        Nothing in this test calls ``restore_delivery_ledger()``. ``resume_run()`` is
        what a successor Coordinator process uses to bind an existing Run, and the
        recovery has to be its own doing -- a helper that only tests remember to call
        proves the helper works, not that a restarted Coordinator uses it.
        """
        first = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(first)
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        recorder = ScriptedExec(
            [
                delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH)),
                delivery("dlv_after_restart", worker_done_message("task_after", "ctx_after")),
            ]
        )
        successor = self.successor(recorder)
        # Recovered by resume_run() itself, before any waiter could be armed.
        self.assertIn(PLAN_DELIVERY, successor._deliveries)

        done, delivery_id = successor.wait_for_done("ctx_after", "task_after")

        self.assertEqual(delivery_id, "dlv_after_restart")
        self.assertEqual(json.loads(done["payload"])["dispatchId"], "ctx_after")
        self.assertIn(run_logging.EVENT_DELIVERY_REPLAYED, self.events(successor))
        self.assertEqual(successor.lifecycle_commands(PLAN_DISPATCH), [])

    def test_the_wait_path_itself_recovers_a_run_bound_any_other_way(self) -> None:
        """Defence in depth: the gate is on `check --wait`, not on one entry point.

        ``self.build()`` binds the run id without going through ``resume_run`` -- the
        shape any other future binding route would have. The recovery still happens,
        because ``_check()`` performs it above the command it guards.
        """
        first = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(first)
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        # The successor re-arms the waiter for the SAME dispatch, which is what a
        # Coordinator that lost its memory of the settlement does. The redelivery
        # satisfies the waiter's own identity check, so nothing but the recovered
        # ledger can stop it from being adopted and settled a second time.
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        successor = self.build(recorder)
        self.assertEqual(successor._deliveries, {})

        with self.assertRaises(OrcaRuntimeError) as raised:
            successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        # Acknowledged as a replay, never adopted, and the mailbox then ran dry.
        self.assertIn("timed out", str(raised.exception))
        self.assertEqual(recorder.acked, [PLAN_DELIVERY])
        self.assertEqual(successor.lifecycle_commands(PLAN_DISPATCH), [])
        self.assertIn(run_logging.EVENT_DELIVERY_REPLAYED, self.events(successor))

    def test_the_delivery_ledger_is_restored_from_the_append_only_audit(self) -> None:
        first = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(first)
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")

        # A genuinely new process: a new harness object over the same run and the same
        # artifact root, with an empty in-memory ledger.
        successor = self.build(ScriptedExec([]))
        self.assertEqual(successor._deliveries, {})

        restored = successor.restore_delivery_ledger()

        self.assertIn(PLAN_DELIVERY, restored)
        self.assertEqual(
            restored[PLAN_DELIVERY][DELIVERY_STATE_FIELD], DELIVERY_STATE_ACKNOWLEDGED
        )
        self.assertEqual(successor.unacknowledged_deliveries(), ())

    def test_a_restart_over_a_run_with_no_audit_restores_nothing_and_does_not_raise(self) -> None:
        successor = self.successor(ScriptedExec([]), run_id="run_never_written")
        self.assertEqual(successor._deliveries, {})

    def test_a_recovered_obligation_does_not_block_the_waiter_it_needs(self) -> None:
        """A predecessor's unacknowledged delivery is not this process's obligation.

        Counting it as one would refuse to arm the only waiter the redelivery can
        arrive on -- fail-closed into a permanent stall. It is resolved by the
        redelivery's disposition, and becomes this process's own obligation the moment
        this process consumes it.
        """
        first = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(first)
        harness.register_terminal(
            "term_p", role="active_worker", origin="self_created",
            intended_role="phase_worker", owner_dispatch_id=PLAN_DISPATCH,
        )
        harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)  # consumed, never acknowledged
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))

        successor = self.successor(ScriptedExec([]))

        self.assertIn(PLAN_DELIVERY, successor._deliveries)
        self.assertTrue(successor._deliveries[PLAN_DELIVERY][DELIVERY_RECOVERED_FIELD])
        self.assertEqual(successor.unacknowledged_deliveries(), ())
        successor.verify_quiescence("COMPLETED")

    def test_resume_run_refuses_to_bind_a_run_it_was_not_given(self) -> None:
        with patch.dict(environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(self.artifact_dir)
        harness._exec_orca = ScriptedExec([])
        with self.assertRaises(OrcaRuntimeError):
            harness.resume_run("", run_owner="term_owner")


class CoordinatorAuditArtifactTests(OS44TestCase):
    """The audit is append-only, immutable, and refuses an unknown event."""

    def test_records_are_published_in_sequence_and_never_overwritten(self) -> None:
        for index in range(3):
            run_logging.append_coordinator_audit_record(
                "run_audit", run_logging.EVENT_DELIVERY_PROCESSED,
                {"delivery_id": f"dlv_{index}"}, base=self.artifact_dir,
            )
        records = run_logging.read_coordinator_audit("run_audit", base=self.artifact_dir)

        self.assertEqual([record["sequence"] for record in records], [0, 1, 2])
        self.assertEqual(
            [record["delivery_id"] for record in records], ["dlv_0", "dlv_1", "dlv_2"]
        )
        self.assertTrue(
            all(
                record["audit_schema_version"]
                == run_logging.COORDINATOR_AUDIT_SCHEMA_VERSION
                for record in records
            )
        )

    def test_a_published_record_is_never_rewritten_by_a_later_write(self) -> None:
        published, _ = run_logging.append_coordinator_audit_record(
            "run_audit", run_logging.EVENT_DELIVERY_PROCESSED,
            {"delivery_id": "dlv_0"}, base=self.artifact_dir,
        )
        record_path = published / run_logging.COORDINATOR_AUDIT_RECORD_FILENAME
        before = record_path.read_bytes()

        run_logging.append_coordinator_audit_record(
            "run_audit", run_logging.EVENT_DELIVERY_ACKNOWLEDGED,
            {"delivery_id": "dlv_0"}, base=self.artifact_dir,
        )

        self.assertEqual(record_path.read_bytes(), before)

    def test_an_unknown_event_is_refused_before_anything_is_published(self) -> None:
        with self.assertRaises(run_logging.CoordinatorAuditError):
            run_logging.append_coordinator_audit_record(
                "run_audit", "delivery_probably_fine", {}, base=self.artifact_dir
            )
        self.assertEqual(
            run_logging.read_coordinator_audit("run_audit", base=self.artifact_dir), []
        )

    def test_an_audit_write_failure_fails_closed_instead_of_being_swallowed(self) -> None:
        """BUGFIX-I1-G1-2. This family is not an ordinary log.

        Section 9's "a logging failure never changes a lifecycle decision" holds for
        ORCHESTRATOR_LOG/TIMING_LOG, which only humans read. This audit is the ONLY
        source a restarted Coordinator can rebuild the delivery ledger from, so
        swallowing a publication failure lets processing and acknowledgement continue
        over an audit that no longer describes them -- and the next process then reads
        an already-handled delivery as brand new. The failure is recorded AND raised.
        """
        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        harness = self.build(recorder)

        with patch.object(
            run_logging, "append_coordinator_audit_record", side_effect=OSError("full disk")
        ):
            with self.assertRaises(OrcaRuntimeError) as raised:
                self.settle(
                    harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p"
                )

        self.assertIn("fails closed", str(raised.exception))
        self.assertIn("full disk", str(raised.exception))
        # Recorded as well as raised, and no acknowledgement went out over an audit
        # that could not record it.
        self.assertTrue(harness._logging_errors)
        self.assertEqual(recorder.acked, [])

    def test_an_unreadable_audit_directory_fails_closed_before_a_waiter_is_armed(self) -> None:
        """The read side of the same rule, at the coarsest granularity.

        This one stubs the replay to raise, which covers only the caller's catch
        branch -- an audit whose DIRECTORY cannot be listed at all. It is deliberately
        not the whole story: ``CorruptCoordinatorAuditTests`` below damages a real
        published record and drives the same refusal through the real reader and the
        real fold, because that is the path a corrupt record actually takes.
        """
        successor = self.build(ScriptedExec([]))

        with patch.object(
            run_logging, "replay_delivery_ledger", side_effect=OSError("unreadable")
        ):
            with self.assertRaises(OrcaRuntimeError) as raised:
                successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("fails closed", str(raised.exception))
        # It failed BEFORE arming anything: no `check --wait` was ever issued.
        self.assertEqual(successor._exec_orca.waits, 0)


class CorruptCoordinatorAuditTests(OS44TestCase):
    """FINAL-R1. A record that is present but unreadable is damage, never absence.

    ``read_coordinator_audit()`` keeps an unparseable record as an ``_unreadable``
    sentinel rather than dropping it, exactly so a corrupt record cannot masquerade as
    an absence. The fold has to honour that. Skipping the sentinel turned it back into
    the absence the reader refused to produce, an absence reads as "this delivery was
    never processed", and a restarted Coordinator then adopts a redelivered,
    already-settled delivery as its new waiter's result -- this ticket's own defect,
    one layer down.

    Every test here damages a REAL published record. None of them patches the reader or
    the replay: a mock proves the caller catches what it is handed, which is precisely
    what stayed green while the production path failed open.
    """

    def corrupt(self, sequence: int) -> Path:
        """Make one published record.json genuinely unparseable, in place."""
        path = self.audit_record_path(sequence)
        path.write_text("{ not json at all", encoding="utf-8")
        return path

    def settled_run(self) -> OrcaRuntimeHarness:
        """A run whose audit really records a processed-and-acknowledged delivery."""
        harness = self.build(
            ScriptedExec(
                [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
            )
        )
        self.settle(harness, task_id=PLAN_TASK, dispatch_id=PLAN_DISPATCH, terminal="term_p")
        return harness

    def test_the_reviewers_probe_raises_instead_of_returning_an_empty_ledger(self) -> None:
        """The exact probe the final review ran, as a standing regression.

        Before the fix this asserted-away sequence was the observed behaviour:
        ``read_coordinator_audit(...) == [{"sequence": 0, "_unreadable": ...}]`` and
        then ``replay_delivery_ledger(...) == {}``, with nothing raised.
        """
        run_logging.append_coordinator_audit_record(
            "run_probe",
            run_logging.EVENT_DELIVERY_PROCESSED,
            {"delivery_id": PLAN_DELIVERY, "task_id": PLAN_TASK},
            base=self.artifact_dir,
        )
        self.audit_record_path(0, run_id="run_probe").write_text(
            "{ not json at all", encoding="utf-8"
        )

        records = run_logging.read_coordinator_audit("run_probe", base=self.artifact_dir)
        self.assertEqual(len(records), 1)
        self.assertIn("_unreadable", records[0])

        with self.assertRaises(run_logging.CoordinatorAuditError) as raised:
            run_logging.replay_delivery_ledger("run_probe", base=self.artifact_dir)

        self.assertIn("could not be read", str(raised.exception))

    def test_a_corrupt_record_stops_the_wait_path_before_any_check_is_issued(self) -> None:
        """The integration this family exists for: no `check --wait` ever happens.

        The successor re-arms the waiter for the SAME dispatch the predecessor already
        settled, and the mailbox is holding that delivery again. If the fold returned a
        truncated ledger the redelivery would look new and be adopted; instead the run
        stops above the command that would have fetched it.
        """
        self.settled_run()
        self.corrupt(self.sequence_of(run_logging.EVENT_DELIVERY_PROCESSED))

        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        successor = self.build(recorder)

        with self.assertRaises(OrcaRuntimeError) as raised:
            successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("fails closed", str(raised.exception))
        self.assertIn("could not be read", str(raised.exception))
        self.assertEqual(recorder.waits, 0)
        self.assertEqual(recorder.acked, [])
        self.assertEqual(successor.lifecycle_commands(PLAN_DISPATCH), [])
        self.assertNotIn(PLAN_DELIVERY, successor._deliveries)

    def test_a_corrupt_record_stops_resume_run_before_it_returns(self) -> None:
        """The other production entry point a restarted Coordinator takes."""
        self.settled_run()
        self.corrupt(self.sequence_of(run_logging.EVENT_DELIVERY_ACKNOWLEDGED))

        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        with self.assertRaises(OrcaRuntimeError) as raised:
            self.successor(recorder)

        self.assertIn("fails closed", str(raised.exception))
        self.assertEqual(recorder.waits, 0)

    def test_a_corrupt_record_among_intact_ones_is_not_folded_around(self) -> None:
        """A partially readable audit is a truncated history, which is the same lie.

        The `delivery_processed` record survives here, so a fold that skipped only the
        damaged record would return a plausible-looking ledger -- and a plausible ledger
        missing the acknowledgement is the state that lets a replay be re-adopted.
        """
        self.settled_run()
        damaged = self.sequence_of(run_logging.EVENT_DELIVERY_ACKNOWLEDGED)
        self.corrupt(damaged)

        with self.assertRaises(run_logging.CoordinatorAuditError):
            run_logging.replay_delivery_ledger(
                "run_c2166e75bb02", base=self.artifact_dir
            )

        # The refusal is about the ONE damaged record, not about the audit having
        # become unopenable: every neighbour still parses, and the sentinel sits on
        # exactly the sequence that was corrupted.
        records = run_logging.read_coordinator_audit(
            "run_c2166e75bb02", base=self.artifact_dir
        )
        self.assertEqual(
            [record["sequence"] for record in records if "_unreadable" in record],
            [damaged],
        )
        self.assertIn(
            run_logging.EVENT_DELIVERY_PROCESSED,
            [record.get("event") for record in records],
        )

    def test_a_record_whose_identity_was_lost_is_refused_not_skipped(self) -> None:
        """Parseable JSON is not the same as foldable JSON.

        A `delivery_processed` record with no delivery id says something about a
        delivery the fold can no longer name. Skipping it drops that delivery from the
        recovered history exactly as the unreadable sentinel did.
        """
        self.settled_run()
        sequence = self.sequence_of(run_logging.EVENT_DELIVERY_PROCESSED)
        path = self.audit_record_path(sequence)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("delivery_id")
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(run_logging.CoordinatorAuditError) as raised:
            run_logging.replay_delivery_ledger(
                "run_c2166e75bb02", base=self.artifact_dir
            )

        self.assertIn("no delivery id", str(raised.exception))

    def test_a_record_filed_under_another_run_is_refused(self) -> None:
        """Identity, not just readability: a misfiled record describes another run."""
        self.settled_run()
        sequence = self.sequence_of(run_logging.EVENT_DELIVERY_PROCESSED)
        path = self.audit_record_path(sequence)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["run_id"] = "run_somewhere_else"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(run_logging.CoordinatorAuditError) as raised:
            run_logging.replay_delivery_ledger(
                "run_c2166e75bb02", base=self.artifact_dir
            )

        self.assertIn("run_somewhere_else", str(raised.exception))

    def test_a_record_declaring_an_unknown_schema_is_refused(self) -> None:
        """The writer stamps one schema version; another one cannot be folded safely."""
        self.settled_run()
        sequence = self.sequence_of(run_logging.EVENT_DELIVERY_PROCESSED)
        path = self.audit_record_path(sequence)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["audit_schema_version"] = "9.9"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(run_logging.CoordinatorAuditError) as raised:
            run_logging.replay_delivery_ledger(
                "run_c2166e75bb02", base=self.artifact_dir
            )

        self.assertIn("9.9", str(raised.exception))

    def test_a_record_that_is_valid_json_but_not_an_object_is_refused(self) -> None:
        """The reader's second sentinel reaches the same refusal as the first."""
        self.settled_run()
        self.audit_record_path(
            self.sequence_of(run_logging.EVENT_DELIVERY_PROCESSED)
        ).write_text("[1, 2, 3]", encoding="utf-8")

        with self.assertRaises(run_logging.CoordinatorAuditError) as raised:
            run_logging.replay_delivery_ledger(
                "run_c2166e75bb02", base=self.artifact_dir
            )

        self.assertIn("not a JSON object", str(raised.exception))

    def test_an_absent_audit_is_an_empty_history_and_never_a_refusal(self) -> None:
        """The boundary the fail-closed rule must not cross.

        No audit at all is a legitimate state -- a run that has processed nothing. Only
        a record that EXISTS and cannot be folded is damage. Conflating the two would
        make every genuinely new run unable to arm its first waiter.
        """
        self.assertEqual(
            run_logging.read_coordinator_audit("run_never_written", base=self.artifact_dir),
            [],
        )
        self.assertEqual(
            run_logging.replay_delivery_ledger("run_never_written", base=self.artifact_dir),
            {},
        )

        recorder = ScriptedExec(
            [delivery(PLAN_DELIVERY, worker_done_message(PLAN_TASK, PLAN_DISPATCH))]
        )
        successor = self.build(recorder, run_id="run_never_written")
        successor.register_terminal(
            "term_p", role="active_worker", origin="self_created",
            intended_role="phase_worker", owner_dispatch_id=PLAN_DISPATCH,
        )

        done, delivery_id = successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(delivery_id, PLAN_DELIVERY)
        self.assertEqual(json.loads(done["payload"])["dispatchId"], PLAN_DISPATCH)
        self.assertEqual(recorder.waits, 1)


class PostWireAckCrashRecoveryTests(OS44TestCase):
    """BUGFIX-I3-MAJOR-1. The window between the wire ack and its audit record.

    ``orca orchestration check --ack`` is accepted -- and the delivery consumed --
    before this process can publish anything about it. A process that dies in that
    window leaves an audit that ends at ``delivery_settled`` while Orca no longer holds
    the delivery, so there is no redelivery to recover on. The previous round's
    successor marked the row ``recovered`` and ``unacknowledged_deliveries()`` excluded
    recovered rows, so it carried on having never recorded the acknowledgement outcome
    it owed.

    Every test below therefore starts from a REAL published audit and a FRESH process
    with an EMPTY mailbox: no redelivery, no same-process ``settle_attempt()`` re-entry,
    and no patching of the recovery path under test.
    """

    def publish(self, *events: str, run_id: str = "run_c2166e75bb02") -> None:
        """Write the audit a crashed predecessor would have left, for real."""
        for event in events:
            run_logging.append_coordinator_audit_record(
                run_id,
                event,
                {
                    "delivery_id": PLAN_DELIVERY,
                    "task_id": PLAN_TASK,
                    "dispatch_id": PLAN_DISPATCH,
                },
                base=self.artifact_dir,
            )

    def audit_ends_at_settled(self) -> None:
        """The reviewer's exact shape: processed, claimed, settled, and nothing more."""
        self.publish(
            run_logging.EVENT_DELIVERY_PROCESSED,
            run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED,
            run_logging.EVENT_DELIVERY_SETTLED,
        )

    def audit_ends_at_ack_intent(self) -> None:
        """One record later: the wire ack was issued and its outcome never recorded."""
        self.audit_ends_at_settled()
        self.publish(run_logging.EVENT_DELIVERY_ACK_INTENT)

    def test_a_fresh_process_over_an_audit_ending_at_settled_closes_the_ack(self) -> None:
        """The finding, with the mailbox empty so redelivery cannot be the answer."""
        self.audit_ends_at_settled()
        recorder = ScriptedExec([])  # empty mailbox: nothing will ever be redelivered

        successor = self.successor(recorder)

        row = successor._deliveries[PLAN_DELIVERY]
        self.assertEqual(row[DELIVERY_STATE_FIELD], DELIVERY_STATE_ACK_RECONCILED)
        reconciled = [
            record
            for record in self.audit(successor)
            if record["event"] == run_logging.EVENT_DELIVERY_ACK_RECONCILED
        ]
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["delivery_id"], PLAN_DELIVERY)
        self.assertEqual(reconciled[0]["dispatch_id"], PLAN_DISPATCH)
        # Deterministic and terminal: nothing is outstanding and nothing was guessed.
        self.assertEqual(successor.unacknowledged_deliveries(), ())
        self.assertEqual(successor.delivery_obligations(), {})

    def test_the_same_holds_when_the_audit_ends_at_the_ack_intent(self) -> None:
        """The other side of the wire command, recorded rather than inferred."""
        self.audit_ends_at_ack_intent()
        recorder = ScriptedExec([])

        successor = self.successor(recorder)

        self.assertEqual(
            successor._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_ACK_RECONCILED,
        )
        self.assertEqual(successor.unacknowledged_deliveries(), ())

    def test_the_reconciliation_repeats_no_lifecycle_action(self) -> None:
        """Closing the acknowledgement must not re-settle, re-release or re-dispatch."""
        self.audit_ends_at_settled()
        recorder = ScriptedExec([])

        successor = self.successor(recorder)

        self.assertEqual(recorder.acked, [PLAN_DELIVERY])
        self.assertEqual(successor.lifecycle_commands(PLAN_DISPATCH), [])
        for forbidden in ("worker-release", "worker-start", "task-create", "dispatch"):
            self.assertNotIn(forbidden, recorder.verbs)

    def test_the_documented_not_outstanding_answer_is_terminal(self) -> None:
        """Orca states that it holds no such delivery for this Run: THAT is terminal.

        The runtime's `acknowledgeRunDelivery` throws `stale_delivery` when no delivery
        row for this Run carries the id, which is an authoritative absence: nothing will
        be redelivered under it. The outcome is recorded under its own reason code and
        the obligation is closed, because waiting for a redelivery that cannot arrive is
        the very failure being fixed.

        This test previously fed three GENERIC `ack_rejected` failures whose message was
        literally `transient` and asserted the same terminal outcome, which asserted the
        fail-open behaviour rather than this one. The generic case is now
        `test_an_unclassified_ack_failure_preserves_the_obligation` and expects the
        opposite.
        """
        self.audit_ends_at_ack_intent()
        recorder = ScriptedExec(
            [],
            ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS},
            ack_error_code="stale_delivery",
        )

        successor = self.successor(recorder)

        reconciled = [
            record
            for record in self.audit(successor)
            if record["event"] == run_logging.EVENT_DELIVERY_ACK_RECONCILED
        ]
        self.assertEqual(
            [row["reason_code"] for row in reconciled],
            [ACK_RECONCILED_NOT_OUTSTANDING],
        )
        self.assertEqual(
            successor._deliveries[PLAN_DELIVERY][DELIVERY_STATE_FIELD],
            DELIVERY_STATE_ACK_RECONCILED,
        )
        self.assertEqual(successor.unacknowledged_deliveries(), ())

    def test_the_authoritative_answer_is_not_retried_after_it_arrives(self) -> None:
        """An authoritative absence is an answer, not a failure to retry past."""
        self.audit_ends_at_ack_intent()
        recorder = ScriptedExec(
            [],
            ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS},
            ack_error_code="stale_delivery",
        )

        self.successor(recorder)

        self.assertEqual(
            [command for command in recorder.commands if "--ack" in command].__len__(), 1
        )

    def test_an_unclassified_ack_failure_preserves_the_obligation(self) -> None:
        """A generic failure is not evidence Orca consumed the delivery.

        BUGFIX-I3-MAJOR-1A. Three transient rejections say nothing about whether the
        delivery is outstanding, so nothing may be closed on them: the reconciliation
        fails closed, publishes its failure under `reconcile_unresolved`, and the row
        stays an open obligation that every downstream gate keeps refusing.
        """
        self.audit_ends_at_ack_intent()
        recorder = ScriptedExec([], ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS})

        harness, error = self.refused_successor(recorder)

        self.assertIn("not evidence the runtime consumed the delivery", str(error))
        self.assertEqual(
            [
                record["reason_code"]
                for record in self.audit(harness)
                if record["event"] == run_logging.EVENT_DELIVERY_ACK_FAILED
            ],
            [ACK_RECONCILE_UNRESOLVED],
        )
        self.assertEqual(
            [
                record
                for record in self.audit(harness)
                if record["event"] == run_logging.EVENT_DELIVERY_ACK_RECONCILED
            ],
            [],
        )
        self.assertEqual(
            harness.delivery_obligations(),
            {PLAN_DELIVERY: quiescence.OBLIGATION_ACK_RECONCILE},
        )
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))

    def test_a_fenced_consumer_is_not_proof_of_absence_either(self) -> None:
        """`consumer_fenced` says who owns the mailbox, not whether the delivery is gone."""
        self.audit_ends_at_settled()
        recorder = ScriptedExec(
            [],
            ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS},
            ack_error_code="consumer_fenced",
        )

        harness, _ = self.refused_successor(recorder)

        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))

    def test_an_unresolved_reconciliation_refuses_the_next_waiter(self) -> None:
        """Fail closed means the successor cannot proceed, not merely that it logged."""
        self.audit_ends_at_settled()
        recorder = ScriptedExec([], ack_failures={PLAN_DELIVERY: ACK_MAX_ATTEMPTS})
        harness, _ = self.refused_successor(recorder)

        with self.assertRaises(OrcaRuntimeError) as raised:
            harness.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("was processed and not acknowledged", str(raised.exception))

    def test_the_successor_can_then_arm_a_waiter_and_end_its_turn(self) -> None:
        """A closed obligation is closed for every gate, not only for the pending list."""
        self.audit_ends_at_settled()
        recorder = ScriptedExec([])
        successor = self.successor(recorder)

        with self.assertRaises(OrcaRuntimeError) as raised:
            successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)
        self.assertIn("timed out", str(raised.exception))
        successor.verify_quiescence("COMPLETED")

        self.assertIn(run_logging.EVENT_QUIESCENCE_VERIFIED, self.events(successor))

    def test_the_obligation_is_visible_before_it_is_closed_never_excluded(self) -> None:
        """2e: an incomplete acknowledgement must be CLOSED, not filtered out.

        Restoring the ledger without reconciling leaves the row exactly where the
        previous round hid it -- so the assertion is that it is REPORTED there, and that
        it is reported as an obligation a successor must discharge rather than as one
        that will resolve itself.
        """
        self.audit_ends_at_settled()
        harness = self.build(ScriptedExec([]))
        harness._deliveries = {}
        harness._deliveries_restored_for = ""

        harness.restore_delivery_ledger()

        self.assertEqual(
            harness.delivery_obligations(),
            {PLAN_DELIVERY: quiescence.OBLIGATION_ACK_RECONCILE},
        )
        self.assertEqual(harness.unacknowledged_deliveries(), (PLAN_DELIVERY,))
        with self.assertRaises(OrcaRuntimeError):
            harness.verify_quiescence("COMPLETED")

    def test_a_predecessor_that_never_reached_the_ack_still_waits_for_redelivery(self) -> None:
        """The tension named in 2e, held from the other side.

        A row consumed with nothing settled, nothing claimed and no ack issued IS
        resolved by the redelivery Orca replays until acknowledged -- so counting it
        would refuse the waiter that redelivery must arrive on. It stands aside from the
        waiter gate by the NAME of its obligation, and it is still reported.
        """
        self.publish(run_logging.EVENT_DELIVERY_PROCESSED)
        recorder = ScriptedExec([])

        successor = self.successor(recorder)

        self.assertEqual(
            successor.delivery_obligations(),
            {PLAN_DELIVERY: quiescence.OBLIGATION_AWAITING_REDELIVERY},
        )
        self.assertEqual(successor.unacknowledged_deliveries(), ())
        self.assertEqual(recorder.acked, [])
        self.assertNotIn(
            run_logging.EVENT_DELIVERY_ACK_RECONCILED, self.events(successor)
        )


class FakeOrca:
    """Orca's `--json` surface for the turn-end boundary: Tasks, Dispatches, gates."""

    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.workers: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self.failing: set[str] = set()
        self.commands: list[tuple[str, ...]] = []

    def dispatch(self, dispatch_id: str, task_id: str, *, running: bool = True) -> None:
        self.tasks.append(
            {"id": task_id, "status": "dispatched", "deps": "[]", "dispatch_id": dispatch_id}
        )
        self.workers.append(
            {
                "dispatchId": dispatch_id,
                "taskId": task_id,
                "dispatchStatus": "dispatched" if running else "completed",
                "workerState": "ready" if running else "settled",
            }
        )

    def task(self, task_id: str, status: str, *deps: str) -> None:
        self.tasks.append({"id": task_id, "status": status, "deps": json.dumps(list(deps))})

    def uncorroborated_dispatch(self, dispatch_id: str, task_id: str) -> None:
        """A Task Orca still calls `dispatched` with NO worker row to back it.

        BUGFIX-I3-CRITICAL-1A. The Task status alone proves nothing is running: it is
        the one field a crashed or fenced Coordinator leaves behind exactly as it was.
        """
        self.tasks.append(
            {"id": task_id, "status": "dispatched", "deps": "[]", "dispatch_id": dispatch_id}
        )

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        args = tuple(args)
        self.commands.append(args)
        verb = args[1] if len(args) > 1 else args[0]
        if verb in self.failing:
            return 1, json.dumps(
                {"ok": False, "error": {"code": "run_not_found", "message": verb}}
            )
        payload = {
            "task-list": {"tasks": list(self.tasks)},
            "worker-list": {"workers": list(self.workers)},
            "gate-list": {"gates": list(self.gates)},
        }.get(verb, {})
        return 0, json.dumps({"ok": True, "result": payload})


class TurnBoundaryCliDriver:
    """The two helpers that drive the real ``turn-end`` CLI over one run.

    A mixin rather than a base test case so the checkpoint-authority class below can
    drive the same command without inheriting -- and re-running -- every test written
    against a run that has no checkpoint store.
    """

    RUN = "run_c2166e75bb02"

    def cli(self, *argv: str, orca: FakeOrca) -> int:
        """The real CLI, stdout captured so a suite run stays readable."""
        self.printed = io.StringIO()
        with patch.object(turn_boundary, "_default_runner", orca):
            with redirect_stdout(self.printed):
                return launcher.run_cli(
                    ["turn-end", "--run-id", self.RUN, "--artifact-base",
                     str(self.artifact_dir), *argv]
                )

    def records(self) -> list[dict]:
        return [
            record
            for record in run_logging.read_coordinator_audit(
                self.RUN, base=self.artifact_dir
            )
            if record["event"]
            in (
                run_logging.EVENT_QUIESCENCE_VERIFIED,
                run_logging.EVENT_QUIESCENCE_VIOLATION,
            )
        ]

    def arm_pause_record(self) -> None:
        """The OS-31 durable pause record, written in its real published shape."""
        record_path = (
            self.artifact_dir / "artifacts" / "runs" / self.RUN
            / turn_boundary.PAUSE_RECORD_FILENAME
        )
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(
            json.dumps(
                {
                    "schema_version": turn_boundary.PAUSE_RECORD_SCHEMA_VERSION,
                    "record": {"run_id": self.RUN, "status": "WAITING_FOR_INPUT"},
                }
            ),
            encoding="utf-8",
        )


class TurnBoundaryCliTests(TurnBoundaryCliDriver, OS44TestCase):
    """BUGFIX-I3-CRITICAL-1. The invocable boundary, driven through the real CLI.

    The PR #31 CRITICAL is that OS-44's guarantee lived in ``finish()`` -- a harness
    completion path -- plus prose. These tests drive ``run_workflow.py turn-end``: the
    same argument parsing, the same derivation from Orca's own Task and Dispatch
    listings, the same exit codes a live Coordinator gets.

    What they can prove is the boundary's behaviour when it is INVOKED, and that a
    skipped invocation stays deterministically detectable afterwards. They cannot prove
    a model was forced to invoke it, because no such interception exists on this
    platform; see ``turn_boundary``'s module docstring.
    """

    def test_the_recorded_stall_is_refused_at_the_boundary(self) -> None:
        """``run_c2166e75bb02``: ANALYSIS settled, PLAN not created, nothing active.

        Orca holds one completed Task and no Dispatch. Nothing can wake the run, and the
        turn is refused with the code that names the state rather than reported on.
        """
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_REFUSED)

        records = self.records()
        self.assertEqual(
            [record["event"] for record in records],
            [run_logging.EVENT_QUIESCENCE_VIOLATION],
        )
        self.assertEqual(
            records[0]["reason_code"], quiescence.QUIESCENCE_IDLE_NON_TERMINAL
        )

    def test_a_created_but_undispatched_next_task_is_refused(self) -> None:
        """The other half of the same gap: the PLAN Task exists and nothing runs it."""
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        orca.task(PLAN_TASK, "pending", ANALYSIS_TASK)

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_REFUSED)

        self.assertEqual(
            self.records()[0]["reason_code"],
            quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED,
        )

    def test_a_dispatched_task_with_no_worker_row_is_not_an_active_wait(self) -> None:
        """BUGFIX-I3-CRITICAL-1A. Missing evidence is not evidence.

        Orca reports the Task as `dispatched` and reports no worker for it. The previous
        round counted exactly this as an active wait -- one stale Task status was then
        enough to call a dead run quiescent, which is the fail-open shape OS-44 exists
        to remove. It is now recovery work, so the turn is refused and the run's audit
        records zero active dispatches.
        """
        orca = FakeOrca()
        orca.uncorroborated_dispatch(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(self.cli("--json", orca=orca), turn_boundary.EXIT_REFUSED)

        summary = json.loads(self.printed.getvalue())
        self.assertEqual(summary["active_dispatches"], [])
        self.assertEqual(
            summary["runnable_actions"],
            [f"{turn_boundary.ACTION_RECONCILE_DISPATCH}:{PLAN_TASK}"],
        )
        record = self.records()[0]
        self.assertEqual(record["event"], run_logging.EVENT_QUIESCENCE_VIOLATION)
        self.assertEqual(record["active_dispatches"], 0)

    def test_an_uncorroborated_dispatch_cannot_support_a_declared_completion(self) -> None:
        """The same branch through the rest-claim path: nothing corroborates rest."""
        orca = FakeOrca()
        orca.uncorroborated_dispatch(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(
            self.cli("--declare", "COMPLETED", orca=orca), turn_boundary.EXIT_REFUSED
        )

        self.assertEqual(
            self.records()[0]["reason_code"],
            quiescence.QUIESCENCE_UNSUPPORTED_REST_CLAIM,
        )

    def test_a_worker_row_for_a_different_dispatch_proves_nothing_about_this_one(self) -> None:
        """Evidence has to be evidence FOR the dispatch in question.

        One genuinely live Dispatch and one Task nothing corroborates. The turn may end
        -- the live wait can still wake the run, which is what the contract calls rest --
        but the uncorroborated Task is counted as recovery WORK, not as a second active
        wait. That distinction is the whole finding: with the live Dispatch removed, the
        same run has nothing to wake it and the turn is refused
        (`test_a_dispatched_task_with_no_worker_row_is_not_an_active_wait`).
        """
        orca = FakeOrca()
        orca.dispatch(ANALYSIS_DISPATCH, ANALYSIS_TASK)
        orca.uncorroborated_dispatch(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(self.cli("--json", orca=orca), turn_boundary.EXIT_QUIESCENT)

        summary = json.loads(self.printed.getvalue())
        self.assertEqual(summary["active_dispatches"], [ANALYSIS_DISPATCH])
        self.assertEqual(
            summary["runnable_actions"],
            [f"{turn_boundary.ACTION_RECONCILE_DISPATCH}:{PLAN_TASK}"],
        )

    def test_a_task_blocked_on_an_unfinished_dependency_is_not_runnable(self) -> None:
        """Runnable means unblocked. A dependent Task is not work the turn skipped."""
        orca = FakeOrca()
        orca.dispatch(ANALYSIS_DISPATCH, ANALYSIS_TASK)
        orca.task(PLAN_TASK, "pending", ANALYSIS_TASK)

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_QUIESCENT)

        record = self.records()[0]
        self.assertEqual(record["event"], run_logging.EVENT_QUIESCENCE_VERIFIED)
        self.assertEqual(record["active_dispatches"], 1)

    def test_continuous_execution_from_phase_pass_to_the_next_active_wait(self) -> None:
        """The whole point: refuse, act, and only then may the turn end.

        The same command over the same run, three times, as the Coordinator does the
        work it was refused for. Nothing about the refusal is advisory -- the exit code
        changes only because the run's own state changed.
        """
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_REFUSED)

        orca.task(PLAN_TASK, "pending", ANALYSIS_TASK)  # the Coordinator creates it
        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_REFUSED)

        orca.tasks = [task for task in orca.tasks if task["id"] != PLAN_TASK]
        orca.dispatch(PLAN_DISPATCH, PLAN_TASK)         # ...and dispatches it
        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_QUIESCENT)

        self.assertEqual(
            [record["reason_code"] for record in self.records()],
            [
                quiescence.QUIESCENCE_IDLE_NON_TERMINAL,
                quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED,
                quiescence.QUIESCENCE_OK,
            ],
        )

    def test_a_declared_rest_state_the_run_does_not_support_is_refused(self) -> None:
        """1e. A status a model typed is not evidence; the run's own state is."""
        orca = FakeOrca()
        orca.dispatch(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(
            self.cli("--declare", "COMPLETED", orca=orca), turn_boundary.EXIT_REFUSED
        )

        self.assertEqual(
            self.records()[0]["reason_code"],
            quiescence.QUIESCENCE_UNSUPPORTED_REST_CLAIM,
        )

    def test_a_declared_human_wait_with_nothing_armed_is_refused(self) -> None:
        """A wait that exists only in the response text cannot wake the run."""
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")

        self.assertEqual(
            self.cli("--declare", "WAITING_FOR_INPUT", orca=orca),
            turn_boundary.EXIT_REFUSED,
        )
        self.assertEqual(
            self.records()[0]["reason_code"],
            quiescence.QUIESCENCE_UNSUPPORTED_REST_CLAIM,
        )

    def test_a_declared_human_wait_backed_by_a_durable_pause_record_is_accepted(self) -> None:
        """...and the same declaration IS accepted once the pause is actually armed."""
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        self.arm_pause_record()

        self.assertEqual(
            self.cli("--declare", "WAITING_FOR_INPUT", orca=orca),
            turn_boundary.EXIT_QUIESCENT,
        )
        self.assertEqual(
            self.records()[0]["event"], run_logging.EVENT_QUIESCENCE_VERIFIED
        )

    def test_a_paused_run_needs_no_declaration_at_all(self) -> None:
        """The pause record is an ARTEFACT, so it answers the status question itself.

        A Coordinator that forgets to declare anything over a genuinely paused run must
        not have that run reported as ACTIVE-and-idle: the durable record already says
        what the run is doing.
        """
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        self.arm_pause_record()

        self.assertEqual(self.cli("--json", orca=orca), turn_boundary.EXIT_QUIESCENT)

        self.assertEqual(
            self.records()[0]["run_status"], quiescence.WAITING_FOR_INPUT
        )

    def test_an_open_decision_gate_also_proves_the_wait(self) -> None:
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        orca.gates.append({"id": "gate_1", "status": "pending"})

        self.assertEqual(
            self.cli("--declare", "WAITING_FOR_INPUT", orca=orca),
            turn_boundary.EXIT_QUIESCENT,
        )

    def test_an_outstanding_delivery_refuses_the_turn_whatever_is_declared(self) -> None:
        """The delivery half of OS-44, read out of the durable audit by a third party.

        `delivery_5c541e7fe1bd` was processed and never acknowledged, and the turn ended
        anyway. The boundary refuses that turn from the audit alone, with an active
        dispatch present and COMPLETED declared -- neither of which outranks an open
        delivery obligation.
        """
        run_logging.append_coordinator_audit_record(
            self.RUN,
            run_logging.EVENT_DELIVERY_PROCESSED,
            {"delivery_id": STALE_DELIVERY, "task_id": ANALYSIS_TASK,
             "dispatch_id": ANALYSIS_DISPATCH},
            base=self.artifact_dir,
        )
        orca = FakeOrca()
        orca.dispatch(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(
            self.cli("--declare", "COMPLETED", orca=orca), turn_boundary.EXIT_REFUSED
        )

        record = self.records()[0]
        self.assertEqual(
            record["reason_code"], quiescence.QUIESCENCE_UNACKNOWLEDGED_DELIVERY
        )
        self.assertEqual(record["delivery_id"], STALE_DELIVERY)

    def test_a_settled_but_unacknowledged_delivery_blocks_the_turn(self) -> None:
        """The post-wire-ack row is an obligation, not a redelivery to wait for."""
        for event in (
            run_logging.EVENT_DELIVERY_PROCESSED,
            run_logging.EVENT_DELIVERY_SETTLED,
        ):
            run_logging.append_coordinator_audit_record(
                self.RUN,
                event,
                {"delivery_id": PLAN_DELIVERY, "task_id": PLAN_TASK,
                 "dispatch_id": PLAN_DISPATCH},
                base=self.artifact_dir,
            )
        orca = FakeOrca()
        orca.dispatch(ANALYSIS_DISPATCH, ANALYSIS_TASK)

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_REFUSED)

        self.assertEqual(
            self.records()[0]["reason_code"],
            quiescence.QUIESCENCE_UNACKNOWLEDGED_DELIVERY,
        )

    def test_a_declared_next_node_can_only_add_work_never_remove_it(self) -> None:
        orca = FakeOrca()
        orca.dispatch(ANALYSIS_DISPATCH, ANALYSIS_TASK)

        self.assertEqual(
            self.cli("--declare", "COMPLETED", "--next-node", "PREPARE_WORKER",
                     orca=orca),
            turn_boundary.EXIT_REFUSED,
        )

    def test_unreachable_run_state_reports_no_verdict_at_all(self) -> None:
        """"I could not find out" is not "the turn may end", and not a violation either."""
        orca = FakeOrca()
        orca.failing.add("task-list")

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_UNAVAILABLE)

        self.assertEqual(self.records(), [])

    def test_a_corrupt_audit_reports_no_verdict_rather_than_a_pass(self) -> None:
        """The delivery history is the only source of the run's obligations."""
        run_logging.append_coordinator_audit_record(
            self.RUN,
            run_logging.EVENT_DELIVERY_PROCESSED,
            {"delivery_id": PLAN_DELIVERY},
            base=self.artifact_dir,
        )
        published = (
            self.artifact_dir / "artifacts" / "runs" / self.RUN
            / run_logging.COORDINATOR_AUDIT_DIRNAME
            / run_logging.coordinator_audit_sequence_key(0)
            / run_logging.COORDINATOR_AUDIT_RECORD_FILENAME
        )
        published.write_text("{ not json", encoding="utf-8")
        orca = FakeOrca()
        orca.dispatch(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_UNAVAILABLE)

    def test_the_verdict_is_durable_so_a_skipped_check_is_an_observable_absence(self) -> None:
        """The honest scope: enforcement when invoked, detection when it is not.

        Nothing can make a language model call this command. What the record buys is
        that a run whose turn ended without it has NO verdict for that turn, and that
        the same command run later -- by a successor, an operator, a scheduled check --
        reaches the identical verdict from durable state with no live process.
        """
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        self.assertEqual(self.records(), [])

        first = self.cli("--json", orca=orca)
        later = self.cli("--json", orca=orca)

        self.assertEqual(first, turn_boundary.EXIT_REFUSED)
        self.assertEqual(later, turn_boundary.EXIT_REFUSED)
        self.assertEqual(
            [record["reason_code"] for record in self.records()],
            [quiescence.QUIESCENCE_IDLE_NON_TERMINAL] * 2,
        )


@unittest.skipUnless(_langgraph_available(), "requires the pinned langgraph runtime")
class TurnBoundaryCheckpointAuthorityTests(TurnBoundaryCliDriver, OS44TestCase):
    """BUGFIX-I3-CRITICAL-1A. Run status and next node bound to the DURABLE checkpoint.

    The review's requirement was that these two are derived from authoritative run and
    runtime state rather than supplied by the caller. When the run has an OS-40
    checkpoint store, they are: the status from the committed ``terminal_status`` /
    ``run_lifecycle`` and the next node from the engine's own ``routing.route``. These
    tests write a real checkpoint through ``FileCheckpointSaver`` -- the same writer the
    engine uses -- and drive the same ``turn-end`` command as ``TurnBoundaryCliTests``,
    whose own cases cover the run that has no checkpoint store at all.
    """

    def setUp(self) -> None:
        super().setUp()
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver

        self.saver_class = FileCheckpointSaver
        self.store = (
            self.artifact_dir / "artifacts" / "runs" / self.RUN / ".workflow_checkpoints.json"
        )
        self.store.parent.mkdir(parents=True, exist_ok=True)

    def workflow_state(self, **overrides: Any) -> dict[str, Any]:
        from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
        from scripts.deterministic_workflow.state import initial_state

        state = dict(
            initial_state(
                run_id=self.RUN,
                thread_id="thread_main",
                phases=("ANALYSIS", "PLAN"),
                capabilities=BASE_CAPABILITIES,
            )
        )
        state.update(overrides)
        return state

    def commit(self, values: dict[str, Any], *, thread_id: str = "thread_main") -> None:
        """One committed checkpoint, written exactly as the engine writes one."""
        checkpoint = {
            "v": 1,
            "id": f"chk_{thread_id}",
            "ts": "2026-01-01T00:00:00Z",
            "channel_values": dict(values),
            "channel_versions": {key: 1 for key in values},
            "versions_seen": {},
            "pending_sends": [],
        }
        self.saver_class(self.store).put(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            checkpoint,
            {"source": "loop", "step": 0},
            {key: 1 for key in values},
        )

    def test_the_checkpoints_next_node_refuses_a_run_with_nothing_dispatched(self) -> None:
        """The engine owes a step, so the turn owes it too -- derived, not declared."""
        self.commit(self.workflow_state())
        orca = FakeOrca()

        self.assertEqual(self.cli("--json", orca=orca), turn_boundary.EXIT_REFUSED)

        summary = json.loads(self.printed.getvalue())
        self.assertEqual(summary["status_authority"], turn_boundary.STATUS_AUTHORITY_CHECKPOINT)
        self.assertEqual(summary["run_status"], "ACTIVE")
        self.assertEqual(
            summary["runnable_actions"],
            [f"{turn_boundary.ACTION_CHECKPOINT_ROUTE}:PREPARE_WORKER"],
        )
        self.assertEqual(
            summary["reason_code"], quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED
        )

    def test_a_declared_status_the_checkpoint_contradicts_is_refused(self) -> None:
        """A model's word never wins over the run's own durable state."""
        self.commit(self.workflow_state())
        orca = FakeOrca()
        orca.dispatch(PLAN_DISPATCH, PLAN_TASK)

        self.assertEqual(
            self.cli("--declare", "COMPLETED", "--json", orca=orca),
            turn_boundary.EXIT_REFUSED,
        )

        summary = json.loads(self.printed.getvalue())
        self.assertEqual(summary["run_status"], "ACTIVE")
        self.assertEqual(
            summary["reason_code"], quiescence.QUIESCENCE_UNSUPPORTED_REST_CLAIM
        )
        self.assertIn("workflow_checkpoint", summary["detail"])

    def test_a_terminal_checkpoint_supplies_the_rest_state_with_nothing_declared(self) -> None:
        """A run that ended says so itself; the caller does not have to."""
        self.commit(
            self.workflow_state(
                terminal_status="COMPLETED",
                run_lifecycle="SETTLED",
                pending_role=None,
                route_token="COMPLETE",
            )
        )
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")

        self.assertEqual(self.cli("--json", orca=orca), turn_boundary.EXIT_QUIESCENT)

        summary = json.loads(self.printed.getvalue())
        self.assertEqual(summary["run_status"], "COMPLETED")
        self.assertEqual(summary["status_authority"], turn_boundary.STATUS_AUTHORITY_CHECKPOINT)
        self.assertEqual(summary["runnable_actions"], [])

    def test_a_paused_checkpoint_reports_the_wait_rather_than_a_next_node(self) -> None:
        """A run already paused re-routes to its own pause; that is not outstanding work."""
        self.commit(
            self.workflow_state(
                run_lifecycle="WAITING_FOR_INPUT",
                decision_state="NEEDS_INPUT",
                pending_clarification_id="req_1",
                pause_binding={
                    "pause_record_id": "pause_1",
                    "paused_at": "2026-01-01T00:00:00Z",
                    "request_id": "req_1",
                    "decision_item_ids": [],
                    "source_ledger_keys": [],
                    "responsible_phase": "ANALYSIS",
                    "repository_binding": {},
                    "artifact_binding": {},
                    "policy_digest": "digest",
                    "settlement_ledger": [],
                    "disposition": None,
                },
            )
        )
        orca = FakeOrca()
        self.arm_pause_record()

        self.assertEqual(self.cli("--json", orca=orca), turn_boundary.EXIT_QUIESCENT)

        summary = json.loads(self.printed.getvalue())
        self.assertEqual(summary["run_status"], quiescence.WAITING_FOR_INPUT)
        self.assertEqual(summary["runnable_actions"], [])
        self.assertEqual(
            summary["durable_wait_evidence"], [turn_boundary.WAIT_EVIDENCE_PAUSE_RECORD]
        )

    def test_a_corrupt_checkpoint_store_yields_no_verdict(self) -> None:
        """An unreadable authority is not an absent one: exit 3, and nothing recorded."""
        self.store.write_text("{not json", encoding="utf-8")
        orca = FakeOrca()

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_UNAVAILABLE)

        self.assertEqual(self.records(), [])

    def test_threads_that_disagree_about_the_run_status_yield_no_verdict(self) -> None:
        """The boundary does not pick a winner among live authorities."""
        self.commit(self.workflow_state(thread_id="thread_main"), thread_id="thread_main")
        self.commit(
            self.workflow_state(
                thread_id="thread_other",
                terminal_status="COMPLETED",
                run_lifecycle="SETTLED",
                pending_role=None,
                route_token="COMPLETE",
            ),
            thread_id="thread_other",
        )
        orca = FakeOrca()

        self.assertEqual(self.cli(orca=orca), turn_boundary.EXIT_UNAVAILABLE)

        self.assertIn("disagree about the run's status", self.printed.getvalue())


class QuiescenceContractTests(unittest.TestCase):
    """The runtime-neutral half, tested without a harness at all."""

    def test_the_five_states_the_ticket_enumerates_are_all_quiescent(self) -> None:
        for state in (
            quiescence.ACTIVE_DISPATCH_WAIT,
            "WAITING_FOR_INPUT",
            "BLOCKED",
            "ESCALATED",
            "COMPLETED",
        ):
            self.assertIn(state, quiescence.QUIESCENT_STATES)

    def test_an_unknown_status_fails_closed(self) -> None:
        verdict = quiescence.quiescence_verdict(run_status="PROBABLY_FINE")
        self.assertFalse(verdict["quiescent"])
        self.assertEqual(verdict["reason_code"], quiescence.QUIESCENCE_UNKNOWN_STATUS)

    def test_assert_quiescent_carries_the_verdict_on_the_exception(self) -> None:
        with self.assertRaises(quiescence.QuiescenceViolation) as raised:
            quiescence.assert_quiescent(run_status="ACTIVE", next_node="ADVANCE_PHASE")
        self.assertEqual(
            raised.exception.reason_code, quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED
        )
        self.assertEqual(raised.exception.verdict["next_node"], "ADVANCE_PHASE")

    def test_the_unacknowledged_check_precedes_the_status_check(self) -> None:
        verdict = quiescence.quiescence_verdict(
            run_status="COMPLETED", unacknowledged_deliveries=["dlv_1"]
        )
        self.assertFalse(verdict["quiescent"])
        self.assertEqual(
            verdict["reason_code"], quiescence.QUIESCENCE_UNACKNOWLEDGED_DELIVERY
        )

    def test_every_run_lifecycle_state_is_classified(self) -> None:
        """The module-level assertion, restated as a test so it cannot be deleted quietly."""
        from scripts.deterministic_workflow.contracts import RUN_LIFECYCLE_STATES

        for state in RUN_LIFECYCLE_STATES:
            verdict = quiescence.quiescence_verdict(run_status=state)
            self.assertNotEqual(
                verdict["reason_code"],
                quiescence.QUIESCENCE_UNKNOWN_STATUS,
                f"{state} is neither a rest state nor a known non-terminal status",
            )

    def test_provenance_reports_a_wrong_dispatch_before_a_wrong_task(self) -> None:
        """Order matters: a stale delivery must report itself as a stale delivery."""
        _state, reason = quiescence.worker_done_provenance(
            {"dispatchId": "ctx_other", "taskId": "task_other"},
            expected_task_id="task_mine",
            expected_dispatch_id="ctx_mine",
        )
        self.assertIn("dispatchId", reason)
        self.assertNotIn("taskId", reason)

    def test_a_non_mapping_payload_is_a_mismatch_not_a_crash(self) -> None:
        state, reason = quiescence.worker_done_provenance(
            ["not", "an", "object"], expected_task_id="t", expected_dispatch_id="d"
        )
        self.assertEqual(state, quiescence.PROVENANCE_MISMATCH)
        self.assertIn("not an object", reason)

    def test_the_four_dispositions_are_decided_by_how_far_the_settlement_got(self) -> None:
        """The whole crash-recovery rule, with no harness and no I/O.

        Each row is the state a previous process would have left behind at one of the
        boundaries a crash can land on, and the disposition is what keeps the delivery
        from being lost at that boundary or duplicated at the next one.
        """
        consumed_only = {
            quiescence.DELIVERY_STATE_FIELD: quiescence.DELIVERY_STATE_PROCESSED
        }
        claimed = {
            **consumed_only,
            quiescence.DELIVERY_SETTLEMENT_CLAIMED_FIELD: True,
        }
        settled = {**claimed, quiescence.DELIVERY_SETTLED_FIELD: True}
        acknowledged = {
            quiescence.DELIVERY_STATE_FIELD: quiescence.DELIVERY_STATE_ACKNOWLEDGED
        }
        cases = {
            "dlv_resume": (consumed_only, quiescence.DELIVERY_RESUME),
            "dlv_recover": (claimed, quiescence.DELIVERY_RECOVER),
            "dlv_settled": (settled, quiescence.DELIVERY_REPLAY),
            "dlv_acked": (acknowledged, quiescence.DELIVERY_REPLAY),
        }
        for delivery_id, (row, expected) in cases.items():
            with self.subTest(delivery=delivery_id):
                disposition, reason = quiescence.delivery_disposition(
                    delivery_id, {delivery_id: row}
                )
                self.assertEqual(disposition, expected)
                self.assertTrue(reason)
        self.assertEqual(
            quiescence.delivery_disposition("dlv_new", {})[0], quiescence.DELIVERY_PROCESS
        )

    def test_an_ack_failed_row_that_settled_is_a_replay_not_a_recovery(self) -> None:
        """An ack that never landed is not evidence that the work did not happen."""
        row = {
            quiescence.DELIVERY_STATE_FIELD: quiescence.DELIVERY_STATE_ACK_FAILED,
            quiescence.DELIVERY_SETTLEMENT_CLAIMED_FIELD: True,
            quiescence.DELIVERY_SETTLED_FIELD: True,
        }
        disposition, _ = quiescence.delivery_disposition("dlv_x", {"dlv_x": row})
        self.assertEqual(disposition, quiescence.DELIVERY_REPLAY)

    def test_an_unseen_delivery_is_processed_and_a_seen_one_is_a_replay(self) -> None:
        ledger: dict[str, dict[str, Any]] = {}
        self.assertEqual(
            quiescence.delivery_disposition("dlv_1", ledger)[0], quiescence.DELIVERY_PROCESS
        )
        ledger["dlv_1"] = {
            quiescence.DELIVERY_STATE_FIELD: quiescence.DELIVERY_STATE_ACKNOWLEDGED,
            "replays": 0,
        }
        self.assertEqual(
            quiescence.delivery_disposition("dlv_1", ledger)[0], quiescence.DELIVERY_REPLAY
        )
        ledger["dlv_1"]["replays"] = quiescence.DELIVERY_REPLAY_LIMIT
        self.assertEqual(
            quiescence.delivery_disposition("dlv_1", ledger)[0],
            quiescence.DELIVERY_REPLAY_EXHAUSTED,
        )


def deny_directory_read(case: unittest.TestCase, directory: Path) -> None:
    """Make ``directory`` genuinely unreadable, or skip the test saying exactly why.

    FINAL attempt-3 R1(e). A test that passes because a ``chmod`` quietly did nothing --
    running as root, or on a filesystem that ignores the mode bits -- is the same class
    of defect this branch keeps shipping: it asserts a behaviour it never exercised. So
    the denial is VERIFIED rather than assumed. The directory is listed after the chmod;
    if that still succeeds, the environment cannot express the state under test and the
    test SKIPS with the reason, which is never the same thing as concluding that the
    authority was proven absent.
    """
    original = directory.stat().st_mode

    def restore() -> None:
        # Tolerant on purpose: the enclosing fixture may already have removed the tree
        # by the time cleanups unwind, and a teardown error would mask the assertion
        # this helper exists to make possible.
        try:
            os.chmod(directory, original)
        except OSError:
            pass

    case.addCleanup(restore)
    os.chmod(directory, 0o000)
    try:
        os.listdir(directory)
    except OSError:
        return
    raise unittest.SkipTest(
        f"this environment ignores directory mode bits (euid={os.geteuid()}); "
        f"{directory} is still listable at mode 000, so a genuinely unreadable Run-state "
        "authority cannot be constructed here. The classification itself is still "
        "covered portably by the not-a-directory case."
    )


def deny_write(case: unittest.TestCase, path: Path) -> None:
    """Make ``path`` genuinely un-writable, or skip the test saying exactly why.

    FINAL adversarial review R1. The sibling of ``deny_directory_read`` above, and it
    verifies the denial for the same reason: a test that passes because a ``chmod`` did
    nothing -- running as root, or on a filesystem that ignores mode bits -- asserts a
    behaviour it never exercised. The probe is a real write attempt, not ``os.access``:
    ``os.access`` is exactly the TOCTOU preflight whose insufficiency this review found,
    so it is not evidence here either.
    """
    original = path.stat().st_mode

    def restore() -> None:
        try:
            os.chmod(path, original)
        except OSError:
            pass

    case.addCleanup(restore)
    os.chmod(path, 0o555 if path.is_dir() else 0o444)
    try:
        if path.is_dir():
            probe = path / ".write_probe"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        else:
            with path.open("a", encoding="utf-8"):
                pass
    except OSError:
        return
    raise unittest.SkipTest(
        f"this environment ignores mode bits (euid={os.geteuid()}); {path} is still "
        "writable at a read-only mode, so a counter that cannot be persisted cannot be "
        "constructed here."
    )


class StopHookBoundaryTests(TurnBoundaryCliDriver, OS44TestCase):
    """FINAL-R1. The turn-end boundary as Claude Code's blocking ``Stop`` hook.

    The previous round asserted that no hook runs when a model stops emitting tokens and
    narrowed OS-44's scope on that premise. The premise was false. Observed on the
    installed Claude Code 2.1.260: the live settings register hooks for a ``Stop`` event
    (Orca's own is already one of them), the binary evaluates them from a query site
    labelled ``blockable_turn_end``, and it turns a hook's blocking error into a message
    pushed onto the conversation and re-invokes the model instead of ending the turn.

    These tests drive the real ``turn-end-hook`` CLI over the real Stop payload shape.
    They prove the four things the wiring has to get right -- it blocks a refused turn,
    it fails closed on unreadable state, it cannot block forever, and it is inert in a
    session that is not bound to a run -- and the two things it must never do: block on
    its own defect, or touch a settings file.
    """

    def hook(
        self,
        payload: dict[str, Any],
        *argv: str,
        orca: "FakeOrca | None" = None,
        bind: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        """The real CLI, over the real stdin/stdout hook contract."""
        self.printed = io.StringIO()
        binding = ["--run-id", self.RUN] if bind else []
        with patch.object(turn_boundary, "_default_runner", orca or FakeOrca()):
            with patch.dict(environ, {turn_boundary.STOP_HOOK_RUN_ENV: ""}):
                with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                    with redirect_stdout(self.printed):
                        code = launcher.run_cli(
                            [
                                "turn-end-hook",
                                *binding,
                                "--artifact-base",
                                str(self.artifact_dir),
                                *argv,
                            ]
                        )
        return code, json.loads(self.printed.getvalue())

    @staticmethod
    def payload(**overrides: Any) -> dict[str, Any]:
        """Claude Code's Stop hook stdin document, in the shape the runtime sends."""
        document = {
            "session_id": "session_os44",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": "/repo",
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        }
        document.update(overrides)
        return document

    def stalled(self) -> "FakeOrca":
        """``run_c2166e75bb02``'s shape: ANALYSIS completed, PLAN pending, nothing runs."""
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        orca.task(PLAN_TASK, "pending", ANALYSIS_TASK)
        return orca

    def test_a_refused_turn_is_blocked_with_an_actionable_reason(self) -> None:
        """The whole point of R1: the refusal now PREVENTS the turn, it does not report.

        ``decision: block`` is what makes the runtime push the reason onto the
        conversation and re-invoke the model, so the reason has to say what is
        outstanding rather than only that something is.
        """
        code, decision = self.hook(self.payload(), orca=self.stalled())

        self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
        self.assertEqual(decision["decision"], "block")
        self.assertIn(quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED, decision["reason"])
        self.assertIn(f"{turn_boundary.ACTION_DISPATCH_TASK}:{PLAN_TASK}", decision["reason"])
        records = self.records()
        self.assertEqual(
            [record["event"] for record in records],
            [run_logging.EVENT_QUIESCENCE_VIOLATION],
        )
        self.assertEqual(records[0]["source"], turn_boundary.STOP_HOOK_SOURCE)

    def test_a_quiescent_turn_is_allowed_and_the_block_budget_is_reset(self) -> None:
        """An active dispatch is a legitimate turn end, and the hook stands aside."""
        orca = FakeOrca()
        orca.task(ANALYSIS_TASK, "completed")
        orca.dispatch("ctx_live", PLAN_TASK)
        turn_boundary.set_stop_hook_block_count(
            self.RUN, "session_os44", 2, artifact_base=self.artifact_dir
        )

        code, decision = self.hook(self.payload(stop_hook_active=True), orca=orca)

        self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
        self.assertNotIn("decision", decision)
        self.assertEqual(
            turn_boundary.stop_hook_block_count(
                self.RUN, "session_os44", artifact_base=self.artifact_dir
            ),
            0,
        )
        self.assertEqual(
            [record["event"] for record in self.records()],
            [run_logging.EVENT_QUIESCENCE_VERIFIED],
        )

    def test_unreadable_authority_blocks_rather_than_allowing_the_turn(self) -> None:
        """Exit 3 on the CLI is a block here. An unreadable authority is not an absent one."""
        orca = self.stalled()
        orca.failing.add("task-list")

        code, decision = self.hook(self.payload(), orca=orca)

        self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("TURN_BOUNDARY_UNAVAILABLE", decision["reason"])

    def test_the_block_budget_releases_the_turn_and_records_what_it_let_through(self) -> None:
        """A hook that blocks forever wedges the session, so this one stops -- loudly.

        The runtime has its own cap (8 consecutive blocks by default) and would override
        the hook regardless; releasing first keeps the escape hatch ours and testable,
        and the release itself is published so a stalled turn that got through is a fact
        in the audit rather than something only the terminal saw.
        """
        orca = self.stalled()
        turn_boundary.set_stop_hook_block_count(
            self.RUN, "session_os44", 2, artifact_base=self.artifact_dir
        )

        code, decision = self.hook(
            self.payload(stop_hook_active=True), "--block-cap", "2", orca=orca
        )

        self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
        self.assertNotIn("decision", decision)
        self.assertIn("releasing this one rather than wedging", decision["systemMessage"])
        sources = [record["source"] for record in self.records()]
        self.assertEqual(
            sources, [turn_boundary.STOP_HOOK_SOURCE, turn_boundary.STOP_HOOK_SOURCE_RELEASED]
        )
        self.assertEqual(
            turn_boundary.stop_hook_block_count(
                self.RUN, "session_os44", artifact_base=self.artifact_dir
            ),
            0,
        )

    def test_consecutive_blocks_are_counted_only_within_one_stop_chain(self) -> None:
        """``stop_hook_active`` false means the model is not here because we blocked it.

        The budget is per stop chain, so a later turn that stalls again is blocked again
        rather than inheriting a spent budget from an earlier one.
        """
        orca = self.stalled()
        turn_boundary.set_stop_hook_block_count(
            self.RUN, "session_os44", 9, artifact_base=self.artifact_dir
        )

        _, decision = self.hook(
            self.payload(stop_hook_active=False), "--block-cap", "2", orca=orca
        )

        self.assertEqual(decision["decision"], "block")
        self.assertEqual(
            turn_boundary.stop_hook_block_count(
                self.RUN, "session_os44", artifact_base=self.artifact_dir
            ),
            1,
        )

    def test_an_unbound_session_in_a_project_with_runs_is_blocked_until_it_binds(
        self,
    ) -> None:
        """FINAL attempt-2 R1. An unattributable session in a project that HAS runs is
        refused, not announced.

        The previous round allowed this turn and printed a notice. That reproduced the
        OS-44 defect one level up: the model still had to REMEMBER an invocation
        (``turn-end-bind``) before ending its turn, and forgetting it was still allowed.
        An allow does not re-invoke the model, so the notice reaches nobody who could
        act on it. A block does, and it carries the command to run.

        The refusal is still cheap: nothing about the run is observed (no ``orca`` call,
        no audit record), because there is no run to observe -- only the turn is held.
        """
        orca = self.stalled()
        (self.artifact_dir / "artifacts" / "runs" / self.RUN).mkdir(parents=True, exist_ok=True)

        code, decision = self.hook(self.payload(), orca=orca, bind=False)

        self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("turn-end-bind", decision["reason"])
        self.assertIn("bound to no Orca Run", decision["reason"])
        self.assertEqual(orca.commands, [])
        self.assertEqual(self.records(), [])
        self.assertEqual(
            turn_boundary.stop_hook_block_count(
                "", "session_os44", artifact_base=self.artifact_dir
            ),
            1,
        )

    def test_the_unbound_block_releases_itself_at_the_cap(self) -> None:
        """And it is finite, which is what makes blocking an unattributable session safe.

        A session that genuinely drives no Run pays a bounded number of extra turn ends
        and is then let through, saying so. Being wrong here costs turns, never a wedged
        session -- that bound is load-bearing for the whole fail-closed choice above.
        """
        (self.artifact_dir / "artifacts" / "runs" / self.RUN).mkdir(parents=True, exist_ok=True)
        turn_boundary.set_stop_hook_block_count(
            "", "session_os44", 2, artifact_base=self.artifact_dir
        )

        _, decision = self.hook(
            self.payload(stop_hook_active=True),
            "--block-cap",
            "2",
            orca=self.stalled(),
            bind=False,
        )

        self.assertNotIn("decision", decision)
        self.assertIn("releasing this one rather than wedging", decision["systemMessage"])
        self.assertEqual(
            turn_boundary.stop_hook_block_count(
                "", "session_os44", artifact_base=self.artifact_dir
            ),
            0,
        )

    def test_an_unbound_session_in_a_project_with_no_runs_is_passed_over_silently(
        self,
    ) -> None:
        """The other half of that judgement: no runs here, nothing to say.

        A hook that chatters at every unrelated session in the project gets uninstalled,
        and an uninstalled hook enforces nothing.
        """
        empty = self.artifact_dir / "no_runs_here"
        empty.mkdir(parents=True, exist_ok=True)

        self.printed = io.StringIO()
        with patch.object(turn_boundary, "_default_runner", self.stalled()):
            with patch.dict(environ, {turn_boundary.STOP_HOOK_RUN_ENV: ""}):
                with patch.object(sys, "stdin", io.StringIO(json.dumps(self.payload()))):
                    with redirect_stdout(self.printed):
                        launcher.run_cli(
                            ["turn-end-hook", "--artifact-base", str(empty)]
                        )

        self.assertEqual(json.loads(self.printed.getvalue()), {"suppressOutput": True})

    def test_run_state_discovery_is_tri_state_not_a_boolean(self) -> None:
        """FINAL attempt-3 R1. ``proven_absent`` and ``unreadable`` are different facts.

        The shipped predicate caught every ``OSError`` from iterating ``artifacts/runs``
        and answered ``False``, which made "I could not look" indistinguishable from
        "there is positively nothing here" -- and only the second licenses this
        boundary's single silent allow. Every layout is asserted, including the two that
        need no permission bits at all, so the classification is pinned on every platform
        this suite runs on.
        """
        cases = {
            "runs present": (
                lambda base: (base / "artifacts" / "runs" / "run_x").mkdir(parents=True),
                turn_boundary.RUN_STATE_PRESENT,
            ),
            "runs root readable and empty": (
                lambda base: (base / "artifacts" / "runs").mkdir(parents=True),
                turn_boundary.RUN_STATE_PROVEN_ABSENT,
            ),
            "no runs root at all": (
                lambda base: (base / "artifacts").mkdir(parents=True),
                turn_boundary.RUN_STATE_PROVEN_ABSENT,
            ),
            "no artifacts directory at all": (
                lambda base: None,
                turn_boundary.RUN_STATE_PROVEN_ABSENT,
            ),
            "a file where the runs root belongs": (
                lambda base: (
                    (base / "artifacts").mkdir(parents=True),
                    (base / "artifacts" / "runs").write_text("x", encoding="utf-8"),
                ),
                turn_boundary.RUN_STATE_UNREADABLE,
            ),
        }
        for name, (build, expected) in cases.items():
            with self.subTest(layout=name):
                base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
                build(base)
                self.assertEqual(turn_boundary.project_run_state(base), expected)

    def test_an_unreadable_runs_root_is_not_proven_absence(self) -> None:
        """The reviewer's exact probe, on a REAL unreadable directory.

        ``artifacts/runs/run_x`` exists and the runs root is at mode 000. Before this
        fix, ``project_has_runs()`` answered ``False`` and the hook answered
        ``{"suppressOutput": true}`` -- byte-identical to a project that genuinely holds
        no runs, which is the silent turn gap OS-44 exists to close. It must classify as
        ``unreadable`` and it must block.
        """
        base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        runs = base / "artifacts" / "runs"
        (runs / "run_x").mkdir(parents=True)
        self.assertEqual(turn_boundary.project_run_state(base), turn_boundary.RUN_STATE_PRESENT)
        deny_directory_read(self, runs)

        self.assertEqual(
            turn_boundary.project_run_state(base), turn_boundary.RUN_STATE_UNREADABLE
        )
        decision = turn_boundary.unbound_session_decision(
            self.payload(), artifact_base=base
        )

        self.assertEqual(decision.get("decision"), "block")
        self.assertIn("could not be read", decision["reason"])
        self.assertNotIn("suppressOutput", decision)

    def test_an_unreadable_authority_on_the_way_to_the_runs_root_also_blocks(self) -> None:
        """And it is the AUTHORITY that has to be readable, not just its last segment.

        ``artifacts`` at mode 000 hides the runs root behind it. ``FileNotFoundError``
        would be proof of absence; ``PermissionError`` is proof of nothing, and the two
        arrive at the same call.
        """
        base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        (base / "artifacts" / "runs" / "run_x").mkdir(parents=True)
        deny_directory_read(self, base / "artifacts")

        self.assertEqual(
            turn_boundary.project_run_state(base), turn_boundary.RUN_STATE_UNREADABLE
        )
        self.assertEqual(
            turn_boundary.unbound_session_decision(
                self.payload(), artifact_base=base
            ).get("decision"),
            "block",
        )

    def test_the_unreadable_refusal_is_finite_like_every_other_one(self) -> None:
        """R1(b): the same cap, not a new unbounded path.

        The counter cannot live under the runs root here -- that root is the very thing
        that cannot be read -- so it falls outward to a directory that will take it. A
        refusal whose budget is never recorded never advances and therefore never
        releases, which is the one thing this boundary must not do to a live session.
        """
        base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        runs = base / "artifacts" / "runs"
        (runs / "run_x").mkdir(parents=True)
        deny_directory_read(self, runs)

        chain = [
            turn_boundary.unbound_session_decision(
                self.payload(stop_hook_active=True), artifact_base=base, cap=3
            ).get("decision", "release")
            for _ in range(4)
        ]

        self.assertEqual(chain, ["block", "block", "block", "release"])
        self.assertTrue(
            (base / "artifacts" / turn_boundary.STOP_HOOK_UNBOUND_STATE_FILENAME).exists(),
            "the refusal must record its budget somewhere it can actually write",
        )

    # -- FINAL adversarial review R1: an unpersistable budget releases -----------------
    #
    # The two directions of this boundary's fail-safe are deliberately opposite, and the
    # tests below pin BOTH so a later reader cannot collapse one into the other:
    #
    #   unreadable Run authority   -> BLOCK, bounded   (the tests above this line)
    #   unpersistable block budget -> RELEASE, with a reason (the tests below)
    #
    # The first asks what the boundary KNOWS about the run; the second asks whether the
    # boundary can still keep its own promise to let go. A cap that cannot be written
    # down guarantees nothing -- the next invocation reads zero and refuses again -- so a
    # refusal that cannot be counted is never issued.

    def test_a_counter_that_cannot_be_persisted_reports_the_failure(self) -> None:
        """The signal the decision path needs, which the pre-fix version did not give.

        ``set_stop_hook_block_count`` used to return ``None`` whether the write landed or
        raised, so ``blocked_so_far`` could sit at zero forever while the hook refused
        every turn. It now reads the counter back and reports whether the record took.
        """
        base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        (base / "artifacts" / "runs" / "run_x").mkdir(parents=True)
        counter = base / "artifacts" / "runs" / turn_boundary.STOP_HOOK_UNBOUND_STATE_FILENAME

        self.assertIs(
            turn_boundary.set_stop_hook_block_count("", "s", 1, artifact_base=base), True
        )
        self.assertEqual(
            turn_boundary.stop_hook_block_count("", "s", artifact_base=base), 1
        )
        deny_write(self, counter)
        self.assertIs(
            turn_boundary.set_stop_hook_block_count("", "s", 2, artifact_base=base), False
        )
        self.assertEqual(
            turn_boundary.stop_hook_block_count("", "s", artifact_base=base),
            1,
            "the failed write must not be reported as having advanced the budget",
        )

    def test_an_unwritable_counter_releases_instead_of_blocking_forever(self) -> None:
        """The reviewer's probe, reproduced: six consecutive active Stop invocations.

        With the counter file at the chosen location unwritable, the pre-fix path
        produced ``['block'] * 6`` against a cap of 3 -- an unbounded refusal from the
        very mechanism that promises a bound, which wedges the session outright. Every
        one of the six must now be a release, and each must SAY why.
        """
        base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        (base / "artifacts" / "runs" / "run_x").mkdir(parents=True)
        counter = base / "artifacts" / "runs" / turn_boundary.STOP_HOOK_UNBOUND_STATE_FILENAME
        counter.write_text("{}", encoding="utf-8")
        deny_write(self, counter)

        decisions = [
            turn_boundary.unbound_session_decision(
                self.payload(stop_hook_active=True), artifact_base=base, cap=3
            )
            for _ in range(6)
        ]

        self.assertEqual(
            [decision.get("decision", "release") for decision in decisions],
            ["release"] * 6,
        )
        for decision in decisions:
            self.assertNotIn("decision", decision)
            self.assertIn("was NOT gated", decision["systemMessage"])
            self.assertIn("could not record", decision["systemMessage"])
            self.assertIn(str(counter), decision["systemMessage"])

    def test_the_release_still_happens_when_no_location_will_take_the_counter(
        self,
    ) -> None:
        """Every candidate refuses, temp directory included -- the reviewer's other case.

        The runs root, ``artifacts`` and the project root are all read-only, so
        ``_unbound_state_dir`` falls all the way out to the temp directory, and the
        counter there cannot be written either. There is nowhere left to count, so there
        is no bounded refusal to issue.
        """
        base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        (base / "artifacts" / "runs" / "run_x").mkdir(parents=True)
        fake_tmp = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        fallback = fake_tmp / turn_boundary.STOP_HOOK_UNBOUND_STATE_FILENAME
        fallback.write_text("{}", encoding="utf-8")
        deny_write(self, fallback)
        for directory in (base / "artifacts" / "runs", base / "artifacts", base):
            deny_write(self, directory)
        with patch.dict(environ, {"TMPDIR": str(fake_tmp)}):
            with patch.object(tempfile, "tempdir", None):
                self.assertEqual(turn_boundary._stop_hook_state_path("", base), fallback)
                chain = [
                    turn_boundary.unbound_session_decision(
                        self.payload(stop_hook_active=True), artifact_base=base, cap=3
                    ).get("decision", "release")
                    for _ in range(6)
                ]

        self.assertEqual(chain, ["release"] * 6)

    def test_a_run_bound_refusal_also_releases_when_its_budget_cannot_be_persisted(
        self,
    ) -> None:
        """And the same, through the real hook CLI on a run it DID resolve.

        The bound path is where the boundary does its actual work, and its counter lives
        in the run's own directory -- which can be unwritable for exactly the reasons the
        review named. A refusal there would be just as unbounded, so it is withheld the
        same way, and the release is published to the audit like any other release the
        cap grants.
        """
        counter = (
            self.artifact_dir / "artifacts" / "runs" / self.RUN
            / turn_boundary.STOP_HOOK_STATE_FILENAME
        )
        counter.parent.mkdir(parents=True, exist_ok=True)
        counter.write_text("{}", encoding="utf-8")
        deny_write(self, counter)

        chain = []
        for _ in range(6):
            code, decision = self.hook(
                self.payload(stop_hook_active=True), "--block-cap", "3", orca=self.stalled()
            )
            self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
            chain.append(decision.get("decision", "release"))

        self.assertEqual(chain, ["release"] * 6)
        self.assertIn(
            turn_boundary.STOP_HOOK_SOURCE_RELEASED,
            [record["source"] for record in self.records()],
            "a release the boundary grants itself has to be observable in the audit",
        )

    def test_a_writable_counter_still_gives_the_bounded_refusal(self) -> None:
        """The control, so the fix above cannot be a blanket weakening of the cap.

        Same six invocations, same cap, nothing denied: block, block, block, release --
        and then the budget starts over, which is what the counter is for. An unreadable
        runs root is covered by ``..._unreadable_refusal_is_finite...`` above and must
        keep blocking; only an unpersistable BUDGET releases early.
        """
        base = Path(tempfile.mkdtemp(dir=str(self.artifact_dir)))
        (base / "artifacts" / "runs" / "run_x").mkdir(parents=True)

        chain = [
            turn_boundary.unbound_session_decision(
                self.payload(stop_hook_active=True), artifact_base=base, cap=3
            ).get("decision", "release")
            for _ in range(6)
        ]

        self.assertEqual(
            chain, ["block", "block", "block", "release", "block", "block"]
        )

    def test_the_session_binding_is_what_the_hook_resolves_the_run_from(self) -> None:
        """The producer the previous round did not have, in process.

        ``turn-end-bind`` writes the record; the hook finds it from the payload's
        ``session_id`` alone, with no ``--run-id`` and no environment variable. The
        subprocess contract tests below prove the same thing through the real registered
        command; this one pins the resolution rules -- newest binding wins, a released
        binding does not.
        """
        turn_boundary.bind_session_run(
            self.RUN, session_id="session_os44", artifact_base=self.artifact_dir
        )

        code, decision = self.hook(self.payload(), orca=self.stalled(), bind=False)

        self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
        self.assertEqual(decision["decision"], "block")

        released_orca = self.stalled()
        turn_boundary.release_session_run(
            self.RUN, session_id="session_os44", artifact_base=self.artifact_dir
        )
        _, released = self.hook(self.payload(), orca=released_orca, bind=False)

        # The release did what it is for: the run is no longer resolved, so the run is
        # no longer observed and the refusal above is gone. What is left is the generic
        # unattributable-session hold, because this project still holds run state -- and
        # that hold is bounded by the cap rather than being an enforcement claim.
        self.assertEqual(released_orca.commands, [])
        self.assertNotIn(quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED, str(released))
        self.assertIn("bound to no Orca Run", released["reason"])

    def test_a_non_stop_event_is_ignored(self) -> None:
        """A ``SubagentStop`` payload is a Worker finishing, not the Coordinator's turn."""
        orca = self.stalled()

        _, decision = self.hook(
            self.payload(hook_event_name="SubagentStop"), orca=orca
        )

        self.assertNotIn("decision", decision)
        self.assertEqual(orca.commands, [])

    def test_the_hook_blocks_when_the_gate_itself_fails(self) -> None:
        """FINAL attempt-2 R1. A defect in the boundary is a refusal, not an allow.

        Fail-closed covers the boundary crashing too, once a run has been resolved: a
        gate that could not run has observed nothing, and "we could not check" is not
        evidence that there is nothing to check. It used to allow the turn and say so,
        which is the same announce-and-allow shape the review rejected.
        """
        with patch.object(turn_boundary, "enforce", side_effect=MemoryError("boom")):
            code, decision = self.hook(self.payload(), orca=self.stalled())

        self.assertEqual(code, turn_boundary.EXIT_STOP_HOOK)
        self.assertEqual(decision["decision"], "block")
        self.assertIn(turn_boundary.STOP_HOOK_FAILURE_REASON_CODE, decision["reason"])
        self.assertIn("boom", decision["reason"])
        self.assertEqual(
            turn_boundary.stop_hook_block_count(
                self.RUN, "session_os44", artifact_base=self.artifact_dir
            ),
            1,
        )

    def test_the_hook_failure_block_releases_at_the_cap_and_records_it(self) -> None:
        """And that refusal is bounded too, so a persistent internal defect cannot wedge
        a live session -- with the release published to the run's audit under the same
        ``cap_released`` source as any other, so a turn that got through on our own bug
        is a fact in the artifacts rather than something only the terminal saw."""
        turn_boundary.set_stop_hook_block_count(
            self.RUN, "session_os44", 2, artifact_base=self.artifact_dir
        )

        with patch.object(turn_boundary, "enforce", side_effect=MemoryError("boom")):
            _, decision = self.hook(
                self.payload(stop_hook_active=True),
                "--block-cap",
                "2",
                orca=self.stalled(),
            )

        self.assertNotIn("decision", decision)
        self.assertIn("turn-end boundary hook failed", decision["systemMessage"])
        self.assertIn("releasing this one", decision["systemMessage"])
        records = self.records()
        self.assertEqual(
            [record["source"] for record in records],
            [turn_boundary.STOP_HOOK_SOURCE_RELEASED],
        )
        self.assertEqual(
            records[0]["reason_code"], turn_boundary.STOP_HOOK_FAILURE_REASON_CODE
        )

    def test_no_settings_file_is_written_and_the_live_global_one_is_untouched(self) -> None:
        """The hard safety constraint, asserted rather than promised.

        Registration is the operator's act. Running the hook writes the run's audit and
        its own counter and nothing else -- in particular not the live global settings
        file, which belongs to sessions that are running right now.
        """
        live = Path.home() / ".claude" / "settings.json"
        before = live.read_bytes() if live.is_file() else None

        self.hook(self.payload(), orca=self.stalled())

        self.assertEqual(live.read_bytes() if live.is_file() else None, before)
        self.assertEqual(
            sorted(path.name for path in self.artifact_dir.rglob("settings*.json")), []
        )

    def test_registration_composes_with_the_existing_orca_stop_hook(self) -> None:
        """Composing, not replacing -- over a fixture shaped like the live settings file.

        The live global settings already carry Orca's own Stop hook. A registration that
        replaced the ``Stop`` array would silently disable it, so the documented
        transformation adds an entry, leaves every other event alone, and is idempotent.
        Applied here to a temporary file; nothing in this repository applies it to a real
        settings location.
        """
        settings_path = self.artifact_dir / "isolated" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [{"hooks": [{"type": "command", "command": "orca"}]}],
                        "Stop": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "$HOME/.orca/agent-hooks/claude-hook.sh",
                                        "timeout": 10,
                                    }
                                ]
                            }
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )
        command = "python3 tools/run_workflow.py turn-end-hook"

        merged = turn_boundary.merge_stop_hook_registration(
            json.loads(settings_path.read_text(encoding="utf-8")), command
        )
        settings_path.write_text(json.dumps(merged), encoding="utf-8")

        stop = merged["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        self.assertEqual(
            stop[0]["hooks"][0]["command"], "$HOME/.orca/agent-hooks/claude-hook.sh"
        )
        self.assertEqual(stop[1]["hooks"][0]["command"], command)
        self.assertEqual(len(merged["hooks"]["SessionStart"]), 1)
        self.assertEqual(
            turn_boundary.merge_stop_hook_registration(merged, command), merged
        )

    def test_the_documented_registration_snippet_registers_this_command(self) -> None:
        """The Skill's JSON block is the operator's copy-paste path; it has to be real.

        BUGFIX-I4-R1-REAL-PATH. Asserting a substring is what let a command that could
        not execute anything pass review, so the snippet is compared to the command the
        code itself publishes, and :class:`DocumentedStopHookRegistrationTests` below
        RUNS that command.
        """
        hooks = documented_registration()

        self.assertEqual(hooks["type"], "command")
        self.assertEqual(hooks["command"], turn_boundary.STOP_HOOK_COMMAND)


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "orca-worker-reviewer-orchestration"
STOP_HOOK_SECTION_MARKER = "### Stop hook으로의 자동 강제"


def documented_registration() -> dict[str, Any]:
    """The single ``Stop`` hook entry the Skill tells an operator to paste.

    Read out of SKILL.md rather than restated here: these tests exist to prove that the
    DOCUMENTED command works, so a test that ran its own spelling of it would prove
    nothing about the thing an operator actually copies.
    """
    skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    section = skill[skill.index(STOP_HOOK_SECTION_MARKER):]
    block = section[section.index("```json") + len("```json"):]
    snippet = json.loads(block[: block.index("```")])
    return snippet["hooks"]["Stop"][0]["hooks"][0]


class DocumentedStopHookRegistrationTests(unittest.TestCase):
    """BUGFIX-I4-R1-REAL-PATH. The registration, EXECUTED, from a real project cwd.

    The defect this class exists to prevent shipped past a passing suite because every
    test entered through ``launcher.run_cli()`` with an explicit ``--run-id`` and the
    only test of the registration itself asserted that a documentation string contained
    a substring. Neither could notice that the command named an entry point no layout
    resolves, or that its ``ORCA_QUIESCENCE_RUN_ID=$ORCA_QUIESCENCE_RUN_ID`` prefix was
    a self-assignment of a variable nothing in the world produces.

    So every test here runs the command string TAKEN FROM SKILL.md in a subprocess, with
    ``shell=True`` because that is how Claude Code runs a hook command, from a temporary
    project directory, with only the environment a hook really gets. The run binding is
    established the way a Coordinator establishes it -- by running ``turn-end-bind`` in
    a separate subprocess carrying ``CLAUDE_CODE_SESSION_ID`` -- so a hook that could no
    longer resolve the entry point, or no longer find the binding, fails these tests
    instead of quietly allowing every turn.

    Nothing here reads or writes any real settings location. The registration fixture is
    a temporary directory, and the live global settings file is asserted byte-identical
    before and after.
    """

    RUN = "run_c2166e75bb02"
    SESSION = "8f0f0f0f-9d64-493d-8112-bbbac21a5da4"
    ANALYSIS_TASK = "task_analysis"
    PLAN_TASK = "task_plan"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.project = root / "project"
        self.home = root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.project.mkdir(parents=True)
        self.command = documented_registration()["command"]
        self.orca = self._fake_orca(running=False)

    # -- fixtures ---------------------------------------------------------------------

    def _fake_orca(self, *, running: bool) -> Path:
        """A stand-in ``orca`` binary on disk, because the hook shells out to one.

        ``running=False`` is ``run_c2166e75bb02``'s recorded shape: ANALYSIS completed,
        PLAN never dispatched, nothing executing -- the turn that must be refused.
        """
        tasks: list[dict[str, Any]] = [
            {"id": self.ANALYSIS_TASK, "status": "completed", "deps": "[]"},
            {
                "id": self.PLAN_TASK,
                "status": "dispatched" if running else "pending",
                "deps": json.dumps([self.ANALYSIS_TASK]),
                "dispatch_id": "ctx_live",
            },
        ]
        workers: list[dict[str, Any]] = (
            [
                {
                    "dispatchId": "ctx_live",
                    "taskId": self.PLAN_TASK,
                    "dispatchStatus": "dispatched",
                    "workerState": "ready",
                }
            ]
            if running
            else []
        )
        path = self.project / ("orca_running" if running else "orca_stalled")
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"TASKS = {tasks!r}\n"
            f"WORKERS = {workers!r}\n"
            "verb = sys.argv[2] if len(sys.argv) > 2 else ''\n"
            "result = {'task-list': {'tasks': TASKS}, 'worker-list': {'workers': WORKERS},\n"
            "          'gate-list': {'gates': []}}.get(verb, {})\n"
            "print(json.dumps({'ok': True, 'result': result}))\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def install_repository_checkout(self) -> Path:
        """Layout 2: this repository checked out as the Claude project."""
        link = self.project / SKILL_ROOT.name
        link.symlink_to(SKILL_ROOT, target_is_directory=True)
        return link

    def install_user_skill(self) -> Path:
        """Layout 4: the Skill where ``orca skills get`` puts it, project unaware."""
        skills = self.home / ".claude" / "skills"
        skills.mkdir(parents=True, exist_ok=True)
        link = skills / SKILL_ROOT.name
        link.symlink_to(SKILL_ROOT, target_is_directory=True)
        return link

    def hook_env(self, *, orca: Path | None = None, home: Path | None = None) -> dict[str, str]:
        """Exactly what a Claude Code hook gets: PATH, HOME, CLAUDE_PROJECT_DIR.

        Deliberately NOT the parent process's environment. ``ORCA_QUIESCENCE_RUN_ID`` is
        absent from every one of these tests, which is the point: the previous
        registration only ever looked there.
        """
        return {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home if home is not None else self.home),
            "CLAUDE_PROJECT_DIR": str(self.project),
            "ORCA_CLI_COMMAND": str(orca if orca is not None else self.orca),
        }

    def bind(self, entry_root: Path, *, session: str = "") -> subprocess.CompletedProcess:
        """Bind this session to the run the way a Coordinator does: its own command."""
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                str(entry_root / "tools" / "run_workflow.py"),
                "turn-end-bind",
                "--run-id",
                self.RUN,
                "--artifact-base",
                str(self.project),
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(self.project),
            env={
                **self.hook_env(),
                turn_boundary.SESSION_ID_ENV: session or self.SESSION,
            },
        )

    def fire(self, *, env: dict[str, str] | None = None, **payload: Any) -> dict[str, Any]:
        """Run the documented command as the runtime runs it, and parse its decision."""
        document = {
            "session_id": self.SESSION,
            "transcript_path": str(self.project / "transcript.jsonl"),
            "cwd": str(self.project),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        }
        document.update(payload)
        completed = subprocess.run(  # noqa: S602 - a hook command IS a shell command
            self.command,
            shell=True,
            input=json.dumps(document),
            capture_output=True,
            text=True,
            check=False,
            cwd=str(self.project),
            env=env if env is not None else self.hook_env(),
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"a Stop hook must always exit 0; stderr={completed.stderr}",
        )
        self.assertTrue(
            completed.stdout.strip(),
            f"the hook command produced no decision at all; stderr={completed.stderr}",
        )
        return json.loads(completed.stdout)

    # -- the contract -----------------------------------------------------------------

    def test_the_documented_command_is_the_command_the_code_publishes(self) -> None:
        """One spelling of the registration, shared by the Skill, the validator and these
        tests, so none of the three can drift into describing a command nobody runs."""
        self.assertEqual(self.command, turn_boundary.STOP_HOOK_COMMAND)
        self.assertNotIn("ORCA_QUIESCENCE_RUN_ID=", self.command)

    def test_a_refused_run_blocks_from_a_repository_checkout(self) -> None:
        """The exact case the review probed and found broken, end to end.

        No ``--run-id``, no ``ORCA_QUIESCENCE_RUN_ID``: the entry point is resolved from
        ``CLAUDE_PROJECT_DIR`` and the Run comes from the binding ``turn-end-bind``
        published for this session id. Both halves have to work or there is no block.
        """
        entry = self.install_repository_checkout()
        bound = self.bind(entry)
        self.assertEqual(bound.returncode, 0, bound.stderr)

        decision = self.fire()

        self.assertEqual(decision["decision"], "block")
        self.assertIn(quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED, decision["reason"])
        self.assertIn(self.PLAN_TASK, decision["reason"])
        # The advice in a block has to be pastable too: the reason used to tell the
        # model to re-derive with `python3 tools/run_workflow.py turn-end`, the same
        # relative path that opens from no working directory.
        self.assertNotIn("python3 tools/run_workflow.py", decision["reason"])
        self.assertIn(str(entry / "tools" / "run_workflow.py"), decision["reason"])

    def test_a_refused_run_blocks_from_an_installed_skill_with_no_repository(self) -> None:
        """Layout 4: the project is not this repository at all.

        An installed Skill does not make its own ``tools/`` the hook's working directory,
        which is why a relative entry point could never work here.
        """
        entry = self.install_user_skill()
        self.assertFalse((self.project / SKILL_ROOT.name).exists())
        bound = self.bind(entry)
        self.assertEqual(bound.returncode, 0, bound.stderr)

        decision = self.fire()

        self.assertEqual(decision["decision"], "block")

    def test_a_quiescent_run_is_allowed_through_the_documented_command(self) -> None:
        """The same command, over a run with a live dispatch: no block, no noise."""
        entry = self.install_repository_checkout()
        self.assertEqual(self.bind(entry).returncode, 0)

        decision = self.fire(env=self.hook_env(orca=self._fake_orca(running=True)))

        self.assertNotIn("decision", decision)

    def test_an_unresolvable_entry_point_blocks_when_the_project_holds_run_state(
        self,
    ) -> None:
        """FINAL attempt-2 R1, through the real registered command.

        No layout resolves, so this module never runs and the decision is made by the
        registration's own shell tail. In a project that holds Orca run state that
        decision is ``block``: a boundary that could not execute has not established
        that anything is at rest, and announcing that while ending the turn tells the
        only party who could repair it nothing it can act on.
        """
        empty_home = Path(self.tmp.name) / "empty_home"
        empty_home.mkdir()
        (self.project / "artifacts" / "runs" / self.RUN).mkdir(parents=True)

        decision = self.fire(env=self.hook_env(home=empty_home))

        self.assertEqual(decision["decision"], "block")
        self.assertIn("run_workflow.py", decision["reason"])
        self.assertIn("Repair the registration", decision["reason"])

    def test_an_unresolvable_entry_point_releases_the_turn_at_its_cap(self) -> None:
        """And that refusal is finite, counted by the registration itself.

        A registration nobody repairs must not wedge the session, so the shell tail
        keeps its own counter beside the runs it is refusing on behalf of.
        """
        empty_home = Path(self.tmp.name) / "empty_home"
        empty_home.mkdir()
        (self.project / "artifacts" / "runs" / self.RUN).mkdir(parents=True)
        env = self.hook_env(home=empty_home)

        blocked = [
            self.fire(env=env).get("decision")
            for _ in range(turn_boundary.STOP_HOOK_BLOCK_CAP_DEFAULT)
        ]
        released = self.fire(env=env)

        self.assertEqual(blocked, ["block"] * turn_boundary.STOP_HOOK_BLOCK_CAP_DEFAULT)
        self.assertNotIn("decision", released)
        self.assertIn("releasing this one", released["systemMessage"])

    def test_an_unresolvable_entry_point_is_silent_where_there_is_no_run_state(
        self,
    ) -> None:
        """The one exemption, kept: a project with no ``artifacts/runs`` directories is
        positively unrelated to this boundary, so its turns end silently. A hook that
        gates every unrelated session in every project gets uninstalled, and an
        uninstalled hook enforces nothing."""
        empty_home = Path(self.tmp.name) / "empty_home"
        empty_home.mkdir()

        decision = self.fire(env=self.hook_env(home=empty_home))

        self.assertNotIn("decision", decision)
        self.assertIn("enforcing nothing", decision["systemMessage"])

    def test_an_unreadable_runs_root_blocks_when_the_entry_point_resolves(self) -> None:
        """FINAL attempt-3 R1, through the real command, on a REAL unreadable directory.

        The entry point resolves, so this is the Python boundary answering: the runs root
        holds ``run_x`` and is at mode 000. The shipped build read that as positive
        evidence that the project holds no runs and returned ``{"suppressOutput": true}``
        -- an automatic, silent turn end in a project that may well have a stalled Run in
        it. It has to refuse instead, and say which authority it could not read.
        """
        self.install_repository_checkout()
        runs = self.project / "artifacts" / "runs"
        (runs / self.RUN).mkdir(parents=True)
        deny_directory_read(self, runs)

        decision = self.fire()

        self.assertEqual(decision["decision"], "block")
        self.assertIn("could not be read", decision["reason"])
        self.assertNotIn("suppressOutput", decision)

    def test_an_unreadable_runs_root_blocks_a_session_that_HAD_bound_its_run(
        self,
    ) -> None:
        """The other half of the matrix: the binding itself lives under that root.

        A Coordinator that bound its Run correctly is unattributable again the moment the
        record cannot be read, and the answer must still be a refusal rather than the
        silence an unreadable authority used to buy.
        """
        entry = self.install_repository_checkout()
        self.assertEqual(self.bind(entry).returncode, 0)
        self.assertEqual(self.fire()["decision"], "block")
        deny_directory_read(self, self.project / "artifacts" / "runs")

        decision = self.fire()

        self.assertEqual(decision["decision"], "block")
        self.assertIn("could not be read", decision["reason"])

    def test_an_unreadable_runs_root_blocks_when_no_entry_point_resolves(self) -> None:
        """And the registration's own shell tail reaches the same verdict without us.

        The reviewer's second half of R1: the tail's directory glob cannot establish a
        child directory under a runs root it may not read, so it left its flag at "no
        runs" and took the allow branch -- the same conflation, one layer down, in the
        one code path that runs when this module cannot. The flag now STARTS at
        "unreadable" and only positive evidence moves it off.
        """
        empty_home = Path(self.tmp.name) / "empty_home"
        empty_home.mkdir()
        runs = self.project / "artifacts" / "runs"
        (runs / self.RUN).mkdir(parents=True)
        deny_directory_read(self, runs)

        decision = self.fire(env=self.hook_env(home=empty_home))

        self.assertEqual(decision["decision"], "block")
        self.assertIn("could not be read", decision["reason"])
        self.assertIn("run_workflow.py", decision["reason"])

    def test_the_unreadable_shell_refusal_releases_at_its_cap(self) -> None:
        """R1(b), in the shell: the same finite budget, counted somewhere writable.

        The tail's counter normally lives at the ``artifacts/runs`` root, which is
        exactly the directory this refusal is about. A counter that cannot be written
        never advances and a refusal that never advances never releases, so the tail
        falls outward to the first directory that will take the file. The chain has to
        end in a release, and the file has to be somewhere other than the unreadable
        root.
        """
        empty_home = Path(self.tmp.name) / "empty_home"
        empty_home.mkdir()
        runs = self.project / "artifacts" / "runs"
        (runs / self.RUN).mkdir(parents=True)
        deny_directory_read(self, runs)
        env = self.hook_env(home=empty_home)

        blocked = [
            self.fire(env=env).get("decision")
            for _ in range(turn_boundary.STOP_HOOK_BLOCK_CAP_DEFAULT)
        ]
        released = self.fire(env=env)

        self.assertEqual(blocked, ["block"] * turn_boundary.STOP_HOOK_BLOCK_CAP_DEFAULT)
        self.assertNotIn("decision", released)
        self.assertIn("releasing this one", released["systemMessage"])
        # Written outside the unreadable root, which is the whole reason the chain above
        # could reach a release at all. (The release itself removes it, so this asserts
        # on the block that preceded it.)
        counted_in = sorted(
            str(path.parent.relative_to(self.project))
            for path in (self.project / "artifacts").rglob(
                turn_boundary.STOP_HOOK_UNRESOLVED_STATE_FILENAME
            )
        )
        self.assertNotIn(
            "artifacts/runs",
            counted_in,
            "the shell tail must not try to count inside the root it cannot read",
        )

    def test_the_shell_tail_releases_when_it_cannot_persist_its_own_budget(self) -> None:
        """FINAL adversarial review R1, in the shell, over six consecutive invocations.

        The tail used to run ``printf %s "$N" >"$C" 2>/dev/null``, ignore the outcome and
        emit ``decision: block`` regardless, so a counter it could not write left the next
        process reading zero and refusing again -- six blocks against a cap of three, and
        a session with no way out. Every candidate location is denied here (the runs root,
        ``artifacts``, the project root, and the temp fallback's own counter file), so
        every one of the six must be a release that says the budget could not be recorded.

        The directory ``-w`` tests in the tail are TOCTOU preflights, not proof; what
        makes this pass is that the tail now checks ``printf``'s status and reads the
        value back before it will emit a refusal.
        """
        empty_home = Path(self.tmp.name) / "empty_home_uncountable"
        empty_home.mkdir()
        fake_tmp = Path(self.tmp.name) / "faketmp"
        fake_tmp.mkdir()
        fallback = fake_tmp / turn_boundary.STOP_HOOK_UNRESOLVED_STATE_FILENAME
        fallback.write_text("0", encoding="utf-8")
        deny_write(self, fallback)
        runs = self.project / "artifacts" / "runs"
        (runs / self.RUN).mkdir(parents=True)
        for directory in (runs, self.project / "artifacts", self.project):
            deny_write(self, directory)
        env = {**self.hook_env(home=empty_home), "TMPDIR": str(fake_tmp)}

        decisions = [self.fire(env=env) for _ in range(6)]

        self.assertEqual([decision.get("decision", "release") for decision in decisions], ["release"] * 6)
        for decision in decisions:
            self.assertNotIn("decision", decision)
            self.assertIn("was NOT gated", decision["systemMessage"])
            self.assertIn("could not record the refusal budget", decision["systemMessage"])
        self.assertEqual(
            fallback.read_text(encoding="utf-8"),
            "0",
            "nothing was persisted, which is precisely why no refusal was issued",
        )

    def test_the_shell_tail_still_blocks_where_it_can_persist_the_budget(self) -> None:
        """The control for the test above: the cap is not weakened, only made honest.

        Same unresolvable registration, same run state -- but a writable counter
        location. The refusal has to hold for the full budget and only then release.
        """
        empty_home = Path(self.tmp.name) / "empty_home_countable"
        empty_home.mkdir()
        (self.project / "artifacts" / "runs" / self.RUN).mkdir(parents=True)
        env = self.hook_env(home=empty_home)

        chain = [
            self.fire(env=env).get("decision", "release")
            for _ in range(turn_boundary.STOP_HOOK_BLOCK_CAP_DEFAULT + 1)
        ]

        self.assertEqual(
            chain, ["block"] * turn_boundary.STOP_HOOK_BLOCK_CAP_DEFAULT + ["release"]
        )

    def test_without_a_run_binding_the_same_command_blocks_and_says_how_to_bind(
        self,
    ) -> None:
        """The reviewer's exact probe, and the outcome it must now have.

        Identical layout, identical command, identical run artifacts -- only the
        ``turn-end-bind`` record is missing. This test used to assert that the turn was
        NOT blocked, which locked the defect in: OS-44 is about a Coordinator forgetting
        a required invocation before ending its turn, and allowing the turn when the
        binding is forgotten is that same defect wearing a different command name.
        """
        self.install_repository_checkout()
        (self.project / "artifacts" / "runs" / self.RUN).mkdir(parents=True)

        decision = self.fire()

        self.assertEqual(decision["decision"], "block")
        self.assertIn("turn-end-bind", decision["reason"])
        self.assertIn("bound to no Orca Run", decision["reason"])

    def test_an_unbound_session_is_silent_where_the_project_holds_no_run_state(
        self,
    ) -> None:
        """And the exemption again, through the real command: no runs here, nothing to
        say and nothing to hold."""
        self.install_repository_checkout()

        decision = self.fire()

        self.assertEqual(decision, {"suppressOutput": True})

    def test_an_unexpected_internal_failure_blocks_through_the_real_command(self) -> None:
        """The third fail-open the review named, exercised end to end.

        ``orca gate-list`` answers with a shape no build should produce, which reaches
        the boundary as an unhandled ``TypeError`` after the run has already been
        resolved. That is a defect in the gate, and the gate does not get to conclude
        from its own defect that the turn may end.
        """
        entry = self.install_repository_checkout()
        self.assertEqual(self.bind(entry).returncode, 0)
        broken = self.project / "orca_broken"
        broken.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "verb = sys.argv[2] if len(sys.argv) > 2 else ''\n"
            "result = {'task-list': {'tasks': []}, 'worker-list': {'workers': []},\n"
            "          'gate-list': {'gates': 7}}.get(verb, {})\n"
            "print(json.dumps({'ok': True, 'result': result}))\n",
            encoding="utf-8",
        )
        broken.chmod(0o755)

        decision = self.fire(env=self.hook_env(orca=broken))

        self.assertEqual(decision["decision"], "block")
        self.assertIn(turn_boundary.STOP_HOOK_FAILURE_REASON_CODE, decision["reason"])

    def test_a_released_binding_stops_gating_that_session(self) -> None:
        """A Coordinator that is done with a Run says so, and stops being gated on it."""
        entry = self.install_repository_checkout()
        self.assertEqual(self.bind(entry).returncode, 0)
        self.assertEqual(self.fire()["decision"], "block")

        released = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                str(entry / "tools" / "run_workflow.py"),
                "turn-end-bind",
                "--run-id",
                self.RUN,
                "--artifact-base",
                str(self.project),
                "--release",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**self.hook_env(), turn_boundary.SESSION_ID_ENV: self.SESSION},
        )

        self.assertEqual(released.returncode, 0, released.stderr)
        # The release stops gating this session on that RUN: the run-specific refusal is
        # gone. The project still holds run state, so what remains is the bounded
        # unattributable-session hold, which names no run and asks for a binding.
        after = self.fire()
        self.assertNotIn(quiescence.QUIESCENCE_NEXT_NODE_UNCONSUMED, str(after))
        self.assertIn("bound to no Orca Run", after["reason"])

    def test_binding_fails_loudly_when_there_is_no_session_to_bind(self) -> None:
        """Outside Claude Code there is no session id, and pretending otherwise would
        recreate the defect: a Coordinator believing it is gated when it is not."""
        entry = self.install_repository_checkout()
        env = self.hook_env()
        env.pop(turn_boundary.SESSION_ID_ENV, None)

        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                str(entry / "tools" / "run_workflow.py"),
                "turn-end-bind",
                "--run-id",
                self.RUN,
                "--artifact-base",
                str(self.project),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        self.assertEqual(completed.returncode, turn_boundary.EXIT_UNAVAILABLE)
        self.assertIn(turn_boundary.SESSION_ID_ENV, completed.stderr)

    def test_the_registration_composes_with_the_existing_orca_stop_hook(self) -> None:
        """Both hooks still run. Claude Code merges Stop hooks from every settings
        source, so the risk is not that ours replaces Orca's inside one file -- it is an
        operator pasting over the array. The documented transformation appends, and this
        test then EXECUTES both commands of the merged array, in order, asserting Orca's
        stand-in still ran and ours still blocked.

        The fixture is a temporary settings tree. The live global settings file is
        asserted byte-identical across the whole test.
        """
        live = Path.home() / ".claude" / "settings.json"
        before = live.read_bytes() if live.is_file() else None
        entry = self.install_repository_checkout()
        self.assertEqual(self.bind(entry).returncode, 0)

        marker = self.project / "orca-hook-ran"
        orca_hook = f"touch {marker}"
        settings_path = self.project / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [{"hooks": [{"type": "command", "command": "orca"}]}],
                        "Stop": [
                            {"hooks": [{"type": "command", "command": orca_hook, "timeout": 10}]}
                        ],
                    }
                }
            ),
            encoding="utf-8",
        )

        merged = turn_boundary.merge_stop_hook_registration(
            json.loads(settings_path.read_text(encoding="utf-8")), self.command
        )
        settings_path.write_text(json.dumps(merged), encoding="utf-8")

        commands = [
            hook["command"]
            for entry_group in merged["hooks"]["Stop"]
            for hook in entry_group["hooks"]
        ]
        self.assertEqual(commands, [orca_hook, self.command])
        self.assertEqual(len(merged["hooks"]["SessionStart"]), 1)
        self.assertEqual(
            turn_boundary.merge_stop_hook_registration(merged, self.command), merged
        )

        subprocess.run(  # noqa: S602 - a hook command IS a shell command
            commands[0], shell=True, check=False, cwd=str(self.project), env=self.hook_env()
        )
        decision = self.fire()

        self.assertTrue(marker.is_file(), "the pre-existing Orca Stop hook did not run")
        self.assertEqual(decision["decision"], "block")
        self.assertEqual(live.read_bytes() if live.is_file() else None, before)

    def test_running_the_documented_command_writes_no_settings_file(self) -> None:
        """The hard safety constraint, over the real subprocess this time."""
        live = Path.home() / ".claude" / "settings.json"
        before = live.read_bytes() if live.is_file() else None
        entry = self.install_repository_checkout()
        self.assertEqual(self.bind(entry).returncode, 0)

        self.fire()

        self.assertEqual(live.read_bytes() if live.is_file() else None, before)
        self.assertEqual(
            sorted(path.name for path in self.home.rglob("settings*.json")), []
        )
        self.assertEqual(
            sorted(path.name for path in self.project.rglob("settings*.json")), []
        )



if __name__ == "__main__":
    unittest.main()
