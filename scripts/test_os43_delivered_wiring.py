"""OS-43 U-12: the DELIVERED watchdog wiring, driven to a SUCCESSFUL recovery.

Every other OS-43 suite proves a property of a part.  This one drives the composition the
CLI actually ships -- ``run_watchdog_cli`` -> ``_watchdog_wiring`` -> the supervisor core
-> the engine -> a real graph over a real checkpoint store -- and it exists because two
wiring defects survived a green 2,912-test suite:

* the action half always built ``FakeAdapter``, so an automatically detected stalled ORCA
  run re-entered a fake runtime and could dispatch no real work (F-001); and
* the default all-runs mode passed a graph factory that returned ``None``, so every
  actionable run it discovered died inside ``graph.invoke`` with an ``AttributeError``
  that the sweep reported as a run it had ACTED on (F-002).

Both were invisible because every test used the fake adapter and the only CLI behaviour
test supplied ``--run-id`` and forced an observation refusal.  So the tests here are
deliberately the two the review asked for: a SUCCESSFUL recovery through the delivered
Orca composition, and an end-to-end CLI sweep over TWO discovered actionable runs that
proves BOTH advance.

**Determinism.**  Nothing here sleeps or reads the wall clock for a verdict.  The Orca
listing authority is an injected runner, the runtime harness is an injected factory, and
the Coordinator liveness lease is published with ``lease_seconds=0.0`` so it is already
expired at the instant it is written.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.deterministic_workflow import (coordinator_liveness, launcher, pause_store,
                                            ports, recovery_runtime, recovery_store,
                                            watchdog_supervisor)
from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
from scripts.deterministic_workflow.runtime_state import (FileRuntimeStateStore,
                                                          ManualLeaseClock)
from scripts.deterministic_workflow.state import initial_state
from scripts.test_deterministic_workflow_pause_fixture import REQUIRES_LANGGRAPH
from scripts.test_orca_runtime_contract import (COMPLETED_AT, RecordingExec,
                                                _flag_value)
from scripts.test_os43_fixture import (FakeDiscoveryPort, FakeLivenessPort,
                                       FakeObservationPort, RecordingAudit)

#: One phase's worth of settlements: the worker round and the two review rounds.
RESULTS: tuple[dict[str, object], ...] = (
    {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"},
    {"result": "PASS", "review_verdict": "PASS", "findings": []},
    {"result": "PASS", "review_verdict": "PASS", "findings": []},
)

#: The shape `orca ... --json` returns for a run with no Task and no worker: no active
#: dispatch, no runnable action, no open decision gate.  A stalled run, in other words.
QUIET_ORCA = {"ok": True, "result": {"tasks": [], "workers": [], "gates": []}}

ACTIVE_CHECKPOINT = {"present": True, "run_status": "ACTIVE",
                     "next_node": "PREPARE_WORKER", "thread_id": "t",
                     "checkpoint_ns": "", "head_checkpoint_id": "cp_1",
                     "status_authority": "workflow_checkpoint"}


def quiet_runner(args):
    """The injected Orca listing authority.  Answers, rather than raising."""
    return 0, json.dumps(QUIET_ORCA)


class OfflineOrcaHarness:
    """A SUBSTITUTE for ``OrcaRuntimeHarness`` -- not the delivered one.

    Read this label before reading anything below it.  This class re-implements
    ``resume_run``, ``create_task``, ``run_existing_task``, ``task_status`` and ``call``
    itself.  Tests that use it therefore drive the REAL ``OrcaAdapter`` and the REAL
    graph, and they are evidence about the ADAPTER only: the delivered
    ``OrcaRuntimeHarness`` adoption path -- ``resume_run``, delivery-ledger
    restoration/reconciliation, terminal registration, liveness binding, the B1 guard,
    the decision ledger and the real command boundary -- is REPLACED here and is
    therefore not covered by any test that uses it.  Same shape as the one
    ``LangGraphAdapterParityTests`` drives the real ``OrcaAdapter`` over
    (``test_deterministic_workflow_adapters.py:84-104``), plus the ``resume_run``
    adoption entry point ``build_orca_adapter_for_run`` calls.

    ``RealHarnessRecoveryTests`` at the bottom of this file is the test that does NOT
    substitute the harness; it is the real-harness evidence, and this class is not.
    """

    def __init__(self, artifact_dir, quality_profile_root=None) -> None:
        self.artifact_dir = artifact_dir
        self.quality_profile_root = quality_profile_root
        self.results = [dict(result) for result in RESULTS]
        self.calls: list[tuple[str, str]] = []
        self.adopted: list[tuple[str, str]] = []
        self.run_id = ""

    def resume_run(self, run_id: str, *, run_owner: str,
                   requested_phases: tuple[str, ...] = ()) -> str:
        self.adopted.append((run_id, run_owner))
        self.run_id = run_id
        # Tracks the real signature: `build_orca_adapter_for_run` restores the adopted
        # run's declared workflow here (see `launcher.declared_phases_for_run`), and a
        # double whose signature drifts from the delivered one hides exactly that.
        self.requested_phases = tuple(requested_phases)
        return run_id

    def create_task(self, spec, *, deps=()):
        self.calls.append(("create_task", ""))
        return f"task_{len(self.calls)}"

    def run_existing_task(self, role, iteration, mode, task_id, *, phase=None, spec=None,
                          round_kind="phase_gate", **kwargs):
        from scripts.deterministic_workflow.fake_adapter import stipulated_gate_envelope
        self.calls.append(("run_existing_task", str(role)))
        result = dict(self.results.pop(0))
        result.setdefault("gate", stipulated_gate_envelope(json.loads(spec)))
        return (SimpleNamespace(body=json.dumps(result),
                                dispatch_id=f"dispatch_{len(self.calls)}"),
                "term_offline")

    def task_status(self, task_id: str) -> str:
        return "completed"

    def call(self, *args, **kwargs):
        return {"ok": True}


class DeliveredWiringFixture(unittest.TestCase):
    """A runs root holding genuinely stalled runs, and the CLI driven over it."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runs = self.base / "artifacts" / "runs"
        self.runs.mkdir(parents=True)
        self.harnesses: list[OfflineOrcaHarness] = []
        # The runtime-state ledger defaults to a path under the SYSTEM temp directory
        # keyed only by run and thread id (``launcher.default_runtime_state_path``), so
        # two runs called ``run_a`` in two different tests would share one ledger and a
        # replayed receipt from a previous test would suppress this one's dispatch.  The
        # documented override makes the fixture hermetic.
        environment = patch.dict(
            os.environ,
            {launcher.RUNTIME_STATE_DIR_ENV: str(self.base / "runtime_state")})
        environment.start()
        self.addCleanup(environment.stop)

    # -- fixtures ---------------------------------------------------------------------
    def stall(self, run_id: str, *, liveness: str = "EXPIRED") -> str:
        """A run interrupted mid-flight, exactly as a crashed Coordinator leaves one.

        The checkpoint is written by REALLY running the graph and stopping it before
        ``EXECUTE_INTENT``, not by hand: a hand-built checkpoint carries no pending task,
        so LangGraph resumes it into a no-op and a "recovery" over it proves nothing.
        Returns the committed head, so a test can assert the run actually moved.
        """
        # Imported HERE, not at module scope: ``checkpoint_store`` and ``graph`` are the
        # LangGraph-bound half and refuse to import without the runtime, and the tests in
        # this module that need neither must still collect in the dependency-absent lane.
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        root = self.runs / run_id
        root.mkdir(parents=True, exist_ok=True)
        saver = FileCheckpointSaver(root / ".workflow_checkpoints.json")
        ledger = FileRuntimeStateStore(launcher.default_runtime_state_path(run_id, "t"))
        journal = pause_store.journal_for(run_id, artifact_base=self.base)
        graph = build_graph(FakeAdapter([dict(item) for item in RESULTS],
                                        runtime_state=ledger, run_id=run_id,
                                        settlement_journal=journal),
                            checkpointer=saver, runtime_state=ledger, journal=journal,
                            interrupt_before=["EXECUTE_INTENT"])
        state = initial_state(run_id=run_id, thread_id="t", phases=("ANALYSIS",),
                              capabilities=BASE_CAPABILITIES)
        graph.invoke(dict(state),
                     {"configurable": {"thread_id": "t", "checkpoint_ns": ""}})
        # lease_seconds=0.0: expired at the instant it is written, so the gate's "the
        # Coordinator's heartbeat expired" is a fact this test states rather than waits for.
        store = coordinator_liveness.store_for(
            run_id, artifact_base=self.base,
            lease_seconds=0.0 if liveness == "EXPIRED" else 600.0)
        store.claim(run_id)
        self.assertEqual(coordinator_liveness.liveness_status(run_id,
                                                              artifact_base=self.base),
                         liveness)
        return self.head(run_id)

    def head(self, run_id: str) -> str:
        from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        return FileCheckpointSaver(
            self.runs / run_id / ".workflow_checkpoints.json").head(
                "t", checkpoint_ns="") or ""

    def results_file(self) -> str:
        path = self.base / "results.json"
        path.write_text(json.dumps([dict(item) for item in RESULTS]), encoding="utf-8")
        return str(path)

    def harness_factory(self, artifact_base, quality_profile_root=None):
        harness = OfflineOrcaHarness(artifact_base, quality_profile_root)
        self.harnesses.append(harness)
        return harness

    # -- the CLI under test -------------------------------------------------------------
    def watchdog(self, *argv: str, orca: bool = False):
        """``run_workflow.py <argv>`` with only the two process boundaries replaced."""
        buffer, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = launcher.run_watchdog_cli(
                list(argv), runner=quiet_runner,
                harness_factory=self.harness_factory if orca else None)
        return code, buffer.getvalue(), errors.getvalue()

    def summary(self, out: str) -> dict:
        return json.loads(out)

    def row(self, summary: dict, run_id: str) -> dict:
        return next(row for row in summary["runs"] if row["run_id"] == run_id)


@REQUIRES_LANGGRAPH
class UnfilteredSweepRecoveryTests(DeliveredWiringFixture):
    """F-002.  The documented default -- no ``--run-id`` -- over TWO actionable runs."""

    def test_the_default_all_runs_sweep_ADVANCES_every_actionable_run(self):
        before = {run_id: self.stall(run_id) for run_id in ("run_a", "run_b")}
        code, out, err = self.watchdog("watchdog", "once",
                                       "--artifact-base", str(self.base),
                                       "--results", self.results_file(), "--json")
        summary = self.summary(out)
        self.assertEqual((summary["runs_observed"], summary["runs_acted"]), (2, 2),
                         f"both discovered runs must be acted on; stderr={err}")
        self.assertEqual(summary["escalations"], [])
        self.assertEqual(code, 0)
        for run_id in ("run_a", "run_b"):
            row = self.row(summary, run_id)
            self.assertEqual(row["state"], "STALLED_RECOVERABLE", run_id)
            self.assertEqual(row["outcome_status"], recovery_runtime.RECOVERED, run_id)
            self.assertEqual(row["outcome_code"], "RECOVERY_ADVANCED", run_id)
            self.assertTrue(row["acted"], run_id)
            self.assertNotEqual(self.head(run_id), before[run_id],
                                f"{run_id}: the head did not move, so nothing was "
                                "recovered however the sweep reported it")
        self.assertNotEqual(self.head("run_a"), self.head("run_b"),
                            "each run resumed its OWN thread; one graph shared across a "
                            "sweep is the defect, not the fix")

    def test_the_continuous_mode_drives_the_same_wiring_over_both_runs(self):
        """``watchdog run`` is the OTHER mode the review found unable to act."""
        before = {run_id: self.stall(run_id) for run_id in ("run_a", "run_b")}
        code, out, _err = self.watchdog("watchdog", "run",
                                        "--artifact-base", str(self.base),
                                        "--results", self.results_file(),
                                        "--interval-seconds", "0", "--max-sweeps", "1",
                                        "--json")
        summary = self.summary(out)
        self.assertEqual((summary["runs_observed"], summary["runs_acted"]), (2, 2))
        self.assertEqual(code, 0)
        for run_id in ("run_a", "run_b"):
            self.assertNotEqual(self.head(run_id), before[run_id], run_id)

    def test_a_FILTERED_sweep_advances_only_the_run_it_was_given(self):
        """``--run-id`` still narrows the sweep, and still reaches a real recovery."""
        self.stall("run_a")
        self.stall("run_b")
        before = {run_id: self.head(run_id) for run_id in ("run_a", "run_b")}
        code, out, _err = self.watchdog("watchdog", "once",
                                        "--artifact-base", str(self.base),
                                        "--results", self.results_file(),
                                        "--run-id", "run_a", "--json")
        summary = self.summary(out)
        self.assertEqual((summary["runs_observed"], summary["runs_acted"]), (1, 1))
        self.assertNotEqual(self.head("run_a"), before["run_a"])
        self.assertEqual(self.head("run_b"), before["run_b"],
                         "a filtered sweep touches nothing else")
        self.assertEqual(code, 0)


@REQUIRES_LANGGRAPH
class RealOrcaCompositionTests(DeliveredWiringFixture):
    """F-001, ADAPTER half only: the wiring acting through the REAL ``OrcaAdapter``.

    The harness under these tests is ``OfflineOrcaHarness``, a substitute -- so what is
    proven here is that the action half builds and drives the real Orca adapter rather
    than ``FakeAdapter``, and nothing about ``OrcaRuntimeHarness`` itself.  The
    delivered harness path is proven in ``RealHarnessRecoveryTests`` below.
    """

    def test_the_delivered_wiring_RECOVERS_a_run_through_the_real_orca_adapter(self):
        before = self.stall("run_real")
        code, out, err = self.watchdog("watchdog", "once",
                                       "--artifact-base", str(self.base),
                                       "--adapter", "orca",
                                       "--run-owner", "term_owner", "--json",
                                       orca=True)
        summary = self.summary(out)
        row = self.row(summary, "run_real")
        self.assertEqual(row["outcome_status"], recovery_runtime.RECOVERED,
                         f"stderr={err}")
        self.assertEqual(row["outcome_code"], "RECOVERY_ADVANCED")
        self.assertEqual((summary["runs_observed"], summary["runs_acted"]), (1, 1))
        self.assertEqual(code, 0)
        self.assertNotEqual(self.head("run_real"), before, "the run did not advance")
        # The action half re-entered the ORCA composition, not the fake adapter. The
        # harness it adopted through is a substitute, so this asserts that adoption was
        # REQUESTED -- not that the delivered `resume_run` performed it.
        self.assertEqual([harness.adopted for harness in self.harnesses],
                         [[("run_real", "term_owner")]],
                         "a recovery ADOPTS the stalled run through resume_run")
        dispatched = [role for name, role in self.harnesses[0].calls
                      if name == "run_existing_task"]
        self.assertEqual(dispatched, ["worker", "reviewer", "final_reviewer"],
                         "the recovered run dispatched through the real OrcaAdapter")

    def test_observation_never_ADOPTS_the_run_it_is_only_observing(self):
        """Adoption publishes a Coordinator liveness lease, so it may not happen early.

        ``resume_run`` calls ``_begin_turn_boundary_liveness``
        (``orca_runtime_harness.py:3510``).  A capability probe that adopted the run
        would therefore make it LIVE to the very gate about to decide whether its
        Coordinator is gone -- the Orca composition would decline every run forever.
        """
        self.stall("run_live", liveness="LIVE")
        code, out, _err = self.watchdog("watchdog", "once",
                                        "--artifact-base", str(self.base),
                                        "--adapter", "orca",
                                        "--run-owner", "term_owner", "--json",
                                        orca=True)
        summary = self.summary(out)
        row = self.row(summary, "run_live")
        self.assertEqual(row["gate_action"], "DECLINE")
        self.assertEqual(row["gate_reason"], "liveness_live")
        self.assertEqual(summary["runs_acted"], 0)
        self.assertEqual(self.harnesses, [],
                         "observing a run must adopt nothing; only a recovery adopts")
        self.assertEqual(summary["escalations"], [],
                         "a live Coordinator is a reason to stand aside, not to escalate")
        self.assertEqual(code, 0)


class CompositionSelectionTests(DeliveredWiringFixture):
    """CON-5 in the DELIVERED composition: one core, two runtimes, neither hardwired."""

    def test_both_compositions_are_selectable_on_every_acting_verb(self):
        parser = launcher.build_watchdog_parser()
        for argv in (["watchdog", "once"], ["watchdog", "run"], ["recover", "--run-id", "r"]):
            with self.subTest(argv=argv):
                self.assertEqual(parser.parse_args(argv).adapter, launcher.FAKE_ADAPTER,
                                 "the standalone composition stays the default, so no "
                                 "existing invocation changes meaning")
                orca = parser.parse_args([*argv, "--adapter", "orca",
                                          "--run-owner", "term_owner"])
                self.assertEqual(orca.adapter, launcher.ORCA_ADAPTER)
                self.assertEqual(orca.run_owner, "term_owner")
        # OS-37 C-DESIGN-1 widens the shared tuple to three.  The property this test
        # asserts is unchanged and is asserted above: the DEFAULT stays `fake`, so no
        # existing invocation changes meaning.  The set stays CLOSED, which is what pinning
        # it is for -- a fourth member must be a visible edit here.
        self.assertEqual(set(launcher.ADAPTERS), {"fake", "orca", "standalone"})

    def test_the_status_verb_offers_no_adapter_because_it_takes_no_action(self):
        args = launcher.build_watchdog_parser().parse_args(["watchdog", "status"])
        self.assertFalse(hasattr(args, "adapter"))

    def test_the_orca_composition_refuses_without_a_run_owner_and_sweeps_NOTHING(self):
        code, out, err = self.watchdog("watchdog", "once",
                                       "--artifact-base", str(self.base),
                                       "--adapter", "orca", "--json", orca=True)
        self.assertEqual(code, launcher.USAGE_EXIT_CODE)
        self.assertIn(launcher.ORCA_ADAPTER_REQUIRES_STATE, err)
        self.assertEqual(out, "", "an incomplete composition reports no sweep at all")
        self.assertEqual(self.harnesses, [])

    def test_the_orca_composition_refuses_the_fake_adapters_scripted_results(self):
        code, _out, err = self.watchdog("watchdog", "once",
                                        "--artifact-base", str(self.base),
                                        "--adapter", "orca", "--run-owner", "term_owner",
                                        "--results", self.results_file(), "--json",
                                        orca=True)
        self.assertEqual(code, launcher.USAGE_EXIT_CODE)
        self.assertIn("--results", err)


class GraphFactoryFailClosedTests(DeliveredWiringFixture):
    """F-002's other half: a factory that cannot build is NAMED, never an AttributeError."""

    def invocation(self, **kwargs):
        return recovery_runtime.EngineRecoveryInvocation(artifact_base=self.base,
                                                         **kwargs)

    def test_the_port_refuses_construction_without_exactly_one_factory(self):
        """The ``lambda saver: None`` fallback is not merely deleted; it is unwritable."""
        with self.assertRaises(ValueError):
            self.invocation()
        with self.assertRaises(ValueError):
            self.invocation(graph_factory=lambda saver: object(),
                            graph_factory_for=lambda run_id: (lambda saver: object()))

    def test_the_factory_is_resolved_PER_RUN_at_request_time(self):
        asked: list[str] = []

        def provider(run_id: str):
            asked.append(run_id)
            return lambda saver: f"graph for {run_id}"

        invocation = self.invocation(graph_factory_for=provider)
        first = invocation.build_request(
            run_id="run_a", recovery_kind=recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)
        second = invocation.build_request(
            run_id="run_b", recovery_kind=recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)
        self.assertEqual(asked, ["run_a", "run_b"])
        self.assertEqual(first.graph_factory(None), "graph for run_a")
        self.assertEqual(second.graph_factory(None), "graph for run_b")

    def test_a_provider_that_yields_no_factory_is_a_NAMED_refusal(self):
        for provider in (lambda run_id: None,
                         lambda run_id: (_ for _ in ()).throw(RuntimeError("no graph"))):
            with self.subTest(provider=provider):
                invocation = self.invocation(graph_factory_for=provider)
                with self.assertRaises(ports.RecoveryPreconditionUnavailable) as raised:
                    invocation.build_request(
                        run_id="run_a",
                        recovery_kind=recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)
                self.assertEqual(raised.exception.code,
                                 ports.RECOVERY_GRAPH_UNAVAILABLE)
                self.assertIn("run_a", raised.exception.detail)

    def test_that_refusal_is_NOT_a_member_of_the_engines_closed_outcome_vocabulary(self):
        """Nothing was claimed and nothing ran, so it is not an outcome of anything."""
        for codes in recovery_runtime.RECOVERY_OUTCOME_CODES.values():
            self.assertNotIn(ports.RECOVERY_GRAPH_UNAVAILABLE, codes)

    def sweep_over(self, recovery):
        return watchdog_supervisor.run_once(
            discovery=FakeDiscoveryPort("run_a"),
            observation=FakeObservationPort(checkpoint_state=ACTIVE_CHECKPOINT),
            liveness=FakeLivenessPort("EXPIRED"), recovery=recovery,
            audit=RecordingAudit(), clock=ManualLeaseClock(), max_concurrent_runs=1)

    def test_a_sweep_NEVER_counts_a_run_it_could_not_invoke_as_acted(self):
        """The accounting defect: ``runs_acted`` counted the GATE, not the invocation."""

        class Unbuildable:
            def identity(self, **kwargs):
                return "rid"

            def build_request(self, *, run_id, recovery_kind):
                raise ports.RecoveryPreconditionUnavailable(
                    ports.RECOVERY_GRAPH_UNAVAILABLE, f"{run_id}: no graph")

            def recover(self, request):        # pragma: no cover - never reached
                raise AssertionError("the request was never built")

        class Exploding(Unbuildable):
            def build_request(self, *, run_id, recovery_kind):
                return {"run_id": run_id}

            def recover(self, request):
                raise AttributeError("'NoneType' object has no attribute 'invoke'")

        for recovery, escalation in ((Unbuildable(), "escalation_unsupported_capability"),
                                     (Exploding(), "escalation_observation_undecidable")):
            with self.subTest(recovery=type(recovery).__name__):
                report = self.sweep_over(recovery)
                row = report.runs[0]
                self.assertEqual(row.gate_action, "ACT",
                                 "the gate DID decide to act; that is exactly why "
                                 "counting the gate hid the defect")
                self.assertFalse(row.acted)
                self.assertEqual(report.runs_acted, 0,
                                 "a run that was never invoked is not a run recovered")
                self.assertEqual(row.escalation, escalation)
                self.assertEqual(report.exit_code, 1)

    def test_a_run_the_engine_DID_answer_is_still_counted(self):
        from scripts.test_os43_fixture import ScriptedRecovery, outcome
        report = self.sweep_over(ScriptedRecovery(
            outcome(recovery_runtime.RECOVERED, "RECOVERY_ADVANCED")))
        self.assertTrue(report.runs[0].acted)
        self.assertEqual(report.runs_acted, 1)

    @REQUIRES_LANGGRAPH
    def test_the_engine_refuses_by_NAME_rather_than_dying_inside_invoke(self):
        """The second line of defence, over a REAL claim and a REAL checkpoint store."""
        self.stall("run_a")
        request = recovery_runtime.RecoveryRequest(
            run_id="run_a", artifact_base=str(self.base),
            graph_factory=lambda saver: None)
        with self.assertRaises(ports.RecoveryPreconditionUnavailable) as raised:
            recovery_runtime.recover_stalled_run(request)
        self.assertEqual(raised.exception.code, ports.RECOVERY_GRAPH_UNAVAILABLE)
        from scripts.deterministic_workflow import recovery_store
        record = recovery_store.store_for("run_a", artifact_base=self.base).read("run_a")
        attempts = (record or {}).get("attempts") or {}
        self.assertTrue(attempts, "the attempt was opened before the effect")
        for attempt in attempts.values():
            self.assertEqual(attempt["stage"], "CLAIMED",
                             "nothing was promoted, because nothing was performed")
            self.assertEqual(attempt["head_after"], "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# ======================================================================================
# F-001 (iteration 3).  The REAL ``OrcaRuntimeHarness``, not a stand-in for it.
#
# ``OfflineOrcaHarness`` above re-implements ``resume_run``/``create_task``/
# ``run_existing_task``/``task_status``/``call``, so the tests that use it exercise the
# real ``OrcaAdapter`` and NOT the delivered harness adoption path.  The review was right
# that this leaves the thing the wiring defect actually lived in uncovered.  Everything
# below drives the REAL ``OrcaRuntimeHarness`` with only ``_exec_orca`` -- the subprocess
# boundary -- replaced, which is the repository's own technique
# (``test_orca_runtime_contract.OfflineHarnessTestCase`` / ``RecordingExec``).
# ======================================================================================


class RealHarnessExec(RecordingExec):
    """``RecordingExec`` for a MULTI-round run: one identity per round, not one pinned.

    ``RecordingExec`` pins ``task_g``/``ctx_1``/``dlv_1``, which is right for the
    single-dispatch tests it was written for and wrong here: the real
    ``wait_for_done`` treats a delivery id it has already processed as a REPLAY and
    keeps waiting, so three rounds answered with one delivery id would hang rather
    than recover.  Each round therefore gets its own Task, Dispatch, delivery and
    message id -- which is also what makes the delivery-ledger assertions below mean
    something.

    Only ``__call__``'s per-verb ANSWERS are supplied here.  Command recording, JSON
    parsing, the ok/returncode check and ``harness._raw`` all stay on the real path
    inside ``RecordingExec.__call__``/``harness.call``.
    """

    def __init__(self, script: tuple[dict[str, object], ...] = RESULTS) -> None:
        super().__init__()
        self.script = [dict(item) for item in script]
        #: The intent JSON handed to ``task-create``, one per round, in dispatch order.
        self.specs: list[str] = []
        #: Bound by the factory, so the probe below can read the harness that owns this
        #: recorder at the exact moment it issues its FIRST command.
        self.harness: object | None = None
        #: ``harness._deliveries_restored_for`` as it stood when the FIRST Orca command
        #: of the run went out.  ``resume_run``'s contract is that the delivery-ledger
        #: recovery happens INSIDE it -- "before this method returns and therefore
        #: before any caller can reach ``check --wait``" -- and ``_check`` restores
        #: lazily too, so only a reading taken before any command can tell the two
        #: apart.  ``None`` until the first command.
        self.restored_when_first_command_issued: object | None = None

    @property
    def round(self) -> int:
        """The one-based round currently in flight (``task-create`` opens it)."""
        return len(self.specs)

    def body(self) -> str:
        """The AGENT-SHAPED settlement body for the round currently in flight.

        Markdown, not JSON, and deliberately so.  ``OfflineOrcaHarness`` returned a JSON
        body, which the real harness never sees a shape of: the delivered
        ``_judge_settlement`` requires ``decision_gate.declares_gate_result`` -- a
        ``DECISION_GATE_STATE`` field line -- and then parses the FENCED record and
        appends it to the run's decision ledger, which is what the NEXT round's B1 guard
        reads.  A JSON body reaches none of that.  The record itself is built by
        ``stipulated_gate_envelope`` from this round's OWN intent, so the real mechanics
        identity check runs against the real dispatch rather than a constant.
        """
        index = self.round
        intent = json.loads(self.specs[index - 1])
        from scripts.deterministic_workflow.fake_adapter import stipulated_gate_envelope
        settlement = dict(self.script[index - 1])
        lines = [f"{field.upper()}: {settlement[field]}"
                 for field in ("status", "result") if field in settlement]
        record = stipulated_gate_envelope(intent)["record"]
        lines.append("DECISION_GATE_STATE: CLEAR")
        lines.extend(["```decision-gate",
                      json.dumps(record, indent=2, sort_keys=True), "```"])
        return "\n".join(lines) + "\n"

    def delivery(self) -> dict:
        """The ``worker_done`` delivery for the round currently in flight."""
        index = self.round
        return {
            "deliveryId": f"dlv_{index}",
            "timedOut": False,
            "messages": [{
                "id": f"msg_{index}",
                "type": "worker_done",
                "payload": json.dumps({"taskId": f"task_{index}",
                                       "dispatchId": f"ctx_{index}",
                                       "outcome": "succeeded"}),
                "body": self.body(),
            }],
        }

    def __call__(self, args):
        args = tuple(args)
        if self.restored_when_first_command_issued is None and self.harness is not None:
            self.restored_when_first_command_issued = getattr(
                self.harness, "_deliveries_restored_for", None)
        verb = args[1] if len(args) > 1 else args[0]
        if verb == "task-create":
            self.specs.append(_flag_value(args, "--spec"))
        index = max(self.round, 1)
        task_id, dispatch_id = f"task_{index}", f"ctx_{index}"
        self.results = {
            **self.results,
            "task-create": {"task": {"id": task_id}},
            "worker-start": {"dispatchId": dispatch_id, "state": "ready"},
            "task-list": {"tasks": [{"id": task_id, "status": "completed",
                                     "spec": self.specs[index - 1]}]
                          if self.specs else []},
            "dispatch-show": {"dispatch": {"status": "completed",
                                           "completed_at": COMPLETED_AT}},
            "worker-show": {
                "dispatch": {"status": "completed", "completed_at": COMPLETED_AT},
                "worker": {"state": "settled"},
                "terminalResource": {"releaseState": "released"},
            },
            "check": self.delivery() if self.specs else {},
        }
        return super().__call__(args)


@REQUIRES_LANGGRAPH
class RealHarnessRecoveryTests(DeliveredWiringFixture):
    """F-001, iteration 3: a SUCCESSFUL recovery through the DELIVERED harness.

    What is real here: ``launcher.run_watchdog_cli`` -> ``_watchdog_wiring`` -> the
    supervisor core -> ``build_orca_adapter_for_run`` -> the real
    ``OrcaRuntimeHarness.resume_run`` (turn-boundary session binding, Coordinator
    liveness binding, terminal registration for the run owner, delivery-ledger
    restoration/reconciliation) -> the real ``OrcaAdapter`` -> the real graph over a
    real checkpoint store.

    What is replaced: ``OrcaRuntimeHarness._exec_orca`` -- the ``subprocess`` call --
    and ``run_watchdog_cli``'s Orca listing ``runner``.  Nothing else.
    """

    def setUp(self) -> None:
        super().setUp()
        self.recorders: list[RealHarnessExec] = []
        self.real_harnesses: list[object] = []

    # -- the delivered harness, with only its process boundary replaced -----------------
    def real_harness_factory(self, artifact_base, quality_profile_root=None):
        from scripts.orca_runtime_harness import PROJECT_ROOT, OrcaRuntimeHarness
        # ORCA_CLI_COMMAND is what `_resolve_orca()` reads; without it the REAL
        # constructor refuses on a machine with no `orca` on PATH, which would make this
        # test environment-dependent rather than offline.  Same stub, same reason, as
        # `OfflineHarnessTestCase.build`.
        with patch.dict(os.environ, {"ORCA_CLI_COMMAND": "/opt/orca-dev"}):
            harness = OrcaRuntimeHarness(
                Path(artifact_base),
                quality_profile_root=(Path(quality_profile_root)
                                      if quality_profile_root else PROJECT_ROOT))
        recorder = RealHarnessExec()
        recorder.harness = harness
        harness._exec_orca = recorder            # the ONLY boundary replaced
        # The liveness keeper `resume_run` starts is a real background thread; retire it
        # with the test rather than leak one per case.
        self.addCleanup(harness._end_turn_boundary_liveness)
        self.recorders.append(recorder)
        self.real_harnesses.append(harness)
        return harness

    def real_watchdog(self, *argv: str):
        buffer, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = launcher.run_watchdog_cli(
                list(argv), runner=quiet_runner,
                harness_factory=self.real_harness_factory)
        return code, buffer.getvalue(), errors.getvalue()

    def predecessor_ledger(self, run_id: str) -> None:
        """The decision ledger the CRASHED Coordinator opened, restored to disk.

        A real stalled run was started by ``start_run``, which opens this ledger; the
        ``stall()`` fixture drives the graph directly and so never does.  Without it the
        real harness's B1 guard refuses every dispatch with
        ``DECISION_GATE_INPUT_MISSING`` -- correctly, because a run whose ledger cannot
        be read is exactly the state that must fail closed.  This is fixture repair, not
        a relaxation: the guard itself runs for real, over a real ledger, below.
        """
        from scripts import run_logging
        run_logging.open_decision_ledger(
            run_id, base=self.base, phases=("analysis",), risk="high",
            ledger_schema_version=1)

    RUN = "run_realharness"

    def recover(self):
        """Stall a run, then drive the delivered CLI over it. Returns everything."""
        before = self.stall(self.RUN)
        self.predecessor_ledger(self.RUN)
        code, out, err = self.real_watchdog(
            "watchdog", "once", "--artifact-base", str(self.base),
            "--adapter", "orca", "--run-owner", "term_owner", "--json")
        return before, code, self.summary(out), err

    # -- the outcome ---------------------------------------------------------------------
    def test_the_delivered_wiring_RECOVERS_a_run_through_the_real_runtime_harness(self):
        before, code, summary, err = self.recover()
        row = self.row(summary, self.RUN)
        self.assertEqual(row["outcome_status"], recovery_runtime.RECOVERED,
                         f"detail={row['detail']} stderr={err}")
        self.assertEqual(row["outcome_code"], "RECOVERY_ADVANCED")
        self.assertEqual((summary["runs_observed"], summary["runs_acted"]), (1, 1))
        self.assertEqual(summary["escalations"], [])
        self.assertEqual(code, 0)
        self.assertNotEqual(self.head(self.RUN), before, "the run did not advance")

    # -- the harness is the DELIVERED one, not a stand-in for it -------------------------
    def test_the_harness_the_recovery_adopted_is_the_real_OrcaRuntimeHarness(self):
        """The guard on this whole class.  ``OfflineOrcaHarness`` would pass every
        behavioural assertion below by re-implementing the method it names; only this
        one refuses a substituted implementation."""
        from scripts import orca_runtime_harness
        self.recover()
        harness = self.real_harnesses[0]
        self.assertIsInstance(harness, orca_runtime_harness.OrcaRuntimeHarness)
        for name in ("resume_run", "create_task", "run_existing_task", "task_status",
                     "call"):
            self.assertIs(type(harness).__dict__.get(name),
                          orca_runtime_harness.OrcaRuntimeHarness.__dict__.get(name),
                          f"{name} must be the delivered implementation, not a double")
        # ...and the ONE thing that is not real is named, rather than left to inference.
        self.assertIs(harness._exec_orca, self.recorders[0])

    # -- what `resume_run` itself did ----------------------------------------------------
    def test_the_recovery_ADOPTS_the_stalled_run_through_the_real_resume_run(self):
        self.recover()
        harness = self.real_harnesses[0]
        self.assertEqual((harness.run_id, harness.run_owner), (self.RUN, "term_owner"))
        # Terminal registration: `resume_run` adopts the owner handle it was given.
        self.assertEqual(harness._terminals["term_owner"]["origin"], "adopted")
        self.assertEqual(harness._terminals["term_owner"]["role"], "run_owner_fixture")

    def test_the_recovery_RESTORES_the_delivery_ledger_before_it_waits(self):
        """OS-44's successor obligation, executed rather than re-implemented.

        ``_restore_delivery_ledger_once`` is the reconciliation a fresh process owes the
        Run it adopts, and ``_check`` refuses to arm a waiter until it has run.  A
        harness double answers ``check`` itself and reaches none of it."""
        self.recover()
        harness = self.real_harnesses[0]
        self.assertEqual(harness._deliveries_restored_for, self.RUN)
        # ...and it happened inside `resume_run`, not lazily at the first wait: the
        # reading below was taken before the run's FIRST Orca command went out.
        self.assertEqual(self.recorders[0].restored_when_first_command_issued, self.RUN,
                         "the delivery ledger must be reconciled by adoption, before "
                         "any command -- a successor that waits first can adopt a "
                         "redelivered settlement as its own result")
        self.assertEqual(sorted(harness._deliveries), ["dlv_1", "dlv_2", "dlv_3"],
                         "each round's delivery is its own ledger row")
        for delivery_id, row in harness._deliveries.items():
            self.assertEqual(row["delivery_state"], "acknowledged", delivery_id)
            self.assertEqual(row["replays"], 0, delivery_id)
            self.assertTrue(row["settled"], delivery_id)

    def test_the_recovery_BINDS_this_process_as_the_run_s_live_Coordinator(self):
        """Adoption publishes a liveness lease, and the run was EXPIRED before it.

        This is the same coupling ``test_observation_never_ADOPTS_the_run_it_is_only_
        observing`` protects from the other side: observation must not make a run live,
        and recovery must."""
        self.stall(self.RUN)
        self.assertEqual(coordinator_liveness.liveness_status(
            self.RUN, artifact_base=self.base), "EXPIRED")
        self.predecessor_ledger(self.RUN)
        self.real_watchdog("watchdog", "once", "--artifact-base", str(self.base),
                           "--adapter", "orca", "--run-owner", "term_owner", "--json")
        self.assertIsNotNone(self.real_harnesses[0]._liveness_keeper)
        self.assertEqual(coordinator_liveness.liveness_status(
            self.RUN, artifact_base=self.base), "LIVE",
            "the successor Coordinator must be publishing the run's lease")

    # -- the real command boundary, and the real decision gate ---------------------------
    def test_every_round_went_out_over_the_real_Orca_command_boundary(self):
        self.recover()
        recorder, harness = self.recorders[0], self.real_harnesses[0]
        self.assertEqual(
            recorder.verbs,
            ["task-create", "create", "wait", "worker-start", "check", "worker-show",
             "task-list", "worker-release", "check"] * 3,
            "the delivered dispatch/settle/release sequence, three times")
        self.assertEqual(recorder.unmodelled, [])
        # `harness.call` ran for real, so the harness's own command log agrees with the
        # recorder's -- the property RecordingExec was written to preserve.
        self.assertEqual([tuple(entry["command"]) for entry in harness._raw],
                         [tuple(command) for command in recorder.commands])
        self.assertEqual([json.loads(spec)["role"] for spec in recorder.specs],
                         ["WORKER", "PHASE_REVIEWER", "FINAL_REVIEWER"],
                         "the adopted run reached its FINAL gate, not just its first")

    def test_the_real_B1_guard_and_decision_ledger_ran_for_every_round(self):
        """``_judge_settlement`` -> ``append_decision_ledger_record`` -> the next round's
        ``_b1_guard``.  A double that returns a settlement dict skips all three."""
        from scripts import run_logging
        self.recover()
        records = run_logging.read_decision_ledger(self.RUN, base=self.base)
        self.assertEqual(
            [(record["sequence"], record["boundary"], record["source"],
              record["phase"]) for record in records],
            [(0, "B1", "coordinator:run_entry", "analysis"),
             (1, "B2", "worker", "analysis"),
             (2, "B3", "reviewer", "analysis"),
             (3, "B3", "reviewer", "final_review")])
        self.assertEqual(self.real_harnesses[0]._logging_errors, [])

    # -- the production defect this test found -------------------------------------------
    def test_an_adopted_run_INHERITS_the_phases_it_was_launched_with(self):
        """``resume_run`` has no launch specification, so without this the final gate
        refuses: "requested_phases is required for the final_review quality gate"."""
        self.stall(self.RUN)
        self.assertEqual(
            launcher.declared_phases_for_run(self.RUN, artifact_base=self.base),
            ("analysis",))
        self.predecessor_ledger(self.RUN)
        self.real_watchdog("watchdog", "once", "--artifact-base", str(self.base),
                           "--adapter", "orca", "--run-owner", "term_owner", "--json")
        self.assertEqual(self.real_harnesses[0].requested_phases, ("analysis",))

    def test_an_unreadable_head_declares_NO_phases_rather_than_guessing(self):
        """The fallback is the behaviour every caller had before the fix: refuse to
        invent a workflow, and let the engine's own read decide."""
        self.assertEqual(
            launcher.declared_phases_for_run("run_absent", artifact_base=self.base), ())
        root = self.runs / "run_corrupt"
        root.mkdir(parents=True)
        (root / ".workflow_checkpoints.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(
            launcher.declared_phases_for_run("run_corrupt", artifact_base=self.base), ())


# ======================================================================================
# TEST phase.  The gaps an independent read of the delivered verification found, all four
# of them the same shape as this run's three implementation defects: a property asserted
# on a substitute for the component that carries it, or a refusal asserted while the
# matching success path stayed unexercised.
#
#   G-1  AC-9's success path.  Every one-shot test asserted a REFUSAL (no checkpoint, no
#        paused run).  Nothing showed the one-shot verb actually RECOVERING a run with no
#        Watchdog in existence, which is the whole of AC-9.
#   G-2  AC-2 through the delivered composition.  The human-wait veto was proven at the
#        classifier over a hand-built fact vector and at the adapter over a hand-written
#        file; no test drove the delivered CLI over a run that really is waiting.
#   G-3  AC-3 through the delivered composition.  Same gap, for the live-dispatch veto:
#        the Orca listing authority was `quiet_runner` in EVERY delivered-wiring test, so
#        no test ever showed a busy run being left alone by the shipped wiring.
#   G-4  AC-6/AC-7 over the REAL ledger.  Every supervisor test used `RecordingAudit`,
#        whose `fold` returns rows the TEST wrote -- so the round trip from what the
#        supervisor PUBLISHES to what the fold RECONSTRUCTS was never executed, and a
#        drift between the two would have been invisible.
# ======================================================================================

BUSY_ORCA = {"ok": True, "result": {
    "tasks": [{"id": "task_live", "status": "dispatched", "dispatch_id": "ctx_live"}],
    "workers": [{"dispatchId": "ctx_live", "taskId": "task_live",
                 "dispatchStatus": "running", "workerState": "working"}],
    "gates": []}}


def busy_runner(args):
    """An Orca listing authority reporting a Worker that is genuinely still running."""
    return 0, json.dumps(BUSY_ORCA)


@REQUIRES_LANGGRAPH
class OneShotSuccessTests(DeliveredWiringFixture):
    """G-1 / AC-9: the one-shot verb RECOVERS, with no Watchdog process in existence."""

    def one_shot(self, run_id: str, *argv: str):
        buffer, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = launcher.run_cli(["recover", "--run-id", run_id,
                                     "--artifact-base", str(self.base),
                                     "--results", self.results_file(), "--json", *argv])
        return code, buffer.getvalue(), errors.getvalue()

    def test_the_one_shot_verb_ADVANCES_a_real_stalled_run_with_no_watchdog_running(self):
        before = self.stall("run_oneshot")
        code, out, err = self.one_shot("run_oneshot")
        summary = json.loads(out)
        self.assertEqual(summary["status"], recovery_runtime.RECOVERED,
                         f"detail={summary.get('detail')} stderr={err}")
        self.assertEqual(summary["code"], "RECOVERY_ADVANCED")
        self.assertTrue(summary["effect_performed"])
        self.assertEqual(code, 0)
        self.assertNotEqual(self.head("run_oneshot"), before,
                            "AC-9 is that the one-shot recovery WORKS, not merely that "
                            "it refuses politely")

    def test_it_writes_NOTHING_under_the_watchdog_ledger_even_when_it_succeeds(self):
        """AC-9's independence: no shared durable state, on the path that ACTS.

        ``test_os43_oneshot_cli.test_the_one_shot_paths_touch_no_watchdog_state`` asserts
        this over a run the verb REFUSED, where nothing could have been written anyway.
        """
        self.stall("run_oneshot")
        self.one_shot("run_oneshot")
        self.assertFalse((self.runs / "run_oneshot" / "watchdog_audit").exists())
        self.assertEqual(
            [path.name for path in (self.runs / "run_oneshot").iterdir()
             if path.name.startswith("watchdog")], [])

    def test_a_replayed_one_shot_over_the_recovered_run_performs_no_second_effect(self):
        """AC-5 on the one-shot path: the engine's identity, not the Watchdog's ledger."""
        self.stall("run_oneshot")
        self.one_shot("run_oneshot")
        after_first = self.head("run_oneshot")
        code, out, _err = self.one_shot("run_oneshot")
        summary = json.loads(out)
        self.assertIn(summary["status"], (recovery_runtime.NO_EFFECT,
                                          recovery_runtime.NOT_RECOVERABLE))
        self.assertFalse(summary["effect_performed"])
        self.assertEqual(self.head("run_oneshot"), after_first,
                         "a replayed one-shot moved the head")
        self.assertIn(code, (0, 1))


@REQUIRES_LANGGRAPH
class DeliveredCompositionVetoTests(DeliveredWiringFixture):
    """G-2 / G-3: AC-2 and AC-3 asserted through the wiring the CLI actually ships."""

    def sweep(self, *, runner=quiet_runner):
        buffer, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = launcher.run_watchdog_cli(
                ["watchdog", "once", "--artifact-base", str(self.base),
                 "--results", self.results_file(), "--json"], runner=runner)
        return code, json.loads(buffer.getvalue()), errors.getvalue()

    def arm_a_human_wait(self, run_id: str) -> None:
        """A REAL OS-31 pause record, written by the store the adapter reads."""
        from scripts.test_deterministic_workflow_pause import record as pause_record
        pause_store.store_for(run_id, artifact_base=self.base).create(
            pause_record(run_id=run_id, thread_id="t", checkpoint_id="chk_absent"))

    # -- AC-2 --------------------------------------------------------------------------
    def test_a_run_WAITING_ON_A_HUMAN_is_left_alone_by_the_delivered_sweep(self):
        before = self.stall("run_waiting")
        self.arm_a_human_wait("run_waiting")
        code, summary, err = self.sweep()
        row = self.row(summary, "run_waiting")
        self.assertEqual(row["state"], "WAITING_ON_HUMAN",
                         f"an EXPIRED Coordinator must not turn a human wait into "
                         f"recoverable work; stderr={err}")
        self.assertEqual(row["rule_index"], 7)
        self.assertEqual(row["gate_reason"], "not_actionable")
        self.assertFalse(row["acted"])
        self.assertEqual(summary["runs_acted"], 0)
        self.assertEqual(self.head("run_waiting"), before,
                         "the head moved: a WAITING_FOR_INPUT run was auto-advanced")
        self.assertEqual(code, 0, "standing aside is not an escalation")

    def test_the_coordinator_lease_really_IS_expired_for_that_waiting_run(self):
        """The premise of the test above, stated rather than assumed: AC-1's trigger is
        satisfied and AC-2 is what stops the run anyway."""
        self.stall("run_waiting")
        self.arm_a_human_wait("run_waiting")
        self.assertEqual(coordinator_liveness.liveness_status("run_waiting",
                                                              artifact_base=self.base),
                         "EXPIRED")

    # -- AC-3 --------------------------------------------------------------------------
    def test_a_run_with_a_LIVE_WORKER_is_left_alone_by_the_delivered_sweep(self):
        before = self.stall("run_busy")
        code, summary, err = self.sweep(runner=busy_runner)
        row = self.row(summary, "run_busy")
        self.assertEqual(row["state"], "ACTIVE_DISPATCH_WAIT",
                         f"a run with work in flight is not stalled; stderr={err}")
        self.assertEqual(row["rule_index"], 8)
        self.assertFalse(row["acted"])
        self.assertEqual(summary["runs_acted"], 0)
        self.assertEqual(self.head("run_busy"), before,
                         "the head moved: an actively running Worker was double-resumed")
        self.assertEqual(code, 0)

    def test_the_SAME_run_with_the_SAME_wiring_IS_recovered_once_the_worker_is_gone(self):
        """The paired positive for both vetoes: neither test above passes because the
        delivered composition is simply inert."""
        before = self.stall("run_busy")
        _code, busy, _err = self.sweep(runner=busy_runner)
        self.assertEqual(busy["runs_acted"], 0)
        _code, quiet, err = self.sweep(runner=quiet_runner)
        row = self.row(quiet, "run_busy")
        self.assertEqual(row["state"], "STALLED_RECOVERABLE", f"stderr={err}")
        self.assertEqual(row["outcome_status"], recovery_runtime.RECOVERED)
        self.assertNotEqual(self.head("run_busy"), before)


class RealLedgerRoundTripTests(unittest.TestCase):
    """G-4 / AC-6 / AC-7: what the supervisor PUBLISHES, folded back by the REAL fold.

    Every other supervisor test folds through ``RecordingAudit``, which returns rows the
    test itself wrote -- so the shapes on the two sides of the ledger were never required
    to agree.  Here one supervisor sweep writes through ``FileWatchdogAudit`` and the NEXT
    sweep reads its own predecessor's records back through
    ``watchdog_audit.replay_watchdog_ledger``.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.clock = ManualLeaseClock()

    def deps(self, recovery):
        from scripts.deterministic_workflow.watchdog_audit import FileWatchdogAudit
        from scripts.test_os43_fixture import (FakeDiscoveryPort, FakeLivenessPort,
                                               FakeObservationPort)
        return {"discovery": FakeDiscoveryPort("run_rt"),
                "observation": FakeObservationPort(checkpoint_state=ACTIVE_CHECKPOINT),
                "liveness": FakeLivenessPort("EXPIRED"), "recovery": recovery,
                "audit": FileWatchdogAudit(self.base), "clock": self.clock,
                "max_concurrent_runs": 1}

    def folded(self):
        from scripts.deterministic_workflow import watchdog_audit
        return watchdog_audit.replay_watchdog_ledger("run_rt", base=self.base)

    def test_a_TERMINAL_outcome_this_supervisor_wrote_stops_the_NEXT_sweep(self):
        from scripts.test_os43_fixture import ScriptedRecovery, outcome
        first = ScriptedRecovery(outcome(recovery_runtime.NOT_RECOVERABLE,
                                         "RECOVERY_NO_RUNNABLE_NODE"))
        report = watchdog_supervisor.run_once(**self.deps(first))
        self.assertEqual(report.runs[0].escalation, "escalation_not_recoverable")
        recovery_id = report.runs[0].recovery_id
        # The records the SUPERVISOR published, read back by the REAL fold.
        row = self.folded()[recovery_id]
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["last_outcome"], recovery_runtime.NOT_RECOVERABLE)
        self.assertTrue(row["terminal"], "the fold did not reconstruct the refusal the "
                                         "supervisor recorded")
        # ...and a successor process declines on that reconstructed state alone.
        second = ScriptedRecovery()            # any invocation raises
        again = watchdog_supervisor.run_once(**self.deps(second))
        self.assertEqual(again.runs[0].gate_reason, "identity_terminal")
        self.assertEqual(second.requests, [])
        self.assertFalse(again.runs[0].acted)

    def test_a_NO_EFFECT_this_supervisor_wrote_settles_the_identity_durably(self):
        """The one outcome whose ONLY ledger evidence is ``watchdog_resume_outcome``.

        ``NO_EFFECT`` consumes no budget, opens no backoff and raises no escalation, so
        neither ``watchdog_failure`` nor ``watchdog_escalated`` is published: if the
        resume-outcome record and the fold ever stop agreeing about a field name, this is
        the case that notices.
        """
        from scripts.test_os43_fixture import ScriptedRecovery, outcome
        first = ScriptedRecovery(outcome(recovery_runtime.NO_EFFECT,
                                         "RECOVERY_ALREADY_APPLIED"))
        report = watchdog_supervisor.run_once(**self.deps(first))
        recovery_id = report.runs[0].recovery_id
        self.assertEqual(report.runs[0].escalation, "")
        from scripts.deterministic_workflow import watchdog_audit
        events = [record["event"] for record in
                  watchdog_audit.read_watchdog_audit("run_rt", base=self.base)]
        self.assertNotIn("watchdog_failure", events)
        self.assertNotIn("watchdog_escalated", events)
        row = self.folded()[recovery_id]
        self.assertEqual(row["last_outcome"], recovery_runtime.NO_EFFECT)
        self.assertEqual(row["last_code"], "RECOVERY_ALREADY_APPLIED")
        self.assertTrue(row["terminal"])
        second = ScriptedRecovery()            # any invocation raises
        again = watchdog_supervisor.run_once(**self.deps(second))
        self.assertEqual(again.runs[0].gate_reason, "identity_terminal")
        self.assertEqual(second.requests, [])

    def test_a_RETRYABLE_failure_this_supervisor_wrote_carries_its_budget_forward(self):
        from scripts.test_os43_fixture import ScriptedRecovery, outcome
        first = ScriptedRecovery(outcome(recovery_runtime.REFUSED,
                                         "PAUSE_RECORD_CORRUPT"))
        report = watchdog_supervisor.run_once(**self.deps(first))
        recovery_id = report.runs[0].recovery_id
        row = self.folded()[recovery_id]
        self.assertEqual(row["attempts"], 1)
        self.assertGreater(row["backoff_until"], self.clock.time(),
                           "the backoff the supervisor computed did not reach the ledger")
        # The pending backoff is honoured from the durable record, not from memory.
        blocked = ScriptedRecovery()
        report = watchdog_supervisor.run_once(**self.deps(blocked))
        self.assertEqual(report.runs[0].gate_reason, "backoff_pending")
        self.assertEqual(blocked.requests, [])
        # ...and once it lapses the SAME budget continues rather than restarting.
        self.clock.advance(row["backoff_until"] - self.clock.time() + 1.0)
        second = ScriptedRecovery(outcome(recovery_runtime.RECOVERED, "RECOVERY_ADVANCED",
                                          effect_performed=True, head_before="cp_1",
                                          head_after="cp_2"))
        report = watchdog_supervisor.run_once(**self.deps(second))
        self.assertTrue(report.runs[0].acted)
        self.assertEqual(self.folded()[recovery_id]["attempts"], 2,
                         "a successor that restarted the budget would read 1 here")


@REQUIRES_LANGGRAPH
class SingleWinnerThroughTheDeliveredWiringTests(DeliveredWiringFixture):
    """G-5 / AC-4: the race resolved where the design says it is -- the ENGINE's claim.

    ``test_os43_supervisor_loop.ConcurrencyTests.test_two_concurrent_supervisors_yield_
    exactly_one_RECOVERED`` scripts BOTH outcomes: it asserts that a fake told to say
    ``RECOVERED`` and a fake told to say ``CONFLICT`` say those things, and it would pass
    unchanged with the engine's atomic claim deleted.  The two halves of the real
    guarantee are executed here instead: the delivered sweep stands aside from a run
    another claimant holds (R10, the doomed-claim optimisation), and the ENGINE refuses a
    genuinely RECOVERABLE run whose lease is held -- the case
    ``RecoveryApiRefusalTests.test_a_pause_record_AND_a_live_recovery_lease_is_CONFLICT_
    never_resolved`` could not reach, because its run has no checkpoint and could not have
    been recovered even unopposed.
    """

    RUN = "run_race"

    def sweep(self):
        buffer, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = launcher.run_watchdog_cli(
                ["watchdog", "once", "--artifact-base", str(self.base),
                 "--results", self.results_file(), "--json"], runner=quiet_runner)
        return code, json.loads(buffer.getvalue()), errors.getvalue()

    def rival(self, clock=None):
        from scripts.deterministic_workflow import recovery_store
        return recovery_store.store_for(self.RUN, artifact_base=self.base, clock=clock,
                                        owner_id="host:pid999")

    def engine_request(self, **overrides):
        """A ``RecoveryRequest`` whose graph really can advance this run."""
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        ledger = FileRuntimeStateStore(
            launcher.default_runtime_state_path(self.RUN, "t"))
        journal = pause_store.journal_for(self.RUN, artifact_base=self.base)

        def factory(saver):
            return build_graph(FakeAdapter([dict(item) for item in RESULTS],
                                           runtime_state=ledger, run_id=self.RUN,
                                           settlement_journal=journal),
                               checkpointer=saver, runtime_state=ledger, journal=journal)
        return recovery_runtime.RecoveryRequest(
            run_id=self.RUN, artifact_base=str(self.base), graph_factory=factory,
            **overrides)

    # -- the Watchdog half: stand aside, and do not pretend that is the guarantee -------
    def test_the_delivered_sweep_stands_aside_from_a_run_a_RIVAL_already_holds(self):
        before = self.stall(self.RUN)
        self.rival().claim(self.RUN)
        code, summary, err = self.sweep()
        row = self.row(summary, self.RUN)
        self.assertEqual((row["state"], row["rule_index"]),
                         ("OWNED_ELSEWHERE_OBSERVE", 10), f"stderr={err}")
        self.assertEqual(row["gate_reason"], "not_actionable")
        self.assertFalse(row["acted"])
        self.assertEqual(summary["runs_acted"], 0)
        self.assertEqual(self.head(self.RUN), before,
                         "the loser moved the head: two claimants both resumed the run")
        self.assertEqual(code, 0, "losing a race is not an escalation")

    def test_the_SAME_run_and_the_SAME_wiring_recover_it_once_the_rival_lets_go(self):
        """The paired positive: R10 is a refusal, not an inert composition."""
        before = self.stall(self.RUN)
        token = self.rival().claim(self.RUN)["lease_token"]
        _code, blocked, _err = self.sweep()
        self.assertEqual(self.row(blocked, self.RUN)["state"], "OWNED_ELSEWHERE_OBSERVE")
        self.rival().release(self.RUN, token)
        _code, summary, err = self.sweep()
        row = self.row(summary, self.RUN)
        self.assertEqual(row["outcome_status"], recovery_runtime.RECOVERED,
                         f"gate={row['gate_action']}/{row['gate_reason']} stderr={err}")
        self.assertNotEqual(self.head(self.RUN), before)

    # -- the ENGINE half: the guarantee itself, on a run that COULD have been recovered --
    def test_the_ENGINE_refuses_a_RECOVERABLE_run_whose_lease_another_claimant_holds(self):
        """Both halves on ONE run: refused while held, recovered once released.

        Driven on a ``ManualLeaseClock`` shared by the rival's store and the engine, with
        the observation window set to a single manual tick -- so the incumbent's lease never lapses on its
        own and the test neither sleeps nor waits out a real 65-second window.  Without
        the shared clock this case takes the LEGITIMATE takeover path instead: an
        incumbent that stops heartbeating is meant to lose its lease, and that is why a
        lease held by a live rival is the only construction that reaches CONFLICT.
        """
        clock = ManualLeaseClock()
        before = self.stall(self.RUN)
        rival = self.rival(clock=clock)
        token = rival.claim(self.RUN)["lease_token"]
        refused = recovery_runtime.recover_stalled_run(
            self.engine_request(observe_timeout_seconds=0.001), clock=clock)
        self.assertEqual(refused.status, recovery_runtime.CONFLICT)
        self.assertEqual(refused.code, "RECOVERY_CLAIM_HELD")
        self.assertFalse(refused.effect_performed)
        self.assertEqual(self.head(self.RUN), before,
                         "the refused claimant resumed the run anyway")
        # ...and the run really was recoverable: the SAME request succeeds unopposed.
        rival.release(self.RUN, token)
        recovered = recovery_runtime.recover_stalled_run(
            self.engine_request(observe_timeout_seconds=0.001), clock=clock)
        self.assertEqual(recovered.status, recovery_runtime.RECOVERED, recovered.detail)
        self.assertTrue(recovered.effect_performed)
        self.assertNotEqual(self.head(self.RUN), before)


# ---- AC-4, EXECUTED: two concurrent claimants on ONE real recovery lease ---------------

class ClaimRendezvousStore(recovery_store.FileRecoveryStateStore):
    """The REAL recovery store, with a rendezvous immediately BEFORE the atomic claim.

    Nothing about the claim is replaced or scripted: ``claim`` is the production method,
    the record is the production record, the critical section is the production
    ``flock``, and which claimant wins is decided by that section alone.  The only
    addition is a two-party :class:`threading.Barrier` the FIRST claim waits on, so both
    claimants are provably inside ``claim`` at the same instant instead of arriving
    whenever the scheduler happens to start their threads.

    A SECOND claim on the same store -- the single takeover a loser is entitled to
    attempt once its rival's lease has lapsed -- deliberately does not wait: there is no
    second party left to meet, and waiting for one would deadlock the very path under
    test.

    It supplies NO identity of its own.  Constructed without ``owner_id`` it is a
    production-default store, exactly the one ``recovery_store.store_for`` builds, and its
    ``claimant_id`` is minted by the delivered code path -- which is what lets the
    in-process subclass below race two of them without manufacturing the distinctness the
    exclusion is supposed to provide.
    """

    def __init__(self, *args: Any, barrier: threading.Barrier,
                 arrivals: list[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._barrier = barrier
        self._arrivals = arrivals
        self._met = False

    def claim(self, run_id: str, **kwargs: Any):
        if not self._met:
            self._met = True
            # The CLAIMANT, not the process: two attempts in one process share an
            # ``owner_id``, so recording that would report one arrival twice and the
            # concurrency assertion would stop meaning anything in the in-process case.
            self._arrivals.append(self.claimant_id)
            self._barrier.wait()
        return super().claim(run_id, **kwargs)


class HoldingGraph:
    """A real graph whose ``invoke`` first holds the winner's freshly claimed lease open.

    The winner blocks here -- lease claimed, attempt entry written, keeper heartbeating --
    until the OTHER claimant's whole attempt has settled.  That is what makes the loser's
    outcome deterministic without a sleep or a timing guess: the loser observes a lease
    that is genuinely still held by a live owner, so the only exit from ``observe`` is its
    own bounded window closing, and the refusal it reports is the one the atomic claim
    produced.  The wait is bounded, so a build in which BOTH claimants reach ``invoke``
    (the exclusion mechanism broken) still terminates -- and is then caught by the
    invocation count, which is the point.
    """

    def __init__(self, inner: Any, *, owner: str, released_by: threading.Event,
                 timeout: float, log: list[str]) -> None:
        self.inner = inner
        self.owner = owner
        self.released_by = released_by
        self.timeout = timeout
        self.log = log

    def invoke(self, value: Any, config: Any) -> Any:
        self.log.append(self.owner)
        self.released_by.wait(timeout=self.timeout)
        return self.inner.invoke(value, config)


@REQUIRES_LANGGRAPH
class ConcurrentClaimRaceTests(DeliveredWiringFixture):
    """AC-4 executed rather than nominal: ONE winner out of a genuine concurrent race.

    ``SingleWinnerThroughTheDeliveredWiringTests`` proves the two halves SEQUENTIALLY --
    a rival lease is installed, and then the other claimant is invoked.  That is real
    evidence about refusal, but it is not the concurrent single-winner property AC-4
    names, and neither is
    ``supervisor_loop.ConcurrencyTests.test_two_concurrent_supervisors_yield_exactly_one_
    RECOVERED``, which starts two threads over two ``ScriptedRecovery`` objects already
    told what to answer.

    Here two REAL ``recover_stalled_run`` invocations run in two threads over ONE real
    ``FileRecoveryStateStore`` identity -- one record path, one lock file, one run --
    and NOTHING tells either of them what to return.  Determinism comes from two
    rendezvous, not from timing:

    * a :class:`threading.Barrier` immediately before the atomic claim, so neither claim
      can return until both threads are inside it; and
    * the winner holding its claimed lease open inside ``graph.invoke`` until the loser
      has settled, so the loser cannot take the legitimate lapsed-lease takeover path by
      accident and its refusal is the claim's, every time.

    The four properties AC-4 actually asserts are checked one per test: exactly one
    ``RECOVERED``, exactly one refusal AND that it came from the claim, exactly one
    effect and one head transition, and exactly one durable lease owner with one attempt
    lineage.
    """

    RUN = "run_truerace"
    #: Bounds every wait in this suite.  In a green run nothing waits: the barrier is
    #: released as soon as the second thread arrives, and the loser settles in
    #: milliseconds.  It exists so a BROKEN exclusion mechanism fails the test instead of
    #: hanging the suite.
    RENDEZVOUS_TIMEOUT = 20.0
    #: The claimant identity each party is HANDED, or ``None`` for the composition case.
    #:
    #: A tuple states two SEPARATE-PROCESS identities -- the deployed topology, and one
    #: two threads cannot be on their own.  That case is real and stays covered.
    #:
    #: ``None`` hands neither party anything: both stores are built the production-default
    #: way, so both carry the SAME ``owner_id`` and whatever separates them is the
    #: product's.  See :class:`SameProcessWatchdogRaceTests`.
    OWNERS: tuple[str, str] | None = ("host:pid8001", "host:pid8002")
    #: Test-local NAMES for the two parties, used to say which one reached the graph.
    #: Never an identity: nothing under test ever sees them.
    LABELS = ("claimant-0", "claimant-1")

    def contender(self, index: int, *, path, barrier: threading.Barrier,
                  arrivals: list[str]) -> Any:
        """The store one contending attempt claims through.

        ``OWNERS is None`` supplies nothing at all, so the identity is minted by
        ``recovery_store.new_claimant_id`` through the ordinary constructor -- the same
        one ``recovery_store.store_for`` and ``launcher._execution_authority`` use.
        """
        owner = None if self.OWNERS is None else self.OWNERS[index]
        return ClaimRendezvousStore(path, owner_id=owner, barrier=barrier,
                                    arrivals=arrivals)

    def engine_request(self, index: int, *, released_by: threading.Event,
                       invocations: list[str], **overrides: Any):
        """A ``RecoveryRequest`` whose graph really can advance this run."""
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph
        ledger = FileRuntimeStateStore(
            launcher.default_runtime_state_path(self.RUN, "t"))
        journal = pause_store.journal_for(self.RUN, artifact_base=self.base)

        def factory(saver):
            inner = build_graph(FakeAdapter([dict(item) for item in RESULTS],
                                            runtime_state=ledger, run_id=self.RUN,
                                            settlement_journal=journal),
                                checkpointer=saver, runtime_state=ledger, journal=journal)
            return HoldingGraph(inner, owner=self.LABELS[index],
                                released_by=released_by,
                                timeout=self.RENDEZVOUS_TIMEOUT, log=invocations)

        return recovery_runtime.RecoveryRequest(
            run_id=self.RUN, artifact_base=str(self.base), graph_factory=factory,
            **overrides)

    def race(self) -> SimpleNamespace:
        """Run the two contending recoveries concurrently and report what happened."""
        before = self.stall(self.RUN)
        path = recovery_store.recovery_record_path(self.RUN, artifact_base=self.base)
        barrier = threading.Barrier(2, timeout=self.RENDEZVOUS_TIMEOUT)
        arrivals: list[str] = []
        invocations: list[str] = []
        settled = (threading.Event(), threading.Event())
        outcomes: list[Any] = [None, None]
        errors: list[str] = []

        stores = [self.contender(index, path=path, barrier=barrier, arrivals=arrivals)
                  for index in (0, 1)]

        def contend(index: int) -> None:
            try:
                outcomes[index] = recovery_runtime.recover_stalled_run(
                    self.engine_request(index, released_by=settled[1 - index],
                                        invocations=invocations,
                                        observe_timeout_seconds=0.001),
                    store=stores[index])
            except BaseException as exc:                  # reported, never swallowed
                errors.append(f"{self.LABELS[index]}: {type(exc).__name__}: {exc}")
            finally:
                settled[index].set()

        threads = [threading.Thread(target=contend, args=(index,),
                                    name=f"claimant-{index}") for index in (0, 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=self.RENDEZVOUS_TIMEOUT * 3)
        self.assertFalse([thread.name for thread in threads if thread.is_alive()],
                         "a claimant never returned; the race did not resolve")
        self.assertEqual(errors, [], "a claimant raised instead of reporting an outcome")
        self.assertNotIn(None, outcomes, "a claimant reported no outcome at all")
        recovered = [item for item in outcomes
                     if item.status == recovery_runtime.RECOVERED]
        refused = [item for item in outcomes
                   if item.status != recovery_runtime.RECOVERED]
        return SimpleNamespace(
            before=before, outcomes=tuple(outcomes), recovered=tuple(recovered),
            refused=tuple(refused), arrivals=tuple(arrivals),
            invocations=tuple(invocations), barrier=barrier, stores=tuple(stores),
            labels={id(item): self.LABELS[index]
                    for index, item in enumerate(outcomes)},
            claimants={id(item): stores[index].claimant_id
                       for index, item in enumerate(outcomes)},
            owner_ids={id(item): stores[index].owner_id
                       for index, item in enumerate(outcomes)})

    def one_winner(self, race: SimpleNamespace) -> Any:
        self.assertEqual(
            len(race.recovered), 1,
            "exactly one of two concurrent claimants may recover the run; got "
            f"{[(item.status, item.code, item.detail) for item in race.outcomes]}")
        return race.recovered[0]

    # -- 1. exactly one RECOVERED ------------------------------------------------------
    def test_two_CONCURRENT_claimants_on_one_lease_yield_exactly_one_RECOVERED(self):
        race = self.race()
        winner = self.one_winner(race)
        self.assertEqual(winner.code, "RECOVERY_ADVANCED")
        self.assertTrue(winner.effect_performed,
                        "the winner claimed the run and then advanced nothing")
        self.assertEqual(winner.recovery_kind,
                         recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)

    # -- 2. exactly one refusal, AND it is the atomic claim that produced it ------------
    def test_the_LOSER_is_refused_BY_THE_CLAIM_and_performs_nothing(self):
        race = self.race()
        self.one_winner(race)
        self.assertEqual(len(race.refused), 1)
        loser = race.refused[0]
        self.assertEqual((loser.status, loser.code),
                         (recovery_runtime.CONFLICT, "RECOVERY_CLAIM_HELD"),
                         "the loser must be refused by the ATOMIC CLAIM itself; a "
                         "RECOVERY_CLAIM_LOST here would mean both claimants took the "
                         "lease and only the fence caught the second one, and a "
                         "NO_EFFECT would mean the lease was not held when it looked")
        self.assertFalse(loser.effect_performed)
        self.assertEqual(loser.resumed_checkpoint_id, "",
                         "a refusal carries no continuation handle")
        self.assertEqual(loser.recovery_id, race.recovered[0].recovery_id,
                         "both claimants contended for the SAME identity on the SAME run")

    # -- 3. exactly ONE effect and ONE head transition ---------------------------------
    def test_the_race_performs_exactly_ONE_effect_and_ONE_head_transition(self):
        race = self.race()
        winner = self.one_winner(race)
        self.assertEqual(list(race.invocations), [race.labels[id(winner)]],
                         "exactly one claimant may reach the graph at all; two "
                         f"invocations is two resumes of one run ({race.invocations})")
        self.assertEqual(winner.head_before, race.before)
        self.assertNotEqual(winner.head_after, race.before, "the head did not move")
        self.assertEqual(self.head(self.RUN), winner.head_after,
                         "the committed head is not the one the winner reported")
        self.assertEqual((race.refused[0].head_before, race.refused[0].head_after),
                         ("", ""), "the loser reports a head transition it never made")

    # -- 4. exactly ONE durable lease owner and ONE attempt lineage --------------------
    def test_the_durable_record_names_ONE_owner_and_ONE_promoted_attempt(self):
        race = self.race()
        winner = self.one_winner(race)
        record = recovery_store.store_for(self.RUN,
                                          artifact_base=self.base).read(self.RUN)
        self.assertEqual(record["claimant_id"], race.claimants[id(winner)],
                         "the durable lease names a claimant that did not win")
        self.assertEqual(record["owner_id"], race.owner_ids[id(winner)],
                         "the durable lease names a host that did not win")
        self.assertEqual(list(record["attempts"]), [winner.recovery_id],
                         "one race, one attempt lineage")
        entry = record["attempts"][winner.recovery_id]
        self.assertEqual(entry["stage"], "PROMOTED")
        self.assertEqual(entry["outcome"], recovery_runtime.RECOVERED)
        self.assertEqual(entry["head_before"], race.before)
        self.assertEqual(entry["head_after"], winner.head_after)

    # -- 5. the settled winner survives a RETRY and a REPLAY ---------------------------
    def test_a_RETRY_and_a_REPLAY_leave_the_WINNER_and_the_OUTCOME_unchanged(self):
        """R6: whoever won stays won, and re-driving the loser's work adds nothing.

        The race decides one winner; this asks whether that decision is DURABLE.  Two
        further ``recover_stalled_run`` invocations run over the settled run -- the retry
        the loser is entitled to make and a replay of the same request afterwards -- and
        neither may reach the graph, move the head, create an effect, add an attempt
        lineage, or rewrite the durable record's claimant.
        """
        race = self.race()
        winner = self.one_winner(race)
        store = recovery_store.store_for(self.RUN, artifact_base=self.base)
        record_before = store.read(self.RUN)
        head_before = self.head(self.RUN)
        replayed: list[str] = []
        released = threading.Event()
        released.set()                       # nothing to hold open: nothing may run
        for attempt in ("retry", "replay"):
            outcome = recovery_runtime.recover_stalled_run(
                self.engine_request(0, released_by=released, invocations=replayed,
                                    observe_timeout_seconds=0.001))
            self.assertNotEqual(
                outcome.status, recovery_runtime.RECOVERED,
                f"the {attempt} recovered a run the race had already settled: "
                f"{outcome.status}/{outcome.code} {outcome.detail}")
            self.assertFalse(outcome.effect_performed,
                             f"the {attempt} performed an external effect")
        self.assertEqual(replayed, [],
                         "a retry or a replay entered graph.invoke; that is a second "
                         f"transition over one settled run -- {replayed}")
        self.assertEqual(self.head(self.RUN), head_before,
                         "a retry or a replay moved the head a second time")
        self.assertEqual(store.read(self.RUN), record_before,
                         "a retry or a replay rewrote the durable authority record -- "
                         "the winner, its claimant, or its attempt lineage changed")
        self.assertEqual(record_before["claimant_id"], race.claimants[id(winner)],
                         "the record must still name the attempt that won the race")

    # -- the race really WAS concurrent, and this test says so --------------------------
    def test_both_claimants_really_were_INSIDE_the_claim_at_the_same_instant(self):
        """Without this the suite above could pass on two sequential invocations.

        ``arrivals`` is appended inside ``claim`` immediately before a two-party
        barrier, and ``Barrier.wait`` returns only once BOTH parties have arrived.  Two
        entries therefore mean neither claim could have returned before the other had
        entered: the contention is real, not a story about the order the threads ran in.
        """
        race = self.race()
        self.assertEqual(sorted(race.arrivals),
                         sorted(store.claimant_id for store in race.stores))
        self.assertEqual(len(set(race.arrivals)), 2,
                         "two ATTEMPTS arrived, or this is one attempt counted twice")
        self.assertFalse(race.barrier.broken,
                         "the barrier broke: a claimant never reached the claim")


@REQUIRES_LANGGRAPH
class SameProcessWatchdogRaceTests(ConcurrentClaimRaceTests):
    """F-001.  The SAME race, between two Watchdogs in ONE process, identities UNSUPPLIED.

    The suite above hands the two claimants ``host:pid8001`` and ``host:pid8002``.  That
    states the deployed separate-process topology, and it stays covered -- but it also
    MANUFACTURES the distinctness the exclusion depends on, so it proves the property it
    assumed rather than the property the product has.  With the identities supplied, a
    store that identified its claimant by process, or by process and role together, passed
    it while admitting two concurrent same-role actors in one process: both claims were
    granted, the second rotated the first's token, and the already-decided winner failed
    its next fence.

    Here ``OWNERS`` is ``None``.  Both stores are built the production-default way, so
    both carry the SAME ``owner_id`` and BOTH claim with ``owner_kind="recovery"`` -- the
    Watchdog-vs-Watchdog composition ``watchdog_supervisor`` makes reachable in one
    process by construction (CON-5).  Every assertion inherited from the suite above then
    applies to a race in which nothing about either claimant came from this file: exactly
    one ``RECOVERED``, one refusal and it came from the atomic claim, one effect and one
    head transition, one durable claimant with one attempt lineage, an unchanged winner
    under retry and replay, and two genuinely simultaneous arrivals inside the claim.
    """

    RUN = "run_inprocessrace"
    OWNERS = None

    def test_the_two_contenders_really_are_INDISTINGUISHABLE_by_process(self):
        """The premise, asserted rather than assumed.

        If ``default_owner_id`` ever stopped returning one value per process, this suite
        would quietly become a second copy of the separate-process one and would stop
        covering the case it exists for.
        """
        path = recovery_store.recovery_record_path(self.RUN, artifact_base=self.base)
        barrier = threading.Barrier(2, timeout=self.RENDEZVOUS_TIMEOUT)
        first, second = (self.contender(index, path=path, barrier=barrier, arrivals=[])
                         for index in (0, 1))
        self.assertEqual(first.owner_id, second.owner_id,
                         "the two contenders must share a process identity")
        self.assertNotEqual(first.claimant_id, second.claimant_id,
                            "two live execution attempts must be two claimants")


@REQUIRES_LANGGRAPH
class DeliveredBackoffLapsesTests(DeliveredWiringFixture):
    """G-6: SC-7's backoff must LAPSE in the composition the CLI actually ships.

    ``watchdog_state.gate`` reads ``float(clock.time()) if clock is not None else 0.0``
    and ``react`` stamps its deadline on the same timeline.  Every existing test supplies
    a ``ManualLeaseClock``, so both sides shared a clock and the deadline lapsed on cue --
    but ``launcher._watchdog_wiring`` built no clock at all, so in the delivered CLI
    ``react`` wrote a deadline of ``0 + delay`` and every later sweep, in every later
    process, compared it against a permanent ``0.0``.  A ``REFUSED`` or ``CONFLICT``
    outcome therefore blocked its identity FOREVER: not a bounded retry with backoff, but
    a permanent stop that only a moving head could clear -- and the head cannot move while
    the recovery is blocked.

    This is the same shape as the two wiring defects the review already found: a property
    verified only against a component the test supplied, never against the one the product
    composes.
    """

    RUN = "run_backoff"

    def sweep(self):
        buffer, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(errors):
            code = launcher.run_watchdog_cli(
                ["watchdog", "once", "--artifact-base", str(self.base),
                 "--results", self.results_file(), "--json"], runner=quiet_runner)
        return code, json.loads(buffer.getvalue()), errors.getvalue()

    def identity(self) -> str:
        return recovery_runtime.recovery_identity(
            run_id=self.RUN, thread_id="t", checkpoint_ns="",
            head_checkpoint_id=self.head(self.RUN),
            recovery_kind=recovery_runtime.RECOVERY_KIND_STALLED_ACTIVE)

    def predecessor_backoff(self, *, backoff_until: float) -> str:
        """One REFUSED attempt, recorded exactly as the supervisor records one."""
        from scripts.deterministic_workflow.watchdog_audit import FileWatchdogAudit
        audit = FileWatchdogAudit(self.base)
        recovery_id, head = self.identity(), self.head(self.RUN)
        audit.append(self.RUN, "watchdog_claim_opened",
                     {"recovery_id": recovery_id, "recovery_kind": "stalled_active",
                      "thread_id": "t", "checkpoint_ns": "", "head_before": head})
        audit.append(self.RUN, "watchdog_resume_outcome",
                     {"recovery_id": recovery_id, "outcome_status": "REFUSED",
                      "outcome_code": "PAUSE_RECORD_CORRUPT", "head_before": head,
                      "head_after": "", "effect_performed": False,
                      "resumed_checkpoint_id": "", "revalidation_codes": []})
        audit.append(self.RUN, "watchdog_failure",
                     {"recovery_id": recovery_id, "outcome_status": "REFUSED",
                      "outcome_code": "PAUSE_RECORD_CORRUPT", "attempt_ordinal": 1,
                      "budget_remaining": 4, "backoff_until": backoff_until})
        return recovery_id

    def test_a_backoff_that_has_ALREADY_LAPSED_no_longer_blocks_the_delivered_sweep(self):
        import time
        before = self.stall(self.RUN)
        self.predecessor_backoff(backoff_until=time.time() - 1.0)
        code, summary, err = self.sweep()
        row = self.row(summary, self.RUN)
        self.assertEqual(row["gate_action"], "ACT",
                         "a deadline a second in the past still blocked the sweep: the "
                         f"delivered wiring is reading no clock (reason="
                         f"{row['gate_reason']!r}, stderr={err})")
        self.assertEqual(row["outcome_status"], recovery_runtime.RECOVERED)
        self.assertNotEqual(self.head(self.RUN), before)
        self.assertEqual(code, 0)

    def test_a_backoff_still_PENDING_does_block_it(self):
        """The paired negative: the fix is a real clock, not a deleted backoff."""
        import time
        before = self.stall(self.RUN)
        self.predecessor_backoff(backoff_until=time.time() + 3600.0)
        code, summary, _err = self.sweep()
        row = self.row(summary, self.RUN)
        self.assertEqual((row["gate_action"], row["gate_reason"]),
                         ("DECLINE", "backoff_pending"))
        self.assertEqual(self.head(self.RUN), before)
        self.assertEqual(code, 0)

    def test_the_delivered_wiring_INJECTS_a_wall_clock_lease_source(self):
        """Stated structurally too, so the cause is named and not merely observed."""
        import time
        args = launcher.build_watchdog_parser().parse_args(
            ["watchdog", "once", "--artifact-base", str(self.base)])
        wiring = launcher._watchdog_wiring(args, runner=quiet_runner)
        self.assertIn("clock", wiring,
                      "the supervisor's gate and its outcome table both read this clock; "
                      "without it every backoff deadline is compared against 0.0")
        self.assertAlmostEqual(wiring["clock"].time(), time.time(), delta=5.0)
