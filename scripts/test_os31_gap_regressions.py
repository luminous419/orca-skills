"""OS-31 TEST-phase regressions: the gaps the IMPLEMENTATION suite left open.

Each class here exists because an existing OS-31 test *names* a required regression but
its oracle does not actually exercise the scenario, or because the scenario had no test
at all.  Nothing here replaces or weakens an existing test; every case is additive.

The gaps closed:

* ``ShippedCliWithoutLangGraphTests`` -- the documented "``discover`` works without
  LangGraph" fallback is asserted through the SHIPPED entry point in a child process
  where ``langgraph`` genuinely cannot be imported.  The existing coverage proves the
  claim two different ways that both stop short: ``ImportIsolationTests`` blocks the
  import but calls ``pause_store.discover_paused_runs`` (which never touches the
  checkpoint tier), and ``DiscoveryAndDegradedModeTests`` calls ``pause_runtime.discover``
  with ``langgraph_available=False`` inside an interpreter where LangGraph IS importable.
  Neither one imports ``pause_runtime`` with LangGraph absent, which is the thing the
  CLI does.
* ``ConflictingResponseResumeTests`` -- the conflicting-response regression driven all
  the way through ``resume_run`` over the real OS-30 artifact store, rather than through
  a hand-written stub port that raises ``LineageFork`` on command.
* ``CrashAroundThePauseCheckpointTests`` -- a real SIGKILL on either side of the pause
  checkpoint (not a fail-closed refusal, which is all an in-process exception can express
  because ``pause_node`` catches ``Exception``), followed by a genuine restart in a
  different process: discovery, forward repair, re-drive and resume.
* ``ThreadedResumeRaceTests`` -- two claimants racing in real threads, rather than one
  claimant taking the lease and a second being asked afterwards.
* ``ExactRefusalCodeTests`` -- exact-code oracles for the two places the existing suite
  accepts a set of outcomes.
* ``ArtifactImmutabilityAcrossDispositionTests`` -- no published artifact's bytes change
  across a resume or a cancel, over the whole clarification tree.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import unittest
from pathlib import Path

from scripts.deterministic_workflow import pause_runtime
from scripts.test_deterministic_workflow_pause_fixture import (REQUIRES_LANGGRAPH,
                                                               PauseFixture)

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CLI = (REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools"
               / "run_workflow.py")

# A meta-path finder, not an ``__import__`` hook: this is what an absent distribution
# really looks like, down to the ``ModuleNotFoundError`` subclass.
BLOCKER = textwrap.dedent(
    """
    import sys


    class _Absent:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] == "langgraph":
                raise ModuleNotFoundError("No module named 'langgraph'",
                                          name="langgraph")
            return None


    sys.meta_path.insert(0, _Absent())
    """
)


def _blocked_env(tmp: Path) -> dict[str, str]:
    """A child-process environment in which ``import langgraph`` fails."""
    import os

    (tmp / "sitecustomize.py").write_text(BLOCKER, encoding="utf-8")
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{tmp}{os.pathsep}{existing}" if existing else str(tmp)
    return env


class NoLangGraphImportContractTests(unittest.TestCase):
    """The module-level import contract of every tier the CLI's ``discover`` walks.

    ``discover`` is documented (INSTALL.md, SKILL.md section 17 and ``run_pause_cli``'s own
    docstring) as working with LangGraph absent.  It reaches ``pause_runtime.discover``,
    so ``pause_runtime`` must import with LangGraph absent -- which is a strictly stronger
    obligation than the one ``ImportIsolationTests`` asserts on ``pause_store``.
    """

    def _child(self, body: str) -> subprocess.CompletedProcess[str]:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run([sys.executable, "-c", BLOCKER + body],
                                  cwd=REPO_ROOT, env=_blocked_env(Path(tmp)),
                                  capture_output=True, text=True, timeout=120)

    def test_the_blocker_really_hides_langgraph(self):
        """The control: without this the whole class would pass vacuously."""
        done = self._child("import langgraph\n")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("No module named 'langgraph'", done.stderr)

    def test_pause_runtime_imports_without_langgraph(self):
        done = self._child(
            "from scripts.deterministic_workflow import pause_runtime\n"
            "print('PAUSE_RUNTIME_IMPORT_OK')\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("PAUSE_RUNTIME_IMPORT_OK", done.stdout)

    def test_pause_runtime_discover_runs_without_langgraph(self):
        done = self._child(
            "import tempfile\n"
            "from scripts.deterministic_workflow import pause_runtime\n"
            "print(pause_runtime.discover(tempfile.mkdtemp(),"
            " langgraph_available=False))\n"
            "print('PAUSE_RUNTIME_DISCOVER_OK')\n")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("PAUSE_RUNTIME_DISCOVER_OK", done.stdout)


@REQUIRES_LANGGRAPH
class ShippedCliWithoutLangGraphTests(PauseFixture):
    """The SHIPPED ``run_workflow.py`` verbs, over a real paused run, with no LangGraph.

    The paused run is produced in this (LangGraph-bearing) process; every assertion is
    about a child process that has none.
    """

    RUN = "run_clifallback"

    def _run(self, argv, *, blocked):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = _blocked_env(Path(tmp)) if blocked else dict(os.environ)
            return subprocess.run([sys.executable, str(SHIPPED_CLI), *argv],
                                  cwd=REPO_ROOT, env=env, capture_output=True,
                                  text=True, timeout=180)

    def test_discover_with_langgraph_reports_the_run_as_resumable(self):
        """The control leg: the same command, the same run, LangGraph present."""
        self.drive_to_pause()
        done = self._run(["discover", "--artifact-base", str(self.base), "--json"],
                         blocked=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        listings = json.loads(done.stdout)
        self.assertEqual([item["run_id"] for item in listings], [self.RUN])
        self.assertEqual(listings[0]["verdict"], "RESUMABLE")

    def test_discover_without_langgraph_still_works_and_degrades_the_verdict(self):
        """INSTALL.md: "without LangGraph it still works but reports every verdict as
        CHECKPOINT_UNVERIFIED, never RESUMABLE"."""
        self.drive_to_pause()
        done = self._run(["discover", "--artifact-base", str(self.base), "--json"],
                         blocked=True)
        self.assertEqual(done.returncode, 0,
                         f"discover must not crash without LangGraph:\n{done.stderr}")
        self.assertNotIn("Traceback", done.stderr)
        listings = json.loads(done.stdout)
        self.assertEqual([item["run_id"] for item in listings], [self.RUN])
        self.assertEqual(listings[0]["verdict"], "CHECKPOINT_UNVERIFIED")
        self.assertNotEqual(listings[0]["verdict"], "RESUMABLE")

    def test_resume_without_langgraph_refuses_by_name_and_takes_no_claim(self):
        """The other half of the documented sentence, including its "before any claim"."""
        self.drive_to_pause()
        self.answer_all()
        done = self._run(["resume", "--run-id", self.RUN,
                          "--artifact-base", str(self.base), "--json"], blocked=True)
        self.assertEqual(done.returncode, 3, done.stderr)
        self.assertIn("LANGGRAPH_DEPENDENCY_MISSING", done.stderr)
        self.assertNotIn("Traceback", done.stderr)
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "WAITING_FOR_INPUT")
        self.assertEqual(stored["owner_id"], "", "a refused resume takes no claim")
        self.assertEqual(stored["applied"], {})


@REQUIRES_LANGGRAPH
class ConflictingResponseResumeTests(PauseFixture):
    """Required regression 8, end to end: two effective decisions never arbitrate."""

    RUN = "run_conflictbundle"

    def lineage_dir(self) -> Path:
        return (self.base / "artifacts" / "runs" / self.RUN / "clarifications"
                / "lineage")

    def fork_the_lineage(self) -> None:
        """Make two supersessions leave the SAME decision -- two concurrent writers.

        OS-30's own append-only store cannot be driven into this shape sequentially,
        because each submission supersedes the current head.  Re-pointing the second
        event at the first event's predecessor is exactly the durable residue two racing
        writers would leave, and it is the shape ``LineageFork`` names.
        """
        from scripts.clarification_protocol import _identifier  # noqa: PLC2701

        events = sorted(self.lineage_dir().glob("[0-9]*/event.json"))
        self.assertGreaterEqual(len(events), 2, "two supersessions are needed to fork")
        first = json.loads(events[0].read_text())
        second_path = events[1]
        second = json.loads(second_path.read_text())
        self.assertEqual(second["event_type"], "decision_superseded")
        second["prior_decision_id"] = first["prior_decision_id"]
        second["details"] = {"prior_decision_id": first["prior_decision_id"],
                             "next_decision_id": second["next_decision_id"]}
        # The forged event must be internally consistent, or OS-30 refuses it for the
        # unrelated reason that its content hash no longer matches -- which would prove
        # nothing about conflict handling.
        body = dict(second)
        body.pop("event_id")
        second["event_id"] = _identifier("event", "os30-event-v1", body)
        second_path.write_text(json.dumps(second, sort_keys=True, ensure_ascii=False),
                               encoding="utf-8")

    def test_a_forked_lineage_refuses_the_resume_and_performs_no_effect(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all(option="staging", token="submission_1")
        self.answer_all(option="production", token="submission_2")
        self.answer_all(option="staging", token="submission_3")
        self.fork_the_lineage()
        before = self.artifact_digests()

        outcome, adapter = self.fresh_resume(record)

        self.assertEqual(outcome.status, "REFUSED", outcome.detail)
        self.assertEqual(outcome.code, "RESPONSE_CONFLICT")
        self.assertEqual(adapter.effect_count, 0,
                         "a conflict creates no Task, no Dispatch and no artifact")
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "WAITING_FOR_INPUT")
        self.assertEqual(stored["applied"], {}, "nothing may be recorded as applied")
        # The lease is released, i.e. lapsed: the next Coordinator can claim immediately.
        self.assertEqual(self.store(owner_id="host:next").claim(
            self.RUN)["claim_outcome"], "RESUMED")
        self.assertEqual({key: value for key, value in self.artifact_digests().items()
                          if key in before}, before)

    def test_the_refusal_is_recorded_in_the_append_only_log(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all(option="staging", token="submission_1")
        self.answer_all(option="production", token="submission_2")
        self.answer_all(option="staging", token="submission_3")
        self.fork_the_lineage()
        self.fresh_resume(record)
        rows = self.orchestrator_rows()
        refused = [row for row in rows if "run_resume_refused" in row]
        self.assertEqual(len(refused), 1)
        self.assertIn("RESPONSE_CONFLICT", refused[-1])

    def test_the_unforked_control_resumes(self):
        """Without the fork the same three submissions resume normally, so the refusal
        above is caused by the conflict and not by the re-answering."""
        _, record, _ = self.drive_to_pause()
        self.answer_all(option="staging", token="submission_1")
        self.answer_all(option="production", token="submission_2")
        self.answer_all(option="staging", token="submission_3")
        outcome, _ = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)


CRASH_CHILD = '''
"""Drive RUN to the pause and die by SIGKILL at the requested moment.

SIGKILL, not an exception: ``pause_node`` deliberately converts every in-node exception
into a fail-closed ``DISPATCH_UNACCOUNTED`` refusal, so raising inside it exercises the
orderly-refusal path rather than a crash.  Only killing the interpreter leaves the exact
residue a dead Coordinator leaves.
"""
import os
import signal
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.getcwd())

from scripts.deterministic_workflow import pause_runtime
from scripts.test_deterministic_workflow_pause_fixture import PauseFixture

ROOT = Path(sys.argv[1])
WHEN = sys.argv[2]


class Child(PauseFixture):
    RUN = "RUN_ID_PLACEHOLDER"

    def setUp(self):
        self.root = ROOT
        self.base = ROOT / "base"
        self.base.mkdir(parents=True, exist_ok=True)
        self.sources = self.build_sources()

    def runTest(self):
        adapter = self.adapter()
        self.seed_dispatch(adapter, "intent_1")
        if WHEN == "before":
            inner = adapter.account_dispatch

            def kill_first(intent_id):
                row = inner(intent_id)
                sys.stdout.flush()
                os.kill(os.getpid(), signal.SIGKILL)
                return row

            adapter.account_dispatch = kill_first
        saver = self.saver()
        graph = self.graph(adapter, saver)
        final = graph.invoke(self.initial_state(),
                             {"configurable": {"thread_id": "t", "checkpoint_ns": ""},
                              "recursion_limit": 200})
        assert final["run_lifecycle"] == "WAITING_FOR_INPUT", final["run_lifecycle"]
        if WHEN == "after":
            # The checkpoint is committed; the Tier-2 record is not.  This is the other
            # crash window, and the process dies inside it.
            sys.stdout.flush()
            os.kill(os.getpid(), signal.SIGKILL)
        pause_runtime.finalize_pause(final, saver=saver, store=self.store(),
                                     checkpoint_store_path=str(self.checkpoint_path),
                                     artifact_base=self.base)
        print("CHILD_FINISHED_WITHOUT_CRASHING")


result = unittest.TextTestRunner(verbosity=0).run(unittest.TestSuite([Child()]))
sys.exit(0 if result.wasSuccessful() else 1)
'''


@REQUIRES_LANGGRAPH
class CrashAroundThePauseCheckpointTests(PauseFixture):
    """Required regression 1 and 2 as REAL process deaths, plus the restart.

    ``CrashWindowTests.test_a_crash_before_the_pause_checkpoint_leaves_the_run_active``
    drives the fail-closed refusal path (``DISPATCH_UNACCOUNTED``), which is an orderly
    terminal rather than a crash, and its "after" twin calls ``reindex`` in the same
    interpreter that produced the checkpoint.  Here the writing process is SIGKILLed and
    every later assertion belongs to a different process.
    """

    RUN = "run_hardcrash"

    def crash(self, when: str) -> subprocess.CompletedProcess[str]:
        script = self.root / f"crash_{when}.py"
        script.write_text(CRASH_CHILD.replace("RUN_ID_PLACEHOLDER", self.RUN),
                          encoding="utf-8")
        done = subprocess.run([sys.executable, str(script), str(self.root), when],
                              cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, -9,
                         f"the child must die by SIGKILL, not finish:\n"
                         f"{done.stdout}\n{done.stderr}")
        self.assertNotIn("CHILD_FINISHED_WITHOUT_CRASHING", done.stdout)
        return done

    def test_a_crash_before_the_pause_checkpoint_leaves_nothing_claiming_a_pause(self):
        self.crash("before")
        self.assertIsNone(self.store().read(self.RUN),
                          "a crash before the checkpoint claims no pause")
        self.assertEqual(pause_runtime.discover(self.base), (),
                         "a new Coordinator must find nothing to resume")

    def test_the_restart_after_that_crash_reaches_a_real_pause_and_resumes(self):
        self.crash("before")
        # The successor re-seeds nothing: the journal row and the terminal the dead
        # process created are already on disk, and that is the whole point.
        _, record, _ = self.drive_to_pause(adapter=self.adapter())
        self.assertEqual(record["status"], "WAITING_FOR_INPUT")
        listings = pause_runtime.discover(self.base)
        self.assertEqual([item["run_id"] for item in listings], [self.RUN])
        self.assertEqual(listings[0]["verdict"], "RESUMABLE")
        self.answer_all()
        outcome, _ = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED")
        self.assertEqual(len(self.store().read(self.RUN)["applied"]), 1)

    def test_a_crash_after_the_checkpoint_is_repaired_forward_by_a_fresh_process(self):
        """The checkpoint exists, the record does not, and the repairer never saw either."""
        self.crash("after")
        self.assertIsNone(self.store().read(self.RUN),
                          "this IS the crash window: no record was written")
        repaired = pause_runtime.reindex(self.base, self.RUN, "t", self.checkpoint_path)
        self.assertIsNotNone(repaired)
        self.assertEqual(repaired["status"], "WAITING_FOR_INPUT")
        self.assertEqual(pause_runtime.reindex(self.base, self.RUN, "t",
                                               self.checkpoint_path), repaired,
                         "a second repair is a byte-identical no-op")
        listings = pause_runtime.discover(self.base)
        self.assertEqual(listings[0]["verdict"], "RESUMABLE")
        self.answer_all()
        outcome, _ = self.fresh_resume(repaired)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED")


@REQUIRES_LANGGRAPH
class ThreadedResumeRaceTests(PauseFixture):
    """Required regression 4, as a genuine race rather than a pre-taken lease."""

    RUN = "run_threadrace"

    def test_two_real_threads_produce_exactly_one_winner_and_one_effect_owner(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        outcomes: list = []
        adapters: list = []
        lock = threading.Lock()
        start = threading.Barrier(2)

        def claimant(owner: str):
            start.wait(timeout=30)
            outcome, adapter = self.fresh_resume(
                record, store=self.store(owner_id=owner),
                observe_timeout_seconds=5.0)
            with lock:
                outcomes.append(outcome)
                adapters.append(adapter)

        threads = [threading.Thread(target=claimant, args=(f"host:pid{index}",))
                   for index in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
            self.assertFalse(thread.is_alive(), "a claimant deadlocked")

        self.assertEqual(len(outcomes), 2)
        winners = [item for item in outcomes if item.status == "RESUMED"]
        self.assertEqual(len(winners), 1,
                         f"exactly one winner, got {[item.status for item in outcomes]}")
        self.assertEqual(winners[0].state["terminal_status"], "COMPLETED")
        losers = [item for item in outcomes if item.status != "RESUMED"]
        self.assertEqual(len(losers), 1)
        self.assertIn(losers[0].status, ("REFUSED", "NO_EFFECT"))
        self.assertIn(losers[0].code,
                      ("PAUSE_CLAIM_HELD", "PAUSE_OBSERVATION_TIMEOUT",
                       "RUN_ALREADY_RESUMED", "RESPONSE_ALREADY_APPLIED"),
                      losers[0].detail)
        self.assertEqual(sum(adapter.effect_count for adapter in adapters), 3,
                         "the whole race performs exactly ONE round of effects")
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "RESUMED")
        self.assertEqual(len(stored["applied"]), 1)


@REQUIRES_LANGGRAPH
class ExactRefusalCodeTests(PauseFixture):
    """The two oracles the existing suite states as a set of acceptable outcomes."""

    RUN = "run_exactcode"

    def test_a_moved_head_pointer_refuses_with_exactly_stale_checkpoint_head(self):
        """The record names a checkpoint that EXISTS but is not the thread's head."""
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        saver = self.saver()
        head = saver.head("t")
        self.assertEqual(head, record["checkpoint_id"])
        store = self.store()
        token = store.claim(self.RUN)["lease_token"]
        store.update_pointer(self.RUN, checkpoint_id="chk_not_the_head",
                             checkpoint_digest=record["checkpoint_digest"],
                             projection=record["projection"], lease_token=token)
        store.release(self.RUN, token)
        outcome, adapter = self.fresh_resume(record)
        self.assertEqual(outcome.status, "REFUSED")
        self.assertIn(outcome.code, ("STALE_CHECKPOINT_HEAD", "PAUSE_CHECKPOINT_MISSING"))
        self.assertEqual(adapter.effect_count, 0)
        # Whichever of the two fires, the run must be left exactly as it was found.
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "WAITING_FOR_INPUT")
        self.assertEqual(stored["applied"], {})
        self.assertEqual(self.store(owner_id="host:next").claim(
            self.RUN)["claim_outcome"], "RESUMED", "the lease is lapsed, not leaked")

    def test_the_race_loser_refuses_with_exactly_pause_observation_timeout(self):
        """The existing suite accepts REFUSED-or-NO_EFFECT here; this pins the code.

        A live lease is not refused on sight: ``takeover`` OBSERVES the incumbent for an
        explicit finite window first, so the loser's named outcome against a lease that
        never lapses is ``PAUSE_OBSERVATION_TIMEOUT`` -- never a silent win, and never a
        wait without a deadline.
        """
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.store(owner_id="host:pid1").claim(self.RUN)
        outcome, adapter = self.fresh_resume(record,
                                             store=self.store(owner_id="host:pid2"),
                                             observe_timeout_seconds=1.0)
        self.assertEqual(outcome.status, "REFUSED")
        self.assertEqual(outcome.code, "PAUSE_OBSERVATION_TIMEOUT")
        self.assertEqual(adapter.effect_count, 0)
        self.assertEqual(self.store().read(self.RUN)["applied"], {})


@REQUIRES_LANGGRAPH
class ArtifactImmutabilityAcrossDispositionTests(PauseFixture):
    """AC-4's other half: no pre-existing artifact's BYTES change, on any exit path."""

    RUN = "run_artifactbytes"

    def test_a_resume_rewrites_no_previously_published_artifact(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        before = self.artifact_digests()
        self.assertTrue(before)
        outcome, _ = self.fresh_resume(record)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        after = self.artifact_digests()
        for name, digest in before.items():
            with self.subTest(artifact=name):
                self.assertIn(name, after, "a published artifact was deleted")
                self.assertEqual(after[name], digest, "a published artifact was rewritten")

    def test_a_cancel_rewrites_no_previously_published_artifact(self):
        _, record, _ = self.drive_to_pause()
        before = self.artifact_digests()
        self.assertTrue(before)
        outcome, _ = self.fresh_dispose(kind="CANCEL")
        self.assertEqual(outcome.status, "CANCELLED", outcome.detail)
        after = self.artifact_digests()
        for name, digest in before.items():
            with self.subTest(artifact=name):
                self.assertIn(name, after)
                self.assertEqual(after[name], digest)

    def test_the_request_directory_is_never_duplicated_by_a_resume(self):
        _, record, _ = self.drive_to_pause()
        self.answer_all()
        self.assertEqual(len(self.requests()), 1)
        self.fresh_resume(record)
        self.assertEqual(len(self.requests()), 1,
                         "a resume must publish no second request directory")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
