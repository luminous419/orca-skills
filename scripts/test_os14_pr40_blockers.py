#!/usr/bin/env python3
"""PR #40 integration review: the TWO blocking findings, PR40-B1 and PR40-B2.

Both are regressions of the OS-14 foundation (I-1 launch lease, I-7 round-scoped target
lifetime) as it stands at ``46d8843``, and both are driven through the SAME production
path the rest of the OS-14 suite uses -- ``launcher.build_orca_adapter`` ->
``executor.execute_intent_node`` -> ``OrcaAdapter.start`` -> ``_prepare_pair`` -> the
harness's real barrier.  No test here calls ``_prepare_pair``, ``_prepare_role``,
``mint_launch_lease``, ``bind_launch_lease`` or ``bind_prepared_target``, and no test
pre-fills ``_launch_leases``, ``_launch_bindings`` or ``_launch_lease_handles``.

**PR40-B1 -- the successor's NATURAL EMPTY CITATION.**  A new harness has an empty lease
registry, so ``_mint_model_selection_ticket`` offers ``launch_token=""`` /
``launch_stamp=0``, and a CONFORMING driver that cites what the ticket tells it returns
that empty citation.  At ``46d8843`` the launch leg's entry condition was
``if minted is not None or attested_launch_token:`` -- i.e. the question *does this target
owe launch verification* was answered by *is there any launch evidence to hand*, which an
empty registry and an empty citation jointly answer "no".  The scenario is reproduced the
only way it can be honest: the parent process prepares BOTH roles through the production
preparation path, stops at the first ``create_task``, and a **REAL SEPARATE PYTHON
PROCESS** then re-enters the same durable records with the same driver class and the
ticket's own default citation.  Nothing is pre-seeded and no past token is force-cited.

**PR40-B2 -- disposal is not connected to the FAILABLE parts of preparation.**  The order
at ``46d8843`` is ``CREATED -> bind_launch_lease -> bind_prepared_target ->
lifetime.prepared``, so a handle whose bind fails is never registered as the round's
disposal target; and a pre-pass refusal or a real Gate B exception leaves ``_prepare_pair``
by an exception that reaches neither ``_refuse_preparation`` nor the engine's
post-settlement observer, so TL clause 2 never runs for it.

WHAT IS AND IS NOT EVIDENCE HERE.  Every driver below is a STAND-IN at the production
driver boundary (``LeaseCitingDriver`` is OS-49's reference ``InProcessModelDriver`` plus
the two hosted-capability names and the launch citation).  **Nothing here is evidence
about any real Claude or GLM model and no stand-in success is a model-identity
verification.**  What IS evidence is the WIRING.  NO MODEL TURN IS SPENT: nothing here
launches a CLI and no ``claude -p`` runs.
"""
from __future__ import annotations

import dataclasses
import os
import unittest
from typing import Any
from unittest.mock import patch

from scripts.deterministic_workflow import pause_store
from scripts.orca_runtime_harness import (MODEL_SELECTION_REQUEST_ABSENT,
                                          MODEL_SELECTION_REQUEST_STALE,
                                          MODEL_SELECTION_UNVERIFIED,
                                          DRIVER_BIND_UNCONFIRMED,
                                          OrcaRuntimeError)
from scripts.test_os14_launch_lease import (NO_REAL_CLI, LeaseCitingDriver, LeaseRoom)
from scripts.test_os14_pair_preparation import (PHASE, REQUIRES_LANGGRAPH,
                                                ChildProcessRoom, PairRecorder,
                                                scripted_bodies)


def setUpModule() -> None:
    """The same module-wide NO-REAL-CLI guard the rest of the OS-14 suite uses.

    ``OrcaRuntimeHarness.__init__`` resolves the executable unconditionally, so any direct
    construction fails on a host with no Orca CLI -- which every CI runner is.  Every test
    here replaces the harness's single process boundary (``_exec_orca``) with a recorder,
    so the resolved path is never executed; it only has to exist as a STRING.
    """
    global _CLI_GUARD
    _CLI_GUARD = patch.dict(os.environ, {"ORCA_CLI_COMMAND": NO_REAL_CLI})
    _CLI_GUARD.start()


def tearDownModule() -> None:
    if _CLI_GUARD is not None:
        _CLI_GUARD.stop()


_CLI_GUARD: Any = None


class _StopBeforeTaskCreate(BaseException):
    """The parent process ENDS here -- strictly after both roles are prepared and strictly
    before the first ``create_task``.

    A ``BaseException`` on purpose, and the choice is load-bearing twice over.  It is the
    only faithful stand-in for the scenario PR40-B1 is about -- a Coordinator that stops
    existing in that window -- and TL row 14 excludes hard process exit from every cleanup
    promise, so an exception the adapter's cleanup is allowed to act on would quietly
    revoke the very durable lease the successor has to find UNREVOKED.  It therefore also
    asserts the control-flow-exception contract: nothing on the preparation path converts
    a non-``Exception`` into a refusal or swallows it.
    """


# ======================================================================================
# PR40-B1: the successor process
# ======================================================================================
@REQUIRES_LANGGRAPH
class SuccessorLaunchVerificationTests(ChildProcessRoom):
    """PR40-B1.  A target's lease obligation is read off TRUSTWORTHY DURABLE PROVENANCE --
    never off the current registry, the driver's citation or the current capability.

    Built on the OS-14 suite's own cross-process machinery (``ChildProcessRoom``): the
    predecessor is driven through the REAL ``launcher.execute_state`` so the run leaves a
    real committed checkpoint head, and the successor is a REAL separate ``python3``
    process pointed at the predecessor's own durable documents AND its own durable
    execution ledger.  It is handed only facts about the world OUTSIDE both processes --
    the terminal listing a real runtime would answer with -- and NO ledger row, NO model
    authority and NO lease: that is exactly what production adoption must do for itself.

    ``@REQUIRES_LANGGRAPH`` because the predecessor's committed head is what makes the
    successor's ``declared_run_identity_for_run`` read feasible rather than
    fixture-supplied, and a committed head needs the pinned runtime.  That is the same
    real dependency ``T7NewProcessRecoveryTests`` declares, and the only reason these
    tests appear in ``scripts/langgraph_skip_manifest.txt``.
    """

    def predecessor_killed_before_the_first_task(self, *, driver):
        """Both roles prepared and VERIFIED; the process then STOPS EXISTING.

        The stop is a ``BaseException``, and that choice is load-bearing rather than
        incidental.  TL row 14 excludes hard process exit / ``SIGKILL`` from every cleanup
        promise, and PR40-B2's fix connects disposal to every CATCHABLE failure of
        preparation -- so an ordinary ``Exception`` here would correctly revoke the round's
        leases and the successor would then meet a REVOKED durable record, which is a
        different (and already-covered) refusal.  PR40-B1 is about the record that is
        still LIVE, which is what a killed Coordinator really leaves behind.  It therefore
        also asserts the control-flow-exception contract: nothing on the preparation path
        converts a non-``Exception`` into a refusal or swallows it.
        """
        launch = self.launch(driver=driver)
        original = launch.harness.create_task

        def killed(spec, **kwargs):
            raise _StopBeforeTaskCreate("the Coordinator stopped existing here")

        launch.harness.create_task = killed
        try:
            self.execute(launch)
        except BaseException:                     # the predecessor "dies" here
            pass
        launch.harness.create_task = original
        self.assertEqual(launch.entry("worker")["stage"], "VERIFIED")
        self.assertEqual(launch.entry("reviewer")["stage"], "VERIFIED")
        self.assertEqual(len(launch.recorder.pair_handles), 2)
        self.assertEqual(sum(1 for c in launch.recorder.commands
                             if c[1:2] == ("task-create",)), 0,
                         "the predecessor issued NO task-create")
        self.assertIsNotNone(self._head(),
                             "the predecessor committed a real checkpoint head")
        return launch

    def assertLiveUnrevokedLeaseOnRecord(self, launch, *, role="worker"):
        """The precondition PR40-B1 is about, asserted rather than assumed."""
        cells = launch.adapter.pair_preparation.launch_lease(PHASE, 1, role)
        self.assertIsNotNone(cells, f"the {role} slot recorded no launch lease")
        self.assertTrue(cells["launch_token"])
        self.assertFalse(cells["launch_revoked_at"],
                         "a hard process exit must leave the lease UNREVOKED -- row 14 "
                         "promises no cleanup and this scenario depends on it")
        self.assertTrue(cells["terminal_digest"],
                        "the record must name the session it was minted for")
        return cells

    # ---- the core reproduction ----------------------------------------------------
    def test_a_real_successor_with_the_tickets_own_empty_citation_delivers_nothing(self):
        """PR40-B1.  The successor's registry is empty, so the ticket offers
        ``launch_token=""`` / ``launch_stamp=0`` and a CONFORMING driver returns that
        citation unchanged.  At ``46d8843`` the launch leg was skipped before the durable
        lease was even read and the run DELIVERED.

        Every number below is a COUNT of commands the successor's own process boundary
        received -- not an absence someone asserted -- and the scenario stopped before the
        first Task, so a correct successor adds no Task either.
        """
        launch = self.predecessor_killed_before_the_first_task(
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        self.assertLiveUnrevokedLeaseOnRecord(launch)
        intent_id = self.interrupted_intent_id(launch)
        self.assertLiveUnsettledClaim(launch, intent_id)
        report = self.child(mode="rebound", driver_class="lease_citing",
                            listing=self.listing_of(launch))
        self.assertIsNone(report["construction_error"])
        self.assertIsNotNone(
            report["execute_error"],
            "the successor DELIVERED on an EMPTY launch citation: status="
            f"{report.get('terminal_status')!r} worker_starts={report['worker_starts']}")
        self.assertIn(MODEL_SELECTION_REQUEST_ABSENT, report["execute_error"])
        self.assertEqual(
            {"pair_creates": report["pair_creates"],
             "task_creates": report["task_creates"],
             "worker_starts": report["worker_starts"],
             "dispatches": report["dispatches"], "sends": report["sends"]},
            {"pair_creates": 0, "task_creates": 0, "worker_starts": 0, "dispatches": 0,
             "sends": 0},
            "a refused successor performed an effect")
        # NEVER repaired: no rebind, no relaunch, no binding restored from the record.
        self.assertEqual(report["driver_binds"], [])
        self.assertEqual(report["live_leases"], [])
        self.assertEqual(report["live_bindings"], [])
        # and no successful settlement of the intent it inherited
        settled = launch.ledger.get_receipt(intent_id)
        self.assertIsNone(settled["settlement"],
                          "the successor SETTLED an intent it never delivered")

    def test_the_successors_refusal_leaves_the_durable_lease_exactly_as_it_found_it(self):
        """A refusal is not a disposal: the successor may not revoke, re-stamp or
        otherwise write authority it never held."""
        launch = self.predecessor_killed_before_the_first_task(
            driver=LeaseCitingDriver(resolve=lambda requested: requested))
        before = {role: self.assertLiveUnrevokedLeaseOnRecord(launch, role=role)
                  for role in ("worker", "reviewer")}
        self.child(mode="rebound", driver_class="lease_citing",
                   listing=self.listing_of(launch))
        for role, cells in before.items():
            after = launch.adapter.pair_preparation.launch_lease(PHASE, 1, role)
            self.assertEqual(after["launch_token"], cells["launch_token"],
                             f"the successor rewrote the {role} launch token")
            self.assertFalse(after["launch_revoked_at"],
                             f"the successor revoked a {role} lease it never held")

    # ---- the positive controls the fix may not break -------------------------------
    def test_the_same_process_positive_control_still_verifies_and_delivers(self):
        """One process, one lease, one binding per session: the control for the two tests
        above, through the same production door.

        Read off the DRIVER's own bind log rather than off the harness registry at the
        end of the run: a successful round legitimately RELEASES at its close (TL clause
        1), so "still bound afterwards" is not the property -- "bound, verified and
        delivered while the round was open" is.
        """
        driver = LeaseCitingDriver(resolve=lambda requested: requested)
        launch = self.launch(driver=driver)
        final = self.execute(launch)
        self.assertNotEqual(final.get("terminal_status"), "BLOCKED",
                            f"the positive control was refused: "
                            f"{final.get('terminal_reason')}")
        self.assertEqual(sorted({handle for handle, _token, _nonce in driver.binds}),
                         sorted(set(launch.recorder.pair_handles)),
                         "every prepared session was bound by the driver exactly once")
        self.assertGreaterEqual(
            sum(1 for c in launch.recorder.commands if c[1:2] == ("worker-start",)), 1)

    def test_a_genuine_legacy_successor_with_no_lease_on_record_is_unaffected(self):
        """The genuine LEGACY / reference path: no lease was EVER recorded for the target.

        The reference driver is not hosted-capable, so ``mint_launch_lease`` returns
        ``None``, no lease cell is written, and the successor's provenance read is a
        POSITIVE "this run recorded no launch for this slot".  That is the one state that
        may read as legacy and the fix may not close it -- here through the SAME
        cross-process door, so the comparison is like for like.
        """
        launch = self.predecessor_killed_before_the_first_task(driver="reference")
        self.assertIsNone(
            launch.adapter.pair_preparation.launch_lease(PHASE, 1, "worker"),
            "a non-hosted-capable driver recorded a launch lease")
        report = self.child(mode="rebound", listing=self.listing_of(launch))
        self.assertIsNone(report["construction_error"])
        self.assertIsNone(
            report["execute_error"],
            f"a genuine legacy successor was refused: {report['execute_error']}")
        self.assertGreaterEqual(report["worker_starts"], 1,
                                "the legacy successor must still deliver")
        self.assertEqual(report["live_leases"], [],
                         "a legacy run holds no lease, before or after")


class UnreadableProvenanceTests(LeaseRoom):
    """PR40-B1: an UNANSWERED provenance question is never a verified no-lease state.

    Three states and three outcomes, each with its own test below:

    | provenance state | this class asserts |
    | --- | --- |
    | `unreadable` -- the reader RAISES (a store failure, a corrupt record) | refused `model_selection_unverified`, by a message that says the record could not be READ |
    | `no_reader` -- no durable reader is wired at all (iteration 2, F-001) | refused `model_selection_request_absent`, with ZERO further effects |
    | `no_launch_recorded` -- a WORKING reader positively reports none | NOT refused: the genuine legacy / reference path |

    Every case reaches the state only after a REAL hosted preparation through the
    production path, so the mint, the durable write, the create and the bind all ran for
    real.  What is then removed is removed, never fabricated: the in-process lease
    registry is CLEARED the way a successor's is empty, and the reader is set to `None`
    through its own public setter.  No private map is ever pre-FILLED.
    """

    @staticmethod
    def effect_counts(launch) -> dict[str, int]:
        """Every externally visible effect of this run, counted off the ONE `orca` process
        boundary.  Snapshotted before and after a probe so "zero effects" is a DELTA over
        a fixture that legitimately produced some."""
        commands = launch.recorder.commands
        return {
            "terminal_create": sum(1 for c in commands if c[:2] == ("terminal", "create")),
            "task_create": sum(1 for c in commands if c[1:2] == ("task-create",)),
            "worker_start": sum(1 for c in commands if c[1:2] == ("worker-start",)),
            "dispatch": sum(1 for c in commands if c[1:2] == ("dispatch",)),
            "send": sum(1 for c in commands if c[:2] == ("terminal", "send")),
        }

    def test_an_unreadable_durable_provenance_is_refused_by_its_own_name(self):
        """The provenance reader RAISES.  Unanswerable is not "it was fine".

        Injected at the STORE boundary -- the one external boundary this scenario is
        about -- and only after both roles were prepared on the real path, so the mint,
        the durable write, the create and the bind all ran for real.  The in-process lease
        REGISTRY is then dropped the way a successor's is; nothing is pre-filled, and the
        handle keeps its real ledger row and its real durable record.
        """
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle = launch.recorder.pair_handles[0]
        store = launch.adapter.pair_preparation
        launch.harness._launch_lease_handles.clear()

        def raising(phase, gate_iteration, role):
            raise pause_store.PairPreparationCorrupt(
                "PAIR_PREPARATION_CORRUPT:the lease cells cannot be read")

        store.launch_lease = raising
        with self.assertRaises(OrcaRuntimeError) as caught:
            launch.harness.verify_model_identity("", handle, role="worker", phase=PHASE,
                                                 attempt=1)
        message = str(caught.exception)
        self.assertIn(MODEL_SELECTION_UNVERIFIED, message)
        self.assertIn("could not be read", message)
        self.assertNotIn("recorded no launch", message)

    def test_an_absent_durable_reader_is_refused_fail_closed(self):
        """F-001 (iteration 2).  NO reader is wired at all.

        `_launch_provenance` answers `no_reader`, and iteration 1 grouped that with the
        document's POSITIVE `no_launch_recorded` and returned -- which made an UNANSWERED
        question permissive and contradicted `set_launch_lease_reader`'s own contract
        ("Leaving it unwired is FAIL-CLOSED, not permissive ... an unconfirmable citation
        is `model_selection_request_absent`").  The reviewer's counterexample reached it
        exactly this way and verification was ACCEPTED.

        The scenario is built the only way it can be honest: a REAL hosted launch, really
        minted, really bound and really recorded durably by the production preparation
        path; then the live authority is CLEARED (not forged) and the reader is REMOVED
        through its own public setter; then the barrier is called with the ticket's natural
        empty citation.

        Every effect count is a DELTA across the probe, snapshotted immediately before it
        -- the fixture legitimately created sessions, a Task and a delivery, so "zero
        effects" can only mean "this refused call added none".
        """
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle = launch.recorder.pair_handles[0]
        # the REAL durable record this obligation is about, asserted rather than assumed
        cells = self.lease_cells(launch.adapter, role="worker")
        self.assertTrue(cells["launch_token"])
        self.assertFalse(cells["launch_revoked_at"])
        before = self.effect_counts(launch)
        bound_before = len(launch.driver.binds)
        lease_before = launch.harness.launch_lease(cells["launch_token"])
        # The live BINDING of handle -> lease is dropped, which is precisely what a
        # successor process lacks; the lease row the real mint produced is left exactly as
        # it is, so the assertions below can prove the refusal MUTATED nothing rather than
        # prove something that was deleted stayed deleted.
        launch.harness._launch_lease_handles.clear()
        launch.harness.set_launch_lease_reader(None)
        self.assertIsNone(launch.harness.launch_lease_for_handle(handle))
        self.assertIsNone(launch.harness.prepared_target(handle))

        with self.assertRaises(OrcaRuntimeError) as caught:
            launch.harness.verify_model_identity("", handle, role="worker", phase=PHASE,
                                                 attempt=1)
        message = str(caught.exception)
        self.assertIn(MODEL_SELECTION_REQUEST_ABSENT, message)
        self.assertIn("no durable launch-lease reader", message)
        self.assertNotIn("recorded no launch", message,
                         "reader ABSENCE was reported as a verified no-lease state")
        # ZERO further effects, and no authority invented to resolve the question
        self.assertEqual(self.effect_counts(launch), before,
                         "a fail-closed refusal performed an effect")
        self.assertEqual(len(launch.driver.binds), bound_before,
                         "the refusal re-bound the target")
        self.assertIsNone(launch.harness.launch_lease_for_handle(handle),
                          "the refusal restored a lease it never held")
        self.assertIsNone(launch.harness.prepared_target(handle),
                          "the refusal restored a binding it never held")
        self.assertEqual(launch.harness.launch_lease(cells["launch_token"]), lease_before,
                          "the refusal mutated the lease row it only read")
        after = self.lease_cells(launch.adapter, role="worker")
        self.assertEqual(after, cells,
                         "the refusal wrote to the durable record it only read")

    def test_an_absent_reader_on_a_target_with_no_hosted_obligation_is_unchanged(self):
        """The companion control for the branch above: NO reader AND no hosted
        capability.  A harness that can mint no lease at all can hold none to be missing,
        so the default / reference path must stay exactly as it was."""
        launch = self.hosted_launch(driver="reference")
        self.dispatch(launch)
        handle = launch.recorder.pair_handles[0]
        self.assertFalse(launch.harness.hosted_delivery_capable())
        launch.harness._launch_lease_handles.clear()
        launch.harness.set_launch_lease_reader(None)
        launch.harness.verify_model_identity("", handle, role="worker", phase=PHASE,
                                             attempt=1)          # must not raise

    def test_a_target_the_document_states_no_launch_for_is_not_refused(self):
        """The companion control: a POSITIVE "no launch recorded for this slot" is the one
        provenance answer that still reads as legacy, through the same code path."""
        launch = self.hosted_launch(driver="reference")
        self.dispatch(launch)
        handle = launch.recorder.pair_handles[0]
        self.assertIsNone(self.lease_cells(launch.adapter, role="worker"))
        launch.harness._launch_lease_handles.clear()
        launch.harness.verify_model_identity("", handle, role="worker", phase=PHASE,
                                             attempt=1)          # must not raise


# ======================================================================================
# PR40-B2: round disposal on every failable boundary
# ======================================================================================
class BindFailureDriver(LeaseCitingDriver):
    """``bind_target`` answers ``None`` for ONE role -- the production shape of
    ``DRIVER_BIND_UNCONFIRMED``."""

    def __init__(self, *, failing_role="reviewer", **kwargs) -> None:
        super().__init__(**kwargs)
        self.failing_role = failing_role

    def bind_target(self, handle, lease, *, bind_nonce):
        if lease.role == self.failing_role:
            return None
        return super().bind_target(handle, lease, bind_nonce=bind_nonce)


class GateBRaisingDriver(LeaseCitingDriver):
    """``select_and_verify`` raises at the REAL Gate B -- the ticket a DELIVERY mints is
    the only one that carries a ``task_id``, so the pre-pass is untouched."""

    def select_and_verify(self, ticket):
        if ticket.task_id:
            raise RuntimeError("Gate B verification failed")
        return super().select_and_verify(ticket)


class GateBForgingDriver(LeaseCitingDriver):
    """A FORGED launch citation at the real Gate B, and only there."""

    def select_and_verify(self, ticket):
        evidence = super().select_and_verify(ticket)
        if ticket.task_id:
            return dataclasses.replace(evidence, launch_token="launch:forged",
                                       launch_stamp=1)
        return evidence


#: The REVIEWER's declared model, made UNSATISFIABLE.  The reference driver's own
#: satisfaction rule then reports `mismatch` for that role, which is how the rest of the
#: OS-14 suite produces a pre-pass refusal (`ChildProcessRoom.predecessor`, stop point
#: B7/B8) -- the driver decides satisfaction, as the barrier's contract says, and nothing
#: here reaches inside the barrier to force a verdict.  The REVIEWER is chosen on purpose:
#: `_prepare_pair` prepares BOTH roles before it verifies either, so both sessions exist
#: when this refuses.
UNSATISFIABLE_REVIEWER = {"glm-5.3-flash": "glm-5.9-unsatisfied"}


class RoundDisposalTests(LeaseRoom):
    """PR40-B2.  Every failable boundary of one round disposes of that round's targets --
    live AND durably -- without turning the failure into a success or masking it."""

    # ---- shared assertions --------------------------------------------------------
    def assertRoundDisposed(self, launch, *, handles, expect_durable=True):
        """Both halves of TL clause 2, for every handle the round prepared."""
        tracker = launch.adapter.round_lifetime()
        tracked = tracker.prepared_handles(phase=PHASE, attempt=1)
        for handle in handles:
            self.assertIn(handle, tracked,
                          f"{handle!r} was created but never registered as a disposal "
                          "target of its round")
            self.assertEqual(tracker.release_count(handle), 1,
                             f"{handle!r} was not released exactly once")
            self.assertIsNone(launch.harness.prepared_target(handle),
                              f"{handle!r} still holds a LIVE binding")
        for role in ("worker", "reviewer"):
            cells = self.lease_cells(launch.adapter, role=role)
            if cells is None:
                continue
            lease = launch.harness.launch_lease(cells["launch_token"])
            self.assertTrue(lease is None or lease.revoked,
                            f"the {role} lease is still LIVE after the round failed")
            if expect_durable:
                self.assertTrue(cells["launch_revoked_at"],
                                f"the {role} lease was not durably revoked")

    def assertNoDelivery(self, launch):
        for verb in ("worker-start", "send", "dispatch"):
            self.assertEqual(
                sum(1 for c in launch.recorder.commands if verb in c), 0,
                f"a PRE-DELIVERY refusal still ran {verb!r}")

    def assertDisposalIdempotent(self, launch, *, handles):
        tracker = launch.adapter.round_lifetime()
        self.assertEqual(launch.adapter.dispose_round(phase=PHASE, attempt=1,
                                                      reason="second"), ())
        for handle in handles:
            self.assertEqual(tracker.release_count(handle), 1,
                             f"{handle!r} was released twice")

    def assertReuseRefused(self, launch, *, handle):
        """A released target is never re-verified and never re-bound."""
        with self.assertRaises(OrcaRuntimeError) as caught:
            launch.harness.verify_model_identity("", handle, role="worker", phase=PHASE,
                                                 attempt=1)
        self.assertIn(MODEL_SELECTION_REQUEST_STALE, str(caught.exception))

    # ---- bind failures ------------------------------------------------------------
    def test_a_reviewer_bind_failure_disposes_the_round_it_already_created(self):
        """The reviewer's bind is refused AFTER the worker's session already exists."""
        launch = self.hosted_launch(
            driver=BindFailureDriver(failing_role="reviewer", resolve=lambda r: r))
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch)
        self.assertIn(DRIVER_BIND_UNCONFIRMED, str(caught.exception),
                      "cleanup replaced the original named refusal")
        handles = launch.recorder.pair_handles
        self.assertEqual(len(handles), 2)
        self.assertRoundDisposed(launch, handles=handles)
        self.assertNoDelivery(launch)
        self.assertDisposalIdempotent(launch, handles=handles)
        self.assertReuseRefused(launch, handle=handles[0])

    def test_a_worker_bind_failure_disposes_the_one_session_that_exists(self):
        """The FIRST role's bind is refused: exactly one session was ever created, and it
        is the round's disposal target even though nothing bound to it."""
        launch = self.hosted_launch(
            driver=BindFailureDriver(failing_role="worker", resolve=lambda r: r))
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch)
        self.assertIn(DRIVER_BIND_UNCONFIRMED, str(caught.exception))
        handles = launch.recorder.pair_handles
        self.assertEqual(len(handles), 1,
                         "the reviewer session must never have been created")
        self.assertRoundDisposed(launch, handles=handles)
        self.assertNoDelivery(launch)
        self.assertDisposalIdempotent(launch, handles=handles)

    # ---- the pre-pass -------------------------------------------------------------
    def test_a_pre_pass_refusal_disposes_the_round(self):
        """The pre-pass verification refuses, which is an OrcaRuntimeError out of
        ``_prepare_pair`` and reaches neither ``_refuse_preparation`` nor the engine's
        post-settlement observer."""
        launch = self.hosted_launch(driver=LeaseCitingDriver(
            resolve=lambda requested: UNSATISFIABLE_REVIEWER.get(requested, requested)))
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch)
        original = str(caught.exception)
        handles = launch.recorder.pair_handles
        self.assertEqual(len(handles), 2)
        self.assertRoundDisposed(launch, handles=handles)
        self.assertNoDelivery(launch)
        self.assertDisposalIdempotent(launch, handles=handles)
        self.assertIn("model_selection", original,
                      "the original barrier refusal was replaced by its own cleanup")

    # ---- the REAL Gate B ----------------------------------------------------------
    def test_a_real_gate_b_driver_exception_disposes_the_round(self):
        """Pre-pass succeeds; the DELIVERY's own barrier call raises."""
        launch = self.hosted_launch(driver=GateBRaisingDriver(resolve=lambda r: r))
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch)
        self.assertIn(MODEL_SELECTION_UNVERIFIED, str(caught.exception))
        handles = launch.recorder.pair_handles
        self.assertEqual(len(handles), 2)
        self.assertRoundDisposed(launch, handles=handles)
        self.assertEqual(sum(1 for c in launch.recorder.commands if "worker-start" in c),
                         0, "Gate B refused and the delivery still ran")
        self.assertDisposalIdempotent(launch, handles=handles)

    def test_a_forged_gate_b_launch_citation_disposes_the_round(self):
        """The same boundary reached by a FORGED citation rather than an exception."""
        launch = self.hosted_launch(driver=GateBForgingDriver(resolve=lambda r: r))
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch)
        self.assertIn(MODEL_SELECTION_REQUEST_ABSENT, str(caught.exception))
        handles = launch.recorder.pair_handles
        self.assertRoundDisposed(launch, handles=handles)
        self.assertEqual(sum(1 for c in launch.recorder.commands if "worker-start" in c),
                         0)

    # ---- the DELIVERY boundary ----------------------------------------------------
    def test_a_delivery_failure_after_the_task_exists_disposes_without_redelivering(self):
        """The Task and the delivery attempt EXIST and the delivery fails UNKNOWN.

        The obligation the shipped path records is preserved, nothing settles
        successfully, nothing is re-delivered and nothing is delivered to another target
        -- and the round's targets are disposed of all the same.
        """
        recorder = PairRecorder(scripted_bodies(self.next_run_id()),
                                run_id=self._pending_run_id,
                                fail_verbs={"worker-start"})
        launch = self.hosted_launch(recorder=recorder, run_id=self._pending_run_id)
        with self.assertRaises(Exception) as caught:
            self.dispatch(launch)
        self.original_delivery_failure = caught.exception
        handles = launch.recorder.pair_handles
        self.assertEqual(len(handles), 2)
        self.assertTrue(any("task-create" in c for c in recorder.commands),
                        "the Task must exist for this boundary")
        self.assertEqual(sum(1 for c in recorder.commands if "worker-start" in c), 1,
                         "exactly ONE delivery attempt, never a second")
        self.assertRoundDisposed(launch, handles=handles)
        self.assertDisposalIdempotent(launch, handles=handles)

    # ---- failures DURING cleanup --------------------------------------------------
    def test_an_additional_failure_during_cleanup_is_recorded_and_skips_no_handle(self):
        """A release that itself raises may not mask the original failure and may not
        silently skip the OTHER prepared handles' cleanup attempts."""
        launch = self.hosted_launch(
            driver=BindFailureDriver(failing_role="reviewer", resolve=lambda r: r))
        attempted: list[str] = []
        real_release = launch.harness.release

        def failing_release(handle):
            attempted.append(handle)
            if handle == launch.recorder.pair_handles[0]:
                raise RuntimeError("the channel teardown itself failed")
            return real_release(handle)

        launch.harness.release = failing_release
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch)
        self.assertIn(DRIVER_BIND_UNCONFIRMED, str(caught.exception),
                      "a cleanup error replaced the original failure")
        handles = launch.recorder.pair_handles
        self.assertEqual(sorted(attempted), sorted(handles),
                         "a cleanup error skipped the other prepared handle")
        tracker = launch.adapter.round_lifetime()
        errors = [row for row in getattr(tracker, "release_errors", [])]
        self.assertTrue(errors, "the additional cleanup error was not recorded anywhere")
        self.assertIn("the channel teardown itself failed", repr(errors))

    def test_an_injected_durable_write_failure_claims_no_durable_cleanup(self):
        """The durable half of the release fails.  The live half still happened, the other
        handle is still cleaned up, the failure is diagnosed -- and nothing claims the
        durable revocation landed."""
        launch = self.hosted_launch(
            driver=BindFailureDriver(failing_role="reviewer", resolve=lambda r: r))
        store = launch.adapter.pair_preparation
        real = store.record_launch_revocation

        def failing(phase, gate_iteration, role, **kwargs):
            if role == "worker":
                raise OSError("the preparation document is not writable")
            return real(phase, gate_iteration, role, **kwargs)

        store.record_launch_revocation = failing
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch)
        self.assertIn(DRIVER_BIND_UNCONFIRMED, str(caught.exception))
        handles = launch.recorder.pair_handles
        worker = self.lease_cells(launch.adapter, role="worker")
        reviewer = self.lease_cells(launch.adapter, role="reviewer")
        # the LIVE half happened for both, durably only where the store was healthy
        self.assertTrue(launch.harness.launch_lease(worker["launch_token"]).revoked)
        self.assertTrue(launch.harness.launch_lease(reviewer["launch_token"]).revoked)
        self.assertFalse(worker["launch_revoked_at"],
                         "the injected write failure must leave NO durable stamp")
        self.assertTrue(reviewer["launch_revoked_at"],
                        "the healthy slot's durable revocation was skipped")
        tracker = launch.adapter.round_lifetime()
        self.assertIn("not writable", repr(getattr(tracker, "release_errors", [])),
                      "the durable-write failure was not diagnosed")
        self.assertEqual(len(handles), 2)

    # ---- the NORMAL round: nothing released early ---------------------------------
    def test_a_normal_high_round_still_holds_both_targets_through_the_whole_round(self):
        """The control for every test above: a successful HIGH pair releases at the
        round's CLOSE and never before it."""
        launch = self.hosted_launch()
        self.dispatch(launch)
        handles = launch.recorder.pair_handles
        tracker = launch.adapter.round_lifetime()
        for handle in handles:
            self.assertEqual(tracker.release_count(handle), 0,
                             f"{handle!r} was released before its round closed")
        for role in ("worker", "reviewer"):
            cells = self.lease_cells(launch.adapter, role=role)
            self.assertFalse(launch.harness.launch_lease(
                cells["launch_token"]).revoked)
            self.assertFalse(cells["launch_revoked_at"])


if __name__ == "__main__":                                  # pragma: no cover
    unittest.main()
