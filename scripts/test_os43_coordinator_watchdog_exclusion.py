"""OS-43 CRITICAL: the Coordinator and the Watchdog may not both drive one stalled run.

The delivered recovery lease (``recovery_store.claim``) serialises Watchdog-vs-Watchdog:
two ``recover_stalled_run`` calls over one record produce exactly one ``CREATED``
(``test_os43_delivered_wiring.ConcurrentClaimRaceTests``).  The Coordinator's ORDINARY
execution path -- ``launcher.execute_state`` -> ``graph.invoke(raw_state, config)``
(``launcher.py:176``) -- took no run-scoped authority at all, so nothing serialised
Coordinator-vs-Watchdog, and ``coordinator_liveness`` is explicitly an OBSERVATION rather
than a mutual-exclusion primitive (``coordinator_liveness.py:164-172``).

The TOCTOU window this file executes is the one the review named, in the order it named
it:

1. Coordinator A's liveness reads ``EXPIRED``;
2. Watchdog W builds the real observation snapshot, classifies ``STALLED_RECOVERABLE``
   and STOPS AT A BARRIER -- after the observation, before recovery begins;
3. A revives, refreshes its liveness lease and resumes the SAME checkpoint through the
   shipped Coordinator entry point, parking inside the ONE external effect it creates;
4. the barrier is released and W attempts its recovery;
5. exactly one of A and W may perform the graph transition and the side effect;
6. the loser must carry an EXPLICIT refusal code; and
7. replaying W and restarting A must add no transition, no dispatch and no attempt row.

**Determinism comes from the rendezvous, not from sleeping.**  Nothing here sleeps.  The
barrier and the two events order every step; the bounded waits exist only so a build in
which the exclusion is BROKEN fails this test instead of hanging the suite, which is the
same discipline ``ConcurrentClaimRaceTests.RENDEZVOUS_TIMEOUT`` uses.
"""
from __future__ import annotations

import contextlib
import threading
import unittest
from unittest import mock
from typing import Any

from scripts.deterministic_workflow import (coordinator_liveness, launcher, pause_store,
                                            recovery_runtime, recovery_store,
                                            watchdog_observation)
from scripts.deterministic_workflow.contracts import BASE_CAPABILITIES
from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
from scripts.deterministic_workflow.state import initial_state
from scripts.deterministic_workflow.watchdog_classifier import (STALLED_RECOVERABLE,
                                                                classify)
from scripts.test_deterministic_workflow_pause_fixture import REQUIRES_LANGGRAPH
from scripts.test_os43_delivered_wiring import (RESULTS, DeliveredWiringFixture,
                                                quiet_runner)

#: The stable code the loser -- whichever party that turns out to be -- must carry.
EXECUTION_AUTHORITY_HELD = "EXECUTION_AUTHORITY_HELD"


class _Recording:
    """Delegating adapter that records the external effects it really creates.

    ``start`` is the ONE call in this engine that creates an external effect for a stable
    intent (``executor._settle_now``), so a party that appears in ``effects`` performed a
    side effect and a party that does not, did not.
    """

    def __init__(self, inner: Any, *, owner: str, effects: list[str]) -> None:
        self.inner = inner
        self.owner = owner
        self.effects = effects

    def start(self, intent: Any, *, lease_token: str = "") -> Any:
        self.effects.append(self.owner)
        return self.inner.start(intent, lease_token=lease_token)

    def __getattr__(self, name: str) -> Any:      # every other port method, unchanged
        return getattr(self.inner, name)


class _Parking(_Recording):
    """The Coordinator's adapter, parked INSIDE its first external effect.

    Parking here is what makes the race a rendezvous instead of a timing guess: while it
    waits, A is provably inside ``graph.invoke`` with whatever authority the Coordinator
    path takes still held, which is exactly the instant the Watchdog is released into.
    """

    def __init__(self, inner: Any, *, owner: str, effects: list[str],
                 parked: threading.Event, release: threading.Event,
                 timeout: float) -> None:
        super().__init__(inner, owner=owner, effects=effects)
        self.parked = parked
        self.release = release
        self.timeout = timeout

    def start(self, intent: Any, *, lease_token: str = "") -> Any:
        result = super().start(intent, lease_token=lease_token)
        self.parked.set()
        self.release.wait(timeout=self.timeout)
        return result


class _LoggingGraph:
    """A real graph that records the fact that a party entered ``graph.invoke``."""

    def __init__(self, inner: Any, *, owner: str, log: list[str]) -> None:
        self.inner = inner
        self.owner = owner
        self.log = log

    def invoke(self, value: Any, config: Any) -> Any:
        self.log.append(self.owner)
        return self.inner.invoke(value, config)


class _ExclusionFixture(DeliveredWiringFixture):
    """One stalled run, two parties, and the rendezvous that orders them.

    A fixture rather than a test case: both suites below drive the same two parties over
    the same real record, and inheriting the ASSERTIONS instead would silently re-run the
    race three extra times per suite.
    """

    RUN = "run_toctou"
    #: Bounds every wait.  In a green run nothing waits on it: each rendezvous is
    #: released by the other party the instant it arrives.
    RENDEZVOUS_TIMEOUT = 20.0
    #: The claimant identity each party is HANDED, or ``None`` for the composition case.
    #:
    #: ``{"A": ..., "W": ...}`` states two SEPARATE-PROCESS identities, which is what A and
    #: W are in the deployed topology and what two threads cannot be on their own.  That
    #: case is real and stays covered.
    #:
    #: ``None`` hands neither party anything: each resolves its own authority through the
    #: delivered code path, from ``runtime_state.default_owner_id`` -- so both identities
    #: are PRODUCTION-DEFAULT and, being one process, IDENTICAL.  This is the case the
    #: injected fixture could not see, and it is the composition the product actually
    #: offers: ``watchdog_supervisor`` is runtime-neutral and callable in-process (CON-5),
    #: so "two actors" is an ACTOR boundary, not an OS-process one.  A test that constructs
    #: the two identities manufactures the distinctness the exclusion depends on, and
    #: proves the property it assumed rather than the property the product has.
    OWNERS: dict[str, str] | None = {"A": "host:pid7001", "W": "host:pid7002"}

    def authority(self, party: str) -> Any:
        """The REAL execution authority over the run's REAL record, as one process.

        Each party resolves the record the way ITS OWN production code does -- the
        Coordinator from the checkpoint store it is about to advance
        (``launcher._execution_authority``), the Watchdog from the run root
        (``recovery_runtime._recover_active``).  Neither is told where the other looked,
        so the test asserts that the two resolutions really converge on one record rather
        than assuming it.

        ``OWNERS is None`` returns ``None``, which is not "no authority": it is the
        injection point left EMPTY, so ``launcher.execute_state`` and
        ``recovery_runtime.recover_stalled_run`` each construct their own the delivered
        way.  Nothing about either claimant is then supplied by this file.
        """
        return self.authority_for(self.RUN, party)

    def authority_for(self, run_id: str, party: str) -> Any:
        """:meth:`authority`, for any run this fixture built.  Same rule, no exceptions."""
        if self.OWNERS is None:
            return None
        path = (recovery_store.authority_path_for_checkpoint(
            self.runs / run_id / ".workflow_checkpoints.json") if party == "A"
            else recovery_store.recovery_record_path(run_id, artifact_base=self.base))
        return recovery_store.FileRecoveryStateStore(path,
                                                     owner_id=self.OWNERS[party])

    # -- the two parties ----------------------------------------------------------------
    def bindings(self) -> tuple[Any, Any]:
        """This run's durable ledger and settlement journal -- ONE pair, shared.

        Both parties address the same run, so both resolve the same ledger the delivered
        wiring resolves (``launcher._watchdog_wiring.bindings_for``).  Giving them two
        would hide a duplicate dispatch behind two private idempotency ledgers.
        """
        return self.bindings_for(self.RUN)

    def bindings_for(self, run_id: str) -> tuple[Any, Any]:
        """:meth:`bindings`, for any run this fixture built.  ONE pair per run, shared."""
        return (FileRuntimeStateStore(launcher.default_runtime_state_path(run_id, "t")),
                pause_store.journal_for(run_id, artifact_base=self.base))

    def coordinator(self, *, effects: list[str], parked: threading.Event,
                    release: threading.Event, outcomes: dict[str, Any],
                    errors: list[str]) -> None:
        """Coordinator A: refresh liveness, then resume the SAME checkpoint.

        ``execute_state`` is the shipped Coordinator entry point -- the one
        ``run_cli`` calls (``launcher.py:1178``) -- and nothing about it is replaced
        here: only the adapter is wrapped, so the effect it creates is observable and the
        run parks inside it.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter

        keeper = None
        try:
            ledger, journal = self.bindings()
            adapter = _Parking(
                FakeAdapter([dict(item) for item in RESULTS], runtime_state=ledger,
                            run_id=self.RUN, settlement_journal=journal),
                owner="A", effects=effects, parked=parked, release=release,
                timeout=self.RENDEZVOUS_TIMEOUT)
            # Step 3, first half: the revived Coordinator republishes its liveness lease,
            # so the fact W observed one rendezvous earlier is now stale.
            keeper = coordinator_liveness.begin_coordinator_liveness(
                self.RUN, artifact_base=self.base, session_id="session_A")
            self.assertEqual(
                coordinator_liveness.liveness_status(self.RUN, artifact_base=self.base),
                coordinator_liveness.LIVENESS_LIVE,
                "A must look alive before it resumes; otherwise this is not the race")
            outcomes["A"] = launcher.execute_state(
                dict(initial_state(run_id=self.RUN, thread_id="t", phases=("ANALYSIS",),
                                   capabilities=BASE_CAPABILITIES)),
                adapter=adapter, runtime_state=ledger, journal=journal, audit_sink=None,
                artifact_base=self.base, execution_authority=self.authority("A"),
                checkpoint_store_path=self.runs / self.RUN / ".workflow_checkpoints.json")
        except BaseException as exc:                  # reported, never swallowed
            errors.append(f"A: {type(exc).__name__}: {exc}")
        finally:
            parked.set()                              # never strand the other party
            if keeper is not None:
                coordinator_liveness.end_coordinator_liveness(keeper, self.RUN,
                                                              artifact_base=self.base)

    def watchdog_request(self, *, effects: list[str], invocations: list[str]) -> Any:
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph

        ledger, journal = self.bindings()
        adapter = _Recording(
            FakeAdapter([dict(item) for item in RESULTS], runtime_state=ledger,
                        run_id=self.RUN, settlement_journal=journal),
            owner="W", effects=effects)

        def factory(saver: Any) -> Any:
            return _LoggingGraph(build_graph(adapter, checkpointer=saver,
                                             runtime_state=ledger, journal=journal),
                                 owner="W", log=invocations)

        return recovery_runtime.RecoveryRequest(
            run_id=self.RUN, artifact_base=str(self.base), graph_factory=factory,
            actor_id="watchdog_W")

    def observe(self) -> Any:
        """W's REAL observation of the run: the delivered ports, the delivered snapshot."""
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter

        observation = recovery_runtime.RunObservationAdapter(
            self.base, runner=quiet_runner,
            capabilities=FakeAdapter([]).capabilities())
        liveness = recovery_runtime.CoordinatorLivenessReader(self.base)
        return watchdog_observation.snapshot(self.RUN, observation=observation,
                                             liveness=liveness, ledger={})

    # -- the DELIVERED audited composition -------------------------------------------
    def audited_sweep(self, *extra: str) -> dict:
        """One sweep through the shipped, AUDITED Watchdog composition.

        ``run_watchdog_cli`` -> ``launcher._watchdog_wiring`` -> ``watchdog_supervisor``
        -> ``watchdog_audit.FileWatchdogAudit``.  This is the only route on which a
        Watchdog audit row is written at all: ``recover_stalled_run`` called bare is the
        ENGINE, it takes no audit port and writes nothing, so a replay driven through it
        can say nothing about duplicate audit rows.
        """
        code, out, err = self.watchdog("watchdog", "once",
                                       "--artifact-base", str(self.base),
                                       "--results", self.results_file(), "--json",
                                       *extra)
        self.assertEqual(code, 0, f"the delivered sweep failed: {err}")
        return self.summary(out)

    def audit_rows(self, run_id: str) -> list[tuple[str, str]]:
        """Every row in the run's Watchdog ledger, as ``(event, recovery_id)``.

        The ledger is append-only and explicitly a LOG rather than a deduplicator
        (``watchdog_audit`` module docstring), so "no duplicate row" is a statement about
        the rows that NAME AN ATTEMPT -- ``IDENTITY_BEARING_WATCHDOG_EVENTS`` -- not about
        the per-sweep bookkeeping, which is supposed to grow once per sweep.
        """
        from scripts.deterministic_workflow import watchdog_audit
        return [(str(row.get("event")), str(row.get("recovery_id") or ""))
                for row in watchdog_audit.read_watchdog_audit(run_id, base=self.base)]

    def attempt_rows(self, run_id: str) -> list[tuple[str, str]]:
        from scripts.deterministic_workflow.watchdog_audit import (
            IDENTITY_BEARING_WATCHDOG_EVENTS)
        return [row for row in self.audit_rows(run_id)
                if row[0] in IDENTITY_BEARING_WATCHDOG_EVENTS]

    def winner(self, run_id: str) -> dict[str, str]:
        """The durable authority record's WINNER FIELDS, re-read from disk on every call.

        Deliberately a reader and never a cached value: the F-001 this replaces passed
        because a value captured BEFORE one action was compared AFTER a different one, so
        every "after" in this file is produced by calling this again, not by keeping the
        dict it returned earlier.
        """
        record = recovery_store.store_for(run_id, artifact_base=self.base).read(run_id)
        return {"status": str((record or {}).get("status") or ""),
                "claimant_id": str((record or {}).get("claimant_id") or ""),
                "lease_token": str((record or {}).get("lease_token") or ""),
                "owner_kind": str((record or {}).get("owner_kind") or "")}

    def coordinator_audit(self, run_id: str) -> dict[str, bytes]:
        """The COORDINATOR's own published audit set for the run, verbatim.

        ``launcher.execute_state`` publishes it through ``audit.RunLoggingAuditSink`` when
        no ``audit_sink`` is supplied -- the delivered default.  Compared as published
        BYTES so a second, differing row for one key would show up as a changed value and
        not only as a changed count.
        """
        from scripts import run_logging
        directory = run_logging.audit_outbox_dir(run_id, base=self.base)
        if not directory.is_dir():
            return {}
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in sorted(directory.rglob("*")) if path.is_file()}

    # -- the race ------------------------------------------------------------------------
    def race(self) -> Any:
        from types import SimpleNamespace

        before = self.stall(self.RUN)
        effects: list[str] = []
        invocations: list[str] = []
        errors: list[str] = []
        outcomes: dict[str, Any] = {}
        observed: dict[str, Any] = {}
        parked, released, done = (threading.Event(), threading.Event(),
                                  threading.Event())
        barrier = threading.Barrier(2, timeout=self.RENDEZVOUS_TIMEOUT)
        request = self.watchdog_request(effects=effects, invocations=invocations)

        def watchdog() -> None:
            try:
                # ---- 1 + 2: observe, then STOP, before recovery begins ----------------
                snapshot = self.observe()
                observed["state"] = classify(snapshot).state
                observed["liveness"] = snapshot.liveness_status
                done.set()                            # "W has observed"
                barrier.wait()                        # ---- 4: released ----------------
                outcomes["W"] = recovery_runtime.recover_stalled_run(
                    request, store=self.authority("W"))
            except BaseException as exc:              # reported, never swallowed
                errors.append(f"W: {type(exc).__name__}: {exc}")
            finally:
                released.set()

        watcher = threading.Thread(target=watchdog, name="watchdog-W")
        watcher.start()
        self.assertTrue(done.wait(timeout=self.RENDEZVOUS_TIMEOUT),
                        "the Watchdog never completed its observation")
        self.assertEqual(observed.get("state"), STALLED_RECOVERABLE,
                         f"W must classify the run STALLED_RECOVERABLE; got {observed}")
        self.assertEqual(observed.get("liveness"), coordinator_liveness.LIVENESS_EXPIRED)

        # ---- 3: A revives and resumes the SAME checkpoint --------------------------------
        coordinator = threading.Thread(
            target=self.coordinator,
            kwargs={"effects": effects, "parked": parked, "release": released,
                    "outcomes": outcomes, "errors": errors},
            name="coordinator-A")
        coordinator.start()
        self.assertTrue(parked.wait(timeout=self.RENDEZVOUS_TIMEOUT),
                        "A never reached its external effect, so W was never released "
                        "into a genuinely executing Coordinator")
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait()                            # ---- 4: release W ----------------
        # Bounded, and only so a BROKEN exclusion fails instead of hanging the suite: in a
        # green run W refuses at the atomic claim and this join returns immediately.
        watcher.join(timeout=self.RENDEZVOUS_TIMEOUT * 3)
        released.set()                                # W has settled; A may finish
        coordinator.join(timeout=self.RENDEZVOUS_TIMEOUT * 3)
        for thread in (watcher, coordinator):
            self.assertFalse(thread.is_alive(), f"{thread.name} never returned")
        return SimpleNamespace(before=before, effects=effects, invocations=invocations,
                               errors=errors, outcomes=outcomes, request=request)


@REQUIRES_LANGGRAPH
class CoordinatorWatchdogExclusionTests(_ExclusionFixture):
    """The TOCTOU race itself: the Watchdog looked, the Coordinator revived, one won."""

    def test_exactly_one_party_performs_the_graph_transition_and_the_side_effect(self):
        """5.  Both parties reached the checkpoint; exactly one may drive it."""
        result = self.race()
        self.assertEqual(result.errors, [], "neither party may fail unexpectedly")
        self.assertTrue(result.effects, "the winner must really have dispatched work")
        self.assertEqual(
            sorted(set(result.effects)), ["A"],
            "every external effect on this run belongs to ONE party; the Coordinator "
            "claimed the run's execution authority first, so the Watchdog must have "
            f"created none -- effects={result.effects}")
        self.assertEqual(
            result.invocations, [],
            "the Watchdog must be refused BEFORE the graph transition; entering "
            "graph.invoke at all means two parties drove one checkpoint")
        self.assertEqual(result.outcomes["A"]["terminal_status"], "COMPLETED",
                         "the winner's ordinary execution must not be regressed")
        self.assertNotEqual(self.head(self.RUN), result.before,
                            "the winner really advanced the run")

    def test_the_loser_receives_an_explicit_stable_refusal_code(self):
        """6.  The loser neither waits nor proceeds, and says why by name."""
        result = self.race()
        outcome = result.outcomes["W"]
        self.assertEqual(outcome.status, recovery_runtime.CONFLICT)
        self.assertEqual(outcome.code, EXECUTION_AUTHORITY_HELD)
        self.assertFalse(outcome.effect_performed)
        self.assertEqual(outcome.resumed_checkpoint_id, "")
        self.assertIn(self.RUN, outcome.detail)

    def test_replay_and_restart_produce_no_duplicate_transition_dispatch_or_audit_row(self):
        """7.  The winner is durable: a replay changes nothing and duplicates nothing.

        Driven through the DELIVERED AUDITED COMPOSITION -- ``run_watchdog_cli`` ->
        ``_watchdog_wiring`` -> ``watchdog_supervisor.sweep`` -> ``FileWatchdogAudit`` --
        and, for the Coordinator, through ``execute_state`` with its DEFAULT audit sink.
        A replay driven through ``recover_stalled_run`` with ``audit_sink=None`` exercises
        no audit writer at all, so it can only assert about the transition and the
        dispatch; the audit row is the third thing step 7 requires, and only the shipped
        composition writes one.

        ``run_control`` is the POSITIVE CONTROL, swept by the SAME command in the SAME
        sweep: it is genuinely stalled, so it must gain attempt rows.  Without it, "the
        settled run gained no attempt row" is equally satisfied by an audit port that was
        never wired.
        """
        result = self.race()
        settled_head = self.head(self.RUN)
        effects_before = list(result.effects)
        record = recovery_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        attempts = dict((record or {}).get("attempts") or {})
        # The race itself never touched the Watchdog ledger: it drove the engine directly.
        self.assertEqual(self.audit_rows(self.RUN), [],
                         "the race writes no watchdog audit row; anything here already "
                         "means this assertion is measuring the wrong ledger")
        control_before = self.stall("run_control")

        # ---- replay: TWO sweeps of the delivered, audited Watchdog ----------------------
        first = self.audited_sweep()
        self.assertEqual(self.row(first, "run_control")["outcome_status"],
                         recovery_runtime.RECOVERED,
                         "the positive control must really be recovered by this sweep, or "
                         "the composition under test is not doing anything")
        self.assertNotEqual(self.head("run_control"), control_before)
        self.assertTrue(self.attempt_rows("run_control"),
                        "the delivered composition must write attempt-bearing audit rows "
                        "for a run it recovers; otherwise a zero count below proves "
                        "nothing about idempotence")
        self.assertFalse(self.row(first, self.RUN)["acted"],
                         "the settled run must not be acted on a second time")
        second = self.audited_sweep()
        self.assertFalse(self.row(second, self.RUN)["acted"])

        self.assertEqual(self.head(self.RUN), settled_head,
                         "a replayed recovery may not move the head a second time")
        # ``result.invocations`` is appended to by the race's OWN graph factory, which the
        # swept composition never calls, so it speaks for the RACE and not for the sweeps:
        # the head above is what constrains them.  Asserted here as the race's standing
        # property, and named as such so it is not read as replay evidence it cannot give.
        self.assertEqual(result.invocations, [],
                         "the Watchdog never entered graph.invoke during the race")
        self.assertEqual(
            self.attempt_rows(self.RUN), [],
            "two sweeps of the delivered audited composition added an attempt-bearing "
            f"watchdog audit row to a settled run: {self.attempt_rows(self.RUN)}")
        from scripts.deterministic_workflow import watchdog_audit
        self.assertEqual(watchdog_audit.FileWatchdogAudit(self.base).fold(self.RUN), {},
                         "the folded watchdog ledger must name no recovery attempt for a "
                         "run the Coordinator settled")

        # ---- restart: the Coordinator, over the same settled checkpoint ------------------
        # ``audit_sink`` is NOT passed, so ``execute_state`` installs the delivered
        # ``RunLoggingAuditSink`` and the restart is audited exactly as production is.
        coordinator_audit_before = self.coordinator_audit(self.RUN)
        # F-001.  Every baseline the restart is judged against is taken HERE, immediately
        # before the restart and after the sweeps -- never carried down from ``race()`` --
        # so what these compare is the restart and nothing else.
        head_before_restart = self.head(self.RUN)
        winner_before_restart = self.winner(self.RUN)
        self.assertEqual(
            winner_before_restart["status"], "SETTLED",
            "the Coordinator's terminal completion must be DURABLE before the restart is "
            "even attempted; an ACTIVE record here is a run any claimant may take as new "
            f"work -- {winner_before_restart}")
        self.assertEqual(winner_before_restart["owner_kind"],
                         recovery_store.OWNER_KIND_COORDINATOR,
                         "the settled record must still name the role that won")
        self.assertTrue(winner_before_restart["claimant_id"],
                        "a settled record with no claimant names no winner at all")
        ledger, journal = self.bindings()
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        restart = launcher.execute_state(
            dict(initial_state(run_id=self.RUN, thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES)),
            adapter=_Recording(FakeAdapter([dict(item) for item in RESULTS],
                                           runtime_state=ledger, run_id=self.RUN,
                                           settlement_journal=journal),
                               owner="A_restart", effects=result.effects),
            runtime_state=ledger, journal=journal,
            artifact_base=self.base, execution_authority=self.authority("A"),
            checkpoint_store_path=self.runs / self.RUN / ".workflow_checkpoints.json")

        self.assertEqual(sorted(set(result.effects)), ["A"],
                         "a replay or a restart may not create a second dispatch; every "
                         f"effect still belongs to the winner -- {result.effects}")
        self.assertEqual(result.effects, effects_before,
                         "a replay or a restart may not add ANY dispatch")
        after = recovery_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        self.assertEqual(dict((after or {}).get("attempts") or {}), attempts,
                         "no replay and no restart may add an attempt row")
        self.assertEqual(self.coordinator_audit(self.RUN), coordinator_audit_before,
                         "an audited restart may not add or rewrite a single Coordinator "
                         "audit record")
        self.assertEqual(self.attempt_rows(self.RUN), [],
                         "the restart may not add a watchdog attempt row either")
        self.assertEqual(restart["terminal_status"], "COMPLETED",
                         "a restart reaches the same settled outcome, not a new one")

        # ---- F-001: the durable state, RE-READ after the restart -----------------------
        # The three values below are read from disk HERE, after the action they constrain.
        # Before this, the head and the winner fields were never looked at again once the
        # restart had run, so a restart that rotated the claimant, rotated the fencing
        # token and advanced the checkpoint a second time passed every assertion above.
        self.assertEqual(
            self.head(self.RUN), head_before_restart,
            "R6: a Coordinator restart over a run that already finished may not perform a "
            "second graph transition; the committed head moved")
        winner_after_restart = self.winner(self.RUN)
        self.assertEqual(
            winner_after_restart, winner_before_restart,
            "R6: a restart may not change the winner. The durable record's status, "
            "claimant_id, owner_kind and lease_token must all still be the ones the "
            f"winner left -- before={winner_before_restart} after={winner_after_restart}")
        self.assertEqual(
            winner_after_restart["claimant_id"], winner_before_restart["claimant_id"],
            "a restart that records a fresh per-attempt claimant has taken the run as new "
            "work, which is exactly what settling it is supposed to prevent")
        self.assertEqual(
            winner_after_restart["lease_token"], winner_before_restart["lease_token"],
            "a restart that rotates the fencing token has re-claimed the authority; the "
            "settled record must not be reclaimable by anyone, of either role")


    def test_an_INTERRUPTED_coordinator_is_NOT_sealed_and_STAYS_recoverable(self):
        """8.  The other half of durable completion, and the one that can go silently wrong.

        Sealing a run on completion is only safe if "finished" can never be confused with
        "stopped short of finishing".  Here the SHIPPED Coordinator entry point stops
        INSIDE the run -- interrupted before ``EXECUTE_INTENT``, the same shape a crashed
        Coordinator leaves and the same shape ``stall()`` builds -- so its committed head
        carries no ``terminal_status`` and its own routing still owes a next node.

        Two things are then asserted by RE-READING durable state after that execution: the
        authority is NOT ``SETTLED``, and the delivered Watchdog really does recover the
        run.  The second is the load-bearing one.  A fix that sealed too eagerly would
        leave the record unclaimable, every sweep would report nothing to do, and the
        Watchdog would be INERT -- which is precisely the regression DI-3 already cost
        this project, and it would look like a green suite without this test.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph

        run = "run_interrupted"
        root = self.runs / run
        root.mkdir(parents=True, exist_ok=True)
        store_path = root / ".workflow_checkpoints.json"
        ledger = FileRuntimeStateStore(launcher.default_runtime_state_path(run, "t"))
        journal = pause_store.journal_for(run, artifact_base=self.base)
        # The SAME injection rule the race uses: an identity when this suite states one,
        # and nothing at all in the production-default variant.
        authority = (None if self.OWNERS is None
                     else recovery_store.FileRecoveryStateStore(
                         recovery_store.authority_path_for_checkpoint(store_path),
                         owner_id=self.OWNERS["A"]))
        launcher.execute_state(
            dict(initial_state(run_id=run, thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES)),
            adapter=FakeAdapter([dict(item) for item in RESULTS], runtime_state=ledger,
                                run_id=run, settlement_journal=journal),
            runtime_state=ledger, journal=journal, audit_sink=None,
            artifact_base=self.base, execution_authority=authority,
            interrupt_before=["EXECUTE_INTENT"], checkpoint_store_path=store_path)

        # ---- the durable evidence, re-read AFTER the interrupted execution -------------
        head = recovery_runtime.resolve_head(run, artifact_base=self.base)
        self.assertIsNotNone(head, "the interrupted run must have committed a head")
        self.assertFalse(head.state.get("terminal_status"),
                         "this run did not finish; a terminal_status here means the "
                         "fixture stopped it in the wrong place and proves nothing")
        self.assertTrue(head.next_node,
                        "the interrupted head's OWN routing must still owe a next node, "
                        "or there is no work for a Watchdog to recover")
        interrupted = self.winner(run)
        self.assertNotEqual(
            interrupted["status"], "SETTLED",
            "an execution that stopped short of a terminal head may NOT be sealed; "
            f"sealing it makes the Watchdog inert -- record={interrupted}")

        # ---- and the delivered Watchdog really recovers it -----------------------------
        effects: list[str] = []
        invocations: list[str] = []
        adapter = _Recording(FakeAdapter([dict(item) for item in RESULTS],
                                         runtime_state=ledger, run_id=run,
                                         settlement_journal=journal),
                             owner="W", effects=effects)

        def factory(saver: Any) -> Any:
            return _LoggingGraph(build_graph(adapter, checkpointer=saver,
                                             runtime_state=ledger, journal=journal),
                                 owner="W", log=invocations)

        before = head.head_checkpoint_id
        outcome = recovery_runtime.recover_stalled_run(
            recovery_runtime.RecoveryRequest(run_id=run, artifact_base=str(self.base),
                                             graph_factory=factory,
                                             actor_id="watchdog_W"),
            store=(None if self.OWNERS is None
                   else recovery_store.FileRecoveryStateStore(
                       recovery_store.recovery_record_path(run,
                                                           artifact_base=self.base),
                       owner_id=self.OWNERS["W"])))
        self.assertEqual(
            outcome.status, recovery_runtime.RECOVERED,
            "a genuinely interrupted run must still be recoverable after the Coordinator "
            f"let go of it -- got {outcome.status}/{outcome.code}: {outcome.detail}")
        self.assertEqual(invocations, ["W"],
                         "the recovery must really have driven the graph")
        after = recovery_runtime.resolve_head(run, artifact_base=self.base)
        self.assertNotEqual(after.head_checkpoint_id, before,
                            "the recovery must really have advanced the run")

        # ---- F-001 (iteration 5): and then the COORDINATOR restarts over it -------------
        # The half this test used to stop one line above.  It proved that an interrupted
        # run stays recoverable and that the Watchdog advances it, and then ended -- so the
        # durable state after the action that matters, a WATCHDOG that FINISHED, was never
        # looked at.  It is looked at here, and then the ordinary Coordinator path is run
        # over the same checkpoint and the same durable stores.
        self.assertTrue(after.state.get("terminal_status"),
                        "the recovery drove this run to completion, so its committed head "
                        "must carry a terminal_status; without that the settlement below "
                        "is not the case this test means to cover")
        self.assertFalse(after.next_node,
                         "a finished run's own routing owes no next node")
        recovered = self.winner(run)
        self.assertEqual(
            recovered["status"], "SETTLED",
            "a WATCHDOG that drove a run to a terminal committed head must leave the "
            "authority SETTLED, exactly as a Coordinator that finished does; an ACTIVE "
            f"record here is a completed run any claimant may take as new work {recovered}")
        self.assertEqual(recovered["owner_kind"], recovery_store.OWNER_KIND_RECOVERY,
                         "the settled record must still name the role that won")
        effects_before, head_before = list(effects), self.head(run)
        coordinator_audit_before = self.coordinator_audit(run)
        restart = launcher.execute_state(
            dict(initial_state(run_id=run, thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES)),
            adapter=_Recording(FakeAdapter([dict(item) for item in RESULTS],
                                           runtime_state=ledger, run_id=run,
                                           settlement_journal=journal),
                               owner="A_restart", effects=effects),
            runtime_state=ledger, journal=journal, artifact_base=self.base,
            execution_authority=authority, checkpoint_store_path=store_path)
        self.assertEqual(self.head(run), head_before,
                         "R6: a Coordinator restart over a run the WATCHDOG finished may "
                         "not perform a second graph transition")
        self.assertEqual(self.winner(run), recovered,
                         "R6: a Coordinator restart may not rotate the WATCHDOG winner's "
                         f"status, claimant_id, owner_kind or lease_token {self.winner(run)}")
        self.assertEqual(effects, effects_before,
                         f"the restart created a second external effect -- {effects}")
        self.assertEqual(invocations, ["W"],
                         "only the Watchdog ever entered graph.invoke on this run")
        self.assertEqual(self.coordinator_audit(run), coordinator_audit_before,
                         "the restart may not add or rewrite a Coordinator audit record")
        self.assertEqual(restart["terminal_status"], "COMPLETED",
                         "a restart reports the outcome the run already reached")


@REQUIRES_LANGGRAPH
class SameProcessCoordinatorWatchdogExclusionTests(CoordinatorWatchdogExclusionTests):
    """F-001.  The SAME race, with NOTHING about either identity supplied by this file.

    The suite above states two separate-process identities.  That topology is real, and it
    stays covered -- but it is not the only one the product offers, and it is not the one
    the requirement is written against: R1/R2/R5/R6 name two ACTORS, and
    ``watchdog_supervisor`` is runtime-neutral and callable in-process by construction
    (CON-5).  Two actors in ONE process therefore have to be serialised too, and a fixture
    that injects ``host:pid7001`` / ``host:pid7002`` cannot show that: it MANUFACTURES the
    distinct identity the exclusion depends on, so it proves the property it assumed
    instead of the property the product has.

    Here ``OWNERS`` is ``None``: neither party is handed an authority, so
    ``launcher.execute_state`` derives its own from the checkpoint store it is about to
    advance and ``recovery_runtime.recover_stalled_run`` derives its own from the run root
    -- both from ``runtime_state.default_owner_id``, both in this process, therefore both
    with the SAME ``owner_id``.  Whatever separates them is the product's, not the test's.
    """

    OWNERS = None

@REQUIRES_LANGGRAPH
class CoordinatorIsRefusedTests(_ExclusionFixture):
    """The mirror image: the Watchdog got there first, so the COORDINATOR is the loser.

    R5 is symmetric or it is nothing.  A Coordinator that finds the run owned must fail
    closed with the same stable code and perform nothing -- it may not wait for the
    holder, and it may not decide that its own liveness entitles it to proceed.
    """

    def test_a_coordinator_that_finds_the_run_owned_fails_closed_and_dispatches_nothing(self):
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter

        before = self.stall(self.RUN)
        # A genuine, live recovery lease held by another process.  Nothing is scripted:
        # this is the production claim on the production record.
        held = self.authority("W").claim(
            self.RUN, thread_id="t", checkpoint_ns="", now_iso="2026-01-01T00:00:00Z",
            owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        self.assertEqual(held["claim_outcome"], recovery_store.CREATED)

        effects: list[str] = []
        ledger, journal = self.bindings()
        final = launcher.execute_state(
            dict(initial_state(run_id=self.RUN, thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES)),
            adapter=_Recording(FakeAdapter([dict(item) for item in RESULTS],
                                           runtime_state=ledger, run_id=self.RUN,
                                           settlement_journal=journal),
                               owner="A", effects=effects),
            runtime_state=ledger, journal=journal, audit_sink=None,
            artifact_base=self.base, execution_authority=self.authority("A"),
            checkpoint_store_path=self.runs / self.RUN / ".workflow_checkpoints.json")

        self.assertEqual(final["terminal_status"], "BLOCKED")
        self.assertEqual(final["terminal_reason"]["code"], EXECUTION_AUTHORITY_HELD)
        self.assertEqual(effects, [], "a refused Coordinator dispatches nothing")
        self.assertEqual(self.head(self.RUN), before,
                         "a refused Coordinator performs no graph transition")
        self.assertEqual(launcher.EXIT_CODES[final["terminal_status"]], 1)

    def test_a_coordinator_that_lost_the_authority_performs_NO_graph_transition(self):
        """R4, the pre-transition check: validated again immediately before ``invoke``.

        The authority is taken and then lost between the claim and the transition -- the
        exact window a claim-time-only check cannot see.  The run must stop having
        committed NOTHING: not a dispatch, and not a superstep.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter

        before = self.stall(self.RUN)
        effects: list[str] = []
        ledger, journal = self.bindings()

        class _LostOnClaim(recovery_store.FileRecoveryStateStore):
            """The production store, with the lease taken away the instant it is granted.

            Nothing about the claim or the fence is replaced: a real successor really
            lapses and re-claims the real record, which rotates the token exactly as any
            takeover does.
            """

            def claim(inner, run_id: str, **kwargs: Any):        # noqa: N805
                granted = super().claim(run_id, **kwargs)
                successor = self.authority("W")
                successor.release(run_id, granted["lease_token"])
                successor.claim(run_id, thread_id="t", checkpoint_ns="",
                                now_iso="2026-01-01T00:00:00Z",
                                owner_kind=recovery_store.OWNER_KIND_RECOVERY)
                return granted

        final = launcher.execute_state(
            dict(initial_state(run_id=self.RUN, thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES)),
            adapter=_Recording(FakeAdapter([dict(item) for item in RESULTS],
                                           runtime_state=ledger, run_id=self.RUN,
                                           settlement_journal=journal),
                               owner="A", effects=effects),
            runtime_state=ledger, journal=journal, audit_sink=None,
            artifact_base=self.base,
            execution_authority=_LostOnClaim(
                recovery_store.authority_path_for_checkpoint(
                    self.runs / self.RUN / ".workflow_checkpoints.json"),
                owner_id=self.OWNERS["A"]),
            checkpoint_store_path=self.runs / self.RUN / ".workflow_checkpoints.json")

        self.assertEqual(final["terminal_status"], "BLOCKED")
        self.assertEqual(final["terminal_reason"]["code"], "EXECUTION_AUTHORITY_LOST")
        self.assertEqual(effects, [])
        self.assertEqual(self.head(self.RUN), before,
                         "a Coordinator refused at the transition may not have committed "
                         "a single superstep")

    def test_a_superseded_coordinator_stops_before_its_NEXT_external_effect(self):
        """R4.  The token is validated before every effect, not only at the claim.

        The Coordinator wins the claim and starts working; its lease is then lapsed and
        taken over by a successor, which ROTATES the token.  The effect already in flight
        stands -- it really happened -- but the run stops at the next external-effect node
        instead of finishing work the successor now owns.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter

        self.stall(self.RUN)
        effects: list[str] = []
        parked, release = threading.Event(), threading.Event()
        ledger, journal = self.bindings()
        adapter = _Parking(
            FakeAdapter([dict(item) for item in RESULTS], runtime_state=ledger,
                        run_id=self.RUN, settlement_journal=journal),
            owner="A", effects=effects, parked=parked, release=release,
            timeout=self.RENDEZVOUS_TIMEOUT)
        final: dict[str, Any] = {}

        def coordinator() -> None:
            try:
                final.update(launcher.execute_state(
                    dict(initial_state(run_id=self.RUN, thread_id="t",
                                       phases=("ANALYSIS",),
                                       capabilities=BASE_CAPABILITIES)),
                    adapter=adapter, runtime_state=ledger, journal=journal,
                    audit_sink=None, artifact_base=self.base,
                    execution_authority=self.authority("A"),
                    checkpoint_store_path=(self.runs / self.RUN
                                           / ".workflow_checkpoints.json")))
            finally:
                parked.set()

        worker = threading.Thread(target=coordinator, name="coordinator-A")
        worker.start()
        self.assertTrue(parked.wait(timeout=self.RENDEZVOUS_TIMEOUT),
                        "A never reached its first external effect")
        # Lapse A's lease and take it over: ``claim`` rotates the token, and that rotation
        # IS the fence.  A's own keeper renews only every lease/3 seconds, so this whole
        # sequence happens inside one renewal period.
        successor = self.authority("W")
        current = successor.read(self.RUN) or {}
        successor.release(self.RUN, current["lease_token"])
        successor.claim(self.RUN, thread_id="t", checkpoint_ns="",
                        now_iso="2026-01-01T00:00:00Z",
                        owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        release.set()
        worker.join(timeout=self.RENDEZVOUS_TIMEOUT * 3)
        self.assertFalse(worker.is_alive(), "the superseded Coordinator never returned")

        self.assertEqual(final.get("terminal_status"), "BLOCKED")
        self.assertEqual(final["terminal_reason"]["code"], "EXECUTION_AUTHORITY_LOST")
        self.assertEqual(effects, ["A"],
                         "the effect already in flight stands; the NEXT one is refused")


class AuthorityClaimContractTests(unittest.TestCase):
    """R7 as a unit: what changed for Coordinator-vs-Watchdog, and what did NOT change."""

    def setUp(self) -> None:
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib_path(self.tmp.name) / ".recovery_state.json"

    def store(self, owner: str):
        return recovery_store.FileRecoveryStateStore(self.path, owner_id=owner)

    # -- F-001: the claimant identity is the ACTOR, not the process --------------------
    def production_store(self):
        """A store built the PRODUCTION way: nothing about its identity supplied here."""
        return recovery_store.FileRecoveryStateStore(self.path)

    def test_two_actors_in_ONE_process_are_two_claimants_and_serialise(self):
        """The defect, as a unit: same process, production-default identities, both ways.

        ``default_owner_id`` names the PROCESS, so these two stores carry the SAME
        ``owner_id`` -- asserted, not assumed, because if it ever stopped being true this
        test would silently stop covering the case it exists for.  The exclusion must hold
        anyway: the claimant is the actor.
        """
        coordinator, watchdog = self.production_store(), self.production_store()
        self.assertEqual(coordinator.owner_id, watchdog.owner_id,
                         "these two stores must share a process identity, or this test is "
                         "no longer about the in-process seam")
        granted = coordinator.claim("run_x",
                                    owner_kind=recovery_store.OWNER_KIND_COORDINATOR,
                                    takeover=False)
        self.assertEqual(granted["claim_outcome"], recovery_store.CREATED)
        with self.assertRaises(recovery_store.RecoveryAuthorityHeld) as caught:
            watchdog.claim("run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        self.assertIn(EXECUTION_AUTHORITY_HELD, str(caught.exception))
        # The refused claim must also have left the winner's fence INTACT.  A claim that
        # refuses but still rotates the token would stop the Coordinator at its next
        # irreversible step -- a second way for the run to stop dead.
        self.assertEqual(coordinator.fence("run_x", granted["lease_token"])["run_id"],
                         "run_x")

    def test_the_same_seam_refuses_the_COORDINATOR_when_recovery_got_there_first(self):
        """R5 is symmetric in one process too, not only across two."""
        watchdog, coordinator = self.production_store(), self.production_store()
        watchdog.claim("run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        with self.assertRaises(recovery_store.RecoveryAuthorityHeld) as caught:
            coordinator.claim("run_x",
                              owner_kind=recovery_store.OWNER_KIND_COORDINATOR,
                              takeover=False)
        self.assertIn(EXECUTION_AUTHORITY_HELD, str(caught.exception))

    def test_resumption_requires_the_CONTINUATION_TOKEN_and_not_a_resemblance(self):
        """The other half of the fix: resumption still works, but it is now EXPLICIT.

        The claim this replaces asked "does the holder LOOK like me?" and inferred the
        answer from identity -- first the process, then the process and the role.  Both
        readings admitted a second CONCURRENT actor as the holder resuming its own work,
        because every identity a claimant can be compared on is one a peer can also hold.
        So the inference is gone, and this test states the two halves that replace it, for
        BOTH roles:

        * a same-process, same-role, production-default peer that presents NOTHING is a
          second claimant and is refused while the holder is live; and
        * the holder's own continuation -- the lease token its claim minted and returned
          to it -- still resumes, so separating the actors did not turn intentional
          resumption into a deadlock.
        """
        for kind in (recovery_store.OWNER_KIND_COORDINATOR,
                     recovery_store.OWNER_KIND_RECOVERY):
            with self.subTest(kind=kind):
                path = self.path.with_name(f"{kind}.recovery_state.json")
                first = recovery_store.FileRecoveryStateStore(path)
                second = recovery_store.FileRecoveryStateStore(path)
                self.assertEqual(first.owner_id, second.owner_id,
                                 "these two stores must share a process identity, or "
                                 "this test is no longer about the in-process seam")
                self.assertNotEqual(first.claimant_id, second.claimant_id,
                                    "two live execution attempts are two claimants")
                granted = first.claim("run_x", owner_kind=kind)
                self.assertEqual(granted["claim_outcome"], recovery_store.CREATED)
                self.assertEqual(granted["claimant_id"], first.claimant_id,
                                 "the durable record must name the ATTEMPT that won")
                with self.assertRaises(recovery_store.RecoveryClaimHeld):
                    second.claim("run_x", owner_kind=kind)
                resumed = second.claim("run_x", owner_kind=kind,
                                       continuation_token=granted["lease_token"])
                self.assertEqual(resumed["claim_outcome"], recovery_store.RESUMED)
                self.assertNotEqual(resumed["lease_token"], granted["lease_token"],
                                    "a resumed claim still rotates the fence")

    def test_a_continuation_token_this_record_does_not_KNOW_is_refused(self):
        """A stale continuation is a LOST claim, never a licence to take the run afresh.

        Without this, "present a token" would degrade into "present anything": a claimant
        whose claim had already been superseded would fall through to an ordinary claim
        and take the run back, which is the supersession the fence exists to stop.
        """
        holder = self.production_store()
        granted = holder.claim("run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        # Something rotates the token: here, a continuation presented by a successor that
        # really does hold it.  The holder's copy is now stale.
        self.production_store().claim(
            "run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY,
            continuation_token=granted["lease_token"])
        with self.assertRaises(recovery_store.RecoveryClaimLost):
            holder.claim("run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY,
                         continuation_token=granted["lease_token"])

    def test_a_continuation_may_not_re_cast_the_ROLE_the_record_published(self):
        """A challenger decides whether it may observe from the holder's role.

        Letting a continuation rewrite ``owner_kind`` would let a live Coordinator
        re-publish itself as a recovery attempt -- and a recovery attempt is precisely the
        thing another Watchdog is entitled to observe and take over.
        """
        holder = self.production_store()
        granted = holder.claim("run_x",
                               owner_kind=recovery_store.OWNER_KIND_COORDINATOR,
                               takeover=False)
        with self.assertRaises(recovery_store.RecoveryStoreError):
            holder.claim("run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY,
                         continuation_token=granted["lease_token"])

    def test_a_LAPSED_in_process_holder_is_still_takeable_over(self):
        """Actor scoping is a claimant identity, never a second lease that outlives one.

        A Coordinator that died holds an expired lease, and the run is then legitimately
        recoverable -- by a recovery attempt in ANY process, including this one.
        """
        dead = recovery_store.FileRecoveryStateStore(self.path, lease_seconds=0.0)
        dead.claim("run_x", owner_kind=recovery_store.OWNER_KIND_COORDINATOR,
                   takeover=False)
        taken = self.production_store().claim(
            "run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        self.assertEqual(taken["claim_outcome"], recovery_store.RESUMED)
        self.assertEqual(taken["owner_kind"], recovery_store.OWNER_KIND_RECOVERY)

    def test_watchdog_vs_watchdog_still_OBSERVES_a_live_recovery_lease(self):
        """The delivered ladder is untouched: a live RECOVERY holder is observable."""
        self.store("host:pid1").claim("run_x", owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        with self.assertRaises(recovery_store.RecoveryClaimHeld) as caught:
            self.store("host:pid2").claim("run_x",
                                          owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        self.assertNotIsInstance(caught.exception, recovery_store.RecoveryAuthorityHeld)
        self.assertIn("RECOVERY_CLAIM_HELD", str(caught.exception))

    def test_a_live_COORDINATOR_holder_is_never_observable_by_anyone(self):
        self.store("host:pid1").claim(
            "run_x", owner_kind=recovery_store.OWNER_KIND_COORDINATOR)
        for kind in (recovery_store.OWNER_KIND_RECOVERY,
                     recovery_store.OWNER_KIND_COORDINATOR):
            with self.assertRaises(recovery_store.RecoveryAuthorityHeld) as caught:
                self.store("host:pid2").claim("run_x", owner_kind=kind)
            self.assertIn(EXECUTION_AUTHORITY_HELD, str(caught.exception))

    def test_a_claimant_that_declares_no_takeover_never_observes_anything(self):
        self.store("host:pid1").claim("run_x",
                                      owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        with self.assertRaises(recovery_store.RecoveryAuthorityHeld):
            self.store("host:pid2").claim(
                "run_x", owner_kind=recovery_store.OWNER_KIND_COORDINATOR,
                takeover=False)

    def test_the_fence_refuses_a_rotated_token_and_an_absent_one_alike(self):
        store = self.store("host:pid1")
        first = store.claim("run_x",
                            owner_kind=recovery_store.OWNER_KIND_COORDINATOR)
        self.assertEqual(store.fence("run_x", first["lease_token"])["run_id"], "run_x")
        # Rotation, taken the ONE way a live claim can now be re-entered: by presenting
        # the token.  A bare second claim would be refused, which is F-001's fix and is
        # asserted above -- what is under test HERE is that whatever rotates the token
        # invalidates every copy of the old one.
        successor = self.store("host:pid1")
        rotated = successor.claim("run_x",
                                  owner_kind=recovery_store.OWNER_KIND_COORDINATOR,
                                  continuation_token=first["lease_token"])
        self.assertNotEqual(rotated["lease_token"], first["lease_token"])
        self.assertEqual(successor.fence("run_x", rotated["lease_token"])["run_id"],
                         "run_x", "the resumer holds the run it continued")
        with self.assertRaises(recovery_store.RecoveryClaimLost):
            store.fence("run_x", first["lease_token"])
        with self.assertRaises(recovery_store.RecoveryClaimRequired):
            store.fence("run_x", "")

    def test_the_fence_refuses_a_LIVE_token_presented_by_a_DIFFERENT_attempt(self):
        """The token is a capability, and the claimant is still checked beside it.

        Both halves, or the fence would authorise any attempt that came into possession
        of the winner's token -- which is the same "someone who resembles the holder may
        write" reasoning the claim itself no longer does.
        """
        holder = self.production_store()
        granted = holder.claim("run_x",
                               owner_kind=recovery_store.OWNER_KIND_RECOVERY)
        other = self.production_store()
        self.assertEqual(holder.owner_id, other.owner_id)
        with self.assertRaises(recovery_store.RecoveryClaimLost):
            other.fence("run_x", granted["lease_token"])
        self.assertEqual(holder.fence("run_x", granted["lease_token"])["run_id"],
                         "run_x", "the real holder is unaffected")

    def test_the_authority_path_is_the_run_rooted_recovery_record(self):
        """One record, or the two parties never meet on it."""
        checkpoint = recovery_store.WORKFLOW_CHECKPOINT_FILENAME
        self.assertEqual(
            recovery_store.authority_path_for_checkpoint(
                f"/base/artifacts/runs/run_x/{checkpoint}"),
            recovery_store.recovery_record_path("run_x", artifact_base="/base"))

    def test_an_unknown_owner_kind_is_refused_rather_than_recorded(self):
        with self.assertRaises(recovery_store.RecoveryStoreError):
            self.store("host:pid1").claim("run_x", owner_kind="whatever")
        with self.assertRaises(recovery_store.RecoveryStoreError):
            self.store("host:pid1").claim("run_x", owner_kind="")


def pathlib_path(value):
    from pathlib import Path
    return Path(value)


if __name__ == "__main__":                                # pragma: no cover
    unittest.main()


@REQUIRES_LANGGRAPH
class CrossRoleRestartMatrixTests(_ExclusionFixture):
    """F-001 (iteration 5).  The FOUR-WAY cross product, not one more direction.

    Three consecutive review gates failed on the MIRROR of the fix before them:
    Coordinator-vs-Watchdog was closed and Watchdog-vs-Watchdog was not; then the
    Coordinator's completion was made durable and the Watchdog's was not.  Each fix was
    correct for the case it was shown and blind to its reflection, so this suite stops
    testing directions and tests the PRODUCT: for winner in {Coordinator, Watchdog} and
    restarter in {Coordinator, Watchdog}, all four pairs, in both identity regimes.

    Every "after" value here is RE-READ from durable state after the restart, never
    carried down from before it -- the same discipline `winner()` is written for.
    """

    #: The run each pair drives.  Distinct, so a pair can never read another pair's record.
    RUNS = {("A", "A"): "run_cc", ("A", "W"): "run_cw",
            ("W", "A"): "run_wc", ("W", "W"): "run_ww"}

    # -- the two roles, each through the path it really ships -----------------------------
    def as_coordinator(self, run_id: str, *, owner: str, effects: list[str]) -> Any:
        """``launcher.execute_state``: the shipped Coordinator entry point.

        ``audit_sink`` is NOT passed, so the delivered ``RunLoggingAuditSink`` is installed
        and both the winning run and the restart are audited exactly as production is.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter

        ledger, journal = self.bindings_for(run_id)
        return launcher.execute_state(
            dict(initial_state(run_id=run_id, thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES)),
            adapter=_Recording(FakeAdapter([dict(item) for item in RESULTS],
                                           runtime_state=ledger, run_id=run_id,
                                           settlement_journal=journal),
                               owner=owner, effects=effects),
            runtime_state=ledger, journal=journal, artifact_base=self.base,
            execution_authority=self.authority_for(run_id, "A"),
            checkpoint_store_path=self.runs / run_id / ".workflow_checkpoints.json")

    def as_watchdog(self, run_id: str, *, owner: str, effects: list[str],
                    invocations: list[str]) -> Any:
        """``recover_stalled_run``: the engine the delivered supervisor invokes.

        ``_LoggingGraph`` records the fact of entering ``graph.invoke`` at all, which is
        what makes "no second transition" an observation rather than an inference from the
        head.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph

        ledger, journal = self.bindings_for(run_id)
        adapter = _Recording(FakeAdapter([dict(item) for item in RESULTS],
                                         runtime_state=ledger, run_id=run_id,
                                         settlement_journal=journal),
                             owner=owner, effects=effects)

        def factory(saver: Any) -> Any:
            return _LoggingGraph(build_graph(adapter, checkpointer=saver,
                                             runtime_state=ledger, journal=journal),
                                 owner=owner, log=invocations)

        return recovery_runtime.recover_stalled_run(
            recovery_runtime.RecoveryRequest(run_id=run_id, artifact_base=str(self.base),
                                             graph_factory=factory, actor_id=owner),
            store=self.authority_for(run_id, "W"))

    def drive(self, role: str, run_id: str, *, owner: str, effects: list[str],
              invocations: list[str]) -> Any:
        return (self.as_coordinator(run_id, owner=owner, effects=effects) if role == "A"
                else self.as_watchdog(run_id, owner=owner, effects=effects,
                                      invocations=invocations))

    # -- the shared body every pair runs ---------------------------------------------------
    def cross_role_restart(self, winner: str, restarter: str) -> None:
        run_id = self.RUNS[(winner, restarter)]
        self.stall(run_id)
        effects: list[str] = []
        invocations: list[str] = []
        self.drive(winner, run_id, owner=f"winner_{winner}", effects=effects,
                   invocations=invocations)

        # ---- the winner really finished, and the record really says so -----------------
        self.assertTrue(effects, "the winner must really have dispatched work; a pair "
                                 "whose winner did nothing proves nothing about a restart")
        settled = self.winner(run_id)
        self.assertEqual(
            settled["status"], "SETTLED",
            f"a {winner!r} winner that drove the run to a terminal committed head must "
            "leave the authority SETTLED; an ACTIVE record here is a completed run that "
            f"any claimant of either role may take as NEW WORK -- {settled}")
        self.assertEqual(
            settled["owner_kind"],
            recovery_store.OWNER_KIND_COORDINATOR if winner == "A"
            else recovery_store.OWNER_KIND_RECOVERY,
            "the settled record must still name the ROLE that won")
        self.assertTrue(settled["claimant_id"],
                        "a settled record with no claimant names no winner at all")

        # ---- every baseline is taken HERE, after the winner and before the restart ------
        head_before = self.head(run_id)
        record = recovery_store.store_for(run_id, artifact_base=self.base).read(run_id)
        attempts_before = dict((record or {}).get("attempts") or {})
        coordinator_audit_before = self.coordinator_audit(run_id)
        watchdog_rows_before = self.attempt_rows(run_id)
        effects_before = list(effects)
        invocations_before = list(invocations)

        outcome = self.drive(restarter, run_id, owner=f"restart_{restarter}",
                             effects=effects, invocations=invocations)

        # ---- and every "after" is RE-READ from durable state, after the restart ---------
        self.assertEqual(
            self.head(run_id), head_before,
            f"R6: a {restarter!r} restart over a run a {winner!r} already finished may not "
            "perform a second graph transition; the committed head moved")
        after = self.winner(run_id)
        self.assertEqual(
            after, settled,
            "R6: a restart may not change the winner. status, claimant_id, owner_kind and "
            f"lease_token must all still be the winner's -- before={settled} after={after}")
        self.assertEqual(
            after["claimant_id"], settled["claimant_id"],
            "a restart that recorded a fresh per-attempt claimant has taken the run as new "
            "work, which is exactly what settling it is supposed to prevent")
        self.assertEqual(
            after["lease_token"], settled["lease_token"],
            "a restart that rotated the fencing token has re-claimed the authority; a "
            "settled record must not be reclaimable by ANY claimant of EITHER role")
        self.assertEqual(effects, effects_before,
                         f"a {restarter!r} restart may not create a second external effect "
                         f"or a second dispatch -- {effects}")
        self.assertEqual(invocations, invocations_before,
                         "the restarter must be refused BEFORE graph.invoke; entering it "
                         "at all means two parties drove one checkpoint")
        again = recovery_store.store_for(run_id, artifact_base=self.base).read(run_id)
        self.assertEqual(dict((again or {}).get("attempts") or {}), attempts_before,
                         "a restart may not add or rewrite an attempt row")
        self.assertEqual(self.coordinator_audit(run_id), coordinator_audit_before,
                         "an audited restart may not add or rewrite a Coordinator audit "
                         "record")
        self.assertEqual(self.attempt_rows(run_id), watchdog_rows_before,
                         "a restart may not add a watchdog attempt-bearing audit row")

        # ---- the OUTCOME the restarter reports is the one the run already reached -------
        if restarter == "A":
            self.assertEqual(outcome["terminal_status"], "COMPLETED",
                             "R6: a restart reports the outcome the run already reached, "
                             "not a new one and not a refusal")
        else:
            self.assertIn(outcome.status, (recovery_runtime.NOT_RECOVERABLE,
                                           recovery_runtime.NO_EFFECT),
                          "a Watchdog over a finished run must refuse or report no effect, "
                          f"by name -- got {outcome.status}/{outcome.code}")
            self.assertFalse(outcome.effect_performed)

    def test_coordinator_wins_then_a_coordinator_restarts(self):
        self.cross_role_restart("A", "A")

    def test_coordinator_wins_then_a_WATCHDOG_restarts(self):
        self.cross_role_restart("A", "W")

    def test_WATCHDOG_wins_then_a_coordinator_restarts(self):
        """The direction FINAL REVIEW attempt 3 reproduced, and the reason this suite exists."""
        self.cross_role_restart("W", "A")

    def test_WATCHDOG_wins_then_a_WATCHDOG_restarts(self):
        self.cross_role_restart("W", "W")

    def test_the_delivered_AUDITED_sweep_adds_no_row_to_a_WATCHDOG_settled_run(self):
        """The audit half of the Watchdog-winner direction, through the shipped composition.

        ``recover_stalled_run`` called bare is the ENGINE: it takes no audit port and
        writes no row, so a replay driven through it can say nothing about duplicate audit
        rows.  Only ``run_watchdog_cli`` -> ``_watchdog_wiring`` -> ``watchdog_supervisor``
        -> ``FileWatchdogAudit`` writes one, so the replay is driven through that.

        ``run_control`` is the POSITIVE CONTROL, swept by the SAME command in the SAME
        sweep: without it, "the settled run gained no attempt row" is equally satisfied by
        an audit port that was never wired at all.
        """
        run_id = "run_watchdogsettled"
        effects: list[str] = []
        invocations: list[str] = []
        self.stall(run_id)
        self.as_watchdog(run_id, owner="winner_W", effects=effects,
                         invocations=invocations)
        self.assertEqual(self.winner(run_id)["status"], "SETTLED")
        head_before = self.head(run_id)
        effects_before = list(effects)
        self.assertTrue(effects_before, "the Watchdog winner must really have dispatched")
        control_before = self.stall("run_control")

        summary = self.audited_sweep()
        self.assertEqual(self.row(summary, "run_control")["outcome_status"],
                         recovery_runtime.RECOVERED,
                         "the positive control must really be recovered by this sweep, or "
                         "the composition under test is not doing anything")
        self.assertNotEqual(self.head("run_control"), control_before)
        self.assertTrue(self.attempt_rows("run_control"),
                        "the delivered composition must write attempt-bearing rows for a "
                        "run it recovers; otherwise a zero count below proves nothing")
        self.assertFalse(self.row(summary, run_id)["acted"],
                         "a run the WATCHDOG settled must not be acted on a second time")
        self.assertEqual(self.head(run_id), head_before,
                         "the delivered sweep moved the head of a settled run")
        self.assertEqual(self.attempt_rows(run_id), [],
                         "the delivered audited sweep added an attempt-bearing watchdog "
                         f"row to a run the Watchdog settled: {self.attempt_rows(run_id)}")
        self.assertEqual(effects, effects_before,
                         "the sweep may not create a second external effect on the run "
                         f"the Watchdog already settled -- {effects}")

    def test_BOTH_roles_end_their_hold_through_the_SAME_shared_function(self):
        """The anti-drift assertion: one discipline, not two that agree today.

        The last three gates failed because the Coordinator half and the Watchdog half were
        two implementations of one rule and one of them was updated.  This asserts the
        structural property that makes a fourth mirror impossible to introduce quietly:
        BOTH roles give up the authority through ``turn_boundary.settle_or_release`` and
        through nothing else, so a change to the rule cannot reach one role only.
        """
        from scripts.deterministic_workflow import turn_boundary

        real = turn_boundary.settle_or_release
        seen: list[tuple[str, str]] = []

        def recording(authority, run_id, lease_token, **kwargs):
            seen.append((run_id, real(authority, run_id, lease_token, **kwargs)))
            return seen[-1][1]

        for role, run_id in (("A", "run_shareda"), ("W", "run_sharedw")):
            self.stall(run_id)
            with mock.patch.object(turn_boundary, "settle_or_release", recording):
                self.drive(role, run_id, owner=f"shared_{role}", effects=[],
                           invocations=[])
            self.assertIn(
                (run_id, "SETTLED"), seen,
                f"the {role!r} role did not close its hold through "
                "turn_boundary.settle_or_release; a role with its own private release is "
                f"exactly the drift this test exists to forbid -- {seen}")
            self.assertEqual(self.winner(run_id)["status"], "SETTLED")


    def test_a_holder_that_DIES_BEFORE_the_transition_leaves_the_run_recoverable(self):
        """The third termination path, in both roles: a raise while the authority is HELD.

        Settlement is only half of "the hold is always closed".  BOTH roles used to take
        the claim and then do more work OUTSIDE the ``try`` that closes it -- the
        Coordinator built the graph and started its lease keeper there, the Watchdog
        resolved its keeper factory and its revalidation codes there -- so a raise in that
        window left the record ACTIVE with nothing releasing it, and the run was
        unrecoverable until the lease lapsed.  Both windows are now inside the held
        section, and this asserts the consequence rather than the shape: after either role
        dies before its transition, the OTHER role can still take the run and finish it.
        """
        from scripts.deterministic_workflow import graph as graph_module

        # ---- the Coordinator dies building the graph, holding the claim ----------------
        run = "run_diesearly"
        self.stall(run)
        boom = RuntimeError("the process died before the transition")
        with mock.patch.object(graph_module, "build_graph", side_effect=boom):
            with self.assertRaises(RuntimeError):
                self.as_coordinator(run, owner="A_dies", effects=[])
        held = self.winner(run)
        self.assertNotEqual(held["status"], "SETTLED",
                            f"a run nobody finished may not be sealed -- {held}")
        effects: list[str] = []
        invocations: list[str] = []
        outcome = self.as_watchdog(run, owner="W_after", effects=effects,
                                   invocations=invocations)
        self.assertEqual(
            outcome.status, recovery_runtime.RECOVERED,
            "a Coordinator that raised before its transition must have let the authority "
            "go; the Watchdog was refused instead -- "
            f"{outcome.status}/{outcome.code}: {outcome.detail}")
        self.assertEqual(invocations, ["W_after"])
        self.assertEqual(self.winner(run)["status"], "SETTLED",
                         "and the Watchdog that finished it settles it")

        # ---- the mirror: the Watchdog dies resolving its lease keeper -------------------
        mirror = "run_wdiesearly"
        self.stall(mirror)

        # ``_stale_active_codes`` is the Watchdog's own equivalent of the Coordinator's
        # graph build: work it used to do after taking the claim and before the ``try``.
        with mock.patch.object(recovery_runtime, "_stale_active_codes",
                               side_effect=RuntimeError("the watchdog died early")):
            with self.assertRaises(RuntimeError):
                recovery_runtime.recover_stalled_run(
                    recovery_runtime.RecoveryRequest(
                        run_id=mirror, artifact_base=str(self.base),
                        graph_factory=lambda saver: None, actor_id="watchdog_dies"),
                    store=self.authority_for(mirror, "W"))
        held = self.winner(mirror)
        self.assertNotEqual(held["status"], "SETTLED",
                            f"a run nobody finished may not be sealed -- {held}")
        restart = self.as_coordinator(mirror, owner="A_after", effects=[])
        self.assertEqual(
            restart["terminal_status"], "COMPLETED",
            "a Watchdog that raised before its transition must have let the authority go; "
            f"the Coordinator was refused instead -- {restart.get('terminal_reason')}")
        self.assertEqual(self.winner(mirror)["status"], "SETTLED")

    def test_an_INTERRUPTED_WATCHDOG_recovery_is_NOT_sealed_and_STAYS_recoverable(self):
        """The MIRROR of step 8, and the fourth mirror this bugfix refuses to leave open.

        Step 8 proves an interrupted COORDINATOR is not sealed.  Sealing on completion is
        only safe if "finished" can never be confused with "stopped short of finishing" --
        and that has to hold for BOTH roles, or the Watchdog becomes the half that seals a
        live run and makes itself inert (DI-3).  Here the recovery itself stops inside the
        run: its graph is interrupted before ``EXECUTE_INTENT``, so it really advances the
        checkpoint and really fails to finish it.

        Then all three consequences are asserted from durable state: the record is NOT
        sealed, a SECOND Watchdog genuinely finishes the run, and only then is it settled --
        after which a Coordinator restart changes nothing.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.graph import build_graph

        run = "run_wdinterrupted"
        before = self.stall(run)
        ledger, journal = self.bindings_for(run)
        effects: list[str] = []
        invocations: list[str] = []
        # ONE adapter and ONE result sequence across both recoveries: two adapters would
        # each replay the phase's results from the start and the run would terminate on an
        # unexpected event rather than on its own work, which is a different test.
        adapter = _Recording(FakeAdapter([dict(item) for item in RESULTS],
                                         runtime_state=ledger, run_id=run,
                                         settlement_journal=journal),
                             owner="W", effects=effects)

        def factory(saver: Any, *, stop: bool) -> Any:
            return _LoggingGraph(
                build_graph(adapter, checkpointer=saver, runtime_state=ledger,
                            journal=journal,
                            interrupt_before=["EXECUTE_INTENT"] if stop else None),
                owner="W_partial" if stop else "W_finish", log=invocations)

        outcome = recovery_runtime.recover_stalled_run(
            recovery_runtime.RecoveryRequest(
                run_id=run, artifact_base=str(self.base),
                graph_factory=lambda saver: factory(saver, stop=True),
                actor_id="watchdog_partial"),
            store=self.authority_for(run, "W"))
        self.assertEqual(outcome.status, recovery_runtime.RECOVERED,
                         f"{outcome.status}/{outcome.code}: {outcome.detail}")
        head = recovery_runtime.resolve_head(run, artifact_base=self.base)
        self.assertNotEqual(head.head_checkpoint_id, before,
                            "the interrupted recovery must really have advanced the run, "
                            "or this is not the case the test means to cover")
        self.assertFalse(head.state.get("terminal_status"),
                         "this recovery did not finish the run; a terminal_status here "
                         "means the fixture stopped it in the wrong place")
        self.assertTrue(head.next_node,
                        "the head's OWN routing must still owe a next node")
        partial_record = self.winner(run)
        self.assertNotEqual(
            partial_record["status"], "SETTLED",
            "a WATCHDOG recovery that stopped short of a terminal head may NOT be sealed; "
            f"sealing it makes every later sweep inert -- record={partial_record}")

        # ---- and a SECOND Watchdog really finishes what the first one left --------------
        finished_outcome = recovery_runtime.recover_stalled_run(
            recovery_runtime.RecoveryRequest(
                run_id=run, artifact_base=str(self.base),
                graph_factory=lambda saver: factory(saver, stop=False),
                actor_id="watchdog_finish"),
            store=self.authority_for(run, "W"))
        self.assertEqual(finished_outcome.status, recovery_runtime.RECOVERED)
        self.assertEqual(invocations, ["W_partial", "W_finish"],
                         "the second recovery must really have driven the graph")
        terminal = recovery_runtime.resolve_head(run, artifact_base=self.base)
        self.assertEqual(str(terminal.state.get("terminal_status") or ""), "COMPLETED",
                         "the second recovery must really have finished the run")
        finished = self.winner(run)
        self.assertEqual(finished["status"], "SETTLED",
                         "and once it IS finished, the record must say so")
        head_before = self.head(run)
        effects_before = list(effects)
        restart = self.as_coordinator(run, owner="A_restart", effects=effects)
        self.assertEqual(self.head(run), head_before,
                         "R6: a Coordinator restart may not move the head")
        self.assertEqual(self.winner(run), finished,
                         "R6: a Coordinator restart may not rotate the winner")
        self.assertEqual(effects, effects_before,
                         f"the restart created a second external effect -- {effects}")
        self.assertEqual(restart["terminal_status"], "COMPLETED",
                         "R6: a restart reports the outcome the run already reached")


@REQUIRES_LANGGRAPH
class SameProcessCrossRoleRestartMatrixTests(CrossRoleRestartMatrixTests):
    """The same four-way cross product with NOTHING about either identity supplied here.

    ``OWNERS is None``: each party derives its own authority the delivered way, from
    ``runtime_state.default_owner_id``, so in one process both identities are PRODUCTION
    DEFAULT and identical.  Whatever separates the two claimants is the product's, not this
    file's -- the same reason ``SameProcessCoordinatorWatchdogExclusionTests`` exists.
    """

    OWNERS = None
    RUNS = {("A", "A"): "run_ccdefault", ("A", "W"): "run_cwdefault",
            ("W", "A"): "run_wcdefault", ("W", "W"): "run_wwdefault"}
