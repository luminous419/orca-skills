"""C2-001: an exclusive, fail-closed durable claim.

Two properties are proven separately here, because they fail for different reasons and one
does not imply the other:

* **concurrent claim safety** -- two *real* operating-system processes racing on one stable
  intent produce exactly one external start.  These tests spawn processes and contend on a
  real ``fcntl.flock``; a thread or a mocked call counter would not exercise the defect at
  all, since the original code was atomic-per-write and only unsafe across processes.
* **crash-safe write** -- a write interrupted part-way leaves the previous ledger intact.

Every guard is checked for load-bearingness: the ``*_is_load_bearing`` tests disable the
guard and assert the corresponding failure reappears, so removing the guard cannot leave a
green suite.  Time-dependent behaviour uses :class:`ManualLeaseClock`, so no test sleeps its
way to an assertion.
"""
from __future__ import annotations

import importlib.metadata
import json
import multiprocessing
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES, make_intent
from scripts.deterministic_workflow.state import initial_state


def _langgraph_ok() -> bool:
    """The dependency-absent lane blocks the import itself, so the guard must be import-based."""
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


REQUIRES_LANGGRAPH = unittest.skipUnless(_langgraph_ok(), "pinned langgraph runtime is absent")

SCHEMA = "os40.runtime_state.v2"


def _intent(run_id="run_race", role="WORKER"):
    state = initial_state(run_id=run_id, thread_id="t", phases=("ANALYSIS",),
                          capabilities=BASE_CAPABILITIES)
    return make_intent(state, role, "PHASE_GATE")


# ---------------------------------------------------------------------------------------
# Child-process entry points.  They must live at module scope so the "spawn" start method
# can import them in a brand-new interpreter -- which is exactly the isolation that makes
# these real multi-process races rather than same-process theatre.
# ---------------------------------------------------------------------------------------

def _child_claim(ledger_path, starts_path, barrier, disable_flock, run_id):
    from scripts.deterministic_workflow import runtime_state as rs
    if disable_flock:
        # Mutation mode: keep every other line of the claim, remove only the lock.
        rs.fcntl.flock = lambda *args, **kwargs: None
    store = rs.FileRuntimeStateStore(ledger_path)
    intent = _intent(run_id)
    barrier.wait()
    try:
        record = store.claim(intent)
    except rs.RuntimeStateConflict:
        return
    if record["claim_outcome"] != rs.CREATED:
        return
    # Stands in for adapter.start(): the one line only the executor of this intent may run.
    with open(starts_path, "a", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()}\n")


def _child_claim_then_die(ledger_path, ready, lease_seconds):
    from scripts.deterministic_workflow import runtime_state as rs
    store = rs.FileRuntimeStateStore(ledger_path, lease_seconds=lease_seconds)
    store.claim(_intent())
    ready.set()
    os.kill(os.getpid(), signal.SIGKILL)     # silent death: no release, no heartbeat


def _child_holds_the_lock(lock_path, acquired, release):
    import fcntl
    handle = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(handle, fcntl.LOCK_EX)
    acquired.set()
    release.wait(30)
    fcntl.flock(handle, fcntl.LOCK_UN)
    os.close(handle)


def _count_starts(path):
    return sum(1 for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip())


class ConcurrentClaimTests(unittest.TestCase):
    """Real processes, one ledger, one intent: exactly one external start."""

    PROCESSES = 4
    TRIALS = 6

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ctx = multiprocessing.get_context("spawn")

    def _race(self, *, disable_flock=False, run_ids=None, ledgers=None):
        starts = self.root / f"starts_{time.time_ns()}"
        starts.write_text("", encoding="utf-8")
        ledger = self.root / f"ledger_{time.time_ns()}.json"
        run_ids = run_ids or [("run_race", ) for _ in range(self.PROCESSES)]
        barrier = self.ctx.Barrier(self.PROCESSES)
        procs = []
        for index in range(self.PROCESSES):
            path = ledgers[index] if ledgers else ledger
            procs.append(self.ctx.Process(
                target=_child_claim,
                args=(str(path), str(starts), barrier, disable_flock, run_ids[index][0])))
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(60)
            self.assertFalse(proc.is_alive(), "a racing child never terminated")
        return _count_starts(starts)

    def test_two_processes_racing_one_intent_start_the_effect_exactly_once(self):
        for trial in range(self.TRIALS):
            with self.subTest(trial=trial):
                self.assertEqual(self._race(), 1,
                                 "one stable intent must produce exactly one external start")

    def test_the_inter_process_lock_is_load_bearing(self):
        """Mutation: remove only the lock and the duplicate external start comes back."""
        duplicates = max(self._race(disable_flock=True) for _ in range(self.TRIALS))
        self.assertGreater(duplicates, 1,
                           "without flock the race must be able to duplicate the effect; a "
                           "test that still passes here is not testing the lock")

    def test_distinct_intents_in_one_ledger_are_not_serialized_away(self):
        """The lock guards the ledger's critical section, never a run's parallelism."""
        run_ids = [(f"run_p{index}",) for index in range(self.PROCESSES)]
        self.assertEqual(self._race(run_ids=run_ids), self.PROCESSES,
                         "different intents in one ledger must each claim successfully")

    def test_distinct_run_ledgers_claim_in_parallel(self):
        ledgers = [self.root / f"run{index}.json" for index in range(self.PROCESSES)]
        run_ids = [(f"run_l{index}",) for index in range(self.PROCESSES)]
        self.assertEqual(self._race(run_ids=run_ids, ledgers=ledgers), self.PROCESSES)

    def test_a_silently_killed_owner_never_makes_the_observer_wait_forever(self):
        from scripts.deterministic_workflow import runtime_state as rs
        ledger = self.root / "killed.json"
        ready = self.ctx.Event()
        child = self.ctx.Process(target=_child_claim_then_die,
                                 args=(str(ledger), ready, 0.25))
        child.start()
        self.assertTrue(ready.wait(30), "the owner never claimed")
        child.join(30)
        self.assertNotEqual(child.exitcode, 0, "the owner was expected to die uncleanly")

        observer = rs.FileRuntimeStateStore(ledger, owner_id="observer")
        started = time.monotonic()
        # An explicit, finite timeout: the observer returns when the dead owner's lease
        # lapses instead of blocking on a process that will never heartbeat again.
        outcome = observer.observe(_intent()["intent_id"], timeout_seconds=10.0)
        elapsed = time.monotonic() - started
        self.assertIsNone(outcome, "an expired lease must be reported, not waited on")
        self.assertLess(elapsed, 10.0, "the observer must not run to its own deadline")
        taken_over = observer.claim(_intent())
        self.assertEqual(taken_over["claim_outcome"], rs.RESUMED)
        self.assertEqual(taken_over["owner_id"], "observer")

    def test_lock_acquisition_has_an_explicit_timeout(self):
        from scripts.deterministic_workflow import runtime_state as rs
        ledger = self.root / "contended.json"
        acquired, release = self.ctx.Event(), self.ctx.Event()
        holder = self.ctx.Process(target=_child_holds_the_lock,
                                  args=(f"{ledger}.lock", acquired, release))
        holder.start()
        self.addCleanup(holder.join, 30)
        self.addCleanup(release.set)
        self.assertTrue(acquired.wait(30), "the lock holder never started")
        store = rs.FileRuntimeStateStore(ledger, lock_timeout_seconds=0.2)
        with self.assertRaises(rs.RuntimeStateLockTimeout):
            store.claim(_intent())


class OwnershipContractTests(unittest.TestCase):
    """owner_id / lease_token / lease_expires_at / last_heartbeat_at, on an injected clock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.json"

    def stores(self, clock, lease_seconds=30.0):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return (FileRuntimeStateStore(self.path, clock=clock, owner_id="A",
                                      lease_seconds=lease_seconds),
                FileRuntimeStateStore(self.path, clock=clock, owner_id="B",
                                      lease_seconds=lease_seconds))

    def test_a_claim_records_the_full_ownership_contract(self):
        from scripts.deterministic_workflow.runtime_state import CREATED, ManualLeaseClock
        clock = ManualLeaseClock(1000.0)
        first, _ = self.stores(clock)
        record = first.claim(_intent())
        self.assertEqual(record["claim_outcome"], CREATED)
        self.assertEqual(record["owner_id"], "A")
        self.assertTrue(record["lease_token"])
        self.assertEqual(record["lease_expires_at"], 1030.0)
        self.assertEqual(record["last_heartbeat_at"], 1000.0)

    def test_a_live_lease_refuses_the_second_coordinator(self):
        from scripts.deterministic_workflow.runtime_state import (ManualLeaseClock,
                                                                  RuntimeStateLeaseHeld)
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock)
        first.claim(_intent())
        clock.advance(29.0)
        with self.assertRaises(RuntimeStateLeaseHeld):
            second.claim(_intent())

    def test_an_expired_lease_allows_exactly_one_takeover(self):
        from scripts.deterministic_workflow.runtime_state import ManualLeaseClock, RESUMED
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock)
        first.claim(_intent())
        clock.advance(31.0)
        taken = second.claim(_intent())
        self.assertEqual(taken["claim_outcome"], RESUMED)
        self.assertEqual(taken["owner_id"], "B")

    def test_a_heartbeating_owner_keeps_the_other_coordinator_out(self):
        from scripts.deterministic_workflow.runtime_state import (ManualLeaseClock,
                                                                  RuntimeStateLeaseHeld)
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock)
        record = first.claim(_intent())
        for _ in range(5):
            clock.advance(20.0)
            first.heartbeat(record["intent_id"], record["lease_token"])
            with self.assertRaises(RuntimeStateLeaseHeld):
                second.claim(_intent())

    def test_a_stale_lease_token_cannot_renew(self):
        from scripts.deterministic_workflow.runtime_state import (ManualLeaseClock,
                                                                  RuntimeStateLeaseHeld)
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock)
        record = first.claim(_intent())
        clock.advance(31.0)
        second.claim(_intent())          # takeover invalidates A's token
        with self.assertRaises(RuntimeStateLeaseHeld):
            first.heartbeat(record["intent_id"], record["lease_token"])

    def test_release_lets_a_successor_in_without_waiting_out_the_lease(self):
        from scripts.deterministic_workflow.runtime_state import ManualLeaseClock, RESUMED
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock)
        record = first.claim(_intent())
        first.release(record["intent_id"], record["lease_token"])
        self.assertEqual(second.claim(_intent())["claim_outcome"], RESUMED)

    def test_observation_timeout_is_explicit_and_uses_the_injected_clock(self):
        from scripts.deterministic_workflow.runtime_state import (
            ManualLeaseClock, RuntimeStateObservationTimeout)
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock, lease_seconds=10_000.0)
        first.claim(_intent())
        started = time.monotonic()
        with self.assertRaises(RuntimeStateObservationTimeout):
            second.observe(_intent()["intent_id"], timeout_seconds=5.0, poll_seconds=1.0)
        self.assertLess(time.monotonic() - started, 5.0,
                        "the timeout must be measured on the injected clock, not by sleeping")
        self.assertGreaterEqual(clock.time(), 1005.0)

    def test_observation_refuses_a_non_positive_timeout(self):
        from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock)
        first.claim(_intent())
        with self.assertRaises(ValueError):
            second.observe(_intent()["intent_id"], timeout_seconds=0)

    def test_the_ledger_is_re_read_inside_the_lock(self):
        """The claim decision must never come from a snapshot taken before the lock."""
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore

        observed: list[int] = []

        class Recording(FileRuntimeStateStore):
            def _read(self):
                observed.append(self._depth)
                return super()._read()

        Recording(self.path).claim(_intent())
        self.assertTrue(observed, "claim must read the ledger")
        self.assertTrue(all(depth > 0 for depth in observed),
                        "every ledger read during a claim must happen while the lock is held")


class LeaseFencingTests(unittest.TestCase):
    """C2-001c: a superseded owner cannot write the effect it was in the middle of.

    Exclusivity at claim time is only half the contract.  The window this class closes is
    the *returning owner*: A claims, blocks inside a slow external call, its lease expires,
    B takes over and starts its own Task, and A then comes back.  If A can still write, the
    ledger ends up naming A's external effect while B is running a different one -- two
    external effects for one stable intent, arriving through the recovery path rather than
    the race path.  The lease token is therefore a fence on every ownership-sensitive
    transition, not merely a renewal ticket.

    Every test here drives an injected :class:`ManualLeaseClock`; none sleeps.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.json"

    def stores(self, clock, lease_seconds=30.0):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return (FileRuntimeStateStore(self.path, clock=clock, owner_id="A",
                                      lease_seconds=lease_seconds),
                FileRuntimeStateStore(self.path, clock=clock, owner_id="B",
                                      lease_seconds=lease_seconds))

    def taken_over(self, lease_seconds=30.0):
        """A claims, its lease expires, B takes over.  Returns (A, B, A's stale record)."""
        from scripts.deterministic_workflow.runtime_state import ManualLeaseClock, RESUMED
        clock = ManualLeaseClock(1000.0)
        first, second = self.stores(clock, lease_seconds=lease_seconds)
        stale = first.claim(_intent())
        clock.advance(lease_seconds + 1.0)
        fresh = second.claim(_intent())
        self.assertEqual(fresh["claim_outcome"], RESUMED)
        self.assertNotEqual(fresh["lease_token"], stale["lease_token"])
        return first, second, stale, fresh

    @staticmethod
    def _event(intent):
        from scripts.deterministic_workflow.contracts import make_settlement_event
        from scripts.test_deterministic_workflow_round2 import worker_result
        return make_settlement_event(intent, worker_result(artifact_root="run_race"),
                                     occurred_at="2026-01-01T00:00:00Z")

    def test_a_superseded_owner_cannot_record_a_receipt(self):
        from scripts.deterministic_workflow.runtime_state import RuntimeStateLeaseHeld
        first, second, stale, _ = self.taken_over()
        intent = _intent()
        with self.assertRaises(RuntimeStateLeaseHeld):
            first.record_receipt(intent["intent_id"],
                                 {"task_id": "task_FROM_STALE_A"}, stale["lease_token"])
        stored = second.get_receipt(intent["intent_id"])
        self.assertEqual(stored["owner_id"], "B")
        self.assertIsNone(stored["receipt"],
                          "a superseded owner must not name its external effect here")
        self.assertEqual(stored["status"], "CLAIMED")

    def test_a_superseded_owner_cannot_settle(self):
        from scripts.deterministic_workflow.runtime_state import RuntimeStateLeaseHeld
        first, second, stale, _ = self.taken_over()
        intent = _intent()
        with self.assertRaises(RuntimeStateLeaseHeld):
            first.settle(intent["intent_id"], self._event(intent), stale["lease_token"])
        self.assertIsNone(second.get_settlement(intent["intent_id"]))

    def test_the_full_takeover_sequence_refuses_every_stale_write(self):
        """The exact reported reproduction: heartbeat *and* both effect writes are refused."""
        from scripts.deterministic_workflow.runtime_state import RuntimeStateLeaseHeld
        first, second, stale, _ = self.taken_over()
        intent = _intent()
        for label, call in (
            ("heartbeat", lambda: first.heartbeat(intent["intent_id"], stale["lease_token"])),
            ("record_receipt", lambda: first.record_receipt(
                intent["intent_id"], {"task_id": "task_FROM_STALE_A"}, stale["lease_token"])),
            ("settle", lambda: first.settle(
                intent["intent_id"], self._event(intent), stale["lease_token"])),
        ):
            with self.subTest(transition=label), self.assertRaises(RuntimeStateLeaseHeld):
                call()
        record = second.get_receipt(intent["intent_id"])
        self.assertEqual((record["owner_id"], record["receipt"], record["settlement"]),
                         ("B", None, None))

    def test_a_missing_token_is_refused_rather_than_skipping_the_check(self):
        """``lease_token=None`` is a missing capability, never permission to skip the fence."""
        from scripts.deterministic_workflow.runtime_state import RuntimeStateLeaseRequired
        _, second, _, _ = self.taken_over()
        intent = _intent()
        for label, call in (
            ("record_receipt", lambda: second.record_receipt(
                intent["intent_id"], {"task_id": "task_1"}, None)),
            ("settle", lambda: second.settle(intent["intent_id"], self._event(intent), None)),
            ("empty token", lambda: second.record_receipt(
                intent["intent_id"], {"task_id": "task_1"}, "")),
        ):
            with self.subTest(transition=label), self.assertRaises(RuntimeStateLeaseRequired):
                call()

    def test_the_current_owner_writes_normally(self):
        """The positive control: fencing must not break the owner that actually holds it."""
        _, second, _, fresh = self.taken_over()
        intent = _intent()
        second.record_receipt(intent["intent_id"], {"task_id": "task_from_B"},
                              fresh["lease_token"])
        second.settle(intent["intent_id"], self._event(intent), fresh["lease_token"])
        record = second.get_receipt(intent["intent_id"])
        self.assertEqual(record["receipt"], {"task_id": "task_from_B"})
        self.assertEqual(record["status"], "SETTLED")

    def test_the_effect_write_fence_is_load_bearing(self):
        """Mutation: restore the old optional check and the stale write lands again."""
        from scripts.deterministic_workflow import runtime_state as rs

        def unfenced(self, records, intent_id, lease_token):
            record = records.get(intent_id)
            if record is None:
                raise rs.RuntimeStateConflict(f"UNCLAIMED_INTENT:{intent_id}")
            if lease_token is not None and record["lease_token"] != lease_token:
                raise rs.RuntimeStateLeaseHeld(f"LEASE_LOST:{intent_id}")
            return record

        first, second, stale, _ = self.taken_over()
        intent = _intent()
        with patch.object(rs._RuntimeStateStore, "_fenced", unfenced):
            first.record_receipt(intent["intent_id"], {"task_id": "task_FROM_STALE_A"}, None)
        self.assertEqual(second.get_receipt(intent["intent_id"])["receipt"],
                         {"task_id": "task_FROM_STALE_A"},
                         "without the fence the superseded owner writes its own effect, "
                         "which is the defect this class exists to prevent")


@REQUIRES_LANGGRAPH
class LeaseFencingInProductionCallersTests(unittest.TestCase):
    """The fence is only real if the production call sites actually carry the token.

    A guard nobody invokes is dead code, which is how the optional ``lease_token`` argument
    passed review while every caller omitted it.  These tests therefore drive the *executor
    and adapters*, not the store, and prove the token minted by ``claim`` reaches each write.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.json"

    def store(self, clock=None, owner_id=None, lease_seconds=30.0):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.path, clock=clock, owner_id=owner_id,
                                     lease_seconds=lease_seconds)

    def prepared(self, run_id="run_fence"):
        from scripts.deterministic_workflow.contracts import make_intent
        state = initial_state(run_id=run_id, thread_id="t", phases=("ANALYSIS",),
                              capabilities=BASE_CAPABILITIES)
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED")
        return state, intent

    def test_the_executor_hands_the_claim_token_to_the_adapter(self):
        from copy import deepcopy
        from scripts.deterministic_workflow.executor import execute_intent_node
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.test_deterministic_workflow_round2 import worker_result

        seen: list[str | None] = []

        class Recording(FakeAdapter):
            def start(self, intent, *, lease_token=None):
                seen.append(lease_token)
                return super().start(intent, lease_token=lease_token)

        state, intent = self.prepared()
        store = self.store()
        adapter = Recording([worker_result(artifact_root="run_fence")], runtime_state=store)
        execute_intent_node(adapter, runtime_state=store)(deepcopy(state))
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0], "the executor must pass the token claim() minted")
        self.assertEqual(seen[0], store.get_receipt(intent["intent_id"])["lease_token"])

    def test_a_slow_orca_start_cannot_land_its_task_after_takeover(self):
        """A blocks inside ``create_task``, B takes over, A returns -- and is refused.

        This is the end-to-end shape of the finding: the duplicate would arrive through the
        recovery path, written by a process that was legitimately the owner when it started.
        The adapter is driven directly so the assertion is about the write A attempts on its
        way out, not about whatever the executor decides to do afterwards.
        """
        from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
        from scripts.deterministic_workflow.runtime_state import (
            ManualLeaseClock, RESUMED, RuntimeStateLeaseHeld, RuntimeStateLeaseRequired)

        clock = ManualLeaseClock(1000.0)
        _, intent = self.prepared(run_id="run_slowstart")
        first = self.store(clock=clock, owner_id="A")
        second = self.store(clock=clock, owner_id="B")
        taken: list[dict] = []

        class SlowHarness:
            """``create_task`` succeeds -- but only after A's lease has already lapsed."""

            run_id = "run_slowstart"

            def create_task(self, spec, *, deps=()):
                clock.advance(31.0)                        # A stalls; its lease expires
                taken.append(second.claim(intent))         # B legitimately takes over
                return "task_FROM_STALE_A"

            def run_existing_task(self, *args, **kwargs):  # pragma: no cover - never reached
                raise AssertionError("A must be fenced before it dispatches")

        claimed_a = first.claim(intent)
        adapter = OrcaAdapter(SlowHarness(), runtime_state=first)
        with self.assertRaises(RuntimeStateLeaseHeld) as caught:
            adapter.start(intent, lease_token=claimed_a["lease_token"])
        # Specifically a *lost* lease: A did present its token and the fence rejected it.
        # Accepting a bare "no token supplied" refusal here would let a build that stopped
        # propagating the token pass this test for the wrong reason.
        self.assertNotIsInstance(caught.exception, RuntimeStateLeaseRequired)
        self.assertIn("LEASE_LOST", str(caught.exception))
        self.assertIn("owner=B", str(caught.exception))
        self.assertEqual(taken[0]["claim_outcome"], RESUMED)
        record = second.get_receipt(intent["intent_id"])
        self.assertEqual(record["owner_id"], "B")
        self.assertIsNone(record["receipt"],
                          "A's Task id must never reach the record B now owns")


class DurableLedgerValidationTests(unittest.TestCase):
    """C2-001b: a malformed or incompatible ledger is never read as an empty one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.json"

    def store(self, **kwargs):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.path, **kwargs)

    def valid_record(self, **overrides):
        intent = _intent()
        record = {
            "intent_id": intent["intent_id"], "command_id": intent["command_id"],
            "payload_digest": intent["payload_digest"], "run_id": intent["run_id"],
            "phase": intent["phase"], "role": intent["role"],
            "round_kind": intent["round_kind"], "status": "CLAIMED",
            "receipt": None, "settlement": None, "owner_id": "A",
            "lease_token": "t0", "lease_expires_at": 0.0, "last_heartbeat_at": 0.0,
        }
        record.update(overrides)
        return record

    def valid_settlement(self, **overrides):
        """A settlement carrying the full closed vocabulary, so identity is what varies."""
        from scripts.deterministic_workflow.contracts import make_settlement_event
        event = dict(make_settlement_event(_intent(), {"status": "COMPLETE"},
                                           occurred_at="2026-01-01T00:00:01Z"))
        event.update(overrides)
        return event

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def assert_refused(self, payload, fragment):
        from scripts.deterministic_workflow.runtime_state import RuntimeStateCorrupt
        self.write(payload)
        with self.assertRaises(RuntimeStateCorrupt) as caught:
            self.store().claim(_intent())
        self.assertIn(fragment, str(caught.exception))

    def test_missing_schema_version_is_not_an_empty_ledger(self):
        self.assert_refused({"records": {}}, "schema_version missing")

    def test_incompatible_schema_version_is_refused(self):
        self.assert_refused({"schema_version": "os40.runtime_state.v1", "records": {}},
                            "INCOMPATIBLE_RUNTIME_STATE")

    def test_non_string_schema_version_is_refused(self):
        self.assert_refused({"schema_version": 1, "records": {}}, "schema_version missing")

    def test_top_level_container_of_the_wrong_type_is_refused(self):
        self.assert_refused([1, 2, 3], "top-level container")

    def test_unknown_top_level_key_is_refused(self):
        self.assert_refused({"schema_version": SCHEMA, "records": {}, "extra": 1},
                            "unknown top-level keys")

    def test_record_container_of_the_wrong_type_is_refused(self):
        self.assert_refused({"schema_version": SCHEMA, "records": [1, 2, 3]},
                            "records container")

    def test_record_of_the_wrong_type_is_refused(self):
        self.assert_refused({"schema_version": SCHEMA, "records": {"i": "nope"}},
                            "record container")

    def test_unknown_record_key_is_refused(self):
        record = self.valid_record(surprise=1)
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "unknown keys")

    def test_missing_record_key_is_refused(self):
        record = self.valid_record()
        record.pop("lease_token")
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "missing keys")

    def test_unknown_status_is_refused(self):
        record = self.valid_record(status="NONSENSE")
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "unknown status")

    def test_conflicting_record_identity_is_refused(self):
        record = self.valid_record()
        self.assert_refused({"schema_version": SCHEMA, "records": {"another_key": record}},
                            "identity mismatch")

    def test_malformed_receipt_shape_is_refused(self):
        record = self.valid_record(status="EFFECTED", receipt="task_1")
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "receipt shape")

    def test_effected_without_a_receipt_is_refused(self):
        record = self.valid_record(status="EFFECTED")
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "EFFECTED content")

    def test_settled_without_a_settlement_is_refused(self):
        record = self.valid_record(status="SETTLED")
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "SETTLED without settlement")

    def test_settlement_bound_to_another_intent_is_refused(self):
        record = self.valid_record(
            status="SETTLED", receipt={"task_id": "t"},
            settlement=self.valid_settlement(intent_id="someone_else"))
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "settlement identity")

    def test_settlement_bound_to_another_command_is_refused(self):
        record = self.valid_record()
        record.update(status="SETTLED", receipt={"task_id": "t"},
                      settlement=self.valid_settlement(intent_id=record["intent_id"],
                                                       command_id="cmd_other"))
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "settlement command")

    def test_lease_field_of_the_wrong_type_is_refused(self):
        record = self.valid_record(lease_expires_at="soon")
        self.assert_refused({"schema_version": SCHEMA, "records": {record["intent_id"]: record}},
                            "lease_expires_at type")

    def test_unparseable_json_is_refused(self):
        from scripts.deterministic_workflow.runtime_state import RuntimeStateCorrupt
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(RuntimeStateCorrupt):
            self.store().claim(_intent())

    def test_a_corrupt_ledger_fails_closed_before_any_external_effect(self):
        from scripts.deterministic_workflow.executor import execute_intent_node
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.runtime_state import RuntimeStateCorrupt
        self.write({"schema_version": "os40.runtime_state.v1", "records": {}})
        state = dict(initial_state(run_id="run_race", thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        state.update(pending_intent=_intent(), intent_status="PREPARED")
        store = self.store()
        adapter = FakeAdapter([{"status": "COMPLETE", "unit_test_status": "PASS"}],
                              runtime_state=store)
        with self.assertRaises(RuntimeStateCorrupt):
            execute_intent_node(adapter, runtime_state=store)(state)
        self.assertEqual(adapter.effect_count, 0,
                         "a corrupt ledger must fail closed BEFORE the external effect")

    def test_ledger_validation_is_load_bearing(self):
        """Mutation: restore the old permissive read and the corrupt ledger looks empty."""
        from scripts.deterministic_workflow import runtime_state as rs
        self.write({"schema_version": "os40.runtime_state.v1",
                    "records": {"i": {"status": "NONSENSE"}}})
        with patch.object(rs, "validate_ledger",
                          lambda raw: raw.get("records") if isinstance(raw.get("records"), dict) else {}), \
                patch.object(rs, "validate_record", lambda intent_id, record: record):
            record = self.store().claim(_intent())
        self.assertEqual(record["claim_outcome"], rs.CREATED,
                         "without validation the corrupt ledger is treated as 'no prior "
                         "claim' -- which is precisely the finding")

    @REQUIRES_LANGGRAPH
    def test_launcher_reports_an_incompatible_ledger_as_blocked(self):
        from scripts.deterministic_workflow import launcher
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        self.write({"schema_version": "os40.runtime_state.v1", "records": {}})
        store = self.store()
        state = launcher.build_state({"run_id": "run_block", "thread_id": "t",
                                      "phases": ["ANALYSIS"]})
        final = launcher.execute_state(state, adapter=FakeAdapter([], runtime_state=store),
                                       runtime_state=store,
                                       checkpoint_store_path=self.path.parent / "cp.json")
        self.assertEqual(final["terminal_status"], "BLOCKED")
        self.assertEqual(final["terminal_reason"]["code"], "INCOMPATIBLE_RUNTIME_STATE")
        self.assertEqual(launcher.EXIT_CODES[final["terminal_status"]], 1)


class CrashSafeWriteTests(unittest.TestCase):
    """Separate from concurrency: an interrupted write must not damage the ledger."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.json"

    def store(self):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.path)

    def test_a_failed_write_leaves_the_previous_ledger_and_no_debris(self):
        store = self.store()
        store.claim(_intent())
        before = self.path.read_text(encoding="utf-8")
        with patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.claim(_intent("run_other"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before,
                         "an interrupted write must not corrupt the committed ledger")
        debris = [p.name for p in self.path.parent.iterdir()
                  if p.name.startswith(f".{self.path.name}.")]
        self.assertEqual(debris, [], "a failed write must not leave temporary files behind")

    def test_the_committed_ledger_is_always_readable_after_a_write(self):
        store = self.store()
        claimed = store.claim(_intent())
        store.record_receipt(_intent()["intent_id"], {"task_id": "task_1"},
                             claimed["lease_token"])
        reread = self.store().get_receipt(_intent()["intent_id"])
        self.assertEqual(reread["status"], "EFFECTED")
        self.assertEqual(reread["receipt"], {"task_id": "task_1"})

    def test_a_runtime_handle_never_reaches_the_ledger(self):
        from scripts.deterministic_workflow.runtime_state import RuntimeStateConflict
        store = self.store()
        claimed = store.claim(_intent())
        with self.assertRaises(RuntimeStateConflict):
            store.record_receipt(_intent()["intent_id"], {"terminal_handle": "term_x"},
                                 claimed["lease_token"])


class LedgerRecordIntegrityTests(unittest.TestCase):
    """C2-001 (correction round): a stored record is closed, and it must be *this* intent's.

    Two layers, because neither implies the other:

    * ``validate_record`` proves a record is internally coherent -- closed receipt and
      settlement vocabularies, and an ``EFFECTED`` record that actually names the external
      effect it claims exists.  It runs on every ledger read, so it fires before any
      external effect can be attempted.
    * ``claim`` re-checks the *stored* identity against the intent now asking for it.
      ``validate_record`` cannot: it never sees the intent.  A digest-only comparison left
      ``run_id``/``phase``/``role``/``round_kind``/``command_id`` forgeable, which would hand
      an intent another intent's external effect.

    Each case below is written against the ledger a previous process left on disk, since
    that is the only surface an attacker or a corrupted write can reach.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.json"

    def store(self, **kwargs):
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        return FileRuntimeStateStore(self.path, **kwargs)

    def effected_record(self, **overrides):
        """A wholly valid ``EFFECTED`` record, so every refusal below is attributable."""
        intent = _intent()
        record = {
            "intent_id": intent["intent_id"], "command_id": intent["command_id"],
            "payload_digest": intent["payload_digest"], "run_id": intent["run_id"],
            "phase": intent["phase"], "role": intent["role"],
            "round_kind": intent["round_kind"], "status": "EFFECTED",
            "receipt": {"task_id": "task_1"}, "settlement": None,
            "owner_id": "coordinator_A", "lease_token": "tok",
            "lease_expires_at": 0.0, "last_heartbeat_at": 0.0,
        }
        record.update(overrides)
        return record

    def write_record(self, record):
        self.path.write_text(
            json.dumps({"schema_version": SCHEMA,
                        "records": {_intent()["intent_id"]: record}}), encoding="utf-8")

    def claim(self):
        return self.store().claim(_intent())

    def assert_refused(self, record, fragment, exc_name="RuntimeStateCorrupt"):
        from scripts.deterministic_workflow import runtime_state as rs
        self.write_record(record)
        with self.assertRaises(getattr(rs, exc_name)) as caught:
            self.claim()
        self.assertIn(fragment, str(caught.exception))

    # ---- the fixture itself must be accepted, or nothing below proves anything ----

    def test_the_untampered_effected_record_is_resumed(self):
        from scripts.deterministic_workflow.runtime_state import RESUMED
        self.write_record(self.effected_record())
        self.assertEqual(self.claim()["claim_outcome"], RESUMED)

    # ---- closed receipt contract ----

    def test_an_effected_record_with_an_empty_receipt_is_refused(self):
        """The strand: EFFECTED says the effect exists, {} names nothing to reconcile."""
        self.assert_refused(self.effected_record(receipt={}), "receipt external identity")

    def test_a_settled_record_whose_receipt_names_no_effect_is_refused(self):
        settlement = _settlement_for(_intent())
        self.assert_refused(
            self.effected_record(status="SETTLED", receipt={"intent_id": _intent()["intent_id"]},
                                 settlement=settlement),
            "receipt external identity")

    def test_an_unknown_receipt_key_is_refused(self):
        self.assert_refused(
            self.effected_record(receipt={"task_id": "task_1", "smuggled": "x"}),
            "receipt unknown keys ['smuggled']")

    def test_a_non_string_receipt_value_is_refused(self):
        self.assert_refused(self.effected_record(receipt={"task_id": 17}), "receipt task_id type")

    def test_an_empty_string_receipt_identifier_is_refused(self):
        self.assert_refused(self.effected_record(receipt={"task_id": ""}), "receipt task_id type")

    def test_a_receipt_bound_to_another_intent_is_refused(self):
        self.assert_refused(
            self.effected_record(receipt={"task_id": "task_1", "intent_id": "intent_other"}),
            "receipt identity")

    # ---- closed settlement contract ----

    def test_a_truncated_settlement_is_refused(self):
        record = self.effected_record(status="SETTLED",
                                      settlement={"intent_id": _intent()["intent_id"]})
        self.assert_refused(record, "settlement fields")

    def test_an_unknown_settlement_key_is_refused(self):
        settlement = dict(_settlement_for(_intent()), smuggled="x")
        self.assert_refused(self.effected_record(status="SETTLED", settlement=settlement),
                            "settlement fields unknown=['smuggled']")

    def test_a_settlement_result_of_the_wrong_type_is_refused(self):
        settlement = dict(_settlement_for(_intent()), result="COMPLETE")
        self.assert_refused(self.effected_record(status="SETTLED", settlement=settlement),
                            "settlement result type")

    def test_a_non_string_settlement_field_is_refused(self):
        settlement = dict(_settlement_for(_intent()), occurred_at=0)
        self.assert_refused(self.effected_record(status="SETTLED", settlement=settlement),
                            "settlement occurred_at type")

    # ---- stored identity is checked against the intent that asks for it ----

    def test_a_conflicting_stored_run_id_is_refused(self):
        self.assert_refused(self.effected_record(run_id="run_other"),
                            "IDEMPOTENCY_CONFLICT", "RuntimeStateConflict")

    def test_a_conflicting_stored_phase_is_refused(self):
        self.assert_refused(self.effected_record(phase="IMPLEMENTATION"),
                            "IDEMPOTENCY_CONFLICT", "RuntimeStateConflict")

    def test_a_conflicting_stored_role_is_refused(self):
        self.assert_refused(self.effected_record(role="PHASE_REVIEWER"),
                            "IDEMPOTENCY_CONFLICT", "RuntimeStateConflict")

    def test_a_conflicting_stored_round_kind_is_refused(self):
        self.assert_refused(self.effected_record(round_kind="FINAL_REVIEW"),
                            "IDEMPOTENCY_CONFLICT", "RuntimeStateConflict")

    def test_a_conflicting_stored_command_id_is_refused(self):
        self.assert_refused(self.effected_record(command_id="cmd_other"),
                            "IDEMPOTENCY_CONFLICT", "RuntimeStateConflict")

    def test_a_conflicting_stored_payload_digest_is_refused(self):
        self.assert_refused(self.effected_record(payload_digest="tampered"),
                            "IDEMPOTENCY_CONFLICT", "RuntimeStateConflict")

    def test_every_identity_field_is_covered(self):
        """The identity set is the contract; a silently shrunk set must fail here."""
        from scripts.deterministic_workflow.runtime_state import IDENTITY_KEYS
        self.assertEqual(sorted(IDENTITY_KEYS),
                         ["command_id", "payload_digest", "phase", "role", "round_kind", "run_id"])

    # ---- the refusal happens before any external effect ----

    def test_a_tampered_record_fails_closed_before_any_external_effect(self):
        from scripts.deterministic_workflow.executor import execute_intent_node
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.runtime_state import RuntimeStateConflict
        for label, record in (("empty receipt", self.effected_record(receipt={})),
                              ("forged run_id", self.effected_record(run_id="run_other"))):
            with self.subTest(label):
                self.write_record(record)
                state = dict(initial_state(run_id="run_race", thread_id="t",
                                           phases=("ANALYSIS",),
                                           capabilities=BASE_CAPABILITIES))
                state.update(pending_intent=_intent(), intent_status="PREPARED")
                store = self.store()
                adapter = FakeAdapter([{"status": "COMPLETE", "unit_test_status": "PASS"}],
                                      runtime_state=store)
                with self.assertRaises(RuntimeStateConflict):
                    execute_intent_node(adapter, runtime_state=store)(state)
                self.assertEqual(adapter.effect_count, 0,
                                 "a tampered ledger must fail closed BEFORE the effect")

    # ---- mutation: each new guard is load-bearing ----

    def test_the_closed_receipt_contract_is_load_bearing(self):
        """Disable the receipt validator and both receipt defects come straight back."""
        from scripts.deterministic_workflow import runtime_state as rs
        for receipt in ({}, {"task_id": "task_1", "smuggled": "x"}):
            with self.subTest(receipt=receipt):
                self.write_record(self.effected_record(receipt=receipt))
                with patch.object(rs, "_validate_receipt", lambda *a, **k: None):
                    self.assertEqual(self.claim()["claim_outcome"], rs.RESUMED)

    def test_the_closed_settlement_contract_is_load_bearing(self):
        from scripts.deterministic_workflow import runtime_state as rs
        self.write_record(self.effected_record(
            status="SETTLED", settlement={"intent_id": _intent()["intent_id"],
                                          "command_id": _intent()["command_id"]}))
        with patch.object(rs, "_validate_settlement", lambda *a, **k: None):
            self.assertEqual(self.claim()["claim_outcome"], rs.ALREADY_SETTLED)

    def test_the_stored_identity_check_is_load_bearing(self):
        """Shrink the identity set back to the digest and every forgery is accepted again."""
        from scripts.deterministic_workflow import runtime_state as rs
        for field, value in (("run_id", "run_other"), ("phase", "IMPLEMENTATION"),
                             ("role", "PHASE_REVIEWER"), ("round_kind", "FINAL_REVIEW"),
                             ("command_id", "cmd_other")):
            with self.subTest(field=field):
                self.write_record(self.effected_record(**{field: value}))
                with patch.object(rs, "IDENTITY_KEYS", ("payload_digest",)):
                    self.assertEqual(self.claim()["claim_outcome"], rs.RESUMED)


def _settlement_for(intent):
    from scripts.deterministic_workflow.contracts import make_settlement_event
    return dict(make_settlement_event(intent, {"status": "COMPLETE", "unit_test_status": "PASS"},
                                      occurred_at="2026-01-01T00:00:01Z"))

if __name__ == "__main__":
    unittest.main()
