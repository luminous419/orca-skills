"""External review round 3: a healthy long-running owner must renew its own lease.

The defect: ``_execute_recoverable`` claimed a 60-second lease and then blocked inside
``adapter.start()`` for as long as the external agent took -- 5 to 15 minutes for a real
Claude/Codex dispatch -- without ever calling ``heartbeat``.  A perfectly healthy Coordinator
became indistinguishable from a dead one: its lease lapsed, a second Coordinator took over
and rotated the token, and the round 2 fence then refused the healthy owner's own receipt and
settlement.  Fencing was working; renewal was missing.

The six scenarios the fix must prove, all automated below:

1. A runs external work that outlasts one lease period;
2. B claims the same intent while that work is in flight;
3. while A's heartbeat holds, B neither takes over nor creates a second external effect;
4. A alone records the receipt and the settlement;
5. when A dies and the beats stop, the lease lapses and B's existing recovery path runs;
6. a rotated token or a failed renewal fails closed: A's later writes are refused.

Nothing here sleeps its way to an assertion.  Waits are on synchronisation primitives with
generous timeouts *as upper bounds*, never as a way of letting time pass; every scenario that
turns on the lease clock -- the healthy owner outlasting it, the takeover after the beats
stop, the un-renewed mutation -- runs on a :class:`ManualLeaseClock` that moves only when the
test moves it, and every observer poll is granted by the test rather than by the scheduler.  ``*_is_load_bearing`` mutation tests disable the keeper
and assert the defect returns, so deleting it cannot leave this suite green.
"""
from __future__ import annotations

import importlib.metadata
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.deterministic_workflow import runtime_state as rs
from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES,
                                                      EXTERNAL_LOOKUP,
                                                      EXTERNAL_RESUME, make_intent)
from scripts.deterministic_workflow import executor as executor_module
from scripts.deterministic_workflow.executor import (IdempotencyRecoveryError,
                                                     execute_intent_node)
from scripts.deterministic_workflow.fake_adapter import FakeAdapter, FileExternalWorld
from scripts.deterministic_workflow.lease_keeper import (DEFAULT_KEEPER_JOIN_SECONDS,
                                                         LeaseKeeper, LeaseKeeperNotStopped,
                                                         LeaseRenewalFailed, _default_waiter,
                                                         heartbeat_interval_for,
                                                         lease_keeper_factory)
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

# The production default.  Nothing here waits for a lease to expire in real seconds: every
# scenario that turns on time runs on a ``ManualLeaseClock`` that only the test advances.
LEASE_SECONDS = rs.DEFAULT_LEASE_SECONDS
# The renewal period the production factory derives from that lease -- the test advances the
# injected clock by exactly this much per beat, so the two can never drift apart.
BEAT_SECONDS = heartbeat_interval_for(LEASE_SECONDS)
# An upper bound on every blocking wait below, so a broken build fails instead of hanging.
# It is a limit, never a pause: nothing in this file reaches it on a passing run.
JOIN_TIMEOUT = 30.0
WORKER_RESULT = {"status": "COMPLETE", "unit_test_status": "PASS"}


class BlockingAdapter(FakeAdapter):
    """``start()`` blocks until the test releases it, exactly like a long external dispatch."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = threading.Event()
        self.release = threading.Event()

    def start(self, intent, *, lease_token=None):
        self.started.set()
        if not self.release.wait(JOIN_TIMEOUT):     # pragma: no cover - only on a wedged test
            raise AssertionError("the test never released the blocking external call")
        return super().start(intent, lease_token=lease_token)


class BeatPacer:
    """A keeper waiter the test drives one beat at a time -- no wall-clock pacing at all.

    ``request_beat`` releases exactly one renewal and blocks until it has landed, so a test
    can say "three more heartbeats happened" as a fact rather than as a hopeful sleep.
    """

    def __init__(self, clock=None, advance_by=0.0):
        self._condition = threading.Condition()
        self._pending = 0
        self._served = 0
        self._cancelled = False
        self._clock = clock
        self._advance_by = float(advance_by)

    def __call__(self, stop: threading.Event, interval: float) -> bool:
        with self._condition:
            self._condition.wait_for(lambda: self._pending > 0 or self._cancelled,
                                     timeout=JOIN_TIMEOUT)
            if self._cancelled or stop.is_set():
                return True
            self._pending -= 1
            self._served += 1
            self._condition.notify_all()
        if self._clock is not None and self._advance_by:
            # The beat *is* the passage of time: advancing here models a keeper renewing
            # every interval while the injected lease clock runs forward.
            self._clock.advance(self._advance_by)
        return False

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def release_beat(self) -> None:
        """Let exactly one renewal through without waiting for it to land."""
        with self._condition:
            self._pending += 1
            self._condition.notify_all()

    def request_beat(self, keeper: LeaseKeeper) -> None:
        before = keeper.beats
        self.release_beat()
        if not keeper.wait_for_beats(before + 1, timeout=JOIN_TIMEOUT):
            raise AssertionError("the lease keeper did not complete the requested heartbeat")


class CapturingFactory:
    """Wraps a keeper factory so the test can drive and inspect the keeper the node built."""

    def __init__(self, inner):
        self._inner = inner
        self.created: list = []
        self.ready = threading.Event()

    def __call__(self, runtime_state, intent_id, lease_token):
        keeper = self._inner(runtime_state, intent_id, lease_token)
        self.created.append(keeper)
        self.ready.set()
        return keeper

    def keeper(self) -> LeaseKeeper:
        if not self.ready.wait(JOIN_TIMEOUT):      # pragma: no cover - only on a wedged test
            raise AssertionError("the executor never created a lease keeper")
        return self.created[-1]


class CountingLedger:
    """A ledger that only records how many renewals were written to it."""

    lease_seconds = LEASE_SECONDS

    def __init__(self):
        self.calls = 0

    def heartbeat(self, intent_id, lease_token):
        self.calls += 1
        return {"intent_id": intent_id}


class LateBeatWaiter:
    """Hands the beat loop a due beat *after* ``stop()`` has already run.

    This is the one window the pre-write re-check exists for: a renewal period can elapse in
    the instant between ``stop()`` setting its flags and the loop reading them.  The waiter
    deliberately exposes no ``cancel()`` and ignores the stop event, so the race is a fact of
    the test rather than a scheduling accident.
    """

    def __init__(self):
        self.parked = threading.Event()
        self.go = threading.Event()
        self.served = 0

    def __call__(self, stop: threading.Event, interval: float) -> bool:
        if self.served:
            return True
        self.served += 1
        self.parked.set()
        if not self.go.wait(JOIN_TIMEOUT):          # pragma: no cover - only on a wedged test
            raise AssertionError("the test never released the late beat")
        return False                                # "one interval has elapsed"


class PacedObserverClock:
    """B's view of the shared manual clock: it may read time, but never make time pass.

    ``observe()`` sleeps between polls and a ``ManualLeaseClock``'s sleep *advances* the
    clock, so an observer thread left to itself would silently push time forward and expire
    the owner's lease -- turning the whole scenario back into a race with the scheduler.
    Here the test grants each poll instead.  ``release()`` hands the observer back its normal
    behaviour (sleeps advance the shared clock again) and is only ever called once the owner
    has finished, so nothing that matters depends on when a thread happens to run.
    """

    def __init__(self, clock):
        self._clock = clock
        self._condition = threading.Condition()
        self._waiting = 0
        self._released = False

    def time(self) -> float:
        return self._clock.time()

    def sleep(self, seconds: float) -> None:
        with self._condition:
            self._waiting += 1
            self._condition.notify_all()
            granted = self._condition.wait_for(lambda: self._released, timeout=JOIN_TIMEOUT)
            self._waiting -= 1
            self._condition.notify_all()
        if not granted:                            # pragma: no cover - only on a wedged test
            raise AssertionError("the observer was never released")
        self._clock.advance(float(seconds))

    def wait_until_parked(self) -> None:
        """Block until the observer is provably waiting between two polls."""
        with self._condition:
            if not self._condition.wait_for(lambda: self._waiting > 0, timeout=JOIN_TIMEOUT):
                raise AssertionError("the observer never reached its polling wait")

    def release(self) -> None:
        with self._condition:
            self._released = True
            self._condition.notify_all()


class WedgedLedger:
    """A ledger whose ``heartbeat`` blocks until the test lets it finish.

    This is the shape that makes cleanup hard: the beat thread is not parked in the waiter
    (where ``cancel()`` reaches it) but inside the renewal write itself, so ``stop()`` cannot
    make it exit and must say so.
    """

    lease_seconds = LEASE_SECONDS

    def __init__(self):
        self.entered = threading.Event()
        self.finish = threading.Event()
        self.calls = 0

    def heartbeat(self, intent_id, lease_token):
        self.calls += 1
        self.entered.set()
        if not self.finish.wait(JOIN_TIMEOUT):      # pragma: no cover - only on a wedged test
            raise AssertionError("the test never released the wedged renewal")
        return {"intent_id": intent_id}


class NullKeeper:
    """Mutation: the pre-fix behaviour -- a claim nobody ever renews."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_if_lost(self) -> None:
        return None


def null_keeper_factory(runtime_state, intent_id, lease_token):
    return NullKeeper()


class _LeaseFixture(unittest.TestCase):
    """Two Coordinators (distinct owner ids), one ledger file, one external world."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = self.root / "ledger.json"
        self.world = FileExternalWorld(self.root / "world.json")
        self.threads: list[threading.Thread] = []
        self.addCleanup(self._join_threads)

    def _join_threads(self):
        for thread in self.threads:
            thread.join(JOIN_TIMEOUT)
            self.assertFalse(thread.is_alive(), "a worker thread outlived its test")

    def store(self, owner_id, *, clock=None, lease_seconds=LEASE_SECONDS):
        return rs.FileRuntimeStateStore(self.ledger, clock=clock, owner_id=owner_id,
                                        lease_seconds=lease_seconds)

    def prepared_state(self, run_id="run_lease"):
        state = dict(initial_state(run_id=run_id, thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        state.update(pending_intent=intent, intent_status="PREPARED", pending_role="WORKER")
        return state, intent

    def record(self, intent_id):
        return self.store("reader")._read().get(intent_id)

    def run_async(self, name, target):
        """Run one node call on its own thread, capturing its outcome for later assertions."""
        outcome: dict = {}

        def body():
            try:
                outcome["event"] = target()["pending_event"]
            except BaseException as exc:           # noqa: BLE001 - reported by the assertions
                outcome["error"] = exc

        thread = threading.Thread(target=body, name=name, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, outcome


# =======================================================================================
# The keeper itself
# =======================================================================================

class LeaseKeeperUnitTests(unittest.TestCase):
    """The renewal period, the fail-closed rule, and cleanup on every exit path."""

    def test_the_renewal_period_is_derived_from_the_lease_and_is_much_shorter(self):
        for lease in (60.0, 30.0, 5.0, 0.6):
            with self.subTest(lease=lease):
                interval = heartbeat_interval_for(lease)
                self.assertLess(interval, lease / 2,
                                "a renewal that is not comfortably shorter than the lease "
                                "cannot keep a healthy claim alive")
                self.assertAlmostEqual(interval, lease / 3.0)
        self.assertAlmostEqual(heartbeat_interval_for(60.0), 20.0)
        # A nonsensical configuration falls back to the documented default rather than to a
        # zero-second busy loop or a period longer than the lease.
        for bogus in (0.0, -1.0, None, "sixty"):
            self.assertAlmostEqual(heartbeat_interval_for(bogus),
                                   rs.DEFAULT_LEASE_SECONDS / 3.0)

    def test_the_keeper_renews_the_lease_and_stops_cleanly(self):
        store = rs.InMemoryRuntimeStateStore(clock=rs.ManualLeaseClock(), owner_id="A",
                                             lease_seconds=60.0)
        state = dict(initial_state(run_id="run_k", thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        record = store.claim(intent)
        pacer = BeatPacer(clock=store.clock, advance_by=20.0)
        keeper = LeaseKeeper(store, intent["intent_id"], record["lease_token"],
                             interval_seconds=20.0, waiter=pacer)
        with keeper:
            for _ in range(9):                     # 180s of lease clock on a 60s lease
                pacer.request_beat(keeper)
            self.assertEqual(keeper.beats, 9)
            self.assertIsNone(keeper.failure)
            live = store.get_receipt(intent["intent_id"])
            self.assertGreater(live["lease_expires_at"], store.clock.time(),
                               "the lease must still be in the future after 3 lease periods")
        self.assertFalse(any(thread.name.startswith("lease-keeper")
                             for thread in threading.enumerate()),
                         "the beat thread must not outlive the block it was renewing for")

    def test_a_failed_renewal_stops_the_keeper_and_is_raised_at_the_checkpoint(self):
        class Rejecting:
            lease_seconds = 60.0
            calls = 0

            def heartbeat(self, intent_id, lease_token):
                Rejecting.calls += 1
                raise rs.RuntimeStateLeaseHeld(f"LEASE_LOST:{intent_id}")

        pacer = BeatPacer()
        keeper = LeaseKeeper(Rejecting(), "intent_x", "token", interval_seconds=0.01,
                             waiter=pacer)
        # Exit is itself a checkpoint (F-ADV-01): a recorded renewal failure is re-raised
        # there too, so a block that never happens to take one of its own cannot end clean.
        with self.assertRaises(LeaseRenewalFailed):
            with keeper:
                pacer.release_beat()               # release one beat; it will fail
                self.assertTrue(keeper.wait_for_beats(1, timeout=JOIN_TIMEOUT))
                self.assertTrue(keeper.lost)
                with self.assertRaises(LeaseRenewalFailed):
                    keeper.raise_if_lost()
        self.assertEqual(Rejecting.calls, 1,
                         "a failed renewal must not be retried into a silent takeover")

    def test_the_keeper_is_stopped_when_the_wrapped_block_raises(self):
        store = rs.InMemoryRuntimeStateStore(clock=rs.ManualLeaseClock(), owner_id="A")
        state = dict(initial_state(run_id="run_k2", thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        record = store.claim(intent)
        pacer = BeatPacer()
        keeper = LeaseKeeper(store, intent["intent_id"], record["lease_token"],
                             interval_seconds=0.01, waiter=pacer)
        with self.assertRaises(ZeroDivisionError):
            with keeper:
                raise ZeroDivisionError("the external call blew up")
        self.assertFalse(any(thread.name.startswith("lease-keeper")
                             for thread in threading.enumerate()),
                         "an exception path must still release the keeper thread")

    def test_the_beat_thread_is_a_daemon_so_it_cannot_block_process_exit(self):
        keeper = LeaseKeeper(object(), "intent_x", "token", interval_seconds=DEFAULT_KEEPER_JOIN_SECONDS,
                             waiter=BeatPacer())
        with keeper:
            self.assertTrue(keeper._thread.daemon)


# =======================================================================================
# Shutdown: every exit path must actually release the beat thread, or say that it did not
# =======================================================================================

class KeeperShutdownTests(unittest.TestCase):
    """``stop()`` is the mirror image of renewal, and it fails closed the same way.

    Renewal that stops too early fences out a healthy owner; cleanup that stops too late
    keeps an *abandoned* intent's lease alive and fences out a legitimate successor.  The
    pre-fix ``stop()`` joined with a timeout, ignored whether the join succeeded, and dropped
    ``self._thread`` first -- so a renewal wedged inside the ledger became an unobservable
    orphan that the executor reported as a clean shutdown.
    """

    # A bound on the join, small because the wedged thread can never satisfy it: this is the
    # timeout under test, not a wait for anything to make progress.
    JOIN_BOUND = 0.05

    def wedged_keeper(self, **kwargs):
        """A keeper whose single renewal is stuck inside the ledger when ``stop()`` runs."""
        ledger = WedgedLedger()
        pacer = BeatPacer()
        keeper = LeaseKeeper(ledger, "intent_wedged", "token", interval_seconds=BEAT_SECONDS,
                             waiter=pacer, join_seconds=self.JOIN_BOUND, **kwargs)
        self.addCleanup(self._retire, ledger, keeper)
        keeper.start()
        pacer.release_beat()
        self.assertTrue(ledger.entered.wait(JOIN_TIMEOUT),
                        "the renewal never reached the ledger")
        return ledger, pacer, keeper

    def _retire(self, ledger, keeper):
        """Let the wedged renewal finish so no test leaves a live thread behind."""
        ledger.finish.set()
        thread = keeper._thread
        if thread is not None:
            thread.join(JOIN_TIMEOUT)

    def test_a_stop_that_cannot_retire_the_thread_reports_failure_and_keeps_the_handle(self):
        """The reviewer's reproduction, as an assertion: no silent orphan, ever."""
        ledger, _pacer, keeper = self.wedged_keeper()

        self.assertFalse(keeper.stop(), "a join that timed out is not a clean shutdown")
        self.assertTrue(keeper.orphaned)
        self.assertTrue(keeper.degraded)
        self.assertIsNotNone(keeper._thread,
                             "the handle must survive the failed join: an orphan nobody "
                             "holds a reference to cannot be observed or joined later")
        self.assertTrue(keeper._thread.is_alive())
        self.assertIsInstance(keeper.cleanup_error(), LeaseKeeperNotStopped)
        self.assertIsInstance(keeper.cleanup_error(), LeaseRenewalFailed)

        # Sticky: a cleanup that failed must never later be reported as clean, even after
        # the thread finally exits and the handle is reaped.
        ledger.finish.set()
        keeper._thread.join(JOIN_TIMEOUT)
        self.assertFalse(keeper.stop())
        self.assertTrue(keeper.orphaned)
        self.assertIsNone(keeper._thread, "a finished thread is reaped by the repeat stop()")

    def test_a_renewal_completing_after_revocation_is_not_counted_and_ends_the_loop(self):
        """An in-flight write cannot be recalled, but it buys the orphan nothing.

        The renewal that was already inside the ledger when ``stop()`` ran lands, and then
        the thread exits without claiming the beat -- so the abandoned lease lapses after at
        most that one period instead of being renewed indefinitely.
        """
        ledger, _pacer, keeper = self.wedged_keeper()
        thread = keeper._thread                    # held here: the pre-fix stop() drops it
        self.assertFalse(keeper.stop())

        ledger.finish.set()
        thread.join(JOIN_TIMEOUT)

        self.assertFalse(thread.is_alive(), "the revoked thread must exit, not keep beating")
        self.assertEqual(ledger.calls, 1, "the orphan must issue no renewal of its own")
        self.assertEqual(keeper.beats, 0,
                         "a renewal completed after revocation is not a beat this keeper "
                         "may claim credit for")
        self.assertTrue(keeper.revoked)

    def test_a_beat_that_falls_due_at_the_instant_of_revocation_writes_nothing(self):
        """The pre-write re-check: revocation wins the race against a due renewal.

        The beat loop tests the stop event once, just after the waiter returns, and then
        writes.  ``stop()`` can land in the gap between those two statements -- the loop has
        already decided to renew, and the renewal it writes extends the lease of an intent
        the executor has just let go of, blocking a takeover the successor is entitled to.

        That gap is nanoseconds wide in production, so the test constructs it rather than
        racing for it: ``stop()`` runs while the beat is parked, and the stop event is then
        cleared to put the loop back exactly where it would be had it read that event one
        instant too early.  Only the revocation flag, re-read immediately before the write,
        can still refuse the beat.
        """
        ledger = CountingLedger()
        waiter = LateBeatWaiter()
        keeper = LeaseKeeper(ledger, "intent_race", "token", interval_seconds=BEAT_SECONDS,
                             waiter=waiter, join_seconds=self.JOIN_BOUND)
        self.addCleanup(waiter.go.set)
        keeper.start()
        thread = keeper._thread
        self.assertTrue(waiter.parked.wait(JOIN_TIMEOUT))

        self.assertFalse(keeper.stop(), "the waiter ignores the stop event, so the join fails")
        keeper._stop.clear()                       # see the docstring: the loop got there first
        waiter.go.set()                            # ... and the period elapses anyway
        thread.join(JOIN_TIMEOUT)

        self.assertFalse(thread.is_alive())
        self.assertEqual(ledger.calls, 0,
                         "a revoked keeper renewed the lease of an intent its executor had "
                         "already released")
        self.assertEqual(keeper.beats, 0)

    def test_the_context_manager_refuses_to_report_a_clean_exit_when_cleanup_failed(self):
        ledger, _pacer, keeper = self.wedged_keeper()
        with self.assertRaises(LeaseKeeperNotStopped):
            with keeper:                           # already started; __enter__ is idempotent
                pass
        self.assertTrue(keeper.orphaned)

    def test_a_failed_cleanup_never_masks_the_exception_the_body_raised(self):
        ledger, _pacer, keeper = self.wedged_keeper()
        with self.assertRaises(ZeroDivisionError):
            with keeper:
                raise ZeroDivisionError("the external call blew up")
        self.assertTrue(keeper.orphaned,
                        "the cleanup failure stays observable even when it is not raised")

    def test_a_clean_shutdown_reports_success_and_stop_stays_idempotent(self):
        store = rs.InMemoryRuntimeStateStore(clock=rs.ManualLeaseClock(), owner_id="A")
        state = dict(initial_state(run_id="run_stop", thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES))
        intent = make_intent(state, "WORKER", "PHASE_GATE")
        record = store.claim(intent)
        pacer = BeatPacer()
        keeper = LeaseKeeper(store, intent["intent_id"], record["lease_token"],
                             interval_seconds=BEAT_SECONDS, waiter=pacer)
        keeper.start()
        pacer.request_beat(keeper)
        self.assertTrue(keeper.stop())
        self.assertTrue(keeper.stop(), "stop() must stay safe, and honest, when repeated")
        self.assertFalse(keeper.orphaned)
        self.assertIsNone(keeper._thread)
        self.assertEqual(keeper.beats, 1)


class KeeperShutdownExecutorTests(_LeaseFixture):
    """The executor must not report success on top of a keeper it could not retire."""

    def test_an_executor_that_cannot_retire_its_keeper_fails_closed(self):
        state, intent = self.prepared_state(run_id="run_stopfail")
        store = self.store("coordinator-A", clock=rs.ManualLeaseClock())
        adapter = FakeAdapter([deepcopy(WORKER_RESULT)], runtime_state=store,
                              external_world=self.world)
        ledger_wedge = WedgedLedger()
        pacer = BeatPacer()
        keepers: list = []

        def wedging_factory(runtime_state, intent_id, lease_token):
            keeper = LeaseKeeper(ledger_wedge, intent_id, lease_token,
                                 interval_seconds=BEAT_SECONDS, waiter=pacer,
                                 join_seconds=KeeperShutdownTests.JOIN_BOUND)
            keepers.append(keeper)
            keeper.start()                         # ``__enter__`` will find it already running
            pacer.release_beat()                   # this renewal wedges inside the ledger
            if not ledger_wedge.entered.wait(JOIN_TIMEOUT):   # pragma: no cover
                raise AssertionError("the renewal never reached the wedged ledger")
            return keeper

        self.addCleanup(ledger_wedge.finish.set)
        with self.assertRaises(IdempotencyRecoveryError) as caught:
            execute_intent_node(adapter, runtime_state=store,
                                keeper_factory=wedging_factory)(deepcopy(state))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_LEASE_LOST")
        self.assertIn("LEASE_KEEPER_NOT_STOPPED", str(caught.exception))
        self.assertTrue(keepers[-1].orphaned)
        ledger_wedge.finish.set()
        keepers[-1]._thread.join(JOIN_TIMEOUT)
        self.assertFalse(keepers[-1]._thread.is_alive())


# =======================================================================================
# Scenarios 1-4: a healthy owner outlasts its lease, B stays an observer
# =======================================================================================

class HealthyLongRunningOwnerTests(_LeaseFixture):
    """Scenarios 1-4 through the shipped executor, with time under the test's control.

    Only two things are injected, and neither weakens the proof: the keeper's *waiter* (so a
    beat happens when the test says so instead of when the scheduler says so) and the lease
    clock (so "the work outlasted the lease" is a fact the test establishes rather than a
    duration it hopes for).  The keeper itself is the production one, built by the production
    ``lease_keeper_factory`` at the production period derived from ``lease_seconds``.
    """

    def test_a_healthy_owner_outlasting_its_lease_keeps_it_and_b_never_takes_over(self):
        """A's external call outlives its lease; B claims mid-flight and stays an observer.

        Before the fix nothing renewed A's lease, so B's mid-flight claim succeeded, the
        token rotated, and A's own settlement was fenced out.
        """
        clock = rs.ManualLeaseClock()
        observer_clock = PacedObserverClock(clock)
        state, intent = self.prepared_state()
        intent_id = intent["intent_id"]
        store_a = self.store("coordinator-A", clock=clock)
        store_b = self.store("coordinator-B", clock=observer_clock)
        adapter_a = BlockingAdapter([deepcopy(WORKER_RESULT)], runtime_state=store_a,
                                    external_world=self.world)
        # B's script is deliberately empty: if B ever started the work, it would raise.
        adapter_b = FakeAdapter([], runtime_state=store_b, external_world=self.world)
        pacer = BeatPacer(clock=clock, advance_by=BEAT_SECONDS)
        # The production factory and the production keeper; only the wait is injected.
        factory = CapturingFactory(lease_keeper_factory(waiter=pacer))

        thread_a, out_a = self.run_async("A", lambda: execute_intent_node(
            adapter_a, runtime_state=store_a, keeper_factory=factory)(deepcopy(state)))
        self.assertTrue(adapter_a.started.wait(JOIN_TIMEOUT), "A never entered adapter.start()")
        keeper = factory.keeper()
        self.assertAlmostEqual(keeper._interval, BEAT_SECONDS,
                               msg="the executor must beat at the lease-derived period")
        claimed_at = self.record(intent_id)
        self.assertEqual(claimed_at["owner_id"], "coordinator-A")
        deadline = claimed_at["lease_expires_at"]

        # Scenario 1: stay inside the external call until the clock is provably past the
        # deadline the original, un-renewed claim carried.  Each beat advances the injected
        # clock by exactly one renewal period and then renews, so this is a counted fact.
        beats_to_outlast = int(LEASE_SECONDS // BEAT_SECONDS) + 1
        for _ in range(beats_to_outlast):
            pacer.request_beat(keeper)
        self.assertGreater(clock.time(), deadline,
                           "the external call must outlast the lease it was claimed under")
        renewed = self.record(intent_id)
        self.assertGreater(renewed["last_heartbeat_at"], deadline,
                           "the executor must renew the lease during the external call")
        self.assertGreater(renewed["lease_expires_at"], clock.time(),
                           "a renewed lease must still be in the future")
        self.assertEqual(renewed["lease_token"], claimed_at["lease_token"],
                         "a renewal extends the claim; it never rotates the token")

        # Scenario 2-3: B claims mid-flight and is refused, then takes the observer role.
        with self.assertRaises(rs.RuntimeStateLeaseHeld):
            store_b.claim(intent)
        thread_b, out_b = self.run_async("B", lambda: execute_intent_node(
            adapter_b, runtime_state=store_b,
            observe_timeout_seconds=10 * LEASE_SECONDS)(deepcopy(state)))
        observer_clock.wait_until_parked()
        # ... and renewal keeps working while B watches: B is an observer, not a blocker.
        for _ in range(beats_to_outlast):
            pacer.request_beat(keeper)
        watched = self.record(intent_id)
        self.assertEqual(watched["owner_id"], "coordinator-A")
        self.assertEqual(watched["lease_token"], claimed_at["lease_token"])
        self.assertEqual(adapter_b.effect_count, 0, "B must create no second external effect")

        adapter_a.release.set()
        thread_a.join(JOIN_TIMEOUT)
        observer_clock.release()                   # B may poll normally now that A is done
        thread_b.join(JOIN_TIMEOUT)

        # Scenario 4: A alone effected and settled; B adopted A's settlement.
        self.assertNotIn("error", out_a, f"A must finish its own work: {out_a.get('error')}")
        self.assertNotIn("error", out_b, f"B must observe, not fail: {out_b.get('error')}")
        self.assertEqual(out_b["event"]["event_id"], out_a["event"]["event_id"],
                         "B must adopt the owner's settlement rather than produce its own")
        self.assertEqual(adapter_a.effect_count, 1)
        self.assertEqual(adapter_b.effect_count, 0)
        settled = self.record(intent_id)
        self.assertEqual(settled["owner_id"], "coordinator-A")
        self.assertEqual(settled["status"], rs.SETTLED)
        self.assertEqual(settled["lease_token"], claimed_at["lease_token"],
                         "the healthy owner's token must never have been rotated")
        self.assertEqual(settled["settlement"]["event_id"], out_a["event"]["event_id"])
        self.assertIsNotNone(settled["receipt"])

    def test_the_lease_keeper_is_load_bearing(self):
        """Mutation: put the un-renewed claim back and the healthy owner is taken over again.

        Same scenario and the same controlled clock, with only the keeper replaced by one
        that renews nothing.  Time advances by one lease period -- explicitly, not by
        waiting -- and B takes ownership from a Coordinator that is still doing the work.
        """
        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_leasemut")
        intent_id = intent["intent_id"]
        store_a = self.store("coordinator-A", clock=clock)
        store_b = self.store("coordinator-B", clock=clock)
        adapter_a = BlockingAdapter([deepcopy(WORKER_RESULT)], runtime_state=store_a,
                                    external_world=self.world)
        thread_a, out_a = self.run_async("A-mutated", lambda: execute_intent_node(
            adapter_a, runtime_state=store_a, keeper_factory=null_keeper_factory,
            observe_timeout_seconds=LEASE_SECONDS / 2)(deepcopy(state)))
        self.assertTrue(adapter_a.started.wait(JOIN_TIMEOUT))
        claimed = self.record(intent_id)

        # The defect, expressed as one line of arithmetic instead of a sleep: A is healthy
        # and still inside adapter.start(), and nothing renewed its lease.
        clock.advance(LEASE_SECONDS + 1.0)
        record_b = store_b.claim(intent)
        self.assertEqual(record_b["claim_outcome"], rs.RESUMED,
                         "without renewal a healthy owner is taken over -- the defect")
        self.assertNotEqual(record_b["lease_token"], claimed["lease_token"],
                            "B rotates the token out from under a Coordinator that is "
                            "still doing the work")
        with self.assertRaises(rs.RuntimeStateLeaseHeld):
            # The harm the review describes: the healthy owner's own writes are refused.
            store_a.record_receipt(intent_id, {"external_id": "healthy"},
                                   claimed["lease_token"])
        adapter_a.release.set()
        thread_a.join(JOIN_TIMEOUT)
        self.assertEqual(adapter_a.effect_count, 1,
                         "A really did the work whose settlement is about to be thrown away")
        self.assertIsInstance(out_a.get("error"), IdempotencyRecoveryError,
                              "the superseded healthy owner cannot settle its own work")
        self.assertIsNone(self.record(intent_id)["settlement"])

    def test_the_executor_wires_the_production_keeper_when_nothing_is_injected(self):
        """No injection at all: the default factory is the real one, at the real period.

        The deterministic scenario above injects a waiter, so this pins the wiring it cannot:
        that ``execute_intent_node`` with no ``keeper_factory`` builds a real ``LeaseKeeper``
        around the ledger's own token, with the lease-derived period and the real waiter.
        """
        from unittest.mock import patch

        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_default")
        store = self.store("coordinator-A", clock=clock)
        adapter = FakeAdapter([deepcopy(WORKER_RESULT)], runtime_state=store,
                              external_world=self.world)
        built: list = []
        real_factory = executor_module.lease_keeper_factory

        def spy(*args, **kwargs):
            inner = real_factory(*args, **kwargs)

            def factory(runtime_state, intent_id, lease_token):
                keeper = inner(runtime_state, intent_id, lease_token)
                built.append(keeper)
                return keeper
            return factory

        with patch.object(executor_module, "lease_keeper_factory", spy):
            execute_intent_node(adapter, runtime_state=store)(deepcopy(state))

        self.assertEqual(len(built), 1, "every claimed intent runs inside exactly one keeper")
        keeper = built[0]
        self.assertIsInstance(keeper, LeaseKeeper)
        self.assertIs(keeper._runtime_state, store)
        self.assertIs(keeper._waiter, _default_waiter,
                      "production must renew on the real clock, not on a test primitive")
        self.assertAlmostEqual(keeper._interval, heartbeat_interval_for(store.lease_seconds))
        self.assertLess(keeper._interval, store.lease_seconds / 2)
        self.assertEqual(keeper._lease_token, self.record(intent["intent_id"])["lease_token"])
        self.assertFalse(keeper.degraded, "the default path must shut its keeper down cleanly")
        self.assertIsNone(keeper._thread)


# =======================================================================================
# Scenario 5: a dead owner still loses its lease; recovery is unchanged
# =======================================================================================

class DeadOwnerRecoveryTests(_LeaseFixture):
    """Renewal must not defeat takeover: when the beats stop, the old path still runs."""

    def test_when_the_beats_stop_the_lease_lapses_and_b_recovers(self):
        """Scenario 5, on an injected clock: no beats, lease expires, B runs the ladder."""
        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_dead")
        intent_id = intent["intent_id"]
        store_a = self.store("coordinator-A", clock=clock, lease_seconds=60.0)
        store_b = self.store("coordinator-B", clock=clock, lease_seconds=60.0)
        claimed = store_a.claim(intent)            # A claims, then is killed: no heartbeat
        adapter_b = FakeAdapter([deepcopy(WORKER_RESULT)], runtime_state=store_b,
                                external_world=self.world)

        clock.advance(120.0)                       # two lease periods with nobody renewing
        event = execute_intent_node(adapter_b, runtime_state=store_b)(
            deepcopy(state))["pending_event"]

        self.assertEqual(adapter_b.effect_count, 1,
                         "the successor runs work the dead owner provably never started")
        settled = self.record(intent_id)
        self.assertEqual(settled["owner_id"], "coordinator-B")
        self.assertEqual(settled["status"], rs.SETTLED)
        self.assertNotEqual(settled["lease_token"], claimed["lease_token"])
        self.assertEqual(settled["settlement"]["event_id"], event["event_id"])
        # The dead owner, were it to come back, is still fenced out.
        with self.assertRaises(rs.RuntimeStateLeaseHeld):
            store_a.record_receipt(intent_id, {"external_id": "zombie"},
                                   claimed["lease_token"])

    def test_a_dead_owner_that_already_created_the_effect_is_collected_not_rerun(self):
        """The EFFECTED rung of the ladder still resumes rather than duplicating."""
        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_dead2")
        intent_id = intent["intent_id"]
        store_a = self.store("coordinator-A", clock=clock, lease_seconds=60.0)
        store_b = self.store("coordinator-B", clock=clock, lease_seconds=60.0)
        claimed = store_a.claim(intent)
        self.world.create(intent)                  # A's effect exists ...
        self.world.complete(intent_id, deepcopy(WORKER_RESULT), "2026-01-01T00:00:01Z")
        store_a.record_receipt(intent_id, {"external_id": "ext_1"}, claimed["lease_token"])
        adapter_b = FakeAdapter([], runtime_state=store_b, external_world=self.world)

        clock.advance(120.0)
        event = execute_intent_node(adapter_b, runtime_state=store_b)(
            deepcopy(state))["pending_event"]

        self.assertEqual(adapter_b.effect_count, 0, "the existing effect must be collected")
        self.assertEqual(self.record(intent_id)["settlement"]["event_id"], event["event_id"])


# =======================================================================================
# Scenario 6: a rotated token or a failed renewal fails closed
# =======================================================================================

class RenewalFailureTests(_LeaseFixture):
    """A keeper that loses the lease stops the owner from writing anything further."""

    def test_a_token_rotated_mid_flight_fails_the_owner_closed(self):
        """Scenario 6, with a *real* rotation: B legitimately takes over, A writes nothing.

        A's lease is dropped (``release``) to model a stalled owner, B claims and rotates the
        token, and A's next renewal therefore fails.  A must then refuse to settle instead of
        pushing its settlement into the successor's record.
        """
        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_rotate")
        intent_id = intent["intent_id"]
        store_a = self.store("coordinator-A", clock=clock, lease_seconds=60.0)
        store_b = self.store("coordinator-B", clock=clock, lease_seconds=60.0)
        # No ledger wired into A's adapter: the write under test is the executor's own
        # ``settle``, which must never be reached once ownership is gone.
        adapter_a = BlockingAdapter([deepcopy(WORKER_RESULT)], external_world=self.world)
        pacer = BeatPacer()
        factory = CapturingFactory(lease_keeper_factory(interval_seconds=20.0, waiter=pacer))

        thread_a, out_a = self.run_async("A", lambda: execute_intent_node(
            adapter_a, runtime_state=store_a, keeper_factory=factory)(deepcopy(state)))
        self.assertTrue(adapter_a.started.wait(JOIN_TIMEOUT))
        keeper = factory.keeper()
        pacer.request_beat(keeper)                 # a healthy renewal first
        self.assertEqual(keeper.beats, 1)
        claimed = self.record(intent_id)

        store_a.release(intent_id, claimed["lease_token"])   # A stalls; its lease lapses
        record_b = store_b.claim(intent)
        self.assertEqual(record_b["claim_outcome"], rs.RESUMED)
        self.assertNotEqual(record_b["lease_token"], claimed["lease_token"])

        pacer.request_beat(keeper)                 # the renewal that discovers the rotation
        self.assertTrue(keeper.lost, "a rotated token must fail the renewal, not renew it")

        adapter_a.release.set()
        thread_a.join(JOIN_TIMEOUT)
        error = out_a.get("error")
        self.assertIsInstance(error, IdempotencyRecoveryError)
        self.assertEqual(error.code, "IDEMPOTENCY_LEASE_LOST")
        after = self.record(intent_id)
        self.assertEqual(after["owner_id"], "coordinator-B")
        self.assertIsNone(after["settlement"],
                          "an owner that lost its lease must record no settlement")
        self.assertNotEqual(after["status"], rs.SETTLED)

    def test_a_renewal_error_fails_closed_before_any_settlement(self):
        """Scenario 6, renewal failure rather than rotation: same fail-closed outcome."""
        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_renewalerror")
        intent_id = intent["intent_id"]
        store = self.store("coordinator-A", clock=clock, lease_seconds=60.0)

        class UnreadableOnHeartbeat:
            """The ledger becomes unreadable exactly while the external call is running."""

            lease_seconds = 60.0

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def heartbeat(self, intent_id, lease_token):
                raise rs.RuntimeStateCorrupt(f"UNREADABLE_RUNTIME_STATE:{intent_id}")

        ledger = UnreadableOnHeartbeat(store)
        adapter = BlockingAdapter([deepcopy(WORKER_RESULT)], external_world=self.world)
        pacer = BeatPacer()
        factory = CapturingFactory(lease_keeper_factory(interval_seconds=20.0, waiter=pacer))
        thread, out = self.run_async("A", lambda: execute_intent_node(
            adapter, runtime_state=ledger, keeper_factory=factory)(deepcopy(state)))
        self.assertTrue(adapter.started.wait(JOIN_TIMEOUT))
        keeper = factory.keeper()
        pacer.request_beat(keeper)
        self.assertTrue(keeper.lost)

        adapter.release.set()
        thread.join(JOIN_TIMEOUT)
        error = out.get("error")
        self.assertIsInstance(error, IdempotencyRecoveryError)
        self.assertEqual(error.code, "IDEMPOTENCY_LEASE_LOST")
        self.assertIsNone(self.record(intent_id)["settlement"])

    def test_the_fail_closed_checkpoint_is_load_bearing(self):
        """Mutation: ignore the lost lease and the superseded owner settles anyway."""
        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_rotatemut")
        intent_id = intent["intent_id"]
        store_a = self.store("coordinator-A", clock=clock, lease_seconds=60.0)
        store_b = self.store("coordinator-B", clock=clock, lease_seconds=60.0)
        adapter_a = BlockingAdapter([deepcopy(WORKER_RESULT)], external_world=self.world)

        class DeafKeeper(NullKeeper):
            """Renews nothing and, crucially, never reports the loss."""

        thread_a, out_a = self.run_async("A-mutated", lambda: execute_intent_node(
            adapter_a, runtime_state=store_a,
            keeper_factory=lambda *args: DeafKeeper())(deepcopy(state)))
        self.assertTrue(adapter_a.started.wait(JOIN_TIMEOUT))
        claimed = self.record(intent_id)
        store_a.release(intent_id, claimed["lease_token"])
        store_b.claim(intent)

        adapter_a.release.set()
        thread_a.join(JOIN_TIMEOUT)
        # Without the checkpoint the superseded owner does not stop: it attempts the write,
        # is refused by the round 2 fence, and then wanders back into the claim/observe path
        # -- the very re-entry the fail-closed rule exists to prevent.
        error = out_a.get("error")
        self.assertIsInstance(error, IdempotencyRecoveryError)
        self.assertNotEqual(error.code, "IDEMPOTENCY_LEASE_LOST",
                            "with the checkpoint disabled the loss is never named")
        self.assertIsNone(self.record(intent_id)["settlement"])


    @REQUIRES_LANGGRAPH
    def test_a_lost_lease_stops_the_run_instead_of_advancing_the_workflow(self):
        """The launcher projects the loss onto a named BLOCKED terminal, not a crash.

        This is the "no workflow state is advanced" half of the fail-closed rule: the run
        ends at ``IDEMPOTENCY_LEASE_LOST`` with no phase consumed and no settlement applied.
        """
        from unittest.mock import patch

        from scripts.deterministic_workflow import graph as graph_module
        from scripts.deterministic_workflow.launcher import execute_state

        state, _ = self.prepared_state(run_id="run_leaselost")
        state.update(pending_intent=None, intent_status="NONE", pending_role=None)
        store = self.store("coordinator-A")
        adapter = FakeAdapter([], runtime_state=store, external_world=self.world)

        class LosingGraph:
            def invoke(self, raw_state, config):
                raise IdempotencyRecoveryError("IDEMPOTENCY_LEASE_LOST",
                                               "the lease could not be renewed")

        with patch.object(graph_module, "build_graph", lambda *args, **kwargs: LosingGraph()):
            terminal = execute_state(state, adapter=adapter, runtime_state=store,
                                     checkpoint_store_path=self.root / "cp.json")
        self.assertEqual(terminal["terminal_status"], "BLOCKED")
        self.assertEqual(terminal["terminal_reason"]["code"], "IDEMPOTENCY_LEASE_LOST")
        self.assertEqual(terminal["phase_iterations"]["ANALYSIS"], 0,
                         "a Coordinator that lost its lease advances no workflow state")


class ThreadSafeLedgerLockTests(_LeaseFixture):
    """The keeper renews from a second thread, so the file store's lock must be thread-safe."""

    def test_a_renewal_thread_cannot_slip_inside_another_threads_critical_section(self):
        """Without the mutex the depth counter lets a second thread skip ``flock`` entirely."""
        store = self.store("coordinator-A", lease_seconds=60.0)
        _, intent = self.prepared_state(run_id="run_threadsafe")
        store.claim(intent)
        inside = threading.Event()
        proceed = threading.Event()
        observed: list[int] = []

        def holder():
            with store._locked():
                inside.set()
                proceed.wait(JOIN_TIMEOUT)

        def renewer():
            inside.wait(JOIN_TIMEOUT)
            entered = threading.Event()

            def attempt():
                with store._locked():
                    observed.append(store._depth)
                entered.set()

            thread = threading.Thread(target=attempt, daemon=True)
            thread.start()
            # The second thread must *block*: the first still owns the critical section.
            self.assertFalse(entered.wait(0.3),
                             "a second thread entered the ledger critical section while "
                             "another thread held it")
            proceed.set()
            thread.join(JOIN_TIMEOUT)

        holder_thread = threading.Thread(target=holder, daemon=True)
        self.threads.append(holder_thread)
        holder_thread.start()
        renewer()
        holder_thread.join(JOIN_TIMEOUT)
        self.assertEqual(observed, [1],
                         "the renewing thread must take the lock itself, not inherit a "
                         "non-zero depth from the thread that already held it")
        self.assertEqual(store._depth, 0)


# =======================================================================================
# Final adversarial review F-ADV-01: the checkpoint-to-write gap
# =======================================================================================

class ParkedSettleLedger:
    """Parks ``settle()`` so a renewal can fail *while the write is in flight*.

    This is the adversarial interleaving, built out of Events rather than elapsed time: the
    executor is held inside its final write, the renewal that fails is released only then,
    and the write is let through only once the failure has been recorded.  The injected
    failure is a :class:`RuntimeStateLockTimeout` on purpose -- it is the kind of renewal
    failure that leaves the lease token perfectly valid, so the round 2 fence accepts the
    write and cannot be what catches this.
    """

    def __init__(self, inner):
        self._inner = inner
        self.lease_seconds = inner.lease_seconds
        self.settle_entered = threading.Event()
        self.allow_settle = threading.Event()
        self.heartbeat_failed = threading.Event()

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def heartbeat(self, intent_id, lease_token):
        self.heartbeat_failed.set()
        raise rs.RuntimeStateLockTimeout(f"RUNTIME_STATE_LOCK_TIMEOUT:{intent_id}")

    def settle(self, intent_id, event, lease_token):
        self.settle_entered.set()
        if not self.allow_settle.wait(JOIN_TIMEOUT):  # pragma: no cover - only if wedged
            raise AssertionError("the test never released the parked settlement write")
        return self._inner.settle(intent_id, event, lease_token)


class BeatOnExit:
    """A keeper wrapper that fires the failing renewal in the one gap no write can cover.

    After the last write's checkpoint the executor takes no further checkpoint -- there is no
    code left between the write and the ``with`` block closing -- so a renewal that fails
    there can only be caught by the context manager's own exit.  Releasing the beat from
    inside ``__exit__``, *before* delegating to the real one, makes that instant reproducible
    instead of leaving it to the scheduler.
    """

    def __init__(self, keeper, pacer):
        self._keeper = keeper
        self._pacer = pacer

    def __getattr__(self, name):
        return getattr(self._keeper, name)

    def __enter__(self):
        self._keeper.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._pacer.release_beat()
        if not self._keeper.wait_for_beats(1, timeout=JOIN_TIMEOUT):  # pragma: no cover
            raise AssertionError("the renewal released at exit never landed")
        return self._keeper.__exit__(exc_type, exc, tb)


class ReceiptWriteLedger:
    """Fails the renewal *inside* the receipt write, the rung before another external call.

    ``_recover`` records the receipt it looked up and then calls ``adapter.resume`` -- a real
    interaction with the external runtime.  A renewal that failed while the receipt was being
    written must stop the executor there, before it touches the external world again on a
    claim it can no longer vouch for.
    """

    def __init__(self, inner, beat):
        self._inner = inner
        self.lease_seconds = inner.lease_seconds
        self._beat = beat

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def heartbeat(self, intent_id, lease_token):
        raise rs.RuntimeStateLockTimeout(f"RUNTIME_STATE_LOCK_TIMEOUT:{intent_id}")

    def record_receipt(self, intent_id, receipt, lease_token):
        result = self._inner.record_receipt(intent_id, receipt, lease_token)
        self._beat()                               # the renewal fails as the write lands
        return result


class LookupOnlyAdapter:
    """The CLAIMED rung: nothing settled, a lookup finds the effect, resume must not run."""

    def __init__(self):
        self.resume_calls = 0

    def capabilities(self):
        return frozenset({EXTERNAL_LOOKUP, EXTERNAL_RESUME})

    def settlement(self, intent_id):
        return None

    def lookup(self, intent):
        return {"external_id": "ext_recovered"}

    def resume(self, intent, receipt):             # pragma: no cover - must never be reached
        self.resume_calls += 1
        return None


class CheckpointToWriteRaceTests(_LeaseFixture):
    """A renewal failure must not be swallowed by landing after the last checkpoint.

    The round 3 fix checkpointed ownership *before* every write, which is only half of it.
    A renewal failure that is not an ownership rotation -- a lock timeout, a transient
    unreadable ledger -- leaves the lease token valid, so the fence rightly accepts the write
    that follows and cannot notice; and the keeper's exit reported only cleanup failures, not
    recorded renewal failures.  A failure landing between the last checkpoint and the end of
    the block therefore vanished, and the node returned success and advanced the workflow on
    a claim it could no longer vouch for.
    """

    def test_a_renewal_failing_while_the_settlement_write_is_in_flight_is_not_swallowed(self):
        """F-ADV-01, as an assertion: the write's own checkpoint is taken *after* it too."""
        state, intent = self.prepared_state(run_id="run_writerace")
        intent_id = intent["intent_id"]
        ledger = ParkedSettleLedger(self.store("coordinator-A", clock=rs.ManualLeaseClock()))
        adapter = FakeAdapter([deepcopy(WORKER_RESULT)], external_world=self.world)
        pacer = BeatPacer()
        factory = CapturingFactory(lease_keeper_factory(interval_seconds=20.0, waiter=pacer))

        thread, out = self.run_async("A", lambda: execute_intent_node(
            adapter, runtime_state=ledger, keeper_factory=factory)(deepcopy(state)))
        self.assertTrue(ledger.settle_entered.wait(JOIN_TIMEOUT),
                        "the executor never reached its final write")
        keeper = factory.keeper()
        pacer.request_beat(keeper)                 # the renewal that fails, mid-write
        self.assertTrue(ledger.heartbeat_failed.is_set())
        self.assertTrue(keeper.lost, "the keeper must record the failed renewal")
        ledger.allow_settle.set()                  # only now does the write land
        thread.join(JOIN_TIMEOUT)

        error = out.get("error")
        self.assertIsInstance(error, IdempotencyRecoveryError,
                              "a renewal that failed during the write must fail the node "
                              "closed, not be swallowed because the token was still valid")
        self.assertEqual(error.code, "IDEMPOTENCY_LEASE_LOST")
        self.assertNotIn("event", out,
                         "no workflow state may be advanced on a claim the executor can no "
                         "longer vouch for")
        self.assertEqual(adapter.effect_count, 1,
                         "failing closed must not re-create the external effect")
        # The settlement that did land is a *fact* -- the external effect really did settle,
        # and a successor adopts it as ALREADY_SETTLED rather than running the work twice.
        # What must not happen is this executor reporting success on top of it.
        self.assertEqual(self.record(intent_id)["status"], rs.SETTLED)

    def test_a_renewal_failing_after_the_last_write_fails_the_keepers_exit_closed(self):
        """The remaining gap: a failure recorded once the body has no checkpoints left."""
        state, intent = self.prepared_state(run_id="run_exitrace")
        intent_id = intent["intent_id"]

        class UnreadableOnHeartbeat:
            lease_seconds = LEASE_SECONDS

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def heartbeat(self, intent_id, lease_token):
                raise rs.RuntimeStateLockTimeout(f"RUNTIME_STATE_LOCK_TIMEOUT:{intent_id}")

        ledger = UnreadableOnHeartbeat(self.store("coordinator-A", clock=rs.ManualLeaseClock()))
        adapter = FakeAdapter([deepcopy(WORKER_RESULT)], external_world=self.world)
        pacer = BeatPacer()
        inner_factory = lease_keeper_factory(interval_seconds=20.0, waiter=pacer)

        def factory(runtime_state, intent_id_, lease_token):
            return BeatOnExit(inner_factory(runtime_state, intent_id_, lease_token), pacer)

        thread, out = self.run_async("A", lambda: execute_intent_node(
            adapter, runtime_state=ledger, keeper_factory=factory)(deepcopy(state)))
        thread.join(JOIN_TIMEOUT)

        error = out.get("error")
        self.assertIsInstance(error, IdempotencyRecoveryError,
                              "a renewal failure recorded after the last write must be "
                              "reported by the keeper's exit, not die with the keeper")
        self.assertEqual(error.code, "IDEMPOTENCY_LEASE_LOST")
        self.assertNotIn("event", out)
        self.assertEqual(adapter.effect_count, 1)
        self.assertEqual(self.record(intent_id)["status"], rs.SETTLED)

    def test_a_renewal_failing_during_the_receipt_write_stops_before_the_next_call(self):
        """The post-write checkpoint on its own rung: no further *external* call is made."""
        clock = rs.ManualLeaseClock()
        state, intent = self.prepared_state(run_id="run_receiptrace")
        intent_id = intent["intent_id"]
        store_a = self.store("coordinator-A", clock=clock, lease_seconds=60.0)
        store_b = self.store("coordinator-B", clock=clock, lease_seconds=60.0)
        store_a.claim(intent)                      # A claims, then dies before any receipt
        clock.advance(120.0)                       # its lease lapses; B takes the ladder

        pacer = BeatPacer()
        factory = CapturingFactory(lease_keeper_factory(interval_seconds=20.0, waiter=pacer))
        ledger = ReceiptWriteLedger(store_b, lambda: pacer.request_beat(factory.keeper()))
        adapter = LookupOnlyAdapter()

        with self.assertRaises(IdempotencyRecoveryError) as raised:
            execute_intent_node(adapter, runtime_state=ledger,
                                keeper_factory=factory)(deepcopy(state))

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_LEASE_LOST")
        self.assertEqual(adapter.resume_calls, 0,
                         "an executor whose renewal failed during the receipt write must "
                         "not go on to touch the external runtime again")
        self.assertIsNone(self.record(intent_id)["settlement"])

    def test_the_exit_checkpoint_never_masks_the_exception_the_body_raised(self):
        """The iteration 2 property survives: a recorded failure never hides a real one."""
        class Rejecting:
            lease_seconds = LEASE_SECONDS

            def heartbeat(self, intent_id, lease_token):
                raise rs.RuntimeStateLeaseHeld(f"LEASE_LOST:{intent_id}")

        pacer = BeatPacer()
        keeper = LeaseKeeper(Rejecting(), "intent_mask", "token",
                             interval_seconds=BEAT_SECONDS, waiter=pacer)
        with self.assertRaises(ZeroDivisionError):
            with keeper:
                pacer.request_beat(keeper)
                self.assertTrue(keeper.lost)
                raise ZeroDivisionError("the external call blew up")
        self.assertTrue(keeper.lost,
                        "the renewal failure stays observable even when it is not raised")

    def test_a_clean_run_still_exits_without_raising(self):
        """The exit checkpoint must fence out failure, not healthy completion."""
        store = rs.InMemoryRuntimeStateStore(clock=rs.ManualLeaseClock(), owner_id="A")
        state, intent = self.prepared_state(run_id="run_cleanexit")
        record = store.claim(intent)
        pacer = BeatPacer()
        keeper = LeaseKeeper(store, intent["intent_id"], record["lease_token"],
                             interval_seconds=BEAT_SECONDS, waiter=pacer)
        with keeper:
            pacer.request_beat(keeper)
        self.assertEqual(keeper.beats, 1)
        self.assertFalse(keeper.degraded)


if __name__ == "__main__":
    unittest.main()
