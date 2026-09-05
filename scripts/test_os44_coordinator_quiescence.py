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

import json
import tempfile
import unittest
from os import environ
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts import decision_gate, run_logging
from scripts.deterministic_workflow import quiescence
from scripts.orca_runtime_harness import (
    ACK_MAX_ATTEMPTS,
    DELIVERY_RECOVERED_FIELD,
    DELIVERY_SETTLED_FIELD,
    DELIVERY_SETTLEMENT_CLAIMED_FIELD,
    DELIVERY_STATE_ACKNOWLEDGED,
    DELIVERY_STATE_ACK_FAILED,
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
    without patching the harness itself.
    """

    def __init__(
        self,
        deliveries: list[dict[str, Any]],
        *,
        ack_failures: dict[str, int] | None = None,
    ) -> None:
        self.deliveries = list(deliveries)
        self.ack_failures = dict(ack_failures or {})
        self.commands: list[tuple[str, ...]] = []
        self.acked: list[str] = []
        self.waits = 0

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
                    {"ok": False, "error": {"code": "ack_rejected", "message": "transient"}}
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
            "task-list": {
                "tasks": [
                    {"id": ANALYSIS_TASK, "status": "completed"},
                    {"id": PLAN_TASK, "status": "completed"},
                ]
            },
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

        A dispatch this Coordinator has claimed and not finalized IS something that can
        wake the run, so ending the turn there is correct -- and the audit records the
        verification rather than a violation.
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

        verdict = harness.verify_quiescence("ACTIVE", next_node="PREPARE_PHASE_REVIEWER")

        self.assertTrue(verdict["quiescent"])
        self.assertEqual(verdict["state"], quiescence.ACTIVE_DISPATCH_WAIT)
        self.assertEqual(harness.active_dispatch_count(), 1)
        self.assertEqual(self.events(harness), [run_logging.EVENT_QUIESCENCE_VERIFIED])

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
        self.assertEqual(row[DELIVERY_STATE_FIELD], DELIVERY_STATE_PROCESSED)
        self.assertEqual(
            self.events(harness),
            [
                run_logging.EVENT_DELIVERY_PROCESSED,
                run_logging.EVENT_DELIVERY_SETTLEMENT_CLAIMED,
                run_logging.EVENT_DELIVERY_SETTLED,
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
        with self.assertRaises(OrcaRuntimeError) as raised:
            successor.wait_for_done(PLAN_DISPATCH, PLAN_TASK)

        self.assertIn("timed out", str(raised.exception))
        self.assertEqual(recorder.acked, [PLAN_DELIVERY])
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


if __name__ == "__main__":
    unittest.main()
