#!/usr/bin/env python3
"""OS-14 correction run: the three reproduced defects of the PR #38 independent review.

Every scenario below is driven through PRODUCTION entry points -- the real
``launcher.build_orca_adapter`` / ``build_orca_adapter_for_run``, the real
``OrcaAdapter.start``, the real ``executor.execute_intent_node`` over a real
``FileRuntimeStateStore`` and the real ``launcher.execute_state``.  No test calls
``_prepare_pair``, ``_prepare_role``, ``_assert_preparation_binding``,
``_refuse_preparation``, ``create_fake_terminal``, ``verify_model_identity``,
``reuse_eligible``, ``terminal_for_next_dispatch``, ``adopt_prepared_terminal`` or
``register_terminal``, and none pre-admits a pair, pre-claims a ledger record or
pre-registers a terminal.

  B1  a VALIDATION REPAIR re-used the pair entry's ALREADY-DISPATCHED session without
      the reuse gate ever being asked.
  B2  the (phase, role) non-drift baseline lived only in process memory, so the next
      ITERATION in a successor process stored a DRIFTED model as VERIFIED.
  B3  an absent ``.pair_preparation.json`` was read as a genuine FIRST preparation, so a
      successor duplicated sessions that were still live.

And the two findings of the phase review of the fix for those three:

  F-001  a PRESENT ``.pair_preparation.json`` MISSING one of the three required top-level
         sections was read as positive EMPTY state, which restored B1's "unused" and B2's
         "no baseline yet" for records that had merely been removed.
  F-002  the claimed same-process drift regression launched a driver with NO drift and
         asserted no refusal, so a specifically required verification was absent.

REFERENCE DRIVER, NOT A PROVIDER.  Every green assertion here is evidence about the
WIRING: the reference drivers' observation legs re-read the value their own request legs
stored.  Nothing in this file is evidence about any real Claude or company model.

RESOURCE COUNTS.  Every count is stated for its OWN setup and asserted off that test's
own recorder, which sees only the commands that test issued.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from scripts.deterministic_workflow import launcher, pause_store, recovery_runtime
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.deterministic_workflow.orca_adapter import PAIR_PREPARATION_REFUSAL_CODES
from scripts.deterministic_workflow.runtime_state import (DEFAULT_LEASE_SECONDS,
                                                          FileRuntimeStateStore,
                                                          ManualLeaseClock)
from scripts.test_os14_pair_preparation import (COMPLETED_AT, PHASE, PHASE_UPPER,
                                                REPO_ROOT, REQUIRES_LANGGRAPH, RUN_ID,
                                                ChildProcessRoom, ClaimAuditingStore,
                                                PairRecorder, PairRoom, _AttestingDriver,
                                                final_body, reviewer_body,
                                                scripted_bodies, worker_body)

#: A Worker body with NO decision-gate declaration and no fenced record at all.  This is
#: the OS-42 repairable FORM defect: the engine's own ``validate_settlement_node`` reaches
#: ``PREPARE_REPAIR``, and the repair re-asks the SAME role, the SAME phase and the SAME
#: ``gate_iteration`` -- which is the identity contract B1 must not change.
GATELESS_WORKER_BODY = "# Worker Result\n\nSTATUS: COMPLETE\n"


def repair_bodies(run_id: str = RUN_ID) -> list[str]:
    """Worker (gate-less) -> validation repair -> Reviewer -> Final Reviewer."""
    return [GATELESS_WORKER_BODY, worker_body(run_id=run_id),
            reviewer_body(run_id=run_id), final_body(run_id=run_id)]


class ReusableRecorder(PairRecorder):
    """A recorder that answers ``worker-show`` the way a runtime reports a session that
    really IS reusable: ``releaseState`` inside ``LIVE_RELEASE_STATES`` and an
    ``ownershipState`` inside ``OWNERSHIP_TRANSFERABLE_STATES``.

    ``not_requested`` is the harness's own documented value for "all 25 observed rung-3
    receipts", so this is the MORE realistic receipt, not a weakened one -- and it is the
    only thing this subclass changes.  It is the POSITIVE control for B1: the gate is
    asked, and when every one of its conditions holds it ANSWERS YES.
    """

    def __call__(self, args: tuple[str, ...]) -> tuple[int, str]:
        code, out = super().__call__(args)
        verb = args[1] if len(args) > 1 else args[0]
        if verb == "worker-start":
            self.results["worker-show"] = {
                "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
                "worker": {"state": "settled"},
                "terminalResource": {"releaseState": "not_requested",
                                     "processState": "running",
                                     "ownershipState": "external"}}
            if code == 0:
                # The terminal effect the harness's own docstring reports for 25 of 25
                # observed `worker-start` receipts.  The default recorder omits it, which
                # is what leaves `terminal_effect` unrecorded.
                payload = json.loads(out)
                payload["result"]["effects"] = [{"kind": "terminal",
                                                 "action": "reused"}]
                out = json.dumps(payload)
        return code, out


class _ReentryDriftDriver(_AttestingDriver):
    """A reference driver that resolves ONE routing role's request to a DIFFERENT model
    from its ``after``-th selection for that role onward.

    MODULE-LEVEL on purpose: ``agent_profile.driver_type_id`` refuses a locally defined
    class, and the driver CLASS is part of the launch identity -- so a predecessor and a
    differently CONFIGURED re-entry of the same run must share this one class.

    The request is UNCHANGED across selections: the routing still declares the same model
    for the same ``(run, phase, role)``.  What changes is what the driver honestly
    OBSERVES, which is the real situation the drift legs exist for -- a provider alias
    that moved under a request still spelled the same way.  Still a reference driver: its
    observation leg re-reads the value its own request leg stored, so nothing it proves is
    evidence about any real provider model.
    """

    def __init__(self, *, drift=None, role: str = "worker", after: int = 0) -> None:
        super().__init__()
        self.drift = dict(drift or {})
        self.role = role
        self.after = after
        self.selections: dict[str, int] = {}

    def select_and_verify(self, ticket):
        routing = "reviewer" if str(ticket.role).endswith("reviewer") else "worker"
        seen = self.selections.get(routing, 0) + 1
        self.selections[routing] = seen
        self.resolve_map = (dict(self.drift)
                            if routing == self.role and seen > self.after else {})
        return super().select_and_verify(ticket)


def deliveries(recorder: PairRecorder) -> list[str]:
    """The handle each ``worker-start`` was actually delivered to, in order.

    The REAL delivery, read off the recorded command line -- never a function-call count.
    """
    return [command[command.index("--terminal") + 1] for command in recorder.commands
            if command[1:2] == ("worker-start",) and "--terminal" in command]


# ======================================================================================
# B2  the (phase, role) non-drift baseline must survive an iteration / process change
# ======================================================================================
B2_CHILD_SCRIPT = r'''
import json, os, sys
from pathlib import Path

options = json.loads(sys.argv[1])
sys.path.insert(0, options["repo_root"])

from scripts.deterministic_workflow import launcher, recovery_runtime
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.test_os14_pair_preparation import (ClaimAuditingStore, PairRecorder,
                                                _AttestingDriver, scripted_bodies)

project = Path(options["project"])
os.environ["PATH"] = options["path"]
recorder = PairRecorder(scripted_bodies(options["run_id"]), run_id=options["run_id"],
                        listing=options["listing"],
                        worktree_id=options["worktree_id"])
drift = options.get("drift") or {}
if options.get("driver_class") == "reference":
    driver = InProcessModelDriver(resolve=lambda requested: drift.get(requested,
                                                                     requested))
else:
    driver = _AttestingDriver(resolve_map=drift)


def harness_factory(artifact_base, **kwargs):
    from scripts.orca_runtime_harness import OrcaRuntimeHarness
    os.environ["ORCA_CLI_COMMAND"] = "/opt/orca-dev"
    harness = OrcaRuntimeHarness(Path(artifact_base), **kwargs)
    harness._exec_orca = recorder
    harness.preflight = lambda: {}
    return harness


report = {"construction_error": None, "execute_error": None, "terminal_status": None,
          "terminal_reason": None}
ledger = ClaimAuditingStore(Path(options["ledger"]),
                            clock=ManualLeaseClock(options["clock_start"]))
try:
    adapter = launcher.build_orca_adapter_for_run(
        options["run_id"], artifact_base=project, runtime_state=ledger,
        run_owner="term_child_owner", project_root=project,
        harness_factory=harness_factory, agent_profile_name="split",
        model_driver=driver)
except Exception as exc:
    report["construction_error"] = f"{type(exc).__name__}: {exc}"
    adapter = None

if adapter is not None:
    if options["resume"] == "head":
        head = recovery_runtime.resolve_head(options["run_id"], artifact_base=project)
        state = dict(head.state)
    else:
        state = launcher.build_state({"run_id": options["run_id"], "thread_id": "os14",
                                      "phases": options["phases"],
                                      "risk": options["risk"]})
    try:
        final = launcher.execute_state(state, adapter=adapter, runtime_state=ledger,
                                       artifact_base=project)
        report["terminal_status"] = final.get("terminal_status")
        report["terminal_reason"] = final.get("terminal_reason")
    except BaseException as exc:
        report["execute_error"] = f"{type(exc).__name__}: {exc}"

report["pair_creates"] = len(recorder.pair_titles)
report["pair_titles"] = list(recorder.pair_titles)
report["task_creates"] = sum(1 for c in recorder.commands if c[1:2] == ("task-create",))
report["worker_starts"] = sum(1 for c in recorder.commands if c[1:2] == ("worker-start",))
report["child_deliveries"] = [c[c.index("--terminal") + 1] for c in recorder.commands
                              if c[1:2] == ("worker-start",) and "--terminal" in c]
report["sends"] = recorder.count("terminal", "send")
report["claim_log"] = [list(item) for item in ledger.claim_log]
report["pid"] = os.getpid()
print("RESULT_JSON " + json.dumps(report))
'''


class _CorrectionChildRoom(ChildProcessRoom):
    """A `ChildProcessRoom` plus ONE addition: a successor process whose report also
    names the HANDLE each delivery was issued to, and which can resume either the run's
    committed head (B2's iteration boundary) or a freshly rebuilt state (B1/B3).

    It also carries the predecessor that settles gate iteration 1 and dies BEFORE
    iteration 2 prepares anything.

    The predecessor stops by raising inside the THIRD ``adapter.start`` -- after the
    Reviewer's FAIL has been applied and the engine has already prepared iteration 2's
    intent, and strictly before iteration 2's preparation writes or creates anything.  So
    iteration 2 has NO preparation entry, which is exactly the state B2 is about.
    """

    def predecessor_at_iteration_boundary(self, *, verdict="FAIL"):
        launch = self.launch(bodies=[worker_body(), reviewer_body(verdict=verdict)],
                             driver=_AttestingDriver())
        production_start = launch.adapter.start
        seen = {"starts": 0}

        def counting_start(intent, **kwargs):
            seen["starts"] += 1
            if seen["starts"] == 3:
                raise RuntimeError("the predecessor died before iteration 2 prepared")
            return production_start(intent, **kwargs)

        launch.adapter.start = counting_start
        try:
            self.execute(launch)
        except BaseException:                     # the predecessor "dies" here
            pass
        launch.adapter.start = production_start
        self.assertEqual(seen["starts"], 3, "iteration 2's dispatch was reached")
        self.assertEqual(len(launch.recorder.pair_titles), 2,
                         "only iteration 1's pair was ever prepared")
        self.assertIsNone(launch.adapter.pair_preparation.entry(PHASE, 2, "worker"),
                          "iteration 2 has NO preparation entry, which is B2's premise")
        head = recovery_runtime.resolve_head(RUN_ID, artifact_base=self.project)
        self.assertIsNotNone(head, "the predecessor committed a real checkpoint head")
        return launch

    def successor(self, launch, *, drift=None, resume="head", run_id=RUN_ID,
                  ledger_name=None, listing=None, driver_class="attesting"):
        """Spawn the REAL successor `python3` process over the predecessor's own ledger."""
        self._generation += 1
        options = {
            "repo_root": str(REPO_ROOT), "project": str(self.project),
            "ledger": str(self.root / (ledger_name or self.LEDGER_NAME)),
            "clock_start": self._epoch + (DEFAULT_LEASE_SECONDS + 1.0) * self._generation,
            "run_id": run_id, "worktree_id": "repo_os14::/project",
            "listing": (listing if listing is not None
                        else [{"handle": handle, "title": title, "orphaned": False}
                              for handle, title in launch.recorder.created_pairs]),
            "drift": drift or {}, "phases": [PHASE_UPPER], "risk": "high",
            "path": str(self.binaries), "resume": resume,
            "driver_class": driver_class,
        }
        script = self.root / "b2_child.py"
        script.write_text(B2_CHILD_SCRIPT, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(script), json.dumps(options)],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300)
        marker = "RESULT_JSON "
        line = next((line for line in completed.stdout.splitlines()
                     if line.startswith(marker)), None)
        self.assertIsNotNone(
            line, f"child produced no report\nSTDOUT:{completed.stdout}\n"
                  f"STDERR:{completed.stderr}")
        report = json.loads(line[len(marker):])
        self.assertNotEqual(report["pid"], os.getpid(),
                            "this subcase must run in a REAL separate OS process")
        return report


# ======================================================================================
# B1  a validation repair may not deliver to an already-dispatched session without the
#     existing reuse gate
# ======================================================================================
@REQUIRES_LANGGRAPH
class B1ValidationRepairReuseTests(PairRoom):
    """The repair's ``gate_iteration`` and artifact path are UNCHANGED; what changes is
    that an already-used session is only re-used through the shipped reuse gate."""

    def test_the_repair_does_not_redeliver_to_the_ineligible_session(self) -> None:
        """B1, the defect itself.

        Before the fix the second delivery went to the SAME handle as the first, although
        the shipped gate would have refused it (``release_state_not_live`` /
        ``ownership_not_transferable`` / ``terminal_effect_unrecorded``).
        """
        launch = self.launch(bodies=repair_bodies())
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        sent = deliveries(launch.recorder)
        self.assertEqual(len(sent), 4, "worker, repair, phase reviewer, final reviewer")
        self.assertNotEqual(
            sent[0], sent[1],
            "the validation repair delivered a SECOND time to the session the first "
            f"dispatch had already used (deliveries={sent})")
        self.assertEqual(sent.count(sent[0]), 1,
                         "the already-dispatched session received exactly ONE delivery")

    def test_the_reuse_gate_was_actually_asked_and_refused_by_name(self) -> None:
        """The gate is REACHED, not merely written: its own diagnostic names the
        conditions that refused."""
        launch = self.launch(bodies=repair_bodies())
        self.execute(launch)
        decision = launch.harness.last_reuse_decision
        self.assertIsNotNone(
            decision, "the reuse gate was never consulted for the repair's delivery")
        self.assertIs(decision["eligible"], False)
        self.assertIn("release_state_not_live", decision["reasons"])
        self.assertIn("ownership_not_transferable", decision["reasons"])

    def test_the_superseding_session_is_recorded_with_its_provenance(self) -> None:
        """A new session replaces the ineligible one SAFELY: a new ``create_attempt``
        under the same entry, naming what it supersedes and why."""
        launch = self.launch(bodies=repair_bodies())
        self.execute(launch)
        entry = launch.entry("worker")
        self.assertEqual(entry["stage"], "VERIFIED")
        self.assertEqual(entry["create_attempt"], "2")
        self.assertTrue(entry["supersedes_digest"],
                        "the new attempt names the session it replaced")
        self.assertIn("release_state_not_live", entry["supersedes_reason"])

    def test_the_repair_keeps_the_same_gate_iteration_and_artifact_path(self) -> None:
        """The OS-42 repair identity is a contract and B1's fix does not touch it."""
        launch = self.launch(bodies=repair_bodies())
        self.execute(launch)
        pairs = launch.adapter.pair_preparation.pairs()
        self.assertEqual(sorted(pairs), [f"{PHASE}#1"],
                         "the repair stayed on the SAME (phase, gate_iteration) pair")

    def test_the_past_use_of_the_superseded_session_is_never_overwritten(self) -> None:
        """``adoption``/re-registration never erases past use state or refusal grounds."""
        launch = self.launch(bodies=repair_bodies())
        self.execute(launch)
        store = launch.adapter.pair_preparation
        first = deliveries(launch.recorder)[0]
        use = store.session_use(pause_store.terminal_use_digest(first))
        self.assertIsNotNone(use, "the first session's USE is still on record")
        self.assertEqual(use["stage"], "DELIVERED")
        self.assertTrue(use["dispatch_id"])

    def test_the_first_delivery_of_an_unused_prepared_session_still_happens(self) -> None:
        """POSITIVE CONTROL: an unused prepared session is delivered to, as before."""
        launch = self.launch()
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        sent = deliveries(launch.recorder)
        self.assertEqual(sent[0], launch.recorder.pair_handles[0],
                         "the Worker's prepared session received the first delivery")
        self.assertEqual(len(launch.recorder.pair_titles), 2,
                         "no extra session was prepared")

    def test_the_normal_worker_to_reviewer_round_is_unchanged(self) -> None:
        """POSITIVE CONTROL: the Reviewer adopts ITS prepared session and nothing is
        re-created -- the counterpart role is not a delivery target of the Worker's turn
        and is therefore never put through the reuse gate."""
        launch = self.launch()
        self.execute(launch)
        sent = deliveries(launch.recorder)
        self.assertEqual(sent[:2], list(launch.recorder.pair_handles),
                         "worker then reviewer, each on its OWN prepared session")
        self.assertEqual(len(launch.recorder.pair_titles), 2)

    def test_an_eligible_session_is_reused_through_the_gate(self) -> None:
        """POSITIVE CONTROL: when every reuse condition holds, the gate says YES and the
        repair is delivered to the SAME session -- no new session is prepared."""
        launch = self.launch(recorder=ReusableRecorder(repair_bodies()))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        decision = launch.harness.last_reuse_decision
        self.assertIsNotNone(decision)
        self.assertIs(decision["eligible"], True, decision["reasons"])
        sent = deliveries(launch.recorder)
        self.assertEqual(sent[0], sent[1],
                         "the gate PERMITTED the reuse, so the repair reused the session")
        self.assertEqual(len(launch.recorder.pair_titles), 2,
                         "an eligible reuse prepares NO new session")


@REQUIRES_LANGGRAPH
class B1RestartedSessionUseTests(_CorrectionChildRoom):
    """A used session, met by a REAL successor process, is never silently re-delivered."""

    def test_a_used_session_after_a_restart_is_not_redelivered(self) -> None:
        launch = self.launch(bodies=[worker_body()])
        try:
            self.execute(launch)                  # dies when the Reviewer body is asked
        except BaseException:
            pass
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        used = deliveries(launch.recorder)[0]
        report = self.successor(launch, resume="fresh", driver_class="reference")
        self.assertNotIn(used, report["child_deliveries"],
                         "a successor process re-delivered to a session a dead "
                         f"predecessor had already used ({used})")


@REQUIRES_LANGGRAPH
class B2RoleModelBaselineTests(_CorrectionChildRoom):
    """A role's resolved model may not change inside one run, whatever process or
    iteration asks."""

    DRIFT = {"glm-5.2": "glm-5.9-drifted"}

    def test_a_drifted_model_at_the_next_iteration_is_refused_by_name(self) -> None:
        """B2, the defect itself.

        Before the fix the successor stored ``glm-5.9-drifted`` as ``VERIFIED`` for the
        same (run, phase, role) whose durable record said ``glm-5.2``, and went on to
        create the Task.  The ONLY thing that stopped the delivery was an unrelated
        ``DECISION_GATE_INPUT_UNBOUND``, which is not a model check.
        """
        launch = self.predecessor_at_iteration_boundary()
        report = self.successor(launch, drift=self.DRIFT)
        reason = report["terminal_reason"] or {}
        self.assertEqual(report["terminal_status"], "BLOCKED",
                         f"status={report['terminal_status']} reason={reason} "
                         f"execute_error={report['execute_error']}")
        self.assertEqual(reason.get("code"), "PAIR_PREPARATION_MODEL_DRIFT",
                         f"the refusal must NAME the model drift; got {reason}")

    def test_the_drifted_iteration_creates_no_task_and_delivers_nothing(self) -> None:
        launch = self.predecessor_at_iteration_boundary()
        report = self.successor(launch, drift=self.DRIFT)
        self.assertEqual(report["task_creates"], 0, "NO Task was created")
        self.assertEqual(report["worker_starts"], 0, "NOTHING was delivered")
        self.assertEqual(report["sends"], 0)

    def test_the_drifted_value_is_never_left_in_an_approved_state(self) -> None:
        launch = self.predecessor_at_iteration_boundary()
        self.successor(launch, drift=self.DRIFT)
        store = launch.adapter.pair_preparation
        second = store.entry(PHASE, 2, "worker")
        if second is not None:
            self.assertNotEqual(second["stage"], "VERIFIED",
                                "a drifted identity was left VERIFIED")
            self.assertNotEqual(second["resolved_model_observed"], "glm-5.9-drifted")
        history = store.role_history(PHASE, "worker")
        self.assertIsNotNone(history, "the run's durable baseline is still readable")
        self.assertEqual(history["resolved_model"], "glm-5.2",
                         "HISTORY is preserved, never replaced by the refused value")

    def test_the_same_model_at_the_next_iteration_proceeds(self) -> None:
        """POSITIVE CONTROL: history refuses a CHANGE, it is not a general stop."""
        launch = self.predecessor_at_iteration_boundary()
        report = self.successor(launch, drift={})
        reason = report["terminal_reason"] or {}
        self.assertNotEqual(reason.get("code"), "PAIR_PREPARATION_MODEL_DRIFT",
                            "an unchanged model is not drift")
        self.assertEqual(report["pair_creates"], 2,
                         "iteration 2 prepared its own pair and got past verification")

    def test_a_genuinely_new_run_gets_an_independent_baseline(self) -> None:
        """POSITIVE CONTROL: the baseline is RUN-scoped, so a new run may resolve a role
        to a different model."""
        first = self.launch(bodies=scripted_bodies())
        self.assertEqual(self.execute(first).get("terminal_status"), "COMPLETED")
        other = "run_os14b"
        second = self.launch(
            recorder=PairRecorder(scripted_bodies(other), run_id=other),
            driver=_AttestingDriver(resolve_map=self.DRIFT),
            ledger_name="ledger_second.json", run_id=other)
        final = self.execute(second)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         f"a NEW run must not inherit another run's baseline: "
                         f"{final.get('terminal_reason')}")
        self.assertEqual(
            second.adapter.pair_preparation.role_history(PHASE, "worker")
            ["resolved_model"], "glm-5.9-drifted")


@REQUIRES_LANGGRAPH
class B2SameProcessBaselineTests(PairRoom):
    """The same baseline, inside one process and one gate iteration.

    The re-entry is REAL: ``OrcaAdapter.start`` runs again for the SAME
    ``(run, phase, gate_iteration)``, re-verifies BOTH roles' sessions, and the driver
    resolves the worker's unchanged request to a DIFFERENT model on that second pass.

    OS-14 BUGFIX (review F-002).  The case this class used to hold launched
    ``_AttestingDriver(resolve_map={})``, which produces NO drift, completed an ordinary
    unchanged-model run and asserted only the initial baseline -- so it was named for
    evidence it never gathered.  It is replaced below by the real drift, and the
    unchanged-model leg it actually exercised is kept as a NAMED positive control.
    """

    DRIFT = {"glm-5.2": "glm-5.9-drifted"}

    #: The worker's selections per pass: the `_prepare_pair` pre-pass and the Gate B
    #: re-verification of the delivery.  So `after=2` is "clean for the whole first pass,
    #: drifted from the re-entry onward".
    SELECTIONS_PER_PASS = 2

    def drifting_reentry(self):
        """Run the pair round with a worker whose SECOND pass observes a different model."""
        launch = self.launch(
            driver=_ReentryDriftDriver(drift=self.DRIFT,
                                       after=self.SELECTIONS_PER_PASS),
            bodies=scripted_bodies())
        raised: list[BaseException] = []
        try:
            self.execute(launch)
        except BaseException as exc:         # the typed refusal, propagated unchanged
            raised.append(exc)
        self.assertEqual(launch.driver.selections.get("worker"),
                         self.SELECTIONS_PER_PASS + 1,
                         "the re-entry really did ask the driver again for the SAME "
                         "(run, phase, role)")
        return launch, (raised[0] if raised else None)

    def test_same_process_drift_inside_one_iteration_is_refused_by_name(self) -> None:
        """F-002: the refusal NAMES the model, and it is a model check that produces it.

        In ONE process the shipped OS-49 barrier still holds the role's baseline in
        memory, so leg (i) is what refuses and it refuses by its own name,
        ``model_selection_ambiguous``, strictly before any delivery act.  The adapter's
        durable ``PAIR_PREPARATION_MODEL_DRIFT`` is the leg for the case memory cannot
        answer -- a new process or a new harness -- and is asserted by
        `B2RoleModelBaselineTests` and `B2DurableBaselineReentryTests`.
        """
        _launch, error = self.drifting_reentry()
        self.assertIsNotNone(error, "a same-process drift completed without a refusal")
        self.assertEqual(type(error).__name__, "OrcaRuntimeError", str(error))
        self.assertIn("model_selection_ambiguous", str(error))
        self.assertIn("glm-5.9-drifted", str(error))
        self.assertIn("'glm-5.2'", str(error),
                      "the refusal cites the baseline it refused against")

    def test_the_drifted_reentry_delivers_nothing_and_creates_no_further_task(self):
        """The round that drifted issues NO delivery and NO Task of its own.

        The ONE delivery and ONE ``task-create`` in the counts below are the FIRST,
        undrifted round's -- the drift is observed on the re-entry that follows it, and
        that re-entry adds neither.
        """
        launch, _error = self.drifting_reentry()
        sent = deliveries(launch.recorder)
        self.assertEqual(len(sent), 1,
                         f"only the first, undrifted round was delivered: {sent}")
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1,
                         "the drifted re-entry created no Task of its own")
        self.assertEqual(launch.recorder.count("terminal", "send"), 0)
        self.assertEqual(len(launch.recorder.pair_titles), 2,
                         "the drifted re-entry prepared no further session")

    def test_the_rejected_session_keeps_no_model_verification_authority(self) -> None:
        """Selection already ran, so the session must not stay advertised as verified."""
        launch, _error = self.drifting_reentry()
        handle = launch.recorder.pair_handles[0]
        row = launch.harness.ledger_terminal(handle)
        self.assertEqual(row.get("resolved_model", ""), "",
                         f"the rejected session still advertises a resolved model: {row}")
        self.assertEqual(row.get("model_selection_state", ""), "",
                         f"the rejected session still advertises a selection state: {row}")

    def test_the_durable_baseline_and_observation_survive_the_refusal(self) -> None:
        """HISTORY is the grounds for the refusal and is never replaced by the refused
        value -- not in the run-scoped row and not in the entry's own observation."""
        launch, _error = self.drifting_reentry()
        history = launch.adapter.pair_preparation.role_history(PHASE, "worker")
        self.assertEqual(history["resolved_model"], "glm-5.2")
        entry = launch.entry("worker")
        self.assertEqual(entry["resolved_model_observed"], "glm-5.2")
        self.assertNotIn("glm-5.9-drifted", json.dumps(
            launch.adapter.pair_preparation.pairs()),
            "the refused identity was written nowhere in the preparation entries")

    def test_an_unchanged_model_across_the_same_reentry_proceeds(self) -> None:
        """POSITIVE CONTROL, separately named: this is the leg the removed case actually
        exercised.  The same re-entry, the same driver class, NO drift -- and the run
        completes with the baseline it established on its first pass."""
        launch = self.launch(driver=_ReentryDriftDriver(drift={}),
                             bodies=scripted_bodies())
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        self.assertGreaterEqual(launch.driver.selections["worker"],
                                self.SELECTIONS_PER_PASS + 1,
                                "the worker's session really was re-verified on the "
                                "same re-entry the drifted case uses")
        self.assertEqual(
            launch.adapter.pair_preparation.role_history(PHASE, "worker")
            ["resolved_model"], "glm-5.2")

    def test_a_same_iteration_recovery_to_the_baseline_is_accepted(self) -> None:
        """A retry that resolves BACK to the role's baseline is RECOVERY, not drift."""
        launch = self.launch(driver=_AttestingDriver(), bodies=repair_bodies())
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))


@REQUIRES_LANGGRAPH
class B2DurableBaselineReentryTests(_CorrectionChildRoom):
    """A same-OS-process re-entry whose harness memory is EMPTY, so only the DURABLE
    baseline can refuse the drift.

    This is the B2 fix's own leg, met without a process boundary: the predecessor dies at
    the admission boundary and a second adapter/harness is built over the SAME run through
    the real adoption door.  The new harness has never observed this role, so the shipped
    in-memory OS-49 leg cannot answer -- exactly the state in which the pre-fix code stored
    the drifted value as VERIFIED.
    """

    DRIFT = {"glm-5.2": "glm-5.9-drifted"}

    def reenter(self, *, drift):
        launch = self.predecessor_at_admission(
            driver=_ReentryDriftDriver(drift={}))
        self.assertEqual(
            launch.adapter.pair_preparation.role_history(PHASE, "worker")
            ["resolved_model"], "glm-5.2",
            "the predecessor left the durable baseline behind")
        recorder = PairRecorder(scripted_bodies(),
                                listing=[{"handle": handle, "title": title,
                                          "orphaned": False}
                                         for handle, title
                                         in launch.recorder.created_pairs])
        adapter = self.adopt(recorder=recorder,
                             driver=_ReentryDriftDriver(drift=drift),
                             ledger=launch.ledger, profile_name="split")
        final = self.execute(
            launch,
            state=launcher.build_state({"run_id": RUN_ID, "thread_id": "os14",
                                        "phases": [PHASE_UPPER], "risk": "high"}),
            adapter=adapter, ledger=launch.ledger)
        return launch, adapter, recorder, final

    def test_the_durable_baseline_refuses_the_drift_in_this_process(self) -> None:
        launch, adapter, recorder, final = self.reenter(drift=self.DRIFT)
        self.assertBlocked(final, "PAIR_PREPARATION_MODEL_DRIFT")
        self.assertNoEffects(recorder)
        self.assertEqual(deliveries(recorder), [])
        self.assertEqual(
            adapter.pair_preparation.role_history(PHASE, "worker")["resolved_model"],
            "glm-5.2", "HISTORY is preserved, never replaced by the refused value")
        handle = launch.recorder.pair_handles[0]
        row = adapter.captured["harness"].ledger_terminal(handle)
        self.assertEqual(row.get("resolved_model", ""), "",
                         f"the rejected session kept its verification authority: {row}")

    def test_an_unchanged_model_is_not_refused_by_the_durable_baseline(self) -> None:
        """POSITIVE CONTROL: the durable baseline refuses a CHANGE, not a re-entry."""
        _launch, _adapter, _recorder, final = self.reenter(drift={})
        reason = final.get("terminal_reason") or {}
        self.assertNotEqual(reason.get("code"), "PAIR_PREPARATION_MODEL_DRIFT",
                            f"an unchanged model is not drift: {reason}")


# ======================================================================================
# B3  an absent preparation record is not a first preparation
# ======================================================================================
@REQUIRES_LANGGRAPH
class B3PreparationRecordLossTests(_CorrectionChildRoom):
    """Prepare, stop before ``create_task``, delete ONLY ``.pair_preparation.json``, then
    re-enter the same intent from a REAL successor process."""

    def _delete_only_the_preparation_document(self) -> Path:
        path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.project)
        self.assertTrue(path.is_file(), "the preparation document existed")
        path.unlink()
        self.assertTrue(
            pause_store.pair_binding_path(RUN_ID, artifact_base=self.project).is_file(),
            "the launch binding is KEPT")
        return path

    def test_a_lost_preparation_record_blocks_before_any_new_effect(self) -> None:
        """B3, the defect itself.

        Before the fix the successor created TWO more sessions under the SAME titles as
        the predecessor's still-live ones, created three Tasks and delivered three
        dispatches.
        """
        launch = self.predecessor_at_admission()
        self._delete_only_the_preparation_document()
        report = self.successor(launch, resume="fresh", driver_class="reference")
        reason = report["terminal_reason"] or {}
        self.assertEqual(report["terminal_status"], "BLOCKED",
                         f"status={report['terminal_status']} reason={reason} "
                         f"execute_error={report['execute_error']}")
        self.assertEqual(reason.get("code"), "PAIR_PREPARATION_RECORD_LOST",
                         f"the refusal must NAME the loss; got {reason}")
        self.assertEqual(report["pair_creates"], 0, "NO additional session was created")
        self.assertEqual(report["task_creates"], 0, "NO additional Task was created")
        self.assertEqual(report["worker_starts"], 0, "NOTHING was delivered")
        self.assertEqual(report["sends"], 0)

    def test_the_successor_adopts_no_title_only_candidate(self) -> None:
        """The predecessor's sessions are LISTED and title-matching, and are still not
        adopted: a title is not an identity."""
        launch = self.predecessor_at_admission()
        self._delete_only_the_preparation_document()
        report = self.successor(launch, resume="fresh", driver_class="reference")
        self.assertEqual(report["child_deliveries"], [],
                         "no listed candidate was adopted and delivered to")

    def test_the_empty_record_is_not_silently_regenerated(self) -> None:
        launch = self.predecessor_at_admission()
        path = self._delete_only_the_preparation_document()
        self.successor(launch, resume="fresh", driver_class="reference")
        if path.is_file():
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document.get("pairs"), {},
                             "a refused re-entry wrote no preparation entry")

    def test_a_genuine_first_preparation_still_proceeds(self) -> None:
        """POSITIVE CONTROL: with nothing ever prepared, the run prepares normally."""
        launch = self.launch()
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        self.assertEqual(len(launch.recorder.pair_titles), 2)


@REQUIRES_LANGGRAPH
class B3SameProcessRecordLossTests(_CorrectionChildRoom):
    """The same loss, met in the SAME process: the marker is durable, not per-process.

    The predecessor stops at the admission boundary -- both roles ``VERIFIED``, no Task
    -- so the ledger record it leaves is ``CLAIMED`` and the re-entry takes the real
    recovery ladder's LOOKUP rung rather than the unsupported resume rung.
    """

    def test_an_in_process_reentry_over_a_deleted_record_blocks(self) -> None:
        launch = self.predecessor_at_admission()
        path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.project)
        self.assertTrue(path.is_file())
        path.unlink()
        recorder = PairRecorder(scripted_bodies(),
                                listing=[{"handle": handle, "title": title,
                                          "orphaned": False}
                                         for handle, title
                                         in launch.recorder.created_pairs])
        adapter = self.adopt(recorder=recorder,
                             driver=InProcessModelDriver(resolve=lambda r: r),
                             ledger=launch.ledger, profile_name="split")
        final = self.execute(
            launch,
            state=launcher.build_state({"run_id": RUN_ID, "thread_id": "os14",
                                        "phases": [PHASE_UPPER], "risk": "high"}),
            adapter=adapter, ledger=launch.ledger)
        self.assertBlocked(final, "PAIR_PREPARATION_RECORD_LOST")
        self.assertNoEffects(recorder)


# ======================================================================================
# F-001  a PRESENT preparation document missing a required section is never positive
#        empty state
# ======================================================================================
def strip_section(path: Path, section: str) -> dict:
    """Remove exactly ONE top-level section from the preparation document on disk.

    The whole fixture: a TRUNCATED document, not a corrupted one.  Everything else -- the
    schema version, the other two sections and every row in them -- is left byte-for-byte
    as the production writer left it, which is what makes the absence indistinguishable
    from "nothing was ever recorded here" to a reader that uses ``get(section, {})``.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    if section not in document:
        raise AssertionError(f"{section!r} was not there to remove: {sorted(document)}")
    document.pop(section)
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return document


class _SectionRemovedAtTheRepair:
    """The shared machinery for "one required section is removed, met by the repair".

    A plain mixin rather than a `TestCase`: a collectible base class would run its own
    cases a second time under its own name, and `PairRoom.setUp` builds ONE project per
    test, so the three sections cannot share a single `subTest` loop either -- they would
    share one artifact base and one run id and stop being independent.  One concrete class
    per section, each naming the section it removes.
    """

    #: The section this class removes.
    SECTION = ""
    #: The named refusal(s) that are CORRECT for removing it.
    CODES: tuple[str, ...] = ("PAIR_PREPARATION_RECORD_CORRUPT",)

    def run_with_the_section_removed_before_the_repair(self):
        """Drive the real repair workflow and remove the section between the first
        delivery and the repair's own ``start``.

        The hook touches the DOCUMENT only -- it calls no adapter or harness internal and
        then hands the production ``start`` the intent unchanged.
        """
        launch = self.launch(bodies=repair_bodies())
        path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.project)
        production_start = launch.adapter.start
        seen = {"starts": 0, "removed": False}

        def start_with_the_section_removed(intent, **kwargs):
            seen["starts"] += 1
            if seen["starts"] == 2:               # the validation repair's own start
                strip_section(path, self.SECTION)
                seen["removed"] = True
            return production_start(intent, **kwargs)

        launch.adapter.start = start_with_the_section_removed
        final = self.execute(launch)
        launch.adapter.start = production_start
        self.assertTrue(seen["removed"],
                        "the repair's start was reached and the section was removed")
        return launch, final

    def test_the_truncated_document_blocks_the_repair_by_name(self) -> None:
        _launch, final = self.run_with_the_section_removed_before_the_repair()
        self.assertEqual(final.get("terminal_status"), "BLOCKED",
                         final.get("terminal_reason"))
        self.assertIn((final.get("terminal_reason") or {}).get("code"), self.CODES)
        # WHICH section was missing is named by the store's own refusal message, which
        # `F001SectionSetTests.test_each_removed_section_is_refused_by_name` asserts; the
        # BLOCKED projection carries the closed CODE, as every other refusal does.

    def test_the_used_session_receives_no_second_delivery(self) -> None:
        launch, _final = self.run_with_the_section_removed_before_the_repair()
        sent = deliveries(launch.recorder)
        self.assertEqual(len(sent), 1,
                         f"the repair was delivered over a truncated document: {sent}")
        self.assertEqual(sent.count(sent[0]), 1,
                         "the already-dispatched session received exactly ONE delivery")

    def test_the_reuse_gate_is_not_bypassed_and_nothing_new_is_created(self) -> None:
        """The refusal lands BEFORE the reuse question is reached, so the gate is not
        answered -- and, crucially, not answered YES by an invented empty section."""
        launch, _final = self.run_with_the_section_removed_before_the_repair()
        decision = launch.harness.last_reuse_decision
        self.assertFalse(
            decision is not None and decision["eligible"] is True,
            f"a truncated document produced an ELIGIBLE reuse verdict: {decision}")
        self.assertEqual(len(launch.recorder.pair_titles), 2,
                         "no session was created after the truncation was met")
        # The same statement read off the RAW command log rather than the recorder's
        # convenience list: exactly the first, intact round's two PAIR `terminal create`s.
        # (The run's own objective terminal is a third `terminal create` issued by the
        # launch itself, before any preparation, and is not a pair session.)
        pair_creates = [command for command in launch.recorder.commands
                        if command[:2] == ("terminal", "create")
                        and any(f"{RUN_ID}-pair-" in part for part in command)]
        self.assertEqual(len(pair_creates), 2, pair_creates)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 1,
                         "only the first, intact round's Task exists")
        self.assertEqual(launch.recorder.count("terminal", "send"), 0)

    def test_an_intact_document_still_runs_the_repair_to_completion(self) -> None:
        """POSITIVE CONTROL: the same workflow, nothing removed."""
        launch = self.launch(bodies=repair_bodies())
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        self.assertEqual(len(deliveries(launch.recorder)), 4)


@REQUIRES_LANGGRAPH
class F001MissingSessionsSectionTests(_SectionRemovedAtTheRepair, PairRoom):
    """(B1) The APPEND-ONLY session USE ledger is removed after the first delivery.

    Before the F-001 fix this read back as "this session was never delivered to", so the
    validation repair re-delivered to the handle the first dispatch had already used and
    the run COMPLETED -- with ``last_reuse_decision`` still ``None``, because the reuse
    gate was never even asked.  This is the reviewer's own F-001 reproduction.
    """

    SECTION = "sessions"


@REQUIRES_LANGGRAPH
class F001MissingPairsSectionTests(_SectionRemovedAtTheRepair, PairRoom):
    """The THIRD required section, so that EVERY one has a workflow regression.

    ``pairs`` was never the permissive hole the review reproduced: an empty ``pairs``
    reads as "nothing was prepared", which the B3 marker already refuses as
    ``PAIR_PREPARATION_RECORD_LOST``.  Either name is therefore a CORRECT refusal here --
    what must never happen is a second delivery, and that is what this class pins.
    """

    SECTION = "pairs"
    CODES = ("PAIR_PREPARATION_RECORD_CORRUPT", "PAIR_PREPARATION_RECORD_LOST")


@REQUIRES_LANGGRAPH
class F001MissingRoleHistorySectionTests(_CorrectionChildRoom):
    """(B2) The run-scoped non-drift baseline section is removed between iterations.

    Before the F-001 fix this read back as "this role has no baseline yet", so the
    successor process established ``glm-5.9-drifted`` as a NEW baseline, stored
    ``design#2.worker`` as ``VERIFIED`` and created its Task.
    """

    DRIFT = {"glm-5.2": "glm-5.9-drifted"}

    def successor_over_the_truncated_history(self, *, drift):
        launch = self.predecessor_at_iteration_boundary()
        path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.project)
        remaining = strip_section(path, "role_history")
        self.assertIn("sessions", remaining, "ONLY role_history was removed")
        self.assertTrue(remaining["pairs"], "iteration 1's entries are still there")
        report = self.successor(launch, drift=drift)
        return launch, report

    def test_the_truncated_history_blocks_iteration_two_by_name(self) -> None:
        _launch, report = self.successor_over_the_truncated_history(drift=self.DRIFT)
        reason = report["terminal_reason"] or {}
        self.assertEqual(report["terminal_status"], "BLOCKED",
                         f"status={report['terminal_status']} reason={reason} "
                         f"execute_error={report['execute_error']}")
        self.assertEqual(reason.get("code"), "PAIR_PREPARATION_RECORD_CORRUPT",
                         f"a truncated history must refuse BY NAME; got {reason}")

    def test_the_truncated_history_creates_no_session_task_or_delivery(self) -> None:
        _launch, report = self.successor_over_the_truncated_history(drift=self.DRIFT)
        self.assertEqual(report["pair_creates"], 0, "NO session was created")
        self.assertEqual(report["task_creates"], 0, "NO Task was created")
        self.assertEqual(report["worker_starts"], 0, "NOTHING was delivered")
        self.assertEqual(report["child_deliveries"], [])
        self.assertEqual(report["sends"], 0)

    def test_a_truncated_history_is_not_a_new_baseline_opportunity(self) -> None:
        """The refused value is established NOWHERE: not as a history row, not as an
        entry observation and not as a VERIFIED iteration-2 entry."""
        launch, _report = self.successor_over_the_truncated_history(drift=self.DRIFT)
        path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.project)
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("glm-5.9-drifted", json.dumps(document),
                         "the drifted identity was written into the document")
        self.assertNotIn(f"{PHASE}#2", document["pairs"],
                         "iteration 2 wrote no preparation entry at all")

    def test_the_same_truncation_refuses_an_undrifted_iteration_too(self) -> None:
        """The refusal is about the RECORD, not about the drift: a missing required
        section is refused even when this pass would have resolved to the same model."""
        _launch, report = self.successor_over_the_truncated_history(drift={})
        reason = report["terminal_reason"] or {}
        self.assertEqual(reason.get("code"), "PAIR_PREPARATION_RECORD_CORRUPT", reason)
        self.assertEqual(report["task_creates"], 0)
        self.assertEqual(report["worker_starts"], 0)

    def test_an_intact_history_still_lets_the_next_iteration_proceed(self) -> None:
        """POSITIVE CONTROL: the same iteration boundary, nothing removed."""
        launch = self.predecessor_at_iteration_boundary()
        report = self.successor(launch, drift={})
        self.assertNotEqual((report["terminal_reason"] or {}).get("code"),
                            "PAIR_PREPARATION_RECORD_CORRUPT",
                            "an intact document is not corrupt")
        self.assertEqual(report["pair_creates"], 2,
                         "iteration 2 prepared its own pair")


class F001SectionSetTests(unittest.TestCase):
    """The reader's own contract: a PRESENT document's section set is EXACT."""

    def setUp(self) -> None:
        import tempfile
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.store = pause_store.pair_preparation_for(RUN_ID, artifact_base=self.root)
        self.path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.root)

    def seed(self) -> None:
        self.store.record(PHASE, 1, "worker", stage="CREATE_INTENDED",
                          terminal_title="t", terminal_worktree="w",
                          requested_model="glm-5.2")

    def test_the_writer_emits_every_section_on_every_write(self) -> None:
        self.seed()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(document),
                         sorted(("schema_version",) + self.store._SECTIONS))

    def test_each_removed_section_is_refused_by_name(self) -> None:
        for section in self.store._SECTIONS:
            with self.subTest(section=section):
                self.seed()
                strip_section(self.path, section)
                for read in (self.store.has_any,
                             lambda: self.store.entry(PHASE, 1, "worker"),
                             lambda: self.store.session_use("deadbeef"),
                             lambda: self.store.role_history(PHASE, "worker")):
                    with self.assertRaises(pause_store.PairPreparationCorrupt) as caught:
                        read()
                    self.assertIn("PAIR_PREPARATION_RECORD_CORRUPT",
                                  str(caught.exception))
                    self.assertIn(section, str(caught.exception))
                self.path.unlink()

    def test_an_absent_file_is_still_the_empty_first_write_path(self) -> None:
        """The genuine no-FILE path is SEPARATE and unchanged: it reads as three empty
        sections, and `has_any()` is False.  Whether that absence is a first write or a
        LOST document is the launch record's B3 marker's question, not this reader's."""
        self.assertFalse(self.path.exists())
        self.assertFalse(self.store.has_any())
        self.assertIsNone(self.store.entry(PHASE, 1, "worker"))
        self.assertEqual(self.store.sessions(), {})
        self.assertEqual(self.store.role_histories(), {})

    def test_an_empty_but_complete_document_is_accepted(self) -> None:
        """A document whose sections are all PRESENT and all EMPTY is valid -- that is
        what a refused first pass leaves behind."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"schema_version": pause_store.PAIR_PREPARATION_SCHEMA_VERSION,
             "pairs": {}, "sessions": {}, "role_history": {}}), encoding="utf-8")
        self.assertFalse(self.store.has_any())
        self.assertEqual(self.store.sessions(), {})

    def test_an_unknown_extra_section_is_still_refused(self) -> None:
        self.seed()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        document["invented"] = {}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.store.has_any()


# ======================================================================================
# static locks: the vocabulary and the record contract
# ======================================================================================
class ContractLockTests(unittest.TestCase):
    """The three new names are members of the adapter's own CLOSED refusal set, and the
    preparation document's own closed schemas carry the new cells."""

    def test_every_new_refusal_code_is_in_the_closed_set(self) -> None:
        for code in ("PAIR_PREPARATION_RECORD_LOST",
                     "PAIR_PREPARATION_SESSION_USE_UNKNOWN",
                     "PAIR_PREPARATION_MODEL_DRIFT"):
            self.assertIn(code, PAIR_PREPARATION_REFUSAL_CODES)

    def test_the_session_use_stages_are_their_own_closed_set(self) -> None:
        self.assertEqual(pause_store.PAIR_SESSION_USE_STAGES,
                         ("DELIVERY_INTENDED", "DELIVERED"))
        self.assertFalse(set(pause_store.PAIR_SESSION_USE_STAGES)
                         & set(pause_store.PAIR_PREPARATION_STAGES))
        self.assertFalse(set(pause_store.PAIR_SESSION_USE_STAGES)
                         & set(pause_store.JOURNAL_STAGES))

    def test_the_schema_versions_moved_so_an_older_document_cannot_be_read(self) -> None:
        """An older document is REFUSED, never read as "nothing was prepared"."""
        self.assertEqual(pause_store.PAIR_PREPARATION_SCHEMA_VERSION,
                         "os14.pair_preparation.v2")
        self.assertEqual(pause_store.PAIR_BINDING_SCHEMA_VERSION,
                         "os14.pair_launch_binding.v2")

    def test_the_launch_identity_tuple_gained_no_cell(self) -> None:
        """The preparation marker is PROVENANCE, not launch identity: adding it to the
        identity would make every live run's binding mismatch itself."""
        self.assertNotIn("preparation_started_at",
                         pause_store.PAIR_LAUNCH_IDENTITY_KEYS)
        self.assertIn("preparation_started_at", pause_store.PAIR_BINDING_KEYS)

    def test_a_new_create_attempt_preserves_the_model_observation(self) -> None:
        """N3: the attempt-clearing set clears this attempt's own cells and NEVER the
        model history."""
        cleared = pause_store._PAIR_ENTRY_ATTEMPT_CLEARED
        self.assertNotIn("resolved_model_observed", cleared)
        self.assertNotIn("observed_at_run", cleared)
        self.assertIn("terminal_digest", cleared)
        self.assertIn("supersedes_digest", cleared)


class PairStoreNewSectionTests(unittest.TestCase):
    """Unit tests OF the two new sections of the preparation document."""

    def setUp(self) -> None:
        import tempfile
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.store = pause_store.pair_preparation_for(RUN_ID, artifact_base=self.root)
        self.binding = pause_store.pair_binding_for(RUN_ID, artifact_base=self.root)

    def test_an_unused_session_reads_as_none_and_a_used_one_reads_back(self) -> None:
        self.assertIsNone(self.store.session_use("deadbeef"))
        self.store.record_session_use("deadbeef", stage="DELIVERY_INTENDED", phase=PHASE,
                                      gate_iteration="1", role="worker",
                                      intent_id="intent_1")
        row = self.store.session_use("deadbeef")
        self.assertEqual(row["stage"], "DELIVERY_INTENDED")
        self.assertEqual(row["dispatch_id"], "")

    def test_a_use_row_is_append_only_in_its_stage(self) -> None:
        self.store.record_session_use("d", stage="DELIVERY_INTENDED", phase=PHASE,
                                      gate_iteration="1", role="worker",
                                      intent_id="i1")
        self.store.record_session_use("d", stage="DELIVERED", phase=PHASE,
                                      gate_iteration="1", role="worker",
                                      intent_id="i1", dispatch_id="ctx_1")
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.store.record_session_use("d", stage="DELIVERY_INTENDED", phase=PHASE,
                                         gate_iteration="1", role="worker",
                                         intent_id="i1", delivery_attempt="1")
        self.assertEqual(self.store.session_use("d")["stage"], "DELIVERED")

    def test_a_second_delivery_of_one_session_is_a_new_delivery_attempt(self) -> None:
        self.store.record_session_use("d", stage="DELIVERY_INTENDED", phase=PHASE,
                                      gate_iteration="1", role="worker", intent_id="i1")
        self.store.record_session_use("d", stage="DELIVERED", phase=PHASE,
                                      gate_iteration="1", role="worker", intent_id="i1",
                                      dispatch_id="ctx_1")
        self.store.record_session_use("d", stage="DELIVERY_INTENDED", phase=PHASE,
                                      gate_iteration="1", role="worker", intent_id="i2",
                                      delivery_attempt="2")
        row = self.store.session_use("d")
        self.assertEqual(row["delivery_attempt"], "2")
        self.assertEqual(row["dispatch_id"], "",
                         "a new delivery attempt does not inherit the last dispatch id")
        self.assertEqual(row["previous_dispatch_ids"], "ctx_1",
                         "past use is APPENDED, never overwritten")

    def test_history_is_write_once_per_role_and_refuses_a_change(self) -> None:
        self.store.record_role_history(PHASE, "worker", "glm-5.2")
        self.assertEqual(self.store.role_history(PHASE, "worker")["resolved_model"],
                         "glm-5.2")
        self.store.record_role_history(PHASE, "worker", "glm-5.2")   # idempotent
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.store.record_role_history(PHASE, "worker", "glm-5.9-drifted")
        self.assertEqual(self.store.role_history(PHASE, "worker")["resolved_model"],
                         "glm-5.2")

    def test_history_is_scoped_to_the_phase_and_the_role(self) -> None:
        self.store.record_role_history(PHASE, "worker", "glm-5.2")
        self.assertIsNone(self.store.role_history(PHASE, "reviewer"))
        self.assertIsNone(self.store.role_history("other", "worker"))

    def test_the_preparation_marker_is_set_once_and_never_creates_a_binding(self) -> None:
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.binding.mark_preparation_started()
        self.assertFalse(self.binding.path.is_file(),
                         "a marker never manufactures the authority it is checked against")
        self.binding.record_binding(runtime="orca", model_aware="false")
        self.assertEqual(self.binding.preparation_started(), "")
        first = self.binding.mark_preparation_started()
        self.assertTrue(first)
        self.assertEqual(self.binding.mark_preparation_started(), first,
                         "the marker is monotonic: the FIRST statement stands")

    def test_an_older_preparation_document_is_refused_not_read_as_empty(self) -> None:
        path = pause_store.pair_preparation_path(RUN_ID, artifact_base=self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "os14.pair_preparation.v1",
                                    "pairs": {}}), encoding="utf-8")
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            self.store.has_any()


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
