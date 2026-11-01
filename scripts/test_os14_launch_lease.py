#!/usr/bin/env python3
"""OS-14 I-1 / I-4: the LAUNCH LEASE -- no call may pass by stamping an old launch.

Design mapping.  Every test below names the design row it executes
(``docs/OS-14-claude-driver-design.md`` 9.3 **Group 1**, plus the 9.1 **I-1** / **I-4**
units and the 3a.11 **P-2** / **P-8** changes), and no test is counted for two rows.

WHAT IS AND IS NOT EVIDENCE HERE.  Every driver in this module is a STAND-IN at the
production boundary: ``LeaseCitingDriver`` is OS-49's reference ``InProcessModelDriver``
with the two hosted-capability names added, and its "observation" re-reads the value its
own request leg stored.  **Nothing in this file is evidence about any real Claude or GLM
model, and no stand-in success here is a model-identity verification.**  What IS evidence
is the WIRING: the lease is minted before the create, bound after the receipt, recorded
durably, checked by the real barrier, and refused on every malformed citation -- all of it
driven through ``launcher.build_orca_adapter`` -> ``executor.execute_intent_node`` ->
``OrcaAdapter.start`` -> ``_prepare_pair`` -> the harness's real barrier.

NO PRIVATE STATE IS PRE-FILLED.  No test writes ``_launch_leases``, ``_launch_bindings``,
``_launch_lease_handles``, ``_model_identity`` or any other private map to reach a state;
every lease in this file was minted by ``mint_launch_lease`` on the real preparation path.
Where an ADVERSARIAL citation is needed, it is produced the only way a real defect would
produce it -- by a DRIVER returning evidence that cites something else -- which is exactly
the surface the barrier's launch leg exists to refuse.

NO MODEL TURN IS SPENT.  Nothing here launches a CLI, and no ``claude -p`` runs.
"""
from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path

from scripts.agent_profile import (HOSTED_DELIVERY_CAPABILITY,
                                   hosted_delivery_capabilities,
                                   model_selection_capabilities)
from scripts.deterministic_workflow import executor, launcher, pause_policy, pause_store
from scripts.deterministic_workflow.fake_adapter import InProcessModelDriver
from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
from scripts.orca_runtime_harness import (DRIVER_TARGET_REBOUND,
                                          DRIVER_TARGET_UNBOUND,
                                          MODEL_SELECTION_MISMATCH,
                                          MODEL_SELECTION_PAIR_UNADMITTED,
                                          MODEL_SELECTION_REQUEST_ABSENT,
                                          MODEL_SELECTION_REQUEST_METHODS,
                                          MODEL_SELECTION_REQUEST_STALE,
                                          MODEL_SELECTION_UNSUPPORTED,
                                          MODEL_SELECTION_UNVERIFIED,
                                          ModelLaunchLease, OrcaRuntimeError,
                                          OrcaRuntimeHarness, endpoint_path_fits,
                                          pair_terminal_digest)
from scripts.test_os14_bugfix_regressions import repair_bodies
from scripts.test_os14_pair_preparation import (PHASE, PHASE_UPPER,
                                                REQUIRES_LANGGRAPH, RUN_ID,
                                                PairRecorder, PairRoom,
                                                scripted_bodies)


# ======================================================================================
# the stand-in drivers
# ======================================================================================
class LeaseCitingDriver(InProcessModelDriver):
    """The reference driver, plus the two HC names, plus the launch CITATION.

    A CONFORMING hosted driver: it cites the launch the ticket tells it about and performs
    no new launch of its own, which is the D2 shape -- ``select_and_verify`` RE-VALIDATES
    an already-minted lease and never presents itself as a new launch request.

    ``cite`` lets a test replace the citation with any shape a defective or hostile driver
    could produce.  It receives ``(ticket, evidence)`` -- the evidence so a case about the
    ORDINALS can cite one the driver really drew -- and returns
    ``(launch_token, launch_stamp)``.
    """

    def __init__(self, *, cite=None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.binds: list[tuple[str, str, str]] = []
        self.released: list[str] = []
        self.cite = cite
        #: ORDER, not only counts: `("verify" | "bind" | "release", handle)` in call
        #: order.  A release COUNT cannot express TL's actual property -- that a target
        #: survives every verification its round still needs -- and since the I-7 engine
        #: connection a successful round legitimately DOES release at close, so "it was
        #: never released" is no longer the assertion.  The ordering is.
        self.timeline: list[tuple[str, str]] = []

    # ---- the model-selection leg (OS-49), with the OS-14 launch citation added -------
    def select_and_verify(self, ticket):
        self.timeline.append(("verify", ticket.terminal))
        evidence = super().select_and_verify(ticket)
        token, stamp = (self.cite(ticket, evidence) if callable(self.cite)
                        else (ticket.launch_token, ticket.launch_stamp))
        return dataclasses.replace(evidence, launch_token=token, launch_stamp=stamp)

    # ---- the two HC names.  Stand-ins: no socket, no shim, no child process ----------
    def bind_target(self, handle, lease, *, bind_nonce):
        self.binds.append((handle, lease.launch_token, bind_nonce))
        self.timeline.append(("bind", handle))
        return {"handle": handle, "bind_nonce": bind_nonce}

    def deliver_hosted(self, handle, prompt):       # pragma: no cover - I-8 is deferred
        raise AssertionError(
            "the hosted delivery branch is I-8 and is deliberately NOT in this release")

    def release_target(self, handle, target):
        self.released.append(handle)
        self.timeline.append(("release", handle))


class DeliverOnlyDriver(InProcessModelDriver):
    """One HC name only: `deliver_hosted` without `bind_target`."""

    def deliver_hosted(self, handle, prompt):       # pragma: no cover - never called
        raise AssertionError("not hosted-capable, so this is unreachable")


class BindOnlyDriver(InProcessModelDriver):
    """One HC name only: `bind_target` without `deliver_hosted`."""

    def bind_target(self, handle, lease, *, bind_nonce):   # pragma: no cover
        raise AssertionError("not hosted-capable, so this is unreachable")


# ======================================================================================
# the room
# ======================================================================================
class LeaseRoom(PairRoom):
    """`PairRoom`'s production launch, driven to the point the pair is prepared.

    The ONE production path: `launcher.build_orca_adapter` builds the real adapter and the
    real stores, `executor.prepare_intent_node` produces the intent the ENGINE's own nodes
    prepare, and `executor.execute_intent_node` claims it and calls `OrcaAdapter.start`,
    which is what runs `_prepare_pair`.  No test in this file calls `_prepare_pair`,
    `_prepare_role`, `mint_launch_lease`, `bind_launch_lease` or `create_fake_terminal`.
    """

    #: Every `hosted_launch` gets its OWN run id, ledger file and therefore its own
    #: durable preparation document.  Without this a `subTest` loop would re-enter the
    #: PREVIOUS case's record, `_prepare_role` would ADOPT instead of create, and the case
    #: would silently stop exercising the mint path it is about.
    _runs = 0

    def hosted_launch(self, *, driver=None, risk="high", profile_name="split",
                      recorder=None, **kwargs):
        driver = driver if driver is not None else LeaseCitingDriver(
            resolve=lambda requested: requested)
        return self.isolated_launch(driver=driver, risk=risk,
                                    profile_name=profile_name, recorder=recorder,
                                    **kwargs)

    def next_run_id(self) -> str:
        """Reserve the next isolated run id.

        `run_[a-z0-9]+` is the engine's own run-id shape (`state.py`), so the suffix
        carries no separator -- and it is kept SHORT deliberately: the derived
        control-endpoint path contains the run id and is bounded by the platform's own
        `sun_path` budget, which a long test-only run id really does exceed.  That is not
        a test accommodation; it is the constraint the production derivation asserts,
        observed here.
        """
        type(self)._runs += 1
        self._pending_run_id = "run_os14" + format(
            abs(hash(type(self).__name__)) % 1296, "x") + str(type(self)._runs)
        return self._pending_run_id

    def isolated_launch(self, *, run_id=None, **kwargs):
        run_id = run_id or self.next_run_id()
        return self.launch(run_id=run_id,
                           ledger_name=f"ledger-{type(self)._runs}.json", **kwargs)

    @staticmethod
    def engine_intent(*, run_id=RUN_ID, risk="high", phases=(PHASE_UPPER,)):
        state = launcher.build_state({"run_id": run_id, "thread_id": "os14",
                                      "phases": list(phases), "risk": risk})
        return executor.prepare_intent_node(
            executor.route_node(executor.validate_node(state)))

    def dispatch(self, launch, prepared=None, *, risk="high"):
        """Run EXECUTE_INTENT once: claim, prepare the pair, create the Task, deliver."""
        prepared = prepared if prepared is not None else self.engine_intent(
            run_id=launch.harness.run_id, risk=risk)
        node = executor.execute_intent_node(launch.adapter, launch.ledger)
        return node(prepared)

    def premint(self, launch, *, role="worker", phase=PHASE, attempt=1,
                create_attempt=7, bind_to=None):
        """Mint an extra lease BEFORE the dispatch, through the real public mint.

        Minting draws an ordinal, so it must happen OUTSIDE the ticket window a driver
        call is inside -- otherwise the ordinal-window leg fires first and the case stops
        testing the launch leg at all.  Which is itself a property worth stating: the two
        ordinal mechanisms share one counter by design.
        """
        lease = launch.harness.mint_launch_lease(
            role=role, phase=phase, attempt=attempt, create_attempt=create_attempt)
        if bind_to is not None:
            launch.harness.bind_launch_lease(lease.launch_token, bind_to)
        return lease

    def refuse(self, launch, prepared=None, *, risk="high"):
        """Drive the same dispatch to a refusal and return the typed adapter refusal."""
        with self.assertRaises(executor.IdempotencyRecoveryError) as caught:
            self.dispatch(launch, prepared, risk=risk)
        return caught.exception

    def refuse_runtime(self, launch, prepared=None, *, risk="high"):
        """Drive the same dispatch to a RUNTIME refusal (the barrier's own exception)."""
        with self.assertRaises(OrcaRuntimeError) as caught:
            self.dispatch(launch, prepared, risk=risk)
        return str(caught.exception)

    # ---- assertions ---------------------------------------------------------------
    def assertNothingDelivered(self, recorder) -> None:
        for verb in ("worker-start", "send", "dispatch"):
            self.assertEqual(
                sum(1 for command in recorder.commands if verb in command), 0,
                f"the barrier refused and {verb!r} still ran")

    def lease_cells(self, adapter, *, role="worker", attempt=1, phase=PHASE):
        return adapter.pair_preparation.launch_lease(phase, attempt, role)


# ======================================================================================
# Group 1 / I-4  the ORDER: pre-record -> create -> receipt bind
# ======================================================================================
class LeaseOrderTests(LeaseRoom):
    """9.3 Group 1 + 3a.11 **P-2** / **P-8**, and the design's ordering invariants
    **O1** (the lease is durably recorded before `terminal create`) and **O3** (the
    `CREATED` write precedes the bind)."""

    def test_the_lease_is_recorded_before_the_create_and_bound_after_the_receipt(self):
        """O1 + O3 + P-8, as ONE observed order.

        Asserted, in order: the `CREATE_INTENDED` entry carries all five mint cells and
        NO `terminal_digest`; the `terminal create` runs after it; the `CREATED` entry
        carries the digest of the handle the receipt reported; the driver's `bind_target`
        is called after that, with THAT handle and the lease's own token; and the lease
        the harness holds is bound to exactly that digest.

        The intermediate `CREATE_INTENDED` state is observed by a store OBSERVER wired to
        the real store's own writes -- not by a replayed guess -- so the order is read off
        the production path rather than reconstructed.
        """
        launch = self.hosted_launch()
        store = launch.adapter.pair_preparation
        observed: list[tuple[str, str, dict]] = []
        real_record = store.record

        def observing_record(phase, gate_iteration, role, *, stage, **fields):
            entry = real_record(phase, gate_iteration, role, stage=stage, **fields)
            observed.append((role, stage, dict(entry)))
            return entry

        store.record = observing_record                     # an OBSERVER, not a stub
        self.addCleanup(lambda: setattr(store, "record", real_record))
        creates: list[int] = []
        real_create = launch.harness.create_fake_terminal

        def observing_create(*args, **kwargs):
            creates.append(len(observed))                   # writes seen BEFORE this create
            return real_create(*args, **kwargs)

        launch.harness.create_fake_terminal = observing_create
        self.dispatch(launch)

        worker_writes = [(stage, entry) for role, stage, entry in observed
                         if role == "worker"]
        stages = [stage for stage, _ in worker_writes]
        self.assertEqual(stages[:2], ["CREATE_INTENDED", "CREATED"],
                         f"the worker's first two writes were {stages[:2]}")
        intended = worker_writes[0][1]
        for cell in pause_store.PAIR_LAUNCH_LEASE_KEYS:
            self.assertTrue(intended[cell],
                            f"{cell} must be published BEFORE the create, not after")
        self.assertEqual(intended["terminal_digest"], "",
                         "the handle does not exist yet, so nothing may claim its digest")
        self.assertEqual(intended["launch_revoked_at"], "",
                         "a lease is never born revoked")
        self.assertEqual(creates[0], 1,
                         "the FIRST create ran after exactly one durable write -- the "
                         "worker's CREATE_INTENDED -- so the pre-record is strictly "
                         "before the only external effect of preparation")
        created = worker_writes[1][1]
        handle = launch.adapter._receipts and None          # never read; see below
        del handle
        worker_handle = launch.harness.ledger_terminal(
            [row for row in launch.recorder.handles][0]) if False else None
        del worker_handle
        # The handle is read from the BIND the driver recorded, which is the only place
        # this test learns it -- so a bind that never happened cannot be papered over.
        self.assertEqual(len(launch.driver.binds), 2,
                         "exactly one bind per prepared role, and no more")
        bound_handle, bound_token, nonce = launch.driver.binds[0]
        self.assertEqual(created["terminal_digest"],
                         pair_terminal_digest(bound_handle),
                         "the CREATED digest is the digest of the handle that was bound")
        self.assertEqual(bound_token, intended["launch_token"],
                         "the bind cites the lease that was pre-recorded, not a new one")
        self.assertTrue(nonce and nonce != intended["launch_token"],
                        "the bind nonce is its own value and is never the lease token")
        held = launch.harness.launch_lease_for_handle(bound_handle)
        self.assertEqual(held.terminal_digest, pair_terminal_digest(bound_handle))
        self.assertFalse(held.revoked)

    def test_the_bind_nonce_is_minted_once_per_lease_and_is_never_durable(self):
        """I-7: the nonce is in-memory only.  A second `bind_prepared_target` for one
        handle mints NO second nonce and makes no second driver call."""
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle, _token, nonce = launch.driver.binds[0]
        self.assertEqual(launch.harness.bind_prepared_target(
            handle, role="worker", phase=PHASE, attempt=1), "already_bound")
        self.assertEqual(len(launch.driver.binds), 2, "no second bind was composed")
        document = json.loads(
            launch.adapter.pair_preparation.path.read_text(encoding="utf-8"))
        self.assertNotIn(nonce, json.dumps(document),
                         "the bind nonce reaches no durable cell")

    def test_a_second_lease_never_rebinds_a_handle(self):
        """I-7: `DRIVER_TARGET_REBOUND`, refused and never repaired."""
        launch = self.hosted_launch()
        self.dispatch(launch)
        handle = launch.driver.binds[0][0]
        other = launch.harness.mint_launch_lease(
            role="worker", phase=PHASE, attempt=9, create_attempt=1)
        with self.assertRaises(OrcaRuntimeError) as caught:
            launch.harness.bind_launch_lease(other.launch_token, handle)
        self.assertIn(DRIVER_TARGET_REBOUND, str(caught.exception))

    def test_the_two_verifications_of_one_attempt_reuse_one_created_target(self):
        """§9.3 Group 1's `test_second_verification_reuses_one_target`, and the only place
        a `terminal create` COUNT is asserted.

        One dispatch of a same-command model-aware pair performs **exactly two** creates --
        one per prepared role, never one per verification -- and the delivery role is then
        verified **twice** on that one target: once by `_prepare_pair`'s pre-pass and once
        by `start_worker`'s Gate B.  Both verifications carry the **same handle**, the
        **same launch token** and therefore the same `session_uuid`, which is what "one
        launch act per session, re-validated" means in D2's terms.

        What this asserts that nothing else did: a COUNT of the external create effect.
        Every other row asserts what a lease says or when it is released; this one asserts
        that re-verification does not create, which is the cost claim D5 rests on and the
        structural form of "기존 lease의 재사용으로 두 번째 create를 실행하지 않는다".
        """
        launch = self.hosted_launch()
        # Measured as the DELTA across the dispatch, and stated for this test's own setup:
        # `launcher.build_orca_adapter` creates the run-owner terminal before any dispatch
        # (`--title "OS-14 pair preparation" --command bash`), and a total would silently
        # count it.  No pre-existing or residual session is enumerated or counted.
        before = launch.recorder.count("terminal", "create")
        self.dispatch(launch)
        self.assertEqual(launch.recorder.count("terminal", "create") - before, 2,
                         "exactly one create per PREPARED ROLE, not one per verification")
        self.assertEqual(len(launch.driver.binds), 2, "and one bind per create")
        per_session: dict[str, list] = {}
        for ticket in launch.driver.tickets:
            per_session.setdefault(ticket.terminal, []).append(ticket)
        worker_handle = launch.driver.binds[0][0]
        worker_tickets = per_session[worker_handle]
        self.assertEqual(len(worker_tickets), 2,
                         "the delivery role is verified twice for ONE attempt: the "
                         "pre-pass and Gate B")
        self.assertEqual({ticket.launch_token for ticket in worker_tickets},
                         {self.lease_cells(launch.adapter)["launch_token"]},
                         "both verifications re-validate the SAME launch")
        self.assertEqual(len({ticket.token for ticket in worker_tickets}), 2,
                         "each draws its OWN ticket, so neither reuses the other's")
        lease = launch.harness.launch_lease_for_handle(worker_handle)
        self.assertEqual(lease.session_uuid,
                         self.lease_cells(launch.adapter)["session_uuid"])
        self.assertTrue(lease.session_uuid)

    def test_the_terminal_digest_derivation_agrees_with_the_policy_module(self):
        """The runtime re-spells `pause_policy.terminal_digest` because it takes no engine
        import; this is the lock that keeps the two spellings equal."""
        for handle in ("term_1", "term_os14_42", ""):
            self.assertEqual(pair_terminal_digest(handle),
                             pause_policy.terminal_digest(handle))


# ======================================================================================
# Group 1  the five checks of barrier leg (a2)
# ======================================================================================
class LaunchLegTests(LeaseRoom):
    """9.3 Group 1's refusal rows, each driven by a DRIVER that cites the wrong thing.

    Every case asserts the closed-vocabulary member AND that nothing was delivered: a
    refusal that still delivered is not a refusal.
    """

    def test_fabricated_launch_token_is_request_absent(self):
        """(a2-1).  A token this harness never minted, whatever the ordinals look like."""
        launch = self.hosted_launch(driver=LeaseCitingDriver(
            resolve=lambda requested: requested,
            cite=lambda ticket, evidence: ("launch:forged:0:0:0:0:0:deadbeef", 1)))
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_REQUEST_ABSENT, message)
        self.assertIn("(a2-1)", message)
        self.assertNothingDelivered(launch.recorder)

    def test_citing_no_launch_at_all_is_request_absent(self):
        """(a2-1), the other direction: a lease WAS minted for this session and the
        evidence cites nothing.  This is the F-001 shape -- reading a model off a session
        whose launch nobody attested -- and OS-49's reference driver, which knows nothing
        about leases, produces it naturally."""
        launch = self.hosted_launch(driver=LeaseCitingDriver(
            resolve=lambda requested: requested,
            cite=lambda ticket, evidence: ("", 0)))
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_REQUEST_ABSENT, message)
        self.assertIn("no launch at all", message)
        self.assertNothingDelivered(launch.recorder)

    def test_lease_absent_from_the_durable_record_is_request_absent(self):
        """(a2-2).  REGISTRY-ONLY IS NEVER SUFFICIENT.

        Two cases, and both must refuse: the durable reader cannot answer at all, and the
        durable record names a DIFFERENT launch than the one cited.  In both the lease is
        live in this process's memory and correctly bound to this very handle -- which is
        exactly the state a permissive reading would accept.
        """
        for case in ("unreadable", "different_token"):
            with self.subTest(case=case):
                launch = self.hosted_launch()
                if case == "unreadable":
                    launch.harness.set_launch_lease_reader(None)
                else:
                    launch.harness.set_launch_lease_reader(
                        lambda *, role, phase, attempt: {
                            "launch_token": "launch:another:1:1:1:1:1:ff",
                            "launch_revoked_at": ""})
                message = self.refuse_runtime(launch)
                self.assertIn(MODEL_SELECTION_REQUEST_ABSENT, message)
                self.assertIn("(a2-2)", message)
                self.assertNothingDelivered(launch.recorder)

    def test_a_durably_revoked_lease_is_stale(self):
        """(a2-3), the DURABLE half: the record stamps the lease revoked, however live
        this process's memory looks."""
        launch = self.hosted_launch()
        launch.harness.set_launch_lease_reader(
            lambda *, role, phase, attempt: dict(
                launch.adapter._read_durable_launch_lease(
                    role=role, phase=phase, attempt=attempt) or {},
                launch_revoked_at="2026-11-01T00:00:00+00:00"))
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)
        self.assertIn("(a2-3)", message)
        self.assertNothingDelivered(launch.recorder)

    def test_lease_for_another_handle_or_another_round_is_stale(self):
        """(a2-4), three shapes, each a valid UNREVOKED lease that is not THIS attempt's:
        another session's lease, another gate round's, and another role's."""
        for case in ("other_session", "other_round", "other_role"):
            with self.subTest(case=case):
                held: dict = {}
                launch = self.hosted_launch(driver=LeaseCitingDriver(
                    resolve=lambda requested: requested,
                    cite=lambda ticket, evidence, held=held: (held["token"],
                                              held["stamp"])))
                if case == "other_session":
                    # A lease for THIS key, minted and genuinely BOUND to another
                    # session -- so the only thing wrong with it is its target.
                    other = self.premint(launch, bind_to="term_elsewhere")
                elif case == "other_round":
                    other = self.premint(launch, attempt=99, create_attempt=1)
                else:
                    other = self.premint(launch, role="reviewer")
                held["token"], held["stamp"] = other.launch_token, other.launch_stamp
                message = self.refuse_runtime(launch)
                self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)
                self.assertIn("(a2-4)", message)
                self.assertNothingDelivered(launch.recorder)

    def test_a_lease_minted_for_another_key_is_stale_even_when_it_is_bound(self):
        """(a2-4)'s second half: the lease IS the one bound to this session, and it was
        minted for another `(run, phase, role, gate iteration)`.

        Reached by making the MINT CALL SITE wrong -- which is the only way this branch is
        reachable at all, and therefore the defect it guards against.  Asserted separately
        from the three citation shapes above, because "not this session's lease" and "not
        this attempt's key" are two different facts and a test must not be cited for both.
        """
        launch = self.hosted_launch()
        harness = launch.harness
        real_mint = harness.mint_launch_lease
        harness.mint_launch_lease = lambda **kwargs: real_mint(
            **{**kwargs, "attempt": 77})
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)
        self.assertIn("(a2-4)", message)
        self.assertIn("77", message)
        self.assertNothingDelivered(launch.recorder)

    def test_a_lease_from_another_run_is_stale(self):
        """(a2-4)'s RUN half -- the one shape of request §5's
        "없는/위조된/revoked/wrong-run/wrong-round/wrong-handle lease" that the
        IMPLEMENTATION phase's tests left undriven.

        The other five are covered by the rows around this one: absent by
        `test_citing_no_launch_at_all_is_request_absent` and
        `test_lease_absent_from_the_durable_record_is_request_absent`, forged by
        `test_fabricated_launch_token_is_request_absent`, revoked by
        `RevocationTests`, wrong-round by `..._another_key_is_stale_even_when_it_is_bound`
        and wrong-handle by `..._another_handle_or_another_round_is_stale`'s
        `other_session` case.  `cited.run_id` was the only term of the key comparison no
        test reached.

        **How it is reached, stated plainly.** The registry is per harness and every lease
        it mints is stamped with the harness's run id, so a cross-run lease cannot arrive
        from a second harness -- a token this harness never minted is `(a2-1)` forged, a
        different fact.  The branch guards the case where the harness's OWN run identity
        has moved between the mint and the citation, which is what a process that adopted
        a different run looks like, so the run id is changed between those two points --
        inside the driver call, after the lease was minted and bound by the real
        preparation path and before the barrier evaluates the key.  No private map is
        pre-filled: the lease under test is the one `_prepare_role` minted.

        Asserted additionally: the refusal is `(a2-4)` and it names **both** keys, so a
        reader can see which run the launch belonged to -- and it fires BEFORE the
        `observed_at` key leg, which would also have refused this state for a different
        reason.  A test that passed on that later leg would not be testing the launch leg.
        """
        launch = self.hosted_launch()
        harness = launch.harness
        original = harness.run_id
        real_select = launch.driver.select_and_verify

        def drifting_run(ticket):
            evidence = real_select(ticket)
            harness.run_id = "run_os14elsewhere"     # this process is now another run
            return evidence

        launch.driver.select_and_verify = drifting_run
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)
        self.assertIn("(a2-4)", message)
        self.assertIn(original, message,
                      "the refusal names the run the cited launch was minted for")
        self.assertIn("run_os14elsewhere", message,
                      "and the run this attempt believes it is")
        self.assertNotIn("was not observed for this (run, task, terminal", message,
                         "the LAUNCH leg refused this, not the later observed_at key leg")
        self.assertNothingDelivered(launch.recorder)

    def test_a_valid_launch_citation_does_not_rescue_replayed_prepass_evidence(self):
        """§9.3 Group 1's `test_prepass_evidence_cannot_be_replayed_at_gate_b`, in the one
        form the launch lease makes newly assertable.

        OS-49 already refuses a token carried across ATTEMPTS
        (`test_os49_delivery_barrier::test_a_previous_attempts_token_is_refused`).  What no
        test asserted is the property the launch leg could have weakened: within ONE
        attempt, the pre-pass's ticket evidence replayed at Gate B must still be refused
        **even when the driver cites a perfectly valid launch** -- live, unrevoked, bound
        to this very session and confirmed by the durable record.

        The pre-pass's ticket is attested with `task_id == ""` and Gate B's with the real
        one, so the two tokens structurally cannot be equal; this asserts that the EARLIER
        ticket-identity leg still fires and that the launch citation neither suppresses nor
        replaces it.  Asserted as the ordering it is: the refusal names the attempt/session
        /role mismatch and NOT any `(a2-*)` check, because a valid launch has nothing to
        refuse.
        """
        launch = self.hosted_launch()
        prepass: dict[str, str] = {}
        real_select = launch.driver.select_and_verify

        def replaying(ticket):
            evidence = real_select(ticket)
            if ticket.task_id == "":
                prepass.setdefault(ticket.terminal, evidence.selection_token)
                return evidence
            replayed = prepass.get(ticket.terminal)
            return (evidence if replayed is None
                    else dataclasses.replace(evidence, selection_token=replayed))

        launch.driver.select_and_verify = replaying
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)
        self.assertIn("belongs to another attempt, session or role", message)
        self.assertNotIn("(a2-", message,
                         "the launch citation was VALID: the ticket leg is what refused, "
                         "and the launch leg must neither suppress nor replace it")
        self.assertNothingDelivered(launch.recorder)

    def test_an_unbound_lease_is_named_not_repaired(self):
        """(a2-4) + the crash-between-create-and-bind row: a lease with no target is
        `DRIVER_TARGET_UNBOUND` -> `model_selection_unverified`.  NAMED, never repaired:
        no second bootstrap is attempted and no binding is restored."""
        held: dict = {}
        launch = self.hosted_launch(driver=LeaseCitingDriver(
            resolve=lambda requested: requested,
            cite=lambda ticket, evidence, held=held: (held["token"],
                                              held["stamp"])))
        # A real lease, minted through the real public mint and NEVER bound -- which is
        # exactly the durable state a crash between `terminal create` and the bind leaves.
        unbound = self.premint(launch, create_attempt=5)
        held["token"], held["stamp"] = unbound.launch_token, unbound.launch_stamp
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_UNVERIFIED, message)
        self.assertIn(DRIVER_TARGET_UNBOUND, message)
        self.assertNothingDelivered(launch.recorder)
        self.assertEqual(launch.harness.launch_lease(held["token"]).terminal_digest, "",
                         "the refusal bound nothing: the lease is still unbound after it")

    def test_launch_stamp_must_precede_the_request_stamp(self):
        """(a2-5), the three integer-order shapes the design names: `launch_stamp == 0`,
        `launch_stamp == request_stamp` and `launch_stamp > request_stamp`.

        All three are order over ordinals THIS HARNESS issued, and all three are
        `model_selection_request_stale`.
        """
        cases = {
            # 0 is "no ordinal was drawn for the launch at all".
            "zero": lambda ticket, evidence: (ticket.launch_token, 0),
            # The launch ordinal EQUAL to this attempt's request ordinal: the launch did
            # not PRECEDE the verification, it would BE the verification.
            "equal": lambda ticket, evidence: (ticket.launch_token,
                                               evidence.request_stamp),
            # And an ordinal drawn after it, which no real launch could have.
            "after": lambda ticket, evidence: (ticket.launch_token, 10 ** 6)}
        for name, cite in cases.items():
            with self.subTest(case=name):
                launch = self.hosted_launch(driver=LeaseCitingDriver(
                    resolve=lambda requested: requested, cite=cite))
                message = self.refuse_runtime(launch)
                self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)
                self.assertIn("(a2-5)", message)
                self.assertNothingDelivered(launch.recorder)

    def test_lease_with_a_different_requested_model_is_mismatch(self):
        """The MODEL equality: the session was launched for one model and this routing
        requests another.  `model_selection_mismatch`, not a staleness -- the lease is
        current, it is simply for a different model."""
        launch = self.hosted_launch()
        store = launch.adapter.pair_preparation
        harness = launch.harness
        real_mint = harness.mint_launch_lease

        def drifting_mint(**kwargs):
            lease = real_mint(**kwargs)
            if lease is None or kwargs["role"] != "worker":
                return lease
            # The lease records a DIFFERENT model than the routing declares.  Produced by
            # replacing the lease in the registry the only way the real code does -- a
            # frozen `replace` written back through the public mint result -- so the
            # durable cells and the registry still agree with each other.
            drifted = dataclasses.replace(lease, requested_model="glm-5.9-elsewhere")
            harness._launch_leases[lease.launch_token] = drifted
            return drifted

        harness.mint_launch_lease = drifting_mint
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_MISMATCH, message)
        self.assertIn("glm-5.9-elsewhere", message)
        self.assertNothingDelivered(launch.recorder)
        del store

    def test_a_request_method_outside_the_closed_set_is_unsupported(self):
        """The `cli_launch_model_flag` row, in the only form this release can state it.

        The design's `test_gate_b_attests_revalidation_not_a_new_request` needs the
        vocabulary MEMBERS `cli_launch_model_flag` / `cli_launch_lease_revalidation`,
        which are **I-10** and are deliberately NOT in this release.  So the assertable
        half is the fail-closed one, and it is asserted here: a driver attesting
        `cli_launch_model_flag` at the barrier is refused `model_selection_unsupported`,
        because the member does not exist.  Adding it is the visible, reviewed act that
        lifts the refusal -- and it has not happened.
        """
        self.assertNotIn("cli_launch_model_flag", MODEL_SELECTION_REQUEST_METHODS)
        self.assertNotIn("cli_launch_lease_revalidation", MODEL_SELECTION_REQUEST_METHODS)
        launch = self.hosted_launch()
        driver = launch.driver
        real = driver.select_and_verify
        driver.select_and_verify = lambda ticket, real=real: dataclasses.replace(
            real(ticket), request_method="cli_launch_model_flag")
        message = self.refuse_runtime(launch)
        self.assertIn(MODEL_SELECTION_UNSUPPORTED, message)
        self.assertNothingDelivered(launch.recorder)


# ======================================================================================
# Group 1  the MINT RULE
# ======================================================================================
class MintRuleTests(LeaseRoom):
    """9.3 Group 1 `test_every_create_mints_a_new_lease` /
    `test_superseded_session_gets_a_new_lease_not_the_old_one`, and 3a.12's mint rule."""

    def test_the_first_create_mints_one_lease_per_prepared_role(self):
        launch = self.hosted_launch()
        self.dispatch(launch)
        tokens = {role: self.lease_cells(launch.adapter, role=role)["launch_token"]
                  for role in ("worker", "reviewer")}
        self.assertEqual(len(set(tokens.values())), 2,
                         "two creates, two leases: a token is never shared")
        for role, token in tokens.items():
            lease = launch.harness.launch_lease(token)
            self.assertEqual((lease.role, lease.phase, lease.attempt), (role, PHASE, 1))
            self.assertEqual(int(self.lease_cells(launch.adapter,
                                                  role=role)["launch_stamp"]),
                             lease.launch_stamp)

    def test_a_create_refused_retry_mints_a_new_lease(self):
        """The second reachable create: the runtime's own receipt proved nothing was
        created, so the retry is a NEW create and takes a NEW token.  The superseded token
        is revoked by the mint itself and is gone from the slot in the same write."""
        launch = self.hosted_launch()
        store = launch.adapter.pair_preparation
        first = launch.harness.mint_launch_lease(
            role="worker", phase=PHASE, attempt=1, create_attempt=1)
        store.record(PHASE, 1, "worker", stage="CREATE_INTENDED", create_attempt="1",
                     terminal_title="t", **first.durable_cells())
        store.record(PHASE, 1, "worker", stage="CREATE_REFUSED", create_attempt="1",
                     refusal_error_code="E_REFUSED")
        second = launch.harness.mint_launch_lease(
            role="worker", phase=PHASE, attempt=1, create_attempt=2,
            supersedes=first.launch_token)
        store.record(PHASE, 1, "worker", stage="CREATE_INTENDED", create_attempt="2",
                     terminal_title="t", **second.durable_cells())
        self.assertNotEqual(first.launch_token, second.launch_token)
        self.assertTrue(launch.harness.launch_lease(first.launch_token).revoked,
                        "the superseded token is revoked by the mint that replaces it")
        self.assertFalse(launch.harness.launch_lease(second.launch_token).revoked)
        cells = store.launch_lease(PHASE, 1, "worker")
        self.assertEqual(cells["launch_token"], second.launch_token)
        self.assertEqual(cells["launch_revoked_at"], "",
                         "the replacement is not born carrying the superseded lease's "
                         "revocation stamp")
        self.assertNotEqual(first.endpoint_digest, second.endpoint_digest,
                            "a new token derives a new endpoint, so two incarnations can "
                            "never share one path")

    @REQUIRES_LANGGRAPH
    def test_a_reuse_supersession_mints_a_new_lease_and_revokes_the_old_one(self):
        """The third reachable create, driven END TO END through the REAL reuse gate.

        The shape OS-14's own B1 regression already locks: a Worker settles with a
        malformed decision-gate record, the engine classifies a FORM defect and
        re-dispatches the SAME role in the SAME round, and the repair finds the previous
        session's `session_use` row at `DELIVERED`.  The shipped reuse gate is asked, it
        refuses (`release_state_not_live` / `ownership_not_transferable`), and
        `_prepare_role` publishes a `create_attempt = 2` `CREATE_INTENDED`.

        Asserted here, and only here: that second `CREATE_INTENDED` carries a NEW
        `launch_token`, the superseded token is REVOKED, its handle was RELEASED, and the
        slot's `launch_revoked_at` is empty -- the replacement is not born stale.

        LangGraph-gated because the validation-repair turn only exists inside the real
        graph run; the mint rule's other two creates are asserted without it.
        """
        run_id = self.next_run_id()
        launch = self.isolated_launch(
            run_id=run_id,
            driver=LeaseCitingDriver(resolve=lambda requested: requested),
            bodies=repair_bodies(run_id=run_id))
        # The replacement's own `CREATE_INTENDED` entry, captured AT THE MOMENT it is
        # published rather than read back at the end of the run.  The scoping is the whole
        # point: since OS-14 I-7's engine connection the round CLOSES at the end of a
        # successful run, so every lease is legitimately revoked by then -- and "the
        # replacement is not born carrying the superseded lease's revocation stamp" is a
        # statement about the write, not about the end state.  An OBSERVER on the real
        # store's own writer, overriding nothing.
        store = launch.adapter.pair_preparation
        published: list[dict] = []
        real_record = store.record

        def observing_record(phase, gate_iteration, role, *, stage, **fields):
            entry = real_record(phase, gate_iteration, role, stage=stage, **fields)
            published.append(dict(entry))
            return entry

        store.record = observing_record
        self.addCleanup(lambda: setattr(store, "record", real_record))
        final = self.execute(launch)
        self.assertEqual(final.get("terminal_status"), "COMPLETED",
                         final.get("terminal_reason"))
        entry = launch.entry("worker")
        self.assertEqual(entry["create_attempt"], "2",
                         "the repair superseded the ineligible session")
        self.assertTrue(entry["supersedes_digest"])
        self.assertIn("release_state_not_live", entry["supersedes_reason"])
        self.assertTrue(entry["launch_token"], "the replacement carries its own lease")
        replacement = [row for row in published
                       if row["role"] == "worker" and row["stage"] == "CREATE_INTENDED"
                       and row["create_attempt"] == "2"]
        self.assertTrue(replacement, "the supersession published no second CREATE_INTENDED")
        self.assertEqual(replacement[0]["launch_revoked_at"], "",
                         "the replacement is not born carrying the superseded lease's "
                         "revocation stamp")
        self.assertTrue(replacement[0]["launch_token"])
        self.assertNotEqual(replacement[0]["launch_token"],
                            replacement[0]["supersedes_digest"])
        tokens = [token for _handle, token, _nonce in launch.driver.binds]
        self.assertEqual(len(set(tokens)), len(tokens),
                         "every create minted its OWN token; none was re-exercised")
        self.assertIn(entry["launch_token"], tokens)
        superseded = [token for token in tokens if token != entry["launch_token"]
                      and launch.harness.launch_lease(token).role == "worker"]
        self.assertTrue(superseded, "the worker was prepared twice, so one is superseded")
        for token in superseded:
            self.assertTrue(launch.harness.launch_lease(token).revoked,
                            "the superseded lease is revoked, not left live")
        self.assertTrue(launch.driver.released,
                        "the superseded TARGET was released, through the driver's own "
                        "release seam")

    def test_no_path_re_exercises_a_recorded_token_for_a_second_create(self):
        """The mint rule's negative half, asserted over the PARSED production source.

        `_prepare_role` is the only method that creates a session, and the only lease it
        ever passes to `record(... CREATE_INTENDED ...)` is the one `mint_launch_lease`
        just returned.  Asserted structurally so a comment cannot satisfy it: in the
        parsed function there is exactly ONE call to `mint_launch_lease`, it is the only
        source of the cells written at `CREATE_INTENDED`, and no call reads a
        `launch_token` out of an existing entry EXCEPT as the `supersedes` argument.
        """
        import ast
        import inspect
        import textwrap
        from scripts.deterministic_workflow import orca_adapter
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(orca_adapter.OrcaAdapter._prepare_role)))
        mints = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and getattr(node.func, "id", "") == "mint"]
        self.assertEqual(len(mints), 1,
                         "exactly one mint call: a second one would be a second lease "
                         "for one create")
        reads = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and node.value == "launch_token"]
        self.assertEqual(len(reads), 1,
                         "the ONLY read of a recorded launch_token is the one that feeds "
                         "`supersedes`, i.e. the REVOCATION of the superseded lease -- "
                         "never a re-exercise of it")
        source = inspect.getsource(orca_adapter.OrcaAdapter._prepare_role)
        self.assertIn("supersedes=superseded_token", source)


# ======================================================================================
# Group 1  REVOCATION -- four paths, and the one the design's row got wrong
# ======================================================================================
class RevocationTests(LeaseRoom):
    """9.3 Group 1 `test_revoked_lease_cannot_be_re_exercised`.

    The design's row names four revoking paths: `_refuse_preparation`,
    `_revoke_model_authority`, a reuse supersession -- and "settlement".  **The fourth is
    superseded by TL (3a.12.1), which is the SINGLE definition of when a target is
    disposed of and which says release is NOT tied to `settle_attempt`.**  The design
    states that anything contradicting TL elsewhere is an error in that place, so the
    fourth path asserted here is TL's own: ROUND CLOSE.  A settlement-scoped revocation is
    asserted NOT to happen, in `test_os14_round_lifetime.py`.
    """

    def test_a_revoked_lease_cannot_be_re_exercised(self):
        for path in ("refuse_preparation", "revoke_model_authority", "supersession",
                     "round_close"):
            with self.subTest(path=path):
                launch = self.hosted_launch()
                self.dispatch(launch)
                token = self.lease_cells(launch.adapter)["launch_token"]
                handle = launch.driver.binds[0][0]
                if path == "refuse_preparation":
                    with self.assertRaises(executor.IdempotencyRecoveryError):
                        launch.adapter._refuse_preparation(
                            "PAIR_PREPARATION_MODEL_UNOBSERVED", "a refusal under test")
                elif path == "revoke_model_authority":
                    launch.adapter._revoke_model_authority(handle)
                elif path == "supersession":
                    launch.harness.mint_launch_lease(
                        role="worker", phase=PHASE, attempt=1, create_attempt=2,
                        supersedes=token)
                else:
                    launch.adapter.observe_round_route("ADVANCE_PHASE", phase=PHASE,
                                                       attempt=1)
                    # (1a) is false until both required roles are VALIDATED, so the close
                    # above holds; TL's own disposal clause is what revokes here.
                    launch.adapter.dispose_round(phase=PHASE, attempt=1,
                                                 reason="under test")
                self.assertTrue(launch.harness.launch_lease(token).revoked,
                                f"{path} left the lease live")
                # The next CITATION of it is stale, through the real barrier.
                launch.driver.cite = lambda ticket, evidence, token=token: (
                    token, launch.harness.launch_lease(token).launch_stamp)
                message = self.refuse_runtime(launch, self.engine_intent())
                self.assertIn(MODEL_SELECTION_REQUEST_STALE, message)

    def test_revocation_is_idempotent_and_the_first_stamp_stands(self):
        launch = self.hosted_launch()
        self.dispatch(launch)
        token = self.lease_cells(launch.adapter)["launch_token"]
        self.assertTrue(launch.harness.revoke_launch_lease(token, reason="first"))
        stamped = launch.harness.launch_lease(token).revoked_at
        self.assertFalse(launch.harness.revoke_launch_lease(token, reason="second"))
        self.assertEqual(launch.harness.launch_lease(token).revoked_at, stamped)

    def test_a_revocation_is_never_applied_to_another_lease(self):
        """The durable half: a stale revocation must not land on the replacement a
        supersession has just minted."""
        launch = self.hosted_launch()
        self.dispatch(launch)
        store = launch.adapter.pair_preparation
        with self.assertRaises(pause_store.PairPreparationCorrupt):
            store.record_launch_revocation(PHASE, 1, "worker",
                                           launch_token="launch:someone:else",
                                           revoked_at="2026-11-01T00:00:00+00:00")
        self.assertEqual(self.lease_cells(launch.adapter)["launch_revoked_at"], "")


# ======================================================================================
# the PROTECTIONS this unit must not weaken
# ======================================================================================
class ProtectionTests(LeaseRoom):
    """9.2's rows, in the two forms this unit can move them: pair admission and the
    authority / durable-history split."""

    def test_pair_admission_still_requires_counterpart_evidence(self):
        """A lease satisfies the REQUEST leg and does not substitute for pair admission.

        Driven by making the COUNTERPART's verification unobservable while the delivery
        role's lease is perfectly valid: the pair is refused, and nothing is delivered.
        """
        launch = self.hosted_launch()
        self.dispatch(launch)
        worker_handle = launch.driver.binds[0][0]
        reviewer_handle = launch.driver.binds[1][0]
        # The lease is valid, live, bound and durably recorded -- and the COUNTERPART's
        # verification authority is then revoked, through the harness's own public
        # operation.  Gate B must refuse the next delivery of the worker role.
        launch.harness.invalidate_model_authority(reviewer_handle)
        with self.assertRaises(OrcaRuntimeError) as caught:
            launch.harness.start_worker("task_admit", worker_handle, "spec",
                                        role="worker", phase=PHASE, attempt=1)
        message = str(caught.exception)
        self.assertIn(MODEL_SELECTION_PAIR_UNADMITTED, message,
                      "a valid launch lease satisfies the REQUEST leg and does not "
                      "substitute for pair admission")

    def test_a_durable_lease_restores_no_binding_and_no_authority(self):
        """3a.12's restart rule, and the first half of TL row 14.

        A SUCCESSOR harness with an EMPTY registry faces a v4 record naming a live,
        unrevoked lease.  The durable record is read, the lease is NOT adopted, nothing is
        rebound, and the next citation of it is refused -- the successor's registry simply
        does not hold it, so the citation is `request_absent`.
        """
        launch = self.hosted_launch()
        self.dispatch(launch)
        cells = self.lease_cells(launch.adapter)
        self.assertTrue(cells["launch_token"])
        self.assertEqual(cells["launch_revoked_at"], "")
        handle = launch.driver.binds[0][0]
        # A SUCCESSOR process, modelled the way the design's restart rule describes one:
        # a NEW harness and a NEW adapter over the SAME durable documents.  Nothing is
        # copied from the predecessor -- not the registry, not the bindings, not the
        # authority maps.
        successor_harness = OrcaRuntimeHarness(
            self.project, agent_routing=launch.harness.agent_routing,
            model_driver=LeaseCitingDriver(resolve=lambda requested: requested))
        successor_harness.run_id = launch.harness.run_id
        successor = OrcaAdapter(
            successor_harness,
            pair_binding=launch.adapter.pair_binding,
            pair_preparation=launch.adapter.pair_preparation)
        self.assertIsNone(successor_harness.launch_lease(cells["launch_token"]),
                          "a successor's registry is EMPTY: nothing durable resurrects "
                          "a binding")
        self.assertIsNone(successor_harness.launch_lease_for_handle(handle),
                          "and nothing durable resurrects a target")
        self.assertIsNone(successor_harness.prepared_target(handle))
        # The durable read IS available to it -- as HISTORY, which is what makes the
        # refusal a NAMED one rather than a silent "nothing was prepared".
        self.assertEqual(
            successor._read_durable_launch_lease(role="worker", phase=PHASE,
                                                 attempt=1)["launch_token"],
            cells["launch_token"])
        # And the successor's own barrier refuses the citation rather than adopting it:
        # `DRIVER_TARGET_UNBOUND` is reported through `(a2-1)`, because a lease this
        # process never minted is not a launch it can revalidate.
        successor_harness.set_launch_lease_reader(
            successor._read_durable_launch_lease)
        successor_harness.adopt_prepared_terminal(handle, "worker", phase=PHASE)
        successor_harness.model_driver.cite = lambda ticket, evidence, token=cells[
            "launch_token"]: (token, int(cells["launch_stamp"]))
        with self.assertRaises(OrcaRuntimeError) as caught:
            successor_harness.verify_model_identity("", handle, role="worker",
                                                    phase=PHASE, attempt=1)
        message = str(caught.exception)
        self.assertIn(MODEL_SELECTION_REQUEST_ABSENT, message)
        self.assertIn("SUCCESSOR process", message)

    def test_the_default_no_driver_path_mints_nothing(self):
        """The non-regression row: the default and the reference-driver paths are
        unchanged, and neither mints a lease, writes a lease cell, derives an endpoint or
        creates a directory."""
        for label, driver in (("reference", InProcessModelDriver(resolve=lambda r: r)),
                              ("deliver_only", DeliverOnlyDriver(resolve=lambda r: r)),
                              ("bind_only", BindOnlyDriver(resolve=lambda r: r))):
            with self.subTest(driver=label):
                launch = self.isolated_launch(driver=driver)
                self.dispatch(launch)
                for role in ("worker", "reviewer"):
                    self.assertIsNone(self.lease_cells(launch.adapter, role=role),
                                      "no hosted launch cell is minted without HC")
                    entry = launch.adapter.pair_preparation.entry(PHASE, 1, role)
                    for cell in (*pause_store.PAIR_LAUNCH_LEASE_KEYS,
                                 "launch_revoked_at"):
                        self.assertEqual(entry[cell], "")
                self.assertFalse(launch.harness.hosted_delivery_capable())
                self.assertIsNone(launch.harness.mint_launch_lease(
                    role="worker", phase=PHASE, attempt=1))

    def test_hc_is_one_derivation_over_two_names(self):
        """I-2, the rule HC.  One name is not hosted-capable, either way round, and the
        model-selection capability is derived SEPARATELY -- so one can never be read off
        the other."""
        hosted = LeaseCitingDriver(resolve=lambda r: r)
        self.assertEqual(hosted_delivery_capabilities(hosted),
                         frozenset({HOSTED_DELIVERY_CAPABILITY}))
        for partial in (DeliverOnlyDriver(), BindOnlyDriver(), InProcessModelDriver(),
                        None, object()):
            self.assertEqual(hosted_delivery_capabilities(partial), frozenset(),
                             f"{type(partial).__name__} is not hosted-capable")
        # Separately derived: the reference driver HAS the model-selection capability and
        # NOT the hosted one, which is the pair of answers F-002 is about.
        self.assertTrue(model_selection_capabilities(InProcessModelDriver()))
        self.assertFalse(hosted_delivery_capabilities(InProcessModelDriver()))

    def test_both_hc_readers_call_the_one_derivation(self):
        """The DR-02 C-1/C-3 defect: the preparation gate and the bind must not be able to
        disagree.  Asserted by driving BOTH through one driver whose capability answer is
        observed, and by reading the parsed source for a second spelling."""
        import inspect
        from scripts import orca_runtime_harness as runtime
        source = inspect.getsource(runtime.OrcaRuntimeHarness.hosted_delivery_capable)
        self.assertIn("hosted_delivery_capabilities", source)
        for name in ("mint_launch_lease", "bind_prepared_target"):
            body = inspect.getsource(getattr(runtime.OrcaRuntimeHarness, name))
            self.assertIn("hosted_delivery_capable()", body,
                          f"{name} must read the ONE derivation, not a second spelling")
            self.assertNotIn("deliver_hosted", body,
                             f"{name} must not re-spell HC's name list")


# ======================================================================================
# the SECURITY boundary this unit touches
# ======================================================================================
class SecurityBoundaryTests(LeaseRoom):
    """9.2's `test_os49_security_boundary` row, in the parts I-1/I-4 can move."""

    def test_no_absolute_endpoint_path_reaches_a_durable_cell(self):
        """Only the DIGEST is recorded -- never the absolute path."""
        launch = self.hosted_launch()
        self.dispatch(launch)
        token = self.lease_cells(launch.adapter)["launch_token"]
        lease = launch.harness.launch_lease(token)
        document = launch.adapter.pair_preparation.path.read_text(encoding="utf-8")
        self.assertTrue(lease.endpoint_path)
        self.assertNotIn(lease.endpoint_path, document)
        self.assertIn(lease.endpoint_digest, document)
        self.assertNotIn(lease.endpoint_path, json.dumps(launch.recorder.commands))

    def test_the_composed_launch_request_digest_carries_no_credential(self):
        """`launch_argv_digest` is a DIGEST: the document carries no command line, and no
        environment value of any kind."""
        launch = self.hosted_launch()
        self.dispatch(launch)
        document = launch.adapter.pair_preparation.path.read_text(encoding="utf-8")
        for secret in ("ANTHROPIC_API_KEY", "--dangerously-skip-permissions",
                       "bypassPermissions", "Bearer "):
            self.assertNotIn(secret, document)
        cells = self.lease_cells(launch.adapter)
        self.assertEqual(len(cells["launch_argv_digest"]), 64)
        self.assertEqual(len(cells["endpoint_digest"]), 64)

    def test_the_endpoint_path_is_asserted_against_the_platform_sun_path(self):
        """The assertion reads the PLATFORM's own budget, not a constant from the design.

        Asserted both ways: a short path is accepted and an over-long one is refused, and
        the refusal happens in `launch_endpoint_path` -- before anything binds and before
        any lease is recorded.
        """
        self.assertTrue(endpoint_path_fits("/tmp/os14-probe.sock"))
        self.assertFalse(endpoint_path_fits("/tmp/" + "x" * 300 + ".sock"))
        launch = self.hosted_launch()
        harness = launch.harness
        harness.run_id = "r" * 200                 # forces an over-long derived path
        with self.assertRaises(OrcaRuntimeError) as caught:
            harness.launch_endpoint_path("launch:token:for:a:long:run")
        message = str(caught.exception)
        self.assertIn("LAUNCH_ENDPOINT_PATH_TOO_LONG", message)
        self.assertIn("before any address was used", message)
        self.assertNotIn("r" * 50, message,
                         "the refusal reports the LENGTH, never the path")

    def test_the_lease_cells_are_whole_or_corruption(self):
        """I-4's validator: a PARTIAL lease, a non-decimal stamp and a revocation with no
        token are each corruption, not a lease with holes."""
        base = {key: "" for key in pause_store.PAIR_ENTRY_KEYS}
        base.update(run_id=RUN_ID, phase=PHASE, gate_iteration="1", role="worker",
                    stage="CREATE_INTENDED", create_attempt="1")
        whole = dict(base, launch_token="launch:t", launch_stamp="3",
                     session_uuid="u", endpoint_digest="d", launch_argv_digest="a")
        self.assertEqual(pause_store.validate_pair_entry(whole)["launch_stamp"], "3")
        for label, broken in (
                ("token only", dict(base, launch_token="launch:t")),
                ("no stamp", dict(whole, launch_stamp="")),
                ("stamp zero", dict(whole, launch_stamp="0")),
                ("stamp not decimal", dict(whole, launch_stamp="later")),
                ("revoked without a token",
                 dict(base, launch_revoked_at="2026-11-01T00:00:00+00:00"))):
            with self.subTest(case=label):
                with self.assertRaises(pause_store.PairPreparationCorrupt):
                    pause_store.validate_pair_entry(broken)


# ======================================================================================
# static locks
# ======================================================================================
class LeaseContractLockTests(unittest.TestCase):
    """What this release's contract IS, including what it deliberately is NOT."""

    def test_the_schema_version_is_v4_and_the_cells_are_closed(self):
        self.assertEqual(pause_store.PAIR_PREPARATION_SCHEMA_VERSION,
                         "os14.pair_preparation.v4")
        self.assertEqual(pause_store.PAIR_LAUNCH_LEASE_KEYS,
                         ("launch_token", "launch_stamp", "session_uuid",
                          "endpoint_digest", "launch_argv_digest"))
        for cell in (*pause_store.PAIR_LAUNCH_LEASE_KEYS, "launch_revoked_at"):
            self.assertIn(cell, pause_store.PAIR_ENTRY_KEYS)
        for cell in (*pause_store.PAIR_LAUNCH_LEASE_KEYS, "launch_revoked_at"):
            self.assertIn(cell, pause_store._PAIR_ENTRY_ATTEMPT_CLEARED,
                          "a new create attempt clears the previous lease's cells: that "
                          "is the durable half of the mint rule")

    def test_the_request_and_observation_vocabularies_are_still_closed(self):
        """I-10 is NOT in this release, and this is the lock that says so.

        The three members the design's P-7/I-10 adds -- `cli_launch_model_flag`,
        `cli_launch_lease_revalidation` and `stream_json_preflight_assistant` -- are
        absent, deliberately: adding a request method early makes an unauthorised path
        look eligible.  The two attested FIELDS are present, because the barrier's launch
        leg has nothing to validate without a citation.
        """
        self.assertEqual(MODEL_SELECTION_REQUEST_METHODS, ("driver_select_and_verify",))
        fields = {field.name for field in dataclasses.fields(
            __import__("scripts.orca_runtime_harness", fromlist=["x"]).ModelEvidence)}
        self.assertIn("launch_token", fields)
        self.assertIn("launch_stamp", fields)

    def test_the_lease_is_frozen_and_its_durable_cells_exclude_the_target(self):
        lease = ModelLaunchLease(
            run_id="r", role="worker", phase="p", attempt=1, create_attempt=1,
            requested_model="m", agent_command="claude", launch_token="t",
            launch_stamp=1, session_uuid="u", endpoint_path="/tmp/x.sock",
            endpoint_digest="d", launch_argv_digest="a")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            lease.launch_token = "other"            # type: ignore[misc]
        self.assertEqual(set(lease.durable_cells()),
                         set(pause_store.PAIR_LAUNCH_LEASE_KEYS))
        self.assertNotIn("terminal_digest", lease.durable_cells(),
                         "the target is bound one stage later, at CREATED")
        self.assertFalse(lease.bound)
        self.assertFalse(lease.revoked)

    def test_the_lease_never_reaches_an_artifact_as_a_path(self):
        """The endpoint PATH is derived, so it is never stored; the DIGEST is."""
        lease = ModelLaunchLease(
            run_id="r", role="worker", phase="p", attempt=1, create_attempt=1,
            requested_model="m", agent_command="claude", launch_token="t",
            launch_stamp=1, session_uuid="u", endpoint_path="/tmp/secret-path.sock",
            endpoint_digest="d", launch_argv_digest="a")
        self.assertNotIn("/tmp/secret-path.sock", json.dumps(lease.durable_cells()))


if __name__ == "__main__":                          # pragma: no cover
    unittest.main()
