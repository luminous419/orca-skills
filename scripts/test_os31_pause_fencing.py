"""OS-31 review regressions: resume lease fencing, pause generations, observe/lease coherence.

Three defects were reported against PR #30 and each one is pinned here by a test that FAILS
against the unfixed code:

C1  ``resume_run`` claimed a 60-second lease and never renewed it, so a second Coordinator
    could claim the same RECORDED bundle while the first was still inside ``graph.invoke()``
    and drive the same effect concurrently.
C2  ``FilePauseRecordStore.create`` returned any existing run-scoped record unconditionally,
    so the second pause of a run silently reused the first generation's record and lost its
    checkpoint pointer and request.
M3  ``DEFAULT_OBSERVE_TIMEOUT_SECONDS`` (30s) was shorter than ``DEFAULT_LEASE_SECONDS``
    (60s), so a single observe-then-takeover call could never legally reach takeover.

Everything here runs on the fake adapter with NO Orca.  The store-level suites use the
injected :class:`ManualLeaseClock` and never sleep.  The two suites that must exercise a
REAL background renewal thread pace it with an explicit short lease and explicit
``threading.Event`` synchronisation rather than with sleeps racing the clock.
"""
from __future__ import annotations

import threading
import unittest
from typing import Any

from scripts.deterministic_workflow import pause_policy, pause_runtime, pause_store
from scripts.deterministic_workflow.lease_keeper import lease_keeper_factory
from scripts.deterministic_workflow.runtime_state import ManualLeaseClock
from scripts.test_deterministic_workflow_pause import projection, record
from scripts.test_deterministic_workflow_pause_fixture import (REQUIRES_LANGGRAPH,
                                                               REVIEW_PASS, WORKER,
                                                               PauseFixture,
                                                               clarification_item)

RUN = "run_x"


class CrashInjected(RuntimeError):
    """The synthetic process death used by the crash-boundary suite."""


# ======================================================================================
# M3 -- the observation window and the lease it observes
# ======================================================================================
class ObservationLeaseCoherenceTests(unittest.TestCase):
    """MAJOR 3: an observer that gives up before takeover is legal can never take over."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "pause.json"
        self.clock = ManualLeaseClock()

    def store(self, owner="host:pid1", lease=pause_store.DEFAULT_LEASE_SECONDS):
        return pause_store.FilePauseRecordStore(self.path, clock=self.clock,
                                                owner_id=owner, lease_seconds=lease)

    def test_the_default_observation_window_covers_a_whole_lease_and_stays_bounded(self):
        self.assertGreater(pause_store.DEFAULT_OBSERVE_TIMEOUT_SECONDS,
                           pause_store.DEFAULT_LEASE_SECONDS,
                           "an observation window shorter than the lease can never "
                           "legally reach takeover")
        self.assertEqual(pause_store.DEFAULT_OBSERVE_TIMEOUT_SECONDS,
                         pause_store.DEFAULT_LEASE_SECONDS
                         + pause_store.DEFAULT_OBSERVE_GRACE_SECONDS)
        # Derived from the lease, so reconfiguring the lease cannot re-create the defect,
        # and still finite: bounded is the contract, unbounded waiting is not.
        for lease in (0.25, 12.0, 600.0):
            self.assertEqual(pause_store.observe_timeout_for(lease),
                             lease + pause_store.DEFAULT_OBSERVE_GRACE_SECONDS)

    def test_one_observe_then_takeover_call_reaches_the_takeover(self):
        """The single-call path ``takeover()`` documents, against a default lease."""
        owner, successor = self.store("host:pid1"), self.store("host:pid2")
        owner.create(record())
        owner.claim(RUN)                      # 60s lease, and this owner then dies
        settled = successor.observe(RUN)      # the DEFAULT window, no explicit value
        self.assertIsNone(settled, "the lapsed lease must be observed, not timed out")
        self.assertEqual(successor.claim(RUN)["claim_outcome"], "RESUMED")

    def test_an_explicit_shorter_window_is_honoured_and_claims_nothing(self):
        """The retry contract: a caller may still bound its own wait, and giving up is safe."""
        owner, successor = self.store("host:pid1"), self.store("host:pid2")
        owner.create(record())
        owner.claim(RUN)
        with self.assertRaises(pause_store.PauseObservationTimeout):
            successor.observe(RUN, timeout_seconds=1.0)
        self.assertEqual(owner.read(RUN)["owner_id"], "host:pid1",
                         "a timed-out observation claims nothing")
        # ...and it is a RETRY, not a verdict: the same caller observing again once the
        # lease has lapsed reaches the takeover.
        self.clock.advance(pause_store.DEFAULT_LEASE_SECONDS)
        self.assertIsNone(successor.observe(RUN, timeout_seconds=1.0))
        self.assertEqual(successor.claim(RUN)["claim_outcome"], "RESUMED")


# ======================================================================================
# C2 -- pause generations, at the store
# ======================================================================================
class PauseGenerationStoreTests(unittest.TestCase):
    """CRITICAL 2: a run pauses more than once and each pause is its own generation."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = ManualLeaseClock()
        self.store = pause_store.FilePauseRecordStore(
            Path(self.tmp.name) / "pause.json", clock=self.clock, owner_id="host:pid1")

    def generation_two(self, **overrides):
        fields = {"pause_record_id": "pause_def", "checkpoint_id": "chk_2",
                  "checkpoint_digest": "d2",
                  "projection": projection(request_id="request_2",
                                           decision_item_ids=["item_b"],
                                           binding_generation=1)}
        fields.update(overrides)
        return record(**fields)

    def resume_generation_one(self):
        self.store.create(record())
        token = self.store.claim(RUN)["lease_token"]
        self.store.mark_resumed(RUN, token, updated_at="2026-01-02T00:00:00Z")
        return token

    def test_a_second_generation_is_persisted_and_never_silently_reuses_the_first(self):
        """The reported defect, exactly: pause #2's record must reach disk."""
        self.resume_generation_one()
        created = self.store.create(self.generation_two())
        self.assertEqual(created["pause_record_id"], "pause_def")
        active = self.store.read(RUN)
        self.assertEqual(active["pause_record_id"], "pause_def")
        self.assertEqual(active["checkpoint_id"], "chk_2")
        self.assertEqual(active["status"], "WAITING_FOR_INPUT")
        self.assertEqual(active["projection"]["request_id"], "request_2")
        self.assertEqual(active["applied"], {}, "a new generation starts with no answer")

    def test_the_superseded_generation_is_retained_whole_with_its_applied_lineage(self):
        """The chosen policy: supersede the ACTIVE slot, RETAIN the answered generation."""
        token = self.store.create(record())["run_id"] and self.store.claim(RUN)["lease_token"]
        entry = {"resume_bundle_id": "bundle_1", "request_id": "request_1",
                 "items": pause_store.applied_items({"item_a": "decision_1"}),
                 "stage": "RESUMED", "recorded_at": "2026-01-01T00:00:00Z",
                 "resumed_at": "2026-01-01T00:00:01Z", "resumed_checkpoint_id": "chk_1"}
        self.store.record_applied(RUN, entry, token)
        self.store.mark_resumed(RUN, token)
        self.store.create(self.generation_two())
        history = self.store.superseded(RUN)
        self.assertEqual([row["pause_record_id"] for row in history], ["pause_abc"])
        self.assertEqual(history[0]["status"], "RESUMED")
        self.assertEqual(list(history[0]["applied"]), ["bundle_1"],
                         "the consumption lineage of a resumed generation is evidence "
                         "and must survive the next pause")

    def test_an_active_waiting_generation_is_never_overwritten(self):
        self.store.create(record())
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            self.store.create(self.generation_two())
        self.assertEqual(ctx.exception.code, "PAUSE_GENERATION_ACTIVE")
        self.assertIn("PAUSE_GENERATION_ACTIVE", pause_policy.PAUSE_REFUSAL_CODES)
        active = self.store.read(RUN)
        self.assertEqual(active["pause_record_id"], "pause_abc")
        self.assertEqual(active["checkpoint_id"], "chk_1")
        self.assertEqual(self.store.superseded(RUN), ())

    def test_re_finalising_the_same_generation_is_still_idempotent(self):
        """The crash/retry property the old unconditional return existed for."""
        first = self.store.create(record())
        again = self.store.create(record(checkpoint_id="chk_1"))
        self.assertEqual(again, first)
        token = self.store.claim(RUN)["lease_token"]
        claimed = self.store.create(record())
        self.assertEqual(claimed["lease_token"], token,
                         "a re-finalise must not drop the live ownership columns")
        self.assertEqual(self.store.superseded(RUN), ())

    def test_a_successor_naming_the_same_checkpoint_is_refused_on_lineage(self):
        self.resume_generation_one()
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            self.store.create(self.generation_two(checkpoint_id="chk_1"))
        self.assertEqual(ctx.exception.code, "PAUSE_GENERATION_LINEAGE")
        self.assertEqual(self.store.read(RUN)["pause_record_id"], "pause_abc")

    def test_binding_generation_never_moves_backwards(self):
        self.store.create(record(projection=projection(binding_generation=4)))
        token = self.store.claim(RUN)["lease_token"]
        self.store.mark_resumed(RUN, token)
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            self.store.create(self.generation_two(
                projection=projection(request_id="request_2", binding_generation=3)))
        self.assertEqual(ctx.exception.code, "PAUSE_GENERATION_LINEAGE")

    def test_a_disposed_run_takes_no_further_generation(self):
        self.store.create(record())
        token = self.store.claim(RUN)["lease_token"]
        self.store.settle_disposition(RUN, {
            "kind": "CANCEL", "cancellation_id": "c1", "actor_id": "alice",
            "actor_type": "human", "submission_id": "s1", "reason": "withdrawn",
            "requested_at": "2026-01-01T00:00:00Z"}, token)
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            self.store.create(self.generation_two())
        self.assertEqual(ctx.exception.code, "RUN_ALREADY_CANCELLED")

    def test_a_single_generation_document_is_unchanged_on_disk(self):
        """The retained history must not perturb the C4 byte-identical reindex property."""
        import json
        self.store.create(record())
        document = json.loads(self.store.path.read_text())
        self.assertEqual(set(document), {"schema_version", "record"})


# ======================================================================================
# C1 -- the lease is renewed for the whole claimed section
# ======================================================================================
class BlockingGraph:
    """The real graph, held at one named point of the claimed section.

    ``where="invoke"`` is the review's own scenario: A is parked inside ``graph.invoke()``.
    ``where="update_state_command"`` parks A one step earlier, before the re-entry has
    moved the checkpoint head, which is the state in which an unfenced successor gets all
    the way to a SECOND effect rather than being stopped by the C2 head check.
    """

    def __init__(self, inner: Any, entered: threading.Event, release: threading.Event,
                 where: str = "invoke") -> None:
        self._inner, self._entered, self._release = inner, entered, release
        self._where = where

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _park(self, where: str) -> None:
        if where != self._where:
            return
        self._entered.set()
        if not self._release.wait(timeout=60):     # bounded, never an unbounded park
            raise AssertionError("the test never released the blocked call")

    def update_state_command(self, *args: Any, **kwargs: Any) -> Any:
        self._park("update_state_command")
        return self._inner.update_state_command(*args, **kwargs)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        self._park("invoke")
        return self._inner.invoke(*args, **kwargs)


@REQUIRES_LANGGRAPH
class ResumeLeaseFencingTests(PauseFixture):
    """CRITICAL 1, as a real two-Coordinator race with a short lease and a blocked invoke."""

    RUN = "run_fencing"
    LEASE = 1.0
    #: Fifty renewals per lease: the keeper is paced explicitly rather than by wall clock,
    #: so a stalled machine cannot make a healthy owner look dead.
    BEAT = 0.02

    def pause_store_for(self, owner, lease=None):
        return pause_store.store_for(self.RUN, artifact_base=self.base, owner_id=owner,
                                     lease_seconds=self.LEASE if lease is None else lease)

    def resume(self, record, *, store, graph_wrapper=None, results=(WORKER, REVIEW_PASS,
                                                                    REVIEW_PASS),
               observe_timeout_seconds=None, keeper_factory=None, on_graph=None):
        port = self.approval_port()
        adapter = self.adapter(results)
        projection = record["projection"]

        def graph_factory(saver):
            if on_graph is not None:
                on_graph()
            graph = self.graph(adapter, saver, approval_port=port)
            return graph_wrapper(graph) if graph_wrapper is not None else graph

        outcome = pause_runtime.resume_run(
            self.RUN, artifact_base=self.base, approval_port=port,
            graph_factory=graph_factory,
            current_repository=projection["repository_binding"],
            current_artifact=projection["artifact_binding"],
            current_policy_digest=projection["policy_digest"],
            store=store, recursion_limit=300,
            observe_timeout_seconds=observe_timeout_seconds,
            keeper_factory=keeper_factory)
        return outcome, adapter

    def race(self, paused, *, where):
        """Park A at ``where`` under a SHORT lease and let B try to take the run.

        Returns ``(a_outcome, a_adapter, b_outcome, b_adapter, b_reached_the_effect)``.
        """
        entered, release = threading.Event(), threading.Event()
        outcomes: dict[str, Any] = {}
        b_reached_the_effect = threading.Event()

        def run_a():
            try:
                outcomes["a"] = self.resume(
                    paused, store=self.pause_store_for("host:A"),
                    graph_wrapper=lambda graph: BlockingGraph(graph, entered, release,
                                                              where=where),
                    keeper_factory=lease_keeper_factory(interval_seconds=self.BEAT))
            except BaseException as exc:                    # noqa: BLE001 - reported below
                outcomes["a"] = exc
                entered.set()

        owner = threading.Thread(target=run_a, name="coordinator-a")
        owner.start()
        try:
            self.assertTrue(entered.wait(timeout=30), "A never entered the claimed section")
            # A is now inside the claimed section.  B arrives with an observation window of
            # two whole leases: without renewal that is far more than enough to watch A's
            # lease lapse and take the run away from it.
            b_outcome, b_adapter = self.resume(
                paused, store=self.pause_store_for("host:B"),
                graph_wrapper=lambda graph: b_reached_the_effect.set() or graph,
                observe_timeout_seconds=2 * self.LEASE)
        finally:
            release.set()
            owner.join(timeout=60)
        self.assertFalse(owner.is_alive(), "coordinator A deadlocked")
        a_outcome = outcomes["a"]
        if isinstance(a_outcome, BaseException):
            raise a_outcome
        return (*a_outcome, b_outcome, b_adapter, b_reached_the_effect)

    def test_b_cannot_reach_the_effect_while_a_is_inside_the_invocation(self):
        """Required regression 1: a SHORT lease and a deliberately BLOCKED invocation.

        Without renewal A's 1-second lease lapses while it is still inside
        ``graph.invoke()``, and B legally claims the run out from under a healthy owner.
        With renewal A stays the owner for the whole call and B is refused by name.
        """
        _, paused, _ = self.drive_to_pause()
        self.answer_all()
        a_outcome, a_adapter, b_outcome, b_adapter, reached = self.race(paused,
                                                                       where="invoke")
        self.assertFalse(reached.is_set(),
                         "B reached the effect while A was still inside it")
        self.assertEqual(b_adapter.effect_count, 0, "B performed an effect")
        self.assertEqual(b_outcome.status, "REFUSED")
        self.assertIn(b_outcome.code,
                      ("PAUSE_CLAIM_HELD", "PAUSE_OBSERVATION_TIMEOUT"), b_outcome.detail)
        self.assertEqual(a_outcome.status, "RESUMED", a_outcome.detail)
        self.assertTrue(a_outcome.effect_performed)
        self.assertEqual(a_adapter.effect_count, 3,
                         "exactly ONE round of effects exists for the whole race")
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "RESUMED")
        self.assertEqual(len(stored["applied"]), 1)

    def test_b_cannot_drive_a_second_effect_while_a_owns_the_unmoved_checkpoint(self):
        """The same race one step earlier, where an unfenced B reaches a SECOND effect.

        Parked before the re-entry writes its checkpoint, the head still says
        WAITING_FOR_INPUT -- so nothing downstream of the lease stops a successor that
        claimed a lapsed-but-live-owner lease: it re-drives ``update_state_command`` and
        ``invoke`` for the same bundle.  The renewed lease is the only thing that does.
        """
        _, paused, _ = self.drive_to_pause()
        self.answer_all()
        a_outcome, a_adapter, b_outcome, b_adapter, reached = self.race(
            paused, where="update_state_command")
        self.assertFalse(reached.is_set(), "B built a graph for an effect A owns")
        self.assertEqual(b_adapter.effect_count, 0, "B performed a SECOND effect")
        self.assertEqual(b_outcome.status, "REFUSED")
        self.assertIn(b_outcome.code,
                      ("PAUSE_CLAIM_HELD", "PAUSE_OBSERVATION_TIMEOUT"), b_outcome.detail)
        self.assertEqual(a_outcome.status, "RESUMED", a_outcome.detail)
        self.assertEqual(a_adapter.effect_count, 3)

    def test_an_owner_that_stops_heartbeating_is_taken_over_in_one_call(self):
        """Required regression 2, and the end of M3: no manual retry, no unbounded wait."""
        _, paused, _ = self.drive_to_pause()
        self.answer_all()
        dead = self.pause_store_for("host:dead", lease=0.4)
        claimed = dead.claim(self.RUN)
        self.assertEqual(claimed["claim_outcome"], "CREATED")
        # The process dies here: no heartbeat, no release, nothing but a lapsing lease.
        successor = self.pause_store_for("host:next", lease=0.4)
        outcome, adapter = self.resume(paused, store=successor,
                                       observe_timeout_seconds=None)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED")
        self.assertEqual(adapter.effect_count, 3)
        self.assertEqual(self.store().read(self.RUN)["owner_id"], "host:next")

    def test_a_lost_lease_stops_the_resume_dead_with_a_named_code(self):
        """Fail-closed by name: a superseded owner writes nothing more.

        The successor here takes the run the way a legitimate takeover of a genuinely
        lapsed lease does -- it rotates the ownership columns -- while A is parked inside
        the effect.  A must then stop: no promotion, no ``mark_resumed``, and a
        ``PAUSE_CLAIM_LOST`` outcome rather than a reported success on a claim it no
        longer holds.  Whether A notices at the keeper's next beat or at the fence of its
        next write, the named outcome is the same one.
        """
        _, paused, _ = self.drive_to_pause()
        self.answer_all()
        store = self.pause_store_for("host:A")
        successor = self.pause_store_for("host:next")
        entered, release = threading.Event(), threading.Event()

        def take_the_run_away(graph):
            def thief():
                entered.wait(timeout=30)
                stolen = dict(successor.read(self.RUN))
                stolen.update({"owner_id": "host:next", "lease_token": "successor-token",
                               "lease_expires_at": stolen["lease_expires_at"] + 600.0})
                successor.replace(stolen)
                release.set()
            threading.Thread(target=thief, name="successor", daemon=True).start()
            return BlockingGraph(graph, entered, release)

        outcome, adapter = self.resume(
            paused, store=store, graph_wrapper=take_the_run_away,
            keeper_factory=lease_keeper_factory(interval_seconds=self.BEAT))
        self.assertEqual(outcome.status, "REFUSED")
        self.assertEqual(outcome.code, "PAUSE_CLAIM_LOST", outcome.detail)
        self.assertFalse(outcome.effect_performed)
        stored = self.store().read(self.RUN)
        self.assertNotEqual(stored["status"], "RESUMED",
                            "a superseded owner must not promote its own resume")
        self.assertEqual(stored["owner_id"], "host:next",
                         "the successor's ownership is not overwritten by the loser")


# ======================================================================================
# C2 -- pause -> resume -> pause -> resume, end to end
# ======================================================================================
class RepausingGraph:
    """The real graph, with a NEW human decision arriving during the re-entry.

    This is what a Coordinator does when the next round blocks again: it writes
    ``SET_DECISION`` and re-invokes.  Wrapping it here is what turns one E2E resume into a
    genuine second pause generation on the same run and the same thread.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def invoke(self, state: Any, config: Any, *args: Any, **kwargs: Any) -> Any:
        self._inner.update_state_command(config, "SET_DECISION", as_node="VALIDATE",
                                         decision_state="NEEDS_INPUT",
                                         decision_reason_code="user_choice_required")
        return self._inner.invoke(state, config, *args, **kwargs)


@REQUIRES_LANGGRAPH
class RepeatedPauseGenerationE2ETests(PauseFixture):
    """pause #1 -> resume #1 -> pause #2 -> resume #2, both generations durable and distinct."""

    RUN = "run_generations"

    def second_round_sources(self):
        from scripts.clarification_protocol import ClarificationSource, decision_item_id
        # The key must be the one ``clarification_item`` itself declares, or the request
        # is refused at publish time as a source binding mismatch.
        key = f"{self.RUN}/analysis/1/B2#2"
        item = clarification_item(self.RUN, suffix="2", open_item="target_round2")
        return (ClarificationSource(
            open_item="target_round2", source_ledger_key=key, source_ledger_keys=(key,),
            state="NEEDS_INPUT", reason_code="user_choice_required", phase="analysis",
            iteration=1, request_input=item),)

    def answer_request(self, request, token):
        from scripts.clarification_protocol import ResponseSubmission
        port = self.approval_port()
        for index, item in enumerate(request["items"]):
            port.ingest(run_id=self.RUN, request_id=request["request_id"],
                        decision_item_id=item["decision_item_id"],
                        submission=ResponseSubmission(
                            f"{token}_{index}", "alice", "human", "desk",
                            "2026-09-01T08:00:00Z", "staging", None, False, "normal"))

    def resume_once(self, paused, *, graph_wrapper=None,
                    results=(WORKER, REVIEW_PASS, REVIEW_PASS)):
        port = self.approval_port()
        adapter = self.adapter(results)
        projection = paused["projection"]

        def graph_factory(saver):
            graph = self.graph(adapter, saver, approval_port=port)
            return graph_wrapper(graph) if graph_wrapper is not None else graph

        outcome = pause_runtime.resume_run(
            self.RUN, artifact_base=self.base, approval_port=port,
            graph_factory=graph_factory,
            current_repository=projection["repository_binding"],
            current_artifact=projection["artifact_binding"],
            current_policy_digest=projection["policy_digest"],
            recursion_limit=300, observe_timeout_seconds=1.0)
        return outcome, adapter

    def test_a_run_pauses_resumes_pauses_and_resumes_again(self):
        _, first, _ = self.drive_to_pause()
        self.assertEqual(first["status"], "WAITING_FOR_INPUT")
        self.answer_all(token="submission_r1")

        # ---- resume #1, during which a NEW decision blocks the run again ----
        self.sources = self.second_round_sources()
        first_resume, _ = self.resume_once(first, graph_wrapper=RepausingGraph)
        self.assertEqual(first_resume.status, "RESUMED", first_resume.detail)
        self.assertEqual(first_resume.state["run_lifecycle"], "WAITING_FOR_INPUT",
                         "the resumed run blocked on a second human decision")

        second = first_resume.next_pause_record
        self.assertIsNotNone(second, "pause #2 was never recorded")
        self.assertNotEqual(second["pause_record_id"], first["pause_record_id"])
        self.assertNotEqual(second["checkpoint_id"], first["checkpoint_id"])
        self.assertEqual(second["status"], "WAITING_FOR_INPUT")
        self.assertEqual(second["applied"], {})

        # ---- generation 2 is what is on disk, and generation 1 is retained ----
        store = self.store()
        active = store.read(self.RUN)
        self.assertEqual(active["pause_record_id"], second["pause_record_id"])
        self.assertEqual(active["checkpoint_id"], second["checkpoint_id"])
        self.assertEqual(active["projection"]["request_id"],
                         second["projection"]["request_id"])
        self.assertNotEqual(active["projection"]["request_id"],
                            first["projection"]["request_id"])
        history = store.superseded(self.RUN)
        self.assertEqual([row["pause_record_id"] for row in history],
                         [first["pause_record_id"]])
        self.assertEqual(history[0]["status"], "RESUMED")
        self.assertEqual(len(history[0]["applied"]), 1,
                         "generation 1's applied bundle is its exactly-once evidence")

        # ---- discovery sees the LIVE generation, not the answered one ----
        listings = {entry["run_id"]: entry for entry in pause_runtime.discover(self.base)}
        self.assertEqual(listings[self.RUN]["verdict"], "RESUMABLE")
        self.assertEqual(listings[self.RUN]["pause_record_id"],
                         second["pause_record_id"])

        # ---- resume #2 ----
        new_requests = [request for request in self.requests()
                        if request["request_id"] == second["projection"]["request_id"]]
        self.assertEqual(len(new_requests), 1, "pause #2 published its own request")
        self.answer_request(new_requests[0], "submission_r2")
        second_resume, adapter = self.resume_once(second)
        self.assertEqual(second_resume.status, "RESUMED", second_resume.detail)
        self.assertEqual(second_resume.state["terminal_status"], "COMPLETED")
        self.assertIsNone(second_resume.next_pause_record)
        self.assertEqual(adapter.effect_count, 3)

        final = store.read(self.RUN)
        self.assertEqual(final["status"], "RESUMED")
        self.assertEqual(final["pause_record_id"], second["pause_record_id"])
        self.assertEqual(len(final["applied"]), 1)
        self.assertEqual([row["pause_record_id"] for row in store.superseded(self.RUN)],
                         [first["pause_record_id"]])

        # ---- a duplicate resume of THIS generation performs no second effect ----
        duplicate, duplicate_adapter = self.resume_once(second)
        self.assertEqual(duplicate.status, "NO_EFFECT")
        self.assertEqual(duplicate.code, "RUN_ALREADY_RESUMED")
        self.assertFalse(duplicate.effect_performed)
        self.assertEqual(duplicate_adapter.effect_count, 0)


# ======================================================================================
# C5 -- the closed schema the recovery is expressed in
# ======================================================================================
class ContinuationSchemaTests(unittest.TestCase):
    """CRITICAL 1: the three durable facts, and the codes that report them, are closed."""

    def test_the_applied_stage_distinguishes_intent_from_continuation(self):
        self.assertEqual(pause_policy.APPLIED_STAGES,
                         ("RECORDED", "CONTINUING", "RESUMED"),
                         "resume intent, graph continuation and completed effect are "
                         "three durable facts, not two")
        self.assertEqual(pause_policy.APPLIED_IN_FLIGHT_STAGES,
                         ("RECORDED", "CONTINUING"))
        self.assertNotIn("RESUMED", pause_policy.APPLIED_IN_FLIGHT_STAGES,
                         "a promoted bundle is finished and is not recovered again")

    def test_the_new_codes_are_closed_and_the_three_sets_stay_disjoint(self):
        self.assertIn("PAUSE_CONTINUATION_UNRECOVERABLE",
                      pause_policy.PAUSE_REFUSAL_CODES)
        self.assertEqual(pause_policy.PAUSE_RECOVERY_CODES,
                         frozenset({"PAUSE_CONTINUATION_RECOVERED",
                                    "PAUSE_CONTINUATION_ALREADY_COMPLETE"}))
        for other in (pause_policy.PAUSE_REFUSAL_CODES,
                      pause_policy.PAUSE_REVALIDATION_CODES):
            self.assertEqual(pause_policy.PAUSE_RECOVERY_CODES & other, frozenset(),
                             "a recovery is neither a refusal nor a revalidation")
        # ...and the refusal really is refusable, i.e. it is a legal PauseRefused code.
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            raise pause_policy.PauseRefused("PAUSE_CONTINUATION_UNRECOVERABLE", "x")
        self.assertEqual(ctx.exception.code, "PAUSE_CONTINUATION_UNRECOVERABLE")

    def test_the_discovery_vocabulary_is_closed_and_names_both_actionable_verdicts(self):
        """Discovery must be able to SAY "recoverable"; a widened RESUMABLE cannot.

        The two actionable verdicts lead to different work -- run the resume from the top,
        or finish a continuation already committed to the checkpoint -- so a caller that
        acts on them differently needs two names, and they are closed.
        """
        self.assertEqual(pause_policy.PAUSE_DISCOVERY_ACTIONABLE_VERDICTS,
                         frozenset({"RESUMABLE", "PAUSE_CONTINUATION_RECOVERABLE"}))
        self.assertEqual(pause_policy.PAUSE_RESUMABLE, "RESUMABLE")
        self.assertEqual(pause_policy.PAUSE_CONTINUATION_RECOVERABLE,
                         "PAUSE_CONTINUATION_RECOVERABLE")
        self.assertEqual(pause_policy.PAUSE_DISCOVERY_VERDICTS,
                         pause_policy.PAUSE_DISCOVERY_ACTIONABLE_VERDICTS
                         | pause_policy.PAUSE_REFUSAL_CODES)
        # An actionable verdict is never a refusal, and the refused counterpart of the
        # recoverable one is: the two are the two sides of C5 and must not collide.
        self.assertEqual(pause_policy.PAUSE_DISCOVERY_ACTIONABLE_VERDICTS
                         & pause_policy.PAUSE_REFUSAL_CODES, frozenset())
        self.assertIn("PAUSE_CONTINUATION_UNRECOVERABLE",
                      pause_policy.PAUSE_DISCOVERY_VERDICTS)
        # Every run that is no longer waiting is reported by a code in the same closed set.
        for status in pause_store.PAUSE_RECORD_STATUSES:
            if status == "WAITING_FOR_INPUT":
                continue
            self.assertIn(f"RUN_ALREADY_{status}", pause_policy.PAUSE_DISCOVERY_VERDICTS)

    def test_the_record_schema_accepts_a_continuing_bundle_and_nothing_else_new(self):
        stored = record(applied={"bundle_1": {
            "resume_bundle_id": "bundle_1", "request_id": "request_1",
            "items": [{"decision_item_id": "item_a", "decision_id": "d1"}],
            "stage": "CONTINUING", "recorded_at": "2026-01-01T00:00:00Z",
            "resumed_at": "", "resumed_checkpoint_id": ""}})
        self.assertEqual(pause_store.validate_pause_record(RUN, stored)["applied"]
                         ["bundle_1"]["stage"], "CONTINUING")
        with self.assertRaises(pause_store.PauseRecordCorrupt):
            pause_store.validate_pause_record(RUN, record(applied={"bundle_1": {
                **stored["applied"]["bundle_1"], "stage": "INVENTED"}}))

    def test_in_flight_bundle_is_pure_and_reads_only_the_record(self):
        self.assertIsNone(pause_policy.in_flight_bundle({"applied": {}}))
        self.assertIsNone(pause_policy.in_flight_bundle(
            {"applied": {"b": {"stage": "RESUMED"}}}))
        for stage in pause_policy.APPLIED_IN_FLIGHT_STAGES:
            self.assertEqual(pause_policy.in_flight_bundle(
                {"applied": {"b": {"stage": stage}}})["stage"], stage)


# ======================================================================================
# crash boundaries
# ======================================================================================
class CrashingStore:
    """The pause store, dying at exactly one named write."""

    def __init__(self, inner: Any, method: str) -> None:
        self._inner, self._method = inner, method

    def __getattr__(self, name: str) -> Any:
        if name == self._method:
            def crash(*args: Any, **kwargs: Any) -> Any:
                raise CrashInjected(f"process died before {name}")
            return crash
        return getattr(self._inner, name)


class CrashingGraph:
    """The real graph, dying either side of ``invoke``."""

    def __init__(self, inner: Any, when: str) -> None:
        self._inner, self._when = inner, when

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def update_state_command(self, *args: Any, **kwargs: Any) -> Any:
        if self._when == "before_checkpoint_update":
            raise CrashInjected("process died before the checkpoint update")
        result = self._inner.update_state_command(*args, **kwargs)
        if self._when == "after_checkpoint_update":
            raise CrashInjected("process died after the checkpoint update")
        return result

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        final = self._inner.invoke(*args, **kwargs)
        if self._when == "after_invoke":
            raise CrashInjected("process died after invoke, before promotion")
        return final


class _CrashBoundaryFixture(PauseFixture):
    """Drive to a pause, answer it, and crash a resume at a chosen boundary.

    Declares no test of its own: the resume-path suite and the discovery suite below both
    need the same crash, and neither may own it.
    """

    RUN = "run_crashboundary"

    def pause_store_for(self, owner_id, *, lease=pause_store.DEFAULT_LEASE_SECONDS):
        return pause_store.store_for(self.RUN, artifact_base=self.base,
                                     owner_id=owner_id, lease_seconds=lease)

    def attempt(self, paused, *, store=None, graph_wrapper=None,
                observe_timeout_seconds=1.0,
                results=(WORKER, REVIEW_PASS, REVIEW_PASS)):
        port = self.approval_port()
        adapter = self.adapter(results)
        projection = paused["projection"]

        def graph_factory(saver):
            graph = self.graph(adapter, saver, approval_port=port)
            return graph_wrapper(graph) if graph_wrapper is not None else graph

        outcome = pause_runtime.resume_run(
            self.RUN, artifact_base=self.base, approval_port=port,
            graph_factory=graph_factory,
            current_repository=projection["repository_binding"],
            current_artifact=projection["artifact_binding"],
            current_policy_digest=projection["policy_digest"],
            store=store, recursion_limit=300,
            observe_timeout_seconds=observe_timeout_seconds)
        return outcome, adapter

    def setUp(self):
        super().setUp()
        _, self.paused, _ = self.drive_to_pause()
        self.answer_all()

    def crash_at(self, when, *, owner_id=None, lease=0.4):
        """Kill a resume at ``when`` and return the record the crash left behind."""
        store = self.pause_store_for(owner_id, lease=lease) if owner_id else None
        with self.assertRaises(CrashInjected):
            self.attempt(self.paused, store=store,
                         graph_wrapper=lambda graph: CrashingGraph(graph, when))
        return self.store().read(self.RUN)

    def fork_the_head_off_the_lineage_root(self, crashed):
        """Move the head to a checkpoint that does NOT descend from the pause.

        The forked head is written off the lineage ROOT, which predates the pause, so the
        pause checkpoint is not among its ancestors -- the one case in which a moved head
        must still be refused.
        """
        saver = self.saver()
        lineage = pause_runtime.checkpoint_lineage(saver, "t", "", saver.head("t"))
        self.assertIn(crashed["checkpoint_id"], lineage)
        root = lineage[-1]
        self.assertNotEqual(root, crashed["checkpoint_id"])
        parent = {"configurable": {"thread_id": "t", "checkpoint_ns": "",
                                   "checkpoint_id": root}}
        tuple_ = saver.get_tuple(parent)
        forked = dict(tuple_.checkpoint)
        forked["id"] = "forked-off-the-root"
        saver.put(parent, forked, dict(tuple_.metadata),
                  dict(forked.get("channel_versions") or {}))
        self.assertEqual(saver.head("t"), "forked-off-the-root")
        self.assertNotIn(crashed["checkpoint_id"],
                         pause_runtime.checkpoint_lineage(saver, "t", "",
                                                          saver.head("t")))
        return "forked-off-the-root"


@REQUIRES_LANGGRAPH
class ResumeCrashBoundaryTests(_CrashBoundaryFixture):
    """Every boundary of the claimed section, each one exactly-once or no-effect."""

    def test_a_crash_before_the_applied_record_write_leaves_nothing_applied(self):
        crashing = CrashingStore(self.store(), "record_applied")
        with self.assertRaises(CrashInjected):
            self.attempt(self.paused, store=crashing)
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["applied"], {}, "no dedupe key was written")
        self.assertEqual(stored["status"], "WAITING_FOR_INPUT")
        outcome, adapter = self.attempt(self.paused)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(adapter.effect_count, 3, "exactly one round of effects")

    def test_a_crash_after_the_applied_write_and_before_the_checkpoint_update_re_drives(self):
        """Boundary 2, and the safe half of the stage/checkpoint ordering.

        ``CONTINUING`` is written strictly BEFORE ``update_state_command``, so a crash in
        this window leaves the STAGE ahead of the CHECKPOINT.  That direction is the safe
        one and is the whole point of the ordering: the head still carries the pause, so
        C5 reads NOT_STARTED, the resume is re-driven from the top byte-identically, and
        exactly one round of effects exists for the run.  The opposite direction -- a
        checkpoint ahead of the stage -- is the state nothing could name, and this
        ordering makes it unreachable.
        """
        with self.assertRaises(CrashInjected):
            self.attempt(self.paused,
                         graph_wrapper=lambda graph: CrashingGraph(
                             graph, "before_checkpoint_update"))
        stored = self.store().read(self.RUN)
        self.assertEqual(len(stored["applied"]), 1)
        self.assertEqual(next(iter(stored["applied"].values()))["stage"], "CONTINUING",
                         "the intent to continue is durable before the head can move")
        self.assertEqual(self.saver().head("t"), stored["checkpoint_id"],
                         "the head did NOT move: the stage is ahead of the checkpoint")
        self.assertEqual(
            pause_runtime.continuation_evidence(stored, self.saver()),
            pause_runtime.CONTINUATION_NOT_STARTED,
            "durable evidence must say the continuation never committed")
        outcome, adapter = self.attempt(self.paused)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(adapter.effect_count, 3)
        self.assertEqual(self.store().read(self.RUN)["status"], "RESUMED")

    def test_a_crash_after_the_checkpoint_update_is_recovered_to_a_terminal(self):
        """Boundary 3 -- THE defect.  A successor recovers; it never refuses forever.

        The head has moved to ACTIVE but no node has run, so the effect has NOT happened.
        Refusing here (which is what ``STALE_CHECKPOINT_HEAD`` did) strands the run
        permanently: ``reindex()`` cannot repair it either, because its repair direction
        needs a head carrying WAITING_FOR_INPUT and this one carries ACTIVE.

        What the successor must do instead is prove, from durable bytes alone, that this
        head is this bundle's own committed continuation, and then finish it -- driving
        the run to a terminal with the effect performed EXACTLY ONCE for the whole run,
        not zero times and not twice.
        """
        with self.assertRaises(CrashInjected):
            self.attempt(self.paused,
                         graph_wrapper=lambda graph: CrashingGraph(
                             graph, "after_checkpoint_update"))
        # The durable evidence the successor reads, and nothing else.
        crashed = self.store().read(self.RUN)
        self.assertEqual(next(iter(crashed["applied"].values()))["stage"], "CONTINUING")
        head_state = pause_runtime.reconstruct(
            {**crashed, "checkpoint_id": self.saver().head("t")}, self.saver())
        self.assertEqual(head_state["run_lifecycle"], "ACTIVE",
                         "the head moved off the pause without the effect running")
        self.assertEqual(pause_runtime.continuation_evidence(crashed, self.saver()),
                         pause_runtime.CONTINUATION_COMMITTED)

        outcome, adapter = self.attempt(self.paused)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.code, "PAUSE_CONTINUATION_RECOVERED", outcome.detail)
        self.assertTrue(outcome.effect_performed)
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED",
                         "the successor drives the run to a terminal state")
        self.assertEqual(adapter.effect_count, 3,
                         "exactly ONE round of effects exists for the whole run")
        stored = self.store().read(self.RUN)
        self.assertEqual(next(iter(stored["applied"].values()))["stage"], "RESUMED")
        self.assertEqual(stored["status"], "RESUMED")

        # ...and it stays exactly-once: a third Coordinator performs no further effect.
        again, again_adapter = self.attempt(self.paused)
        self.assertEqual(again.status, "NO_EFFECT")
        self.assertEqual(again.code, "RUN_ALREADY_RESUMED")
        self.assertEqual(again_adapter.effect_count, 0)

    def test_a_crash_after_invoke_and_before_promotion_never_repeats_the_effect(self):
        """Boundary 4: the effect committed, the record never learned it.

        This boundary IS durably distinguishable from boundary 3, and the persisted head
        is what distinguishes it: boundary 3 leaves the head on the ACTIVE re-entry with
        its supersteps still pending, boundary 4 leaves the head already SETTLED.  The
        Tier-2 stage is ``CONTINUING`` in both cases -- the stage is deliberately the
        weaker fact -- but Tier-1, the checkpoint, is the authority, and C5 reads it.  So
        the two are reported by different codes: ``PAUSE_CONTINUATION_RECOVERED`` there,
        ``PAUSE_CONTINUATION_ALREADY_COMPLETE`` here.

        The original guarantee of this test is therefore kept exactly as it was -- the
        committed effect is NEVER repeated -- and it is what the name still says.  What
        changed is only the disposition of the RECORD.  Refusing with
        ``STALE_CHECKPOINT_HEAD`` (the old behaviour) left the bundle permanently
        ``RECORDED`` against a run that had already finished, and no repair path could
        settle it.  Because the head is distinguishable, the successor can do better
        without weakening anything: at a SETTLED head it recognises ALREADY_COMPLETE,
        settles the record to match the checkpoint, and performs NO new effect.
        """
        crashing = self.pause_store_for("host:dead", lease=0.4)
        with self.assertRaises(CrashInjected):
            self.attempt(self.paused, store=crashing,
                         graph_wrapper=lambda graph: CrashingGraph(graph, "after_invoke"))
        crashed = self.store().read(self.RUN)
        self.assertEqual(next(iter(crashed["applied"].values()))["stage"], "CONTINUING")
        head_before = self.saver().head("t")
        head_state = pause_runtime.reconstruct(
            {**crashed, "checkpoint_id": head_before}, self.saver())
        self.assertEqual(head_state["run_lifecycle"], "SETTLED",
                         "the re-entry itself did commit, and ran to a terminal")
        # ...and that is exactly what separates this boundary from boundary 3, whose head
        # reconstructs to ACTIVE.  The durable states are NOT identical.
        self.assertNotEqual(head_state["run_lifecycle"], "ACTIVE")

        # A genuinely FRESH Coordinator: its own owner identity, so it has to observe the
        # dead owner's lease lapse and take the run over before it may touch anything.
        successor = self.pause_store_for("host:next", lease=0.4)
        outcome, adapter = self.attempt(self.paused, store=successor,
                                        observe_timeout_seconds=None)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.code, "PAUSE_CONTINUATION_ALREADY_COMPLETE",
                         outcome.detail)
        self.assertFalse(outcome.effect_performed,
                         "a finished continuation performs nothing further")
        self.assertEqual(adapter.effect_count, 0,
                         "THE ORIGINAL GUARANTEE: the committed effect is never repeated")
        self.assertEqual(self.saver().head("t"), head_before,
                         "recovering a finished continuation moves no checkpoint")
        stored = self.store().read(self.RUN)
        self.assertEqual(next(iter(stored["applied"].values()))["stage"], "RESUMED",
                         "the record is settled to match the checkpoint, not stranded")
        self.assertEqual(stored["status"], "RESUMED")
        self.assertEqual(stored["owner_id"], "host:next",
                         "the run was settled by the fresh Coordinator, not the dead one")

        # ...and it ends exactly-once: a further Coordinator performs no effect either.
        again, again_adapter = self.attempt(self.paused)
        self.assertEqual(again.status, "NO_EFFECT")
        self.assertEqual(again.code, "RUN_ALREADY_RESUMED")
        self.assertEqual(again_adapter.effect_count, 0,
                         "the committed effect is still never repeated")

    def test_a_genuinely_different_coordinator_recovers_the_stranded_continuation(self):
        """Boundary 3 again, but the successor is a DIFFERENT owner identity.

        The four boundary tests above already drop every in-memory object and rebuild the
        driver over the same on-disk stores, which is what "a new Coordinator" means to
        this fixture.  This one goes further and gives the successor its own ``owner_id``,
        so it has to reach the run the way a real second machine does: observe the dead
        owner's lease, watch it lapse, and take it over -- and only then recover.
        """
        crashing = self.pause_store_for("host:dead", lease=0.4)
        with self.assertRaises(CrashInjected):
            self.attempt(self.paused, store=crashing,
                         graph_wrapper=lambda graph: CrashingGraph(
                             graph, "after_checkpoint_update"))
        successor = self.pause_store_for("host:next", lease=0.4)
        outcome, adapter = self.attempt(self.paused, store=successor,
                                        observe_timeout_seconds=None)
        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.code, "PAUSE_CONTINUATION_RECOVERED", outcome.detail)
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED")
        self.assertEqual(adapter.effect_count, 3, "exactly one round of effects")
        stored = self.store().read(self.RUN)
        self.assertEqual(stored["status"], "RESUMED")
        self.assertEqual(stored["owner_id"], "host:next")

    def test_a_head_that_does_not_descend_from_the_pause_is_never_continued(self):
        """The other half of C5: recovery is not "the head moved, so drive it".

        A head that is neither the pause checkpoint nor a descendant of it is not this
        bundle's continuation, and continuing it would drive a run this record does not
        speak for.  That fails closed by name, and it is the one case in which a moved
        head still refuses.
        """
        with self.assertRaises(CrashInjected):
            self.attempt(self.paused,
                         graph_wrapper=lambda graph: CrashingGraph(
                             graph, "after_checkpoint_update"))
        saver = self.saver()
        crashed = self.store().read(self.RUN)
        lineage = pause_runtime.checkpoint_lineage(saver, "t", "", saver.head("t"))
        self.assertIn(crashed["checkpoint_id"], lineage)
        root = lineage[-1]                       # predates the pause, so a fork off it
        self.assertNotEqual(root, crashed["checkpoint_id"])
        parent = {"configurable": {"thread_id": "t", "checkpoint_ns": "",
                                   "checkpoint_id": root}}
        tuple_ = saver.get_tuple(parent)
        forked = dict(tuple_.checkpoint)
        forked["id"] = "forked-off-the-root"
        saver.put(parent, forked, dict(tuple_.metadata),
                  dict(forked.get("channel_versions") or {}))
        self.assertEqual(saver.head("t"), "forked-off-the-root")
        self.assertNotIn(crashed["checkpoint_id"],
                         pause_runtime.checkpoint_lineage(saver, "t", "",
                                                          saver.head("t")))
        with self.assertRaises(pause_policy.PauseRefused) as ctx:
            pause_runtime.continuation_evidence(crashed, self.saver())
        self.assertEqual(ctx.exception.code, "PAUSE_CONTINUATION_UNRECOVERABLE")
        outcome, adapter = self.attempt(self.paused)
        self.assertEqual(outcome.status, "REFUSED")
        self.assertEqual(outcome.code, "PAUSE_CONTINUATION_UNRECOVERABLE", outcome.detail)
        self.assertEqual(adapter.effect_count, 0, "no effect on an unprovable head")

    def test_a_duplicate_resume_of_the_same_generation_performs_no_second_effect(self):
        first, first_adapter = self.attempt(self.paused)
        self.assertEqual(first.status, "RESUMED", first.detail)
        self.assertEqual(first_adapter.effect_count, 3)
        second, second_adapter = self.attempt(self.paused)
        self.assertEqual(second.status, "NO_EFFECT")
        self.assertEqual(second.code, "RUN_ALREADY_RESUMED")
        self.assertEqual(second_adapter.effect_count, 0)


# ======================================================================================
# MAJOR -- discovery must report what resume_run will actually do
# ======================================================================================
@REQUIRES_LANGGRAPH
class DiscoveryContinuationTests(_CrashBoundaryFixture):
    """MAJOR: ``discover`` applied C1 then C2 and never asked C5.

    A run crashed at the continuation boundary legitimately has an in-flight ``CONTINUING``
    bundle and a head that DESCENDS from the recorded pause checkpoint.  C2 sees only that
    the head moved, so discovery reported ``STALE_CHECKPOINT_HEAD`` for exactly the runs
    C5 was written to recover: ``resume_run`` could recover them, but only an operator who
    already knew the run id could ever call it.  A Coordinator that scans durable state --
    which is what OS-31 promises a new session can do -- never saw a candidate.

    Every test here reads durable bytes only: no in-memory driver survives the crash.
    """

    def listing(self, run_id=None):
        found = {entry["run_id"]: entry for entry in pause_runtime.discover(self.base)}
        return found[run_id or self.RUN]

    def actionable(self):
        return [entry for entry in pause_runtime.discover(self.base)
                if entry["verdict"] in pause_policy.PAUSE_DISCOVERY_ACTIONABLE_VERDICTS]

    def head_lifecycle(self, crashed):
        saver = self.saver()
        return pause_runtime.reconstruct({**crashed, "checkpoint_id": saver.head("t")},
                                         saver)["run_lifecycle"]

    # ---- 1: boundary 3, an ACTIVE descendant head ------------------------------------
    def test_discovery_reports_a_boundary_3_crash_as_a_recoverable_continuation(self):
        """The head is ACTIVE and descends from the pause: recoverable, not stale."""
        crashed = self.crash_at("after_checkpoint_update")
        self.assertEqual(next(iter(crashed["applied"].values()))["stage"], "CONTINUING")
        self.assertEqual(self.head_lifecycle(crashed), "ACTIVE")
        entry = self.listing()
        self.assertEqual(entry["status"], "WAITING_FOR_INPUT")
        self.assertEqual(entry["verdict"],
                         pause_policy.PAUSE_CONTINUATION_RECOVERABLE, entry["detail"])
        self.assertNotEqual(entry["verdict"], "STALE_CHECKPOINT_HEAD",
                            "the defect: the recoverable run was reported as stale")
        self.assertIn(entry["verdict"], pause_policy.PAUSE_DISCOVERY_VERDICTS)

    # ---- 2: boundary 4, a SETTLED descendant head ------------------------------------
    def test_discovery_reports_a_boundary_4_crash_as_a_recoverable_continuation(self):
        """The continuation already finished; the record never learned it.

        Discovery reports the same actionable verdict as boundary 3 -- both are "this
        head is this bundle's own continuation" -- and which side of the effect boundary
        the dead process reached stays the resume's business, reported there by
        ``PAUSE_CONTINUATION_ALREADY_COMPLETE``.
        """
        crashed = self.crash_at("after_invoke", owner_id="host:dead")
        self.assertEqual(next(iter(crashed["applied"].values()))["stage"], "CONTINUING")
        self.assertEqual(self.head_lifecycle(crashed), "SETTLED")
        entry = self.listing()
        self.assertEqual(entry["verdict"],
                         pause_policy.PAUSE_CONTINUATION_RECOVERABLE, entry["detail"])
        self.assertNotEqual(entry["verdict"], "STALE_CHECKPOINT_HEAD")

    # ---- 3: the negative case, which must STILL refuse -------------------------------
    def test_discovery_refuses_a_head_that_does_not_descend_from_the_pause(self):
        """This fix must not turn C2 into a rubber stamp.

        A forked head has moved just like a recoverable one has.  What separates them is
        the checkpoint store's own parent links, and nothing else: the forked head does
        not carry the pause checkpoint among its ancestors, so it is not this bundle's
        continuation, it is not actionable, and it is named as such.
        """
        crashed = self.crash_at("after_checkpoint_update")
        self.fork_the_head_off_the_lineage_root(crashed)
        entry = self.listing()
        self.assertEqual(entry["verdict"], "PAUSE_CONTINUATION_UNRECOVERABLE",
                         entry["detail"])
        self.assertNotIn(entry["verdict"],
                         pause_policy.PAUSE_DISCOVERY_ACTIONABLE_VERDICTS)
        self.assertEqual(self.actionable(), [],
                         "a run nobody may drive is offered to nobody")
        # ...and discovery is read-only: it took no claim and moved no head.
        self.assertEqual(self.store().read(self.RUN)["owner_id"], crashed["owner_id"])
        self.assertEqual(self.saver().head("t"), "forked-off-the-root")

    # ---- 4: the whole path, driven by a Coordinator that knows only the base ----------
    def test_a_fresh_coordinator_discovers_takes_over_and_resumes_exactly_once(self):
        """discover -> takeover -> resume, end to end, with NO prior knowledge of the run.

        The successor below is given one thing: the artifact base.  It learns the run id
        from ``discover``, everything else from the durable record that discovery names,
        and it has its own ``owner_id``, so it must observe the dead owner's lease lapse
        and take the run over before it may touch anything.  This is the path the reviewer
        found untested: the existing crash tests call ``resume_run`` directly with a run
        id the test already held.
        """
        self.crash_at("after_checkpoint_update", owner_id="host:dead")

        # ---- everything from here knows only ``self.base`` ---------------------------
        candidates = self.actionable()
        self.assertEqual(len(candidates), 1, "the crashed run is discoverable")
        found = candidates[0]
        self.assertEqual(found["verdict"], pause_policy.PAUSE_CONTINUATION_RECOVERABLE,
                         found["detail"])
        run_id = found["run_id"]

        successor = pause_store.store_for(run_id, artifact_base=self.base,
                                          owner_id="host:fresh", lease_seconds=0.4)
        projection = successor.read(run_id)["projection"]
        port = self.approval_port()
        adapter = self.adapter((WORKER, REVIEW_PASS, REVIEW_PASS), run_id=run_id)
        outcome = pause_runtime.resume_run(
            run_id, artifact_base=self.base, approval_port=port,
            graph_factory=lambda saver: self.graph(adapter, saver, approval_port=port),
            current_repository=projection["repository_binding"],
            current_artifact=projection["artifact_binding"],
            current_policy_digest=projection["policy_digest"],
            store=successor, recursion_limit=300, observe_timeout_seconds=None)

        self.assertEqual(outcome.status, "RESUMED", outcome.detail)
        self.assertEqual(outcome.code, "PAUSE_CONTINUATION_RECOVERED", outcome.detail)
        self.assertEqual(outcome.state["terminal_status"], "COMPLETED")
        self.assertEqual(adapter.effect_count, 3,
                         "EXACTLY one round of effects exists for the whole run")
        stored = successor.read(run_id)
        self.assertEqual(stored["status"], "RESUMED")
        self.assertEqual(stored["owner_id"], "host:fresh",
                         "the run was taken over, not driven under the dead lease")
        self.assertEqual(next(iter(stored["applied"].values()))["stage"], "RESUMED")

        # ...and the run stops being offered: the next scan finds nothing actionable and
        # a second Coordinator that acts on the settled verdict performs no effect.
        self.assertEqual(self.actionable(), [])
        self.assertEqual(self.listing(run_id)["verdict"], "RUN_ALREADY_RESUMED")
        again, again_adapter = self.attempt(self.paused)
        self.assertEqual(again.status, "NO_EFFECT")
        self.assertEqual(again.code, "RUN_ALREADY_RESUMED")
        self.assertEqual(again_adapter.effect_count, 0)

    # ---- the agreement itself --------------------------------------------------------
    def test_an_untouched_pause_is_still_reported_resumable(self):
        """The fix widens nothing: with no in-flight bundle the verdict is unchanged."""
        entry = self.listing()
        self.assertEqual(entry["verdict"], pause_policy.PAUSE_RESUMABLE, entry["detail"])
        self.assertIsNone(pause_policy.in_flight_bundle(self.store().read(self.RUN)))

    def test_discovery_and_resume_read_the_same_classification(self):
        """One implementation, so the two answers cannot drift apart.

        Asserted against the durable record rather than by mocking: for the untouched
        pause, the boundary-2 crash (stage ahead of the checkpoint) and the boundary-3
        crash, the verdict discovery reports is the verdict ``classify_head`` returns
        inside the claimed section.
        """
        for label, crash in (("untouched", None),
                             ("stage ahead of the checkpoint", "before_checkpoint_update"),
                             ("committed continuation", "after_checkpoint_update")):
            with self.subTest(label):
                if crash is not None:
                    self.crash_at(crash)
                record = self.store().read(self.RUN)
                classified = pause_runtime.classify_head(record, self.saver())
                self.assertEqual(self.listing()["verdict"], classified.verdict)
        self.assertEqual(self.listing()["verdict"],
                         pause_policy.PAUSE_CONTINUATION_RECOVERABLE)


if __name__ == "__main__":  # pragma: no cover - unittest discovery is the entry point
    unittest.main()
