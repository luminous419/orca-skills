"""OS-43 U-4: the run-scoped Coordinator liveness lease (AC-1's premise).

The store-level suites run on :class:`ManualLeaseClock` and never sleep.  The one suite
that must exercise a REAL background renewal thread paces it with an explicit short lease
and an explicit ``threading.Event`` waiter -- never with sleeps racing the clock -- which
is the discipline ``test_deterministic_workflow_lease_keeper`` already established.
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.deterministic_workflow import coordinator_liveness as module
from scripts.deterministic_workflow import lease_keeper, turn_boundary
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock

RUN = "run_live"


class PacedWaiter:
    """A ``LeaseKeeper`` waiter a test drives beat by beat.  Nothing here sleeps blindly."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.arrived = threading.Event()
        self.stopped = False

    def __call__(self, stop: threading.Event, interval: float) -> bool:
        self.arrived.set()
        while not self.release.wait(0.01):
            if stop.is_set() or self.stopped:
                return True
        self.release.clear()
        return stop.is_set() or self.stopped

    def cancel(self) -> None:
        self.stopped = True
        self.release.set()

    def beat(self) -> None:
        self.arrived.wait(5.0)
        self.release.set()


class RecordShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.clock = ManualLeaseClock()

    def store(self, owner="host:pid1", lease=60.0):
        return module.store_for(RUN, artifact_base=self.base, clock=self.clock,
                                owner_id=owner, lease_seconds=lease)

    def path(self):
        return module.liveness_record_path(RUN, artifact_base=self.base)

    def test_the_record_lands_at_a_NEW_filename_under_the_run_root(self):
        self.store().claim(RUN)
        self.assertTrue(self.path().is_file())
        self.assertEqual(self.path().name, ".coordinator_liveness.json")

    def test_the_key_set_is_CLOSED_and_validated_on_every_read(self):
        self.store().claim(RUN)
        document = json.loads(self.path().read_text(encoding="utf-8"))
        self.assertEqual(set(document["record"]),
                         set(module.COORDINATOR_LIVENESS_RECORD_KEYS))
        document["record"]["surprise"] = 1
        self.path().write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(module.CoordinatorLivenessCorrupt):
            self.store().read(RUN)

    def test_a_corrupt_record_is_never_read_as_no_prior_lease(self):
        self.path().parent.mkdir(parents=True, exist_ok=True)
        self.path().write_text("{not json", encoding="utf-8")
        with self.assertRaises(module.CoordinatorLivenessCorrupt):
            self.store().read(RUN)
        self.assertEqual(module.liveness_status(RUN, artifact_base=self.base),
                         module.LIVENESS_UNREADABLE)

    def test_the_owner_id_is_the_PROCESS_which_is_the_liveness_granularity(self):
        from scripts.deterministic_workflow.runtime_state import default_owner_id
        record = module.store_for(RUN, artifact_base=self.base,
                                  clock=self.clock).claim(RUN)
        self.assertEqual(record["owner_id"], default_owner_id())

    def test_a_fenced_write_needs_the_token_claim_returned(self):
        store = self.store()
        store.claim(RUN)
        for absent in ("", None):
            with self.assertRaises(module.CoordinatorLivenessClaimLost):
                store.heartbeat(RUN, absent)          # type: ignore[arg-type]


class FourValuedReadTests(unittest.TestCase):
    """ABSENT is not EXPIRED, and UNREADABLE is not EXPIRED either."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.clock = ManualLeaseClock()

    def status(self):
        return module.liveness_status(RUN, artifact_base=self.base, clock=self.clock)

    def test_a_run_that_never_published_a_lease_reads_ABSENT_not_EXPIRED(self):
        self.assertEqual(self.status(), module.LIVENESS_ABSENT)

    def test_a_fresh_lease_reads_LIVE(self):
        module.store_for(RUN, artifact_base=self.base, clock=self.clock).claim(RUN)
        self.assertEqual(self.status(), module.LIVENESS_LIVE)

    def test_a_lease_nobody_refreshed_reads_EXPIRED_after_the_threshold(self):
        module.store_for(RUN, artifact_base=self.base, clock=self.clock).claim(RUN)
        self.clock.advance(59.0)
        self.assertEqual(self.status(), module.LIVENESS_LIVE)
        self.clock.advance(2.0)
        self.assertEqual(self.status(), module.LIVENESS_EXPIRED)

    def test_a_RELEASED_lease_reads_ABSENT_never_EXPIRED(self):
        store = module.store_for(RUN, artifact_base=self.base, clock=self.clock)
        token = store.claim(RUN)["lease_token"]
        store.release(RUN, token)
        self.assertEqual(self.status(), module.LIVENESS_ABSENT,
                         "a Coordinator that let the run go is not evidence of death")

    def test_the_status_vocabulary_is_closed_at_four(self):
        self.assertEqual(module.LIVENESS_STATUSES,
                         ("LIVE", "EXPIRED", "ABSENT", "UNREADABLE"))


class RenewalIndependenceTests(unittest.TestCase):
    """The whole point of the record: renewal is INDEPENDENT of claimed sections."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_the_cadence_is_derived_from_the_lease_and_stays_below_it(self):
        for lease in (0.3, 60.0, 900.0):
            self.assertLess(lease_keeper.heartbeat_interval_for(lease), lease)
        self.assertEqual(lease_keeper.heartbeat_interval_for(60.0), 20.0)

    def test_a_coordinator_ALIVE_BETWEEN_claimed_sections_keeps_the_lease_fresh(self):
        """The false positive is GONE: no claimed section is entered anywhere here."""
        waiter = PacedWaiter()
        keeper = module.begin_coordinator_liveness(RUN, artifact_base=self.base,
                                                   lease_seconds=0.6, waiter=waiter)
        self.addCleanup(lambda: module.end_coordinator_liveness(keeper, RUN,
                                                                artifact_base=self.base))
        self.assertIsNotNone(keeper)
        before = module.liveness_record(RUN, artifact_base=self.base)["last_heartbeat_at"]
        waiter.beat()
        self.assertTrue(keeper.wait_for_beats(1, timeout=5.0))
        after = module.liveness_record(RUN, artifact_base=self.base)["last_heartbeat_at"]
        self.assertGreater(after, before)
        self.assertEqual(module.liveness_status(RUN, artifact_base=self.base),
                         module.LIVENESS_LIVE)

    def test_lease_keeper_is_reused_UNMODIFIED(self):
        """The store exposes exactly the one method the keeper calls."""
        import inspect
        signature = inspect.signature(module.FileCoordinatorLivenessStore.heartbeat)
        self.assertEqual(list(signature.parameters), ["self", "run_id", "lease_token"])
        source = Path(lease_keeper.__file__).read_text(encoding="utf-8")
        self.assertIn("self._runtime_state.heartbeat(self._intent_id, self._lease_token)",
                      source, "lease_keeper.py must not have been modified")

    def test_a_coordinator_that_STOPS_refreshing_expires(self):
        clock = ManualLeaseClock()
        module.store_for(RUN, artifact_base=self.base, clock=clock,
                         lease_seconds=30.0).claim(RUN)
        clock.advance(31.0)
        self.assertEqual(module.liveness_status(RUN, artifact_base=self.base, clock=clock),
                         module.LIVENESS_EXPIRED)

    def test_ending_liveness_records_the_release_rather_than_deleting_the_file(self):
        keeper = module.begin_coordinator_liveness(RUN, artifact_base=self.base,
                                                   lease_seconds=0.6,
                                                   waiter=PacedWaiter())
        self.assertTrue(module.end_coordinator_liveness(keeper, RUN,
                                                        artifact_base=self.base))
        record = module.liveness_record(RUN, artifact_base=self.base)
        self.assertIsNotNone(record)
        self.assertTrue(record["released_at"],
                        "'this Coordinator let go of that run at 12:04' is a fact a reader "
                        "of the run's artifacts can see; an absent file is not")


class BlastRadiusTests(unittest.TestCase):
    """D-4.4: additive only.  No existing record, verdict or refusal path changes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_the_session_binding_document_is_UNCHANGED(self):
        turn_boundary.bind_session_run(RUN, session_id="s1", artifact_base=self.base)
        path = turn_boundary.session_binding_path(RUN, "s1", artifact_base=self.base)
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(document),
                         {"schema", "run_id", "session_id", "bound_at", "released_at"})
        self.assertEqual(document["schema"], turn_boundary.SESSION_BINDING_SCHEMA)

    def test_no_existing_closed_key_set_gained_a_member(self):
        from scripts.deterministic_workflow import pause_store, runtime_state
        self.assertNotIn("liveness_token", pause_store.PAUSE_RECORD_KEYS)
        self.assertNotIn("liveness_token", runtime_state.RECORD_KEYS)

    def test_the_new_filename_is_invisible_to_every_existing_reader(self):
        from scripts.deterministic_workflow import pause_store
        (self.base / "artifacts" / "runs" / RUN).mkdir(parents=True)
        module.store_for(RUN, artifact_base=self.base).claim(RUN)
        self.assertEqual(pause_store.discover_paused_runs(self.base), (),
                         "discover_paused_runs filters on PAUSE_RECORD_FILENAME")
        turn_boundary.bind_session_run(RUN, session_id="s1", artifact_base=self.base)
        self.assertEqual(turn_boundary.session_bound_run_id("s1",
                                                            artifact_base=self.base), RUN)

    def test_the_harness_producer_is_opt_outable_and_never_raises(self):
        from scripts import orca_runtime_harness
        self.assertEqual(orca_runtime_harness.OrcaRuntimeHarness.LIVENESS_ENV,
                         "ORCA_OS43_COORDINATOR_LIVENESS")
        harness = orca_runtime_harness.OrcaRuntimeHarness.__new__(
            orca_runtime_harness.OrcaRuntimeHarness)
        harness.run_id = ""
        harness.artifact_dir = self.base
        harness._begin_turn_boundary_liveness()      # no run id: nothing to publish
        harness._end_turn_boundary_liveness()        # and cleanup is a no-op


class LivenessCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def run_cli(self, argv):
        import contextlib
        import io
        from scripts.deterministic_workflow.launcher import run_cli
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run_cli(argv)
        return code, buffer.getvalue()

    def test_the_verb_publishes_a_lease_and_reports_the_four_valued_read(self):
        code, out = self.run_cli(["turn-end-liveness", "--run-id", RUN,
                                  "--artifact-base", str(self.base), "--json"])
        self.assertEqual(code, 0)
        self.assertIn("liveness_status", out)
        code, out = self.run_cli(["turn-end-liveness", "--run-id", RUN,
                                  "--artifact-base", str(self.base), "--status",
                                  "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["liveness_status"], module.LIVENESS_LIVE,
                         "a cron-style publish leaves the lease LIVE until it expires; "
                         "releasing it on exit would defeat the whole point")
        code, out = self.run_cli(["turn-end-liveness", "--run-id", RUN,
                                  "--artifact-base", str(self.base), "--release",
                                  "--json"])
        self.assertEqual(json.loads(out)["liveness_status"], module.LIVENESS_ABSENT)

    def test_turn_end_bind_still_works_with_no_liveness(self):
        code, _out = self.run_cli(["turn-end-bind", "--run-id", RUN, "--session-id", "s1",
                                   "--artifact-base", str(self.base), "--no-liveness",
                                   "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(module.liveness_status(RUN, artifact_base=self.base),
                         module.LIVENESS_ABSENT)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
